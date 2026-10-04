"""Laying an RGBI pass's infrared onto an RGB pass of the same frame.

The passes are synthesised from one continuous picture, so the true offset
between them is known to the hundredth of a pixel and so is where every speck
of dust in the infrared belongs. Two kinds of offset are drawn: a shift with a
turn, as two top-down passes of a slide sat, and the wander a bottom-up pass
showed on 2026-10-03 -- some 25 px, stepping down the frame and bending across
it -- which one affine map left 1.2-1.9 px from.
"""

import numpy as np
import pytest

from rps7200.merge import (
    MAX_SHIFT_PX,
    Registration,
    lay_on,
    merge_infrared,
    register,
    tile_shift,
    warp,
)

H, W = 600, 900


def displacement(y, x):
    """How far the colour pass sits from the RGBI pass: a shift and a turn,
    of the size measured on a 3600 dpi slide."""
    return (1.2 - 0.6 * y / H + 1.1 * x / W,
            0.3 - 0.8 * y / H + 0.5 * x / W)


def wander(y, x, h=900):
    """A bottom-up pass against a top-down one: ~25 px in y, stepping by up
    to a pixel down the frame, and an x offset that bends across it."""
    return (25.0 + 0.9 * np.sin(2 * np.pi * y / 450) + 0.5 * x / W,
            6.0 + 1.5 * (x / W) ** 2 - 0.8 * y / h)


def render(blobs, rows, moved, shift=displacement):
    """Gaussian blobs drawn at sub-pixel centres -- broadband, like film,
    where sinusoids give phase correlation nothing to hold. ``moved`` draws
    each where the RGBI pass has it: a blob at b in the colour pass sits at
    b - d(b) in the other (d varies by under a tenth of a pixel across a
    blob)."""
    out = np.zeros((rows, W))
    for by, bx, sigma, amp in blobs:
        if moved:
            dy, dx = shift(by, bx)
            by, bx = by - dy, bx - dx
        r = int(4 * sigma) + 2
        y0, y1 = max(0, int(by) - r), min(rows, int(by) + r + 1)
        x0, x1 = max(0, int(bx) - r), min(W, int(bx) + r + 1)
        if y0 >= y1 or x0 >= x1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        out[y0:y1, x0:x1] += amp * np.exp(
            -((yy - by) ** 2 + (xx - bx) ** 2) / (2 * sigma ** 2))
    return out


def passes(seed: int = 0, rgbi_rows: int | None = None, h: int = H,
           shift=displacement):
    rgbi_rows = h if rgbi_rows is None else rgbi_rows
    rng = np.random.default_rng(seed)
    n = int(2500 * h / H)
    centres = np.c_[rng.uniform(-40, h + 40, n), rng.uniform(-10, W + 10, n)]
    sigmas = rng.uniform(1.5, 10, n)
    amps = rng.uniform(-6000, 6000, (n, 3))
    specks = [(rng.uniform(40, h - 40), rng.uniform(40, W - 40), 1.5, -30000.0)
              for _ in range(int(12 * h / H))]

    def planes(rows, moved):
        colour = [30000 + render([(cy, cx, s, a[c]) for (cy, cx), s, a
                                  in zip(centres, sigmas, amps)], rows, moved,
                                 shift)
                  for c in range(3)]
        return colour, 40000 + render(specks, rows, moved, shift)

    colour, truth = planes(h, moved=False)
    other, dust = planes(rgbi_rows, moved=True)
    return (np.clip(np.stack(colour, -1), 0, 65535).astype(np.uint16),
            np.clip(np.stack(other + [dust], -1), 0, 65535).astype(np.uint16),
            truth)


def test_tile_shift_reads_a_whole_pixel_move():
    rng = np.random.default_rng(1)
    a = rng.normal(size=(128, 128)).cumsum(0).cumsum(1)
    b = np.roll(np.roll(a, -3, axis=0), 2, axis=1)
    dy, dx, peak = tile_shift(a, b)
    assert (dy, dx) == pytest.approx((3, -2), abs=0.05)
    assert peak > 0.5


def test_the_shift_and_turn_between_the_passes_is_recovered():
    colour, rgbi, _ = passes()
    reg = register(colour, rgbi)
    assert reg.ok, reg.reason
    assert reg.tiles >= 20
    # Where tiles were measured: the outermost sit half a tile (75 px here)
    # in from the edges, and beyond them the map holds its last values -- a
    # corner is an extrapolation, a tenth of a pixel out at worst.
    for y, x in ((80, 80), (80, W - 81), (H - 81, 80), (H - 81, W - 81),
                 (H / 2, W / 2)):
        # A parabola through a correlation peak is biased by a few
        # hundredths of a pixel. A tenth is far inside anything dust could
        # show.
        assert reg.at(y, x) == pytest.approx(displacement(y, x), abs=0.1)


def test_dust_lands_where_the_colour_pass_has_it():
    colour, rgbi, truth = passes()
    merged, record = merge_infrared(colour, rgbi)
    assert merged is not None, record
    assert merged.shape == (H, W, 4) and merged.dtype == np.uint16
    assert np.array_equal(merged[..., :3], colour), "the colour is the RGB pass's own"
    inner = (slice(8, -8), slice(8, -8))
    err = np.abs(merged[..., 3].astype(float) - truth)[inner]
    unwarped = np.abs(rgbi[..., 3].astype(float) - truth)[inner]
    # A 1.5 px speck 30000 deep: a pixel off reads thousands, on top of it
    # only the interpolation's own few hundred.
    assert err.max() < unwarped.max() / 4, (err.max(), unwarped.max())
    assert record["after_px"]["median_px"] < 0.1
    assert record["ok"] and record["tiles"] >= 20


def test_a_pass_a_line_shorter_is_still_laid_on_whole():
    colour, rgbi, truth = passes(rgbi_rows=H - 2)
    merged, record = merge_infrared(colour, rgbi)
    assert merged is not None, record
    assert merged.shape[:2] == colour.shape[:2]


def test_another_picture_is_refused_rather_than_laid_on():
    colour, _, _ = passes(seed=0)
    _, other, _ = passes(seed=7)
    merged, record = merge_infrared(colour, other)
    assert merged is None
    assert not record["ok"] and record["reason"]


def test_a_blank_frame_has_nothing_to_align_by():
    flat = np.full((H, W, 3), 30000, np.uint16)
    rgbi = np.full((H, W, 4), 30000, np.uint16)
    merged, record = merge_infrared(flat, rgbi)
    assert merged is None
    assert "matched" in record["reason"]


def test_passes_far_apart_are_refused():
    colour, _, _ = passes()
    far = np.roll(colour, int(MAX_SHIFT_PX) + 8, axis=1)
    rgbi = np.concatenate([far, far[..., :1]], axis=-1)
    merged, record = merge_infrared(colour, rgbi)
    assert merged is None
    assert "apart" in record["reason"]


def test_a_bottom_up_pass_wandering_down_the_frame_is_followed():
    """What one affine map could not do: 9 of 12 slide pairs on 2026-10-03
    went out without infrared for it."""
    h = 900
    colour, rgbi, truth = passes(seed=2, h=h, shift=lambda y, x: wander(y, x, h))
    merged, record = merge_infrared(colour, rgbi)
    assert merged is not None, record
    assert record["after_px"]["median_px"] < 0.15
    reg = register(colour, rgbi)
    for y in np.linspace(60, h - 60, 12):
        for x in (60, W / 2, W - 60):
            assert reg.at(y, x) == pytest.approx(wander(y, x, h), abs=0.2), (y, x)
    inner = (slice(40, -40), slice(12, -12))
    err = np.abs(merged[..., 3].astype(float) - truth)[inner]
    assert err.max() < 30000 / 4


def test_a_zero_map_warps_nothing():
    rng = np.random.default_rng(3)
    plane = rng.integers(0, 65535, (300, 200), dtype=np.uint16)
    assert np.array_equal(warp(plane, Registration.flat()), plane)


def test_a_window_of_the_warp_is_the_same_as_the_whole():
    colour, rgbi, _ = passes()
    reg = register(colour, rgbi)
    whole = warp(rgbi[..., 3], reg, colour.shape[:2])
    part = warp(rgbi[..., 3], reg, colour.shape[:2],
                rows=(100, 357), cols=(40, 300))
    assert np.array_equal(part, whole[100:357, 40:300])


def test_a_stored_map_lays_the_infrared_on_exactly_as_the_measured_one():
    """The entry keeps the map, so a view or an export need not measure it
    again; what it rebuilds from the record must be the same picture."""
    colour, rgbi, _ = passes()
    merged, record = merge_infrared(colour, rgbi)
    assert merged is not None, record
    again = Registration.from_record(record)
    assert again is not None
    rebuilt = lay_on(colour, rgbi, again)
    assert np.abs(rebuilt.astype(int) - merged.astype(int)).max() <= 1


def test_a_refused_or_foreign_record_is_no_map():
    colour, _, _ = passes()
    _, other, _ = passes(seed=7)
    _, refused = merge_infrared(colour, other)
    assert Registration.from_record(refused) is None
    assert Registration.from_record({"affine": [[0, 0, 0], [0, 0, 0]],
                                     "ok": True}) is None


def test_no_infrared_means_nothing_to_merge():
    colour, rgbi, _ = passes()
    merged, record = merge_infrared(colour, rgbi[..., :3])
    assert merged is None and "no infrared" in record["reason"]
