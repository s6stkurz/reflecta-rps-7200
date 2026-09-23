"""From two edges to one move that centres the frame.

`decide` is `research/frame-edge/common.py:decide` (commit 32a98e5), copied.
The frame is wider than the aperture -- `FRAME_WIDTH_UNITS`, measured from
prescans of one frame -- so a centred frame shows no base on either side and
overhangs both edges by the same amount, ``-t`` columns.

Two conversions leave this module, and each goes to the scale its user reads:

* **units** for anything a person reads (the window's captions);
* **`offset_mm`** for the hold loop, as ``columns * APERTURE_MM / width`` --
  the exact scale `framing.measure_shift_mm` verifies a move in, so the loop
  lands on the column the detector asked for. `MM_PER_UNIT` is the actuator's
  model and differs from it by 0.2%; it is not used for this.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sides import ALL_BASE, EDGE, NO_FILM, PICTURE_TO_BORDER, EdgeResult
from .units import (
    APERTURE_MM, FRAME_WIDTH_UNITS, PRESCAN_COLUMNS, SMALLEST_MOVE, units_per_column,
)


def frame_columns(width: float = PRESCAN_COLUMNS,
                  frame_units: float = FRAME_WIDTH_UNITS) -> float:
    """The frame's width in columns of a pass ``width`` columns wide."""
    return frame_units / units_per_column(int(width))


def columns_to_mm(columns: float, width: float = PRESCAN_COLUMNS) -> float:
    """A move of ``columns`` prescan columns, in the hold loop's `offset_mm` scale."""
    return float(columns) * APERTURE_MM / float(width)


@dataclass
class Decision:
    """What the transport should do: ``action`` is right/left/none/refuse."""

    action: str
    units: float | None
    lo: float | None = None
    hi: float | None = None
    left_units: float | None = None
    right_units: float | None = None
    why: str = ""

    def caption(self) -> str:
        if self.action == "refuse":
            return "refuse"
        if self.action == "none":
            return "none" if self.units is None else f"none ({self.units:+.1f} u)"
        arrow = "->" if self.action == "right" else "<-"
        return f"{self.units:+.1f} u {arrow}"


def target_base(frame_width: float, width: float = PRESCAN_COLUMNS) -> float:
    """Columns of base each side shows when the frame is centred: ``t``.

    Negative when the frame is wider than the aperture, meaning a centred frame
    overhangs both edges by ``-t``.
    """
    return (width - frame_width) / 2.0


def decide(result: EdgeResult, width: int, frame_width: float,
           bias_units: float = 0.0, deadband: float = SMALLEST_MOVE,
           agree_units: float = 2.0) -> Decision:
    """Turn two sides into one move, in units, positive = picture to the right.

    Each side proposes an interval of moves:

    * ``EDGE`` on the left, ``b_L`` columns of base: ``u = -(b_L - t)``.
    * ``EDGE`` on the right, ``b_R`` columns: ``u = +(b_R - t)``.
    * ``PICTURE_TO_BORDER`` on the left says ``b_L <= 0``, so ``u >= +t``; on
      the right, ``u <= -t``. Half-open, not a point.

    Two edges are averaged and must agree within ``agree_units``. A lone
    half-open side cannot place the frame, so it refuses -- unless the other
    side bounds it too, in which case the move is the interval's middle.
    ``bias_units`` is added to every answer: it is the target, kept apart from
    the detector so a roll-wide offset can be tested without touching it.
    """
    upc = units_per_column(width)
    t = target_base(frame_width, width)
    lo, hi = -np.inf, np.inf
    points: list[float] = []
    per: dict[str, float | None] = {"left": None, "right": None}
    for name, sign in (("left", -1.0), ("right", +1.0)):
        s = result.side(name)
        if s.state in (NO_FILM, ALL_BASE):
            return Decision("refuse", None, why=f"{name}: {s.state}")
        if s.state == EDGE and s.x is not None:
            b = s.base_width(name, width)
            assert b is not None
            u = sign * (b - t) * upc
            per[name] = u
            points.append(u)
        elif s.state == PICTURE_TO_BORDER:
            if name == "left":
                lo = max(lo, t * upc)
            else:
                hi = min(hi, -t * upc)
    if points:
        if len(points) == 2 and abs(points[0] - points[1]) > agree_units:
            return Decision("refuse", None, left_units=per["left"],
                            right_units=per["right"],
                            why=f"sides disagree by {abs(points[0] - points[1]):.1f} u")
        u = float(np.mean(points))
        # A border side still constrains a single edge: it cannot contradict it.
        if u < lo - agree_units or u > hi + agree_units:
            return Decision("refuse", None, left_units=per["left"],
                            right_units=per["right"],
                            why="edge contradicts the other side's border")
        ulo, uhi = u, u
    elif np.isfinite(lo) and np.isfinite(hi):
        if lo > hi + agree_units:
            return Decision("refuse", None, why="both borders, frame narrower than aperture")
        u = (lo + hi) / 2.0
        ulo, uhi = min(lo, hi), max(lo, hi)
    else:
        return Decision("refuse", None, why="no side places the frame")
    u += bias_units
    ulo += bias_units
    uhi += bias_units
    # "none" on the best estimate, not the interval: no base on either side
    # leaves the frame anywhere within its overhang, and every one of those
    # positions shows no base -- which is what "sits right" means.
    if abs(u) < deadband:
        return Decision("none", u, ulo, uhi, per["left"], per["right"])
    return Decision("right" if u > 0 else "left", u, ulo, uhi, per["left"], per["right"])
