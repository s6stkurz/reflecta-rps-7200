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
import time

import numpy as np
import pytest

from conftest import load_tool
from rps7200 import shortcuts
from rps7200.mono import MONO_AVERAGE

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
    source = (__import__("pathlib").Path(gui.__file__)).read_text()
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


def test_a_move_reaches_the_worst_real_misframing_and_not_much_further():
    """One command is 1.01 mm. The aperture slack a drifted frame shows is
    about 0.5 mm and the worst mis-framing on record is the 6 mm CyberView lost
    on one frame of its own strip. Reaching much past that would mean doing
    badly, in twenty nudges, what one slide button does properly."""
    assert gui.MAX_TRAVEL_MM > 6.0
    assert gui.MAX_TRAVEL_MM < gui.APERTURE_MM / 4


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
    """param 1 and param 8 of distance = 0.1057 x param + 0.1662."""
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


def test_a_mac_trackpad_scrolls_by_what_it_reports():
    """Single digits, not multiples of 120. Treating a 3 as one notch is what
    made two fingers feel like one click per gesture."""
    assert gui._wheel_amount(_Wheel(delta=-3)) == (3, False)
    assert gui._wheel_amount(_Wheel(delta=3)) == (-3, False)


def test_a_windows_notch_is_one_line_not_a_hundred_and_twenty():
    assert gui._wheel_amount(_Wheel(delta=-120)) == (1, False)
    assert gui._wheel_amount(_Wheel(delta=240)) == (-2, False)


def test_x11_sends_buttons_and_no_delta_at_all():
    assert gui._wheel_amount(_Wheel(num=4)) == (-1, False)
    assert gui._wheel_amount(_Wheel(num=5)) == (1, False)


def test_a_movement_too_small_to_matter_does_nothing():
    """A trackpad reports zeros between real movement; scrolling on them would
    be scrolling on nothing."""
    assert gui._wheel_amount(_Wheel(delta=0)) == (0, False)


def test_shift_means_sideways():
    assert gui._wheel_amount(_Wheel(delta=-3, state=1))[1] is True
    assert gui._wheel_amount(_Wheel(delta=-3, state=0))[1] is False


def test_scrolling_up_and_down_are_opposite():
    for delta in (1, 3, 7, 120, 240):
        up = gui._wheel_amount(_Wheel(delta=delta))[0]
        down = gui._wheel_amount(_Wheel(delta=-delta))[0]
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


def test_the_film_goes_back_to_where_the_walk_started():
    """A survey ends at the last picture; a roll starts where the film is."""
    assert gui.rewind_frames([4, 5, 6, 7, 8, 9]) == 5


def test_rewinding_counts_positions_not_frames_that_came_back():
    """A frame can fail and still have moved the film. Counting the results
    would leave the rewind one short for every failure, and every frame after
    that would be the wrong photograph."""
    # Six pictures walked, positions 0..5, but only four came back with one.
    assert gui.rewind_frames([0, 1, 4, 5]) == 5


def test_rewinding_falls_back_to_one_frame_a_result():
    """A transport that will not say where it is still has to be rewound."""
    assert gui.rewind_frames([None, None, None]) == 2
    assert gui.rewind_frames([None]) == 0
    assert gui.rewind_frames([]) == 0


def test_starting_part_way_in_is_rewound_past_as_well():
    """`start at 3` advances twice before the first picture, and the roll that
    follows advances twice again -- so the film has to go back that far too."""
    assert gui.rewind_frames([2, 3, 4], start_at=3) == 4


def test_the_rewind_is_never_negative():
    assert gui.rewind_frames([7, 7, 7]) == 0


# -- the window itself, where a display allows it ---------------------------
#
# The rest of this file tests the window's pure functions, deliberately: a
# suite that needs a display does not run everywhere. These two need real Tk
# widgets, because what they check is which controls are greyed out, so they
# skip rather than fail where there is no display.


@pytest.fixture
def window():
    tk = pytest.importorskip("tkinter")

    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()

    gui_mod = load_tool("gui")
    session = ScanSession(root="/tmp/none", rolls="/tmp/none", verbose=False)
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


# -- the approved-offset helpers ---------------------------------------------
#
# Pure module-level functions, so they are tested directly rather than through
# a window. What they encode is that an offset shown to the operator must be a
# position the transport can actually reach.


def test_an_offset_snaps_to_something_the_transport_can_reach():
    assert gui.snap_offset(0.48) == pytest.approx(0.4833, abs=1e-4)
    assert gui.snap_offset(-0.48) == pytest.approx(-0.4833, abs=1e-4)


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
    assert out[0].offset_mm == pytest.approx(0.4833, abs=1e-4)
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
    }))
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
        }))
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
    assert "infrared" in jpeg, "the one thing the format cannot carry"
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
    assert every.count("_redraw_strip") == 1
    assert every.count("_say") == 1
    assert "_redraw_strip" not in inspect.getsource(gui._ContactSheet._orient)


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
        rotation=0, flip=False, _frame_rotations={2: 90}, _frame_flips={2: True},
        results=[], survey=[], _surveying=False, _transport=None,
        _show=lambda _r: None, _redraw_strip=lambda: None,
    )

    def result(kind, number):
        return types.SimpleNamespace(
            kind=kind, number=number, seq=number, image=None, position=None)

    turned = result("frame", 2)
    gui.ScannerGui._add_result(stub, turned)
    assert turned.rotation == 90
    assert turned.flipped is True, "and a mirror travels the same road"

    plain = result("frame", 1)
    gui.ScannerGui._add_result(stub, plain)
    assert plain.rotation == 0, "a frame nobody turned follows the session"
    assert plain.flipped is False

    # A prescan of the same picture is a reference, not a deliverable, and the
    # session files it unturned -- so showing it turned would be a lie too.
    reference = result("prescan", 2)
    gui.ScannerGui._add_result(stub, reference)
    assert reference.rotation == 0 and reference.flipped is False


def test_a_new_strip_does_not_inherit_the_last_ones_orientations():
    """The numbers start again at 1 for a different film. Keeping them would
    turn whatever happens to land on frame 2 of the next roll."""
    walk = inspect.getsource(gui.ScannerGui.on_roll)
    assert "self._frame_rotations = {}" in walk
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


def test_no_shortcut_reaches_the_scanner():
    """The rule this whole table is written under. `on_scan`, `on_prescan`
    and the transport buttons submit immediately with no confirmation, and a
    slip on the keyboard is not a decision to spend four minutes of hardware
    or move somebody's negative."""
    import inspect
    sources = [inspect.getsource(gui.ScannerGui._actions),
               inspect.getsource(gui._ContactSheet._actions),
               inspect.getsource(gui._FrameAdjuster._actions)]
    for forbidden in shortcuts.NEVER_BOUND:
        for source in sources:
            assert forbidden not in source, forbidden


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
    handler = gui.ScannerGui._runner(stub, lambda: ran.append(1))
    assert handler(None) is None and ran == []
    stub._typing = lambda: False
    assert handler(None) == "break" and ran == [1]


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
    stub = types.SimpleNamespace(keys={"flip": "", "rotate_right": "<Key-r>"})
    assert gui.ScannerGui.accelerator(stub, "flip") == ""
    assert gui.ScannerGui.accelerator(stub, "missing_entirely") == ""
    assert gui.ScannerGui.accelerator(stub, "rotate_right") == \
        shortcuts.describe("<Key-r>")


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
