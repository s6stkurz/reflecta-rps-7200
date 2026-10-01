"""Train the network: Adam on the per-column cross-entropy.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/train.py \\
        --train local/synth/train-1 --synval local/synth/synval-1 --run local/runs/r1

Runs for minutes per epoch on a CPU, so start it in the background. Every epoch
writes `epoch-NN.npz` (the weights, as numpy arrays -- never torch.save) and a
line in `log.jsonl`: the training loss, the synthetic-validation loss and
column accuracy, and the real-val sides correct by the study's own rule. The
epoch kept is the one with the most real-val sides correct; ties go to the
lower synval loss. `--resume` carries on from the last epoch written.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from fpn import data, read, weights  # noqa: E402
from fpn.inputs import prepare  # noqa: E402
from fpn.loss import column_loss  # noqa: E402
from fpn.model import build_model, parameter_count  # noqa: E402

LEARNING_RATE = 1e-3
STEP_EPOCHS = 10        # the learning rate drops by GAMMA every STEP_EPOCHS
GAMMA = 0.3
BATCH = 16


def probabilities(model: torch.nn.Module, x: np.ndarray) -> np.ndarray:
    """(N, 6, 287, 428) inputs -> (N, 4, 428) probabilities, averaged with the mirror."""
    model.eval()
    with torch.no_grad():
        t = torch.from_numpy(np.ascontiguousarray(x))
        p = torch.softmax(model(t).squeeze(2), dim=1)
        pm = torch.softmax(model(torch.flip(t, dims=[3])).squeeze(2), dim=1)
        return ((p + torch.flip(pm, dims=[2])) / 2).numpy()


def evaluate_synthetic(model: torch.nn.Module, data_set: data.SyntheticSet,
                       limit: int = 400) -> dict:
    losses, right, total = [], 0, 0
    for start in range(0, min(limit, len(data_set)), BATCH):
        idx = list(range(start, min(start + BATCH, limit, len(data_set))))
        x = np.stack([data_set.inputs(i) for i in idx])
        t = data_set.targets[idx]
        model.eval()
        with torch.no_grad():
            logits = model(torch.from_numpy(x))
            w = torch.ones(t.shape[0], t.shape[2])
            losses.append(float(column_loss(logits, torch.from_numpy(t), w)))
        p = probabilities(model, x)
        right += int((p.argmax(axis=1) == t.argmax(axis=1)).sum())
        total += t.shape[0] * t.shape[2]
    return {"loss": float(np.mean(losses)), "column_accuracy": right / max(total, 1)}


def evaluate_real(model: torch.nn.Module, frames: list, sure: float = read.SURE) -> dict:
    common = data.study_common()
    agree = scored = 0
    for f, image, film_name in frames:
        x = prepare(image, dtype=f.dtype, film=film_name)[None]
        result = read.edges(probabilities(model, x)[0], sure=sure)
        truth = data.study.truth(f)
        if truth is None:
            continue
        for name in ("left", "right"):
            ok = common.side_agrees(truth.side(name), result.side(name), name, 428)
            if ok is None:
                continue
            scored += 1
            agree += int(ok)
    return {"sides_correct": agree, "sides_scored": scored}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--synval", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--benchmark", type=int, help="time this many steps and stop")
    args = ap.parse_args(argv)

    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    rng = np.random.default_rng(args.seed)
    args.run.mkdir(parents=True, exist_ok=True)

    train_set = data.SyntheticSet(args.train)
    synval_set = data.SyntheticSet(args.synval)
    realval = data.real_frames("realval")

    model = build_model()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=STEP_EPOCHS, gamma=GAMMA)
    start = 0
    if args.resume:
        done = sorted(args.run.glob("epoch-*.npz"))
        if done:
            start = weights.load_training(done[-1], model, optimizer, scheduler)
    print(f"{parameter_count(model)} parameters, {len(train_set)} training samples, "
          f"{len(synval_set)} synval, {len(realval)} real-val frames, from epoch {start}", flush=True)

    for epoch in range(start, args.epochs):
        model.train()
        t0 = time.time()
        losses = []
        for step, (x, t, w) in enumerate(data.batches(train_set, BATCH, rng)):
            optimizer.zero_grad()
            logits = model(torch.from_numpy(x))
            loss = column_loss(logits, torch.from_numpy(t), torch.from_numpy(w))
            loss.backward()
            optimizer.step()
            losses.append(float(loss))
            if args.benchmark and step + 1 >= args.benchmark:
                dt = (time.time() - t0) / args.benchmark
                print(f"{dt:.2f} s per step of {BATCH}: "
                      f"{dt * len(train_set) / BATCH / 60:.1f} min per epoch", flush=True)
                return 0
        scheduler.step()
        entry = {"epoch": epoch + 1, "seconds": round(time.time() - t0),
                 "train_loss": float(np.mean(losses)),
                 "lr": optimizer.param_groups[0]["lr"],
                 "synval": evaluate_synthetic(model, synval_set),
                 "realval": evaluate_real(model, realval)}
        weights.save_training(args.run / f"epoch-{epoch + 1:02d}.npz", model, optimizer, scheduler,
                              epoch + 1, {"python": platform.python_version(),
                                          "torch": torch.__version__, "numpy": np.__version__})
        with open(args.run / "log.jsonl", "a") as log:
            log.write(json.dumps(entry) + "\n")
        print(json.dumps(entry), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
