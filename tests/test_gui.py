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
import time

import pytest

from conftest import load_tool

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
    assert "preview.rotate(r.image, r.rotation)" in inspect.getsource(
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
    a window that locks up for that long while you wait to be told about
    clipping is its own kind of unhelpful."""
    import inspect
    source = inspect.getsource(gui.ScannerGui.on_histogram)
    assert "threading.Thread" in source
    assert "self._measured.put" in source
    assert "self._measured" in inspect.getsource(gui.ScannerGui._pump), (
        "and the main loop collects it")


def test_a_histogram_window_closed_while_measuring_is_not_drawn_into():
    """The thread finishes whatever happens; the drawing has to notice."""
    import inspect
    assert "if not self.alive():" in inspect.getsource(gui._Histogram.show)


def test_the_histogram_prefers_the_scans_own_pixels():
    """And says which it used, because a reduced copy answers a slightly
    different question about how much is against the ceiling."""
    import inspect
    source = inspect.getsource(gui.ScannerGui._finest_pixels)
    assert "self._levels" in source
    assert "the scan's own" in source and "copy" in source


def test_infrared_is_drawn_grey():
    """A measurement, not a colour -- the same reason its preview is not
    tinted."""
    assert gui._CHANNEL_INK[3].count(gui._CHANNEL_INK[3][1:3]) == 3, (
        f"{gui._CHANNEL_INK[3]} should be a neutral grey")


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
    # picture on screen for the prescan and the scan alike.
    assert app.v_channel.get() == app.v_mono_channel.get()


def test_changing_the_monochrome_channel_changes_the_view(window):
    app, root = window
    app.v_film.set("bw")
    app._sync_film()
    for channel in ("R", "B", "G"):
        app.v_mono_channel.set(channel)
        app._sync_mono_view()
        root.update()
        assert app.v_channel.get() == channel


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
