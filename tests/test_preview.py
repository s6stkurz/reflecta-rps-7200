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
