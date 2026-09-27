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
            )


def run(tmp_path, monkeypatch, *argv, frames=3, opened=None):
    """``(the scanner the tool opened, its exit code)``. ``opened`` collects
    every scanner made, for a test whose run is refused: the pair is never
    returned then, so it is the only way to see whether one was opened."""
    created = [] if opened is None else opened

    class Patched(FakeRollScanner):
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
    entries = sorted((tmp_path / "lib").glob("*/scan.json"))
    assert len(entries) == 2
    for record in entries:
        image, stored = library.load(record.parent)
        assert int(image.max()) == RAW_LEVEL, "the corrected image was filed"
        assert stored["image"]["corrections_applied"] == []


# -- the dry run, which writes prescans instead -----------------------------


def test_a_dry_run_writes_prescans_and_no_frames(tmp_path, monkeypatch):
    _scanner, code = run(tmp_path, monkeypatch, "--dry-run", "--frames", "2")
    assert code == 0
    assert sorted(p.name for p in (tmp_path / "roll").glob("prescan*.tif")) \
        == ["prescan01.tif", "prescan02.tif"]
    assert not list((tmp_path / "roll").glob("frame*.tif"))
    assert (tmp_path / "roll" / "survey.json").exists()


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
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 2
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
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 2
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
         *argv])
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
    before = {"roll": "roll", "frames": [{"number": 1, "prescan": "p.tif"}]}
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
        {"number": 1, "transport_position": 5, "prescan": "prescan01.tif"}]}
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
    record = json.loads(next((tmp_path / "lib").glob("*/scan.json"))
                        .read_text(encoding="utf-8"))
    assert record["film"]["frame"] == "teststrip-01"
    assert record["extra"]["roll_membership"]["kind"] == "frame"


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
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    created = []

    class Patched(scanner_class):
        def __init__(self, **kw):
            super().__init__(frames=frames)
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(blocker / "lib"), "--no-shading",
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
        self.order = []

    def debug_claim(self, pixels):
        self.claimed.append(pixels)

    def __exit__(self, *exc):
        self.order.append("scanner exited")


def test_a_frame_is_claimed_from_debug_filing_only_once_it_is_filed(
        tmp_path, monkeypatch):
    """Claimed as it was queued, a frame whose filing failed was deleted from
    the debug spool at close as well -- the one copy RPS7200_DEBUG=1 keeps."""
    created, code = _refusing_library(tmp_path, monkeypatch, _Claiming,
                                      frames=2)
    assert code != 0
    assert created[0].claimed == []


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
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 2
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
    (entry,) = [p.parent for p in (tmp_path / "lib").glob("*/scan.json")]
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
        def __exit__(self, *exc):
            seen.append(os.environ.get("RPS7200_DEBUG_ROOT"))

    monkeypatch.setattr(scan_roll, "DirectScanner",
                        lambda **kw: Exiting(frames=1))
    monkeypatch.setattr(sys, "argv", [
        "scan_roll.py", "--out", str(tmp_path / "roll"),
        "--library", str(tmp_path / "lib"), "--no-shading",
        "--roll", "own", "--frames", "1"])
    assert scan_roll.main() == 0
    assert seen == [str(tmp_path / "lib")]
    assert "RPS7200_DEBUG_ROOT" not in os.environ
