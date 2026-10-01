"""Do the synthetic prescans look like film? Four checks, before any training.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/realism.py \\
        --set local/synth/train-1

1. Statistics: the C-41 numbers `fpn.stats` measures, synthetic against the
   real compose frames. Gate: each synthetic picture quantile within 0.25 of a
   stop of the real one, base grain within a third.
2. ensemble_v2 reads them: the in-software vote run on synthetic C-41 and B&W
   frames, scored by the study's side rule. Gate: on real-content samples at
   least 95% of sides and no false base; on picture-based ones within 10
   points of that.
3. Real or synthetic? A small classifier on 64-column crops of 8-bit C-41,
   trained on half the frames and tested on the other half. Gate: AUC <= 0.75.
   Above that, something gives the synthetic frames away, and its crops show
   what.
4. Stefan's eye: a blind sheet of 12 real and 12 synthetic C-41 frames,
   shuffled, the key written beside it; and sheets of synthetic B&W and slides.

Writes local/realism/report.json and the sheets there. Local only: the
sheets show private pictures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from fpn import data, paths, sheets, stats, study  # noqa: E402
from tools.frame_edges import propose  # noqa: E402
from tools.frame_edges.sides import EDGE, PICTURE_TO_BORDER, EdgeResult  # noqa: E402

OUT = paths.LOCAL / "realism"
CROP = 64


def _real_c41() -> list[tuple[np.ndarray, EdgeResult]]:
    out = []
    for f in study.frames("compose"):
        if f.kind == "film" and f.film == "c41" and f.dtype == "uint8":
            t = study.truth(f)
            if t is not None:
                out.append((f.image(), t))
    return out


def _samples(s: data.SyntheticSet, film: str, source: str) -> list[int]:
    """Indexes of one film's samples from one source: "personal" or "real"."""
    out = []
    for i, m in enumerate(s.rows_meta):
        rec = m["recipe"]
        if rec["film"] != film:
            continue
        is_real = str(rec.get("source", "")).startswith("real:")
        if (source == "real") == is_real:
            out.append(i)
    return out


def check_statistics(s: data.SyntheticSet, real: dict) -> dict:
    rng = np.random.default_rng(0)
    idx = _samples(s, "c41", "personal")[:300]
    frames = [stats.measure(s.images[i, : s.rows[i]], EdgeResult.from_json(s.rows_meta[i]["truth"]),
                            rng) for i in idx]
    bases = [f.base if f.base is not None else np.ones(3) for f in frames]
    syn = stats.summarise(frames, bases)
    q_syn = np.array([syn["picture_log2_vs_base"][str(q)] for q in stats.QUANTILES])
    q_real = np.array([real["picture_log2_vs_base"][str(q)] for q in stats.QUANTILES])
    spread = np.array(syn["base_spread"]) / np.array(real["base_spread"])
    return {"synthetic": syn, "real": real,
            "worst_quantile_stops": float(np.abs(q_syn - q_real).max()),
            "grain_ratio": spread.tolist(),
            "pass": bool(np.abs(q_syn - q_real).max() <= 0.25
                         and np.all(np.abs(spread - 1) <= 1 / 3))}


def check_ensemble(s: data.SyntheticSet) -> dict:
    common = data.study_common()
    out: dict[str, Any] = {}
    for name, film, source in (("c41 real", "c41", "real"), ("c41 picture", "c41", "personal"),
                               ("bw picture", "bw", "personal")):
        idx = _samples(s, film, source)[:150]
        agree = scored = false_base = 0
        for i in idx:
            truth = EdgeResult.from_json(s.rows_meta[i]["truth"])
            got = propose.detect(s.images[i, : s.rows[i]], film="negative" if film == "c41" else "bw",
                                 dtype="uint8")
            for side in ("left", "right"):
                ok = common.side_agrees(truth.side(side), got.side(side), side, 428)
                if ok is None:
                    continue
                scored += 1
                agree += int(ok)
                g, a = truth.side(side), got.side(side)
                if g.state == PICTURE_TO_BORDER and a.state == EDGE and a.x is not None:
                    base = a.x if side == "left" else 428 - a.x
                    false_base += int(base >= 1.0)
        out[name] = {"frames": len(idx), "sides": scored, "agree": agree,
                     "share": agree / max(scored, 1), "false_base": false_base}
    real = out["c41 real"]
    out["pass"] = bool(real["share"] >= 0.95 and real["false_base"] == 0
                       and all(out[k]["share"] >= real["share"] - 0.10
                               for k in ("c41 picture", "bw picture")))
    return out


def _crops(image: np.ndarray, rng: np.random.Generator, n: int) -> list[np.ndarray]:
    img = image[5:281, :, :3].astype(np.float32)
    starts = rng.integers(0, img.shape[1] - CROP, size=n)
    return [img[:, s: s + CROP] for s in starts]


def check_classifier(s: data.SyntheticSet, real: list[tuple[np.ndarray, EdgeResult]]) -> dict:
    import torch
    from torch import nn

    rng = np.random.default_rng(0)
    torch.manual_seed(0)
    syn_idx = _samples(s, "c41", "personal")[: len(real)]
    xs, ys, groups = [], [], []
    for k, (image, _) in enumerate(real):
        for c in _crops(image, rng, 4):
            xs.append(c), ys.append(1.0), groups.append(k % 2)
    for k, i in enumerate(syn_idx):
        for c in _crops(s.images[i, : s.rows[i]], rng, 4):
            xs.append(c), ys.append(0.0), groups.append(k % 2)
    x = np.log2(np.stack(xs) + 0.5).transpose(0, 3, 1, 2)
    y = np.array(ys, dtype=np.float32)
    g = np.array(groups)
    layers: list[nn.Module] = []
    layers.append(nn.Conv2d(3, 16, kernel_size=3, padding=1))
    layers.append(nn.ReLU())
    layers.append(nn.MaxPool2d(2))
    layers.append(nn.Conv2d(16, 32, kernel_size=3, padding=1))
    layers.append(nn.ReLU())
    layers.append(nn.AdaptiveAvgPool2d(1))
    layers.append(nn.Flatten())
    layers.append(nn.Linear(32, 1))
    model = nn.Sequential(*layers)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    tx, ty = torch.from_numpy(x[g == 0].copy()), torch.from_numpy(y[g == 0])
    for _ in range(15):
        order = torch.randperm(len(tx))
        for b in range(0, len(tx), 32):
            sel = order[b: b + 32]
            optimizer.zero_grad()
            loss = nn.functional.binary_cross_entropy_with_logits(model(tx[sel]).squeeze(1), ty[sel])
            loss.backward()
            optimizer.step()
    with torch.no_grad():
        score = model(torch.from_numpy(x[g == 1].copy())).squeeze(1).numpy()
    truth = y[g == 1]
    pos, neg = score[truth == 1], score[truth == 0]
    auc = float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())
    return {"crops_test": int((g == 1).sum()), "auc": auc, "pass": auc <= 0.75}


def blind_sheet(s: data.SyntheticSet, real: list[tuple[np.ndarray, EdgeResult]]) -> dict:
    rng = np.random.default_rng(2026)
    picks = [("real", k) for k in rng.choice(len(real), 12, replace=False)]
    syn = _samples(s, "c41", "personal")
    picks += [("synthetic", int(i)) for i in rng.choice(syn, 12, replace=False)]
    order = rng.permutation(len(picks))
    tiles, key = [], []
    for n, j in enumerate(order):
        kind, k = picks[int(j)]
        image = real[k][0] if kind == "real" else s.images[k, : s.rows[k]]
        tiles.append(sheets.tile(image, "c41"))
        key.append({"tile": n + 1, "is": kind})
    sheets.sheet(tiles, OUT / "blind_c41.png")
    (OUT / "blind_c41_key.json").write_text(json.dumps(key, indent=1))
    for film_name, recipe_film in (("bw", "bw"), ("slide", "positive")):
        idx = _samples(s, film_name, "personal")[:12]
        sheets.sheet([sheets.tile(s.images[i, : s.rows[i]], recipe_film,
                                  truth=EdgeResult.from_json(s.rows_meta[i]["truth"]),
                                  target=s.targets[i]) for i in idx],
                     OUT / f"synthetic_{film_name}.png")
    return {"blind": str(OUT / "blind_c41.png"), "key": str(OUT / "blind_c41_key.json")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", type=Path, required=True)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    s = data.SyntheticSet(args.set)
    real_frames = _real_c41()
    calibration = json.loads((paths.LOCAL / "calibration.json").read_text())
    report: dict[str, Any] = {"set": str(args.set),
              "statistics": check_statistics(s, calibration["real_c41"]),
              "ensemble_v2": check_ensemble(s),
              "classifier": check_classifier(s, real_frames),
              "sheets": blind_sheet(s, real_frames)}
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=float))
    for name in ("statistics", "ensemble_v2", "classifier"):
        print(f"{name:12s} {'pass' if report[name]['pass'] else 'FAIL'}")
    print(json.dumps({k: v for k, v in report["ensemble_v2"].items() if k != "pass"}, indent=1))
    print("classifier AUC", round(report["classifier"]["auc"], 3),
          "| worst quantile", round(report["statistics"]["worst_quantile_stops"], 3), "stops",
          "| grain ratio", np.round(report["statistics"]["grain_ratio"], 2).tolist())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
