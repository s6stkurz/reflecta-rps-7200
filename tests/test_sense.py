"""What the scanner said, and what the driver does about it.

Every refusal arrives as one CHECK CONDITION; only the additional sense code
separates "the scan is finished" from "that command was invalid". Reading it
wrong is expensive in both directions: a missed end-of-data aborts a scan that
had in fact completed, and a missed failure leaves a partial frame looking
whole.

The sense read can itself fail -- often *because* of the condition it was going
to explain -- and the driver still has to decide something. That case has to be
distinguishable from a code, not silently collapsed into one.
"""

import pytest

from conftest import FakeTransport
from rps7200.direct import (
    ASC_END_OF_DATA,
    ASC_NOT_READY,
    SCSI_READ,
    SCSI_REQUEST_SENSE,
    DirectScanner,
    EndOfData,
    ScanReadError,
    Sense,
)
from rps7200.usb_transport import CheckCondition, UsbError


def sense_bytes(key=0x05, code=0x00, qual=0x00):
    """A REQUEST SENSE response as the device lays one out."""
    blob = bytearray(14)
    blob[2] = key
    blob[12] = code
    blob[13] = qual
    return bytes(blob)


class RefusingTransport(FakeTransport):
    """Refuses READ, and answers REQUEST SENSE with whatever it was given."""

    def __init__(self, sense=None, sense_fails=False):
        super().__init__()
        self.sense = sense if sense is not None else sense_bytes()
        self.sense_fails = sense_fails
        self.reads = 0

    def command(self, command, data=None, read_size=0, timeout_ms=0, max_wait_s=60.0):
        if command[0] == SCSI_READ:
            self.reads += 1
            raise CheckCondition(SCSI_READ)
        if command[0] == SCSI_REQUEST_SENSE:
            if self.sense_fails:
                raise UsbError("status read failed: LIBUSB_ERROR_TIMEOUT")
            return self.sense
        return super().command(command, data, read_size, timeout_ms, max_wait_s)


def scanner(transport):
    s = DirectScanner(transport=transport)
    s.verbose = False
    return s


# --- parsing ----------------------------------------------------------------


def test_sense_reads_the_fields_off_the_response():
    s = Sense.parse(sense_bytes(key=0x06, code=0x82, qual=0x00))
    assert (s.key, s.code, s.qualifier) == (0x06, 0x82, 0x00)
    assert s.readable


def test_only_the_low_nibble_of_byte_2_is_the_key():
    """The upper bits carry flags, and reading them as the key mislabels it."""
    blob = bytearray(sense_bytes(code=0x20))
    blob[2] = 0xF5                       # flags set, key 0x05
    assert Sense.parse(bytes(blob)).key == 0x05


def test_a_short_response_is_unreadable_not_a_code():
    s = Sense.parse(b"\x70\x00")
    assert not s.readable
    assert not s.end_of_data and not s.not_ready
    assert "expected 14" in str(s)


def test_an_unreadable_sense_answers_no_to_everything():
    """It must not read as end-of-data, which would truncate a good scan."""
    s = Sense.unreadable("the bus timed out")
    assert not s.readable
    assert not s.end_of_data
    assert not s.not_ready
    assert "the bus timed out" in str(s)


@pytest.mark.parametrize(
    "code, is_eod, is_not_ready",
    [(ASC_END_OF_DATA, True, False), (ASC_NOT_READY, False, True), (0x24, False, False)],
)
def test_the_codes_that_change_what_the_driver_does(code, is_eod, is_not_ready):
    s = Sense.parse(sense_bytes(code=code))
    assert s.end_of_data is is_eod
    assert s.not_ready is is_not_ready


def test_str_names_the_condition_not_just_the_numbers():
    text = str(Sense.parse(sense_bytes(key=0x05, code=0x24)))
    assert "0x24" in text and "invalid field in CDB" in text


# --- read_lines decides on the parsed value, not on a log string ------------


def test_a_read_out_of_lines_is_end_of_data():
    s = scanner(RefusingTransport(sense_bytes(key=0x05, code=ASC_END_OF_DATA)))
    with pytest.raises(EndOfData):
        s.read_lines(4, 100, retries=1)


def test_any_other_refusal_is_a_read_error():
    s = scanner(RefusingTransport(sense_bytes(key=0x05, code=0x24)))
    with pytest.raises(ScanReadError) as exc:
        s.read_lines(4, 100, retries=1)
    assert not isinstance(exc.value, EndOfData)


def test_an_unreadable_sense_is_not_mistaken_for_end_of_data():
    """Reporting a bus failure as a finished scan would truncate the frame."""
    s = scanner(RefusingTransport(sense_fails=True))
    with pytest.raises(ScanReadError) as exc:
        s.read_lines(4, 100, retries=1)
    assert not isinstance(exc.value, EndOfData)
    assert "sense unavailable" in str(exc.value)


def test_the_qualifier_is_not_read_as_the_code():
    """A 0x20 in the qualifier must not end a scan that is still running."""
    s = scanner(RefusingTransport(sense_bytes(key=0x05, code=0x24, qual=0x20)))
    with pytest.raises(ScanReadError) as exc:
        s.read_lines(4, 100, retries=1)
    assert not isinstance(exc.value, EndOfData)


def test_read_lines_retries_before_giving_up():
    """A queued one-shot condition lands on whichever command arrives next."""
    transport = RefusingTransport(sense_bytes(code=0x24))
    with pytest.raises(ScanReadError):
        scanner(transport).read_lines(4, 100, retries=3)
    assert transport.reads == 3


# --- start_scan must not walk away from a start it has begun ----------------


class StartRefusingTransport(FakeTransport):
    """Refuses START SCAN; its REQUEST SENSE fails, as it does when wedged."""

    def __init__(self, sense_fails=True, code=0x82):
        super().__init__()
        self.sense_fails = sense_fails
        self.code = code
        self.starts = 0

    def command(self, command, data=None, read_size=0, timeout_ms=0, max_wait_s=60.0):
        from rps7200.direct import SCSI_SCAN

        if command[0] == SCSI_SCAN:
            self.starts += 1
            raise CheckCondition(SCSI_SCAN)
        if command[0] == SCSI_REQUEST_SENSE:
            if self.sense_fails:
                raise UsbError("status read failed: LIBUSB_ERROR_TIMEOUT")
            return sense_bytes(key=0x06, code=self.code)
        return super().command(command, data, read_size, timeout_ms, max_wait_s)


def test_a_failing_sense_read_does_not_abandon_the_start():
    """A UsbError escaping the handler left the scan half-started, and a start
    abandoned mid-sequence is what needs a power cycle."""
    from rps7200.direct import CalibrationRequired

    transport = StartRefusingTransport(sense_fails=True)
    s = scanner(transport)
    with pytest.raises(CalibrationRequired):
        s.start_scan(retries=2, ready_timeout=5.0)
    assert transport.starts == 2, "the retry must still happen"
    assert s._scanning is False, "the scanner must not be left thinking it started"


def test_the_refusal_is_reported_with_what_the_scanner_said():
    from rps7200.direct import CalibrationRequired

    s = scanner(StartRefusingTransport(sense_fails=False, code=0x82))
    with pytest.raises(CalibrationRequired) as exc:
        s.start_scan(retries=1, ready_timeout=5.0)
    assert "calibration disable not granted" in str(exc.value)
