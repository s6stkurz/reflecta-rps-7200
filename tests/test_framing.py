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
    columns = framing.APERTURE_UNITS / _WIDTH       # units a column
    frame = int(round(framing.LEGACY_FRAME_UNITS / columns))
    side = (_WIDTH - frame) // 2
    out = {
        "registration": framing.registration_error(_prescan(left=40))[1],
        "too wide": framing.picture_start(_prescan(left=45), _BASE)[1][
            "reason"],
        "left gap": framing.frame_offset(_prescan(left=6), _BASE).reason,
        "right gap": framing.frame_offset(_prescan(right=6), _BASE).reason,
        "right band": framing.right_gap_closure(
            _prescan(left=6, right=45), _BASE).reason,
        "not a frame": framing.right_gap_closure(
            _prescan(left=20, right=20), _BASE).reason,
        "closes": framing.right_gap_closure(
            _prescan(left=side, right=_WIDTH - frame - side), _BASE).reason,
        "prior": framing.predict_offset(
            [(1, 0.0, 1.0), (2, 2.5, 3.0), (3, 4.2, 5.0)]).reason,
        "strip": framing.predict_from_strip(
            {1: 1.0, 2: 2.0, 4: 4.1, 5: 5.0}, 3).reason,
    }
    picture = _film(h=287, w=_WIDTH, seed=9)
    moved = np.roll(picture, 8, axis=0)
    out["off axis"] = framing.measure_shift(
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


# -- the move to units changes no column (stored-mm, 2026-10-13) --------------
#
# Every distance in `framing` is a millimetre literal over `MM_PER_UNIT` now,
# and every column it decides is the one the millimetre code decided. A
# two-place literal would not be: 2.37 units for 0.25 mm moves `GAP_MIN` at
# 5172 columns. The widths are every pass this scanner returns that a hold or
# the gap model sees -- the 300, 600 (both widths), 900, 1800 and 3600 dpi
# prescans.

#: The constants as they stood in millimetres at b0b546a.
_APERTURE_MM = 10344 * 25.4 / 7200
_HISTORIC = {"SEARCH": 11.0, "GAP_MIN": 0.25, "MAX_GAP": 2.6,
             "RECORDED_SEARCH": 9.0}
WIDTHS = (428, 860, 862, 1292, 2584, 5172)


@pytest.mark.parametrize("width", WIDTHS)
def test_unit_constants_keep_every_derived_column(width):
    from rps7200.protocol import MM_PER_UNIT

    mm_px = _APERTURE_MM / width
    units_px = framing.APERTURE_UNITS / width
    # The search reach `register` is given, and the reaches the study tools
    # compare (9.0 and 10.0 mm as the records were made).
    assert framing.reach_px(width) == int(
        _HISTORIC["SEARCH"] / max(mm_px, 1e-9))
    for mm in (9.0, 10.0, 11.0):
        assert framing.reach_px(width, mm / MM_PER_UNIT) == int(
            mm / max(mm_px, 1e-9)), mm
    # The shortest band that is a gap, and the widest, in `picture_start`
    # and `right_gap_closure`.
    assert max(1, int(round(framing.GAP_MIN_UNITS / units_px))) == max(
        1, int(round(_HISTORIC["GAP_MIN"] / mm_px)))
    assert int(round(framing.MAX_GAP_UNITS / units_px)) == int(
        round(_HISTORIC["MAX_GAP"] / mm_px))
    # And `edge_bands`' widest, which it compares unrounded.
    assert framing.MAX_GAP_UNITS / units_px == pytest.approx(
        _HISTORIC["MAX_GAP"] / mm_px, rel=1e-12)
    # The legacy target's columns, which every aim of a roll with no edge
    # reader is measured from.
    assert framing.TARGET_GAP_UNITS / units_px == pytest.approx(
        ((_APERTURE_MM - 36.0) / 2.0) / mm_px, rel=1e-9)


def test_a_constant_is_its_millimetre_figure_over_mm_per_unit():
    """Written as the quotient, never as a rounded number of units."""
    from rps7200.protocol import COMMAND_UNITS, MM_PER_UNIT

    assert framing.SEARCH_UNITS == 11.0 / MM_PER_UNIT
    assert framing.GAP_MIN_UNITS == 0.25 / MM_PER_UNIT
    assert framing.MAX_GAP_UNITS == 2.6 / MM_PER_UNIT
    assert framing.ROLL_TRAVEL_LIMIT_UNITS == 12.0 / MM_PER_UNIT
    assert framing.DRIFT_SHORTFALL_UNITS == 0.85 / MM_PER_UNIT
    assert framing.HOLD_TOLERANCE_UNITS == 1.0 + COMMAND_UNITS
    assert framing.AGREE_UNITS == framing.HOLD_TOLERANCE_UNITS
    assert framing.APERTURE_UNITS == pytest.approx(_APERTURE_MM / MM_PER_UNIT,
                                                   rel=1e-15)
    assert framing.MAX_DY_FRACTION == framing.MAX_DY_PX / 287
    for gone in ("APERTURE_MM", "APERTURE_HEIGHT_MM", "SEARCH_MM", "MAX_DY_MM",
                 "GAP_MIN_MM", "HOLD_TOLERANCE_MM", "AGREE_MM", "MAX_GAP_MM",
                 "ROLL_TRAVEL_LIMIT_MM", "measure_shift_mm", "frame_offset_mm",
                 "registration_error_mm"):
        assert not hasattr(framing, gone), gone


def test_the_hold_scale_and_the_readers_meet_only_through_columns():
    """Two scales 0.2% apart, both units: the hold's (the aperture over the
    width) and the reader's (`COLUMNS_PER_UNIT`). A frame the reader measures
    at 350.6 is 351.3 on the hold's, and taking one for the other would move
    a centred frame by the difference."""
    assert framing.units_per_column_hold(428) == pytest.approx(
        framing.APERTURE_UNITS / 428)
    frame = framing.reader_columns_to_hold_units(
        framing.FRAME_WIDTH_UNITS * framing.COLUMNS_PER_UNIT, 428)
    assert frame == pytest.approx(351.33, abs=0.01)
    assert framing.units_per_column(428) / framing.units_per_column_hold(
        428) == pytest.approx(0.998, abs=0.0005)


@pytest.mark.parametrize(("record", "expected"), [
    ({"offset_units": 4.73}, 4.73),
    ({"offset_mm": 0.5}, 0.5 / 0.1057),
    ({"offset_units": 4.73, "offset_mm": 9.0}, 4.73),
    ({"offset_units": None, "offset_mm": 0.5}, 0.5 / 0.1057),
    ({}, None),
    ({"offset_units": float("nan")}, None),
    ({"offset_mm": float("nan")}, None),
    ({"offset_units": "sideways"}, None),
    ({"offset_units": True}, None),
    (None, None),
])
def test_units_of_reads_a_record_in_either_unit(record, expected):
    """Store units, read both: `<name>_units` as written since 2026-10-13,
    else `<name>_mm` converted, else nothing -- and nothing for a value that
    is no finite number, which is no position rather than the largest one."""
    got = framing.units_of(record, "offset")
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)
