# Where does the frame end? An offline study

The automatic positioning (`docs/frame-measurement-plan.md`) is limited by
one unknown: **where the exposed picture ends and the unexposed film base
begins** on a 300 dpi prescan. On the negative, base is the *brightest* thing
on the film; in the positive it is the black strip Stefan sees at a frame's
edge. This folder finds it offline, with nothing from `rps7200/` in the
detectors, against a baseline labelled by eye. ~~Integration into the software
is a separate, later step.~~

No scanner is touched anywhere here. Everything runs from stored library
entries.

## In the software (since 2026-09-23)

The winning detector, `ensemble_v2` -- four numpy-only members voting per side
-- is in the software as a copy, `tools/frame_edges`, not moved into
`rps7200/framing.py` as `REPORT.md`'s "Moving it into the software" section
proposed. It lives in `tools/` by Stefan's choice and `rps7200` never imports
it: the window and `tools/scan_roll.py` hand it to the driver as the roll's
edge reader (`walk_reader`, `session.edge_reader`) and ask it for a sheet's
positions (`propose_centred`); the window reads a walk in the background with
`EdgeWatch`, to the same answer.

- **Held to this study.** `FRAME_EDGE_PARITY=1 uv run pytest
  tests/test_frame_edges_parity.py` checks the copy's vote (`vote.detect`)
  against the answers stored here, frames and their mirror images, and runs
  only where these frames are on disk. It does not reach `propose.centring`,
  which turns the vote into a move. Run it after any change under
  `tools/frame_edges`.
- **One departure, on purpose.** `vote.detect` asks each member through
  `_member`, so a member that raises abstains on both sides instead of failing
  the frame -- `gapmodel` divided by a base level of 0 on a near-black strip
  end. No answer stored here changes: on these frames no member raised.
- **The frame width it centres with** is `framing.FRAME_WIDTH_UNITS`, 350.6
  units (435.6 columns), measured on 39 pairs of prescans of one frame -- not
  the 425 (36 mm) the old code assumed, nor the ~440 read off the dev gaps in
  `REPORT.md`.
- **What it reads.** Colour negative and black and white (`FILM_TYPES`); a
  positive or Kodachrome gets no reader, and such a roll still aims on the
  older 36.0 mm model in `rps7200/framing.py`. Only 300 dpi prescans are read
  (`READ_AT_DPI`): the device returns 860-862 columns at 600 dpi and 1292 at
  900, and a walk at either is refused frame by frame -- the window says so
  before it starts, and `tools/scan_roll.py` refuses `--correct` there.
- **Units.** `decide` is `common.py`'s, copied: it works in columns and param
  units, and what a person reads is in units. The hold loop is handed
  `offset_mm`, `columns * APERTURE_MM / width` (`centre.columns_to_mm`), the
  scale `framing.measure_shift_mm` verifies a move in.

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
