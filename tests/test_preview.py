"""The display path: pixels in, something a screen can show out.

Nothing here touches the scanner or a stored file. These are the transforms
between a decoded frame and a Tk `PhotoImage`, and the reason they are tested at
all is that the GUI's preview and the comparison files Stefan judges by eye now
run through the same code -- so a change to one is a change to both.
"""
import numpy as np
import pytest

from rps7200 import preview


def rgbi(h=40, w=60, seed=1):
    rng = np.random.default_rng(seed)
    return (rng.random((h, w, 4)) * 65535).astype(np.uint16)


def test_every_channel_renders_to_screen_shaped_bytes():
    image = rgbi()
    for channel in preview.CHANNELS:
        out = preview.render(image, channel)
        assert out.shape == (40, 60, 3), channel
        assert out.dtype == np.uint8, channel


def test_a_three_channel_pass_has_no_infrared_to_offer():
    """A prescan is RGB 8-bit. Offering IR would draw a black rectangle and
    leave the operator wondering whether the infrared really came back empty."""
    prescan = (np.random.default_rng(2).random((20, 30, 3)) * 255).astype(np.uint8)
    assert "IR" not in preview.channels_available(prescan)
    assert "IR" in preview.channels_available(rgbi())
    with pytest.raises(ValueError, match="no 'IR'"):
        preview.render(prescan, "IR")


def test_a_single_plane_comes_back_grey_not_tinted():
    """The infrared view is a measurement, not a colour."""
    out = preview.render(rgbi(), "IR")
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])


def test_inverting_turns_the_bright_end_dark():
    ramp = np.linspace(0, 65535, 64, dtype=np.uint16).reshape(1, 64)
    ramp = np.repeat(ramp[..., None], 3, axis=2)
    positive = preview.render(ramp, "RGB", invert=False)
    negative = preview.render(ramp, "RGB", invert=True)
    assert positive[0, 0, 0] < positive[0, -1, 0]
    assert negative[0, 0, 0] > negative[0, -1, 0]


def test_a_flat_frame_does_not_divide_by_zero():
    """An unexposed prescan has no range at all, and used to come out NaN."""
    out = preview.render(np.zeros((8, 8, 3), np.uint16))
    assert np.isfinite(out).all()
    assert out.dtype == np.uint8


def test_the_stretch_is_per_channel():
    """A negative's orange mask is a huge constant offset between channels.
    Stretched jointly, the picture lands inside a tenth of the range."""
    image = np.zeros((10, 10, 3), np.uint16)
    image[..., 0] = np.linspace(40000, 60000, 100).reshape(10, 10)
    image[..., 1] = np.linspace(2000, 6000, 100).reshape(10, 10)
    out = preview.normalise(image)
    for c in (0, 1):
        assert out[..., c].min() == pytest.approx(0.0, abs=0.01)
        assert out[..., c].max() == pytest.approx(1.0, abs=0.01)


def test_the_stretch_is_the_one_the_comparison_files_use():
    """`tools/make_comparison.py` writes the files Stefan judges by eye and now
    shares this code. Two copies of "how a negative is made judgeable" would
    drift, and the drift would only show up as an argument about a picture."""
    image = rgbi(seed=5)[..., :3]
    x = image.astype(np.float64)
    expected = np.empty_like(x)
    for c in range(3):
        lo, hi = np.percentile(x[..., c], [0.5, 99.5])
        expected[..., c] = 65535 - np.clip((x[..., c] - lo) / (hi - lo), 0, 1) * 65535
    got = np.clip((1.0 - preview.normalise(image)) * 65535, 0, 65535)
    assert np.abs(expected - got).max() < 1e-6


def test_ppm_is_what_tk_expects():
    out = preview.render(rgbi(8, 5))
    blob = preview.to_ppm(out)
    assert blob.startswith(b"P6\n5 8\n255\n")
    assert len(blob) == len(b"P6\n5 8\n255\n") + 8 * 5 * 3


def test_ppm_refuses_anything_but_screen_bytes():
    with pytest.raises(ValueError):
        preview.to_ppm(np.zeros((4, 4, 4), np.uint8))
    with pytest.raises(ValueError):
        preview.to_ppm(np.zeros((4, 4, 3), np.uint16))


@pytest.mark.parametrize("shape", [(6888, 10344), (3444, 5172), (287, 431)])
def test_downscale_bounds_the_long_side_and_keeps_the_aspect(shape):
    image = np.zeros((*shape, 4), np.uint16)
    out = preview.downscale(image, 256)
    assert max(out.shape[:2]) <= 256
    before = shape[1] / shape[0]
    after = out.shape[1] / out.shape[0]
    assert after == pytest.approx(before, rel=0.05)


def test_downscale_leaves_a_small_image_alone():
    image = np.zeros((100, 120, 3), np.uint16)
    assert preview.downscale(image, 256) is image


def test_fit_lands_inside_the_box():
    image = np.zeros((3444, 5172, 4), np.uint16)
    out = preview.fit(image, 900, 600)
    assert out.shape[0] <= 600 and out.shape[1] <= 900


def test_a_crop_at_the_edge_stays_inside_the_image():
    image = np.arange(40 * 60, dtype=np.uint16).reshape(40, 60)
    out = preview.crop(image, 0, 0, 20, 10)
    assert out.shape == (10, 20)
    assert np.array_equal(out, image[0:10, 0:20])


def test_a_crop_larger_than_the_image_is_the_image():
    image = np.zeros((12, 14), np.uint16)
    assert preview.crop(image, 6, 7, 500, 500).shape == (12, 14)


# -- turning a picture ------------------------------------------------------


def test_a_quarter_turn_swaps_the_sides():
    image = rgbi(40, 60)
    assert preview.rotate(image, 90).shape == (60, 40, 4)
    assert preview.rotate(image, 270).shape == (60, 40, 4)
    assert preview.rotate(image, 180).shape == (40, 60, 4)
    assert preview.rotate(image, 0).shape == (40, 60, 4)


def test_turning_is_lossless():
    """A scan is a measurement. A rotation that resampled would not be the same
    file any more, which is why only quarter turns are offered."""
    image = rgbi(13, 17)
    turned = preview.rotate(image, 90)
    assert sorted(turned.ravel().tolist()) == sorted(image.ravel().tolist())


def test_four_quarter_turns_come_home():
    image = rgbi(13, 17)
    there = image
    for _ in range(4):
        there = preview.rotate(there, 90)
    assert np.array_equal(there, image)


def test_left_and_right_undo_each_other():
    image = rgbi(9, 14)
    assert np.array_equal(preview.rotate(preview.rotate(image, 90), 270), image)


def test_anything_but_a_quarter_turn_is_refused():
    with pytest.raises(ValueError, match="quarter turn"):
        preview.rotate(rgbi(4, 4), 45)


@pytest.mark.parametrize("degrees", preview.ROTATIONS)
def test_a_point_on_a_turned_view_maps_back_to_where_it_came_from(degrees):
    """This is what keeps aiming honest on a turned prescan: the film moves
    along the unrotated x axis however the picture looks on screen."""
    marked = np.zeros((4, 7), np.uint16)
    marked[1, 5] = 999                       # y=1, x=5
    turned = preview.rotate(marked, degrees)
    ys, xs = np.where(turned == 999)
    back = preview.unrotate_point(int(xs[0]), int(ys[0]), turned.shape, degrees)
    assert (round(back[0]), round(back[1])) == (5, 1), degrees


def test_an_unturned_point_is_left_alone():
    assert preview.unrotate_point(3, 4, (10, 10), 0) == (3, 4)


# -- levels, measured once and reused --------------------------------------


def test_levels_are_one_pair_per_channel():
    image = rgbi(30, 40)
    assert preview.levels(image).shape == (4, 2)
    assert preview.levels(image[..., 0]).shape == (1, 2)


def test_supplying_levels_gives_the_same_picture_as_measuring_them():
    """The whole point of caching them is that nothing changes but the cost."""
    image = rgbi(60, 80, seed=11)
    measured = preview.render(image, "RGB")
    supplied = preview.render(
        image, "RGB", cuts=preview.channel_levels(preview.levels(image), "RGB"))
    assert np.array_equal(measured, supplied)


def test_a_crop_keeps_the_whole_pictures_levels():
    """Otherwise the brightness changes as you pan, and the same negative looks
    different depending on where you happen to be looking."""
    image = rgbi(80, 120, seed=3)
    image[:40] //= 4                         # a dark half and a bright half
    cuts = preview.channel_levels(preview.levels(image), "RGB")
    dark_alone = preview.render(image[:40], "RGB")
    dark_in_context = preview.render(image[:40], "RGB", cuts=cuts)
    assert not np.array_equal(dark_alone, dark_in_context), (
        "stretching a crop on its own is what made panning change the picture")
    whole = preview.render(image, "RGB", cuts=cuts)
    assert np.array_equal(whole[:40], dark_in_context)


def test_channel_levels_picks_the_right_rows():
    image = rgbi(20, 20)
    all_levels = preview.levels(image)
    assert np.array_equal(preview.channel_levels(all_levels, "RGB"), all_levels[:3])
    assert np.array_equal(preview.channel_levels(all_levels, "IR"), all_levels[[3]])
    assert np.array_equal(preview.channel_levels(all_levels, "G"), all_levels[[1]])


def test_levels_are_exact_not_sampled():
    """The comparison files Stefan judges by eye come through here, and a
    subsampled percentile would move them."""
    image = rgbi(200, 300, seed=4)
    for c in range(4):
        want = np.percentile(image[..., c], [preview.LOW, preview.HIGH])
        assert preview.levels(image)[c] == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_the_lookup_path_matches_the_arithmetic_it_replaced(dtype):
    top = 255 if dtype == np.uint8 else 65535
    rng = np.random.default_rng(9)
    image = (rng.random((40, 55, 3)) * top).astype(dtype)
    cuts = preview.channel_levels(preview.levels(image), "RGB")
    through_lut = preview.render(image, "RGB", invert=True, cuts=cuts)
    x = preview.normalise(image, cuts=cuts)
    by_hand = np.clip((1.0 - x) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    assert np.array_equal(through_lut, by_hand)


# -- sampling at any scale --------------------------------------------------


def test_sampling_at_one_to_one_is_the_pixels_themselves():
    image = rgbi(40, 60)
    assert np.array_equal(preview.sample(image, 1.0, 0, 0, 20, 15), image[:15, :20])


def test_sampling_at_half_matches_striding():
    image = rgbi(40, 60)
    assert np.array_equal(preview.sample(image, 0.5, 0, 0, 30, 20),
                          image[:40:2, :60:2])


def test_sampling_above_one_replicates_rather_than_inventing():
    """A scan is a measurement. Upscaling shows bigger pixels, not guesses."""
    image = rgbi(10, 12)
    out = preview.sample(image, 2.0, 0, 0, 8, 6)
    assert np.array_equal(out[0, 0], out[0, 1])
    assert np.array_equal(out[0, 0], image[0, 0])


def test_the_offset_is_honoured():
    image = rgbi(30, 40)
    out = preview.sample(image, 1.0, 7, 5, 4, 3)
    assert np.array_equal(out, image[5:8, 7:11])


@pytest.mark.parametrize("scale", [0.31, 0.437, 0.79, 1.0, 1.37, 2.63])
def test_any_scale_gives_the_size_asked_for(scale):
    """Whole-number decimation could only show 50%, 100%, 200%, so a smooth
    zoom arrived on screen as jumps between them."""
    image = rgbi(200, 300)
    out = preview.sample(image, scale, 0, 0, 120, 90)
    assert out.shape == (90, 120, 4)


def test_sampling_past_the_edge_clamps_rather_than_wrapping():
    image = rgbi(20, 20)
    out = preview.sample(image, 1.0, 15, 15, 20, 20)
    assert out.shape == (20, 20, 4)
    assert np.array_equal(out[-1, -1], image[-1, -1])


def test_an_empty_window_is_empty_not_an_error():
    assert preview.sample(rgbi(10, 10), 1.0, 0, 0, 0, 0).size == 0


def test_narrowing_the_columns_does_not_change_the_pixels():
    """The optimisation that keeps a zoomed view off 142 MB of memory has to be
    invisible in the result."""
    image = rgbi(60, 200, seed=6)
    out = preview.sample(image, 1.0, 120, 10, 30, 20)
    assert np.array_equal(out, image[10:30, 120:150])


def test_the_same_view_reads_the_same_place_from_a_finer_copy():
    """The window measures the view against a reduced copy and samples either
    that or the full-resolution scan. Converting between them is one rule -- the
    start is `detail` times further in, the scale `detail` times smaller -- and
    getting it backwards samples a region `detail` squared too small and blows
    it up. Every coordinate stays consistent with every other when that happens,
    so only comparing what the two actually show catches it.

    A smooth picture, so that nearest neighbour landing on a different pixel of
    the finer array is a small difference rather than a random one.
    """
    y, x = np.mgrid[0:400, 0:600]
    smooth = ((x * 40 + y * 25) % 60000).astype(np.uint16)
    fine = np.repeat(smooth[:, :, None], 3, axis=2)
    detail = 4
    coarse = np.ascontiguousarray(fine[::detail, ::detail])

    for scale, x0, y0 in ((1.5, 20.0, 12.0), (0.5, 5.0, 3.0), (3.0, 40.0, 30.0)):
        from_coarse = preview.sample(coarse, scale, x0, y0, 40, 30)
        from_fine = preview.sample(fine, scale / detail,
                                   x0 * detail, y0 * detail, 40, 30)
        assert from_coarse.shape == from_fine.shape
        apart = np.abs(from_coarse.astype(int) - from_fine.astype(int)).mean()
        assert apart < 800, f"scale {scale}: {apart:.0f} apart -- a different place"


def test_getting_the_conversion_backwards_lands_somewhere_else():
    """A guard on the guard: the wrong rule must not accidentally agree."""
    y, x = np.mgrid[0:400, 0:600]
    smooth = ((x * 40 + y * 25) % 60000).astype(np.uint16)
    fine = np.repeat(smooth[:, :, None], 3, axis=2)
    detail = 4
    coarse = np.ascontiguousarray(fine[::detail, ::detail])

    right = preview.sample(fine, 1.5 / detail, 80.0, 48.0, 40, 30)
    wrong = preview.sample(fine, 1.5 * detail, 80.0, 48.0, 40, 30)
    reference = preview.sample(coarse, 1.5, 20.0, 12.0, 40, 30)
    # Measured: the right rule lands 64 from the reference, the wrong one
    # 2797 -- it samples a six-pixel strip where a hundred were wanted.
    assert np.abs(right.astype(int) - reference.astype(int)).mean() < 800
    assert np.abs(wrong.astype(int) - reference.astype(int)).mean() > 1500


# -- levels between the working copy and the scan ---------------------------


def test_a_pyramid_reaches_from_the_copy_to_the_scan():
    image = rgbi(400, 600)
    levels = preview.pyramid(image, 4.0)
    factors = [f for f, _ in levels]
    assert factors == sorted(factors), "coarsest first"
    assert factors[-1] == 4.0 and levels[-1][1] is image
    assert 2.0 in factors, "a step in between, or the jump is the whole way"


def test_each_level_is_the_size_its_factor_claims():
    image = rgbi(400, 600)
    for factor, array in preview.pyramid(image, 4.0):
        assert array.shape[1] == pytest.approx(600 * factor / 4.0, rel=0.01)


def test_levels_are_decimated_not_averaged():
    """A level is the pixels the scanner sent with some left out. An average
    would show a smoothness the file does not have."""
    image = rgbi(200, 300, seed=8)
    levels = dict(preview.pyramid(image, 4.0))
    half = levels[2.0]
    assert np.array_equal(half, image[::2, ::2])


def test_a_shallow_pyramid_is_just_the_image():
    """Nothing to put in between when the copy is already close to the scan."""
    image = rgbi(100, 120)
    assert [f for f, _ in preview.pyramid(image, 1.0)] == [1.0]


def test_every_level_is_contiguous():
    """A strided view would put the gather back on scattered memory, which is
    the cost the levels exist to avoid."""
    image = rgbi(200, 400)
    for _factor, array in preview.pyramid(image, 4.0):
        assert array.flags["C_CONTIGUOUS"]
