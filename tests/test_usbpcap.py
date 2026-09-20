"""Reading a USB capture, without tshark and without the captures.

The real ones cannot be committed -- they carry keyboard HID from the machine
that recorded them, which is why `captures/` is gitignored -- so the parser is
driven here by a pcapng built byte by byte in the test. That is the stronger
test anyway: it can contain a keystroke on an interrupt endpoint and assert
that the parser never hands it back.

A second set runs against the real captures when a checkout happens to have
them, and skips when it does not, the way `test_tiff.py` does for `scans/`.
"""

import struct
from pathlib import Path

import pytest

from rps7200.usb_transport import (
    PORT_SCSI_CMD,
    PORT_SCSI_STATUS,
    PRODUCT_ID,
    VENDOR_ID,
    _REQUEST_BUFFER,
    _REQUEST_REGISTER,
    _REQUEST_TYPE_IN,
    _REQUEST_TYPE_OUT,
)
from rps7200.usbpcap import (
    BULK,
    CONTROL,
    INTERRUPT,
    STAGE_SETUP,
    packets,
    scanner_device,
    scanner_devices,
    setups,
)

LINKTYPE_USBPCAP = 249


def usbpcap_record(device, endpoint, transfer, payload, *, function=0x0017,
                   from_device=False, stage=None):
    """One USBPcap pseudo-header and its payload, packed as the tool writes it."""
    header_len = 27 if stage is None else 28
    head = struct.pack(
        "<HQIHBHHBBI",
        header_len,
        0x1234,            # irpId
        0,                 # status
        function,
        1 if from_device else 0,
        0,                 # bus
        device,
        endpoint,
        transfer,
        len(payload),
    )
    if stage is not None:
        head += bytes([stage])
    return head + payload


def pcapng(records):
    """A minimal but real pcapng holding those records."""
    out = bytearray()
    body = struct.pack("<IHHqI", 0x1A2B3C4D, 1, 0, -1, 0)[:20]
    out += struct.pack("<II", 0x0A0D0D0A, 28) + body[:16] + struct.pack("<I", 28)
    idb = struct.pack("<HHI", LINKTYPE_USBPCAP, 0, 0xFFFF)
    out += struct.pack("<II", 1, 12 + len(idb)) + idb + struct.pack("<I", 12 + len(idb))
    for data in records:
        padded = data + b"\x00" * (-len(data) % 4)
        epb = struct.pack("<IIIII", 0, 0, 0, len(data), len(data)) + padded
        out += struct.pack("<II", 6, 12 + len(epb)) + epb \
            + struct.pack("<I", 12 + len(epb))
    return bytes(out)


def setup_packet(request_type, request, value, index=0, length=1, data=b""):
    """The eight setup bytes, with an OUT transfer's data after them.

    Which is how USBPcap records it -- not as a separate data stage -- and is
    the detail that makes the command stream readable at all.
    """
    return struct.pack("<BBHHH", request_type, request, value, index,
                       length) + data


#: A keystroke, on an interrupt endpoint, exactly where a real capture has one.
KEYSTROKE = bytes([0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00])


@pytest.fixture
def capture(tmp_path):
    """A bus with a scanner on address 7 and a keyboard on address 2."""
    path = tmp_path / "bus.pcapng"
    path.write_bytes(pcapng([
        # the keyboard, first, so nothing can pass by only looking at the tail
        usbpcap_record(2, 0x81, INTERRUPT, KEYSTROKE, from_device=True),
        # the scanner saying who it is
        usbpcap_record(7, 0, CONTROL, setup_packet(0x80, 0x06, 0x0100, length=18),
                       stage=STAGE_SETUP),
        usbpcap_record(7, 0, CONTROL,
                       bytes([18, 1, 0, 2, 0xFF, 0xFF, 0xFF, 64])
                       + struct.pack("<HH", VENDOR_ID, PRODUCT_ID),
                       from_device=True, stage=3),
        # a one-byte write to the command port, with its data byte attached
        usbpcap_record(7, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_REGISTER,
                                    PORT_SCSI_CMD, data=b"\x12"),
                       stage=STAGE_SETUP),
        usbpcap_record(7, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_REGISTER,
                                    PORT_SCSI_CMD, data=b"\x00"),
                       stage=STAGE_SETUP),
        # a status read
        usbpcap_record(7, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_IN, _REQUEST_REGISTER,
                                    PORT_SCSI_STATUS),
                       stage=STAGE_SETUP),
        # the eight-byte length handshake
        usbpcap_record(7, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_BUFFER,
                                    0x0082, length=8, data=bytes(8)),
                       stage=STAGE_SETUP),
        # and a bulk payload read
        usbpcap_record(7, 0x81, BULK, b"\xab" * 16, from_device=True),
        # the keyboard again, at the end
        usbpcap_record(2, 0x81, INTERRUPT, KEYSTROKE, from_device=True),
    ]))
    return path


# -- the format --------------------------------------------------------------


def test_the_blocks_and_records_are_read_at_all(capture):
    got = list(packets(capture.read_bytes()))
    assert len(got) == 9
    assert {p.device for p in got} == {2, 7}
    assert [p.transfer for p in got].count(INTERRUPT) == 2


def test_setups_are_decoded_into_what_the_bus_saw(capture):
    got = [s for s in setups(capture, device=7)]
    vendor = [s for s in got if s.request_type in (0x40, 0xC0)]
    assert len(vendor) == 4
    assert (0x40, _REQUEST_REGISTER, PORT_SCSI_CMD, 1) in [
        (s.request_type, s.request, s.value, s.length) for s in vendor]
    assert (0xC0, _REQUEST_REGISTER, PORT_SCSI_STATUS, 1) in [
        (s.request_type, s.request, s.value, s.length) for s in vendor]
    assert (0x40, _REQUEST_BUFFER, 0x0082, 8) in [
        (s.request_type, s.request, s.value, s.length) for s in vendor]


def test_a_truncated_file_stops_rather_than_spinning(capture):
    raw = capture.read_bytes()
    for cut in (0, 4, 9, 30, 44, len(raw) - 7):
        # no exception, no hang: a block length that cannot advance ends it
        list(packets(raw[:cut]))


def test_rubbish_is_not_mistaken_for_a_capture():
    assert list(packets(b"")) == []
    assert list(packets(b"not a pcapng at all")) == []


# -- the line this module draws ----------------------------------------------


def test_a_keystroke_is_never_handed_back(capture):
    """The captures are whole-bus and carry HID from the recording machine.

    So the parser reads control *setup* packets -- USB protocol headers, which
    never hold a keystroke -- and payloads only for a device the caller named.
    This is the assertion that keeps that true.
    """
    for s in setups(capture):
        assert s.device == 7, "a setup packet from another device was returned"

    named = [p for p in packets(capture.read_bytes()) if p.device == 7]
    assert KEYSTROKE not in [p.payload for p in named]

    # and the keyboard's traffic is interrupt, which nothing here asks for
    others = [p for p in packets(capture.read_bytes()) if p.device != 7]
    assert all(p.transfer == INTERRUPT for p in others)


# -- finding the scanner ------------------------------------------------------


def test_the_scanner_is_found_by_its_descriptor(capture):
    assert scanner_devices(capture, VENDOR_ID, PRODUCT_ID) == {7}
    assert scanner_device(capture, VENDOR_ID, PRODUCT_ID) == 7


def test_it_is_also_found_by_the_ports_only_it_has(tmp_path):
    """A capture that missed the enumeration still has to be readable -- and a
    vendor request to a bridge port is this device and nothing else."""
    path = tmp_path / "late.pcapng"
    path.write_bytes(pcapng([
        usbpcap_record(2, 0x81, INTERRUPT, KEYSTROKE, from_device=True),
        usbpcap_record(9, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_REGISTER,
                                    PORT_SCSI_CMD, data=b"\x12"),
                       stage=STAGE_SETUP),
    ]))
    assert scanner_devices(path, VENDOR_ID, PRODUCT_ID) == {9}


def test_every_address_it_used_is_returned_not_just_the_first(tmp_path):
    """It re-enumerates. A capture spanning a power-on holds it under more than
    one address, and taking the first read the power-on capture as empty."""
    path = tmp_path / "reenumerated.pcapng"
    path.write_bytes(pcapng([
        usbpcap_record(5, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_REGISTER,
                                    PORT_SCSI_CMD, data=b"\x12"),
                       stage=STAGE_SETUP),
        usbpcap_record(6, 0, CONTROL,
                       setup_packet(_REQUEST_TYPE_OUT, _REQUEST_REGISTER,
                                    PORT_SCSI_CMD, data=b"\x03"),
                       stage=STAGE_SETUP),
    ]))
    assert scanner_devices(path, VENDOR_ID, PRODUCT_ID) == {5, 6}


def test_a_bus_without_the_scanner_says_so(tmp_path):
    path = tmp_path / "nothing.pcapng"
    path.write_bytes(pcapng([
        usbpcap_record(2, 0x81, INTERRUPT, KEYSTROKE, from_device=True),
    ]))
    assert scanner_devices(path, VENDOR_ID, PRODUCT_ID) == set()
    assert scanner_device(path, VENDOR_ID, PRODUCT_ID) is None


# -- against the real thing, when it is there --------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("name", ["Scan.pcapng", "600_ICE_FILM_STRIP_5.pcapng"])
def test_the_vendor_sends_what_this_driver_sends(name, pytestconfig):
    """The protocol, checked against CyberView rather than against ourselves.

    Every vendor control transfer in these captures is one of the three shapes
    `usb_transport` makes, and there are no others -- 107,000 of them across the
    six. That is the strongest confirmation available that this driver's control
    plane is the vendor's.
    """
    path = Path(pytestconfig.rootpath) / "captures" / name
    if not path.exists():
        pytest.skip(f"captures/{name} is not in this checkout")

    ours = {
        (_REQUEST_TYPE_OUT, _REQUEST_REGISTER, 1),
        (_REQUEST_TYPE_IN, _REQUEST_REGISTER, 1),
        (_REQUEST_TYPE_OUT, _REQUEST_BUFFER, 8),
    }
    devices = scanner_devices(path, VENDOR_ID, PRODUCT_ID)
    assert devices, "the scanner is not in this capture"
    seen = set()
    for s in setups(path):
        if s.device in devices and s.request_type in (0x40, 0xC0):
            seen.add((s.request_type, s.request, s.length))
    assert seen, "no vendor traffic found"
    assert seen <= ours, f"CyberView sends a shape this driver does not: {seen - ours}"


@pytest.mark.slow
def test_the_vendor_never_sends_set_scan_head(pytestconfig):
    """CLAUDE.md rests a hard safety rule on this -- 0xD2 drives a mechanism
    with no feedback and needed a power cycle at 1000 steps. The claim is that
    CyberView never sends it. Here it is, checked rather than repeated."""
    root = Path(pytestconfig.rootpath) / "captures"
    found = sorted(root.glob("*.pcapng")) if root.exists() else []
    if not found:
        pytest.skip("captures/ is not in this checkout")

    from conftest import load_tool
    parse_capture = load_tool("parse_capture")

    total = 0
    for path in found:
        commands = parse_capture.parse(parse_capture.stream(str(path)))
        total += len(commands)
        assert not [c for c in commands if c[0] == 0xD2], f"0xD2 in {path.name}"
    assert total > 3000, f"only {total} commands parsed; the reader is missing traffic"
