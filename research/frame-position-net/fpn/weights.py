"""Weights as plain numpy arrays: never torch.save, never torch.load.

torch 2.2.2's `torch.load` can run code from a file even with
`weights_only=True` (CVE-2025-32434), and it is the only torch this Mac can
have. So a weights file is an `.npz` of arrays and one JSON string, loaded with
`allow_pickle=False`: nothing in it can be anything but numbers and text.

    model/<name>          the state_dict, BatchNorm statistics included
    optimizer/<i>/<key>   Adam's moments and step, for --resume
    __meta__              JSON: epoch, Adam's param groups, the scheduler, versions
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch

META = "__meta__"


def _arrays(model: torch.nn.Module) -> dict[str, np.ndarray]:
    return {f"model/{k}": v.detach().cpu().numpy() for k, v in model.state_dict().items()}


def save(path: Path, model: torch.nn.Module, meta: dict[str, Any]) -> None:
    np.savez(path, **_arrays(model), **{META: np.array(json.dumps(meta))})


def save_training(path: Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer,
                  scheduler: Any, epoch: int, meta: dict[str, Any]) -> None:
    arrays = _arrays(model)
    state = optimizer.state_dict()
    for index, entries in state["state"].items():
        for key, value in entries.items():
            arrays[f"optimizer/{index}/{key}"] = torch.as_tensor(value).cpu().numpy()
    full = dict(meta, epoch=epoch, param_groups=state["param_groups"],
                scheduler=scheduler.state_dict())
    np.savez(path, **arrays, **{META: np.array(json.dumps(full, default=float))})


def _open(path: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    with np.load(path, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    return arrays, json.loads(str(arrays.pop(META)))


def load(path: Path, model: torch.nn.Module) -> dict[str, Any]:
    """Weights into ``model``; the shapes and names must match exactly."""
    arrays, meta = _open(path)
    state = {k[len("model/"):]: torch.from_numpy(v) for k, v in arrays.items()
             if k.startswith("model/")}
    model.load_state_dict(state, strict=True)
    return meta


def load_training(path: Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer,
                  scheduler: Any) -> int:
    arrays, meta = _open(path)
    load(path, model)
    entries: dict[int, dict[str, torch.Tensor]] = {}
    for k, v in arrays.items():
        if k.startswith("optimizer/"):
            _, index, key = k.split("/")
            entries.setdefault(int(index), {})[key] = torch.from_numpy(v)
    optimizer.load_state_dict({"state": entries, "param_groups": meta["param_groups"]})
    scheduler.load_state_dict(meta["scheduler"])
    return int(meta["epoch"])
