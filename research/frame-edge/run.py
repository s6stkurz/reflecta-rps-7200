"""Run one detector over the dataset, score it, and draw its sheets.

    uv run python research/frame-edge/run.py <algo> <iter>            # dev: results, scores, sheets
    uv run python research/frame-edge/run.py <algo> <iter> --no-sheets
    uv run python research/frame-edge/run.py <algo> <iter> --split test   # orchestrator only, once

``<algo>`` is a module in `algos/` exposing ``detect(image, ctx) -> EdgeResult``.
``ctx`` carries ``id``, ``roll``, ``film_type`` (``c41``/``bw``/``unknown``),
``dtype`` (of the file the pixels came from) and ``roll_ids`` (the other
frames of the same roll and split, loadable with ``common.load``). It never
carries a label.

Besides the labelled film frames the detector is run on:

* the **dups** -- the same position at another exposure, bit depth or file --
  whose answers must not move;
* the **ladder** -- twenty prescans a known ~7 columns apart;
* every dev frame **mirrored**, whose answer must mirror.

Output: `results/<algo>/<iter>/results.json`, `scores.json`, `sheet_<roll>.png`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "algos"))

from common import RESULTS, EdgeResult, frame_records, load, save_results  # noqa: E402


def context(rec: dict[str, Any], all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    roll_ids = [r["id"] for r in all_rows
                if r["roll"] == rec["roll"] and r["split"] == rec["split"]
                and r["kind"] == "film" and r["id"] != rec["id"]]
    return {"id": rec["id"], "roll": rec["roll"], "film_type": rec.get("film_type", "unknown"),
            "dtype": rec.get("dtype"), "roll_ids": roll_ids}


def run(algo: str, split: str) -> tuple[dict[str, EdgeResult], dict[str, EdgeResult], float]:
    module = importlib.import_module(algo)
    detect = module.detect
    rows = [r for r in frame_records(kind="") if r["split"] in (split, "ladder")]
    results: dict[str, EdgeResult] = {}
    mirrored: dict[str, EdgeResult] = {}
    t0 = time.perf_counter()
    for rec in rows:
        fr = load(rec)
        ctx = context(rec, rows)
        results[rec["id"]] = detect(fr.image, ctx)
        if rec["kind"] == "film":
            mctx = dict(ctx, mirrored=True)
            mirrored[rec["id"]] = detect(np.ascontiguousarray(fr.image[:, ::-1]), mctx)
    per_frame = (time.perf_counter() - t0) / max(len(rows) + len(mirrored), 1)
    return results, mirrored, per_frame


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("algo")
    ap.add_argument("iter")
    ap.add_argument("--split", default="dev", choices=("dev", "test"))
    ap.add_argument("--no-sheets", action="store_true")
    args = ap.parse_args()
    out = RESULTS / args.algo / args.iter
    results, mirrored, per_frame = run(args.algo, args.split)
    tag = "" if args.split == "dev" else "_test"
    save_results(out / f"results{tag}.json", results,
                 {"algo": args.algo, "iter": args.iter, "split": args.split,
                  "seconds_per_frame": round(per_frame, 4)})
    save_results(out / f"mirrored{tag}.json", mirrored, {"mirrored": True})
    import evaluate
    scores = evaluate.score(results, mirrored, split=args.split)
    scores["seconds_per_frame"] = round(per_frame, 4)
    (out / f"scores{tag}.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    print(evaluate.summary_line(args.algo, args.iter, scores))
    if not args.no_sheets:
        import contact_sheet
        for p in contact_sheet.sheets(results, out, f"{args.algo} {args.iter}",
                                      split=args.split):
            print(p)


if __name__ == "__main__":
    main()
