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


# --- duplicates -------------------------------------------------------------

def entry_with(tmp_path, *, stock="Kodak Gold 200", frame="3", dpi=1800,
               revision=1, raw=True, reference=True, channels=3,
               exposure_scale=None, metered=True):
    stream, image = index_stream(16, 8, channels)
    meta = {
        "resolution_dpi": dpi, "channels": channels,
        "channel_order": list(CHANNEL_ORDER[:channels]),
        "width": 16, "height": 8, "depth": 16, "frame": [0, 0, 10343, 6887],
        "bytes_per_line": 32, "film": "negative", "shading": None,
        "protocol_revision": revision,
    }
    if exposure_scale is not None:
        meta["exposure_scale"] = exposure_scale
        meta["exposure_metered"] = metered
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": channels}
    ref = ShadingReference(
        ref={c: np.full(16, 30000.0) for c in range(channels)},
        mean={c: 30000.0 for c in range(channels)}, pixels_per_line=16,
    ) if reference else None
    return library.save(
        image, meta, root=tmp_path, film=FilmNotes(stock=stock, frame=frame),
        reference=ref, raw=stream if raw else None,
        raw_layout=layout if raw else None,
    )


def test_the_same_scan_of_the_same_picture_is_a_duplicate(tmp_path):
    entry_with(tmp_path)
    entry_with(tmp_path)
    doomed = library.prunable(tmp_path)
    assert len(doomed) == 1
    assert "same scan of the same picture" in doomed[0][1]


def test_a_different_picture_or_setting_is_not(tmp_path):
    entry_with(tmp_path, frame="3")
    entry_with(tmp_path, frame="4")            # different frame
    entry_with(tmp_path, frame="3", dpi=3600)  # different resolution
    assert library.prunable(tmp_path) == []


def test_scans_across_a_protocol_change_are_both_kept(tmp_path):
    """Once the conversation with the scanner moves, they are different
    measurements of the same film, not copies of one."""
    entry_with(tmp_path, revision=1)
    entry_with(tmp_path, revision=2)
    assert library.prunable(tmp_path) == []


def test_a_bracket_is_not_a_pile_of_duplicates(tmp_path):
    """The failure this guards against would delete the bracket.

    A bracket is the same frame at the same dpi, depth and channel count. The
    only thing separating its members is the exposure each pass was *told* to
    use -- and signature() excludes exposure, deliberately, because a metered
    exposure is an outcome that lands differently every run. Without the
    commanded/metered distinction all three of these share one signature,
    `duplicates` calls them copies, and `--delete` keeps one and destroys the
    two that made it a bracket.
    """
    for scale in (0.5, 1.0, 2.0):
        entry_with(tmp_path, exposure_scale=scale, metered=False)
    assert library.prunable(tmp_path) == []


def test_two_metered_runs_of_one_scan_are_still_duplicates(tmp_path):
    """The other half: metering never lands on exactly the same number twice.

    If a landed exposure counted towards identity, no two runs of the same scan
    would ever be recognised as copies and the library would never reduce.
    """
    entry_with(tmp_path, exposure_scale=0.7851, metered=True)
    entry_with(tmp_path, exposure_scale=0.7863, metered=True)
    doomed = library.prunable(tmp_path)
    assert len(doomed) == 1
    assert "same scan of the same picture" in doomed[0][1]


def test_entries_written_before_the_field_existed_are_unchanged(tmp_path):
    """Legacy entries carry no exposure_metered. They were metered; treating
    them as such leaves their signatures exactly as they were."""
    entry_with(tmp_path)                      # no exposure keys at all
    entry_with(tmp_path)
    assert len(library.prunable(tmp_path)) == 1


def test_the_entry_that_can_still_be_used_is_the_one_kept(tmp_path):
    """Age does not decide it: raw bytes and calibration do."""
    keeper = entry_with(tmp_path, raw=True, reference=True)
    entry_with(tmp_path, raw=False, reference=False)   # newer, but a dead end
    doomed = library.prunable(tmp_path)
    assert len(doomed) == 1
    assert doomed[0][0]["id"] != keeper.name
    assert "no raw bytes either" in doomed[0][1]


def test_keep_two_retains_a_pair_for_comparison(tmp_path):
    for _ in range(3):
        entry_with(tmp_path)
    assert len(library.prunable(tmp_path, keep=1)) == 2
    assert len(library.prunable(tmp_path, keep=2)) == 1


def test_a_corrected_entry_reconstructs_without_crying_wolf(tmp_path):
    """scan.tif may hold shading-corrected pixels, so a raw decode cannot match
    it. Before this was handled, every corrected entry reported ~99% of samples
    differing -- which would have hidden a real decode regression completely."""
    from rps7200.shading import apply_shading

    stream, image = index_stream(16, 8, 3)
    reference = ShadingReference(
        ref={c: np.linspace(28000, 32000, 16) for c in range(3)},
        mean={c: 30000.0 for c in range(3)},
        pixels_per_line=16,
    )
    corrected, report = apply_shading(image, reference, None)
    assert not np.array_equal(corrected, image), "fixture must actually change pixels"

    meta = {
        "resolution_dpi": 1800, "channels": 3,
        "channel_order": list(CHANNEL_ORDER[:3]),
        "width": 16, "height": 8, "depth": 16, "bytes_per_line": 32,
        "shading": report,                      # marks the stored image corrected
    }
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(corrected, meta, root=tmp_path, film=FilmNotes(),
                        reference=reference, raw=stream, raw_layout=layout)

    rebuilt, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(rebuilt, corrected)


def test_a_corrected_entry_without_its_reference_says_so(tmp_path):
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 1800, "channels": 3, "width": 16, "height": 8,
            "bytes_per_line": 32, "shading": {"columns": 16, "width": 16}}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=None, raw=stream, raw_layout=layout)
    _, verdict = library.reconstruct(path)
    assert "reference is missing" in verdict, verdict
