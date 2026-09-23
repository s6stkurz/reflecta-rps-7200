"""Merge the labellers' files into one ground truth, and say where they disagree.

    uv run python research/frame-edge/merge_labels.py

Reads `labels/<who>/<id>.json` (L1..L6 the first two passes, R* any third
look, S* Stefan's own corrections, which decide over everything) and writes
`ground_truth.json`.

Each `edge` label is **snapped**: within +-3 columns of the eye's `x`, every
row's 50% crossing between the local base level and the local picture level is
found on the channel with the most contrast, and the median over rows replaces
the eye's value -- but only if it moves it by 1.5 columns or less. The eye
decides *which* transition is the edge; the snap only places it.

Two labels agree when their states match and, for edges, the snapped
positions are within ``AGREE`` columns. Agreeing labels are averaged. A
disagreement is flagged and needs a third look (a file under `labels/R*/`),
which then decides.
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
    EDGE, GROUND_TRUTH, GROUND_TRUTH_TEST, UNREADABLE, WORK, frame_records, load,
    pitch_columns, trimmed,
)

LABELS = WORK / "labels"
ANCHORS = WORK / "anchors.json"
AGREE = 1.0
SNAP_RADIUS = 3
SNAP_MAX_MOVE = 1.5
MIN_WIDTH_READINGS = 3
NOMINAL_FRAME_WIDTH = 425.2   # 36.0 mm at 300 dpi, `framing.FRAME_WIDTH_MM`
APERTURE_FLOOR = 430.0        # 428 + one column each side: frames showing no base anywhere


def snap(image: np.ndarray, side: str, x_eye: float) -> float | None:
    """The per-row 50% crossing near ``x_eye``, median over rows; None if unclear."""
    img = trimmed(image).astype(np.float64)
    h, w = img.shape[:2]
    if side == "right":
        img = img[:, ::-1]
        x_eye = w - x_eye
    # now base is on the left, columns [0, x_eye)
    e = int(round(x_eye))
    base_cols = list(range(max(0, e - 6), max(0, e - 1)))
    if not base_cols:
        base_cols = [0] if e >= 1 else []
    pic_cols = list(range(min(w - 1, e + 1), min(w, e + 7)))
    if not base_cols or not pic_cols:
        return None
    base = np.median(img[:, base_cols], axis=1)      # (h, 3)
    pic = np.median(img[:, pic_cols], axis=1)
    contrast = base - pic
    noise = np.median(np.abs(np.diff(img[:, base_cols], axis=0)), axis=(0, 1)) + 1e-6
    ch = int(np.argmax(np.median(contrast, axis=0) / noise))
    lo = max(0, e - SNAP_RADIUS - 1)
    hi = min(w - 1, e + SNAP_RADIUS)
    crossings = []
    for r in range(h):
        c_r = contrast[r, ch]
        if c_r < 3.0 * noise[ch] * 1.4826:
            continue
        thr = (base[r, ch] + pic[r, ch]) / 2.0
        row = img[r, :, ch]
        for c in range(lo, hi):
            v1, v2 = row[c], row[c + 1]
            if v1 >= thr > v2:
                crossings.append(c + 0.5 + (v1 - thr) / (v1 - v2))
                break
    if len(crossings) < 0.3 * h:
        return None
    x = float(np.median(crossings))
    return w - x if side == "right" else x


def _labels() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for who in sorted(p for p in LABELS.iterdir() if p.is_dir()):
        for f in sorted(who.glob("*.json")):
            lab = json.loads(f.read_text(encoding="utf-8"))
            lab["labeller"] = who.name
            out.setdefault(lab["id"], []).append(lab)
    return out


def _side_value(lab: dict[str, Any], side: str, image: np.ndarray) -> dict[str, Any]:
    s = dict(lab[side])
    s["labeller"] = lab["labeller"]
    s["x_eye"] = s.get("x")
    if s.get("state") == EDGE and s.get("x") is not None:
        snapped = snap(image, side, float(s["x"]))
        s["x_snap"] = snapped
        if snapped is not None and abs(snapped - float(s["x"])) <= SNAP_MAX_MOVE:
            s["x"] = snapped
    return s


def merge() -> dict[str, Any]:
    labs = _labels()
    records = {r["id"]: r for r in frame_records(kind="film")}
    frames: dict[str, Any] = {}
    flags: list[str] = []
    pairs_same_state = 0
    pairs_total = 0
    diffs: list[float] = []
    for fid, rec in records.items():
        entries = labs.get(fid, [])
        if not entries:
            flags.append(f"{fid}: no labels")
            continue
        image = load(fid).image
        first = [e for e in entries if e["labeller"].startswith("L")]
        # Stefan's own corrections (S*) decide over any third look (R*): his
        # eye is the acceptance test.
        stefan = [e for e in entries if e["labeller"].startswith("S")]
        third = stefan or [e for e in entries if e["labeller"].startswith("R")]
        out: dict[str, Any] = {"split": rec["split"], "roll": rec["roll"], "flags": []}
        for side in ("left", "right"):
            vals = [_side_value(e, side, image) for e in first]
            # a third look names only the side it decides; the other is null
            decider = [_side_value(e, side, image) for e in third
                       if (e.get(side) or {}).get("state")]
            merged: dict[str, Any]
            if len(vals) >= 2:
                pairs_total += 1
                a, b = vals[0], vals[1]
                same = a.get("state") == b.get("state")
                pairs_same_state += int(same)
                agree = same and (a.get("state") != EDGE or abs(a["x"] - b["x"]) <= AGREE)
                if same and a.get("state") == EDGE:
                    diffs.append(abs(a["x"] - b["x"]))
            else:
                agree = False
            if decider:
                merged = _combine(decider + [v for v in vals if _same(v, decider[0])])
                merged["resolved_by"] = decider[0]["labeller"]
            elif agree:
                merged = _combine(vals)
            else:
                merged = _combine(vals[:1]) if vals else {"state": UNREADABLE}
                merged["flag"] = "disagree" if len(vals) >= 2 else "single label"
                out["flags"].append(side)
                flags.append(f"{fid} {side}: " + " vs ".join(
                    f"{v['labeller']} {v.get('state')} {v.get('x')}" for v in vals))
            merged["labels"] = vals + decider
            out[side] = merged
        frames[fid] = out
    kappa_like = pairs_same_state / max(pairs_total, 1)
    about = {
        "what": "Ground truth for the frame-edge study, merged from labels/*/ by merge_labels.py",
        "state_agreement": round(kappa_like, 3),
        "edge_position_mae_cols": round(float(np.mean(diffs)), 3) if diffs else None,
        "edge_position_p90_cols": round(float(np.percentile(diffs, 90)), 3) if diffs else None,
        "n_frames": len(frames), "n_flags": len(flags),
    }
    return {"about": about, "frames": frames, "flags": flags}


def _same(v: dict[str, Any], d: dict[str, Any]) -> bool:
    if v.get("state") != d.get("state"):
        return False
    return v.get("state") != EDGE or abs(v["x"] - d["x"]) <= AGREE


def _combine(vals: list[dict[str, Any]]) -> dict[str, Any]:
    state = vals[0].get("state")
    out: dict[str, Any] = {"state": state}
    if state == EDGE:
        xs = [float(v["x"]) for v in vals if v.get("x") is not None]
        out["x"] = float(np.mean(xs))
        los = [float(v["lo"]) for v in vals if v.get("lo") is not None]
        his = [float(v["hi"]) for v in vals if v.get("hi") is not None]
        out["lo"] = min(los) if los else out["x"] - 0.5
        out["hi"] = max(his) if his else out["x"] + 0.5
        for key in ("outer", "x_top", "x_bottom"):
            got = [float(v[key]) for v in vals if v.get(key) is not None]
            out[key] = float(np.mean(got)) if got else None
    return out


def frame_widths(gt: dict[str, Any]) -> dict[str, Any]:
    """Per roll, the frame's width in columns, from what the labels can show.

    Directly where a frame shows base at both sides (``right.x - left.x``);
    otherwise through the gap, where a neighbour's picture shows beyond it:
    frame = pitch - gap. The median of both per roll; ``_all`` over every
    roll, and the evidence behind each.
    """
    pitch = pitch_columns()
    per_roll: dict[str, list[float]] = {}
    evidence: dict[str, list[str]] = {}
    for fid, f in gt["frames"].items():
        left, right = f.get("left") or {}, f.get("right") or {}
        got: list[tuple[float, str]] = []
        if left.get("state") == EDGE and right.get("state") == EDGE:
            got.append((right["x"] - left["x"], f"{fid} both edges"))
        for name in ("left", "right"):
            s = f.get(name) or {}
            if s.get("state") == EDGE and s.get("outer") is not None:
                gap = abs(s["x"] - s["outer"])
                got.append((pitch - gap, f"{fid} {name} gap {gap:.1f}"))
        for value, why in got:
            per_roll.setdefault(f["roll"], []).append(value)
            evidence.setdefault(f["roll"], []).append(f"{why} -> {value:.1f}")
    # A roll gets its own width only on MIN_WIDTH_READINGS or more readings;
    # otherwise the median of every reading, never below the aperture. The
    # floor is the strongest evidence there is: 45 of 98 dev frames show no
    # base on either side, which a frame narrower than the 428-column aperture
    # cannot do -- the nominal 36.0 mm (425.2 columns) the software assumes is
    # contradicted by the film. The readings above it are few (two here, one
    # a "gap" a labeller doubted) and go through a pitch of 455 columns that is
    # itself 1.4% off the 38.0 mm standard, so this is the least-bad number,
    # not a measurement. Truth and detector are decided with the same width:
    # it moves the absolute answer, not the comparison.
    out: dict[str, Any] = {k: round(max(float(np.median(v)), APERTURE_FLOOR), 2)
                           for k, v in per_roll.items() if len(v) >= MIN_WIDTH_READINGS}
    every = [x for v in per_roll.values() for x in v]
    out["_all"] = round(max(float(np.median(every)), APERTURE_FLOOR), 2) if every \
        else NOMINAL_FRAME_WIDTH
    out["_measured"] = {k: [round(x, 1) for x in v] for k, v in per_roll.items()}
    out["_evidence"] = evidence
    return out


def pair_check(gt: dict[str, Any]) -> dict[str, Any]:
    """The labels against the measured shifts: an edge must move with its picture."""
    from common import manifest
    rows: list[dict[str, Any]] = []
    for pr in manifest().get("pairs", []):
        a, b = gt["frames"].get(pr["a"]), gt["frames"].get(pr["b"])
        if not a or not b:
            continue
        for name in ("left", "right"):
            sa, sb = a.get(name) or {}, b.get(name) or {}
            if sa.get("state") == EDGE and sb.get("state") == EDGE:
                rows.append({"a": pr["a"], "b": pr["b"], "side": name, "dx": pr["dx"],
                             "err": round(sb["x"] - sa["x"] - pr["dx"], 2)})
    errs = [abs(float(r["err"])) for r in rows]
    return {"n": len(rows), "mae": round(float(np.mean(errs)), 3) if errs else None,
            "max": round(float(np.max(errs)), 3) if errs else None,
            "bad": [r for r in rows if abs(float(r["err"])) > 1.5]}


def main() -> None:
    everything = merge()
    # The test split goes to its own file: dev work must never print it.
    test = {k: v for k, v in everything["frames"].items() if v.get("split") == "test"}
    gt = dict(everything)
    gt["frames"] = {k: v for k, v in everything["frames"].items() if v.get("split") != "test"}
    gt["flags"] = [f for f in everything["flags"] if f.split()[0].rstrip(":") not in test]
    gt["frame_width"] = frame_widths(gt)
    gt["pair_check"] = pair_check(gt)
    print("frame width:", {k: v for k, v in gt["frame_width"].items() if k != "_evidence"})
    print("pair check:", {k: v for k, v in gt["pair_check"].items() if k != "bad"})
    for b in gt["pair_check"]["bad"]:
        print("  PAIR", b)
    locked: dict[str, Any] = {"about": "test split truth -- final scoring only", "frames": test,
                              "frame_width": gt["frame_width"]}
    # The labels' own consistency is checked on the test split too: a
    # dataset that is all test (a second library) would otherwise get none.
    # Only the summary is printed; the rows stay in the locked file.
    locked["pair_check"] = pair_check(locked)
    print("pair check (locked):",
          {k: v for k, v in locked["pair_check"].items() if k != "bad"},
          f"bad: {len(locked['pair_check']['bad'])}")
    if ANCHORS.exists():
        gt["anchors_check"] = check_anchors(gt)
        locked["anchors_check"] = check_anchors(locked)
    GROUND_TRUTH.write_text(json.dumps(gt, indent=1), encoding="utf-8")
    GROUND_TRUTH_TEST.parent.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_TEST.write_text(json.dumps(locked, indent=1), encoding="utf-8")
    print(json.dumps(gt["about"], indent=1))
    if "anchors_check" in gt:
        print("anchors:", json.dumps(gt["anchors_check"]["summary"]))
    for f in gt["flags"]:
        print("FLAG", f)


def check_anchors(gt: dict[str, Any]) -> dict[str, Any]:
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8")).get("anchors", {})
    rows: list[dict[str, Any]] = []
    for fid, a in anchors.items():
        g = gt["frames"].get(fid)
        if not g:
            continue
        for side in ("left", "right"):
            asd, gsd = a.get(side) or {}, g.get(side) or {}
            row = {"id": fid, "side": side, "anchor": asd.get("state"), "gt": gsd.get("state")}
            if asd.get("state") == EDGE and gsd.get("state") == EDGE and asd.get("x") is not None:
                row["diff"] = round(float(gsd["x"]) - float(asd["x"]), 2)
            rows.append(row)
    diffs = [abs(float(r["diff"])) for r in rows if "diff" in r]
    summary = {
        "n": len(rows),
        "state_match": sum(r["anchor"] == r["gt"] for r in rows),
        "edge_mae": round(float(np.mean(diffs)), 3) if diffs else None,
        "edge_max": round(float(np.max(diffs)), 3) if diffs else None,
    }
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    main()
