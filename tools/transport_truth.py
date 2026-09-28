#!/usr/bin/env python3
"""Which transport commands actually move the film, judged by the picture.

    RPS7200_DEBUG=1 uv run python tools/transport_truth.py --dry-run
    RPS7200_DEBUG=1 uv run python tools/transport_truth.py

**Ask before running this.** It moves the film, and calibrates first unless
`--reuse` finds a reference (every prescan here is a corrected pass), so the
film must be loaded.

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

It adds no SLIDE_INIT (`10 <param> 00 00`) of its own, although the vendor
sends it 28 times and it is the commonest transport command in the captures.
The param varies 01/13/14/15/16 across files with no explanation, which is a
mechanism command with an unknown-meaning parameter -- the exact shape of the
SET_SCAN_HEAD hazard, where ten and a hundred steps looked like a clean no-op
and a thousand turned the gears. Sending it as a *step* would need someone who
knows what it means, not someone who has seen it in a capture.

It is sent all the same, and this used to say it was not: every pass the
driver takes begins with `SLIDE 10 16 00 00` (`scan()`'s `slide_init_param`),
so each prescan here sends it once, with the vendor's commonest param. What
is left out is any other param, and SLIDE_INIT as a thing to test.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.console import use_utf8_stdout                     # noqa: E402
from rps7200.direct import DirectScanner                        # noqa: E402
from rps7200.framing import (                                   # noqa: E402
    APERTURE_MM,
    CONFIDENCE_FLOOR,
    SEARCH_MM,
)
from rps7200.protocol import (                                  # noqa: E402
    MM_PER_UNIT,
    say_units,
    units_for_param,
)
from rps7200.uniformity import luminance, register              # noqa: E402
from tools import probing                                       # noqa: E402

#: How far the film may end up from where it started, in units of the SLIDE
#: param. Every fine move here is undone, so this is a stop on a surprise
#: rather than a budget to spend. It was 2.5 mm, 23.7 units.
MAX_DRIFT_UNITS = 23.0

#: The fine move each step uses, in units. Comfortably above the smallest
#: move there is (`units_for_param(1)`, 2.84) so a success is unambiguous, and
#: well inside the slack. It was 0.5 mm, 4.7 units.
STEP_UNITS = 5.0


def shift_mm(before: np.ndarray, after: np.ndarray, scale: float) -> tuple:
    """How far the film moved between two prescans, and whether to believe it."""
    # The driver's own reach, not a retyped 106: `measure_shift_mm` searches
    # `SEARCH_MM` at this width's scale, and the floor below is its floor.
    reach = int(SEARCH_MM / max(scale, 1e-9))
    dy, dx, conf = register(luminance(before), luminance(after),
                            max_shift=reach)
    return -float(dx) * scale, int(dy), float(conf)


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolution", type=int, default=300)
    ap.add_argument("--step", type=float, default=STEP_UNITS,
                    help="the fine move, in units of the SLIDE param "
                         "(default: %(default)s; the smallest move is "
                         f"{units_for_param(1):.2f})")
    ap.add_argument("--json", type=Path, default=None)
    probing.add_arguments(ap)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # The one conversion: the driver's `nudge` takes millimetres, and nothing
    # printed here does.
    step_mm = args.step * MM_PER_UNIT
    max_drift_mm = MAX_DRIFT_UNITS * MM_PER_UNIT
    plan = [
        ("fine forward", "nudge", +step_mm),
        ("fine back", "nudge", -step_mm),
        ("fine back again", "nudge", -step_mm),
        ("SLIDE_PREV (retreat)", "retreat", None),
        ("SLIDE_NEXT (advance)", "advance", None),
    ]

    print("what moves the film, judged by the picture and not the counter\n")
    print(f"  a prescan before and after every step, {args.resolution} dpi")
    for i, (label, _kind, mm) in enumerate(plan, 1):
        print(f"  {i}. {label}" + (f"  {say_units(mm)}" if mm is not None else ""))
    print(f"\n  fine moves are undone at the end; hard stop at "
          f"{MAX_DRIFT_UNITS} units from the start")
    print("  SLIDE_INIT only as every pass sends it (10 16 00 00), never as "
          "a step -- see this file's docstring")
    # A prescan a step and two more, and the calibration before them unless
    # --reuse finds the cache -- which the estimate left out.
    seconds = (len(plan) + 2) * 20 + probing.calibration_seconds(args)
    print(f"  roughly {seconds / 60:.1f} minutes"
          + (" -- background it" if seconds > 8 * 60 else ""))

    if args.dry_run:
        print("\ndry run: no device was opened")
        return 0

    out: dict = {"steps": [], "resolution": args.resolution}
    if probing.refuse_unfiled(DirectScanner):
        return 2
    scanner = DirectScanner(verbose=False)
    net = 0.0
    guard = probing.Guard(scanner)
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
        # A prescan is a corrected pass, and a session with no reference
        # refuses one: without this the probe died on its first.
        probing.ensure_reference(scanner, args)

        previous, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        scale = APERTURE_MM / previous.shape[1]
        first = previous
        print(f"baseline prescan {previous.shape}, "
              f"{scale / MM_PER_UNIT:.3f} units/px\n")
        print(f"  {'step':<24} {'asked':>8} {'measured':>9} {'conf':>7} "
              f"{'dy':>3} {'pos':>4}  moved?")

        for label, kind, mm in plan:
            asked = "-" if mm is None else f"{mm / MM_PER_UNIT:+.1f}"
            if kind == "nudge":
                if abs(net + mm) > max_drift_mm:
                    print(f"  {label:<24} refused: would leave the film "
                          f"{say_units(net + mm)} out")
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
            if conf < CONFIDENCE_FLOOR:
                verdict = "picture changed -- a whole frame or more"
            elif abs(moved) < scale:
                verdict = "NO -- under one pixel"
            else:
                verdict = f"yes, {moved/scale:+.1f} px"
            print(f"  {label:<24} {asked:>8} {moved / MM_PER_UNIT:>+9.2f} "
                  f"{conf:>7.1f} "
                  f"{dy:>3} {str(position):>4}  {verdict}")
            out["steps"].append({
                "label": label, "asked_mm": mm, "measured_mm": round(moved, 4),
                "confidence": round(conf, 1), "dy": dy, "position": position,
                "verdict": verdict,
            })
            previous = now

        if abs(net) > 1e-6:
            print(f"\nputting the fine moves back: {say_units(-net)}")
            scanner.nudge(-net)
            time.sleep(0.4)
            back, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
            moved, dy, conf = shift_mm(first, back, scale)
            print(f"  net from the start: {say_units(moved)} "
                  f"(confidence {conf:.1f})")
            out["net_from_start_mm"] = round(moved, 4)
    except probing.Stopped as exc:
        # Between passes, so nothing was abandoned; the fine moves are not
        # undone, since undoing them is more driving the operator stopped.
        print(f"\n{exc}; the film is {say_units(net)} from where it started",
              file=sys.stderr)
        out["stopped"] = str(exc)
    finally:
        guard.release()
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
