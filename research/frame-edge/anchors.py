"""Anchor edges: where each prescan's picture ends, read off the scan beside it.

    uv run python research/frame-edge/anchors.py                  # every frame with a hires scan
    uv run python research/frame-edge/anchors.py gold200_16 ...   # just these
    uv run python research/frame-edge/anchors.py --cache DIR      # keep the heavy part for reruns
    uv run python research/frame-edge/anchors.py --window         # where each scan's window falls

Forty-one prescans in the dataset were taken beside a higher-resolution scan of
the same aperture -- 600 to 3600 dpi, or a second 300 dpi pass. That scan sees
the base/picture boundary 2-12 times finer than the prescan does, so where it
puts the edge is evidence that owes nothing to a person squinting at the
prescan. This script turns it into prescan coordinates (``common.py``: a column
*boundary*, left x = columns of base, right base occupies ``[x, W)``).

Three steps, each checkable on its own:

1. **Register.** The scan is box-averaged onto the prescan's grid and the pair
   is matched with :func:`rps7200.uniformity.register`, at the reach the
   driver itself uses (``framing.SEARCH_MM``), so its confidence reads on the
   driver's scale -- and with the scan's rows both ways round, because some
   library scans came back reversed against their prescan (the byte-14 bit-0
   effect, ``docs/byte14-plan.md``). That is only the integer start. The fine
   fit is a forward model: every prescan pixel is predicted as the mean of the
   scan over its footprint, with the footprint's position a cubic in x plus a
   shear in y, and the parameters are those that maximise the channel-mean
   Pearson correlation. The cubic is not decoration: a straight scale-and-
   offset leaves a bow of ~0.4 columns between the middle and the ends of the
   frame, the same in every channel and in the top and bottom halves.
2. **Find the edge in the scan.** Base is the unexposed film: the brightest
   thing on a negative, uniform over the full height, no wider than the gap
   between two frames, and meeting the picture along a straight line. Per row,
   the edge is the 50% crossing between that row's base level and its picture
   level beside it, over the rows between 10% and 90% of the height (the
   camera gate's corners are rounded, so the extremes curl).
3. **Map it back** through the fitted model, row by row -- and then refit the
   model's offset on a window around the edge itself, because even the cubic
   is up to ~0.4 column out at the ends of some frames, which is exactly
   where edges are.

Every anchor is drawn to ``crops/anchors/<id>.png`` -- the scan's edge region
with its line, and the prescan's edge region x10 with the mapped line -- so it
can be checked by eye. An anchor is evidence; the picture is what makes it so.

``--window`` reads the stored fits for something else, and registers nothing:
where each scan's own first and last columns fall in its prescan's
(`window_report`). That is where `framing.SCAN_WINDOW_COLUMNS` comes from.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    EDGE, NO_FILM, PICTURE_TO_BORDER, UNREADABLE, load, manifest,
)
from rps7200 import framing, library, uniformity  # noqa: E402

LIBRARY = ROOT / "library"
OUT_JSON = HERE / "anchors.json"
CROPS = HERE / "crops" / "anchors"

#: `register`'s confidence is a z-score whose scale depends on the reach, so
#: the reach is the driver's own: `framing.reach_px` at a 300 dpi prescan's
#: width, which moves with `SEARCH_MM` -- 129 columns at its 11.0, 105 at the
#: 9.0 this line was first written against. This file used to work it out for
#: itself, as `SEARCH_MM / 25.4 * 300` -- 106 at 9.0, a column further than
#: the driver searched -- and the `anchors.json` committed with the study was
#: registered at that: its confidences are on that reach's scale, not today's.
REACH = framing.reach_px(int(framing.PRESCAN_COLUMNS))
#: Below this the integer match is not trusted and the frame is refused.
MIN_CONFIDENCE = 50.0
#: The fine fit's channel-mean Pearson correlation must reach this. Same
#: picture twice starts at 0.89 in `docs/frame-adjustment-findings.md`.
MIN_CORRELATION = 0.85
#: The effective scale must be within this fraction of dpi / 300.
SCALE_TOLERANCE = 0.02
#: Prescan columns examined each side, from the aperture's border inwards.
ZONE = 90
#: Rows used for the edge, as fractions of the height: the gate's corners curl.
ROW_SPAN = (0.10, 0.90)


# --------------------------------------------------------------------------
# registration


@dataclass
class Mapping:
    """Prescan boundary coordinates -> scan boundary coordinates.

    ``x_scan = n * (x_p + c0 + c1 t + c2 t^2 + c3 t^3 + shear v)`` with
    ``t = (x_p - W/2) / (W/2)`` and ``v = (y_p - H/2) / (H/2)``, and
    ``y_scan = ky * y_p + oy``. ``n`` is the nominal dpi / 300; everything
    else is fitted. ``flipped`` means the scan's rows run the other way.
    """

    n: float
    width: int
    height: int
    c: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    shear: float = 0.0
    ky: float = 1.0
    oy: float = 0.0
    flipped: bool = False

    def _t(self, xp: np.ndarray) -> np.ndarray:
        return (np.asarray(xp, dtype=np.float64) - self.width / 2) / (self.width / 2)

    def _v(self, yp: np.ndarray) -> np.ndarray:
        return (np.asarray(yp, dtype=np.float64) - self.height / 2) / (self.height / 2)

    def shift(self, xp: Any, yp: Any) -> np.ndarray:
        """Prescan columns to add to ``x_p`` before scaling: the fitted offset field."""
        t = self._t(xp)
        c0, c1, c2, c3 = self.c
        return c0 + c1 * t + c2 * t**2 + c3 * t**3 + self.shear * self._v(yp)

    def scan_x(self, xp: Any, yp: Any) -> np.ndarray:
        return self.n * (np.asarray(xp, dtype=np.float64) + self.shift(xp, yp))

    def scan_y(self, yp: Any) -> np.ndarray:
        return self.ky * np.asarray(yp, dtype=np.float64) + self.oy

    def prescan_y(self, ys: Any) -> np.ndarray:
        return (np.asarray(ys, dtype=np.float64) - self.oy) / self.ky

    def prescan_x(self, xs: Any, ys: Any) -> np.ndarray:
        """Invert :meth:`scan_x` at scan rows ``ys`` (monotone in x, so Newton converges)."""
        xs = np.asarray(xs, dtype=np.float64)
        yp = self.prescan_y(ys)
        xp = xs / self.n - self.c[0]
        for _ in range(30):
            f = self.scan_x(xp, yp) - xs
            h = 1e-3
            d = (self.scan_x(xp + h, yp) - self.scan_x(xp - h, yp)) / (2 * h)
            xp = xp - f / d
            if np.max(np.abs(f)) < 1e-6:
                break
        return xp

    def scale(self, xp: float) -> float:
        """d x_scan / d x_prescan at ``xp``: the local k."""
        h = 1e-3
        return float((self.scan_x(xp + h, 0.0) - self.scan_x(xp - h, 0.0)) / (2 * h))


def _interp_cum(cum: np.ndarray, pos: np.ndarray, axis: int) -> np.ndarray:
    """A cumulative sum read at fractional boundary positions (linear inside a sample)."""
    n = cum.shape[axis] - 1
    pos = np.clip(pos, 0.0, float(n))
    i0 = np.clip(np.floor(pos).astype(np.int64), 0, n - 1)
    f = pos - i0
    if axis == 0:
        a, b = cum[i0], cum[i0 + 1]
        return a + (b - a) * f[:, None, None]
    rows = np.arange(cum.shape[0])[:, None]
    a, b = cum[rows, i0], cum[rows, i0 + 1]
    return a + (b - a) * f[..., None]


class Model:
    """The scan, prepared so any :class:`Mapping` predicts a prescan cheaply."""

    def __init__(self, prescan: np.ndarray, n: float, scan: np.ndarray | None = None,
                 rows: np.ndarray | None = None, key: tuple[float, float] | None = None,
                 scan_shape: tuple[int, int] | None = None) -> None:
        """From the scan itself, or from its rows already averaged under ``key = (ky, oy)``.

        The second is what the cache keeps: enough to refit anything in x, nothing in y.
        """
        self.scan = scan
        self.prescan = prescan.astype(np.float64)
        self.n = n
        self.hp, self.wp = prescan.shape[:2]
        if scan is not None:
            self.hs, self.ws = scan.shape[:2]
        else:
            assert scan_shape is not None and rows is not None
            self.hs, self.ws = scan_shape
        self.cum_y: np.ndarray | None = None
        if scan is not None:
            self.cum_y = np.concatenate(
                [np.zeros((1, self.ws, 3)), np.cumsum(scan, axis=0, dtype=np.float64)], axis=0)
        self._rows_key: tuple[float, float] | None = key
        self._cum_x: np.ndarray | None = None
        if rows is not None:
            self._cum_x = np.concatenate(
                [np.zeros((self.hp, 1, 3)), np.cumsum(rows, axis=1, dtype=np.float64)], axis=1)

    def rows(self, m: Mapping) -> np.ndarray:
        """The scan averaged onto the prescan's rows under ``m``'s y mapping."""
        return np.diff(self._rows(m), axis=1)

    def _rows(self, m: Mapping) -> np.ndarray:
        """Cumulative sum along x of the scan averaged onto the prescan's rows."""
        key = (m.ky, m.oy)
        if key != self._rows_key or self._cum_x is None:
            if self.cum_y is None:
                raise ValueError("this model holds rows for one y mapping only")
            yb = m.scan_y(np.arange(self.hp + 1))
            y = _interp_cum(self.cum_y, yb, 0)
            rows = (y[1:] - y[:-1]) / np.maximum(np.diff(yb), 1e-9)[:, None, None]
            self._cum_x = np.concatenate(
                [np.zeros((self.hp, 1, 3)), np.cumsum(rows, axis=1)], axis=1)
            self._rows_key = key
        return self._cum_x

    def predict(self, m: Mapping, cols: slice = slice(None)) -> tuple[np.ndarray, np.ndarray]:
        """The prescan columns ``cols`` this scan implies under ``m``, and where defined."""
        lo, hi, _ = cols.indices(self.wp)
        cum_x = self._rows(m)
        yc = np.arange(self.hp) + 0.5
        xb = m.scan_x(np.arange(lo, hi + 1)[None, :], yc[:, None])       # (hp, n+1)
        x = _interp_cum(cum_x, xb, 1)
        width = np.maximum(np.diff(xb, axis=1), 1e-9)
        pred = (x[:, 1:] - x[:, :-1]) / width[..., None]
        yb = m.scan_y(np.arange(self.hp + 1))
        ok = (xb[:, :-1] >= 0) & (xb[:, 1:] <= self.ws)
        ok &= ((yb[:-1] >= 0) & (yb[1:] <= self.hs))[:, None]
        return pred, ok

    def correlation(self, m: Mapping, cols: slice = slice(None),
                    rows: slice = slice(None)) -> float:
        """Channel-mean Pearson correlation of prescan and prediction over ``cols`` x ``rows``."""
        lo, hi, _ = cols.indices(self.wp)
        pred, ok = self.predict(m, slice(lo, hi))
        mask = np.zeros_like(ok)
        mask[rows] = True
        mask[:4] = mask[-4:] = False      # the prescan's own first and last rows
        mask &= ok
        if mask.sum() < 50:
            return -1.0
        total = 0.0
        for c in range(3):
            a = self.prescan[:, lo:hi, c][mask]
            b = pred[..., c][mask]
            a = a - a.mean()
            b = b - b.mean()
            total += float(a @ b / np.sqrt((a @ a) * (b @ b) + 1e-12))
        return total / 3.0


def _nelder_mead(f: Any, x0: np.ndarray, step: np.ndarray,
                 iters: int = 600, xtol: float = 1e-4) -> tuple[np.ndarray, float]:
    """A plain Nelder-Mead (numpy only; scipy is not a dependency here)."""
    n = len(x0)
    pts = [np.asarray(x0, dtype=np.float64)]
    pts += [pts[0] + np.eye(n)[i] * step[i] for i in range(n)]
    vals = [float(f(p)) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if max(float(np.max(np.abs(p - pts[0]))) for p in pts[1:]) < xtol:
            break
        centre = np.mean(pts[:-1], axis=0)
        xr = centre + (centre - pts[-1])
        fr = float(f(xr))
        if fr < vals[0]:
            xe = centre + 2.0 * (centre - pts[-1])
            fe = float(f(xe))
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = centre + 0.5 * (pts[-1] - centre)
            fc = float(f(xc))
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [float(f(p)) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def _box(scan: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return scan
    h, w = scan.shape[0] // n * n, scan.shape[1] // n * n
    return scan[:h, :w].reshape(h // n, n, w // n, n, -1).mean(axis=(1, 3))


@dataclass
class Registration:
    ok: bool
    why: str
    n: float
    dy: int = 0
    dx: int = 0
    conf: float = 0.0
    conf_flipped: float = 0.0
    corr: float = 0.0
    corr_linear: float = 0.0
    mapping: Mapping | None = None
    bands: list[dict[str, float]] = field(default_factory=list)


def register(prescan: np.ndarray, scan: np.ndarray, dpi: int) -> tuple[Registration, Model]:
    n = dpi / 300.0
    ni = max(1, int(round(n)))
    small = _box(scan, ni)
    dy, dx, conf = uniformity.register(prescan, small, max_shift=REACH)
    fdy, fdx, fconf = uniformity.register(prescan, small[::-1], max_shift=REACH)
    flipped = fconf > conf
    if flipped:
        scan = scan[::-1]
        dy, dx, conf, fconf = fdy, fdx, fconf, conf
    reg = Registration(False, "", n, dy, dx, conf, fconf)
    model = Model(prescan, n, scan=scan)
    if conf < MIN_CONFIDENCE:
        reg.why = f"integer match confidence {conf:.1f} < {MIN_CONFIDENCE:.0f}"
        return reg, model
    # a[i, j] ~ b[i - dy, j - dx]: prescan boundary x <-> boxed boundary x - dx.
    m = Mapping(n, model.wp, model.hp, [-float(dx), 0.0, 0.0, 0.0], 0.0,
                ky=n, oy=-float(dy) * ni, flipped=flipped)
    inner = slice(8, model.wp - 8)

    def with_y(p: np.ndarray) -> Mapping:
        return Mapping(n, m.width, m.height, m.c, m.shear, float(p[0]), float(p[1]), flipped)

    def with_x(p: np.ndarray, cubic: bool = True) -> Mapping:
        c = [float(p[0]), float(p[1]), float(p[2]) if cubic else 0.0,
             float(p[3]) if cubic else 0.0]
        return Mapping(n, m.width, m.height, c, float(p[4]) if cubic else 0.0,
                       m.ky, m.oy, flipped)

    for _ in range(2):
        py, _ = _nelder_mead(lambda p: -model.correlation(with_y(p), inner),
                             np.array([m.ky, m.oy]), np.array([0.01 * n, 0.3 * n]))
        m = with_y(py)
        px, _ = _nelder_mead(lambda p: -model.correlation(with_x(p), inner),
                             np.array(m.c + [m.shear]), np.array([0.3, 0.3, 0.2, 0.2, 0.2]))
        m = with_x(px)
    lin, _ = _nelder_mead(lambda p: -model.correlation(with_x(p, cubic=False), inner),
                          np.array(m.c[:2] + [0.0, 0.0, 0.0]),
                          np.array([0.3, 0.3, 1e-9, 1e-9, 1e-9]))
    reg.corr_linear = model.correlation(with_x(lin, cubic=False), inner)
    reg.corr = model.correlation(m, inner)
    reg.mapping = m
    k_mid = m.scale(m.width / 2)
    if reg.corr < MIN_CORRELATION:
        reg.why = f"fine-fit correlation {reg.corr:.3f} < {MIN_CORRELATION}"
    elif abs(k_mid / n - 1.0) > SCALE_TOLERANCE:
        reg.why = f"scale {k_mid:.4f} is not dpi/300 = {n:.3f}"
    else:
        reg.ok = True
        reg.why = "ok"
    return reg, model


def local_offset(model: Model, m: Mapping, cols: slice, rows: slice = slice(None)) -> float:
    """Prescan columns the fitted model is off by over ``cols`` (a parabola on a 0.05 grid)."""
    offs = np.arange(-1.0, 1.0001, 0.05)
    vals = []
    for d in offs:
        mm = Mapping(m.n, m.width, m.height, [m.c[0] + d] + m.c[1:], m.shear, m.ky, m.oy,
                     m.flipped)
        vals.append(model.correlation(mm, cols, rows))
    v = np.asarray(vals)
    i = int(np.clip(np.argmax(v), 1, len(v) - 2))
    den = v[i - 1] - 2 * v[i] + v[i + 1]
    frac = 0.5 * (v[i - 1] - v[i + 1]) / den if den < 0 else 0.0
    return float(offs[i] + frac * 0.05)


#: Bands the local check fits the offset over, as prescan column boundaries.
BANDS = 16


@dataclass
class Prepared:
    """Everything the edge step needs from one frame, cheap to keep."""

    id: str
    entry: str
    dpi: int
    reg: Registration
    zones: dict[str, np.ndarray]          # side -> (H_scan, w, 3) float32, scan counts
    zone_x0: dict[str, int]               # side -> first scan column of that zone
    scan_shape: tuple[int, int]
    band_edges: list[int]
    band_offsets: list[list[float]]       # per band: all rows, top half, bottom half
    model: Model | None = None            # rows only: refits in x near an edge


def _zone_columns(m: Mapping, ws: int) -> dict[str, tuple[int, int]]:
    mid = m.height / 2
    n = int(np.ceil(m.n))
    left_end = int(np.ceil(float(m.scan_x(ZONE, mid)))) + 2 * n
    right_start = int(np.floor(float(m.scan_x(m.width - ZONE, mid)))) - 2 * n
    return {"left": (0, min(ws, left_end)), "right": (max(0, right_start), ws)}


def _load_scan(entry: str) -> np.ndarray:
    image, _ = library.corrected(LIBRARY / entry)
    return np.ascontiguousarray(image[..., :3], dtype=np.float32)


def prepare(record: dict[str, Any], cache: Path | None) -> Prepared:
    """Register one frame and cut out what the edge step needs; from ``cache`` if it has it."""
    fid = record["id"]
    hires = record["hires"]
    prescan = load(record).image
    if cache is not None:
        meta_path, arr_path = cache / f"{fid}.json", cache / f"{fid}.npz"
        if meta_path.exists() and arr_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            arrays = dict(np.load(arr_path))
            reg_d = dict(meta["reg"])
            mapping = Mapping(**reg_d.pop("mapping")) if reg_d.get("mapping") else None
            reg = Registration(**reg_d, mapping=mapping)
            zones = {s: arrays[s].astype(np.float32) * 65536.0 for s in ("left", "right")
                     if s in arrays}
            shape = (int(meta["scan_shape"][0]), int(meta["scan_shape"][1]))
            model = None
            if mapping is not None:
                if "rows" not in arrays:
                    # An older cache: the fit is kept, only the rows are recomputed.
                    scan = _load_scan(hires["entry"])
                    if mapping.flipped:
                        scan = scan[::-1]
                    arrays["rows"] = Model(prescan, mapping.n, scan=scan).rows(mapping)
                    del scan
                    np.savez(arr_path, **{k: v for k, v in arrays.items()})
                model = Model(prescan, mapping.n, rows=arrays["rows"].astype(np.float64),
                              key=(mapping.ky, mapping.oy), scan_shape=shape)
            return Prepared(fid, meta["entry"], meta["dpi"], reg, zones, meta["zone_x0"],
                            shape, meta["band_edges"], meta["band_offsets"], model)
    scan = _load_scan(hires["entry"])
    reg, model = register(prescan, scan, int(hires["dpi"]))
    del scan
    zones: dict[str, np.ndarray] = {}
    zone_x0: dict[str, int] = {}
    band_edges = [int(v) for v in np.linspace(0, model.wp, BANDS + 1).round()]
    band_offsets: list[list[float]] = []
    m = reg.mapping
    rows = None
    if m is not None:
        assert model.scan is not None
        for side, (a, b) in _zone_columns(m, model.ws).items():
            zones[side] = np.array(model.scan[:, a:b], dtype=np.float32)
            zone_x0[side] = a
        half = model.hp // 2
        for lo, hi in zip(band_edges[:-1], band_edges[1:]):
            cols = slice(lo, hi)
            band_offsets.append([local_offset(model, m, cols),
                                 local_offset(model, m, cols, slice(0, half)),
                                 local_offset(model, m, cols, slice(half, model.hp))])
        rows = model.rows(m).astype(np.float32)
    # Only the rows are kept from here: the full scan is hundreds of megabytes.
    model.scan = None
    model.cum_y = None
    prepared = Prepared(fid, hires["entry"], int(hires["dpi"]), reg, zones, zone_x0,
                        (model.hs, model.ws), band_edges, band_offsets,
                        model if m is not None else None)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        meta = {"reg": asdict(reg), "entry": hires["entry"], "dpi": int(hires["dpi"]),
                "zone_x0": zone_x0, "scan_shape": list(prepared.scan_shape),
                "band_edges": band_edges, "band_offsets": band_offsets}
        (cache / f"{fid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        arrays_out = {s: (z / 65536.0).astype(np.float16) for s, z in zones.items()}
        if rows is not None:
            arrays_out["rows"] = rows
        np.savez(cache / f"{fid}.npz", **arrays_out)
    return prepared


# --------------------------------------------------------------------------
# the edge, in the scan

#: A base column: at least this bright relative to the side's brightest ...
BASE_LEVEL = 0.90
#: ... and this uniform down the frame: (p90 - p10) / median, worst channel.
BASE_SPREAD = 0.15
#: A row counts only if its picture is at least this much darker than its base.
#: Low on purpose: a pale picture beside the base still draws a straight line,
#: and the median over rows and the straightness test do the rest.
MIN_CONTRAST = 0.05
#: Base on one side is the gap between frames: about 30 prescan columns at most
#: (pitch 455 columns, frame about 425). A base-level plateau reaching further in
#: is scene -- a wall, a sky -- or a leader, and says nothing about the edge.
MAX_BASE = 40.0
#: An edge must be readable in at least this fraction of the rows ...
MIN_COVERAGE = 0.35
#: ... and straight: median absolute residual from a line, in prescan columns.
MAX_RESIDUAL = 0.5


@dataclass
class SideAnchor:
    state: str
    x: float | None = None
    lo: float | None = None
    hi: float | None = None
    x_top: float | None = None
    x_bottom: float | None = None
    note: str = ""
    scan_x: float | None = None          # the edge in the scan's own boundary columns
    outer: float | None = None           # far side of the base, if picture shows beyond it
    coverage: float | None = None        # fraction of rows the edge was read in
    residual: float | None = None        # from a straight line, prescan columns
    row_iqr: float | None = None         # spread of the per-row edge, prescan columns
    width_10_90: float | None = None     # the transition, prescan columns
    reg_err: float | None = None         # the mapping's own uncertainty there
    contrast: float | None = None        # (base - picture) / base, median row
    prescan_50: float | None = None      # the same crossing read on the prescan (a check)
    x_global: float | None = None        # x through the global model alone
    candidate: float | None = None       # where a non-edge verdict found its line, if any
    d_edge: float | None = None          # local refit on the window holding the edge
    d_edge_top: float | None = None      # ... the same, top half of the rows
    d_edge_bottom: float | None = None   # ... and bottom half
    d_picture: float | None = None       # local refit on picture beside it, edge excluded
    points: list[tuple[float, float, float]] = field(default_factory=list)  # (y_scan, x_scan, x_p)


def _inward(zone: np.ndarray, side: str) -> np.ndarray:
    """The zone with column 0 at the aperture's border and columns running inwards."""
    return zone if side == "left" else zone[:, ::-1]


def _to_scan(u: float, side: str, x0: int, ws: int) -> float:
    """An inward boundary coordinate back to the scan's boundary columns."""
    return x0 + u if side == "left" else ws - u


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """``[start, end)`` of every run of True."""
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(mask.tolist() + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    return out


def _crossing(s: np.ndarray, near: float, half: float) -> float | None:
    """The boundary coordinate where ``s`` falls through ``half`` going inwards, nearest ``near``."""
    above = s >= half
    js = np.nonzero(above[:-1] & ~above[1:])[0] + 1        # s[j-1] >= half > s[j]
    if len(js) == 0:
        return None
    us = (js - 0.5) + (s[js - 1] - half) / np.maximum(s[js - 1] - s[js], 1e-9)
    return float(us[np.argmin(np.abs(us - near))])


def _row_bands(p: Prepared, side: str) -> tuple[np.ndarray, np.ndarray]:
    """The zone averaged into one row per prescan row, over ``ROW_SPAN``; and their scan y."""
    m = p.reg.mapping
    assert m is not None
    ni = max(1, int(round(m.n)))
    hs = p.scan_shape[0]
    a = _inward(p.zones[side], side)
    r0, r1 = int(ROW_SPAN[0] * hs), int(ROW_SPAN[1] * hs)
    groups = (r1 - r0) // ni
    rows = a[r0:r0 + groups * ni].reshape(groups, ni, a.shape[1], 3).mean(axis=1)
    return rows, r0 + (np.arange(groups) + 0.5) * ni


def reference(p: Prepared) -> np.ndarray:
    """Per channel, what "brightest on this film" reads in this scan: both zones' 99.5%.

    On a negative that is the base wherever base shows at all; where it does not, it
    is the thinnest picture, and the plateau test below can then be fooled -- which
    is why an edge must also be sharp, straight and read in most rows.
    """
    pix = [_row_bands(p, s)[0].reshape(-1, 3) for s in ("left", "right") if s in p.zones]
    return np.percentile(np.concatenate(pix), 99.5, axis=0)


def _local_fits(p: Prepared, side: str, x: float) -> dict[str, float]:
    """How far the global model is off right at this edge, two ways.

    ``d_edge`` refits the offset on a window holding the edge itself (up to 6 columns
    of base, 18 of picture); ``d_picture`` on 24 columns of picture beginning two
    columns in, which leaves the edge out and so has to be carried to it. Positive
    means the scan's content sits further out in the prescan than the model says,
    so the edge's prescan x is ``x_global - d``.
    """
    m = p.reg.mapping
    assert m is not None and p.model is not None
    wp = m.width
    u = x if side == "left" else wp - x

    def cols(a: float, b: float) -> slice:
        lo, hi = (a, b) if side == "left" else (wp - b, wp - a)
        return slice(int(np.clip(np.floor(lo), 0, wp)), int(np.clip(np.ceil(hi), 0, wp)))

    half = p.model.hp // 2
    edge = cols(u - 6, u + 18)
    return {"d_edge": local_offset(p.model, m, edge),
            "d_edge_top": local_offset(p.model, m, edge, slice(0, half)),
            "d_edge_bottom": local_offset(p.model, m, edge, slice(half, p.model.hp)),
            "d_picture": local_offset(p.model, m, cols(u + 2, u + 26))}


def find_edge(p: Prepared, side: str, prescan: np.ndarray, ref: np.ndarray) -> SideAnchor:
    m = p.reg.mapping
    assert m is not None
    n = m.n
    ni = max(1, int(round(n)))
    hs, ws = p.scan_shape
    x0 = p.zone_x0[side]
    rows, y_scan = _row_bands(p, side)
    groups, w = rows.shape[:2]
    med = np.median(rows, axis=0)                                          # (w, 3)
    lvl = (med / ref).mean(axis=1)
    q10, q90 = np.percentile(rows, [10, 90], axis=0)
    spread = ((q90 - q10) / np.maximum(med, 1e-9)).max(axis=1)
    basey = (lvl >= BASE_LEVEL) & (spread <= BASE_SPREAD)

    runs = [r for r in _runs(basey) if r[1] - r[0] >= max(2, ni // 2)]
    sliver = False
    if not runs and lvl[0] >= BASE_LEVEL:
        # Too narrow to be a plateau, but the outermost columns are at base level:
        # a sliver of base at the aperture. Its level comes from those columns, not
        # from a uniform run, so the rows and the line have to carry the verdict.
        k = 0
        while k < w and lvl[k] >= BASE_LEVEL:
            k += 1
        if k < 3 * ni:
            runs, sliver = [(0, k)], True
    if not runs:
        # No plateau anywhere near: the picture reaches the aperture.
        edge_lvl = float(np.median(lvl[: max(2, ni)]))
        note = (f"no uniform base plateau within {ZONE} prescan columns; "
                f"border at {edge_lvl:.2f} of the frame's brightest")
        beside = float(np.median(lvl[ni: 3 * ni + 1]))
        if lvl[0] - beside > 0.1:
            note += (f"; but the outermost scan column reads {lvl[0]:.2f} against {beside:.2f} "
                     f"beside it: base may show under part of a column (in the corners?)")
        return SideAnchor(PICTURE_TO_BORDER, note=note)
    start, end = runs[0]
    if end >= w - ni:
        return SideAnchor(UNREADABLE,
                          note=f"base-level and uniform from scan column {start} to the end "
                               f"of the {ZONE}-column zone: no boundary (picture at base level?)")
    depth = float(m.prescan_x(_to_scan(float(end), side, x0, ws), hs / 2))
    depth = depth if side == "left" else m.width - depth
    if depth > MAX_BASE:
        return SideAnchor(UNREADABLE,
                          note=f"a base-level uniform region reaches {depth:.0f} columns in: "
                               f"wider than any gap between frames, so scene (or a leader)")
    outer = None
    if start > ni:
        outer = float(m.prescan_x(_to_scan(float(start), side, x0, ws), hs / 2))
    sig = (rows / ref).mean(axis=2)                                        # (groups, w)
    # Where the edge is in each of eight row bands: it tilts and bows, so one
    # column for the whole height would put some rows' windows on the ramp.
    nb = 8
    centres: list[float] = []
    us: list[float] = []
    widths: list[float] = []
    for band in np.array_split(np.arange(groups), nb):
        prof = np.median(sig[band], axis=0)
        plateau = float(np.median(prof[start:end]))
        inner = float(np.median(prof[min(end + ni, w - 1): min(end + 3 * ni, w)]))
        if (plateau - inner) / max(plateau, 1e-9) < MIN_CONTRAST:
            continue
        u = _crossing(prof, float(end), 0.5 * (plateau + inner))
        if u is not None and abs(u - end) <= 3 * ni + 2:
            centres.append(float(np.mean(band)))
            us.append(u)
            t10 = _crossing(prof, u, inner + 0.9 * (plateau - inner))
            t90 = _crossing(prof, u, inner + 0.1 * (plateau - inner))
            if t10 is not None and t90 is not None:
                widths.append(abs(t90 - t10) / n)
    if not us:
        return SideAnchor(UNREADABLE, outer=outer,
                          note="a base plateau, but no row band falls off it into picture")
    mb = max(1, int(round(0.5 * n)))
    mp = max(1, int(round(0.75 * n)))
    pic_w = max(2, ni)
    col_base = float(np.median(sig[:, start:end]))
    xs_rows: list[float] = []
    ys_rows: list[float] = []
    contrasts: list[float] = []
    for g in range(groups):
        s = sig[g]
        u_ref = float(np.interp(g, centres, us))
        iu = int(np.floor(u_ref))
        b_lo, b_hi = max(start, iu - mb - 2 * ni), iu - mb
        bl = float(np.median(s[b_lo:b_hi])) if b_hi > b_lo else col_base
        if iu + mp + pic_w > w:
            continue
        pl = float(np.median(s[iu + mp: iu + mp + pic_w]))
        c = (bl - pl) / max(bl, 1e-9)
        if c < MIN_CONTRAST:
            continue
        lo_i = max(1, iu - mb - 1)
        hi_i = min(w, iu + mp + 2)
        u = _crossing(s[lo_i - 1:hi_i], u_ref - (lo_i - 1), 0.5 * (bl + pl))
        if u is None:
            continue
        xs_rows.append(_to_scan(u + lo_i - 1, side, x0, ws))
        ys_rows.append(float(y_scan[g]))
        contrasts.append(c)
    u_col = float(np.median(us))
    coverage = len(xs_rows) / max(groups, 1)
    if len(xs_rows) < 5:
        return SideAnchor(UNREADABLE, coverage=coverage, outer=outer,
                          note=f"a plateau ends near scan column "
                               f"{_to_scan(u_col, side, x0, ws):.0f} but only {len(xs_rows)} "
                               f"rows have contrast >= {MIN_CONTRAST}")
    xs_a = np.asarray(xs_rows)
    ys_a = np.asarray(ys_rows)
    xp_a = m.prescan_x(xs_a, ys_a)
    yp_a = m.prescan_y(ys_a)
    # Straightness, robustly: fit, drop > 3 MAD, fit again.
    keep = np.ones(len(xp_a), dtype=bool)
    fit = np.polyfit(yp_a, xp_a, 1)
    for _ in range(2):
        res = xp_a - np.polyval(fit, yp_a)
        mad = float(np.median(np.abs(res - np.median(res[keep])))) + 1e-9
        keep = np.abs(res) <= max(3.0 * 1.4826 * mad, 0.15)
        fit = np.polyfit(yp_a[keep], xp_a[keep], 1)
    residual = float(np.median(np.abs(xp_a[keep] - np.polyval(fit, yp_a[keep]))))
    x_global = float(np.median(xp_a))
    # The global model can be ~0.4 column out at the ends of the frame (the local
    # band check shows it), so the offset is refitted on the edge's own window and
    # every row's position moves by it.
    local = _local_fits(p, side, x_global) if p.model is not None else {}
    d = local.get("d_edge", 0.0)
    xp_a = xp_a - d
    x = x_global - d
    third = (yp_a.max() - yp_a.min()) / 3.0
    top_sel = yp_a <= yp_a.min() + third
    bot_sel = yp_a >= yp_a.max() - third
    x_top = float(np.median(xp_a[top_sel])) if top_sel.any() else None
    x_bottom = float(np.median(xp_a[bot_sel])) if bot_sel.any() else None
    q25, q75 = np.percentile(xp_a, [25, 75])
    width = float(np.median(widths)) if widths else None
    # The interval: the local refit's own disagreement between the top and bottom
    # halves, half its disagreement with a refit that leaves the edge out, and the
    # standard error of the row median. 0.05 is a floor, not a measurement.
    split = abs(local.get("d_edge_top", d) - local.get("d_edge_bottom", d)) / 2.0
    beside = abs(d - local.get("d_picture", d)) / 2.0
    stat = 1.2533 * 1.4826 * float(np.median(np.abs(xp_a - x))) / np.sqrt(len(xp_a))
    reg_err = float(np.hypot(max(0.05, split), beside))
    err = float(np.hypot(reg_err, stat))
    anchor = SideAnchor(EDGE, x, x - err, x + err, x_top, x_bottom, x_global=x_global,
                        d_edge=local.get("d_edge"), d_edge_top=local.get("d_edge_top"),
                        d_edge_bottom=local.get("d_edge_bottom"),
                        d_picture=local.get("d_picture"),
                        scan_x=float(np.median(xs_a)), outer=outer, coverage=coverage,
                        residual=residual, row_iqr=float(q75 - q25), width_10_90=width,
                        reg_err=reg_err, contrast=float(np.median(contrasts)),
                        points=[(float(yy), float(xx), float(pp))
                                for yy, xx, pp in zip(ys_a, xs_a, xp_a)])
    wp = m.width
    beyond = (side == "left" and x <= 0.0) or (side == "right" and x >= wp)
    notes = []
    if sliver:
        notes.append("a sliver: base narrower than a plateau, its level read at the border")
    if residual > MAX_RESIDUAL:
        anchor.state = UNREADABLE
        notes.append(f"not straight: residual {residual:.2f} col from a line "
                     f"(candidate {x:.2f}, read in {coverage:.0%} of rows)")
    elif beyond:
        # Read in however few rows: a straight gate edge outside the prescan in
        # those rows is outside it in all of them.
        anchor.state = PICTURE_TO_BORDER
        notes.append(f"base shows in the scan only beyond the prescan's coverage: the edge "
                     f"maps to {x:.2f} (read in {coverage:.0%} of rows)")
    elif coverage < MIN_COVERAGE:
        anchor.state = UNREADABLE
        notes.append(f"read in only {coverage:.0%} of rows (candidate {x:.2f}): the rest have "
                     f"picture at base level, or this is scene, not base")
    # An empty gate is far brighter than base: read the prescan on the plateau.
    pl_scan = _to_scan(0.5 * (start + min(end, u_col - mb)), side, x0, ws)
    pl_p = float(m.prescan_x(pl_scan, hs / 2))
    j = int(np.clip(np.floor(pl_p), 0, prescan.shape[1] - 1))
    if 0 <= pl_p < prescan.shape[1] and float(prescan[5:-5, j].min(axis=-1).mean()) > 100:
        anchor.state = NO_FILM
        notes.append("the plateau is gate-bright in the prescan: no film")
    if anchor.state != EDGE:
        # common.Side: x is set only for an edge. The number is kept as a candidate.
        anchor.candidate = anchor.x
        anchor.x = anchor.lo = anchor.hi = anchor.x_top = anchor.x_bottom = None
    anchor.note = "; ".join(notes)
    return anchor


def prescan_crossing(prescan: np.ndarray, side: str, x: float) -> float | None:
    """The same 50% crossing read on the prescan itself, near ``x`` -- a sanity check only.

    It needs at least two whole columns of base beside the line; with fewer there is
    no base level to read and it says nothing.
    """
    img = prescan.astype(np.float64)
    hp, wp = img.shape[:2]
    a = img if side == "left" else img[:, ::-1]
    u = x if side == "left" else wp - x
    r0, r1 = int(ROW_SPAN[0] * hp), int(ROW_SPAN[1] * hp)
    ref = np.percentile(a[r0:r1].reshape(-1, 3), 99.5, axis=0)
    sig = (a[r0:r1] / ref).mean(axis=2)
    iu = int(np.floor(u))
    if iu - 1 < 2 or iu + 3 > wp:
        return None
    out = []
    for s in sig:
        bl = float(np.median(s[max(0, iu - 3): iu - 1]))
        pl = float(np.median(s[iu + 1: iu + 3]))
        if (bl - pl) / max(bl, 1e-9) < MIN_CONTRAST:
            continue
        c = _crossing(s, u, 0.5 * (bl + pl))
        if c is not None and abs(c - u) < 2.0:
            out.append(c)
    if len(out) < 5:
        return None
    v = float(np.median(out))
    return v if side == "left" else wp - v


# --------------------------------------------------------------------------
# pictures to check every anchor by eye

PANEL_H = 480
SCAN_W = 240
PRESCAN_HALF = 12          # prescan columns either side of the line
PRESCAN_ZOOM = 10
RED = (230, 30, 30)
CYAN = (0, 170, 230)
ORANGE = (255, 150, 0)


def _stretch(img: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Not inverted, each channel over 0..its base-ish top: base white, picture darker."""
    return (np.clip(img / np.maximum(top, 1e-9), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _scan_panel(p: Prepared, side: str, a: SideAnchor, top: np.ndarray) -> Image.Image:
    m = p.reg.mapping
    assert m is not None
    hs, ws = p.scan_shape
    zone = p.zones[side]
    x0 = p.zone_x0[side]
    half = max(30, int(round(3 * m.n)))
    if a.scan_x is not None:
        centre = int(round(a.scan_x))
    else:
        centre = x0 + half if side == "left" else ws - half
    lo = int(np.clip(centre - half, x0, x0 + zone.shape[1] - 2 * half))
    crop = zone[:, lo - x0: lo - x0 + 2 * half]
    im = Image.fromarray(_stretch(crop, top)).resize((SCAN_W, PANEL_H), Image.Resampling.BILINEAR)
    d = ImageDraw.Draw(im)
    sx, sy = SCAN_W / (2 * half), PANEL_H / hs
    for yy, xx, _ in a.points[::max(1, len(a.points) // 120)]:
        u, v = (xx - lo) * sx, yy * sy
        d.ellipse([u - 1.5, v - 1.5, u + 1.5, v + 1.5], fill=RED)
    if a.scan_x is not None:
        u = (a.scan_x - lo) * sx
        d.line([(u, 0), (u, 8)], fill=RED, width=2)
        d.line([(u, PANEL_H - 9), (u, PANEL_H)], fill=RED, width=2)
    for f in ROW_SPAN:
        d.line([(0, f * PANEL_H), (6, f * PANEL_H)], fill=CYAN, width=1)
    d.rectangle([0, 0, SCAN_W, 12], fill=(0, 0, 0))
    d.text((2, 0), f"scan {p.dpi} dpi cols {lo}-{lo + 2 * half}", fill=(255, 255, 0))
    return im


def _prescan_panel(prescan: np.ndarray, side: str, a: SideAnchor, m: Mapping) -> Image.Image:
    hp, wp = prescan.shape[:2]
    line = a.x if a.x is not None else a.candidate
    colour = RED if a.x is not None else ORANGE
    if line is not None:
        centre = line
    else:
        centre = PRESCAN_HALF if side == "left" else wp - PRESCAN_HALF
    lo = int(np.clip(round(centre) - PRESCAN_HALF, 0, wp - 2 * PRESCAN_HALF))
    crop = prescan[:, lo:lo + 2 * PRESCAN_HALF]
    top = np.percentile(prescan[5:-5].reshape(-1, 3), 99.5, axis=0)
    zx = PRESCAN_ZOOM
    zy = PANEL_H / hp
    im = Image.fromarray(_stretch(crop, top)).resize((2 * PRESCAN_HALF * zx, PANEL_H),
                                                      Image.Resampling.NEAREST)
    d = ImageDraw.Draw(im)
    for c in range(2 * PRESCAN_HALF + 1):
        col = lo + c
        tall = 10 if col % 5 == 0 else 4
        d.line([(c * zx, PANEL_H - tall), (c * zx, PANEL_H)], fill=CYAN, width=1)
        if col % 5 == 0:
            d.text((c * zx + 2, PANEL_H - 22), str(col), fill=CYAN)
    for yy, _, xp in a.points[::max(1, len(a.points) // 120)]:
        u, v = (xp - lo) * zx, float(m.prescan_y(yy)) * zy
        d.ellipse([u - 1.5, v - 1.5, u + 1.5, v + 1.5], fill=colour)
    if line is not None:
        u = (line - lo) * zx
        d.line([(u, 0), (u, 14)], fill=colour, width=2)
        d.line([(u, PANEL_H - 40), (u, PANEL_H - 26)], fill=colour, width=2)
    d.rectangle([0, 0, 2 * PRESCAN_HALF * zx, 12], fill=(0, 0, 0))
    d.text((2, 0), f"prescan x{zx} cols {lo}-{lo + 2 * PRESCAN_HALF}", fill=(255, 255, 0))
    return im


def _caption(a: SideAnchor) -> str:
    if a.x is None:
        return a.state + (f" (candidate {a.candidate:.2f})" if a.candidate is not None else "")
    return (f"{a.state} x={a.x:.2f} [{a.lo:.2f},{a.hi:.2f}] top {a.x_top:.2f} bot "
            f"{a.x_bottom:.2f}")


def render(p: Prepared, prescan: np.ndarray, sides: dict[str, SideAnchor], ref: np.ndarray,
           path: Path) -> None:
    """Both sides of one frame: the scan stretched by the frame's own base level, so white
    is base and nothing else, and the prescan x10 with the mapped line and rows."""
    m = p.reg.mapping
    assert m is not None
    gap = 12
    side_w = SCAN_W + gap + 2 * PRESCAN_HALF * PRESCAN_ZOOM
    sheet = Image.new("RGB", (2 * side_w + 3 * gap, PANEL_H + 64), (35, 35, 35))
    d = ImageDraw.Draw(sheet)
    g = p.reg
    d.text((gap, 4), f"{p.id}  {p.dpi} dpi  conf {g.conf:.0f}  corr {g.corr:.4f}  "
                     f"k {m.scale(m.width / 2):.4f}  {'rows reversed ' if m.flipped else ''}"
                     f"{p.entry}", fill=(255, 255, 255))
    for i, side in enumerate(("left", "right")):
        a = sides[side]
        ox = gap + i * (side_w + gap)
        sheet.paste(_scan_panel(p, side, a, ref), (ox, 22))
        sheet.paste(_prescan_panel(prescan, side, a, m), (ox + SCAN_W + gap, 22))
        d.text((ox, PANEL_H + 26), f"{side}: {_caption(a)}", fill=(255, 255, 255))
        d.text((ox, PANEL_H + 42), a.note[:110], fill=(255, 200, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


# --------------------------------------------------------------------------
# the whole run


def _round(v: Any, nd: int = 3) -> Any:
    return None if v is None else round(float(v), nd)


def _side_json(a: SideAnchor) -> dict[str, Any]:
    out: dict[str, Any] = {"state": a.state}
    for key in ("x", "lo", "hi", "x_top", "x_bottom", "scan_x", "outer", "coverage",
                "residual", "row_iqr", "width_10_90", "reg_err", "contrast", "prescan_50",
                "x_global", "candidate", "d_edge", "d_edge_top", "d_edge_bottom", "d_picture"):
        out[key] = _round(getattr(a, key))
    out["note"] = a.note
    return out


ABOUT = {
    "what": "Edge anchors: each frame's base/picture boundary read off the higher-resolution "
            "scan of the same aperture, mapped into the prescan's boundary columns "
            "(common.py coordinates: left x = columns of base; right: base is [x, W)). "
            "x is set only for state 'edge'; any other verdict that still found a line "
            "keeps it as 'candidate'.",
    "registration": "scan box-averaged onto the prescan grid; integer start from "
                    "uniformity.register at framing.SEARCH_MM reach (conf is on the driver's "
                    "scale; a frame would be refused below 50 -- none was), tried with the "
                    "scan's rows both ways round (subpixel.flipped); then a forward model -- "
                    "each prescan pixel the mean of the scan over its footprint, footprint "
                    "x = n (x_p + c0 + c1 t + c2 t^2 + c3 t^3 + shear v), y = ky y_p + oy, t and "
                    "v the prescan position scaled to -1..1 -- fitted by maximising the "
                    "channel-mean Pearson correlation (corr; corr_linear: c2 = c3 = shear = 0). "
                    "k = the local scale at mid-frame, k_left/k_right at 10 columns in. "
                    "band_offsets: per 27-column band, the model's residual offset "
                    "[all rows, top half, bottom half].",
    "edge": "base = a run of columns at >= 0.90 of the frame's brightest and uniform down the "
            "frame ((p90-p10)/median <= 0.15), starting nearest the aperture and ending no "
            "more than 40 prescan columns in (a gap between frames is ~30; wider is scene); "
            "a narrower run at base level against the border is taken as a sliver. Per "
            "prescan-row band of the scan, the 50% crossing between that row's base level "
            "and its picture level just inside, over rows 10-90% of the height, where the "
            "contrast is >= 5%.",
    "mapping": "each row's crossing goes through the global model (x_global), then the whole "
               "edge moves by d_edge, the model's offset refitted on a window holding the "
               "edge (6 columns of base, 18 of picture): the global model is up to ~0.4 column "
               "out at the ends of the frame, and not the same way on every frame. d_picture "
               "is the same refit on picture only (2-26 columns in), for comparison. x is the "
               "median of the rows; x_top/x_bottom the medians of the top and bottom thirds.",
    "states": "edge: straight (residual <= 0.5 col), read in >= 35% of rows, inside the "
              "prescan. picture_to_border: no base plateau, or a clean edge that maps outside "
              "the prescan (a scan reaches about 0.3 column past the prescan's left border "
              "and 2.5 past its right; anchors.py --window). unreadable: base-level "
              "everywhere, a line in too few rows, or base wider than a gap.",
    "shapes": "scan_shape: the scan's (rows, columns) as registered; prescan_shape the "
              "prescan's. With the fit they place the scan's own window in the prescan's "
              "columns (anchors.py --window).",
    "interval": "lo/hi = x -/+ hypot(max(0.05, |d_edge_top - d_edge_bottom|/2), "
                "|d_edge - d_picture|/2, standard error of the row median). row_iqr is the "
                "edge's own bow and tilt, not an error.",
    "prescan_50": "the same 50% crossing read on the prescan alone near x (needs two whole "
                  "columns of base): a sanity check, not an input.",
    "pair_checks": "anchored frames that register as the same picture (conf >= "
                   "framing.CONFIDENCE_FLOOR): one's x or candidate carried into the other "
                   "through a prescan-to-prescan fit. diff = carried - other.",
    "checked": "every frame drawn to crops/anchors/<id>.png and looked at.",
}


def _number(side: dict[str, Any]) -> float | None:
    """An edge's x, or where a non-edge verdict found its line."""
    x = side.get("x")
    return x if x is not None else side.get("candidate")


def pair_checks(anchors: dict[str, Any]) -> list[dict[str, Any]]:
    """Two anchored prescans of the same picture: carried from one to the other, do they agree?

    Independent end to end -- two scans, two registrations, two edge reads -- and
    joined by a third registration, prescan to prescan. Which frames show the same
    picture is left to the pixels: any pair that ``register`` matches above the
    driver's own ``CONFIDENCE_FLOOR``.
    """
    ids = sorted(i for i, e in anchors.items()
                 if any(_number(e.get(s) or {}) is not None for s in ("left", "right")))
    images = {i: load(i).image for i in ids}
    out: list[dict[str, Any]] = []
    for ia, a in enumerate(ids):
        for b in ids[ia + 1:]:
            _, _, conf = uniformity.register(images[a], images[b], max_shift=REACH)
            if conf < framing.CONFIDENCE_FLOOR:
                continue
            reg, _ = register(images[a], images[b].astype(np.float32), 300)
            m = reg.mapping
            if not reg.ok or m is None:
                continue
            for side in ("left", "right"):
                xa, xb = _number(anchors[a][side]), _number(anchors[b][side])
                if xa is None or xb is None:
                    continue
                carried = float(m.scan_x(xa, m.height / 2))
                out.append({"a": a, "b": b, "side": side, "conf": round(conf, 1),
                            "states": [anchors[a][side]["state"], anchors[b][side]["state"]],
                            "a_x": xa, "b_x": xb, "a_in_b": round(carried, 3),
                            "diff": round(carried - xb, 3)})
    return out


def run(ids: list[str], cache: Path | None, pictures: bool) -> dict[str, Any]:
    rows = [r for r in manifest()["frames"] if r.get("hires")]
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    anchors: dict[str, Any] = {}
    if OUT_JSON.exists():
        anchors = json.loads(OUT_JSON.read_text(encoding="utf-8")).get("anchors", {})
    for r in rows:
        t0 = time.time()
        p = prepare(r, cache)
        g = p.reg
        m = g.mapping
        entry: dict[str, Any] = {
            "kind": r.get("kind"), "hires_entry": p.entry, "dpi": p.dpi,
            # The scan's width is what puts its last column in the prescan's
            # through the fit (`--window`), and the entry it came from need not
            # be on the machine that reads this file.
            "scan_shape": list(p.scan_shape),
            "k": _round(m.scale(m.width / 2), 5) if m else None,
            "k_left": _round(m.scale(10.0), 5) if m else None,
            "k_right": _round(m.scale(m.width - 10.0), 5) if m else None,
            "reg": {"dy": g.dy, "dx": g.dx, "conf": _round(g.conf, 1),
                    "conf_rows_reversed": _round(g.conf_flipped, 1),
                    "corr": _round(g.corr, 4), "corr_linear": _round(g.corr_linear, 4),
                    "ok": g.ok, "why": g.why,
                    "subpixel": None if m is None else {
                        "c": [_round(v, 4) for v in m.c], "shear": _round(m.shear, 4),
                        "ky": _round(m.ky, 5), "oy": _round(m.oy, 3), "flipped": m.flipped},
                    "band_offsets": [[_round(v, 3) for v in b] for b in p.band_offsets]},
        }
        prescan = load(r).image
        entry["prescan_shape"] = list(prescan.shape[:2])
        if not g.ok or m is None:
            entry["left"] = entry["right"] = {"state": "refuse", "note": g.why}
            print(f"{r['id']}: refused -- {g.why}")
        else:
            ref = reference(p)
            sides = {s: find_edge(p, s, prescan, ref) for s in ("left", "right")}
            for s, a in sides.items():
                if a.x is not None and 0.0 < a.x < prescan.shape[1]:
                    a.prescan_50 = prescan_crossing(prescan, s, a.x)
            for s, a in sides.items():
                entry[s] = _side_json(a)
            if pictures:
                render(p, prescan, sides, ref, CROPS / f"{r['id']}.png")
            print(f"{r['id']:11s} {p.dpi:4d} conf {g.conf:5.1f} corr {g.corr:.4f}  "
                  f"L {_caption(sides['left'])} | R {_caption(sides['right'])}  "
                  f"({time.time() - t0:.0f}s)", flush=True)
        anchors[r["id"]] = entry
    checks = pair_checks(anchors)
    for c in checks:
        print(f"same picture {c['a']} / {c['b']} {c['side']}: {c['a_x']:.2f} carried to "
              f"{c['a_in_b']:.2f} against {c['b_x']:.2f}, diff {c['diff']:+.3f}")
    body = {"about": ABOUT, "anchors": dict(sorted(anchors.items())), "pair_checks": checks}
    OUT_JSON.write_text(json.dumps(body, indent=1), encoding="utf-8")
    return body


# --------------------------------------------------------------------------
# the scan's own window, from the stored fits

#: What a whole-frame pass comes back as, in its own columns, by resolution:
#: the device's width, for a pair whose own record is not on disk to say. 300,
#: 600, 1200, 1800 and 3600 dpi are every whole-frame entry in the library
#: this was first run against -- 941, 97, 1, 24 and 357 of them, one width
#: each, 2026-10-05 -- and 900 dpi is the 1292 `DemoScanner._shape_for`
#: records. 600 dpi has also come back 862 wide (README); a pair read at 862
#: would end a prescan column further right than this makes it.
DEVICE_COLUMNS = {300: 428, 600: 860, 900: 1292, 1200: 1724, 1800: 2584, 3600: 5172}


def scan_columns(entry: dict[str, Any], library: Path | None) -> tuple[int, str] | None:
    """How wide a pair's scan came back, in its own columns, and how that is known.

    Its own record first -- ``scan_shape``, which a run since `--window` existed
    stores beside the fit, then its entry's `scan.json` -- and the device's
    width at that resolution only where neither is on disk. None if not even
    that is known.
    """
    shape = entry.get("scan_shape")
    if shape:
        return int(shape[1]), "anchors.json"
    if library is not None:
        try:
            record = json.loads((library / entry["hires_entry"] / "scan.json")
                                .read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            record = {}
        shape = (record.get("image") or {}).get("shape")
        if shape and len(shape) >= 2:
            return int(shape[1]), "its entry"
    width = DEVICE_COLUMNS.get(int(entry.get("dpi") or 0))
    return (width, "the device's") if width else None


@dataclass
class Window:
    """Where one pair's scan begins and ends, in its prescan's boundary columns."""

    id: str
    dpi: int
    reversed: bool               # the scan's rows ran the other way to the prescan's
    columns: int                 # the scan's width, in its own columns
    known_from: str              # how that width is known (`scan_columns`)
    prescan_width: int
    left: float                  # the scan's first boundary: negative is past the prescan
    right: float                 # its last
    shear: float                 # how far either edge leans, middle row to top or bottom

    @property
    def past_left(self) -> float:
        return -self.left

    @property
    def past_right(self) -> float:
        return self.right - self.prescan_width


def window_of(fid: str, entry: dict[str, Any], library: Path | None) -> Window | None:
    """One pair's scan window, through its stored fit, at the prescan's middle row.

    The fit maps a prescan boundary to the scan's (`Mapping.scan_x`), so the
    scan's own first and last boundaries -- 0 and its width -- go back through
    the inverse. Nothing is registered again: the fit is the whole of what the
    registration found. At the middle row the shear term is zero, whatever the
    height, which is why a pair stored without its prescan's shape can be given
    a nominal one; at the top and bottom it moves either edge by ``shear``.

    None for a pair with no fit, a refused one, or no width to read.
    """
    reg = entry.get("reg") or {}
    sub = reg.get("subpixel")
    if not sub or not reg.get("ok"):
        return None
    known = scan_columns(entry, library)
    if known is None:
        return None
    columns, known_from = known
    rows, wide = entry.get("prescan_shape") or (1, int(framing.PRESCAN_COLUMNS))
    m = Mapping(int(entry["dpi"]) / 300.0, int(wide), max(1, int(rows)),
                [float(v) for v in sub["c"]], float(sub["shear"]), float(sub["ky"]),
                float(sub["oy"]), bool(sub["flipped"]))
    middle = m.scan_y(m.height / 2.0)
    return Window(fid, int(entry["dpi"]), m.flipped, columns, known_from, m.width,
                  float(m.prescan_x(0.0, middle)), float(m.prescan_x(float(columns), middle)),
                  m.shear)


def _spread(values: list[float]) -> dict[str, float]:
    return {"median": float(np.median(values)), "lo": float(min(values)),
            "hi": float(max(values))}


def _medians(windows: list[Window]) -> dict[str, Any]:
    return {"pairs": len(windows),
            "left": float(np.median([w.left for w in windows])),
            "right": float(np.median([w.right for w in windows]))}


def window_report(path: Path = OUT_JSON, library: Path | None = LIBRARY) -> dict[str, Any]:
    """The scan's window in a 300 dpi prescan's columns, from the fits `run` stored.

    A prescan comes back 428 columns wide and a scan covers more of the
    transport than that. Read from the committed `anchors.json` on 2026-10-05,
    with every scan's width the device's (none of the 41 entries, from
    2026-08-29 to 09-14, was in the library this ran against):

    ===================  ======  ======  ===========  ============
    36 scans, 600-3600     left   right  past left    past right
    ===================  ======  ======  ===========  ============
    all (median)          -0.31  430.48  0.31         2.48
    range over pairs                     -0.30..1.48  1.30..3.85
    600 dpi (6)           -0.28  429.76
    900 dpi (4)           -0.34  430.34
    1800 dpi (9)          -0.68  430.49
    3600 dpi (17)         -0.26  430.84
    as the prescan (26)   -0.45  430.43
    reversed (10)         +0.15  430.92
    ===================  ======  ======  ===========  ============

    600 dpi ends 0.7 column short on the right because the device returns 860
    columns there -- 430 of a prescan's -- where every other resolution returns
    430.7-431. A scan read the other way round from its prescan sits about half
    a column right of one read the same way, at both edges. The shear moves an
    edge by at most 0.10 column between the middle row and the top or bottom.

    The five pairs whose "scan" was a second 300 dpi pass -- the prescan's own
    window -- come back at +0.15 and 427.85: 0.15 column inside either edge,
    which is the method's floor rather than a window.
    """
    anchors = json.loads(path.read_text(encoding="utf-8")).get("anchors", {})
    windows = [w for fid, e in sorted(anchors.items())
               if (w := window_of(fid, e, library)) is not None]
    # A pair at 300 dpi is two prescans: the same window twice, not a scan's.
    scans = [w for w in windows if w.dpi > 300]
    same = [w for w in windows if w.dpi <= 300]
    out: dict[str, Any] = {"windows": windows, "pairs": len(scans)}
    if not scans:
        return out
    out.update(_medians(scans))
    out["past_left"] = _spread([w.past_left for w in scans])
    out["past_right"] = _spread([w.past_right for w in scans])
    out["per_dpi"] = {dpi: _medians([w for w in scans if w.dpi == dpi])
                      for dpi in sorted({w.dpi for w in scans})}
    out["per_direction"] = {
        name: _medians(group) for name, group in (
            ("as the prescan", [w for w in scans if not w.reversed]),
            ("reversed", [w for w in scans if w.reversed])) if group}
    out["shear"] = float(max(abs(w.shear) for w in scans))
    if same:
        out["prescan_dpi"] = _medians(same)
    return out


def say_window_report(report: dict[str, Any]) -> None:
    """`window_report`, printed: every pair, then what they come to."""
    windows = report["windows"]
    print(f"the scan's window in its prescan's columns, at the middle row, from "
          f"{len(windows)} stored fits")
    print(f"  {'id':12s} {'dpi':>4s}  {'rows':8s} {'columns':>7s} {'width from':12s} "
          f"{'left':>7s} {'right':>8s}  {'past left':>9s} {'past right':>10s}")
    for w in windows:
        print(f"  {w.id:12s} {w.dpi:4d}  {'reversed' if w.reversed else 'as-is':8s} "
              f"{w.columns:7d} {w.known_from:12s} {w.left:+7.2f} {w.right:8.2f}  "
              f"{w.past_left:+9.2f} {w.past_right:+10.2f}")
    if not report.get("pairs"):
        print("no pair above 300 dpi has a fit and a width")
        return
    left, right = report["past_left"], report["past_right"]
    print(f"{report['pairs']} scans at 600-3600 dpi: left {report['left']:+.2f}, right "
          f"{report['right']:.2f} (medians) -- past the prescan by {left['median']:.2f} on the "
          f"left ({left['lo']:+.2f}..{left['hi']:+.2f}) and {right['median']:.2f} on the right "
          f"({right['lo']:+.2f}..{right['hi']:+.2f})")
    for dpi, g in report["per_dpi"].items():
        print(f"  {dpi:4d} dpi ({g['pairs']:2d}): {g['left']:+.2f} {g['right']:.2f}")
    for name, g in report["per_direction"].items():
        print(f"  {name} ({g['pairs']:2d}): {g['left']:+.2f} {g['right']:.2f}")
    print(f"  shear: an edge leans at most {report['shear']:.2f} column, middle row to end")
    same = report.get("prescan_dpi")
    if same:
        print(f"{same['pairs']} second passes at 300 dpi, the prescan's own window: "
              f"{same['left']:+.2f} {same['right']:.2f} -- the method's floor")
    held = framing.SCAN_WINDOW_COLUMNS
    print(f"framing.SCAN_WINDOW_COLUMNS = {held}: {held[0] - report['left']:+.3f} and "
          f"{held[1] - report['right']:+.3f} from these")
    sources: dict[str, int] = {}
    for w in windows:
        sources[w.known_from] = sources.get(w.known_from, 0) + 1
    print("widths: " + ", ".join(f"{n} {source}" for source, n in sorted(sources.items())))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("ids", nargs="*", help="frame ids (default: every frame with a hires scan)")
    ap.add_argument("--cache", type=Path, default=None,
                    help="keep registrations and edge zones here, for reruns")
    ap.add_argument("--no-pictures", action="store_true")
    ap.add_argument("--window", action="store_true",
                    help="say where each scan's window falls in its prescan's columns, from "
                         "the fits already in anchors.json, and stop: nothing is registered")
    ap.add_argument("--library", type=Path, default=LIBRARY,
                    help="where --window looks for a pair's entry, for its scan's width "
                         f"(default {LIBRARY})")
    args = ap.parse_args(argv)
    if args.window:
        say_window_report(window_report(library=args.library))
        return 0
    run(args.ids, args.cache, not args.no_pictures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
