"""The window's own decisions, tested without opening one.

A Tk window driven from pytest hangs on macOS -- reliably, at the second test --
though the same sequence runs clean as a plain script. A suite that hangs is
worse than one that covers a little less, so the logic that must not be wrong
lives in module-level functions here rather than inside the widget, and the
widget wiring is checked by running `make run-demo`.

What is tested is what would mislead the operator: the stop button saying which
of the two things it will do, the option parsing that decides what the scanner
is asked for, and the resolution guard.
"""
import inspect
import json
import sys
import time
import types

import numpy as np
import pytest

from rps7200.session import Approved

from conftest import load_tool
from rps7200 import shortcuts
from rps7200.mono import MONO_AVERAGE

# `gui` imports tkinter, and this runs at collection: without it every test in
# this file was a collection error rather than a skip, which is what a Linux
# box with no python3-tk looks like.
pytest.importorskip("tkinter")

gui = load_tool("gui")


# -- the stop button --------------------------------------------------------


def test_a_roll_promises_to_stop_after_the_frame():
    """Not "now". The frame in flight always finishes -- an abandoned read is
    what leaves the scanner needing a power cycle."""
    assert gui.stop_label("scanning a roll of 6 frames") == "Stop after this frame"
    assert gui.stop_label("walking a roll of 6 frames") == "Stop after this frame"


def test_a_single_pass_does_not_promise_a_frame_boundary_it_has_not_got():
    for job in ("scanning at 1800 dpi RGBI",
                "prescanning at 300 dpi (~16 s)",
                "calibrating (3-4 minutes)"):
        assert gui.stop_label(job) == "Stop (finishes this pass)", job


def test_the_label_does_not_depend_on_the_progress_readout():
    """It used to read the progress label, which the line counter overwrites a
    second into the pass -- so it relabelled itself mid-roll, which is the one
    thing this button must never do."""
    from rps7200.session import Roll, _describe
    running = _describe(Roll(frames=6, resolution=1800))
    assert gui.stop_label(running) == "Stop after this frame"


# -- what the form asks the scanner for -------------------------------------


def test_the_dpi_ladder_only_offers_resolutions_with_evidence():
    """Every value on it has been driven at, or is what the scanner reports.

    It used to hold every integer divisor of 7200, taken from the candidate
    list in `docs/dpi-tradeoff-plan.md` -- whose Part 1 is titled "which
    resolutions the device accepts" and has not been run. A menu of guesses
    reads as a menu of capabilities.
    """
    for driven in (300, 600, 900, 1200, 1800, 3600):
        assert driven in gui.DPI_LADDER, f"{driven} appears in the captures"
    assert gui.DPI_LADDER[-1] == 7200, "what INQUIRY calls the optical maximum"
    # 1200 joined the list when captures/slide.pcapng turned up: CyberView
    # drives it there, which is the same evidence the others rest on.
    for unprobed in (360, 400, 450, 480, 720, 800, 1440, 2400):
        assert unprobed not in gui.DPI_LADDER, (
            f"{unprobed} has never been asked of this scanner")


def test_the_ladder_is_a_convenience_and_not_a_limit():
    """The box is editable on purpose: there is no client-side validation, and
    the device refuses what it dislikes before any image data moves. So an
    unlisted value can still be typed -- 2400 is the suspected sweet spot and
    is exactly the sort of thing worth trying."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._build_scan)
    assert 'state="readonly"' not in source.split("scan dpi")[1].split("prescan")[0]


@pytest.mark.parametrize("seconds,expected", [
    (16, "16 s"), (70, "70 s"), (227, "3m 47s"), (334, "5m 34s"),
    (3600 * 3 + 240, "3h 04m"),
])
def test_durations_read_the_way_a_person_says_them(seconds, expected):
    assert gui._duration(seconds) == expected


def test_the_window_never_writes_the_comparison_files():
    """`tools/scan.py` and `tools/scan_roll.py` both write 1_/2_/3_*.tif to the
    repo root and clobber each other. The GUI must not join in."""
    source = (__import__("pathlib").Path(gui.__file__)).read_text(encoding="utf-8")
    for name in ("1_nothing_done", "2_corrected", "3_corrected_inverted"):
        assert name not in source


def test_the_window_files_its_own_entries_rather_than_letting_the_driver():
    """Both would write every frame twice."""
    from rps7200.session import ScanSession
    import inspect
    source = inspect.getsource(ScanSession._default_scanner)
    assert "debug=False" in source


# -- aiming the film at a point on the prescan ------------------------------


def test_a_point_already_at_its_edge_asks_for_no_movement():
    assert gui.aim_millimetres(0.0) == pytest.approx(0.0, abs=1e-9)
    assert gui.aim_millimetres(1.0) == pytest.approx(0.0, abs=1e-9)


def test_a_point_goes_to_the_edge_it_is_nearer():
    """Clicking is for putting a border of the picture against a border of the
    aperture. Aiming at the middle was the wrong tool: what you can see and
    want to place is the edge of the frame."""
    left = gui.aim_millimetres(0.25)
    right = gui.aim_millimetres(0.75)
    # A quarter of the aperture in each case, in opposite directions.
    assert abs(left) == pytest.approx(gui.APERTURE_MM * 0.25, abs=0.01)
    assert abs(right) == pytest.approx(gui.APERTURE_MM * 0.25, abs=0.01)
    assert left == pytest.approx(-right, abs=1e-9)


def test_the_two_halves_are_mirror_images():
    """The arithmetic must not favour a direction; only the transport does."""
    for f in (0.1, 0.2, 0.3, 0.45):
        assert gui.aim_millimetres(f) == pytest.approx(
            -gui.aim_millimetres(1 - f), abs=1e-9)


def test_the_furthest_a_click_can_ask_for_is_half_the_aperture():
    """Clicking the middle means "send this to an edge", which is the longest
    trip on offer -- and further than the sub-frame law can be trusted, which
    is why the dialog refuses rather than delivering something else."""
    worst = max(abs(gui.aim_millimetres(f)) for f in (0.499, 0.501))
    assert worst == pytest.approx(gui.APERTURE_MM / 2, abs=0.05)
    assert worst > gui.MAX_TRAVEL_MM, "so the dialog has to refuse it"


def test_a_click_near_an_edge_is_within_reach():
    """Which is the case this is for: the picture's border is near the
    aperture's, and a few millimetres puts them together."""
    assert abs(gui.aim_millimetres(0.05)) < gui.MAX_TRAVEL_MM
    assert abs(gui.aim_millimetres(0.95)) < gui.MAX_TRAVEL_MM


def test_the_aperture_matches_the_transport_window():
    """36.49 mm against a 36 mm frame -- the half-millimetre of slack the
    registration work is all about."""
    assert gui.APERTURE_MM == pytest.approx(36.49, abs=0.01)


def test_one_pixel_of_click_is_finer_than_the_transport_can_move():
    """At 300 dpi the prescan is ~431 px across, so one pixel is 0.085 mm --
    a third of the smallest step. Clicking precisely is not the hard part."""
    per_pixel = gui.APERTURE_MM / 431
    assert per_pixel < gui.FINE_STEP_MM


def test_a_move_reaches_the_worst_real_misframing_in_exactly_one_command():
    """The worst mis-framing on record is the 6 mm CyberView lost on one frame
    of its own strip, and a drifted frame shows about 0.5 mm of aperture slack.

    One command used to be 1.01 mm, so reaching 6 took a chain of them. With
    the cap at param 87 it is 9.36, so the whole range arrives in one -- which
    is the point, because each command pays the ramp again and scatters again
    and the scatter does not shrink with the size of the move.

    The upper bound still matters: a fine adjustment that could travel half the
    aperture would be doing badly, and slowly, what one slide button does
    properly.
    """
    from rps7200.session import plan_nudges

    assert gui.MAX_TRAVEL_MM > 6.0
    assert gui.MAX_TRAVEL_MM < gui.APERTURE_MM / 3
    assert len(plan_nudges(gui.MAX_TRAVEL_MM)) == 1
    assert len(plan_nudges(6.0)) == 1


def test_the_window_and_the_session_agree_on_how_far_is_too_far():
    """Two different numbers would mean the window offering a move the session
    then refuses, which reads as the button being broken."""
    from rps7200 import session as s
    assert gui.MAX_FINE_STEPS == s.MAX_FINE_STEPS
    assert gui.MAX_FINE_MM == pytest.approx(s.FINE_MAX_MM, abs=0.01)
    assert gui.FINE_STEP_MM == pytest.approx(s.FINE_MIN_MM, abs=0.01)


# -- the exposure field, which parses more than one shape -------------------


@pytest.mark.parametrize("text,expected", [
    ("2.5", [2.5]),
    ("1.8, 0.9, 2.1, 1.0", [1.8, 0.9, 2.1, 1.0]),
    ("1.8 0.9 2.1", [1.8, 0.9, 2.1]),
    ("", []),
    ("1,5", [1.0, 5.0]),
])
def test_numbers_reads_the_shapes_people_type(text, expected):
    assert gui._numbers(text) == expected


def test_numbers_refuses_what_is_not_a_number():
    assert gui._numbers("bright") is None
    assert gui._numbers("1.8, oops") is None


def test_the_fine_step_bounds_are_the_calibrated_ones():
    """param 1 and param 87 of distance = 0.1057 x param + 0.1662.

    Derived rather than copied: the window used to carry rounded literals and
    they drifted the moment the cap moved.
    """
    from rps7200.direct import DirectScanner as D
    assert gui.FINE_STEP_MM == pytest.approx(D.STEP_MM * 1 + D.OVERHEAD_MM, abs=0.01)
    assert gui.MAX_FINE_MM == pytest.approx(
        D.STEP_MM * D.MAX_CORRECTION_PARAM + D.OVERHEAD_MM, abs=0.01)


def test_the_prescan_ladder_starts_at_the_scanners_own_preview_resolution():
    """300 is what INQUIRY reports as the fast preview and what the vendor uses
    before every frame. A framing pass that is not cheap has no reason to be."""
    assert gui.PRESCAN_LADDER[0] == 300
    assert all(d <= 900 for d in gui.PRESCAN_LADDER), "a prescan is meant to be cheap"
    assert all(d in gui.DPI_LADDER for d in gui.PRESCAN_LADDER), (
        "a prescan resolution is still a resolution -- it needs the same evidence")


# -- the wheel, which means different numbers on every platform -------------


class _Wheel:
    def __init__(self, delta=0, num=0, state=0):
        self.delta, self.num, self.state = delta, num, state


def _mac(delta=0, num=0, state=0):
    """A wheel event read by the Aqua rule, whatever machine this is."""
    return gui._wheel_amount(_Wheel(delta, num, state), platform="darwin")


def _win(delta=0, num=0, state=0, carry=None):
    """A wheel event read by the Windows rule, with its own carry."""
    return gui._wheel_amount(_Wheel(delta, num, state), platform="win32",
                             carry=carry or gui._WheelCarry())


def test_a_mac_trackpad_scrolls_by_what_it_reports():
    """Single digits, not multiples of 120. Treating a 3 as one notch is what
    made two fingers feel like one click per gesture."""
    assert _mac(delta=-3) == (3, False)
    assert _mac(delta=3) == (-3, False)


def test_a_windows_notch_is_one_line_not_a_hundred_and_twenty():
    assert _win(delta=-120) == (1, False)
    assert _win(delta=240) == (-2, False)


def test_a_windows_touchpad_scrolls_by_the_finger_not_by_the_event():
    """Windows reports a fraction of 120, and a Precision Touchpad sends 12 at
    a time. Reading those as line counts -- which the rule that serves a Mac
    does -- scrolled twelve times too far for the movement that caused it.

    The other half is that they must still add up: rounding each one down to
    nothing would leave a touchpad unable to scroll at all.
    """
    carry = gui._WheelCarry()
    moved = [_win(delta=12, carry=carry)[0] for _ in range(9)]
    assert moved == [0] * 9, "a part of a notch is not yet a line"
    assert _win(delta=12, carry=carry)[0] == -1, "ten twelfths make a line"
    assert carry.value == 0

    # And it survives a change of direction rather than double-counting.
    carry = gui._WheelCarry()
    _win(delta=60, carry=carry)
    assert _win(delta=-60, carry=carry)[0] == 0
    assert carry.value == 0


def test_x11_sends_buttons_and_no_delta_at_all():
    assert _mac(num=4) == (-1, False)
    assert _win(num=4) == (-1, False)
    assert _mac(num=5) == (1, False)
    assert _win(num=5) == (1, False)


def test_a_movement_too_small_to_matter_does_nothing():
    """A trackpad reports zeros between real movement; scrolling on them would
    be scrolling on nothing."""
    assert _mac(delta=0) == (0, False)
    assert _win(delta=0) == (0, False)


def test_shift_means_sideways():
    assert _mac(delta=-3, state=1)[1] is True
    assert _mac(delta=-3, state=0)[1] is False
    assert _win(delta=-120, state=1)[1] is True


def test_scrolling_up_and_down_are_opposite():
    for delta in (1, 3, 7, 120, 240):
        assert _mac(delta=delta)[0] == -_mac(delta=-delta)[0], delta
    for delta in (120, 240, 360):
        # A fresh carry each way: the Windows rule is a running total, so
        # sharing one would be measuring the previous assertion.
        up = _win(delta=delta, carry=gui._WheelCarry())[0]
        down = _win(delta=-delta, carry=gui._WheelCarry())[0]
        assert up == -down, delta


# -- the trackpad, which is a different event entirely ----------------------


class _Touchpad:
    """A <TouchpadScroll> event: both axes packed into one integer."""

    def __init__(self, dx=0, dy=0):
        self.delta = (dx << 16) | (dy & 0xFFFF)
        self.state = 0
        self.num = 0


@pytest.mark.parametrize("dx,dy", [
    (0, 12), (0, -12), (7, 0), (-7, 0), (3, -5), (-9, 40), (0, 0),
])
def test_touchpad_deltas_unpack_both_axes(dx, dy):
    """Tk packs x in the high half and y in the low half, sign-extended. Tk 9
    on macOS sends these instead of <MouseWheel> for a trackpad, which is the
    whole reason binding the wheel alone left two fingers dead."""
    assert gui._touchpad_deltas(_Touchpad(dx, dy)) == (dx, dy)


def test_a_touchpad_at_rest_asks_for_nothing():
    assert gui._touchpad_deltas(_Touchpad(0, 0)) == (0, 0)


def test_touchpad_deltas_are_pixels_not_notches():
    """A wheel notch is one line; a trackpad reports how far it actually
    travelled, and scrolling by a line per event would crawl."""
    assert gui._touchpad_deltas(_Touchpad(0, 40))[1] == 40


class _Tk86:
    """A widget from a Tk that predates <TouchpadScroll>, which is Tk 8.7.

    No real Tk here on purpose. What is being tested is the refusal, and a
    fake can refuse on a machine whose Tk knows the event perfectly well --
    which is every Mac this was written on, and is why the gap went unseen.
    """

    def __init__(self):
        self.bound: list[str] = []

    def bind(self, sequence, handler, add=None):
        if sequence == "<TouchpadScroll>":
            raise gui.tk.TclError('bad event type or keysym "TouchpadScroll"')
        self.bound.append(sequence)

    def bind_all(self, sequence, handler, add=None):
        return self.bind(sequence, handler, add)


def test_an_event_this_tk_does_not_know_is_declined_not_fatal():
    """Windows and most Linux ship Tk 8.6, where binding it raises.

    It raised from inside `_build`, so the window never reached the screen at
    all -- the whole driver was macOS-only over this one line.
    """
    widget = _Tk86()
    assert gui._bind_optional(widget, "<MouseWheel>", lambda e: None) is True
    assert gui._bind_optional(widget, "<TouchpadScroll>", lambda e: None) is False
    assert gui._bind_optional(widget, "<TouchpadScroll>", lambda e: None,
                              everywhere=True) is False
    # The wheel still got bound: the machines without the event are exactly the
    # ones a wheel covers.
    assert widget.bound == ["<MouseWheel>"]


def test_zoom_is_not_so_eager_that_a_flick_overshoots():
    """These events arrive many times a second, and each carries a handful of
    pixels. Too much per pixel and a flick takes the picture past 8x."""
    import math
    a_flick = math.exp(400 * gui._ZOOM_PER_PIXEL)
    assert a_flick < 20, f"a long flick would multiply by {a_flick:.0f}"


# -- zoom, which has to answer the hand ------------------------------------


def test_a_short_swipe_visibly_changes_the_zoom():
    """Spending one 1.15x notch per 60 px meant five full two-finger swipes to
    double the size, which reads as the gesture doing nothing at all."""
    import math
    for travel, least in ((20, 1.05), (100, 1.5)):
        assert math.exp(travel * gui._ZOOM_PER_PIXEL) > least, travel


def test_doubling_the_size_costs_a_swipe_not_five():
    import math
    pixels = math.log(2) / gui._ZOOM_PER_PIXEL
    assert 60 < pixels < 200, f"{pixels:.0f} px to double"


def test_zooming_in_and_out_by_the_same_travel_comes_home():
    import math
    there = math.exp(140 * gui._ZOOM_PER_PIXEL)
    back = math.exp(-140 * gui._ZOOM_PER_PIXEL)
    assert there * back == pytest.approx(1.0, abs=1e-9)


def test_a_wheel_notch_is_worth_more_than_a_trackpad_pixel():
    """A notch is a discrete click; a pixel is a fraction of a gesture."""
    import math
    assert gui._ZOOM_PER_NOTCH > math.exp(gui._ZOOM_PER_PIXEL)


# -- redrawing, which has to keep up with a gesture -------------------------


def test_the_frames_themselves_are_never_postponed():
    """Cancelling the pending redraw on every event sounds like coalescing and
    is not: a trackpad sends events right through a gesture and for most of a
    second of momentum after it, so each one pushed the redraw back and the
    picture did not move until everything stopped. That was the half-second.

    The sharp frame that follows a gesture *is* debounced, and should be, so
    this names the job that must not be -- the earlier version forbade
    `after_cancel` outright and broke the moment a settle timer was added,
    which is what comes of asserting on a string rather than on the rule."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._schedule_redraw)
    assert "after_cancel(self._redraw_job)" not in source, (
        "cancelling the pending frame is what made a gesture wait for its end")
    assert "_drawn_at" in source, "it has to know when it last drew"


def test_a_frame_budget_that_allows_a_smooth_gesture():
    """60 Hz. A redraw costs about 16 ms at a full window, so the throttle
    should not be what limits it."""
    assert 8 <= gui._FRAME_MS <= 33


def test_a_view_reads_the_coarsest_array_that_can_serve_it():
    """The scan's own pixels back the picture at every size now, but a view
    that does not need them still reads the reduced copy -- it is the coarsest
    level, so fit costs what it always did while a zoom gets real detail."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._pixels)
    assert "factor >= scale" in source, (
        "chosen by the scale being drawn -- at fit the zoom is zero, and "
        "choosing by it upscaled the reduced copy on a wide pane")
    loaded = inspect.getsource(gui.ScannerGui._loaded)
    assert "(1.0, self.current.image)" in loaded, (
        "the reduced copy has to be in the list, or fit reads more than it needs")


def test_the_scan_is_read_as_soon_as_a_picture_is_shown():
    """Not held back until a zoom asks for it: what is on screen should be the
    scan wherever it can be, with the copy filling the moment it takes to
    arrive."""
    import inspect
    assert "self._load_full(result)" in inspect.getsource(gui.ScannerGui._show)


def test_the_view_is_measured_against_one_array_only():
    """The zoom and the view are in working-copy coordinates whatever is being
    sampled. Rescaling them when the big array arrived meant the zoom crossed
    back under its own threshold, which swapped the array again -- a picture
    that jumped about as it passed 1:1."""
    import inspect
    assert "preview.orient(r.image, r.rotation, r.flipped)" in inspect.getsource(
        gui.ScannerGui._source)
    loaded = inspect.getsource(gui.ScannerGui._loaded)
    assert "grow" not in loaded, "the arrival of the big array must move nothing"


def test_the_loader_thread_never_touches_tk():
    """`after()` from another thread is not safe, and wrapping it in a
    try/except turned a visible failure into a silent one: the read finished,
    the callback never arrived, and the full-resolution view simply never
    appeared with nothing anywhere to say why. It hands the result back through
    a queue that the main loop already drains."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._load_full)
    assert "self._reads.put" in source
    assert "self.root" not in source, "the reading thread must not call Tk"
    assert "_reads" in inspect.getsource(gui.ScannerGui._pump), (
        "and the main loop has to collect it")


def test_a_moving_frame_is_drawn_coarse_and_a_still_one_sharp():
    """Both the sampling and the image Tk shows cost in proportion to the pixel
    count, so a moving frame draws a quarter of them. The only moment the
    detail is any use is the moment you have stopped moving."""
    import inspect
    assert gui._GESTURE_FACTOR >= 2
    source = inspect.getsource(gui.ScannerGui._redraw)
    assert "quick" in source and "_GESTURE_FACTOR" in source
    painting = inspect.getsource(gui.ScannerGui._paint)
    assert '"-zoom", coarse, coarse' in painting, (
        "a coarse frame has to be enlarged, or the picture would shrink")


def test_the_sharp_frame_follows_soon_enough_to_feel_immediate():
    assert 60 <= gui._SETTLE_MS <= 250


def test_the_sharp_frame_is_the_only_thing_that_waits():
    import inspect
    source = inspect.getsource(gui.ScannerGui._schedule_redraw)
    assert "after_cancel(self._settle_job)" in source
    assert "_settle_job" in source


# -- where a zoom pivots ----------------------------------------------------


def test_a_zoom_pivots_on_the_pointer():
    """Without it the picture scales about the middle of the canvas, so what
    you were looking at slides away exactly as you lean in on it. The
    arithmetic was right and the pivot was wrong."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._zoom_by)
    assert "anchor" in source
    assert "_source_at" in source


def test_the_pointer_is_recorded_when_a_gesture_arrives():
    import inspect
    source = inspect.getsource(gui.ScannerGui._scrolls)
    assert source.count("self._pointer") == 2, (
        "both the wheel and the trackpad have to say where they were")


def test_fit_is_the_floor():
    """Below it the picture only shrinks into the middle of an empty canvas,
    which is not a view of anything."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._zoom_by)
    assert "target <= fit" in source
    assert "self._zoom = 0.0" in source


def test_there_is_a_ceiling_and_it_is_past_one_to_one():
    assert gui._MAX_ZOOM > 1.0


def test_one_place_decides_where_the_picture_sits():
    """A pivot worked out from the last frame drawn creeps, because several
    throttled zoom steps can pass between redraws. Both the drawing and the
    pivot read the same live geometry."""
    import inspect
    for method in (gui.ScannerGui._redraw, gui.ScannerGui._source_at):
        assert "_geometry(" in inspect.getsource(method), method.__name__


def test_the_view_is_one_point_not_a_centre_and_a_padding():
    """The anchor is exact only because there is a single thing to solve for:
    which point of the picture sits at the corner of the canvas."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._zoom_by)
    assert "self._view = [focus[0] - anchor[0] / target" in source


def test_a_picture_may_be_pushed_partly_off_the_canvas():
    """Insisting the whole picture stay visible is what made it slide between
    fit and about 105%: there was room to show all of it, so it was moved to
    show all of it, and a point held under the pointer moved with it. Zooming
    into the top of something is a request to let the bottom go."""
    assert 0.0 < gui._OFF_CANVAS < 1.0
    import inspect
    source = inspect.getsource(gui.ScannerGui._clamped_view)
    assert "_OFF_CANVAS" in source


def test_fit_still_centres_what_it_shows():
    import inspect
    source = inspect.getsource(gui.ScannerGui._geometry)
    assert "/ 2" in source, "fit centres the picture on both axes"


def test_the_finer_array_is_sampled_with_a_smaller_scale():
    """One of `detail` pixels of the picture is `detail` of the scan's, so a
    step across the output covers more of them and the scale is divided. It was
    multiplied, which sampled a region sixteen times too small and blew it up
    -- at exactly 25% of a 3600 dpi scan, where the two arrays swap."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._redraw)
    assert "scale / detail" in source
    assert "scale * detail" not in source
    assert "x0 * detail" in source, "but the start is multiplied"


def test_the_coarsest_level_that_has_the_detail_is_the_one_read():
    """Going straight from the working copy to the scan meant decimating a
    strip four times wider than the view out of 142 MB -- 30 ms against the
    copy's 4, a step you could feel at exactly the scale where they met."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._pixels)
    assert "for factor, array in self._levels" in source
    assert "factor >= scale" in source


def test_a_moving_frame_asks_for_less_detail_as_well_as_fewer_pixels():
    """It draws a third of them, so a third of the detail is all that can
    reach the screen; asking for the sharp frame's level gathered from a finer
    array than anything shown could use."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._redraw)
    assert "self._pixels(scale / coarse)" in source


def test_the_picture_is_written_into_an_image_tk_already_knows():
    """Building a fresh PhotoImage every frame and binding it to a fresh canvas
    item cost 6.5 ms in `create_image` alone -- an image Tk has not seen before
    makes it start from scratch. Two thirds of a moving frame went there."""
    import inspect
    painting = inspect.getsource(gui.ScannerGui._paint)
    assert "_sized(" in painting, "the image is reused when it is already right"
    assert "tk.PhotoImage(data=" not in painting, (
        "allocating from data every frame is what this replaced")
    placing = inspect.getsource(gui.ScannerGui._place)
    assert "self.canvas.coords" in placing, "and the canvas item is moved, not remade"


def test_only_the_overlay_is_cleared_between_frames():
    """Deleting everything would take the picture item with it, and remaking
    that is the cost being avoided."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._redraw)
    assert 'self.canvas.delete("note")' in source
    assert 'self.canvas.delete("all")' not in source


def test_the_histogram_is_measured_off_the_ui_thread():
    """Counting a full 3600 dpi frame exactly is about a third of a second, and
    a window that locks up for that long each time you click along the
    filmstrip is its own kind of unhelpful."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._measure_histogram)
    assert "threading.Thread" in source
    assert "self._measured.put" in source
    assert "self._measured" in inspect.getsource(gui.ScannerGui._pump), (
        "and the main loop collects it")


def test_the_histogram_is_always_on_screen():
    """Not a window opened from a menu. "Is this against the ceiling" is the
    question being asked continuously while an exposure is judged, and a
    reading you have to go and ask for is a reading nobody takes."""
    import inspect
    assert not hasattr(gui, "_Histogram"), "the separate window is gone"
    assert not hasattr(gui.ScannerGui, "on_histogram")
    assert "Histogram" not in inspect.getsource(gui.ScannerGui._fill_result_menu)
    built = inspect.getsource(gui.ScannerGui._build_preview)
    assert "_HistogramPanel(top)" in built and "self.histogram.place()" in built
    # Placed, not packed: it floats over the picture rather than taking width
    # from it, and a canvas redraw cannot clear it.
    assert ".place(" in inspect.getsource(gui._HistogramPanel.place)


def test_a_measurement_overtaken_by_a_later_one_is_dropped():
    """One pass is measured twice -- the working copy, then the scan's own
    pixels. Without a token the coarse answer can land second and quietly
    replace the fine one, and clicking quickly along the filmstrip leaves the
    wrong frame's numbers on screen."""
    import inspect
    pump = inspect.getsource(gui.ScannerGui._pump)
    assert "if token != self._histogram_token:" in pump
    assert "continue" in pump
    # A counter, not the result's seq, which cannot tell the two apart.
    assert "self._histogram_token += 1" in inspect.getsource(
        gui.ScannerGui._measure_histogram)


def test_the_histogram_prefers_the_scans_own_pixels():
    """And says which it used, because a reduced copy answers a slightly
    different question about how much is against the ceiling."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._finest_pixels)
    assert "self._levels" in source
    assert "the scan's own" in source and "copy" in source
    # Which is why the arrival of those pixels measures again.
    assert "_measure_histogram" in inspect.getsource(gui.ScannerGui._loaded)


def test_infrared_is_not_in_the_histogram():
    """It is a dust measurement, not an exposure: how much of it sits at full
    scale says nothing about whether this frame was exposed well, and on
    traditional black and white it holds the picture over again at +0.97
    correlation with green -- a fourth curve tracing the third."""
    rgbi = np.zeros((4, 4, 4), np.uint16)
    assert gui.rgb_only(rgbi).shape[2] == 3
    assert len(gui._CHANNEL_INK) == 3, "and there is no ink for a fourth"
    assert gui._CHANNEL_NAMES == "RGB"


def test_dropping_infrared_leaves_every_other_shape_alone():
    """A prescan is RGB and a monochrome scan is one plane. Neither has an
    infrared plane to lose, and slicing one that is not there would be a
    silent change of picture."""
    rgb = np.zeros((4, 4, 3), np.uint16)
    assert gui.rgb_only(rgb) is rgb
    mono = np.zeros((4, 4), np.uint16)
    assert gui.rgb_only(mono) is mono


def test_infrared_is_dropped_before_it_is_counted_not_after():
    """`clipping` counts every pixel of every plane. Measuring a plane that is
    then thrown away is a quarter of the work for nothing, on the measurement
    that already costs a third of a second."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._measure_histogram)
    assert source.index("rgb_only(pixels)") < source.index("preview.histogram")


# -- picking frames off a contact sheet --------------------------------------


#
# `rewind_frames` used to be here: how far back to wind before scanning the
# ticked frames, counted from where the walk had *ended*. It is gone because a
# roll now goes to its first frame by the transport's own counter, and
# counting back from the walk's end scanned the wrong frames without a word
# whenever the film had moved since. What replaced it is tested below, and end
# to end in `test_the_sheet_scans_the_frames_it_showed_wherever_the_film_is`.


def test_a_sheets_roll_starts_on_its_first_ticked_frame():
    """And covers every frame to the last one ticked, so it ends there."""
    assert gui.chosen_span((4, 2)) == (2, 3)
    assert gui.chosen_span((7,)) == (7, 1)


def test_the_readout_counts_frames_the_way_a_roll_does():
    """It showed the counter itself: "frame position: 10" beside a roll that
    called the same picture frame 11."""
    assert gui.position_label(10) == "film on frame 11"
    assert gui.position_label(0) == "film on frame 1"
    assert gui.position_label(None) == "film on frame ?"


def test_the_roll_dialog_says_what_happens_to_the_film_first():
    """The report was a roll that started where the film was; the dialog now
    says where it will go, from where the transport last said it was."""
    back, cost = gui.seek_note(10, 1)
    assert "frame 11" in back and "winds back 10 frames to frame 1" in back
    assert cost == 0.0

    ahead, cost = gui.seek_note(0, 4)
    assert "advances 3 frames to frame 4" in ahead
    assert cost == pytest.approx(3 * gui.FORWARD_FRAME_S)

    here, cost = gui.seek_note(3, 4)
    assert "nothing moves" in here and cost == 0.0

    unknown, cost = gui.seek_note(None, 1)
    assert "asks the transport first" in unknown and cost == 0.0


def test_a_frame_no_strip_has_is_forecast_as_the_refusal_it_gets():
    """The seek refuses frame 45 before it reads or moves anything. The
    dialog said the film would advance 44 frames first, in about 202 s, and
    the operator confirmed a wind that never happened."""
    note, cost = gui.seek_note(0, 45)
    assert "refuse" in note and "costs nothing" in note, note
    assert "advances" not in note
    assert cost == 0.0
    assert "refuse" in gui.seek_note(None, 45)[0]
    # The last frame a strip is taken to have is still a wind, the next not.
    last = gui.LAST_PLAUSIBLE_POSITION + 1
    assert f"advances {last - 1} frames" in gui.seek_note(0, last)[0]
    assert "refuse" in gui.seek_note(0, last + 1)[0]


def test_a_wind_back_is_not_given_a_time_nobody_measured():
    """A forward frame is measured; a backward one never has been. An
    estimate that invents one reads exactly like one that measured it."""
    note, cost = gui.seek_note(9, 1)
    assert cost == 0.0
    assert "never been timed" in note


def _stub_window(survey, transport, submitted, tmp_path):
    """Enough of `ScannerGui` for `on_scan_chosen` to build its roll."""
    from rps7200.library import FilmNotes

    def var(value):
        return types.SimpleNamespace(get=lambda: value)

    return types.SimpleNamespace(
        busy=False, survey=survey, _transport=transport, _survey_start=1,
        _survey_predpi=300, orientations={},
        v_ir=var(False), v_fast_ir=var(True), v_film=var("negative"),
        v_meter=var("none"), v_correct=var(False), v_mono=var(False),
        v_reverse=var(False), v_mono_channel=var("G"),
        fields={"roll": var("sheet-roll")},
        _per_frame_seconds=lambda **kw: 60.0,
        _approved_note=lambda *a: "", _options_note=lambda *a: "",
        _write_approved=lambda *a: None, _notes=FilmNotes,
        _tags=lambda: (), _say=lambda *a: None,
        session=types.SimpleNamespace(submit=submitted.append,
                                      rolls=str(tmp_path / "rolls")),
    )


def _scan_the_sheet(monkeypatch, tmp_path, survey, numbers, film_at):
    """Commission `numbers` from `survey` the way the sheet's button does,
    then run that roll against a strip whose film is on `film_at`."""
    from conftest import StripScanner

    from rps7200.session import ScanSession

    submitted = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    options = {"dpi": "300", "predpi": "300", "ir": False, "fast_ir": True,
               "film": "negative", "meter": "none", "correct": False}
    gui.ScannerGui.on_scan_chosen(
        _stub_window(survey, film_at, submitted, tmp_path), tuple(numbers),
        (), options)
    assert len(submitted) == 1, "the sheet commissioned one roll"

    scanner = StripScanner(at=film_at)
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(submitted[0])
    s.shutdown()
    s.join(timeout=20)
    manifest = tmp_path / "rolls" / "sheet-roll" / "roll.json"
    if not manifest.exists():
        return []
    return [(f["number"], f["transport_position"]) for f in json.loads(
        manifest.read_text(encoding="utf-8"))["frames"]]


def test_the_sheet_scans_the_frames_it_showed_wherever_the_film_is(
        monkeypatch, tmp_path):
    """Walked 1 to 6 on positions 0 to 5, then the film moved on to 10 -- by
    the window's own button or the scanner's keys, it does not matter. Ticking
    2 and 4 used to rewind by the walk's span and scan positions 6 and 8 as
    frames 2 and 4, without a word."""
    survey = [types.SimpleNamespace(number=n, position=n - 1)
              for n in range(1, 7)]
    frames = _scan_the_sheet(monkeypatch, tmp_path, survey, (2, 4), 10)
    assert frames == [(2, 1), (4, 3)]


def test_a_walk_numbered_the_old_way_opens_on_the_strips_numbers(tmp_path):
    """rolls/2026-09-23, as it is on disk: a second walk, begun on the
    counter's 5, called that frame 1 -- and the roll beside it, begun on 0,
    scanned the same picture as its frame 6. Each mapped by its own recorded
    position, the two agree about which frame that is and that it is done."""
    folder = tmp_path / "2026-09-23"
    _write_survey(folder, frames=2)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["transport_position"] = record["number"] + 4
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "roll.json").write_text(json.dumps({
        "roll": "2026-09-23", "wanted": [1, 2, 3, 4, 5, 6],
        "frames": [{"number": n, "transport_position": n - 1, "done": True}
                   for n in range(1, 7)],
    }), encoding="utf-8")
    (folder / "approved.json").write_text(json.dumps({
        "roll": "2026-09-23",
        "frames": [{"number": 2, "offset_mm": 0.25, "rotation": 90}],
    }), encoding="utf-8")

    out = gui.read_survey(folder)
    assert [(r.number, r.position) for r in out["results"]] == [(6, 5), (7, 6)]
    assert 6 in out["scanned"] and 7 not in out["scanned"]
    # Decided against the roll's numbering, which began on 0: its frame 2 is
    # the strip's frame 2, whatever the later walk called its own frames.
    assert out["offsets"] == {2: pytest.approx(0.25)}


def test_a_roll_resumed_under_8a9ba17_reopens_with_the_frames_it_scanned(
        tmp_path):
    """8a9ba17 resumed a roll into the file its first run left, each run
    counting from wherever the film then was: frames 1-3 at positions 0-2,
    4-6 at 5-7. Read with one shift for the file, the window called frames 4
    and 5 of the strip done -- never scanned -- and 6 to 8 not, and offered
    to scan the wrong ones. The browser's list reads it the same way."""
    from conftest import resumed_by_8a9ba17

    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "roll.json").write_text(json.dumps(resumed_by_8a9ba17("tied")),
                                      encoding="utf-8")
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert sorted(out["scanned"]) == [1, 2, 3, 6, 7, 8]
    assert said == []
    assert sorted(gui.roll_summary(folder)["done"]) == [1, 2, 3, 6, 7, 8]


@pytest.mark.parametrize("which, scanned, said_about", [
    ("rewound", [6, 7, 8, 9], ["frame 7", "frame 8"]),
    ("reinserted", [4, 5, 6, 7, 8], ["frame 6"]),
    ("one-frame", [6, 7, 8], ["frame 7"]),
])
def test_an_8a9ba17_roll_that_went_over_a_place_twice_reopens_as_scanned(
        tmp_path, which, scanned, said_about):
    """Two 8a9ba17 runs into one roll with the film taken back between them,
    so some places were scanned twice. The window called strip frames 10 and
    11 of the rewound roll done, 11 of the reinserted one and 9 of the last,
    none of them ever scanned -- the one-frame roll's picture of frame 7 was
    that 9. It says which places hold two scans, and the browser's list
    agrees."""
    from conftest import resumed_by_8a9ba17

    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "roll.json").write_text(json.dumps(resumed_by_8a9ba17(which)),
                                      encoding="utf-8")
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert sorted(out["scanned"]) == scanned
    assert len(said) == len(said_about), said
    for line, place in zip(said, said_about):
        assert f"{place} of the strip" in line, line
    assert sorted(gui.roll_summary(folder)["done"]) == scanned


def test_a_roll_the_tool_wrote_reopens_as_one_run(tmp_path):
    """A roll.json from `tools/scan_roll.py` -- `dry_run` only inside
    `settings`, and one roll, since the tool wrote each afresh -- whose last
    frame's counter read one frame late, 5, 6, 7, 7. Read as two rolls, it
    kept that frame on 8 beside frame 3, so the window and the browser called
    strip frame 9 not scanned and offered it again."""
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "roll.json").write_text(json.dumps({
        "roll": "t", "started": "2026-09-01T10:00:00+00:00",
        "settings": {"dpi": 300, "dry_run": False, "start_at": 1,
                     "frames": 4, "prescan_resolution": 300},
        "frames": [{"number": n, "index": n - 1, "transport_position": p,
                    "registration": {}, "error": None, "done": True}
                   for n, p in enumerate([5, 6, 7, 7], start=1)],
    }), encoding="utf-8")
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert sorted(out["scanned"]) == [6, 7, 8, 9]
    assert sorted(gui.roll_summary(folder)["done"]) == [6, 7, 8, 9]
    assert len(said) == 1 and "frame 4 of t" in said[0], said


@pytest.mark.parametrize("positions, stale", [([72, 1, 2, 3], 1),
                                              ([0, 1, 72, 3], 3)])
def test_a_walk_that_recorded_the_stale_72_reopens_on_the_strips_numbers(
        tmp_path, positions, stale):
    """`docs/protocol.md` section 9's stale 72, recorded by one frame of an
    old walk. The sheet showed that frame as 73 -- past the end of every
    strip, so its tick could only ever be refused -- and nothing said why.
    It comes back where the rest of its walk puts it, and the log says so."""
    folder = tmp_path / "stale"
    _write_survey(folder, frames=4)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["dry_run"] = True                   # as the session writes a walk
    for record, position in zip(survey["frames"], positions):
        record["transport_position"] = position
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")

    said = []
    out = gui.read_survey(folder, say=said.append)
    assert [(r.number, r.position) for r in out["results"]] == list(
        zip([1, 2, 3, 4], positions))
    assert len(said) == 1, said
    assert f"frame {stale} of a-strip" in said[0], said
    assert "72, which no strip has" in said[0], said


def test_a_reopened_old_walk_sends_the_film_to_the_frame_it_showed(
        monkeypatch, tmp_path):
    """The reopen path, end to end: an old walk begun on the counter's 14
    (`registration-F`), the strip put back in -- counter 0 -- and its second
    frame ticked. The frame that walk saw on 15 is the one scanned."""
    folder = tmp_path / "registration-F"
    _write_survey(folder, frames=3)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["transport_position"] = record["number"] + 13
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")

    results = gui.read_survey(folder)["results"]
    ticked = [r.number for r in results if r.position == 15]
    frames = _scan_the_sheet(monkeypatch, tmp_path, results, ticked, 0)
    assert frames == [(16, 15)]


def test_a_decision_filed_on_the_strips_numbers_is_read_as_it_stands(tmp_path):
    """`approved.json` says when its numbers are places on the strip, so an old
    walk's shift is not applied to it a second time."""
    folder = tmp_path / "roll"
    _write_survey(folder, frames=2)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["transport_position"] = record["number"] + 4
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "approved.json").write_text(json.dumps({
        "roll": "roll", "numbering": "strip",
        "frames": [{"number": 7, "offset_mm": 0.25}],
    }), encoding="utf-8")
    assert gui.read_survey(folder)["offsets"] == {7: pytest.approx(0.25)}


# -- the window itself, where a display allows it ---------------------------
#
# The rest of this file tests the window's pure functions, deliberately: a
# suite that needs a display does not run everywhere. These two need real Tk
# widgets, because what they check is which controls are greyed out, so they
# skip rather than fail where there is no display.


@pytest.fixture
def window(tmp_path):
    tk = pytest.importorskip("tkinter")

    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()

    gui_mod = load_tool("gui")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"), verbose=False)
    session._open_scanner = lambda: DemoScanner("library", speed=1e9)
    app = gui_mod.ScannerGui(root, session, demo=True)
    root.update()
    try:
        yield app, root
    finally:
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_the_monochrome_controls_follow_the_film(window):
    """Enabled only for black and white, because reducing a colour negative or
    a slide to one channel throws the picture away rather than a redundant copy
    of it. And the view follows, so what is on screen is what will be written.
    """
    app, root = window

    for film in ("negative", "positive", "kodachrome"):
        app.v_film.set(film)
        app._sync_film()
        root.update()
        assert app.v_mono.get() is False, film
        assert str(app.c_mono.cget("state")) == "disabled", film
        assert str(app.b_mono_channel.cget("state")) == "disabled", film
        assert app.v_channel.get() == "RGB", film

    app.v_film.set("bw")
    app._sync_film()
    root.update()
    assert app.v_mono.get() is True
    assert str(app.c_mono.cget("state")) == "normal"
    assert str(app.b_mono_channel.cget("state")) == "readonly"
    # A single plane renders grey, so this is what puts a black and white
    # picture on screen for the prescan and the scan alike. The default
    # reduction is the average, whose view is MONO -- "avg" is not a plane.
    assert app.v_mono_channel.get() == MONO_AVERAGE
    assert app.v_channel.get() == "MONO"


def test_changing_the_monochrome_channel_changes_the_view(window):
    """Every setting the picker offers has to show what it will deliver --
    including the average, whose view is MONO rather than any one plane."""
    app, root = window
    app.v_film.set("bw")
    app._sync_film()
    for channel, expected in (("R", "R"), ("B", "B"), ("G", "G"),
                              (MONO_AVERAGE, "MONO")):
        app.v_mono_channel.set(channel)
        app._sync_mono_view()
        root.update()
        assert app.v_channel.get() == expected, channel


# -- the two live time estimates ---------------------------------------------
#
# _progress() and _update_roll_eta() are driven directly with controlled
# inputs rather than through a timed demo run: the numbers are real
# wall-clock math, and asserting on them through actual sleeps would be both
# slow and flaky. What a live run looks like was checked by hand against the
# demo backend before this was called done.


def test_the_pass_eta_is_empty_until_a_line_has_landed(window):
    """done == 0 says nothing has been measured yet -- there is no rate to
    interpolate from, so no number is better than a fabricated one."""
    app, root = window
    app._progress(0, 100)
    assert app.v_progress.get() == "0/100 lines"
    assert app.v_pass_eta.get() == ""


def test_the_pass_eta_appears_once_something_has_been_measured(window):
    app, root = window
    app._progress(0, 100)
    time.sleep(0.05)
    app._progress(40, 100)
    assert "left" in app.v_pass_eta.get()


def test_the_pass_eta_resets_for_a_new_pass(window):
    """A different total is a different pass -- 600 dpi does not inherit
    300's clock, or its first reading would be nonsense."""
    app, root = window
    app._progress(0, 300)
    time.sleep(0.05)
    app._progress(280, 300)               # nearly done, a slow measured rate
    slow_start = app._pass_started_at

    app._progress(10, 860)                # a new, larger pass begins
    assert app._pass_started_at is not None
    assert app._pass_started_at > slow_start
    assert app._pass_total_seen == 860


def test_the_pass_eta_resets_when_done_goes_backwards(window):
    """Belt and braces against the same total recurring by coincidence."""
    app, root = window
    app._progress(0, 300)
    app._progress(250, 300)
    first_start = app._pass_started_at
    time.sleep(0.02)
    app._progress(5, 300)                 # same total, but done went backwards
    assert app._pass_started_at > first_start


def test_the_roll_line_is_empty_with_no_roll_running(window):
    app, root = window
    assert app._roll_wall_start is None
    app._update_roll_eta()
    assert app.v_roll_eta.get() == ""


def test_the_roll_line_shows_the_rough_guess_before_any_frame(window):
    """The general estimate _on_roll would have made, on screen before the
    first line of the first frame has even come back."""
    app, root = window
    app._roll_wall_start = time.monotonic()
    app._roll_dry = False
    app._roll_frames_total = 6
    app._roll_frames_done = 0
    app._roll_seconds_per_frame = 90.0
    app._update_roll_eta()
    text = app.v_roll_eta.get()
    assert "0/6" in text
    assert "estimated" in text
    assert "9m" in text or "540" in text      # 6 x 90s = 540s = 9m 00s


def test_the_roll_line_switches_to_measured_after_a_frame(window):
    """The point of the feature: once real frames exist, the number comes
    from them rather than from estimate_seconds()."""
    app, root = window
    app._roll_wall_start = time.monotonic() - 30.0   # one frame, 30s ago
    app._roll_dry = False
    app._roll_frames_total = 3
    app._roll_frames_done = 1
    app._roll_seconds_per_frame = 30.0
    app._update_roll_eta()
    text = app.v_roll_eta.get()
    assert "1/3" in text
    assert "measured" in text
    assert "estimated" not in text


def test_an_open_ended_roll_states_pace_not_a_false_total(window):
    """No frame count was given, so there is nothing to count down to --
    saying a total anyway would be inventing one."""
    app, root = window
    app._roll_wall_start = time.monotonic() - 10.0
    app._roll_dry = False
    app._roll_frames_total = None
    app._roll_frames_done = 2
    app._roll_seconds_per_frame = 5.0
    app._update_roll_eta()
    text = app.v_roll_eta.get()
    assert "left" not in text
    assert "/frame" in text
    assert "elapsed" in text


def test_a_dry_run_says_walked_not_scanned(window):
    app, root = window
    app._roll_wall_start = time.monotonic()
    app._roll_dry = True
    app._roll_frames_total = 6
    app._roll_frames_done = 0
    app._roll_seconds_per_frame = 23.0
    app._update_roll_eta()
    assert "walked" in app.v_roll_eta.get()
    assert "scanned" not in app.v_roll_eta.get()


def _press_roll(app, monkeypatch, frames="3", start_at="1", answer=True):
    """Press Scan roll with these fields; return (dialog texts, jobs, errors)."""
    said, jobs, errors = [], [], []

    def ask(title, message, **kw):
        said.append(message)
        return answer

    monkeypatch.setattr(gui.messagebox, "askokcancel", ask)
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.v_frames.set(frames)
    app.v_startat.set(start_at)
    app.v_dryrun.set(True)
    app.on_roll()
    return said, jobs, errors


def test_the_roll_pace_is_timed_from_where_the_seek_landed(window,
                                                           monkeypatch):
    """The Roll button winds the film to its first frame inside the job, and
    the clock started at the press -- so a minute's wind back became frame
    1's time, and "left" read about 19 minutes on a 15-frame walk that had
    about five. It restarts when the session says where the seek put the
    film, which it does before the first frame."""
    from rps7200.session import Event

    app, root = window
    _press_roll(app, monkeypatch, frames="15")
    app._roll_wall_start -= 60.0                      # the wind back
    app._handle(Event(kind="transport", done=0))      # and where it landed
    assert time.monotonic() - app._roll_wall_start < 5.0
    later = app._roll_wall_start
    app._handle(Event(kind="transport", done=1))      # a frame's own report
    assert app._roll_wall_start == later, "only the seek restarts it"


def test_an_unknown_position_replaces_the_one_before_it(window):
    """After a report of "unknown" the readout said "film on frame ?" while
    the Roll dialog went on forecasting a wind from the older value."""
    from rps7200.session import Event

    app, root = window
    app._handle(Event(kind="transport", done=10))
    assert app._transport == 10
    app._handle(Event(kind="transport", done=-1))
    assert app.v_position.get() == "film on frame ?"
    assert app._transport is None
    assert "has not heard" in gui.seek_note(app._transport, 1)[0]


def test_a_results_counter_no_strip_has_is_not_a_forecast(window):
    """A pass carries the counter it was taken at, and the window keeps it for
    the Roll dialog. One that read 72 would have the dialog forecast a
    72-frame wind back -- the filter `_report_position` and `_move` apply
    holds here as well, and the frame heard before stands."""
    from rps7200.session import Event, Result

    app, root = window
    app._handle(Event(kind="transport", done=10))
    app._handle(Event(kind="result", result=Result(
        seq=1, kind="prescan", label="frame 3 prescan",
        image=np.zeros((8, 8, 3), np.uint8),
        meta={"resolution_dpi": 300}, position=72, number=3)))
    assert app._transport == 10


def test_the_window_leaves_refusing_a_far_frame_to_the_seek(window,
                                                            monkeypatch):
    """`start at` 45 was refused by the window, on the premise that the film
    would otherwise be wound first. It would not: the seek refuses a frame no
    strip has before it reads or moves anything, and says so on the
    failed-job path. One refusal, in the backend."""
    from conftest import StripScanner

    from rps7200 import session
    from rps7200.session import Event

    app, root = window
    app._handle(Event(kind="transport", done=0))
    said, jobs, errors = _press_roll(app, monkeypatch, start_at="45")
    assert errors == []
    assert [job.start_at for job in jobs] == [45]
    # And the dialog forecasts the refusal it will get, not "advances 44
    # frames -- about 202 s" and "Roughly 4m 31s" for a wind and frames
    # that never happen.
    assert "refuse" in said[0] and "costs nothing" in said[0], said[0]
    assert "advances" not in said[0] and "Roughly" not in said[0], said[0]
    film = StripScanner(at=0)
    with pytest.raises(session.FilmNotPlaced, match="frame 45 is past"):
        session.seek(film, 44)
    assert film.moves == [] and film.warmed == 0, "refused before anything"


def test_a_roll_to_the_end_of_the_strip_gives_a_pace_not_a_total(
        window, monkeypatch):
    """"Every frame to the end of the strip", then a figure for six frames,
    whatever the strip held."""
    app, root = window
    said, _jobs, _errors = _press_roll(app, monkeypatch, frames="0",
                                       answer=False)
    assert "every frame to the end of the strip" in said[0]
    assert "a frame" in said[0] and "no count to add up" in said[0]
    assert gui.roll_estimate(23.0, 6, 0.0) == "Roughly 2m 18s."
    assert gui.roll_estimate(23.0, 0, 0.0).startswith("Roughly 23 s a frame")


# -- the approved-offset helpers ---------------------------------------------
#
# Pure module-level functions, so they are tested directly rather than through
# a window. What they encode is that an offset shown to the operator must be a
# position the transport can actually reach.


def test_an_offset_snaps_to_something_the_transport_can_reach():
    assert gui.snap_offset(0.48) == pytest.approx(0.5116, abs=1e-4)
    assert gui.snap_offset(-0.48) == pytest.approx(-0.5116, abs=1e-4)


def test_an_offset_inside_the_unreachable_hole_becomes_zero():
    """Nothing exists between zero and one SLIDE command. Offering +0.14 mm
    would invite him to aim at a place that is not there."""
    assert gui.snap_offset(0.10) == 0.0
    assert gui.snap_offset(-0.10) == 0.0


def test_a_snapped_offset_can_always_be_planned_again():
    """The adjuster stores what snap_offset returns and the mover plans from
    it later. A value the planner would refuse on the way back is a number
    that works in the window and fails at the scanner."""
    from rps7200.session import plan_nudges

    for want in (-99.0, -8.5, -7.0, -1.5, -0.3, 0.0, 0.3, 1.5, 7.0, 8.5, 99.0):
        value = gui.snap_offset(want)
        plan_nudges(value)                    # must not raise
        assert gui.snap_offset(value) == pytest.approx(value, abs=1e-9)


def test_every_ticked_frame_gets_an_approval_including_untouched_ones():
    """An untouched frame still carries "leave it where I saw it, and here is
    the picture I saw" -- which is what lets the scan check it instead of
    assuming."""
    class R:
        def __init__(self, number, image, entry=""):
            self.number, self.image, self.entry = number, image, entry

    frames = [R(1, np.zeros((4, 4, 3), np.uint8), "library/a"),
              R(2, np.ones((4, 4, 3), np.uint8), "library/b"),
              R(3, np.zeros((4, 4, 3), np.uint8))]
    out = gui.approved_from_sheet(frames, (1, 3), {1: 0.48})

    assert [a.number for a in out] == [1, 3]
    assert out[0].offset_mm == pytest.approx(0.5116, abs=1e-4)
    assert out[1].offset_mm == 0.0, "an untouched frame is still an approval"
    assert out[0].reference is frames[0].image, "carries the pixels he saw"
    assert out[0].reference_entry == "library/a"
    assert out[1].reference_entry == ""


# -- reopening a survey ------------------------------------------------------
#
# survey.json has been written since rolls existed and was never read back, so
# a strip walked before closing the window had to be walked again -- four
# minutes of transport for nothing.


def _write_survey(folder, rotation=0, frames=3, offsets=None, rotations=None,
                  flipped=False, flips=None):
    """A survey folder shaped exactly as `ScanSession._roll` writes one."""
    from rps7200 import preview, tiff

    folder.mkdir(parents=True, exist_ok=True)
    records, images = [], {}
    for number in range(1, frames + 1):
        rng = np.random.default_rng(number)
        image = rng.integers(0, 255, (40, 60, 3), dtype=np.uint16).astype(np.uint8)
        images[number] = image
        # written turned, as the writer does
        tiff.write(str(folder / f"prescan{number:02d}.tif"),
                   preview.orient(image, rotation, flipped))
        records.append({"number": number, "index": number - 1,
                        "transport_position": number - 1,
                        "registration": {"contrast": 0.3}, "error": None,
                        "prescan": f"prescan{number:02d}.tif"})
    (folder / "survey.json").write_text(json.dumps({
        "roll": "a-strip", "start_at": 1, "prescan_resolution": 300,
        "rotation": rotation, "flipped": flipped, "frames": records,
    }), encoding="utf-8")
    if offsets or rotations or flips:
        numbers = sorted(set(offsets or {}) | set(rotations or {})
                         | set(flips or {}))
        (folder / "approved.json").write_text(json.dumps({
            "roll": "a-strip",
            "frames": [{"number": n,
                        "offset_mm": (offsets or {}).get(n, 0.0),
                        "rotation": (rotations or {}).get(n, 0),
                        "flipped": (flips or {}).get(n, False),
                        "reference_entry": f"library/frame{n}"}
                       for n in numbers],
        }), encoding="utf-8")
    return images


def test_a_survey_written_yesterday_opens_again(tmp_path):
    _write_survey(tmp_path / "roll")
    out = gui.read_survey(tmp_path / "roll")

    assert len(out["results"]) == 3
    assert [r.number for r in out["results"]] == [1, 2, 3]
    assert out["start_at"] == 1
    assert out["prescan_resolution"] == 300
    assert all(r.kind == "prescan" and r.levels is not None
               for r in out["results"])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_reopened_prescan_comes_back_in_the_films_own_orientation(tmp_path,
                                                                    rotation):
    """prescanNN.tif is written turned the way the screen had it. A reference
    has to be the film's own orientation or it will not correlate against a
    fresh pass -- and the turn is carried on the result instead, exactly as a
    live pass does it."""
    images = _write_survey(tmp_path / "roll", rotation=rotation)
    out = gui.read_survey(tmp_path / "roll")

    for result in out["results"]:
        assert np.array_equal(result.image, images[result.number]), (
            f"a {rotation} degree survey did not come back unturned")
        assert result.rotation == rotation


def test_positions_already_approved_come_back_with_it(tmp_path):
    _write_survey(tmp_path / "roll", offsets={2: 0.4833})
    out = gui.read_survey(tmp_path / "roll")

    assert out["offsets"] == {2: pytest.approx(0.4833)}
    entry = {r.number: r.entry for r in out["results"]}[2]
    assert entry is not None and entry.name == "frame2"


def test_a_survey_whose_prescans_are_gone_yields_nothing_rather_than_half(tmp_path):
    _write_survey(tmp_path / "roll")
    for stale in (tmp_path / "roll").glob("prescan*.tif"):
        stale.unlink()
    assert gui.read_survey(tmp_path / "roll")["results"] == []


def test_a_filed_prescans_entry_survives_as_a_string():
    """`Result.entry` is a Path once the prescan has been filed, and
    `Approved.reference_entry` is declared a str. A Path reaching json.dumps
    raised inside the Tk callback and the whole commission died silently --
    the operator pressed 'Scan chosen frames' and nothing happened at all.

    It only showed up against a real scanner: with the demo the filing had not
    finished by the time the sheet was commissioned, so entry was still None
    and the bug stayed hidden."""
    from pathlib import Path

    class R:
        def __init__(self, number, entry):
            self.number = number
            self.image = np.zeros((2, 2, 3), np.uint8)
            self.entry = entry

    filed = gui.approved_from_sheet([R(1, Path("library/an-entry"))], (1,), {})
    assert isinstance(filed[0].reference_entry, str)
    json.dumps({"e": filed[0].reference_entry})          # must not raise

    unfiled = gui.approved_from_sheet([R(1, None)], (1,), {})
    assert unfiled[0].reference_entry == ""


def test_failing_to_write_the_note_never_costs_the_scan(tmp_path):
    """approved.json records what was asked for. The scan is the work. A
    bookkeeping failure must not stop it -- which is exactly what happened."""
    import types

    said = []
    stub = types.SimpleNamespace(
        session=types.SimpleNamespace(rolls=str(tmp_path)),
        fields={"roll": types.SimpleNamespace(get=lambda: "a-roll")},
        _say=said.append,
    )

    class Approvedish:
        number = 1
        offset_mm = 0.5
        reference_entry = ""

    # Anything at all going wrong in here -- not just the Path that actually
    # did it -- has to be contained.
    def boom():
        raise RuntimeError("the roll name went missing")

    stub.fields["roll"].get = boom
    gui.ScannerGui._write_approved(stub, (Approvedish(),))

    assert said and "scanning anyway" in said[0]
    assert "went missing" in said[0]


def test_an_unticked_frame_says_so_in_words():
    """A prescan of a negative is very dark -- mean 16 of 255 across real
    surveys -- so a ring turning from amber to dark grey around a nearly black
    picture reads as an empty space. A frame that had merely been clicked off
    looked like a frame that had vanished, and went unscanned twice."""
    source = inspect.getsource(gui._ContactSheet._changed)
    assert "not scanning" in inspect.getsource(gui._ContactSheet._cell)
    assert "pack_forget" in source and "self._skips" in source
    # and the two states must not be a pair of dark greys
    assert gui._ContactSheet.CHOSEN != gui._ContactSheet.SKIPPED


def test_a_walked_frame_with_no_picture_is_reported_not_swallowed():
    """It cannot be ticked, so it silently does not get scanned. Saying
    nothing about that is how a roll comes back short."""
    source = inspect.getsource(gui._ContactSheet.__init__)
    assert "will not be scanned" in source


def test_a_quality_typed_by_hand_can_never_lose_a_scan():
    """The spinbox hands back a string and an operator can type in it. Clamped
    rather than refused: a scan is minutes of hardware and must not be lost to
    a typo in a quality field."""
    jpeg_quality = gui.jpeg_quality

    assert jpeg_quality("95") == 95
    assert jpeg_quality(80) == 80
    assert jpeg_quality("") == 95, "an empty box means the default"
    assert jpeg_quality("abc") == 95
    assert jpeg_quality(None) == 95
    assert jpeg_quality("5000") == 100, "clamped, not refused"
    assert jpeg_quality("-3") == 60


def test_the_save_dialog_opens_on_the_format_already_in_use():
    """Order is the point: the first entry is what the dialog offers, so an
    operator working in JPEG is not asked to pick it again every time."""
    save_as_types = gui.save_as_types

    assert save_as_types("jpeg")[0] == ("JPEG", "*.jpg")
    assert save_as_types("tiff")[0] == ("TIFF", "*.tif")
    for fmt in ("tiff", "jpeg"):
        assert len(save_as_types(fmt)) == 2, "both are always offered"


def test_the_output_label_stops_promising_a_tiff_when_it_is_a_jpeg():
    """The label is the only place the infrared cost is visible before a scan
    is taken, so it has to name it."""
    output_note = gui.output_note

    assert "TIFF" in output_note("tiff")
    jpeg = output_note("jpeg")
    assert "JPEG" in jpeg
    assert "infrared" in jpeg, "the plane the format has no room for"
    assert "DNG" in jpeg, "and where it goes instead, which is the actionable half"
    assert "negative" in jpeg, "it is not the inverted picture"


# -- turning a picture from wherever it is shown ----------------------------
#
# The menu used to live on the filmstrip alone, so the picture you were
# actually looking at -- full size in the middle, or laid out in the contact
# sheet -- was the one you could not turn.


def test_both_menus_are_filled_by_the_same_method():
    """One list, one place. A second copy would be wrong the first time an
    item was added to either."""
    for handler in (gui.ScannerGui.on_strip_menu, gui.ScannerGui.on_canvas_menu):
        assert "_fill_result_menu" in inspect.getsource(handler)
    filled = inspect.getsource(gui.ScannerGui._fill_result_menu)
    for item in ("Save as", "Rotate right", "Straighten",
                 "Show prescan", "Delete"):
        assert item in filled, item


def test_the_menu_over_the_picture_does_not_leave_a_drag_running():
    """Control-click is still Button-1 as far as `<B1-Motion>` is concerned,
    so a menu opened that way over a live drag pans the picture underneath it.
    The "break" matters too: without it the same click reaches `on_press`,
    which in aim mode moves film."""
    import types

    stub = types.SimpleNamespace(_drag=(1, 2, [0.0, 0.0]), current=None)
    assert gui.ScannerGui.on_canvas_menu(stub, None) == "break"
    assert stub._drag is None


def test_every_surface_asks_for_a_menu_the_same_three_ways():
    """X11 sends Button-3, a Mac trackpad's two-finger tap arrives as
    Button-2, and Control-click is for people with neither."""
    assert gui.MENU_EVENTS == ("<Button-3>", "<Button-2>", "<Control-Button-1>")
    for source in (inspect.getsource(gui.ScannerGui._build_preview),
                   inspect.getsource(gui._ContactSheet._cell)):
        assert "MENU_EVENTS" in source


class _Surveyed:
    """A surveyed frame, as much of one as `approved_from_sheet` reads."""

    def __init__(self, number, rotation=0):
        self.number = number
        self.rotation = rotation
        self.image = np.zeros((2, 2, 3), np.uint8)
        self.entry = None


def test_a_turn_in_the_sheet_reaches_the_scan():
    """The whole point of turning a frame there: it is scanned that way up."""
    approved = gui.approved_from_sheet(
        [_Surveyed(1), _Surveyed(2, 90)], (1, 2), {})
    turns = {a.number: a.rotation for a in approved}
    assert turns == {1: 0, 2: 90}, "one frame turned, the other untouched"


def test_a_frame_straightened_after_a_rotate_all_stays_straight():
    """Zero is a decision, not an absence.

    "Rotate all" moves the session default. A frame turned back to upright
    afterwards therefore cannot be recorded as "no turn set" and left to fall
    back on that default -- it would be scanned sideways, which is what
    happened the first time this was driven: four frames turned to 90, one
    straightened, and all four came out portrait.
    """
    approved = gui.approved_from_sheet(
        [_Surveyed(1, 0), _Surveyed(2, 90)], (1, 2), {})
    assert {a.number: a.rotation for a in approved} == {1: 0, 2: 90}
    # And the sheet keeps the zero rather than popping it, which is what makes
    # the record above say zero instead of saying nothing.
    orient = inspect.getsource(gui._ContactSheet._orient)
    assert "self.rotations[number] = result.rotation" in orient
    assert "self.flips[number] = result.flipped" in orient, "and the same for a flip"
    assert ".pop(" not in orient


def test_a_frame_nobody_turned_still_carries_the_orientation_it_is_shown_at():
    """A rotate in the filmstrip before the sheet was opened is just as much
    the operator's decision, and it is on the result already."""
    approved = gui.approved_from_sheet([_Surveyed(1, 270)], (1,), {})
    assert approved[0].rotation == 270


def test_a_turn_survives_closing_the_window(tmp_path):
    """A position set by hand already survives a reopen. An orientation is the
    same kind of decision and costs the same to make again."""
    _write_survey(tmp_path / "roll", offsets={2: 0.4833}, rotations={2: 270})
    out = gui.read_survey(tmp_path / "roll")

    assert out["rotations"] == {2: 270}
    assert out["offsets"] == {2: pytest.approx(0.4833)}


def test_a_frame_turned_by_itself_is_not_confused_with_the_rolls_turn(tmp_path):
    """`prescanNN.tif` is un-rotated by the manifest's single `rotation`, so
    that number has to keep describing the file on disk. A per-frame turn is
    recorded separately and laid over the results afterwards."""
    images = _write_survey(tmp_path / "roll", rotation=90, rotations={2: 180})
    out = gui.read_survey(tmp_path / "roll")

    for result in out["results"]:
        assert np.array_equal(result.image, images[result.number]), (
            "the pixels still come back in the film's own orientation")
        assert result.rotation == 90, "and every result carries the roll's turn"
    assert out["rotations"] == {2: 180}, "the frame's own turn arrives beside it"
    assert "self.rotations[result.number]" in inspect.getsource(
        gui._ContactSheet.__init__), "which the sheet then lays over the result"


def test_a_cell_is_drawn_by_the_same_code_that_redraws_it():
    """Two ways to build one thumbnail is two ways for it to disagree with
    what will be scanned."""
    assert "self._render(result)" in inspect.getsource(gui._ContactSheet._cell)
    assert "self._render(result)" in inspect.getsource(gui._ContactSheet._orient)


def test_turning_every_frame_is_one_redraw_and_one_line_in_the_log():
    """Seventeen of each for a seventeen-frame strip is a stutter and a log
    nobody can read."""
    every = inspect.getsource(gui._ContactSheet._all)
    assert every.count("_reshow") == 1
    assert every.count("_say") == 1
    assert "_reshow" not in inspect.getsource(gui._ContactSheet._orient)


def test_turning_every_frame_also_sets_what_the_next_scan_follows():
    """A whole roll one way up is the ordinary case; saying it once should be
    enough for anything scanned outside the sheet too."""
    every = inspect.getsource(gui._ContactSheet._all)
    assert "self.gui.rotation = last.rotation" in every
    assert "self.gui.session.rotation = last.rotation" in every
    assert "self.gui.rotation" not in inspect.getsource(gui._ContactSheet._one)


def test_a_frame_comes_back_shown_the_way_it_was_written():
    """A roll that returns a picture the filmstrip draws one way up and the
    file on disk holds another is saying the file is something it is not."""
    import types

    stub = types.SimpleNamespace(
        rotation=0, flip=False, orientations={("frame", 2): (90, True)},
        results=[], survey=[], _surveying=False, _transport=None,
        _show=lambda _r: None, _redraw_strip=lambda: None,
    )
    stub._arrange = lambda r, s=None: gui.ScannerGui._arrange(stub, r, s)
    stub.remember_arrangement = lambda r: gui.ScannerGui.remember_arrangement(
        stub, r)

    def result(kind, number, meta=None):
        return types.SimpleNamespace(
            kind=kind, number=number, seq=number, image=None, position=None,
            meta=meta or {})

    turned = result("frame", 2)
    gui.ScannerGui._add_result(stub, turned)
    assert turned.rotation == 90
    assert turned.flipped is True, "and a mirror travels the same road"

    plain = result("frame", 1)
    gui.ScannerGui._add_result(stub, plain)
    assert plain.rotation == 0, "a frame nobody turned follows the session"
    assert plain.flipped is False

    # A prescan of frame 2 is a pass over the same photograph, so it is shown
    # the way that photograph was said to be -- which is the whole point of
    # keying this by picture rather than by pass.
    reference = result("prescan", 2)
    gui.ScannerGui._add_result(stub, reference)
    assert (reference.rotation, reference.flipped) == (90, True)


def test_a_new_strip_does_not_inherit_the_last_ones_orientations():
    """The numbers start again at 1 for a different film. Keeping them would
    turn whatever happens to land on frame 2 of the next roll."""
    walk = inspect.getsource(gui.ScannerGui.on_roll)
    assert "self.orientations = {}" in walk
    assert walk.index("self.survey = []") < walk.index("self._surveying = True")


# -- the picture and the panel share the canvas ------------------------------


class _FakeCanvas:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h


def _area(width, footprint, height=400):
    import types
    stub = types.SimpleNamespace(
        canvas=_FakeCanvas(width, height),
        histogram=types.SimpleNamespace(footprint=lambda: footprint))
    return gui.ScannerGui._picture_area(stub)


def test_the_picture_is_laid_out_beside_the_histogram_not_under_it():
    """The panel floats over the canvas, so without this the top right of
    every frame sits behind a chart."""
    assert _area(1000, 286) == (714, 400)


def test_a_pane_too_narrow_to_share_is_overlapped_rather_than_emptied():
    """A picture too small to judge anything by is worse than one with a
    chart in the corner of it."""
    assert _area(400, 286) == (200, 400), "at most half is ever given up"
    assert _area(2, 286)[0] >= 1, "and never all of it"


def test_nothing_lays_out_a_picture_against_the_raw_canvas():
    """One place decides how big the picture may be. A size read straight off
    the canvas is one that has not heard about the panel, and the picture
    would go back under it for that one operation -- a zoom that pivots on a
    point the redraw puts somewhere else."""
    import inspect
    for name in ("_redraw", "_source_at", "_zoom_by", "_set_zoom",
                 "on_double_click"):
        source = inspect.getsource(getattr(gui.ScannerGui, name))
        assert "canvas.winfo_width()" not in source, name
        assert "_picture_area()" in source, name


def test_the_panel_leaves_the_same_gap_on_both_sides_of_itself():
    """It is inset from the corner, and the picture stops the same distance
    short of it -- otherwise the two touch and read as one object."""
    import inspect
    assert "self.MARGIN * 2" in inspect.getsource(gui._HistogramPanel.footprint)
    placed = inspect.getsource(gui._HistogramPanel.place)
    assert "x=-self.MARGIN" in placed and "y=self.MARGIN" in placed


# -- the filmstrip stays where it was put ------------------------------------


class _FakeStrip:
    """Enough of a canvas for `_keep_in_strip`, recording where it was sent."""

    def __init__(self, width, left=0.0):
        self._width, self._left = width, left
        self.moved = []

    def update_idletasks(self):
        pass

    def winfo_width(self):
        return self._width

    def canvasx(self, _x):
        return self._left

    def xview_moveto(self, fraction):
        self.moved.append(fraction)


def _scroll(span, total, width, left=0.0):
    import types
    strip = _FakeStrip(width, left)
    gui.ScannerGui._keep_in_strip(types.SimpleNamespace(strip=strip), span, total)
    return strip.moved


def test_clicking_a_frame_already_in_view_does_not_scroll():
    """The bug this replaces: every redraw jumped to the end, so clicking the
    first frame of a long strip showed you the last one -- the picture changed
    to the frame asked for and the strip scrolled away from it, leaving the
    highlight off screen and no sign of what had been chosen."""
    assert _scroll((100, 250), 2700, 900, left=0.0) == []
    assert _scroll((1000, 1150), 2700, 900, left=950.0) == []


def test_a_frame_off_to_the_left_is_scrolled_back_to():
    moved = _scroll((100, 250), 2700, 900, left=900.0)
    assert len(moved) == 1 and 0 <= moved[0] < 900 / 2700


def test_a_frame_off_to_the_right_is_scrolled_forward_to():
    moved = _scroll((2500, 2650), 2700, 900, left=0.0)
    assert len(moved) == 1
    # Far enough that its end is in view, and no further.
    assert moved[0] * 2700 + 900 >= 2650
    assert moved[0] * 2700 <= 2500


def test_a_new_pass_still_brings_the_end_of_the_strip_into_view():
    """It falls out of the same rule: a pass that has just arrived is the
    selected one and it is off the right-hand end."""
    moved = _scroll((2600, 2750), 2760, 900, left=0.0)
    assert moved and moved[0] * 2760 + 900 >= 2750


def test_a_strip_that_fits_is_shown_from_the_start():
    assert _scroll((100, 250), 600, 900) == [0.0]


def test_a_strip_with_nothing_selected_is_left_alone():
    assert _scroll(None, 2700, 900) == []


def test_the_strip_no_longer_jumps_to_the_end_on_every_redraw():
    import inspect
    source = inspect.getsource(gui.ScannerGui._redraw_strip)
    assert "xview_moveto(1.0)" not in source
    assert "_keep_in_strip" in source


# -- a mirror, carried the same road as the turn -----------------------------


def test_a_flip_in_the_sheet_reaches_the_scan():
    approved = gui.approved_from_sheet(
        [_Surveyed(1), _Surveyed(2)], (1, 2), {})
    assert [a.flipped for a in approved] == [False, False]

    mirrored = _Surveyed(2)
    mirrored.flipped = True
    assert gui.approved_from_sheet([mirrored], (2,), {})[0].flipped is True


def test_a_flip_survives_closing_the_window(tmp_path):
    """The same round trip a hand-set position and a turn already get."""
    _write_survey(tmp_path / "roll", rotations={2: 90}, flips={2: True, 3: False})
    out = gui.read_survey(tmp_path / "roll")
    assert out["flips"] == {2: True, 3: False}, "and an explicit False survives"


@pytest.mark.parametrize("flipped", [False, True])
def test_a_reopened_prescan_comes_back_as_the_film_sat(tmp_path, flipped):
    """`prescanNN.tif` is written arranged the way the screen had it, and a
    reference has to be the film's own orientation or it will not correlate
    against a fresh pass. Un-orienting is not the same as orienting by the
    opposite, because a mirror and a turn do not commute."""
    images = _write_survey(tmp_path / "roll", rotation=90, flipped=flipped)
    out = gui.read_survey(tmp_path / "roll")
    for result in out["results"]:
        assert np.array_equal(result.image, images[result.number])
        assert result.rotation == 90 and result.flipped is flipped


def test_the_aim_comes_back_through_the_flip_as_well_as_the_turn():
    """The one place the arrangement is not cosmetic: the transport moves along
    the scanner's own x axis, so a click on a mirrored prescan that was only
    un-rotated would send the film the wrong way."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._aim)
    assert "preview.unorient_point(" in source
    assert "self.current.flipped" in source


def test_one_phrasing_says_how_a_pass_is_arranged():
    """The caption over the picture and the line in the log underneath it
    cannot then describe the same frame two different ways."""
    import types
    assert gui._arrangement(types.SimpleNamespace(rotation=0, flipped=False)) \
        == "as the scanner sent it"
    assert gui._arrangement(types.SimpleNamespace(rotation=90, flipped=False)) \
        == "90°"
    assert gui._arrangement(types.SimpleNamespace(rotation=0, flipped=True)) \
        == "flipped"
    assert gui._arrangement(types.SimpleNamespace(rotation=180, flipped=True)) \
        == "180°, flipped"


def test_a_turn_and_a_flip_carry_over_by_the_same_route():
    """Which way round the film went in does not change between one frame and
    the next, so both follow the pass they were set on."""
    import inspect
    for name in ("on_rotate", "on_flip"):
        assert "self._carry(" in inspect.getsource(getattr(gui.ScannerGui, name))
    carry = inspect.getsource(gui.ScannerGui._carry)
    for line in ("self.rotation = result.rotation", "self.flip = result.flipped",
                 "self.session.rotation", "self.session.flip"):
        assert line in carry, line


def test_nothing_arranges_a_picture_with_only_half_the_answer():
    """Every place a picture is arranged takes both, through `preview.orient`.
    A `preview.rotate` left behind is a view that disagrees with the file
    written from it."""
    import inspect
    source = inspect.getsource(gui)
    assert "preview.rotate(" not in source, (
        "the window arranges pictures with preview.orient, not preview.rotate")
    assert "preview.unrotate_point(" not in source


def test_flipping_all_agrees_the_strip_rather_than_swapping_each():
    """A toggle would leave a half-mirrored strip still half-mirrored, which
    is the one thing an operator reaching for "all" is trying to fix."""
    import inspect
    every = inspect.getsource(gui._ContactSheet._flip_all)
    assert "not all(r.flipped for r in self.frames)" in every
    orient = inspect.getsource(gui._ContactSheet._orient)
    assert "if flip is not None:" in orient and "result.flipped = flip" in orient
    assert "not result.flipped" not in orient, "the state is passed in, not toggled here"


def test_flipping_all_twice_lands_where_it_started():
    """Which is what makes the menu item that says "unflip all" do that."""
    import inspect
    assert "Unflip all" in inspect.getsource(gui._ContactSheet.on_cell_menu)


# -- the keyboard ------------------------------------------------------------


def _window_actions():
    """The main window's dispatch table, without building a window."""
    import types
    stub = types.SimpleNamespace(
        current=None, busy=False, v_invert=None, v_channel=None,
        on_rotate=lambda *a: None, on_flip=lambda *a: None,
        on_save_as=lambda *a: None, on_show_prescan=lambda *a: None,
        on_delete=lambda *a: None, on_contact_sheet=lambda: None,
        on_stop=lambda: None, on_shortcuts=lambda: None,
        _zoom_by=lambda *a: None, _set_zoom=lambda *a: None,
        _finest=lambda: 1.0, _walk=lambda *a: None, _jump=lambda *a: None,
        _on_current=lambda *a: None, _straighten=lambda: None,
        _toggle_invert=lambda: None, _cycle_channel=lambda *a: None,
        on_prescan=lambda: None, on_scan=lambda: None, on_roll=lambda: None,
        on_save_all=lambda: None, _confirm_then=lambda *a: None,
        _prescan_cost=lambda: "", _scan_cost=lambda: "",
    )
    return gui.ScannerGui._actions(stub)


def test_every_action_in_the_table_has_something_to_do():
    """An id in `shortcuts.ACTIONS` with no handler is a key that silently
    does nothing, and the editor would still offer it."""
    handled = set(_window_actions())
    for scope, owner in (("sheet", gui._ContactSheet),
                         ("adjuster", gui._FrameAdjuster)):
        import inspect
        source = inspect.getsource(owner._actions)
        for action in shortcuts.ACTIONS:
            if action.scope == scope:
                assert f'"{action.id}"' in source, action.id
    for action in shortcuts.ACTIONS:
        if action.scope == "window":
            assert action.id in handled, action.id


def test_no_shortcut_moves_film_or_calibrates():
    """The rule this table is written under. There is no undo for a moved
    negative or a wedged device, so no key reaches those on any terms."""
    import inspect
    sources = [inspect.getsource(gui.ScannerGui._actions),
               inspect.getsource(gui._ContactSheet._actions),
               inspect.getsource(gui._FrameAdjuster._actions)]
    for forbidden in shortcuts.NEVER_BOUND:
        for source in sources:
            assert forbidden not in source, forbidden


def test_every_key_that_starts_a_pass_asks_first():
    """The premise the old, stronger rule rested on was that `on_prescan` and
    `on_scan` "submit their job immediately, with no confirmation". They still
    do -- that is right for a button, where reaching for it is the decision --
    so the *keys* go through `_confirm_then` instead. `on_roll` is the exception
    because it asks its own question already, and asking twice would train the
    habit of dismissing both.

    This is the test that keeps the relaxation honest: without it, a later
    edit could point the key straight at `on_scan` and nothing would notice."""
    import inspect
    # Whitespace-collapsed, because the table wraps these calls across lines.
    table = " ".join(inspect.getsource(gui.ScannerGui._actions).split())
    assert '"prescan": lambda: self._confirm_then(' in table
    assert '"scan": lambda: self._confirm_then(' in table
    assert '"roll": self.on_roll' in table
    assert "askokcancel" in inspect.getsource(gui.ScannerGui.on_roll), \
        "roll is unwrapped only because it asks for itself"
    assert "askokcancel" in inspect.getsource(gui.ScannerGui._confirm_then)


def test_confirming_a_key_actually_gates_it():
    """`_confirm_then` must not run the action when the answer is no, and must
    not reach the scanner at all while one is already running."""
    import types
    ran = []
    stub = types.SimpleNamespace(
        busy=False, root=None,
        _say=lambda *a: None,
    )
    answers = iter([False, True])
    real = gui.messagebox.askokcancel
    gui.messagebox.askokcancel = lambda *a, **k: next(answers)
    try:
        gui.ScannerGui._confirm_then(stub, "a scan", lambda: "cost",
                                     lambda: ran.append("went"))
        assert ran == [], "a no still started it"
        gui.ScannerGui._confirm_then(stub, "a scan", lambda: "cost",
                                     lambda: ran.append("went"))
        assert ran == ["went"]
        stub.busy = True
        gui.ScannerGui._confirm_then(stub, "a scan", lambda: "cost",
                                     lambda: ran.append("again"))
        assert ran == ["went"], "it asked while the scanner was working"
    finally:
        gui.messagebox.askokcancel = real


def test_stop_is_the_one_exception_and_only_while_something_runs():
    """`request_stop` is cooperative and always safe -- but the log is
    evidence, and "finishing what is already running" with nothing running is
    a line that will be read back one day and believed."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._actions)
    assert "self.on_stop() if self.busy else None" in source


def test_a_key_does_nothing_while_a_text_field_has_the_focus():
    """Without this, typing "rotate" into the subject field turns the picture
    four times and deletes a pass on the "e"."""
    import inspect
    import types
    guard = inspect.getsource(gui.ScannerGui._typing)
    for widget in ("tk.Entry", "ttk.Entry", "tk.Text", "tk.Spinbox",
                   "ttk.Spinbox", "ttk.Combobox"):
        assert widget in guard, widget

    ran = []
    stub = types.SimpleNamespace(_typing=lambda: True)
    bare = gui.ScannerGui._runner(stub, lambda: ran.append(1), "<Key-r>")
    assert bare(None) is None and ran == []
    stub._typing = lambda: False
    assert bare(None) == "break" and ran == [1]


def test_a_modified_key_fires_even_while_a_text_field_has_the_focus():
    """⌘S in the middle of typing a subject line is a save, and every other
    application treats it as one. Refusing it would be this window inventing a
    rule of its own."""
    import types
    ran = []
    stub = types.SimpleNamespace(_typing=lambda: True)
    modified = gui.ScannerGui._runner(
        stub, lambda: ran.append(1), f"<{shortcuts.ACCEL}-Key-s>")
    assert modified(None) == "break" and ran == [1]


def test_the_binding_tells_the_handler_which_key_it_is():
    """Or the handler cannot know whether to stand aside for a text field."""
    import inspect
    for source in (inspect.getsource(gui.ScannerGui._bind_shortcuts),
                   inspect.getsource(gui._ContactSheet.rebind),
                   inspect.getsource(gui._FrameAdjuster.rebind)):
        assert "_runner(run, sequence)" in source


def test_rebinding_takes_the_old_key_off_the_window():
    """Otherwise the old sequence keeps firing beside the new one, and a key
    the operator deliberately moved still does the thing they moved it from."""
    import inspect
    for owner in (gui.ScannerGui._bind_shortcuts, gui._ContactSheet.rebind,
                  gui._FrameAdjuster.rebind):
        source = inspect.getsource(owner)
        assert "unbind(sequence)" in source
        assert "self._bound = []" in source


def test_keys_are_bound_on_the_window_and_not_on_everything():
    """A widget's bindtags run widget, class, toplevel, all -- so a binding on
    the window catches a key pressed anywhere in it and only in it. With
    `bind_all`, `r` in the contact sheet would rotate the preview underneath
    it as well."""
    import inspect
    # The call, not the word: the docstring explains why bind_all is wrong.
    for owner in (gui.ScannerGui._bind_shortcuts, gui._ContactSheet.rebind,
                  gui._FrameAdjuster.rebind):
        source = inspect.getsource(owner)
        assert ".bind_all(" not in source, owner.__qualname__
    assert "self.root.bind(" in inspect.getsource(gui.ScannerGui._bind_shortcuts)
    assert "self.top.bind(" in inspect.getsource(gui._ContactSheet.rebind)
    assert "self.top.bind(" in inspect.getsource(gui._FrameAdjuster.rebind)


def test_only_the_changed_keys_reach_the_settings_file():
    import inspect
    source = inspect.getsource(gui.ScannerGui.set_keys)
    assert "shortcuts.overrides_from(self.keys)" in source
    assert "shortcuts" in inspect.getsource(gui.ScannerGui._remember)
    assert "shortcuts.resolve(" in inspect.getsource(gui.ScannerGui._restore)


def test_the_settings_file_has_somewhere_to_put_them():
    from rps7200 import settings
    assert "shortcuts" in settings.SECTIONS


# -- what the contact sheet's keys move --------------------------------------


def test_the_sheet_knows_which_cell_the_keyboard_is_on():
    """It had no notion of "this one" at all before there were keys: a click
    ticked a picture and nothing was current, so there was nothing for an
    arrow to move."""
    import inspect
    assert "self.selected = 0" in inspect.getsource(gui._ContactSheet.__init__)
    move = inspect.getsource(gui._ContactSheet._move)
    assert "self._select(" in move


def test_selection_and_ticking_are_shown_as_two_different_things():
    """A cell can be selected and not scanned, or scanned and not selected."""
    import inspect
    paint = inspect.getsource(gui._ContactSheet._paint_rings)
    assert "background=colour" in paint, "the ring colour says whether it scans"
    assert "highlightbackground=" in paint, "the outline says where the keyboard is"
    assert gui._ContactSheet.SELECTED not in (gui._ContactSheet.CHOSEN,
                                              gui._ContactSheet.SKIPPED)


def test_moving_the_selection_does_not_make_the_grid_jump():
    """The outline is always there and only changes colour, so every cell
    keeps the same size whether it is selected or not."""
    import inspect
    paint = inspect.getsource(gui._ContactSheet._paint_rings)
    assert "highlightthickness=2" in paint
    assert "highlightthickness=0" not in paint


def test_the_sheet_scrolls_only_as_far_as_it_has_to():
    """The same rule the filmstrip follows: a sheet that jumped somewhere on
    every keypress would lose the operator's place rather than keep it."""
    import inspect
    source = inspect.getsource(gui._ContactSheet._scroll_to)
    assert "if total <= height:" in source and "return" in source
    assert "yview_moveto" in source


# -- the frame position window -----------------------------------------------


def test_return_keeps_the_frame_and_moves_on():
    """The offset is already recorded as the picture is dragged, so there is
    nothing here to save. What Return does is say yes: tick it for scanning,
    and show the next one."""
    import inspect
    source = inspect.getsource(gui._FrameAdjuster._accept)
    assert source.index("self.v_tick.set(True)") < source.index("self._go(1)")
    assert "self._tick_changed()" in source


def test_the_last_frame_closes_the_window_rather_than_sitting_there():
    """There is nowhere further to go, and leaving it open invites a press
    that does nothing."""
    import inspect
    source = inspect.getsource(gui._FrameAdjuster._accept)
    assert "self.index >= len(self.sheet.frames) - 1" in source
    assert "self.top.destroy()" in source


def test_every_menu_item_that_has_a_key_shows_it():
    """The menu is how anybody finds out a key exists -- nobody reads a
    shortcut list first -- so an item without its key on it is a key nobody
    will ever learn."""
    import inspect
    preview_menu = inspect.getsource(gui.ScannerGui._fill_result_menu)
    for action_id in ("save_as", "rotate_right", "rotate_left", "rotate_180",
                      "straighten", "flip", "show_prescan", "delete_pass"):
        assert f'accelerator=self.accelerator("{action_id}")' in preview_menu \
            or f'"{action_id}"' in preview_menu, action_id

    cell_menu = inspect.getsource(gui._ContactSheet.on_cell_menu)
    for action_id in ("sheet_rotate_right", "sheet_rotate_left",
                      "sheet_rotate_180", "sheet_straighten", "sheet_flip",
                      "sheet_toggle", "sheet_adjust", "sheet_show"):
        assert action_id in cell_menu, action_id


def test_an_item_with_no_key_shows_nothing_rather_than_a_dash():
    """A menu is a list of things you can do. An em dash in the accelerator
    column reads as a key you cannot make out rather than as the absence of
    one -- the editor is the place that has to say "no key"."""
    import types
    stub = types.SimpleNamespace(
        keys={"flip": "", "rotate_right": f"<{shortcuts.ACCEL}-Key-r>"})
    assert gui.ScannerGui.accelerator(stub, "flip") == ""
    assert gui.ScannerGui.accelerator(stub, "missing_entirely") == ""
    assert gui.ScannerGui.accelerator(stub, "rotate_right") == \
        shortcuts.accelerator_text(f"<{shortcuts.ACCEL}-Key-r>")


def test_a_menu_gets_the_form_tk_parses_not_the_one_a_person_reads():
    """Tk parses the accelerator itself, looking for modifier names, and draws
    the glyphs. Given "⌘R" it finds no name it knows, takes the whole string
    as a key equivalent and draws the first character only -- which put a lone
    ⌘ in the menu with no letter beside it."""
    import types
    stub = types.SimpleNamespace(keys={"r": f"<{shortcuts.ACCEL}-Key-r>"})
    shown = gui.ScannerGui.accelerator(stub, "r")
    # Only Aqua's Tk parses this string. Everywhere else it is printed as it
    # is given, so there the finished "Ctrl" is what belongs in a menu.
    assert ("Command" if sys.platform == "darwin" else "Ctrl") in shown, shown
    assert "R" in shown, shown
    assert "\u2318" not in shown, "a glyph here is the bug this fixes"
    # And the editor, which draws its own label, keeps the readable form --
    # the glyph on a Mac, the word everywhere else.
    readable = "\u2318" if sys.platform == "darwin" else "Cmd"
    assert readable in shortcuts.describe("<Command-Key-r>")


def test_the_menu_shows_the_key_as_it_is_now_not_as_it_shipped():
    """A rebind has to reach the menu, and the menu is rebuilt on every
    right-click -- so it reads `self.keys` rather than the defaults."""
    import inspect
    assert "self.keys.get(action_id" in inspect.getsource(
        gui.ScannerGui.accelerator)


def test_right_clicking_a_cell_selects_it_first():
    """Otherwise the menu offers "Rotate right, R" over one frame while R
    turns a different one: the key acts on the selection and the menu on what
    was clicked, and the two disagreeing about the same item is worse than
    either alone."""
    import inspect
    source = inspect.getsource(gui._ContactSheet.on_cell_menu)
    assert source.index("self._select(index)") < source.index("self.menu.delete")


def test_the_sheet_offers_the_same_turns_the_window_does():
    """A frame that can be turned 180° or straightened from the filmstrip and
    not from the sheet is a gap with no reason behind it."""
    sheet = {a.id for a in shortcuts.ACTIONS if a.scope == "sheet"}
    for what in ("rotate_right", "rotate_left", "rotate_180", "straighten",
                 "flip"):
        assert f"sheet_{what}" in sheet, what


# -- how far one arrow press moves a frame -----------------------------------


def test_the_finest_step_lands_on_every_place_the_film_can_go():
    """0.30 mm is not the lattice's spacing -- it is what a single command
    delivers off zero. Above that the positions are 0.11 mm apart, because a
    command's distance grows by STEP_MM per param. Adding a flat first step and
    snapping stepped over two out of every three of them."""
    reached, offset = [], 0.0
    for _ in range(8):
        offset = gui.step_offset(offset, 1)
        reached.append(round(offset, 3))
    assert reached == [0.300, 0.406, 0.512, 0.617, 0.723, 0.829, 0.934, 1.040]


def test_the_old_step_skipped_most_of_them():
    """Kept as the reason this changed, not as a thing anyone should use."""
    coarse, offset = [], 0.0
    for _ in range(8):
        offset = gui.snap_offset(offset + gui.FINE_STEP_MM)
        coarse.append(round(offset, 3))
    finest, offset = [], 0.0
    while offset < coarse[-1] - 1e-9:
        offset = gui.step_offset(offset, 1)
        finest.append(round(offset, 3))
    assert set(coarse) < set(finest), "every coarse stop is a fine one"
    assert len(finest) > 2 * len(coarse), "and there are far more in between"


def test_a_step_of_nothing_still_moves():
    """A flat 0.11 mm would round to nothing off zero -- there is no such
    position -- and the frame would never move at all. The finest step is
    found rather than computed, so it cannot fall into that."""
    assert gui.step_offset(0.0, 1) > 0
    assert gui.step_offset(0.0, -1) < 0


def test_stepping_back_walks_the_same_places_and_crosses_zero():
    there, offset = [], 0.0
    for _ in range(4):
        offset = gui.step_offset(offset, 1)
        there.append(round(offset, 3))
    back = []
    for _ in range(6):
        offset = gui.step_offset(offset, -1)
        back.append(round(offset, 3))
    assert back[:3] == list(reversed(there[:3]))
    assert 0.0 in back and back[-1] < 0, "and out the other side"


@pytest.mark.parametrize("choice, first", [
    ("finest", 0.300), ("small (4.8 units)", 0.512),
    ("medium (9.8 units)", 1.040), ("large (21.8 units)", 2.308),
])
def test_a_chosen_step_lands_on_a_reachable_position(choice, first):
    """Whatever is asked for, what comes back is somewhere the film can go --
    a number finer than the hardware is a lie."""
    landed = gui.step_offset(0.0, 1, gui.step_millimetres(choice))
    assert round(landed, 3) == first
    assert landed == gui.snap_offset(landed)


@pytest.mark.parametrize("choice", gui.ADJUST_STEPS[1:])
def test_every_offered_step_is_exactly_one_command(choice):
    """The labels promise a param, so each has to be a single command.

    Before the cap went to 87 the largest rung would have been three of them,
    and each command pays the ramp again and scatters again -- so a rung that
    chains is a rung whose label is not the whole story.
    """
    from rps7200.session import plan_nudges

    assert len(plan_nudges(gui.step_millimetres(choice))) == 1


def test_the_step_labels_name_a_param_and_are_not_parsed_as_numbers():
    """The old parser read the first token of the label as a distance.

    These labels lead with a word, so that parser would have returned 0.0 for
    every rung -- and 0.0 means "finest", so every step would quietly have
    become the smallest one, with nothing to see in the window.
    """
    assert gui.step_millimetres("small (4.8 units)") > 0
    assert gui.step_millimetres("nonsense") == 0.0
    assert gui.step_millimetres("") == 0.0
    for name, param in gui.ADJUST_PARAMS.items():
        expected = gui.MM_PER_UNIT * param + gui.MM_PER_COMMAND
        assert gui.step_millimetres(f"{name} (whatever)") == pytest.approx(expected)


@pytest.mark.parametrize("typed, wanted", [
    ("2.8", "param 1"),
    ("8", "param 6"),
    ("1.0", "would not move"),
    ("200", "slide buttons"),
])
def test_the_typed_field_says_what_it_will_actually_send(typed, wanted):
    """He types a distance; the transport delivers the nearest command to it.

    The gap between those two is exactly what the window never showed him, and
    it is why a frame could be set to a position that was quietly delivered as
    no move at all.
    """
    said = gui.fine_preview(typed)
    assert wanted in said
    assert "mm" not in said


def test_the_typed_field_reports_the_shortfall_it_cannot_close():
    """param is an integer, so most asked-for distances are not reachable."""
    said = gui.fine_preview("8")
    assert "off" in said
    assert gui.fine_preview("2.8").endswith("+2.8 units")   # exactly param 1


def test_the_offered_steps_read_as_distances_except_the_finest():
    """"finest" is not a distance and cannot be written as one: the gap is
    0.27 mm off zero and 0.11 mm everywhere above it."""
    assert gui.ADJUST_STEPS[0] == "finest"
    assert gui.step_millimetres("finest") == 0.0
    for label in gui.ADJUST_STEPS[1:]:
        assert gui.step_millimetres(label) > 0, label


def test_a_step_never_leaves_the_reach_of_the_transport():
    offset = 0.0
    for _ in range(80):
        offset = gui.step_offset(offset, 1, 1.0)
    assert abs(offset) <= gui.MAX_TRAVEL_MM


def test_the_chosen_step_outlives_the_window_that_uses_it():
    """That window is opened and closed all through a roll; a setting that
    died with it would be re-chosen seventeen times."""
    import inspect
    assert "self.v_adjuststep" in inspect.getsource(gui.ScannerGui.__init__)
    assert "adjuststep" in gui.REMEMBERED
    assert "self.gui.v_adjuststep.get()" in inspect.getsource(
        gui._FrameAdjuster._step)


# -- one arrangement per photograph, not per pass ----------------------------


def test_a_scan_comes_back_the_way_its_prescan_was_left():
    """You frame a picture and say which way up it is; the scan of it is the
    same photograph and should not need telling again. It used to inherit
    whatever was last set anywhere, which drifts: frame two pictures, turn
    them differently, and the second one's answer reached the first one's
    scan."""
    import types
    stub = types.SimpleNamespace(
        rotation=0, flip=False, orientations={}, results=[], survey=[],
        _surveying=False, _transport=None,
        _show=lambda _r: None, _redraw_strip=lambda: None)
    stub._arrange = lambda r, s=None: gui.ScannerGui._arrange(stub, r, s)
    stub.remember_arrangement = lambda r: gui.ScannerGui.remember_arrangement(
        stub, r)

    def pass_of(kind, position, seq):
        return types.SimpleNamespace(kind=kind, number=0, seq=seq, image=None,
                                     position=position, meta={})

    first = pass_of("prescan", 4, 1)
    gui.ScannerGui._add_result(stub, first)
    first.rotation, first.flipped = 90, True
    stub.remember_arrangement(first)

    # A prescan of a different picture, turned differently. This is what used
    # to poison the answer for the first one.
    second = pass_of("prescan", 7, 2)
    gui.ScannerGui._add_result(stub, second)
    second.rotation, second.flipped = 180, False
    stub.remember_arrangement(second)
    stub.rotation, stub.flip = 180, False

    scan = pass_of("scan", 4, 3)
    gui.ScannerGui._add_result(stub, scan)
    assert scan.supersedes == 1, "it stands in for the prescan of picture 4"
    assert (scan.rotation, scan.flipped) == (90, True), (
        "and is arranged like that picture, not like the session")


def test_a_photograph_is_identified_by_frame_first_and_position_second():
    """A roll number survives the film being moved and put back; a transport
    position is what there is otherwise, and it is already how a scan is
    matched to its prescan."""
    import types
    assert gui.picture_of(types.SimpleNamespace(number=3, position=9)) == ("frame", 3)
    assert gui.picture_of(types.SimpleNamespace(number=0, position=9)) == ("at", 9)
    assert gui.picture_of(types.SimpleNamespace(number=0, position=None)) is None


def test_a_turn_in_the_sheet_reaches_the_window_behind_it():
    """It used to reach the thumbnail and stop there, so the preview went on
    showing the old arrangement until the frame was clicked again -- the
    window disagreeing with itself about a decision just made."""
    import inspect
    assert "self.gui.remember_arrangement(result)" in inspect.getsource(
        gui._ContactSheet._orient)
    assert "self.gui._reshow(" in inspect.getsource(gui._ContactSheet._one)
    reshow = inspect.getsource(gui.ScannerGui._reshow)
    assert "_redraw_strip" in reshow and "_schedule_redraw" in reshow


def test_the_file_is_arranged_like_the_pass_that_was_on_screen():
    """The session carried the last arrangement set anywhere, which drifts.
    The pass on screen is the one being scanned, so it is the one that
    decides."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._pin_arrangement)
    assert "self.session.rotation = self.current.rotation" in source
    assert "self.session.flip = self.current.flipped" in source
    assert "_pin_arrangement" in inspect.getsource(gui.ScannerGui.on_scan)


def test_a_reversed_pass_is_composed_the_same_way_in_both_places():
    """The window and the writer each turn the pass themselves, from the same
    number. If they composed it differently the file and the preview would
    disagree about which way up a photograph is."""
    import inspect
    from rps7200 import session as session_module
    for source in (inspect.getsource(gui.ScannerGui._arrange),
                   inspect.getsource(session_module.ScanSession._file)):
        assert "preview.compose(" in source
        assert 'reversal' in source


# --- tying infrared to the resolution --------------------------------------


def test_the_resolution_box_survives_being_half_typed():
    """`infrared_cost_note` is recomputed on every keystroke in the dpi box, so
    it meets "18" on the way to "1800" and must not raise there."""
    assert gui.dpi_or("1800") == 1800
    assert gui.dpi_or(" 900 ") == 900
    assert gui.dpi_or("") == 1800
    assert gui.dpi_or("oops") == 1800
    assert gui.dpi_or("", fallback=300) == 300


def test_the_cost_note_gives_both_numbers():
    """The choice is only meaningful as a comparison: 110 s against 220 s at
    1800 dpi is a decision, and "110 s" on its own is not."""
    tied = gui.infrared_cost_note(1800, True)
    untied = gui.infrared_cost_note(1800, False)
    assert "1800 dpi" in tied
    # Both lines name both costs, so switching the box does not hide the other.
    for note in (tied, untied):
        assert note.count("s") >= 2
    assert "untied" in tied
    assert "tied to the resolution" in untied


def test_the_cost_note_says_when_there_is_no_choice_left():
    """Past ~3700 dpi the line count has overtaken the floor and the two cost
    the same. Said outright rather than leaving an operator to notice that the
    two figures have converged."""
    assert "little in it" in gui.infrared_cost_note(7200, True)
    assert "little in it" in gui.infrared_cost_note(3600, True)


def test_the_saving_the_note_reports_is_the_measured_one():
    """300 dpi measured 219.2 s untied and 24.6 s tied. A note that rounded
    that to "about 4 minutes either way" would be talking the operator out of
    the default."""
    note = gui.infrared_cost_note(300, True)
    assert "little in it" not in note


# --- resuming a roll -------------------------------------------------------


def _roll_folder(tmp_path, survey=None, roll=None, prescans=0):
    """A roll directory as the session writes one."""
    import numpy as np
    from rps7200 import tiff
    folder = tmp_path / "2026-09-14"
    folder.mkdir(parents=True, exist_ok=True)
    if survey is not None:
        (folder / "survey.json").write_text(json.dumps(survey),
                                            encoding="utf-8")
    if roll is not None:
        (folder / "roll.json").write_text(json.dumps(roll), encoding="utf-8")
    for n in range(1, prescans + 1):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((8, 12, 3), 900 * n, np.uint16))
    return folder


def test_a_frame_that_errored_is_offered_again():
    """The case a resume exists for. A frame counted as done because the roll
    reached it would be the one thing worse than not resuming at all."""
    done = gui.scanned_frames({"frames": [
        {"number": 1, "done": True},
        {"number": 2, "done": False, "error": "read timed out"},
        {"number": 3, "done": True},
    ]})
    assert done == {1, 3}


def test_a_manifest_written_before_done_existed_still_reads():
    """The rolls already on disk have no `done` key. Treating them as nothing
    finished would offer a whole roll again; treating them as all finished
    would offer none of it."""
    done = gui.scanned_frames({"frames": [
        {"number": 1},                       # scanned, no error
        {"number": 2, "error": "boom"},      # not scanned
        {"number": 3, "prescan": "prescan03.tif"},   # a walk, not a scan
    ]})
    assert done == {1}


def test_what_the_roll_was_asked_for_beats_what_it_walked():
    """`only` is what the contact sheet's ticks become, so it is the answer to
    "how many frames is this roll" -- not the number the walk found."""
    manifest = {"frames": [{"number": n} for n in range(1, 7)]}
    assert gui.wanted_frames(manifest, {"settings": {"only": [2, 4]}}) == [2, 4]
    assert gui.wanted_frames(manifest, {}) == [1, 2, 3, 4, 5, 6]


def test_a_roll_summary_needs_no_pixels(tmp_path):
    """The browser lists these, and a roll directory can hold 38 frames at
    142 MB. Opening them to find out whether the roll finished would make the
    list unusable."""
    folder = _roll_folder(
        tmp_path,
        survey={"roll": "strip", "frames": [{"number": n} for n in range(1, 5)]},
        roll={"roll": "strip", "settings": {"resolution": 1800, "infrared": True,
                                            "only": [1, 2, 3, 4]},
              "frames": [{"number": 1, "done": True}, {"number": 2, "done": True}]})
    summary = gui.roll_summary(folder)
    assert summary["remaining"] == [3, 4]
    assert summary["resolution"] == 1800
    assert "2 of 4 scanned" in gui.roll_line(summary)
    assert gui.roll_summary(tmp_path) is None, "not a roll directory"


def test_a_roll_can_be_read_back_with_its_walk_and_its_progress(tmp_path):
    """The two manifests live in one directory, and both matter: the walk is
    the only one with prescans beside it, and the roll is how far it got."""
    folder = _roll_folder(
        tmp_path,
        survey={"roll": "strip", "prescan_resolution": 300, "start_at": 1,
                "frames": [{"number": n, "prescan": f"prescan{n:02d}.tif"}
                           for n in range(1, 5)]},
        roll={"roll": "strip", "settings": {"resolution": 3600, "only": [1, 2, 3, 4],
                                            "fast_infrared": False},
              "frames": [{"number": 1, "done": True}]},
        prescans=4)
    out = gui.read_survey(folder)
    assert len(out["results"]) == 4, "the sheet comes from the walk"
    assert out["scanned"] == {1}
    assert out["wanted"] == [1, 2, 3, 4]
    assert out["settings"]["resolution"] == 3600


def test_a_walk_nobody_acted_on_still_reads(tmp_path):
    """No roll.json at all. Nothing is done and everything is wanted."""
    folder = _roll_folder(
        tmp_path,
        survey={"roll": "strip", "frames": [{"number": n,
                                            "prescan": f"prescan{n:02d}.tif"}
                                           for n in (1, 2)]},
        prescans=2)
    out = gui.read_survey(folder)
    assert out["scanned"] == set()
    assert out["wanted"] == [1, 2]
    assert out["settings"] == {}


def test_a_folder_that_is_neither_is_refused(tmp_path):
    import pytest
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no survey.json"):
        gui.read_survey(tmp_path / "empty")


def test_settings_a_roll_has_nothing_to_say_about_are_left_alone():
    """`fast_infrared` is younger than the rolls already on disk. A manifest
    without it must not assert its default over whatever the window holds --
    absent is not the same as off."""
    assert "fast_ir" not in gui.restorable({"resolution": 1800})
    assert gui.restorable({"fast_infrared": False})["fast_ir"] is False
    assert gui.restorable({"resolution": 1800})["dpi"] == "1800"
    # `mono` is derived from the film type by `_sync_film`, so restoring it
    # would be overwritten a moment later by something that looks like a
    # disagreement.
    assert "mono" not in gui.restorable({"mono": True})


def test_a_batch_name_says_what_the_file_is():
    """NegPy reads these next, so what it is leads. A timestamp sorts by when
    it was scanned and says nothing about what it was."""
    import types
    frame = types.SimpleNamespace(
        kind="frame", number=3, seq=7,
        meta={"resolution_dpi": 1800, "channels": 4})
    assert gui.batch_name(frame, "tiff") == "frame03_1800dpi_ir.tif"
    assert gui.batch_name(frame, "jpeg") == "frame03_1800dpi_ir.jpg"
    loose = types.SimpleNamespace(kind="scan", number=None, seq=-12,
                                  meta={"resolution_dpi": 300, "channels": 3})
    assert gui.batch_name(loose, "tiff") == "scan_012_300dpi.tif"


# --- the rolls table -------------------------------------------------------


def _shelf(tmp_path, *, name="2026-09-14", wanted=(1, 2, 3, 4), done=(1, 2),
           dpi=1800, film="negative", ir=True, approved=None):
    """A roll folder as the session writes one, without any pixels."""
    folder = tmp_path / "rolls" / name
    folder.mkdir(parents=True)
    (folder / "survey.json").write_text(json.dumps(
        {"roll": name, "frames": [{"number": n} for n in wanted]}),
        encoding="utf-8")
    (folder / "roll.json").write_text(json.dumps({
        "roll": name, "wanted": list(wanted),
        "settings": {"resolution": dpi, "film": film, "infrared": ir},
        "frames": [{"number": n, "done": n in done} for n in wanted]}),
        encoding="utf-8")
    if approved:
        (folder / "approved.json").write_text(json.dumps({"frames": approved}),
                                              encoding="utf-8")
    return folder


def _entry(tmp_path, roll, number, name=None):
    """A library entry that says which roll frame it is."""
    entry = tmp_path / "library" / (name or f"{roll}-{number}")
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text(json.dumps(
        {"film": {"frame": f"{roll}-{number:02d}"}}), encoding="utf-8")
    return entry


def test_entries_are_joined_to_rolls_by_the_frame_they_name(tmp_path):
    """Nothing in a roll manifest records its entries -- the entry is created on
    the writer thread after the frame's record is written. The join is on
    `film.frame`, which `_file` sets to "{roll}-{NN}"."""
    _entry(tmp_path, "strip", 1)
    _entry(tmp_path, "strip", 2)
    _entry(tmp_path, "other", 1)
    # Entries that belong to no roll, and a malformed one, are not a crash.
    loose = tmp_path / "library" / "loose"
    loose.mkdir()
    (loose / "scan.json").write_text(json.dumps({"film": {"frame": ""}}),
                                     encoding="utf-8")
    broken = tmp_path / "library" / "broken"
    broken.mkdir()
    (broken / "scan.json").write_text("{not json", encoding="utf-8")

    index = gui.roll_entry_index(tmp_path / "library")
    assert sorted(index["strip"]) == [1, 2]
    assert sorted(index["other"]) == [1]
    assert "" not in index


def test_a_rolls_own_date_beats_the_filesystems(tmp_path):
    """A roll duplicated or copied to another disk gets a fresh birthtime while
    its name still says when the film was scanned. The name is the roll's own
    answer; the filesystem's is about the copy."""
    named = gui.folder_created(_shelf(tmp_path, name="2026-09-14"))
    assert time.strftime("%Y-%m-%d", time.localtime(named)) == "2026-09-14"
    # A folder whose name is not a date still gets an answer.
    assert gui.folder_created(_shelf(tmp_path, name="strip")) > 0


def test_a_summary_reports_size_and_entries_without_reading_pixels(tmp_path):
    _shelf(tmp_path)
    _entry(tmp_path, "2026-09-14", 1)
    listed = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")
    assert len(listed) == 1
    summary = listed[0]
    assert summary["size"] > 0
    assert summary["modified"] > 0
    assert sorted(summary["entries"]) == [1]
    # The library root is optional; without it nothing knows about entries.
    assert gui.rolls_on_disk(tmp_path / "rolls")[0]["entries"] == {}


def test_the_table_sorts_on_values_not_on_the_text_it_shows():
    """"2 of 10" sorts before "2 of 4" as text, and "900 MB" before "1.0 GB"."""
    few = {"roll": "a", "wanted": [1, 2, 3, 4], "done": [1, 2],
           "remaining": [3, 4], "resolution": 900, "film": "bw",
           "created": 1.0, "size": 900_000_000, "scanned": True, "entries": {}}
    many = dict(few, roll="b", wanted=list(range(1, 11)),
                remaining=list(range(3, 11)), resolution=3600,
                size=1_000_000_000, created=2.0)
    assert sorted([many, few], key=gui.SORT_KEYS["size"])[0] is few
    assert sorted([many, few], key=gui.SORT_KEYS["dpi"])[0] is few
    # Fewest frames left first, and a longer roll breaks the tie.
    assert sorted([many, few], key=gui.SORT_KEYS["frames"])[0] is few
    assert gui.human_size(900_000_000) == "900.0 MB"
    assert gui.human_size(1_000_000_000) == "1.0 GB"
    assert gui.when(None) == "--", "an unopened roll must not read as 1970"


def test_the_filter_looks_at_the_film_as_well_as_the_name(tmp_path):
    """"the slide one" is as likely a way to look for a roll as its date is."""
    _shelf(tmp_path, name="2026-09-14", film="negative")
    _shelf(tmp_path, name="2026-08-02", film="positive", done=(1, 2, 3, 4))
    listed = gui.rolls_on_disk(tmp_path / "rolls")
    assert [r["roll"] for r in gui.matching(listed, "positive")] == ["2026-08-02"]
    assert [r["roll"] for r in gui.matching(listed, "09-14")] == ["2026-09-14"]
    assert [r["roll"] for r in gui.matching(listed, unfinished_only=True)] \
        == ["2026-09-14"]
    assert len(gui.matching(listed)) == 2


def test_a_duplicate_is_told_apart_from_its_original(tmp_path):
    """A duplicate keeps the original's roll name inside its manifest, so two
    rows read identically unless the folder name leads -- which is exactly when
    you need to tell them apart."""
    folder = _shelf(tmp_path, name="2026-09-14")
    summary = gui.roll_summary(folder)
    assert gui.roll_cells(summary)[0] == "2026-09-14"
    copied = dict(summary, folder=folder.with_name("2026-09-14-2"))
    assert gui.roll_cells(copied)[0] == "2026-09-14-2  (2026-09-14)"


def test_a_duplicate_never_lands_on_something_already_there(tmp_path):
    folder = _shelf(tmp_path, name="strip")
    assert gui.duplicate_name(folder).name == "strip-2"
    (folder.parent / "strip-2").mkdir()
    assert gui.duplicate_name(folder).name == "strip-3"


def test_an_export_plan_holds_each_frames_own_arrangement(tmp_path):
    """The frame's own decision where it made one, the roll's otherwise -- the
    same precedence the delivered files follow."""
    _shelf(tmp_path, approved=[{"number": 2, "rotation": 270, "flipped": True}])
    _entry(tmp_path, "2026-09-14", 1)
    _entry(tmp_path, "2026-09-14", 2)
    summary = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")[0]
    summary["settings"]["rotation"] = 90
    plan = {item.number: item for item in gui.roll_exports(summary)}
    assert plan[1].rotation == 90 and plan[1].flipped is False
    assert plan[2].rotation == 270 and plan[2].flipped is True
    assert plan[1].meta["resolution_dpi"] == 1800
    assert plan[1].meta["channels"] == 4, "the roll was infrared"


def test_a_frame_with_no_library_entry_is_left_out_of_an_export(tmp_path):
    """Export re-corrects from the entries, so a frame without one cannot be
    exported at all. Left out here; the window counts the difference against
    `done` and says how many were skipped."""
    _shelf(tmp_path, done=(1, 2))
    _entry(tmp_path, "2026-09-14", 1)
    summary = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")[0]
    assert [item.number for item in gui.roll_exports(summary)] == [1]
    assert len(summary["done"]) == 2, "both were scanned; only one survives"


def test_approvals_are_read_without_loading_a_survey(tmp_path):
    """`approved.json` is the one thing in a roll folder the library cannot
    rebuild, so Delete has to be able to ask about it without reading pixels."""
    folder = _shelf(tmp_path, approved=[
        {"number": 1, "offset_mm": 0.3, "rotation": 90, "flipped": True}])
    offsets, rotations, flips, _, _ = gui.read_approved(folder)
    assert offsets == {1: 0.3} and rotations == {1: 90} and flips == {1: True}
    # A folder without one, and a corrupt one, both answer empty.
    assert gui.read_approved(tmp_path) == ({}, {}, {}, {}, {})
    (folder / "approved.json").write_text("{nope", encoding="utf-8")
    assert gui.read_approved(folder) == ({}, {}, {}, {}, {})


# --- defaults you can get back to ------------------------------------------


def test_a_tick_means_the_same_however_it_was_written():
    """A BooleanVar answers True, a hand-edited settings file may hold 1, and
    JSON round-trips true. All three are the same tick, and a comparison that
    disagreed would offer to reset a control nobody had touched."""
    for same in (True, 1, "1", "true", "True", "yes"):
        assert gui.as_text(same) == "1", same
    for same in (False, 0, "0", "false", "no"):
        assert gui.as_text(same) == "0", same
    assert gui.as_text(" 1800 ") == "1800", "whitespace is not a change"
    assert gui.as_text("negative") == "negative"


def test_only_what_moved_is_counted():
    defaults = {"dpi": "1800", "ir": True, "frames": "0"}
    assert gui.changed_controls({"dpi": "1800", "ir": 1}, defaults,
                                ("dpi", "ir")) == ()
    assert gui.changed_controls({"dpi": "3600", "ir": False}, defaults,
                                ("dpi", "ir")) == ("dpi", "ir")
    # Order follows the panel's own order, so a message reads the way the
    # controls are laid out.
    assert gui.changed_controls({"ir": False, "dpi": "900"}, defaults,
                                ("dpi", "ir")) == ("dpi", "ir")


def test_a_control_with_no_recorded_default_is_left_alone():
    """It means the control did not exist when the defaults were taken, and
    inventing one is how a reset button starts changing things nobody set."""
    assert gui.changed_controls({"mystery": "x"}, {"dpi": "1800"},
                                ("mystery",)) == ()


def test_every_remembered_setting_can_be_reset():
    """A setting that persists but sits in no panel is one an operator can move
    and never put back. This is the test that makes adding a control to
    `REMEMBERED` force a decision about where its reset lives."""
    reachable = {name for panel in gui.PANEL_CONTROLS.values()
                 for name in panel} | set(gui.PANEL_LESS)
    missing = [name for name in gui.REMEMBERED if name not in reachable]
    assert not missing, f"no reset reaches: {missing}"


def test_the_panels_name_real_controls_and_do_not_overlap():
    """A name in two panels would be reset twice and counted twice; a name in
    none is the case above."""
    seen = []
    for panel in gui.PANEL_CONTROLS.values():
        seen.extend(panel)
    assert len(seen) == len(set(seen)), "a control is in two panels"
    # Film's controls are the film fields, not `v_` variables; everything else
    # should be a remembered control or the one deliberate extra.
    known = set(gui.REMEMBERED) | set(gui.REMEMBERED_FILM) | {
        "roll", "frame", "subject", "notes", "mono_channel"}
    assert not set(seen) - known, set(seen) - known


def test_the_settings_payload_carries_every_section():
    """The regression this guards actually shipped: `_remember` built its payload
    without the `rolls` key, and because the file is written whole that did not
    merely fail to save the rolls table's "Last opened" -- it erased it on every
    save. A section in `settings.SECTIONS` that the payload omits is silently
    destroyed, so the check is against that list rather than a hand-kept one."""
    import inspect
    from rps7200 import settings as settings_module

    source = inspect.getsource(gui.ScannerGui._remember)
    for section in settings_module.SECTIONS:
        assert f'"{section}"' in source, section


# -- what the contact sheet was left holding --------------------------------
#
# The sheet is a Toplevel and is destroyed when it closes, so everything
# decided in it -- which frames to scan, where each sits, which way up -- died
# with the window: closing it and pressing "Contact sheet ..." again rebuilt it
# from the survey with every position gone and the whole strip re-ticked.
# `_clean_sheet_state` is what a stored set has to survive, and it is a
# staticmethod precisely so it can be tested without opening a window.

clean = gui.ScannerGui._clean_sheet_state


def test_frame_numbers_come_back_as_integers_after_a_restart():
    """JSON has no integer keys. Everything the sheet stores is keyed by frame
    number, so a round trip through `gui-settings.json` hands back `"3"` where
    `3` went in -- and a lookup by number then misses every entry, which is
    indistinguishable from not having saved at all."""
    out = clean({"offsets": {"3": 1.5}, "rotations": {"3": 90},
                 "flips": {"3": True}, "ticks": {"3": False}})
    assert out["offsets"] == {3: 1.5}
    assert out["rotations"] == {3: 90}
    assert out["flips"] == {3: True}
    assert out["ticks"] == {3: False}


def test_a_deliberate_zero_rotation_survives():
    """Zero is a decision for rotations, unlike offsets. "Rotate all" moves the
    session default, so a frame straightened by hand that came back absent would
    fall back to that default and be scanned sideways -- which happened on the
    first strip this was driven on. Dropping a falsy value here would rebuild
    exactly that bug on every restart."""
    out = clean({"rotations": {"4": 0}, "flips": {"4": False}})
    assert out["rotations"] == {4: 0}
    assert out["flips"] == {4: False}


def test_each_kind_keeps_its_own_type():
    """An offset is millimetres and a rotation is degrees; a rotation that came
    back as 89.7 would not match any of the four quarter turns."""
    out = clean({"offsets": {"1": 2}, "rotations": {"1": "180"},
                 "ticks": {"1": 1}})
    assert isinstance(out["offsets"][1], float) and out["offsets"][1] == 2.0
    assert out["rotations"][1] == 180 and isinstance(out["rotations"][1], int)
    assert out["ticks"][1] is True


def test_a_walked_roll_reopened_comes_back_with_its_positions():
    """Closing the window must not throw the walk's proposals away.

    `open_roll` restored `approved.json` -- positions already *committed* --
    and never re-proposed. A roll walked and then closed without commissioning
    has no `approved.json`, so it reopened with nothing at all: every proposal
    died with the window, and the roll then scanned uncorrected with no sign
    anything was missing. That cost a real 23-minute roll on 2026-09-22.
    """
    walked = _strip()
    proposed, _notes = gui._propose_positions(walked, {})
    assert proposed, "the fixture has to propose something for this to mean anything"

    # what open_roll does now: stored positions in as `kept`, re-proposed round
    reopened, notes = gui._propose_positions(walked, {}, {})
    assert reopened == proposed
    assert all((notes[n] or {}).get("source") for n in reopened)


def test_a_committed_position_still_wins_on_reopen():
    """His number is the authority and re-proposing must not overwrite it."""
    walked = _strip()
    kept = {2: 0.5116}
    reopened, notes = gui._propose_positions(walked, kept, {2: "operator"})
    assert reopened[2] == 0.5116
    assert notes[2]["source"] == "operator"


def test_approved_json_carries_who_decided_each_position(tmp_path):
    """Written since the sheet began proposing them; it has to read back too,
    or a reopened roll relabels the ensemble's numbers as his."""
    folder = tmp_path / "roll"
    folder.mkdir()
    (folder / "approved.json").write_text(json.dumps({
        "roll": "r",
        "frames": [{"number": 1, "offset_mm": 0.5116, "source": "measured"},
                   {"number": 2, "offset_mm": 0.3002}],
    }), encoding="utf-8")
    offsets, _rot, _flip, _entries, sources = gui.read_approved(folder)
    assert offsets[1] == pytest.approx(0.5116)
    assert sources == {1: "measured"}       # absent means his, as it always did


# -- reading a walk the command line wrote ---------------------------------


def test_a_command_line_manifest_yields_the_keys_the_window_reads():
    """`scan_roll` writes these inside `settings`; this reader wanted them at
    the top level, so they came back None.

    `prescan_resolution` is the one that costs something. It becomes
    `_survey_predpi`, which pins a commissioned scan's prescan to the
    resolution its positions were decided at. Unpinned, the reference is
    resampled and confidence falls 93.5 -> 47.4 against a floor of 55, so every
    frame reads `unverified` and nothing moves -- hours of transport, no
    correction, and nothing said.
    """
    cli = {"roll": "registration-M",
           "settings": {"dpi": 1800, "prescan_resolution": 300,
                        "start_at": 4, "film": "negative"}}
    merged = gui.manifest_settings(cli)
    assert merged["prescan_resolution"] == 300
    assert merged["start_at"] == 4
    assert merged["resolution"] == 1800          # the CLI calls it dpi


def test_the_windows_own_manifest_is_unchanged_by_the_merge():
    """It writes them at the top level and inside settings, and the top level
    is what it meant."""
    own = {"roll": "r", "prescan_resolution": 300, "start_at": 2,
           "settings": {"resolution": 1200, "prescan_resolution": 300,
                        "start_at": 2}}
    merged = gui.manifest_settings(own)
    assert merged["prescan_resolution"] == 300
    assert merged["start_at"] == 2
    assert merged["resolution"] == 1200


def test_a_resumed_rolls_progress_wins_over_the_survey():
    merged = gui.manifest_settings(
        {"settings": {"dpi": 600}}, {"resolution": 1800})
    assert merged["resolution"] == 1800


def test_restorable_reads_the_alias_but_still_leaves_absent_keys_alone():
    """Absent means "this roll has nothing to say about it", not "off" -- the
    walks from before `prescan_resolution` existed depend on that."""
    assert gui.restorable({"dpi": 1800})["dpi"] == "1800"
    assert "predpi" not in gui.restorable({"dpi": 1800})
    assert gui.restorable({}) == {}


# -- the last thing shown before the film moves ----------------------------


class _Confirming:
    """Enough of the window for `_approved_note`, which reads nothing else."""

    _approved_note = gui.ScannerGui._approved_note

    def __init__(self, correct=True):
        self.v_correct = types.SimpleNamespace(get=lambda: correct)


def test_the_dialog_counts_positions_by_who_decided_them():
    """It used to say every one of them was "a position you set by hand".

    True when typing was the only way to have one. The sheet pre-fills a
    position for every frame it can read, so that sentence was attributing the
    machine's decisions to him -- on the screen where he confirms them.
    """
    note = _Confirming()._approved_note([
        Approved(1, 0.5, source="operator"),
        Approved(2, 0.5, source="measured"),
        Approved(3, 0.5, source="measured"),
        Approved(4, 0.5, source="neighbours"),
    ])
    assert "1 you positioned" in note
    assert "2 two detectors agreed" in note
    assert "1 read from the frames either side" in note
    assert "by hand" not in note


def test_the_dialog_never_promises_the_automatic_nudge():
    """Every ticked frame gets an approval, so the held branch always wins and
    `elif correct` is never reached. The clause described something that cannot
    happen -- see TODO.md, the tick itself should go."""
    note = _Confirming(correct=True)._approved_note(
        [Approved(1, 0.5, source="measured")])
    assert "nudge" not in note


def test_a_roll_where_nothing_moves_says_nothing():
    assert _Confirming()._approved_note([Approved(1, 0.0)]) == ""
    assert _Confirming()._approved_note([]) == ""


def test_the_dialog_is_not_in_millimetres():
    note = _Confirming()._approved_note([Approved(1, 0.5, source="measured")])
    assert "mm" not in note


def test_the_coloured_ring_stands_off_the_picture():
    """The line says "this one will be scanned". It has to read as drawn
    around the print, not as part of it.

    It was one frame with thick padding, which made a slab of colour rather
    than a border with room inside it. Three nested frames now: the ring is the
    line, the mount is the gap, the picture sits in the mount. Geometry needs a
    window, but the nesting is the fact that matters and it can be pinned here
    -- the same way this file pins its other wiring.
    """
    body = inspect.getsource(gui._ContactSheet._cell)
    assert "tk.Frame(ring" in body, "the mount must sit inside the ring"
    assert "tk.Label(mount" in body, "the picture must sit inside the mount"
    assert "tk.Label(ring" not in body, "the picture must not touch the line"
    assert "background=self.MOUNT_BG" in body


def test_the_line_is_thin_and_the_gap_is_wider_than_it():
    """Otherwise it is a band of colour again, which is what was wrong."""
    assert gui._ContactSheet.RING <= 3
    assert gui._ContactSheet.MOUNT > gui._ContactSheet.RING


# -- the blue line that says where a frame is going ------------------------


def test_the_mark_stands_off_the_edge_the_film_moves_toward():
    """Backward from the left, forward from the right. The direction is half
    the information -- a mark that ignored it would say the strip is offset
    without saying which way."""
    back = gui.adjustment_mark(-9.84 * 0.1057, 210)
    fwd = gui.adjustment_mark(+9.84 * 0.1057, 210)
    assert back is not None and fwd is not None
    assert back < 210 // 2 < fwd
    assert back + fwd == 210          # mirrored about the middle


def test_the_mark_is_true_to_scale_and_not_exaggerated():
    """A line drawn larger than the move would have the sheet claiming
    something the transport is not going to do. A 9.84-unit correction is 2.9%
    of the aperture, so on a 210 px thumbnail it is 6 px -- small, because the
    correction is small."""
    assert gui.adjustment_mark(-9.84 * 0.1057, 210) == 6
    assert gui.adjustment_mark(-2.84 * 0.1057, 210) == 2


def test_no_adjustment_draws_no_mark():
    assert gui.adjustment_mark(0.0, 210) is None
    assert gui.adjustment_mark(None, 210) is None


def test_a_wild_reading_cannot_draw_itself_as_the_picture():
    """Clamped at half the width, and never on the edges where it would be
    invisible -- the same bargain the rest of the sheet makes with a detector
    it cannot fully trust."""
    assert gui.adjustment_mark(-999.0, 210) == 105
    assert 1 <= gui.adjustment_mark(-0.001, 210) <= 208
    assert gui.adjustment_mark(-9.84 * 0.1057, 3) is None


# -- the cell's caption, which had no tests while carrying four mistakes ----


def test_a_scanned_frame_keeps_saying_so_even_once_it_has_a_position():
    """The marker that has to survive a resume is "scanned".

    `_refresh_caption` returned on the offset branch before it could reach the
    `done` check. That was harmless while most frames had no offset; the sheet
    now proposes a position for every frame it can read, so on a resumed roll
    almost every scanned frame lost its marker -- and a resume exists precisely
    so that three hours of transport is not spent twice.
    """
    said, colour = gui.frame_caption(0.5116, "measured", done=True)
    assert "scanned" in said
    assert colour == "DONE"
    # and the position is still there, for a frame re-ticked deliberately
    assert "4.8" in said


def test_a_frame_with_nothing_decided_shows_its_contrast():
    said, colour = gui.frame_caption(0.0, None, contrast=0.42)
    assert said == "contrast 0.42"
    assert colour == "GREY"


def test_a_position_the_detector_read_says_which_detector_read_it():
    """The three words are not worth the same and he is the one who decides."""
    for source in gui.MACHINE_SOURCES:
        said, _ = gui.frame_caption(0.5116, source)
        assert source in said


def test_a_position_he_set_does_not_wear_the_detectors_badge():
    said, _ = gui.frame_caption(0.5116, "operator")
    assert "measured" not in said and "unconfirmed" not in said
    assert said.startswith("moved")


def test_a_frame_read_and_already_in_place_says_so_rather_than_nothing():
    """Dropping an unreachable proposal must not drop the fact it was read.

    A proposal below one command cannot be delivered, so it leaves `offsets`.
    But "the detector saw this and it is already as close as the transport can
    put it" is not the same as "nothing could read it", and the driver makes
    the same distinction with `in_place`.
    """
    said, _ = gui.frame_caption(0.0, "measured")
    assert said == "in place (measured)"
    assert gui.frame_caption(0.0, None, read=True)[0] == "in place"


def test_no_caption_anywhere_is_in_millimetres():
    for args in [(0.5116, "measured"), (0.5116, "operator"),
                 (0.0, "measured"), (0.5116, "measured", True)]:
        assert "mm" not in gui.frame_caption(*args)[0]


# -- what the sheet proposes, and who it says decided it --------------------


def test_a_stored_source_that_is_not_one_of_the_five_words_is_dropped():
    """"measured" is a claim that a detector read the frame. A settings file
    edited by hand does not get to assert it about something else."""
    out = gui.ScannerGui._clean_sheet_state(
        {"sources": {"1": "measured", "2": "invented", "3": 17}})
    assert out["sources"] == {1: "measured"}


@pytest.mark.parametrize("rubbish", [None, "not a dict", 17, [], {"offsets": 9}])
def test_nonsense_reads_as_no_decisions_rather_than_raising(rubbish):
    """Same bargain as the settings file itself: this is a convenience, and
    nothing about reading it back may stop the sheet opening."""
    out = clean(rubbish)
    assert out == {"ticks": {}, "offsets": {}, "rotations": {}, "flips": {},
                   "sources": {}, "options": {}}


def test_one_bad_entry_costs_only_itself():
    """A file edited by hand should lose the line it got wrong, not the roll's
    other twelve positions."""
    out = clean({"offsets": {"2": 1.0, "bent": 3.0, "5": "sideways", "7": 2.5}})
    assert out["offsets"] == {2: 1.0, 7: 2.5}


def test_every_way_out_of_the_sheet_keeps_what_was_decided():
    """The Close button, the title bar's X, commissioning the scan and Escape
    all destroy the window. Each has to go through `_dismiss` first, or the
    decisions are kept for one way out and silently dropped for another --
    which is the shape of the original bug.

    Escape was the fourth way out and this test did not know about it. It was
    bound straight to `top.destroy`, so a sheet left by the key everyone
    reaches for lost every tick, drag and turn without a word.
    """
    import inspect

    built = inspect.getsource(gui._ContactSheet.__init__)
    assert 'text="Close", command=self._dismiss' in built
    assert 'protocol("WM_DELETE_WINDOW", self._dismiss)' in built
    scan = inspect.getsource(gui._ContactSheet._scan)
    assert "self._dismiss()" in scan and "self.top.destroy()" not in scan
    keys = inspect.getsource(gui._ContactSheet._actions)
    assert '"sheet_close": self._dismiss' in keys


def test_reopening_a_roll_keeps_who_decided_each_position():
    """`open_roll` replaces the sheet state wholesale, and it used to drop the
    sources while keeping the offsets.

    A kept offset with no recorded source is read as one he set by hand, so the
    next sheet built from that state relabelled every machine proposal
    `operator` -- and the confirm dialog then counted them as his, on the
    screen where he approves them.
    """
    import inspect

    body = inspect.getsource(gui.ScannerGui.open_roll)
    assert '"sources": dict(out["sources"])' in body


def test_a_fresh_walk_does_not_inherit_the_last_strips_decisions():
    """Frame numbers on a new strip name different pictures. The window already
    clears `orientations` for this reason -- "a different film, shown and
    written sideways" -- and the sheet's own copy has to go at the same moment
    or the positions are applied to whatever lands on those numbers."""
    import inspect

    source = inspect.getsource(gui.ScannerGui.on_roll)
    assert "self.orientations = {}" in source, "the existing guard moved"
    assert "self.sheet_state = {}" in source


# -- the sheet's own scan options -------------------------------------------


def test_the_sheet_offers_only_options_a_roll_can_carry():
    """Exposure and shading are session-wide rather than per-roll, so a copy
    of them on the sheet would either do nothing to the roll or quietly change
    the window's next single scan. Both are worse than not offering them."""
    import dataclasses

    from rps7200.session import Roll

    carried = {f.name for f in dataclasses.fields(Roll)}
    maps = {"dpi": "resolution", "predpi": "prescan_resolution",
            "ir": "infrared", "fast_ir": "fast_infrared", "film": "film",
            "meter": "meter", "correct": "correct"}
    assert set(gui._ContactSheet.OPTIONS) == set(maps)
    for field in maps.values():
        assert field in carried, field


def test_the_sheets_options_reach_the_roll_rather_than_the_windows():
    """"Sheet wins for the roll". The job has to be built from the resolved
    values; reading any of them back off the main window would mean setting
    3600 on the sheet and scanning at whatever the window still showed."""
    import inspect

    source = inspect.getsource(gui.ScannerGui.on_scan_chosen)
    for built in ("resolution=dpi", "prescan_resolution=predpi",
                  "infrared=infrared", "fast_infrared=fast_ir", "film=film",
                  "meter=meter", "correct=correct", "mono=mono"):
        assert built in source, built
    for leaked in ("infrared=self.v_ir.get()", "film=self.v_film.get()",
                   "meter=self.v_meter.get()", "correct=self.v_correct.get()"):
        assert leaked not in source, leaked


def test_an_absent_options_set_still_falls_back_to_the_window():
    """`on_scan_chosen` is reachable without a sheet, and that path has to
    behave exactly as it did before the panel existed."""
    import inspect

    source = inspect.getsource(gui.ScannerGui.on_scan_chosen)
    assert "options=None" in source
    assert "self._dpi(), self._prescan_dpi()" in source


def test_stored_options_come_back_and_unknown_ones_are_dropped():
    """A key left over from an older version would be handed to a widget that
    is not there."""
    out = clean({"options": {"dpi": "3600", "ir": False, "bogus": 1}})
    assert out["options"] == {"dpi": "3600", "ir": False}


# --- what the sheet says a frame's aiming did -------------------------------


def test_the_caption_tells_a_corrected_frame_from_a_refused_one():
    """`gap_edges` made these the same silence: it answered "registered" both
    for a frame it had checked and for one it could not see."""
    from tools.gui import _aim_note

    assert "aimed -5.8 units" in _aim_note(
        {"correction": {"outcome": "held", "decision_mm": -0.61}})
    assert "in place" in _aim_note({"correction": {"outcome": "in_place"}})
    assert "would aim" in _aim_note(
        {"correction": {"outcome": "dry_run", "decision_mm": -0.61}})


def test_the_caption_says_why_a_frame_was_not_aimed():
    from tools.gui import _aim_note

    note = _aim_note({"correction": {
        "outcome": "abstained",
        "reason": "only 1 member(s) could measure this frame; two that agree"}})
    assert "not aimed" in note and "1 member" in note
    assert "not aimed (not converged)" in _aim_note(
        {"correction": {"outcome": "not_converged"}})


def test_a_frame_nobody_aimed_says_nothing():
    """An ordinary roll's caption must be exactly what it was."""
    from tools.gui import _aim_note

    assert _aim_note({}) == ""
    assert _aim_note({"offset_mm": 0.2}) == ""


# --- proposing the whole strip's positions when the sheet opens -------------


class _Walked:
    def __init__(self, number, image):
        self.number, self.image = number, image


def _strip(count=8, gap=18):
    import numpy as np
    out = []
    for n in range(1, count + 1):
        rng = np.random.default_rng(n)
        a = rng.random((40, 428, 3)) * 90 + 15
        a[:, :gap] = 37.0 + rng.random((40, gap, 3)) * 0.6
        out.append(_Walked(n, a))
    return out


def _walked_folder(tmp_path, count=8):
    """A roll folder on disk in the shape `read_survey` expects.

    Built from `_strip()`, so the prescans are the same synthetic frames the
    proposal tests use, and deliberately without an `approved.json`: that is
    the state a walk is left in when it is closed before being commissioned,
    and the state the demo opens.
    """
    from rps7200 import tiff

    folder = tmp_path / "walk"
    folder.mkdir()
    records = []
    for frame in _strip(count=count):
        name = f"prescan{frame.number:02d}.tif"
        tiff.write(str(folder / name), frame.image.astype(np.uint8))
        records.append({"number": frame.number, "prescan": name})
    (folder / "survey.json").write_text(json.dumps({
        "roll": "walk",
        "settings": {"dpi": 600, "prescan_resolution": 300, "dry_run": True,
                     "film": "negative", "start_at": 1},
        "frames": records,
    }), encoding="utf-8")
    return folder


def test_a_walk_reopened_is_measured_again_not_remembered(tmp_path):
    """The whole point of the demo: the numbers come from the pixels.

    A walk closed without being commissioned has no `approved.json`, so
    `read_survey` hands back no offsets and every position on the sheet is one
    the ensemble has just read off the prescans.
    """
    folder = _walked_folder(tmp_path)
    out = gui.read_survey(folder)
    assert out["offsets"] == {}, "nothing was committed, so nothing is restored"
    proposed, notes = gui._propose_positions(
        out["results"], out["offsets"], out.get("sources"))
    assert proposed
    assert all((notes[n] or {}).get("source") in gui.MACHINE_SOURCES
               for n in proposed)


def test_a_stale_remembered_sheet_cannot_reach_a_reopened_walk(tmp_path):
    """`open_roll` reads disk and nothing else.

    The sheet cache is real and it is a feature -- within one run, reopening
    the sheet gives back the frames that were dragged. It must not survive into
    a fresh launch, or the demo would replay last time's answer and call it a
    measurement.
    """
    from rps7200 import settings as settings_mod

    folder = _walked_folder(tmp_path)
    out = gui.read_survey(folder)
    first, _notes = gui._propose_positions(
        out["results"], out["offsets"], out.get("sources"))

    # a previous run's decisions, deliberately wrong
    path = tmp_path / "gui-settings.json"
    settings_mod.save({"sheet": {folder.name: {
        "offsets": {"1": 9.9, "2": -9.9}, "sources": {"1": "operator"},
        "ticks": {}, "rotations": {}, "flips": {}, "options": {}}}}, path)
    assert path.exists()

    again = gui.read_survey(folder)
    second, _notes = gui._propose_positions(
        again["results"], again["offsets"], again.get("sources"))
    assert second == first
    assert 9.9 not in second.values()


def test_the_same_walk_measures_the_same_way_twice():
    """"Re-measured every launch" is only legible if it is also "the same
    answer every launch". Nothing in the proposal path is random, and this is
    what says so."""
    walked = _strip()
    first, _ = gui._propose_positions(walked, {})
    second, _ = gui._propose_positions(walked, {})
    assert first == second


def test_the_launch_path_does_not_consult_the_sheet_cache():
    """`open_roll` re-proposes; `on_contact_sheet` replays. The demo opens a
    roll, so it gets the measurement. If `open_roll` ever started reading
    `_recall_sheet_state` the demo would quietly stop measuring."""
    import inspect

    body = inspect.getsource(gui.ScannerGui.open_roll)
    assert "_propose_positions(" in body
    assert "_recall_sheet_state" not in body


def test_no_film_does_not_become_a_branch_in_the_window():
    """The demo is the real software with different inputs.

    An empty transport is a fact about the film, so the backend refuses and
    the window reports it through the path it already has for a transport
    fault. Gating the controls instead was the first attempt and it skipped
    the work: `on_scan_chosen` is the sole writer of `approved.json` and the
    sole submitter of a `Roll`, so nothing between the sheet and the hold loop
    ran at all -- in the demo built to show exactly that.
    """
    import inspect

    for where in (gui._ContactSheet._changed, gui._ContactSheet._scan,
                  gui.ScannerGui.on_scan_chosen):
        assert "look_only" not in inspect.getsource(where), where.__name__


def _launch(monkeypatch, tmp_path, *argv):
    """Run `main()` as far as the window and return the session it built.

    Asking the session which opener it holds is the check. This used to read
    `main`'s source for `no_film=args.look_only`, and it went on passing while
    that line sat one indent outside `if args.demo:` -- so `make run` handed
    every launch the demo's opener, and died on a `DemoScanner` it had never
    imported. A substring cannot see which block it is in.
    """
    built = {}

    class Window:
        def __init__(self, root, session, **kwargs):
            built["session"] = session

    class Root:
        def mainloop(self):
            pass

    monkeypatch.setattr(gui, "tk", types.SimpleNamespace(Tk=Root))
    monkeypatch.setattr(gui, "ScannerGui", Window)
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(sys, "argv", [
        "gui.py", "--library", str(tmp_path / "library"),
        "--reference", str(tmp_path / "shading.npz"),
        "--rolls", str(tmp_path / "rolls"), *argv])
    assert gui.main() == 0
    return built["session"]


def test_make_run_opens_the_real_scanner(monkeypatch, tmp_path):
    """No `--demo`, no stand-in: the session keeps the opener it was built
    with, which is the one that finds the device on the bus."""
    session = _launch(monkeypatch, tmp_path)
    assert session._open_scanner == session._default_scanner


def test_no_film_is_told_to_the_backend(monkeypatch, tmp_path):
    """Which is the only place that could honestly know it."""
    import rps7200.demo

    opened = []
    monkeypatch.setattr(rps7200.demo, "DemoScanner",
                        lambda *args, **kwargs: opened.append(kwargs))
    _launch(monkeypatch, tmp_path, "--demo", "--look-only")._open_scanner()
    _launch(monkeypatch, tmp_path, "--demo")._open_scanner()
    assert [kw["no_film"] for kw in opened] == [True, False]


def test_an_empty_transport_refuses_where_the_transport_would():
    """Not a disabled button: a raised error, from the thing that would raise
    it, carrying a sentence a person can act on."""
    from rps7200.demo import DemoScanner
    from rps7200.usb_transport import UsbError

    empty = DemoScanner("library", no_film=True)
    for call in (lambda: empty.scan(resolution=300, infrared=False),
                 lambda: list(empty.scan_roll(frames=1, dry_run=True)),
                 empty.advance, empty.retreat, lambda: empty.nudge(0.5)):
        with pytest.raises(UsbError, match="no film in the transport"):
            call()

    # and with film the same methods work, or the demo would refuse its own
    # reason for existing
    loaded = DemoScanner("library")
    assert loaded.advance() is not None
    assert loaded.nudge(0.5)["param"] > 0


def test_every_proposal_is_somewhere_the_film_can_actually_go():
    """The caption showed the raw proposal; the commission delivered a snapped
    one. So a frame captioned as moving could be delivered as no move at all,
    and five other readers of `offsets` carried numbers that do not exist.
    Snapping at the seam makes every one of them agree."""
    offsets, _notes = gui._propose_positions(_strip(), {})
    assert offsets
    for value in offsets.values():
        assert value == gui.snap_offset(value)
        assert value != 0.0


def test_a_position_kept_from_a_machine_stays_a_machine_position():
    """Closing the sheet and opening it again used to relabel the whole strip.

    Every offset comes back as `kept`, and anything kept was stamped
    `operator` -- true when typing was the only way to have one, false from the
    moment the sheet began proposing them.
    """
    walked, kept = _strip(), {2: 0.5116}
    _o, notes = gui._propose_positions(walked, kept, {2: "measured"})
    assert notes[2]["source"] == "measured"
    _o, notes = gui._propose_positions(walked, kept, {2: "operator"})
    assert notes[2]["source"] == "operator"
    # nothing remembered means his, which is what an offset used to mean
    _o, notes = gui._propose_positions(walked, kept, None)
    assert notes[2]["source"] == "operator"


def test_the_sheet_opens_holding_a_proposal_for_every_frame():
    from tools.gui import _propose_positions

    offsets, notes = _propose_positions(_strip(), {})
    assert len(offsets) == 8
    assert all(notes[n]["source"] in
               ("measured", "unconfirmed", "neighbours") for n in offsets)


def test_a_position_the_operator_set_is_never_re_proposed():
    """The sheet is where he corrects this, so overwriting what he typed would
    undo the correction it exists to collect."""
    from tools.gui import _propose_positions

    offsets, notes = _propose_positions(_strip(), {3: 1.234})
    assert offsets[3] == 1.234
    assert notes[3]["source"] == "operator"


def test_a_walk_too_short_to_fit_proposes_nothing_and_still_opens():
    from tools.gui import _propose_positions

    offsets, notes = _propose_positions(_strip(1), {})
    assert offsets == {} and notes == {}


def test_a_detector_that_raises_does_not_stop_the_sheet_opening():
    """The walk has already been paid for and the frames are still choosable;
    a sheet that will not open is worse than one with no proposals."""
    from tools.gui import _propose_positions

    broken = [_Walked(1, "not an image"), _Walked(2, "nor this")]
    offsets, notes = _propose_positions(broken, {2: 0.5})
    assert offsets == {2: 0.5}
