"""The Windows transport that goes through the scanner's own driver.

None of this needs Windows, and that is deliberate. What can be got wrong here
is arithmetic -- an IOCTL code, a struct offset, the rule mapping a transfer
length to a bRequest -- and arithmetic can be checked anywhere. The parts that
genuinely need the operating system are behind `_api`, which nothing here
calls.

The numbers are not invented. The IOCTL codes are the ones found in
`C:\\Windows\\System32\\MF5000_x64.dll`, CyberView's own scan engine, and the
pipe table is the 68 bytes this scanner's driver actually returned.
"""

import ctypes

import pytest

from rps7200 import usbscan
from rps7200.usb_transport import (
    PORT_SCSI_SIZE,
    PORT_SCSI_STATUS,
    UsbError,
    _REQUEST_BUFFER,
    _REQUEST_REGISTER,
    open_transport,
)


# -- the IOCTL codes --------------------------------------------------------


def test_the_ioctl_codes_are_the_ones_the_vendor_engine_uses():
    """Read out of MF5000_x64.dll: WRITE_REGISTERS x59, READ_REGISTERS x27,
    SEND_USB_REQUEST x0. Those first two are the whole of this transport."""
    assert usbscan.IOCTL_GET_VERSION == 0x80002000
    assert usbscan.IOCTL_READ_REGISTERS == 0x8000200C
    assert usbscan.IOCTL_WRITE_REGISTERS == 0x80002010
    assert usbscan.IOCTL_GET_DEVICE_DESCRIPTOR == 0x80002018
    assert usbscan.IOCTL_RESET_PIPE == 0x8000201C
    assert usbscan.IOCTL_GET_PIPE_CONFIGURATION == 0x80002028
    assert usbscan.IOCTL_SET_TIMEOUT == 0x8000202C


# -- the rule that makes this transport possible at all ---------------------


def test_every_transfer_this_driver_makes_is_one_usbscan_can_express():
    """usbscan.sys picks bRequest from the length and offers no way to say
    otherwise. The whole approach rests on our three transfers agreeing with
    it, so the agreement is asserted rather than assumed."""
    assert usbscan.derived_request(1) == _REQUEST_REGISTER     # 0x0C
    assert usbscan.derived_request(8) == _REQUEST_BUFFER       # 0x04
    # The three, as usb_transport sends them:
    #   _control_out      OUT  0x0C  1 byte
    #   _control_in       IN   0x0C  1 byte
    #   _announce_length  OUT  0x04  8 bytes
    for length, request in ((1, _REQUEST_REGISTER), (8, _REQUEST_BUFFER)):
        assert usbscan.derived_request(length) == request


def test_a_transfer_it_could_not_express_is_refused_not_mis_sent():
    """The failure to avoid is a transfer going out as the wrong request and
    being debugged from the far end."""
    t = usbscan.UsbscanTransport()
    t._expect(_REQUEST_REGISTER, 1)          # fine
    t._expect(_REQUEST_BUFFER, 8)            # also fine
    with pytest.raises(UsbError) as exc:
        t._expect(_REQUEST_REGISTER, 8)      # would go out as 0x04
    assert "IOCTL_SEND_USB_REQUEST" in str(exc.value)


# -- the pipe table ---------------------------------------------------------

#: Exactly what IOCTL_GET_PIPE_CONFIGURATION returned for this scanner.
REAL_PIPES = bytes.fromhex(
    "03000000"
    "000281000200000000020200020000000100830603000000"
    "0000000000000000000000000000000000000000"
    "00000000000000000000000000000000"
)


def test_the_pipe_table_is_decoded_the_way_the_driver_reports_it():
    got = usbscan.pipes(REAL_PIPES)
    assert len(got) == 3
    assert got[0] == {"max_packet_size": 512, "endpoint": 0x81,
                      "interval": 0, "type": 2}
    assert got[1] == {"max_packet_size": 512, "endpoint": 0x02,
                      "interval": 0, "type": 2}
    assert got[2] == {"max_packet_size": 1, "endpoint": 0x83,
                      "interval": 6, "type": 3}


def test_the_endpoint_it_finds_is_the_one_libusb_finds():
    """Both paths have to agree about where the payload comes from, or a scan
    taken on Windows would not be the same scan."""
    bulk_in = [p for p in usbscan.pipes(REAL_PIPES)
               if p["type"] == 2 and p["endpoint"] & 0x80]
    assert len(bulk_in) == 1
    assert bulk_in[0]["endpoint"] == 0x81
    assert bulk_in[0]["max_packet_size"] == 512
    # and it is what this transport starts from before it has asked, which is
    # the same assumption the libusb path makes when the descriptor read fails
    fresh = usbscan.UsbscanTransport()
    assert fresh.bulk_in_ep == 0x81
    assert fresh.max_packet_size == 512


def test_a_short_or_empty_pipe_table_does_not_explode():
    assert usbscan.pipes(b"") == []
    assert usbscan.pipes(b"\x00\x00\x00\x00") == []
    # a count larger than the bytes actually returned
    assert usbscan.pipes(b"\x08\x00\x00\x00" + b"\x00" * 8) == [
        {"max_packet_size": 0, "endpoint": 0, "interval": 0, "type": 0}]


# -- the structs ------------------------------------------------------------


def test_the_io_block_has_the_layout_the_driver_reads():
    """uOffset, uLength, then a pointer that must be aligned, then uIndex."""
    fields = dict((name, getattr(usbscan._IO_BLOCK, name).offset)
                  for name, _ in usbscan._IO_BLOCK._fields_)
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert fields["uOffset"] == 0
    assert fields["uLength"] == 4
    assert fields["pbyData"] == pointer          # 8 on x64, 4 on x86
    assert fields["uIndex"] == pointer + pointer


def test_the_timeout_struct_is_three_shorts_of_seconds():
    assert ctypes.sizeof(usbscan._USBSCAN_TIMEOUT) == 6


def test_the_timeout_ceiling_is_recorded_against_the_infrared_floor():
    """214 s is the driver's documented maximum and the infrared pass has a
    floor of about 212. Two seconds of margin on the one failure that costs a
    power cycle, so the number is pinned here rather than left in prose."""
    assert usbscan.MAX_TIMEOUT_S == 214
    assert usbscan.UsbscanTransport.READ_TIMEOUT_S <= usbscan.MAX_TIMEOUT_S


# -- choosing a transport ---------------------------------------------------


def test_an_unproven_transport_is_not_the_default(monkeypatch):
    """It would be the better answer on Windows if it worked -- nothing to
    replace, CyberView keeps working -- but its register IOCTLs come back
    ERROR_SEM_TIMEOUT and it has never carried a byte. A transport in that
    state is opt-in, whatever its promise, and on whatever platform."""
    monkeypatch.setattr("rps7200.usb_transport.Transport.__init__",
                        lambda self, **kw: None)
    monkeypatch.setattr(usbscan, "available", lambda: True)
    for value in ("", "libusb", "anything else"):
        monkeypatch.setenv("RPS7200_USB_BACKEND", value)
        assert not isinstance(open_transport(), usbscan.UsbscanTransport)
    monkeypatch.delenv("RPS7200_USB_BACKEND")
    assert not isinstance(open_transport(), usbscan.UsbscanTransport)


def test_but_it_can_be_asked_for_deliberately(monkeypatch):
    """Which is how it gets finished, and how the next session drives it."""
    monkeypatch.setenv("RPS7200_USB_BACKEND", "usbscan")
    monkeypatch.setattr(usbscan, "available", lambda: False)
    # unavailable, and chosen anyway: asking for it means asking for its own
    # failure, not a quiet fall back to the other one
    assert isinstance(open_transport(), usbscan.UsbscanTransport)


def test_it_reports_itself_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr(usbscan.sys, "platform", "darwin")
    assert usbscan.available() is False


def test_the_module_imports_and_reasons_anywhere():
    """No Windows needed to check arithmetic. `ctypes.wintypes` will not
    import off Windows at all, which is why this module does not ask it to."""
    assert usbscan.derived_request(1) == 0x0C
    assert usbscan.pipes(REAL_PIPES)[0]["endpoint"] == 0x81


# -- the primitives are the ones Transport is written in terms of -----------


def test_it_overrides_exactly_the_device_primitives():
    """Everything above these -- the IEEE1284 daisy, the SCSI framing, the
    windowed payload read -- is inherited unchanged. If that list ever grows,
    the two transports have stopped sending the same bytes."""
    overridden = {name for name, value in vars(usbscan.UsbscanTransport).items()
                  if callable(value) and not name.startswith("__")}
    device = {"_raw_open", "_discover_endpoints", "close", "_control_out",
              "_control_in", "_announce_length", "_bulk_read_into",
              "clear_halt"}
    assert device <= overridden, device - overridden
    # and nothing from the protocol layer
    protocol = {"command", "_command", "_send_command", "_read_payload",
                "_wait_not_busy", "ieee_command", "reset"}
    assert not (protocol & overridden), protocol & overridden


def test_a_closed_transport_refuses_rather_than_crashing():
    t = usbscan.UsbscanTransport()
    with pytest.raises(UsbError):
        t._registers(usbscan.IOCTL_READ_REGISTERS, PORT_SCSI_STATUS, None, 1)
    with pytest.raises(UsbError):
        t._bulk_read_into(memoryview(bytearray(4)), 1000)
    t.clear_halt()          # a no-op, not an error
    t.close()               # and closing twice is safe
    t.close()


def test_the_announce_payload_is_the_same_eight_bytes_either_way():
    """The length handshake is little-endian at offset 4, and both transports
    have to build it identically or a window would be the wrong size."""
    sent = {}
    t = usbscan.UsbscanTransport()
    t._registers = lambda code, port, data, length: sent.update(
        code=code, port=port, data=data, length=length)
    t._announce_length(0x8000)
    assert sent["code"] == usbscan.IOCTL_WRITE_REGISTERS
    assert sent["port"] == PORT_SCSI_SIZE
    assert sent["length"] == 8
    assert sent["data"] == b"\x00\x00\x00\x00\x00\x80\x00\x00"
