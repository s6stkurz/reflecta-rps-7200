"""research/frame-position-net: the synthetic prescans and their truth.

Everything here runs on made-up arrays -- no library, no private pictures, no
torch -- so it runs anywhere CI does. What it holds:

- the device's 300 dpi sampling as measured: point samples across, an exact
  box of 0.65 of a line down;
- the three things that must agree exactly: a recipe's truth (`EdgeResult`),
  its per-column training target, and what `fpn.read` reads back off that
  target -- so the network is trained toward the very answer it is scored on;
- the sign of the move and the param, from the driver's own conversion;
- the locked test sets refuse to be loaded for anything but scoring.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "frame-position-net"))

from fpn import compose, film, read, realcompose, sampling, study  # noqa: E402
from fpn.personal import srgb_to_linear  # noqa: E402


def test_the_srgb_table_is_the_standard_curve():
    t = srgb_to_linear()
    assert t[0] == 0.0 and t[255] == pytest.approx(1.0)
    assert np.all(np.diff(t) > 0)
    assert t[10] == pytest.approx(10 / 255 / 12.92)          # the linear toe
    assert t[128] == pytest.approx(((128 / 255 + 0.055) / 1.055) ** 2.4)


def test_columns_are_point_samples_and_rows_an_exact_box():
    k = np.arange(40)
    ramp = ((k + 0.5) / sampling.OVERSAMPLE)[None, :].repeat(3, 0).astype(np.float32)
    assert sampling.point_columns(ramp, np.array([1.0, 2.5]))[0].tolist() == [1.0, 2.5]
    assert np.isnan(sampling.point_columns(ramp, np.array([9.9]))[0, 0])
    step = (np.arange(40) >= 20).astype(np.float32)[:, None]   # an edge at row 5.0
    got = sampling.box_rows(step, np.array([4.8, 5.0, 5.2]), 0.65)[:, 0]
    # a box [c - 0.325, c + 0.325] over a step at 5.0: the share past the step
    assert got == pytest.approx([0.125 / 0.65, 0.5, 0.525 / 0.65], abs=1e-6)


@pytest.mark.parametrize("film_name", ["c41", "bw", "slide"])
def test_truth_target_and_reading_agree_exactly(film_name):
    for seed in range(150):
        r = compose.draw_recipe(film_name, ("pA01", "pA02", "pA03"), "x", seed)
        truth = compose.truth(r)
        got = read.edges(compose.column_target(r).astype(np.float64), sure=0.0)
        for name in ("left", "right"):
            a, b = truth.side(name), got.side(name)
            base = None if a.x is None else (a.x if name == "left" else 428 - a.x)
            if a.state == "edge" and base is not None and base < 1.0:
                continue      # under a column of base is scored as no base anyway
            assert a.state == b.state, (seed, name)
            if a.state == "edge":
                assert b.x == pytest.approx(a.x, abs=1e-6)


def test_the_label_target_matches_the_recipe_target():
    for seed in range(150):
        r = compose.draw_recipe("c41", ("pA01", "pA02", "pA03"), "x", seed)
        t = compose.truth(r)
        if r.strip_end or not r.pictures[1] or t.left.outer or t.right.outer:
            continue
        diff = np.abs(compose.column_target(r) - realcompose.target_from_result(t, 287)).max()
        assert diff < 0.03


def test_base_on_the_left_moves_the_picture_left_with_the_drivers_param():
    from rps7200.direct import DirectScanner
    from tools.frame_edges.centre import columns_to_mm
    from tools.frame_edges.sides import EDGE, PICTURE_TO_BORDER, EdgeResult, Side

    result = EdgeResult(Side(EDGE, x=20.0), Side(PICTURE_TO_BORDER))
    a = read.answer(result)
    assert a.action == "left" and a.units is not None and a.units < 0
    from rps7200.framing import units_per_column
    expected = DirectScanner.param_for_mm(columns_to_mm(a.units / units_per_column(428)))
    assert a.param == expected and 1 <= a.param <= DirectScanner.MAX_CORRECTION_PARAM
    centred = read.answer(EdgeResult(Side(PICTURE_TO_BORDER), Side(PICTURE_TO_BORDER)))
    assert centred.action == "none" and centred.param is None


@pytest.mark.parametrize("film_name", ["c41", "bw", "slide"])
def test_film_curves_run_the_right_way_on_a_neutral_ramp(film_name):
    params = film.FilmParams()
    roll = film.draw_roll(film_name, params, np.random.default_rng(0))
    ramp = np.geomspace(1e-4, 1.0, 50)[:, None, None].repeat(3, axis=2)
    t = film.film_curve(ramp, roll, params)[:, 0, :]
    step = np.diff(t, axis=0)
    if film_name == "slide":
        assert np.all(step >= 0)       # a positive: more light, more transmittance
    else:
        assert np.all(step <= 1e-12)   # a negative: more light, more density


def test_unexposed_negative_film_is_its_base():
    params = film.FilmParams()
    roll = film.draw_roll("c41", params, np.random.default_rng(1))
    t = film.film_curve(np.zeros((1, 1, 3)), roll, params)[0, 0]
    assert t == pytest.approx(roll.base_t, rel=1e-3)


def test_locked_sets_are_refused_outside_scoring():
    for role in ("locked_main", "locked_test2", "locked_bw", "locked_positive"):
        with pytest.raises(PermissionError):
            study.frames(role)


def test_the_split_is_committed_and_keeps_tests_apart():
    split = study.split()
    roles = set(split["frames"].values())
    assert {"compose", "realval", "locked_main", "locked_test2"} <= roles
    assert split["personal_rolls"]["E"] == "synval"
    # one physical strip never straddles compose and realval
    strip = split["main_groups"].get("strip-r0911-r0914-x0920", [])
    assert len({split["frames"][f] for f in strip} - {"unused"}) <= 1


def test_a_second_piece_of_picture_beyond_base_is_the_neighbour():
    """picture | base | picture on one side: the widest piece is this frame,
    the other the neighbour beyond the gap -- an edge with an outer, not a
    refusal. Without base between the pieces, it is still refused."""
    p = np.zeros((4, 428))
    p[2, :30] = 1.0            # read as picture by the network, though a neighbour
    p[0, :30], p[2, :30] = 1.0, 0.0
    p[1, 30:47] = 1.0          # a 17-column gap
    p[0, 47:] = 1.0            # this frame
    got = read.edges(p, sure=0.0)
    assert got.left.state == "edge" and got.left.x == pytest.approx(47.0)
    assert got.left.outer == pytest.approx(30.0)
    assert got.right.state == "picture_to_border"
    q = np.zeros((4, 428))
    q[0] = 1.0
    q[0, 200:202], q[1, 200:202] = 0.0, 1.0   # two columns of "base" inside the picture
    assert read.edges(q, sure=0.0).left.state == "refuse"
