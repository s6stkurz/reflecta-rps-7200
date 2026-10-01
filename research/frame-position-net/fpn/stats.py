"""Statistics of a prescan that a synthetic one has to match.

Measured the same way on real frames (from their labels) and on synthetic ones
(from their recipe), on the delivered uint8 pixels, rows trimmed as the study
trims them:

- base: per-channel median level, R/G and B/G, and the per-pixel spread (SD
  over mean) -- grain, sensor and rounding together, as they arrive;
- picture: quantiles of log2(picture / base) per channel, and the share of
  picture pixels brighter than base in each channel.

A side's base is used where at least `MIN_BASE` columns show, one column clear
of the edge; the picture is everything at least `MARGIN` columns inside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tools.frame_edges.sides import EDGE, TRIM_ROWS, EdgeResult

MIN_BASE = 4.0
MARGIN = 3
QUANTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)


@dataclass
class FrameStats:
    base: np.ndarray | None = None          # (3,) median counts
    base_spread: np.ndarray | None = None   # (3,) SD / mean
    picture: np.ndarray | None = None       # (n, 3) counts, a sample
    extra: dict = field(default_factory=dict)


def _base_columns(result: EdgeResult, width: int) -> list[int]:
    cols: list[int] = []
    left, right = result.left, result.right
    if left.state == EDGE and left.x is not None and left.x >= MIN_BASE:
        start = 0 if left.outer is None else int(np.ceil(left.outer)) + 1
        cols += list(range(start, int(np.floor(left.x)) - 1))
    if right.state == EDGE and right.x is not None and width - right.x >= MIN_BASE:
        stop = width if right.outer is None else int(np.floor(right.outer)) - 1
        cols += list(range(int(np.ceil(right.x)) + 1, stop))
    return [c for c in cols if 0 <= c < width]


def _picture_columns(result: EdgeResult, width: int) -> list[int]:
    lo = 0 if result.left.state != EDGE or result.left.x is None else int(np.ceil(result.left.x))
    hi = width if result.right.state != EDGE or result.right.x is None \
        else int(np.floor(result.right.x))
    return list(range(lo + MARGIN, hi - MARGIN))


def measure(image: np.ndarray, result: EdgeResult, rng: np.random.Generator,
            sample: int = 4000) -> FrameStats:
    img = image[TRIM_ROWS: image.shape[0] - TRIM_ROWS, :, :3].astype(np.float64)
    width = img.shape[1]
    out = FrameStats()
    base_cols = _base_columns(result, width)
    if len(base_cols) >= 2:
        base = img[:, base_cols].reshape(-1, 3)
        out.base = np.median(base, axis=0)
        # Spread about each column's own level: the shading leaves a little
        # column pattern, and a base band is narrow enough to show it.
        cols = img[:, base_cols]
        resid = cols - cols.mean(axis=0, keepdims=True)
        out.base_spread = resid.reshape(-1, 3).std(axis=0) / np.maximum(out.base, 1e-6)
    pic_cols = _picture_columns(result, width)
    if len(pic_cols) > 20:
        pic = img[:, pic_cols].reshape(-1, 3)
        take = rng.choice(len(pic), size=min(sample, len(pic)), replace=False)
        out.picture = pic[take]
    return out


def summarise(frames: list[FrameStats], roll_base: list[np.ndarray]) -> dict:
    """Pool frames: base ratios and spread, and picture against its roll's base."""
    bases = np.array([f.base for f in frames if f.base is not None])
    spreads = np.array([f.base_spread for f in frames if f.base_spread is not None])
    rel, above = [], []
    for f, base in zip(frames, roll_base):
        if f.picture is None:
            continue
        r = np.log2((f.picture + 0.5) / (base + 0.5))
        rel.append(r)
        above.append((f.picture > base).mean(axis=0))
    rel_all = np.concatenate(rel) if rel else np.zeros((0, 3))
    return {
        "frames": len(frames),
        "base_counts": {"median": np.median(bases, axis=0).tolist(),
                        "p10": np.percentile(bases, 10, axis=0).tolist(),
                        "p90": np.percentile(bases, 90, axis=0).tolist()} if len(bases) else None,
        "base_r_over_g": np.percentile(bases[:, 0] / bases[:, 1], [10, 50, 90]).tolist()
        if len(bases) else None,
        "base_b_over_g": np.percentile(bases[:, 2] / bases[:, 1], [10, 50, 90]).tolist()
        if len(bases) else None,
        "base_spread": np.median(spreads, axis=0).tolist() if len(spreads) else None,
        "picture_log2_vs_base": {str(q): np.percentile(rel_all, q, axis=0).tolist()
                                 for q in QUANTILES} if len(rel_all) else None,
        "picture_above_base": np.median(np.array(above), axis=0).tolist() if above else None,
    }
