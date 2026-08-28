# Apply the scanner's own shading calibration

## Status: DONE — implemented, run on the scanner, verified

Confirmed working on 2026-08-28 and confirmed by eye. What was measured, on
real scans rather than predicted:

**The correction removes the fixed column pattern.** Measured as how well the
top half of a frame predicts the bottom, which separates a reproducible sensor
pattern from picture content:

```
1800 dpi RGB          raw    corrected        worst column defect
  R                 0.897  ->   0.265         13.01% ->  1.44%
  G                 0.782  ->   0.242          8.20% ->  1.95%
  IR (RGBI pass)        -           -          4.67% ->  0.89%
```

**Blue does not improve, and should not.** Its raw top/bottom correlation is
0.153 — there is no fixed pattern to remove. Blue carries the least signal on
this scanner (orange mask, weak lamp, exposure at the timer ceiling), so its
column variation is noise, which alternates sign and no reference can subtract.

**No sensor defects survive.** A colour-opposed detector (signed, per channel,
relative to the other channels) flagged ~120 columns after correction, but the
cross-frame test — the same column in two different film positions — showed
those are picture content: the four columns common to both frames carry
*opposite* tints in each, so they are coincidence, and the worst of them sits
on an edge with 109x the frame's median gradient.

**The library round-trips.** All three 1800 dpi RGBI entries verify, and
re-decoding their stored raw bytes reproduces the stored pixels exactly.

### What the plan got wrong, and what the hardware corrected

- The two-point dark/light split is implemented and parses CyberView's own
  calibration bytes correctly, but on real scans it measures **the same** as the
  single-point form. The dark term is ~170 counts against signals of ~20000 and
  does not transfer across exposures; scaling it made things worse. Kept because
  it is the more principled form and costs nothing, not because it helped.
- Calibrating at the scan's exposure does **not** help: the device meters the
  calibration pass itself and returns the same ~48000 light level whatever is
  written beforehand. `calibrate_shading(exposure_scale=...)` exists to make
  that testable; the answer is no.

### Three bugs the hardware found

- `calibrate_shading` only collected lines when `keep_data` was set, so the
  default path built the reference from an empty buffer and silently produced
  none.
- `scan()` discarded the CCD mask it reads, so the library stored the
  calibration pass's mask against a scan at another resolution.
- `SET GAIN OFFSET` does not persist across a scan sequence; exposure cannot be
  pinned on the device between passes and must go through each scan's
  `exposure_scale`.

### Still open

- **Blue behaves differently in RGBI than RGB** at identical exposure — 2.0x
  brighter on one frame, ~3.7x on another, and not linear in between. Metering
  in RGB and reusing the values is what CyberView does, but it keeps blue far
  lower than this driver's auto-exposure does. Until that is understood, meter
  an IR scan from its own throwaway IR pass rather than from an RGB probe.
- The 7200 dpi guard is implemented but untested: the mask covers 5172 columns
  and a 7200 dpi pass is 10344 wide, so the correction refuses and returns raw.

## Context

Scans from this driver carry vertical colour stripes that CyberView's scans do not.
Months of work treated this as a sensor defect to be patched over (`destripe`,
`find_column_defects`, flat-field division), reaching 14.43% → 3.94% worst column
defect but never removing the lines.

The cause is now identified. **The scanner returns raw pixels and never applies its
own shading correction — that is the host's job.** `calibrate_shading()` already runs
the correct pass and receives a 1.66 MB per-column reference, and the driver drained
it with `keep_data=False`. The observation "calibration changes nothing" was correct;
the conclusion that it was broken was not.

The intended outcome: apply that reference so the stripes are removed at source, and
demote the destripe machinery to a fallback.

## What the recordings establish

All verified against `captures/frist_open.pcapng` (the only capture taken from
power-on) unless noted. Nothing here is inferred from the SANE C source alone.

**The vendor calibrates once per power-on, never again.**

| capture | passes | calibration |
|---|---|---|
| `frist_open` | 6 | `quality=0x0800` CALIBRATE once, then 5 × `0x0008` reuse |
| `300_3600` | 3 | none — all reuse |
| `600_ICE_FILM_STRIP_5` | 17 | none — all reuse |
| `300_900_1800_ICE` | 4 | none — all reuse |
| `scan2` | 1 | none — reuse |

The 17-pass film-strip session reused a reference acquired in an earlier session,
which is what proves the reference is film-independent.

**The calibration pass** (frame 5219): MODE SELECT 3600 dpi, `passes=0x80`,
`quality` bytes 9,10 = `00 08` → `0x0800`; scan frame written at frame 4803 decodes
to `(0, 3431, 10343, 6888)`, confirming `CALIBRATION_FRAME`. Returns 1,655,852 bytes.

**Shading descriptor** (prep `0a…95 00 02 00 00 00` at frame 4663, then READ 128 at
frame 4737, response at frame 4754):

```
n_entries=4  entry_size=6
  entry 0: type=0x00 send_bits=16 receive_bits=16 lines=20 pixels_per_line=10344
  entry 1: type=0x08  (same)
  entry 2: type=0x10  (same)
  entry 3: type=0x20  (same)
```

`pixels_per_line=10344` is **bytes**, not columns: 10344 = 5172 × 2 at 16 bits, so a
line is 2 + 10344 = **10346 bytes = 5172 columns**. Confirmed directly in the stream —
tags sit at 0, 10346, 20692, 31038 with exactly 10346 spacing.

**Line format:** doubled ASCII channel tag then 5172 × uint16 LE, channels
interleaved `B R G I`. IR is included in the reference.

**It is a two-point calibration.** The declared 4 × 20 = 80 lines account for half the
stream; ~160 lines arrive, in two phases:

```
frames 5886, 6074, 6328   means  B 195   R 201   G 161      dark reference
frames 6702, 6890, 7144   means  B 47146 R 46486 G 47827     light reference
```

Not a subtlety: the dark reference has **12–15%** column-to-column variation against
the light reference's 0.8%. `pieusb`'s `calculate_shading` averages every line sharing
a tag, which blends the two — do not copy that.

**Light path was empty during calibration.** Line-to-line spread 0.13–0.31%, and no
sample reaches full scale (max 82%, mean 72%). Film cannot produce that uniformity
across `y=3431..6888`. Matches the manual: power on with nothing loaded, wait for
solid green, then the software connects.

**The CCD mask carries the column mapping, and it is per-pass.** Read via
`SCSI_COPY` on all 6 passes, 5172 bytes, `0x00` = used, `0x70` = unused:

```
calibration 3600 dpi:  5172 used, first at 0    (every pixel)
600 dpi:                860 used, first at 5    (every 6th)
300 dpi:                428 used, first at 11   (every 12th)
```

The first-used index differs per resolution. This is why alignment cannot be derived
from the frame.

## Implementation

### 1. `rps7200/shading.py` — rewrite the parse and correction

Replace `calculate_shading` / `apply_shading` (currently a single-reference port of
pieusb, written before the two-phase structure was known):

- `parse_calibration(data, columns=5172) -> CalibrationReference` — walk the stream at
  stride `2 + columns*2`, key the channel off byte 0 (`TAG_TO_CHANNEL` already
  correct), and **split dark from light by level, not by declared counts**: cluster
  each channel's line means and split at the gap. The two phases are ~250× apart, so
  this is unambiguous and survives the descriptor's declared-vs-actual line mismatch.
  Return per-channel `dark[c]`, `light[c]` and their means.
- Keep `build_width_to_loc(ccd_mask, width)` as it is — verified correct.
- `apply_shading(image, reference, ccd_mask)` — two-point, per channel:

  ```
  gain  = (mean_light[c] - mean_dark[c]) / (light[c][loc] - dark[c][loc])
  value = (raw - dark[c][loc]) * gain
  ```

  Guard non-positive denominators (gain 1.0), clamp to dtype max, keep the existing
  clipped-sample count in the report. Fall back to the single-point form when a
  channel has no dark lines.
- `ShadingReference.save/load` extends to both planes; keep the `.npz` cache.

### 2. `rps7200/direct.py` — keep what is already received

`calibrate_shading()` and `scan()` already retain `data` and `ccd_mask` (commit
`5ded2d6`); only the parse call changes. Two fixes:

- `calibrate_shading` currently hardcodes `width` from `CALIBRATION_FRAME`. Read it
  from `get_shading_parms()` instead — the descriptor gives `pixels_per_line` in bytes,
  so columns = `pixels_per_line // 2`. Keep the frame-derived value as fallback.
- `scan()` must refuse to correct when `width > len(used pixels in mask)` and say so.
  At 7200 dpi the image is 10344 columns but the mask holds 5172 entries, so the
  mapping cannot cover it. Log and return raw rather than corrupt half the frame.

No change to the command sequence. This has been verified twice; the entire diff of
commit `5ded2d6` against the command path was two lines assigning return values.

### 3. `tools/scan.py` — already written, needs one addition

Print the light-reference mean immediately after calibrating. ~45,000–48,000 means the
path was clear; substantially lower means film was in the way and the calibration
should be repeated. This is the check that makes "film in or out" self-verifying.

### 4. Demote the destripe path

Leave `destripe` / `find_column_defects` / `column_defect_sigma` in place but off by
default once shading correction is confirmed working. They exist only because the real
correction was missing.

## Open question — do not paper over it

Two of my own passes at **identical** frame `(0,0,10343,6887)` and identical 3600 dpi
correlate at r = −0.02 at lag 0 but **r = +0.936 at lag −16**. Neither the mask (all
5172 used at 3600 dpi) nor the frame explains a 16-column shift, and the reader cannot
cause it — `_deinterleave` validates a channel tag on every line and would raise.

So the readout start appears to vary between passes. This must be **measured, not
corrected away**. Step 5 below tests it explicitly. If a per-pass shift is real, the
candidates are `ScanParameters.filter_offset1/2` (read at `get_parameters` and
currently unused) or a genuine hardware start offset; only then decide whether to
estimate the lag by cross-correlation.

## Work that needs no scanner

Both of these can be done before hardware access, and both de-risk the steps above.

**Validate the parser against the vendor's own reference.** `frist_open.pcapng`
contains a real calibration block, so the parse can be checked against genuine data
rather than the synthetic fixture currently used. USBPcap truncates at a 64 KB
snaplen, so only ~18% survives — but the intact transfers are enough, and they are the
ones already identified:

```
dark  : frames 5886, 6074, 6328     light : frames 6702, 6890, 7144
extract: tshark -r captures/frist_open.pcapng -Y "frame.number==<F>" \
           -T fields -e usb.capdata | tr -d ':'
```

Feed those bytes straight to `parse_calibration` and assert: stride 10346, tags
`B R G I` at 0 / 10346 / 20692 / 31038, dark means ≈ 160–200, light means ≈ 46,500–47,800,
no sample at full scale, and the level-based dark/light split lands in the right place.
This is a real regression test — it pins the byte layout to vendor data, so a future
change that reintroduces the `pixels_per_line` bytes-vs-columns confusion fails loudly.
Keep the recovered reference as a fixture rather than leaving it in the scratchpad.

**Characterise the 16-column lag from scans already in the repo.** No new capture
needed: cross-correlate the column profiles of every existing same-resolution pair
(`scans/flat/flat_clearfilm_3600dpi.tif`, `scans/negatives/negative_3600dpi.tif`,
`series_*.tif`) at lags ±32 and tabulate the best lag per pair. What this answers
before touching the scanner:

- if the lag is always 0 except for one pair, it is an artefact of that scan, not a
  hardware behaviour, and the open question above dissolves
- if lags vary per pair, look for what correlates with them — resolution, channel
  count, exposure, or capture order
- check whether the sidecar JSONs differ in anything that could shift the readout;
  `filter_offset1/2` are read by `get_parameters` but not currently recorded, so add
  them to the metadata so future scans carry the evidence

## Validation

Hardware precondition: power-cycle with **no film loaded**, wait for solid green
(documented warm-up), calibrate, then load film.

**After every significant step, regenerate the three files in the repo root and send
them** — `1_nothing_done.tif`, `2_corrected.tif`, `3_corrected_inverted.tif` via
`tools/make_comparison.py`, plus the previews. This is the primary acceptance test;
Stefan judges by eye and has repeatedly caught defects the metrics missed.

1. **Reference sanity, before any scan.** Light mean ≈ 47,000 (72% of full scale), no
   saturated samples, line-to-line spread < 0.5%, dark mean ≈ 170. Compare the parsed
   reference against the vendor's, recovered from the capture at
   `scratchpad/vendor_ref.npz` — the column structure should correlate strongly.
2. **Raw vs corrected, same session, same frame.** Scan once with `--no-shading` and
   once with correction. Report worst column defect across **all channels** (the
   green-only metric hid the true 14.43%). Target: stripes gone, not reduced.
3. **The three TIFFs**, sent for visual check.
4. **Resolution independence.** Correct scans at 300, 600 and 1800 dpi from the one
   reference. If the mask mapping is right, all three come out clean; a mapping error
   shows as stripes that worsen with lower dpi.
5. **The lag question.** Scan the same frame twice without moving anything, and
   cross-correlate the column profiles at lags ±32. Lag 0 means the shift was an
   artefact of comparing older scans; a repeatable non-zero lag means the readout start
   varies and needs handling before the correction can be exact.
6. **IR.** Confirm the reference's `I` channel corrects an RGBI scan, since the
   calibration carries IR and the previous flat-based path never touched it.
