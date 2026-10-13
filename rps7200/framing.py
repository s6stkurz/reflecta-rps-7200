"""Where the film and the picture sit in the transport window.

A frame is wider than the aperture: `FRAME_WIDTH_UNITS`, 350.6 units, against
the 428-column prescan's 344.5, measured on prescans of one frame -- so a
centred frame shows no unexposed base at either edge, and even a sliver of base
means the frame is several units off. It is the one frame model here: the
model `tools/frame_edges` centres with, and since 2026-10-13 the one the
strip-level detector below aims a roll with no edge reader by -- a positive,
Kodachrome, or `DirectScanner.scan_roll` called on its own (`FRAME_MODEL`).
The 36.0 mm frame in a 36.5 mm aperture this module grew up on is retired
(Stefan's decision, 2026-10-13); what it computed lives on only in
`tools/roll_registration_history.py`, so the stored studies replay as they
ran. The frame was measured on negatives and has never been checked on a
slide here.

Every distance here is in units of the adjustment parameter, on the hold's
scale (`units_per_column_hold`); see the units block below. A record written
before 2026-10-13 holds millimetres, and `units_of` reads it.

Everything here measures. Nothing here moves the film.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np

from .protocol import (
    COMMAND_UNITS,
    COORD_PER_INCH,
    MM_PER_INCH,
    MM_PER_UNIT,
    say,
    to_units,
)

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


# --- the units every distance here is in -----------------------------------
#
# Millimetres are prohibited (CLAUDE.md), in what a person reads and, since
# 2026-10-13, in what is stored. A distance is a number of units of the
# `SLIDE` param on **the hold's scale**: a millimetre of the aperture over
# `MM_PER_UNIT`, which is the scale `measure_shift` measures a move in and
# `DirectScanner.nudge_units` sends one by, so a hold lands on the column it
# asked for. The frame-edge geometry (`FRAME_WIDTH_UNITS`, `PITCH_UNITS`) was
# measured on another scale, `COLUMNS_PER_UNIT`, 0.2% apart, and crosses over
# through columns: `reader_columns_to_hold_units`.
#
# Each constant is its historic millimetre literal over `MM_PER_UNIT` --
# written as that quotient, never as a rounded number of units, because a
# two-place literal moves the columns derived from it: 2.37 units in place of
# 0.25 mm changes `GAP_MIN_UNITS` in columns of a 5172-wide pass. Nothing
# sent to the scanner changed with the move to units (the tests hold every
# param to the one the millimetre code sent).

#: The whole transport window, 10344 units of 1/7200 inch: 345.2 units.
APERTURE_UNITS = ((FULL_FRAME[2] - FULL_FRAME[0] + 1) * MM_PER_INCH
                  / COORD_PER_INCH) / MM_PER_UNIT

#: How far the match is searched, in units of film travel: 11.0 mm, 104.1
#: units. Why that far, and why fixed: see :data:`CONFIDENCE_FLOOR` below and
#: the note above :func:`reach_px`.
SEARCH_UNITS = 11.0 / MM_PER_UNIT

#: How wide a band must be before it is a gap rather than noise: 0.25 mm,
#: 2.37 units.
GAP_MIN_UNITS = 0.25 / MM_PER_UNIT

#: How close counts as arrived: **the smallest move the hardware can make**,
#: one SLIDE command at param 1, 2.84 units. See the note above `hold_plan`.
HOLD_TOLERANCE_UNITS = 1.0 + COMMAND_UNITS

#: A shortfall past which a frame's picture is called drifted: 0.85 mm, 8.04
#: units -- the vendor's picture width (`VENDOR_PICTURE_COORDS`) less what a
#: prescan's film bounds found, in units. The window and `tools/scan_roll.py`
#: both judge with this one; each had its own typed 0.85.
DRIFT_SHORTFALL_UNITS = 0.85 / MM_PER_UNIT

#: The width of the picture the vendor's software scans, in scanner
#: coordinates at maximum resolution: its own detected windows on a strip it
#: had registered correctly -- (96,71) to (10175,6815), so 10079 wide --
#: against a 10344-coordinate transport window. **Not the frame model**
#: (`FRAME_MODEL`): it is where CyberView crops, and what `registration`
#: measures a picture's shortfall against, because a picture measuring much
#: less than this has part of itself outside the aperture -- the only way a
#: drifted frame is visible, since the prescan cannot see what the window does
#: not cover. Named `NOMINAL_FRAME_WIDTH` until 2026-10-13.
VENDOR_PICTURE_COORDS = 10080

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
        clear = float(np.percentile(profile, CLEAR_PERCENTILE))
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

#: The percentile of a profile `film_bounds` calls "clear". Not the maximum,
#: deliberately: a threshold set as a fraction of the maximum is set by its
#: worst outlier. Named so `tools/film_edge_study.py` reads the one in force
#: rather than a copy of it.
CLEAR_PERCENTILE = 98.0


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

    A frame that has drifted is a frame with its edge outside the aperture,
    and no scan window can get that back. The vendor's own 5-frame strip shows
    it happening -- its detected windows started at x=96 for four frames and
    then at x=1727 for the fifth, which lost 1631 coordinates, 54 units, of
    picture.

    ``offset`` is signed, in scanner coordinates at maximum resolution (1/7200
    inch): positive means the picture sits right of centre, i.e. the film is
    under-advanced. ``offset_units``, ``shortfall_units`` and
    ``margin_units`` are the same three in units of the adjustment parameter.

    ``shortfall`` is the one that matters, and it is why the width is measured at
    all. A drifted frame cannot be seen directly -- the prescan only covers the
    aperture, so a picture hanging outside it is simply not there to be found.
    What shows instead is a picture *narrower* than a whole frame. The vendor's
    fifth strip frame measured 8472 coordinates against 10079 for the four
    before it: 1.6 k of them, 54 units, of picture that never reached the
    sensor.

    Measurement only. Nothing here moves the film.
    """
    x0, _, x1, _ = film_bounds(image, full_frame)
    fx0, _, fx1, _ = full_frame
    width = x1 - x0
    offset = (x0 + x1) / 2.0 - (fx0 + fx1) / 2.0
    shortfall = max(0, VENDOR_PICTURE_COORDS - width)

    def in_units(coordinates: float) -> float:
        return round(coordinates * MM_PER_INCH / COORD_PER_INCH / MM_PER_UNIT, 2)

    return {
        "x0": int(x0),
        "x1": int(x1),
        "width": int(width),
        "offset": int(round(offset)),
        "offset_units": in_units(offset),
        "shortfall": int(shortfall),
        "shortfall_units": in_units(shortfall),
        "margin": int(min(x0 - fx0, fx1 - x1)),
        "margin_units": in_units(min(x0 - fx0, fx1 - x1)),
    }


# --- registration from the inter-frame gap -------------------------------

#: A column counts as gap when it is this many MADs above the median level.
GAP_LEVEL_SIGMA = 2.0
#: ...and varies less than this fraction of the picture's own variation.
GAP_FLATNESS = 0.35
#: Shorter runs than this at an edge are noise, not a gap.
GAP_MIN_RUN = 3

# `registration_error` stood below `gap_edges` until 2026-10-13: the first
# one-shot corrector, bounded by `MAX_REGISTRATION_UNITS` -- the 4.6 units of
# slack a 36 mm frame left in the aperture. Nothing had called it since
# `_aim_frame` replaced it, and the frame it was derived from is retired; it
# is kept, as it was, in `tools/roll_registration_history.py`.


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
#   * everything is in units of travel, not pixels. `GAP_MIN_RUN` above is in
#     pixels, so its deadband is 2.4 units at 300 dpi and 1.2 at 600 without
#     anyone saying so. `GAP_MIN_UNITS`, `SEARCH_UNITS` and
#     `HOLD_TOLERANCE_UNITS` are distances for this reason.

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


def edge_bands(image: np.ndarray, *, aperture_units: float = APERTURE_UNITS,
               model: FrameModel | None = None) -> list[tuple[float, float]]:
    """The flattest run at each edge of one frame, as ``(level, flatness)``.

    Found by flatness alone, deliberately: this is what measures the level, so
    it must not assume one to find it.

    A run wider than the model's widest gap (`FrameModel.max_gap_units`) is
    dropped here rather than survived later. `picture_start` has always
    refused such a band -- about 16 units of gap exist between frames on 135
    film, and a longer run is the end of the strip or picture at the base
    level -- but the calibration did not, which is why a 104-column band of
    sky could reach the median at all and why the spread had to be made robust
    to it after the fact. So this bound decides which bands *arm* the strip's
    base as well as which gaps are believed: narrowing it from 24.6 units to
    18.8 on 2026-10-13 dropped 22 of the 393 bands the stored walks' prescans
    held (6 negative, 15 black and white, 1 positive) and changed which of
    those walks armed in two cases, both black and white.
    """
    grey = _grey(image)
    if grey.size == 0:
        return []
    model = model or FRAME_MODEL
    widest = model.max_gap_units / (aperture_units / grey.shape[1])
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


def film_base(images, *, model: FrameModel | None = None
              ) -> tuple[FilmBase | None, dict]:
    """Calibrate the strip's own base level, from its own frames.

    The whole-strip form, kept for the offline paths that have every frame in
    hand at once. A walk uses `edge_bands` and `film_base_from` directly, so
    that a frame it re-prescans replaces its own bands rather than adding more.
    """
    return film_base_from({i: edge_bands(im, model=model)
                           for i, im in enumerate(images)})


@dataclass(frozen=True)
class FrameModel:
    """The frame the strip-level detector places, in units on the hold's scale.

    Three numbers, each a distance across the aperture whatever the
    resolution, so a pass ``width`` columns wide has them in columns as
    ``units / units_per_column_hold(width)``:

    ``frame_units``
        how wide the picture is: converting "the picture ends here" into "the
        picture begins there" goes through it, which is why a reading off the
        right edge carries an assumption the left one does not;
    ``target_units``
        the base each side shows when the frame is centred -- negative when
        the frame is wider than the aperture and overhangs both edges, as
        `tools/frame_edges` centres (`centre.target_base`);
    ``max_gap_units``
        the widest band believed to be a gap between frames. A longer run of
        base is the end of the strip, or a smooth part of a photograph that
        sits at the base level.

    One instance is in force, `FRAME_MODEL`, derived from the measured frame
    (`FRAME_WIDTH_UNITS`) and pitch (`PITCH_UNITS`). A model is an argument
    rather than three module constants so that `tools/roll_registration_history`
    can replay a stored study through these same functions with the 36 mm
    model it was run on, and get the numbers it got then.
    """

    frame_units: float
    target_units: float
    max_gap_units: float
    name: str = ""

    @property
    def max_correction_units(self) -> float:
        """The furthest a correction can be asked for: what `picture_start`
        can report before the widest gap refuses the band, as an offset."""
        return self.max_gap_units - self.target_units

#: How far from an edge a run may begin and still be the gap that entered
#: there, as a fraction of the window. A gap is not flush whenever a sliver of
#: the neighbouring frame is in view beside it.
EDGE_FRACTION = 0.12


def picture_start(
    image: np.ndarray, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    model: FrameModel | None = None,
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
    model = model or FRAME_MODEL
    width = grey.shape[1]
    u_px = aperture_units / width
    smallest = max(1, int(round(GAP_MIN_UNITS / u_px)))
    widest = int(round(model.max_gap_units / u_px))
    margin = int(round(width * EDGE_FRACTION))

    runs = base_runs(image, base.level, flatness=max(base.flatness * 2.0,
                                                     BASE_FLATNESS),
                     min_run=smallest)
    detail: dict[str, Any] = {"runs": runs, "units_per_px": round(u_px, 4)}
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
        # frame 23.1 units away from what every one of its neighbours said and
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
            f"a {length}-column band is "
            f"{say(length * u_px, signed=False)}, wider than the "
            f"{say(model.max_gap_units, signed=False)} a gap can be -- the "
            "end of the strip, or picture at the base level"))
    return start + length, dict(detail, gap=(start, length),
                                gap_units=round(length * u_px, 2))


def picture_end(
    image: np.ndarray, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    model: FrameModel | None = None,
) -> tuple[int | None, dict]:
    """The column after this frame's picture ends, or None.

    The mirror of :func:`picture_start`, and implemented as one: the columns
    are reversed and the same rule applied. Two separate rules would drift
    apart the first time either was tuned.
    """
    start, detail = picture_start(image[:, ::-1], base,
                                  aperture_units=aperture_units, model=model)
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
    image: np.ndarray, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    model: FrameModel | None = None,
) -> tuple[int | None, dict]:
    """Where this frame's picture begins, from whichever edge shows base.

    The left edge answers directly. The right edge answers too, and has to be
    allowed to: **a frame that has gone too far the other way shows no base at
    the left at all**, so a detector that only looks there can say "too far
    along" and never "not far enough". Measured on film 2026-09-21 -- the film
    moved about 19 units the other way, a 12.9-unit band sat plainly at the
    right, and every member abstained on a frame that needed +10.6 units.

    Preferred left, because that reading is direct. The right one is converted
    through the frame width, which is an assumption the left reading does not
    need, so `detail["edge"]` says which answered and a caller that cares can
    weigh them differently.
    """
    model = model or FRAME_MODEL
    start, detail = picture_start(image, base, aperture_units=aperture_units,
                                  model=model)
    if start is not None:
        return start, dict(detail, edge="left")

    end, right = picture_end(image, base, aperture_units=aperture_units,
                             model=model)
    if end is None:
        return None, dict(detail, edge=None,
                          reason=f"left: {detail.get('reason', '')}; "
                                 f"right: {right.get('reason', '')}")
    u_px = aperture_units / image.shape[1]
    return int(round(end - model.frame_units / u_px)), dict(
        right, edge="right", picture_end=end,
        reason=right.get("reason", "") +
        f" (read from the right edge, through a "
        f"{say(model.frame_units, signed=False)} frame)")


# Where a frame's picture should begin is the model's `target_units`: since
# 2026-10-13 the frame's own overhang, -3.05 units -- a frame wider than the
# aperture, centred, shows no base at either edge. It was `TARGET_GAP_UNITS`
# until then, half the slack a 36 mm frame left, +2.3 units of base at the
# left; the 36 mm frame was assumed, the 350.6-unit one measured on 39 pairs
# of prescans of one frame (`FRAME_WIDTH_UNITS`).


def strip_offsets(
    frames, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    target: str = "centre", model: FrameModel | None = None,
) -> tuple[dict[int, float], dict[int, dict]]:
    """What each frame should be moved by, in units, keyed by frame number.

    ``frames`` is ``(number, image)`` pairs for one strip.

    ``target`` is ``"centre"``, which aims every frame at the model's
    `FrameModel.target_units` so the picture sits as squarely in the aperture
    as it can, or ``"median"``, which aims them at the strip's own middle.

    Centre is the default, and the difference is not cosmetic. A whole strip
    can be shifted the same way -- walk A's sixteen frames all carried 13 to
    21 units of gap at the left, and all of them were losing that much picture
    off the right edge. Against the median every one of them reads as
    "nothing to do", because they agree with each other; against the centre
    they read as needing 11 to 19 units, which is what they need. A target
    taken from the frames themselves cannot see an error they all share.

    Median remains for the case centre cannot serve: a strip whose frame width
    is not the model's. It moves outliers to meet the frames that are already
    fine and never asks where the aperture's middle is.

    Sign follows `nudge_units` and `Approved.offset_units`: positive means the
    film moves toward +x. A picture starting further right than the median
    needs a negative offset to come back, which is what the ladder showed --
    commanded +2.6 units moved the picture start +3.2 px.
    """
    model = model or FRAME_MODEL
    measured: dict[int, int] = {}
    details: dict[int, dict] = {}
    u_px = None
    for number, image in frames:
        start, detail = picture_span(image, base,
                                     aperture_units=aperture_units, model=model)
        details[int(number)] = detail
        if "units_per_px" in detail:
            # From the width, not from the detail's figure, which is rounded
            # for the record (as `frame_offset` does): an offset is a
            # distance the transport is asked to move. It took the rounded
            # millimetre figure until 2026-10-13, a difference of a few parts
            # in 100,000; nothing that moves film calls this.
            u_px = aperture_units / image.shape[1]
        if start is not None:
            measured[int(number)] = start

    if not measured or u_px is None:
        return {}, details

    if target == "median":
        want = float(np.median(list(measured.values())))
    else:
        want = model.target_units / u_px
    offsets = {n: (want - start) * u_px for n, start in measured.items()}
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

#: How far the match is searched -- `SEARCH_UNITS`, at the top of this
#: module. Fixed, and **not** derived from what a particular frame needs.
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
#: 11.0 mm since 2026-10-05: 129 columns of a 300 dpi prescan, 104.1 units. It
#: was 9.0 -- 105 columns, 85.1 units -- chosen just over the largest move when
#: a move chained param-8 commands, which left one param-87 command (88.8
#: units, about 110 columns) and the sheet's own largest offset outside it:
#: nine stored looks 107-112 columns out collapsed to `unverified` at
#: confidence 4.6-11.7. At 11.0 every one is found.
#:
#: Why 104.1 and not just enough. A frame arrives where the one before it was
#: left, less the walk's aim at it (sub-frame moves carry over), so a frame set
#: near the sheet's largest offset, 88.84 units, puts the *next* frame that
#: far out too, plus or minus the walk's aim there. At 94.4 units an aim of 6
#: units the other way put it out of sight -- `unverified`, no move -- and
#: every frame after it arrived as far off. 104.1 leaves 15 units for that.
#:
#: Re-fitted with `tools/registration_margin.py --holds` over every stored hold
#: (435 holds, 477 true looks, 9246 pairs). A wider window lifts every
#: confidence, true matches a little more than the nulls (median x1.073
#: against x1.054 from 10.0), so the separation is no worse in proportion;
#: what shrinks is the clearance under a fixed floor, to 8.7 points over the
#: worst realistic null on the film axis (46.3). Expressed as a distance
#: rather than pixels so the figure means the same at any prescan resolution;
#: `reach_px` turns it into columns, for the driver and every tool alike.

#: Correlation z-score below which a match is not believed -- **only
#: comparable at :data:`SEARCH_UNITS`**.
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
#: **Re-measured 2026-10-05 at the new reach, and kept.** `tools/
#: registration_margin.py --holds` paired every stored hold's reference with
#: its own looks (477 true) and with the looks of the *other* frames of its
#: roll -- the realistic null, a strip standing at the wrong frame. Those reach
#: higher than the 2026-09-14 pairs did: **46.3** within the dy gate at this
#: reach (a neighbouring frame of a similar scene, 2026-09-27 frame 3 against
#: frame 4, matched +59 columns away). So the clearance is 8.7 points now, not
#: 24.8, and lowering the floor would spend it: 50 keeps 468 of 477 true looks
#: against 463 here, with 3.7 points left, and 45 lets that null through. The
#: looks this refuses are mostly plain black and white frames at 38-54 (8 of
#: 168) and colour negatives far out, and a refused frame is scanned where it
#: lies and marked, never moved somewhere wrong.
#:
#: **Scored on the part both pictures show since 2026-10-06, and kept.** On
#: the whole pictures the hold refused three true looks of one black and
#: white strip -- 50.3, 41.4 and 27.5 -- and scanned frame 6 84 units from
#: where it was set: grain, and a frame far out showing film its reference
#: never saw (see :func:`measure_shift`, :data:`MATCH_SMOOTH`). Smoothed
#: and scored on what both show, over every stored hold (9306 pairs, 487 true
#: looks), the weakest true look scores **63.5** where it scored 43.4, the
#: weakest 80 columns out or more **73.4** where it scored 27.5, and 486 of
#: the 487 clear 55 -- the other is refused by the off-axis gate -- where 470
#: did. The nulls rise as well: the 2026-09-27 pair above, which is one
#: painting photographed twice, scores 70.5. Every null on the film axis over
#: 40 is a pattern two frames share and is refused by :data:`AGREEMENT_FLOOR`;
#: the strongest the agreement test lets through scores **38.5**, 16.5 under
#: this floor. The three frames refused on 2026-10-06 score 72.3, 81.6 and
#: 74.7 now.
#:
#: **Self-similar frames are the best case, not the worst** -- grass and sky
#: score *highest* (153-192), because phase correlation matches the phase
#: spectrum rather than the look of the texture. The failure mode is a
#: collapse, not a lie: as content fades the score falls smoothly past this
#: floor while the answer is still right, so a wrong-and-confident match has
#: no regime to live in. See `docs/frame-adjustment-findings.md`, and
#: re-run `tools/registration_margin.py` after any change to `register`, the
#: prescan resolution or :data:`SEARCH_UNITS` -- each moves the scale.
CONFIDENCE_FLOOR = 55.0

#: How far off the film axis a match may sit. The transport moves only in x,
#: so a match that claims the picture also moved vertically has found
#: something else. True matches gave 0 to 1; the nulls ranged +-61.
#:
#: 3 since 2026-10-05, from 2. A walk's first prescan sits 0.8-3.1 rows off
#: the frames after it in y, decaying by the third (the carriage warming up),
#: and a pass read the other way starts about 2 rows off: the 2-row gate
#: refused six good matches at dy 3, confidence 70-160, five of them frame 1
#: of a slide roll -- two with an operator's offset that was then never
#: applied. 476 of the 477 stored true looks sit within 3
#: (`tools/registration_margin.py --holds`); the one at dy 5, a colour negative
#: of 2026-09-29, stays refused, and so does the strongest null near the axis,
#: 72.2 at dy 5 -- which is why it is not 5. A gate of 4 would admit nothing
#: more that the record knows of.
MAX_DY_PX = 3

#: The same gate as a fraction of the pass's height, which is what
#: :func:`measure_shift` uses: `MAX_DY_PX` rows of a 300 dpi prescan's 287.
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
#: Rows of a 300 dpi prescan, which is where the figures above were measured.
#: It was `MAX_DY_PX` x 24.3053/287 mm against the window's 24.2993 mm height,
#: a typed copy 0.02% off; the gate is the same for every pass up to 1361 rows
#: high, every prescan included.
#:
#: The one typed copy of that height on this branch. `fix/audit-b6-demo` adds
#: `DEVICE_PASS_SHAPES`, whose 300 dpi row is (287, 428): whichever merges
#: second makes this `DEVICE_PASS_SHAPES[300][0]`, so the gate and the demo
#: cannot come to disagree about one measured pass.
PRESCAN_ROWS_300 = 287
MAX_DY_FRACTION = MAX_DY_PX / PRESCAN_ROWS_300

#: How hard :func:`measure_shift` damps the finest frequencies:
#: `uniformity.register`'s ``smooth``, a Gaussian on the whitened cross-power
#: whose sigma is this fraction of the Nyquist frequency (weight 0.14 there).
#:
#: Phase correlation gives every frequency the same say, and at the top of a
#: 300 dpi prescan two passes of the same grainy frame differ mostly by their
#: own noise -- so on black and white the finest frequencies vote for nothing
#: and dilute the peak. 2026-10-06, frame 4 of a B&W strip: a true match 34
#: columns out scored 50.3 and was refused; damped, 72.3. 0.5 rather than 0.3:
#: both lift the weak, but 0.3 also takes a third off the strong (a median of
#: 119 against 154) by throwing away detail that was carrying them.
MATCH_SMOOTH = 0.5

#: How alike the part both pictures show has to be before a match is
#: believed: :func:`agreement`, the correlation coefficient of the two crops'
#: luminance averaged over :data:`AGREEMENT_BLOCK` -- the test
#: `tools/registration_margin.py` uses as its arbiter of "the same picture".
#:
#: The z-score says a peak is sharp; it cannot say how much of the picture
#: stands behind it. 2026-09-27 frames 3 and 4 are one textured painting
#: photographed twice, a step apart: on the painting they match 59 columns
#: over at 70.5 -- above the floor -- and agree 0.39. Over every stored hold
#: (9306 pairs) the 487 true looks agree **0.982** at worst, and the
#: realistic nulls on the film axis that score over 40 agree 0.77 at most
#: (one scene shot twice, 2026-10-04-150019 frames 4 and 5); the most alike
#: on the axis, 0.87, scores 38.5. 0.85 refuses each of those on its own and
#: leaves the weakest true look 0.13 of room. Off the axis one scene shot
#: twice five rows apart (2026-09-24 4 and 5) scores 70.9 and agrees 0.83:
#: the off-axis gate refuses that one, and the margin here is thin.
AGREEMENT_FLOOR = 0.85

#: The block :func:`agreement` averages both crops over first, in columns of
#: a 300 dpi prescan (`agreement_block` scales it to a pass's width).
#:
#: Pixel for pixel, two passes of grainy film disagree by their own noise:
#: the 487 true looks reach down to 0.875 that way, and a synthetic strip of
#: low-contrast detail under prescan-sized noise to 0.41. Averaged over 4 x 4
#: the noise falls fourfold and the picture stays: 0.982 and 0.88. A pattern
#: two frames share is not helped -- the painting stays at 0.39 -- because
#: what disagrees there is the picture itself.
AGREEMENT_BLOCK = 4


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


def reach_px(width: int, search_units: float | None = None,
             aperture_units: float = APERTURE_UNITS) -> int:
    """`SEARCH_UNITS` in columns of a pass this wide: what `register` is
    given.

    One home, because a confidence is only comparable at the reach it was
    searched at and every tool that re-fits or replays one has to search
    exactly as far as the driver does. Each had its own formula, and they
    disagreed by a pixel -- 106 against the driver's 105 at 300 dpi.
    """
    search = SEARCH_UNITS if search_units is None else search_units
    return int(search / max(aperture_units / max(int(width), 1), 1e-9))


class Match(NamedTuple):
    """What :func:`measure_shift` measures before it judges anything.

    ``dy``/``dx`` are `register`'s, so the picture moved by ``-dx`` columns.
    """

    dy: int
    dx: int
    #: The part both pictures show, correlated again: what the floor judges.
    confidence: float
    #: The whole pictures', at the peak that found the shift.
    whole: float
    #: The whole pictures' read the other way up.
    other: float
    #: Whether the stronger reading was the one with its rows reversed.
    flipped: bool
    #: Where the shared part peaked from the crop's origin, as ``(rows,
    #: columns)`` in the picture's sign: ``(0, 0)`` for a true match.
    shared_px: tuple[int, int]
    #: How many columns both pictures show at the shift.
    shared_columns: int
    #: How alike the shared part is, block for block (:func:`agreement`).
    agreement: float


class Shared(NamedTuple):
    """:func:`score_shared`'s answer, in `register`'s sign."""

    dy: int
    dx: int
    confidence: float
    columns: int
    agreement: float


def agreement(a: np.ndarray, b: np.ndarray, block: int = 1) -> float:
    """The correlation coefficient of two same-sized pictures' luminance,
    each first averaged over ``block`` x ``block`` pixels: 1.0 for the same
    picture whatever the exposure of each, about 0 for unrelated ones. 0.0
    where either is flat."""
    from .uniformity import luminance

    x, y = luminance(a), luminance(b)
    if block > 1:
        h = (x.shape[0] // block) * block
        w = (x.shape[1] // block) * block
        x = x[:h, :w].reshape(h // block, block, w // block, block).mean(
            axis=(1, 3))
        y = y[:h, :w].reshape(h // block, block, w // block, block).mean(
            axis=(1, 3))
    x, y = x.ravel(), y.ravel()
    if x.size < 2 or x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def agreement_block(width: int) -> int:
    """:data:`AGREEMENT_BLOCK` in pixels of a pass this wide."""
    return max(1, int(round(AGREEMENT_BLOCK * width / PRESCAN_COLUMNS)))


def score_shared(reference: np.ndarray, now: np.ndarray, dy: int, dx: int,
                 reach: int, smooth: float | None = MATCH_SMOOTH) -> Shared:
    """Both pictures cropped to what they show in common at `register`'s
    ``(dy, dx)`` (`uniformity.align`), correlated again at the same reach,
    and compared block for block (:func:`agreement`).

    Exposed for the tools that find the shift their own way -- from a cached
    surface, at several reaches -- and must score it as the driver does.
    """
    from .uniformity import align, register

    a, b = align(reference, now, int(dy), int(dx))
    sdy, sdx, confidence = register(a, b, max_shift=reach, smooth=smooth)
    return Shared(dy=int(sdy), dx=int(sdx), confidence=float(confidence),
                  columns=int(b.shape[1]),
                  agreement=agreement(a, b,
                                      agreement_block(reference.shape[1])))


def match_against(reference: np.ndarray, now: np.ndarray, reach: int,
                  smooth: float | None = MATCH_SMOOTH) -> Match:
    """Where ``now`` sits against ``reference``, both ways up, scored on the
    part both show -- :func:`measure_shift`'s measurement without its
    gate, for a pair the same shape. The tools that re-fit the gate call
    this, so they score a pair exactly as a hold does."""
    from .uniformity import register

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
    upright = register(reference, now, max_shift=reach, smooth=smooth)
    flipped = register(reference, now[::-1], max_shift=reach, smooth=smooth)
    turned = flipped[2] > upright[2]
    dy, dx, whole = flipped if turned else upright
    other = (upright if turned else flipped)[2]

    # Believed on what both pictures show. The same reach, so the score is on
    # the scale the floor was fitted on; the crop is at least `w - reach`
    # columns wide, so `register` never has to clamp it in x. A true match
    # peaks at the crop's origin, give or take the pixel the whole picture's
    # peak was rounded to.
    shared = score_shared(reference, now[::-1] if turned else now, dy, dx,
                          reach, smooth)
    return Match(dy=int(dy), dx=int(dx), confidence=shared.confidence,
                 whole=float(whole), other=float(other), flipped=bool(turned),
                 shared_px=(shared.dy, -shared.dx),
                 shared_columns=shared.columns, agreement=shared.agreement)


def measure_shift(
    reference: np.ndarray,
    now: np.ndarray,
    *,
    aperture_units: float = APERTURE_UNITS,
) -> tuple[float | None, dict[str, Any]]:
    """How far the film has moved since ``reference`` was taken, in units.

    Positive means the picture sits further along +x than it did. ``None``
    means the question could not be answered, and the caller must not move on
    a measurement that is not believed -- refusing is the whole reason this
    returns an option rather than a number.

    **The sign is the easiest thing here to invert.** :func:`register` returns
    ``dx`` defined so ``a[i, j]`` matches ``b[i, j - dx]``, so content that has
    moved right by *s* pixels gives ``dx = -s``. Content displacement is
    therefore ``-dx``, verified at every shift from -60 to +60.

    The search reaches :data:`SEARCH_UNITS` whatever this frame is trying to
    do. :func:`register`'s own default of 64 px is 51.6 units at 300 dpi, less than
    the transport can travel, so a frame at the far end would be measured as
    something nearer -- but sizing the reach per frame is worse than leaving
    it too small, because ``confidence`` is measured over the searched window
    and a per-frame reach makes the floor mean a different thing every time.

    This never consults :func:`gap_edges`. That counts gap runs anchored at the
    window's edges, so a frame drifted far enough that the gap sits in the
    middle returns ``(0, 0)`` and the retired ``registration_error`` then
    reported ``0.0, "no gap in view -- registered"`` -- a positive assertion of
    correctness for a badly misregistered frame. Under a loop that would be
    fail-silent rather than fail-safe.

    **Found on the whole picture, believed on the part both share.** The
    shift is the peak of the whole pictures' correlation; the confidence the
    floor judges is that of the two pictures cropped to what they have in
    common at that shift (`uniformity.align`) and correlated again. A frame
    far out shows film the reference never saw -- the next frame, a gap, the
    end of the strip -- and on the whole pictures that unshared part scores
    against the match: 2026-10-06 frame 6, 104 columns out with the strip's
    bright end in view, matched at the right place at 27.5 and was refused, so
    the frame was scanned 84 units from where it was set. On the 324 columns
    both shared it scored 74.7. At no shift the two are the same measurement.
    The record keeps both, ``confidence`` (the shared part's, which is judged)
    and ``confidence_whole``, and says so in ``scored_on``: a record without
    it is from before 2026-10-06, its ``confidence`` the whole picture's,
    unsmoothed.
    """
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
    units_per_px = aperture_units / width
    reach = reach_px(width, aperture_units=aperture_units)
    detail["reach_px"] = reach

    m = match_against(reference, now, reach)
    detail.update(confidence=round(m.confidence, 2), dy=m.dy, dx=m.dx,
                  px=-m.dx, row_reversed=m.flipped,
                  confidence_whole=round(m.whole, 2),
                  confidence_other=round(m.other, 2),
                  shared_columns=m.shared_columns,
                  shared_px=list(m.shared_px),
                  agreement=round(m.agreement, 4),
                  scored_on="shared", smooth=MATCH_SMOOTH)

    if m.confidence < CONFIDENCE_FLOOR:
        detail["reason"] = (f"correlation too weak ({m.confidence:.1f} below "
                            f"{CONFIDENCE_FLOOR:.0f} on the "
                            f"{m.shared_columns} columns both pictures show"
                            + (f"; {m.other:.1f} the other way up)"
                               if m.other else ")"))
        return None, detail
    rows, columns = m.shared_px
    if abs(rows) > 1 or abs(columns) > 1:
        # The shared part does not say what the whole did, and that is not
        # believed whatever it scores.
        detail["reason"] = (f"the part both pictures show matches "
                            f"{columns:+d} columns, {rows:+d} rows from where "
                            "the whole picture did")
        return None, detail
    if m.agreement < AGREEMENT_FLOOR:
        detail["reason"] = (f"a pattern matched, not the picture: where the "
                            f"two overlap they agree {m.agreement:.2f}, below "
                            f"{AGREEMENT_FLOOR:.2f}")
        return None, detail

    # A fraction of the height, not pixels. Every reversed pass measured
    # matched at dy = -2 with **zero** margin against a gate of 2 px -- that
    # offset is the forward-versus-reverse start the byte 14 plan predicts, a
    # distance on the film, so at a 600 dpi prescan it is 4 px and a pixel gate
    # would refuse every rescue silently. Anyone raising `prescan_resolution`
    # would have turned this off without touching it.
    if abs(m.dy) / max(now.shape[0], 1) > MAX_DY_FRACTION:
        # Said in rows of the prescan: across the film, which no transport
        # distance is, so no unit of the transport's says it.
        detail["reason"] = (f"matched {abs(m.dy):g} rows off the film axis, "
                            "which the transport cannot do")
        return None, detail

    detail["reason"] = ("matched with its rows reversed" if m.flipped
                        else "matched")
    return float(-m.dx) * units_per_px, detail


def _resample_to(image: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resize onto ``shape``. Enough for correlation."""
    rows = np.clip((np.arange(shape[0]) * image.shape[0] // max(shape[0], 1)),
                   0, image.shape[0] - 1)
    columns = np.clip((np.arange(shape[1]) * image.shape[1] // max(shape[1], 1)),
                      0, image.shape[1] - 1)
    return np.take(np.take(image, rows, axis=0), columns, axis=1)


# How close counts as arrived: `HOLD_TOLERANCE_UNITS`, at the top of this
# module. **The smallest move the hardware can make** -- one SLIDE command at
# param 1, `protocol.units_for_param(1)`. That value and not a smaller one is
# what makes a limit cycle impossible: the loop can never ask for a correction
# it cannot deliver, so it cannot chatter between two positions either side of
# the target. Built from `protocol`'s law, as the driver's is: it was typed
# out once "because `direct` imports this module", when this module imports
# `protocol` like `direct` does, and a typed copy is what went stale at 2.57
# units once already. A test still pins it to the driver's.
#
# 2.84 units since 2026-09-22, up from 2.57, because the ramp a command pays
# was re-measured at 1.84 units rather than 1.572 and this constant is
# *defined* as what param 1 travels. It has to follow the law or it stops
# being the smallest move, and the no-limit-cycle argument above goes with
# it: the loop would start commanding moves it cannot deliver and chatter
# either side of the target, which is the one failure this number exists to
# prevent.

#: Moves per frame. Each one costs a verification prescan, 15-20 s at 300 dpi,
#: and Stefan allowed about a minute a frame for placing it (2026-10-05) --
#: three. The record says three is plenty: of 435 stored hold records with
#: their moves counted, 305 needed none, 125 one, 4 two and 1 three.
MAX_HOLD_MOVES = 3

#: How far a run of moves the same way must have asked for, while moving the
#: film less than half the smallest move, before the loop calls the transport
#: unresponsive (`no_effect`), in units. Never one command: a silent non-move
#: is about one command in 28 (`docs/frame-adjustment-findings.md`), and a
#: change of direction loses up to about 9 units to the gear train before the
#: film follows (`docs/protocol.md` section 11's ladder; `BACKLASH_UNITS` is the
#: one clean take-up, 3.9). So at least two moves, and the most backlash ever
#: seen plus one smallest move.
NO_EFFECT_UNITS = 12.0

#: How much further from the target a look may land than the look before it
#: before the loop stops believing its own moves (`diverging`), in units. One
#: smallest move: a step's worth of scatter is noise, a step and more after a
#: move toward the target is the film doing something nobody asked for. Its
#: own constant at that value rather than `HOLD_TOLERANCE_UNITS` again, which
#: already has five jobs (TODO.md) and would gain a sixth nobody could see.
DIVERGE_MARGIN_UNITS = 1.0 + COMMAND_UNITS

#: How far a roll's aims may have moved the strip, **net and signed**, before
#: *aiming* stops: 113.5 units (the 12.0 mm it was set at, as a quotient). A
#: sub-frame nudge does not touch the transport's frame counter, so nothing
#: downstream notices the film creeping: fifteen frames each corrected -14
#: units walk the strip 213 units -- most of a frame. `StripWalk` enforces it
#: on a walk's aims, counting from the aims a walk of the same strip already
#: left on it when a roll follows one (`session.walk_aimed_units`).
#:
#: Net, not the sum of every move's size (Stefan's "net-budget" decision,
#: 2026-10-13). Creep is one-way, and that is what the limit is for; the
#: abs sum also spent itself on centring that goes both ways -- an edge-reader
#: walk of black and white film summed 63.5 units for a net of -3.8 -- and
#: would have stopped it. The two walks that ever hit it, 2026-09-27 and
#: 2026-10-03, were monotone (net -92.5 and -77.4), so a net bound still
#: catches the case it fired on.
#:
#: A roll held to approved positions needs no such sum. Every held frame is
#: driven to its own walk reference plus the operator's offset, so where the
#: film ends up is set by what the walk saw, frame by frame, and cannot creep:
#: whatever one frame's hold leaves behind, the next frame's hold measures and
#: removes -- as long as the next frame arrives within `SEARCH_UNITS`. One that
#: arrives further out (a frame set near the sheet's largest offset, then a
#: large walk aim the other way at the next) cannot be measured, is scanned
#: where it lies and marked, and the frames after it arrive as far off until
#: one comes back within reach; 104.1 units leave 15 past the sheet's
#: largest offset for that.
#:
#: Set well above what a real roll needs. Walk A wanted about 14 units on its
#: first frame and the per-advance drift after that, so a roll that reaches
#: this has found something other than the gap.
ROLL_NET_LIMIT_UNITS = 12.0 / MM_PER_UNIT


def hold_plan(
    *,
    target_units: float,
    looks: Sequence[float | None],
    moves: Sequence[float] = (),
) -> tuple[float | None, str]:
    """Whether to move, how far, and if not why not.

    ``target_units`` is the operator's number and is a constant of the frame: this
    only ever computes a residual against it, so his decision cannot be
    overwritten by a measurement. ``looks`` is where each look at this frame
    found the film relative to the same reference -- the first before any
    move, then one after each -- with ``None`` for one that could not be
    believed. ``moves`` is every move sent, signed, at the distance the law
    says it travels. Every distance is in units, as `measure_shift` and
    `nudge_units` speak.

    Returns ``(units, outcome)``. A distance means move that far;
    ``None`` means stop, and the outcome says why. The frame is scanned either
    way -- nothing here refuses to produce a picture. First rule that applies:

    =================  ====================================================
    ``unverified``     the last look could not be believed
    ``held``           within one smallest move of the target
    ``no_effect``      two or more moves one way asked for `NO_EFFECT_UNITS`
                       and the film moved less than half a smallest move
    ``diverging``      further from the target than the look before, by more
                       than `DIVERGE_MARGIN_UNITS`
    ``not_converged``  `MAX_HOLD_MOVES` already made
    ``would_reverse``  the move needed would be this frame's second reversal
    ``move``           otherwise: move by the residual
    =================  ====================================================

    **It decides from the residual and nothing else.** It used to allow a
    frame `|target|` plus a fixed headroom of travel and refuse anything more
    with no move at all -- so a frame that *arrived* 29 units off, carried
    there by the walk's own aims, was scanned 29 units off and the hold said
    `budget` (2026-10-05-195753). Nine of 156 delivered frames audited on
    2026-10-05 sat 28 to 50 units out that way, and three such frames in a
    row then switched holding off for the rest of the roll. Where a frame
    arrived from is no reason to leave it there.

    **One reversal is allowed.** It used to be refused outright, on the
    reasoning that backlash releases distance later; but the loop measures
    after every move, so backlash costs a move rather than a wrong position,
    and refusing left an overshoot standing. One reversal and
    `MAX_HOLD_MOVES` still make chatter impossible.
    """
    if not looks or looks[-1] is None:
        return None, "unverified"

    now = looks[-1]
    residual = target_units - now
    if abs(residual) < HOLD_TOLERANCE_UNITS:
        return None, "held"

    run = _last_run(moves)
    if len(run) >= 2:
        # The look before the run's first move. Every look inside a run was
        # believed: an unbelieved one ends the loop before the next move.
        start = looks[len(moves) - len(run)]
        asked = sum(abs(m) for m in run)
        if (start is not None and asked >= NO_EFFECT_UNITS
                and abs(now - start) < (1.0 + COMMAND_UNITS) / 2):
            return None, "no_effect"

    if len(looks) >= 2 and looks[-2] is not None:
        before = abs(target_units - looks[-2])
        if abs(residual) > before + DIVERGE_MARGIN_UNITS:
            return None, "diverging"

    if len(moves) >= MAX_HOLD_MOVES:
        return None, "not_converged"

    if moves and (residual > 0) != (moves[-1] > 0) and _reversals(moves):
        return None, "would_reverse"
    return residual, "move"


def _last_run(moves: Sequence[float]) -> list[float]:
    """The moves since the last change of direction, in order."""
    run: list[float] = []
    for m in reversed(moves):
        if run and (m > 0) != (run[0] > 0):
            break
        run.insert(0, m)
    return run


def _reversals(moves: Sequence[float]) -> int:
    """How many times these moves changed direction."""
    return sum(1 for a, b in zip(moves, moves[1:]) if (a > 0) != (b > 0))


#: What each hold outcome means, for a person at the end of a roll. One
#: phrasing for the window and the tools, rather than one each: a code like
#: "not_converged" is not what anyone needs to read after an hour. `stopped`
#: is kept for records made before 2026-10-05, when a Stop could cut a hold
#: short; `budget` is an aim's now -- the roll's aim travel used up -- and a
#: hold's only on those older records. `move` is what a hold was doing when a
#: pass under it failed: its record goes into the failed frame's marks.
HOLD_SAID = {
    "held": "reached the position",
    "unverified": "could not be verified against your picture",
    "no_effect": "did not move when told to -- the transport may be slipping",
    "diverging": "moved away from the position instead of toward it",
    "not_converged": "did not get close enough in three moves",
    "would_reverse": "overshot twice, and a second reversal was refused",
    "wrong_way": "moved the wrong way -- the direction is inverted",
    "off": "was left alone; holding had been switched off",
    "abandoned": "no longer looked like the frame that was judged",
    "budget": "would have travelled further than allowed",
    "move": "was cut off part way by a pass that failed",
    "stopped": "was interrupted",
}


def say_hold(outcome: str | None) -> str:
    """One hold outcome in words; an unknown one is passed through as it is."""
    return HOLD_SAID.get(outcome or "", outcome or "?")


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
#   * **Agreement is judged in units of travel, never in confidence.** The
#     scores are not on one scale and cannot be put on one: `register` returns
#     a z-score observed from 4 to 203, `film_base` a fraction and a count. A
#     distance means the same thing to all of them. Confidence only breaks
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
#: Fixed as a distance rather than computed per pass, deliberately: "within a
#: pixel" is half as much at 600 dpi and would tighten with nobody saying so,
#: which is the mistake `GAP_MIN_RUN` made.
AGREE_UNITS = HOLD_TOLERANCE_UNITS

# The ensemble had a third member until 2026-10-13, `right_gap_closure`: a
# reading off the right edge, believed only when the picture between the two
# gaps closed to a 36 mm frame within `FRAME_WIDTH_SPREAD_UNITS`. A frame wider
# than the aperture leaves no view with a gap at both edges, so under the
# measured frame it could never answer; it went with the 36 mm model, and
# lives on in `tools/roll_registration_history.py`. The walk's members are the
# left gap and the prior, the whole strip's the left gap and the strip line.
#
# The furthest a correction can be asked for is `MAX_CORRECTION_UNITS`, at
# the end of this module with the frame model it is derived from.


@dataclass(frozen=True)
class Reading:
    """One member's answer, and how far it stands from its own refusal.

    ``margin`` is that distance as a fraction of the member's own threshold, 0
    meaning "only just qualified" and 1 "nowhere near refusing". It is not
    comparable between members in any deeper sense and is never treated as one:
    it breaks ties and does nothing else.

    ``precision`` is how finely this member can answer, in units, and
    unlike ``margin`` it means exactly the same thing to all of them -- which is
    why the agreement test is built on it. A member that reads to the pixel and
    one that predicts to the transport's own repeatability do not have to be
    held to one tolerance, and holding them to one is what made the first
    version of this refuse eleven frames of thirteen.
    """

    units: float | None
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


def frame_offset(
    image: np.ndarray, base: FilmBase, *, aperture_units: float = APERTURE_UNITS,
    model: FrameModel | None = None,
) -> Reading:
    """Where this frame sits, from the gap that entered at the left.

    The one-frame form of `strip_offsets(target="centre")`, and it needs no
    strip: that target is the model's `target_units`, a constant of the frame
    and the aperture rather than of the frames walked, so a forward walk can
    use it from its third frame.

    This is the validated member. Against the displacement ladder it tracked
    the film at gain 1.008 with a residual rms of 0.2 units and placed the
    picture on all seven rungs, including the four where `gap_edges` read 0.
    That validated the reading, not the target: the ladder's frames were
    placed against the 36 mm model's +2.3 units of base, and this aims at the
    measured frame's -3.05 since 2026-10-13.
    """
    model = model or FRAME_MODEL
    start, detail = picture_span(image, base, aperture_units=aperture_units,
                                 model=model)
    edge = detail.get("edge")
    source = "right-gap" if edge == "right" else "left-gap"
    if start is None:
        return Reading(None, 0.0, "left-gap", detail.get("reason", ""), detail)

    # From the width, not from `detail["units_per_px"]`, which is rounded for
    # the manifest. The offset is a distance the transport is asked to move;
    # it should not carry a rounding meant for a log line.
    u_px = aperture_units / image.shape[1]
    gap_start, length = detail["gap"]
    level, flat = _band(image, gap_start, length)
    # How far this band stands from each of the three tests that could have
    # refused it. The smallest is the one that nearly did.
    margin = min(
        1.0 - abs(level - base.level) / max(base.level * BASE_TOLERANCE, 1e-9),
        1.0 - flat / max(base.flatness * 2.0, BASE_FLATNESS),
        1.0 - length * u_px / model.max_gap_units,
    )
    return Reading(
        units=model.target_units - start * u_px,
        margin=_clip01(margin),
        source=source,
        precision=u_px,
        reason=(f"{length} columns of base, "
                f"{say(length * u_px, signed=False)}"
                + (f", read from the right edge through a "
                   f"{say(model.frame_units, signed=False)} frame"
                   if edge == "right" else " at the left edge")),
        detail=dict(detail, band_level=round(level, 2),
                    band_flatness=round(flat, 2)),
    )


def predict_offset(history, target=None, *, least: int = 1) -> Reading:
    """Where the next frame will arrive, from the frames already walked.

    ``history`` is ``(number, arrived, final)`` per frame, in units, **in walk
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
        spread, margin, precision = 0.0, 0.0, HOLD_TOLERANCE_UNITS
    else:
        spread = float(1.4826 * np.median(np.abs(np.array(errors) - step)))
        margin = _clip01(1.0 - spread / HOLD_TOLERANCE_UNITS)
        # How repeatable this transport's advance actually is, measured on this
        # roll -- but never claimed more finely than the sample supports. Two
        # identical readings say the sample is small, not that the advance is
        # perfect, and a floor that ignores this is what made the prior refuse
        # its partner by 0.2 units on frames 8 and 9 of walk D. The floor is a
        # whole step at one advance and decays as evidence arrives.
        precision = max(spread, HOLD_TOLERANCE_UNITS / np.sqrt(len(errors)))
    return Reading(
        units=float(history[-1][2]) + step,
        margin=margin,
        source="prior",
        precision=precision,
        reason=f"{len(errors)} advance(s), {say(step)} each, "
               f"scatter {say(spread, signed=False)}",
        detail={"advances": len(errors), "step_units": round(step, 3),
                "scatter_units": round(spread, 3)},
    )


def combine(
    readings, *, agree_units: float = AGREE_UNITS,
    bound_units: float | None = None,
) -> tuple[float | None, dict]:
    """What the members together say, or ``None`` and why not.

    ``bound_units`` is the furthest reading believed, `MAX_CORRECTION_UNITS`
    unless given: a reading past it is dropped from the vote rather than
    clamped into range, because a clamped reading is a fabricated one.

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

    ``agree_units`` is the ceiling on that sum, not the gate. Two readings further
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
             "units": None if r.units is None else round(r.units, 3),
             "margin": round(r.margin, 3), "reason": r.reason}
            for r in readings
        ]
    }
    bound = MAX_CORRECTION_UNITS if bound_units is None else bound_units
    usable, dropped = [], []
    for reading in readings:
        if reading.units is None:
            continue
        if abs(reading.units) > bound:
            dropped.append(f"{reading.source} {say(reading.units)}")
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
        return min(a.precision + b.precision, agree_units)

    agreed = {
        i for i, a in enumerate(usable)
        for j, b in enumerate(usable)
        if i != j and abs(a.units - b.units) <= gate(a, b)
    }
    if not agreed:
        # The pair nearest to agreeing, which is what the message names. It
        # took `max` over (excess, Reading, Reading) tuples: the pair furthest
        # apart, called "closest", and a tie on the excess compared two
        # Readings, which do not order -- a TypeError outside the roll's
        # per-frame net, ending the roll. A key compares the number alone.
        worst = min(
            ((abs(a.units - b.units) - gate(a, b), a, b)
             for i, a in enumerate(usable)
             for j, b in enumerate(usable) if i < j),
            key=lambda pair: pair[0],
        )
        return None, dict(detail, reason=(
            f"{len(usable)} members measured and none agree: closest are "
            f"{worst[1].source} {say(worst[1].units)} and "
            f"{worst[2].source} {say(worst[2].units)}, "
            f"{say(abs(worst[1].units - worst[2].units), signed=False)} apart "
            f"against {say(gate(worst[1], worst[2]), signed=False)} "
            "allowed"))

    best = max((usable[i] for i in agreed), key=lambda r: r.margin)
    detail.update(agreed=sorted(usable[i].source for i in agreed),
                  chose=best.source, margin=round(best.margin, 3))
    return best.units, detail


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
    #: How far this strip's aims have moved it, signed, in hold units: from
    #: where the roll began, plus what a walk of it left there
    #: (``net_units=`` at construction). See `ROLL_NET_LIMIT_UNITS`.
    net_units: float = 0.0
    aiming: bool = True
    off_reason: str = ""
    misses: int = 0
    #: Something that reads a frame's edges itself -- the window hands in
    #: `tools/frame_edges`' walk reader through `scan_roll(edge_reader=...)`.
    #: With one, `observe` and `judge` ask it instead of calibrating a base
    #: level from the strip; the budget, the misses and the history stay here.
    #: Without one, this is the detector it always was. Duck-typed:
    #: ``observe(number, image) -> dict``, ``judge(number, image) ->
    #: (units | None, detail)``, ``reread(number, image) -> units | None``,
    #: and optionally ``reread_note(number, image) -> (units | None,
    #: detail)``, which the driver files as a hold's second look. Units on
    #: the hold's scale, as everything the driver moves by.
    reader: Any = None
    #: The frame the strip-level detector places, without a ``reader``:
    #: `FRAME_MODEL` unless given. Given only by the study's replay of the
    #: 36 mm model (`tools/roll_registration_history.py`).
    model: FrameModel | None = None

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

        Called once on every frame, the first two included, with its prescan
        as it arrived. In the driver that is `DirectScanner.scan_roll`,
        before any hold or aim moves the film; offline,
        `tools/roll_registration_study.py --ensemble` replays a stored walk's
        prescans through a walk with no reader, in walk order. This said it
        was called again on a frame's replacement prescan; nothing has ever
        done that, and a hold's verification prescans are not observed. With
        a ``reader`` the reader observes, and what it returns is filed as the
        frame's ``base`` mark.

        The base gets more trustworthy as the walk goes on, and it needs to:
        the level is physically a constant -- one lamp, one exposure, one
        shading reference, and a prescan meters nothing -- so anything that
        moves it is the detector finding picture rather than base.
        """
        if self.reader is not None:
            return dict(self.reader.observe(int(number), image))
        self.bands[int(number)] = edge_bands(image, model=self.model)
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
                    reading = frame_offset(self.pending[earlier], self.base,
                                           model=self.model)
                    if reading.units is not None:
                        self.placed[earlier] = reading.units
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
        """What to do about this frame, from every member that can see it.

        With aiming off nothing is read, whichever detector is in charge: the
        answer is None and ``members`` is ``[]``. Otherwise ``members`` is the
        deciding detector's own: `combine`'s list of readings here, and with
        a ``reader`` a dict of each edge-reader member's answers by role
        (`tools/frame_edges`). The frame's ``base`` mark says which: its
        ``reader`` is ``frame_edges`` for the second.
        """
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

        model = self.model or FRAME_MODEL
        left = frame_offset(image, self.base, model=model)
        # The prior is asked before this frame is recorded, never after.
        prior = predict_offset(self.history(number), number)
        decision, detail = combine([left, prior],
                                   bound_units=model.max_correction_units)
        if left.units is not None:
            self.placed[int(number)] = left.units
        detail["base_level"] = round(self.base.level, 2)
        return decision, detail

    def record(self, number: int, settled_units: float | None,
               moved_units: float = 0.0, verified: bool = True) -> None:
        """Where this frame was left, and how far its aim moved the strip.

        ``moved_units`` is signed: what the hold measured the film to have
        moved (its ``final_units``) where it verified, and otherwise the
        signed sum of what was sent, the move in flight included.

        An **unverified** move drops the frame from the history entirely. The
        film has moved and nothing knows how far, so the arrival this frame
        contributed is a stale number -- and a prior built on one bad delta is
        the poison a median cannot fix once a few of them agree.
        """
        if not verified:
            self.placed.pop(int(number), None)
            self.settled.pop(int(number), None)
        elif settled_units is not None:
            self.settled[int(number)] = float(settled_units)
        self.net_units += float(moved_units)
        if abs(self.net_units) > ROLL_NET_LIMIT_UNITS:
            self.stop(
                f"this strip's aims have moved it {say(self.net_units)} "
                f"in all, past the {say(ROLL_NET_LIMIT_UNITS, signed=False)} "
                "a strip should ever need. A sub-frame move does not touch "
                "the frame counter, so nothing downstream would notice the "
                "film creeping")

    def affordable(self, offset_units: float) -> bool:
        """Whether this strip can still take that move: whether its net, signed,
        stays within `ROLL_NET_LIMIT_UNITS` -- so a move back toward where the
        strip began spends nothing."""
        return abs(self.net_units + float(offset_units)) <= ROLL_NET_LIMIT_UNITS


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
        units=slope * float(number) + intercept,
        margin=_clip01(1.0 - scatter / HOLD_TOLERANCE_UNITS),
        source="strip",
        # Never finer than the scatter of the frames it was fitted through,
        # and never finer than the smallest move that scatter could hide.
        precision=max(scatter, HOLD_TOLERANCE_UNITS / np.sqrt(len(others))),
        reason=f"{len(others)} frames, {say(slope)} per frame, "
               f"scatter {say(scatter, signed=False)}",
        detail={"frames": len(others), "slope_units": round(slope, 3),
                "scatter_units": round(scatter, 3)},
    )


def propose_offsets(
    frames, *, aperture_units: float = APERTURE_UNITS, target: str = "centre",
    model: FrameModel | None = None,
) -> tuple[dict[int, float], dict[int, dict]]:
    """What every frame of a walked strip should be moved by, in units, and
    why.

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
    4 each carried a real left-gap reading near -10 units, the members disagreed,
    and the fallback replaced a measurement from the one detector graded on the
    displacement ladder with a line extrapolated from the far end of the strip
    -- and said nothing about having done so. A model may stand in for a
    measurement that does not exist. It may not overrule one that does.
    """
    model = model or FRAME_MODEL
    images = [im for _n, im in frames]
    base, base_detail = film_base(images, model=model)
    notes: dict[int, dict] = {}
    if base is None:
        why = ("the strip's base level could not be calibrated: "
               + str(base_detail.get("reason", "")))
        return {}, {int(n): {"reason": why, "source": "none"} for n, _ in frames}

    # Every frame's own reading first: the leave-one-out fit needs them all
    # before it can judge any one of them.
    placed, _detail = strip_offsets(frames, base, aperture_units=aperture_units,
                                    target=target, model=model)

    bound = model.max_correction_units
    proposals: dict[int, float] = {}
    for number, image in frames:
        number = int(number)
        left = frame_offset(image, base, aperture_units=aperture_units,
                            model=model)
        strip = predict_from_strip(placed, number)
        decision, detail = combine([left, strip], bound_units=bound)
        notes[number] = dict(detail, base_level=round(base.level, 2))
        if decision is not None:
            proposals[number] = decision
            notes[number]["source"] = "measured"
        elif left.units is not None and abs(left.units) <= bound:
            # The reading stands, and the sheet says it was not corroborated.
            # `left-gap` is the member validated against the ladder at gain
            # 1.008 with a residual rms of 0.2 units; the disagreement is worth
            # showing, not worth discarding the measurement over.
            proposals[number] = left.units
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
# param adds. Everything below is floating point; the only rounding is the
# integer the driver puts into the command (`DirectScanner.param_for_units`,
# capped at `MAX_CORRECTION_PARAM`). A second law lived here once --
# `command_for`, with ceilings of 160 and 255 that the driver's cap exists
# because nothing can verify -- called by nothing, and a trap for whoever
# reached for it first.
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
#: single commands only, so nothing in it could see a per-command term. The
#: driver's own term, `protocol.COMMAND_UNITS`, not a second 1.84: the two
#: were typed separately and nothing held them equal, so a re-measured ramp
#: would have moved the mover and left the detector's deadband behind.
COMMAND_COST = COMMAND_UNITS

#: The shape a whole-frame pass comes back as, ``{dpi: (lines, columns)}``:
#: what `DirectScanner.scan` hands over, so at 7200 dpi after the stagger
#: realignment. One table, because the widths are the device's own rounding
#: and not a multiple of the dpi -- 860 at 600, where twice 428 is 856 -- so a
#: width worked out from a resolution is a guess, and a guess at 600 dpi was
#: what put the demo's walks through the frame-edge reader where the device's
#: never go (FE-A1, Stefan's decision 2026-10-13: measured widths, one table,
#: and a resolution it cannot size refused by the demo's stand-in).
#:
#: Each row is the commonest shape among the library's whole-frame entries at
#: that resolution, demo passes left out (counted 2026-10-13), except where
#: said:
#:
#: - 300: 1,355 entries (1,345 RGB, 10 RGBI). 37 more came back a line short,
#:   286 x 428; the width has never varied.
#: - 600: 109 of 109. 862 has been measured too: the vignette study's 600
#:   dpi passes, held in another machine's library, were 862 wide against a
#:   CCD mask of 860 used pixels (`uniformity.register`'s ``trailing``). No
#:   entry here is, and the demo answers the commoner 860.
#: - 900: no entry in this library or any sibling's. From five entries since
#:   gone, recorded in `docs/tiff-compression-plan.md`.
#: - 1200: one entry. Added at Stefan's decision because the window offers it.
#: - 1800: 23 entries.
#: - 3600: 536 entries.
#: - 7200: **documented, not measured** -- no 7200 dpi entry exists. Added at
#:   Stefan's decision so the demo answers a raw 7200 dpi pass as the window
#:   can ask for one, and in his words: "10344 x 6887 less 4 rows", 6883. Two
#:   readings of the README stand behind it. Its own table said 6884, the
#:   6888 lines of the transport window less the realignment's four
#:   (`DirectScanner.NATIVE_COLUMN_STAGGER_LINES`) -- but the same table said
#:   1722 at 1800 dpi and 3444 at 3600, each 6888 scaled, where every entry
#:   here is a line short of that, 1721 and 3443, as 900 dpi's 860 is of 861.
#:   7200 is on that ladder (900, 1800, 3600), so the device would read 6887
#:   and the realignment leave 6883, which is the decision. 300, 600 and 1200
#:   are exact (287, 574, 1148), so the pattern is not universal: one 7200
#:   dpi pass settles it.
#:
#: The demo sizes its passes from this where its library has no whole-frame
#: entry at the resolution (`DemoScanner._fit`), and refuses one it has no
#: row for. Nothing sent to the scanner reads it.
DEVICE_PASS_SHAPES: dict[int, tuple[int, int]] = {
    300: (287, 428),
    600: (574, 860),
    900: (860, 1292),
    1200: (1148, 1724),
    1800: (1721, 2584),
    3600: (3443, 5172),
    7200: (6883, 10344),
}


def device_shape(dpi: int | None) -> tuple[int, int] | None:
    """``(lines, columns)`` of a whole-frame pass at ``dpi``, or None.

    None where nothing has sized that resolution: a width is never derived
    from the dpi (`DEVICE_PASS_SHAPES`).
    """
    if dpi is None:
        return None
    return DEVICE_PASS_SHAPES.get(int(dpi))


def dpi_of_width(columns: int) -> int | None:
    """The resolution whose whole-frame pass is ``columns`` wide, or None.

    For a stored picture that does not say its resolution, such as a kept
    `prescan.tif`: a width is the device's answer to one resolution only.
    """
    for dpi, (_, width) in DEVICE_PASS_SHAPES.items():
        if width == int(columns):
            return dpi
    return None


#: The width a 300 dpi prescan comes back as. Columns scale with it. The
#: table's 300 dpi row, not a second 428: everything under
#: `tools/frame_edges` scales from this, so it must stay exactly 428.0.
PRESCAN_COLUMNS = float(DEVICE_PASS_SHAPES[300][1])

#: Gap to gap along the strip, in units: tracked across twelve commands and
#: four gaps entering and leaving on walk K (455, 454, 455 px; see
#: `docs/frame-adjustment-findings.md`). The frame-edge detector's gap model
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

#: Where a scan's window falls in a 300 dpi prescan's columns, ``(left,
#: right)`` as column boundaries: ``left`` is negative where the scan reaches
#: past the prescan's left edge, and ``right`` is `PRESCAN_COLUMNS` plus what
#: it reaches past the right. The prescan comes back 428 columns wide; every
#: scan from 600 to 3600 dpi covers more of the transport than that, about
#: 431 of the prescan's columns, so the edges of what a scan takes are these
#: and not the prescan's own.
#:
#: Medians over the 36 prescan/scan pairs at 600-3600 dpi whose registrations
#: `research/frame-edge/anchors.py` stored (`--window`): 0.31 column past the
#: left edge (-0.30 to 1.48 over the pairs) and 2.48 past the right (1.30 to
#: 3.85). One constant rather than a table by resolution and read direction,
#: on purpose. Per resolution the medians sit within 0.4 column of these but
#: for 600 dpi's right edge, 0.7 short -- the device returns 860 columns there,
#: 430 of a prescan's, where 900-3600 dpi return 430.7-431 -- and a scan read
#: the other way round from its prescan sits about half a column right of one
#: read the same way. Half a column is about one screen pixel in the frame-
#: position window, which shows the prescan at 2x, and which way a scan will
#: read is not known when the guide is drawn.
SCAN_WINDOW_COLUMNS = (-0.31, 430.48)


def scan_window(width: int) -> tuple[float, float]:
    """`SCAN_WINDOW_COLUMNS` in columns of a prescan this wide.

    Scaled by ``width / PRESCAN_COLUMNS``, as `units_per_column` scales a
    column. Exact for the 300 dpi prescan every walk takes; a 600 or 900 dpi
    prescan is not a whole multiple of 428 columns wide, and there this is out
    by a column or two of the 300 dpi prescan's.
    """
    scale = float(width) / PRESCAN_COLUMNS
    left, right = SCAN_WINDOW_COLUMNS
    return left * scale, right * scale


def units_per_column(width: int) -> float:
    """One column of a pass this wide, in param units.

    Replaces the aperture over ``width``, which is wrong by 0.7%: with the true pitch
    the geometry of a whole roll closes to -0.35 units and with that expression
    to +1.5. A wider pass has proportionally narrower columns, so the
    resolution never has to be passed in.

    This is the reader's scale (`COLUMNS_PER_UNIT`), the one the frame-edge
    geometry was measured on. What the hold measures and moves by is
    `units_per_column_hold`, 0.2% apart.
    """
    return (PRESCAN_COLUMNS / float(width or 1)) / COLUMNS_PER_UNIT


def columns_per_unit(width: int) -> float:
    """The inverse, for turning a measured distance back into columns."""
    return 1.0 / units_per_column(width)


#: The smallest distance a single command can deliver: `param 1` plus the cost
#: of issuing it. Nothing between zero and this exists, which is what makes a
#: deadband unavoidable rather than a choice.
SMALLEST_MOVE = 1.0 + COMMAND_COST


def units_per_column_hold(width: int) -> float:
    """One column of a pass this wide, in units on **the hold's scale**.

    The aperture over the width: the scale `measure_shift` measures a move in
    and every stored offset is on, 345.2 units across whatever the
    resolution. `units_per_column` is the reader's, 0.2% apart; a quantity
    measured on that one comes across through columns
    (`reader_columns_to_hold_units`), never by relabelling it.
    """
    return APERTURE_UNITS / float(width or 1)


def reader_columns_to_hold_units(columns: float, width: int) -> float:
    """``columns`` of a pass ``width`` wide, in hold units.

    The one bridge from the reader's geometry to what the hold moves by: a
    frame `FRAME_WIDTH_UNITS` wide is ``FRAME_WIDTH_UNITS * COLUMNS_PER_UNIT``
    columns of a 300 dpi prescan, and that many columns are 351.3 hold units,
    not 350.6. Taking the reader's number as hold units would move a centred
    frame by the difference.
    """
    return float(columns) * units_per_column_hold(width)


def units_of(record: Any, name: str) -> float | None:
    """The distance ``name`` from a stored record, in units, or None.

    Read both: ``<name>_units`` as written since 2026-10-13, else
    ``<name>_mm`` from a file written before it, converted -- old files are
    read, never rewritten. None where the record holds neither, or holds
    something that is no finite number: a missing reading is not a zero.
    """
    if not isinstance(record, dict):
        return None
    for key, convert in ((f"{name}_units", float), (f"{name}_mm", to_units)):
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = convert(float(value))
        except (TypeError, ValueError):
            return None
        return number if np.isfinite(number) else None
    return None


# --- the frame model --------------------------------------------------------
#
# One frame, measured: `FRAME_WIDTH_UNITS` wide at `PITCH_UNITS` gap to gap,
# both on the reader's scale and both in columns of a 300 dpi prescan first --
# which is where they were measured -- and only then in hold units, through
# `reader_columns_to_hold_units`. Here at the end because those constants are
# defined above it; every function that places a frame takes it at call time.
#
# Until 2026-10-13 the strip-level detector placed the 36 mm model's frame,
# 340.6 units in the 345.2-unit aperture -- assumed rather than measured, 4.6
# units of slack, a target of +2.3 units of base at the left and gaps
# believed up to 24.6 units. Stefan retired it fully (the "36mm-model" decision):
# slides and Kodachrome are aimed by this frame too. **The frame was measured
# on negatives and has never been checked on a slide here**: every positive
# walk stored (nine walks of five frames) left the base unarmed on all 45
# frames, so this model has not placed a positive frame and neither had the
# old one. A scanner check on a slide roll is proposed for Stefan to OK.


#: What the window's Roll question and `tools/scan_roll.py` say before a roll
#: aims a film the frame-edge reader does not read (a positive, Kodachrome):
#: said, never refused or greyed -- the aim may still be what he wants, and
#: an unarmed base leaves every frame as it came.
NO_READER_FRAME_NOTE = (
    "This film is aimed without the frame-edge reader: by the strip's base "
    f"level and a {FRAME_WIDTH_UNITS:.1f}-unit frame. That frame was measured "
    "on negatives and has never been checked on a slide here, and on the 45 "
    "positive frames walked so far the base never armed, so expect frames to "
    "be left as they came.")


def frame_columns(width: float = PRESCAN_COLUMNS) -> float:
    """The frame's width in columns of a pass ``width`` columns wide: 435.6 at
    428. The same number as `tools/frame_edges` centre.frame_columns, which
    this module cannot import."""
    return FRAME_WIDTH_UNITS / units_per_column(int(width))


def target_base_columns(width: float = PRESCAN_COLUMNS) -> float:
    """Columns of base each side shows when the frame is centred: -3.78 at 428,
    so a centred frame overhangs both edges. `centre.target_base`'s ``t``."""
    return (float(width) - frame_columns(width)) / 2.0


#: The widest band believed to be the gap between two frames, in reader
#: units: the pitch less the frame -- 15.9 units of unexposed film between
#: two pictures -- plus one smallest move, 18.7 in all (Stefan's "gap-bound"
#: decision, 2026-10-13). The move is room for the reading's own column of
#: scatter and for a second camera's narrower frame (432.7 columns against
#: 435.6 measured on 2 pairs), whose gap would otherwise be refused.
MAX_GAP_READER_UNITS = PITCH_UNITS - FRAME_WIDTH_UNITS + SMALLEST_MOVE


def max_gap_columns(width: float = PRESCAN_COLUMNS) -> float:
    """`MAX_GAP_READER_UNITS` in columns of a pass this wide: 23.3 at 428."""
    return MAX_GAP_READER_UNITS / units_per_column(int(width))


def _hold_units(columns: float) -> float:
    """Columns of a 300 dpi prescan, in hold units -- which is the same
    distance at any width, since both scales are a fixed fraction of it."""
    return reader_columns_to_hold_units(columns, int(PRESCAN_COLUMNS))


#: The frame model in force, in hold units: a 351.3-unit frame (350.6 on the
#: reader's scale), centred at -3.05 units of base each side, gaps up to 18.8.
FRAME_MODEL = FrameModel(
    frame_units=_hold_units(frame_columns()),
    target_units=_hold_units(target_base_columns()),
    max_gap_units=_hold_units(max_gap_columns()),
    name="measured frame",
)

#: The widest band believed to be a gap, in hold units: 18.8 since
#: 2026-10-13, 24.6 under the 36 mm model.
MAX_GAP_UNITS = FRAME_MODEL.max_gap_units

#: The furthest a correction can be asked for, in hold units: what
#: `picture_start` can report before `MAX_GAP_UNITS` refuses the band,
#: restated as an offset from the target -- 21.8 units, 22.3 under the 36 mm
#: model. Not a new limit. A reading past it is dropped from the vote rather
#: than clamped into range, because a clamped reading is a fabricated one.
#: One bound for both readers: `combine` here, and a hold's second look
#: (`direct._SecondLook`) whether `tools/frame_edges` or this module read the
#: frame. It is how badly the transport can leave a frame; walk A sat 13 to
#: 21 units out.
MAX_CORRECTION_UNITS = FRAME_MODEL.max_correction_units
