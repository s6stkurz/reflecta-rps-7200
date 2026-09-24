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

REPO = Path(__file__).resolve().parent.parent
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
