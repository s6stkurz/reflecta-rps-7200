# Brief for a detector agent

You own **one** detector family: `research/frame-edge/algos/<family>.py` and
`research/frame-edge/results/<family>/`. Touch nothing else. No git, no
scanner, nothing under `rps7200/`, `tools/`, `tests/`. Run python as
`uv run --no-sync python ...` from `research/frame-edge/`.

## The question

A 300 dpi prescan of a 35 mm **negative** strip, `(~286, 428, 3)`, columns =
the transport axis. Between frames is **unexposed film base**. On the
negative it is the **brightest** thing on the film (orange on C-41, about
RGB (68, 30, 15) in 8-bit counts; neutral on B&W). Where does the picture end
and base begin, on each side -- or is there no base at all? Work **in the
negative**, not the inverted image.

Stefan's description of real base, the one test that has held: *"a sharp line,
completely black from top to bottom"* (in the positive). Uniform, full height,
straight near-vertical boundary.

Why this is hard, measured over four earlier detectors
(`docs/frame-measurement-plan.md`, `docs/registration-accuracy-plan.md`):

* A dark scene area (silhouette at dusk, black wall, shade) is nearly as clear
  as base on the negative, and flat by column. Level, flatness, 2-D uniformity
  and infrared all failed to separate it. Every detector that averaged the
  rows first was confidently wrong somewhere.
* A frame that is well placed shows **no** base -- or under one column. The
  detector must then say `picture_to_border` **confidently**; "found nothing"
  is not the same as "there is nothing". This is also how the correction gets
  verified on the second prescan.
* The empty gate (no film, ~182 neutral in 8-bit) is brighter than base and
  must never be called base (`no_film`). Blank frames are `all_base`.
* Base can be one or two columns wide at the very border.

## Interface (from `common.py`, frozen -- read it)

```python
def detect(image: np.ndarray, ctx: dict) -> EdgeResult
```

* `image`: float32 `(H, W, 3)` in the file's **native counts** (uint8 and
  uint16 sources both occur; the scale differs by ~250x). Normalise against
  your own estimate of the base or the frame, never against a dtype maximum.
* `ctx`: `id`, `roll`, `film_type` (`c41`/`bw`/`unknown`), `dtype`,
  `roll_ids` (other frames of the same roll -- `common.load(id).image`; the
  software has the whole walk when it proposes), `mirrored` (True when the
  runner feeds you the frame flipped left-right to test symmetry; do not use it
  to change behaviour).
* Return `EdgeResult(left=Side(...), right=Side(...), debug={...})`.
  `Side.state` is one of `edge`, `picture_to_border`, `all_base`, `no_film`,
  `refuse`. For `edge`: `x` is a **column boundary** float -- left: base
  occupies `[0, x)`; right: base occupies `[x, W)` -- with an honest `lo`/`hi`
  and `conf` in 0..1. Optional `outer` (far side of the gap if the neighbour's
  picture shows), `x_top`/`x_bottom` for tilt. The edge is the per-row **50%
  crossing** between base level and picture level.
* **numpy only** (Pillow for your own debugging pictures). No scipy, no
  sklearn: the winner must drop into `rps7200/framing.py`, which is numpy-only.
  Must run in well under a second per frame.

## The loop

    uv run --no-sync python run.py <family> iter01

runs your detector on every dev frame, the duplicates (same position at another
exposure / bit depth -- answers must not move), the stage9b ladder (edges
should step ~7 columns linearly), and every frame mirrored (the answer must
mirror); writes `results/<family>/iter01/{results,scores}.json` and one
`sheet_<roll>.png` per roll (inverted, base black, red line = you, green =
ground truth). **Look at the sheets** -- Read them -- every iteration; the
numbers do not show *why*. `scores.json` `worst` lists the biggest misses.

`ground_truth.json` (the dev labels, by eye) may not exist when you start.
Until it does, work from the label-free checks (pairs / dups / ladder / mirror
in `scores.json` `label_free`) and your own eyes on the sheets and
`crops/label/<id>_A.png` (the zoomed edge views). Check for it each
iteration; once it is there, score against it.

**Never look at the test split** (rolls `gold200`, `hwcheck`). They are not in
the dev run and must stay unseen until the final scoring.

Do at most **four iterations** (`iter01`..`iter04`). Keep each one: do not
overwrite an earlier iteration's folder. Guard against over-fitting: a rule
that fixes one frame and has no reason beyond that frame is not a rule. Prefer
a detector that **refuses** to one that is confidently wrong -- a refusal costs
a manual check, a wrong answer costs a frame.

## What to hand back

A short report: the idea, what each iteration changed and why, the last
iteration's summary line (printed by `run.py`), where it still fails and
whether that failure is fixable within the family, and which frames it
refuses. Keep the module documented the way `common.py` is: say what was
measured and why each constant has its value.
