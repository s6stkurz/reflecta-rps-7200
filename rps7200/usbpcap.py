"""Read a USBPcap capture, without tshark.

`tools/parse_capture.py` shelled out to `tshark` to get at these, which meant
the captures could only be read on a machine with Wireshark installed -- and on
Windows tshark is not on PATH even when it is. pcapng is a simple block format
and USBPcap's pseudo-header is documented, so this reads them directly.

**These captures are whole-bus, and that matters.** They carry keyboard HID
reports from the machine that recorded them, which is why `captures/` is
gitignored and why CLAUDE.md says to keep it that way. So this module draws one
line and holds it: it reads **control setup packets** -- the eight-byte USB
protocol header that says type, request, value, index and length -- and it
reads payloads only for a device the caller has named. It never returns an
interrupt-endpoint payload, which is where keystrokes live.

That is not a comment, it is what `setups` and `scanner_devices` actually
do, and `tests/test_usbpcap.py` puts a keystroke on a synthetic bus and
asserts it never comes back.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator, NamedTuple

#: pcapng block types.
_SHB = 0x0A0D0D0A
_EPB = 0x00000006
_BYTE_ORDER_MAGIC = 0x1A2B3C4D

#: USBPcap transfer kinds, as its pseudo-header numbers them.
ISOCHRONOUS, INTERRUPT, CONTROL, BULK = 0, 1, 2, 3

#: Control transfer stages, for captures that record them.
STAGE_SETUP = 0

#: The bridge's register ports, as they appear in wValue. A vendor control
#: transfer to one of these identifies this scanner on a bus whatever address
#: it currently holds -- which is the point, because it does not hold one for
#: long. Kept here rather than imported from `usb_transport` so that reading a
#: capture pulls in nothing that knows how to open a device.
BRIDGE_PORTS = frozenset({0x0082, 0x0084, 0x0085, 0x0087, 0x0088})

#: The URB functions worth naming. The rest are printed as hex.
URB_FUNCTIONS = {
    0x0000: "SELECT_CONFIGURATION",
    0x0002: "ABORT_PIPE",
    0x0008: "CONTROL_TRANSFER",
    0x0009: "BULK_OR_INTERRUPT_TRANSFER",
    0x000B: "GET_DESCRIPTOR_FROM_DEVICE",
    0x0017: "VENDOR_DEVICE",
    0x0018: "VENDOR_INTERFACE",
    0x0019: "VENDOR_ENDPOINT",
    0x001A: "CLASS_DEVICE",
    0x001B: "CLASS_INTERFACE",
    0x001E: "SYNC_RESET_PIPE_AND_CLEAR_STALL",
    0x001F: "CONTROL_TRANSFER_EX",
}


class Setup(NamedTuple):
    """One control transfer's eight setup bytes, and who it was for."""

    device: int
    request_type: int      # bmRequestType
    request: int           # bRequest
    value: int             # wValue -- the bridge port, for this scanner
    index: int             # wIndex
    length: int            # wLength


def _blocks(raw: bytes) -> Iterator[tuple[int, bytes, str]]:
    """Every pcapng block, with the byte order the section header declares."""
    endian, at = "<", 0
    while at + 8 <= len(raw):
        kind = struct.unpack_from(endian + "I", raw, at)[0]
        if kind == _SHB:
            # The byte order is four bytes further in, and a file truncated
            # between the two is a real thing -- a capture cut off mid-write.
            if at + 12 > len(raw):
                return
            magic = struct.unpack_from("<I", raw, at + 8)[0]
            endian = "<" if magic == _BYTE_ORDER_MAGIC else ">"
            kind = struct.unpack_from(endian + "I", raw, at)[0]
        length = struct.unpack_from(endian + "I", raw, at + 4)[0]
        # A length that does not advance would spin forever on a truncated file.
        if length < 12 or at + length > len(raw):
            return
        yield kind, raw[at + 8: at + length - 4], endian
        at += length


class Packet(NamedTuple):
    device: int
    endpoint: int
    transfer: int
    function: int
    from_device: bool
    stage: int | None
    payload: bytes


def packets(raw: bytes) -> Iterator[Packet]:
    """Every USBPcap record in the file.

    The pseudo-header is 27 bytes, or 28 when a control transfer's stage is
    recorded. Anything shorter is not one and is skipped rather than guessed at.
    """
    for kind, body, endian in _blocks(raw):
        if kind != _EPB or len(body) < 20:
            continue
        captured = struct.unpack_from(endian + "I", body, 12)[0]
        data = body[20: 20 + captured]
        if len(data) < 27:
            continue
        header_len = struct.unpack_from("<H", data, 0)[0]
        if header_len > len(data) or header_len < 27:
            continue
        yield Packet(
            device=struct.unpack_from("<H", data, 19)[0],
            endpoint=data[21],
            transfer=data[22],
            function=struct.unpack_from("<H", data, 14)[0],
            from_device=bool(data[16] & 1),
            stage=data[27] if header_len >= 28 else None,
            payload=data[header_len:],
        )


def setups(path: Path | str, device: int | None = None) -> Iterator[Setup]:
    """Every control setup packet, optionally for one device only.

    Setup packets are USB protocol headers -- eight bytes of type, request,
    value, index and length. They are not payload, and a keystroke is never in
    one. Interrupt payloads, which is where a keystroke *is*, are not touched
    here at all.
    """
    raw = Path(path).read_bytes()
    for packet in packets(raw):
        if packet.transfer != CONTROL or len(packet.payload) < 8:
            continue
        if packet.stage not in (None, STAGE_SETUP):
            continue
        if device is not None and packet.device != device:
            continue
        kind, request, value, index, length = struct.unpack_from(
            "<BBHHH", packet.payload, 0)
        yield Setup(packet.device, kind, request, value, index, length)


def scanner_devices(path: Path | str, vendor: int, product: int) -> set[int]:
    """Every device address on this bus that was the scanner.

    **Every**, not one. A bus address is not an identity: this scanner
    re-enumerates -- the README describes exactly that happening under SANE,
    where its name moves between one command and the next -- so a capture that
    spans a power-on or a reset holds it under two addresses or more. Returning
    only the first silently drops the rest, which is what made the power-on
    capture read as empty.

    Two ways in, because neither alone is enough. A GET_DESCRIPTOR answer names
    the device but only if the capture caught the enumeration; a vendor request
    to one of this bridge's ports is unmistakable but only appears once the
    protocol starts.
    """
    raw = Path(path).read_bytes()
    found: set[int] = set()
    asked: dict[int, bool] = {}
    for packet in packets(raw):
        if packet.transfer != CONTROL or len(packet.payload) < 8:
            continue
        if packet.stage in (None, STAGE_SETUP):
            kind, request, value, _, _ = struct.unpack_from(
                "<BBHHH", packet.payload, 0)
            asked[packet.device] = (kind == 0x80 and request == 0x06
                                    and value == 0x0100)
            # A vendor request to a bridge port is this device and nothing else.
            if kind in (0x40, 0xC0) and value in BRIDGE_PORTS:
                found.add(packet.device)
            continue
        if asked.get(packet.device) and len(packet.payload) >= 12 \
                and packet.payload[1] == 0x01:
            got_vendor = int.from_bytes(packet.payload[8:10], "little")
            got_product = int.from_bytes(packet.payload[10:12], "little")
            if (got_vendor, got_product) == (vendor, product):
                found.add(packet.device)
    return found


def scanner_device(path: Path | str, vendor: int, product: int) -> int | None:
    """One of them, for a caller that only wants somewhere to start."""
    found = scanner_devices(path, vendor, product)
    return min(found) if found else None
