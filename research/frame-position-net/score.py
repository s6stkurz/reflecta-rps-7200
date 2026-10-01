"""Score the network exactly as ensemble_v2 was scored, on one role of the split.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/score.py \\
        --weights local/runs/r1/epoch-17.npz --role realval

The frames are loaded and scored by the frame-edge study's own code: its
`common.load`, and `evaluate.score` with its EDGE_TOL, THIN, truth widths and
moves. The network runs on each frame and on its mirror image, as run.py does.
ensemble_v2's stored answers for the very same frames are scored beside it.

A locked role (`locked_main`, `locked_test2`, `locked_bw`, `locked_positive`)
is refused unless PREREGISTERED.md is committed and names these weights'
sha256: the tests are scored once, after everything is fixed.

Writes results/<role>/<weights stem>.json (committed: numbers only).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from fpn import paths, study  # noqa: E402

STUDY_CODE = paths.ROOT / "research" / "frame-edge"
FILM = {"c41": "negative", "bw": "bw", "unknown": "negative"}
SPLIT_OF_ROLE = {"realval": "dev", "compose": "dev", "locked_bw": "dev",
                 "locked_main": "test", "locked_test2": "test", "locked_positive": "test"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preregistered(weights: Path) -> bool:
    doc = HERE / "PREREGISTERED.md"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(doc)], cwd=HERE,
                             capture_output=True).returncode == 0
    return tracked and sha256(weights) in doc.read_text()


def worker(work: Path, ids: list[str], weights: Path, split: str, sure: float | None) -> dict:
    """Runs with FRAME_EDGE_WORK set: the study's modules read it at import."""
    sys.path.insert(0, str(STUDY_CODE))
    common = importlib.import_module("common")
    evaluate = importlib.import_module("evaluate")
    from fpn.net import Net

    net = Net(weights, sure=sure)
    rows = {r["id"]: r for r in common.frame_records(kind="")}
    results: dict[str, Any] = {}
    mirrored: dict[str, Any] = {}
    for fid in ids:
        rec = rows[fid]
        image = common.load(rec).image
        film = "positive" if rec["kind"] == "slide" else FILM.get(rec.get("film_type"), "negative")
        dtype = rec.get("dtype") or "uint8"
        results[fid], _ = net.detect(image, film=film, dtype=dtype)
        mirrored[fid], _ = net.detect(image[:, ::-1].copy(), film=film, dtype=dtype)
    tag = "" if split == "dev" else "_test"
    stored = common.RESULTS / "ensemble_v2" / "iter01" / f"results{tag}.json"
    ensemble = {}
    if stored.exists():
        ensemble = {k: v for k, v in common.load_results(stored).items() if k in ids}
    return {"net": evaluate.score(results, mirrored, split=split),
            "ensemble_v2": evaluate.score(ensemble, None, split=split) if ensemble else None,
            "frames": len(ids),
            "answers": {k: v.to_json() for k, v in results.items()}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--role", required=True, choices=sorted(SPLIT_OF_ROLE))
    ap.add_argument("--sure", type=float, help="the abstain threshold; default the weights'")
    ap.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--ids", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    split = SPLIT_OF_ROLE[args.role]
    if args.worker:
        ids = json.loads(args.ids.read_text()) if args.ids else []
        print(json.dumps(worker(args.worker, ids, args.weights, split, args.sure), default=float))
        return 0
    locked = args.role.startswith("locked")
    if locked and not preregistered(args.weights):
        print("a locked set is scored only after PREREGISTERED.md is committed "
              "and names these weights' sha256", file=sys.stderr)
        return 1
    frames = study.frames(args.role, unlock=locked)
    out: dict[str, Any] = {"role": args.role, "weights": str(args.weights),
                           "weights_sha256": sha256(args.weights), "works": {}}
    for work in sorted({f.work for f in frames}):
        ids = [f.id for f in frames if f.work == work and f.kind in ("film", "slide")
               and study.truth(f) is not None]
        if not ids:
            continue
        ids_file = paths.LOCAL / "score-ids.json"
        ids_file.parent.mkdir(parents=True, exist_ok=True)
        ids_file.write_text(json.dumps(ids))
        env = dict(os.environ, FRAME_EDGE_WORK=str(work))
        cmd = [sys.executable, __file__, "--weights", str(args.weights), "--role", args.role,
               "--worker", str(work), "--ids", str(ids_file)]
        if args.sure is not None:
            cmd += ["--sure", str(args.sure)]
        done = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if done.returncode != 0:
            print(done.stderr, file=sys.stderr)
            return 1
        out["works"][work.name] = json.loads(done.stdout.strip().splitlines()[-1])
        for who in ("net", "ensemble_v2"):
            sc = out["works"][work.name][who]
            if sc:
                side, dec = sc["side"], sc["decision"]
                print(f"{work.name:10s} {who:12s} sides {side['correct']}/{side['n']}  "
                      f"false base {side['false_base']}  missed {side['missed']}  "
                      f"refused {side['refused']}  moves ok {dec['ok']}/{dec['n']}  "
                      f"bad {dec['bad']}  wrong way {dec['sign_errors']}")
    target = HERE / "results" / args.role / f"{args.weights.stem}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=float))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
