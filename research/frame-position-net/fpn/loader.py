"""Feeding the training: a PyTorch Dataset over a synthetic set, for DataLoader.

Preparing a prescan's inputs (`fpn.inputs.prepare`: log2, the median per
channel) costs about 9 ms per sample on the CPU -- on a GPU it is most of what
an epoch costs. `DataLoader(num_workers=N)` spreads it over N processes.

On Linux the workers are forked and share the set's images in memory. On macOS
Python starts them fresh, and each would receive its own copy of the whole set
(about 1.8 GB for 5000 samples), so `workers` stays 0 there.

Augmentation is per sample: mirrored (labels too) with probability one half,
turned upside down with probability one half. Each is decided by a generator
seeded with (seed, epoch, sample), so a run flips the same samples whatever the
number of workers.
"""

from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from fpn.data import SyntheticSet
from fpn.loss import column_weights


Sample = tuple[np.ndarray, np.ndarray, np.ndarray]


class AugmentedSet(Dataset[Sample]):
    def __init__(self, data: SyntheticSet, seed: int, augment: bool = True):
        self.data = data
        self.seed = seed
        self.augment = augment
        self.epoch = 0

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Sample:
        x = self.data.inputs(index)                               # (6, 287, 428)
        t = self.data.targets[index].copy()                       # (4, 428)
        if self.augment:
            rng = np.random.default_rng([self.seed, self.epoch, index])
            if rng.random() < 0.5:                                # mirrored, labels too
                x = x[..., ::-1]
                t = t[..., ::-1]
            if rng.random() < 0.5:                                # the other way up
                x = x[:, ::-1, :]
        t = np.ascontiguousarray(t)
        return np.ascontiguousarray(x), t, column_weights(t[None])[0]


def loader(data: AugmentedSet, batch: int, seed: int, workers: int,
           pin_memory: bool = False) -> DataLoader:
    """Shuffled batches; the order is seeded too. A short last batch is dropped,
    as the earlier hand-written batching did. The workers are not persistent:
    made afresh each epoch, they see the epoch the main process set on the
    set, so the flips change from epoch to epoch."""
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(data, batch_size=batch, shuffle=True, drop_last=True,
                      num_workers=workers, generator=generator, pin_memory=pin_memory)


def pick_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda: no CUDA GPU here")
    if name == "mps" and not torch.backends.mps.is_available():
        raise SystemExit("--device mps: no Apple GPU here")
    return torch.device(name)
