"""Every detector iteration, re-scored against today's ground truth, in one table.

    uv run python research/frame-edge/leaderboard.py

Scores in a run's own `scores.json` were computed against whatever ground
truth and `decide` existed when it ran -- some before the truth existed at
all. This re-scores every `results/<algo>/<iter>/results.json` (dev) now, so
the rows compare like with like, and writes `results/leaderboard.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402
from common import RESULTS, load_results  # noqa: E402


def rows() -> list[dict]:
    out = []
    for path in sorted(RESULTS.glob("*/*/results.json")):
        algo, it = path.parent.parent.name, path.parent.name
        mpath = path.with_name("mirrored.json")
        s = evaluate.score(load_results(path), load_results(mpath) if mpath.exists() else None)
        sd, dc, lf = s["side"], s["decision"], s["label_free"]
        out.append({
            "algo": algo, "iter": it,
            "side_acc": sd["accuracy"], "false_base": sd["false_base"], "missed": sd["missed"],
            "refused": sd["refused"], "edge_mae": sd["edge_mae_cols"],
            "edge_p90": sd["edge_p90_cols"], "move_ok": dc["ok"], "move_n": dc["n"],
            "move_bad": dc["bad"], "sign": dc["sign_errors"], "move_refused": dc["refused"],
            "pairs_mae": lf["pairs"]["mae_cols"], "dups_mae": lf["dups"]["mae_cols"],
            "mirror": lf.get("mirror", {}).get("state_changes"),
            "per_film": {k: v["accuracy"] for k, v in s["per_film"].items()},
        })
    return out


def main() -> None:
    table = rows()
    (RESULTS / "leaderboard.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
    head = (f"{'algo':12s} {'iter':7s} {'side':>5s} {'fbase':>5s} {'miss':>4s} {'ref':>4s} "
            f"{'mae':>5s} {'p90':>5s} {'move ok':>8s} {'bad':>4s} {'sign':>4s} "
            f"{'pairs':>5s} {'dups':>5s} {'mirr':>4s}")
    print(head)
    for r in table:
        print(f"{r['algo']:12s} {r['iter']:7s} {r['side_acc']:5.2f} {r['false_base']:5d} "
              f"{r['missed']:4d} {r['refused']:4d} {r['edge_mae']:5.2f} {r['edge_p90']:5.2f} "
              f"{r['move_ok']:3d}/{r['move_n']:<4d} {r['move_bad']:4d} {r['sign']:4d} "
              f"{r['pairs_mae']!s:>5s} {r['dups_mae']!s:>5s} {r['mirror']!s:>4s}")


if __name__ == "__main__":
    main()
