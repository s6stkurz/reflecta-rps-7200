"""Synthetic sets in memory, real frames for checking, and batches for training."""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterator

import numpy as np

from fpn import paths, study
from fpn.inputs import prepare
from fpn.loss import column_weights

FILM_OF_STUDY = {"c41": "c41", "bw": "bw", "unknown": "c41"}
FILM_OF_RECIPE = {"c41": "c41", "bw": "bw", "slide": "positive"}


class SyntheticSet:
    """One `synthesize.py` set: images (n, 287, 428, 3) uint8, rows, targets, films."""

    def __init__(self, folder: Path):
        self.folder = folder
        shards = sorted(folder.glob("shard-*.npz"))
        if not shards:
            raise FileNotFoundError(f"no shards in {folder}")
        parts = [np.load(s) for s in shards]
        self.images = np.concatenate([p["images"] for p in parts])
        self.rows = np.concatenate([p["rows"] for p in parts])
        self.targets = np.concatenate([p["targets"] for p in parts]).astype(np.float32)
        ids = np.concatenate([p["ids"] for p in parts])
        manifest = {}
        for line in (folder / "manifest.jsonl").read_text().splitlines():
            row = json.loads(line)
            manifest[row["i"]] = row
        self.rows_meta = [manifest[int(i)] for i in ids]
        self.films = [FILM_OF_RECIPE[m["recipe"]["film"]] for m in self.rows_meta]

    def __len__(self) -> int:
        return len(self.images)

    def inputs(self, i: int) -> np.ndarray:
        return prepare(self.images[i, : self.rows[i]], dtype="uint8", film=self.films[i])


def batches(data: SyntheticSet, size: int, rng: np.random.Generator,
            mirror: bool = True) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Shuffled (inputs, targets, weights); half of them mirrored, labels too."""
    order = rng.permutation(len(data))
    for start in range(0, len(order) - size + 1, size):
        idx = order[start: start + size]
        x = np.stack([data.inputs(int(i)) for i in idx])
        t = data.targets[idx].copy()
        if mirror:
            flip = rng.random(len(idx)) < 0.5
            x[flip] = x[flip][..., ::-1]
            t[flip] = t[flip][..., ::-1]
        yield x, t, column_weights(t)


@lru_cache(maxsize=1)
def study_common() -> ModuleType:
    """The study's own common.py, by path: the scoring rules come from there."""
    path = paths.ROOT / "research" / "frame-edge" / "common.py"
    spec = importlib.util.spec_from_file_location("frame_edge_common", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def real_frames(role: str, *, unlock: bool = False) -> list[tuple[study.Frame, np.ndarray, str]]:
    """(frame, image, film) for the labelled film frames of a role."""
    out = []
    for f in study.frames(role, unlock=unlock):
        if f.kind not in ("film", "slide") or study.truth(f) is None:
            continue
        film = "positive" if f.kind == "slide" else FILM_OF_STUDY.get(f.film, "c41")
        out.append((f, f.image(), film))
    return out
