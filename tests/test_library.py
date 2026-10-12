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


def test_the_edge_reader_is_named_by_a_digest_of_its_own(tmp_path):
    """The package's digest covers `rps7200/*.py`, and a frame's position
    comes from `tools/frame_edges`, which it never covered. A field of its
    own, so the package's still compares with every entry filed before."""
    import hashlib

    def digest(folder):
        h = hashlib.sha256()
        for module in sorted(folder.glob("*.py")):
            h.update(module.name.encode("utf-8") + b"\0" + module.read_bytes())
        return h.hexdigest()

    repo = Path(library.__file__).resolve().parent.parent
    path, _, _ = make_entry(tmp_path)
    code = json.loads((path / "scan.json").read_text(encoding="utf-8"))["provenance"]
    assert code["edge_reader_source_sha256_at_import"] == digest(
        repo / "tools" / "frame_edges")
    assert code["driver_source_sha256_at_import"] == digest(repo / "rps7200"), \
        "the package's digest is no longer what every earlier entry recorded"
    # And none, rather than the digest of nothing, where there is no reader.
    assert library._source_digest(tmp_path / "nowhere") is None


def test_the_code_a_reading_names_is_had_without_git(monkeypatch):
    """What the window's Tk thread and an aiming roll's manifest write to say
    which code read a frame: what every entry records at import, the
    reader's source and the driver's beside it -- the reader takes code from
    `rps7200.framing` -- and not one call to git, which `provenance` made
    twice, ten seconds each at worst."""
    import subprocess

    at_import = library.provenance()

    def no_git(*args, **kwargs):
        raise AssertionError(f"git was asked: {args}")

    monkeypatch.setattr(subprocess, "run", no_git)
    code = library.code_identity()
    assert code == {
        "driver_commit": at_import["driver_commit_at_import"],
        "driver_dirty": at_import["driver_dirty_at_import"],
        "driver_source_sha256": at_import["driver_source_sha256_at_import"],
        "edge_reader_sha256": at_import["edge_reader_source_sha256_at_import"]}
    assert len(code["driver_source_sha256"]) == len(code["edge_reader_sha256"]) == 64


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


def test_a_named_twin_without_raw_bytes_never_costs_the_one_with_them(
        tmp_path):
    """Twins with raw bytes on one side only are the same by their pixels.
    Ranked on whether a roll names it before anything else, a named copy
    holding neither raw bytes nor a calibration stood as the twin of one
    holding both, and the only raw bytes of the scan went -- with a reason
    that did not even say so. Both stay now."""
    bare = entry_with(tmp_path, raw=False, reference=False)
    full = entry_with(tmp_path, raw=True, reference=True)
    assert library.prunable(tmp_path, named={bare.name}) == []
    # Named by nothing, the bare one goes, as it always has.
    [(doomed, _)] = library.prunable(tmp_path)
    assert doomed["id"] == bare.name != full.name


def test_an_entry_a_roll_names_is_never_pruned(tmp_path):
    """So no roll is left naming an entry `duplicates --delete` took, and
    none has a frame to mark for it: of two twins both named, both stay."""
    first, second = entry_with(tmp_path), entry_with(tmp_path)
    assert library.prunable(tmp_path, named={first.name, second.name}) == []
    [(doomed, _)] = library.prunable(tmp_path, named={first.name})
    assert doomed["id"] == second.name


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
    if record_it:
        return library.save(stored, meta, root=tmp_path, raw=stream,
                            raw_layout=layout), stored
    # Filed today with nothing to say its rows were trimmed, the bytes are
    # four rows more than the picture, and `save` says so. An entry from
    # before the record said so was filed before that was asked, too.
    with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
        path = library.save(stored, meta, root=tmp_path, raw=stream,
                            raw_layout=layout)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    del record["raw"]["disagrees"]
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    return path, stored


def test_a_7200_dpi_entry_reconstructs_with_its_realignment(tmp_path):
    """`scan()` realigns the native column stagger before filing, so the
    decode alone is 4 rows taller and zigzagged. Unrecorded, every 7200 dpi
    entry read "decode CHANGED" for ever."""
    path, stored = _native_entry(tmp_path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["scan"]["stagger_realigned"] == 4
    # The rows it trimmed are the bytes' and not the picture's, and the
    # check at filing knows it: no false alarm on every 7200 dpi entry.
    assert record["raw"]["disagrees"] == {}
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


# --- raw bytes held against the picture they are filed with -----------------


def _layout(width, lines, channels, **more):
    return {"format": "index", "bytes_per_line": width * 2,
            "line_stride": width * 2 + INDEX_HEADER, "index_header": INDEX_HEADER,
            "width": width, "lines": lines, "channels": channels,
            "byte_order": "little", **more}


def _meta(image, **more):
    lines, width, channels = image.shape
    return {"resolution_dpi": 600, "channels": channels,
            "channel_order": list(CHANNEL_ORDER[:channels]), "width": width,
            "height": lines, "depth": 16, "bytes_per_line": width * 2,
            "film": "negative", "shading": None, **more}


def _disagrees(path):
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    return record["raw"]["disagrees"]


def test_bytes_of_another_picture_are_filed_and_said(tmp_path):
    """Another pass's bytes decode to a different photograph. The session and
    the tools drop such bytes before filing; a caller that did not ask filed
    them under a record that said nothing, and only `reconstruct` could find
    out. Filed all the same -- they may be the one record of what went wrong
    -- and the record, a warning and `verify` say so."""
    stream, _ = index_stream(20, 8, 3, seed=1)              # 20 wide
    _, image = index_stream(16, 8, 3, seed=2)
    with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
        path = library.save(image, _meta(image), root=tmp_path, raw=stream,
                            raw_layout=_layout(20, 8, 3, lines_received=24))
    assert library.read_raw(path) == stream, "the bytes were dropped"
    assert _disagrees(path) == {"width": [16, 20]}, "as [picture, bytes]"
    said = [p for p in library.verify(tmp_path) if path.name in p]
    assert any("width 16 for the picture, 20 in the bytes" in p for p in said), said


@pytest.mark.parametrize("streamed", [False, True])
def test_bytes_whose_length_is_not_their_layouts_are_said(tmp_path, streamed):
    """A layout taken from one pass with the bytes of another: the layout
    agrees with the picture, so only the bytes' own length can tell. Asked of
    the count already made while writing them, streamed or not."""
    stream, _ = index_stream(16, 12, 3, seed=1)             # 12 rows
    _, image = index_stream(16, 8, 3, seed=2)
    given = {"raw": stream}
    if streamed:
        (tmp_path / "spooled.bin").write_bytes(stream)
        given = {"raw_path": tmp_path / "spooled.bin"}
    with pytest.warns(RuntimeWarning, match="bytes"):
        path = library.save(image, _meta(image), root=tmp_path / "lib",
                            raw_layout=_layout(16, 8, 3, lines_received=24),
                            **given)
    stride = 16 * 2 + INDEX_HEADER
    assert _disagrees(path) == {"bytes": [8 * 3 * stride, 12 * 3 * stride]}
    assert library.read_raw(path) == stream


def test_passes_cut_short_or_read_bottom_up_agree_with_their_bytes(
        tmp_path, monkeypatch):
    """The shapes the session's guard was taught, asked again at filing: a
    read the device ended early -- part way through a row's planes too --
    and a pass read bottom-up and turned upright. A false alarm on these is
    an alarm on every pass that matters most. And nothing is decoded to ask:
    filing runs with the device open."""
    import warnings

    from rps7200.direct import DirectScanner, ScanParameters
    from rps7200.direction import encode_index

    width, lines = 16, 8
    scene = np.random.default_rng(3).integers(0, 65535, (lines, width, 3),
                                              dtype=np.uint16)
    params = ScanParameters(width=width, lines=lines, bytes_per_line=width * 2,
                            filter_offset1=0, filter_offset2=0,
                            available_lines=0)
    stride = width * 2 + INDEX_HEADER
    filed = []
    for upward in (False, True):
        blob = encode_index(scene, reversed=upward)
        for short in (0, 1, 2, 4):                          # planes not sent
            sent = blob[:len(blob) - short * stride]
            pixels, _read = DirectScanner.decode_index(sent, params, 3)
            filed.append((pixels, DirectScanner._raw_layout(params, 3, sent),
                          sent))

    def no_decode(*a, **kw):
        raise AssertionError("decoded at filing")

    monkeypatch.setattr(DirectScanner, "decode_index", no_decode)
    monkeypatch.setattr(library, "decode_raw", no_decode)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for n, (pixels, layout, sent) in enumerate(filed):
            path = library.save(pixels, _meta(pixels), root=tmp_path / str(n),
                                raw=sent, raw_layout=layout)
            assert _disagrees(path) == {}, (n, _disagrees(path))
    assert not any("cannot be" in p for p in library.verify(tmp_path / "0"))


def test_an_entry_with_no_bytes_or_no_picture_is_not_judged(tmp_path):
    """None, not {}: nothing was held against anything. A failed pass whose
    bytes nothing could decode is filed as its lines, one row each -- not a
    picture, so not another pass's either."""
    stream, image = index_stream(16, 8, 3)
    assert _disagrees(library.save(image, _meta(image), root=tmp_path)) is None
    stride = 16 * 2 + INDEX_HEADER
    lines = np.frombuffer(stream, np.uint8).reshape(24, stride, 1)
    failed = _meta(image, failed={"stage": "in the decode", "error": "x",
                                  "pixels": "the lines as they arrived"})
    path = library.save(lines, failed, root=tmp_path, raw=stream,
                        raw_layout=_layout(16, 8, 3, lines_received=24))
    assert _disagrees(path) is None


def test_the_bytes_length_is_counted_by_the_session_guards_own_rule():
    """One rule, in `raw_bytes_disagree`: the rows the bytes hold, less what
    the 7200 dpi realignment trimmed. `size` only adds the bytes' own count
    of them; the session's guard, which passes none, asks what it always did."""
    from rps7200.direct import raw_bytes_disagree

    stride = 16 * 2 + INDEX_HEADER
    layout = _layout(16, 12, 3, lines_received=36)
    trimmed = {"stagger_realigned": 4}
    assert raw_bytes_disagree((8, 16, 3), layout, trimmed) == {}
    assert raw_bytes_disagree((8, 16, 3), layout, trimmed,
                              size=36 * stride) == {}
    assert raw_bytes_disagree((8, 16, 3), layout, trimmed,
                              size=36 * stride + 2 * stride) == {}
    assert raw_bytes_disagree((8, 16, 3), layout, trimmed,
                              size=30 * stride) == {
        "bytes": (30 * stride, 36 * stride)}
    # Where the layout gives no stride or no channel count, the length says
    # nothing -- as an absent field says nothing.
    assert raw_bytes_disagree((8, 16, 3), {"width": 16}, size=7) == {}


def _set_disagrees(path, value):
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["raw"]["disagrees"] = value
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")


def test_reconstruct_names_bytes_filed_as_not_their_pictures_apart(tmp_path):
    """`reconstruct` is the regression gate, and an entry `save` filed
    knowing its bytes cannot be its picture's read "decode CHANGED" on
    every run, for good. Its own kind -- decoded and compared all the same.
    The record's word is what makes it so: without it, the same entry is a
    change as ever."""
    stream, _ = index_stream(20, 8, 3, seed=1)              # 20 wide
    _, image = index_stream(16, 8, 3, seed=2)
    with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
        path = library.save(image, _meta(image), root=tmp_path, raw=stream,
                            raw_layout=_layout(20, 8, 3, lines_received=24))
    decoded, verdict = library.reconstruct(path)
    assert verdict.kind == library.DISAGREED, verdict
    assert "width 16 for the picture, 20 in the bytes" in verdict
    assert "now (8, 20, 3), stored (8, 16, 3)" in verdict
    assert decoded is not None and decoded.shape == (8, 20, 3)

    _set_disagrees(path, {})
    _, verdict = library.reconstruct(path)
    assert verdict.kind == library.CHANGED, verdict
    assert verdict.startswith("decode CHANGED: now (8, 20, 3)"), verdict


def test_bytes_filed_as_not_their_pictures_that_will_not_decode(tmp_path):
    """Bytes whose length the picture's rows cannot take, and that today's
    decode raises on: known at filing, so not a decode regression either."""
    _, image = index_stream(16, 8, 3)
    stride = 16 * 2 + INDEX_HEADER
    with pytest.warns(RuntimeWarning, match="bytes"):
        path = library.save(image, _meta(image), root=tmp_path,
                            raw=b"\x00" * (12 * 3 * stride),
                            raw_layout=_layout(16, 8, 3))
    decoded, verdict = library.reconstruct(path)
    assert decoded is None
    assert verdict.kind == library.DISAGREED, verdict
    assert "today's decode raises on them" in verdict


def test_bytes_said_to_disagree_are_still_held_to_their_checksum(tmp_path):
    """The record's word excuses a decode that does not reproduce the
    picture, and nothing else: bytes that do reproduce it are identical,
    and damage is damage."""
    path, _, raw = make_entry(tmp_path)
    _set_disagrees(path, {"width": [16, 20]})
    _, verdict = library.reconstruct(path)
    assert verdict.kind == library.IDENTICAL, verdict

    (path / "raw.bin.gz").write_bytes(
        gzip.compress(raw[:-1] + bytes([raw[-1] ^ 1])))
    _, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict


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


@pytest.mark.parametrize("stored", [[], "a string", 3])
def test_a_record_that_is_not_an_object_breaks_nothing_else(tmp_path, stored):
    """Valid JSON that is not an object raised in entries(), and with it in
    reindex, verify and every later save -- which then called a complete
    entry failed, and its caller kept a second copy of the picture."""
    odd = tmp_path / "20260101T000000Z_odd_300dpi"
    odd.mkdir()
    (odd / "scan.json").write_text(json.dumps(stored), encoding="utf-8")
    path, _, _ = make_entry(tmp_path)
    assert [r["_dir"] for r in library.entries(tmp_path)] == [path.name]
    problems = library.verify(tmp_path)
    assert any(odd.name in p and "not a record" in p for p in problems)


def test_an_index_that_raises_anything_does_not_fail_a_filed_entry(
        tmp_path, monkeypatch):
    """Not only OSError: whatever the summary trips on is said, and the
    entry stands."""
    def broken(root):
        raise TypeError("a record the summary cannot read")

    monkeypatch.setattr(library, "reindex", broken)
    with pytest.warns(RuntimeWarning, match="reindex"):
        path, _, _ = make_entry(tmp_path)
    assert not (path / library.INCOMPLETE).exists()


def test_a_marker_held_open_as_save_removes_it_does_not_fail_the_save(
        tmp_path, monkeypatch):
    """`verify` reads every marker it meets, and on Windows an unlink while
    a reader has the file open raises PermissionError. `save` removed it
    once, unguarded, so a `make verify` at that instant made a whole entry a
    failed save. Here the marker is opened as the record lands and let go
    only in the wait between attempts; on POSIX the first attempt goes
    through regardless, and on Windows it is the real refusal."""
    import os
    import warnings

    held, waited = [], []
    real_sync_dir = library._sync_dir

    def sync_dir(path):
        marker = Path(path) / library.INCOMPLETE
        if not held and (Path(path) / "scan.json").exists() and marker.exists():
            held.append(open(marker, encoding="utf-8"))
        return real_sync_dir(path)

    def let_go(seconds):
        waited.append(seconds)
        for fh in held:
            fh.close()

    try:
        with monkeypatch.context() as reading, warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            reading.setattr(library, "_sync_dir", sync_dir)
            reading.setattr(library.time, "sleep", let_go)
            path, _, _ = make_entry(tmp_path)
    finally:
        for fh in held:
            fh.close()
    assert held, "the marker was never held"
    assert not (path / library.INCOMPLETE).exists()
    assert library.verify(tmp_path) == []
    if os.name == "nt":
        assert waited, "the held marker did not refuse its removal"


def test_a_marker_that_will_not_go_leaves_a_filed_entry_said_to_be(
        tmp_path, monkeypatch):
    """Refused past every retry, the removal costs the marker and nothing
    else: the record is durable, so save warns and hands back the entry,
    `entries()` lists it, and `verify` names the marker and finds the files
    whole."""
    real = Path.unlink
    tries = []

    def unlink(self, *a, **k):
        if self.name == library.INCOMPLETE:
            tries.append(self)
            raise PermissionError(32, "in use by another process", str(self))
        return real(self, *a, **k)

    with monkeypatch.context() as holding:
        holding.setattr(Path, "unlink", unlink)
        holding.setattr(library, "REPLACE_RETRY_S", (0.0, 0.0))
        with pytest.warns(RuntimeWarning, match="marker could not be removed"):
            path, _, _ = make_entry(tmp_path)
    assert len(tries) == 3
    assert (path / "scan.json").exists() and (path / library.INCOMPLETE).exists()
    assert [r["_dir"] for r in library.entries(tmp_path)] == [path.name]
    [line] = library.verify(tmp_path)
    assert path.name in line and "did not finish" in line
    assert "only removing this marker was cut short or refused" in line


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
    # Whatever step it stopped at, the marker says which pass it was.
    said = library.unfinished(cut[0])
    assert said["id"] == cut[0].name and said["resolution_dpi"] == 1800


def test_an_entry_cut_short_says_which_pass_it_was(tmp_path, monkeypatch):
    """The marker was a fixed sentence and the record is written last, so a
    kill part way left raw bytes nothing could name: which roll and frame,
    and the layout a decode of them needs."""
    member = {"roll": "2026-10-09-120000", "number": 7, "kind": "frame",
              "folder": "rolls/2026-10-09-120000"}
    with monkeypatch.context() as failing:
        _fails_at(failing, "scan.tif")
        with pytest.raises(OSError):
            library.save(
                np.zeros((8, 16, 3), np.uint16),
                {"resolution_dpi": 1800, "channels": 3,
                 "channel_order": ["R", "G", "B"], "roll_membership": member},
                root=tmp_path, film=FilmNotes(stock="Kodak Gold 200", frame="7"),
                tags=["roll"], raw=b"\x01" * 816,
                raw_layout={"format": "index", "width": 16, "lines": 8,
                            "channels": 3, "bytes_per_line": 32})
    [cut] = [p for p in tmp_path.iterdir() if p.is_dir()]
    said = library.unfinished(cut)
    assert said["incomplete"] == library.UNFINISHED
    assert said["id"] == cut.name and said["started_utc"]
    assert said["channels"] == 3 and said["channel_order"] == ["R", "G", "B"]
    assert said["image"] == {"shape": [8, 16, 3], "dtype": "uint16"}
    assert said["raw"]["file"] == library.RAW_FILE and said["raw"]["bytes"] == 816
    assert said["raw"]["layout"]["lines"] == 8
    assert said["roll_membership"] == member
    assert said["film"]["stock"] == "Kodak Gold 200" and said["tags"] == ["roll"]
    [line] = [p for p in library.verify(tmp_path) if cut.name in p]
    for words in ("did not finish", "1800 dpi", "3 channels",
                  "roll 2026-10-09-120000 frame 7", "Kodak Gold 200 frame 7",
                  "tagged roll", "raw.bin.gz expected, 816 bytes",
                  "16 wide by 8 lines", "holds raw.bin.gz"):
        assert words in line, (words, line)


@pytest.mark.parametrize("text, says", [
    ("this entry was being written and did not finish\n",
     "from before markers described the pass"),
    ("", "from before markers described the pass"),
    ('{"incomplete": "this entry was being wri', "cannot be read"),
], ids=["sentence", "empty", "cut short"])
def test_a_marker_that_describes_nothing_still_marks_the_entry(tmp_path, text,
                                                               says):
    """An entry left by code from before the marker described its pass -- or
    a marker a kill cut short -- is still unfinished, and said to be."""
    make_entry(tmp_path)
    cut = tmp_path / "20260924T220001Z_unknown-film_300dpi"
    cut.mkdir()
    (cut / library.INCOMPLETE).write_text(text, encoding="utf-8")
    (cut / "raw.bin.gz").write_bytes(b"part")
    said = library.unfinished(cut)
    assert said is not None and "id" not in said
    [line] = [p for p in library.verify(tmp_path) if cut.name in p]
    assert "did not finish" in line and says in line and "holds raw.bin.gz" in line


@pytest.mark.parametrize("form", ["description", "sentence"])
def test_every_reader_of_the_marker_knows_both_forms(tmp_path, form,
                                                     monkeypatch):
    """The marker became a JSON description; every reader asks only whether
    it is there, and must go on leaving out an entry marked either way."""
    from conftest import load_tool

    # Imported, not `load_tool`ed: its dataclasses need their module
    # registered. From the repo root, which nothing else here puts on the path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent))
    from tools import film_edge_study as edge_study

    headroom = load_tool("exposure_headroom")
    path, _, _ = make_entry(tmp_path)
    assert headroom.decode(path) is not None
    assert edge_study._record(path) is not None
    if form == "description":
        library._mark_unfinished(path, lambda: {"id": path.name})
    else:
        (path / library.INCOMPLETE).write_text(library.UNFINISHED + "\n",
                                               encoding="utf-8")
    assert headroom.decode(path) is None
    assert edge_study._record(path) is None
    assert library.unfinished(path) is not None
    assert any(path.name in p and "did not finish" in p
               for p in library.verify(tmp_path))


def test_a_marker_whose_description_will_not_build_is_written_anyway(tmp_path):
    """Its presence is the point; a description that raises costs only
    itself."""
    def broken():
        raise TypeError("nothing to say")

    tmp_path.joinpath("e").mkdir()
    library._mark_unfinished(tmp_path / "e", broken)
    assert library.unfinished(tmp_path / "e") == {
        "incomplete": library.UNFINISHED, "id": "e"}


def test_a_finished_entry_is_not_unfinished_and_a_record_beside_is_said(
        tmp_path):
    """Killed after the record's rename and before the marker went: the
    record is there, and only the marker's removal was cut short."""
    path, _, _ = make_entry(tmp_path)
    assert library.unfinished(path) is None
    library._mark_unfinished(path, lambda: {"id": path.name})
    [line] = [p for p in library.verify(tmp_path) if "did not finish" in p]
    assert "only removing this marker was cut short" in line


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
    """`compact` killed between swapping the TIFFs in and writing the record.

    The record it writes before the swaps, naming the checksums they will
    bring, goes through; the one after them is where the kill lands."""
    real = library._write_atomic

    def killed(target, text, *a, **k):
        if Path(target).name == "scan.json" \
                and json.loads(text).get(library.COMPACTING):
            return real(target, text, *a, **k)
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


def test_a_compact_cut_short_is_not_reported_as_damage(tmp_path, monkeypatch):
    """A false alarm in the one check that exists to catch real damage.
    compact swaps the recompressed TIFFs in before it writes the record, so
    a kill between them left scan.json holding the old checksums and verify
    calling intact pixels damaged. It is unfinished, and said so -- with the
    command that finishes it -- but it is not damage."""
    path, _, _ = _plain_entry_with_bytes(tmp_path)
    before = (path / "scan.tif").read_bytes()
    _compact_cut_short_after_the_swaps(path, monkeypatch)
    if (path / "scan.tif").read_bytes() == before:
        # The bare-install writer (no tifffile) has no compression to apply,
        # so the swap changes no byte and there is no checksum to go stale.
        pytest.skip("this TIFF writer compresses nothing")
    problems = _real_problems(tmp_path)
    assert not any("checksum" in p for p in problems), problems
    assert len(problems) == 1 and "compaction stopped part way" in problems[0]
    assert library.compact(path) is True
    assert _real_problems(tmp_path) == []


def test_a_damaged_tiff_beside_plain_bytes_is_still_damage(tmp_path):
    """The proof is the decode, not the plain bytes being there: a scan.tif
    that no longer holds the decode, or a prescan changed while scan.tif was
    not, is damage whatever else the entry holds."""
    path, image, _ = _plain_entry_with_bytes(tmp_path)
    tiff.write(str(path / "scan.tif"), image + 1)
    tiff.write(str(path / "prescan.tif"), np.full((4, 6, 3), 9, np.uint8))
    problems = _real_problems(tmp_path)
    assert any("scan.tif does not match its checksum" in p for p in problems)
    assert any("prescan.tif does not match its checksum" in p for p in problems)
    assert not any("stopped part way" in p for p in problems)


@pytest.mark.parametrize("beside", [library.RAW_PLAIN, library.MIGRATE_KEPT])
def test_a_scan_tif_that_will_not_read_is_reported_not_raised(tmp_path, beside):
    """Asking whether a TIFF holds the decode must not be where real damage
    raises. A compressed TIFF cut short raises zlib.error, which is neither
    an OSError nor a ValueError, and verify -- which has no guard around
    `damage` -- died with a traceback where it had said "does not match"."""
    path, image, _ = _plain_entry_with_bytes(tmp_path)
    if beside == library.MIGRATE_KEPT:
        # A migrate-raw from before the kept file was checksummed: raw bytes
        # gzipped, the kept file there and named by nothing.
        assert library.compact(path) is True
        tiff.write(str(path / beside), image)
    tiff.write(str(path / "scan.tif"), image)
    data = (path / "scan.tif").read_bytes()
    (path / "scan.tif").write_bytes(data[: len(data) // 2])
    problems = _real_problems(tmp_path)
    assert any("scan.tif does not match its checksum" in p for p in problems)
    if beside == library.RAW_PLAIN:
        with pytest.raises(OSError, match="scan.tif does not match"):
            library.compact(path)


def test_a_plain_entry_without_bytes_cut_short_is_not_damage(tmp_path,
                                                            monkeypatch):
    """compact now deflates an entry with no raw bytes too, and swapped its
    TIFFs in before writing the record. Killed between the two, both failed
    their checksums, there were no bytes to prove them by, and a second
    compact found nothing uncompressed and gave up: intact pixels reported
    damaged for good."""
    if not tiff._has_tifffile():
        pytest.skip("only tifffile compresses; there is nothing to deflate")
    _, image = index_stream(16, 8, 3, seed=5)
    prescan = np.full((4, 6, 3), 7, np.uint8)
    path = library.save(image, {"resolution_dpi": 300, "channels": 3},
                        root=tmp_path, compress=False, prescan=prescan)
    _compact_cut_short_after_the_swaps(path, monkeypatch)
    assert not library._uncompressed_tiffs(path), "the swaps did not happen"
    problems = [p for p in _real_problems(tmp_path) if "no raw bytes" not in p]
    assert not any("checksum" in p for p in problems), problems
    assert len(problems) == 1 and "compaction stopped part way" in problems[0]
    assert library.compact(path) is True
    assert [p for p in _real_problems(tmp_path) if "no raw bytes" not in p] == []
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert np.array_equal(tiff.read(str(path / "prescan.tif")), prescan)


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


def test_a_name_the_filesystem_decomposed_is_still_its_own(tmp_path):
    """HFS+ returns names decomposed (NFD) where the record keeps the id as
    typed (NFC); every entry with an umlaut in it failed the id check."""
    import unicodedata

    path, _, _ = make_entry(tmp_path)
    composed = path.name + "-s\u00fcd"
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["id"] = composed
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    path.rename(path.with_name(unicodedata.normalize("NFD", composed)))
    assert not any("records itself" in p for p in library.verify(tmp_path))


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


def deflate_start(data: bytes) -> int:
    """Where a gzip member's deflate stream begins: past its header, which
    `gzip.open` writes with the file's name in it."""
    flags, at = data[3], 10
    if flags & 0x04:                                     # FEXTRA
        at += 2 + int.from_bytes(data[at:at + 2], "little")
    for flag in (0x08, 0x10):                            # FNAME, FCOMMENT
        if flags & flag:
            at = data.index(b"\0", at) + 1
    return at + (2 if flags & 0x02 else 0)               # FHCRC


def test_a_deflate_stream_damaged_inside_is_damage_not_a_traceback(tmp_path):
    """A flip inside the deflate stream, not its header or its checksum, can
    raise zlib.error -- not an OSError -- and it escaped `read_raw` into
    `reconstruct`, `verify` and `migrate_direction`, which stopped there."""
    import zlib

    path, _, _ = make_entry(tmp_path)
    data = bytearray((path / library.RAW_FILE).read_bytes())
    # The first block's type set to 11, which deflate reserves: the header
    # is intact, the stream itself is not.
    data[deflate_start(bytes(data))] |= 0x06
    (path / library.RAW_FILE).write_bytes(bytes(data))
    with pytest.raises(zlib.error):
        with gzip.open(path / library.RAW_FILE, "rb") as fh:
            fh.read()

    assert library.read_raw(path) is None
    assert library.decode_raw(path) is None
    _image, verdict = library.reconstruct(path)
    assert verdict.kind == library.DAMAGED, verdict
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert library.damage(path, record) == [
        "raw bytes do not match their checksum"]
    assert any(path.name in p and "raw bytes" in p
               for p in library.verify(tmp_path))
    # Not "no raw bytes", recorded as an unknown direction for good: named,
    # and left as it is.
    record["scan"]["read_direction"] = None
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(OSError, match="cannot be read"):
        library.migrate_direction(path, write=True)
    assert json.loads((path / "scan.json").read_text(
        encoding="utf-8"))["scan"]["read_direction"] is None


def test_a_record_that_is_not_utf8_is_named_not_raised(tmp_path):
    """A scan.json saved by hand in cp1252 raised UnicodeDecodeError -- a
    ValueError, not a JSONDecodeError -- out of `entries()`, and with it
    `reindex`, every later save's index and `make verify`."""
    odd = tmp_path / "20260101T000000Z_odd_300dpi"
    odd.mkdir()
    (odd / "scan.json").write_bytes(
        json.dumps({"id": odd.name, "film": {"notes": "Südtirol"}},
                   ensure_ascii=False).encode("cp1252"))
    path, _, _ = make_entry(tmp_path)
    assert [r["_dir"] for r in library.entries(tmp_path)] == [path.name]
    library.reindex(tmp_path)
    problems = library.verify(tmp_path)
    assert any(odd.name in p and "scan.json cannot be read (not UTF-8" in p
               for p in problems), problems
    _image, verdict = library.reconstruct(odd)
    assert verdict.kind == library.DAMAGED, verdict


# --- a pass filed tagged failed, and reconstruct -------------------------------


#: What `DirectScanner._keep_failed_pass` records of a pass whose bytes did
#: not decode, as `extra.failed.pixels`.
UNDECODED = ("the lines as they arrived, one row each, tags included -- not "
             "a picture")


def failed_entry(root, *, decodes=False, pixels=UNDECODED, tags=("failed",),
                 failed=True):
    """A pass filed as `_keep_failed_pass` files one: tagged failed, its bytes
    kept, and its lines as scan.tif where they did not decode. ``decodes``
    stands in for a decode fixed since: bytes today's code can read."""
    stream, image = index_stream(16, 8, 3, seed=11)
    if not decodes:
        stream = b"\x00" * len(stream)                   # no tag it can place
    stride = 32 + INDEX_HEADER
    lines = np.frombuffer(stream, np.uint8).reshape(-1, stride, 1)
    stored = image if pixels == "decoded" else lines
    meta = {"resolution_dpi": 300, "channels": 3, "width": 16,
            "height": stored.shape[0], "depth": 16}
    if failed:
        meta["failed"] = {
            "stage": "after the decode" if pixels == "decoded" else "in the decode",
            "error": "ScanReadError: no recognisable channel tags",
            "pixels": pixels}
    path = library.save(
        stored, meta, root=root, raw=stream, tags=list(tags),
        raw_layout={"format": "index", "bytes_per_line": 32,
                    "line_stride": stride, "index_header": INDEX_HEADER,
                    "width": 16, "lines": 8, "channels": 3})
    return path, image


def test_a_pass_filed_because_it_would_not_decode_is_known_not_failed(tmp_path):
    path, _ = failed_entry(tmp_path)
    image, verdict = library.reconstruct(path)
    assert image is None
    assert verdict.kind == library.KNOWN_FAILED, verdict
    assert "in the decode" in verdict and "still cannot" in verdict


def test_a_failed_pass_today_s_decode_can_read_is_said_as_news(tmp_path):
    path, image = failed_entry(tmp_path, decodes=True)
    rebuilt, verdict = library.reconstruct(path)
    assert verdict.kind == library.DECODES_NOW, verdict
    assert np.array_equal(rebuilt, image)
    assert "CHANGED" not in verdict


@pytest.mark.parametrize("tags, failed", [((), True), (("failed",), False)],
                         ids=["no tag", "no extra.failed"])
def test_the_exemption_wants_both_the_tag_and_the_record(tmp_path, tags, failed):
    """Never known failed on one word. With the tag alone, nothing in the
    record says why scan.tif is lines and not a picture, so `save` held the
    bytes against it and recorded that they disagree: `verify` names that,
    which is where such an entry stays a problem."""
    if failed:
        path, _ = failed_entry(tmp_path, tags=tags, failed=failed)
    else:
        with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
            path, _ = failed_entry(tmp_path, tags=tags, failed=failed)
    _image, verdict = library.reconstruct(path)
    if failed:
        assert verdict.kind == library.FAILED, verdict
    else:
        assert verdict.kind == library.DISAGREED, verdict
        assert any(path.name in p and "cannot be this picture's" in p
                   for p in library.verify(tmp_path))


def test_a_failed_pass_that_did_decode_is_still_held_to_its_decode(tmp_path):
    """Failed after its decode: scan.tif is a picture, and a decode that no
    longer makes it -- or no longer runs on its bytes -- is a regression."""
    path, image = failed_entry(tmp_path, decodes=True, pixels="decoded")
    assert library.reconstruct(path)[1].kind == library.IDENTICAL
    moved = image.copy()
    moved[0, 0, 0] ^= 0xFFFF
    tiff.write(str(path / "scan.tif"), moved, resolution=300)
    assert library.reconstruct(path)[1].kind == library.CHANGED

    gone, _ = failed_entry(tmp_path / "b", decodes=False, pixels="decoded")
    assert library.reconstruct(gone)[1].kind == library.FAILED


def test_a_failed_pass_whose_bytes_are_damaged_is_damaged(tmp_path):
    path, _ = failed_entry(tmp_path)
    data = (path / library.RAW_FILE).read_bytes()
    (path / library.RAW_FILE).write_bytes(data[: len(data) // 2])
    assert library.reconstruct(path)[1].kind == library.DAMAGED


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


def test_an_entry_filed_plain_without_raw_bytes_is_still_compacted(tmp_path):
    """The shape guard can refuse a pass's bytes, and the demo has none for a
    finished source: such an entry is filed plain all the same, and compact
    returned before its TIFFs because there was no raw.bin to gzip."""
    if not tiff._has_tifffile():
        pytest.skip("only tifffile compresses; there is nothing to deflate")
    _, image = index_stream(16, 8, 3, seed=5)
    path = library.save(image, {"resolution_dpi": 300, "channels": 3},
                        root=tmp_path, compress=False,
                        prescan=np.full((4, 6, 3), 7, np.uint8))
    assert library._uncompressed_tiffs(path)
    assert library.compact(path) is True
    assert not library._uncompressed_tiffs(path)
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)
    assert np.array_equal(tiff.read(str(path / "prescan.tif")),
                          np.full((4, 6, 3), 7, np.uint8))
    assert [p for p in library.verify(tmp_path)
            if "never be corrected" not in p and "no raw bytes" not in p] == []
    assert library.compact(path) is False, "compacted twice"


def test_a_compaction_that_fails_leaves_no_partial_file(tmp_path, monkeypatch):
    """A full disk half-way through the gzip left `.raw.bin.gz.part` behind
    -- a hundred megabytes at 3600 dpi -- and nothing ever removed it."""
    path, _, stream = _plain_entry_with_bytes(tmp_path)
    real = library.gzip.open

    def full(target, *a, **k):
        fh = real(target, *a, **k)
        fh.write(b"half")
        fh.close()
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(library.gzip, "open", full)
    with pytest.raises(OSError):
        library.compact(path)
    assert not list(path.glob(".*.part"))
    assert library.read_raw(path) == stream


def test_a_compaction_the_disk_cannot_finish_touches_nothing(tmp_path,
                                                            monkeypatch):
    """Everything is written beside before the plain files go, so for a
    while the entry holds both: a full disk showed itself part way through,
    as a gzip cut short. It is refused before anything is written now, with
    `storage.SPACE_RESERVE` kept free, and finished by a later run."""
    import collections
    import shutil

    from rps7200 import storage

    path, _, stream = _plain_entry_with_bytes(tmp_path)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    need = sum((path / name).stat().st_size
               for name in (library.RAW_PLAIN, "scan.tif", "prescan.tif"))
    monkeypatch.setattr(storage, "SPACE_RESERVE", 2 * 10**9)
    usage = collections.namedtuple("usage", "total used free")
    room = {"free": need + 2 * 10**9 - 1}
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda _path: usage(10**13, 0, room["free"]))
    with pytest.raises(OSError) as refused:
        library.compact(path)
    said = str(refused.value)
    assert said.startswith(f"{path.name}: 2.0 GB free")
    assert "it stays plain and complete" in said
    assert "tools/library.py compact --write" in said
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before, \
        "something was written or removed before the refusal"
    assert library.read_raw(path) == stream
    assert [p for p in library.verify(tmp_path)
            if "never be corrected" not in p] == []

    room["free"] = need + 2 * 10**9
    assert library.compact(path) is True
    assert (path / library.RAW_FILE).exists()
    assert not (path / library.RAW_PLAIN).exists()
    assert library.read_raw(path) == stream


def test_a_library_on_a_drive_that_has_gone_is_not_made_again(tmp_path):
    """`_reserve` made a missing library with its parents, so one on a stick
    that was pulled out was made again on C:, and filled there. Its own
    folder may be made; anything above it is refused, and nothing made."""
    from rps7200 import storage

    image = np.zeros((4, 6, 3), np.uint16)
    with pytest.raises(storage.DriveMissing, match="is the drive mounted"):
        library.save(image, {"resolution_dpi": 300},
                     root=tmp_path / "gone" / "library")
    assert not (tmp_path / "gone").exists()
    filed = library.save(image, {"resolution_dpi": 300},
                         root=tmp_path / "library")
    assert filed.parent == tmp_path / "library"


def test_a_pass_the_library_refused_is_still_kept_elsewhere(tmp_path,
                                                            monkeypatch):
    """`keep_unfiled` tries an unfiled folder beside each delivered copy,
    then the system's temporary one; both are folders below one that is
    there, so the stricter rule costs neither."""
    from rps7200 import session, storage

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    image = np.zeros((4, 6, 3), np.uint16)
    with pytest.raises(storage.DriveMissing):
        library.save(image, {"resolution_dpi": 300},
                     root=tmp_path / "gone" / "library")
    beside = tmp_path / "out"
    beside.mkdir()
    kept, refused = session.keep_unfiled(image, {"resolution_dpi": 300},
                                         near=[beside])
    assert refused == [] and kept.parent == beside / session.UNFILED
    kept, refused = session.keep_unfiled(
        image, {"resolution_dpi": 300}, near=[tmp_path / "gone" / "out"])
    assert len(refused) == 1 and "is the drive mounted" in refused[0]
    assert kept.parent == temp / session.UNFILED_TEMP


def test_a_tiff_rewrite_that_fails_leaves_no_partial_file(tmp_path, monkeypatch):
    path, image, _ = make_entry(tmp_path)
    before = (path / "scan.tif").read_bytes()

    def full(target, *a, **k):
        Path(target).write_bytes(b"half a tiff")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(library.tiff, "write", full)
    with pytest.raises(OSError):
        library._replace_tiff(path / "scan.tif", image)
    assert not list(path.glob(".*.part"))
    assert (path / "scan.tif").read_bytes() == before


def test_a_tiff_rewrite_is_on_the_disk_before_it_has_its_name(tmp_path,
                                                             monkeypatch):
    """Renamed in unsynced, a power cut could leave zeros under the name --
    for a prescan, with no bytes of its own to make it again from."""
    path, image, _ = make_entry(tmp_path)
    order = []
    real_sync, real_replace = library._sync, library._replace
    monkeypatch.setattr(library, "_sync", lambda p: (
        order.append(("sync", Path(p).name)), real_sync(p))[-1])
    monkeypatch.setattr(library, "_replace", lambda t, p: (
        order.append(("replace", Path(t).name, Path(p).name)),
        real_replace(t, p))[-1])
    library._replace_tiff(path / "scan.tif", image)
    assert order == [("sync", ".scan.tif.part"),
                     ("replace", ".scan.tif.part", "scan.tif")]
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)


def test_a_cleanup_that_is_refused_never_hides_the_failure(tmp_path,
                                                         monkeypatch):
    """Each rewrite removes its temporary in a `finally`. Where Windows
    refuses that unlink too, its PermissionError replaced the error that said
    what went wrong -- a full disk became a complaint about a `.part`."""
    path, image, _ = _plain_entry_with_bytes(tmp_path)

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    def held(self, *a, **k):
        raise PermissionError(13, "held by another process")

    monkeypatch.setattr(library, "_replace", full)
    monkeypatch.setattr(Path, "unlink", held)
    with pytest.raises(OSError, match="No space left"):
        library._replace_tiff(path / "scan.tif", image)
    with pytest.raises(OSError, match="No space left"):
        library._write_atomic(path / "scan.json", "{}")
    with pytest.raises(OSError, match="No space left"):
        library.compact(path)


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


def archived(root, ppl=16, seed=0, legacy=False):
    """A calibration archived by the driver's own writer, in the library at
    ``root`` -- or, ``legacy``, where it was archived until 2026-10-09:
    ``root/<UTC time>/``, beside the cache."""
    import shutil
    import tempfile

    from conftest import FakeTransport

    from rps7200.direct import DirectScanner
    from rps7200.shading import calculate_shading

    data = calibration_lines(ppl, seed)
    s = DirectScanner(transport=FakeTransport())
    s.verbose = False
    reference = calculate_shading(data, ppl)
    result = {"data": data, "reference": reference, "ccd_mask": bytes(ppl),
              "pixels_per_line": ppl, "bytes_per_line": ppl * 2}
    if not legacy:
        return s.archive_calibration(result, root), reference
    shelf = Path(tempfile.mkdtemp(prefix="shelf-", dir=Path(root).parent
                                  if Path(root).parent.exists() else None))
    old = Path(root) / f"20260927T0000{seed:02d}Z"
    old.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s.archive_calibration(result, shelf)), str(old))
    shutil.rmtree(shelf)
    return old, reference


def test_an_archived_calibration_reduces_again_to_its_reference(tmp_path):
    """Nothing read the archive back: it was kept for a recomputation no
    code could make."""
    folder, reference = archived(tmp_path / "library")
    assert library.same_reference(library.rebuild_reference(folder), reference)
    _ref, verdict = library.recalibrate(folder)
    assert verdict.kind == library.IDENTICAL, verdict


def test_a_changed_reduction_is_reported(tmp_path, monkeypatch):
    from rps7200 import shading

    folder, _ = archived(tmp_path / "library")
    monkeypatch.setattr(library, "calculate_shading",
                        lambda data, ppl: shading.calculate_shading(
                            data, ppl, split_ratio=1e9))       # one phase only
    _ref, verdict = library.recalibrate(folder)
    assert verdict.kind == library.CHANGED, verdict


def test_damaged_calibration_bytes_are_refused_not_reduced(tmp_path):
    import pytest

    folder, _ = archived(tmp_path / "library")
    data = bytearray((folder / "data.bin").read_bytes())
    data[5] ^= 0xFF
    (folder / "data.bin").write_bytes(bytes(data))
    with pytest.raises(ValueError, match="checksum"):
        library.rebuild_reference(folder)
    assert library.recalibrate(folder)[1].kind == library.DAMAGED


# --- where the calibration is kept: in the library, by its lines -------------


def test_a_calibration_is_kept_in_the_library_by_the_hash_of_its_lines(tmp_path):
    """Beside the cache, outside the library, a library copied or moved went
    without the lines behind every reference in it (DBG-6, TP-07)."""
    import hashlib

    root = tmp_path / "library"
    folder, _ = archived(root)
    data = (folder / "data.bin").read_bytes()
    assert folder == root / library.CALIBRATIONS / hashlib.sha256(data).hexdigest()
    record = json.loads((folder / library.CALIBRATION_RECORD).read_text(
        encoding="utf-8"))
    assert record["sha256"] == folder.name and record["archived_utc"]
    assert not (folder / library.INCOMPLETE).exists()


def test_the_same_lines_kept_again_rewrite_nothing(tmp_path):
    """Content-addressed: archived twice, one folder, and the first record
    stands -- marked here so a rewrite would show."""
    root = tmp_path / "library"
    folder, _ = archived(root)
    said = folder / library.CALIBRATION_RECORD
    record = json.loads(said.read_text(encoding="utf-8"))
    said.write_text(json.dumps(dict(record, archived_utc="the first")),
                    encoding="utf-8")
    again, _ = archived(root)
    assert again == folder
    assert json.loads(said.read_text(encoding="utf-8"))["archived_utc"] == "the first"
    assert [p.name for p in (root / library.CALIBRATIONS).iterdir()] == [folder.name]


def test_a_kept_calibration_cut_short_is_written_again_whole(tmp_path, monkeypatch):
    """A kill or a full disk left the folder marked INCOMPLETE; the same
    lines kept again finish it rather than being taken for already kept."""
    import pytest

    def full(path, text):
        raise OSError(28, "No space left on device")

    root = tmp_path / "library"
    with monkeypatch.context() as disk:
        disk.setattr(library, "_write_atomic", full)
        with pytest.raises(OSError):
            archived(root)
    [cut] = (root / library.CALIBRATIONS).iterdir()
    assert (cut / library.INCOMPLETE).exists()
    assert any("did not finish" in p for p in library.verify(root))
    folder, _ = archived(root)
    assert folder == cut
    assert not (folder / library.INCOMPLETE).exists()
    assert library.archive_damage(folder) is None


def test_a_whole_calibration_whose_marker_stayed_says_to_delete_the_marker(
        tmp_path, monkeypatch):
    """`archive_calibration` keeps a whole archive whose marker a reader
    held, and says `verify` names it. It named it "did not finish", as it
    does one cut short -- with no fix, over the only copy of the lines."""
    def held(path):
        raise PermissionError("held by a reader")

    root = tmp_path / "library"
    with monkeypatch.context() as reader:
        reader.setattr(library, "_unmark", held)
        folder, _ = archived(root)
    assert (folder / library.INCOMPLETE).exists()
    [line] = [p for p in library.verify(root) if folder.name in p]
    assert "did not finish" not in line
    assert line.endswith("its record and lines are whole, so only removing "
                         "its INCOMPLETE marker was cut short or refused -- "
                         "delete that file")
    (folder / library.INCOMPLETE).unlink()
    assert not any(folder.name in p for p in library.verify(root))


def test_a_calibration_kept_again_and_cut_short_did_not_finish(tmp_path):
    """Kept again, a folder's marker is written before its files: a record
    older than the marker is the last keep's, and lines the same as that
    one's say nothing of the files being written now."""
    import os

    root = tmp_path / "library"
    folder, _ = archived(root)
    record = (folder / library.CALIBRATION_RECORD).stat()
    (folder / library.INCOMPLETE).write_text("{}", encoding="utf-8")
    os.utime(folder / library.INCOMPLETE,
             ns=(record.st_atime_ns, record.st_mtime_ns + 10**9))
    [line] = [p for p in library.verify(root) if folder.name in p]
    assert line.endswith("was being written and did not finish")
    # And one whose lines are not whole did not finish, however new.
    os.utime(folder / library.INCOMPLETE,
             ns=(record.st_atime_ns, record.st_mtime_ns - 10**9))
    data = (folder / "data.bin").read_bytes()
    (folder / "data.bin").write_bytes(data[:len(data) // 2])
    [line] = [p for p in library.verify(root) if folder.name in p]
    assert line.endswith("was being written and did not finish")


def _entry_corrected_by(tmp_path, reference, origin, root=None, **kw):
    stream, image = index_stream(16, 8, 3)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    return library.save(image, {"resolution_dpi": 300, "channels": 3,
                                "shading_origin": origin},
                         root=root or tmp_path / "library", reference=reference,
                         ccd_mask=bytes(16), raw=stream, raw_layout=layout,
                         **kw)


def _named(folder):
    """What an entry corrected by the archive in ``folder`` records."""
    return {"action": "calibrated", "archive": str(folder),
            "archive_sha256": json.loads((folder / library.CALIBRATION_RECORD)
                                         .read_text(encoding="utf-8"))["sha256"]}


def test_an_entry_finds_the_calibration_behind_its_reference(tmp_path, monkeypatch):
    """By the path its record names -- and, for a reference loaded from the
    cache, which names none, by content. These are archives kept as they
    were until 2026-10-09, beside the cache."""
    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("calibration"), legacy=True)
    named = _entry_corrected_by(tmp_path, reference,
                                {"action": "calibrated", "archive": str(folder)})
    loaded = _entry_corrected_by(tmp_path, reference,
                                 {"action": "loaded", "path": "calibration/shading.npz"})
    assert library.calibration_of(named).resolve() == folder.resolve()
    assert library.calibration_of(loaded).resolve() == folder.resolve()
    _other, elsewhere = archived(tmp_path / "other", seed=3)
    stranger = _entry_corrected_by(tmp_path, elsewhere, {"action": "loaded"})
    assert library.calibration_of(stranger) is None


def test_an_entry_finds_its_calibration_by_id_wherever_the_library_went(
        tmp_path, monkeypatch):
    """The path an entry records is relative to wherever the scan ran from;
    its id finds the archive in the entry's own library, copied anywhere."""
    import shutil

    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("library"))
    entry = _entry_corrected_by(tmp_path, reference, _named(folder))
    loaded = _entry_corrected_by(tmp_path, reference, {"action": "loaded"})
    assert library.calibration_of(entry).resolve() == folder.resolve()
    shutil.copytree(tmp_path / "library", tmp_path / "far" / "away")
    shutil.rmtree(tmp_path / "library")
    moved = tmp_path / "far" / "away"
    assert library.calibration_of(moved / entry.name) == (
        moved / library.CALIBRATIONS / folder.name)
    # And a reference loaded from a cache that named nothing, by content.
    assert library.calibration_of(moved / loaded.name) == (
        moved / library.CALIBRATIONS / folder.name)
    assert library.verify(moved) == []


def test_verify_checks_the_calibrations_the_library_keeps(tmp_path):
    """By the id the entries name, and every one on the shelf: lines that
    are not their own any more, or not the ones their folder is named for."""
    import hashlib

    root = tmp_path / "library"
    folder, reference = archived(root)
    _entry_corrected_by(tmp_path, reference, _named(folder))
    assert library.verify(root) == [], "the shelf is not an entry"
    assert len(library.entries(root)) == 1
    library.reindex(root)
    assert len(json.loads((root / library.INDEX).read_text(encoding="utf-8"))) == 1

    data = bytearray((folder / "data.bin").read_bytes())
    data[5] ^= 0xFF
    (folder / "data.bin").write_bytes(bytes(data))
    problems = library.verify(root)
    assert any("is damaged" in p and folder.name in p for p in problems), problems

    other, _ = archived(root, seed=4)
    (other / "data.bin").write_bytes(bytes(data))
    record = json.loads((other / library.CALIBRATION_RECORD).read_text(
        encoding="utf-8"))
    record["sha256"] = hashlib.sha256(bytes(data)).hexdigest()
    (other / library.CALIBRATION_RECORD).write_text(json.dumps(record),
                                                    encoding="utf-8")
    assert any(f"{library.CALIBRATIONS}/{other.name}" in p
               and "not the calibration this folder is named for" in p
               for p in library.verify(root))


def test_verify_reports_a_calibration_its_entries_name_and_lost(tmp_path, monkeypatch):
    import shutil

    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("library"))
    _entry_corrected_by(tmp_path, reference, _named(folder))
    assert library.verify(tmp_path / "library") == []
    shutil.rmtree(folder)
    problems = library.verify(tmp_path / "library")
    assert any("is missing" in p and folder.name in p for p in problems), problems


def test_a_calibration_still_outside_the_library_is_said_once_not_as_damage(
        tmp_path, monkeypatch):
    """Archived beside the cache before 2026-10-09, it is intact and only
    elsewhere: one line, with the command that copies it in."""
    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("calibration"), legacy=True)
    for _ in range(3):
        _entry_corrected_by(tmp_path, reference, _named(folder))
    # Not a problem: among them, it failed every library filed before
    # 2026-10-09 with nothing damaged.
    notes: list[str] = []
    assert library.verify(tmp_path / "library", notes=notes) == []
    assert library.verify(tmp_path / "library") == []
    assert len(notes) == 1, notes
    assert "outside the library" in notes[0]
    assert "named by 3 entries" in notes[0]
    assert "migrate-calibrations --write" in notes[0]
    assert "damaged" not in notes[0].replace("not damage", "")
    assert "missing" not in notes[0]


def test_a_folder_outside_is_named_once_however_entries_spelled_it(
        tmp_path, monkeypatch):
    """Some entries recorded `calibration` and some its absolute path, and
    the note on the real library read "... in C:\...\calibration,
    calibration": the same folder twice."""
    monkeypatch.chdir(tmp_path)
    first, one = archived(Path("calibration"), legacy=True)
    second, two = archived(Path("calibration"), seed=1, legacy=True)
    assert first != second
    _entry_corrected_by(tmp_path, one, _named(first))
    _entry_corrected_by(tmp_path, two, _named(second.resolve()))
    notes: list[str] = []
    assert library.verify(tmp_path / "library", notes=notes) == []
    [note] = notes
    assert "2 calibrations named by 2 entries" in note, note
    where = note.split(" in ", 1)[1].split(" -- ")[0]
    assert where == str((tmp_path / "calibration").resolve()), note


def test_a_path_only_entry_is_not_called_missing_once_its_lines_are_kept(
        tmp_path, monkeypatch):
    """Entries filed 2026-09-24 to 28 name their archive by a path and no
    id. Copied in and the original deleted -- which the TODO all but
    invited -- verify called it missing while `calibration_of` found it,
    and the same from any other working directory. A path-only archive
    that is truly gone is still missing."""
    import hashlib
    import shutil

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "library"
    folder, reference = archived(Path("calibration"), seed=1, legacy=True)
    entry = _entry_corrected_by(tmp_path, reference,
                                {"action": "calibrated", "archive": str(folder)})
    assert "archive_sha256" not in json.loads(
        (entry / "scan.json").read_text(encoding="utf-8"))["extra"]["shading_origin"]
    library.migrate_calibrations(root, write=True)
    kept = root / library.CALIBRATIONS / hashlib.sha256(
        (folder / "data.bin").read_bytes()).hexdigest()
    assert library.verify(root) == []
    shutil.rmtree("calibration")

    notes: list[str] = []
    assert library.verify(root, notes=notes) == [] and notes == []
    assert library.calibration_of(entry) == kept
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert library.verify(root) == []

    monkeypatch.chdir(tmp_path)
    lost, theirs = archived(Path("calibration"), seed=2, legacy=True)
    _entry_corrected_by(tmp_path, theirs,
                        {"action": "calibrated", "archive": str(lost)})
    shutil.rmtree(lost)
    problems = library.verify(root)
    assert len(problems) == 1 and "is missing" in problems[0], problems
    assert lost.name in problems[0]


def test_calibrations_come_oldest_first_not_in_hash_order(tmp_path):
    """Named by its UTC time a folder sorted by its name; named by its
    lines it sorts by hash, which says nothing of when it was taken."""
    def kept_at(folder, **said):
        path = folder / library.CALIBRATION_RECORD
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(said)
        path.write_text(json.dumps({k: v for k, v in record.items()
                                    if v is not None}), encoding="utf-8")

    root = tmp_path / "library"
    by_hash = sorted((archived(root, seed=s)[0] for s in (1, 2, 3)),
                     key=lambda f: f.name)
    # The first by hash the newest, so the two orders are opposite.
    for folder, hour in zip(by_hash, (10, 9, 8)):
        kept_at(folder, archived_utc=f"2026-10-09T{hour:02d}:00:00Z")
    assert library.calibrations(root / library.CALIBRATIONS) == by_hash[::-1]
    assert library.calibration_time(by_hash[0]) == "2026-10-09T10:00:00Z"

    # Kept before `archived_utc` was recorded: under `calibration/`, its
    # name was the time; copied in, where the name is its id, the time it
    # was measured.
    old, _ = archived(tmp_path / "calibration", seed=4, legacy=True)
    kept_at(old, archived_utc=None)
    assert library.calibration_time(old) == "2026-09-27T00:00:04Z"
    kept_at(by_hash[2], archived_utc=None, measured_utc="2026-10-09T11:00:00Z")
    assert library.calibrations(root / library.CALIBRATIONS) == [
        by_hash[1], by_hash[0], by_hash[2]]


def test_an_archive_recorded_on_windows_is_found_on_any_os(tmp_path, monkeypatch):
    """The driver records the archive with `str()`, so a Windows entry names
    ``cal-a\\<time>``; read elsewhere that was one file name, and verify
    called an intact archive missing. Kept outside `calibration/` so the
    search by content cannot find it in the named path's place."""
    monkeypatch.chdir(tmp_path)
    folder, reference = archived(Path("cal-a"), legacy=True)
    named = _entry_corrected_by(
        tmp_path, reference,
        {"action": "calibrated", "archive": f"cal-a\\{folder.name}"})
    assert not any("missing" in p for p in library.verify(tmp_path / "library"))
    found = library.calibration_of(named)
    assert found is not None and found.resolve() == folder.resolve()


def test_migrating_copies_the_old_archives_in_and_leaves_them_be(
        tmp_path, monkeypatch):
    """A dry run unless asked; then each is copied in under its id, the
    original untouched, and a second run finds nothing to do. A damaged one
    is not copied: its lines would be filed as sound under an id they no
    longer hash to."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "library"
    good, reference = archived(Path("calibration"), seed=1, legacy=True)
    bad, _ = archived(Path("calibration"), seed=2, legacy=True)
    (bad / "data.bin").write_bytes(b"\x00" * 10)
    # A `--reference` directory's archive, which only an entry names.
    beside, theirs = archived(tmp_path / "refs", seed=3, legacy=True)
    _entry_corrected_by(tmp_path, reference, _named(good))
    _entry_corrected_by(tmp_path, theirs, _named(beside))
    before = {f: f.read_bytes() for f in sorted(good.iterdir())}

    planned = library.migrate_calibrations(root)
    assert sorted(what.split(":")[0] for _s, _t, what in planned) == [
        "damaged", "would copy", "would copy"]
    assert not (root / library.CALIBRATIONS).exists(), "a dry run wrote"

    done = library.migrate_calibrations(root, write=True)
    copied = {source.name: target for source, target, what in done
              if what == "copied"}
    assert set(copied) == {good.name, beside.name}
    for source in (good, beside):
        target = copied[source.name]
        assert target.parent == root / library.CALIBRATIONS
        assert sorted(f.name for f in target.iterdir()) == sorted(
            f.name for f in source.iterdir())
        assert library.archive_damage(target) is None
    assert {f: f.read_bytes() for f in sorted(good.iterdir())} == before
    assert bad.exists() and beside.exists()
    assert library.verify(root) == []

    again = library.migrate_calibrations(root, write=True)
    assert sorted(what.split(":")[0] for _s, _t, what in again) == [
        "already in the library", "already in the library", "damaged"]


# --- corrected() with the reference rebuilt from the calibration's lines -----


def test_corrected_can_rebuild_the_reference_from_its_calibration(
        tmp_path, monkeypatch):
    """P05-r2: `corrected()` applied the stored shading.npz and nothing
    else, so a better reduction could be judged on references but never on
    a picture. Opt-in; the default stays the reference filed."""
    from rps7200 import shading

    root = tmp_path / "library"
    folder, reference = archived(root)
    entry = _entry_corrected_by(tmp_path, reference, _named(folder))
    stored, as_filed = library.corrected(entry)
    rebuilt, record = library.corrected(entry, rebuild=True)
    assert record["corrected"] == "applied"
    assert record["reference_rebuilt"] == str(folder)
    assert np.array_equal(rebuilt, stored), "today's reduction is the same"
    assert "reference_rebuilt" not in as_filed

    def brighter(data, ppl, **kw):
        out = shading.calculate_shading(data, ppl, **kw)
        for c in out.channels:
            out.ref[c] = out.ref[c] * 1.25
        return out

    monkeypatch.setattr(library, "calculate_shading", brighter)
    rebuilt, record = library.corrected(entry, rebuild=True)
    assert record["corrected"] == "applied"
    assert not np.array_equal(rebuilt, stored)
    assert np.array_equal(library.corrected(entry)[0], stored), (
        "the default moved with the reduction")


def test_a_reference_that_cannot_be_rebuilt_is_said_never_substituted(tmp_path):
    """Corrected with the stored reference under the name of a rebuilt one,
    a comparison of the two would read "no change"."""
    import shutil

    root = tmp_path / "library"
    folder, reference = archived(root)
    entry = _entry_corrected_by(tmp_path, reference, _named(folder))
    shutil.rmtree(folder)
    image, record = library.corrected(entry, rebuild=True)
    assert record["corrected"] == "not rebuilt"
    assert "no archived calibration" in record["rebuild_error"]
    assert np.array_equal(image, library.load(entry)[0])
    assert library.corrected(entry)[1]["corrected"] == "applied"


def test_a_rebuilt_reference_is_named_only_where_it_was_applied(tmp_path):
    """`reference_rebuilt` went into the record before `correct` decided
    anything, so an entry filed corrected or taken raw came back named for
    an archive that divided nothing."""
    from rps7200.direct import SHADING_SKIPPED_EXPLICIT

    root = tmp_path / "library"
    folder, reference = archived(root)
    already = _entry_corrected_by(tmp_path, reference, _named(folder),
                                  corrections=["shading"])
    image, record = library.corrected(already, rebuild=True)
    assert record["corrected"] == "already"
    assert "reference_rebuilt" not in record
    assert np.array_equal(image, library.load(already)[0])

    stream, raw = index_stream(16, 8, 3, seed=2)
    layout = {"bytes_per_line": 32, "width": 16, "lines": 8, "channels": 3}
    chosen = library.save(raw, {"resolution_dpi": 300, "channels": 3,
                                "shading_skipped": SHADING_SKIPPED_EXPLICIT,
                                "shading_origin": _named(folder)},
                          root=root, raw=stream, raw_layout=layout)
    _image, record = library.corrected(chosen, rebuild=True)
    assert record["corrected"] == "deliberately raw"
    assert "reference_rebuilt" not in record

    applied = _entry_corrected_by(tmp_path, reference, _named(folder))
    assert library.corrected(applied, rebuild=True)[1]["reference_rebuilt"] == (
        str(folder))


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


# -- an entry being written now, told from one a save left -------------------

#: A save in another process, held part way -- its first TIFF waits for the
#: release file -- after saying it has started.
_SLOW_SAVE = """
import sys, time
from pathlib import Path
import numpy as np
from rps7200 import library, tiff
root, release = sys.argv[1:3]
real = tiff.write
def slow(path, *a, **k):
    print("writing", flush=True)
    while not Path(release).exists():
        time.sleep(0.02)
    return real(path, *a, **k)
tiff.write = slow
library.save(np.zeros((4, 6, 3), np.uint16),
             {"resolution_dpi": 300, "channel_order": ["R", "G", "B"],
              "demo": True}, root=root, tags=["slow"])
"""


def test_an_entry_being_written_elsewhere_is_a_note_naming_its_writer(
        tmp_path):
    """Every filing turned `make verify` red until it finished: a marker
    was a problem whoever was still writing behind it. Its save holds it
    locked now, and verify names the process doing so."""
    import subprocess
    import sys

    release = tmp_path / "release"
    writer = subprocess.Popen(
        [sys.executable, "-c", _SLOW_SAVE, str(tmp_path / "library"),
         str(release)], cwd=Path(__file__).resolve().parent.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert writer.stdout.readline().strip() == "writing", \
            writer.communicate(timeout=30)[1]
        notes: list[str] = []
        problems = library.verify(tmp_path / "library", notes=notes)
        assert not any("did not finish" in p for p in problems), problems
        [note] = notes
        entry = next((tmp_path / "library").iterdir())
        assert note.startswith(f"{entry.name}: being written now by python "
                               "(pid ")
        said = json.loads((entry / library.INCOMPLETE).read_text(
            encoding="utf-8"))
        assert note.endswith(f"(pid {said['pid']})")
        assert said["what"] == "python" and said["host"]
    finally:
        release.touch()
        _out, err = writer.communicate(timeout=60)
    assert writer.returncode == 0, err
    assert not any("did not finish" in p
                   for p in library.verify(tmp_path / "library"))


def test_a_save_that_raised_in_this_process_lets_go_of_its_marker(
        tmp_path, monkeypatch):
    """A window whose save raised lives on for days, so a live process says
    nothing of whether its entry is still being written: the marker's own
    lock does, and a save lets go of it however it ends."""
    from rps7200 import locks

    def full(path, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tiff, "write", full)
    with pytest.raises(OSError, match="No space left"):
        make_entry(tmp_path)
    [entry] = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert locks.file_held(entry / library.INCOMPLETE) is False
    notes: list[str] = []
    problems = library.verify(tmp_path, notes=notes)
    assert any(entry.name in p and "did not finish" in p for p in problems)
    assert notes == []


def test_a_calibration_kept_now_is_held_and_says_by_whom(tmp_path):
    from rps7200 import locks

    sha = "ab" * 32
    folder, marker = library.calibration_slot(tmp_path, sha)
    try:
        said = json.loads((folder / library.INCOMPLETE).read_text(
            encoding="utf-8"))
        assert said["id"] == sha and said["pid"] and said["host"]
        assert said["what"]
        assert locks.file_held(folder / library.INCOMPLETE) is True
        notes: list[str] = []
        problems = library.verify(tmp_path, notes=notes)
        assert not any(sha in p for p in problems)
        assert any(f"{library.CALIBRATIONS}/{sha}: being written now by" in n
                   for n in notes)
    finally:
        marker.release()
    assert any(f"{library.CALIBRATIONS}/{sha}: was being written and did not "
               "finish" in p for p in library.verify(tmp_path))


def test_a_calibrations_part_file_is_a_note_while_it_may_be_written_now(
        tmp_path):
    """Its record is written beside and renamed in, as an entry's files are:
    a `make verify` run while the window archived a calibration failed on
    the `.part` the entry check already took for a write in progress."""
    from rps7200 import locks

    sha = "cd" * 32
    folder, marker = library.calibration_slot(tmp_path, sha)
    part = folder / f".{library.CALIBRATION_RECORD}.part"
    part.write_text("{", encoding="utf-8")
    try:
        notes: list[str] = []
        problems = library.verify(tmp_path, notes=notes)
        assert not any(part.name in p for p in problems), problems
        assert any("being written now" in n for n in notes)
    finally:
        marker.release()
    with locks.take(locks.LIBRARY, tmp_path, exclusive=False,
                    what="the window"):
        notes = []
        problems = library.verify(tmp_path, notes=notes)
        assert not any(part.name in p for p in problems), problems
        [note] = [n for n in notes if part.name in n]
        assert note.startswith(f"{library.CALIBRATIONS}/{sha}: {part.name} "
                               f"{library.PARTIAL} -- or a write in progress, "
                               "while the window (")
    assert any(part.name in p and library.PARTIAL in p
               for p in library.verify(tmp_path))


def test_on_a_disk_that_locks_nothing_a_save_that_stopped_is_a_problem(
        tmp_path, monkeypatch):
    """The marker is written plainly there, and nothing holds it. Its lock
    failing was read as "held", so verify named a process long gone as
    writing it now and passed over the half-written entry."""
    from conftest import a_disk_that_locks_nothing

    from rps7200 import locks

    a_disk_that_locks_nothing(monkeypatch)

    def full(path, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tiff, "write", full)
    with pytest.raises(OSError, match="No space left"):
        make_entry(tmp_path)
    [entry] = [p for p in tmp_path.iterdir() if p.is_dir()]
    marker = entry / library.INCOMPLETE
    assert json.loads(marker.read_text(encoding="utf-8"))["pid"]
    assert locks.file_held(marker) is None
    notes: list[str] = []
    problems = library.verify(tmp_path, notes=notes)
    assert any(entry.name in p and "did not finish" in p for p in problems)
    assert not any("being written now" in n for n in notes), notes


def test_compact_keeps_a_tag_added_while_it_ran(tmp_path, monkeypatch):
    """compact reads the record, gzips for minutes and writes it back whole:
    `tools/library.py tag` beside it lost its tag to that write."""
    path, _image, _raw = make_entry(tmp_path, compress=False)
    real = tiff.write
    added = []

    def tag_meanwhile(target, *a, **k):
        if not added:
            added.append(library.add_tags(path, ["added-meanwhile"]))
        return real(target, *a, **k)

    monkeypatch.setattr(tiff, "write", tag_meanwhile)
    assert library.compact(path)
    tags = json.loads((path / "scan.json").read_text(encoding="utf-8"))["tags"]
    assert "added-meanwhile" in tags and "stripe-test" in tags
    assert library.verify(tmp_path) == []


# -- every file anyone relies on reaches the disk before it is named ------------


def test_fsync_asks_the_drive_itself_on_macos(monkeypatch):
    """On macOS a plain fsync hands the data to the drive, whose own cache
    may hold it through a power cut; `F_FULLFSYNC` asks the drive to write
    it. A filesystem that refuses that gets the plain one."""
    import sys
    import types

    asked = []
    fake = types.SimpleNamespace(F_FULLFSYNC=51,
                                 fcntl=lambda fd, op: asked.append((fd, op)))
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    monkeypatch.setattr(library.sys, "platform", "darwin")
    plain = []
    monkeypatch.setattr(library.os, "fsync", plain.append)
    library._fsync(7)
    assert asked == [(7, 51)] and plain == []

    def refused(fd, op):
        raise OSError("not on this filesystem")

    fake.fcntl = refused
    library._fsync(8)
    assert plain == [8]

    monkeypatch.setattr(library.sys, "platform", "win32")
    asked.clear()
    fake.fcntl = lambda fd, op: asked.append((fd, op))
    library._fsync(9)
    assert asked == [] and plain == [8, 9]


def test_a_rename_into_place_makes_its_folder_durable(tmp_path, monkeypatch):
    """On POSIX a rename lives in the folder: unsynced, a power cut can take
    it back after the data it names reached the disk. After the rename,
    never before, and through the retries."""
    synced = []
    monkeypatch.setattr(library, "_sync_dir", synced.append)
    monkeypatch.setattr(library, "REPLACE_RETRY_S", (0.0,))
    real = library.os.replace
    tries = []

    def once_held(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            assert synced == []
            raise PermissionError("held for an instant")
        return real(src, dst)

    monkeypatch.setattr(library.os, "replace", once_held)
    (tmp_path / ".x.part").write_text("new", encoding="utf-8")
    library._replace(tmp_path / ".x.part", tmp_path / "x")
    assert (tmp_path / "x").read_text(encoding="utf-8") == "new"
    assert synced == [tmp_path] and len(tries) == 2


def test_compact_names_the_gzip_only_once_it_is_on_the_disk(tmp_path,
                                                            monkeypatch):
    """`raw.bin` goes at the end. Renamed in unsynced, a power cut after
    that could leave a gzip of zeros under the name and no plain bytes
    beside it -- the one file nothing can be derived back into."""
    path, _image, stream = _plain_entry_with_bytes(tmp_path)
    order = []
    real_sync, real_dir = library._sync, library._sync_dir
    real_replace, real_unlink = library._replace, Path.unlink

    monkeypatch.setattr(library, "_sync", lambda p: (
        order.append(("sync", Path(p).name)), real_sync(p))[-1])
    monkeypatch.setattr(library, "_sync_dir", lambda p: (
        order.append(("folder", Path(p).name)), real_dir(p))[-1])
    monkeypatch.setattr(library, "_replace", lambda t, p: (
        order.append(("replace", Path(t).name, Path(p).name)),
        real_replace(t, p))[-1])

    def unlink(self, *a, **k):
        order.append(("unlink", self.name))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert library.compact(path) is True
    gz = order.index(("replace", ".raw.bin.gz.part", library.RAW_FILE))
    assert ("sync", ".raw.bin.gz.part") in order[:gz]
    gone = order.index(("unlink", library.RAW_PLAIN))
    record = max(i for i, step in enumerate(order[:gone])
                 if step[0] == "replace" and step[2] == "scan.json")
    assert ("folder", path.name) in order[record:gone]
    assert library.read_raw(path) == stream


def test_a_compact_killed_after_the_gzip_went_in_is_finished_by_another(
        tmp_path, monkeypatch):
    path, image, stream = _plain_entry_with_bytes(tmp_path)
    real = library._replace

    def killed(temp, target):
        real(temp, target)
        if Path(target).name == library.RAW_FILE:
            raise KeyboardInterrupt

    with monkeypatch.context() as dying:
        dying.setattr(library, "_replace", killed)
        with pytest.raises(KeyboardInterrupt):
            library.compact(path)
    assert (path / library.RAW_PLAIN).exists()
    assert library.compact(path) is True
    assert not list(path.glob(".*.part"))
    assert not (path / library.RAW_PLAIN).exists()
    assert library.read_raw(path) == stream
    assert _real_problems(tmp_path) == []
    assert np.array_equal(tiff.read(str(path / "scan.tif")), image)


def test_a_migrated_calibration_whose_marker_stays_is_still_copied(
        tmp_path, monkeypatch):
    """The copy is whole; the marker left over is `verify`'s to name, and
    the run says so rather than raising out of it."""
    from rps7200.direct import DirectScanner

    from conftest import FakeTransport

    old = tmp_path / "calibration" / "20260920T000000Z"
    old.parent.mkdir()
    s = DirectScanner(transport=FakeTransport())
    kept = s.archive_calibration({"data": b"\x07" * 8}, tmp_path / "scratch")
    import shutil
    shutil.copytree(kept, old)
    root = tmp_path / "library"
    root.mkdir()

    def held(path):
        raise PermissionError("held by a reader")

    synced = []
    real_sync = library._sync
    monkeypatch.setattr(library, "_sync", lambda p: (
        synced.append(Path(p).name), real_sync(p))[-1])
    monkeypatch.setattr(library, "_unmark", held)
    [(source, target, what)] = library.migrate_calibrations(
        root, (tmp_path / "calibration",), write=True)
    assert what.startswith("copied, but its INCOMPLETE marker")
    assert (target / "data.bin").read_bytes() == b"\x07" * 8
    assert "data.bin" in synced and f".{library.CALIBRATION_RECORD}.part" in synced
    # And `verify`, which it says names it, says the marker alone is left.
    [line] = [p for p in library.verify(root) if target.name in p]
    assert "only removing its INCOMPLETE marker" in line
    assert line.endswith("delete that file")


def test_a_spool_says_the_pictures_a_kill_left_without_a_record(tmp_path):
    """Pixels spooled and killed before their record was written: nothing
    says what they are, `file-spool` passes them over, and the spool they
    keep is never removed. Said, as what nothing can file."""
    root = tmp_path / "library"
    spool = root / ".spool" / "rps7200-debug-abc"
    spool.mkdir(parents=True)
    np.save(spool / "001-image.npy", np.zeros((2, 2, 3), np.uint8))
    [left] = library.leftovers(root)
    assert left.kind == "spool" and not left.problem
    assert "1 picture with no record beside it, which nothing can file" \
        in left.said


def test_what_the_rolls_folder_left_is_counted_under_rolls(tmp_path):
    """Told by its kind's name, a partial write in a roll folder and a roll
    record set aside were counted under "library:" in the one line the
    window and the tools open with."""
    root = tmp_path / "library"
    root.mkdir()
    rolls = tmp_path / "rolls"
    walk = rolls / "walk"
    walk.mkdir(parents=True)
    (walk / "survey.json").write_text("{}", encoding="utf-8")
    (walk / ".roll.json.1-2.part").write_text("[", encoding="utf-8")
    (walk / "roll.json.unreadable").write_text("[", encoding="utf-8")
    kept = walk / library.UNFILED / "20261009T000000Z_x"
    kept.mkdir(parents=True)
    (kept / "scan.json").write_text(json.dumps(
        {"extra": {"unfiled_from": library.library_named(root)}}), encoding="utf-8")
    (root / ".index.json.1-2.part").write_text("[", encoding="utf-8")
    found = library.leftovers(root, rolls=rolls)
    assert {(left.kind, left.where) for left in found} == {
        ("stray-part", "rolls"), ("unreadable-manifest", "rolls"),
        ("unfiled", "rolls"), ("stray-part", "library")}
    assert library.leftovers_said(found) == (
        "left behind -- library: 1 partial write; rolls: 1 pass kept outside "
        "the library, 1 partial write, 1 roll record set aside -- `make "
        "verify` says what clears each")


def _marked_copy(rolls, records, holds, source=None):
    """A Duplicate's target holding ``holds``, its marker listing
    ``records`` -- None for a marker from before it listed them."""
    target = rolls / "walk-2"
    target.mkdir(parents=True)
    said = {"incomplete": "being copied", "from": str(source or "")}
    if records is not None:
        said["records"] = records
    (target / library.INCOMPLETE).write_text(json.dumps(said),
                                             encoding="utf-8")
    for name in holds:
        (target / name).write_text("{}", encoding="utf-8")
    return target


@pytest.mark.parametrize("records, holds, source, finished", [
    (["approved.json", "survey.json"], ["approved.json", "survey.json"],
     None, True),
    (["approved.json", "roll.json", "survey.json"],
     ["approved.json", "roll.json"], None, False),
    (None, ["survey.json"], None, True),
    (None, ["roll.json"], "no walk", True),
    (None, ["roll.json"], "a walk", False),
    (None, ["roll.json"], "gone", False),
    (None, ["prescan01.tif"], None, False),
])
def test_a_finished_copy_whose_marker_stayed_is_not_called_unfinished(
        tmp_path, records, holds, source, finished):
    """The window keeps a whole copy whose marker a reader held, and calls
    it whole; `verify` told Stefan to delete it, perhaps tens of gigabytes.
    The records go in last, so a target holding every one its source held
    finished, and only the marker is to go."""
    rolls = tmp_path / "rolls"
    origin = tmp_path / "source"
    if source in ("no walk", "a walk"):
        origin.mkdir()
        (origin / "roll.json").write_text("{}", encoding="utf-8")
        if source == "a walk":
            (origin / "survey.json").write_text("{}", encoding="utf-8")
    target = _marked_copy(rolls, records, holds, origin)
    [left] = library.leftovers(tmp_path / "library", rolls=rolls)
    assert left.path == target and left.where == "rolls"
    if finished:
        assert left.kind == "roll-copied"
        assert left.said == (f"{target}: the copy finished, but its "
                             "INCOMPLETE marker was left -- delete that file "
                             "and it lists as a roll")
        assert "1 finished copy still marked unfinished" in \
            library.leftovers_said([left])
    else:
        assert left.kind == "roll-copy"
        assert "a Duplicate that did not finish" in left.said
