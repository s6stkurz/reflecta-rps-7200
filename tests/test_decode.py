"""Turning the scanner's bytes into pixels.

The scanner sends one colour plane per line in INDEX format, each prefixed with
a two-byte header whose first byte is the ASCII channel letter. A frame is
therefore channels x height lines, not height lines of interleaved pixels, and
the planes arrive interleaved by channel rather than one plane after another.

This is the layer the library exists to protect: every entry keeps the raw bytes
so a change here can be re-run against every scan ever taken. These tests are
the fast version of that check.
"""

import numpy as np
import pytest

from rps7200.direct import DirectScanner
from rps7200.protocol import (
    CHANNEL_ORDER,
    INDEX_HEADER,
    MAX_BATCH_LINES,
    READ_BUDGET_BYTES,
    ScanParameters,
    ScanReadError,
    batch_for,
)

WIDTH = 8


def params(width=WIDTH, lines=4, depth=2):
    return ScanParameters(
        width=width,
        lines=lines,
        bytes_per_line=width * depth,
        filter_offset1=0,
        filter_offset2=0,
        available_lines=0,
    )


def stream(planes, width=WIDTH, order=None, depth=2):
    """Encode planes as the scanner does: interleaved by channel, tagged."""
    dtype = "<u2" if depth == 2 else np.uint8
    order = order or list(CHANNEL_ORDER[: len(planes)])
    height = len(planes[0])
    out = bytearray()
    for row in range(height):
        for tag, plane in zip(order, planes):
            out += tag.encode() * INDEX_HEADER
            out += np.asarray(plane[row], dtype=dtype).tobytes()
    return bytes(out)


# --- batch_for --------------------------------------------------------------


def test_the_batch_never_exceeds_what_the_vendor_sends():
    """216 lines is the vendor's ceiling and 64 does not work at all -- the
    device simply sends nothing."""
    for bpl in (1, 16, 430, 2522, 5042):
        assert 1 <= batch_for(bpl) <= MAX_BATCH_LINES


def test_the_batch_fills_the_byte_budget_when_lines_are_small():
    assert batch_for(430) == MAX_BATCH_LINES


def test_a_long_line_gets_fewer_lines_per_read():
    assert batch_for(5042) < batch_for(2522) <= MAX_BATCH_LINES
    assert batch_for(5042) * 5042 >= READ_BUDGET_BYTES - 5042


def test_the_batch_is_never_zero():
    """A batch of zero asks the scanner for nothing and never terminates."""
    for bpl in (0, -1, READ_BUDGET_BYTES * 10):
        assert batch_for(bpl) >= 1


# --- _deinterleave ----------------------------------------------------------


def test_the_planes_come_back_in_rgbi_order():
    r = np.full((4, WIDTH), 100, np.uint16)
    g = np.full((4, WIDTH), 200, np.uint16)
    b = np.full((4, WIDTH), 300, np.uint16)
    image = DirectScanner._deinterleave(stream([r, g, b]), params(), 3)
    assert image.shape == (4, WIDTH, 3)
    assert image[..., 0].max() == 100
    assert image[..., 1].max() == 200
    assert image[..., 2].max() == 300


def test_the_infrared_plane_is_the_fourth():
    planes = [np.full((4, WIDTH), v, np.uint16) for v in (10, 20, 30, 40)]
    image = DirectScanner._deinterleave(stream(planes), params(), 4)
    assert image.shape == (4, WIDTH, 4)
    assert image[..., 3].max() == 40


def test_the_tag_decides_the_channel_not_the_arrival_order():
    """The scanner tags every line; nothing may depend on the order they land."""
    planes = [np.full((4, WIDTH), v, np.uint16) for v in (100, 200, 300)]
    forward = DirectScanner._deinterleave(stream(planes), params(), 3)
    shuffled = DirectScanner._deinterleave(
        stream(planes[::-1], order=["B", "G", "R"]), params(), 3
    )
    assert np.array_equal(forward, shuffled)


def test_pixel_values_survive_the_round_trip():
    rng = np.random.default_rng(0)
    planes = [rng.integers(0, 65535, (4, WIDTH), dtype=np.uint16) for _ in range(3)]
    image = DirectScanner._deinterleave(stream(planes), params(), 3)
    for c, plane in enumerate(planes):
        assert np.array_equal(image[..., c], plane)


def test_an_eight_bit_pass_decodes_as_bytes():
    """The prescan is 8-bit; sizing from the wrong depth reads nonsense."""
    planes = [np.full((4, WIDTH), v, np.uint8) for v in (10, 20, 30)]
    image = DirectScanner._deinterleave(
        stream(planes, depth=1), params(depth=1), 3
    )
    assert image.dtype == np.uint8
    assert image.shape == (4, WIDTH, 3)


def test_a_short_final_batch_truncates_to_the_shortest_plane():
    """A scan that ends mid-row must not stack planes of different heights."""
    planes = [np.full((4, WIDTH), v, np.uint16) for v in (100, 200, 300)]
    blob = stream(planes)
    line = params().bytes_per_line + INDEX_HEADER
    image = DirectScanner._deinterleave(blob[: -2 * line], params(), 3)
    assert image.shape[0] == 3


def test_unrecognised_tags_are_refused_rather_than_guessed():
    blob = bytearray(stream([np.zeros((4, WIDTH), np.uint16)] * 3))
    for i in range(0, len(blob), params().bytes_per_line + INDEX_HEADER):
        blob[i] = ord("X")
    with pytest.raises(ScanReadError, match="no recognisable channel tags"):
        DirectScanner._deinterleave(bytes(blob), params(), 3)


def test_a_missing_channel_is_refused_not_silently_dropped():
    """Three planes where four were asked for is a broken pass, not a 3-channel
    scan: the caller would write an RGB file and call it RGBI."""
    planes = [np.full((4, WIDTH), v, np.uint16) for v in (10, 20, 30)]
    with pytest.raises(ScanReadError, match="expected 4 channels"):
        DirectScanner._deinterleave(stream(planes), params(), 4)


def test_an_empty_stream_is_refused():
    with pytest.raises(ScanReadError):
        DirectScanner._deinterleave(b"", params(), 3)


# --- the native 7200 dpi column stagger --------------------------------------
#
# Confirmed 2026-09-13 on two unrelated 7200 dpi library entries: even and odd
# columns correlate best at a shared row lag of 4, not 0 -- the signature of a
# staggered CCD, two rows of elements offset along the scan direction. Decoded
# without correction this draws a zigzag, column by column. See
# `_realign_native_column_stagger`'s docstring.


def test_realigning_undoes_a_known_column_stagger():
    lines = 3
    h, w = 20, 4
    true = np.arange(h, dtype=np.uint16)
    raw = np.zeros((h, w, 1), dtype=np.uint16)
    raw[:, 0::2, 0] = true[:, None]
    # Odd columns read the same content, but `lines` rows later in the stream.
    raw[lines:, 1::2, 0] = true[: h - lines, None]

    aligned = DirectScanner._realign_native_column_stagger(raw, lines=lines)

    assert aligned.shape == (h - lines, w, 1)
    assert np.array_equal(aligned[:, 0::2, 0], aligned[:, 1::2, 0])
    assert np.array_equal(aligned[:, 0, 0], true[: h - lines])


def test_realigning_by_zero_lines_is_a_no_op():
    raw = np.arange(24, dtype=np.uint16).reshape(4, 6, 1)
    aligned = DirectScanner._realign_native_column_stagger(raw, lines=0)
    assert np.array_equal(aligned, raw)


def test_realigning_a_frame_too_short_for_the_stagger_is_refused():
    raw = np.zeros((3, 4, 1), dtype=np.uint16)
    with pytest.raises(ValueError):
        DirectScanner._realign_native_column_stagger(raw, lines=4)


# --- predicting a pass's column count, before taking it ----------------------


def test_shading_columns_needed_matches_the_3600dpi_calibration():
    from rps7200.direct import CALIBRATION_FRAME
    assert DirectScanner._shading_columns_needed(CALIBRATION_FRAME, 3600) == 5172


def test_shading_columns_needed_at_7200dpi_is_exactly_double():
    from rps7200.direct import FULL_FRAME
    assert DirectScanner._shading_columns_needed(FULL_FRAME, 3600) == 5172
    assert DirectScanner._shading_columns_needed(FULL_FRAME, 7200) == 10344
