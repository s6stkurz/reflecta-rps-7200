"""A pass read in full and then lost to the host is filed, tagged failed.

The raw bytes are kept so the decode can be fixed later, and the one case
where the decode visibly fails -- a tag the firmware was not expected to send,
an infrared pass that came back with three planes, a pass wider than its
reference -- dropped them, debug filing included, and the next pass
overwrote what was left. These drive `DirectScanner.scan` itself, through the
real read and decode, with only the device's answers scripted.

A pass that fails before any line arrives -- the wedge cases -- has nothing
to file, and is written down instead (`DirectScanner._record_failure`).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import FakeTransport, settings
from rps7200 import library, tiff
from rps7200.direct import DirectScanner, library_named
from rps7200.protocol import (
    INDEX_HEADER,
    SCSI_SCAN,
    EndOfData,
    Inquiry,
    ScanParameters,
    ScanReadError,
    ShadingUnavailable,
    State,
)
from rps7200.usb_transport import CheckCondition, NoDataYet, UsbError
from rps7200.shading import ShadingReference

#: A frame 8 columns wide at 300 dpi, so the reference below covers it.
FRAME = (0, 0, 191, 100)
LINES = 4


def tagged(image: np.ndarray, tags: bytes = b"RGB") -> bytes:
    """``image`` as the scanner sends it: a tagged line per plane per row."""
    out = bytearray()
    for row in range(image.shape[0]):
        for c, tag in enumerate(tags):
            out += bytes([tag]) * INDEX_HEADER
            out += image[row, :, c].astype("<u2").tobytes()
    return bytes(out)


def picture(width: int) -> np.ndarray:
    return np.random.default_rng(width).integers(
        0, 65535, (LINES, width, 3)).astype(np.uint16)


class OnePass(DirectScanner):
    """Just enough of a device for `scan`, its lines scripted."""

    def __init__(self, blob: bytes, width: int, **kw):
        super().__init__(transport=FakeTransport(), debug=False, **kw)
        self._own_transport = False
        self.width = width
        stride = width * 2 + INDEX_HEADER
        self._lines = iter(blob[k * stride:(k + 1) * stride]
                           for k in range(len(blob) // stride))

    def read_state(self, retries=3):
        return SimpleNamespace(warming_up=False, media_loaded=True, scanning=0)

    def wait_warm(self, *a, **kw):
        pass

    def test_unit_ready(self):
        return True

    def set_exposure_time(self, *a, **kw):
        pass

    def set_highlight_shadow(self, *a, **kw):
        pass

    def set_scan_frame(self, *a, **kw):
        pass

    def cmd_17(self, *a, **kw):
        pass

    def get_gain_offset(self):
        return settings(8000, 20000, 50000, 8000)

    def set_gain_offset(self, s, infrared=False):
        pass

    def set_mode(self, **kw):
        pass

    def slide(self, *a, **kw):
        pass

    def wait_ready(self, *a, **kw):
        return True

    def carriage_record(self):
        return None

    def start_scan(self, *a, **kw):
        pass

    def get_ccd_mask(self, size):
        return b"\x00" * size

    def get_parameters(self):
        return ScanParameters(width=self.width, lines=LINES,
                              bytes_per_line=self.width * 2, filter_offset1=0,
                              filter_offset2=0, available_lines=LINES)

    def finish_scan(self, polls=3):
        pass

    def read_lines(self, lines, bytes_per_line, retries=3, **kw):
        out = b"".join(line for _, line in zip(range(lines), self._lines))
        if not out:
            raise EndOfData("no more lines")
        return out


def scan(s: OnePass, **kw):
    return s.scan(resolution=300, infrared=False, frame=FRAME, **kw)


def test_a_pass_whose_bytes_do_not_decode_is_filed_tagged_failed(tmp_path):
    blob = tagged(picture(8), tags=b"XYZ")          # no channel it knows
    s = OnePass(blob, width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError, match="no recognisable channel tags"):
        scan(s, shading=False, keep_raw=True)
    assert library.entries(tmp_path) == [], "filed with the device open"
    s.close()
    [record] = library.entries(tmp_path)
    entry = tmp_path / record["id"]
    assert "failed" in record["tags"]
    assert library.read_raw(entry) == blob, "not the bytes the scanner sent"
    assert record["extra"]["failed"]["stage"] == "in the decode"
    assert "no recognisable channel tags" in record["extra"]["failed"]["error"]
    # What scan.tif can hold of bytes nothing could decode: the lines as
    # they arrived, and said to be.
    stride = 8 * 2 + INDEX_HEADER
    lines = tiff.read(str(entry / "scan.tif"))
    assert lines.reshape(-1).tobytes() == blob[: len(blob) // stride * stride]
    assert record["raw"]["layout"]["lines_received"] == 3 * LINES
    # Not a picture, so not held against the bytes as one at filing: every
    # field would have disagreed, about the pass that most needs its bytes.
    assert record["raw"]["disagrees"] is None
    assert not [p for p in library.verify(tmp_path) if "cannot be" in p]
    assert record["extra"]["commands"] is not None
    # The entry says it all: no failure record beside it as well.
    assert library.failure_records(tmp_path) == []


def test_a_pass_that_failed_after_its_read_leaves_no_bytes_for_later(tmp_path):
    """`read_planes` holds a pass's bytes in `last_raw` before the decode, and
    a decode that then raised left them there, for the next `capture_record`
    to hand over as whatever pass a caller filed next. Spooled, they are kept
    where they belong; held, they were one call from another photograph."""
    s = OnePass(tagged(picture(8), tags=b"XYZ"), width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError, match="no recognisable channel tags"):
        scan(s, shading=False, keep_raw=True)
    record = s.capture_record()
    assert record["raw"] is None and record["raw_layout"] is None
    assert s.last_pixels_raw is None
    s.close()
    [entry] = library.entries(tmp_path)
    assert "failed" in entry["tags"], "and the pass itself is still filed"


def test_a_pass_filed_undecodable_is_known_to_reconstruct_not_a_regression(
        tmp_path):
    """Its bytes would not decode the day they arrived; that is why it was
    filed tagged failed. `reconstruct` called it "could not decode", a
    regression, and kept `make reconstruct` red for good over it."""
    s = OnePass(tagged(picture(8), tags=b"XYZ"), width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=True)
    s.close()
    [record] = library.entries(tmp_path)
    image, verdict = library.reconstruct(tmp_path / record["id"])
    assert image is None
    assert verdict.kind == library.KNOWN_FAILED, verdict
    assert "in the decode" in verdict and "no recognisable channel tags" in verdict


def test_a_read_given_up_part_way_whose_lines_decoded_is_compared_as_any(
        tmp_path):
    """Its scan.tif is a decode, so the exemption is not its to have: today's
    decode must still reproduce it."""
    s = CutShort(tagged(picture(8)), width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=True)
    s.close()
    [record] = library.entries(tmp_path)
    assert record["extra"]["failed"]["pixels"] == "decoded"
    _image, verdict = library.reconstruct(tmp_path / record["id"])
    assert verdict.kind == library.IDENTICAL, verdict


def test_a_pass_that_fails_after_its_decode_is_filed_with_its_pixels(tmp_path):
    """Wider than the reference that was to correct it: refused after the
    read, as it must be, and the pixels and bytes of the pass kept."""
    image = picture(12)
    s = OnePass(tagged(image), width=12)
    s.debug_root = tmp_path
    s._shading = ShadingReference(ref={c: np.full(8, 3.0) for c in range(3)},
                                  mean={c: 3.0 for c in range(3)},
                                  pixels_per_line=8)
    with pytest.raises(ShadingUnavailable, match="came back 12 columns"):
        scan(s, shading=True, keep_raw=True)
    s.close()
    [record] = library.entries(tmp_path)
    entry = tmp_path / record["id"]
    assert "failed" in record["tags"]
    assert record["extra"]["failed"]["stage"] == "after the decode"
    assert np.array_equal(tiff.read(str(entry / "scan.tif")), image)
    assert record["raw"]["disagrees"] == {}, "its own bytes, said otherwise"
    rebuilt, verdict = library.reconstruct(entry)
    assert rebuilt is not None and verdict.startswith("identical"), verdict
    # Not corrected, and saying why rather than passing for a raw pass.
    _pixels, loaded = library.corrected(entry)
    assert loaded["corrected"] == "raw -- correction was asked for"


def test_a_pass_taken_raw_on_purpose_is_still_raw_on_purpose_when_it_fails(
        tmp_path):
    """`shading=False` with no reference in the session -- calibration off.
    Its record said "the pass failed", which `verify` and `corrected` read
    as a correction that was asked for and missed: `make verify` red for
    good, over a pass that never wanted one."""
    s = OnePass(tagged(picture(8), tags=b"XYZ"), width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=True)
    s.close()
    [record] = library.entries(tmp_path)
    assert "failed" in record["tags"]
    assert "no recognisable channel tags" in record["extra"]["failed"]["error"]
    assert not [p for p in library.verify(tmp_path) if "shading" in p], \
        library.verify(tmp_path)
    _pixels, loaded = library.corrected(tmp_path / record["id"])
    assert loaded["corrected"] == "deliberately raw"


class CutShort(OnePass):
    """Read six lines at a time, and refused after the first six: two rows."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.served = 0

    def read_planes(self, params, channels, **kw):
        return super().read_planes(params, channels, batch=6, **kw)

    def read_lines(self, lines, bytes_per_line, retries=3, **kw):
        if self.served:
            raise ScanReadError("reading 6 lines was refused: aborted command")
        self.served += lines
        return b"".join(line for _, line in zip(range(lines), self._lines))


def test_a_read_given_up_part_way_keeps_the_lines_that_arrived(tmp_path):
    """The anomalous pass, and the one whose bytes are most worth keeping:
    they lived only in the read's own list and went with it. The device is
    suspect and nothing drives it again, but what it sent is filed."""
    image = picture(8)
    blob = tagged(image)
    s = CutShort(blob, width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError, match="aborted command"):
        scan(s, shading=False, keep_raw=True)
    assert s.suspect is not None, "the device may still be mid-scan"
    s.close()
    [record] = library.entries(tmp_path)
    entry = tmp_path / record["id"]
    assert "failed" in record["tags"]
    assert record["extra"]["failed"]["stage"] == "during the read"
    stride = 8 * 2 + INDEX_HEADER
    assert library.read_raw(entry) == blob[: 6 * stride]
    assert np.array_equal(tiff.read(str(entry / "scan.tif")), image[:2])
    assert record["raw"]["disagrees"] == {}, "the rows that arrived, said otherwise"


def test_a_caller_that_keeps_no_bytes_is_not_filed_a_failed_pass(tmp_path):
    """`--no-library`, a probe that files nothing: not filed behind its back.
    What it sent and why it failed is still written down, in a few
    kilobytes beside the entries rather than as one."""
    s = OnePass(tagged(picture(8), tags=b"XYZ"), width=8)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=False)
    s.close()
    assert library.entries(tmp_path) == []
    [(_folder, [path])] = library.failure_records(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "no recognisable channel tags" in record["error"]
    assert record["bytes"].startswith("its caller kept no bytes")
    assert record["lines_arrived"] == LINES * 3, "every line came, and went"


def test_a_pass_that_succeeds_holds_no_bytes_for_later(tmp_path):
    s = OnePass(tagged(picture(8)), width=8)
    scan(s, shading=False, keep_raw=True)
    assert s._in_flight is None


class RefusedMode(OnePass):
    def set_mode(self, **kw):
        raise CheckCondition(0x15)


def test_a_pass_refused_before_its_read_stops_its_command_log():
    """It went on recording every later command into a list the next pass
    threw away, so what led to the refusal was the one thing never kept."""
    s = RefusedMode(b"", width=8)
    with pytest.raises(CheckCondition):
        scan(s, shading=False, keep_raw=True)
    assert s.t.record is None, "still recording after the pass failed"
    assert s.last_failed_commands is not None
    # And kept past the process, in the library debug filing would use.
    [record] = records(library.DEFAULT_ROOT)
    assert record["bytes"] == "it failed before its read began"
    assert record["pass"] is None, "it was never set up"
    assert record["request"]["resolution"] == 300


def test_a_pass_that_failed_leaves_no_earlier_pass_bytes_behind():
    """`capture_record` then handed an earlier pass's bytes over as this one's."""
    s = RefusedMode(b"", width=8)
    s.last_raw = b"another pass"
    s.last_raw_layout = {"width": 8}
    with pytest.raises(CheckCondition):
        scan(s, shading=False, keep_raw=True)
    assert s.capture_record()["raw"] is None
    assert s.capture_record()["raw_layout"] is None


def test_a_read_the_device_ended_early_says_so_in_the_record():
    """"End of data" part way through is taken as the end, and the pass
    decodes to fewer rows. It was filed as an ordinary pass, and without its
    raw bytes nothing in the entry said it was short."""
    image = picture(8)
    s = OnePass(tagged(image[:3]), width=8)       # 3 of the 4 rows declared
    got, meta = scan(s, shading=False, keep_raw=False)
    assert got.shape[0] == 3
    assert meta["lines_declared"] == LINES and meta["short_read"] is True
    whole = OnePass(tagged(image), width=8)
    assert scan(whole, shading=False, keep_raw=False)[1]["short_read"] is False


def test_a_pass_records_what_it_was_taken_for_and_only_that_pass():
    """Set by the loop that takes it -- a metering probe, a verification
    prescan -- and recorded with that pass, never the next."""
    s = OnePass(tagged(picture(8)) * 2, width=8)
    s._pass_role = {"kind": "metering probe", "round": 1}
    _, meta = scan(s, shading=False, keep_raw=True)
    assert meta["pass_role"] == {"kind": "metering probe", "round": 1}
    _, meta = scan(s, shading=False, keep_raw=True)
    assert "pass_role" not in meta


# --- a pass no line of which arrived is written down ----------------------


class NoLineComes(OnePass):
    """START SCAN taken, through the command log, and then "not yet" for
    ever: the device that wedged, as the read sees it."""

    def start_scan(self, *a, **kw):
        DirectScanner.start_scan(self, *a, **kw)

    def read_lines(self, lines, bytes_per_line, retries=3, **kw):
        raise NoDataYet("not scanned that far yet")


def met(s: DirectScanner) -> DirectScanner:
    """What a session knows of its device before a pass: the INQUIRY and a
    READ STATE, as a failure record should carry them."""
    s._inquiry = Inquiry(
        vendor="PIE", product="RPS 7200", model=0x30, firmware="1.04",
        max_resolution=7200, ccd_width=10344, ccd_length=6888, filters=0x1f,
        depths=0x0f, formats=0x07, optional_devices=0,
        frame=(0, 0, 10343, 6887), preview_resolution=300, raw_hex="00ff")
    s.last_state = State(button=False, warming_up=False, scanning=0x0d,
                         busy=0, position=3, raw=bytes(range(13)))
    return s


def records(root) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for _folder, paths in library.failure_records(root) for p in paths]


@pytest.mark.parametrize("keep_raw", [True, False])
def test_a_pass_no_line_of_which_arrived_is_written_down(no_waiting, tmp_path,
                                                         keep_raw):
    """"No data for 120 s at 0/N lines" -- the wedge case. There were no
    bytes to spool, and the commands that led there lived only in
    `last_failed_commands`, gone with the process. Debug filing off, as it
    is in ordinary use: the record is written whatever it says.

    And the same for a caller that keeps no bytes -- `--no-library`, a roll
    filed nowhere, a probe -- whose record called the wedge "its caller kept
    no bytes": it had no list of lines to tell the two apart by."""
    s = met(NoLineComes(b"", width=8))
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError, match="no data for 120s at 0/12 lines"):
        scan(s, shading=False, keep_raw=keep_raw, should_stop=lambda: False)
    assert s.suspect is not None, "the device may still be mid-scan"
    [record] = records(tmp_path)
    assert record["kind"] == "scan"
    assert "no data for 120s at 0/12 lines" in record["error"]
    assert record["bytes"] == "no line of it arrived"
    assert record["lines_arrived"] == 0
    assert record["suspect"] == s.suspect
    # What it was asked for, off the call, and what it was set up as.
    assert record["request"]["resolution"] == 300
    assert record["request"]["frame"] == list(FRAME)
    assert record["request"]["keep_raw"] is keep_raw
    assert "should_stop" not in record["request"], "a function is no setting"
    assert record["pass"]["resolution_dpi"] == 300
    assert record["pass"]["exposure"] == [8000, 20000, 50000, 8000]
    # Every command sent, as an entry's `extra.commands` holds them.
    sent = record["commands"]["sent"]
    assert any(c["cdb"] == bytes([SCSI_SCAN, 0, 0, 0, 1, 0]).hex() for c in sent)
    assert record["commands"] == s.last_failed_commands
    assert record["last_state"]["read_state"] == bytes(range(13)).hex()
    assert record["last_state"]["position"] == 3
    assert record["inquiry"]["product"] == "RPS 7200"
    assert record["inquiry"]["raw_hex"] == "00ff"
    assert (record["driver"]["source_sha256_at_import"]
            == library._AT_IMPORT["source_sha256"])
    assert record["library"] == library_named(tmp_path)
    s.close()
    assert library.entries(tmp_path) == [], "nothing of it to file"
    assert library.verify(tmp_path) == [], "the record is not a broken entry"


def test_a_failure_record_goes_with_the_session_s_library(no_waiting,
                                                         tmp_path):
    """Where its calibrations go (`library_root`), not RPS7200_DEBUG_ROOT's:
    `verify` and `list` on the library the run files into are where anyone
    looks, and a debug root elsewhere hid it from both."""
    s = met(NoLineComes(b"", width=8))
    s.library_root = tmp_path / "library"
    s.debug_root = tmp_path / "debug"
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=False, should_stop=lambda: False)
    [record] = records(tmp_path / "library")
    assert record["library"] == library_named(tmp_path / "library")
    assert records(tmp_path / "debug") == []
    s.close()


class StartNeverAnswered(OnePass):
    """START SCAN sent, and the bus gave up waiting for its status."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

        def timed_out(command):
            raise UsbError("LIBUSB_ERROR_TIMEOUT: the status never came")

        self.t._inner.replies[SCSI_SCAN] = timed_out

    def start_scan(self, *a, **kw):
        DirectScanner.start_scan(self, *a, **kw)


@pytest.mark.parametrize("keep_raw", [True, False])
def test_a_start_scan_whose_status_never_came_is_written_down(tmp_path,
                                                              keep_raw):
    """Its read never began, whatever its caller would have kept of it."""
    s = StartNeverAnswered(b"", width=8)
    s.debug_root = tmp_path
    with pytest.raises(UsbError, match="status never came"):
        scan(s, shading=False, keep_raw=keep_raw)
    [record] = records(tmp_path)
    assert "status never came" in record["error"]
    assert record["bytes"] == "it failed before its read began"
    assert record["lines_arrived"] is None
    [start] = [c for c in record["commands"]["sent"]
               if c["cdb"].startswith(f"{SCSI_SCAN:02x}")]
    assert "status never came" in start["refused"]


def test_a_pass_refused_before_it_sent_anything_is_not_written_down(tmp_path):
    """No reference for a corrected pass: refused before its first command,
    with nothing to say about the device."""
    s = OnePass(b"", width=8)
    s.debug_root = tmp_path
    with pytest.raises(ShadingUnavailable):
        scan(s, shading=True, keep_raw=True)
    assert records(tmp_path) == []


def test_a_record_that_cannot_be_written_leaves_the_failure_as_it_was(
        no_waiting, tmp_path, monkeypatch):
    """Nowhere to write it: said, and the pass's own exception goes on, not
    one of the record's."""
    said: list[str] = []

    def refused(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(library, "_write_atomic", refused)
    s = NoLineComes(b"", width=8, log_hook=said.append)
    s.debug_root = tmp_path
    with pytest.raises(ScanReadError, match="no data for 120s"):
        scan(s, shading=False, keep_raw=True)
    assert any("could not write down the failed scan" in m for m in said), said


def test_a_library_that_cannot_be_written_has_it_in_the_temporary_directory(
        no_waiting, tmp_path):
    """As the spool falls back there; the record names the library it was
    meant for, and that library's tools find it there."""
    import tempfile

    blocked = tmp_path / "library"
    blocked.mkdir()
    (blocked / DirectScanner.FAILURES_DIR).write_text("a file in the way")
    s = NoLineComes(b"", width=8)
    s.debug_root = blocked
    with pytest.raises(ScanReadError):
        scan(s, shading=False, keep_raw=True)
    [(folder, [path])] = library.failure_records(blocked)
    assert folder == DirectScanner.failure_folders(blocked)[1]
    assert folder.parent == Path(tempfile.gettempdir())
    assert json.loads(path.read_text(encoding="utf-8"))["library"] \
        == library_named(blocked)
    # Another library's records there are not this one's.
    assert library.failure_records(tmp_path / "another") == []


def test_two_failures_in_one_second_are_two_records(no_waiting, tmp_path,
                                                    monkeypatch):
    from rps7200 import direct

    monkeypatch.setattr(direct.time, "gmtime", lambda *a: direct.time._real
                        .gmtime(0))
    s = NoLineComes(b"", width=8)
    s.debug_root = tmp_path
    for _ in range(2):
        with pytest.raises(ScanReadError):
            scan(s, shading=False, keep_raw=True)
        s.suspect = None                  # a stand-in's power cycle
    [(_folder, paths)] = library.failure_records(tmp_path)
    assert [p.name for p in paths] == ["19700101T000000Z_scan.json",
                                       "19700101T000000Z_scan-2.json"]


# --- a calibration ends on end of data, and only on its whole -------------

#: Calibration columns: lines are 16-bit whatever the mode depth.
CAL_WIDTH = 8
DARK, LIT = 170, 47000


def calibration_lines(dark: int, lit: int) -> list[bytes]:
    """Lines as the calibration sends them: the unlit phase first, then the
    lit one, channel after channel throughout."""
    out = []
    for k in range(dark + lit):
        level = DARK if k < dark else LIT
        out.append(bytes([b"RGB"[k % 3]]) * INDEX_HEADER
                   + np.full(CAL_WIDTH, level, "<u2").tobytes())
    return out


class Calibrating(OnePass):
    """A calibration whose four-line reads are scripted, then ``then``.

    Its descriptor declares ``declared`` lines, one phase's worth, as the
    device's does (4 x 20, where about 160 arrive)."""

    def __init__(self, lines, declared, then=EndOfData):
        super().__init__(b"", CAL_WIDTH)
        self.blocks = [b"".join(lines[k:k + 4]) for k in range(0, len(lines), 4)]
        self.declared = declared
        self.then = then

    def get_shading_parms(self):
        return [{"pixels_per_line": 2 * CAL_WIDTH,
                 "lines": self.declared // 3}] * 3

    def read_lines(self, lines, bytes_per_line, retries=3, **kw):
        if self.blocks:
            return self.blocks.pop(0)
        raise self.then("refused: key=0x06 code=0x29 -- unit attention")


@pytest.fixture
def no_waiting(monkeypatch):
    from conftest import NoWaiting
    from rps7200 import direct

    monkeypatch.setattr(direct, "time", NoWaiting())


def test_a_whole_calibration_builds_its_reference(no_waiting):
    s = Calibrating(calibration_lines(12, 12), declared=12)
    result = s.calibrate_shading()
    assert result["refused"] is None and s.suspect is None
    assert s.shading is not None and s.shading.two_point
    assert s.shading.mean[0] == LIT


def test_a_calibration_refused_part_way_is_not_taken_as_finished(no_waiting):
    """Any refusal ended it as though the device had finished: the device,
    maybe still mid-calibration, was driven on, and the reference built from
    the lines so far -- the dark floor -- corrected every scan after it."""
    s = Calibrating(calibration_lines(12, 12)[:4], declared=12,
                    then=ScanReadError)
    before = s.shading
    with pytest.raises(ScanReadError, match="unit attention"):
        s.calibrate_shading()
    assert s.suspect is not None, "the device may still be mid-calibration"
    assert s.shading is before, "a reference was built from a partial pass"


def test_a_calibration_ended_short_of_its_declared_lines_installs_nothing(
        no_waiting):
    """End of data in the dark phase: its reference would be ~170 counts,
    divided into every scan and filed as corrected."""
    s = Calibrating(calibration_lines(12, 12)[:8], declared=12)
    result = s.calibrate_shading(keep_data=True)
    assert s.shading is None and result["reference"] is None
    assert "8 lines arrived where the descriptor declared 12" in result["refused"]
    assert result["data"], "the lines are kept all the same"


def test_a_calibration_ended_at_the_phase_boundary_installs_nothing(
        no_waiting):
    """As many lines as the descriptor declares, every one of them dark: the
    count passes it, no level gap splits it, and its single-point reference
    was the ~170-count floor."""
    s = Calibrating(calibration_lines(12, 12)[:12], declared=12)
    result = s.calibrate_shading(keep_data=True)
    assert s.shading is None and result["reference"] is None
    assert "channels R, G, B did not split" in result["refused"]
    assert result["data"], "the lines are kept all the same"


def test_a_calibration_lit_in_only_some_channels_installs_nothing(
        no_waiting):
    """One lit line in, red's: red splits, green and blue are still the
    dark floor alone, and a reference is per channel."""
    s = Calibrating(calibration_lines(12, 12)[:13], declared=12)
    result = s.calibrate_shading()
    assert s.shading is None and result["reference"] is None
    assert "channels G, B did not split" in result["refused"]


def test_a_calibration_refused_part_way_keeps_its_lines(no_waiting, tmp_path):
    """They went with it: the only record of what the device sent before it
    stopped. Archived beside the good ones, saying where it failed, with no
    reference to be taken for one."""
    lines = calibration_lines(12, 12)
    s = Calibrating(lines[:4], declared=12, then=ScanReadError)
    s.debug_root = tmp_path / "library"
    s.library_root = tmp_path / "library"
    with pytest.raises(ScanReadError):
        s.ensure_shading(tmp_path / "calibration" / "shading.npz")
    [folder] = (tmp_path / "library" / library.CALIBRATIONS).iterdir()
    assert (folder / "data.bin").read_bytes() == b"".join(lines[:4])
    record = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
    assert record["failed"]["stage"] == "during the read"
    assert "unit attention" in record["failed"]["error"]
    assert record["reference"] is None and not (folder / "shading.npz").exists()
    assert record["commands"] is not None
    assert not (folder / library.INCOMPLETE).exists()
    assert s.last_failed_calibration is None, "held for a second archive"
    # The archive holds its commands: no failure record as well.
    assert records(tmp_path / "library") == []


def test_a_calibration_no_line_of_which_arrived_is_written_down(no_waiting,
                                                                tmp_path):
    """Refused at its first read: no lines for the archive, which keeps a
    calibration only by its bytes, so what it sent is written down."""
    s = Calibrating([], declared=12, then=ScanReadError)
    s.debug_root = tmp_path / "library"
    with pytest.raises(ScanReadError, match="unit attention"):
        s.ensure_shading(tmp_path / "calibration" / "shading.npz")
    assert not (tmp_path / "calibration").exists() or not any(
        (tmp_path / "calibration").iterdir()), "an archive of no bytes"
    [record] = records(tmp_path / "library")
    assert record["kind"] == "calibration"
    assert record["bytes"] == "no line of it arrived"
    assert record["calibration"]["failed"]["stage"] == "during the read"
    assert "data" not in record["calibration"]
    assert record["request"] == {"resolution": 3600, "timeout": 300.0,
                                 "keep_data": True}
    assert record["commands"] is not None


def test_a_calibration_archive_cut_short_says_so(tmp_path, monkeypatch):
    """Written in place, a folder a kill or a full disk cut short passed for
    a whole calibration."""
    def full(path, text):
        raise OSError(28, "No space left on device")

    s = DirectScanner(transport=FakeTransport())
    s.verbose = False
    whole = s.archive_calibration({"data": b"\x01" * 8}, tmp_path / "a")
    assert not (whole / library.INCOMPLETE).exists()
    monkeypatch.setattr(library, "_write_atomic", full)
    with pytest.raises(OSError):
        s.archive_calibration({"data": b"\x01" * 8}, tmp_path / "b")
    [cut] = (tmp_path / "b" / library.CALIBRATIONS).iterdir()
    assert (cut / library.INCOMPLETE).exists()
    assert not (cut / "calibration.json").exists()
