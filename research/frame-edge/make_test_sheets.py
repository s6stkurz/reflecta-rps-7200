"""Run the ensemble again, unchanged, and file its sheets under `contact_sheet_test/`.

    uv run --no-sync python make_test_sheets.py                              # main: dev + test
    FRAME_EDGE_WORK=$PWD/lib2 uv run --no-sync python make_test_sheets.py    # library 2
    ... make_test_sheets.py --algo ensemble_v2 --round round2                 # a later round

The detector is `algos/ensemble.py` exactly as pre-registered; nothing in
it or its members changes here. Each set gets its own folder with
`results.json`, `scores.json`, `mismatches.txt` (every frame whose answer is
not the truth, and why) and one sheet per roll page.

A move needs a frame width; every set uses the main study's (see
`score_heldout.py`), so the numbers compare.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "algos"))

import common  # noqa: E402
import contact_sheet  # noqa: E402
import evaluate  # noqa: E402
import run  # noqa: E402

OUT = HERE / "contact_sheet_test"
MAIN_TRUTH = HERE / "ground_truth.json"
ALGO = "ensemble"


def one_set(name: str, split: str, algo: str = ALGO, round_dir: str = "") -> None:
    out = OUT / round_dir / name
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sheet_*.png"):
        old.unlink()
    results, mirrored, per_frame = run.run(algo, split)
    common.save_results(out / "results.json", results,
                        {"algo": algo, "set": name, "split": split,
                         "seconds_per_frame": round(per_frame, 4)})
    scores = evaluate.score(results, mirrored, split=split)
    (out / "scores.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    truth = contact_sheet.truth_results(split)
    width = json.loads(MAIN_TRUTH.read_text(encoding="utf-8"))["frame_width"]["_all"]
    lines = []
    for fid in sorted(truth):
        if fid not in results:
            continue
        w = int(common.load(fid).image.shape[1])
        why = contact_sheet.mismatches(results[fid], truth[fid], w, width)
        if why:
            lines.append(f"{fid}: " + "; ".join(why))
    (out / "mismatches.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    contact_sheet.sheets(results, out, f"{algo} ({name})", split=split)
    print(evaluate.summary_line(algo, name, scores), f"| {len(lines)} frames not the truth",
          flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--algo", default=ALGO)
    ap.add_argument("--round", default="", help="subfolder of contact_sheet_test/")
    args = ap.parse_args()
    if not args.round:
        # round 1: library 2 was all held out, scored with the main study's width
        evaluate.GROUND_TRUTH = MAIN_TRUTH
        contact_sheet.GROUND_TRUTH = MAIN_TRUTH
    if common.WORK == HERE:
        one_set("main_dev", "dev", args.algo, args.round)
        one_set("main_test", "test", args.algo, args.round)
    elif args.round:
        # from round 2 library 2 is split into dev2 and a locked test2
        one_set("library2_dev", "dev", args.algo, args.round)
        one_set("library2_test", "test", args.algo, args.round)
    else:
        one_set("library2", "test", args.algo, args.round)


if __name__ == "__main__":
    main()
