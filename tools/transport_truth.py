#!/usr/bin/env python3
"""Which transport commands actually move the film, judged by the picture.

    RPS7200_DEBUG=1 uv run python tools/transport_truth.py --dry-run
    RPS7200_DEBUG=1 uv run python tools/transport_truth.py

**Ask before running this.** It moves the film.

The reason this exists: `READ_STATE` byte 2 is the only signal the driver has
that a transport command worked, and `advance`/`retreat` both decide they
failed by watching it for a change. That makes the counter a witness to its
own reliability, which is not a thing a witness can be. A command that moves
the film without moving the counter reads as "no movement"; a counter that
ticks without the film moving reads as success.

So every step here takes a prescan and measures the displacement by
correlating against the previous one. The picture is the witness. `register`
is trusted for this: its confidence floor was fitted over 3850 real pairs, its
failure mode is a smooth collapse rather than a confident lie, and on this
machine two passes of one frame with nothing moved register at dx=0 on
sixteen of sixteen frames.

What it tries, in increasing order of what it would cost to be wrong about:

  1. a fine move forward -- known good, and the control
  2. a fine move back -- the direction that has never been verified by pixels
  3. a second fine move back -- backlash swallows two to three commands after
     a direction change, so one failure proves nothing
  4. SLIDE_PREV -- which this driver uses for `retreat` and which the vendor
     sends ZERO times in 3,987 commands across six captures
  5. SLIDE_NEXT -- to see whether a refusal to advance is the film or the
     counter

It does NOT send SLIDE_INIT (`10 <param> 00 00`), although the vendor sends it
28 times and it is the commonest transport command in the captures. The param
varies 01/13/14/15/16 across files with no explanation, which is a mechanism
command with an unknown-meaning parameter -- the exact shape of the
SET_SCAN_HEAD hazard, where ten and a hundred steps looked like a clean no-op
and a thousand turned the gears. Sending it needs someone who knows what it
means, not someone who has seen it in a capture.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.console import use_utf8_stdout                     # noqa: E402
from rps7200.direct import DirectScanner                        # noqa: E402
from rps7200.framing import APERTURE_MM                         # noqa: E402
from rps7200.uniformity import luminance, register              # noqa: E402

#: How far the film may end up from where it started. Every fine move here is
#: undone, so this is a stop on a surprise rather than a budget to spend.
MAX_DRIFT_MM = 2.5

#: The fine move each step uses. Comfortably above the 0.2719 mm minimum so a
#: success is unambiguous, and well inside the slack.
STEP_MM = 0.5


def shift_mm(before: np.ndarray, after: np.ndarray, scale: float) -> tuple:
    """How far the film moved between two prescans, and whether to believe it."""
    dy, dx, conf = register(luminance(before), luminance(after), max_shift=106)
    return -float(dx) * scale, int(dy), float(conf)


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolution", type=int, default=300)
    ap.add_argument("--step", type=float, default=STEP_MM)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = [
        ("fine forward", "nudge", +args.step),
        ("fine back", "nudge", -args.step),
        ("fine back again", "nudge", -args.step),
        ("SLIDE_PREV (retreat)", "retreat", None),
        ("SLIDE_NEXT (advance)", "advance", None),
    ]

    print("what moves the film, judged by the picture and not the counter\n")
    print(f"  a prescan before and after every step, {args.resolution} dpi")
    for i, (label, _kind, mm) in enumerate(plan, 1):
        print(f"  {i}. {label}" + (f"  {mm:+.3f} mm" if mm is not None else ""))
    print(f"\n  fine moves are undone at the end; hard stop at "
          f"{MAX_DRIFT_MM} mm from the start")
    print("  NOT sent: SLIDE_INIT -- unknown parameter, see this file's "
          "docstring")
    print(f"  roughly {(len(plan)+2) * 20 / 60:.1f} minutes")

    if args.dry_run:
        print("\ndry run: no device was opened")
        return 0
    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1", file=sys.stderr)
        return 2

    out: dict = {"steps": [], "resolution": args.resolution}
    scanner = DirectScanner(verbose=False)
    net = 0.0
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"\nbefore: position {state.position}, "
              f"flags {state.scanning:#04x}")
        if state.scanning & 0x80:
            print("refusing: reports a scan in progress", file=sys.stderr)
            return 2
        scanner.session_start()
        scanner.wait_warm()

        previous, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        scale = APERTURE_MM / previous.shape[1]
        first = previous
        print(f"baseline prescan {previous.shape}, {scale:.5f} mm/px\n")
        print(f"  {'step':<24} {'asked':>8} {'measured':>9} {'conf':>7} "
              f"{'dy':>3} {'pos':>4}  moved?")

        for label, kind, mm in plan:
            asked = "-" if mm is None else f"{mm:+.3f}"
            if kind == "nudge":
                if abs(net + mm) > MAX_DRIFT_MM:
                    print(f"  {label:<24} refused: would leave the film "
                          f"{net+mm:+.2f} mm out")
                    continue
                scanner.nudge(mm)
                net += mm
                time.sleep(0.4)
            elif kind == "retreat":
                scanner.retreat(timeout=20.0)
            else:
                scanner.advance(timeout=20.0)

            now, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
            moved, dy, conf = shift_mm(previous, now, scale)
            position = scanner.position()
            # Under a pixel and a confident match means it did not move. A
            # weak match means the picture changed too much to compare, which
            # for a whole-frame command is itself the answer.
            if conf < 55:
                verdict = "picture changed -- a whole frame or more"
            elif abs(moved) < scale:
                verdict = "NO -- under one pixel"
            else:
                verdict = f"yes, {moved/scale:+.1f} px"
            print(f"  {label:<24} {asked:>8} {moved:>+9.4f} {conf:>7.1f} "
                  f"{dy:>3} {str(position):>4}  {verdict}")
            out["steps"].append({
                "label": label, "asked_mm": mm, "measured_mm": round(moved, 4),
                "confidence": round(conf, 1), "dy": dy, "position": position,
                "verdict": verdict,
            })
            previous = now

        if abs(net) > 1e-6:
            print(f"\nputting the fine moves back: {-net:+.3f} mm")
            scanner.nudge(-net)
            time.sleep(0.4)
            back, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
            moved, dy, conf = shift_mm(first, back, scale)
            print(f"  net from the start: {moved:+.4f} mm "
                  f"(confidence {conf:.1f})")
            out["net_from_start_mm"] = round(moved, 4)
    finally:
        try:
            scanner.close()
        except Exception:                                   # noqa: BLE001
            pass
        if args.json:
            args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
            print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
