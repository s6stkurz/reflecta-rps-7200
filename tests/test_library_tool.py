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


def test_an_entry_whose_bytes_cannot_be_its_picture_is_marked(tmp_path):
    """`library.save` files such bytes and records `raw.disagrees`; the
    survey says so beside the entry, and counts them."""
    root = tmp_path / "library"
    path = entry(root, "odd", {"resolution_dpi": 300, "channels": 3})
    entry(root, "fine", {"resolution_dpi": 300, "channels": 3})
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["raw"]["disagrees"] = {"width": [16, 20]}
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    done = run_list(root)
    assert done.returncode == 0, done.stderr
    rows = {line.split()[0]: line for line in done.stdout.splitlines()
            if line.startswith(("odd", "fine"))}
    assert rows["odd"].endswith("[raw DISAGREES]"), rows
    assert rows["fine"].endswith("[raw]"), rows
    assert "1 filed with raw bytes that cannot be their picture's" in done.stdout


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


def test_a_migrate_that_fails_leaves_no_partial_file(tmp_path, monkeypatch):
    """A full disk part way through left `.scan.tif.part` in the entry, and
    verify reported it as a partial write on every run after."""
    from conftest import load_tool

    from rps7200 import library, tiff
    from rps7200.shading import apply_shading

    root = tmp_path / "library"
    path, _decode, stored = _filed(
        root, lambda d, r, m: apply_shading(d, r, m)[0])
    tool = load_tool("library")

    def full(target, *a, **k):
        Path(target).write_bytes(b"half a tiff")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tiff, "write", full)
    monkeypatch.setattr(sys, "argv", ["library.py", "migrate-raw", "--write",
                                      "--root", str(root)])
    with pytest.raises(OSError, match="No space left"):
        tool.main()
    assert not list(path.glob(".*.part"))
    assert not any("partial write" in p for p in library.verify(root))


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


def _spooling(root: Path):
    """A session with debug filing on, its library at ``root``, no device."""
    from rps7200.direct import DirectScanner

    class Detached(DirectScanner):
        def __init__(self):
            super().__init__(transport=object(), debug=True)
            self._own_transport = False

    s = Detached()
    s.debug_root = root
    return s


def test_file_spool_looks_in_the_temporary_directory_too(tmp_path):
    """Where a session spools when its library cannot be written. Such a
    spool was found only by the path its log had named. That directory is
    every library's fallback -- and the test suite's, which left 93 passes
    in it -- so only a spool whose passes name this library is filed into
    it; another is said and left, with how to file it anyway."""
    import tempfile

    import numpy as np

    from rps7200.direct import DirectScanner

    temp = Path(tempfile.gettempdir())            # the test's own (conftest)
    root = tmp_path / "library"
    root.mkdir()
    # A file where the spool's folder goes: the library cannot take it.
    (root / DirectScanner.DEBUG_SPOOL_DIR).write_text("in the way")
    ours = _spooling(root)
    ours._debug_capture(np.zeros((8, 16, 3), np.uint8),
                        {"resolution_dpi": 300, "channel_order": ["R", "G", "B"]})
    fell_back = ours._debug_pending[0]["image_path"].parent
    assert fell_back.parent == temp, "the fallback this is about"
    (root / DirectScanner.DEBUG_SPOOL_DIR).unlink()    # writable again

    theirs = _spooling(tmp_path / "another library")
    theirs._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))
    theirs._debug_capture(np.ones((8, 16, 3), np.uint8),
                          {"resolution_dpi": 300})
    other = theirs._debug_spool

    done = subprocess.run(
        [sys.executable, str(TOOL), "file-spool", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert f"looked in {root / DirectScanner.DEBUG_SPOOL_DIR} and {temp}" \
        in done.stdout
    assert "1 pass(es) filed" in done.stdout, done.stdout
    assert len(list(root.glob("*/scan.json"))) == 1
    assert not fell_back.exists()
    assert other.exists(), "another library's passes filed into this one"
    assert f"{other}: left" in done.stdout
    assert str(tmp_path / "another library") in done.stdout
    assert f"file-spool {other} --root {root}" in done.stdout

    # Named, it is filed here all the same: the operator's call.
    done = subprocess.run(
        [sys.executable, str(TOOL), "file-spool", str(other), "--root",
         str(root)], capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert len(list(root.glob("*/scan.json"))) == 2


def test_file_spool_looks_in_the_temporary_directory_for_a_missing_library(
        tmp_path):
    """A library that could not be made is one reason a session spools into
    the system's temporary directory -- and file-spool answered "no library
    at" for it before it had looked there, or filed a spool it was named.
    It says the library is missing, looks, and makes it as it files."""
    import tempfile

    import numpy as np

    from rps7200.direct import DirectScanner

    temp = Path(tempfile.gettempdir())            # the test's own (conftest)
    in_the_way = tmp_path / "a file"
    in_the_way.write_text("in the way")
    root = in_the_way / "library"             # cannot be made under a file
    s = _spooling(root)
    s._debug_capture(np.zeros((8, 16, 3), np.uint8),
                     {"resolution_dpi": 300, "channel_order": ["R", "G", "B"]})
    fell_back = s._debug_pending[0]["image_path"].parent
    assert fell_back.parent == temp, "the fallback this is about"
    in_the_way.unlink()                       # it could be made now
    assert not root.exists()

    done = subprocess.run(
        [sys.executable, str(TOOL), "file-spool", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert f"no library at {root} yet" in done.stdout
    assert f"looked in {root / DirectScanner.DEBUG_SPOOL_DIR} and {temp}" \
        in done.stdout
    assert "1 pass(es) filed" in done.stdout, done.stdout
    assert len(list(root.glob("*/scan.json"))) == 1
    assert not fell_back.exists()


def test_file_spool_with_nothing_to_file_makes_no_library(tmp_path):
    root = tmp_path / "library"
    done = subprocess.run(
        [sys.executable, str(TOOL), "file-spool", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert "no spool left behind" in done.stdout
    assert not root.exists()


def _failure_record(folder: Path, name: str, library_root: Path) -> Path:
    from rps7200.direct import library_named

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({"kind": "scan", "error": "ScanReadError: x",
                                "library": library_named(library_root)}),
                    encoding="utf-8")
    return path


def test_verify_and_list_say_where_the_failure_records_are(tmp_path):
    """One small JSON per pass that failed with nothing to file -- the wedge
    cases. Not an entry, so not an entry with no scan.json either; not a
    problem with the library, so `verify` stays green; and said, because a
    record nobody is told of is one nobody reads."""
    import tempfile

    from rps7200.direct import DirectScanner

    root = tmp_path / "library"
    beside, fallback = DirectScanner.failure_folders(root)
    _failure_record(beside, "20261009T101010Z_scan.json", root)
    _failure_record(beside, "20261009T111111Z_calibration.json", root)
    assert fallback.parent == Path(tempfile.gettempdir())
    _failure_record(fallback, "20261009T121212Z_scan.json", root)
    _failure_record(fallback, "20261009T131313Z_scan.json",
                    tmp_path / "another library")

    done = subprocess.run(
        [sys.executable, str(TOOL), "verify", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stdout
    assert "library is intact" in done.stdout
    assert "has no scan.json" not in done.stdout
    assert (f"2 record(s) of a pass that failed with nothing to file, in "
            f"{beside} (newest 20261009T111111Z_calibration.json)") \
        in done.stdout
    assert (f"1 record(s) of a pass that failed with nothing to file, in "
            f"{fallback}") in done.stdout, "another library's counted"

    listed = run_list(root)
    assert listed.returncode == 0, listed.stderr
    assert "library is empty" in listed.stdout
    assert f"in {beside}" in listed.stdout


def test_a_missing_library_still_has_its_failure_records_said(tmp_path):
    """A library that could not be made is why a record went to the
    temporary directory; "no library" alone hid the one record of why."""
    from rps7200.direct import DirectScanner

    root = tmp_path / "never made"
    _beside, fallback = DirectScanner.failure_folders(root)
    _failure_record(fallback, "20261009T121212Z_scan.json", root)
    for action in ("list", "verify"):
        done = subprocess.run(
            [sys.executable, str(TOOL), action, "--root", str(root)],
            capture_output=True, text=True, cwd=REPO)
        assert done.returncode == 1, done.stdout
        assert f"no library at {root}" in done.stderr
        assert (f"1 record(s) of a pass that failed with nothing to file, "
                f"in {fallback}") in done.stdout, action


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
    good, _ = archived(cal, seed=1, legacy=True)
    bad, _ = archived(cal, seed=2, legacy=True)

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


def test_calibrations_reduces_the_librarys_own_and_an_old_copy_once(tmp_path):
    """The library's, by id, and the ones still beside the cache -- less an
    old one already copied in, which is the same lines reduced twice."""
    from test_library import archived

    from rps7200 import library

    root, cal = tmp_path / "library", tmp_path / "calibration"
    kept, _ = archived(root, seed=1)
    copy, _ = archived(cal, seed=1, legacy=True)
    old, _ = archived(cal, seed=2, legacy=True)

    def run():
        return subprocess.run(
            [sys.executable, str(TOOL), "calibrations", "--calibrations",
             str(cal), "--root", str(root)],
            capture_output=True, text=True, cwd=REPO)

    done = run()
    assert done.returncode == 0, done.stdout + done.stderr
    # With when it was kept: the name is a hash, and says nothing of that.
    when = library.calibration_time(kept)
    assert when and f"  {library.CALIBRATIONS}/{kept.name} ({when}): identical" \
        in done.stdout
    assert f"  {old.name}: identical" in done.stdout
    assert copy.name not in done.stdout
    assert "1 under" in done.stdout and "copied into the library already" in done.stdout
    (kept / "data.bin").write_bytes(b"\x00" * 10)
    done = run()
    assert done.returncode == 1, done.stdout
    assert f"! {library.CALIBRATIONS}/{kept.name}" in done.stdout


def test_migrate_calibrations_copies_them_in_and_verify_then_passes(tmp_path):
    """`verify` says an archive is still outside -- not damage -- and names
    this command; a dry run copies nothing, `--write` copies and never
    moves, and the library is then whole. `compact` and `list` see the
    calibrations folder as no entry."""
    from test_library import _entry_corrected_by, _named, archived

    from rps7200 import library

    root, cal = tmp_path / "library", tmp_path / "calibration"
    folder, reference = archived(cal, seed=1, legacy=True)
    _entry_corrected_by(tmp_path, reference, _named(folder))

    def run(*args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args, "--root", str(root)],
            capture_output=True, text=True, cwd=REPO)

    # A note, not a failure: counted as a problem, it turned every library
    # filed before 2026-10-09 red with nothing damaged.
    checked = run("verify")
    assert checked.returncode == 0, checked.stdout
    assert "note: 1 calibration" in checked.stdout
    assert "outside the library" in checked.stdout
    assert "migrate-calibrations --write" in checked.stdout
    assert "library is intact" in checked.stdout

    dry = run("migrate-calibrations", "--calibrations", str(cal))
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "would copy" in dry.stdout and "pass --write" in dry.stdout
    assert not (root / library.CALIBRATIONS).exists()

    done = run("migrate-calibrations", "--calibrations", str(cal), "--write")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 calibration copied into the library" in done.stdout
    assert (folder / "data.bin").exists(), "the original was moved"
    assert (root / library.CALIBRATIONS / _named(folder)["archive_sha256"]
            / "data.bin").read_bytes() == (folder / "data.bin").read_bytes()

    checked = run("verify")
    assert checked.returncode == 0, checked.stdout
    assert "library is intact" in checked.stdout
    assert "note:" not in checked.stdout
    again = run("migrate-calibrations", "--calibrations", str(cal))
    assert "already in the library" in again.stdout

    for action in (("compact",), ("compact", "--write"), ("list",)):
        seen = run(*action)
        assert seen.returncode == 0, seen.stdout + seen.stderr
        assert "Traceback" not in seen.stderr
        assert library.CALIBRATIONS not in seen.stdout
    assert "1 entries" in run("list").stdout


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


def test_a_pass_filed_failed_for_its_decode_is_said_and_fails_nothing(tmp_path):
    """Kept because nothing could decode its bytes, tagged failed for it, and
    counted as a decode regression: one such pass held `make reconstruct`
    red for good. Said on its own line now, and a real regression beside it
    still fails the run."""
    from test_library import failed_entry

    root = tmp_path / "library"
    known, _ = failed_entry(root)
    readable, _ = failed_entry(root, decodes=True)
    done = _reconstruct(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"x {known.name}: filed tagged failed" in done.stdout
    assert f"+ {readable.name}: filed tagged failed" in done.stdout
    assert "1 filed tagged failed because their bytes did not decode when " \
           "they were taken, and still do not" in done.stdout
    assert "today's decode reads them -- not a regression, and worth a " \
           "look" in done.stdout
    assert "decode regression" not in done.stdout

    _good(root, raw=b"\x00" * (8 * 3 * (16 * 2 + 2)))
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout
    assert "1 could not be decoded by today's code" in done.stdout


def test_migrate_raw_leaves_a_failed_pass_today_s_decode_reads(tmp_path):
    """Its scan.tif is its lines, not corrected pixels: compared with the
    decode, the shapes differed and the run failed. And counted with the
    entries that would not decode, the one today's decode reads was called
    undecodable here while `reconstruct` called it news."""
    from test_library import failed_entry

    root = tmp_path / "library"
    path, _ = failed_entry(root, decodes=True)
    known, _ = failed_entry(root)
    before = {p: (p / "scan.tif").read_bytes() for p in (path, known)}
    done = _migrate(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert {p: (p / "scan.tif").read_bytes() for p in (path, known)} == before
    assert "2 filed tagged failed, whose scan.tif is their lines as they " \
           "arrived -- left alone" in done.stdout
    assert "could not be decoded" not in done.stdout


def test_verify_and_reconstruct_name_damage_inside_the_deflate_stream(tmp_path):
    """zlib.error, which is not an OSError, ended both runs with a traceback
    at the damaged entry."""
    from test_library import deflate_start

    root = tmp_path / "library"
    path = _good(root)
    data = bytearray((path / "raw.bin.gz").read_bytes())
    data[deflate_start(bytes(data))] |= 0x06
    (path / "raw.bin.gz").write_bytes(bytes(data))
    for action in ("verify", "reconstruct"):
        done = subprocess.run(
            [sys.executable, str(TOOL), action, "--root", str(root)],
            capture_output=True, text=True, cwd=REPO)
        assert "Traceback" not in done.stderr, done.stderr
        assert done.returncode == 1, done.stdout
        assert f"! {path.name}" in done.stdout or path.name in done.stdout


def test_verify_names_a_record_that_is_not_utf8(tmp_path):
    root = tmp_path / "library"
    _good(root)
    odd = root / "20260101T000000Z_odd_300dpi"
    odd.mkdir()
    (odd / "scan.json").write_bytes('{"notes": "Südtirol"}'.encode("cp1252"))
    done = subprocess.run(
        [sys.executable, str(TOOL), "verify", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO)
    assert "Traceback" not in done.stderr, done.stderr
    assert done.returncode == 1
    assert f"{odd.name}: scan.json cannot be read (not UTF-8" in done.stdout


def test_an_entry_with_nothing_stored_is_still_benign(tmp_path):
    import numpy as np

    from rps7200 import library

    root = tmp_path / "library"
    library.save(np.zeros((8, 16, 3), np.uint16),
                 {"resolution_dpi": 300, "channels": 3}, root=root)
    done = _reconstruct(root)
    assert done.returncode == 0, done.stdout
    assert "nothing to decode from" in done.stdout


def test_bytes_filed_as_not_their_pictures_are_said_and_fail_nothing(tmp_path):
    """`library.save` files bytes that cannot be the picture beside them and
    records why (`raw.disagrees`). Counted as a decode CHANGED, one such
    entry kept `make reconstruct` red on every run for good, over what the
    record said the day it was filed. Said apart, and out of the exit
    status -- while a real regression beside it still fails the gate."""
    import numpy as np

    from rps7200 import library

    root = tmp_path / "library"
    _good(root)
    width, lines = 20, 8                       # the picture below is 16 wide
    planes = np.random.default_rng(3).integers(2000, 60000, (3, lines, width),
                                               dtype=np.uint16)
    stream = bytearray()
    for y in range(lines):
        for c, tag in enumerate("RGB"):
            stream += tag.encode() * 2 + planes[c][y].tobytes()
    with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
        odd = library.save(
            np.zeros((lines, 16, 3), np.uint16),
            {"resolution_dpi": 300, "channels": 3, "width": 16,
             "height": lines, "depth": 16},
            root=root, raw=bytes(stream),
            raw_layout={"format": "index", "bytes_per_line": width * 2,
                        "width": width, "lines": lines, "channels": 3})

    done = _reconstruct(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"? {odd.name}: as filed, its raw bytes cannot be" in done.stdout
    assert "width 16 for the picture, 20 in the bytes" in done.stdout
    assert "1 filed with raw bytes their record says cannot" in done.stdout
    assert "no longer decode" not in done.stdout

    _good(root, raw=b"\x00" * (8 * 3 * (16 * 2 + 2)))
    done = _reconstruct(root)
    assert done.returncode == 1, done.stdout
    assert f"? {odd.name}" in done.stdout


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
