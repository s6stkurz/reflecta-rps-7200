#!/usr/bin/env python3
"""Is the gain field in SET GAIN OFFSET analog, or a digital multiplier?

    RPS7200_DEBUG=1 python3 tools/gain_probe.py

See `docs/analog-gain-plan.md` for why this is worth two minutes and why it is
not the SET_SCAN_HEAD situation. In one line: blue cannot reach its target in
plain RGB because the 16-bit exposure timer runs out, and the gain field is the
only lever left -- but it is worth having only if it amplifies *before* the ADC.
If it is digital, the noise rises with the signal and it buys nothing.

**Ask before running this.** It drives a register the vendor never changes.
Nothing moves: the transport is untouched and no scan frame is advanced.

What it does, at a fixed exposure throughout:

  * meters once, then deliberately backs blue off to ~35% of full scale, so the
    ladder has somewhere to go before it clips
  * two passes at the device's own gain of 21, which is what `noise_split`
    needs -- two repeats at one setting are the only honest way to separate
    random noise from fixed pattern
  * then 25, 29, 33, 39. The top is red's own value, so every rung is a number
    this device is already known to accept in that field

It stops early if a rung fails to brighten blue (the field is being ignored, or
something wrapped) or if blue clips (a clipped channel measures nothing), and it
restores 39/33/21/21 on every exit path.

`scan()` reads the gain reference itself before each pass, so a gain written
beforehand would simply be overwritten. `get_gain_offset` is therefore
substituted for the run -- which keeps the command sequence byte-for-byte what
an ordinary scan sends, rather than inventing a new one for the probe.

**The substitute still performs the device read**, and must. `cmd_17` queues a
one-shot sense condition here on every pass ("cmd_17 reported a condition;
continuing"), and it is `get_gain_offset`'s round trip that absorbs it --
`set_gain_offset` has no retry of its own, so the condition lands there instead
and the write is refused. A first version of this probe returned a cached
Settings without touching the device and was refused on its first rung for
exactly that reason.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.direct import (                                        # noqa: E402
    FILM_NEGATIVE,
    FULL_FRAME,
    CheckCondition,
    DirectScanner,
)

#: Blue's gain, rung by rung. 21 is the device's own; 39 is red's, so the top of
#: the ladder is a value this hardware is known to accept in that field. The
#: repeat at 21 is what noise_split needs.
LADDER = (21, 21, 25, 29, 33, 39)

#: Where blue is put before the ladder starts, as a fraction of full scale.
#: 39/21 is x1.86, so an analog gain would take 0.35 to about 0.65 -- clear of
#: the rail with room to spare, and high enough for the noise to be measurable.
BLUE_START = 0.35

#: Statistics are taken here, not over the whole frame: FULL_FRAME includes the
#: clear transport beside the film, which saturates at any exposure and would
#: trip the clipping guard on the first rung.
CROP = 0.25


def centre(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    return image[int(h * CROP):int(h * (1 - CROP)),
                 int(w * CROP):int(w * (1 - CROP))]


def measure(image: np.ndarray) -> dict[str, float]:
    c = centre(image)
    blue = c[..., 2].astype(np.float64)
    return {
        "median": float(np.median(blue)),
        "p99_5": float(np.percentile(blue, 99.5)),
        "at_rail_pct": float(100 * (c[..., 2] >= 65534).mean()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolution", type=int, default=300)
    ap.add_argument("--ladder", default=None,
                    help="comma-separated blue gains; repeat a value to get the "
                         "pair noise_split needs at that rung")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()
    ladder = (tuple(int(v) for v in args.ladder.split(","))
              if args.ladder else LADDER)

    if args.dry_run:
        print(f"would take {len(ladder)} passes at {args.resolution} dpi, "
              f"blue gain {list(ladder)}, blue started at {BLUE_START:.0%}")
        return 0

    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1: a probe that files "
              "nothing cannot be re-analysed, and this one is worth keeping",
              file=sys.stderr)
        return 2

    results: list[dict] = []
    scanner = DirectScanner(verbose=True)
    reference = None
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"state: scanning={state.scanning:#04x} "
              f"media_loaded={state.media_loaded} position={state.position}")
        scanner.session_start()
        scanner.wait_warm()

        reference = scanner.get_gain_offset()
        print(f"device reference: exposure={reference.exposure} "
              f"gain={reference.gain} offset={reference.offset}")

        # Meter normally, then put blue where the ladder can climb from.
        real_get = scanner.get_gain_offset
        scales = list(scanner.auto_exposure(
            resolution=args.resolution, film=FILM_NEGATIVE, infrared=False))

        # Blue's *achieved* level, not the target it was aiming at. On this
        # scanner blue is often held at the timer ceiling and never reaches the
        # target at all, so scaling from the target would leave the ladder
        # starting somewhere else entirely.
        landed = 0.0
        if scanner.last_metering and scanner.last_metering["rounds"]:
            landed = scanner.last_metering["rounds"][-1]["levels"][2]
        if landed <= 0.01:
            print("blue metered at nothing; cannot site the ladder", file=sys.stderr)
            return 1
        scales[2] *= BLUE_START / landed
        print(f"\nblue landed at {landed:.0%} of scale when metered; backing it "
              f"off to ~{BLUE_START:.0%} so the ladder has somewhere to climb")
        print(f"exposure held at {[round(v, 3) for v in scales]} for every rung\n")

        previous = None
        for rung, gain in enumerate(ladder, 1):
            settings = replace(reference, gain=[reference.gain[0],
                                                reference.gain[1],
                                                gain,
                                                reference.gain[3]])
            # scan() re-reads this before every pass, so substituting it is
            # what makes the gain survive into the payload. The real read still
            # happens: it is what absorbs the one-shot condition cmd_17 queues.
            def patched(s=settings):
                try:
                    real_get()
                except CheckCondition:
                    pass
                return s

            scanner.get_gain_offset = patched

            started = time.monotonic()
            image, meta = scanner.scan(
                resolution=args.resolution,
                infrared=False,
                frame=FULL_FRAME,
                exposure_scale=scales,
                auto_exposure=False,
                shading=False,
                keep_raw=True,
            )
            stats = measure(image)
            stats.update(rung=rung, gain=gain,
                         seconds=round(time.monotonic() - started, 1),
                         exposure=list(meta["exposure"]),
                         gain_written=list(meta["gain"]))
            results.append(stats)
            print(f"  gain {gain:>3}: blue median {stats['median']:>7.0f} "
                  f"({stats['median'] / 655.35:>5.1f}% of scale)  "
                  f"p99.5 {stats['p99_5'] / 655.35:>5.1f}%  "
                  f"at rail {stats['at_rail_pct']:.3f}%  {stats['seconds']}s")

            if stats["at_rail_pct"] > 0.1:
                print("  stopping: blue is clipping, which measures nothing")
                break
            if previous is not None and gain != previous[0]:
                if stats["median"] <= previous[1] * 1.005:
                    print(f"  stopping: gain {previous[0]} -> {gain} did not "
                          f"brighten blue ({previous[1]:.0f} -> "
                          f"{stats['median']:.0f}); the field is not doing what "
                          f"this probe assumes")
                    break
            previous = (gain, stats["median"])

    except BaseException as exc:                       # noqa: BLE001
        print(f"\nprobe stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        # Put the register back before anything else, on every path.
        if reference is not None:
            try:
                scanner.get_gain_offset = real_get
                try:
                    real_get()          # absorb anything queued, then write
                except BaseException:   # noqa: BLE001
                    pass
                scanner.set_gain_offset(reference)
                print(f"\nrestored gain {reference.gain}")
            except BaseException as exc:               # noqa: BLE001
                print(f"COULD NOT RESTORE GAIN: {exc}", file=sys.stderr)
        try:
            scanner.close()
        except BaseException:                          # noqa: BLE001
            pass

    if args.json and results:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"written to {args.json}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
