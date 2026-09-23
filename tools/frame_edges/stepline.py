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

from typing import Any

import numpy as np

from .sides import (
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
HIDDEN_RESID = 0.30
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
    q = np.percentile(s, [25, 75], axis=0)
    flat = (q[1] - q[0]) / np.maximum(lvl, 1e-9) <= 0.05
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
    else:
        c["plateau_rows"] = 0.0
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
        return None
    s, w, b = FLOAT_RULE
    if c["strong"] >= s and c["weak"] >= w and c["band"] >= b:
        return "float"
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
    """Robust line x = m*r + c through the crossings: LSQ after a MAD cut."""
    ok = np.isfinite(xs)
    r, x = rows[ok], xs[ok]
    if len(x) < 4:
        return float(np.nanmedian(xs)), 0.0
    m, c = np.polyfit(r, x, 1)
    res = x - (m * r + c)
    mad = np.median(np.abs(res)) + 1e-6
    keep = np.abs(res) <= 3.0 * 1.4826 * mad + 0.25
    if keep.sum() >= 4:
        m, c = np.polyfit(r[keep], x[keep], 1)
    return float(c), float(m)


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
        c0, m = _fit(rows, mid)
        resid = float(np.nanmedian(np.abs(mid - (c0 + m * rows))))
        tilt = m * h
        x = float(np.nanmedian(xs))
        cand_dbg = dict(x0=c["j"] + 1, x=round(x, 2), rule=c["rule"], resid=round(resid, 3),
                        fit_tilt=round(tilt, 2), n_rows=n, strong=round(c["strong"], 3),
                        weak=round(c["weak"], 3), neg=round(c["neg"], 3),
                        clear=round(c["clear"], 3), level=round(level / base, 3))
        limit = HIDDEN_RESID if c["rule"] == "hidden" else RESID_MAX
        if abs(tilt) > TILT_MAX or resid > limit:
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
