# Multi-exposure and multi-pass: archived

Scanning one frame several times and combining the passes was built, measured
twice, and taken out of the driver on 2026-09-26. It is kept here, whole, so
that nothing has to be rediscovered if the question comes back.

**The verdict:** it works, and it is not worth it.
- **The gain.** Registered, and with its merge fixed, combining passes lowers
  shadow noise by 2-7% against a single pass.
- **By eye.** Side by side at 100% the two look the same.
- **Why.** Most of what reads as noise on film is grain, identical in every
  pass. The scanner's own random noise is small beside it.

`plan.md` has the numbers and how each was computed.

## Two findings that outlive the feature

- **Passes of one frame are not pixel-aligned.** The carriage lands slightly
  differently for every pass: 2.45 lines and 0.60 columns across a nine-pass
  bracket. Anything that compares passes pixel by pixel has to register them
  first.
  - That drift, not the exposure, is what made the first study conclude that
    "passes stop agreeing as the bracket widens".
  - `code/passes.py` does the registration: sub-pixel, numpy only, and it
    moves a pass without smoothing its noise.
- **The sensor's knee** at 0.80 of full scale lives on in the driver as
  `rps7200.direct.CLIP_START`, which metering aims below.
  - `solve_relation`, the per-channel fit between two exposures, lives on in
    the `measure-scan-quality` skill's `metrics.py`.

## What is here

| path | what it is |
|---|---|
| `plan.md` | The study: the verdict, the registered re-measurement, and the earlier unregistered one it corrects |
| `results.md` | The final numbers, as the scripts below print them |
| `code/bracket.py` | The merge: inverse-variance weighting, per-channel exposure fit, disagreement gate, valid masks |
| `code/passes.py` | Sub-pixel pass registration and a Fourier-phase shift |
| `code/scan_bracket.py` | `bracket_ladder` and `scan_bracket`, formerly methods of `DirectScanner`, as a mixin |
| `tests/` | The feature's tests: `uv run pytest docs/multi-exposure/tests` |
| `analysis/merge_library.py` | Re-merge stored passes; prints shifts, agreement before and after, noise vs every pass |
| `analysis/drift_by_band.py` | How far each pass sits from the first, band by band down the frame |
| `analysis/noise_split.py` | Random share and ceiling of a repeat pair, as stored and registered |
| `analysis/resampling_control.py` | Proof that shifting a pass leaves its noise alone |
| `analysis/crops.py` | The 100% side-by-side the verdict was reached on; writes to `previews/` |

Every script reads the stored raw passes in `library/` and applies today's
correction; nothing needs the scanner. Run them from the repository root, for
example:

    uv run python docs/multi-exposure/analysis/merge_library.py --tag bracket-3600x9

**No pictures and no library data are kept here.** The scripts write their
TIFFs and PNGs to `previews/` or wherever `--out` points, and git ignores both.
The passes they read live in `library/`, which is ignored too; they are named
here by entry id only.

## Where it used to be

The older sections of `plan.md` name the paths the code had at the time.

| there | now |
|---|---|
| `rps7200/bracket.py` | `code/bracket.py` |
| `rps7200/passes.py` | `code/passes.py` |
| `DirectScanner.bracket_ladder` / `.scan_bracket` in `rps7200/direct.py` | `code/scan_bracket.py` |
| `tools/scan.py --bracket N --stops X` | removed; see below |
| `tools/library.py merge` | `analysis/merge_library.py` |

The last version in which all of it ran inside the driver, with `--bracket` in
`tools/scan.py`, is commit `52d513f`:

    git show 52d513f:tools/scan.py
    git show 52d513f:rps7200/direct.py

To bring it back, start from `code/` here rather than from that commit:
- **`passes.py`.** The copy here carries its own `cross_power`. The one at
  `52d513f` imported it from that commit's `rps7200/uniformity.py`.
- **`scan_bracket`.** It has to go back into `DirectScanner`, because it drives
  the device, calling `scan()` once per rung. It belongs in the driver, not in a
  script.

## What was never settled

- **The noise model.** The merge still assumes α = 1, β = 4096, about four times
  too optimistic for corrected pixels, so its disagreement gate distrusts ~12%
  of a registered frame.
- **The leftover tilt.** A rigid shift leaves ±0.3 lines at the top and bottom
  of a frame; a shift that varies down the frame was never built.
- **Dense frames.** Only one slide was measured, whose shadows sit far above
  the noise floor. A dense or underexposed frame is the one case in which the
  scanner's own noise could matter.

The approach to registration and the merge's structure come from
[pyopticfilm](https://github.com/jboneng/pyopticfilm) (GPL-3.0-or-later, the
same licence as this project).
