"""gapmodel: place a frame's edges by fitting the strip's own geometry.

The film is a sequence, and that is the whole idea here::

    [neighbour picture] | base gap | [this frame] | base gap | [next neighbour]

with a pitch that is known (`common.pitch_columns`, 455.3 columns of a
428-wide prescan) and a gap width ``G`` that one camera makes the same, give
or take its film advance. The frame width is then ``F = pitch - G``, and on
any one prescan:

* at most **one full gap** can be in view (the aperture is narrower than a
  pitch), and a full gap -- neighbour picture on its far side -- must be about
  ``G`` wide;
* a gap cut by the border can be at most ``G`` wide;
* base on both sides at once needs ``b_left + b_right <= W - F``. Measured
  here: ``G`` 14-18 columns on the r0914/x0920 camera (``F`` 437-441, wider
  than the aperture: only one side of a frame can ever show base, a centred
  frame shows none) and 29.6 on the ladder strip (``F`` 426, the 36 mm frame).

A candidate that breaks one of these is not a gap, whatever it looks like.
That is how the model refuses a 90-column stretch of dim museum wall at the
base level, or a grout band in a mosaic when the frame's true gap shows at the
other side: it would put a gap at a width, or at a place, the strip cannot
have. When the two sides each hold a plausible gap and the frame width says
only one can be real, the stronger one -- weighted by how far it would move the
frame -- is kept and the other side is picture to the border.

Underneath the geometry sits the per-row evidence, never a row average (the
detectors in `docs/frame-measurement-plan.md` that averaged rows first were
each confidently wrong somewhere):

1. **Uniform columns.** A column is uniform when ``FB_MIN`` of its rows lie
   within ``theta`` of its slow vertical profile. Base is free of texture top
   to bottom; picture almost never is. The frame's base level ``B`` is the
   plateau of its brightest uniform columns -- not of its brightest pixels,
   because on C-41 the orange mask is consumed where dye forms and 4-7% of
   picture pixels come out brighter than base (measured on strip6_01). A
   border column brighter than that plateau raises it (a one-column sliver is
   not uniform: the edge crosses it on a slant).
2. **Runs.** Consecutive uniform columns at one level form a run, if that
   level is one base can have (``LEVEL_FLOOR`` of ``B``, ``PEAK_FLOOR`` of
   the film's brightest pixels). Per row, a pixel belongs to the run when it
   is within ``theta`` of the run's profile, above *or* below.
3. **Edges.** At a run's end, every row with contrast places its own 50%
   crossing between the run's level and the picture beside it (area model, to
   a fraction of a column). A line is fitted through those crossings; "a sharp
   line, completely black from top to bottom" means most rows sit on it and
   the transition is one or two columns, not a ramp. Rows whose picture is as
   clear as base (dark scene right against the gap) cannot place the edge and
   are left out, not counted against it.
4. **Slivers.** Base narrower than a clean run (0.5-2.5 columns) is read from
   the border columns' per-row base fraction, and trusted less.
5. **The model** judges each run: its width against ``G``, its place, and the
   two sides against ``F``. What is left without an edge is picture to the
   border when the border column says so (darker than base can be, off base
   level in most rows, or textured), or when the plateau it sits in is wider
   than any gap; a uniform base-level border no wider than a gap with no edge
   that can be placed is refused.

``G`` is a roll-level parameter: the *other* frames of the roll
(``ctx['roll_ids']``) that show a full gap -- both edges straight, the other
side of that frame free of base -- each measure it once. With two or more
measurements their median stands; with fewer the prior ``G_PRIOR`` +-
``G_TOL_PRIOR`` does, so a frame never validates its own gap. Levels are never
pooled: exposures differ across a roll, so each frame is judged against its
own plateau. With no roll at all (a verifying second prescan) the prior
serves.

Everything is numpy, symmetric by construction (the right side is the left
side of the mirrored plane), and about 0.05 s a frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from common import (
    ALL_BASE,
    EDGE,
    NO_FILM,
    PICTURE_TO_BORDER,
    REFUSE,
    TRIM_ROWS,
    EdgeResult,
    Side,
    load,
    pitch_columns,
)

# --------------------------------------------------------------------------
# constants -- each with what it was measured on

#: Rows in the vertical running median taken before any per-row test. The edge
#: is near-vertical (tilt about a column over the height), so five rows keep it
#: sharp. A median, not a mean: dust and fine scratches on base are one or two
#: rows tall, and a mean smears each into five rows of not-quite-base
#: (r0914_06's gap read 0.87 of its rows base-like at the outer columns with a
#: mean, 0.95 with the median).
VSMOOTH = 5
#: A pixel is at a level within this fraction of it, at least. Base in one gap
#: varies by ~3% across its width (r0914_20, columns 86-102: 1.00 to 0.966).
THETA_MIN = 0.05
#: ... and at least this many sigmas of the smoothed noise (B&W grain: ~8%).
THETA_K = 3.5
#: Rows in the box mean that gives a column its slow vertical profile. A box,
#: not a median: a step between two flat regions (wall above floor) becomes a
#: ramp that the pixels either side depart from, so a picture column stays
#: non-uniform; a gradient of a quarter over the height (B&W base, bw0910)
#: stays within theta of it.
TREND_ROWS = 61
#: A column is uniform when this fraction of its rows is within theta of its
#: slow profile. Real base columns read 0.93-1.00 (dust takes a few rows); a
#: dark scene that touches the border in some rows reads 0.2-0.8.
FB_MIN = 0.90
#: ``B`` is the median of the uniform columns within this of the brightest one.
PLATEAU = 0.03
#: A run's level must be at least this fraction of ``B``: base is not always
#: the brightest uniform plateau (see point 1 above), but it is never far below.
LEVEL_FLOOR = 0.85
#: ... and at least this fraction of the film's brightest pixels (99.5th
#: percentile). Picture can come out brighter than base on C-41, where dye
#: formation consumes the mask -- base read 0.80-0.86 of this on strip6, and
#: 0.94-1.15 on every other clean edge of dev -- but not by half again: the
#: strips that read 0.41-0.59 of it (x0920_19's bluish 9-column band at the
#: left, r0911_16/27's single columns) are not base.
PEAK_FLOOR = 0.65
#: A row places the edge only when the picture beside the run is at least this
#: far below the run's level; closer, the 50% crossing is noise.
C_MIN = 0.10
#: A row sits "on the line" within this many columns of the fitted edge.
ON_LINE = 1.0
#: Of the rows that place the edge, at least this fraction must sit on the line
#: (straight), and they must be at least ``MIN_STEP_FRAC`` of all rows (an edge
#: seen in a few rows is a scene edge).
MIN_ON = 0.70
MIN_STEP_FRAC = 0.25
#: Gap width, columns. Whole gaps measured on dev: r0914_20 17.8 (outer 85.5,
#: inner 103.2), r0914_24 14.7 (a one-column neighbour sliver at the right
#: border), x0920_73 13.9 -- one camera; and the ladder strip, another camera,
#: 29.6 (ladder9b_14: 14.2 to 43.9), which is the 36 mm frame the brief
#: assumes. So the prior spans both, 10-34, and only a roll's own measurements
#: narrow it. Film advance varies by a few columns frame to frame, so the
#: tolerance is not tight even when measured.
G_PRIOR = 22.0
G_TOL_PRIOR = 12.0
G_TOL_MEASURED = 4.0
#: Measurements from other frames a roll needs before their median replaces
#: the prior. Two: on dev only r0914 gets there (17.8 and 14.7).
G_MIN_MEASURED = 2
#: Scale of the displacement prior, columns: a candidate's score is multiplied
#: by exp(-reach / REACH_PRIOR). It only decides between two candidates that the
#: frame width says cannot both be gaps; it never rejects one on its own. A
#: walked frame is aimed at the centre, so a gap far into the aperture needs
#: more evidence than one at the border (r0914_17: a 14-column black grout band
#: at 47-61 against a clean 9.6-column gap at the right).
REACH_PRIOR = 60.0
#: Two conflicting candidates: the stronger is kept only this far ahead.
JOINT_MARGIN = 1.5
#: A run may start this many columns in from the border and still touch it,
#: when the columns before it are mostly at its level: the outermost column
#: reads a little darker and noisier (r0914_01: 65 against 66, sd 3.1 against
#: 2.2).
BORDER_SLACK = 2
#: A film edge is sharp: of the eight columns around a row's crossing, one or
#: two are in transition (mean over rows 0.1-1.2 on the clean edges of dev,
#: 1.5 for r0914_20's inner edge against a shaded wall). A shaded wall or a
#: vignetted sky is a ramp: 1.7-2.4 (x0920_55's left, r0914_13's and
#: r0914_24's left walls, stage3_03's right).
SOFT_MAX = 1.6
#: Runs narrower than this, not touching the border, are not gaps.
MIN_FULL_GAP = 6.0
#: The empty gate is neutral grey; C-41 base has R/B of about 4. A column is
#: gate-coloured when max/min over its channel medians is below this.
GATE_NEUTRAL = 1.35
#: ... and at least this much brighter than every film column beside it
#: (r0909_13: gate 549 against base 89; r0914_21: 554 against 120).
GATE_RATIO = 1.8
#: Columns beside the gate that may be part gate, part film: the cut end of a
#: strip is slanted (r0914_21: 95 at the bottom to 98 at the top).
GATE_CUT = 4
#: Relative per-pixel noise below which a bright neutral field is the gate
#: rather than film: the gate reads 0.4%, base 1.8% (8-bit C-41) to 7% (B&W).
GATE_NOISE = 0.010
#: A blank frame: this fraction of film columns in base runs.
ALL_BASE_FRAC = 0.90
#: Sliver: a border strip narrower than a clean run, read from the border
#: columns' own per-row base fraction. Accepted up to this width.
SLIVER_MAX = 2.5
#: ... and only when this fraction of rows places it. A clean sliver is read in
#: 84-100% of rows (r0909_08/10, r0914_31, x0920_10); the B&W "slivers" that
#: disagreed with their own shifted repeats by 5-6 columns were read in 26-46%.
SLIVER_MIN_STEP = 0.6
#: A sliver against a run on the other side, when only one can be: a sliver is
#: read from a column or two, a run from a plateau and a line (r0914_14: a
#: 0.55-column reading at the left against a clean 7-column gap at the right).
SLIVER_WEIGHT = 0.3


# --------------------------------------------------------------------------
# the pixel-level picture of one frame

@dataclass
class Plane:
    """One frame (or its mirror), reduced to what every test below reads."""

    rgb: np.ndarray              # (h, W, 3) float64, trimmed
    g: np.ndarray                # (h, W) channel sum
    gs: np.ndarray               # (h, W) vertical running median
    trend: np.ndarray            # (h, W) gs averaged over TREND_ROWS: the slow part
    colmed: np.ndarray           # (W,) column medians of gs
    uniform: np.ndarray          # (W,) bool
    level: float                 # B: the plateau of the brightest uniform columns
    peak: float                  # the film's 99.5th percentile, smoothed pixels
    sigma: float                 # relative noise per smoothed pixel
    theta: float                 # level tolerance, relative
    film: tuple[int, int]        # [a, b) columns that hold film (not gate)
    gate_left: bool = False
    gate_right: bool = False
    notes: list[str] = field(default_factory=list)


def _vsmooth(a: np.ndarray, k: int = VSMOOTH) -> np.ndarray:
    """Running median over ``k`` rows, same shape, edges replicated."""
    r = k // 2
    pad = np.pad(a, ((r, r), (0, 0)), mode="edge")
    stack = np.stack([pad[i:i + a.shape[0]] for i in range(k)])
    return np.median(stack, axis=0)


def _col_noise(g: np.ndarray) -> np.ndarray:
    """(W,) per-pixel noise of each column, from row-to-row differences (MAD)."""
    d = np.diff(g, axis=0)
    mad = np.median(np.abs(d - np.median(d, axis=0)), axis=0)
    return 1.4826 * mad / np.sqrt(2.0)


def _gate_cols(colmed: np.ndarray, chan: np.ndarray, noise: np.ndarray,
               from_left: bool) -> int:
    """How many columns of empty gate touch this border (0 when none)."""
    w = colmed.size
    neutral = chan.max(axis=1) / np.maximum(chan.min(axis=1), 1e-9) < GATE_NEUTRAL
    bright = colmed >= 0.85 * float(colmed.max())
    order = range(w) if from_left else range(w - 1, -1, -1)
    n = 0
    for c in order:
        if not (neutral[c] and bright[c]):
            break
        n += 1
    if n < 3:
        return 0
    gate = slice(0, n) if from_left else slice(w - n, w)
    # the film's end is cut on a slant: its first few columns are part gate
    rest = slice(n + GATE_CUT, w) if from_left else slice(0, max(w - n - GATE_CUT, 0))
    level = float(np.median(colmed[gate]))
    if float(np.median(noise[gate])) / max(level, 1e-9) > GATE_NOISE:
        return 0
    if n + GATE_CUT < w and float(colmed[rest].max()) * GATE_RATIO > level:
        return 0
    return n


def _plane(image: np.ndarray) -> Plane:
    """The frame's plane. Every step is column-wise or symmetric in the two
    borders, so the plane of the mirrored frame is this one mirrored (`_flip`)
    -- which is what makes the answer mirror exactly."""
    rgb = np.asarray(image, dtype=np.float64)[..., :3]
    rgb = rgb[TRIM_ROWS:rgb.shape[0] - TRIM_ROWS]
    g = rgb.sum(axis=2)
    w = g.shape[1]
    noise = _col_noise(g)
    colmed_raw = np.median(g, axis=0)
    chan = np.median(rgb, axis=0)                              # (W, 3)
    gl = _gate_cols(colmed_raw, chan, noise, True)
    gr = _gate_cols(colmed_raw, chan, noise, False)
    notes: list[str] = []
    a, b = gl, w - gr
    if b - a < 8:
        a, b = 0, w
    if gl:
        notes.append(f"gate at left, {gl} columns")
    if gr:
        notes.append(f"gate at right, {gr} columns")
    gs = _vsmooth(g)
    colmed = np.median(gs, axis=0)
    # The film's cut edge beside a gate is a few columns of mixture.
    ia = a + (4 if gl and a > 0 else 0)
    ib = b - (4 if gr and b < w else 0)
    if ib - ia < 4:
        ia, ib = a, b
    film = np.zeros(w, bool)
    film[ia:ib] = True
    top0 = float(colmed[film].max())
    brightest = film & (colmed >= (1.0 - PLATEAU) * top0)
    sigma = float(np.median(noise[brightest] / np.maximum(colmed[brightest], 1e-9)))
    sigma = sigma / np.sqrt(VSMOOTH) * 1.25        # a 5-row median, not a mean
    theta = max(THETA_MIN, THETA_K * sigma)
    trend = _vmean(gs, TREND_ROWS)
    near = np.abs(gs - trend) <= theta * np.maximum(trend, 1e-9)
    uniform = near.mean(axis=0) >= FB_MIN
    uniform[:a] = False
    uniform[b:] = False
    film_u = uniform & film
    if film_u.any():
        top = float(colmed[film_u].max())
        pick = film_u & (colmed >= (1.0 - PLATEAU) * top)
        level = float(np.median(colmed[pick]))
    else:
        level = top0
        notes.append("no uniform column: level from the brightest column")
    # A one-column sliver of base at a border is not uniform (the edge runs
    # across it on a slant), so the plateau can be a dark wall well below the
    # base: r0914_35's wall is the plateau and its right border column reads
    # 1.27 of it. A border column whose median is above the plateau by more
    # than theta says where base is. Not beside a gate: that column is the
    # strip's cut end, part gate.
    for c, gate in ((a, gl), (b - 1, gr)):
        if not gate and colmed[c] > level * (1.0 + theta):
            notes.append(f"base level from border column {c}: {colmed[c] / level:.2f} "
                         f"of the plateau")
            level = float(colmed[c])
    peak = float(np.percentile(gs[:, a:b], 99.5))
    return Plane(rgb, g, gs, trend, colmed, uniform, level, peak, sigma, theta, (a, b),
                 bool(gl), bool(gr), notes)


def _flip(p: Plane) -> Plane:
    """The plane of the mirrored frame."""
    w = p.g.shape[1]
    a, b = p.film
    return Plane(p.rgb[:, ::-1], p.g[:, ::-1], p.gs[:, ::-1], p.trend[:, ::-1], p.colmed[::-1],
                 p.uniform[::-1], p.level, p.peak, p.sigma, p.theta, (w - b, w - a),
                 p.gate_right, p.gate_left, list(p.notes))


def _near(p: Plane, level: float | np.ndarray, cols: slice | int) -> np.ndarray:
    """Per row, is the pixel within theta of ``level`` (both ways). ``level``
    is a number or a per-row profile (h,)."""
    lv: float | np.ndarray = level
    if isinstance(level, np.ndarray) and level.ndim > 0:
        lv = level[:, None] if isinstance(cols, slice) else level
    return np.abs(p.gs[:, cols] - lv) <= p.theta * lv


def _floor(p: Plane) -> float:
    """The lowest level base can have in this frame."""
    return max(LEVEL_FLOOR * p.level, PEAK_FLOOR * p.peak)


def _vmean(a: np.ndarray, k: int) -> np.ndarray:
    """Box mean over ``k`` rows, same shape, edges replicated."""
    r = k // 2
    pad = np.pad(a, ((r + 1, r), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0)
    return (c[k:] - c[:-k]) / k


def _profile(p: Plane, s: int, e: int) -> np.ndarray:
    """(h,) the slow per-row level of columns [s, e): what base is in each row."""
    e = max(e, s + 1)
    return _vmean(np.median(p.gs[:, s:e], axis=1)[:, None], TREND_ROWS)[:, 0]


# --------------------------------------------------------------------------
# candidates on one side (the left; the right is the left of the mirror)

@dataclass
class Candidate:
    """A run of full-height base on one side, and where its inner edge is."""

    start: int                   # first column of the run
    end: int                     # first column past it
    x: float                     # inner edge, fitted at mid-height
    x_top: float
    x_bottom: float
    outer: float | None          # far edge, when the neighbour's picture shows
    level: float                 # the run's own level
    step_frac: float             # rows that place the edge / all rows
    on_frac: float               # of those, on the line
    lo: float
    hi: float
    straight: bool
    border: bool = False         # touches the border (or the gate)
    width: float = 0.0           # columns of base: border (or outer) to x
    reach: float = 0.0           # columns from the border to x
    outer_on: float = 1.0        # the far edge's support, full gaps
    rel_level: float = 1.0       # level / B
    sliver: bool = False         # read from the border columns, not a run
    softness: float = 0.0        # median columns in transition at the edge
    why: str = ""

    def score(self) -> float:
        """Evidence, times the displacement prior."""
        lev = float(np.clip((self.rel_level - LEVEL_FLOOR) / (1.0 - LEVEL_FLOOR), 0.2, 1.0))
        return (self.step_frac * self.on_frac * min(1.0, self.outer_on) * lev
                * float(np.exp(-self.reach / REACH_PRIOR)))


@dataclass
class SideEvidence:
    cands: list[Candidate]
    fb0: float                   # border column: rows at base level (B or its run)
    rel0: float                  # border column median / B
    sliver: Candidate | None
    own0: float = 1.0            # border column: rows within theta of its own median
    plateau: int = 0             # bright uniform columns running in from the border
    dark0: bool = False          # border column below the lowest level base can have


def _crossings(gs: np.ndarray, end: int, level: float | np.ndarray, lo_col: int, hi_col: int,
               inward: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per row, the 50% crossing where base meets picture, area model.

    ``inward`` True: base at columns < end, picture from ``end`` on (the inner
    edge). False: base at columns >= end, picture below it (the outer edge of a
    full gap). Returns (rows, x, soft) for the rows with contrast, ``soft``
    being how many of the eight columns around the crossing are neither base
    nor picture (between 15% and 85% of the way) -- a film edge is one or two,
    a shaded wall six.
    """
    h, w = gs.shape
    hi_col = min(hi_col, w)
    lo_col = max(lo_col, 0)
    empty = (np.zeros(0), np.zeros(0), np.zeros(0))
    if inward:
        pa, pb = min(end + 1, w - 1), min(end + 5, w)
    else:
        pa, pb = max(end - 5, 0), max(end - 1, 1)
    if pb <= pa or hi_col <= lo_col:
        return empty
    p = np.median(gs[:, pa:pb], axis=1)
    span = level - p
    ok = span >= C_MIN * level
    mid = p + 0.5 * span
    window = gs[:, lo_col:hi_col]
    below = window < mid[:, None]
    if inward:
        found = below.any(axis=1)
        c = lo_col + np.argmax(below, axis=1)
    else:
        rev = below[:, ::-1]
        found = rev.any(axis=1)
        c = hi_col - 1 - np.argmax(rev, axis=1)
    ok &= found
    if not ok.any():
        return empty
    rows = np.nonzero(ok)[0]
    c = c[rows]
    pr, sr = p[rows][:, None], np.maximum(span[rows], 1e-9)[:, None]
    # three columns about the crossing, clipped to the frame
    if inward:
        j = np.maximum(c - 1, 0)[:, None] + np.arange(3)[None, :]
    else:
        j = (np.minimum(c + 1, w - 1) - 2)[:, None] + np.arange(3)[None, :]
    valid = (j >= 0) & (j < w)
    f = np.clip((gs[rows[:, None], np.clip(j, 0, w - 1)] - pr) / sr, 0.0, 1.0) * valid
    if inward:
        xs = j[:, 0] + f.sum(axis=1)
    else:
        xs = j[:, 2] + 1 - f.sum(axis=1)
    k = c[:, None] + np.arange(-4, 4)[None, :]
    kv = (k >= 0) & (k < w)
    around = (gs[rows[:, None], np.clip(k, 0, w - 1)] - pr) / sr
    soft = (((around > 0.15) & (around < 0.85)) & kv).sum(axis=1)
    return rows.astype(float), xs.astype(float), soft.astype(float)


def _fit_line(rows: np.ndarray, xs: np.ndarray, h: int) -> tuple[float, float, float, float]:
    """Robust line x(row) through per-row crossings: (x_mid, slope, on, mad)."""
    if xs.size == 0:
        return float("nan"), 0.0, 0.0, float("inf")
    x0 = float(np.median(xs))
    keep = np.abs(xs - x0) <= 2.5
    slope = 0.0
    if keep.sum() >= 20:
        rr = rows[keep] - (h - 1) / 2.0
        A = np.vstack([np.ones_like(rr), rr]).T
        coef, *_ = np.linalg.lstsq(A, xs[keep], rcond=None)
        x0, slope = float(coef[0]), float(coef[1])
        # a frame edge tilts by a column or two over the height, not more
        if abs(slope) * h > 3.0:
            slope = 0.0
            x0 = float(np.median(xs[keep]))
    res = xs - (x0 + slope * (rows - (h - 1) / 2.0))
    on = float(np.mean(np.abs(res) <= ON_LINE))
    inl = np.abs(res) <= 2.5
    mad = float(np.median(np.abs(res[inl]))) if np.any(inl) else float("inf")
    return x0, slope, on, mad


def _interval(x0: float, mad: float, n: int) -> float:
    """Half-width of the honest interval on a fitted edge."""
    if not np.isfinite(mad):
        return 2.0
    return max(0.35, 2.5 * 1.4826 * mad / np.sqrt(max(n, 1)) + 0.25)


def _runs(p: Plane, half: int) -> list[tuple[int, int, float]]:
    """(start, end, level) of runs of uniform columns at one bright level.

    "Uniform" and "at this level" are both judged against the slow vertical
    profile, not a single number: the base of the B&W roll falls by a quarter
    from the middle of the frame to its bottom rows (bw0910_11, columns 0-4:
    1.03 in the middle bands, 0.77-0.80 in the bottom one) while staying free
    of texture, and a single level called it not base."""
    a, b = p.film
    floor = _floor(p)
    out = []
    c = a
    stop = min(b, half + 1)
    while c < stop:
        if p.uniform[c] and p.colmed[c] >= floor:
            s = c
            lev = [p.colmed[c]]
            c += 1
            while (c < stop and p.uniform[c]
                   and abs(p.colmed[c] - np.median(lev)) <= p.theta * np.median(lev)):
                lev.append(p.colmed[c])
                c += 1
            level = float(np.median(lev))
            prof = _profile(p, s, c)
            # grow over columns that are at this level in most rows though
            # not uniform on their own (the run's first and last column)
            e = c
            while e < stop and _near(p, prof, e).mean() >= FB_MIN:
                e += 1
            out.append((s, e, level))
            c = max(c, e)
        else:
            c += 1
    return out


def _side(p: Plane) -> SideEvidence:
    """Every run on the left half, with its edges fitted; and the border."""
    h, w = p.gs.shape
    a, _b = p.film
    out: list[Candidate] = []
    for s, e, level in _runs(p, w // 2):
        prof = _profile(p, s, max(e - 1, s + 1))
        border = s == a or (s - a <= BORDER_SLACK
                            and bool(np.all(_near(p, prof, slice(a, s)).mean(axis=0) >= 0.5)))
        if border:
            s = a
        if e - s >= 3:
            prof = _profile(p, s, e - 1)
        level = float(np.median(prof))
        rows, xs, soft = _crossings(p.gs, e, prof, max(s, e - 4), e + 4, inward=True)
        x0, slope, on, mad = _fit_line(rows, xs, h)
        softness = float(np.mean(soft)) if soft.size else 0.0
        step_frac = rows.size / h
        outer, outer_on, why = None, 1.0, []
        if not border:
            orow, oxs, _osoft = _crossings(p.gs, s, prof, s - 4, min(s + 4, e), inward=False)
            ox, _os, oon, _om = _fit_line(orow, oxs, h)
            if orow.size / h >= MIN_STEP_FRAC and oon >= MIN_ON and np.isfinite(ox):
                outer = ox
                outer_on = (orow.size / h) * oon
            else:
                why.append(f"far side not straight ({orow.size / h:.2f} rows, on {oon:.2f})")
        if not np.isfinite(x0):
            x0 = float(e)
        straight = step_frac >= MIN_STEP_FRAC and on >= MIN_ON and softness <= SOFT_MAX
        if not straight:
            why.append(f"edge not straight and sharp ({step_frac:.2f} rows, on {on:.2f}, "
                       f"soft {softness:.1f})")
        hw = _interval(x0, mad, rows.size)
        out.append(Candidate(
            start=s, end=e, x=x0, x_top=x0 - slope * (h - 1) / 2.0,
            x_bottom=x0 + slope * (h - 1) / 2.0, outer=outer, level=level,
            step_frac=step_frac, on_frac=on, lo=x0 - hw, hi=x0 + hw,
            straight=straight, border=border,
            width=x0 - (outer if outer is not None else float(a)), reach=x0 - float(a),
            outer_on=outer_on, rel_level=level / p.level, softness=softness,
            why="; ".join(why)))

    # The border column itself.
    rel0 = float(p.colmed[a] / p.level) if a < w else 0.0
    dark0 = bool(a < w and p.colmed[a] < _floor(p))
    levels = [p.level] + [c.level for c in out if c.border]
    fb0 = max(float(_near(p, lv, a).mean()) for lv in levels) if a < w else 0.0
    own0 = float(_near(p, p.trend[:, a], a).mean()) if a < w else 0.0
    # How far bright uniform columns run in from the border, breaks of up to
    # two columns allowed: a dim wall at the base level reads as several runs of
    # slightly different level (r0911_12: 1.00, 0.96, 0.95 over 32 columns).
    bright = p.uniform & (p.colmed >= _floor(p))
    c = a
    while c < w // 2 and (bright[c] or (c > a and bool(bright[c + 1:c + 3].any()))):
        c += 1
    plateau = c - a
    sliver = None
    if not any(c.border for c in out) and a + 8 < w and (fb0 >= 0.2 or rel0 >= LEVEL_FLOOR):
        sliver = _sliver(p, a)
    return SideEvidence(out, fb0, rel0, sliver, own0, plateau, dark0)


def _sliver(p: Plane, a: int) -> Candidate | None:
    """A strip of base narrower than a clean run: per row, how much of the
    border columns is base, against the picture beside them.

    The base level is ``B``, or the border column's own upper decile when that
    is higher -- a column that is part base, part picture has no plateau of its
    own to read (r0909_05: column 0 reads 0.5-1.25 of B down its height)."""
    h = p.gs.shape[0]
    v = p.gs[:, a:a + 3]
    pic = np.median(p.gs[:, a + 3:a + 7], axis=1)
    base = max(p.level, float(np.percentile(p.gs[:, a], 90)))
    span = base - pic
    ok = span >= C_MIN * base
    if ok.sum() < MIN_STEP_FRAC * h:
        return None
    f = np.clip((v[ok] - pic[ok, None]) / span[ok, None], 0.0, 1.0).sum(axis=1)
    rows = np.nonzero(ok)[0].astype(float)
    x0, slope, on, mad = _fit_line(rows, a + f, h)
    step_frac = ok.sum() / h
    # Under half a column the reading is the picture's own texture as much as
    # base; that is "no base", said by the caller.
    if (on < MIN_ON or step_frac < SLIVER_MIN_STEP or not np.isfinite(x0)
            or x0 - a > SLIVER_MAX or x0 - a < 0.5):
        return None
    hw = _interval(x0, mad, int(ok.sum())) + 0.25
    return Candidate(start=a, end=a + int(np.ceil(x0 - a)), x=x0,
                     x_top=x0 - slope * (h - 1) / 2.0, x_bottom=x0 + slope * (h - 1) / 2.0,
                     outer=None, level=base, step_frac=step_frac, on_frac=on,
                     lo=x0 - hw, hi=x0 + hw, straight=True, border=True,
                     width=x0 - a, reach=x0 - a, rel_level=1.0, sliver=True,
                     why="sliver read from the border columns")


def _to_right(c: Candidate, w: int) -> Candidate:
    """A left-side candidate of the mirrored frame, in this frame's columns."""
    d = dict(vars(c))
    d.update(start=w - c.end, end=w - c.start, x=w - c.x, x_top=w - c.x_top,
             x_bottom=w - c.x_bottom, outer=None if c.outer is None else w - c.outer,
             lo=w - c.hi, hi=w - c.lo)
    return Candidate(**d)


def _both_sides(image: np.ndarray) -> tuple[Plane, SideEvidence, SideEvidence]:
    """The frame's plane and both sides' evidence, right in this frame's columns."""
    w = image.shape[1]
    p = _plane(image)
    pm = _flip(p)
    left = _side(p)
    rm = _side(pm)
    right = SideEvidence([_to_right(c, w) for c in rm.cands], rm.fb0, rm.rel0,
                         None if rm.sliver is None else _to_right(rm.sliver, w),
                         rm.own0, rm.plateau, rm.dark0)
    return p, left, right


# --------------------------------------------------------------------------
# the roll: its gap width

_CACHE: dict[str, list[float]] = {}


def _consistent_widths(left: SideEvidence, right: SideEvidence) -> list[float]:
    """Full-gap widths that measure G: straight on both edges, well supported,
    and the frame's other side showing no base -- only one side of a frame can
    (r0914_17: a 14-column black grout band at 47-61 while the true gap shows at
    the right; the grout must not become the roll's gap)."""
    def wide_border(side: SideEvidence) -> bool:
        return any(c.border and c.straight and c.width > 2.0 for c in side.cands)

    out = []
    for mine, other in ((left, right), (right, left)):
        if wide_border(other):
            continue
        out.extend(round(c.width, 3) for c in mine.cands
                   if c.outer is not None and c.straight and c.width >= MIN_FULL_GAP
                   and c.step_frac >= 0.5 and c.outer_on >= 0.5)
    return out


def roll_gap(ctx: dict[str, Any]) -> tuple[float, float, list[float]]:
    """(G, tolerance, measurements) for this frame's roll."""
    widths: list[float] = []
    for fid in ctx.get("roll_ids") or []:
        if fid not in _CACHE:
            try:
                _, left, right = _both_sides(load(fid).image)
                _CACHE[fid] = _consistent_widths(left, right)
            except Exception:                                     # noqa: BLE001
                _CACHE[fid] = []
        widths.extend(_CACHE[fid])
    if len(widths) >= G_MIN_MEASURED:
        return float(np.median(widths)), G_TOL_MEASURED, widths
    return G_PRIOR, G_TOL_PRIOR, widths


# --------------------------------------------------------------------------
# the joint fit

def _judge(c: Candidate, gap: float, tol: float) -> tuple[bool, str]:
    """Does the model allow this run as a gap?"""
    if not c.straight:
        return False, c.why
    if c.outer is not None:
        if c.width < MIN_FULL_GAP:
            return False, f"full gap {c.width:.1f} too narrow"
        if abs(c.width - gap) > tol:
            return False, f"full gap {c.width:.1f} is not G={gap:.1f}+-{tol:.0f}"
        return True, ""
    if c.border:
        if c.width > gap + tol:
            return False, f"border run {c.width:.1f} wider than G={gap:.1f}+{tol:.0f}"
        return True, ""
    return False, "run inside the frame without a straight far side"


def _describe(c: Candidate) -> dict[str, Any]:
    return dict(s=c.start, e=c.end, x=round(c.x, 2), soft=c.softness,
                outer=None if c.outer is None else round(c.outer, 2),
                step=round(c.step_frac, 2), on=round(c.on_frac, 2),
                lvl=round(c.rel_level, 3), border=c.border, score=round(c.score(), 3))


def _edge_side(c: Candidate) -> Side:
    conf = float(np.clip(c.step_frac * c.on_frac * 1.2, 0.0, 1.0))
    if c.sliver:
        conf *= 0.6
    return Side(EDGE, x=round(c.x, 3), lo=round(c.lo, 3), hi=round(c.hi, 3),
                conf=round(conf, 3), outer=None if c.outer is None else round(c.outer, 3),
                x_top=round(c.x_top, 3), x_bottom=round(c.x_bottom, 3),
                note=(c.why if c.sliver else
                      f"run {c.start}-{c.end}, {c.step_frac:.0%} of rows place it, "
                      f"{c.on_frac:.0%} on the line"))


def _no_base_side(ev: SideEvidence, why: str, forced: bool, wide: bool) -> Side:
    """No gap on this side. Picture to the border, or will not say.

    Base is uniform and full height, so the border column is picture when

    * it is darker than any base can be (`_floor`), or
      most of its rows are off the base level -- the plain case;
    * it is near the base level but not uniform: some rows hold picture, and
      base is in every row or in none;
    * the other side's gap places the frame (``forced``);
    * the bright plateau it belongs to runs further in than any gap can
      (``wide``): a gap would have to end inside it, in a step no row shows --
      the dim walls of r0911. Least sure of the four.

    A uniform border column at the base level, in a plateau no wider than a gap
    and with no edge that can be placed, is refused: that is exactly the case
    to hand to a person."""
    if ev.dark0:
        return Side(PICTURE_TO_BORDER, conf=0.95,
                    note=f"border column at {ev.rel0:.2f} of the base level: darker than "
                         f"base can be")
    if ev.fb0 < 0.5:
        return Side(PICTURE_TO_BORDER, conf=round(1.0 - ev.fb0, 3),
                    note=f"border column at base level in {ev.fb0:.0%} of rows {why}".strip())
    if ev.own0 < FB_MIN:
        conf = float(np.clip(0.5 + (FB_MIN - ev.own0), 0.5, 0.8))
        return Side(PICTURE_TO_BORDER, conf=round(conf, 3),
                    note=f"border column near base level but uniform in only "
                         f"{ev.own0:.0%} of rows: picture reaches it")
    if forced:
        return Side(PICTURE_TO_BORDER, conf=0.5,
                    note="border column at base level, but the other side's gap places "
                         "the frame past it")
    if wide:
        return Side(PICTURE_TO_BORDER, conf=0.4,
                    note=f"a plateau at base level runs {ev.plateau} columns in from the "
                         f"border with no edge inside it: wider than any gap")
    return Side(REFUSE, note=f"border column at base level in {ev.fb0:.0%} of rows, no "
                             f"straight sharp full-height edge; {why}".strip("; "))


def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    w = image.shape[1]
    p, lev, rev = _both_sides(image)
    debug: dict[str, Any] = {"level": p.level, "theta": round(p.theta, 4),
                             "sigma": round(p.sigma, 5), "notes": p.notes,
                             "border": [round(lev.fb0, 2), round(rev.fb0, 2)],
                             "rel0": [round(lev.rel0, 3), round(rev.rel0, 3)]}

    # The empty gate, and a blank frame, are not edges.
    a, b = p.film
    if p.gate_left and p.gate_right and (a, b) == (0, w):
        s = Side(NO_FILM, conf=0.9, note="the whole aperture is the empty gate")
        return EdgeResult(s, Side(**vars(s)), debug)
    runs = [c for c in lev.cands + rev.cands]
    covered = np.zeros(w, bool)
    for c in runs:
        covered[c.start:c.end] = True
    if covered[a:b].mean() >= ALL_BASE_FRAC and b - a > w // 2:
        s = Side(ALL_BASE, conf=0.8, note="base across the frame")
        return EdgeResult(s, Side(**vars(s)), debug)

    gap, tol, widths = roll_gap(ctx)
    frame_w = pitch_columns(w) - gap
    debug.update(gap=round(gap, 2), gap_tol=tol, gap_widths=widths[:40],
                 frame_width=round(frame_w, 2))

    def best(ev: SideEvidence, name: str) -> tuple[Candidate | None, list[str]]:
        rejected, choice = [], None
        for c in ev.cands:
            ok, why = _judge(c, gap, tol)
            if ok and (choice is None or c.score() > choice.score()):
                choice = c
            elif not ok:
                rejected.append(f"{name} run {c.start}-{c.end}: {why}")
        if choice is None and ev.sliver is not None:
            choice = ev.sliver
        return choice, rejected

    lbest, lrej = best(lev, "left")
    rbest, rrej = best(rev, "right")
    debug["rejected"] = lrej + rrej
    debug["candidates"] = {"left": [_describe(c) for c in lev.cands],
                           "right": [_describe(c) for c in rev.cands],
                           "slivers": [None if s is None else _describe(s)
                                       for s in (lev.sliver, rev.sliver)]}

    # Joint: base on both sides must leave room for a frame of width F.
    joint = ""
    if lbest is not None and rbest is not None:
        bl, br = lbest.x, w - rbest.x
        room = max(0.0, w - frame_w) + 2.0
        if bl + br > room:
            # a sliver is read from two columns; a run from a plateau and a line
            ls = lbest.score() * (SLIVER_WEIGHT if lbest.sliver else 1.0)
            rs = rbest.score() * (SLIVER_WEIGHT if rbest.sliver else 1.0)
            if ls >= JOINT_MARGIN * rs:
                joint = f"right {rbest.x:.1f} dropped: F={frame_w:.0f} leaves no room for both"
                rbest = None
            elif rs >= JOINT_MARGIN * ls:
                joint = f"left {lbest.x:.1f} dropped: F={frame_w:.0f} leaves no room for both"
                lbest = None
            else:
                why = (f"base on both sides ({bl:.1f} + {br:.1f}) cannot fit a frame of "
                       f"{frame_w:.0f}, and neither is clearly the gap")
                debug["joint"] = why
                return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why), debug)
    debug["joint"] = joint

    def as_side(c: Candidate | None, ev: SideEvidence, rej: list[str],
                other: Candidate | None, gate: bool) -> Side:
        if gate:
            return Side(NO_FILM, conf=0.9, note="the empty gate is in view on this side")
        if c is not None:
            return _edge_side(c)
        forced = other is not None and not other.sliver and other.reach > 2.0
        # "wider than a gap" at the typical width, not the prior's far end: it
        # only ever yields a low-confidence border, never an edge
        return _no_base_side(ev, "; ".join(rej)[:200], forced,
                             ev.plateau > gap + G_TOL_MEASURED)

    left = as_side(lbest, lev, lrej, rbest, p.gate_left)
    right = as_side(rbest, rev, rrej, lbest, p.gate_right)
    return EdgeResult(left, right, debug)
