# Contact sheets: the ensemble against the truth

## Round 2: `ensemble_v2` → [round2/](round2/)

Library 2 was split by picture before any tuning:

- **dev2**: 78 frames, tuned on.
- **test2**: 47 frames from 5 pictures drawn at random, locked.

Two members were rebuilt: `changepoint_v2` and `gapmodel_v2`. One rule was
added to the vote: a gap with the neighbour's picture beyond it can stand on
one member's word. The whole setup was chosen on the dev sets, and every test
set was scored once. See `../REPORT.md`, "Round 2".

| set | frames | sides correct | false base | missed | refused | moves ok | red boxes |
|---|---|---|---|---|---|---|---|
| [main_dev](round2/main_dev/) | 98 | **1.00** (193/193) | 0 | 0 | 0 | 94/94 | 0 |
| [main_test](round2/main_test/) | 20 | 0.97 (39/40) | 0 | 0 | 1 | 19/20 | 1 |
| [library2_dev](round2/library2_dev/) (dev2) | 78 | 0.98 (149/152) | 0 | 3 | 0 | 60/63 | 3 |
| [library2_test](round2/library2_test/) (test2, **unseen**) | 47 | **0.91** (85/93) | 0 | 8 | 0 | 38/46 | 8 |

Five of test2's eight red boxes are one picture:
[b0922a](round2/library2_test/sheet_b0922a.png), where the dark-brown gap
lies against shaded foliage. Round 1 missed 7 of that picture's frames and
round 2 misses 5.

---

## Round 1: `ensemble` (below, and the folders beside this file)

The ensemble is `algos/ensemble.py`, run again unchanged on 2026-09-23 after
Stefan's review. `make_test_sheets.py` rebuilds everything here.

## How to read a sheet

- **Red dashes** are where the detector puts the edge between exposed and
  unexposed film.
- **Green dashes** are the truth.
- **A red box** around a frame means the detector does not say what the truth
  says. Its last caption line, in red, gives the reason: a side in the wrong
  state, an edge more than a column off, a refusal, or a move more than one
  smallest step (2.84 units) off.
- Tiles show the positive, so base is black, on a white sheet.
- Under each frame are the outer 32 columns of each side, magnified 6×.

## What changed before this run

- **r0911_06 left is now an edge at 10.0.** Stefan's correction
  (`labels/S1/`). Every other ground-truth frame was accepted as drawn.
- **Library 2: 38 frames taken out of the test,** on Stefan's call. The list
  is in [EXCLUDED_library2.txt](EXCLUDED_library2.txt):
  - the 9 frames on b0922a's second page;
  - b0921c_62, 63 and 67;
  - 25 more where the unexposed strip lies in the middle of the aperture,
    meaning its centre is at least 100 columns from both borders;
  - one duplicate of an excluded frame.
- The main set has no frame with a gap in the middle.
- The sheets now draw 2 px lines and the red boxes.

## Results

| set | frames | sides correct | false base | missed base | refused | moves ok | red boxes |
|---|---|---|---|---|---|---|---|
| [main_dev](main_dev/) | 98 | 0.99 (191/193) | 0 | 1 | 1 | 92/94 | 2 |
| [main_test](main_test/) (Gold 200 + hwcheck) | 20 | 0.97 (39/40) | 0 | 0 | 1 | 19/20 | 1 |
| [library2](library2/) | 125 | 0.87 (213/245) | 1 | 28 | 1 | 80/109 | 31 |

Each folder's `mismatches.txt` lists every boxed frame and why.

- **main_dev:**
  - r0911_06: the ensemble says "border" where Stefan sees 10 columns of base.
  - r0911_07: refused. Base there sits against a wall at base level.
- **main_test:** gold200_14 right is refused. It is the frame whose labels
  needed a third look.
- **library2:** almost every red box is base the ensemble missed on the left,
  on four patterns:
  - base against a scene as clear as base (b0922a, b0922b);
  - gaps well inside the frame with the neighbour's picture beyond
    (b0921c_37, 38, 49, 54 …);
  - a few slivers;
  - b0922s.

  False base is 1 in 245 sides.
