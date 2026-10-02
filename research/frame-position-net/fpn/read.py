"""From column labels to the answer. Plain numpy; nothing here is learned.

The network gives, per column, the probability of each class in
`fpn.model.CLASSES`: this frame's picture, base, the neighbour's picture, no
film. This finds where this frame's picture begins and ends, says it in the
study's own terms (`EdgeResult`), and lets the study's `decide` turn that into
the move -- the same function every detector is scored through.

Per side, scanning in from the border:
- the border column is mostly "no film"              -> `no_film`
- there is almost no picture of this frame at all    -> `all_base` (a blank frame)
- the border column is already this frame's picture  -> `picture_to_border`
- otherwise                                          -> `edge`, with x the sum of
  the non-picture shares up to the picture, a fraction of a column, and
  `outer` the neighbour's share where it shows beyond the gap.

A side is refused when the labels near its boundary are not sure (top
probability under `SURE`). Picture in more than one piece is read as this
frame (the widest piece) and its neighbour beyond a gap, when base separates
them; without base between, the frame is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rps7200.direct import DirectScanner
from rps7200.framing import units_per_column
from tools.frame_edges.centre import Decision, columns_to_mm, decide, frame_columns
from tools.frame_edges.sides import (
    ALL_BASE,
    EDGE,
    NO_FILM,
    PICTURE_TO_BORDER,
    REFUSE,
    EdgeResult,
    Side,
)

from fpn.inputs import COLUMNS
from fpn.model import CLASSES

PICTURE, BASE, NEIGHBOUR, NO_FILM_CLASS = (CLASSES.index(c) for c in
                                            ("picture", "base", "neighbour", "no_film"))
#: Below this many columns of this frame's picture, the frame is blank.
BLANK_COLUMNS = 5.0
#: How far past the boundary the shares are summed, and how far either side
#: the labels must be sure.
REACH = 3
#: Default certainty; set on real-val by `fpn.abstain`.
SURE = 0.8
#: The least base that makes a second piece of picture the neighbour's: the
#: gaps between frames measure 4-34 columns (gapmodel's prior).
GAP_COLUMNS = 3


def _left(p: np.ndarray, sure: float) -> Side:
    """The left side from (4, W) probabilities."""
    width = p.shape[1]
    hard = p.argmax(axis=0)
    if hard[0] == NO_FILM_CLASS:
        return Side(NO_FILM, conf=float(p[NO_FILM_CLASS, 0]))
    picture = p[PICTURE] >= 0.5
    if not picture.any():
        return Side(ALL_BASE, conf=float(1.0 - p[PICTURE].max()))
    start = int(np.argmax(picture))
    if start == 0:
        return Side(PICTURE_TO_BORDER, conf=float(p[PICTURE, 0]))
    stop = min(width, start + REACH)
    x = float((1.0 - p[PICTURE, :stop]).sum())
    near = p[:, max(0, start - REACH): stop]
    conf = float(near.max(axis=0).min())
    neighbour = float(p[NEIGHBOUR, :start].sum())
    outer = neighbour if neighbour >= 1.0 else None
    if conf < sure:
        return Side(REFUSE, conf=conf, note=f"unsure near column {start} ({conf:.2f})")
    return Side(EDGE, x=x, lo=x - 0.5, hi=x + 0.5, conf=conf, outer=outer)


def _mirror(side: Side, width: int) -> Side:
    def flip(v: float | None) -> float | None:
        return None if v is None else width - v

    return Side(side.state, x=flip(side.x), lo=flip(side.hi), hi=flip(side.lo),
                conf=side.conf, outer=flip(side.outer), note=side.note)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) of each run of True."""
    edges_ = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
    return [(int(a), int(b)) for a, b in zip(edges_[::2], edges_[1::2])]


def _neighbours_named(p: np.ndarray) -> tuple[np.ndarray, str | None]:
    """Picture in more than one piece: the widest piece is this frame, and a
    piece cut off from it by at least `GAP_COLUMNS` of base is the neighbour
    beyond the gap -- picture, base, picture on one side is no other strip.
    Pieces not cut off by base cannot be told apart and are refused."""
    runs = _runs(p[PICTURE] >= 0.5)
    if len(runs) <= 1:
        return p, None
    frame = max(runs, key=lambda r: r[1] - r[0])
    base = p[BASE] >= 0.5
    q = p.copy()
    for start, stop in runs:
        if (start, stop) == frame:
            continue
        between = base[stop: frame[0]] if stop <= frame[0] else base[frame[1]: start]
        if int(between.sum()) < GAP_COLUMNS:
            return p, "picture in more than one piece, with no gap between"
        q[NEIGHBOUR, start:stop] += q[PICTURE, start:stop]
        q[PICTURE, start:stop] = 0.0
    return q, None


def edges(p: np.ndarray, sure: float = SURE) -> EdgeResult:
    """(4, 428) class probabilities -> the frame's two sides."""
    if p.shape != (len(CLASSES), COLUMNS):
        raise ValueError(f"expected ({len(CLASSES)}, {COLUMNS}), got {p.shape}")
    p, split = _neighbours_named(p)
    if split is not None:
        return EdgeResult(Side(REFUSE, note=split), Side(REFUSE, note=split), {"refused": split})
    total = float(p[PICTURE].sum())
    left = _left(p, sure)
    right = _mirror(_left(p[:, ::-1], sure), COLUMNS)
    if total < BLANK_COLUMNS:
        for name, side in (("left", left), ("right", right)):
            if side.state != NO_FILM:
                side = Side(ALL_BASE, conf=float(1.0 - p[PICTURE].max()))
            if name == "left":
                left = side
            else:
                right = side
    return EdgeResult(left, right, {"picture_columns": total})


@dataclass(frozen=True)
class Answer:
    action: str              # "left", "right", "none", "refuse"
    units: float | None      # signed, positive moves the picture to higher columns
    param: int | None        # the SLIDE param, from the driver's own conversion
    why: str = ""


def answer(result: EdgeResult) -> Answer:
    """The move, at the runtime frame width, and the param the driver would send."""
    d: Decision = decide(result, COLUMNS, frame_columns())
    if d.action in ("refuse", "none") or d.units is None:
        return Answer(d.action, d.units, None, d.why)
    columns = d.units / units_per_column(COLUMNS)
    param = DirectScanner.param_for_mm(columns_to_mm(columns))
    return Answer(d.action, d.units, param, d.why)
