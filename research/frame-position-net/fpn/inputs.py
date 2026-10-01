"""From a delivered prescan to the network's six input channels.

What the edge reader is handed at runtime is the prescan `scan()` returns:
corrected, upright, uint8, (286 or 287, 428, 3) in RGB order, at the device's
own exposure (`DirectScanner.prescan`). The study's frames also hold uint16
passes. Both come through here unchanged; nothing is inverted.

    channels 0-2  log2(counts + 0.5) minus that channel's median over the frame:
                  the exposure drops out, the colour ratios stay
    channels 3-5  constant 1 or 0: is_c41, is_bw, is_positive
"""

from __future__ import annotations

import numpy as np

from rps7200.framing import PRESCAN_COLUMNS

ROWS = 287
#: framing keeps it as a float (428.0), for its unit arithmetic.
COLUMNS = int(PRESCAN_COLUMNS)
#: Film names as the study writes them (`ctx["film_type"]`), and the driver's.
FILMS = ("c41", "bw", "positive")
DRIVER_FILMS = {"negative": "c41", "bw": "bw", "positive": "positive"}


def film_flags(film: str) -> tuple[float, float, float]:
    film = DRIVER_FILMS.get(film, film)
    if film not in FILMS:
        raise ValueError(f"film {film!r} is not read; one of {FILMS + tuple(DRIVER_FILMS)}")
    c41, bw, positive = (1.0 if film == f else 0.0 for f in FILMS)
    return c41, bw, positive


def prepare(image: np.ndarray, *, dtype: str, film: str) -> np.ndarray:
    """(6, 287, 428) float32 from one prescan. ``dtype`` is the delivered one."""
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError(f"expected (rows, columns, >=3), got {image.shape}")
    rows, columns = image.shape[:2]
    if columns != COLUMNS:
        raise ValueError(f"{columns} columns: only 300 dpi prescans ({COLUMNS}) are read")
    if rows not in (ROWS - 1, ROWS):
        raise ValueError(f"{rows} rows: a 300 dpi prescan has {ROWS - 1} or {ROWS}")
    counts = image[..., :3].astype(np.float32)
    if dtype == "uint16":
        counts /= 257.0
    elif dtype != "uint8":
        raise ValueError(f"dtype {dtype!r}: uint8 or uint16")
    if rows == ROWS - 1:
        counts = np.concatenate([counts, counts[-1:]], axis=0)
    level = np.log2(counts + 0.5)
    level -= np.median(level.reshape(-1, 3), axis=0)
    out = np.empty((6, ROWS, COLUMNS), dtype=np.float32)
    out[:3] = level.transpose(2, 0, 1)
    for k, flag in enumerate(film_flags(film)):
        out[3 + k] = flag
    return out
