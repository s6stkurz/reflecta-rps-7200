"""Where the picture ends on a negative prescan, and the move that centres it.

A copy of the frame-edge study's winning detector (`research/frame-edge`,
`ensemble_v2`): four numpy-only detectors that vote per side, scored against
hand labels on frames none of them was tuned on -- 97% of sides on Gold 200,
91% on unseen library-2 pictures, base claimed inside the picture nowhere.
`tests/test_frame_edges_parity.py` holds this copy to the study's answers.

It lives in `tools/` by Stefan's choice; `rps7200` never imports it. The
window and `tools/scan_roll.py` hand it to the driver (`walk_reader`) and ask
it for a sheet's positions (`propose_centred`); the window reads a walk in the
background as its prescans arrive (`EdgeWatch`), to the same answer.
"""

from __future__ import annotations

from .centre import FRAME_WIDTH_UNITS, columns_to_mm, decide, frame_columns
from .propose import (
    FILM_TYPES, READ_AT_DPI, WalkReader, centring, detect, film_type, propose_centred, summary,
    unread_at, walk_reader,
)
from .roll import Summary, summarise
from .sides import EdgeResult, Side
from .watch import DONE, FAILED, IDLE, READING, SKIPPED, EdgeWatch, Progress

__all__ = [
    "DONE", "FAILED", "FILM_TYPES", "FRAME_WIDTH_UNITS", "IDLE", "READ_AT_DPI", "READING",
    "SKIPPED",
    "EdgeResult", "EdgeWatch", "Progress", "Side", "Summary", "WalkReader",
    "centring", "columns_to_mm", "decide", "detect", "film_type", "frame_columns",
    "propose_centred", "summarise", "summary", "unread_at", "walk_reader",
]
