"""Score every detector once on a held-out dataset built in another work folder.

    FRAME_EDGE_WORK=$PWD/lib2 uv run --no-sync python score_heldout.py

For a second library labelled as split `test` (see README, "A second
dataset"): no detector was tuned on it or run on it before. The detectors are
exactly those pre-registered in `results/ensemble/iter01/PREREGISTERED.txt`,
as they are on disk.

A move needs a frame width. The held-out set has no dev frames to measure one
from, so the main study's width is used -- fixed before this set was seen,
and applied to truth and detector alike. The set's own width, from its gap
readings, is printed alongside as a finding, not used.

Writes `<work>/results/<algo>/<iter>/{results_test,scores_test}.json` and the
ensemble's sheets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "algos"))

import common  # noqa: E402
import contact_sheet  # noqa: E402
import evaluate  # noqa: E402
import run  # noqa: E402

MAIN_TRUTH = HERE / "ground_truth.json"
DETECTORS = [("changepoint", "iter04"), ("chroma", "iter04"), ("stepline", "iter04"),
             ("gapmodel", "iter04"), ("texture", "iter04"), ("baserun", "iter04"),
             ("learned", "iter04"), ("ensemble", "iter01")]


def own_frame_width() -> list[float]:
    """The held-out set's frame widths, pitch minus gap, from its own `outer` labels."""
    truth = json.loads(common.GROUND_TRUTH_TEST.read_text(encoding="utf-8"))["frames"]
    pitch = common.pitch_columns()
    out = []
    for f in truth.values():
        for name in ("left", "right"):
            s = f.get(name) or {}
            if s.get("state") == common.EDGE and s.get("outer") is not None:
                out.append(pitch - abs(float(s["x"]) - float(s["outer"])))
    return out


def main() -> None:
    if common.WORK == HERE:
        raise SystemExit("set FRAME_EDGE_WORK to the held-out dataset's folder")
    # the frame width is the main study's, fixed before this set was seen
    evaluate.GROUND_TRUTH = MAIN_TRUTH
    contact_sheet.GROUND_TRUTH = MAIN_TRUTH
    width = json.loads(MAIN_TRUTH.read_text(encoding="utf-8"))["frame_width"]["_all"]
    widths = own_frame_width()
    print(f"frame width used: {width} (main study)")
    if widths:
        w = np.array(widths)
        print(f"this set's own frame width, pitch - gap over {len(w)} gaps: median "
              f"{np.median(w):.1f}, IQR {np.percentile(w, 25):.1f}-{np.percentile(w, 75):.1f}")
    for algo, it in DETECTORS:
        results, mirrored, per_frame = run.run(algo, "test")
        out = common.RESULTS / algo / it
        common.save_results(out / "results_test.json", results,
                            {"algo": algo, "iter": it, "split": "test",
                             "seconds_per_frame": round(per_frame, 4)})
        common.save_results(out / "mirrored_test.json", mirrored, {"mirrored": True})
        scores = evaluate.score(results, mirrored, split="test")
        scores["seconds_per_frame"] = round(per_frame, 4)
        (out / "scores_test.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
        print(evaluate.summary_line(algo, it, scores), flush=True)
        if algo == "ensemble":
            contact_sheet.sheets(results, out, f"{algo} {it} (held out)", split="test")


if __name__ == "__main__":
    main()
