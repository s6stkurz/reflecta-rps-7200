# Find the useful scanning resolution, by measurement

## Context

A first pass over the existing `series_*` scans put the useful resolution far
below the scanner's 7200 dpi rating: real detail on that frame ran out around
**26 cycles/mm**, needing ~1340 dpi to sample, and the 1800 dpi scan showed
clear aliasing — 1.85x the power of the 3600 dpi scan at the same physical
frequency near its own Nyquist.

That was one frame, at whatever exposure auto-exposure happened to pick, on
five resolutions that were never intended as an experiment. It is enough to
suspect 2400 dpi is the sweet spot; it is not enough to settle it.

This plan runs it properly: every resolution the device actually accepts, in
RGB and RGBI, on one chosen frame at one fixed exposure, with the times
recorded — and produces the number that decides what to scan at from now on.

**Step 0: copy this plan to `docs/dpi-tradeoff-plan.md`**, alongside the other
three.

## What makes the measurement valid

Four things, each of which would otherwise wreck it:

- **One exposure for the whole series.** Noise level sets where detail
  disappears, so a series metered per pass measures the metering, not the
  optics. Meter once at 1800 dpi, then run every pass with
  `exposure_scale=1.0`: `Settings.scaled(1.0)` returns the settings unchanged
  (`rps7200/direct.py`), so each scan writes back exactly what it read. Every
  sidecar already records `exposure`/`gain`/`offset` — check afterwards that
  they never moved, rather than assuming.
- **One frame, one position.** `FULL_FRAME` for every pass, no `advance`, film
  untouched from first scan to last.
- **Spectral analysis, not pixel differences, across passes.** Passes can sit
  at different column offsets (two 3600 dpi passes correlated at r=0.936 only
  once shifted 16 columns, still unexplained). Power spectra are shift
  invariant, so they are immune to it. Anything comparing pixels must stay
  *within* one pass — downsample the 3600 dpi scan and compare it to itself.
- **Run it after the shading correction works.** Fixed-pattern column noise
  raises the noise floor and so lowers the apparent resolution limit. Until
  then, every number here is a lower bound; say so rather than quoting it as
  the answer.

## Choosing the frame

Scene-limited, not scanner-limited: the measurement can only find detail the
negative actually holds. Pick a frame that is sharply focused, well exposed,
and has fine detail spread across the field rather than in one corner —
foliage, brickwork, fabric, text. A soft or motion-blurred frame will report a
low limit that says nothing about the scanner.

Worth stating plainly in the results: without a resolution target (USAF 1951 or
similar) this measures *this negative through this scanner*, not the scanner's
MTF. That is the more useful number for deciding how to scan, but it is not
comparable with a lab figure.

## Part 1 — which resolutions the device accepts

There is no client-side validation: the value goes straight into MODE SELECT as
a 16-bit field and the device refuses what it dislikes with sense `0x26/0x82`,
"MODE SELECT invalid: resolution too high" (`rps7200/direct.py`). So a bad
value fails loudly and cheaply.

Probe with a **short frame** — a few hundred lines of `FULL_FRAME`'s width —
since scan time follows line count, so each probe is seconds rather than
minutes. Candidates:

- integer divisors of 7200, which is what the captures have only ever used:
  `300, 360, 400, 450, 480, 600, 720, 800, 900, 1200, 1440, 1800, 2400, 3600, 7200`
- deliberate non-divisors, to learn whether arbitrary values work at all:
  `1000, 2000, 3000`

Record accepted/refused and the sense data for refusals. This alone is worth
having written down.

## Part 2 — the timing table

Full-frame scans at every accepted resolution, RGB and RGBI. From the existing
sidecars, time follows the **line count**, not the pixel count:

```
              RGB      RGBI
 900 dpi     38.8 s   226.6 s
1800 dpi     69.6 s   227.2 s     <- identical: an IR floor of ~227 s
3600 dpi        -     333.7 s
```

RGB fits `t = 8 + 0.036 x lines`. RGBI has a floor around 227 s that resolution
does not move until roughly 1800 dpi. Both need confirming across the full
range, and the RGBI floor's exact shape is the most interesting unknown — if it
holds to 2400, IR scans get more resolution for nothing.

Produce one table: dpi, mode, pixels, lines, measured seconds, file size, and
seconds relative to 1800 dpi.

Budget roughly 15 min for the RGB set and 25-30 for RGBI, plus one shading
calibration (~3.5 min). 7200 dpi in RGBI is ~570 MB and slow — take it once,
in RGB only, purely as the high-water reference.

## Part 3 — the resolution analysis

Three measurements, because no single one is conclusive.

**A. Where detail meets noise** (absolute, from the highest-resolution pass).
Row-averaged power spectrum of a detailed crop, frequency axis in cycles/mm so
it is comparable across resolutions. The flat top of the band is the white
noise floor; subtract it, and the frequency where the remainder falls to it is
the limit. Multiply by 2 x 25.4 for the dpi needed to sample it. This is what
gave 26 c/mm before.

**B. Aliasing per configuration** (relative — the answer if A proves too
scene-dependent, and what the user asked for as the fallback). For each
resolution, compare its power near its own Nyquist with the same physical
frequency in the highest-resolution pass. A ratio near 1 means it is resolving;
well above 1 means it is folding energy back. Previously: 1.09 at 8 c/mm rising
to 1.85 at 34 c/mm for 1800 dpi. **The lowest resolution whose ratio stays near
1 at its own Nyquist is the recommendation** — and this needs no absolute
calibration at all, only that the scans are of one picture.

**C. What each step actually adds** (within one pass, so registration cannot
confound it). Downsample the highest-resolution scan to each lower one, restore
it, and measure the residual against the noise sigma. A step whose residual is
at the noise level adds nothing.

Also report the per-channel figure. Blue is metered worst on this scanner and
may well resolve less than red and green; if so, that is worth knowing.

## Implementation

Two tools, both driven from the existing API — no changes to `rps7200/`
expected:

- **`tools/dpi_series.py`** — runs Parts 1 and 2. Probes candidates, meters once
  at 1800 dpi, then scans each accepted resolution in both modes with
  `exposure_scale=1.0`, filing every pass through `rps7200/library.py` with
  `tags=["dpi-series"]` and the frame's details. The library keeps the raw bytes
  and the calibration, so the whole series can be re-analysed later without
  rescanning — which is exactly what it is for. Writes the timing table as
  markdown and CSV.
- **`tools/dpi_analysis.py`** — runs Part 3 over the library entries carrying
  that tag. Reuses `rps7200.library.load()`. Emits the numbers, a log-log plot
  of signal against the noise floor with each Nyquist marked, and a 100% crop
  comparison at the two or three candidate resolutions.

## Verification

1. **Exposure held.** Assert `exposure`, `gain` and `offset` are identical in
   every sidecar of the series. If they moved, the series is void — stop and
   say so rather than analysing it.
2. **Same frame throughout.** Cross-correlate the downsampled thumbnails of
   every pass; all must agree. Catches the film having shifted mid-series.
3. **Timing model.** Check measured times against `t = a + b x lines` per mode
   and report the residuals. A pass far off the line means something else was
   going on.
4. **The three methods agree.** A, B and C should point at the same
   neighbourhood. If they disagree, report the disagreement rather than picking
   the convenient one — most likely cause would be a scene without enough fine
   detail to measure.
5. **`tools/library.py verify` and `reconstruct`** clean over the new entries,
   proving the series is reusable.
6. **Send the plot and the crops** for visual confirmation, plus the three
   standing TIFFs if any correction path changed.

## The decision it produces

A single recommended default for RGB and for RGBI, with the cost of each step
in time and bytes, and an explicit statement of what it depends on — this
negative, this exposure, this noise floor. If the shading correction later
lowers the noise floor, the measurement is re-runnable from the library with no
scanner at all, which is the point of keeping the raw bytes.
