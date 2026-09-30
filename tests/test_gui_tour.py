"""The GUI tour's judgement of a frame: what it calls a band, blank or shifted.

The tour itself opens a window and is run by hand, or by the demo-gui-tester
agent; these hold the measures its verdicts rest on. A measure that passes a
band is a tour that calls a broken prescan clean.
"""
import numpy as np

from conftest import load_tool

tour = load_tool("gui_tour")


def grain(height=40, width=60, seed=0, dtype=np.uint16):
    """A picture no two of whose neighbouring columns are equal, as film is."""
    rng = np.random.default_rng(seed)
    base = np.linspace(4000, 30000, width)[None, :, None]
    return (base + rng.normal(0, 300, (height, width, 3))).clip(
        0, 65535).astype(dtype)


def test_a_picture_with_grain_is_clean():
    problems, facts = tour.frame_problems(grain())
    assert problems == [], problems
    assert facts["repeated_columns"]["longest"] == 0


def test_an_edge_column_repeated_is_a_band():
    """What the demo once drew where the film moved: the last column, again
    and again, down one side of every aimed frame."""
    picture = grain()
    picture[:, -16:] = picture[:, -17:-16]
    problems, facts = tour.frame_problems(picture)
    assert any("band of 17" in p for p in problems), problems
    assert facts["repeated_columns"]["right"] == 16


def test_a_mirrored_strip_has_no_run_and_is_left_to_the_bit_check():
    """A mirror repeats one column, at the fold. It is caught by comparing
    with the stored source, not by this measure -- which is why both run."""
    picture = grain()
    picture[:, -8:] = picture[:, -9:-17:-1]
    problems, _ = tour.frame_problems(picture)
    assert not any("band" in p for p in problems), problems


def test_a_flat_bar_at_an_edge_is_found():
    picture = grain()
    picture[:, :4] = 0
    problems, _ = tour.frame_problems(picture)
    assert any("flat bar" in p for p in problems), problems


def test_a_blank_picture_is_blank():
    problems, _ = tour.frame_problems(np.full((40, 60, 3), 900, np.uint16))
    assert any(p.startswith("blank") for p in problems), problems


def test_a_picture_drawn_twice_its_size_is_not_a_band_on_screen():
    """Nearest-neighbour doubles every column, which is not a band there."""
    shown = np.repeat(grain(dtype=np.uint8), 2, axis=1)
    problems, _ = tour.frame_problems(shown, rail=False,
                                      band=tour.SHOT_BAND_COLUMNS)
    assert problems == [], problems


def test_the_rail_is_counted_per_channel():
    picture = grain()
    picture[:10, :, 2] = 65535
    _, facts = tour.frame_problems(picture)
    assert facts["rail_top"][2] == 0.25 and facts["rail_top"][0] == 0.0


def test_a_known_shift_is_found_between_two_views():
    picture = grain(width=200)
    moved = np.roll(picture, 12, axis=1)
    assert tour._column_shift(picture, moved)[0] == 12
    assert tour._column_shift(picture, picture) == (0, 1.0)
