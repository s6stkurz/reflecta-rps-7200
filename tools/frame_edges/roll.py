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


def summarise(image: np.ndarray, dtype: str | None = None) -> Summary:
    """What ``image`` contributes to the context of the other frames of its walk."""
    img = np.asarray(image).astype(np.float32)
    if dtype is None:
        dtype = str(np.asarray(image).dtype)
    try:
        best = chroma.frame_summary(img)["best"]
        level = None if best is None else best["level"]
    except Exception:                                             # noqa: BLE001
        level = None
    try:
        _, left, right = gapmodel._both_sides(img)
        widths = tuple(gapmodel._consistent_widths(left, right))
        colours = tuple(gapmodel._base_colours(left, right))
    except Exception:                                             # noqa: BLE001
        widths, colours = (), ()
    return Summary(dtype, level, widths, colours)
