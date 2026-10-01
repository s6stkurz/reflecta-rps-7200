"""Contact sheets for Stefan's eye. Local only: they show private pictures.

Each tile is one prescan as delivered, each channel stretched on its own for
viewing -- negatives as the positive the study's sheets show
(`display_positive`), slides as they are. Under it, a bar of the column
classes: picture grey, base orange, neighbour blue, no film magenta -- the
truth's above the network's when both are given. Truth edges are drawn green,
the network's red.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fpn.data import study_common

CLASS_COLOURS = np.array([[170, 170, 170], [255, 140, 0], [70, 110, 255], [255, 0, 255]],
                         dtype=np.float64)
TRUTH, NET = (0, 230, 0), (255, 40, 40)


def _display(image: np.ndarray, film: str) -> np.ndarray:
    if film in ("c41", "bw"):
        return study_common().display_positive(image)
    v = image[..., :3].astype(np.float64)
    lo = np.percentile(v, 0.5, axis=(0, 1))
    hi = np.percentile(v, 99.5, axis=(0, 1))
    return (np.clip((v - lo) / np.maximum(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)


def _bar(p: np.ndarray, height: int = 7) -> np.ndarray:
    return np.repeat((p.T @ CLASS_COLOURS).clip(0, 255).astype(np.uint8)[None], height, axis=0)


def _mark(tile: np.ndarray, x: float | None, colour: tuple[int, int, int], dashed: bool) -> None:
    if x is None:
        return
    c = int(np.clip(np.floor(x), 0, tile.shape[1] - 1))
    rows = np.arange(tile.shape[0])
    if dashed:
        rows = rows[(rows // 4) % 2 == 0]
    tile[rows, c] = colour


def tile(image: np.ndarray, film: str, truth=None, target: np.ndarray | None = None,
         answer=None, probs: np.ndarray | None = None, rows: int = 287) -> np.ndarray:
    view = np.zeros((rows, image.shape[1], 3), dtype=np.uint8)
    view[: image.shape[0]] = _display(image, film)
    for result, colour, dashed in ((truth, TRUTH, False), (answer, NET, True)):
        if result is not None:
            for name in ("left", "right"):
                _mark(view, result.side(name).x, colour, dashed)
    parts = [view]
    for p in (target, probs):
        if p is not None:
            parts += [np.zeros((2, view.shape[1], 3), np.uint8), _bar(p)]
    parts.append(np.zeros((6, view.shape[1], 3), np.uint8))
    return np.concatenate(parts, axis=0)


def sheet(tiles: list[np.ndarray], path: Path, per_row: int = 4) -> None:
    from PIL import Image

    height = max(t.shape[0] for t in tiles)
    padded = [np.pad(t, ((0, height - t.shape[0]), (0, 4), (0, 0))) for t in tiles]
    while len(padded) % per_row:
        padded.append(np.zeros_like(padded[0]))
    rows = [np.concatenate(padded[k: k + per_row], axis=1) for k in range(0, len(padded), per_row)]
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate(rows, axis=0)).save(path)
