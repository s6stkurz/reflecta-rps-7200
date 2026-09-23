"""Frame edges from texture along the column plus level: base is the brightest AND the quietest.

The idea, and what measurement did to it
----------------------------------------

Unexposed film base carries only sensor noise; exposed film carries grain and
picture structure even where it is almost as clear as base. So measure local
texture, normalise it by the noise the frame itself shows, combine it with
level, and find per band of rows the change-point going inward from each
border, requiring agreement over the full height.

Measured on the dev frames before any detector was written (8-bit prescans,
`scan applied`, all at the base level of ~66/29/15 counts):

    region                        |dy| p75/level  R  G      band-median spread R
    real base (r0914_01, _20, r0911_19)   4.4-4.6%  6.6-6.8%     0.8-1.1 %
    dark wall (r0911_04 left)             3.1%      3.5%         1.0 %
    night scene (r0911_22 borders)        4.5%      6.8%         0.8 %
    wall in shade (r0914_19 left)         4.8%      7.1%         2.4 %
    lamp background (r0914_20 104-140)    5.5%      7.7%         6.5 %

* **Pixel-scale texture does not separate them.** At 300 dpi one pixel is
  85 um; the film's granularity there is about 0.4 counts against ~2 counts
  of sensor noise at base level, a few percent of the variance. Grain is not
  visible in a prescan, and a dark scene at fog level *is* base physically.
* **Band-scale structure along y does separate some picture** -- a gradient,
  a shadow, a glow -- and never flags base, which is flat along y to its noise.
  It is the useful part of "texture" here, and it is what `UNIFORM_MAX` tests.
* What remains, a uniform scene area at the base level, is separated only by
  geometry: real base ends in a *straight step, the same column in every band*.

**Which direction to differentiate.** The CCD spans the columns, so its
fixed pattern is a per-column offset and gain -- vertical stripes. Every
texture measure here is taken *along a column* (differences between rows of
the same column, spreads of band medians of the same column), where that
pattern is a constant and cancels. Nothing differentiates along a row except
the step itself, which compares columns only a few apart and only by their
band medians, where the pattern is a fraction of a count.

**Normalisation.** Nothing is compared against a dtype maximum or a fixed
count: the level is divided by this frame's own base estimate (per channel),
and noise is measured on this frame's own brightest columns at the border it
is testing, then scaled by the shot-noise law. The dups (another exposure,
another bit depth) are the check that this holds.

What the detector does, per side (the right is the left of the mirrored
frame, so the answer mirrors by construction -- exactly, on every dev frame)
-------------------------------------------------------------------------

1. **Gate.** Columns at a border far brighter than the film (`GATE_RATIO`),
   flat along y (`GATE_FLAT`, texture again: no film, no picture) and, on
   colour film, neutral, are the empty gate: ``no_film``.
2. **Base level** ``B``: per channel, the brightest column median on film
   (`_base_level`). A frame with no base gets its brightest picture instead;
   the step test, not the level, is what refuses that.
3. **Bands**: the rows are cut into `N_BANDS` bands; per band and column the
   median of the combined, base-normalised level ``Y`` (base = 1.0).
4. **Base-like** per band and column: ``Y`` within `LEVEL_TOL` (plus the band
   median's noise) of the side's own plateau, which must itself be near ``B``
   in every channel separately (`PLATEAU_TOL`). A column is *full-height base*
   when every band is; a dip at the top or bottom end keeps its own plateau
   (`END_DIP`).
5. **The step**: for each candidate transition column, the drop from the base
   side to the picture side, per band, in units of its noise; summed over the
   bands along a straight (possibly tilted) line. An edge needs the run of
   full-height base before it, a drop that is significant in the sum
   (`Z_EDGE`), present in a third of the bands and in the rest either present
   or flat (a base-level scene meeting the gap), never reversed, a base run
   that is flat along y (`UNIFORM_MAX`), and crossings on one straight line.
6. **Position**: per band the 50 % crossing between the base plateau and the
   picture beside it (area method, as the labels are read), then a weighted
   straight-line fit over the bands: ``x``, ``x_top``, ``x_bottom``.
7. **A band inside the frame** that passes all of that, bounded by a
   full-height rise on its outer side, is **refused**, never answered: on dev
   it was a scene band three times and a gap once, and the pixels do not say
   which.
8. **No base** is claimed as ``picture_to_border`` only on evidence that
   survives an uncorrected prescan's 39% falloff: structure along y, a level
   below anything the falloff leaves of base, or one channel far below its own
   base (a dye, not a falloff). A border that is flat and merely darker than
   base -- picture if the prescan was corrected, dimmed base if not -- or flat
   at the base level with no step within a gap's width, is **refused**.
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:  # the runner puts research/frame-edge on sys.path
    from common import (
        ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, TRIM_ROWS, EdgeResult, Side,
    )
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import (  # noqa: E402
        ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, TRIM_ROWS, EdgeResult, Side,
    )

# --------------------------------------------------------------------------
# constants, and why each has its value

#: Bands of rows. 276 trimmed rows / 12 = 23 rows per band: a band median then
#: has ~0.3 of a pixel's noise, and a silhouette that touches the border over a
#: sixth of the height still owns two bands.
N_BANDS = 12

#: A gap is at most this many columns wide. `docs/frame-measurement-plan.md`:
#: the gap must exceed 21.7 units (27 columns) for the frame to fit, flat core
#: 15.2 units (19 columns). 34 leaves room for tilt and a soft edge; a base-level
#: run from the border wider than this is not all base.
MAX_GAP = 34
#: A gap bounded on the outside by the neighbour's picture shows its full
#: width. Walk K's gaps are 19+ columns, but the stage9b ladder's right-hand
#: gap is 9 (ladder9b_03: black 397-406, the neighbour's picture beyond) and
#: r0911_17's is 5.4 by the labels, so gaps differ between cameras and
#: frames; 5 is a floor, not a model. Such an *interior* band is never
#: answered as an edge, only refused -- see `_side`.
MIN_INTERIOR_GAP = 5
#: An interior gap has no border to lean on, so its outer rise must be seen in
#: this fraction of bands (the inner step may still merge with a dark scene).
RISE_FRACTION = 0.75
#: How far in from the border the outer side of a gap may lie.
REACH_OUTER = 140

#: Base-like: a band median within this fraction of the side's base plateau.
#: Base itself varies by up to ~4% across a gap (shading residual 1.4% plus
#: the per-column gain; r0914_20's gap runs 70.9 -> 68.3 counts), so this is
#: a coarse gate; the step is what decides.
LEVEL_TOL = 0.06
#: The outermost column sits at the aperture's edge and in a resampled or
#: uncorrected source is not uniform along y: r0914_34's last column (a 1-2
#: column base) spans +-2% in the corrected prescan, +-3% in its as-delivered
#: dup and +-9% in its uint16 dup. It gets twice the level tolerance, and a
#: base run of at most `THIN_RUN` columns with a full-height step (all bands
#: but `THIN_MISS`) is not asked to be flat -- the step is the evidence there.
EDGE_COL_TOL = 2 * 0.06
THIN_RUN = 2
THIN_MISS = 2
#: Base may darken toward the top or bottom end of the frame and still be
#: base: every B&W dev frame's left strip (bw0910, 2-7 columns by the labels)
#: holds 0.97 of base over nine bands and falls to 0.87, 0.78, 0.63 over the
#: last three (0.92 already in the fourth), flat across x within each, with
#: the step still there; the uint16 ones dim at the top band as well
#: (bw0910_09: 0.86 top, 0.88 and 0.69 bottom). Up to `END_DIP` bands in all,
#: contiguous from the top and from the bottom end -- a third of the height --
#: may keep their own plateau once it is half a `LEVEL_TOL` low, if not below
#: `DIP_FLOOR`; they must still show the step or stay flat.
END_DIP = 4
DIP_FLOOR = 0.5
#: The side's plateau may sit this far below the frame's brightest column and
#: still be base. Measured: an as-delivered (uncorrected) prescan falls off in
#: x, and base at column 0 of ladder9b_11 reads 26.8 against 29.6 at column 21
#: of the same gap (-9.5%); and on C-41 picture can be brighter than base
#: (strip6_01: base 54/26/14, picture p99.5 63/31/17). So the level only
#: admits a candidate; the step and the flatness decide.
PLATEAU_TOL = 0.15

#: Combined step significance (sum over bands of per-band z / sqrt(bands)).
#: Real border edges on dev read 33-650. Below 30 the candidates were scene
#: edges meeting a base-level wall (r0911_06 at 25, r0911_13 at 21, r0911_22
#: at 9.5), and the unit is soft: band medians of real base spread up to 2.4x
#: the white-noise model (see `UNIFORM_MAX`), so "30 sigma" is nearer 12.
#: Iteration 3 used 15 and took r0911_06 as base.
Z_EDGE = 30.0
#: Per band, a drop at least this many sigma counts as the step being there.
Z_BAND = 3.0
#: Bands that must show the step. Where a scene area at base level meets the
#: gap there is no contrast to show, so a band may instead be *flat* (the
#: picture side still base-level); what may not happen is a band that is
#: darker beyond the gap but not at this column. At least this fraction
#: steps, and never fewer than `MIN_STEP_BANDS`.
BAND_FRACTION = 1.0 / 3.0
MIN_STEP_BANDS = 4
#: The per-band crossings must lie on one straight line to within this (cols).
MAX_SCATTER = 1.2
#: No band may show a reversed step (picture brighter than base) beyond this.
Z_REVERSED = 4.0
#: Band-median spread along y about a straight line in y, in units of the
#: white-noise model, above which a base run is structured -- picture. Real
#: base on C-41 measured 0.8-2.0; on the 8-bit B&W dups, at ~20 counts where
#: the band median is quantized, 1.6-2.4 (bw0910_01 2.26, bw0910_04 2.42, both
#: base by the labels). Structured picture at the base level: 2.6 and up
#: (r0914_18's tiles, r0911_17's picture), glows and silhouettes 4-50. The
#: undecidable remainder is a flat dark wall, which no texture can tell from
#: base; there the step decides. Iteration 3 used 2.2 and lost the B&W edges.
UNIFORM_MAX = 2.5
#: Evidence for picture at the border: bands in which the outer columns are
#: clearly not base.
P2B_MIN_BANDS = 3
#: The largest dimming an uncorrected prescan's falloff can put on base at the
#: border: 39% across the frame raw (`docs/vignette-plan.md`, red channel).
FALLOFF_MAX = 0.39

#: Empty gate: brighter than the film's brightest column by this in every
#: channel. Measured 182 counts against C-41 base 65/29/15 (2.8x in red, the
#: tightest) and B&W base ~19 (9.6x).
GATE_RATIO = 1.8
#: ... and on colour film neutral: min/max channel ratio. The gate reads
#: 182/181/180; C-41 base 65/29/15 reads 0.23.
GATE_NEUTRAL = 0.75
#: ... and with no film in it, flat: the relative spread of a column's band
#: medians along y. Measured: the gate 0.25% (r0909_13) and 0.31% (r0914_21);
#: a dark painting that a colour-rebalanced uint16 dup renders neutral and
#: 2.8x brighter than the rest of the frame (x0920_29, cols 0-170) 30%.
GATE_FLAT = 0.02
#: The cut end of a strip, between gate and film, is neutral and bright but
#: not flat: 2 columns on both gate frames on dev. Wider than this, the
#: neutral stretch is not a cut end but film a rebalanced dup renders neutral
#: (x0920_29: 2 flat base columns, then 174 of painting). And a gate run
#: narrower than `GATE_MIN` is not told apart from such base at all.
GATE_CUT = 6
GATE_MIN = 3

#: Two edges imply the frame's width, x_right - x_left. A 36 mm frame is 425
#: columns nominal (445 measured on dev, wider than the aperture); below this
#: the pair cannot both be frame edges: r0914_17's black grid line between TV
#: tiles, 13 columns wide and full height, sat at 49-62 against a real right
#: edge at 418.5 -- a 356-column frame. (Since interior bands are refused this
#: guards only pairs of border edges.)
FRAME_MIN = 395.0

#: Tilt search: total drift of the line over the full height, in columns.
TILTS = (-2.0, -1.0, 0.0, 1.0, 2.0)


# --------------------------------------------------------------------------
# helpers

def _rgb(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 2:
        img = img[..., None]
    if img.shape[2] >= 3:
        return img[..., :3]
    return np.repeat(img[..., :1], 3, axis=2)


def _robust_sigma_dy(block: np.ndarray) -> np.ndarray:
    """Per-pixel noise from differences *along the column* (rows), per channel.

    ``block`` is (rows, cols, ch). The 75th percentile of |dy| is 1.15 sigma of
    dy for a Gaussian and ignores picture structure in a quarter of the rows;
    dividing by sqrt(2) takes a difference back to one pixel.
    """
    d = np.abs(np.diff(block, axis=0))
    p75 = np.percentile(d.reshape(-1, block.shape[2]), 75, axis=0)
    return np.maximum(p75 / 1.15 / np.sqrt(2.0), 1e-9)


def _gate_columns(colmed: np.ndarray, flat_y: np.ndarray, film: str) -> tuple[int, int]:
    """Columns of empty gate contiguous from the left and from the right border.

    ``flat_y`` is each column's relative spread along y (band medians). The
    gate is found on flat columns and then extended through the strip's cut
    end, which is neutral and bright but, being tilted or ragged, not flat
    (r0909_13, column 150: 0.56) -- left out, it would stand in the film as
    its brightest column.
    """
    W = colmed.shape[0]
    lvl = np.maximum(colmed, 1e-6)
    neutral = lvl.min(axis=1) / lvl.max(axis=1)
    gm = np.exp(np.log(lvl).mean(axis=1))
    if film == "bw":
        loose = gm >= 4.0 * np.percentile(gm, 10)
    else:
        loose = neutral >= GATE_NEUTRAL
    core = loose & (flat_y < GATE_FLAT)

    def run(mask: np.ndarray, order: np.ndarray) -> int:
        n = 0
        for x in order:
            if not mask[x]:
                break
            n += 1
        return n

    fwd, bwd = np.arange(W), np.arange(W)[::-1]
    nl, nr = run(core, fwd), run(core, bwd)
    el = run(loose, fwd) if nl else 0
    er = run(loose, bwd) if nr else 0
    if nl >= W:
        return W, 0
    if el + er >= W:
        return (0, 0)
    el = el if (nl >= GATE_MIN and el - nl <= GATE_CUT) else 0
    er = er if (nr >= GATE_MIN and er - nr <= GATE_CUT) else 0
    rest = np.ones(W, bool)
    rest[:el] = False
    rest[W - er:] = False
    ref = colmed[rest].max(axis=0)
    res = []
    for n, e, sl in ((nl, el, slice(0, nl)), (nr, er, slice(W - nr, W))):
        if n < GATE_MIN or e - n > GATE_CUT:
            res.append(0)
            continue
        ok = np.all(colmed[sl] >= GATE_RATIO * ref[None, :], axis=1)
        res.append(e if ok.mean() > 0.8 else 0)
    return res[0], res[1]


def _base_level(colmed: np.ndarray) -> np.ndarray:
    """Brightest film column, per channel, from the combined ranking.

    One column is enough -- base can be one column wide -- so the maximum is
    taken, of the column with the highest geometric-mean level (so all three
    channels come from the same column, not three different ones).
    """
    lvl = np.maximum(colmed, 1e-6)
    gm = np.exp(np.log(lvl).mean(axis=1))
    tied = gm >= gm.max() * (1.0 - 1e-9)       # ties averaged: the mirror agrees
    return colmed[tied].mean(axis=0)


# --------------------------------------------------------------------------
# one side

def _bands(n_rows: int) -> list[tuple[int, int]]:
    e = np.linspace(0, n_rows, N_BANDS + 1).astype(int)
    return [(int(e[i]), int(e[i + 1])) for i in range(N_BANDS)]


def _step_z(m: np.ndarray, s_m: np.ndarray, a: int, ks: np.ndarray) -> np.ndarray:
    """Per band, the drop from base side to picture side at transition ``ks[b]``.

    Base side: up to two columns before the transition column (never before the
    run start ``a``); picture side: the two columns after it. The transition
    column itself is left out -- it is the mixed one. In units of its noise.
    """
    nb = m.shape[0]
    z = np.empty(nb)
    for b in range(nb):
        k = int(ks[b])
        lo = max(a, k - 2)
        left = m[b, lo:k].mean()
        right = m[b, k + 1:k + 3].mean()
        z[b] = (left - right) / (s_m[b] * np.sqrt(1.0 / (k - lo) + 0.5))
    return z


def _crossing(m: np.ndarray, s_m: np.ndarray, a: int, ks: np.ndarray,
              W: int) -> list[tuple[int, float, float]]:
    """Per band, the 50 % crossing by area: (band, x, weight).

    The labels read an edge as the transition column's base fraction
    ``(v - P) / (L - P)``; summing that fraction over a window that starts on
    the plateau and ends in picture gives the boundary directly, whether the
    edge is sharp or spread over two columns. Weight: contrast squared, so a
    band where picture meets base with no contrast does not steer the line.
    """
    out = []
    for b in range(m.shape[0]):
        k = int(ks[b])
        lo, hi = max(a, k - 2), min(W - 1, k + 2)
        L = float(np.median(m[b, max(a, k - 3):k])) if k > a else 1.0
        P = float(m[b, min(W - 1, k + 2)])
        if L - P <= 3.0 * s_m[b]:
            continue
        frac = np.clip((m[b, lo:hi] - P) / (L - P), 0.0, 1.0)
        out.append((b, lo + float(frac.sum()), (L - P) ** 2))
    return out


def _line(pts: list[tuple[int, float, float]], yc: np.ndarray,
          H: int) -> tuple[float, float, float]:
    """Weighted straight line through the per-band crossings: (x at mid-height,
    drift top-to-bottom, weighted rms scatter). A drift beyond 4 columns is not
    near-vertical; then the line is taken flat.
    """
    bb = np.array([p[0] for p in pts])
    xs = np.array([p[1] for p in pts])
    ws = np.array([p[2] for p in pts])
    u = yc[bb] / H - 0.5
    A = np.stack([np.ones(len(bb)), u], axis=1)
    sw = np.sqrt(ws)
    coef, *_ = np.linalg.lstsq(A * sw[:, None], xs * sw, rcond=None)
    x0, slope = float(coef[0]), float(coef[1])
    if abs(slope) > 4.0 or len(bb) < 4:
        x0, slope = float(np.average(xs, weights=ws)), 0.0
    res = xs - (x0 + slope * u)
    return x0, slope, float(np.sqrt(np.average(res ** 2, weights=ws)))


def _side(Y: np.ndarray, sig_pix: float, chan: np.ndarray,
          interior: bool = True) -> tuple[Side, dict[str, Any]]:
    """Analyse the left border of ``Y`` (rows x film columns, base ~ 1.0).

    ``chan`` is (columns, 3): each channel's column median over the channel's
    own brightest column. Base is at the top in *every* channel; the combined
    ``Y`` leans on red, which has the best signal to noise, and alone would
    let a region that is bright in red and dense in blue pass as base
    (r0914_34: red 0.99 of base, green 0.76, blue 0.30).
    """
    H, W = Y.shape
    bands = _bands(H)
    nb = len(bands)
    m = np.stack([np.median(Y[a:b], axis=0) for a, b in bands])          # (nb, W)
    h = np.array([b - a for a, b in bands], float)
    s_m = 1.25 * sig_pix / np.sqrt(h)                                     # (nb,)
    yc = np.array([(a + b) / 2.0 for a, b in bands])
    # uniformity along y: spread of the band medians about a straight line in
    # y (base may carry a smooth shading along y, ~1%), the worst band dropped
    # (a hair, a dust clump), in units of the band-median noise
    u = yc / H - 0.5
    Ay = np.stack([np.ones(nb), u], axis=1)
    coef, *_ = np.linalg.lstsq(Ay, m, rcond=None)
    dev2 = np.sort((m - Ay @ coef) ** 2, axis=0)[:-1]
    U = np.sqrt(dev2.sum(axis=0) / max(nb - 3, 1)) / float(np.median(s_m))
    dbg: dict[str, Any] = {"U_border": np.round(U[:4], 2)}

    reach = min(W - MIN_INTERIOR_GAP - 3, REACH_OUTER)
    # outer starts: the border, or a full-height rise from the neighbour's
    # picture into base -- a local maximum of the combined rise
    rz = np.full(W, -np.inf)
    for a in range(2, reach):
        rise = (m[:, a] - m[:, a - 2]) / (np.sqrt(2.0) * s_m)
        if np.mean(rise > Z_BAND) >= RISE_FRACTION and rise.min() > -Z_REVERSED:
            rz[a] = rise.sum() / np.sqrt(nb)
    starts = [0] + [a for a in range(2, reach) if interior
                    and rz[a] > Z_EDGE and rz[a] >= rz[a - 1] and rz[a] >= rz[a + 1]]
    runs: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    interior_seen: dict[str, Any] | None = None
    for a0 in starts:
        # plateau: the brightest of the first columns from the start (by their
        # median over bands) -- clear of a soft rise, of an outermost column
        # the aperture's own edge shades (ladder9b_12: column 0 at half), and
        # still right when base is a single column (r0914_34's right)
        seg = slice(a0, min(W, a0 + 4))
        plateau = float(np.median(m[:, seg], axis=0).max())
        rec: dict[str, Any] = {"a": a0, "plateau": round(plateau, 4)}
        runs.append(rec)
        pc = chan[seg].max(axis=0)
        rec["per_channel"] = np.round(pc, 3)
        if plateau < 1.0 - PLATEAU_TOL or pc.min() < 1.0 - PLATEAU_TOL:
            rec["why"] = "below base"
            continue
        # per band, the plateau of those first columns; a dip at one end of
        # the height keeps its own (see END_DIP), anywhere else it does not
        Pb = m[:, seg].max(axis=1)
        ref = np.full(nb, plateau)
        dip = Pb < plateau * (1.0 - LEVEL_TOL / 2.0)
        if dip.any():
            # contiguous from the top and from the bottom, nothing in between
            n_top = int(np.argmin(dip)) if not dip.all() else nb
            n_bot = int(np.argmin(dip[::-1])) if not dip.all() else nb
            if (n_top + n_bot == int(dip.sum()) and n_top + n_bot <= END_DIP
                    and Pb[dip].min() >= DIP_FLOOR):
                ref[dip] = Pb[dip]
                rec["dip"] = np.flatnonzero(dip).tolist()
        solid = ref >= plateau                  # bands at the full plateau

        # the run starts at the first full-height base-like column near a0
        def base_col(c: int) -> bool:
            # the fixed allowance for shading, plus the band median's own
            # noise: 4.6% a pixel on the uint16 B&W dups is 1.2% a band, and a
            # fixed 6% is then five sigma (bw0910_11, column 1, band 9: -7.6%)
            tol = (EDGE_COL_TOL if c == 0 else LEVEL_TOL) + 3.0 * s_m
            return bool(np.all(m[:, c] >= ref * (1.0 - tol)))
        a = next((c for c in range(max(0, a0 - 1), min(W, a0 + 4)) if base_col(c)), None)
        if a is None:
            rec["why"] = "no full-height base column"
            continue
        if a0 == 0 and a == 1:
            a = 0                          # column 0 shaded: still base [0, x)
        k = a + 1
        while k < W and base_col(k):
            k += 1
        rec["run"] = k - a
        rec["a_run"] = a
        if a == 0 and k >= W - 1:
            rec["why"] = "all base"
            best = rec
            break
        # the step may lie inside the run: a scene area at base level extends
        # the run past the true edge, and only the step shows where it is
        hi_k = min(k + 2, a + MAX_GAP + 2, W - 3)
        cands = []
        frac_y = (yc / H) - 0.5
        for kk in range(a + 1, hi_k + 1):
            for tilt in TILTS:
                ks = kk + np.round(tilt * frac_y).astype(int)
                if ks.min() < a + 1 or ks.max() + 3 > W:
                    continue
                z = _step_z(m, s_m, a, ks)
                cands.append((float(z.sum() / np.sqrt(nb)), kk, tilt, z, ks))
        if not cands:
            rec["why"] = "no room for a step"
            continue
        Z, kk, tilt, z, ks = max(cands, key=lambda c: c[0])
        # flatness of the base run, on the full-height base columns before
        # the step, less the innermost one: next to a tilted or soft edge a
        # column is mixed in some bands and varies along y because of it
        # (r0914_34's right: the base column 1.5, the one beside it 6.9)
        run_end = min(k, int(ks.min()))
        cols = np.arange(a, max(a + 1, run_end))
        n_run = cols.size
        if cols.size >= 2:
            cols = cols[:-1]
        if cols.size >= 3:
            cols = cols[1:]
        if solid.all():
            unif = float(np.median(U[cols]))
        else:                                   # flatness over the solid bands only
            mm = m[solid][:, cols]
            As = Ay[solid]
            cf, *_ = np.linalg.lstsq(As, mm, rcond=None)
            d2 = np.sort((mm - As @ cf) ** 2, axis=0)[:-1]
            Uc = np.sqrt(d2.sum(axis=0) / max(int(solid.sum()) - 3, 1)) / float(np.median(s_m))
            unif = float(np.median(Uc))
        width = float(np.median(ks)) - a
        step = z > Z_BAND
        # a band without the step is acceptable only if the picture side there
        # is still base-level: no contrast, not a contradiction
        pic = np.array([m[b, ks[b] + 1:ks[b] + 3].mean() for b in range(nb)])
        flat = ~step & (pic >= ref * (1.0 - LEVEL_TOL / 2.0))
        n_step, n_flat = int(step.sum()), int(flat.sum())
        rec.update({"k": kk, "tilt": tilt, "Z": round(Z, 2), "steps": n_step,
                    "flat": n_flat, "zmin": round(float(z.min()), 2),
                    "uniform": round(unif, 2)})
        why = []
        if Z < Z_EDGE:
            why.append("weak step")
        if n_step < max(MIN_STEP_BANDS, BAND_FRACTION * nb):
            why.append("step in too few bands")
        if n_step + n_flat < nb - 1:
            why.append(f"{nb - n_step - n_flat} bands darker beyond but no step here")
        if z.min() <= -Z_REVERSED:
            why.append("reversed in a band")
        thin = n_run <= THIN_RUN and n_step >= nb - THIN_MISS
        if unif > UNIFORM_MAX and not thin:
            why.append("base side structured")
        if width > MAX_GAP:
            why.append("wider than a gap")
        if a0 > 0 and width < MIN_INTERIOR_GAP:
            why.append("interior gap too narrow")
        if not why:
            pts = _crossing(m, s_m, a, ks, W)
            fit = _line(pts, yc, H) if len(pts) >= 3 else None
            if fit is None:
                why.append("too faint to place")
            elif fit[2] > MAX_SCATTER:
                why.append(f"not straight (scatter {fit[2]:.2f})")
            else:
                rec["fit"] = fit
                rec["n_pts"] = len(pts)
        rec["why"] = ", ".join(why) or "ok"
        if a0 > 0 and (not why or why == ["interior gap too narrow"]):
            # a full-height, flat, base-level band inside the frame with a
            # straight step on both sides. On dev it was a scene band three
            # times (r0914_19, r0914_20, x0920_73's TV grid) and a real gap
            # with the neighbour's picture beyond once (r0911_17, 5.4 wide);
            # the stage9b ladder has real ones. The pixels do not say which,
            # and "picture to border" would hide a displaced frame: refuse.
            interior_seen = rec
            continue
        if not why:
            rec["ks"] = ks
            best = rec
            break
    dbg["runs"] = runs

    if best is not None and best["why"] == "all base":
        return Side(ALL_BASE, conf=0.6, note="full-height flat base across the frame"), dbg

    if best is not None:
        a = best["a_run"]
        x0, slope, scatter = best["fit"]
        n = best["n_pts"]
        half = 0.3 + 2.0 * scatter / np.sqrt(n)
        full = best["steps"] / nb
        conf = float(np.clip(0.4 + 0.5 * full + (best["Z"] - Z_EDGE) / 200.0, 0.3, 0.95))
        side = Side(EDGE, x=x0, lo=x0 - half, hi=x0 + half, conf=conf,
                    outer=float(a) if best["a"] > 0 else None,
                    x_top=x0 - slope / 2.0, x_bottom=x0 + slope / 2.0,
                    note=f"Z {best['Z']:.0f}, step in {best['steps']}/{nb} bands "
                         f"({best['flat']} flat), scatter {scatter:.2f}")
        dbg["edge"] = {"x": round(x0, 3), "slope": round(slope, 3),
                       "scatter": round(scatter, 3)}
        return side, dbg

    if interior_seen is not None:
        r = interior_seen
        return Side(REFUSE, note=f"full-height base-level band from column {r['a_run']} "
                                 f"to ~{r['k']} (Z {r['Z']:.0f}): the gap with the "
                                 "neighbour's picture beyond, or a dark band in the "
                                 "scene"), dbg

    # --- no edge: is there evidence of picture at the border? --------------
    # Evidence has to survive an uncorrected prescan's lamp-and-sensor falloff
    # (39% across x, `docs/vignette-plan.md`), which dims base at the border
    # by a smooth factor the frame cannot reveal: r0911_03's base, a straight
    # full-height strip to column 17, reads 0.76 of the frame's brightest
    # column. So, in order of strength:
    #   * structure along y, which a smooth x-profile cannot create, in both
    #     outer columns (column 0 alone is the aperture's edge, see
    #     EDGE_COL_TOL), with some band darker than base;
    #   * bands darker than any falloff could make base (`FALLOFF_MAX`);
    #   * only darker than base, and flat: picture on a corrected prescan,
    #     base on an uncorrected one -- said with little confidence.
    hard = [int(np.sum(m[:, c] < 1.0 - FALLOFF_MAX)) for c in (0, 1)]
    # one channel far below its own maximum over the full height: the falloff
    # is near neutral (39/36/35%), a dye is not (r0914_13's blue wall: red at
    # base, blue at 0.30)
    chan_low = [float(chan[c].min()) for c in (0, 1)]
    soft = [int(np.sum(((1.0 - LEVEL_TOL) - m[:, c]) / s_m > Z_BAND)) for c in (0, 1)]
    u01 = float(min(U[0], U[1]))
    structured = u01 > UNIFORM_MAX
    dbg["border"] = {"hard": hard, "soft": soft, "U01": round(u01, 2),
                     "lvl": np.round(np.sort(m[:, 0]), 3)}
    if structured and min(soft) >= 1:
        conf = float(np.clip(0.7 + 0.02 * min(soft) + 0.01 * u01, 0.7, 0.95))
        return Side(PICTURE_TO_BORDER, conf=conf,
                    note=f"border structured along y (U {u01:.1f}), darker than base "
                         f"in {min(soft)}/{nb} bands"), dbg
    if max(chan_low) < 1.0 - FALLOFF_MAX:
        return Side(PICTURE_TO_BORDER, conf=0.85,
                    note=f"border's weakest channel at {max(chan_low):.2f} of that "
                         "channel's base: a dye, not a falloff"), dbg
    if min(hard) >= P2B_MIN_BANDS:
        conf = float(np.clip(0.7 + 0.02 * min(hard), 0.7, 0.95))
        return Side(PICTURE_TO_BORDER, conf=conf,
                    note=f"border darker than any falloff leaves base in {min(hard)}/{nb} "
                         "bands"), dbg
    if min(soft) >= P2B_MIN_BANDS:
        # picture on a corrected prescan, base under an uncorrected one's
        # falloff; the pixels do not say which (r0911_03 is the latter)
        return Side(REFUSE, note=f"border flat and darker than base in {min(soft)}/{nb} "
                                 "bands: picture if the prescan is corrected, dimmed "
                                 "base if not"), dbg
    if structured:
        return Side(PICTURE_TO_BORDER, conf=0.45,
                    note=f"border at base level but structured along y (U {u01:.1f})"), dbg
    return Side(REFUSE, note="border at base level and flat, no step within a gap: "
                             "dark scene or hidden base"), dbg


# --------------------------------------------------------------------------
# the frame

def _to_frame(s: Side, lo_f: int, hi_f: int, mirrored: bool) -> Side:
    """Side coordinates (film columns from this border inward) to frame columns."""
    def f(v: float | None) -> float | None:
        if v is None:
            return None
        return hi_f - v if mirrored else lo_f + v
    lo, hi = f(s.lo), f(s.hi)
    if mirrored:
        lo, hi = hi, lo
    return Side(EDGE, x=f(s.x), lo=lo, hi=hi, conf=s.conf, outer=f(s.outer),
                x_top=f(s.x_top), x_bottom=f(s.x_bottom), note=s.note)


def detect(image: np.ndarray, ctx: dict) -> EdgeResult:
    rgb = _rgb(image)
    t = rgb[TRIM_ROWS:rgb.shape[0] - TRIM_ROWS]
    H, W, _ = t.shape
    film = str(ctx.get("film_type", "unknown"))
    colmed = np.median(t, axis=0)
    bm = np.stack([np.median(t[a:b], axis=0) for a, b in _bands(H)])     # (nb, W, 3)
    flat_y = (bm.std(axis=0) / np.maximum(bm.mean(axis=0), 1e-6)).mean(axis=1)
    gl, gr = _gate_columns(colmed, flat_y, film)
    debug: dict[str, Any] = {"gate": [gl, gr]}
    if gl >= W:
        return EdgeResult(Side(NO_FILM, conf=0.9), Side(NO_FILM, conf=0.9), debug)
    lo_f, hi_f = gl, W - gr
    B = _base_level(colmed[lo_f:hi_f])
    B = np.maximum(B, 1e-6)
    # per-channel noise at base level, measured on this frame's brightest columns
    gm = np.exp(np.log(np.maximum(colmed[lo_f:hi_f], 1e-6)).mean(axis=1))
    # the brightest 5% of film columns, chosen by threshold and not by rank
    # (a rank breaks ties by position, and the mirrored frame then measures
    # other columns: 30% apart on r0911_07); per column, then the median
    top = np.flatnonzero(gm >= np.percentile(gm, 95)) + lo_f
    per_col = np.stack([_robust_sigma_dy(t[:, c:c + 1, :]) for c in top])
    sig_c = np.median(per_col, axis=0)
    lvl_top = np.maximum(np.median(colmed[top], axis=0), 1e-6)
    sig_c = sig_c * np.sqrt(B / lvl_top)          # shot-noise law to the base level
    rel = sig_c / B
    w = 1.0 / rel ** 2
    w = w / w.sum()
    Y = (t / B[None, None, :] * w[None, None, :]).sum(axis=2)
    sig_pix = float(np.sqrt(np.sum((w * rel) ** 2)))
    debug.update({"B": B, "sig_rel": rel, "sig_pix": sig_pix})

    cm = colmed[lo_f:hi_f]
    chan = cm / np.maximum(cm.max(axis=0), 1e-6)[None, :]

    def one(name: str, interior: bool = True) -> tuple[Side, dict[str, Any]]:
        n_gate = gl if name == "left" else gr
        if n_gate > 0:
            return Side(NO_FILM, conf=0.9, note=f"{n_gate} columns of empty gate"), {}
        Ys = Y[:, lo_f:hi_f]
        cs = chan
        if name == "right":
            Ys, cs = Ys[:, ::-1], cs[::-1]
        s, d = _side(np.ascontiguousarray(Ys), sig_pix, cs, interior)
        if s.state == EDGE:
            s = _to_frame(s, lo_f, hi_f, mirrored=(name == "right"))
        return s, d

    sides: dict[str, Side] = {}
    for name in ("left", "right"):
        sides[name], debug[name] = one(name)
    width = _implied_width(sides["left"], sides["right"])
    if width is not None and width < FRAME_MIN:
        # the pair cannot both be frame edges, and neither says which it is not
        why = f"two edges imply a {width:.0f}-column frame"
        sides = {"left": Side(REFUSE, note=why), "right": Side(REFUSE, note=why)}
    return EdgeResult(sides["left"], sides["right"], debug)


def _implied_width(left: Side, right: Side) -> float | None:
    """The frame width two edges imply, or None unless both sides are edges."""
    if left.state != EDGE or right.state != EDGE or left.x is None or right.x is None:
        return None
    return right.x - left.x
