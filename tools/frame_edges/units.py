"""The transport's units, as the frame-edge detectors use them -- from `rps7200.framing`.

Nothing is retyped here: the constants live in `rps7200/framing.py` beside the
command law they describe, and this module only derives the one quantity the
gap model needs, the pitch in columns of a pass this wide.
"""

from __future__ import annotations

from rps7200.framing import (
    APERTURE_MM, COLUMNS_PER_UNIT, FRAME_WIDTH_UNITS, PITCH_UNITS, PRESCAN_COLUMNS,
    SMALLEST_MOVE, units_per_column,
)

__all__ = ["APERTURE_MM", "COLUMNS_PER_UNIT", "FRAME_WIDTH_UNITS", "PITCH_UNITS",
           "PRESCAN_COLUMNS", "SMALLEST_MOVE", "pitch_columns", "units_per_column"]


def pitch_columns(width: float = PRESCAN_COLUMNS) -> float:
    """Gap to gap, in columns of a pass ``width`` columns wide (455.3 at 428)."""
    return PITCH_UNITS / units_per_column(int(width))
