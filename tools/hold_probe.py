#!/usr/bin/env python3
"""Does holding a frame to an approved position work on the real scanner?

    RPS7200_DEBUG=1 python3 tools/hold_probe.py --dry-run   # the plan, no device
    RPS7200_DEBUG=1 python3 tools/hold_probe.py             # the real thing

**Ask before running this.** It moves the film, in sub-frame steps, on one
frame. Nothing advances: no SLIDE_NEXT, no SLIDE_PREV, no frame counter
change. Film must be loaded -- the calibration frame is the lower transport,
which the film does not cover, and calibrating an empty one preceded a wedge.

Everything in `_hold_to_approved` is settled offline except one thing, and it
is the thing offline evidence cannot settle: **which way the film physically
moves.** The GUI carries a "reverse the direction" tick precisely because that
was never certain. Stefan says it is normally unticked, so that is what this
assumes, and the loop's own first-move check is what catches it if that is
wrong -- at a cost of one 0.5 mm move in the wrong direction, which this
script then offers to undo.

What it does, on ONE frame, about five minutes:

  1. a reference prescan -- standing in for the picture approved in the
     contact sheet
  2. hold it at +0.0 mm. Should cost nothing: measure, agree, move nothing
  3. hold it at +0.5 mm. The real test -- one move, then a look to confirm
  4. measure where the film ended up against the reference, and offer to put
     it back

What to read afterwards, in order of what it settles:

  * **the sign.** Step 3 should end "held" with the film +0.5 mm along. If it
    ends "wrong_way", the sense is inverted and the GUI's reverse tick wants
    setting -- which is a result, not a failure.
  * **confidence**, which offline data puts at 61-96 for a true match against
    4.3-28.8 for two different photographs. A floor of 40 sits in that gap. If
    real confidences come back near 40, the floor is too close to the noise.
  * **dy**, which should be 0 or 1. The transport moves only in x, so anything
    larger means the correlation found something that is not this frame.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from rps7200.direct import (                                  # noqa: E402
    CheckCondition,
    DirectScanner,
)
from rps7200.framing import measure_shift_mm                  # noqa: E402
from rps7200.session import Approved                          # noqa: E402

#: The deliberate offset step 3 asks for. Comfortably above the smallest move
#: the hardware can make (0.272 mm) so a success is unambiguous, and well
#: inside the half-millimetre of slack a correctly loaded frame has.
PROBE_MM = 0.5

#: Refuse to run if the film has travelled further than this in total. The
#: loop has its own per-frame budget; this is the script's own stop, so a
#: surprise cannot walk the film across the aperture while nobody is counting.
TOTAL_TRAVEL_LIMIT_MM = 3.0


def show(label: str, held: dict) -> None:
    print(f"\n--- {label} ---")
    print(f"  outcome     {held['outcome']}")
    print(f"  target      {held['target_mm']:+.3f} mm")
    final = held.get("final_mm")
    print(f"  ended at    {'not measured' if final is None else f'{final:+.3f} mm'}")
    print(f"  moves       {held['moves']}   travelled {held['spent_mm']:.3f} mm")
    print(f"  confidence  {held.get('confidence')}      dy {held.get('dy')}")
    for i, step in enumerate(held.get("history") or []):
        print(f"    look {i}: dx {step.get('dx')} px  dy {step.get('dy')}  "
              f"conf {step.get('confidence')}  {step.get('reason')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=float, default=PROBE_MM,
                    help="the deliberate offset to ask for (default: %(default)s mm)")
    ap.add_argument("--resolution", type=int, default=300,
                    help="prescan resolution (default: %(default)s)")
    ap.add_argument("--restore", action="store_true",
                    help="move the film back towards where it started at the end")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    if abs(args.offset) > TOTAL_TRAVEL_LIMIT_MM:
        print(f"refusing: {args.offset} mm is past this probe's "
              f"{TOTAL_TRAVEL_LIMIT_MM} mm limit", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"would, on ONE frame at {args.resolution} dpi, advancing nothing:")
        print("  1. calibrate if this session has no reference (~2 min)")
        print("  2. a reference prescan")
        print("  3. hold at +0.000 mm  -- expect 'held', no movement")
        print(f"  4. hold at {args.offset:+.3f} mm  -- expect one move, then 'held'")
        print("  5. measure the net displacement"
              + (" and move back" if args.restore else ""))
        print("\n  about 5 minutes. Film must be loaded.")
        return 0

    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1: a probe that files "
              "nothing cannot be re-analysed, and this one is worth keeping",
              file=sys.stderr)
        return 2

    out: dict = {"offset_mm": args.offset, "resolution": args.resolution}
    scanner = DirectScanner(verbose=True)
    started = time.monotonic()
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"state: scanning={state.scanning:#04x} "
              f"media_loaded={state.media_loaded} position={state.position}")
        out["state_before"] = {"media_loaded": bool(state.media_loaded),
                               "position": int(state.position)}
        scanner.session_start()
        scanner.wait_warm()

        # -- 1. the reference, standing in for the contact sheet ----------
        print("\n=== the reference prescan ===")
        print("(a calibration runs first if this session has none -- ~2 min)")
        reference, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        print(f"reference {reference.shape} {reference.dtype}")

        # -- 2. hold at zero: should cost nothing -------------------------
        print("\n=== hold at +0.000 mm -- expect 'held', no movement ===")
        now, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        zero = scanner._hold_to_approved(
            0, now, args.resolution,
            Approved(number=1, offset_mm=0.0, reference=reference),
            keep_raw=True)
        zero.pop("prescan", None)
        show("hold at 0.0 mm", zero)
        out["zero"] = zero
        if zero["moves"]:
            print("\nNOTE: it moved for a zero offset. That means the film "
                  "shifted between the two prescans above, which is worth "
                  "knowing on its own.")

        # -- 3. the real test ---------------------------------------------
        print(f"\n=== hold at {args.offset:+.3f} mm -- the sign test ===")
        now, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        held = scanner._hold_to_approved(
            0, now, args.resolution,
            Approved(number=1, offset_mm=args.offset, reference=reference),
            keep_raw=True)
        moved_image = held.pop("prescan", None)
        show(f"hold at {args.offset:+.3f} mm", held)
        out["held"] = held

        # -- 4. where did it actually end up ------------------------------
        print("\n=== net displacement from the reference ===")
        final_image = moved_image if moved_image is not None else now
        net, detail = measure_shift_mm(reference, final_image)
        out["net_mm"] = net
        out["net_detail"] = detail
        if net is None:
            print(f"  could not measure: {detail.get('reason')}")
        else:
            print(f"  the film sits {net:+.3f} mm from where it started "
                  f"(asked for {args.offset:+.3f})")

        # -- the verdict ---------------------------------------------------
        print("\n" + "=" * 66)
        if held["outcome"] == "wrong_way":
            print("THE SIGN IS INVERTED. Not a failure -- the loop caught it on "
                  "its first move and stopped, which is what it is for.")
            print("Set 'reverse the direction' in the window before using "
                  "approved positions.")
            out["verdict"] = "inverted"
        elif held["outcome"] == "held":
            print("THE SIGN IS RIGHT, and the frame reached the position asked "
                  "for.")
            out["verdict"] = "ok"
        else:
            print(f"Inconclusive: {held['outcome']}. Read the per-look lines "
                  "above -- a low confidence means the match, not the move.")
            out["verdict"] = held["outcome"]

        confidences = [s.get("confidence") for s in
                       (zero.get("history") or []) + (held.get("history") or [])
                       if s.get("confidence") is not None]
        if confidences:
            print(f"confidence ran {min(confidences):.1f} to "
                  f"{max(confidences):.1f}; offline data predicted 61-96, and "
                  f"the floor is 40")
            out["confidence_range"] = [min(confidences), max(confidences)]

        # -- 5. put it back ------------------------------------------------
        if args.restore and net:
            print(f"\nmoving back {-net:+.3f} mm")
            scanner.nudge(-net)
            time.sleep(0.4)
            back, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
            left, _ = measure_shift_mm(reference, back)
            out["after_restore_mm"] = left
            print(f"  now {('unmeasured' if left is None else f'{left:+.3f} mm')} "
                  "from where it started")
        elif net:
            print(f"\nThe film is left {net:+.3f} mm from where it started. "
                  "Re-run with --restore to move it back, or leave it -- "
                  "nothing advanced, so the frame counter is untouched.")

    except CheckCondition as exc:
        print(f"\nstopped on a sense condition: {exc}", file=sys.stderr)
        out["error"] = str(exc)
    except BaseException as exc:                              # noqa: BLE001
        print(f"\nSTOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        out["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        out["duration_s"] = round(time.monotonic() - started, 1)
        try:
            state = scanner.read_state()
            print(f"\nstate after: scanning={state.scanning:#04x} "
                  f"position={state.position}")
        except BaseException as exc:                          # noqa: BLE001
            print(f"state after: unreadable ({exc})", file=sys.stderr)
        try:
            scanner.close()
            print("device closed; filing what was captured")
        except BaseException as exc:                          # noqa: BLE001
            print(f"close: {exc}", file=sys.stderr)
        if args.json:
            Path(args.json).write_text(json.dumps(out, indent=2, default=str))
            print(f"written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
