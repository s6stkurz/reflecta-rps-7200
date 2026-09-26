# Automatic frame adjustment: what was measured and decided

What the frame-adjustment code rests on, in the transport's own units. For
the code itself, see `docs/frame-adjustment-code.md`.

This replaces four earlier documents that were mostly plans, finished TODOs
and superseded numbers: `frame-measurement-plan.md`, `step-calibration-plan.md`,
`registration-accuracy-plan.md` and `registration-confidence-plan.md`. Their full
text is in git history. Each section below names the one it came from.
Measurements taken 2026-09-14 to 2026-09-23. The raw passes are in
`probe/step-calibration/`, `rolls/` and `library/`, all gitignored.

> **Distances are in param units** -- one unit is what one increment of the
> `SLIDE` param adds (CLAUDE.md). One unit is 1.2423 columns of a 300 dpi
> prescan.

## The transport

### One command travels `param + 1.84` units

*From frame-measurement-plan and step-calibration-plan; `tools/verify_protocol.py`
stages 10-13, frame 2, 2026-09-21/22.*

Three legs, each totalling `param 10`:

| leg | commands | travelled |
|---|---|---|
| A | 10 × `param 1` | **36.41 px** |
| B | 5 × `param 2` | 24.82 px |
| C | 1 × `param 10` | **15.19 px** |

**Ten small commands travel 2.40× as far as one large one** for the same param
total. Solving A and C gives a per-command ramp of **1.84 units**, paid before
the param does anything.

Leg B took no part in the solution. It predicts **24.62 px** against 24.82
measured.

Stage 15 re-measured the ramp with one estimator and repeats per rung, and got
1.948. That corroborates 1.84 and rules out the earlier 1.57, which came from a
fit over single commands, where no per-command term could show.

`param 0` is accepted and does nothing (CLAUDE.md), so **param 1, 2.84 units,
is the smallest move there is.**

### Scatter does not grow with the command, so a correction is one command

*From frame-measurement-plan and step-calibration-plan.*

    param  1 :  3.93 4.25 4.04 3.92 3.68 3.14 2.90 3.31 3.61 3.62 px   ±18%
    param  2 :  4.98 4.95 4.91 4.95 5.03 px                            ±1.2%
    param 12 :  median 16.71 px, 14.15 to 18.78, sd 1.2                ±14%
    param 87 :  108.08 px, sd 0.90, twelve commands, all confident

The scatter is about one pixel either way, whatever the size of the command.
Error therefore accumulates with the square root of the command count, and
the count is what the software controls: **fewer commands is strictly
better.** Crossing a whole pitch costs about 6 px of scatter at `param 12` and
about 2 px at `param 87`.

That is why `nudge` sends one command per move and the manual adjuster is
capped at one command.

**The smallest command is the noisiest.** `param 1` is what `HOLD_TOLERANCE`
is defined by, and it scatters by a fifth of itself, so the deadband cannot be
tightened below that.

**One command in 28 did not move the film at all** (0.42 px where 16.7 was
expected). The hold loop re-measures after every move, so it survives one. No
counter records them.

### The top of the range

*From frame-measurement-plan; stage 15.*

- **The step holds.** `param 255` is accepted, and the step is constant to within
  3% from 87 to 160. `protocol.md` §11 calls `param 87` "the least trustworthy
  point", but that reading came from a helper that returns the tallest bump in a
  predicted window whatever the confidence. Measured properly, 87 repeats to
  about a pixel on a 108 px move.
- **Verification stops at about param 160.** Beyond that, consecutive prescans no
  longer share enough film to register: `param 160` moved 196 px at confidence
  27, and 200 and 255 gave 6-23 with impossible distances. This is a limit on
  measurement, not on the transport.
- **So the cap is 87 (88.8 units).** It is the largest move two prescans can
  confirm at the floor, and the largest the vendor sends. The hold loop's own
  search reach, `SEARCH_MM`, is about 85 units, which is about the same.

### The chain is sound

*From step-calibration-plan.*

An inter-frame gap is fixed on the film, so it has to move exactly as far as
the film does. Over ten commands with one gap in view, the gap moved **169 px**
and the accumulated correlation travel said **169.09 px**. The travel derived
from correlation is trustworthy.

### Backlash: unresolved

*From frame-measurement-plan, and `protocol.md` §5 and §11.*

The readings disagree:

- Two to three commands are swallowed after a direction change (`protocol.md`
  §11, and the rewinds of 2026-09-21 in `session.BACKLASH_COMMANDS`).
- The one careful measurement made during the step calibration saw only
  **0.06 units**.

The code does not depend on settling it. `hold_plan` **never reverses within a
frame**: it iterates forward and reports an overshoot instead. A roll enters
each frame loaded forward, so a backward move spends its first command on
backlash, and two passes converge where one would report failure.

## The film

*From frame-measurement-plan and `research/frame-edge/REPORT.md`.*

| | units | how |
|---|---|---|
| pitch, gap to gap | **366.5** | tracked across twelve commands and four gaps, walk K |
| aperture | **344.5** | 428 columns of a 300 dpi prescan |
| frame | **350.6** | 435.6 columns: 39 pairs of prescans of one frame, one showing each edge, the registered shift between them closing the width with no pitch assumed (interquartile 435.3-435.9 columns) |
| gap | **~16** | pitch − frame |

**The frame is wider than the aperture.** A centred frame therefore shows **no**
unexposed base at either edge, and even a sliver of base means the frame is
several units off.

A second camera read 432.7 columns (2 pairs). That would move a centred frame
by 1.2 units, under half the smallest move.

**A column is `25.4/dpi`, not `APERTURE_MM / width`.** The two differ by 0.7%.
With the true pitch, the geometry of a whole roll closes to −0.35 units; with
`APERTURE_MM / width` it closes to +1.5. The hold loop still verifies in the
aperture scale, and the detector hands its move over in that scale deliberately
(`docs/frame-adjustment-code.md`, *Units*).

## Finding the edge

*From frame-measurement-plan; the full study is `research/frame-edge/REPORT.md`.*

**Ground truth by eye.** 122 frame positions from the library were each labelled
twice. On the 41 of them that have a 600-3600 dpi scan stored beside them, the
labels agree with independently measured edges to **0.16 columns**. A second
library added 125 more.

**The detector.** Seven detector families were tried. The one kept is a vote of
four whose errors fall on different frames (`ensemble_v2`, now
`tools/frame_edges`). On frames none of them was tuned on:

| | the old rule | the ensemble |
|---|---|---|
| sides right, Gold 200 | 80% | **97%** |
| sides right, unseen library-2 pictures | 24% | **91%** |
| base claimed inside the picture, 20 unseen frames | 4, up to 26 columns deep | **0** |

**Base is not the brightest thing on a negative.** Exposed C-41 loses its orange
mask, so a few percent of picture pixels are brighter than base in some channel.
Base is recognised by:

- its colour ratio: R/G 2.1-2.3, B/G about 0.53
- a straight edge running the full height of the frame

That is why every detector based on level and flatness found silhouettes.

### Rejected: bright and flat by column

The first detector took a gap to be columns that are bright and flat down their
length. On a negative, so is a tree silhouette at dusk. On one delivered roll it
produced false bands on:

- frame 3, a beige wall
- frame 5, shaded grass
- frame 6, a 35-column silhouette
- frame 9, where the false band placed the frame 23 units from what every
  neighbour said

Level, flatness, two-dimensional uniformity and the infrared plane all failed to
separate a gap from a silhouette.

### Rejected: the per-row rule on the prescan

Stefan described real base as "a sharp line, completely black from top to
bottom", which suggested testing the width row by row: a real gap is the same
width in every row, and a silhouette is not. On **600 dpi delivered frames** it
separates cleanly: real gaps have a per-row width spread of 2-4 px, the
silhouette 233. **It does not transfer to the 300 dpi prescan**, which is where
positions are decided:

| statistic | why it failed |
|---|---|
| per-row width, interquartile spread | 10 of 15 frames refused; real gaps rejected |
| per-row width, median against tenth percentile | 8 of 15; frame 5 still took a 52-column band |
| fraction of rows within 1.5× the tenth percentile | frame 5's false band scored 0.71 against 0.35-0.46 for real gaps |
| colour neutrality against a known gap's ratio | frame 5 read neutral across all 428 columns |

The weak link is the pixel question underneath: whether a pixel is base, asked
of a value with a few counts of noise against a tolerance of a few counts. The
code survives only in `tools/roll_registration_study.py`.

## Verifying a move: the confidence floor

*From registration-confidence-plan. Asked 2026-09-14, after the hold loop
shipped. Re-derive with `tools/registration_margin.py`.*

### What `confidence` is

`register` returns a **z-score**:

    confidence = (window.max() - window.mean()) / window.std()

It is not a ratio. The `std` term makes it depend on how far the match was
searched: the same pair scores **23.8 at a 16 px reach and 129.7 at 200 px**.
Every number below is therefore valid only at `SEARCH_MM` (about 85 units), and
the reach is fixed rather than sized per frame.

### The floor, 55

The arbiter for "same picture or not" is the pixels, not the labels. Each pair
is aligned at the lag `register` chose and then correlated. Over 3850 pairs that
measure is sharply bimodal, with **nothing between 0.75 and 0.85**, so the cut
is 0.80. An initial cut of 0.95, picked by eye, called 22 genuine pairs false.
Library frame labels are not ground truth either: five entries labelled frame 01
held three different photographs.

| pairs | n | confidence |
|---|---|---|
| different photographs | 3754 | 4.1 - **30.2** |
| the same picture twice | 96 | **54.9** - 203.5 |
| the same picture, within an hour | 74 | **58.9** - 203.5 |

**No pair of different photographs has ever scored above the floor**, and the
worst clears it by 24.8 points. On the other side the margin is **3.9**, so
genuine matches will sometimes be refused. That asymmetry is deliberate:

- A false positive moves the film to the wrong place.
- A refusal leaves the frame where it is, scans it and flags it.

Anything from about 35 to 55 fits the data; 55 is the conservative end.

One roll alone had suggested a chasm, with nulls up to 11.4 and matches from
93.5. It took every pair to find the real margin. An apparent counter-example,
two survey frames scoring 120, turned out to be the same piece of film passed
over twice (0.989 aligned correlation).

### Self-similar frames are the best case

The worry was grass and sky, whose pictures look alike wherever they are slid.
Each of 17 real frames was displaced by ±3/8/24 px, with a real pass-to-pass
residual added. **Every trial recovered the exact lag**: 102 of 102.

Confidence rises with detail. The frames with the strongest autocorrelation
sidelobes, periods of 67-104 px squarely inside the reach, scored *highest*
(153-192). Phase correlation normalises every frequency before transforming
back, so it matches the phase spectrum, not how the texture looks, and a grass
field has the richest phase spectrum there is.

The ambiguity that does exist never competes. Across the 96 same-picture pairs,
the tallest rival peak more than 3 px away is 9× to 167× below the true one, and
no rival exceeds z = 6.5.

### The failure mode is a collapse, not a lie

The flattest frame was faded towards featureless, displaced +8 px:

| contrast kept | confidence | lag recovered |
|---|---|---|
| 1.00 | 161.0 | correct |
| 0.25 | 67.0 | correct, verified |
| 0.10 | 29.7 | correct, **refused** |
| 0.02 | 7.4 | correct, refused |
| 0.00 | 10.5 | wrong, refused |

Confidence falls smoothly and crosses the floor long before the answer goes
wrong. There is no regime where the measurement is wrong and confident.

### Rejected

- **A peak-to-sidelobe ratio.** It separates the same cases by a *smaller*
  margin than `confidence`, and a global PSR overlapped outright (17.12 against
  18.47 where `confidence` puts 15× between them).
- **Displacing with `np.roll`.** The noise moves with the content, so every pair
  matches perfectly (~210). The residual must be real and independent.
- **Gaussian noise plus a synthetic sensor pattern.** It collapsed everything to
  about 5, because phase correlation whitens and synthetic content has energy at
  few frequencies. The content must be real film.

`library.save` keeps `meta["registration"]` for this reason. Without it every
measured confidence lived only in a gitignored `roll.json`, and the floor could
not be re-fitted.

**Re-run** `tools/registration_margin.py` (and `--self-similar`) after any
change to `register`, the prescan resolution or `SEARCH_MM`. It exits non-zero
if any pair of different pictures scores above the floor.

## The eye against the numbers

*From registration-accuracy-plan and frame-measurement-plan.*

A 15-frame roll was scanned at 600 dpi, each frame held to a position proposed
from its walk. The software called 14 of 15 `held`, with a median residual
under one unit. Stefan's verdicts, verbatim in `docs/stefan-judgement.json`:

    good         3, 5, 6, 7, 8, 12, 14
    a bit right  1, 4, 11, 13, 15   black shows at the right; should have moved further right
    a bit left   2
    bad          9, 10

Scored against those verdicts:

- **The amount of black at an edge does not predict the verdict.** Mann-Whitney
  p = 0.38. The widest black bar on the roll is on a frame he called good, and
  frames 2 and 4 have identical numbers and opposite verdicts. Contrast explains
  the extremes (an invisible bar at 0.02 contrast, a glaring one at 0.81) but not
  the middle.
- **There is a roll-wide bias.** Every readable frame needed **+2.4 to +4.0 units
  further right**: the same sign on all ten, with a scatter of 0.7. That is one
  systematic offset, not fifteen errors.
- **Frames 9 and 10 are one failure.** Frame 9's proposal came from a false band,
  23 units from its neighbours. It *reached* that target, so it recorded `held`.
  Frame 10 inherited the displacement, because a sub-frame move does not touch the
  frame counter. Across the other transitions, frames arrived within one prescan
  pixel of where the previous frame was left: corrections carry cleanly, and so
  does a bad one.

The detector these verdicts were scored against has since been replaced by
`tools/frame_edges` (centring on 350.6 units rather than aiming one edge at a
fixed gap). That removes the asymmetry of "black at the right" by construction.
The roll-wide bias has not been re-measured on the new detector.

## Standing rules

- **Work in param units and floating point.** Round only where the byte goes into
  the command. Every conversion here has hidden a mistake once: a delivery ratio
  computed against a magnitude, a resampling scale error, a search reach smaller
  than the move it was measuring.
- **Never measure a move with a helper that cannot refuse.** Any displacement
  reading needs a confidence and a reach wide enough to contain the answer. A
  helper without either is how `param 87` came to be recorded as the least
  repeatable value when it is among the best.
- **Do not calibrate the base level from the pictures.** The exposure register
  predicts it: `base_R = 1.7339 × exposure_R`, to ±0.54%. Calibrating from the
  frames is a detector labelling its own training set.
- **Do not verify a correction with the detector that made it.** A correctly
  placed frame shows no gap, so a gap detector goes blind exactly when it
  succeeds. The hold loop verifies by correlating against the approved prescan,
  not by re-reading edges.
- **Do not shrink the deadband.** `HOLD_TOLERANCE_MM` is param 1's travel, the
  smallest move and the noisiest. Below it the loop would command moves it cannot
  deliver and chatter around the target. The constant also has other roles
  (`TODO.md`).
- **Stefan's eye is the acceptance test.** It has been right against the numbers
  four times in two days: the frame width, the reversed prescans, the floating
  band and the systematic offset.
- **Answer questions offline first.** The walks and rolls are stored with raw
  bytes, so a proposal can be replayed and scored against the verdicts without
  the scanner.

## Open questions

- **The roll-wide bias** of +2.4 to +4.0 units. `centre.decide` has a
  `bias_units` parameter for exactly this, and nothing sets it. It has to be
  re-scored against the verdicts with the current detector before anything is
  applied.
- **The travel budget punishes honesty.** `hold_plan` allows `|target|` plus a
  fixed headroom. A wrong, large target therefore authorises more travel than a
  right, small one: frame 9's bogus target bought 36 units, frame 10's correct one
  25, and frame 10 was refused its last move by **0.33 units**. A budget that
  does not scale with the target would not have refused it.
- **Backlash**: see *The transport*.
- **Silent non-moves**, about one command in 28, are survived but not counted.
