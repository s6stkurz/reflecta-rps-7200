"""One SCSI command through the bridge: the retry protocol `_command` runs.

Every command the driver sends goes through `Transport._command`, which reads
one status byte after the command block and decides: AGAIN re-sends the
command, BUSY polls the status port *without* re-sending, OK sends any data
out, READ fetches the payload and then drains the BUSY that follows it, CHECK
hands the caller a sense to read. Getting one of those wrong -- a command
re-issued while the device is still working on it, a BUSY left undrained so
the next transfer stalls, a refusal after data-out taken for success -- is
the kind of fault that wedges this scanner, and none of it had an offline
test: every other test replaces `command()` whole.

`conftest.FakeUsb` stands in for libusb, so `Transport` runs unchanged down
to its ctypes buffers and only the bridge's answers are scripted.
"""

import pytest

from conftest import FakeUsb
from rps7200 import usb_transport as u
from rps7200.usb_transport import CheckCondition, UsbError, UsbStatus

INQUIRY = bytes([0x12, 0, 0, 0, 5, 0])
WRITE = bytes([0x0A, 0, 0, 0, 3, 0])


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A clock that moves only when the code under test sleeps."""
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(u.time, "sleep", sleep)
    monkeypatch.setattr(u.time, "monotonic", lambda: now[0])
    return now


def issued(command):
    """What the bridge sees for one issue of ``command``."""
    return ["select", *(("byte", b) for b in command)]


def test_an_accepted_command_is_sent_once_and_answers_nothing(monkeypatch):
    usb = FakeUsb([UsbStatus.OK])
    assert usb.transport(monkeypatch).command(bytes(6)) == b""
    assert usb.sequence() == [*issued(bytes(6)), ("status", UsbStatus.OK)]


def test_every_transfer_is_one_of_the_three_shapes_the_vendor_uses(monkeypatch):
    """`test_usbpcap` holds CyberView's captures to these three -- one-byte
    register writes and reads, and the eight-byte length handshake -- and
    finds no other. Here the same is held of this driver, across a command
    with data out and one with data in."""
    usb = FakeUsb([UsbStatus.OK, UsbStatus.OK, UsbStatus.READ, UsbStatus.OK],
                  bulk=bytes(range(5)))
    t = usb.transport(monkeypatch)
    t.command(WRITE, data=b"abc")
    t.command(INQUIRY, read_size=5)
    assert usb.shapes() == {
        (u._REQUEST_TYPE_OUT, u._REQUEST_REGISTER, 1),
        (u._REQUEST_TYPE_IN, u._REQUEST_REGISTER, 1),
        (u._REQUEST_TYPE_OUT, u._REQUEST_BUFFER, 8),
    }
    ports = {port for _d, _r, port, _l, _p in usb.transfers}
    assert ports <= {u.PORT_SCSI_SIZE, u.PORT_SCSI_STATUS, u.PORT_SCSI_CMD,
                     u.PORT_PAR_CTRL, u.PORT_PAR_DATA}


def test_again_sends_the_command_again_after_a_pause(monkeypatch, clock):
    usb = FakeUsb([UsbStatus.AGAIN, UsbStatus.OK])
    usb.transport(monkeypatch).command(INQUIRY)
    assert usb.sequence() == [*issued(INQUIRY), ("status", UsbStatus.AGAIN),
                              *issued(INQUIRY), ("status", UsbStatus.OK)]
    assert clock[0] >= 1.0


def test_busy_is_waited_out_on_the_status_port_never_by_resending(monkeypatch):
    """The command is already in the device. Sending it again while it works
    is a second START SCAN into a scan in progress."""
    usb = FakeUsb([UsbStatus.BUSY, UsbStatus.BUSY, UsbStatus.BUSY,
                   UsbStatus.OK])
    usb.transport(monkeypatch).command(INQUIRY)
    assert usb.sequence().count("select") == 1
    assert usb.sequence() == [*issued(INQUIRY)] + [
        ("status", s) for s in (UsbStatus.BUSY,) * 3 + (UsbStatus.OK,)]


def test_busy_then_again_is_a_resend(monkeypatch):
    usb = FakeUsb([UsbStatus.BUSY, UsbStatus.AGAIN, UsbStatus.OK])
    usb.transport(monkeypatch).command(INQUIRY)
    assert usb.sequence().count("select") == 2


def test_busy_then_data_reads_the_payload_after_one_issue(monkeypatch):
    usb = FakeUsb([UsbStatus.BUSY, UsbStatus.READ, UsbStatus.OK],
                  bulk=b"\x01\x02\x03\x04\x05")
    got = usb.transport(monkeypatch).command(INQUIRY, read_size=5)
    assert got == b"\x01\x02\x03\x04\x05"
    assert usb.sequence().count("select") == 1
    assert ("length", 5) in usb.sequence()


def test_the_busy_after_a_payload_is_drained_before_returning(monkeypatch):
    """The device reports BUSY once it has handed the data over, until it can
    take the next command. Returning before that clears is what stalls the
    following transfer -- so every scripted status must have been read."""
    usb = FakeUsb([UsbStatus.READ, UsbStatus.BUSY, UsbStatus.BUSY,
                   UsbStatus.OK], bulk=bytes(5))
    usb.transport(monkeypatch).command(INQUIRY, read_size=5)
    assert usb.statuses == [], "returned with the device still busy"
    assert usb.sequence()[-3:] == [("status", UsbStatus.BUSY),
                                   ("status", UsbStatus.BUSY),
                                   ("status", UsbStatus.OK)]


def test_a_check_after_the_payload_is_a_check_condition(monkeypatch):
    usb = FakeUsb([UsbStatus.READ, UsbStatus.BUSY, UsbStatus.CHECK],
                  bulk=bytes(5))
    with pytest.raises(CheckCondition):
        usb.transport(monkeypatch).command(INQUIRY, read_size=5)
    assert not usb.bulk, "the payload was left in the device"


def test_data_out_goes_after_the_command_block_one_byte_at_a_time(monkeypatch):
    usb = FakeUsb([UsbStatus.OK, UsbStatus.BUSY, UsbStatus.OK])
    assert usb.transport(monkeypatch).command(WRITE, data=b"xyz") == b""
    assert usb.sequence() == [*issued(WRITE), ("status", UsbStatus.OK),
                              *(("byte", b) for b in b"xyz"),
                              ("status", UsbStatus.BUSY),
                              ("status", UsbStatus.OK)]


def test_a_refusal_after_data_out_is_a_check_condition_not_success(monkeypatch):
    usb = FakeUsb([UsbStatus.OK, UsbStatus.CHECK])
    with pytest.raises(CheckCondition):
        usb.transport(monkeypatch).command(WRITE, data=b"xyz")


def test_data_out_is_not_sent_to_a_command_the_device_refused(monkeypatch):
    usb = FakeUsb([UsbStatus.CHECK])
    with pytest.raises(CheckCondition):
        usb.transport(monkeypatch).command(WRITE, data=b"xyz")
    assert usb.sequence() == [*issued(WRITE), ("status", UsbStatus.CHECK)]


def test_any_other_answer_to_data_out_is_an_error(monkeypatch):
    usb = FakeUsb([UsbStatus.OK, UsbStatus.FAIL])
    with pytest.raises(UsbError) as refused:
        usb.transport(monkeypatch).command(WRITE, data=b"xyz")
    assert not isinstance(refused.value, CheckCondition)


def test_a_device_that_stays_busy_is_given_up_on_without_a_resend(monkeypatch):
    usb = FakeUsb([UsbStatus.BUSY] * 10_000)
    with pytest.raises(UsbError, match="stayed busy"):
        usb.transport(monkeypatch).command(INQUIRY, max_wait_s=0.0)
    assert usb.sequence().count("select") == 1


def test_a_device_that_keeps_asking_again_is_given_up_on(monkeypatch, clock):
    usb = FakeUsb([UsbStatus.AGAIN] * 100)
    with pytest.raises(UsbError, match="kept asking for retry"):
        usb.transport(monkeypatch).command(INQUIRY, max_wait_s=5.0)
    # One issue a second, for as long as it was allowed -- not a spin.
    assert 5 <= usb.sequence().count("select") <= 7


def test_data_with_nowhere_to_put_it_is_an_error(monkeypatch):
    usb = FakeUsb([UsbStatus.READ])
    with pytest.raises(UsbError, match="no read size"):
        usb.transport(monkeypatch).command(INQUIRY)
    assert usb.bulk_reads == [], "read a payload nobody asked for"


def test_an_unknown_status_is_an_error(monkeypatch):
    usb = FakeUsb([0x42])
    with pytest.raises(UsbError, match="unexpected status"):
        usb.transport(monkeypatch).command(INQUIRY)


def test_a_failed_command_never_resets_the_bridge(monkeypatch):
    """The vendor never sends IEEE1284 RESET, and sending one after a fault
    is what left the next session unable to talk to the scanner at all."""
    for statuses in ([UsbStatus.CHECK], [0x42], [UsbStatus.OK, UsbStatus.FAIL]):
        usb = FakeUsb(statuses)
        with pytest.raises(UsbError):
            usb.transport(monkeypatch).command(WRITE, data=b"x")
        written = [p[0] for d, _r, port, _l, p in usb.transfers
                   if d == "out" and port == u.PORT_PAR_DATA]
        assert u.IEEE1284_RESET not in written, statuses


def test_a_closed_transport_sends_nothing(monkeypatch):
    usb = FakeUsb([UsbStatus.OK])
    t = usb.transport(monkeypatch)
    t.close()
    with pytest.raises(UsbError, match="not open"):
        t.command(INQUIRY)
    assert usb.transfers == []
