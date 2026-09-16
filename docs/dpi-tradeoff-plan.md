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

  Of those, the captures now contain **300, 600, 900, 1200, 1800 and 3600** —
  1200 from `captures/slide.pcapng`, which is why it is on the window's ladder.
  The rest of the list is still a list of guesses.
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
does not move until roughly 1800 dpi.

> **The 333.7 s at 3600 dpi is superseded for estimation.** A sweep of every
> resolution at one held exposure (2026-09-16, `docs/fast-infrared-plan.md`)
> measured an untied RGBI pass flat at 219.2-220.3 s from 300 to 1800 dpi and
> **221.0 s at 3600** -- so the floor holds far past 1800, and does not climb to
> 334. The two disagree because they are different film at different exposures
> and scan time tracks exposure: the same 1800 dpi pass took 250.5 s on colour
> negative and 220.3 s on that slide. `estimate_seconds` now uses the sweep,
> which is the one that covers the range in a single run and is therefore
> internally comparable. The figure above stays as what was measured that day. Both need confirming across the full
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

---

# Results, 2026-09-13

Run on the ladder Stefan scanned 2026-09-11 10:20-10:42: one frame — a wall of
old televisions, plenty of fine detail — seven RGB passes from 300 to 7200 dpi
with a repeat pair at the top. `tools/dpi_analysis.py` reproduces all of it from
the library, with no scanner.

## The recommendation

**RGB: 3600 dpi** for quality, **1800 dpi** when time matters.
**RGBI: 3600 dpi**, because there resolution is nearly free.

| dpi | seconds | MB | verdict |
|---|---|---|---|
| 600 | 34.1 | 3.0 | visibly blocky |
| 1200 | 59.9 | 11.9 | big gain over 600 |
| 1800 | 85.5 | 26.7 | clear gain again; **the fast default** |
| 3600 | 165.4 | 106.8 | modest gain, least aliasing; **the quality default** |
| 7200 | 314.4 | 427.4 | **adds nothing** — softer, 2x the time, 4x the bytes |

**7200 dpi is past the optics.** In matched 100% crops its edges are *softer*
than 3600, which is what oversampling a blur looks like: the same optical
unsharpness spread over twice as many pixels. It costs 149 s and 320 MB more per
frame to record that blur in more detail.

**And it cannot be shading-corrected at all**, which was established separately
and for unrelated reasons — the device will not produce a shading reference
wider than 5172 columns at any resolution, against a 7200 dpi pass's 10344
(`docs/7200dpi-plan.md`). `scan()` refuses such a pass rather than ship it
uncorrected. Two independent arguments, one from the optics and one from the
calibration hardware, landing on the same conclusion: **do not scan at 7200
dpi.** The vendor agrees by omission — 7200 dpi appears in none of the nine
CyberView captures, whose highest resolution is 3600.

## The infrared floor does not move with resolution

The most useful number here, and it needed no new scanning — timing does not care
which frame it is, so existing library entries answer it:

```
RGBI  300 dpi   248.3 s        RGBI 3600 dpi   253.1 - 256.5 s  (7 passes)
RGBI  600 dpi   231.8, 248.7   RGBI 3600 dpi   254.9, 291.2
```

A twelve-fold resolution increase costs about **six seconds**. The ~250 s
infrared floor dominates everything. So there is no reason to scan infrared at
low resolution — take RGBI at 3600 and it is essentially free.

## What each method actually returned

**B — aliasing at each pass's own Nyquist — is the one that worked.** Power
density near each resolution's Nyquist against the 7200 dpi pass at the same
physical frequency. 1.0 means resolving; above means folding energy back:

```
channel      600     1200     1800     3600
    red     1.58     1.87     1.93     1.22
  green     1.46     1.77     1.99     1.23
   blue     1.32     1.68     1.84     1.33
```

3600 is clearly the least aliased. The method carries a **sanity band** — density
well below Nyquist, which must already read ~1.00 — and it reads 0.75-0.98. So
the comparison is good to roughly ±20%: enough for the trend and for 3600 being
best, not enough to separate 1200 from 1800.

That band earned its place twice. A first version reported 1.42 at 3600 where a
resolving pass must read ~1.0, because `rfft` does not normalise and raw power
scales with row length squared. The fix was to convert to density,
`|X|^2 dx / (N mean(win^2))` — and the band then read exactly `dpi/7200`,
catching a second missing factor of N. Without it both versions would have been
reported as findings.

**A — where detail meets the noise floor — did not work here.** It returned
141.7 c/mm for every channel, which is precisely 7200 dpi's Nyquist: the spectrum
never falls to the floor within the measured band, so the "flat top of the band is
white noise" assumption does not hold on this frame. Grain reaches Nyquist.

**C — what each step adds — is ambiguous, not wrong.** Downsampling the 7200 crop
and restoring it leaves a residual of 3.9-22x the random noise sigma at every
step, decreasing with resolution but never reaching 1. But the floor it compares
against is the *random per-pass* noise from the repeat pair, and **grain is fixed
between repeats**, so grain counts as signal. C therefore says "7200 captures
something 3600 does not" without being able to say whether that something is
detail or grain. The crops say grain.

Noise floor at 7200 dpi, from the repeat pair (`noise_split`):

```
  red    random 262.6 DN   total 537.3 DN   random share 48.9%
green    random 212.8 DN   total 475.9 DN   random share 44.7%
 blue    random 129.3 DN   total 330.4 DN   random share 39.1%
```

## What this rests on

- **One negative, one exposure, one noise floor.** Without a resolution target
  this measures *this film through this scanner*, not the scanner's MTF, and is
  not comparable with a lab figure. It is the more useful number for deciding how
  to scan.
- **Exposure drifted 1.3-1.4%** across the series (R 27214-27596, G 41446-41971),
  where the plan called for it pinned. The effects measured are 9-99%, so a 1.4%
  change in signal — 0.7% in the shot-noise floor — is an order of magnitude
  below the smallest of them. Recorded rather than hidden; if a future result
  turns on a margin this thin, re-scan with `exposure_scale=1.0`.
- **Blue is a lower bound.** Its exposure sat at 65535, the 16-bit timer ceiling,
  on every RGB pass, so blue is under-exposed (mean ~20100 against 54000-61000 for
  red and green) though never clipped. A darker channel resolves less; blue's
  figures above are a floor, and the fix is RGBI, where blue is ~5x more sensitive
  and does not rail.
- **Re-runnable.** Everything came from stored raw bytes. When the shading
  correction lowers the noise floor, run `tools/dpi_analysis.py` again — no
  scanner, no film, no re-shoot.
