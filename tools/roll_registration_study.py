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
from rps7200 import tiff                     # noqa: E402
from rps7200.console import use_utf8_stdout  # noqa: E402
from rps7200.framing import (                # noqa: E402
    APERTURE_MM,
    BLANK_CONTRAST,
    HOLD_TOLERANCE_MM,
    StripWalk,
    combine,
    frame_offset_mm,
    predict_offset,
    right_gap_closure,
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
    # A run that starts within a gap's width of the edge is that gap, sitting
    # a few columns in because a sliver of the ADJACENT frame is also in view.
    # The anchored rule requires the run to start at column 0 exactly, so it
    # discards these and reports nothing -- and `registration_error_mm` then
    # asserts the frame is registered. That is a false assertion about a frame
    # whose gap it could see.
    near = max(GAP_MIN_RUN * 4, 12)
    false_calls = []
    for r in rows:
        if r["error_mm"] != 0.0:
            continue
        displaced = [(s, n) for s, n in r["stray"]
                     if s <= near or s + n >= width - near]
        if displaced:
            false_calls.append((r, displaced))

    print(f"\n   asserted 'registered' on {len(asserted)}/{len(rows)} frames.")
    if false_calls:
        print(f"\n   *** {len(false_calls)} of those {len(asserted)} are FALSE. "
              f"Each has a gap run within")
        print(f"   {near} px of an edge that the anchored rule discarded, "
              f"because it")
        print("   requires the run to begin at column 0 exactly:")
        for r, displaced in false_calls:
            where = ", ".join(f"starts at {s}, {n} px wide" for s, n in displaced)
            print(f"     {r['name']:<16} {where}")
        print("\n   A gap is not flush with the window when a sliver of the")
        print("   neighbouring frame is in view beside it -- which is exactly")
        print("   the situation a registration detector exists to find. The")
        print("   rule is blind in the one case that matters.")
    stray_rows = [r for r in rows if r["stray"]]
    if len(stray_rows) > len(false_calls):
        print(f"\n   {len(stray_rows) - len(false_calls)} further run(s) sit "
              f"away from any edge. Those are the test")
        print("   firing on the photograph, which is the other failure mode.")
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


def load_walk(log: Path) -> tuple[list[tuple[str, np.ndarray]], list[dict]]:
    """A walk's frames, from its log rather than from a glob.

    The log names each frame's two passes, and the pairing is the point: two
    passes with nothing moved between them are the only measurement of the
    pass-to-pass noise, and a glob cannot tell which two belong together.

    The first pass of each frame stands for the frame everywhere else, so the
    detector table is comparable with one built from a single-pass walk.
    """
    record = json.loads(log.read_text(encoding="utf-8"))
    folder = log.parent
    images, pairs = [], []
    for frame in record.get("frames") or []:
        names = frame.get("passes") or []
        loaded = []
        for name in names:
            try:
                loaded.append(tiff.read(str(folder / name)).astype(np.float64))
            except (OSError, ValueError):
                loaded.append(None)
        if not loaded or loaded[0] is None:
            continue
        images.append((names[0], loaded[0]))
        if len(loaded) > 1 and loaded[1] is not None:
            pairs.append({"number": frame.get("number"),
                          "names": names[:2],
                          "images": (loaded[0], loaded[1])})
    return images, pairs


def noise_floor(pairs: list[dict], dpi: int, scale: float) -> dict:
    """How far apart two passes of one frame read, with nothing moved.

    This is sigma_0, and nothing else in the study can be interpreted without
    it. The actionable band is under three pixels wide, and the repo's only
    prior figures are ~1.3 px equivalent at 3600 dpi and `[0, 2]` at 600 -- it
    has never been measured at the resolution every roll actually prescans at.

    A non-zero reading here is not necessarily the film moving. Two passes are
    two traverses of the same carriage, and the carriage's re-home is recorded
    as not landing in the same place twice.
    """
    reach = reach_px(dpi)
    rows = []
    for pair in pairs:
        a, b = pair["images"]
        dy, dx, conf = register(luminance(a), luminance(b), max_shift=reach)
        rows.append({"number": pair["number"], "dx": int(dx), "dy": int(dy),
                     "confidence": round(float(conf), 1),
                     "mm": round(float(dx) * scale, 4)})
    shifts = [r["dx"] for r in rows]
    confs = [r["confidence"] for r in rows]
    return {
        "rows": rows,
        "n": len(rows),
        "max_abs_px": max((abs(s) for s in shifts), default=0),
        "sd_px": float(np.std(shifts)) if shifts else 0.0,
        "mean_px": float(np.mean(shifts)) if shifts else 0.0,
        "min_confidence": min(confs) if confs else 0.0,
        "below_floor": [r for r in rows if r["confidence"] < CONFIDENCE_FLOOR],
        "off_axis": [r for r in rows if abs(r["dy"]) > MAX_DY_PX],
    }


def report_noise(noise: dict, scale: float) -> None:
    print("\n-- the noise floor: two passes, nothing moved " + "-" * 26)
    if not noise["n"]:
        print("   no repeat pairs in this cohort -- run a walk that takes two")
        print("   prescans per frame, which is what --walk reads.")
        return
    print(f"   {'frame':>6} {'dx px':>6} {'dy':>4} {'mm':>9} {'confidence':>11}")
    for r in noise["rows"]:
        flag = "  <- below floor" if r["confidence"] < CONFIDENCE_FLOOR else ""
        print(f"   {r['number']:>6} {r['dx']:>6} {r['dy']:>4} {r['mm']:>9.4f} "
              f"{r['confidence']:>11.1f}{flag}")
    print(f"\n   {noise['n']} pairs. |dx| max {noise['max_abs_px']} px "
          f"= {noise['max_abs_px']*scale:.4f} mm, "
          f"sd {noise['sd_px']:.2f} px = {noise['sd_px']*scale:.4f} mm")
    print(f"   confidence: min {noise['min_confidence']:.1f} "
          f"against a floor of {CONFIDENCE_FLOOR}")
    if noise["below_floor"]:
        print(f"   *** {len(noise['below_floor'])} pair(s) below the floor. "
              f"Two passes of the SAME frame should be the easiest match")
        print("   there is; if these are refused, the floor is too high for "
              "300 dpi.")
    if noise["off_axis"]:
        print(f"   *** {len(noise['off_axis'])} pair(s) with |dy| > "
              f"{MAX_DY_PX} -- the transport moves only in x")
    band = (APERTURE_MM - FRAME_MM) / 2.0 / scale
    print(f"\n   The decision band is {band:.2f} px wide. This floor is "
          f"{noise['max_abs_px']} px at worst,")
    print(f"   which is {noise['max_abs_px']/band*100:.0f}% of it.")


#: The base level a gap sits at, and how far a column may stray from it. Found
#: empirically at 36.5-37 across two separate walks; kept as a tolerance rather
#: than a constant because it is a property of this lamp at this exposure.
BASE_TOLERANCE = 0.12


def picture_start(image: np.ndarray, base: float) -> tuple[int, int]:
    """Where the frame's own picture begins, as ``(gap_start, gap_end)``.

    The registration-relevant quantity is the *end* of the gap, not its width:
    that is the first column of this photograph, and holding it constant from
    frame to frame is what "registered" means.

    Found by absolute level and absolute flatness -- the two properties
    unexposed base has and a photograph does not reliably have. It still
    over-runs wherever a smooth region of the picture sits at the same level,
    so a width far past the ~2 mm a 135 gap can be is a contaminated reading
    and is reported rather than silently used.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    level, spread = grey.mean(axis=0), grey.std(axis=0)
    isbase = (np.abs(level - base) <= base * BASE_TOLERANCE) & (spread < 3.0)
    best, run, start = (0, 0), 0, 0
    for i in range(len(isbase) // 3):
        if isbase[i]:
            if run == 0:
                start = i
            run += 1
            if run > best[1]:
                best = (start, run)
        else:
            run = 0
    return best[0], best[0] + best[1]


def report_position(rows, images, base: float, scale: float) -> dict:
    """Is the picture in the same place on every frame?"""
    print("\n-- where each frame's picture begins " + "-" * 34)
    print("   the END of the gap: the first column of this photograph\n")
    # A 135 gap is about 2 mm. Anything much past that is the run walking into
    # a smooth part of the picture, and is excluded rather than averaged in.
    ceiling = int(round(2.6 / scale))
    print(f"   {'frame':<16} {'gap':>9} {'end px':>7} {'end mm':>8}   ")
    ends, used = [], []
    for r, (_n, im) in zip(rows, images, strict=True):
        s, e = picture_start(im, base)
        wide = (e - s) > ceiling
        note = f"  <- {e-s} px wide, over {ceiling}: contaminated" if wide else ""
        print(f"   {r['name']:<16} {f'{s}..{e}':>9} {e:>7} {e*scale:>8.3f}{note}")
        ends.append(e)
        if not wide:
            used.append(e)
    if len(used) < 3:
        print("\n   too few clean readings to say anything about position.")
        return {"ends": ends, "used": used}

    arr = np.array(used, float)
    print(f"\n   {len(used)} clean of {len(ends)}. "
          f"mean {arr.mean():.1f} px = {arr.mean()*scale:.3f} mm, "
          f"sd {arr.std():.2f} px = {arr.std()*scale:.4f} mm, "
          f"range {arr.max()-arr.min():.0f} px")
    no_loss = (APERTURE_MM - FRAME_MM) / 2.0
    print(f"\n   Those starts are {arr.mean()*scale:.2f} mm into a "
          f"{APERTURE_MM:.2f} mm aperture. If the image really were "
          f"{FRAME_MM} mm")
    print(f"   the most it could be is {no_loss:.3f} mm, so either every frame "
          f"is losing")
    print("   picture at the far edge -- which the operator would see -- or")
    print(f"   the image on this film is about "
          f"{APERTURE_MM - 2*arr.mean()*scale:.1f} mm wide, not {FRAME_MM}.")
    print("   That second reading makes the slack larger than "
          f"MAX_REGISTRATION_MM ({MAX_REGISTRATION_MM} mm),")
    print("   which is why genuine gap readings are being refused as "
          "detector error.")
    print("   The displacement ladder settles which it is.")
    return {"ends": ends, "used": used,
            "mean_px": float(arr.mean()), "sd_px": float(arr.std())}


def report_ensemble(images: list[tuple[str, np.ndarray]]) -> dict:
    """Replay a stored walk through the ensemble, causally, in walk order.

    What each frame would have been told to do knowing only what the walk knew
    when it reached it: the base armed from the frames already passed, the prior
    from the advances already seen.

    **Observe-only, and it has to be.** The film in a stored walk never moved,
    so frame 8's image shows where the film actually was -- not where it would
    have been had frame 7 been corrected. An earlier version of this simulated
    the corrections landing and so manufactured advance errors of -0.78 mm
    against a real wander of +/-0.17, then reported the ensemble refusing frames
    it would not refuse. What this measures is whether the members agree about
    where the film *is*, which is the question the gate actually asks.

    Costs no scanner time: `rolls/` keeps the prescans.
    """
    walk = StripWalk()
    seen: list[np.ndarray] = []
    rows, moved, in_place, refused = [], 0, 0, 0

    print("\n-- the ensemble, replayed in walk order --\n")
    print(f"  {'frame':<14} {'base':>6} {'left':>7} {'prior':>7} {'right':>7}"
          f"  outcome")
    for order, (name, image) in enumerate(images, 1):
        seen.append(image)
        walk.observe(order, image)
        if walk.base is None:
            print(f"  {name[:14]:<14} {'-':>6} {'-':>7} {'-':>7} {'-':>7}  "
                  f"no base yet: {walk.base_detail.get('reason', '')[:34]}")
            rows.append({"frame": name, "outcome": "no_base"})
            continue

        left = frame_offset_mm(image, walk.base)
        right = right_gap_closure(image, walk.base)
        prior = predict_offset(walk.history(order), order)
        decision, detail = combine([left, right, prior])
        if left.mm is not None:
            walk.placed[order] = left.mm

        if decision is None:
            outcome = "refused: " + detail.get("reason", "")[:44]
            refused += 1
        elif abs(decision) < HOLD_TOLERANCE_MM:
            outcome = f"in place ({detail['chose']})"
            in_place += 1
        else:
            outcome = f"move {decision:+.2f} mm ({detail['chose']})"
            moved += 1

        def show(r):
            return f"{r.mm:+7.2f}" if r.mm is not None else "      -"

        print(f"  {name[:14]:<14} {walk.base.level:6.1f} {show(left)} "
              f"{show(prior)} {show(right)}  {outcome}")
        rows.append({
            "frame": name, "decision_mm": decision,
            "outcome": outcome.split(":")[0].split(" (")[0],
            "members": detail.get("members"), "chose": detail.get("chose"),
        })

    print(f"\n  {moved} would move, {in_place} in place, {refused} refused")
    if walk.base is not None:
        print(f"  base {walk.base.level:.2f} counts, spread "
              f"{walk.base.spread * 100:.2f}%, {walk.base.bands} bands from "
              f"{walk.base_detail.get('frames')} frames")
    print("\n  A refusal is the safe failure and the common one early: the "
          "prior\n  needs two placed frames behind it before it can speak at "
          "all.")
    return {"rows": rows, "moved": moved, "in_place": in_place,
            "refused": refused,
            "base": None if walk.base is None else walk.base.level}

def report_held(folder: Path) -> dict:
    """What a roll's holding actually delivered, from the manifest it wrote.

    Read back rather than recomputed, per CLAUDE.md: the interesting thing is
    what the loop decided and measured while it ran, and a recomputation would
    quietly agree with itself.

    The question this answers is not "did it converge" -- `hold_plan` says that
    and would say it of a frame that never moved -- but three separate ones:

      * **did anything move at all**, which `moves: 0` and `spent_mm: 0.0`
        settle, and which a manifest otherwise reports identically to success;
      * **how much of what was commanded arrived**, the delivery ratio, which
        is `final_mm / spent_mm` and which one earlier frame put at 1.011;
      * **does a nudge survive an advance**. That is the one nobody has
        measured. A sub-frame move does not touch the frame counter, so a
        correction should still be in the film's position when the next frame
        arrives -- and if it is, frame N's first reading is not about frame N's
        own error at all. The prediction is

            history[0].px  ~=  -final_mm(N-1) / mm_per_px

        and a reading near zero on every frame refutes it.
    """
    manifest = json.loads(
        (folder / ("roll.json" if (folder / "roll.json").exists()
                   else "survey.json")).read_text(encoding="utf-8"))
    rows = []
    for record in manifest.get("frames", []):
        held = ((record.get("registration") or {}).get("approved") or {})
        if not held:
            continue
        history = held.get("history") or [{}]
        rows.append({
            "frame": record.get("number"),
            "target_mm": held.get("target_mm"),
            "outcome": held.get("outcome"),
            "moves": held.get("moves", 0),
            "spent_mm": held.get("spent_mm", 0.0),
            "final_mm": held.get("final_mm"),
            "residual_mm": held.get("residual_mm"),
            "arrived_px": history[0].get("px"),
            "confidence": history[0].get("confidence"),
            "clamped": held.get("clamped"),
        })
    if not rows:
        print(f"\nno held frames in {folder}")
        return {"rows": []}

    scale = APERTURE_MM / 428.0
    print(f"\n-- what the holding delivered, from {folder.name}'s own "
          f"manifest --\n")
    print(f"  {'fr':>3} {'asked':>7} {'sent':>7} {'landed':>7} {'left':>6} "
          f"{'mv':>3} {'arrived':>9} {'conf':>6}  outcome")
    for r in rows:
        def mm(key, width=7):
            v = r.get(key)
            return f"{v:+{width}.3f}" if isinstance(v, (int, float)) else f"{'-':>{width}}"
        arrived = r["arrived_px"]
        # A displacement the correlator refused is not a small reading, it is
        # no reading: below the floor `register` is matching noise, and the
        # number it returns is the position of the tallest bump in it.
        trusted = (r["confidence"] or 0) >= CONFIDENCE_FLOOR
        shown = ("-" if arrived is None
                 else f"{arrived:+d} px" if trusted else "(refused)")
        print(f"  {r['frame']:>3} {mm('target_mm')} {r['spent_mm']:>7.3f} "
              f"{mm('final_mm')} {mm('residual_mm', 6)} {r['moves']:>3} "
              f"{shown:>9} {(r['confidence'] or 0):>6.1f}  {r['outcome']}")

    moved = [r for r in rows if r["moves"]]
    still = [r for r in rows if not r["moves"]]
    print(f"\n  {len(moved)} frame(s) moved, {len(still)} sent no command at "
          f"all")
    if still:
        print(f"    a frame that never moved reports outcome "
              f"'{still[0]['outcome']}' too -- which is why `moves` is the "
              f"column that matters")

    # `final_mm` is the total displacement from the reference, not the
    # distance this frame travelled: a frame arrives already carrying whatever
    # the last one was corrected by. What was delivered is the difference.
    ratios = []
    for r in moved:
        if r["final_mm"] is None or not r["spent_mm"]:
            continue
        arrived_mm = (r["arrived_px"] or 0) * scale
        ratios.append(abs(r["final_mm"] - arrived_mm) / abs(r["spent_mm"]))
    if ratios:
        print(f"  delivered per mm commanded: "
              f"{min(ratios):.3f}-{max(ratios):.3f}, median "
              f"{float(np.median(ratios)):.3f}   (one earlier frame: 1.011)")

    # Does a nudge survive an advance? The prediction, frame by frame.
    print(f"\n  does a nudge survive the advance? "
          f"(predicted arrival = final(N-1) / {scale:.5f} mm/px)")
    checked, agreed = 0, 0
    for before, after in zip(rows, rows[1:]):
        if before.get("final_mm") is None or after.get("arrived_px") is None:
            continue
        if (after.get("confidence") or 0) < CONFIDENCE_FLOOR:
            continue                  # no reading to compare the prediction to
        # Where the last frame was LEFT is where this one arrives: a
        # sub-frame move does not touch the frame counter, so the film
        # is still displaced by that much when the next frame reaches
        # the gate. Same sign, which the first version of this had
        # backwards -- and so reported 14 of 14 magnitudes agreeing
        # within a pixel as "DOES NOT AGREE".
        predicted = before["final_mm"] / scale
        seen = after["arrived_px"]
        checked += 1
        close = abs(predicted - seen) <= 2.0
        agreed += close
        print(f"    frame {after['frame']:>2}: predicted {predicted:+6.1f} px, "
              f"saw {seen:+4d} px   {'agrees' if close else 'DOES NOT AGREE'}")
    if checked:
        print(f"\n    {agreed} of {checked} within 2 px. "
              + ("The correction carries across the advance, so a frame's "
                 "first reading is mostly the LAST frame's correction."
                 if agreed > checked / 2 else
                 "The correction does NOT survive the advance -- each frame "
                 "arrives where the transport put it, independent of the last."))
    return {"rows": rows, "moved": len(moved), "still": len(still),
            "carry_checked": checked, "carry_agreed": agreed}

def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("rolls/2026-09-21"),
                    help="a roll folder holding prescanNN.tif, or a library")
    ap.add_argument("--walk", type=Path, default=None,
                    help="a walk-X.json from tools/roll_registration_walk.py; "
                         "reads its two-passes-per-frame pairing, which is the "
                         "only thing that measures the noise floor")
    ap.add_argument("--dpi", type=int, default=300,
                    help="the resolution to select when reading a library")
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the whole result here")
    ap.add_argument("--glob", default=None,
                    help="the filename pattern to read, for a cohort not "
                         "named prescanNN.tif -- walk A's passes came from an "
                         "earlier probe and are A01_p1.tif and so on")
    ap.add_argument("--held", type=Path, default=None,
                    help="a roll folder whose frames were held to approved "
                         "positions; reports what the holding actually "
                         "delivered, read back from that run's own manifest")
    ap.add_argument("--ensemble", action="store_true",
                    help="replay the walk through the ensemble that decides "
                         "corrections, frame by frame in walk order, using "
                         "only what each frame could have known at the time")
    args = ap.parse_args(argv)

    if args.held and not args.walk and not args.ensemble:
        # Reporting on a finished roll needs its manifest and nothing else.
        report_held(args.held)
        return 0

    pairs: list[dict] = []
    if args.walk:
        if not args.walk.is_file():
            print(f"no walk log at {args.walk}")
            return 1
        images, pairs = load_walk(args.walk)
        if not images:
            print(f"no readable passes named in {args.walk}")
            return 1
    else:
        if not args.root.is_dir():
            print(f"no folder at {args.root}")
            return 1
        if args.glob:
            images = [(q.name, tiff.read(str(q)).astype(np.float64))
                      for q in sorted(args.root.glob(args.glob))]
        else:
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

    scale = mm_per_px(images[0][1].shape[1])
    base = result.get("base", {}).get("mean")
    if base:
        result["position"] = report_position(rows, images, base, scale)

    if args.held:
        result["held"] = report_held(args.held)

    if args.ensemble:
        result["ensemble"] = report_ensemble(images)

    if pairs:
        noise = noise_floor(pairs, args.dpi, scale)
        report_noise(noise, scale)
        result["noise"] = noise

    if args.json:
        args.json.write_text(json.dumps(result, indent=2, default=str),
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
