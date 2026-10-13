"""The 36 mm frame model, kept so stored studies replay as they ran. HISTORY.

**Nothing here aims a roll, and nothing in `rps7200` imports it.** Until
2026-10-13 the strip-level detector in `rps7200/framing.py` placed a 36.0 mm
frame in the 36.5 mm aperture: assumed rather than measured, 4.6 units of
slack, a target of +2.3 units of base at the left, gaps believed up to 24.6
units, and a third member, `right_gap_closure`, that read the frame off the
right edge when the picture between two gaps closed to that frame. Stefan
retired it fully that day (the "36mm-model" decision): every roll is aimed by
the measured frame, `framing.FRAME_MODEL`, slides and Kodachrome included.

What stayed behind is what the offline studies need to compute what they
computed when they were run -- `tools/roll_registration_study.py`'s per-frame
and ensemble reports, and `research/frame-edge/baseline_current.py` and
`compare_proposals.py`, whose stored results came from this model:

* `HISTORIC_MODEL`, the 36 mm frame as a `framing.FrameModel`, so the driver's
  own `picture_start`, `edge_bands`, `frame_offset` and `strip_offsets`
  compute through it exactly what they computed before -- the functions were
  not copied, only the three numbers they read;
* `registration_error` and `right_gap_closure` as they were, and the
  constants only they read (`MAX_REGISTRATION_UNITS`,
  `FRAME_WIDTH_SPREAD_UNITS`);
* `combine` and `propose_offsets` as the three-member ensemble ran them, and
  `walk()`, a `StripWalk` that calibrates its base through the old gap bound.

Each constant is its historic millimetre literal over `MM_PER_UNIT`, as
`framing`'s were, so a column derived from it is the column it always was.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.framing import (                 # noqa: E402
    APERTURE_UNITS,
    BASE_FLATNESS,
    BLANK_CONTRAST,
    EDGE_FRACTION,
    FULL_FRAME,
    GAP_MIN_UNITS,
    FilmBase,
    FrameModel,
    Reading,
    StripWalk,
    _clip01,
    _grey,
    base_runs,
    combine as _combine,
    film_base,
    fill_from_neighbours,
    frame_contrast,
    frame_offset as _frame_offset,
    gap_edges,
    predict_from_strip,
    strip_offsets,
)
from rps7200.protocol import (                # noqa: E402
    COORD_PER_INCH,
    MM_PER_INCH,
    MM_PER_UNIT,
    say,
)

#: The exposed image on 135 film in the old model: 36.0 mm, 340.6 units on the
#: hold's scale. Assumed rather than measured; the frame measured since is
#: `framing.FRAME_WIDTH_UNITS`, 350.6 on the reader's scale and wider than the
#: aperture.
HISTORIC_FRAME_UNITS = 36.0 / MM_PER_UNIT

#: Where a frame's picture was aimed to begin: half the slack the 36 mm frame
#: left, +2.3 units of base at the left.
TARGET_GAP_UNITS = (APERTURE_UNITS - HISTORIC_FRAME_UNITS) / 2.0

#: The widest band believed to be a gap: 2.6 mm, 24.6 units.
MAX_GAP_UNITS = 2.6 / MM_PER_UNIT

#: How far the picture between two gaps could sit from a whole 36 mm frame
#: before one of the bands was called not a gap: 0.30 mm, 2.84 units.
FRAME_WIDTH_SPREAD_UNITS = 0.30 / MM_PER_UNIT

#: All the room the 36 mm frame had: about 4.6 units of slack (0.49 mm) in
#: the 345.2-unit aperture, past which `registration_error` called a reading
#: detector error.
MAX_REGISTRATION_UNITS = 0.49 / MM_PER_UNIT

#: The 36 mm model, for the driver's functions to compute through.
HISTORIC_MODEL = FrameModel(
    frame_units=HISTORIC_FRAME_UNITS,
    target_units=TARGET_GAP_UNITS,
    max_gap_units=MAX_GAP_UNITS,
    name="36 mm frame (retired 2026-10-13)",
)

#: The correction bound the three-member ensemble used: 22.3 units.
MAX_CORRECTION_UNITS = HISTORIC_MODEL.max_correction_units


def registration_error(
    image: np.ndarray, full_frame: tuple[int, int, int, int] = FULL_FRAME
) -> tuple[float | None, str]:
    """How far the frame sits from centred, in units, or ``None`` and why.

    As it stood in `rps7200/framing.py` until 2026-10-13, unchanged. Positive
    means the frame is too far towards +x, so a gap has appeared on the left.
    Returns ``None`` when the reading cannot be trusted, which is more often
    than not and is the point: acting on a bad measurement drives the
    transport for nothing.
    """
    if frame_contrast(image) < BLANK_CONTRAST:
        return None, "no picture in the window"

    left, right = gap_edges(image)
    if left and right:
        return None, f"gap at both edges ({left}, {right} px) -- not drift"
    if not left and not right:
        # Not "registered". This detector anchors its runs at column 0, so a
        # gap with a sliver of the neighbour beside it reads as no gap at all,
        # and a frame drifted far enough that the gap sits in the middle reads
        # the same. Four of nine such calls on a real sixteen-frame walk were
        # provably false, and each returned a positive assertion of correctness
        # that nothing downstream could tell from a measurement.
        return None, "no gap at either edge -- this cannot see where the frame is"

    width = image.shape[1] or 1
    px = left or -right
    off = (px * (full_frame[2] - full_frame[0] + 1) / width * MM_PER_INCH
           / COORD_PER_INCH / MM_PER_UNIT)
    if abs(off) > MAX_REGISTRATION_UNITS:
        return None, (
            f"{say(abs(off), signed=False)} exceeds the "
            f"{say(MAX_REGISTRATION_UNITS, signed=False)} the aperture "
            "allows -- detector error, not film movement"
        )
    return off, f"{px:+d} px of gap"


def frame_offset(image: np.ndarray, base: FilmBase, *,
                 aperture_units: float = APERTURE_UNITS) -> Reading:
    """The left-gap member as the 36 mm model read it: the driver's own
    `frame_offset` through `HISTORIC_MODEL`."""
    return _frame_offset(image, base, aperture_units=aperture_units,
                         model=HISTORIC_MODEL)


def right_gap_closure(
    image: np.ndarray, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    frame_units: float = HISTORIC_FRAME_UNITS,
) -> Reading:
    """Where this frame sits, from the gap at the *right* -- and a check.

    As it stood in `rps7200/framing.py` until 2026-10-13, unchanged. A second
    reading of the same quantity off the opposite edge, believed only when
    the picture between the two gaps closed to a 36 mm frame within
    `FRAME_WIDTH_SPREAD_UNITS`.

    Abstains whenever the right edge holds no gap, which was most of the time
    and was geometry rather than bad luck: 345.2 units of aperture less ~340.6
    of frame leaves 4.6 units of slack, and `GAP_MIN_UNITS` is 2.37, so both
    edges can show a readable gap only in a window narrower than the
    detector's own resolution. Under the measured frame, wider than the
    aperture, it can never answer -- which is why it was retired.

    On walk D a loose closure test accepted this reading on three frames and
    placed the picture at 334.8 units each time -- under the 339.6 the strip
    measures -- so what it had found was picture at the base level, not a gap.
    """
    grey = _grey(image)
    if grey.size == 0:
        return Reading(None, 0.0, "closure", "nothing to measure")
    width = grey.shape[1]
    u_px = aperture_units / width
    smallest = max(1, int(round(GAP_MIN_UNITS / u_px)))
    widest = int(round(MAX_GAP_UNITS / u_px))
    margin_px = int(round(width * EDGE_FRACTION))

    runs = base_runs(image, base.level,
                     flatness=max(base.flatness * 2.0, BASE_FLATNESS),
                     min_run=smallest)
    detail: dict[str, Any] = {"runs": runs, "units_per_px": round(u_px, 4)}
    left = [(s, n) for s, n in runs if s <= margin_px]
    right = [(s, n) for s, n in runs if s + n >= width - margin_px]
    if not left:
        return Reading(None, 0.0, "closure", "no gap at the left edge", detail)
    if not right:
        return Reading(None, 0.0, "closure",
                       "no base at the right edge -- the picture runs to the "
                       "last column and is being cut", detail)
    if right[-1][1] > widest:
        return Reading(None, 0.0, "closure",
                       f"the right band is "
                       f"{say(right[-1][1] * u_px, signed=False)}, "
                       f"wider than the {say(MAX_GAP_UNITS, signed=False)} "
                       "a gap can be", detail)

    start = left[0][0] + left[0][1]
    end = right[-1][0]
    if end <= start:
        return Reading(None, 0.0, "closure",
                       "the two bands meet -- no picture between them", detail)

    # The free error bar: with both gaps in view the picture is a frame wide.
    picture = (end - start) * u_px
    residual = picture - frame_units
    detail.update(picture_units=round(picture, 2),
                  closure_units=round(residual, 2))
    if abs(residual) > FRAME_WIDTH_SPREAD_UNITS:
        return Reading(None, 0.0, "closure",
                       f"the picture between the bands is "
                       f"{say(picture, signed=False)}, not the "
                       f"{say(frame_units, signed=False)} a frame is -- one "
                       "of them is not a gap", detail)
    return Reading(
        units=(aperture_units - TARGET_GAP_UNITS) - end * u_px,
        margin=_clip01(1.0 - abs(residual) / FRAME_WIDTH_SPREAD_UNITS),
        source="closure",
        precision=u_px,
        reason=(f"picture {say(picture, signed=False)}, closes "
                f"to {say(residual)}"),
        detail=detail,
    )


def combine(readings) -> tuple[float | None, dict]:
    """The driver's `combine` with the bound the 36 mm model gave it."""
    return _combine(readings, bound_units=MAX_CORRECTION_UNITS)


def walk() -> StripWalk:
    """A walk with no reader, calibrating its base through the old gap bound.

    Its `judge` is the driver's, which asks the two members it has today; the
    three-member judgment the study replays is `judge` below.
    """
    return StripWalk(model=HISTORIC_MODEL)


def judge(walk: StripWalk, number: int, image: np.ndarray, *,
          prior: Reading) -> tuple[float | None, dict, Reading, Reading]:
    """One frame as the three-member walk judged it: the left gap, the right
    closure and the prior, combined. Returns the decision, its detail, and
    the left and closure readings for the study to print."""
    assert walk.base is not None
    left = frame_offset(image, walk.base)
    closure = right_gap_closure(image, walk.base)
    decision, detail = combine([left, closure, prior])
    return decision, detail, left, closure


def propose_offsets(
    frames, *, aperture_units: float = APERTURE_UNITS, target: str = "centre",
) -> tuple[dict[int, float], dict[int, dict]]:
    """Every frame's proposal as the three-member whole-strip pass made it,
    until 2026-10-13: `framing.propose_offsets` with the closure member and
    the 36 mm model. The sheet has not seeded from this since
    `tools/frame_edges` replaced it; the studies compare against it."""
    images = [im for _n, im in frames]
    base, base_detail = film_base(images, model=HISTORIC_MODEL)
    notes: dict[int, dict] = {}
    if base is None:
        why = ("the strip's base level could not be calibrated: "
               + str(base_detail.get("reason", "")))
        return {}, {int(n): {"reason": why, "source": "none"} for n, _ in frames}

    placed, _detail = strip_offsets(frames, base, aperture_units=aperture_units,
                                    target=target, model=HISTORIC_MODEL)

    proposals: dict[int, float] = {}
    for number, image in frames:
        number = int(number)
        left = _frame_offset(image, base, aperture_units=aperture_units,
                             model=HISTORIC_MODEL)
        closure = right_gap_closure(image, base, aperture_units=aperture_units)
        strip = predict_from_strip(placed, number)
        decision, detail = combine([left, closure, strip])
        notes[number] = dict(detail, base_level=round(base.level, 2))
        if decision is not None:
            proposals[number] = decision
            notes[number]["source"] = "measured"
        elif left.units is not None and abs(left.units) <= MAX_CORRECTION_UNITS:
            proposals[number] = left.units
            notes[number]["source"] = "unconfirmed"
        else:
            notes[number]["source"] = "none"

    filled = fill_from_neighbours(proposals, [int(n) for n, _ in frames])
    for number, value in filled.items():
        proposals[number] = value
        notes[number] = dict(notes.get(number, {}), source="neighbours",
                             reason="predicted from the frames either side")
    return proposals, notes
