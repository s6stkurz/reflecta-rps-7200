"""The stand-in scanner, which the window is tried out against.

Two things matter here beyond "it returns an array". It must decode the
**raw bytes** a library entry stores rather than reading back the TIFF beside
them -- that is what makes trying the window out exercise the path that can
break -- and it must refuse what the hardware refuses, because a stand-in that
accepts a combination the device rejects teaches the window a shape that does
not exist.
"""


import numpy as np
import pytest

from rps7200 import library
from rps7200.demo import DemoScanner
from rps7200.library import FilmNotes


def entry(root, channels=3, lines=6, width=8):
    """A library entry with raw bytes, its TIFF, and nothing corrected."""
    tags = "RGBI"[:channels]
    rows, raw = [], bytearray()
    for _ in range(lines):
        for c in range(channels):
            row = np.arange(width, dtype="<u2") * (c + 1) * 100
            raw += tags[c].encode() * 2 + row.tobytes()
            rows.append(row)
    image = np.stack(
        [np.array(rows[c::channels]) for c in range(channels)], axis=-1
    ).astype(np.uint16)
    meta = {
        "resolution_dpi": 900, "channels": channels,
        "channel_order": list(tags), "width": width, "height": lines,
        "bytes_per_line": width * 2, "depth": 16,
    }
    return library.save(
        image, meta, root=root, film=FilmNotes(frame="demo"),
        raw=bytes(raw),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "line_stride": width * 2 + 2, "index_header": 2,
                    "width": width, "lines": lines, "channels": channels},
    ), image


def test_it_decodes_the_raw_bytes_not_the_tiff(tmp_path):
    """Proved by corrupting the TIFF: the pixels must still come back right."""
    path, truth = entry(tmp_path)
    tiff_path = path / "scan.tif"
    tiff_path.write_bytes(b"not a tiff at all")

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    s.close()
    assert np.array_equal(image, truth), (
        "the demo read the TIFF; it must decode raw.bin.gz"
    )


def test_what_it_files_can_be_reconstructed(tmp_path):
    """The bytes it hands back have to be the bytes behind its pixels."""
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False)
    capture = s.capture_record()
    s.close()

    assert capture["raw"] is not None
    out = library.save(image, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    _, verdict = library.reconstruct(out)
    assert verdict.startswith("identical"), verdict


def test_reshaping_the_image_drops_the_bytes(tmp_path):
    """Asking a four-channel entry for RGB leaves bytes that describe four.

    Filing those would produce an entry whose raw decodes to a different
    picture, which is the one failure the library exists to make impossible.
    """
    entry(tmp_path, channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    capture = s.capture_record()
    s.close()
    assert image.shape[2] == 3
    assert capture["raw"] is None, "bytes describing four channels were kept"
    # The calibration is still worth keeping; only the bytes went.
    assert "reference" in capture


@pytest.mark.parametrize("film", ["bw", "kodachrome"])
def test_it_refuses_infrared_where_the_device_would(tmp_path, film):
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    with pytest.raises(ValueError, match="infrared is blind"):
        s.scan(resolution=900, infrared=True, film=film)
    with pytest.raises(ValueError, match="infrared is blind"):
        list(s.scan_roll(frames=1, infrared=True, film=film))
    s.close()


def test_it_allows_infrared_on_film_that_can_use_it(tmp_path):
    entry(tmp_path, channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=True, film="negative")
    s.close()
    assert image.shape[2] == 4


def test_an_entry_with_no_bytes_falls_back_rather_than_failing(tmp_path):
    """A library full of older entries must still drive the window."""
    path, _ = entry(tmp_path)
    (path / "raw.bin.gz").unlink()
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    s.close()
    assert image.ndim == 3 and image.size > 0
