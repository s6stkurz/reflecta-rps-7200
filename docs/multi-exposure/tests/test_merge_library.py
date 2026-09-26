"""The study's merge script, against a library built here.

    uv run pytest docs/multi-exposure/tests
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TOOL = HERE.parent / "analysis" / "merge_library.py"


def load_merge_tool():
    spec = importlib.util.spec_from_file_location("merge_library", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    tool = load_merge_tool()
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
        [sys.executable, str(TOOL), "--tag", "bracket-test",
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
        [sys.executable, str(TOOL), "nope", "--root", str(root)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert done.returncode == 1
    assert "no library entry" in done.stderr
