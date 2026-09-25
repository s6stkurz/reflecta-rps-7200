"""The vote of four detectors -- the study's `ensemble_v2`, copied.

From `research/frame-edge/algos/ensemble.py` (the vote) and
`research/frame-edge/algos/ensemble_v2.py` (its round-2 members and the one
rule it adds), commit 32a98e5. The logic is the study's, line for line; only
the imports changed, and a member that raises abstains (`_member`) where the
study's run would have stopped -- which changes no answer the study stored,
since on its frames none raised. `tests/test_frame_edges_parity.py` holds it
to the study's stored answers.

The members were chosen by one measurement, not by their scores alone: on the
dev frames **no two of them are wrong on the same side**, so a vote can
outnumber any one of them. Per side:

* ``no_film`` if two members say so -- the empty gate is unmistakable.
* The **edge answers are clustered**: those within ``AGREE`` columns of their
  median. An edge stands when at least two members are in the cluster and
  they are at least as many as the members saying picture-to-border. Its
  position is the cluster's median, its interval the cluster's spread.
* Otherwise picture-to-border stands when at least two members say so.
* Anything else is refused -- **except** a gap with the neighbour's picture
  beyond it, which stands on one member's word when no other member places an
  edge elsewhere on that side (round 2: two-sided evidence the border-growing
  members cannot see).

A tie between an edge pair and a border pair goes to the edge, on purpose:
some members report base narrower than a column and others answer "border"
there by design, not by evidence. With the frame wider than the aperture a
sliver is worth several units of move.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from . import changepoint, chroma, gapmodel, stepline
from .sides import EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, EdgeResult, Side

#: The role each member plays in the vote, in the order the study voted them.
#: The order matters: `lone_gap` takes the first member that qualifies.
MEMBERS: dict[str, Any] = {"changepoint": changepoint, "chroma": chroma,
                           "stepline": stepline, "gapmodel": gapmodel}
#: Two edge answers are one edge when they agree this closely, in columns.
#: The labels themselves agree to 0.2 columns (p90) and the members' edge
#: errors are 0.15-0.4 at p90, so a column is generous for the same edge and
#: tight for two different ones.
AGREE = 1.0
MIN_VOTES = 2


def vote(sides: dict[str, Side]) -> Side:
    """One side's answer from the members' answers (the round-1 vote)."""
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
    """The round-2 vote: the round-1 vote, then a lone gap-with-neighbour."""
    v = vote(sides)
    if v.state == EDGE:
        return v
    return lone_gap(sides) or v


def _member(role: str, module: Any, image: np.ndarray,
            ctx: dict[str, Any]) -> tuple[EdgeResult, str | None]:
    """One member's answer -- or, when it raises, its abstention and why.

    A member that fails refuses both sides, which is what a vote already
    does with a member that cannot see: it is left out, and the others are
    counted as they would be without it. Contained here, per member, because
    one member's arithmetic cost the whole frame otherwise, and on the roll
    path the whole roll. `gapmodel` divides by a base level that is 0 on a
    near-black 8-bit prescan -- fogged leader, an opaque strip end, sparse
    1-count noise -- and its ZeroDivisionError left `direct.scan_roll`'s
    net, which catches the device's failures and ValueError only, so the roll
    ended there, sometimes with the film already moved.
    """
    try:
        return module.detect(image, ctx), None
    except Exception as exc:                                  # noqa: BLE001
        why = f"{role} failed: {type(exc).__name__}: {exc}"
        return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why)), why


def detect(image: np.ndarray, ctx: dict[str, Any]) -> EdgeResult:
    """Every member on one prescan, then the vote per side.

    ``image`` is float ``(H, W, 3)`` in the counts of the file it came from.
    ``ctx`` carries ``film_type`` (``c41``/``bw``/``unknown``), ``dtype`` (of
    the source pixels) and ``roll`` (`roll.Summary` records of the *other*
    frames of the walk; may be empty). A member that raises abstains, and
    says so in ``debug["members"]``; see `_member`.
    """
    answers = {role: _member(role, module, image, ctx)
               for role, module in MEMBERS.items()}
    results = {role: result for role, (result, _why) in answers.items()}
    left = vote_v2({k: r.left for k, r in results.items()})
    right = vote_v2({k: r.right for k, r in results.items()})
    debug: dict[str, dict[str, Any]] = {
        k: {"left": [r.left.state, r.left.x], "right": [r.right.state, r.right.x]}
        for k, r in results.items()}
    for k, (_result, why) in answers.items():
        if why is not None:
            debug[k]["failed"] = why
    return EdgeResult(left, right, {"members": debug})
