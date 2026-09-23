"""Score a detector against the ground truth, and against what needs no labels.

Called by `run.py`; on its own:

    uv run python research/frame-edge/evaluate.py results/<algo>/<iter>/results.json

**Per side** (left and right of every labelled frame):

* the confusion of states -- truth x answer;
* ``correct``: the right state, and for an edge within ``EDGE_TOL`` columns.
  A base narrower than ``THIN`` columns answered as "border", or the reverse,
  counts as correct: below a column the two are the same picture;
* ``false_base``: base claimed (at least ``THIN`` wide) where the truth is
  picture to the border -- the silhouette failure;
* ``missed``: the truth shows at least ``THIN`` columns of base, the detector
  says border;
* position error on edges both found, in columns and units.

**Per frame**, the move: both truth and answer go through `common.decide`
with the roll's frame width, and ``bad`` counts answers that are not a
refusal and are more than one smallest move (2.84 units) from the truth's.

**Without labels**: shifted pairs of one picture (`manifest.pairs`) must move
their edges by the measured shift; dups must not move at all; the ladder must
step linearly; a mirrored frame must give the mirrored answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    ALL_BASE, EDGE, GROUND_TRUTH, NO_FILM, PICTURE_TO_BORDER, REFUSE, SMALLEST_MOVE,
    UNREADABLE, EdgeResult, decide, frame_records, load_results, manifest, units_per_column,
)
from contact_sheet import truth_results  # noqa: E402

EDGE_TOL = 1.0
THIN = 1.0
DEFAULT_FRAME_WIDTH = 425.2   # 36.0 mm at 300 dpi


def _width_of(fid: str, rows: dict[str, dict[str, Any]]) -> int:
    return int(rows[fid]["shape"][1])


def _base(side_name: str, x: float, width: int) -> float:
    return x if side_name == "left" else width - x


def _state(s: Any) -> str:
    return s.state if s is not None else REFUSE


def score(results: dict[str, EdgeResult], mirrored: dict[str, EdgeResult] | None = None,
          split: str = "dev") -> dict[str, Any]:
    rows = {r["id"]: r for r in frame_records(kind="")}
    have_truth = GROUND_TRUTH.exists()
    gt_doc = json.loads(GROUND_TRUTH.read_text(encoding="utf-8")) if have_truth else {}
    widths = gt_doc.get("frame_width", {})
    truth = truth_results(split) if have_truth else {}
    out: dict[str, Any] = {"split": split}

    states = (EDGE, PICTURE_TO_BORDER, ALL_BASE, NO_FILM, REFUSE, UNREADABLE)
    confusion = {g: {p: 0 for p in states} for g in states}
    errs: list[float] = []
    per_film: dict[str, dict[str, list[float]]] = {}
    per_roll: dict[str, dict[str, list[float]]] = {}
    n = correct = false_base = missed = refused = 0
    worst: list[tuple[float, str]] = []
    dec_n = dec_ok = dec_bad = dec_refuse = dec_sign = 0
    du: list[float] = []
    for fid, t in truth.items():
        if fid not in results:
            continue
        p = results[fid]
        w = _width_of(fid, rows)
        film = rows[fid].get("film_type", "unknown")
        pf = per_film.setdefault(film, {"correct": [], "err": []})
        pr = per_roll.setdefault(rows[fid]["roll"], {"correct": [], "err": []})
        for name in ("left", "right"):
            g, a = t.side(name), p.side(name)
            if g.state == UNREADABLE:
                continue
            n += 1
            confusion[g.state][_state(a)] += 1
            ok = False
            if a.state == REFUSE:
                refused += 1
            elif g.state == EDGE and a.state == EDGE and a.x is not None and g.x is not None:
                e = float(a.x - g.x)
                errs.append(e)
                pf["err"].append(e)
                pr["err"].append(e)
                ok = abs(e) <= EDGE_TOL
                if not ok:
                    worst.append((abs(e), f"{fid} {name} off {e:+.1f} cols"))
            elif g.state == EDGE and a.state == PICTURE_TO_BORDER:
                assert g.x is not None
                gb = _base(name, g.x, w)
                ok = gb < THIN
                if not ok:
                    missed += 1
                    worst.append((gb, f"{fid} {name} missed {gb:.1f} cols of base"))
            elif g.state == PICTURE_TO_BORDER and a.state == EDGE and a.x is not None:
                ab = _base(name, a.x, w)
                ok = ab < THIN
                if not ok:
                    false_base += 1
                    worst.append((ab, f"{fid} {name} false base {ab:.1f} cols"))
            else:
                ok = g.state == a.state
                if not ok:
                    worst.append((5.0, f"{fid} {name} {a.state} where truth {g.state}"))
            correct += int(ok)
            pf["correct"].append(float(ok))
            pr["correct"].append(float(ok))
        fw = float(widths.get(rows[fid]["roll"], widths.get("_all", DEFAULT_FRAME_WIDTH)))
        dg, dp = decide(t, w, fw), decide(p, w, fw)
        if dg.action == "refuse" or dg.units is None:
            continue
        dec_n += 1
        if dp.action == "refuse" or dp.units is None:
            dec_refuse += 1
            continue
        d = float(dp.units - dg.units)
        du.append(d)
        if dp.action == dg.action and abs(d) <= SMALLEST_MOVE / 2:
            dec_ok += 1
        if abs(d) > SMALLEST_MOVE:
            dec_bad += 1
            worst.append((abs(d) / units_per_column(w), f"{fid} move {dp.caption()} vs "
                          f"truth {dg.caption()}"))
        if {dp.action, dg.action} == {"left", "right"}:
            dec_sign += 1

    ae = np.abs(np.array(errs)) if errs else np.array([np.nan])
    upc = units_per_column()
    out["side"] = {
        "n": n, "correct": correct, "accuracy": round(correct / max(n, 1), 3),
        "false_base": false_base, "missed": missed, "refused": refused,
        "edge_n": len(errs),
        "edge_mae_cols": round(float(np.nanmean(ae)), 3),
        "edge_p90_cols": round(float(np.nanpercentile(ae, 90)), 3),
        "edge_max_cols": round(float(np.nanmax(ae)), 3),
        "edge_bias_cols": round(float(np.mean(errs)), 3) if errs else None,
        "edge_mae_units": round(float(np.nanmean(ae)) * upc, 3),
        "confusion": confusion,
    }
    adu = np.abs(np.array(du)) if du else np.array([np.nan])
    out["decision"] = {
        "n": dec_n, "ok": dec_ok, "bad": dec_bad, "refused": dec_refuse, "sign_errors": dec_sign,
        "accuracy": round(dec_ok / max(dec_n, 1), 3),
        "du_mae_units": round(float(np.nanmean(adu)), 3),
        "du_p90_units": round(float(np.nanpercentile(adu, 90)), 3),
    }
    out["per_film"] = {
        k: {"accuracy": round(float(np.mean(v["correct"])), 3) if v["correct"] else None,
            "edge_mae_cols": round(float(np.mean(np.abs(v["err"]))), 3) if v["err"] else None,
            "n": len(v["correct"])}
        for k, v in per_film.items()}
    out["per_roll"] = {
        k: {"accuracy": round(float(np.mean(v["correct"])), 3) if v["correct"] else None,
            "edge_mae_cols": round(float(np.mean(np.abs(v["err"]))), 3) if v["err"] else None,
            "n": len(v["correct"])}
        for k, v in per_roll.items()}
    out["worst"] = [w for _, w in sorted(worst, reverse=True)[:25]]
    out["label_free"] = label_free(results, mirrored, rows, split)
    return out


def label_free(results: dict[str, EdgeResult], mirrored: dict[str, EdgeResult] | None,
               rows: dict[str, dict[str, Any]], split: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # shifted pairs of one picture
    pair_err: list[float] = []
    pair_state = 0
    pair_n = 0
    for pr in manifest().get("pairs", []):
        a, b = pr["a"], pr["b"]
        if a not in results or b not in results or rows[a]["split"] != split:
            continue
        for name in ("left", "right"):
            sa, sb = results[a].side(name), results[b].side(name)
            if sa.state == EDGE and sb.state == EDGE and sa.x is not None and sb.x is not None:
                pair_err.append(float((sb.x - sa.x) - pr["dx"]))
            elif sa.state != sb.state:
                pair_state += 1
            pair_n += 1
    pe = np.abs(np.array(pair_err)) if pair_err else np.array([np.nan])
    out["pairs"] = {"n": pair_n, "both_edge": len(pair_err), "state_changes": pair_state,
                    "mae_cols": round(float(np.nanmean(pe)), 3),
                    "p90_cols": round(float(np.nanpercentile(pe, 90)), 3),
                    "over_1_5": int(np.sum(pe > 1.5))}
    # dups: same position, other exposure / depth / file
    dup_err: list[float] = []
    dup_state = 0
    dup_n = 0
    for fid, r in rows.items():
        if r.get("kind") != "dup" or r["split"] != split or not r.get("same_as"):
            continue
        rep = r["same_as"]
        if fid not in results or rep not in results:
            continue
        for name in ("left", "right"):
            s1, s2 = results[rep].side(name), results[fid].side(name)
            dup_n += 1
            if s1.state == EDGE and s2.state == EDGE and s1.x is not None and s2.x is not None:
                dup_err.append(abs(float(s2.x - s1.x)))
            elif s1.state != s2.state:
                dup_state += 1
    de = np.array(dup_err) if dup_err else np.array([np.nan])
    out["dups"] = {"n": dup_n, "state_changes": dup_state,
                   "mae_cols": round(float(np.nanmean(de)), 3),
                   "over_1": int(np.sum(de > 1.0))}
    # the ladder
    lad = sorted((r["index"], fid) for fid, r in rows.items() if r.get("kind") == "ladder")
    ladder: dict[str, Any] = {}
    for name in ("left", "right"):
        pts = [(i, results[f].side(name).x) for i, f in lad
               if f in results and results[f].side(name).state == EDGE]
        if len(pts) >= 3:
            xs = np.array([p[0] for p in pts], float)
            ys = np.array([p[1] for p in pts], float)
            slope, icpt = np.polyfit(xs, ys, 1)
            res = ys - (slope * xs + icpt)
            ladder[name] = {"found": len(pts), "of": len(lad), "slope_cols": round(float(slope), 3),
                            "resid_mad": round(float(np.median(np.abs(res))), 3),
                            "resid_max": round(float(np.max(np.abs(res))), 3)}
        else:
            ladder[name] = {"found": len(pts), "of": len(lad)}
    out["ladder"] = ladder
    # mirror
    if mirrored:
        mm_state = 0
        mm_err: list[float] = []
        for fid, m in mirrored.items():
            if fid not in results:
                continue
            w = int(rows[fid]["shape"][1])
            for name, other in (("left", "right"), ("right", "left")):
                s, ms = results[fid].side(name), m.side(other)
                if s.state == EDGE and ms.state == EDGE and s.x is not None and ms.x is not None:
                    mm_err.append(abs(float(s.x - (w - ms.x))))
                elif s.state != ms.state:
                    mm_state += 1
        me = np.array(mm_err) if mm_err else np.array([np.nan])
        out["mirror"] = {"state_changes": mm_state, "mae_cols": round(float(np.nanmean(me)), 3),
                         "over_0_5": int(np.sum(me > 0.5))}
    return out


def summary_line(algo: str, it: str, s: dict[str, Any]) -> str:
    sd, dc, lf = s["side"], s["decision"], s["label_free"]
    return (f"{algo} {it} [{s['split']}]  side acc {sd['accuracy']:.2f} ({sd['correct']}/{sd['n']})"
            f"  edge MAE {sd['edge_mae_cols']:.2f} p90 {sd['edge_p90_cols']:.2f} cols"
            f"  false-base {sd['false_base']} missed {sd['missed']} refused {sd['refused']}"
            f"  | move ok {dc['ok']}/{dc['n']} bad {dc['bad']} sign {dc['sign_errors']}"
            f" refused {dc['refused']}"
            f"  | pairs MAE {lf['pairs']['mae_cols']} dups MAE {lf['dups']['mae_cols']}"
            f" mirror {lf.get('mirror', {}).get('state_changes', '-')}")


def main() -> None:
    path = Path(sys.argv[1])
    path = path if path.is_absolute() else HERE / path
    results = load_results(path)
    mpath = path.with_name(path.name.replace("results", "mirrored"))
    mirrored = load_results(mpath) if mpath.exists() else None
    split = json.loads(path.read_text(encoding="utf-8"))["about"].get("split", "dev")
    s = score(results, mirrored, split)
    (path.with_name(path.name.replace("results", "scores"))).write_text(
        json.dumps(s, indent=1), encoding="utf-8")
    print(summary_line(path.parent.parent.name, path.parent.name, s))
    for w in s["worst"][:15]:
        print("  ", w)


if __name__ == "__main__":
    main()
