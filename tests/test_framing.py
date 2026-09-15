"""Working out how a pass sits, from the pixels alone.

The scanner will hand a pass back reversed and say nothing about it: MODE
SELECT byte 14 bit 0 skips the re-home for bidirectional speed, and a pass
following another bit-0 pass reads top-and-bottom reversed with no status bit
and no sense condition. The only evidence is that it does not match the
framing pass taken of the same picture a minute earlier.

Being wrong here stands a photograph on its head, so what is checked as hard
as the catching is the refusing.
"""
import numpy as np
import pytest

from rps7200 import framing

# -- which way up did this pass come back? -----------------------------------


def _film(h=200, w=300, seed=4):
    """A picture with enough structure to tell one way up from another.

    Scaled into 0..255 so that reducing it to an 8-bit prescan below is a
    faithful copy. A first version of this ran from -129 to 251 and the cast
    wrapped the negatives, which made the reference a different picture -- and
    `reversal_against` refused to call it, correctly, which is how the bad
    fixture was found.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    a = (np.sin(x / 40) * 90 + np.cos(y / 25) * 60 + y * 0.5
         + rng.normal(0, 8, (h, w)))
    return (a - a.min()) * 255.0 / float(a.max() - a.min())


@pytest.mark.parametrize("came_as", [(0, False), (180, False),
                                     (0, True), (180, True)])
def test_a_reversed_pass_is_recognised_against_its_prescan(came_as):
    """The scanner hands a pass back reversed with nothing to say it has --
    no status bit, no sense condition. The only evidence is that it does not
    match the framing pass of the same picture."""
    from rps7200 import preview
    truth = _film()
    prescan = truth[::4, ::4].astype(np.uint8)          # coarse, and 8-bit
    scan = (preview.orient(truth, *came_as) * 257).astype(np.uint16)

    found, detail = framing.reversal_against(prescan, scan)
    assert found == came_as, detail
    if came_as != (0, False):
        assert detail["margin"] > framing.REVERSAL_MARGIN


def test_correcting_by_what_was_found_puts_the_pass_back():
    from rps7200 import preview
    truth = _film()
    prescan = truth[::4, ::4].astype(np.uint8)
    for came_as in ((180, False), (0, True), (180, True)):
        scan = preview.orient(truth, *came_as)
        found, _ = framing.reversal_against(prescan, scan)
        assert np.allclose(preview.orient(scan, *found), truth)


def test_it_answers_about_arrangement_and_not_about_identity():
    """Worth being explicit, because it is the limit of what this can do.

    Two different photographs with the same broad structure correlate, and
    this will happily say which way round one sits against the other. It is
    not a check that they are the same picture -- keeping the reference
    honest is the caller's job, and `ScanSession._prescan_here` does it by
    refusing a prescan taken at a different transport position.
    """
    from rps7200 import preview
    other = _film(seed=99)                # same structure, different grain
    found, _ = framing.reversal_against(
        other[::4, ::4].astype(np.uint8), preview.orient(_film(), 180))
    assert found == (180, False), "it compares arrangement, nothing more"


def test_a_frame_with_nothing_to_go_on_is_left_exactly_as_it_came():
    """Being wrong here stands a photograph on its head, so the ambiguous
    case is refused rather than guessed at."""
    rng = np.random.default_rng(1)
    unrelated = rng.normal(0, 1, (50, 75))
    found, detail = framing.reversal_against(unrelated, rng.normal(0, 1, (200, 300)))
    assert found == (0, False)
    assert "not enough" in detail["reason"]


def test_a_blank_pass_says_so_rather_than_scoring_something():
    found, detail = framing.reversal_against(np.zeros((50, 75)), np.zeros((20, 30)))
    assert found == (0, False)
    assert detail["reason"] == "nothing to compare"


def test_a_quarter_turn_is_not_one_of_the_answers():
    """The carriage can reverse its travel and the film can be read from the
    other side. Neither changes the shape, and the operator's own turn is a
    separate question from this one."""
    assert set(framing._READINGS) == {(0, False), (180, False),
                                      (0, True), (180, True)}


def test_the_margin_is_set_to_refuse_rather_than_to_catch():
    """A half turn is a huge signal on real film -- the right reading wins by
    a wide margin or there is nothing to judge by at all."""
    assert framing.REVERSAL_MARGIN >= 0.2


def test_infrared_is_not_what_it_judges_by():
    """It holds the dust rather than the picture, and a prescan has no fourth
    plane to compare it against."""
    rng = np.random.default_rng(2)
    truth = _film(60, 90)
    rgb = np.repeat(truth[..., None], 3, axis=2)
    rgbi = np.concatenate([rgb, rng.normal(0, 400, (60, 90, 1))], axis=2)
    assert np.allclose(framing._comparable(rgb), framing._comparable(rgbi))
