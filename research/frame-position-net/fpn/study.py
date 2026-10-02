"""The frame-edge study's frames and labels, read-only, behind the split.

Frames are `data/frames/<id>.npy` (float32 holding the delivered counts) in the
study's main work folder and in `lib2/`; labels are `ground_truth.json` there
(dev) and `locked/ground_truth_test.json` (the tests). A locked frame is handed
out only to scoring that names it: `frames(role)` refuses a `locked_*` role
unless `unlock` is given, which `score.py` does after PREREGISTERED.md exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

import numpy as np

from tools.frame_edges.sides import EdgeResult, Side

from fpn import paths

_SIDE_FIELDS = {f.name for f in fields(Side)}


@dataclass(frozen=True)
class Frame:
    id: str
    work: Path            # the study work folder it lives in
    roll: str
    film: str             # the study's film_type: c41, bw, unknown
    kind: str
    dtype: str
    role: str
    same_as: str | None = None   # a dup: the frame whose position (and label) it repeats

    def image(self) -> np.ndarray:
        return np.load(self.work / "data" / "frames" / f"{self.id}.npy").astype(self.dtype)


@lru_cache(maxsize=1)
def split() -> dict:
    return json.loads(paths.SPLIT.read_text())


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Frame]:
    roles = split()["frames"]
    out: dict[str, Frame] = {}
    for work in (paths.STUDY, paths.STUDY_LIB2):
        for row in json.loads((work / "data" / "manifest.json").read_text())["frames"]:
            rid = row["id"]
            out[rid] = Frame(rid, work, row["roll"], row.get("film_type") or "unknown",
                             row["kind"], row["dtype"], roles.get(rid, "unused"),
                             row.get("same_as") or None)
    return out


def frames(role: str, *, unlock: bool = False) -> list[Frame]:
    if role.startswith("locked") and not unlock:
        raise PermissionError(f"{role} is a locked test set: scored once, by score.py")
    return [f for f in _manifest().values() if f.role == role]


def _side(data: dict) -> Side:
    return Side(**{k: v for k, v in data.items() if k in _SIDE_FIELDS})


@lru_cache(maxsize=4)
def _truth_file(path: Path) -> dict:
    return json.loads(path.read_text())["frames"] if path.exists() else {}


def truth(frame: Frame) -> EdgeResult | None:
    """The labelled answer, following a dup to the frame it repeats."""
    ids = [frame.id] + ([frame.same_as] if frame.same_as else [])
    for name in ("ground_truth.json", "locked/ground_truth_test.json"):
        table = _truth_file(frame.work / name)
        for fid in ids:
            if fid in table:
                t = table[fid]
                return EdgeResult(_side(t["left"]), _side(t["right"]))
    return None


def frame_width(frame: Frame) -> float:
    """The width the study scores this frame's moves with: per roll where measured."""
    for name in ("ground_truth.json", "locked/ground_truth_test.json"):
        path = frame.work / name
        if path.exists():
            widths = json.loads(path.read_text()).get("frame_width") or {}
            if frame.roll in widths:
                return float(widths[frame.roll])
            if "_all" in widths:
                return float(widths["_all"])
    raise KeyError(f"no frame width for {frame.id}")
