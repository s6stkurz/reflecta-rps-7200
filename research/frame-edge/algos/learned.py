"""The learned family: a per-pixel base/not-base classifier, then per-row transitions.

    uv run --no-sync python run.py learned iter04            # from research/frame-edge/
    uv run --no-sync python algos/learned_train.py           # LORO table, then re-fit weights

The idea
--------

Every earlier detector was a hand-set rule on a level, a flatness or a band
average, and each was confidently wrong somewhere because a dark scene area is
nearly as clear as base. This one lets the dev labels pick the combination: a
tiny two-layer network (23 -> 12 tanh -> 1, numpy, hand-written Adam in
`learned_train.py`) scores every **pixel** as base or not, from features that
carry no scale -- and only then is anything aggregated, one row at a time:

1. **Per frame, a reference level**: the median of the frame's three brightest
   full-height flat columns, per channel. The frames of one roll do not share
   a scale (exposure ladders, uint16 scans beside uint8 prescans), so every
   feature is relative to *this frame*: never a dtype maximum, never a roll
   level. The reference is an estimate and misses (strip6's picture is
   brighter than its base in places; a one-column base is outvoted), so the
   network is trained against jittered references too (`learned_train.py`).
2. **The empty gate is taken out first**, by rule: neutral, and at least twice
   the film's level in every channel. Two dev frames show it -- too few to
   learn from.
3. **Features per pixel** (`FEATURES`): log level against the reference, the
   same averaged down 7 rows (base is vertical, so this sharpens it without
   moving the edge), colour against the reference's, the step along the row
   inward and outward, the column's full-height statistics, and how far
   base-like pixels continue inward (base is a band ~15 columns wide; a wall
   at the base level is not). **Nothing that knows where the border is**:
   distance and connectivity features were tried and each drew straight lines
   of its own -- see `features`. The right side is the left side of the
   mirrored frame: one code path, and a mirrored frame gets the mirrored
   answer by construction (mirror state changes: 0 in every iteration).
4. **Bands, then rows.** Columns that most rows call base form bands; for
   each band starting within `SEARCH_COLS` of the border, each row's base run
   through it, and where that run ends.
5. **Each row's end is checked against the image**: a step down of `STEP_Z`
   noise sigmas into the picture beyond. The network's confidence can fade at
   the same column in every row of a frame whose walls sit at the base level;
   that is a straight line drawn by the model, and it has no step under it.
6. **Full-height agreement** -- Stefan's "sharp line, completely black from
   top to bottom". An edge needs `AGREE_MIN` of the rows either on one
   straight (possibly tilted) line with a step, or base straight through it
   (the picture beyond is at base level in that row), and `LINE_MIN` on the
   line itself. A band that does not touch the border must also step up from
   the neighbour's picture and be no wider than a gap.
7. **No band**: rows with no stepped base run over most of the height say
   `picture_to_border`, confidently -- unless the border column is brighter
   than the next in every channel row after row, which is a **sliver** of
   base inside that one column (`SLIVER_ROWS`). Anything between is `refuse`.
8. **Sub-column position**: the per-row 50% crossing between the base level
   and the picture beside it, on the channel with the most contrast, median
   over the agreeing rows -- the definition the ground truth snaps to.

What it does not do, and why (details at the constants): it does not find a
gap more than ~48 columns in (r0914_20, the ladder's last frames) -- the museum
walls of r0911 are indistinguishable from a gap there; it refuses B&W base
next to a night sky that is 1 count away (bw0910_01/04/05); and it refuses
base beside picture at the base level in most rows (r0914_18, strip6_02).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from common import (
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, TRIM_ROWS, EdgeResult, Side,
)

WEIGHTS = Path(__file__).with_name("learned_weights.json")

# --------------------------------------------------------------------------
# reference level and the empty gate

#: A column's pixels count as "at its own level" within this relative
#: deviation (all three channels). Base at 8-bit reads ~(68, 30, 16) counts, so
#: +-1 count of quantisation on blue alone is 6%; 12% leaves room for that and
#: for the sensor noise without admitting textured picture.
LEVEL_TOL = 0.12
#: A column is "full-height flat" when this fraction of its rows is at its own
#: level. Base columns measured 0.9+ on 8-bit prescans; 0.7 keeps a column with
#: a scratch or two.
FLAT_FRACTION = 0.7
#: How many of the brightest flat columns the reference is the median of.
#: Three, so one hot column (a stripe, a pinhole) cannot set it -- but a base
#: sliver of one or two columns still pulls it most of the way.
REF_COLUMNS = 3
#: The empty gate is neutral: max/min over channels measured 1.008-1.02 on the
#: two dev frames that show it (~183 counts in all three). Near-neutral *base*
#: occurs on uint16 scans exposed per channel -- 1.22 (r0914_29), 1.24
#: (x0920_11) -- so the line sits well below those.
GATE_NEUTRAL = 1.08
#: ... and brighter than any film in the frame, in every channel: 183 against
#: base (68, 30, 16) is 2.7x on red, the smallest of the three.
GATE_RATIO = 2.0

# --------------------------------------------------------------------------
# features

#: Rows averaged down a column for the smoothed features. Base is vertical,
#: so this sharpens the base/picture contrast without moving the edge; 7 rows
#: cuts pixel noise by 2.6x.
V_SMOOTH = 7
#: Deviation from the reference, in log units, that still counts as "at base"
#: for the band-width features: ~10%, the same argument as LEVEL_TOL. A
#: tolerance in noise units instead (3 sigma of the smoothed level, 2% floor:
#: ~3% on 8-bit red) was tried to separate r0911_07's base from the wall 6%
#: under it; held out it lost strip6 entirely and fixed nothing (LORO 170/193
#: against 175).
RUN_TOL = 0.10
#: Base-like runs are counted up to this many columns inward, and one that
#: goes FAR or further is wider than any gap: the widest labelled base on dev
#: is 18 columns, the gap between frames ~15.
RUN_CAP = 64
FAR = 40
#: floor for log(): 1% of the reference, so a zero-count pixel on a
#: dense frame is a large but finite negative number.
FLOOR = 0.01

FEATURES = (
    "L_r", "L_g", "L_b",                 # log(v / ref) per channel, this pixel
    "Lv_r", "Lv_g", "Lv_b",              # the same, averaged over V_SMOOTH rows
    "Lv_min", "Lv_max",                  # darkest / brightest channel of it
    "step_in1", "step_in3",              # lum(x+1) - lum(x), lum(x+3) - lum(x): inward
    "step_out1", "step_out3",            # lum(x) - lum(x-1), lum(x) - lum(x-3): outward
    "col_r", "col_g", "col_b",           # the column's median of L over rows
    "col_p10",                           # 10th percentile over rows of min_c L
    "col_frac",                          # fraction of the column's rows at base
    "ref_cred",                          # mean_c log(ref / p99.5): is ref the top?
    "ch_rg", "ch_bg",                    # Lv_r - Lv_g, Lv_b - Lv_g: colour against ref's
    "run_in",                            # how far base-like continues inward from here
    "col_far",                           # fraction of the column's rows where it goes far
    "frame_at_base",                     # fraction of the whole frame at the ref level
)

# --------------------------------------------------------------------------
# from pixels to a side

#: A base band is looked for starting within this many columns of the
#: border: a sliver of the neighbour's picture can come first. The gap is ~15
#: columns; a neighbour showing over 48 means the frame is ~60 columns off.
#: r0914_20 is (its gap at columns 86-103) and is missed. Searching to 100
#: finds it, and in leave-one-roll-out costs 13 sides: the museum walls of
#: r0911 sit within 2-3% of base in every channel, full height, between two
#: straight vertical lines -- a gap by every local measure and by the roll's
#: base level too -- and x0920_73 grows a false gap at column 70.
SEARCH_COLS = 48
#: A column seeds a band when this fraction of its rows is called base.
BAND_SUPPORT = 0.5
#: A band that does not touch the border is the gap between two pictures,
#: so it can be no wider than a gap: ~15 columns on the dev rolls (pitch
#: against frame width), 18 the widest labelled. Twice that.
MAX_GAP = 36
#: ... and its outer side must step *up* from the neighbour's picture in at
#: least this fraction of its rows (a gap has two sharp sides).
OUTER_STEP_MIN = 0.6
#: ... and on its inner side the picture must stay below base over this many
#: columns, not just the three every edge is checked over.
INNER_WIDE = 8
#: Rows with a base run starting this close to the border count as "base at
#: the border" when deciding picture_to_border: the same reach.
OUTER_MAX = SEARCH_COLS
#: Rows whose run end sits within this many columns of the fitted line agree.
#: The ground truth's per-row crossings scatter ~0.5 column on a clean edge;
#: the integer run ends add +-1.
ROW_TOL = 1.5
#: Fraction of rows that must agree with an edge (show the line, or base
#: straight through it), and fraction with no base at the border for
#: picture_to_border. Stefan's test is *full height*; 0.8 leaves
#: room for a scratch, a dust speck and the odd noisy row.
AGREE_MIN = 0.80
#: ... of which at least this fraction must show the line itself (a step at
#: it); the rest may be base straight through it. 0.6: r0914_17's base meets
#: tiles in 70% of its rows and grid lines at base level in the rest.
LINE_MIN = 0.60
#: Tilt allowed, columns over the used height: the transport can skew a strip
#: a little; more than this is not a frame edge.
MAX_TILT = 4.0
#: Probability above which a pixel is called base.
P_BASE = 0.5
#: A row's run must end at a step down of at least this many noise sigmas
#: (of the 7-row smoothed log level, largest channel). 5 sigma: a real
#: base/picture step on the dev edges is tens of sigma; noise alone gives
#: 5 sigma in well under one row per frame.
STEP_Z = 5.0
#: A **sliver**: base covering part of the border column only, which no pixel
#: classifier can call base (the pixel is mostly picture). It shows as the
#: border column brighter than the next in *every* channel, row after row.
#: Measured on dev: the seven labelled slivers of 0.15-0.75 columns have that
#: in 71-100% of rows; of 133 sides labelled picture_to_border, 132 have it in
#: at most 61%, the median 16% -- one (x0920_19 right) reaches 92%, and is the
#: one false sliver. It matters for the move, not the side score: with the
#: frame 12 columns wider than the aperture, any base at all puts the frame
#: 6+ columns off, so "border" on a 0.2-column sliver is a 5-unit error.
SLIVER_ROWS = 0.70


@lru_cache(maxsize=1)
def _weights() -> dict[str, Any]:
    return json.loads(WEIGHTS.read_text(encoding="utf-8"))


def _rgb(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 3 and img.shape[2] > 3:
        img = img[..., :3]
    return img[TRIM_ROWS:img.shape[0] - TRIM_ROWS]


def column_levels(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per column: the median over rows (w, 3), and the fraction of rows at it."""
    med = np.median(img, axis=0)
    rel = np.abs(img - med[None]) / np.maximum(med[None], 1e-9)
    at = np.all(rel < LEVEL_TOL, axis=2).mean(axis=0)
    return med, at


def gate_columns(med: np.ndarray, film_type: str) -> tuple[np.ndarray, str]:
    """Columns that are the empty gate: neutral, and brighter than any film.

    Returns the mask and why. When every column is neutral and alike there is
    no film to compare against; on C-41 that is the gate (base is orange), on
    anything else it could be a blank neutral frame and nothing is masked.
    """
    w = med.shape[0]
    lo = np.maximum(med.min(axis=1), 1e-9)
    neutral = med.max(axis=1) / lo < GATE_NEUTRAL
    if not neutral.any():
        return np.zeros(w, bool), "no neutral column"
    top = med[neutral].mean(axis=1).max()
    cand = neutral & (med.mean(axis=1) >= 0.8 * top)
    rest = ~cand
    if not rest.any():
        if film_type == "c41":
            return cand, "every column neutral on C-41"
        return np.zeros(w, bool), "every column neutral, film not C-41: undecided"
    # the film's top level: a high percentile, not the max -- the column where
    # the film ends is part gate and part film (r0914_21/22: 145 counts)
    film_top = np.percentile(med[rest], 90, axis=0)
    gate_level = np.median(med[cand], axis=0)
    if np.all(gate_level >= GATE_RATIO * film_top):
        # the transition column belongs to the gate too; base next to it
        # (orange, a quarter of the level) does not
        part = (med.max(axis=1) / lo < 1.3) & (med.mean(axis=1) >= 0.5 * gate_level.mean())
        return cand | part, "neutral and brighter than all film"
    return np.zeros(w, bool), "neutral columns are not brighter than the film"


def reference(img: np.ndarray, med: np.ndarray, at: np.ndarray,
              usable: np.ndarray) -> np.ndarray:
    """(3,) the frame's own base estimate: brightest full-height flat columns."""
    cand = usable & (at >= FLAT_FRACTION)
    if cand.sum() < REF_COLUMNS:
        cand = usable.copy()
    if not cand.any():
        cand = np.ones_like(usable)
    idx = np.flatnonzero(cand)
    g = np.log(np.maximum(med[idx], 1e-9)).mean(axis=1)
    top = idx[np.argsort(-g)[:REF_COLUMNS]]
    return np.maximum(np.median(med[top], axis=0), 1e-9)


def _vmean(a: np.ndarray, k: int) -> np.ndarray:
    """Running mean down axis 0 over k rows, edge-padded."""
    pad = k // 2
    p = np.concatenate([np.repeat(a[:1], pad, 0), a, np.repeat(a[-1:], pad, 0)], 0)
    c = np.cumsum(p, axis=0, dtype=np.float64)
    c = np.concatenate([np.zeros_like(c[:1]), c], 0)
    return (c[k:] - c[:-k]) / k


def _shift(a: np.ndarray, s: int) -> np.ndarray:
    """a[:, x + s] with edge padding (s may be negative)."""
    w = a.shape[1]
    idx = np.clip(np.arange(w) + s, 0, w - 1)
    return a[:, idx]


def smoothed_noise(L: np.ndarray, Lv: np.ndarray) -> np.ndarray:
    """(3,) noise of the V_SMOOTH-row mean of log level, per channel.

    From row-to-row differences, over pixels near the reference level (the
    whole frame if too few): the noise where it matters, at base.
    """
    flat = np.all(np.abs(Lv) < RUN_TOL, axis=2)
    d = np.abs(np.diff(L, axis=0))
    pick = flat[1:] & flat[:-1]
    if pick.sum() < 200:
        pick = np.ones_like(pick)
    sigma = 1.4826 * np.median(d[pick], axis=0) / np.sqrt(2.0) / np.sqrt(V_SMOOTH)
    return np.maximum(sigma, 1e-3)


def features(img: np.ndarray, ref: np.ndarray, gate: np.ndarray) -> np.ndarray:
    """(h, w, F) features for base at the LEFT border (mirror the frame for the right)."""
    h, w, _ = img.shape
    L = np.log(np.maximum(img, FLOOR * ref) / ref)
    Lv = _vmean(L, V_SMOOTH)
    lum = Lv.mean(axis=2)
    col = np.median(L, axis=0)                           # (w, 3)
    col_p10 = np.percentile(L.min(axis=2), 10, axis=0)   # (w,)
    at_base = np.all(np.abs(Lv) < RUN_TOL, axis=2) & ~gate[None]
    col_frac = at_base.mean(axis=0)
    x = np.arange(w, dtype=np.float64)
    p995 = np.maximum(np.percentile(img[:, ~gate] if (~gate).any() else img,
                                    99.5, axis=(0, 1)), 1e-9)
    cred = float(np.mean(np.log(ref / p995)))
    F = np.empty((h, w, len(FEATURES)), dtype=np.float32)
    F[..., 0:3] = L
    F[..., 3:6] = Lv
    F[..., 6] = Lv.min(axis=2)
    F[..., 7] = Lv.max(axis=2)
    F[..., 8] = _shift(lum, 1) - lum
    F[..., 9] = _shift(lum, 3) - lum
    F[..., 10] = lum - _shift(lum, -1)
    F[..., 11] = lum - _shift(lum, -3)
    F[..., 12:15] = col[None]
    F[..., 15] = col_p10[None]
    F[..., 16] = col_frac[None]
    # Nothing that knows where the border is. Three distance features
    # (exp(-x/k), k = 2, 8, 32) were in the first fit and taught the network
    # a *width*: on frames whose picture sits at the base level it called
    # base out to a fixed column in every row -- a straight line drawn by the
    # model, not the film. Two connectivity features (the least column
    # support, and the share of the row at base, between the border and
    # here) were in the second; dropping them left the leave-one-roll-out
    # score where it was (175/193 both ways) and let the gap be found behind a
    # sliver of the neighbour's picture: on the stage9b ladder, frames 12-14
    # and 18 went from refusals and misses to 30.3, 37.0, 43.9 and 73.0 --
    # 7 columns a step, as the ladder moves. *Where* a band may start is the
    # band logic's business (SEARCH_COLS), not the network's.
    F[..., 17] = cred
    # Colour against the reference's. On C-41 prescans the base's channel
    # ratios are stable where its level is not (R/G 2.08-2.30, B/G 0.53-0.54
    # over three rolls, while R reads 54 on strip6 and 66-69 elsewhere). The
    # *absolute* ratios were tried and dropped: scans exposed per channel
    # (uint16) move them, and B&W base is neutral -- held out, bw0910 lost
    # half its base pixels to them (iter01 LORO, base recall 0.49).
    F[..., 18] = Lv[..., 0] - Lv[..., 1]
    F[..., 19] = Lv[..., 2] - Lv[..., 1]
    # Width. Base is a band: the gap between frames is ~15 columns on the
    # dev rolls, and the widest labelled base is 18. A dark wall at the base
    # level (r0911_06: within 2 counts of base in all three channels) is
    # base-like for a hundred columns. So: how far does base-like go inward?
    nxt = np.where(~at_base, x[None], float(w))
    nxt = np.minimum.accumulate(nxt[:, ::-1], axis=1)[:, ::-1]
    run_in = np.minimum(nxt - x[None], RUN_CAP)
    F[..., 20] = np.log1p(run_in) / np.log1p(RUN_CAP)
    F[..., 21] = (run_in >= FAR).mean(axis=0)[None]
    F[..., 22] = float(at_base.mean())
    return F


def prepare(image: np.ndarray, film_type: str) -> dict[str, Any]:
    """Everything per frame that does not depend on the side."""
    img = _rgb(image)
    med, at = column_levels(img)
    gate, gate_why = gate_columns(med, film_type)
    ref = reference(img, med, at, ~gate)
    return {"img": img, "med": med, "at": at, "gate": gate, "gate_why": gate_why,
            "ref": ref}


def side_views(prep: dict[str, Any]) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """(side, image with that side's border at column 0, gate mask) for both sides."""
    img, gate = prep["img"], prep["gate"]
    return [("left", img, gate),
            ("right", np.ascontiguousarray(img[:, ::-1]), gate[::-1].copy())]


def mlp_logit(F: np.ndarray, wts: dict[str, Any]) -> np.ndarray:
    """The network's log-odds of base, for features (..., F)."""
    shape = F.shape[:-1]
    use = [FEATURES.index(n) for n in wts.get("use", FEATURES)]
    X = (F.reshape(-1, F.shape[-1])[:, use].astype(np.float64) - np.asarray(wts["mu"])) \
        / np.asarray(wts["sd"])
    W1, b1 = np.asarray(wts["W1"]), np.asarray(wts["b1"])
    W2, b2 = np.asarray(wts["W2"]), float(wts["b2"])
    hid = X @ W1 + b1
    if not wts.get("linear"):
        hid = np.tanh(hid)
    return (hid @ W2 + b2).reshape(shape)


# --------------------------------------------------------------------------
# per row, then full height


def run_bounds(base: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel: first column of the run it is in, and one past its last."""
    h, w = base.shape
    idx = np.broadcast_to(np.arange(w), (h, w))
    prev_false = np.maximum.accumulate(np.where(~base, idx, -1), axis=1)
    nxt = np.where(~base, idx, w)[:, ::-1]
    next_false = np.minimum.accumulate(nxt, axis=1)[:, ::-1]
    return prev_false + 1, next_false


def row_runs(base: np.ndarray, gate: np.ndarray, lo: int = 0, hi: int = OUTER_MAX
             ) -> tuple[np.ndarray, np.ndarray]:
    """Per row: start and end of the base run through the first base column in [lo, hi).

    ``base`` (h, w) bool. Rows with none get start = end = -1.
    """
    h, w = base.shape
    b = base & ~gate[None]
    starts = np.full(h, -1)
    ends = np.full(h, -1)
    seg = b[:, lo:min(hi, w)]
    if seg.shape[1] == 0:
        return starts, ends
    has = seg.any(axis=1)
    c0 = lo + np.argmax(seg, axis=1)
    first, stop = run_bounds(b)
    r = np.flatnonzero(has)
    starts[r] = first[r, c0[r]]
    ends[r] = stop[r, c0[r]]
    return starts, ends


def bands(base: np.ndarray, gate: np.ndarray) -> list[tuple[int, int, float]]:
    """Column intervals [a, b) where most rows are called base, nearest the border first.

    Only bands starting within ``SEARCH_COLS`` of the border; each with its
    peak column support.
    """
    h, w = base.shape
    sup = (base & ~gate[None]).mean(axis=0)
    on = sup >= BAND_SUPPORT
    out = []
    c = 0
    while c < w:
        if on[c]:
            e = c
            while e < w and on[e]:
                e += 1
            if c < SEARCH_COLS:
                out.append((c, e, float(sup[c:e].max())))
            c = e
        else:
            c += 1
    return out


def crossing(img: np.ndarray, e_rows: np.ndarray, rows: np.ndarray) -> tuple[float | None, int]:
    """Median over ``rows`` of the per-row 50% crossing near each row's integer edge.

    Left-border coordinates. The base level is the median of up to five
    columns inside the base run, the picture level the median of six beyond
    it, the channel is the one with the most contrast over noise: the
    ground truth's own definition, so the two are not biased against each
    other.
    """
    h, w, _ = img.shape
    e0 = int(round(float(np.median(e_rows[rows]))))
    base_cols = list(range(max(0, e0 - 6), max(0, e0 - 1))) or ([0] if e0 >= 1 else [])
    pic_cols = list(range(min(w - 1, e0 + 1), min(w, e0 + 7)))
    if not base_cols or not pic_cols:
        return None, 0
    base = np.median(img[:, base_cols], axis=1)
    pic = np.median(img[:, pic_cols], axis=1)
    contrast = base - pic
    noise = np.median(np.abs(np.diff(img[:, base_cols], axis=0)), axis=(0, 1)) + 1e-9
    ch = int(np.argmax(np.median(contrast[rows], axis=0) / noise))
    out = []
    for r in rows:
        e = int(e_rows[r])
        c_r = contrast[r, ch]
        if c_r <= 0:
            continue
        thr = (base[r, ch] + pic[r, ch]) / 2.0
        row = img[r, :, ch]
        lo, hi = max(0, e - 3), min(w - 1, e + 2)
        for c in range(lo, hi):
            v1, v2 = row[c], row[c + 1]
            if v1 >= thr > v2:
                out.append(c + 0.5 + (v1 - thr) / (v1 - v2))
                break
    if len(out) < 0.3 * len(rows):
        return None, len(out)
    return float(np.median(out)), len(out)


def _line(rows: np.ndarray, ends: np.ndarray) -> tuple[float, float]:
    """Robust line end = a + b * row through the rows' run ends (two passes)."""
    r = rows.astype(np.float64)
    e = ends.astype(np.float64)
    a, b = float(np.median(e)), 0.0
    for _ in range(2):
        keep = np.abs(e - (a + b * r)) <= 2.0 * ROW_TOL
        if keep.sum() < 10:
            break
        b, a = np.polyfit(r[keep], e[keep], 1)
    return float(a), float(b)


class Levels:
    """The side's smoothed log levels against the reference, and their noise."""

    def __init__(self, img: np.ndarray, ref: np.ndarray) -> None:
        L = np.log(np.maximum(img, FLOOR * ref) / ref)
        self.Lv = _vmean(L, V_SMOOTH)
        self.sigma = smoothed_noise(L, self.Lv)

    def step_down(self, starts: np.ndarray, ends: np.ndarray, beyond: int = 3
                  ) -> np.ndarray:
        """Per row: the step down from the run to the picture beyond it, in sigmas.

        The classifier knows where base-like pixels are; it does not by itself
        know that a run *ends at an edge*. A run that stops because the
        network's confidence faded -- on a frame whose dark walls sit within 2
        counts of base (r0911_04..06) it faded at the same column in every
        row, a straight line drawn by the model and not by the film -- has no
        step under its end. So each row's end is checked against the image:
        up to four run columns against the three after the partial one, the
        largest drop over the channels divided by that channel's noise.
        """
        h, w, _ = self.Lv.shape
        z = np.full(h, -np.inf)
        for r in np.flatnonzero(starts >= 0):
            e, s = int(ends[r]), int(starts[r])
            if e + 2 > w:
                continue
            inside = self.Lv[r, max(s, e - 4):e].mean(axis=0)
            past = self.Lv[r, e + 1:min(w, e + 1 + beyond)].mean(axis=0)
            z[r] = float(np.max((inside - past) / self.sigma))
        return z

    def step_up(self, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
        """Per row: the step up from the neighbour's picture into the run, in sigmas."""
        h = self.Lv.shape[0]
        z = np.full(h, -np.inf)
        for r in np.flatnonzero(starts >= 2):
            s, e = int(starts[r]), int(ends[r])
            inside = self.Lv[r, s:min(e, s + 4)].mean(axis=0)
            before = self.Lv[r, max(0, s - 4):s - 1].mean(axis=0)
            z[r] = float(np.max((inside - before) / self.sigma))
        return z

    def sliver_rows(self) -> float:
        """Fraction of rows whose border column is brighter than the next in every channel."""
        return float(np.mean(np.all(self.Lv[:, 0] > self.Lv[:, 1], axis=1)))


def read_band(prob: np.ndarray, img: np.ndarray, lev: Levels, gate: np.ndarray,
              band: tuple[int, int, float]) -> tuple[Side | None, dict]:
    """An edge from one band of base columns, or None with why not."""
    h, w = prob.shape
    a, b, peak = band
    starts, ends = row_runs(prob > P_BASE, gate, a, b)
    has = starts >= 0
    z = lev.step_down(starts, ends)
    stepped = has & (z >= STEP_Z)
    dbg: dict[str, Any] = {"band": [a, b], "peak": round(peak, 3),
                           "with_run": round(float(has.mean()), 3),
                           "stepped": round(float(stepped.mean()), 3)}
    if stepped.sum() < 10:
        return None, dbg
    rows = np.flatnonzero(stepped)
    la, lb = _line(rows, ends[rows])
    tilt = abs(lb) * (h - 1)
    fit = la + lb * np.arange(h)
    agree = stepped & (np.abs(ends - fit) <= ROW_TOL)
    frac = float(agree.mean())
    s_med = float(np.median(starts[agree])) if agree.any() else 0.0
    # A row whose base run goes straight through the line does not contradict
    # it: the picture beyond is at the base level in that row, so no step can
    # show there (r0914_17: a grid whose lines sit at base level meets the
    # base band in 30% of the rows). It must cover the band, though.
    through = has & ~agree & (ends > fit + ROW_TOL) & (starts <= s_med + 1)
    ok = float((agree | through).mean())
    dbg.update(agree=round(frac, 3), through=round(float(through.mean()), 3),
               tilt=round(tilt, 2), line_a=round(la, 2), line_b=round(lb, 5))
    if (frac < LINE_MIN or ok < AGREE_MIN or tilt > MAX_TILT
            or np.median(ends[agree]) < 1):
        return None, dbg
    interior = s_med >= 2 and np.mean(starts[agree] > 0) >= 0.5
    if interior:
        # A gap has picture on both sides: a step up from the neighbour, and
        # a step down that *stays* down -- a thin dark line in a wall at the
        # base level (r0911_01, column 213) steps down for two columns and
        # comes straight back up.
        width = float(np.median(ends[agree] - starts[agree]))
        up = lev.step_up(starts, ends)
        up_frac = float(np.mean(up[agree] >= STEP_Z))
        wide = lev.step_down(starts, ends, beyond=INNER_WIDE)
        down_frac = float(np.mean(wide[agree] >= STEP_Z))
        dbg.update(width=width, up=round(up_frac, 3), down_wide=round(down_frac, 3))
        if width > MAX_GAP or up_frac < OUTER_STEP_MIN or down_frac < AGREE_MIN:
            return None, dbg
    ar = np.flatnonzero(agree)
    e_rows = np.rint(fit).astype(int)
    x, n = crossing(img, e_rows, ar)
    if x is None:
        x = float(np.median(ends[agree]))
    mid = (h - 1) / 2.0
    off = x - (la + lb * mid)
    x_top = la + off if tilt > 1.0 else None
    x_bot = la + lb * (h - 1) + off if tilt > 1.0 else None
    spread = float(np.median(np.abs(ends[agree] - fit[agree]))) * 1.4826
    half = max(0.5, spread)
    outer = s_med if interior else None
    pm = float(np.mean(prob[agree, int(np.median(starts[agree]))]))
    conf = ok * (0.5 + 0.5 * pm) * (0.5 + 0.5 * frac)
    dbg.update(crossings=n, spread=round(spread, 3), ok=round(ok, 3))
    return Side(EDGE, x=x, lo=x - half, hi=x + half, conf=round(conf, 3), outer=outer,
                x_top=x_top, x_bottom=x_bot,
                note=f"line in {frac:.0%} of rows, base through it in {ok - frac:.0%}"), dbg


def read_side(prob: np.ndarray, img: np.ndarray, gate: np.ndarray,
              ref: np.ndarray) -> tuple[Side, dict]:
    """One side, from its per-pixel base probabilities (border at column 0)."""
    h, w = prob.shape
    dbg: dict[str, Any] = {}
    # the empty gate at the border
    if gate[0]:
        n = int(np.argmin(gate)) if not gate.all() else w
        dbg["gate_cols"] = n
        return Side(NO_FILM, conf=0.9, note=f"empty gate over {n} columns"), dbg
    base = prob > P_BASE
    # all base: a run from the border across (nearly) the whole width, full height
    starts, ends = row_runs(base, gate, 0, 1)
    full = (starts == 0) & (ends >= w - 2)
    if full.mean() >= AGREE_MIN:
        return Side(ALL_BASE, conf=round(float(full.mean()), 3),
                    note="base across the frame"), dbg
    lev = Levels(img, ref)
    tried = []
    strong = False
    passed: list[tuple[float, int, Side]] = []
    for band in bands(base, gate):
        side, bd = read_band(prob, img, lev, gate, band)
        tried.append(bd)
        if side is not None:
            # the best-supported band, not the first: a bright border column
            # (r0911_17) makes a one-column band that also passes
            passed.append((float(bd["ok"]) + float(bd["agree"]), -band[0], side))
        else:
            strong |= band[2] >= AGREE_MIN and bd.get("stepped", 0) >= 0.5
    dbg["bands"] = tried
    if passed:
        return max(passed, key=lambda t: (t[0], t[1]))[2], dbg
    if strong:
        return Side(REFUSE, note="a full-height base band that is not one straight edge"), dbg
    # no band: is there base at the border in most rows, or none?
    starts, ends = row_runs(base, gate, 0, OUTER_MAX)
    stepped = (starts >= 0) & (lev.step_down(starts, ends) >= STEP_Z)
    frac_none = 1.0 - float(stepped.mean())
    sliver = lev.sliver_rows()
    dbg.update(frac_none=round(frac_none, 3), sliver=round(sliver, 3))
    if frac_none < AGREE_MIN:
        return Side(REFUSE, note=f"rows disagree: {1 - frac_none:.0%} with a stepped base "
                                 f"run, none on one line"), dbg
    if sliver >= SLIVER_ROWS:
        # part of the border column is base: under a column, over none. Its
        # exact share would need the base level, which a frame showing only a
        # sliver cannot supply -- so the middle, with the honest interval.
        return Side(EDGE, x=0.5, lo=0.05, hi=1.0, conf=round(sliver, 3),
                    note=f"sliver: border column brighter in {sliver:.0%} of rows"), dbg
    return Side(PICTURE_TO_BORDER, conf=round(frac_none * (1.0 - sliver), 3),
                note=f"{frac_none:.0%} of rows show no base"), dbg


def side_probabilities(prep: dict[str, Any], wts: dict[str, Any] | None = None
                       ) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Per side: (prob (h, w), image, gate) with that side's border at column 0."""
    wts = wts if wts is not None else _weights()
    out = {}
    for name, img, gate in side_views(prep):
        F = features(img, prep["ref"], gate)
        logit = mlp_logit(F, wts)
        prob = 1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30)))
        prob[:, gate] = 0.0
        out[name] = (prob, img, gate)
    return out


def detect(image: np.ndarray, ctx: dict, wts: dict[str, Any] | None = None) -> EdgeResult:
    """The interface. ``wts`` is for `learned_train.py`'s held-out runs only."""
    prep = prepare(image, str(ctx.get("film_type", "unknown")))
    W = prep["img"].shape[1]
    debug: dict[str, Any] = {"ref": [round(float(v), 3) for v in prep["ref"]],
                             "gate": prep["gate_why"], "gate_cols": int(prep["gate"].sum())}
    sides = {}
    for name, (prob, img, gate) in side_probabilities(prep, wts).items():
        s, d = read_side(prob, img, gate, prep["ref"])
        if name == "right":
            if s.x is not None:
                s.x, s.lo, s.hi = W - s.x, W - s.hi, W - s.lo
            if s.outer is not None:
                s.outer = W - s.outer
            if s.x_top is not None and s.x_bottom is not None:
                s.x_top, s.x_bottom = W - s.x_top, W - s.x_bottom
        sides[name] = s
        debug[name] = d
    # a blank frame is base on both sides or it is not blank
    if (sides["left"].state == ALL_BASE) != (sides["right"].state == ALL_BASE):
        for name in ("left", "right"):
            if sides[name].state == ALL_BASE:
                sides[name] = Side(REFUSE, note="all base on one side only")
    return EdgeResult(sides["left"], sides["right"], debug)
