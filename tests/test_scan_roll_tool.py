"""The roll tool, driven with no scanner underneath.

`tools/scan_roll.py` had no tests at all, and that is how it came to write no
frame TIFFs for twelve days. Commit 5ebbcb7 renamed `FrameWriter`'s `path` to
`paths` when the writer grew a second destination, updated five files including
`tests/test_roll_writer.py`, and missed this tool. `job.get("paths")` was then
always None, the write loop never ran, nothing raised, and the tool went on
printing a success line naming a file that did not exist.

The test that existed built the job dict by hand, in the writer's new shape, so
it stayed green against a tool that no longer produced that shape. What these
hold it to is the thing that only driving `main()` can show: that a roll leaves
files behind.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import FilmOnFrame, load_tool
from rps7200.direct import DirectScanner, RollFrame

scan_roll = load_tool("scan_roll")

RAW_LEVEL, CORRECTED_LEVEL = 111, 222


class FakeRollScanner(FilmOnFrame, DirectScanner):
    """Yields frames the way the driver does, including the raw pixels.

    `RollFrame` carries `raw_image` beside `image` precisely because the
    library stores what the scanner sent and recomputes the correction on the
    way out. The two levels here are distinct so a test can say which was
    filed.

    On a transport now, because the tool asks where the film is before it
    moves anything; a double that cannot say is a scanner every roll refuses.
    Its frames count from ``first_index`` and sit where the film is, as the
    driver's do.
    """

    def __init__(self, frames: int = 3, **kw):
        self.verbose = False
        self._shading = None
        self._ccd_mask = b"\x00" * 16
        self.last_raw = b"raw-bytes"
        self.last_raw_layout = {"format": "index"}
        self._frames = frames
        self.asked = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def inquiry(self, refresh=False):
        return SimpleNamespace(vendor="Reflecta", product="RPS 7200",
                               model=0x31, firmware="1.0")

    def ensure_shading(self, path, reuse=False, skip=False):
        return {"action": "skipped", "reference": None, "path": None,
                "summary": "shading correction disabled"}

    def scan_roll(self, **kw):
        self.asked = dict(kw)
        # Honour what the tool asked for, so a test that passes --frames gets
        # that many. A fake that ignores its arguments cannot catch an
        # argument that stops being passed, which is this tool's known
        # failure mode: --no-fast-ir was parsed, stored and dropped.
        count = kw.get("frames") or self._frames
        first = kw.get("first_index", 0)
        for i in range(count):
            if i:
                self.at += 1                  # the advance between frames
            shape = (6, 6, 3)
            yield RollFrame(
                index=first + i,
                position=self.at,
                image=np.full(shape, CORRECTED_LEVEL, np.uint16),
                meta={"resolution_dpi": kw.get("resolution", 1800),
                      "channel_order": list("RGB"), "duration_s": 1.0},
                prescan=np.full((3, 3, 3), 40, np.uint8),
                registration={"contrast": 0.3},
                raw_image=np.full(shape, RAW_LEVEL, np.uint16),
                raw_prescan=np.full((3, 3, 3), 30, np.uint8),
                # As the driver publishes it: a prescan with none is written
                # and not filed.
                prescan_meta={"resolution_dpi": kw.get("prescan_resolution",
                                                       300),
                              "channel_order": list("RGB")},
            )


def filed(lib, kind):
    """The `scan.json` of every entry of this roll kind: "frame" or "prescan".

    A roll files each frame's prescan raw, as an entry of its own, beside the
    frame's; counting every entry counts both.
    """
    import json

    return [p for p in sorted(lib.glob("*/scan.json"))
            if (json.loads(p.read_text(encoding="utf-8")).get("extra") or {})
            .get("roll_membership", {}).get("kind") == kind]


def run(tmp_path, monkeypatch, *argv, frames=3, opened=None,
        cls=FakeRollScanner):
    """``(the scanner the tool opened, its exit code)``. ``opened`` collects
    every scanner made, for a test whose run is refused: the pair is never
    returned then, so it is the only way to see whether one was opened.
    ``cls`` is the scanner, a `FakeRollScanner`."""
    created = [] if opened is None else opened

    class Patched(cls):
        def __init__(self, **kw):
            super().__init__(frames=frames)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "teststrip", *argv],
    )
    code = scan_roll.main()
    return (created[0] if created else None), code


# -- the bug that shipped ---------------------------------------------------


def test_a_roll_writes_a_tiff_for_every_frame(tmp_path, monkeypatch):
    """The whole point of the tool. It reported success for twelve days while
    writing nothing, because the writer's key was renamed under it."""
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "3")
    assert code == 0
    written = sorted((tmp_path / "roll").glob("frame*.tif"))
    assert [p.name for p in written] == ["frame01.tif", "frame02.tif",
                                         "frame03.tif"], written


def test_the_file_it_names_is_the_file_it_wrote(tmp_path, monkeypatch):
    """The manifest records `file` per frame, and the printed line names a
    path. Both were true of a file that did not exist."""
    import json

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    for record in manifest["frames"]:
        named = record.get("file")
        assert named, record
        assert (tmp_path / "roll" / named).exists(), named


def test_the_library_entry_holds_raw_pixels(tmp_path, monkeypatch):
    """`RollFrame` carries `raw_image` because the library stores what the
    scanner sent. This tool passed only `image`, so its entries held the
    corrected pixels while the record said raw -- and `library.corrected()`
    would then shade them a second time."""
    from rps7200 import library

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    entries = filed(tmp_path / "lib", "frame")
    assert len(entries) == 2
    for record in entries:
        image, stored = library.load(record.parent)
        assert int(image.max()) == RAW_LEVEL, "the corrected image was filed"
        assert stored["image"]["corrections_applied"] == []


def test_a_real_roll_files_each_prescan_raw_in_its_own_entry(tmp_path,
                                                             monkeypatch):
    """It survived only inside the frame's entry, as the corrected
    `prescan.tif`, with no bytes and nothing to say it was corrected."""
    from rps7200 import library

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    prescans = filed(tmp_path / "lib", "prescan")
    assert len(prescans) == 2
    for path in prescans:
        image, stored = library.load(path.parent)
        assert int(image.max()) == 30, "the corrected prescan was filed"
        assert stored["image"]["corrections_applied"] == []
    assert not list((tmp_path / "roll").glob("prescan*.tif")), \
        "a real roll's folder holds its frames"


def test_its_prescans_wait_against_their_own_bound(tmp_path, monkeypatch):
    """`FrameWriter` bounds frames and prescans apart, by the job's kind. A
    prescan not said to be one takes a frame's room, and the scanning thread
    stops a frame early behind a writer held up for a moment."""
    submitted = []
    real = scan_roll.FrameWriter.submit

    def noted(self, **job):
        submitted.append((job.get("kind"), "prescan" in job["tags"]))
        real(self, **job)

    monkeypatch.setattr(scan_roll.FrameWriter, "submit", noted)
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    assert sorted(submitted, key=str) == [("prescan", True)] * 2 + [
        (None, False)] * 2


def test_a_frame_that_failed_keeps_its_prescan_and_is_not_named_by_it(
        tmp_path, monkeypatch):
    """Its prescan is the only account of it, and was dropped. Filed now, it
    must not become the entry the roll's record names for the frame."""
    import json

    class OneFails(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if frame.index == 1:
                    frame = RollFrame(
                        index=frame.index, position=frame.position,
                        image=None, meta={}, prescan=frame.prescan,
                        registration=frame.registration,
                        error="pretend failure",
                        raw_prescan=frame.raw_prescan,
                        prescan_meta=frame.prescan_meta)
                yield frame

    class Patched(OneFails):
        def __init__(self, **kw):
            super().__init__(frames=2)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "teststrip", "--frames", "2"])
    scan_roll.main()
    numbers = sorted(json.loads(p.read_text(encoding="utf-8"))["extra"]
                     ["roll_membership"]["number"]
                     for p in filed(tmp_path / "lib", "prescan"))
    assert numbers == [1, 2]
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    failed = [f for f in manifest["frames"] if f["number"] == 2][0]
    assert failed["error"] and not failed.get("entry"), failed


# -- the dry run, which writes prescans instead -----------------------------


def test_a_dry_run_writes_prescans_and_no_frames(tmp_path, monkeypatch):
    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "2")
    assert code == 0
    assert sorted(p.name for p in (tmp_path / "roll").glob("prescan*.tif")) \
        == ["prescan01.tif", "prescan02.tif"]
    assert not list((tmp_path / "roll").glob("frame*.tif"))
    assert (tmp_path / "roll" / "survey.json").exists()


def test_a_walk_names_a_prescan_and_its_entry_once_the_writer_has_them(
        tmp_path, monkeypatch):
    """Named as it was queued, a prescan the writer then could not write left
    the walk naming a file nothing wrote -- or, walked again, the last walk's
    picture of that place. And the entry it was filed as was named only in
    the final save, which a kill never reaches."""
    from pathlib import Path

    from rps7200 import session

    real = session.export.write

    def refuses(path, image, **kw):
        if Path(path).name == "prescan02.tif":
            raise OSError("the disk is full")
        return real(path, image, **kw)

    monkeypatch.setattr(session.export, "write", refuses)
    run(tmp_path, monkeypatch, "--dry-run", "--frames", "2")
    survey = json.loads((tmp_path / "roll" / "survey.json")
                        .read_text(encoding="utf-8"))
    by_number = {f["number"]: f for f in survey["frames"]}
    assert by_number[1]["prescan"] == "prescan01.tif"
    assert (tmp_path / "lib" / by_number[1]["prescan_entry"]
            / "scan.json").exists()
    assert "prescan" not in by_number[2], by_number[2]
    assert by_number[2]["prescan_entry"]


# -- the flags reach the driver ---------------------------------------------


@pytest.mark.parametrize("flag, key, value", [
    ("--correct", "correct", True),
    ("--correct-dry-run", "correct_dry_run", True),
    ("--dry-run", "dry_run", True),
])
def test_a_flag_reaches_scan_roll(tmp_path, monkeypatch, flag, key, value):
    """Parsed, stored and dropped is a failure mode this tool has had before:
    --no-fast-ir was accepted and never passed on."""
    scanner, code = run(tmp_path, monkeypatch, flag, "--frames", "1")
    assert code == 0
    assert scanner.asked[key] is value, scanner.asked


# -- losing the scanner part way --------------------------------------------


def test_a_shading_failure_files_the_frames_already_scanned(tmp_path,
                                                            monkeypatch):
    """`ShadingUnavailable` is raised in four places in `direct.py` and was not
    in the roll's except tuple, so it ended the roll rather than the frame --
    unwinding past `writer.finish()` and taking the queued frames with it.

    Two independent fixes and this holds both: the tuple now catches it, and
    the writer is finished whatever comes out of the scanning block.
    """
    from rps7200.protocol import ShadingUnavailable

    class FailsPartWay(FakeRollScanner):
        def scan_roll(self, **kw):
            self.asked = dict(kw)
            for frame in super().scan_roll(**kw):
                if frame.index == 2:
                    raise ShadingUnavailable("no reference for this pass")
                yield frame

    created = []

    class Patched(FailsPartWay):
        def __init__(self, **kw):
            super().__init__(frames=4)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "cut-short", "--frames", "4"],
    )
    code = scan_roll.main()

    # The two frames that got through are on disk, in both places.
    assert sorted(p.name for p in (tmp_path / "roll").glob("frame*.tif")) \
        == ["frame01.tif", "frame02.tif"]
    assert len(filed(tmp_path / "lib", "frame")) == 2
    assert code != 0, "losing the roll part way is not a success"


def test_no_shading_reaches_every_pass_of_the_roll(tmp_path, monkeypatch):
    """`--no-shading` used to skip only the up-front calibration. The roll's
    prescans still asked for a correction, so the first one calibrated inside
    itself -- the path measured twice as stalling the device."""
    scanner, code = run(tmp_path, monkeypatch, "--frames", "1")
    assert code == 0
    assert scanner.asked["shading"] is False, scanner.asked


def test_ctrl_c_reaches_the_roll_as_a_stop_between_frames(tmp_path, monkeypatch):
    """The roll checks it before every frame, so a Ctrl-C finishes the frame
    in flight instead of abandoning its read -- which wedges the scanner."""
    scanner, code = run(tmp_path, monkeypatch, "--frames", "1")
    assert code == 0
    assert callable(scanner.asked.get("should_stop")), scanner.asked
    assert scanner.asked["should_stop"]() is False


def test_the_roll_is_named_to_the_driver(tmp_path, monkeypatch):
    """So the passes only debug filing keeps -- a probe, a hold's look --
    say which roll they served; a frame index is the same in every roll."""
    scanner, code = run(tmp_path, monkeypatch, "--frames", "1")
    assert code == 0
    assert scanner.asked.get("roll") == "teststrip", scanner.asked


def test_a_second_ctrl_c_still_files_the_frames_already_scanned(tmp_path,
                                                               monkeypatch):
    """KeyboardInterrupt is not an Exception, and used to skip
    `writer.finish()`: the frames queued for filing died with the process."""

    class Interrupted(FakeRollScanner):
        def scan_roll(self, **kw):
            self.asked = dict(kw)
            for frame in super().scan_roll(**kw):
                if frame.index == 2:
                    raise KeyboardInterrupt
                yield frame

    class Patched(Interrupted):
        def __init__(self, **kw):
            super().__init__(frames=4)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "interrupted", "--frames", "4"],
    )
    code = scan_roll.main()
    assert len(filed(tmp_path / "lib", "frame")) == 2
    assert code != 0


def test_the_manifest_says_what_stopped_it(tmp_path, monkeypatch):
    from rps7200.protocol import ShadingUnavailable

    class Explodes(FakeRollScanner):
        def scan_roll(self, **kw):
            self.asked = dict(kw)
            raise ShadingUnavailable("no reference at all")
            yield  # pragma: no cover

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: Explodes(frames=1))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", "", "--no-shading", "--roll", "dead"],
    )
    assert scan_roll.main() != 0
    import json
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert "ShadingUnavailable" in manifest.get("stopped", "")


def test_a_frame_that_failed_makes_the_run_fail(tmp_path, monkeypatch):
    """It used to be `failed and not scanned`, so a roll that scanned twenty
    and lost three reported success to whatever was checking."""
    class OneBad(FakeRollScanner):
        def scan_roll(self, **kw):
            self.asked = dict(kw)
            for frame in super().scan_roll(**kw):
                if frame.index == 1:
                    yield type(frame)(
                        index=frame.index, position=frame.position,
                        image=None, meta={}, prescan=frame.prescan,
                        registration={}, error="the read timed out")
                else:
                    yield frame

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: OneBad(frames=3))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", "", "--no-shading", "--roll", "mixed", "--frames", "3"],
    )
    code = scan_roll.main()
    assert len(list((tmp_path / "roll").glob("frame*.tif"))) == 2
    assert code != 0, "two scanned and one lost is not a clean run"


# --- --start-at is a place on the strip --------------------------------------


def _run_on(tmp_path, monkeypatch, at, *argv):
    created = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=2)
            self.at = at
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--no-shading", "--roll", "placed", *argv])
    code = scan_roll.main()
    return created[0], code


def test_start_at_is_a_place_on_the_strip(tmp_path, monkeypatch):
    """`--start-at 3` used to mean "advance twice from wherever the film is",
    so with the film on frame 8 it scanned 10 and 11 and called them 3 and 4.
    It goes to frame 3 now, by the transport's counter, and numbers from it."""
    import json

    scanner, code = _run_on(tmp_path, monkeypatch, 7,
                            "--start-at", "3", "--frames", "2")
    assert code == 0
    assert scanner.moves == [("retreat", 1)] * 5
    assert scanner.asked["first_index"] == 2
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert [(f["number"], f["transport_position"])
            for f in manifest["frames"]] == [(3, 2), (4, 3)]
    assert manifest["numbering"] == "strip"


def test_start_at_resumes_the_roll_rather_than_replacing_it(tmp_path,
                                                            monkeypatch):
    """The docstring promised `--start-at` resumes from the manifest; it wrote
    a fresh one over it, and the record of the frames already scanned went
    with it."""
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    _scanner, code = run(tmp_path, monkeypatch, "--start-at", "3",
                         "--frames", "2")
    assert code == 0
    folder = tmp_path / "roll"
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in manifest["frames"]] == [1, 2, 3, 4]
    assert all(f["done"] and f["file"] for f in manifest["frames"])
    # and the run before this one, as it stood, beside it
    before = json.loads((folder / "roll.json.bak").read_text(encoding="utf-8"))
    assert [f["number"] for f in before["frames"]] == [1, 2]


def test_the_advice_names_a_frame_whose_entry_went_to_the_trash(
        tmp_path, monkeypatch, capsys):
    """T-14: the advice read `done` as it stood, so a frame whose entry the
    window had moved to the trash was never offered again. It reads the
    roll as the window does (`session.scanned_frames`): not scanned while
    that entry is out of the library, and every take is listed."""
    from pathlib import PureWindowsPath

    from rps7200 import library, session

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    capsys.readouterr()
    folder, lib = tmp_path / "roll", tmp_path / "lib"
    progress = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    entry = lib / PureWindowsPath(progress["frames"][1]["entry"]).name
    assert [t["entry"] for t in progress["frames"][1]["takes"]] == [entry.name]
    scan = session.ScanSession(root=str(lib), rolls=str(tmp_path),
                               verbose=False)
    done = scan.trash_entries([entry], by="the window")
    assert done.frames == [(folder / "roll.json", 2)]
    assert library.in_trash(lib, entry.name) is not None

    _scanner, code = run(tmp_path, monkeypatch, "--start-at", "3",
                         "--frames", "1")
    assert code == 0
    (advice,) = [line for line in capsys.readouterr().err.splitlines()
                 if line.startswith("resume ")]
    assert advice.endswith("--start-at 2 --only 2")


def test_a_run_nobody_named_has_a_folder_of_its_own(tmp_path, monkeypatch):
    """`rolls/<today>` was also the folder of every unnamed walk the window
    made that day, so a run from here replaced that walk's survey."""
    import time

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: FakeRollScanner(frames=1))
    today = tmp_path / "rolls" / time.strftime("%Y-%m-%d")
    today.mkdir(parents=True)
    walk = {"roll": today.name, "frames": [{"number": 1}]}
    (today / "survey.json").write_text(json.dumps(walk), encoding="utf-8")
    for _ in range(2):
        monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                          "--no-shading", "--dry-run",
                                          "--frames", "1"])
        assert scan_roll.main() == 0
    made = sorted(p for p in (tmp_path / "rolls").iterdir() if p != today)
    assert len(made) == 2, made
    for folder in made:
        assert folder.name.startswith(today.name + "-")
        survey = json.loads((folder / "survey.json").read_text(
            encoding="utf-8"))
        assert survey["roll"] == folder.name
    assert json.loads((today / "survey.json").read_text(
        encoding="utf-8")) == walk


def test_the_advice_to_resume_names_the_roll_it_resumes(tmp_path, monkeypatch,
                                                        capsys):
    """An unnamed run's folder is new every run, so "--start-at N" alone,
    followed as printed, started another roll beside this one and never read
    the manifest that says what this one has done -- one roll, two folders."""
    class OneBad(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if frame.index == 1:
                    yield type(frame)(
                        index=frame.index, position=frame.position,
                        image=None, meta={}, prescan=frame.prescan,
                        registration={}, error="the read timed out")
                else:
                    yield frame

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: OneBad(frames=3))
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                      "--no-shading", "--frames", "3"])
    assert scan_roll.main() == 1
    (folder,) = (tmp_path / "rolls").iterdir()
    (advice,) = [line for line in capsys.readouterr().err.splitlines()
                 if line.startswith("resume ")]
    assert advice.endswith(f"with --roll {folder.name} --start-at 2 --only 2")

    # Followed as printed, it finishes that roll.
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: FakeRollScanner(frames=1))
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                      "--no-shading", "--roll", folder.name,
                                      "--start-at", "2", "--only", "2"])
    assert scan_roll.main() == 0
    assert list((tmp_path / "rolls").iterdir()) == [folder]
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert {f["number"]: f["done"] for f in manifest["frames"]} == {
        1: True, 2: True, 3: True}


def test_the_advice_to_resume_quotes_a_folder_a_shell_would_split(
        tmp_path, monkeypatch, capsys):
    def full_disk(self, job):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: FakeRollScanner(frames=1))
    monkeypatch.setattr(scan_roll.FrameWriter, "_write", full_disk)
    out = tmp_path / "Gold 200"
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                      "--no-shading", "--frames", "1",
                                      "--out", str(out)])
    assert scan_roll.main() == 1
    assert f'with --out "{out}" --start-at 1 --only 1' in capsys.readouterr().err


def test_a_manifest_the_disk_refuses_does_not_stop_the_roll(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Each frame's rewrite of roll.json raised into the roll's except, and
    the roll stopped at its first frame; the last write raised past the
    summary as a traceback. On Windows a rename is refused whenever anyone
    has the file open. The frames are scanned and filed, and the exit status
    and stderr say the manifest is behind."""
    import os

    from rps7200 import session

    monkeypatch.setattr(session, "REPLACE_RETRY_S", ())
    real = os.replace
    first = []

    def refused_after_the_first(src, dst):
        if str(dst).endswith("roll.json"):
            if first:
                raise PermissionError(13, "being used by another process")
            first.append(dst)
        return real(src, dst)

    monkeypatch.setattr(session.os, "replace", refused_after_the_first)
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "3")
    assert len(list((tmp_path / "roll").glob("frame*.tif"))) == 3
    assert code == 1
    assert "could not write" in capsys.readouterr().err


def test_a_frame_that_was_never_filed_names_no_file(tmp_path, monkeypatch):
    """`file` named a TIFF before anything had written it, and a resume took
    that as done."""
    from rps7200 import session

    real = session.FrameWriter._write

    def fails_on_two(self, job):
        if job["number"] == 2:
            raise OSError(28, "No space left on device")
        return real(self, job)

    monkeypatch.setattr(session.FrameWriter, "_write", fails_on_two)
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "3")
    assert code == 1
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    frames = {f["number"]: f for f in manifest["frames"]}
    assert frames[2]["done"] is False and frames[2]["file"] is None
    assert "No space left" in frames[2]["filing_error"]
    assert frames[1]["done"] and frames[1]["file"] == "frame01.tif"


def test_a_roll_the_tool_cannot_place_scans_nothing(tmp_path, monkeypatch):
    from rps7200 import session

    class Silent(FakeRollScanner):
        def position(self):
            return None

    created = []

    class Patched(Silent):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0, raising=False)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--no-shading", "--roll", "lost", "--frames", "2"])
    assert scan_roll.main() == 1
    assert created[0].asked == {}, "the roll was never started"
    assert not list((tmp_path / "roll").glob("frame*.tif"))


class _Cold(FakeRollScanner):
    """Silent until its lamp is warm, and `wait_warm` is what warms it -- as
    `DirectScanner.wait_warm` blocks until TEST UNIT READY succeeds. That the
    counter says nothing meanwhile is `wait_warm`'s docstring's premise, not
    a measurement. ``order`` is what the tool asked, in order."""

    warm = False

    def __init__(self, frames=2, at=0, order=None):
        super().__init__(frames=frames)
        self.at = at
        self.order = [] if order is None else order

    def wait_warm(self, timeout=300.0, poll=5.0):
        self.order.append("wait_warm")
        self.warm = True

    def position(self):
        self.order.append("position")
        return self.at if self.warm else None

    def ensure_shading(self, path, reuse=False, skip=False):
        self.order.append("calibrate")
        return super().ensure_shading(path, reuse, skip)


def _tool(tmp_path, monkeypatch, *argv, make):
    """Run the tool with ``make()`` standing where the scanner stands."""
    from rps7200 import session

    created = []

    def opened(**kw):
        created.append(make())
        return created[-1]

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0)
    monkeypatch.setattr(scan_roll, "DirectScanner", opened)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--reference", str(tmp_path / "shading.npz"), "--roll", "roll",
         "--film-loaded", *argv])
    return scan_roll.main(), created


def test_a_roll_from_cold_waits_for_the_lamp_before_it_asks_where_the_film_is(
        tmp_path, monkeypatch):
    """With the counter silent while the lamp warms, a seek that asked first
    would hear nothing and refuse. The lamp is waited for with status queries
    alone, then the film is placed, and only then is anything calibrated."""
    order = []
    code, created = _tool(tmp_path, monkeypatch, "--dry-run", "--frames", "2",
                          make=lambda: _Cold(order=order))
    assert code == 0
    assert order[0] == "wait_warm", order
    assert order.index("position") < order.index("calibrate"), order
    assert created[0].asked["first_index"] == 0


def test_a_counter_no_strip_has_refuses_before_anything_is_calibrated(
        tmp_path, monkeypatch):
    """A stale 72 is the one reading of a transport with no strip in
    (`full_17_strip`). The tool calibrated first and refused after, spending
    3-4 minutes calibrating what may have been an empty transport -- the
    state CLAUDE.md says preceded a wedge. It refuses first now."""
    order = []
    code, created = _tool(tmp_path, monkeypatch, "--dry-run", "--frames", "2",
                          make=lambda: _Cold(at=72, order=order))
    assert code == 1
    assert "calibrate" not in order, order
    assert created[0].asked == {}, "the roll never started"
    assert created[0].moves is None, "nothing moved"
    assert not (tmp_path / "roll").exists()


def test_frames_0_does_not_calibrate(tmp_path, monkeypatch):
    """A rewind alone is a transport job; a calibration there would spend
    3-4 minutes of lamp on nothing."""
    order = []

    def warm():
        scanner = _Cold(at=5, order=order)
        scanner.warm = True
        return scanner

    code, created = _tool(tmp_path, monkeypatch, "--frames", "0",
                          "--rewind", "2", make=warm)
    assert code == 0
    assert created[0].at == 3, "the rewind itself ran"
    assert "calibrate" not in order, order


def test_a_rewind_from_cold_waits_for_the_lamp_first(tmp_path, monkeypatch):
    """`--rewind` is the first thing that talks to the transport, so the lamp
    is waited for ahead of it as well. Ahead of a silent counter, the rewind
    read every command as backlash and refused."""
    order = []
    code, created = _tool(tmp_path, monkeypatch, "--frames", "0",
                          "--rewind", "2",
                          make=lambda: _Cold(at=5, order=order))
    assert code == 0
    assert order[0] == "wait_warm", order
    assert created[0].at == 3


def test_a_calibration_that_fails_writes_nothing_and_exits_1(tmp_path,
                                                             monkeypatch):
    """The folder is today's date by default, the window's walks' name, so a
    run that stops before its roll begins leaves the walk there alone."""
    class Broken(_Cold):
        def ensure_shading(self, path, reuse=False, skip=False):
            raise TimeoutError("lamp still warming after 300s")

    walked = tmp_path / "roll"
    walked.mkdir()
    # Its prescan filed: a folder a fresh walk may go into (P18-a).
    before = {"roll": "roll", "frames": [{"number": 1, "prescan": "p.tif",
                                          "prescan_entry": "e1"}]}
    (walked / "survey.json").write_text(json.dumps(before), encoding="utf-8")
    code, created = _tool(tmp_path, monkeypatch, "--dry-run", "--frames", "2",
                          make=Broken)
    assert code == 1
    assert created[0].asked == {}, "nothing was scanned"
    assert json.loads((walked / "survey.json").read_text(
        encoding="utf-8")) == before
    assert [p.name for p in walked.iterdir()] == ["survey.json"]


def test_a_roll_that_cannot_be_placed_leaves_the_folder_as_it_was(
        tmp_path, monkeypatch):
    """The default roll name is today's date, the same name the window's
    walks use, and the manifest was written before the device was opened --
    so a seek that refused replaced that day's survey.json with an empty
    one. Nothing is written until the film is on the roll's first frame."""
    import json

    from rps7200 import session

    class Silent(FakeRollScanner):
        def position(self):
            return None

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0, raising=False)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: Silent(frames=2))

    walked = tmp_path / "2026-09-23"
    walked.mkdir()
    before = {"roll": "2026-09-23", "frames": [
        {"number": 1, "transport_position": 5, "prescan": "prescan01.tif",
         "prescan_entry": "e1"}]}
    (walked / "survey.json").write_text(json.dumps(before), encoding="utf-8")
    fresh = tmp_path / "never-placed"
    for out in (walked, fresh):
        monkeypatch.setattr(
            sys, "argv",
            ["scan_roll.py", "--out", str(out), "--library", "",
             "--no-shading", "--roll", out.name, "--dry-run",
             "--frames", "2"])
        assert scan_roll.main() == 1
    assert json.loads((walked / "survey.json").read_text(
        encoding="utf-8")) == before, "the walk was overwritten"
    assert not fresh.exists(), "a folder for a roll that never started"


def test_an_old_walks_prescans_are_held_under_the_strips_numbers(
        tmp_path, monkeypatch):
    """`registration-F` named its prescans 01 to 03 from a walk begun on the
    counter's 14. Held under those numbers, a roll that now numbers frames by
    their place on the strip would hold frame 1 of the strip to frame 15's
    reference."""
    import json

    from rps7200 import tiff

    folder = tmp_path / "registration-F"
    folder.mkdir()
    for n in (1, 2, 3):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 40 + n, np.uint8))
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n + 13,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2, 3)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    held, _note = scan_roll.hold_from_walk(folder)
    assert sorted(held) == [15, 16, 17]
    assert all(a.number == n for n, a in held.items())


def test_every_walked_frame_is_held_and_none_past_one_command(
        tmp_path, monkeypatch):
    """The sheet holds every frame it commissions -- one the detector left
    unplaced at 0, which is what corrects the rewind -- and clamps each to
    one command. --approved, which says it is the sheet's path, held only
    the frames with a proposal, and held those unclamped."""
    import json

    from rps7200 import tiff
    from rps7200.session import FINE_MAX_MM

    folder = tmp_path / "walk"
    folder.mkdir()
    for n in (1, 2, 3):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 40 + n, np.uint8))
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n - 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2, 3)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({1: 0.4, 3: 25.0},
                                   {1: {"source": "measured"},
                                    2: {"source": "none"},
                                    3: {"source": "measured"}}))
    held, note = scan_roll.hold_from_walk(folder)
    assert sorted(held) == [1, 2, 3], "a frame with no proposal went unheld"
    assert held[2].offset_mm == 0.0 and held[2].source == "none"
    assert held[1].offset_mm == pytest.approx(0.4)
    assert held[3].offset_mm == pytest.approx(FINE_MAX_MM)
    # And the note -- what `main` prints and the manifest records -- says
    # what is held: every frame, each at the offset it will be sent to.
    assert note["sources"] == {1: "measured", 2: "none", 3: "measured"}
    assert note["offsets"] == {n: round(a.offset_mm, 4)
                               for n, a in held.items()}


def test_a_folder_walked_twice_is_held_as_its_survey_lists_it(
        tmp_path, monkeypatch):
    """`rolls/2026-09-23` as it is on disk. The survey lists the second walk:
    prescan01 and 02, at transport positions 5 and 6. prescan03 to 06 were
    left by an earlier walk into the same folder, at positions 2 to 5. Read
    by file name with the survey's one shift, those four became frames 8 to
    11 -- prescan06 a second picture of prescan01's place, held as frame 11
    -- where the window's sheet shows frames 6 and 7. The tool and the window
    read one folder one way."""
    import json

    from rps7200 import tiff

    folder = tmp_path / "2026-09-23"
    folder.mkdir()
    for n in range(1, 7):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 40 + n, np.uint8))
    (folder / "survey.json").write_text(json.dumps({
        "roll": "2026-09-23", "dry_run": True,
        "settings": {"start_at": 1, "only": None, "prescan_resolution": 300},
        "frames": [{"number": n, "transport_position": n + 4,
                    "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    held, note = scan_roll.hold_from_walk(folder)
    assert sorted(held) == [6, 7]
    assert note["walked"] == 2
    assert np.array_equal(held[6].reference,
                          tiff.read(str(folder / "prescan01.tif")))

    gui = load_tool("gui")
    shown = [r.number for r in gui.read_survey(folder)["results"]]
    assert sorted(held) == shown, "the tool and the window disagree"


def _prescans(folder, numbers):
    from rps7200 import tiff

    folder.mkdir()
    for n in numbers:
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 40 + n, np.uint8))


def test_a_turned_walk_is_held_to_references_the_way_the_film_sits(
        tmp_path, monkeypatch):
    """The window writes a walk's prescans arranged the way the screen had
    them, each record saying how. Read as they lay on disk, a walk made turned
    handed the detector and the hold sideways references."""
    from rps7200 import preview, tiff

    folder = tmp_path / "turned-walk"
    folder.mkdir()
    film = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    records = []
    for n, (turn, flip) in ((1, (0, False)), (2, (90, True))):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   preview.orient(film, turn, flip))
        records.append({"number": n, "prescan": f"prescan{n:02d}.tif",
                        "prescan_rotation": turn, "prescan_flipped": flip})
    (folder / "survey.json").write_text(json.dumps(
        {"numbering": "strip", "rotation": 0, "frames": records}),
        encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: (seen.update(frames) or
                                   {n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    held, _note = scan_roll.hold_from_walk(folder)
    for n in (1, 2):
        assert np.array_equal(seen[n], film), f"frame {n} read as it lay"
        assert np.array_equal(held[n].reference, film)


def _walked_at(tmp_path, monkeypatch, dpi, *, top_level=True,
               film="negative"):
    """A walk folder whose manifest says it was prescanned at ``dpi``: the
    window's shape (top level and `settings`), or this tool's (`settings`)."""
    folder = tmp_path / f"walk-{dpi}-{film}"
    _prescans(folder, (1, 2))
    manifest = {"roll": folder.name, "numbering": "strip",
                "settings": {"prescan_resolution": dpi, "film": film},
                "frames": [{"number": n, "transport_position": n - 1,
                            "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}
    if top_level:
        manifest["prescan_resolution"] = dpi
    (folder / "survey.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    return folder


@pytest.mark.parametrize("top_level", [True, False])
def test_approved_prescans_at_the_resolution_its_walk_was_made_at(
        tmp_path, monkeypatch, top_level):
    """It took `--prescan-dpi`, 300 unless told, and so held a walk made at
    600 dpi in the window against 300 dpi passes: every frame unverified,
    nothing moved, exit 0. The walk's own resolution is the roll's now, read
    as the window reads it whichever tool wrote the walk."""
    folder = _walked_at(tmp_path, monkeypatch, 600, top_level=top_level)
    _held, note = scan_roll.hold_from_walk(folder)
    assert note["prescan_resolution"] == 600

    scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                        "--dry-run", "--frames", "1")
    assert code == 0
    assert scanner.asked["prescan_resolution"] == 600
    written = json.loads((tmp_path / "roll" / "survey.json").read_text(
        encoding="utf-8"))
    assert written["settings"]["prescan_resolution"] == 600


def test_approved_with_another_prescan_dpi_is_refused_before_opening(
        tmp_path, monkeypatch):
    folder = _walked_at(tmp_path, monkeypatch, 600)
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--approved", str(folder),
            "--prescan-dpi", "300", "--frames", "1", opened=opened)
    assert refused.value.code == 2
    assert opened == [], "the device was opened before the refusal"


def test_approved_from_a_walk_that_never_said_keeps_300(tmp_path, monkeypatch):
    """A walk from before the resolution was recorded: the tool's default,
    as it was, and an explicit one is taken as given."""
    folder = tmp_path / "old-walk"
    _prescans(folder, (1, 2))
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n - 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}), encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                        "--dry-run", "--frames", "1")
    assert code == 0 and scanner.asked["prescan_resolution"] == 300


# --- a prescan resolution the frame-edge detector cannot read ----------------


def test_correct_at_a_prescan_the_edges_are_not_read_at_is_refused(
        tmp_path, monkeypatch, capsys):
    """The device's 600 and 900 dpi prescans are 860 and 1292 columns, which
    the detector refuses, so --correct would correct nothing -- one "left as
    it came" per frame of an unattended roll. Refused before the device
    opens, and the reason names the resolution."""
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--correct", "--prescan-dpi", "600",
            "--frames", "1", opened=opened)
    assert refused.value.code == 2
    assert opened == [], "the device was opened before the refusal"
    assert "not read at a 600 dpi prescan" in capsys.readouterr().err


def test_a_walk_at_a_prescan_the_edges_are_not_read_at_is_warned_about(
        tmp_path, monkeypatch, capsys):
    """Warned, not refused: a walk's prescans are still a survey of the
    strip. Said before the device opens, which is when anyone is watching."""
    scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "1",
                        "--prescan-dpi", "900")
    assert code == 0
    assert scanner.asked["prescan_resolution"] == 900
    assert "not read at a 900 dpi prescan" in capsys.readouterr().err

    scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "1")
    assert code == 0
    assert "not read" not in capsys.readouterr().err


def test_approved_is_warned_about_on_the_film_its_walk_was_read_on(
        tmp_path, monkeypatch, capsys):
    """The walk --approved names was read on its own film, and was judged on
    --film, which is negative unless told: a slide walk at 600 dpi was warned
    about though slides never reach the detector, and a negative walk run
    with --film positive was not, though every frame of it was refused."""
    slides = _walked_at(tmp_path, monkeypatch, 600, film="positive")
    assert scan_roll.hold_from_walk(slides)[1]["film"] == "positive"
    capsys.readouterr()
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(slides),
                         "--frames", "1")
    assert code == 0
    assert "not read" not in capsys.readouterr().err

    negatives = _walked_at(tmp_path, monkeypatch, 600)
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(negatives),
                         "--film", "positive", "--frames", "1")
    assert code == 0
    assert "not read at a 600 dpi prescan" in capsys.readouterr().err

    # --correct reads this roll's own prescans, which are --film's whatever
    # the walk was, so its refusal stays judged on --film -- typed here, since
    # left out it is now the walk's own.
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--approved", str(slides), "--correct",
            "--film", "negative", "--frames", "1", opened=opened)
    assert refused.value.code == 2
    assert opened == []


def test_correct_on_a_film_the_edges_are_not_read_on_is_not_refused(
        tmp_path, monkeypatch, capsys):
    """Slides go to the strip detector, not to the frame-edge one, so the
    resolution the frame-edge detector reads is not theirs to be held to."""
    scanner, code = run(tmp_path, monkeypatch, "--correct", "--film",
                        "positive", "--prescan-dpi", "600", "--frames", "1")
    assert code == 0 and scanner.asked["correct"] is True
    assert "not read" not in capsys.readouterr().err


def test_a_walk_with_only_its_roll_json_is_held_from_that(tmp_path,
                                                          monkeypatch):
    """A walk made before walks had a file of their own wrote `roll.json`.
    With no `survey.json` beside it, that is the walk's record -- read by the
    same reader, so its old numbers still become the strip's."""
    folder = tmp_path / "old-walk"
    _prescans(folder, (1, 2, 3))
    (folder / "roll.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n + 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2, 3)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    held, note = scan_roll.hold_from_walk(folder)
    assert sorted(held) == [3, 4, 5]
    assert note["walked"] == 3


def test_a_walk_whose_transport_stalled_is_held_frame_for_frame(
        tmp_path, monkeypatch):
    """One advance of the walk did not move: positions 5, 6, 6, 7, 8. Two of
    its frames came back under one number, so `--approved` reported five
    walked and held four, dropping walk frame 1's prescan without a word.
    One walk numbers no two frames alike, and the window's sheet agrees."""
    folder = tmp_path / "stall"
    _prescans(folder, (1, 2, 3, 4, 5))
    (folder / "survey.json").write_text(json.dumps({
        "roll": "stall", "dry_run": True,
        "settings": {"start_at": 1, "only": None, "prescan_resolution": 300},
        "frames": [{"number": n, "transport_position": p,
                    "prescan": f"prescan{n:02d}.tif"}
                   for n, p in enumerate([5, 6, 6, 7, 8], start=1)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"} for n, _ in frames}))
    held, note = scan_roll.hold_from_walk(folder)
    assert note["walked"] == 5
    assert sorted(held) == [5, 6, 7, 8, 9]
    # each prescan is filled with 40 + its walk number
    assert [int(held[n].reference[0, 0, 0]) - 40 for n in sorted(held)] == [
        1, 2, 3, 4, 5]

    gui = load_tool("gui")
    shown = [r.number for r in gui.read_survey(folder)["results"]]
    assert sorted(held) == shown, "the tool and the window disagree"


def test_a_walk_whose_survey_cannot_be_read_is_refused(tmp_path):
    """Not quietly read from `roll.json` instead, and not held as nothing: a
    walk that cannot be read says so before the scanner is even opened."""
    folder = tmp_path / "torn"
    _prescans(folder, (1, 2))
    (folder / "survey.json").write_text('{"frames": [', encoding="utf-8")
    (folder / "roll.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n - 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}),
        encoding="utf-8")
    with pytest.raises(SystemExit, match="survey.json cannot be read"):
        scan_roll.hold_from_walk(folder)


def test_a_torn_walk_is_read_from_the_version_kept_beside_it(tmp_path,
                                                             monkeypatch):
    """As the window reads it (`session.read_manifest`): a bare `json.loads`
    refused a walk whose previous version was right there, and let one that
    parsed to a list through."""
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    folder = tmp_path / "torn"
    _prescans(folder, (1, 2))
    (folder / "survey.json").write_text('{"frames": [', encoding="utf-8")
    (folder / "survey.json.bak").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n - 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}),
        encoding="utf-8")
    assert sorted(scan_roll.hold_from_walk(folder)[0]) == [1, 2]

    (folder / "survey.json").write_text("[]", encoding="utf-8")
    (folder / "survey.json.bak").unlink()
    with pytest.raises(SystemExit, match="not a JSON object"):
        scan_roll.hold_from_walk(folder)


# --- the positions set on the window's sheet, replayed: --positions ----------


def _walk_for_positions(tmp_path, monkeypatch):
    """A walk of frames 1-3, numbered by the strip, that the detector
    proposes an `offset_mm` of 0.4, 0 and 0.2 for, each `measured`."""
    folder = tmp_path / "walk"
    _prescans(folder, (1, 2, 3))
    (folder / "survey.json").write_text(json.dumps({
        "roll": "walk", "numbering": "strip", "dry_run": True,
        "settings": {"prescan_resolution": 300},
        "frames": [{"number": n, "transport_position": n - 1,
                    "prescan": f"prescan{n:02d}.tif"} for n in (1, 2, 3)]}),
        encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({1: 0.4, 2: 0.0, 3: 0.2},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    return folder


def _approved_json(path, frames, **top):
    """An `approved.json` as the window writes one, with ``top`` beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**top, "frames": frames}), encoding="utf-8")
    return path


def test_positions_win_over_the_proposals_and_are_clamped(
        tmp_path, monkeypatch, capsys):
    """--approved re-proposed every frame, so a roll the window commissioned
    could not be replayed from here with the positions set on its sheet. A
    frame the file places is held to its position, clamped to one command as
    a proposal is; one it leaves at an untouched zero keeps its proposal, as
    the reopened sheet would show it; and the references stay the walk's."""
    from rps7200.session import FINE_MAX_MM

    folder = _walk_for_positions(tmp_path, monkeypatch)
    file = _approved_json(folder / "approved.json", [
        {"number": 1, "offset_mm": -0.9344, "source": "operator"},
        # Ticked and never touched: every ticked frame is written with a
        # position, and this zero is nobody's decision.
        {"number": 2, "offset_mm": 0.0, "source": "operator"},
        {"number": 3, "offset_mm": 20.0, "source": "operator"},
        {"number": 4, "offset_mm": 0.5, "source": "operator"},
    ], numbering="strip")
    held, note = scan_roll.hold_from_walk(folder, positions=file)
    assert held[1].offset_mm == pytest.approx(-0.9344)
    assert held[1].source == "operator"
    assert held[2].offset_mm == 0.0 and held[2].source == "measured"
    assert held[3].offset_mm == pytest.approx(FINE_MAX_MM)
    assert note["clamped"] == {3: 20.0}
    assert note["from_positions"] == [1, 3]
    assert note["not_walked"] == [4], "a frame the walk has no prescan of"
    assert [int(held[n].reference[0, 0, 0]) - 40 for n in (1, 2, 3)] == [1, 2, 3]

    # Through main, to the driver, each frame said before the device opens.
    capsys.readouterr()
    scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                        "--positions", str(file), "--frames", "3")
    assert code == 0
    sent = {i + 1: a.offset_mm for i, a in scanner.asked["approved"].items()}
    assert sent == pytest.approx({1: -0.9344, 2: 0.0, 3: FINE_MAX_MM})
    out, err = capsys.readouterr()
    assert "frame 1: -8.8 units, operator (approved.json)" in out
    assert "frame 2: +0.0 units, measured (proposed from the walk)" in out
    assert ("frame 3: +88.8 units, operator (approved.json; clamped from "
            "+189.2 units") in out
    assert "places frame 4" in err and "not held" in err
    recorded = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))["held"]
    assert recorded["positions"] == str(file)
    assert recorded["from_positions"] == [1, 3]


def test_positions_without_a_walk_are_refused_before_opening(
        tmp_path, monkeypatch, capsys):
    """Each position is an offset from where a walk saw the frame, and the
    walk's prescans are what the hold measures against: alone, a file has
    nothing to be held to."""
    file = _approved_json(tmp_path / "approved.json",
                          [{"number": 1, "offset_mm": 0.5}], numbering="strip")
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--positions", str(file), "--frames", "1",
            opened=opened)
    assert refused.value.code == 2
    assert opened == [], "the device was opened before the refusal"
    assert "--positions needs --approved" in capsys.readouterr().err


def test_positions_nothing_can_number_by_the_strip_are_refused(
        tmp_path, monkeypatch):
    """A file from before frame numbers were places on the strip numbered its
    frames as its walk did, and keeps no positions to say how. Copied out of
    its roll folder nothing beside it can say either, and a guess would hold
    each frame to another frame's position -- reported held. Refused before
    the scanner opens, saying what would make it usable."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    alone = _approved_json(tmp_path / "elsewhere" / "approved.json",
                           [{"number": 1, "offset_mm": 0.5}])
    opened: list = []
    with pytest.raises(SystemExit, match='"numbering": "strip"'):
        run(tmp_path, monkeypatch, "--approved", str(folder), "--positions",
            str(alone), "--frames", "1", opened=opened)
    assert opened == []
    with pytest.raises(SystemExit, match="no such file"):
        scan_roll.hold_from_walk(folder, positions=tmp_path / "gone.json")
    untouched = _approved_json(
        tmp_path / "untouched" / "approved.json",
        [{"number": 1, "offset_mm": 0.0, "source": "operator"}],
        numbering="strip")
    with pytest.raises(SystemExit, match="places no frame"):
        scan_roll.hold_from_walk(folder, positions=untouched)


def test_older_positions_beside_their_walk_are_numbered_as_it_was(
        tmp_path, monkeypatch):
    """As the window reads one (`session.approved_shift`): the walk beside it
    began on the counter's 4, so the file's frame 1 is the strip's 5."""
    folder = tmp_path / "old-walk"
    _prescans(folder, (1, 2))
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n + 3,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}), encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    file = _approved_json(folder / "approved.json",
                          [{"number": 1, "offset_mm": 0.5}])
    held, note = scan_roll.hold_from_walk(folder, positions=folder)
    assert sorted(held) == [5, 6]
    assert held[5].offset_mm == pytest.approx(0.5)
    assert held[5].source == "operator", "a placed frame nobody named is his"
    assert held[6].offset_mm == 0.0 and held[6].source == "measured"
    assert note["positions_shift"] == 4
    assert note["positions"] == str(file)


def test_each_reference_names_the_entry_its_walk_filed_it_as(tmp_path,
                                                             monkeypatch):
    """The hold records what it measured against (`reference_entry`), and
    from here that was nothing: a roll held from a walk could not be laid
    beside its references afterwards (`tools/hold_check.py`). Named now as
    the window's sheet finds it, from the walk's own record; and a position
    whose file names another picture than that is said."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["prescan_entry"] = f"walk-{record['number']:02d}"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    lib = tmp_path / "lib"
    for n in (1, 2, 3):
        (lib / f"walk-{n:02d}").mkdir(parents=True)
    file = _approved_json(folder / "approved.json", [
        {"number": 1, "offset_mm": 0.5, "reference_entry": "library\\walk-01"},
        {"number": 2, "offset_mm": 0.5, "reference_entry": "library\\other"}],
        numbering="strip")
    held, note = scan_roll.hold_from_walk(folder, positions=file,
                                          library_root=lib)
    assert [held[n].reference_entry for n in (1, 2, 3)] == [
        str(lib / f"walk-{n:02d}") for n in (1, 2, 3)]
    assert note["other_reference"] == {2: "other"}
    assert note["not_in_library"] == [] and note["unnamed"] == []


def test_a_reference_is_named_and_compared_without_the_library(tmp_path,
                                                              monkeypatch):
    """Both used to come from the library alone: run where `--library` held
    none of the walk's entries -- a worktree's own, empty `library` -- every
    hold recorded no reference, and a position set on another picture was
    held to without a word. The walk's record names each entry by its id,
    which is what the hold records and what the file is compared with."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["prescan_entry"] = f"walk-{record['number']:02d}"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    file = _approved_json(folder / "approved.json", [
        {"number": 1, "offset_mm": 0.5, "reference_entry": "library\\walk-01"},
        {"number": 2, "offset_mm": 0.5, "reference_entry": "library\\other"}],
        numbering="strip")
    for root in (None, tmp_path / "an-empty-library"):
        held, note = scan_roll.hold_from_walk(folder, positions=file,
                                              library_root=root)
        assert [held[n].reference_entry for n in (1, 2, 3)] == [
            "walk-01", "walk-02", "walk-03"], root
        assert note["other_reference"] == {2: "other"}, root
        assert note["unnamed"] == [] and note["unchecked"] == []
    assert note["not_in_library"] == [1, 2, 3]

    # A walk from before its records named their entries, and no library to
    # find them in: nothing names them, and a file's references cannot be
    # compared with anything -- both said.
    for record in survey["frames"]:
        del record["prescan_entry"]
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    held, note = scan_roll.hold_from_walk(folder, positions=file)
    assert [held[n].reference_entry for n in (1, 2, 3)] == ["", "", ""]
    assert note["unnamed"] == [1, 2, 3]
    assert note["unchecked"] == [1, 2] and note["other_reference"] == {}


def test_what_approved_warns_about_is_said_before_the_film_is_asked_about(
        tmp_path, monkeypatch, capsys):
    """A position set on another picture was warned about after the question
    whether the film is in -- the last thing anyone answers -- and the
    scanner opened straight after it. Said before the question now, with
    this library holding none of the walk's entries, which nothing said."""
    import rps7200.console

    folder = _walk_for_positions(tmp_path, monkeypatch)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["prescan_entry"] = f"walk-{record['number']:02d}"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    file = _approved_json(folder / "approved.json", [
        {"number": 2, "offset_mm": 0.5, "reference_entry": "library\\other"}],
        numbering="strip")
    asked, opened = [], []

    def question(asserted, **kw):
        asked.append(capsys.readouterr())
        return None                               # "yes, the film is in"

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=3)
            opened.append(bool(asked))

    monkeypatch.setattr(rps7200.console, "film_unconfirmed", question)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--out", str(tmp_path / "roll"),
        "--library", str(tmp_path / "lib"), "--roll", "asked",
        "--reference", str(tmp_path / "calibration" / "shading.npz"),
        "--approved", str(folder), "--positions", str(file), "--frames", "3"])
    assert scan_roll.main() == 0
    (before,) = asked
    assert opened == [True], "the device opened before the film was asked about"
    assert f"frame 2's position in {file} was set on other" in before.err
    assert (f"{tmp_path / 'lib'} holds none of the entries the walk in "
            f"{folder} filed its prescans of frame(s) 1, 2, 3 as") in before.err
    assert "estimated" in before.out


class _Holding(FakeRollScanner):
    """Holds each frame it is handed an `Approved` for, recording what the
    driver records (`DirectScanner._hold_to_approved`): frame 1 reaches its
    position in one move, frame 2 runs out of moves, and frame 3 is left
    alone because holding was off by then."""

    def scan_roll(self, **kw):
        approved = kw.get("approved") or {}
        for frame in super().scan_roll(**kw):
            asked = approved.get(frame.index)
            if asked is not None:
                frame.registration = dict(frame.registration,
                                          approved=self.hold(frame.index,
                                                             asked))
            yield frame

    @staticmethod
    def hold(index, asked):
        target = round(asked.offset_mm, 4)
        if index == 0:
            return {"target_mm": target, "outcome": "held", "moves": 1,
                    "arrived_units": 0.0, "final_mm": target - 0.0035}
        if index == 1:
            return {"target_mm": target, "outcome": "not_converged",
                    "moves": 3, "arrived_units": 30.0,
                    "final_mm": 10 * 0.1057}
        return {"target_mm": target, "outcome": "off",
                "reason": "holding was switched off earlier in this roll"}


def test_each_hold_is_said_as_it_happens_and_the_misses_at_the_end(
        tmp_path, monkeypatch, capsys):
    """A hold that missed its position still scans the frame, so the frame
    is delivered somewhere else and only the driver's log said so, in prose,
    an hour back. One line a frame, one shape, and every miss again at the
    end -- without failing a roll whose frames were all scanned."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    file = _approved_json(folder / "approved.json", [
        {"number": 1, "offset_mm": -0.9344, "source": "operator"},
        {"number": 3, "offset_mm": 20.0, "source": "operator"}],
        numbering="strip")
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: _Holding(frames=3))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library",
         str(tmp_path / "lib"), "--no-shading", "--roll", "held",
         "--approved", str(folder), "--positions", str(file), "--frames", "3"])
    capsys.readouterr()
    assert scan_roll.main() == 0
    out, err = capsys.readouterr()
    assert ("hold: frame 1 held target -8.8 u, arrived +0.0 u, now -8.9 u "
            "after 1 move(s)\n") in out
    assert ("hold: frame 2 not_converged target +0.0 u, arrived +30.0 u, now "
            "+10.0 u after 3 move(s) -- did not get close enough in three "
            "moves") in out
    assert ("hold: frame 3 off target +88.8 u, arrived --, now -- after 0 "
            "move(s) -- was left alone; holding had been switched off") in out
    listed = err[err.index("positions not reached"):].splitlines()
    assert listed[0] == "positions not reached (2 of the 3 frame(s) held):"
    assert listed[1].startswith("  frame 2: did not get close enough in "
                                "three moves (not_converged); target +0.0 u, "
                                "now +10.0 u")
    assert listed[2].startswith("  frame 3: was left alone; holding had been "
                                "switched off (off); target +88.8 u, now --")
    assert "frame 1" not in "\n".join(listed[:3])


def test_a_roll_whose_holds_all_reached_says_so(tmp_path, monkeypatch,
                                                capsys):
    """And says nothing is missing when nothing is."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: _Holding(frames=1))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library",
         str(tmp_path / "lib"), "--no-shading", "--roll", "held",
         "--approved", str(folder), "--frames", "1"])
    assert scan_roll.main() == 0
    out, err = capsys.readouterr()
    assert "hold: frame 1 held target +3.8 u" in out
    assert "every one of the 1 frame(s) held reached its position" in out
    assert "positions not reached" not in err


class _HeldThenFailed(FakeRollScanner):
    """Frame 1 held in one move and scanned; frame 2's verification prescan
    timed out after its first move, so the driver files the hold as far as
    it got, `move` (`DirectScanner._hold_to_approved`), in a failed frame's
    marks; frame 3 was held and its scan then failed."""

    def scan_roll(self, **kw):
        approved = kw.get("approved") or {}
        for frame in super().scan_roll(**kw):
            asked = approved.get(frame.index)
            target = round(asked.offset_mm, 4) if asked is not None else 0.0
            if frame.index == 0:
                hold = {"target_mm": target, "outcome": "held", "moves": 1,
                        "arrived_units": 0.0, "final_mm": target}
            elif frame.index == 1:
                hold = {"target_mm": target, "outcome": "move", "moves": 1,
                        "arrived_units": -29.0}
            else:
                hold = {"target_mm": target, "outcome": "held", "moves": 1,
                        "arrived_units": 12.0, "final_mm": target}
            frame.registration = dict(frame.registration, approved=hold)
            if frame.index:
                frame.image = frame.raw_image = None
                frame.meta = {}
                frame.error = "the read timed out"
            yield frame


def test_a_held_frame_that_failed_is_not_listed_as_scanned_off_position(
        tmp_path, monkeypatch, capsys):
    """A pass under a hold that failed leaves its record at the step it was
    taking, `move`, and the roll listed it among the positions not reached
    -- "frame 2: move (move)", as though it had been scanned there. It was
    not scanned at all; nor was a frame held and then failed in its scan.
    Said as not scanned, in words, and listed apart."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: _HeldThenFailed(frames=3))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library",
         str(tmp_path / "lib"), "--no-shading", "--roll", "failed",
         "--approved", str(folder), "--frames", "3"])
    assert scan_roll.main() == 1
    out, err = capsys.readouterr()
    assert ("hold: frame 2 move target +0.0 u, arrived -29.0 u, now -- after "
            "1 move(s) -- was cut off part way by a pass that failed; the "
            "frame was not scanned") in out
    assert ("hold: frame 3 held target +1.9 u, arrived +12.0 u, now +1.9 u "
            "after 1 move(s) -- the frame was not scanned") in out
    assert "positions not reached" not in err
    assert "every one of the 1 frame(s) held reached its position" in out
    listed = err[err.index("held, and then failed"):].splitlines()
    assert listed[:3] == [
        "held, and then failed -- not delivered (2):",
        "  frame 2: was cut off part way by a pass that failed (move) after "
        "1 move(s)",
        "  frame 3: reached the position (held) after 1 move(s), and then "
        "failed"]


# --- winding the film back, and refusing to go on if it did not -------------


class _Transport:
    """A film that can be wound back, and can stop part-way.

    `swallows` is backlash: that many opening commands do nothing, which is
    what a transport left loaded forward really does.
    """

    def __init__(self, at, sticks_at=None, swallows=0):
        self.at, self.sticks_at, self.calls = at, sticks_at, 0
        self.swallows = swallows

    def position(self):
        return self.at

    def retreat(self, **kw):
        self.calls += 1
        if self.swallows:
            self.swallows -= 1
            return None
        if self.sticks_at is not None and self.at <= self.sticks_at:
            return None
        self.at -= 1
        return self.at


def test_a_rewind_that_lands_reports_where_it_got_to():
    t = _Transport(16)
    assert scan_roll.rewind(t, 16) == 0
    assert t.calls == 16


def test_a_rewind_that_stops_short_returns_none():
    """The caller must not go on. `_move` in the window reports this by
    returning a *string* the worker logs before taking the next job, so a
    rewind that got three of fourteen is followed straight away by a roll that
    scans frames it has mis-numbered -- and a break after three successes reads
    identically to a break after none."""
    t = _Transport(16, sticks_at=10)
    assert scan_roll.rewind(t, 16) is None
    assert t.at == 10, "it must stop where it stuck, not keep asking"


def test_a_rewind_goes_one_frame_at_a_time():
    """`retreat(steps=N)` exists, but the wait underneath only watches for the
    position to CHANGE -- so a multi-step call returns as soon as it has moved
    at all and cannot tell sixteen frames from one."""
    t = _Transport(5)
    scan_roll.rewind(t, 5)
    assert t.calls == 5


def test_backlash_at_the_start_is_not_a_failed_rewind():
    """A roll leaves the transport loaded forward, so the first backward
    command is a direction change and two to three of them are swallowed.
    Measured on film 2026-09-21: a 14-frame rewind ran first time after one
    roll and had its first command swallowed after the next."""
    t = _Transport(14, swallows=2)
    assert scan_roll.rewind(t, 14) == 0
    assert t.calls == 16, "two swallowed, then fourteen that moved"


def test_backlash_is_only_tolerated_before_the_film_moves():
    """Once it is moving, a command that does nothing is the end of the strip
    -- not something to keep pushing against."""
    t = _Transport(14, sticks_at=10)
    assert scan_roll.rewind(t, 14) is None
    assert t.at == 10


def test_backlash_tolerance_is_bounded():
    t = _Transport(14, swallows=99)
    assert scan_roll.rewind(t, 14) is None
    assert t.calls == scan_roll.BACKLASH_COMMANDS + 1


@pytest.mark.parametrize("argv", [
    ["--dpi", "7200"],
    ["--start-at", "0"],
    ["--frames", "-1"],
])
def test_what_cannot_work_is_refused_before_the_scanner_opens(tmp_path,
                                                              monkeypatch, argv):
    """The roll calibrates and meters before its first frame; a refusal after
    that spent minutes on what the arguments already said."""
    created = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--roll", "refused", *argv],
    )
    with pytest.raises(SystemExit) as refused:
        scan_roll.main()
    assert refused.value.code == 2
    assert created == []


def test_a_walk_files_its_prescans_with_their_raw_pixels(tmp_path, monkeypatch):
    """A walk from here left only corrected prescanNN.tif -- nothing that could
    be re-decoded, and the references `--approved` holds frames to later."""
    import json

    from rps7200 import library

    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "2")
    assert code == 0
    filed = sorted((tmp_path / "lib").glob("*/scan.json"))
    assert len(filed) == 2
    for path in filed:
        record = json.loads(path.read_text(encoding="utf-8"))
        image, _ = library.load(path.parent)
        assert int(image.max()) == 30, "the corrected prescan was filed"
        member = record["extra"]["roll_membership"]
        assert member["kind"] == "prescan" and member["roll"] == "teststrip"
        assert record["film"]["frame"].startswith("teststrip-")


def test_a_frame_is_labelled_the_way_the_window_labels_it(tmp_path, monkeypatch):
    import json

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "1")
    assert code == 0
    for path in filed(tmp_path / "lib", "frame") + filed(tmp_path / "lib",
                                                          "prescan"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["film"]["frame"] == "teststrip-01"
    assert len(filed(tmp_path / "lib", "frame")) == 1


# --- filing that fails, and what debug filing is told ------------------------


class _Patient(FakeRollScanner):
    """Waits between frames for the writer, as a real frame's minutes do.

    The fake yields its frames at once, so the writer would not have filed
    the first before the last was scanned. Here each frame after the first
    waits, up to a second, for the stop the roll would see in that time.
    """

    def scan_roll(self, **kw):
        import time

        stop = kw.get("should_stop") or (lambda: False)
        for frame in super().scan_roll(**kw):
            if frame.index:
                deadline = time.monotonic() + 1.0
                while not stop() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if stop():
                    return
            yield frame


def _refusing_library(tmp_path, monkeypatch, scanner_class, *argv, frames=4):
    """Run with a library that refuses every frame filed into it -- as a disk
    that filled during the roll does. A library under a file stood for one;
    it is refused before the device opens now, as a missing drive is."""
    from rps7200 import library

    refused = tmp_path / "lib"
    real = library.save

    def save(image, meta, *, root=library.DEFAULT_ROOT, **kw):
        if Path(root) == refused:
            raise OSError(28, "No space left on device")
        return real(image, meta, root=root, **kw)

    monkeypatch.setattr(library, "save", save)
    created = []

    class Patched(scanner_class):
        def __init__(self, **kw):
            super().__init__(frames=frames)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(refused), "--no-shading",
         "--roll", "refused", "--frames", str(frames), *argv],
    )
    return created, scan_roll.main()


def test_a_roll_whose_filing_fails_stops_at_once(tmp_path, monkeypatch, capsys):
    """A full disk or a library that has gone fails every frame after it the
    same way. The tool scanned on for hours, printing a success line per
    frame, and said so only at the end; the window stops after the frame in
    flight, and so does this now."""
    created, code = _refusing_library(tmp_path, monkeypatch, _Patient)
    assert code != 0
    err = capsys.readouterr().err
    assert "could not be filed" in err
    assert len(list((tmp_path / "roll").glob("frame*.tif"))) < 4, (
        "the roll scanned on into a library it could not write")
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert "could not be filed" in manifest.get("stopped", "")


def test_a_frame_the_library_refused_names_the_copy_it_wrote(tmp_path,
                                                            monkeypatch):
    """Its frameNN.tif is written whatever the library says, and its record
    named no file: the writer's answer for a refused frame said nothing had
    been written, so a resume, a carry or anything else reading the record
    could not find the copy that was kept."""
    _created, code = _refusing_library(tmp_path, monkeypatch, _Patient,
                                       frames=1)
    assert code != 0
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    (record,) = manifest["frames"]
    assert record["done"] is False
    assert "could not be filed" in record["filing_error"]
    assert record["file"] == "frame01.tif"
    assert (tmp_path / "roll" / record["file"]).exists()


def test_a_refused_frames_raw_data_is_compressed_once_the_device_closes(
        tmp_path, monkeypatch):
    """Kept plain, as everything filed with the device open is -- and only
    the window's close compacted what its writer kept plain, so from here it
    stayed raw.bin and uncompressed TIFFs for good."""
    from rps7200 import library

    _created, code = _refusing_library(tmp_path, monkeypatch, _Patient,
                                       frames=1)
    assert code != 0
    (kept,) = [p.parent for p in
               (tmp_path / "roll" / "unfiled").glob("*/scan.json")]
    assert (kept / library.RAW_FILE).exists()
    assert not (kept / library.RAW_PLAIN).exists()
    assert library.read_raw(kept) == b"raw-bytes"


class _Claiming(FakeRollScanner):
    """Records what the tool tells debug filing, and when the scanner exits."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.claimed = []
        self.answered = []
        self.order = []

    def debug_claim(self, pixels):
        self.claimed.append(pixels)
        # The receipt, as `DirectScanner.debug_claim` hands one back.
        return self.answered.append

    def __exit__(self, *exc):
        self.order.append("scanner exited")


def test_a_frame_whose_filing_failed_is_answered_as_not_filed(
        tmp_path, monkeypatch):
    """Claimed as it was queued and let go on the claim alone, a frame whose
    filing failed was deleted from the debug spool at close as well -- the
    one copy RPS7200_DEBUG=1 keeps. The claim is answered once the writer is
    done with it, and here the answer is that it was not filed."""
    created, code = _refusing_library(tmp_path, monkeypatch, _Claiming,
                                      frames=2)
    assert code != 0
    assert created[0].claimed, "nothing was claimed"
    assert created[0].answered == [None] * len(created[0].claimed)


def test_debug_filing_runs_once_every_frame_is_filed(tmp_path, monkeypatch):
    """The scanner's exit files what debug filing spooled and deletes what
    was claimed. Run as the device closed, it came before the writer had
    filed the last frames: those were deleted unfiled, or filed twice."""
    from rps7200 import session

    created = []

    class Patched(_Claiming):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    real_finish = session.FrameWriter.finish

    def finish(self):
        created[0].order.append("writer finished")
        return real_finish(self)

    monkeypatch.setattr(session.FrameWriter, "finish", finish)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "ordered", "--frames", "2"],
    )
    assert scan_roll.main() == 0
    assert created[0].order == ["writer finished", "scanner exited"]
    assert len(created[0].claimed) == 2
    assert all(int(p.max()) == RAW_LEVEL for p in created[0].claimed)


def test_a_ctrl_c_while_filing_waits_for_the_filing_and_debug_filing(
        tmp_path, monkeypatch):
    """The writer's last frames and debug filing ran after the roll's Ctrl-C
    deferral had ended, once `HeldOpen` moved debug filing after the writer.
    A Ctrl-C while the last frames gzipped raised there: debug filing never
    ran, and what it had spooled was left where nothing names it."""
    import signal

    from rps7200 import session

    created = []

    class Patched(_Claiming):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    real_finish = session.FrameWriter.finish

    def finish(self):
        # A Ctrl-C now, taken by whatever handler is in force.
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        created[0].order.append("writer finished")
        return real_finish(self)

    monkeypatch.setattr(session.FrameWriter, "finish", finish)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "ordered", "--frames", "2"],
    )
    # SIGINT as a tool started from a terminal has it, for this test only.
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code = scan_roll.main()
    except KeyboardInterrupt:
        pytest.fail("one Ctrl-C cut the filing short: "
                    f"{created[0].order}")
    finally:
        signal.signal(signal.SIGINT, previous)
    assert created[0].order == ["writer finished", "scanner exited"]
    # Two frames, and the prescan each was framed on, in an entry of its own.
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 4
    assert code == 0


def test_a_walks_prescans_are_written_off_the_scanning_thread(tmp_path,
                                                             monkeypatch):
    """Deflated on the thread that drives the scanner, each prescan held the
    device open and idle while it was written; the window's walk has always
    handed them to its writer."""
    import threading

    from rps7200 import export

    threads = []
    real = export.write

    def write(path, image, **kw):
        threads.append((str(path), threading.current_thread()))
        return real(path, image, **kw)

    monkeypatch.setattr(export, "write", write)
    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "2")
    assert code == 0
    written = [(p, t) for p, t in threads if "prescan" in p]
    assert len(written) == 2
    assert all(t is not threading.main_thread() for _p, t in written)
    assert sorted(p.name for p in (tmp_path / "roll").glob("prescan*.tif")) \
        == ["prescan01.tif", "prescan02.tif"]


def test_a_walk_with_the_library_off_still_leaves_its_prescans(tmp_path,
                                                              monkeypatch):
    created = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--no-shading", "--roll", "nolib", "--dry-run", "--frames", "2"],
    )
    assert scan_roll.main() == 0
    assert sorted(p.name for p in (tmp_path / "roll").glob("prescan*.tif")) \
        == ["prescan01.tif", "prescan02.tif"]


def test_a_walk_prescan_is_not_filed_with_a_later_passs_bytes(tmp_path,
                                                             monkeypatch):
    """The bytes on hand are the scanner's last pass's. A prescan whose raw
    pixels are not that pass's array was not that pass, however alike their
    shapes -- a verification prescan taken after it looks exactly the same."""
    from rps7200 import library

    created = []

    class Verified(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)
            self.last_pixels_raw = np.zeros((3, 3, 3), np.uint8)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Verified)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "verified", "--dry-run", "--frames", "1"],
    )
    assert scan_roll.main() == 0
    (entry,) = [p.parent for p in (tmp_path / "lib").glob("*/scan.json")]
    assert library.read_raw(entry) is None


def test_a_walk_prescan_that_was_the_last_pass_keeps_its_bytes(tmp_path,
                                                              monkeypatch):
    from rps7200 import library

    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "1")
    assert code == 0
    (entry,) = [p.parent for p in (tmp_path / "lib").glob("*/scan.json")]
    assert library.read_raw(entry) == b"raw-bytes"


def test_a_roll_that_ends_short_of_the_frames_asked_for_says_so(
        tmp_path, monkeypatch, capsys):
    """A frame with no picture in it reads as the end of the film. With
    --frames 36 the roll could end at 12, print "12 scanned, 0 failed" and
    exit 0 -- success, to whatever was checking an unattended run."""

    class EndsEarly(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if frame.index == 2:
                    return            # frame 3 held no picture
                yield frame

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: EndsEarly(frames=5))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "short", "--frames", "5"],
    )
    code = scan_roll.main()
    assert len(list((tmp_path / "roll").glob("frame*.tif"))) == 2
    assert code != 0, "ended short of what was asked, and called it success"
    assert "ended after 2 of the 5 frames" in capsys.readouterr().err
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert "ended after 2 of the 5" in manifest["stopped"]


def test_a_roll_with_no_count_that_runs_to_the_end_is_not_short(tmp_path,
                                                                monkeypatch):
    _scanner, code = run(tmp_path, monkeypatch)
    assert code == 0


# --- resuming just the frames that were left ---------------------------------


def test_only_reaches_the_driver_as_places_on_the_strip(tmp_path, monkeypatch):
    """A resume that scans from --start-at to the end took again every frame
    already done: hours at 3600 dpi, and a second library entry for each."""
    scanner, code = run(tmp_path, monkeypatch, "--start-at", "3",
                        "--only", "3,9,15")
    assert code == 0
    assert scanner.asked["only"] == (2, 8, 14)
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert manifest["settings"]["only"] == [3, 9, 15]


@pytest.mark.parametrize("argv", [
    ["--only", "0"],
    ["--only", "two"],
    ["--start-at", "5", "--only", "3,9"],     # 3 is behind where it starts
])
def test_an_only_that_cannot_be_reached_is_refused_before_opening(
        tmp_path, monkeypatch, argv):
    opened = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, *argv, opened=opened)
    assert refused.value.code == 2
    assert opened == []


def test_the_advice_to_resume_names_every_frame_left(tmp_path, monkeypatch,
                                                     capsys):
    class TwoBad(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if frame.index in (0, 2):
                    yield type(frame)(
                        index=frame.index, position=frame.position,
                        image=None, meta={}, prescan=frame.prescan,
                        registration={}, error="the read timed out")
                else:
                    yield frame

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: TwoBad(frames=4))
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                      "--no-shading", "--frames", "4",
                                      "--roll", "twobad",
                                      "--out", str(tmp_path / "roll")])
    assert scan_roll.main() == 1
    err = capsys.readouterr().err
    assert "--start-at 1 --only 1,3" in err


class _Driven(FakeRollScanner):
    """Ends where the driver's own loop ends, and yields what it yields.

    `DirectScanner.roll_ends` for --frames and --only, a frame nobody chose
    advanced past without being yielded, ``failing`` (places on the strip,
    from 1) yielded with an error, and `max_failures` of those in a row
    ending the roll with no word to the caller. The fake above yields
    ``frames`` pictures whatever --only says, so it could not show a roll
    that scanned every frame it chose being called short.
    """

    failing: frozenset = frozenset()
    #: The last place with a picture in it, from 1.
    strip = 12

    def scan_roll(self, **kw):
        self.asked = dict(kw)
        first = kw.get("first_index", 0)
        only = kw.get("only")
        wanted = None if only is None else frozenset(only)
        finished = DirectScanner.roll_ends(first, 0, kw.get("frames"), wanted)
        index, in_a_row = first, 0
        while not finished(index) and index < self.strip:
            if wanted is None or index in wanted:
                if index + 1 in self.failing:
                    in_a_row += 1
                    yield RollFrame(index=index, position=self.at, image=None,
                                    meta={}, prescan=None, registration={},
                                    error="the read timed out")
                    if in_a_row >= kw.get("max_failures", 3):
                        return
                else:
                    in_a_row = 0
                    shape = (6, 6, 3)
                    yield RollFrame(
                        index=index, position=self.at,
                        image=np.full(shape, CORRECTED_LEVEL, np.uint16),
                        meta={"resolution_dpi": 1800,
                              "channel_order": list("RGB")},
                        prescan=None, registration={},
                        raw_image=np.full(shape, RAW_LEVEL, np.uint16))
            index += 1
            self.at += 1


def _driven(tmp_path, monkeypatch, *argv, failing=()):
    class Patched(_Driven):
        pass

    Patched.failing = frozenset(failing)
    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: Patched(frames=0))
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--library", "",
                                      "--no-shading", "--roll", "driven",
                                      "--out", str(tmp_path / "roll"), *argv])
    return scan_roll.main()


def test_a_resume_with_only_and_frames_is_not_short(tmp_path, monkeypatch,
                                                    capsys):
    """--frames counts places on the strip and --only the frames chosen
    among them. The advice's --start-at and --only, added to a first run's
    command line with its --frames, scanned both frames it chose and failed,
    "ended after 2 of the 10 frames asked for"."""
    code = _driven(tmp_path, monkeypatch, "--start-at", "2", "--only", "2,5",
                   "--frames", "10")
    err = capsys.readouterr().err
    assert len(list((tmp_path / "roll").glob("frame*.tif"))) == 2
    assert "ended after" not in err
    assert code == 0
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert "stopped" not in manifest


def test_a_roll_that_ends_before_its_chosen_frames_is_short(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """And short is still short: judged on the frames chosen, the strip
    running out before the last of them is a roll that did not finish."""
    code = _driven(tmp_path, monkeypatch, "--only", "2,5,14")
    assert code == 1
    assert "ended after 2 of the 3 frames" in capsys.readouterr().err


def test_an_only_past_the_end_of_frames_is_refused_before_opening(
        tmp_path, monkeypatch):
    opened = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--start-at", "2", "--only", "2,5",
            "--frames", "3", opened=opened)
    assert refused.value.code == 2
    assert opened == []


def test_the_estimate_costs_the_chosen_frames_not_frames(tmp_path, monkeypatch,
                                                         capsys):
    _driven(tmp_path, monkeypatch, "--start-at", "2", "--only", "2,5",
            "--frames", "10")
    assert "scanning the 2 chosen frame(s)" in capsys.readouterr().out


def _advice(err: str) -> list[str]:
    return [line for line in err.splitlines()
            if line.startswith(("resume ", "and "))]


def test_the_advice_names_the_frames_a_roll_that_gave_up_never_reached(
        tmp_path, monkeypatch, capsys):
    """Frames 3-5 of 8 fail, and the driver gives up. The advice named only
    the frames with a record, and followed as printed it never scanned 6-8."""
    code = _driven(tmp_path, monkeypatch, "--frames", "8",
                   failing=(3, 4, 5))
    assert code == 1
    out = scan_roll._quoted(tmp_path / "roll")
    assert _advice(capsys.readouterr().err) == [
        f"resume the unfinished frames with --out {out} --start-at 3 "
        "--only 3,4,5,6,7,8"]


def test_the_advice_goes_on_to_the_end_of_a_strip_it_did_not_reach(
        tmp_path, monkeypatch, capsys):
    """With no --frames the roll runs to the end of the strip, and giving up
    at frame 5 is not that end: the rest has to be asked for as well."""
    code = _driven(tmp_path, monkeypatch, failing=(3, 4, 5))
    assert code == 1
    out = scan_roll._quoted(tmp_path / "roll")
    assert _advice(capsys.readouterr().err) == [
        f"resume the unfinished frames with --out {out} --start-at 3 "
        "--only 3,4,5",
        "and the frames this run never reached, to the end of the strip, "
        f"with --out {out} --start-at 6"]


def test_a_strip_that_ran_out_is_not_advised_past(tmp_path, monkeypatch,
                                                  capsys):
    """A roll with no end asked for that ended at a blank frame ended where
    the strip does; only its failed frame is left."""
    code = _driven(tmp_path, monkeypatch, failing=(3,))
    assert code == 1
    (advice,) = _advice(capsys.readouterr().err)
    assert advice.endswith("--start-at 3 --only 3")


def test_approved_scans_as_the_film_its_walk_was_made_on(tmp_path, monkeypatch,
                                                         capsys):
    """--film was negative unless typed, whatever the walk was: a slide walk
    scanned from here was metered per channel, which takes a slide's own
    cast off -- baked into the raw bytes, where nothing re-derives it."""
    slides = _walked_at(tmp_path, monkeypatch, 300, film="positive")
    scanner, code = run(tmp_path, monkeypatch, "--approved", str(slides),
                        "--frames", "1")
    assert code == 0
    assert scanner.asked["film"] == "positive"
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert manifest["settings"]["film"] == "positive"


def test_a_film_typed_against_the_walks_wins_and_is_said(tmp_path, monkeypatch,
                                                        capsys):
    slides = _walked_at(tmp_path, monkeypatch, 300, film="positive")
    scanner, code = run(tmp_path, monkeypatch, "--approved", str(slides),
                        "--film", "negative", "--frames", "1")
    assert code == 0
    assert scanner.asked["film"] == "negative"
    assert "was made on positive film" in capsys.readouterr().err


def test_infrared_on_a_walk_of_black_and_white_is_refused_before_opening(
        tmp_path, monkeypatch):
    """--ir is refused for film infrared cannot see through; a B&W walk
    adopted as the film is that film, typed or not."""
    bw = _walked_at(tmp_path, monkeypatch, 300, film="bw")
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--approved", str(bw), "--ir",
            "--frames", "1", opened=opened)
    assert refused.value.code == 2
    assert opened == []


def test_a_roll_without_approved_is_still_negative_unless_told(tmp_path,
                                                             monkeypatch):
    scanner, code = run(tmp_path, monkeypatch, "--frames", "1")
    assert code == 0
    assert scanner.asked["film"] == "negative"


# --- a resume holds to what the roll was taken with --------------------------


def _earlier_roll(tmp_path, settings):
    folder = tmp_path / "roll"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "teststrip", "numbering": "strip", "settings": settings,
        "frames": [{"number": 1, "transport_position": 0, "done": True}],
    }), encoding="utf-8")
    return folder


@pytest.mark.parametrize("argv, said", [
    (["--dpi", "3600"], "dpi 1800"),
    (["--ir"], "infrared False"),
    (["--film", "positive"], "film negative"),
    (["--meter", "none"], "metering each"),
])
def test_a_resume_that_asks_for_other_settings_is_refused(
        tmp_path, monkeypatch, capsys, argv, said):
    """Night one at 1800 dpi RGB, night two resumed with a flag changed or
    forgotten: the roll silently mixed resolutions, channels or metering --
    baked into the raw bytes -- and roll.json recorded only the last run's."""
    _earlier_roll(tmp_path, {"dpi": 1800, "infrared": False,
                             "film": "negative", "meter": "each"})
    opened: list = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--start-at", "2", *argv, opened=opened)
    assert refused.value.code == 2
    assert opened == [], "the scanner was opened for a resume that differs"
    assert said in capsys.readouterr().err


def test_a_rewind_alone_is_not_held_to_the_rolls_settings(tmp_path,
                                                          monkeypatch):
    """--frames 0 rewinds and stops, and scans nothing that could differ;
    it was refused for a resolution it never asked for."""
    _earlier_roll(tmp_path, {"dpi": 3600, "infrared": False,
                             "film": "negative", "meter": "each"})
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "0")
    assert code == 0


def test_a_resume_with_the_same_settings_goes_ahead(tmp_path, monkeypatch):
    _earlier_roll(tmp_path, {"dpi": 1800, "infrared": False,
                             "film": "negative", "meter": "each"})
    _scanner, code = run(tmp_path, monkeypatch, "--start-at", "2",
                         "--frames", "1")
    assert code == 0


def test_a_resume_of_a_window_roll_is_held_to_its_resolution(tmp_path,
                                                             monkeypatch):
    """The window writes `resolution` where this tool writes `dpi`."""
    _earlier_roll(tmp_path, {"resolution": 600, "infrared": False,
                             "film": "negative", "meter": "each"})
    with pytest.raises(SystemExit):
        run(tmp_path, monkeypatch, "--start-at", "2")


def test_the_roll_records_what_its_frames_were_taken_with(tmp_path, monkeypatch):
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "1", "--correct")
    assert code == 0
    settings = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))["settings"]
    assert settings["shading"] is False           # the helper runs --no-shading
    assert settings["correct"] is True
    assert settings["fast_infrared"] is False     # an RGB roll
    assert settings["max_failures"] == 3
    # Which code read the frames is each frame's to say, not the run's.
    assert "code" not in settings


class _Aiming(FakeRollScanner):
    """Marks each frame as the driver's aiming roll does when asked to aim:
    what the walk took of its prescan (`base`) and the aim's judgment
    (`correction`). Nothing else of the driver's roll is imitated."""

    def scan_roll(self, **kw):
        for frame in super().scan_roll(**kw):
            if kw.get("correct") or kw.get("correct_dry_run"):
                frame.registration = dict(
                    frame.registration,
                    base={"reader": "frame_edges", "index": frame.index},
                    correction={"outcome": "in_place", "ensemble": {}})
            yield frame


def _frames_of(tmp_path, name="roll.json") -> dict:
    written = json.loads((tmp_path / "roll" / name).read_text(encoding="utf-8"))
    return {f["number"]: f for f in written["frames"]}


def test_a_roll_nothing_reads_the_frames_of_names_no_code(tmp_path, monkeypatch):
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "1",
                         cls=_Aiming)
    assert code == 0
    settings = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))["settings"]
    assert "code" not in settings
    assert "read_by" not in _frames_of(tmp_path)[1]


def test_a_resumed_roll_keeps_the_code_each_frame_was_read_by(tmp_path,
                                                             monkeypatch):
    """The resume carries the earlier run's frames on whole, under settings
    it writes afresh. Named once for the file, frame 1's reading was named
    by no code once the resume did not aim -- and the hashes differ across
    sessions on any edit to the source. Each frame names its own; the
    identities here stand for two such sessions."""
    from rps7200 import library

    monkeypatch.setattr(library, "code_identity", lambda: {"session": 1})
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "1", "--correct",
                         cls=_Aiming)
    assert code == 0
    assert _frames_of(tmp_path)[1]["read_by"] == {"session": 1}
    monkeypatch.setattr(library, "code_identity", lambda: {"session": 2})
    _scanner, code = run(tmp_path, monkeypatch, "--start-at", "2",
                         "--frames", "2", cls=_Aiming)
    assert code == 0
    frames = _frames_of(tmp_path)
    assert sorted(frames) == [1, 2, 3]
    assert frames[1]["registration"]["base"]["reader"] == "frame_edges"
    assert frames[1]["read_by"] == {"session": 1}
    assert "read_by" not in frames[2] and "read_by" not in frames[3]
    # And a resume that aims names its own frames by its own code.
    monkeypatch.setattr(library, "code_identity", lambda: {"session": 3})
    _scanner, code = run(tmp_path, monkeypatch, "--start-at", "3",
                         "--frames", "1", "--correct", cls=_Aiming)
    assert code == 0
    frames = _frames_of(tmp_path)
    assert [frames[n].get("read_by") for n in (1, 2, 3)] == [
        {"session": 1}, None, {"session": 3}]


def test_a_roll_held_to_proposed_positions_keeps_what_was_read(tmp_path,
                                                              monkeypatch):
    """--approved is the sheet's path with the window taken off, and kept
    the positions alone: what the detector read of each frame it held goes
    in beside them, on the frame's record as the window's approved.json
    keeps it, with the code that read it."""
    from rps7200 import library

    folder = _walked_at(tmp_path, monkeypatch, 300)
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                         "--frames", "2")
    assert code == 0
    written = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    assert "code" not in written["settings"]
    assert "readings" not in written["held"]
    for n in (1, 2):
        record = _frames_of(tmp_path)[n]
        assert record["reading"] == {"source": "measured"}
        assert record["read_by"] == library.code_identity()
        assert "detector_reading" not in record


def test_a_resumed_held_roll_keeps_the_reading_each_frame_was_held_to(
        tmp_path, monkeypatch):
    """`held` is rebuilt by every run: on a resume it held the new run's
    reading of frames the earlier run had held by its own -- under the new
    run's code. The reading a frame was held to goes on with its record."""
    from rps7200 import library

    folder = _walked_at(tmp_path, monkeypatch, 300)
    for session, argv in ((1, ("--frames", "1")),
                          (2, ("--start-at", "2", "--frames", "1"))):
        monkeypatch.setattr(library, "code_identity",
                            lambda s=session: {"session": s})
        monkeypatch.setattr(
            scan_roll.frame_edges, "propose_centred",
            lambda frames, film=None, s=session: (
                {n: 0.0 for n, _ in frames},
                {n: {"source": "measured", "session": s} for n, _ in frames}))
        _scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                             *argv)
        assert code == 0
    frames = _frames_of(tmp_path)
    for n in (1, 2):
        assert frames[n]["reading"] == {"source": "measured", "session": n}
        assert frames[n]["read_by"] == {"session": n}


def test_a_position_he_set_keeps_the_detectors_reading_beside_it(
        tmp_path, monkeypatch):
    """As approved.json keys it: a position from --positions is his, and
    what the detector read of that frame is kept as `detector_reading`."""
    folder = _walk_for_positions(tmp_path, monkeypatch)
    file = _approved_json(folder / "approved.json", [
        {"number": 1, "offset_mm": -0.9344, "source": "operator"}],
        numbering="strip")
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                         "--positions", str(file), "--frames", "2")
    assert code == 0
    frames = _frames_of(tmp_path)
    assert frames[1]["detector_reading"] == {"source": "measured"}
    assert "reading" not in frames[1]
    assert frames[2]["reading"] == {"source": "measured"}
    assert "detector_reading" not in frames[2]


# --- what it says it will cost ------------------------------------------------


def test_a_roll_says_how_long_it_will_take_before_it_opens(tmp_path,
                                                          monkeypatch, capsys):
    """CLAUDE.md sends a walk here and says a run past ~8 minutes must be
    backgrounded; this tool, the likeliest to pass that line, said nothing."""
    said_before_opening = []

    class Watched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)
            said_before_opening.append(capsys.readouterr())

    monkeypatch.setattr(scan_roll, "DirectScanner", Watched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--no-shading", "--roll", "costed", "--dry-run", "--frames", "1"],
    )
    assert scan_roll.main() == 0
    (said,) = said_before_opening
    assert "estimated" in said.out
    assert "background" not in said.err, "one prescan is not eight minutes"


def test_a_roll_to_the_end_of_the_strip_is_told_to_go_to_the_background(
        tmp_path, monkeypatch, capsys):
    """With no --frames it runs until the strip does: a whole strip's walk
    is a calibration and a prescan a frame, well past ten minutes."""
    _scanner, code = run(tmp_path, monkeypatch, "--dry-run")
    assert code == 0
    out = capsys.readouterr()
    assert "to the end of the strip" in out.out
    assert "background" in out.err


def test_a_real_roll_costs_its_scans(tmp_path, monkeypatch, capsys):
    _scanner, code = run(tmp_path, monkeypatch, "--dpi", "3600", "--ir",
                         "--frames", "3")
    assert code == 0
    assert "background" in capsys.readouterr().err


def _long_walk(tmp_path, monkeypatch, frames=10):
    """A walk of ``frames`` frames, numbered by the strip, each proposed
    where the walk saw it."""
    folder = tmp_path / "long-walk"
    _prescans(folder, range(1, frames + 1))
    (folder / "survey.json").write_text(json.dumps({
        "roll": "long-walk", "numbering": "strip", "dry_run": True,
        "settings": {"prescan_resolution": 300},
        "frames": [{"number": n, "transport_position": n - 1,
                    "prescan": f"prescan{n:02d}.tif"}
                   for n in range(1, frames + 1)]}), encoding="utf-8")
    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    return folder


def test_a_held_walk_costs_the_prescans_its_holds_take(tmp_path, monkeypatch,
                                                      capsys):
    """Each held frame is placed by up to three moves, each a SLIDE and a
    verification prescan, and the estimate left them out: a ten-frame held
    walk came to 475 s at the slow end, under the 480 that warns, with up to
    thirty more prescans to take. Costed at the cap, it is told to go to the
    background."""
    from rps7200 import session
    from rps7200.framing import MAX_HOLD_MOVES

    folder = _long_walk(tmp_path, monkeypatch)
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                         "--dry-run", "--frames", "10")
    assert code == 0
    out, err = capsys.readouterr()
    assert ("walking 10 frame(s), each held with up to "
            f"{MAX_HOLD_MOVES} more prescans:") in out
    assert "background" in err

    # By exactly the looks: a prescan each at the slow end, and the move.
    args = scan_roll.build_parser().parse_args(
        ["--dry-run", "--frames", "10", "--no-shading", "--prescan-dpi",
         "300", "--start-at", "3"])
    bare = scan_roll.say_roll_estimate(args)
    # Frames 1 and 2 are before --start-at, and 13 past --frames: not held.
    held = scan_roll.say_roll_estimate(args, held=[1, 2, *range(3, 9), 13])
    look = (session.estimate_seconds(300, False) * session.SLOW_PASS
            + session.HOLD_MOVE_S)
    assert held - bare == pytest.approx(6 * MAX_HOLD_MOVES * look)
    # And an aim takes the same loop, on every frame it reaches.
    args.correct = True
    assert (scan_roll.say_roll_estimate(args) - bare
            == pytest.approx(10 * MAX_HOLD_MOVES * look))


def test_a_nudge_is_asked_for_in_the_windows_units(tmp_path, monkeypatch,
                                                   capsys):
    """--nudge took millimetres, which CLAUDE.md prohibits for transport
    distances; a value read off the window, which shows units, moved the film
    about 9.5 times as far as meant."""
    from rps7200.protocol import units

    sent = []

    class Nudged(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)

        def nudge(self, millimetres):
            sent.append(millimetres)
            return {"asked_mm": millimetres}

    monkeypatch.setattr(scan_roll.time, "sleep", lambda s: None)
    monkeypatch.setattr(scan_roll, "DirectScanner", Nudged)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--no-shading", "--roll", "nudged", "--nudge", "20", "--frames", "1"],
    )
    assert scan_roll.main() == 0
    assert abs(units(sum(sent)) - 20) < 2.84, "not the 20 units asked for"
    out = capsys.readouterr().out
    assert "units" in out and " mm" not in out


@pytest.mark.parametrize("argv, channels", [
    (["--film", "bw"], 1),
    (["--film", "bw", "--no-mono"], 3),
    (["--film", "negative"], 3),
    (["--film", "negative", "--mono"], 1),
])
def test_a_black_and_white_roll_is_delivered_in_one_channel(
        tmp_path, monkeypatch, argv, channels):
    """The same B&W strip came out RGB from here and mono from the window
    and tools/scan.py, and was taken for colour negative by what read it."""
    from rps7200 import library, tiff

    _scanner, code = run(tmp_path, monkeypatch, "--frames", "1", *argv)
    assert code == 0
    delivered = tiff.read(tmp_path / "roll" / "frame01.tif")
    assert (1 if delivered.ndim == 2 else delivered.shape[2]) == channels
    # The frame's entry; the prescan it was framed on has one of its own.
    (entry,) = [tmp_path / "lib" / e["id"]
                for e in library.entries(tmp_path / "lib")
                if "prescan" not in e["tags"]]
    image, _record = library.load(entry)
    assert image.shape[2] == 3, "the library keeps all three regardless"


# --- the reference, and where debug filing files ------------------------------


def test_a_reference_inside_a_library_entry_is_refused_before_opening(
        tmp_path, monkeypatch):
    entry = tmp_path / "lib" / "20260927T000000Z_x_300dpi"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}", encoding="utf-8")
    opened: list = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__()
            opened.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--out", str(tmp_path / "roll"),
        "--library", str(tmp_path / "lib"), "--roll", "ref",
        "--reference", str(entry / "shading.npz"), "--frames", "1"])
    with pytest.raises(SystemExit) as refused:
        scan_roll.main()
    assert refused.value.code == 2
    assert opened == []


def test_a_roll_files_its_debug_passes_into_its_own_library(tmp_path,
                                                           monkeypatch):
    import os

    monkeypatch.delenv("RPS7200_DEBUG_ROOT", raising=False)
    seen = []

    class Exiting(FakeRollScanner):
        debug_root = None

        def __exit__(self, *exc):
            seen.append(self.debug_root)

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: Exiting(frames=1))
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--out", str(tmp_path / "roll"),
        "--library", str(tmp_path / "lib"), "--no-shading",
        "--roll", "own", "--frames", "1"])
    assert scan_roll.main() == 0
    assert seen == [str(tmp_path / "lib")]
    assert "RPS7200_DEBUG_ROOT" not in os.environ


# --- on the driver itself ----------------------------------------------------
#
# The doubles above file b"raw-bytes" beside every frame, which no decode could
# turn into its pixels, and this file's CORRECTED_LEVEL was never read back out
# of a frameNN.tif. These run the real `DirectScanner` -- its roll loop,
# metering, prescans and passes -- on a device double with a strip in it.


def run_on_device(tmp_path, monkeypatch, *argv):
    from conftest import tool_on_device

    # --film-loaded: these calibrate, and stand in for an operator who has
    # said the film is in.
    devices = tool_on_device(scan_roll, monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"),
         "--reference", str(tmp_path / "calibration" / "shading.npz"),
         "--roll", "teststrip", "--dpi", "300", "--film-loaded", *argv],
    )
    return devices, scan_roll.main()


def _filed(root):
    from rps7200 import library
    return {r["extra"]["roll_membership"]["number"]: root / r["id"]
            for r in library.entries(root)}


def test_a_rolls_frames_reconstruct_and_its_frame_files_are_corrected(
        tmp_path, monkeypatch):
    """Each frame's entry re-decodes to itself from its own bytes, and each
    frameNN.tif is that entry corrected -- the picture delivered, not the
    sensor's."""
    from rps7200 import library, tiff

    devices, code = run_on_device(tmp_path, monkeypatch, "--frames", "2")
    assert code == 0
    filed = _filed(tmp_path / "lib")
    assert sorted(filed) == [1, 2]
    sent = {p["blob"] for p in devices[0].passes}
    for number, entry in filed.items():
        assert library.reconstruct(entry)[1].startswith("identical"), number
        assert library.load(entry)[1]["image"]["corrections_applied"] == []
        assert library.read_raw(entry) in sent
        delivered = tiff.read(str(tmp_path / "roll" / f"frame{number:02d}.tif"))
        assert np.array_equal(delivered, library.corrected(entry)[0]), number
        assert not np.array_equal(delivered, library.load(entry)[0]), number


def test_a_walks_prescans_reconstruct_and_its_prescan_files_are_corrected(
        tmp_path, monkeypatch):
    from rps7200 import library, tiff

    _devices, code = run_on_device(tmp_path, monkeypatch, "--dry-run",
                                   "--frames", "2")
    assert code == 0
    filed = _filed(tmp_path / "lib")
    assert sorted(filed) == [1, 2]
    for number, entry in filed.items():
        assert library.reconstruct(entry)[1].startswith("identical"), number
        assert library.load(entry)[1]["image"]["corrections_applied"] == [], \
            "the corrected prescan was filed in place of the raw one"
        delivered = tiff.read(
            str(tmp_path / "roll" / f"prescan{number:02d}.tif"))
        assert np.array_equal(delivered, library.corrected(entry)[0]), number
        assert not np.array_equal(delivered, library.load(entry)[0]), number


def test_a_roll_stopped_by_ctrl_c_exits_130(tmp_path, monkeypatch):
    """A roll stopped at Ctrl-C with nothing lost returned 0: the manifest's
    "stopped" kept it from counting as short, and the exit ignored the
    interrupt. `tools/scan.py` says 130 in the same case, and a caller
    checking the status is who needs to know the roll did not finish."""
    from rps7200.console import DeferredInterrupt

    asked = {"stop": False}

    class Asked(DeferredInterrupt):
        def requested(self):
            return asked["stop"]

    class StopsWhenAsked(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if kw["should_stop"]():
                    return
                yield frame
                asked["stop"] = True          # Ctrl-C during the first frame

    class Patched(StopsWhenAsked):
        def __init__(self, **kw):
            super().__init__(frames=3)

    monkeypatch.setattr(scan_roll, "DeferredInterrupt", Asked)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "stopped", "--frames", "3"],
    )
    code = scan_roll.main()
    assert len(filed(tmp_path / "lib", "frame")) == 1
    assert code == 130


# --- Ctrl-C before the first frame -------------------------------------------


class _Pressed(FakeRollScanner):
    """Ctrl-C pressed once, during the ``nth`` ``during``: one of the steps
    ``order`` records. Pressed through the handler in force, as the signal
    calls it -- the tool's own `DeferredInterrupt` -- and the step it lands
    in finishes, as a pass does. ``seek`` waits for the lamp too, so a second
    ``wait_warm`` in ``order`` is the seek's.

    ``ensure_shading`` answers as the driver decides, or ``answer`` where a
    test needs a driver that decided otherwise."""

    def __init__(self, during, at=0, answer=None, nth=1):
        super().__init__(frames=2)
        self.at = at
        self.during = during
        self.answer = answer
        self.nth = nth
        self.order = []
        self.pressed = False

    def _doing(self, step):
        import signal

        self.order.append(step)
        if (step == self.during and self.order.count(step) == self.nth
                and not self.pressed):
            self.pressed = True
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

    def wait_warm(self, timeout=300.0, poll=5.0):
        self._doing("wait_warm")

    def advance(self, steps=1, **kw):
        self._doing("advance")
        return super().advance(steps, **kw)

    def retreat(self, steps=1, **kw):
        self._doing("retreat")
        return super().retreat(steps, **kw)

    def nudge(self, millimetres):
        self._doing("nudge")
        return {"asked_mm": millimetres}

    def ensure_shading(self, path, reuse=False, skip=False):
        from pathlib import Path

        self._doing("calibrate")
        action = self.answer or (
            "skipped" if skip
            else "loaded" if reuse and Path(path).exists() else "calibrated")
        return {"action": action, "reference": None, "path": None,
                "summary": f"shading {action}"}

    def scan_roll(self, **kw):
        self._doing("roll")
        yield from super().scan_roll(**kw)


def _pressed(tmp_path, monkeypatch, during, *argv, at=0, answer=None, nth=1):
    """``(exit code, the scanner)`` of a run Ctrl-C was pressed in once."""
    import signal

    # SIGINT as a tool started from a terminal has it, for this test only.
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code, created = _tool(tmp_path, monkeypatch, *argv,
                              make=lambda: _Pressed(during, at=at,
                                                    answer=answer, nth=nth))
    except KeyboardInterrupt:
        pytest.fail("one Ctrl-C was taken as two")
    finally:
        signal.signal(signal.SIGINT, previous)
    return code, created[0]


def _walked(tmp_path):
    """A folder holding a walk, as the default roll name finds one."""
    walked = tmp_path / "roll"
    walked.mkdir()
    # Its prescan filed: a folder a fresh walk may go into (P18-a).
    before = {"roll": "roll", "frames": [{"number": 1, "prescan": "p.tif",
                                          "prescan_entry": "e1"}]}
    (walked / "survey.json").write_text(json.dumps(before), encoding="utf-8")
    return walked, before


def test_a_ctrl_c_during_the_seek_calibrates_nothing_and_keeps_the_walk(
        tmp_path, monkeypatch, capsys):
    """Nothing asked between the seek and the calibration, so a Ctrl-C during
    the seek was told "stopping" and then sat through 3-4 minutes of
    calibration -- and a dry run replaced survey.json with a walk of
    nothing before the roll's first question ended it."""
    walked, before = _walked(tmp_path)
    code, scanner = _pressed(tmp_path, monkeypatch, "advance", "--dry-run",
                             "--frames", "2", "--start-at", "3")
    assert code == 130
    assert scanner.at == 2, "the seek under way finished"
    assert "calibrate" not in scanner.order, scanner.order
    assert "roll" not in scanner.order, scanner.order
    assert json.loads((walked / "survey.json").read_text(
        encoding="utf-8")) == before, "the walk was replaced"
    assert [p.name for p in walked.iterdir()] == ["survey.json"]
    assert "stopped at Ctrl-C before calibrating" in capsys.readouterr().err


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_ctrl_c_during_the_calibration_starts_no_frame(
        tmp_path, monkeypatch, capsys, dry_run):
    """The calibration under way finishes -- it is a read -- and nothing
    after it starts: no frame, and on a dry run no empty survey.json."""
    walked, before = _walked(tmp_path)
    code, scanner = _pressed(tmp_path, monkeypatch, "calibrate",
                             *(["--dry-run"] if dry_run else []),
                             "--frames", "2")
    assert code == 130
    assert scanner.order[-1] == "calibrate", scanner.order
    assert scanner.asked == {}, "a frame was started"
    assert json.loads((walked / "survey.json").read_text(
        encoding="utf-8")) == before
    assert [p.name for p in walked.iterdir()] == ["survey.json"]
    assert "stopped at Ctrl-C after the calibration" in capsys.readouterr().err


@pytest.mark.parametrize("argv, cached, answer, said", [
    ((), False, None, "after the calibration"),
    (("--no-shading",), False, None, "before the first frame"),
    (("--reuse",), True, None, "before the first frame"),
    # A driver that turned the cached reference down and measured one -- a
    # reuse that also checked the gain, say -- where the tool guessed a load.
    (("--reuse",), True, "calibrated", "after the calibration"),
])
def test_a_stop_after_the_reference_says_what_the_driver_did(
        tmp_path, monkeypatch, capsys, argv, cached, answer, said):
    """From `ensure_shading`'s answer, not from another copy of its
    decision: a copy that drifted would say "after the calibration" where
    none ran, or the reverse."""
    if cached:
        (tmp_path / "shading.npz").write_bytes(b"a cached reference")
    code, scanner = _pressed(tmp_path, monkeypatch, "calibrate", *argv,
                             "--frames", "2", answer=answer)
    assert code == 130
    assert scanner.asked == {}, "a frame was started"
    assert f"stopped at Ctrl-C {said}" in capsys.readouterr().err


def test_a_ctrl_c_during_the_rewind_moves_the_film_no_further(
        tmp_path, monkeypatch, capsys):
    """The rewind asked for finishes; the seek after it does not start."""
    code, scanner = _pressed(tmp_path, monkeypatch, "retreat",
                             "--rewind", "2", "--frames", "2", at=5)
    assert code == 130
    assert scanner.moves == [("retreat", 1)] * 2, scanner.moves
    assert scanner.at == 3
    assert "calibrate" not in scanner.order, scanner.order
    assert ("stopped at Ctrl-C before placing the film on frame 1"
            in capsys.readouterr().err)


def test_a_ctrl_c_while_the_lamp_warms_moves_nothing(tmp_path, monkeypatch,
                                                     capsys):
    """And the promise names the warm-up: it is waited out in full, about
    80 s from cold, and a wait the promise left out brings the second
    press."""
    code, scanner = _pressed(tmp_path, monkeypatch, "wait_warm",
                             "--frames", "2", at=2)
    assert code == 130
    assert scanner.moves is None, "the film moved after the Ctrl-C"
    assert scanner.order == ["wait_warm"], scanner.order
    err = capsys.readouterr().err
    assert "after the lamp's warm-up, move or calibration under way" in err
    assert "stopped at Ctrl-C" in err


@pytest.mark.parametrize("during, argv, at, said", [
    ("retreat", ("--rewind", "2"), 5, "after the rewind"),
    ("wait_warm", (), 2, "after the lamp's warm-up"),
])
def test_a_ctrl_c_with_nothing_after_it_still_exits_130(
        tmp_path, monkeypatch, capsys, during, argv, at, said):
    """`--frames 0` ends once its moves are done, and only the seek it skips
    asked after the rewind: a Ctrl-C during one exited 0, "nothing else
    asked for", where the tool's own rule is that a run stopped by one is
    not a finished one."""
    code, scanner = _pressed(tmp_path, monkeypatch, during, *argv,
                             "--frames", "0", at=at)
    assert code == 130
    if argv:
        assert scanner.moves == [("retreat", 1)] * 2, "the rewind finished"
    assert "calibrate" not in scanner.order, scanner.order
    out, err = capsys.readouterr()
    assert f"stopped at Ctrl-C {said}" in err
    assert "nothing else asked for" not in out


@pytest.mark.parametrize("nth, said", [
    (2, "after 2 of the 3 offset commands"),
    (3, "after the offset"),
])
def test_an_offset_alone_exits_130_whichever_command_the_press_lands_in(
        tmp_path, monkeypatch, capsys, nth, said):
    """`--nudge 200 --frames 0` is three commands. A press in the second
    stopped before the third with 130; one in the last let the loop end, and
    the run said "nothing else asked for" and exited 0."""
    monkeypatch.setattr(scan_roll.time, "sleep", lambda s: None)
    code, scanner = _pressed(tmp_path, monkeypatch, "nudge",
                             "--nudge", "200", "--frames", "0", nth=nth)
    assert code == 130
    assert scanner.order.count("nudge") == nth, scanner.order
    assert f"stopped at Ctrl-C {said}" in capsys.readouterr().err


def test_a_ctrl_c_during_an_offset_sends_no_further_command(
        tmp_path, monkeypatch, capsys):
    """Asked between the commands of one offset, as between frames, and what
    was sent is said -- in units, never millimetres."""
    monkeypatch.setattr(scan_roll.time, "sleep", lambda s: None)
    code, scanner = _pressed(tmp_path, monkeypatch, "nudge",
                             "--nudge", "200", "--frames", "1")
    assert code == 130
    assert scanner.order.count("nudge") == 1, scanner.order
    assert "calibrate" not in scanner.order, scanner.order
    err = capsys.readouterr().err
    assert "after 1 of the 3 offset commands" in err and "units" in err
    assert " mm" not in err


@pytest.mark.parametrize("dry_run, passes", [
    (True, "its prescans --"),
    (False, "its prescans, metering and the scan --"),
])
def test_a_ctrl_c_promises_the_frame_in_flight_not_the_pass(
        tmp_path, monkeypatch, capsys, dry_run, passes):
    """The driver asks `should_stop` only between frames, and the shared
    wording promised "after the pass in flight": an operator told one pass
    and kept waiting through a prescan, metering and a scan is the one who
    presses again, and the second press abandons the read."""
    _pressed(tmp_path, monkeypatch, "calibrate",
             *(["--dry-run"] if dry_run else []), "--frames", "2")
    err = capsys.readouterr().err
    assert "stopping after the frame in flight -- " + passes in err, err
    assert "pass in flight" not in err


def test_the_prescan_before_an_aim_is_never_filed_with_the_later_ones_meta(
        tmp_path, monkeypatch):
    """Another pass, which can have read the other way: filed with the
    replacing prescan's meta, its entry described that pass instead."""

    class NoBeforeMeta(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                frame.prescan_before = frame.prescan
                frame.raw_prescan_before = frame.raw_prescan
                frame.prescan_before_meta = {}
                yield frame

    class Patched(NoBeforeMeta):
        def __init__(self, **kw):
            super().__init__(frames=1)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "unaimed", "--frames", "1", "--dry-run"])
    assert scan_roll.main() == 0
    tags = [json.loads(p.read_text(encoding="utf-8"))["tags"]
            for p in filed(tmp_path / "lib", "prescan")]
    assert len(tags) == 1 and "before" not in tags[0], tags
    assert (tmp_path / "roll" / "prescan01-before.tif").exists()


def _free(monkeypatch, free):
    """Every disk reports ``free()`` bytes free."""
    import collections
    import shutil

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: usage(10 ** 13, 0, free()))


def test_a_roll_the_disk_cannot_hold_is_refused_before_the_device_opens(
        tmp_path, monkeypatch):
    """Nothing asked how much room there was: a full disk showed itself as a
    failed filing hours in, after the scanner time was spent."""
    from rps7200 import session

    one = session.frame_bytes(1800, False)
    _free(monkeypatch, lambda: 2 * one)          # three frames need nine
    opened = []
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, monkeypatch, "--frames", "3", opened=opened)
    assert refused.value.code == 2
    assert opened == [], "the device was opened for a roll with no room"


def test_a_roll_stops_before_a_frame_there_is_no_room_for(tmp_path,
                                                          monkeypatch):
    from rps7200 import session

    one = session.frame_bytes(1800, False)
    room = {"free": 100 * one}
    _free(monkeypatch, lambda: room["free"])

    class Fills(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                yield frame
                room["free"] = one             # not three copies' worth
                if kw["should_stop"]():
                    return

    class Patched(Fills):
        def __init__(self, **kw):
            super().__init__(frames=3)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "filling", "--frames", "3"])
    code = scan_roll.main()
    assert code == 1
    assert len(filed(tmp_path / "lib", "frame")) == 1
    manifest = json.loads((tmp_path / "roll" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert "no room for the next frame" in manifest["stopped"]


# --- a copy not written is not a pass not filed ------------------------------


def _refusing_copies(monkeypatch, name):
    """`export.write` refusing one file by name, as a full or pulled output
    disk or a viewer holding it open would."""
    from pathlib import Path

    from rps7200 import session

    real = session.export.write

    def refuses(path, image, **kw):
        if Path(path).name == name:
            raise OSError(28, "No space left on device")
        return real(path, image, **kw)

    monkeypatch.setattr(session.export, "write", refuses)


def test_a_frame_whose_copy_was_not_written_is_not_called_a_failed_scan(
        tmp_path, monkeypatch, capsys):
    """Its pass is in the library. It was counted as a frame that could not
    be filed -- "1 failed", "could not file ..." -- and the advice was to
    scan it again with --start-at and --only."""
    _refusing_copies(monkeypatch, "frame02.tif")
    _scanner, code = run(tmp_path, monkeypatch, "--frames", "3")
    out, err = capsys.readouterr()
    assert code == 1, "a copy not written is still a loss to the caller"
    assert "3 scanned, 0 failed, 1 filed without its copy" in out
    assert err.count("frame02.tif") >= 1
    assert "could not file" not in err
    assert "--start-at" not in err and "--only" not in err
    (line,) = [x for x in err.splitlines()
               if x.startswith("picture 2 is filed in the library as ")]
    assert "Save As or Export writes it again" in line
    manifest = json.loads((tmp_path / "roll" / "roll.json").read_text(
        encoding="utf-8"))
    frame = {f["number"]: f for f in manifest["frames"]}[2]
    assert frame["done"] is True and frame["entry"]
    assert frame["file"] is None
    assert "No space left" in frame["file_error"]
    assert "stopped" not in manifest, "the roll stopped on a copy"


@pytest.mark.parametrize("refused", ["copy", "library"])
def test_a_walk_that_lost_a_frame_is_never_told_to_rescan_it(
        tmp_path, monkeypatch, capsys, refused):
    """"resume a failed picture with --out X --start-at N --only N",
    followed as printed after a walk, ran a full-resolution scan into the
    walk's folder; and with --dry-run added it would replace a walk of 36
    frames with one, since a walk here is never carried forward."""
    if refused == "copy":
        _refusing_copies(monkeypatch, "prescan02.tif")
        _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames",
                             "3")
    else:
        _created, code = _refusing_library(tmp_path, monkeypatch, _Patient,
                                           "--dry-run", frames=3)
    out, err = capsys.readouterr()
    assert code == 1
    assert "--start-at" not in err and "--only" not in err
    if refused == "copy":
        (line,) = [x for x in err.splitlines()
                   if x.startswith("frame 2's prescan is filed in the library")]
        assert "the contact sheet will not show it" in line
        assert "keeping the sheet" in line
        assert "0 failed, 1 filed without its copy" in out
    else:
        assert "walk the strip again with the same flags" in err
        assert "walk the missing frames again, keeping the sheet" in err


def test_a_picture_from_before_an_aim_not_written_takes_no_frame_off_the_sheet(
        tmp_path, monkeypatch, capsys):
    """The sheet reads prescanNN.tif, never the picture from before an aim.
    Filed with no part, prescanNN-before.tif took the walk prescan's words:
    "the contact sheet will not show it -- walk that frame again", for a
    frame the sheet showed, and a walk spent on it."""

    class Aimed(FakeRollScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                frame.prescan_before = frame.prescan
                frame.raw_prescan_before = frame.raw_prescan
                frame.prescan_before_meta = dict(frame.prescan_meta or {})
                frame.prescan_before_capture = frame.prescan_capture
                yield frame

    class Patched(Aimed):
        def __init__(self, **kw):
            super().__init__(frames=3)

    _refusing_copies(monkeypatch, "prescan02-before.tif")
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "aimed", "--frames", "3", "--dry-run"])
    code = scan_roll.main()
    out, err = capsys.readouterr()
    assert code == 1, "a copy not written is still a loss"
    assert "contact sheet" not in err, err
    assert "walk that frame again" not in err and "walk it again" not in err
    (line,) = [x for x in err.splitlines()
               if x.startswith("frame 2's prescan from before its aim")]
    assert "prescan02-before.tif was not written" in line
    assert line.endswith("the entry holds it whole")
    (live,) = [x for x in err.splitlines()
               if x.startswith("picture 2: could not write")]
    assert live.endswith("is safe")
    # The sheet is whole: every frame's own prescan is named.
    survey = json.loads((tmp_path / "roll" / "survey.json")
                        .read_text(encoding="utf-8"))
    assert [f.get("prescan") for f in survey["frames"]] == [
        f"prescan{n:02d}.tif" for n in (1, 2, 3)]
    assert "prescan_before" not in survey["frames"][1]
    assert "1 filed without its copy" in out


# -- the folders, the paths and the disks, before the device opens ------------


@pytest.mark.parametrize("walk", [False, True])
@pytest.mark.parametrize("what", ["out", "reference", "full", "long"])
def test_a_run_that_cannot_be_kept_is_refused_before_the_device_opens(
        tmp_path, monkeypatch, capsys, walk, what):
    """A roll folder or a reference folder on a drive that has gone, a full
    disk, or a path too long for Windows: each was found after the seek and
    the calibration -- a walk's too, which was checked for none of them."""
    from types import SimpleNamespace as Namespace

    from rps7200 import storage

    gone = tmp_path / "gone"
    argv = ["--out", str(tmp_path / "roll"), "--library",
            str(tmp_path / "lib"), "--roll", "teststrip", "--frames", "2",
            "--film-loaded"]
    if what == "out":
        argv[1] = str(gone / "roll")
    elif what == "reference":
        argv += ["--reference", str(gone / "calibration" / "shading.npz")]
    elif what == "full":
        _free(monkeypatch, lambda: 1000)
    else:
        monkeypatch.setattr(storage, "sys", Namespace(platform="win32"))
        argv[1] = str(tmp_path / ("r" * 230))
    created = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", *argv,
                                      *(["--dry-run"] if walk else [])])
    with pytest.raises(SystemExit) as refused:
        scan_roll.main()
    assert refused.value.code == 2
    assert created == [], "the device was opened"
    err = capsys.readouterr().err
    assert f"not starting the {'walk' if walk else 'roll'}" in err
    assert {"out": "is the drive mounted?", "reference": "is the drive mounted?",
            "full": "GB free", "long": "past the 259"}[what] in err
    assert not gone.exists()


def test_a_long_roll_name_is_cut_and_kept_as_typed(tmp_path, monkeypatch,
                                                   capsys):
    """Its folder is the name cut to 56 characters and a hash, said, and
    roll.json keeps the name as it was typed -- through a resume by --out
    too, which names no roll and wrote the manifest afresh without it."""
    from rps7200 import session

    typed = "A very long roll name typed into the command line, twice " * 2
    monkeypatch.chdir(tmp_path)
    created = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--library", str(tmp_path / "lib"), "--no-shading",
        "--roll", typed, "--frames", "1"])
    assert scan_roll.main() == 0
    folder = tmp_path / "rolls" / session._safe(typed)
    assert len(folder.name) == session.NAME_CAP
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert manifest["typed_name"] == typed.strip()
    assert f"its folder and files are called {folder.name}" in \
        capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--library", str(tmp_path / "lib"), "--no-shading",
        "--out", str(folder), "--frames", "1"])
    assert scan_roll.main() == 0
    resumed = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert resumed["roll"] == folder.name
    assert resumed["typed_name"] == typed.strip()
    # And by `--roll <its folder>`, which is what this tool's advice prints:
    # typed, but nothing beside the folder's name, so not the name typed --
    # twice, so the .bak is not what holds it.
    for _ in range(2):
        monkeypatch.setattr(sys, "argv", [
            "scan_roll.py", "--library", str(tmp_path / "lib"),
            "--no-shading", "--roll", folder.name, "--frames", "1"])
        assert scan_roll.main() == 0
        resumed = json.loads((folder / "roll.json").read_text(
            encoding="utf-8"))
        assert resumed["typed_name"] == typed.strip()


def test_a_duplicated_roll_over_the_cap_is_found_by_its_own_name(
        tmp_path, monkeypatch, capsys):
    """A Duplicate's `<name>-2` of a folder at the cap is past it, and was
    looked up as that name cleaned and capped: `--roll <the copy>` started a
    third folder, with another hash, and called the name cut. Found as it
    is now, and its record keeps the name the roll was typed as."""
    import shutil

    from rps7200 import session

    typed = "A very long roll name typed into the command line, twice " * 2
    monkeypatch.chdir(tmp_path)

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--library", str(tmp_path / "lib"), "--no-shading",
        "--roll", typed, "--frames", "1"])
    assert scan_roll.main() == 0
    folder = tmp_path / "rolls" / session._safe(typed)
    copy = folder.with_name(f"{folder.name}-2")
    shutil.copytree(folder, copy)
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--library", str(tmp_path / "lib"), "--no-shading",
        "--roll", copy.name, "--frames", "1"])
    assert scan_roll.main() == 0
    assert "its folder and files are called" not in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "rolls").iterdir()
                  if p.is_dir()) == sorted([folder.name, copy.name])
    resumed = json.loads((copy / "roll.json").read_text(encoding="utf-8"))
    assert resumed["roll"] == folder.name
    assert resumed["typed_name"] == typed.strip()


# -- other programs on the same folders ---------------------------------------


@pytest.mark.parametrize("what", ["roll", "library"])
def test_a_folder_another_program_holds_refuses_before_the_scanner_opens(
        tmp_path, monkeypatch, capsys, what):
    """The window scanning into the same roll folder, renaming or deleting
    it, or `compact --write` rewriting the library: refused, naming who,
    before any scanner is made -- nothing moved, nothing written."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    held = (HeldElsewhere(locks.ROLL, tmp_path / "roll", what="the window")
            if what == "roll" else
            HeldElsewhere(locks.LIBRARY, tmp_path / "lib", exclusive=True,
                          what="tools/library.py compact --write"))
    opened: list = []
    with held as other:
        with pytest.raises(SystemExit) as refused:
            run(tmp_path, monkeypatch, opened=opened)
    assert refused.value.code == 2
    said = capsys.readouterr().err
    assert f"pid {other.pid}" in said
    assert ("is in use by the window (" in said
            and "nothing was moved or scanned" in said if what == "roll"
            else "is rewriting the library" in said
            and "Nothing was sent." in said)
    assert opened == [], "a scanner was made"
    assert not (tmp_path / "roll").exists()


def test_the_rolls_folder_is_held_until_the_tool_has_filed(tmp_path,
                                                           monkeypatch):
    import os

    from rps7200 import locks

    seen = []
    real = FakeRollScanner.scan_roll

    def watching(self, **kw):
        seen.append([h["pid"] for h in locks.holders(locks.ROLL,
                                                     tmp_path / "roll")])
        yield from real(self, **kw)

    monkeypatch.setattr(FakeRollScanner, "scan_roll", watching)
    _scanner, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert seen == [[os.getpid()]]
    assert locks.holders(locks.ROLL, tmp_path / "roll") == []


def test_approved_holds_its_walk_only_while_it_reads_it(tmp_path,
                                                        monkeypatch, capsys):
    """Every prescan is read before the scanner opens, so the walk is held
    as shown for that long and no longer: a Rename of it in the window need
    not wait for the hours the roll runs -- and one in progress refuses."""
    import json
    import os

    from conftest import HeldElsewhere

    from rps7200 import locks, tiff

    folder = tmp_path / "walk"
    folder.mkdir()
    for n in (1, 2):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 40 + n, np.uint8))
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": n, "transport_position": n - 1,
         "prescan": f"prescan{n:02d}.tif"} for n in (1, 2)]}),
        encoding="utf-8")
    real = scan_roll.hold_from_walk
    seen = []

    def watched(*a, **kw):
        seen.append([h["pid"] for h in locks.holders(locks.ROLL_VIEW,
                                                     folder)])
        return real(*a, **kw)

    monkeypatch.setattr(scan_roll, "hold_from_walk", watched)
    opened: list = []
    _scanner, code = run(tmp_path, monkeypatch, "--approved", str(folder),
                         "--frames", "2", opened=opened)
    assert code == 0
    assert seen == [[os.getpid()]]
    assert locks.holders(locks.ROLL_VIEW, folder) == []

    with HeldElsewhere(locks.ROLL_VIEW, folder, exclusive=True,
                       what="the window"):
        with pytest.raises(SystemExit):
            run(tmp_path, monkeypatch, "--approved", str(folder),
                "--frames", "2")
    assert "is renaming or deleting it" in capsys.readouterr().err


def test_one_damaged_walk_prescan_costs_its_own_hold_at_most(
        tmp_path, monkeypatch, capsys):
    """Every file was read in one comprehension, and one cut short by a
    power cut stopped `--approved` with a traceback before the scanner was
    even opened. Each is read on its own now (`session.read_walk_prescan`):
    one the walk's own entry can stand in for is held to that, said; one it
    cannot is left out, said, and that frame is held to nothing."""
    import numpy as np

    from conftest import walk_with_entries

    monkeypatch.setattr(
        scan_roll.frame_edges, "propose_centred",
        lambda frames, film=None: ({n: 0.0 for n, _ in frames},
                                   {n: {"source": "measured"}
                                    for n, _ in frames}))
    folder, lib, manifest, pictures = walk_with_entries(
        tmp_path, numbers=(1, 2, 3), tags={3: ["before"]})
    for n in (2, 3):
        whole = (folder / f"prescan0{n}.tif").read_bytes()
        (folder / f"prescan0{n}.tif").write_bytes(whole[:len(whole) // 2])
    held, note = scan_roll.hold_from_walk(folder, library_root=lib)
    said = capsys.readouterr().out
    assert sorted(held) == [1, 2]
    assert np.array_equal(held[2].reference, pictures[2])
    assert held[2].reference_entry.endswith(
        manifest["frames"][1]["prescan_entry"])
    assert "frame 2: " in said and "shown from its library entry" in said
    assert "frame 3: " in said and "that frame is held to nothing" in said
    assert note["walked"] == 2


def test_a_walk_that_takes_no_frame_leaves_the_walk_before_it(
        tmp_path, monkeypatch, capsys):
    """FOUND-2: the manifest was written before the first frame, so a walk
    that took none replaced survey.json with an empty walk -- and the next
    attempt copied that over survey.json.bak, the one list left of the old
    walk's prescans' entries."""
    from rps7200 import session

    folder = tmp_path / "roll"
    folder.mkdir()
    for frames in (1, 2):
        session.write_manifest(folder / "survey.json", {
            "roll": "teststrip", "numbering": "strip", "walk_id": "old",
            "frames": [{"number": n, "prescan": f"prescan{n:02d}.tif",
                        "prescan_entry": f"e{n}"} for n in range(1, frames + 1)]},
            keep_previous=True)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    for _attempt in range(2):
        _scanner, code = run(tmp_path, monkeypatch, "--dry-run", frames=0)
        assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
        assert "is left as it was: nothing was walked" in capsys.readouterr().err
    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", frames=1)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in survey["frames"]] == [1]
    assert len(survey["walk_id"]) == 32 and survey["walk_id"] != "old"
    assert (folder / "survey.json.bak").read_bytes() == before["survey.json"]


# -- a roll of strips, and a walk of its own (--strip, P18-a) -----------------
#
# Stefan's call, 2026-10-10: one roll folder per film, a folder per strip,
# frames numbered across the film. `--strip next|N` walks or scans a strip of
# one; without it the tool is what it was, and refuses a roll of strips.


def _strip_run(tmp_path, monkeypatch, *argv, frames=3, opened=None,
               cls=FakeRollScanner):
    """``(the scanner the tool opened, its exit code)`` for a run in
    ``tmp_path``, whose rolls folder is ``tmp_path/rolls``."""
    monkeypatch.chdir(tmp_path)
    created = [] if opened is None else opened

    class Patched(cls):
        def __init__(self, **kw):
            super().__init__(frames=frames)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--library", str(tmp_path / "lib"), "--no-shading",
        *argv])
    code = scan_roll.main()
    return (created[0] if created else None), code


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _labels(lib, kind, strip):
    out = {}
    for path in sorted(lib.glob("*/scan.json")):
        record = _json(path)
        member = (record.get("extra") or {}).get("roll_membership") or {}
        if member.get("kind") == kind and member.get("strip") == strip:
            out[member["number"]] = (record["film"]["frame"], member)
    return out


def test_strip_next_makes_a_roll_of_strips_numbered_across_it(
        tmp_path, monkeypatch, capsys):
    roll = tmp_path / "rolls" / "Gold200"
    _scanner, code = _strip_run(tmp_path, monkeypatch, "--roll", "Gold200",
                                "--strip", "next", "--dry-run", frames=6)
    assert code == 0
    assert "strip 1 of Gold200: frames numbered from 1" in capsys.readouterr().out
    survey = _json(roll / "strip-01" / "survey.json")
    assert survey["strip"] == {"number": 1, "first": 1}
    assert survey["strip_end"] == 7
    assert [(f["number"], f["roll_number"]) for f in survey["frames"]] == [
        (n, n) for n in range(1, 7)]
    _scanner, code = _strip_run(tmp_path, monkeypatch, "--roll", "Gold200",
                                "--strip", "next", "--dry-run", frames=6)
    assert code == 0
    said = capsys.readouterr().out
    assert ("strip 2 of Gold200: frames numbered from 7 (strip 1 has 6) "
            "-- strip 1 is closed at 1-6 first") in said
    index = _json(roll / "strips.json")
    assert [(s["strip"], s["first"], s["last"]) for s in index["strips"]] == [
        (1, 1, 6), (2, 7, None)]
    survey = _json(roll / "strip-02" / "survey.json")
    assert [(f["number"], f["roll_number"]) for f in survey["frames"]] == [
        (n, n + 6) for n in range(1, 7)]
    prescans = _labels(tmp_path / "lib", "prescan", 2)
    assert [prescans[n][0] for n in range(1, 7)] == [
        f"Gold200-{n:02d}" for n in range(7, 13)]
    assert prescans[2][1] == {"roll": "Gold200", "number": 2,
                              "kind": "prescan",
                              "folder": str(Path("rolls") / "Gold200"
                                            / "strip-02"),
                              "strip": 2, "roll_number": 8}

    # A roll scanned into strip 2: by place inside it, by the roll outside.
    _scanner, code = _strip_run(tmp_path, monkeypatch, "--roll", "Gold200",
                                "--strip", "2", "--start-at", "2",
                                "--frames", "3")
    assert code == 0
    frames = _labels(tmp_path / "lib", "frame", 2)
    assert {n: frames[n][0] for n in frames} == {
        2: "Gold200-08", 3: "Gold200-09", 4: "Gold200-10"}
    progress = _json(roll / "strip-02" / "roll.json")
    assert progress["strip"] == {"number": 2, "first": 7}
    assert [(f["number"], f["roll_number"]) for f in progress["frames"]] == [
        (2, 8), (3, 9), (4, 10)]
    assert sorted(p.name for p in (roll / "strip-02").glob("frame*.tif")) == [
        "frame02.tif", "frame03.tif", "frame04.tif"]
    # Across the whole roll: strip 1 is not scanned at other settings.
    with pytest.raises(SystemExit):
        _strip_run(tmp_path, monkeypatch, "--roll", "Gold200", "--strip", "1",
                   "--dpi", "600", "--frames", "1")
    assert "strip-02" in capsys.readouterr().err


def test_a_strip_walked_again_is_added_to_and_its_advice_names_it(
        tmp_path, monkeypatch, capsys):
    roll = tmp_path / "rolls" / "Gold200"
    _strip_run(tmp_path, monkeypatch, "--roll", "Gold200", "--strip", "next",
               "--dry-run", "--frames", "2", frames=2)
    first = _json(roll / "strip-01" / "survey.json")
    capsys.readouterr()

    class Short(FakeRollScanner):
        """A walk that ends two frames in, of the four asked for."""

        def scan_roll(self, **kw):
            yield from list(super().scan_roll(**kw))[:2]

    _scanner, code = _strip_run(tmp_path, monkeypatch, "--roll", "Gold200",
                                "--strip", "1", "--dry-run", "--start-at", "3",
                                "--frames", "4", cls=Short)
    survey = _json(roll / "strip-01" / "survey.json")
    assert [f["number"] for f in survey["frames"]] == [1, 2, 3, 4]
    assert survey["walk_id"] == first["walk_id"], "the same walk"
    err = capsys.readouterr().err
    assert "--roll Gold200 --strip 1 --start-at N" in err, err


def _two_strips(tmp_path, stopped=False):
    from rps7200 import session

    roll = tmp_path / "rolls" / "Gold200"
    roll.mkdir(parents=True)
    listed = []
    for strip, first in ((1, 1), (2, 7)):
        folder = roll / f"strip-0{strip}"
        folder.mkdir()
        frames = range(1, 5) if stopped and strip == 2 else range(1, 7)
        session.write_manifest(folder / "survey.json", {
            "roll": "Gold200", "strip": {"number": strip, "first": first},
            "frames": [{"number": n} for n in frames],
            **({"stopped": "stopped after frame 4, as asked"}
               if stopped and strip == 2 else {"strip_end": 7})})
        listed.append({"strip": strip, "folder": folder.name, "first": first,
                       "last": 6 if strip == 1 else None})
    session.write_manifest(roll / "strips.json", {
        "kind": session.STRIPS_KIND, "version": 1, "roll": "Gold200",
        "strips": listed, "extra": []})
    return roll


@pytest.mark.parametrize("argv, said", [
    (("--roll", "Gold200"),
     "Gold200 is a roll of 2 strips (1: 1-6, 2: 7-12); give --strip N for "
     "one of them, or --strip next (3, from 13)"),
    (("--roll", "Gold200", "--strip", "5"),
     "strips are taken in order; next is 3"),
    (("--out", str(Path("rolls") / "Gold200" / "strip-02"), "--strip", "3"),
     "--out names strip 2 of Gold200, and --strip says 3"),
    (("--roll", "old", "--strip", "next"),
     "one strip from before rolls held strips"),
])
def test_what_strip_refuses_it_refuses_before_the_scanner_opens(
        tmp_path, monkeypatch, capsys, argv, said):
    _two_strips(tmp_path)
    (tmp_path / "rolls" / "old").mkdir()
    (tmp_path / "rolls" / "old" / "survey.json").write_text(
        "{}", encoding="utf-8")
    opened: list = []
    with pytest.raises(SystemExit):
        _strip_run(tmp_path, monkeypatch, *argv, "--dry-run", opened=opened)
    assert opened == [], "the scanner was opened"
    assert said in capsys.readouterr().err


def test_strip_next_after_a_stopped_walk_names_both_ways_on(
        tmp_path, monkeypatch, capsys):
    _two_strips(tmp_path, stopped=True)
    opened: list = []
    with pytest.raises(SystemExit):
        _strip_run(tmp_path, monkeypatch, "--roll", "Gold200", "--strip",
                   "next", "--dry-run", opened=opened)
    assert opened == []
    err = capsys.readouterr().err
    assert ("--strip 2 --start-at 5 adds to it; --strip 3 starts the next, "
            "and strip 2 then counts 4 frames") in err, err


@pytest.mark.parametrize("how", ["--roll", "--out"])
def test_a_walk_into_a_used_folder_is_refused_naming_the_free_one(
        tmp_path, monkeypatch, capsys, how):
    """P18-a: a walk wrote its survey.json over another strip's and left
    its roll.json, approved.json and the only copies of an older walk's
    prescans. Refused before the scanner opens, naming the free folder as
    the run was given."""
    used = tmp_path / "rolls" / "used"
    used.mkdir(parents=True)
    (used / "roll.json").write_text(json.dumps({"frames": [{"number": 1}]}),
                                    encoding="utf-8")
    before = (used / "roll.json").read_bytes()
    given = "used" if how == "--roll" else str(Path("rolls") / "used")
    opened: list = []
    with pytest.raises(SystemExit):
        _strip_run(tmp_path, monkeypatch, how, given, "--dry-run",
                   opened=opened)
    assert opened == []
    err = capsys.readouterr().err
    assert "1 scanned frame (roll.json)" in err
    assert ("--roll used-2 is free" if how == "--roll"
            else f"--out {Path('rolls') / 'used-2'} is free") in err, err
    assert (used / "roll.json").read_bytes() == before
    # --frames 0 walks nothing, so nothing is refused.
    _scanner, code = _strip_run(tmp_path, monkeypatch, how, given,
                                "--dry-run", "--frames", "0")
    assert code == 0


def test_a_walk_only_folder_is_walked_again_in_place(tmp_path, monkeypatch):
    folder = tmp_path / "rolls" / "walked"
    folder.mkdir(parents=True)
    old = json.dumps({"roll": "walked", "frames": [
        {"number": 1, "prescan": "prescan01.tif", "prescan_entry": "e1"}]})
    (folder / "survey.json").write_text(old, encoding="utf-8")
    _scanner, code = _strip_run(tmp_path, monkeypatch, "--roll", "walked",
                                "--dry-run", "--frames", "1", frames=1)
    assert code == 0
    assert (folder / "survey.json.bak").read_text(encoding="utf-8") == old
    assert not (tmp_path / "rolls" / "walked-2").exists()
