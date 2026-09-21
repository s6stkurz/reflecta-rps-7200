# Why the frames are not quite right, and what to do about it

> **Millimetres are prohibited in this work.** Every distance here is in units
> of the adjustment parameter -- one unit is the distance one increment of
> `param` adds. See `docs/step-calibration-plan.md` and CLAUDE.md.

## Why

A fifteen-frame roll was scanned at 600 dpi with every frame held to a position
proposed from its own prescan walk. The software called fourteen of fifteen
`held` with a median residual under one unit. Stefan looked at the files and
gave a per-frame verdict, recorded verbatim in `rolls/stefan-judgement.json`:

    good        : 3, 5, 6, 7, 8, 12, 14
    a bit RIGHT : 1, 4, 11, 13, 15    black shows at the right; it should have
                                      moved further right
    a bit LEFT  : 2
    bad         : 9, 10

So the software's own account -- fourteen held, residuals inside the deadband --
is not what a person sees. **That gap is the subject of this plan.** It is also
the third time today the eye has been right and a number wrong, which is what
CLAUDE.md says to expect.

**Five frames show black at the right and one at the left.** One-sided by five
to one is not scatter. Something is systematically placing frames short.

## What is already known

* **The inheritance is near-perfect.** Across all fourteen frame-to-frame
  transitions of this roll, a frame arrives within one prescan pixel of where
  the previous frame was left. A sub-frame command does not touch the frame
  counter, so a correction carries, and this roll shows it carrying cleanly.
  That is why a single bad frame propagates.

* **Frames 9 and 10 are one failure, not two.** Frame 9's proposal came from a
  false band: its whole left side sat at the film-base level -- smooth picture --
  and only the flatness test separated it, splitting one region at an arbitrary
  column. The proposal came out 23 units away from what every neighbour said.
  Frame 9 then *reached* that wrong target, so the roll recorded it as `held`
  and counted no miss. Frame 10 inherited the displacement, and its own honest
  proposal was refused by the travel budget.

* **The travel budget punishes honesty.** `hold_plan`'s budget is the target
  plus a fixed headroom, so a frame with a *wrong, large* target authorises
  itself more travel than a frame with a right, small one. Frame 9's bogus
  target bought 36 units of travel; frame 10's correct target bought 25, and
  frame 10 was refused its last move by **0.33 units** -- one and a half percent
  of the headroom. Three moves would have converged it.

* **A reachability rule now refuses the band that caused it.** A band set back
  from the edge is a gap only if what stands between it and the edge is picture.
  Landed after this roll ran, so the delivered frames still carry the damage.

* **The frame width is not what the code assumes.** Fifteen frames across six
  walks show base at both edges, which measures the camera frame directly:
  **316 to 336 units, median 334**, against the **340.6** the code assumes.
  `TARGET_GAP` is derived from that assumption, so every proposal ever made
  carries the error.

## The leading explanation

If the frame is 334 units and the aperture 345.2, the slack is **11.2 units**,
not the 4.6 the code believes. Driving the left edge to `TARGET_GAP` = 2.32
units then leaves **8.9 units at the right**. That is black at the right, on
every frame, by construction -- and it is what Stefan sees.

It also predicts something sharper, which is the strongest test available:
**a frame read from the left edge and a frame read from the right edge are
driven to different positions**, because each drives its own edge to 2.32 and
the other edge absorbs the remaining slack. On this roll twelve frames were read
from the right and three from the left, so the strip should contain two
populations about 6.6 units apart.

**This is not yet confirmed and the sign must be checked rather than believed.**
An earlier attempt at this arithmetic today came out backwards, and the
nine-agent analysis running against Stefan's verdicts was told to say so loudly
if it does again.

## What to do, in order

### 1. Measure the frame, do not infer it

`docs/step-calibration-plan.md` traverses a frame in parameter units and
measures the aperture and the camera frame directly. Until that number exists,
`TARGET_GAP` should not move: it is derived from the frame width, every proposal
derives from it, and the indirect measurement has a spread of twenty units
clustered in two groups rather than scattered about one value -- which usually
means the boundary column is being counted differently on different frames, not
that the film varies.

### 2. Score the measurements against the verdicts

For all fifteen frames, put every number the software has beside Stefan's
verdict, and find which one predicts him. Specifically: how much base shows at
each edge of the delivered frame, and whether the good frames split differently
from the "a bit right" ones. If they separate, the difference is what the target
is wrong by, measured against the only authority that matters.

### 3. Fix what is confirmed, record what is not

Candidates, in descending order of confidence:

* **The budget should not scale with the target.** A frame that needs more
  travel gets more budget only if its target is large, which is exactly
  backwards when a large target is the symptom of a bad proposal. This is
  confirmed by frames 9 and 10 and costs nothing to correct.
* **A disagreement of 23 units between members should refuse, not propose the
  lone reading.** Frame 9's members disagreed by that much and the fallback
  proposed the outlier. The sheet is reviewed by a person, so the case for
  showing him *something* is real -- but it should be shown as a refusal with
  both numbers, not as a proposal.
* **`EDGE_FRACTION` permits a band to begin 41 units from the edge**, where the
  whole inter-frame gap is about 19. The largest genuine sliver measured is
  under 5. It is roughly eight times looser than the film allows.

### 4. Explore what Stefan suggested

Every detector in this file starts by averaging the channels to grey, so the
whole stack is colour-blind. His suggestion -- a colour *noise* measure -- has a
specific reason to work that the rejected orange-mask ratio did not: sensor
noise is independent per channel, while picture structure is correlated across
them. So a column of unexposed base should show near-zero correlation between
channels and a column of picture a high one, regardless of level or flatness.

That is a genuinely different axis from everything in the ensemble, and it
would fail differently -- which is the repo's own test for whether a second
statistic earns its place. It must be shown to separate cases the level and
flatness rule does not, over many frames from several walks, with the "known
base" columns labelled by something other than the detector under test.

## What not to do yet

* **Do not move `TARGET_GAP`** until the frame is measured rather than inferred.
* **Do not shrink the deadband.** Already investigated and recorded in TODO.md:
  the constant has five separate roles, and halving it makes ensemble members
  agree less often, which produces more of the unverified frames that were the
  real problem.
* **Do not re-scan to check any of this.** Six walks and three rolls of the same
  strip are on disk with their raw bytes. Every question above can be answered
  offline at no scanner cost, which is what CLAUDE.md asks for.

## How it gets verified

Offline, against the stored walks, before any scanner time: replay
`propose_offsets` over `rolls/registration-{A,D,E,G,I,J,K}` with and without
each change, and score the result against `rolls/stefan-judgement.json`. A
change that does not move frames toward his verdicts is not an improvement
whatever the metric says.

Then one roll, and his eye on it. That is the acceptance test and there is no
other.
