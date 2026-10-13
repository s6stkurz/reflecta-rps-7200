"""Deciding a frame's position from more than one detector.

The four detectors in `docs/whole-roll-plan.md`'s graveyard each produced a
confident number and each was wrong on some frames, and every one was caught
only by looking at the picture. What they had in common was not a bad
threshold: it was that one detector's word was enough to move film.

These pin the rules that replace that. Two members must agree before anything
moves; agreement is judged in units of travel because the members' confidences are
not on one scale and cannot be put on one; and where an agreeing pair differs,
the more trusted member's own number is taken rather than an average of the
two, which would be a number neither of them measured.
"""
import numpy as np
import pytest
from conftest import load_tool

from rps7200.framing import (
    AGREE_UNITS,
    FRAME_MODEL,
    MAX_GAP_UNITS,
    predict_from_strip,
    propose_offsets,
    APERTURE_UNITS,
    HOLD_TOLERANCE_UNITS,
    MAX_CORRECTION_UNITS,
    FilmBase,
    Reading,
    StripWalk,
    combine,
    frame_offset,
    predict_offset,
)
from rps7200.protocol import to_units

#: The 36 mm model, retired from the driver on 2026-10-13 and kept for the
#: stored studies: its closure member is tested where it now lives.
history = load_tool("roll_registration_history")

# The distances below were measured, and these tests first written, in
# millimetres; `to_units` keeps every one where it was against the bounds.

WIDTH, HEIGHT = 428, 60
BASE_LEVEL = 37.0
UNITS_PX = APERTURE_UNITS / WIDTH


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


def reading(units, trust, source="left-gap", precision=UNITS_PX) -> Reading:
    return Reading(units=units, margin=trust, source=source, precision=precision)


# -- the gate ---------------------------------------------------------------


def test_one_member_alone_moves_nothing():
    """The whole gate. `gap_edges` returned a confident number on its own and
    was provably wrong on four of nine frames of a real walk."""
    decision, detail = combine([reading(to_units(1.0), 0.9)])
    assert decision is None
    assert "two that agree" in detail["reason"]


def test_two_members_that_agree_decide():
    decision, detail = combine([reading(to_units(-0.61), 0.8),
                                reading(to_units(-0.65), 0.4, "prior",
                                        to_units(0.1))])
    assert decision == pytest.approx(to_units(-0.61))
    assert detail["chose"] == "left-gap"


def test_two_members_that_disagree_move_nothing():
    decision, detail = combine([reading(to_units(-0.61), 0.8),
                                reading(to_units(+1.20), 0.9)])
    assert decision is None
    assert "none agree" in detail["reason"]
    # and it must say how far apart they were, not merely that they were
    assert "17.1 units apart" in detail["reason"]
    assert "mm" not in detail["reason"]


def test_the_pair_said_to_be_closest_is_the_closest():
    """It took the pair furthest apart and called it 'closest' -- the one
    line a person reads to learn why a frame was left where it came."""
    decision, detail = combine([reading(to_units(-0.60), 0.8, "left-gap", 0.1),
                                reading(0.00, 0.8, "prior", 0.1),
                                reading(to_units(0.35), 0.8, "closure", 0.1)])
    assert decision is None
    assert "closest are prior" in detail["reason"], detail["reason"]
    assert "closure" in detail["reason"] and "left-gap" not in detail["reason"]


def test_two_pairs_equally_close_are_said_without_a_crash():
    """The pairs were tuples ending in two Readings, which do not order: a
    tie on the number compared them, and the TypeError escaped the roll's
    per-frame net and ended the roll."""
    decision, detail = combine([reading(to_units(-0.40), 0.8, "left-gap", 0.1),
                                reading(0.00, 0.8, "prior", 0.1),
                                reading(to_units(0.40), 0.8, "closure", 0.1)])
    assert decision is None
    assert "none agree" in detail["reason"]


def test_an_agreeing_pair_is_never_averaged():
    """The bracket merge (archived, docs/multi-exposure/) settled this for
    exposures: where sources conflict, the single most-trusted one wins and
    nothing is blended. The mean of 0.30 and 0.45 is 0.375, a distance neither
    detector measured."""
    decision, _d = combine([reading(to_units(0.30), 0.9),
                            reading(to_units(0.45), 0.4, "prior")])
    assert decision == pytest.approx(to_units(0.30))


def test_the_least_trusted_of_an_agreeing_pair_never_sets_the_number():
    decision, detail = combine([reading(to_units(0.30), 0.2),
                                reading(to_units(0.45), 0.95, "prior")])
    assert decision == pytest.approx(to_units(0.45))
    assert detail["chose"] == "prior"


# -- what agreement means ---------------------------------------------------


def test_the_gate_is_the_pairs_own_precisions_not_one_tolerance():
    """The finding that made this work. Held to a flat one-pixel gate the
    ensemble refused eleven frames of thirteen on walk D -- because the left
    gap reads to a quarter pixel against the ladder while the advance itself
    wanders +-2 px, so the prior was being asked to be eight times more
    repeatable than the mechanism it describes."""
    apart = 2.4 * UNITS_PX                        # just over two pixels
    precise = [reading(0.0, 0.5), reading(apart, 0.5, "closure")]
    assert combine(precise)[0] is None, "two pixel readings must stay strict"

    mixed = [reading(0.0, 0.5),
             reading(apart, 0.5, "prior", precision=to_units(0.12))]
    assert combine(mixed)[0] is not None, "a prior answers to its own precision"


def test_agreement_is_capped_at_the_smallest_move_the_hardware_can_make():
    """Two readings further apart than one step would command different moves,
    so calling them agreed is meaningless however imprecise either admits to
    being. Without the cap a member could agree with anything by claiming to
    be bad enough."""
    assert AGREE_UNITS == HOLD_TOLERANCE_UNITS
    sloppy = [reading(0.0, 0.5, "prior", precision=999.0),
              reading(AGREE_UNITS * 2, 0.5, "left-gap", precision=999.0)]
    assert combine(sloppy)[0] is None


def test_the_gate_cleared_the_spread_the_old_frame_width_had():
    """Under the 36 mm model a left reading aimed at `TARGET_GAP_UNITS` and
    the right closure at the aperture less the same; the two agreed exactly
    only for a frame of exactly that width, measured 339.6 to 342.5 units, so
    a gate below the spread would have called two right readings a
    disagreement. Kept with the history module that replays it."""
    assert AGREE_UNITS > history.FRAME_WIDTH_SPREAD_UNITS / 2


def test_a_walk_judges_on_the_left_gap_and_the_prior_alone(monkeypatch):
    """The closure member went with the 36 mm model: a frame wider than the
    aperture leaves no view with a gap at both edges, so it could never
    answer. A walk with no reader asks two members and no third."""
    import rps7200.framing as framing

    seen = []
    real = framing.combine

    def spy(readings, **kw):
        seen.append([r.source for r in readings])
        return real(readings, **kw)

    monkeypatch.setattr(framing, "combine", spy)
    walk = StripWalk()
    for n in range(1, 4):
        walk.observe(n, frame(gap_px=18, seed=n))
    assert walk.base is not None
    walk.judge(3, frame(gap_px=18, seed=3))
    assert seen == [["left-gap", "prior"]]


# -- the physical bound -----------------------------------------------------


def test_a_reading_past_the_bound_is_dropped_and_not_clamped():
    """`picture_start` accepts a run beginning anywhere within `EDGE_FRACTION`
    of the edge, so it can report far more than a gap's width -- the bound is
    not implied by `MAX_GAP_UNITS` and has to be applied here. A clamped
    reading would be a fabricated one."""
    wild = reading(MAX_CORRECTION_UNITS + 9.0, 0.9)
    decision, detail = combine([wild, reading(to_units(0.30), 0.4, "prior")])
    assert decision is None
    assert detail["dropped"], "the reading must be named, not silently ignored"
    assert detail["members"][0]["units"] == pytest.approx(
        MAX_CORRECTION_UNITS + 9.0, abs=1e-3)


def test_the_bound_is_not_max_registration():
    """4.6 units is how well a frame can be placed, not how badly the
    transport can leave one. Walk A sat 13 to 21 units out and needed every
    one."""
    assert MAX_CORRECTION_UNITS > to_units(2.0)


def test_the_bound_comes_from_the_measured_frame():
    """The widest gap less the target, both from the measured frame and
    pitch through columns (Stefan's gap-bound decision: pitch less frame plus
    one smallest move): 18.8 hold units of gap past a -3.05 target, 21.8 in
    all, where the 36 mm model gave 22.3."""
    assert MAX_CORRECTION_UNITS == pytest.approx(
        MAX_GAP_UNITS - FRAME_MODEL.target_units)
    assert MAX_CORRECTION_UNITS == pytest.approx(21.8, abs=0.05)
    assert history.MAX_CORRECTION_UNITS == pytest.approx(22.3, abs=0.05)


# -- the members ------------------------------------------------------------


def test_the_left_gap_places_a_frame_and_reports_its_precision():
    r = frame_offset(frame(gap_px=18), calibrated())
    assert r.units is not None
    assert r.precision == pytest.approx(UNITS_PX), "one pixel, and it must say so"
    assert 0.0 <= r.margin <= 1.0


def test_a_frame_the_left_gap_cannot_read_abstains_with_a_reason():
    r = frame_offset(frame(gap_px=0), calibrated())
    assert r.units is None and r.margin == 0.0
    assert "no unexposed base" in r.reason


def test_the_right_hand_reading_refuses_a_picture_that_is_not_a_frame_wide():
    """The retired closure member, as the history module keeps it for the
    stored studies. Its closure test, and it had to be tight. A loose one fired on three
    frames of walk D and put the picture at 334.8 units each time -- under the
    339.6 that strip measures, so the band it found was picture."""
    narrow = frame(gap_px=10, right_px=120)       # far too much "gap" at right
    r = history.right_gap_closure(narrow, calibrated())
    assert r.units is None
    assert "a frame is" in r.reason or "wider than" in r.reason


def test_the_right_hand_reading_abstains_when_the_picture_is_cut():
    """Geometry, not bad luck: 4.6 units of slack against a 2.4-unit minimum
    gap means one edge shows a gap or the other does, rarely both."""
    r = history.right_gap_closure(frame(gap_px=18, right_px=0), calibrated())
    assert r.units is None
    assert "right edge" in r.reason


# -- the causal prior -------------------------------------------------------


def test_the_prior_uses_no_pixels_from_the_frame_it_predicts():
    """Which is the entire reason it belongs here: it fails on none of the
    axes the pixel detectors fail on, so a sky frame cannot defeat both."""
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1), (3, -1.2, -1.2)], 4)
    assert r.units == pytest.approx(-1.3, abs=0.01)


def test_a_correction_already_applied_is_carried_into_the_prediction():
    """The difference from `fill_from_neighbours`, which fits offset against
    frame number. Once frame 3 is pulled to centre, frame 4 does not continue
    the line frames 1 and 2 were on."""
    uncorrected = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1),
                                  (3, -1.2, -1.2)], 4).units
    corrected = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1),
                                (3, -1.2, 0.0)], 4).units
    assert corrected == pytest.approx(uncorrected + 1.2, abs=0.01)


def test_only_consecutive_frames_contribute_an_advance():
    """Pairing across a frame nobody could measure calls two advances one,
    which reads as a doubled error and predicts the next frame into the middle
    of nowhere."""
    r = predict_offset([(1, -1.0, -1.0), (3, -1.2, -1.2)], 4)
    assert r.units is None
    assert "consecutive" in r.reason


def test_the_prior_refuses_a_frame_more_than_one_advance_away():
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1)], 7)
    assert r.units is None
    assert "more than one advance" in r.reason


def test_one_advance_may_confirm_but_never_sets_the_distance():
    """A single sample is a number with nothing to check it against. It can
    still agree with a pixel reading, which is evidence, but a margin of zero
    keeps it from winning the tie-break."""
    r = predict_offset([(1, -1.0, -1.0), (2, -1.1, -1.1)], 3)
    assert r.units is not None
    assert r.margin == 0.0
    assert r.precision == pytest.approx(HOLD_TOLERANCE_UNITS)


def test_the_prior_claims_no_more_precision_than_its_sample_supports():
    """Two identical readings say the sample is small, not that the advance is
    perfect. Ignoring that made the prior miss its partner by 0.2 units on
    frames 8 and 9 of walk D."""
    two = predict_offset([(n, -1.0, -1.0) for n in (1, 2, 3)], 4)
    many = predict_offset([(n, -1.0, -1.0) for n in range(1, 11)], 11)
    assert two.precision > many.precision
    assert many.precision < HOLD_TOLERANCE_UNITS


# -- proposing a whole strip at once -----------------------------------------
#
# The walk takes one complete pass and only then decides anything. That is
# worth more than deciding as it goes, and the reason is evidence rather than
# effort: a forward walk can only fit the frames behind it, where a finished
# one fits across all of them and can speak for a frame from both sides.


def strip(count=8, gap=18, seed0=0):
    return [(n, frame(gap_px=gap, seed=seed0 + n)) for n in range(1, count + 1)]


def test_the_strip_speaks_for_every_frame_it_can_read():
    offsets, notes = propose_offsets(strip())
    assert len(offsets) == 8
    assert all(notes[n]["source"] in ("measured", "unconfirmed", "neighbours")
               for n in offsets)


def test_a_measurement_is_never_overruled_by_the_model():
    """Walk E, and the reason this function was rewritten. Frames 1 to 4 each
    carried a real left-gap reading near -10 units, the members disagreed, and
    the fallback replaced the reading from the detector graded on the
    displacement ladder with a line extrapolated from the far end of the strip
    -- silently. A model may stand in for a measurement that does not exist; it
    may not overrule one that does.
    """
    # one frame well away from the others, so no member will corroborate it
    frames = strip(6)
    # 21 columns: inside the measured frame's 23-column gap bound, and three
    # columns -- more than the two members' precisions -- from the others.
    frames[0] = (1, frame(gap_px=21, seed=99))
    offsets, notes = propose_offsets(frames)
    assert notes[1]["source"] == "unconfirmed", notes[1]
    alone = frame_offset(frames[0][1], calibrated())
    assert offsets[1] == pytest.approx(alone.units, abs=0.2)


def test_a_frame_nothing_could_read_is_filled_and_says_so():
    frames = strip(6)
    frames[3] = (4, frame(gap_px=0, seed=4))          # no base in view at all
    offsets, notes = propose_offsets(frames)
    assert notes[4]["source"] == "neighbours"
    assert 4 in offsets


def test_the_strip_fit_never_includes_the_frame_it_judges():
    """Including it would make the two members agree by construction, and two
    detectors that cannot disagree are one detector."""
    placed = {1: 0.0, 2: -0.1, 3: -0.2, 4: 9.9, 5: -0.4, 6: -0.5}
    said = predict_from_strip(placed, 4)
    assert said.units == pytest.approx(-0.3, abs=0.1), "the 9.9 must not be used"


def test_the_strip_fit_needs_frames_either_side_of_a_guess():
    assert predict_from_strip({1: 0.0, 2: -0.1}, 3).units is None


def test_a_strip_with_no_calibratable_base_proposes_nothing():
    """It abstains for the whole strip rather than per frame: without a level
    there is no detector at all, and saying so once is the honest form."""
    offsets, notes = propose_offsets([(n, frame(gap_px=0, seed=n))
                                      for n in range(1, 5)])
    assert offsets == {}
    assert all(n["source"] == "none" and n["reason"] for n in notes.values())
