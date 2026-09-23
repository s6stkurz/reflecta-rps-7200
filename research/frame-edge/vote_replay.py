"""Replay voting rules on the members' stored answers -- no detector is run.

    uv run --no-sync python vote_replay.py                              # main dev
    FRAME_EDGE_WORK=$PWD/lib2 uv run --no-sync python vote_replay.py    # library 2 dev2

    ... --members changepoint_v2:iter04,chroma:iter04,...   to replay other versions

Each rule sees, per side, the four members' `Side` answers exactly as
`ensemble.detect` would, and is scored with `evaluate.score` on the dev split.
Rules are only ever compared on dev; the test splits are not read.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "algos"))

import evaluate  # noqa: E402
from common import (  # noqa: E402
    EDGE, PICTURE_TO_BORDER, REFUSE, RESULTS, EdgeResult, Side, load_results,
)

#: `ensemble` lives in algos/, which only the runtime path reaches
vote = importlib.import_module("ensemble").vote

DEFAULT = "changepoint:iter04,chroma:iter04,stepline:iter04,gapmodel:iter04"
Rule = Callable[[dict[str, Side]], Side]


def rule_base(sides: dict[str, Side]) -> Side:
    """The pre-registered vote."""
    return vote(sides)


def _lone_interior(sides: dict[str, Side], member: str) -> Side | None:
    """``member``'s gap-with-a-neighbour, if nobody places an edge elsewhere."""
    g = sides.get(member)
    if g is None or g.state != EDGE or g.x is None or g.outer is None:
        return None
    for k, s in sides.items():
        if k != member and s.state == EDGE and s.x is not None and abs(s.x - g.x) > 1.0:
            return None
    return Side(EDGE, x=g.x, lo=g.lo, hi=g.hi, conf=0.3, outer=g.outer,
                x_top=g.x_top, x_bottom=g.x_bottom, note=f"lone interior gap: {member}")


def rule_interior(sides: dict[str, Side]) -> Side:
    """The vote, but gapmodel's gap-with-a-neighbour stands unless contradicted."""
    v = vote(sides)
    if v.state == EDGE:
        return v
    return _lone_interior(sides, "gapmodel") or v


def rule_any_interior(sides: dict[str, Side]) -> Side:
    """As `rule_interior`, for any member that reports the neighbour beyond the gap."""
    v = vote(sides)
    if v.state == EDGE:
        return v
    for m in sides:
        got = _lone_interior(sides, m)
        if got is not None:
            return got
    return v


def rule_edge_or_refuse(sides: dict[str, Side]) -> Side:
    """A lone edge no longer loses to border votes: the side is refused instead."""
    v = vote(sides)
    if v.state == PICTURE_TO_BORDER and any(s.state == EDGE for s in sides.values()):
        return Side(REFUSE, note="lone edge against border votes")
    return v


RULES: dict[str, Rule] = {
    "base (pre-registered)": rule_base,
    "gapmodel interior stands": rule_interior,
    "any interior stands": rule_any_interior,
    "lone edge -> refuse": rule_edge_or_refuse,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--members", default=DEFAULT)
    args = ap.parse_args()
    members = [m.split(":") for m in args.members.split(",")]
    stored = {name: load_results(RESULTS / name / it / "results.json") for name, it in members}
    ids = set.intersection(*(set(r) for r in stored.values()))
    for label, rule in RULES.items():
        out: dict[str, EdgeResult] = {}
        for fid in ids:
            sides = {n.split("_v")[0]: stored[n][fid] for n, _ in members}
            out[fid] = EdgeResult(rule({k: r.left for k, r in sides.items()}),
                                  rule({k: r.right for k, r in sides.items()}))
        s = evaluate.score(out, None, split="dev")
        sd, dc = s["side"], s["decision"]
        print(f"{label:26s} side {sd['accuracy']:.3f}  false-base {sd['false_base']}  "
              f"missed {sd['missed']}  refused {sd['refused']}  | move ok {dc['ok']}/{dc['n']} "
              f"bad {dc['bad']} sign {dc['sign_errors']}")
    _ = np  # numpy is the only dependency the rules may use


if __name__ == "__main__":
    main()
