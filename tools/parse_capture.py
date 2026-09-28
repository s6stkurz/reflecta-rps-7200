#!/usr/bin/env python3
"""Reconstruct the SCSI command stream from a CyberView USB capture.

    uv run python tools/parse_capture.py captures/bw.pcapng

Reads the pcapng itself, with no `tshark`. It used to shell out to one, which
meant these could only be read where Wireshark was installed -- and on Windows
tshark is not on PATH even then, so the captures were unreadable on the machine
that recorded them. `rps7200.usbpcap` reads the format directly.

The captures are gitignored because they carry keyboard HID traffic from the
machine that recorded them, so this reads whatever is put in front of it rather
than assuming a path -- and it reads only the scanner's own control transfers.

Why this exists: the obvious extraction finds nothing. Commands do not travel
as bulk payloads, so grepping `usb.capdata` for a 16-byte MODE SELECT returns
empty and the capture looks unreadable.

Commands and their data-out payloads both go to PORT_SCSI_CMD (wValue 0x0085),
one byte per control transfer, so the capture holds them as a flat byte stream:
a six-byte CDB whose bytes 3-4 are a big-endian length, then that many data
bytes, then the next CDB.

Not always that many. The host sends a command's data only once the status
port (0x0084) has answered OK after its CDB; an AGAIN means the CDB is sent
again and no data follows it, and a CHECK means no data at all. Read as a flat
stream, the re-sent CDB was taken for the first one's data and the parse lost
step with the device. `events` keeps the status reads beside the bytes, and
`parse_events` consumes data only after an OK, as `usb_transport` sends it.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.console import use_utf8_stdout                       # noqa: E402
from rps7200.usb_transport import (                               # noqa: E402
    PORT_SCSI_CMD,
    PORT_SCSI_STATUS,
    PRODUCT_ID,
    VENDOR_ID,
    UsbStatus,
)
from rps7200.usbpcap import (                                     # noqa: E402
    CONTROL,
    STAGE_SETUP,
    packets,
    scanner_devices,
)

OPS = {0x00:"TEST_UNIT_READY",0x03:"REQUEST_SENSE",0x08:"READ",0x0A:"WRITE",
       0x0F:"GET_PARAMETERS",0x12:"INQUIRY",0x15:"MODE_SELECT",0x18:"COPY",
       0x1B:"SCAN",0xD1:"SLIDE",0xD2:"SET_SCAN_HEAD",0xD7:"READ_GAIN_OFFSET",
       0xDC:"WRITE_GAIN_OFFSET",0xDD:"READ_STATE",0xE7:"VENDOR_E7",
       0x95:"CAL_INFO_PREPARE"}
SUBS = {0x12:"SET_SCAN_FRAME",0x13:"SET_EXPOSURE",0x14:"SET_HIGHLIGHT_SHADOW",
        0x15:"CAL_INFO",0x16:"CAL_DATA",0x17:"CMD_17"}

#: What `events` yields: a byte written to the command port, or a status read.
BYTE, STATUS = "byte", "status"


def events(path):
    """Every byte written to PORT_SCSI_CMD and every status read, in order.

    Commands and their data-out payloads both go to that one port, a byte per
    control transfer, so the capture holds them as a flat stream. USBPcap puts
    an OUT transfer's data in the same record as its setup packet, right after
    the eight setup bytes -- which is why this reads `payload[8:]` rather than
    looking for a separate data stage. A status read's answer comes back in
    the device's own record after its setup, and is known by that setup.
    """
    raw = Path(path).read_bytes()
    # Plural: the scanner re-enumerates, so a capture spanning a power-on holds
    # it under more than one bus address and taking the first drops the rest.
    devices = scanner_devices(path, VENDOR_ID, PRODUCT_ID)
    if not devices:
        raise SystemExit(
            f"no {VENDOR_ID:#06x}:{PRODUCT_ID:#06x} in {path} -- is this a "
            "capture of the scanner?")
    asked: dict[int, int | None] = {}
    # Named, because `packets` returns nothing from an address nobody named:
    # the keyboard on the same bus never reaches this loop at all.
    for packet in packets(raw, devices):
        if packet.transfer != CONTROL:
            continue
        if packet.from_device:
            # The answer to the last setup this device was sent.
            if asked.pop(packet.device, None) == PORT_SCSI_STATUS \
                    and packet.payload:
                yield STATUS, packet.payload[0]
            continue
        if packet.stage not in (None, STAGE_SETUP) or len(packet.payload) < 8:
            continue
        value = int.from_bytes(packet.payload[2:4], "little")
        asked[packet.device] = value if packet.payload[0] & 0x80 else None
        if value == PORT_SCSI_CMD:
            for byte in packet.payload[8:]:
                yield BYTE, byte


def stream(path):
    """Every byte written to PORT_SCSI_CMD, in order, without the statuses."""
    return bytes(value for kind, value in events(path) if kind == BYTE)


def parse(b):
    i, cmds = 0, []
    while i + 6 <= len(b):
        op = b[i]
        if op not in OPS:                # lost sync; step until it makes sense
            i += 1
            continue
        size = (b[i+3] << 8) | b[i+4]
        cdb = b[i:i+6]
        # A read command's size is what the *device* will send, not data-out.
        writes = op in (0x0A, 0x15, 0xDC, 0xD1)
        n = size if writes else 0
        data = b[i+6:i+6+n] if writes else b""
        cmds.append((op, size, cdb, bytes(data)))
        i += 6 + (n if writes else 0)
    return cmds


#: The opcodes whose size is data the host sends, not data it reads.
WRITES = (0x0A, 0x15, 0xDC, 0xD1)


def parse_events(evts):
    """The commands, taking each one's data-out only once the device said OK.

    Returns the commands, as `parse` does, and how many bytes were set aside:
    CDBs the device answered AGAIN, which the host sends again. A CDB whose
    status the capture did not record is read as `parse` reads it, with its
    data following -- so a capture without status reads parses as before.
    """
    evts = list(evts)
    cmds, dropped, i = [], 0, 0
    while i < len(evts):
        # Six command-port bytes in a row, with no status between them.
        cdb = []
        while i < len(evts) and len(cdb) < 6:
            kind, value = evts[i]
            i += 1
            if kind != BYTE:
                cdb = []
                continue
            if not cdb and value not in OPS:     # lost sync; step on
                continue
            cdb.append(value)
        if len(cdb) < 6:
            break
        op, size = cdb[0], (cdb[3] << 8) | cdb[4]
        status = None
        while i < len(evts) and evts[i][0] == STATUS:
            status = evts[i][1]
            i += 1
            if status != UsbStatus.BUSY:
                break
        if status == UsbStatus.AGAIN:
            # Not executed: the same CDB comes again.
            dropped += 6
            continue
        data = b""
        if op in WRITES and size and status in (None, UsbStatus.OK):
            taken = []
            while i < len(evts) and len(taken) < size:
                kind, value = evts[i]
                i += 1
                if kind == BYTE:
                    taken.append(value)
            data = bytes(taken)
        cmds.append((op, size, bytes(cdb), data))
    return cmds, dropped

if __name__ == "__main__":
    use_utf8_stdout()
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    evts = list(events(sys.argv[1]))
    cmds, _ = parse_events(evts)
    count = sum(1 for kind, _ in evts if kind == BYTE)
    print(f"{count} command bytes -> {len(cmds)} commands\n")
    print("counts:")
    for op, n in Counter(c[0] for c in cmds).most_common():
        print(f"  {OPS.get(op,hex(op)):20} {n}")
