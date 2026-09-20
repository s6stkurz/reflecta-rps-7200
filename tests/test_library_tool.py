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
