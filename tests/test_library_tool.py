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


# --- merge ------------------------------------------------------------------

def filed_pass(root: Path, *, dy: float, dx: float, exposure: float, seed: int) -> Path:
    """A pass of one textured frame, filed as a scan would be."""
    import numpy as np

    from rps7200 import library
    from rps7200.direct import CHANNEL_ORDER
    from rps7200.library import FilmNotes
    from rps7200.shading import ShadingReference
    from test_passes import as_pass, texture

    h, w = 256, 320
    image = as_pass(texture(h, w, dy=dy, dx=dx, level=4000.0), exposure=exposure, seed=seed)
    meta = {
        "resolution_dpi": 1800, "channels": 3, "channel_order": list(CHANNEL_ORDER[:3]),
        "width": w, "height": h, "depth": 16, "frame": [0, 0, 10343, 6887],
        "bytes_per_line": w * 2, "film": "positive", "shading": None,
        "exposure_scale": [exposure] * 3, "exposure_metered": False,
    }
    reference = ShadingReference(
        ref={c: np.full(w, 30000.0) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=w,
    )
    return library.save(image, meta, root=root, film=FilmNotes(stock="slide"),
                        tags=["bracket-test"], reference=reference, ccd_mask=bytes(w))


def test_merge_registers_the_passes_before_it_merges_them(tmp_path):
    """The shifts are the carriage's: 2.4 lines is what the library's nine-pass
    bracket walked. Registered, the passes agree like repeats; the merge is
    only worth anything once they do."""
    from conftest import load_tool

    tool = load_tool("library")
    root = tmp_path / "library"
    paths = [filed_pass(root, dy=dy, dx=dx, exposure=e, seed=i)
             for i, (dy, dx, e) in enumerate([(0, 0, 1.0), (-1.1, 0.5, 2.0), (-2.4, 0.6, 4.0)])]
    _, report = tool.merge_entries(list(reversed(paths)))    # order is by exposure
    assert report["kind"] == "bracket"
    assert report["exposures"] == [1.0, 2.0, 4.0]
    got = [(s["dy"], s["dx"]) for s in report["shifts"][1:]]
    for (dy, dx), want in zip(got, [(-1.1, 0.5), (-2.4, 0.6)]):
        assert abs(dy - want[0]) < 0.1 and abs(dx - want[1]) < 0.1, got
    assert max(report["agreement_after"]) < max(report["agreement_before"])
    assert report["noise_vs_middle"] < 0.0


def test_merge_writes_the_picture_and_its_report(tmp_path):
    root = tmp_path / "library"
    for i in range(2):
        filed_pass(root, dy=0.4 * i, dx=0.0, exposure=1.0, seed=i)
    out = tmp_path / "merged.tif"
    done = subprocess.run(
        [sys.executable, str(TOOL), "merge", "--tag", "bracket-test",
         "--root", str(root), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert done.returncode == 0, done.stderr
    assert "stack of 2 passes" in done.stdout
    assert out.exists()
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["kind"] == "stack" and report["registered"]


def test_merge_refuses_what_is_not_there(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    done = subprocess.run(
        [sys.executable, str(TOOL), "merge", "nope", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert done.returncode == 1
    assert "no library entry" in done.stderr
