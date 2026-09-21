#!/usr/bin/env python3
"""What the roll's registration detectors actually say about real prescans.

    uv run python tools/roll_registration_study.py --root rolls/2026-09-21

Offline. Reads stored prescans and reports, per frame, what each of the three
detectors in `rps7200/framing.py` returns and *why* -- not just whether it
fired, but how close it came.

Why a separate tool rather than reading `survey.json`: the manifest records the
answer, and the answer on a loaded strip is `offset_mm: 0.0` for every frame.
That is the whole-window fallback, and a fallback and a measurement look
identical once they are written down. What settles whether a detector works is
the margin by which it abstained, which nothing stores.

Three questions it answers with no scanner time:

  * **How far is `film_bounds` from firing?** It gates on an empty aperture
    being in view -- `percentile(profile, 98) >= median * CLEAR_RATIO`. On a
    loaded strip there is none, so it returns the whole window. A gate margin
    sitting at 1.05 means hopeless; 1.9 means marginal. Those are different
    findings and the difference is not recoverable from the stored result.

  * **Did `gap_edges` have something to see and drop it?** It counts bright-flat
    runs anchored at the window *edges* only. A run in the middle is discarded
    silently, and `registration_error_mm` then answers `0.0, "no gap in view --
    registered"` -- a positive assertion of correctness. This scans for runs
    anywhere and reports the ones the anchored rule could not use.

  * **Is the gap at a constant absolute level?** If unexposed base reads the
    same from frame to frame -- one physical object, one lamp, one exposure --
    then a cut on that level finds it every time. `gap_edges` instead
    thresholds at `median + 2*MAD` of *the frame's own content*, so a
    high-contrast photograph raises the bar above its own gap. That is the
    difference between a content-independent detector and a content-dependent
    one, and it is measurable here with no scanner at all.

What it found on the five stored prescans, recorded here because it is the
reason this tool exists:

    the band's level is 36.47 +- 0.44 across all five, a 3.4% spread,
    and `gap_edges` is blind to it on two of them -- both of which then
    report "no gap in view -- registered", which is false.

It answers none of this *completely*, because a sound strip contains no
frame whose true offset is known: there is nothing here a detector could be
graded against. Widths in particular are not measurable -- they are found by
flatness, and a smooth part of the picture next to the band extends the run.
Grading needs frames deliberately pushed out of position by a known amount.
That is the displacement ladder, and it needs the scanner.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration_margin import (            # noqa: E402
    SAME_PICTURE,
    aligned_correlation,
    cohort,
    reach_px,
)
from rps7200.console import use_utf8_stdout  # noqa: E402
from rps7200.framing import (                # noqa: E402
    APERTURE_MM,
    BLANK_CONTRAST,
    CLEAR_RATIO,
    CONFIDENCE_FLOOR,
    GAP_FLATNESS,
    GAP_LEVEL_SIGMA,
    GAP_MIN_RUN,
    MAX_DY_PX,
    MAX_REGISTRATION_MM,
    frame_contrast,
    gap_edges,
    registration,
    registration_error_mm,
)
from rps7200.uniformity import luminance, register  # noqa: E402

#: The image a 135 frame actually carries, against the 36.49 mm aperture. The
#: difference is all the room the film has, and half of it is the most a frame
#: can be off while still losing no picture -- which is the tolerance this
#: study is measured against.
FRAME_MM = 36.0


def mm_per_px(width: int) -> float:
    return APERTURE_MM / width


def gate_margin(image: np.ndarray) -> tuple[float, float]:
    """How close `film_bounds` came to firing, on each axis.

    Its gate is ``percentile(profile, 98) >= median(profile) * CLEAR_RATIO``
    (`framing.py:121-124`), so this is that ratio. Below `CLEAR_RATIO` the
    function returns the whole window and every number downstream of it --
    `offset_mm`, `shortfall_mm` -- is a fallback rather than a reading.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    out = []
    for profile in (grey.mean(axis=0), grey.mean(axis=1)):
        median = float(np.median(profile))
        clear = float(np.percentile(profile, 98))
        out.append(clear / median if median > 0 else 0.0)
    return out[0], out[1]


def gap_runs(image: np.ndarray, min_run: int = 1) -> list[tuple[int, int]]:
    """Every bright-and-flat run, as ``(start, length)`` -- not only the edges.

    `gap_edges` counts runs anchored at column 0 and at the last column and
    discards everything else. That is safe when a gap can only enter from an
    edge, and a gap always does; but the *detector* can also light up in the
    middle on picture content, and when it does the anchored rule returns
    ``(0, 0)`` which `registration_error_mm` reads as "registered".

    So this exists to see what the anchored rule threw away. A run it finds in
    the middle is not a gap -- it is evidence that the bright-and-flat test
    fired on a photograph, which is the failure every detector in this repo's
    graveyard had.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return []
    level = grey.mean(axis=0)
    spread = grey.std(axis=0)
    med = float(np.median(level))
    mad = 1.4826 * float(np.median(np.abs(level - med))) or 1e-9
    typical = float(np.median(spread)) or 1e-9
    flags = (level > med + GAP_LEVEL_SIGMA * mad) & (spread < GAP_FLATNESS * typical)

    runs, start = [], None
    for i, on in enumerate(flags):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_run:
                runs.append((start, i - start))
            start = None
    if start is not None and len(flags) - start >= min_run:
        runs.append((start, len(flags) - start))
    return runs


#: A column is flat enough to be unexposed base if its variation down the
#: frame is under this, in counts. Absolute rather than relative to the
#: frame's own spread, for the same reason the level test below is: base does
#: not know what the photograph beside it looks like.
BASE_FLAT = 2.0


def edge_band(image: np.ndarray) -> dict:
    """The flat band at each edge, and what level it sits at.

    Flatness alone, deliberately: this is measuring *what the band's level is*,
    so it must not assume a level to find it. The width it returns is therefore
    an over-estimate wherever a smooth part of the photograph adjoins the band,
    and is reported only so that over-estimation is visible. The level is the
    trustworthy half -- it is a median over the flattest columns at the edge.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    level, spread = grey.mean(axis=0), grey.std(axis=0)

    def band(lv: np.ndarray, sp: np.ndarray) -> tuple[int, float]:
        n = 0
        while n < len(sp) and sp[n] < BASE_FLAT:
            n += 1
        return n, (float(np.median(lv[:n])) if n else float("nan"))

    nl, ll = band(level, spread)
    nr, lr = band(level[::-1], spread[::-1])
    med = float(np.median(level))
    mad = 1.4826 * float(np.median(np.abs(level - med))) or 1e-9
    return {
        "left_px": nl, "left_level": ll,
        "right_px": nr, "right_level": lr,
        "median": med, "mad": mad,
        "relative_threshold": med + GAP_LEVEL_SIGMA * mad,
    }


def base_level(bands: list[dict]) -> dict:
    """Is the band's level the same from frame to frame?

    This is the question that decides whether a *content-independent* detector
    is possible at all. Unexposed film base is one physical object under one
    lamp at one exposure, so its level should not care which photograph sits
    next to it. If that holds, an absolute cut finds the band on every frame --
    including the high-contrast ones where `gap_edges`, whose threshold is
    `median + 2*MAD` of the frame's *own* content, raises the bar above its own
    gap and goes blind.
    """
    levels = [b["left_level"] for b in bands if not np.isnan(b["left_level"])]
    if not levels:
        return {"n": 0}
    mean = float(np.mean(levels))
    blind = [b for b in bands
             if not np.isnan(b["left_level"])
             and b["left_level"] < b["relative_threshold"]]
    return {
        "n": len(levels), "levels": levels, "mean": mean,
        "sd": float(np.std(levels)),
        "spread_percent": (max(levels) - min(levels)) / mean * 100.0,
        "blind": [b for b in blind],
        "blind_count": len(blind),
    }


def per_frame(name: str, image: np.ndarray) -> dict:
    """Everything the three detectors say about one prescan."""
    width = image.shape[1]
    scale = mm_per_px(width)
    marks = registration(image)
    left, right = gap_edges(image)
    value, reason = registration_error_mm(image)
    runs = gap_runs(image, min_run=GAP_MIN_RUN)
    anchored = {r for r in runs if r[0] == 0 or r[0] + r[1] >= width}
    gx, gy = gate_margin(image)
    return {
        "name": name,
        "contrast": round(frame_contrast(image), 4),
        "gate_x": round(gx, 3),
        "gate_y": round(gy, 3),
        "fired": gx >= CLEAR_RATIO or gy >= CLEAR_RATIO,
        "offset_mm": marks["offset_mm"],
        "shortfall_mm": marks["shortfall_mm"],
        "gap_left": left,
        "gap_right": right,
        "error_mm": value,
        "reason": reason,
        "runs": runs,
        "stray": sorted(set(runs) - anchored),
        "mm_per_px": round(scale, 5),
    }


def report(rows: list[dict], images: list[tuple[str, np.ndarray]],
           dpi: int) -> dict:
    width = images[0][1].shape[1]
    scale = mm_per_px(width)
    no_loss = (APERTURE_MM - FRAME_MM) / 2.0

    print(f"\n{len(rows)} prescans, {width} px across, {scale:.5f} mm/px")
    print(f"aperture {APERTURE_MM:.4f} mm, frame {FRAME_MM} mm")
    print(f"  nothing lost while |e| <= {no_loss:.4f} mm = {no_loss/scale:.2f} px")
    print(f"  a detector error above    {MAX_REGISTRATION_MM} mm = "
          f"{MAX_REGISTRATION_MM/scale:.2f} px")
    print(f"  so the actionable band is {no_loss:.3f}..{MAX_REGISTRATION_MM} mm "
          f"= {no_loss/scale:.2f}..{MAX_REGISTRATION_MM/scale:.2f} px\n")

    # -- 1. film_bounds ----------------------------------------------------
    print("-- film_bounds: how close to firing " + "-" * 40)
    print(f"   gate is percentile(p,98)/median >= CLEAR_RATIO ({CLEAR_RATIO})\n")
    print(f"   {'frame':<16} {'contrast':>8} {'gate x':>7} {'gate y':>7} "
          f"{'fired':>6} {'offset_mm':>10} {'shortfall':>10}")
    for r in rows:
        print(f"   {r['name']:<16} {r['contrast']:>8.4f} {r['gate_x']:>7.3f} "
              f"{r['gate_y']:>7.3f} {str(r['fired']):>6} "
              f"{r['offset_mm']:>10.2f} {r['shortfall_mm']:>10.2f}")
    gates = [r["gate_x"] for r in rows]
    fired = sum(1 for r in rows if r["fired"])
    print(f"\n   fired on {fired}/{len(rows)}. "
          f"gate x: min {min(gates):.3f}, median {np.median(gates):.3f}, "
          f"max {max(gates):.3f}, needs {CLEAR_RATIO}")
    if max(gates) < CLEAR_RATIO:
        short = (CLEAR_RATIO - max(gates)) / CLEAR_RATIO * 100
        print(f"   VERDICT: never fires. The closest frame is {short:.0f}% short "
              f"of the gate.")
        print("   Every offset_mm and shortfall_mm above is the whole-window")
        print("   fallback, not a measurement. This is geometry, not tuning:")
        print("   the gate needs an EMPTY APERTURE in view and a loaded strip")
        print("   has none.")

    # -- 2. gap_edges ------------------------------------------------------
    print("\n-- gap_edges: what it saw, and what it discarded " + "-" * 24)
    print(f"   bright: level > med + {GAP_LEVEL_SIGMA}*MAD;  "
          f"flat: spread < {GAP_FLATNESS}*typical;  min run {GAP_MIN_RUN} px"
          f" = {GAP_MIN_RUN*scale:.4f} mm\n")
    print(f"   {'frame':<16} {'L':>3} {'R':>3} {'error_mm':>9}  "
          f"{'reason':<34} stray runs (start,len)")
    for r in rows:
        value = "None" if r["error_mm"] is None else f"{r['error_mm']:.3f}"
        stray = ", ".join(f"({s},{n})" for s, n in r["stray"][:4]) or "-"
        print(f"   {r['name']:<16} {r['gap_left']:>3} {r['gap_right']:>3} "
              f"{value:>9}  {r['reason'][:34]:<34} {stray}")
    asserted = [r for r in rows if r["error_mm"] == 0.0]
    stray_rows = [r for r in rows if r["stray"]]
    print(f"\n   asserted 'registered' on {len(asserted)}/{len(rows)} frames.")
    print(f"   {len(stray_rows)}/{len(rows)} frames carry a bright-flat run the")
    print("   anchored rule could not use.")
    if stray_rows:
        print("   Each of those is a place the test fired on the PHOTOGRAPH.")
        print("   The anchored rule discards them silently and then asserts")
        print("   'registered' -- which is the documented fail-silent hole.")
    print(f"\n   Its deadband is {GAP_MIN_RUN} px = {GAP_MIN_RUN*scale:.4f} mm "
          f"against a no-loss threshold of {no_loss:.4f} mm.")
    print(f"   Those agree to {abs(GAP_MIN_RUN*scale - no_loss)/no_loss*100:.1f}% "
          f"-- at THIS resolution only. GAP_MIN_RUN is in pixels, so at 600 dpi")
    print(f"   it would be {GAP_MIN_RUN*scale/2:.4f} mm and the detector would")
    print("   start firing inside the band where nothing is lost.")

    # -- 3. the band at the edge, and why the relative threshold misses it --
    print("\n-- the flat band at the edge: an absolute level " + "-" * 25)
    bands = [edge_band(im) for _n, im in images]
    base = base_level(bands)
    print(f"   {'frame':<16} {'band px':>8} {'its level':>10} "
          f"{'median':>8} {'MAD':>7} {'med+2MAD':>9}  sees it?")
    for r, b in zip(rows, bands, strict=True):
        level = b["left_level"]
        sees = "yes" if level >= b["relative_threshold"] else "BLIND"
        print(f"   {r['name']:<16} {b['left_px']:>8} {level:>10.1f} "
              f"{b['median']:>8.1f} {b['mad']:>7.1f} "
              f"{b['relative_threshold']:>9.1f}  {sees}")

    if base["n"]:
        print(f"\n   The band's level is {base['mean']:.2f} +- {base['sd']:.2f} "
              f"across {base['n']} frames -- a spread of "
              f"{base['spread_percent']:.1f}%.")
        print("   That is the signature of one physical object: unexposed film")
        print("   base, under one lamp, at one exposure. It does not depend on")
        print("   the photograph beside it.")
        if base["blind_count"]:
            print(f"\n   *** `gap_edges` is BLIND to it on "
                  f"{base['blind_count']}/{base['n']} frames.")
            print("   Its threshold is median + 2*MAD of the frame's OWN")
            print("   content, so a high-contrast photograph raises the bar")
            print("   above its own gap. Those frames then report")
            print('   "no gap in view -- registered", which is false: the gap')
            print("   is in view and it is the same brightness as on the")
            print("   frames where it was seen.")
            print("\n   A content-relative threshold cannot find a")
            print("   content-independent object. That is the bug, and it is")
            print("   the same shape as the four in the graveyard.")

    print("\n   The band WIDTHS above are not measurements. They are found by")
    print(f"   flatness alone (spread < {BASE_FLAT}), so wherever a smooth part of")
    print("   the picture adjoins the band the run keeps going. Any drift")
    print("   estimate built on them would be an artefact -- which is what the")
    print("   repo's own retracted drift reading was. Widths need the")
    print("   displacement ladder, where the true offset is known.")

    # -- 4. correlation ----------------------------------------------------
    reach = reach_px(dpi)
    print(f"\n-- register: confidence and dy over {len(rows)} frames " + "-" * 18)
    print(f"   reach {reach} px ({reach*scale:.2f} mm), floor {CONFIDENCE_FLOOR}, "
          f"|dy| <= {MAX_DY_PX}\n")
    pairs, nulls = [], []
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            a, b = images[i][1], images[j][1]
            dy, dx, conf = register(luminance(a), luminance(b), max_shift=reach)
            arb = aligned_correlation(a, b, int(dx))
            row = {"a": images[i][0], "b": images[j][0], "dy": int(dy),
                   "dx": int(dx), "confidence": round(float(conf), 1),
                   "arbiter": round(arb, 3),
                   "same": arb >= SAME_PICTURE}
            (pairs if row["same"] else nulls).append(row)
    print(f"   {len(nulls)} different-picture pairs, "
          f"{len(pairs)} same-picture pairs")
    if nulls:
        top = max(nulls, key=lambda r: r["confidence"])
        print(f"   highest null confidence {top['confidence']} "
              f"({top['a']} vs {top['b']}, arbiter {top['arbiter']})")
        if top["confidence"] >= CONFIDENCE_FLOOR:
            print(f"   *** a null cleared the floor of {CONFIDENCE_FLOOR} -- "
                  f"report this, it would be the first")
        else:
            print(f"   clears the floor by {CONFIDENCE_FLOOR - top['confidence']:.1f}"
                  f" -- consistent with the 3850-pair fit")
    dys = [abs(r["dy"]) for r in nulls + pairs]
    print(f"   |dy| across all pairs: max {max(dys) if dys else 0}, "
          f"median {np.median(dys) if dys else 0:.0f}")

    return {
        "frames": rows,
        "bands": bands,
        "base": base,
        "pairs": pairs,
        "nulls": nulls,
        "geometry": {
            "width_px": width, "mm_per_px": scale,
            "aperture_mm": APERTURE_MM, "frame_mm": FRAME_MM,
            "no_loss_mm": no_loss,
            "actionable_px": [no_loss / scale, MAX_REGISTRATION_MM / scale],
        },
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("rolls/2026-09-21"),
                    help="a roll folder holding prescanNN.tif, or a library")
    ap.add_argument("--dpi", type=int, default=300,
                    help="the resolution to select when reading a library")
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the whole result here")
    args = ap.parse_args(argv)

    if not args.root.is_dir():
        print(f"no folder at {args.root}")
        return 1
    images = cohort(args.root, args.dpi)
    if not images:
        print(f"no comparable prescans in {args.root}")
        return 1

    blank = [n for n, im in images if frame_contrast(im) < BLANK_CONTRAST]
    if blank:
        print(f"note: {len(blank)} pass(es) below BLANK_CONTRAST "
              f"({BLANK_CONTRAST}) -- past the end of the strip: {blank}")

    rows = [per_frame(name, image) for name, image in images]
    result = report(rows, images, args.dpi)

    if args.json:
        args.json.write_text(json.dumps(result, indent=2, default=str),
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
