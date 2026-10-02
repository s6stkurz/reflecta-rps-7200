# Does the frame sit right? A network that reads one prescan

A challenger to `ensemble_v2`, the four-member vote in `tools/frame_edges`. It
reads one non-inverted 300 dpi prescan, exactly as `scan()` delivers it to the
edge reader, and answers with a move:

- `left` or `right` with a signed number of transport units, where positive
  moves the picture to higher columns;
- `none` below 2.84 units;
- `refuse`.

The SLIDE param is not learned. The driver's own `param_for_mm` turns the move
into one.

Offline only: no scanner is touched anywhere here. Nothing is wired into the
window. `fpn/reader.py` offers the same interface as `tools/frame_edges`, for
when the numbers say it should be.

## The network: `fpn/model.py`

Plain PyTorch, one layer per line, in a `layers.append(...)` list. The network
labels each of the 428 columns as one of four classes: this frame's picture,
film base, the neighbour's picture, or no film.

| | shape |
|---|---|
| input: R, G, B as log2 counts minus the frame's median; film flags c41/bw/positive | (6, 287, 428) |
| five blocks: 3x3 conv, BatchNorm, ReLU, max-pool halving the rows | (64, 8, 428) |
| average over the rows | (64, 1, 428) |
| eight blocks: 1x3 conv dilated 1, 2, 4 ... 128 columns, BatchNorm, ReLU, Dropout 0.1 | (64, 1, 428) |
| 1x1 conv to four class scores | (4, 1, 428) |

The model has 153,356 parameters, and a forward pass costs about 2 GFLOP.

- `build_model_small` is the same design, slimmer: the rows are averaged four
at a time before anything else (the edges run straight down the frame), and
the 3x3 blocks use half the channels. 37,668 parameters, 0.25 s per training
step of 16 against 2.8 s. `train.py --model small` trains it, and the weights
file records which model it holds.

**Loss:** cross-entropy per column against the class shares
  (`fpn/loss.py`). The column an edge cuts through is part base, so the edge is
  learned to a fraction of a column. Weights:
  - 4 at the boundary columns, and 3 either side of them;
  - 4 on picture at the border, because calling it base is a false base;
  - 1 elsewhere.
- **Optimiser:** Adam, lr 1e-3, times 0.3 every 10 epochs, batch 16, 30
  epochs. Half of each batch is mirrored, labels too.
- **From labels to the answer:** `fpn/read.py`, plain numpy. It turns the
  column labels into an `EdgeResult` in the study's own terms. Then the study's
  `decide` gives the units, and `param_for_mm` the param. A frame is read
  together with its mirror image, so a mirrored frame gets exactly the
  mirrored answer.

## The data: synthetic training, real tests

Every training sample is synthetic, and every test is real. The unit of the
split is the picture: `split.json`, committed and frozen before anything was
fitted.

- **Private pictures** (`library personal/`): five lab rolls, 190 pictures,
  used under anonymous ids.
  - Each is cached at 4x prescan resolution in linear light.
  - It is then sampled the way the device makes 300 dpi: point samples across
    the columns, 0.65 of a line down the rows (`fpn/sampling.py`).
  - Each picture is used as a slide, as a C-41 negative and as a B&W negative
    (`fpn/film.py`).
  - Rolls A–D train; roll E is synthetic validation.
- **Real dev frames** with different base on either side (`fpn/realcompose.py`).
  - A side with base is widened with copies of the frame's own base columns.
  - The picture is shifted by whole columns, so every pixel stays real.
- **Strips** (`fpn/compose.py`): neighbour, gap, frame, gap, neighbour, with
  measured frame widths and gaps, soft and slightly tilted edges, blank frames
  and strip ends.
- **The device** (`fpn/device.py`): a real calibration's shading turns the
  picture back into 8-bit raw, and then `library.correct` applies the driver's
  own correction. This matches a real prescan in all 368,508 pixels.
- **Film levels:**
  - C-41 is fitted to the real compose frames (`calibrate.py`).
  - B&W and slides use the numbers measured on their frames (Stefan,
    2026-09-30).

## Steps

```bash
# from the repository root; in a checkout without the data, add FPN_DATA_ROOT=<one with it>
uv run python research/frame-position-net/prepare_personal.py       # cache the pictures
uv run python research/frame-position-net/split.py                  # once: freeze the split
uv run python research/frame-position-net/calibrate.py              # fit the films (~30 min)
uv run python research/frame-position-net/synthesize.py --n 5000 --set train
uv run python research/frame-position-net/synthesize.py --n 1000 --set synval
uv run python research/frame-position-net/realism.py --set research/frame-position-net/local/synth/train-1
uv run python research/frame-position-net/train.py --train ... --synval ... --run ...   # hours: background
uv run python research/frame-position-net/score.py --weights ... --role realval
```

The locked sets are scored only once, after `PREREGISTERED.md` is committed
naming the weights' sha256. `score.py` refuses them before that.

## The bar

It must do better than `ensemble_v2` on the locked sets, and never move a
frame the wrong way:

| set | condition |
|---|---|
| every locked set | false base 0, wrong-direction moves 0 |
| main test (Gold 200 + hwcheck) | sides ≥ 39/40, moves ok ≥ 19/20 |
| test2 (library 2's five pictures) | sides ≥ 86/93, moves ok ≥ 39/46 |

## What is committed and what is not

- **Committed:** code, `split.json`, and results as numbers.
- **Kept in `local/` (gitignored), never published:** everything made from
  the private pictures. That is the caches, synthetic sets, sheets,
  `calibration.json` and the weights.
- **Weights:** saved as `.npz` and loaded with `allow_pickle=False`, never with
  `torch.save` or `torch.load`. torch 2.2.2's `torch.load` can run code from a
  file (CVE-2025-32434).
