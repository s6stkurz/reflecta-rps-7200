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


def test_aiming_at_the_centre_asks_for_no_movement():
    assert gui.aim_millimetres(0.5) == pytest.approx(0.0, abs=1e-9)


def test_aiming_left_and_right_are_mirror_images():
    """The arithmetic must not favour a direction; only the transport does."""
    assert gui.aim_millimetres(0.25) == pytest.approx(-gui.aim_millimetres(0.75))


def test_the_edges_are_half_an_aperture_away():
    """36.49 mm across the window, so an edge is 18.2 mm from centred -- far
    beyond anything a sub-frame move can deliver, which is why the dialog says
    what it will actually do rather than promising the click."""
    assert gui.aim_millimetres(0.0) == pytest.approx(gui.APERTURE_MM / 2, abs=0.01)
    assert gui.aim_millimetres(1.0) == pytest.approx(-gui.APERTURE_MM / 2, abs=0.01)


def test_the_aperture_matches_the_transport_window():
    """36.49 mm against a 36 mm frame -- the half-millimetre of slack the
    registration work is all about."""
    assert gui.APERTURE_MM == pytest.approx(36.49, abs=0.01)


def test_a_click_one_pixel_off_centre_is_below_what_the_hardware_can_do():
    """At 300 dpi the prescan is ~431 px across, so one pixel is 0.085 mm --
    a third of the smallest step. Clicking precisely is not the hard part."""
    per_pixel = abs(gui.aim_millimetres(0.5 + 1 / 431))
    assert per_pixel < gui.FINE_STEP_MM


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
