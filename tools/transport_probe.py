#!/usr/bin/env python3
"""Measure what the film transport actually does, before a roll depends on it.

    python3 tools/transport_probe.py

Six USB captures of the vendor software contain exactly one film-advance
command -- `SLIDE 04 01 00 01`, sent three times, plus `04 01 00 02` once for a
reason the capture does not explain. Nothing in them says how far one advance
moves the film, whether the transport goes backwards, or what the payload's last
byte is for. `pieusb` is no help either: it declines to implement this transport
at all ("option 'advance' is not implemented yet").

So measure it. Each probe is a 300 dpi prescan, a transport command, and another
prescan; the shift in where the picture sits is what the command was worth. About
25 s per probe, no shading calibration needed -- this is looking at geometry, not
colour.

**Run this on a strip you do not mind handling.** It deliberately sends commands
this scanner has never been given, including ones no capture contains.

Writes probe results to stdout and, with --json, to a file for docs/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.direct import (
    COORD_PER_INCH,
    MM_PER_INCH,
    SLIDE_NEXT,
    SLIDE_PREV,
    DirectScanner,
    frame_contrast,
    registration,
)
from rps7200.usb_transport import CheckCondition, UsbError

#: What each probe sends, and what it is asking.
PROBES = [
    (SLIDE_NEXT, 0x01, 1, "the vendor's advance -- what one frame is worth"),
    (SLIDE_NEXT, 0x01, 1, "again, to see whether the step repeats"),
    (SLIDE_NEXT, 0x01, 2, "last byte 2: a step count, or something else?"),
    (SLIDE_NEXT, 0x01, 0, "last byte 0, as this driver used to send: any movement?"),
    (SLIDE_PREV, 0x01, 1, "does the transport go backwards at all?"),
]


def look(scanner: DirectScanner, dpi: int) -> dict:
    """One prescan, reduced to where the picture sits."""
    image, _ = scanner.prescan(resolution=dpi)
    marks = dict(registration(image))
    marks["contrast"] = round(frame_contrast(image), 4)
    marks["position"] = scanner.position()
    return marks


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dpi", type=int, default=300, help="prescan resolution")
    ap.add_argument("--json", metavar="PATH", help="also write the results here")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="how long to wait for the position to change")
    args = ap.parse_args()

    results = []
    with DirectScanner(verbose=True) as s:
        info = s.inquiry()
        print(f"{info.vendor} {info.product}, firmware {info.firmware}, "
              f"ADF reported: {'yes' if info.has_adf else 'no'}\n")

        before = look(s, args.dpi)
        print(f"start: position {before['position']}, picture at "
              f"x{before['x0']}..{before['x1']}, contrast {before['contrast']}\n")

        for action, param, value, question in PROBES:
            payload = f"{action:02x} {param:02x} 00 {value:02x}"
            print(f"--- SLIDE {payload}  ({question})", flush=True)
            moved, error = None, None
            started = time.monotonic()
            try:
                s.slide(action, param=param, value=value)
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    time.sleep(0.5)
                    now = s.position()
                    if now is not None and now != before["position"]:
                        moved = now
                        break
            except (CheckCondition, UsbError) as exc:
                error = str(exc)
                print(f"    refused: {exc}")

            after = look(s, args.dpi)
            shift = after["x0"] - before["x0"]
            row = {
                "payload": payload,
                "question": question,
                "error": error,
                "position_before": before["position"],
                "position_after": after["position"],
                "position_changed": moved is not None,
                "settle_s": round(time.monotonic() - started, 1),
                "x0_before": before["x0"],
                "x0_after": after["x0"],
                "shift": shift,
                "shift_mm": round(shift * MM_PER_INCH / COORD_PER_INCH, 2),
                "contrast_after": after["contrast"],
            }
            results.append(row)
            print(
                f"    position {before['position']} -> {after['position']}"
                f"{' (never changed)' if moved is None else ''}, "
                f"picture x0 {before['x0']} -> {after['x0']} "
                f"({row['shift_mm']:+.2f} mm), settled in {row['settle_s']}s\n",
                flush=True,
            )
            before = after

    print("=" * 70)
    print(f"{'payload':<14} {'pos':>9} {'shift':>10} {'settle':>7}  question")
    for r in results:
        pos = f"{r['position_before']}->{r['position_after']}"
        print(f"{r['payload']:<14} {pos:>9} {r['shift_mm']:>+9.2f}mm "
              f"{r['settle_s']:>6.1f}s  {r['question']}")
    print(
        "\nWhat this decides: if no payload moves the film by less than a whole\n"
        "frame, registration drift can only be reported, not corrected, and\n"
        "scan_roll should keep saying so rather than pretending to fix it."
    )

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
