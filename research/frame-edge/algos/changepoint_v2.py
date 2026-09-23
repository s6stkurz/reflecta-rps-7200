"""Change-point baseline: [neighbour sliver] [base plateau] [picture], per side.

The simple, transparent reference every cleverer detector has to beat. It asks
one question per side, from the border inward, and answers it with statistics
a person can check against the zoomed crops:

    Is there a run of columns near the border that is flat in every row, at
    the top of this frame's range, and that ends in one sharp, straight,
    near-vertical step down to the picture?

That is Stefan's description of real base -- *"a sharp line, completely black
from top to bottom"* in the positive -- turned into tests on the negative,
where base is the clearest thing on the film.

How, per side (the right side is the left side of the mirrored frame, run
through the same code, so a mirrored frame gives the mirrored answer by
construction):

1. **Column statistics over rows, not row averages.** Per column and channel,
   a trimmed mean over rows (the level). Per column, on the channel-summed
   signal normalised by that column's own level, the 10th percentile over
   rows (``lo``) and a robust per-pixel noise from vertical differences.
   Base is at its level in *every* row, so its ``lo`` sits within noise of
   1; a picture column that is merely bright on average has denser rows and
   a ``lo`` far below. Averaging the rows first is what lost this in the
   detectors before this one.
2. **Base-like columns.** Flat (``1 - lo`` within a few sigma), and at the
   top of the frame: within ``TOP_BAND`` of the clearest column of the frame
   (film only -- the gate and opaque columns are excluded). "Top" is judged
   on column trimmed means, not on pixels: on C-41, 4-7% of *pixels* can be
   brighter than base (strip6_01: base (54, 26, 14), frame p99.5 (63, 31,
   17)), so a pixel percentile would reject real base; but across 28 plateaus
   judged base by eye no picture *column* came within 2% of base.
3. **Three segments from the border.** The plateau is grown, column by
   column, around the first base-like column within ``MAX_OUTER`` of the
   border. Before it (usually nothing) is the outer segment: a neighbour
   frame's picture that shows when a frame is placed far off -- the stage-9b
   ladder walks the inter-frame gap right across the aperture, and r0914_20
   sits a whole gap off -- or an odd column at the very border (r0914_24's
   last column reads 0.64 of base). The change-points are the run's ends.
4. **Is the plateau base?** Base is featureless: nothing on film is clearer
   than it, so no row of the plateau may be clearer than the plateau
   (``BRIGHT_*``). A plateau away from the border must be a *gap*: between
   ``MIN_GAP`` and ``MAX_GAP`` wide, and straight on both sides. A plateau at
   the border wider than any gap is a dark scene or a leader: refused.
5. **The step.** Per row, the edge is the 50% crossing between the plateau
   level and the picture level just past it, read as the fraction of base in
   the straddling columns (the labelling convention: a column 13% of the way
   from picture to base holds 0.13 columns of base). A real frame edge is the
   camera gate's shadow: the per-row crossings lie on one straight line. A
   silhouette's do not. Their spread about the fitted line is the
   straightness test, the line gives the tilt.
6. **Slivers.** A border column clearer than everything beyond it by
   ``SLIVER_MARGIN`` is base even when the edge crosses it and it is not
   flat; its step is read the same way. Narrower still, a border column that
   in nearly every row sits a steady fraction of the way from its neighbour
   up to the frame's top holds that fraction of a column of base (``SUB_*``).
   These matter more than their width: the frame is ~445 columns against a
   428-column aperture, so a well-placed frame shows no base at all and a
   0.2-column sliver means the frame is ~9 units off.

States. ``no_film``: a border run far brighter than all the film in the frame
and far less orange than it (the empty gate). ``all_base``: every column is
base-like. ``picture_to_border``: no base-like run near the border --
confidently when the border is dense or well below the frame's top -- or,
at 0.5, a run that fails as base: wider than a gap at the border, clearer rows
inside it, a band inside the frame that is not shown to be a gap, or (round 2)
a border plateau whose step lies at the border itself.
``refuse``: a plateau that passes as base but whose step cannot be read or is
not straight, a gap whose outer side cannot be read, or (round 2) base
claimed on both sides.

Iterations (dev, 98 labelled frames): iter01 anchor-at-the-border only;
iter02 three segments, so gaps inside the frame (the ladder) are read; iter03
the featureless test and gap widths; iter04 slivers, and failures that the
labels showed to be scenery answered as picture rather than refused.

Round 2 (``changepoint_v2``; `changepoint` stays as it was). Library 2 is one
C-41 strip whose gaps often lie beside scenes nearly as clear as base, and
iter04 missed 9 of its dev sides, all gaps 40-105 columns in. Five changes,
each a statement about the film rather than a threshold moved (iter01: 7-9
with the sweep inside the plateau only; iter02: the sweep at the border
plateau's end, the steady run by spread, and 10; iter03: 11):

7. **Every candidate band, not the first.** A band that fails as base says
   nothing about what lies beyond it: the neighbour's picture can hold a
   base-level stripe of its own before the gap (b0922b_08: a 5-column band
   at 25, the gap at 43-62). The search goes on to the next base-like run
   within ``MAX_OUTER`` until one is *not* shown to be picture.
8. **A gap anchored by either side.** Iter04 asked an off-border band for an
   inner step in half the rows. The far step -- base against the neighbour's
   picture -- is as much evidence: now either step may be the anchor
   (``ANCHOR_ROWS``) and the other need only be straight in ``MIN_ROWS`` of
   the rows and parallel to it (``PARALLEL_COLS``).
9. **The step inside the plateau.** A picture a few percent under base joins
   the plateau (the chain tolerance is 4% per column), so the step read at
   the plateau's end is picture texture, crooked or faint. When it fails, the
   step is looked for inside the plateau (`_sweep`), against the base next to
   the anchor -- the one base level the band has shown -- on rows averaged in
   bins of six, and accepted only when several change-points in a row read
   the same straight line: an edge is read at the same place from every
   window that holds it, a gradient's reading moves with the window. For a
   gap the anchor is the strong side; for base at the border, the border --
   and there the plateau's own end is read again this way too, which is
   what a faint edge right at the end needs (r0911_06/07).
10. **Not both sides.** A frame (~438 columns) is wider than the aperture
   (428), so base on both sides is a contradiction; both are refused.
11. **A step at the border is no base.** A border plateau whose step line
   lies at or before the border (x <= 0.5 columns of base) says no base
   shows: picture at base density, at 0.5, instead of a refusal.

What round 2 still cannot do: a gap neither of whose steps shows in half the
rows (b0921c_57, b0922b_09 on library 2: 34-36% and 17-18%, and the inner
lines read there are 1.3-1.5 columns from the labels anyway), or one whose
steps show nowhere (b0922b_06: 1% under base in the column means, visible
only by eye on a row-smoothed, stretched view). Both need evidence from
outside the frame.

Everything is relative to the frame's own levels -- never a dtype maximum,
never an absolute count -- because the dataset holds uint8 and uint16 frames
and exposure ladders whose base spans ~0.4 to ~112 counts, with per-channel
gains that differ between files. Ratios *between* two regions of one frame are
invariant to those gains, which is what every test below uses. (Base colour is
stable within one gain setting, R/G 2.07-2.31 and B/G 0.52-0.56 here, but the
dups at other gains move it, so it is not used.)

What this detector cannot do, by design: tell base from a dark scene area in a
frame that shows no base anywhere. Both are the clearest thing in the frame;
only the straight step separates them, and architecture has straight edges.
The r0911 museum walls (2-4% under base, full height) are refused, not read.

numpy only; ~30 ms a frame (the per-row crossings are vectorised; the sweep
runs only where a plateau's step has failed).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from common import (
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, TRIM_ROWS, EdgeResult, Side,
)

# --------------------------------------------------------------------------
# constants -- each says what it guards and why it has its value

#: A column is "no data" -- opaque, outside the film window: the 16 columns at
#: the left of x0920_01 read (65, 12, 145) against a picture of ~(3700, 2600,
#: 3700) -- when its brightest pixel in any channel is below this fraction of
#: the frame's 99th percentile. Those columns peak at 5-7.5% of it; the densest
#: real picture column seen here (r0914_07) peaks at ~36%.
DEAD_FRACTION = 0.10

#: Trimmed mean over rows, central 80%: a speck of dust or a scratch in a base
#: column cannot move its level.
TRIM_LOW, TRIM_HIGH = 10.0, 90.0

#: The low percentile over rows that asks whether *every* row of a column is at
#: the level: 10% of ~276 rows is ~28 rows, a region a tenth of the frame tall.
#: Lower would chase single-pixel noise in 8-bit data.
LO_PERCENTILE = 10.0

#: The gate. Film always attenuates; the gate does not. Base on 8-bit C-41
#: prescans is ~(68, 30, 15) against the gate's ~182 neutral: brighter by
#: (2.7, 6, 12), and *bluer relative to red* by 182/182 / (15/68) = 4.5x. B&W
#: base is ~15 counts neutral: the gate is ~12x brighter in every channel.
#: r0914_07 set these: 4 columns of base against a very dense picture read
#: 3.3-5.3x brighter than the picture in every channel -- a plain brightness
#: ratio called it the gate. What it cannot fake is colour: base there is only
#: 1.6x bluer-relative-to-red than the picture.
GATE_MIN_RATIO = 1.5          # brighter than all the film, every channel
GATE_CHROMA = 2.5             # C-41: B/R at least this multiple of the film's
GATE_BW_RATIO = 5.0           # B&W (neutral film): brighter by this, every channel

#: Flat: ``1 - lo`` within ``FLAT_SIGMA`` noise sigmas plus ``FLAT_FLOOR``.
#: Measured on base columns: 8-bit C-41 sits at 1.0-2.5 sigma (p10 of a
#: Gaussian is 1.28), 8-bit B&W base at ~15 counts at 2.7-4.0 -- its noise is
#: heavy-tailed. A column a tenth of whose rows are picture reads 5-30 sigma.
FLAT_SIGMA = 4.0
FLAT_FLOOR = 0.02

#: Base-like: the channel-summed level within this fraction of the frame's
#: clearest column. Base drifts ~5% across a 29-column gap (ladder9b_14: 99 ->
#: 105 summed counts, shading residual and the film itself); the dark floor
#: beside that gap sits at 0.87-0.91 and x0920_28's picture next to its base at
#: 0.88-0.92. 0.06 keeps the gap and leaves both out.
TOP_BAND = 0.06
#: A border column clearer than every column beyond it by this is a sliver of
#: base even when it is not flat (r0909_08: 2.6x, x0920_10: 1.3x, r0909_05:
#: 1.3x; the flat slivers found as plateaus read 1.2-2.4x).
SLIVER_MARGIN = 0.15
#: A sliver narrower than a column: the border column holds a fraction ``f``
#: of base, so in every row it sits ``f`` of the way from its neighbour up to
#: the frame's top. Read per row where the neighbour is at least
#: ``SUB_CONTRAST`` below the top; accepted when at least ``SUB_ROWS`` of the
#: rows can be read, ``SUB_POSITIVE`` of those lean the same way, the median
#: ``f`` is at least ``SUB_MIN``, and the next pair of columns (1 against 2)
#: shows no such lean (``SUB_CONTROL``), so a picture gradient is not taken
#: for base. Measured on the dev labels: the six labelled sub-column slivers
#: (0.15-0.5 columns) read median f 0.16-0.67 with 83-100% of rows leaning;
#: of 60-odd picture borders with enough rows, none reached f 0.10.
SUB_CONTRAST = 0.10
SUB_ROWS = 0.5
SUB_POSITIVE = 0.8
SUB_MIN = 0.12
SUB_CONTROL = 0.05
#: Growing the plateau from a base-like column: each next column's summed
#: level within this of its plateau neighbours'. Adjacent base columns agree to
#: ~1%; the steps out of base measured here are 10-12% in one column
#: (ladder9b_14 base to floor, x0920_28 base to picture).
PLATEAU_CHAIN_TOL = 0.04
#: C-41 picture can beat base in a *single* channel -- a coloured coupler is
#: consumed where its layer is exposed (r0914_13: red 1.02 of base) -- so the
#: band is on the channel sum, with a looser bound per channel.
TOP_CHANNEL_BAND = 0.15
#: Below this fraction of the frame's top a border column is clearly picture.
TOP_CLEAR = 0.85

#: How far in from the border a plateau may start. The stage-9b ladder walks a
#: 29-column gap from the border to column 59 before it leaves the search.
MAX_OUTER = 100
#: A plateau away from the border is a gap between frames, and no gap is wider
#: than this: 29 and ~10 columns are the two measured in the ladder, 21.7 units
#: (27 columns) is the lower bound docs/frame-measurement-plan.md derives.
MAX_GAP = 40
#: An outer segment this narrow is an odd border column (r0914_24's last
#: column) or a neighbour sliver too narrow to read a step from: the step
#: window needs ``HALF_WINDOW + PICTURE_SPAN`` = 5 columns of picture beyond
#: two of crossing. ladder9b_11/12 put the gap 4 columns in and could not be
#: read at 3.
OUTER_TRUST = 6
#: A plateau away from the border is an unusual placement -- the frame is off
#: by more than a gap -- so its inner step must show in at least this share of
#: the rows. The ladder's true gaps show in 66-90%; the dark floor beside the
#: gap in ladder9b_08, 7% under base and the clearest thing in that frame,
#: showed a straight step in 29%.
OFF_BORDER_ROWS = 0.5
#: ... and must hold at least this many whole base columns: with both sides
#: inside the frame the whole gap shows, and gaps here run from 5.4 columns
#: (r0911_17, 3 whole columns and two part ones, labelled) through 9-10 and
#: 17 to 29. A 1-2 column stripe is scenery (r0914_19's).
MIN_GAP = 3

#: Nothing on film is clearer than base, so a plateau with rows clearer than
#: itself is not base: per row, the median over the plateau's inner columns
#: may exceed its level by ``BRIGHT_SIGMA`` noise sigmas of that median (and
#: ``BRIGHT_FLOOR``) in at most ``BRIGHT_ROWS`` of the rows. Measured on 33
#: plateaus judged base by eye: 0% everywhere but bw0910_04 (2.5%, 8-bit B&W
#: with heavy-tailed noise). x0920_40's false plateau: 38%; x0920_55's, a blue
#: wall crossed by a railing clearer than it: 4%.
BRIGHT_SIGMA = 6.0
BRIGHT_FLOOR = 0.05
BRIGHT_ROWS = 0.03

#: Per row, the step is read only where base and picture differ by at least
#: this many noise sigmas (and ``ROW_CONTRAST_FLOOR``): below it the picture in
#: that row is as clear as base and the crossing is noise.
ROW_CONTRAST_SIGMA = 4.0
ROW_CONTRAST_FLOOR = 0.05

#: The per-row window: two columns either side of the crossing, then the
#: picture level from the three columns after the window.
HALF_WINDOW = 2
PICTURE_SPAN = 3

#: Straightness: the per-row crossings' MAD about their fitted line, in
#: columns. A frame edge is straight to a fraction of a column (0.06-0.35 on
#: the clear edges of iteration 1); a silhouette's boundary wanders by tens.
STRAIGHT_MAD = 0.6
#: ... and at least this fraction of the rows must have the contrast to read.
MIN_ROWS = 0.2
#: Fewer rows than this and there is nothing to fit.
MIN_ROW_COUNT = 12

#: Tilt below this over the frame height (in columns) is reported as vertical.
TILT_MIN = 1.0

# -- round 2: gaps anchored by one side, and steps too faint to read per row --

#: A gap is two straight steps with base between, and either may carry it: the
#: *anchor* is straight and shows in at least this share of the rows -- the
#: same bar ``OFF_BORDER_ROWS`` set for the inner step alone. On library 2 the
#: far step (base against the neighbour's sky) is often the strong one and
#: the inner step, against a scene at base level, shows only where the scene
#: is dense: b0922b_07's in 36% of rows, b0921c_54's in 36%.
ANCHOR_ROWS = 0.5
#: The partner of an anchor must be parallel to it: the two slopes agree to
#: this many columns over the frame height. Measured on the 50 gaps read on
#: both sides on the two dev sets: the inner line minus the outer is +2.1
#: columns at the median, +1.4 to +2.7 for 80% of them, -1.7 and +5.2 at the
#: extremes -- the gap widens towards the bottom on every roll, likely the
#: frame's rounded corners at one end of the prescan. It does not separate the
#: grout of x0920_73 (-3.0 to -3.7; straightness does that). What it rejects is
#: a partner found inside the band that runs across it: on x0920_74/75's grout,
#: before the sweep asked for a steady run, lines 5.3 and 6.8 columns off.
PARALLEL_COLS = 4.0
#: Reading a step inside a plateau (`_sweep`): rows averaged in bins of
#: ``SWEEP_BIN`` (a near-vertical edge moves 0.04 columns in six rows; the
#: per-pixel noise of ~1.9% of base drops to ~0.8%), so the contrast floor
#: can fall from ``ROW_CONTRAST_FLOOR`` to ``SWEEP_FLOOR`` -- about 4 binned
#: sigmas. The steps this is for sit 3-7% under base in the rows that show
#: them (b0921a_07's bottom rows: 0.92-0.95 against 0.99). A line needs
#: ``SWEEP_MIN_BINS`` bins (36 rows) to be fitted at all.
SWEEP_BIN = 6
SWEEP_FLOOR = 0.03
SWEEP_MIN_BINS = 6
#: ... and it is a step, not a gradient, when at least ``STABLE_RUN``
#: consecutive change-points read straight lines that all lie within
#: ``STABLE_TOL`` columns of each other. On the border plateaus that ran on
#: past their edge the true edge was read from 4-6 change-points within 0.15-
#: 0.5 (b0920_05, b0921a_07, b0922s_04 on library 2; r0911_06/07 on the main
#: set), and every reading past it moved with the window, by 0.25 to 1.2
#: columns per column. Of the four border plateaus labelled picture whose step fails
#: (bw0910_01, r0911_04, r0911_23, b0922s_06), none holds such a run.
STABLE_RUN = 3
STABLE_TOL = 0.5
#: Both sides claiming at least this many columns of base contradict each
#: other: the frame measures 429-440 columns (median 438) over library 2's 38
#: measured gaps and ~440 on the main set (pitch minus gap,
#: `ground_truth.json`), against a 428-column aperture, and no dev frame of
#: either set is labelled with base on both sides. A column, because below it
#: base and border are scored the same.
BOTH_SIDES_MIN = 1.0


# --------------------------------------------------------------------------
# statistics


def _levels(img: np.ndarray) -> np.ndarray:
    """(W, 3): per column and channel, the trimmed mean over rows."""
    lo_q, hi_q = np.percentile(img, [TRIM_LOW, TRIM_HIGH], axis=0)
    inside = (img >= lo_q[None]) & (img <= hi_q[None])
    return (img * inside).sum(axis=0) / np.maximum(inside.sum(axis=0), 1)


def _noise(s: np.ndarray) -> np.ndarray:
    """(w,): robust per-pixel noise of each column, from vertical differences.

    MAD of row-to-row differences over sqrt(2): texture that changes slowly
    down a column hardly touches it, an edge crossing the column touches one
    difference out of ~276.
    """
    d = np.diff(s, axis=0)
    med = np.median(d, axis=0)
    return 1.4826 * np.median(np.abs(d - med[None]), axis=0) / np.sqrt(2.0)


def _dead_run(img: np.ndarray) -> tuple[int, int]:
    """Opaque columns (no data) at the left and at the right."""
    colmax = img.max(axis=(0, 2))
    ref = float(np.percentile(img, 99.0))
    dead = colmax <= DEAD_FRACTION * max(ref, 1e-12)
    w = len(dead)
    left = _run_from(dead, 0)
    right = _run_from(dead[::-1], 0)
    return left, min(right, w - left)


def _run_from(mask: np.ndarray, start: int) -> int:
    """Length of the run of True in ``mask`` starting at ``start``."""
    n = 0
    while start + n < len(mask) and mask[start + n]:
        n += 1
    return n


# --------------------------------------------------------------------------
# the gate


def _gate_run(level: np.ndarray, start: int, stop: int, rest: np.ndarray,
              film: str) -> tuple[int, dict[str, Any]]:
    """Columns from ``start`` that are the empty gate, judged against ``rest``."""
    anchor = level[start]
    if np.any(anchor <= 0):
        return 0, {}
    same = np.all(level[start:stop] >= 0.8 * anchor, axis=1)
    n = _run_from(same, 0)
    others = rest.copy()
    others[start:start + n + HALF_WINDOW + 1] = False
    if not np.any(others):
        return 0, {"why": "nothing to compare with"}
    lv = level[others]
    top = lv.max(axis=0)
    ratio = anchor / np.maximum(top, 1e-12)
    # the film's colour: its clearest column by channel-mean relative level
    clearest = lv[int(np.argmax((lv / np.maximum(top, 1e-12)).mean(axis=1)))]
    chroma = ((anchor[2] / max(anchor[0], 1e-12))
              / max(clearest[2] / max(clearest[0], 1e-12), 1e-12))
    dbg = {"ratio": ratio.tolist(), "chroma": float(chroma), "run": n}
    if not np.all(ratio >= GATE_MIN_RATIO):
        return 0, dbg
    colour = chroma >= GATE_CHROMA
    bright = bool(np.all(ratio >= GATE_BW_RATIO))
    if film == "c41":
        ok = colour
    elif film == "bw":
        ok = bright
    else:
        ok = colour or bright
    return (n if ok else 0), dbg


# --------------------------------------------------------------------------
# the step


def _crossings(sb: np.ndarray, centre: np.ndarray, sigma: float,
               floor: float = ROW_CONTRAST_FLOOR) -> tuple[np.ndarray, np.ndarray]:
    """Per row, the fraction-of-base edge position around ``centre[r]``.

    ``sb`` is a [base | picture] profile relative to the base level (base =
    1). Columns before the window are taken as base; the window's columns
    contribute the fraction of the way from the row's picture level to base;
    the picture level is the median of the ``PICTURE_SPAN`` columns after the
    window. Returns (x per row, valid per row).
    """
    h, w = sb.shape
    floor = max(floor, ROW_CONTRAST_SIGMA * sigma)
    rows = np.arange(h)[:, None]
    c = np.round(np.asarray(centre, dtype=np.float64)).astype(np.int64)
    w0 = np.maximum(c - HALF_WINDOW, 0)
    w1 = np.maximum(c + HALF_WINDOW, 1)
    valid = w1 + PICTURE_SPAN <= w
    pidx = np.clip(w1[:, None] + np.arange(PICTURE_SPAN), 0, w - 1)
    pic = np.median(sb[rows, pidx], axis=1)
    contrast = 1.0 - pic
    valid &= contrast >= floor
    widx = w0[:, None] + np.arange(2 * HALF_WINDOW)
    inside = widx < w1[:, None]
    vals = sb[rows, np.clip(widx, 0, w - 1)]
    safe = np.where(valid, contrast, 1.0)
    frac = np.clip((vals - pic[:, None]) / safe[:, None], 0.0, 1.0) * inside
    x = np.where(valid, w0 + frac.sum(axis=1), np.nan)
    return x, valid


def _line(rows: np.ndarray, xs: np.ndarray, min_count: int = MIN_ROW_COUNT
          ) -> tuple[float, float, float, np.ndarray]:
    """Robust straight line x = a*row + b: fit, drop 3-MAD outliers, refit."""
    a, b = np.polyfit(rows, xs, 1)
    resid = xs - (a * rows + b)
    mad = float(np.median(np.abs(resid - np.median(resid))))
    keep = np.abs(resid - np.median(resid)) <= max(3.0 * 1.4826 * mad, 0.5)
    if keep.sum() >= min_count:
        a, b = np.polyfit(rows[keep], xs[keep], 1)
    return float(a), float(b), mad, keep


def _step(sb: np.ndarray, k: int, sigma: float, floor: float = ROW_CONTRAST_FLOOR,
          min_count: int = MIN_ROW_COUNT) -> dict[str, Any] | None:
    """Read the step of a [base | picture] profile whose change-point is ``k``.

    Two passes: per-row crossings around column ``k``, a robust line through
    them, then the crossings again around that line (a tilted edge leaves the
    first window). None when too few rows have the contrast to read.
    """
    rows = np.arange(sb.shape[0], dtype=np.float64)
    x1, v1 = _crossings(sb, np.full(len(rows), float(k)), sigma, floor)
    if v1.sum() < min_count:
        return None
    a, b, _, _ = _line(rows[v1], x1[v1], min_count)
    x2, v2 = _crossings(sb, a * rows + b, sigma, floor)
    if v2.sum() < min_count:
        return None
    a, b, mad, keep = _line(rows[v2], x2[v2], min_count)
    mid = (len(rows) - 1) / 2.0
    return {"x": a * mid + b, "top": b, "bottom": a * (len(rows) - 1) + b,
            "slope": a, "mad": mad, "rows": float(v2.mean()), "n": int(keep.sum())}


def _straight(st: dict[str, Any] | None) -> tuple[bool, str]:
    if st is None:
        return False, "no contrast to read the step"
    why = []
    if st["mad"] > STRAIGHT_MAD:
        why.append(f"step not straight (MAD {st['mad']:.2f} cols)")
    if st["rows"] < MIN_ROWS:
        why.append(f"only {st['rows']:.0%} of rows show the step")
    return not why, "; ".join(why)


# --------------------------------------------------------------------------
# one side


def _columns(img: np.ndarray, level: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per column: flatness in noise sigmas, and the noise itself.

    Each column is normalised by its own summed level, so ``1 - lo`` asks how
    far its densest tenth of rows falls below the column's level.
    """
    tot = np.maximum(level.sum(axis=1), 1e-12)
    s = img.sum(axis=2) / tot[None, :]
    lo = np.percentile(s, LO_PERCENTILE, axis=0)
    sig = _noise(s)
    return (1.0 - lo - FLAT_FLOOR) / np.maximum(sig, 1e-6), sig


def _sliver(img: np.ndarray, tot: np.ndarray, noise: np.ndarray, film: np.ndarray,
            b0: int, stop: int) -> tuple[Side, dict[str, Any]] | None:
    """A border column of base too narrow or too tilted to be flat.

    A column that is clearer than every column beyond it by ``SLIVER_MARGIN``
    holds base: across 28 plateaus judged base by eye no picture column came
    within 2% of it. It is not flat because the edge crosses it (tilt) or
    because it is only partly base. Its base level is its 90th percentile over
    rows -- the rows where it is whole base -- and the step is read from it.
    """
    beyond = film.copy()
    beyond[:b0 + HALF_WINDOW + 1] = False
    beyond[stop:] = False
    if not np.any(beyond) or tot[b0] < (1.0 + SLIVER_MARGIN) * tot[beyond].max():
        return None
    col = img[:, b0].sum(axis=1)
    base = float(np.percentile(col, 90.0))
    sb = img[:, b0:stop].sum(axis=2) / max(base, 1e-12)
    st = _step(sb, 1, float(noise[b0]))
    ok, why = _straight(st)
    dbg = {"sliver": st, "sliver_margin": float(tot[b0] / tot[beyond].max())}
    if st is None:
        return Side(REFUSE, note="border column is base but its step cannot be read"), dbg
    x = float(b0 + st["x"])
    if not ok or not (b0 - 0.5 <= x <= b0 + HALF_WINDOW + 0.5):
        return Side(REFUSE, x=x, note=f"border sliver: {why or 'step strays'}"), dbg
    half = float(max(0.5, 3.0 * 1.4826 * st["mad"] / np.sqrt(max(st["n"], 1))))
    side = Side(EDGE, x=x, lo=x - half, hi=x + half,
                conf=float(np.clip(0.8 * min(1.0, st["rows"] / 0.6)
                                   * (1.0 - 0.5 * st["mad"] / STRAIGHT_MAD), 0.3, 0.8)),
                note=f"border sliver, step in {st['rows']:.0%} of rows, "
                     f"MAD {st['mad']:.2f}")
    if abs(st["bottom"] - st["top"]) >= TILT_MIN:
        side.x_top = float(b0 + st["top"])
        side.x_bottom = float(b0 + st["bottom"])
    return side, dbg


def _sub_sliver(img: np.ndarray, top: float, b0: int, stop: int) -> dict[str, Any]:
    """How much of the border column is base, when less than all of it.

    Per row, ``f = (s0 - s1) / (1 - s1)`` with ``s`` the channel sum over the
    frame's top: a column that is ``f`` base and ``1 - f`` the picture beside
    it reads exactly that. The same statistic on columns 1 and 2 is the
    control: a picture that brightens toward the border does it there too.
    """
    if stop - b0 < 4:
        return {"accept": False}
    s = img[:, b0:b0 + 3].sum(axis=2) / max(top, 1e-12)
    out: dict[str, Any] = {"accept": False}
    fs = []
    for a, b in ((0, 1), (1, 2)):
        c = 1.0 - s[:, b]
        ok = c > SUB_CONTRAST
        f = (s[ok, a] - s[ok, b]) / c[ok] if ok.sum() >= MIN_ROW_COUNT else np.array([])
        fs.append((float(ok.mean()), f))
    rows, f01 = fs[0]
    _, f12 = fs[1]
    if not len(f01):
        return out
    med = float(np.median(f01))
    pos = float(np.mean(f01 > 0.03))
    ctrl = float(np.median(f12)) if len(f12) else 0.0
    out.update(f=med, positive=pos, rows=rows, control=ctrl)
    out["accept"] = (rows >= SUB_ROWS and pos >= SUB_POSITIVE and med >= SUB_MIN
                     and ctrl <= SUB_CONTROL)
    return out


def _grow(seed: int, tot: np.ndarray, flat: np.ndarray, start: int, stop: int
          ) -> tuple[int, int]:
    """The plateau around a base-like ``seed`` column: [o, e).

    Grown column by column in both directions while each new column is flat
    and within ``PLATEAU_CHAIN_TOL`` of the mean of the (up to) three plateau
    columns next to it. Chained rather than held to one level because base
    drifts slowly across a gap -- 6.5% over ladder9b_13's 28 columns -- while
    every step out of base seen here is 10% or more in one column.
    """
    o, e = seed, seed + 1
    while e < stop and flat[e] and abs(tot[e] / tot[max(o, e - 3):e].mean() - 1.0) \
            <= PLATEAU_CHAIN_TOL:
        e += 1
    while o > start and flat[o - 1] and abs(tot[o - 1] / tot[o:min(e, o + 3)].mean() - 1.0) \
            <= PLATEAU_CHAIN_TOL:
        o -= 1
    return o, e


def _side(img: np.ndarray, level: np.ndarray, flat_sig: np.ndarray, noise: np.ndarray,
          start: int, stop: int, rest: np.ndarray) -> tuple[Side, dict[str, Any]]:
    """Read the left side of ``img`` (the right side arrives mirrored).

    ``start``/``stop``: the live columns. ``rest``: the film columns (live, not
    gate) the frame's top is taken over.
    """
    dbg: dict[str, Any] = {}
    tot = level.sum(axis=1)
    film = rest if np.any(rest) else np.arange(len(tot)) >= 0
    top = float(tot[film].max())
    top_ch = level[film].max(axis=0)
    t = tot / max(top, 1e-12)
    chan_ok = np.all(level / np.maximum(top_ch, 1e-12)[None, :] >= 1.0 - TOP_CHANNEL_BAND,
                     axis=1)
    flat = flat_sig <= FLAT_SIGMA
    baseish = flat & (t >= 1.0 - TOP_BAND) & chan_ok
    baseish[:start] = False
    baseish[stop:] = False

    # the plateau: the first base-like run within MAX_OUTER of the border
    reach = min(stop, start + MAX_OUTER)
    found = np.flatnonzero(baseish[start:reach])
    b0 = start
    dbg.update(border_t=float(t[b0]), border_flat_sigmas=float(flat_sig[b0]))
    if not len(found):
        sliver = _sliver(img, tot, noise, film, b0, stop)
        if sliver is not None:
            side, sdbg = sliver
            dbg.update(sdbg)
            return side, dbg
        sub = _sub_sliver(img, top, b0, stop)
        dbg["sub_sliver"] = sub
        if sub["accept"]:
            f = float(min(sub["f"], 1.0))
            return Side(EDGE, x=b0 + f, lo=b0 + max(f - 0.3, 0.0), hi=b0 + f + 0.3,
                        conf=0.5, note=f"sub-column sliver: {f:.2f} of the border column "
                                       f"is base ({sub['positive']:.0%} of rows)"), dbg
        # no base near the border: how clearly is the border picture?
        c_level = 0.9 if t[b0] <= TOP_CLEAR else float(
            np.clip(0.5 + 4.0 * (1.0 - TOP_BAND - t[b0]), 0.5, 0.9))
        c_flat = 0.5 if flat[b0] else float(
            np.clip(0.5 + 0.1 * (flat_sig[b0] - FLAT_SIGMA), 0.5, 0.95))
        return Side(PICTURE_TO_BORDER, conf=max(c_level, c_flat),
                    note=f"no base-like run (border at {t[b0]:.2f} of top, "
                         f"{flat_sig[b0]:.1f} sigma)"), dbg

    # Every base-like run within reach, from the border in, until one is not
    # shown to be picture. A band that fails as base says nothing about what
    # lies beyond it: the neighbour's picture can hold a base-level stripe of
    # its own before the gap (b0922b_06/08/09).
    first: Side | None = None
    bands: list[dict[str, Any]] = []
    pos = start
    while True:
        nxt = np.flatnonzero(baseish[pos:reach])
        if not len(nxt):
            break
        o, e = _grow(pos + int(nxt[0]), tot, flat, pos, stop)
        side, pdbg = _plateau(img, level, noise, tot, o, e, start, stop)
        bands.append(dict(pdbg, o=o, e=e, state=side.state, note=side.note))
        if first is None:
            first = side
        if side.state != PICTURE_TO_BORDER:
            if side.state == EDGE or first.state == PICTURE_TO_BORDER:
                first = side
            break
        pos = e
    dbg["bands"] = bands
    assert first is not None
    return first, dbg


def _sweep(sb: np.ndarray, lo: int, hi: int, sigma: float, want: float | None,
           inside: bool = True) -> dict[str, Any] | None:
    """The first steady straight step from the base side, change-point in ``[lo, hi]``.

    For a step too faint to read row by row (a picture only a few percent
    under base), inside a plateau that ran on past it. Rows are averaged in
    bins of ``SWEEP_BIN`` -- a near-vertical edge moves 0.04 columns in six
    rows, the noise drops by 2.4 -- and every change-point from ``lo`` up is
    read as `_step` reads one. A real edge is read at the same place from
    every window that holds it; a gradient's reading follows the window
    (b0921a_07: 23.8-24.7 from change-points 21-27, the true edge 24.6, then
    25.8, 27.3, 28.4, 29.4). So the answer is the first run of at least
    ``STABLE_RUN`` consecutive change-points whose lines are straight, all
    lie within ``STABLE_TOL`` columns of each other, lie inside the plateau
    (unless ``inside`` is False) and -- when an anchor gives a slope
    ``want`` -- are parallel to it; the run's median reading is returned.
    None when there is no such run.
    """
    h = sb.shape[0]
    n = h // SWEEP_BIN
    sbb = sb[:n * SWEEP_BIN].reshape(n, SWEEP_BIN, -1).mean(axis=1)
    sigma_b = sigma / np.sqrt(SWEEP_BIN)
    reads: list[dict[str, Any] | None] = []
    for c in range(max(lo, 1), hi + 1):
        st = _step(sbb, c, sigma_b, SWEEP_FLOOR, SWEEP_MIN_BINS)
        good = _straight(st)[0]
        if good:
            assert st is not None
            # back to rows: the line through the bins' centres
            slope = st["slope"] / SWEEP_BIN
            st = dict(st, slope=slope, top=st["x"] - slope * (h - 1) / 2.0,
                      bottom=st["x"] + slope * (h - 1) / 2.0, n=st["n"] * SWEEP_BIN)
            # A line within a window's width of the plateau's end is that
            # end's step read again, not a different one: on the grout of
            # r0914_17 and x0920_73 a shifted window turned a crooked outer
            # step (MAD 0.64-0.71) into a passable one.
            good = (not inside or st["x"] <= hi - HALF_WINDOW) and (
                want is None or abs(st["slope"] - want) * h <= PARALLEL_COLS)
        reads.append(st if good else None)
    for i in range(len(reads)):
        j = i
        while j < len(reads) and reads[j] is not None:
            # every read in [i, j] is a line here: the loop only walks over lines
            xs = [float(r["x"]) for r in reads[i:j + 1] if r is not None]
            if max(xs) - min(xs) > STABLE_TOL:
                break
            j += 1
        if j - i >= STABLE_RUN:
            run = [r for r in reads[i:j] if r is not None]
            order = np.argsort([r["x"] for r in run])
            return dict(run[int(order[(len(run) - 1) // 2])], steady=len(run))
    return None


def _plateau(img: np.ndarray, level: np.ndarray, noise: np.ndarray, tot: np.ndarray,
             o: int, e: int, start: int, stop: int) -> tuple[Side, dict[str, Any]]:
    """Is the base-like run ``[o, e)`` base, and where does it meet picture?"""
    dbg: dict[str, Any] = {}
    k = e - o                                   # e: first column past the plateau
    dbg.update(outer_cols=o - start, run=k)
    if e >= stop:
        if o - start <= OUTER_TRUST:
            return Side(ALL_BASE, conf=0.6, note="every column is base-like"), dbg
        return Side(REFUSE, note=f"base-like from column {o} to the far side"), dbg

    # featureless: no row of the plateau clearer than the plateau
    sigma = float(np.median(noise[o:e]))
    plate = img[:, o:e].sum(axis=2) / max(float(tot[o:e].mean()), 1e-12)
    if k >= 4:
        plate = plate[:, 1:-1]
    row_med = np.median(plate, axis=1)
    lift = max(BRIGHT_FLOOR, BRIGHT_SIGMA * sigma / np.sqrt(plate.shape[1]))
    bright = float(np.mean(row_med > 1.0 + lift))
    dbg["bright_rows"] = bright
    if bright > BRIGHT_ROWS:
        if o - start > OUTER_TRUST:
            # a band inside the frame that is not base: the border is picture
            return Side(PICTURE_TO_BORDER, conf=0.5,
                        note=f"band at col {o} has {bright:.0%} of rows clearer than "
                             "itself: scenery, not a gap"), dbg
        # Not uniform, so not base: a dark scene with clearer things in it
        # (all eight such border plateaus on dev are labelled picture).
        return Side(PICTURE_TO_BORDER, conf=0.5,
                    note=f"plateau at col {o} has {bright:.0%} of rows clearer than "
                         "itself: scenery, not base"), dbg

    # the inner step, against the plateau's level next to it
    near = level[max(o, e - 3):e].mean(axis=0)
    sb = img[:, start:stop].sum(axis=2) / max(float(near.sum()), 1e-12)
    inner = _step(sb, e - start, sigma)
    ok_in, why_in = _straight(inner)
    dbg["inner"] = inner

    # the outer step, when the plateau is off the border
    outer_x: float | None = None
    if o - start <= OUTER_TRUST and k > MAX_GAP:
        # Base at the border is at most a gap wide before the neighbour's
        # picture shows; a wider run is a dark scene (every one of the eight
        # on dev is labelled picture or unreadable) -- or, rarely, a leader.
        return Side(PICTURE_TO_BORDER, conf=0.5,
                    note=f"base-like {k} cols from the border: wider than any gap, "
                         "so a dark scene"), dbg
    if o - start > OUTER_TRUST:
        if k > MAX_GAP or k < MIN_GAP:
            return Side(PICTURE_TO_BORDER, conf=0.5 if k < MIN_GAP else 0.6,
                        note=f"base-like band {k} cols wide from col {o}: "
                             f"{'narrower' if k < MIN_GAP else 'wider'} than any gap, "
                             "so picture"), dbg
        far = level[o:min(o + 3, e)].mean(axis=0)
        sb_out = (img[:, start:e].sum(axis=2) / max(float(far.sum()), 1e-12))[:, ::-1]
        outer = _step(sb_out, e - o, sigma)
        ok_out, why_out = _straight(outer)
        # A gap is two straight steps with base between. Either may be the
        # anchor -- straight and in at least ANCHOR_ROWS of the rows. When
        # the other cannot be read at the plateau's end, the plateau may run
        # on into a picture at base level (b0921c_49: five columns of it), so
        # the partner is looked for inside the plateau, against the base
        # level next to the anchor: the one base this band has shown.
        strong_in = ok_in and inner is not None and inner["rows"] >= ANCHOR_ROWS
        strong_out = ok_out and outer is not None and outer["rows"] >= ANCHOR_ROWS
        if strong_out and not ok_in:
            sb_far = img[:, start:stop].sum(axis=2) / max(float(far.sum()), 1e-12)
            cand = _sweep(sb_far, o - start + MIN_GAP, e - start, sigma,
                          -outer["slope"])
            if cand is not None:
                inner, (ok_in, why_in) = cand, _straight(cand)
                dbg["swept"], dbg["steady"] = "inner", cand["steady"]
        if strong_in and not ok_out:
            assert inner is not None      # strong_in says so; the sweep above only replaces it
            sb_near = (img[:, start:e].sum(axis=2) / max(float(near.sum()), 1e-12))[:, ::-1]
            cand = _sweep(sb_near, MIN_GAP, e - o, sigma, -inner["slope"])
            if cand is not None:
                outer, (ok_out, why_out) = cand, _straight(cand)
                dbg["swept"], dbg["steady"] = "outer", cand["steady"]
        dbg["inner"], dbg["outer"] = inner, outer
        parallel = (inner is not None and outer is not None
                    and abs(inner["slope"] + outer["slope"]) * img.shape[0] <= PARALLEL_COLS)
        anchored = ok_in and ok_out and parallel and (strong_out or strong_in)
        dbg["anchored"] = bool(anchored)
        # An off-border band must earn its place: before round 2, its inner
        # step in OFF_BORDER_ROWS of the rows; now also any anchored pair.
        if not anchored and (inner is None or inner["rows"] < OFF_BORDER_ROWS):
            # failing, the border -- which is not base -- decides.
            return Side(PICTURE_TO_BORDER, conf=0.5,
                        note=f"band at col {o}: inner step in only "
                             f"{0 if inner is None else inner['rows']:.0%} of rows, "
                             "not shown to be a gap"), dbg
        if not ok_out:
            if outer is not None and outer["mad"] > STRAIGHT_MAD:
                return Side(PICTURE_TO_BORDER, conf=0.5,
                            note=f"band at col {o} is not a gap: its outer "
                                 f"{why_out}"), dbg
            return Side(REFUSE, note=f"gap at col {o}, outer side: {why_out}"), dbg
        assert outer is not None
        outer_x = float(e - outer["x"])
    elif o > start:
        outer_x = float(o)

    if o - start <= OUTER_TRUST and not ok_in:
        # Base at the border whose step, read at the plateau's end, fails: the
        # plateau may have run on into a picture a few percent under base
        # (b0921a_07: 3% in the column means, 24 columns of base and then
        # seven of picture). Look for the step inside it, against the base at
        # the border.
        edge_ref = level[o:min(o + 3, e)].mean(axis=0)
        sb_b = img[:, start:stop].sum(axis=2) / max(float(edge_ref.sum()), 1e-12)
        cand = _sweep(sb_b, o - start + 1, e - start + HALF_WINDOW, sigma, None,
                      inside=False)
        if cand is not None:
            inner, (ok_in, why_in) = cand, _straight(cand)
            dbg["swept"], dbg["steady"] = "inner", cand["steady"]
            dbg["inner"] = inner

    if inner is None:
        return Side(REFUSE, note="no contrast between plateau and picture"), dbg
    x = float(start + inner["x"])
    tilt = abs(inner["bottom"] - inner["top"])
    if not (o - 0.5 <= x <= e + HALF_WINDOW + 0.5 + tilt):
        if o - start <= OUTER_TRUST and x <= start + 0.5:
            # The step read from a border plateau lies at the border itself:
            # the line says no base shows, whatever the plateau's level. Both
            # dev cases are picture at base density (r0911_21's museum wall,
            # b0922s_06's night scene), refused by iter04.
            return Side(PICTURE_TO_BORDER, conf=0.5,
                        note=f"step line at {x:.1f}, at the border: the plateau "
                             f"[{o}, {e}) is not followed by a step, so no base"), dbg
        return Side(REFUSE, note=f"step line at {x:.1f} strays from the plateau "
                                 f"[{o}, {e})"), dbg
    half = float(max(0.5, 3.0 * 1.4826 * inner["mad"] / np.sqrt(max(inner["n"], 1))))
    side = Side(EDGE, x=x, lo=x - half, hi=x + half, outer=outer_x)
    if abs(inner["bottom"] - inner["top"]) >= TILT_MIN:
        side.x_top = float(start + inner["top"])
        side.x_bottom = float(start + inner["bottom"])
    if not ok_in:
        side.state, side.conf, side.note = REFUSE, 0.0, why_in
        return side, dbg
    f = inner["rows"]
    conf = 0.95 * min(1.0, f / 0.6) * (1.0 - 0.5 * inner["mad"] / STRAIGHT_MAD)
    if o - start > OUTER_TRUST:
        conf *= 0.9
    if dbg.get("swept"):
        # read on binned rows from a faint step: less to defend
        conf *= 0.8
    side.conf = float(np.clip(conf, 0.3, 0.95))
    side.note = (f"{k} base cols" + (f" from col {o}" if o > start else "")
                 + f", step in {f:.0%} of rows, MAD {inner['mad']:.2f}"
                 + (f"; {dbg['swept']} step read on {SWEEP_BIN}-row bins, steady over "
                    f"{dbg.get('steady', 0)} change-points" if dbg.get("swept") else ""))
    return side, dbg


# --------------------------------------------------------------------------
# the frame


def _mirror_side(side: Side, width: int) -> Side:
    """A left-side answer on the mirrored frame, as a right-side answer."""
    def m(v: float | None) -> float | None:
        return None if v is None else float(width - v)
    return Side(side.state, m(side.x), m(side.hi), m(side.lo), side.conf, m(side.outer),
                m(side.x_top), m(side.x_bottom), side.note)


def detect(image: np.ndarray, ctx: dict) -> EdgeResult:
    img = np.asarray(image, dtype=np.float64)[..., :3]      # RGBI -> RGB
    img = img[TRIM_ROWS:img.shape[0] - TRIM_ROWS]
    w = img.shape[1]
    film = str(ctx.get("film_type", "unknown"))
    pad_l, pad_r = _dead_run(img)
    debug: dict[str, Any] = {"pad": [pad_l, pad_r]}
    if pad_l + pad_r >= w - 16:
        return EdgeResult(Side(REFUSE, note="no data"), Side(REFUSE, note="no data"), debug)
    level = _levels(img)
    flat_sig, noise = _columns(img, level)
    live = np.zeros(w, dtype=bool)
    live[pad_l:w - pad_r] = True

    # the gate, at either border
    g_l, dg_l = _gate_run(level, pad_l, w - pad_r, live, film)
    g_r, dg_r = _gate_run(level[::-1], pad_r, w - pad_l, live[::-1], film)
    rest = live.copy()
    rest[pad_l:pad_l + g_l] = False
    rest[w - pad_r - g_r:w - pad_r] = False
    debug["gate"] = {"left": dg_l, "right": dg_r}

    sides: list[Side] = []
    for name, sl, gate, st, sp in (("left", slice(None), g_l, pad_l, w - pad_r),
                                   ("right", slice(None, None, -1), g_r, pad_r, w - pad_l)):
        if gate:
            side = Side(NO_FILM, conf=0.9, note=f"empty gate, {gate} columns")
            dbg: dict[str, Any] = {}
        else:
            side, dbg = _side(img[:, sl], level[sl], flat_sig[sl], noise[sl], st, sp,
                              rest[sl].copy())
        if name == "right":
            side = _mirror_side(side, w)
        if st:
            side.note = (side.note + f"; {st} opaque columns at the border").lstrip("; ")
        sides.append(side)
        debug[name] = dbg

    left, right = sides
    # All base from one side is all base from the other: blank film.
    if ALL_BASE in (left.state, right.state):
        conf = min(left.conf, right.conf) if left.state == right.state else 0.3
        note = left.note if left.state == ALL_BASE else right.note
        left, right = Side(ALL_BASE, conf=conf, note=note), Side(ALL_BASE, conf=conf, note=note)
    # Base on both sides needs a frame narrower than the aperture, and no
    # 35 mm frame here is (``BOTH_SIDES_MIN``). One of the two is wrong and
    # nothing here says which, so both are refused.
    bl, br = left.base_width("left", w), right.base_width("right", w)
    if bl is not None and br is not None and min(bl, br) >= BOTH_SIDES_MIN:
        why = (f"base on both sides ({bl:.1f} and {br:.1f} cols): the frame is wider "
               "than the aperture, so one is not base")
        left = Side(REFUSE, x=left.x, note=f"{why}; this side: {left.note}")
        right = Side(REFUSE, x=right.x, note=f"{why}; this side: {right.note}")
    return EdgeResult(left, right, debug)
