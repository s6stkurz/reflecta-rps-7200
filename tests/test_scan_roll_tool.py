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


def run(tmp_path, monkeypatch, *argv, frames=3):
    created = []

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


def test_a_roll_from_cold_calibrates_before_it_asks_where_the_film_is(
        tmp_path, monkeypatch):
    """8a9ba17 calibrated first, and the calibration waited the lamp out. The
    seek then put in front of it asked the counter while the lamp warmed,
    heard nothing -- the scanner is NOT READY to everything for ~80 s -- and
    the tool exited 1 telling the operator to check the strip."""
    from rps7200 import session

    order = []
    created = []

    class Cold(FakeRollScanner):
        warm = False

        def position(self):
            order.append("position")
            return self.at if self.warm else None

        def ensure_shading(self, path, reuse=False, skip=False):
            # `calibrate_shading` waits for the lamp before it measures.
            order.append("calibrate")
            self.warm = True
            return super().ensure_shading(path, reuse, skip)

    class Patched(Cold):
        def __init__(self, **kw):
            super().__init__(frames=2)
            created.append(self)

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0)
    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"), "--library", "",
         "--reference", str(tmp_path / "shading.npz"), "--roll", "cold",
         "--dry-run", "--frames", "2"])
    assert scan_roll.main() == 0
    assert order.index("calibrate") < order.index("position"), order
    assert created[0].asked["first_index"] == 0


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
        scan_roll.framing, "propose_offsets",
        lambda frames: ({n: 0.0 for n, _ in frames},
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
        scan_roll.framing, "propose_offsets",
        lambda frames: ({n: 0.0 for n, _ in frames},
                        {n: {"source": "measured"} for n, _ in frames}))
    held, note = scan_roll.hold_from_walk(folder)
    assert sorted(held) == [6, 7]
    assert note["walked"] == 2
    assert np.array_equal(held[6].reference,
                          tiff.read(str(folder / "prescan01.tif")))

    gui = load_tool("gui")
    shown = [r.number for r in gui.read_survey(folder)["results"]]
    assert sorted(held) == shown, "the tool and the window disagree"


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
