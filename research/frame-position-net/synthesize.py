"""Create a set of synthetic training prescans.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/synthesize.py \\
        --n 5000 --seed 1 --set train --mix c41=0.55,bw=0.20,slide=0.25

Each sample is a strip of frames under the aperture (fpn.compose) made from the
private pictures of the set's rolls (`split.json`: train = A-D, synval = E),
developed as the film asked for (fpn.film, with local/calibration.json), and
delivered through a real calibration and the driver's own correction
(fpn.device) -- a uint8 prescan as `scan()` returns it -- with its truth.

Writes local/synth/<set>-<seed>/:
    shard-NNNNN.npz   images (n, 287, 428, 3) uint8, rows (n,), targets (n, 4, 428)
    manifest.jsonl    one line per sample: recipe, truth (EdgeResult), the move
    about.json        the command, the calibration's and split's sha256, counts

Sample i is made from `default_rng([seed, i])` alone, so any one can be made
again (--only i), and a set is the same on every machine. Refuses to start if
the set would leave less than 10 GB free.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from fpn import compose, device, film, paths, personal, realcompose, study  # noqa: E402
from tools.frame_edges.centre import decide, frame_columns  # noqa: E402
from fpn.inputs import COLUMNS, ROWS  # noqa: E402

BYTES_PER_SAMPLE = 170_000          # compressed: 165 kB measured on a 40-sample set
FREE_FLOOR = 10 * 1024 ** 3

_state: dict[str, Any] = {}


def params_from(calibration: Path | None) -> film.FilmParams:
    params = film.FilmParams()
    if calibration is None:
        return params
    fitted = json.loads(calibration.read_text())["films"]
    from dataclasses import replace

    def fit(name: str, current):
        p = (fitted.get(name) or {}).get("params")
        if not p:
            return current
        return replace(current, **{k: tuple(v) if isinstance(v, list) else v for k, v in p.items()})

    return replace(params, c41=fit("c41", params.c41), bw=fit("bw", params.bw),
                   slide=fit("slide", params.slide))


def _init(entries: list[str], calibration: str | None, rolls: list[str],
          real_share: float = 0.0) -> None:
    _state["real_share"] = real_share
    _state["sources"] = realcompose.sources() if real_share > 0 else []
    _state["pool"] = device.ReferencePool([Path(e) for e in entries])
    _state["grid"] = personal.GridLoader(paths.PERSONAL_CACHE, keep=4)
    _state["params"] = params_from(Path(calibration) if calibration else None)
    _state["by_roll"] = personal.roll_pictures(paths.PERSONAL_CACHE, set(rolls))


def make(args: tuple[int, int, list[tuple[str, float]]]) -> dict:
    seed, i, mix = args
    rng = np.random.default_rng([seed, i])
    names = [m for m, _ in mix]
    weights = np.array([w for _, w in mix]) / sum(w for _, w in mix)
    film_name = names[int(rng.choice(len(names), p=weights))]
    if film_name == "c41" and _state["sources"] and rng.random() < _state["real_share"]:
        image, truth, source = realcompose.make(_state["sources"], rng)
        d = decide(truth, COLUMNS, frame_columns())
        return {"i": i, "image": image, "target": realcompose.target_from_result(truth, image.shape[0]),
                "truth": truth.to_json(),
                "move": {"action": d.action, "units": d.units, "why": d.why, "physical_units": None},
                "recipe": {"film": "c41", "source": source}, "roll": None}
    triple = personal.triples(_state["by_roll"], 1, rng)[0]
    cal = _state["pool"].pick(rng)
    recipe_seed = int(rng.integers(2 ** 31))
    r = compose.draw_recipe(film_name, triple, cal.entry, recipe_seed)
    roll = film.draw_roll(film_name, _state["params"], np.random.default_rng([recipe_seed, 3]))
    image, target, truth = compose.render(r, _state["grid"], _state["pool"], _state["params"], roll)
    return {"i": i, "image": image, "target": target, "truth": truth.to_json(),
            "move": compose.move(r, truth), "recipe": compose.recipe_json(r),
            "roll": {"film": roll.film, "base_t": roll.base_t, "exposure_stops": roll.exposure_stops}}


def _write_shard(out: Path, k: int, items: list[dict]) -> None:
    images = np.zeros((len(items), ROWS, COLUMNS, 3), dtype=np.uint8)
    rows = np.zeros(len(items), dtype=np.int16)
    for j, it in enumerate(items):
        images[j, : it["image"].shape[0]] = it["image"]
        rows[j] = it["image"].shape[0]
    targets = np.stack([it["target"] for it in items]).astype(np.float16)
    ids = np.array([it["i"] for it in items], dtype=np.int64)
    np.savez_compressed(out / f"shard-{k:05d}.npz", images=images, rows=rows,
                        targets=targets, ids=ids)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--set", choices=("train", "synval"), default="train")
    ap.add_argument("--mix", default="c41=0.55,bw=0.20,slide=0.25")
    ap.add_argument("--real-share", type=float, default=0.5,
                    help="of the C-41 samples, the share made from real compose frames")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--shard", type=int, default=250)
    ap.add_argument("--defaults", action="store_true",
                    help="the film models' defaults, without local/calibration.json")
    ap.add_argument("--only", type=int, help="make sample i alone and print its truth")
    args = ap.parse_args(argv)

    mix = [(k, float(v)) for k, v in (m.split("=") for m in args.mix.split(","))]
    rolls = [r for r, role in study.split()["personal_rolls"].items() if role == args.set]
    calibration = None if args.defaults else paths.LOCAL / "calibration.json"
    if calibration is not None and not calibration.exists():
        print(f"{calibration} is missing: run calibrate.py first, or --defaults", file=sys.stderr)
        return 1
    from calibrate import pool_entries

    entries = [str(e) for e in pool_entries()]
    if args.only is not None:
        _init(entries, str(calibration) if calibration else None, rolls,
              args.real_share if args.set == "train" else 0.0)
        it = make((args.seed, args.only, mix))
        print(json.dumps({k: it[k] for k in ("truth", "move", "recipe")}, indent=1, default=float))
        return 0

    out = paths.LOCAL / "synth" / f"{args.set}-{args.seed}"
    need = args.n * BYTES_PER_SAMPLE
    free = shutil.disk_usage(paths.LOCAL.parent).free
    if free - need < FREE_FLOOR:
        print(f"needs ~{need / 1e9:.1f} GB and only {free / 1e9:.1f} GB is free", file=sys.stderr)
        return 1
    if out.exists() and any(out.iterdir()):
        print(f"{out} exists; a set is never overwritten", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    shard: list[dict] = []
    k = 0
    with open(out / "manifest.jsonl", "w") as manifest, \
            Pool(args.workers, initializer=_init,
                 initargs=(entries, str(calibration) if calibration else None, rolls,
                           args.real_share if args.set == "train" else 0.0)) as pool:
        jobs = ((args.seed, i, mix) for i in range(args.n))
        for done, it in enumerate(pool.imap(make, jobs, chunksize=4), 1):
            manifest.write(json.dumps({"id": f"{args.set}-{args.seed}-{it['i']:05d}", "i": it["i"],
                                       "shard": k, "truth": it["truth"], "move": it["move"],
                                       "recipe": it["recipe"], "roll": it["roll"]},
                                      default=float) + "\n")
            shard.append(it)
            if len(shard) == args.shard:
                _write_shard(out, k, shard)
                shard, k = [], k + 1
            if done % 100 == 0:
                rate = done / (time.time() - t0)
                print(f"{done}/{args.n}  {rate:.1f}/s  ~{(args.n - done) / rate / 60:.0f} min left",
                      flush=True)
        if shard:
            _write_shard(out, k, shard)
    about = {"command": sys.argv, "n": args.n, "seed": args.seed, "set": args.set, "mix": mix,
             "real_share": args.real_share if args.set == "train" else 0.0,
             "rolls": rolls, "seconds": round(time.time() - t0),
             "split_sha256": hashlib.sha256(paths.SPLIT.read_bytes()).hexdigest(),
             "calibration_sha256": hashlib.sha256(calibration.read_bytes()).hexdigest()
             if calibration else None}
    (out / "about.json").write_text(json.dumps(about, indent=1))
    print(f"wrote {args.n} samples to {out} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
