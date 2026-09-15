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


def test_infrared_leaves_in_a_dng_beside_the_jpeg(tmp_path):
    """Three channels is all a JPEG has, so the fourth goes into a file of its
    own rather than being dropped. A DNG and not a `_ir.tif` sidecar because
    NegPy's JPEG loader reports no infrared whatever sits next to the file,
    while its raw loader reads a four-sample LinearRaw page -- so the DNG is
    the only container that actually reaches the consumer."""
    from PIL import Image

    image = picture(channels=4)
    out = tmp_path / "frame.jpg"
    note = export.write(out, image)

    assert np.asarray(Image.open(out)).shape[2] == 3, "the picture is the picture"
    companion = tmp_path / "frame.dng"
    assert companion.exists(), "the plane has to go somewhere"
    assert companion.name in note, "and the operator has to be told where"

    kept = tiff.read(str(companion))
    assert kept.shape == image.shape
    assert np.array_equal(kept, image), (
        "the DNG is the full-depth copy -- if this were lossy the JPEG "
        "delivery would be worse than a TIFF in both files, not one"
    )


def test_the_dng_takes_the_jpegs_own_name(tmp_path):
    """Same stem, so the two sort together and read as one scan. The `_ir` tag
    `_out_name` adds stays on both: it says what was scanned, and both files
    came off that pass."""
    assert export.infrared_path("a/b/2026-09-14_frame01_1800dpi_ir.jpg").name == (
        "2026-09-14_frame01_1800dpi_ir.dng"
    )
    assert export.infrared_path("frame.jpeg").name == "frame.dng"


def test_a_three_channel_jpeg_leaves_one_file(tmp_path):
    """No plane, no companion. A DNG holding nothing a JPEG lacks would be a
    second full-size copy of the same picture."""
    assert export.write(tmp_path / "frame.jpg", picture(channels=3)) == ""
    assert not (tmp_path / "frame.dng").exists()


def test_a_dng_that_cannot_be_written_costs_the_plane_and_not_the_scan(
    tmp_path, monkeypatch
):
    """The picture is on disk by this point and the library entry is still to
    come, so a full disk must not turn a written scan into a failed one. The
    note is how it stays visible instead of quiet."""
    from PIL import Image

    def refuse(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(export.dng, "write", refuse)
    out = tmp_path / "frame.jpg"
    note = export.write(out, picture(channels=4))

    assert np.asarray(Image.open(out)).shape[2] == 3, "the JPEG survived"
    assert "no space left on device" in note
    assert "library" in note, "and says where the plane can still be found"


def test_a_monochrome_scan_stays_one_channel(tmp_path):
    """`to_monochrome` reduces on the way out so a consumer can tell black and
    white from a slide by the shape. That has to survive the encoder."""
    from PIL import Image

    out = tmp_path / "bw.jpg"
    assert export.write(out, picture(channels=1)) == ""
    assert np.asarray(Image.open(out)).ndim == 2


def test_a_four_channel_tiff_keeps_its_infrared_in_band(tmp_path):
    """The companion above belongs to JPEG alone: a TIFF carries the plane as a
    fourth sample, which NegPy reads off the file itself."""
    image = picture(channels=4)
    out = tmp_path / "frame.tif"
    assert export.write(out, image) == ""
    assert tiff.read(str(out)).shape[2] == 4
    assert not (tmp_path / "frame.dng").exists(), "nothing to rescue from a TIFF"


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
    image = picture(channels=4)
    note = export.write(tmp_path / "frame.jpg", image)
    assert "Pillow" in note
    assert not (tmp_path / "frame.jpg").exists()
    assert np.array_equal(tiff.read(str(tmp_path / "frame.tif")), image)
    assert not (tmp_path / "frame.dng").exists(), (
        "the TIFF it fell back to carries the plane itself; a DNG as well "
        "would be a second copy of a file nobody asked for"
    )
