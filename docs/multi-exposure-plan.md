# Multi-exposure: N-bracket capture and merge, in this driver

## Status: measured on real film, and the hardware will not support it

The merge is built and tested, the capture works, and the exposure ladder is
accurate to 1.5%. Multi-exposure still does not work on this scanner, for two
measured reasons that compound.

### 1. There is very little to win

Two scans at one exposure differ only by what is random per pass; grain, detail
and fixed pattern all cancel. In the darkest tenth of a slide:

```
                    random   total high-freq   random share   ceiling on 9 passes
300 dpi, green    123.2 DN          584.3 DN            21%                 -1.8%
1800 dpi, green   154.9 DN          577.2 DN            27%                 -3.5%
```

Three quarters of what looks like shadow noise is film grain, identical in every
pass. **No multi-pass method can beat about -3.5% here**, bracketing or
averaging. Predicted -1.8% from averaging five at 300 dpi against -1.6%
achieved, which is how we know the model is right rather than the merge broken.

Higher resolution does not change this. Shadow noise measures 12.63% at 1800 dpi
and 5.76% at 3600 -- more resolution resolves grain rather than adding noise.

### 2. Passes stop agreeing as the bracket widens

Scaled onto each other, two passes should differ only by noise. Two repeats at
one exposure set the baseline at |z| = 1.03. Bracket passes do not hold it:

```
                    fitted ratio   median |z|
1800 dpi  pass 0-2        x1.40         0.99   agrees like a repeat
          pass 0-4        x1.97         1.19
          pass 0-8        x3.80         1.72
3600 dpi  pass 0-1        x1.97         1.00
          pass 0-2        x3.83         1.41
```

Confirmed independently at both resolutions. **No transfer function removes it**
-- linear, quadratic and cubic fits leave 1.73 / 1.51 / 1.48 at x3.8, against
the 1.03 baseline -- so the passes differ per pixel in a way that grows with the
ratio and cannot be modelled away.

### The trap

- At **x1.4** the passes agree well enough to merge cleanly, but that bracket
  spans half a stop and the ceiling is still -3.5%.
- At **x3.8** the bracket is finally wide enough to be worth taking, and there
  the disagreement exceeds the gain, so the merge injects more error than it
  removes.

There is no span where both hold. Measured outcome: the merge lands **+3.2%**
at 1800 dpi and **+9.8%** at 3600 where the ceiling was -3.5%. That is not a
blending bug; it is the blend faithfully carrying a disagreement the hardware
put there.

### Recommendation

**Do not ship it.** What would change the answer is a frame whose shadows
approach the noise floor -- a dense, underexposed slide -- where the random
share would be far higher than 25%. Neither frame measured here is close: the
darkest tenth sits at 4833 DN against a 131 DN floor, with zero saturation at
the metered exposure, so one pass already captures the film end to end.

The code stays. `rps7200/bracket.py` and `scan_bracket()` are tested and cost
nothing unused, the brackets are in the library with their raw bytes, and the
whole measurement re-runs offline on any future frame in minutes.

## Status of the build

**Phase 1 offline: done.** `rps7200/bracket.py` merges a bracket by
inverse-variance weighting; `scan_bracket()` and `bracket_ladder()` capture one.
`tools/scan.py --bracket N --stops X`. 129 tests.

Measured on a synthetic bracket, RMS error against the noise-free scene:

```
config                      RMS all   RMS shadows
single pass                    61.4          43.7
bracket N=2                    37.8          11.6   -73%
bracket N=5                    33.9           8.6   -80%
bracket N=9                    32.9           6.6   -85%
5 averaged at one exposure     24.7          10.2   -77%
```

Bracketing beats averaging in the shadows, which is the point. Averaging still
wins across the whole frame. Both belong in the decision.

**Verification 5 (the ladder is real): PASSED**, 300 dpi RGB, 5 passes over
2 stops, on a slide:

```
pass   wanted    exposure R/G/B    median G  achieved   error   p99.9    sat
   0    1.000  16384/11099/11099      8856     1.000   +0.0%   46.6%   0.00%
   1    1.414  23170/15696/15696     12577     1.420   +0.4%   64.8%   0.00%
   2    2.000  32768/22198/22198     17972     2.029   +1.5%   90.8%   0.00%
   3    2.828  46340/31392/31392     25370     2.865   +1.3%  100.0%   7.07%
   4    4.000  65535/44395/44395     35654     4.026   +0.7%  100.0%  10.78%
```

Every pass within 1.5% of its requested ratio, monotonic, no timer wrap, all
five exposures distinct, red exactly on the 65535 ceiling at the top. The two
brightest passes clip by design; the merge is what recovers them.

Metering returned `[2.627, 2.627, 2.627]` — equal across channels, because
`film=positive` locks the white balance and leaves the slide's cast
(R 68%, G 32%, B 18%) rather than equalising it.

**Next: verification 6**, the deciding measurement — shadow noise on a real
frame, single pass against N=2/5/9 and against N averaged passes. Then 1800 dpi.
Phase 2 (infrared) does not start until 6 passes.

## Context

Two earlier versions of this plan were wrong: the first argued against
multi-exposure on a mis-derived exposure ceiling, the second proposed handing
the merge to NegPy. Both are superseded. The merge belongs here, where the
linear sensor data and the per-pass calibration live.

The reference implementation is **pyopticfilm's `feat/me-n-brackets`**
([PR #47](https://github.com/jboneng/pyopticfilm/pull/47)), not its `main`.
`main` has only the fixed 2-bracket version; the branch generalises it to 2–9.
NegPy [PR #1041](https://github.com/marcinz606/NegPy/pull/1041) is the consumer,
still a draft pending that release.

**It is measured to work.** TobbyTravel reports 5 exposures against 2 on an
OpticFilm 8100 V2: **−26% relative chroma noise, −28% relative luma noise.**
That is the evidence that N > 2 is worth building, and it is the number this
plan has to reproduce in spirit before shipping.

Goal: capture **2–9 exposures** of one frame, merge them here, without an
infrared pass per exposure.

## What the ecosystem does

| project | multi-exposure | how |
|---|---|---|
| **pyopticfilm `feat/me-n-brackets`** | **yes, 2–9** | geometric spacing between a short floor and an adaptive, safety-clamped ceiling; N-way IVW merge |
| pyopticfilm `main` | yes, 2 | short + adaptively chosen long; pairwise IVW |
| **NegPy** | yes, N | host-side HDR bracket merge; solves ratios from pixels |
| **nkscan** | **no** | multi-*sampling* only — up to 16 reads per line (`MAX_SAMPLES`) |
| SANE `pieusb` | no | per-channel exposure only |
| SilverFast / VueScan | yes | proprietary; SilverFast originated the feature |

pyopticfilm is **GPL-3.0-or-later, the same licence as this project**, so
adapting its approach is legally clean. Credit it in the source.

## What to take from it, precisely

- **Geometric spacing** between a short floor and an adaptively chosen,
  safety-clamped top exposure. Not linear, not "from the ceiling down".
- **`merge_n_exposures`** — an N-way generalisation of pairwise inverse-variance
  weighting: `w_i = c_i / v_i`, `merged = Σ(w_i·x_i) / Σ w_i`, every pass scaled
  into the reference's units.
- **Variance from a Poisson–Gaussian model**, `var ≈ α·mean + β`. Do not
  hardcode their constants (β ≈ 4096 DN², measured on a different sensor) — fit
  α and β from the flats already in our library.
- **Soft confidence**, rolling off from 0.80 of full scale to 0.95 rather than
  cutting at the rail, so the CCD knee never bleeds into the weighting.
- **Reference is `frames[0]`, the shortest exposure.**
- **The disagreement gate, which is the part that matters most here.** A
  per-bracket residual z-score against `frames[0]` gives a confidence, and the
  pixel's confidence is the **worst (min) across all brackets**; where it is low,
  blend toward the single highest-individually-weighted bracket rather than the
  IVW blend. This is what stops per-channel IVW turning a misaligned edge into
  coloured fringes — the exact artefact this project has already chased once.

## The two things that differ on our hardware

**1. Our exposure ceiling is a hard 16-bit rail.** Their per-model ceilings are
42,000 and 85,000 — the latter above 65535, so their exposure register is wider
than ours. Ours wraps: past 65535 a pass comes back *darker*, not brighter. The
device base is a fixed reference (9604 / 6506 / 6506), so:

| | base | ceiling | span |
|---|---|---|---|
| R | 9604 | 65535 | ×6.82 (2.77 stops) |
| G | 6506 | 65535 | ×10.07 |
| B | 6506 | 65535 | ×10.07 |

**Red binds the bracket at 2.77 stops.** Nine passes across it sit 0.35 stops
apart. The ladder must clamp explicitly rather than relying on
`Settings.scaled()`'s clamp, because a clamped pass silently duplicates its
neighbour and adds nothing but time.

**2. Infrared must not be bracketed.** pyopticfilm captures IR once, at the
short exposure, and the N-bracket PR leaves that alone. Here it matters far
more: an infrared pass costs its own ~212 s floor regardless of resolution, so
bracketing it would dominate everything. Our infrared exposure is a device
constant (7745 across all 36 captured gain/offset responses; the vendor never
meters it), so it is unaffected by the visible ladder anyway.

Our modes are RGB (`0x80`) or RGBI (`0x90`) with no infrared-only mode, so
**one pass of the bracket is taken as RGBI and the rest as RGB** — that pass
yields the infrared plane *and* serves as a bracket member.

```
at 1800 dpi        RGB only     one pass RGBI
  N = 2             ~2.5 min       ~5 min
  N = 5             ~6 min         ~8.5 min
  N = 9            ~10.5 min      ~13 min
```

## Build it in two phases, RGB first

### Phase 1 — RGB only, proven before anything infrared

- **`rps7200/bracket.py`** — the merge, adapted from pyopticfilm's
  `exposure_merge.py` and credited. Soft confidence, Poisson–Gaussian variance,
  N-way IVW, the min-across-brackets disagreement gate, row-banded so a 3600 dpi
  merge does not need several full-frame float planes.
- **`fit_noise_params(flats)`** — fit `var ≈ α·mean + β` from library flats
  rather than importing constants measured on someone else's sensor.
- **`DirectScanner.scan_bracket(passes=3, ...)`**, `passes` 2–9, refusing
  anything else with the reason. Meter once; take the first pass at the short
  floor; choose the top from that pass's content (the densest percentile lifted
  to a target DN, as pyopticfilm does); clamp to the red-limited ×6.82; space the
  rest geometrically. **One shading calibration for the whole bracket.**
- **`tools/scan.py --bracket N`**.
- Every pass files in the library with its own raw bytes, mask and exposure, so
  a bracket can be re-merged offline later without rescanning.

### Phase 2 — infrared, only after Phase 1 passes

One pass taken as RGBI; align the infrared plane to the reference; emit
`(H, W, 4)` as now, merged RGB plus the single infrared plane.

## Verification

Each stage has a number that stops it. **Phase 2 does not begin until 1–6 pass.**

**Offline, no scanner:**

1. **N = 2 reduces exactly to the pairwise form.** pyopticfilm verified this
   algebraically and by test, and it is the cheapest possible check that the
   N-way generalisation is right. Assert bit-identical output.
2. **Synthetic bracket beats every individual pass.** Known scene, simulated
   exposures with clipping and Poisson–Gaussian noise; the merge must beat the
   best single pass on RMS error against the noise-free truth, and beat it by
   more as N rises — reproducing the direction of the −28% at N=5.
3. **Degenerate cases**, each a test: all passes identical (merge equals input,
   noise not amplified); one pass fully clipped (ignored, not averaged in); one
   pass all zeros; N=2 at the limits.
4. **Deliberate misregistration.** Shift one pass by 1, 4 and 16 columns and
   confirm the gate suppresses IVW there rather than producing colour fringes.
   16 columns is the real pass-to-pass offset recorded in `TODO.md`.

**On the scanner, RGB only:**

5. **The ladder is real.** With **no film loaded**, where the level is uniform,
   check each pass's median tracks its requested ratio. A timer wrap shows up at
   once as a pass coming back darker than the one below it. Report requested
   versus achieved for all N, and assert no two passes landed on the same
   exposure.
6. **The deciding measurement.** Densest 10% of a real negative, per-channel
   standard deviation: single metered pass, N=2, N=5, N=9. Expect the direction
   TobbyTravel measured — meaningfully better at 5 than at 2. **Also report
   against N averaged passes at one exposure**, so the honest comparison against
   plain multi-sampling is on the record. If the merge does not beat both, the
   feature does not ship, and that is a legitimate outcome.

**Then Phase 2:**

7. **Infrared is unaffected.** The merged RGB is unchanged whether or not one
   pass carried infrared, and the infrared plane still correlates weakly with the
   visible channels.
8. **Time matches the table above**, measured.

**Throughout:** `tools/library.py verify` and `reconstruct` clean; the three
comparison TIFFs plus a 100% crop of a dense region, single pass beside merge —
a downscaled preview cannot show shadow noise, and Stefan's read decides.
