"""The scan library: does an entry survive a round trip, and stay reproducible.

The point of keeping the scanner's own bytes is that the decode can change and
every scan already taken can be re-run against the new one. These tests hold
that property: an entry must reconstruct to exactly the pixels it was saved
with, and must say so plainly when it no longer does.
"""

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from rps7200 import library, tiff
from rps7200.direct import (CHANNEL_ORDER, INDEX_HEADER,
                            SHADING_SKIPPED_EXPLICIT)
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

    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["film"]["stock"] == "Kodak Gold 200"
    assert "stripe-test" in record["tags"]
    assert record["scan"]["resolution_dpi"] == 1800
    assert record["device_settings"]["exposure"][0] == 9604
    # which build produced it, so a decode change can be attributed
    assert "driver_commit" in record["provenance"]
    assert "numpy" in record["provenance"]["versions"]
    # and which code, when git cannot say: a dirty tree, or none at all
    source = record["provenance"]["driver_source_sha256_at_import"]
    assert isinstance(source, str) and len(source) == 64


def test_values_json_cannot_hold_are_kept_whole_not_printed(tmp_path):
    """`default=str` wrote an array over a thousand elements with "..." in
    the middle, bytes as "b'...'" and a numpy integer as a string."""
    profile = np.arange(2000, dtype=np.float64) / 7
    path, _, _ = make_entry(tmp_path)
    stream, image = index_stream(16, 8, 3)
    other = library.save(image, {"resolution_dpi": 300, "channels": 3,
                                 "profile": profile, "count": np.int64(7),
                                 "answer": b"\x00\xff"},
                         root=tmp_path / "b", raw=stream,
                         raw_layout={"bytes_per_line": 32, "width": 16,
                                     "lines": 8, "channels": 3})
    extra = json.loads((other / "scan.json").read_text(encoding="utf-8"))["extra"]
    assert extra["profile"] == profile.tolist()
    assert extra["count"] == 7
    assert extra["answer"] == "00ff"
    assert path.exists()


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


def test_a_scan_that_wanted_correction_and_missed_it_says_so(tmp_path):
    """Six 3600 dpi RGBI frames were filed uncorrectable in one sitting because
    a restarted session had no reference and nothing recorded that the
    correction had been *asked for*.

    An empty `corrections_applied` cannot say it: a scan deliberately taken raw
    looks exactly the same.
    """
    raw, image = index_stream(8, 4, 3)
    library.save(
        image,
        {"resolution_dpi": 3600, "channels": 3,
         "shading_skipped": "no shading reference in this session"},
        root=tmp_path,
    )
    problems = library.verify(tmp_path)
    assert any("correction was asked for" in p for p in problems), problems


def test_a_scan_deliberately_taken_raw_is_not_reported_as_a_shortfall(tmp_path):
    raw, image = index_stream(8, 4, 3)
    library.save(image, {"resolution_dpi": 300, "channels": 3}, root=tmp_path)
    problems = library.verify(tmp_path)
    assert any("can never be corrected" in p for p in problems)
    assert not any("correction was asked for" in p for p in problems)


def test_the_metering_telemetry_reaches_the_sidecar(tmp_path):
    """It did not, and that is why a blown B&W blue channel could not be
    diagnosed from the entry: auto_exposure recorded what the probe measured,
    scan() put it in meta, and library.save built the sidecar from fixed key
    lists that had no place for it. The evidence was assembled and dropped.
    """
    raw, image = index_stream(8, 4, 3)
    probe = {"target": 0.8, "film": "bw", "infrared": True,
             "blue_headroom": 11.0,
             "rounds": [{"round": 1, "levels": [0.2, 0.2, 0.3],
                         "targets": [0.8, 0.8, 0.073], "clipped": [0, 0, 0]}]}
    path = library.save(
        image,
        {"resolution_dpi": 600, "channels": 3, "metering": probe},
        root=tmp_path,
    )
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["metering"] == probe


def test_a_scan_given_its_exposure_records_no_metering(tmp_path):
    raw, image = index_stream(8, 4, 3)
    path = library.save(image, {"resolution_dpi": 600, "channels": 3},
                        root=tmp_path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["metering"] is None


def test_the_index_summarises_every_entry(tmp_path):
    make_entry(tmp_path)
    make_entry(tmp_path, width=8, lines=4)
    summary = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(summary) == 2
    assert {e["film"] for e in summary} == {"Kodak Gold 200"}
    assert all(e["dpi"] == 1800 for e in summary)


# --- duplicates -------------------------------------------------------------

def entry_with(tmp_path, *, stock="Kodak Gold 200", frame="3", dpi=1800,
               revision=1, raw=True, reference=True, channels=3,
               exposure_scale=None, metered=True, picture=0):
    stream, image = index_stream(16, 8, channels, seed=picture)
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


def test_different_photographs_asked_for_alike_are_never_duplicates(tmp_path):
    """The failure this guards against deleted photographs.

    Two frames scanned from the window with the film notes left empty -- or two
    strips filed under one day's default roll name -- are the same request of
    the scanner: same notes, dpi, depth, channels, frame window. The signature
    cannot tell them apart. Their bytes can, and `--delete` used to keep one
    and destroy the rest, raw bytes included.
    """
    entry_with(tmp_path, stock="", frame="", picture=1)
    entry_with(tmp_path, stock="", frame="", picture=2)
    entry_with(tmp_path, stock="", frame="", picture=3)
    assert len(library.duplicates(tmp_path)) == 1, "one request, three answers"
    assert library.prunable(tmp_path) == []


def test_only_the_identical_ones_of_a_group_are_redundant(tmp_path):
    twins = {entry_with(tmp_path, picture=1).name,
             entry_with(tmp_path, picture=1).name}   # the same bytes twice
    other = entry_with(tmp_path, picture=2)          # same request, another picture
    doomed = library.prunable(tmp_path)
    assert len(doomed) == 1
    assert doomed[0][0]["id"] in twins
    assert doomed[0][0]["id"] != other.name


def test_an_entry_with_no_checksum_is_never_proved_the_same():
    assert not library.same_data({"image": {}}, {"image": {}})
    assert library.same_data({"image": {"sha256": "a"}}, {"image": {"sha256": "a"}})
    # Raw bytes decide where both kept them, whatever the pixels say.
    a = {"raw": {"file": "raw.bin.gz", "sha256": "x"}, "image": {"sha256": "p"}}
    b = {"raw": {"file": "raw.bin.gz", "sha256": "y"}, "image": {"sha256": "p"}}
    assert not library.same_data(a, b)


def test_a_survivor_damaged_on_disk_never_costs_the_intact_copy(tmp_path):
    """The survivor was chosen from the records alone. With its raw bytes
    truncated since, `--delete` removed the only intact copy."""
    first = entry_with(tmp_path)
    second = entry_with(tmp_path)
    [(doomed, _)] = library.prunable(tmp_path)
    survivor = second if doomed["id"] == first.name else first
    data = (survivor / "raw.bin.gz").read_bytes()
    (survivor / "raw.bin.gz").write_bytes(data[: len(data) // 2])
    assert library.prunable(tmp_path) == []


def test_the_twin_that_carries_more_is_the_one_kept(tmp_path):
    """The newest won every tie, so a bare copy filed later -- the debug
    flush's, say -- survived the entry with the prescan and the tags."""
    rich = entry_with(tmp_path)
    record = json.loads((rich / "scan.json").read_text(encoding="utf-8"))
    record["tags"] = ["roll-7"]
    record["extra"] = {"roll_membership": {"roll": "r", "frame": 3}}
    record["created"] = "2026-09-01T10:00:00+00:00"           # the older one
    (rich / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    entry_with(tmp_path)                                   # newer, and bare
    [(doomed, _)] = library.prunable(tmp_path)
    assert doomed["id"] != rich.name


def test_verify_reports_a_missing_prescan(tmp_path):
    """Only the reference and the mask were checked for being there: a
    prescan.tif the record names and checksums could vanish unremarked."""
    path, _, _ = make_entry(tmp_path, prescan=np.zeros((4, 6, 3), np.uint8))
    assert library.verify(tmp_path) == []
    (path / "prescan.tif").unlink()
    assert any("prescan.tif is missing" in p for p in library.verify(tmp_path))


def test_verify_reports_a_partial_write_left_behind(tmp_path):
    path, _, _ = make_entry(tmp_path)
    (path / ".scan.tif.part").write_bytes(b"half a file")
    assert any(".scan.tif.part" in p for p in library.verify(tmp_path))


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
        "shading": report,
    }
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    # `corrections` is what marks the stored image corrected, and a caller has
    # to say so deliberately: entries hold raw pixels now, so `meta["shading"]`
    # means "this is the correction that goes with these pixels", not "it is
    # already in them". The two were conflated, and every prescan was filed
    # claiming to be raw when it was not.
    path = library.save(corrected, meta, root=tmp_path, film=FilmNotes(),
                        reference=reference, raw=stream, raw_layout=layout,
                        corrections=["shading"])

    rebuilt, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(rebuilt, corrected)


def test_a_corrected_entry_without_its_reference_says_so(tmp_path):
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 1800, "channels": 3, "width": 16, "height": 8,
            "bytes_per_line": 32, "shading": {"columns": 16, "width": 16}}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=None, raw=stream, raw_layout=layout,
                        corrections=["shading"])
    _, verdict = library.reconstruct(path)
    assert "reference is missing" in verdict, verdict
    assert verdict.kind == library.NOTHING


def test_a_corrected_entry_that_lost_its_named_reference_is_damaged(tmp_path):
    """Filed with no reference is nothing to reproduce from; filed with one
    that has since gone is damage. Both read "nothing", and `reconstruct`
    exited 0 on an entry `verify` called broken."""
    from rps7200.shading import apply_shading

    stream, image = index_stream(16, 8, 3)
    reference = ShadingReference(
        ref={c: np.linspace(28000, 32000, 16) for c in range(3)},
        mean={c: 30000.0 for c in range(3)},
        pixels_per_line=16,
    )
    corrected, report = apply_shading(image, reference, None)
    meta = {"resolution_dpi": 1800, "channels": 3, "width": 16, "height": 8,
            "depth": 16, "bytes_per_line": 32, "shading": report}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(corrected, meta, root=tmp_path, film=FilmNotes(),
                        reference=reference, raw=stream, raw_layout=layout,
                        corrections=["shading"])
    (path / "shading.npz").unlink()
    _, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict
    assert "shading.npz is missing" in verdict


def test_a_raw_file_the_record_names_and_has_lost_is_damaged(tmp_path):
    path, _, _ = make_entry(tmp_path)
    (path / library.RAW_FILE).unlink()
    _, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict
    assert "no raw bytes stored" not in verdict


def test_raw_can_be_streamed_from_a_file(tmp_path):
    """A 7200 dpi frame is 570 MB. Handing it over as bytes would put it back on
    the heap, which is exactly what spooling it to disk was meant to avoid."""
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 1800, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]), "width": 16, "height": 8,
            "depth": 16, "frame": [0, 0, 10343, 6887], "bytes_per_line": 32,
            "film": "negative", "shading": None, "protocol_revision": 1}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}

    spool = tmp_path / "spooled.bin"
    spool.write_bytes(stream)

    from_bytes = library.save(image, meta, root=tmp_path / "a", raw=stream,
                              raw_layout=layout)
    from_file = library.save(image, meta, root=tmp_path / "b", raw_path=spool,
                             raw_layout=layout)

    a = json.loads((from_bytes / "scan.json").read_text(encoding="utf-8"))["raw"]
    b = json.loads((from_file / "scan.json").read_text(encoding="utf-8"))["raw"]
    assert a["sha256"] == b["sha256"], "streaming changed the bytes"
    assert a["bytes"] == b["bytes"] == len(stream)
    assert library.read_raw(from_file) == stream


def test_the_scan_block_carries_everything_scan_records(tmp_path):
    """The sidecar is built from a fixed list of keys, and twice now something
    `scan()` deliberately recorded has been dropped by not being on it.

    `metering` was, which is why a blown blue channel could not be diagnosed
    from the entry. `filter_offsets` was, which is the field the pass-to-pass
    column offset would be investigated with. Both were noticed by accident.

    The meta here is typed by hand, so this pins the two fields that were
    dropped and cannot notice a third. That `scan()`'s own meta reaches the
    record whole is `test_real_pass.py`'s, which runs the pass.
    """
    raw, image = index_stream(8, 4, 3)
    meta = {
        "resolution_dpi": 900, "channels": 3, "channel_order": ["R", "G", "B"],
        "width": 8, "height": 4, "depth": 16, "bytes_per_line": 16,
        "film": "negative", "frame": [0, 0, 10343, 6887],
        "exposure_scale": 1.0, "exposure_metered": False, "duration_s": 1.0,
        "protocol_revision": 1, "rotation": 0,
        "filter_offsets": [12, 12],
        "metering": {"target": 0.8, "rounds": []},
    }
    path = library.save(image, meta, root=tmp_path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))

    assert record["scan"]["filter_offsets"] == [12, 12]
    assert record["metering"] == meta["metering"]

    # Nothing scan() records about the *scan* should be silently absent.
    scan_facts = {
        k: v for k, v in meta.items()
        if k not in ("metering", "shading", "shading_skipped",
                     "exposure", "gain", "offset")
    }
    missing = [k for k in scan_facts if k not in record["scan"]]
    assert not missing, f"the sidecar drops {missing} on the floor"


def test_an_entry_stores_raw_pixels_and_recomputes_the_correction(tmp_path):
    """The bargain this library rests on: keep what the scanner sent, keep the
    reference beside it, and compute the usable image on the way out.

    A corrected file cannot be un-corrected, so storing one forecloses every
    later improvement to the correction on every scan ever taken. Storing raw
    costs nothing -- `corrected()` reproduces the image exactly."""
    from rps7200.shading import apply_shading

    stream, image = index_stream(16, 8, 3)
    reference = ShadingReference(
        ref={c: np.linspace(28000, 32000, 16) for c in range(3)},
        mean={c: 30000.0 for c in range(3)},
        pixels_per_line=16,
    )
    want, report = apply_shading(image, reference, None)
    assert not np.array_equal(want, image), "fixture must actually change pixels"

    meta = {
        "resolution_dpi": 1800, "channels": 3,
        "channel_order": list(CHANNEL_ORDER[:3]),
        "width": 16, "height": 8, "depth": 16, "bytes_per_line": 32,
        # The correction that goes *with* these pixels, not one baked into them.
        "shading": report,
    }
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=reference, raw=stream, raw_layout=layout)

    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["image"]["corrections_applied"] == [], \
        "a shading report describes the pass, it does not mean the pixels are corrected"
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)

    back, info = library.corrected(path)
    assert info["corrected"] == "applied"
    assert np.array_equal(back, want), "the corrected image must be reproducible"
    assert np.array_equal(library.load(path)[0], image), "load() stays raw"


def test_a_raw_entry_reconstructs_by_plain_comparison(tmp_path):
    """With raw pixels stored, `reconstruct` compares the decode alone. The
    shading re-apply it had to do to avoid crying wolf is only for the legacy
    entries `tools/library.py migrate-raw` converts."""
    path, image, _ = make_entry(tmp_path)
    rebuilt, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(rebuilt, image)
    assert np.array_equal(library.decode_raw(path), image)


def test_a_pass_that_could_not_be_corrected_says_so_rather_than_lying(tmp_path):
    """`corrected()` never pretends. An entry with no reference comes back raw
    and says which, so a caller showing it can say the same."""
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 300, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]),
            "width": 16, "height": 8, "depth": 16, "bytes_per_line": 32,
            "shading_skipped": "shading=False (explicit)"}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=None, raw=stream, raw_layout=layout)
    back, info = library.corrected(path)
    assert info["corrected"] == "deliberately raw"
    assert np.array_equal(back, image)


def test_a_pass_that_wanted_correction_is_not_called_a_choice(tmp_path):
    """The other half of the entry above, and the one that was wrong.

    Both arrive as a non-empty `calibration.skipped` and they are opposite
    things: one caller passed `shading=False`, the other asked for correction
    and was filed without any. `verify` has always split them -- it calls the
    second "correction was asked for" -- and `corrected()` called both a
    deliberate choice, so Save As told the operator the rawness was intended.

    Sixteen entries in the real library say exactly this, from before `scan()`
    raised `ShadingUnavailable` rather than returning raw quietly.
    """
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 300, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]),
            "width": 16, "height": 8, "depth": 16, "bytes_per_line": 32,
            "shading_skipped": "no shading reference in this session"}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=None, raw=stream, raw_layout=layout)
    back, info = library.corrected(path)
    assert info["corrected"] == "raw -- correction was asked for"
    assert "deliberately" not in info["corrected"]
    assert np.array_equal(back, image)


def test_only_the_explicit_sentinel_counts_as_a_choice(tmp_path):
    """Pinned against the constant rather than against a copy of its text, so
    that changing the wording in `direct.py` cannot silently make every legacy
    entry read as deliberate again."""
    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 300, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]),
            "width": 16, "height": 8, "depth": 16, "bytes_per_line": 32,
            "shading_skipped": SHADING_SKIPPED_EXPLICIT}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, film=FilmNotes(),
                        reference=None, raw=stream, raw_layout=layout)
    assert library.corrected(path)[1]["corrected"] == "deliberately raw"


# --- which way each pass was read -------------------------------------------


def bottom_up_entry(tmp_path, *, stored_as_read=False, prescan=None,
                    prescan_meta=None):
    """An entry of a pass read bottom-up: its raw bytes in the order the
    scanner sent them, `scan.tif` upright -- or, with ``stored_as_read``, in
    that order too, as every entry filed before the decode turned passes."""
    from rps7200.direct import DirectScanner, ScanParameters
    from rps7200.direction import encode_index

    width, lines = 16, 8
    image = np.random.default_rng(5).integers(0, 65535, (lines, width, 3),
                                              dtype=np.uint16)
    raw = encode_index(image, reversed=True)
    params = ScanParameters(width=width, lines=lines, bytes_per_line=width * 2,
                            filter_offset1=0, filter_offset2=0, available_lines=0)
    upright, read = DirectScanner.decode_index(raw, params, 3)
    layout = {"format": "index", "bytes_per_line": width * 2,
              "line_stride": width * 2 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": width, "lines": lines, "channels": 3, "byte_order": "little"}
    meta = {"resolution_dpi": 600, "channels": 3, "channel_order": ["R", "G", "B"],
            "width": width, "height": lines, "depth": 16, "bytes_per_line": width * 2,
            "film": "negative", "shading": None,
            "read_direction": None if stored_as_read else read.as_record()}
    path = library.save(upright[::-1] if stored_as_read else upright, meta,
                        root=tmp_path, raw=raw, raw_layout=layout,
                        prescan=prescan, prescan_meta=prescan_meta)
    if stored_as_read:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
        record["scan"].pop("read_direction", None)
        (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    return path, image


def test_an_entry_says_which_way_its_pass_and_its_prescan_were_read(tmp_path):
    prescan_read = {"direction": "reversed", "turned": True, "lead": "B"}
    path, image = bottom_up_entry(
        tmp_path, prescan=np.zeros((4, 4, 3), np.uint8),
        prescan_meta={"read_direction": prescan_read,
                      "carriage_state": {"far_end": True}})
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["scan"]["read_direction"]["direction"] == "reversed"
    assert record["scan"]["read_direction"]["turned"] is True
    assert record["prescan"]["read_direction"] == prescan_read
    assert record["prescan"]["carriage_state"] == {"far_end": True}
    # the raw bytes stay in the order the scanner sent them; the decode is upright
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert library.reconstruct(path)[1].startswith("identical")


def test_a_stored_prescan_says_whether_it_was_corrected(tmp_path):
    """A roll's frame entry files the prescan `prescan()` returned, which is
    corrected, beside raw pixels in scan.tif -- and the record said nothing."""
    prescan = np.zeros((4, 4, 3), np.uint8)
    report = {"columns": 4, "width": 4, "clipped": 0}

    def label(**kw):
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        path, _, _ = make_entry(root, prescan=prescan, **kw)
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
        return record["prescan"]["corrections_applied"]

    assert label(prescan_meta={"shading": report}) == ["shading"]
    assert label(prescan_meta={"shading": None,
                               "shading_skipped": SHADING_SKIPPED_EXPLICIT}) == []
    assert label(prescan_meta={"shading": report},
                 prescan_corrections=[]) == [], "a caller filing it raw says so"
    assert label() is None, "nothing to go on: not known"


def test_an_entry_filed_bottom_up_is_named_not_called_a_regression(tmp_path):
    path, _ = bottom_up_entry(tmp_path, stored_as_read=True)
    _, verdict = library.reconstruct(path)
    assert "bottom-up" in verdict and "migrate-direction" in verdict


def test_migrating_turns_a_bottom_up_entry_upright_only_when_written(tmp_path):
    path, image = bottom_up_entry(tmp_path, stored_as_read=True)
    before = (path / "scan.tif").read_bytes()

    planned = library.migrate_direction(path)
    assert any("turned upright" in line for line in planned)
    assert (path / "scan.tif").read_bytes() == before, "a dry run writes nothing"

    library.migrate_direction(path, write=True)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert record["scan"]["read_direction"]["turned"] is True
    assert record["image"]["sha256"] == library._sha256(path / "scan.tif")
    assert library.reconstruct(path)[1].startswith("identical")
    assert library.migrate_direction(path, write=True) == [], "and only once"


def test_a_stored_prescan_is_turned_only_when_the_picture_is_decisive(tmp_path):
    """No bytes of its own, so it is judged against its upright scan -- and
    left alone, recorded unknown, when the picture cannot say."""
    rng = np.random.default_rng(9)
    # A top and a bottom, and a left and a right: a picture the same both
    # ways across could not tell rows reversed from a half turn.
    down = np.linspace(0, 40000, 40)[:, None, None]
    across = np.linspace(0, 20000, 16)[None, :, None]
    scene = (down + across + rng.random((40, 16, 3)) * 3000).astype(np.uint16)
    upside_down = np.ascontiguousarray(scene[::-1])

    path, _ = entry_with_prescan(tmp_path / "a", scene, upside_down)
    library.migrate_direction(path, write=True)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["prescan"]["read_direction"]["direction"] == "reversed"
    assert np.array_equal(tiff.read(str(path / "prescan.tif")), scene)

    flat = np.full((40, 16, 3), 1000, np.uint16)
    path, _ = entry_with_prescan(tmp_path / "b", scene, flat)
    library.migrate_direction(path, write=True)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["prescan"]["read_direction"]["direction"] == "unknown"
    assert np.array_equal(tiff.read(str(path / "prescan.tif")), flat)


def entry_with_prescan(root, scene, prescan):
    """A top-down pass of ``scene`` filed the old way, with ``prescan`` beside it."""
    from rps7200.direction import encode_index

    h, w = scene.shape[:2]
    layout = {"format": "index", "bytes_per_line": w * 2,
              "line_stride": w * 2 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": w, "lines": h, "channels": 3, "byte_order": "little"}
    meta = {"resolution_dpi": 600, "channels": 3, "channel_order": ["R", "G", "B"],
            "width": w, "height": h, "depth": 16, "bytes_per_line": w * 2,
            "film": "negative", "shading": None}
    path = library.save(scene, meta, root=root, raw=encode_index(scene),
                        raw_layout=layout, prescan=prescan)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record.pop("prescan", None)                       # filed before it existed
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    return path, record


# --- the 7200 dpi realignment -------------------------------------------------


def _native_entry(tmp_path, *, record_it=True, lines=12, width=16):
    from rps7200.direct import DirectScanner

    stream, decoded = index_stream(width, lines, 3, seed=7)
    stored = DirectScanner._realign_native_column_stagger(decoded)
    meta = {"resolution_dpi": 7200, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]), "width": width,
            "height": stored.shape[0], "depth": 16, "frame": [0, 0, 10343, 6887],
            "bytes_per_line": width * 2, "film": "negative"}
    if record_it:
        meta["stagger_realigned"] = DirectScanner.NATIVE_COLUMN_STAGGER_LINES
    layout = {"format": "index", "bytes_per_line": width * 2,
              "line_stride": width * 2 + INDEX_HEADER,
              "index_header": INDEX_HEADER, "width": width, "lines": lines,
              "channels": 3}
    return library.save(stored, meta, root=tmp_path, raw=stream,
                        raw_layout=layout), stored


def test_a_7200_dpi_entry_reconstructs_with_its_realignment(tmp_path):
    """`scan()` realigns the native column stagger before filing, so the
    decode alone is 4 rows taller and zigzagged. Unrecorded, every 7200 dpi
    entry read "decode CHANGED" for ever."""
    path, stored = _native_entry(tmp_path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["scan"]["stagger_realigned"] == 4
    _image, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(library.decode_raw(path), stored)


def test_a_7200_dpi_entry_filed_before_the_record_said_so_is_read_the_same(tmp_path):
    path, stored = _native_entry(tmp_path, record_it=False)
    _image, verdict = library.reconstruct(path)
    assert verdict == "identical to the stored image", verdict
    assert np.array_equal(library.decode_raw(path), stored)


def test_a_real_7200_dpi_regression_is_still_reported(tmp_path):
    path, stored = _native_entry(tmp_path)
    broken = stored.copy()
    broken[0, 0, 0] ^= 1
    tiff.write(str(path / "scan.tif"), broken)
    _image, verdict = library.reconstruct(path)
    assert verdict.startswith("decode CHANGED"), verdict


# --- atomic entries, checksums, and what verify can see ----------------------


def test_two_writers_in_one_second_get_two_entries(tmp_path):
    """The collision check looked for `scan.json`, which the first writer
    only creates at the very end -- so a second writer in the same second
    wrote into the same directory."""
    first = library._reserve(tmp_path, "20260924T220000Z_unknown-film_300dpi")
    second = library._reserve(tmp_path, "20260924T220000Z_unknown-film_300dpi")
    assert first != second and first.exists() and second.exists()


def test_a_finished_entry_carries_no_incomplete_marker(tmp_path):
    path, _, _ = make_entry(tmp_path)
    assert not (path / library.INCOMPLETE).exists()
    assert not list(path.glob(".*.part"))


def test_an_entry_cut_short_is_reported_not_passed_over(tmp_path):
    make_entry(tmp_path)
    cut = tmp_path / "20260924T220001Z_unknown-film_300dpi"
    cut.mkdir()
    (cut / library.INCOMPLETE).write_text("", encoding="utf-8")
    (cut / "scan.tif").write_bytes(b"half")
    orphan = tmp_path / "20260924T220002Z_unknown-film_300dpi"
    orphan.mkdir()
    (orphan / "raw.bin.gz").write_bytes(b"bytes with nothing to say what they are")
    problems = library.verify(tmp_path)
    assert any(cut.name in p and "did not finish" in p for p in problems)
    assert any(orphan.name in p and "no scan.json" in p for p in problems)


def test_an_index_that_cannot_be_rewritten_does_not_fail_a_filed_entry(
        tmp_path, monkeypatch):
    """The entry is complete once its record is in place. A sync client
    holding index.json made save() raise over a finished entry, and every
    caller treated the frame as lost: the roll stopped, its copies were never
    written, and the debug spool was kept to be filed twice."""
    import pytest

    def held(root):
        raise PermissionError(13, "held by another process", "index.json")

    monkeypatch.setattr(library, "reindex", held)
    with pytest.warns(RuntimeWarning, match="reindex"):
        path, image, _ = make_entry(tmp_path)
    assert (path / "scan.json").exists()
    assert not (path / library.INCOMPLETE).exists()
    monkeypatch.undo()
    assert library.verify(tmp_path) == []


def test_the_raw_bytes_are_written_before_anything_that_can_refuse(tmp_path):
    """They are the ground truth, and they were written last: a pass whose
    image the TIFF writer refused lost the only record of what went wrong."""
    import pytest

    stream, image = index_stream(16, 8, 3)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    with pytest.raises(ValueError, match="empty"):
        library.save(image[:0], {"resolution_dpi": 300, "channels": 3},
                     root=tmp_path, raw=stream, raw_layout=layout)
    [cut] = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (cut / library.INCOMPLETE).exists()
    assert library.read_raw(cut) == stream
    assert any(cut.name in p and "did not finish" in p
               for p in library.verify(tmp_path))


def test_two_indexes_written_at_once_leave_a_whole_one(tmp_path, monkeypatch):
    """The roll's filing thread and the debug flush in `close()` reindex at
    the same moment; one shared temporary name let them tear each other.

    Both writers are held between writing and renaming until the other is
    there too -- the window the race needs. Left to run freely, each thread
    wrote the same small JSON whole under the GIL, and the test passed
    against the shared name and against a plain rewrite in place alike."""
    import os
    import threading

    for _ in range(3):
        entry_with(tmp_path)
    real = os.replace
    both = threading.Barrier(2, timeout=10)
    renamed: dict[int, str] = {}

    def replace(src, dst):
        # Only a thread's first rename: `_replace` tries again on Windows
        # while the other's rename holds the file, and a second wait would
        # find nobody at the barrier.
        me = threading.get_ident()
        if Path(dst).name == library.INDEX and me not in renamed:
            renamed[me] = Path(src).name
            both.wait()
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    errors = []

    def go():
        try:
            library.reindex(tmp_path)
        except Exception as exc:                           # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # Each went beside and was renamed over, under a name of its own.
    assert len(set(renamed.values())) == 2, renamed
    assert len(json.loads((tmp_path / library.INDEX).read_text(
        encoding="utf-8"))) == 3
    assert not list(tmp_path.glob(".*.part"))


def _fails_at(monkeypatch, step):
    """Make one step of `library.save` fail as a full disk or a kill would."""
    def refuse(*a, **k):
        raise OSError(28, f"No space left on device ({step})")

    if step == "scan.tif":
        monkeypatch.setattr(library.tiff, "write", refuse)
    elif step == "prescan.tif":
        real = library.tiff.write

        def write(path, *a, **k):
            if str(path).endswith("prescan.tif"):
                refuse()
            return real(path, *a, **k)

        monkeypatch.setattr(library.tiff, "write", write)
    elif step == "shading.npz":
        monkeypatch.setattr(ShadingReference, "save", refuse)
    elif step == "raw bytes":
        monkeypatch.setattr(library.gzip, "open", refuse)
    elif step == "record":
        monkeypatch.setattr(library, "_write_atomic", refuse)


@pytest.mark.parametrize("step", ["scan.tif", "prescan.tif", "shading.npz",
                                  "raw bytes", "record"])
def test_a_save_cut_short_is_reported_and_never_listed(tmp_path, monkeypatch,
                                                       step):
    """Whole or not at all, and the "not at all" has to be visible. The only
    test of that marker built the half-written directory by hand; this one
    makes `library.save` itself fail at each of its writes in turn."""
    make_entry(tmp_path)
    # A context of its own: `undo()` would also lift conftest's isolation.
    with monkeypatch.context() as failing:
        _fails_at(failing, step)
        with pytest.raises(OSError):
            make_entry(tmp_path, prescan=np.zeros((4, 6, 3), np.uint8))

    cut = [p for p in tmp_path.iterdir()
           if p.is_dir() and (p / library.INCOMPLETE).exists()]
    assert len(cut) == 1, f"no marker left when {step} failed"
    assert len(library.entries(tmp_path)) == 1, "the cut entry was listed"
    assert any(cut[0].name in p and "did not finish" in p
               for p in library.verify(tmp_path))


def _plain_entry_with_bytes(tmp_path):
    stream, image = index_stream(16, 8, 3, seed=5)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16}
    layout = {"format": "index", "bytes_per_line": 32,
              "line_stride": 32 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, raw=stream,
                        raw_layout=layout, compress=False,
                        prescan=np.full((4, 6, 3), 7, np.uint8))
    return path, image, stream


def _compact_cut_short_after_the_swaps(path, monkeypatch):
    """`compact` killed between swapping the TIFFs in and writing the record."""
    def killed(*a, **k):
        raise OSError("killed before the record was written")

    with monkeypatch.context() as dying:
        dying.setattr(library, "_write_atomic", killed)
        with pytest.raises(OSError):
            library.compact(path)


def _real_problems(root):
    """What verify says, less the one thing every entry here lacks."""
    return [p for p in library.verify(root) if "never be corrected" not in p]


def test_a_compact_cut_short_leaves_pixels_and_bytes_intact_and_can_finish(
        tmp_path, monkeypatch):
    """What an interrupted compact leaves is readable, loses nothing, and a
    second compact completes it -- including the prescan, which no compact
    test had covered."""
    path, image, stream = _plain_entry_with_bytes(tmp_path)
    _compact_cut_short_after_the_swaps(path, monkeypatch)

    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert library.read_raw(path) == stream
    assert library.compact(path) is True
    assert _real_problems(tmp_path) == []
    assert library.reconstruct(path)[1] == "identical to the stored image"


@pytest.mark.xfail(strict=True, reason=(
    "T-07: compact swaps the recompressed TIFFs in before it writes the "
    "record, so an interruption between them leaves scan.json holding the "
    "old checksums and verify calls intact pixels damaged"))
def test_a_compact_cut_short_is_not_reported_as_damage(tmp_path, monkeypatch):
    """A false alarm in the one check that exists to catch real damage, on
    an entry nothing will ever compact again: the session compacts only what
    it filed itself, and no tool re-runs it."""
    path, _, _ = _plain_entry_with_bytes(tmp_path)
    before = (path / "scan.tif").read_bytes()
    _compact_cut_short_after_the_swaps(path, monkeypatch)
    if (path / "scan.tif").read_bytes() == before:
        # The bare-install writer (no tifffile) has no compression to apply,
        # so the swap changes no byte and there is no checksum to go stale.
        pytest.skip("this TIFF writer compresses nothing")
    assert _real_problems(tmp_path) == []


def test_every_file_of_an_entry_is_checksummed(tmp_path):
    """A damaged reference corrects every export of the entry wrongly, and
    `verify` could see damage only to scan.tif and the raw bytes."""
    path = entry_with(tmp_path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert "shading.npz" in record["files"]
    assert library.verify(tmp_path) == []
    data = bytearray((path / "shading.npz").read_bytes())
    data[-1] ^= 0xFF
    (path / "shading.npz").write_bytes(bytes(data))
    assert any("shading.npz does not match" in p for p in library.verify(tmp_path))


def test_a_renamed_entry_is_found_where_it_is(tmp_path):
    path, _, _ = make_entry(tmp_path)
    moved = path.with_name(path.name + "-copy")
    path.rename(moved)
    record = library.entries(tmp_path)[0]
    assert library.entry_path(tmp_path, record) == moved
    assert any("records itself as" in p for p in library.verify(tmp_path))


def test_a_scan_taken_raw_on_purpose_is_not_a_problem(tmp_path):
    """Reported as one -- 'correction was asked for', about the sentinel that
    says it was not -- it kept `make verify` red for a week."""
    from rps7200.direct import SHADING_SKIPPED_EXPLICIT

    stream, image = index_stream(16, 8, 3)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16, "shading_skipped": SHADING_SKIPPED_EXPLICIT}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    library.save(image, meta, root=tmp_path, raw=stream, raw_layout=layout)
    assert library.verify(tmp_path) == []


def test_an_uncalibrated_ladder_can_be_marked_as_meant(tmp_path):
    path = entry_with(tmp_path, reference=False)
    assert any("never be corrected" in p for p in library.verify(tmp_path))
    library.add_tags(path, [library.ON_PURPOSE])
    assert library.verify(tmp_path) == []


def test_reconstruct_reports_a_missing_scan_tif_and_carries_on(tmp_path):
    path, _, _ = make_entry(tmp_path)
    (path / "scan.tif").unlink()
    _image, verdict = library.reconstruct(path)
    assert verdict.startswith("could not read scan.tif")
    assert verdict.kind == library.DAMAGED


def test_a_decode_that_changes_only_the_sample_type_is_a_change(tmp_path):
    """`array_equal` ignores dtype, and `apply_shading` scales by it: 8-bit
    values coming back as uint16 would correct almost black while every value
    still "matched"."""
    _, image = index_stream(16, 8, 3)
    small = (image >> 8).astype(np.uint16)          # every value fits a byte
    stream = bytearray()
    for y in range(8):
        for c in range(3):
            stream += (CHANNEL_ORDER[c].encode() * INDEX_HEADER
                       + small[y, :, c].tobytes())
    # Two bytes a sample in the stream, one in the stored file: the same values.
    path = library.save(
        small.astype(np.uint8), {"resolution_dpi": 300, "channels": 3,
                                 "width": 16, "height": 8, "depth": 8},
        root=tmp_path, raw=bytes(stream),
        raw_layout={"bytes_per_line": 32, "width": 16, "lines": 8,
                    "channels": 3})
    _image, verdict = library.reconstruct(path)
    assert verdict.kind == library.CHANGED, verdict
    assert "uint16" in verdict and "uint8" in verdict


def test_a_bit_flipped_raw_file_is_damage_not_a_decode_change(tmp_path):
    """A plain `raw.bin` that decodes still decodes: it was reported as
    "decode CHANGED", blaming the decoder for the disk."""
    stream, image = index_stream(16, 8, 3, seed=9)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16}
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, raw=stream,
                        raw_layout=layout, compress=False)
    data = bytearray((path / library.RAW_PLAIN).read_bytes())
    data[40] ^= 0x01
    (path / library.RAW_PLAIN).write_bytes(bytes(data))
    _image, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict
    assert "CHANGED" not in verdict


def test_a_damaged_reference_is_a_verdict_not_an_abort(tmp_path):
    """A truncated .npz raises BadZipFile, which stopped the whole run."""
    from rps7200.shading import apply_shading

    stream, image = index_stream(16, 8, 3)
    reference = ShadingReference(
        ref={c: np.linspace(28000, 32000, 16) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=16)
    shaded, _ = apply_shading(image, reference, None)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(shaded, {"resolution_dpi": 300, "channels": 3},
                        root=tmp_path, reference=reference, raw=stream,
                        raw_layout=layout, corrections=["shading"])
    data = (path / "shading.npz").read_bytes()
    (path / "shading.npz").write_bytes(data[: len(data) // 2])
    _image, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict
    assert "shading.npz" in verdict


# --- filed plain with the scanner open, compacted after -----------------------


def test_an_entry_filed_plain_reads_like_any_other_and_compacts_losslessly(tmp_path):
    """Compressing with the scanner open and idle preceded a wedge, so the
    window files single scans plain and compresses them once it has closed."""
    stream, image = index_stream(16, 8, 3, seed=5)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16}
    layout = {"format": "index", "bytes_per_line": 32,
              "line_stride": 32 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, raw=stream,
                        raw_layout=layout, compress=False)
    assert (path / library.RAW_PLAIN).exists()
    assert not (path / library.RAW_FILE).exists()
    assert library.read_raw(path) == stream
    # (It has no shading reference; that is the only thing verify may say.)
    assert [p for p in library.verify(tmp_path) if "never be corrected" not in p] == []
    assert library.reconstruct(path)[1] == "identical to the stored image"

    assert library.compact(path) is True
    assert not (path / library.RAW_PLAIN).exists()
    assert library.read_raw(path) == stream
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert [p for p in library.verify(tmp_path) if "never be corrected" not in p] == []
    assert library.reconstruct(path)[1] == "identical to the stored image"
    assert library.compact(path) is False, "compacted twice"


def test_a_reference_without_a_mask_is_not_applied_to_a_narrower_pass(tmp_path):
    """No mask matches columns one to one, right only for a pass that read
    every CCD pixel. On a narrower one the wrong columns were divided in and
    the result called "applied"."""
    stream, image = index_stream(16, 8, 3)
    wide = ShadingReference(ref={c: np.linspace(20000.0, 40000.0, 64)
                                 for c in range(3)},
                            mean={c: 30000.0 for c in range(3)},
                            pixels_per_line=64)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, {"resolution_dpi": 1800, "channels": 3},
                        root=tmp_path, reference=wide, raw=stream,
                        raw_layout=layout)
    out, record = library.corrected(path)
    assert record["corrected"] == "no mask"
    assert np.array_equal(out, image)
    assert any("no CCD mask" in p for p in library.verify(tmp_path))


def test_a_reference_that_will_not_load_is_named_not_raised(tmp_path):
    """A truncated .npz raised BadZipFile out of every view and export."""
    path, image, _ = make_entry(tmp_path)
    data = (path / "shading.npz").read_bytes()
    (path / "shading.npz").write_bytes(data[: len(data) // 2])
    out, record = library.corrected(path)
    assert record["corrected"] == "reference unreadable"
    assert "shading.npz" in record["reference_error"]
    assert np.array_equal(out, image)


def _plain_entry(tmp_path, prescan=None):
    stream, image = index_stream(16, 8, 3, seed=5)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16}
    layout = {"format": "index", "bytes_per_line": 32,
              "line_stride": 32 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": 16, "lines": 8, "channels": 3}
    path = library.save(image, meta, root=tmp_path, raw=stream,
                        raw_layout=layout, compress=False, prescan=prescan)
    return path, image


def _integrity(tmp_path):
    return [p for p in library.verify(tmp_path) if "never be corrected" not in p]


def test_a_compaction_a_kill_stopped_is_finished_by_a_second_one(tmp_path):
    """Stopped after scan.tif was swapped and before the record: the stale
    checksum made verify report damage on an intact picture for ever."""
    prescan = np.full((4, 6, 3), 7, np.uint8)
    path, image = _plain_entry(tmp_path, prescan=prescan)
    # the swap, with the record left as it was: same pixels, other bytes
    library._replace_tiff(path / "scan.tif", image, resolution=None)
    library._replace_tiff(path / "prescan.tif", prescan, resolution=72)
    assert _integrity(tmp_path), "the fixture must leave stale checksums"
    assert library.compact(path) is True
    assert _integrity(tmp_path) == []
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)


def test_compaction_does_not_bless_a_damaged_picture(tmp_path):
    """A checksum that disagrees is re-taken only where the pixels are proved;
    re-checksumming whatever is there would hide real damage for good."""
    path, image = _plain_entry(tmp_path)
    damaged = image.copy()
    damaged[0, 0, 0] ^= 1
    tiff.write(str(path / "scan.tif"), damaged, resolution=300,
               compress=False)
    import pytest
    with pytest.raises(OSError, match="scan.tif does not match"):
        library.compact(path)
    assert (path / library.RAW_PLAIN).exists()
    assert any("scan.tif does not match" in p for p in library.verify(tmp_path))


def test_a_damaged_prescan_is_not_mistaken_for_a_stopped_compaction(tmp_path):
    """prescan.tif has no bytes to prove it by. It is swapped after scan.tif,
    so a compaction stopped past it shows in scan.tif too; alone, it is
    damage."""
    path, _ = _plain_entry(tmp_path, prescan=np.full((4, 6, 3), 7, np.uint8))
    tiff.write(str(path / "prescan.tif"), np.full((4, 6, 3), 9, np.uint8),
               compress=False)
    import pytest
    with pytest.raises(OSError, match="prescan.tif does not match"):
        library.compact(path)


def test_migrating_again_after_a_stop_refreshes_the_turned_checksum(tmp_path):
    """Stopped after scan.tif was turned upright and before the record: the
    re-run recorded the direction and kept the old checksum."""
    path, image = bottom_up_entry(tmp_path, stored_as_read=True)
    library._replace_tiff(path / "scan.tif", image, resolution=600)
    assert any("scan.tif does not match" in p for p in library.verify(tmp_path))
    library.migrate_direction(path, write=True)
    assert [p for p in library.verify(tmp_path)
            if "never be corrected" not in p] == []


def test_decode_raw_is_none_for_bytes_it_cannot_place(tmp_path):
    """It promised None when a decode is impossible, and let the decode's
    ScanReadError escape instead."""
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    path = library.save(np.zeros((8, 16, 3), np.uint16),
                        {"resolution_dpi": 300, "channels": 3}, root=tmp_path,
                        raw=b"\x00" * (8 * 3 * 34), raw_layout=layout)
    assert library.decode_raw(path) is None


# --- the calibration behind a reference, read back ---------------------------


def calibration_lines(ppl=16, seed=0):
    """Calibration bytes as the device sends them: 16-bit, tagged, unlit
    lines then lit ones for every channel."""
    rng = np.random.default_rng(seed)
    out = bytearray()
    for level in (170, 47000):
        for _ in range(4):
            for tag in b"RGB":
                line = rng.normal(level, level * 0.01, ppl).clip(0, 65535)
                out += bytes([tag, tag]) + line.astype("<u2").tobytes()
    return bytes(out)


def archived(root, ppl=16, seed=0):
    """A calibration archived by the driver's own writer."""
    from conftest import FakeTransport

    from rps7200.direct import DirectScanner
    from rps7200.shading import calculate_shading

    data = calibration_lines(ppl, seed)
    s = DirectScanner(transport=FakeTransport())
    s.verbose = False
    reference = calculate_shading(data, ppl)
    folder = s.archive_calibration(
        {"data": data, "reference": reference, "ccd_mask": bytes(ppl),
         "pixels_per_line": ppl, "bytes_per_line": ppl * 2}, root)
    return folder, reference


def test_an_archived_calibration_reduces_again_to_its_reference(tmp_path):
    """Nothing read the archive back: it was kept for a recomputation no
    code could make."""
    folder, reference = archived(tmp_path / "calibration")
    assert library.same_reference(library.rebuild_reference(folder), reference)
    _ref, verdict = library.recalibrate(folder)
    assert verdict.kind == library.IDENTICAL, verdict


def test_a_changed_reduction_is_reported(tmp_path, monkeypatch):
    from rps7200 import shading

    folder, _ = archived(tmp_path / "calibration")
    monkeypatch.setattr(library, "calculate_shading",
                        lambda data, ppl: shading.calculate_shading(
                            data, ppl, split_ratio=1e9))       # one phase only
    _ref, verdict = library.recalibrate(folder)
    assert verdict.kind == library.CHANGED, verdict


def test_damaged_calibration_bytes_are_refused_not_reduced(tmp_path):
    import pytest

    folder, _ = archived(tmp_path / "calibration")
    data = bytearray((folder / "data.bin").read_bytes())
    data[5] ^= 0xFF
    (folder / "data.bin").write_bytes(bytes(data))
    with pytest.raises(ValueError, match="checksum"):
        library.rebuild_reference(folder)
    assert library.recalibrate(folder)[1].kind == library.DAMAGED


def _entry_corrected_by(tmp_path, reference, origin):
    stream, image = index_stream(16, 8, 3)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    return library.save(image, {"resolution_dpi": 300, "channels": 3,
                                "shading_origin": origin},
                         root=tmp_path / "library", reference=reference,
                         ccd_mask=bytes(16), raw=stream, raw_layout=layout)


def test_an_entry_finds_the_calibration_behind_its_reference(tmp_path, monkeypatch):
    """By the path its record names -- and, for a reference loaded from the
    cache, which names none, by content."""
    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("calibration"))
    named = _entry_corrected_by(tmp_path, reference,
                                {"action": "calibrated", "archive": str(folder)})
    loaded = _entry_corrected_by(tmp_path, reference,
                                 {"action": "loaded", "path": "calibration/shading.npz"})
    assert library.calibration_of(named).resolve() == folder.resolve()
    assert library.calibration_of(loaded).resolve() == folder.resolve()
    _other, elsewhere = archived(tmp_path / "other", seed=3)
    stranger = _entry_corrected_by(tmp_path, elsewhere, {"action": "loaded"})
    assert library.calibration_of(stranger) is None


def test_verify_reports_a_calibration_its_entries_name_and_lost(tmp_path, monkeypatch):
    import shutil

    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("calibration"))
    _entry_corrected_by(tmp_path, reference,
                        {"action": "calibrated", "archive": str(folder)})
    assert library.verify(tmp_path / "library") == []
    shutil.rmtree(folder)
    problems = library.verify(tmp_path / "library")
    assert any("is missing" in p and folder.name in p for p in problems), problems


def test_an_archive_recorded_on_windows_is_found_on_any_os(tmp_path, monkeypatch):
    """The driver records the archive with `str()`, so a Windows entry names
    ``cal-a\\<time>``; read elsewhere that was one file name, and verify
    called an intact archive missing. Kept outside `calibration/` so the
    search by content cannot find it in the named path's place."""
    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("cal-a"))
    named = _entry_corrected_by(
        tmp_path, reference,
        {"action": "calibrated", "archive": f"cal-a\\{folder.name}"})
    assert library.verify(tmp_path / "library") == []
    found = library.calibration_of(named)
    assert found is not None and found.resolve() == folder.resolve()


def test_compacting_never_blesses_a_damaged_tiff(tmp_path):
    """compact re-read the plain TIFFs, rewrote them compressed and recorded
    fresh checksums, so a scan.tif or prescan.tif damaged since filing came
    out verified -- and a prescan has no raw bytes to rebuild it from."""
    stream, image = index_stream(16, 8, 3, seed=5)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
            "depth": 16}
    layout = {"format": "index", "bytes_per_line": 32,
              "line_stride": 32 + INDEX_HEADER, "index_header": INDEX_HEADER,
              "width": 16, "lines": 8, "channels": 3}
    prescan = np.full((4, 6, 3), 90, np.uint8)
    path = library.save(image, meta, root=tmp_path, raw=stream,
                        raw_layout=layout, prescan=prescan, compress=False)
    damaged = prescan.copy()
    damaged[0, 0, 0] = 91                        # one bit of rot, same size
    tiff.write(str(path / "prescan.tif"), damaged, compress=False)
    try:
        library.compact(path)
    except OSError as refused:
        assert "prescan.tif does not match" in str(refused)
    else:
        raise AssertionError("compacted a damaged prescan.tif")
    assert (path / library.RAW_PLAIN).exists(), "left as it was"
    assert any("prescan.tif" in p for p in library.verify(tmp_path))


def test_a_demo_entry_without_a_reference_is_not_a_problem(tmp_path):
    """Built from a finished picture, it has no calibration by design."""
    stream, image = index_stream(16, 8, 3)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    library.save(image, {"resolution_dpi": 300, "channels": 3, "width": 16,
                         "height": 8, "depth": 16, "demo": True},
                 root=tmp_path, raw=stream, raw_layout=layout)
    assert library.verify(tmp_path) == []
