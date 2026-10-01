"""Fit the three film models and write local/calibration.json.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/calibrate.py

C-41 is fitted to the real compose frames (8-bit, at the device's exposure);
B&W and slides to the numbers measured on their real frames (fpn.calibrate).
The result stays local: it is fitted on private pictures. Its sha256 goes into
PREREGISTERED.md. Takes about twenty minutes; run it in the background.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from fpn import calibrate, device, paths, personal, stats, study  # noqa: E402


def pool_entries() -> list[Path]:
    """Raw uint8 300 dpi entries: their references and masks, never their pixels."""
    out = []
    for d in sorted(paths.LIBRARY.iterdir()):
        try:
            r = json.loads((d / "scan.json").read_text())
        except (OSError, ValueError):
            continue
        sc, im = r.get("scan") or {}, r.get("image") or {}
        if (sc.get("resolution_dpi") == 300 and im.get("dtype") == "uint8"
                and not im.get("corrections_applied")
                and (r.get("calibration") or {}).get("ccd_mask")):
            out.append(d)
    return out


def real_c41() -> dict:
    rng = np.random.default_rng(0)
    frames = [f for f in study.frames("compose")
              if f.film == "c41" and f.dtype == "uint8" and f.kind == "film"]
    measured = []
    for f in frames:
        t = study.truth(f)
        if t is not None:
            measured.append((f, stats.measure(f.image(), t, rng)))
    by_roll: dict[str, list] = defaultdict(list)
    for f, s in measured:
        if s.base is not None:
            by_roll[f.roll].append(s.base)
    roll_base = {r: np.median(v, axis=0) for r, v in by_roll.items()}
    keep = [(f, s) for f, s in measured if f.roll in roll_base]
    out = stats.summarise([s for _, s in keep], [roll_base[f.roll] for f, _ in keep])
    out["rolls"] = sorted(roll_base)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=36, help="bench frames per film")
    ap.add_argument("--iterations", type=int, default=160)
    ap.add_argument("--films", default="c41,bw,slide")
    ap.add_argument("--merge", action="store_true",
                    help="keep the other films' fits already in calibration.json")
    args = ap.parse_args(argv)
    t0 = time.time()

    def log(s: str) -> None:
        print(f"[{time.time() - t0:6.0f}s] {s}", flush=True)

    pool = device.ReferencePool(pool_entries())
    log(f"{len(pool.calibrations)} distinct calibrations")
    grid = personal.GridLoader(paths.PERSONAL_CACHE)
    split = study.split()
    train = {r for r, role in split["personal_rolls"].items() if role == "train"}
    by_roll = personal.roll_pictures(paths.PERSONAL_CACHE, train)
    target = paths.LOCAL / "calibration.json"
    previous = json.loads(target.read_text()) if target.exists() and args.merge else {}
    out: dict = {"split_sha256": hashlib.sha256(paths.SPLIT.read_bytes()).hexdigest(),
                 "calibrations": [c.entry for c in pool.calibrations],
                 "films": dict(previous.get("films") or {})}
    if "real_c41" in previous:
        out["real_c41"] = previous["real_c41"]
    films = args.films.split(",")
    params = None
    for k, name in enumerate(films):
        rng = np.random.default_rng([7, k])
        triples = personal.triples(by_roll, args.frames, rng)
        bench = calibrate.make_bench(name, triples, grid, pool, seed=100 + k)
        log(f"{name}: bench of {len(triples)} frames ready")
        if name == "c41":
            real = real_c41()
            out["real_c41"] = real
            params, report = calibrate.fit_c41(bench, real, args.iterations, log)
            out["films"]["c41"] = {"params": asdict(params.c41), **report}
        elif name == "bw":
            params, report = calibrate.fit_bw(bench, args.iterations // 2, log)
            out["films"]["bw"] = {"params": asdict(params.bw), **report}
        elif name == "slide":
            params, report = calibrate.fit_slide(bench, args.iterations // 2, log)
            out["films"]["slide"] = {"params": asdict(params.slide), **report}
        log(f"{name}: loss {report['loss']:.4f}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=float))
    log(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
