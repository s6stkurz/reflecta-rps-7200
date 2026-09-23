"""What a frame-edge detector answers, per side of one prescan.

Copied from `research/frame-edge/common.py` (the study's shared ground, commit
32a98e5) without its dataset, display and scoring parts -- the detectors in
this package are the study's, and they speak exactly these types.

Coordinates: a **position is a boundary between columns**, a float. The left
answer is where the picture *starts* (base occupies ``[0, x)``); the right
answer is where it *ends* (base occupies ``[x, W)``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

#: Rows at the top and bottom that can be base across the whole width: the
#: prescan is 24.3 mm tall against a 24 mm frame.
TRIM_ROWS = 5

#: What a side can be.
EDGE = "edge"                        # base shows, and x is where it meets picture
PICTURE_TO_BORDER = "picture_to_border"   # no base: the picture runs off the aperture
ALL_BASE = "all_base"                # the whole frame is base (blank frame, strip end)
NO_FILM = "no_film"                  # the empty gate: brighter than base, no film at all
REFUSE = "refuse"                    # the detector will not say
UNREADABLE = "unreadable"            # a labeller could not tell
STATES = (EDGE, PICTURE_TO_BORDER, ALL_BASE, NO_FILM, REFUSE, UNREADABLE)


def trimmed(image: np.ndarray, rows: int = TRIM_ROWS) -> np.ndarray:
    """The rows that can hold picture: ``TRIM_ROWS`` off the top and bottom."""
    return image[rows:image.shape[0] - rows]


@dataclass
class Side:
    """One side of one frame.

    ``x`` is set only for ``EDGE``. ``lo``/``hi`` bracket it -- the detector's
    own honest interval, not a decoration. ``conf`` is 0..1 and means whatever
    the detector can defend. ``outer`` is the far side of the gap, where the
    *neighbouring* frame's picture begins, if that shows.
    """

    state: str = REFUSE
    x: float | None = None
    lo: float | None = None
    hi: float | None = None
    conf: float = 0.0
    outer: float | None = None
    x_top: float | None = None
    x_bottom: float | None = None
    note: str = ""

    def base_width(self, side: str, width: int) -> float | None:
        """Columns of base showing on this side, or None when not an edge."""
        if self.state != EDGE or self.x is None:
            return None
        return self.x if side == "left" else width - self.x


@dataclass
class EdgeResult:
    left: Side = field(default_factory=Side)
    right: Side = field(default_factory=Side)
    debug: dict[str, Any] = field(default_factory=dict)

    def side(self, name: str) -> Side:
        return self.left if name == "left" else self.right

    def to_json(self) -> dict[str, Any]:
        return {"left": asdict(self.left), "right": asdict(self.right),
                "debug": _jsonable(self.debug)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EdgeResult:
        return cls(Side(**data["left"]), Side(**data["right"]), data.get("debug") or {})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
