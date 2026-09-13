# 7200 dpi: the zigzag, and never shipping an uncorrected scan

## Status

**The column stagger: DONE, offline-verified.** Two independent library
entries, every channel, confirm a 4-line stagger and the fix removes it.

**Auto-calibrating at 7200 dpi: implemented, UNTESTED ON HARDWARE.** No
capture -- vendor or ours -- has ever sent a calibrate-mode MODE SELECT at
7200 dpi. Ask Stefan before the first real 7200 dpi scan under this code.

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

**What is not verified:** whether the scanner actually accepts and correctly
answers a calibrate-mode MODE SELECT at 7200 dpi -- whether it returns a
10344-column reference, whether the CCD mask it reports at that width is
meaningful, whether the 4-line stagger shows up in the calibration pass itself
(it should, being the same native readout, but that is a prediction, not a
measurement). `PROTOCOL_REVISION` moved 1 -> 2 for this: `scan()` can now send
a full calibration sequence it did not send before, mid-session, that no
capture has ever exercised at this resolution.

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
4. **The auto-calibration path needs Stefan at the scanner.** First real run
   should watch: does the calibrate-mode MODE SELECT at 7200 dpi complete at
   all, does the resulting reference actually cover 10344 columns, and does
   applying it remove the vertical striping the way the 3600 dpi reference
   does at its own resolutions. If it does not, `scan()` raises rather than
   shipping something wrong -- that failure is itself the answer to whether
   this is safe to rely on.
