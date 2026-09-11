# Open work

State at 0.1.0. Grouped by what it needs, because most of the remaining work
does **not** need the scanner — the library keeps every scan's raw bytes and
calibration, so decode and correction changes can be re-run offline.

## Confirmed on the hardware (2026-09-11)

All four ran in one power-on, ~15 min total, no failures, scanner healthy
throughout (idle, unit ready, at position 7 after -- exactly what 3 roll
advances plus 6 dry-run advances from position 0 predicts). `media_loaded`
read `False` before the session started with film demonstrably in the gate,
then `True` after -- another data point for the existing caveat that a clear
bit is not evidence, not a new problem.

- **`tools/scan.py --dpi 600 --bracket 3`.** Calibrated fresh (22s of data,
  ~1.66 MB), then three exposures (x1.706, x3.412, x6.824), merged, filed —
  `library/20260911T094114Z_unknown-film_600dpi` plus `-2`/`-3`, raw bytes
  kept for all three. The top rung clipped hard on red (100% of what
  clipped), which is expected -- it is pinned at the exposure ceiling by
  design -- and the merge fell back to a lower pass for 32% of pixels rather
  than trusting a clipped sample, which is `bracket.py`'s confidence gate
  doing its job, not a fault.
- **`tools/scan_roll.py --frames 3 --dpi 600`.** 3 scanned, 0 failed, 5.0 min.
  Confirmed the async writer genuinely overlaps scanning rather than only
  claiming to: the three library entries carry `created` timestamps 93 s and
  87 s apart (09:43:12, 09:44:45, 09:46:12) -- spread across the roll as it
  ran, not bunched at the process exit, which is what the old
  session-held-open-and-idle path would have produced.
- **`tools/scan_roll.py --dry-run --frames 6`.** 0 scanned (by design), 0
  failed, 1.4 min, six real prescans with varying contrast (0.084-0.314) --
  genuine picture content across every position, not an empty transport read
  six times. Advanced cleanly from position 2 (where the roll above left it)
  through position 7.
- **`tools/scan.py --dpi 600 --reuse`.** `loaded shading from
  calibration/shading.npz` -- no recalibration ran. Filed with 0 samples
  clipped.

`tools/library.py verify` clean on every entry this session filed.

## Known problems

- **An RGBI scan can come back with every row in reverse order, and nothing
  says so.** Found 2026-09-11 driving `docs/byte14-plan.md`'s byte 14 ladder.
  Byte 14 bit 0 clear forces the carriage to re-home to the top before
  scanning; bit 0 set (`0x21`, this driver's unconditional default for every
  RGBI scan) permits scanning from wherever the carriage already sits, and
  when that is the bottom -- because the previous pass also had bit 0 set --
  the read comes back top-and-bottom reversed. Confirmed three separate
  times on the ladder (every second bit-0-set pass in a row, never the
  first, never a bit-0-clear pass) and, worse, in real prior use: the
  tag-lead signature that identifies a reversed pass flags
  `library/20260828T012327Z_unknown-film_1800dpi_ir` -- a `shading-test`
  entry, not delivered work, but real and unprompted, nearly three weeks
  before this was known to be possible. `READ_STATE`, the MODE SELECT
  acknowledgement and `GET PARAMETERS` are all silent about it; the only
  tell is the trilinear CCD's own R/G/B lead-trail order in the raw tags,
  or a correlation check against a known-same frame.

  `scan_roll` and `auto_exposure` avoid the trigger today by the shape of
  the code -- an RGB prescan or probe always precedes the RGBI capture, so
  it is normally the first bit-0-set command since the last reset -- but
  nothing enforces that, and it has never been a designed protection.
  Not fixed. See `docs/byte14-plan.md` for the candidates: detect the
  tag-lead signature and correct automatically, force bit 0 clear always
  and give up the free bidirectional speed, or something else. Whichever is
  chosen changes what is sent to the device, so `PROTOCOL_REVISION` in
  `rps7200/direct.py` moves with it.

- ~~**Drift in roll scans.**~~ **Closed 2026-09-06.** The 1 -> 10 -> 36 px
  strip3 reading that started this was itself a measurement artefact, not the
  transport: four registration detectors were built chasing it and each was
  confidently wrong on some frames (column variance fired on the film's own
  skewed edge; level-vs-aperture is blind mid-strip; brightness-and-flatness
  fired on a bright picture region; the orange-mask ratio read a cyan subject
  as no film at all). A full unattended 17-slide pass -- same length as
  `full_17_strip.pcapng` -- ran clean, every advance sound, judged by eye. The
  transport counts stepper steps and is open loop, but nothing in seventeen
  frames asked it to self-correct because nothing had drifted. **Do not add
  drift correction on the strength of the old reading; there is nothing it
  would be correcting.**

  Separately, and worth keeping distinct from the above: five `SLIDE`
  payloads this file once called unidentified turned out to move the film
  *without* touching the frame counter -- `00 <param> 00 04` forward,
  `01 <param> 00 04` backward, `0.1057 x param + 0.1662` mm, repeatable to
  ±0.02 mm. So sub-frame positioning does exist over USB, contrary to what
  this entry used to say, and `scan_roll(correct=True)` uses it. It is
  insurance for a roll that drifts for some other reason, not a fix for a
  problem that turned out not to exist. See `docs/whole-roll-plan.md`'s
  "Settled" and "Overturned" sections.

- ~~**`registration()` cannot see picture position mid-strip.**~~ **Closed.**
  `film_bounds` keys on film-versus-empty-aperture and is blind mid-strip, as
  this entry said -- but `gap_edges` (`rps7200/framing.py`) does not have that
  blind spot: the inter-frame gap is unexposed base, both brighter than the
  picture *and* flatter down the column, and keying on both together is what
  the four failed detectors above each missed by keying on one. It backs
  `registration_error_mm`, which `_correct_registration` calls -- this is the
  same mechanism the 17-slide roll ran on, not a standalone measurement
  nobody wired up.
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
  passes.

  `filter_offsets` is recorded on every real scan now (it was being dropped by
  `library.save` until 2026-09-11, the same failure `metering` had) and 24
  entries checked since -- every resolution from 300 to 3600 dpi, both film
  types, three separate sessions across three days -- read exactly
  **`[12, 12]`**, matching the one value ever seen in a capture
  (`bw.pcapng`). **Constant rules it out as the explanation**, not in: a fixed
  number cannot be why two passes of the *same* frame sometimes shift by 16
  columns and sometimes by 0. The suspect has an alibi; the offset itself is
  still unexplained. (Prescans do not carry the field yet -- `_prescan`'s meta
  in `rps7200/session.py` does not pass it through -- but prescans are framing
  aids, not the pixels this mystery is about.)
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

- **Filing a roll compresses while the device is open.** `FrameWriter` gzips
  each frame on its own thread while the next one scans, which is what keeps a
  38-frame roll from ending in an eleven-minute wait. CLAUDE.md's warning is
  about the scanner open and **idle**, and here it is busy -- a plausible
  distinction and an untested one, on a path that runs for hours unattended.
  `tools/filing_load_test.py` measures it: alternating identical 300 dpi passes
  on a quiet host and one gzipping in the background, so warm-up cannot look
  like an effect. ~5 minutes, nothing touches the transport.

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
- **Otsu plus morphological opening in `film_bounds`.** Now load-bearing in a
  second place: metering crops to it, so a bad edge would mis-expose rather
  than only mis-report registration. It still fails safe -- an undetected edge
  returns the whole window, which is what metering did before -- but the
  threshold is worth improving on its own merits. It currently cuts at a
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
