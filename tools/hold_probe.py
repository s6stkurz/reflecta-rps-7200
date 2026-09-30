#!/usr/bin/env python3
"""Does holding a frame to an approved position work on the real scanner?

    RPS7200_DEBUG=1 uv run python tools/hold_probe.py --dry-run   # the plan, no device
    RPS7200_DEBUG=1 uv run python tools/hold_probe.py             # the real thing

**Ask before running this.** It moves the film, in sub-frame steps, on one
frame. Nothing advances: no SLIDE_NEXT, no SLIDE_PREV, no frame counter
change. Film must be loaded -- the calibration frame is the lower transport,
which the film does not cover, and calibrating an empty one preceded a wedge.

Everything in `_hold_to_approved` is settled offline except one thing, and it
is the thing offline evidence cannot settle: **which way the film physically
moves.** The GUI carries a "reverse the direction" tick precisely because that
was never certain. Stefan says it is normally unticked, so that is what this
assumes, and the loop's own first-move check is what catches it if that is
wrong -- at a cost of one 5-unit move in the wrong direction, which this
script then offers to undo. Every distance here is in units of the `SLIDE`
param, taken and given at the command line as the transport's own unit.

What it does, on ONE frame, about five minutes -- after a calibration's three
or four, unless `--reuse` finds a reference:

  1. a reference prescan -- standing in for the picture approved in the
     contact sheet
  2. hold it at +0.0 units. Should cost nothing: measure, agree, move nothing
  3. hold it at +5.0 units. The real test -- one move, then a look to confirm
  4. measure where the film ended up against the reference, and offer to put
     it back

What to read afterwards, in order of what it settles:

  * **the sign.** Step 3 should end "held" with the film +5 units along. If it
    ends "wrong_way", the sense is inverted and the GUI's reverse tick wants
    setting -- which is a result, not a failure.
  * **confidence**, which offline data puts at 61-96 for a true match against
    4.3-28.8 for two different photographs. The floor the hold loop refuses
    below is `framing.CONFIDENCE_FLOOR` (55 today; this said 40, a floor since
    raised), and the probe prints the one in force. If real confidences come
    back near it, the floor is too close to the noise.
  * **dy**, which should be 0 or 1. The transport moves only in x, so anything
    larger means the correlation found something that is not this frame.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from rps7200.console import use_utf8_stdout
from rps7200.direct import (                                  # noqa: E402
    CheckCondition,
    DirectScanner,
)
from rps7200.framing import (                                 # noqa: E402
    CONFIDENCE_FLOOR,
    measure_shift_mm,
)
from rps7200.protocol import (                                # noqa: E402
    MM_PER_UNIT,
    say_units,
    units,
    units_for_param,
)
from rps7200.session import Approved                          # noqa: E402
from tools import probing                                     # noqa: E402

#: The deliberate offset step 3 asks for, in units. Comfortably above the
#: smallest move the hardware can make (`units_for_param(1)`, 2.84) so a
#: success is unambiguous, and well inside the slack a correctly loaded frame
#: has. It was 0.5 mm, 4.7 units.
PROBE_UNITS = 5.0

#: Refuse to run if the film has travelled further than this in total, in
#: units. The loop has its own per-frame budget; this is the script's own
#: stop, so a surprise cannot walk the film across the aperture while nobody
#: is counting. It was 3.0 mm, 28.4 units.
TOTAL_TRAVEL_LIMIT_UNITS = 28.0

#: The prescans and the two holds, which this file has always put at about
#: five minutes. A calibration first is `probing.calibration_seconds`.
HOLD_S = 5 * 60.0


def show(label: str, held: dict) -> None:
    print(f"\n--- {label} ---")
    print(f"  outcome     {held['outcome']}")
    # The loop's record is in the driver's millimetres; a person reads units.
    print(f"  target      {say_units(held['target_mm'])}")
    final = held.get("final_mm")
    print(f"  ended at    {'not measured' if final is None else say_units(final)}")
    print(f"  moves       {held['moves']}   travelled "
          f"{say_units(held['spent_mm'], signed=False)}")
    print(f"  confidence  {held.get('confidence')}      dy {held.get('dy')}")
    for i, step in enumerate(held.get("history") or []):
        print(f"    look {i}: dx {step.get('dx')} px  dy {step.get('dy')}  "
              f"conf {step.get('confidence')}  {step.get('reason')}")


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=float, default=PROBE_UNITS,
                    help="the deliberate offset to ask for, in units of the "
                         "SLIDE param (default: %(default)s; the smallest "
                         f"move is {units_for_param(1):.2f})")
    ap.add_argument("--resolution", type=int, default=300,
                    help="prescan resolution (default: %(default)s)")
    ap.add_argument("--restore", action="store_true",
                    help="move the film back towards where it started at the end")
    ap.add_argument("--json", default=None)
    probing.add_arguments(ap)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    if abs(args.offset) > TOTAL_TRAVEL_LIMIT_UNITS:
        print(f"refusing: {args.offset} units is past this probe's "
              f"{TOTAL_TRAVEL_LIMIT_UNITS} unit limit", file=sys.stderr)
        return 2
    # The one conversion, where the driver's `Approved` is built.
    offset_mm = args.offset * MM_PER_UNIT

    # The five minutes are the prescans and holds; the calibration before
    # them was left out, and with it the run crosses the eight minutes past
    # which it has to be backgrounded.
    seconds = HOLD_S + probing.calibration_seconds(args)
    advice = (f"about {seconds / 60:.1f} minutes"
              + (" -- background it" if seconds > 8 * 60 else ""))
    if args.dry_run:
        print(f"would, on ONE frame at {args.resolution} dpi, advancing nothing:")
        print("  1. calibrate, unless --reuse finds a reference (3-4 min)")
        print("  2. a reference prescan")
        print("  3. hold at +0.0 units  -- expect 'held', no movement")
        print(f"  4. hold at {args.offset:+.1f} units  -- expect one move, "
              "then 'held'")
        print("  5. measure the net displacement"
              + (" and move back" if args.restore else ""))
        print(f"\n  {advice}. Film must be loaded.")
        return 0
    # On the real run too: the harness kills a foreground command at ten
    # minutes, and a killed read is an abandoned one.
    print(advice)

    out: dict = {"offset_mm": offset_mm, "offset_units": args.offset,
                 "resolution": args.resolution}
    if probing.refuse_unfiled(DirectScanner):
        return 2
    scanner = DirectScanner(verbose=True)
    started = time.monotonic()
    guard = probing.Guard(scanner)
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
        # A prescan is a corrected pass, and a session with no reference
        # refuses one. This said a calibration would run first, which was
        # true until the driver stopped calibrating inside a pass; since
        # then the probe died here.
        probing.ensure_reference(scanner, args)
        print("\n=== the reference prescan ===")
        reference, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        print(f"reference {reference.shape} {reference.dtype}")

        # -- 2. hold at zero: should cost nothing -------------------------
        print("\n=== hold at +0.0 units -- expect 'held', no movement ===")
        now, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        zero = scanner._hold_to_approved(
            0, now, args.resolution,
            Approved(number=1, offset_mm=0.0, reference=reference),
            keep_raw=True)
        zero.pop("prescan", None)
        show("hold at 0.0 units", zero)
        out["zero"] = zero
        if zero["moves"]:
            print("\nNOTE: it moved for a zero offset. That means the film "
                  "shifted between the two prescans above, which is worth "
                  "knowing on its own.")

        # -- 3. the real test ---------------------------------------------
        print(f"\n=== hold at {args.offset:+.1f} units -- the sign test ===")
        now, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
        held = scanner._hold_to_approved(
            0, now, args.resolution,
            Approved(number=1, offset_mm=offset_mm, reference=reference),
            keep_raw=True)
        moved_image = held.pop("prescan", None)
        show(f"hold at {args.offset:+.1f} units", held)
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
            print(f"  the film sits {say_units(net)} from where it started "
                  f"(asked for {args.offset:+.1f})")

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
                  f"the hold loop's floor is {CONFIDENCE_FLOOR:g}")
            out["confidence_range"] = [min(confidences), max(confidences)]

        # -- 5. put it back ------------------------------------------------
        if args.restore and net and abs(units(net)) > TOTAL_TRAVEL_LIMIT_UNITS:
            # The stop this script promises on the whole run. It was checked
            # against --offset alone, so a misread net could send one restore
            # of up to the transport's largest move with nothing counting it.
            print(f"\nnot moving back {say_units(-net)}: past this probe's "
                  f"{TOTAL_TRAVEL_LIMIT_UNITS} unit limit, so the reading is more "
                  "likely wrong than the film that far out", file=sys.stderr)
        elif args.restore and net:
            print(f"\nmoving back {say_units(-net)}")
            scanner.nudge(-net)
            time.sleep(0.4)
            back, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)
            left, _ = measure_shift_mm(reference, back)
            out["after_restore_mm"] = left
            print(f"  now {('unmeasured' if left is None else say_units(left))} "
                  "from where it started")
        elif net:
            print(f"\nThe film is left {say_units(net)} from where it started. "
                  "Re-run with --restore to move it back, or leave it -- "
                  "nothing advanced, so the frame counter is untouched.")

    except CheckCondition as exc:
        print(f"\nstopped on a sense condition: {exc}", file=sys.stderr)
        out["error"] = str(exc)
    except probing.Stopped as exc:
        # Between passes: nothing was abandoned, and nothing more is driven.
        print(f"\n{exc}", file=sys.stderr)
        out["error"] = str(exc)
    except BaseException as exc:                              # noqa: BLE001
        print(f"\nSTOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        out["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        guard.release()
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
            Path(args.json).write_text(json.dumps(out, indent=2, default=str),
                                       encoding="utf-8")
            print(f"written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
