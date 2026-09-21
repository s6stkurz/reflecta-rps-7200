"""Deciding a frame's position from more than one detector.

The four detectors in `docs/whole-roll-plan.md`'s graveyard each produced a
confident number and each was wrong on some frames, and every one was caught
only by looking at the picture. What they had in common was not a bad
threshold: it was that one detector's word was enough to move film.

These pin the rules that replace that. Two members must agree before anything
moves; agreement is judged in millimetres because the members' confidences are
not on one scale and cannot be put on one; and where an agreeing pair differs,
the more trusted member's own number is taken rather than an average of the
two, which would be a number neither of them measured.
"""
import numpy as np
import pytest

from rps7200.framing import (
    AGREE_MM,
    APERTURE_MM,
    FRAME_WIDTH_SPREAD_MM,
    HOLD_TOLERANCE_MM,
    MAX_CORRECTION_MM,
    FilmBase,
    Reading,
    combine,
    frame_offset_mm,
    predict_offset,
    right_gap_closure,
)

WIDTH, HEIGHT = 428, 60
BASE_LEVEL = 37.0
MM_PX = APERTURE_MM / WIDTH


def frame(gap_px: int = 18, *, before: int = 0, seed: int = 0,
          right_px: int = 0, base: float = BASE_LEVEL) -> np.ndarray:
    """One prescan: an optional gap, picture, and an optional right gap."""
    rng = np.random.default_rng(seed)
    out = rng.random((HEIGHT, WIDTH, 3)) * 90 + 15
    if gap_px:
        out[:, before:before + gap_px] = (
            base + rng.random((HEIGHT, gap_px, 3)) * 0.6)
    if right_px:
        out[:, WIDTH - right_px:] = (
            base + rng.random((HEIGHT, right_px, 3)) * 0.6)
    return out


def calibrated(level: float = BASE_LEVEL) -> FilmBase:
    return FilmBase(level=level, flatness=1.0, bands=16, spread=0.007)


def reading(mm, trust, source="left-gap", precision=MM_PX) -> Reading:
    return Reading(mm=mm, margin=trust, source=source, precision=precision)


# -- the gate ---------------------------------------------------------------


def test_one_member_alone_moves_nothing():
    """The whole gate. `gap_edges` returned a confident number on its own and
    was provably wrong on four of nine frames of a real walk."""
    decision, detail = combine([reading(1.0, 0.9)])
    assert decision is None
    assert "two that agree" in detail["reason"]


def test_two_members_that_agree_decide():
    decision, detail = combine([reading(-0.61, 0.8),
                                reading(-0.65, 0.4, "prior", 0.1)])
    assert decision == pytest.approx(-0.61)
    assert detail["chose"] == "left-gap"


def test_two_members_that_disagree_move_nothing():
    decision, detail = combine([reading(-0.61, 0.8), reading(+1.20, 0.9)])
    assert decision is None
    assert "none agree" in detail["reason"]
    # and it must say how far apart they were, not merely that they were
    assert "1.81 mm apart" in detail["reason"]


def test_an_agreeing_pair_is_never_averaged():
    """`bracket.py:355` settled this for exposures: where sources conflict, the
    single most-trusted one wins and nothing is blended. The mean of 0.30 and
    0.45 is 0.375, a distance neither detector measured."""
    decision, _d = combine([reading(0.30, 0.9), reading(0.45, 0.4, "prior")])
    assert decision == pytest.approx(0.30)


def test_the_least_trusted_of_an_agreeing_pair_never_sets_the_number():
    decision, detail = combine([reading(0.30, 0.2), reading(0.45, 0.95, "prior")])
    assert decision == pytest.approx(0.45)
    assert detail["chose"] == "prior"


# -- what agreement means ---------------------------------------------------


def test_the_gate_is_the_pairs_own_precisions_not_one_tolerance():
    """The finding that made this work. Held to a flat one-pixel gate the
    ensemble refused eleven frames of thirteen on walk D -- because the left
    gap reads to a quarter pixel against the ladder while the advance itself
    wanders +-2 px, so the prior was being asked to be eight times more
    repeatable than the mechanism it describes."""
    apart = 2.4 * MM_PX                           # just over two pixels
    precise = [reading(0.0, 0.5), reading(apart, 0.5, "closure")]
    assert combine(precise)[0] is None, "two pixel readings must stay strict"

    mixed = [reading(0.0, 0.5), reading(apart, 0.5, "prior", precision=0.12)]
    assert combine(mixed)[0] is not None, "a prior answers to its own precision"


def test_agreement_is_capped_at_the_smallest_move_the_hardware_can_make():
    """Two readings further apart than one step would command different moves,
    so calling them agreed is meaningless however imprecise either admits to
    being. Without the cap a member could agree with anything by claiming to
    be bad enough."""
    assert AGREE_MM == HOLD_TOLERANCE_MM
    sloppy = [reading(0.0, 0.5, "prior", precision=99.0),
              reading(AGREE_MM * 2, 0.5, "left-gap", precision=99.0)]
    assert combine(sloppy)[0] is None


def test_the_gate_clears_the_spread_the_frame_width_itself_has():
    """A left reading aims at `TARGET_GAP_MM` and a right one at the aperture
    less the same; the two agree exactly only for a frame of exactly 36.000 mm.
    The measured spread is 35.90 to 36.2, so a gate below it would call two
    readings that are both right a disagreement."""
    assert AGREE_MM > FRAME_WIDTH_SPREAD_MM / 2


# -- the physical bound -----------------------------------------------------


def test_a_reading_past_the_bound_is_dropped_and_not_clamped():
    """`picture_start` accepts a run beginning anywhere within `EDGE_FRACTION`
    of the edge, so it can report far more than a gap's width -- the bound is
    not implied by `MAX_GAP_MM` and has to be applied here. A clamped reading
    would be a fabricated one."""
    wild = reading(MAX_CORRECTION_MM + 1.0, 0.9)
    decision, detail = combine([wild, reading(0.30, 0.4, "prior")])
    assert decision is None
    assert detail["dropped"], "the reading must be named, not silently ignored"
    assert detail["members"][0]["mm"] == pytest.approx(
        MAX_CORRECTION_MM + 1.0, abs=1e-4)


def test_the_bound_is_not_max_registration_mm():
    """0.49 mm is how well a frame can be placed, not how badly the transport
    can leave one. Walk A sat 1.4 to 2.2 mm out and needed every millimetre."""
    assert MAX_CORRECTION_MM > 2.0


# -- the members ------------------------------------------------------------


def test_the_left_gap_places_a_frame_and_reports_its_precision():
    r = frame_offset_mm(frame(gap_px=18), calibrated())
    assert r.mm is not None
    assert r.precision == pytest.approx(MM_PX), "one pixel, and it must say so"
    assert 0.0 <= r.margin <= 1.0


def test_a_frame_the_left_gap_cannot_read_abstains_with_a_reason():
    r = frame_offset_mm(frame(gap_px=0), calibrated())
    assert r.mm is None and r.margin == 0.0
    assert "no unexposed base" in r.reason


def test_the_right_hand_reading_refuses_a_picture_that_is_not_a_frame_wide():
    """Its closure test, and it has to be tight. A loose one fired on three
    frames of walk D and put the picture at 35.39 mm each time -- under the
    35.90 that strip measures, so the band it found was picture."""
    narrow = frame(gap_px=10, right_px=120)       # far too much "gap" at right
    r = right_gap_closure(narrow, calibrated())
    assert r.mm is None
    assert "not the 36.0 mm a frame is" in r.reason or "wider than" in r.reason


def test_the_right_hand_reading_abstains_when_the_picture_is_cut():
    """Geometry, not bad luck: 0.49 mm of slack against a 0.25 mm minimum gap
    means one edge shows a gap or the other does, rarely both."""
    r = right_gap_closure(frame(gap_px=18, right_px=0), calibrated())
    assert r.mm is None
    assert "right edge" in r.reason


# -- the causal prior -------------------------------------------------------


def test_the_prior_uses_no_pixels_from_the_frame_it_predicts():
    """Which is the entire reason it belongs here: it fails on none of the
    axes the pixel detectors fail on, so a sky frame cannot defeat both."""
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1), (3, -1.2, -1.2)], 4)
    assert r.mm == pytest.approx(-1.3, abs=0.01)


def test_a_correction_already_applied_is_carried_into_the_prediction():
    """The difference from `fill_from_neighbours`, which fits offset against
    frame number. Once frame 3 is pulled to centre, frame 4 does not continue
    the line frames 1 and 2 were on."""
    uncorrected = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1),
                                  (3, -1.2, -1.2)], 4).mm
    corrected = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1),
                                (3, -1.2, 0.0)], 4).mm
    assert corrected == pytest.approx(uncorrected + 1.2, abs=0.01)


def test_only_consecutive_frames_contribute_an_advance():
    """Pairing across a frame nobody could measure calls two advances one,
    which reads as a doubled error and predicts the next frame into the middle
    of nowhere."""
    r = predict_offset([(1, -1.0, -1.0), (3, -1.2, -1.2)], 4)
    assert r.mm is None
    assert "consecutive" in r.reason


def test_the_prior_refuses_a_frame_more_than_one_advance_away():
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1)], 7)
    assert r.mm is None
    assert "more than one advance" in r.reason


def test_one_advance_may_confirm_but_never_sets_the_distance():
    """A single sample is a number with nothing to check it against. It can
    still agree with a pixel reading, which is evidence, but a margin of zero
    keeps it from winning the tie-break."""
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1)], 3)
    assert r.mm is not None
    assert r.margin == 0.0
    assert r.precision == pytest.approx(HOLD_TOLERANCE_MM)


def test_the_prior_claims_no_more_precision_than_its_sample_supports():
    """Two identical readings say the sample is small, not that the advance is
    perfect. Ignoring that made the prior miss its partner by 0.02 mm on frames
    8 and 9 of walk D."""
    two = predict_offset([(n, -1.0, -1.0) for n in (1, 2, 3)], 4)
    many = predict_offset([(n, -1.0, -1.0) for n in range(1, 11)], 11)
    assert two.precision > many.precision
    assert many.precision < HOLD_TOLERANCE_MM
