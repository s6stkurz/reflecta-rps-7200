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
