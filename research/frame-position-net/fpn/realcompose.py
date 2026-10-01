"""New samples from real frames: the same picture with more base showing.

Stefan's idea: take the good labelled dev frames (role `compose`) and make new
ones from them with different spaces left and right, the base taken from the
frames themselves. Nothing is invented:

- the picture is the frame's own pixels, shifted by a whole number of columns,
  so every one of them is bit-exact and the label keeps its fraction (x + d);
- new base columns are copies of the frame's own base columns -- whole columns,
  so the grain, the level down the rows and the shading's comb are all real;
- beyond a wider gap, optionally, the neighbour: the real picture edge of
  another compose frame of the same roll, cut just past its own labelled edge.

Only a side with base and no neighbour beyond can be widened: past a border the
picture's continuation is unknown, and past a visible neighbour so is the
neighbour's. Frames that cannot be widened are used as they are. Only 8-bit
C-41 frames: the runtime prescan is 8-bit, and B&W and slide frames are tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tools.frame_edges.sides import EDGE, PICTURE_TO_BORDER, TRIM_ROWS, EdgeResult, Side

from fpn import study
from fpn.compose import CLASS_BASE, CLASS_NEIGHBOUR, CLASS_PICTURE
from fpn.inputs import COLUMNS

#: The widest gap a widened side may reach (gapmodel.py's prior, 4-34).
GAP_MAX = 32.0
NEIGHBOUR_SHARE = 0.4


@dataclass
class Source:
    frame: study.Frame
    image: np.ndarray
    truth: EdgeResult


def sources() -> list[Source]:
    out = []
    for f in study.frames("compose"):
        if f.kind != "film" or f.film != "c41" or f.dtype != "uint8":
            continue
        t = study.truth(f)
        if t is None or t.left.state not in (EDGE, PICTURE_TO_BORDER) \
                or t.right.state not in (EDGE, PICTURE_TO_BORDER):
            continue
        out.append(Source(f, f.image(), t))
    return out


def _mirror_side(s: Side, width: int) -> Side:
    def flip(v: float | None) -> float | None:
        return None if v is None else width - v

    return Side(s.state, x=flip(s.x), lo=flip(s.hi), hi=flip(s.lo), conf=s.conf,
                outer=flip(s.outer), x_top=flip(s.x_top), x_bottom=flip(s.x_bottom), note=s.note)


def mirror(image: np.ndarray, result: EdgeResult) -> tuple[np.ndarray, EdgeResult]:
    w = image.shape[1]
    return image[:, ::-1].copy(), EdgeResult(_mirror_side(result.right, w),
                                             _mirror_side(result.left, w))


def _shift_side(s: Side, d: float) -> Side:
    def move(v: float | None) -> float | None:
        return None if v is None else v + d

    return Side(s.state, x=move(s.x), lo=move(s.lo), hi=move(s.hi), conf=s.conf,
                outer=move(s.outer), x_top=move(s.x_top), x_bottom=move(s.x_bottom), note=s.note)


def widenable(src: Source) -> str | None:
    """"left" or "right" if that side has base and no neighbour beyond it."""
    for name in ("left", "right"):
        s = src.truth.side(name)
        other = src.truth.side("right" if name == "left" else "left")
        if s.state == EDGE and s.x is not None and s.outer is None \
                and other.state == PICTURE_TO_BORDER:
            base = s.x if name == "left" else COLUMNS - s.x
            if base >= 3.0:
                return name
    return None


def widen(src: Source, side: str, rng: np.random.Generator,
          neighbours: list[Source]) -> tuple[np.ndarray, EdgeResult]:
    """More base on ``side``, and maybe the neighbour beyond it."""
    image, truth = (src.image, src.truth) if side == "left" else mirror(src.image, src.truth)
    left = truth.left
    assert left.x is not None
    edge_low = min(v for v in (left.x, left.x_top, left.x_bottom) if v is not None)
    base_cols = np.arange(0, int(np.floor(edge_low)) - 1)
    gap = float(rng.uniform(max(left.x + 1.0, 10.0), max(left.x + 2.0, GAP_MAX)))
    d = int(rng.integers(1, max(2, int(gap - left.x) + 1)))
    take = rng.choice(base_cols, size=d, replace=True)
    new = np.concatenate([image[:, take], image[:, : COLUMNS - d]], axis=1)
    out = EdgeResult(_shift_side(left, d), truth.right)
    candidates = [n for n in neighbours if n.image.shape[0] == image.shape[0]
                  and n.frame.roll == src.frame.roll and n is not src]
    room = (left.x + d) - 4.0                          # at least a 4-column gap stays
    if candidates and room > 2.0 and rng.random() < NEIGHBOUR_SHARE:
        nb = candidates[int(rng.integers(len(candidates)))]
        nb_image, nb_truth = (nb.image, nb.truth) if nb.truth.right.state == EDGE \
            else mirror(nb.image, nb.truth)
        xr = nb_truth.right.x
        assert xr is not None
        w = int(rng.integers(1, int(room) + 1))         # neighbour columns to show
        s0 = int(np.floor(xr)) + 1 - w
        if s0 >= 0:
            new[:, :w] = nb_image[:, s0: s0 + w]
            out = EdgeResult(Side(EDGE, x=out.left.x, lo=out.left.lo, hi=out.left.hi,
                                  conf=out.left.conf, outer=xr - s0, x_top=out.left.x_top,
                                  x_bottom=out.left.x_bottom), out.right)
    if side == "right":
        new, out = mirror(new, out)
    return new, out


def target_from_result(result: EdgeResult, rows: int) -> np.ndarray:
    """(4, 428) class shares over the trimmed rows, from labelled sides.

    A tilted edge runs from x_top to x_bottom; the median row sits at x, which
    is what the label is."""
    j = np.arange(COLUMNS, dtype=np.float64)
    trimmed = np.arange(TRIM_ROWS, rows - TRIM_ROWS)
    mid = (trimmed[0] + trimmed[-1]) / 2.0
    acc = np.zeros((4, COLUMNS))

    def boundary(s: Side, row: int) -> float:
        assert s.x is not None
        if s.x_top is None or s.x_bottom is None:
            return s.x
        slope = (s.x_bottom - s.x_top) / max(trimmed[-1] - trimmed[0], 1)
        return s.x + (row - mid) * slope

    for row in trimmed:
        lo = boundary(result.left, row) if result.left.state == EDGE else -1e9
        hi = boundary(result.right, row) if result.right.state == EDGE else 1e9
        intervals = [(-1e9, 1e9, CLASS_BASE), (lo, hi, CLASS_PICTURE)]
        if result.left.state == EDGE and result.left.outer is not None:
            intervals.append((-1e9, result.left.outer, CLASS_NEIGHBOUR))
        if result.right.state == EDGE and result.right.outer is not None:
            intervals.append((result.right.outer, 1e9, CLASS_NEIGHBOUR))
        share = np.zeros((4, COLUMNS))
        for a, b, cls in intervals:
            cover = np.clip(np.minimum(b, j + 1) - np.maximum(a, j), 0.0, 1.0)
            share *= (1.0 - cover)[None, :]
            share[cls] += cover
        acc += share
    acc /= len(trimmed)
    return (acc / acc.sum(axis=0, keepdims=True)).astype(np.float32)


def make(srcs: list[Source], rng: np.random.Generator) -> tuple[np.ndarray, EdgeResult, str]:
    """One sample: a widened frame where one can be, else a frame as it is."""
    src = srcs[int(rng.integers(len(srcs)))]
    side = widenable(src)
    if side is not None and rng.random() < 0.85:
        image, truth = widen(src, side, rng, srcs)
        return image, truth, f"real:{src.frame.id}:widened-{side}"
    return src.image.copy(), src.truth, f"real:{src.frame.id}:as-is"
