"""Where the film and the picture sit in the transport window.

The aperture is 36.5 mm and a 35 mm frame is 36 mm, so there is half a
millimetre of slack. A frame that has drifted is a frame with its edge outside
the aperture, and no scan window can get that back -- which is why a drifted
frame shows up as a picture *narrower* than a whole one rather than as a picture
in the wrong place. The prescan cannot see what the window does not cover.

Everything here measures. Nothing here moves the film.
"""
from __future__ import annotations

import numpy as np

from .protocol import COORD_PER_INCH, MM_PER_INCH

MIN_INSET_X = 96
MIN_INSET_Y = 71

#: Frame the vendor scans for shading calibration -- the lower part of the
#: transport, not the picture area.
CALIBRATION_FRAME = (0, 3431, 10343, 6888)

#: Full scan extent, 0-based pixels at maximum resolution. Matches the vendor
#: software's frame and this scanner's 10344 x 6888 CCD. Used so that scanning
#: needs no INQUIRY: neither CyberView nor any run that scanned successfully
#: issues one, and doing so from inside the scan flow has broken reads.
FULL_FRAME = (0, 0, 10343, 6887)

#: CCD mask length the vendor software requests (pieusb uses shading_width).
CCD_MASK_SIZE = 5172

#: A whole 35 mm frame, in scanner units at maximum resolution. From the vendor's
#: own detected windows on a strip it had registered correctly -- (96,71) to
#: (10175,6815), so 10079 wide -- against a 10344-unit transport window. A
#: picture measuring much less than this has part of itself outside the
#: aperture, which is the only way a drifted frame is visible: the prescan
#: cannot see what the window does not cover.
NOMINAL_FRAME_WIDTH = 10080

#: Below this, a prescan is clear film rather than a picture, and a roll walking
#: frame by frame has run off the end of the film. Measured as variation *down*
#: the columns, so the lamp's horizontal falloff -- about 22% centre to edge, and
#: present in a blank window too -- does not read as a picture.
BLANK_CONTRAST = 0.02


def frame_contrast(image: np.ndarray) -> float:
    """How much a prescan varies down its columns, relative to its own level.

    The discriminator for "is there a picture in the window at all", which is
    what tells a roll it has reached the end of the film.

    Down the columns, specifically. Vignetting and lamp falloff vary *across*
    the sensor and are constant down it, so a window of blank film still varies
    ~22% column to column while varying almost nothing row to row. Measuring
    across the width would score empty film as a picture.

    Relative to the mean, so it does not move with exposure.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return 0.0
    level = float(grey.mean())
    if level <= 0:
        return 0.0
    return float(np.median(grey.std(axis=0)) / level)


#: Fraction of the clear-aperture level below which a column is film. Measured
#: on a C-41 negative: the clear strip read 143/153/153 in R/G/B and the film
#: 34/15/7, so anything from 0.6 to 0.9 returns the same edges.
FILM_LEVEL = 0.75

#: How much brighter the clear aperture has to be than the median column before
#: there is believed to be one in view at all. Below this the film fills the
#: window, which is the normal case for a well-registered frame.
CLEAR_RATIO = 2.0


def film_bounds(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
    level: float = FILM_LEVEL,
    clear_ratio: float = CLEAR_RATIO,
) -> tuple[int, int, int, int]:
    """Where the film sits in the transport window, by how much light it stops.

    Film attenuates and an empty aperture does not, so the film's edge is a step
    in *level*. :meth:`DirectScanner.detect_frame` looks for a step in variance
    instead, and on a real negative that fails badly: a dark, low-contrast frame
    varies less than the hard border at the film's edge, so a threshold set
    relative to the peak selects the border and discards the picture. Measured
    on this scanner it reduced a perfectly registered frame -- picture filling
    the window edge to edge -- to a 0.26 mm sliver, and reported it as 35 mm of
    drift.

    Level does not have that failure mode, because it does not depend on picture
    content at all. On one 300 dpi prescan of a C-41 negative the clear strip
    read 143/153/153 in R/G/B against the film's 34/15/7, and every threshold
    between 60% and 90% of the clear level returned the same edges.

    With no clear aperture in view -- ``clear_ratio`` -- the film fills the
    window, which is what a well-registered frame looks like, and the whole
    window is returned. An *empty* window reads the same way; use
    :func:`frame_contrast` to tell those apart, as :meth:`DirectScanner.scan_roll`
    does before it calls this.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return full_frame

    fx0, fy0, fx1, fy1 = full_frame

    def span(profile: np.ndarray, lo: int, hi: int, n: int) -> tuple[int, int]:
        clear = float(np.percentile(profile, 98))
        median = float(np.median(profile))
        if median <= 0 or clear < median * clear_ratio:
            return lo, hi                       # no empty aperture in view
        covered = np.flatnonzero(profile < clear * level)
        if covered.size == 0:
            return lo, hi
        return (
            lo + int(round(covered[0] / n * (hi - lo))),
            lo + int(round(covered[-1] / n * (hi - lo))),
        )

    x0, x1 = span(grey.mean(axis=0), fx0, fx1, grey.shape[1])
    y0, y1 = span(grey.mean(axis=1), fy0, fy1, grey.shape[0])
    if x1 <= x0 or y1 <= y0:
        return full_frame
    return x0, y0, x1, y1


def registration(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
) -> dict[str, float | int]:
    """Where the picture sits in the transport window, from a prescan.

    The window is 36.5 mm and a 35 mm frame is 36 mm, so there is half a
    millimetre of slack: a frame that has drifted is a frame with its edge
    outside the aperture, and no scan window can get that back. The vendor's own
    5-frame strip shows it happening -- its detected windows started at x=96 for
    four frames and then at x=1727 for the fifth, which lost 6 mm of picture.

    ``offset`` is signed, in scanner units at maximum resolution: positive means
    the picture sits right of centre, i.e. the film is under-advanced.

    ``shortfall`` is the one that matters, and it is why the width is measured at
    all. A drifted frame cannot be seen directly -- the prescan only covers the
    aperture, so a picture hanging outside it is simply not there to be found.
    What shows instead is a picture *narrower* than a whole frame. The vendor's
    fifth strip frame measured 8472 units against 10079 for the four before it:
    1.6 k units, 5.7 mm, of picture that never reached the sensor.

    Measurement only. Nothing here moves the film.
    """
    x0, _, x1, _ = film_bounds(image, full_frame)
    fx0, _, fx1, _ = full_frame
    width = x1 - x0
    offset = (x0 + x1) / 2.0 - (fx0 + fx1) / 2.0
    shortfall = max(0, NOMINAL_FRAME_WIDTH - width)

    def mm(units: float) -> float:
        return round(units * MM_PER_INCH / COORD_PER_INCH, 2)

    return {
        "x0": int(x0),
        "x1": int(x1),
        "width": int(width),
        "offset": int(round(offset)),
        "offset_mm": mm(offset),
        "shortfall": int(shortfall),
        "shortfall_mm": mm(shortfall),
        "margin": int(min(x0 - fx0, fx1 - x1)),
        "margin_mm": mm(min(x0 - fx0, fx1 - x1)),
    }
