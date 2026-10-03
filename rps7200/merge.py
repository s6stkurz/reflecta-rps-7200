"""Laying one pass's infrared onto another pass of the same frame.

Why a frame would be scanned twice at all: on slide film blue comes back
17.7-20.4x brighter in an RGBI pass than in an RGB one at the same exposure,
against ~5 on colour negative. Metering takes blue down to the scanner's
shortest exposure and its lowest gain, and on a bright slide that is still
not far enough -- 2026-10-03, a blue sky clipped in the RGBI pass and drew
lines with it, while an RGB pass of the same slide, taken straight after, had
none. So a slide scanned with infrared takes both: its colour from the RGB
pass and its infrared from the RGBI one (`DirectScanner.wants_colour_pass`).

**The two passes do not land on the same pixels.** Measured on that slide at
3600 dpi: the RGBI pass sat -0.68 to -1.82 px off in y and -0.62 to +0.11 px
in x, varying across the frame -- a shift with a slight turn, not one offset.
A dust record laid on a picture a pixel or two away from it marks the wrong
pixels, so the infrared plane is registered before it is used:

- **Red is what is matched.** Every visible channel is in both passes, and
  blue is the one that differs between them -- it is the reason for the
  second pass. Red was the channel the measurement above used.
- **A shift per tile, by phase correlation**, on a 5x5 grid across the frame,
  sub-pixel by a parabola through the peak. A tile whose peak is weak --
  flat sky, a blank margin -- says nothing and is left out.
- **One affine map fitted to the tiles**, dy and dx each linear in (y, x).
  On the slide above the fit left 0.12 px median, 0.40 px worst, and the
  dust the infrared plane saw then landed on the dust the RGB pass showed.
- **The warp is bilinear**, in strips of rows, so a 3600 dpi plane is never
  held as a full float grid of coordinates.

Pure numpy, on pixels either pass may hold; it touches no device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Tiles along each side of the frame.
GRID = 5
#: The side of a tile at full size, in pixels. Smaller passes get smaller
#: tiles (`_tile_side`); the grid stays 5x5.
TILE = 384
#: Below this a tile's correlation peak is noise rather than a match.
#: Measured on the slide in the module docstring, where matched tiles read
#: 0.3-0.9 and a flat one under 0.1.
MIN_PEAK = 0.15
#: Fewer matched tiles than this, and an affine is not fitted: two numbers
#: per tile against six unknowns wants more than the bare three to be
#: worth trusting.
MIN_TILES_AFFINE = 6
#: A registration whose tiles disagree with its own map by more than this
#: (median, in pixels) is not used. A real one left 0.12.
MAX_RESIDUAL_PX = 1.0
#: Nor one asking for more than this anywhere in the frame. The passes have
#: been measured under two pixels apart; tens of pixels is another picture,
#: or the same one turned, and laying its infrared on this one would mark
#: dust that is not there.
MAX_SHIFT_PX = 12.0
#: Rows warped at a time.
STRIP_ROWS = 256


@dataclass
class Registration:
    """How the infrared pass sits against the colour pass.

    ``affine`` is 2x3, in pixels: ``dy = a[0] @ (1, y, x)`` and likewise
    ``dx``, the distance the colour pass's picture sits from the infrared
    pass's at (y, x). ``ok`` is False, with ``reason``, where the map is not
    to be trusted, and then nothing should be warped with it.
    """

    affine: np.ndarray
    tiles: int
    of: int
    residual_px: float
    worst_px: float
    ok: bool
    reason: str | None = None
    #: Per matched tile: centre y, centre x, dy, dx, peak.
    points: list[tuple[float, float, float, float, float]] = field(
        default_factory=list)

    def at(self, y: float, x: float) -> tuple[float, float]:
        v = np.array([1.0, y, x])
        return float(self.affine[0] @ v), float(self.affine[1] @ v)

    def record(self, shape: tuple[int, ...]) -> dict[str, Any]:
        """For a pass's meta: enough to redo the warp and to judge it."""
        h, w = shape[:2]
        corners = [self.at(y, x) for y in (0, h - 1) for x in (0, w - 1)]
        return {
            "method": "affine from phase-correlated red tiles",
            "affine": [[round(float(v), 9) for v in row] for row in self.affine],
            "tiles": self.tiles,
            "of": self.of,
            "residual_px": round(self.residual_px, 3),
            "worst_px": round(self.worst_px, 3),
            "shift_px": {
                "dy": [round(min(c[0] for c in corners), 3),
                       round(max(c[0] for c in corners), 3)],
                "dx": [round(min(c[1] for c in corners), 3),
                       round(max(c[1] for c in corners), 3)],
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
    """`TILE`, or what fits a grid of them on a smaller pass. Even, >= 32."""
    side = min(TILE, h // 3, w // 3)
    return max(32, side - side % 2)


def _fit(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares affine through ``points`` and each point's miss."""
    m = np.c_[np.ones(len(points)), points[:, 0], points[:, 1]]
    a = np.vstack([np.linalg.lstsq(m, points[:, 2], rcond=None)[0],
                   np.linalg.lstsq(m, points[:, 3], rcond=None)[0]])
    miss = np.hypot(points[:, 2] - m @ a[0], points[:, 3] - m @ a[1])
    return a, miss


def register(colour: np.ndarray, other: np.ndarray, channel: int = 0,
             grid: int = GRID) -> Registration:
    """Where ``colour``'s picture sits against ``other``'s, as an affine map.

    Both are passes of one frame, H x W x C; only ``channel`` of each is
    read, and only where both have pixels -- an RGB pass can be a line or two
    shorter than the RGBI one before or after it.
    """
    h = min(colour.shape[0], other.shape[0])
    w = min(colour.shape[1], other.shape[1])
    side = _tile_side(h, w)
    nothing = np.zeros((2, 3))
    if h < 3 * 32 or w < 3 * 32:
        return Registration(nothing, 0, grid * grid, float("inf"),
                            float("inf"), False,
                            f"a {h}x{w} pass is too small to register")
    points = []
    for fy in np.linspace(0.08, 0.92, grid):
        for fx in np.linspace(0.08, 0.92, grid):
            y0 = min(max(0, int(h * fy) - side // 2), h - side)
            x0 = min(max(0, int(w * fx) - side // 2), w - side)
            ys, xs = slice(y0, y0 + side), slice(x0, x0 + side)
            dy, dx, peak = tile_shift(colour[ys, xs, channel],
                                      other[ys, xs, channel])
            if peak >= MIN_PEAK:
                points.append((y0 + side / 2, x0 + side / 2, dy, dx, peak))
    of = grid * grid
    if not points:
        return Registration(nothing, 0, of, float("inf"), float("inf"), False,
                            "no tile matched: nothing in the frame to align by")
    pts = np.array(points)
    if len(pts) < MIN_TILES_AFFINE:
        # Too few to fit a turn: one shift, the median, for the whole frame.
        affine = nothing.copy()
        affine[0, 0] = np.median(pts[:, 2])
        affine[1, 0] = np.median(pts[:, 3])
        miss = np.hypot(pts[:, 2] - affine[0, 0], pts[:, 3] - affine[1, 0])
    else:
        affine, miss = _fit(pts)
        # One pass of rejection: a tile on a repeating texture can lock onto
        # the wrong period and pull the whole map with it.
        keep = miss <= max(0.5, 3 * float(np.median(miss)))
        if keep.sum() >= MIN_TILES_AFFINE and not keep.all():
            pts = pts[keep]
            affine, miss = _fit(pts)
    reg = Registration(affine, len(pts), of, float(np.median(miss)),
                       float(miss.max()), True,
                       points=[(float(p[0]), float(p[1]), float(p[2]),
                                float(p[3]), float(p[4])) for p in pts])
    furthest = max(np.hypot(*reg.at(y, x))
                   for y in (0, h - 1) for x in (0, w - 1))
    if reg.residual_px > MAX_RESIDUAL_PX:
        reg.ok = False
        reg.reason = (f"the tiles disagree with any one map by "
                      f"{reg.residual_px:.2f} px (median)")
    elif furthest > MAX_SHIFT_PX:
        reg.ok = False
        reg.reason = (f"the passes are {furthest:.1f} px apart at a corner; "
                      "they have been measured under two")
    return reg


def warp(plane: np.ndarray, affine: np.ndarray,
         shape: tuple[int, int] | None = None,
         rows: tuple[int, int] | None = None,
         cols: tuple[int, int] | None = None) -> np.ndarray:
    """``plane`` resampled onto the colour pass's pixels, bilinearly.

    Output pixel (y, x) takes ``plane`` at ``(y - dy, x - dx)``, the affine's
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
        ys = np.arange(s, e, dtype=np.float64)[:, None]
        sy = ys - (affine[0, 0] + affine[0, 1] * ys + affine[0, 2] * xs)
        sx = xs - (affine[1, 0] + affine[1, 1] * ys + affine[1, 2] * xs)
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

    Measured on the warped pixels rather than predicted from the fit, and
    only over the tiles that matched, so it costs a few hundred thousand
    samples rather than a whole plane.
    """
    h, w = colour.shape[:2]
    side = _tile_side(min(h, other.shape[0]), min(w, other.shape[1]))
    left = []
    for cy, cx, *_ in reg.points:
        y0 = int(round(cy - side / 2))
        x0 = int(round(cx - side / 2))
        moved = warp(other[..., channel], reg.affine, (h, w),
                     rows=(y0, y0 + side), cols=(x0, x0 + side))
        dy, dx, peak = tile_shift(colour[y0:y0 + side, x0:x0 + side, channel],
                                  moved)
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
    says why -- the caller keeps the RGBI pass whole instead, which is what
    a frame was before there were two passes.
    """
    if rgbi.ndim != 3 or rgbi.shape[2] < 4:
        return None, {"ok": False, "reason": "the infrared pass has no infrared"}
    if colour.ndim != 3 or colour.shape[2] < 3:
        return None, {"ok": False, "reason": "the colour pass has no colour"}
    reg = register(colour, rgbi)
    record = reg.record(colour.shape)
    if not reg.ok:
        return None, record
    h, w = colour.shape[:2]
    ir = warp(rgbi[..., 3], reg.affine, (h, w))
    if ir.dtype != colour.dtype:
        ir = ir.astype(colour.dtype)
    merged = np.empty((h, w, 4), dtype=colour.dtype)
    merged[..., :3] = colour[..., :3]
    merged[..., 3] = ir
    record["after_px"] = residual_after(colour, rgbi, reg)
    return merged, record
