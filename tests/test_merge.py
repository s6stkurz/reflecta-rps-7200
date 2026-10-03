"""Laying an RGBI pass's infrared onto an RGB pass of the same frame.

The passes are synthesised from one continuous picture, so the true offset
between them is known to the hundredth of a pixel and so is where every speck
of dust in the infrared belongs.
"""

import numpy as np
import pytest

from rps7200.merge import (
    MAX_SHIFT_PX,
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


def render(blobs, rows, moved):
    """Gaussian blobs drawn at sub-pixel centres -- broadband, like film,
    where sinusoids give phase correlation nothing to hold. ``moved`` draws
    each where the RGBI pass has it: a blob at b in the colour pass sits at
    b - d(b) in the other (d varies by ~1e-3 px across a blob)."""
    out = np.zeros((rows, W))
    for by, bx, sigma, amp in blobs:
        if moved:
            dy, dx = displacement(by, bx)
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


def passes(seed: int = 0, rgbi_rows: int = H):
    rng = np.random.default_rng(seed)
    n = 2500
    centres = np.c_[rng.uniform(-10, H + 10, n), rng.uniform(-10, W + 10, n)]
    sigmas = rng.uniform(1.5, 10, n)
    amps = rng.uniform(-6000, 6000, (n, 3))
    specks = [(rng.uniform(40, H - 40), rng.uniform(40, W - 40), 1.5, -30000.0)
              for _ in range(12)]

    def planes(rows, moved):
        colour = [30000 + render([(cy, cx, s, a[c]) for (cy, cx), s, a
                                  in zip(centres, sigmas, amps)], rows, moved)
                  for c in range(3)]
        return colour, 40000 + render(specks, rows, moved)

    colour, truth = planes(H, moved=False)
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
    for y, x in ((0, 0), (0, W - 1), (H - 1, 0), (H - 1, W - 1), (H / 2, W / 2)):
        # A parabola through a correlation peak is biased by a few
        # hundredths of a pixel, and a corner is extrapolated from tiles
        # further in. A tenth is far inside anything dust could show.
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


def test_a_zero_map_warps_nothing():
    rng = np.random.default_rng(3)
    plane = rng.integers(0, 65535, (300, 200), dtype=np.uint16)
    assert np.array_equal(warp(plane, np.zeros((2, 3))), plane)


def test_a_window_of_the_warp_is_the_same_as_the_whole():
    colour, rgbi, _ = passes()
    reg = register(colour, rgbi)
    whole = warp(rgbi[..., 3], reg.affine, colour.shape[:2])
    part = warp(rgbi[..., 3], reg.affine, colour.shape[:2],
                rows=(100, 357), cols=(40, 300))
    assert np.array_equal(part, whole[100:357, 40:300])


def test_no_infrared_means_nothing_to_merge():
    colour, rgbi, _ = passes()
    merged, record = merge_infrared(colour, rgbi[..., :3])
    assert merged is None and "no infrared" in record["reason"]
