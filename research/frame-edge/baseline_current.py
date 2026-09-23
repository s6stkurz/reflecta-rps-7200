"""Iteration 0: today's detector in `rps7200/framing.py`, run as the software runs it.

    uv run --no-sync python research/frame-edge/baseline_current.py

Nothing here is a new detector. It is the code the contact sheet seeds itself
from, fed each roll of the dataset the way `tools/gui.py::_propose_positions`
feeds a walked strip, so that it can be scored against ground truth like every
detector that follows it:

* a roll's prescans -- ``kind`` film and dup, in ``index`` order, the index as
  the frame number -- go to `framing.film_base` for the strip's base level and
  to `framing.propose_offsets` for the proposals, exactly as the sheet does;
* each frame is then read with `picture_start` / `picture_end` against that
  base, which is what `picture_span`, `frame_offset_mm` and `right_gap_closure`
  are built on.

The only change to the inputs: a frame whose manifest ``dtype`` is uint16 is
divided by 257 so the absolute 8-bit constants (`BASE_FLATNESS` = 3 counts and
friends) mean what they were measured to mean. Not rounded -- the software
converts to float64 anyway. A four-channel (RGBI) frame keeps its RGB: a
walk's prescans are RGB and `_grey` would otherwise average infrared in.

Mapping to this study's states, as the software acts on each answer:

* a column from `picture_start` -> ``edge`` at that boundary, conf 1.0;
* ``None`` from it, whatever the reason -> ``picture_to_border``, conf 0.5,
  with the software's own reason in the note (it lumps four different ones);
* `film_base` failing for the roll -> ``refuse`` on both sides.

`picture_end` returns ``width - start`` of the mirrored image, which is the
first column of base at the right: already this study's right-side boundary.
"""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    EDGE,
    PICTURE_TO_BORDER,
    REFUSE,
    RESULTS,
    EdgeResult,
    Frame,
    Side,
    frame_records,
    load,
    save_results,
)

from rps7200 import framing  # noqa: E402
from rps7200.protocol import MM_PER_UNIT  # noqa: E402

OUT = RESULTS / "current" / "iter00" / "results.json"
KINDS = ("film", "dup")
UINT16_TO_8 = 257.0


def counts8(frame: Frame) -> tuple[np.ndarray, list[str]]:
    """The frame as the software's 8-bit prescan counts, and what was done to it."""
    image = frame.image
    done = []
    if image.ndim == 3 and image.shape[2] > 3:
        image = image[..., :3]
        done.append(f"kept RGB of {frame.image.shape[2]} channels")
    if frame.meta.get("dtype") == "uint16":
        image = image / UINT16_TO_8
        done.append("uint16 counts / 257")
    return np.ascontiguousarray(image, dtype=np.float32), done


def reason_kind(reason: str) -> str:
    """Which of `picture_start`'s refusals this is, for counting."""
    if "no unexposed base" in reason:
        return "no_run"
    if "past the" in reason and "margin" in reason:
        return "past_margin"
    if "not reachable from the edge" in reason:
        return "not_reachable"
    if "wider than the" in reason:
        return "too_wide"
    return "other"


def mm_units(mm: float | None) -> dict[str, float | None]:
    if mm is None:
        return {"mm": None, "units": None}
    return {"mm": round(float(mm), 4), "units": round(float(mm) / MM_PER_UNIT, 3)}


def reading(r: framing.Reading) -> dict[str, Any]:
    return dict(mm_units(r.mm), source=r.source, margin=round(r.margin, 3),
                precision_mm=round(float(r.precision), 5), reason=r.reason)


def left_side(image: np.ndarray, base: framing.FilmBase) -> tuple[Side, dict]:
    start, detail = framing.picture_start(image, base)
    if start is None:
        reason = str(detail.get("reason", ""))
        return Side(PICTURE_TO_BORDER, conf=0.5,
                    note=f"no base run found ({reason})"), dict(
            detail, kind=reason_kind(reason))
    gs, gl = detail["gap"]
    # The far side of the gap, when a sliver of the neighbour shows before it.
    outer = float(gs) if gs > 0 else None
    return Side(EDGE, x=float(start), conf=1.0, outer=outer,
                note=f"gap {gl} columns at {gs}"), detail


def right_side(image: np.ndarray, base: framing.FilmBase) -> tuple[Side, dict]:
    end, detail = framing.picture_end(image, base)
    if end is None:
        reason = str(detail.get("reason", ""))
        return Side(PICTURE_TO_BORDER, conf=0.5,
                    note=f"no base run found ({reason})"), dict(
            detail, kind=reason_kind(reason))
    width = image.shape[1]
    gs, gl = detail["gap"]           # already back in this frame's own columns
    outer = float(gs + gl) if gs + gl < width else None
    return Side(EDGE, x=float(end), conf=1.0, outer=outer,
                note=f"gap {gl} columns at {gs}"), detail


def run_roll(roll: str, records: list[dict[str, Any]]
             ) -> tuple[dict[str, EdgeResult], dict[str, Any]]:
    frames = [load(r) for r in sorted(records, key=lambda r: int(r["index"]))]
    inputs, prep = [], {}
    for f in frames:
        image, done = counts8(f)
        inputs.append((f.index, image))
        if done:
            prep[f.id] = done

    images = [im for _n, im in inputs]
    base, base_detail = framing.film_base(images)
    bands = {f.id: framing.edge_bands(im) for f, (_n, im) in zip(frames, inputs)}

    # What the sheet does with the same strip, including its blanket catch.
    try:
        proposals, notes = framing.propose_offsets(inputs)
        propose_error = None
    except Exception as exc:                                  # noqa: BLE001
        proposals, notes, propose_error = {}, {}, f"{type(exc).__name__}: {exc}"

    summary: dict[str, Any] = {
        "frames": len(frames),
        "ids": [f.id for f in frames],
        "film_base": None if base is None else {
            "level": round(base.level, 3), "flatness": round(base.flatness, 3),
            "bands": base.bands, "spread": round(base.spread, 4)},
        "film_base_detail": base_detail,
        "propose_error": propose_error,
        "prepared": prep,
    }

    results: dict[str, EdgeResult] = {}
    for f, (number, image) in zip(frames, inputs):
        note = notes.get(number) or {}
        debug: dict[str, Any] = {
            "roll": roll, "number": number, "dtype": f.meta.get("dtype"),
            "prepared": prep.get(f.id, []),
            "edge_bands": [(round(lv, 2), round(fl, 2)) for lv, fl in bands[f.id]],
            "film_base": summary["film_base"],
            "proposal": dict(mm_units(proposals.get(number)),
                             source=note.get("source"), reason=note.get("reason"),
                             chose=note.get("chose"), agreed=note.get("agreed"),
                             members=note.get("members"), dropped=note.get("dropped")),
        }
        if propose_error:
            debug["proposal"]["error"] = propose_error

        if base is None:
            why = ("film_base failed for the roll: "
                   + str(base_detail.get("reason", "")))
            results[f.id] = EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why),
                                       debug)
            continue

        left, ldet = left_side(image, base)
        right, rdet = right_side(image, base)
        span, sdet = framing.picture_span(image, base)
        debug.update(
            left_detail=ldet, right_detail=rdet,
            picture_span={"start": span, "edge": sdet.get("edge"),
                          "reason": sdet.get("reason")},
            readings={
                "left_gap": reading(framing.frame_offset_mm(image, base)),
                "closure": reading(framing.right_gap_closure(image, base)),
            },
        )
        results[f.id] = EdgeResult(left, right, debug)
    return results, summary


def main() -> int:
    records = [r for kind in KINDS for r in frame_records(None, kind)]
    by_roll: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_roll.setdefault(str(r["roll"]), []).append(r)

    results: dict[str, EdgeResult] = {}
    rolls: dict[str, Any] = {}
    for roll in sorted(by_roll):
        got, summary = run_roll(roll, by_roll[roll])
        results.update(got)
        rolls[roll] = summary

    missing = [r["id"] for r in records if r["id"] not in results]
    if missing:
        raise SystemExit(f"no result for {missing}")

    source = (ROOT / "rps7200" / "framing.py").read_bytes()
    about = {
        "detector": "current",
        "iteration": 0,
        "what": ("rps7200/framing.py as it stands: per roll, film_base over the roll's "
                 "film+dup prescans in index order (index = frame number), then "
                 "picture_start (left) and picture_end (right) against that base; "
                 "propose_offsets over the same (number, image) list, as "
                 "tools/gui.py::_propose_positions calls it (before its snap_offset)."),
        "inputs": ("float32 native counts; uint16-origin frames (manifest dtype) divided "
                   "by 257, not rounded; RGBI frames reduced to RGB. No trimming."),
        "mapping": {
            "edge": "picture_start/picture_end returned a column; x = it, conf 1.0, "
                    "lo/hi None (the code claims no interval); outer = the gap's far "
                    "side when it does not touch the border",
            "picture_to_border": "the call returned None for any reason; conf 0.5; the "
                                 "software's reason is in note and debug.*_detail.kind",
            "refuse": "film_base returned None for the roll",
        },
        "units": f"debug proposal units = mm / rps7200.protocol.MM_PER_UNIT ({MM_PER_UNIT}); "
                 "positive = forward = picture to higher columns",
        "framing_sha1": hashlib.sha1(source).hexdigest(),
        "constants": {k: getattr(framing, k) for k in (
            "BASE_TOLERANCE", "BASE_FLATNESS", "BASE_SPREAD_LIMIT", "MIN_BASE_BANDS",
            "MIN_BASE_SOURCES", "GAP_MIN_MM", "MAX_GAP_MM", "EDGE_FRACTION",
            "APERTURE_MM", "TARGET_GAP_MM", "MAX_CORRECTION_MM")},
        "rolls": rolls,
    }
    save_results(OUT, results, about)

    print(f"{len(results)} frames -> {OUT}")
    for roll, s in rolls.items():
        ids = s["ids"]
        base = s["film_base"]
        head = (f"base {base['level']:.2f} (spread {base['spread']:.3f}, "
                f"{base['bands']} bands)" if base else
                f"base FAILED: {s['film_base_detail'].get('reason')}")
        per = {side: Counter(results[i].side(side).state for i in ids)
               for side in ("left", "right")}
        srcs = Counter(results[i].debug["proposal"]["source"] for i in ids)
        print(f"{roll:8s} n={len(ids):3d} {head}")
        for side in ("left", "right"):
            print(f"         {side:5s} " + ", ".join(f"{k} {v}" for k, v in
                                                   sorted(per[side].items())))
        print("         proposals " + ", ".join(f"{k} {v}" for k, v in
                                                 sorted(srcs.items(), key=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
