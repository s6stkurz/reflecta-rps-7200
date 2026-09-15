"""The DNG that carries infrared out of a JPEG delivery.

Two things have to hold, and they are different kinds of thing. The **pixels**
must survive exactly -- a DNG is the full-depth copy of a pass, so any loss here
would make it worse than the TIFF it replaces. And the **tags** must be the ones
the consumer matches on: NegPy finds an infrared plane by looking for a page with
``SamplesPerPixel == 4`` and ``PhotometricInterpretation == 34892``
(``negpy/infrastructure/loaders/rawpy_loader.py``, ``_peek_linearraw_4ch``), and
a file that got either wrong would open as an ordinary picture with the infrared
silently gone. So the tags are read back from the bytes here rather than trusted.

The tag reader below is deliberately its own small thing rather than
``tifffile``: it runs on the bare install too, and what it asserts *is* the byte
layout, which is the point.
"""
from __future__ import annotations

import struct

import numpy as np
import pytest

from rps7200 import dng, tiff

# The tags worth naming in a test. Numbers, because that is what is in the file.
PHOTOMETRIC = 262
SAMPLES_PER_PIXEL = 277
EXTRA_SAMPLES = 338
DNG_VERSION = 50706
NEW_SUBFILE_TYPE = 254
X_RESOLUTION = 282
BITS_PER_SAMPLE = 258
COMPRESSION = 259
ORIENTATION = 274
STRIP_OFFSETS = 273
STRIP_BYTE_COUNTS = 279

IMPLEMENTATIONS = ["builtin", "tifffile"]


def sample(shape=(17, 23, 4), dtype=np.uint16, seed=0):
    rng = np.random.default_rng(seed)
    high = 65535 if dtype == np.uint16 else 255
    return rng.integers(0, high, size=shape, dtype=np.uint32).astype(dtype)


def tags(path) -> dict[int, list[int]]:
    """Every IFD entry of a single-page little-endian TIFF, as plain integers.

    Enough of a reader to check a tag set and no more: RATIONAL comes back as
    its numerator and denominator, ASCII as its bytes, and anything else as
    whatever fixed-width integers it holds.
    """
    data = open(path, "rb").read()
    assert data[:2] == b"II", "this writer emits little-endian files"
    assert struct.unpack_from("<H", data, 2)[0] == 42, "classic TIFF, not BigTIFF"
    (ifd,) = struct.unpack_from("<I", data, 4)
    (count,) = struct.unpack_from("<H", data, ifd)
    widths = {1: ("B", 1), 2: ("B", 1), 3: ("H", 2), 4: ("I", 4), 5: ("I", 4)}
    out: dict[int, list[int]] = {}
    for i in range(count):
        at = ifd + 2 + 12 * i
        tag, ftype, n = struct.unpack_from("<HHI", data, at)
        fmt, size = widths.get(ftype, ("B", 1))
        if ftype == 5:                                   # RATIONAL: two LONGs each
            n *= 2
        total = size * n
        where = at + 8
        if total > 4:
            (where,) = struct.unpack_from("<I", data, at + 8)
        out[tag] = list(struct.unpack_from("<" + fmt * n, data, where))
    return out


@pytest.fixture
def using(monkeypatch):
    """Force one `tiff.read` implementation, skipping if it is not installed."""
    real = tiff._has_tifffile()

    def choose(name):
        if name == "tifffile" and not real:
            pytest.skip("tifffile is not installed")
        monkeypatch.setattr(tiff, "_has_tifffile", lambda: name == "tifffile")
        assert tiff._has_tifffile() is (name == "tifffile")

    return choose


# -- the pixels -------------------------------------------------------------


@pytest.mark.parametrize("reader", IMPLEMENTATIONS)
@pytest.mark.parametrize("dtype", [np.uint16, np.uint8])
def test_the_pixels_come_back_exactly(tmp_path, using, reader, dtype):
    """A DNG is a TIFF, so `tiff.read` reads one -- which is the cross-check
    that pays for this module not going through `tiff.write`. Both readers,
    because either may be the one installed."""
    image = sample(dtype=dtype)
    out = tmp_path / "frame.dng"
    dng.write(out, image)
    using(reader)
    back = tiff.read(str(out))
    assert back.shape == image.shape
    assert back.dtype == image.dtype
    assert np.array_equal(back, image), "a DNG is the full-depth copy; nothing is lossy here"


def test_a_picture_that_needs_several_strips(tmp_path, monkeypatch):
    """Every real scan takes more than one 8 MiB strip, and the offsets and byte
    counts are the part most likely to be wrong once and never noticed. The
    boundary is moved rather than the picture grown, so the arithmetic is tested
    without carrying an eight-megabyte array through the suite."""
    monkeypatch.setattr(dng, "_TARGET_STRIP_BYTES", 1024)
    image = sample(shape=(60, 23, 4))
    out = tmp_path / "tall.dng"
    dng.write(out, image)
    found = tags(out)
    assert len(found[STRIP_BYTE_COUNTS]) > 1, "should have taken several strips"
    assert len(found[STRIP_OFFSETS]) == len(found[STRIP_BYTE_COUNTS])
    assert sum(found[STRIP_BYTE_COUNTS]) == image.nbytes
    assert np.array_equal(tiff.read(str(out)), image)


def test_eight_bit_samples_are_not_stretched_into_sixteen(tmp_path):
    """The reader scales by the dtype's own maximum, so widening here would
    only make the file twice the size for no more information."""
    out = tmp_path / "prescan.dng"
    dng.write(out, sample(shape=(9, 11, 4), dtype=np.uint8))
    assert tags(out)[BITS_PER_SAMPLE] == [8, 8, 8, 8]


# -- the tags a consumer matches on -----------------------------------------


def test_the_two_tags_negpy_finds_infrared_by(tmp_path):
    """`_peek_linearraw_4ch` matches on exactly these two and nothing else. Get
    either wrong and the file opens as a picture with the infrared gone."""
    out = tmp_path / "frame.dng"
    dng.write(out, sample())
    found = tags(out)
    assert found[SAMPLES_PER_PIXEL] == [4]
    assert found[PHOTOMETRIC] == [34892], "LinearRaw, not RGB"


def test_three_extra_samples_not_one(tmp_path):
    """A LinearRaw page implies one colour sample, so every plane past the
    first is 'extra' -- where an RGB TIFF of the same array declares one. This
    is what tifffile writes, and therefore what the reader has seen."""
    out = tmp_path / "frame.dng"
    dng.write(out, sample())
    assert tags(out)[EXTRA_SAMPLES] == [0, 0, 0]


def test_it_says_it_is_a_dng(tmp_path):
    out = tmp_path / "frame.dng"
    dng.write(out, sample())
    found = tags(out)
    assert found[DNG_VERSION] == [1, 4, 0, 0]
    # LibRaw rejects a DNG whose first page does not say it is the full image.
    assert found[NEW_SUBFILE_TYPE] == [0]
    assert found[COMPRESSION] == [1], "uncompressed: DNG blesses deflate for floats only"


def test_the_orientation_tag_does_not_repeat_the_turn(tmp_path):
    """The array arrives already the way up it was asked to be. A tag saying
    otherwise would have a reader turn it a second time."""
    out = tmp_path / "frame.dng"
    dng.write(out, sample())
    assert tags(out)[ORIENTATION] == [1]


def test_the_resolution_is_recorded(tmp_path):
    out = tmp_path / "frame.dng"
    dng.write(out, sample(), resolution=3600)
    assert tags(out)[X_RESOLUTION] == [3600, 1]


# -- what it refuses --------------------------------------------------------


def test_three_channels_are_refused(tmp_path):
    """Not an oversight. A three-sample LinearRaw DNG is handed to libraw by
    the very reader this exists for, which is a different and much less certain
    path -- so there is no reason to write one and every reason not to."""
    with pytest.raises(ValueError, match="4 samples"):
        dng.write(tmp_path / "rgb.dng", sample(shape=(8, 8, 3)))


def test_a_flat_plane_on_its_own_is_refused(tmp_path):
    with pytest.raises(ValueError, match="4 samples"):
        dng.write(tmp_path / "ir.dng", sample(shape=(8, 8)))


def test_a_dtype_that_is_neither_is_refused(tmp_path):
    with pytest.raises(ValueError, match="uint8 or uint16"):
        dng.write(tmp_path / "f.dng", sample().astype(np.float32))


def test_an_empty_image_is_refused(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        dng.write(tmp_path / "none.dng", np.zeros((0, 4, 4), dtype=np.uint16))
