"""Delivering black and white as one channel.

The scanner has no monochrome mode worth using, so a B&W scan is an RGB scan
and the reduction is a host-side step. It has to happen here because the
consumer cannot work it out from the pixels: measured across this library, B&W
channel correlation spans 0.926-0.988 and colour negative 0.008-0.976, which
overlap.
"""

import numpy as np
import pytest

from rps7200.mono import MONO_CHANNEL, to_monochrome


def scene(h=32, w=48, channels=3):
    rng = np.random.default_rng(7)
    base = np.linspace(2000, 40000, w)[None, :] * np.ones((h, 1))
    out = np.empty((h, w, channels), np.uint16)
    for c in range(channels):
        out[..., c] = np.clip(base * (1.0 + 0.1 * c)
                              + rng.normal(0, 200, (h, w)), 0, 65535)
    return out


def test_it_returns_one_plane():
    img = scene()
    mono = to_monochrome(img)
    assert mono.shape == img.shape[:2]
    assert mono.ndim == 2, "a consumer must see a 2-D image, not (H, W, 1)"
    assert mono.dtype == img.dtype


def test_the_default_channel_is_green():
    """Green and blue measure the same on random noise and are comparably
    sharp; red is worse on both. Green breaks the tie because it is the filter
    the hardware's own single-filter mode uses."""
    assert MONO_CHANNEL == "G"
    img = scene()
    assert np.array_equal(to_monochrome(img), img[..., 1])


@pytest.mark.parametrize("channel,index", [("R", 0), ("G", 1), ("B", 2)])
def test_each_channel_can_be_asked_for(channel, index):
    img = scene()
    assert np.array_equal(to_monochrome(img, channel), img[..., index])


def test_it_does_not_touch_the_input():
    """The three-channel array is what the library files, and a merged channel
    cannot be un-merged."""
    img = scene()
    before = img.copy()
    mono = to_monochrome(img)
    mono[0, 0] = 12345
    assert np.array_equal(img, before)


def test_an_infrared_scan_still_yields_its_visible_channel():
    img = scene(channels=4)
    assert np.array_equal(to_monochrome(img, "G"), img[..., 1])


def test_a_channel_the_scan_does_not_have_is_refused():
    with pytest.raises(ValueError, match="not in this scan"):
        to_monochrome(scene(channels=3), "I")


def test_an_already_flat_image_passes_through():
    flat = scene()[..., 1]
    assert np.array_equal(to_monochrome(flat), flat)


def test_a_consumer_that_classifies_by_channel_correlation_cannot_be_wrong():
    """NegPy expands a single-channel TIFF to three identical planes, so the
    minimum pairwise correlation it measures is exactly 1.0 and its `> 0.99`
    monochrome test cannot go the other way.

    On three real channels it does go the other way: every B&W scan in this
    library measured below that threshold.
    """
    img = scene()

    def min_corr(a):
        if a.ndim == 2:                      # what the consumer's loader does
            a = np.stack([a] * 3, axis=-1)
        a = a.astype(np.float64)
        def corr(x, y):
            x = x.ravel() - x.mean()
            y = y.ravel() - y.mean()
            return float((x*y).sum() / (np.sqrt((x*x).sum()*(y*y).sum()) + 1e-12))
        return min(corr(a[...,0], a[...,1]), corr(a[...,1], a[...,2]),
                   corr(a[...,0], a[...,2]))

    assert min_corr(to_monochrome(img)) == pytest.approx(1.0)
