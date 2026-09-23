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
    """The scanner tags every line, and the tag alone says which plane a line
    belongs to. The order they land in says something else -- which way the
    carriage read the pass (see below) -- so the planes here are flat, where
    that cannot show."""
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


# --- which way the pass was read ----------------------------------------------
#
# A pass read bottom-up arrives with its lines in reverse order: B first and R
# last, where a top-down pass starts with R and ends with B. `decode_index`
# reads that from the tags and turns the rows back, so every path from bytes to
# pixels delivers the picture upright. `rps7200/direction.py` has the evidence.

from pathlib import Path  # noqa: E402

from rps7200.direction import (  # noqa: E402
    FORWARD, REVERSED, UNKNOWN, encode_index, read_direction, reverse_lines,
)


def picture(rows=6, width=WIDTH, channels=3, seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 65535, (rows, width, channels), dtype=np.uint16)


@pytest.mark.parametrize("channels", [3, 4])
def test_a_pass_read_bottom_up_is_turned_upright(channels):
    image = picture(channels=channels)
    p = params(lines=image.shape[0])
    down, read_down = DirectScanner.decode_index(encode_index(image), p, channels)
    up, read_up = DirectScanner.decode_index(
        encode_index(image, reversed=True), p, channels)
    assert np.array_equal(down, image) and np.array_equal(up, image)
    assert (read_down.state, read_down.lead, read_down.trail) == (FORWARD, "R", "B"
                                                                 if channels == 3 else "I")
    assert (read_up.state, read_up.lead, read_up.trail) == (REVERSED,
                                                            "B" if channels == 3 else "I",
                                                            "R")
    assert read_up.as_record()["turned"] is True
    assert read_down.as_record()["turned"] is False


def test_only_the_rows_are_turned_never_the_columns():
    """Columns run along the strip, where the transport moves: a column that
    moved would put every edge and every hold in the wrong place."""
    image = picture(rows=5)
    turned = DirectScanner._deinterleave(encode_index(image, reversed=True),
                                         params(lines=5), 3)
    assert np.array_equal(turned[0], image[0]), "rows back in place"
    assert np.array_equal(turned[:, 0], image[:, 0]), "and no column moved"


def test_a_short_bottom_up_read_is_cut_to_aligned_planes_before_it_is_turned():
    """A read that stops short loses its last lines -- the top of the picture
    on a pass read bottom-up. The planes are aligned by index from the start
    of the read first, then turned; the other way round would misalign them by
    however many lines each plane lost."""
    image = picture(rows=8)
    stride = WIDTH * 2 + INDEX_HEADER
    blob = encode_index(image, reversed=True)
    short = blob[: stride * (3 * 8 - 4)]            # row 0 and row 1's R missing
    got, read = DirectScanner.decode_index(short, params(lines=8), 3)
    assert read.state == REVERSED and read.trail is None
    assert np.array_equal(got, image[2:])


def test_evidence_that_contradicts_itself_is_unknown_and_nothing_is_turned():
    image = picture(rows=4)
    stride = WIDTH * 2 + INDEX_HEADER
    lines = [encode_index(image)[k * stride:(k + 1) * stride] for k in range(12)]
    # the last row arrives B, G, R -- a tail that says bottom-up under a head
    # that says top-down
    lines[9:12] = lines[9:12][::-1]
    blob = b"".join(lines)
    read = read_direction(blob, stride, lines=4, channels=3)
    assert read.state == UNKNOWN and "last" in read.why
    got, _ = DirectScanner.decode_index(blob, params(lines=4), 3)
    assert np.array_equal(got[:3], image[:3]), "left as it came"


def test_a_single_plane_cannot_say_which_way_it_was_read():
    blob = stream([np.zeros((4, WIDTH), np.uint16)], order=["G"])
    assert read_direction(blob, WIDTH * 2 + INDEX_HEADER).state == UNKNOWN
    assert read_direction(b"", 18).state == UNKNOWN


def test_reversing_the_lines_is_exactly_a_read_the_other_way():
    """What the demo does to a stored pass, and what the carriage does."""
    image = picture(rows=5, channels=4)
    stride = WIDTH * 2 + INDEX_HEADER
    assert reverse_lines(encode_index(image), stride) == encode_index(image,
                                                                      reversed=True)


LIBRARY = Path(__file__).resolve().parent.parent / "library"
#: Found by their tags and confirmed by picture: three passes of the byte-14
#: ladder, and one real 1800 dpi pass filed on 2026-08-28.
BOTTOM_UP = ("20260911T091346Z_unknown-film_600dpi-2",
             "20260911T091346Z_unknown-film_600dpi-4",
             "20260911T091347Z_unknown-film_600dpi",
             "20260828T012327Z_unknown-film_1800dpi_ir")
TOP_DOWN = ("20260911T091346Z_unknown-film_600dpi",
            "20260911T091346Z_unknown-film_600dpi-3",
            "20260828T011439Z_unknown-film_1800dpi_ir")


def _stored(name):
    import json

    from rps7200 import library

    path = LIBRARY / name
    raw = library.read_raw(path) if path.exists() else None
    if raw is None:
        pytest.skip(f"{name} is not in this library")
    layout = json.loads((path / "scan.json").read_text())["raw"]["layout"]
    p = ScanParameters(width=layout["width"], lines=layout["lines"],
                       bytes_per_line=layout["bytes_per_line"], filter_offset1=0,
                       filter_offset2=0, available_lines=0)
    return DirectScanner.decode_index(raw, p, layout["channels"])


@pytest.mark.parametrize("name", BOTTOM_UP)
def test_the_passes_known_to_be_bottom_up_read_that_way(name):
    image, read = _stored(name)
    assert read.state == REVERSED and (read.lead, read.trail) == ("B", "R")
    # and turned, the colour planes still agree with each other row for row
    g = image[..., 1].astype(np.float64)
    for c in (0, 2):
        other = image[..., c].astype(np.float64)
        same = np.corrcoef(g[1:-1].ravel(), other[1:-1].ravel())[0, 1]
        shifted = np.corrcoef(g[2:].ravel(), other[:-2].ravel())[0, 1]
        assert same > shifted, "a plane slipped a row against green"


@pytest.mark.parametrize("name", TOP_DOWN)
def test_their_neighbours_read_top_down(name):
    _image, read = _stored(name)
    assert read.state == FORWARD and (read.lead, read.trail) == ("R", "B")


def test_a_pass_read_off_the_wire_bottom_up_arrives_upright_and_says_so(monkeypatch):
    """`read_planes` is the live path: what `scan()` records as the pass's
    `read_direction` is what this leaves behind."""
    image = picture(rows=6)
    blob = encode_index(image, reversed=True)
    stride = WIDTH * 2 + INDEX_HEADER
    s = DirectScanner.__new__(DirectScanner)
    s.verbose = False
    s.progress_hook = None
    s._log = lambda *a, **k: None
    lines = iter(blob[k * stride:(k + 1) * stride] for k in range(len(blob) // stride))
    monkeypatch.setattr(s, "read_lines",
                        lambda n, bpl, retries=1: b"".join(next(lines) for _ in range(n)),
                        raising=False)
    got = s.read_planes(params(lines=6), 3)
    assert np.array_equal(got, image)
    assert s.last_read_direction.state == REVERSED


def test_the_state_before_a_pass_is_kept_and_marked_stale_by_a_calibration():
    from conftest import FakeTransport

    far = bytearray(13)
    far[6], far[11] = 0x8D, 0x08

    class Far(FakeTransport):
        def command(self, command, data=None, **kw):
            if command[0] == 0xDD:
                self.sent.append((command[0], b""))
                return bytes(far)
            return super().command(command, data, **kw)

    s = DirectScanner(transport=Far())
    s.read_state()
    record = s.carriage_record()
    assert record == {"read_state": bytes(far).hex(), "far_end": True, "stale": False}
    s._calibrated_since_state = True           # what calibrate_shading leaves
    assert s.carriage_record()["stale"] is True
    s.read_state()
    assert s.carriage_record()["stale"] is False
