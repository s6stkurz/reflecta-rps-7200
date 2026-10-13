#!/usr/bin/env python3
"""What the roll's registration detectors actually say about real prescans.

    uv run python tools/roll_registration_study.py --root rolls/2026-09-21
    uv run python tools/roll_registration_study.py --replay --since 2026-09-22

`--replay` is the other question: where each held frame of a roll should have
arrived, from the sub-frame moves before it in its own run -- the walk's aims
and the holds of the frames before -- and where it did, read back from
`roll.json` and `survey.json`; and the first decision the hold makes today on
that arrival, as today's gate judges it. The library (`--library`) says which
run each frame came from, and holds the pictures each stored first look is
measured again on -- and, for a frame whose hold never looked because holding
was off, the pictures it is measured on for the first time. Without it the
manifests are all there is, and every look is read by the verdict it stored.

Offline. Reads stored prescans and reports, per frame, what each of the three
detectors in `rps7200/framing.py` returns and *why* -- not just whether it
fired, but how close it came.

Why a separate tool rather than reading `survey.json`: the manifest records the
answer, and the answer on a loaded strip is an offset of 0.0 for every frame.
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
    silently, and `registration_error` then answers `0.0, "no gap in view --
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
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import NamedTuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration_margin import (            # noqa: E402
    RECORDED_SEARCH_UNITS,
    SAME_PICTURE,
    Gate,
    Hold,
    Pictures,
    _label_number,
    _roll_of,
    _time,
    aligned_correlation,
    cohort,
    entry_named,
    find_references,
    recorded_reading,
    reproduces,
    stored_holds,
)
from rps7200 import library, tiff            # noqa: E402
from rps7200.console import use_utf8_stdout  # noqa: E402
from rps7200.framing import (                # noqa: E402
    APERTURE_UNITS,
    BLANK_CONTRAST,
    HOLD_TOLERANCE_UNITS,
    PRESCAN_ROWS_300,
    device_shape,
    predict_offset,
    CLEAR_RATIO,
    CONFIDENCE_FLOOR,
    FRAME_WIDTH_UNITS,
    GAP_FLATNESS,
    GAP_LEVEL_SIGMA,
    GAP_MIN_RUN,
    MAX_DY_PX,
    frame_contrast,
    gap_edges,
    MATCH_SMOOTH,
    hold_plan,
    match_against,
    measure_shift,
    reach_px,
    registration,
    say_hold,
    aim_units,
    sent_units,
    units_of,
)
from rps7200.session import (                # noqa: E402
    PREVIOUS,
    manifest_settings,
    renumbered,
)
import roll_registration_history as history  # noqa: E402
from roll_registration_history import (      # noqa: E402
    HISTORIC_FRAME_UNITS,
    MAX_GAP_UNITS,
    MAX_REGISTRATION_UNITS,
    registration_error,
)

#: The image a 135 frame carries in the older gap model, against the 345.2
#: unit aperture. The difference is all the room the film has in that model,
#: and half of it is the most a frame can be off while still losing no
#: picture -- which is the tolerance this study is measured against.
#:
#: The 36 mm model's, retired from the driver on 2026-10-13 and kept in
#: `roll_registration_history` so this study computes what it computed. The
#: frame-edge work measured a frame *wider* than the aperture
#: (`framing.FRAME_WIDTH_UNITS`), which every roll is aimed by now and in
#: which no such band exists; this study describes the gap model, and says so
#: where it prints.
FRAME_UNITS = HISTORIC_FRAME_UNITS


def units_per_px(width: int) -> float:
    """One column of a pass this wide on the hold's scale, as every distance
    the driver measures and stores is."""
    return APERTURE_UNITS / width


def gate_margin(image: np.ndarray) -> tuple[float, float]:
    """How close `film_bounds` came to firing, on each axis.

    Its gate is ``percentile(profile, 98) >= median(profile) * CLEAR_RATIO``
    (`framing.py:121-124`), so this is that ratio. Below `CLEAR_RATIO` the
    function returns the whole window and every number downstream of it --
    `offset_units`, `shortfall_units` -- is a fallback rather than a reading.
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
    ``(0, 0)`` which `registration_error` reads as "registered".

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
    scale = units_per_px(width)
    marks = registration(image)
    left, right = gap_edges(image)
    value, reason = registration_error(image)
    runs = gap_runs(image, min_run=GAP_MIN_RUN)
    anchored = {r for r in runs if r[0] == 0 or r[0] + r[1] >= width}
    gx, gy = gate_margin(image)
    return {
        "name": name,
        "contrast": round(frame_contrast(image), 4),
        "gate_x": round(gx, 3),
        "gate_y": round(gy, 3),
        "fired": gx >= CLEAR_RATIO or gy >= CLEAR_RATIO,
        "offset_units": marks["offset_units"],
        "shortfall_units": marks["shortfall_units"],
        "gap_left": left,
        "gap_right": right,
        "error_units": value,
        "reason": reason,
        "runs": runs,
        "stray": sorted(set(runs) - anchored),
        "units_per_px": round(scale, 4),
    }


def report(rows: list[dict], images: list[tuple[str, np.ndarray]],
           dpi: int) -> dict:
    width = images[0][1].shape[1]
    scale = units_per_px(width)
    no_loss = (APERTURE_UNITS - FRAME_UNITS) / 2.0

    print(f"\n{len(rows)} prescans, {width} px across, {scale:.4f} units/px")
    print(f"aperture {APERTURE_UNITS:.1f} units, frame {FRAME_UNITS:.1f} units")
    print(f"  nothing lost while |e| <= {no_loss:.2f} units = {no_loss/scale:.2f} px"
          f"  -- in the {FRAME_UNITS:.1f}-unit gap model; a frame measured by its "
          f"edges is {FRAME_WIDTH_UNITS} units, wider than the aperture, and "
          f"loses picture at any offset")
    print(f"  a detector error above    {MAX_REGISTRATION_UNITS:.2f} units = "
          f"{MAX_REGISTRATION_UNITS/scale:.2f} px")
    print(f"  so the actionable band is {no_loss:.2f}..{MAX_REGISTRATION_UNITS:.2f} "
          f"units = {no_loss/scale:.2f}..{MAX_REGISTRATION_UNITS/scale:.2f} px\n")

    # -- 1. film_bounds ----------------------------------------------------
    print("-- film_bounds: how close to firing " + "-" * 40)
    print(f"   gate is percentile(p,98)/median >= CLEAR_RATIO ({CLEAR_RATIO})\n")
    print(f"   {'frame':<16} {'contrast':>8} {'gate x':>7} {'gate y':>7} "
          f"{'fired':>6} {'offset u':>10} {'shortfall':>10}")
    for r in rows:
        print(f"   {r['name']:<16} {r['contrast']:>8.4f} {r['gate_x']:>7.3f} "
              f"{r['gate_y']:>7.3f} {str(r['fired']):>6} "
              f"{r['offset_units']:>10.2f} {r['shortfall_units']:>10.2f}")
    gates = [r["gate_x"] for r in rows]
    fired = sum(1 for r in rows if r["fired"])
    print(f"\n   fired on {fired}/{len(rows)}. "
          f"gate x: min {min(gates):.3f}, median {np.median(gates):.3f}, "
          f"max {max(gates):.3f}, needs {CLEAR_RATIO}")
    if max(gates) < CLEAR_RATIO:
        short = (CLEAR_RATIO - max(gates)) / CLEAR_RATIO * 100
        print(f"   VERDICT: never fires. The closest frame is {short:.0f}% short "
              f"of the gate.")
        print("   Every offset and shortfall above is the whole-window")
        print("   fallback, not a measurement. This is geometry, not tuning:")
        print("   the gate needs an EMPTY APERTURE in view and a loaded strip")
        print("   has none.")

    # -- 2. gap_edges ------------------------------------------------------
    print("\n-- gap_edges: what it saw, and what it discarded " + "-" * 24)
    print(f"   bright: level > med + {GAP_LEVEL_SIGMA}*MAD;  "
          f"flat: spread < {GAP_FLATNESS}*typical;  min run {GAP_MIN_RUN} px"
          f" = {GAP_MIN_RUN*scale:.2f} units\n")
    print(f"   {'frame':<16} {'L':>3} {'R':>3} {'error u':>9}  "
          f"{'reason':<34} stray runs (start,len)")
    for r in rows:
        value = "None" if r["error_units"] is None else f"{r['error_units']:.2f}"
        stray = ", ".join(f"({s},{n})" for s, n in r["stray"][:4]) or "-"
        print(f"   {r['name']:<16} {r['gap_left']:>3} {r['gap_right']:>3} "
              f"{value:>9}  {r['reason'][:34]:<34} {stray}")
    asserted = [r for r in rows if r["error_units"] == 0.0]
    # A run that starts within a gap's width of the edge is that gap, sitting
    # a few columns in because a sliver of the ADJACENT frame is also in view.
    # The anchored rule requires the run to start at column 0 exactly, so it
    # discards these and reports nothing -- and `registration_error` then
    # asserts the frame is registered. That is a false assertion about a frame
    # whose gap it could see.
    near = max(GAP_MIN_RUN * 4, 12)
    false_calls = []
    for r in rows:
        if r["error_units"] != 0.0:
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
    print(f"\n   Its deadband is {GAP_MIN_RUN} px = {GAP_MIN_RUN*scale:.2f} units "
          f"against a no-loss threshold of {no_loss:.2f} units.")
    print(f"   Those agree to {abs(GAP_MIN_RUN*scale - no_loss)/no_loss*100:.1f}% "
          f"-- at THIS resolution only. GAP_MIN_RUN is in pixels, so at 600 dpi")
    print(f"   it would be {GAP_MIN_RUN*scale/2:.2f} units and the detector would")
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
    # what `measure_shift` searches: worked out from the resolution, it
    # once searched 106 columns where the driver searched 105, and a
    # confidence is only comparable at the reach it was searched at.
    reach = reach_px(width)
    print(f"\n-- register: confidence and dy over {len(rows)} frames " + "-" * 18)
    print(f"   reach {reach} px ({reach * scale:.1f} units), "
          f"floor {CONFIDENCE_FLOOR}, "
          f"|dy| <= {MAX_DY_PX}\n")
    pairs, nulls = [], []
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            a, b = images[i][1], images[j][1]
            # Scored as the gate scores it (`framing.match_against`): the
            # shift found on the whole pictures, the confidence on the part
            # both show.
            m = match_against(a, b, reach)
            dy, dx, conf = m.dy, m.dx, m.confidence
            # The rows taken out as well, as `registration_margin` judges a
            # pair: along x alone, two passes of one frame three rows apart
            # correlate under the cut and are counted as a null.
            arb = aligned_correlation(a, b, int(dx), int(dy))
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
                  f" -- consistent with its fit on every stored hold "
                  f"(registration_margin.py --holds)")
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
            "width_px": width, "units_per_px": scale,
            "aperture_units": APERTURE_UNITS, "frame_units": FRAME_UNITS,
            "no_loss_units": no_loss,
            "actionable_px": [no_loss / scale, MAX_REGISTRATION_UNITS / scale],
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
        # The driver's reach for a pass this wide, as `report` uses, and its
        # score (`framing.match_against`).
        m = match_against(a, b, reach_px(b.shape[1]))
        dy, dx, conf = m.dy, m.dx, m.confidence
        rows.append({"number": pair["number"], "dx": int(dx), "dy": int(dy),
                     "confidence": round(float(conf), 1),
                     "units": round(float(dx) * scale, 3)})
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
    print(f"   {'frame':>6} {'dx px':>6} {'dy':>4} {'units':>9} {'confidence':>11}")
    for r in noise["rows"]:
        flag = "  <- below floor" if r["confidence"] < CONFIDENCE_FLOOR else ""
        print(f"   {r['number']:>6} {r['dx']:>6} {r['dy']:>4} {r['units']:>9.2f} "
              f"{r['confidence']:>11.1f}{flag}")
    print(f"\n   {noise['n']} pairs. |dx| max {noise['max_abs_px']} px "
          f"= {noise['max_abs_px']*scale:.2f} units, "
          f"sd {noise['sd_px']:.2f} px = {noise['sd_px']*scale:.2f} units")
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
    band = (APERTURE_UNITS - FRAME_UNITS) / 2.0 / scale
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
    so a width far past the ~19 units a 135 gap can be is a contaminated reading
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
    # A 135 gap is about 19 units. Anything much past that is the run walking
    # into a smooth part of the picture, and is excluded rather than averaged
    # in. The widest the 36 mm gap model allowed (`history.MAX_GAP_UNITS`).
    ceiling = int(round(MAX_GAP_UNITS / scale))
    print(f"   {'frame':<16} {'gap':>9} {'end px':>7} {'end u':>8}   ")
    ends, used = [], []
    for r, (_n, im) in zip(rows, images, strict=True):
        s, e = picture_start(im, base)
        wide = (e - s) > ceiling
        note = f"  <- {e-s} px wide, over {ceiling}: contaminated" if wide else ""
        print(f"   {r['name']:<16} {f'{s}..{e}':>9} {e:>7} {e*scale:>8.2f}{note}")
        ends.append(e)
        if not wide:
            used.append(e)
    if len(used) < 3:
        print("\n   too few clean readings to say anything about position.")
        return {"ends": ends, "used": used}

    arr = np.array(used, float)
    print(f"\n   {len(used)} clean of {len(ends)}. "
          f"mean {arr.mean():.1f} px = {arr.mean()*scale:.2f} units, "
          f"sd {arr.std():.2f} px = {arr.std()*scale:.2f} units, "
          f"range {arr.max()-arr.min():.0f} px")
    no_loss = (APERTURE_UNITS - FRAME_UNITS) / 2.0
    print(f"\n   Those starts are {arr.mean()*scale:.1f} units into a "
          f"{APERTURE_UNITS:.1f}-unit aperture. If the image really were "
          f"{FRAME_UNITS:.1f} units")
    print(f"   the most it could be is {no_loss:.2f} units, so either every frame "
          f"is losing")
    print("   picture at the far edge -- which the operator would see -- or")
    print(f"   the image on this film is about "
          f"{APERTURE_UNITS - 2*arr.mean()*scale:.1f} units wide, not "
          f"{FRAME_UNITS:.1f}.")
    print("   That second reading makes the slack larger than "
          f"MAX_REGISTRATION_UNITS ({MAX_REGISTRATION_UNITS:.2f} units),")
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
    the corrections landing and so manufactured advance errors of -7.4 units
    against a real wander of +/-1.6, then reported the ensemble refusing frames
    it would not refuse. What this measures is whether the members agree about
    where the film *is*, which is the question the gate actually asks.

    Costs no scanner time: `rolls/` keeps the prescans.
    """
    walk = history.walk()
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

        prior = predict_offset(walk.history(order), order)
        decision, detail, left, right = history.judge(walk, order, image,
                                                      prior=prior)
        if left.units is not None:
            walk.placed[order] = left.units

        if decision is None:
            outcome = "refused: " + detail.get("reason", "")[:44]
            refused += 1
        elif abs(decision) < HOLD_TOLERANCE_UNITS:
            outcome = f"in place ({detail['chose']})"
            in_place += 1
        else:
            outcome = f"move {decision:+.1f} units ({detail['chose']})"
            moved += 1

        def show(r):
            return f"{r.units:+7.1f}" if r.units is not None else "      -"

        print(f"  {name[:14]:<14} {walk.base.level:6.1f} {show(left)} "
              f"{show(prior)} {show(right)}  {outcome}")
        rows.append({
            "frame": name, "decision_units": decision,
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

    Its own verdict where the look records one -- `measure_shift`'s
    reason, "matched" or "matched with its rows reversed" -- which is the one
    reading of a stored look that needs no floor at all. A confidence compared
    against `CONFIDENCE_FLOOR` means something only at the reach it was
    searched at, the look's own `reach_px` or the records' `RECORDED_SEARCH_UNITS`,
    and the floor may since have been fitted at another. So a look that gives
    no reason is judged against the floor only where the two reaches agree,
    and is None -- said, not guessed -- where they do not.
    """
    columns = int(round(width))
    if not look:
        return None, reach_px(columns, RECORDED_SEARCH_UNITS)
    reach = int(look.get("reach_px") or reach_px(columns, RECORDED_SEARCH_UNITS))
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
    read at twice its real scale.

    The width is the device's (`framing.device_shape`), never a ratio of
    the dpi: 860 at 600 and 1292 at 900, where 428 scaled gives 856 and
    1284 and every unit reported is 0.5-0.6% off. A resolution the table
    has no row for raises ValueError rather than be guessed."""
    shape = (held or {}).get("reference_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2 and shape[1]:
        return float(shape[1])
    dpi = int(manifest_settings(manifest).get("prescan_resolution") or 300)
    sized = device_shape(dpi)
    if sized is None:
        raise ValueError(f"the width of a {dpi} dpi prescan is not known "
                         "(framing.DEVICE_PASS_SHAPES has no row for it)")
    return float(sized[1])


def report_held(folder: Path) -> dict:
    """What a roll's holding actually delivered, from the manifest it wrote.

    Read back rather than recomputed, per CLAUDE.md: the interesting thing is
    what the loop decided and measured while it ran, and a recomputation would
    quietly agree with itself.

    The question this answers is not "did it converge" -- `hold_plan` says that
    and would say it of a frame that never moved -- but three separate ones:

      * **did anything move at all**, which `moves: 0` and `spent_units: 0.0`
        settle, and which a manifest otherwise reports identically to success;
      * **how much of what was commanded arrived**, the delivery ratio, which
        is `final_units / spent_units` and which one earlier frame put at 1.011;
      * **does a nudge survive an advance**. A sub-frame move does not touch
        the frame counter, so a correction should still be in the film's
        position when the next frame arrives -- and if it is, frame N's first
        reading is not about frame N's own error at all. The prediction here
        is the simple one,

            history[0].px  ~=  final(N-1) in columns

        which leaves out what the walk's own aims carried; `--replay` puts
        them in.

    In units, as every transport distance a person reads is. A roll from
    before 2026-10-13 keeps its holds in millimetres (``target_mm`` and the
    rest), read here through `framing.units_of`.
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
            "target_units": units_of(held, "target"),
            "outcome": held.get("outcome"),
            "moves": held.get("moves", 0),
            "spent_units": units_of(held, "spent") or 0.0,
            "final_units": units_of(held, "final"),
            "residual_units": units_of(held, "residual"),
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
            return (f"{v:+{width}.1f}" if isinstance(v, (int, float))
                    else f"{'-':>{width}}")
        arrived = r["arrived_px"]
        # A displacement the correlator refused is not a small reading, it is
        # no reading: below the floor `register` is matching noise, and the
        # number it returns is the position of the tallest bump in it.
        shown = ("-" if arrived is None
                 else f"{arrived:+d} px" if r["believed"]
                 else "(refused)" if r["believed"] is False
                 else f"{arrived:+d} px?")
        print(f"  {r['frame']:>3} {u('target_units')} "
              f"{r['spent_units'] or 0.0:>7.1f} {u('final_units')} "
              f"{u('residual_units', 6)} {r['moves']:>3} {shown:>9} "
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

    # `final_units` is the total displacement from the reference, not the
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
        if (r["final_units"] is None or not r["spent_units"]
                or r["target_units"] is None or r["arrived_px"] is None
                or not r["believed"]):
            continue
        arrived = r["arrived_px"] * APERTURE_UNITS / r["width"]
        commanded = float(np.copysign(abs(r["spent_units"]),
                                      r["target_units"] - arrived))
        ratios.append((r["final_units"] - arrived) / commanded)
    if ratios:
        print(f"  delivered per unit commanded, signed: "
              f"{min(ratios):.3f}-{max(ratios):.3f}, median "
              f"{float(np.median(ratios)):.3f}   (one earlier frame: 1.011)"
              + ("  -- a negative one moved the wrong way"
                 if min(ratios) < 0 else ""))

    # Does a nudge survive an advance? The prediction, frame by frame.
    columns = rows[0]["width"]
    print(f"\n  does a nudge survive the advance? (predicted arrival = "
          f"final(N-1), {APERTURE_UNITS / columns:.3f} units a "
          f"column)")
    checked, agreed = 0, 0
    for before, after in zip(rows, rows[1:]):
        if before.get("final_units") is None or after.get("arrived_px") is None:
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
        predicted = before["final_units"] / (APERTURE_UNITS / after["width"])
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
# **Within one run, and only after the walk it is measured against.**
# `roll.json` keeps each frame's latest run, so two neighbours in it can come
# from runs an hour apart, with that run's holds, a stop or another walk
# between them. Chained, the second was "predicted" from a neighbour the film
# had long left: 2026-09-27 frame 2, from the 09:41 run, missed by 58.9 units
# a prediction made from frame 1 of the 09:26 run, whose own frame 2 had
# arrived where the model put it. So the library says which run each frame
# came from (`Shelf`, `Runs`): a frame is carried from the one before it only
# where that is the frame its own run scanned just before it, and predicted
# from the walk's aims only where nothing was scanned between the picture it
# was held to and it. A frame held to an earlier walk than the one
# `survey.json` keeps has none either -- the aims it passed through are not
# kept -- and every frame without a prediction says why.
#
# **The first decision is today's**, on each stored first look judged by
# today's gate (`judge_first`): measured again on the library's pictures
# where it was searched at another reach, judged from its own numbers where
# it was searched at today's, and only where its pictures are not on disk by
# the verdict it stored -- each said. Every stored look so far was searched
# at 105 columns under a 2-row gate, and reading their verdicts as today's
# showed that gate's refusals as if the new one made them: two operator
# offsets at dy 3, and four frames 108-112 columns out, among them.
#
# Over every roll since 2026-09-22, replayed 2026-10-06: 254 of 262 frames
# whose arrival was measured and believed landed within 3.5 units of where
# the carry-over put them, and 177 of 185 since 2026-10-03. One of the eight
# arrived a perforation from it, on a slide roll -- an advance that slipped
# a sprocket hole; five are the second or third frame of a slide roll, 4.0
# to 4.8 units off; the tool lists every one.

#: One perforation of 135 film, in units. An arrival this far from where the
#: carry-over put it is an advance that slipped a sprocket hole rather than a
#: move carried: 2026-10-03-221354's second frame arrived 46 units out.
PERFORATION_UNITS = 44.9

#: How close to a perforation a miss has to be to be flagged as one, in units.
PERFORATION_SLACK_UNITS = 5.0

#: How close an arrival has to land to its prediction to count as predicted,
#: in units: the figure the 2026-10-05 audit used.
PREDICTED_WITHIN_UNITS = 3.5


# What a hold or an aim sent, and how far an aim moved its strip, are
# `framing.sent_units` and `framing.aim_units`: the roll's net budget
# reads a stored aim the same way, the move in flight of a failed aim
# included. They were two readings here and in `session`, which
# disagreed about exactly that record.


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
    return measure_shift(image, now)


def _manifest(path: Path) -> dict:
    """A manifest beside a roll, on the strip's numbering; empty where it is
    missing or unreadable -- each one it reads is optional."""
    try:
        return renumbered(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


class Pass(NamedTuple):
    """One visit to a frame in a roll, as the library filed it."""

    when: float              # when its pass began, in UTC seconds
    number: int              # the frame it was of
    names: frozenset         # its entries: a slide's two passes are one visit
    approved: dict           # its hold record, where it kept one


#: Tags of an entry that is no frame's pass, though a roll filed it.
_NOT_A_PASS = frozenset({"prescan", "before", "probe", "hold", "debug"})


def named_time(name, times: dict[str, float]) -> float | None:
    """When the pass an entry holds began, from the name a record gives it:
    the library's own record of that entry, or -- the entry gone -- the
    time its directory is named by, ``20261004T130034Z_...``."""
    if not name:
        return None
    name = PureWindowsPath(str(name)).name
    if name in times:
        return times[name]
    try:
        return datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


class Shelf:
    """What the library knows of the rolls a replay reads, read once.

    Every frame pass each roll filed, run after run, in the order they were
    scanned -- `roll.json` keeps only each frame's latest run, and says when
    only that run began; when each entry's pass began, which places a hold's
    reference and a walk's prescans in time; when the walks of rolls from
    before entries said which roll they were prescanned; and every stored
    hold, with the pictures of its looks and its reference, named or found
    by reproducing what it recorded (`registration_margin.stored_holds`).
    """

    def __init__(self, library_root: Path, rolls_root: Path):
        self.root = Path(library_root)
        rolls_root = Path(rolls_root)
        records = library.entries(self.root)
        visits: dict[tuple[str, str], Pass] = {}
        #: When each entry's pass began, by the entry's directory name.
        self.times: dict[str, float] = {}
        #: When each roll's walks prescanned, for a roll from before entries
        #: said which roll they were.
        self.walked: dict[str, list[float]] = defaultdict(list)
        for record in records:
            tags = set(record.get("tags") or ())
            extra = record.get("extra") or {}
            member = extra.get("roll_membership") or {}
            when = (_time(extra.get("started_utc"))
                    or _time(record.get("created")))
            if when is None:
                continue
            name = library.entry_path(self.root, record).name
            self.times[name] = when
            if member:
                if member.get("kind") not in ("frame", "infrared pass"):
                    continue
                roll, number = member.get("roll"), member.get("number")
            elif "prescan" in tags:
                # A walk's prescan, from before entries said which roll they
                # were. A roll of that age filed no prescans of its own, so
                # one of these between two passes is a walk between them.
                roll = _roll_of(record, rolls_root)
                if roll and "before" not in tags:
                    self.walked[str(roll)].append(when)
                continue
            elif "roll" not in tags or tags & _NOT_A_PASS:
                continue
            else:
                roll, number = (_roll_of(record, rolls_root),
                                _label_number(record))
            if not roll or number is None:
                continue
            approved = (record.get("registration") or {}).get("approved") or {}
            pair = str((extra.get("infrared_pair") or {}).get("id") or name)
            seen = visits.get((str(roll), pair))
            visits[(str(roll), pair)] = (
                Pass(when, int(number), frozenset({name}), approved)
                if seen is None else
                Pass(min(seen.when, when), seen.number, seen.names | {name},
                     seen.approved or approved))
        self.passes: dict[str, list[Pass]] = defaultdict(list)
        for (roll, _pair), visit in visits.items():
            self.passes[roll].append(visit)
        for passes in self.passes.values():
            passes.sort(key=lambda p: p.when)
        holds, self.candidates = stored_holds(self.root, rolls_root, records)
        self.holds = {name: hold for hold in holds for name in hold.names}
        self._sought: set[int] = set()

    def hold(self, visit: Pass | None, record: dict) -> Hold | None:
        """The stored hold of a frame of `roll.json`, where the library has
        it with its looks on disk."""
        names = set(visit.names) if visit is not None else set()
        if record.get("entry"):
            names.add(PureWindowsPath(str(record["entry"])).name)
        return next((self.holds[n] for n in sorted(names) if n in self.holds),
                    None)

    def reference(self, hold: Hold, pictures: Pictures) -> tuple | None:
        """The hold's reference: named, or found once by reproduction."""
        if hold.reference is None and id(hold) not in self._sought:
            self._sought.add(id(hold))
            find_references([hold], self.candidates, pictures)
        return hold.reference


class Runs:
    """Which run each of a roll's passes was, and what each one's prediction
    may be reckoned from -- from when the library says things happened.

    A pass begins a run where its frame number does not rise from the pass
    before, or where something started between them: a run `roll.json`
    records (its `started`, and the one in the copy it kept of the run before
    it), or the walk (`survey.json`'s `started`, its prescans, and for a roll
    from before those were recorded, a walk's prescan in the library). A
    resumed run whose start neither file kept, and whose first frame is past
    the last one before it, is read as the same run: nothing on disk says
    otherwise.
    """

    def __init__(self, passes: list[Pass], cuts: list[float],
                 walk_end: float | None, walk_times: dict[int, float]):
        self.passes = sorted(passes, key=lambda p: p.when)
        self.by_name = {name: p for p in self.passes for name in p.names}
        self.before: dict[frozenset, Pass | None] = {}
        previous = None
        for p in self.passes:
            begins = (previous is None or p.number <= previous.number
                      or any(previous.when < cut <= p.when for cut in cuts))
            self.before[p.names] = None if begins else previous
            previous = p
        #: When the walk ended, for a frame whose records name no reference.
        self.walk_end = walk_end
        #: When the walk took each frame's picture, where it named the entry.
        self.walk_times = walk_times
        after = [p for p in self.passes
                 if walk_end is None or p.when > walk_end]
        #: The first pass after the walk: what the walk's aims predict, for a
        #: frame whose records name no reference.
        self.first = after[0] if after else None

    @classmethod
    def of(cls, shelf: Shelf | None, roll: str, manifest: dict, walk: dict,
           previous: dict | None = None) -> Runs | None:
        if shelf is None or not shelf.passes.get(roll):
            return None
        walked = sorted(shelf.walked.get(roll, ()))
        walk_times: dict[int, float] = {}
        for record in walk.get("frames") or ():
            when = named_time(record.get("prescan_entry"), shelf.times)
            if when is not None and isinstance(record.get("number"), int):
                walk_times[record["number"]] = when
        walk_end = (_time(walk.get("finished")) or _time(walk.get("started"))
                    or (walked[-1] if walked else None))
        cuts = [t for t in (_time(manifest.get("started")),
                            _time((previous or {}).get("started")),
                            _time(walk.get("started")), *walked,
                            *walk_times.values())
                if t is not None]
        return cls(shelf.passes[roll], cuts, walk_end, walk_times)

    def visit(self, record: dict) -> Pass | None:
        """The pass a frame of `roll.json` was: the entry it names, or for
        an older manifest, which names none, the one pass whose hold record
        is the frame's own."""
        if record.get("entry"):
            return self.by_name.get(PureWindowsPath(str(record["entry"])).name)
        approved = (record.get("registration") or {}).get("approved") or {}
        if not approved:
            return None
        found = [p for p in self.passes if p.approved == approved]
        if len(found) > 1:
            # "Holding was switched off" is word for word the same on every
            # frame it left alone; the number tells those apart.
            found = [p for p in found if p.number == record.get("number")]
        return found[0] if len(found) == 1 else None

    def place(self, visit: Pass, number: int, reference: float | None,
              aims: dict[int, float | None]) -> str | None:
        """What a pass's prediction is reckoned from: ``"first"``, the
        walk's aims after its frame; a reason it has none (``"earlier
        walk"``, ``"re-walked"``); or None for a pass that follows another
        since its reference was taken, which is carried from the pass
        before it in its run or is a new run.

        ``reference`` is when the picture it was measured against was taken.
        Without one -- a roll whose records name no entries -- the walk's
        end stands in for it, and only the first pass after that is first.
        """
        if reference is None:
            if self.walk_end is not None and visit.when < self.walk_end:
                return "earlier walk"
            if self.first is not None and visit.names == self.first.names:
                return "first"
            return None
        walked = min(self.walk_times.values(), default=None)
        if walked is not None and any(reference < p.when < walked
                                      for p in self.passes):
            # A run came between the picture it was held to and the walk on
            # record, so the aims it passed through are not the ones kept.
            return "earlier walk"
        if any(reference < p.when < visit.when for p in self.passes):
            return None
        return self.timing([j for j in aims if j > number], reference,
                           visit.when, aims) or "first"

    def timing(self, frames, since: float | None, until: float,
               aims: dict[int, float | None]) -> str | None:
        """``"re-walked"`` where an aim at one of ``frames`` was made outside
        the span the prediction is reckoned over -- after ``until``, or
        before the reference taken at ``since`` -- because the walk was taken
        again in part, so its order on the strip is not its order in time.
        None where each was made inside it, moved nothing, or has no time on
        record."""
        for j in frames:
            if aims.get(j) == 0.0:
                continue                  # moved nothing: when is no matter
            when = self.walk_times.get(j)
            if when is None:
                continue
            if when > until or (since is not None and when < since):
                return "re-walked"
        return None


#: What a frame with no prediction is missing, for a person.
NO_PREDICTION = {
    "new run": "a new run: nothing to carry from",
    "earlier walk": "measured against a walk before the one on record, whose "
                    "aims are not kept",
    "re-walked": "the walk was taken again in part, so its aims are out of "
                 "order",
    "broken": "the frame before it in its run is not the one roll.json "
              "keeps: nothing to carry from",
    "unknown": "the frame before moved by an unrecorded amount",
    "unknown aim": "an aim of the walk's it passed moved by an unrecorded "
                   "amount",
}


def _carry(prev: dict | None, number: int, visit: Pass | None,
           runs: Runs | None, aims: dict[int, float | None], start_at,
           last_aim: float | None, reference: float | None
           ) -> tuple[float | None, str, float | None]:
    """Where the moves before it should have put a frame, in units: ``(where,
    how, the last move before it)``, with ``where`` None and ``how`` saying
    why where nothing can say."""
    def carried():
        if prev["left"] is None:
            return None, "unknown", prev["last_move"]
        if runs is not None:
            until = next((t for t in (reference,
                                      visit.when if visit else None)
                          if t is not None), float("inf"))
            why = runs.timing([j for j in aims if prev["number"] < j <= number],
                              prev["reference"], until, aims)
            if why:
                return None, why, None
        passed = _between(aims, prev["number"], number)
        if passed is None:
            return None, "unknown aim", None
        return prev["left"] - passed, "carried", prev["last_move"]

    def first():
        after = _between(aims, number)
        if after is None:
            return None, "unknown aim", None
        return after, "first", last_aim

    if visit is not None and runs is not None:
        placed = runs.place(visit, number, reference, aims)
        if placed == "first":
            return first()
        if placed is not None:
            return None, placed, None
        before = runs.before.get(visit.names)
        if before is None:
            return None, "new run", None
        if prev is not None and (
                before.names == getattr(prev["visit"], "names", None)
                # A frame roll.json keeps no hold record of is found by
                # nothing; its number is all there is to know it by.
                or (prev["visit"] is None and before.number == prev["number"])):
            return carried()
        return None, "broken", None
    # Nothing says when this frame was scanned: `roll.json` alone, whose
    # `start_at` is where its latest run began.
    if prev is None:
        return first()
    if number <= prev["number"] or (isinstance(start_at, int)
                                    and number == start_at
                                    and prev["number"] < start_at):
        return None, "new run", None
    return carried()


class FirstLook(NamedTuple):
    """A hold's first look as today's gate judges it."""

    believed: bool | None    # today's verdict; None for no verdict at all
    reach: int               # the reach that verdict is at
    px: int | None           # the displacement it is about
    look: dict | None        # the numbers it was judged on
    source: str              # "re-measured", "judged", "stored" or "none"
    then: bool | None        # the verdict the look stored, at its own reach
    then_reach: int
    rows: int | None         # rows of the prescan it was taken on
    #: Why it could not be measured again, where it was judged as stored:
    #: one of :data:`NOT_REMEASURED`.
    why: str | None = None


#: Why a stored first look was not measured again, for a person.
NOT_REMEASURED = {
    "no library": "there is no library to measure it on",
    "no entry": "the library holds no entry of the frame with its prescan",
    "no picture": "its picture was never filed -- a hold that moved, from "
                  "before arrivals were filed tagged 'before'",
    "not its look": "the library's record of the hold holds another look",
    "no reference": "the picture it was held to is not in the library",
    "resampled": "it was measured against a picture of another size",
    "differs": "the pictures on disk no longer reproduce what it recorded",
}


def judge_first(first: dict | None, width: float, rows: int | None,
                hold: Hold | None = None, shelf: Shelf | None = None,
                pictures: Pictures | None = None) -> FirstLook:
    """A stored first look, judged by today's gate, and how.

    ``re-measured``: searched at another reach than today's, so measured
    again by `measure_shift` on the pictures it was taken from
    (:func:`remeasure_look`). ``judged``: searched at today's reach, so its
    own confidence and dy against today's floor and off-axis gate. ``stored``:
    neither possible, the verdict the look recorded, at the reach it records.
    """
    then, then_reach = looked(first, width)
    today = reach_px(int(round(width)))
    if first is None:
        return FirstLook(None, today, None, None, "none", then, then_reach,
                         rows)
    # As stored only where it was scored as today's gate scores: at today's
    # reach, on the part both pictures show, smoothed alike. A look from
    # before 2026-10-06 holds the whole pictures' score, which today's floor
    # was not fitted on, so it is measured again.
    if (then_reach == today and rows and first.get("confidence") is not None
            and first.get("dy") is not None
            and first.get("scored_on") == "shared"
            and first.get("smooth") == MATCH_SMOOTH):
        believed = Gate().believes(float(first["confidence"]),
                                   int(first["dy"]), rows,
                                   float(first.get("agreement") or 0.0),
                                   first.get("shared_px"))
        return FirstLook(believed, today, first.get("px"), first, "judged",
                         then, then_reach, rows)
    measured, why = remeasure_look(first, then_reach, hold, shelf, pictures)
    if measured is not None:
        shift, detail, shape = measured
        return FirstLook(shift is not None, int(detail.get("reach_px") or today),
                         detail.get("px"), detail, "re-measured", then,
                         then_reach, int(shape[0]))
    return FirstLook(then, then_reach, first.get("px"), first, "stored", then,
                     then_reach, rows, why)


def remeasure_look(first: dict, reach: int, hold: Hold | None,
                   shelf: Shelf | None, pictures: Pictures | None
                   ) -> tuple[tuple[float | None, dict, tuple] | None,
                              str | None]:
    """A stored first look measured again today: ``((units, detail, shape),
    None)``, or ``(None, why)`` with why it could not be, one of
    :data:`NOT_REMEASURED`.

    On the pictures `--holds` pairs it with -- the hold's reference, named or
    found by reproducing its last look, and the prescan its first look was,
    the frame's own where it made no move and the arrival filed tagged
    `before` where it did -- and only once those reproduce what the hold
    recorded, at the reach it searched: a picture that does not is some
    other one, and measuring it says nothing about this hold.
    """
    if shelf is None or pictures is None:
        return None, "no library"
    if hold is None:
        return None, "no entry"
    look = next((k for k in hold.looks if k.kind in ("first", "only")), None)
    if look is None:
        return None, "no picture"
    if look.stored != first:
        return None, "not its look"
    if shelf.reference(hold, pictures) is None:
        return None, "no reference"
    reference, now = pictures(hold.reference), pictures(look.source)
    if reference is None or now is None:
        return None, "no reference" if reference is None else "no picture"
    if reference.shape[:2] != now.shape[:2]:
        return None, "resampled"
    # As it was measured when it was recorded, which for a look from before
    # 2026-10-06 is the whole pictures, unsmoothed (`recorded_reading`).
    again = recorded_reading(first, pictures, hold.reference, look.source,
                             reach)
    if not reproduces(first, again.confidence, again.px):
        return None, "differs"
    shift, detail = measure_shift(reference, now)
    return (shift, detail, now.shape[:2]), None


def prescan_rows(held: dict) -> int | None:
    """Rows of the prescans a hold compared, where its record kept their
    shape; nothing else says, and the off-axis gate is judged per row."""
    shape = held.get("reference_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2 and shape[0]:
        return int(shape[0])
    return None


def gate_rows(dy: int | None, rows: int | None) -> int | None:
    """A match's dy in rows of a 300 dpi prescan, the off-axis gate's unit;
    as it stands where the prescan's height is not known -- every roll here
    prescans at 300 dpi."""
    if dy is None:
        return None
    if not rows:
        return abs(int(dy))
    return int(round(abs(int(dy)) * PRESCAN_ROWS_300 / rows))


def refusal(row: dict) -> str | None:
    """Why today's gate refused a first look: ``"axis"``, ``"weak"``,
    ``"pattern"`` (a match the shared part does not bear out), or None for
    one it believed or never judged."""
    if row["believed"] is not False:
        return None
    look = row["look"] or {}
    if row["source"] == "judged" and look.get("confidence") is not None:
        if float(look["confidence"]) < CONFIDENCE_FLOOR:
            return "weak"
        borne = Gate().bears_out(float(look.get("agreement") or 0.0),
                                 look.get("shared_px"))
        return "axis" if borne else "pattern"
    reason = str(look.get("reason"))
    if "off the film axis" in reason:
        return "axis"
    if "a pattern matched" in reason or "the part both pictures show" in reason:
        return "pattern"
    return "weak"


def replay(folder: Path, library_root: Path | None = None,
           shelf: Shelf | None = None) -> dict:
    """Every held frame of one roll: where the carry-over put it, where it
    arrived, and what the hold decides from there today.

    The decision is `hold_plan`'s first, on the first look judged by today's
    gate (:func:`judge_first`) -- or, for a frame that was never measured
    because holding was off, measured now (`remeasure`) or, failing that,
    the prediction, and said so. The prediction is made within a run only,
    from what the library says of when each frame was scanned (:class:`Runs`).

    ``shelf`` is the library, read once for every roll a caller replays;
    given only ``library_root``, it is read here. With neither, `roll.json`
    and `survey.json` are all there is, and a run begins only where the frame
    number goes back or `roll.json`'s `start_at` says.
    """
    manifest = renumbered(json.loads((folder / "roll.json").read_text(
        encoding="utf-8")))
    walk = _manifest(folder / "survey.json")
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

    if shelf is None and library_root is not None:
        shelf = Shelf(library_root, folder.parent)
    runs = Runs.of(shelf, folder.name, manifest, walk,
                   _manifest(folder / ("roll.json" + PREVIOUS)))
    times = shelf.times if shelf is not None else {}
    # The picture each frame was held to, where its hold names none: the
    # sheet's approval, else the walk's own prescan of the frame.
    named = {r["number"]: r.get("reference_entry")
             for r in _manifest(folder / "approved.json").get("frames") or ()
             if isinstance(r.get("number"), int)}
    walked = {r["number"]: r.get("prescan_entry")
              for r in walk.get("frames") or ()
              if isinstance(r.get("number"), int)}
    pictures = Pictures()
    start_at = manifest.get("start_at")

    rows: list[dict] = []
    prev: dict | None = None              # the frame before, and where it was left
    frames = sorted((r for r in manifest.get("frames") or ()
                     if isinstance(r.get("number"), int)),
                    key=lambda r: r["number"])
    for record in frames:
        number = record["number"]
        held = (record.get("registration") or {}).get("approved") or {}
        visit = runs.visit(record) if runs is not None else None
        reference = named_time(held.get("reference_entry") or named.get(number)
                               or walked.get(number), times)
        predicted, how, last_move = _carry(prev, number, visit, runs, aims,
                                           start_at, last_aim, reference)
        if not held:
            # Scanned without a hold: nothing moved it, so it was left where
            # it arrived.
            prev = {"number": number, "left": predicted, "visit": visit,
                    "last_move": last_move, "reference": reference}
            continue

        width = prescan_width(manifest, held)
        per_column = APERTURE_UNITS / width
        history = held.get("history") or []
        first = history[0] if history else None
        judged = judge_first(first, width, prescan_rows(held),
                             shelf.hold(visit, record) if shelf else None,
                             shelf, pictures)
        believed, reach, look = judged.believed, judged.reach, judged.look
        source, look_rows = judged.source, judged.rows
        if source == "re-measured":
            arrived = judged.px * per_column
        else:
            arrived = held.get("arrived_units")
            if arrived is None and first and first.get("px") is not None:
                arrived = first["px"] * per_column
        if first is None:
            measured = remeasure(folder, number, record,
                                 shelf.root if shelf else library_root)
            if measured is not None:
                shift, detail = measured
                look, believed, source = detail, shift is not None, "measured"
                reach = int(detail.get("reach_px") or reach_px(int(width)))
                arrived = detail["px"] * per_column
                look_rows = prescan_rows(held)

        seen = None
        if believed and arrived is not None:
            seen = arrived
        elif look is None and predicted is not None:
            seen, source = predicted, "predicted"
        target = units_of(held, "target")
        want, decision = (hold_plan(target_units=target, looks=[seen])
                          if target is not None else (None, "?"))

        miss = (arrived - predicted
                if arrived is not None and predicted is not None else None)
        perforation = (miss is not None and abs(abs(miss) - PERFORATION_UNITS)
                       <= PERFORATION_SLACK_UNITS)
        row = {
            "frame": number, "how": how,
            "target_units": target,
            "predicted_units": predicted, "arrived_units": arrived,
            "miss_units": miss, "believed": believed, "reach_px": reach,
            "look": look, "source": source,
            "then": judged.then, "then_reach_px": judged.then_reach,
            "kept_because": judged.why if source == "stored" else None,
            "stored_look": first, "look_rows": look_rows,
            "dy_rows": gate_rows((look or {}).get("dy"), look_rows),
            "stored": held.get("outcome"), "decision": decision,
            "move_units": want,
            "perforation": perforation, "after_move_units": last_move,
            "moves": held.get("moves", 0),
        }
        row["refused"] = refusal(row)
        rows.append(row)

        # Where the run left it, which is where the next frame of the run
        # arrives: what the hold did, not what it would do today.
        if units_of(held, "final") is not None:
            left = units_of(held, "final")
        else:
            base = arrived if believed and arrived is not None else predicted
            sent = sent_units(held)
            left = None if base is None or sent is None else base + sent
        prev = {"number": number, "left": left, "visit": visit,
                "last_move": _last_move(held) or last_move,
                "reference": reference}
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


#: What a first look was judged on today, for a person.
JUDGED_ON = {
    "re-measured": "measured again on the library's pictures",
    "judged": "judged from their own numbers, searched at today's reach",
    "stored": "by the verdict they stored, not measurable again:",
    "measured": "measured now, holding having been off",
    "predicted": "with nothing measured, the prediction standing in",
    "none": "with nothing measured and nothing to predict from",
}

#: The same in the table's column, beside the look.
_JUDGED_SHORT = {"re-measured": "again", "judged": "own", "stored": "stored",
                 "measured": "now"}


def _off_axis(look: dict | None) -> bool:
    return "off the film axis" in str((look or {}).get("reason"))


def report_replay(results: list[dict]) -> dict:
    """The per-roll tables, then what they add up to."""
    def u(v, width=7):
        return f"{v:+{width}.1f}" if isinstance(v, (int, float)) else \
            f"{'-':>{width}}"

    for result in results:
        # An aim that moved by an amount nobody kept is "?": it is why the
        # frames it lies before have no prediction.
        aims = {n: a for n, a in result["aims"].items() if a is None or a}
        said = (", ".join(f"{n}: {'?' if a is None else f'{a:+.1f}'}"
                          for n, a in sorted(aims.items()))
                if aims else "none")
        print(f"\n-- {result['roll']} ({result['film'] or '?'}); the walk's "
              f"aims, in units: {said}")
        print(f"  {'fr':>3} {'target':>7} {'predict':>7} {'arrived':>7} "
              f"{'miss':>6}  {'first look today':<24} {'stored':<14} now")
        for r in result["rows"]:
            if r["source"] in _JUDGED_SHORT:
                look = (f"{say_look(r['look'], r['believed'], r['reach_px'])} "
                        f"{_JUDGED_SHORT[r['source']]}")
            else:
                look = r["source"] if r["source"] != "none" else "-"
            now = r["decision"] + (f" {r['move_units']:+.1f}"
                                   if r["move_units"] is not None else "")
            flag = ""
            if r["perforation"]:
                after = r["after_move_units"]
                flag = ("  PERFORATION? after "
                        + ("no move" if after is None else
                           f"a {after:+.1f} move "
                           f"({'forward' if after > 0 else 'back'})"))
            if r["predicted_units"] is None and r["how"] in NO_PREDICTION:
                flag += f"  ({NO_PREDICTION[r['how']]})"
            if (r["stored_look"] and r["then"] is not None
                    and r["then"] != r["believed"]):
                # Today's verdict is not the one it stored: say that one.
                was = say_look(r["stored_look"], r["then"],
                               r["then_reach_px"])
                flag += f"  was {was}"
            print(f"  {r['frame']:>3} {u(r['target_units'])} "
                  f"{u(r['predicted_units'])} {u(r['arrived_units'])} "
                  f"{u(r['miss_units'], 6)}  {look:<24} "
                  f"{str(r['stored']):<14} {now}{flag}")

    rows = [r for result in results for r in result["rows"]]
    print(f"\n== {len(results)} rolls, {len(rows)} held frames")

    # How each first look was judged, and what today's gate makes of the
    # ones the stored verdict, at its own reach and gate, refused.
    on = Counter(r["source"] for r in rows)
    print(f"   today's gate: {Gate().describe()}")
    print("   first looks judged by it:")
    for source, what in JUDGED_ON.items():
        if on[source]:
            print(f"     {on[source]:4d} {what}")
        if source == "stored":
            kept = Counter(r["kept_because"] for r in rows
                           if r["source"] == "stored")
            for why, n in sorted(kept.items(), key=lambda kv: -kv[1]):
                print(f"            {n:4d} {NOT_REMEASURED.get(why, why)}")
    now_in = [r for r in rows if r["then"] is False and r["believed"] is True]
    now_out = [r for r in rows if r["then"] is True and r["believed"] is False]
    axis = [r for r in now_in if _off_axis(r["stored_look"])]
    far = [r for r in now_in if r not in axis
           and abs(int((r["look"] or {}).get("px") or 0)) > r["then_reach_px"]]
    print(f"   believed today where the stored verdict refused: {len(now_in)}"
          f" -- {len(axis)} refused off the film axis, {len(far)} further out "
          f"than that search reached, {len(now_in) - len(axis) - len(far)} "
          f"below the floor there; refused today where it believed: "
          f"{len(now_out)}")
    edge = [r for r in rows if r["stored_look"] and gate_rows(
        r["stored_look"].get("dy"), r["look_rows"]) == MAX_DY_PX]
    edge_in = [r for r in edge if r["believed"]]
    edge_moving = [r for r in edge_in if r["decision"] == "move"]
    print(f"   first looks {MAX_DY_PX} rows off the film axis: {len(edge)} -- "
          f"{len(edge_in)} believed today, {len(edge_moving)} of them now "
          f"move")

    # Measured: stored by the hold, or measured now -- again, or for a frame
    # whose hold never looked. Never the prediction standing in for a look.
    measured = ("re-measured", "judged", "stored", "measured")
    judged = [r for r in rows if r["believed"] and r["miss_units"] is not None
              and r["source"] in measured]
    close = [r for r in judged if abs(r["miss_units"]) <= PREDICTED_WITHIN_UNITS]
    perforated = [(result["roll"], r) for result in results
                  for r in result["rows"] if r["perforation"]]
    print(f"   arrival within {PREDICTED_WITHIN_UNITS} units of the carry-over "
          f"prediction: {len(close)} of {len(judged)} measured and believed "
          f"({sum(1 for r in judged if r['source'] == 'measured')} of them "
          f"measured now, holding having been off)")
    unpredicted = Counter(r["how"] for r in rows
                          if r["predicted_units"] is None)
    if unpredicted:
        print(f"   no prediction for {sum(unpredicted.values())}:")
        for how, n in sorted(unpredicted.items()):
            print(f"     {n:4d} {NO_PREDICTION.get(how, how)}")
    refused = [r for r in rows if r["refused"] and r["miss_units"] is not None]
    for name, kind, what in (
            ("refused by today's off-axis gate", "axis",
             "a real peak the gate would not believe"),
            ("refused by today's gate as too weak", "weak",
             "the tallest bump in noise, as often as not"),
            ("refused by today's gate as a pattern, not the picture",
             "pattern", "a pattern both pictures hold")):
        group = [r for r in refused if r["refused"] == kind]
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
    for outcome in ("budget", "off", "unverified"):
        these = [r for r in rows if r["stored"] == outcome]
        if not these:
            continue
        # What each now decides, and on what: a stored look, one measured
        # again or now, the prediction standing in for one, or nothing.
        decided: dict[tuple[str, str], int] = {}
        for r in these:
            source = (r["source"] if r["look"] is not None
                      or r["source"] == "predicted" else "no look")
            decided[(r["decision"], source)] = (
                decided.get((r["decision"], source), 0) + 1)
        said = []
        for (decision, source), n in sorted(decided.items()):
            where = {"stored": "the stored look",
                     "re-measured": "the look measured again",
                     "judged": "the look as stored, at today's reach",
                     "measured": "the look measured now",
                     "predicted": "the prediction",
                     "no look": "no look on disk and nothing to predict "
                                "from"}.get(source, source)
            said.append(f"{n} {decision} on {where}")
        moving = sum(n for (decision, _s), n in decided.items()
                     if decision == "move")
        print(f"   stored '{outcome}': {len(these)} -- {moving} now move; "
              + "; ".join(said))
    return {"rows": len(rows), "predicted": [len(close), len(judged)],
            "judged_on": dict(on),
            "kept_because": dict(Counter(r["kept_because"] for r in rows
                                         if r["source"] == "stored")),
            "believed_now": len(now_in), "refused_now": len(now_out),
            "edge_dy": [len(edge), len(edge_in), len(edge_moving)],
            "unpredicted": dict(unpredicted),
            "refused": len(refused),
            "refused_off_axis": sum(1 for r in refused
                                    if r["refused"] == "axis"),
            "perforations": [(roll, r["frame"], round(r["miss_units"], 1))
                             for roll, r in perforated],
            "transitions": {f"{a} -> {b}": n for (a, b), n in table.items()}}


def run_replay(args) -> int:
    """`--replay`: the rolls named, or every roll; a table each, and the sum.

    The library is read once for all of them (:class:`Shelf`): which run
    each frame came from, and the pictures every stored first look is
    measured again on.
    """
    if args.replay:
        folders = [Path(name) if Path(name).is_dir() else args.rolls_root / name
                   for name in args.replay]
    else:
        # Each strip of a roll of strips too (`library.roll_folders`): one
        # level deep, a replay skipped every strip, and the figures it is
        # quoted for would under-count from the first roll of strips on.
        folders = [p for p in library.roll_folders(args.rolls_root,
                                                   trash=False)
                   if (p / "roll.json").exists()]
    if args.since:
        folders = [f for f in folders if (roll_date(f) or "") >= args.since]
    shelf = (Shelf(args.library, args.rolls_root) if args.library.is_dir()
             else None)
    results = []
    started = time.monotonic()
    for i, folder in enumerate(folders, 1):
        if not (folder / "roll.json").exists():
            print(f"no roll.json in {folder}")
            continue
        try:
            result = replay(folder, shelf=shelf)
        except (OSError, ValueError) as exc:
            print(f"{folder}: {exc}")
            continue
        print(f"  [{i}/{len(folders)}] {folder.name}: "
              f"{len(result['rows'])} held frames, "
              f"{time.monotonic() - started:.0f} s",
              file=sys.stderr, flush=True)
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
                    help="--replay: the library that says which run each "
                         "frame came from, and holds the pictures its first "
                         "look is measured on today (default ./library)")
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

    scale = units_per_px(images[0][1].shape[1])
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
