"""Tests for the parts that do not need the scanner attached.

The channel-count derivation is the load-bearing piece: the backend reports RGBI
as ``SANE_FRAME_RGB``, so a frontend that trusts ``format`` reads four channels
of data as three and shears the image. Everything here checks that the stride
maths recovers the real layout.
"""

import numpy as np
import pytest

from rps7200 import tiff
from rps7200.device import Frame, Scanner
from rps7200.sane_ffi import Frame as FrameFormat
from rps7200.sane_ffi import SANE_Parameters


def params(pixels, lines, depth, channels):
    return SANE_Parameters(
        format=FrameFormat.RGB,  # what pieusb reports, even for RGBI
        last_frame=1,
        bytes_per_line=pixels * channels * (depth // 8),
        pixels_per_line=pixels,
        lines=lines,
        depth=depth,
    )


class TestChannelDerivation:
    def test_rgbi_16bit_is_four_channels(self):
        assert Scanner._channels(params(100, 50, 16, 4)) == 4

    def test_rgb_16bit_is_three_channels(self):
        assert Scanner._channels(params(100, 50, 16, 3)) == 3

    def test_rgbi_8bit_is_four_channels(self):
        assert Scanner._channels(params(100, 50, 8, 4)) == 4

    def test_gray_is_one_channel(self):
        assert Scanner._channels(params(100, 50, 16, 1)) == 1

    def test_format_field_is_ignored(self):
        """A 4-channel frame still reports SANE_FRAME_RGB; stride must win."""
        p = params(640, 480, 16, 4)
        assert p.format == FrameFormat.RGB
        assert Scanner._channels(p) == 4


class TestStreamDecoding:
    """The byte order sane_read produces, per buffer_update_read_index:
    byte-in-sample -> colour -> pixel -> line, 16-bit little-endian.
    """

    def build_stream(self, lines, pixels):
        """R,G,B,I interleaved with values encoding their own position."""
        expected = np.zeros((lines, pixels, 4), dtype="<u2")
        for y in range(lines):
            for x in range(pixels):
                for c in range(4):
                    expected[y, x, c] = (y * pixels + x) * 4 + c
        return expected.tobytes(), expected

    def test_roundtrip(self):
        lines, pixels = 7, 11
        raw, expected = self.build_stream(lines, pixels)
        assert len(raw) == lines * pixels * 4 * 2

        decoded = np.frombuffer(raw, dtype="<u2").reshape(lines, pixels, 4)
        assert np.array_equal(decoded, expected)

    def test_channels_are_separable(self):
        lines, pixels = 4, 5
        raw, expected = self.build_stream(lines, pixels)
        decoded = np.frombuffer(raw, dtype="<u2").reshape(lines, pixels, 4)

        frame = Frame(
            data=decoded, resolution=600, depth=16, channels=4, preview=False
        )
        assert np.array_equal(frame.rgb, expected[..., :3])
        assert np.array_equal(frame.ir, expected[..., 3])
        assert frame.has_ir

    def test_little_endian(self):
        """A sample of 0x0102 must appear as 02 01 on the wire."""
        a = np.array([[[0x0102, 0, 0, 0]]], dtype="<u2")
        assert a.tobytes()[:2] == b"\x02\x01"

    def test_wrong_channel_count_shears(self):
        """Reading 4-channel data as 3 channels misaligns - the bug we avoid."""
        lines, pixels = 6, 8
        raw, expected = self.build_stream(lines, pixels)
        wrong = np.frombuffer(raw, dtype="<u2")[: lines * pixels * 3].reshape(
            lines, pixels, 3
        )
        assert not np.array_equal(wrong, expected[..., :3])


class TestFrame:
    def test_ir_raises_without_fourth_channel(self):
        frame = Frame(
            data=np.zeros((4, 4, 3), dtype=np.uint16),
            resolution=600,
            depth=16,
            channels=3,
            preview=False,
        )
        assert not frame.has_ir
        with pytest.raises(ValueError, match="no infrared"):
            _ = frame.ir

    def test_metadata_reports_channel_order(self):
        frame = Frame(
            data=np.zeros((3, 5, 4), dtype=np.uint16),
            resolution=600,
            depth=16,
            channels=4,
            preview=False,
        )
        meta = frame.metadata()
        assert meta["channel_order"] == ["R", "G", "B", "I"]
        assert (meta["width"], meta["height"]) == (5, 3)
        assert meta["resolution_dpi"] == 600


class TestTiff:
    @pytest.mark.parametrize(
        "shape,dtype",
        [
            ((17, 23, 4), np.uint16),
            ((17, 23, 3), np.uint16),
            ((17, 23, 1), np.uint16),
            ((9, 11, 4), np.uint8),
            ((300, 200, 4), np.uint16),  # crosses a strip boundary
        ],
    )
    def test_roundtrip(self, tmp_path, shape, dtype):
        rng = np.random.default_rng(0)
        high = 65535 if dtype == np.uint16 else 255
        image = rng.integers(0, high, size=shape, dtype=np.uint32).astype(dtype)
        path = str(tmp_path / "img.tif")
        tiff.write(path, image, resolution=600)
        assert np.array_equal(tiff.read(path), image)

    def test_16bit_values_survive(self, tmp_path):
        """Guards against a silent 8-bit truncation."""
        image = np.full((4, 4, 4), 60000, dtype=np.uint16)
        path = str(tmp_path / "img.tif")
        tiff.write(path, image, resolution=600)
        back = tiff.read(path)
        assert back.dtype == np.uint16
        assert back.max() == 60000

    def test_rejects_float(self, tmp_path):
        with pytest.raises(ValueError, match="uint8 or uint16"):
            tiff.write(str(tmp_path / "x.tif"), np.zeros((4, 4, 4), dtype=np.float32))
