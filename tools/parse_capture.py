#!/usr/bin/env python3
"""Reconstruct the SCSI command stream from a CyberView USB capture.

    python3 tools/parse_capture.py captures/bw.pcapng

Needs `tshark`. The captures themselves are gitignored -- they carry keyboard
HID traffic from the machine that recorded them -- so this reads whatever is
put in front of it rather than assuming a path.

Why this exists: the obvious extraction finds nothing. Commands do not travel
as bulk payloads, so grepping `usb.capdata` for a 16-byte MODE SELECT returns
empty and the capture looks unreadable.

Commands and their data-out payloads both go to PORT_SCSI_CMD (wValue 0x0085),
one byte per control transfer, so the capture holds them as a flat byte stream:
a six-byte CDB whose bytes 3-4 are a big-endian length, then that many data
bytes, then the next CDB.
"""
import subprocess
import sys
from collections import Counter

OPS = {0x00:"TEST_UNIT_READY",0x03:"REQUEST_SENSE",0x08:"READ",0x0A:"WRITE",
       0x0F:"GET_PARAMETERS",0x12:"INQUIRY",0x15:"MODE_SELECT",0x18:"COPY",
       0x1B:"SCAN",0xD1:"SLIDE",0xD2:"SET_SCAN_HEAD",0xD7:"READ_GAIN_OFFSET",
       0xDC:"WRITE_GAIN_OFFSET",0xDD:"READ_STATE",0xE7:"VENDOR_E7",
       0x95:"CAL_INFO_PREPARE"}
SUBS = {0x12:"SET_SCAN_FRAME",0x13:"SET_EXPOSURE",0x14:"SET_HIGHLIGHT_SHADOW",
        0x15:"CAL_INFO",0x16:"CAL_DATA",0x17:"CMD_17"}

def stream(path):
    out = subprocess.run(
        ["tshark","-r",path,"-Y","usb.setup.wValue == 0x0085",
         "-T","fields","-e","usb.data_fragment"],
        capture_output=True, text=True).stdout
    b = bytearray()
    for line in out.splitlines():
        h = line.replace(":","").strip()
        if len(h) == 2:
            b.append(int(h,16))
    return bytes(b)

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

if __name__ == "__main__":
    b = stream(sys.argv[1])
    cmds = parse(b)
    print(f"{len(b)} command bytes -> {len(cmds)} commands\n")
    print("counts:")
    for op, n in Counter(c[0] for c in cmds).most_common():
        print(f"  {OPS.get(op,hex(op)):20} {n}")
