# 7200 dpi: the zigzag, and never shipping an uncorrected scan

## Status

**The column stagger: DONE, offline-verified.** Two independent library
entries, every channel, confirm a 4-line stagger and the fix removes it.

**Calibrating at 7200 dpi: ANSWERED ON THE HARDWARE 2026-09-13, and the
answer is no.** The device will not produce a shading reference wider than
5172 columns at any resolution, so a 10344-column pass cannot be corrected
from it. 7200 dpi with `shading=True` is now refused outright, before the
pass runs. See "What the hardware said" below.

**It cost a power cycle**, through a bug that had nothing to do with 7200 dpi
and everything to do with error handling. Also below, because it is the more
transferable lesson.

## The zigzag

Reported by eye: a 7200 dpi scan looked like the columns were shifted against
each other -- one at the "true" position, the next shifted up, then back, a
comb pattern that a 3600 dpi scan of the same kind of frame does not show.

Two 7200 dpi library entries have their raw bytes on hand
(`20260911T103600Z_unknown-film_7200dpi`, `20260911T104244Z_unknown-film_7200dpi`
-- two different frames, so a sensor effect and picture content can be told
apart). Decoded and cross-correlated: split each channel plane into its even
and odd columns, subtract each column's own mean first (so the ordinary
shading pattern -- a per-column brightness offset -- does not dominate the
comparison), then correlate even against odd at a range of row lags.

```
                        lag -6   -5   -4   -3   -2   -1    0   +1   +2 ...
crop (3243, 4972)                        0.995                0.963
crop (1521, 2386)                        0.997                0.954
crop (4965, 7558)                        0.951                0.670
```

Every crop, both entries, all of R/G/B: **the correlation peaks cleanly at lag
4**, well above lag 0, and falls off symmetrically on both sides of it. That
is the signature of two populations of the same content offset by a fixed
number of rows -- not picture content (ruled out by using two different
frames) and not chance (peaks in the same place regardless of channel, entry,
or where in the frame the crop is taken).

**Reading:** a native 7200 dpi read comes from two rows of CCD elements,
physically offset from each other along the scan direction to pack more
columns into the sensor than one row's element pitch allows -- odd columns are
reading the frame 4 scan lines later than even ones. This is why the stagger
is invisible at 3600 dpi and below: at those resolutions the CCD mask
(`docs/shading-calibration-plan.md`) already selects a subset of columns, and
that subset comes from one of the two rows only -- the *other* row is simply
never read below 7200 dpi. There is nothing to misalign until both rows are.

**Fix:** `DirectScanner._realign_native_column_stagger` (`rps7200/direct.py`)
shifts the odd columns back by 4 rows relative to the even ones, trimming the
last 4 rows of the frame (there is no data to shift the other parity into).
Verified against both stored entries: after realigning, the same
cross-correlation peaks at lag 0, at the same strength the lag-4 peak had
before (0.995 vs 0.996 on one crop, 0.997 vs 0.995 on another) -- the
realignment moves the peak, it does not create or destroy it. `scan()` runs
this whenever `resolution == 7200`, after the pass is read and before shading
is applied (row alignment and per-column shading are independent, so order
does not matter to the result).

*Since 2026-09-24 (8b01894, audit P06):* the realignment is recorded. A pass
records the rows it trimmed as `stagger_realigned` -- 4 here, 0 when none ran --
the library keeps it as `scan.stagger_realigned`, and `library.decode_raw` and
`reconstruct` replay it, so a 7200 dpi entry reconstructs as identical instead
of reading "decode CHANGED" for ever. Entries filed before the field existed
are recognised by their resolution and a shortfall of exactly those rows. The
two entries of 2026-09-11 predate the fix itself and hold the zigzag in their
stored pixels.

Tests: `tests/test_decode.py` -- a synthetic frame with a known stagger,
checked that realigning recovers it; zero lines is a no-op; a frame shorter
than the stagger is refused rather than producing something silently wrong.

## Never shipping an uncorrected scan

The separate, explicit request this shares a branch with: a 7200 dpi scan
should get a shading correction automatically, and no scan -- 7200 dpi or
otherwise -- should ever come back raw when correction was asked for.

**Why 7200 dpi never had one.** `calibrate_shading` hardcoded `resolution=3600`
because that is the only resolution any capture, vendor or ours, has ever
calibrated at -- CyberView calibrates once at power-on, always at 3600 dpi,
and every scan afterwards reuses that reference regardless of what resolution
it runs at. The reference it produces is 5172 columns; a 7200 dpi pass is
10344. `scan()` used to see the mismatch, log it, and hand back raw pixels --
correcting half the frame from a reference that does not cover it would have
been worse.

**What changed:**

- `calibrate_shading(resolution=...)` -- the MODE SELECT resolution, and the
  width formula, are no longer hardcoded to 3600. Default unchanged, so every
  existing call keeps doing exactly what it did.
- Fixed a real off-by-one while generalising the width formula: it used
  `(x1 - x0)` where the frame's span is inclusive, `x1 - x0 + 1`. At 3600 dpi
  this was invisible -- `10343 * 3600 / 7200 = 5171.5` rounds to 5172 either
  way -- but at 7200 dpi there is no `.5` to hide behind: `10343` against the
  `10344` every 7200 dpi pass has actually reported. Extracted as
  `DirectScanner._shading_columns_needed(frame, resolution)`, tested directly
  against both known cases.
- The CCD mask read in both `calibrate_shading` and `scan()` used to request a
  fixed `CCD_MASK_SIZE` (5172) regardless of what the active calibration
  actually covers -- harmless only because every calibration before this one
  ran at 3600 dpi and 5172 was always the right number by construction. Both
  now size the read from the calibration in hand.
- `scan()` now checks, *before* taking a pass, whether the reference it holds
  covers the width that pass will need (`_shading_columns_needed`). If not --
  no reference at all, or one too narrow -- it calibrates at the pass's own
  resolution first. This is what makes a 7200 dpi request self-sufficient: the
  first one in a session pays for a wider calibration, every later one in the
  same session reuses it, the same shape as the vendor's own once-per-power-on
  pattern.

  *Superseded twice.* After the hardware answered (below), `scan()` calibrated
  at the default 3600 dpi rather than the pass's own resolution. And since
  2026-09-24 (1492d2c, audit P15) it never calibrates at all: calibrating inside
  a pass is the path measured twice as stalling the device. A pass wider than
  any reference the device will produce -- 7200 dpi -- is refused with
  `DirectScanner.uncorrectable`, and a corrected pass no reference covers with
  `DirectScanner.uncalibrated`, both before anything is sent, metering
  included. A calibration is `ensure_shading`'s, at 3600 dpi.
- If shading was asked for and still cannot be honoured after calibrating --
  the device refuses, or the new resolution's calibration itself comes back
  unusable, both unverified for anything but 3600 dpi -- `scan()` raises
  `ShadingUnavailable` (`rps7200/protocol.py`) instead of returning raw pixels
  with a note buried in the metadata. `ScanSession._scan` (`rps7200/session.py`)
  no longer has a soft-warning path for this: the exception reaches the job
  dispatcher's existing generic handler and is surfaced as a failure, which is
  more visible than the log line it replaces, not less.
- `prescan()` used to run with `shading=False` unconditionally, because the
  reference is always 16-bit sensor counts and an 8-bit prescan applying it
  unscaled subtracted a ~170-count dark floor from samples that top out at
  255 -- driving every pixel to zero (`docs/whole-roll-plan.md`, "Shading on
  an 8-bit prescan"). `apply_shading` (`rps7200/shading.py`) now scales the
  whole reference down to the pass's own dtype maxval before using it -- the
  gain is a ratio of same-scaled reference quantities and is unaffected by a
  uniform rescale, only the *subtracted* dark floor needed to change units.
  Verified against a real 8-bit prescan library entry with its own reference
  attached (`20260911T101511Z_unknown-film_300dpi`): unscaled, 100% of output
  pixels were zero; scaled, 2.8% are (plausible near-black content, not a
  correction failure), mean brightness within 1% of the raw pass's own mean,
  no clipping. `prescan()` now asks for shading like every other pass.
  Test: `tests/test_shading.py::test_an_eight_bit_pass_is_scaled_to_the_references_own_depth`.

**What this does not change:** the vendor's own 3600 dpi calibration
behaviour, or anything for a session that never asks for 7200 dpi -- the
default `resolution=3600` reproduces the exact width (5172) it always has.

`PROTOCOL_REVISION` moved 1 -> 2 for this: `scan()` can now send a full
calibration sequence it did not send before, mid-session.

## What the hardware said

`calibrate_shading(resolution=7200)`, run on the device 2026-09-13 with film
loaded. The relevant three lines of the log:

```
shading parms: [{'type': 0, ..., 'pixels_per_line': 10344}, ... x4]
note: descriptor says 5172 columns, the frame implies 10344; using the descriptor
shading descriptor: 4 entries, 80 lines declared, 5172 columns
```

`pixels_per_line` in the descriptor is a **byte** count -- 10344 bytes = 5172
columns -- and this is the *identical* descriptor the device declares at
3600 dpi. **The calibration does not widen with the MODE SELECT resolution
field.** It went on to read `10346 bytes/line (width 5172)`: asked for 7200
dpi, it calibrated 5172 columns anyway.

So the premise this feature was built on is disproved. `MAX_SHADING_COLUMNS`
in `rps7200/direct.py` now records the ceiling, and `scan()` refuses a pass
wider than it *before* running, rather than spending two minutes calibrating
and 5.5 minutes scanning to arrive at the same refusal.

### Could a 5172-column reference correct a 10344-column pass anyway?

The obvious idea: map output column *j* to calibration column *j // 2*, so
each calibration column serves the two output columns it covers. Measured
offline against both stored 7200 dpi entries -- no scanner needed, which is
the point of keeping raw bytes:

| channel | even/odd alternation | parity gap |
|---|---|---|
| R | 0.49% -> 0.48% | 0.32% -> **0.48%** |
| G | 1.55% -> 1.55% | 1.66% -> **1.69%** |
| B | 5.16% -> 5.19% | 4.92% -> **5.14%** |

**No, and it cannot.** The dominant 7200 dpi artefact is a difference between
the two column parities, and a mapping that hands both parities the same gain
and the same dark offset cannot express a difference between them -- it is
ruled out by construction, and the measurement agrees, moving nothing and
slightly worsening the parity gap. Blue is the worst affected at ~5%.

That the alternation is the **sensor** and not the picture is settled by the
cross-frame test, which is the only thing that settles it
(`.claude/skills/measure-scan-quality/SKILL.md`): the even/odd component of
two *different* frames correlates at **r = +0.9958**.

### What would actually correct 7200 dpi -- not built, needs a decision

The device caps its *calibrate-mode* reference at 5172 columns, but an
ordinary **scan** at 7200 dpi over `CALIBRATION_FRAME` -- the lower transport,
where the light path is clear and the film does not reach -- would return
10344 columns of clear-path response. That is the same measurement the
calibration makes, taken through a command the device is willing to run at
full width.

It is not the flat-through-film idea `rps7200/shading.py` warns against: that
one fails because it is measured through film and at the wrong exposure, and
this would be neither. The open questions are whether the clear-path level at
the scan's own exposure is usable, and whether the stagger realignment
interacts with it. Both are answerable in one ~6-minute pass, but this is a
design change and wants Stefan's agreement first, not a quiet implementation.

## The wedge, and the bug that actually caused it

The 7200 dpi run above crashed partway through the calibration read loop and
left the scanner needing a power cycle. The cause is worth separating from
7200 dpi entirely, because it is not about resolution:

```
File "rps7200/direct.py", line 1200, in get_gain_offset
    offset=[d[66], d[67], d[68], d[100]],
IndexError: index out of range
```

`_query` returned a response shorter than the `read_size` it asked for and
handed it back unchecked; every caller decodes by fixed offset, so the short
buffer raised `IndexError` far from the cause. `calibrate_shading`'s read loop
catches `CheckCondition` and `ScanReadError` and would have shrugged this off
-- but an `IndexError` is neither, so it escaped the loop, **abandoned the
calibration mid-read**, and cost a power cycle. That is precisely the hazard
CLAUDE.md names; it arrived through an unhandled exception rather than a
timeout.

`_query` now refuses a short response with `ScanReadError`, which the loops
that matter already tolerate. Tested in
`tests/test_scanner_api.py::test_a_short_gain_offset_response_is_a_scan_error_not_an_indexerror`.

**The transferable lesson:** on this device, an exception type that a retry
loop does not expect is not merely a crash -- it is an abandoned read, and an
abandoned read is a power cycle. Decoding by fixed offset without a length
check is how you manufacture one.

## Verification

1. `make all` clean; `uv run pytest tests/test_decode.py tests/test_shading.py -v`
   for the two new units in isolation.
2. `tools/library.py reconstruct` -- clean against every stored entry except
   one pre-existing, unrelated mismatch on `main` before this branch
   (`20260911T091347Z_unknown-film_600dpi-3`, a stale `corrections_applied`
   bookkeeping issue, confirmed present on `main` by stashing this branch's
   changes and re-running).
3. The stagger fix needed no hardware: both entries' raw bytes already
   contained the evidence.
4. **The auto-calibration path was run on the scanner 2026-09-13** and
   answered negatively; see "What the hardware said". The refusal it now
   produces is the designed behaviour, reached without spending a pass.
5. The `j // 2` pairing was measured offline against both stored 7200 dpi
   entries and rejected -- `scratchpad/can_5172_correct_10344.py`, rebuilt
   from raw bytes, so it re-runs with no scanner.
