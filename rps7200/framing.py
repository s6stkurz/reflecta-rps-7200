"""Where the film and the picture sit in the transport window.

The aperture is 36.5 mm and a 35 mm frame is 36 mm, so there is half a
millimetre of slack. A frame that has drifted is a frame with its edge outside
the aperture, and no scan window can get that back -- which is why a drifted
frame shows up as a picture *narrower* than a whole one rather than as a picture
in the wrong place. The prescan cannot see what the window does not cover.

Everything here measures. Nothing here moves the film.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .protocol import COORD_PER_INCH, MM_PER_INCH, say_units

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


#: How far inside the film's own edge metering looks, taken off **each** edge of
#: **each** axis. 0.05 therefore keeps 90% of the width and 90% of the height,
#: which is **81% of the pass** -- worth stating, because "5%" and "90%" and
#: "81%" are all true of it and only the last is the area.
#:
#: The edge is a transition rather than a step -- a few columns of partial
#: coverage -- and immediately beside a 35 mm frame is clear base and the
#: sprocket margin, which are brighter than any part of the picture. So the
#: inset is for clearing the edge, not for centre-weighting: it is applied to
#: whatever :func:`film_bounds` returned, which is the whole window when no
#: aperture is in view.
METERING_INSET = 0.05


def metering_region(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
    inset: float = METERING_INSET,
) -> np.ndarray:
    """The part of a prescan worth metering: inside the film, inside its edge.

    Metering takes a high percentile, so anything brighter than the picture
    decides the exposure. The empty aperture is *much* brighter than film --
    measured on a C-41 negative, 143/153/153 against the film's 34/15/7 -- so a
    strip that does not fill the window pulls the percentile up and the scan
    comes out under-exposed, silently, by however much aperture happens to be in
    view.

    Measured on three prescans where an aperture was visible: the 99.5th
    percentile read 5.9-11.1% high across all three channels, so the scan
    exposed 5.6-10.0% short. It is not a colour shift -- every channel moves
    together -- and it is not constant, because it depends on where the frame
    sits, which is exactly the inconsistency `meter=once` exists to avoid.

    **Take it from the first probe, at the device's base exposure, and reuse
    it.** :func:`film_bounds` needs the aperture to be at least
    :data:`CLEAR_RATIO` brighter than the median, and metering's whole job is to
    brighten the film until it is nearly as bright as the aperture -- so by the
    round that settles the exposure, the contrast this depends on is gone and
    the bounds come back as the whole window. Detecting once, while the film is
    still dark, is not an optimisation; it is the only time it works.

    Returns a **view**, and returns the whole image rather than something tiny
    if the bounds come back degenerate. Failing back to today's behaviour is
    the right failure: it is the thing this improves on, not something worse.
    """
    return image[metering_slice(image, full_frame, inset)]


def metering_slice(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
    inset: float = METERING_INSET,
) -> tuple[slice, slice]:
    """The rows and columns :func:`metering_region` would keep.

    Separate so a caller can measure the region once, on a dark pass, and apply
    it to later ones -- see the note there about why that is necessary rather
    than merely cheaper.
    """
    whole = (slice(None), slice(None))
    if image.ndim < 2 or image.size == 0:
        return whole
    x0, y0, x1, y1 = film_bounds(image, full_frame)
    fx0, fy0, fx1, fy1 = full_frame
    span_x, span_y = fx1 - fx0, fy1 - fy0
    if span_x <= 0 or span_y <= 0:
        return whole

    h, w = image.shape[:2]
    cx0 = int(round(w * (x0 - fx0) / span_x))
    cx1 = int(round(w * (x1 - fx0) / span_x))
    cy0 = int(round(h * (y0 - fy0) / span_y))
    cy1 = int(round(h * (y1 - fy0) / span_y))

    # Then step inside the edge itself, which is a transition and not a step.
    pad_x = int(round((cx1 - cx0) * inset))
    pad_y = int(round((cy1 - cy0) * inset))
    cx0, cx1 = cx0 + pad_x, cx1 - pad_x
    cy0, cy1 = cy0 + pad_y, cy1 - pad_y

    cx0, cy0 = max(0, cx0), max(0, cy0)
    cx1, cy1 = min(w, cx1), min(h, cy1)
    if cx1 - cx0 < 2 or cy1 - cy0 < 2:
        return whole
    return slice(cy0, cy1), slice(cx0, cx1)


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


# --- registration from the inter-frame gap -------------------------------

#: A column counts as gap when it is this many MADs above the median level.
GAP_LEVEL_SIGMA = 2.0
#: ...and varies less than this fraction of the picture's own variation.
GAP_FLATNESS = 0.35
#: Shorter runs than this at an edge are noise, not a gap.
GAP_MIN_RUN = 3

#: All the room the film has. The aperture is 10344 units = 36.49 mm and a 35 mm
#: frame is ~36 mm, so registration cannot be off by more than about half a
#: millimetre. A larger reading is the detector failing, not the film moving --
#: which is the single assumption that made this measurable at all, after four
#: successive detectors each reported confident nonsense.
MAX_REGISTRATION_MM = 0.49


def gap_edges(image: np.ndarray) -> tuple[int, int]:
    """Columns of inter-frame gap visible at each edge, as ``(left, right)``.

    The gap is unexposed film base, so it is **both brighter than the picture and
    flatter down the column**. Neither test alone works, and that is not a
    guess -- four detectors were built for this and every one keyed on a single
    property and was confidently wrong on some frames. Brightness alone fires on
    a sunlit sky; flatness alone fires on any smooth dark area.

    Zero is the normal answer. The picture overfills the aperture, so a
    well-registered frame shows no gap at all, and only once the film creeps does
    a sliver appear at one edge. Zero therefore means "nothing to correct", not
    "could not measure".
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return 0, 0

    level = grey.mean(axis=0)
    spread = grey.std(axis=0)
    med = float(np.median(level))
    mad = 1.4826 * float(np.median(np.abs(level - med))) or 1e-9
    typical = float(np.median(spread)) or 1e-9

    gap = (level > med + GAP_LEVEL_SIGMA * mad) & (spread < GAP_FLATNESS * typical)

    def run(flags: np.ndarray) -> int:
        n = 0
        while n < len(flags) and flags[n]:
            n += 1
        return n if n >= GAP_MIN_RUN else 0

    return run(gap), run(gap[::-1])


def registration_error_mm(
    image: np.ndarray, full_frame: tuple[int, int, int, int] = FULL_FRAME
) -> tuple[float | None, str]:
    """How far the frame sits from centred, in mm, or ``None`` with a reason.

    Positive means the frame is too far towards +x, so a gap has appeared on the
    left. Returns ``None`` when the reading cannot be trusted, which is more
    often than not and is the point: acting on a bad measurement drives the
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
    mm = px * (full_frame[2] - full_frame[0] + 1) / width * MM_PER_INCH / COORD_PER_INCH
    if abs(mm) > MAX_REGISTRATION_MM:
        return None, (
            f"{abs(mm):.2f} mm exceeds the {MAX_REGISTRATION_MM} mm the aperture "
            "allows -- detector error, not film movement"
        )
    return mm, f"{px:+d} px of gap"


#: How tall the transport window is, in millimetres. 6888 units at 7200 dpi.
#: Needed because the off-axis gate is in mm rather than pixels, so that it
#: means the same thing at every prescan resolution.
APERTURE_HEIGHT_MM = ((FULL_FRAME[3] - FULL_FRAME[1] + 1)
                      * MM_PER_INCH / COORD_PER_INCH)

#: The whole transport window, in millimetres. 10344 units at 7200 dpi.
APERTURE_MM = (FULL_FRAME[2] - FULL_FRAME[0] + 1) * MM_PER_INCH / COORD_PER_INCH


# --- where the frame actually sits, from the gap's own brightness -----------
#
# The detectors above key on something relative to the frame's own content --
# `film_bounds` on an empty aperture being twice the median, `gap_edges` on a
# run being brighter than median + 2*MAD. Both fail on real film, and they
# fail in the same direction: the worse a frame is placed, the more gap is in
# view, the higher the frame's own median climbs, and the less the detector
# sees. Measured on a ladder with known offsets, `gap_edges` read 5, 8, then
# 0, 0, 0, 0, 0 while the gap widened from 7 to 26 px.
#
# These key on an ABSOLUTE level instead. Unexposed film base is one physical
# object under one lamp at one exposure: measured across sixteen frames of one
# strip its level held to 3.4%, and it does not care which photograph sits
# beside it. That is the property the four detectors in the graveyard lacked.
#
# Two further differences, both learned the same way:
#
#   * the run is searched anywhere in the window, not only flush with column 0.
#     A gap is not flush whenever a sliver of the neighbouring frame is in view
#     beside it, which is exactly the situation worth detecting -- and the
#     anchored rule answers "no gap, registered" for it.
#   * everything is in millimetres. `GAP_MIN_RUN` above is in pixels, so its
#     deadband is 0.25 mm at 300 dpi and 0.13 mm at 600 without anyone saying
#     so. `SEARCH_MM` and `HOLD_TOLERANCE_MM` are in mm for this reason.

#: How wide a band must be before it is a gap rather than noise, in mm.
GAP_MIN_MM = 0.25

#: How far a column's level may sit from the strip's base level and still be
#: base, as a fraction. The measured spread across one strip was 3.4%; this is
#: loose enough for a second strip and tight enough to exclude picture.
BASE_TOLERANCE = 0.15

#: A base column varies little down the frame. Measured in counts rather than
#: relative to the frame's own spread -- the whole point is not to ask the
#: photograph anything.
BASE_FLATNESS = 3.0

#: Refuse a strip whose base level is not consistent. Measured 3.4% over
#: sixteen frames; past this the level is not describing one object.
BASE_SPREAD_LIMIT = 0.10

#: Fewest *bands* that can calibrate a strip. Two would give a level with no
#: way to see that one of them was wrong.
#:
#: Bands, not frames, and the distinction matters to anyone arming this from a
#: short walk: `film_base` harvests one band at each edge of each image, so two
#: frames can satisfy this on their own.
MIN_BASE_BANDS = 3


@dataclass(frozen=True)
class FilmBase:
    """What unexposed base looks like on this strip, at this exposure.

    Measured from the strip rather than declared, which is what makes the
    detector resolution-independent and exposure-independent at once: a
    constant that was right on one roll is wrong on the next, and the level
    a 600 dpi prescan reads is not the level a 300 dpi one reads.
    """

    level: float                     # counts
    flatness: float                  # counts of column spread it showed
    bands: int                       # how many contributed -- bands, not frames
    spread: float                    # fractional spread of the level


def _grey(image: np.ndarray) -> np.ndarray:
    grey = image.astype(np.float64)
    return grey.mean(axis=2) if grey.ndim == 3 else grey


def base_runs(
    image: np.ndarray,
    level: float,
    *,
    tolerance: float = BASE_TOLERANCE,
    flatness: float = BASE_FLATNESS,
    min_run: int = 1,
) -> list[tuple[int, int]]:
    """Every run of unexposed base, as ``(start, length)`` in columns.

    Anywhere in the window, not only at an edge. A run in the middle is not a
    gap -- the film cannot show base between two halves of one photograph --
    but it is worth returning so a caller can refuse rather than quietly use
    the wrong one.
    """
    grey = _grey(image)
    if grey.size == 0:
        return []
    column, spread = grey.mean(axis=0), grey.std(axis=0)
    is_base = (np.abs(column - level) <= level * tolerance) & (spread < flatness)

    runs, start = [], None
    for i, on in enumerate(is_base):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_run:
                runs.append((start, i - start))
            start = None
    if start is not None and len(is_base) - start >= min_run:
        runs.append((start, len(is_base) - start))
    return runs


#: Fewest *distinct frames* that must contribute before a level is believed.
#:
#: Separate from `MIN_BASE_BANDS`, and the two are not the same guard. One
#: image yields a band at each edge, so a single frame casts two votes and
#: seven prescans of one frame cast fourteen -- all of them the same evidence
#: about the same columns. That is how one run calibrated a "base" of 176.9
#: counts which was the picture: not a weak median, a median of one thing
#: counted many times.
MIN_BASE_SOURCES = 2


def edge_bands(image: np.ndarray, *, aperture_mm: float = APERTURE_MM
               ) -> list[tuple[float, float]]:
    """The flattest run at each edge of one frame, as ``(level, flatness)``.

    Found by flatness alone, deliberately: this is what measures the level, so
    it must not assume one to find it.

    A run wider than `MAX_GAP_MM` is dropped here rather than survived later.
    `picture_start` has always refused such a band -- about two millimetres of
    gap exists on 135 film and a longer run is the end of the strip or picture
    at the base level -- but the calibration did not, which is why a 104-column
    band of sky could reach the median at all and why the spread had to be made
    robust to it after the fact.
    """
    grey = _grey(image)
    if grey.size == 0:
        return []
    widest = MAX_GAP_MM / (aperture_mm / grey.shape[1])
    column, spread = grey.mean(axis=0), grey.std(axis=0)
    found = []
    for scan in (slice(None), slice(None, None, -1)):
        run, values = 0, column[scan]
        for value in spread[scan]:
            if value >= BASE_FLATNESS:
                break
            run += 1
        if run and run <= widest:
            found.append((float(np.median(values[:run])),
                          float(np.median(spread[scan][:run]))))
    return found


def film_base_from(bands_by_frame: dict) -> tuple[FilmBase | None, dict]:
    """Calibrate the strip's base level from bands already gathered.

    Keyed by frame number rather than accumulated in a list, which is what
    makes the contamination failure structurally impossible: a second pass over
    frame 5 replaces frame 5's bands, it does not cast two more votes for the
    same columns.
    """
    levels = [lv for bands in bands_by_frame.values() for lv, _f in bands]
    flats = [fl for bands in bands_by_frame.values() for _l, fl in bands]
    sources = sum(1 for bands in bands_by_frame.values() if bands)

    if sources < MIN_BASE_SOURCES:
        return None, {"reason": f"bands from only {sources} frame(s), "
                                f"need {MIN_BASE_SOURCES}",
                      "bands": len(levels), "frames": sources}
    if len(levels) < MIN_BASE_BANDS:
        return None, {"reason": f"only {len(levels)} band(s) found, "
                                f"need {MIN_BASE_BANDS}", "bands": len(levels),
                      "frames": sources}
    level = float(np.median(levels))
    # Robust, not max-minus-min. Finding a band by flatness alone also finds
    # smooth *picture* on some frames -- one strip had a 104-column flat run
    # that was sky -- and a single such candidate makes the range meaningless
    # while leaving the median untouched. What matters is whether most of them
    # agree, which is what a MAD measures and a range does not.
    deviation = np.abs(np.array(levels) - level)
    spread = float(1.4826 * np.median(deviation) / level) if level else 1.0
    agreeing = int(np.sum(deviation <= level * BASE_TOLERANCE))
    detail = {"level": round(level, 2), "spread": round(spread, 4),
              "bands": len(levels), "agreeing": agreeing, "frames": sources}
    if agreeing < MIN_BASE_BANDS:
        return None, dict(detail, reason=(
            f"only {agreeing} band(s) agree on a level, need "
            f"{MIN_BASE_BANDS}"))
    if spread > BASE_SPREAD_LIMIT:
        return None, dict(detail, reason=(
            f"base level varies by {spread*100:.1f}%, past the "
            f"{BASE_SPREAD_LIMIT*100:.0f}% one lamp at one exposure explains"))
    return FilmBase(level=level, flatness=float(np.median(flats)),
                    bands=len(levels), spread=spread), detail


def film_base(images) -> tuple[FilmBase | None, dict]:
    """Calibrate the strip's own base level, from its own frames.

    The whole-strip form, kept for the offline paths that have every frame in
    hand at once. A walk uses `edge_bands` and `film_base_from` directly, so
    that a frame it re-prescans replaces its own bands rather than adding more.
    """
    return film_base_from({i: edge_bands(im) for i, im in enumerate(images)})


#: How wide the exposed image on 135 film is, across the film. Implicit in
#: `TARGET_GAP_MM` until the right-hand reading needed it by name: converting
#: "the picture ends here" into "the picture begins there" goes through it,
#: which is why a reading off the right edge carries an assumption the left one
#: does not.
#:
#: Assumed rather than measured, and worth knowing that: this file's own note
#: on `TARGET_GAP_MM` puts the strip at 35.90 and reasons about 36.2.
FRAME_WIDTH_MM = 36.0

#: The widest an inter-frame gap can be. On 135 the pitch is ~38 mm against a
#: ~36 mm image, so about 2 mm of it exists and the aperture can show at most
#: that. A longer run of base is not a gap -- it is the end of the strip, or a
#: smooth part of a photograph that happens to sit at the base level.
MAX_GAP_MM = 2.6

#: How far from an edge a run may begin and still be the gap that entered
#: there, as a fraction of the window. A gap is not flush whenever a sliver of
#: the neighbouring frame is in view beside it.
EDGE_FRACTION = 0.12


def picture_start(
    image: np.ndarray, base: FilmBase, *, aperture_mm: float = APERTURE_MM
) -> tuple[int | None, dict]:
    """The column where this frame's own picture begins, or None.

    The registration-relevant quantity is where the gap *ends*, not how wide
    it is: that is the first column of this photograph, and holding it in the
    same place from frame to frame is what registered means.

    Refuses rather than guesses. A run wider than a gap can be, a run that is
    not near an edge, and no run at all are three different reasons and each
    is returned as one.
    """
    grey = _grey(image)
    if grey.size == 0:
        return None, {"reason": "nothing to measure"}
    width = grey.shape[1]
    mm_px = aperture_mm / width
    smallest = max(1, int(round(GAP_MIN_MM / mm_px)))
    widest = int(round(MAX_GAP_MM / mm_px))
    margin = int(round(width * EDGE_FRACTION))

    runs = base_runs(image, base.level, flatness=max(base.flatness * 2.0,
                                                     BASE_FLATNESS),
                     min_run=smallest)
    detail: dict[str, Any] = {"runs": runs, "mm_per_px": round(mm_px, 5)}
    if not runs:
        return None, dict(detail, reason="no unexposed base in view")

    # The gap that entered from the left: the first run beginning within a
    # margin of that edge. Not `start == 0` -- that is the rule `gap_edges`
    # uses, and it is why a gap with a sliver of the neighbouring frame beside
    # it reads as no gap at all.
    near = [(start, length) for start, length in runs if start <= margin]
    if not near:
        return None, dict(detail, reason=(
            f"the only base in view begins at column {runs[0][0]}, past the "
            f"{margin}-column margin -- that is picture, not a gap"))
    start, length = near[0]
    if start:
        # Unexposed base creeps in from an edge; it cannot float in the middle
        # of a frame. A band that sits back from the edge is only a gap if
        # what stands between it and the edge is **picture** -- the tail of the
        # neighbouring frame, which is what this margin exists to allow.
        #
        # Measured on walk A, where three real slivers read 8 to 22 counts
        # against a base of 36.6: far outside anything base can be. And on
        # frame 9 of walk K, where the columns in front read 35.6 -- base
        # level, failing the flatness test by a hair and nothing more. There
        # the whole left of the frame was smooth picture sitting at the base
        # level and the run was split at an arbitrary column, which put the
        # frame 2.44 mm away from what every one of its neighbours said and
        # cost the frame after it as well.
        ahead = float(_grey(image)[:, :start].mean())
        if abs(ahead - base.level) <= base.level * BASE_TOLERANCE:
            return None, dict(detail, reason=(
                f"the band at column {start} is not reachable from the edge: "
                f"the {start} column(s) in front of it read {ahead:.1f}, which "
                f"is base rather than picture, so this is one smooth region "
                f"split in two rather than a gap"))
    if length > widest:
        return None, dict(detail, reason=(
            f"a {length}-column band is {length*mm_px:.2f} mm, wider than the "
            f"{MAX_GAP_MM} mm a gap can be -- the end of the strip, or "
            f"picture at the base level"))
    return start + length, dict(detail, gap=(start, length),
                                gap_mm=round(length * mm_px, 3))


def picture_end(
    image: np.ndarray, base: FilmBase, *, aperture_mm: float = APERTURE_MM
) -> tuple[int | None, dict]:
    """The column after this frame's picture ends, or None.

    The mirror of :func:`picture_start`, and implemented as one: the columns
    are reversed and the same rule applied. Two separate rules would drift
    apart the first time either was tuned.
    """
    start, detail = picture_start(image[:, ::-1], base, aperture_mm=aperture_mm)
    detail = dict(detail, mirrored=True)
    if start is None:
        return None, detail
    width = image.shape[1]
    gap = detail.get("gap")
    if isinstance(gap, tuple):
        # Back into this frame's own columns, so a caller drawing the band
        # draws it where it is rather than where its mirror was.
        gs, gl = gap
        detail["gap"] = (width - gs - gl, gl)
    return width - start, detail


def picture_span(
    image: np.ndarray, base: FilmBase, *, aperture_mm: float = APERTURE_MM,
    frame_mm: float = FRAME_WIDTH_MM,
) -> tuple[int | None, dict]:
    """Where this frame's picture begins, from whichever edge shows base.

    The left edge answers directly. The right edge answers too, and has to be
    allowed to: **a frame that has gone too far the other way shows no base at
    the left at all**, so a detector that only looks there can say "too far
    along" and never "not far enough". Measured on film 2026-09-21 -- the film
    moved about 2 mm the other way, a 1.36 mm band sat plainly at the right,
    and every member abstained on a frame that needed +1.12 mm.

    Preferred left, because that reading is direct. The right one is converted
    through the frame width, which is an assumption the left reading does not
    need, so `detail["edge"]` says which answered and a caller that cares can
    weigh them differently.
    """
    start, detail = picture_start(image, base, aperture_mm=aperture_mm)
    if start is not None:
        return start, dict(detail, edge="left")

    end, right = picture_end(image, base, aperture_mm=aperture_mm)
    if end is None:
        return None, dict(detail, edge=None,
                          reason=f"left: {detail.get('reason', '')}; "
                                 f"right: {right.get('reason', '')}")
    mm_px = aperture_mm / image.shape[1]
    return int(round(end - frame_mm / mm_px)), dict(
        right, edge="right", picture_end=end,
        reason=right.get("reason", "") +
        f" (read from the right edge, through a {frame_mm} mm frame)")


#: Where a frame's picture should begin: half the slack, so what the aperture
#: cannot hold is lost evenly from both edges instead of all from one.
#:
#: Measured rather than assumed, in the end. Three frames with gaps of 7, 19
#: and 26 columns all run picture to the last column with no base at the right
#: -- spread 3.7 to 11.6 counts where base shows 1 -- so the picture is wider
#: than what is left of the window and is being cut. The tightest of those puts
#: the frame past 421 columns, 35.90 mm, which corroborates the 36 mm
#: `MAX_REGISTRATION_MM` was derived from and leaves 0.49 mm of slack.
#:
#: Robust across the range that survives: at 35.9 mm the ideal is 3.5 columns
#: and at 36.2 mm it is 1.7, so this sits between them and is wrong by under a
#: pixel either way -- well inside the 3.2 columns of the smallest move the
#: transport can make.
TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0


def strip_offsets(
    frames, base: FilmBase, *, aperture_mm: float = APERTURE_MM,
    target: str = "centre",
) -> tuple[dict[int, float], dict[int, dict]]:
    """What each frame should be moved by, keyed by frame number.

    ``frames`` is ``(number, image)`` pairs for one strip.

    ``target`` is ``"centre"``, which aims every frame at `TARGET_GAP_MM` so
    the picture sits as squarely in the aperture as it can, or ``"median"``,
    which aims them at the strip's own middle.

    Centre is the default, and the difference is not cosmetic. A whole strip
    can be shifted the same way -- walk A's sixteen frames all carried 1.4 to
    2.2 mm of gap at the left, and all of them were losing that much picture
    off the right edge. Against the median every one of them reads as
    "nothing to do", because they agree with each other; against the centre
    they read as needing 1.2 to 2.0 mm, which is what they need. A target
    taken from the frames themselves cannot see an error they all share.

    Median remains for the case centre cannot serve: a strip whose frame width
    is not the 36 mm `TARGET_GAP_MM` assumes. It moves outliers to meet the
    frames that are already fine and never asks where the aperture's middle
    is.

    Sign follows `nudge` and `Approved.offset_mm`: positive means the film
    moves toward +x. A picture starting further right than the median needs a
    negative offset to come back, which is what the ladder showed -- commanded
    +0.27 mm moved the picture start +3.2 px.
    """
    measured: dict[int, int] = {}
    details: dict[int, dict] = {}
    mm_px = None
    for number, image in frames:
        start, detail = picture_span(image, base, aperture_mm=aperture_mm)
        details[int(number)] = detail
        mm_px = detail.get("mm_per_px", mm_px)
        if start is not None:
            measured[int(number)] = start

    if not measured or mm_px is None:
        return {}, details

    if target == "median":
        want = float(np.median(list(measured.values())))
    else:
        want = TARGET_GAP_MM / mm_px
    offsets = {n: (want - start) * mm_px for n, start in measured.items()}
    for number, start in measured.items():
        # The target goes on each frame rather than beside them: the mapping
        # is keyed by frame number, and one string key among the integers is
        # the kind of thing that reads fine and breaks a caller that iterates.
        details[number].update(source="measured", start_px=start,
                               target_px=want, target=target)
    return offsets, details


def fill_from_neighbours(
    offsets: dict[int, float], numbers, *, least: int = 3
) -> dict[int, float]:
    """Predict the frames the detector could not place, from the ones it did.

    A strip shares one pitch and one advance, so position is affine in frame
    number: a per-frame advance that is consistently long accumulates
    linearly. Fitted with Theil-Sen -- the median of pairwise slopes -- rather
    than least squares, because one bad placement is exactly what this exists
    to survive and least squares would let it tilt the whole line.

    Returns only the filled entries. Fewer than `least` measured frames fills
    nothing: two points give a line with no redundancy, and a line through two
    points that disagree is a confident answer built on nothing.
    """
    if len(offsets) < least:
        return {}
    xs = np.array(sorted(offsets), dtype=float)
    ys = np.array([offsets[int(x)] for x in xs], dtype=float)
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs)) for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    if not slopes:
        return {}
    slope = float(np.median(slopes))
    intercept = float(np.median(ys - slope * xs))
    return {int(n): slope * float(n) + intercept
            for n in numbers if int(n) not in offsets}


# --- holding a frame to the position an operator approved -------------------
#
# A different footing from everything above. `film_bounds` and `gap_edges`
# decide where a frame *should* be; these two only ask whether it is still
# where somebody looked at it and said yes. That matters, because every
# automatic detector built for this scanner has been confidently wrong on some
# frames -- and on real film `film_bounds` abstains on 97% of prescans, so
# there is nothing to be confidently wrong *with*.

#: How far the match is searched, in millimetres of film travel. Fixed, and
#: **not** derived from what a particular frame needs.
#:
#: `register`'s confidence is a z-score, `(peak - mean) / std` over the
#: searched surface, so it is a property of the match *and of the window it
#: was searched in*: the same pair of prescans scores 23.8 at a 16 px reach
#: and 129.7 at 200 px, rising almost linearly in between. A floor compared
#: against a confidence from some other reach is comparing two different
#: quantities. An earlier version sized the reach from each frame's travel
#: budget, which made the floor mean something different on every frame --
#: caught on the scanner 2026-09-14, where a true match scored 41.5 against a
#: floor of 40 and came within 1.5 points of refusing a good frame.
#:
#: Just over MAX_TRAVEL_MM, so any displacement the transport can produce is
#: inside the window. Expressed in mm rather than pixels so the figure means
#: the same at any prescan resolution.
SEARCH_MM = 9.0

#: Correlation z-score below which a match is not believed -- **only
#: comparable at :data:`SEARCH_MM`**.
#:
#: Measured 2026-09-14 at that reach over **3850 pairs** of real film -- every
#: roll and every 300 dpi library entry. 3754 pairs that are not the same
#: picture reach **30.2** at worst; 96 that are start at **54.9**, or 58.9
#: restricting to passes under an hour apart, which is the comparison this
#: actually makes.
#:
#: So the margin is **lopsided on purpose**: 24.8 points of clearance on the
#: side that matters and 3.9 on the side that does not. A false positive moves
#: the film to the wrong place; a refusal means the film is not moved, the
#: frame is scanned where it lies and flagged. Genuine matches will therefore
#: be refused occasionally, and that is the cheap error. Anything in 35-55 fits
#: this data; 55 is its conservative end.
#:
#: **Self-similar frames are the best case, not the worst** -- grass and sky
#: score *highest* (153-192), because phase correlation matches the phase
#: spectrum rather than the look of the texture. The failure mode is a
#: collapse, not a lie: as content fades the score falls smoothly past this
#: floor while the answer is still right, so a wrong-and-confident match has
#: no regime to live in. See `docs/registration-confidence-plan.md`, and
#: re-run `tools/registration_margin.py` after any change to `register`, the
#: prescan resolution or :data:`SEARCH_MM` -- each moves the scale.
CONFIDENCE_FLOOR = 55.0

#: How far off the film axis a match may sit. The transport moves only in x,
#: so a match that claims the picture also moved vertically has found
#: something else. True matches gave 0 to 1; the nulls ranged +-61.
MAX_DY_PX = 2

#: The same gate in millimetres, which is what :func:`measure_shift_mm` uses.
#:
#: Pixels were the wrong unit and the reason is specific rather than tidiness.
#: A pass that came back with its rows reversed matches at dy = -2 -- measured
#: on five of one roll's fifteen prescans, all five at exactly -2, with **zero**
#: margin against a 2 px gate. That offset is a distance on the film, the
#: forward-versus-reverse start `docs/byte14-plan.md` predicts, so at a 600 dpi
#: prescan it is 4 px and a pixel gate refuses every one of them. Raising
#: `prescan_resolution` would have silently turned the reversal rescue off
#: without anybody editing this line.
#:
#: 2 px of a 300 dpi prescan, which is where the 0 to 1 above was measured.
MAX_DY_MM = MAX_DY_PX * (24.3053 / 287.0)


#: How much better the reversed reading has to correlate before a pass is
#: turned to match its prescan. A half turn is a huge signal -- on real film
#: the right reading beats the wrong one by a wide margin or the picture has
#: no content to judge by at all -- so this is set to refuse the ambiguous
#: case rather than to catch the marginal one. Being wrong here stands a
#: photograph on its head.
REVERSAL_MARGIN = 0.25

#: The side of the square both passes are reduced to before they are compared.
#: Small on purpose: what is being asked is "which way up", not "how far", and
#: a coarse grid answers it while being blind to the grain and the resolution
#: difference between a 300 dpi prescan and a 3600 dpi scan.
REVERSAL_GRID = 96


def _comparable(image: np.ndarray, side: int = REVERSAL_GRID) -> np.ndarray | None:
    """One pass as a small, normalised, square greyscale grid.

    Square regardless of the shape it came from: a prescan and a scan cover
    the same transport window, so the same grid samples the same places on the
    film whatever each one's pixel count is. Normalised because one is an
    8-bit prescan and the other a 16-bit scan, and the question is about
    arrangement rather than about brightness.
    """
    a = np.asarray(image)
    if a.size == 0 or a.ndim < 2 or min(a.shape[:2]) < 2:
        return None
    if a.ndim == 3:
        # The visible planes only. Infrared holds the dust rather than the
        # picture, and a prescan has no fourth plane to compare it against.
        a = a[..., :min(3, a.shape[2])].mean(axis=2)
    rows = np.linspace(0, a.shape[0] - 1, side).astype(int)
    columns = np.linspace(0, a.shape[1] - 1, side).astype(int)
    grid = a[np.ix_(rows, columns)].astype(np.float64)
    grid -= grid.mean()
    size = float(np.sqrt((grid * grid).sum()))
    return grid / size if size > 0 else None


#: The arrangements a scan can come back in relative to its prescan. Only
#: these four: the carriage can reverse its travel and the film can be read
#: from the other side, and neither changes the shape -- a quarter turn would,
#: and the operator's own turn is a separate question from this one.
_READINGS = ((0, False), (180, False), (0, True), (180, True))


def reversal_against(
    reference: np.ndarray, image: np.ndarray
) -> tuple[tuple[int, bool], dict[str, Any]]:
    """The extra arrangement that makes `image` read like `reference`.

    Returns ``((degrees, flipped), detail)``, ``(0, False)`` meaning "as it
    came". `reference` is the prescan of the same photograph and `image` the
    pass to be judged.

    This exists because the scanner sometimes hands back a pass reversed with
    nothing to say it has. `MODE SELECT` byte 14 bit 0 skips the re-home and
    buys bidirectional speed, and a pass that immediately follows another
    bit-0-set pass comes back top-and-bottom reversed -- see CLAUDE.md. There
    is no status bit for it and no sense condition; the only evidence is that
    the picture does not match the framing pass taken a minute earlier.

    **It refuses far more readily than it corrects.** Every automatic detector
    written for this scanner has been confidently wrong on some frames, and
    this one can stand a photograph on its head, so the winner has to beat
    "as it came" by :data:`REVERSAL_MARGIN` before it is believed. A frame
    with nothing to correlate -- a blank sky, a badly under-exposed strip --
    scores everything alike and is left exactly as it arrived.
    """
    detail: dict[str, Any] = {"scores": {}, "margin": None, "reason": ""}
    here = _comparable(image)
    there = _comparable(reference)
    if here is None or there is None:
        detail["reason"] = "nothing to compare"
        return (0, False), detail

    scores: dict[tuple[int, bool], float] = {}
    for turn, flip in _READINGS:
        # On a square grid the four readings are index tricks, and doing them
        # here rather than through `orient` keeps this free of a dependency on
        # the preview module for the sake of two slices.
        candidate = here[:, ::-1] if flip else here
        if turn == 180:
            candidate = candidate[::-1, ::-1]
        scores[(turn, flip)] = float((candidate * there).sum())
    detail["scores"] = {f"{t}{'F' if f else ''}": round(v, 4)
                        for (t, f), v in scores.items()}

    as_it_came = scores[(0, False)]
    best, score = max(scores.items(), key=lambda kv: kv[1])
    detail["margin"] = round(score - as_it_came, 4)
    if best == (0, False):
        detail["reason"] = "as it came"
        return (0, False), detail
    if score - as_it_came < REVERSAL_MARGIN:
        detail["reason"] = (
            f"{detail['margin']:+.3f} is not enough to be sure; left alone")
        return (0, False), detail
    detail["reason"] = f"reads {best[0]}°{' mirrored' if best[1] else ''}"
    return best, detail


def measure_shift_mm(
    reference: np.ndarray,
    now: np.ndarray,
    *,
    aperture_mm: float = APERTURE_MM,
) -> tuple[float | None, dict[str, Any]]:
    """How far the film has moved since ``reference`` was taken, in mm.

    Positive means the picture sits further along +x than it did. ``None``
    means the question could not be answered, and the caller must not move on
    a measurement that is not believed -- refusing is the whole reason this
    returns an option rather than a number.

    **The sign is the easiest thing here to invert.** :func:`register` returns
    ``dx`` defined so ``a[i, j]`` matches ``b[i, j - dx]``, so content that has
    moved right by *s* pixels gives ``dx = -s``. Content displacement is
    therefore ``-dx``, verified at every shift from -60 to +60.

    The search reaches :data:`SEARCH_MM` whatever this frame is trying to do.
    :func:`register`'s own default of 64 px is 5.5 mm at 300 dpi, less than
    the transport can travel, so a frame at the far end would be measured as
    something nearer -- but sizing the reach per frame is worse than leaving
    it too small, because ``confidence`` is measured over the searched window
    and a per-frame reach makes the floor mean a different thing every time.

    This never consults :func:`gap_edges`. That counts gap runs anchored at the
    window's edges, so a frame drifted far enough that the gap sits in the
    middle returns ``(0, 0)`` and :func:`registration_error_mm` then reports
    ``0.0, "no gap in view -- registered"`` -- a positive assertion of
    correctness for a badly misregistered frame. Under a loop that would be
    fail-silent rather than fail-safe.
    """
    from .uniformity import register

    detail: dict[str, Any] = {"confidence": None, "dy": None, "dx": None,
                              "px": None, "resampled": False}
    if reference is None or now is None or reference.size == 0 or now.size == 0:
        detail["reason"] = "nothing to compare"
        return None, detail

    if reference.shape[:2] != now.shape[:2]:
        # The survey and the scan ran at different prescan resolutions. Match
        # the reference to what is in hand rather than refusing: the operator's
        # decision is still about this picture.
        #
        # It costs about half the confidence, measured on real passes -- 93.5
        # same-resolution against 47.4 resampled -- which is enough to drop a
        # good match below the floor. The geometry survives (the shift comes
        # back right); it is the peak that softens. So this is a fallback, and
        # the window pins a commissioned scan to the survey's own prescan
        # resolution rather than relying on it.
        reference = _resample_to(reference, now.shape[:2])
        detail["resampled"] = True

    width = max(now.shape[1], 1)
    mm_per_px = aperture_mm / width
    reach = int(SEARCH_MM / max(mm_per_px, 1e-9))

    # Both orientations, always, and the stronger one wins.
    #
    # A pass can come back with every row reversed: the byte-14 bit 0 of an
    # RGBI scan leaves the carriage at the far end, and the prescan after it
    # may be read bottom-up. Measured on one 600 dpi roll: **five of fifteen**.
    # The decode now turns every pass upright from its own line tags
    # (`rps7200.direction`), so both pictures normally arrive upright -- the
    # flipped reading stays for a reference taken before that, such as a
    # walk's stored `prescanNN.tif`, and for a pass whose direction was
    # unknown. `row_reversed` therefore says the reference and this pass
    # disagree top to bottom, not that this pass came back reversed.
    #
    # Safe to compare because the reversal is a flip in **y** while the
    # displacement wanted is in **x**. `reversal_against` reports it as
    # `(180, True)`, and fliplr then rot180 is flipud exactly, so no column is
    # ever permuted.
    #
    # Symmetric rather than a fallback after refusal, which is what this was
    # first written as. Trying the flip only once the upright reading has been
    # refused privileges "as it came" with nothing behind the privilege, and it
    # leaves `confidence` meaning the upright z-score on one frame and the
    # flipped one on the next -- a column that mixes two populations, in a
    # record `library.save` keeps so `CONFIDENCE_FLOOR` can be re-fitted from
    # it. The cost of doing both is one more `register`: 46 ms measured at the
    # size this runs at, against 45 s for the frame scan it precedes.
    #
    # It does not weaken the floor. The distribution that matters here is a
    # genuine pair read the wrong way up, which nobody had measured: over 15
    # real pairs it spans 4.9 to 8.9 against a floor of 55, separating the
    # right orientation from the wrong one by 10.5x to 28.2x.
    upright = register(reference, now, max_shift=reach)
    flipped = register(reference, now[::-1], max_shift=reach)
    reversed_wins = flipped[2] > upright[2]
    dy, dx, confidence = flipped if reversed_wins else upright
    other = (upright if reversed_wins else flipped)[2]
    detail.update(confidence=round(float(confidence), 2), dy=int(dy),
                  dx=int(dx), px=int(-dx), row_reversed=bool(reversed_wins),
                  confidence_other=round(float(other), 2))

    if confidence < CONFIDENCE_FLOOR:
        detail["reason"] = (f"correlation too weak ({confidence:.1f} below "
                            f"{CONFIDENCE_FLOOR:.0f}"
                            + (f"; {other:.1f} the other way up)"
                               if other else ")"))
        return None, detail

    # In millimetres, not pixels. Every reversed pass measured matched at
    # dy = -2 with **zero** margin against a gate of 2 px -- that offset is the
    # forward-versus-reverse start the byte 14 plan predicts, about 0.17 mm, so
    # at a 600 dpi prescan it is 4 px and a pixel gate would refuse every
    # rescue silently. Anyone raising `prescan_resolution` would have turned
    # this off without touching it.
    dy_mm = abs(dy) * (APERTURE_HEIGHT_MM / max(now.shape[0], 1))
    if dy_mm > MAX_DY_MM:
        detail["reason"] = (f"matched {dy_mm:.2f} mm off the film axis, which "
                            "the transport cannot do")
        return None, detail

    detail["reason"] = ("matched with its rows reversed" if reversed_wins
                        else "matched")
    return float(-dx) * mm_per_px, detail


def _resample_to(image: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resize onto ``shape``. Enough for correlation."""
    rows = np.clip((np.arange(shape[0]) * image.shape[0] // max(shape[0], 1)),
                   0, image.shape[0] - 1)
    columns = np.clip((np.arange(shape[1]) * image.shape[1] // max(shape[1], 1)),
                      0, image.shape[1] - 1)
    return np.take(np.take(image, rows, axis=0), columns, axis=1)


#: How close counts as arrived. **The smallest move the hardware can make** --
#: one SLIDE command at param 1, `STEP_MM + OVERHEAD_MM` on `DirectScanner`.
#: That value and not a smaller one is what makes a limit cycle impossible: the
#: loop can never ask for a correction it cannot deliver, so it cannot chatter
#: between two positions either side of the target. Duplicated rather than
#: imported because `direct` imports this module; a test pins the two together,
#: which is the same arrangement `tools/gui.py`'s FINE_STEP_MM already has.
#:
#: 0.3002 since 2026-09-22, up from 0.2719, because the ramp a command pays was
#: re-measured at 1.84 units rather than 1.572 and this constant is *defined*
#: as what param 1 travels. It has to follow the law or it stops being the
#: smallest move, and the no-limit-cycle argument above goes with it: the loop
#: would start commanding moves it cannot deliver and chatter either side of
#: the target, which is the one failure this number exists to prevent.
HOLD_TOLERANCE_MM = 0.3002

#: Moves per frame. Four prescans is already 70 s added to a frame.
MAX_HOLD_MOVES = 3

#: On top of the operator's own offset, how far one frame may travel before
#: the loop decides the film is not where anybody thinks it is.
HOLD_HEADROOM_MM = 2.0

#: How far a whole roll may nudge before aiming stops. Nothing bounded this
#: before: `hold_plan`'s budget resets every frame, and a sub-frame nudge does
#: not touch the transport's frame counter, so nothing downstream notices the
#: film creeping. Fifteen frames each corrected -1.5 mm walks the strip 22 mm
#: -- most of a frame -- with every individual move inside its own budget.
#:
#: Set well above what a real roll needs. Walk A wanted about 1.5 mm on its
#: first frame and the per-advance drift after that, so a roll that reaches
#: this has found something other than the gap.
ROLL_TRAVEL_LIMIT_MM = 12.0


def hold_plan(
    target_mm: float,
    measured_mm: float | None,
    spent_mm: float = 0.0,
    direction: int = 0,
    moves: int = 0,
) -> tuple[float | None, str]:
    """Whether to move, how far, and if not why not.

    ``target_mm`` is the operator's number and is a constant of the frame: this
    only ever computes a residual against it, so his decision cannot be
    overwritten by a measurement. ``measured_mm`` is where the film actually
    sits relative to the same reference, or ``None`` when that could not be
    established.

    Returns ``(millimetres, outcome)``. A distance means move that far;
    ``None`` means stop, and the outcome says why. The frame is scanned either
    way -- nothing here refuses to produce a picture.

    ``direction`` is the sign of the previous move within this frame, and a
    reversal is refused rather than performed. Backlash swallows two to three
    commands after a direction change and releases the distance later, so a
    loop that reverses is reasoning from a position the mechanism has not
    finished delivering. Overshoot is accepted and reported instead.
    """
    if measured_mm is None:
        return None, "unverified"

    residual = target_mm - measured_mm
    if abs(residual) < HOLD_TOLERANCE_MM:
        return None, "held"
    if moves >= MAX_HOLD_MOVES:
        return None, "not_converged"

    way = 1 if residual > 0 else -1
    if direction and way != direction:
        return None, "would_reverse"

    budget = abs(target_mm) + HOLD_HEADROOM_MM
    if spent_mm + abs(residual) > budget:
        return None, "budget"
    return residual, "move"


# --- deciding from more than one reading ------------------------------------
#
# Every detector in this file fails along one of four axes, and two detectors
# on the same axis are one detector:
#
#   A  content-relative thresholds -- a high-contrast photograph raises the
#      frame's own median above the gap. `gap_edges`, and the graveyard.
#   B  needs an empty aperture in view -- `film_bounds` abstains on 97% of real
#      prescans, because a loaded strip never shows one mid-roll.
#   C  smooth picture sitting at the base level -- sky, fog, an evenly-lit wall
#      defeat `picture_start` and `base_runs`.
#   D  nothing to correlate -- a dark or blank frame defeats `register`.
#
# A and B are why the four detectors in `docs/whole-roll-plan.md` were retired.
# C and D are the two that survive, and they are anti-correlated: the
# grass-and-sky frame that is C's nightmare is D's best case, scoring 185-189
# where the floor is 55, and the featureless frame that collapses D is the
# easiest read C ever gets. That pairing is what makes an ensemble worth having
# rather than two chances to make the same mistake.
#
# Two rules, both learned here rather than chosen:
#
#   * **Agreement is judged in millimetres, never in confidence.** The scores
#     are not on one scale and cannot be put on one: `register` returns a
#     z-score observed from 4 to 203, `film_base` a fraction and a count. A
#     millimetre means the same thing to all of them. Confidence only breaks
#     ties.
#   * **Where members disagree, follow the most confident one -- never blend.**
#     The bracket merge (archived, docs/multi-exposure/) settled this for
#     exposures, at `np.argmax(weights)`: averaging two readings that disagree
#     produces a number neither of them supports.

#: The **ceiling** on how far apart two members may be and still be called
#: agreed. The gate itself is their own two precisions added together; this
#: stops that sum from growing past the point where it would mean nothing.
#:
#: Set to the smallest move the transport can make. Two readings further apart
#: than one step would command different moves, so calling them agreed is
#: meaningless however imprecise either one admits to being -- and a member
#: whose own precision is already a whole step contributes agreement, never
#: distance, because `margin` decides which of a pair sets the number.
#:
#: Fixed in millimetres rather than computed per pass, deliberately: "within a
#: pixel" is half as much at 600 dpi and would tighten with nobody saying so,
#: which is the mistake `GAP_MIN_RUN` made.
AGREE_MM = HOLD_TOLERANCE_MM

#: How far the picture between two gaps may sit from a whole frame before one
#: of the bands is not a gap. The frame measures 35.90 to 36.2 mm across
#: (`TARGET_GAP_MM`), so this is that spread with a little room.
#:
#: Measured, not chosen. At `MAX_GAP_MM` -- the first value tried -- the right
#: hand reading fired on three frames of walk D and put the picture at 35.39 mm
#: every time, which is below the 35.90 the strip was measured at: the band it
#: found was picture, not base, and a tolerance that wide accepted all three.
FRAME_WIDTH_SPREAD_MM = 0.30

#: The furthest a correction can be asked for. Not a new limit -- it is what
#: `picture_start` can report before `MAX_GAP_MM` refuses the band, restated as
#: an offset. A reading past it is dropped from the vote rather than clamped
#: into range, because a clamped reading is a fabricated one.
#:
#: `MAX_REGISTRATION_MM` is emphatically *not* this bound. That is how well a
#: 36 mm frame can be placed in a 36.49 mm aperture; this is how badly the
#: transport can leave one, and walk A sat 1.4 to 2.2 mm out.
MAX_CORRECTION_MM = MAX_GAP_MM - TARGET_GAP_MM


@dataclass(frozen=True)
class Reading:
    """One member's answer, and how far it stands from its own refusal.

    ``margin`` is that distance as a fraction of the member's own threshold, 0
    meaning "only just qualified" and 1 "nowhere near refusing". It is not
    comparable between members in any deeper sense and is never treated as one:
    it breaks ties and does nothing else.

    ``precision`` is how finely this member can answer, in millimetres, and
    unlike ``margin`` it means exactly the same thing to all of them -- which is
    why the agreement test is built on it. A member that reads to the pixel and
    one that predicts to the transport's own repeatability do not have to be
    held to one tolerance, and holding them to one is what made the first
    version of this refuse eleven frames of thirteen.
    """

    mm: float | None
    margin: float
    source: str
    reason: str = ""
    detail: dict | None = None
    precision: float = 0.0


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _band(image: np.ndarray, start: int, length: int) -> tuple[float, float]:
    """A band's own level and its own flatness, in counts."""
    grey = _grey(image)[:, start:start + length]
    if grey.size == 0:
        return 0.0, 0.0
    return float(grey.mean()), float(grey.std(axis=0).mean())


def frame_offset_mm(
    image: np.ndarray, base: FilmBase, *, aperture_mm: float = APERTURE_MM
) -> Reading:
    """Where this frame sits, from the gap that entered at the left.

    The one-frame form of `strip_offsets(target="centre")`, and it needs no
    strip: that target is `TARGET_GAP_MM`, a constant of the aperture rather
    than of the frames, so a forward walk can use it from its third frame.

    This is the validated member. Against the displacement ladder it tracked
    the film at gain 1.008 with a residual rms of 0.021 mm and placed the
    picture on all seven rungs, including the four where `gap_edges` read 0.
    """
    start, detail = picture_span(image, base, aperture_mm=aperture_mm)
    edge = detail.get("edge")
    source = "right-gap" if edge == "right" else "left-gap"
    if start is None:
        return Reading(None, 0.0, "left-gap", detail.get("reason", ""), detail)

    # From the width, not from `detail["mm_per_px"]`, which is rounded to five
    # places for the manifest. The offset is a distance the transport is asked
    # to move; it should not carry a rounding meant for a log line.
    mm_px = aperture_mm / image.shape[1]
    gap_start, length = detail["gap"]
    level, flat = _band(image, gap_start, length)
    # How far this band stands from each of the three tests that could have
    # refused it. The smallest is the one that nearly did.
    margin = min(
        1.0 - abs(level - base.level) / max(base.level * BASE_TOLERANCE, 1e-9),
        1.0 - flat / max(base.flatness * 2.0, BASE_FLATNESS),
        1.0 - length * mm_px / MAX_GAP_MM,
    )
    return Reading(
        mm=TARGET_GAP_MM - start * mm_px,
        margin=_clip01(margin),
        source=source,
        precision=mm_px,
        reason=(f"{length} columns of base, {length * mm_px:.2f} mm"
                + (f", read from the right edge through a {FRAME_WIDTH_MM} mm "
                   "frame" if edge == "right" else " at the left edge")),
        detail=dict(detail, band_level=round(level, 2),
                    band_flatness=round(flat, 2)),
    )


def right_gap_closure(
    image: np.ndarray, base: FilmBase, *, aperture_mm: float = APERTURE_MM,
    frame_mm: float = 36.0,
) -> Reading:
    """Where this frame sits, from the gap at the *right* -- and a check.

    A second reading of the same quantity off the opposite edge, which
    `base_runs` already finds and `picture_start` throws away. What makes it
    worth having is not the second number but the constraint that comes with
    it: with both gaps in view the picture between them must be a frame wide,
    and how far it departs from that is an error bar this costs nothing to
    compute. The same trick as `uniformity.parity_residual` -- the redundancy
    is already in the measurement.

    Abstains whenever the right edge holds no gap, which is most of the time
    and is geometry rather than bad luck: 36.49 mm of aperture less ~36 mm of
    frame leaves 0.49 mm of slack, and `GAP_MIN_MM` is 0.25, so both edges can
    show a readable gap only in a window narrower than the detector's own
    resolution. These are not two opinions about one frame. They are the
    readings for the two opposite directions, and the left one structurally
    cannot see a frame that has gone too far the other way.

    Which is why the closure test has to be tight. On walk D a loose one
    accepted this reading on three frames and placed the picture at 35.39 mm
    each time -- under the 35.90 the strip measures -- so what it had found was
    picture at the base level, not a gap.
    """
    grey = _grey(image)
    if grey.size == 0:
        return Reading(None, 0.0, "closure", "nothing to measure")
    width = grey.shape[1]
    mm_px = aperture_mm / width
    smallest = max(1, int(round(GAP_MIN_MM / mm_px)))
    widest = int(round(MAX_GAP_MM / mm_px))
    margin_px = int(round(width * EDGE_FRACTION))

    runs = base_runs(image, base.level,
                     flatness=max(base.flatness * 2.0, BASE_FLATNESS),
                     min_run=smallest)
    detail: dict[str, Any] = {"runs": runs, "mm_per_px": round(mm_px, 5)}
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
                       f"the right band is {right[-1][1] * mm_px:.2f} mm, wider "
                       f"than the {MAX_GAP_MM} mm a gap can be", detail)

    start = left[0][0] + left[0][1]
    end = right[-1][0]
    if end <= start:
        return Reading(None, 0.0, "closure",
                       "the two bands meet -- no picture between them", detail)

    # The free error bar: with both gaps in view the picture is a frame wide.
    picture_mm = (end - start) * mm_px
    residual = picture_mm - frame_mm
    detail.update(picture_mm=round(picture_mm, 3),
                  closure_mm=round(residual, 3))
    if abs(residual) > FRAME_WIDTH_SPREAD_MM:
        return Reading(None, 0.0, "closure",
                       f"the picture between the bands is {picture_mm:.2f} mm, "
                       f"not the {frame_mm} mm a frame is -- one of them is not "
                       f"a gap", detail)
    return Reading(
        mm=(aperture_mm - TARGET_GAP_MM) - end * mm_px,
        margin=_clip01(1.0 - abs(residual) / FRAME_WIDTH_SPREAD_MM),
        source="closure",
        precision=mm_px,
        reason=f"picture {picture_mm:.2f} mm, closes to {residual:+.2f} mm",
        detail=detail,
    )


def predict_offset(history, target=None, *, least: int = 1) -> Reading:
    """Where the next frame will arrive, from the frames already walked.

    ``history`` is ``(number, arrived_mm, final_mm)`` per frame **in walk
    order**: the frame's number, the offset it showed when its first prescan
    came back, and the offset it was left at. The last two are both needed
    because correcting a frame breaks the series the obvious model would fit --
    once frame 3 is pulled to centre, frame 4 does not continue the line frames
    1 and 2 were on.

    What is actually constant is the *advance*, so what this fits is the error
    one advance delivers: ``arrived(n+1) - final(n)``. A median of those, added
    to where the last frame was left.

    Only **consecutive** frames contribute. A frame nobody could measure leaves
    no entry, and pairing across the hole would silently call two advances one
    -- which reads as a doubled error and predicts the next frame into the
    middle of nowhere. ``number`` is carried for no other reason.

    ``target`` is the frame this is predicting. It must follow the last frame
    in the history, since one advance is what the step describes.

    Uses no pixels from the frame it is predicting, which is the whole reason it
    belongs in the ensemble -- it fails on none of the four axes the pixel
    detectors fail on. It is strictly causal: a forward walk has no later
    frames, which is why `fill_from_neighbours` cannot serve here.
    """
    errors = [
        float(history[i + 1][1] - history[i][2])
        for i in range(len(history) - 1)
        if int(history[i + 1][0]) - int(history[i][0]) == 1
    ]
    if not history:
        return Reading(None, 0.0, "prior", "nothing walked yet")
    if target is not None and int(target) - int(history[-1][0]) != 1:
        return Reading(None, 0.0, "prior", (
            f"frame {int(history[-1][0])} is the last one measured, so frame "
            f"{int(target)} is more than one advance away"))
    if len(errors) < least:
        return Reading(None, 0.0, "prior",
                       f"{len(errors)} consecutive advance(s) seen, "
                       f"need {least}")
    step = float(np.median(errors))
    if len(errors) == 1:
        # One sample is a number with nothing to check it against. It may vote
        # -- agreeing with a pixel reading is still evidence -- but a margin of
        # zero means it can never win the tie-break and set the distance, and
        # its precision is taken as the whole hardware step, which is the most
        # conservative thing that is still a number.
        spread, margin, precision = 0.0, 0.0, HOLD_TOLERANCE_MM
    else:
        spread = float(1.4826 * np.median(np.abs(np.array(errors) - step)))
        margin = _clip01(1.0 - spread / HOLD_TOLERANCE_MM)
        # How repeatable this transport's advance actually is, measured on this
        # roll -- but never claimed more finely than the sample supports. Two
        # identical readings say the sample is small, not that the advance is
        # perfect, and a floor that ignores this is what made the prior refuse
        # its partner by 0.02 mm on frames 8 and 9 of walk D. The floor is a
        # whole step at one advance and decays as evidence arrives.
        precision = max(spread, HOLD_TOLERANCE_MM / np.sqrt(len(errors)))
    return Reading(
        mm=float(history[-1][2]) + step,
        margin=margin,
        source="prior",
        precision=precision,
        reason=f"{len(errors)} advance(s), {step:+.2f} mm each, "
               f"scatter {spread:.2f} mm",
        detail={"advances": len(errors), "step_mm": round(step, 4),
                "scatter_mm": round(spread, 4)},
    )


def combine(
    readings, *, agree_mm: float = AGREE_MM,
    bound_mm: float = MAX_CORRECTION_MM,
) -> tuple[float | None, dict]:
    """What the members together say, or ``None`` and why not.

    Two members agreeing is the gate, and what counts as agreement is **the two
    members' own precisions added together**, not one tolerance for everybody.
    Two pixel readings must land within a pixel of each other; a pixel reading
    and a prediction good to the transport's repeatability must land within
    their sum.

    That is not a loosening, it is the only test that means anything across
    members this unalike. Measured on walk D: the left gap reads to a quarter
    of a pixel against the displacement ladder, while the advance itself wanders
    +/-2 px frame to frame -- so a flat one-pixel gate asked the prior to be
    eight times more repeatable than the mechanism it describes, and refused
    eleven frames of thirteen for it.

    ``agree_mm`` is the ceiling on that sum, not the gate. Two readings further
    apart than the smallest move the hardware can make would command different
    moves, so calling them agreed would be meaningless however imprecise either
    one admits to being.

    Where several pairs qualify, the distance comes from whichever member stands
    furthest from its own refusal -- never from an average, which would be a
    number neither of them measured.

    One member alone is not an ensemble, and that is the case this exists to
    refuse: a single detector returning a confident number is exactly what
    `gap_edges` did on four of nine frames of a real walk, wrongly.
    """
    detail: dict[str, Any] = {
        "members": [
            {"source": r.source,
             "mm": None if r.mm is None else round(r.mm, 4),
             "margin": round(r.margin, 3), "reason": r.reason}
            for r in readings
        ]
    }
    usable, dropped = [], []
    for reading in readings:
        if reading.mm is None:
            continue
        if abs(reading.mm) > bound_mm:
            dropped.append(f"{reading.source} {say_units(reading.mm)}")
        else:
            usable.append(reading)
    if dropped:
        # Recorded rather than clipped into range. A reading this far out is a
        # detector that has found the wrong thing, and the distance it names is
        # not evidence about where the film is.
        detail["dropped"] = dropped
    if len(usable) < 2:
        return None, dict(detail, reason=(
            f"only {len(usable)} member(s) could measure this frame; two that "
            "agree are needed before the film is moved"))

    def gate(a: Reading, b: Reading) -> float:
        return min(a.precision + b.precision, agree_mm)

    agreed = {
        i for i, a in enumerate(usable)
        for j, b in enumerate(usable)
        if i != j and abs(a.mm - b.mm) <= gate(a, b)
    }
    if not agreed:
        worst = max(
            (abs(a.mm - b.mm) - gate(a, b), a, b)
            for i, a in enumerate(usable)
            for j, b in enumerate(usable) if i < j
        )
        return None, dict(detail, reason=(
            f"{len(usable)} members measured and none agree: closest are "
            f"{worst[1].source} {say_units(worst[1].mm)} and "
            f"{worst[2].source} {say_units(worst[2].mm)}, "
            f"{say_units(abs(worst[1].mm - worst[2].mm), signed=False)} apart "
            f"against {say_units(gate(worst[1], worst[2]), signed=False)} "
            "allowed"))

    best = max((usable[i] for i in agreed), key=lambda r: r.margin)
    detail.update(agreed=sorted(usable[i].source for i in agreed),
                  chose=best.source, margin=round(best.margin, 3))
    return best.mm, detail


#: Frames whose picture is held while the base calibrates. The base needs bands
#: from two frames, so the first frame or two arrive before there is anything
#: to place them against -- and they are worth placing, because they are the
#: prior's first advances. A 300 dpi prescan is about 3 MB, and this is a cap
#: on a transient rather than a budget: it empties the moment the base arms.
MAX_PENDING_FRAMES = 4


@dataclass
class StripWalk:
    """What a walk has learned about this strip so far. Causal, and pure.

    Nothing here touches the device -- this module measures and does not move
    film -- but it is the only thing in a walk that remembers anything.
    `scan_roll` accumulates holding, misses, scales, failures and the index,
    and no prescan history at all, which is why a forward walk had no way to
    know what its own advance was doing.

    Everything is keyed by frame number rather than appended to a list. That is
    what makes the calibration failure structurally impossible rather than
    merely unlikely: a second pass over frame 5 replaces frame 5's bands, so
    seven prescans of one frame cannot cast fourteen votes about the same
    columns. That is what they did once, and the "base" of 176.9 counts they
    agreed on was the picture.
    """

    bands: dict[int, list] = field(default_factory=dict)
    placed: dict[int, float] = field(default_factory=dict)   # as it arrived
    settled: dict[int, float] = field(default_factory=dict)  # as it was left
    pending: dict[int, Any] = field(default_factory=dict)
    base: FilmBase | None = None
    base_detail: dict = field(default_factory=dict)
    armed_level: float | None = None
    travel_mm: float = 0.0
    aiming: bool = True
    off_reason: str = ""
    misses: int = 0
    #: Something that reads a frame's edges itself -- the window hands in
    #: `tools/frame_edges`' walk reader through `scan_roll(edge_reader=...)`.
    #: With one, `observe` and `judge` ask it instead of calibrating a base
    #: level from the strip; the budget, the misses and the history stay here.
    #: Without one, this is the detector it always was. Duck-typed:
    #: ``observe(number, image) -> dict``, ``judge(number, image) ->
    #: (offset_mm | None, detail)``, ``reread(number, image) -> offset_mm | None``.
    reader: Any = None

    def landed(self) -> None:
        self.misses = 0

    def missed(self, why: str, *, give_up: int = 3) -> None:
        """A frame that did not reach its number. Three running is a setup fault.

        The same shape as `HOLD_GIVE_UP_FRAMES` and `max_failures`, and it
        matters more here than on the approved path: a sub-frame nudge does not
        touch the transport's frame counter, so a correction that goes wrong is
        inherited by every frame after it with nothing downstream to notice.
        """
        self.misses += 1
        if self.misses >= give_up:
            self.stop(f"{self.misses} frames in a row did not reach the "
                      f"position measured for them ({why}); aiming is off for "
                      "the rest of this roll. Frames are still scanned")

    def stop(self, why: str) -> None:
        """Stop aiming for the rest of this roll. Frames are still scanned."""
        if self.aiming:
            self.aiming, self.off_reason = False, why

    def observe(self, number: int, image) -> dict:
        """Take this frame's bands, and re-arm the base from everything seen.

        Called on every frame including the first two, and again on a frame's
        replacement prescan. It gets more trustworthy as the walk goes on, and
        it needs to: the level is physically a constant -- one lamp, one
        exposure, one shading reference, and a prescan meters nothing -- so
        anything that moves it is the detector finding picture rather than base.
        """
        if self.reader is not None:
            return dict(self.reader.observe(int(number), image))
        self.bands[int(number)] = edge_bands(image)
        was = self.base
        self.base, self.base_detail = film_base_from(self.bands)

        if self.base is None:
            if len(self.pending) < MAX_PENDING_FRAMES:
                self.pending[int(number)] = image
            return dict(self.base_detail)

        if self.armed_level is None:
            self.armed_level = self.base.level
        elif abs(self.base.level - self.armed_level) > (
                self.armed_level * BASE_TOLERANCE):
            self.stop(
                f"the base level moved from {self.armed_level:.1f} to "
                f"{self.base.level:.1f} counts. One lamp at one exposure "
                "cannot do that, so what is being measured is not base")

        if was is None and self.pending:
            # The base could not arm before the second frame, but the frames it
            # took to arm it are still in hand. Placing them now costs nothing
            # and gives the prior its first advances -- on walk D it is the
            # difference between aiming from frame 7 and aiming from frame 6.
            for earlier in sorted(self.pending):
                if earlier != int(number):
                    reading = frame_offset_mm(self.pending[earlier], self.base)
                    if reading.mm is not None:
                        self.placed[earlier] = reading.mm
            self.pending.clear()
        return dict(self.base_detail)

    def history(self, before: int | None = None) -> list:
        """``(number, arrived, left at)`` for the frames already walked.

        Strictly before ``before``, because a frame cannot be evidence about
        where it is itself going to be.
        """
        return [
            (n, self.placed[n], self.settled.get(n, self.placed[n]))
            for n in sorted(self.placed)
            if before is None or n < int(before)
        ]

    def judge(self, number: int, image) -> tuple[float | None, dict]:
        """What to do about this frame, from every member that can see it."""
        if not self.aiming:
            return None, {"reason": self.off_reason, "members": []}
        if self.reader is not None:
            decision, detail = self.reader.judge(int(number), image)
            if decision is not None:
                self.placed[int(number)] = float(decision)
            return decision, dict(detail)
        if self.base is None:
            return None, {"members": [], "reason": (
                "the strip's base level is not calibrated yet: "
                + str(self.base_detail.get("reason", "")))}

        left = frame_offset_mm(image, self.base)
        closure = right_gap_closure(image, self.base)
        # The prior is asked before this frame is recorded, never after.
        prior = predict_offset(self.history(number), number)
        decision, detail = combine([left, closure, prior])
        if left.mm is not None:
            self.placed[int(number)] = left.mm
        detail["base_level"] = round(self.base.level, 2)
        return decision, detail

    def record(self, number: int, settled_mm: float | None,
               travelled_mm: float = 0.0, verified: bool = True) -> None:
        """Where this frame was left, and what it cost to leave it there.

        An **unverified** move drops the frame from the history entirely. The
        film has moved and nothing knows how far, so the arrival this frame
        contributed is a stale number -- and a prior built on one bad delta is
        the poison a median cannot fix once a few of them agree.
        """
        if not verified:
            self.placed.pop(int(number), None)
            self.settled.pop(int(number), None)
        elif settled_mm is not None:
            self.settled[int(number)] = float(settled_mm)
        self.travel_mm += abs(float(travelled_mm))
        if self.travel_mm > ROLL_TRAVEL_LIMIT_MM:
            self.stop(
                f"this roll has nudged "
                f"{say_units(self.travel_mm, signed=False)} in total, past "
                f"the {say_units(ROLL_TRAVEL_LIMIT_MM, signed=False)} a strip "
                "should ever need. A "
                "sub-frame move does not touch the frame counter, so nothing "
                "downstream would notice the film creeping")

    def affordable(self, offset_mm: float) -> bool:
        """Whether this roll can still afford that move."""
        return self.travel_mm + abs(offset_mm) <= ROLL_TRAVEL_LIMIT_MM


# --- proposing a whole strip's positions at once ----------------------------
#
# The walk takes one complete pass over the strip, and only then is anything
# decided. That is worth more than deciding as it goes, and the reason is not
# effort but evidence: a forward walk can only ever fit the frames behind it,
# while a finished walk fits across all of them and can predict a frame it
# could not place from *both* sides.
#
# It also puts the decision where it can be argued with. The proposals arrive
# in the contact sheet, the operator changes the ones he disagrees with, and
# the scan is held to whatever is there -- his number where he set one, this
# one where he left it alone.


def _theil_sen(xs, ys) -> tuple[float, float]:
    """Median of pairwise slopes, and the intercept that centres it.

    Not least squares: one bad placement is precisely what this has to survive,
    and least squares lets a single one tilt the whole line.
    """
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs)) for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    if not slopes:
        return 0.0, float(np.median(ys)) if len(ys) else 0.0
    slope = float(np.median(slopes))
    return slope, float(np.median(np.asarray(ys) - slope * np.asarray(xs)))


def predict_from_strip(placed: dict, number: int, *, least: int = 3) -> Reading:
    """Where the strip says this frame should sit, judged without it.

    Leave-one-out: the line is fitted through every *other* placed frame, so
    the prediction owes nothing to the reading it is about to be compared with.
    Including the frame would make the two members agree by construction, which
    is the failure an ensemble exists to avoid -- two detectors that cannot
    disagree are one detector.

    This is the member a whole-strip pass has and a forward walk does not: it
    can see a frame's neighbours on both sides.
    """
    others = {int(n): float(v) for n, v in placed.items() if int(n) != int(number)}
    if len(others) < least:
        return Reading(None, 0.0, "strip",
                       f"{len(others)} other frame(s) placed, need {least}")
    xs = np.array(sorted(others), dtype=float)
    ys = np.array([others[int(x)] for x in xs], dtype=float)
    slope, intercept = _theil_sen(xs, ys)
    residual = np.abs(ys - (slope * xs + intercept))
    scatter = float(1.4826 * np.median(residual))
    return Reading(
        mm=slope * float(number) + intercept,
        margin=_clip01(1.0 - scatter / HOLD_TOLERANCE_MM),
        source="strip",
        # Never finer than the scatter of the frames it was fitted through,
        # and never finer than the smallest move that scatter could hide.
        precision=max(scatter, HOLD_TOLERANCE_MM / np.sqrt(len(others))),
        reason=f"{len(others)} frames, {slope:+.3f} mm per frame, "
               f"scatter {scatter:.2f} mm",
        detail={"frames": len(others), "slope_mm": round(slope, 4),
                "scatter_mm": round(scatter, 4)},
    )


def propose_offsets(
    frames, *, aperture_mm: float = APERTURE_MM, target: str = "centre",
) -> tuple[dict[int, float], dict[int, dict]]:
    """What every frame of a walked strip should be moved by, and why.

    ``frames`` is ``(number, image)`` for the whole strip, in any order.

    Returns the proposals and a note per frame saying where each came from:

      ``measured``     two members agreed
      ``unconfirmed``  one member could read the frame and nothing corroborated it
      ``neighbours``   nothing could read it; the strip's own line spoke for it

    **The two-members gate does not apply here, and that is deliberate.** It
    exists to stop film being moved on one detector's word, and nothing is
    moved by this -- these are proposals, and a person looks at every one before
    any of them reaches the transport. So the rule that serves him is the
    opposite: show the best reading there is and say how well supported it is.

    Getting that backwards made this worse than useless on walk E. Frames 1 to
    4 each carried a real left-gap reading near -1.1 mm, the members disagreed,
    and the fallback replaced a measurement from the one detector graded on the
    displacement ladder with a line extrapolated from the far end of the strip
    -- and said nothing about having done so. A model may stand in for a
    measurement that does not exist. It may not overrule one that does.
    """
    images = [im for _n, im in frames]
    base, base_detail = film_base(images)
    notes: dict[int, dict] = {}
    if base is None:
        why = ("the strip's base level could not be calibrated: "
               + str(base_detail.get("reason", "")))
        return {}, {int(n): {"reason": why, "source": "none"} for n, _ in frames}

    # Every frame's own reading first: the leave-one-out fit needs them all
    # before it can judge any one of them.
    placed, _detail = strip_offsets(frames, base, aperture_mm=aperture_mm,
                                    target=target)

    proposals: dict[int, float] = {}
    for number, image in frames:
        number = int(number)
        left = frame_offset_mm(image, base, aperture_mm=aperture_mm)
        closure = right_gap_closure(image, base, aperture_mm=aperture_mm)
        strip = predict_from_strip(placed, number)
        decision, detail = combine([left, closure, strip])
        notes[number] = dict(detail, base_level=round(base.level, 2))
        if decision is not None:
            proposals[number] = decision
            notes[number]["source"] = "measured"
        elif left.mm is not None and abs(left.mm) <= MAX_CORRECTION_MM:
            # The reading stands, and the sheet says it was not corroborated.
            # `left-gap` is the member validated against the ladder at gain
            # 1.008 with a residual rms of 0.021 mm; the disagreement is worth
            # showing, not worth discarding the measurement over.
            proposals[number] = left.mm
            notes[number]["source"] = "unconfirmed"
        else:
            notes[number]["source"] = "none"

    # Only now, and only from what survived: a frame nothing could place gets
    # the strip's own line, which is the one thing that can speak for it.
    filled = fill_from_neighbours(proposals, [int(n) for n, _ in frames])
    for number, value in filled.items():
        proposals[number] = value
        notes[number] = dict(notes.get(number, {}), source="neighbours",
                             reason="predicted from the frames either side")
    return proposals, notes


# --- the transport's own units ----------------------------------------------
#
# Millimetres are prohibited here; see CLAUDE.md. A distance is a number of
# **param units**, where one unit is what a single increment of the `SLIDE`
# param adds. Everything below is floating point, and the only rounding in the
# whole path is the integer that goes into the command.
#
# Measured 2026-09-21/22 on the film, `tools/verify_protocol.py` stages 10-13,
# passes in `probe/step-calibration/`:
#
#   one command travels   param + 1.84  units
#   one unit is           1.2423        columns of a 300 dpi prescan
#
# The first term came from three legs of equal param total over ten, five and
# one commands -- ten small commands travel 2.40x as far as one large one, and
# the spread between the legs is the per-command cost with nothing assumed. The
# second is the mean of twelve `param 87` commands, every one measured with a
# confidence above the floor, sd 0.90 px.

#: Columns of a 300 dpi prescan that one unit of param moves the film.
COLUMNS_PER_UNIT = 1.2423

#: What issuing a command costs, in units, before any param is applied. Real,
#: and 21% larger than the fit in `docs/protocol.md` section 11 -- that fit used
#: single commands only, so nothing in it could see a per-command term.
COMMAND_COST = 1.84

#: The width a 300 dpi prescan comes back as. Columns scale with it.
PRESCAN_COLUMNS = 428.0

#: Gap to gap along the strip, in units: tracked across twelve commands and
#: four gaps entering and leaving on walk K (455, 454, 455 px; see
#: `docs/frame-measurement-plan.md`). The frame-edge detector's gap model
#: (`tools/frame_edges/gapmodel.py`) places a frame's far edge through it.
PITCH_UNITS = 366.5

#: A frame's width, in units: 435.6 columns of a 300 dpi prescan, measured on
#: 39 pairs of prescans of one frame -- one showing the frame's left edge, the
#: other its right, the registered shift between them closing the width with
#: no pitch assumed (interquartile 435.3-435.9; `research/frame-edge`). A
#: second camera read 432.7 (2 pairs): the difference moves a centred frame by
#: 1.2 units, under half the smallest move. Wider than the aperture (428
#: columns), so a centred frame shows no base at all.
FRAME_WIDTH_UNITS = 350.6

#: The largest param the byte allows. `param 255` is accepted by the device;
#: nothing above 87 had been sent before 2026-09-21. The step stays constant to
#: within 3% out to at least 160.
MAX_PARAM = 255

#: The largest param whose move can still be *verified* by comparing two
#: prescans. Above this they no longer share enough film for the correlation to
#: lock: at 200 and 255 it returned confidences of 6-23 and impossible negative
#: distances. A correction that cannot be checked is worse than a smaller one
#: that can.
MAX_VERIFIABLE_PARAM = 160


def units_per_column(width: int) -> float:
    """One column of a pass this wide, in param units.

    Replaces `APERTURE_MM / width`, which is wrong by 0.7%: with the true pitch
    the geometry of a whole roll closes to -0.35 units and with that expression
    to +1.5. A wider pass has proportionally narrower columns, so the
    resolution never has to be passed in.
    """
    return (PRESCAN_COLUMNS / float(width or 1)) / COLUMNS_PER_UNIT


def columns_per_unit(width: int) -> float:
    """The inverse, for turning a measured distance back into columns."""
    return 1.0 / units_per_column(width)


#: The smallest distance a single command can deliver: `param 1` plus the cost
#: of issuing it. Nothing between zero and this exists, which is what makes a
#: deadband unavoidable rather than a choice.
SMALLEST_MOVE = 1.0 + COMMAND_COST

#: The largest a single command can deliver.
LARGEST_MOVE = MAX_PARAM + COMMAND_COST

#: The largest a single command can deliver and still be checked afterwards.
LARGEST_VERIFIABLE_MOVE = MAX_VERIFIABLE_PARAM + COMMAND_COST


def command_for(units: float, *, verifiable: bool = True) -> tuple | None:
    """The one `SLIDE` command that moves the film this far, or None.

    **One command, never several.** The scatter of a command is about one
    prescan column whatever its size -- `param 12` measured sd 1.2 and `param
    87` sd 0.90 -- so it does not shrink when a move is split up. Two commands
    are twice the noise and twice the time for the same distance, and each one
    also costs `COMMAND_COST` before it moves at all. Splitting a correction is
    strictly worse and the old `plan_nudges` shape should not come back.

    Returns ``(action, param, delivers)`` with ``action`` 0x00 forward and 0x01
    backward, ``param`` the integer byte -- **the only rounding in this whole
    module** -- and ``delivers`` the distance that integer actually buys, so a
    caller can carry the difference rather than pretend it asked for what it
    got.

    ``None`` means leave the film alone: either the distance is below the
    smallest command that exists, or it is beyond what one command can do.
    """
    wanted = float(units)
    ceiling = LARGEST_VERIFIABLE_MOVE if verifiable else LARGEST_MOVE
    if not np.isfinite(wanted) or abs(wanted) < SMALLEST_MOVE / 2.0:
        return None
    param = int(round(abs(wanted) - COMMAND_COST))
    if param < 1:
        # Nearer to the smallest command than to standing still, so make it.
        param = 1
    if abs(wanted) > ceiling:
        return None
    param = min(param, MAX_PARAM)
    delivers = (param + COMMAND_COST) * (1.0 if wanted > 0 else -1.0)
    return (0x00 if wanted > 0 else 0x01, param, delivers)


def describe_command(units: float, *, verifiable: bool = True) -> str:
    """What `command_for` would do, in words, for a log line."""
    got = command_for(units, verifiable=verifiable)
    if got is None:
        if abs(units) < SMALLEST_MOVE / 2.0:
            return (f"{units:+.2f} units is below the smallest command there "
                    f"is ({SMALLEST_MOVE:.2f}); left alone")
        return (f"{units:+.2f} units is more than one command can deliver "
                f"and be checked ({LARGEST_VERIFIABLE_MOVE:.0f})")
    action, param, delivers = got
    return (f"{units:+.2f} units -> {'forward' if action == 0 else 'back'} "
            f"param {param}, delivering {delivers:+.2f}, "
            f"leaving {units - delivers:+.2f}")


# --- finding a gap by looking down it, not across it ------------------------
#
# Every detector before this one began by averaging the rows away, and that is
# why they could all be fooled by the same thing. Unexposed film base is bright
# and flat down a column; so, on a negative, is a tree silhouette at dusk --
# clear film at the base level, dead flat by column mean. Measured over one
# delivered roll, **level, flatness, two-dimensional uniformity and the
# infrared plane all fail to separate them**, and the false bands they produced
# put four frames wrong, one of which cost the frame after it as well.
#
# Stefan named the test from the picture before any of this was traced:
# unexposed film gives "a sharp line and will be completely black from the top
# to the bottom". A real gap is the same width in **every row**. A silhouette
# is not.
#
#     real gaps              per-row width, interquartile range    2-4 columns
#     a 35-column silhouette per-row width, interquartile range    233 columns

#: How much of a column's height must be base before the column counts. Not
#: all of it: a dust speck or a scratch crossing the gap should not disqualify
#: the column it crosses.
GAP_ROW_AGREEMENT = 0.90

#: How far the median row may run past the tenth-percentile row, as a fraction
#: of the band, before this is something other than a gap.
#:
#: Measured on walk K: a real gap reads 0.09 to 0.22 -- frame 10 at 10/11/12
#: columns, frame 13 at 11/12/29, frame 5 at 9/11/20. The silhouette on frame 9
#: reads 12/39/138, which is **2.25**. An order of magnitude between them, so
#: the threshold is not delicate.
GAP_WIDTH_SPREAD = 0.5


@dataclass(frozen=True)
class Band:
    """A run of unexposed base, and how much it looks like one.

    ``start`` and ``width`` are floating point and in columns; ``consistency``
    runs 0 to 1, one meaning every row agreed on the same width.
    """

    start: float
    width: float
    consistency: float
    rows: float

    @property
    def end(self) -> float:
        return self.start + self.width


def _row_runs(mask: np.ndarray, side: str) -> np.ndarray:
    """For each row, how many columns of base it has at one edge.

    Counted from the edge inward, because that is the only place a gap can be:
    base creeps in from the left or the right and cannot float in the middle of
    a frame.
    """
    look = mask if side == "left" else mask[:, ::-1]
    # the first False in each row is where that row's run ends
    ends = np.argmin(look, axis=1)
    # a row that is base all the way across has no False at all
    ends = np.where(look.all(axis=1), look.shape[1], ends)
    return ends.astype(np.float64)


def edge_band(image: np.ndarray, level: float, side: str, *,
              tolerance: float = BASE_TOLERANCE) -> tuple:
    """The band of base at one edge, judged by looking down it.

    Returns ``(Band | None, detail)``. The band is refused when too few rows
    agree that there is one, or when the rows disagree about how wide it is --
    which is the silhouette case and the one that has cost real frames.
    """
    pixels = _grey(image)
    if pixels.size == 0:
        return None, {"reason": "nothing to measure"}
    # Smoothed along the row before thresholding. A single pixel of noise
    # should not end a row's run: prescan noise is a few counts against a
    # tolerance of a few counts, so raw per-pixel thresholding ends every row
    # early at a random speck and the widths then disagree for a reason that
    # has nothing to do with the film. Three columns is the narrowest smooth
    # that removes it and it costs a column and a half of edge resolution,
    # which is well inside one unit.
    smooth = np.copy(pixels)
    if pixels.shape[1] >= 3:
        smooth[:, 1:-1] = (pixels[:, :-2] + pixels[:, 1:-1] + pixels[:, 2:]) / 3.0
    is_base = np.abs(smooth - level) <= level * tolerance
    runs = _row_runs(is_base, side)

    saying = float(np.mean(runs > 0))
    detail: dict[str, Any] = {"side": side, "rows_agreeing": round(saying, 3)}
    if saying < GAP_ROW_AGREEMENT:
        return None, dict(detail, reason=(
            f"only {saying*100:.0f}% of rows show base at the {side} edge, "
            f"under the {GAP_ROW_AGREEMENT*100:.0f}% a band running the whole "
            "height would give"))

    speaking = runs[runs > 0]
    # The width nearly every row agrees on, not the middle one. Rows that run
    # longer are rows where the picture beside the gap happens to sit near the
    # base level, and there are always a few: frame 13 of walk K reads 11, 11,
    # 12 at the tenth, twenty-fifth and fiftieth percentile and then 24 and 29
    # at the upper ones. A median lets those drag the answer; the low
    # percentile is the edge the film actually has.
    width = float(np.percentile(speaking, 10))
    middle = float(np.percentile(speaking, 50))
    spread = middle - width
    consistency = 1.0 - min(1.0, spread / max(width, 1e-9))
    detail.update(width=round(width, 2), spread=round(spread, 2),
                  consistency=round(consistency, 3))
    if spread > width * GAP_WIDTH_SPREAD:
        return None, dict(detail, reason=(
            f"the rows disagree about how wide it is -- {spread:.0f} columns "
            f"of spread on a {width:.0f}-column band. Unexposed film gives the "
            "same width in every row; this does not, so it is picture that "
            "happens to sit at the base level"))

    start = 0.0 if side == "left" else float(pixels.shape[1]) - width
    return Band(start=start, width=width, consistency=consistency,
                rows=saying), detail
