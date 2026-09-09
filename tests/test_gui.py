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


def test_the_dpi_ladder_is_the_divisors_the_captures_use():
    assert gui.DPI_LADDER[0] == 300 and gui.DPI_LADDER[-1] == 7200
    assert all(7200 % d == 0 for d in gui.DPI_LADDER)
    for seen_in_captures in (300, 600, 900, 1800, 3600):
        assert seen_in_captures in gui.DPI_LADDER


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
    assert gui.PRESCAN_LADDER[0] == 300
    assert all(d <= 1200 for d in gui.PRESCAN_LADDER), "a prescan is meant to be cheap"


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
