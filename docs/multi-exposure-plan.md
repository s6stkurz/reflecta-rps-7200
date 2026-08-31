# Multi-exposure: capture a bracket, let NegPy merge it

## Context

An earlier version of this plan argued against multi-exposure, and was wrong on
its central number (it is in the git history of this file). It claimed the long pass was capped at **×1.23** because
blue "already runs at 81% of full scale". That figure came from the vendor's
*no-film calibration* exposure and from a post-auto-exposure sidecar — neither
of which is a floor. The device's actual base is a fixed reference (9604 / 6506
/ 6506, confirmed across 36 captured READ GAIN/OFFSET responses), so the real
picture is:

| | base | ceiling | metered uses | left for a longer pass |
|---|---|---|---|---|
| R | 9604 | 65535 (×6.82) | ×2.88 | ×2.37 |
| G | 6506 | 65535 (×10.07) | ×6.03 | **×1.67** |
| B | 6506 | 65535 (×10.07) | ×2.6–×10.07 | varies |

**Green binds, not blue**, and the honest headroom is ×1.67 — 0.74 stops, not
the 0.09 density the old plan claimed. Worth doing, and worth being honest that
it is modest.

## What the other drivers do

**pyopticfilm** (Plustek OpticFilm 8200i, via NegPy's `plustek_backend.py`):
`exposure_long` is a fixed per-model constant and the device has **no
auto-exposure at all** — `_validate_params` refuses `auto_exposure` outright.
The pass layout is `_gl128_me_pass_layout`: `n_early = 2 if capture_ir else 1`,
then one long pass, then "Merging exposures". So: the normal pass(es), one
longer pass, merged inside the library. Two exposures, both hardcoded.

**nkscan** does not do multi-exposure. It exposes `Samples` — the scanner
averaging 1–16 reads per line — which is multi-*sampling*.

**NegPy itself already merges brackets, and this is the important one.**
`negpy/features/hdr/logic.py` takes two or more exposures of one frame and:

- **solves the exposure ratios from the pixels**, so our metadata does not have
  to be accurate
- **aligns the frames itself** (`estimate_shift`, `cv2.warpAffine`)
- blends with a rolloff from 0.90 to `SATURATION = 0.995`, so the frame a pixel
  comes from changes gradually instead of banding
- picks as reference the longest exposure with under 0.1% clipping, keeping the
  result in [0, 1] so recovered range arrives as shadow detail

So **do not write a merge.** Ours would be worse, and Stefan already runs NegPy.
The driver's job is to produce a well-formed bracket.

## What to build

A bracket capture in `rps7200/direct.py`, reusing what already exists:

- `Settings.scaled()` already takes a per-channel factor and clamps at 65535
- `auto_exposure()` already meters per channel in two RGB rounds
- `scan(exposure_scale=...)` already applies a scale per pass
- `rps7200/library.py` already files each pass with its raw bytes and calibration

`scan_bracket(stops, passes, ...)`:

1. Meter once, as now. That is the reference exposure.
2. Derive the ladder **downward from the ceiling**, not upward from the metered
   value: the long pass goes as high as the timer allows (green's ×1.67 over
   metered), and the rest step down by `stops`. Going up from metered would
   waste the headroom that exists below it.
3. Refuse, with the numbers, when the requested span does not fit — the ceiling
   is per channel and green runs out first.
4. Scan each pass at `FULL_FRAME`, film untouched, calibration reused.
5. File every pass in the library, tagged `bracket:<id>` with its index and
   requested ratio, and write the TIFFs NegPy will consume.

Expose it as `tools/scan.py --bracket N [--stops 0.7]`.

**Do not re-run `calibrate_shading` between passes** — one reference per session
is what the vendor does, and the passes must share it to stay comparable.

## The honest ceiling

The gain over a single metered scan is the exposure ratio: **×1.67 at best, 0.74
stops.** Averaging two passes gives ×1.41 for the same two passes' time, so
multi-exposure wins, but not by much. Both beat one pass.

This should be measured, not assumed, and the plan below measures it. If it
comes out at ×1.2 in practice the feature is not worth shipping, and that is a
legitimate outcome.

Two levers that are *not* timer-limited and are worth one experiment each,
because they would change the answer:

- **Analogue gain** (8-bit, currently 39/33/21) amplifies before the ADC, so it
  helps if quantisation rather than shot noise dominates the shadows.
- **Lamp level** (`Settings.light`, vendor sends 6, pieusb default 4).

## Verification

The failure mode to guard against is shipping a feature that measurably does
nothing, so every check is a measurement with a number that decides it.

1. **The exposures are actually what was asked for.** For each pass, measure the
   median of a mid-tone patch and check the ratios between passes match the
   requested ones. This is the linearity check, and it is also the one that
   catches the timer wrapping: past 65535 a pass comes out *darker*, which shows
   up immediately as a ratio going the wrong way. Do it with **no film loaded**
   first, where the level is uniform and unambiguous.
2. **Nothing clips that should not.** The short pass must have zero saturated
   pixels; the long pass may clip, but only where the short one has data.
   Report the saturated fraction per channel per pass.
3. **Shadow noise actually improves.** Take the densest 10% of the frame and
   report the per-channel standard deviation for the single metered pass and for
   the long pass. Expect improvement by the exposure ratio if read-noise limited,
   by its square root if shot-noise limited. **If neither shows, say so and stop**
   — that is the measurement that decides whether this ships.
4. **NegPy accepts the bracket and reports the span it sees.** It prints
   "Merged N exposures spanning X stops", solved from the pixels. If its number
   disagrees with the requested span, our exposures are not doing what we think.
5. **Alignment is good enough for NegPy's aligner.** Cross-correlate the passes;
   `estimate_shift` handles small offsets, but the unexplained pass-to-pass
   column offset in `TODO.md` is exactly the thing that could defeat it. Measure
   the shift between bracket passes and report it.
6. **The library round-trips.** `tools/library.py verify` and `reconstruct` clean
   over the new entries, and each pass carries its own exposure in its sidecar.
7. **The three comparison TIFFs**, plus a 100% crop of a dense region from the
   single pass and from the merge, for Stefan's eye. His read decides, and a
   downscaled preview cannot show shadow noise.

## Cost

Each pass is a full scan: ~70 s at 1800 dpi RGB, ~230 s with infrared (the
~212 s infrared floor applies per pass). A three-pass bracket with IR is about
12 minutes per frame, so this is a per-frame tool, not a roll tool.
