"""`tools/dpi_analysis.py` compares rungs of one ladder, so they must be alike.

It loaded every rung through `library.corrected` and never looked at what came
back. A full-width 7200 dpi pass cannot be corrected at all, so the series'
reference -- the rung every other is divided by -- was raw while every lower
rung was flat-fielded, and the ratios measured the correction as much as the
resolution. These hold it to one domain for the whole series, or a refusal.
"""

import json

import numpy as np
import pytest

from conftest import load_tool
from rps7200 import library
from rps7200.direct import CHANNEL_ORDER, SHADING_SKIPPED_EXPLICIT
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference

dpi_analysis = load_tool("dpi_analysis")


def rung(root, dpi, *, correctable=True, seed=0):
    """One filed RGB pass of a flat-ish frame, `dpi / 25` columns wide."""
    w, h = dpi // 25, 48
    rng = np.random.default_rng(seed)
    image = rng.normal(20000, 400, (h, w, 3)).clip(0, 65535).astype(np.uint16)
    meta = {"resolution_dpi": dpi, "channels": 3,
            "channel_order": list(CHANNEL_ORDER[:3]),
            "width": w, "height": h, "depth": 16}
    reference = None
    if correctable:
        reference = ShadingReference(
            ref={c: np.linspace(28000, 32000, w) for c in range(3)},
            mean={c: 30000.0 for c in range(3)}, pixels_per_line=w)
    else:
        # What the device answers for a full-width 7200 dpi pass: no
        # reference wide enough, so the pass is taken raw on purpose.
        meta["shading_skipped"] = SHADING_SKIPPED_EXPLICIT
    return library.save(image, meta, root=root, film=FilmNotes(),
                        reference=reference).name


@pytest.fixture
def mixed(tmp_path):
    root = tmp_path / "lib"
    ids = [rung(root, 300, seed=1), rung(root, 600, seed=2),
           rung(root, 1200, correctable=False, seed=3)]
    return root, ids


def _main(tmp_path, root, *argv):
    import sys
    old = sys.argv
    sys.argv = ["dpi_analysis.py", "--root", str(root), "--out",
                str(tmp_path / "out"), "--crop", "16", *argv]
    try:
        return dpi_analysis.main()
    finally:
        sys.argv = old


def test_a_series_with_a_raw_rung_among_corrected_ones_is_refused(tmp_path,
                                                                   mixed, capsys):
    root, ids = mixed
    assert _main(tmp_path, root, "--entries", *ids) == 1
    said = capsys.readouterr().out
    assert "refused" in said
    assert ids[2] in said, "the refusal must name the rung that differs"
    assert not (tmp_path / "out" / "results.json").exists()


def test_the_same_series_compared_raw_throughout_runs(tmp_path, mixed):
    root, ids = mixed
    assert _main(tmp_path, root, "--entries", *ids, "--domain", "raw") == 0
    results = json.loads((tmp_path / "out" / "results.json").read_text())
    assert results["domain"] == "raw"
    assert {e["state"] for e in results["series"]} == {"raw"}


def test_every_rung_corrected_runs_and_says_so(tmp_path):
    root = tmp_path / "lib"
    ids = [rung(root, dpi, seed=dpi) for dpi in (300, 600, 1200)]
    assert _main(tmp_path, root, "--entries", *ids) == 0
    results = json.loads((tmp_path / "out" / "results.json").read_text())
    assert results["domain"] == "corrected"
    assert {e["state"] for e in results["series"]} == {"applied"}


def test_entries_are_named_rather_than_caught_by_a_window(tmp_path, mixed):
    """Anything filed inside the window joined the ladder -- another frame, a
    prescan. Named entries are the series and nothing else."""
    root, ids = mixed
    rung(root, 2400, seed=9)                     # filed alongside, not asked for
    series = dpi_analysis.ladder(root, "", "", ids[:2])
    assert sorted(e["dir"].name for e in series) == sorted(ids[:2])


def test_a_named_entry_that_is_not_there_is_an_error(tmp_path, mixed):
    root, _ = mixed
    with pytest.raises(FileNotFoundError):
        dpi_analysis.ladder(root, "", "", ["20990101T000000Z_nothing_300dpi"])
