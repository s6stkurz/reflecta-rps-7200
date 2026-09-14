"""The delivery path: what the operator's file is, in the format they asked for.

The library's own writer is `rps7200.tiff` and is tested in `tests/test_tiff.py`.
What matters here is the format choice and the reduction to eight bits -- the
two things that turn one scan into two different files on disk.
"""
from __future__ import annotations

import builtins

import numpy as np
import pytest

from rps7200 import export, tiff


def picture(height=12, width=16, channels=3, dtype=np.uint16):
    """A picture with structure in it, so a swap or a flip is visible."""
    rng = np.random.default_rng(7)
    top = 65535 if dtype == np.uint16 else 255
    ramp = np.linspace(0, top, width, dtype=np.float64)
    out = np.empty((height, width, channels), dtype=np.float64)
    for c in range(channels):
        # Each channel different, so reading them back in the wrong order fails.
        out[:, :, c] = np.roll(ramp, c * 4) * (0.5 + 0.25 * c)
    out += rng.normal(0, top / 500, out.shape)
    return np.clip(out, 0, top).astype(dtype)


# -- the reduction ----------------------------------------------------------


def test_eight_bits_is_a_shift_and_nothing_else():
    """Not a percentile stretch. The JPEG has to be the same picture as the
    TIFF beside it, and metering already targets 80% of full scale, so there is
    no headroom a stretch would be reclaiming -- only a different picture."""
    image = picture()
    assert np.array_equal(export.to_8bit(image), (image >> 8).astype(np.uint8))


def test_a_prescan_is_already_eight_bits_and_is_left_alone():
    image = picture(dtype=np.uint8)
    out = export.to_8bit(image)
    assert out.dtype == np.uint8
    assert np.array_equal(out, image)


def test_a_dtype_that_is_neither_is_refused():
    with pytest.raises(ValueError, match="uint8 or uint16"):
        export.to_8bit(picture().astype(np.float32))


# -- picking the format -----------------------------------------------------


def test_the_filename_picks_the_format():
    assert export.format_of("a/b/frame01.tif") == "tiff"
    assert export.format_of("frame01.JPG") == "jpeg"
    assert export.format_of("frame01.jpeg") == "jpeg"


def test_a_name_that_does_not_say_is_refused_rather_than_guessed():
    """A silent default would write a TIFF into a file called `.png` and leave
    the operator to find out from something else."""
    with pytest.raises(ValueError, match="cannot tell what format"):
        export.format_of("frame01.png")


def test_the_two_formats_have_the_suffixes_the_rest_of_the_code_expects():
    assert export.suffix_for("tiff") == ".tif"
    assert export.suffix_for("jpeg") == ".jpg"
    with pytest.raises(ValueError):
        export.suffix_for("png")


# -- writing ----------------------------------------------------------------


def test_a_tiff_goes_through_untouched(tmp_path):
    image = picture()
    out = tmp_path / "frame.tif"
    assert export.write(out, image) == ""
    assert np.array_equal(tiff.read(str(out)), image), "the TIFF path is lossless"


def test_a_jpeg_is_the_same_picture_at_eight_bits(tmp_path):
    """Close, not equal -- JPEG is lossy. The bound is what would catch a
    channel swap, an inversion or a stretch, all of which move pixels much
    further than the codec does."""
    from PIL import Image

    image = picture()
    out = tmp_path / "frame.jpg"
    assert export.write(out, image, quality=95) == ""
    back = np.asarray(Image.open(out))
    assert back.shape == image.shape
    assert back.dtype == np.uint8
    wanted = export.to_8bit(image).astype(np.int16)
    assert np.abs(back.astype(np.int16) - wanted).mean() < 2.0


def test_a_jpeg_is_not_inverted(tmp_path):
    """The window's rule is that inversion never reaches disk. A dark negative
    must still be dark in the JPEG."""
    from PIL import Image

    dark = np.full((8, 8, 3), 4000, dtype=np.uint16)
    out = tmp_path / "dark.jpg"
    export.write(out, dark)
    assert np.asarray(Image.open(out)).mean() < 40, "inverted somewhere"


def test_infrared_cannot_ride_along_and_the_caller_is_told(tmp_path):
    """The one real cost of choosing JPEG. Said out loud rather than dropped
    quietly: infrared is what dust removal runs on."""
    from PIL import Image

    out = tmp_path / "frame.jpg"
    note = export.write(out, picture(channels=4))
    assert "infrared" in note
    assert np.asarray(Image.open(out)).shape[2] == 3


def test_a_monochrome_scan_stays_one_channel(tmp_path):
    """`to_monochrome` reduces on the way out so a consumer can tell black and
    white from a slide by the shape. That has to survive the encoder."""
    from PIL import Image

    out = tmp_path / "bw.jpg"
    assert export.write(out, picture(channels=1)) == ""
    assert np.asarray(Image.open(out)).ndim == 2


def test_a_four_channel_tiff_keeps_its_infrared(tmp_path):
    """The cost above belongs to JPEG alone."""
    image = picture(channels=4)
    out = tmp_path / "frame.tif"
    assert export.write(out, image) == ""
    assert tiff.read(str(out)).shape[2] == 4


def test_without_pillow_the_scan_is_written_as_a_tiff_instead(tmp_path, monkeypatch):
    """A scan costs minutes of hardware. An optional package that is not
    installed is no reason to lose one, so the JPEG becomes a TIFF and the
    caller is handed something to show the operator."""
    real_import = builtins.__import__

    def no_pillow(name, *args, **kw):
        if name.startswith("PIL"):
            raise ImportError("no module named PIL")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    image = picture()
    note = export.write(tmp_path / "frame.jpg", image)
    assert "Pillow" in note
    assert not (tmp_path / "frame.jpg").exists()
    assert np.array_equal(tiff.read(str(tmp_path / "frame.tif")), image)
