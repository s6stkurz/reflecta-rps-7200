"""The loss: cross-entropy per column, weighted where the move is decided.

The target for each column is a probability over the four classes rather than
one class: the column an edge cuts through gets the share of its rows that are
base, so the edge is learned to a fraction of a column, and a tilted edge
(base in some rows, picture in others) comes out right too.

Weights, per column:
    4  the two columns that meet at a true boundary, and EDGE_REACH either
       side of them -- those decide the move
    4  a picture column at either border -- calling it base is a false base,
       the one mistake the bar allows none of
    1  everywhere else
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

PICTURE = 0
EDGE_REACH = 3
EDGE_WEIGHT = 4.0
BORDER_WEIGHT = 4.0

_cross_entropy = nn.CrossEntropyLoss(reduction="none")


def column_weights(target: np.ndarray) -> np.ndarray:
    """(N, 428) weights from (N, 4, 428) target probabilities."""
    hard = target.argmax(axis=1)
    weights = np.ones(hard.shape, dtype=np.float32)
    boundary = np.zeros(hard.shape, dtype=bool)
    boundary[:, 1:] |= hard[:, 1:] != hard[:, :-1]
    boundary[:, :-1] |= hard[:, 1:] != hard[:, :-1]
    boundary |= target.max(axis=1) < 0.999  # a column an edge cuts through
    near = boundary.copy()
    for shift in range(1, EDGE_REACH + 1):
        near[:, shift:] |= boundary[:, :-shift]
        near[:, :-shift] |= boundary[:, shift:]
    weights[near] = EDGE_WEIGHT
    for side in (0, -1):
        weights[:, side] = np.where(hard[:, side] == PICTURE, BORDER_WEIGHT, weights[:, side])
    return weights


def column_loss(logits: torch.Tensor, target: torch.Tensor,
                weights: torch.Tensor) -> torch.Tensor:
    """logits (N, 4, 1, 428), target (N, 4, 428) probabilities, weights (N, 428)."""
    per_column = _cross_entropy(logits.squeeze(2), target)  # (N, 428)
    return (per_column * weights).sum() / weights.sum()
