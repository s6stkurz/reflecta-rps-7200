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
from rps7200.protocol import ShadingUnavailable
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
    # It used to promise raw scans, and the next scan calibrated inside itself
    # instead. Now a corrected scan is refused until a calibration succeeds --
    # and the calibration itself fails, rather than a summary line going by.
    with pytest.raises(ShadingUnavailable, match="refused"):
        s.ensure_shading(tmp_path / "shading.npz", reuse=False)
    assert not (tmp_path / "shading.npz").exists()


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
    from rps7200.direct import FULL_FRAME, ScanReadError
    from rps7200.protocol import SCSI_READ_GAIN_OFFSET, SCSI_SCAN

    t = FakeTransport(replies={SCSI_READ_GAIN_OFFSET: bytes(123)})
    s = DirectScanner(transport=t)
    s.verbose = False
    # This fake answers only the gain read, so the pass ends at its first
    # read after START SCAN -- the CCD mask. Ending there, and nowhere
    # earlier, is the proof: every refusal comes before START SCAN. This
    # used to swallow any exception at all, and the pass was in fact dying
    # at the gain read, before START SCAN, unseen. (A whole 7200 dpi frame
    # is 427 MB, too much for DeviceAtCommands to serve here.)
    with pytest.raises(ScanReadError, match="get_ccd_mask"):
        s.scan(resolution=7200, infrared=False, frame=FULL_FRAME,
               shading=False, require_media=False)
    assert any(op == SCSI_SCAN for op, _ in t.sent), \
        "shading=False must not be refused"


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
    from conftest import windows_mkdir

    calls = windows_mkdir(monkeypatch)
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


def test_a_calibration_is_kept_in_the_library_the_session_files_into(
        tmp_path, monkeypatch):
    """Not beside the cache, and not where an operator's RPS7200_DEBUG_ROOT
    sends debug passes: beside the frames its reference corrects."""
    import hashlib

    from rps7200 import library
    from rps7200.session import debug_filing_into

    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(tmp_path / "debug"))
    s = scanner()
    _calibrated(s)
    debug_filing_into(s, tmp_path / "lib")
    assert s.debug_root is None, "the operator's debug root no longer wins"
    cache = tmp_path / "calibration" / "shading.npz"
    s.ensure_shading(cache)
    folder = Path(s._shading_origin["archive"])
    sha = hashlib.sha256(b"\x01\x02" * 100).hexdigest()
    assert folder == tmp_path / "lib" / library.CALIBRATIONS / sha
    assert s._shading_origin["archive_sha256"] == sha
    assert not (tmp_path / "debug").exists()
    assert not [p for p in cache.parent.iterdir() if p.is_dir()]


def test_a_reused_reference_reads_its_calibrations_gain_by_id(tmp_path):
    """The path a cache's sidecar names was recorded relative to wherever
    the calibration ran; a window started from another folder found no
    settings there, and held no scan to them. By id it finds them in its
    own library."""
    import json

    from test_reference_moved import log

    s = scanner()
    s.library_root = tmp_path / "lib"
    ref = reference()
    folder = s.archive_calibration(
        {"data": b"\x03" * 8, "reference": ref,
         "commands": {"sent": log(([39, 33, 21], [13, 11, 30]))}},
        s.library_root)
    s._shading = ref
    s._shading_origin = {"action": "calibrated", "archive": "gone/elsewhere",
                         "archive_sha256": folder.name}
    cache = s.save_shading(tmp_path / "calibration" / "shading.npz")
    sidecar = json.loads(Path(f"{cache}.json").read_text(encoding="utf-8"))
    assert sidecar["archive_sha256"] == folder.name

    later = scanner()
    later.library_root = tmp_path / "lib"
    later.load_shading(cache)
    assert later.reference_settings == {"gain": [39, 33, 21],
                                        "offset": [13, 11, 30]}
    assert later._shading_origin["archive_sha256"] == folder.name


def test_a_kept_calibrations_reference_is_refused_as_a_tools_reference(tmp_path):
    """Calibrating replaces the file it is given; inside a kept calibration
    that is the reference its lines reduced to."""
    from rps7200.session import reference_refused

    folder = scanner().archive_calibration(
        {"data": b"\x04" * 8, "reference": reference()}, tmp_path / "lib")
    assert "kept calibration" in str(reference_refused(folder / "shading.npz"))
    assert reference_refused(tmp_path / "calibration" / "shading.npz") is None


# -- the library held while the scanner is open --------------------------------


def _claims(monkeypatch, fake):
    """`Transport.open` as the bus would answer it, on `FakeUsb`: the
    device claimed, which every refusal below must come before."""
    from rps7200 import usb_transport

    claimed = []
    monkeypatch.setattr(usb_transport, "_lib", fake)

    def open_(self):
        claimed.append(True)
        self._handle = object()
        return self

    monkeypatch.setattr(usb_transport.Transport, "open", open_)
    monkeypatch.setattr(usb_transport.Transport, "close",
                        lambda self: setattr(self, "_handle", None))
    return claimed


def test_open_holds_the_library_until_close_has_filed(monkeypatch, tmp_path):
    """The catch-all for the probes and scripts that open the scanner
    themselves: the library their calibrations and debug filing go to is
    held as in use from before the claim, and let go only once close() has
    filed what debug filing kept."""
    import os

    from conftest import FakeUsb

    from rps7200 import locks

    claimed = _claims(monkeypatch, FakeUsb())
    root = tmp_path / "library"
    scanner = DirectScanner(verbose=False, debug=True)
    scanner.library_root = scanner.debug_root = root
    held_while_filing = []
    real_flush = scanner._debug_flush
    monkeypatch.setattr(scanner, "_debug_flush", lambda *a, **k: (
        held_while_filing.append(
            [h["pid"] for h in locks.holders(locks.LIBRARY, root)]),
        real_flush(*a, **k)))
    scanner.open()
    assert claimed == [True]
    assert [h["pid"] for h in locks.holders(locks.LIBRARY, root)] \
        == [os.getpid()]
    scanner.close()
    assert held_while_filing == [[os.getpid()]]
    assert locks.holders(locks.LIBRARY, root) == []


def test_open_is_refused_before_the_claim_while_the_library_is_rewritten(
        monkeypatch, tmp_path):
    from conftest import FakeUsb, HeldElsewhere

    from rps7200 import locks

    fake = FakeUsb()
    claimed = _claims(monkeypatch, fake)
    root = tmp_path / "library"
    scanner = DirectScanner(verbose=False, debug=False)
    scanner.library_root = root
    with HeldElsewhere(locks.LIBRARY, root, exclusive=True,
                       what="tools/library.py migrate-raw --write") as other:
        with pytest.raises(locks.Refused) as refused:
            scanner.open()
    said = str(refused.value)
    assert said.startswith("not opening the scanner: tools/library.py "
                           "migrate-raw --write (")
    assert f"pid {other.pid}" in said and "Nothing was sent." in said
    assert claimed == [] and fake.transfers == []
    # Gone, the scanner opens.
    scanner.open()
    assert claimed == [True]
    scanner.close()


def test_open_goes_ahead_where_no_lock_can_be_taken(monkeypatch, tmp_path):
    """A lock folder that cannot be written is no reason to refuse a scan:
    said, and the scanner opens as it did before there were locks."""
    from conftest import FakeUsb

    from rps7200 import locks

    claimed = _claims(monkeypatch, FakeUsb())
    blocked = tmp_path / "not a folder"
    blocked.write_text("in the way", encoding="utf-8")
    monkeypatch.setenv(locks.LOCKS_ENV, str(blocked / "locks"))
    said = []
    scanner = DirectScanner(verbose=False, debug=False, log_hook=said.append)
    scanner.library_root = tmp_path / "library"
    scanner.open()
    assert claimed == [True]
    assert any("could not take the library lock" in m for m in said), said
    scanner.close()
