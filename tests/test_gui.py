"""The window's own decisions, and the window itself where one can be opened.

Two kinds of test live here. Most exercise module-level functions, so the logic
that must not be wrong is checked with no display at all. The rest take the
`window` fixture, which builds a real `ScannerGui` on the demo stand-in: they
run wherever this Python has Tk and there is a display (CI gives Linux one with
xvfb) and skip elsewhere. This file used to open by saying the window was
tested without opening one, because a Tk window driven from pytest once hung
on macOS at the second test; by the time the fixture's tests numbered in the
dozens, that sentence described a suite that no longer existed.

What is tested is what would mislead the operator: the stop button saying which
of the two things it will do, the option parsing that decides what the scanner
is asked for, and the resolution guard.
"""
import contextlib
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


@pytest.fixture(autouse=True)
def _demo_is_calibrated(monkeypatch):
    """The window asks for a calibration before any picture is taken, and
    these tests stand in for an operator who has given one. The demo, like
    the scanner, now refuses a corrected pass no calibration covers."""
    from rps7200.demo import DemoScanner

    monkeypatch.setattr(DemoScanner, "_calibrated", True, raising=False)


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
    # A roll given its frames by number, as the sheet's commission is, is
    # "scanning 2 chosen frames" -- and it too stops after the frame.
    chosen = _describe(Roll(frames=3, start_at=1, only=(1, 3), resolution=1800))
    assert gui.stop_label(chosen) == "Stop after this frame"


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
    assert "_reads" in inspect.getsource(gui.ScannerGui._drain), (
        "and the main loop has to collect it")


def test_one_full_resolution_read_at_a_time(window, monkeypatch):
    """Each read is a correction of the whole scan -- 1.5-2 GB on the way at
    7200 dpi RGBI -- and clicking through the filmstrip started one for every
    pass passed over, all at once, beside a scanner held open. The pass on
    screen when the read in flight comes back is the one read next; those
    clicked past on the way are not read at all."""
    import threading

    app, root = window
    started, release = [], threading.Event()

    def load(entry):
        started.append(entry)
        release.wait(10)
        return np.zeros((4, 4, 3), np.uint16), {}

    # Read once, then corrected: `library.load`, then `library.correct`.
    monkeypatch.setattr(gui.library, "load", load)
    monkeypatch.setattr(gui.library, "correct", lambda image, record: (
        image, {"corrected": "applied"}))
    passes = [types.SimpleNamespace(seq=n, entry=f"e{n}", label=f"pass {n}",
                                    image=np.zeros((2, 2, 3), np.uint8))
              for n in (1, 2, 3)]
    for shown in passes:
        app.current = shown
        app._load_full(shown)
    time.sleep(0.2)
    assert started == ["e1"]
    release.set()
    deadline = time.monotonic() + 10
    while started != ["e1", "e3"] and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert started == ["e1", "e3"]
    app.current = None


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
    assert "self._measured" in inspect.getsource(gui.ScannerGui._drain), (
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
    pump = inspect.getsource(gui.ScannerGui._drain)
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
        busy=False, sheet=None, _calibration_missing=lambda parent=None: False,
        survey=survey, _transport=transport, _survey_start=1,
        _survey_predpi=300, orientations={},
        v_ir=var(False), v_fast_ir=var(True), v_film=var("negative"),
        v_meter=var("none"), v_correct=var(False), v_mono=var(False),
        v_reverse=var(False), v_mono_channel=var("G"),
        fields={"roll": var("sheet-roll")},
        _per_frame_seconds=lambda **kw: 60.0,
        _approved_note=lambda *a: "", _options_note=lambda *a: "",
        _write_approved=lambda *a: None, _notes=FilmNotes,
        _roll_folder=lambda: tmp_path / "rolls" / "sheet-roll",
        _show_roll_name=lambda name, ours=True, folder=None: None,
        _edges_pending=lambda: "", _update_roll_eta=lambda: None,
        _tags=lambda: (), _say=lambda *a: None,
        session=types.SimpleNamespace(submit=submitted.append,
                                      rolls=str(tmp_path / "rolls")),
        _working=lambda: False, _hand_over=submitted.append,
        _no_scanner=lambda *a, **k: False,
        _scanned_in=lambda folder: set(),
        _refused_up_front=lambda *a, **k: False,
        _sheet_roll=None, root=None,
        _walk_replaced=lambda: False,
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


def test_the_hand_move_reverse_tick_never_mirrors_a_sheets_positions(
        monkeypatch, tmp_path):
    """The Transport panel's 'reverse the direction' is remembered between
    launches and was handed to every roll the sheet commissioned, where it
    negated each approved position before the hold loop ran. The loop is
    closed in the picture, so the mirror was reached and logged `held`: a
    frame set 40 units right was scanned 40 units left, all roll long."""
    from conftest import ScannerOnStrip

    from rps7200.session import ScanSession

    reached = []

    class Recording(ScannerOnStrip):
        def _hold_to_approved(self, index, image, prescan_resolution,
                              approved, **kw):
            reached.append((approved.offset_mm, dict(kw)))
            return super()._hold_to_approved(index, image, prescan_resolution,
                                             approved, **kw)

    submitted = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    survey = [types.SimpleNamespace(number=n, position=n - 1)
              for n in range(1, 4)]
    window = _stub_window(survey, 0, submitted, tmp_path)
    window.v_reverse = types.SimpleNamespace(get=lambda: True)
    reference = np.zeros((8, 8, 3), np.uint8)
    gui.ScannerGui.on_scan_chosen(
        window, (2,), (Approved(number=2, offset_mm=0.5, reference=reference),),
        {"dpi": "300", "predpi": "300", "ir": False, "fast_ir": True,
         "film": "negative", "meter": "none", "correct": False})
    assert len(submitted) == 1

    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: Recording(at=0), verbose=False)
    s.start()
    s.submit(submitted[0])
    s.shutdown()
    s.join(timeout=20)

    assert [offset for offset, _ in reached] == [0.5]
    assert not any(kw.get("reverse") for _, kw in reached), reached
    settings = json.loads((tmp_path / "rolls" / "sheet-roll" / "roll.json")
                          .read_text(encoding="utf-8"))["settings"]
    assert not settings.get("reverse_hold")
    # Nor does reopening an older roll that recorded it put the tick back.
    assert "reverse" not in gui.restorable({"reverse_hold": True})


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


def test_a_reopened_walk_knows_the_entry_of_each_prescan(tmp_path):
    """Its results took their entry only from approved.json, so a walk
    reopened before anything was approved gave every reference none, and
    the approvals made from it wrote `reference_entry` ""."""
    folder = tmp_path / "walk"
    _write_survey(folder, frames=2)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    lib = tmp_path / "lib"
    for record in survey["frames"]:
        (lib / f"entry-{record['number']}").mkdir(parents=True)
        record["prescan_entry"] = f"entry-{record['number']}"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")

    out = gui.read_survey(folder, library_root=lib)
    assert {r.number: r.entry for r in out["results"]} == {
        1: lib / "entry-1", 2: lib / "entry-2"}


def test_a_walk_reopened_beside_its_roll_is_not_given_the_rolls_prescans(
        tmp_path):
    """A walk from before records named their entries is joined on the
    library. The roll after it files its verification prescans into the same
    folder under the same numbers, and taken newest first they became the
    walk's references -- written into approved.json by the next approval."""
    from rps7200 import session as rsession

    folder = tmp_path / "walk"
    _write_survey(folder, frames=2)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey.update(started="2026-09-20T10:00:00+00:00",
                  finished="2026-09-20T10:01:00+00:00")
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "roll.json").write_text(json.dumps({
        "roll": "a-strip", "started": "2026-09-20T10:30:00+00:00",
        "frames": []}), encoding="utf-8")
    lib = tmp_path / "lib"
    for name, number, created in (
            ("walk-1", 1, "2026-09-20T10:00:20+00:00"),
            ("roll-1", 1, "2026-09-20T10:31:00+00:00"),
            ("roll-2", 2, "2026-09-20T10:32:00+00:00")):
        (lib / name).mkdir(parents=True)
        (lib / name / "scan.json").write_text(json.dumps({
            "id": name, "created": created, "tags": ["roll", "prescan"],
            "extra": {"roll_membership": rsession.roll_membership(
                "a-strip", number, "prescan", folder)}}), encoding="utf-8")

    out = gui.read_survey(folder, library_root=lib)
    assert {r.number: r.entry for r in out["results"]} == {
        1: lib / "walk-1", 2: None}


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


def _walk_beside(folder, positions):
    """A walk's survey.json in `folder`, beside whatever roll.json is there,
    as 8a9ba17's session left a date-named folder: `dry_run` at the top."""
    _write_survey(folder, frames=len(positions))
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["dry_run"] = True
    for record, position in zip(survey["frames"], positions):
        record["transport_position"] = position
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")


@pytest.mark.parametrize("beside", [False, True],
                         ids=["roll alone", "walk beside"])
@pytest.mark.parametrize("which, scanned, said_about", [
    ("rewound", [6, 7, 8, 9], ["frame 7", "frame 8"]),
    ("reinserted", [4, 5, 6, 7, 8], ["frame 6"]),
    ("one-frame", [6, 7, 8], ["frame 7"]),
])
def test_an_8a9ba17_roll_that_went_over_a_place_twice_reopens_as_scanned(
        tmp_path, which, scanned, said_about, beside):
    """Two 8a9ba17 runs into one roll with the film taken back between them,
    so some places were scanned twice. The window called strip frames 10 and
    11 of the rewound roll done, 11 of the reinserted one and 9 of the last,
    none of them ever scanned -- the one-frame roll's picture of frame 7 was
    that 9. It says which places hold two scans, and the browser's list
    agrees.

    Said with a walk's survey.json beside the roll as well, which is where
    each of the three session roll.json files under `rolls/` is: the roll
    was read there with nothing to say to, so both scans sat on one number
    without a word -- and said only once when the roll.json is alone, where
    the window reads it as the walk too."""
    from conftest import resumed_by_8a9ba17

    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "roll.json").write_text(json.dumps(resumed_by_8a9ba17(which)),
                                      encoding="utf-8")
    if beside:
        _walk_beside(folder, [5, 6, 7])
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert sorted(out["scanned"]) == scanned
    assert len(said) == len(said_about), said
    for line, place in zip(said, said_about):
        assert f"{place} of the strip" in line, line
    assert sorted(gui.roll_summary(folder)["done"]) == scanned


@pytest.mark.parametrize("beside", [False, True],
                         ids=["roll alone", "walk beside"])
def test_an_old_rolls_stale_72_is_said_wherever_the_roll_is(tmp_path,
                                                            beside):
    """The last frame of the tied 8a9ba17 roll, its counter read as 72. It is
    numbered where its run puts it, 8, and said once -- beside a walk too,
    where the roll used to be read in silence."""
    from conftest import resumed_by_8a9ba17

    folder = tmp_path / "r"
    folder.mkdir()
    old = resumed_by_8a9ba17("tied")
    old["frames"][-1]["transport_position"] = 72
    (folder / "roll.json").write_text(json.dumps(old), encoding="utf-8")
    if beside:
        _walk_beside(folder, [0, 1, 2])
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert sorted(out["scanned"]) == [1, 2, 3, 6, 7, 8]
    assert len(said) == 1, said
    assert "frame 6 of r" in said[0] and "72, which no strip has" in said[0]


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


def test_a_walk_whose_transport_stalled_reopens_every_prescan(tmp_path):
    """One advance of a walk that did not move, 5, 6, 6, 7. Its frames 3 and
    4 came back as two frame 8s: two Results the sheet ticks by one number,
    so one of the two prescans could never be chosen on its own. One walk
    numbers no two frames alike."""
    folder = tmp_path / "stall"
    _walk_beside(folder, [5, 6, 6, 7])
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert [r.number for r in out["results"]] == [6, 7, 8, 9]
    assert len({r.seq for r in out["results"]}) == 4
    assert len(said) == 2 and not any("two rolls" in s for s in said), said


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
# Everything from here that takes `window` needs real Tk widgets -- which
# controls are greyed out, what a dialog was asked, what the sheet shows -- so
# it skips rather than fails where there is no display.


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
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    # Neither the stand-in's pictures nor the window's memory come from the
    # checkout: "library" and gui-settings.json are relative to wherever
    # pytest runs, which put the operator's own entries in front of the demo
    # (and signed every one of them) and wrote test folders into the file
    # the operator's next launch restores from. The pictures' folder is not
    # the session's, so what a test files is not drawn on as a picture.
    stored = tmp_path / "demo-library"
    session._open_scanner = lambda: DemoScanner(str(stored), speed=1e9)
    app = gui_mod.ScannerGui(root, session, demo=True,
                             settings_path=tmp_path / "gui-settings.json")
    root.update()
    try:
        yield app, root
    finally:
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_the_window_says_when_its_settings_could_not_be_read(window, tmp_path):
    """They were moved aside and the window opened on its defaults with
    nothing on screen to say so."""
    import tkinter

    from rps7200.session import ScanSession

    app, root = window
    broken = tmp_path / "broken-settings.json"
    broken.write_text('{"controls": {"dpi": ', encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"), verbose=False)
    session.start = lambda: None                 # no worker, no device
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True, settings_path=broken)
    try:
        assert "could not be read" in other.log.get("1.0", "end")
    finally:
        other._alive = False
        top.destroy()


def test_a_window_opened_without_its_locks_says_so_on_screen(tmp_path,
                                                            monkeypatch):
    """Printed to stderr alone, which `make run` on Windows sends to a log
    shown only for a window that ends within five seconds: two windows
    could share a library with nothing on screen to say the guard was
    off. `hold_window` hands the line to the window's log too."""
    import contextlib

    plain = tmp_path / "plain"
    plain.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("RPS7200_LOCKS", str(plain / "locks"))
    notes: list[str] = []
    with contextlib.ExitStack() as held:
        refused = gui.hold_window(
            held, "the window", library=tmp_path / "library",
            rolls=tmp_path / "rolls",
            settings_file=tmp_path / "gui-settings.json", say=notes.append)
    assert refused is None, "opened all the same"
    [line] = notes
    assert line.startswith("could not take the window lock on ")
    assert str(tmp_path / "rolls") in line and "library lock" in line
    assert line.endswith("the window opens all the same, and a second "
                         "window or a library rewrite (compact --write, "
                         "migrations, file-spool) on these folders will not "
                         "be refused")


def test_what_main_heard_before_the_window_reaches_its_log(window, tmp_path):
    import tkinter

    from rps7200.session import ScanSession

    app, root = window
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"), verbose=False)
    session.start = lambda: None                 # no worker, no device
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True,
                           settings_path=tmp_path / "other-settings.json",
                           notes=["could not take the window lock on x"])
    try:
        assert "could not take the window lock on x" in other.log.get(
            "1.0", "end")
    finally:
        other._alive = False
        top.destroy()


def test_a_key_tk_does_not_know_costs_that_key_and_not_the_window(tmp_path):
    """gui-settings.json is meant to be edited by hand, and `resolve` takes
    any string. `<Foo>` raised TclError from the constructor's bind, so one
    typo in a shortcut and the window never opened again."""
    tk = pytest.importorskip("tkinter")

    from rps7200 import shortcuts
    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    stored = tmp_path / "gui-settings.json"
    stored.write_text(json.dumps({"shortcuts": {"save_as": "<Foo>"}}),
                      encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    session._open_scanner = lambda: DemoScanner("library", speed=1e9)
    try:
        app = load_tool("gui").ScannerGui(root, session, demo=True,
                                          settings_path=str(stored))
        default = shortcuts.defaults()["save_as"]
        assert default in app._bound and "<Foo>" not in app._bound
        assert "<Foo>" in app.log.get("1.0", "end")
    finally:
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_a_refused_key_does_not_fall_back_onto_another_actions_key(tmp_path):
    """Save as's `<Foo>` is refused and it falls back to its default -- which
    the operator had given to previous pass. Tk's `bind` replaces what a key
    did, so previous pass's shortcut silently saved instead, while the log
    said "using its default". The default is left to its owner."""
    tk = pytest.importorskip("tkinter")

    from rps7200 import shortcuts
    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    default = shortcuts.defaults()["save_as"]
    stored = tmp_path / "gui-settings.json"
    # previous_pass is bound before save_as, so without the check the
    # fallback is the binding that stands.
    stored.write_text(json.dumps({"shortcuts": {
        "save_as": "<Foo>", "previous_pass": default}}), encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    session._open_scanner = lambda: DemoScanner("library", speed=1e9)
    try:
        app = load_tool("gui").ScannerGui(root, session, demo=True,
                                          settings_path=str(stored))
        assert app._bound.count(default) == 1, app._bound
        said = app.log.get("1.0", "end")
        assert "left unbound" in said and "previous_pass" in said, said
    finally:
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_a_hand_edited_key_already_taken_in_the_window_is_put_back(tmp_path):
    """Two actions of the main window on one key from the settings file: both
    were bound and the key ran whichever was bound last. The one the file
    moved goes back to its default, and the log says so."""
    tk = pytest.importorskip("tkinter")

    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    save = shortcuts.defaults()["save_as"]
    stored = tmp_path / "gui-settings.json"
    stored.write_text(json.dumps({"shortcuts": {"flip": save}}),
                      encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    session._open_scanner = lambda: DemoScanner("library", speed=1e9)
    try:
        app = load_tool("gui").ScannerGui(root, session, demo=True,
                                          settings_path=str(stored))
        assert app.keys["save_as"] == save
        assert app.keys["flip"] == shortcuts.defaults()["flip"]
        assert "flip" not in app.shortcut_overrides
        said = app.log.get("1.0", "end")
        assert "already another action's key" in said and "flip" in said, said
    finally:
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_the_editor_refuses_a_key_held_under_another_spelling(tmp_path):
    """A hand edit may spell Save As's key `<Control-s>`, which Tk binds as
    the `<Control-Key-s>` a captured key press is. The editor looked the
    captured spelling up as written, found nothing, and gave the key to a
    second action -- and Tk's second binding replaced the first."""
    tk = pytest.importorskip("tkinter")

    from rps7200 import shortcuts
    from rps7200.demo import DemoScanner
    from rps7200.session import ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    stored = tmp_path / "gui-settings.json"
    stored.write_text(json.dumps({"shortcuts": {
        "save_as": f"<{shortcuts.ACCEL}-s>"}}), encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    session._open_scanner = lambda: DemoScanner("library", speed=1e9)
    try:
        gui = load_tool("gui")
        app = gui.ScannerGui(root, session, demo=True,
                             settings_path=str(stored))
        editor = gui._ShortcutSettings(app)
        editor._set("flip", f"<{shortcuts.ACCEL}-Key-s>")
        assert editor.keys["flip"] == shortcuts.defaults()["flip"]
        assert "already does" in editor.v_note.get()
        assert shortcuts.conflicts(app.keys) == {}
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


def test_the_infrared_tie_follows_the_infrared_box(window):
    """The box only redid the estimate, so the tie under it stayed live after
    infrared was unticked, and greyed when it was ticked after a black and
    white film had greyed it."""
    app, root = window
    app.v_film.set("bw")
    app._sync_film()
    app.v_film.set("negative")
    app._sync_film()
    assert app.v_ir.get() is False
    app.c_ir.invoke()                                  # ticked
    assert str(app.c_fast_ir.cget("state")) == "normal"
    app.c_ir.invoke()                                  # and unticked
    assert str(app.c_fast_ir.cget("state")) == "disabled"


def test_a_folder_typed_into_save_scans_to_is_used(window, tmp_path):
    """Only Choose ... and Clear reached the session: a path typed or pasted
    into the box was ignored for the rest of the session -- and saved, and
    used from the next launch on."""
    app, root = window
    app.v_outdir.set(f"  {tmp_path / 'typed'}  ")
    _press(app.e_outdir, "<Return>")
    assert app.session.out_dir == tmp_path / "typed"
    app.v_outdir.set("")
    _press(app.e_outdir, "<FocusOut>")
    assert app.session.out_dir is None, "and emptied by hand, it stops"


def test_the_rail_in_the_histogram_is_the_sensors(window, tmp_path):
    """Every sample railed on the sensor, in columns whose gain is below one:
    corrected, they come back near two thirds of full scale, and the table
    measured on the corrected pixels said nothing was at or near full. The
    operator judges an exposure by that table."""
    from rps7200 import library
    from rps7200.library import FilmNotes
    from rps7200.session import Result
    from rps7200.shading import MASK_USED, ShadingReference

    app, root = window
    width, lines = 12, 8
    ccd = 2 * width + 4
    mask = bytearray([0x70]) * ccd
    for j in range(width):
        mask[1 + 2 * j] = MASK_USED
    reference = ShadingReference(
        ref={c: np.full(ccd, 60000.0) for c in range(4)},
        mean={c: 40000.0 for c in range(4)}, pixels_per_line=ccd,
        dark={c: np.full(ccd, 170.0) for c in range(4)},
        dark_mean={c: 170.0 for c in range(4)})
    raw = np.full((lines, width, 3), 65535, np.uint16)
    entry = library.save(
        raw, {"resolution_dpi": 900, "channels": 3, "film": "negative",
              "channel_order": list("RGB"), "width": width, "height": lines},
        root=tmp_path / "library", film=FilmNotes(frame="rail"),
        reference=reference, ccd_mask=bytes(mask))
    corrected, _ = library.corrected(entry)
    assert int(corrected.max()) < 60000, "the premise: correction hid the rail"

    result = Result(seq=1, kind="scan", label="railed", image=corrected,
                    meta={}, entry=entry)
    app._add_result(result)
    app._show(result)
    at_full = app.histogram._cells[(1, 2)]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and app._levels_seq != 1:
        root.update()
        time.sleep(0.02)
    assert app._levels_seq == 1, "the scan's own pixels never arrived"
    # Then its second measurement, which is the one that counts.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and at_full.cget("text") == "--":
        root.update()
        time.sleep(0.02)
    assert at_full.cget("text") == "100.00%", at_full.cget("text")
    assert "sensor" in app.histogram.v_source.get()


def test_the_full_view_reads_its_entry_once_and_names_the_rail_honestly(
        window, tmp_path, monkeypatch):
    """The rail counts read the entry raw and `library.corrected` then read
    it again: two reads of a 7200 dpi scan where one was enough. And an entry
    filed corrected -- a legacy one -- had its rail counted on corrected
    pixels under a caption saying the sensor read them."""
    from rps7200 import library
    from rps7200.library import FilmNotes
    from rps7200.session import Result

    app, root = window
    image = np.full((8, 12, 3), 65535, np.uint16)
    entry = library.save(
        image, {"resolution_dpi": 900, "channels": 3, "film": "negative",
                "channel_order": list("RGB"), "width": 12, "height": 8},
        root=tmp_path / "library", film=FilmNotes(frame="legacy"),
        corrections=["shading"])
    reads = []
    loading = library.load
    monkeypatch.setattr(library, "load",
                        lambda path: reads.append(path) or loading(path))

    result = Result(seq=1, kind="scan", label="legacy", image=image,
                    meta={}, entry=entry)
    app._add_result(result)
    app._show(result)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and app._levels_seq != 1:
        root.update()
        time.sleep(0.02)
    assert app._levels_seq == 1, "the scan's own pixels never arrived"
    assert len(reads) == 1, f"read {len(reads)} times"
    assert app._rail is None
    assert "sensor" not in app.histogram.v_source.get()


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


# -- a calibration is asked for when a scan needs one, and only then ---------


CONTROLS = ("TEntry", "TCombobox", "TCheckbutton", "TRadiobutton", "TButton",
            "TSpinbox", "Scale", "TScale")


def _toplevels(root) -> list:
    return [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]


def _prompt_button(app, text):
    """The prompt's button with this label, found the way a person finds it."""
    for widget in gui._descendants(app._calibrate_prompt):
        if widget.winfo_class() == "TButton" and widget.cget("text") == text:
            return widget
    raise AssertionError(f"no {text!r} button in the prompt")


def _prompt_tick(app, text="The film is in the transport"):
    """The prompt's checkbox with this label, found the same way."""
    for widget in gui._descendants(app._calibrate_prompt):
        if widget.winfo_class() == "TCheckbutton" and widget.cget("text") == text:
            return widget
    raise AssertionError(f"no {text!r} tick in the prompt")


def _press(top, key="<Return>"):
    """Fire what ``top`` binds to ``key``, as pressing it would.

    Not `event_generate`: a generated key goes to the window with the focus,
    and a prompt over the tests' withdrawn window is never mapped, so it has
    none and the key is dropped -- a test of the binding that passes because
    nothing happened. This calls the bound command by its Tcl name instead,
    with the nineteen fields a key event carries (tkinter's `_subst_format`).
    """
    script = top.bind(key)
    assert script, f"nothing is bound to {key}"
    name = script.split("[", 1)[1].split()[0]
    fields = ["0"] * 19
    fields[10] = ""                                    # %A, the character
    fields[12] = key.strip("<>")                       # %K, the keysym
    fields[14] = str(top)                              # %W, the window
    fields[15] = "2"                                   # %T, KeyPress
    top.tk.call(name, *fields)


def _never_confirm(*_a, **_k):
    raise AssertionError("asked about the scan before the calibration")


def test_without_a_calibration_nothing_is_asked_and_nothing_is_locked(window):
    """Stefan could not change the frame count until he had calibrated. The
    window opens with every setting live and no prompt in front of it; a
    calibration changes the label on its own button and nothing else."""
    from rps7200.session import Event

    app, root = window
    deadline = time.monotonic() + 10
    while not app.session.inquiry_text and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert app.session.inquiry_text, "the demo scanner never opened"
    for _ in range(10):                  # the old prompt came 50 ms after this
        root.update()
        time.sleep(0.02)
    assert app._calibrate_prompt is None and not _toplevels(root)
    assert app.b_calibrate.cget("text") == "Calibrate"

    def states():
        return {str(w): str(w.cget("state")) for w in gui._descendants(root)
                if w.winfo_class() in CONTROLS}

    before = states()
    app._handle(Event(kind="calibrated", done=1))
    root.update()
    assert states() == before
    assert app.b_calibrate.cget("text") == "Calibrate again"


@pytest.mark.parametrize("press", ["on_prescan", "on_scan", "on_roll",
                                   "the prescan key", "the sheet"])
def test_a_scan_without_a_calibration_asks_for_one_and_scans_nothing(
        window, monkeypatch, press):
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel", _never_confirm)
    monkeypatch.setattr(gui.messagebox, "showinfo", _never_confirm)
    do = {"the prescan key": lambda: app._confirm_then(
              "a prescan", lambda: "cost", app.on_prescan),
          "the sheet": lambda: app.on_scan_chosen((1, 2))}.get(
              press, getattr(app, press, None))
    do()
    root.update()
    assert jobs == []
    assert app._calibrate_prompt is not None
    assert app._calibrate_prompt.winfo_exists()
    do()                                   # pressed again: raised, not doubled
    root.update()
    assert len(_toplevels(root)) == 1


def test_calibrate_now_starts_one_closes_the_prompt_and_scans_next_time(
        window, monkeypatch):
    from rps7200.session import Calibrate, Prescan

    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.on_prescan()
    _prompt_tick(app).invoke()
    _prompt_button(app, "Calibrate now").invoke()
    root.update()
    assert [type(j) for j in jobs] == [Calibrate]
    assert jobs[0].mode == "measure"
    assert app._calibrate_prompt is None and not _toplevels(root)
    # Queued behind the calibration rather than asking a second time: the
    # session runs its jobs in order.
    app.on_prescan()
    assert [type(j) for j in jobs] == [Calibrate, Prescan]


# The film is confirmed before a calibration starts. Only Stefan can see the
# transport, and an empty one calibrated preceded a wedge; the button used to
# start one on the spot, and a bare Return in the prompt did too.


def _no_cache(app, tmp_path):
    """A session whose cached reference is not there, so nothing can reuse."""
    app.session.reference = str(tmp_path / "calibration" / "shading.npz")


def _a_cache(app, tmp_path):
    """A session with a cached reference on disk, from some other power-on."""
    path = tmp_path / "calibration" / "shading.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a reference")
    app.session.reference = str(path)


@pytest.mark.parametrize("ask", ["the Calibrate button", "a scan"])
def test_calibrate_now_asks_for_the_film_before_it_starts_anything(
        window, monkeypatch, tmp_path, ask):
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    (app.b_calibrate.invoke if ask == "the Calibrate button"
     else app.on_prescan)()
    root.update()
    assert app._calibrate_prompt is not None
    assert jobs == [], "calibrated without asking what is in the transport"
    tick = _prompt_tick(app)
    assert "selected" not in tick.state()
    _prompt_button(app, "Calibrate now").invoke()
    root.update()
    assert jobs == [] and app.calibrated is False
    assert app._calibrate_prompt is not None, "refused by closing the question"
    tick.invoke()
    _prompt_button(app, "Calibrate now").invoke()
    root.update()
    assert [j.mode for j in jobs] == ["measure"]
    assert app._calibrate_prompt is None and not _toplevels(root)


def test_a_bare_return_does_not_calibrate(window, monkeypatch, tmp_path):
    """Return was bound to "Calibrate now" -- the answer with the scanner
    time and the wedge behind it -- so one keystroke meant for something
    else started a calibration. It asks for the tick now, and Return then."""
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    app.on_scan()
    top = app._calibrate_prompt
    _press(top, "<Return>")
    root.update()
    assert jobs == []
    assert app._calibrate_prompt is top and top.winfo_exists()
    notes = [w.cget("text") for w in gui._descendants(top)
             if w.winfo_class() == "TLabel"]
    assert any("Nothing started" in str(n) for n in notes), notes
    _prompt_tick(app).invoke()
    _press(top, "<Return>")
    root.update()
    assert [j.mode for j in jobs] == ["measure"]
    assert app._calibrate_prompt is None


def test_the_tick_is_asked_again_every_time(window, monkeypatch, tmp_path):
    """Not remembered from the last calibration: a strip taken out since then
    is exactly the case the question is for."""
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    app.b_calibrate.invoke()
    _prompt_tick(app).invoke()
    _prompt_button(app, "Calibrate now").invoke()
    root.update()
    assert len(jobs) == 1
    app.b_calibrate.invoke()                            # "Calibrate again"
    root.update()
    assert "selected" not in _prompt_tick(app).state()
    _prompt_button(app, "Calibrate now").invoke()
    assert len(jobs) == 1


def test_the_panels_calibrate_button_raises_the_prompt_it_does_not_answer_it(
        window, monkeypatch, tmp_path):
    """With the "calibrate first" prompt open, the panel's button used to
    calibrate past it. It raises the one prompt, which then does the asking."""
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    app.on_scan()
    assert app._calibrate_prompt is not None
    app.b_calibrate.invoke()
    root.update()
    assert jobs == []
    assert len(_toplevels(root)) == 1
    _prompt_tick(app).invoke()
    _prompt_button(app, "Calibrate now").invoke()
    root.update()
    assert len(jobs) == 1
    assert app._calibrate_prompt is None and not _toplevels(root)


def test_the_cached_reference_loads_without_the_question(window, monkeypatch,
                                                       tmp_path):
    """Loading a file moves nothing, so neither the prompt's "Use the cached
    one" nor the panel set to reuse waits for the tick."""
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _a_cache(app, tmp_path)
    app.on_prescan()
    _prompt_button(app, "Use the cached one").invoke()
    root.update()
    assert [j.mode for j in jobs] == ["reuse"]
    assert app._calibrate_prompt is None

    app.v_shading.set("reuse")
    app.b_calibrate.invoke()
    root.update()
    assert [j.mode for j in jobs] == ["reuse", "reuse"]
    assert not _toplevels(root)


def test_reuse_of_a_reference_from_before_this_window_shows_its_age_first(
        window, monkeypatch, tmp_path):
    """"Reuse" is remembered across launches, and the button then loaded the
    cached file whatever its age -- another power-on's, a lamp change ago --
    past the one prompt that says how old it is."""
    import os

    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _a_cache(app, tmp_path)
    old = time.time() - 14 * 24 * 3600
    os.utime(app.session.reference, (old, old))
    app.v_shading.set("reuse")
    app.b_calibrate.invoke()
    root.update()
    assert jobs == []
    assert app._calibrate_prompt is not None
    notes = [str(w.cget("text")) for w in gui._descendants(app._calibrate_prompt)
             if w.winfo_class() == "TLabel"]
    assert any("from a different power-on" in n for n in notes), notes
    _prompt_button(app, "Use the cached one").invoke()
    root.update()
    assert [j.mode for j in jobs] == ["reuse"], "still his to choose"


def test_reuse_with_nothing_cached_is_a_calibration_and_asks(
        window, monkeypatch, tmp_path):
    """`ensure_shading` measures when there is no file to reuse, so "reuse"
    with nothing cached moves the carriage like any calibration."""
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    app.v_shading.set("reuse")
    app.b_calibrate.invoke()
    root.update()
    assert jobs == []
    assert app._calibrate_prompt is not None


def test_the_prompts_other_answers_do_not_calibrate(window, monkeypatch,
                                                   tmp_path):
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    _no_cache(app, tmp_path)
    app.b_calibrate.invoke()
    _prompt_tick(app).invoke()
    _press(app._calibrate_prompt, "<Escape>")
    root.update()
    assert jobs == [] and app._calibrate_prompt is None
    assert not _toplevels(root)


def test_not_now_leaves_it_to_be_asked_again(window, monkeypatch):
    app, root = window
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.on_prescan()
    _prompt_button(app, "Not now").invoke()
    root.update()
    assert jobs == [] and not _toplevels(root) and app.calibrated is False
    app.on_prescan()
    assert app._calibrate_prompt is not None


def test_a_calibration_that_left_no_reference_is_asked_for_again(window,
                                                               monkeypatch):
    """Queued counts as calibrated; the session's answer when it ends is the
    one that stands."""
    from rps7200.session import Event

    app, root = window
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    app.on_calibrate("measure")
    assert app.calibrated is True
    app._handle(Event(kind="calibrated", done=0))
    assert app.calibrated is False
    assert app.b_calibrate.cget("text") == "Calibrate"
    app.on_prescan()
    assert app._calibrate_prompt is not None


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


def _press_roll(app, monkeypatch, last="3", first="1", answer=True):
    """Press Scan roll with these fields; return (dialog texts, jobs, errors)."""
    said, jobs, errors = [], [], []

    def ask(title, message, **kw):
        said.append(message)
        return answer

    monkeypatch.setattr(gui.messagebox, "askokcancel", ask)
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.calibrated = True             # the question here is the roll's own
    app.v_last.set(last)
    app.v_startat.set(first)
    app.v_dryrun.set(True)
    app.on_roll()
    return said, jobs, errors


def test_aiming_at_a_prescan_the_edges_cannot_be_read_at_is_refused(
        window, monkeypatch):
    """At 600 dpi every frame is refused by the edge reader, so 'aim each
    frame' aims nothing while the roll looks centred. The command line
    refuses `--correct` there; the window warned under one OK."""
    app, _root = window
    app.v_film.set("negative")
    app.v_predpi.set("600")
    app.v_correct.set(True)
    said, jobs, errors = _press_roll(app, monkeypatch)
    assert jobs == [] and said == [], "the roll was offered anyway"
    assert errors and "not read at a 600 dpi" in errors[0][1]
    # The walk alone is still only warned about: it is a survey either way.
    app.v_correct.set(False)
    said, jobs, errors = _press_roll(app, monkeypatch)
    assert errors == [] and len(jobs) == 1
    assert "not read at a 600 dpi" in said[0]


def test_the_roll_pace_is_timed_from_where_the_seek_landed(window,
                                                           monkeypatch):
    """The Roll button winds the film to its first frame inside the job, and
    the clock started at the press -- so a minute's wind back became frame
    1's time, and "left" read about 19 minutes on a 15-frame walk that had
    about five. It restarts when the session says where the seek put the
    film, which it does before the first frame."""
    from rps7200.session import Event

    app, root = window
    _press_roll(app, monkeypatch, last="15")
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


def test_turning_an_old_frame_does_not_move_the_forecast(window):
    """Turning a frame -- in the sheet or on the filmstrip -- set the window's
    "where is the film" to that frame's walk position, and the Roll dialog
    then forecast from there: the film on frame 17, the dialog saying frame
    3 and nothing to wind. A pass arriving still says where it was taken."""
    from rps7200.session import Event, Result

    app, root = window
    app._handle(Event(kind="transport", done=16))
    old = Result(seq=-5, kind="prescan", label="frame 3 (reopened)",
                 image=np.zeros((8, 8, 3), np.uint8), meta={}, position=2,
                 number=3)
    old.rotation, old.flipped, old.hidden, old.supersedes = 90, False, False, None
    app.results.append(old)
    app.remember_arrangement(old)
    assert app._transport == 16
    app._handle(Event(kind="result", result=Result(
        seq=7, kind="prescan", label="prescan", meta={}, position=4,
        image=np.zeros((8, 8, 3), np.uint8))))
    assert app._transport == 4


def test_the_window_leaves_refusing_a_far_frame_to_the_seek(window,
                                                            monkeypatch):
    """First frame 45 was refused by the window, on the premise that the film
    would otherwise be wound first. It would not: the seek refuses a frame no
    strip has before it reads or moves anything, and says so on the
    failed-job path. One refusal, in the backend."""
    from conftest import StripScanner

    from rps7200 import session
    from rps7200.session import Event

    app, root = window
    app._handle(Event(kind="transport", done=0))
    said, jobs, errors = _press_roll(app, monkeypatch, first="45", last="")
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
    said, _jobs, _errors = _press_roll(app, monkeypatch, last="",
                                       answer=False)
    assert "every frame from frame 1 to the end of the strip" in said[0]
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


def test_a_position_that_is_not_a_number_is_no_move_at_all(tmp_path):
    """`min(M, nan)` is M, so a NaN offset -- a hand-edited approved.json or
    gui-settings.json, json reads one happily -- snapped to the largest
    forward move there is, 88.8 units, and was held to without a word."""
    assert gui.snap_offset(float("nan")) == 0.0
    assert gui.snap_offset(float("inf")) == 0.0
    folder = tmp_path / "a-roll"
    folder.mkdir()
    (folder / "approved.json").write_text(json.dumps({
        "frames": [{"number": 2, "offset_mm": float("nan")},
                   {"number": 3, "offset_mm": 0.5}]}), encoding="utf-8")
    offsets = gui.read_approved(folder)[0]
    assert offsets == {3: 0.5}
    state = gui.ScannerGui._clean_sheet_state(
        {"offsets": {"2": float("nan"), "3": 0.5, "4": "inf"}})
    assert state["offsets"] == {3: 0.5}


def test_a_saved_file_says_the_resolution_it_was_scanned_at(tmp_path):
    """Save as, Save all and Export passed no resolution, so every file from
    the window said 72 dpi or nothing, depending on what was installed."""
    from rps7200 import library, tiff

    entry = library.save(
        np.full((4, 6, 3), 1000, np.uint16),
        {"resolution_dpi": 900, "channels": 3, "film": "negative",
         "channel_order": list("RGB"), "width": 6, "height": 4},
        root=tmp_path / "lib")
    result = types.SimpleNamespace(entry=entry, rotation=0, flipped=False,
                                   image=None)
    out = tmp_path / "saved.tif"
    gui.ScannerGui._deliver_one(types.SimpleNamespace(), result, out, 95,
                                False, "G")
    tifffile = pytest.importorskip("tifffile")
    with tifffile.TiffFile(str(out)) as handle:
        assert handle.pages[0].tags["XResolution"].value == (900, 1)
    assert tiff.read(str(out)).shape == (4, 6, 3)


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


@pytest.mark.parametrize("platform", ["here", "windows"])
def test_failing_to_write_the_note_never_costs_the_scan(tmp_path, monkeypatch,
                                                        platform):
    """approved.json records what was asked for. The scan is the work. A
    bookkeeping failure must not stop it -- which is exactly what happened.
    And it says which roll: Windows names the file in the way, not the
    folder that could not be made."""
    import types

    if platform == "windows":
        from conftest import windows_mkdir
        windows_mkdir(monkeypatch)

    said = []
    # A rolls folder that is there, and a file in it where the roll's folder
    # goes: the rolls folder itself a file would be refused by the session
    # too (`ScanSession._preflight`), and is said so (below).
    rolls = tmp_path / "rolls"
    rolls.mkdir()
    blocker = rolls / "blocker"
    stub = types.SimpleNamespace(_say=said.append,
                                 session=types.SimpleNamespace(rolls=rolls),
                                 _sheet_walk=lambda: None)

    class Approvedish:
        number = 1
        offset_mm = 0.5
        reference_entry = ""

    # Anything at all going wrong in here -- not just the Path that actually
    # did it -- has to be contained: here a folder that cannot be made.
    blocker.write_bytes(b"not a directory")
    gui.ScannerGui._write_approved(stub, (Approvedish(),), blocker / "a-roll")

    assert said and "scanning anyway" in said[0]
    assert "a-roll" in said[0]


def _approving(rolls, said=None):
    """Enough of the window for `_write_approved` to file into a roll under
    ``rolls``, the session's rolls folder."""
    return types.SimpleNamespace(
        _say=(said.append if said is not None else lambda *a: None),
        session=types.SimpleNamespace(rolls=rolls),
        # The sheet's walk, which a commission names (P22-b): none here.
        _sheet_walk=lambda: None)


@pytest.mark.parametrize("how", ["drive gone", "rolls a file"])
def test_a_rolls_folder_that_cannot_be_had_is_never_scanning_anyway(
        tmp_path, how):
    """Its drive pulled while the sheet was open: "scanning anyway", said
    a moment before the session refused the roll for the same reason
    (`missing_root` of the rolls folder, `ScanSession._preflight`)."""
    from rps7200 import locks, storage

    rolls = (tmp_path / "gone" / "rolls" if how == "drive gone"
             else tmp_path / "rolls")
    if how == "rolls a file":
        rolls.write_text("not a folder", encoding="utf-8")
    folder = rolls / "a-roll"
    said: list[str] = []
    gui.ScannerGui._write_approved(_approving(rolls, said),
                                   (Approved(1, rotation=90),), folder)
    [line] = said
    assert "scanning anyway" not in line
    assert line.startswith(f"approved.json not written in {folder} (")
    assert line.endswith("the roll will be refused until the rolls folder "
                         "is back")
    assert storage.missing_root(rolls) is not None, "and the session refuses"
    assert not (tmp_path / "gone").exists(), "nothing was made"
    assert locks.holders(locks.ROLL, folder) == []


def test_a_sheet_opened_past_its_rolls_folders_link_files_its_decisions(
        tmp_path):
    """The rolls folder a link or a junction, and the sheet opened by the
    folder it points to: `_roll_folder` calls that sheet inside its rolls,
    links followed, and `_write_approved` refused it as outside them, links
    not followed. The roll ran, and approved.json -- where Export reads the
    frames' turns -- was never written."""
    from conftest import folder_link

    real = tmp_path / "data" / "rolls"
    sheet = real / "a-roll"
    sheet.mkdir(parents=True)
    rolls = tmp_path / "rolls"
    folder_link(rolls, real)
    said: list = []
    stub = _approving(rolls, said)
    stub._sheet_roll = sheet
    folder = gui.ScannerGui._roll_folder(stub)
    assert folder == sheet, "not taken for the sheet's own folder"
    gui.ScannerGui._write_approved(stub, (Approved(1, rotation=90),), folder)
    assert not any("scanning anyway" in line for line in said), said
    assert gui.read_approved(sheet)[1] == {1: 90}


def test_a_commission_adds_to_the_decisions_already_filed(tmp_path):
    """Each commission replaced approved.json with only the frames ticked
    that time, so finishing a roll's last three frames erased the turns of
    the first fifteen -- which Export then used."""
    stub, folder = _approving(tmp_path), tmp_path / "a-roll"
    gui.ScannerGui._write_approved(stub, (Approved(1, rotation=90),
                                          Approved(2, rotation=180)), folder)
    gui.ScannerGui._write_approved(stub, (Approved(2, rotation=0),
                                          Approved(3, rotation=270)), folder)
    _off, rotations, _flips, _entries, _src = gui.read_approved(
        tmp_path / "a-roll")
    assert rotations == {1: 90, 2: 0, 3: 270}
    kept = json.loads((tmp_path / "a-roll" / "approved.json.bak").read_text(
        encoding="utf-8"))
    assert [f["number"] for f in kept["frames"]] == [1, 2]


def test_approved_json_that_cannot_be_read_is_kept_and_said(tmp_path):
    """A damaged file read back as "no decisions" with nothing said, and the
    next commission wrote over it."""
    folder = tmp_path / "a-roll"
    folder.mkdir()
    (folder / "approved.json").write_text('{"frames": [{"num',
                                          encoding="utf-8")
    said = []
    assert gui.read_approved(folder, say=said.append)[1] == {}
    assert said and "approved.json" in said[0]
    gui.ScannerGui._write_approved(_approving(tmp_path, said),
                                   (Approved(4, rotation=90),), folder)
    assert (folder / "approved.json.unreadable").read_text(
        encoding="utf-8") == '{"frames": [{"num'
    assert gui.read_approved(folder)[1] == {4: 90}
    assert any("approved.json.unreadable" in line for line in said)


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
    # The turn and the mirror in one change (`ScanSession.arrange`).
    assert "self.gui.session.arrange(last.rotation, last.flipped)" in every
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
                 "self.session.arrange(result.rotation, result.flipped)"):
        assert line in carry, line


def test_no_window_sets_the_sessions_turn_and_mirror_one_at_a_time():
    """Set one after the other while a roll filed a frame, the frame got the
    new turn and the old mirror: every place the window arranges what is
    written next goes through `ScanSession.arrange`, both in one change."""
    source = inspect.getsource(gui)
    assert "session.rotation =" not in source
    assert "session.flip =" not in source


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


def _window_actions(**over):
    """The main window's dispatch table, without building a window, and the
    stand-in it runs against -- ``over`` replacing any of its parts."""
    import types
    stub = types.SimpleNamespace(**{**dict(
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
        session=types.SimpleNamespace(outstanding=lambda: ()),
    ), **over})
    return stub, gui.ScannerGui._actions(stub)


class _Held:
    """The session's count of the jobs it holds (`ScanSession.outstanding`),
    for a window whose `submit` a test has taken over: a job handed over is
    held until the test says the worker took it and ended it (`taken`), or
    a stop drops it -- what the window asks before every hand-over."""

    def __init__(self, app, monkeypatch):
        self.app = app
        self.jobs: list = []
        self.held: list = []
        monkeypatch.setattr(app.session, "submit", self.submit)
        monkeypatch.setattr(app.session, "outstanding",
                            lambda: tuple(self.held))
        monkeypatch.setattr(app.session, "request_stop", self.held.clear)

    def submit(self, job) -> None:
        self.jobs.append(job)
        self.held.append(job)

    def taken(self, text, *between) -> None:
        """The oldest job started, said what it said, and ended -- let go
        of before "idle", as the worker does."""
        from rps7200.session import Event
        self.app._handle(Event(kind="state", text=text, busy=True))
        for event in between:
            self.app._handle(event)
        if self.held:
            self.held.pop(0)
        self.app._handle(Event(kind="state", text="idle", busy=False))


def _every_key_pressed(app, root, monkeypatch, answer):
    """Run every action a key can reach in the main window, answering every
    question ``answer``. Returns the jobs handed to the scanner, the questions
    asked, and any `NEVER_BOUND` method reached."""
    asked, reached = [], []
    held = _Held(app, monkeypatch)
    jobs = held.jobs
    for name in shortcuts.NEVER_BOUND:
        monkeypatch.setattr(app, name,
                            lambda *a, _name=name, **k: reached.append(_name))

    def ask(title, *a, **k):
        asked.append(title)
        return answer

    for name in ("askokcancel", "askyesno", "askyesnocancel"):
        monkeypatch.setattr(gui.messagebox, name, ask)
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: None)
    for name in ("asksaveasfilename", "askdirectory", "askopenfilename"):
        monkeypatch.setattr(gui.filedialog, name, lambda *a, **k: "")
    for action_id, run in app._actions().items():
        run()
        root.update()
        # As if the worker had taken it and finished: nothing does here, and
        # a job still waiting refuses the next key as "the scanner is busy".
        held.held.clear()
    return jobs, asked, reached


def test_every_action_in_the_table_has_something_to_do(window, tmp_path):
    """An id in `shortcuts.ACTIONS` with no handler is a key that silently
    does nothing, and the editor would still offer it."""
    app, root = window
    sheet = _read_sheet(app, _walked(tmp_path, count=2))
    sheet.adjust(0)
    root.update()
    handled = {"window": set(app._actions()), "sheet": set(sheet._actions()),
               "adjuster": set(sheet._adjuster._actions())}
    for action in shortcuts.ACTIONS:
        assert action.id in handled[action.scope], action.id
    sheet.top.destroy()


def test_no_shortcut_moves_film_or_calibrates(window, tmp_path, monkeypatch):
    """The rule this table is written under. There is no undo for a moved
    negative or a wedged device, so no key reaches those on any terms --
    pressed, every one of them, in all three windows, with every question
    answered yes."""
    from rps7200.session import Calibrate, Move

    app, root = window
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(_walked_folder(tmp_path, count=2))
    sheet = app.sheet
    sheet.adjust(0)
    root.update()
    adjuster = sheet._adjuster
    jobs, _asked, reached = _every_key_pressed(app, root, monkeypatch, True)
    for scoped in (adjuster, sheet):
        for _action_id, run in scoped._actions().items():
            if scoped.alive():
                run()
                root.update()
    assert reached == []
    assert not [j for j in jobs if isinstance(j, (Move, Calibrate))], jobs


def test_every_key_that_starts_a_pass_asks_first(window, monkeypatch):
    """The premise the old, stronger rule rested on was that `on_prescan` and
    `on_scan` "submit their job immediately, with no confirmation". They still
    do -- that is right for a button, where reaching for it is the decision --
    so the *keys* go through `_confirm_then` instead. `on_roll` is the exception
    because it asks its own question already, and asking twice would train the
    habit of dismissing both.

    This is the test that keeps the relaxation honest: every key pressed, and
    with every question answered no, nothing reaches the scanner. Answered
    yes, the three that start a pass each asked once."""
    from rps7200.session import Prescan, Roll, Scan

    app, root = window
    app.calibrated = True
    jobs, asked, _reached = _every_key_pressed(app, root, monkeypatch, False)
    assert jobs == [], "a key reached the scanner with every question refused"
    jobs, asked, _reached = _every_key_pressed(app, root, monkeypatch, True)
    started = [type(j) for j in jobs]
    assert sorted(t.__name__ for t in started) == ["Prescan", "Roll", "Scan"]
    assert {Prescan, Scan, Roll} == set(started)
    assert len(asked) == 3, asked


def test_confirming_a_key_actually_gates_it():
    """`_confirm_then` must not run the action when the answer is no, and must
    not reach the scanner at all while one is already running -- or handed
    over and not yet begun, which `busy` says a tick late."""
    import types

    from rps7200.session import Scan
    ran, held = [], []
    stub = types.SimpleNamespace(
        busy=False, root=None,
        session=types.SimpleNamespace(outstanding=lambda: tuple(held)),
        _say=lambda *a: None, _calibration_missing=lambda parent=None: False,
    )
    stub._working = lambda: gui.ScannerGui._working(stub)
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
        stub.busy = False
        held.append(object.__new__(Scan))
        gui.ScannerGui._confirm_then(stub, "a scan", lambda: "cost",
                                     lambda: ran.append("queued"))
        assert ran == ["went"], "it asked with a pass handed over"
    finally:
        gui.messagebox.askokcancel = real


def test_stop_is_the_one_exception_and_only_while_something_runs():
    """`request_stop` is cooperative and always safe -- but the log is
    evidence, and "finishing what is already running" with nothing running is
    a line that will be read back one day and believed."""
    stops = []
    stub, table = _window_actions(on_stop=lambda: stops.append(1))
    table["stop"]()
    assert stops == [], "nothing running, nothing said"
    stub.busy = True
    table["stop"]()
    assert stops == [1]


def test_escape_stops_a_job_handed_over_this_very_tick():
    """The key asked `busy` alone, which follows the worker's report of the
    start, read up to a tick after the hand-over: an Escape pressed in
    between was dropped. The session holds the job from its hand-over
    (`ScanSession.outstanding`), and the key asks it."""
    import types

    from rps7200.session import Scan

    stops = []
    stub, table = _window_actions(
        on_stop=lambda: stops.append(1),
        session=types.SimpleNamespace(outstanding=lambda: (Scan(),)))
    assert not stub.busy
    table["stop"]()
    assert stops == [1]


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
    # Every scope binds through `_bind_key`, which hands the handler the key
    # it actually bound -- the one set, or the default it fell back to.
    for source in (inspect.getsource(gui.ScannerGui._bind_shortcuts),
                   inspect.getsource(gui._ContactSheet.rebind),
                   inspect.getsource(gui._FrameAdjuster.rebind)):
        assert "_bind_key(" in source
    assert "self._runner(run, candidate)" in inspect.getsource(
        gui.ScannerGui._bind_key)


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
    assert ".bind_all(" not in inspect.getsource(gui.ScannerGui._bind_key)
    assert "_bind_key(self.root," in inspect.getsource(
        gui.ScannerGui._bind_shortcuts)
    assert "_bind_key(self.top," in inspect.getsource(gui._ContactSheet.rebind)
    assert "_bind_key(self.top," in inspect.getsource(gui._FrameAdjuster.rebind)
    assert "widget.bind(candidate," in inspect.getsource(
        gui.ScannerGui._bind_key)


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


def test_the_file_is_arranged_like_the_pass_that_was_on_screen(window,
                                                               monkeypatch):
    """The session carried the last arrangement set anywhere, which drifts.
    The pass on screen is the one being scanned, so it is the one that
    decides."""
    app, _root = window
    app.calibrated = True
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.session.rotation, app.session.flip = 0, False
    app.current = types.SimpleNamespace(rotation=270, flipped=True)
    app.on_scan()
    assert jobs, "the scan was handed over"
    assert (app.session.rotation, app.session.flip) == (270, True)


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
    # what the browser's row says of it
    assert gui.roll_cells(summary)[1] == "2 of 4"
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


def test_a_channel_the_chooser_does_not_offer_is_not_put_back():
    """A manifest is hand-editable, and whatever it said for `mono_channel`
    went into the chooser and on to every roll after it: "I" delivered the
    dust plane as the photograph once, and is refused by `to_monochrome`
    now -- for every frame of the roll."""
    assert gui.restorable({"mono_channel": "G"})["mono_channel"] == "G"
    for wrong in ("I", "green", 3):
        assert "mono_channel" not in gui.restorable({"mono_channel": wrong})


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


def test_a_reduced_preview_is_named_as_one(tmp_path):
    """Save all right after a roll writes the passes not yet filed from the
    1400-pixel copy on screen, and named them with the scan's full dpi --
    so the copy later passed for the delivery."""
    unfiled = types.SimpleNamespace(
        kind="frame", number=38, seq=40, entry=None,
        meta={"resolution_dpi": 3600, "channels": 3})
    assert gui.batch_name(unfiled, "tiff") == "frame38_3600dpi_preview.tif"
    filed = tmp_path / "entry"
    filed.mkdir()
    (filed / "scan.tif").write_bytes(b"")
    unfiled.entry = filed
    assert gui.batch_name(unfiled, "tiff") == "frame38_3600dpi.tif"


def test_a_save_as_that_fails_says_so_in_the_window(monkeypatch):
    """`best.png`, a full disk, an entry that will not read: each went to
    Tk's default handler, a traceback on a terminal nobody watches, and the
    window said nothing at all."""
    said, shown = [], []
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **kw: "/somewhere/best.png")
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **kw: shown.append(a))

    def refuse(*a):
        raise ValueError("cannot tell what format 'best.png' should be")

    import queue

    # On a writer thread, whose word reaches the window through `_saves`
    # (`_saved`): run here at once, and the queue then drained as the pump
    # drains it.
    stub = types.SimpleNamespace(
        session=types.SimpleNamespace(out_format="tiff"), root=None,
        v_jpegq=types.SimpleNamespace(get=lambda: 95),
        _mono_for=lambda result: (False, "G"),
        _start_writing=lambda run, name: run(), _saves=queue.Queue(),
        _deliver_one=refuse, _say=said.append)
    result = types.SimpleNamespace(label="scan 1")
    gui.ScannerGui.on_save_as(stub, result)
    while not stub._saves.empty():
        gui.ScannerGui._saved(stub, stub._saves.get())
    assert shown and "best.png" in shown[0][1]
    assert any("could not save best.png" in line for line in said), said


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
    assert sorted(index["names"]["strip"]) == [1, 2]
    assert sorted(index["names"]["other"]) == [1]
    assert "" not in index["names"]


def _member(tmp_path, roll, number, kind, name):
    entry = tmp_path / "library" / name
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text(json.dumps({
        "film": {"frame": f"{roll}-{number:02d}"},
        "tags": ["roll", kind] if kind == "prescan" else ["roll"],
        "extra": {"roll_membership": {"roll": roll, "number": number,
                                      "kind": kind}},
    }), encoding="utf-8")
    return entry


def test_a_walks_prescan_is_never_joined_as_the_frame(tmp_path):
    """A walk's prescan and the frame scanned there share a roll and a number.
    Joined on those alone, Export delivered the 300 dpi prescan as the frame
    at full resolution whenever it came last in glob order."""
    frame = _member(tmp_path, "strip", 3, "frame", "a-frame")
    _member(tmp_path, "strip", 3, "prescan", "z-prescan")
    index = gui.roll_entry_index(tmp_path / "library")
    assert index["names"]["strip"][3] == frame


def test_entries_filed_by_the_roll_tool_are_found_too(tmp_path):
    """`tools/scan_roll.py` labelled frames `<roll>/<NN>`, which the window
    could not parse, so Export found nothing of a roll scanned from there."""
    entry = tmp_path / "library" / "cli"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text(json.dumps(
        {"film": {"frame": "cli-roll/04"}}), encoding="utf-8")
    assert gui.roll_entry_index(tmp_path / "library")["names"]["cli-roll"][4] == entry


def test_an_old_walk_prescan_without_a_membership_is_left_out(tmp_path):
    old = tmp_path / "library" / "old-prescan"
    old.mkdir(parents=True)
    (old / "scan.json").write_text(json.dumps(
        {"film": {"frame": "strip-02"}, "tags": ["gui", "roll", "prescan"]}),
        encoding="utf-8")
    index = gui.roll_entry_index(tmp_path / "library")
    assert index["names"] == {} and index["folders"] == {}


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


def test_an_export_is_arranged_the_way_each_frame_file_was_written():
    """A turn made in the window while a roll ran reached the frames written
    after it; the roll's one `rotation` is the one it began with. The frame's
    own record says what its file got, and wins."""
    summary = {"folder": "/nowhere", "settings": {"rotation": 0},
               "entries": {1: "e1", 2: "e2", 3: "e3"},
               "arranged": {2: (90, True)}}
    plan = {item.number: item for item in gui.roll_exports(summary)}
    assert (plan[1].rotation, plan[1].flipped) == (0, False)
    assert (plan[2].rotation, plan[2].flipped) == (90, True)


def test_a_roll_summary_says_how_each_frame_file_was_arranged(tmp_path):
    folder = tmp_path / "roll"
    folder.mkdir()
    (folder / "roll.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": 1, "done": True},
            {"number": 2, "done": True, "rotation": 450, "flipped": True}]}),
        encoding="utf-8")
    assert gui.roll_summary(folder)["arranged"] == {2: (90, True)}


def test_turning_a_walked_prescan_does_not_walk_it_twice():
    """A turn or a flip calls `remember_arrangement`, which added a walked
    prescan to the survey: turning one while the walk ran put it on the sheet
    twice, and turning an older walk's put it into this walk."""
    import types

    added = []
    stub = types.SimpleNamespace(
        rotation=0, flip=False, orientations={}, results=[], survey=[],
        _surveying=True, _transport=None, _kept_walk=set(), _rewalked=set(),
        _same_strip_from=None,
        edge_watch=types.SimpleNamespace(add=lambda n, im: added.append(n)),
        _show=lambda _r: None, _redraw_strip=lambda: None)
    stub._arrange = lambda r, s=None: gui.ScannerGui._arrange(stub, r, s)
    stub._into_survey = lambda r: gui.ScannerGui._into_survey(stub, r)
    stub.remember_arrangement = lambda r: gui.ScannerGui.remember_arrangement(
        stub, r)

    def prescan(number, seq):
        return types.SimpleNamespace(kind="prescan", number=number, seq=seq,
                                     image=None, position=number - 1, meta={})

    older = prescan(7, 1)          # from a walk before this one, not in it
    first = prescan(1, 2)
    gui.ScannerGui._add_result(stub, first)
    first.rotation = 90
    stub.remember_arrangement(first)
    older.rotation, older.flipped = 180, False
    stub.remember_arrangement(older)
    assert stub.survey == [first] and added == [1]


def test_a_frame_with_no_library_entry_is_left_out_of_an_export(tmp_path):
    """Export re-corrects from the entries, so a frame without one cannot be
    exported at all. Left out here; the window counts the difference against
    `done` and says how many were skipped."""
    _shelf(tmp_path, done=(1, 2))
    _entry(tmp_path, "2026-09-14", 1)
    summary = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")[0]
    assert [item.number for item in gui.roll_exports(summary)] == [1]
    assert len(summary["done"]) == 2, "both were scanned; only one survives"


def _filed(tmp_path, roll, number, name, folder, *, dpi=1800, channels=3):
    """A frame's entry as the session files one: it records its folder."""
    entry = tmp_path / "library" / name
    entry.mkdir(parents=True)
    # Its pixels are filed: a result whose entry holds no scan.tif is
    # delivered from the reduced copy, and named `_preview` for it.
    (entry / "scan.tif").write_bytes(b"")
    (entry / "scan.json").write_text(json.dumps({
        "scan": {"resolution_dpi": dpi, "channels": channels},
        "film": {"frame": f"{roll}-{number:02d}"},
        "extra": {"roll_membership": {"roll": roll, "number": number,
                                      "kind": "frame", "folder": str(folder)}},
    }), encoding="utf-8")
    return entry


def _names_its_entries(folder, entries):
    """roll.json naming the entry filed for each frame, as `RollManifest` does."""
    progress = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    for record in progress["frames"]:
        if record["number"] in entries:
            record.update(done=True, entry=str(entries[record["number"]]))
    (folder / "roll.json").write_text(json.dumps(progress), encoding="utf-8")


def test_a_duplicate_and_its_original_each_export_their_own_frames(tmp_path):
    """A duplicate keeps its original's roll name, and Export joined entries
    to rolls on that name alone: after the original was rescanned -- what the
    Duplicate dialog says the copy is for -- both exported the rescans, and
    the scans the copy was made to keep were delivered from neither."""
    import shutil

    original = _shelf(tmp_path, name="R", done=(1, 2))
    first = {n: _filed(tmp_path, "R", n, f"2026a-{n}", original) for n in (1, 2)}
    _names_its_entries(original, first)
    shutil.copytree(original, original.with_name("R-2"))     # Duplicate
    again = {n: _filed(tmp_path, "R", n, f"2026b-{n}", original, dpi=3600)
             for n in (1, 2)}
    _names_its_entries(original, again)

    listed = {s["folder"].name: s for s in
              gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")}
    assert listed["R-2"]["entries"] == first
    assert listed["R"]["entries"] == again
    assert [i.entry for i in gui.roll_exports(listed["R-2"])] == [first[1], first[2]]
    # A frame whose own entry has gone has none: not the older take the
    # folder also holds, which a name typed again after a Delete would make
    # another strip's.
    shutil.rmtree(again[2])
    listed = {s["folder"].name: s for s in
              gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")}
    assert listed["R"]["entries"] == {1: again[1]}


def test_an_entry_recorded_on_windows_is_found_elsewhere(tmp_path):
    """roll.json records `str(entry)`, which on Windows has backslashes, and
    a POSIX path reads the whole string as one name. A library and its rolls
    on a shared disk, scanned on Windows and opened on a Mac, then called
    every frame's entry deleted and exported nothing."""
    folder = _shelf(tmp_path, name="R", done=(1, 2))
    entries = {n: _filed(tmp_path, "R", n, f"2026a-{n}", folder) for n in (1, 2)}
    _names_its_entries(folder, {1: rf"library\{entries[1].name}",
                                2: rf"D:\scans\library\{entries[2].name}"})
    listed = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")
    assert listed[0]["entries"] == entries


def test_a_name_typed_again_after_a_delete_is_a_new_roll(tmp_path):
    """The folder comes back under the same name and the same path, so
    neither the name nor the folder tells the two strips apart. Frames 7-12 of
    the deleted roll are not frames of a new one that scanned 1-6."""
    import shutil

    old = _shelf(tmp_path, name="Portra", wanted=range(1, 13), done=range(1, 13))
    _names_its_entries(old, {n: _filed(tmp_path, "Portra", n, f"2026a-{n:02d}", old)
                             for n in range(1, 13)})
    shutil.rmtree(old)                                       # Delete
    new = _shelf(tmp_path, name="Portra", wanted=range(1, 7), done=range(1, 7))
    ours = {n: _filed(tmp_path, "Portra", n, f"2026b-{n:02d}", new)
            for n in range(1, 7)}
    _names_its_entries(new, ours)
    summary = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")[0]
    assert summary["entries"] == ours


def test_an_exported_frame_is_named_by_its_own_entry(tmp_path):
    """The roll's settings are its last run's: twelve frames at 1800 dpi RGB
    resumed at 3600 dpi RGBI said "3600dpi_ir" of all of them. And a roll the
    command-line tool made records `dpi`, which read as `resolution` alone was
    "0dpi"."""
    folder = _shelf(tmp_path, name="R", done=(1, 2), dpi=3600, ir=True)
    entries = {1: _filed(tmp_path, "R", 1, "2026a-1", folder, dpi=1800),
               2: _filed(tmp_path, "R", 2, "2026a-2", folder, dpi=3600,
                         channels=4)}
    _names_its_entries(folder, entries)
    summary = gui.rolls_on_disk(tmp_path / "rolls", tmp_path / "library")[0]
    named = [gui.batch_name(i, "tiff") for i in gui.roll_exports(summary)]
    assert named == ["frame01_c41_1800dpi.tif", "frame02_c41_3600dpi_ir.tif"]

    cli = {"folder": tmp_path / "nowhere", "entries": {4: tmp_path / "gone"},
           "settings": {"dpi": 1800}}
    assert gui.roll_exports(cli)[0].meta["resolution_dpi"] == 1800


def test_an_export_is_one_channel_or_all_as_the_rolls_own_frames_were(tmp_path):
    """The roll's own film and its own choice, not the window's: a colour
    roll exported with the window on black and white came out as one grey
    plane, and a black and white roll with it on negative as three."""
    def plan(film, mono=None):
        summary = {"folder": tmp_path / "nowhere", "entries": {1: "e1"},
                   "film": film, "settings": {"film": film, "mono": mono,
                                              "mono_channel": "R"}}
        return gui.roll_exports(summary)[0]

    assert (plan("bw").mono, plan("bw").mono_channel) == (True, "R")
    assert plan("negative").mono is False
    assert plan("bw", mono=False).mono is False, "his choice for that roll"
    assert plan(None).mono is None, "a roll that does not say: the window's"


def test_a_saved_pass_is_one_channel_or_all_as_its_own_film_asks(window,
                                                                 monkeypatch):
    app, root = window
    app.v_film.set("negative")
    app._sync_film()
    bw = types.SimpleNamespace(meta={"film": "bw"}, label="scan 1")
    colour = types.SimpleNamespace(meta={"film": "negative"}, label="scan 2")
    assert app._mono_for(bw)[0] is True
    assert app._mono_for(colour)[0] is False
    item = types.SimpleNamespace(meta={}, mono=False, mono_channel="G")
    app.v_film.set("bw")
    app._sync_film()
    assert app._mono_for(item) == (False, "G"), "a roll's own answer wins"
    assert app._mono_for(colour)[0] is False
    # And Save As asks it rather than the window.
    delivered = []
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **k: "/nowhere/out.tif")
    monkeypatch.setattr(app, "_deliver_one",
                        lambda r, p, q, mono, ch: delivered.append(mono) or "")
    app.on_save_as(colour)
    for thread in app._writing:
        thread.join(5)
    assert delivered == [False]


def test_a_delivered_file_says_the_resolution_it_was_scanned_at(window,
                                                                monkeypatch,
                                                                tmp_path):
    """Save As, Save all and Export wrote no resolution, so a 3600 dpi frame
    said 72 dpi or nothing -- while the output folder's copy of the same pass
    said 3600."""
    app, root = window
    entry = tmp_path / "entry"
    entry.mkdir()
    (entry / "scan.tif").write_bytes(b"raw")
    monkeypatch.setattr(gui.library, "corrected", lambda e: (
        np.zeros((4, 6, 3), np.uint16),
        {"scan": {"resolution_dpi": 3600}, "corrected": "applied"}))
    written = []
    monkeypatch.setattr(gui.export, "write", lambda path, image, **kw:
                        written.append(kw.get("resolution")) or "")
    result = types.SimpleNamespace(entry=entry, rotation=0, flipped=False,
                                   meta={}, image=None)
    app._deliver_one(result, tmp_path / "out.tif", 95, False, "G")
    assert written == [3600]


def test_save_as_says_when_it_could_not_write(window, monkeypatch):
    """It ran on the UI thread with nothing around it: a full or pulled disk
    raised into Tk's stderr handler, nothing was said, and the operator took
    it as saved. It runs where Save all does now, and says so as Save all
    does."""
    import threading

    app, root = window
    where = []

    def full(*_a):
        where.append(threading.current_thread())
        raise OSError(28, "No space left on device")

    shown = []
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **k: "/media/stick/out.tif")
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **kw: shown.append(a))
    monkeypatch.setattr(app, "_deliver_one", full)
    app.on_save_as(types.SimpleNamespace(meta={}, label="scan 1"))
    for thread in app._writing:
        thread.join(5)
    app._drain()
    assert where and where[0] is not threading.main_thread()
    assert shown and "out.tif" in shown[0][1]
    assert "could not save out.tif" in app.log.get("1.0", "end")
    assert "No space left" in app.log.get("1.0", "end")


def test_a_delivered_name_is_free_of_the_files_it_may_bring(tmp_path):
    """"Nothing already there is overwritten" checked only the name asked
    for: a JPEG's infrared DNG beside it, or the TIFF it becomes without
    Pillow, replaced whatever had that stem."""
    wanted = tmp_path / "frame01_1800dpi.jpg"
    assert gui.unclaimed_delivery(wanted) == wanted
    (tmp_path / "frame01_1800dpi.dng").write_bytes(b"an earlier infrared")
    assert gui.unclaimed_delivery(wanted).name == "frame01_1800dpi-2.jpg"
    (tmp_path / "frame01_1800dpi-2.tif").write_bytes(b"an earlier tiff")
    assert gui.unclaimed_delivery(wanted).name == "frame01_1800dpi-3.jpg"
    tif = tmp_path / "frame02_1800dpi.tif"
    assert gui.unclaimed_delivery(tif) == tif


@pytest.mark.parametrize("rotation,flipped,mono", [
    (0, False, False), (90, True, False), (270, False, True)])
def test_save_as_save_all_and_export_deliver_the_entry_corrected(
        tmp_path, monkeypatch, rotation, flipped, mono):
    """`_deliver_one` is the single function behind Save As, Save all and
    Export, and nothing called it: every delivered-file test used a stand-in
    whose raw and corrected pixels were the same array, so a delivery of the
    raw pixels would have passed them all. The entry here is a real pass,
    filed raw with a reference that visibly changes it; what is written must
    be that entry corrected, turned as the pass was, and never the raw."""
    from types import SimpleNamespace

    from conftest import scanner_at_commands
    from rps7200 import library, preview, tiff
    from rps7200.mono import to_monochrome

    scanner, _ = scanner_at_commands(monkeypatch)
    scanner.calibrate_shading()
    scanner.scan(resolution=300, infrared=False, keep_raw=True)
    entry = library.save(scanner.last_pixels_raw, scanner.last_scan_meta,
                         root=tmp_path / "library", **scanner.capture_record())
    result = SimpleNamespace(entry=entry, image=None, rotation=rotation,
                             flipped=flipped)
    out = tmp_path / "delivered.tif"

    # No widget is touched, which is why it may run off the UI thread; so no
    # window is needed to call it either.
    said = gui.ScannerGui._deliver_one(None, result, str(out), 95, mono, "G")
    assert "full resolution" in said

    def arranged(pixels):
        turned = preview.orient(pixels, rotation, flipped)
        return to_monochrome(turned, "G") if mono else turned

    written = tiff.read(str(out))
    assert np.array_equal(written, arranged(library.corrected(entry)[0]))
    assert not np.array_equal(written, arranged(library.load(entry)[0])), \
        "the raw pixels were delivered"


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


def test_the_settings_payload_carries_every_section(window, tmp_path):
    """The regression this guards actually shipped: `_remember` built its payload
    without the `rolls` key, and because the file is written whole that did not
    merely fail to save the rolls table's "Last opened" -- it erased it on every
    save. A section in `settings.SECTIONS` that the payload omits is silently
    destroyed, so the check is against that list rather than a hand-kept one.
    Read off the file the window writes, not off its source."""
    from rps7200 import settings as settings_module

    app, _root = window
    app._note_roll_opened(tmp_path / "strip")        # the section that was lost
    app._remember()
    written = json.loads((tmp_path / "gui-settings.json").read_text(
        encoding="utf-8"))
    for section in settings_module.SECTIONS:
        assert section in written, section
    assert "strip" in written["rolls"]


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


def test_a_walked_roll_reopened_comes_back_with_its_positions(
        window, tmp_path, monkeypatch):
    """Closing the window must not throw the walk's proposals away.

    `open_roll` restored `approved.json` -- positions already *committed* --
    and never re-proposed. A roll walked and then closed without commissioning
    has no `approved.json`, so it reopened with nothing at all: every proposal
    died with the window, and the roll then scanned uncorrected with no sign
    anything was missing. That cost a real 23-minute roll on 2026-09-22.
    """
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(_walked_folder(tmp_path))
    _settle(app, root)
    reopened, notes = dict(app.sheet.offsets), app.sheet.proposals
    app.sheet.top.destroy()
    assert reopened, "the fixture has to propose something for this to mean anything"
    assert all((notes[n] or {}).get("source") in gui.MACHINE_SOURCES
               for n in reopened)


def test_a_committed_position_still_wins_on_reopen(window, tmp_path):
    """His number is the authority and re-proposing must not overwrite it."""
    app, _root = window
    reopened, notes, _edges = _sheet_reads(app, _walked(tmp_path),
                                           {2: 0.5116}, {2: "operator"})
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
    assert "4 frames carry a position" in note
    assert "1 by you" in note
    assert "2 where two detectors agreed" in note
    assert "1 read from the frames either side" in note
    assert "by hand" not in note
    # One frame is one frame: it read "1 frame carry a position: 1 two
    # detectors agreed".
    one = _Confirming()._approved_note([Approved(3, 0.5, source="measured")])
    assert "1 frame carries a position: 1 where two detectors agreed." in one


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


def test_every_way_out_of_the_sheet_keeps_what_was_decided(window, tmp_path,
                                                          monkeypatch):
    """The Close button, the title bar's X, commissioning the scan and Escape
    all destroy the window. Each has to go through `_dismiss` first, or the
    decisions are kept for one way out and silently dropped for another --
    which is the shape of the original bug.

    Escape was the fourth way out and this test did not know about it. It was
    bound straight to `top.destroy`, so a sheet left by the key everyone
    reaches for lost every tick, drag and turn without a word.
    """
    app, root = window
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    kept = []
    monkeypatch.setattr(app, "_store_sheet_state", kept.append)
    folder = _walked_folder(tmp_path, count=2)

    def close_button(sheet):
        button, = [w for w in gui._descendants(sheet.top)
                   if w.winfo_class() == "TButton" and w.cget("text") == "Close"]
        button.invoke()

    ways = {
        "Close": close_button,
        "the title bar's X": lambda sheet: sheet.top.tk.call(
            sheet.top.protocol("WM_DELETE_WINDOW")),
        "Escape": lambda sheet: sheet._actions()["sheet_close"](),
        "commissioning": lambda sheet: sheet._scan(),
    }
    for way, leave in ways.items():
        app.open_roll(folder)
        sheet = app.sheet
        sheet._one(2, 90)
        kept.clear()
        leave(sheet)
        root.update()
        assert not sheet.alive(), way
        assert kept and kept[-1]["rotations"].get(2) == 90, way


def test_reopening_a_roll_keeps_who_decided_each_position(window, tmp_path,
                                                         monkeypatch):
    """`open_roll` replaces the sheet state wholesale, and it used to drop the
    sources while keeping the offsets.

    A kept offset with no recorded source is read as one he set by hand, so the
    next sheet built from that state relabelled every machine proposal
    `operator` -- and the confirm dialog then counted them as his, on the
    screen where he approves them.
    """
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)
    (folder / "approved.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": 2, "offset_mm": 0.5116, "source": "measured"},
            {"number": 3, "offset_mm": 0.2, "source": "operator"}]}),
        encoding="utf-8")
    app.open_roll(folder)
    assert app.sheet_state["sources"] == {2: "measured", 3: "operator"}
    _settle(app, root)
    assert app.sheet.proposals[2]["source"] in gui.MACHINE_SOURCES
    assert app.sheet.proposals[3]["source"] == "operator"
    app.sheet.top.destroy()


def test_a_fresh_walk_does_not_inherit_the_last_strips_decisions(window,
                                                                 monkeypatch):
    """Frame numbers on a new strip name different pictures. The window already
    clears `orientations` for this reason -- "a different film, shown and
    written sideways" -- and the sheet's own copy has to go at the same moment
    or the positions are applied to whatever lands on those numbers."""
    app, _root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.orientations = {("frame", 3): (90, False)}
    app.sheet_state = {"offsets": {3: 1.25}, "rotations": {3: 90}}
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_roll()
    assert jobs, "the walk was handed over"
    assert app.orientations == {} and app.sheet_state == {}


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


def test_the_sheets_options_reach_the_roll_rather_than_the_windows(
        window, monkeypatch, tmp_path):
    """"Sheet wins for the roll". The job has to be built from the resolved
    values; reading any of them back off the main window would mean setting
    3600 on the sheet and scanning at whatever the window still showed."""
    app, jobs = _commissioning(window, monkeypatch, tmp_path)
    app.on_scan_chosen((1, 2), options={
        "dpi": "3600", "predpi": "300", "ir": False, "fast_ir": False,
        "film": gui.FILM_BW, "meter": gui.METER_MODES[-1], "correct": True})
    roll, = jobs
    assert (roll.resolution, roll.prescan_resolution) == (3600, 300)
    assert (roll.infrared, roll.fast_infrared) == (False, False)
    assert roll.film == gui.FILM_BW and roll.meter == gui.METER_MODES[-1]
    assert roll.correct is True
    assert roll.mono is True, "black and white, derived from the sheet's film"


def test_an_absent_options_set_still_falls_back_to_the_window(
        window, monkeypatch, tmp_path):
    """`on_scan_chosen` is reachable without a sheet, and that path has to
    behave exactly as it did before the panel existed."""
    app, jobs = _commissioning(window, monkeypatch, tmp_path)
    app.on_scan_chosen((1, 2))
    roll, = jobs
    assert (roll.resolution, roll.prescan_resolution) == (1200, 300)
    assert (roll.infrared, roll.film, roll.meter) == (
        True, "negative", gui.METER_MODES[0])


def _commissioning(window, monkeypatch, tmp_path):
    """A window set one way, ready to commission a roll; its submitted jobs."""
    app, _root = window
    app.calibrated = True
    app._sheet_roll = tmp_path / "rolls" / "sheet"
    app.v_dpi.set("1200")
    app.v_predpi.set("300")
    app.v_ir.set(True)
    app.v_film.set("negative")
    app.v_meter.set(gui.METER_MODES[0])
    app.v_correct.set(False)
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    for name in ("showinfo", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, **k: None)
    return app, jobs


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
    """A walked strip of realistic negatives, each showing ``gap`` columns of
    base at its left edge -- off centre, so the detector proposes a move for
    every one. A flat grey band is not film base and no longer reads as one."""
    from conftest import negative_prescan
    return [_Walked(n, negative_prescan(float(gap), seed=n)) for n in range(1, count + 1)]


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


def _walked(tmp_path, count=8):
    """`_strip()`'s frames as a stored walk reads back: what the sheet shows."""
    return gui.read_survey(_walked_folder(tmp_path, count=count))["results"]


def _read_sheet(app, results, kept=None, remembered=None, film="negative"):
    """The window's sheet on ``results``, once the reader is done with them.

    The window's own path, not a function beside it: the walk handed to
    `EdgeWatch`, the sheet opened on what was kept (`_open_sheet`), and the
    reading taken in as the sheet takes it (`take_readings`).
    """
    app.survey = list(results)
    app.edge_watch.load([(int(r.number), r.image) for r in results], film)
    assert app.edge_watch.wait(120), "the frame-edge reader never finished"
    app._open_sheet(dict(kept or {}), remembered)
    progress = app.edge_watch.progress()
    app.sheet.take_readings(progress.offsets, progress.notes)
    return app.sheet


def _sheet_reads(app, results, kept=None, remembered=None, film="negative"):
    """`_read_sheet`'s offsets, per-frame notes and edge lines; closed after."""
    sheet = _read_sheet(app, results, kept, remembered, film)
    out = (dict(sheet.offsets),
           {n: dict(v) for n, v in sheet.proposals.items()}, dict(sheet.edges))
    sheet.top.destroy()
    return out


def test_a_walk_reopened_is_measured_again_not_remembered(window, tmp_path):
    """The whole point of the demo: the numbers come from the pixels.

    A walk closed without being commissioned has no `approved.json`, so
    `read_survey` hands back no offsets and every position on the sheet is one
    the ensemble has just read off the prescans.
    """
    folder = _walked_folder(tmp_path)
    out = gui.read_survey(folder)
    assert out["offsets"] == {}, "nothing was committed, so nothing is restored"
    app, _root = window
    proposed, notes, _edges = _sheet_reads(app, out["results"], out["offsets"],
                                           out.get("sources"))
    assert proposed
    assert all((notes[n] or {}).get("source") in gui.MACHINE_SOURCES
               for n in proposed)


def test_a_stale_remembered_sheet_cannot_reach_a_reopened_walk(
        window, tmp_path, monkeypatch):
    """The detector's positions are measured again on every launch.

    The sheet cache is real and it is a feature -- reopening the sheet gives
    back the frames that were dragged, and a walk not yet commissioned comes
    back as it was left. What it must not carry into a fresh launch is the
    machine's answer, or the demo would replay last time's and call it a
    measurement. `open_roll` is the launch path, and the one the demo takes.
    """
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path)
    first, _notes, _edges = _sheet_reads(
        app, gui.read_survey(folder)["results"])

    # a previous run's sheet, where the window keeps it and stamped for this
    # very walk: one position his, one the machine's, both deliberately wrong
    app.remembered["sheet"] = {folder.name: {
        "walk": gui.walk_stamp(folder),
        "offsets": {"1": 9.9, "2": -9.9},
        "sources": {"1": "operator", "2": "measured"},
        "ticks": {}, "rotations": {}, "flips": {}, "options": {}}}

    app.open_roll(folder)
    _settle(app, root)
    second = dict(app.sheet.offsets)
    app.sheet.top.destroy()
    assert second[1] == 9.9, "his comes back as he left it"
    assert second[2] == first[2], "the machine's is read again, not replayed"
    assert {n: v for n, v in second.items() if n != 1} == {
        n: v for n, v in first.items() if n != 1}


def test_the_same_walk_measures_the_same_way_twice(window, tmp_path):
    """"Re-measured every launch" is only legible if it is also "the same
    answer every launch". Nothing in the proposal path is random, and this is
    what says so."""
    app, _root = window
    walked = _walked(tmp_path)
    first, _n, _e = _sheet_reads(app, walked)
    second, _n, _e = _sheet_reads(app, walked)
    assert first == second


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
    # The folders below are the demo's own, as --demo requires of them.
    monkeypatch.setattr(gui, "DEMO_ROOT", tmp_path)
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


def test_look_only_without_the_demo_is_refused_before_anything_opens(
        monkeypatch, tmp_path, capsys):
    """Only the demo's stand-in can be told there is no film. Given alone,
    the flag changed the sheet's words and nothing else: it promised a
    refusal while the real scanner was sought, moved, calibrated with an
    empty transport and scanned. Refused as a usage error instead, before a
    session or a window exists."""
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(gui, "ScanSession",
                        lambda *a, **kw: pytest.fail("a session was built"))
    monkeypatch.setattr(gui, "ScannerGui",
                        lambda *a, **kw: pytest.fail("a window was built"))
    monkeypatch.setattr(sys, "argv", [
        "gui.py", "--library", str(tmp_path / "library"),
        "--rolls", str(tmp_path / "rolls"), "--look-only"])
    with pytest.raises(SystemExit) as refused:
        gui.main()
    assert refused.value.code == 2
    assert "--look-only needs --demo" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--library", "--rolls", "--reference",
                                  "--settings", "--out"])
def test_the_demo_is_refused_a_real_library_or_rolls_folder(
        monkeypatch, tmp_path, capsys, flag):
    """Accepted without a word, `--demo --library library` filed synthetic
    entries -- resampled pixels, bytes the demo encoded, a made-up infrared
    plane -- among the real scans under ordinary ids, and `--rolls rolls`
    let a roll from the sheet write back into the walk it was showing. A
    demo calibration leaves its cache where `--reference` points, so that
    is held to the demo's folder too. And so are `--settings`, which wrote
    the demo's geometry and sheet decisions over the real window's, and
    `--out`, synthetic pictures under the names real scans get (P18-c)."""
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(gui, "DEMO_ROOT", tmp_path / "demo")
    monkeypatch.setattr(gui, "ScanSession",
                        lambda *a, **kw: pytest.fail("a session was built"))
    monkeypatch.setattr(sys, "argv", ["gui.py", "--demo",
                                      flag, str(tmp_path / "real")])
    with pytest.raises(SystemExit) as refused:
        gui.main()
    assert refused.value.code == 2
    assert f"{flag} " in capsys.readouterr().err


def test_the_demo_takes_its_settings_and_output_under_demo(monkeypatch,
                                                          tmp_path):
    """Under the demo's own folder, both are let through as they were."""
    import types

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    built: dict = {}

    class Window:
        def __init__(self, root, session, **kwargs):
            built.update(kwargs, session=session)

    class Root:
        def mainloop(self):
            pass

    monkeypatch.setattr(gui, "tk", types.SimpleNamespace(Tk=Root))
    monkeypatch.setattr(gui, "ScannerGui", Window)
    settings_file = gui.DEMO_ROOT / "mine" / "gui-settings.json"
    out = gui.DEMO_ROOT / "out"
    monkeypatch.setattr(sys, "argv", ["gui.py", "--demo",
                                      "--settings", str(settings_file),
                                      "--out", str(out)])
    assert gui.main() == 0
    assert built["settings_path"] == str(settings_file)
    assert built["session"].out_dir == out
    # And its log is kept under the demo's folder, as the rest is.
    assert built["logs"] == gui.DEMO_ROOT / "logs"


def test_a_typed_out_on_a_drive_that_has_gone_is_refused_at_launch(
        monkeypatch, tmp_path, capsys):
    """Its own folder is made, and never one above it: a stick not plugged
    in was made again on C: by the first copy written."""
    real = gui.ScanSession
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(gui, "ScanSession",
                        lambda *a, **kw: pytest.fail("a session was built"))
    gone = tmp_path / "gone"
    monkeypatch.setattr(sys, "argv", ["gui.py", "--out",
                                      str(gone / "scans")])
    with pytest.raises(SystemExit) as refused:
        gui.main()
    assert refused.value.code == 2
    assert "is the drive mounted?" in capsys.readouterr().err
    assert not gone.exists()
    monkeypatch.setattr(gui, "ScanSession", real)
    made = _launch(monkeypatch, tmp_path, "--out", str(tmp_path / "scans"))
    assert made.out_dir == tmp_path / "scans" and made.out_dir.is_dir()


def test_the_demo_makes_its_own_folder(monkeypatch, tmp_path):
    """It is in no checkout until a demo has run, and its library, rolls
    and calibration are folders below it, which may each be made only where
    their parent is there."""
    demo = tmp_path / "demo"
    monkeypatch.setattr(gui, "DEMO_ROOT", demo)
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.setattr(sys, "argv", ["gui.py", "--demo"])
    assert gui.main() == 0
    assert demo.is_dir()
    assert built["session"].root == str(demo / "library")


def test_a_remembered_output_folder_that_has_gone_is_said_and_refused(
        tmp_path):
    """The remembered output folder is iCloud's: had iCloud taken it away,
    the next scan made it again on C: as a plain folder and filled it. It is
    said at launch and kept as set, and the session refuses every job that
    writes a copy -- through the failed-job path the demo runs too, never a
    greyed button -- until it is back, chosen again or cleared."""
    tk = pytest.importorskip("tkinter")

    from rps7200.demo import DemoScanner
    from rps7200.session import Calibrate, Scan, ScanSession

    try:
        root = tk.Tk()
    except tk.TclError as exc:                       # no display
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    gone = tmp_path / "iCloudDrive"
    stored = tmp_path / "gui-settings.json"
    stored.write_text(json.dumps({"output": str(gone)}), encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          verbose=False)
    session._open_scanner = lambda: DemoScanner(str(tmp_path / "pictures"),
                                                speed=1e9)
    app = gui.ScannerGui(root, session, demo=True,
                         settings_path=str(stored))
    try:
        said = app.log.get("1.0", "end")
        assert f"{gone} is not there -- is the drive mounted?" in said
        assert "refused until it is" in said
        assert session.out_dir == gone, "kept as it was set"
        assert "disabled" not in str(app.b_scan["state"])
        session.submit(Calibrate(mode="off", reference=str(
            tmp_path / "shading.npz")))
        session.submit(Scan(resolution=300, infrared=False))
        events = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not any(
                e.kind == "failed" for e in events):
            events.extend(session.poll())
            time.sleep(0.02)
        failed = [e for e in events if e.kind == "failed"]
        assert len(failed) == 1, "the calibration writes no copy"
        assert "not scanning" in failed[0].text
        assert "Choose the output folder again, or clear it" in failed[0].text
        app._handle(failed[0])
        assert "Choose the output folder again" in app.log.get("1.0", "end")
        assert not gone.exists(), "the output folder was made again"
    finally:
        app._alive = False
        session.shutdown()
        session.join(timeout=10)
        root.destroy()


def test_a_mistyped_demo_entry_is_refused_at_launch(monkeypatch, tmp_path,
                                                    capsys):
    """It was accepted, logged as 'demo mode: showing <name>', and every
    picture came from some other entry."""
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(gui, "ScanSession",
                        lambda *a, **kw: pytest.fail("a session was built"))
    monkeypatch.setattr(sys, "argv", ["gui.py", "--demo", "--demo-entry",
                                      str(tmp_path / "no-such-entry")])
    with pytest.raises(SystemExit) as refused:
        gui.main()
    assert refused.value.code == 2
    assert "--demo-entry" in capsys.readouterr().err


def test_no_film_is_told_to_the_backend(monkeypatch, tmp_path):
    """Which is the only place that could honestly know it."""
    import rps7200.demo

    opened = []
    monkeypatch.setattr(rps7200.demo, "DemoScanner",
                        lambda *args, **kwargs: opened.append(kwargs))
    _launch(monkeypatch, tmp_path, "--demo", "--look-only")._open_scanner()
    _launch(monkeypatch, tmp_path, "--demo")._open_scanner()
    assert [kw["no_film"] for kw in opened] == [True, False]


def test_the_demo_is_handed_every_library_beside_its_own(monkeypatch, tmp_path):
    """A roll after the first draws other photographs, from `library 2` as
    well as `library`; the likenesses are kept under the demo's own folder."""
    import rps7200.demo

    for name in ("library", "library 2"):
        entry_dir = tmp_path / name / "20260101T000000Z_x"
        entry_dir.mkdir(parents=True)
        (entry_dir / "scan.json").write_text("{}", encoding="utf-8")
    opened = []
    monkeypatch.setattr(rps7200.demo, "DemoScanner",
                        lambda *args, **kwargs: opened.append((args, kwargs)))
    _launch(monkeypatch, tmp_path, "--demo", "--demo-source",
            str(tmp_path / "library"))._open_scanner()
    (source,), kwargs = opened[0]
    assert kwargs["libraries"] == [tmp_path / "library", tmp_path / "library 2"]
    assert kwargs["cache"] == gui.DEMO_ROOT / "pictures.npz"


def test_an_empty_transport_refuses_where_the_transport_would():
    """Not a disabled button: a raised error, from the thing that would raise
    it, carrying a sentence a person can act on."""
    from rps7200.demo import DemoScanner
    from rps7200.usb_transport import UsbError

    empty = DemoScanner("library", no_film=True)
    for call in (lambda: empty.scan(resolution=300, infrared=False),
                 empty.prescan,
                 lambda: list(empty.scan_roll(frames=1, dry_run=True)),
                 empty.advance, empty.retreat, lambda: empty.nudge(0.5)):
        with pytest.raises(UsbError, match="no film in the transport"):
            call()

    # and with film the same methods work, or the demo would refuse its own
    # reason for existing
    loaded = DemoScanner("library")
    assert loaded.advance() is not None
    assert loaded.nudge(0.5)["param"] > 0


def test_every_proposal_is_somewhere_the_film_can_actually_go(window, tmp_path):
    """The caption showed the raw proposal; the commission delivered a snapped
    one. So a frame captioned as moving could be delivered as no move at all,
    and five other readers of `offsets` carried numbers that do not exist.
    Snapping at the seam makes every one of them agree."""
    app, _root = window
    offsets, _notes, _edges = _sheet_reads(app, _walked(tmp_path))
    assert offsets
    for value in offsets.values():
        assert value == gui.snap_offset(value)
        assert value != 0.0


def test_a_position_kept_from_a_machine_stays_a_machine_position(window,
                                                                 tmp_path):
    """Closing the sheet and opening it again used to relabel the whole strip.

    Every offset comes back as `kept`, and anything kept was stamped
    `operator` -- true when typing was the only way to have one, false from the
    moment the sheet began proposing them.
    """
    app, _root = window
    walked, kept = _walked(tmp_path), {2: 0.5116}
    _o, notes, _e = _sheet_reads(app, walked, kept, {2: "measured"})
    assert notes[2]["source"] in gui.MACHINE_SOURCES
    _o, notes, _e = _sheet_reads(app, walked, kept, {2: "operator"})
    assert notes[2]["source"] == "operator"
    # nothing remembered means his, which is what an offset used to mean
    _o, notes, _e = _sheet_reads(app, walked, kept, None)
    assert notes[2]["source"] == "operator"


def test_the_sheet_opens_holding_a_proposal_for_every_frame(window, tmp_path):
    app, _root = window
    offsets, notes, _edges = _sheet_reads(app, _walked(tmp_path))
    assert len(offsets) == 8
    assert all(notes[n]["source"] in
               ("measured", "unconfirmed", "neighbours") for n in offsets)


def test_a_position_the_operator_set_is_never_re_proposed(window, tmp_path):
    """The sheet is where he corrects this, so overwriting what he typed would
    undo the correction it exists to collect."""
    app, _root = window
    offsets, notes, _edges = _sheet_reads(app, _walked(tmp_path), {3: 1.234})
    assert offsets[3] == 1.234
    assert notes[3]["source"] == "operator"


def test_a_single_frame_is_read_on_its_own(window, tmp_path):
    """The detector reads a frame's own edges, so one prescan is enough -- the
    old strip-level detector needed two to calibrate a base level at all."""
    app, _root = window
    offsets, notes, _edges = _sheet_reads(app, _walked(tmp_path, count=1))
    assert set(offsets) == {1}
    assert notes[1]["source"] == "measured"
    assert offsets[1] < 0, "base at the left: the picture goes left, toward it"


def test_a_detector_that_raises_does_not_stop_the_sheet_opening(
        window, tmp_path, monkeypatch):
    """The walk has already been paid for and the frames are still choosable;
    a sheet that will not open is worse than one with no proposals."""
    from tools.frame_edges import watch

    def broken(*_a, **_k):
        raise RuntimeError("the detector fell over")

    monkeypatch.setattr(watch, "read_frame", broken)
    app, _root = window
    offsets, notes, _edges = _sheet_reads(app, _walked(tmp_path, count=2),
                                          {2: 0.5})
    assert offsets == {2: 0.5}
    assert notes[1]["source"] == "none"
    assert "fell over" in notes[1]["reason"]


def test_the_big_frame_shows_the_detectors_edge_dotted_red(window, tmp_path):
    """Opening a frame big shows where the frame-edge detector read the picture
    ending: a red dotted line, heavier than the aperture guides, drawn on the
    film so it moves with the picture -- with the proposed move applied it sits
    just outside the guide, since the frame is wider than the aperture."""
    app, root = window
    sheet = _read_sheet(app, _walked(tmp_path))
    root.update()
    sheet.adjust(0)
    adj = sheet._adjuster
    root.update()
    adj._draw()
    c = adj.canvas
    red = [it for it in c.find_all()
           if c.type(it) == "line" and c.itemcget(it, "fill") == sheet.EDGE_LINE]
    guides = sorted(c.coords(it)[0] for it in c.find_all()
                    if c.type(it) == "line" and c.itemcget(it, "fill") == adj.GUIDE)
    assert len(red) == 1, "the strip shows base at the left only"
    assert c.itemcget(red[0], "dash") and float(c.itemcget(red[0], "width")) == 3.0
    x = c.coords(red[0])[0]
    # the edge sits a few columns outside the left guide once the move is applied
    assert guides[0] - 20 < x < guides[0]
    sheet.top.destroy()


def test_every_frame_keeps_the_detectors_edges_whoever_set_its_position(
        window, tmp_path):
    """A remembered position -- in the settings or `approved.json` -- used to
    replace the frame's note and throw the detector's reading away, so a sheet
    reopened with remembered positions drew no edge line on any frame."""
    app, _root = window
    walked = _walked(tmp_path)
    fresh, fresh_notes, _e = _sheet_reads(app, walked)
    kept = {1: 0.1234, 2: 0.5116}
    offsets, notes, edges = _sheet_reads(app, walked, kept,
                                         {1: "operator", 2: "measured"})
    # his stays his, and still shows where the detector read the edge
    assert offsets[1] == 0.1234 and notes[1]["source"] == "operator"
    assert edges[1]["edges"] == fresh_notes[1]["edges"]
    # the machine's is read again: today's detector, not a remembered number
    assert offsets[2] == fresh[2] and notes[2]["source"] == "measured"
    assert set(edges) == set(notes)


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
@pytest.mark.parametrize("flipped", [False, True])
def test_the_edge_is_painted_dotted_red_where_it_was_read(degrees, flipped):
    """On the thumbnail itself, so it shows wherever the cell does, turned
    with the picture and broken into dots so the base under it still shows."""
    from rps7200 import preview

    arr = np.zeros((140, 210, 3), dtype=np.uint8)
    read = {"width": 428, "edges": {"left": {"state": "edge", "x": 107.0},
                                    "right": {"state": "picture_to_border", "x": None}}}
    turned = preview.orient(arr, degrees, flipped)
    painted = gui.paint_edges(turned, read, degrees, flipped)
    red = np.all(painted == gui.EDGE_RGB, axis=2)
    axis, f = gui.axis_mark(107.0 / 428, degrees, flipped)
    h, w = painted.shape[:2]
    if axis == "x":
        cols = np.flatnonzero(red.any(axis=0))
        assert abs(cols.mean() - f * w) <= 1.5
        assert 0 < red[:, cols[0]].sum() < h, "dotted, not solid"
    else:
        rows = np.flatnonzero(red.any(axis=1))
        assert abs(rows.mean() - f * h) <= 1.5
        assert 0 < red[rows[0], :].sum() < w, "dotted, not solid"
    assert not np.any(painted[red == 0]), "nothing else is touched"


def test_a_border_side_paints_nothing():
    arr = np.zeros((10, 20, 3), dtype=np.uint8)
    read = {"width": 428, "edges": {"left": {"state": "picture_to_border", "x": None}}}
    assert np.array_equal(gui.paint_edges(arr, read), arr)
    assert np.array_equal(gui.paint_edges(arr, None), arr)


def test_the_big_frame_shows_the_edge_on_a_frame_he_positioned(window, tmp_path):
    """The case that hid the line: a sheet opened with remembered positions."""
    app, root = window
    kept = {n: 0.3 for n in range(1, 9)}
    sheet = _read_sheet(app, _walked(tmp_path), kept,
                        {n: "operator" for n in kept})
    root.update()
    sheet.adjust(0)
    adj = sheet._adjuster
    root.update()
    adj._draw()
    c = adj.canvas
    red = [it for it in c.find_all()
           if c.type(it) == "line" and c.itemcget(it, "fill") == sheet.EDGE_LINE]
    assert len(red) == 1
    sheet.top.destroy()


# -- the frame edges are read in the background ------------------------------


def test_the_header_says_which_frame_of_the_roll_it_is_on():
    assert gui.frame_status(0, 36) == "frame 1 of 36"
    assert gui.frame_status(11, 36) == "frame 12 of 36"
    assert gui.frame_status(36, 36) == "frame 36 of 36"
    assert gui.frame_status(4, None) == "frame 5", "to the end of the strip"


def test_the_edge_count_says_how_far_the_reader_has_got():
    from tools import frame_edges

    def said(state, done=0, total=0, film="negative"):
        return gui.edges_label(frame_edges.Progress(1, state, done, total, film=film))

    assert said(frame_edges.IDLE) == "frame edges"
    assert said(frame_edges.READING, 12, 36) == "frame edges 12/36"
    assert said(frame_edges.DONE, 36, 36) == "frame edges 36/36"
    assert "failed" in said(frame_edges.FAILED, 36, 36)
    assert said(frame_edges.SKIPPED, film="positive") == (
        "frame edges: not read on positive")
    assert set(gui.EDGE_LIGHT) == {frame_edges.IDLE, frame_edges.READING,
                                   frame_edges.DONE, frame_edges.FAILED,
                                   frame_edges.SKIPPED}


def _settle(app, root, seconds=60.0):
    """Pump the window until the frame-edge reader has finished."""
    from tools import frame_edges

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        finished = app.edge_watch.progress().state in (frame_edges.DONE,
                                                        frame_edges.FAILED)
        # and the window's pump has shown that answer, not only reached it
        if finished and app._edge_seen == app.edge_watch.version:
            return app.edge_watch.progress()
        time.sleep(0.02)
    raise AssertionError("the frame-edge reader never finished")


def test_opening_a_roll_opens_its_sheet_before_the_edges_are_read(
        window, tmp_path, monkeypatch):
    """The detector used to run when the sheet was asked for, and the sheet
    waited for it. Now the sheet is there at once and the positions arrive
    in it, with the light going blue to green."""
    from tools import frame_edges

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path)
    app.open_roll(folder)
    assert app.sheet is not None and app.sheet.alive(), "no waiting for the reader"
    progress = _settle(app, root)
    assert progress.state == frame_edges.DONE
    assert app.v_edges.get() == "frame edges 8/8"
    assert app.edge_light.itemcget(app._edge_bulb, "fill") == gui.EDGE_LIGHT[
        frame_edges.DONE]
    assert app.sheet.v_edges.get() == "frame edges 8/8"
    # the whole strip read in one call, as `propose_centred` reads it, and
    # snapped as the sheet snaps it: the answer the reader has to reach
    want, want_notes = gui._snap_proposals(*frame_edges.propose_centred(
        [(r.number, r.image) for r in gui.read_survey(folder)["results"]],
        film="negative"))
    assert app.sheet.offsets == want
    assert {n: v["source"] for n, v in app.sheet.proposals.items()} == {
        n: v["source"] for n, v in want_notes.items()}
    assert set(app.sheet.edges) == set(want_notes)
    app.sheet.top.destroy()


def _sheet_with_readings(app, tmp_path, kept=None):
    from tools import frame_edges

    out = gui.read_survey(_walked_folder(tmp_path))
    frames = [(r.number, r.image) for r in out["results"]]
    offsets, notes = frame_edges.propose_centred(frames, film="negative")
    mine, mine_notes = gui._merge_kept({}, {}, kept or {},
                                       {n: "operator" for n in (kept or {})})
    sheet = gui._ContactSheet(app, out["results"], offsets=mine,
                              proposals=mine_notes, readings=({}, {}))
    return sheet, offsets, notes


def test_a_reading_that_arrives_late_fills_the_sheet_and_leaves_his_alone(
        window, tmp_path):
    app, root = window
    sheet, offsets, notes = _sheet_with_readings(app, tmp_path, kept={2: 0.5116})
    root.update()
    assert sheet.offsets == {2: 0.5116} and not sheet.edges
    sheet.take_readings(offsets, notes)
    root.update()
    snapped, _ = gui._snap_proposals(offsets, notes)
    assert sheet.offsets[1] == snapped[1]
    assert sheet.proposals[1]["source"] == "measured"
    # his position stands; the detector's edge lines are drawn on it anyway
    assert sheet.offsets[2] == 0.5116 and sheet.proposals[2]["source"] == "operator"
    assert sheet.edges[2]["edges"] == notes[2]["edges"]
    sheet.top.destroy()


def test_his_as_surveyed_is_still_his_when_the_sheet_is_reopened(window,
                                                                 tmp_path):
    """Centre on a frame the detector wanted to move: an explicit zero. It
    was dropped from the offsets, so on the next reopen the detector's number
    came back labelled measured, and the roll moved the frame."""
    app, root = window
    sheet, offsets, notes = _sheet_with_readings(app, tmp_path)
    sheet.take_readings(offsets, notes)
    assert sheet.offsets.get(1), "the detector proposes a move for frame 1"
    sheet.adjust(0)
    root.update()
    sheet._adjuster._centre()
    assert sheet.offsets[1] == 0.0 and sheet.proposals[1]["source"] == "operator"
    assert sheet._captions[1].cget("text") == "as walked (yours)"
    state = json.loads(json.dumps(sheet.state()))       # through the settings
    frames = sheet.frames
    sheet.top.destroy()

    kept = gui.ScannerGui._clean_sheet_state(state)
    mine, mine_notes = gui._merge_kept({}, {}, kept["offsets"], kept["sources"])
    again = gui._ContactSheet(app, frames, offsets=mine, proposals=mine_notes,
                              readings=(offsets, notes))
    assert again.offsets[1] == 0.0
    assert again.proposals[1]["source"] == "operator"
    again.top.destroy()


def test_a_sheet_saved_before_zeros_were_kept_still_keeps_his_centre():
    """Those recorded him as the source of a frame with no position at all."""
    out, notes = gui._merge_kept({1: 0.5116}, {1: {"source": "measured"}},
                                 {}, {1: "operator", 2: "measured"})
    assert out[1] == 0.0 and notes[1]["source"] == "operator"
    assert 2 not in out


def test_his_as_surveyed_goes_into_approved_json_as_his(tmp_path):
    """Every ticked frame is written with a position, so a zero alone says
    nothing -- an untouched frame's zero is no decision, and older files
    labelled those `operator` too. His is marked."""
    class R:
        def __init__(self, number):
            self.number, self.image, self.entry = number, None, ""

    approved = gui.approved_from_sheet(
        [R(1), R(2), R(3)], (1, 2, 3), {1: 0.0, 3: 0.5116},
        {1: {"source": "operator"}, 3: {"source": "measured"}})
    assert [a.source for a in approved] == ["operator", "none", "measured"]
    folder = tmp_path / "roll"
    gui.ScannerGui._write_approved(_approving(tmp_path), approved, folder)
    offsets, _r, _f, _e, sources = gui.read_approved(folder)
    assert offsets == {1: 0.0, 3: pytest.approx(0.5116)}
    assert sources == {1: "operator", 3: "measured"}

    older = tmp_path / "older"
    older.mkdir()
    (older / "approved.json").write_text(json.dumps({"numbering": "strip",
        "frames": [{"number": 4, "offset_mm": 0.0, "source": "operator"}]}),
        encoding="utf-8")
    offsets, _r, _f, _e, sources = gui.read_approved(older)
    assert offsets == {} and sources == {}, "an untouched frame, as it was"


def test_a_machine_position_is_filed_with_what_the_detector_read(tmp_path):
    """approved.json kept the snapped offset alone: whether a frame that came
    out off centre was read wrong or moved wrong could not be told, nor which
    detector had read it."""
    from rps7200 import library

    stub = _approving(tmp_path)
    stub.edge_watch = types.SimpleNamespace(frame_units=350.6)
    note = {"source": "measured", "width": np.int64(428), "units": 12.5,
            "columns": 15.6, "reason": "both sides",
            "edges": {"left": {"state": "edge", "x": np.float64(18.0)}}}
    approved = (Approved(1, offset_mm=0.5116, source="measured"),
                Approved(2, offset_mm=0.2, source="operator"))
    folder = tmp_path / "roll"
    gui.ScannerGui._write_approved(stub, approved, folder,
                                   {1: note, 2: {"source": "operator"}})
    frames = {f["number"]: f for f in json.loads(
        (folder / "approved.json").read_text(encoding="utf-8"))["frames"]}
    reading = frames[1]["reading"]
    assert reading["width"] == 428 and reading["units"] == 12.5
    assert reading["edges"]["left"]["x"] == 18.0
    assert reading["frame_units"] == 350.6
    assert frames[1]["read_by"]["driver_commit"] == (
        library.provenance()["driver_commit_at_import"])
    # The reader's own source too: a commit names it only from a clean tree.
    reader = frames[1]["read_by"]["edge_reader_sha256"]
    assert reader == library.provenance()["edge_reader_source_sha256_at_import"]
    assert isinstance(reader, str) and len(reader) == 64
    # And the driver's, which the reader takes constants and code from.
    assert frames[1]["read_by"] == library.code_identity()
    assert "reading" not in frames[2] and "read_by" not in frames[2], (
        "his position is his, not a reading")
    offsets, _r, _f, _e, sources = gui.read_approved(folder)
    assert sources == {1: "measured", 2: "operator"}


def test_his_position_keeps_the_detectors_reading_beside_it(tmp_path):
    """The frames he overruled are where a reading is most worth having, and
    approved.json kept none for them: the sheet's note there is his."""
    from rps7200 import library

    stub = _approving(tmp_path)
    stub.edge_watch = types.SimpleNamespace(frame_units=350.6)
    theirs = {"source": "measured", "units": -4.0, "image_sha1": "ab" * 20,
              "members": {"chroma": {"left": ["edge", 3.25], "right": None}}}
    approved = (Approved(1, offset_mm=0.2, source="operator"),
                Approved(2, offset_mm=0.0, source="operator"))
    folder = tmp_path / "roll"
    gui.ScannerGui._write_approved(
        stub, approved, folder,
        {1: {"source": "operator"}, 2: {"source": "operator"}},
        {1: theirs})
    frames = {f["number"]: f for f in json.loads(
        (folder / "approved.json").read_text(encoding="utf-8"))["frames"]}
    assert frames[1]["source"] == "operator" and "reading" not in frames[1]
    kept = frames[1]["detector_reading"]
    assert kept["units"] == -4.0 and kept["members"] == theirs["members"]
    assert kept["frame_units"] == 350.6
    assert frames[1]["read_by"] == library.code_identity()
    # One the detector had not read yet keeps nothing beside his.
    assert "detector_reading" not in frames[2] and "read_by" not in frames[2]
    # A note that will not go into the file costs that note, not the file.
    gui.ScannerGui._write_approved(stub, approved[:1], folder,
                                   {}, {1: ["not", "a", "note"]})
    frames = {f["number"]: f for f in json.loads(
        (folder / "approved.json").read_text(encoding="utf-8"))["frames"]}
    assert "record_error" in frames[1]["detector_reading"]
    assert frames[1]["offset_mm"] == 0.2


def test_the_sheets_commission_files_the_readings_behind_its_positions(
        window, tmp_path, monkeypatch):
    """The same, through the sheet's own button: its notes reach the file,
    each with what it needs to be read again -- every member's answer, the
    picture read and the frames it was read against -- and the code named
    without asking git, on the Tk thread."""
    import subprocess

    from rps7200 import library

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    app.calibrated = True
    app.open_roll(_walked_folder(tmp_path, count=3))
    _settle(app, root)
    # Frame 1 put where he wanted it, as the frame-position window does.
    app.sheet.offsets[1] = gui.snap_offset(1.0)
    app.sheet.proposals[1] = {"source": "operator", "reason": "you set this one"}

    def no_git(*args, **kwargs):
        raise AssertionError(f"git was asked on the Tk thread: {args}")

    monkeypatch.setattr(subprocess, "run", no_git)
    app.sheet._scan()
    written, = [p for p in tmp_path.rglob("approved.json")]
    frames = {f["number"]: f for f in json.loads(
        written.read_text(encoding="utf-8"))["frames"]}
    machine = [f for f in frames.values() if f["source"] in gui.MACHINE_SOURCES]
    assert machine, "the fixture's frames are read by the detector"
    for record in machine:
        reading = record["reading"]
        assert reading["edges"] and reading["width"] == 428
        assert set(reading["members"]) == {"changepoint", "chroma", "stepline",
                                           "gapmodel"}
        assert reading["image_sha1"] and reading["against_sha1"]
        assert reading["against"] == sorted({1, 2, 3} - {record["number"]})
        assert record["read_by"] == library.code_identity()
    assert frames[1]["source"] == "operator" and "reading" not in frames[1]
    theirs = frames[1]["detector_reading"]
    assert theirs["against"] == [2, 3] and set(theirs["members"])


def test_the_frame_position_window_closes_with_its_sheet(window, tmp_path):
    """It was the main window's child, outlived the sheet, and every position
    set in it afterwards went into a sheet that no longer existed."""
    app, root = window
    sheet, _offsets, _notes = _sheet_with_readings(app, tmp_path)
    sheet.adjust(0)
    root.update()
    adjuster = sheet._adjuster
    assert adjuster.alive()
    sheet._dismiss()
    root.update()
    assert not adjuster.alive()


def test_opening_another_roll_keeps_what_the_open_sheet_held(window, tmp_path,
                                                            monkeypatch):
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    first = _walked_folder(tmp_path, count=3).rename(tmp_path / "first")
    second = _walked_folder(tmp_path, count=3)
    app.open_roll(first)
    app.sheet._rotate(2, 90)
    app.open_roll(second)
    assert app.remembered["sheet"]["first"]["rotations"][2] == 90
    app.sheet.top.destroy()


def _leave_a_sheet(app, folder):
    """What a sheet closed on this walk files in the settings: frame 1
    unticked, frame 2 put where he wanted it, frame 3 turned."""
    app._sheet_roll = folder
    app._store_sheet_state({
        "ticks": {1: False, 2: True, 3: True},
        "offsets": {2: gui.snap_offset(1.5)}, "sources": {2: "operator"},
        "rotations": {3: 90}, "flips": {}, "options": {}})
    app.sheet_state, app._sheet_roll = {}, None


def test_a_new_walk_in_a_folder_of_that_name_starts_a_clean_sheet(window,
                                                                  tmp_path):
    """The decisions were filed by folder name alone, so a fresh walk into a
    folder of that name -- the film stock typed again for the next strip, or
    a name reused after a Delete -- opened on the last strip's ticks, turns
    and positions, the positions measured against prescans since replaced."""
    import os

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    app._sheet_roll = folder
    assert app._recall_sheet_state()["rotations"] == {3: 90}, "the same walk"
    # The next strip walked into the same folder writes its own survey.
    later = (folder / "survey.json").stat().st_mtime_ns + 10**9
    os.utime(folder / "survey.json", ns=(later, later))
    recalled = app._recall_sheet_state()
    assert recalled["rotations"] == {} and recalled["offsets"] == {}
    assert recalled["ticks"] == {}


def test_a_walk_not_yet_commissioned_reopens_as_it_was_left(window, tmp_path,
                                                           monkeypatch):
    """Quit said it was keeping the sheet of a walk not yet commissioned,
    and no reopen ever read it back: after a restart every tick, turn and
    hand-set position was gone. A commissioned roll still comes back from
    its approved.json."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    app.open_roll(folder)
    sheet = app.sheet
    assert sheet.ticks[1].get() is False
    assert sheet.rotations.get(3) == 90
    assert sheet.offsets.get(2) == gui.snap_offset(1.5)
    sheet.top.destroy()

    # Commissioned since, frame 3 turned back: approved.json is the record
    # for the frame it records, and the sheet's own decisions stand for the
    # frames it does not (P22-a) -- once there was an approved.json they
    # were never read again.
    (folder / "approved.json").write_text(json.dumps(
        {"numbering": "strip", "frames": [
            {"number": 3, "rotation": 0,
             "decided_utc": "2999-01-01T00:00:00+00:00"}]}),
        encoding="utf-8")
    _leave_a_sheet(app, folder)
    app.open_roll(folder)
    assert app.sheet.ticks[1].get() is False
    assert app.sheet.offsets.get(2) == gui.snap_offset(1.5)
    assert not app.sheet.rotations.get(3), "approved.json is the record"
    app.sheet.top.destroy()


def test_a_decision_on_the_sheet_is_on_disk_before_the_sheet_closes(
        window, tmp_path, monkeypatch):
    """Filed only when the sheet closed, so a crash or a kill with it open
    lost everything decided since it opened -- for a walk not yet
    commissioned, the only record of it."""
    app, root = window
    monkeypatch.setattr(gui, "SHEET_KEEP_MS", 10, raising=False)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)
    app.open_roll(folder)
    app.sheet._rotate(2, 90)
    app.sheet._toggle(3)
    deadline = time.monotonic() + 5
    stored = {}
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
        on_disk = json.loads((tmp_path / "gui-settings.json").read_text(
            encoding="utf-8"))
        stored = (on_disk.get("sheet") or {}).get("walk") or {}
        if (stored.get("rotations") or {}).get("2") == 90:
            break
    assert app.sheet.alive(), "and without closing it"
    assert stored["rotations"]["2"] == 90 and stored["ticks"]["3"] is False
    app.sheet.top.destroy()


def test_a_renamed_walk_keeps_its_sheet_and_a_restored_one_finds_it(
        window, tmp_path, monkeypatch):
    """A Delete moves the folder into the trash now, and a restore brings it
    back under its name with its walk: the sheet's decisions, found by the
    two, are found again. A new walk into the name is another walk, and
    finds none (`_stored_sheet`)."""
    from rps7200 import library

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "holiday")
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_rename_roll({"folder": folder, "roll": "walk"})
    moved = folder.with_name("holiday")
    assert "walk" not in app.remembered["sheet"]
    app._sheet_roll = moved
    assert app._recall_sheet_state()["rotations"] == {3: 90}
    app._sheet_roll = None
    app.on_delete_rolls([{"folder": moved, "roll": "walk", "size": 0}])
    assert not moved.exists()
    assert library.in_trash(tmp_path, "holiday") is not None
    library.restore(tmp_path, "holiday")
    app._sheet_roll = moved
    assert app._recall_sheet_state()["rotations"] == {3: 90}
    app._sheet_roll = None


def _another_walk(tmp_path, name="walk"):
    """A fresh walk into ``tmp_path / name``, its survey.json a second on
    from any walk before it, so the two are different walks."""
    import os

    made = _walked_folder(tmp_path, count=3)
    if made.name != name:
        made = made.rename(tmp_path / name)
    survey = made / "survey.json"
    later = survey.stat().st_mtime_ns + 10**9
    os.utime(survey, ns=(later, later))
    return made


def _leave_another_sheet(app, folder):
    """A sheet left on another walk: frame 2 turned upside down."""
    app._sheet_roll = folder
    app._store_sheet_state({"ticks": {}, "offsets": {}, "sources": {},
                            "rotations": {2: 180}, "flips": {},
                            "options": {}})
    app.sheet_state, app._sheet_roll = {}, None


def _recalled(app, folder, restarted=False):
    """The turns a sheet opened on ``folder`` would come back with -- after
    a restart, with ``restarted``: read back from the settings file."""
    if restarted:
        app.remembered = gui.settings.load(app._settings_path)
    app.sheet_state, app._sheet_roll = {}, folder
    try:
        return app._recall_sheet_state()["rotations"]
    finally:
        app._sheet_roll = None


@pytest.mark.parametrize("restarted", [False, True])
def test_a_trashed_walks_sheet_outlives_a_new_walk_of_its_name(
        window, tmp_path, monkeypatch, restarted):
    """Filed by folder name, a walk not yet commissioned had its sheet's
    decisions -- their only record -- written over by the next walk typed
    under its name once it was in the trash. They are set aside, and found
    by their walk when it comes back."""
    from rps7200 import library

    app, root = window
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    app.on_delete_rolls([{"folder": folder, "roll": "walk", "size": 0}])
    first = library.in_trash(tmp_path, "walk")
    assert first is not None and not folder.exists()
    again = _another_walk(tmp_path)
    _leave_another_sheet(app, again)
    assert _recalled(app, again, restarted) == {2: 180}
    app.on_delete_rolls([{"folder": again, "roll": "walk", "size": 0}])
    library.restore(tmp_path, first.name)
    assert folder.is_dir()
    assert _recalled(app, folder, restarted) == {3: 90}


def test_a_walk_restored_under_another_name_finds_its_sheet(
        window, tmp_path, monkeypatch):
    """Its name taken since, a walk comes back from the trash as
    ``<name>-2``, and its decisions were looked for under that name."""
    from rps7200 import library

    app, root = window
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    app.on_delete_rolls([{"folder": folder, "roll": "walk", "size": 0}])
    again = _another_walk(tmp_path)
    _leave_another_sheet(app, again)
    said = library.restore(tmp_path, "walk")
    back = tmp_path / "walk-2"
    assert back.is_dir(), said
    assert _recalled(app, back, restarted=True) == {3: 90}
    assert _recalled(app, again) == {2: 180}, "the new walk keeps its own"


def test_a_rename_onto_a_trashed_walks_name_keeps_that_walks_sheet(
        window, tmp_path, monkeypatch):
    from rps7200 import library

    app, root = window
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "walk")
    folder = _walked_folder(tmp_path, count=3)
    _leave_a_sheet(app, folder)
    app.on_delete_rolls([{"folder": folder, "roll": "walk", "size": 0}])
    other = _another_walk(tmp_path, name="other")
    _leave_another_sheet(app, other)
    app.on_rename_roll({"folder": other, "roll": "walk"})
    renamed = tmp_path / "walk"
    assert renamed.is_dir() and not other.exists()
    assert _recalled(app, renamed) == {2: 180}
    app.on_delete_rolls([{"folder": renamed, "roll": "walk", "size": 0}])
    library.restore(tmp_path, library.trash_batches(tmp_path)[0].name)
    assert folder.is_dir()
    assert _recalled(app, folder, restarted=True) == {3: 90}


def test_deleting_a_roll_says_what_nothing_can_rebuild(window, tmp_path,
                                                       monkeypatch):
    """The question called everything but approved.json re-derivable, and
    counted that only where it held a turn. No tool rebuilds a walk.

    DOC-08: and it counted every prescan from before an in-walk correction
    as having no entry, when both front ends file one; promised the walk's
    prescans stay and every frame can be exported; left out a pass the
    library refused, kept in unfiled/ with its raw bytes, and its size; and
    said nothing of a frame whose entry had gone, its frameNN.tif the last
    copy. What goes for good is counted from the disk and the library now,
    once the trash is emptied -- the folder itself goes into the trash."""
    import pathlib

    from rps7200 import library

    app, root = window
    library_root = pathlib.Path(app.session.root)
    filed = _a_library_entry(library_root, seed=5)
    folder = _walked_folder(tmp_path, count=3)
    (folder / "prescan02-before.tif").write_bytes(b"only here")
    (folder / "prescan03-before.tif").write_bytes(b"filed too")
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["frames"][2].update(prescan_before="prescan03-before.tif",
                               prescan_before_entry=filed.name)
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "approved.json").write_text(json.dumps({
        "numbering": "strip",
        "frames": [{"number": 1, "offset_mm": 0.5, "source": "operator"}]}),
        encoding="utf-8")
    kept = folder / "unfiled" / "20261009T000000Z_refused"
    kept.mkdir(parents=True)
    (kept / "scan.json").write_text("{}", encoding="utf-8")
    (kept / "raw.bin").write_bytes(b"\0" * 2_500_000)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "walk", "numbering": "strip",
        "frames": [{"number": 1, "done": True, "entry": "library\\gone"},
                   {"number": 2, "done": True,
                    "entry": f"library\\{filed.name}"}]}), encoding="utf-8")
    (folder / "frame01.tif").write_bytes(b"the last copy")
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or False)
    app.on_delete_rolls([gui.roll_summary(folder)])
    assert "survey.json), which nothing rebuilds" in asked[0]
    assert "set by hand in it (approved.json)" in asked[0]
    assert ("1 prescan taken before an in-walk correction with no library "
            "entry") in asked[0]
    assert "1 pass the library refused, kept in unfiled/ (2.5 MB)" in asked[0]
    assert ("frame 1 of walk, whose entry is gone: its frameNN.tif is the "
            "last copy") in asked[0]
    assert "frame 2 of walk" not in asked[0], "its entry is in the library"
    assert "can be rebuilt from them" not in asked[0]
    assert "prescans stay" not in asked[0]
    assert "into the rolls' trash" in asked[0]
    assert folder.exists()

    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete_rolls([gui.roll_summary(folder)])
    assert not folder.exists()
    (batch,) = library.trash_batches(tmp_path)
    assert batch.names == ["walk"] and batch.kind == "rolls"
    assert (batch.path / "walk" / "unfiled" / kept.name / "raw.bin").exists()
    said = app.log.get("1.0", "end")
    assert f"to the trash ({batch.path})" in said
    assert f"restore {batch.name}" in said and "--rolls" in said


def test_deleting_a_roll_names_the_folder_that_goes(window, tmp_path,
                                                    monkeypatch):
    """A duplicate or a renamed roll keeps its original's name inside its
    manifest. The question named that, so with a roll and its duplicate side
    by side it asked to delete "2026-09-28-154137" and removed renamed-roll."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    renamed = folder.rename(folder.with_name("renamed-roll"))
    summary = gui.roll_summary(renamed)
    assert summary["roll"] != "renamed-roll", "the manifest keeps its name"
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or False)
    app.on_delete_rolls([summary])
    assert asked[0].startswith("Move 1 roll folder -- renamed-roll -- into "
                               "the rolls' trash")
    # One folder, said as one: "They stay".
    assert "It stays whole there" in asked[0]
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete_rolls([summary])
    assert not renamed.exists()
    assert "moved roll folder renamed-roll to the trash" in app.log.get(
        "1.0", "end")


def test_opening_a_roll_says_what_is_scanned_and_names_its_folder(
        window, tmp_path, monkeypatch):
    """Six frames walked, the sheet's two scanned: it said "all of them
    already scanned". And a copy was named by the roll inside it, so opening
    walk-copy said "walk"."""
    app, root = window
    folder = _walked_folder(tmp_path, count=6)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "walk", "numbering": "strip", "wanted": [2, 4],
        "frames": [{"number": 2, "done": True}, {"number": 4, "done": True}],
    }), encoding="utf-8")
    copy = folder.rename(folder.with_name("walk-copy"))
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda title, words, **k: said.append((title, words)))
    app.open_roll(copy)
    (words,) = [w for t, w in said if t == "Open a roll"]
    assert words.startswith(
        "6 frames from walk-copy; 2, 4 scanned, which is every frame asked "
        "for."), words
    assert "all of them" not in words
    with contextlib.suppress(Exception):
        app.sheet.top.destroy()


def test_a_count_agrees_with_its_noun():
    assert gui.counted(1, "frame") == "1 frame"
    assert gui.counted(2, "frame") == "2 frames"
    assert gui.counted(1, "pass", "passes") == "1 pass"
    assert gui.counted(0, "pass", "passes") == "0 passes"
    assert (gui.is_are(1), gui.is_are(3)) == ("is", "are")


def test_save_all_of_one_pass_says_one_pass(window, tmp_path, monkeypatch):
    """It asked to "Write 1 passes", "1 of them are re-corrected"."""
    from rps7200.session import Result

    app, root = window
    app._add_result(Result(seq=1, kind="scan", label="one", image=np.zeros(
        (4, 6, 3), np.uint16), meta={}, entry=tmp_path))
    assert len(app.results) == 1
    monkeypatch.setattr(gui.filedialog, "askdirectory",
                        lambda **k: str(tmp_path))
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda title, words, **k: asked.append(words) or False)
    app.on_save_all()
    assert asked[0].startswith(f"Write 1 pass into {tmp_path.name} as ")
    assert "It is re-corrected from the library" in asked[0]
    assert "takes a moment." in asked[0], "not \"a moment each\""
    assert "passes" not in asked[0] and "of them" not in asked[0]


def test_a_batch_ends_by_saying_what_it_wrote(window):
    """Export shared Save all's closing line, which said "saved 1 of 1 pass"
    of a frame."""
    app, root = window
    app._saved(("done", 1, 1, "frame", "frames"))
    app._saved(("done", 2, 2))
    lines = app.log.get("1.0", "end").rstrip().splitlines()
    assert lines[-2] == "saved 1 of 1 frame"
    assert lines[-1] == "saved 2 of 2 passes"


def test_export_names_the_folder_and_logs_the_frames_it_skips(
        window, tmp_path, monkeypatch):
    """A copy was named by the roll inside it -- "None of the frames in
    after-roll" of after-roll-2 -- and the question promised "the log names
    it" of a skipped frame the log never named."""
    app, root = window
    copy = tmp_path / "after-roll-2"
    copy.mkdir()
    summary = {"folder": copy, "roll": "after-roll", "done": [1, 2]}
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda title, words, **k: said.append(words))
    # The window's own namespace: the fixture loads the tool afresh.
    window_gui = type(app).on_export_rolls.__globals__
    monkeypatch.setitem(window_gui, "roll_exports", lambda s: [])
    app.on_export_rolls([summary])
    assert said and said[0].startswith("None of the frames in after-roll-2 ")

    kept = types.SimpleNamespace(number=2)
    monkeypatch.setitem(window_gui, "roll_exports", lambda s: [kept])
    monkeypatch.setattr(gui.filedialog, "askdirectory",
                        lambda **k: str(tmp_path))
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda title, words, **k: asked.append(words) or True)
    monkeypatch.setattr(app, "_mono_for", lambda item: (False, "avg"))
    monkeypatch.setattr(app, "_start_writing", lambda run, name: None)
    app.on_export_rolls([summary])
    assert "1 scanned frame has no library entry left and is skipped -- the " \
           "log names it." in asked[0]
    assert "after-roll-2: frame 1 has no library entry left, and is skipped" \
        in app.log.get("1.0", "end")
    app._saving = False


def test_opening_a_roll_of_one_scanned_frame_is_not_all_of_them(
        window, tmp_path, monkeypatch):
    app, root = window
    folder = _walked_folder(tmp_path, count=1)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "walk", "numbering": "strip",
        "frames": [{"number": 1, "done": True}],
    }), encoding="utf-8")
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda title, words, **k: said.append((title, words)))
    app.open_roll(folder)
    (words,) = [w for t, w in said if t == "Open a roll"]
    assert words.startswith("1 frame from walk, already scanned."), words
    with contextlib.suppress(Exception):
        app.sheet.top.destroy()


def test_positions_not_reached_speaks_only_of_the_job_that_ended(window,
                                                                 monkeypatch):
    """It read every pass the window held, so one roll's missed frame was
    announced again after every job that followed -- another roll, a single
    scan, a plain prescan -- naming a frame nobody was scanning."""
    from rps7200.session import Event, Result

    app, root = window
    warned = []
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda title, words, **k: warned.append(words))
    image = np.zeros((4, 6, 3), np.uint16)

    def job(*results):
        app._handle(Event(kind="state", text="scanning", busy=True))
        for result in results:
            app._handle(Event(kind="result", result=result))
        app._handle(Event(kind="state", text="idle", busy=False))
        app._handle(Event(kind="finished", text="done"))

    missed = Result(seq=901, kind="frame", label="frame 2", image=image,
                    meta={}, number=2, registration={"approved": {
                        "outcome": "budget", "residual_mm": 1.0}})
    job(missed)
    assert len(warned) == 1 and "frame 2" in warned[0]
    job(Result(seq=902, kind="prescan", label="prescan", image=image,
               meta={}))
    assert len(warned) == 1, "a later job said it again"


# -- marking the frames a roll did not place ---------------------------------
#
# Stefan's choice, 2026-10-05: a badge on the frame and a list at the end of
# the roll, at two levels -- a miss the hold measured, with how far, and a
# frame nothing could measure.

#: Hold records as `_hold_to_approved` files them, one of each kind.
_PLACED = {"outcome": "held", "target_mm": 0.0, "moves": 1,
           "final_mm": 0.05, "residual_mm": -0.05}
_MISSED = {"outcome": "not_converged", "target_mm": 0.0, "moves": 3,
           "final_mm": -2.41, "residual_mm": 2.41}
_UNVERIFIED = {"outcome": "unverified", "target_mm": -0.24, "moves": 1,
               "final_mm": None, "residual_mm": None}
_OFF = {"outcome": "off", "target_mm": 0.0,
        "reason": "holding was switched off earlier in this roll"}


def test_a_hold_that_placed_its_frame_is_not_marked():
    assert gui.hold_level(_PLACED) is None
    assert gui.hold_level(None) is None
    assert gui.hold_badge(_PLACED) == ""
    assert gui.hold_note(_PLACED) == "held after 1 move"
    assert gui.hold_note(dict(_PLACED, moves=2)) == "held after 2 moves"
    assert gui.hold_note(dict(_PLACED, moves=0)) == "held where it arrived"
    assert gui.hold_note(None) == ""


def test_a_missed_position_says_how_far_out_in_units():
    from rps7200.framing import say_hold
    from rps7200.protocol import say_units, units

    assert gui.hold_level(_MISSED) == gui.MISSED
    note = gui.hold_note(_MISSED)
    assert note == (f"not placed: {say_hold('not_converged')} "
                    f"({say_units(2.41, signed=False)} out)")
    assert "mm" not in note
    assert gui.hold_badge(_MISSED) == f"{units(2.41):.0f} units out"


def test_a_frame_nothing_could_measure_is_not_verified():
    from rps7200.framing import say_hold

    # The last is a hold a failure broke into part way: its record keeps the
    # step it had reached, a move.
    for held in (_UNVERIFIED, _OFF, {"outcome": "move", "moves": 1}):
        assert gui.hold_level(held) == gui.UNVERIFIED, held
        assert gui.hold_badge(held) == "not verified"
        assert "out)" not in gui.hold_note(held)
    assert gui.hold_note(_UNVERIFIED) == "not placed: " + say_hold("unverified")
    assert gui.hold_note(_OFF) == "not placed: " + say_hold("off")
    # In the tools' words for it. The window said "was interrupted", a Stop's
    # words, while `tools/scan_roll.py` printed the bare code.
    assert say_hold("move") != "move"
    assert gui.hold_note({"outcome": "move"}) == "not placed: " + say_hold("move")


def test_the_two_badges_are_told_apart():
    assert gui.HOLD_BADGE[gui.MISSED] != gui.HOLD_BADGE[gui.UNVERIFIED]
    assert " " not in gui.MISSED + gui.UNVERIFIED, "they are canvas tags too"


def _held_roll(app, end="finished"):
    """A roll of four frames through the window's own pump, ending ``end``.

    Frame 1 placed, 2 measured off its position, 3 not verified, and 4 failed
    after a hold that did not place it either: its prescan arrives, its scan
    never does. In the session's order -- each frame's prescan, then its scan
    sharing one registration -- and the end before the window goes idle.
    """
    from rps7200.session import Event, Result

    image = np.zeros((4, 6, 3), np.uint16)
    app._handle(Event(kind="state", text="scanning a roll of 4 frames", busy=True))
    seq = 900
    for number, held, scanned in ((1, _PLACED, True), (2, _MISSED, True),
                                  (3, _UNVERIFIED, True), (4, _MISSED, False)):
        marks = {"approved": dict(held)}
        kinds = ("prescan", "frame") if scanned else ("prescan",)
        for kind in kinds:
            seq += 1
            app._handle(Event(kind="result", result=Result(
                seq=seq, kind=kind, label=f"frame {number} {kind}", image=image,
                meta={}, number=number, registration=marks, position=number)))
    app._handle(Event(kind=end, text="done" if end == "finished"
                      else "DeviceSuspect: the scanner may still be mid-scan"))
    app._handle(Event(kind="state", text="idle", busy=False))


@pytest.mark.parametrize("end", ["finished", "failed"])
def test_the_frames_a_roll_did_not_place_are_named_in_two_lists(window,
                                                                monkeypatch, end):
    """At the end of the roll, finished or failed part way: it ran only on
    "finished", so a roll that failed at frame 7 never said which of frames 1
    to 6 had gone out off their positions. A frame that failed is not in the
    list -- it was not scanned, and is offered again."""
    from rps7200.framing import say_hold
    from rps7200.protocol import say_units

    app, _root = window
    warned = []
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda title, words, **k: warned.append(words))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)
    _held_roll(app, end)
    (said,) = warned
    assert said.startswith("2 frames were scanned without reaching the position")
    measured, unmeasured = said.split("Measured away from it:")[1].split("Not verified")
    assert (f"frame 2: {say_hold('not_converged')} -- "
            f"{say_units(2.41, signed=False)} out") in measured
    assert f"frame 3: {say_hold('unverified')}" in unmeasured
    assert "frame 1" not in said and "frame 4" not in said


def test_the_filmstrip_badges_a_frame_the_roll_did_not_place(window, monkeypatch):
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: None)
    _held_roll(app)
    root.update()
    strip = app.strip
    by_frame = {}
    for item in strip.find_withtag("hold"):
        (seq,) = [int(t[1:]) for t in strip.gettags(item)
                  if t.startswith("r") and t[1:].isdigit()]
        (result,) = [r for r in app.results if r.seq == seq]
        (level,) = [t for t in strip.gettags(item) if t.startswith("hold-")]
        # Every badge, in a list: in a set, a prescan and its scan both
        # marked read as one.
        by_frame.setdefault(result.number, []).append((result.kind, level))
        assert strip.itemcget(item, "fill") == gui.HOLD_BADGE[level[5:]][0]
    # The frame placed carries none; its scan replaced its prescan, so each
    # scanned frame shows once. The one that failed shows its prescan, marked.
    assert by_frame == {2: [("frame", "hold-missed")],
                        3: [("frame", "hold-unverified")],
                        4: [("prescan", "hold-missed")]}


def test_a_pass_is_on_the_filmstrip_as_soon_as_it_arrives(window):
    """It was arranged -- which shows it and redraws the strip -- before it
    was in the list the strip is drawn from, so the strip was always a pass
    behind, and a roll's last frame and its badge were missing from it until
    something else redrew it."""
    from rps7200.session import Event, Result

    app, _root = window
    image = np.zeros((4, 6, 3), np.uint16)
    for seq in (991, 992):
        app._handle(Event(kind="result", result=Result(
            seq=seq, kind="prescan", label=f"prescan {seq}", image=image,
            meta={})))
        assert app.strip.find_withtag(f"r{seq}"), seq
        assert app.current.seq == seq


def test_the_shown_frame_says_how_it_was_held(window, monkeypatch):
    from rps7200.framing import say_hold

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: None)
    _held_roll(app)
    frames = {r.number: r for r in app.results if r.kind == "frame"}
    app._show(frames[1])
    assert "held after 1 move" in app.v_caption.get()
    app._show(frames[2])
    assert "not placed: " + say_hold("not_converged") in app.v_caption.get()
    assert "units out)" in app.v_caption.get()
    app._show(frames[3])
    assert "not placed: " + say_hold("unverified") in app.v_caption.get()


def test_the_contact_sheet_badges_a_frame_the_roll_did_not_place(
        window, tmp_path, monkeypatch):
    """Reopened from disk, as the roll filed it: roll.json's frames carry the
    hold under `registration.approved`. Amber with how far for a measured
    miss, red for one nothing measured; nothing on a frame it placed, one it
    has not scanned, or one whose scan failed."""
    from rps7200.protocol import units

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=5)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "walk", "numbering": "strip",
        "frames": [
            {"number": 1, "done": True, "registration": {"approved": _PLACED}},
            {"number": 2, "done": True, "registration": {"approved": _MISSED}},
            {"number": 3, "done": True, "registration": {"approved": _OFF}},
            {"number": 4, "done": False, "error": "read timed out",
             "registration": {"approved": _MISSED}},
        ]}), encoding="utf-8")
    assert set(gui.read_survey(folder)["holds"]) == {1, 2, 3}
    app.open_roll(folder)
    root.update()
    sheet = app.sheet

    def badges():
        return {n: (b.cget("text"), b.cget("background"))
                for n, b in app.sheet._badges.items()}

    assert badges() == {
        2: (f"{units(2.41):.0f} units out", gui.HOLD_BADGE[gui.MISSED][0]),
        3: ("not verified", gui.HOLD_BADGE[gui.UNVERIFIED][0])}
    # On the picture, in its corner, and a click there is a click on the frame.
    assert sheet._badges[2].winfo_manager() == "place"
    ticked = sheet.ticks[2].get()
    _press(sheet._badges[2], "<Button-1>")
    assert sheet.ticks[2].get() is not ticked
    # Closed and opened again from the window, the badges come back.
    sheet._dismiss()
    app.on_contact_sheet()
    root.update()
    assert set(badges()) == {2, 3}
    app.sheet.top.destroy()


def _roll_json(folder, *frames):
    """``folder``'s roll.json as the session writes it, holding ``frames``."""
    (folder / "roll.json").write_text(json.dumps({
        "roll": folder.name, "numbering": "strip", "frames": list(frames)}),
        encoding="utf-8")


def _badged(app):
    """What the open sheet's badges say, by frame."""
    return {n: b.cget("text") for n, b in app.sheet._badges.items()}


def test_a_frame_taken_again_keeps_no_badge_from_its_last_take(
        window, tmp_path, monkeypatch):
    """The sheet's button laid what roll.json says now over what it said when
    the roll was opened, which only adds. The session keeps one record per
    frame, so a frame taken again unheld -- the Roll button holds nothing --
    or whose new take failed went on wearing the old take's badge, which the
    same roll opened afresh did not show."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    (tmp_path / "rolls").mkdir()
    folder = _walked_folder(tmp_path / "rolls", count=4)
    _roll_json(folder,
               {"number": 2, "done": True, "registration": {"approved": _MISSED}},
               {"number": 3, "done": True, "registration": {"approved": _OFF}},
               {"number": 4, "done": True, "registration": {"approved": _MISSED}})
    app.open_roll(folder)
    root.update()
    assert set(_badged(app)) == {2, 3, 4}
    app.sheet.top.destroy()
    _roll_json(folder,
               {"number": 2, "done": True, "registration": {"contrast": 0.3}},
               {"number": 3, "done": False, "error": "read timed out",
                "registration": {"approved": _OFF}},
               {"number": 4, "done": True, "registration": {"approved": _MISSED}})
    app.on_contact_sheet()
    root.update()
    assert set(_badged(app)) == {4} == set(gui.read_survey(folder)["holds"])
    app.sheet.top.destroy()


def test_a_roll_json_speaks_only_for_the_frames_it_records(tmp_path):
    """And only on the strip's numbering, as `done_in` reads it; otherwise
    what the roll said when it was opened stands."""
    kept = {1: _MISSED, 5: _UNVERIFIED}
    folder = tmp_path / "roll"
    folder.mkdir()
    assert gui.holds_in(folder, kept) == kept, "no roll.json"
    (folder / "roll.json").write_text(json.dumps(
        {"roll": "roll", "frames": [{"number": 1, "done": True}]}),
        encoding="utf-8")
    assert gui.holds_in(folder, kept) == kept, "numbered the old way"
    _roll_json(folder, {"number": 1, "done": True},
               {"number": 2, "done": True, "registration": {"approved": _OFF}})
    assert gui.holds_in(folder, kept) == {2: _OFF, 5: _UNVERIFIED}


def test_a_roll_from_elsewhere_is_marked_from_the_folder_it_is_scanned_into(
        window, tmp_path, monkeypatch):
    """A roll opened from outside this session's rolls is scanned into a
    folder here (`_roll_folder`), and its badges were read from the folder
    it came from, which nothing scans into: a frame the roll left off its
    position was marked only in the list at the roll's end, shown once."""
    import pathlib

    from rps7200.protocol import units

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    app.open_roll(folder)
    app.sheet.top.destroy()
    # Handed to no session (`_commission`), so none holds it: as if it had
    # run.
    _commission(app, monkeypatch, numbers=(2,))
    here = pathlib.Path(app.session.rolls) / folder.name
    _roll_json(here, {"number": 2, "done": True,
                      "registration": {"approved": _MISSED}})
    missed = {2: f"{units(2.41):.0f} units out"}
    app.on_contact_sheet()
    root.update()
    assert _badged(app) == missed
    app.sheet.top.destroy()
    # Opened again as `--open-roll` opens it, by a later window that knows
    # nothing of this one: the folder here holds this walk, so it is read.
    app._carried.clear()
    app.open_roll(folder)
    root.update()
    assert _badged(app) == missed
    app.sheet.top.destroy()


def test_reading_a_rolls_badges_never_chooses_where_it_is_scanned(
        window, tmp_path, monkeypatch):
    """The badges of a roll from elsewhere ask `_roll_folder` where its
    frames go. Had asking chosen, the folder of its name that was free when
    the roll was opened would have been kept once another roll had been
    written there, and this strip's frames and approvals added to that one's
    -- what `test_a_roll_from_elsewhere_is_not_added_to_another_of_its_name`
    pins for a folder taken before the roll was opened."""
    import pathlib

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    app.open_roll(folder)                                # badges read here
    app.sheet.top.destroy()
    app.on_contact_sheet()                               # and here
    app.sheet.top.destroy()
    other = pathlib.Path(app.session.rolls) / folder.name
    other.mkdir(parents=True)
    (other / "survey.json").write_text(json.dumps(
        {"roll": "another strip", "frames": [{"number": 9}]}), encoding="utf-8")
    (job,) = _commission(app, monkeypatch)
    assert job.out == str(other.with_name(f"{folder.name}-2"))
    assert not (other / "approved.json").exists()


def test_a_walks_own_holds_are_not_marked_as_a_rolls(window, tmp_path,
                                                      monkeypatch):
    """`tools/scan_roll.py` holds a walk's frames too -- ``--dry-run`` with
    approved positions -- and a walk it held comes back from survey.json with
    each hold. The filmstrip marked those prescans "not placed", as if a roll
    had missed them, while the sheet built on the same pictures, whose badges
    are roll.json's, marked none. Only a frame a roll scanned is marked."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    for record in survey["frames"]:
        record["registration"] = {"approved": dict(_UNVERIFIED)}
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    app.open_roll(folder)
    root.update()
    assert app.sheet._badges == {}
    assert not app.strip.find_withtag("hold")
    for result in app.survey:
        app._show(result)
        assert "not placed" not in app.v_caption.get()
    app.sheet.top.destroy()


def test_quitting_keeps_what_the_open_sheet_held(window, tmp_path,
                                                 monkeypatch):
    """For a walk not yet commissioned the sheet is the only record of the
    ticks, positions and turns, and quitting with it open lost them."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    folder = _walked_folder(tmp_path, count=3)
    app.open_roll(folder)
    sheet = app.sheet
    sheet._rotate(3, 180)
    app.on_close()
    assert not sheet.alive()
    assert app.remembered["sheet"]["walk"]["rotations"][3] == 180


def test_a_ctrl_c_in_the_terminal_quits_after_the_frame_in_flight(window,
                                                                  monkeypatch):
    """Ctrl-C in the terminal the window came from, SIGTERM, or that terminal
    closing ended the process at once: the scanner thread died inside its
    bulk read -- the abandoned read -- and the writer part way through an
    entry. It takes Quit's "stop after the frame in flight" now, asking no
    one, since whoever sent it may not be at the window."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("asked the operator"))
    stops, waits = [], []
    monkeypatch.setattr(app.session, "request_stop", lambda: stops.append(1))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: waits.append(1))
    app.busy = True
    app.on_interrupt()
    app.on_interrupt()                       # the second goes to the handler
    assert stops == [1] and waits == [1]
    assert app.closing


def test_a_closed_console_quits_after_the_frame_in_flight_and_says_so(
        window, monkeypatch):
    """Windows' console closing reaches no Python handler in time, and
    Windows ends the process about 5 s on whatever it is doing. It takes the
    same path as Ctrl-C, and the log says what those seconds can and cannot
    do."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("asked the operator"))
    stops, waits = [], []
    monkeypatch.setattr(app.session, "request_stop", lambda: stops.append(1))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: waits.append(1))
    app.busy = True
    app.on_interrupt(console_closed=True)
    assert stops == [1] and waits == [1] and app.closing
    said = app.log.get("1.0", "end")
    assert "console this window was started from was closed" in said
    assert "about 5 s" in said and "make run" in said
    assert "interrupted from the terminal" not in said


def test_a_console_closed_after_quit_let_the_queue_run_stops_it(window,
                                                                monkeypatch):
    """Quit's "No: let everything queued finish" asks no stop, and the close
    that followed asked none either: the roll went on -- the next SLIDE,
    prescan and scan all started -- until Windows ended the process part
    way through one of them, while the log said it was stopping. Whatever
    Quit was told, the close stops after the frame in flight: a pass that
    ends inside the 5 s leaves the device idle when the end comes."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: False)              # No: let it run
    stops, shutdowns = [], []
    real_shutdown = app.session.shutdown
    monkeypatch.setattr(app.session, "request_stop", lambda: stops.append(1))
    monkeypatch.setattr(app.session, "shutdown",
                        lambda: (shutdowns.append(1), real_shutdown()))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    app.busy = True
    app.on_close()
    assert app.closing and stops == [] and shutdowns == [1]
    app.on_interrupt(console_closed=True)
    assert stops == [1]
    assert shutdowns == [1], "it quits once"
    assert "was closed" in app.log.get("1.0", "end")


@pytest.mark.parametrize("console_closed", [True, False])
def test_a_job_handed_over_and_not_begun_is_not_the_frame_in_flight(
        window, monkeypatch, console_closed):
    """`busy` follows the worker's own report, read up to a tick after the job
    was taken, and the stop was asked only `if busy`: a Scan handed over in
    that tick started behind the shutdown -- with seconds to live, for a
    closed console, and run to its end before the quit for a Ctrl-C. Both
    are the session's halt now (`ScanSession.halt`): whatever it holds is
    dropped, the job it has taken is stopped before its first command, and
    none starts after -- tested on the session itself."""
    from rps7200.session import Scan

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)
    halts = []
    monkeypatch.setattr(app.session, "halt", lambda **kw: halts.append(kw))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    held = _Held(app, monkeypatch)
    app._hand_over(Scan())                       # handed over, start not read
    assert not app.busy and app._working()
    assert held.held
    app.on_interrupt(console_closed=console_closed)
    assert halts == [{"console_closed": console_closed}] and app.closing


@pytest.mark.parametrize("job", ["Scan", "Calibrate"])
def test_quit_asks_about_a_job_handed_over_and_not_begun(window, monkeypatch,
                                                         job):
    """Quit asked only `if busy`, and `busy` is read up to a tick after the
    worker took the job: a Quit inside that tick asked nothing and the
    whole job -- a roll, or a calibration -- ran behind the shutdown. It
    asks now, of the session's own count, and "stop after what is in
    flight" halts the session, which drops the job."""
    from rps7200 import session

    app, root = window
    asked, halts = [], []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(app.session, "halt", lambda **kw: halts.append(kw))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    held = _Held(app, monkeypatch)
    held.held.append(object.__new__(getattr(session, job)))
    assert not app.busy
    app.on_close()
    assert len(asked) == 1 and halts == [{"console_closed": False}]
    assert ("Yes: stop after what is in flight -- a roll's frame, or a "
            "scan's pass or metering probe -- then quit.") in asked[0]
    assert app.closing


def test_a_stop_for_the_window_alone_lets_the_next_job_run(window,
                                                           monkeypatch):
    """The Stop button is not quitting: a job pressed after it is the
    operator's next decision, and is not stopped as it starts -- the
    session is not halted."""
    from rps7200.session import Event

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)
    stops = []
    monkeypatch.setattr(app.session, "request_stop", lambda: stops.append(1))
    app.on_stop()
    app._handle(Event(kind="state", text="scanning", busy=True))
    assert stops == [1]
    assert not app.session.halted


def test_the_window_takes_a_terminal_interrupt_as_quit():
    """Only the wiring is checked here: `DeferredInterrupt` and `ConsoleClose`
    are tested on their own, and a window's mainloop is not something a test
    can signal. The console closing is checked on a real console, opt-in, in
    `test_console_close.py`. Its line goes to a file as well as stderr: the
    console it was said in is what closed."""
    source = inspect.getsource(gui.main)
    assert "DeferredInterrupt(" in source and "on_interrupt" in source
    assert "ConsoleClose(" in source and "app.on_interrupt, True" in source
    assert 'logged_to(home / "logs" / "console-closed.log")' in source
    # After the C runtime's handler, so Windows reaches it first.
    # And inside the window's own locks, which outlive both.
    assert "with held, interrupt, closed, kept_hooks(" in source


def test_a_closed_console_halts_the_session_without_waiting_for_tk(
        tmp_path, monkeypatch):
    """Windows calls `ConsoleClose`'s handler on a thread of its own, and the
    window's quit was handed to Tk's thread from there -- however long Tk
    took to get to it, with about 5 s to live. The session is halted from
    Windows' thread at once, before Tk's quit is so much as booked; Ctrl-C
    and SIGTERM still go through Tk, their handler running on its thread."""
    import sys
    import types

    calls = []

    class Session:
        def __init__(self, **kw):
            calls.append("session")
            self.edge_reader = None

        def halt(self, *, console_closed=False):
            calls.append(("halt", console_closed))

    class Root:
        def after(self, ms, *call):
            calls.append(("after",) + call[1:])

        def mainloop(self):
            closed["on_close"]()

    class Window:
        def __init__(self, root, session, **kwargs):
            self.on_interrupt = lambda *a: None

    closed: dict = {}

    class Close:
        def __init__(self, on_close=None, say=None):
            closed["on_close"] = on_close

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def closed(self):
            return True

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "tk", types.SimpleNamespace(Tk=Root))
    monkeypatch.setattr(gui, "ScannerGui", Window)
    monkeypatch.setattr(gui, "ScanSession", Session)
    monkeypatch.setattr(gui, "ConsoleClose", Close)
    monkeypatch.setattr(gui, "_claim_real_pixels", lambda: None)
    monkeypatch.setattr(gui, "hold_window", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["gui.py"])
    assert gui.main() == 130
    assert calls[1:] == [("halt", True), ("after", True)], calls


def test_a_scan_handed_over_and_closed_in_the_same_tick_never_scans(
        window, monkeypatch):
    """B1-T1, found by the demo-gui-tester: a Scan handed over and, in the
    same tick, the console closed. The worker had taken it and cleared the
    stop as it began, so the pass ran and was filed -- a read Windows ends
    about 5 s on. The real session and the demo's scanner: nothing scans,
    nothing is filed."""
    from rps7200 import library
    from rps7200.session import Scan

    app, root = window
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    app.calibrated = True
    job = Scan(resolution=600, infrared=False, auto_exposure=False)
    app._hand_over(job)
    # As `main`'s handler does it, from Windows' thread: no tick between.
    app.session.halt(console_closed=True)
    app.session.shutdown()
    events = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(
            e.kind == "closed" for e in events):
        events += app.session.poll()
        time.sleep(0.01)
    assert any(e.kind == "closed" for e in events)
    assert not [e for e in events if e.kind == "result"], \
        "a pass was taken after the console closed"
    # Whichever side of the worker's take the halt lands: taken, the job is
    # stopped before its first command; not yet -- the worker still opening
    # the scanner, on a loaded machine -- it is dropped. Asked for "stopped"
    # alone, this failed in a whole-suite run that dropped it.
    ended = ([e.text for e in events if e.kind == "finished"],
             [e.job for e in events if e.kind == "dropped"])
    assert ended in ((["stopped"], []), ([], [job])), ended
    assert library.entries(app.session.root) == []


def test_working_needs_no_tick_after_a_hand_over(window, monkeypatch):
    """`_working`, the Stop button and Escape all followed `busy`, a tick
    behind the worker; the session's own count knows a job from its
    hand-over. A calibration alone outstanding still lets a pass queue
    behind it, as it is meant to."""
    from rps7200.session import Calibrate, Scan

    app, root = window
    stops = []
    held = _Held(app, monkeypatch)
    monkeypatch.setattr(app, "on_stop", lambda: stops.append(1))
    app._hand_over(Calibrate(mode="measure"))
    assert not app._working(), "a pass may queue behind a calibration"
    held.held.clear()
    app._hand_over(Scan())
    assert not app.busy and app._working()
    assert str(app.b_stop.cget("state")) == "normal"
    app._actions()["stop"]()
    assert stops == [1]


def test_the_window_tests_never_write_the_checkouts_settings(window, tmp_path,
                                                            monkeypatch):
    """Opening a roll and quitting both save, and the window fixture used to
    save into ``./gui-settings.json`` -- the file the operator's next launch
    restores its controls, rolls and uncommissioned sheets from. It was found
    holding this file's test folders. The save has to land in the test's own
    file, and the checkout's must come out byte for byte as it went in."""
    from pathlib import Path

    from rps7200 import settings

    app, root = window
    checkout = Path(settings.DEFAULT_PATH).resolve()
    before = checkout.read_bytes() if checkout.exists() else None
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    app.open_roll(_walked_folder(tmp_path, count=3))
    app.on_close()

    assert "walk" in settings.load(tmp_path / "gui-settings.json")["rolls"]
    after = checkout.read_bytes() if checkout.exists() else None
    assert after == before, f"{checkout} was written by a test"


def test_reset_in_the_big_view_puts_that_frame_back_and_no_other(window, tmp_path):
    app, root = window
    sheet, offsets, notes = _sheet_with_readings(app, tmp_path)
    sheet.take_readings(offsets, notes)
    detected = dict(sheet.offsets)
    sheet.adjust(0)
    root.update()
    adj = sheet._adjuster
    adj._set(0.9)
    adj._go(1)
    adj._set(0.9)
    assert sheet.proposals[1]["source"] == sheet.proposals[2]["source"] == "operator"
    adj._reset()
    assert sheet.offsets[2] == detected[2]
    assert sheet.proposals[2]["source"] == "measured"
    assert sheet.proposals[1]["source"] == "operator", "the other frame keeps his"
    sheet.top.destroy()


def test_reset_positions_puts_the_whole_roll_back_after_asking(
        window, tmp_path, monkeypatch):
    app, root = window
    sheet, offsets, notes = _sheet_with_readings(app, tmp_path,
                                                 kept={1: 0.9, 3: -0.9})
    sheet.take_readings(offsets, notes)
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno",
                        lambda title, message, **k: asked.append(message) or False)
    sheet.reset_all()
    assert "1, 3" in asked[0]
    assert sheet.offsets[1] == 0.9, "no is no"
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    sheet.reset_all()
    snapped, _ = gui._snap_proposals(offsets, notes)
    assert sheet.offsets == snapped
    assert all(v["source"] != "operator" for v in sheet.proposals.values())
    sheet.top.destroy()


def test_a_walk_is_read_while_it_is_walked(window, monkeypatch):
    """The sheet a walk opens by itself takes the reader's answers; the reader
    started on the first prescan, not when the sheet was asked for."""
    from tools import frame_edges

    app, root = window
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.calibrated = True
    app.v_last.set("3")
    app.v_startat.set("1")
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    added = []
    real_add = app.edge_watch.add
    monkeypatch.setattr(app.edge_watch, "add",
                        lambda n, im: added.append((n, app.sheet)) or real_add(n, im))
    app.on_roll()
    statuses = set()
    deadline = time.monotonic() + 60
    while app.sheet is None and time.monotonic() < deadline:
        root.update()
        statuses.add(app.v_frame_status.get())
        time.sleep(0.01)
    assert app.sheet is not None, "the walk never opened its sheet"
    assert [n for n, _ in added] == [1, 2, 3]
    assert all(sheet is None for _, sheet in added), "read before any sheet"
    assert "frame 1 of 3" in statuses
    progress = _settle(app, root)
    assert progress.state == frame_edges.DONE and progress.done == 3
    assert app.sheet.generation == progress.generation
    assert app.v_frame_status.get() == ""
    app.sheet.top.destroy()


# -- first frame to last frame, and a walk that adds to the sheet -------------


def test_the_roll_panel_takes_a_range_with_both_ends_included():
    """Stefan: "1-20 means frame 1 and all frames up to 20 and frame 20", and
    an empty last frame is the end of the strip."""
    assert gui.frame_range("1", "20") == (1, 20)
    assert gui.frame_range("11", "12") == (11, 2)
    assert gui.frame_range("7", "7") == (7, 1)
    assert gui.frame_range("11", "") == (11, None)
    assert gui.frame_range(" ", " ") == (1, None)
    for first, last in (("5", "3"), ("0", ""), ("x", ""), ("1", "y"),
                        ("1", "101")):
        with pytest.raises(ValueError):
            gui.frame_range(first, last)


def test_the_range_is_said_the_way_it_was_typed():
    assert gui.range_words(1, 20) == "frames 1 to 20"
    assert gui.range_words(7, 1) == "frame 7"
    assert gui.range_words(11, None) == (
        "every frame from frame 11 to the end of the strip")
    assert gui.number_spans([3, 1, 2, 5, 7, 8]) == "1-3, 5, 7-8"
    assert gui.rewalked([1, 2, 3, 9, 10], 9, None) == [9, 10]
    assert gui.rewalked([1, 2, 3], 4, 2) == []
    assert gui.rewalked([1, 2, 3], 2, 1) == [2]


def test_a_reopened_roll_puts_its_range_back_as_a_range():
    """A `Roll` records a start and a count; the panel asks for a first and a
    last frame. Null is a roll that ran to the end, which an empty box says;
    a roll that recorded nothing leaves the box alone."""
    assert gui.restorable({"start_at": 11, "frames": 2})["last"] == "12"
    assert gui.restorable({"start_at": 1, "frames": None})["last"] == ""
    assert "last" not in gui.restorable({"start_at": 4})
    assert "frames" not in gui.RESTORABLE.values()


def test_an_old_remembered_count_is_not_read_as_a_last_frame():
    """`frames` held a count. The box that replaced it is remembered under
    another name, so a "6" from before is not "last frame 6"."""
    assert "frames" not in gui.REMEMBERED and "last" in gui.REMEMBERED
    assert gui.PANEL_CONTROLS["Roll"][:2] == ("startat", "last")


def test_each_reopened_prescan_is_unturned_by_its_own_record(tmp_path):
    """Two walks merged into one survey: the first made at 90 degrees, the
    frame added later at 0. The manifest's one pair un-turned the new frame
    by 90 it was never turned by."""
    from rps7200 import preview, tiff

    images = _write_survey(tmp_path / "roll", rotation=90)
    tiff.write(str(tmp_path / "roll" / "prescan03.tif"),
               preview.orient(images[3], 0, False))
    manifest = json.loads((tmp_path / "roll" / "survey.json").read_text(
        encoding="utf-8"))
    manifest["frames"][2].update(prescan_rotation=0, prescan_flipped=False)
    (tmp_path / "roll" / "survey.json").write_text(json.dumps(manifest),
                                                    encoding="utf-8")
    out = gui.read_survey(tmp_path / "roll")
    for result in out["results"]:
        assert np.array_equal(result.image, images[result.number]), result.number
    assert [r.rotation for r in out["results"]] == [90, 90, 0]


def _walk_through(app, root, monkeypatch, first, last, keep=True,
                  seconds=60.0):
    """Press Scan roll for a walk of this range and wait for its sheet.

    Returns what the keep question said, or None when it was not asked."""
    asked = []
    # A modal nobody answers would hang the run rather than fail it.
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: pytest.fail(f"an error dialog: {a}"))
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda title, message, **k: asked.append(message) or keep)
    app.v_startat.set(first)
    app.v_last.set(last)
    app.on_roll()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if (not app._surveying and not app.busy and app.sheet is not None
                and app.sheet.alive()):
            break
        time.sleep(0.01)
    assert app.sheet is not None and app.sheet.alive(), "no sheet opened"
    return asked[0] if asked else None


def test_a_second_walk_adds_to_the_sheet_when_asked(window, monkeypatch):
    """Stefan: walk 1 to 3, find the strip is longer, walk 4 on -- and keep
    the sheet. The new frames join it, what was decided on the old stays, and
    `survey.json` holds every frame so the roll reopens whole."""
    import pathlib

    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    assert _walk_through(app, root, monkeypatch, "1", "3") is None, \
        "nothing to keep yet"
    first_sheet = app.sheet
    first_sheet._rotate(2, 90)
    turned = first_sheet.rotations[2]
    first_sheet.ticks[3].set(False)
    folder = app._sheet_roll

    asked = _walk_through(app, root, monkeypatch, "4", "5")
    assert "frames 1-3" in asked and "Keep it?" in asked
    assert not first_sheet.alive(), "the old sheet was closed, not left behind"
    assert app._sheet_roll == folder
    assert [r.number for r in app.survey] == [1, 2, 3, 4, 5]
    sheet = app.sheet
    assert [r.number for r in sheet.frames] == [1, 2, 3, 4, 5]
    assert sheet.rotations.get(2) == turned, "a turn made on the old sheet"
    assert sheet.ticks[3].get() is False, "and an untick"
    assert sheet.ticks[4].get() and sheet.ticks[5].get(), "new frames ticked"
    progress = _settle(app, root)
    assert progress.done == 5 and sheet.generation == progress.generation
    manifest = json.loads((pathlib.Path(folder) / "survey.json").read_text(
        encoding="utf-8"))
    assert [f["number"] for f in manifest["frames"]] == [1, 2, 3, 4, 5]
    sheet.top.destroy()


def test_a_second_walk_starts_a_new_sheet_when_told_to(window, monkeypatch):
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    _walk_through(app, root, monkeypatch, "1", "2")
    _walk_through(app, root, monkeypatch, "3", "4", keep=False)
    assert [r.number for r in app.survey] == [3, 4]
    assert [r.number for r in app.sheet.frames] == [3, 4]
    app.sheet.top.destroy()


def test_cancelling_the_keep_question_moves_nothing(window, monkeypatch):
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    _walk_through(app, root, monkeypatch, "1", "2")
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: None)
    app.v_startat.set("3")
    app.v_last.set("4")
    app.on_roll()
    assert jobs == [] and [r.number for r in app.survey] == [1, 2]
    assert app.sheet.alive(), "and the sheet is left as it was"
    app.sheet.top.destroy()


def test_a_frame_walked_again_loses_the_position_set_on_its_old_prescan(
        window, monkeypatch):
    """An offset is measured from where the frame was surveyed. Walked again,
    that prescan is replaced and the number says nothing; its turn is about
    the picture and stays."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    _walk_through(app, root, monkeypatch, "1", "2")
    app.sheet.offsets[1] = 3.0
    app.sheet.offsets[2] = 3.0
    app.sheet.proposals[2] = {"source": "operator"}
    app.sheet._rotate(2, 180)
    turned = app.sheet.rotations[2]
    asked = _walk_through(app, root, monkeypatch, "2", "3")
    assert "Frame 2 is on it already" in asked
    kept = app._recall_sheet_state()
    assert kept["offsets"].get(1) == 3.0, "a frame not walked again keeps it"
    # His position on the old prescan is gone. The reader's own word on the
    # new one may be filed by now -- the sheet files as it reads -- and is
    # never his.
    assert kept["offsets"].get(2) != 3.0
    assert kept["sources"].get(2) != "operator"
    assert kept["rotations"].get(2) == turned
    assert [r.number for r in app.survey] == [1, 2, 3]
    app.sheet.top.destroy()


def test_a_sheet_walked_another_way_is_not_added_to(window, monkeypatch):
    """Frames prescanned at another resolution would be references the hold
    loop scores at half the confidence, beside frames that are not."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.v_predpi.set("300")
    _walk_through(app, root, monkeypatch, "1", "2")
    app.v_predpi.set("600")
    said, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("offered"))
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or True)
    app.v_startat.set("3")
    app.v_last.set("4")
    app.on_roll()
    assert "cannot be added" in said[0] and "300 dpi" in said[0]
    assert [job.extend_walk for job in jobs] == [False]
    assert app.survey == [], "a new sheet, as OK said"


@pytest.mark.parametrize(("dry", "correct", "predpi", "warned"), [
    (True, False, "600", True),        # the sheet's positions come from it
    (True, False, "300", False),
    # "correct" at 900 dpi is refused rather than warned about now; see
    # test_aiming_at_a_prescan_the_edges_cannot_be_read_at_is_refused.
    (False, False, "600", False),      # nothing reads edges on this roll
])
def test_a_prescan_the_edges_are_not_read_at_is_said_before_the_walk(
        window, monkeypatch, dry, correct, predpi, warned):
    """At 600 or 900 dpi the device's prescans are 860 and 1292 columns and
    the detector refuses every frame: the edge light went green, every
    caption said refused, and a roll with "correct" on looked centred and
    was not. Said in the question that starts it, and in the log -- and
    still started, since the walk is a survey of the strip either way."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(dry)
    app.v_correct.set(correct)
    app.v_film.set("negative")
    app.v_predpi.set(predpi)
    app.v_startat.set("1")
    app.v_last.set("2")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    app.on_roll()
    said = f"not read at a {predpi} dpi prescan"
    assert (said in asked[0]) is warned, asked[0]
    assert (said in app.log.get("1.0", "end")) is warned
    assert [job.prescan_resolution for job in jobs] == [int(predpi)]


def test_a_fresh_walk_closes_the_sheet_of_the_last_one(window, monkeypatch):
    """It used to stay open, and the end of the new walk raised it again --
    showing the old frames, and filing them under the new roll on close."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    _walk_through(app, root, monkeypatch, "1", "2")
    old = app.sheet
    _walk_through(app, root, monkeypatch, "1", "3", keep=False)
    assert not old.alive() and app.sheet is not old
    assert [r.number for r in app.sheet.frames] == [1, 2, 3]
    app.sheet.top.destroy()


# -- one roll, one folder ----------------------------------------------------


def test_an_unnamed_strip_gets_a_name_of_its_own_and_keeps_it(window,
                                                              monkeypatch):
    """With the roll box empty, every walk and roll of a day went to
    rolls/<date>, the sheet's decisions to rolls/roll, and a second strip
    replaced the first one's walk. Now the walk makes a roll of its own --
    strip 1 of it, by a dated name the box shows -- and the commission
    scans into the walk's own folder beside its decisions."""
    import pathlib

    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    assert app.fields["roll"].get() == ""
    _walk_through(app, root, monkeypatch, "1", "2")
    first = pathlib.Path(app._sheet_roll)
    assert first.name == "strip-01"
    assert first.parent.parent == pathlib.Path(app.session.rolls)
    assert app.fields["roll"].get() == first.parent.name, (
        "the roll's name is shown to the operator, never the strip's")

    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.sheet._scan()
    assert jobs[0].out == str(first)
    assert (first / "approved.json").exists()
    assert (first / "survey.json").exists()
    assert not (pathlib.Path(app.session.rolls) / "roll").exists()


def test_a_second_strip_walked_is_the_rolls_next_strip(window, monkeypatch):
    """Stefan's roll model: a film is one roll, its strips walked in order.
    A fresh walk with the roll in the box -- No to keeping the sheet -- is
    its next strip, numbered on from the frames the first really had; the
    first strip's walk is left as it was. New roll starts another roll."""
    import pathlib

    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    _walk_through(app, root, monkeypatch, "1", "2")
    first = pathlib.Path(app._sheet_roll)
    name = first.parent.name
    assert (f"strip 1 of {name} walked: frames 1-2, its walk went as far as "
            f"frame 2; the next walk under {name} adds to it, or starts strip "
            "2 from 3") in app.log.get("1.0", "end")
    asked = _walk_through(app, root, monkeypatch, "1", "3", keep=False)
    assert "strip 2 of" in asked and "numbered from 3" in asked, asked
    second = pathlib.Path(app._sheet_roll)
    assert second == first.parent / "strip-02"
    assert app.fields["roll"].get() == first.parent.name
    walked = json.loads((first / "survey.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in walked["frames"]] == [1, 2], "left alone"
    index = json.loads((first.parent / "strips.json").read_text(
        encoding="utf-8"))
    assert [(s["strip"], s["first"], s["last"]) for s in index["strips"]] == [
        (1, 1, 2), (2, 3, None)]
    # Walked over frames 1 to 3 only, strip 2 is not known to be complete:
    # the next walk is offered to it first.
    app._offer_strip_now()
    assert app.v_strip_note.get() == (
        f"{name}: strip 2's walk went as far as frame 3 -- the next walk adds "
        "to it, or starts strip 3 from 6")
    app.on_new_roll()
    assert app.fields["roll"].get() == ""
    _walk_through(app, root, monkeypatch, "1", "2", keep=False)
    third = pathlib.Path(app._sheet_roll)
    assert third.name == "strip-01" and third.parent != first.parent
    app.sheet.top.destroy()


def test_a_roll_from_before_strips_typed_for_a_walk_starts_a_roll_beside_it(
        window, tmp_path, monkeypatch):
    """A folder from before strips is one strip and takes no second: a walk
    typed into it starts a roll of its own, <name>-2, says why, and shows
    that name as the window's own so the next walk goes on with it. A plain
    roll typed into it still resumes it, as it always did."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    rolls = tmp_path / "rolls"
    rolls.mkdir(exist_ok=True)
    legacy = _walked_folder(rolls, count=3)          # rolls/walk
    app.fields["roll"].set("walk")
    said, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or True)
    app.v_startat.set("1")
    app.v_last.set("3")
    app.on_roll()
    assert ("rolls/walk is one strip from before rolls held strips and takes "
            "no second strip: this walk starts a new roll, rolls/walk-2"
            in said[0]), said[0]
    (job,) = jobs
    assert job.out == str(rolls / "walk-2") and job.new_strip
    assert job.strip_plan == (1, 1) and not job.extend_walk
    assert app.fields["roll"].get() == "walk-2"
    assert app._roll_named == "walk-2"
    # A plain roll typed into it resumes it.
    app.v_dryrun.set(False)
    app.fields["roll"].set("walk")
    said.clear()
    jobs.clear()
    app.on_roll()
    (job,) = jobs
    assert job.out == str(legacy) and not job.new_strip
    assert "Into rolls/walk, which already holds a walk" in said[0], said[0]


def test_a_long_name_typed_for_a_walk_is_said_and_recorded(window,
                                                          monkeypatch):
    """The window works a roll's folder out itself and handed the session
    that folder alone, so a name cut to its cap was neither said in the log
    nor kept in survey.json -- on the path every walk and sheet takes. Only
    a session given the name itself did either."""
    from pathlib import Path

    from rps7200 import session as session_mod

    app, root = window
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    typed = "Holiday in the mountains with the whole family " * 3
    app.fields["roll"].set(typed)
    _walk_through(app, root, monkeypatch, "1", "2")
    folder = Path(app.session.rolls) / session_mod._safe(typed)
    survey = json.loads((folder / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    assert survey["roll"] == folder.name
    assert survey["typed_name"] == typed.strip()
    index = json.loads((folder / "strips.json").read_text(encoding="utf-8"))
    assert index["typed_name"] == typed.strip()
    assert (f"its folder and files are called {folder.name}"
            in app.log.get("1.0", "end"))
    app.sheet.top.destroy()


def test_a_reopened_roll_is_continued_in_its_own_folder(window, tmp_path,
                                                        monkeypatch):
    """Its name was never put back in the roll box, so "continue" scanned
    into rolls/<today> beside it."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    (tmp_path / "rolls").mkdir()
    folder = _walked_folder(tmp_path / "rolls", count=3)
    app.open_roll(folder)
    assert app.fields["roll"].get() == folder.name
    assert app._next_roll_folder(fresh=False) == folder
    assert app._roll_folder() == folder
    assert app._next_roll_folder(fresh=True) != folder, (
        "a fresh walk is another strip, not a walk over this one")
    app.sheet.top.destroy()


def test_a_roll_opened_from_elsewhere_is_never_scanned_back_into(window,
                                                                 tmp_path,
                                                                 monkeypatch):
    """`--open-roll` under `--demo` shows a real walk; what the demo then
    scans goes under its own rolls folder, beside nothing real."""
    import pathlib

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    app.open_roll(folder)
    assert app._roll_folder() == pathlib.Path(app.session.rolls) / folder.name
    app.sheet.top.destroy()


def test_a_walk_added_to_a_roll_opened_from_elsewhere_stays_here(window,
                                                                 tmp_path,
                                                                 monkeypatch):
    import pathlib

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    app.open_roll(folder)
    app.calibrated = True
    app.v_dryrun.set(True)
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.v_startat.set("4")
    app.v_last.set("5")
    app.on_roll()
    assert jobs[0].extend_walk
    assert jobs[0].out == str(pathlib.Path(app.session.rolls) / folder.name)


def test_a_walk_added_to_a_roll_opened_from_elsewhere_takes_that_walk_along(
        window, tmp_path, monkeypatch):
    """The walk goes into the folder of the same name here, and the session
    carries forward the survey.json it finds there -- none. So the new survey
    listed only the frames just walked, while the sheet covered the strip,
    and the folder reopened as a partial sheet."""
    import pathlib

    from conftest import StripScanner
    from rps7200.session import ScanSession

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    before = sorted((p.name, p.read_bytes()) for p in folder.iterdir())
    app.open_roll(folder)
    app.calibrated = True
    app.v_dryrun.set(True)
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.v_startat.set("4")
    app.v_last.set("5")
    app.on_roll()
    here = pathlib.Path(app.session.rolls) / folder.name
    assert sorted(p.name for p in here.glob("prescan*.tif")) == [
        "prescan01.tif", "prescan02.tif", "prescan03.tif"]

    # The walk itself, run by the session the window hands it to.
    walked = ScanSession(root=str(tmp_path / "library"),
                         rolls=app.session.rolls, verbose=False,
                         open_scanner=lambda: StripScanner(at=3, last=5))
    walked.start()
    walked.submit(jobs[0])
    walked.shutdown()
    walked.join(timeout=10)
    survey = json.loads((here / "survey.json").read_text(encoding="utf-8"))
    assert sorted(f["number"] for f in survey["frames"]) == [1, 2, 3, 4, 5]
    assert all((here / f["prescan"]).exists() for f in survey["frames"])
    assert sorted((p.name, p.read_bytes()) for p in folder.iterdir()) \
        == before, "the walk it was opened from is left as it was"


def _commission(app, monkeypatch, numbers=(1, 2)):
    """Press "Scan chosen frames" and say yes, with nothing reaching a device."""
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.calibrated = True
    app.on_scan_chosen(numbers)
    return jobs


def test_a_roll_opened_from_elsewhere_is_commissioned_with_its_walk(
        window, tmp_path, monkeypatch):
    """Only a walk added to it carried the walk across; a commission wrote
    approved.json and the frames into the folder here with no survey, and it
    reopened as a roll "scanned without walking the strip first"."""
    import pathlib

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    app.open_roll(folder)
    app.sheet.top.destroy()
    jobs = _commission(app, monkeypatch)
    here = pathlib.Path(app.session.rolls) / folder.name
    assert jobs[0].out == str(here)
    assert gui.same_walk(folder, here)
    assert sorted(p.name for p in here.glob("prescan*.tif")) == [
        "prescan01.tif", "prescan02.tif", "prescan03.tif"]


def test_a_roll_from_elsewhere_is_not_added_to_another_of_its_name(
        window, tmp_path, monkeypatch):
    """Two rolls from different roots can share a name -- date-named folders
    do -- and the folder of that name here was taken whatever it held: this
    strip's approvals were merged into that one's and its frames written over
    that one's frameNN.tif."""
    import pathlib

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)           # not under rolls/
    other = pathlib.Path(app.session.rolls) / folder.name
    other.mkdir(parents=True)
    (other / "survey.json").write_text(json.dumps(
        {"roll": "another strip", "frames": [{"number": 9}]}), encoding="utf-8")
    app.open_roll(folder)
    app.sheet.top.destroy()
    jobs = _commission(app, monkeypatch)
    ours = other.with_name(f"{folder.name}-2")
    assert jobs[0].out == str(ours)
    assert not (other / "approved.json").exists()
    assert gui.same_walk(folder, ours)
    # The same folder for the rest of the session, however its survey grows.
    (ours / "survey.json").write_text('{"frames": [1]}', encoding="utf-8")
    assert app._roll_folder() == ours


def test_a_turn_while_a_roll_runs_says_it_does_not_reach_that_roll(
        window, tmp_path, monkeypatch):
    """The sheet can be reopened while its roll runs, and a turn there
    showed the arriving frame upright while the roll wrote it as it was
    commissioned. Said, rather than left to be found in the files."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(_walked_folder(tmp_path, count=3))
    app.sheet._one(2, 90)
    assert "not for a roll running now" not in app.log.get("1.0", "end")
    app.busy = True
    app.sheet._one(3, 90)
    assert "not for a roll running now" in app.log.get("1.0", "end")
    app.busy = False
    app.sheet.top.destroy()


def test_the_sheet_stays_open_until_its_roll_is_handed_over(window, tmp_path,
                                                             monkeypatch):
    """It closed before the question, so a Cancel -- or a busy scanner, or a
    calibration still to make -- left it gone, under a message saying to
    press its button again."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(_walked_folder(tmp_path, count=3))
    sheet = app.sheet
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: False)
    sheet._scan()
    assert sheet.alive() and jobs == []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    sheet._scan()
    assert not sheet.alive() and len(jobs) == 1


def test_carrying_a_walk_never_writes_over_one_or_follows_a_path(tmp_path):
    source = _walked_folder(tmp_path, count=2)
    survey = json.loads((source / "survey.json").read_text(encoding="utf-8"))
    survey["frames"][0]["prescan"] = "../elsewhere.tif"
    (tmp_path / "elsewhere.tif").write_bytes(b"not the walk's")
    (source / "survey.json").write_text(json.dumps(survey), encoding="utf-8")

    target = tmp_path / "rolls" / "walk"
    assert gui.carry_walk(source, target) == ["prescan02.tif", "survey.json"]
    assert not (target / "elsewhere.tif").exists()
    assert json.loads((target / "survey.json").read_text(encoding="utf-8")) \
        == survey

    # A folder that has a walk of its own keeps it.
    (target / "survey.json").write_text('{"frames": []}', encoding="utf-8")
    assert gui.carry_walk(source, target) == []
    assert (target / "survey.json").read_text(encoding="utf-8") \
        == '{"frames": []}'
    assert gui.carry_walk(source, source) == []


def test_a_carried_prescan_is_whole_on_the_disk_before_it_has_its_name(
        tmp_path, monkeypatch):
    """Copied straight to its name, a copy cut short sat under the name
    survey.json lists and was read back as a hold's reference. Each goes
    beside, is synced and renamed in; one that fails leaves neither its
    name nor what was written beside, and no survey.json naming it."""
    source = _walked_folder(tmp_path, count=3)
    target = tmp_path / "rolls" / "walk"
    order = []
    real_copy, real_sync = gui.shutil.copyfile, gui.library._sync
    real_replace = gui.library._replace

    def copy(src, dst, *a, **k):
        if gui.Path(src).name == "prescan02.tif":
            gui.Path(dst).write_bytes(gui.Path(src).read_bytes()[:10])
            raise OSError(28, "No space left on device")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(gui.shutil, "copyfile", copy)
    monkeypatch.setattr(gui.library, "_sync", lambda p: (
        order.append(("sync", gui.Path(p).name)), real_sync(p))[-1])
    monkeypatch.setattr(gui.library, "_replace", lambda t, p: (
        order.append(("replace", gui.Path(t).name, gui.Path(p).name)),
        real_replace(t, p))[-1])
    with pytest.raises(OSError, match="No space left"):
        gui.carry_walk(source, target)
    assert order == [("sync", ".prescan01.tif.part"),
                     ("replace", ".prescan01.tif.part", "prescan01.tif")]
    assert sorted(p.name for p in target.iterdir()) == ["prescan01.tif"]
    assert (target / "prescan01.tif").read_bytes() == \
        (source / "prescan01.tif").read_bytes()


def test_the_folder_a_roll_goes_into_is_said_before_it_starts(tmp_path):
    new = gui.folder_note(tmp_path / "rolls" / "fresh", dry=True)
    assert new == "Into rolls/fresh, a new roll."
    folder = tmp_path / "rolls" / "old"
    folder.mkdir(parents=True)
    (folder / "survey.json").write_text(json.dumps(
        {"numbering": "strip", "frames": [{"number": 1}, {"number": 2}]}),
        encoding="utf-8")
    (folder / "roll.json").write_text(json.dumps(
        {"numbering": "strip", "frames": [{"number": 2, "done": True}]}),
        encoding="utf-8")
    walk = gui.folder_note(folder, dry=True)
    assert "a walk of frames 1-2 and 1 scanned frame" in walk
    assert "replaces its walk" in walk
    assert "adds its frames" in gui.folder_note(folder, dry=False)

    # The walk's frames, not the ones asked for: a sheet that asked for 2
    # and 4 of a walk of six made it "a walk of frames 2, 4".
    chosen = tmp_path / "rolls" / "chosen"
    chosen.mkdir()
    (chosen / "survey.json").write_text(json.dumps(
        {"numbering": "strip",
         "frames": [{"number": n} for n in range(1, 7)]}), encoding="utf-8")
    (chosen / "roll.json").write_text(json.dumps(
        {"numbering": "strip", "wanted": [2, 4],
         "frames": [{"number": 2, "done": True}]}), encoding="utf-8")
    assert "a walk of frames 1-6 and 1 scanned frame" in gui.folder_note(
        chosen, dry=False)


# -- reopened frames ------------------------------------------------------------


def test_reopened_frames_never_share_a_sequence_number(tmp_path):
    """They were ``-number``: two rolls reopened, or one opened twice, gave
    two frames one number, and the full-resolution view and Delete took the
    wrong one."""
    folder = _walked_folder(tmp_path, count=3)
    seqs = [r.seq for _ in range(2)
            for r in gui.read_survey(folder)["results"]]
    assert len(set(seqs)) == 6 and all(seq < 0 for seq in seqs)


def _reopened_with_entry(app, entry, seq=-99):
    from rps7200.session import Result

    result = Result(seq=seq, kind="prescan", label="frame 3 (reopened)",
                    image=np.zeros((4, 6, 3), np.uint8), meta={},
                    entry=entry, number=3)
    result.hidden, result.supersedes = False, None
    result.rotation, result.flipped = 0, False
    app.results.append(result)
    return result


def _a_library_entry(root, seed=1, **meta):
    """A small pass filed into ``root`` as `library.save` files one."""
    from rps7200 import library

    image = np.random.default_rng(seed).integers(0, 65535, (4, 6, 3),
                                                 dtype=np.uint16)
    return library.save(image, {"resolution_dpi": 300, "channels": 3, **meta},
                        root=root)


def test_the_full_view_says_when_the_scan_could_not_be_corrected(
        window, monkeypatch, tmp_path):
    """`library.corrected` hands an entry it cannot correct back raw and says
    so; the 1:1 view threw that away and showed the striped frame as "the
    scan's own pixels" -- the one thing the library contract says a person is
    never shown without a word."""
    app, root = window
    entry = tmp_path / "old-entry"
    entry.mkdir()
    monkeypatch.setattr(gui.library, "load", lambda e: (
        np.zeros((8, 12, 3), np.uint16), {}))
    monkeypatch.setattr(gui.library, "correct", lambda image, record: (
        image, {"corrected": "no reference"}))
    result = _reopened_with_entry(app, entry)
    app._show(result)
    deadline = time.monotonic() + 10
    while "own 12x8 pixels" not in app.log.get("1.0", "end") \
            and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    shown = app.log.get("1.0", "end")
    assert "own 12x8 pixels -- UNCORRECTED (no reference)" in shown


@pytest.mark.parametrize("demo", [True, False])
def test_a_demo_never_deletes_an_entry_that_is_not_its_own(window, tmp_path,
                                                           monkeypatch, demo):
    """`make run-sheet` opens a real walk, whose frames carry the entries it
    filed. Delete on one, and No to "keep the entry?", removed a real entry's
    raw bytes under the one mode promised to touch nothing real.

    And the same without the demo: the refusal is the window's rule about
    its own library, not an `if demo:` -- the demo exercises what runs."""
    app, root = window
    app.demo = demo
    entry = tmp_path / "real-library" / "an-entry"
    entry.mkdir(parents=True)
    (entry / "scan.tif").write_bytes(b"raw")
    result = _reopened_with_entry(app, entry)
    said = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("offered to delete it"))
    app.on_delete(result)
    assert (entry / "scan.tif").exists()
    assert "not in this session's library" in said[0]
    assert result not in app.results, "it still leaves the window"


def test_deleting_a_reopened_frames_entry_says_what_it_is_first(window,
                                                                monkeypatch):
    import pathlib

    app, root = window
    entry = _a_library_entry(pathlib.Path(app.session.root))
    result = _reopened_with_entry(app, entry)
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or None)
    app.on_delete(result)
    assert "the one its walk filed" in asked[0]
    assert entry.exists() and result in app.results, "Cancel is cancel"


def test_deleting_a_pass_keeps_its_entry_unless_asked_twice(window,
                                                            monkeypatch):
    """"Keep the library entry?" under a title of "Delete": read by the
    title, No was the natural answer, and it rmtree'd the only raw copy in
    one click. No keeps it now, and moving it to the trash takes a second,
    separately worded yes.

    Even while the scanner works: a move into the trash is a rename on the
    library's disk, and an entry filed is moved at once. It was refused
    then, and a Delete mid-roll was "try again later" for hours."""
    import pathlib

    from rps7200 import library

    app, root = window
    session_root = pathlib.Path(app.session.root)
    entry = _a_library_entry(session_root)
    answers, said = {"also": False, "sure": False}, []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: answers["also"])
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or answers["sure"])

    result = _reopened_with_entry(app, entry)
    app.on_delete(result)                        # No
    assert entry.exists() and result not in app.results

    answers["also"] = True                       # Yes, then not sure
    result = _reopened_with_entry(app, entry)
    app.on_delete(result)
    assert entry.exists() and result in app.results
    assert "are not freed until the trash is emptied" in said[-1]

    app.busy = True                              # sure, and mid-roll
    answers["sure"] = True
    app.on_delete(result)
    app.busy = False
    assert not entry.exists() and result not in app.results
    (batch,) = library.trash_batches(session_root)
    assert batch.path.parent == session_root / library.TRASH
    assert batch.names == [entry.name] and batch.record["by"] == "the demo window"
    assert not list(session_root.glob(f"{entry.name}*")), "nothing half-moved"
    assert not [p.name for p in session_root.iterdir()
                if p.is_dir() and not p.name.startswith(".")]
    assert not list(session_root.glob("*.deleting"))
    said = app.log.get("1.0", "end")
    assert f"moved {entry.name} to the trash" in said
    assert f"restore {batch.name}" in said


@pytest.mark.parametrize("keep", [True, False])
def test_keep_the_library_entry_means_what_it_says(window, monkeypatch, keep):
    """The one question in the window whose wrong answer loses raw bytes.
    Only Cancel and the entry-outside-the-library case were tested, so a
    change that made one answer do the other's work would have gone
    unnoticed. The question is "move its library entry to the trash as
    well?": No keeps it; Yes, confirmed, moves it and takes it out of the
    index; either way the frame leaves the window."""
    import pathlib

    from rps7200 import library

    app, root = window
    session_root = pathlib.Path(app.session.root)
    entry = library.save(np.zeros((4, 6, 3), np.uint16),
                         {"resolution_dpi": 300, "channels": 3},
                         root=session_root)
    result = _reopened_with_entry(app, entry)
    asked, confirmed = [], []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or not keep)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: confirmed.append(m) or True)
    app.on_delete(result)

    assert "Move its library entry to the library's trash as well?" in asked[0]
    assert "stays whole there until the trash is emptied" in asked[0]
    assert "keeps the entry" in asked[0]
    # Moving it is asked again, in its own words; keeping it is not.
    assert bool(confirmed) is not keep
    assert entry.exists() is keep
    assert (library.in_trash(session_root, entry.name) is None) is keep
    assert result not in app.results
    indexed = {r["id"] for r in json.loads(
        (session_root / library.INDEX).read_text(encoding="utf-8"))}
    assert (entry.name in indexed) is keep


@pytest.mark.parametrize("demo", [True, False])
def test_the_demo_moves_its_own_entry_into_its_own_trash(window, monkeypatch,
                                                          demo):
    """The demo runs the real Delete: its own entries go into its own
    library's trash by the same code, with no `if demo:` -- and a window
    without the demo does just the same."""
    import pathlib

    from rps7200 import library

    app, root = window
    app.demo = demo
    session_root = pathlib.Path(app.session.root)
    entry = _a_library_entry(session_root)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete(_reopened_with_entry(app, entry))
    (batch,) = library.trash_batches(session_root)
    assert batch.path.parent == session_root / library.TRASH
    assert (batch.path / entry.name / "scan.json").exists()


def test_an_entry_held_open_is_left_whole_and_its_pass_stays(window,
                                                             monkeypatch):
    """A folder with a file open in it cannot be renamed on Windows: the
    move is tried again for a moment, and one still refused is left whole,
    said, and the pass stays in the window."""
    import os
    import pathlib

    from rps7200 import library

    app, root = window
    entry = _a_library_entry(pathlib.Path(app.session.root))
    real = os.rename

    def rename(source, target):
        if os.path.basename(source) == entry.name:
            raise PermissionError(13, "in use", str(source))
        return real(source, target)

    monkeypatch.setattr(library.os, "rename", rename)
    monkeypatch.setattr(library, "REPLACE_RETRY_S", ())
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    result = _reopened_with_entry(app, entry)
    app.on_delete(result)
    assert entry.exists() and result in app.results
    assert f"{entry.name} is left whole in the library" in app.log.get(
        "1.0", "end")


def test_a_pass_still_being_filed_says_so_and_keeps_its_entry(window,
                                                              monkeypatch):
    """B1-T2: until the window handled "filed", a pass's entry was unknown
    to it, and Delete offered only to drop the pass; the entry was filed
    after with nothing said. The session says now: while it is owed, the
    question says so -- OK keeps the entry, Cancel keeps the pass -- and
    once filed, before the event has come, its entry is offered as any."""
    import pathlib

    from rps7200 import library

    app, root = window
    result = _reopened_with_entry(app, None, seq=4242)
    state = {"now": ("owed", None)}
    monkeypatch.setattr(app.session, "filing", lambda seq: state["now"])
    asked, answer = [], {"ok": False}
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append((m, k)) or answer["ok"])
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("no entry to offer yet"))
    app.on_delete(result)
    words, how = asked[-1]
    assert "still being written" in words and "press Delete again" in words
    assert how.get("default") == gui.messagebox.CANCEL
    assert result in app.results, "Cancel keeps the pass"
    answer["ok"] = True
    app.on_delete(result)
    assert result not in app.results

    entry = _a_library_entry(pathlib.Path(app.session.root))
    result = _reopened_with_entry(app, None, seq=4243)
    state["now"] = ("filed", entry)
    offered = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: offered.append(m) or True)
    app.on_delete(result)
    assert entry.name in offered[0]
    assert library.in_trash(pathlib.Path(app.session.root),
                            entry.name) is not None


def test_a_slides_infrared_pass_goes_with_it(window, monkeypatch):
    """NEW-T1: a slide's RGBI pass is filed as an entry of its own and never
    shown; Delete took the colour pass and left it, an entry nothing named.
    It is named in the question and goes in the same delete."""
    import pathlib

    from rps7200 import library

    app, root = window
    session_root = pathlib.Path(app.session.root)
    infrared = _a_library_entry(session_root, 7, infrared_pair={
        "id": "p1", "role": "infrared"})
    colour = _a_library_entry(session_root, 8, infrared_pair={
        "id": "p1", "role": "colour"})
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    app.on_delete(_reopened_with_entry(app, colour))
    assert f"Its infrared pass, {infrared.name}, goes with it" in asked[0]
    assert infrared.name in asked[1]
    (batch,) = library.trash_batches(session_root)
    assert batch.names == sorted([colour.name, infrared.name])
    assert batch.record["moved"] == [colour.name, infrared.name]


def test_only_an_entry_directly_in_the_library_is_offered(window, monkeypatch):
    """NEW-T7: anything inside the session's library was offered -- an entry
    of a library nested in it too, whose index was then rebuilt in the
    wrong folder. Only one directly in it, as the trash takes it."""
    import pathlib

    app, root = window
    pathlib.Path(app.session.root).mkdir(exist_ok=True)
    nested = _a_library_entry(pathlib.Path(app.session.root) / "300dpi")
    said = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("offered to move it"))
    result = _reopened_with_entry(app, nested)
    app.on_delete(result)
    assert nested.exists() and result not in app.results
    assert "not in this session's library, and is left where it is" in said[0]


def test_the_entry_is_kept_while_the_window_closes(window, monkeypatch):
    """Its compaction may be in that entry as the scanner closes."""
    import pathlib

    app, root = window
    entry = _a_library_entry(pathlib.Path(app.session.root))
    told = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: told.append(m))
    result = _reopened_with_entry(app, entry)
    app.closing = True
    try:
        app.on_delete(result)
    finally:
        app.closing = False
    assert entry.exists() and result in app.results
    assert "The window is closing" in told[0]


def _a_scanned_roll(tmp_path, app, numbers=(1, 2, 3)):
    """A roll in the session's rolls folder whose frames were filed into its
    library, each entry saying which frame it is, as the session files them."""
    import pathlib

    folder = pathlib.Path(app.session.rolls) / "strip"
    folder.mkdir(parents=True)
    entries = {n: _a_library_entry(
        pathlib.Path(app.session.root), n, roll_membership={
            "roll": "strip", "number": n, "kind": "frame",
            "folder": str(folder)}) for n in numbers}
    (folder / "roll.json").write_text(json.dumps({
        "roll": "strip", "numbering": "strip", "wanted": list(numbers),
        "frames": [{"number": n, "done": True, "entry": str(e)}
                   for n, e in entries.items()]}), encoding="utf-8")
    for n in numbers:
        (folder / f"frame{n:02d}.tif").write_bytes(b"picture")
    return folder, entries


def test_a_roll_frames_entry_in_the_trash_is_offered_again(window, tmp_path,
                                                           monkeypatch):
    """T-14: roll.json went on calling a deleted frame done, so finishing
    the roll skipped it. Its record is marked first, beside the entry's
    name -- a duplicate's too, which carries the record -- and the frame is
    not scanned while that entry is in the trash, in done_in, the reopened
    sheet's and the browser's count; restored, it is scanned again, with
    nothing written."""
    import pathlib

    from rps7200 import library

    app, root = window
    folder, entries = _a_scanned_roll(tmp_path, app)
    copy = folder.with_name("strip-2")
    gui.shutil.copytree(folder, copy)
    session_root = pathlib.Path(app.session.root)
    app._sheet_roll, app._sheet_done = folder, {1, 2, 3}
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete(_reopened_with_entry(app, entries[2]))
    assert ("Frame 2 of strip then counts as not scanned, and is offered "
            "again when the roll is finished; frame02.tif stays in its "
            "folder.") in asked[0]
    for where in (folder, copy):
        (record,) = [r for r in json.loads((where / "roll.json").read_text(
            encoding="utf-8"))["frames"] if r["number"] == 2]
        assert record["entry_deleted"]["id"] == entries[2].name
        assert record["done"] is True, "the record says what it was"
        assert gui.done_in(where, session_root) == {1, 3}
    assert app._sheet_done == {1, 3}
    assert app._scanned_in(folder) == {1, 3}
    summary = gui.rolls_on_disk(app.session.rolls, session_root)
    cells = {gui.Path(s["folder"]).name: gui.roll_cells(s)[1]
             for s in summary}
    assert cells["strip"] == "2 of 3 (1 without an entry)"
    assert "frame 2 of strip counts as not scanned now" in app.log.get(
        "1.0", "end")

    # The Roll question counts as done_in does a few lines further on.
    assert "holds 2 scanned frames" in gui.folder_note(folder, False,
                                                       session_root)

    before = (folder / "roll.json").read_bytes()
    library.restore(session_root, entries[2].name)
    assert gui.done_in(folder, session_root) == {1, 2, 3}
    assert (folder / "roll.json").read_bytes() == before, "nothing written"
    summary = gui.rolls_on_disk(app.session.rolls, session_root)
    assert {gui.roll_cells(s)[1] for s in summary} == {"finished, 3"}
    # Read without the library, every marked frame counted gone, and the
    # Roll question said 2 here and "skipped: 1-3" below.
    assert "holds 3 scanned frames" in gui.folder_note(folder, False,
                                                       session_root)


def test_deleting_an_earlier_take_says_the_roll_keeps_its_later_one(
        window, tmp_path, monkeypatch):
    """The question was built from the entry's own roll membership, so an
    earlier take of a frame scanned again since was promised "counts as not
    scanned" -- while nothing was marked, the frame stayed scanned, and
    frameNN.tif was the later take's. It says what happens now."""
    import pathlib

    app, root = window
    session_root = pathlib.Path(app.session.root)
    folder, entries = _a_scanned_roll(tmp_path, app)
    later = _a_library_entry(session_root, 22, roll_membership={
        "roll": "strip", "number": 2, "kind": "frame", "folder": str(folder)})
    progress = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    progress["frames"][1].update(entry=str(later), takes=[
        {"entry": entries[2].name}, {"entry": later.name}])
    (folder / "roll.json").write_text(json.dumps(progress), encoding="utf-8")
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete(_reopened_with_entry(app, entries[2]))
    assert "counts as not scanned" not in asked[0]
    assert (f"It is an earlier take of frame 2 of strip: the roll keeps its "
            f"later one, {later.name}, and still counts that frame as "
            "scanned.") in asked[0]
    assert not entries[2].exists(), "moved all the same"
    assert gui.done_in(folder, session_root) == {1, 2, 3}
    assert "entry_deleted" not in (folder / "roll.json").read_text(
        encoding="utf-8")


def test_deleting_a_frame_of_a_roll_in_the_trash_marks_it_there(
        window, tmp_path, monkeypatch):
    """A frame whose entry went while its roll was in the rolls' trash was
    left unmarked, and read as done once the roll was restored -- the
    question promising all the while that it would be offered again."""
    import pathlib

    from rps7200 import library

    app, root = window
    session_root = pathlib.Path(app.session.root)
    rolls = pathlib.Path(app.session.rolls)
    folder, entries = _a_scanned_roll(tmp_path, app)
    roll = library.trash(rolls, [folder], by="a test", kind="rolls")
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.on_delete(_reopened_with_entry(app, entries[1]))
    assert ("Frame 1 of strip, a roll in the rolls' trash, then counts as not "
            "scanned once that roll is restored") in asked[0]
    assert not entries[1].exists()
    library.restore(rolls, roll.batch.name)
    assert gui.done_in(folder, session_root) == {2, 3}


@pytest.mark.parametrize("debug", [True, False])
def test_a_pass_not_filed_says_so_on_delete(window, monkeypatch, debug):
    """B1-T2's other half: a pass whose filing failed has no entry to offer,
    and says why -- and, with debug filing on, that its spooled copy may
    still be filed as the scanner closes. One question, OK or Cancel."""
    import types

    app, root = window
    result = _reopened_with_entry(app, None, seq=4244)
    result.filing = gui.NOT_FILED
    monkeypatch.setattr(app.session, "filing", lambda seq: ("none", None))
    asked, answer = [], {"ok": False}
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or answer["ok"])
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("no entry to offer"))
    real = app.session._scanner
    app.session._scanner = types.SimpleNamespace(debug=debug)
    try:
        assert app.session.debug_filing is debug
        app.on_delete(result)
        assert result in app.results, "Cancel keeps the pass"
        answer["ok"] = True
        app.on_delete(result)
    finally:
        app.session._scanner = real
    assert result not in app.results
    assert "It was not filed in the library -- the log says why" in asked[0]
    assert ("with debug filing on, its spooled copy may still be filed when "
            "the scanner closes" in asked[0]) is debug
    assert asked[0] == asked[1]


def test_a_frame_still_being_written_into_its_roll_is_refused(window,
                                                              tmp_path,
                                                              monkeypatch):
    """The writer tells the window before it tells the roll's record: marked
    in between, the mark would land on the take before."""
    import pathlib

    from rps7200 import session as session_module

    app, root = window
    folder, entries = _a_scanned_roll(tmp_path, app)
    live = session_module.RollManifest(folder / "roll.json", json.loads(
        (folder / "roll.json").read_text(encoding="utf-8")))
    live.record({"number": 2}, awaiting=True)
    app.session._manifests[(folder / "roll.json").resolve()] = live
    told = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: told.append(m))
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("asked anyway"))
    result = _reopened_with_entry(app, entries[2])
    app.on_delete(result)
    assert told == ["Frame 2 is still being written into its roll.json; "
                    "try again in a moment."]
    assert entries[2].exists() and result in app.results
    assert pathlib.Path(app.session.root, ".trash").exists() is False


def test_a_reopened_walk_never_takes_a_reference_from_the_trash(window,
                                                                tmp_path):
    """P25-a: approved.json's reference_entry outranked the walk's own entry
    after it had gone -- and as written it is a path relative to wherever
    the window ran. Only an entry of the library, by its id, stands now;
    otherwise the walk's own, and approved.json is left as it is. And never
    over a walk record that names another (FOUND-1): that frame was walked
    again since its position was set."""
    import pathlib

    from rps7200 import library

    app, root = window
    session_root = pathlib.Path(app.session.root)
    folder = _walked_folder(tmp_path, count=2)
    own = _a_library_entry(session_root, 1)
    approved = _a_library_entry(session_root, 2)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["frames"][0]["prescan_entry"] = own.name
    survey["frames"][1]["prescan_entry"] = None
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "approved.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": n, "reference_entry": f"elsewhere\\{approved.name}"}
            for n in (1, 2)]}),
        encoding="utf-8")
    written = (folder / "approved.json").read_bytes()
    entries = {r.number: r.entry for r in gui.read_survey(
        folder, library_root=session_root)["results"]}
    assert entries[2] == session_root / approved.name, "by its id, here"
    assert entries[1] == session_root / own.name, "walked again since"
    library.trash(session_root, [approved], by="a test")
    entries = {r.number: r.entry for r in gui.read_survey(
        folder, library_root=session_root)["results"]}
    assert entries[1] == session_root / own.name, "the walk's own"
    assert entries[2] is None, "none from the trash"
    assert (folder / "approved.json").read_bytes() == written


def test_the_rolls_trash_is_no_roll(window, tmp_path):
    """NEW-T2: the browser listed every folder that held a roll's records,
    and a roll moved into the trash still does."""
    import pathlib

    from rps7200 import library

    app, root = window
    folder, _entries = _a_scanned_roll(tmp_path, app)
    rolls = pathlib.Path(app.session.rolls)
    library.trash(rolls, [folder], by="a test", kind="rolls")
    assert gui.rolls_on_disk(rolls, app.session.root) == []
    assert gui.roll_summary(rolls / library.TRASH) is None


@pytest.mark.parametrize("answer", [True, False, None])
def test_save_as_asks_before_it_replaces_or_strands_a_companion(
        window, tmp_path, monkeypatch, answer):
    """The dialog asks about the one name typed. A pass with no infrared
    saved over x.jpg left the earlier save's x.dng beside it, reading as one
    scan with a picture it does not belong to. Yes saves under the name and
    removes that DNG once the new file is in place; No takes the next free
    name; Cancel writes nothing."""
    pytest.importorskip("PIL")
    app, root = window
    (tmp_path / "x.jpg").write_bytes(b"an earlier save")
    (tmp_path / "x.dng").write_bytes(b"its infrared")
    result = types.SimpleNamespace(
        label="scan 1", entry=None, image=np.zeros((4, 6, 3), np.uint16),
        rotation=0, flipped=False, meta={})
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **k: str(tmp_path / "x.jpg"))
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda title, words, **k: asked.append(words) or answer)
    monkeypatch.setattr(app, "_mono_for", lambda r: (False, "G"))
    app.on_save_as(result)
    for thread in app._writing:
        thread.join(10)
    app._drain()
    (words,) = asked
    assert "x.dng is beside x.jpg already" in words
    assert "No saves as x-2.jpg instead" in words
    log = app.log.get("1.0", "end")
    if answer is None:
        # Less the window's own kept log, beside its settings in this folder.
        assert sorted(p.name for p in tmp_path.iterdir()
                      if p.name != "logs") == ["x.dng", "x.jpg"]
        assert (tmp_path / "x.jpg").read_bytes() == b"an earlier save"
    elif answer:
        assert (tmp_path / "x.jpg").read_bytes() != b"an earlier save"
        assert not (tmp_path / "x.dng").exists(), "the stale DNG was left"
        assert "removed x.dng" in log
    else:
        assert (tmp_path / "x-2.jpg").exists()
        assert (tmp_path / "x.jpg").read_bytes() == b"an earlier save"
        assert (tmp_path / "x.dng").read_bytes() == b"its infrared"
    from rps7200 import export
    assert export._claims == {}, "Save As kept its name claimed"


def test_save_as_leaves_the_dng_of_a_jpeg_it_did_not_replace(
        window, tmp_path, monkeypatch):
    """Without Pillow a JPEG is written as a TIFF, and the earlier save's
    x.jpg stays. Its x.dng, not rewritten, was taken for stale and removed:
    the JPEG still there lost its infrared."""
    from rps7200 import export

    app, root = window
    (tmp_path / "x.jpg").write_bytes(b"an earlier save")
    (tmp_path / "x.dng").write_bytes(b"its infrared")

    def no_pillow(*a, **k):
        raise ImportError("No module named 'PIL'")

    monkeypatch.setattr(export, "_write_jpeg", no_pillow)
    result = types.SimpleNamespace(
        label="scan 1", entry=None, image=np.zeros((4, 6, 3), np.uint16),
        rotation=0, flipped=False, meta={})
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **k: str(tmp_path / "x.jpg"))
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: True)
    monkeypatch.setattr(app, "_mono_for", lambda r: (False, "G"))
    app.on_save_as(result)
    for thread in app._writing:
        thread.join(10)
    app._drain()
    assert (tmp_path / "x.tif").exists(), "the fallback was not written"
    assert (tmp_path / "x.jpg").read_bytes() == b"an earlier save"
    assert (tmp_path / "x.dng").read_bytes() == b"its infrared", \
        "the JPEG still there lost its infrared"
    assert "removed x.dng" not in app.log.get("1.0", "end")


def test_save_as_asks_nothing_where_nothing_is_beside_it(window, tmp_path,
                                                        monkeypatch):
    pytest.importorskip("PIL")
    app, root = window
    (tmp_path / "x.tif").write_bytes(b"a TIFF is no JPEG's companion here")
    result = types.SimpleNamespace(
        label="scan 1", entry=None, image=np.zeros((4, 6, 3), np.uint16),
        rotation=0, flipped=False, meta={})
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename",
                        lambda **k: str(tmp_path / "y.jpg"))
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr(app, "_mono_for", lambda r: (False, "G"))
    app.on_save_as(result)
    for thread in app._writing:
        thread.join(10)
    assert (tmp_path / "y.jpg").exists()


@pytest.mark.parametrize("answer", [None, True, False])
def test_quitting_mid_pass_asks_and_never_abandons_the_read(window, monkeypatch,
                                                            answer):
    """Cancel keeps working; Yes stops after the frame in flight and then
    quits; No lets the queue finish. None of the three may tear the window
    down while the worker still has the device: that is an abandoned read,
    and a power cycle. The busy branch had no test; the one on_close test
    stubbed out the wait."""
    app, root = window
    app.busy = True
    calls = []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: answer)
    real_shutdown, real_thread = app.session.shutdown, app.session._thread
    monkeypatch.setattr(app.session, "request_stop",
                        lambda: calls.append("stop"))
    monkeypatch.setattr(app.session, "shutdown",
                        lambda: (calls.append("shutdown"), real_shutdown()))
    monkeypatch.setattr(app, "_quit", lambda: calls.append("quit"))

    class Working:
        """The worker as `_wait_to_quit` sees it: still on the device until
        the test says otherwise. Joining reaches the real one, so the
        fixture still ends the session it started."""
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            real_thread.join(timeout)

    worker = Working()
    monkeypatch.setattr(app.session, "_thread", worker)
    app.on_close()

    if answer is None:
        assert calls == [] and not app.closing
        return
    assert calls == (["stop", "shutdown"] if answer else ["shutdown"])
    assert app.closing
    app._wait_to_quit()
    assert "quit" not in calls, "quit with the worker still on the device"
    worker.alive = False
    app._wait_to_quit()
    assert calls[-1] == "quit"


def test_the_session_closing_waits_for_files_still_being_written(
        window, monkeypatch):
    """An idle session closes a second or two after Quit, and its "closed"
    went straight to `_quit`: the window went, the interpreter exited under
    the daemon Save all / Export thread, and the file it was writing was
    left truncated under its final name."""
    import threading

    from rps7200.session import Event

    app, root = window
    quits = []
    monkeypatch.setattr(app, "_quit", lambda: quits.append(1))
    release = threading.Event()
    app._start_writing(lambda: release.wait(10), "export-test")
    app.closing = True
    try:
        app._handle(Event(kind="closed"))
        assert quits == [], "quit with an export still writing"
        assert "finishing the files" in app.v_state.get()
    finally:
        release.set()
    for thread in app._writing:
        thread.join(10)
    app._wait_to_quit()
    assert quits, "and quits once the writing is done"


@pytest.mark.parametrize("broken", ["library", "copies", "slide"])
def test_a_failed_filing_is_shown_and_not_only_logged(window, tmp_path,
                                                     monkeypatch, broken):
    """A pass that could not be filed, or whose delivered copy could not be
    written, reached the window as a log line and nothing else -- twenty
    frames into a roll, nobody reads the log. The session here is a real one
    on the demo stand-in, so these are its own records.

    Marked by the session's `Filing`, never by the words of a log line: a
    slide's picture whose copy failed was guessed by its frame number onto
    the Scan, "not filed in the library", a moment before the Scan's own
    "filed" -- both its passes were in the library."""
    from rps7200.demo import DemoScanner
    from rps7200.session import (COPY_FAILED, NOT_FILED, UNFILED_TEMP, Scan,
                                 ScanSession)

    app, root = window
    # A refused library keeps the pass in the system's temp folder when there
    # is no delivered copy to keep it beside: that would be the machine's own
    # %TEMP%, gaining a real entry on every run of the suite.
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    # A library that refuses the pass, or an output folder that takes no
    # file, once the job has begun: a disk that filled. A file in the way of
    # either stood for one, and is refused before the job moves anything now.
    from pathlib import Path

    from rps7200 import export, library

    refused, out = tmp_path / "library", tmp_path / "out"
    if broken == "library":
        save = library.save

        def refusing(image, meta, *, root=library.DEFAULT_ROOT, **kw):
            if Path(root) == refused:
                raise OSError(28, "No space left on device")
            return save(image, meta, root=root, **kw)

        monkeypatch.setattr(library, "save", refusing)
    else:
        out.mkdir()
        write = export.write

        def full(path, *a, **kw):
            if out in Path(path).parents:
                raise OSError(28, "No space left on device")
            return write(path, *a, **kw)

        monkeypatch.setattr(export, "write", full)
    session = ScanSession(
        root=str(refused if broken == "library" else tmp_path / "lib"),
        out_dir=str(out) if broken != "library" else None,
        reference=str(tmp_path / "shading.npz"), verbose=False,
        open_scanner=lambda: DemoScanner(str(tmp_path / "pictures"),
                                         speed=1e9))
    session.start()
    session.submit(Scan(resolution=300, infrared=broken == "slide",
                        film="positive" if broken == "slide" else "negative"))
    session.shutdown()
    session.join(timeout=60)
    notices = []
    monkeypatch.setattr(app, "_filing_notice",
                        lambda outcome, text: notices.append((outcome, text)))
    _at_once(app, monkeypatch)
    events = [e for e in session.poll() if e.kind != "closed"]
    # The close says the writer's words again, as log lines: they count for
    # nothing now, so a line repeated is not a failure repeated.
    for event in events + [e for e in events if e.kind == "log"]:
        app._handle(event)
    (scanned,) = [r for r in app.results if r.kind == "scan"]
    wanted = NOT_FILED if broken == "library" else COPY_FAILED
    owner = next(e.done for e in events if e.kind == "state" and e.busy)
    assert scanned.filing == wanted, "the pass is marked, by its own seq"
    assert app._filing_counts[owner] == {wanted: 1}
    assert [outcome for outcome, _text in notices] == [wanted], \
        "one notice, outside the pump"
    assert "see the log" in app.v_progress.get()
    if broken == "library":
        assert "not filed in the library" in scanned.error
        assert app.strip.find_withtag("failed"), "red on the filmstrip"
        assert "1 not filed" in app.v_progress.get()
        assert any((temp / UNFILED_TEMP).iterdir()), \
            "kept in the test's own temp folder"
    else:
        # Its pass is in the library, and says so: blue, never "not filed".
        assert scanned.entry is not None and scanned.entry.exists()
        assert "not filed" not in scanned.error
        assert app.strip.find_withtag("unwritten")
        assert not app.strip.find_withtag("failed")
        assert "1 copy not written" in app.v_progress.get()
        assert ("its delivered picture was not written" if broken == "slide"
                else "a delivered copy was not written") in scanned.error
    if broken == "slide":
        assert "made from the frame's two passes" in notices[0][1]
    # and the notice itself is a window of its own, not a modal
    gui.ScannerGui._filing_notice(app, *notices[0])
    assert app._filing_window.winfo_exists()
    assert app._filing_window.title() == (
        "Not filed" if broken == "library" else "Copy not written")
    app._filing_window.destroy()


def _at_once(app, monkeypatch):
    """What the window defers runs at once, and its pump stays still: the
    fixture's own session is running, and its events -- a job of its own
    taken -- would land between these."""
    monkeypatch.setattr(app, "_later", lambda _ms, call: call())


def _filing(outcome, seq, owner, number=0, part="", shown=None, **fields):
    """A "filing" event, as the session sends one with its `Filing`."""
    from rps7200.session import NOT_FILED, Event, Filing

    filing = Filing(seq=seq, number=number, kind="scan", outcome=outcome,
                    shown=seq if shown is None else shown, owner=owner,
                    part=part, error="x" if outcome == NOT_FILED else None,
                    **fields)
    return Event(kind="filing", done=filing.shown or 0, total=number,
                 text=outcome, filing=filing)


def _scan_result(app, seq, number=0, label=None):
    """A pass the window shows, as a "result" event hands one over."""
    from rps7200.session import Result

    result = Result(seq=seq, kind="scan", label=label or f"pass {seq}",
                    image=np.zeros((4, 6, 3), np.uint16), meta={},
                    number=number)
    app._add_result(result)
    return result


def test_a_late_filing_counts_against_the_job_that_queued_it(window,
                                                            monkeypatch):
    """`finished` comes as soon as a job's passes are taken, and its filings
    land after -- during the next job, as the demo showed for two Scans. The
    failure was counted against the next job, used up its one notice, and
    never reached its own job's finished line."""
    from pathlib import Path

    from rps7200.session import COPY_FAILED, NOT_FILED, Event

    app, root = window
    notices = []
    monkeypatch.setattr(app, "_filing_notice",
                        lambda outcome, text: notices.append(outcome))
    _at_once(app, monkeypatch)
    app._handle(Event(kind="state", text="scanning at 300 dpi RGB",
                      busy=True, done=1))
    first = _scan_result(app, 1, label="first scan")
    app._handle(Event(kind="finished", text="scanning at 300 dpi RGB"))
    app._handle(Event(kind="state", text="idle", busy=False))
    app._handle(Event(kind="state", text="prescanning at 300 dpi (~16 s)",
                      busy=True, done=2))
    second = _scan_result(app, 2)
    app._handle(_filing(COPY_FAILED, seq=1, owner=1, entry=Path("e1"),
                        unwritten=(("out/a.tif", "full"),)))
    assert app._filing_counts == {1: {COPY_FAILED: 1}}
    assert first.filing == COPY_FAILED
    assert getattr(second, "filing", None) is None
    # Job 2 runs on, its line its own.
    assert app.v_progress.get() == "prescanning at 300 dpi (~16 s)"
    app._handle(Event(kind="finished", text="prescanning at 300 dpi"))
    assert app.v_progress.get() == "prescanning at 300 dpi -- done", \
        "the second job's line said the first one's failure"
    # One of its own, landing after its "finished" and before its "idle":
    # its done line again, with the count, not a line about one pass.
    app._handle(_filing(NOT_FILED, seq=2, owner=2))
    assert app.v_progress.get() == ("prescanning at 300 dpi -- done -- "
                                    "1 not filed -- see the log")
    app._handle(Event(kind="state", text="idle", busy=False))
    # Another of job 1's, with nothing running: its own line, said again.
    app._handle(_filing(COPY_FAILED, seq=1, owner=1, entry=Path("e1"),
                        unwritten=(("out/b.tif", "full"),)))
    assert app.v_progress.get() == ("scanning at 300 dpi RGB -- done -- "
                                    "2 copies not written -- see the log")
    assert notices == [COPY_FAILED, NOT_FILED], \
        "one notice for each kind in each job, and no more"


def test_log_words_mark_nothing_and_records_are_never_deduplicated(
        window, monkeypatch):
    """The window read failures from log lines, and kept every line it had
    taken so the close's repetition was not counted twice -- for the whole
    session, so a retaken frame failing the same way again was neither
    marked nor counted."""
    from rps7200.session import NOT_FILED, Event

    app, root = window
    monkeypatch.setattr(app, "_filing_notice", lambda outcome, text: None)
    _at_once(app, monkeypatch)
    app._handle(Event(kind="state", text="scanning a roll", busy=True,
                      done=1))
    first = _scan_result(app, 5, number=3)
    app._handle(Event(kind="log", text="picture 3 could not be filed: x"))
    assert getattr(first, "filing", None) is None
    assert app._filing_counts == {}
    retaken = _scan_result(app, 9, number=3)
    app._handle(_filing(NOT_FILED, seq=5, owner=1, number=3))
    app._handle(_filing(NOT_FILED, seq=9, owner=1, number=3))
    assert first.filing == retaken.filing == NOT_FILED
    assert app._filing_counts == {1: {NOT_FILED: 2}}
    # A filing no result stands for is counted, and marks nothing.
    app._handle(_filing(NOT_FILED, seq=77, owner=1, number=4))
    assert app._filing_counts == {1: {NOT_FILED: 3}}


def test_a_slides_marks_keep_the_worst_of_each_part(window, monkeypatch):
    """Its infrared pass refused, then its picture's copy refused, then its
    RGB pass filed: red stays, and the caption names both parts."""
    from pathlib import Path

    from rps7200.session import COPY_FAILED, FILED, NOT_FILED, Event

    app, root = window
    monkeypatch.setattr(app, "_filing_notice", lambda outcome, text: None)
    _at_once(app, monkeypatch)
    app._handle(Event(kind="state", text="scanning at 300 dpi RGBI",
                      busy=True, done=1))
    slide = _scan_result(app, 4, label="300 dpi RGB + infrared")
    app._handle(_filing(NOT_FILED, seq=5, shown=4, owner=1,
                        part="infrared pass"))
    app._handle(_filing(COPY_FAILED, seq=6, shown=4, owner=1, part="picture",
                        unwritten=(("out/x.tif", "full"),)))
    app._handle(_filing(FILED, seq=4, owner=1, entry=Path("rgb")))
    app._handle(Event(kind="filed", done=4, text="rgb"))
    assert slide.filing == NOT_FILED
    assert slide.error == ("its infrared pass was not filed in the library; "
                           "its delivered picture was not written -- see the "
                           "log")
    assert app.strip.find_withtag("failed")
    assert app._filing_counts == {1: {NOT_FILED: 1, COPY_FAILED: 1}}


@pytest.mark.parametrize("typed,aborted", [
    (None, False), ("", False), ("yes", False), ("abort it", False),
    ("ABORT", True), ("  abort ", True)])
def test_force_abort_needs_the_word_typed(window, monkeypatch, typed, aborted):
    """Closing the transport under a read almost certainly costs a power
    cycle, so only the word itself does it -- not Enter, not "yes"."""
    app, root = window
    fired = []
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: typed)
    monkeypatch.setattr(app.session, "force_abort", lambda: fired.append(1))
    app.on_abort()
    assert bool(fired) is aborted


def test_a_plain_roll_is_not_shown_with_the_sheets_turns(window, monkeypatch):
    """The Roll button's frames are written the session's way; turns a sheet
    left against frame numbers put this roll's frames of the same number on
    screen, and in Save As, the other way up from their files."""
    app, root = window
    app.calibrated = True
    app.v_dryrun.set(False)
    app.orientations = {("frame", 2): (90, False), ("at", 5): (180, True)}
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    app.v_startat.set("1")
    app.v_last.set("3")
    app.on_roll()
    assert app.orientations == {("at", 5): (180, True)}


# -- a pass read bottom-up is shown upright, and nothing is turned by it ------


def test_the_caption_says_when_a_pass_was_read_bottom_up():
    assert gui.read_note({"direction": "reversed", "turned": True}) == (
        "   ·   read bottom-up, turned upright")
    assert "unknown" in gui.read_note({"direction": "unknown"})
    assert gui.read_note({"direction": "forward"}) == ""
    assert gui.read_note(None) == ""


def test_prescan_scan_prescan_scan_shows_everything_upright(window, monkeypatch,
                                                              tmp_path):
    """The path that used to ship a correct scan upside down: a prescan taken
    straight after an RGBI scan comes back bottom-up, and the scan after it
    was then judged against it and turned. The demo's carriage reads that
    prescan bottom-up the way the scanner does; the decode turns it, and the
    scan is left as it came."""
    import json as _json

    app, root = window
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    app.v_film.set("negative")
    app._sync_film()
    app.v_ir.set(True)

    def wait_for(count):
        deadline = time.monotonic() + 60
        while len(app.results) < count or app.busy:
            root.update()
            time.sleep(0.01)
            assert time.monotonic() < deadline, "the demo never answered"

    for press, count in ((app.on_prescan, 1), (app.on_scan, 2),
                         (app.on_prescan, 3), (app.on_scan, 4)):
        press()
        wait_for(count)
    prescans = [r for r in app.results if r.kind == "prescan"]
    scans = [r for r in app.results if r.kind == "scan"]
    assert [(r.meta.get("read_direction") or {}).get("direction")
            for r in prescans] == ["forward", "reversed"]
    assert all(not r.meta.get("reversal") for r in scans), "no scan was turned"
    app._show(prescans[1])
    assert "read bottom-up, turned upright" in app.v_caption.get()
    # and the library says the same
    app.session.shutdown()
    app.session.join(timeout=10)
    filed = [_json.loads(p.read_text()) for p in
             sorted((tmp_path / "library").glob("*/scan.json"))]
    directions = [(r["scan"].get("read_direction") or {}).get("direction")
                  for r in filed if "prescan" in r.get("tags", [])]
    assert directions == ["forward", "reversed"]


# -- the big view: which line is which, and how far the frame overhangs -----


def test_one_read_edge_puts_the_other_end_a_frame_width_away():
    from tools import frame_edges

    frame = frame_edges.frame_columns(428)
    left_only = {"width": 428, "edges": {"left": {"state": "edge", "x": 14.0},
                                         "right": {"state": "picture_to_border"}}}
    assert gui.frame_ends(left_only) == (14.0, 14.0 + frame, True)
    right_only = {"width": 428, "edges": {"right": {"state": "edge", "x": 410.0}}}
    assert gui.frame_ends(right_only) == (410.0 - frame, 410.0, True)
    both = {"width": 428, "edges": {"left": {"state": "edge", "x": 2.0},
                                    "right": {"state": "edge", "x": 426.0}}}
    assert gui.frame_ends(both) == (2.0, 426.0, False)
    assert gui.frame_ends({"width": 428, "edges": {}}) is None


def test_a_centred_frame_overhangs_the_guides_as_the_scan_window_has_it(window,
                                                                       tmp_path):
    """What Stefan saw as the orange line being off: the frame is 350.6 units
    and the scan takes about 346.8 of them, so centred, the red line sits a
    little outside the orange one -- by design. Not alike on both sides: the
    guides are where the scan cuts, which reaches further past the prescan on
    the right, so a frame centred on its prescan overhangs the left guide by
    about 2.8 units and the right by about 1.0."""
    from rps7200.framing import FRAME_WIDTH_UNITS, scan_window, units_per_column

    app, _root = window
    offsets, notes, _edges = _sheet_reads(app, _walked(tmp_path))
    assert notes
    cut_left, cut_right = scan_window(428)
    spare = FRAME_WIDTH_UNITS - (cut_right - cut_left) * units_per_column(428)
    want_left, want_right = gui.centred_overhang()
    assert want_left + want_right == pytest.approx(spare)
    for n, note in notes.items():
        left, right = gui.frame_overhang(note, offsets.get(n, 0.0))
        assert left + right == pytest.approx(spare, abs=1e-6)
        # where centring puts it, but for the snap to a position a command reaches
        assert abs((left - right) - (want_left - want_right)) < 1.1, (n, left, right)
        assert left > 1.5 and right > 0.0


def test_the_overhang_is_measured_against_where_the_scan_cuts():
    """Not against the prescan's own edges, which the scan reaches past: a
    frame ending exactly where the scan cuts overhangs nothing, and one ending
    at the prescan's edges shows base inside the scan on both sides."""
    from rps7200.framing import scan_window, units_per_column

    cut_left, cut_right = scan_window(428)

    def ends(left, right):
        return {"width": 428, "edges": {"left": {"state": "edge", "x": left},
                                        "right": {"state": "edge", "x": right}}}

    assert gui.frame_overhang(ends(cut_left, cut_right), 0.0) == pytest.approx(
        (0.0, 0.0), abs=1e-9)
    left, right = gui.frame_overhang(ends(0.0, 428.0), 0.0)
    per = units_per_column(428)
    assert left == pytest.approx(cut_left * per) and left < 0.0
    assert right == pytest.approx((428.0 - cut_right) * per) and right < 0.0


def test_a_scan_reaches_past_the_prescan_further_on_the_right():
    """About 0.2 units past its left edge and 2.0 past its right; and the
    frame, the scan's window and what overhangs it add up."""
    from rps7200.framing import FRAME_WIDTH_UNITS, units_per_column

    reach_left, reach_right = gui.scan_reach_units()
    assert 0.0 < reach_left < 0.5 and 1.5 < reach_right < 2.5
    past_left, past_right = gui.centred_overhang()
    assert (428 * units_per_column(428) + reach_left + reach_right
            + past_left + past_right) == pytest.approx(FRAME_WIDTH_UNITS)


def test_the_overhang_is_said_in_words():
    assert "3.0 past the left, 3.1 past the right" in gui.overhang_note((3.02, 3.08))
    assert "2.0 of base showing on the left" in gui.overhang_note((-2.0, 8.1))
    assert gui.overhang_note(None) == ""


def test_the_big_view_draws_the_frames_other_end_lighter(window, tmp_path):
    app, root = window
    sheet = _read_sheet(app, _walked(tmp_path))
    root.update()
    sheet.adjust(0)
    adj = sheet._adjuster
    root.update()
    adj._draw()
    c = adj.canvas
    lines = {c.itemcget(it, "fill"): c.coords(it) for it in c.find_all()
             if c.type(it) == "line"}
    guides = sorted(c.coords(it)[0] for it in c.find_all()
                    if c.type(it) == "line" and c.itemcget(it, "fill") == adj.GUIDE)
    assert sheet.EDGE_LINE in lines and adj.FAR_EDGE in lines
    read, far = lines[sheet.EDGE_LINE][0], lines[adj.FAR_EDGE][0]
    # the read edge just outside the left guide, the far end just outside the right
    assert read < guides[0] < guides[1] < far
    # overhanging as the scan's window has it -- more on the left, which the
    # scan reaches less far past -- but for the snap to a reachable position
    from rps7200.framing import units_per_column

    px_per_unit = adj._photo.width() / adj._source().shape[1] / units_per_column(428)
    want_left, want_right = gui.centred_overhang()
    lopsided = (want_left - want_right) * px_per_unit
    assert abs((guides[0] - read) - (far - guides[1]) - lopsided) < 4
    assert "past the left" in adj.v_read.get()
    sheet.top.destroy()


def test_the_guides_are_where_the_scan_cuts_not_the_prescans_edges(window,
                                                                  tmp_path):
    """The dashed lines stood at the prescan's own edges and were called "the
    aperture -- exactly what the scan takes". A scan takes more of the
    transport than the 428 columns a prescan comes back with
    (`framing.SCAN_WINDOW_COLUMNS`), and the guides stand there now. The grey
    rectangle a moved frame shows stays the prescan's own extent: where the
    picture was walked."""
    from rps7200.framing import scan_window

    app, root = window
    sheet = _read_sheet(app, _walked(tmp_path, count=2))
    root.update()
    sheet.adjust(0)
    adj = sheet._adjuster
    root.update()
    assert adj.offset, "the detector moves this frame, so the walked extent shows"
    adj._draw()
    c = adj.canvas
    columns = adj._source().shape[1]
    cut_left, cut_right = scan_window(columns)

    def guides():
        return sorted(c.coords(it)[0] for it in c.find_all()
                      if c.type(it) == "line" and c.itemcget(it, "fill") == adj.GUIDE)

    (walked,) = [c.coords(it) for it in c.find_all() if c.type(it) == "rectangle"]
    x0, x1 = walked[0], walked[2]
    assert x1 - x0 == adj._photo.width(), "the prescan's own extent"
    per = (x1 - x0) / columns
    assert guides() == pytest.approx([x0 + cut_left * per, x0 + cut_right * per])
    assert guides()[0] < x0 < x1 < guides()[1]
    # Put back as walked: the picture sits where the walk saw it, the walked
    # extent is the picture and is not drawn, and the guides have not moved.
    before = guides()
    adj._centre()
    root.update()
    (picture,) = [c.coords(it) for it in c.find_all() if c.type(it) == "image"]
    assert picture[0] == pytest.approx(x0)
    assert not [it for it in c.find_all() if c.type(it) == "rectangle"]
    assert guides() == pytest.approx(before)
    sheet.top.destroy()


def test_the_position_window_says_where_the_scan_really_cuts(window, tmp_path):
    """It called the dashed lines "the aperture -- exactly what the scan
    takes" while they stood at the prescan's edges, which the scan reaches
    past. It says how far now, and how far a centred frame overhangs each,
    from the constants rather than from a number typed into the sentence."""
    app, root = window
    sheet = _read_sheet(app, _walked(tmp_path, count=2))
    root.update()
    sheet.adjust(0)
    adj = sheet._adjuster
    root.update()
    (label,) = [w for w in gui._descendants(adj.top)
                if w.winfo_class() == "TLabel" and "dashed" in str(w.cget("text"))]
    said = str(label.cget("text"))
    # Wrapped to the window: unwrapped, it was one line thousands of pixels
    # long and everything after its first sentence was cut off unread.
    assert 0 < int(str(label.cget("wraplength"))) <= adj.WIDTH
    assert "exactly what the scan takes" not in said
    assert "aperture" not in said
    reach_left, reach_right = gui.scan_reach_units()
    assert (f"about {reach_left:.1f} units past its left edge and "
            f"{reach_right:.1f} past its right") in said
    past_left, past_right = gui.centred_overhang()
    assert (f"overhangs the left guide by about {past_left:.0f} units and the "
            f"right by about {past_right:.0f}") in said
    # The picture is the prescan as it came (`_source`), so a frame the sheet
    # shows turned or flipped opens here on its side or mirrored: said, as it
    # was before the rewrite dropped it.
    assert ("Shown as the film sits in the transport, not turned or flipped"
            in said)
    sheet.top.destroy()


def test_a_double_click_on_a_frame_leaves_its_tick_alone(window, tmp_path):
    """Double-click is how the sheet says to set a position, and its first
    press is a click, which ticks. So every frame double-clicked was also
    unticked, the position window loaded that, and Done left it out of the
    roll."""
    app, root = window
    out = gui.read_survey(_walked_folder(tmp_path, count=3))
    sheet = gui._ContactSheet(app, out["results"])
    root.update()
    picture = sheet._pictures[2]
    assert sheet.ticks[2].get() is True
    _press(picture, "<Button-1>")                   # the first press ...
    _press(picture, "<Double-Button-1>")            # ... and the second
    root.update()
    assert sheet.ticks[2].get() is True
    assert sheet._adjuster is not None and sheet._adjuster.v_tick.get() is True
    _press(picture, "<Button-1>")                   # a click still ticks
    assert sheet.ticks[2].get() is False
    sheet.top.destroy()


def test_return_through_the_strip_does_not_tick_a_scanned_frame(window, tmp_path):
    """A resumed roll opens its scanned frames unticked so they are not
    scanned twice. Return in the position window -- "keep this one, next" --
    ticked every frame it passed, scanned or not."""
    app, root = window
    out = gui.read_survey(_walked_folder(tmp_path, count=3))
    sheet = gui._ContactSheet(app, out["results"], done={2})
    root.update()
    assert sheet.ticks[2].get() is False
    sheet.ticks[1].set(False)
    sheet.adjust(0)
    for _ in range(2):
        sheet._adjuster._accept()
    assert sheet.ticks[1].get() is True, "a frame not yet scanned is ticked"
    assert sheet.ticks[2].get() is False, "a scanned one is left as it was"
    sheet.top.destroy()


def test_the_commission_names_frames_it_would_scan_again(window, monkeypatch,
                                                        tmp_path):
    """"All" ticks a scanned frame as readily as any other, and the question
    before the film moves gave only a count. It names them now -- the ones
    the reopened roll said were done, and those its roll.json says since."""
    app, root = window
    app.calibrated = True
    folder = tmp_path / "rolls" / "resumed"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps({
        "numbering": "strip", "frames": [{"number": 3, "done": True}]}),
        encoding="utf-8")
    app._sheet_roll = folder
    app._sheet_done = {1}
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or False)
    app.on_scan_chosen((1, 2, 3))
    assert "scanned again if you go on: 1, 3." in asked[0]
    asked.clear()
    app._sheet_done = set()
    app._sheet_roll = tmp_path / "rolls" / "fresh"
    app.on_scan_chosen((1, 2))
    assert "scanned again" not in asked[0]


def test_a_sheet_opened_mid_walk_holds_the_whole_walk_at_its_end(window,
                                                                 tmp_path):
    """Opened while the walk was still going, it held the frames walked by
    then, and the walk's end only raised it: the rest had no cell and no tick,
    and a commission from it left them unscanned without a word."""
    from rps7200.session import Event

    app, root = window
    results = gui.read_survey(_walked_folder(tmp_path, count=3))["results"]
    app._surveying = True
    app.survey = results[:2]
    app.on_contact_sheet()
    root.update()
    assert sorted(app.sheet.ticks) == [1, 2]
    app.sheet.ticks[1].set(False)                    # decided while it walked
    app.survey.append(results[2])
    app._handle(Event(kind="finished", text="walked 3 frames"))
    root.update()
    assert sorted(app.sheet.ticks) == [1, 2, 3]
    assert app.sheet.ticks[1].get() is False, "what was decided is kept"
    assert app.sheet.ticks[3].get() is True
    app.sheet.top.destroy()


def test_a_sheet_open_through_a_kept_walk_drops_what_it_walked_again(
        window, tmp_path):
    """The walk's end dropped a re-walked frame's position from the window's
    copy and said so -- and then closed the open sheet, which filed its own
    copy, the position still in it, over the top. The sheet came back holding
    a position measured on a prescan the walk had just replaced."""
    import copy

    from rps7200.session import Event

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    results = gui.read_survey(folder)["results"]
    app._sheet_roll = folder
    app.survey = list(results)
    app.sheet_state = {"offsets": {2: 0.5116}, "sources": {2: "operator"},
                       "rotations": {2: 90}}
    app._kept_walk, app._rewalked = {1, 2, 3}, set()
    app._surveying = True
    app.on_contact_sheet()
    root.update()
    assert app.sheet.offsets[2] == 0.5116, "his position, before the walk"
    app._into_survey(copy.copy(results[1]))             # frame 2, again
    app._handle(Event(kind="finished", text="walked 1 frame"))
    root.update()
    assert app.sheet.proposals.get(2, {}).get("source") != "operator"
    assert app.sheet.offsets.get(2) != 0.5116
    assert app.sheet.rotations[2] == 90, "a turn is about the picture: kept"
    for kept in (app.sheet_state, app.remembered["sheet"][folder.name]):
        assert 2 not in (kept.get("offsets") or {})
        assert "2" not in (kept.get("offsets") or {})
    app.sheet.top.destroy()


@pytest.mark.parametrize("options,refused", [
    ({"dpi": "7200", "predpi": "300", "ir": False, "film": "negative"},
     "7200 dpi pass cannot be shading-corrected"),
    ({"dpi": "1800", "predpi": "7200", "ir": False, "film": "negative"},
     "7200 dpi pass cannot be shading-corrected"),
    ({"dpi": "1800", "predpi": "300", "ir": True, "film": "bw"},
     "Infrared is blind to bw"),
])
def test_a_commission_the_driver_would_refuse_is_refused_before_it_moves(
        window, monkeypatch, tmp_path, options, refused):
    """The roll winds to its first frame before the driver reads what it was
    asked for. At 7200 dpi it then prescanned, held and metered three frames
    before giving up; infrared on B&W, which the sheet's panel let through,
    was refused with the film already moved."""
    app, root = window
    app.calibrated = True
    app._sheet_roll = tmp_path / "rolls" / "sheet"
    jobs, said = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda *a, **k: pytest.fail("asked to start it"))
    app.on_scan_chosen((1, 2), options=dict(options, fast_ir=True,
                                            meter="none", correct=False))
    assert jobs == [] and refused in said[0] and "Nothing has moved" in said[0]
    assert not (app._sheet_roll / "approved.json").exists()


def test_a_roll_at_a_resolution_nothing_can_correct_is_refused(window,
                                                               monkeypatch):
    app, root = window
    app.calibrated = True
    jobs, said, asked = [], [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda *a, **k: asked.append(1) or False)
    app.v_dpi.set("7200")
    app.v_dryrun.set(False)
    app.on_roll()
    assert jobs == [] and asked == [] and "7200 dpi" in said[0]
    # A walk scans only its prescans, so it is asked about as usual.
    app.v_dryrun.set(True)
    app.on_roll()
    assert len(said) == 1 and asked == [1]


def test_aim_measures_only_from_a_prescan_of_the_film_where_it_is(window,
                                                                  monkeypatch):
    """Any prescan on screen was aimed from: an older frame's, a reopened
    roll's from another day, or the one just aimed from -- a second click on
    it applied the same correction twice."""
    from rps7200.session import Event, Result

    app, root = window
    aimed, said, jobs = [], [], []
    monkeypatch.setattr(app, "_aim", lambda event: aimed.append(event))
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    app.v_aim.set(True)

    def prescan(seq, number=0):
        return Result(seq=seq, kind="prescan", label=f"prescan {seq}",
                      image=np.zeros((20, 30, 3), np.uint8), meta={},
                      number=number)

    older, fresh = prescan(1), prescan(2)
    for result in (older, fresh):
        app._handle(Event(kind="result", result=result))
    click = types.SimpleNamespace(x=5, y=5)
    app._show(fresh)
    app.on_press(click)
    assert len(aimed) == 1
    app._show(older)
    app.on_press(click)
    assert len(aimed) == 1 and "no longer shows where the film is" in said[-1]
    app._show(fresh)
    app.on_nudge(1, 0.5)                          # what an aim ends in
    app.on_press(click)
    assert len(aimed) == 1, "the film has moved since"
    walked = prescan(3, number=4)                 # a walk's, the film moved on
    app._handle(Event(kind="result", result=walked))
    app._show(walked)
    app.on_press(click)
    assert len(aimed) == 1


def _unwalked_roll(tmp_path, done, settings=True):
    """A roll scanned without a walk, six frames asked for, `done` done and
    the rest failed -- as the session writes one: a roll that named no frames
    records no `wanted`, and is read as wanting every frame it recorded."""
    folder = tmp_path / "rolls" / "resume"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "resume", "numbering": "strip", "wanted": None,
        **({"settings": {"resolution": 1800, "prescan_resolution": 300,
                         "film": "negative", "start_at": 1, "frames": 6,
                         "only": None}}
           if settings else {}),
        "frames": [{"number": n, "done": n in done} for n in range(1, 7)]}),
        encoding="utf-8")
    return folder


def test_finishing_a_reopened_roll_skips_the_frames_it_has(window, monkeypatch,
                                                           tmp_path):
    """The Roll button carried a first frame and a count, so with 1, 2, 4
    and 5 of six done, "first frame 3" scanned 3 to 6: 4 and 5 again, their
    frameNN.tif replaced, at up to minutes a frame."""
    app, root = window
    app.calibrated = True
    said, asked, jobs = [], [], []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.open_roll(_unwalked_roll(tmp_path, done=(1, 2, 4, 5)))
    assert "the frames already done are skipped" in said[-1]
    assert (app.v_startat.get(), app.v_last.get()) == ("3", "6")
    app.v_dryrun.set(False)
    app.on_roll()
    assert jobs[0].only == (3, 6) and jobs[0].start_at == 3
    assert "Already scanned, and skipped: 4-5." in asked[0]


def test_finishing_a_reopened_duplicate_over_the_cap_scans_into_it(
        window, monkeypatch, tmp_path):
    """A roll typed longer than the cap is filed as 64 characters, and its
    Duplicate is `<that>-2`, 66. Reopened with no walk and finished with
    Roll, the folder name the box held was looked up as that name capped --
    another folder, so nothing was skipped and every frame was scanned again
    into a third."""
    import shutil

    from rps7200.session import NAME_CAP, roll_dir

    app, root = window
    app.calibrated = True
    asked, jobs = [], []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(app.session, "submit", jobs.append)
    typed = "Kodak Portra 400, the summer holiday at the coast, part two " * 2
    original = _unwalked_roll(tmp_path, done=(1, 2, 4, 5))
    capped = original.rename(roll_dir(app.session.rolls, typed))
    assert len(capped.name) <= NAME_CAP < len(typed)
    copy = gui.duplicate_name(capped)
    shutil.copytree(capped, copy)
    assert len(copy.name) > NAME_CAP
    app.open_roll(copy)
    app.v_dryrun.set(False)
    app.on_roll()
    assert jobs[0].out == str(copy)
    assert jobs[0].only == (3, 6) and jobs[0].start_at == 3
    assert "Already scanned, and skipped: 4-5." in asked[0]


def test_a_reopened_roll_finished_in_part_still_wants_what_is_left(
        window, monkeypatch, tmp_path):
    """Resumed with just the frames left, a roll that had named none came
    back wanting only those: stopped after frame 3 with 6 still to do, the
    browser called it finished and a reopen found nothing left to scan."""
    from conftest import StripScanner
    from rps7200.session import ScanSession

    app, root = window
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    jobs = []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    folder = _unwalked_roll(tmp_path, done=(1, 2, 4, 5))
    app.open_roll(folder)
    app.v_dryrun.set(False)
    app.on_roll()
    assert jobs[0].only == (3, 6)
    # The job the window handed over, run by a session on a strip that ends
    # at frame 4: frame 3 is scanned, and 6 is never reached.
    resumed = ScanSession(root=str(tmp_path / "library"),
                          rolls=app.session.rolls, verbose=False,
                          open_scanner=lambda: StripScanner(at=0, last=3))
    resumed.start()
    resumed.submit(jobs[0])
    resumed.shutdown()
    resumed.join(timeout=10)
    summary = gui.roll_summary(folder)
    assert summary["done"] == [1, 2, 3, 4, 5]
    assert summary["remaining"] == [6]
    assert gui.roll_cells(summary)[1] == "5 of 6"


def test_a_reopened_roll_with_no_settings_does_not_say_they_are_back(
        window, monkeypatch, tmp_path):
    app, root = window
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    app.v_startat.set("1")
    app.open_roll(_unwalked_roll(tmp_path, done=(1, 2), settings=False))
    assert "Its settings are back" not in said[-1]
    assert "records no settings" in said[-1]
    assert app.v_startat.get() == "1", "and it did not set the box"


def test_keys_and_aim_clicks_do_not_queue_work_while_the_scanner_works(window):
    """Only the buttons grey while the scanner works. The roll key queued a
    second roll behind the first, and an aim-click or a fine move queued a
    film move that ran wherever the job ended."""
    app, _root = window
    submitted = []
    app.session.submit = submitted.append
    app.busy = True
    app.calibrated = True
    app.on_roll()
    app.on_nudge(1, 0.5)
    app.on_move_frames(1)
    assert submitted == []


# -- the event pump survives a handler that fails -----------------------------


def test_one_failing_event_neither_stops_the_pump_nor_drops_the_rest(
        window, monkeypatch):
    """The pump booked its next tick on its last line, so one exception in any
    handler stopped it for good -- and `session.poll` had already drained the
    events behind the failing one, which were lost with it. The worker went on
    scanning and filing; the window showed nothing more, not even the end of
    the job, and an operator who took that for a hang and killed it abandoned
    the read in flight."""
    from rps7200.session import Event

    app, root = window
    real, handled = app._handle, []

    def handle(event):
        if event.text == "boom":
            raise RuntimeError("a handler that fails")
        handled.append(event.text)
        real(event)

    monkeypatch.setattr(app, "_handle", handle)
    for text in ("boom", "after it"):
        app.session._events.put(Event(kind="log", text=text))
    app._pump()
    assert "after it" in handled, "the rest of the batch was dropped"
    assert "could not handle" in app.log.get("1.0", "end")
    app.session._events.put(Event(kind="log", text="next tick"))
    deadline = time.monotonic() + 10
    while "next tick" not in handled and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert "next tick" in handled, "the pump stopped"


# -- a job handed over is a job running, for every way in ---------------------


def test_a_double_pressed_scan_queues_one_pass(window, monkeypatch):
    """The buttons grey when the worker reports the job it took, a tick or so
    after it was handed over; a double press inside that tick queued two
    passes -- minutes of scanner time and a second entry nobody asked for.
    The session holds the job from its hand-over (`_Held`)."""
    app, root = window
    app.calibrated = True
    held = _Held(app, monkeypatch)
    jobs = held.jobs
    app.on_scan()
    app.on_scan()
    app.on_prescan()
    assert len(jobs) == 1
    # Taken and finished: the next press is a new pass.
    held.taken("scanning")
    app.on_prescan()
    assert len(jobs) == 2
    # And a Stop drops a pass that has not begun, which is held no more.
    app.on_stop()
    app.on_scan()
    assert len(jobs) == 3


def test_a_job_stop_drops_before_it_starts_leaves_nothing_set(window,
                                                              tmp_path,
                                                              monkeypatch):
    """Stop is offered from the hand-over, so at launch -- the worker still
    opening the scanner -- it could drop a job the window had already set
    things for: a calibration counted as done, a walk as walking, a roll's
    estimate on screen. Nothing reported for the job, and all of it stayed:
    the next Scan went to the driver with no reference, and the next job's
    end ended a walk that never ran. The session says what it dropped, and
    the window sets each back -- not where a job of the same kind was
    handed over again before the drop was read."""
    import tkinter

    from rps7200.session import ScanSession

    app, root = window
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, **k: None)
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"),
                          reference=str(tmp_path / "shading2.npz"),
                          verbose=False)
    session.start = lambda: None                 # still opening the scanner
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True,
                           settings_path=tmp_path / "s2.json")

    def read():
        for event in session.poll():
            other._handle(event)

    try:
        other.on_calibrate("measure")
        other.v_dryrun.set(True)
        other.on_roll()
        assert other.calibrated and other._calibration_pending
        assert other._surveying and other._roll_wall_start is not None
        assert len(session.outstanding()) == 2
        other.on_stop()
        read()
        assert session.outstanding() == ()
        assert other.calibrated is False and not other._calibration_pending
        assert other.b_calibrate.cget("text") == "Calibrate"
        assert not other._surveying
        assert other._roll_wall_start is None and not other._roll_seeking
        assert other.v_roll_eta.get() == ""
        # Handed over again before the drop is read: the new one's.
        other.on_calibrate("measure")
        other.on_stop()
        other.on_calibrate("measure")
        read()
        assert other.calibrated and other._calibration_pending
    finally:
        other._alive = False
        top.destroy()


def test_a_roll_is_not_opened_while_the_scanner_works(window, monkeypatch,
                                                      tmp_path):
    """The busy check sat on the button that opens the browser, and the
    browser stays open: its Open replaced the survey mid-walk, the walk's
    later prescans joined the other roll's under the same numbers, and the
    walk's end pointed that sheet at the new folder."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    for working in ("busy", "_surveying"):
        setattr(app, working, True)
        app.open_roll(folder)
        setattr(app, working, False)
        assert app.survey == [] and app._sheet_roll is None, working
        assert app._loaded_roll is None, working
        assert "The scanner is working" in said[-1]


def test_a_folder_that_would_not_open_is_not_the_open_roll(window, monkeypatch,
                                                          tmp_path):
    """It was marked open before it was read, and so kept from Rename and
    Delete until the window restarted."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)
    broken = tmp_path / "rolls" / "broken"
    broken.mkdir(parents=True)
    (broken / "survey.json").write_text("{not json", encoding="utf-8")
    app.open_roll(broken)
    assert app._loaded_roll is None


def test_the_sheets_own_roll_and_one_still_filing_are_not_moved(window,
                                                               monkeypatch,
                                                               tmp_path):
    """Only a roll reopened from the browser was protected. The walk the open
    sheet belongs to could be renamed or deleted, and its commission then
    recreated the old folder empty and scanned into it; and a roll whose last
    frames were still with the writer could be moved under them."""
    app, root = window
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    walked = tmp_path / "rolls" / "walked"
    filing = tmp_path / "rolls" / "filing"
    other = tmp_path / "rolls" / "other"
    app._sheet_roll = walked

    class Behind:
        unsaved = None

        def ahead_of_disk(self):
            return True

    monkeypatch.setitem(app.session._manifests,
                        (filing / "roll.json").resolve(), Behind())
    for folder, refused in ((walked, "open in this window"),
                            (filing, "still being filed")):
        assert app._roll_is_busy([{"folder": folder, "roll": folder.name}],
                                 "Delete")
        assert refused in said[-1]
    assert not app._roll_is_busy([{"folder": other, "roll": "other"}], "Delete")


def test_a_calibration_is_not_queued_behind_a_running_job(window, monkeypatch):
    app, root = window
    jobs, said = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    app.busy = True
    app.on_calibrate("measure")
    assert jobs == [] and app.calibrated is False
    assert "The scanner is working" in said[-1]


def test_a_calibration_that_never_ran_does_not_leave_the_window_calibrated(
        window, monkeypatch):
    """The window calls itself calibrated as the job is queued, and only the
    "calibrated" event corrected it -- which a calibration a stop kept from
    running never sends. Every scan after it was then refused by the driver
    instead of being asked about here."""
    from rps7200.session import Event

    app, root = window
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    app.on_calibrate("measure")
    assert app.calibrated is True
    app._handle(Event(kind="state", text="calibrating", busy=True))
    app._handle(Event(kind="finished", text="stopped"))
    app._handle(Event(kind="state", text="idle", busy=False))
    assert app.calibrated is False
    assert app.b_calibrate.cget("text") == "Calibrate"


def test_a_move_and_a_pass_are_never_queued_one_behind_the_other(
        window, monkeypatch):
    """`busy` follows the worker's event, a tick after the job was taken. A
    move key or an aim-click in that tick after a Scan was queued behind it
    and moved the film once the pass had ended; and a Move's own start
    cleared the note that a Scan was waiting behind it, so once the Move had
    ended a second press queued a second pass. A pass queued behind a
    calibration, as it is meant to be, is still waiting while that runs."""
    from rps7200.session import Event

    app, root = window
    app.calibrated = True
    held = _Held(app, monkeypatch)
    jobs, taken = held.jobs, held.taken

    app.on_scan()
    app.on_move_frames(1)
    app.on_nudge(1, 0.5)
    assert [type(j).__name__ for j in jobs] == ["Scan"]
    taken("scanning")
    app.on_move_frames(1)
    app.on_scan()
    assert [type(j).__name__ for j in jobs] == ["Scan", "Move"]
    taken("moving")
    app.on_calibrate("measure")
    app.on_prescan()
    taken("calibrating", Event(kind="calibrated", done=1))
    app.on_scan()
    assert [type(j).__name__ for j in jobs] == [
        "Scan", "Move", "Calibrate", "Prescan"]
    taken("prescanning")
    app.on_scan()
    assert [type(j).__name__ for j in jobs] == [
        "Scan", "Move", "Calibrate", "Prescan", "Scan"]


def test_a_pass_pressed_with_no_scanner_leaves_the_window_usable(
        window, monkeypatch, tmp_path):
    """With no scanner on the bus the session closes at launch and nothing
    reads its queue. A pass pressed then was never taken, so it never said
    it had started, and the window counted it as about to for good: Open,
    Rename, Delete and Calibrate all answered that the scanner was working,
    where browsing and exporting rolls with the scanner off is ordinary use."""
    from rps7200.session import Event

    app, root = window
    app.calibrated = True
    jobs, said = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: False)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: None)
    app._handle(Event(kind="closed"))
    app.on_prescan()
    assert jobs == [] and not app._working(), "nothing would ever take it"
    for press in (app.on_prescan, app.on_scan, app.on_roll,
                  lambda: app.on_calibrate("measure")):
        press()
        assert "There is no scanner" in said[-1]
    app.on_move_frames(1)
    assert "no scanner" in app.log.get("1.0", "end")
    assert jobs == [] and not app._working()
    folder = _walked_folder(tmp_path, count=3)
    app.open_roll(folder)
    assert app._loaded_roll == folder
    app.sheet.top.destroy()


# -- presets -----------------------------------------------------------------


def test_a_hand_edited_preset_sets_only_what_a_preset_carries(window):
    """Choosing a preset set `v_<key>` for every key it held, so a
    `mono_channel` added by hand -- not a preset key -- reached the chooser
    unchecked, and a film type nobody offers went to the next roll."""
    app, root = window
    app.v_mono_channel.set("G")
    app.v_film.set("negative")
    app.presets["edited"] = {"dpi": "3600", "film": "kodachrome-ish",
                             "mono_channel": "I", "meter": "each"}
    app.v_preset.set("edited")
    app.on_preset_chosen()
    assert app.v_dpi.get() == "3600", "what a preset carries still arrives"
    assert app.v_meter.get() == "each"
    assert app.v_film.get() == "negative", "a film the chooser does not offer"
    assert app.v_mono_channel.get() == "G", "not a preset key at all"
    said = app.log.get("1.0", "end")
    assert "mono_channel='I'" in said and "film='kodachrome-ish'" in said


def test_a_preset_is_taken_key_by_key_against_its_choices():
    values, refused = gui.preset_values(
        {"film": gui.FILM_TYPES[0], "ir": True, "fast_ir": "maybe",
         "shading": "reuse", "expmode": "sometimes", "v_mono": 1})
    assert values == {"film": gui.FILM_TYPES[0], "ir": "1", "shading": "reuse"}
    assert sorted(refused) == ["expmode", "fast_ir", "v_mono"]


def test_a_preset_choice_arrives_as_the_choice_it_was_checked_as():
    """The check strips a hand edit's stray space, so `" negative"` passed --
    and then reached `v_film` with the space still on it, where no film
    lookup matched it."""
    film = gui.FILM_TYPES[0]
    values, refused = gui.preset_values(
        {"film": f" {film} ", "shading": "reuse\n", "fast_ir": "true",
         "dpi": "1800"})
    assert values == {"film": film, "shading": "reuse", "fast_ir": "1",
                      "dpi": "1800"}
    assert refused == []


# -- a black-and-white picture is exported as it is viewed -------------------


def _bw_result(seq, entry=None, film="bw"):
    from rps7200.session import Result
    return Result(seq=seq, kind="scan", label=f"pass {seq}",
                  image=np.full((40, 60, 3), 20000 + seq, np.uint16),
                  meta={"film": film}, entry=entry)


def test_a_black_and_white_picture_is_exported_as_it_is_viewed(window):
    """Stefan's choice, 2026-10-04: the view switch on a B&W picture decides
    what its Save As and Export write -- still his to change after the
    scan."""
    from rps7200.mono import MONO_CHANNEL
    app, root = window
    bw = _bw_result(1)
    app._add_result(bw)
    app._show(bw)
    for view, expected in (("G", (True, "G")), ("R", (True, "R")),
                           ("B", (True, "B")), ("MONO", (True, MONO_AVERAGE)),
                           ("RGB", (False, MONO_CHANNEL))):
        app.v_channel.set(view)
        app._on_view_chosen()
        assert app._mono_for(bw) == expected, view


def test_the_view_a_picture_was_left_on_comes_back_with_it(window):
    app, root = window
    first, second = _bw_result(1), _bw_result(2)
    for r in (first, second):
        app._add_result(r)
    app._show(first)
    app.v_channel.set("G")
    app._on_view_chosen()
    app._show(second)
    app.v_channel.set("MONO")
    app._on_view_chosen()
    app._show(first)
    assert app.v_channel.get() == "G", "what Save As will write is on screen"
    app._show(second)
    assert app.v_channel.get() == "MONO"


def test_a_colour_picture_keeps_its_export_whatever_it_is_viewed_as(window):
    """One plane of a colour negative is a different photograph."""
    app, root = window
    app.v_film.set("negative")
    app._sync_film()
    colour = _bw_result(1, film="negative")
    app._add_result(colour)
    app._show(colour)
    app.v_channel.set("R")
    app._on_view_chosen()
    assert getattr(colour, "export_mix", None) is None
    assert app._mono_for(colour)[0] is False


def test_a_rolls_export_follows_a_frame_viewed_in_this_window(window, tmp_path):
    """Export builds its own items from the library; the one entry they
    share is how a frame's view reaches it, ahead of the roll's setting."""
    app, root = window
    frame = _bw_result(1, entry=tmp_path / "entry")
    app._add_result(frame)
    app._show(frame)
    app.v_channel.set("B")
    app._on_view_chosen()
    item = types.SimpleNamespace(meta={"film": "bw"}, entry=tmp_path / "entry",
                                 mono=True, mono_channel=MONO_AVERAGE)
    assert app._mono_for(item) == (True, "B")
    other = types.SimpleNamespace(meta={"film": "bw"}, entry=tmp_path / "x",
                                  mono=True, mono_channel=MONO_AVERAGE)
    assert app._mono_for(other) == (True, MONO_AVERAGE), "the roll's own"


# -- a calibration that no longer matches the scanner -------------------------


def test_a_moved_reference_offers_a_calibration_or_scanning_anyway(window):
    """The session refused a scan because the scanner's gain has moved off
    the reference; the window says so and offers both ways on. Stefan's
    choice, 2026-10-04."""
    from rps7200.session import Event
    app, root = window
    app.session.reference_moved = {"now": {"gain": [37, 33, 21]},
                                   "reference": {"gain": [39, 33, 21]}}
    app._handle(Event(kind="reference_moved",
                      text="the scanner's gain is 37/33/21 ... anyway"))
    root.update()
    prompt = app._calibrate_prompt
    assert prompt is not None and prompt.title() == "Calibrate again"
    shown = [w.cget("text") for w in gui._descendants(prompt)
             if w.winfo_class() in ("TLabel", "TButton")]
    assert any("37/33/21" in t for t in shown)
    assert "Calibrate now" in shown and "Use it anyway" in shown
    assert "Use the cached one" not in shown, "the cache is the stale one"
    _prompt_button(app, "Use it anyway").invoke()
    root.update()
    assert app._calibrate_prompt is None
    assert app.session.accepted_mismatch == app.session.reference_moved


def test_save_all_and_export_names_say_the_films_process(tmp_path):
    frame = types.SimpleNamespace(seq=1, kind="frame", number=3, meta={
        "resolution_dpi": 1800, "channels": 4, "film": "positive"})
    assert gui.batch_name(frame, "tiff") == "frame03_e6_1800dpi_ir.tif"
    entry = tmp_path / "e"
    entry.mkdir()
    (entry / "scan.json").write_text(json.dumps(
        {"scan": {"resolution_dpi": 3600, "channels": 3, "film": "bw"}}),
        encoding="utf-8")
    (entry / "scan.tif").write_bytes(b"")      # filed: not a preview
    summary = {"folder": tmp_path / "nowhere", "entries": {1: entry},
               "film": "negative", "settings": {"resolution": 3600}}
    item = gui.roll_exports(summary)[0]
    assert gui.batch_name(item, "tiff") == "frame01_bw_3600dpi.tif", (
        "the entry's own film, not the roll's setting")


# --- other programs: the rolls, the window's own readers ---------------------


def _told(monkeypatch):
    """Every message box shown, as ``(title, message)``; and every question
    answered yes, the new name "holiday"."""
    told = []
    for kind in ("showinfo", "showerror"):
        monkeypatch.setattr(gui.messagebox, kind,
                            lambda t, m, **k: told.append((t, m)))
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(gui.simpledialog, "askstring",
                        lambda *a, **k: "holiday")
    return told


def _until_copied(app, root, timeout: float = 20.0) -> None:
    """Pump the window until its Duplicate has ended: the copy runs on a
    thread under a modal dialog, and says it has ended through the pump."""
    import time

    deadline = time.monotonic() + timeout
    while app._copy_stop is not None:
        assert time.monotonic() < deadline, "the copy never ended"
        root.update()
        time.sleep(0.01)


def test_a_roll_another_program_writes_into_is_not_renamed_deleted_or_copied(
        window, tmp_path, monkeypatch):
    """`_roll_is_busy` saw this window only: a `tools/scan_roll.py` scanning
    into the folder had it renamed, deleted or copied half-written from
    under it. Each is refused now, naming it, and nothing changes."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    told = _told(monkeypatch)
    with HeldElsewhere(locks.ROLL, folder, what="tools/scan_roll.py") as other:
        app.on_rename_roll(gui.roll_summary(folder))
        app.on_delete_rolls([gui.roll_summary(folder)])
        app.on_duplicate_roll(gui.roll_summary(folder))
    assert [title for title, _ in told] == ["Rename", "Delete", "Duplicate"]
    for _title, said in told:
        assert said.startswith("walk is in use by tools/scan_roll.py (")
        assert f"pid {other.pid}" in said and "Nothing was changed." in said
    assert sorted(p.name for p in tmp_path.iterdir()
                  if p.name.startswith(("walk", "holiday"))) == ["walk"]


def test_a_roll_another_window_shows_is_not_renamed_or_deleted(
        window, tmp_path, monkeypatch):
    """A demo window showing a real walk: renamed or deleted under it, its
    sheet's next roll scanned into a folder that had gone. Copying it is
    reading it, and goes ahead -- holding it as written into meanwhile."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    told = _told(monkeypatch)
    during = []
    real_copy = gui.shutil.copytree

    def copying(source, target, *a, **k):
        during.append([sorted(h["what"] for h in locks.holders(kind, path))
                       for kind, path in ((locks.ROLL, source),
                                          (locks.ROLL_VIEW, source),
                                          (locks.ROLL, target))])
        return real_copy(source, target, *a, **k)

    monkeypatch.setattr(gui.shutil, "copytree", copying)
    with HeldElsewhere(locks.ROLL_VIEW, folder, exclusive=False,
                       what="the demo window") as other:
        app.on_rename_roll(gui.roll_summary(folder))
        app.on_delete_rolls([gui.roll_summary(folder)])
        app.on_duplicate_roll(gui.roll_summary(folder))
        _until_copied(app, root)
    assert [title for title, _ in told] == ["Rename", "Delete"]
    for _title, said in told:
        assert said.startswith("walk is open in the demo window (")
        assert f"pid {other.pid}" in said
        # What lets go of it: closing the sheet there does not, and "close
        # it there first" asked for what the window has no way to do.
        assert "Open another roll there, or quit it, first" in said
        assert "closing its contact sheet does not let go of it" in said
        assert "close it there first" not in said
        assert said.endswith("\n\nNothing was changed.")
    assert folder.exists() and (tmp_path / "walk-2").is_dir()
    # Held through the copy -- the source as written into and as shown, by
    # this window beside the other, and the copy as written into -- and let
    # go after it.
    assert during == [[["the demo window"],
                       ["the demo window", "the demo window"],
                       ["the demo window"]]]
    for kind, path in ((locks.ROLL, folder), (locks.ROLL_VIEW, folder),
                       (locks.ROLL, tmp_path / "walk-2")):
        assert locks.holders(kind, path) == []


def test_carrying_a_walk_into_a_folder_another_program_holds_is_refused(
        tmp_path):
    from conftest import HeldElsewhere

    from rps7200 import locks

    source = _walked_folder(tmp_path, count=2)
    target = tmp_path / "rolls" / "walk"
    (tmp_path / "rolls").mkdir()
    with HeldElsewhere(locks.ROLL, target, what="tools/scan_roll.py"):
        with pytest.raises(locks.Refused, match="tools/scan_roll.py"):
            gui.carry_walk(source, target, rolls=tmp_path / "rolls")
    assert not target.exists()
    assert gui.carry_walk(source, target, rolls=tmp_path / "rolls")
    assert (target / "survey.json").exists()
    assert locks.holders(locks.ROLL, target) == []


def test_the_roll_open_here_is_held_as_shown_until_another_is_opened(
        window, tmp_path, monkeypatch):
    """Rename and Delete in another window are refused while this one shows
    the roll; opening another lets go of the first."""
    import os
    import shutil

    from conftest import HeldElsewhere, HolderRefused

    from rps7200 import locks

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    first = _walked_folder(tmp_path, count=3)
    app.open_roll(first)
    assert [h["pid"] for h in locks.holders(locks.ROLL_VIEW, first)] \
        == [os.getpid()]
    # Another window showing it too sits beside it; a Rename there is
    # refused, naming this one.
    with HeldElsewhere(locks.ROLL_VIEW, first, exclusive=False):
        pass
    with pytest.raises(HolderRefused, match=rf"is in use by the demo window "
                                            rf"\(.*\bpid {os.getpid()}\b"):
        with HeldElsewhere(locks.ROLL_VIEW, first, exclusive=True):
            pass
    second = shutil.copytree(first, tmp_path / "second")
    app._close_sheet()
    app.open_roll(second)
    assert locks.holders(locks.ROLL_VIEW, first) == []
    assert [h["what"] for h in locks.holders(locks.ROLL_VIEW, second)] \
        == ["the demo window"]
    app._sheet_roll = app._loaded_roll = None
    assert locks.holders(locks.ROLL_VIEW, second) == []


def test_compaction_at_close_waits_for_the_windows_readers(window,
                                                           monkeypatch):
    """Save all, Export and the full-resolution view hold an entry's files
    open; on Windows a compaction swapping them gave up part way. The
    session waits for them -- with the device already closed -- through
    the window's `_wait_for_readers`."""
    import threading
    from pathlib import Path
    from types import SimpleNamespace

    app, root = window
    assert app.session.before_compact == app._wait_for_readers
    reading = threading.Event()
    let_go = threading.Event()

    def slow_load(entry):
        reading.set()
        let_go.wait(10)
        raise OSError("read")

    monkeypatch.setattr(gui.library, "load", slow_load)
    app._load_full(SimpleNamespace(seq=7, entry=Path("an entry")))
    assert reading.wait(5)
    app._start_writing(lambda: let_go.wait(10), "export-rolls")
    waited = threading.Event()
    waiter = threading.Thread(
        target=lambda: (app._wait_for_readers(), waited.set()))
    waiter.start()
    assert not waited.wait(0.3), "it did not wait for them"
    let_go.set()
    assert waited.wait(10)
    waiter.join(5)


def test_approved_json_is_not_written_into_a_roll_another_program_holds(
        tmp_path):
    """A `tools/scan_roll.py` scanning into the folder: the decisions are not
    written in beside it, and the roll that follows is refused before the
    film moves (`ScanSession._hold_roll`)."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    folder = tmp_path / "a-roll"
    said = []
    with HeldElsewhere(locks.ROLL, folder, what="tools/scan_roll.py") as other:
        gui.ScannerGui._write_approved(_approving(tmp_path, said),
                                       (Approved(1, rotation=90),), folder)
    assert not (folder / "approved.json").exists()
    # Said as what follows: no "scanning anyway" ahead of a refused roll.
    [line] = said
    assert line.startswith(f"{folder} is in use by tools/scan_roll.py (")
    assert f"pid {other.pid}" in line
    assert line.endswith("approved.json not written, and the roll will be "
                         "refused unless that lets go of it first")
    assert locks.holders(locks.ROLL, folder) == []


# --- a walk read back one prescan at a time ------------------------------------


def test_a_damaged_prescan_costs_its_frame_at_most_not_the_walk(tmp_path):
    """One prescan a power cut cut short refused the whole walk -- four
    minutes of transport -- with an error the sheet could not open past.
    It is made again from the walk's own entry for it, and said; and
    approved.json naming another entry as that frame's reference changes
    nothing about which picture stands in."""
    from conftest import walk_with_entries

    folder, lib, manifest, pictures = walk_with_entries(tmp_path,
                                                        numbers=(1, 2, 3))
    whole = (folder / "prescan02.tif").read_bytes()
    (folder / "prescan02.tif").write_bytes(whole[:len(whole) // 2])
    (folder / "approved.json").write_text(json.dumps({
        "numbering": "strip",
        "frames": [{"number": 2, "reference_entry":
                    manifest["frames"][0]["prescan_entry"]}]}),
        encoding="utf-8")
    said = []
    survey = gui.read_survey(folder, say=said.append, library_root=lib)
    shown = {r.number: r.image for r in survey["results"]}
    assert sorted(shown) == [1, 2, 3]
    assert np.array_equal(shown[2], pictures[2]), "another frame's picture"
    own = manifest["frames"][1]["prescan_entry"]
    # And the reference is the entry the picture was made from: an approval
    # made on it names that, not what approved.json said before.
    [frame2] = [r for r in survey["results"] if r.number == 2]
    assert frame2.entry is not None and frame2.entry.name == own
    assert any(line.startswith("frame 2: ") and "prescan02.tif" in line
               and f"shown from its library entry {own}" in line
               for line in said)


def test_a_damaged_prescan_with_no_entry_to_stand_in_is_left_off(tmp_path):
    from conftest import walk_with_entries

    folder, lib, manifest, _pictures = walk_with_entries(
        tmp_path, numbers=(1, 2, 3), tags={2: ["before"]})
    (folder / "prescan02.tif").write_bytes(b"II*\x00\x08\x00")
    said = []
    survey = gui.read_survey(folder, say=said.append, library_root=lib)
    assert [r.number for r in survey["results"]] == [1, 3]
    assert any("frame 2: " in line and "left off the sheet" in line
               and "from before an aim" in line for line in said)


# --- Duplicate: reserved, copied under a dialog, never half a roll ----------


def _gated_copy(app, monkeypatch):
    """`copy_roll` made to wait until the test lets it go -- in the module
    the window was built from, which `load_tool` loads afresh for each."""
    import threading

    gate = threading.Event()
    own = app.on_duplicate_roll.__globals__
    real = own["copy_roll"]

    def held(*a, **k):
        assert gate.wait(20), "the test never let the copy go"
        return real(*a, **k)

    monkeypatch.setitem(own, "copy_roll", held)
    return gate


def _messages(monkeypatch):
    told = _told(monkeypatch)
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda t, m, **k: told.append((t, m)))
    return told


def test_a_duplicate_is_reserved_marked_and_listed_only_once_whole(
        window, tmp_path, monkeypatch):
    """Copied straight into a visible `<name>-2`, a copy that failed on a
    large frame left a folder with frames and no walk, or with no manifest
    at all -- one the browser could not list or delete. The name is
    Duplicate's from the start, marked, and lists as a roll only once the
    copy is whole."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    told = _messages(monkeypatch)
    gate = _gated_copy(app, monkeypatch)
    app.on_duplicate_roll(gui.roll_summary(folder))
    target = tmp_path / "walk-2"
    # Nothing renames or deletes the source meanwhile, whatever reaches the
    # window around its dialog.
    app.on_rename_roll(gui.roll_summary(folder))
    assert told[-1] == ("Rename", "A roll is being copied. Wait for the "
                        "copy to finish, or stop it.")
    assert folder.is_dir()
    assert (target / gui.library.INCOMPLETE).exists()
    assert gui.duplicate_name(folder).name == "walk-3"
    assert gui.roll_summary(target) is None
    assert [gui.Path(s["folder"]).name for s in gui.rolls_on_disk(tmp_path)] == [
        "walk"]
    dialog = app._copy_dialog
    assert dialog is not None and dialog.winfo_exists()
    assert "Stop" in [w.cget("text") for w in gui._descendants(dialog)
                      if w.winfo_class() == "TButton"]
    gate.set()
    _until_copied(app, root)
    assert not dialog.winfo_exists()
    assert not (target / gui.library.INCOMPLETE).exists()
    assert sorted(p.name for p in target.iterdir()) == sorted(
        p.name for p in folder.iterdir())
    assert gui.roll_summary(target) is not None
    assert "duplicated walk to walk-2" in app.log.get("1.0", "end")


def test_a_duplicate_writes_the_records_last_and_its_marker_after_them(
        window, tmp_path, monkeypatch):
    """What makes a copy a roll goes in only once what it names is there."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    (folder / "approved.json").write_text('{"frames": []}', encoding="utf-8")
    (folder / "roll.json").write_text('{"frames": []}', encoding="utf-8")
    _messages(monkeypatch)
    order = []
    real_copy, real_unmark = gui.shutil.copy2, gui.library._unmark
    monkeypatch.setattr(gui.shutil, "copy2", lambda s, d, **k: (
        order.append(gui.Path(s).name), real_copy(s, d, **k))[-1])
    monkeypatch.setattr(gui.library, "_unmark", lambda p: (
        order.append("unmark"), real_unmark(p))[-1])
    app.on_duplicate_roll(gui.roll_summary(folder))
    _until_copied(app, root)
    frames = [n for n in order if n.startswith("prescan")]
    assert len(frames) == 3
    assert order[len(frames):] == ["approved.json", "roll.json",
                                   "survey.json", "unmark"]


def test_a_duplicate_leaves_out_partial_writes_and_refused_passes(
        window, tmp_path, monkeypatch):
    """`unfiled/` holds passes the library refused, which exist nowhere
    else: copied, a move into the library filed each twice. And a write
    beside a kill left is no file of the roll's."""
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    (folder / ".prescan01.1a-2.part.tif").write_bytes(b"half")
    kept = folder / "unfiled" / "20261009T000000Z_x"
    kept.mkdir(parents=True)
    (kept / "scan.json").write_text("{}", encoding="utf-8")
    asked = []
    _messages(monkeypatch)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    app.on_duplicate_roll(gui.roll_summary(folder))
    _until_copied(app, root)
    target = tmp_path / "walk-2"
    assert not (target / "unfiled").exists()
    assert not list(target.glob(".*"))
    assert "The 1 pass the library refused" in asked[0]
    assert "is not copied" in asked[0]


@pytest.mark.parametrize("how", ["fails", "stopped"])
def test_a_duplicate_that_does_not_finish_leaves_nothing_behind(
        window, tmp_path, monkeypatch, how):
    app, root = window
    folder = _walked_folder(tmp_path, count=4)
    told = _messages(monkeypatch)
    if how == "fails":
        real, calls = gui.shutil.copy2, []

        def full(src, dst, **k):
            calls.append(src)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            return real(src, dst, **k)

        monkeypatch.setattr(gui.shutil, "copy2", full)
        app.on_duplicate_roll(gui.roll_summary(folder))
    else:
        gate = _gated_copy(app, monkeypatch)
        app.on_duplicate_roll(gui.roll_summary(folder))
        [stop] = [w for w in gui._descendants(app._copy_dialog)
                  if w.winfo_class() == "TButton"]
        stop.invoke()
        gate.set()
    _until_copied(app, root)
    assert not (tmp_path / "walk-2").exists()
    said = app.log.get("1.0", "end")
    assert "nothing was left behind" in said
    if how == "fails":
        assert "No space left on device" in said
        assert told and told[-1][0] == "Duplicate"
    else:
        assert "the copy was stopped" in said
        assert not [t for t in told if t[0] == "Duplicate"], "a box for a Stop"
    assert gui.roll_summary(folder) is not None, "the source is untouched"


@pytest.mark.parametrize("held", ["a record", "the folder", "nothing"])
def test_a_duplicate_failing_among_its_records_never_lists_as_a_roll(
        window, tmp_path, monkeypatch, held):
    """Removed by `rmtree(ignore_errors=True)`, which goes on past a file
    it cannot delete -- and NTFS lists `INCOMPLETE` before `roll.json`: a
    record a reader held outlived the marker, and half a copy listed as a
    roll. The records go first and the marker last, kept while anything
    else is left; and "marked" is said only of a folder that is."""
    import os

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    (folder / "approved.json").write_text('{"frames": []}', encoding="utf-8")
    (folder / "roll.json").write_text('{"frames": []}', encoding="utf-8")
    target = tmp_path / "walk-2"
    told = _messages(monkeypatch)
    real_copy, real_unlink, real_rmdir = (gui.shutil.copy2, os.unlink,
                                          os.rmdir)

    def full(src, dst, **k):
        if gui.Path(src).name == "survey.json":
            raise OSError(28, "No space left on device")
        return real_copy(src, dst, **k)

    def unlink(path, *a, **k):
        if held == "a record" and gui.Path(path) == target / "roll.json":
            raise PermissionError(13, "held by a reader", str(path))
        return real_unlink(path, *a, **k)

    def rmdir(path, *a, **k):
        if held == "the folder" and gui.Path(path) == target:
            raise PermissionError(13, "held by a reader", str(path))
        return real_rmdir(path, *a, **k)

    monkeypatch.setattr(gui.shutil, "copy2", full)
    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "rmdir", rmdir)
    app.on_duplicate_roll(gui.roll_summary(folder))
    _until_copied(app, root)
    assert gui.roll_summary(target) is None, "half a copy listed as a roll"
    assert [gui.Path(s["folder"]).name
            for s in gui.rolls_on_disk(tmp_path)] == ["walk"]
    said = app.log.get("1.0", "end")
    assert "No space left on device" in said
    left = [item for item in gui.library.leftovers(
        tmp_path / "library", rolls=tmp_path) if item.path == target]
    if held == "a record":
        assert (target / gui.library.INCOMPLETE).exists()
        assert (target / "roll.json").exists()
        assert not (target / "approved.json").exists(), "the records first"
        assert [item.kind for item in left] == ["roll-copy"]
        assert "a Duplicate that did not finish" in left[0].said
        assert f"is left part-copied, marked {gui.library.INCOMPLETE}" in said
    elif held == "the folder":
        assert target.is_dir() and not list(target.iterdir())
        assert left == []
        assert f"{target} is left part-copied -- delete it" in said
        assert "marked" not in said
    else:
        assert not target.exists()
        assert "nothing was left behind" in said and "marked" not in said
    assert told and told[-1][0] == "Duplicate"
    assert gui.roll_summary(folder) is not None, "the source is untouched"


def test_quit_during_a_duplicate_stops_it_and_waits_for_it(
        window, tmp_path, monkeypatch):
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    _messages(monkeypatch)
    gate = _gated_copy(app, monkeypatch)
    app.on_duplicate_roll(gui.roll_summary(folder))
    waits = []
    monkeypatch.setattr(app, "_wait_to_quit", lambda: waits.append(1))
    app.on_close()
    assert app._copy_stop is not None and app._copy_stop.is_set()
    assert waits == [1]
    assert any(t.name == "duplicate-roll" and t.is_alive()
               for t in app._writing), "Quit waits for the writers"
    gate.set()
    _until_copied(app, root)
    assert not (tmp_path / "walk-2").exists()


def test_a_folder_a_duplicate_left_marked_is_no_roll(tmp_path):
    folder = _walked_folder(tmp_path, count=2)
    assert gui.roll_summary(folder) is not None
    (folder / gui.library.INCOMPLETE).write_text("{}", encoding="utf-8")
    assert gui.roll_summary(folder) is None
    assert gui.rolls_on_disk(tmp_path) == []


def test_a_duplicate_too_big_for_the_disk_is_refused_before_anything(
        window, tmp_path, monkeypatch):
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    told = _messages(monkeypatch)
    monkeypatch.setitem(app.on_duplicate_roll.__globals__, "check_space",
                        lambda needs, factor=1.0: (
                            ["0.1 GB free on the disk holding it"], []))
    app.on_duplicate_roll(gui.roll_summary(folder))
    assert told == [("Duplicate", "Not enough room to copy walk: 0.1 GB free "
                     "on the disk holding it.\n\nNothing was copied.")]
    assert not (tmp_path / "walk-2").exists()


@pytest.mark.parametrize("queued_on", ["this disk", "another disk"])
def test_a_duplicate_counts_what_the_writer_has_still_to_file(
        window, tmp_path, monkeypatch, queued_on):
    """Every job counts the frames the writer has queued and not yet
    written (`ScanSession._room`); Duplicate read the disk alone, and
    copying a roll while the last one's 3600 dpi frames were still being
    filed to the same disk could fill it part way. Only the copy's own
    disk counts: a library elsewhere is no reason to refuse it."""
    import shutil
    from collections import namedtuple

    from rps7200 import storage

    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    told = _messages(monkeypatch)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    need = gui.roll_copy_bytes(folder)
    filing = 3 * 10**9
    elsewhere = tmp_path / "elsewhere"
    queued = [(tmp_path if queued_on == "this disk" else elsewhere, filing)]
    monkeypatch.setattr(app.session, "queued_needs", lambda: list(queued),
                        raising=False)
    usage = namedtuple("usage", "total used free")
    # Room for the copy and the reserve; not for the frames as well.
    free = need + storage.SPACE_RESERVE + filing // 2
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda where: usage(10**13, 0, free))
    real_device = storage._device
    monkeypatch.setattr(storage, "_device", lambda where: (
        "elsewhere" if gui.Path(where) == elsewhere
        else real_device(where)))
    elsewhere.mkdir()
    app.on_duplicate_roll(gui.roll_summary(folder))
    target = tmp_path / "walk-2"
    if queued_on == "this disk":
        [(title, said)] = told
        assert title == "Duplicate"
        assert said.startswith("Not enough room to copy walk: ")
        assert "Part of that is a scan just taken, still being filed: try " \
               "again once it has landed." in said
        assert said.endswith("\n\nNothing was copied.")
        assert not target.exists(), "reserved for a copy refused"
    else:
        _until_copied(app, root)
        assert gui.roll_summary(target) is not None
        assert not [t for t in told if t[0] == "Duplicate"], told


# --- Quit with settings that will not save ---------------------------------


def _settings_refused(monkeypatch, times: int = 10**6):
    """`settings.save` failing ``times`` times, then saving."""
    real, calls = gui.settings.save, []

    def save(values, where=None, why=None):
        calls.append(1)
        if len(calls) <= times:
            if why is not None:
                why.append("OSError: [Errno 28] No space left on device")
            return None
        return real(values, where, why=why)

    monkeypatch.setattr(gui.settings, "save", save)
    return calls


def test_quit_asks_about_settings_first_and_cancel_costs_nothing(
        window, tmp_path, monkeypatch):
    """For a walk not yet commissioned the settings file is the only record
    of its sheet's decisions. Asked after the "still working" question, a
    Cancel left a window that stopped every job it started; asked first,
    keeping the window open stops nothing and dismisses nothing."""
    from rps7200.session import Event

    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    folder = _walked_folder(tmp_path, count=3)
    app.open_roll(folder)
    sheet = app.sheet
    _settings_refused(monkeypatch)
    asked, stops = [], []
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m))      # Cancel
    monkeypatch.setattr(app, "_wait_to_quit",
                        lambda: pytest.fail("quit anyway"))
    held = _Held(app, monkeypatch)
    monkeypatch.setattr(app.session, "request_stop", lambda: stops.append(1))
    queued = object()
    held.held.append(queued)
    app.busy = True
    app.on_close()
    [question] = asked
    assert "could not be saved (OSError: [Errno 28] No space left on " \
           "device)" in question
    assert "the contact sheet's decisions for walk" in question
    assert "Cancel: keep the window open" in question
    assert not app.closing and sheet.alive()
    assert stops == [] and held.held == [queued]
    assert not app.session.halted
    app._handle(Event(kind="state", text="prescanning", busy=True))
    assert stops == [], "a job started after Cancel was stopped"
    sheet.top.destroy()


def test_quit_tries_again_until_the_settings_go(window, monkeypatch):
    app, root = window
    calls = _settings_refused(monkeypatch, times=1)
    answers = iter([True])                                   # Yes: try again
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: next(answers))
    waits = []
    monkeypatch.setattr(app, "_wait_to_quit", lambda: waits.append(1))
    app.on_close()
    assert len(calls) >= 2 and waits == [1] and app.closing
    assert gui.settings.load(app._settings_path)["output"] == app.v_outdir.get()


def test_quit_without_the_settings_goes_on(window, monkeypatch):
    app, root = window
    _settings_refused(monkeypatch)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: False)               # No
    waits = []
    monkeypatch.setattr(app, "_wait_to_quit", lambda: waits.append(1))
    app.on_close()
    assert waits == [1] and app.closing


@pytest.mark.parametrize("console_closed", [False, True])
def test_an_interrupt_with_settings_that_will_not_save_writes_the_sheet_down(
        window, tmp_path, monkeypatch, console_closed):
    """Nobody to ask: whoever sent it may not be at the window, and a closed
    console's process is ended about 5 s on. The decisions go to a log,
    with the reason, where a person can find them."""
    app, root = window
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("asked the operator"))
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    folder = _walked_folder(tmp_path, count=3)
    app.open_roll(folder)
    app.sheet._rotate(3, 180)
    _settings_refused(monkeypatch)
    app.on_interrupt(console_closed=console_closed)
    log = tmp_path / "logs" / "window-closed.log"
    [line] = log.read_text(encoding="utf-8").splitlines()
    assert ("its console was closed" if console_closed
            else "interrupted (Ctrl-C or SIGTERM)") in line
    assert "No space left on device" in line
    sheet = json.loads(line.split("decisions were: ", 1)[1])
    assert sheet["walk"]["rotations"]["3"] == 180


def test_an_interrupt_whose_settings_go_writes_no_log(window, tmp_path,
                                                      monkeypatch):
    app, root = window
    monkeypatch.setattr(app, "_wait_to_quit", lambda: None)
    app.on_interrupt()
    assert not (tmp_path / "logs" / "window-closed.log").exists()


# --- what earlier runs left, said as the window opens --------------------------


def _leftovers_said(app, root) -> str:
    """The window's log once its look for leftovers has ended."""
    import threading
    import time

    for thread in [t for t in threading.enumerate() if t.name == "leftovers"]:
        thread.join(10)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        said = app.log.get("1.0", "end")
        if "left behind" in said:
            return said
        time.sleep(0.02)
    return app.log.get("1.0", "end")


def test_the_window_says_in_one_line_what_was_left_behind(window, tmp_path):
    app, root = window
    assert "left behind" not in _leftovers_said(app, root), "a clean library"
    entry = tmp_path / "library" / "20261009T000000Z_plain"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}", encoding="utf-8")
    (entry / "raw.bin").write_bytes(b"\x00" * 8)
    copy = tmp_path / "rolls" / "walk-2"
    copy.mkdir(parents=True)
    (copy / gui.library.INCOMPLETE).write_text("{}", encoding="utf-8")
    app._look_for_leftovers()
    said = _leftovers_said(app, root)
    lines = [line for line in said.splitlines() if "left behind" in line]
    assert len(lines) == 1
    assert "library: 1 entry still plain" in lines[0]
    assert "rolls: 1 part-copied roll folder" in lines[0]
    assert "`make verify` says what clears each" in lines[0]


def test_a_finished_duplicate_whose_marker_will_not_go_is_kept(
        window, tmp_path, monkeypatch):
    """A reader holding the marker past `_unmark`'s waits is no reason to
    throw away everything a long copy wrote: kept whole, and said."""
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    told = _messages(monkeypatch)
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: told.append((t, m)))

    def held(path):
        raise PermissionError("held by a reader")

    monkeypatch.setattr(gui.library, "_unmark", held)
    app.on_duplicate_roll(gui.roll_summary(folder))
    _until_copied(app, root)
    target = tmp_path / "walk-2"
    assert (target / "survey.json").exists()
    assert (target / gui.library.INCOMPLETE).exists()
    assert told[-1][0] == "Duplicate"
    assert "the copy is whole, and lists as a roll once that file is " \
           "deleted" in told[-1][1]
    # And `verify` and the start-up line agree: the marker is to go, not
    # the copy the window has just called whole.
    marker = json.loads((target / gui.library.INCOMPLETE).read_text(
        encoding="utf-8"))
    assert marker["records"] == ["survey.json"]
    [left] = [item for item in gui.library.leftovers(
        tmp_path / "library", rolls=tmp_path) if item.path == target]
    assert left.kind == "roll-copied" and not left.problem
    assert "the copy finished" in left.said and "delete that file" in left.said
    (target / gui.library.INCOMPLETE).unlink()
    assert gui.roll_summary(target) is not None


# -- the window's kept log ------------------------------------------------------


def _kept(app) -> str:
    """What the window's kept log holds so far."""
    assert app._kept.path is not None, "nothing was kept"
    return app._kept.path.read_text(encoding="utf-8")


def test_every_line_the_window_says_is_kept(window, tmp_path):
    """The log pane was the only account of a session, and went with the
    window. Each line it shows is kept in `logs/pane-<UTC start>.log` beside
    the window's settings, with its UTC time, readable while the window is
    still open; and the file begins with which code, folders and setup."""
    import re

    app, root = window
    app._say("a line worth keeping")
    path = app._kept.path
    assert path.parent == tmp_path / "logs"
    assert re.fullmatch(r"pane-\d{8}T\d{6}Z(-\d+)?\.log", path.name)
    text = _kept(app)
    assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z a line worth "
                     r"keeping$", text, re.M)
    first = text.splitlines()[0]
    assert "Reflecta RPS 7200 demo window, pid" in first
    assert str((tmp_path / "library").resolve()) in text \
        or str(tmp_path / "library") in text
    assert "protocol revision" in text and "setup: {" in text


def test_job_boundaries_and_tracebacks_are_kept(window):
    """What the pane does not show, and the file exists to explain
    afterwards: where each job started and ended, and every traceback -- a
    handler the pump guards, and a Tk callback that raised, which Tk only
    printed to a stderr nobody reads."""
    from rps7200.session import Event, Scan

    app, root = window
    app._handle(Event(kind="state", text="scanning at 600 dpi RGB",
                      busy=True, done=7, job=Scan()))
    app._handle(Event(kind="finished", text="all done"))
    app._handle(Event(kind="state", text="idle", busy=False))

    def broken(*_a):
        raise ValueError("a handler that breaks")

    app._safely("a test event", broken)
    root.report_callback_exception(
        RuntimeError, RuntimeError("a callback that breaks"), None)
    text = _kept(app)
    assert "job 7: scanning at 600 dpi RGB" in text
    assert "job 7 ended: all done" in text
    assert "ValueError: a handler that breaks" in text
    assert "Traceback" in text
    assert "RuntimeError: a callback that breaks" in text
    pane = app.log.get("1.0", "end")
    assert "job 7:" not in pane, "the boundaries are the file's alone"
    assert "could not handle an event: RuntimeError" in pane


def test_the_scanners_answer_is_kept_once(window):
    """The header names the code and the folders; the scanner the session
    opened is said as it answers, once."""
    from rps7200.session import Event

    app, root = window
    app.session.inquiry_text = "DEMO RPS 7200 1.00"
    for _ in range(2):
        app._handle(Event(kind="state", text="DEMO RPS 7200 1.00"))
    assert _kept(app).count("scanner: DEMO RPS 7200 1.00") == 1


def test_the_last_lines_before_quit_are_kept(window, monkeypatch):
    """The window quits as soon as its worker has ended, which can be before
    the pump has read the session's last words: kept in the file, as the
    pane goes with the window, and the file closed."""
    from rps7200.session import Event

    app, root = window
    app.session.shutdown()
    app.session.join(10)
    app.session.poll()
    app.session._events.put(Event(kind="log", text="the last words"))
    app.session._events.put(Event(kind="failed", text="and a failure"))
    # Put back before the fixture's own teardown destroys the root: the
    # autouse fixtures' monkeypatch outlives `window`, and patched to the
    # end the root was never destroyed and stayed tkinter's default -- every
    # picture a later test's window made without a master went into it
    # ('image "pyimage1090" doesn't exist').
    with monkeypatch.context() as quitting:
        quitting.setattr(root, "destroy", lambda: None)
        app._wait_to_quit()
    assert not app._alive
    text = _kept(app)
    assert "the last words" in text and "and a failure" in text
    assert text.rstrip().endswith("the window closed")
    app._say("said after the close")
    assert "said after the close" not in _kept(app)


def test_a_log_that_cannot_be_written_is_said_once(window, tmp_path):
    """A logs folder that is a file: said in the pane, once, and the window
    goes on saying everything else there."""
    import tkinter

    from rps7200.session import ScanSession

    app, root = window
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"), verbose=False)
    session.start = lambda: None                 # no worker, no device
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True,
                           settings_path=tmp_path / "s2.json",
                           logs=blocker / "logs")
    try:
        other._say("one")
        other._say("two")
        pane = other.log.get("1.0", "end")
        assert pane.count("the log could not be kept in") == 1
        assert "one" in pane and "two" in pane
    finally:
        other._alive = False
        top.destroy()


def test_threads_that_die_and_warnings_are_kept(tmp_path, monkeypatch):
    """Process-wide, so `main` installs them and puts them back: a thread
    that ended on an exception -- the filing thread, say -- and a Python
    warning, such as the library's that an index could not be rewritten.
    Both still go where they went as well: the hooks they replace are
    called first, whatever they are -- stderr's in the window, pytest's
    here."""
    import threading
    import warnings

    from rps7200.console import KeptLog

    went: list = []
    monkeypatch.setattr(threading, "excepthook",
                        lambda args: went.append(("thread", args.exc_value)))
    monkeypatch.setattr(warnings, "showwarning",
                        lambda message, *a, **k: went.append(
                            ("warning", message)))
    kept = KeptLog(tmp_path / "logs")
    hook, show = threading.excepthook, warnings.showwarning
    with gui.kept_hooks(kept):
        def die():
            raise RuntimeError("the thread's last words")

        t = threading.Thread(target=die, name="filing")
        t.start()
        t.join(5)
        warnings.showwarning(RuntimeWarning("the index was not rewritten"),
                             RuntimeWarning, "library.py", 684)
    assert threading.excepthook is hook and warnings.showwarning is show
    kept.close()
    text = kept.path.read_text(encoding="utf-8")
    assert "thread filing ended on an exception" in text
    assert "RuntimeError: the thread's last words" in text
    assert "warning: RuntimeWarning: the index was not rewritten" in text
    assert [(kind, str(what)) for kind, what in went] == [
        ("thread", "the thread's last words"),
        ("warning", "the index was not rewritten")]


def test_settings_unreadable_at_launch_are_merged_once_readable(
        window, tmp_path, monkeypatch):
    """A settings file Windows held at launch -- Defender, the indexer -- was
    moved aside as unreadable, and the window's next save wrote its
    defaults over the place: every preset, shortcut and other walk's sheet
    decisions gone. It is left in place, nothing is written over it while
    it cannot be read, and once it can the window's changes are laid over
    what it holds."""
    import tkinter
    from pathlib import Path

    from rps7200 import settings as stored_settings
    from rps7200.session import ScanSession

    app, root = window
    path = tmp_path / "held.json"
    stored = {"presets": {"theirs": {"dpi": "3600"}},
              "rolls": {"old-walk": {"opened": 1}},
              "sheet": {"old-walk": {"rotations": {"2": 90}}}}
    path.write_text(json.dumps(stored), encoding="utf-8")
    original = path.read_bytes()
    real = Path.read_text
    held = {"on": True}

    def read_text(self, *a, **k):
        if held["on"] and self == path:
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"), verbose=False)
    session.start = lambda: None                 # no worker, no device
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True, settings_path=path)
    try:
        assert "left as it is" in other.log.get("1.0", "end")
        other.presets["mine"] = {"dpi": "1800"}
        other.remembered["sheet"]["new-walk"] = {"rotations": {"1": 180}}
        ok, why = other._remember()
        assert not ok and "could not be read at launch" in why
        assert path.read_bytes() == original, "written over while held"
        # Every save asks again, and says nothing new: the launch said it,
        # and the first save that wrote nothing said that once.
        ok, _why = other._remember()
        assert not ok
        pane = other.log.get("1.0", "end")
        assert pane.count(f"{path} could not be read (") == 1, pane
        assert pane.count("could not save the window's settings") == 1, pane
        assert "opened with its defaults" in pane
        assert pane.count("opened with its defaults") == 1, pane
        held["on"] = False
        ok, _why = other._remember()
        assert ok
        now = stored_settings.load(path)
        assert set(now["presets"]) == {"theirs", "mine"}
        assert set(now["sheet"]) == {"old-walk", "new-walk"}
        assert "old-walk" in now["rolls"]
    finally:
        other._alive = False
        top.destroy()


def test_settings_held_at_launch_and_nonsense_once_read_are_moved_aside(
        window, tmp_path, monkeypatch):
    """Read at last and found to be nonsense, the file is moved aside and
    said, and the window's own are written: not said to have been read and
    kept, which it was not."""
    import tkinter
    from pathlib import Path

    from rps7200 import settings as stored_settings
    from rps7200.session import ScanSession

    app, root = window
    path = tmp_path / "held.json"
    path.write_text('{"presets": ', encoding="utf-8")
    real = Path.read_text
    held = {"on": True}

    def read_text(self, *a, **k):
        if held["on"] and self == path:
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    session = ScanSession(root=str(tmp_path / "library2"),
                          rolls=str(tmp_path / "rolls2"), verbose=False)
    session.start = lambda: None                 # no worker, no device
    top = tkinter.Toplevel(root)
    other = gui.ScannerGui(top, session, demo=True, settings_path=path)
    try:
        other.presets["mine"] = {"dpi": "1800"}
        held["on"] = False
        ok, _why = other._remember()
        assert ok
        pane = other.log.get("1.0", "end")
        assert pane.count(f"{path} could not be read (") == 2, pane
        assert "the file is kept as held.json.unreadable-" in pane
        assert "can be read again" not in pane
        assert list(tmp_path.glob("held.json.unreadable-*"))
        assert "mine" in stored_settings.load(path)["presets"]
    finally:
        other._alive = False
        top.destroy()


def test_a_change_to_the_output_waits_for_the_next_job(window, tmp_path):
    """GUI1-12: a job takes the output folder, format and quality as it
    starts. A change made while one runs says it applies from the next job;
    nothing is said between jobs, nor for a value that did not change."""
    from rps7200.session import Output

    app, root = window
    first = tmp_path / "first"
    first.mkdir()
    app._set_outdir(str(first))
    app.v_outfmt.set("tiff")
    app._sync_format()
    before = app.log.get("1.0", "end")
    app.session._output = Output(first, "tiff", 90)
    try:
        app._set_outdir(str(first))
        app.v_outfmt.set("tiff")
        app._sync_format()
        assert app.log.get("1.0", "end") == before, "nothing changed"
        app._set_outdir(str(tmp_path))
        app.v_outfmt.set("jpeg")
        app._sync_format()
        pane = app.log.get("1.0", "end")
        assert (f"the output folder is now {tmp_path} -- the job running now "
                f"goes on with {first}; this applies from the next job to "
                "start") in pane
        assert ("the format is now JPEG -- the job running now goes on with "
                "TIFF") in pane
    finally:
        app.session._output = None
    before = app.log.get("1.0", "end")
    app.v_outfmt.set("tiff")
    app._sync_format()
    assert "applies from the next job" not in app.log.get(
        "1.0", "end")[len(before):], "said between jobs"


def test_a_demo_output_folder_outside_demo_is_said_once(window, tmp_path):
    """Chosen in the demo window it is allowed -- the operator's own act --
    and said, once a folder: the demo's pictures are made up, under the
    names real scans get. `--out` outside demo/ is refused at launch."""
    app, root = window
    assert app.demo
    for _ in range(3):
        app._set_outdir(str(tmp_path))
    pane = app.log.get("1.0", "end")
    assert pane.count("the demo's scans are made up") == 1
    assert str(tmp_path) in pane


# -- one walk per sheet: its id, its decisions, and the frames it holds ------
#
# P22-b: a sheet's decisions are found by the walk's own id, written once.
# P22-a: after a restart they come back frame by frame beside approved.json.
# FOUND-1: a frame walked again after its position was set takes the walk's
# own prescan as its reference. P21-r1: the name join is for records from
# before entries were named, record by record.


def _with_id(folder, walk_id):
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["walk_id"] = walk_id
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")


def test_a_walk_is_known_by_its_id_and_an_old_one_by_its_time(tmp_path):
    folder = _walked_folder(tmp_path, count=2)
    assert gui.walk_stamp(folder) == (folder / "survey.json").stat().st_mtime_ns
    _with_id(folder, "a" * 32)
    assert gui.walk_stamp(folder) == "a" * 32
    assert gui.walk_stamp(tmp_path / "nothing") is None
    assert gui.walk_stamp(None) is None
    (folder / "survey.json").write_text("{", encoding="utf-8")
    (folder / "survey.json.bak").write_text(json.dumps({"walk_id": "b"}),
                                            encoding="utf-8")
    assert gui.walk_stamp(folder) is gui.UNKNOWN, "never the copy's walk"


def test_decisions_filed_before_a_late_answer_are_found_after_a_restart(
        window, tmp_path):
    """The writer's late answers rewrite survey.json after the walk has
    ended: decisions filed between the two were keyed to a time no file had
    any more, and set aside where nothing found them."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    _with_id(folder, "c" * 32)
    _leave_a_sheet(app, folder)
    stored = app.remembered["sheet"][folder.name]
    assert stored["walk"] == "c" * 32 and stored["saved_utc"]
    # A late answer rewrites the file, and the id with it unchanged.
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["frames"][0]["prescan_entry"] = "late"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    app._walk_seen = None                       # as after a restart
    app._sheet_roll = folder
    assert app._stored_sheet() is stored


def test_a_walk_that_cannot_be_read_files_nothing_until_it_can(window,
                                                               tmp_path):
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    _with_id(folder, "d" * 32)
    good = (folder / "survey.json").read_bytes()
    (folder / "survey.json").write_text("{", encoding="utf-8")
    app._sheet_roll, app._walk_seen = folder, None
    said = []
    app._say = said.append
    state = {"ticks": {1: False}, "offsets": {}, "rotations": {2: 90},
             "flips": {}, "sources": {}, "options": {}}
    assert app._file_sheet_state(state) is False
    assert folder.name not in (app.remembered.get("sheet") or {})
    assert app.sheet_state is state, "kept here meanwhile"
    # Said, once: a survey.json that stays damaged -- its sheet opened from
    # the copy beside it -- kept every decision out of the settings file
    # without a word, and the window's close lost them.
    assert app._file_sheet_state(state) is False
    unfiled = [s for s in said if "cannot be read now" in s]
    assert len(unfiled) == 1 and "kept in this window only" in unfiled[0]
    (folder / "survey.json").write_bytes(good)
    assert app._file_sheet_state(state) is True
    assert app.remembered["sheet"][folder.name]["walk"] == "d" * 32


def test_a_frame_walked_again_drops_its_position_as_its_prescan_arrives(
        window):
    """Dropped only once the walk ended, and filed then: a kill part way
    left the position measured on the replaced prescan, and a restart held
    the frame to it."""
    app, root = window
    filed = []
    app._store_sheet_state = filed.append
    app.sheet_state = {"ticks": {}, "offsets": {3: 0.5, 4: 0.7},
                       "sources": {3: "operator", 4: "operator"},
                       "rotations": {3: 90}, "flips": {}, "options": {}}
    app.survey = [types.SimpleNamespace(number=n) for n in (3, 4)]
    app._kept_walk, app._rewalked = {3, 4}, set()
    app._into_survey(types.SimpleNamespace(number=3))
    assert app.sheet_state["offsets"] == {4: 0.7}
    assert app.sheet_state["rotations"] == {3: 90}, "the turn is kept"
    assert filed == [app.sheet_state], "filed at once"


def test_the_end_of_a_walk_names_the_positions_its_frames_lost(
        window, tmp_path):
    """Dropped as each prescan arrived, the positions were gone from the
    sheet's state by the walk's end, which named only what it found there:
    with no sheet open, the line saying which positions went never came."""
    import copy

    from rps7200.session import Event

    app, root = window
    folder = _walked_folder(tmp_path, count=3)
    results = gui.read_survey(folder)["results"]
    app._sheet_roll = folder
    app.survey = list(results)
    app.sheet_state = {"ticks": {}, "offsets": {2: 0.5},
                       "sources": {2: "operator"}, "rotations": {3: 90},
                       "flips": {}, "options": {}}
    app._kept_walk, app._rewalked = {1, 2, 3}, set()
    app._surveying = True
    said = []
    app._say = said.append
    app._into_survey(copy.copy(results[1]))             # frame 2, again
    app._into_survey(copy.copy(results[2]))             # frame 3, again
    assert 2 not in app.sheet_state["offsets"], "dropped as it arrived"
    app._handle(Event(kind="finished", text="walked 2 frames"))
    root.update()
    assert ("walked frames 2-3 again: their new prescans replace the old, "
            "and the positions set on 2 are dropped") in said, said
    assert app._dropped == set(), "a walk's own, and forgotten with it"


def test_the_sheet_comes_back_frame_by_frame():
    """P22-a: approved.json's word for a frame it records, unless the sheet
    was saved since; the sheet's for every other frame; ticks less the
    frames done."""
    approved = {"offsets": {1: 0.5}, "rotations": {1: 0, 3: 180},
                "flips": {1: False}, "sources": {1: "measured"},
                "decided": {1: "2026-10-10T10:00:00+00:00",
                            3: "2026-10-10T12:00:00+00:00", 4: None}}
    stored = {"ticks": {"1": True, "8": True, "9": False},
              "offsets": {"1": 0.9, "3": 0.2, "9": 0.4},
              "rotations": {"1": 90, "3": 90, "9": 270},
              "sources": {"1": "operator", "3": "operator", "9": "operator"},
              "flips": {}, "options": {"dpi": "1800"},
              "saved_utc": "2026-10-10T11:00:00+00:00"}
    state, theirs, since = gui.merge_sheet(approved, stored, done={8})
    # Frame 1 commissioned before the save: the save wins, all of it.
    assert (state["offsets"][1], state["rotations"][1],
            state["sources"][1]) == (0.9, 90, "operator")
    # Frame 3 commissioned after: approved.json's, its zero included.
    assert 3 not in state["offsets"] and state["rotations"][3] == 180
    # Frame 4 recorded with no time: approved.json's, with no position.
    assert 4 not in state["offsets"]
    # Frame 9 not recorded at all: the sheet's.
    assert (state["offsets"][9], state["rotations"][9]) == (0.4, 270)
    assert state["ticks"] == {1: True, 9: False}, "no tick on a frame done"
    assert state["options"] == {"dpi": "1800"}
    assert (theirs, since) == (2, 2)
    # Files from before times were written: approved.json's frames.
    old = dict(stored)
    old.pop("saved_utc")
    state, theirs, _since = gui.merge_sheet(approved, old)
    assert state["offsets"][1] == 0.5 and state["rotations"][1] == 0
    assert state["offsets"][9] == 0.4


def test_a_frame_walked_again_after_its_position_drops_it(tmp_path):
    """FOUND-1, by id: approved.json writes "library\\\\<id>", the walk the
    bare id, and as strings no two of 324 agreed."""
    folder = _walked_folder(tmp_path, count=3)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey["frames"][0]["prescan_entry"] = "20261004T135358Z_x-01_300dpi"
    survey["frames"][1]["prescan_entry"] = "20261004T130034Z_x-02_300dpi"
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    (folder / "approved.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": n, "offset_mm": 0.5, "rotation": 90,
             "flipped": True, "source": "operator",
             "reference_entry": f"library\\20261004T130034Z_x-0{n}_300dpi"}
            for n in (1, 2)]}), encoding="utf-8")
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert 1 not in out["offsets"] and 1 not in out["sources"]
    assert (out["rotations"][1], out["flips"][1]) == (90, True)
    assert out["offsets"][2] == 0.5, "the same id, prefix or not"
    assert any("walked again after its position was set" in s for s in said)


def test_an_approved_json_of_another_walk_is_not_read_here(window, tmp_path):
    from rps7200.session import read_approved

    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    _with_id(folder, "e" * 32)
    old = json.dumps({"numbering": "strip", "walk_id": "f" * 32, "frames": [
        {"number": 1, "offset_mm": 0.5, "rotation": 90, "source": "operator"},
        {"number": 2, "rotation": 180}]})
    (folder / "approved.json").write_text(old, encoding="utf-8")
    said = []
    out = gui.read_survey(folder, say=said.append)
    assert out["offsets"] == {} and out["rotations"] == {}
    assert out["decided"] == {}
    assert any("decided on another walk" in s for s in said)
    # Export, Delete's count and the tools read the file as it is.
    assert read_approved(folder)[1] == {1: 90, 2: 180}
    # A commission here starts it afresh, the old file kept beside it.
    app._sheet_roll, app._walk_seen = folder, None
    app.session.rolls = str(folder.parent)
    app._write_approved((Approved(2, rotation=0),), folder)
    written = json.loads((folder / "approved.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in written["frames"]] == [2]
    assert written["walk_id"] == "e" * 32
    assert written["frames"][0]["decided_utc"]
    assert (folder / "approved.json.bak").read_text(encoding="utf-8") == old


def test_a_commission_from_a_sheet_whose_walk_was_replaced_is_refused(
        window, tmp_path, monkeypatch):
    """A `tools/scan_roll.py` walk into the folder the window shows, which a
    view lock does not keep out: the sheet's positions were measured on
    prescans no longer there. A walk from before ids, whose file's time a
    late answer moves, is never refused for it."""
    app, root = window
    folder = _walked_folder(tmp_path, count=2)
    _with_id(folder, "1" * 32)
    app._sheet_roll, app._walk_seen = folder, None
    assert app._sheet_walk() == "1" * 32
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    assert app._walk_replaced() is False
    _with_id(folder, "2" * 32)
    assert app._walk_replaced() is True and "was replaced" in errors[0][1]
    # A walk from before ids: its time moving is no new walk.
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    survey.pop("walk_id")
    (folder / "survey.json").write_text(json.dumps(survey), encoding="utf-8")
    app._walk_seen = None
    app._sheet_walk()
    import os
    later = (folder / "survey.json").stat().st_mtime_ns + 10**9
    os.utime(folder / "survey.json", ns=(later, later))
    assert app._walk_replaced() is False


def test_the_name_join_is_for_records_from_before_entries_were_named(
        tmp_path):
    """P21-r1, record by record: a roll tool's record from before `done`
    (entry, no done) by its own id; a record with no `entry` key by folder
    and then name; a modern record done with entry None joins nothing."""
    folder = tmp_path / "rolls" / "mixed"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": 1, "entry": "library\\scan600-01", "error": None},
            {"number": 2, "done": True},
            {"number": 3, "done": True, "entry": None},
            {"number": 4, "done": True, "entry": "library\\new-04"}]}),
        encoding="utf-8")
    entries = {name: tmp_path / "library" / name
               for name in ("scan600-01", "new-04", "by-folder-2",
                            "by-folder-3")}
    index = {"ids": {n: p for n, p in entries.items()},
             "folders": {gui._folder_key(folder): {
                 2: entries["by-folder-2"], 3: entries["by-folder-3"]}},
             "names": {}}
    summary = gui.roll_summary(folder)
    assert summary["done"] == [1, 2, 3, 4]
    assert summary["legacy"] == [2]
    joined = gui.roll_entries(summary, index)
    assert joined == {1: entries["scan600-01"], 2: entries["by-folder-2"],
                      4: entries["new-04"]}


def test_a_prescan_kept_beside_a_new_one_is_counted_as_lost(tmp_path):
    """P18-b: Delete's question names the old prescans a walk added to kept,
    the only copies there are."""
    from rps7200 import tiff

    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    for name in ("prescan03.tif", "prescan03-2.tif"):
        tiff.write(str(folder / name), np.zeros((4, 6, 3), np.uint8))
    (folder / "survey.json").write_text(json.dumps({
        "numbering": "strip", "frames": [
            {"number": 3, "prescan": "prescan03-2.tif",
             "prescan_entry": "e3",
             "takes": [{"file": "prescan03.tif", "kind": "prescan",
                        "replaced_utc": "2026-10-10T00:00:00+00:00"},
                       {"entry": "e3", "kind": "prescan",
                        "file": "prescan03-2.tif"}]}]}),
        encoding="utf-8")
    # The walk's own prescan is in the library: only the old one kept
    # beside it is the last copy. Without a library to join on, the walk's
    # own counted too, and so the count said nothing of the kept one.
    root = tmp_path / "library"
    (root / "e3").mkdir(parents=True)
    (root / "e3" / "scan.json").write_text("{}", encoding="utf-8")
    lost = gui.roll_losses({"folder": folder}, library_root=root)
    assert lost["prescans"] == [3]
    (folder / "prescan03.tif").unlink()
    assert gui.roll_losses({"folder": folder},
                           library_root=root)["prescans"] == []


# -- a roll of strips, in the window ------------------------------------------
#
# Stefan's call, 2026-10-10: a film is one roll folder, its strips walked in
# order into strip-01, strip-02 ... inside it and numbered across it. These
# are the window's half: where the Roll button puts a walk, the questions,
# the Roll panel's line, the sheet's numbers, the browser's tree, and what is
# refused in words.


def _strips_on_disk(rolls, name="Gold200", strips=((1, 1, 6, 6, True),),
                    scanned=None, extra=(), settings=None):
    """A roll of strips under ``rolls`` as the session leaves one: its index
    listing each of ``strips`` -- ``(strip, first, last, walked, ended)`` --
    each strip's walk of ``walked`` frames, prescans and all, carrying the
    copy of its claim and each frame's number across the roll, with
    ``strip_end`` where it was walked to its end and a Stop where not; and
    ``scanned``, by strip, the places its roll.json records done. Every
    strip records 600 dpi, 300 dpi prescans and negative film, and what
    ``settings`` adds."""
    from rps7200 import library, tiff
    from rps7200.session import NUMBERING, STRIPS_KIND, write_manifest

    roll = rolls / name
    roll.mkdir(parents=True)
    listed = []
    settings = {"dpi": 600, "prescan_resolution": 300, "dry_run": True,
                "film": "negative", "start_at": 1, **(settings or {})}
    for number, first, last, walked, ended in strips:
        folder = roll / library.STRIP_FOLDER.format(number)
        folder.mkdir()
        records = []
        for frame in _strip(count=walked):
            prescan = f"prescan{frame.number:02d}.tif"
            tiff.write(str(folder / prescan), frame.image.astype(np.uint8))
            records.append({"number": frame.number, "prescan": prescan,
                            "roll_number": first + frame.number - 1})
        survey = {"roll": name, "numbering": NUMBERING,
                  "walk_id": f"{name}-{number}",
                  "strip": {"number": number, "first": first},
                  "settings": settings, "frames": records}
        if ended:
            survey["strip_end"] = walked + 1
        else:
            survey["stopped"] = f"stopped after frame {walked}, as asked"
        write_manifest(folder / "survey.json", survey)
        done = (scanned or {}).get(number)
        if done:
            write_manifest(folder / "roll.json", {
                "roll": name, "numbering": NUMBERING, "settings": settings,
                "strip": {"number": number, "first": first},
                "frames": [{"number": p, "done": True, "entry": "",
                            "roll_number": first + p - 1} for p in done]})
        listed.append({"strip": number, "folder": folder.name, "first": first,
                       "last": last, "ends_at": None})
    write_manifest(roll / library.STRIPS_INDEX, {
        "kind": STRIPS_KIND, "version": 1, "roll": name,
        "created": "2026-10-10T19:02:03Z", "strips": listed,
        "extra": list(extra)})
    return roll


def test_a_walk_or_a_roll_from_the_roll_button_is_placed_by_one_rule(
        tmp_path):
    """`roll_target`: flows 1, 2, 6 and 11 of the roll model."""
    rolls = tmp_path / "rolls"
    rolls.mkdir()
    # An empty box: a new roll by a dated name, strip 1 from 1.
    target = gui.roll_target(rolls, "", dry=True)
    assert target.kind == "new roll" and target.ours
    assert (target.plan.number, target.plan.first) == (1, 1)
    assert target.folder == target.roll and target.roll.parent == rolls
    assert target.plan.folder == target.roll / "strip-01"
    assert "strip 1 of a new roll" in target.note
    # A name with no folder: a new roll of that name.
    target = gui.roll_target(rolls, "Gold200", dry=True)
    assert (target.kind, target.roll, target.ours) == (
        "new roll", rolls / "Gold200", False)
    # A roll of strips: its next strip, numbered on from its last frame.
    roll = _strips_on_disk(rolls)
    target = gui.roll_target(rolls, "Gold200", dry=True)
    assert target.kind == "next strip" and target.new_strip
    assert (target.plan.number, target.plan.first) == (2, 7)
    assert "Into Gold200/strip-02: strip 2 of Gold200, numbered from 7" \
        in target.note
    # A plain roll with a strip of it open goes into that strip.
    target = gui.roll_target(rolls, "Gold200", dry=False,
                             current=roll / "strip-01")
    assert (target.kind, target.folder) == ("strip", roll / "strip-01")
    assert not target.new_strip
    # Without one, the next strip scanned without walking it first.
    target = gui.roll_target(rolls, "Gold200", dry=False)
    assert target.kind == "next strip"
    assert "without walking it first" in target.note
    # A roll from before strips: a plain roll resumes it...
    legacy = _walked_folder(rolls, count=3)          # rolls/walk
    target = gui.roll_target(rolls, "walk", dry=False)
    assert (target.kind, target.folder) == ("legacy", legacy)
    # ... a walk typed into it starts walk-2, saying why ...
    target = gui.roll_target(rolls, "walk", dry=True)
    assert target.kind == "new roll" and target.roll == rolls / "walk-2"
    assert target.ours and "takes no second strip" in target.note
    # ... and one with the window's own name in the box -- a roll it opened
    # -- a new dated roll, as before; the line names no second it starts at.
    target = gui.roll_target(rolls, "walk", own="walk", dry=True)
    assert target.kind == "new roll" and target.roll.name.startswith("20")
    assert gui.strip_line(rolls, "walk", own="walk") == (
        "walk: one strip from before rolls held strips -- a walk starts a "
        "new roll", None)
    # walk-2 a roll of strips already: joined.
    _strips_on_disk(rolls, name="walk-2")
    target = gui.roll_target(rolls, "walk", dry=True)
    assert target.kind == "next strip" and target.roll == rolls / "walk-2"
    assert "a roll of strips already" in target.note
    # A folder of no roll's, for a walk and a roll alike.
    (rolls / "study").mkdir()
    (rolls / "study" / "A01.tif").write_bytes(b"x")
    for dry in (True, False):
        target = gui.roll_target(rolls, "study", dry=dry)
        assert target.roll == rolls / "study-2" and target.kind == "new roll"
        assert "holds files that are no roll's" in target.note
    # An index neither copy of which reads numbers nothing.
    (roll / "strips.json").write_text("{", encoding="utf-8")
    (roll / "strips.json.bak").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="strips.json"):
        gui.roll_target(rolls, "Gold200", dry=True)


def test_a_strip_is_said_by_its_numbers_across_the_roll(tmp_path):
    """One wording: the roll's number first, its place beside it wherever
    the two differ; strip 1 and a roll from before strips as they were."""
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    strip = gui.place_of(roll / "strip-02")
    assert gui.places_said(strip, [2, 4]) == "8, 10 (frames 2 and 4 of strip 2)"
    assert gui.places_said(gui.place_of(roll / "strip-01"), [2, 4]) == "2, 4"
    assert gui.places_said(None, [2, 4]) == "2, 4"
    assert gui.sheet_title(strip, range(1, 7)) == (
        "Contact sheet -- Gold200, strip 2 (frames 7-12)")
    assert gui.sheet_title(None, range(1, 7)) == "Contact sheet"
    # A place past a closed range has no number until it is scanned.
    closed = gui.place_of(roll / "strip-01")
    assert gui.shown_number(closed, 7) is None
    assert gui.shown_number(closed, 6) == 6


def test_the_keep_question_names_the_strip_and_defaults_by_its_end(
        window, tmp_path, monkeypatch):
    """Flow 2: Yes adds to the strip, No walks the next one, numbered on
    from it; the default button is No after a walk to the strip's end and
    Yes after one that stopped, and the meanings never change."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-01")
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.v_startat.set("1")
    app.v_last.set("")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append((m, k)) or False)
    app.on_roll()
    message, kw = asked[0]
    assert "The contact sheet has frames 1-6 of Gold200, strip 1." in message
    assert ("No -- walk the next strip: strip 2 of Gold200, numbered from 7 "
            "(strip 1 has 6, walked to the end of the strip)") in message
    assert kw.get("default") == gui.messagebox.NO
    (job,) = jobs
    assert job.out == str(roll) and job.new_strip and job.strip_plan == (2, 7)
    assert not job.extend_walk


def test_a_stopped_strip_is_offered_first_and_opened_when_no_sheet_is(
        window, tmp_path, monkeypatch):
    """Flow 2 with no sheet open: strip 1's walk stopped after frame 4, so
    the window opens it quietly and asks, Yes first; Yes adds to it."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, None, 4, False),))
    told = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda *a, **k: told.append(a))
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.fields["roll"].set("Gold200")
    app.v_startat.set("5")
    app.v_last.set("")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append((m, k)) or True)
    app.on_roll()
    assert told == [], "opened quietly"
    message, kw = asked[0]
    assert kw.get("default") == gui.messagebox.YES
    assert ("Strip 1's walk stopped after frame 4, so it counts as 4 frames "
            "(1-4) and strip 2 is numbered from 5; a frame of strip 1 walked "
            "later gets a number after the roll's last") in message
    assert "To add to strip 1: set first frame to 5" not in message, (
        "it is set to 5 already")

    (job,) = jobs
    assert job.out == str(roll / "strip-01") and job.extend_walk
    assert not job.new_strip and job.start_at == 5


def test_a_stopped_strip_opened_for_a_walk_is_walked_with_its_own_settings(
        window, tmp_path, monkeypatch):
    """Opening strip 1 puts its settings back into the controls, and the
    walk and its question are built from them -- read before the opening,
    its prescan resolution and the restored film disagreed, and the walk
    could not be added to the strip it had just opened. His first and last
    frame stay as he pressed them."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, None, 4, False),))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda *a, **k: pytest.fail(f"refused: {a}"))
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.v_dpi.set("1800")
    app.v_predpi.set("600")
    app.fields["roll"].set("Gold200")
    app.v_startat.set("5")
    app.v_last.set("8")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or True)
    app.on_roll()
    (message,) = asked
    assert message.startswith(
        "Strip 1 of Gold200 is open, with its own settings back in the "
        "window: scan dpi 600, prescan dpi 300.\n\nWalk frames 5 to 8, at "
        "600 dpi."), message
    (job,) = jobs
    assert (job.prescan_resolution, job.resolution) == (300, 600)
    assert (job.start_at, job.frames) == (5, 4)
    assert job.out == str(roll / "strip-01") and job.extend_walk
    assert (app.v_startat.get(), app.v_last.get()) == ("5", "8")
    app.sheet.top.destroy()


@pytest.mark.parametrize("last", ["", "6"])
def test_finishing_a_stopped_strip_asks_again_with_its_own_settings(
        window, tmp_path, monkeypatch, last):
    """Flow 6's three-way question, for a plain roll whose roll's last strip
    stopped: Cancel moves nothing, No scans the next strip numbered after
    it, and Yes opens strip 1 -- its own settings back -- and asks again
    with them, naming the done frames it skips or scans again. The job is
    what that last question says. Yes used to start at once with the
    window's resolution and the strip's infrared, under a promise that
    the done frames were skipped which, with no last frame, it broke."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, None, 4, False),),
                           scanned={1: [1, 2, 3]},
                           settings={"infrared": True})
    told = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: told.append(m))
    app.calibrated = True
    app.v_dryrun.set(False)
    app.v_film.set("negative")
    app.v_dpi.set("1800")
    app.v_ir.set(False)
    app.fields["roll"].set("Gold200")
    app.v_startat.set("1")
    app.v_last.set(last)
    answers, asked, confirmed, jobs = [], [], [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append((m, k))
                        or answers.pop(0))
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: confirmed.append(m) or True)
    # Cancel: nothing moves, nothing is opened.
    answers.append(None)
    app.on_roll()
    message, kw = asked[-1]
    assert kw.get("default") == gui.messagebox.YES
    assert "Strip 1 of Gold200's walk stopped after frame 4." in message
    assert ("Yes -- finish strip 1 (1-4 so far): it is opened first, with "
            "its own settings, and the question after this one says which "
            "of its frames are scanned.") in message
    assert "skipped" not in message
    assert ("No -- Scan strip 2 of Gold200 (new), numbered from 5, without "
            "walking it first") in message
    assert jobs == [] and app._loaded_roll is None
    assert app.v_dpi.get() == "1800"
    # No: the next strip, at the window's settings.
    answers.append(False)
    app.on_roll()
    (job,) = jobs
    assert job.out == str(roll) and job.new_strip
    assert job.strip_plan == (2, 5)
    assert (job.resolution, job.infrared) == (1800, False)
    jobs.clear()
    # Yes: strip 1 opened quietly, and asked about again.
    answers.append(True)
    app.on_roll()
    assert told == [], "opened without its box"
    assert len(asked) == 3, "the three-way question is not asked again"
    (again,) = confirmed
    assert again.startswith(
        "Strip 1 of Gold200 is open, with its own settings back in the "
        "window: scan dpi 600, infrared (RGBI) on.\n\n")
    (job,) = jobs
    assert job.out == str(roll / "strip-01") and not job.new_strip
    assert job.strip_plan is None
    # What the question said is what goes.
    assert f"at {job.resolution} dpi with infrared." in again
    assert (job.resolution, job.infrared) == (600, True)
    assert (app.v_dpi.get(), app.v_ir.get()) == ("600", True)
    if last:
        assert "Scan frames 1 to 6," in again
        assert "Already scanned, and skipped: 1-3." in again
        assert (job.start_at, job.frames, job.only) == (1, 6, (4, 5, 6))
    else:
        assert "Scan every frame from frame 1 to the end of the strip" in again
        assert ("Already scanned, and scanned again unless a last frame "
                "stops before them: 1-3.") in again
        assert (job.start_at, job.frames, job.only) == (1, None, None)
    app.sheet.top.destroy()


def test_a_strip_with_every_frame_scanned_offers_the_next_one(
        window, tmp_path, monkeypatch):
    """A plain roll into a strip the window has open, with every frame of
    it scanned and its walk at the strip's end: the next strip instead, in
    one OK/Cancel question, numbered after it."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, None, 6, True),),
                           scanned={1: [1, 2, 3, 4, 5, 6]})
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-01")
    app.calibrated = True
    app.v_dryrun.set(False)
    app.v_startat.set("1")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    for last in ("", "6"):
        app.v_last.set(last)
        app.on_roll()
        assert ("Every frame of strip 1 of Gold200 is scanned, and it was "
                "walked to its end.") in asked[-1]
        assert ("Scan strip 2 of Gold200 (new), numbered from 7, without "
                "walking it first") in asked[-1]
        (job,) = jobs
        assert job.out == str(roll) and job.new_strip
        assert job.strip_plan == (2, 7) and job.only is None
        jobs.clear()
    app.sheet.top.destroy()


def test_adding_past_a_closed_strips_numbers_is_said_first(
        window, tmp_path, monkeypatch):
    """A walk added to strip 1, or a roll into it, once strip 2 has fixed
    its numbers: a frame past its 6th takes the number after the roll's
    last for good, and the question says so before it runs -- not only the
    log, frame by frame, once the number is used up. Nothing is said where
    the range ends inside the strip's numbers."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    said = ("Strip 1 has numbers 1-6. A frame past its 6th gets a number "
            "after the roll's last, from 13 -- numbers already given never "
            "change.")
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-01")
    app.calibrated = True
    app.v_film.set("negative")
    app.v_startat.set("1")
    asked = []
    monkeypatch.setattr(app.session, "submit", lambda job: None)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append(m) or None)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or False)
    for dry in (True, False):
        app.v_dryrun.set(dry)
        app.v_last.set("")
        app.on_roll()
        assert said in asked[-1], asked[-1]
        app.v_last.set("6")
        app.on_roll()
        assert "after the roll's last, from" not in asked[-1]
    assert gui.extras_said(gui.place_of(roll / "strip-02"), 1, None) == ""
    app.sheet.top.destroy()


def test_a_sheet_of_an_earlier_strip_is_not_taken_for_the_rolls_last(
        window, tmp_path, monkeypatch):
    """Strip 2's sheet open while strip 3, the roll's last, stopped: the
    next strip goes in the way strip 3 went in, said of strip 3; how to add
    to strip 3 is said; and the default is Cancel, since neither Yes nor No
    adds to it -- No was the default, and walked strip 4 past it."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, 12, 6, True),
                                          (3, 13, None, 4, False)))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-02")
    app.calibrated = True
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    app.v_startat.set("1")
    app.v_last.set("6")
    asked, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda t, m, **k: asked.append((m, k)) or None)
    app.on_roll()
    (message, kw), = asked
    assert ("No -- walk the next strip: strip 4 of Gold200, numbered from 17 "
            "(strip 3's walk stopped after frame 4). Put strip 4 in the way "
            "strip 3 went in.") in message
    assert "strip 2 went in" not in message
    assert ("Strip 3 is the roll's last, and its walk stopped after frame 4: "
            "to add to it instead, Cancel and open strip 3 under Rolls ...") \
        in message
    assert kw.get("default") == gui.messagebox.CANCEL
    assert jobs == []
    app.sheet.top.destroy()


def test_a_commission_from_strip_two_is_said_by_its_numbers(
        window, tmp_path, monkeypatch):
    """Flow 3: the frames are places on the strip, said with their numbers
    across the roll; the box names the roll, never the strip."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-02")
    assert app.fields["roll"].get() == "Gold200"
    said, jobs = [], []
    monkeypatch.setattr(app.session, "submit", jobs.append)
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: said.append(m) or True)
    app.calibrated = True
    assert app.on_scan_chosen((2, 4))
    (job,) = jobs
    assert job.out == str(roll / "strip-02") and job.only == (2, 4)
    assert "8, 10 (frames 2 and 4 of strip 2)" in said[0]
    assert app.fields["roll"].get() == "Gold200"


def test_a_strip_shown_from_outside_this_windows_rolls_is_never_written_to(
        window, tmp_path, monkeypatch):
    """P18-d for a strip: Scan chosen frames, adding to its walk, a plain
    Roll and "Strip ends here" are refused in words, naming --rolls; nothing
    is handed over and nothing written there."""
    app, root = window
    elsewhere = tmp_path / "elsewhere"
    roll = _strips_on_disk(elsewhere, strips=((1, 1, None, 6, False),))
    before = (roll / "strips.json").read_bytes()
    said, jobs = [], []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.open_roll(roll / "strip-01")
    said.clear()
    app.calibrated = True
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    assert not app.on_scan_chosen((1, 2))
    assert "outside this window's rolls" in said[-1]
    assert f"--rolls {elsewhere}" in said[-1]
    app.v_dryrun.set(False)
    app.on_roll()
    assert "outside this window's rolls" in said[-1]
    app.on_strip_end(4)
    assert "outside this window's rolls" in said[-1]
    app.v_dryrun.set(True)
    app.v_film.set("negative")
    monkeypatch.setattr(gui.messagebox, "askyesnocancel",
                        lambda *a, **k: True)
    app.on_roll()
    assert "outside this window's rolls" in said[-1]
    assert jobs == []
    assert (roll / "strips.json").read_bytes() == before
    assert not (roll / "strip-01" / "approved.json").exists()
    app.sheet.top.destroy()


def test_a_plain_roll_for_a_roll_opened_from_elsewhere_is_refused(
        window, tmp_path, monkeypatch):
    """P18-d: a roll from before strips opened by `--open-roll` from outside
    this window's rolls put its bare name in the box, and a plain Roll
    scanned into a folder of that name here -- another roll, perhaps.
    Refused, with directions; its sheet's commission still copies it in."""
    app, root = window
    folder = _walked_folder(tmp_path, count=3)        # not under rolls/
    (tmp_path / "rolls").mkdir(exist_ok=True)
    said, jobs = [], []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(app.session, "submit", jobs.append)
    app.open_roll(folder)
    app.calibrated = True
    app.v_dryrun.set(False)
    app.on_roll()
    assert jobs == []
    assert "was opened from" in said[-1] and "--rolls" in said[-1]
    assert "Scan chosen frames" in said[-1]
    app.sheet.top.destroy()


def test_the_sheets_decisions_are_keyed_by_roll_and_strip(window, tmp_path,
                                                          monkeypatch):
    """strip-02 of two rolls never share decisions; a roll from before
    strips keeps its bare key; a Rename moves a roll's strips' keys."""
    app, root = window
    rolls = tmp_path / "rolls"
    first = _strips_on_disk(rolls, name="A", strips=((1, 1, 6, 6, True),
                                                     (2, 7, None, 6, True)))
    other = _strips_on_disk(rolls, name="B", strips=((1, 1, 6, 6, True),
                                                     (2, 7, None, 6, True)))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(first / "strip-02")
    assert app._sheet_key() == "A/strip-02"
    app.sheet.rotations[3] = 90
    app.sheet._dismiss()
    assert "A/strip-02" in app.remembered["sheet"]
    assert "A/strip-02" in app.remembered["rolls"]
    app.open_roll(other / "strip-02")
    assert app._sheet_key() == "B/strip-02"
    assert app._stored_sheet() is None, "another roll's strip-02"
    app.sheet._dismiss()
    legacy = _walked_folder(rolls, count=2)
    app.open_roll(legacy)
    assert app._sheet_key() == "walk"
    app.sheet._dismiss()
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "C")
    app.session._hold_roll(first)
    app.on_rename_roll({"folder": first, "kind": "roll", "roll": "A"})
    assert (rolls / "C" / "strip-02").is_dir()
    assert not app.session._roll_locks, "the old name is let go of (P23-b)"
    assert "C/strip-02" in app.remembered["sheet"]
    assert "A/strip-02" not in app.remembered["sheet"]
    assert "C/strip-02" in app.remembered["rolls"]


def test_a_strips_row_is_not_renamed_nor_duplicated_and_says_why(
        window, tmp_path, monkeypatch):
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls)
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    monkeypatch.setattr(gui.simpledialog, "askstring",
                        lambda *a, **k: pytest.fail("asked for a name"))
    app.on_rename_roll({"folder": roll / "strip-01", "kind": "strip",
                        "roll": "Gold200"})
    assert "Rename the roll, Gold200, instead" in said[-1]
    browser = gui._RollBrowser(app, gui.rolls_on_disk(rolls))
    try:
        browser.table.selection_set("r0s0")
        browser._duplicate()
        assert "Duplicate copies a whole roll" in said[-1]
        assert sorted(p.name for p in rolls.iterdir()) == ["Gold200"]
    finally:
        browser.top.destroy()


def test_a_case_only_rename_goes_through(window, tmp_path, monkeypatch):
    """PLAT-06: on a disk that folds case, `gold200` is there already as
    `Gold200` itself, and the rename was refused as "already there"."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls)
    monkeypatch.setattr(gui.simpledialog, "askstring",
                        lambda *a, **k: "GOLD200")
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: pytest.fail(f"refused: {a}"))
    app.on_rename_roll({"folder": roll, "kind": "roll", "roll": "Gold200"})
    assert [p.name for p in rolls.iterdir()] == ["GOLD200"]
    # And on a disk that folds case, a name typed in another case finds it
    # in its own spelling.
    if (rolls / "gold200").exists():
        assert gui.roll_dir(rolls, "gold200").name == "GOLD200"


def test_the_roll_panel_says_what_the_next_walk_is(window, tmp_path,
                                                   monkeypatch):
    """The line under Scan roll, New roll, and Continue after a restart:
    the box is not kept across one, the roll walked last is offered back in
    one click, and nothing goes under it unless that is pressed."""
    from rps7200 import settings

    app, root = window
    rolls = tmp_path / "rolls"
    app._offer_strip_now()
    assert app.v_strip_note.get() == "a new roll: frames from 1"
    assert not app.b_continue.winfo_ismapped()
    _strips_on_disk(rolls)
    app.remembered["last_roll"] = "Gold200"
    app._offer_strip_now()
    assert app.v_strip_note.get() == (
        "a new roll -- or Continue Gold200: strip 2 next, numbered from 7")
    assert app.b_continue.cget("text") == "Continue Gold200"
    app.on_continue_roll()
    assert app.fields["roll"].get() == "Gold200"
    assert app._roll_named == "Gold200"
    app._offer_strip_now()
    assert app.v_strip_note.get() == "Gold200: strip 2 next, numbered from 7"
    app.on_new_roll()
    assert app.fields["roll"].get() == "" and app._roll_named is None
    _walked_folder(rolls, count=2)
    app.fields["roll"].set("walk")
    app._offer_strip_now()
    assert app.v_strip_note.get().startswith(
        "walk: one strip from before rolls held strips -- a walk starts a "
        "new roll, walk-2")
    # Kept in the settings file, and read back.
    app._remember()
    assert settings.load(app._settings_path)["last_roll"] == "Gold200"


def test_a_strips_sheet_shows_its_numbers_across_the_roll(window, tmp_path,
                                                          monkeypatch):
    """Ticks read the roll's number, the caption the place beside it, the
    title which strip; strip 1 and a roll from before strips as before."""
    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-02")
    sheet = app.sheet
    assert sheet.top.title() == ("Contact sheet -- Gold200, strip 2 "
                                 "(frames 7-12)")
    assert [sheet._tick_buttons[n].cget("text") for n in range(1, 7)] == [
        f"Frame {n}" for n in range(7, 13)]
    assert str(sheet._captions[2].cget("text")).startswith("strip 2 · 2\n")
    sheet.adjust(1)
    root.update()
    assert sheet._adjuster.v_title.get() == "Frame 8 (2 of 6 on strip 2)"
    sheet._adjuster.top.destroy()
    app.open_roll(roll / "strip-01")
    sheet = app.sheet
    assert sheet.top.title() == ("Contact sheet -- Gold200, strip 1 "
                                 "(frames 1-6)")
    assert sheet._tick_buttons[2].cget("text") == "Frame 2"
    assert not str(sheet._captions[2].cget("text")).startswith("strip")
    legacy = _walked_folder(rolls, count=6)
    app.open_roll(legacy)
    assert app.sheet.top.title() == "Contact sheet"
    app.sheet.adjust(3)
    root.update()
    assert app.sheet._adjuster.v_title.get() == "Frame 4 of 6"
    app.sheet._adjuster.top.destroy()
    app.sheet.top.destroy()


def test_strip_ends_here_leaves_a_stub_out_of_the_numbers(window, tmp_path,
                                                          monkeypatch):
    """Offered on the roll's open last strip alone; refused, in words,
    while the scanner works and past a scanned frame; the next strip is
    numbered from the end it sets, and counting to the end undoes it."""
    from rps7200.session import plan_next, write_manifest

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, None, 7, True),))
    said = []
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda t, m, **k: said.append(m))
    app.open_roll(roll / "strip-01")
    sheet = app.sheet
    monkeypatch.setattr(sheet.menu, "tk_popup", lambda *a: None)

    def labels():
        menu = app.sheet.menu
        return [menu.entrycget(i, "label")
                for i in range(menu.index("end") + 1)
                if menu.type(i) == "command"]

    sheet.on_cell_menu(types.SimpleNamespace(x_root=0, y_root=0), 5)
    assert "Strip ends here" in labels()
    assert "Count to the end of the strip" not in labels()
    monkeypatch.setattr(app, "_working", lambda: True)
    app.on_strip_end(6)
    assert "The scanner is working" in said[-1]
    monkeypatch.setattr(app, "_working", lambda: False)
    app.on_strip_end(6)
    assert plan_next(roll).next.first == 7
    assert sheet.top.title() == "Contact sheet -- Gold200, strip 1 (frames 1-6)"
    assert not sheet.ticks[7].get(), "the stub is unticked"
    assert "past where the strip ends" in str(sheet._captions[7].cget("text"))
    sheet.on_cell_menu(types.SimpleNamespace(x_root=0, y_root=0), 5)
    assert "Count to the end of the strip" in labels()
    app.on_strip_end(None)
    assert plan_next(roll).next.first == 8
    # Past a frame scanned there: refused.
    write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [{"number": 7, "done": True,
                                          "entry": "x"}]})
    app.on_strip_end(6)
    assert "is scanned" in said[-1]
    assert plan_next(roll).next.first == 8
    sheet.top.destroy()
    # A closed strip is not offered it.
    closed = _strips_on_disk(rolls, name="Done", strips=(
        (1, 1, 6, 6, True), (2, 7, None, 6, True)))
    app.open_roll(closed / "strip-01")
    monkeypatch.setattr(app.sheet.menu, "tk_popup", lambda *a: None)
    app.sheet.on_cell_menu(types.SimpleNamespace(x_root=0, y_root=0), 0)
    assert "Strip ends here" not in labels()
    app.sheet.top.destroy()


def test_the_browser_lists_a_roll_with_its_strips(tmp_path):
    """Roll rows with their strips under them, each by its numbers; a strip
    in the trash; an index that cannot be read; a copy cut short hidden; a
    roll from before strips as it was."""
    from pathlib import Path

    from rps7200 import library

    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, 12, 6, True),
                                          (3, 13, None, 4, False)))
    library.trash(rolls, [roll / "strip-02"], by="a test", kind="strips")
    _walked_folder(rolls, count=3)
    broken = _strips_on_disk(rolls, name="Broken")
    (broken / "strips.json").write_text("{", encoding="utf-8")
    (broken / "strips.json.bak").write_text("{", encoding="utf-8")
    half = _strips_on_disk(rolls, name="Gold200-2")
    (half / library.INCOMPLETE).write_text("{}", encoding="utf-8")
    listed = {Path(s["folder"]).name: s for s in gui.rolls_on_disk(rolls)}
    assert sorted(listed) == ["Broken", "Gold200", "walk"]
    assert listed["walk"]["kind"] == "folder"
    assert gui.roll_cells(listed["walk"])[0] == "walk"
    gold = listed["Gold200"]
    assert gold["kind"] == "roll"
    assert gui.roll_cells(gold)[0] == "Gold200 · 3 strips"
    assert [(s["strip"], s["state"]) for s in gold["strips"]] == [
        (1, "here"), (2, "in the trash"), (3, "here")]
    assert [gui.roll_cells(s)[0] for s in gold["strips"]] == [
        "strip 1 · 1-6", "strip 2 · 7-12", "strip 3 · 13-16"]
    assert gui.roll_cells(gold["strips"][1])[1] == "in the rolls' trash"
    assert "strips.json cannot be read" in gui.roll_cells(listed["Broken"])[0]
    assert [s["strip"] for s in listed["Broken"]["strips"]] == [1]


def test_a_rolls_frames_are_exported_by_their_numbers_across_it(tmp_path):
    """Export of a roll of strips: every strip's frames in strip order, each
    named by its number across the roll; one with no entry left is named
    by that number too."""
    rolls = tmp_path / "rolls"
    _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                   (2, 7, None, 6, True)),
                    scanned={1: [1, 2], 2: [2, 4]})
    entries = {}
    for strip, place in ((1, 1), (1, 2), (2, 2)):
        entry = tmp_path / "library" / f"e{strip}{place}"
        entry.mkdir(parents=True)
        (entry / "scan.json").write_text(json.dumps({
            "scan": {"resolution_dpi": 300, "channels": 3}}),
            encoding="utf-8")
        entries[(strip, place)] = entry
    (summary,) = gui.rolls_on_disk(rolls)
    for strip in summary["strips"]:
        strip["entries"] = {place: entries[(strip["strip"], place)]
                            for place in strip["done"]
                            if (strip["strip"], place) in entries}
    items = gui.roll_exports(summary)
    assert [(i.place, i.number) for i in items] == [(1, 1), (2, 2), (2, 8)]
    assert [gui.batch_name(i, "tiff").split("_")[0] for i in items] == [
        "frame01", "frame02", "frame08"]
    assert gui.export_skipped(summary, items) == [10]
    (strip2,) = [s for s in summary["strips"] if s["strip"] == 2]
    assert gui.export_skipped(strip2, gui.roll_exports(strip2)) == [10]


def test_a_frame_is_named_by_the_number_its_record_carries(window, tmp_path):
    """An extra whose write to the roll's index was refused is kept in the
    frame's records, and the session delivered the frame by it: Export, the
    browser's row, a Delete's question and an entry's Delete name it by
    that too. Worked out from the index alone, strip 1's seventh frame was
    exported as frame07 -- strip 2's first frame's number -- and dropped
    from what a Delete said goes for good."""
    import shutil

    from rps7200.session import NUMBERING, write_manifest

    app, root = window
    rolls = tmp_path / "rolls"
    # Strip 1 closed at 1-6, and its seventh place walked and scanned since,
    # numbered 25 in its records alone: the index's extra was not written.
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 7, True),
                                          (2, 7, None, 6, True)))
    strip1 = roll / "strip-01"
    walk = json.loads((strip1 / "survey.json").read_text(encoding="utf-8"))
    walk["frames"][6]["roll_number"] = 25
    write_manifest(strip1 / "survey.json", walk)
    library_root = tmp_path / "library"
    entry = library_root / "e17"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text(json.dumps({
        "scan": {"resolution_dpi": 300, "channels": 3}}), encoding="utf-8")
    write_manifest(strip1 / "roll.json", {
        "roll": "Gold200", "numbering": NUMBERING,
        "strip": {"number": 1, "first": 1},
        "frames": [{"number": 1, "done": True, "entry": "gone",
                    "roll_number": 1},
                   {"number": 7, "done": True, "entry": "e17",
                    "roll_number": 25}]})
    for place in (1, 7):
        (strip1 / f"frame{place:02d}.tif").write_bytes(b"x")
    (gold,) = gui.rolls_on_disk(rolls, library_root)
    assert 25 in gold["done"] and 7 not in gold["done"]
    one = gold["strips"][0]
    one["entries"] = {7: entry}
    items = gui.roll_exports(one)
    assert [(i.place, i.number) for i in items] == [(7, 25)]
    assert gui.batch_name(items[0], "tiff").startswith("frame25_")
    assert gui.export_skipped(one, items) == [1]
    assert gui.roll_losses(one, library_root)["last"] == [1]
    # An entry's Delete names the frame by its record's number.
    said = app._frames_said(entry, {})
    assert said and said[0].startswith(
        "Frame 25 of Gold200 (strip 1 · 7) then counts as not scanned"), said
    # Where no record numbers it, by its place rather than dropped: a
    # Delete's question counts what goes for good.
    shutil.rmtree(entry)
    for name in ("survey.json", "roll.json"):
        data = json.loads((strip1 / name).read_text(encoding="utf-8"))
        for record in data["frames"]:
            if record["number"] == 7:
                del record["roll_number"]
        write_manifest(strip1 / name, data)
    (gold,) = gui.rolls_on_disk(rolls, library_root)
    assert gui.roll_losses(gold["strips"][0], library_root)["last"] == [1, 7]


def test_the_browser_opens_a_rolls_first_strip_with_frames_left(
        window, tmp_path, monkeypatch):
    """Open on a roll's row opens its first strip with frames left to scan,
    and its last where none has; the log says which, and how to open
    another."""
    from rps7200.session import write_manifest

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    # Two walks, nothing scanned: strip 1.
    app.open_roll(roll)
    assert app._sheet_roll == roll / "strip-01"
    assert "opened strip 1 of Gold200" in app.log.get("1.0", "end")
    every = [{"number": n, "done": True, "entry": ""} for n in range(1, 7)]
    write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": every})
    app.open_roll(roll)
    assert app._sheet_roll == roll / "strip-02"
    write_manifest(roll / "strip-02" / "roll.json", {
        "numbering": "strip", "frames": every})
    app.open_roll(roll)
    assert app._sheet_roll == roll / "strip-02", "nothing left: the last"
    app.sheet.top.destroy()


def test_a_strip_deleted_on_its_own_keeps_its_numbers(window, tmp_path,
                                                      monkeypatch):
    """Delete of the roll's open last strip closes it first, in the lock the
    Delete holds, and moves it into the rolls' trash as a strip of its
    roll; the next strip is numbered after its numbers. The question says
    so. A roll's own Delete sums what goes over its strips."""
    from rps7200 import library, locks
    from rps7200.session import plan_next

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, None, 6, True)))
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel",
                        lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    (gold,) = gui.rolls_on_disk(rolls, app.session.root)
    strip2 = gold["strips"][1]
    # A selection of the roll and one of its strips is the roll alone.
    assert gui._RollBrowser._whole([gold, strip2]) == [gold]
    # As after a roll scanned into it: the session holds the roll.
    app.session._hold_roll(roll)
    app.on_delete_rolls([strip2])
    assert "Move strip 2 of Gold200 into the rolls' trash" in asked[-1]
    assert "strip 2 has 7-12. Its numbers stay given" in asked[-1]
    batch = library.in_trash(rolls, "strip-02")
    assert batch is not None and batch.kind == "strips"
    assert batch.within == "Gold200" and not (roll / "strip-02").exists()
    index = json.loads((roll / "strips.json").read_text(encoding="utf-8"))
    assert index["strips"][1]["last"] == 12, "closed at the frames it had"
    assert plan_next(roll).next.first == 13
    said = app.log.get("1.0", "end")
    assert "tools/library.py restore" in said
    # Not let go of for a strip (P23-b): the roll is still there, and the
    # session's; the line says the restore waits for the window to close.
    assert locks.key(roll) in app.session._roll_locks
    assert "once this window has closed, as it holds Gold200" in said
    (gold,) = gui.rolls_on_disk(rolls, app.session.root)
    app.on_delete_rolls([gold])
    # By the strips the folder holds: strip 2 is in the trash already.
    assert "Move 1 roll folder -- Gold200 (1 strip) -- into" in asked[-1]
    assert "1 walk (survey.json)" in asked[-1]
    assert not roll.exists()
    assert locks.key(roll) not in app.session._roll_locks, "let go of"


def test_the_browsers_trash_note_counts_a_strip_apart(window, tmp_path):
    """A strip deleted on its own is no roll: the browser's note counts it
    apart, as the log's line does (`library.trash_said`)."""
    from rps7200 import library

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 6, True),
                                          (2, 7, 12, 6, True)))
    library.trash(rolls, [roll / "strip-02"], by="a test", kind="strips")
    _walked_folder(rolls, count=1)
    library.trash(rolls, [rolls / "walk"], by="a test", kind="rolls")
    browser = gui._RollBrowser(app, gui.rolls_on_disk(rolls))
    try:
        assert "\ntrash: 1 roll and 1 strip, " in browser.v_note.get()
    finally:
        browser.top.destroy()


def test_a_cut_short_copy_of_a_roll_of_strips_goes_index_first(tmp_path,
                                                               monkeypatch):
    """`remove_copy` of a Duplicate of a roll of strips that did not finish:
    what makes it a roll goes first -- its index, then each strip's
    records, the walk last written first -- then everything else, and the
    marker last of all."""
    from pathlib import Path

    from rps7200 import library

    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 2, True),
                                          (2, 7, None, 2, True)),
                           scanned={2: [1]})
    target = gui.duplicate_name(roll)
    gui.reserve_copy(roll, target)
    assert gui.copy_roll(roll, target) is None
    # As a kill leaves one: every record in, the marker still there.
    (target / library.INCOMPLETE).write_text("{}", encoding="utf-8")
    gone = []
    real = gui.os.unlink
    inside = target.resolve()

    def unlink(path, *a, **k):
        # A bare name where `rmtree` removes by its folder's descriptor;
        # a record not there is tried and passed over, and not counted.
        if k.get("dir_fd") is not None:
            gone.append(Path(path).name)
        elif gui.os.path.lexists(path):
            gone.append(Path(path).resolve().relative_to(inside).as_posix())
        return real(path, *a, **k)

    monkeypatch.setattr(gui.os, "unlink", unlink)
    assert gui.remove_copy(target)
    assert not target.exists()
    records = {"strip-01/survey.json", "strip-02/survey.json",
               "strip-02/roll.json"}
    assert gone[0] == "strips.json"
    assert set(gone[1:4]) == records, gone
    assert gone.index("strip-02/survey.json") < gone.index(
        "strip-02/roll.json"), "the walk, written last, goes first"
    assert any(g.endswith(".tif") for g in gone[4:])
    assert gone[-1] == library.INCOMPLETE


def test_a_duplicate_of_a_roll_of_strips_writes_its_records_last(tmp_path):
    """Each strip's records after every file, the index last of all; each
    strip's unfiled/ left out; the marker lists the records inside the
    copy, and a copy cut short is no roll."""
    import unittest.mock
    from pathlib import Path

    from rps7200 import library

    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls, strips=((1, 1, 6, 2, True),
                                          (2, 7, None, 2, True)))
    kept = roll / "strip-02" / gui.UNFILED / "pass"
    kept.mkdir(parents=True)
    (kept / "scan.json").write_text("{}", encoding="utf-8")
    target = gui.duplicate_name(roll)
    gui.reserve_copy(roll, target)
    marker = json.loads((target / library.INCOMPLETE).read_text(
        encoding="utf-8"))
    assert marker["records"] == ["strip-01/survey.json",
                                 "strip-02/survey.json", "strips.json"]
    assert [Path(s["folder"]).name for s in gui.rolls_on_disk(rolls)] == [
        "Gold200"], "the copy is no roll yet"
    order = []
    real = library._replace

    def replace(temp, path):
        order.append(Path(path).relative_to(target).as_posix())
        return real(temp, path)

    with unittest.mock.patch.object(library, "_replace", replace):
        assert gui.copy_roll(roll, target) is None
    assert order == ["strip-01/survey.json", "strip-02/survey.json",
                     "strips.json"]
    assert not (target / "strip-02" / gui.UNFILED).exists()
    assert (target / "strip-01" / "prescan01.tif").is_file()
    assert gui.unfiled_kept(roll) == [kept]
    assert sorted(Path(s["folder"]).name for s in gui.rolls_on_disk(rolls)) \
        == ["Gold200", "Gold200-2"]


def test_a_strip_is_locked_by_its_roll_where_the_window_writes_in_it(
        window, tmp_path, monkeypatch):
    """approved.json of a strip, and a walk carried in, are written under
    the roll's lock: a second program holding the roll is met."""
    from conftest import HeldElsewhere, HolderRefused

    from rps7200 import locks

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls)
    source = _walked_folder(tmp_path, count=1)
    with HeldElsewhere(locks.ROLL, roll):
        app._write_approved([Approved(number=1, offset_mm=0.0)],
                            roll / "strip-01")
        assert "is in use by another program" in app.log.get("1.0", "end")
        assert not (roll / "strip-01" / "approved.json").exists()
        with pytest.raises(locks.Refused):
            gui.carry_walk(source, roll / "strip-02", rolls=rolls)
    # A strip shown here holds its roll as shown: another program's Rename
    # or Delete of the roll is refused while it is.
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.open_roll(roll / "strip-01")
    with pytest.raises(HolderRefused):
        with HeldElsewhere(locks.ROLL_VIEW, roll, exclusive=True):
            pass
    app.sheet.top.destroy()


def test_the_same_strip_walked_again_as_the_next_is_said(window, tmp_path,
                                                         monkeypatch):
    """The first frame of a new strip's walk held up against the strip
    before it's, by the hold's own test: the same picture is said, another
    is not. A warning only."""
    from rps7200 import tiff

    app, root = window
    rolls = tmp_path / "rolls"
    roll = _strips_on_disk(rolls)
    lines = []
    monkeypatch.setattr(app._saves, "put", lines.append)
    threads = []
    real = gui.threading.Thread

    def thread(*a, **k):
        made = real(*a, **k)
        threads.append(made)
        return made

    monkeypatch.setattr(gui.threading, "Thread", thread)
    for name in ("prescan01.tif", "prescan04.tif"):
        image = np.asarray(tiff.read(str(roll / "strip-01" / name)))
        app._check_same_strip(roll / "strip-01",
                              types.SimpleNamespace(image=image))
        threads[-1].join(30)
    assert lines == [("line", "frame 1 of this walk looks like frame 1 of "
                              "strip 1 -- if strip 1 is still in, stop and "
                              "swap it")]
