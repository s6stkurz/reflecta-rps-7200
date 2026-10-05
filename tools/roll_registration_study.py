#!/usr/bin/env python3
"""What the roll's registration detectors actually say about real prescans.

    uv run python tools/roll_registration_study.py --root rolls/2026-09-21
    uv run python tools/roll_registration_study.py --replay --since 2026-09-22

`--replay` is the other question, and needs no pictures at all: where each
held frame of a roll should have arrived, from the sub-frame moves before it
-- the walk's aims and the holds of the frames before -- and where it did,
read back from `roll.json` and `survey.json`; and the first decision today's
`hold_plan` makes on that arrival. A frame whose hold never looked, because
holding was off, is measured from the library (`--library`) where it can be.

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
    RECORDED_SEARCH_MM,
    SAME_PICTURE,
    aligned_correlation,
    cohort,
    entry_named,
)
from rps7200 import library, tiff            # noqa: E402
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
    FRAME_WIDTH_MM,
    FRAME_WIDTH_UNITS,
    PRESCAN_COLUMNS,
    GAP_FLATNESS,
    GAP_LEVEL_SIGMA,
    GAP_MIN_RUN,
    MAX_DY_PX,
    MAX_REGISTRATION_MM,
    frame_contrast,
    gap_edges,
    hold_plan,
    measure_shift_mm,
    reach_px,
    registration,
    registration_error_mm,
    say_hold,
)
from rps7200.protocol import MM_PER_UNIT, units  # noqa: E402
from rps7200.session import manifest_settings, renumbered  # noqa: E402
from rps7200.uniformity import luminance, register  # noqa: E402

#: The image a 135 frame carries in the older gap model, against the 36.49 mm
#: aperture. The difference is all the room the film has in that model, and
#: half of it is the most a frame can be off while still losing no picture --
#: which is the tolerance this study is measured against.
#:
#: The driver's own, not a copy: `framing.FRAME_WIDTH_MM`, which still aims a
#: roll with no edge reader. The frame-edge work measured a frame *wider*
#: than the aperture (`framing.FRAME_WIDTH_UNITS`), in which no such band
#: exists; this study describes the gap model, and says so where it prints.
FRAME_MM = FRAME_WIDTH_MM


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
    print(f"  nothing lost while |e| <= {no_loss:.4f} mm = {no_loss/scale:.2f} px"
          f"  -- in the {FRAME_MM} mm gap model; a frame measured by its edges "
          f"is {FRAME_WIDTH_UNITS} units, wider than the aperture, and loses "
          f"picture at any offset")
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
    # The driver's reach for passes this wide (`framing.reach_px`), which is
    # what `measure_shift_mm` searches: worked out from the resolution it was
    # 106 columns against the driver's 105, and a confidence is only
    # comparable at the reach it was searched at.
    reach = reach_px(width)
    print(f"\n-- register: confidence and dy over {len(rows)} frames " + "-" * 18)
    print(f"   reach {reach} px ({reach * scale / MM_PER_UNIT:.1f} units), "
          f"floor {CONFIDENCE_FLOOR}, "
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
    rows = []
    for pair in pairs:
        a, b = pair["images"]
        # The driver's reach for a pass this wide, as `report` uses.
        dy, dx, conf = register(luminance(a), luminance(b),
                                max_shift=reach_px(b.shape[1]))
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


def looked(look: dict | None, width: float) -> tuple[bool | None, int]:
    """Whether the driver believed a stored look, and the reach it searched.

    Its own verdict where the look records one -- `measure_shift_mm`'s
    reason, "matched" or "matched with its rows reversed" -- which is the one
    reading of a stored look that needs no floor at all. A confidence compared
    against `CONFIDENCE_FLOOR` means something only at the reach it was
    searched at, the look's own `reach_px` or the records' `RECORDED_SEARCH_MM`,
    and the floor may since have been fitted at another. So a look that gives
    no reason is judged against the floor only where the two reaches agree,
    and is None -- said, not guessed -- where they do not.
    """
    columns = int(round(width))
    if not look:
        return None, reach_px(columns, RECORDED_SEARCH_MM)
    reach = int(look.get("reach_px") or reach_px(columns, RECORDED_SEARCH_MM))
    reason = look.get("reason")
    if isinstance(reason, str) and reason:
        return (reason.startswith("matched")
                and "off the film axis" not in reason), reach
    if look.get("confidence") is None or reach != reach_px(columns):
        return None, reach
    return float(look["confidence"]) >= CONFIDENCE_FLOOR, reach


def say_look(look: dict | None, believed: bool | None, reach: int) -> str:
    """A stored look's confidence for a person, with the reach it is at."""
    if not look or look.get("confidence") is None:
        return "-"
    mark = {True: "", False: " refused", None: " ?"}[believed]
    return f"{float(look['confidence']):.1f}@{reach}{mark}"


def prescan_width(manifest: dict, held: dict | None = None) -> float:
    """Columns of the roll's prescans: the reference's own where the hold
    kept its shape, else what a pass at the roll's prescan resolution is.
    428 columns is a 300 dpi prescan, and a roll prescanned at 600 dpi was
    read at twice its real scale."""
    shape = (held or {}).get("reference_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2 and shape[1]:
        return float(shape[1])
    dpi = int(manifest_settings(manifest).get("prescan_resolution") or 300)
    return PRESCAN_COLUMNS * dpi / 300


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
      * **does a nudge survive an advance**. A sub-frame move does not touch
        the frame counter, so a correction should still be in the film's
        position when the next frame arrives -- and if it is, frame N's first
        reading is not about frame N's own error at all. The prediction here
        is the simple one,

            history[0].px  ~=  final(N-1) in columns

        which leaves out what the walk's own aims carried; `--replay` puts
        them in.

    In units, as every transport distance a person reads is.
    """
    manifest = json.loads(
        (folder / ("roll.json" if (folder / "roll.json").exists()
                   else "survey.json")).read_text(encoding="utf-8"))
    # Numbered by the strip, as the window and the roll tool read a manifest:
    # read raw, a merged or resumed one paired frames that were not
    # neighbours in the carry-over check below.
    manifest = renumbered(manifest)
    rows = []
    for record in manifest.get("frames", []):
        held = ((record.get("registration") or {}).get("approved") or {})
        if not held:
            continue
        history = held.get("history") or [{}]
        width = prescan_width(manifest, held)
        believed, reach = looked(history[0], width)
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
            "believed": believed, "reach_px": reach, "width": width,
            "look": history[0],
            "clamped": held.get("clamped"),
        })
    if not rows:
        print(f"\nno held frames in {folder}")
        return {"rows": []}

    print(f"\n-- what the holding delivered, from {folder.name}'s own "
          f"manifest, in units --\n")
    print(f"  {'fr':>3} {'asked':>7} {'sent':>7} {'landed':>7} {'left':>6} "
          f"{'mv':>3} {'arrived':>9} {'conf@reach':>15}  outcome")
    for r in rows:
        def u(key, width=7):
            v = r.get(key)
            return (f"{units(v):+{width}.1f}" if isinstance(v, (int, float))
                    else f"{'-':>{width}}")
        arrived = r["arrived_px"]
        # A displacement the correlator refused is not a small reading, it is
        # no reading: below the floor `register` is matching noise, and the
        # number it returns is the position of the tallest bump in it.
        shown = ("-" if arrived is None
                 else f"{arrived:+d} px" if r["believed"]
                 else "(refused)" if r["believed"] is False
                 else f"{arrived:+d} px?")
        print(f"  {r['frame']:>3} {u('target_mm')} "
              f"{units(r['spent_mm'] or 0.0):>7.1f} {u('final_mm')} "
              f"{u('residual_mm', 6)} {r['moves']:>3} {shown:>9} "
              f"{say_look(r['look'], r['believed'], r['reach_px']):>15}  "
              f"{r['outcome']}")

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
    #
    # Signed, both of them. The magnitudes were divided, which is the mistake
    # CLAUDE.md names: a frame that moved the wrong way by what it was sent
    # scored 1.0 "delivered". The commanded distance is signed by where the
    # hold was sent -- from where the frame arrived towards its target -- so
    # a delivery the wrong way reads negative. And only from an arrival the
    # driver stood behind: a refused `px` is the tallest bump in noise, which
    # the table already calls refused.
    ratios = []
    for r in moved:
        if (r["final_mm"] is None or not r["spent_mm"]
                or r["target_mm"] is None or r["arrived_px"] is None
                or not r["believed"]):
            continue
        arrived_mm = r["arrived_px"] * APERTURE_MM / r["width"]
        commanded = float(np.copysign(abs(r["spent_mm"]),
                                      r["target_mm"] - arrived_mm))
        ratios.append((r["final_mm"] - arrived_mm) / commanded)
    if ratios:
        print(f"  delivered per unit commanded, signed: "
              f"{min(ratios):.3f}-{max(ratios):.3f}, median "
              f"{float(np.median(ratios)):.3f}   (one earlier frame: 1.011)"
              + ("  -- a negative one moved the wrong way"
                 if min(ratios) < 0 else ""))

    # Does a nudge survive an advance? The prediction, frame by frame.
    columns = rows[0]["width"]
    print(f"\n  does a nudge survive the advance? (predicted arrival = "
          f"final(N-1), {APERTURE_MM / columns / MM_PER_UNIT:.3f} units a "
          f"column)")
    checked, agreed = 0, 0
    for before, after in zip(rows, rows[1:]):
        if before.get("final_mm") is None or after.get("arrived_px") is None:
            continue
        if (not isinstance(before.get("frame"), int)
                or after.get("frame") != before["frame"] + 1):
            continue                  # not neighbours: nothing carried over
        if not after.get("believed"):
            continue                  # no reading to compare the prediction to
        # Where the last frame was LEFT is where this one arrives: a
        # sub-frame move does not touch the frame counter, so the film
        # is still displaced by that much when the next frame reaches
        # the gate. Same sign, which the first version of this had
        # backwards -- and so reported 14 of 14 magnitudes agreeing
        # within a pixel as "DOES NOT AGREE".
        predicted = before["final_mm"] / (APERTURE_MM / after["width"])
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


# --- the replay: where each frame should have arrived -----------------------
#
# A sub-frame move never touches the frame counter, so it stays on the strip
# through every whole-frame advance -- and through the rewind from the walk's
# end back to the roll's first frame. Every frame therefore arrives where the
# moves before it left the film, and that is predictable offline from the
# manifests alone:
#
#   * the walk aims frame j by a_j, measured from the picture it arrived at,
#     and takes frame j's reference after the aim;
#   * the roll's first frame k arrives at the sum of the walk's aims made
#     after its reference, a_j for every j > k;
#   * every later frame N arrives where the frame before it, M, was left,
#     less the walk's aims at the frames it passed on the way, M < j <= N --
#     each of them was in the reference it is measured against.
#
# On 2026-10-05 that predicted 148 of 156 delivered frames to within 3.5
# units. Most of the rest arrived about one perforation from the prediction,
# on slide rolls: an advance that slipped a sprocket hole.

#: One perforation of 135 film, in units. An arrival this far from where the
#: carry-over put it is an advance that slipped a sprocket hole rather than a
#: move carried: 2026-10-03-221354's second frame arrived 46 units out.
PERFORATION_UNITS = 44.9

#: How close to a perforation a miss has to be to be flagged as one, in units.
PERFORATION_SLACK_UNITS = 5.0

#: How close an arrival has to land to its prediction to count as predicted,
#: in units: the figure the 2026-10-05 audit used.
PREDICTED_WITHIN_UNITS = 3.5


def sent_units(record: dict) -> float | None:
    """What a hold or an aim sent, signed, at the distance the law says each
    command travels; None for one that moved and kept no record of how."""
    moves = record.get("moves_sent")
    if moves:
        return sum(abs(float(m.get("asked_units") or 0.0))
                   * (1.0 if int(m.get("action", 0)) == 0 else -1.0)
                   for m in moves)
    return 0.0 if not record.get("moves") else None


def aim_units(correction: dict) -> float | None:
    """How far a walk's aim moved the film, in units.

    What it measured once it had moved -- `final_mm`, against the picture it
    arrived at, which is what an aim holds its frame against -- or, where it
    could not measure, what it sent. Nothing for an aim that sent nothing.
    """
    if not correction or not (correction.get("moves")
                              or correction.get("moves_sent")):
        return 0.0
    if correction.get("final_mm") is not None:
        return units(correction["final_mm"])
    return sent_units(correction)


def _last_move(record: dict) -> float | None:
    moves = record.get("moves_sent") or []
    if not moves:
        return None
    m = moves[-1]
    return (abs(float(m.get("asked_units") or 0.0))
            * (1.0 if int(m.get("action", 0)) == 0 else -1.0))


def _between(aims: dict[int, float | None], after: int,
             upto: int | None = None) -> float | None:
    """The walk's aims at frames after ``after``, up to ``upto`` if given; None
    if one of them moved the film by an amount nobody knows."""
    total = 0.0
    for number, aim in aims.items():
        if number > after and (upto is None or number <= upto):
            if aim is None:
                return None
            total += aim
    return total


def remeasure(folder: Path, number: int, record: dict,
              library_root: Path | None) -> tuple[float | None, dict] | None:
    """A frame's arrival measured offline: its prescan, as the roll filed it,
    against the reference `approved.json` names for it -- for a frame whose
    hold never looked, because holding was off. None where either is gone."""
    if library_root is None:
        return None
    try:
        approved = json.loads((folder / "approved.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    named = next((f.get("reference_entry") for f in approved.get("frames") or ()
                  if f.get("number") == number), None)
    reference = entry_named(library_root, named)
    entry = entry_named(library_root, record.get("entry"))
    if reference is None or entry is None or not (entry / "prescan.tif").exists():
        return None
    try:
        image, info = library.corrected(reference)
        now = tiff.read(str(entry / "prescan.tif"))
    except (OSError, ValueError):
        return None
    if info.get("corrected") != "applied":
        return None
    return measure_shift_mm(image, now)


def replay(folder: Path, library_root: Path | None = None) -> dict:
    """Every held frame of one roll: where the carry-over put it, where it
    arrived, and what the hold decides from there today.

    The decision is `hold_plan`'s first, on the arrival the hold measured --
    or, for a frame that was never measured because holding was off,
    measured now (`remeasure`) or, failing that, the prediction, and said so.
    """
    manifest = renumbered(json.loads((folder / "roll.json").read_text(
        encoding="utf-8")))
    walk: dict = {}
    if (folder / "survey.json").exists():
        try:
            walk = renumbered(json.loads((folder / "survey.json").read_text(
                encoding="utf-8")))
        except (OSError, ValueError):
            walk = {}
    aims: dict[int, float | None] = {}
    last_aim = None
    for record in walk.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        correction = (record.get("registration") or {}).get("correction") or {}
        aims[number] = aim_units(correction)
        last_aim = _last_move(correction) or last_aim

    rows: list[dict] = []
    left: float | None = None             # where the last frame was left
    at: int | None = None                 # and which frame that was
    last_move = last_aim
    for record in manifest.get("frames") or ():
        number = record.get("number")
        if not isinstance(number, int):
            continue
        held = (record.get("registration") or {}).get("approved") or {}
        if at is None:
            predicted = _between(aims, number)
            how = "first"
        elif number <= at:
            # Back along the strip: a resumed or merged run, whose film came
            # from wherever the run before left it.
            predicted, how = None, "new run"
        elif left is None:
            # The frame before moved by an amount its record does not keep.
            predicted, how = None, "unknown"
        else:
            passed = _between(aims, at, number)
            predicted = None if passed is None else left - passed
            how = "carried"
        if not held:
            # Scanned without a hold: nothing moved it, so it was left where
            # it arrived.
            left, at = predicted, number
            continue

        width = prescan_width(manifest, held)
        per_column = APERTURE_MM / width / MM_PER_UNIT
        history = held.get("history") or []
        first = history[0] if history else None
        believed, reach = looked(first, width)
        source = "stored"
        arrived = held.get("arrived_units")
        if arrived is None and first and first.get("px") is not None:
            arrived = first["px"] * per_column
        if first is None:
            measured = remeasure(folder, number, record, library_root)
            if measured is not None:
                mm, detail = measured
                first = detail
                believed = mm is not None
                reach = int(detail.get("reach_px") or reach_px(int(width)))
                arrived = detail["px"] * per_column
                source = "re-measured"

        look_mm = None
        if believed and arrived is not None:
            look_mm = arrived * MM_PER_UNIT
        elif first is None and predicted is not None:
            look_mm, source = predicted * MM_PER_UNIT, "predicted"
        target = held.get("target_mm")
        want, decision = (hold_plan(float(target), [look_mm])
                          if target is not None else (None, "?"))

        miss = (arrived - predicted
                if arrived is not None and predicted is not None else None)
        perforation = (miss is not None and abs(abs(miss) - PERFORATION_UNITS)
                       <= PERFORATION_SLACK_UNITS)
        rows.append({
            "frame": number, "how": how,
            "target_units": None if target is None else units(target),
            "predicted_units": predicted, "arrived_units": arrived,
            "miss_units": miss, "believed": believed, "reach_px": reach,
            "look": first, "source": source,
            "stored": held.get("outcome"), "decision": decision,
            "move_units": None if want is None else units(want),
            "perforation": perforation, "after_move_units": last_move,
            "moves": held.get("moves", 0),
        })

        if held.get("final_mm") is not None:
            left = units(held["final_mm"])
        else:
            base = arrived if believed and arrived is not None else predicted
            sent = sent_units(held)
            left = None if base is None or sent is None else base + sent
        at = number
        last_move = _last_move(held) or last_move
    return {"roll": folder.name, "film": manifest.get("film"), "rows": rows,
            "aims": aims}


def roll_date(folder: Path) -> str | None:
    """The day a roll was made: its folder's name where that starts with a
    date, else the day its `roll.json` says it started."""
    name = folder.name
    if len(name) >= 10 and name[4] == "-" and name[7] == "-" \
            and name[:4].isdigit():
        return name[:10]
    try:
        started = json.loads((folder / "roll.json").read_text(
            encoding="utf-8")).get("started")
    except (OSError, ValueError):
        return None
    return started[:10] if isinstance(started, str) else None


def report_replay(results: list[dict]) -> dict:
    """The per-roll tables, then what they add up to."""
    def u(v, width=7):
        return f"{v:+{width}.1f}" if isinstance(v, (int, float)) else \
            f"{'-':>{width}}"

    for result in results:
        aims = {n: a for n, a in result["aims"].items() if a}
        said = (", ".join(f"{n}: {a:+.1f}" for n, a in sorted(aims.items()))
                if aims else "none")
        print(f"\n-- {result['roll']} ({result['film'] or '?'}); the walk's "
              f"aims, in units: {said}")
        print(f"  {'fr':>3} {'target':>7} {'predict':>7} {'arrived':>7} "
              f"{'miss':>6}  {'look':<22} {'stored':<14} now")
        for r in result["rows"]:
            look = say_look(r["look"], r["believed"], r["reach_px"])
            if r["source"] != "stored":
                look = f"{r['source']} {look}" if r["look"] else r["source"]
            now = r["decision"] + (f" {r['move_units']:+.1f}"
                                   if r["move_units"] is not None else "")
            flag = ""
            if r["perforation"]:
                after = r["after_move_units"]
                flag = ("  PERFORATION? after "
                        + ("no move" if after is None else
                           f"a {after:+.1f} move "
                           f"({'forward' if after > 0 else 'back'})"))
            if r["how"] == "new run":
                flag += "  (a new run: nothing to carry from)"
            elif r["how"] == "unknown":
                flag += "  (the frame before moved by an unrecorded amount)"
            print(f"  {r['frame']:>3} {u(r['target_units'])} "
                  f"{u(r['predicted_units'])} {u(r['arrived_units'])} "
                  f"{u(r['miss_units'], 6)}  {look:<22} "
                  f"{str(r['stored']):<14} {now}{flag}")

    rows = [r for result in results for r in result["rows"]]
    # Measured: stored by the hold, or measured now for a frame whose hold
    # never looked. Never the prediction standing in for a look.
    judged = [r for r in rows if r["believed"] and r["miss_units"] is not None
              and r["source"] in ("stored", "re-measured")]
    close = [r for r in judged if abs(r["miss_units"]) <= PREDICTED_WITHIN_UNITS]
    perforated = [(result["roll"], r) for result in results
                  for r in result["rows"] if r["perforation"]]
    print(f"\n== {len(results)} rolls, {len(rows)} held frames")
    print(f"   arrival within {PREDICTED_WITHIN_UNITS} units of the carry-over "
          f"prediction: {len(close)} of {len(judged)} measured and believed "
          f"({sum(1 for r in judged if r['source'] == 're-measured')} of them "
          f"measured now, holding having been off)")
    refused = [r for r in rows if r["believed"] is False
               and r["miss_units"] is not None]
    off_axis = [r for r in refused
                if "off the film axis" in str((r["look"] or {}).get("reason"))]
    for name, group, what in (
            ("refused by the off-axis gate", off_axis,
             "a real peak the gate would not believe"),
            ("refused as too weak", [r for r in refused if r not in off_axis],
             "the tallest bump in noise, as often as not")):
        if group:
            near = sum(1 for r in group
                       if abs(r["miss_units"]) <= PREDICTED_WITHIN_UNITS)
            print(f"   {name}: {near} of {len(group)} within "
                  f"{PREDICTED_WITHIN_UNITS} units anyway -- its px is {what}")
    for roll, r in perforated:
        after = r["after_move_units"]
        print(f"   perforation-sized: {roll} frame {r['frame']} missed by "
              f"{r['miss_units']:+.1f} units, after "
              + ("no move" if after is None else f"a {after:+.1f} move"))
    others = [(result["roll"], r) for result in results for r in result["rows"]
              if r in judged and r not in close and not r["perforation"]]
    for roll, r in others:
        print(f"   off the prediction: {roll} frame {r['frame']} by "
              f"{r['miss_units']:+.1f} units ({r['how']})")

    print("\n   stored outcome -> the first decision today")
    table: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (str(r["stored"]), r["decision"])
        table[key] = table.get(key, 0) + 1
    for (stored, now), n in sorted(table.items()):
        print(f"     {stored:<14} -> {now:<12} {n:4d}")
    for outcome in ("budget", "off"):
        these = [r for r in rows if r["stored"] == outcome]
        if not these:
            continue
        # What each now decides, and on what: a stored look, one measured
        # now, the prediction standing in for one, or nothing at all.
        on: dict[tuple[str, str], int] = {}
        for r in these:
            source = (r["source"] if r["look"] is not None
                      or r["source"] == "predicted" else "no look")
            on[(r["decision"], source)] = on.get((r["decision"], source), 0) + 1
        said = []
        for (decision, source), n in sorted(on.items()):
            where = {"stored": "the stored look",
                     "re-measured": "the look measured now",
                     "predicted": "the prediction",
                     "no look": "no look on disk and nothing to predict "
                                "from"}.get(source, source)
            said.append(f"{n} {decision} on {where}")
        moving = sum(n for (decision, _s), n in on.items() if decision == "move")
        print(f"   stored '{outcome}': {len(these)} -- {moving} now move; "
              + "; ".join(said))
    return {"rows": len(rows), "predicted": [len(close), len(judged)],
            "refused": len(refused), "refused_off_axis": len(off_axis),
            "perforations": [(roll, r["frame"], round(r["miss_units"], 1))
                             for roll, r in perforated],
            "transitions": {f"{a} -> {b}": n for (a, b), n in table.items()}}


def run_replay(args) -> int:
    """`--replay`: the rolls named, or every roll; a table each, and the sum."""
    if args.replay:
        folders = [Path(name) if Path(name).is_dir() else args.rolls_root / name
                   for name in args.replay]
    else:
        folders = sorted(p for p in args.rolls_root.glob("*")
                         if (p / "roll.json").exists())
    if args.since:
        folders = [f for f in folders if (roll_date(f) or "") >= args.since]
    library_root = args.library if args.library.is_dir() else None
    results = []
    for folder in folders:
        if not (folder / "roll.json").exists():
            print(f"no roll.json in {folder}")
            continue
        try:
            result = replay(folder, library_root)
        except (OSError, ValueError) as exc:
            print(f"{folder}: {exc}")
            continue
        if result["rows"]:
            results.append(result)
    if not results:
        print("no held frames in the rolls named")
        return 1
    summary = report_replay(results)
    seen = sorted({r["stored"] for res in results for r in res["rows"]}
                  | {r["decision"] for res in results for r in res["rows"]},
                  key=str)
    # The codes in the tables, as a person reads them. "move" is no outcome:
    # it is the hold going on to move by the residual.
    print("\n   " + "\n   ".join(f"{o}: {say_hold(o)}" for o in seen
                                  if o and o != "move"))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "rolls": results},
                                        indent=2, default=str),
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


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
    ap.add_argument("--replay", nargs="*", default=None, metavar="ROLL",
                    help="the carry-over, per held frame: where the moves "
                         "before it should have put it, where it arrived, "
                         "and the first decision the hold makes today. ROLL "
                         "is a folder or a name under --rolls-root; every "
                         "roll when none is given")
    ap.add_argument("--rolls-root", type=Path, default=Path("rolls"),
                    help="--replay: the folder of roll folders (default ./rolls)")
    ap.add_argument("--library", type=Path, default=Path("library"),
                    help="--replay: the library a frame whose hold never "
                         "looked is measured from (default ./library)")
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="--replay: only rolls made on or after this day")
    args = ap.parse_args(argv)

    if args.replay is not None:
        return run_replay(args)

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
