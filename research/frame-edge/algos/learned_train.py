"""Fit the `learned` detector's pixel classifier, and validate it leave-one-roll-out.

    uv run --no-sync python algos/learned_train.py              # LORO table, then final fit
    uv run --no-sync python algos/learned_train.py --final-only # skip the table

Reads the **dev** ground truth only (`ground_truth.json`, frames whose split is
``dev``); the test rolls are never loaded. Writes `learned_weights.json`
beside this file.

Pixel labels, per side, in that side's own coordinates (border at column 0,
the right side mirrored):

* ``edge`` at ``x``: columns wholly inside ``[outer, x)`` are base, those
  beyond ``x`` picture; one column either side of ``x`` (and of ``outer``) is
  left out -- it is part base, part picture. A labelled tilt (``x_top``,
  ``x_bottom``) moves the boundary per row.
* ``picture_to_border``: every column picture -- this is where the dark
  scene areas are, the negatives the classifier most needs.
* ``all_base``: every column base. ``no_film`` / ``unreadable``: the side is
  left out; the gate is handled by rule, not learned.

Only ``film`` frames and their ``dup``s: the ``slide`` kind (x0920_01..09,
positive film) is not what this detector is for and is never trained on.

Only the outer ``K_TRAIN`` columns of each side are used, rows subsampled.
**Dups** (the same position at another exposure or bit depth) take their
representative's label: they are the scale variation the features must be
blind to. Each physical position weighs the same however many dups it has,
and base and not-base pixels weigh the same in total.

Validation is **leave one roll out**: train on every other dev roll, run the
whole detector on the held-out roll's labelled frames, score sides the way
`evaluate.py` does (state right; an edge within 1 column; under a column of
base and "border" count as the same). Only then are the final weights fitted
on every dev roll.

The table behind the shipped weights (iter04; 23 features, 12 hidden, one
jittered-reference copy per image, 7.07 M pixels). "pixel" is the held-out
roll's pixel recall, base / picture; "sides" the whole detector's:

    held out   pixels   base   pic    sides   refused  false-base  missed
    bw0910     618k     0.542  1.000  13/16   0        0           3
    r0909      638k     0.993  0.998  20/20   0        0           0
    r0911      1657k    0.977  0.893  49/55   5        0           1
    r0914      1882k    0.894  0.998  50/54   1        0           3
    stage3     236k     1.000  0.999   6/6    0        0           0
    strip3     177k     1.000  1.000   6/6    0        0           0
    strip6     176k     0.889  1.000   5/6    0        0           1
    x0920      1687k    1.000  0.987  28/30   2        0           0
    total                            177/193  8        0           8   (0.917)
    without x0920                    149/163                           (0.914)

x0920 is listed apart because it is 15 labelled representatives of exposure
ladders, not a walk: its dups are the same pictures at other exposures, so
it rewards scale-blindness more than a real roll would. It scores like the
rest (28/30 against 149/163), so it is not flattering the total. bw0910 is the
only black-and-white roll: held out, the network has never seen neutral base,
and its three misses are base beside a night sky 1 count away -- in-sample
those are refused, not found. Scored by `evaluate.py` as a whole (every dev
frame answered by the fold that never saw its roll), the moves come out
77/94 ok, 9 bad, 8 refused, no sign error: `results/learned/iter04/loro_*`.

What the table moved through (LORO sides, same scorer):

* 147/193 -- first fit, three distance-from-border features, per-row runs
  checked for agreement only. 15 false bases: on r0911's dark frames the
  network's confidence faded at one column in every row.
* 174 -- each row's run end must sit on a 5-sigma step down (false bases 0).
* 171 / 167 -- bands searched to mid-frame; then without the connectivity
  features: finds r0914_20's interior gap, and the museum walls with it.
* 175 -- search kept to 48 columns, rows through the line allowed, the best
  band rather than the first; the sliver reading; no distance or
  connectivity features (the stage9b ladder tracked 7 columns a step).
* 170 -- a noise-scaled "at base" tolerance: dropped.
* 177 -- the jittered reference (strip6 base recall 0.65 -> 0.89).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import learned  # noqa: E402
from common import (  # noqa: E402
    ALL_BASE, EDGE, GROUND_TRUTH, PICTURE_TO_BORDER, REFUSE, frame_records, load,
)

K_TRAIN = 214          # columns from each border learned from: the half a band is searched in
ROW_STRIDE = 4         # every fourth used row of a representative frame
HIDDEN = 12            # hidden units; 0 = plain logistic regression
EPOCHS = 6
BATCH = 4096
LR = 3e-3
L2 = 1e-4
SEED = 7
TEST_ROLLS = ("gold200", "hwcheck")
#: The reference is an estimate -- the median of a frame's three brightest
#: flat columns -- and it misses: strip6's picture is brighter than its base
#: in 4-7% of pixels, a one-column base (x0920_10) is outvoted by the picture.
#: Training copies against a reference scaled by this factor (and +-3% per
#: channel) teach the network not to lean on its exact value.
REF_JITTER = (0.88, 1.12)
JITTER = 1


def truth() -> dict[str, dict[str, Any]]:
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["frames"]
    return {k: v for k, v in gt.items() if v.get("split") == "dev"
            and v.get("roll") not in TEST_ROLLS}


def labelled_images() -> list[dict[str, Any]]:
    """Dev film frames with truth, and their dups carrying the same truth."""
    gt = truth()
    rows = [r for r in frame_records(kind="") if r["split"] == "dev"
            and r["roll"] not in TEST_ROLLS and r["kind"] in ("film", "dup")]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rep = r["id"] if r["kind"] == "film" else r.get("same_as")
        if rep in gt:
            groups.setdefault(rep, []).append(r)
    out = []
    for rep, members in groups.items():
        for r in members:
            out.append({"id": r["id"], "rep": rep, "roll": r["roll"], "kind": r["kind"],
                        "film_type": r.get("film_type", "unknown"), "gt": gt[rep],
                        "group": len(members)})
    return out


def side_labels(g: dict[str, Any], side: str, h: int, w: int) -> np.ndarray | None:
    """(h, K) float labels 1 base / 0 picture / nan ignore, side coordinates."""
    s = g.get(side) or {}
    state = s.get("state")
    k = min(K_TRAIN, w)
    c = np.arange(k) + 0.5
    if state == PICTURE_TO_BORDER:
        return np.zeros((h, k))
    if state == ALL_BASE:
        return np.ones((h, k))
    if state != EDGE or s.get("x") is None:
        return None

    def own(v: float | None) -> float | None:
        if v is None:
            return None
        return float(v) if side == "left" else w - float(v)

    x = own(s["x"])
    xt, xb = own(s.get("x_top")), own(s.get("x_bottom"))
    if xt is not None and xb is not None:
        xr = xt + (xb - xt) * np.arange(h) / max(h - 1, 1)
    else:
        xr = np.full(h, x)
    out = np.full((h, k), np.nan)
    base = c[None] < xr[:, None] - 1.0
    pic = c[None] > xr[:, None] + 1.0
    outer = own(s.get("outer"))
    if outer is not None:
        base &= c[None] > outer + 1.0
        pic |= c[None] < outer - 1.0
    out[base] = 1.0
    out[pic] = 0.0
    return out


def build(items: list[dict[str, Any]], rng: np.random.Generator, jitter: int = JITTER
          ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Features X, labels y, weights wt, and roll index per sample.

    With ``jitter`` > 0 each image is also featurised that many more times
    against a *perturbed* reference (see REF_JITTER), each copy weighing the
    same as the original in total.
    """
    Xs, ys, ws, rs = [], [], [], []
    rolls = sorted({it["roll"] for it in items})
    for it in items:
        fr = load(it["id"])
        prep = learned.prepare(fr.image, it["film_type"])
        # a dup is subsampled harder: each physical position counts once
        stride = ROW_STRIDE * max(1, int(round(np.sqrt(it["group"]))))
        refs = [prep["ref"]]
        for _ in range(jitter):
            g = rng.uniform(*REF_JITTER) * rng.uniform(0.97, 1.03, 3)
            refs.append(prep["ref"] * g)
        for name, img, gate in learned.side_views(prep):
            h, w, _ = img.shape
            lab = side_labels(it["gt"], name, h, w)
            if lab is None:
                continue
            k = lab.shape[1]
            for ref in refs:
                F = learned.features(img, ref, gate)[:, :k]
                off = int(rng.integers(stride))
                rows = np.arange(off, h, stride)
                Fr, lr = F[rows], lab[rows]
                keep = ~np.isnan(lr) & ~gate[None, :k]
                X = Fr[keep]
                y = lr[keep]
                if y.size == 0:
                    continue
                Xs.append(X)
                ys.append(y)
                ws.append(np.full(y.size, 1.0 / (it["group"] * y.size) * (k * len(rows))
                                  / len(refs)))
                rs.append(np.full(y.size, rolls.index(it["roll"])))
    X = np.concatenate(Xs).astype(np.float64)
    y = np.concatenate(ys)
    wt = np.concatenate(ws)
    # base and not-base weigh the same in total
    for cls in (0.0, 1.0):
        m = y == cls
        wt[m] *= 0.5 / wt[m].sum()
    wt *= y.size
    return X, y, wt, np.concatenate(rs)


def fit(X: np.ndarray, y: np.ndarray, wt: np.ndarray, hidden: int = HIDDEN,
        epochs: int = EPOCHS, seed: int = SEED) -> dict[str, Any]:
    """Weighted cross-entropy, Adam, minibatches. Returns the weights dict."""
    rng = np.random.default_rng(seed)
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-6
    Z = (X - mu) / sd
    n, f = Z.shape
    if hidden:
        params = {"W1": rng.normal(0, 1.0 / np.sqrt(f), (f, hidden)),
                  "b1": np.zeros(hidden),
                  "W2": rng.normal(0, 1.0 / np.sqrt(hidden), hidden),
                  "b2": np.zeros(1)}
    else:
        params = {"W2": np.zeros(f), "b2": np.zeros(1)}
    m = {k: np.zeros_like(v) for k, v in params.items()}
    v = {k: np.zeros_like(v) for k, v in params.items()}
    b1_, b2_, eps = 0.9, 0.999, 1e-8
    t = 0
    for _ep in range(epochs):
        order = rng.permutation(n)
        for i in range(0, n, BATCH):
            idx = order[i:i + BATCH]
            zb, yb, wb = Z[idx], y[idx], wt[idx]
            if hidden:
                a1 = zb @ params["W1"] + params["b1"]
                h1 = np.tanh(a1)
            else:
                h1 = zb
            logit = h1 @ params["W2"] + params["b2"][0]
            p = 1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30)))
            g = wb * (p - yb) / wb.sum()
            grads = {"W2": h1.T @ g + L2 * params["W2"], "b2": np.array([g.sum()])}
            if hidden:
                gh = np.outer(g, params["W2"]) * (1.0 - h1 ** 2)
                grads["W1"] = zb.T @ gh + L2 * params["W1"]
                grads["b1"] = gh.sum(axis=0)
            t += 1
            for k in params:
                m[k] = b1_ * m[k] + (1 - b1_) * grads[k]
                v[k] = b2_ * v[k] + (1 - b2_) * grads[k] ** 2
                mh = m[k] / (1 - b1_ ** t)
                vh = v[k] / (1 - b2_ ** t)
                params[k] -= LR * mh / (np.sqrt(vh) + eps)
    out: dict[str, Any] = {"mu": mu.tolist(), "sd": sd.tolist(),
                           "b2": float(params["b2"][0]), "W2": params["W2"].tolist()}
    if hidden:
        out["W1"] = params["W1"].tolist()
        out["b1"] = params["b1"].tolist()
    else:   # logistic regression as a degenerate net: identity hidden layer
        out["W1"] = np.eye(f).tolist()
        out["b1"] = np.zeros(f).tolist()
        out["linear"] = True
    return out


def logit_of(X: np.ndarray, wts: dict[str, Any]) -> np.ndarray:
    Z = (X - np.asarray(wts["mu"])) / np.asarray(wts["sd"])
    h = Z @ np.asarray(wts["W1"]) + np.asarray(wts["b1"])
    if not wts.get("linear"):
        h = np.tanh(h)
    return h @ np.asarray(wts["W2"]) + wts["b2"]


def pixel_scores(X: np.ndarray, y: np.ndarray, wts: dict[str, Any]) -> dict[str, float]:
    pred = logit_of(X, wts) > 0
    tpr = float(np.mean(pred[y == 1])) if np.any(y == 1) else float("nan")
    tnr = float(np.mean(~pred[y == 0])) if np.any(y == 0) else float("nan")
    return {"base_recall": round(tpr, 4), "picture_recall": round(tnr, 4),
            "balanced": round(float(np.nanmean([tpr, tnr])), 4)}


def side_score(items: list[dict[str, Any]], wts: dict[str, Any]) -> dict[str, Any]:
    """Run the whole detector on the labelled (film) frames; score like evaluate.py."""
    n = ok = refused = false_base = missed = 0
    misses: list[str] = []
    errs: list[float] = []
    for it in items:
        if it["kind"] != "film":
            continue
        fr = load(it["id"])
        res = learned.detect(fr.image, {"id": it["id"], "film_type": it["film_type"]}, wts)
        w = fr.image.shape[1]
        for name in ("left", "right"):
            g = it["gt"].get(name) or {}
            gs = g.get("state", "unreadable")
            if gs == "unreadable":
                continue
            a = res.side(name)
            n += 1
            good = False
            if a.state == REFUSE:
                refused += 1
            elif gs == EDGE and a.state == EDGE:
                e = float(a.x - g["x"])
                errs.append(e)
                good = abs(e) <= 1.0
            elif gs == EDGE and a.state == PICTURE_TO_BORDER:
                b = g["x"] if name == "left" else w - g["x"]
                good = b < 1.0
                missed += int(not good)
            elif gs == PICTURE_TO_BORDER and a.state == EDGE and a.x is not None:
                b = a.x if name == "left" else w - a.x
                good = b < 1.0
                false_base += int(not good)
            else:
                good = gs == a.state
            ok += int(good)
            if not good:
                what = (f"{a.state} {a.x:.1f}" if a.x is not None else a.state)
                gw = (f"{gs} {g['x']:.1f}" if g.get("x") is not None else gs)
                misses.append(f"{it['id']} {name}: {what} vs {gw}")
    e = np.abs(np.array(errs)) if errs else np.array([np.nan])
    return {"sides": n, "correct": ok, "acc": round(ok / max(n, 1), 3), "refused": refused,
            "false_base": false_base, "missed": missed,
            "edge_mae": round(float(np.nanmean(e)), 3), "misses": misses}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--final-only", action="store_true")
    ap.add_argument("--hidden", type=int, default=HIDDEN)
    ap.add_argument("--out", type=Path, default=learned.WEIGHTS)
    ap.add_argument("--drop", default="", help="comma-separated features to leave out")
    ap.add_argument("--jitter", type=int, default=JITTER,
                    help="extra copies of each image against a perturbed reference")
    ap.add_argument("--report", type=Path, default=None,
                    help="write the LORO table as JSON here")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    items = labelled_images()
    rolls = sorted({it["roll"] for it in items})
    t0 = time.perf_counter()
    X, y, wt, r = build(items, rng, args.jitter)
    drop = [d for d in args.drop.split(",") if d]
    use = [n for n in learned.FEATURES if n not in drop]
    X = X[:, [learned.FEATURES.index(n) for n in use]]
    print(f"{len(items)} images, {X.shape[0]} pixels, {X.shape[1]} features, "
          f"{np.mean(y):.3f} base  [{time.perf_counter() - t0:.1f} s]", flush=True)
    table: dict[str, Any] = {}
    if not args.final_only:
        tot = {"sides": 0, "correct": 0, "refused": 0, "false_base": 0, "missed": 0}
        for i, roll in enumerate(rolls):
            tr = r != i
            wts = fit(X[tr], y[tr], wt[tr], args.hidden)
            wts["use"] = use
            px = pixel_scores(X[~tr], y[~tr], wts)
            held = [it for it in items if it["roll"] == roll]
            sc = side_score(held, wts)
            table[roll] = {"pixels": int((~tr).sum()), **px, **sc}
            if args.report:
                fold = args.report.with_name(f"{args.report.stem}_fold_{roll}.json")
                fold.write_text(json.dumps(wts), encoding="utf-8")
            for k in tot:
                tot[k] += sc[k]
            print(f"  hold out {roll:8s} px {int((~tr).sum()):7d}  base {px['base_recall']:.3f}"
                  f"  pic {px['picture_recall']:.3f}  | sides {sc['correct']}/{sc['sides']}"
                  f"  refused {sc['refused']} false-base {sc['false_base']}"
                  f" missed {sc['missed']} edge MAE {sc['edge_mae']}", flush=True)
            for mline in sc["misses"]:
                print("      ", mline)
        table["_total"] = dict(tot, acc=round(tot["correct"] / max(tot["sides"], 1), 3))
        print("  LORO total", table["_total"], flush=True)
        if args.report:
            args.report.write_text(json.dumps(table, indent=1), encoding="utf-8")
    wts = fit(X, y, wt, args.hidden)
    wts["use"] = use
    wts["about"] = {"trained_on": rolls, "pixels": int(X.shape[0]), "hidden": args.hidden,
                    "epochs": EPOCHS, "k_train": K_TRAIN, "train_pixel": pixel_scores(X, y, wts),
                    "loro": {k: {kk: vv for kk, vv in v.items() if kk != "misses"}
                             for k, v in table.items()}}
    args.out.write_text(json.dumps(wts), encoding="utf-8")
    print("final", wts["about"]["train_pixel"], "->", args.out)


if __name__ == "__main__":
    main()
