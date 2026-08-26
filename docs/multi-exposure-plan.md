# Multi-exposure / multi-sampling for the RPS 7200

## Context

SilverFast's Multi-Exposure scans a frame twice at different exposures and merges the
two, to pull shadow detail out of dense film without clipping the clear areas. The
question is whether to build the same thing here, and whether it is worth it.

Research says: **not in the classic form, and not for the film you shoot** — but a
closely related technique is worth building, and one hardware fact decides it.

## The finding that settles it

Exposure on this scanner is a 16-bit Timer 1 count. `pieusb` measured the overflow
behaviour directly on this hardware family:

```
exp_rel   1086   1500  |  1598  1599  |  2200  3300
measured  x10.5  x14.7 |   clip x1.00 | x5.79 x1.06
```

`timer = exposure_time * exp_rel / 100`, and it **wraps** past 65535 — a scan asked to
be brighter comes out darker. The ceiling is therefore a fixed 65535 timer counts, no
matter how the product is split between the absolute and relative fields. Relative
exposure below 100% is clamped, so it cannot be used to go the other way either.

Now the actual settings. From CyberView's own `SET GAIN OFFSET` in
`captures/frist_open.pcapng` (frame 5099), and from this driver's scan sidecars:

| channel | vendor exposure | mine | headroom to 65535 |
|---|---|---|---|
| R | 8277 | 27569 | ×2.4 |
| G | 28645 | 39445 | ×1.7 |
| B | **53160** | 52048 | **×1.23** |

Blue already runs at 81% of full scale — the vendor puts it there itself. So the
longest possible "long" pass is **×1.23 on blue**, worth `log10(1.23)` = **+0.09
density**. That is nothing.

Meanwhile averaging two passes at the same exposure gives √2 = ×1.41 noise reduction,
and four gives ×2. **Multi-sampling beats multi-exposure on this scanner from the very
first pass**, and keeps beating it, because it has no ceiling.

`rps7200/direct.py` already knows this ceiling — `Settings.scaled()` clamps exposure to
65535 — it was just never connected to a reason.

## Is it necessary?

Independent testing (filmscanner.info) measured a Canon 9000F Mk II going from density
range 3.17 to 3.98 with Multi-Exposure, so the technique is real. But two things make
it a poor fit here:

- **The RPS 7200 already out-ranges the film.** Claimed Dmax 3.6, measured "magnitude
  3". Colour negative — Kodak Gold 200 — has a Dmax around 2.0–2.2. The scanner has
  roughly a full density of margin over the film. Multi-Exposure targets slides
  (Velvia and friends, Dmax 3.5+) where the film out-ranges the scanner. It is aimed at
  a problem you do not have.
- **The gain is capped at log(2) = 0.3 density per doubling of exposure** even when
  headroom exists, and we have ×1.23.

Shadow *noise* is a genuine problem worth solving — it just is not a dynamic-range
problem, and multi-sampling is the right tool for it.

Users also report ~10% of SilverFast Multi-Exposure scans showing double contours from
misregistration between passes. That failure mode applies to multi-sampling equally,
which is why registration is step 1 below and not an afterthought.

## What NegPy does

`multi_exposure` exists as a capability flag and a checkbox
(`negpy/infrastructure/scanners/params.py:16`, `base.py:37`), gated on
`model.exposure_long`, and passed straight through to `pyopticfilm` — NegPy implements
no merge of its own, and the flag reaches only Plustek OpticFilm 8200i SE / 8100 V2.
Their user guide: *"merges short and long colour passes for more highlight and shadow
detail; it takes longer than a normal scan."*

More relevant: their **nkscan** backend exposes `Samples` — *"reads per line the
scanner averages (1–16). Higher settings cut shadow noise and cost proportionally more
time."* That is multi-sampling, done in the scanner. It is the feature worth copying,
and on this hardware it has to be done host-side.

## Recording the USB channel — probably not worth it

**VueScan** is the practical target if we do record: the trial runs indefinitely,
unlocks multi-exposure and multi-sampling, and only watermarks *saved output* — USB
traffic is unaffected. SilverFast is paid with a demo. Nothing open-source implements
multi-exposure.

But unlike the shading calibration, **there is nothing to discover on the wire.**
Multi-exposure is entirely host-side: the scanner is told a different `exposure_time`
and scanned again, and the merge happens in software where USB cannot see it. A capture
would show us two passes and their exposure values, nothing more.

Worth capturing only to answer three specific questions, and only if the implementation
hits them: what exposure ratio the vendor picks, whether it re-runs calibration between
passes, and whether it re-feeds the film or re-sweeps in place.

## Implementation

### Step 1 — registration test (blocking, and shared with the shading work)

Nothing else matters if passes do not align. This is the same open question left over
from the shading plan (`docs/shading-calibration-plan.md`): two of my 3600 dpi passes
at an identical frame correlate at **r = +0.936 at lag −16**, not at lag 0.

Scan one frame N times without touching anything. For each pair, cross-correlate column
profiles at lags ±32 and row profiles at lags ±32. Outcomes:

- **aligned to <1 px** — proceed to step 2
- **constant integer offset** — correct it, and check `ScanParameters.filter_offset1/2`
  (read in `get_parameters`, currently unused and unrecorded) as the likely source
- **varying, sub-pixel, or with y drift** — averaging will soften the image. Stop and
  report; a resampling registration step is a much bigger piece of work

Add `filter_offset1/2` to the scan metadata regardless, so future scans carry evidence.

### Step 2 — multi-sampling in `rps7200/direct.py`

`scan_averaged(n, ...)`: run `scan()` n times reusing the open session and the cached
shading reference, register per step 1, accumulate in float64, divide. Return the mean
plus a per-pixel standard deviation map — the std map is the measurement that proves it
worked and costs nothing to produce.

Do not re-run `calibrate_shading()` between passes; the vendor reuses one reference for
a whole session and re-calibrating would cost 3.5 minutes per pass.

### Step 3 — per-channel multi-exposure, only if step 2 shows it is needed

Red has ×2.4 of real headroom and blue has ×1.23, so this is worth at most a red-channel
improvement. Reuse `Settings.scaled()` with a per-channel factor — it already takes a
sequence and already clamps. Merge by replacing non-clipped long-pass values, scaled by
the exposure ratio, keeping short-pass values where the long pass saturates.

Gate it behind a measurement showing red shadow noise actually dominates.

### Other levers worth one experiment each

- **Lamp level.** `Settings.light` — the vendor sends 6, `pieusb`'s default is 4. If the
  lamp can be driven brighter, that raises signal without touching Timer 1, which is the
  only way around the ceiling. Test the response curve of light level vs measured level.
- **Analogue gain.** 8-bit, currently 39/33/21. Raising it amplifies read noise with the
  signal, so it helps only if quantisation downstream dominates. One bracket answers it.

## Validation

**After each significant step, regenerate and send the three files** —
`1_nothing_done.tif`, `2_corrected.tif`, `3_corrected_inverted.tif` via
`tools/make_comparison.py`, plus previews.

1. **Registration numbers from step 1**, before any averaging is built.
2. **Shadow noise, measured.** Pick the densest patch of a real negative and report the
   per-channel standard deviation for n = 1, 2, 4, 8. Expect σ ∝ 1/√n. If it does not
   follow √n, the noise is not read noise and averaging is the wrong tool — say so
   rather than shipping it.
3. **Sharpness unchanged.** Compare an edge profile between n = 1 and n = 8. Any
   softening means registration failed; this is the double-contour failure SilverFast
   users report.
4. **Honest cost statement.** n passes cost n × scan time. With IR forcing a ~212 s
   floor, n = 8 with IR is ~28 minutes per frame. Report it before recommending it.
5. **The three TIFFs**, for visual judgement — which takes precedence over the metrics
   above.
