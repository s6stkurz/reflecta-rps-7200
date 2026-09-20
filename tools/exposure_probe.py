#!/usr/bin/env python3
"""Walks a strip of negatives, metering each, with an exposure ladder on some.

    RPS7200_DEBUG=1 python3 tools/exposure_probe.py --json probe/exposure.json
                                                             # background it

**Ask before running this.** About 40 minutes with a colour negative strip
loaded, and the film stays in throughout -- calibration happens with it in, and
calibrating an empty transport preceded a wedge. Nothing but whole-frame advances
moves: no ``SET_SCAN_HEAD``, no sub-frame nudging.

### The two questions, and why they want different shapes

**Does metering land where it should, frame to frame?** Nobody has ever walked a
strip. Of 243 library entries 13 carry a ``metering`` block, and all seven colour
negative ones are one frame in one sitting -- a resolution sweep. So every frame
here is metered and then scanned at exactly what metering asked for. That is a
property of the *picture*, so it needs all fifteen.

**Where does the sensor stop being linear on negative?** That is a property of the
*sensor*, so measuring it fifteen times would spend half an hour learning it once.
A ladder runs on every fourth frame by default.

``docs/exposure-negative-plan.md`` has what the offline half settled: the slide
ladder that ``EXPOSURE_TARGET``'s linearity argument cites departs by only -0.64%,
while the one three-rung negative bracket departs -1.20% -- twice as much, on the
film that matters, from data too thin to say where the bend starts.

### The ladder

    x1.00   x0.55   x0.66   x0.80   x0.96   x1.15   x1.00

Relative to each frame's own metered exposure, geometric between 0.55 and 1.15 so
the rungs are evenly spaced in exposure, which is what an adjacent-ratio measure
wants. Metering puts red near 0.79 on negative, so these land it around 0.43,
0.52, 0.63, 0.75 and **0.91** -- across the top bands without saturating, which
the slide ladder could not do, because there red pinned at 0.91 from its fifth
rung and every ratio above that read as compression.

It opens and closes on ``x1.00``, and that pair is three things at once: the pass
at what ships, the repeat pair ``noise_split`` and ``agreement_z`` need, and the
drift check across the frame's own ladder.

**Those two are not linearity rungs**, and the analysis has to leave them out.
:data:`RUNGS` is the chain -- 0.55 to 1.15 at a clean x1.2 a step -- while
``x1.00`` sits 4% from ``x0.96``, and an adjacent-ratio measure fed a pair that
close divides one noisy number by another and calls the result compression. It is
the same mistake as the slide ladder's saturated top, arriving from the other
end.

**300 dpi, RGB.** An exposure question is about levels rather than detail, and 300
dpi is the cheapest pass this scanner takes -- 150 dpi costs the same, so there is
nothing below it. RGB also removes a hazard: its byte 14 default is ``0x10``, bit 0
clear, so the consecutive-bit-0-set row reversal cannot fire.

**Blue will not move above ``x1.00``.** On negative it is already at the 16-bit
timer ceiling, so ``Settings.scaled`` clamps it and the brighter rungs are a red
and green measurement. Said here rather than left to look like a null result.

### What it measures on the spot, and what it leaves for later

Each pass's delivered level is read the way ``auto_exposure`` reads a probe --
:func:`metering_slice` for the film, then the 99.5th percentile -- so "did
metering land in the band" is answered against **the pass that was delivered**,
not against the probe's last round. The last round's levels describe the scales
*before* the correction that produced the returned ones, so they are one step
stale; the final entry, reported alongside, is only indicative.

Everything else -- linearity per band, ``agreement_z`` on each frame's ``x1.00``
pair, the fixed/random noise split -- runs offline from the filed raw bytes.
``tools/linearity.py`` is the first of those, and needs no second hardware round.
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

from rps7200.direct import (                                        # noqa: E402
    EXPOSURE_TARGET,
    FILM_NEGATIVE,
    FULL_FRAME,
    OVER_TARGET_TOLERANCE,
    CheckCondition,
    DirectScanner,
)
from rps7200.bracket import FULL_SCALE                              # noqa: E402
from rps7200.framing import metering_slice                          # noqa: E402

#: The linearity chain, as multiples of what metering asked for: x1.2 a step, so
#: every adjacent ratio carries the same weight. This is what the offline
#: analysis reads.
RUNGS = (0.55, 0.66, 0.80, 0.96, 1.15)

#: What the run actually takes, the anchor pass wrapped around the chain. Opens
#: and closes on 1.00; the docstring says why that pass has to be there three
#: times over, and why it is not one of `RUNGS`.
LADDER = (1.00, *RUNGS, 1.00)

#: What a frame that is not a ladder frame gets: the metered exposure alone.
PLAIN = (1.00,)

#: 300 dpi is the cheapest pass this scanner takes; nothing below it costs less.
RESOLUTION = 300

#: Rough per-pass wall clock at 300 dpi RGB, measured. The brighter rungs run
#: slower -- scan time tracks exposure -- so this is an average, not a bound.
SECONDS_PER_PASS = 33.0
SECONDS_METERING = 2 * SECONDS_PER_PASS          # two 300 dpi RGB probe rounds
SECONDS_ADVANCE = 7.0

#: How far under target still counts as landed. `auto_exposure`'s own default,
#: repeated rather than imported because the band is what is being checked.
UNDER_TOLERANCE = 0.08


def plan(frames: int, every: int) -> list[tuple[int, tuple[float, ...]]]:
    """Which rungs each frame gets, in order. Frame numbers are 1-based."""
    out = []
    for number in range(1, frames + 1):
        laddered = every > 0 and (number - 1) % every == 0
        out.append((number, LADDER if laddered else PLAIN))
    return out


def budget(schedule: list[tuple[int, tuple[float, ...]]]) -> float:
    """Seconds the whole run will take, metering and advances included."""
    passes = sum(len(rungs) for _, rungs in schedule)
    return (passes * SECONDS_PER_PASS
            + len(schedule) * SECONDS_METERING
            + max(0, len(schedule) - 1) * SECONDS_ADVANCE)


def verdict(level: float | None, target: float = EXPOSURE_TARGET,
            tolerance: float = UNDER_TOLERANCE) -> str:
    """Where a delivered level sits relative to the acceptance band.

    The band is asymmetric on purpose -- under costs a little noise, over clips
    and nothing downstream can undo it -- so these three readings are not
    symmetric and the report must not print them as though they were.
    """
    if level is None:
        return "?"
    if level > target + OVER_TARGET_TOLERANCE:
        return "OVER"
    if level < target - tolerance:
        return "under"
    return "in band"


def levels_of(image: np.ndarray, percentile: float = 99.5) -> list[float]:
    """The delivered level per channel, read as `auto_exposure` reads a probe.

    Inside the film rather than the whole window: the empty aperture is far
    brighter than any part of the picture, and metering the whole frame lets
    however much of it is in view decide the answer.
    """
    crop = image[metering_slice(image)]
    return [round(float(np.percentile(crop[..., c], percentile)) / FULL_SCALE, 4)
            for c in range(crop.shape[2])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=15,
                    help="how many negatives to walk")
    ap.add_argument("--ladder-every", type=int, default=4, metavar="N",
                    help="run the exposure ladder on every Nth frame; 1 for "
                         "every frame, 0 for none. Linearity belongs to the "
                         "sensor, so it does not need fifteen readings.")
    ap.add_argument("--only", default=None, metavar="A-B",
                    help="walk just these frames of the plan, to chunk a run "
                         "under the harness's ten-minute foreground kill")
    ap.add_argument("--film", default=FILM_NEGATIVE)
    ap.add_argument("--resolution", type=int, default=RESOLUTION)
    ap.add_argument("--target", type=float, default=EXPOSURE_TARGET,
                    help="the metering target to check. Defaults to what ships.")
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-rewind", action="store_true",
                    help="leave the film where the walk ended instead of "
                         "returning it to where it started")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the budget, and open nothing")
    args = ap.parse_args()

    schedule = plan(args.frames, args.ladder_every)
    if args.only:
        first, _, last = args.only.partition("-")
        lo, hi = int(first), int(last or first)
        schedule = [(n, r) for n, r in schedule if lo <= n <= hi]
        if not schedule:
            print(f"--only {args.only} selects no frames", file=sys.stderr)
            return 2

    if args.dry_run:
        laddered = [n for n, rungs in schedule if len(rungs) > 1]
        print(f"{len(schedule)} frames at {args.resolution} dpi RGB on "
              f"{args.film}, target {args.target}")
        print(f"  ladder on frames {laddered or 'none'}: "
              + " ".join(f"x{r:.2f}" for r in LADDER))
        print("  every other frame: the metered pass alone")
        print(f"  {sum(len(r) for _, r in schedule)} passes, "
              f"{len(schedule)} meterings")
        print(f"  roughly {budget(schedule) / 60:.0f} minutes -- background it")
        return 0

    if not os.environ.get(DirectScanner.DEBUG_ENV):
        print(f"refusing to run without {DirectScanner.DEBUG_ENV}=1: a probe "
              f"that files nothing cannot be re-analysed, and forty minutes of "
              f"scanner time is not worth spending twice", file=sys.stderr)
        return 2

    passes: list[dict] = []
    positions: list[int] = []
    scanner = DirectScanner(verbose=True)
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"state: warming_up={state.warming_up} "
              f"media_loaded={state.media_loaded} position={state.position}")
        if not state.media_loaded:
            # Evidence, not proof: the bit is clear throughout the vendor's
            # power-on capture and has read clear with film demonstrably in, so
            # a set bit means loaded and a clear one means ask. Only Stefan can
            # see the transport, so this warns and carries on.
            print("   READ_STATE says no media -- a set bit is evidence and a "
                  "clear one is not, so this is a note, not a refusal")
        scanner.session_start()
        scanner.wait_warm()

        for index, (number, rungs) in enumerate(schedule):
            here = scanner.position()
            if here is not None:
                positions.append(here)
            print(f"\n-- frame {number} of {schedule[-1][0]}"
                  f"{' (ladder)' if len(rungs) > 1 else ''}, "
                  f"transport at {here} --")

            # Metered per frame, which is half of what is being measured: every
            # negative has its own density, and one held exposure would put some
            # frames' ladders in the wrong place entirely.
            scales = list(scanner.auto_exposure(
                resolution=args.resolution, film=args.film,
                infrared=False, target=args.target))
            metering = dict(scanner.last_metering or {})
            probed = ((metering.get("rounds") or [{}])[-1].get("levels")
                      or [None, None, None])

            for rung in rungs:
                started = time.monotonic()
                image, meta = scanner.scan(
                    resolution=args.resolution, infrared=False,
                    frame=FULL_FRAME,
                    exposure_scale=[s * rung for s in scales],
                    auto_exposure=False, film=args.film,
                    keep_raw=True, shading=True,
                )
                if image is None or image.size == 0:
                    raise RuntimeError(f"frame {number} x{rung}: no image")
                # Read off the delivered pixels while they are in hand. The
                # library keeps the raw bytes, so this is a convenience for the
                # report rather than the only chance to measure it.
                delivered = levels_of(image)
                passes.append({
                    "frame": number, "rung": rung,
                    "transport_position": here,
                    "resolution": args.resolution,
                    "entry": meta.get("library_entry"),
                    "duration_s": meta["duration_s"],
                    "exposure": list(meta["exposure"]),
                    "clamped": [e >= 65535 for e in meta["exposure"]],
                    "metering": metering if rung is rungs[0] else None,
                    "probed_levels": list(probed),
                    "levels": delivered,
                    "wall_s": round(time.monotonic() - started, 1),
                })
                print(f"   x{rung:<5.2f} exposure={list(meta['exposure'])[:3]} "
                      f"-> " + " ".join(f"{c}={v:.3f}"
                                        for c, v in zip("RGB", delivered))
                      + f"  {meta['duration_s']:5.1f}s")
                del image
                # Written after every pass. A forty-minute run that loses
                # everything because the last pass raised is worse than no run.
                if args.json:
                    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
                    Path(args.json).write_text(json.dumps(
                        {"schedule": [[n, list(r)] for n, r in schedule],
                         "target": args.target, "ladder": list(LADDER),
                         "passes": passes}, indent=2, default=float))

            if index < len(schedule) - 1:
                if scanner.advance() is None:
                    print("   the transport did not move -- end of the strip")
                    break

    except BaseException as exc:                        # noqa: BLE001
        print(f"\nprobe stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not isinstance(exc, (KeyboardInterrupt, CheckCondition, RuntimeError)):
            raise
    finally:
        # Put the film back before letting go of the device, so the strip is
        # where it started and the next run does not begin half way along it.
        try:
            if not args.no_rewind and positions:
                back = max(positions) - min(positions)
                for _ in range(back):
                    if scanner.retreat() is None:
                        break
                if back:
                    print(f"\nreturned the film {back} frame"
                          f"{'s' if back != 1 else ''}")
        except BaseException as exc:                    # noqa: BLE001
            print(f"could not return the film: {exc}", file=sys.stderr)
        try:
            scanner.close()
        except BaseException:                           # noqa: BLE001
            pass

    if not passes:
        return 1
    return report(passes, args.target)


def report(passes: list[dict], target: float = EXPOSURE_TARGET) -> int:
    shipped = [p for p in passes if p["rung"] == 1.0]

    print("\n" + "=" * 74)
    print("where the metered exposure actually landed, frame by frame")
    print("the 99.5th percentile of the delivered pass, inside the film\n")
    print(f"{'frame':>6}{'R':>8}{'G':>8}{'B':>8}   verdict")
    missed = []
    for row in shipped:
        r, g, b = (row["levels"] + [None, None, None])[:3]
        reads = [verdict(v, target) for v in (r, g, b)]
        # Blue is expected low on negative: it sits at the 16-bit timer ceiling,
        # which is the hardware and not a metering failure. Counting it would
        # make every frame a miss and hide the ones that are.
        if "OVER" in reads or "under" in reads[:2]:
            missed.append(row["frame"])
        print(f"{row['frame']:>6}" + "".join(
            f"{v:8.3f}" if v is not None else f"{'?':>8}" for v in (r, g, b))
            + "   " + ", ".join(f"{c} {v}" for c, v in zip("RGB", reads)))

    band = f"{target - UNDER_TOLERANCE:.2f}-{target + OVER_TARGET_TOLERANCE:.2f}"
    print(f"\n{len(shipped)} passes at the metered exposure, band {band}.")
    print(f"red or green outside it, or anything above it: "
          f"{missed or 'none'}")

    if any(p["clamped"][2] for p in shipped if len(p["clamped"]) > 2):
        pinned = sum(1 for p in shipped if p["clamped"][2])
        print(f"blue hit the 16-bit timer ceiling on {pinned} of "
              f"{len(shipped)} -- expected on negative, and the reason its "
              f"level is not counted above")

    # Drift before the effect. A ladder whose two ends disagree by more than the
    # departures it is meant to measure has measured its own warm-up instead.
    pairs = {}
    for row in shipped:
        pairs.setdefault(row["frame"], []).append(row)
    drifting = {f: rows for f, rows in pairs.items() if len(rows) > 1}
    if drifting:
        print("\ndrift across each ladder, first x1.00 against last:")
        for frame, (first, last) in sorted(drifting.items()):
            deltas = [100 * (b / a - 1) if a else 0.0
                      for a, b in zip(first["levels"], last["levels"])]
            print(f"{frame:>6}   " + "  ".join(
                f"{c}{d:+6.2f}%" for c, d in zip("RGB", deltas)))
        print("  a departure smaller than this is not a reading")

    print(f"\n{len(passes)} passes filed. Next, offline and with no scanner:")
    print("  tools/linearity.py --film negative       where linearity bends")
    print("  agreement_z on each frame's x1.00 pair   the baseline to beat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
