"""Finding where a frame sits, from the gap's own brightness.

The detectors this sits beside key on something relative to the frame's own
content, and both fail in the direction that matters: the worse a frame is
placed, the more gap is in view, the higher the frame's own median climbs, and
the less they see. Measured on a ladder with known offsets, `gap_edges` read
5, 8, then 0, 0, 0, 0, 0 while the gap widened from 7 to 26 px.

These hold the replacement to the two properties that buys: it keys on an
absolute level, so it does not care what the photograph looks like; and it
abstains with a reason rather than returning a number it cannot support.
"""
import numpy as np
import pytest

from rps7200.framing import (
    APERTURE_MM,
    BASE_SPREAD_LIMIT,
    MAX_GAP_MM,
    FilmBase,
    base_runs,
    fill_from_neighbours,
    film_base,
    picture_start,
    strip_offsets,
)

WIDTH, HEIGHT = 428, 60
BASE_LEVEL = 37.0


def frame(gap_px: int = 18, *, before: int = 0, seed: int = 0,
          base: float = BASE_LEVEL, width: int = WIDTH) -> np.ndarray:
    """One prescan: an optional sliver of the neighbour, a gap, then picture.

    The picture varies down each column -- that is what separates it from
    base, and keying on it is what the graveyard did.
    """
    rng = np.random.default_rng(seed)
    out = rng.random((HEIGHT, width, 3)) * 90 + 15
    if gap_px:
        band = slice(before, before + gap_px)
        # Base is flat down the column: one object, one lamp, one exposure.
        out[:, band] = base + rng.random((HEIGHT, gap_px, 3)) * 0.6
    return out


def calibrated(level: float = BASE_LEVEL) -> FilmBase:
    return FilmBase(level=level, flatness=1.0, bands=16, spread=0.007)


# -- calibrating the strip --------------------------------------------------


def test_the_base_level_is_found_without_assuming_one():
    """It is measuring what the level is, so it must not start from one."""
    strip = [frame(gap_px=18, seed=i) for i in range(8)]
    base, detail = film_base(strip)
    assert base is not None, detail
    assert base.level == pytest.approx(BASE_LEVEL, abs=1.5)
    assert base.spread < BASE_SPREAD_LIMIT


def test_one_contaminated_frame_does_not_move_the_level():
    """Finding a band by flatness alone also finds smooth picture -- one real
    strip had a 104-column flat run that was sky. The median must survive it,
    which is why the spread is a MAD and not a range: with a range, one such
    frame took the measured spread to 83.7% and the calibration refused a
    strip it should have accepted."""
    strip = [frame(gap_px=18, seed=i) for i in range(7)]
    strip.append(frame(gap_px=120, seed=99, base=150.0))
    base, detail = film_base(strip)
    assert base is not None, detail
    assert base.level == pytest.approx(BASE_LEVEL, abs=1.5)


def test_a_strip_with_nothing_flat_in_it_refuses():
    strip = [frame(gap_px=0, seed=i) for i in range(5)]
    base, detail = film_base(strip)
    assert base is None
    assert detail["reason"]


# -- placing one frame ------------------------------------------------------


def test_the_picture_start_is_where_the_gap_ends():
    start, detail = picture_start(frame(gap_px=18), calibrated())
    assert start == pytest.approx(18, abs=1), detail


def test_a_gap_that_is_not_flush_with_the_edge_is_still_found():
    """`gap_edges` requires the run to begin at column 0 exactly, so a gap
    with a sliver of the neighbouring frame beside it reads as no gap at all
    -- and `registration_error_mm` then asserts the frame is registered. Four
    of nine such calls on a real sixteen-frame walk were provably false."""
    start, detail = picture_start(frame(gap_px=15, before=5), calibrated())
    assert start == pytest.approx(20, abs=1), detail


def test_a_band_wider_than_a_gap_is_refused():
    """About two millimetres of gap exists on 135 film. A 104-column band is
    8.9 mm and is the end of the strip or picture at the base level -- on a
    real walk it was a smooth sky, and taking it for a gap gave 8.87 mm."""
    start, detail = picture_start(frame(gap_px=140), calibrated())
    assert start is None
    assert "wider than" in detail["reason"]
    assert str(MAX_GAP_MM) in detail["reason"]


def test_base_only_in_the_middle_is_refused():
    """Film cannot show base between two halves of one photograph, so a band
    there is the test firing on the picture."""
    image = frame(gap_px=0, seed=3)
    image[:, 200:216] = BASE_LEVEL
    start, detail = picture_start(image, calibrated())
    assert start is None
    assert "picture, not a gap" in detail["reason"]


def test_a_frame_with_no_base_in_view_abstains():
    """The channel `registration_error_mm` does not have. It returns
    `(0.0, "no gap in view -- registered")` for this case -- a positive
    assertion of correctness about a frame it could not see."""
    start, detail = picture_start(frame(gap_px=0), calibrated())
    assert start is None
    assert "no unexposed base" in detail["reason"]


def test_the_detector_is_resolution_independent():
    """`GAP_MIN_RUN` is in pixels, so its deadband is 0.25 mm at 300 dpi and
    0.13 mm at 600 with nobody saying so. Everything here is in millimetres,
    for the reason `SEARCH_MM` and `HOLD_TOLERANCE_MM` already are."""
    narrow, _ = picture_start(frame(gap_px=18, width=428), calibrated())
    wide, _ = picture_start(frame(gap_px=36, width=856), calibrated())
    assert narrow is not None and wide is not None
    assert narrow * (APERTURE_MM / 428) == pytest.approx(
        wide * (APERTURE_MM / 856), abs=0.06)


# -- the strip, and the sign ------------------------------------------------


def test_a_whole_strip_shifted_the_same_way_is_still_corrected():
    """The reason the target is the aperture and not the strip's own middle.

    Walk A's sixteen frames all carried 1.4 to 2.2 mm of gap at the left and
    all of them were losing that much picture off the right edge -- confirmed
    by the right-hand columns being picture, spread 3.7 to 11.6 counts where
    base shows 1. Against the median every one read as "nothing to do",
    because they agree with each other. A target taken from the frames
    themselves cannot see an error they all share.
    """
    frames = [(n, frame(gap_px=18, seed=n)) for n in range(1, 7)]
    offsets, _detail = strip_offsets(frames, calibrated())
    assert all(v < -1.0 for v in offsets.values()), offsets

    against_median = strip_offsets(frames, calibrated(), target="median")[0]
    assert all(abs(v) < 0.05 for v in against_median.values()), against_median


def test_the_median_target_moves_outliers_to_meet_the_rest():
    """Still there for a strip whose frame is not the 36 mm the centre target
    assumes."""
    frames = [(n, frame(gap_px=18, seed=n)) for n in range(1, 6)]
    frames.append((6, frame(gap_px=28, seed=6)))
    offsets, _detail = strip_offsets(frames, calibrated(), target="median")
    assert offsets[1] == pytest.approx(0.0, abs=0.03)
    assert offsets[6] < -0.5, offsets


def test_a_picture_further_right_needs_a_negative_offset():
    """The sign `nudge` and `Approved.offset_mm` use: positive moves the film
    toward +x. The ladder showed commanded +0.27 mm moving the picture start
    +3.2 px, so a start past the median comes back with a negative one.
    Getting this backwards drives every frame of a roll the wrong way."""
    frames = [(1, frame(gap_px=18, seed=1)), (2, frame(gap_px=18, seed=2)),
              (3, frame(gap_px=28, seed=3))]
    offsets, _detail = strip_offsets(frames, calibrated())
    assert offsets[3] < 0, offsets


def test_a_frame_it_cannot_place_is_absent_rather_than_zero():
    frames = [(n, frame(gap_px=18, seed=n)) for n in (1, 2, 3)]
    frames.append((4, frame(gap_px=0, seed=4)))
    offsets, detail = strip_offsets(frames, calibrated())
    assert 4 not in offsets
    assert detail[4]["reason"]


# -- filling the gaps -------------------------------------------------------


def test_neighbours_fill_a_frame_the_detector_could_not_place():
    filled = fill_from_neighbours({1: 0.0, 2: -0.1, 3: -0.2, 5: -0.4},
                                  range(1, 6))
    assert set(filled) == {4}
    assert filled[4] == pytest.approx(-0.3, abs=0.05)


def test_one_bad_placement_does_not_tilt_the_fit():
    """Theil-Sen, not least squares. A bad placement is exactly what this
    exists to survive, and least squares would let one drag the whole line."""
    placed = {n: -0.1 * n for n in (1, 2, 3, 4, 5, 6, 7)}
    placed[4] = 9.0                                  # one wild reading
    filled = fill_from_neighbours(placed, [8])
    assert filled[8] == pytest.approx(-0.8, abs=0.2), filled


def test_fewer_than_three_placed_frames_fills_nothing():
    """A line through two points that disagree is a confident answer built on
    nothing."""
    assert fill_from_neighbours({1: 0.0, 2: -0.1}, range(1, 6)) == {}


# -- the primitive ----------------------------------------------------------


def test_base_runs_finds_a_band_that_is_not_at_an_edge():
    image = frame(gap_px=12, before=4)
    runs = base_runs(image, BASE_LEVEL, min_run=3)
    assert runs, "the gap itself must be found"
    assert any(start >= 3 for start, _n in runs), runs


# -- the old detector, made to abstain ---------------------------------------


def test_the_old_detector_no_longer_asserts_a_frame_it_cannot_see():
    """It used to answer `(0.0, "no gap in view -- registered")`, which is a
    positive claim of correctness about a frame it had not located -- and
    nothing downstream could tell that from a measurement.

    Strictly safety-improving: it can only turn wrong assertions into
    abstentions. Nothing pinned the old return, which is why it survived.
    """
    from rps7200.framing import registration_error_mm

    mm, why = registration_error_mm(frame(gap_px=0, seed=5))
    assert mm is None
    assert "cannot see where the frame is" in why
