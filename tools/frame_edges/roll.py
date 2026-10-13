"""What one prescan tells the others of its walk -- made once per frame.

Two members use the rest of the roll. `chroma` confirms a base level that
recurs on other frames of the same bit depth; `gapmodel` takes the gap width
and the base colour the other frames measured for themselves. The study kept
these in module caches keyed by frame id, filled from its dataset on demand
(`research/frame-edge/algos/chroma.py`, `gapmodel_v2.py`). A window that stays
open sees new prescans under old frame numbers, so here a `Summary` is made
once per frame per proposal run and handed to the others -- N summaries for a
sheet of N frames, nothing kept between runs.

The numbers are exactly the study's: the same functions on the same float32
pixels (the study loaded every frame as float32), with a failure giving the
same empty answer the study cached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from . import chroma, gapmodel


@dataclass(frozen=True)
class Summary:
    """One frame's contribution to its roll's context."""

    #: numpy dtype name of the pixels as they came from the scanner or file
    #: (``uint8``, ``uint16``); chroma compares base levels within one only.
    dtype: str | None
    #: the base level chroma found for this frame on its own, or None
    chroma_level: np.ndarray | None
    #: full-gap widths this frame measures (columns), for the roll's gap G
    gap_widths: tuple[float, ...]
    #: (log R/G, log B/G) of this frame's confirmed base runs
    gap_colours: tuple[tuple[float, float], ...]
    #: What failed while summarising, one line per member that raised --
    #: ``"chroma summary: ZeroDivisionError: ..."`` -- with its numbers left
    #: as the study's empty answer. Defaulted, and last, so a summary made
    #: positionally is the summary it was; the window's light reads it
    #: (`watch.Progress.abstained`). Never part of a note.
    errors: tuple[str, ...] = ()


def summarise(image: np.ndarray, dtype: str | None = None) -> Summary:
    """What ``image`` contributes to the context of the other frames of its walk."""
    img = np.asarray(image).astype(np.float32)
    if dtype is None:
        dtype = str(np.asarray(image).dtype)
    errors: list[str] = []
    try:
        best = chroma.frame_summary(img)["best"]
        level = None if best is None else best["level"]
    except Exception as exc:                                      # noqa: BLE001
        level = None
        errors.append(f"chroma summary: {type(exc).__name__}: {exc}")
    try:
        _, left, right = gapmodel._both_sides(img)
        widths = tuple(gapmodel._consistent_widths(left, right))
        colours = tuple(gapmodel._base_colours(left, right))
    except Exception as exc:                                      # noqa: BLE001
        widths, colours = (), ()
        errors.append(f"gapmodel summary: {type(exc).__name__}: {exc}")
    return Summary(dtype, level, widths, colours, tuple(errors))


def summary_record(s: Summary) -> dict[str, Any]:
    """``s`` in plain JSON types, every number as it was: `summary_from` undoes it.

    A walk that aims reads each frame against the summaries of the frames it
    has seen, and nothing else of them. Kept in the walk's record frame by
    frame, they are what that reading needs to be made again offline -- the
    other frames' prescans need not be at hand, nor be the pictures they
    were. The base level keeps its dtype (float64 from `summarise` today),
    so it comes back bit for bit rather than as whatever JSON's floats are.
    """
    level = None if s.chroma_level is None else np.asarray(s.chroma_level)
    return {
        "dtype": s.dtype,
        "chroma_level": None if level is None else {
            "dtype": str(level.dtype), "values": level.tolist()},
        "gap_widths": [float(w) for w in s.gap_widths],
        "gap_colours": [[float(a), float(b)] for a, b in s.gap_colours],
        # Only where a member failed, so every other record is as it was.
        **({"errors": list(s.errors)} if s.errors else {}),
    }


def summary_from(record: dict[str, Any]) -> Summary:
    """The `Summary` a `summary_record` was made from."""
    level = record.get("chroma_level")
    return Summary(
        record.get("dtype"),
        None if level is None else np.array(level["values"], dtype=level["dtype"]),
        tuple(float(w) for w in record.get("gap_widths") or ()),
        tuple((float(a), float(b)) for a, b in record.get("gap_colours") or ()),
        tuple(str(e) for e in record.get("errors") or ()),
    )
