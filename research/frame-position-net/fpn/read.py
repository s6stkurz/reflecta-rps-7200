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
probability under `SURE`), or when this frame's picture is not one piece --
picture, base, picture again on one side is no strip there is.
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


def edges(p: np.ndarray, sure: float = SURE) -> EdgeResult:
    """(4, 428) class probabilities -> the frame's two sides."""
    if p.shape != (len(CLASSES), COLUMNS):
        raise ValueError(f"expected ({len(CLASSES)}, {COLUMNS}), got {p.shape}")
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
    picture = (p[PICTURE] >= 0.5).astype(np.int8)
    runs = int(np.count_nonzero(np.diff(picture) == 1)) + int(picture[0])
    result = EdgeResult(left, right, {"picture_runs": runs, "picture_columns": total})
    if runs > 1:
        result = EdgeResult(Side(REFUSE, note="picture in more than one piece"),
                            Side(REFUSE, note="picture in more than one piece"), result.debug)
    return result


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
