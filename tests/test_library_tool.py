"""`tools/library.py list`, against entries that predate the fields it prints.

The library is the one artefact here that grows for years, so its survey command
meets records written by older code than itself. `list` died 56 rows into a
311-entry library on exactly that: 69 entries carry `scan.channels` as an
explicit `null` -- the key is *present* and holds None -- so `get("channels", "")`
never reached its default and the format spec raised on NoneType.

The shape of the bug is worth keeping in mind beyond this one line: a `get`
default guards a *missing* key and does nothing for a key that is there holding
None, and every record in this library is a JSON document written by whatever
version of the driver was current that day.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO =Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "library.py"


def entry(root: Path, name: str, scan: dict) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "scan.json").write_text(json.dumps({
        "id": name,
        "scan": scan,
        "film": {"stock": "Kodak Gold 200", "notes": ""},
        "raw": {"file": "raw.bin.gz"},
    }), encoding="utf-8")
    return path


def run_list(root: Path):
    return subprocess.run(
        [sys.executable, str(TOOL), "list", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )


def test_an_entry_with_a_null_channel_count_still_lists(tmp_path):
    """The real regression. `channels: null` is what 69 filed entries hold."""
    root = tmp_path / "library"
    entry(root, "20260909T105555Z_unknown-film_300dpi",
          {"resolution_dpi": 300, "channels": None})
    done = run_list(root)
    assert done.returncode == 0, done.stderr
    assert "1 entries" in done.stdout
    assert "TypeError" not in done.stderr


def test_a_null_resolution_or_id_does_not_crash_it_either(tmp_path):
    """Same trap, same line, two more keys away. Fixing only the one that
    happened to be hit would leave the next old entry to find the others."""
    root = tmp_path / "library"
    path = entry(root, "no-resolution", {"resolution_dpi": None, "channels": None})
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["id"] = None
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    done = run_list(root)
    assert done.returncode == 0, done.stderr
    assert "TypeError" not in done.stderr


def test_it_lists_every_entry_it_was_given(tmp_path):
    """The count is the point of the command, and the crash truncated it
    silently enough to look like a short library rather than a failure."""
    root = tmp_path / "library"
    for n in range(5):
        entry(root, f"entry-{n}",
              {"resolution_dpi": 300, "channels": None if n % 2 else 4})
    done = run_list(root)
    assert done.returncode == 0, done.stderr
    assert "5 entries" in done.stdout
    for n in range(5):
        assert f"entry-{n}" in done.stdout


def test_a_complete_entry_still_prints_its_numbers(tmp_path):
    """The fix uses `or ''`, which also swallows a legitimate zero. Nothing here
    is ever 0 dpi or 0 channels, but the columns that do carry values must still
    show them -- a fix that blanked every row would pass the tests above."""
    root = tmp_path / "library"
    entry(root, "complete", {"resolution_dpi": 1800, "channels": 4})
    done = run_list(root)
    assert done.returncode == 0, done.stderr
    assert "1800" in done.stdout
    assert "4" in done.stdout


# -- migrate-raw repairs a mislabelled entry and nothing else -----------------


def _filed(root: Path, stored_from):
    """An entry whose scan.tif is `stored_from(decode, reference, mask)`."""
    import numpy as np

    from rps7200 import library, tiff
    from rps7200.shading import ShadingReference

    width, lines = 16, 8
    rng = np.random.default_rng(3)
    planes = [rng.integers(2000, 60000, (lines, width), dtype=np.uint16)
              for _ in range(3)]
    raw = bytearray()
    for y in range(lines):
        for c, tag in enumerate("RGB"):
            raw += tag.encode() * 2 + planes[c][y].tobytes()
    decode = np.stack(planes, axis=-1)
    reference = ShadingReference(
        ref={c: np.linspace(20000.0, 40000.0, width) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=width)
    mask = None
    path = library.save(
        decode, {"resolution_dpi": 300, "channels": 3, "width": width,
                 "height": lines, "depth": 16, "channel_order": list("RGB")},
        root=root, reference=reference, ccd_mask=mask, raw=bytes(raw),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "width": width, "lines": lines, "channels": 3})
    stored = stored_from(decode, reference, mask)
    tiff.write(str(path / "scan.tif"), stored, resolution=300)
    return path, decode, stored


def _migrate(root: Path):
    return subprocess.run(
        [sys.executable, str(TOOL), "migrate-raw", "--write", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)


def test_corrected_pixels_filed_as_raw_are_rewritten_and_the_old_file_kept(tmp_path):
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, decode, stored = _filed(
        root, lambda d, r, m: apply_shading(d, r, m)[0])
    done = _migrate(root)
    assert done.returncode == 0, done.stderr
    assert np.array_equal(tiff.read(str(path / "scan.tif")), decode)
    kept = path / "scan.before-migrate-raw.tif"
    assert kept.exists(), "the only corrected rendition was destroyed"
    assert np.array_equal(tiff.read(str(kept)), stored)


def test_a_decode_that_changed_is_left_alone_not_laundered(tmp_path):
    """A stored image one shading does not explain is a decode regression --
    the thing `reconstruct` exists to report -- and rewriting it from today's
    decode would bury it for good."""
    import numpy as np

    from rps7200 import tiff

    def drifted(d, r, m):
        out = d.copy()
        out[0, 0, 0] ^= 1
        return out

    root = tmp_path / "library"
    path, _decode, stored = _filed(root, drifted)
    done = _migrate(root)
    assert "left alone" in done.stdout + done.stderr
    assert np.array_equal(tiff.read(str(path / "scan.tif")), stored)
    assert not (path / "scan.before-migrate-raw.tif").exists()


def test_file_spool_files_what_debug_filing_left_behind(tmp_path):
    """From the library's `.spool`, by default, and nothing left there after."""
    import numpy as np

    from rps7200.direct import DirectScanner

    class Detached(DirectScanner):
        def __init__(self):
            super().__init__(transport=object(), debug=True)
            self._own_transport = False

    root = tmp_path / "library"
    s = Detached()
    s.debug_root = root
    s._debug_capture(np.zeros((8, 16, 3), np.uint8),
                     {"resolution_dpi": 300, "channel_order": ["R", "G", "B"]})
    spool = s._debug_pending[0]["image_path"].parent
    assert spool.parent == root / DirectScanner.DEBUG_SPOOL_DIR
    done = subprocess.run(
        [sys.executable, str(TOOL), "file-spool", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert "1 pass(es) filed" in done.stdout
    assert len(list(root.glob("*/scan.json"))) == 1
    assert not spool.exists()


def _label(path: Path, corrections=("shading",)):
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["image"]["corrections_applied"] = list(corrections)
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_labelled_entry_one_shading_explains_is_rewritten(tmp_path):
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, decode, stored = _filed(root, lambda d, r, m: apply_shading(d, r, m)[0])
    _label(path)
    done = _migrate(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert np.array_equal(tiff.read(str(path / "scan.tif")), decode)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["image"]["corrections_applied"] == []
    # the kept original is checksummed like every other file
    from rps7200 import library
    assert "scan.before-migrate-raw.tif" in record["files"]
    assert library.verify(root) == []


def test_a_labelled_entry_whose_decode_changed_is_left_alone(tmp_path):
    """The laundering guard ran only for unlabelled entries, so a labelled one
    whose decode had moved became today's decode of it, relabelled raw --
    and `reconstruct` then called it identical."""
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    def drifted(d, r, m):
        out = apply_shading(d, r, m)[0]
        out[0, 0, 0] ^= 1
        return out

    root = tmp_path / "library"
    path, _decode, stored = _filed(root, drifted)
    _label(path)
    done = _migrate(root)
    assert "left alone" in done.stdout, done.stdout
    assert done.returncode == 1
    assert np.array_equal(tiff.read(str(path / "scan.tif")), stored)
    assert not (path / "scan.before-migrate-raw.tif").exists()


def test_a_labelled_entry_without_its_reference_is_left_alone(tmp_path):
    """Nothing then proves the stored pixels came from these bytes, and the
    rewrite turned an entry whose Save As was corrected into a striped one."""
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, _decode, stored = _filed(root, lambda d, r, m: apply_shading(d, r, m)[0])
    _label(path)
    (path / "shading.npz").unlink()
    done = _migrate(root)
    assert "left alone" in done.stdout, done.stdout
    assert np.array_equal(tiff.read(str(path / "scan.tif")), stored)


def test_a_rewrite_stopped_before_its_record_is_finished_not_undone(tmp_path):
    """Stopped after the swap: raw pixels under a record still saying
    corrected. A re-run moved scan.tif over the kept file, replacing the one
    corrected rendition with the raw decode."""
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, decode, stored = _filed(root, lambda d, r, m: apply_shading(d, r, m)[0])
    _label(path)
    # the state a kill between the swap and the record leaves
    tiff.write(str(path / "scan.before-migrate-raw.tif"), stored, resolution=300)
    tiff.write(str(path / "scan.tif"), decode, resolution=300)
    done = _migrate(root)
    assert done.returncode == 0, done.stdout
    assert "earlier run" in done.stdout
    assert np.array_equal(
        tiff.read(str(path / "scan.before-migrate-raw.tif")), stored)
    assert np.array_equal(tiff.read(str(path / "scan.tif")), decode)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    assert record["image"]["corrections_applied"] == []


def test_a_mislabelled_rewrite_stopped_before_its_record_is_named_and_finished(
        tmp_path):
    """The unlabelled kind stopped at the same point: raw pixels under a
    record that says raw and checksums the corrected file. verify called its
    scan.tif damaged, and migrate-raw took it for one already done."""
    import numpy as np

    from rps7200 import library, tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, decode, stored = _filed(root, lambda d, r, m: apply_shading(d, r, m)[0])
    # What a legacy entry's record holds: the checksum of the file it filed.
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["image"]["sha256"] = library._sha256(path / "scan.tif")
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    # the state a kill between the swap and the record leaves
    tiff.write(str(path / library.MIGRATE_KEPT), stored, resolution=300)
    tiff.write(str(path / "scan.tif"), decode, resolution=300)

    problems = library.verify(root)
    assert not any("checksum" in p for p in problems), problems
    assert any("migrate-raw stopped part way" in p for p in problems)

    done = _migrate(root)
    assert done.returncode == 0, done.stdout
    assert "earlier run" in done.stdout
    assert np.array_equal(tiff.read(str(path / library.MIGRATE_KEPT)), stored)
    assert np.array_equal(tiff.read(str(path / "scan.tif")), decode)
    assert library.verify(root) == []


def test_a_kept_file_holding_another_picture_is_never_overwritten(tmp_path):
    import numpy as np

    from rps7200 import tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, _decode, stored = _filed(root, lambda d, r, m: apply_shading(d, r, m)[0])
    other = np.zeros_like(stored)
    tiff.write(str(path / "scan.before-migrate-raw.tif"), other, resolution=300)
    done = _migrate(root)
    assert "left alone" in done.stdout
    assert np.array_equal(
        tiff.read(str(path / "scan.before-migrate-raw.tif")), other)
    assert np.array_equal(tiff.read(str(path / "scan.tif")), stored)


def test_compact_finishes_what_a_killed_window_left_plain(tmp_path):
    """Nothing else ever compacts them: the window's list of plain entries
    lives in memory and dies with it."""
    from rps7200 import library

    root = tmp_path / "library"
    path = _good(root, compress=False)
    assert (path / library.RAW_PLAIN).exists()
    dry = subprocess.run(
        [sys.executable, str(TOOL), "compact", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert (path / library.RAW_PLAIN).exists(), "a dry run wrote"
    done = subprocess.run(
        [sys.executable, str(TOOL), "compact", "--write", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (path / library.RAW_PLAIN).exists()
    assert (path / library.RAW_FILE).exists()
    assert [p for p in library.verify(root) if "never be corrected" not in p] == []


def test_compact_finds_a_plain_entry_with_no_raw_bytes(tmp_path):
    """The tool chose entries by `raw.bin` alone, so one the window filed
    plain without bytes and was killed before compacting was never offered
    to the compact that now deflates it."""
    import numpy as np

    from rps7200 import library, tiff

    if not tiff._has_tifffile():
        pytest.skip("only tifffile compresses; there is nothing to deflate")
    root = tmp_path / "library"
    path = library.save(np.full((8, 16, 3), 900, np.uint16),
                        {"resolution_dpi": 300, "channels": 3},
                        root=root, compress=False)
    assert library._uncompressed_tiffs(path)
    done = subprocess.run(
        [sys.executable, str(TOOL), "compact", "--write", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"compacted: {path.name}" in done.stdout
    assert not library._uncompressed_tiffs(path)
    assert not library.wants_compacting(path)


def test_migrate_direction_carries_on_past_bytes_it_cannot_decode(tmp_path):
    """One entry whose tags the decode could not place ended the run with a
    traceback part way, before the reindex."""
    root = tmp_path / "library"
    bad = _good(root, raw=b"\x00" * (8 * 3 * (16 * 2 + 2)))
    record = json.loads((bad / "scan.json").read_text(encoding="utf-8"))
    record["scan"]["read_direction"] = None
    (bad / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(TOOL), "migrate-direction", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert "Traceback" not in done.stderr, done.stderr
    assert f"! {bad.name}" in done.stdout


def test_calibrations_re_reduces_every_archive_and_fails_on_damage(tmp_path):
    from test_library import archived

    cal = tmp_path / "calibration"
    good, _ = archived(cal, seed=1)
    bad, _ = archived(cal, seed=2)

    def run():
        return subprocess.run(
            [sys.executable, str(TOOL), "calibrations", "--calibrations",
             str(cal), "--root", str(tmp_path / "no-library-needed")],
            capture_output=True, text=True, cwd=REPO)

    done = run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "identical" in done.stdout
    (bad / "data.bin").write_bytes(b"\x00" * 10)
    done = run()
    assert done.returncode == 1, done.stdout
    assert f"! {bad.name}" in done.stdout


# -- reconstruct: its exit status is the regression gate ----------------------


def _reconstruct(root: Path):
    return subprocess.run(
        [sys.executable, str(TOOL), "reconstruct", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)


def _good(root: Path, raw: bytes | None = None, **kw) -> Path:
    """An ordinary entry, or one whose stored bytes are `raw` instead."""
    import numpy as np

    from rps7200 import library

    width, lines = 16, 8
    rng = np.random.default_rng(7)
    planes = [rng.integers(2000, 60000, (lines, width), dtype=np.uint16)
              for _ in range(3)]
    stream = bytearray()
    for y in range(lines):
        for c, tag in enumerate("RGB"):
            stream += tag.encode() * 2 + planes[c][y].tobytes()
    return library.save(
        np.stack(planes, axis=-1),
        {"resolution_dpi": 300, "channels": 3, "width": width,
         "height": lines, "depth": 16},
        root=root, raw=bytes(stream) if raw is None else raw,
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "width": width, "lines": lines, "channels": 3}, **kw)


def test_a_clean_library_reconstructs_with_exit_zero(tmp_path):
    root = tmp_path / "library"
    _good(root)
    done = _reconstruct(root)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_decode_that_now_raises_fails_the_gate(tmp_path):
    """Bytes that decoded yesterday and make today's decode raise are a decode
    regression. They were counted as "nothing to decode from" and the run
    exited 0, so a change that broke every entry passed `make reconstruct`.
    The bytes here pass their checksum; only the decode can be at fault."""
    root = tmp_path / "library"
    _good(root, raw=b"\x00" * (8 * 3 * (16 * 2 + 2)))
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout
    assert "could not decode" in done.stdout
    assert "not a regression" not in done.stdout


def test_a_scan_tif_that_no_longer_reads_fails_the_gate(tmp_path):
    root = tmp_path / "library"
    path = _good(root)
    (path / "scan.tif").write_bytes(b"not a tiff")
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout


def test_corrupt_raw_bytes_are_damage_not_nothing_stored(tmp_path):
    """A truncated gzip read as "no raw bytes stored", which the tool counted
    as benign."""
    root = tmp_path / "library"
    path = _good(root)
    data = (path / "raw.bin.gz").read_bytes()
    (path / "raw.bin.gz").write_bytes(data[: len(data) // 2])
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout
    assert "damage" in done.stdout and "verify" in done.stdout
    assert "no raw bytes stored" not in done.stdout


def test_a_deleted_raw_file_fails_the_gate(tmp_path):
    """The record names raw.bin.gz and checksums it; with the file gone,
    reconstruct asked only the disk, read "no raw bytes stored", and exited 0
    while verify called the same file missing."""
    root = tmp_path / "library"
    path = _good(root)
    (path / "raw.bin.gz").unlink()
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout
    assert "no raw bytes stored" not in done.stdout


def test_an_entry_with_nothing_stored_is_still_benign(tmp_path):
    import numpy as np

    from rps7200 import library

    root = tmp_path / "library"
    library.save(np.zeros((8, 16, 3), np.uint16),
                 {"resolution_dpi": 300, "channels": 3}, root=root)
    done = _reconstruct(root)
    assert done.returncode == 0, done.stdout
    assert "nothing to decode from" in done.stdout


# -- duplicates --delete, the one command-line rmtree over entries ------------


def _pass(root: Path, seed: int) -> Path:
    """One 300 dpi pass filed as the scanner files it: raw bytes and all.
    The same seed is the same picture; the request is the same for every
    seed, so they all share one signature."""
    import numpy as np

    from rps7200 import library
    from rps7200.direction import encode_index

    image = np.random.default_rng(seed).integers(
        0, 65535, (6, 8, 3), dtype=np.uint16)
    return library.save(
        image, {"resolution_dpi": 300, "channels": 3, "width": 8, "height": 6,
                "depth": 16, "channel_order": list("RGB"),
                "exposure_metered": True, "protocol_revision": 6},
        root=root, raw=encode_index(image),
        raw_layout={"format": "index", "bytes_per_line": 16, "width": 8,
                    "lines": 6, "channels": 3})


def _duplicates(root: Path, *argv):
    return subprocess.run(
        [sys.executable, str(TOOL), "duplicates", "--root", str(root), *argv],
        capture_output=True, text=True, cwd=REPO)


def test_duplicates_deletes_a_twin_and_nothing_else(tmp_path):
    """Two filings of one pass, and a different picture asked for the same
    way. Only the twin may go: a shared request is not a shared photograph,
    and this command once destroyed every such picture but one, raw bytes
    included. Without --delete it removes nothing at all."""
    from rps7200 import library

    root = tmp_path / "library"
    first, twin, other = _pass(root, 1), _pass(root, 1), _pass(root, 2)

    dry = _duplicates(root)
    assert dry.returncode == 0, dry.stderr
    assert first.exists() and twin.exists() and other.exists()
    assert "would be freed" in dry.stdout

    done = _duplicates(root, "--delete")
    assert done.returncode == 0, done.stderr
    left = {p.name for p in (first, twin, other) if p.exists()}
    assert other.name in left, "a different picture was deleted as a duplicate"
    assert len(left) == 2 and len({first.name, twin.name} & left) == 1
    indexed = {r["id"] for r in json.loads(
        (root / library.INDEX).read_text(encoding="utf-8"))}
    assert indexed == left, "the index still lists what was removed"
    for name in left:
        assert library.reconstruct(root / name)[1].startswith("identical")
