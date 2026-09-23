"""The `chroma` family: base is one colour at one density, tested per pixel in log-density.

Unexposed C-41 base is the least dense thing on the film in **every channel at
once** -- the orange mask at its own density. A picture pixel is base plus a
non-negative dye density, so in log space it sits at ``b + e`` with ``e >= 0``
channel by channel. This detector estimates ``b`` for the frame, splits every
pixel's deviation from it into two parts measured in noise sigmas,

* ``zd`` -- the **density excess**: the noise-weighted mean of ``b_c - ln I_c``
  over the three channels, one-sided, the part a neutral dark area moves;
* ``zp2`` -- the **chroma deviation**: what is left of the three-channel
  chi-square once ``zd`` is taken out, two degrees of freedom, the part a
  *coloured* area moves even when it is as clear as base,

and calls a pixel base only when both are small. Then, per row, it finds where
base stops going inward, and requires the rows to agree on one straight line.

What was measured before any rule was written (dev frames only):

* Base noise in 8-bit prescans, per pixel, in ln-units: R 0.03-0.04, G 0.045,
  B 0.065-0.085 (r0914_05, r0909_01, r0911_05, strip6_01). Base is flat to +-1%
  along y and +-1% across its columns.
* **Chroma cannot separate a neutral dark area from base, and it cannot survive
  an exposure change here.** A neutral scene density adds the same ``k`` to all
  three log channels, which leaves the ratios alone. The x0920 dups put the base
  of one position at R/G 2.2 and at 0.97 at another exposure. Worse, r0909 has
  base at (56, 26, 15) in one prescan and at (68, 30, 16) in its dup: the ratio
  vector (0.82, 0.87, 0.94) is the one a dark wall shows against base in r0911_01
  (0.79, 0.86, 0.90). So a level from *another* frame can confirm a base but is
  never allowed to veto one.
* **Scene black is base.** An unexposed part of the scene -- the black behind
  the lamps in r0914_20, the gaps between tiles in r0914_19 -- reads base to the
  count (69, 31, 16). Nothing in colour distinguishes it; only geometry does:
  base is full height, bounded by a straight sharp line, and no wider than a
  gap between frames.

So what colour buys is narrower than the idea promised, and the module says so:
it rejects *coloured* near-base areas and the neutral **empty gate**, and it
makes the per-pixel test one-sided and three-channel instead of a threshold on
one grey level. The decisive rule is physical and within one frame: **a border
region denser than some other full-height region of the same frame is not
base**, because base is the minimum density there is.

B&W: the three channels are three noisy copies of one density, ``zp2`` carries
nothing, and 8-bit B&W base sits at ~19 counts where one count is 5%. The same
code runs; confidence is scaled down by ``BW_CONF``.

What it does, in order:

1. Per column: median, trimmed mean, spread, flat-column noise; the empty gate
   (neutral, far brighter, and grain-free); full-height flat **plateaus**.
2. The reference base: the brightest plateau that no column exceeds in any
   channel (**dominance**). The roll confirms it (same dtype, level within 5%)
   or, when the frame's best is scene under a recurring roll level -- far below
   it, or below it in colour rather than density -- replaces it. A roll level
   never replaces a frame's own base for being merely a little brighter: r0909
   holds one position at two exposures, 18% apart.
3. Per side: a run of full-height base columns within 64 of the border, grown
   while the density drifts smoothly (<= 10%, no step over 3%); per-row 50%
   crossings against the base measured beside the edge; a robust straight line.
   A run wider than a gap, or with no straight line, is scene: picture to the
   border. An interior run is a gap only if one of its sides is a picture edge
   in almost every row.
4. Where no run is found: the **sliver** test on the outer three columns.
5. Two edges that leave no room for a frame: the weaker is scene.

Tuned on the dev labels after iter03, and so to be read with suspicion until the
test split: the sliver thresholds, and turning the two "no straight edge / too
wide" refusals into picture-to-border.

Round 2 (``chroma_v2``, a copy; ``chroma`` stays as it was at iter04). On
library 2's dev part iter04 missed 26 sides of base. Why, miss by miss:

* **The plateau search** (12): ``MAX_REACH`` 64 never looked for a gap 63-88
  columns in (b0921c_08..14, _66, _70, b0922b_18, b0921c_55/_64 right).
* **Run growth** (4): the drift allowance that lets a run follow base which
  darkens towards the aperture edge also carried runs 5-9 columns *inward*
  into a scene nearly as clear as base; the row search then looked for the
  edge in the wrong place (b0921c_49, b0922b_05, b0921a_07, b0921c_57).
* **The base reference** (3): base and a dark scene 3-4% denser beside it
  joined into one plateau, so the reference lay between them and the strict
  test passed both (b0920_05, b0922s_04, b0922b_09).
* **The straightness / gap rules** (7): real interior gaps whose sides run
  on into dark scene in 6-56% of rows, or whose inner edge shows in fewer than
  40% of rows (b0922b_07/_13, b0921c_51/_55/_64/_70, b0921a_01).
* **The per-pixel threshold** was not the cause in any of them: where the
  scene differs from base, ``f`` falls from 0.97 to 0.6-0.8 within two
  columns. It is what the rows *beside* a strict run do that failed.

v2 iter01: ``MAX_REACH`` 100; inward growth only through columns as clean as
the reference (``CLEAN``); the reference plateau cut at a real step
(``_split``); a row that runs out of image at the far border is not "running
on" (``_row_edges``).

v2 iter02: an interior gap needs one *clean straight side* (``GAP_ROWS``,
``GAP_AGREE``, ``GAP_RMSE``) instead of a side that never runs on; no tilt
from rows bunched in part of the height (``TILT_SPAN``); the reference's
ties broken by width, not by column order (a mirror asymmetry); the
half-rows exception for B&W only; and colour, pooled: a run whose columns
are not base's colour over their full height is scene (``P2_RUN``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, TRIM_ROWS, EdgeResult, Side, load,
)

# --------------------------------------------------------------------------
# constants, each with where its value comes from

#: Per-pixel base test, in noise sigmas. ``zd`` is one-sided (picture is only
#: ever denser than base): 2.5 keeps 99.4% of true base pixels. A pixel more
#: than ``ZUP`` sigma *brighter* than base is a defect or the gate, not base.
ZD = 2.5
ZUP = 5.0
#: Chroma chi-square, 2 dof: P(chi2_2 > 12) = 0.25%.
ZP2 = 12.0
#: A column is base when this fraction of its rows are. Measured on clean base
#: plateaus the per-pixel test passes 96-99% (dust, 8-bit quantisation); a
#: column at the edge of a dark scene area that touches base in some rows only
#: must not extend the run, which is the silhouette failure.
F_BASE = 0.85
#: Plateau search: a column is flat when this fraction of rows sit within
#: ``FLAT_K`` sigma (bright-level noise) of the column median in every channel.
G_FLAT = 0.85
FLAT_K = 3.5
#: A reference plateau narrower than this is a line, not an area. Bright single
#: columns occur inside pictures (r0911_01 col 214) and must not set the base.
MIN_REF_WIDTH = 3
#: Base must start within this many columns of the border. iter04 had 64 --
#: the outer 64 columns are what the labeller's zoomed views show, and the
#: ladder's gaps sit 28-46 columns in -- which missed every gap further in:
#: library 2 has them 63-88 columns from the border with the neighbour's
#: picture beyond (b0921c_08..14, b0922b_18, r0914_20 on the main set). A gap
#: whose centre is 100 or more columns from both borders is not a side's
#: business (Stefan's rule: such frames are unsuitable and never scored), and
#: a gap ~17-26 wide centred under 100 starts under ~92.
MAX_REACH = 100
#: The widest gap between frames seen on the dev strips is ~30 columns (ladder9b
#: left, 28..58); a base run touching the border and wider than this is a scene
#: area at base level, alone or merged with the base.
MAX_GAP = 48
#: An interior gap (neighbour picture beyond it) narrower than this is a black
#: line in the scene. Gaps between frames vary: ~4 columns on the r0911 strip
#: (r0911_17, 10..14, the neighbour's picture at 0..10), 12 and 30 on the
#: ladder's. Black scene lines wider than this are left to the frame-width test.
MIN_INTERIOR_GAP = 3
#: Per-row edge: rows whose picture beside the edge is closer to base than this
#: (joint weighted distance, sigmas) carry no position and are left out. 4, not
#: more: 8-bit B&W base is ~19 counts at 0.1-0.15 ln per pixel, and a ledge at
#: 14 beside it (bw0910_01) is only 4.3 sigma.
C_MIN = 4.0
#: A straight edge: rows agreeing within this of the fitted line.
ROW_TOL = 1.25
#: How far rows may wander from the run's end before they are "not this edge":
#: tilt up to ~5 columns over the frame height.
TILT = 4
#: Largest believable tilt, columns per row (~5.5 columns over 276 rows).
MAX_SLOPE = 0.02
#: An edge must be carried by at least this many rows, and this fraction of the
#: rows that have contrast.
MIN_ROWS = 30
MIN_FRAC_IN = 0.6
#: ...half that many rows when they agree to 85%: B&W at 19 counts shows its
#: base only where the picture beside it is dense (bw0910_01 right: 18 rows,
#: 95% on one line, rmse 0.46). B&W only since v2: on C-41 the exception
#: carried a false edge at 23 rows beside a dark shelf (r0914_30, a dup of
#: r0914_29, whose truth is picture to the border).
#: ...or this many rows when the line is confirmed from top to bottom in
#: ``BAND_MIN`` of ``N_BANDS`` horizontal bands (``_full_height``). b0921a_07:
#: 25 rows on the bench, 8 of 8 bands; the C-41 sides this admits nothing
#: else on dev -- the scene areas at base level with 19-23 rows (r0911_21,
#: r0914_30, x0920_04/05) confirm in at most 2 bands.
MIN_ROWS_FULL = 20
N_BANDS = 8
BAND_MIN = 7
#: A band's step, ln: at least this (and 4 of its sigmas). Base columns wander
#: +-0.5-0.7% by fixed pattern; the weakest real steps seen are 1.7%.
BAND_STEP = 0.012
#: ...and its crossing within this of the fitted line. Band crossings of a weak
#: step read up to 1.5 columns inside the rows' line (b0921a_07's top band).
BAND_TOL = 1.5
#: An interior gap (neighbour's picture beyond it) is accepted when its inner
#: edge is a line at all (``MIN_ROWS``, ``MIN_FRAC_IN``) and **one of its two
#: sides is a clean straight line**: carried by ``GAP_ROWS`` of the frame
#: height, with ``GAP_AGREE`` of its rows on it and an rmse under
#: ``GAP_RMSE`` columns. The camera gate cuts a gap; a line of scene black is
#: laid by hand and seen in perspective.
#:
#: iter04 asked instead that rows run on past at most one side in at most 5%
#: of the frame, and that the inner edge show in 40% of it. Library 2 broke
#: both: real gaps beside dark foliage or a night scene run on 6-56% on *both*
#: sides (b0921c_51/_55/_64/_70, b0922b_07/_13), and a gap beside a scene at
#: base level shows its inner edge in 30-98 rows only (b0922b_05/_07,
#: b0921c_49) -- while the other side is a line in 141-224 rows at 0.89-0.96.
#: Measured, the line quality separates what the run-on did not: the scene
#: lines on dev are r0914_17 (grout: 0.85 / 0.86 agreement, rmse 0.54 /
#: 0.66), x0920_73-75 (tile grid: 0.72-0.76), x0920_77/78 (41 rows); the
#: accepted gaps' better side is 0.89-1.00 at rmse 0.13-0.48, over 97-276 rows.
GAP_ROWS = 0.35
GAP_AGREE = 0.85
GAP_RMSE = 0.5
#: A tilt is fitted only from rows spread over at least this fraction of the
#: height: rows bunched in one part -- the bench at the bottom of b0922b_05,
#: where the scene above is at base level -- give a slope that, extrapolated
#: to mid-height, puts the edge a column off (0.0106 there; the edge's own is
#: 0.002).
TILT_SPAN = 0.5
#: Roll levels match when every channel agrees within this (ln). Base across one
#: frame varies ~2%; two frames of one exposure agree to ~3% (r0914 walk).
ROLL_TOL = 0.05
#: A roll level this far above a frame's own best (ln, every channel) replaces it.
FAR_BELOW = 0.3
#: ...or this much more below it in one channel than in another (ln).
CHROMA_SPLIT = 0.35
#: Two plateaus of one frame are the same level within this (ln, every channel).
SAME_TOL = 0.05
#: Empty gate: near-neutral (ln spread over channels) and at least this much
#: brighter (ln, every channel) than the brightest film plateau. Measured gate
#: 183 neutral vs base (68, 30, 16): 1.0 ln in red, the smallest margin.
GATE_NEUTRAL = 0.25
GATE_MARGIN = 0.4
#: ...and smooth: no grain. The gate's own pixel noise reads 0.0075-0.0094 ln
#: (r0909_13, r0914_21); film base never below 0.017 in an unclipped channel,
#: even a uint16 base at 57000 counts (x0920_29) or a neutral-looking one
#: (x0920_02, 0.033-0.043). Without this, a B&W base -- neutral, and far
#: clearer than a dense frame -- reads as the gate (bw0910_06, _09, _11).
GATE_SMOOTH = 0.014
#: ...on B&W only (v2). A C-41 base is orange; the gate is neutral and far
#: clearer, so on C-41 the level and the balance already say "no film". At a
#: strip's end the film is cut on a slant and the columns beside the gate mix
#: gate and film in some rows, which reads as grain: 0.021 median over the gate
#: columns of b0921b_10, 0.03 in b0921b_40's column 0 -- and iter04 then saw
#: base there (2.9 columns of it) where the truth is the empty gate.
#: **Dominance**, the one rule colour really buys: base is the least dense film
#: in *every* channel, so a candidate that any column of the frame exceeds by
#: more than this (ln, or 1.5 counts where that is more) in *any* channel is not
#: base. An orange scene area can match base in red (r0914_34/35: (70, 24, 4)
#: against (68, 30, 16)); it cannot match it in blue.
DOM_TOL = 0.08
#: ...or this many counts, where more: 8-bit B&W base sits at 18-21 counts and
#: one column of the scene reads 20 against a base of 18 (bw0910_01, col 82).
#: Measured over twenty frames with a base by eye, no full-height column median
#: exceeds base by more than 0.069 ln in any channel (strip6_02, blue, one
#: count at 14). *Pixels* do -- 1-5% of them by 3 sigma, where exposure has
#: used up the orange mask (strip6_03) -- which is why this is a test on
#: column medians and never on pixels.
DOM_COUNTS = 2.5
#: Base across a gap is not flat everywhere: the stage-9b ladder's gap falls
#: 8.5% in red over the 22 columns next to the aperture edge (ladder9b_12,
#: 54 -> 59). A run may drift this far from the reference ...
DRIFT = 0.10
#: ... but never in a step: adjacent full-height columns of one base differ by
#: <0.5% (the ladder's gradient per column); an edge between base and a scene
#: area 8-12% denser is one or two columns (ladder9b_16, 61 -> 54 at col 58).
STEP = 0.03
#: C-41 base colour on the prescan scale: R/G 2.04-2.30, B/G 0.53-0.58, over
#: twenty frames of six rolls, both dtypes (x0920_61 is 2.15 / 0.54). A
#: reference outside this window is at another exposure balance or is not
#: base; it is used, but trusted less. It cannot tell a dark neutral wall from
#: base: a neutral density leaves ratios alone (r0911_01's wall is 2.08 / 0.56).
C41_RG = (1.85, 2.5)
C41_BG = (0.46, 0.66)
#: Two edges must leave room for a frame. Frames here are wider than the
#: aperture: 440 and 450 columns gap to gap on the two dev rolls where a gap
#: and a sliver show together (ground_truth.json frame_width). Narrower than
#: this means one side is a scene area at base level (r0914_17: the white grid
#: between tiles, 47..62, would make 356; bw0910_01: night sky from 388).
MIN_FRAME_COLS = 420
#: Sliver test (``_sliver``). On dev the nine sides whose truth is 0.15-1.14
#: columns of base score curvature 0.09-0.72 with 80-100% of rows stepping,
#: except r0909_05 (0.056); the 120 picture-to-border sides score at most
#: 0.059 (r0914_14), and the one that steps in 95% of rows scores 0.047
#: (x0920_19, a picture lightening to the border).
SLIVER_KAPPA = 0.075
SLIVER_FRAC = 0.75
#: **Colour, pooled.** A run is base only if its columns are base's colour: the
#: median over the run of each column's median chroma chi-square (2 dof)
#: against the reference is at most this. Noise alone gives 1.39; the base runs
#: on both dev sets read 0.3-1.6 at the reference level and up to 2.1 where
#: they drift off it near the aperture edge (r0914_18 right). The scene areas
#: that pass the per-pixel test one pixel at a time but not pooled: 3.3
#: (r0914_19 left), 3.8 (b0922s_08 right, where it was a false edge). A dark
#: area of base's own colour -- scene black -- is untouched by this; only
#: geometry rejects that.
P2_RUN = 3.0
#: A plateau covering this much of the width is the whole frame: blank.
ALL_BASE_FRAC = 0.9
#: A plateau is cut in two where the mean levels of its parts differ by this
#: much (ln, channel-weighted). Base's own columns wander +-0.7% (fixed
#: pattern: b0921c_49's base, D -0.015..+0.014), so the largest mean
#: difference over the cuts of a pure-base plateau is under 1%; base against
#: a dark scene beside it steps 3-4% (b0920_05, b0922s_04).
SPLIT_STEP = 0.02
#: ...and this many standard errors of the parts' means, from the columns'
#: own scatter: b0920_05's cut is 10 (0.045 against 0.0043).
SPLIT_T = 5.0
#: Inward growth: a column joins the run only while its ``f_loose`` and its
#: flatness ``g`` are within this of what the reference scores. Base columns,
#: drifting or not, read 0.97-1.00 on both; the first column of a scene nearly
#: as clear as base 0.91-0.93 (b0921c_49 col 84, b0922b_05 col 69, b0921a_07
#: col 25).
CLEAN = 0.05
#: B&W: chroma carries nothing and 8-bit B&W base is ~19 counts.
BW_CONF = 0.7


# --------------------------------------------------------------------------
# frame analysis

def _prep(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(Ic, L): the trimmed RGB rows in counts, and their natural log.

    The log floor is relative to the frame's own bright level, never a dtype.
    """
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 3 and img.shape[2] > 3:
        img = img[..., :3]           # RGBI: infrared is not a colour of the base
    core = img[TRIM_ROWS:img.shape[0] - TRIM_ROWS]
    top = np.percentile(core.reshape(-1, 3), 99.5, axis=0)
    floor = np.maximum(top * 1e-3, 1e-6)
    L = np.log(np.maximum(core, floor))
    return core, L


def _own_noise(L: np.ndarray) -> np.ndarray:
    """(W, 3): each column's pixel noise in ln-units, from vertical differences.

    Used only to tell the gate from film (``GATE_SMOOTH`` was measured with it).

    Neighbouring rows cancel the scene's slow structure. The largest 10% of
    ``|d|`` (edges, dust) are dropped and the trimmed RMS is corrected by the
    factor a Gaussian loses to that trim (0.789), then by sqrt 2 for the
    difference of two pixels.
    """
    d = np.diff(L, axis=0)                                    # (H-1, W, 3)
    a = np.abs(d)
    cut = np.percentile(a, 90, axis=0, keepdims=True)
    d2 = np.where(a <= cut, d * d, np.nan)
    return np.sqrt(np.nanmean(d2, axis=0)) / 0.789 / np.sqrt(2)


def _col_spread(L: np.ndarray, M: np.ndarray) -> np.ndarray:
    """(W, 3): each column's spread about its own median, ln-units.

    RMS of the central 80% of deviations over the 0.662 a Gaussian keeps under
    that trim. Vertical differences read low here -- neighbouring rows of a
    prescan are correlated (0.029 against 0.042 on r0914_05's base in red) --
    so flatness is judged against spread, not against differences.
    """
    dev = L - M[None]
    a = np.abs(dev)
    cut = np.percentile(a, 80, axis=0, keepdims=True)
    d2 = np.where(a <= cut, dev * dev, np.nan)
    return np.sqrt(np.nanmean(d2, axis=0)) / 0.662


def _trimmed_mean(L: np.ndarray) -> np.ndarray:
    """(W, 3): per column, the mean of the rows between the 10th and 90th percentile.

    Not the median: 8-bit medians are whole counts, and one count of blue at 16
    is a 6% step that is not there.
    """
    lo, hi = np.percentile(L, [10, 90], axis=0)
    inside = (L >= lo[None]) & (L <= hi[None])
    return np.nansum(np.where(inside, L, 0.0), axis=0) / np.maximum(inside.sum(0), 1)


def _flat_noise(s_vd: np.ndarray, spread: np.ndarray, use: np.ndarray) -> np.ndarray:
    """(W, 3): the spread each column would have if it were flat, ln-units.

    Vertical differences cancel a column's slow structure but read low because
    neighbouring rows are correlated; the spread about the median reads true on
    a flat column and high on anything with structure. Their ratio on the
    flattest fifth of the columns is the correlation factor -- measured
    0.96-1.4 on C-41 base and 1.3-1.6 on B&W -- and that factor times each
    column's own vertical noise is what flat would look like there.
    """
    r = spread[use] / np.maximum(s_vd[use], 1e-9)
    kappa = np.clip(np.percentile(r, 20, axis=0), 0.9, 2.0)
    return s_vd * kappa[None, :]


def _noise_at(fs: dict[str, Any], level: np.ndarray) -> np.ndarray:
    """Flat-column noise at an arbitrary level (ln), from columns near it."""
    M, sf, use = fs["M"], fs["sig_flat"], fs["use"]
    near = use & np.all(np.abs(M - level[None]) <= 0.1, axis=1)
    pool = sf[near] if near.sum() >= 3 else sf[use]
    s = np.percentile(pool, 30 if near.sum() >= 3 else 20, axis=0)
    return np.maximum(s, 0.4 / np.maximum(np.exp(level), 1e-9))


def _plateaus(L: np.ndarray, M: np.ndarray, Mt: np.ndarray, sig: np.ndarray, use: np.ndarray
              ) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Runs of full-height flat columns at one level, anywhere in the frame.

    ``sig`` is (W, 3), the flat-column noise of each column. A column is flat
    when ``G_FLAT`` of its rows sit within ``FLAT_K`` sigma of its median in
    every channel. Neighbouring flat columns belong to one plateau unless their
    trimmed means step by more than ``STEP`` (or half a sigma) in some channel
    -- a test on pairs, so the same plateaus come out read from either end.
    """
    H, W, _ = L.shape
    dev = np.abs(L - M[None]) <= FLAT_K * sig[None]
    g = dev.all(2).mean(0)                                   # (W,)
    flat = (g >= G_FLAT) & use
    step = np.abs(np.diff(Mt, axis=0))                       # (W-1, 3)
    tol = np.maximum(STEP, 0.5 * np.maximum(sig[1:], sig[:-1]))
    joined = flat[1:] & flat[:-1] & np.all(step <= tol, axis=1)
    out: list[dict[str, Any]] = []
    x = 0
    while x < W:
        if not flat[x]:
            x += 1
            continue
        start = x
        while x + 1 < W and joined[x]:
            x += 1
        x += 1
        lev = np.median(L[:, start:x].reshape(-1, 3), axis=0)
        out.append({"a": start, "e": x, "w": x - start, "level": lev,
                    "lum": float(lev.mean()), "g": float(g[start:x].mean())})
    return out, g


def _split(Mt: np.ndarray, sig: np.ndarray, a: int, e: int) -> int | None:
    """Where ``[a, e)`` steps in level, if it does: the column of the cut, or None.

    The plateau join is a test on *pairs* of columns, so a blurred edge
    between base and a scene 3-4% denser -- one intermediate column, each
    pair under ``STEP`` -- joins the two into one plateau whose level lies
    between them (b0920_05: base 0-8 at D -0.03 and a dark bar 9-19 at
    +0.01 against the joint level; b0922s_04 the same). Then the reference
    is not base, and every later test is against the wrong level.

    The cut maximises the difference of the parts' mean levels (weighted
    over channels as ``D`` is) scaled by ``sqrt(n1 n2 / n)``. It is a step
    when the difference exceeds ``SPLIT_STEP`` *and* ``SPLIT_T`` times its
    own standard error from the columns' scatter about each part's mean,
    with both parts at least ``MIN_REF_WIDTH`` wide. Symmetric under
    reversal of the columns.
    """
    n = e - a
    if n < 2 * MIN_REF_WIDTH:
        return None
    w = 1.0 / np.maximum(np.median(sig[a:e], axis=0), 1e-6) ** 2
    lum = (Mt[a:e] * w[None]).sum(1) / w.sum()               # (n,), ln
    c = np.concatenate([[0.0], np.cumsum(lum)])
    best, cut = 0.0, -1
    for s in range(MIN_REF_WIDTH, n - MIN_REF_WIDTH + 1):
        m1, m2 = c[s] / s, (c[n] - c[s]) / (n - s)
        stat = abs(m2 - m1) * np.sqrt(s * (n - s) / n)
        if stat > best:
            best, cut = stat, s
    if cut < 0:
        return None
    p1, p2 = lum[:cut], lum[cut:]
    step = abs(float(p2.mean() - p1.mean()))
    se = np.sqrt(p1.var(ddof=1) / len(p1) + p2.var(ddof=1) / len(p2)) if min(len(p1), len(p2)) > 1 \
        else np.inf
    if step < SPLIT_STEP or step < SPLIT_T * se:
        return None
    return a + cut


def _refine_best(best: dict[str, Any], L: np.ndarray, Mt: np.ndarray, sig: np.ndarray,
                 g: np.ndarray, M: np.ndarray, use: np.ndarray) -> dict[str, Any]:
    """The reference plateau, cut to its brighter part where it holds a step.

    Only the chosen reference is cut, never the other plateaus: a cut part of
    a scene plateau is a narrow, noisy bright sliver that would then win
    "brightest" (b0921c_49, tried: a 3-column part of a wall at 141-144 took
    the reference from a 24-column base).
    """
    cut = _split(Mt, sig, best["a"], best["e"])
    if cut is None:
        return best
    parts = []
    for a, e in ((best["a"], cut), (cut, best["e"])):
        lev = np.median(L[:, a:e].reshape(-1, 3), axis=0)
        parts.append({"a": a, "e": e, "w": e - a, "level": lev, "lum": float(lev.mean()),
                      "g": float(g[a:e].mean()), "over": _dominated(lev, M, use),
                      "cut_from": [best["a"], best["e"]]})
    # brighter by the trimmed means the cut was found on: medians are whole
    # counts in 8-bit and can tie across a real 2% step
    top = max(parts, key=lambda p: float(Mt[p["a"]:p["e"]].mean()))
    return top if top["over"] == 0 else best


def _gate_columns(M: np.ndarray, s_own: np.ndarray, smooth: bool = True) -> np.ndarray:
    """(W,) bool: the empty gate, a neutral run at a border far brighter than film.

    Column-based, not plateau-based: the gate's own noise is a fifth of the
    film's (0.004-0.006 ln against 0.02-0.08) and a flatness test tuned on it
    breaks it into pieces.
    """
    W = M.shape[0]
    lum = M.mean(1)
    neutral = (M.max(1) - M.min(1)) <= GATE_NEUTRAL
    top = float(lum.max())
    cand = neutral & (lum >= top - 0.15)
    gate = np.zeros(W, bool)
    if not cand.any() or cand.all():
        return gate
    lev = np.median(M[cand], axis=0)
    rest = ~cand
    film_top = np.percentile(M[rest], 99, axis=0)
    if not np.all(lev - film_top >= GATE_MARGIN):
        return gate
    if smooth and float(np.median(s_own[cand], axis=0).max()) > GATE_SMOOTH:
        return gate                                           # grain: this is film
    # runs touching a border, bridging breaks of up to 3 columns
    for sl in (slice(None), slice(None, None, -1)):
        c = cand[sl]
        g = np.zeros(W, bool)
        gap = 0
        last = -1
        for i in range(W):
            if c[i]:
                last = i
                gap = 0
            else:
                gap += 1
                if gap > 3:
                    break
        if c[0]:
            g[:last + 1] = True
        gate |= g[sl]
    return gate


def _dominated(level: np.ndarray, M: np.ndarray, use: np.ndarray) -> int:
    """How many usable columns are clearer than ``level`` in some channel."""
    tol = np.maximum(DOM_TOL, DOM_COUNTS / np.maximum(np.exp(level), 1e-9))
    over = (M[use] - level[None]) > tol[None]
    return int(over.any(1).sum())


def frame_summary(image: np.ndarray, film: str | None = None) -> dict[str, Any]:
    """Everything about a frame that does not depend on a chosen base."""
    Ic, L = _prep(image)
    M = np.median(L, axis=0)                                  # (W, 3)
    s_vd = _own_noise(L)
    # The grain test is for B&W, whose base is neutral too; C-41 film is
    # orange, so a neutral area far clearer than all of it is not film at all.
    gate_cols = _gate_columns(M, s_vd, smooth=film != "c41")
    spread = _col_spread(L, M)
    floor = 0.4 / np.maximum(np.exp(M), 1e-9)                # 0.4 count
    sig_flat = np.maximum(_flat_noise(s_vd, spread, ~gate_cols), floor)
    Mt = _trimmed_mean(L)
    plats, g = _plateaus(L, M, Mt, sig_flat, ~gate_cols)
    W = L.shape[1]
    # a reference: at a border any width (a sliver of base is one column), inside
    # at least MIN_REF_WIDTH (a bright line in the picture is not an area)
    refs = [p for p in plats if p["w"] >= MIN_REF_WIDTH
            or (p["a"] <= 0 or p["e"] >= W)
            or (gate_cols.any() and (gate_cols[max(p["a"] - 1, 0)] or gate_cols[min(p["e"], W - 1)]))]
    # dominance: nothing on the film may be clearer than base in any channel;
    # columns beside the gate are part-gate and do not count
    use = ~gate_cols
    if gate_cols.any():
        near = np.convolve(gate_cols.astype(float), np.ones(7), mode="same") > 0
        use &= ~near
    for p in refs:
        p["over"] = _dominated(p["level"], M, use)
    adm = [p for p in refs if p["over"] == 0]
    # Ties are common -- 8-bit medians are whole counts, so a wall and the base
    # can share a level to the count (b0921c_49: 138-144 and 59-83 both read
    # (63, 29, 16)) -- and ``max`` would then pick whichever comes first, which
    # the mirror reverses. The wider, then the flatter, plateau wins.
    best = max(adm, key=lambda p: (p["lum"], p["w"], p["g"])) if adm else None
    if best is not None:
        best = _refine_best(best, L, Mt, sig_flat, g, M, use)
    return {"Ic": Ic, "L": L, "M": M, "Mt": Mt, "sig_flat": sig_flat,
            "spread": spread, "g": g,
            "plateaus": plats,
            "gate_cols": gate_cols, "best": best, "use": use}


# --------------------------------------------------------------------------
# the roll: levels that recur confirm a base; they never veto one

_ROLL_CACHE: dict[str, dict[str, Any] | None] = {}


def _roll_level(fid: str) -> dict[str, Any] | None:
    if fid not in _ROLL_CACHE:
        try:
            fr = load(fid)
        except Exception:                                     # noqa: BLE001
            _ROLL_CACHE[fid] = None
            return None
        s = frame_summary(fr.image, fr.film)
        b = s["best"]
        _ROLL_CACHE[fid] = None if b is None else {
            "level": b["level"], "dtype": fr.meta.get("dtype"), "w": b["w"]}
    return _ROLL_CACHE[fid]


def roll_levels(ctx: dict[str, Any]) -> list[np.ndarray]:
    """The base levels the roll's other frames found for themselves, same dtype only."""
    out = []
    for fid in ctx.get("roll_ids") or []:
        r = _roll_level(fid)
        if r is not None and r["dtype"] == ctx.get("dtype"):
            out.append(r["level"])
    return out


def roll_support(level: np.ndarray, ctx: dict[str, Any]) -> tuple[int, int]:
    """(frames whose own base level matches ``level``, frames compared)."""
    ids = ctx.get("roll_ids") or []
    dtype = ctx.get("dtype")
    n = hit = 0
    for fid in ids:
        r = _roll_level(fid)
        if r is None or r["dtype"] != dtype:
            continue
        n += 1
        if np.all(np.abs(r["level"] - level) <= ROLL_TOL):
            hit += 1
    return hit, n


# --------------------------------------------------------------------------
# per pixel: density excess and chroma deviation against a base

def _zmaps(L: np.ndarray, b: np.ndarray, sig: np.ndarray, M: np.ndarray | None = None
           ) -> dict[str, np.ndarray]:
    """Per pixel against base ``b``: density excess, chroma deviation, the verdict.

    ``match`` is the strict test; ``loose`` lets the density drift by ``DRIFT``
    (a base that is not flat across its gap) while keeping the chroma test.
    ``D`` is the same density excess on the column medians, in ln, for steps.
    """
    e = b[None, None, :] - L                                  # excess density, ln
    w = 1.0 / sig ** 2
    se = 1.0 / np.sqrt(w.sum())                               # sigma of the weighted mean
    zd = (e * w).sum(2) * se                                  # one-sided, sigmas
    chi2 = ((e / sig) ** 2).sum(2)
    zp2 = np.maximum(chi2 - zd ** 2, 0.0)
    ok = (zd > -ZUP) & (zp2 < ZP2)
    match = ok & (zd < ZD)
    loose = ok & (zd < ZD + DRIFT / se)
    out = {"zd": zd, "zp2": zp2, "match": match, "f": match.mean(0), "f_loose": loose.mean(0),
           "p2c": np.median(zp2, axis=0)}
    if M is not None:
        out["D"] = ((b[None, :] - M) * w[None, :]).sum(1) / w.sum()
    return out


def _base_noise(fs: dict[str, Any], a: int, e: int) -> np.ndarray:
    """Per-channel ln noise of the base itself: the median spread of its columns.

    The spread is the Gaussian core (the worst 20% trimmed): dust and the odd
    hot pixel then fail the per-pixel test, which the column fraction allows.
    """
    sp = np.median(fs["spread"][a:e], axis=0)
    lev = np.median(fs["M"][a:e], axis=0)
    return np.maximum(sp, 0.4 / np.maximum(np.exp(lev), 1e-9))


# --------------------------------------------------------------------------
# one side, always analysed as a LEFT side (the right is the mirror image)

def _row_edges(Ic: np.ndarray, match: np.ndarray, B: np.ndarray, sig: np.ndarray,
               start: int, e: int, inward: bool = True) -> dict[str, Any]:
    """Per-row 50% crossing near column boundary ``e``, base on the low side.

    For each row: the first two consecutive non-base pixels at or after
    ``max(start, e - TILT)`` mark the transition; the picture colour ``P`` is
    the median of the next three pixels; each pixel's base fraction is its
    joint weighted projection onto the segment ``P -> B``; the crossing is the
    base-pixel count, which for a step blurred symmetrically is the 50% point.
    """
    H, W, _ = Ic.shape
    s0 = max(start, e - TILT)
    lim = min(W - 6, e + TILT + 1)
    # Near the far border there is no room for the window: a row that walks
    # to ``lim`` there has run out of image, not into scene at base level. It
    # runs on only if base reaches the border (b0921a_01: the neighbour's
    # picture is the outer 3.4 columns, and iter04 read 99% of rows as
    # running on there).
    clipped = lim < e + TILT + 1
    wl = 1.0 / (sig * B) ** 2                                 # linear-count weights
    xs = np.full(H, np.nan)
    cont = np.zeros(H, bool)
    con = np.zeros(H)
    for y in range(H):
        if s0 >= W - 6 or not match[y, s0]:
            continue
        row = match[y]
        k = s0
        while k < lim and (row[k] or row[k + 1]):
            k += 1
        if k >= lim:
            # base goes on: scene at base level
            cont[y] = bool(row[k:].all()) if clipped else True
            continue
        P = np.median(Ic[y, k + 2:k + 5], axis=0)
        d = B - P
        den = float((wl * d * d).sum())
        con[y] = np.sqrt(den)
        if con[y] < C_MIN:
            continue
        lo = max(start, k - 2)
        seg = Ic[y, lo:k + 2]
        al = ((seg - P) * d * wl).sum(1) / den
        xs[y] = lo + float(np.clip(al, -0.25, 1.25).sum())
    return {"xs": xs, "cont": cont, "con": con}


def _full_height(Ic: np.ndarray, B: np.ndarray, sig: np.ndarray, a: int, e: int,
                 fit: dict[str, Any]) -> int:
    """How many of ``N_BANDS`` horizontal bands show the edge at the fitted line.

    Per band, the channel-weighted density excess against base ``B`` is
    averaged over the band's rows, column by column -- a scene 2% denser than
    base, 1 sigma per pixel, is 6 sigma over a band of 34 rows. The band shows
    the edge when the step from the run's last columns to the next five is at
    least ``BAND_STEP`` and 4 of its own sigmas, and the profile crosses half
    that step within ``BAND_TOL`` columns of the line.

    Stefan's test for base is "a sharp line, completely black from top to
    bottom". Where the scene beside base is nearly as clear as base, rows show
    that line only where a picture feature touches it (b0921a_07: the bench,
    25 rows), but every band still steps at it: 8 of 8 there, 0.017-0.32 each.
    The scene areas at base level rejected on dev step in 1-3 bands, or cross
    all over (r0914_30: 2.7-9.7).
    """
    H, W, _ = Ic.shape
    lo, hi = max(a, e - 6), min(W, e + 7)
    if hi - e < 6 or e - lo < 2:
        return 0
    w = 1.0 / sig ** 2
    ex = ((np.log(B)[None, None] - np.log(np.maximum(Ic[:, lo:hi], 1e-9))) * w).sum(2) / w.sum()
    se = 1.0 / np.sqrt(w.sum())
    rows = np.linspace(0, H, N_BANDS + 1).astype(int)
    hits = 0
    for k in range(N_BANDS):
        y0, y1 = rows[k], rows[k + 1]
        prof = ex[y0:y1].mean(0)                                # columns lo..hi-1
        base = float(np.median(prof[:e - 1 - lo])) if e - 1 - lo >= 1 else float(prof[0])
        scene = float(np.median(prof[e + 1 - lo:e + 6 - lo]))
        step = scene - base
        if step < max(BAND_STEP, 4.0 * se / np.sqrt(y1 - y0)):
            continue
        half = base + step / 2.0
        xb = fit["x"] + fit["slope"] * ((y0 + y1 - 1) / 2.0 - fit["yc"])
        for c in range(max(lo + 1, e - 4), hi):
            if prof[c - lo] >= half:
                prev = prof[c - 1 - lo]
                pos = (c - 0.5) + (half - prev) / max(prof[c - lo] - prev, 1e-9)
                hits += int(abs(pos - xb) <= BAND_TOL)
                break
    return hits


def _fit_line(xs: np.ndarray) -> dict[str, Any] | None:
    ys = np.arange(len(xs), dtype=float)
    ok = np.isfinite(xs)
    if ok.sum() < MIN_ROWS // 2:
        return None
    m = float(np.median(xs[ok]))
    inl = ok & (np.abs(xs - m) <= 2.5)
    p1, p0 = 0.0, m
    yc = (len(xs) - 1) / 2.0
    for _ in range(3):
        if inl.sum() < 5:
            break
        p1, p0 = np.polyfit(ys[inl] - yc, xs[inl], 1)
        span = float(ys[inl].max() - ys[inl].min())
        if abs(p1) > MAX_SLOPE or span < TILT_SPAN * len(xs):
            p1 = 0.0
            p0 = float(np.median(xs[inl]))
        res = xs - (p0 + p1 * (ys - yc))
        inl = ok & (np.abs(res) <= ROW_TOL)
    res = xs - (p0 + p1 * (ys - yc))
    rmse = float(np.sqrt(np.mean(res[inl] ** 2))) if inl.any() else 9.9
    return {"x": float(p0), "slope": float(p1), "n_in": int(inl.sum()),
            "n_ok": int(ok.sum()), "frac_in": float(inl.sum() / max(ok.sum(), 1)),
            "rmse": rmse, "yc": yc}


def _local_base(Ic: np.ndarray, a: int, e: int, inner: bool) -> np.ndarray:
    """Base colour in counts next to one end of the run ``[a, e)``.

    The columns beside the edge, not the frame reference: a base that drifts
    across its gap (the ladder) must be measured where it meets the picture.
    The column at the very end may be part picture and is left out.
    """
    if e - a <= 2:
        lo, hi = a, e
    elif inner:
        lo, hi = max(a, e - 5), e - 1
    else:
        lo, hi = a + 1, min(e, a + 5)
    return np.median(Ic[:, lo:hi].reshape(-1, 3), axis=0)


def _left_side_runs(fs: dict[str, Any], z: dict[str, np.ndarray], b: np.ndarray,
                    sig: np.ndarray, trust: float, gate_cols: np.ndarray
                    ) -> tuple[Side, dict[str, Any]]:
    """One side, as the left: base occupies ``[0, x)`` or ``[outer, x)``."""
    Ic = fs["Ic"]
    H, W, _ = Ic.shape
    f, fl, D, g = z["f"], z["f_loose"], z["D"], fs["g"]
    dbg: dict[str, Any] = {"f_head": np.round(f[:12], 2)}
    if gate_cols[0]:
        return Side(NO_FILM, conf=0.9, note="empty gate at the border"), dbg
    fb, gb = float(z["fb"]), float(z["gb"])
    base_cols = f >= fb
    reach = min(MAX_REACH, W // 2)
    cand = np.where(base_cols[:reach])[0]

    # Grow a run while columns stay full-height base within the drift and
    # never step: a gradient across the gap joins it, an edge stops it.
    def joins(x: int, nb: int) -> bool:
        return bool(fl[x] >= fb and g[x] >= gb and abs(D[x] - D[nb]) <= STEP)

    # Drift must be clean. A column joins the run through the drift allowance
    # only while its ``fl`` is within ``CLEAN`` of what base itself scores --
    # the reference plateau's, or the run's own strict core's where that is
    # cleaner. The drift the loose test allows is illumination falling off
    # towards the aperture edge (the ladder's gap: 1.00 in every drifting
    # column); a scene nearly as clear as base passes the loose test too, but
    # it is not flat -- 0.91 at b0921c_49's col 84, the first scene column --
    # and on library 2 the drift carried runs 5-9 columns into such scenes
    # (b0921c_49, b0922b_05, b0921a_07), where no straight edge was then found.
    # The core matters where the reference is itself a textured plateau:
    # b0921c_57's scores 0.88, its gap 0.97-1.00 and the dark street either
    # side 0.82-0.88, so against the reference the run swallowed both sides.
    fl_ref = float(z["fl_ref"])

    # Seeds: a column at the reference level within reach; or the border
    # column itself if it is base within the drift -- base next to the
    # aperture edge can read 8% below the same base further in (ladder9b_09:
    # 53-56 against a wall at 57 inside the frame). Interior runs are seeded
    # only at the reference level: any flat scene area is within 10% of
    # something.
    border_seed = bool(fl[0] >= fb and g[0] >= gb)
    if len(cand) == 0 and not border_seed:
        # nothing at base level near the border: picture, as sure as the base is
        clear = float(np.mean(z["zd"][:, :2] > ZD))
        conf = trust * min(1.0, 0.5 + clear)
        return Side(PICTURE_TO_BORDER, conf=round(conf, 3),
                    note=f"no base within {reach} cols; border rows denser {clear:.2f}"), dbg
    if border_seed and (len(cand) == 0 or cand[0] > 0):
        a, e = 0, 1
        thr = fl_ref - CLEAN
    else:
        a = int(cand[0])
        e = a + 1
        while e < W and base_cols[e]:                         # the strict core
            e += 1
        thr = max(fl_ref, float(np.median(fl[a:e]))) - CLEAN

    def clean_join(x: int, nb: int) -> bool:
        return bool(fl[x] >= thr and (base_cols[x] or joins(x, nb)))

    while a > 0 and clean_join(a - 1, a):
        a -= 1
    while e < W and clean_join(e, e - 1):
        e += 1
    # The outermost column or two can read a little off (r0914_02: column 427
    # at 64 against 66, f 0.8). Base that begins one or two columns in, with
    # those columns still mostly base, touches the border; a neighbour's
    # picture there would read as picture in nearly every row.
    if 0 < a <= 2 and float(fl[:a].mean()) >= 0.4:
        a = 0
    width = e - a
    at_level = bool(base_cols[a:e].any())
    p2 = float(np.median(z["p2c"][a:e]))
    dbg.update(run=[a, e], at_level=at_level, p2=round(p2, 3))
    if p2 > P2_RUN:
        # not base's colour, pooled over its full height: a scene area near
        # base level whose balance is off (b0922s_08 right: B/G 0.50 against
        # base's 0.55, every pixel within ``ZP2`` and the column medians 3.8)
        return Side(PICTURE_TO_BORDER, conf=round(0.6 * trust, 3),
                    note=f"run [{a},{e}) is not base's colour: chroma chi2 {p2:.1f}"), dbg
    if not at_level:
        trust *= 0.7
    if a == 0 and width > MAX_GAP:
        if not at_level:
            # a smooth area near base level but never at it: not a gap
            return Side(PICTURE_TO_BORDER, conf=round(0.5 * trust, 3),
                        note=f"near-base area {width} cols wide, never at base level"), dbg
        # Base-level, at the border, wider than any gap and bounded by no
        # line: a dark scene area. On dev every such side is picture to the
        # border (r0911_04/06/13/22/23/26) but one, where 2.4 columns of base
        # merge into a wall at the same level (r0911_07) -- so, low confidence.
        return Side(PICTURE_TO_BORDER, conf=round(0.4 * trust, 3),
                    note=f"base-level run {width} cols wide from the border: scene"), dbg
    if a > 0 and width < MIN_INTERIOR_GAP:
        return Side(PICTURE_TO_BORDER, conf=round(0.6 * trust, 3),
                    note=f"only a {width}-col base-level line at {a}"), dbg
    if a > 0 and width > MAX_GAP:
        return Side(PICTURE_TO_BORDER, conf=round(0.4 * trust, 3),
                    note=f"interior base-level run {width} cols wide: scene"), dbg

    Bi = _local_base(Ic, a, e, inner=True)
    zi = _zmaps(np.log(np.maximum(Ic, 1e-9)), np.log(Bi), sig)
    rows_in = _row_edges(Ic, zi["match"], Bi, sig, a, e)
    fit = _fit_line(rows_in["xs"])
    cont_in = float(rows_in["cont"].mean())
    dbg["cont_in"] = round(cont_in, 3)
    dbg["fit"] = fit
    rows_out = ofit = None
    if a > 0 and fit is not None:
        # the neighbour's picture: where base begins, seen from the border side
        Bo = _local_base(Ic, a, e, inner=False)
        Irev = Ic[:, ::-1]
        zo = _zmaps(np.log(np.maximum(Irev, 1e-9)), np.log(Bo), sig)
        rows_out = _row_edges(Irev, zo["match"], Bo, sig, W - e, W - a)
        ofit = _fit_line(rows_out["xs"])
        dbg["cont_out"] = round(float(rows_out["cont"].mean()), 3)
        dbg["outer_fit"] = ofit
        dbg["gap"] = {"w": e - a, "H": H, "n_in": fit["n_in"], "n_ok": fit["n_ok"],
                      "frac_in": round(fit["frac_in"], 3), "rmse": round(fit["rmse"], 3),
                      "cont_in": round(cont_in, 3),
                      "cont_out": round(float(rows_out["cont"].mean()), 3),
                      "both": round(float((rows_in["cont"] & rows_out["cont"]).mean()), 3),
                      "o_n_in": None if ofit is None else ofit["n_in"],
                      "o_frac_in": None if ofit is None else round(ofit["frac_in"], 3),
                      "o_rmse": None if ofit is None else round(ofit["rmse"], 3),
                      "at_level": at_level}
    enough = fit is not None and fit["frac_in"] >= MIN_FRAC_IN and (
        fit["n_in"] >= MIN_ROWS
        or (bool(z["bw"]) and fit["n_in"] >= MIN_ROWS // 2 and fit["frac_in"] >= 0.85))
    if fit is not None and not enough and fit["frac_in"] >= MIN_FRAC_IN \
            and fit["n_in"] >= MIN_ROWS_FULL:
        # few rows, but a line from top to bottom
        bands = _full_height(Ic, Bi, sig, a, e, fit)
        dbg["bands"] = bands
        enough = bands >= BAND_MIN
    if enough and a > 0:
        # An interior gap: its inner edge must be a line carried by MIN_ROWS
        # (a gap that only a few rows bound is a line of scene black: x0920_77's
        # tile grid, 31 of 276 rows), and one of its sides a clean line.
        def clean(ft: dict[str, Any] | None) -> bool:
            return bool(ft is not None and ft["n_in"] >= GAP_ROWS * H
                        and ft["frac_in"] >= GAP_AGREE and ft["rmse"] <= GAP_RMSE)
        assert fit is not None
        if fit["n_in"] < MIN_ROWS or not (clean(fit) or clean(ofit)):
            return Side(PICTURE_TO_BORDER, conf=round(0.4 * trust, 3),
                        note=f"base-level line [{a},{e}) is scene: neither side a clean line"), dbg
    if fit is None or not enough:                             # ``enough`` implies a fit
        # Base is bounded by a straight line; an area at base level that is
        # not is scene. Every such side on dev is picture to the border (ten
        # of them: r0911_04/05/06/12/21/24, stage3_01, x0920_40, bw0910_04,
        # r0914_19).
        return Side(PICTURE_TO_BORDER, conf=round(0.5 * trust, 3),
                    note=f"base-level run [{a},{e}) with no straight edge: scene"), dbg
    x = fit["x"]
    hw = 0.3 + 2.0 * fit["rmse"] / np.sqrt(max(fit["n_in"], 1))
    hw += 0.5 * (1.0 - fit["frac_in"])
    x_top = x + fit["slope"] * (0 - fit["yc"])
    x_bot = x + fit["slope"] * (H - 1 - fit["yc"])
    straight = min(1.0, (fit["frac_in"] - 0.4) / 0.45)
    rows = min(1.0, fit["n_in"] / (0.5 * H))
    conf = trust * straight * (0.6 + 0.4 * rows)
    outer = None
    if a > 0:
        assert rows_out is not None
        conf *= 0.8
        if ofit is not None and ofit["frac_in"] >= MIN_FRAC_IN:
            outer = round(float(W - ofit["x"]), 3)
        else:
            outer = float(a)
    side = Side(EDGE, x=round(x, 3), lo=round(x - hw, 3), hi=round(x + hw, 3),
                conf=round(float(np.clip(conf, 0.0, 1.0)), 3), outer=outer,
                x_top=round(x_top, 3) if fit["slope"] else None,
                x_bottom=round(x_bot, 3) if fit["slope"] else None,
                note=f"run [{a},{e}) rows {fit['n_in']}/{fit['n_ok']} rmse {fit['rmse']:.2f}")
    return side, dbg


def _sliver(Ic: np.ndarray, B: np.ndarray, sig: np.ndarray) -> dict[str, float] | None:
    """Less than a column or two of base at the border: a step, not a slope.

    Per row, each of the outer three columns' base fraction ``a0, a1, a2`` is
    its projection onto the segment from the picture (median of columns 3-6)
    to base. A sliver of width ``x < 1`` makes ``a0 = x`` and ``a1 = a2 = 0``;
    a picture that merely lightens towards the border makes all three rise
    together. So the test is on the curvature ``a0 - 2 a1 + a2`` and on how
    many rows have ``a0 > a1``.
    """
    P = np.median(Ic[:, 3:7], axis=1)
    d = B[None] - P
    wl = 1.0 / (sig * B) ** 2
    den = (wl * d * d).sum(1)
    ok = np.sqrt(den) >= 6.0
    if ok.sum() < MIN_ROWS:
        return None
    al = [((Ic[:, k] - P) * d * wl).sum(1)[ok] / den[ok] for k in range(3)]
    kappa = float(np.median(al[0] - 2 * al[1] + al[2]))
    frac = float(np.mean(al[0] > al[1]))
    a0, a1 = float(np.median(al[0])), float(np.median(al[1]))
    x = max(a0, 0.0) + max(a1, 0.0)
    spread = float(np.median(np.abs(al[0] - a0)))
    return {"kappa": kappa, "frac": frac, "x": x, "a0": a0, "a1": a1,
            "n": int(ok.sum()), "mad": spread}


def _left_side(fs: dict[str, Any], z: dict[str, np.ndarray], b: np.ndarray, sig: np.ndarray,
               trust: float, gate_cols: np.ndarray) -> tuple[Side, dict[str, Any]]:
    """One side: the base runs, then -- where they find none -- a sliver.

    A sliver matters out of proportion to its width: with frames 445 columns
    wide in a 428-column aperture, "picture to the border" on both sides
    leaves a 13.6-unit window, and a 0.2-column sliver on one side places the
    frame to a unit (r0911_14/16/27, r0914_08, x0920_10 on dev).
    """
    side, dbg = _left_side_runs(fs, z, b, sig, trust, gate_cols)
    if side.state != PICTURE_TO_BORDER:
        return side, dbg
    sl = _sliver(fs["Ic"], np.exp(b), sig)
    dbg["sliver"] = sl
    if sl is None or sl["kappa"] < SLIVER_KAPPA or sl["frac"] < SLIVER_FRAC:
        return side, dbg
    x = float(np.clip(sl["x"], 0.05, 2.5))
    # the row spread of a0 gives the statistical part; 0.3 is the model's own
    # doubt (a0 + a1 is the area of a step only if the blur is symmetric)
    hw = 0.3 + 2.5 * 1.4826 * sl["mad"] / np.sqrt(sl["n"])
    conf = 0.6 * trust * min(1.0, (sl["frac"] - 0.5) / 0.4)
    return Side(EDGE, x=round(x, 3), lo=round(max(0.0, x - hw), 3), hi=round(x + hw, 3),
                conf=round(float(conf), 3),
                note=f"sliver: col 0 {sl['a0']:.2f} base, step {sl['kappa']:.2f} in "
                     f"{sl['frac']:.0%} of {sl['n']} rows"), dbg


def _mirror(side: Side, W: int) -> Side:
    if side.state != EDGE:
        return side
    def m(v: float | None) -> float | None:
        return None if v is None else round(W - v, 3)
    return Side(side.state, x=m(side.x), lo=m(side.hi), hi=m(side.lo), conf=side.conf,
                outer=m(side.outer), x_top=m(side.x_top), x_bottom=m(side.x_bottom),
                note=side.note)


# --------------------------------------------------------------------------
# the detector

def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    fs = frame_summary(image, ctx.get("film_type"))
    Ic, L = fs["Ic"], fs["L"]
    H, W, _ = Ic.shape
    bw = ctx.get("film_type") == "bw"
    gate_cols = fs["gate_cols"]
    debug: dict[str, Any] = {"gate_cols": int(gate_cols.sum())}

    if gate_cols.all():
        return EdgeResult(Side(NO_FILM, conf=0.8), Side(NO_FILM, conf=0.8), debug)

    best = fs["best"]
    levels = roll_levels(ctx)
    if best is None:
        # No flat full-height area anywhere. A roll level can still stand in,
        # if nothing in this frame is brighter than it -- film cannot be less
        # dense than its own base -- and it recurs in the roll; the brightest
        # such, because a level too dim would call a dark scene base.
        cands = []
        for lev in levels:
            sup = sum(bool(np.all(np.abs(o - lev) <= ROLL_TOL)) for o in levels)
            if sup >= 2 and _dominated(lev, fs["M"], fs["use"]) == 0:
                cands.append((float(lev.mean()), lev, sup))
        if not cands:
            debug["ref"] = "none"
            conf = 0.55 * (BW_CONF if bw else 1.0)
            s = Side(PICTURE_TO_BORDER, conf=round(conf, 3), note="no flat full-height area")
            left = Side(NO_FILM, conf=0.9) if gate_cols[0] else s
            right = Side(NO_FILM, conf=0.9) if gate_cols[-1] else s
            return EdgeResult(left, right, debug)
        _lum, b, sup = max(cands, key=lambda t: t[0])
        sig = _noise_at(fs, b)
        trust, source = 0.7, f"roll level only ({sup} frames)"
        ref_cols = None
    else:
        b = best["level"]
        sig = _base_noise(fs, best["a"], best["e"])
        ref_cols = [best["a"], best["e"]]
        if best["w"] >= ALL_BASE_FRAC * W:
            return EdgeResult(Side(ALL_BASE, conf=0.7), Side(ALL_BASE, conf=0.7),
                              {"ref": np.exp(b).round(1)})
        # How much the reference is worth: confirmed by the roll, or by a second
        # full-height plateau of this frame at the same level, it is base (or
        # scene black, which reads the same); alone, it is the brightest area.
        hit, n_roll = roll_support(b, ctx)
        twins = [p for p in fs["plateaus"] if p is not best and p["w"] >= 2
                 and np.all(np.abs(p["level"] - b) <= SAME_TOL)
                 and (p["a"] >= best["e"] + 8 or p["e"] <= best["a"] - 8)]
        # A frame whose brightest flat area is far below -- more than
        # ``FAR_BELOW`` in every channel -- a level that recurs in the roll,
        # with nothing of its own above that level, shows no base: its
        # brightest area is scene (bw0910_07/08: 11 counts against a roll base
        # of 19). Exposure differences inside a roll stay well inside this
        # (r0909: 0.20 / 0.14 / 0.06 ln); x0920's ladders move channels in
        # opposite directions and never trip it.
        # The same when the frame's best differs from such a level in colour
        # rather than density: dimmer in some channel by far more than in
        # another (``CHROMA_SPLIT``), never brighter in any. That is a
        # coloured scene area under the roll's base -- x0920_55's blue wall
        # reads (16787, 6129, 1227) against a base of (16500, 7700, 4000),
        # level in red and a third in blue. A neutral offset -- an exposure
        # change, a dark grey wall -- keeps the frame's own level.
        def replaces(lev: np.ndarray) -> bool:
            d = lev - b
            if np.any(d < -ROLL_TOL):
                return False
            return bool(np.all(d >= FAR_BELOW) or float(d.max() - d.min()) >= CHROMA_SPLIT)

        far = [lev for lev in levels if replaces(lev)
               and sum(bool(np.all(np.abs(o - lev) <= ROLL_TOL)) for o in levels) >= 2
               and _dominated(lev, fs["M"], fs["use"]) == 0]
        if hit == 0 and far:
            b = max(far, key=lambda v: float(v.mean()))
            sig = _noise_at(fs, b)
            ref_cols = None
        if hit == 0 and far:
            trust, source = 0.6, "roll level; the frame's brightest area is scene under it"
        elif hit >= 2 or (hit >= 1 and n_roll <= 3):
            trust, source = 1.0, f"roll {hit}/{n_roll}"
        elif twins:
            trust, source = 0.9, "second plateau in frame"
        elif best["w"] <= 2:
            trust, source = 0.6, "a border sliver only"
        else:
            trust, source = 0.75, "brightest plateau only"
    rg, bg = float(np.exp(b[0] - b[1])), float(np.exp(b[2] - b[1]))
    if ctx.get("film_type") == "c41" and not (C41_RG[0] <= rg <= C41_RG[1]
                                              and C41_BG[0] <= bg <= C41_BG[1]):
        trust *= 0.85
        source += f"; ratios {rg:.2f}/{bg:.2f} off the C-41 base"
    if bw:
        trust *= BW_CONF
    debug.update(ref=np.exp(b).round(2), ref_cols=ref_cols, sig=sig.round(4),
                 source=source, trust=trust)

    z = _zmaps(L, b, sig, fs["Mt"])
    # The column thresholds, relative to what the reference itself scores: on
    # 8-bit B&W at 19 counts the per-pixel test passes only 83-88% of true
    # base (bw0910_01, 388..428), against 93-99% on C-41; a fixed 0.85 then
    # cuts a 40-column base into noise-length pieces. The scene beside that
    # base, at base level in two thirds of its rows, scores 0.72-0.77.
    if ref_cols is not None:
        f_ref = float(np.median(z["f"][ref_cols[0]:ref_cols[1]]))
        fl_ref = float(np.median(z["f_loose"][ref_cols[0]:ref_cols[1]]))
        g_ref = float(np.median(fs["g"][ref_cols[0]:ref_cols[1]]))
    else:
        f_ref, fl_ref, g_ref = 1.0, 1.0, 1.0
    z["fb"] = np.asarray(min(F_BASE, f_ref - 0.08))           # 0-d: passes the flip unchanged
    z["gb"] = np.asarray(min(G_FLAT, g_ref - 0.08))
    z["fl_ref"] = np.asarray(fl_ref)
    z["bw"] = np.asarray(bw)
    z["g_ref"] = np.asarray(g_ref)
    debug.update(fb=float(z["fb"]), gb=float(z["gb"]))
    left, dl = _left_side(fs, z, b, sig, trust, gate_cols)
    zr = {k: (v[:, ::-1] if v.ndim == 2 else v[::-1]) if np.ndim(v) else v
          for k, v in z.items()}
    fr = dict(fs, Ic=Ic[:, ::-1], L=L[:, ::-1], g=fs["g"][::-1])
    right_m, dr = _left_side(fr, zr, b, sig, trust, gate_cols[::-1])
    right = _mirror(right_m, W)
    # Two edges must leave room for a frame. The weaker one -- an interior gap
    # before a border-touching one, then the lower confidence -- is a scene
    # area at base level, and a frame whose other side shows base runs off
    # this border.
    if left.state == EDGE and right.state == EDGE and left.x is not None \
            and right.x is not None and right.x - left.x < MIN_FRAME_COLS:
        key = lambda sd: (sd.outer is None, sd.conf)          # noqa: E731
        weak = "left" if key(left) < key(right) else "right"
        other = right if weak == "left" else left
        repl = Side(PICTURE_TO_BORDER, conf=round(0.6 * other.conf, 3),
                    note=f"base-level area rejected: frame would be {right.x - left.x:.0f} cols")
        if weak == "left":
            left = repl
        else:
            right = repl
        debug["narrow"] = weak
    debug["left"] = dl
    debug["right"] = dr
    return EdgeResult(left, right, debug)
