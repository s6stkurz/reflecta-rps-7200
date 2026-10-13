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


# -- where a scan cuts, in a prescan's columns --------------------------------


def _anchors(monkeypatch):
    """`research/frame-edge/anchors.py`, for the fits it stored in `anchors.json`.

    Imported rather than its numbers copied here, so the constant is held to
    the study's own reading of its fits. Importing it puts the study's folder
    on `sys.path`, and its dataclasses want it in `sys.modules`; `monkeypatch`
    takes both off again afterwards.
    """
    import importlib.util
    import sys
    from pathlib import Path

    pytest.importorskip("PIL")
    study = Path(framing.__file__).resolve().parents[1] / "research" / "frame-edge"
    if not (study / "anchors.json").exists():
        pytest.skip("the frame-edge study's anchors.json is not in this checkout")
    monkeypatch.syspath_prepend(str(study))
    spec = importlib.util.spec_from_file_location("frame_edge_anchors",
                                                  study / "anchors.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_the_scan_window_is_what_the_stored_fits_say(monkeypatch):
    """A prescan comes back 428 columns wide and a scan takes more than that;
    the guides drawn as the scan's edges are this constant, so it has to be
    the medians of what the study's 36 prescan/scan pairs measured. Read with
    the device's widths only, so the answer does not depend on which library
    entries happen to be on the machine."""
    report = _anchors(monkeypatch).window_report(library=None)
    assert report["pairs"] >= 30
    left, right = framing.SCAN_WINDOW_COLUMNS
    assert left == pytest.approx(report["left"], abs=0.1)
    assert right == pytest.approx(report["right"], abs=0.1)
    # The docstring's reasons for one constant rather than a table: each read
    # direction within half a column of it, and each resolution within 0.4 --
    # but 600 dpi's right edge, which the device's 860 columns cut short.
    for group in report["per_direction"].values():
        assert abs(group["left"] - left) < 0.5 and abs(group["right"] - right) < 0.5
    for dpi, group in report["per_dpi"].items():
        assert abs(group["left"] - left) < 0.4, dpi
        assert abs(group["right"] - right) < (0.75 if dpi == 600 else 0.4), dpi


def test_a_scan_takes_more_than_the_prescan_on_both_sides():
    left, right = framing.SCAN_WINDOW_COLUMNS
    assert left < 0.0 < framing.PRESCAN_COLUMNS < right


def test_the_scan_window_scales_with_the_prescans_width():
    left, right = framing.SCAN_WINDOW_COLUMNS
    assert framing.scan_window(428) == pytest.approx((left, right))
    assert framing.scan_window(856) == pytest.approx((2 * left, 2 * right))
    assert framing.scan_window(214) == pytest.approx((left / 2, right / 2))


# -- framing's reasons are said in the transport's units (NEW-T2) ------------
#
# They land in each frame's record and the study tools print them. Two
# readers match their words -- tools/roll_registration_study.py on "off the
# film axis", research/frame-edge/baseline_current.py on "no unexposed
# base", "past the" ... "margin", "not reachable from the edge" and "wider
# than the" -- so those phrases stay word for word; only the numbers moved
# from millimetres to units, and to rows for an offset across the film.

#: Base, as `FilmBase` measures it, and a prescan's width.
_BASE = framing.FilmBase(level=200.0, flatness=1.0, bands=4, spread=0.0)
_WIDTH = int(framing.PRESCAN_COLUMNS)


def _prescan(left=0, right=0, height=60, seed=3):
    """Picture texture, with flat base ``left`` and ``right`` columns wide
    at its edges."""
    rng = np.random.default_rng(seed)
    image = rng.uniform(20, 120, (height, _WIDTH))
    if left:
        image[:, :left] = _BASE.level
    if right:
        image[:, _WIDTH - right:] = _BASE.level
    return image


def _reasons():
    """Every reason the members give, each from a picture made to give it."""
    columns = framing.APERTURE_MM / _WIDTH          # mm a column
    frame = int(round(framing.FRAME_WIDTH_MM / columns))
    side = (_WIDTH - frame) // 2
    out = {
        "registration": framing.registration_error_mm(_prescan(left=40))[1],
        "too wide": framing.picture_start(_prescan(left=45), _BASE)[1][
            "reason"],
        "left gap": framing.frame_offset_mm(_prescan(left=6), _BASE).reason,
        "right gap": framing.frame_offset_mm(_prescan(right=6), _BASE).reason,
        "right band": framing.right_gap_closure(
            _prescan(left=6, right=45), _BASE).reason,
        "not a frame": framing.right_gap_closure(
            _prescan(left=20, right=20), _BASE).reason,
        "closes": framing.right_gap_closure(
            _prescan(left=side, right=_WIDTH - frame - side), _BASE).reason,
        "prior": framing.predict_offset(
            [(1, 0.0, 0.1), (2, 0.25, 0.3), (3, 0.42, 0.5)]).reason,
        "strip": framing.predict_from_strip(
            {1: 0.1, 2: 0.2, 4: 0.41, 5: 0.5}, 3).reason,
    }
    picture = _film(h=287, w=_WIDTH, seed=9)
    moved = np.roll(picture, 8, axis=0)
    out["off axis"] = framing.measure_shift_mm(
        picture.astype(np.uint8), moved.astype(np.uint8))[1].get("reason", "")
    return out


def test_no_reason_says_millimetres():
    reasons = _reasons()
    for name, reason in reasons.items():
        assert reason, f"{name} gave no reason"
        assert " mm" not in reason, (name, reason)


def test_the_phrases_the_study_tools_match_are_kept():
    reasons = _reasons()
    assert "wider than the" in reasons["too wide"]
    assert "a gap can be" in reasons["too wide"]
    assert reasons["too wide"].endswith(
        "units a gap can be -- the end of the strip, or picture at the base "
        "level")
    assert "wider than the" in reasons["right band"]
    assert reasons["registration"].endswith(
        "units the aperture allows -- detector error, not film movement")
    assert "units at the left edge" in reasons["left gap"]
    assert "through a 340.6 units frame" in reasons["right gap"]
    assert "not the 340.6 units a frame is" in reasons["not a frame"]
    assert reasons["closes"].startswith("picture ")
    assert "units each, scatter" in reasons["prior"]
    assert "units per frame, scatter" in reasons["strip"]


def test_an_offset_across_the_film_is_said_in_rows():
    """Across the film is no transport distance: it is said in rows of the
    prescan, and "off the film axis" is kept for the study tool."""
    reason = _reasons()["off axis"]
    assert reason == ("matched 8 rows off the film axis, which the transport "
                      "cannot do"), reason


# -- the device's pass shapes -------------------------------------------------


@pytest.mark.real_pass_shapes
def test_the_device_pass_shapes_are_the_measured_ones():
    """Stefan's decision (2026-10-13): one table of what the device sends,
    the five measured rows, 1200 dpi from one entry and 7200 dpi from the
    README -- documented, not measured, and its comment says so."""
    assert framing.DEVICE_PASS_SHAPES == {
        300: (287, 428), 600: (574, 860), 900: (860, 1292),
        1200: (1148, 1724), 1800: (1721, 2584), 3600: (3443, 5172),
        7200: (6884, 10344),
    }
    assert framing.device_shape(600) == (574, 860)
    assert framing.device_shape(1000) is None, "never a width from a ratio"
    assert framing.device_shape(None) is None
    assert framing.dpi_of_width(428) == 300
    assert framing.dpi_of_width(856) is None


@pytest.mark.real_pass_shapes
def test_the_prescan_width_is_the_tables_and_exactly_428():
    """Everything under tools/frame_edges scales from it: moved by so much as
    a fraction, the reader's scale moves and its parity with the study has
    to be run again."""
    assert framing.PRESCAN_COLUMNS == framing.DEVICE_PASS_SHAPES[300][1] == 428.0
    assert isinstance(framing.PRESCAN_COLUMNS, float)
