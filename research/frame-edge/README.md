# Where does the frame end? An offline study

The automatic positioning (`docs/frame-measurement-plan.md`) is limited by
one unknown: **where the exposed picture ends and the unexposed film base
begins** on a 300 dpi prescan. On the negative, base is the *brightest* thing
on the film; in the positive it is the black strip Stefan sees at a frame's
edge. This folder finds it offline, with nothing from `rps7200/` in the
detectors, against a baseline labelled by eye. Integration into the software
is a separate, later step.

No scanner is touched anywhere here. Everything runs from stored library
entries.

## Steps

```bash
cd research/frame-edge
uv run --no-sync python build_dataset.py --triage   # sheets of every candidate prescan
uv run --no-sync python build_dataset.py            # data/ from triage.json
uv run --no-sync python label_crops.py              # crops/label/<id>_{A,B}.png + <id>.txt
#   ... labellers write labels/<who>/<id>.json (labels/INSTRUCTIONS.md)
uv run --no-sync python merge_labels.py             # -> ground_truth.json
uv run --no-sync python contact_sheet.py --truth    # the ground truth, for Stefan's eye
uv run --no-sync python run.py <algo> <iter>        # one detector: results, scores, sheets
uv run --no-sync python evaluate.py results/<algo>/<iter>/results.json
```

### A second dataset

The same scripts build and label another library in its own folder, without
touching this one:

```bash
export FRAME_EDGE_WORK="$PWD/lib2"                        # triage, data, labels, truth, results
export FRAME_EDGE_LIBRARY="$PWD/../../library 2/300dpi"   # where the entries are
```

Prefix its roll names so they cannot collide with these, and give its rolls
split `test`: data no detector has seen is a held-out set, and its truth
lands in `lib2/locked/`.

## What is where

| | |
|---|---|
| `triage.json` | every candidate prescan classified by eye: roll, split, film type, duplicates |
| `data/manifest.json` | the dataset (ignored by git; rebuilt from the library) |
| `labels/` | one file per frame per labeller; `INSTRUCTIONS.md` is the protocol |
| `ground_truth.json` | the merged labels, per-roll frame width, and the labels' own pair check |
| `anchors.json` | edges measured on the 600-3600 dpi scan beside a prescan, mapped back |
| `common.py` | coordinates, `EdgeResult`, the units, `decide` (edges -> a move) |
| `algos/` | one detector per file; `algos/BRIEF.md` is what each was asked to do |
| `results/<algo>/<iter>/` | results, scores and one contact sheet per roll |

## Coordinates and units

A position is a **column boundary**: left `x` = columns of base at the left,
right `x` = where the picture ends (base is `[x, W)`). One transport unit is
1.2423 columns; **positive units move the picture to the right** (forward).
The smallest move is 2.84 units, so less than that is "none".

## The data

122 labelled positions (102 dev, 20 test) from the library -- the 09-11 and
09-14 walks, Gold 200, strip6/3, 09-09, stage3, the 09-10 black-and-white set
and one representative per position of the 09-20 exposure ladders -- plus 81
duplicates at the same position and the 21-step stage9b ladder. **Gold 200 and
the hardware check (the same strip) are the test set**, scored once at the end.
`demo/rolls` is left out: its frames are library prescans cycled into fake
strips. The walks Stefan's verdicts refer to (`rolls/registration-*`) are not
on this machine.
