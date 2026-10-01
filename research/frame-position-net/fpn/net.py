"""The trained network as a detector: a prescan in, an EdgeResult out.

    reader = Net(Path("local/runs/r1/epoch-17.npz"))
    result, probs = reader.detect(image, film="negative", dtype="uint8")

The frame and its mirror image are both read and their probabilities averaged
(the mirror's flipped back), so the answer for a mirrored frame is exactly the
mirror of the answer -- the study's mirror check holds by construction.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from tools.frame_edges.sides import EdgeResult

from fpn import read, weights
from fpn.inputs import prepare
from fpn.model import build_model


class Net:
    def __init__(self, path: Path, sure: float | None = None):
        self.model = build_model()
        self.meta = weights.load(path, self.model)
        self.model.eval()
        self.sure = sure if sure is not None else float(self.meta.get("sure", read.SURE))

    def probabilities(self, image: np.ndarray, *, film: str, dtype: str) -> np.ndarray:
        x = torch.from_numpy(prepare(image, dtype=dtype, film=film)[None])
        with torch.no_grad():
            p = torch.softmax(self.model(x).squeeze(2), dim=1)
            pm = torch.softmax(self.model(torch.flip(x, dims=[3])).squeeze(2), dim=1)
        return ((p + torch.flip(pm, dims=[2])) / 2)[0].numpy().astype(np.float64)

    def detect(self, image: np.ndarray, *, film: str, dtype: str,
               sure: float | None = None) -> tuple[EdgeResult, np.ndarray]:
        probs = self.probabilities(image, film=film, dtype=dtype)
        return read.edges(probs, sure=self.sure if sure is None else sure), probs
