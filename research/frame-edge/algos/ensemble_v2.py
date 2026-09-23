"""Round 2 of the comprehension: two improved members, and one rule more.

Members: `changepoint_v2` and `gapmodel_v2` (round 2, tuned on the main dev
set and library 2's dev2), with `chroma` and `stepline` as they were in round
1. The vote is `ensemble.vote`, unchanged, plus one rule:

* **A gap with the neighbour's picture beyond it stands on one member's word**
  -- when no other member places an edge anywhere else on that side. A gap
  seen with picture on *both* sides is two-sided evidence (a straight band of
  base between two pictures), and it is what the other members, which grow
  base from the border, cannot see. On the dev sets this costs nothing and
  recovers b0922b_09; its one cost with round-1 members (the tile grout on
  x0920_73) is refused by gapmodel_v2's colour gate.

Chosen on dev only, before any test split was scored with it
(`results/ensemble_v2/iter01/PREREGISTERED.txt`).
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

from common import EDGE, EdgeResult, Side  # noqa: E402
from ensemble import AGREE, vote  # noqa: E402

#: the role each member plays in the vote, and the module that plays it now
MEMBERS = {"changepoint": "changepoint_v2", "chroma": "chroma",
           "stepline": "stepline", "gapmodel": "gapmodel_v2"}

_modules: dict[str, Any] = {}


def _member(module: str) -> Any:
    if module not in _modules:
        _modules[module] = importlib.import_module(module)
    return _modules[module]


def lone_gap(sides: dict[str, Side]) -> Side | None:
    """A member's gap-with-a-neighbour that no other member contradicts."""
    for name, s in sides.items():
        if s.state != EDGE or s.x is None or s.outer is None:
            continue
        elsewhere = any(o.state == EDGE and o.x is not None and abs(o.x - s.x) > AGREE
                        for k, o in sides.items() if k != name)
        if not elsewhere:
            return Side(EDGE, x=s.x, lo=s.lo, hi=s.hi, conf=0.3, outer=s.outer,
                        x_top=s.x_top, x_bottom=s.x_bottom,
                        note=f"gap with neighbour, one vote: {name}")
    return None


def vote_v2(sides: dict[str, Side]) -> Side:
    v = vote(sides)
    if v.state == EDGE:
        return v
    return lone_gap(sides) or v


def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    results = {role: _member(mod).detect(image, ctx) for role, mod in MEMBERS.items()}
    left = vote_v2({k: r.left for k, r in results.items()})
    right = vote_v2({k: r.right for k, r in results.items()})
    debug = {k: {"left": [r.left.state, r.left.x], "right": [r.right.state, r.right.x]}
             for k, r in results.items()}
    return EdgeResult(left, right, {"members": debug})
