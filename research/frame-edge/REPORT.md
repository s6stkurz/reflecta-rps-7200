# Where the frame ends: report

2026-09-23, branch `experiment/frame-edge`. Offline only: no scanner was touched.

## The answer

**A vote of four independent detectors finds the edge between picture and
unexposed base on the negative.** The current version is `ensemble_v2`
(round 2, below): 100% on the main dev set, 97% on Gold 200, 91% on five
library-2 pictures it never saw, and no false base anywhere.

- **It almost never claims base that is not there:** 1 false base in 359
  held-out sides.
- **Where base shows near the border with some contrast, it places the edge to
  a fraction of a column.**
- **What it still misses** are gaps deep in the aperture and base beside a
  scene as clear as base. On library 2 that costs a fifth of the sides; see
  below.

On 20 frames no detector had seen (Gold 200 and the hardware check):

| | today's `framing.py` | **the ensemble** |
|---|---|---|
| sides judged correctly | 80% (32/40) | **97% (39/40)** |
| base claimed inside the picture | 4, up to 26 columns deep | **0** |
| base that was there, missed | 3 | **0** |
| refused | 0 | 1 (the hardest frame in the set) |
| edge error, median / p90 | 2.10 / 4.56 columns | **0.15 / 0.28 columns** |
| move within half a step of the truth | 13/20 | **19/20** |
| move more than one smallest step off | 7 | **0** |
| move in the wrong direction | 1 | **0** |

On the 98 development frames it scores 99% (192/193 sides), with 0 bad moves.
That number is optimistic, because the members were tuned there. The test
number above is the honest one: the ensemble's membership was fixed
([PREREGISTERED.txt](results/ensemble/iter01/PREREGISTERED.txt)) before any
test score existed, and each detector was scored on the test set exactly once.

It works on the raw negative and never inverts. It runs in about 0.27 s per
frame and uses numpy only, so it can move into `rps7200/framing.py` unchanged
in kind.

## The second held-out set: library 2, and where the ensemble stops

Another session labelled `library 2/300dpi` (162 frame positions, one C-41
strip) with the same protocol. Its labels are consistent: state agreement
0.991, and 374 shifted pairs move with their picture to 0.27 columns. No
detector had seen it. Every detector was scored on it once
(`score_heldout.py`):

| library 2, 319 sides | today | **ensemble** | gapmodel | changepoint |
|---|---|---|---|---|
| sides correct | 23% | **81%** | 83% | 81% |
| base claimed inside the picture | 4 | **1** | 0 | 1 |
| base missed | 12 | **58** | 38 | 49 |
| refused | 212 | 1 | 15 | 8 |
| move within half a step | 24/146 | 87/146 | 94/146 | 90/146 |

**The ensemble almost never invents base, but here it misses it.** It found
83 of the 143 labelled edges. The misses have two causes:

1. **Gaps deep in the aperture, with the neighbour's picture beyond.**
   - Of the edges with more than 100 columns of base, the ensemble found 6
     and missed 30. Walk b0921c holds most of them: its gaps land anywhere in
     the aperture.
   - Every member caps how far in it searches, at 45–100 columns, so as not to
     take walls and grout for base.
   - gapmodel, the member that models a gap with a neighbour, often finds
     them, but it is outvoted three to one.
2. **Base against a scene as clear as base.**
   - On b0922a the gap shows dark brown in the positive, not black, beside
     shaded foliage. The step from base to picture is a few counts.
   - It is the same limit as r0911_07 in the main set, here across a whole
     roll.

Letting gapmodel's gaps-with-a-neighbour stand alone was checked on the dev
frames only. It gains nothing there and costs one false base (the grout on
x0920_73). That trade cannot be settled on library 2 without spending it as a
test set, so it is left for the next round.

**Library 2 also measures the frame width independently:** pitch minus gap
over its 100 gaps gives **436 columns** (interquartile range 434–438). That
agrees with the main set's ~440 and with the 45 frames there that show no
base at all: the frame is wider than the 428-column aperture.

## Round 2: tuned on library 2, tested on pictures it never saw

After Stefan reviewed the sheets, three things changed:

- r0911_06 got 10 columns of base on the left.
- 38 library-2 frames were taken out as unsuitable, among them every frame
  whose gap lies in the middle of the aperture.
- Library 2 became development material. So that an honest test survived, it
  was **split by picture before anything was tuned**. Its 125 frames are 17
  pictures seen across sessions. Five pictures (47 frames) were drawn at
  random into a locked test2 without looking at any result (`lib2/split.json`).
  The other 78 frames are dev2.

Two members were rebuilt against both dev sets:

- **`changepoint_v2`** anchors a gap on its strong far side, where base meets
  the neighbour's picture, then reads the faint inner step in six-row bins.
  It accepts only a line that several windows read in the same place.
- **`gapmodel_v2`** reads faint edges band by band. A band counts as base
  only if it has the roll's confirmed base colour, which also rejects tile
  grout. It widens the gap widths it accepts, and it refuses a deep gap that
  the roll cannot confirm.

(`chroma_v2` and `stepline_v2` were stopped before they finished and are not
used.)

**`ensemble_v2`** is the same vote with these two members, plus one rule: a
gap with the neighbour's picture beyond it stands on one member's word unless
another member places an edge elsewhere. It was chosen on the dev sets only,
by replaying rules on stored answers (`vote_replay.py`), and pre-registered
before any test score existed.

| | today | ensemble (round 1) | **ensemble_v2** |
|---|---|---|---|
| main dev, sides correct | 66% | 99% | **100%** (193/193), 94/94 moves |
| library 2 dev2 | – | 89% | **98%**, 0 false base |
| **library 2 test2** (5 unseen pictures) | 24% | 84% | **91%**, 0 false base, 38/46 moves |
| Gold 200 + hwcheck (regression check) | 80% | 97% | **97%**, unchanged |

On the unseen pictures round 2 helps (84% → 91%) and still invents no base.
The fixes carried over only partly, though: dev2 reaches 98% and test2 91%.
**Five of test2's eight misses are one picture, b0922a**, the shaded foliage
where the gap shows dark brown, not black. The random draw put it entirely in
test2. On three of those frames the only member that sees the edge is
round-1 `chroma`, and it is outvoted. This is the case to work on next: base
beside a scene nearly as clear as base.

dev2's 38 gaps measure the frame at **438 columns**, which settles the frame
width better than anything before.

## How it decides

Four families, each looking at the frame differently:

| member | what it looks at | its own weak spot |
|---|---|---|
| `changepoint` | per column, statistics over the rows (a low percentile, not the mean): a flat base plateau from the border, then picture | refuses base against a wall at base level |
| `chroma` | each pixel's distance to the base colour in log-density, density and chroma separately; per-row crossings, one straight line | on test it put one edge 32 columns off (gold200_14) |
| `stepline` | per row, a step from base level down to picture; a line voted for by most rows, straight and full height | misses a line whose rows scatter |
| `gapmodel` | the strip's geometry: neighbour, gap, frame, gap; a band must fit a gap the roll can have | takes a deep full-height band for a gap |

**On the dev frames no two of them are wrong on the same side.** That
measurement, not their individual scores, is why they vote (see
[algos/ensemble.py](algos/ensemble.py)):

- An edge stands when at least two members agree within one column, and they
  are at least as many as the members saying "no base here".
- Its position is the median of the agreeing members.
- "Picture to the border" stands on two votes.
- Anything else is refused.

Nothing in the vote was fitted.

The edge is placed where each row crosses 50% between the base plateau and the
picture beside it, the same definition the labels use. The whole frame is
never averaged into one profile first, which is where every earlier detector
went wrong.

Three more families were built and scored, and left out of the vote:

- `texture` (0.90 on test): its errors overlap the others'.
- `baserun` (0.78): it refuses honestly but often.
- `learned` (0.93): a small numpy network. Held-out rolls score it at 0.917,
  and it shares the members' misses.

## The ground truth

- **Collection.** 122 frame positions were collected from `library/`:
  - the 09-11 and 09-14 walks, Gold 200, strip6/3, 09-09, stage3 and the 09-10
    black-and-white set;
  - one position per group from the 09-20 exposure ladders.

  Nine slide frames (x0920_01–09) were found and removed. `demo/rolls` is left
  out, because its frames are library prescans cycled into fake strips. The
  walks behind your verdicts (`rolls/registration-*`) are not on this machine.
- **Labelling.** Six labelling agents each labelled the frames twice, from
  zoomed crops with a ruler on every column and a table of the numbers under
  them. I made the call on the few disagreements.
- **Checks.**
  - The two labels on a frame agreed on state 98% of the time.
  - Where a picture was prescanned twice at a measured shift, the labelled
    edges move with it to 0.48 columns. That is the limit of integer
    registration.
  - Against the edges measured independently on the 600–3600 dpi scans stored
    beside 41 prescans, the labels agree to 0.16 columns.
- **One label error, caught by the detectors.** On r0914_20 a gap lay at
  columns 85–103, deeper than the labelling crops reached. Four detectors
  found it independently, and the truth was corrected.

The sheets for checking it by eye are in [results/truth/](results/truth/).

## What was learned about the film, beyond the detector

1. **Base is not the brightest thing on the negative.** Exposed C-41 loses its
   orange mask, so 1–7% of picture pixels are brighter than base in some
   channel. What identifies base is its **colour ratio**: R/G 2.1–2.3 and B/G
   about 0.53 on every roll. Add to that its geometry: full height, uniform,
   and a sharp straight boundary. Any rule of the form "at the top of the
   frame's range" misses real base.
2. **The frame is wider than the aperture.**
   - 45 of 98 frames show no base on either side, which a frame narrower than
     428 columns cannot do.
   - The measured gaps give about 440 columns; the code assumes 425 (36 mm).
   - Consequence: a sliver of base a fraction of a column wide means the frame
     is about 7 units off centre. Slivers therefore matter as much as wide
     bars. The ensemble takes them from the members that can see them.
3. **Scene black can be base, to the pixel.** Tile grout and black walls can
   carry exactly base's colour and density (x0920_73). Only geometry and
   context separate them: a real gap is straight and full height, and the
   picture on either side of it is two different pictures.
4. **A walk frame can be about 100 columns off.** Walk 09-14 frame 16
   (r0914_20) shows the neighbour's statue, a full gap, and then its own
   picture. A detector must be able to see a gap well inside the frame.
5. **Unrelated, found on the way:** 10 of the 41 high-resolution library scans
   are stored top-to-bottom reversed against their prescans: every odd Gold
   200 frame from 03, strip3_03 and strip6_03. That fits the byte-14 reversal
   in `docs/byte14-plan.md`.

## Open decisions (yours)

- **The target.** Where does a frame "sit perfectly"?
  - Centring needs the frame width. The film says about 440 columns; the code
    says 425.
  - The alternative is "move until no base shows". That needs no width, but
    stops at the first column of picture.
  - This does not change which detector is best: truth and detector are always
    decided with the same width. It sets the absolute "N units".
- **The +2.4 to +4.0 unit roll-wide bias** in your verdicts on walk K could
  not be tested here, because the walk is not on this machine.
  [common.decide](common.py) takes it as a separate `bias_units`, so it can
  be added without touching the detector.

## Moving it into the software (not done yet)

1. **Code.** Move the four members and the vote into `rps7200/framing.py` (or a
   `framing_edges.py` beside it), numpy only. They have no dependencies on
   this folder beyond `common.py`'s `Side`/`EdgeResult`.
2. **Proposals.** Replace `picture_start` / `picture_end` in `propose_offsets`
   with the vote. Turn edges into one command with `common.decide`, whose
   units and sign already match `command_for`.
3. **Verification.** Verify on the second prescan with a registered shift, not
   with the detector alone. The doc's rule "do not verify a correction with
   the detector that made it" still holds.
4. **Tests.** Build them from stored frames: this dataset, and library 2 once
   it is labelled. Its 162 positions are being labelled now as a second
   held-out test set, and no detector has seen them.

## Reproduce

```bash
cd research/frame-edge
uv run --no-sync python build_dataset.py            # data/ from library/ and triage.json
uv run --no-sync python merge_labels.py             # ground_truth.json from labels/
uv run --no-sync python run.py ensemble iter01      # dev: results, scores, sheets
uv run --no-sync python leaderboard.py              # every iteration, re-scored
```

The per-family iterations and their sheets are under [results/](results/).
Each family's module documents what every constant was measured on.
