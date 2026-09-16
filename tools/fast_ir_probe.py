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
    FILM_TYPES,
    FULL_FRAME,
    CheckCondition,
    DirectScanner,
)
from rps7200.framing import reversal_against                        # noqa: E402
from rps7200.uniformity import register                             # noqa: E402

from metrics import _highpass, agreement_z, dark_mask              # noqa: E402

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
    ap.add_argument("--film", default="negative", choices=sorted(FILM_TYPES),
                    help="reaches metering only, and it matters: a slide is "
                         "metered with the visible channels locked together "
                         "and a negative is not. Infrared is refused outright "
                         "for the stocks blind to it.")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    if args.dry_run:
        print(f"would take {len(LADDER)} RGBI passes at {args.resolution} dpi "
              f"on {args.film}, sequence "
              f"{['on' if v else 'off' for v in LADDER]}, "
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
            resolution=args.resolution, film=args.film, infrared=True))
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
                film=args.film,
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
    """The three readings, from passes that are actually comparable.

    **Drift is measured first and everything else is conditioned on it.** Run
    once on 2026-09-16, the carriage start crept 0/0/-1/-2/-2/-3 lines across
    six passes -- with bit 0 *clear*, so a re-home before every one -- and dx
    stayed 0 throughout. Three lines is enough to wreck any per-pixel
    comparison: the first version of this function compared one far-apart pair
    against one adjacent pair and reported the difference as an effect of the
    flag. It was drift.

    So agreement is reported as three *families* -- off/off, on/on, off/on --
    which is the comparison that survives drift, and specks are compared only
    between passes whose measured drift matches, always beside a same-setting
    control.
    """
    print("\n" + "=" * 72)
    ordered = [(r["fast_infrared"], f) for r, f in
               zip(results, _in_order(results, frames))]
    summary: dict[str, object] = {}

    # -- drift, first, because everything below is conditioned on it ---------
    base = _lum(ordered[0][1])
    drift = [register(base, _lum(f), 24)[0] for _, f in ordered]
    summary["drift_lines"] = drift
    print("drift\n")
    print("  carriage start, lines against the first pass: "
          + " ".join(f"{d:+d}" for d in drift))
    if max(drift) - min(drift) > 0:
        print(f"  spread {max(drift) - min(drift)} lines -- pairs further "
              f"apart in the run agree less for this reason alone, which is "
              f"why the families below are what decide it")

    # -- time ---------------------------------------------------------------
    off_ms = [r["ms_per_line"] for r in results if not r["fast_infrared"]]
    on_ms = [r["ms_per_line"] for r in results if r["fast_infrared"]]
    print("\ntime\n")
    print(f"{'setting':>8}{'n':>4}{'mean ms/line':>14}{'vs off':>9}")
    base_ms = float(np.mean(off_ms)) if off_ms else None
    for label, rows in (("off", off_ms), ("on", on_ms)):
        if rows:
            mean_ms = float(np.mean(rows))
            print(f"{label:>8}{len(rows):>4}{mean_ms:>14.2f}"
                  f"{(mean_ms / base_ms if base_ms else 1.0):>9.3f}")
            summary[f"ms_per_line_{label}"] = mean_ms
    if off_ms and on_ms:
        saved = 1 - float(np.mean(on_ms)) / base_ms
        summary["time_saved"] = saved
        print(f"\n  {saved:+.1%} on the pass. Under 15% is not worth a "
              f"protocol change.")

    # -- agreement, by family -----------------------------------------------
    for label, channel in (("picture (green, darkest tenth)", 1),
                           ("infrared plane", IR)):
        print(f"\n{label} -- every pair at its own best alignment\n")
        fam, shifts = _families(ordered, channel)
        summary[f"agreement_{channel}"] = {k: float(np.median(v))
                                           for k, v in fam.items() if v}
        for kind, values in fam.items():
            if values:
                print(f"  {kind:>7}: n={len(values)}  "
                      f"median |z| = {np.median(values):.2f}  "
                      f"range {min(values):.2f}-{max(values):.2f}   "
                      f"shifts {sorted(set(shifts[kind]))}")
        control = fam["off/off"] or fam["on/on"]
        if control and fam["off/on"]:
            verdict = ("unchanged" if np.median(fam["off/on"])
                       <= np.median(control) * 1.15 else
                       "MOVED -- this is where it stops")
            print(f"\n  off/on against a same-setting control: {verdict}")
        same = set(shifts["off/off"]) | set(shifts["on/on"])
        if channel == 1 and same and set(shifts["off/on"]) - same:
            print(f"  the flag moves the carriage start: same-setting pairs "
                  f"align at {sorted(same)}, across the flag at "
                  f"{sorted(set(shifts['off/on']))}")

    # -- specks, only where the drift matches -------------------------------
    print("\ndust\n")
    groups: dict[int, list[tuple[bool, np.ndarray]]] = {}
    for (fast, frame), d in zip(ordered, drift):
        groups.setdefault(d, []).append((fast, frame))
    rows = []
    for d, members in sorted(groups.items()):
        for i, (fast_a, a) in enumerate(members):
            for j, (fast_b, b) in enumerate(members):
                if i == j:
                    continue
                picks = speck_pixels(a[..., IR])
                if int(picks.sum()) < MIN_SPECKS:
                    continue
                depth_a = speck_depth(a[..., IR], picks)
                depth_b = speck_depth(b[..., IR], picks)
                kind = (("off" if not fast_a else "on") + "->"
                        + ("off" if not fast_b else "on"))
                rows.append({"drift": d, "kind": kind, "specks": int(picks.sum()),
                             "source_sigma": depth_a, "measured_sigma": depth_b,
                             "ratio": depth_b / max(depth_a, 1e-9)})
    if not rows:
        print("  no two passes share an alignment, or the frame has no dust "
              "-- nothing here can be compared speck by speck. A frame with "
              "visible dust and a shorter run would answer it.")
    else:
        print(f"{'drift':>6}{'kind':>9}{'specks':>8}{'source':>9}"
              f"{'measured':>10}{'ratio':>8}")
        for r in rows:
            print(f"{r['drift']:>6}{r['kind']:>9}{r['specks']:>8}"
                  f"{r['source_sigma']:>9.2f}{r['measured_sigma']:>10.2f}"
                  f"{r['ratio']:>8.2f}")
        same = [r["ratio"] for r in rows if r["kind"] in ("off->off", "on->on")]
        cross = [r["ratio"] for r in rows if r["kind"] in ("off->on", "on->off")]
        if same and cross:
            print(f"\n  same-setting control {np.median(same):.2f}x, "
                  f"across the flag {np.median(cross):.2f}x")
        summary["specks"] = rows

    print("\n  note: `noise_split`'s random share is not reported for the "
          "infrared plane. Its highpass is a horizontal box filter, which "
          "understates total high-frequency content on a plane that is mostly "
          "smooth base, and the share comes out above 100%.")

    if out:
        Path(out).write_text(json.dumps(
            {"passes": results, "summary": summary}, indent=2, default=float))
        print(f"\nwritten to {out}")
    return 0


def _lum(frame: np.ndarray) -> np.ndarray:
    return frame[..., :3].astype(np.float64).mean(axis=2)


def _in_order(results: list[dict],
              frames: dict[bool, list[np.ndarray]]) -> list[np.ndarray]:
    """The frames back in the order they were taken.

    `frames` is grouped by setting for the noise pairs; the drift measurement
    needs the run's own sequence, which is what `results` still carries.
    """
    taken = {True: 0, False: 0}
    out = []
    for r in results:
        fast = r["fast_infrared"]
        out.append(frames[fast][taken[fast]])
        taken[fast] += 1
    return out


#: Line shifts searched when aligning one pass to another. Four lines each way
#: covers everything seen: a carriage that creeps a line or two across a run,
#: plus the offset the flag itself introduces.
ALIGN = range(-4, 5)


def _aligned_z(a: np.ndarray, b: np.ndarray, channel: int) -> tuple[float, int]:
    """Best agreement over a small range of line shifts, and the shift.

    **Every comparison goes through this, and that is not defensive coding.**
    Twice this probe reported an effect that was a misregistration. The first
    time the carriage crept monotonically across the run, so pairs further
    apart agreed less; grouping into families fixed that. The second time the
    drift *correlated with the flag* -- every `on` pass landed a line or two
    from every `off` pass -- and families did not help at all, because the
    off/on family was then the only one comparing misaligned passes. It
    reported the picture and the infrared plane as both degraded, at 1.84 and
    1.62 against controls of 1.41 and 1.33. Aligned pair by pair, the same data
    gives 1.15 and 1.10 against 1.08 and 1.09: no effect whatever.

    A pair compared at anything but its own best alignment is not measuring the
    flag, and there is no arrangement of the ladder that avoids this -- only
    aligning does.
    """
    pad = max(abs(s) for s in ALIGN)
    best = (float("inf"), 0)
    for shift in ALIGN:
        left = a[pad:a.shape[0] - pad]
        right = np.roll(b, shift, axis=0)[pad:b.shape[0] - pad]
        mask = (dark_mask(left, 10.0) if channel < IR
                else np.ones(left.shape[:2], bool))
        best = min(best, (agreement_z(left, right, mask, channel=channel), shift))
    return best


def _families(ordered: list[tuple[bool, np.ndarray]],
              channel: int) -> tuple[dict[str, list[float]], dict[str, list[int]]]:
    """Every pair's best-aligned agreement, grouped by whether the flag differed.

    Returns the agreements and the shifts they needed. The shifts are not
    bookkeeping: a systematic offset between the `on` and `off` families is the
    flag moving where the carriage starts, which is worth knowing on its own and
    is invisible in the agreement numbers once it has been corrected for.
    """
    fam: dict[str, list[float]] = {"off/off": [], "on/on": [], "off/on": []}
    shifts: dict[str, list[int]] = {"off/off": [], "on/on": [], "off/on": []}
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            (fast_a, a), (fast_b, b) = ordered[i], ordered[j]
            kind = ("off/off" if not fast_a and not fast_b
                    else "on/on" if fast_a and fast_b else "off/on")
            z, shift = _aligned_z(a, b, channel)
            fam[kind].append(z)
            shifts[kind].append(shift)
    return fam, shifts


if __name__ == "__main__":
    raise SystemExit(main())
