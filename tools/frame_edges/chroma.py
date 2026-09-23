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
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .sides import (
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, TRIM_ROWS, EdgeResult, Side,
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
#: Base must start within this many columns of the border -- the outer 64
#: columns are what the labeller's zoomed views show, and the ladder's gaps sit
#: 28-46 columns in.
MAX_REACH = 64
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
#: 95% on one line, rmse 0.46).
#: An interior gap must be bounded over this fraction of the frame height,
#: with rows running on past at most one of its sides in at most ``GAP_CONT``
#: of the frame and agreeing on the inner line to ``GAP_AGREE``.
INTERIOR_ROWS = 0.4
GAP_CONT = 0.05
GAP_AGREE = 0.85
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
#: A plateau covering this much of the width is the whole frame: blank.
ALL_BASE_FRAC = 0.9
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


def _gate_columns(M: np.ndarray, s_own: np.ndarray) -> np.ndarray:
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
    if float(np.median(s_own[cand], axis=0).max()) > GATE_SMOOTH:
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


def frame_summary(image: np.ndarray) -> dict[str, Any]:
    """Everything about a frame that does not depend on a chosen base."""
    Ic, L = _prep(image)
    M = np.median(L, axis=0)                                  # (W, 3)
    s_vd = _own_noise(L)
    gate_cols = _gate_columns(M, s_vd)
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
    best = max(adm, key=lambda p: p["lum"]) if adm else None
    return {"Ic": Ic, "L": L, "M": M, "Mt": Mt, "sig_flat": sig_flat,
            "spread": spread, "g": g,
            "plateaus": plats,
            "gate_cols": gate_cols, "best": best, "use": use}


# --------------------------------------------------------------------------
# the roll: levels that recur confirm a base; they never veto one

# The roll's other frames arrive in ``ctx["roll"]`` as `roll.Summary` records,
# one per frame, each made once by `roll.summarise`. The study kept the same
# numbers in a module cache keyed by frame id; a window that stays open sees
# new prescans under old numbers, so here nothing is kept between calls.


def roll_levels(ctx: dict[str, Any]) -> list[np.ndarray]:
    """The base levels the roll's other frames found for themselves, same dtype only."""
    out = []
    for other in ctx.get("roll") or ():
        if other.chroma_level is not None and other.dtype == ctx.get("dtype"):
            out.append(other.chroma_level)
    return out


def roll_support(level: np.ndarray, ctx: dict[str, Any]) -> tuple[int, int]:
    """(frames whose own base level matches ``level``, frames compared)."""
    dtype = ctx.get("dtype")
    n = hit = 0
    for other in ctx.get("roll") or ():
        if other.chroma_level is None or other.dtype != dtype:
            continue
        n += 1
        if np.all(np.abs(other.chroma_level - level) <= ROLL_TOL):
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
    out = {"zd": zd, "zp2": zp2, "match": match, "f": match.mean(0), "f_loose": loose.mean(0)}
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
            cont[y] = True                                    # base goes on: scene at base level
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
        if abs(p1) > MAX_SLOPE:
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
    else:
        a = int(cand[0])
        e = a
        while e < W and base_cols[e]:
            e += 1
    while a > 0 and joins(a - 1, a):
        a -= 1
    while e < W and joins(e, e - 1):
        e += 1
    # The outermost column or two can read a little off (r0914_02: column 427
    # at 64 against 66, f 0.8). Base that begins one or two columns in, with
    # those columns still mostly base, touches the border; a neighbour's
    # picture there would read as picture in nearly every row.
    if 0 < a <= 2 and float(fl[:a].mean()) >= 0.4:
        a = 0
    width = e - a
    at_level = bool(base_cols[a:e].any())
    dbg.update(run=[a, e], at_level=at_level)
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
    enough = fit is not None and fit["frac_in"] >= MIN_FRAC_IN and (
        fit["n_in"] >= MIN_ROWS or (fit["n_in"] >= MIN_ROWS // 2 and fit["frac_in"] >= 0.85))
    if enough and a > 0:
        # An interior gap has picture on both sides of it; a gap that only a
        # few rows bound is a line of scene black (x0920_77's tile grid: 31 of
        # 276 rows). Real ones seen: 190-270 rows (ladder, r0911_17, r0914_24).
        enough = fit["n_in"] >= INTERIOR_ROWS * H
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
        # the neighbour's picture: where base begins, seen from the border side
        conf *= 0.8
        Bo = _local_base(Ic, a, e, inner=False)
        Irev = Ic[:, ::-1]
        zo = _zmaps(np.log(np.maximum(Irev, 1e-9)), np.log(Bo), sig)
        rows_out = _row_edges(Irev, zo["match"], Bo, sig, W - e, W - a)
        ofit = _fit_line(rows_out["xs"])
        dbg["cont_out"] = round(float(rows_out["cont"].mean()), 3)
        if ofit is not None and ofit["frac_in"] >= MIN_FRAC_IN:
            outer = round(float(W - ofit["x"]), 3)
        else:
            outer = float(a)
        dbg["outer_fit"] = ofit
        # A gap between frames is bounded, on at least one side, by a picture
        # edge in essentially every row. A line of scene black between tiles
        # runs on into the horizontal grid on both sides (x0920_73, r0914_17:
        # 9-13% of rows continue on each side, rows agreeing 72-86%); the real
        # gaps seen continue in 0-4% on one side, with 99-100% agreement.
        cont_out = float(rows_out["cont"].mean())
        if min(cont_in, cont_out) > GAP_CONT or fit["frac_in"] < GAP_AGREE:
            return Side(PICTURE_TO_BORDER, conf=round(0.4 * trust, 3),
                        note=f"base-level line [{a},{e}) is scene: rows run on "
                             f"{cont_in:.2f}/{cont_out:.2f}"), dbg
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
    fs = frame_summary(image)
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
        g_ref = float(np.median(fs["g"][ref_cols[0]:ref_cols[1]]))
    else:
        f_ref, g_ref = 1.0, 1.0
    z["fb"] = np.asarray(min(F_BASE, f_ref - 0.08))           # 0-d: passes the flip unchanged
    z["gb"] = np.asarray(min(G_FLAT, g_ref - 0.08))
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
