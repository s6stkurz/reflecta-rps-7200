# Pre-registered: the network's one look at the locked sets

Fixed 2026-10-04, before any network weights were scored on a locked frame.
Committed on its own, and the locked sets are scored straight after, once each.

## What is scored

- **Weights:** `local/runs/r2-small/epoch-20.npz`, kept local and never committed.
  - sha256 `8823402839ff2e4fdff0b4a8c6ca4b1a51a96a0af787de39d022d6db5193fe65`.
  - The slim model, `build_model_small` (37,668 parameters), at epoch 20 of run r2-small.
- **Abstain threshold:** 0.5, as `score.py --sure 0.5`.
- **Code:** as committed at `b3a633a`. Nothing under `research/frame-position-net/`,
  `research/frame-edge/` or `tools/frame_edges/` changes before the scores are in.
- **Split:** `split.json`, sha256 `9e0e32544e3ed938ec84c6cbf791b38261f2806343c39fc2cbb3e2be376a3eb2`.
- **Calibration:** `local/calibration.json`, sha256
  `a30c10a7e9c70eb6147e01053e7cf681ea8d2149b7ced29e800d50bbb8a9b3d2`.
- **Training sets**, by the sha256 of their manifests:
  - `local/synth/train-3`: `82e9fb27aeeb8637b1f7916b0a9930fea9bf312656f3788a53e7bad1ee3e3fda`;
  - `local/synth/synval-1`: `73338effe2855a292ddee727a22dd4eae2588e420715e09f90c678570c575222`.

## How it was chosen: on realval only

Three runs (r1, r2, r2-small) were scored at every epoch and at thresholds
0.4–0.7 (`results/realval-epochs.tsv`).

**The rule:** the most realval sides at a threshold, with ties going to the
lower synthetic-validation loss.

**The choice:** r2-small epoch 20 at 0.5.
- Sides: 65/68, with 0 false base.
- Moves: 32/33 right, 0 the wrong way, 0 worse.

That passes gate G2.

## The sets

| role | frames | `ensemble_v2`, as the study stored it |
|---|---|---|
| `locked_main`: Gold 200 + hwcheck | 20 | 39/40 sides, 19/20 moves |
| `locked_test2`: library 2's five pictures | 47 | 85/93 sides, 38/46 moves |
| `locked_bw`: bw0910 | 8 | 16/16 sides, though these frames were among those it was chosen on |
| `locked_positive`: x0920 slides | 9 | none: not labelled yet |

## The bar

- **Every locked set:** 0 false base, 0 moves the wrong way, 0 worse moves.
  A worse move leaves the frame further from centred than not moving would.
- **`locked_main`:** at least 39/40 sides and 19/20 moves right. That is
  `ensemble_v2`'s record.
- **`locked_test2`:** at least 86/93 sides and 39/46 moves right. That is one
  better than `ensemble_v2` on each.
- **`locked_bw`:** no count, only the line above. The result is provisional,
  because the B&W film model took its levels from statistics of these frames.
  It is reported beside `ensemble_v2`.
- **`locked_positive`:** scored later, once it has been labelled, with these
  same weights and this threshold. The labelling is two blind labellers working
  on inverted counts, then Stefan's review. It is provisional for the same
  reason: the slide levels came from x0920.

The network passes only if it meets the main and test2 bars and every locked
set meets the safety line. Whatever comes out is reported as it is. These
sets are not used to retrain, retune or score again.

## Where the scores go

They go to `results/<role>/r2-small-epoch-20-sure0.5.json` and to the README,
whatever they say.
