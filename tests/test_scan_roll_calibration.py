"""When the roll tool calibrates, and why a dry run must too.

A dry run prescans, and a prescan wants a shading reference. Skipping the
explicit `ensure_shading` did not avoid the calibration -- it moved it inside
`prescan()`, where the driver runs it lazily on the first frame. Measured
twice on real hardware, that lazy calibration stalls: `bulk read of 16384
bytes failed after 0 bytes: LIBUSB_ERROR_PIPE`, immediately after the shading
descriptor, and the device then stops answering INQUIRY.

`tools/scan.py` and the window's Calibrate job both make the same call up
front and both work, so the call is not the problem -- where it is made from
is. These pin that the roll tool makes it on every path, and that `--reuse`
and `--no-shading` reach it, which they did not before: the flags are read in
`calibrate()` and nowhere else, so the lazy path ignored them.
"""
import sys
from types import SimpleNamespace

import numpy as np

from conftest import FilmOnFrame, load_tool
from rps7200.direct import DirectScanner, RollFrame

scan_roll = load_tool("scan_roll")


class RecordingScanner(FilmOnFrame, DirectScanner):
    """Notes whether it was asked to calibrate, and with what.

    On frame 1 of a strip, because the tool asks where the film is before a
    roll, and a double that cannot say is one every roll refuses.
    """

    def __init__(self, **kw):
        self.verbose = False
        self._shading = None
        self._ccd_mask = b"\x00" * 16
        self.last_raw = b"raw"
        self.last_raw_layout = {"format": "index"}
        self.shading_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def inquiry(self, refresh=False):
        return SimpleNamespace(vendor="Reflecta", product="RPS 7200",
                               model=0x31, firmware="1.0")

    def ensure_shading(self, path, reuse=False, skip=False):
        self.shading_calls.append({"path": str(path), "reuse": reuse,
                                   "skip": skip})
        return {"action": "reused" if reuse else "measured",
                "reference": None, "path": str(path),
                "summary": "shading ready"}

    def scan_roll(self, **kw):
        for index in range(2):
            yield RollFrame(
                index=index, position=index,
                image=np.full((4, 4, 3), 200, np.uint16),
                meta={"resolution_dpi": 300, "channel_order": list("RGB"),
                      "duration_s": 1.0},
                prescan=np.full((3, 3, 3), 40, np.uint8),
                registration={"contrast": 0.3},
                raw_image=np.full((4, 4, 3), 100, np.uint16),
                raw_prescan=np.full((3, 3, 3), 30, np.uint8),
            )


def run(tmp_path, monkeypatch, *argv):
    created = []

    class Patched(RecordingScanner):
        def __init__(self, **kw):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(scan_roll, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", "", "--roll", "cal", "--frames", "2", *argv],
    )
    code = scan_roll.main()
    return created[0], code


def test_a_dry_run_calibrates_up_front(tmp_path, monkeypatch):
    """The bug: it did not, and the driver then calibrated lazily inside the
    first prescan, which stalled the device twice on real hardware."""
    scanner, code = run(tmp_path, monkeypatch, "--dry-run")
    assert code == 0
    assert scanner.shading_calls, "a dry run prescans, so it needs a reference"


def test_a_real_roll_calibrates_up_front(tmp_path, monkeypatch):
    scanner, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert len(scanner.shading_calls) == 1, scanner.shading_calls


def test_reuse_reaches_the_calibration_on_a_dry_run(tmp_path, monkeypatch):
    """`--reuse` is read in `calibrate()` and nowhere else, so while a dry run
    skipped that call the flag did nothing at all and the lazy path
    recalibrated regardless."""
    scanner, _code = run(tmp_path, monkeypatch, "--dry-run", "--reuse")
    assert scanner.shading_calls[0]["reuse"] is True


def test_no_shading_reaches_the_calibration_on_a_dry_run(tmp_path, monkeypatch):
    scanner, _code = run(tmp_path, monkeypatch, "--dry-run", "--no-shading")
    assert scanner.shading_calls[0]["skip"] is True


def test_it_calibrates_once_for_the_whole_roll(tmp_path, monkeypatch):
    """Once per roll, as the vendor does once per power-on -- not per frame."""
    scanner, _code = run(tmp_path, monkeypatch, "--frames", "2")
    assert len(scanner.shading_calls) == 1, scanner.shading_calls
