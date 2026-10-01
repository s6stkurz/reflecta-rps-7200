"""How the device makes a 300 dpi pass out of the film, as measured.

The 300 dpi and 3600 dpi passes of one frame (20260911T102059Z / 103000Z)
register at exactly 12x in both axes, and fitting the 300 dpi pass from the
3600 dpi one says:

- across the CCD, a column is a **point sample**: the best fit is one 3600 dpi
  column wide, not twelve, and the pass is aliased;
- down the transport, a row **integrates about 0.65 of a line** (0.5-0.8 fit).

So a high-resolution picture must not be area-averaged down to 300 dpi -- that
would be smoother than anything the device returns. Pictures are kept on a grid
`OVERSAMPLE` times finer than a prescan (`fpn.personal`), and the prescan is
sampled from it here: columns by linear interpolation at their centres, rows by
an exact box average over `ROW_APERTURE` of a line.

Coordinates are in prescan columns and rows. Grid cell k covers
[k / OVERSAMPLE, (k + 1) / OVERSAMPLE), so its centre is at (k + 0.5) / OVERSAMPLE.
"""

from __future__ import annotations

import numpy as np

OVERSAMPLE = 4
ROW_APERTURE = 0.65


def point_columns(grid: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Linear interpolation of ``grid`` (rows, k, ...) along axis 1 at ``positions``.

    ``positions`` are in prescan columns; outside the grid gives NaN, so a
    caller cannot mistake a missing column for picture.
    """
    u = np.asarray(positions, dtype=np.float64) * OVERSAMPLE - 0.5
    k0 = np.floor(u).astype(np.int64)
    frac = (u - k0).astype(np.float32)
    n = grid.shape[1]
    inside = (k0 >= 0) & (k0 + 1 < n)
    k0c = np.clip(k0, 0, n - 2)
    shape = (1, -1) + (1,) * (grid.ndim - 2)
    f = frac.reshape(shape)
    out = grid[:, k0c] * (1 - f) + grid[:, k0c + 1] * f
    out[:, ~inside] = np.nan
    return out


def box_rows(grid: np.ndarray, centres: np.ndarray, aperture: float = ROW_APERTURE) -> np.ndarray:
    """Exact box average of ``grid`` (k, ...) along axis 0 over ``aperture`` lines.

    ``centres`` are in prescan rows. Rows whose box leaves the grid are NaN.
    """
    n = grid.shape[0]
    cum = np.concatenate([np.zeros((1,) + grid.shape[1:], dtype=np.float64),
                          np.cumsum(grid, axis=0, dtype=np.float64)], axis=0)

    def integral(at: np.ndarray) -> np.ndarray:
        # The integral of the grid from its start to `at` (in grid cells),
        # exact for a piecewise-constant grid.
        a = np.clip(at, 0, n)
        k = np.minimum(np.floor(a).astype(np.int64), n - 1)
        part = (a - k).reshape((-1,) + (1,) * (grid.ndim - 1))
        return cum[k] + (cum[k + 1] - cum[k]) * part

    centres = np.asarray(centres, dtype=np.float64)
    lo = (centres - aperture / 2) * OVERSAMPLE
    hi = (centres + aperture / 2) * OVERSAMPLE
    out = (integral(hi) - integral(lo)) / (hi - lo).reshape((-1,) + (1,) * (grid.ndim - 1))
    bad = (lo < 0) | (hi > n)
    out[bad] = np.nan
    return out.astype(np.float32)
