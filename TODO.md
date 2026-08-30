# Open work

State at 0.1.0. Grouped by what it needs, because most of the remaining work
does **not** need the scanner — the library keeps every scan's raw bytes and
calibration, so decode and correction changes can be re-run offline.

## Known problems

- **Drift in roll scans.** Frame positions walk as a strip advances. Owned by
  the whole-roll work; see `docs/whole-roll-plan.md`.
- **Blue is 2-3.7x brighter in RGBI than in RGB at the same exposure**, and the
  cause is unknown. Worked around by metering blue with 4x headroom when an
  infrared scan follows (`auto_exposure(infrared=True)`). The headroom is the
  worst of only two measurements plus margin, so it is probably too
  conservative. Tighten it for free: have each RGBI scan log its actual blue
  level against what the RGB prescan predicted, and the real range emerges over
  a few scans with no extra passes.
- **Passes sit at different column offsets and nobody knows why.** Two 3600 dpi
  passes of one frame correlate at r=0.936 only once shifted 16 columns, and a
  shading reference matched a scan best at lag -11/-12 across sessions. Within
  a session at lag 0 it is fine, so it does not currently bite, but it defeats
  any pixel-wise comparison across passes — which is why multi-sampling and the
  dpi study both have to use spectra instead. `ScanParameters.filter_offset1/2`
  are read and unused; they are the first place to look.
- **Library entry `20260828T010052Z` has blue saturated on 2.07% of pixels**,
  from a metering error. Its raw bytes are fine; treat its pixels as damaged.
- **`tools/scan.py` and `tools/scan_roll.py` both write the three comparison
  TIFFs to the repo root**, so running them together clobbers one another.

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

## Process

- **Orientation is unresolved.** The scanner delivers lines in transport order,
  which is upside down for viewing. The vertical flip is currently applied only
  to hand-delivered files, never in `scan()`, so the library keeps what the
  scanner sent and still matches its raw bytes. Whether the flip belongs in the
  driver depends on whether it is inherent to the transport or to how the strip
  was inserted — untested.
