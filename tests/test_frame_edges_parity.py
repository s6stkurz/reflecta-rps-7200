"""The copy in `tools/frame_edges` is the detector the study scored, exactly.

`research/frame-edge` scored `ensemble_v2` against hand labels and stored its
every answer (`results/ensemble_v2/iter01/`, and the same under `lib2/`). This
runs the copy on the same frames, with each frame's roll context rebuilt the
way the study's runner built it (`research/frame-edge/run.py:context`: the
other film frames of its roll and split), and requires the same answer on
every side -- the states, and every position to 1e-9 columns -- for the frames
and for their mirror images.

The frames are Stefan's own pictures and are not in the repository, so this
skips anywhere they are not on disk. Where they are, it is the proof that
porting changed nothing -- and it takes about three minutes, so it runs only
when asked, after any change under `tools/frame_edges/`:

    FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
STUDY = ROOT / "research" / "frame-edge"
SETS = [STUDY, STUDY / "lib2"]
FIELDS = ("x", "lo", "hi", "outer", "x_top", "x_bottom")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (STUDY / "data" / "manifest.json").exists(),
                       reason="the study's frames are not on this machine"),
    pytest.mark.skipif(not os.environ.get("FRAME_EDGE_PARITY"),
                       reason="three minutes; set FRAME_EDGE_PARITY=1 to run"),
]

sys.path.insert(0, str(ROOT))


def _cases():
    for work in SETS:
        for split, stored in (("dev", ""), ("test", "_test")):
            if (work / "results" / "ensemble_v2" / "iter01" / f"results{stored}.json").exists():
                yield pytest.param(work, split, stored, id=f"{work.name}-{split}")


def _same(ours, theirs, where: str) -> list[str]:
    bad = []
    for name in ("left", "right"):
        a, b = getattr(ours, name), theirs[name]
        if a.state != b["state"]:
            bad.append(f"{where} {name}: {a.state} != {b['state']}")
            continue
        for f in FIELDS:
            va, vb = getattr(a, f), b[f]
            if (va is None) != (vb is None) or (va is not None and abs(va - vb) > 1e-9):
                bad.append(f"{where} {name}.{f}: {va} != {vb}")
    return bad


@pytest.mark.parametrize(("work", "split", "stored"), list(_cases()))
def test_the_copy_answers_as_the_study_did(work: Path, split: str, stored: str) -> None:
    from tools.frame_edges import vote
    from tools.frame_edges.roll import summarise

    rows = json.loads((work / "data" / "manifest.json").read_text())["frames"]
    here = [r for r in rows if r["split"] in (split, "ladder")]
    res_dir = work / "results" / "ensemble_v2" / "iter01"
    answers = json.loads((res_dir / f"results{stored}.json").read_text())["results"]
    mirrored = json.loads((res_dir / f"mirrored{stored}.json").read_text())["results"]

    def image(fid: str) -> np.ndarray:
        return np.load(work / "data" / "frames" / f"{fid}.npy").astype(np.float32)

    summaries = {}
    for r in here:
        if r["kind"] == "film":
            summaries[r["id"]] = summarise(image(r["id"]), r.get("dtype"))

    bad: list[str] = []
    for r in here:
        if r["id"] not in answers:
            continue
        roll = [summaries[o["id"]] for o in here
                if o["roll"] == r["roll"] and o["split"] == r["split"]
                and o["kind"] == "film" and o["id"] != r["id"]]
        ctx = {"film_type": r.get("film_type", "unknown"), "dtype": r.get("dtype"),
               "roll": roll}
        img = image(r["id"])
        bad += _same(vote.detect(img, ctx), answers[r["id"]], r["id"])
        if r["id"] in mirrored:
            flipped = np.ascontiguousarray(img[:, ::-1])
            bad += _same(vote.detect(flipped, ctx), mirrored[r["id"]], r["id"] + " mirrored")
    assert not bad, f"{len(bad)} differences, first: " + "; ".join(bad[:8])
