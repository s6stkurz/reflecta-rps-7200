# Open work

State at 0.1.0. Grouped by what it needs, because most of the remaining work
does **not** need the scanner — the library keeps every scan's raw bytes and
calibration, so decode and correction changes can be re-run offline.

## Waiting on the scanner

Everything here is finished, tested and merged; only a run on the hardware is
outstanding. None of it blocks anything. One session of about twenty minutes
covers the lot, and it has to be one session because a shading reference belongs
to the power-on that measured it.

- `tools/scan.py --dpi 600 --bracket 3` — the bracket path end to end. Film
  loaded. ~4 min calibration plus ~3 min.
- `tools/scan_roll.py --frames 3 --dpi 600` — the frame writer, which now files
  each frame on its own thread rather than with the session open and idle. Strip
  loaded, ~6 min.
- `tools/scan_roll.py --dry-run --frames 6` — the advance path alone, ~2.5 min.
- `tools/scan.py --dpi 600 --reuse` — that a cached reference is restored.

## Known problems

- **Drift in roll scans — the blocker for unattended rolls.** The inter-frame
  gap intrudes 1 -> 10 -> 36 px over three frames on strip3, about 0.2 mm per
  advance, cumulative and monotonic. The cause is hardware: the transport
  counts stepper steps rather than sprocket holes, so it is open loop and does
  not self-correct. The aperture is 36.5 mm against a 36 mm frame, so ~0.5 mm
  of slack is eaten in two or three frames. No fine-positioning command exists
  in anything published — SANE's 26-command set has none and CyberView sends
  none in 3,955 commands — yet the scanner's own Forward/Reverse keys do
  vernier adjustment, so the firmware can do it and nothing reaches it over
  USB. Three untried paths, ranked: (a) the `SLIDE` 0x00/0x01 actions CyberView
  sends every session that SANE never documented, which do not move the frame
  counter and occupy the mechanism 1.5-3 s; (b) backlash exploitation with the
  already-verified `SLIDE_NEXT`/`SLIDE_PREV`, testing whether NEXT-then-PREV
  returns exactly; (c) detect-and-stop, halting when drift exceeds a threshold
  so it can be nudged by hand and resumed with `--start-at`.
  See `docs/whole-roll-plan.md`.

- **`registration()` cannot see picture position mid-strip.** `film_bounds`
  keys on film-versus-empty-aperture, so on a continuous strip it always
  returns the full window and reports 0.00 mm — it reported "no drift" on the
  exact frames that were drifting. A gap-based detector works, keying on
  columns that are both bright and flat since the inter-frame gap is unexposed
  base, and recovers the 1/10/36 px cleanly. Measured, not implemented.
- **The gain register is a digital multiplier** (measured 2026-09-10, closed).
  It was the only lever left for blue in plain RGB, where the exposure timer
  runs out with blue still ~30% below red and green. It is honoured, and not
  linear in the code — 21→39 is ×1.857 in the code and ×1.480 in the output —
  but the noise rises with the signal: ×1.4842 against ×1.4763, a shortfall of
  0.53% where analog gain would give ~4%. Net SNR gain 0.5%. No code change;
  `docs/analog-gain-plan.md` has the ladder and the reasoning so nobody spends
  the scanner time again.

- **The red plane is one scan line out, in every scan measured.** Cross-
  correlating R and B against green over 14 entries -- 300, 600, 900 and 1800
  dpi, colour negative and B&W alike -- red lines up best at row -1 every
  single time, and blue at row -1 or 0. Correcting it lifts R-to-G correlation
  from 0.944 to 0.962 on a B&W frame, where the two channels are the same
  photograph and ought to agree almost perfectly.

  **It does not scale with resolution**, which is the interesting part: a
  physical offset between the rows of a trilinear CCD would be four lines at
  3600 dpi where it is one at 900. A constant one line points at the decode or
  at a residual the device leaves after its own compensation, not at optics.

  `GET PARAMETERS` returns `filter_offset1` and `filter_offset2` -- both 12 in
  `captures/bw.pcapng` -- and this driver reads them, records them in `meta`,
  and never applies them. `library.save` then drops them on the floor, the same
  way it was dropping `metering`, so no filed entry has them either. Start
  there.

- **Black and white is now delivered as one channel** (done). NegPy classifies
  by minimum channel correlation, `> 0.99` meaning monochrome. Measured across
  this library, B&W spans 0.926-0.988 and colour negative 0.008-0.976: they
  overlap, so no threshold separates them and it is not a value that needs
  tuning. Run through NegPy's own `detect_process_mode` in its own venv, a
  three-channel B&W scan came back **Transparency** -- processed as a slide.

  Its loader expands a 2-D image to three identical planes, so a genuine
  greyscale TIFF classifies as B&W with certainty and nothing downstream has to
  change. `rps7200/mono.py` does the reduction; `tools/scan.py --mono` is on by
  default for `--film bw`. The library still files all three channels.

  The window follows the film type: setting it to `bw` ticks "deliver one
  channel", and that reaches the scan, the roll and Save as -- including the
  branch that used to copy the entry's three-channel file over verbatim.

- **Black and white comes out as an RGB file, and nothing converts it.** The
  hardware has no black and white mode worth using -- `passes = 0x04` is the
  green filter alone, returns untagged PIXEL-format data our deinterleave
  cannot read, and still costs a full colour pass (`docs/protocol.md`).
  CyberView does the same thing: `captures/bw.pcapng` is eight passes and every
  one is `0x80` RGB.

  So the conversion is a host-side step. **It is not an average**, which was
  the obvious guess and is wrong: the grain in a silver emulsion is the same
  grain in all three channels, so it does not average out, and a plain mean
  measured 5.6-22.4% *worse* than the best single channel across three
  resolutions.

  **Blue is the cleanest channel**, from repeat pairs at 900 dpi -- random noise
  per unit signal 2.7 against green 3.0 and red 3.7. A single pass says the
  opposite, because red reads low at the dense *and* the thin end, which is
  softness rather than cleanliness. Only the repeat pair separates them.

  Not started. It must be optional and must not overwrite the three-channel
  file -- a merged channel cannot be un-merged.

- **How much brighter blue comes back in RGBI depends on the film.** Two
  matched pairs, each the same frame in both modes minutes apart, with red and
  green confirming the mode was the only variable: **4.98-5.02 on colour
  negative**, **~9.6 on black and white** (8.3-10.5 across percentiles). The
  cause is still unknown, but the shape now points somewhere: on the colour
  negative the ratio is flat across density (5.02 densest decile against 4.95
  brightest) while on B&W it slopes 10.2 -> 8.3, which is what an *additive*
  leak into the blue record looks like rather than a change of gain. On a
  colour negative the mask suppresses blue hard enough that the leak dominates
  everywhere, which would make it look multiplicative.

  A single constant was wrong and cost a scan: 5.2 applied to black and white
  put 34% of its blue channel at the rail. `blue_rgbi_headroom(film)` now
  carries a value per film, with unmeasured films taking the safe end.

  What would settle it: a matched RGB/RGBI pair on a slide and on Kodachrome,
  which are still unmeasured, and a clear-base frame to separate the leak from
  the film. `last_metering` is filed with every metered scan now, so the
  achieved blue level accumulates without a special run.

- **Passes sit at different column offsets and nobody knows why.** Two 3600 dpi
  passes of one frame correlate at r=0.936 only once shifted 16 columns, and a
  shading reference matched a scan best at lag -11/-12 across sessions.
  Counter-evidence at 600 dpi: two passes with nothing touched between them
  register at exactly [0, 0], and a take-out-and-put-back pass at [0, 2] — so
  nothing gross happens there, though 16 columns at 3600 dpi is only ~2.7 at
  600 and under that measurement's noise. The bar any drift claim must clear is
  1.35-1.52% peak-to-peak, the smooth-field difference between two untouched
  passes. `filter_offsets` is now recorded on every scan (fc179a6) and is the
  prime suspect; nobody has looked at the values yet.
- **Some library entries are deliberately kept as records of failure.**
  `20260828T010052Z` has blue saturated on 2.07% of pixels from a metering
  error, and `strip6-01..03` are tagged blue-clipped / not-a-reference. Their
  raw bytes are fine; do not prune them as junk.
- **`tools/scan.py` and `tools/scan_roll.py` both write the three comparison
  TIFFs to the repo root**, so running them together clobbers one another.

- **`calibrate_shading(exposure_scale=...)` is a no-op.** The device self-meters
  the calibration pass: writing 7540-5108-5108 still produced 9604-6506-6506 on
  all 40 blocks. Harmless in practice but the parameter looks effective and is
  not. Delete it or wire it up.

- **`apply_shading` silently leaves trailing columns uncorrected** when the CCD
  mask yields fewer used pixels than the image width — it writes only
  `out[:, :loc.size, c]`. `scan()`'s guard catches the gross 7200 dpi case only.
  Latent, not active: at 600 dpi width and columns both came back 860.

## Untested

- **The 7200 dpi shading guard.** The CCD mask covers 5172 columns and a
  7200 dpi pass is 10344 wide, so the correction refuses and returns raw
  pixels. The code path has never been exercised.

## Specced but not built

- **Lossless TIFF compression** — `docs/tiff-compression-plan.md`. Worth 16% on
  every file, lossless. Needs the built-in reader taught deflate + predictor
  first, or files written with tifffile become unreadable without it. The
  harness for that now exists: `tests/test_tiff.py` runs every write/read
  pairing of the two implementations, so the compression work is adding
  compressed rows to a matrix rather than writing one. No scanner needed. Note
  it will *not* make NegPy faster: it shrinks the file on disk, not the array in
  memory.
- **The dpi trade-off measurement** — `docs/dpi-tradeoff-plan.md`. Evidence so
  far says real detail runs out around 26 c/mm (~1340 dpi to sample) and that
  1800 dpi aliases, pointing at 2400 dpi as the sweet spot. Needs the scanner
  and about 45 minutes.

## Improvements identified but not applied

From reading [nkscan](https://github.com/activexray/nkscan), a from-scratch
driver for Nikon Coolscans:

- ~~**Metering target 0.70 -> 0.85.**~~ **Done, at 0.80**, and settled by
  measurement rather than by copying pieusb. `tools/exposure_headroom.py`
  simulates a higher exposure on stored raw bytes and runs the real shading
  correction over it: clipping permits well past 0.90 (worst case 0.001% of blue
  at 0.80 over six entries and four frames), but the sensor compresses 1.5-1.9%
  above 75% of scale, so linearity binds before clipping does. 0.80 also matches
  `bracket.py`'s `CLIP_START`, so metering no longer aims where another module
  declines to follow. See `EXPOSURE_TARGET` in `rps7200/direct.py`.

  What that change *cost*, and is worth remembering: the acceptance band was
  `abs(level - target) <= 0.08`, which at 0.70 topped out at 0.78 and was
  harmless. Moving the target to 0.80 moved the top of that band to 0.88, past
  the knee, and a B&W frame duly landed at 87% with samples at the rail. The
  band is asymmetric now. Raising a target is not safe unless the band above it
  is looked at too.
- **Otsu plus morphological opening in `film_bounds`.** It currently cuts at a
  fixed fraction of the clear level, the rule nkscan explicitly rejects because
  it "lands in the wrong population" when the proportion of film in the pass
  changes. Self-contained and testable against the prescans already stored.
  (The variance-based `detect_frame` this item used to name has been deleted;
  it was documented as unreliable and nothing called it.)

## Measured and left alone

- **Column defects in the frame interior are corrected as far as they can be.**
  Signed channel-relative deviation on the delivered file, interior columns
  only: 9.32% peak before, 2.43% after, and seven columns of 2464 still above
  2%. None of the seven is a sensor defect -- checked across nine 1800 dpi
  library entries at different film positions, the strongest reads high in four
  of them and a sensor defect would read high in all nine. What is left is film.
  Chasing it further would be correcting the picture.


- **`library.save()` reindexes the whole library on every write.** Genuinely
  quadratic across a roll, and not worth fixing: at 400 entries the rebuild is
  34 ms against a real save's 1.7 s at 1800 dpi and several times that at 3600,
  so it is under 2% of the cost. An append path that can drift out of step with
  the rebuild would buy nothing.

## Decided against

- **IR dust removal** — NegPy does it, and does it well. The point of this
  driver is handing the infrared plane over untouched.

## Do not lose

`library/`, `calibration/` and `previews/` are gitignored and hold data that
cannot be re-derived without the scanner — including the vignette study's nine
600 dpi passes with their raw bytes and shading references. A `git clean -xdf`
or a fresh clone would destroy them. Back `library/` up before anything
aggressive.

## Process

- **Orientation is unresolved.** The scanner delivers lines in transport order,
  which is upside down for viewing. The vertical flip is currently applied only
  to hand-delivered files, never in `scan()`, so the library keeps what the
  scanner sent and still matches its raw bytes. Whether the flip belongs in the
  driver depends on whether it is inherent to the transport or to how the strip
  was inserted — untested.
