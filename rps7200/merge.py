"""Laying one pass's infrared onto another pass of the same frame.

Why a frame would be scanned twice at all: on slide film blue comes back
17.7-20.4x brighter in an RGBI pass than in an RGB one at the same exposure,
against ~5 on colour negative. Metering takes blue down to the scanner's
shortest exposure and its lowest gain, and on a bright slide that is still
not far enough -- 2026-10-03, a blue sky clipped in the RGBI pass and drew
lines with it, while an RGB pass of the same slide, taken straight after, had
none. So a slide scanned with infrared takes both: its colour from the RGB
pass and its infrared from the RGBI one (`DirectScanner.wants_colour_pass`).

**The two passes do not land on the same pixels, and not by one shift.** A
dust record laid on a picture a pixel or two away from it marks the wrong
pixels, so the infrared plane is registered before it is used. What there is
to register, measured on the twelve slide pairs of 2026-10-03 at 3600 dpi:

- **Both passes read top-down:** 1-3 px apart in y, under 2 in x, and not
  uniformly -- the y offset curves down the frame, and the x offset tilts
  across it by an amount that grows and shrinks down it.
- **One pass read bottom-up** (turned upright by the decode,
  `rps7200.direction`): 21-31 px apart in y and 1-10 in x. The carriage does
  not travel the two directions at the same speed line for line: the y
  offset steps by up to 1.3 px within 64-128 rows, the same at both edges of
  the frame, and the x offset bends across the width. Nine of those twelve
  pairs were of this kind; one affine map left 1.2-1.9 px between them, and
  they went out without infrared.

So the map is one a line scanner needs, in two levels:

- **Bands of rows** (~230 rows apart at 3600 dpi): in each, the y offset is a
  straight line across x and the x offset a parabola across x, fitted to a
  row of tiles; between band centres the coefficients are interpolated.
- **A fine correction in y every `FINE_STEP` rows**: wide, short strips
  across the frame, compared once the bands have done their work. The steps
  the carriage takes are shorter than any band, and the same right across
  the frame, so a strip the width of the frame measures them well; each
  row's correction is the median over the strip's segments, so flat sky in
  one segment costs nothing. Not in x: x changes smoothly down the frame,
  which the bands follow, and one value a row for it pushed the frame's
  edges out where the bands tilt across it (0.12 px on a synthetic pair),
  for 0.01-0.03 px gained on the real ones.

How the tiles are read and the map is judged:

- **Red is what is matched.** Every visible channel is in both passes, and
  blue is the one that differs between them -- it is the reason for the
  second pass.
- **A shift per tile, by phase correlation**, sub-pixel by a parabola through
  the peak. A tile whose peak is weak -- flat sky, a blank margin -- says
  nothing and is left out; a band or row without enough takes its value from
  those either side.
- **Judged on tiles it was not fitted to.** After the warp the passes are
  compared again on a grid staggered half a step from the fitting grid, and a
  map that leaves more than `MAX_AFTER_PX` there is not used.
- **The warp is bilinear**, in strips of rows, so a 3600 dpi plane is never
  held as a full float grid of coordinates.

Pure numpy, on pixels either pass may hold; it touches no device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: The side of a tile at full size, in pixels. Smaller passes get smaller
#: tiles (`_tile_side`).
TILE = 256
#: Tiles across each band of rows: enough to fit a parabola with some to
#: spare.
COLUMNS = 9
#: The most bands a pass is cut into. At 3600 dpi (3443 rows) tiles of
#: `TILE` give about fifteen, a band every ~230 rows.
MAX_BANDS = 25
#: Rows between fine corrections, and the half-height of the strip each is
#: measured on is `FINE_HALF`. The carriage's steps were seen over 64-128
#: rows; 32 samples them twice over.
FINE_STEP = 32
FINE_HALF = 24
#: Segments a fine strip is cut into across the frame.
FINE_SEGMENTS = 4
#: Below this a tile's correlation peak is noise rather than a match.
#: Measured on the 2026-10-03 slides, where matched tiles read 0.2-0.45 at
#: 256 px and flat ones under 0.1.
MIN_PEAK = 0.15
#: Fewer matched tiles than this across the whole pass, and nothing is fitted.
MIN_TILES = 6
#: A registration whose tiles disagree with its own bands by more than this
#: (median, in pixels) is not used.
MAX_RESIDUAL_PX = 1.0
#: Nor one that leaves more than this between the passes once warped,
#: measured on tiles the fit never saw (median, in pixels).
MAX_AFTER_PX = 0.5
#: Nor one asking for more than this anywhere in the frame. A bottom-up pass
#: sat up to 31 px from a top-down one; tens more is another picture, and
#: laying its infrared on this one would mark dust that is not there.
MAX_SHIFT_PX = 48.0
#: Rows warped at a time.
STRIP_ROWS = 256


@dataclass
class Registration:
    """How the infrared pass sits against the colour pass.

    ``centres`` are band centre rows and ``coeffs`` one row ``(a, b, c, d, e)``
    per band; ``fine_rows`` are rows with a fine correction ``fine`` in y
    each. At row y and column x -- everything interpolated between its rows
    and held beyond the end ones --

        dy = a + b*x + fine        dx = c + d*x + e*x*x

    is the distance the colour pass's picture sits from the infrared pass's.
    ``ok`` is False, with ``reason``, where the map is not to be trusted, and
    then nothing should be warped with it.
    """

    centres: np.ndarray
    coeffs: np.ndarray
    tiles: int
    of: int
    residual_px: float
    worst_px: float
    ok: bool
    reason: str | None = None
    #: Per matched tile: centre y, centre x, dy, dx, peak.
    points: list[tuple[float, float, float, float, float]] = field(
        default_factory=list)
    #: The tile side the map was measured with.
    side: int = TILE
    fine_rows: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fine: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @classmethod
    def flat(cls, dy: float = 0.0, dx: float = 0.0) -> Registration:
        """One shift for the whole pass -- a map with nothing to vary."""
        return cls(np.array([0.0]), np.array([[dy, 0.0, dx, 0.0, 0.0]]), 0, 0,
                   0.0, 0.0, True)

    @classmethod
    def from_record(cls, rec: dict[str, Any] | None) -> Registration | None:
        """The map a :meth:`record` describes, or None.

        None for a map that was refused, and for any record not written by
        this kind of map -- an entry filed while the merge was one affine
        map has no bands, and is measured again instead.
        """
        if not isinstance(rec, dict) or not rec.get("ok"):
            return None
        try:
            bands = np.asarray(rec["bands"], np.float64)
            fine_rows = np.asarray(rec.get("fine_rows") or [], np.float64)
            fine = np.asarray(rec.get("fine") or [], np.float64)
        except (KeyError, TypeError, ValueError):
            return None
        if (bands.ndim != 2 or bands.shape[1] != 6 or not len(bands)
                or fine.ndim != 1 or len(fine_rows) != len(fine)):
            return None
        return cls(bands[:, 0], bands[:, 1:], int(rec.get("tiles") or 0),
                   int(rec.get("of") or 0), float(rec.get("residual_px") or 0),
                   float(rec.get("worst_px") or 0), True,
                   fine_rows=fine_rows, fine=fine)

    def coefficients(self, ys: np.ndarray) -> np.ndarray:
        """``(len(ys), 5)``: each row's ``a, b, c, d, e``, fine part included."""
        ys = np.asarray(ys, np.float64)
        k = np.stack([np.interp(ys, self.centres, self.coeffs[:, i])
                      for i in range(5)], axis=-1)
        if len(self.fine_rows):
            k[:, 0] += np.interp(ys, self.fine_rows, self.fine)
        return k

    def at(self, y: float, x: float) -> tuple[float, float]:
        a, b, c, d, e = self.coefficients(np.array([float(y)]))[0]
        return float(a + b * x), float(c + d * x + e * x * x)

    def furthest(self, h: int, w: int) -> float:
        """The largest shift the map asks for anywhere in an h x w pass."""
        ys = np.concatenate([[0.0, h - 1.0], self.centres])
        return max(float(np.hypot(*self.at(y, x)))
                   for y in ys for x in (0.0, w / 2, w - 1.0))

    def record(self, shape: tuple[int, ...]) -> dict[str, Any]:
        """For a pass's meta: enough to redo the warp and to judge it."""
        h, w = shape[:2]
        ys = np.concatenate([[0.0, h - 1.0], self.centres])
        where = [self.at(y, x) for y in ys for x in (0.0, w / 2, w - 1.0)]

        # Enough digits that a map rebuilt from this (`from_record`) lays
        # the infrared within a count of where the measured one did: at
        # 0.001 px a speck's steep edge moved by 6.
        def sig(v: float) -> float:
            return float(f"{float(v):.10g}")

        return {
            "method": "row bands (dy linear, dx quadratic in x) plus a fine "
                      "y correction every few rows; phase-correlated red",
            "bands": [[round(float(c), 1)] + [sig(v) for v in k]
                      for c, k in zip(self.centres, self.coeffs)],
            "fine_rows": [int(r) for r in self.fine_rows],
            "fine": [round(float(g), 6) for g in self.fine],
            "tiles": self.tiles,
            "of": self.of,
            "residual_px": round(self.residual_px, 3),
            "worst_px": round(self.worst_px, 3),
            "shift_px": {
                "dy": [round(min(p[0] for p in where), 3),
                       round(max(p[0] for p in where), 3)],
                "dx": [round(min(p[1] for p in where), 3),
                       round(max(p[1] for p in where), 3)],
            },
            "ok": self.ok,
            "reason": self.reason,
        }


def tile_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """``(dy, dx, peak)``: how far ``a``'s picture sits from ``b``'s.

    So ``a[y, x] ~ b[y - dy, x - dx]``. Phase correlation through a Hanning
    window, sub-pixel by a parabola through the peak in each direction.
    ``peak`` is the normalised correlation there: near 1 for a clean match,
    under `MIN_PEAK` for none.
    """
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    a = a - a.mean()
    b = b - b.mean()
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    cross = np.fft.fft2(a * win) * np.conj(np.fft.fft2(b * win))
    cross /= np.abs(cross) + 1e-9
    c = np.fft.ifft2(cross).real
    y, x = np.unravel_index(int(np.argmax(c)), c.shape)
    h, w = c.shape

    def vertex(m: float, p: float, n: float) -> float:
        d = m - 2 * p + n
        return 0.0 if d == 0 else 0.5 * (m - n) / d

    dy = y + vertex(c[(y - 1) % h, x], c[y, x], c[(y + 1) % h, x])
    dx = x + vertex(c[y, (x - 1) % w], c[y, x], c[y, (x + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dy), float(dx), float(c[y, x])


def _tile_side(h: int, w: int) -> int:
    """`TILE`, or what fits a usable grid on a smaller pass. Even, >= 32."""
    side = min(TILE, h // 4, w // 4)
    return max(32, side - side % 2)


def _grid(h: int, w: int, side: int, stagger: bool = False
          ) -> tuple[np.ndarray, np.ndarray]:
    """Tile centres: rows (one per band) and columns. ``stagger`` moves
    both half a step, for tiles the fit never saw."""
    bands = int(np.clip(round(h / (0.9 * side)), 3, MAX_BANDS))
    cols = int(np.clip(round(w / (0.6 * side)), 3, COLUMNS))
    ys = np.linspace(side / 2, h - side / 2, bands)
    xs = np.linspace(side / 2, w - side / 2, cols)
    if stagger:
        ys = (ys[:-1] + ys[1:]) / 2
        xs = (xs[:-1] + xs[1:]) / 2
    # On whole-pixel tile corners, so a tile's centre is exactly its band's.
    return (np.round(ys - side / 2) + side / 2,
            np.round(xs - side / 2) + side / 2)


def _measure(colour: np.ndarray, other: np.ndarray, ys: np.ndarray,
             xs: np.ndarray, side: int, channel: int
             ) -> list[tuple[float, float, float, float, float]]:
    out = []
    for cy in ys:
        y0 = int(round(cy - side / 2))
        for cx in xs:
            x0 = int(round(cx - side / 2))
            dy, dx, peak = tile_shift(
                colour[y0:y0 + side, x0:x0 + side, channel],
                other[y0:y0 + side, x0:x0 + side, channel])
            if peak >= MIN_PEAK:
                out.append((y0 + side / 2, x0 + side / 2, dy, dx, peak))
    return out


def _poly(xs: np.ndarray, v: np.ndarray, degree: int) -> list[float]:
    """Least-squares polynomial in x, lowest power first, padded to
    ``degree + 1`` terms; fewer points than terms fit fewer powers."""
    use = min(degree, len(xs) - 1) if np.ptp(xs) > 0 else 0
    if use <= 0:
        terms = [float(np.median(v))]
    else:
        terms = [float(t) for t in np.polyfit(xs, v, use)[::-1]]
    return terms + [0.0] * (degree + 1 - len(terms))


def _band(mine: np.ndarray) -> tuple[list[float], list[float]]:
    """One band's ``(a, b)`` and ``(c, d, e)``, with one outlier rejection."""
    xs, dys, dxs = mine[:, 1], mine[:, 2], mine[:, 3]
    ab, cde = _poly(xs, dys, 1), _poly(xs, dxs, 2)
    if len(mine) >= 5:
        # A tile on a repeating texture can lock onto the wrong period and
        # bend its band's curve with it.
        miss = np.hypot(dys - (ab[0] + ab[1] * xs),
                        dxs - (cde[0] + cde[1] * xs + cde[2] * xs * xs))
        keep = miss <= max(0.5, 3 * float(np.median(miss)))
        if 3 <= keep.sum() < len(mine):
            ab = _poly(xs[keep], dys[keep], 1)
            cde = _poly(xs[keep], dxs[keep], 2)
    return ab, cde


def _fill(rows: np.ndarray, values: np.ndarray) -> np.ndarray | None:
    """NaN entries taken from the rows either side; None if all are NaN."""
    have = ~np.isnan(values)
    if not have.any():
        return None
    out = values.copy()
    out[~have] = np.interp(rows[~have], rows[have], values[have])
    return out


def _fine(colour: np.ndarray, other: np.ndarray, reg: Registration,
          channel: int) -> tuple[np.ndarray, np.ndarray]:
    """The fine correction in y: what the bands leave, row by row.

    Wide, short strips across the frame, compared after the bands' warp, so
    each sees only the little the bands missed. One value a row: the median
    over the strip's segments, of those whose peak counts.
    """
    h = min(colour.shape[0], other.shape[0])
    w = min(colour.shape[1], other.shape[1])
    margin = max(8, w // 40)
    seg = (w - 2 * margin) // FINE_SEGMENTS
    rows = np.arange(FINE_HALF, h - FINE_HALF + 1, FINE_STEP, dtype=np.float64)
    if len(rows) < 2 or seg < 64:
        return np.zeros(0), np.zeros(0)
    # A power-of-two width, centred in its segment: a strip 1228 wide took
    # 17 ms to correlate and one 1024 wide 8.3, and there are 400-odd.
    width = 1 << (seg.bit_length() - 1)
    starts = [margin + s * seg + (seg - width) // 2
              for s in range(FINE_SEGMENTS)]
    # The plane warped once and cut into strips: warping 400-odd windows one
    # by one cost four times as long at 3600 dpi.
    moved = warp(other[..., channel], reg, colour.shape[:2])
    gy = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        y0, y1 = int(r) - FINE_HALF, int(r) + FINE_HALF
        got = []
        for x0 in starts:
            dy, _, peak = tile_shift(colour[y0:y1, x0:x0 + width, channel],
                                     moved[y0:y1, x0:x0 + width])
            if peak >= MIN_PEAK and abs(dy) < FINE_HALF / 2:
                got.append(dy)
        if got:
            gy[i] = float(np.median(got))
    filled = _fill(rows, gy)
    if filled is None:
        return np.zeros(0), np.zeros(0)
    return rows, filled


def register(colour: np.ndarray, other: np.ndarray, channel: int = 0
             ) -> Registration:
    """Where ``colour``'s picture sits against ``other``'s.

    Both are passes of one frame, H x W x C; only ``channel`` of each is
    read, and only where both have pixels -- an RGB pass can be a line or two
    shorter than the RGBI one before or after it.
    """
    h = min(colour.shape[0], other.shape[0])
    w = min(colour.shape[1], other.shape[1])
    if h < 3 * 32 or w < 3 * 32:
        reg = Registration.flat()
        reg.ok, reg.reason = False, f"a {h}x{w} pass is too small to register"
        return reg
    side = _tile_side(h, w)
    ys, xs = _grid(h, w, side)
    of = len(ys) * len(xs)
    pts = _measure(colour, other, ys, xs, side, channel)
    if len(pts) < MIN_TILES:
        reg = Registration.flat()
        reg.tiles, reg.of, reg.side = len(pts), of, side
        reg.ok, reg.reason = False, (
            "no tile matched: nothing in the frame to align by" if not pts
            else f"only {len(pts)} of {of} tiles matched")
        return reg
    p = np.array(pts)
    coeffs = np.full((len(ys), 5), np.nan)
    for i, cy in enumerate(ys):
        mine = p[p[:, 0] == cy]
        if len(mine) == 0:
            continue
        ab, cde = _band(mine)
        if len(mine) < 3:
            # Too few to say how it varies across x: the level only, and
            # the rest from the bands either side.
            ab[1] = cde[1] = cde[2] = np.nan
        coeffs[i] = ab + cde
    for k in range(5):
        filled = _fill(ys, coeffs[:, k])
        coeffs[:, k] = 0.0 if filled is None else filled
    reg = Registration(ys.astype(np.float64), coeffs, len(pts), of, 0.0, 0.0,
                       True, points=list(pts),
                       side=side)
    miss = np.array([np.hypot(dy - reg.at(y, x)[0], dx - reg.at(y, x)[1])
                     for y, x, dy, dx, _ in pts])
    reg.residual_px = float(np.median(miss))
    reg.worst_px = float(miss.max())
    if reg.residual_px > MAX_RESIDUAL_PX:
        reg.ok = False
        reg.reason = (f"the tiles disagree with the map by "
                      f"{reg.residual_px:.2f} px (median)")
        return reg
    reg.fine_rows, reg.fine = _fine(colour, other, reg, channel)
    furthest = reg.furthest(h, w)
    if furthest > MAX_SHIFT_PX:
        reg.ok = False
        reg.reason = (f"the passes are {furthest:.1f} px apart somewhere; a "
                      f"bottom-up pass has been measured at up to 31")
    return reg


def warp(plane: np.ndarray, reg: Registration,
         shape: tuple[int, int] | None = None,
         rows: tuple[int, int] | None = None,
         cols: tuple[int, int] | None = None) -> np.ndarray:
    """``plane`` resampled onto the colour pass's pixels, bilinearly.

    Output pixel (y, x) takes ``plane`` at ``(y - dy, x - dx)``, the map's
    displacement there. ``shape`` is the colour pass's (H, W), which may
    differ from ``plane``'s by a line or two; samples past ``plane``'s edge
    take its edge. ``rows`` and ``cols`` warp only that window of the output.
    The result has ``plane``'s dtype.
    """
    src_h, src_w = plane.shape[:2]
    h, w = shape if shape is not None else (src_h, src_w)
    r0, r1 = rows if rows is not None else (0, h)
    c0, c1 = cols if cols is not None else (0, w)
    out = np.empty((r1 - r0, c1 - c0), dtype=plane.dtype)
    top = (float(np.iinfo(plane.dtype).max)
           if np.issubdtype(plane.dtype, np.integer) else None)
    xs = np.arange(c0, c1, dtype=np.float64)[None, :]
    for s in range(r0, r1, STRIP_ROWS):
        e = min(r1, s + STRIP_ROWS)
        rows_here = np.arange(s, e, dtype=np.float64)
        k = reg.coefficients(rows_here)
        ys = rows_here[:, None]
        sy = ys - (k[:, 0:1] + k[:, 1:2] * xs)
        sx = xs - (k[:, 2:3] + k[:, 3:4] * xs + k[:, 4:5] * xs * xs)
        sy = np.clip(sy, 0, src_h - 1)
        sx = np.clip(sx, 0, src_w - 1)
        y0 = np.minimum(np.floor(sy).astype(np.intp), src_h - 2)
        x0 = np.minimum(np.floor(sx).astype(np.intp), src_w - 2)
        fy = (sy - y0).astype(np.float32)
        fx = (sx - x0).astype(np.float32)
        p00 = plane[y0, x0].astype(np.float32)
        p10 = plane[y0 + 1, x0].astype(np.float32)
        p01 = plane[y0, x0 + 1].astype(np.float32)
        p11 = plane[y0 + 1, x0 + 1].astype(np.float32)
        v = ((p00 * (1 - fy) + p10 * fy) * (1 - fx)
             + (p01 * (1 - fy) + p11 * fy) * fx)
        if top is not None:
            v = np.clip(np.rint(v), 0, top)
        out[s - r0:e - r0] = v
    return out


def residual_after(colour: np.ndarray, other: np.ndarray, reg: Registration,
                   channel: int = 0) -> dict[str, float]:
    """What is left between the passes once ``other`` is warped by ``reg``.

    Measured on the warped pixels rather than predicted from the fit, on a
    grid staggered half a step from the one the bands were fitted on -- tiles
    they never saw -- and only those windows are warped.
    """
    h = min(colour.shape[0], other.shape[0])
    w = min(colour.shape[1], other.shape[1])
    side = reg.side
    ys, xs = _grid(h, w, side, stagger=True)
    left = []
    for cy in ys:
        y0 = int(round(cy - side / 2))
        for cx in xs:
            x0 = int(round(cx - side / 2))
            moved = warp(other[..., channel], reg, colour.shape[:2],
                         rows=(y0, y0 + side), cols=(x0, x0 + side))
            dy, dx, peak = tile_shift(
                colour[y0:y0 + side, x0:x0 + side, channel], moved)
            if peak >= MIN_PEAK:
                left.append(float(np.hypot(dy, dx)))
    if not left:
        return {"median_px": float("nan"), "worst_px": float("nan"),
                "tiles": 0}
    return {"median_px": round(float(np.median(left)), 3),
            "worst_px": round(float(max(left)), 3), "tiles": len(left)}


def merge_infrared(colour: np.ndarray, rgbi: np.ndarray
                   ) -> tuple[np.ndarray | None, dict[str, Any]]:
    """The colour pass with the RGBI pass's infrared registered onto it.

    Returns ``(merged, record)``: ``merged`` is H x W x 4, ``colour``'s
    shape and dtype, its first three planes ``colour``'s own. Where the
    passes cannot be registered with confidence it is None, and ``record``
    says why.
    """
    if rgbi.ndim != 3 or rgbi.shape[2] < 4:
        return None, {"ok": False, "reason": "the infrared pass has no infrared"}
    if colour.ndim != 3 or colour.shape[2] < 3:
        return None, {"ok": False, "reason": "the colour pass has no colour"}
    reg = register(colour, rgbi)
    after = None
    if reg.ok:
        after = residual_after(colour, rgbi, reg)
        if not after["tiles"]:
            reg.ok = False
            reg.reason = "nothing left to check the warp against"
        elif after["median_px"] > MAX_AFTER_PX:
            reg.ok = False
            reg.reason = (f"{after['median_px']:.2f} px left between the "
                          "passes after the warp (median)")
    record = reg.record(colour.shape)
    if after is not None:
        record["after_px"] = after
    if not reg.ok:
        return None, record
    return lay_on(colour, rgbi, reg), record


def lay_on(colour: np.ndarray, rgbi: np.ndarray, reg: Registration
           ) -> np.ndarray:
    """``colour``'s three planes and ``rgbi``'s infrared, warped by ``reg``.

    H x W x 4 in ``colour``'s shape and dtype. For a map already measured --
    `merge_infrared`'s own, or one rebuilt from an entry's record.
    """
    h, w = colour.shape[:2]
    ir = warp(rgbi[..., 3], reg, (h, w))
    if ir.dtype != colour.dtype:
        ir = ir.astype(colour.dtype)
    merged = np.empty((h, w, 4), dtype=colour.dtype)
    merged[..., :3] = colour[..., :3]
    merged[..., 3] = ir
    return merged
