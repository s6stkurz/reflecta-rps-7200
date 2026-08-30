# Open work

State at 0.1.0. Grouped by what it needs, because most of the remaining work
does **not** need the scanner — the library keeps every scan's raw bytes and
calibration, so decode and correction changes can be re-run offline.

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
- **Blue is 2-3.7x brighter in RGBI than in RGB at the same exposure**, and the
  cause is unknown. Worked around by metering blue with 4x headroom when an
  infrared scan follows (`auto_exposure(infrared=True)`). The headroom is the
  worst of only two measurements plus margin, so it is probably too
  conservative. Tighten it for free: have each RGBI scan log its actual blue
  level against what the RGB prescan predicted, and the real range emerges over
  a few scans with no extra passes.
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
- **The dependency-free TIFF reader/writer.** `rps7200/tiff.py` has two
  implementations and nothing in `tests/` ever forces `_has_tifffile()` to
  False, so half of it is unexercised. Worth fixing regardless of the
  compression work below.

## Specced but not built

- **Lossless TIFF compression** — `docs/tiff-compression-plan.md`. Worth 16% on
  every file, lossless. Needs the built-in reader taught deflate + predictor
  first, or files written with tifffile become unreadable without it. No
  scanner needed. Note it will *not* make NegPy faster: it shrinks the file on
  disk, not the array in memory.
- **The dpi trade-off measurement** — `docs/dpi-tradeoff-plan.md`. Evidence so
  far says real detail runs out around 26 c/mm (~1340 dpi to sample) and that
  1800 dpi aliases, pointing at 2400 dpi as the sweet spot. Needs the scanner
  and about 45 minutes.

## Improvements identified but not applied

From reading [nkscan](https://github.com/activexray/nkscan), a from-scratch
driver for Nikon Coolscans:

- **Metering target 0.70 -> 0.85.** No reasoning was ever recorded for 0.70.
  `pieusb` uses 0.85 *for this sensor family*, explicitly to leave room for the
  shading correction's per-column gain, which clips edge columns first. That
  reasoning now applies to us. Do it together with a check that nothing clips
  after correction, and do not go to nkscan's 0.97.
- **Otsu plus morphological opening in `detect_frame`.** It currently uses
  `0.25 x peak`, the fixed-fraction rule nkscan explicitly rejects because it
  "lands in the wrong population" when the proportion of film in the pass
  changes. Self-contained and testable against the prescans already stored.

## Decided against

- **Multi-exposure** — `docs/multi-exposure-plan.md`. The 16-bit exposure timer
  caps the long pass at ~1.23x on blue, worth +0.09 density, while averaging
  two passes gives 1.41x. Multi-sampling beats it from the first pass. And this
  scanner already out-ranges colour negative film by about a full density.
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
