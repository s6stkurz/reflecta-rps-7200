"""Tests for both TIFF implementations, and for their agreeing with each other.

``rps7200.tiff`` has two independent implementations -- ``tifffile`` when it is
installed, and a hand-written dependency-free path -- and which one runs is an
installation accident. So the interesting tests are not "does it round-trip"
but "does it round-trip *across* the two": a file written on a machine with
tifffile has to read back identically on one without it, and the other way
round.

Everything here forces the implementation explicitly. Nothing in the suite did
that before, so with tifffile installed -- as it is on the scanning machine --
the built-in half never ran at all; and uninstalling tifffile only ever tested
it against *itself*, which cannot catch a disagreement between the two by
construction. Every divergence found when they were first put side by side was
of exactly that kind: the built-in reader returned read-only arrays, kept a
channel axis tifffile drops, and handed back big-endian dtypes.
"""

import numpy as np
import pytest

from rps7200 import tiff

#: Same shapes the round-trip test has always used -- 4-channel RGBI, plain RGB,
#: single channel, 8-bit, and one large enough to cross the built-in writer's
#: 8 MiB strip boundary -- plus a bare 2D plane, which is what ``--split``
#: writes for the IR.
SHAPES = [
    ((17, 23, 4), np.uint16),
    ((17, 23, 3), np.uint16),
    ((17, 23, 1), np.uint16),
    ((17, 23), np.uint16),
    ((9, 11, 4), np.uint8),
    ((300, 200, 4), np.uint16),
]

IMPLEMENTATIONS = ["builtin", "tifffile"]


def sample(shape, dtype, seed=0):
    rng = np.random.default_rng(seed)
    high = 65535 if dtype == np.uint16 else 255
    return rng.integers(0, high, size=shape, dtype=np.uint32).astype(dtype)


def expected_shape(shape):
    """What :func:`tiff.read` returns for an image written from ``shape``.

    TIFF records a sample count, not an array shape, so ``(H, W, 1)`` and
    ``(H, W)`` are the same file and both come back 2D.
    """
    return shape[:2] if len(shape) == 3 and shape[2] == 1 else shape


@pytest.fixture
def using(monkeypatch):
    """Force one implementation, skipping if it is not installed."""
    real = tiff._has_tifffile()

    def choose(name):
        if name == "tifffile" and not real:
            pytest.skip("tifffile is not installed")
        monkeypatch.setattr(tiff, "_has_tifffile", lambda: name == "tifffile")
        # The whole point of this module is that the patch actually bites; a
        # silently ineffective one would make every test below run twice on the
        # same path and pass.
        assert tiff._has_tifffile() is (name == "tifffile")

    return choose


class TestCrossImplementation:
    """Write with one implementation, read with the other. The regression this
    guards is a file that only opens on the machine that wrote it."""

    @pytest.mark.parametrize("reader", IMPLEMENTATIONS)
    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    @pytest.mark.parametrize("shape,dtype", SHAPES)
    def test_roundtrip(self, tmp_path, using, writer, reader, shape, dtype):
        image = sample(shape, dtype)
        path = str(tmp_path / "img.tif")

        using(writer)
        tiff.write(path, image, resolution=600)

        using(reader)
        back = tiff.read(path)

        assert back.shape == expected_shape(shape)
        assert back.dtype == dtype
        assert np.array_equal(back, image.reshape(back.shape))

    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_byte_for_byte_pixels(self, tmp_path, using, writer):
        """The two readers must not merely agree with the source -- they must
        agree with each other, including dtype and shape."""
        image = sample((40, 60, 4), np.uint16)
        path = str(tmp_path / "img.tif")
        using(writer)
        tiff.write(path, image, resolution=1800)

        using("builtin")
        by_builtin = tiff.read(path)
        using("tifffile")
        by_tifffile = tiff.read(path)

        assert by_builtin.shape == by_tifffile.shape
        assert by_builtin.dtype == by_tifffile.dtype
        assert np.array_equal(by_builtin, by_tifffile)


class TestReadContract:
    """What :func:`tiff.read` promises regardless of which half ran."""

    @pytest.mark.parametrize("reader", IMPLEMENTATIONS)
    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_result_is_writeable(self, tmp_path, using, writer, reader):
        """np.frombuffer over immutable bytes yields a read-only array, so this
        used to fail only where tifffile was missing."""
        path = str(tmp_path / "img.tif")
        using(writer)
        tiff.write(path, sample((12, 14, 4), np.uint16), resolution=600)
        using(reader)
        back = tiff.read(path)
        assert back.flags.writeable
        back[0, 0, 0] = 1234  # must not raise
        assert back[0, 0, 0] == 1234

    @pytest.mark.parametrize("reader", IMPLEMENTATIONS)
    def test_dtype_is_native(self, tmp_path, using, reader):
        path = str(tmp_path / "img.tif")
        using("builtin")
        tiff.write(path, sample((8, 9, 3), np.uint16), resolution=600)
        using(reader)
        assert tiff.read(path).dtype == np.dtype(np.uint16)
        assert tiff.read(path).dtype.byteorder in ("=", "|", "<" if np.little_endian else ">")

    @pytest.mark.parametrize("reader", IMPLEMENTATIONS)
    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_single_channel_comes_back_two_dimensional(
        self, tmp_path, using, writer, reader
    ):
        """A lone IR plane written by ``--split`` reads back as a plane.

        The built-in reader used to keep the channel axis while tifffile
        dropped it, so the same file had two shapes.
        """
        plane = sample((17, 23), np.uint16)
        path = str(tmp_path / "ir.tif")
        using(writer)
        tiff.write(path, plane, resolution=600)
        using(reader)
        back = tiff.read(path)
        assert back.shape == (17, 23)
        assert np.array_equal(back, plane)

    @pytest.mark.parametrize("reader", IMPLEMENTATIONS)
    def test_16bit_values_survive(self, tmp_path, using, reader):
        """Guards against a silent 8-bit truncation."""
        image = np.full((4, 4, 4), 60000, dtype=np.uint16)
        path = str(tmp_path / "img.tif")
        using("builtin")
        tiff.write(path, image, resolution=600)
        using(reader)
        back = tiff.read(path)
        assert back.dtype == np.uint16
        assert back.max() == 60000


class TestWriteContract:
    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_rejects_float(self, tmp_path, using, writer):
        using(writer)
        with pytest.raises(ValueError, match="uint8 or uint16"):
            tiff.write(str(tmp_path / "x.tif"), np.zeros((4, 4, 4), dtype=np.float32))

    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_rejects_empty(self, tmp_path, using, writer):
        """tifffile only warns and then writes a file it cannot read back."""
        using(writer)
        with pytest.raises(ValueError, match="empty image"):
            tiff.write(str(tmp_path / "x.tif"), np.zeros((0, 4, 3), dtype=np.uint16))

    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_rejects_four_dimensional(self, tmp_path, using, writer):
        using(writer)
        with pytest.raises(ValueError, match="2D or 3D"):
            tiff.write(str(tmp_path / "x.tif"), np.zeros((2, 4, 4, 3), dtype=np.uint16))

    def test_builtin_writes_little_endian(self, tmp_path, using):
        """The header says II, so the samples have to match it. Testing the
        dtype for ">" missed a native uint16, which reports "=" and is
        big-endian on a big-endian host."""
        using("builtin")
        path = tmp_path / "img.tif"
        tiff.write(str(path), np.array([[[0x0102, 0, 0, 0]]], dtype=np.uint16))
        raw = path.read_bytes()
        assert raw[:2] == b"II"
        assert raw[8:10] == b"\x02\x01"

    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_resolution_is_recorded(self, tmp_path, using, writer):
        """An out-of-line RATIONAL in the built-in writer -- the one field whose
        payload does not fit the IFD's four inline bytes."""
        tifffile = pytest.importorskip("tifffile")
        using(writer)
        path = str(tmp_path / "img.tif")
        tiff.write(path, sample((8, 8, 4), np.uint16), resolution=1800)
        with tifffile.TiffFile(path) as handle:
            tags = handle.pages[0].tags
            assert tags["XResolution"].value == (1800, 1)
            assert tags["YResolution"].value == (1800, 1)
            assert tags["ResolutionUnit"].value == 2  # inch
            assert tags["Software"].value == "rps7200"

    @pytest.mark.parametrize("writer", IMPLEMENTATIONS)
    def test_ir_plane_is_extra_sample_not_alpha(self, tmp_path, using, writer):
        """ExtraSamples 0 means "unspecified": data, not something to composite
        the picture against."""
        tifffile = pytest.importorskip("tifffile")
        using(writer)
        path = str(tmp_path / "img.tif")
        tiff.write(path, sample((8, 8, 4), np.uint16), resolution=600)
        with tifffile.TiffFile(path) as handle:
            page = handle.pages[0]
            assert page.tags["SamplesPerPixel"].value == 4
            assert tuple(page.tags["ExtraSamples"].value) == (0,)


class TestBuiltinReaderRefusals:
    """Every unsupported feature has to name what it found. The alternative is
    a KeyError, or -- worse -- plausible nonsense."""

    def write_via_tifffile(self, path, **kwargs):
        tifffile = pytest.importorskip("tifffile")
        image = sample((64, 64, 4), np.uint16)
        tifffile.imwrite(
            str(path), image, photometric="rgb",
            extrasamples=["unspecified"], **kwargs,
        )
        return image

    def test_refuses_compression(self, tmp_path, using):
        path = tmp_path / "deflate.tif"
        self.write_via_tifffile(path, compression="zlib")
        using("builtin")
        with pytest.raises(ValueError, match="compression 8"):
            tiff.read(str(path))

    def test_refuses_tiles(self, tmp_path, using):
        path = tmp_path / "tiled.tif"
        self.write_via_tifffile(path, tile=(16, 16))
        using("builtin")
        with pytest.raises(ValueError, match="tiled"):
            tiff.read(str(path))

    def test_refuses_planar(self, tmp_path, using):
        tifffile = pytest.importorskip("tifffile")
        path = tmp_path / "planar.tif"
        # Planar-separate wants the samples first: (C, H, W), one plane each.
        tifffile.imwrite(
            str(path), sample((64, 64, 4), np.uint16).transpose(2, 0, 1),
            photometric="rgb", planarconfig="separate",
            extrasamples=["unspecified"],
        )
        using("builtin")
        with pytest.raises(ValueError, match="planar configuration 2"):
            tiff.read(str(path))

    def test_refuses_float_samples(self, tmp_path, using):
        """float16 is the same width as the uint16 expected, so without the
        SampleFormat check it would decode into silently wrong numbers."""
        tifffile = pytest.importorskip("tifffile")
        path = tmp_path / "half.tif"
        tifffile.imwrite(str(path), np.zeros((8, 8), np.float16), photometric="minisblack")
        using("builtin")
        with pytest.raises(ValueError, match="sample format"):
            tiff.read(str(path))

    def test_refuses_non_tiff(self, tmp_path, using):
        path = tmp_path / "not.tif"
        path.write_bytes(b"GIF89a" + b"\x00" * 64)
        using("builtin")
        with pytest.raises(ValueError, match="not a TIFF"):
            tiff.read(str(path))

    def test_reports_truncation(self, tmp_path, using):
        """The IFD is written after the pixels, so a cut-off file loses its
        directory first and used to fail with a bare struct.error."""
        using("builtin")
        path = tmp_path / "short.tif"
        tiff.write(str(path), sample((40, 60, 4), np.uint16), resolution=600)
        whole = path.read_bytes()
        path.write_bytes(whole[: len(whole) - 400])
        with pytest.raises(ValueError, match="truncated"):
            tiff.read(str(path))

    def test_reports_truncated_pixels(self, tmp_path, using):
        """Cut between the pixel data and the IFD: the directory survives and
        promises strips that are not all there."""
        using("builtin")
        path = tmp_path / "short.tif"
        image = sample((40, 60, 4), np.uint16)
        tiff.write(str(path), image, resolution=600)
        whole = bytearray(path.read_bytes())
        del whole[8 : 8 + 2048]  # excise part of the single strip
        path.write_bytes(bytes(whole))
        with pytest.raises(ValueError, match="truncated"):
            tiff.read(str(path))

    def test_reads_big_endian(self, tmp_path, using):
        """Nothing here writes MM, but a foreign file must still come back in
        the host's byte order rather than as a >u2 nobody downstream expects."""
        tifffile = pytest.importorskip("tifffile")
        path = tmp_path / "be.tif"
        image = sample((16, 20, 4), np.uint16)
        tifffile.imwrite(
            str(path), image, photometric="rgb",
            extrasamples=["unspecified"], byteorder=">",
        )
        assert path.read_bytes()[:2] == b"MM"
        using("builtin")
        back = tiff.read(str(path))
        assert back.dtype == np.dtype(np.uint16)
        assert np.array_equal(back, image)


class TestStoredScans:
    """Files that predate this work must read the same through both halves.

    ``scans/`` is gitignored, so these skip on a fresh clone; where the files
    are present they are the only check against real scanner output, including
    the multi-strip layouts older tifffile versions wrote.
    """

    PATHS = [
        "scans/negatives/ir_300dpi.tif",
        "scans/negatives/series_300dpi.tif",
        "scans/negatives/out_1_raw.tif",
    ]

    @pytest.mark.parametrize("name", PATHS)
    def test_both_implementations_agree(self, using, name):
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / name
        if not path.exists():
            pytest.skip(f"{name} is not in this checkout")

        using("tifffile")
        by_tifffile = tiff.read(str(path))
        using("builtin")
        by_builtin = tiff.read(str(path))

        assert by_builtin.shape == by_tifffile.shape
        assert by_builtin.dtype == by_tifffile.dtype
        assert np.array_equal(by_builtin, by_tifffile)
