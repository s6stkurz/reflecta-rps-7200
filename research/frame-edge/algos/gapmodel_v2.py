"""gapmodel_v2: place a frame's edges by fitting the strip's own geometry.

Round 2 of `gapmodel` (iter04 stays as it was; this is a copy). What changed,
and why, is at the end of this docstring; the idea is unchanged.

The film is a sequence, and that is the whole idea here::

    [neighbour picture] | base gap | [this frame] | base gap | [next neighbour]

with a pitch that is known (`common.pitch_columns`, 455.3 columns of a
428-wide prescan) and a gap width ``G`` that one camera keeps roughly the
same, give or take its film advance. The frame width is then ``F = pitch - G``,
and on any one prescan:

* at most **one full gap** can be in view (the aperture is narrower than a
  pitch), and a full gap -- neighbour picture on its far side -- must be a
  width the roll's gaps have;
* a gap cut by the border can be at most as wide as the widest gap;
* base on both sides at once needs ``b_left + b_right <= W - F``. Measured:
  ``F`` 438 on library 2 (38 gaps) and ~440 on the main set, both wider than the
  428-column aperture -- only one side of a frame can ever show base, and a
  centred frame shows none.

A candidate that breaks one of these is not a gap, whatever it looks like.
When the two sides each hold a plausible gap and the frame width says only one
can be real, the stronger one -- weighted by how far it would move the frame --
is kept and the other side is picture to the border.

Underneath the geometry sits the per-row evidence, never a whole-frame row
average (the detectors in `docs/frame-measurement-plan.md` that averaged rows
first were each confidently wrong somewhere):

1. **Uniform columns.** A column is uniform when ``FB_MIN`` of its rows lie
   within ``theta`` of its slow vertical profile. The frame's base level ``B``
   is the plateau of its brightest uniform columns -- not of its brightest
   pixels, because on C-41 the orange mask is consumed where dye forms and
   4-7% of picture pixels come out brighter than base (strip6_01).
2. **Runs.** Consecutive uniform columns at one level form a run, if that
   level is one base can have (``LEVEL_FLOOR`` of ``B``, ``PEAK_FLOOR`` of the
   film's brightest pixels). A run whose end shows no step in any row runs on
   into the next run (base is not always flat across a gap: b0921c_48's falls
   by 8% from one side to the other).
3. **Edges, row by row.** At a run's end, every row with contrast places its
   own 50% crossing between the run's level and the picture beside it (area
   model, to a fraction of a column). A line is fitted through them; "a sharp
   line, completely black from top to bottom" means most rows sit on it and
   the transition is one or two columns, not a ramp.
4. **Edges, band by band** (new). Where the picture beside the base is a dark
   scene, the step is a few counts and too few rows clear ``C_MIN``: 10-17% of
   rows on library 2's gaps against shaded foliage and dark interiors, where
   the scene sits 3% below base in the median row. Averaged over bands of
   ``BAND`` rows the same step is five noise sigmas and shows in most bands.
   An edge seen this way must still be a line: most bands step, their
   crossings agree to a column, the step is sharp, the run is at the base
   level, and -- because a dark scene can be as clear as base -- the run must
   have the base's **colour**, the one thing exposure cannot give a scene
   (below).
5. **Slivers.** Base narrower than a clean run (0.5-2.5 columns) is read from
   the border columns' per-row base fraction, and trusted less.
6. **The model** judges each run: its width against the roll's gaps, how deep
   it lies, and the two sides against ``F``. What is left without an edge is
   picture to the border when the border column says so, or when the plateau
   it sits in is wider than any gap; a uniform base-level border no wider than
   a gap with no edge that can be placed is refused.

The roll (``ctx['roll_ids']``) supplies two things, each from the *other*
frames only, so a frame never validates itself:

* the gaps it has shown -- full gaps, both edges straight, the other side of
  that frame free of base. Their median is ``G``.
* the colour of base on it -- log R/G and log B/G of the confirmed base runs
  (straight, strong, at the base level). Ratios, not levels: exposure differs
  across a roll, so levels are never pooled, but the orange mask's colour is
  the film's (R/G 2.1-2.3, B/G ~0.53 on every roll here; spread within a roll
  0.4-2% on library 2).

With no roll at all (a verifying second prescan) the prior serves and weak
edges are refused.

Everything is numpy, symmetric by construction (the right side is the left
side of the mirrored plane), and about 0.05 s a frame.

What round 2 changed (library 2's misses; `algos/BRIEF_ROUND2.md`)
-------------------------------------------------------------------
Scored on both dev sets (main 98 frames, library 2's dev part 78), iter04 of
the old module against iter04 of this one: main 185 -> 191 of 193 sides, false
base 1 -> 0; library 2 135 -> 150 of 152, missed 11 -> 1, false base 0 -> 0.

* **Gap widths vary more than the model allowed.** Library 2 is one strip,
  and its gaps between particular pairs of pictures run 15-26 columns (truth,
  38 gaps); the roll's median is 16-17 because one gap is prescanned 14 times
  in a walk. A full gap is now accepted from ``G - 4`` to ``G + 10`` columns,
  not ``G +- 4``. With no roll measurement the prior spans 4-34 (r0911_17's
  gap is 5.4, the ladder strip's 29.6). One measurement from another frame is
  enough to count as the roll's.
* **Deep gaps are checked against the roll** (``DEEP_REACH``). A full gap more
  than 40 columns in must show both edges as lines (strong by rows, as sharp as
  a film edge where it shows, or seen by bands) and have the roll's base colour
  when the roll has one. If no other frame of the roll shows a gap, it is not
  placed: the side is *refused* unless its colour already says it is not base
  (x0920_73's grout: 0.046 off in B/G, and a picture). This costs the stage9b
  ladder its deep gaps -- the ladder frames have no roll -- and they refuse.
* **Weak edges by bands** (point 4 above), gated by level and colour, placed
  midway between the rows' and the bands' placement (``WEAK_ROWS``).
* **Holds.** A base-coloured run at base level with a straight inner edge
  whose far side does not show, or a deep gap the roll cannot confirm, makes
  its side refuse instead of "picture to the border": that side's border
  column is the neighbour's picture and proves nothing (b0921c_57).
* **Runs that drift** are joined (`_merged_runs`, b0921c_48).
* **A wide border run far off the base colour is picture** (``NOT_BASE_COLOUR``,
  x0920_55's blue-black shadow).
* **The gate beside a sliver of film** is found from one column (b0921b_40:
  two gate columns, previously read as a 4.8x-bright "base level").
* **Slivers need a border column that reaches the brightness floor**
  (``SLIVER_FLOOR_FRAC``; r0911_18/28's picture read as slivers), and a full
  gap beats a sliver on the other side outright (b0922b_15).

Still wrong on dev: b0922b_06 (the gap beside dark grass is within 1-3% of
the grass in every band: invisible to this model), and refused: b0921c_57 (far
edge against foliage, bands on the line 0.83), r0911_19 (a 2-3 column soft
edge), r0911_21.
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
#: An edge is *strong* when this fraction of rows place it: every clean gap edge
#: on both dev sets reads 0.55-1.00; r0914_19's black band beside a TV mosaic
#: 0.29, x0920_73's grout 0.63 (strong -- the roll check, not this, stops it).
STRONG_STEP = 0.5
#: Gap width, columns. Whole gaps: r0914 14.7-17.8, x0920 ~14, library 2 15-26
#: (one strip; 38 gaps labelled), the ladder strip 29.6 (the 36 mm frame), and
#: r0911_17's 5.4. ``G_PRIOR`` is the typical gap: pitch 455.3 minus the frame
#: width measured on both sets, 438.
G_PRIOR = 17.0
#: With no roll measurement, any full gap in this range is possible.
G_LO_PRIOR = 4.0
G_HI_PRIOR = 34.0
#: With one: from ``G - G_TOL_BELOW`` to ``G + G_TOL_ABOVE``. Asymmetric,
#: because a roll's median sits at its common gap and the rarer ones are wider
#: (library 2: median 16-17 on b0921c/b0922b, gaps up to 25.8 on both).
G_TOL_BELOW = 4.0
G_TOL_ABOVE = 10.0
#: Measurements from other frames a roll needs before their median replaces
#: the prior. One: r0914_20's own 17.8-column gap lies 103 columns in, and the
#: roll's only other measurement (r0914_24, 14.7) is what vouches for it.
G_MIN_MEASURED = 1
#: A full gap whose inner edge lies more than this many columns in from the
#: border is *deep*: a walked frame is aimed at the centre, so a band there is
#: as likely a black stripe in the scene as a gap. It needs the roll to have
#: shown a gap (x0920_73: grout, 70 columns in, no other gap on its roll), the
#: roll's base colour where it has one (the same grout on x0920_73's duplicates,
#: whose roll *does* show a gap -- x0920_73's own), and both edges seen as lines
#: (r0914_19). Library 2's real gaps lie 18-105 in.
DEEP_REACH = 40.0
#: Scale of the displacement prior, columns: a candidate's score is multiplied
#: by exp(-reach / REACH_PRIOR). It only decides between two candidates that the
#: frame width says cannot both be gaps; it never rejects one on its own
#: (r0914_17: a 14-column black grout band at 47-61 against a clean 9.6-column
#: gap at the right).
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
#: An edge seen in fewer than ``STRONG_STEP`` of rows must, where it shows, be
#: as sharp as a film edge: median transition columns per row. The camera's
#: gate edge sits on the film; a scene edge comes through the lens. Library 2's
#: deep gaps beside a dark scene, seen only against the sky in their top rows:
#: 0.1-0.2 (b0921c_54, b0922b_07); r0914_19's TV screens against their black
#: surround: 1.3. Clean strong edges on both sets read 0.1-1.2.
SHARP_PARTIAL = 1.0
#: Full gaps narrower than this are not gaps (r0911_17's is 5.4).
MIN_FULL_GAP = 4.0
#: A run whose end shows a step in fewer rows than this has no edge there; if
#: the next run starts where it ends, the two are one run.
MERGE_STEP = 0.05
#: Rows per band for the band evidence. The edge tilts by at most a column or
#: two over the 276 rows, so over 15 it moves a tenth of a column; the band
#: mean cuts the per-pixel noise by ~4 (8-bit C-41 base: 1.7% per pixel of the
#: channel sum, 0.45% per band).
BAND = 15
#: A band steps when its picture is this far below the run, at least ...
BAND_C_MIN = 0.02
#: ... and this many band-noise sigmas (B&W grain at 7% per pixel: 7.2%).
BAND_K = 4.0
#: An edge seen by bands: this fraction of bands step (library 2's dark-scene
#: gaps: 0.56-0.94; a dim wall that only ramps away: 0-0.3 at the level
#: needed), their crossings are on one line within ``ON_LINE`` ...
BAND_MIN_FRAC = 0.5
BAND_ON = 0.85
#: ... and the step is sharp: median columns in transition per band.
BAND_SOFT_MAX = 2.0
#: Where an edge is seen by bands, the few rows that clear ``C_MIN`` (scene
#: features touching the edge) place it too, if at least this fraction of rows
#: do and they are on a line. The two placements disagree by 0.1-1.4 columns on
#: the ten band-seen edges of both dev sets, in opposite directions from the
#: labels -- rows +0.4 on average (a feature's own soft edge), bands -0.2 (the
#: dark scene's slow gradient pulls the 50% point) -- so the edge is put at
#: their midpoint and its interval spans both. Alone, bands get +-1 column.
WEAK_ROWS = 0.05
#: A run seen only by bands must be this close to ``B``: dark-scene gaps read
#: 0.99-1.00; the dim walls of r0911 that step by bands, 0.90-0.98.
WEAK_LEVEL = 0.985
#: ... and have the roll's base colour to this, in log R/G and log B/G. True
#: base across library 2 and the main set: within 0.02 of its roll (8-bit, runs
#: of three or more interior columns); a dim wall 0.05-0.32 (r0911), a grout
#: band 0.044-0.048 in B/G (r0914_17, x0920_73), a blue-black shadow 0.99.
COLOUR_TOL = 0.03
#: A border run of three or more interior columns whose colour is this far off
#: the roll's base is picture, whatever its level: true base runs on both sets
#: read at most 0.047 off (their interior still touches an edge column); the
#: blue-black shadow at x0920_55's left border 0.96, r0911's walls 0.14-0.32.
NOT_BASE_COLOUR = 0.10
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
#: Gate columns needed to call it the gate. One: b0921b_40 shows two, and the
#: brightness (1.8x any film column), neutrality and quiet already rule film out.
GATE_MIN_COLS = 1
#: A border column may raise the base level (a sliver of base is not uniform)
#: but by no more than this: r0914_35's reads 1.27 of its dark-wall plateau; the
#: gate reads 4.8.
BORDER_LEVEL_MAX = 1.5
#: A blank frame: this fraction of film columns in base runs.
ALL_BASE_FRAC = 0.90
#: Sliver: a border strip narrower than a clean run, read from the border
#: columns' own per-row base fraction. Accepted up to this width.
SLIVER_MAX = 2.5
#: ... and only when this fraction of rows places it. A clean sliver is read in
#: 84-100% of rows (r0909_08/10, r0914_31, x0920_10); the B&W "slivers" that
#: disagreed with their own shifted repeats by 5-6 columns were read in 26-46%.
SLIVER_MIN_STEP = 0.6
#: ... and only when its border column reaches `_floor` in this fraction of
#: rows. True slivers: 0.51-1.00 (r0909_05/08/10, r0914_31, x0920_10); the
#: picture read as a sliver on r0911_18/28 (not base-coloured, 32/12/6 against
#: base 66/29/16): 0.19-0.22.
SLIVER_FLOOR_FRAC = 0.4
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
    gb: np.ndarray               # (nb, W) band means of g
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

    @property
    def raw_noise(self) -> float:
        """Relative noise of one unsmoothed pixel of the channel sum."""
        return self.sigma * np.sqrt(VSMOOTH) / 1.25


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


def _bands(g: np.ndarray) -> np.ndarray:
    """(nb, W) means over bands of ``BAND`` rows, the remainder split top and bottom."""
    h = g.shape[0]
    nb = max(h // BAND, 1)
    top = (h - nb * BAND) // 2
    return g[top:top + nb * BAND].reshape(nb, BAND, -1).mean(axis=1)


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
    if n < GATE_MIN_COLS:
        return 0
    gate = slice(0, n) if from_left else slice(w - n, w)
    # the film's end is cut on a slant: its first few columns are part gate
    rest = slice(n + GATE_CUT, w) if from_left else slice(0, max(w - n - GATE_CUT, 0))
    level = float(np.median(colmed[gate]))
    if float(np.median(noise[gate])) / max(level, 1e-9) > GATE_NOISE:
        return 0
    if n + GATE_CUT >= w or float(colmed[rest].max()) * GATE_RATIO > level:
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
    # than theta says where base is -- unless it is far brighter than base can
    # be against any plateau (a column or two of gate the gate test missed).
    # Not beside a gate: that column is the strip's cut end, part gate.
    for c, gate in ((a, gl), (b - 1, gr)):
        if (not gate and colmed[c] > level * (1.0 + theta)
                and colmed[c] <= level * BORDER_LEVEL_MAX):
            notes.append(f"base level from border column {c}: {colmed[c] / level:.2f} "
                         f"of the plateau")
            level = float(colmed[c])
    peak = float(np.percentile(gs[:, a:b], 99.5))
    return Plane(rgb, g, gs, trend, _bands(g), colmed, uniform, level, peak, sigma, theta,
                 (a, b), bool(gl), bool(gr), notes)


def _flip(p: Plane) -> Plane:
    """The plane of the mirrored frame."""
    w = p.g.shape[1]
    a, b = p.film
    return Plane(p.rgb[:, ::-1], p.g[:, ::-1], p.gs[:, ::-1], p.trend[:, ::-1], p.gb[:, ::-1],
                 p.colmed[::-1], p.uniform[::-1], p.level, p.peak, p.sigma, p.theta,
                 (w - b, w - a), p.gate_right, p.gate_left, list(p.notes))


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


def _colour(p: Plane, s: int, e: int) -> tuple[float, float]:
    """(log R/G, log B/G) of columns [s, e): the mean over pixels whose channel
    sum lies within the 10-90th percentile (dust and scratches out). A mean,
    not a median: on 8-bit film blue is ~16 counts, and a median of integers
    cannot resolve the 3% that tells base from a black scene."""
    px = p.rgb[:, s:max(e, s + 1)].reshape(-1, 3)
    tot = px.sum(axis=1)
    lo, hi = np.percentile(tot, [10, 90])
    keep = (tot >= lo) & (tot <= hi)
    m = np.maximum(px[keep].mean(axis=0) if keep.any() else px.mean(axis=0), 1e-9)
    return float(np.log(m[0] / m[1])), float(np.log(m[2] / m[1]))


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
    step_frac: float             # rows (or bands) that place the edge / all
    on_frac: float               # of those, on the line
    lo: float
    hi: float
    straight: bool               # the inner edge is seen, by rows or by bands
    border: bool = False         # touches the border (or the gate)
    width: float = 0.0           # columns of base: border (or outer) to x
    reach: float = 0.0           # columns from the border to x
    span: float = 0.0            # columns from the run's own start to x (mirror-safe)
    outer_on: float = 1.0        # the far edge's support, full gaps
    rel_level: float = 1.0       # level / B
    sliver: bool = False         # read from the border columns, not a run
    softness: float = 0.0        # median columns in transition at the edge
    inner_by: str = ""           # "rows", "bands" or "" (not seen)
    outer_by: str = ""
    inner_strong: bool = False   # rows, and at least STRONG_STEP of them
    outer_strong: bool = False
    lines: bool = True           # both edges show as a line (deep gaps: see _side)
    colour: tuple[float, float] = (0.0, 0.0)
    why: str = ""

    @property
    def weak(self) -> bool:
        """Some edge of it is seen only by bands."""
        return self.inner_by == "bands" or (self.outer is not None and self.outer_by == "bands")

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
               inward: bool, cmin: float = C_MIN, contiguous: bool = False
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per row, the 50% crossing where base meets picture, area model.

    ``inward`` True: base at columns < end, picture from ``end`` on (the inner
    edge). False: base at columns >= end, picture below it (the outer edge of a
    full gap). Returns (rows, x, soft) for the rows with contrast of at least
    ``cmin``, ``soft`` being how many of the eight columns around the crossing
    are neither base nor picture (between 15% and 85% of the way) -- a film
    edge is one or two, a shaded wall six. ``gs`` may be rows or bands.

    ``contiguous`` counts only the transition columns that touch the crossing
    (walking out from it while the value stays between 15% and 85%), so that
    texture in the picture beyond does not count as softness: on bands, the
    foliage beside b0921c_57's gap put three of the eight columns in between
    by texture alone, while the step itself took one.
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
    ok = span >= cmin * level
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
    mid_ok = ((around > 0.15) & (around < 0.85)) & kv
    if not contiguous:
        soft = mid_ok.sum(axis=1)
    else:
        # index 4 is the crossing column c (first below mid, inward; last
        # below mid, outward); the base side is 3 and down (inward) or 5 and
        # up (outward). Count the unbroken in-between columns either side.
        if inward:
            pic_side, base_side = mid_ok[:, 4:], mid_ok[:, 3::-1]
        else:
            pic_side, base_side = mid_ok[:, 4::-1], mid_ok[:, 5:]
        soft = (np.cumprod(pic_side, axis=1).sum(axis=1)
                + np.cumprod(base_side, axis=1).sum(axis=1))
    return rows.astype(float), xs.astype(float), soft.astype(float)


def _fit_line(rows: np.ndarray, xs: np.ndarray, h: int,
              min_fit: int = 20) -> tuple[float, float, float, float]:
    """Robust line x(row) through per-row crossings: (x_mid, slope, on, mad)."""
    if xs.size == 0:
        return float("nan"), 0.0, 0.0, float("inf")
    x0 = float(np.median(xs))
    keep = np.abs(xs - x0) <= 2.5
    slope = 0.0
    if keep.sum() >= min_fit:
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


@dataclass
class BandEdge:
    """An edge read from bands of rows: ``frac`` of bands step, ``on`` of those
    on the line, ``soft`` the median transition width."""

    frac: float
    on: float
    soft: float
    x: float
    slope: float
    hw: float

    @property
    def seen(self) -> bool:
        return (self.frac >= BAND_MIN_FRAC and self.on >= BAND_ON
                and self.soft <= BAND_SOFT_MAX and bool(np.isfinite(self.x)))


def _band_edge(p: Plane, s: int, e: int, inward: bool, both: bool = False) -> BandEdge:
    """The edge of run [s, e) at ``e`` (inward) or ``s`` (the far side), by bands.

    Base per band is the run's median over its columns nearest the edge; the
    picture is the four columns beyond, as for rows. A band steps when its
    picture is ``BAND_C_MIN`` below base and ``BAND_K`` band-noise sigmas.

    ``both`` also counts bands whose picture is that much *brighter* than the
    run -- C-41 picture where the dye consumed the mask. That is the question
    "does the line show in this band at all", asked of a run already known to
    be base-like; the brighter bands are reflected about the base and crossed
    the same way. b0921c_54's inner edge steps down in 39% of bands and up in
    61%."""
    gb = p.gb
    nb, _w = gb.shape
    h = p.g.shape[0]
    if inward:
        cols = slice(max(s, e - 8), max(e - 1, s + 1))
    else:
        cols = slice(min(s + 1, e - 1), max(min(e, s + 8), s + 1))
    base = np.median(gb[:, cols], axis=1)
    cmin = max(BAND_C_MIN, BAND_K * p.raw_noise / np.sqrt(BAND))
    planes = [gb, 2.0 * base[:, None] - gb] if both else [gb]
    found = []
    for q in planes:
        if inward:
            found.append(_crossings(q, e, base, max(s, e - 4), e + 4, True, cmin, True))
        else:
            found.append(_crossings(q, s, base, s - 4, min(s + 4, e), False, cmin, True))
    rows = np.concatenate([f[0] for f in found])
    xs = np.concatenate([f[1] for f in found])
    soft = np.concatenate([f[2] for f in found])
    if rows.size == 0:
        return BandEdge(0.0, 0.0, 99.0, float("nan"), 0.0, 2.0)
    centre = (rows + 0.5) * (h / nb)
    x0, slope, on, mad = _fit_line(centre, xs, h, min_fit=6)
    return BandEdge(rows.size / nb, on, float(np.median(soft)), x0, slope,
                    _interval(x0, mad, rows.size) + 0.15)


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


def _merged_runs(p: Plane, half: int) -> list[tuple[int, int, float]]:
    """`_runs`, with a run joined to the next when nothing separates them:
    base that drifts across a gap (b0921c_48: 1.02 of B beside the picture,
    0.93 at the border, 1% a column) is read as two levels, because a run is
    held to the median of its first columns. They are one run when the next
    starts where this one ends, this one's end steps down in almost no row, and
    the two columns either side of the junction agree within theta in most
    rows -- a drift, not the step up from a dim wall to a black band
    (r0914_19: 0.94 to 1.00 at column 83)."""
    runs = _runs(p, half)
    h = p.gs.shape[0]
    out: list[tuple[int, int, float]] = []
    i = 0
    while i < len(runs):
        s, e, lev = runs[i]
        while i + 1 < len(runs) and runs[i + 1][0] - e <= 1:
            prof = _profile(p, s, max(e - 1, s + 1))
            rows, _xs, _soft = _crossings(p.gs, e, prof, max(s, e - 4), e + 4, True)
            left, right = p.gs[:, e - 1], p.gs[:, runs[i + 1][0]]
            agree = float(np.mean(np.abs(right - left) <= p.theta * np.maximum(left, 1e-9)))
            if rows.size / h >= MERGE_STEP or agree < FB_MIN:
                break
            i += 1
            e = runs[i][1]
        out.append((s, e, lev))
        i += 1
    return out


def _side(p: Plane) -> SideEvidence:
    """Every run on the left half, with its edges fitted; and the border."""
    h, w = p.gs.shape
    a, _b = p.film
    out: list[Candidate] = []
    for s, e, level in _merged_runs(p, w // 2):
        prof = _profile(p, s, max(e - 1, s + 1))
        border = s == a or (s - a <= BORDER_SLACK
                            and bool(np.all(_near(p, prof, slice(a, s)).mean(axis=0) >= 0.5)))
        if border:
            s = a
        if e - s >= 3:
            prof = _profile(p, s, e - 1)
        level = float(np.median(prof))
        why: list[str] = []
        # the inner edge, by rows
        rows, xs, soft = _crossings(p.gs, e, prof, max(s, e - 4), e + 4, inward=True)
        x0, slope, on, mad = _fit_line(rows, xs, h)
        softness = float(np.mean(soft)) if soft.size else 0.0
        step_frac = rows.size / h
        by_rows = (step_frac >= MIN_STEP_FRAC and on >= MIN_ON and softness <= SOFT_MAX
                   and bool(np.isfinite(x0)))
        inner_by = "rows" if by_rows else ""
        hw = _interval(x0, mad, rows.size)
        if not by_rows:
            be = _band_edge(p, s, e, True)
            if be.seen:
                inner_by = "bands"
                # Where the rows that show the edge agree on a line, the edge
                # sits between their placement and the bands' (see WEAK_ROWS).
                if (rows.size / h >= WEAK_ROWS and on >= MIN_ON and np.isfinite(x0)
                        and abs(x0 - be.x) <= 2.0):
                    lo_x, hi_x = min(x0, be.x), max(x0, be.x)
                    x0, slope = (x0 + be.x) / 2.0, (slope + be.slope) / 2.0
                    hw = max((hi_x - lo_x) / 2.0 + 0.35, 0.5)
                else:
                    x0, slope, hw = be.x, be.slope, max(be.hw, 1.0)
                step_frac, on, softness = be.frac, be.on, be.soft
            else:
                why.append(f"edge not straight and sharp ({step_frac:.2f} rows, on {on:.2f}, "
                           f"soft {softness:.1f}; bands {be.frac:.2f}, on {be.on:.2f}, "
                           f"soft {be.soft:.1f})")
        # the far edge, full gaps
        outer, outer_on, outer_by, outer_strong, outer_line = None, 1.0, "", False, False
        if not border:
            orow, oxs, osoft = _crossings(p.gs, s, prof, s - 4, min(s + 4, e), inward=False)
            ox, _os, oon, _om = _fit_line(orow, oxs, h)
            osoftness = float(np.mean(osoft)) if osoft.size else 0.0
            if orow.size / h >= MIN_STEP_FRAC and oon >= MIN_ON and np.isfinite(ox):
                outer, outer_on, outer_by = ox, (orow.size / h) * oon, "rows"
                outer_strong = orow.size / h >= STRONG_STEP
                outer_line = outer_strong or osoftness <= SHARP_PARTIAL
            else:
                bo = _band_edge(p, s, e, False)
                if bo.seen:
                    outer, outer_on, outer_by, outer_line = bo.x, bo.frac * bo.on, "bands", True
                else:
                    why.append(f"far side not straight ({orow.size / h:.2f} rows, on {oon:.2f}; "
                               f"bands {bo.frac:.2f}, on {bo.on:.2f})")
        if not np.isfinite(x0):
            x0 = float(e)
        # A deep full gap must show both its edges as lines: strong by rows,
        # or sharp as a film edge in the rows that show it, or by bands of rows
        # in either direction (the picture beside a gap can be brighter than
        # base as well as darker).
        lines = True
        if outer is not None and x0 - a > DEEP_REACH:
            inner_line = (inner_by == "bands"
                          or (inner_by == "rows" and (step_frac >= STRONG_STEP
                                                      or softness <= SHARP_PARTIAL)))
            lines = ((inner_line or _band_edge(p, s, e, True, both=True).seen)
                     and (outer_line or _band_edge(p, s, e, False, both=True).seen))
        interior = (s + 1, e - 1) if e - s >= 4 else (s, e)
        out.append(Candidate(
            start=s, end=e, x=x0, x_top=x0 - slope * (h - 1) / 2.0,
            x_bottom=x0 + slope * (h - 1) / 2.0, outer=outer, level=level,
            step_frac=step_frac, on_frac=on, lo=x0 - hw, hi=x0 + hw,
            straight=bool(inner_by), border=border,
            width=x0 - (outer if outer is not None else float(a)), reach=x0 - float(a),
            span=x0 - float(s),
            outer_on=outer_on, rel_level=level / p.level, softness=softness,
            inner_by=inner_by, outer_by=outer_by,
            inner_strong=inner_by == "rows" and step_frac >= STRONG_STEP,
            outer_strong=outer_strong, lines=lines, colour=_colour(p, *interior),
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
    own to read (r0909_05: column 0 reads 0.5-1.25 of B down its height). A
    column that is half base or more reaches the lowest level base can have
    (`_floor`) in most rows; one that does not is picture, and its upper decile
    only the picture's bright rows (``SLIVER_FLOOR_FRAC``)."""
    if float(np.mean(p.gs[:, a] >= _floor(p))) < SLIVER_FLOOR_FRAC:
        return None
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
                     inner_by="rows", inner_strong=True,
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
# the roll: its gap width and its base colour

@dataclass
class Roll:
    gap: float                   # G: the median measured gap, or the prior
    lo: float                    # full gaps from lo ...
    hi: float                    # ... to hi columns
    measured: bool               # G comes from the roll
    widths: list[float]
    colour: tuple[float, float] | None   # the roll's base colour, or None
    n_colour: int


_CACHE: dict[str, tuple[list[float], list[tuple[float, float]]]] = {}


def _consistent_widths(left: SideEvidence, right: SideEvidence) -> list[float]:
    """Full-gap widths that measure G: straight by rows on both edges, well
    supported, and the frame's other side showing no base -- only one side of a
    frame can (r0914_17: a 14-column black grout band at 47-61 while the true
    gap shows at the right; the grout must not become the roll's gap)."""
    def wide_border(side: SideEvidence) -> bool:
        return any(c.border and c.straight and c.width > 2.0 for c in side.cands)

    out = []
    for mine, other in ((left, right), (right, left)):
        if wide_border(other):
            continue
        out.extend(round(c.width, 3) for c in mine.cands
                   if c.outer is not None and c.inner_by == "rows" and c.outer_by == "rows"
                   and c.width >= MIN_FULL_GAP and c.step_frac >= 0.5 and c.outer_on >= 0.5)
    return out


def _base_colours(left: SideEvidence, right: SideEvidence) -> list[tuple[float, float]]:
    """Colours of the frame's confirmed base: runs whose inner edge is strong by
    rows, at the base level, with three or more interior columns (the columns
    beside an edge are part picture), and no wider than any gap."""
    out = []
    for side in (left, right):
        for c in side.cands:
            if (c.inner_strong and c.rel_level >= WEAK_LEVEL and c.end - c.start >= 5
                    and (c.border or c.outer_by == "rows") and c.width <= G_HI_PRIOR):
                out.append(c.colour)
    return out


def roll_of(ctx: dict[str, Any]) -> Roll:
    """What the other frames of this frame's roll say: G and the base colour."""
    widths: list[float] = []
    colours: list[tuple[float, float]] = []
    for fid in ctx.get("roll_ids") or []:
        if fid not in _CACHE:
            try:
                _, left, right = _both_sides(load(fid).image)
                _CACHE[fid] = (_consistent_widths(left, right), _base_colours(left, right))
            except Exception:                                     # noqa: BLE001
                _CACHE[fid] = ([], [])
        widths.extend(_CACHE[fid][0])
        colours.extend(_CACHE[fid][1])
    colour = None
    if colours:
        arr = np.array(colours)
        colour = (float(np.median(arr[:, 0])), float(np.median(arr[:, 1])))
    if len(widths) >= G_MIN_MEASURED:
        g = float(np.median(widths))
        return Roll(g, max(MIN_FULL_GAP, g - G_TOL_BELOW), g + G_TOL_ABOVE, True, widths,
                    colour, len(colours))
    return Roll(G_PRIOR, G_LO_PRIOR, G_HI_PRIOR, False, widths, colour, len(colours))


# --------------------------------------------------------------------------
# the joint fit

def _colour_off(c: Candidate, roll: Roll) -> float | None:
    """How far the run's colour is from the roll's base colour (log units), or
    None when the roll has none."""
    if roll.colour is None:
        return None
    return max(abs(c.colour[0] - roll.colour[0]), abs(c.colour[1] - roll.colour[1]))


def _judge(c: Candidate, roll: Roll) -> tuple[bool, str, bool]:
    """Does the model allow this run as a gap? (ok, why, hold).

    ``hold`` marks a rejected run that may still be a gap -- base-coloured, a
    straight inner edge, but a far side or a roll that cannot confirm it. A
    side with a hold is refused rather than called picture: the border column
    of a side whose gap lies inside the aperture is the *neighbour's* picture,
    so "the border is dark" says nothing there."""
    off = _colour_off(c, roll)
    if not c.straight:
        return False, c.why, False
    if c.weak:
        if c.rel_level < WEAK_LEVEL:
            return False, f"edge seen only by bands, run at {c.rel_level:.3f} of B", False
        if off is None:
            return False, "edge seen only by bands, and no roll base colour to check it", False
        if off > COLOUR_TOL:
            return False, f"edge seen only by bands, colour {off:.3f} off the roll's base", False
    if c.outer is not None:
        if c.width < MIN_FULL_GAP:
            return False, f"full gap {c.width:.1f} too narrow", False
        if c.reach > DEEP_REACH:
            if off is not None and off > COLOUR_TOL:
                return False, (f"full gap {c.reach:.0f} columns in, colour {off:.3f} off the "
                               f"roll's base"), False
            if not c.lines:
                return False, (f"full gap {c.reach:.0f} columns in, and its edges do not both "
                               f"show as a line"), False
            if not roll.measured:
                return False, (f"full gap {c.reach:.0f} columns in, and no other frame of the "
                               f"roll shows a gap to confirm it"), True
        if not roll.lo <= c.width <= roll.hi:
            return False, (f"full gap {c.width:.1f} is not a gap of this roll "
                           f"({roll.lo:.1f}-{roll.hi:.1f})"), False
        return True, "", False
    if c.border:
        if c.width > roll.hi:
            return False, f"border run {c.width:.1f} wider than any gap ({roll.hi:.1f})", False
        return True, "", False
    # a run inside the frame whose far side does not show: a gap if its colour,
    # level and width say so, but not one that can be placed
    hold = (off is not None and off <= COLOUR_TOL and c.rel_level >= WEAK_LEVEL
            and roll.lo <= c.span <= roll.hi)
    return False, "run inside the frame without a straight far side", hold


def _describe(c: Candidate) -> dict[str, Any]:
    return dict(s=c.start, e=c.end, x=round(c.x, 2), soft=round(c.softness, 2),
                outer=None if c.outer is None else round(c.outer, 2),
                step=round(c.step_frac, 2), on=round(c.on_frac, 2),
                lvl=round(c.rel_level, 3), border=c.border, score=round(c.score(), 3),
                by=f"{c.inner_by}/{c.outer_by}", col=[round(v, 3) for v in c.colour])


def _edge_side(c: Candidate) -> Side:
    conf = float(np.clip(c.step_frac * c.on_frac * 1.2, 0.0, 1.0))
    if c.sliver:
        conf *= 0.6
    if c.weak:
        conf *= 0.6
    by = "" if not c.weak else " (seen by bands of rows)"
    return Side(EDGE, x=round(c.x, 3), lo=round(c.lo, 3), hi=round(c.hi, 3),
                conf=round(conf, 3), outer=None if c.outer is None else round(c.outer, 3),
                x_top=round(c.x_top, 3), x_bottom=round(c.x_bottom, 3),
                note=(c.why if c.sliver else
                      f"run {c.start}-{c.end}, {c.step_frac:.0%} of "
                      f"{'bands' if c.inner_by == 'bands' else 'rows'} place it, "
                      f"{c.on_frac:.0%} on the line{by}"))


def _no_base_side(ev: SideEvidence, why: str, forced: bool, wide: bool,
                  foreign: float | None = None) -> Side:
    """No gap on this side. Picture to the border, or will not say.

    Base is uniform and full height, so the border column is picture when

    * it is darker than any base can be (`_floor`), or
      most of its rows are off the base level -- the plain case;
    * it is near the base level but not uniform: some rows hold picture, and
      base is in every row or in none;
    * the level run it belongs to is not base-coloured (``foreign``, its
      distance from the roll's base colour);
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
    if foreign is not None:
        return Side(PICTURE_TO_BORDER, conf=0.6,
                    note=f"the level run at the border is {foreign:.2f} off the roll's base "
                         f"colour: not base")
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

    roll = roll_of(ctx)
    frame_w = pitch_columns(w) - roll.gap
    debug.update(gap=round(roll.gap, 2), gap_range=[round(roll.lo, 1), round(roll.hi, 1)],
                 gap_measured=roll.measured, gap_widths=roll.widths[:40],
                 roll_colour=None if roll.colour is None else [round(v, 3) for v in roll.colour],
                 frame_width=round(frame_w, 2))

    def best(ev: SideEvidence, name: str) -> tuple[Candidate | None, list[str], list[str]]:
        rejected, held, choice = [], [], None
        for c in ev.cands:
            ok, why, hold = _judge(c, roll)
            if ok and (choice is None or c.score() > choice.score()):
                choice = c
            elif not ok:
                rejected.append(f"{name} run {c.start}-{c.end}: {why}")
                if hold:
                    held.append(f"run {c.start}-{c.end} may be a gap: {why}")
        if choice is None and ev.sliver is not None:
            choice = ev.sliver
        return choice, rejected, held

    lbest, lrej, lheld = best(lev, "left")
    rbest, rrej, rheld = best(rev, "right")
    debug["rejected"] = lrej + rrej
    debug["held"] = lheld + rheld
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
            # a sliver is read from two columns; a run from a plateau and a line;
            # a full gap that fits the roll from two lines (b0922b_15: a 21-column
            # gap with both edges against a 0.5-column sliver at the right)
            ls = lbest.score() * (SLIVER_WEIGHT if lbest.sliver else 1.0)
            rs = rbest.score() * (SLIVER_WEIGHT if rbest.sliver else 1.0)
            if rbest.sliver and lbest.outer is not None:
                ls = np.inf
            elif lbest.sliver and rbest.outer is not None:
                rs = np.inf
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

    def as_side(c: Candidate | None, ev: SideEvidence, rej: list[str], held: list[str],
                other: Candidate | None, gate: bool) -> Side:
        if gate:
            return Side(NO_FILM, conf=0.9, note="the empty gate is in view on this side")
        if c is not None:
            return _edge_side(c)
        forced = other is not None and not other.sliver and other.reach > 2.0
        if held and not forced:
            return Side(REFUSE, note="; ".join(held)[:200])
        offs = [_colour_off(b, roll) for b in ev.cands if b.border and b.end - b.start >= 5]
        foreign = max((o for o in offs if o is not None), default=None)
        # "wider than a gap" at the typical width plus the spread above it, not
        # the prior's far end: it only ever yields a low-confidence border
        return _no_base_side(ev, "; ".join(rej)[:200], forced,
                             ev.plateau > roll.gap + G_TOL_ABOVE,
                             foreign if foreign is not None and foreign > NOT_BASE_COLOUR
                             else None)

    left = as_side(lbest, lev, lrej, lheld, rbest, p.gate_left)
    right = as_side(rbest, rev, rrej, rheld, lbest, p.gate_right)
    return EdgeResult(left, right, debug)
