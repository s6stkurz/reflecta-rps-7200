# Measuring a frame, and moving it

> **Millimetres are prohibited.** Every distance here is in **param units** --
> one unit is the distance one increment of the `SLIDE` param adds. See
> CLAUDE.md and `docs/step-calibration-plan.md`.

## What this replaces

The frame detector and the correction loop were built on four things that have
now all been measured and three of which were wrong.

| | was | is |
|---|---|---|
| what makes a gap a gap | bright and flat by column | **the same width in every row** |
| the base level | calibrated from the pictures | **predictable from the exposure register** |
| one prescan column | `APERTURE_MM / width` | **25.4/dpi** -- 0.7% different |
| a correction | up to three commands | **one**, because scatter does not scale |

## The measurements this rests on

All taken 2026-09-21/22 on the film in the transport, `probe/step-calibration/`,
stages 10-13 of `tools/verify_protocol.py`.

### One command travels `param + 1.84` units

Three legs of equal param total over ten, five and one commands:

    10 x param 1  ->  36.41 px        1 x param 10  ->  15.19 px

**Ten small commands travel 2.40x as far as one large one for the same param
total.** Solving them: a command costs **1.84 units** before it moves at all,
and one unit is **1.2423 columns** of a 300 dpi prescan. Leg B took no part in
the solution and predicted itself to 0.2 px.

### Scatter does not grow with the command

    param 12  (stage 11):  16.71 px, sd 1.2
    param 87  (stage 13): 108.08 px, sd 0.90   over twelve commands, all confident

**About one pixel either way.** So for a given distance, fewer commands is
strictly better -- error accumulates as the square root of the count, and the
count is what you control. Crossing a pitch costs about 6 px of scatter at
`param 12` and about 2 px at `param 87`.

This is the opposite of what one might assume, and it is the whole argument for
correcting in a single command.

### The top of the range

`param 255` is accepted; nothing above 87 had ever been sent. The step is
**constant at 1.24 px/param from 87 to 160**, against 1.283 at low param -- a 3%
decay, not the collapse section 11 describes. `param 87` repeated one pixel
apart on a 108 px move, where section 11 calls it "the least trustworthy point
in the whole set" at 0.84 mm apart; that reading came from a helper that
searches a narrow window around a *predicted* value and returns the tallest
bump in it whatever the confidence, with the prediction taken from the law
being tested.

**Above about `param` 160 a move cannot be measured** by comparing consecutive
prescans -- they no longer share enough film. That is a measurement limit, not
a transport one, and it sets the ceiling on a *verifiable* correction. The hold
loop's own reach is smaller still: `SEARCH_MM` 9.0 is about **param 84**.

### The film

Tracked across twelve commands, four gaps entering and leaving:

    pitch, gap to gap   366.5 units      three readings: 457, 454, 455 px
    aperture            344.5 units      428 columns
    gap, flat core        15.2 units     a LOWER bound
    frame                351.3 units     therefore an UPPER bound

The frame comes out **wider than the aperture**, which cannot be true -- you
would never see unexposed base, and it is visible on most frames. So the gap is
under-measured, and by enough to matter: for the frame to fit at all the gap
must exceed 21.7 units where the flat-core test reads 15.2.

**That is the whole problem in one number.** The pitch is solid. The frame is
unknown because *where a gap ends* is unknown.

## Why the old detector cannot find the edge

A gap is unexposed film base. The detector looked for columns that are bright
and flat down their length. On a negative, so is a tree silhouette at dusk --
clear film at exactly the base level, dead flat by column mean.

Measured over the delivered roll: **level, flatness, two-dimensional uniformity
and the infrared plane all fail to separate them.** The false bands they
produced were on frames 3 (a beige wall), 5 (shaded grass), 6 (a 35-column
silhouette) and 9 -- and frame 9's cost two frames of the roll, because its
false reading put it 23 units from what every neighbour said and the frame
after it inherited the move and ran out of travel.

**One test separates them, and it is Stefan's own description**: real base is
"a sharp line, completely black from top to bottom". Per row, a real gap is the
same width in every row; a silhouette is not.

    real gaps                    per-row width IQR    2-4 px
    frame 6's silhouette         per-row width IQR    233 px

That is the discriminator. It was never implemented because every detector in
`framing.py` begins by averaging the rows away.

## What the eye says that the geometry does not

Stefan's verdicts on the delivered roll are in `docs/stefan-judgement.json`.
Scored against them:

* **How much black shows at an edge does not predict his verdict.** Mann-Whitney
  p = 0.38; the difference between "good" and "a bit right" is smaller than one
  600 dpi column. The widest black bar on the roll is on a frame he called good.
  Frames 2 and 4 have identical software numbers and opposite verdicts.
* **What is real is a roll-wide bias.** Every readable frame needed
  **+2.4 to +4.0 units further right**, the same sign on all ten, scatter 0.7
  units. One systematic offset, not fifteen individual errors.
* Part of that offset is the wrong pixel pitch. The geometry chain closes to
  -0.35 units with the true pitch and +1.5 units with `APERTURE_MM/width`.

So the target is systematically wrong, and on top of it sits something the
geometry does not capture -- contrast explains the extremes (an invisible bar at
0.02 contrast, a glaring one at 0.81) but not the middle.

## Comprehension: what a future session needs to know

**Work in param units and floating point.** Round to an integer only when the
byte goes into the command. Every conversion in this codebase has hidden a
mistake: a delivery ratio computed against a magnitude, a resampling scale
error that made a good roll look bad, a search reach smaller than the move it
was measuring.

**Never measure a move with a helper that cannot refuse.** `_shift_near`
returns the tallest bump in a window regardless of confidence, and it is how
`param 87` came to be recorded as the least repeatable value in the set when it
is among the best. Any displacement reading needs a confidence, and a search
reach wide enough to contain the answer -- `measure_shift_mm` looks +-105 px
and silently refuses everything larger.

**Do not calibrate the base level from the pictures.** The exposure register
predicts it: `base_R = 1.7339 x exposure_R` to +-0.54%. Calibrating from the
frames is where circularity enters -- a detector labelling its own training set.

**A frame that is correctly placed is invisible to a detector that looks for a
gap**, because the gap shrinks below the detection floor as the placement
improves. Do not verify a correction with the detector that made it.

**Stefan's eye is the acceptance test.** It has been right against the numbers
four times in two days: the frame width, the reversed prescans, the floating
band, and the systematic offset. When a metric disagrees with what he can see,
the metric is wrong.

## The rewrite

### Measurement, in floating point

* `UNITS_PER_COLUMN` -- one prescan column in param units, from the measured
  1.2423 columns per unit, scaled by resolution. Replaces `APERTURE_MM/width`.
* `gap_runs` -- per row rather than per column. A column belongs to a gap when
  most of its rows are base **and** the per-row widths agree. Returns runs with
  a consistency figure, so a caller can refuse a silhouette.
* `base_level` -- from the pass's own exposure, not from the strip.
* Every position, offset and width a float. No rounding anywhere in the
  measurement path.

### Adjustment, in one command

    units  = the correction wanted, floating point
    param  = round(|units| - COMMAND_COST)        <- the only rounding
    action = forward if units > 0 else backward

One command. Not several, because the scatter is per command and does not
shrink with the size of it, so two commands are twice the noise and twice the
time for no gain. Below the smallest command the film is left alone, and that
floor is `1 + COMMAND_COST` = **2.84 units**, which is what the deadband
becomes.

**The one thing that argues against it**, and it needs Stefan's call: backlash.
The first command after a direction change is partly or wholly swallowed --
measured at two to three commands in section 11, though only 0.06 units in the
one careful measurement made here. With a single command there is no second
chance, so a correction that reverses direction may simply not happen. The
options are to spend the backlash with a throwaway command when the direction
changes, to allow exactly one retry after a reversal, or to accept it and
report it.

## What was built, and what was not

### Done and measured

The units and the single-command adjustment are in `rps7200/framing.py`:
`COLUMNS_PER_UNIT`, `COMMAND_COST`, `units_per_column`, `command_for`. Floating
point throughout, with the integer appearing in exactly one place -- the param
byte. `command_for` returns what the integer actually buys, so a caller carries
the difference rather than pretending it asked for what it got, and it refuses
a distance beyond what one command can deliver and still be checked.

*What moves the film is not that yet (checked 2026-09-25).* `command_for` is
built and tested, and only `describe_command` calls it, which nothing calls.
Moves still go through `DirectScanner.param_for_mm` and `nudge`, in millimetres,
chained by `session.plan_nudges` for the window's manual moves and
`tools/scan_roll.py --nudge`; one hold correction is one command up to
`MAX_CORRECTION_PARAM` (87). `measure_shift_mm` still scales by
`APERTURE_MM / width` rather than `units_per_column`.

### Done: finding the edge (2026-09-23, `tools/frame_edges`)

What this section asked for was built offline, from stored prescans only, in
`research/frame-edge/` (its `REPORT.md` has everything). In short:

* **A ground truth by eye.** 122 frame positions from the library, each
  labelled twice. They agree with edges measured independently on the
  600-3600 dpi scans stored beside 41 of them to **0.16 columns**. A second
  library added 125 more.
* **Seven detector families**, and a vote of four whose errors fall on
  different frames (`ensemble_v2`). On frames none of them was tuned on it
  gets **97%** of sides right on Gold 200 and **91%** on unseen library-2
  pictures, against 80% and 24% for the rule this section describes. It
  claims base inside the picture on **none** of them. It reads per row, never
  averaging the rows away, and a gap with the neighbour's picture beyond it is
  two-sided evidence the others cannot see.
* **Base is not the brightest thing on the film.** Exposed C-41 loses its
  orange mask, so a few percent of picture pixels are brighter than base in a
  channel. Base is recognised by its colour ratio (R/G 2.1-2.3, B/G ~0.53) and
  a straight full-height edge. That is why every level-and-flatness rule above
  failed.
* **The frame is 350.6 units wide** (`framing.FRAME_WIDTH_UNITS`, 435.6
  columns): measured on 39 pairs of prescans of one frame, one showing its
  left edge and the other its right, the registered shift between them
  closing the width with no pitch assumed. It is wider than the aperture, so
  "the frame comes out wider than the aperture" above was true, not a
  measurement error. A centred frame shows no base at all, and the gap is
  about 16 units.

The window and the roll now centre each frame with it. `tools/frame_edges` is a
copy of the study's detector, and `tests/test_frame_edges_parity.py` holds it
to the study's stored answers. The rule below is kept for the record and still
decides only when no reader is handed to `StripWalk`.

### Superseded: the per-row rule on the prescan

**The per-row rule does not separate a gap from a silhouette on a 300 dpi
prescan.** Four statistics were tried against walk K, whose false bands on
frames 3, 5, 6 and 9 are known:

| statistic | why it failed |
|---|---|
| per-row width, interquartile spread | 10 of 15 frames refused; real gaps rejected |
| per-row width, median against tenth percentile | 8 of 15; frame 5 still took a 52-column band |
| fraction of rows within 1.5x the tenth percentile | frame 5's false band scored **0.71** against **0.35-0.46** for real gaps on frames 7, 8, 14, 15 |
| colour neutrality against a known gap's ratio | frame 5 read neutral across all 428 columns |

The measurement that motivated this was taken on the **600 dpi delivered
frames** with a different test for whether a pixel is base -- a per-column
standard deviation threshold fitted to that resolution, plus neutrality. It
separated cleanly there: real gaps 2-4 columns of spread, a silhouette 233.
**It does not transfer to the prescan**, and the prescan is where positions are
decided.

The weak link is not the per-row idea. It is the pixel-level question
underneath it -- *is this pixel unexposed base* -- which on a 300 dpi prescan is
asked of a value with a few counts of noise against a tolerance of a few
counts. Smoothing along the row helped the false bands and hurt the real ones.

So: **do not tune this further against walk K.** Four detectors in
`docs/whole-roll-plan.md` were each tuned until they looked right and each was
confidently wrong on some frame, and a fifth tuned against fifteen frames would
be the same mistake. What it needs is either a better pixel test -- the
exposure-register prediction of the base level, which has not been tried -- or
the measurement moved to where the evidence is strong, which is the delivered
scan rather than the prescan.

## How it gets verified

Offline first, against `rolls/registration-{A,D,E,G,I,J,K}` and
`docs/stefan-judgement.json`: does the new detector place the frames the old
one placed, refuse the four false bands, and move the proposals toward his
verdicts? A change that does not move toward them is not an improvement
whatever the metric says.

Then one roll, and his eye on it.
