"""stepline: real base ends in a straight line from top to bottom; picture does not.

The one test that has held (Stefan): real base is *"a sharp line, completely
black from top to bottom"* in the positive. On the negative that is a plateau
at base level ending in one straight near-vertical step down to picture. A
dark scene area (a silhouette, shade) can be as bright as base and as flat,
but its boundary is not one straight line down the whole height.

So nothing here averages the rows away. Per side, working always as the left
side (the right is the same code on the mirrored frame, so the answer mirrors
exactly):

1. **Per row, per column boundary, a step vote.** A row votes for boundary
   ``x`` when the columns just outside it are near base level and the two just
   inside it (skipping the one column the transition smears over) are darker
   by a real margin -- relative to the frame's own base estimate and to its
   measured noise. No absolute level anywhere: uint8 and uint16 frames differ
   by ~250x.
2. **Candidate lines, Hough-like.** Every near-vertical line (tilt up to four
   columns over the height) is scored by the fraction of rows that voted
   within a column of it (*strong* support). Each local maximum is a
   candidate, and is then measured along its whole length:

   * *weak* support: the fraction of **all** rows whose step across the line
     is merely positive (2.5 sigma). Real base ends in every row, even where
     the picture beside it is nearly as clear; the edge of a painting on a
     dark wall ends only where the painting is. Measured: real edges
     0.84-1.00, picture lines 0.57-0.81.
   * the same per third of the height (a line must be full height);
   * *plateau rows*: in how many rows the median of everything between the
     border and the line is at base level -- whether the plateau reaches the
     border;
   * *contradictions*: rows stepping the wrong way (the inside brighter), and
     among the rows without a step, how many are clear as base on the inside
     too. A row where the scene beside real base is as clear as base shows no
     step and contradicts nothing; a row where the "plateau" is picture does.

3. **Three rules, by where the plateau is.**

   * Base that reaches the border and is no wider than a gap (``TOUCH_RULE``)
     is how base normally shows; there a line may be half hidden by a dark
     scene (strip6_02: 45% strong, 84% weak).
   * Base that does not reach the border needs the neighbour's picture beyond
     it, and a black object in the scene (a grid, a door jamb) looks exactly
     like that -- there the line must be supported almost everywhere
     (``FLOAT_RULE``). Beyond ``GAP_MAX`` such a plateau is refused outright:
     on dev every one there was a black object (r0914_20's gap between
     sculpture and wall: two straight sides at exactly base level), and a
     frame that far off has left the walk anyway.
   * Base mostly *hidden* -- a night sky or a black wall beside it, the step
     visible in a fifth of the rows (bw0910_04, r0911_07) -- is taken only
     near the border (``HIDDEN_X``), only when no row contradicts it, only
     when the rows that do step lie on one straight line (``HIDDEN_RESID``),
     and at low confidence. On dev this rule adds four right edges and no
     wrong one, but the nearest wrong line (r0911_06, a wall edge at 9.3)
     scattered 0.32 against the limit's 0.30: it is the rule most exposed to
     scenes it has not seen.

4. **Per-row 50% crossing, median along the line.** In every supporting row
   the base width is the step's *area* -- ``sum((v - P) / (B - P))`` over a
   window around the line, B the plateau's level and P the row's picture
   level just inside -- which is the 50% crossing of a symmetric blur and
   exact for a box pixel with a sharp edge. The answer is the median over
   rows; a robust line through the crossings gives ``x_top``/``x_bottom``.
5. **A gate edge is straight and nearly square.** The crossings of the chosen
   line are fitted on the middle 70% of the rows (the gate's corners are
   rounded); a line that leans more than ``TILT_MAX`` or scatters more than
   ``RESID_MAX`` is not a gate edge -- a black grid bar in the scene passed
   every support test and failed this one -- and the next candidate is tried.
6. **Picture to the border, confidently.** When no line qualifies the border
   column itself is examined. Base is full height, so a border column that
   sits clearly below base level in most rows is picture; so is one at base
   level whose rows are *textured* (base is uniform down its length). Both are
   confident ``picture_to_border``. A border column that is uniform *and* at
   base level with no qualifying step inside is the ambiguous case -- a dark
   scene, or base whose end cannot be seen -- and is answered at 0.25; when a
   strong but leaning line was rejected beside it, the side refuses.
7. **The frame is wider than the aperture.** Two edges closer than
   ``FRAME_MIN`` cannot both be real (the one whose plateau reaches the border
   wins; with no reason to prefer either, both refuse), and an edge with more
   base than the aperture could leave spare makes the other side picture.

Base level and its tolerance. Base is not strictly the brightest thing on
C-41 (exposed areas lose the orange mask; strip6_01's base reads 0.86 of the
frame's p99.5), so the reference is the brightest *full-height* level -- the
largest column median, after a 5-column running median so that a single
bright sensor column (r0911: column 214, 127 counts against base 111) cannot
set it. Over every edge found on dev the plateau read 0.96-1.00 of it. How far
a pixel may sit below it and still be "at base level" is measured too: 2.5x
the spread down that reference column, at least ``BASE_TOL`` -- 8-bit C-41
base spreads 1-3%, B&W base 7-10% (a row pattern), and one tolerance for both
either admitted picture on C-41 or lost B&W base to its own noise.

Not a negative. A frame with a full-height strip near black (the slide film
x0920_01-09, already a positive) is refused: nothing above holds there.

Empty gate (``no_film``): neutral, grain-free and far brighter than any film
in the same frame. A frame with nothing but base is ``all_base``.

Iterations (dev, 193 labelled sides): iter01 0.94 side accuracy, five false
bases (picture lines taken for edges); iter02 added weak support, the two
plateau rules and the straightness test: 0.97, one false base; iter03 made
bright-but-textured border columns a confident border and refused one leaning
line; iter04 is described above.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, TRIM_ROWS, EdgeResult, Side,
)

# --------------------------------------------------------------------------
# constants

#: Columns searched inward from each border. The stage9b ladder walks an edge
#: to 88 columns; 120 is over a quarter of the frame.
ZONE = 120
#: Rows averaged vertically before the per-row test: a 3-row box lowers the
#: noise by 1.7x and leaves a vertical edge where it was.
VSMOOTH = 3
#: A row's step must be at least this fraction of the base level ...
STEP_REL = 0.06
#: ... and this many noise sigmas of the difference it is measured from.
STEP_SIGMA = 4.0
#: A step is "weakly present" at this many sigmas (the weak support).
WEAK_SIGMA = 2.5
#: Least fraction below base still "at base level"; the working tolerance is
#: BASE_SPREAD times the spread down the reference column, capped at
#: BASE_TOL_MAX. 8-bit C-41 base measured 2.4-3.2% pixel sd, 5th percentile
#: 0.95; B&W base 7-10% spread.
BASE_TOL = 0.10
BASE_SPREAD = 2.5
BASE_TOL_MAX = 0.20
#: Tilts tried, in columns across the full height of the frame, smallest
#: first so that a tie goes to the more vertical line.
TILTS = sorted(np.arange(-4.0, 4.01, 0.5), key=lambda t: (abs(t), t))
#: Candidate lines: local maxima of strong support at least this high.
CANDIDATE_MIN = 0.12
#: A plateau "reaches the border" when in this fraction of rows the median
#: between the border and the line is at base level (below THIN_X columns it
#: cannot be told, and is assumed).
PLATEAU_ROWS_MIN = 0.85
THIN_X = 3
#: Widest base that can show at a border with no neighbour beyond it, in
#: columns: gaps measured 5-37 columns on dev (r0911_17, the stage9b ladder).
#: A plateau clear of the border further in than this is refused.
GAP_MAX = 45
#: (strong, weak, weakest third) a border plateau's line must reach ...
TOUCH_RULE = (0.40, 0.80, 0.50)
#: ... and a line whose plateau does not reach the border.
FLOAT_RULE = (0.75, 0.90, 0.70)
#: The hidden-base rule: base reaching the border, no wider than HIDDEN_X,
#: stepping in at least HIDDEN_STRONG of the rows; no more than HIDDEN_NEG of
#: rows stepping the wrong way; of the rows without a step at least
#: HIDDEN_CLEAR clear as base on the inside as well; the stepping rows within
#: HIDDEN_RESID columns (median absolute) of one line. On dev the real hidden
#: edges scattered 0.12-0.25; the wall edge in r0911_06 0.32.
HIDDEN_X = 12
HIDDEN_STRONG = 0.12
HIDDEN_NEG = 0.03
HIDDEN_CLEAR = 0.75
#: Hidden base wider than HIDDEN_X (library 2: 17-25 columns of base beside a
#: night silhouette or a dark interior, the step in 22-72% of rows): the same
#: rule, stricter where a dark scene could pass it. Of the rows without a step
#: WIDE_CLEAR must be clear as base inside -- every real one on dev measured
#: 0.96-1.00, every picture line that far in 0.39-0.79 or stepping the wrong
#: way in 8-38% of rows; the plateau at base level in WIDE_ROWS of the rows;
#: and the plateau as uniform down its length as base is -- the per-row
#: median's spread under WIDE_TEXTURE of base (real: 0.5-1.0% on C-41; a
#: B&W base's row pattern is not admitted).
WIDE_CLEAR = 0.94
WIDE_ROWS = 0.97
WIDE_TEXTURE = 0.03
HIDDEN_CONF = 0.35
#: A frame edge is the camera gate's: straight, and square to the strip but
#: for the strip's own skew. Measured on the middle 70% of the rows (the
#: gate's corners are rounded): over every plausible edge on dev and the
#: ladder the fitted tilt was -2.8..+2.5 columns over the height and the
#: per-row crossings sat 0.03-0.39 columns (median absolute) off the line;
#: the two black grid bars that passed everything else (r0914_17, x0920_73)
#: leaned 3.6 columns and scattered 0.47-0.60. Limits sit between.
MID_ROWS = 0.15
TILT_MAX = 3.2
RESID_MAX = 0.45
#: A row's step is sharp when this much of it falls between the column before
#: its crossing and the one after (`_sharp_rows`); the scatter is judged on
#: sharp rows when there are CLEAN_MIN of them in the middle 70%.
SHARP = 0.75
CLEAN_MIN = 8
#: A border column is picture when at most this fraction of rows is base-like.
BORDER_BASE_MAX = 0.5
#: ... or when it is textured: its robust spread down the rows (IQR/1.349,
#: relative to base) over both of these. Every C-41 base border column on dev
#: measured 0.007-0.026; the B&W ones 0.07-0.10 at 3-5x their noise, so the
#: noise term keeps B&W from being called on texture it has as base.
TEXTURE_MIN = 0.05
TEXTURE_SIGMA = 6.0
#: Two edges closer than this cannot both be real. The labelled frame is
#: ~445 columns (wider than the 428-column aperture); 395 is a conservative
#: 36 mm frame less 2.5 mm, so that this never decides a close case.
FRAME_MIN = 395.0
#: An edge with more base than this makes the other side picture: the frame
#: cannot fit otherwise (428 - 395 columns, the same allowance).
IMPLIES_BORDER = 428.0 - FRAME_MIN
#: Darkest full-height column over the base estimate below which the frame is
#: not a negative. Every negative on dev and the ladder measured >= 0.14; the
#: already-inverted x0920_01-09 measured 0.015-0.051.
NOT_A_NEGATIVE = 0.08
#: Columns next to the empty gate kept out of the base estimate and search.
GATE_MARGIN = 3
#: The band: a gap clear of the border, the neighbour's picture beyond it.
#: Widths between the two lines, in columns: measured 5.4 (r0911_17) to 26
#: (b0921c_49, b0922b_05) over every labelled band; 4-40 leaves room either way.
BAND_MIN = 4
BAND_MAX = 40
#: A band's columns are at base level in at least BAND_COLS of a row for the
#: row to count, and at least BAND_ROWS of the rows must count. It is the
#: strict form of "completely black from top to bottom", and it is what
#: separates the real inner line from a dark scene area next to it: a band
#: drawn one line too far in holds picture in the rows where the true line
#: steps (b0921c_49: 18% of rows show the true line at 84, and a line at 96
#: in the scene had more).
BAND_COLS = 0.90
BAND_ROWS = 0.95
#: Each line of a band steps in at least BAND_LINE of the rows, and one of the
#: two in at least BAND_UNION.
BAND_LINE = 0.12
BAND_UNION = 0.60
#: A row where a line does not step must be as clear as base beyond it:
#: that line is hidden there by a scene as clear as base, not absent. At most
#: 1 - BAND_CLEAR of all rows may be neither.
BAND_CLEAR = 0.92
#: Band candidates examined per side, strongest first.
BAND_TRY = 6
#: Fraction of all film pixels at base level for the frame to be blank.
ALL_BASE_FRAC = 0.98


def _vsmooth(a: np.ndarray, n: int = VSMOOTH) -> np.ndarray:
    if n <= 1:
        return a
    pad = n // 2
    p = np.pad(a, ((pad, pad), (0, 0)), mode="edge")
    c = np.cumsum(np.pad(p, ((1, 0), (0, 0))), axis=0)
    return (c[n:] - c[:-n]) / n


def _noise(s: np.ndarray) -> float:
    """Per-pixel noise of ``s`` from vertical neighbours: MAD of differences."""
    d = np.diff(s, axis=0)
    return float(np.median(np.abs(d - np.median(d))) / 0.6745 / np.sqrt(2.0)) + 1e-9


def _running_median(a: np.ndarray, n: int = 5) -> np.ndarray:
    pad = n // 2
    p = np.pad(a, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(p, n), axis=1)


def _spread(v: np.ndarray) -> float:
    q = np.percentile(v, [25, 75])
    return float((q[1] - q[0]) / 1.349)


def _gate_columns(rgb: np.ndarray) -> np.ndarray:
    """Columns that are the empty gate: neutral, flat and far brighter than film.

    Measured on r0909_13 and r0914_21: the gate reads 181-186 in all three
    channels of an 8-bit prescan, row sd ~1.6, against film at most ~70/30/16.
    """
    med = np.median(rgb, axis=0)                                  # (W, 3)
    lo = np.maximum(med.min(axis=1), 1e-9)
    neutral = med.max(axis=1) / lo <= 1.3
    s = rgb.sum(axis=2)
    lvl = np.median(s, axis=0)
    # Flat in most rows, not in a quartile range: a film end cut at a slant
    # covers the border columns in some rows (b0921b_40: columns 0-1 are
    # gate in 80% and 59% of rows, film in the rest), and an IQR test then
    # lost the gate and took its level for base.
    flat = np.mean(np.abs(s - lvl[None, :]) <= 0.05 * np.maximum(lvl, 1e-9), axis=0) >= 0.5
    cand = neutral & flat
    if not cand.any():
        return cand
    film = ~cand
    if film.sum() < 8:
        return cand
    film_max = float(np.percentile(lvl[film], 99))
    return cand & (lvl >= 1.8 * film_max)


# --------------------------------------------------------------------------
# one side, as the left


class _Frame:
    """What every side shares: the smoothed sum, base, its tolerance, noise."""

    def __init__(self, sv: np.ndarray, base: float, tol: float, sigma: float) -> None:
        self.sv, self.base, self.tol, self.sigma = sv, base, tol, sigma

    @property
    def floor(self) -> float:
        """The lowest value still at base level."""
        return self.base * (1.0 - self.tol)


def _steps(sv: np.ndarray, f: _Frame, zone: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(step, noise of step, outside-is-base) per row and boundary x = 1..zone.

    The step at ``x`` is the mean of the (up to) two columns outside minus the
    mean of columns ``x+1, x+2`` -- column ``x`` is skipped, the one a soft
    edge smears. At ``x = 1`` the outside is the border column alone, which a
    base narrower than a column only partly covers; there the step itself is
    the evidence and the level is not asked.
    """
    h, w = sv.shape
    z = min(zone, w - 3)
    c = np.cumsum(np.pad(sv, ((0, 0), (1, 0))), axis=1)
    xs = np.arange(1, z + 1)
    a = np.maximum(xs - 2, 0)
    outer = (c[:, xs] - c[:, a]) / (xs - a)
    inner = (c[:, xs + 3] - c[:, xs + 1]) / 2.0
    d = outer - inner
    sd = f.sigma / np.sqrt(VSMOOTH) * np.sqrt(1.0 / (xs - a) + 0.5)
    baselike = outer >= f.floor
    baselike[:, 0] = True
    return d, sd, baselike


def _candidates(vote: np.ndarray) -> list[dict[str, Any]]:
    """Local maxima of strong support over x, each with its best tilt."""
    h, z = vote.shape
    vd = vote.copy()
    vd[:, 1:] |= vote[:, :-1]
    vd[:, :-1] |= vote[:, 1:]
    rr = (np.arange(h) - (h - 1) / 2.0) / h
    best = np.full(z, -1.0)
    btilt = np.zeros(z)
    for tilt in TILTS:
        off = np.rint(rr * tilt).astype(int)
        idx = np.arange(z)[None, :] + off[:, None]
        ok = (idx >= 0) & (idx < z)
        hit = np.zeros((h, z), bool)
        hit[ok] = vd[np.nonzero(ok)[0], idx[ok]]
        frac = hit.mean(axis=0)
        better = frac > best + 1e-9
        best[better] = frac[better]
        btilt[better] = tilt
    out = []
    for j in range(z):
        if best[j] < CANDIDATE_MIN:
            continue
        if j > 0 and best[j - 1] > best[j]:
            continue
        if j < z - 1 and best[j + 1] >= best[j]:
            continue
        off = np.rint(rr * btilt[j]).astype(int)
        out.append({"j": j, "strong": float(best[j]), "tilt": float(btilt[j]),
                    "idx": np.clip(j + off, 0, z - 1)})
    return out


def _measure(c: dict[str, Any], sv: np.ndarray, f: _Frame, d: np.ndarray,
             sd: np.ndarray, vote: np.ndarray) -> None:
    """Weak support, weakest third, plateau rows and contradictions along ``c``."""
    h, z = d.shape
    idx = c["idx"]
    rows = np.arange(h)
    cols = np.clip(np.stack([idx - 1, idx, idx + 1], axis=1), 0, z - 1)
    dr = d[rows[:, None], cols].max(axis=1)
    weak = dr >= WEAK_SIGMA * sd[idx]
    bands = np.minimum((rows * 3) // h, 2)
    c["weak"] = float(weak.mean())
    c["band"] = float(min(weak[bands == b].mean() for b in range(3)))
    c["hit"] = vote[rows[:, None], cols].any(axis=1)
    x = c["j"] + 1
    xb = idx + 1                                     # the boundary, in columns
    if x <= GAP_MAX:          # further in, the float rule applies regardless
        # the columns fully outside the line in every row (a tilt moves it by
        # a column or two, which a median does not notice)
        reach = max(int(xb.min()) - 1, 1)
        plateau = np.median(sv[:, :reach], axis=1)
        c["plateau_rows"] = float(np.mean(plateau >= f.floor))
        # how uniform the plateau is down its length, relative to base
        c["texture"] = _spread(plateau) / f.base
    else:
        c["plateau_rows"] = 0.0
        c["texture"] = 1.0
    c["touch"] = bool(x <= THIN_X or c["plateau_rows"] >= PLATEAU_ROWS_MIN)
    c["neg"] = float(np.mean(d[rows, idx] <= -WEAK_SIGMA * sd[idx]))
    inside = sv[rows, np.minimum(xb + 1, sv.shape[1] - 1)]
    quiet = ~c["hit"]
    c["clear"] = float(np.mean(inside[quiet] >= f.floor)) if quiet.any() else 1.0


def _rule(c: dict[str, Any]) -> str | None:
    """Which rule the candidate passes: touch, float, hidden, or None."""
    x = c["j"] + 1
    if c["touch"] and x <= GAP_MAX:
        s, w, b = TOUCH_RULE
        if c["strong"] >= s and c["weak"] >= w and c["band"] >= b:
            return "touch"
        if (x <= HIDDEN_X and c["strong"] >= HIDDEN_STRONG and c["neg"] <= HIDDEN_NEG
                and c["clear"] >= HIDDEN_CLEAR):
            return "hidden"
        if (x > HIDDEN_X and c["strong"] >= HIDDEN_STRONG and c["neg"] <= HIDDEN_NEG
                and c["clear"] >= WIDE_CLEAR and c["plateau_rows"] >= WIDE_ROWS
                and c["texture"] <= WIDE_TEXTURE):
            return "hidden"
        return None
    # A plateau clear of the border is a band: `_band` decides it, with the
    # neighbour's line as well as this one.
    return None


def _crossings(sv: np.ndarray, c: dict[str, Any], base: float) -> tuple[np.ndarray, float]:
    """Per supporting row, the base width as the area of the step (NaN elsewhere).

    B is the plateau's own level along the line (its median two columns
    further out), or the frame's base estimate where the plateau is too thin
    to have a clean column.
    """
    h, w = sv.shape
    out = np.full(h, np.nan)
    xb = c["idx"] + 1
    rows = np.nonzero(c["hit"])[0]
    far = xb[rows] - 4
    if len(rows) and np.median(far) >= 0:
        vals = sv[rows[far >= 0], far[far >= 0]]
        level = float(np.median(vals)) if len(vals) else base
    else:
        level = base
    for r in rows:
        a = max(int(xb[r]) - 3, 0)
        b = min(int(xb[r]) + 3, w - 3)
        p = float(np.median(sv[r, b:b + 3]))
        span = level - p
        if span <= 0:
            continue
        frac = np.clip((sv[r, a:b] - p) / span, 0.0, 1.0)
        out[r] = a + float(frac.sum())
    return out, level


def _fit(rows: np.ndarray, xs: np.ndarray) -> tuple[float, float]:
    """Robust line x = m*r + c through the crossings: LSQ after MAD cuts.

    The cuts start from the square line through the median, not from a
    first least-squares fit: a line seen in a run of rows at one end
    (b0922s_06: rows 41-98 at 8.0) is tipped by two stray crossings at the
    other (rows 213 and 230 at 3.8-4.6) far enough that a cut about the
    tipped line keeps them -- the fit leaned 6.2 columns, the rows 0.5.
    A gate edge leans at most ~3 columns over the height, so the square
    line is within the cut of every real crossing.
    """
    ok = np.isfinite(xs)
    r, x = rows[ok], xs[ok]
    if len(x) < 4:
        return float(np.nanmedian(xs)), 0.0
    m, c = 0.0, float(np.median(x))
    for _ in range(2):
        res = x - (m * r + c)
        mad = np.median(np.abs(res)) + 1e-6
        keep = np.abs(res) <= 3.0 * 1.4826 * mad + 0.25
        if keep.sum() < 4:
            break
        m, c = np.polyfit(r[keep], x[keep], 1)
    return float(c), float(m)


def _sharp_rows(sv: np.ndarray, xs: np.ndarray, level: float, outward: bool = False) -> np.ndarray:
    """Rows whose step is a gate edge's: most of it inside two columns.

    A row's crossing is a gate edge's own position only where the profile
    drops from base to picture within the columns either side of it. Where
    the picture beside the edge is itself a ramp or a texture near base
    level -- foliage, a shaded wall -- the area of the step takes some of
    the scene for base and the crossing wanders by a column or two, in rows
    that say nothing about the edge's shape (b0921a_23: 0.82 columns of
    scatter over all rows, 0.38 over the sharp ones). Sharp: at least
    SHARP of the step between the column before the crossing and the one
    after it.
    """
    h, w = sv.shape
    out = np.zeros(h, bool)
    for r in np.nonzero(np.isfinite(xs))[0]:
        k = int(np.floor(xs[r]))
        if outward:
            lo, hi, p = k + 1, k - 1, sv[r, max(k - 4, 0):max(k - 1, 1)]
        else:
            lo, hi, p = k - 1, k + 1, sv[r, min(k + 2, w - 1):min(k + 5, w)]
        if min(lo, hi) < 0 or max(lo, hi) >= w or len(p) == 0:
            continue
        span = level - float(np.median(p))
        out[r] = span > 0 and (sv[r, lo] - sv[r, hi]) >= SHARP * span
    return out


def _straightness(rows: np.ndarray, xs: np.ndarray, sharp: np.ndarray,
                  h: int) -> tuple[float, float, float, float, int]:
    """(c, m, resid, tilt, sharp rows) of a line through the middle rows' crossings.

    The scatter is judged on the sharp rows when there are CLEAN_MIN of
    them (see `_sharp_rows`), on all of them otherwise. The lean is the
    smaller of the two fits' -- a line through a few sharp rows may lean
    more than the edge does (b0921a_15: 16 rows, -3.4 columns; -1.4 over
    all 74) -- which rejects only what leans either way: every picture line
    on dev that got this far leaned 4.2-10 columns on both.
    """
    mid = np.where((rows >= MID_ROWS * h) & (rows <= (1.0 - MID_ROWS) * h), xs, np.nan)
    c_a, m_a = _fit(rows, mid)
    clean = np.where(sharp, mid, np.nan)
    n_clean = int(np.isfinite(clean).sum())
    if n_clean >= CLEAN_MIN:
        c0, m = _fit(rows, clean)
        resid = float(np.nanmedian(np.abs(clean - (c0 + m * rows))))
        tilt = min(abs(m_a), abs(m)) * h
    else:
        c0, m = c_a, m_a
        resid = float(np.nanmedian(np.abs(mid - (c0 + m * rows))))
        tilt = abs(m) * h
    return c0, m, resid, tilt, n_clean


def _plateau_start(sv: np.ndarray, c: dict[str, Any], floor: float) -> float | None:
    """Far side of a plateau that does not reach the border (median over rows)."""
    h = sv.shape[0]
    starts = []
    for r in np.nonzero(c["hit"])[0]:
        k = int(c["idx"][r]) - 1                 # last column fully outside
        while k > 0 and sv[r, k - 1] >= floor:
            k -= 1
        starts.append(k)
    return float(np.median(starts)) if len(starts) >= 0.3 * h else None


# --------------------------------------------------------------------------
# the band: a gap clear of the border, two parallel lines


def _out_steps(sv: np.ndarray, f: _Frame, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """(step, outside-is-base) at the far side of a band, boundary o = 1..zone.

    The mirror of `_steps`: base occupies ``[o, ...)`` and the neighbour's
    picture ``[..., o)``. The step is the mean of columns ``o, o+1`` minus the
    mean of ``o-3, o-2`` (column ``o-1`` is the smeared one). Below ``o = 3``
    there is no room and nothing votes.
    """
    h, w = sv.shape
    z = min(zone, w - 3)
    c = np.cumsum(np.pad(sv, ((0, 0), (1, 0))), axis=1)
    d = np.full((h, z), -np.inf)
    inside = np.zeros((h, z), bool)
    o = np.arange(3, z + 1)
    band = (c[:, o + 2] - c[:, o]) / 2.0
    d[:, o - 1] = band - (c[:, o - 1] - c[:, o - 3]) / 2.0
    inside[:, o - 1] = band >= f.floor
    return d, inside


def _tilted(vote: np.ndarray, dilate: bool = True) -> np.ndarray:
    """(tilt, row, j): the vote on (within a column of) the tilted line through ``j``."""
    h, z = vote.shape
    vd = vote.copy()
    if dilate:
        vd[:, 1:] |= vote[:, :-1]
        vd[:, :-1] |= vote[:, 1:]
    rr = (np.arange(h) - (h - 1) / 2.0) / h
    out = np.zeros((len(TILTS), h, z), bool)
    for t, tilt in enumerate(TILTS):
        off = np.rint(rr * tilt).astype(int)
        idx = np.arange(z)[None, :] + off[:, None]
        ok = (idx >= 0) & (idx < z)
        out[t][ok] = vd[np.nonzero(ok)[0], idx[ok]]
    return out


def _best_lines(hit: np.ndarray, exact: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per column ``j``, the line's best tilt, its support, and its exact column.

    ``hit`` is the vote within a column of each tilted line (a line is found
    even where the edge wanders by one); ``exact`` the vote on it exactly,
    which then places the line on its best column of the three -- crossings
    are measured in a window around it, and a window a column or two off
    truncates them (ladder9b_21). Ties go to the smaller tilt (TILTS order).
    """
    sup = hit.mean(axis=1)                                     # (T, z)
    t = np.argmax(sup, axis=0)
    z = hit.shape[2]
    s = sup[t, np.arange(z)]
    ex = exact.mean(axis=1)
    fine = np.arange(z)
    for j in range(z):
        opts = [(ex[t[j], q], -abs(q - j), q) for q in (j - 1, j, j + 1) if 0 <= q < z]
        fine[j] = max(opts)[2]
    return t, s, fine


def _band_candidates(hin: np.ndarray, hout: np.ndarray, ein: np.ndarray,
                     eout: np.ndarray) -> list[dict[str, Any]]:
    """Pairs of lines, outer ``o = k + 1`` and inner ``x = j + 1``, BAND_MIN..BAND_MAX apart.

    Each line takes its own best tilt: two frames' gate edges need not be
    parallel to a column (b0921a_01: this one leans +1 over the height, the
    neighbour's -1.3), and a common tilt pulls the weaker line off its
    column (b0922b_08). A pair is scored by the rows either line steps in
    plus the rows the weaker one steps in, so that a strong inner line pairs
    with the outer line that is really there rather than with any column.
    Best partner per inner line, strongest first.
    """
    T, h, z = hin.shape
    tin, sin, fin = _best_lines(hin, ein)
    tout, sout, fout = _best_lines(hout, eout)
    out = []
    for j in np.nonzero(sin >= BAND_LINE)[0]:
        best = None
        for k in range(max(j - BAND_MAX, 0), j - BAND_MIN + 1):
            if sout[k] < BAND_LINE:
                continue
            u = float((hin[tin[j], :, j] | hout[tout[k], :, k]).mean())
            if u < BAND_UNION:
                continue
            score = u + min(float(sin[j]), float(sout[k]))
            if best is None or score > best[0] + 1e-9:
                best = (score, u, k)
        if best is None:
            continue
        score, u, k = best
        out.append({"j": int(fin[j]), "k": int(fout[k]), "t_in": int(tin[j]),
                    "t_out": int(tout[k]), "score": score, "union": u,
                    "s_in": float(sin[j]), "s_out": float(sout[k])})
    out.sort(key=lambda c: (-c["score"], -c["j"]))
    # one per line: a neighbour within two columns with a lower score is the same line
    kept: list[dict[str, Any]] = []
    for c in out:
        if all(abs(c["j"] - q["j"]) > 2 for q in kept):
            kept.append(c)
    return kept


def _step_crossing(row: np.ndarray, xb: int, level: float, outward: bool) -> float:
    """The 50% crossing of one row's step at boundary ``xb``, by its area.

    Inward (base left of ``xb``): as in `_crossings`. Outward (base right of
    ``xb``): the mirror, base width counted from the right end of the window.
    """
    w = len(row)
    if not outward:
        a, b = max(xb - 3, 0), min(xb + 3, w - 3)
        p = float(np.median(row[b:b + 3]))
    else:
        a, b = max(xb - 3, 3), min(xb + 3, w)
        p = float(np.median(row[a - 3:a]))
    span = level - p
    if span <= 0 or b <= a:
        return np.nan
    frac = np.clip((row[a:b] - p) / span, 0.0, 1.0)
    return a + float(frac.sum()) if not outward else b - float(frac.sum())


def _joint_fit(r_in: np.ndarray, x_in: np.ndarray, r_out: np.ndarray,
               x_out: np.ndarray) -> tuple[float, float, float]:
    """Two parallel lines through the crossings: (slope, c_in, c_out), LSQ after a MAD cut."""
    r = np.concatenate([r_in, r_out])
    x = np.concatenate([x_in, x_out])
    g = np.concatenate([np.zeros(len(r_in)), np.ones(len(r_out))])
    a = np.stack([r, 1.0 - g, g], axis=1)
    sol = np.linalg.lstsq(a, x, rcond=None)[0]
    res = x - a @ sol
    mad = np.median(np.abs(res)) + 1e-6
    keep = np.abs(res) <= 3.0 * 1.4826 * mad + 0.25
    if keep.sum() >= 6 and (g[keep] == 0).sum() >= 3 and (g[keep] == 1).sum() >= 3:
        sol = np.linalg.lstsq(a[keep], x[keep], rcond=None)[0]
    return float(sol[0]), float(sol[1]), float(sol[2])


def _band_measure(c: dict[str, Any], sv: np.ndarray, f: _Frame, hin: np.ndarray,
                  hout: np.ndarray) -> dict[str, Any]:
    """Interior, clearance and the two lines' crossings for one band candidate."""
    h, w = sv.shape
    rows = np.arange(h)
    rr = (rows - (h - 1) / 2.0) / h
    xb = c["j"] + 1 + np.rint(rr * TILTS[c["t_in"]]).astype(int)    # inner boundary per row
    ob = c["k"] + 1 + np.rint(rr * TILTS[c["t_out"]]).astype(int)   # outer boundary per row
    hit_in = hin[c["t_in"], :, c["j"]]
    hit_out = hout[c["t_out"], :, c["k"]]
    # strict interior: columns ob+1 .. xb-2 per row (one column kept off each line)
    at = np.cumsum(np.pad(sv >= f.floor, ((0, 0), (1, 0))), axis=1)
    a = np.clip(ob + 1, 0, w)
    b = np.clip(xb - 1, 0, w)
    n = np.maximum(b - a, 1)
    frac = (at[rows, b] - at[rows, a]) / n
    interior = float(np.mean(frac >= BAND_COLS))
    inside = sv[rows, np.clip(xb + 1, 0, w - 1)]
    beyond = sv[rows, np.clip(ob - 2, 0, w - 1)]
    # rows where a line does not step and the far side is not clear either
    clear_in = 1.0 - float(np.mean(~hit_in & (inside < f.floor)))
    clear_out = 1.0 - float(np.mean(~hit_out & (beyond < f.floor)))
    # the band's own level, for the crossings: its interior median
    mid = np.array([np.median(sv[r, a[r]:max(b[r], a[r] + 1)]) for r in range(h)])
    level = float(np.median(mid))
    x_in = np.array([_step_crossing(sv[r], int(xb[r]), level, False) if hit_in[r] else np.nan
                     for r in range(h)])
    x_out = np.array([_step_crossing(sv[r], int(ob[r]), level, True) if hit_out[r] else np.nan
                      for r in range(h)])
    return dict(interior=interior, clear_in=clear_in, clear_out=clear_out, level=level,
                x_in=x_in, x_out=x_out)


def _band(c: dict[str, Any], sv: np.ndarray, f: _Frame, hin: np.ndarray,
          hout: np.ndarray) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Accept or reject one band candidate; (answer, debug).

    Both lines must be what a gate edge is (section 5): near-vertical and
    straight, each measured on its own supporting rows in the middle 70% of
    the height, exactly as a line at the border is.
    """
    h = sv.shape[0]
    m = _band_measure(c, sv, f, hin, hout)
    dbg = dict(x0=c["j"] + 1, o0=c["k"] + 1, union=round(c["union"], 3),
               s_in=round(c["s_in"], 3), s_out=round(c["s_out"], 3),
               interior=round(m["interior"], 3), clear_in=round(m["clear_in"], 3),
               clear_out=round(m["clear_out"], 3))
    why = None
    if m["interior"] < BAND_ROWS:
        why = "interior"
    elif min(m["clear_in"], m["clear_out"]) < BAND_CLEAR:
        why = "clear"
    if why:
        dbg["why"] = why
        return None, dbg
    rows = np.arange(h, dtype=float)
    sel = (rows >= MID_ROWS * h) & (rows <= (1.0 - MID_ROWS) * h)
    mids = {name: np.where(sel, xs, np.nan) for name, xs in (("in", m["x_in"]),
                                                             ("out", m["x_out"]))}
    if min(np.isfinite(v).sum() for v in mids.values()) < 4:
        dbg["why"] = "rows"
        return None, dbg
    # Two models of "straight": each line on its own, or both sharing one
    # tilt. The second steadies a line seen in a few rows, whose own tilt is
    # poorly measured (b0921c_49: 18% of rows, 3.2 columns on its own); the
    # first lets two gate edges differ in lean (b0921a_01). Either will do;
    # a line that fits neither is not a gate edge.
    sharp = {"in": _sharp_rows(sv, m["x_in"], m["level"]),
             "out": _sharp_rows(sv, m["x_out"], m["level"], outward=True)}
    fits = {name: _straightness(rows, xs, sharp[name], h)
            for name, xs in (("in", m["x_in"]), ("out", m["x_out"]))}
    # the shared tilt, on the sharp rows of each line where it has enough
    use = {name: np.where(sharp[name], v, np.nan)
           if np.isfinite(np.where(sharp[name], v, np.nan)).sum() >= CLEAN_MIN else v
           for name, v in mids.items()}
    oki, oko = np.isfinite(use["in"]), np.isfinite(use["out"])
    js, jc_in, jc_out = _joint_fit(rows[oki], use["in"][oki], rows[oko], use["out"][oko])
    j_in = float(np.median(np.abs(use["in"][oki] - (js * rows[oki] + jc_in))))
    j_out = float(np.median(np.abs(use["out"][oko] - (js * rows[oko] + jc_out))))
    own_ok = (max(fits["in"][3], fits["out"][3]) <= TILT_MAX
              and max(fits["in"][2], fits["out"][2]) <= RESID_MAX)
    joint_ok = abs(js) * h <= TILT_MAX and max(j_in, j_out) <= RESID_MAX
    dbg.update(tilt_in=round(fits["in"][3], 2), resid_in=round(fits["in"][2], 3),
               tilt_out=round(fits["out"][3], 2), resid_out=round(fits["out"][2], 3),
               sharp=[fits["in"][4], fits["out"][4]],
               joint_tilt=round(js * h, 2), joint_resid=[round(j_in, 3), round(j_out, 3)])
    x = float(np.nanmedian(m["x_in"]))
    outer = float(np.nanmedian(m["x_out"]))
    width = x - outer
    dbg["width"] = round(width, 2)
    if not (own_ok or joint_ok):
        why = "straight"
    elif not BAND_MIN <= width <= BAND_MAX:
        why = "width"
    if why:
        dbg["why"] = why
        return None, dbg
    xi = m["x_in"]
    n = int(np.isfinite(xi).sum())
    spread = float(np.nanpercentile(xi, 75) - np.nanpercentile(xi, 25))
    half = max(0.35, 1.5 * spread / np.sqrt(n) + 0.25)
    dbg.update(x=round(x, 2), outer=round(outer, 2), n_rows=n)
    c0, slope = fits["in"][0], fits["in"][1]
    return dict(x=x, outer=outer, half=half, slope=slope, c_in=c0, union=c["union"],
                s_in=c["s_in"], s_out=c["s_out"]), dbg


def _side(sv: np.ndarray, f: _Frame) -> tuple[Side, dict[str, Any]]:
    h, w = sv.shape
    base = f.base
    d, sd, baselike = _steps(sv, f, ZONE)
    thr = np.maximum(STEP_REL * base, STEP_SIGMA * sd)
    vote = (d >= thr[None, :]) & baselike
    cands = _candidates(vote)
    for c in cands:
        _measure(c, sv, f, d, sd, vote)
        c["rule"] = _rule(c)
    border_base = float(np.mean(sv[:, 0] >= f.floor))
    dbg: dict[str, Any] = {
        "border_base": round(border_base, 3),
        "cands": [[c["j"] + 1, round(c["strong"], 2), round(c["weak"], 2),
                   round(c["band"], 2), round(c["plateau_rows"], 2), c["rule"]]
                  for c in cands if c["strong"] >= 0.3 or c["rule"]][:8]}
    # Full rules before the hidden one; most support first; a near tie goes to
    # the innermost, which is where this frame's picture begins (r0911_17:
    # base, a sliver, base, picture). A line that proves bent or leaning gives
    # way to the next.
    # The band first: two parallel lines with base between them, the
    # neighbour's picture beyond. When one holds, this frame's picture starts
    # at its inner line whatever the border shows (r0911_17: base, a sliver,
    # base, picture).
    dout, band_side = _out_steps(sv, f, ZONE)
    vout = (dout >= max(STEP_REL * base, STEP_SIGMA * f.sigma / np.sqrt(VSMOOTH))) & band_side
    hin, hout = _tilted(vote), _tilted(vout)
    ein, eout = _tilted(vote, dilate=False), _tilted(vout, dilate=False)
    for bc in _band_candidates(hin, hout, ein, eout)[:BAND_TRY]:
        ans, bd = _band(bc, sv, f, hin, hout)
        dbg.setdefault("bands", []).append(bd)
        if ans is None:
            continue
        x, half = ans["x"], ans["half"]
        conf = float(np.clip(0.75 * ans["union"], 0.0, 1.0))
        note = (f"band {ans['x'] - ans['outer']:.1f} wide: this line steps in "
                f"{ans['s_in']:.0%} of rows, the neighbour's in {ans['s_out']:.0%}, "
                f"one of them in {ans['union']:.0%}; base between them in every row")
        return Side(EDGE, x=x, lo=x - half, hi=x + half, conf=conf, outer=ans["outer"],
                    x_top=ans["c_in"], x_bottom=ans["c_in"] + ans["slope"] * (h - 1),
                    note=note), dbg
    good = [c for c in cands if c["rule"]]
    good.sort(key=lambda c: (c["rule"] == "hidden",
                             -round((c["strong"] + c["weak"]) / 0.03), -c["j"]))
    rows = np.arange(h, dtype=float)
    for c in good:
        xs, level = _crossings(sv, c, base)
        n = int(np.isfinite(xs).sum())
        if n < max(8, 0.1 * h):
            continue
        # tilt and straightness from the middle rows: a gate's corners are
        # rounded (r0911_19: the top 40 rows sit 2-3 columns further in)
        mid = np.where((rows >= MID_ROWS * h) & (rows <= (1.0 - MID_ROWS) * h), xs, np.nan)
        if np.isfinite(mid).sum() < 4:
            continue
        c0, m, resid, tilt, n_sharp = _straightness(rows, xs, _sharp_rows(sv, xs, level), h)
        x = float(np.nanmedian(xs))
        cand_dbg = dict(x0=c["j"] + 1, x=round(x, 2), rule=c["rule"], resid=round(resid, 3),
                        fit_tilt=round(tilt, 2), n_rows=n, n_sharp=n_sharp,
                        strong=round(c["strong"], 3),
                        weak=round(c["weak"], 3), neg=round(c["neg"], 3),
                        clear=round(c["clear"], 3), level=round(level / base, 3))
        if tilt > TILT_MAX or resid > RESID_MAX:
            dbg.setdefault("rejected", []).append(cand_dbg)
            continue
        dbg.update(cand_dbg)
        if x < 0.5:
            break                            # under half a column: the border
        if c["rule"] == "float" and x > GAP_MAX:
            return Side(REFUSE, note=(f"straight base-level strip ending at {x:.1f}, clear "
                                      "of the border: a gap that far in, or a black object")), dbg
        spread = float(np.nanpercentile(xs, 75) - np.nanpercentile(xs, 25))
        # the median's own scatter, plus what the level choice can shift it;
        # a plateau too thin to measure its own level borrows the frame's
        half = max(0.35, 1.5 * spread / np.sqrt(n) + 0.25) + (0.2 if c["j"] < THIN_X else 0.0)
        conf = float(np.clip(0.5 * c["strong"] + 0.5 * c["weak"], 0.0, 1.0))
        if c["rule"] == "float":
            conf *= 0.75                     # a black object looks the same
        elif c["rule"] == "hidden":
            conf = HIDDEN_CONF
            half += 0.5
        outer = _plateau_start(sv, c, f.floor) if c["rule"] == "float" else None
        note = {"touch": f"line through {c['strong']:.0%} of rows, steps down in {c['weak']:.0%}",
                "float": (f"line through {c['strong']:.0%} of rows; plateau clear of "
                          "the border, the neighbour beyond it"),
                "hidden": (f"step in only {c['strong']:.0%} of rows, the rest as clear as "
                           "base on both sides; straight, no row against it")}[c["rule"]]
        return Side(EDGE, x=x, lo=x - half, hi=x + half, conf=conf, outer=outer,
                    x_top=c0, x_bottom=c0 + m * (h - 1), note=note), dbg
    # no line: is the border picture?
    if border_base <= BORDER_BASE_MAX:
        conf = 0.5 + 0.5 * float(np.clip(1.0 - border_base / BORDER_BASE_MAX, 0.0, 1.0))
        return Side(PICTURE_TO_BORDER, conf=conf,
                    note=f"border column at base level in only {border_base:.0%} of rows"), dbg
    # At base level in most rows. Base is uniform down its length: a border
    # column with texture is picture however bright it is.
    spread = _spread(sv[:, 0]) / base
    noise = f.sigma / np.sqrt(VSMOOTH) / base
    dbg["border_spread"] = round(spread, 4)
    if spread > max(TEXTURE_MIN, TEXTURE_SIGMA * noise):
        return Side(PICTURE_TO_BORDER, conf=0.7,
                    note=f"border column bright but textured ({spread:.1%} spread)"), dbg
    strong_rejected = [r for r in dbg.get("rejected", []) if r["strong"] >= FLOAT_RULE[0]]
    if strong_rejected:
        r = strong_rejected[0]
        return Side(REFUSE, note=(f"border at base level and a straight line at {r['x']:.1f} "
                                  f"that leans {r['fit_tilt']:+.1f} / scatters "
                                  f"{r['resid']:.2f}")), dbg
    return Side(PICTURE_TO_BORDER, conf=0.25,
                note=f"border uniform at base level in {border_base:.0%} of rows, "
                     "no straight step"), dbg


def _mirror(side: Side, w: int) -> Side:
    def m(v: float | None) -> float | None:
        return None if v is None else w - v
    if side.state == EDGE:
        side.x, side.lo, side.hi = m(side.x), m(side.hi), m(side.lo)
        side.outer, side.x_top, side.x_bottom = m(side.outer), m(side.x_top), m(side.x_bottom)
    return side


# --------------------------------------------------------------------------
# the frame


def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    rgb = np.asarray(image, dtype=np.float64)[..., :3]
    rgb = rgb[TRIM_ROWS:rgb.shape[0] - TRIM_ROWS]
    h, w, _ = rgb.shape
    s = rgb.sum(axis=2)
    gate = _gate_columns(rgb)
    debug: dict[str, Any] = {"gate_cols": int(gate.sum())}
    # The film's cut end is a soft column or two brighter than any film but
    # not flagged as gate (r0909_13: column 150 reads 536 against gate 540):
    # keep GATE_MARGIN columns of it out of the base estimate and the search.
    near_gate = gate.copy()
    for k in range(1, GATE_MARGIN + 1):
        near_gate[k:] |= gate[:-k]
        near_gate[:-k] |= gate[k:]
    film = ~near_gate
    if film.sum() < 8:
        # Nothing to compare the neutral flat field with. On C-41 that is the
        # gate (base is orange); a blank B&W frame is neutral and flat too.
        if ctx.get("film_type") == "bw":
            why = "neutral and flat everywhere: empty gate or blank B&W, no film to compare"
            return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why), debug)
        return EdgeResult(Side(NO_FILM, conf=0.6), Side(NO_FILM, conf=0.6), debug)

    sv = _vsmooth(s)
    sigma = _noise(s[:, film])
    colmed = _running_median(np.median(sv, axis=0))
    ref = int(np.argmax(np.where(film, colmed, -np.inf)))
    base = float(colmed[ref])
    tol = float(np.clip(BASE_SPREAD * _spread(sv[:, ref]) / base, BASE_TOL, BASE_TOL_MAX))
    f = _Frame(sv, base, tol, sigma)
    debug.update(base=round(base, 2), tol=round(tol, 3), sigma=round(sigma, 3))

    # A negative's densest full-height area still passes a tenth of base; a
    # full-height strip near black means the pixels are a positive (x0920_01-09
    # are), where base is the darkest thing and nothing below holds.
    darkest = float(np.min(colmed[film])) / base
    debug["darkest"] = round(darkest, 4)
    if darkest < NOT_A_NEGATIVE:
        why = f"a full-height strip at {darkest:.1%} of the brightest: not a negative"
        return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why), debug)

    if np.mean(sv[:, film] >= f.floor) >= ALL_BASE_FRAC:
        blank = [Side(NO_FILM, conf=0.7, note="empty gate beside a blank frame")
                 if near_gate[k] else Side(ALL_BASE, conf=0.7) for k in (0, w - 1)]
        return EdgeResult(blank[0], blank[1], debug)

    sides: dict[str, Side] = {}
    for name, flip in (("left", False), ("right", True)):
        if (gate[::-1] if flip else gate)[:2].any():
            sides[name] = Side(NO_FILM, conf=0.9, note="empty gate at the border")
            continue
        g = near_gate[::-1] if flip else near_gate
        a = sv[:, ::-1] if flip else sv
        stop = int(np.argmax(g)) if g.any() else w       # search stops at the gate
        if stop < 8:
            sides[name] = Side(NO_FILM, conf=0.7, note="empty gate within a few columns")
            continue
        side, dbg = _side(np.ascontiguousarray(a[:, :stop]), f)
        debug[name] = dbg
        sides[name] = _mirror(side, w) if flip else side

    left, right = sides["left"], sides["right"]
    if (left.state == EDGE and right.state == EDGE and left.x is not None
            and right.x is not None and right.x - left.x < FRAME_MIN):
        # both cannot be real; keep the one whose plateau reaches the border
        def rank(s: Side) -> tuple[int, float]:
            return (int(s.outer is None), round(s.conf, 3))
        if rank(left) == rank(right):          # no reason to prefer either
            why = f"edges at {left.x:.1f} and {right.x:.1f} cannot both be real"
            sides = {"left": Side(REFUSE, note=why), "right": Side(REFUSE, note=why)}
            debug["conflict"] = "both"
        else:
            loser = "left" if rank(left) < rank(right) else "right"
            sides[loser] = Side(PICTURE_TO_BORDER, conf=0.4,
                                note=f"edge at {sides[loser].x:.1f} contradicts the frame width")
            debug["conflict"] = loser
    for name, other in (("left", "right"), ("right", "left")):
        s_, o = sides[name], sides[other]
        b = s_.base_width(name, w)
        if b is not None and b > IMPLIES_BORDER and o.state == PICTURE_TO_BORDER:
            o.conf = max(o.conf, 0.8 * s_.conf)
            o.note += f"; implied by {b:.0f} columns of base at the {name}"
    return EdgeResult(sides["left"], sides["right"], debug)
