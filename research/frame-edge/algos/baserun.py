"""baserun: decide per pixel whether it is unexposed base, then ask the rows.

The idea, in the order the code does it, for one side (the right side is the
left side of the mirrored frame, so the answers mirror by construction):

1. **A base level that is the band's own, not the frame's maximum.** On C-41
   base is *not* the brightest thing in a frame: exposed areas lose the orange
   mask, and 1-7 % of picture pixels read brighter than base in some channel
   (measured by the coordinator on strip6_01, r0914_03, r0909_01). Uncorrected
   prescans add a shading bowl on top, so strip6_01's base sits at 0.83 of the
   frame's p99.5. A reference taken from a high percentile -- what iter01 did
   -- therefore missed real, dimmer gaps. The reference here is the brightest
   of the outermost `LEAD_COLS` + 1 columns, by its median down the frame
   (`_SideData`), and every pixel is judged against *that*.

2. **A per-pixel test with a tolerance from measured noise.** A pixel becomes
   one number, its *deficit* against the reference, averaged over R, G, B with
   inverse-variance weights so the noisy blue of a C-41 base does not dominate.
   Noise is measured on the frame from column-to-column differences among
   pixels near the reference level (`_noise_rel`). Rows are averaged in short
   vertical **blocks** first -- averaging *down* an edge costs no resolution
   across it, which is where `framing.edge_band` went wrong: it smoothed along
   the row and blurred the edge it was measuring. The test is **two-sided**:
   a pixel brighter than the reference by more than the tolerance is not the
   same stuff either, and that is what makes a textured border fail.

3. **Per block, how far base runs in from the border**, and whether the
   blocks agree (`_border_band`). A real gap is the same width in every row; a
   silhouette is not. Each block's edge is the sub-pixel base *area* over a
   short window (the 50 % crossing for a blurred step), and a straight line is
   fitted through them. Blocks with picture *inside* the line are rows with
   no gap where the gap would be -- base runs the full height, so more than a
   few of those rejects the band.

4. **What a band must also be.** Its level must be plausible for base: at
   least `PLAUSIBLE` of the frame's p99.5. That does not separate base from a
   dark wall -- measured, the r0911 walls carry exactly base's colour ratio and
   sit at 0.82-0.95 of p99.5 against 0.83-1.02 for real base -- it only
   removes the gross cases (a night sky at 0.68). The geometry is the test.

5. **When there is no band.** A border column that is not uniform down the
   frame is picture: `picture_to_border`, confidently. A uniform region that
   runs further than any gap can is either picture at the base level or base
   beside such picture with no visible edge; it is picture if it is clearly
   darker than base measured on the other side of the same frame, and refused
   otherwise. A full-height band further in, with picture beyond it, is the
   gap with the neighbour's picture showing (`_inner_band`, ``outer`` set).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, PRESCAN_COLUMNS, REFUSE, TRIM_ROWS,
    EdgeResult, Side,
)

# --------------------------------------------------------------------------
# constants

#: Rows dropped at the top and bottom on top of `TRIM_ROWS`: the prescan is
#: 24.3 mm tall against a 24 mm frame, so the first and last few rows can be
#: base across the whole width and would make every column look like a gap.
ROW_MARGIN = TRIM_ROWS + 3

#: The noise a block value should have, as a fraction of the base level. A
#: C-41 prescan's per-pixel noise in base is R 3.1-3.4 %, G 3.8-4.6 %,
#: B 5.5-6.7 % (r0914_23, r0914_01, r0909_01; the same along and across the
#: rows), about 2.5 % combined, so eight rows bring a block to about 1 %.
BLOCK_TARGET = 0.010
MIN_BLOCK_ROWS = 8
MAX_BLOCK_ROWS = 24

#: A block is base-like while its deficit is within K_SIGMA block sigmas plus
#: LEVEL_SYSTEMATIC of the reference. The systematic part is what the noise
#: does not cover: 8-row block means of known base scatter 0.6-1.5 % against
#: 0.3-0.7 % expected from pixel noise (the same bands) -- base drifting
#: 1.5-1.8 % from top to bottom of one frame (r0909_01, r0911_19).
K_SIGMA = 3.5
LEVEL_SYSTEMATIC = 0.025

#: A block noisier than this cannot tell base from picture a few percent away
#: from it, and a detector that cannot tell should not answer. Exposure
#: ladders at 0.4 counts of base (x0920) are the reason this exists.
MAX_BLOCK_SIGMA = 0.03

#: Base must be at least this share of the frame's p99.5 (weighted over the
#: channels). Measured on 21 known bands, per channel: 0.83 (strip6_01,
#: uncorrected) to 1.02; weighted as here, strip6_03's base reads 0.76 -- the
#: shading bowl of an uncorrected prescan. So this only removes the gross
#: cases, dense picture at the border; a night sky (0.68-0.78, bw0910_01) and
#: a dark wall pass it and are left to the geometry.
PLAUSIBLE = 0.65

#: Base is the least dense thing in *every* layer at once, so its level over
#: the frame's p99.5 is about the same in R, G and B; a coloured scene element
#: at a similar brightness is not. Spread of that ratio across the channels,
#: measured: real base 0.03-0.11 on 21 bands (per-channel exposure changes
#: cancel, it is a ratio to the same channel); the blue wall strip that
#: r0914_13/24/34 and x0920_55 share -- trousers run through it, so it is
#: exposed -- 0.45-0.70; the TV rack's edge on r0914_15 0.40. The r0911 dark
#: walls read 0.06-0.11: zero-exposure black is base-coloured and this test
#: leaves them to the geometry, as it must.
COLOUR_SPREAD = 0.2

#: A channel whose border level is at least this share of the file's full
#: scale is clipped, and left out of the deficit. This is the only use of the
#: dtype's range: it says where the rail is, never what a level means.
CLIP_SHARE = 0.95

#: Columns of a 428-wide prescan that a band touching the border may span:
#: the gap on 135 is about 2.6 mm (`framing.MAX_GAP_MM`), 30.7 columns at
#: 300 dpi, and a little more for tilt and blur.
MAX_BORDER_BAND = 34.0
#: How far in from each border the detector looks for a band with the
#: neighbour's picture beyond it. The stage9b ladder walks a gap to column 88.
ZONE = 120
#: Up to this many outermost columns may be defective in a block before the
#: band starts. r0914_16 and r0914_23 share a scanner artifact in the last one
#: to three columns of the same rows (blocks 17, 20, 23, 31 of both), which
#: otherwise ended the run at zero in 12-15 % of blocks.
LEAD_COLS = 3
#: A run ends at this many consecutive non-base columns, so dust and specks up
#: to two columns wide do not end a gap -- an edge is never that thin.
END_FAILS = 3
#: How many accepted columns the running base level is the median of.
ADAPT = 6
#: A block's own border level counts as possibly base within this deficit of
#: the reference: bw0910_11's gap darkens by up to 29 % in its bottom rows. A
#: night sky at the border sits 30 % and more below base (bw0910_01).
OWN_LEVEL = 0.30

#: A per-block edge agrees with the fitted line within this many columns. Real
#: gaps measured 2-4 px of per-row IQR at 600 dpi, so 1-2 columns at 300.
AGREE_COLS = 1.5
#: Of the blocks, at least this share must put the edge on the line (the
#: others may run past it: picture beside the gap at the base level) ...
MIN_SUPPORT = 0.5
#: ... or at least this share, with the conditions in `_band_passes`.
LOW_SUPPORT = 0.3
LOW_SUPPORT_BLOCKS = 8
#: ... and at most this share may have picture where the band should be.
MAX_SHORT = 0.12
#: The most an edge may lean over the frame height, in columns.
MAX_TILT = 4.0
#: A band narrower than this is reported as picture_to_border (see `_side`).
THIN_BAND = 1.0
#: The most the per-block edges may scatter about their line (std, columns).
#: Every band accepted on dev that looks right by eye is at 0.62 or under; the
#: TV rack's edge on r0914_15 scattered 1.53, r0914_29's right sliver 0.84.
MAX_EDGE_SPREAD = 0.75
#: Share of blocks with no base at the border that makes the side
#: picture_to_border. Base is full height, so a border that fails in a third
#: of its rows has no gap at it; dust and the edge artifact are tolerated
#: separately (`LEAD_COLS`, `END_FAILS`).
ZERO_PTB = 0.35

#: The empty gate is neutral and far brighter than film: 182 counts against a
#: C-41 base of (68, 30, 16) and a B&W base near 20. A plateau at a border at
#: least this factor brighter, in every channel, than the rest of the film ...
GATE_RATIO = 1.8
#: ... and at least this many columns wide. A one- or two-column base sliver
#: beside a dense frame passed the ratio alone (r0909_08, r0914_07, r0914_08).
GATE_MIN_COLS = 8
#: On C-41 the gate is neutral and base is orange (G/R 0.43-0.48 measured);
#: the plateau's smallest channel must be at least this share of its largest.
GATE_NEUTRAL = 0.7

#: A refused flat side is picture when it is this much darker than base
#: measured by an accepted band on the other side of the same frame: twice
#: the level systematic, since both levels carry it.
OTHER_SIDE_DROP = 2 * LEVEL_SYSTEMATIC

#: The roll: only bands this wide give a level (see `_bands_of`) ...
ROLL_BAND_COLS = 2.0
#: ... and it takes this many of them agreeing within ROLL_AGREE in every
#: channel. Accepted bands in one roll measured R 65-70 on r0914's corrected
#: frames and within 1 % across x0920's three; 5 % keeps a walk's bands and
#: drops a different kind of prescan (r0909: 56-57 against 67-70).
ROLL_MIN_BANDS = 3
ROLL_AGREE = 0.05
#: A refused side is picture when its border sits this far below the roll's
#: base in any one channel: twice `LEVEL_SYSTEMATIC`, as for the other side of
#: the same frame. The r0911 walls read 1-7 % below in blue.
ROLL_DROP = 2 * LEVEL_SYSTEMATIC
#: A frame whose p99.5 is more than this over the roll's base (mean over the
#: channels) is not of the walk's kind: corrected C-41 frames measure
#: 0.97-1.10, uncorrected prescans 1.18-1.37 (r0909_09, r0909_10, strip6).
ROLL_TOP_MAX = 1.15
#: A frame whose bright end has more relative noise than this is noise
#: throughout: the x0920 ladder frame at 0.4 counts of base. Both sides refuse.
MAX_TOP_NOISE = 0.15

#: Share of the frame's blocks that must match the border's level to call the
#: frame all base. 0.9 let r0911_22 through: a night frame whose sky is at the
#: base level everywhere but for a lit doorway.
ALL_BASE_SHARE = 0.97

#: The frame is *wider* than the 428-column aperture: 445 columns, from the
#: coordinator's measurement on the dev set (r0911 449.9, r0914 440.1; 45 dev
#: frames show no base on either side). So an edge can show on one side only,
#: and a band *inside* the zone is only a gap if the picture it bounds has this
#: width against an edge found on the other side. The black bar of a TV rack
#: on r0914_17 is full height, straight and at exactly the base level (68, 30,
#: 15 against the right-hand band's 68, 30, 16); only the 356 columns it would
#: leave the frame give it away.
FRAME_COLS = 445.0
#: How far the width may miss: cameras differ by a few tenths of a millimetre
#: and the edges each carry a column or so.
FRAME_TOL = 12.0


# --------------------------------------------------------------------------
# frame-level measurements


def _prepare(image: np.ndarray) -> np.ndarray:
    im = np.asarray(image, dtype=np.float64)
    if im.ndim == 2:
        im = np.repeat(im[..., None], 3, axis=2)
    im = im[..., :3]
    m = ROW_MARGIN if im.shape[0] > 4 * ROW_MARGIN else 0
    return im[m:im.shape[0] - m] if m else im


def _row_blocks(a: np.ndarray, n: int) -> np.ndarray:
    """Means of ``n`` consecutive rows; the remainder is split top and bottom."""
    k = a.shape[0] // n
    off = (a.shape[0] - k * n) // 2
    return a[off:off + k * n].reshape((k, n) + a.shape[1:]).mean(axis=1)


def _gate_columns(im: np.ndarray, film: str) -> tuple[np.ndarray, dict[str, Any]]:
    """Columns that are the empty gate: a bright plateau touching a border."""
    colmed = np.median(im, axis=0)                      # (W, 3)
    W = colmed.shape[0]
    y = colmed.sum(axis=1)
    gate = np.zeros(W, dtype=bool)
    info: dict[str, Any] = {}
    widest = max(3, int(round(GATE_MIN_COLS * W / PRESCAN_COLUMNS)))
    for side in ("left", "right"):
        yy = y if side == "left" else y[::-1]
        cm = colmed if side == "left" else colmed[::-1]
        if yy[0] < 0.85 * yy.max():
            continue
        run = 0
        while run < W and yy[run] >= 0.85 * yy[0]:
            run += 1
        if run < widest or run >= W - 3:
            continue
        film_top = np.percentile(cm[run + 3:], 99, axis=0)
        plateau = np.median(cm[:run], axis=0)
        ratio = plateau / np.maximum(film_top, 1e-9)
        neutral = float(plateau.min() / max(plateau.max(), 1e-9))
        info[side] = {"run": run, "ratio": np.round(ratio, 2).tolist(),
                      "neutral": round(neutral, 2)}
        if np.all(ratio >= GATE_RATIO) and (film != "c41" or neutral >= GATE_NEUTRAL):
            idx = np.arange(min(W, run + 3))   # the film's cut edge blurs a column or two
            gate[idx if side == "left" else W - 1 - idx] = True
    return gate, info


def _noise_rel(im: np.ndarray, level: np.ndarray) -> np.ndarray:
    """Per-channel pixel noise near ``level``, as a fraction of it.

    From column-to-column differences between pixels within 25 % of the level,
    as a trimmed standard deviation (a plain MAD of 8-bit differences is
    quantised to 1.05 or 2.1 counts). Along and across the rows measure the
    same in base (r0914_23: 3.2 % both ways in red), so one direction is enough.
    """
    lev = np.maximum(level, 1e-9)
    near = np.all(np.abs(im / lev - 1.0) <= 0.25, axis=-1)
    pair = near[:, 1:] & near[:, :-1]
    if pair.sum() < 300:
        pair = np.ones_like(pair)
    diff = im[:, 1:] - im[:, :-1]
    sig = np.empty(3)
    for c in range(3):
        d = diff[..., c][pair]
        mad = 1.4826 * np.median(np.abs(d)) + 0.5
        d = d[np.abs(d) <= 4.0 * mad]
        sig[c] = max(float(d.std()) / np.sqrt(2.0), 0.3) if d.size else 0.3
    return sig / lev


# --------------------------------------------------------------------------
# one side, with the border at column 0


def _colour_spread(level: np.ndarray, p995: np.ndarray) -> float:
    """How unequally a level sits below the frame's p99.5 across R, G, B.

    Clipped at 1 first: base above the picture's p99.5 in a channel says only
    that the picture never got that thin there. x0920_46, exposed with a
    different blue balance, has its base sliver at 1.05, 1.11, 1.32 of p99.5.
    """
    r = np.minimum(level / np.maximum(p995, 1e-9), 1.0)
    return float(r.max() - r.min())


class _SideData:
    """Everything one side is judged on, in the side's own coordinates."""

    def __init__(self, im: np.ndarray, zone: int, p995: np.ndarray, rail: float | None = None):
        self.H, self.W = im.shape[:2]
        self.im = im
        self.zone = zone
        self.p995 = p995
        # median of 8-row block means, not of pixels: a uint8 median is an
        # integer, and blue sits at 16 counts on a C-41 base -- 3 % a step
        colmed = np.median(_row_blocks(im[:, :LEAD_COLS + 1], 8), axis=0)
        wt = 1.0 / np.maximum(p995, 1e-9)
        self.ref_col = int(np.argmax((colmed * wt).sum(axis=1)))
        self.ref = colmed[self.ref_col]
        rel = _noise_rel(im[:, :zone + 8], self.ref)
        w = 1.0 / rel ** 2
        # A channel at the rail says nothing: base and near-base picture both
        # read the rail. x0920's exposure ladders put base at 55,000-65,000 of
        # 65,535 and widened a thin band from 0.9 to 2.0 columns through it.
        self.clipped = (np.zeros(3, dtype=bool) if rail is None
                        else self.ref >= CLIP_SHARE * rail)
        if self.clipped.any() and not self.clipped.all():
            w = np.where(self.clipped, 0.0, w)
        self.w = w / w.sum()
        self.sig_pix = float(np.sqrt((self.w ** 2 * rel ** 2).sum()))
        n = int(np.clip(np.ceil((self.sig_pix / BLOCK_TARGET) ** 2),
                        MIN_BLOCK_ROWS, MAX_BLOCK_ROWS))
        self.rows = max(1, min(n, self.H // 6))
        self.sig = self.sig_pix / np.sqrt(self.rows)
        self.tau = K_SIGMA * self.sig + LEVEL_SYSTEMATIC
        self.plaus = float((self.w * self.ref / np.maximum(p995, 1e-9)).sum())
        self.spread = _colour_spread(self.ref, p995)
        #: the level of the band this side accepted, if any
        self.band_level: np.ndarray | None = None

    def deficit(self, ref: np.ndarray, cols: int | None = None) -> np.ndarray:
        """Block means of the weighted deficit against ``ref``: (K, cols)."""
        a = self.im if cols is None else self.im[:, :cols]
        d = ((1.0 - a / np.maximum(ref, 1e-9)) * self.w).sum(axis=-1)
        return _row_blocks(d, self.rows)


def _runs(D: np.ndarray, tau: float, own: bool = False
          ) -> tuple[np.ndarray, np.ndarray]:
    """Per block, how many columns base runs in from the border, and the mask
    of which columns were judged base along the way.

    The run may start up to `LEAD_COLS` in (a defective outermost column counts
    as part of the band) and ends at `END_FAILS` consecutive failures. After
    the start each column is judged against the median of the last `ADAPT`
    accepted ones rather than the fixed reference, so that a slow drift across
    the band -- the stage9b ladder's gaps fall 6 % over 30 columns, the shading
    slope of an uncorrected prescan -- does not end it, while a step does.

    ``own`` starts each block at its own border level instead of the
    reference: the first of the outer two columns that its neighbour agrees
    with. That is for base that is not uniform down the frame -- bw0910_11's
    gap is 6-29 % darker in its bottom rows than its top, yet steps to picture
    at column 5 in every row. The runs then measure geometry only; whether the
    level could be base is the caller's question.
    """
    K, Z = D.shape
    out = np.zeros(K, dtype=int)
    ok = np.abs(D) <= tau
    for k in range(K):
        row = D[k]
        start = -1
        for x in range(min((2 if own else LEAD_COLS + 1), Z - 1)):
            if (abs(row[x] - row[x + 1]) <= tau) if own else (ok[k, x] and ok[k, x + 1]):
                start = x
                break
        if start < 0:
            continue
        hist = [row[start]]
        last, fails = start, 0
        for x in range(start, Z):
            local = float(np.median(hist[-ADAPT:]))
            good = abs(row[x] - local) <= tau
            ok[k, x] = good
            if good:
                hist.append(row[x])
                last, fails = x, 0
            else:
                fails += 1
                if fails >= END_FAILS:
                    break
        out[k] = last + 1
    return out, ok


def _area_edge(prof: np.ndarray, start: int, end: int, sig: float
               ) -> tuple[float, float] | None:
    """Sub-pixel boundary where base (a run ``prof[start:end]``) meets picture.

    The base level is the median of the run without its last two columns, the
    picture level the median of the five columns after it, and the boundary is
    the window start plus the base *area* in ``[end - 3, end + 4)`` -- exact for
    a partly covered pixel and the 50 % crossing for a symmetric blur. The
    window reaches back three columns because a blurred step ends the
    base-like run early: r0911_19's left edge ramps over columns 3 to 6.
    ``end == start`` means the border pixel itself is not base; the base level
    is then the reference. The picture may be brighter than base (C-41 mask
    loss), so the contrast is signed. Returns (boundary, contrast) or None.
    """
    Z = prof.shape[0]
    if end + 2 > Z:
        return None
    n = end - start
    if n >= 4:
        db = float(np.median(prof[max(start, end - 8):end - 2]))
    elif n > 0:
        db = float(np.median(prof[start:end]))
    else:
        db = 0.0
    dp = float(np.median(prof[end + 1:min(Z, end + 6)]))
    c = dp - db
    if abs(c) < 3.0 * sig:
        return None
    a = max(start, end - 3)
    b = min(Z, end + 4)
    t = np.clip((prof[a:b] - db) / c, 0.0, 1.0)
    return a + float(np.sum(1.0 - t)), c


def _fit_line(k: np.ndarray, e: np.ndarray, mid: float
              ) -> tuple[float, float] | None:
    """Robust straight line through (k, e); returns (value at ``mid``, slope)."""
    if e.size < 3:
        return None
    inl = np.abs(e - np.median(e)) <= 2.5
    if inl.sum() < 3:
        return None
    s, a0 = 0.0, float(e[inl].mean())
    for _ in range(4):
        kk, ee = k[inl], e[inl]
        kc = kk.mean()
        s = float(np.polyfit(kk - kc, ee, 1)[0]) if np.ptp(kk) > 0 else 0.0
        a0 = float(ee.mean()) - s * kc
        new = np.abs(e - (a0 + s * k)) <= AGREE_COLS
        if new.sum() < 3 or np.array_equal(new, inl):
            break
        inl = new
    return a0 + s * mid, s


def _border_band(D: np.ndarray, tau: float, sig: float, scale: float) -> dict[str, Any]:
    """A band of base touching the border: per-block edges and their agreement.

    Each block gives an edge twice: from a run judged against the reference
    (``e_ref``), and from a run started at the block's own border level
    (``e_own``, counted only while that level is within `OWN_LEVEL` of the
    reference). A candidate line is then judged block by block: *support* puts
    either edge on it; *long* runs past it -- a row where the picture beside
    the gap sits at the base level, which says nothing either way; *short* is
    everything else, a row with no band where the band should be, which base,
    running the full height, never is.

    The line chosen is the **first from the border** that enough blocks
    support and too few contradict, not the one most blocks agree on. On
    strip6_02 the gap's edge at column 11 shows in 13 of 33 blocks and in the
    rest a dark picture runs on to 28-40; a fit through all of them put the
    edge at 28.
    """
    K, Z = D.shape
    runs, ok = _runs(D, tau)
    rown, _ = _runs(D, tau, own=True)
    ks = np.arange(K, dtype=float)
    e_ref = np.full(K, np.nan)
    e_own = np.full(K, np.nan)
    own_ok = np.zeros(K, dtype=bool)
    for k in range(K):
        w = int(runs[k])
        if w >= Z - 4:
            e_ref[k] = np.inf
        else:
            got = _area_edge(D[k], 0, w, sig)
            if got is not None:
                e_ref[k] = got[0]
        w = int(rown[k])
        if w >= 3 and abs(float(np.median(D[k, :w]))) <= OWN_LEVEL:
            own_ok[k] = True
            if w >= Z - 4:
                e_own[k] = np.inf
            else:
                got = _area_edge(D[k], 0, w, sig)
                if got is not None:
                    e_own[k] = got[0]
    usable_own = np.isfinite(e_own) | np.isinf(e_own)
    out: dict[str, Any] = {"runs": runs,
                           "zero": float(np.mean(runs == 0)),
                           "long": float(np.mean(runs >= MAX_BORDER_BAND * scale))}
    allv = np.concatenate([e_ref[np.isfinite(e_ref)], e_own[np.isfinite(e_own)]])
    cands = [c for c in np.unique(np.round(allv)) if np.sum(np.abs(allv - c) <= 2.5) >= 3]
    best: dict[str, Any] | None = None
    mid = (K - 1) / 2.0
    for c in cands:
        near_r = np.isfinite(e_ref) & (np.abs(e_ref - c) <= 2.5)
        near_o = np.isfinite(e_own) & (np.abs(e_own - c) <= 2.5)
        pick = np.where(near_r & (~near_o | (np.abs(e_ref - c) <= np.abs(e_own - c))),
                        e_ref, e_own)
        sel = near_r | near_o
        if sel.sum() < 3:
            continue
        fit = _fit_line(ks[sel], pick[sel], mid)
        if fit is None:
            continue
        a, sl = fit
        line = a + sl * (ks - mid)
        support = ((np.isfinite(e_ref) & (np.abs(e_ref - line) <= AGREE_COLS))
                   | (np.isfinite(e_own) & (np.abs(e_own - line) <= AGREE_COLS)))
        # past the line: by the edge where there is one, else by the run
        # itself (a run into picture at the base level ends with no step)
        longer = ~support & ((e_ref > line + AGREE_COLS) | (usable_own & (e_own > line + AGREE_COLS))
                             | (runs > line + AGREE_COLS) | (own_ok & (rown > line + AGREE_COLS)))
        short = ~support & ~longer
        res = {"x": a, "slope": sl, "support": float(support.mean()),
               "short": float(short.mean()), "longer": float(longer.mean()),
               "x_top": float(line[0] - sl * 0.5), "x_bottom": float(line[-1] + sl * 0.5),
               "spread": float(np.std((pick - line)[support & sel])) if (support & sel).sum() > 1
               else 0.0, "n_support": int(support.sum())}
        if best is None or res["support"] - res["short"] > best["support"] - best["short"]:
            best = res
        if _band_passes(res, K) and a >= 0.5:
            out.update(res)
            out["first"] = True
            return out
    if best is not None:
        out.update(best)
    return out


def _band_passes(res: dict[str, Any], K: int) -> bool:
    """Whether a candidate line has enough rows behind it and too few against.

    Half the blocks on the line is the plain case. Fewer is allowed -- the
    picture beside a gap can sit at the base level in most rows (strip6_02:
    13 of 33 blocks show its edge, the rest run on) -- but only for a band
    wide enough not to be the border pixel's own mixture, backed by enough
    blocks, straight and upright. What that rules out was measured: a night
    sky gave "bands" of 1.7-3.2 columns on 4-5 blocks leaning 1.4-3.9 columns
    (bw0910_01, bw0910_05); r0914_16, r0914_27, x0920_07 and the ladder's
    edges gave 0.5-1.6 columns on 42-49 % of blocks.
    """
    if res["short"] > MAX_SHORT:
        return False
    if res["support"] >= MIN_SUPPORT:
        return True
    return (res["support"] >= LOW_SUPPORT and res["x"] >= 3.0
            and res["n_support"] >= LOW_SUPPORT_BLOCKS and res["short"] <= MAX_SHORT / 2
            and abs(res["slope"]) * K <= MAX_TILT / 2 and res["spread"] <= 0.6)


def _inner_band(sd: _SideData, scale: float) -> dict[str, Any] | None:
    """A full-height band of base inside the zone, picture on both sides of it.

    Candidate columns are uniform down the frame (their blocks within the
    tolerance of the column's own median) and plausibly bright. Each group of
    them is then judged against its own level: both of its edges are measured
    per block and must be straight, and it must be no wider than a gap.
    """
    Z = sd.zone
    sub = sd.im[:, :Z]
    colmed = np.median(sub, axis=0)
    wt = sd.w / np.maximum(sd.p995, 1e-9)
    bright = (colmed * wt).sum(axis=1) >= PLAUSIBLE
    blk = _row_blocks(sub, sd.rows)                        # (K, Z, 3)
    dev = ((1.0 - blk / np.maximum(colmed, 1e-9)) * sd.w).sum(axis=-1)
    uniform = (np.abs(dev) <= sd.tau).mean(axis=0) >= 0.85
    cand = bright & uniform
    cand[:2] = False
    widest = MAX_BORDER_BAND * scale
    x = 2
    while x < Z:
        if not cand[x]:
            x += 1
            continue
        g0 = x
        while x < Z and cand[x]:
            x += 1
        g1 = x
        if g1 >= Z - 4 or g1 - g0 > widest:
            continue
        ref = colmed[g0 + int(np.argmax((colmed[g0:g1] * wt).sum(axis=1)))]
        if _colour_spread(ref, sd.p995) > COLOUR_SPREAD:
            continue
        D = sd.deficit(ref, Z)
        K = D.shape[0]
        c0 = (g0 + g1) // 2
        fwd, _ = _runs(D[:, c0:], sd.tau)
        back, _ = _runs(D[:, :c0 + 1][:, ::-1], sd.tau)
        inner, outer = np.full(K, np.nan), np.full(K, np.nan)
        for k in range(K):
            got = _area_edge(D[k, c0:], 0, int(fwd[k]), sd.sig)
            if got is not None:
                inner[k] = c0 + got[0]
            got = _area_edge(D[k, :c0 + 1][::-1], 0, int(back[k]), sd.sig)
            if got is not None:
                outer[k] = c0 + 1 - got[0]
        ks = np.arange(K, dtype=float)
        mid = (K - 1) / 2.0
        res = {}
        for name, arr in (("x", inner), ("outer", outer)):
            fin = np.isfinite(arr)
            if fin.sum() < max(3, int(np.ceil(0.6 * K))):
                break
            fit = _fit_line(ks[fin], arr[fin], mid)
            if fit is None:
                break
            a, s = fit
            line = a + s * (ks - mid)
            sup = float((fin & (np.abs(arr - line) <= AGREE_COLS)).mean())
            res[name] = (a, s, sup, line)
        if len(res) < 2:
            continue
        a, s, sup, line = res["x"]
        width = a - res["outer"][0]
        if (sup >= 0.6 and res["outer"][2] >= 0.6 and 2.0 <= width <= widest
                and abs(s) * K <= MAX_TILT and res["outer"][0] >= 1.0):
            return {"x": a, "slope": s, "support": sup, "outer": res["outer"][0],
                    "x_top": float(line[0] - s * 0.5), "x_bottom": float(line[-1] + s * 0.5),
                    "width": width, "level": ref}
    return None


def _side(sd: _SideData, whole_ok: float) -> tuple[Side, dict[str, Any]]:
    """One side, border at column 0, in the side's own coordinates."""
    scale = sd.W / PRESCAN_COLUMNS
    dbg: dict[str, Any] = {"ref": np.round(sd.ref, 2).tolist(), "ref_col": sd.ref_col,
                           "plaus": round(sd.plaus, 3), "colour": round(sd.spread, 3),
                           "tau": round(sd.tau, 4),
                           "rows": sd.rows, "sig_pix": round(sd.sig_pix, 4)}
    if sd.sig > MAX_BLOCK_SIGMA:
        # A border far darker than the frame's p99.5 is picture however noisy
        # its own level is -- stage3_03's snow reads 0.25 -- once the frame's
        # bright end is itself above the noise (`detect` refuses otherwise).
        if sd.plaus < PLAUSIBLE:
            return Side(state=PICTURE_TO_BORDER, conf=0.6,
                        note=f"border too dark for base ({sd.plaus:.2f} of p99.5)"), dbg
        return Side(state=REFUSE, note=f"too noisy: {sd.sig:.1%} per block"), dbg
    D = sd.deficit(sd.ref, sd.zone)
    K = D.shape[0]
    band = _border_band(D, sd.tau, sd.sig, scale)
    dbg.update({k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in band.items() if k != "runs"})
    dbg["runs_q"] = np.percentile(band["runs"], [10, 50, 90]).round(1).tolist()

    # 1. a band at the border whose rows agree. Under `THIN_BAND` columns it
    # is reported as picture to the border: that is the border pixel's own
    # mixture, the same picture to an eye (the scoring treats it so), and a
    # sub-column "edge" moved by noise from one prescan of a frame to the next
    # (r0914_14 and r0914_25 read 0.5 and 0.6 six columns apart).
    thin_note = ""
    if "x" in band and band["x"] >= 0.5:
        x = band["x"]
        tilt = abs(band["slope"]) * K
        good = (_band_passes(band, K) and tilt <= MAX_TILT and x <= MAX_BORDER_BAND * scale
                and band["spread"] <= MAX_EDGE_SPREAD)
        dbg["band_ok"] = bool(good)
        # A thin or a coloured band is not a gap at the border, but the side
        # still gets the search for one further in (ladder9b_04's gap at 404
        # was lost behind a coloured 0.7-column sliver).
        if good and sd.spread > COLOUR_SPREAD:
            thin_note = f"a band, but coloured (spread {sd.spread:.2f}): picture"
        elif good and x < THIN_BAND:
            thin_note = f"under {THIN_BAND:.0f} column of base ({x:.2f})"
        else:
            thin_note = ""
        if good and not thin_note and sd.plaus >= PLAUSIBLE:
            sd.band_level = sd.ref
            half = max(0.5, 2.0 * band["spread"] / np.sqrt(max(band["n_support"], 1)) + 0.3)
            conf = float(np.clip(band["support"] * (1.0 - band["short"]), 0.0, 1.0))
            return Side(state=EDGE, x=x, lo=x - half, hi=x + half, conf=conf,
                        x_top=band["x_top"], x_bottom=band["x_bottom"],
                        note=f"band, {band['support']:.0%} of rows agree"), dbg

    # 2. the border is not uniform down the frame, or too dark to be base: no
    # gap at it. Maybe a band further in, with the neighbour's picture beyond.
    if band["zero"] >= ZERO_PTB or sd.plaus < PLAUSIBLE or thin_note:
        inner = _inner_band(sd, scale)
        if inner is not None:
            x = inner["x"]
            dbg["inner"] = {k: v for k, v in inner.items() if k != "level"}
            sd.band_level = inner["level"]
            return Side(state=EDGE, x=x, lo=x - 0.8, hi=x + 0.8, conf=0.6 * inner["support"],
                        outer=inner["outer"], x_top=inner["x_top"], x_bottom=inner["x_bottom"],
                        note=f"band inside, {inner['width']:.1f} wide"), dbg
        why = (thin_note or (f"border too dark for base ({sd.plaus:.2f} of p99.5)"
                             if sd.plaus < PLAUSIBLE
                             else f"border not uniform in {band['zero']:.0%} of rows"))
        return Side(state=PICTURE_TO_BORDER, conf=float(min(1.0, max(band["zero"], 0.5) / 0.6)),
                    note=why), dbg

    # 3. uniform all the way in, or rows that disagree: coloured is picture;
    # base-coloured is all base, or picture at the base level
    if sd.spread > COLOUR_SPREAD:
        return Side(state=PICTURE_TO_BORDER, conf=0.7,
                    note=f"border coloured (spread {sd.spread:.2f}): picture"), dbg
    if band["long"] >= 0.8:
        dbg["whole_base"] = round(whole_ok, 3)
        if whole_ok >= ALL_BASE_SHARE:
            return Side(state=ALL_BASE, conf=whole_ok, note="base across the frame"), dbg
        return Side(state=REFUSE, note="flat, base-like and wider than any gap"), dbg
    return Side(state=REFUSE, note="rows disagree about the band"), dbg


# --------------------------------------------------------------------------
# the entry point


def _flip(s: Side, W: int) -> Side:
    """A side measured on the mirrored frame, in the frame's own columns."""
    def f(v: float | None) -> float | None:
        return None if v is None else W - v
    return Side(state=s.state, x=f(s.x), lo=f(s.hi), hi=f(s.lo), conf=s.conf,
                outer=f(s.outer), x_top=f(s.x_top), x_bottom=f(s.x_bottom), note=s.note)


def _analyse(image: np.ndarray, ctx: dict
             ) -> tuple[dict[str, Side], dict[str, _SideData], dict[str, Any], np.ndarray]:
    """Both sides of one frame from the frame alone: sides, their data, debug, p99.5."""
    im = _prepare(image)
    W = im.shape[1]
    film = str(ctx.get("film_type") or "unknown")
    dt = str(ctx.get("dtype") or "")
    rail = {"uint8": 255.0, "uint16": 65535.0}.get(dt)
    gate, ginfo = _gate_columns(im, film)
    debug: dict[str, Any] = {"gate": ginfo}
    if gate.all():
        s = Side(state=NO_FILM, conf=0.5, note="nothing but the gate")
        return {"left": s, "right": Side(**s.__dict__)}, {}, debug, np.full(3, np.nan)
    p995 = np.percentile(im[:, ~gate].reshape(-1, 3), 99.5, axis=0)
    debug["p995"] = np.round(p995, 2).tolist()
    top_noise = _noise_rel(im[:, ~gate], p995)
    top_noise = float(1.0 / np.sqrt((1.0 / top_noise ** 2).sum()))
    debug["top_noise"] = round(top_noise, 4)
    if top_noise > MAX_TOP_NOISE:
        s = Side(state=REFUSE, note=f"the frame's bright end is noise ({top_noise:.1%})")
        return {"left": s, "right": Side(**s.__dict__)}, {}, debug, p995
    Z = int(min(round(ZONE * W / PRESCAN_COLUMNS), W // 2))

    sides: dict[str, Side] = {}
    data: dict[str, _SideData] = {}
    for name in ("left", "right"):
        view = im if name == "left" else im[:, ::-1]
        g = gate if name == "left" else gate[::-1]
        if g[0]:
            side = Side(state=NO_FILM, conf=0.9, note="empty gate at the border")
            dbg: dict[str, Any] = {}
        else:
            zone = int(np.argmax(g[:Z])) if g[:Z].any() else Z   # never into the gate
            sd = _SideData(view, max(zone, 8), p995, rail)
            data[name] = sd
            full = sd.deficit(sd.ref)[:, ~g]
            side, dbg = _side(sd, float(np.mean(np.abs(full) <= sd.tau)))
        if name == "right":
            side = _flip(side, W)
        sides[name] = side
        debug[name] = dbg

    # Two bands in one frame are the same film base under the same pass, so
    # their levels must agree. The dimmer one, if clearly dimmer and wide
    # enough for its level to be its own (a band under two columns reads the
    # picture beside it too), is picture with a straight edge: ladder9b_21's
    # dark floor at 0.69 of p99.5 took a 4-column "band" that then vetoed the
    # real gap on the other side.
    if (sides["left"].state == EDGE and sides["right"].state == EDGE
            and all(n in data and data[n].band_level is not None for n in ("left", "right"))):
        la, ra = data["left"].band_level, data["right"].band_level
        wb = data["left"].w + data["right"].w      # the same weights either way round
        ratio = float((wb * la).sum() / max(float((wb * ra).sum()), 1e-9))
        debug["band_ratio_lr"] = round(ratio, 4)
        dim = "left" if ratio < 1.0 else "right"
        drop = 1.0 - (ratio if ratio < 1.0 else 1.0 / ratio)
        ds = sides[dim]
        wide = ds.x is not None and (ds.x if dim == "left" else W - ds.x) >= 2.0 or ds.outer is not None
        if drop > OTHER_SIDE_DROP and wide:
            sides[dim] = Side(state=PICTURE_TO_BORDER, conf=0.5,
                              note=f"band {drop:.1%} dimmer than the other side's base")

    # An inner band must leave the frame its width against a border band on
    # the other side; otherwise the border evidence stands (it was picture).
    fw = FRAME_COLS * W / PRESCAN_COLUMNS
    for name, other in (("left", "right"), ("right", "left")):
        s, o = sides[name], sides[other]
        if (s.state == EDGE and s.outer is not None and o.state == EDGE and o.outer is None
                and s.x is not None and o.x is not None):
            width = abs(s.x - o.x)
            if abs(width - fw) > FRAME_TOL * W / PRESCAN_COLUMNS:
                sides[name] = Side(state=PICTURE_TO_BORDER, conf=0.6,
                                   note=f"inner band would leave a {width:.0f}-column frame")

    # A flat, refused side against a band on the other side of the same frame:
    # the same film, the same exposure, the same pass.
    for name, other in (("left", "right"), ("right", "left")):
        s, o = sides[name], sides[other]
        if (s.state == REFUSE and s.note.startswith("flat") and o.state == EDGE
                and name in data and other in data and data[other].band_level is not None):
            mine, theirs = data[name], data[other]
            drop = float((mine.w * (1.0 - mine.ref / np.maximum(theirs.band_level, 1e-9))).sum())
            debug[name]["drop_vs_other"] = round(drop, 4)
            if drop > OTHER_SIDE_DROP:
                sides[name] = Side(state=PICTURE_TO_BORDER, conf=0.6,
                                   note=f"flat, {drop:.1%} darker than the other side's base")
    return sides, data, debug, p995


# --------------------------------------------------------------------------
# the roll

_BANDS: dict[str, tuple[str, list[np.ndarray]]] = {}


def _bands_of(fid: str) -> tuple[str, list[np.ndarray]]:
    """The levels of the border bands one frame of the roll accepted, cached.

    Only bands at least `ROLL_BAND_COLS` wide: a narrower one's level is mixed
    with the picture beside it (r0911_17's 1.5-column band reads 55 counts of
    red against 66-68 for the roll's wider ones).
    """
    if fid not in _BANDS:
        from common import load
        fr = load(fid)
        dt = str(fr.meta.get("dtype") or "")
        sides, data, _, _ = _analyse(fr.image, {"film_type": fr.meta.get("film_type"),
                                                "dtype": dt})
        W = fr.image.shape[1]
        levels = []
        for name in ("left", "right"):
            s = sides[name]
            if (s.state == EDGE and s.outer is None and s.x is not None and name in data
                    and data[name].band_level is not None
                    and (s.x if name == "left" else W - s.x) >= ROLL_BAND_COLS):
                levels.append(np.asarray(data[name].band_level, dtype=np.float64))
        _BANDS[fid] = (dt, levels)
    return _BANDS[fid]


def _roll_base(ctx: dict) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Base measured on the other frames of the walk, when they agree.

    Pooled from every accepted band of the same file type, then the cluster
    around their median: at least `ROLL_MIN_BANDS` within `ROLL_AGREE` of it in
    every channel. A dataset roll can mix exposures and corrected with
    uncorrected prescans (r0909: bands at 67-70 and at 56-57 counts of red);
    a real walk does not, and the cluster is what a walk would give.
    """
    ids = [i for i in (ctx.get("roll_ids") or []) if i != ctx.get("id")]
    dt = str(ctx.get("dtype") or "")
    levels = []
    for fid in ids:
        try:
            fdt, lv = _bands_of(fid)
        except Exception:        # a frame that cannot be loaded is not evidence
            continue
        if fdt == dt:
            levels.extend(lv)
    info: dict[str, Any] = {"bands": len(levels)}
    if len(levels) < ROLL_MIN_BANDS:
        return None, info
    arr = np.array(levels)
    med = np.median(arr, axis=0)
    keep = np.all(np.abs(arr / np.maximum(med, 1e-9) - 1.0) <= ROLL_AGREE, axis=1)
    info["agree"] = int(keep.sum())
    if keep.sum() < ROLL_MIN_BANDS:
        return None, info
    base = arr[keep].mean(axis=0)
    info["base"] = np.round(base, 2).tolist()
    return base, info


def detect(image: np.ndarray, ctx: dict) -> EdgeResult:
    sides, data, debug, p995 = _analyse(image, ctx)
    # The roll only ever turns a refusal into picture_to_border: a side that is
    # flat, or whose rows disagree, and is clearly darker than base measured
    # on the rest of the walk is picture at the border. It never makes a band.
    refused = [n for n in ("left", "right") if sides[n].state == REFUSE and n in data
               and (sides[n].note.startswith("flat") or sides[n].note.startswith("rows"))]
    if not refused or not ctx.get("roll_ids"):
        return EdgeResult(sides["left"], sides["right"], debug)
    base, info = _roll_base(ctx)
    debug["roll"] = info
    if base is None:
        return EdgeResult(sides["left"], sides["right"], debug)
    # Is this frame of the walk's kind? Its own bands must match the roll's,
    # and its bright end must not sit far above base -- the centre of an
    # uncorrected prescan does, by its shading bowl (r0911_01: 0.72 at the
    # borders against 1.0 in the middle).
    for n in ("left", "right"):
        if n in data and data[n].band_level is not None and sides[n].state == EDGE:
            if np.any(np.abs(data[n].band_level / base - 1.0) > ROLL_AGREE):
                debug["roll"]["skip"] = f"{n} band disagrees with the roll"
                return EdgeResult(sides["left"], sides["right"], debug)
    if float(np.mean(p995 / base)) > ROLL_TOP_MAX:
        debug["roll"]["skip"] = f"p99.5 at {float(np.mean(p995 / base)):.2f} of the roll's base"
        return EdgeResult(sides["left"], sides["right"], debug)
    for n in refused:
        drop = 1.0 - data[n].ref / base
        debug[n]["roll_drop"] = np.round(drop, 4).tolist()
        if float(drop.max()) > ROLL_DROP:
            sides[n] = Side(state=PICTURE_TO_BORDER, conf=0.5,
                            note=f"{float(drop.max()):.1%} below the roll's base in a channel")
    return EdgeResult(sides["left"], sides["right"], debug)
