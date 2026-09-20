"""The few things only the scanner itself can answer.

Everything else in this suite runs against stored bytes, on purpose: a scan
costs minutes of hardware and an abandoned read costs a power cycle. These
tests exist because the rungs *below* a scan -- the interface claim, endpoint
discovery, the IEEE1284 preamble, one command round trip -- cannot be tested
that way at all, and on Windows they go through WinUSB rather than IOKit and
have never been driven. Until Zadig binds WinUSB to the device they cannot be.

    uv run pytest tests/ -m hardware      # with the scanner attached

They are deselected by default (`addopts` in pyproject.toml). With no scanner
on the bus they skip rather than fail, so running them on a bare machine, or in
CI, is harmless -- and the skip reason says which of "not plugged in" and "held
by another driver" it was, because those need opposite fixes.

**The envelope is INQUIRY and READ STATE, and nothing else.** Both only read.
Nothing here calibrates, scans, meters or moves the transport: that costs the
owner's hardware time and is asked for each time, per CLAUDE.md. A test added
here later has to stay inside that envelope, or it stops being a test and
becomes a scan somebody did not agree to.

Each test carries `@pytest.mark.hardware` of its own rather than inheriting a
module-level `pytestmark`. It is the mark that is grepped for -- the count of
`@pytest.mark.hardware` in `tests/` was zero, which is what these are answering
-- and a test copied out of this file keeps it.
"""

import pytest

from rps7200.direct import DirectScanner
from rps7200.usb_transport import ScannerNotFound, Transport, UsbError

#: READ STATE byte 6, bit 7: a pass is running. The vendor's power-on capture
#: reads 0x0d, 0x15, 0x1d or 0x35 idle and 0x8d, 0x95 or 0x9d while scanning.
STATE_SCANNING = 0x80

#: What INQUIRY reports on this scanner. The offsets follow
#: `sanei_pieusb_cmd_inquiry` and were confirmed on model 0x31, firmware 1.70;
#: another model answers at other offsets, so a mismatch means the fields are
#: being read out of the wrong place, not that a number has changed.
EXPECTED_MODEL = 0x31
EXPECTED_FIRMWARE = "1.70"

#: 10344 x 6888, which `framing.FULL_FRAME` is hard-coded to.
EXPECTED_CCD = (10344, 6888)


@pytest.fixture(scope="module")
def scanner():
    """One open session for the whole module.

    Module-scoped rather than per-test: each open claims interface 0 and each
    close releases it, and doing that once per test would exercise nothing the
    first one does not while giving the bridge eight more chances to be left
    half-open. The session is closed even if a test raises.

    Every reason this cannot run is a skip, never an error. A missing libusb, a
    scanner that is not plugged in, and a scanner held by another driver are
    all "there is nothing to test here" -- and the transport's own message
    names which, including the Zadig instructions on Windows.
    """
    try:
        transport = Transport()
    except (OSError, UsbError) as exc:      # libusb absent, or init refused
        pytest.skip(f"libusb unavailable: {exc}")
    try:
        transport.open()
    except (ScannerNotFound, UsbError) as exc:
        transport.close()
        pytest.skip(str(exc))
    # debug=False: the rule that every scan is filed is about scans, and
    # nothing in this module scans.
    session = DirectScanner(transport=transport, debug=False)
    try:
        yield session
    finally:
        transport.close()


# --------------------------------------------------------------------------
# the open: what Zadig is for
# --------------------------------------------------------------------------


@pytest.mark.hardware
def test_the_interface_is_claimed(scanner):
    """The claim is the step the stock Windows driver makes impossible.

    Reading the private attribute because a successful claim has no public
    witness other than commands working, and the point of this test is to fail
    *before* a command does, so a failure says "the claim" and not "INQUIRY".
    """
    assert scanner.t._handle, "the device is not open"
    assert scanner.t._interface == 0


@pytest.mark.hardware
def test_the_bulk_endpoint_came_from_the_descriptor(scanner):
    """Endpoint discovery walks the config descriptor and can silently fail.

    When the descriptor cannot be read the transport keeps its assumed 0x81 and
    512 and logs it, so a wrong endpoint would only show as reads that return
    nothing, deep inside a scan. These are the two properties any real bulk-in
    endpoint has: the IN bit set, and a legal high- or full-speed packet size.
    """
    assert scanner.t.bulk_in_ep & 0x80, (
        f"endpoint {scanner.t.bulk_in_ep:#04x} is not an IN endpoint"
    )
    assert scanner.t.max_packet_size in (64, 512)


# --------------------------------------------------------------------------
# INQUIRY
# --------------------------------------------------------------------------


@pytest.mark.hardware
def test_inquiry_identifies_this_scanner(scanner):
    """The first full round trip, and the one that proves the decode lands.

    Preamble, six command bytes, a status handshake and a short windowed read.
    If the fields below are right then the payload arrived intact and is being
    read at the offsets it was measured at -- a shifted or truncated reply
    would show up as nonsense in one of them long before it showed up as a bad
    scan.
    """
    info = scanner.inquiry()
    assert (info.vendor, info.product) == ("PIE", "MF Scanner")
    assert info.model == EXPECTED_MODEL
    assert info.max_resolution == 7200
    assert info.preview_resolution == 300
    assert info.has_infrared
    assert info.supports_16bit


@pytest.mark.hardware
def test_the_ccd_is_the_size_every_scan_assumes(scanner):
    """`framing.FULL_FRAME` is hard-coded to this, and no scan ever checks it.

    Scanning deliberately never issues INQUIRY -- neither CyberView nor any
    successful run here does, and asking from inside the scan flow has broken
    reads -- so a device whose CCD is not 10344 x 6888 would be given a scan
    window off the end of its sensor with nothing reporting it. This is the
    only place the constant is ever held against the hardware.
    """
    info = scanner.inquiry()
    assert (info.ccd_width, info.ccd_length) == EXPECTED_CCD


@pytest.mark.hardware
def test_the_firmware_is_the_one_every_measurement_was_made_on(scanner):
    """Everything in docs/ was measured on 1.70, including the offsets above.

    Not a fault in the driver if it fails -- it means the device is not the one
    the measurements describe, and the exposure timer, the byte-14 reversal and
    the blue-in-RGBI ratio all have to be re-measured before they are trusted.
    """
    assert scanner.inquiry().firmware == EXPECTED_FIRMWARE


@pytest.mark.hardware
def test_inquiry_answers_the_same_way_twice(scanner):
    """Two round trips back to back, which one cached answer cannot prove.

    `inquiry()` caches, so the ordinary path sends the command once per
    session; `refresh=True` sends it again. A bridge that answers the first
    command of a session and then loses the IEEE1284 sequence -- the failure
    mode the windowed reader exists for -- passes the test above and fails
    this one.
    """
    first = scanner.inquiry()
    again = scanner.inquiry(refresh=True)
    assert (again.model, again.firmware) == (first.model, first.firmware)
    assert (again.ccd_width, again.ccd_length) == (first.ccd_width,
                                                   first.ccd_length)


# --------------------------------------------------------------------------
# READ STATE
# --------------------------------------------------------------------------


@pytest.mark.hardware
def test_read_state_answers_and_the_device_is_idle(scanner):
    """Thirteen bytes, and byte 6 says no pass is running.

    Two things at once. The length: pieusb asks for 12 and CyberView for 13,
    and a reply shorter than asked for is refused by `_query` rather than
    handed back, so simply getting a `State` proves the 13 is right. And the
    state: nothing in this module starts a scan, so a device reporting one is
    holding a pass abandoned by an earlier session and needs a power cycle
    before anything else is asked of it.
    """
    state = scanner.read_state()
    assert not state.scanning & STATE_SCANNING, (
        f"byte 6 is {state.scanning:#04x}: the device thinks it is scanning, "
        "which means an earlier session abandoned a read. Power-cycle it."
    )
    assert not state.warming_up, (
        "byte 5 says the lamp is still warming -- about 80 s from cold"
    )


@pytest.mark.hardware
def test_the_media_flag_is_one_of_the_two_values_it_has_ever_held(scanner):
    """Byte 8 reads 0 or 1. What it means is not this test's business.

    Measured here with one variable changed -- a strip going in -- byte 8 went
    1 empty to 0 loaded, and all 737 capture responses hold 0. So the value is
    a film reading, and this test deliberately does not assert which: only the
    person at the scanner can see the transport, and a test that demanded film
    would fail for the wrong reason on an empty one.

    Anything other than 0 or 1 is the interesting failure: it means byte 8 is
    not the byte being read, which is how this flag was wrong before -- it used
    to come off byte 6, whose 0x40 read "no film" with a strip demonstrably
    loaded.
    """
    state = scanner.read_state()
    assert state.busy in (0, 1)
    # Byte 6's own 0x40 -- MEDIA_PRESENT -- is deliberately not asserted at
    # all, in either direction: it is clear throughout the vendor's power-on
    # capture and has read clear with film demonstrably loaded, so a set bit is
    # evidence and a clear one is not. There is nothing here to hold it to.


@pytest.mark.hardware
def test_nothing_in_this_module_moves_the_transport(scanner):
    """Byte 2 is the only signal that says the film moved. It must not.

    This is the envelope, asserted rather than promised: every command sent by
    this file reads, so the position after them all is the position before. If
    this ever fails, something in here has started driving the mechanism, and
    the marker that makes these tests opt-in was protecting the wrong thing.
    """
    before = scanner.read_state().position
    scanner.inquiry(refresh=True)
    assert scanner.read_state().position == before
