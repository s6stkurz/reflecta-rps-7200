"""The comprehension: four detector families vote, side by side.

The members were chosen by one measurement, not by their scores alone: on the
98 dev frames **no two of them are wrong on the same side**. Each family's
mistakes are its own -- chroma misses base against a wall of base colour,
stepline a line that scatters, gapmodel a deep band it cannot refute -- so a
vote can outnumber any one of them. texture (13 wrong sides, shared with
others) and baserun (honest, but refuses 28 sides) are left out; learned is
added only if it earns it.

Per side:

* ``no_film`` if two members say so -- the empty gate is unmistakable.
* The **edge answers are clustered**: those within ``AGREE`` columns of their
  median. An edge stands when at least two members are in the cluster and
  they are at least as many as the members saying picture-to-border. Its
  position is the cluster's median, its interval the cluster's spread.
* Otherwise picture-to-border stands when at least two members say so.
* Anything else is refused.

A tie between an edge pair and a border pair goes to the edge, on purpose:
three members report base narrower than a column (slivers) and two answer
"border" there by design, not by evidence. With the frame wider than the
aperture a sliver is worth ~7 units of move, so the members that can see it
are the ones to believe.

No parameter here was fitted: two votes, one column, ties to the edge.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, EdgeResult, Side,
)

MEMBERS = ("changepoint", "chroma", "stepline", "gapmodel")
#: Two edge answers are one edge when they agree this closely, in columns.
#: The labels themselves agree to 0.2 columns (p90) and the members' edge
#: errors are 0.15-0.4 at p90, so a column is generous for the same edge and
#: tight for two different ones.
AGREE = 1.0
MIN_VOTES = 2

_modules: dict[str, Any] = {}


def _member(name: str) -> Any:
    if name not in _modules:
        _modules[name] = importlib.import_module(name)
    return _modules[name]


def vote(sides: dict[str, Side]) -> Side:
    """One side's answer from the members' answers."""
    answers = {k: s for k, s in sides.items() if s.state != REFUSE}
    if sum(s.state == NO_FILM for s in answers.values()) >= MIN_VOTES:
        return Side(NO_FILM, conf=0.9, note="gate: " + ",".join(
            k for k, s in answers.items() if s.state == NO_FILM))
    edges = {k: s for k, s in answers.items() if s.state == EDGE and s.x is not None}
    borders = [k for k, s in answers.items() if s.state == PICTURE_TO_BORDER]
    if len(edges) >= MIN_VOTES:
        xs = np.array([float(s.x) for s in edges.values() if s.x is not None])
        centre = float(np.median(xs))
        cluster = {k: s for k, s in edges.items()
                   if s.x is not None and abs(float(s.x) - centre) <= AGREE}
        if len(cluster) >= MIN_VOTES and len(cluster) >= len(borders):
            cx = np.array([float(s.x) for s in cluster.values() if s.x is not None])
            x = float(np.median(cx))
            tops = [float(s.x_top) - float(s.x) for s in cluster.values()
                    if s.x_top is not None and s.x is not None]
            bottoms = [float(s.x_bottom) - float(s.x) for s in cluster.values()
                       if s.x_bottom is not None and s.x is not None]
            outers = [float(s.outer) for s in cluster.values() if s.outer is not None]
            return Side(
                EDGE, x=x,
                lo=float(min(cx.min(), x - 0.25)), hi=float(max(cx.max(), x + 0.25)),
                conf=len(cluster) / len(MEMBERS),
                outer=float(np.median(outers)) if len(outers) >= MIN_VOTES else None,
                x_top=x + float(np.median(tops)) if len(tops) >= MIN_VOTES else None,
                x_bottom=x + float(np.median(bottoms)) if len(bottoms) >= MIN_VOTES else None,
                note="edge: " + ",".join(cluster) + (
                    "; border: " + ",".join(borders) if borders else ""))
    if len(borders) >= MIN_VOTES:
        return Side(PICTURE_TO_BORDER, conf=len(borders) / len(MEMBERS),
                    note="border: " + ",".join(borders) + (
                        "; edge: " + ",".join(edges) if edges else ""))
    return Side(REFUSE, note="no agreement: " + ", ".join(
        f"{k}={s.state}" + (f"@{s.x:.1f}" if s.x is not None else "")
        for k, s in sides.items()))


def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    results = {name: _member(name).detect(image, ctx) for name in MEMBERS}
    left = vote({k: r.left for k, r in results.items()})
    right = vote({k: r.right for k, r in results.items()})
    debug = {k: {"left": [r.left.state, r.left.x], "right": [r.right.state, r.right.x]}
             for k, r in results.items()}
    return EdgeResult(left, right, {"members": debug})
