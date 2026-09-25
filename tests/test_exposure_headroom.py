"""`tools/exposure_headroom.py`: the offline study behind the exposure target.

It simulates a longer exposure on an entry's raw pixels and reads the clipping
off the real correction. Two things made its numbers meaningless on some
entries: an 8-bit prescan was read as a 16-bit pass -- "landed at 0.4%", asked
for 200x, and the cast back to eight bits wrapped -- and the dark floor the
simulation holds fixed was taken from the wrong reference column.
"""

import numpy as np

from conftest import load_tool
from rps7200 import library
from rps7200.direct import CHANNEL_ORDER, INDEX_HEADER
from rps7200.library import FilmNotes
from rps7200.shading import MASK_USED, ShadingReference, build_width_to_loc

headroom = load_tool("exposure_headroom")


def index_pass(width, lines, dtype, level):
    """An INDEX-format pass: one tagged line per channel per row."""
    rng = np.random.default_rng(0)
    top = np.iinfo(dtype).max
    planes = [rng.integers(level // 2, level, (lines, width)).clip(0, top)
              .astype(dtype) for _ in range(3)]
    out = bytearray()
    for y in range(lines):
        for c in range(3):
            tag = CHANNEL_ORDER[c].encode()
            out += tag * INDEX_HEADER + planes[c][y].astype(
                dtype.newbyteorder("<")).tobytes()
    return bytes(out), np.stack(planes, axis=-1)


def filed(tmp_path, dtype, level):
    width, lines = 24, 12
    stream, image = index_pass(width, lines, np.dtype(dtype), level)
    size = np.dtype(dtype).itemsize
    layout = {"format": "index", "bytes_per_line": width * size,
              "width": width, "lines": lines, "channels": 3}
    reference = ShadingReference(
        ref={c: np.linspace(40000, 44000, width) for c in range(3)},
        mean={c: 42000.0 for c in range(3)},
        dark={c: np.full(width, 400.0) for c in range(3)},
        dark_mean={c: 400.0 for c in range(3)},
        pixels_per_line=width,
    )
    meta = {"resolution_dpi": 300, "channels": 3, "film": "negative",
            "channel_order": list(CHANNEL_ORDER[:3]), "width": width,
            "height": lines, "depth": 8 * size, "bytes_per_line": width * size}
    return library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=reference, raw=stream, raw_layout=layout)


def test_an_8_bit_prescan_is_not_studied_as_a_16_bit_pass(tmp_path):
    assert headroom.study(filed(tmp_path / "a", np.uint8, 200)) is None


def test_a_16_bit_pass_still_is(tmp_path):
    got = headroom.study(filed(tmp_path / "b", np.uint16, 30000))
    assert got is not None
    assert all(0 < v < 1 for v in got["achieved"])


def test_a_simulated_exposure_clips_at_the_images_own_rail():
    """Handed a 16-bit ceiling, an 8-bit pass wrapped on the cast back."""
    image = np.full((2, 3, 3), 200, np.uint8)
    reference = ShadingReference(ref={c: np.ones(3) for c in range(3)},
                                 mean={c: 1.0 for c in range(3)},
                                 pixels_per_line=3)
    out = headroom.scale_exposure(image, reference, [4.0, 4.0, 4.0])
    assert out.dtype == np.uint8
    assert (out == 255).all(), "a lifted 8-bit sample must clip, not wrap"


def test_the_dark_floor_is_looked_up_as_the_correction_looks_it_up():
    """Output column j is the j-th element the mask marks used, not element j."""
    ppl = 12
    reference = ShadingReference(
        ref={0: np.full(ppl, 40000.0)}, mean={0: 40000.0},
        dark={0: np.arange(ppl, dtype=float) * 10}, dark_mean={0: 55.0},
        pixels_per_line=ppl,
    )
    mask = bytes(MASK_USED if i % 2 else 0x70 for i in range(ppl))
    got = headroom.dark_floor(reference, 0, 6, mask)
    want = reference.dark[0][build_width_to_loc(mask, 6)]
    assert np.array_equal(got, want)
    assert not np.array_equal(got, reference.dark[0][:6]), (
        "the fixture must make the two lookups differ")
