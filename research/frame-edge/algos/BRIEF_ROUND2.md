# Round 2: the misses on library 2

Read `algos/BRIEF.md` first; everything there still holds (ownership, interface,
numpy only, no git, no scanner, look at the sheets every iteration). This is
what is new.

## Where things stand

The ensemble (`algos/ensemble.py` = vote of changepoint, chroma, stepline,
gapmodel, each at iter04) is near-perfect on the main set and on Gold 200.
On library 2 -- one C-41 strip, 17 pictures, many sessions -- it **almost
never invents base but misses it**. Starting point on library 2's dev part:

    ensemble iter01 [dev2]    side acc 0.89  false-base 1  missed 14  | move ok 49/63 bad 14
    changepoint iter04        0.90  false-base 1  missed  9  refused  4
    gapmodel iter04           0.89  false-base 0  missed 11  refused  5
    chroma iter04             0.80  false-base 1  missed 26
    stepline iter04           0.70  false-base 0  missed 21  refused 24

The misses, by kind:

1. **Base against a scene nearly as clear as base.** The gap shows dark brown
   in the positive, not black, beside shaded foliage: the step from base to
   picture is a few counts, while the step from base to the *neighbour's*
   picture on the far side is large.
2. **A gap a little way into the frame with the neighbour's picture beyond it**
   (`outer` in the truth) -- base 15-70 columns from the border. gapmodel sees
   many of these and is outvoted.
3. **Slivers and narrow gaps at the border** that one member sees and the
   others call "border".

Frames whose gap lies in the **middle** of the aperture (gap centre >= 100
columns from both borders) are *not* test frames -- Stefan's call; they are
marked `unsuitable` and never scored. Do not optimise for them.

## Your job

Improve **your family** on these misses **without inventing base**.

- Copy your module to `algos/<family>_v2.py` and change only the copy. The
  iter04 module must stay exactly as it is: the pre-registered results depend
  on it. Results go to `results/<family>_v2/iterNN/`.
- Every iteration, run **both** development sets and report both lines:

      cd research/frame-edge
      uv run --no-sync python run.py <family>_v2 iterNN                   # main dev (98 frames)
      FRAME_EDGE_WORK=$PWD/lib2 uv run --no-sync python run.py <family>_v2 iterNN   # library 2 dev2 (78)

  (use `--no-sheets` while exploring; render sheets for the iterations you
  report). Library 2's sheets and scores land in `lib2/results/...`.
- **Hard constraints:** false base stays at 0-1 on each set; the main dev
  score must not fall below your iter04's; mirror stays exact.
- Frame width: library 2 measures **438 columns** (38 gaps), the main set
  ~440. A gap plus a frame is the pitch (~455 columns at the doc's unit; the
  38.0 mm film standard is 448.8).
- Budget: at most **four** iterations (iter01..iter04 of `_v2`).

## Never look at

- `locked/` in either folder, and the test rolls `gold200` / `hwcheck`.
- library 2's test part: frames with `split == "test"` in
  `lib2/data/manifest.json` (5 whole pictures, drawn at random -- see
  `lib2/split.json`, which you may read only to know what to avoid).
  `run.py` without `--split` never touches them.

## Report

The idea of each change and why; both summary lines for the last iteration
and for your iter04 baseline; which dev2 misses it fixed and which remain,
and why; any main-dev frame that changed. Keep the module documented as
before.
