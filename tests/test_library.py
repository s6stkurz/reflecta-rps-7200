"""The scan library: does an entry survive a round trip, and stay reproducible.

The point of keeping the scanner's own bytes is that the decode can change and
every scan already taken can be re-run against the new one. These tests hold
that property: an entry must reconstruct to exactly the pixels it was saved
with, and must say so plainly when it no longer does.
"""

import gzip
import json

import numpy as np
import pytest

from rps7200 import library
from rps7200.direct import CHANNEL_ORDER, INDEX_HEADER
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference


def index_stream(width, lines, channels, seed=0):
    """A pass in INDEX format: one plane per line, each behind a 2-byte tag."""
    rng = np.random.default_rng(seed)
    planes = {
        CHANNEL_ORDER[c]: rng.integers(0, 65535, (lines, width), dtype=np.uint16)
        for c in range(channels)
    }
    out = bytearray()
    for y in range(lines):
        for c in range(channels):
            tag = CHANNEL_ORDER[c].encode()
            out += tag * INDEX_HEADER + planes[tag.decode()][y].tobytes()
    image = np.stack([planes[CHANNEL_ORDER[c]] for c in range(channels)], axis=-1)
    return bytes(out), image


def make_entry(tmp_path, width=16, lines=8, channels=3, **kw):
    raw, image = index_stream(width, lines, channels)
    layout = {
        "format": "index",
        "bytes_per_line": width * 2,
        "line_stride": width * 2 + INDEX_HEADER,
        "index_header": INDEX_HEADER,
        "width": width,
        "lines": lines,
        "channels": channels,
        "byte_order": "little",
    }
    meta = {
        "resolution_dpi": 1800,
        "channels": channels,
        "channel_order": list(CHANNEL_ORDER[:channels]),
        "width": width,
        "height": lines,
        "depth": 16,
        "frame": [0, 0, 10343, 6887],
        "bytes_per_line": width * 2,
        "exposure": [9604, 6506, 6506, 7745],
        "gain": [39, 33, 21, 21],
        "offset": [13, 11, 29, 11],
        "film": "negative",
        "shading": None,
    }
    reference = ShadingReference(
        ref={c: np.full(width, 30000.0) for c in range(channels)},
        mean={c: 30000.0 for c in range(channels)},
        pixels_per_line=width,
    )
    path = library.save(
        image, meta, root=tmp_path,
        film=FilmNotes(stock="Kodak Gold 200", frame="3", notes="the stripe frame"),
        tags=["colour-negative", "stripe-test"],
        reference=reference, ccd_mask=bytes(width), raw=raw, raw_layout=layout,
        **kw,
    )
    return path, image, raw


def test_an_entry_keeps_everything_needed_to_use_it_again(tmp_path):
    path, _, _ = make_entry(tmp_path)
    for name in ("scan.tif", "scan.json", "shading.npz", "ccd_mask.bin", "raw.bin.gz"):
        assert (path / name).exists(), f"{name} was not written"

    record = json.loads((path / "scan.json").read_text())
    assert record["film"]["stock"] == "Kodak Gold 200"
    assert "stripe-test" in record["tags"]
    assert record["scan"]["resolution_dpi"] == 1800
    assert record["device_settings"]["exposure"][0] == 9604
    # which build produced it, so a decode change can be attributed
    assert "driver_commit" in record["provenance"]
    assert "numpy" in record["provenance"]["versions"]


def test_the_raw_bytes_are_stored_byte_for_byte(tmp_path):
    path, _, raw = make_entry(tmp_path)
    with gzip.open(path / "raw.bin.gz", "rb") as fh:
        assert fh.read() == raw
    assert library.read_raw(path) == raw


def test_an_entry_reconstructs_to_the_pixels_it_was_saved_with(tmp_path):
    """The property the whole design exists for."""
    path, image, _ = make_entry(tmp_path)
    rebuilt, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(rebuilt, image)


def test_a_changed_decode_is_reported_not_hidden(tmp_path):
    path, image, _ = make_entry(tmp_path)
    from rps7200 import tiff
    damaged = image.copy()
    damaged[0, 0, 0] ^= 0xFFFF          # stand in for a decode that moved
    tiff.write(str(path / "scan.tif"), damaged, resolution=1800)

    _, verdict = library.reconstruct(path)
    assert "CHANGED" in verdict and "1 of" in verdict


def test_load_returns_the_calibration_alongside_the_pixels(tmp_path):
    path, image, _ = make_entry(tmp_path)
    loaded, record = library.load(path)
    assert np.array_equal(loaded, image)
    assert record["reference"] is not None
    assert record["reference"].pixels_per_line == 16
    assert record["ccd_mask"] is not None


def test_verify_is_quiet_on_a_good_entry_and_loud_on_a_broken_one(tmp_path):
    path, _, _ = make_entry(tmp_path)
    assert library.verify(tmp_path) == []

    (path / "raw.bin.gz").write_bytes(b"not the bytes that arrived")
    problems = library.verify(tmp_path)
    assert any("do not match their checksum" in p for p in problems)


def test_an_entry_without_calibration_is_flagged(tmp_path):
    raw, image = index_stream(8, 4, 3)
    library.save(image, {"resolution_dpi": 300, "channels": 3}, root=tmp_path)
    problems = library.verify(tmp_path)
    assert any("can never be corrected" in p for p in problems)
    assert any("cannot be re-decoded" in p for p in problems)


def test_the_index_summarises_every_entry(tmp_path):
    make_entry(tmp_path)
    make_entry(tmp_path, width=8, lines=4)
    summary = json.loads((tmp_path / "index.json").read_text())
    assert len(summary) == 2
    assert {e["film"] for e in summary} == {"Kodak Gold 200"}
    assert all(e["dpi"] == 1800 for e in summary)
