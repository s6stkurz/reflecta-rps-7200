#!/usr/bin/env python3
"""Drives MODE SELECT quality bit 0x80 -- "fast infrared" -- on and off.

    RPS7200_DEBUG=1 python3 tools/fast_ir_probe.py        # background it

**Ask before running this.** Nothing moves -- the transport is untouched and no
scan frame is advanced -- but it costs about 25 minutes of scanner time and the
film has to be loaded.

`QUALITY_FAST_INFRARED` has been defined and reachable since the protocol was
first written and has never once been sent. The reference backend calls it
"acquire the infrared plane in a faster, lower-quality pass"; **CyberView sends
it in none of 3,955 captured commands**, so there is no capture to check this
against and the bytes are held by `tests/test_fast_infrared.py` instead.

It is worth asking about because the infrared floor is the dominant cost of
everything here: 69.6 s for an RGB pass at 1800 dpi against 227.2 s for the
RGBI one, and the floor does not move with resolution. A 38-frame infrared roll
is over two hours of it.

And it is worth accepting a worse answer for, which no other speed/quality trade
on this scanner was: **the infrared plane is a dust mask, not a picture.**
Nothing looks at it directly. A degradation that would be unacceptable in R, G
or B may cost nothing at all in I -- which is the question, and why the readings
below are what they are rather than a single noise figure.

What it does, at one fixed exposure throughout:

  * meters once, RGBI, and holds that exposure for every pass
  * six RGBI passes of one frame, `off on off on on off` -- three of each so
    `noise_split` and `agreement_z` have a same-setting pair on both sides, and
    `off` first and last so drift across 25 minutes is visible rather than
    mistaken for an effect
  * no seventh pass to restore anything: the ladder already ends on `off`, and
    the bit is sent fresh with every MODE SELECT rather than persisting

**Byte 14 is forced to 0x20 -- bit 0 clear -- on every pass, and that is
load-bearing.** This driver's default for RGBI is 0x21, bit 0 set, which skips
the carriage re-home; a bit-0-set pass that immediately follows another comes
back top-and-bottom reversed with nothing in the response to say so
(`docs/byte14-plan.md`). Six consecutive RGBI passes is exactly that case, so
the default would have reversed passes 2 through 6 and every comparison here
would have been made against an upside-down frame. Clearing bit 0 costs the
bidirectional speed uniformly, which does not matter when the comparison is
on-against-off. `reversal_against` re-checks every pass anyway, and the run
stops if one arrives reversed regardless.

### What decides it

Three readings, and the second can kill it outright.

**Time.** ms/line, `on` against the `off` baseline. Under about 15% is not worth
a protocol change; the floor is the whole point of asking.

**Do R, G and B survive?** The bit is documented as affecting the infrared plane
but it sits in a field that governs the whole pass. `agreement_z` on an off/off
pair is the control, the same pair-at-one-exposure comparison that gives 1.03 in
`docs/multi-exposure-plan.md`; `agreement_z` on an off/on pair is the test. If
the picture degrades at all, the answer is no and nothing else matters.

**Does the dust survive?** Not "is the plane noisier" but "can the specks still
be found". The specks are located once, in an `off` plane, and then the *same*
pixels are measured in both -- how many sigmas below their local base they sit.
A plane that is noisier but keeps its specks proportionally deep is still a
usable mask.
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / ".claude" / "skills" / "measure-scan-quality" / "scripts"))

from rps7200.direct import (                                        # noqa: E402
    FILM_NEGATIVE,
    FULL_FRAME,
    CheckCondition,
    DirectScanner,
)
from rps7200.framing import reversal_against                        # noqa: E402

from metrics import _highpass, agreement_z, dark_mask, noise_split  # noqa: E402

#: `off on off on on off`. Three of each so both sides have a same-setting pair,
#: interleaved so drift does not line up with the variable, and `off` at both
#: ends so the run can be checked for drift at all.
LADDER = (False, True, False, True, True, False)

#: Byte 14 with bit 0 clear: re-home before every pass. See the module
#: docstring -- this is what stops passes 2 onward coming back reversed.
BYTE14_REHOME = 0x20

#: Statistics are taken here, not over the whole frame: FULL_FRAME includes the
#: clear transport beside the film, which is neither timed nor dusty.
CROP = 0.25

#: How far below its local base a pixel must sit to be called a speck. High on
#: purpose: what is wanted is unambiguous dust, not the noise floor.
SPECK_SIGMA = 5.0

#: Below this many specks the frame has nothing to judge dust removal by, and
#: the probe says so rather than reporting a number computed from noise.
MIN_SPECKS = 20

#: The infrared plane, once the visible three are past.
IR = 3


def centre(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    return image[int(h * CROP):int(h * (1 - CROP)),
                 int(w * CROP):int(w * (1 - CROP))]


def speck_pixels(plane: np.ndarray) -> np.ndarray:
    """Where the dust is, as infrared sees it: dark points on a smooth base."""
    hp = _highpass(plane.astype(np.float64), k=9)
    return hp < -SPECK_SIGMA * float(np.std(hp))


def speck_depth(plane: np.ndarray, picks: np.ndarray) -> float:
    """How many sigmas below its local base the chosen dust sits, median.

    Measured on pixels chosen from a *different* plane, deliberately: asking
    each plane for its own darkest points would compare two different sets of
    pixels and call that agreement.
    """
    hp = _highpass(plane.astype(np.float64), k=9)
    return float(np.median(-hp[picks]) / max(float(np.std(hp)), 1e-9))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolution", type=int, default=1800,
                    help="1800 by default: the floor dominates there and the "
                         "visible half is still short")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    if args.dry_run:
        print(f"would take {len(LADDER)} RGBI passes at {args.resolution} dpi, "
              f"sequence {['on' if v else 'off' for v in LADDER]}, "
              f"byte14={BYTE14_REHOME:#04x} throughout")
        print(f"budget roughly {len(LADDER) * 230 / 60:.0f} minutes at the "
              f"infrared floor, plus metering -- background it")
        return 0

    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1: a probe that files "
              "nothing cannot be re-analysed, and 25 minutes of infrared "
              "floor is not worth spending twice", file=sys.stderr)
        return 2

    results: list[dict] = []
    frames: dict[bool, list[np.ndarray]] = {}
    scales: list[float] = []
    scanner = DirectScanner(verbose=True)
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"state: scanning={state.scanning:#04x} "
              f"media_loaded={state.media_loaded} position={state.position}")
        scanner.session_start()
        scanner.wait_warm()

        # Metered with infrared=True so blue is given its RGBI headroom: it
        # comes back several times brighter in an infrared pass at the same
        # exposure, film-dependently, and metering it as though this were RGB
        # would put it at the rail.
        scales = list(scanner.auto_exposure(
            resolution=args.resolution, film=FILM_NEGATIVE, infrared=True))
        print(f"\nexposure held at {[round(v, 3) for v in scales]} for every "
              f"pass\n")

        first = None
        for i, fast in enumerate(LADDER, 1):
            started = time.monotonic()
            image, meta = scanner.scan(
                resolution=args.resolution,
                infrared=True,
                frame=FULL_FRAME,
                exposure_scale=scales,
                auto_exposure=False,
                shading=False,
                keep_raw=True,
                byte14=BYTE14_REHOME,
                fast_infrared=fast,
            )
            wall = time.monotonic() - started
            if image is None or image.size == 0:
                raise RuntimeError(f"pass {i} returned no image")

            # The prevention above is what stops a reversal; this is the check
            # that it worked. A reversed pass compared against an upright one
            # measures the frame standing on its head, and every number below
            # would be wrong without saying so.
            if first is None:
                first = image
            else:
                reading, detail = reversal_against(first, image)
                if reading != (0, False):
                    raise RuntimeError(
                        f"pass {i} came back {reading} against the first pass "
                        f"({detail.get('reason') or 'correlation'}); the "
                        f"comparison would be meaningless -- stopping"
                    )

            ms_per_line = 1000 * meta["duration_s"] / meta["height"]
            frames.setdefault(fast, []).append(centre(image))
            print(f"  [{i}/{len(LADDER)}] fast_infrared={'on ' if fast else 'off'}"
                  f"  height={meta['height']}  duration={meta['duration_s']:.1f}s"
                  f"  ms/line={ms_per_line:6.2f}  (wall {wall:.1f}s)")

            results.append({
                "index": i, "fast_infrared": bool(fast),
                "resolution": args.resolution, "height": meta["height"],
                "duration_s": meta["duration_s"],
                "ms_per_line": round(ms_per_line, 4),
                "exposure": list(meta["exposure"]),
            })

    except BaseException as exc:                        # noqa: BLE001
        print(f"\nprobe stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not isinstance(exc, (KeyboardInterrupt, CheckCondition, RuntimeError)):
            raise
    finally:
        # Nothing to restore: the bit is sent fresh with every MODE SELECT
        # rather than persisting in a register, and the ladder ends on `off`
        # in any case. A seventh pass here would cost four minutes to prove
        # something the sixth already proved.
        try:
            scanner.close()
        except BaseException:                           # noqa: BLE001
            pass

    if not results:
        return 1
    return report(results, frames, args.json)


def report(results: list[dict], frames: dict[bool, list[np.ndarray]],
           out: str | None) -> int:
    print("\n" + "=" * 72)

    off_rows = [r["ms_per_line"] for r in results if not r["fast_infrared"]]
    on_rows = [r["ms_per_line"] for r in results if r["fast_infrared"]]
    base = float(np.mean(off_rows)) if off_rows else None
    summary: dict[str, object] = {}

    print("time\n")
    print(f"{'setting':>8}{'n':>4}{'mean ms/line':>14}{'vs off':>9}")
    for label, rows in (("off", off_rows), ("on", on_rows)):
        if not rows:
            continue
        mean_ms = float(np.mean(rows))
        ratio = mean_ms / base if base else 1.0
        print(f"{label:>8}{len(rows):>4}{mean_ms:>14.2f}{ratio:>9.3f}")
        summary[f"ms_per_line_{label}"] = mean_ms
    if off_rows and on_rows:
        saved = 1 - float(np.mean(on_rows)) / base
        summary["time_saved"] = saved
        print(f"\n  {saved:+.1%} on the pass. Under 15% is not worth a "
              f"protocol change.")

    off, on = frames.get(False, []), frames.get(True, [])
    if len(off) >= 2 and len(on) >= 1:
        print("\ndoes the picture survive it\n")
        mask = dark_mask(off[0], 10.0)
        control = agreement_z(off[0], off[1], mask, channel=1)
        test = agreement_z(off[0], on[0], mask, channel=1)
        summary["agreement_control"] = control
        summary["agreement_on_off"] = test
        print(f"  off vs off (control)  |z| = {control:.2f}")
        print(f"  off vs on  (test)     |z| = {test:.2f}")
        print(f"\n  {'the visible channels are unchanged' if test <= control * 1.15 else 'THE PICTURE MOVED -- this is where it stops'}")

    if len(on) >= 2 and len(off) >= 2:
        print("\ndoes the dust survive it\n")
        picks = speck_pixels(off[0][..., IR])
        found = int(picks.sum())
        summary["specks"] = found
        if found < MIN_SPECKS:
            print(f"  only {found} specks in the off plane -- this frame has "
                  f"nothing to judge dust by. Pick a dustier one.")
        else:
            depth_off = speck_depth(off[0][..., IR], picks)
            depth_on = speck_depth(on[0][..., IR], picks)
            summary["speck_depth_off"] = depth_off
            summary["speck_depth_on"] = depth_on
            print(f"  {found} specks, measured in both planes at the same "
                  f"pixels")
            print(f"  depth below local base:  off {depth_off:.2f} sigma   "
                  f"on {depth_on:.2f} sigma   ({depth_on / depth_off:.2f}x)")

        for label, pair in (("off", off), ("on", on)):
            mask = np.ones(pair[0].shape[:2], bool)
            rnd, total, share = noise_split(pair[0], pair[1], mask, channel=IR)
            summary[f"ir_noise_{label}"] = {"random": rnd, "total": total,
                                            "share": share}
            print(f"  infrared plane, {label:>3}: random {rnd:7.1f} DN   "
                  f"total {total:7.1f} DN   random share {share:.0%}")

    first_off = next((r for r in results if not r["fast_infrared"]), None)
    last_off = next((r for r in reversed(results) if not r["fast_infrared"]), None)
    if first_off and last_off and first_off is not last_off:
        print(f"\ndrift check: first off pass {first_off['ms_per_line']:.2f} "
              f"ms/line, last {last_off['ms_per_line']:.2f} ms/line")

    if out:
        Path(out).write_text(json.dumps(
            {"passes": results, "summary": summary}, indent=2, default=float))
        print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
