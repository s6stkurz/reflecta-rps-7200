"""The session state a scan cannot be re-used without.

The scanner returns raw pixels and its calibration lives only as long as the
session: the shading reference is acquired per session, the CCD mask per pass,
and the raw bytes are overwritten by the next pass. All three have to leave the
session together or the scan can never be corrected again -- which is what
`capture_record` is for, and what every tool used to assemble by reaching into
private attributes.
"""

from pathlib import Path

import numpy as np
import pytest

from conftest import FakeTransport
from rps7200.direct import DirectScanner
from rps7200.shading import ShadingReference


def reference(pixels_per_line=64, channels=(0, 1, 2)):
    """A shading reference shaped like one the device would hand back."""
    return ShadingReference(
        ref={c: np.full(pixels_per_line, 40000.0) for c in channels},
        mean={c: 40000.0 for c in channels},
        pixels_per_line=pixels_per_line,
        dark={c: np.full(pixels_per_line, 170.0) for c in channels},
        dark_mean={c: 170.0 for c in channels},
    )


def scanner():
    s = DirectScanner(transport=FakeTransport())
    s.verbose = False
    return s


# --- the reference round-trips through a file -------------------------------


def test_a_saved_reference_loads_back_identical(tmp_path):
    s = scanner()
    s.shading = reference()
    path = s.save_shading(tmp_path / "shading.npz")
    assert path is not None and path.exists()

    other = scanner()
    loaded = other.load_shading(path)
    assert loaded.pixels_per_line == 64
    assert loaded.channels == [0, 1, 2]
    assert loaded.two_point
    for c in loaded.channels:
        assert np.array_equal(loaded.ref[c], s.shading.ref[c])
        assert np.array_equal(loaded.dark[c], s.shading.dark[c])


def test_saving_without_a_reference_writes_nothing(tmp_path):
    """A session that never calibrated must not leave an empty file behind."""
    path = tmp_path / "nested" / "shading.npz"
    assert scanner().save_shading(path) is None
    assert not path.exists()


def test_saving_creates_the_directory(tmp_path):
    s = scanner()
    s.shading = reference()
    assert s.save_shading(tmp_path / "calibration" / "shading.npz").exists()


# --- ensure_shading decides once, and says what it decided ------------------


def test_skip_leaves_the_session_with_no_reference(tmp_path):
    s = scanner()
    result = s.ensure_shading(tmp_path / "shading.npz", skip=True)
    assert result["action"] == "skipped"
    assert s.shading is None
    assert "striping" in result["summary"]


def test_reuse_loads_the_cached_reference_without_calibrating(tmp_path):
    path = tmp_path / "shading.npz"
    seed = scanner()
    seed.shading = reference()
    seed.save_shading(path)

    s = scanner()
    s.calibrate_shading = lambda **kw: pytest.fail("must not calibrate")
    result = s.ensure_shading(path, reuse=True)
    assert result["action"] == "loaded"
    assert s.shading is not None
    assert str(path) in result["summary"]


def test_reuse_falls_back_to_calibrating_when_the_file_is_absent(tmp_path):
    """--reuse on a fresh checkout must calibrate, not scan uncorrected."""
    s = scanner()
    ran = []

    def fake_calibrate(**kw):
        ran.append(True)
        s.shading = reference()
        return {"reference": s.shading, "bytes_drained": 1_660_000}

    s.calibrate_shading = fake_calibrate
    result = s.ensure_shading(tmp_path / "missing.npz", reuse=True)
    assert ran and result["action"] == "calibrated"
    assert (tmp_path / "missing.npz").exists()


def test_a_calibration_that_yields_nothing_says_so(tmp_path):
    s = scanner()
    s.calibrate_shading = lambda **kw: {"reference": None, "bytes_drained": 0}
    result = s.ensure_shading(tmp_path / "shading.npz", reuse=False)
    assert result["reference"] is None
    # It used to promise raw scans, and the next scan calibrated inside itself
    # instead. Now a corrected scan is refused until a calibration succeeds.
    assert "no usable shading reference" in result["summary"]
    assert "refused" in result["summary"]


# --- capture_record ---------------------------------------------------------


def test_capture_record_carries_everything_the_library_needs():
    s = scanner()
    s.shading = reference()
    s._ccd_mask = b"\x00" * 64
    s.last_raw = b"RRdata"
    s.last_raw_layout = {"format": "index", "width": 32}

    record = s.capture_record()
    assert set(record) == {"reference", "ccd_mask", "raw", "raw_layout"}
    assert record["reference"] is s.shading
    assert record["ccd_mask"] == b"\x00" * 64
    assert record["raw"] == b"RRdata"
    assert record["raw_layout"]["width"] == 32


def test_capture_record_is_accepted_by_library_save(tmp_path):
    """The keys must line up with library.save's parameters, not merely exist."""
    from rps7200 import library

    s = scanner()
    s.shading = reference()
    s._ccd_mask = b"\x00" * 64
    s.last_raw = b"raw bytes"
    s.last_raw_layout = {"format": "index"}

    entry = library.save(
        np.zeros((4, 4, 3), np.uint16),
        {"resolution_dpi": 600, "channels": 3},
        root=tmp_path,
        **s.capture_record(),
    )
    assert (entry / "shading.npz").exists()
    assert (entry / "ccd_mask.bin").exists()
    assert (entry / "raw.bin.gz").exists()


def test_a_session_that_captured_nothing_records_nothing():
    record = scanner().capture_record()
    assert record == {
        "reference": None, "ccd_mask": None, "raw": None, "raw_layout": None
    }


# --- a short response is refused, not indexed into -------------------------
#
# This wedged the scanner on 2026-09-13. get_gain_offset came back short
# inside calibrate_shading's read loop -- a loop that catches ScanReadError
# and would have carried on -- but the short buffer raised IndexError from
# `d[66]` instead, which is not a ScanReadError, so it escaped the loop,
# abandoned the calibration mid-read, and cost a power cycle.


def test_a_short_gain_offset_response_is_a_scan_error_not_an_indexerror():
    from rps7200.direct import ScanReadError
    from rps7200.protocol import SCSI_READ_GAIN_OFFSET

    s = DirectScanner(
        transport=FakeTransport(replies={SCSI_READ_GAIN_OFFSET: b"\x00" * 40})
    )
    s.verbose = False
    with pytest.raises(ScanReadError, match="expected 123"):
        s.get_gain_offset()


def test_a_full_length_gain_offset_response_still_decodes():
    from rps7200.protocol import SCSI_READ_GAIN_OFFSET

    blob = bytearray(123)
    blob[60:62] = (1234).to_bytes(2, "little")
    blob[72] = 39
    s = DirectScanner(
        transport=FakeTransport(replies={SCSI_READ_GAIN_OFFSET: bytes(blob)})
    )
    s.verbose = False
    got = s.get_gain_offset()
    assert got.exposure[0] == 1234
    assert got.gain[0] == 39


# --- 7200 dpi is refused before it costs anything --------------------------


def test_a_7200_dpi_pass_is_refused_without_touching_the_scanner():
    """The device will not calibrate wider than MAX_SHADING_COLUMNS at any
    resolution (measured 2026-09-13), and a 7200 dpi frame is twice that. The
    refusal has to land before the pass: discovering it afterwards spends 5.5
    minutes of hardware to learn what the frame and resolution already say."""
    from rps7200.direct import FULL_FRAME, ShadingUnavailable
    from rps7200.protocol import SCSI_SCAN

    t = FakeTransport()
    s = DirectScanner(transport=t)
    s.verbose = False
    with pytest.raises(ShadingUnavailable, match="cannot be corrected at all"):
        s.scan(resolution=7200, infrared=False, frame=FULL_FRAME,
               require_media=False)

    assert not any(op == SCSI_SCAN for op, _ in t.sent), (
        "the refusal must come before START SCAN, or it costs scanner time"
    )


def test_shading_false_is_still_allowed_at_7200_dpi():
    """Raw pixels on purpose stays available -- the refusal is about silently
    shipping uncorrected ones, not about forbidding the resolution."""
    from rps7200.direct import FULL_FRAME, ShadingUnavailable

    t = FakeTransport()
    s = DirectScanner(transport=t)
    s.verbose = False
    try:
        s.scan(resolution=7200, infrared=False, frame=FULL_FRAME,
               shading=False, require_media=False)
    except ShadingUnavailable:  # pragma: no cover
        pytest.fail("shading=False must not be refused")
    except Exception:
        pass  # the fake cannot serve a real pass; only the refusal matters here


# --- a calibration's own bytes are kept -------------------------------------


def _calibrated(s, data=b"\x01\x02" * 100):
    ref = reference()

    def calibrate(**kw):
        s._shading = ref
        s._shading_origin = {"action": "calibrated", "measured_utc": "now"}
        return {"reference": ref, "bytes_drained": len(data), "data": data,
                "ccd_mask": b"\x01" * 8, "pixels_per_line": 8,
                "bytes_per_line": 18, "resolution": 3600,
                "commands": {"sent": [{"cdb": "28"}]}, "duration_s": 1.0}

    s.calibrate_shading = calibrate
    return ref


def test_a_calibration_keeps_its_own_bytes(tmp_path):
    """The reference is a reduction of these; once they are gone no
    correction in the library can be recomputed from scratch."""
    import json

    s = scanner()
    _calibrated(s)
    result = s.ensure_shading(tmp_path / "calibration" / "shading.npz")
    folder = Path(s._shading_origin["archive"])
    assert (folder / "data.bin").read_bytes() == b"\x01\x02" * 100
    record = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
    assert record["bytes"] == 200 and record["sha256"]
    assert record["commands"]["sent"][0]["cdb"] == "28"
    assert (folder / "shading.npz").exists() and (folder / "ccd_mask.bin").exists()
    assert str(folder) in result["summary"]


def test_a_cache_that_cannot_be_written_does_not_cost_the_calibration(tmp_path):
    """A successful 3-4 minute calibration was discarded over a full disk,
    and every scan after it refused."""
    s = scanner()
    ref = _calibrated(s)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    result = s.ensure_shading(blocker / "shading.npz")
    assert result["reference"] is ref
    assert s._shading is ref
    assert "not cached" in result["summary"]


def test_a_parent_in_the_way_fails_the_archive_once(tmp_path, monkeypatch):
    """Windows answers `mkdir` under a file with FileExistsError for a folder
    that does not exist, and a retry-the-next-name loop took that as a name
    collision and never ended -- inside a calibration, with the device open.
    Windows' answer is reproduced here so the loop is caught on any runner."""
    import os

    real_mkdir = os.mkdir
    calls = []

    def windows_mkdir(path, *args, **kwargs):
        calls.append(path)
        assert len(calls) < 50, "mkdir retried without end"
        p = Path(path)
        if any(parent.is_file() for parent in p.parents):
            raise FileNotFoundError(path)
        if p.is_file():
            raise FileExistsError(path)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", windows_mkdir)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    s = scanner()
    with pytest.raises(OSError):
        s.archive_calibration({"data": b"\x01" * 8}, blocker / "calibration")
    assert len(calls) < 10


def test_two_calibrations_in_one_second_keep_both(tmp_path):
    s = scanner()
    first = s.archive_calibration({"data": b"\x01" * 8}, tmp_path)
    second = s.archive_calibration({"data": b"\x02" * 8}, tmp_path)
    assert first != second
    assert (first / "data.bin").read_bytes() == b"\x01" * 8
    assert (second / "data.bin").read_bytes() == b"\x02" * 8
