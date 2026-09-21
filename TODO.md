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

- **Nothing has ever been scanned at the protocol revision `main` is on.**
  `PROTOCOL_REVISION` went to 3 when fast infrared became the default (7244cc1,
  merged as 1b74ee7) and to 4 when the bit was gated on `infrared` (2026-09-19).
  Across all 217 library entries: **112 at revision 1, 36 at revision 2, 69
  prescans carrying none, and none at all at 3 or 4.**

  The sweep that justified revision 3 is real and is not in question -- but it
  was taken by `tools/fast_ir_probe.py` on the feature branch, with the flag
  passed *explicitly*, before the default moved. Every entry from it reads
  revision 2. What has never run on the device is the path an ordinary scan now
  takes: `tools/scan.py` or the window, with `fast_infrared=True` arriving as a
  default rather than as an argument.

  So the next scan anybody takes is the first exercise of it. Worth doing
  deliberately -- one RGB pass and one RGBI pass, filed with `RPS7200_DEBUG=1`,
  checking that the entries read `protocol_revision: 4` and that the RGB one
  carries no fast-infrared bit -- rather than finding out in the middle of a
  roll. Two of the entries below are the kind of thing that hides in an
  unexercised default: both were found by reading, not by scanning, and both
  are now fixed without the device having confirmed either.

- **`make verify` reports 16 problems and has for a week, so it no longer
  reports anything.** All sixteen are the byte-14 ladder passes, filed between
  `20260911T091344Z` and `20260911T091347Z`, taken with no shading reference so
  they can never be corrected. Two add *"correction was asked for: no shading
  reference in this session"*.

  They are evidence and must not be pruned; `scan.protocol_revision` reads 1 on
  every one, from a code version that no longer exists (`scan()` now raises
  `ShadingUnavailable` rather than filing a pass that wanted correction and
  went without). The problem is the check, not the entries: **the same failure
  `reconstruct` had with its 26 false alarms**, below. A check that is always
  red is a check nobody reads, and this is the one that would catch a real
  regression in the library.

  There is no way to mark an entry as deliberately uncalibrated. `blue-clipped`
  and `not-a-reference` already mark the other deliberate failures, so the
  mechanism exists and `verify` simply does not consult it. Not fixed here --
  recorded so that a red `verify` is known to mean these sixteen and nothing
  else, until someone makes it green.

- ~~**`library.corrected()` calls a shortfall a choice, and tells the operator
  so.**~~ **Fixed 2026-09-19.** It treated *any* non-empty
  `calibration.skipped` as a deliberate `shading=False`, where `verify` reads
  the identical field and correctly splits it: a `skipped` reason other than an
  explicit request is "a thing that went wrong". The two read one field with
  opposite meanings, and the wrong one was what Save As printed.

  For anything scanned today they agree -- `scan()` can only ever write
  `"shading=False (explicit)"`, because it raises `ShadingUnavailable`
  otherwise, and `session.py` documents that invariant. They disagree on four
  entries already on disk, which predate it: two of the sixteen above, which
  carry "no shading reference in this session" (the other fourteen have no
  reason recorded at all and come back as "no reference"), and the two 7200 dpi
  passes of 2026-09-11, which carry "this pass is 10344 columns but the
  reference covers 5172".

  Those four now read `"raw -- correction was asked for"`, and the 23 that
  genuinely passed `shading=False` still read `"deliberately raw"`. The
  sentinel moved to `SHADING_SKIPPED_EXPLICIT` in `direct.py` and is imported
  rather than written out twice, so the producer and its two consumers cannot
  drift apart again; one of the tests pins the constant rather than a copy of
  its text, because a reworded string would otherwise silently make every
  legacy entry read as deliberate again.

  **The wrong label reaches a person.** Save As prints the provenance verbatim,
  so saving one of those entries says *"(deliberately raw)"* -- telling the
  operator the rawness was a choice when it was a shortfall. CLAUDE.md's whole
  point about raw pixels is that a raw file shown to a person is what this
  driver stopped delivering; being told it was intentional is worse than being
  told nothing. Both `corrected()` call sites are otherwise correct and
  carefully commented: the bug is one branch inside `corrected()`, not the
  delivery paths.

- ~~**An RGBI scan can come back with every row in reverse order, and nothing
  says so.**~~ **Closed 2026-09-13 as won't-fix, by Stefan's decision:** a
  reversed pass is visible the moment you look at it and flipping it back is
  trivial, so it does not justify a protocol change. The measurement below
  stays because it explains the behaviour; what is dropped is the intent to
  correct it in the driver. Note the two candidate fixes both cost something
  real -- forcing bit 0 clear gives up the free bidirectional speed on every
  RGBI pass, and auto-detecting the tag-lead signature adds a decode-time
  guess to every scan. Neither is worth it to save a flip.

  If this is ever reopened, the thing to check first is whether anything
  *automated* depends on orientation -- roll framing and `gap_edges` read the
  picture's position -- because a human flipping a delivered file is not the
  same as a reversed pass going through registration. It has not bitten, and
  the trigger is avoided today by the shape of the code, but that is the edge
  where "the user can flip it" stops being sufficient.

  Found 2026-09-11 driving `docs/byte14-plan.md`'s byte 14 ladder.
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
  `docs/byte14-plan.md` holds the candidate fixes, kept for the record
  rather than as a plan: both change what is sent to the device, so either
  would move `PROTOCOL_REVISION` in `rps7200/direct.py`.

  **There is a third path, and it does not avoid the trigger: `tools/scan.py`.**
  Without `--auto-exposure` it takes a single `s.scan(...)` and no RGB pass at
  all, and `data[14]` is `0x21` for RGBI against `0x10` for RGB. The carriage
  position that decides the reversal is *device* state, so it survives process
  exit. Scanning a strip by hand the obvious way --

      uv run python tools/scan.py --dpi 1800 --ir --frame 1     # normal
      uv run python tools/scan.py --dpi 1800 --ir --frame 2     # reversed
      uv run python tools/scan.py --dpi 1800 --ir --frame 3     # normal

  -- gives every second frame upside down, filed that way, with
  `reversal_against` unable to help because it judges a pass against the prescan
  of the same frame and this path takes none. The window has the same shape:
  nothing forces a prescan before a scan, and `_note_reversal` falls back to
  `_prescan_here()`, which is `None` if the operator just presses Scan twice.

  **The decision does not change** -- Stefan reaffirmed won't-fix on
  2026-09-19. What changes is the premise written down above it: "an RGB pass
  always precedes the RGBI one" was true of `scan_roll` and `auto_exposure` and
  was never true of `tools/scan.py` or of the window. Recorded so that the
  won't-fix rests on what the code actually does.

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

- **`registration()` cannot see picture position mid-strip.** *Reopened, then
  closed a second way (2026-09-21).* This entry was marked closed on the
  strength of `gap_edges`, and that was wrong. `gap_edges` keys on brightness
  and flatness **relative to the frame's own content**, so the worse a frame is
  placed, the more gap is in view, the higher the frame's own median climbs and
  the less the detector sees. Measured on a ladder with known offsets it read
  5, 8, then 0, 0, 0, 0, 0 while the gap widened from 7 to 26 px; and because
  it requires the run to start at column 0 exactly, a gap with a sliver of the
  neighbour beside it reads as no gap at all. On a real sixteen-frame walk four
  of nine calls asserted "registered" about a frame it could not see.

  What closes it is `picture_start`, which keys on the gap's **absolute** level
  -- unexposed base is one object under one lamp at one exposure, and its level
  held to 0.66-0.71% across two strips -- plus the rule that no single detector
  may move film. `combine` requires two members that agree in millimetres. See
  `tests/test_ensemble.py`.
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

- ~~**Black and white comes out as an RGB file, and nothing converts it.**~~
  **Built.** The hardware has no black and white mode worth using -- `passes =
  0x04` is the green filter alone, returns untagged PIXEL-format data our
  deinterleave cannot read, and still costs a full colour pass
  (`docs/protocol.md`). CyberView does the same: `captures/bw.pcapng` is eight
  passes and every one is `0x80` RGB. So the conversion is a host-side step,
  and `rps7200/mono.py` is that step.

  **What is delivered is the average of the visible channels**, with R, G and
  B offered beside it — `tools/scan.py --mono-channel`, and the picker in the
  window. Infrared is never averaged in: it is the dust plane, not a record of
  the picture. The library still files all three channels with the raw bytes,
  so the choice is a delivery decision and never a destructive one.

  This entry used to say the conversion was "not started" and to argue for a
  single channel; both are superseded. The measurements behind the argument
  still stand and are in `rps7200/mono.py`'s docstring — averaging gains under
  1% on what the eye sees, because random noise is only 12-18% of the
  high-frequency content and grain is the same grain in all three channels;
  and red is soft, so averaging it in costs a little real detail. The average
  is the default anyway, because it uses everything the scanner captured and
  needs no argument about which channel deserves to win. **If you are picking
  one, pick green or blue** — they are indistinguishable on random noise
  (1.548% against 1.554% in the densest tenth) while red is worse on both
  counts.

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
- ~~**The window sets the fast-infrared quality bit on RGB passes; both command
  line tools deliberately clear it.**~~ **Fixed 2026-09-19, and
  `PROTOCOL_REVISION` moved to 4 with it.** `set_mode` ORs
  `QUALITY_FAST_INFRARED` in whenever `fast_infrared` is truthy, and `scan()`
  forwarded it **without gating on `infrared`** -- so it landed on a
  `passes=ONE_PASS_COLOR` pass just as readily. `tools/scan.py` guarded against
  that (`if not args.ir: args.fast_ir = False`, with a comment saying the bit
  governs nothing when there is no plane to acquire), and so did
  `tools/scan_roll.py` (`args.fast_ir and args.ir`). The window passed
  `v_fast_ir` bare at all three of its call sites, and `Scan.fast_infrared`
  defaults to `True`.

  So: infrared unticked, fast-infrared ticked -- which is what
  `gui-settings.json` persists -- and an **RGB pass went out carrying a quality
  bit that has only ever been measured on RGBI passes.**
  `docs/fast-infrared-plan.md` characterises it for infrared and says nothing
  about RGB. It may well be inert; the point is that nobody knew, two of the
  three callers thought it worth guarding, and the third shipped the unmeasured
  combination by default. Prescans were never affected -- `prescan()` takes no
  `fast_infrared` at all.

  The gate went into `scan()` (`fast_infrared and infrared`) rather than into
  the window, so one place closes all three callers and the tools' own guards
  became redundant rather than load-bearing. `set_mode` is unchanged and still
  writes what it is told: calibration and metering reach it directly and should
  not be gated.

  Measured on the payload, driving a real `scan()` as far as MODE SELECT: an
  RGB pass asked for the bit sent `0x0088` before and sends `0x0008` after,
  which is what CyberView sends; RGBI is `0x0088` either way, and `--no-fast-ir`
  still gives `0x0008`. **Only the RGB row moved**, and none of it has been
  confirmed on the device -- see the first entry in this section.

- ~~**`--no-fast-ir` is accepted and discarded on the bracket path.**~~
  **Fixed 2026-09-19**, alongside the entry above. `scan_bracket` took no
  `fast_infrared` parameter, and `tools/scan.py`'s bracket call passed none --
  where the single-scan branch fourteen lines below it did. `--dpi 600 --ir
  --bracket 3 --no-fast-ir` parsed the flag, set `args.fast_ir = False`, and
  then ran the bracket's infrared pass tied anyway, because `scan()`'s own
  default is `True`. Nothing reported it.

  Worse than a dropped flag usually is: below 1800 dpi the tied pass's *quality*
  was never measured and Stefan waived it (see "Untested" below). `--no-fast-ir`
  is the escape hatch from that waiver, and on this path it did nothing.

  The tests for it subscript `fast_infrared` rather than using `.get()`,
  deliberately: the key used to be *absent*, and an absent flag reads as False
  to any default-tolerant check -- which is how it went unnoticed, and which
  made a first version of the test pass against the unfixed code.

- **`tools/scan.py` and `tools/scan_roll.py` both write the three comparison
  TIFFs to the repo root**, so running them together clobbers one another.

- ~~**`calibrate_shading(exposure_scale=...)` is a no-op.**~~ **Deleted
  2026-09-13.** The device self-meters the calibration pass: writing
  7540-5108-5108 still produced 9604-6506-6506 on all 40 blocks. Harmless in
  practice, but a parameter that looks effective and is not is worse than no
  parameter. The read-then-write of gain/offset stays — the vendor does it
  immediately before this pass — it simply no longer pretends the host chooses
  the values. Note this is *not* the same as exposure being irrelevant to the
  reference: a channel calibrated 3x below its scan exposure corrected
  13.0% -> 1.4%, 6x below 8.2% -> 2.0%, and 10x below got *worse*,
  10.0% -> 11.2%. The device just does not let the host pick.

- **`--reuse` will load a shading reference from a different power-on, and
  nothing can catch it afterwards.** The README is explicit that "the reference
  belongs to the power-on that measured it". Nothing enforces it:
  `ensure_shading(path, reuse=True)` loads whatever file is at the path if it
  exists, and `ShadingReference.save` writes **no timestamp, no session id, no
  device identity** -- only the arrays.

  So a reference measured days ago loads as a success ("reusing
  calibration/shading.npz (5172 columns...)"), the scan is filed with it as
  though it were this session's, and `reconstruct` reproduces it faithfully
  forever. There is no evidence anywhere in the entry that the reference was
  stale. Cheap to close from the save side: a timestamp in the `.npz` and a
  warning when it predates the session.

  Not currently live -- `calibration/` is not on disk, so `--reuse` falls
  through to a real calibration -- which is why this is recorded rather than
  urgent. The next run that writes the cache re-arms it.

- **`apply_shading` silently leaves trailing columns uncorrected** when the CCD
  mask yields fewer used pixels than the image width — it writes only
  `out[:, :loc.size, c]`. `scan()`'s guard catches the gross 7200 dpi case only.
  Latent, not active: at 600 dpi width and columns both came back 860.

## Untested

- **The device path has never run on anything but macOS (2026-09-20).** The
  driver now builds, tests and runs the window on Windows -- 1034 passed, 5
  skipped, `make all` and `make test-all` both green, and CI covers Ubuntu,
  macOS and Windows on every push. None of that touches the scanner.

  What is confirmed on Windows without the device: the loader finds libusb,
  `libusb_init` succeeds, the bus enumerates, `05e3:0144` is seen on it, and
  the open is correctly refused because the stock Image/WIA driver holds it.
  What is **not** confirmed is everything after the open -- the claim, endpoint
  discovery, the IEEE1284 preamble, the 32 KB length handshake, the 16 KB bulk
  reads. Those go through WinUSB rather than IOKit and nothing has driven them.

  The first real pass should be one 300 dpi RGB frame -- about 22 s at the
  median, and budget above it, because scan time tracks `sum(exposure)` as well
  as line count -- filed with `RPS7200_DEBUG=1`. RGB rather than RGBI to keep
  the run short, not to dodge a floor: tied to the resolution, which is the
  default, infrared at 300 dpi costs about 25 s against RGB's 22. A stall or a
  short read in the windowed reader would show there immediately. **Wants Stefan's agreement and
  Zadig run first** -- and note that the Zadig swap stops CyberView and VueScan
  seeing the scanner until the driver is put back through Device Manager.

  Linux is further back still: nothing has been run there at all beyond CI.
  The udev rule in `packaging/` is written from the device reporting
  `bDeviceClass 0xff` and has not been exercised.

- **`force_abort` under Windows is unknown, and should stay that way for now.**
  It closes the handle and calls `libusb_exit` while a bulk transfer may be in
  flight; the docstring already calls that undefined. macOS survives it. Do not
  find out casually what WinUSB does -- a wedge costs a power cycle.

- **Resuming a roll has never been driven on the scanner.** A roll that dies
  part-way can now be reopened from *Rolls ...*, which brings back its contact
  sheet, marks what is scanned, and restores the roll's own settings from its
  manifest -- resolution, film, infrared, metering, and the exposure/gain/offset
  the scanner was asked for. The remaining frames then go through the ordinary
  *Scan chosen frames* path, which rewinds and advances by `SLIDE_NEXT` exactly
  as a fresh roll does.

  Every part is tested offline against synthetic manifests and driven in the
  demo window, and **no real roll has ever been resumed** -- for the same reason
  nothing else about rolls has: a commissioned multi-frame roll on real hardware
  has still never run. The two things a real run would settle: whether the
  restored exposure is still the right *request* a year on, and whether the
  rewind lands where the walk's frame numbers assume when the strip has been
  taken out and put back.

  A resumed roll measures a new shading reference rather than inheriting one.
  That is a choice, not a limit -- `load_shading` and `--reuse` exist, and every
  library entry keeps the reference it would be corrected with. But a reference
  describes the sensor at the exposure and gain of the pass that measured it, so
  the one a roll started with is the wrong thing to hand a resume months later.
  Calibrate set to "reuse" still loads a saved one for anyone who wants to skip
  the 3-4 minutes. See `docs/scanner-options-survey.md` for what is left on the
  scanner side.


- **~~Fast infrared~~ -- answered and adopted 2026-09-16. The infrared plane is
  now tied to the resolution asked for.** Quality bit `0x80` removes the
  infrared floor: a tied pass costs `7.46 s + 59.88 ms/line`, an untied one a
  flat ~220 s whatever the line count.

  ```
    dpi    lines    untied      tied      saved
    300      286     219.2s    24.6s     -88.8%
    600      573     219.6s    41.7s     -81.0%
    900      860     219.8s    58.9s     -73.2%
   1200     1147     220.1s    76.3s     -65.3%
   1800     1721     220.3s   110.5s     -49.8%
   3600     3443     221.0s   213.6s      -3.4%
   7200     6886      ~420s    ~420s       ~0     (predicted; refused anyway)
  ```

  The curves cross at ~3709 dpi, which is the whole explanation of the 3600 dpi
  figure. Film was a red herring: 1800 dpi gives -49.7% on colour negative and
  -49.8% on slide. Quality is clean at 1800 (negative) and 3600 (slide), eleven
  passes. **Below 1800 dpi quality was never measured and Stefan waived it** --
  a low-resolution infrared plane is a coarse dust mask either way. Recorded as
  his decision in `docs/fast-infrared-plan.md`.

  `PROTOCOL_REVISION` moved to 3 with the default. Untie it with
  `--no-fast-ir`, `scan(fast_infrared=False)` or the box in the window.

  Still unexplained, and recorded rather than chased: the bit made the infrared
  plane *quieter* at 3600 dpi (493 -> 314 DN random) where at 1800 it was
  slightly noisier (576 -> 603).

- **~~The untied infrared estimate disagreed with the sweep at 3600 dpi~~ --
  settled by Stefan, sweep wins.** `estimate_seconds` used a 334 s anchor from
  the timing table in `docs/dpi-tradeoff-plan.md`; the 2026-09-16 sweep measured
  221 s. Both are real, on different film at different exposures, and scan time
  tracks exposure -- but the sweep covers the whole range in one run at one held
  exposure, which is what makes a table of resolutions comparable at all. The
  untied estimate is now a *floor* rather than a curve: ~220 s until the line
  count overtakes it, the line count after. That also makes the window's two
  figures converge above 3600 dpi as measurement says they should, where before
  it promised a two-minute saving that was really seven seconds. The 334 s
  figure stays in the timing table as what was measured that day, annotated.

- **The carriage start moves between passes, and fast infrared moves it.** Found by the ladders
  above, not looked for: six passes of one frame registered at 0, 0, -1, -2, -2,
  -3 lines against the first, dx = 0 throughout, over about twenty minutes --
  **with byte 14 bit 0 clear, so a re-home before every pass.** The re-home does
  not land in the same place twice.

  Nothing is known to be broken by it: ~3 lines is 42 um at 1800 dpi, roll
  registration works at a coarser scale than that, and a single delivered scan
  does not care where a previous pass started. What it does break is the
  assumption that two passes of one frame are pixel-aligned, which any
  multi-pass measurement makes -- it silently cost the first reading of the fast
  infrared ladder, where a point feature lost most of its contrast to a
  half-line shift. Anything comparing passes pixel by pixel should register
  first. The second ladder made it worse: at 3600 dpi the offset aligned
  *perfectly with the flag* -- every fast-infrared pass a line or two from every
  ordinary one -- which nearly produced a false negative, because grouping pairs
  into families does not help when the drift correlates with the variable.
  Unmeasured: whether it also moves with bit 0 set, and whether it saturates.

- **Filing a roll compresses while the device is open.** `FrameWriter` gzips
  each frame on its own thread while the next one scans, which is what keeps a
  38-frame roll from ending in an eleven-minute wait. CLAUDE.md's warning is
  about the scanner open and **idle**, and here it is busy -- a plausible
  distinction and an untested one, on a path that runs for hours unattended.
  `tools/filing_load_test.py` measures it: alternating identical 300 dpi passes
  on a quiet host and one gzipping in the background, so warm-up cannot look
  like an effect. ~5 minutes, nothing touches the transport.

- **~~Calibrating at 7200 dpi~~ -- answered on the hardware 2026-09-13, and
  the answer is no.** The device declares the same shading descriptor at
  7200 dpi as at 3600 -- `pixels_per_line=10344` *bytes*, so 5172 columns --
  and calibrates 5172 columns however it is asked. A 10344-column pass
  therefore cannot be corrected from the scanner's own reference at all.
  `MAX_SHADING_COLUMNS` records the ceiling and `scan()` refuses such a pass
  before running it. Mapping output column *j* to calibration column *j // 2*
  was the obvious rescue and was measured offline against both stored 7200 dpi
  entries: it does nothing, because the artefact is a difference *between* the
  column parities and that mapping gives both the same correction. See
  `docs/7200dpi-plan.md`.

- **~~The 7200 dpi shading guard~~ -- done, offline-verified.** Was: the CCD
  mask covers 5172 columns and a 7200 dpi pass is 10344 wide, so the
  correction refused and returned raw pixels, unexercised. What that
  investigation actually found is a **hardware column stagger**: even and odd
  columns of a native 7200 dpi read are physically offset by 4 scan lines --
  confirmed by cross-correlating two unrelated 7200 dpi library entries, every
  channel, correlation peaking cleanly at lag 4 rather than 0. Fixed in
  `_realign_native_column_stagger`, exercised against the stored raw bytes of
  both entries. See `docs/7200dpi-plan.md`.

## Specced but not built

- **A 7200 dpi shading reference from an ordinary pass** —
  `docs/7200dpi-plan.md`. The device caps its *calibrate-mode* reference at
  5172 columns, but an ordinary **scan** at 7200 dpi over `CALIBRATION_FRAME`
  — the lower transport, light path clear, film does not reach it — would
  return 10344 columns of clear-path response through a command the device
  will run at full width. Not the flat-through-film idea `rps7200/shading.py`
  warns against: that one is measured through film and at the wrong exposure,
  and this would be neither. Open: whether the clear-path level at the scan's
  own exposure is usable, and how it interacts with the stagger realignment.
  One ~6 minute pass answers both. **Wants Stefan's agreement before being
  built** — it is a design change, not a fix.
- ~~**Lossless TIFF compression**~~ — **done 2026-09-13**, see
  `docs/tiff-compression-plan.md`. Deflate + horizontal differencing on the
  `tifffile` write path, and **both** readers taught to read it, which was the
  mandatory half: writing files the dependency-free reader cannot open would
  have made the "the built-in one is complete" promise false.

  **It is not "16% on every file"**, as this entry used to say — that came
  from one file. Measured across five real entries the spread is 8–18%,
  averaging 13.4%, and the plan's expectation that a shading-corrected scan
  would beat 16% did not hold. The good case is **RGBI at 18%**, which is also
  the biggest file at 142 MB, so the saving is where it is worth most. Write
  cost 2.2 s on that frame against a 217–334 s scan.

  Still true, and still the thing to correct if someone expects otherwise: it
  will *not* make NegPy faster. It shrinks the file on disk, not the array in
  memory.
- ~~**The dpi trade-off measurement**~~ — **answered 2026-09-13**, see the
  "Results" section of `docs/dpi-tradeoff-plan.md`. **RGB: 3600 dpi for
  quality, 1800 dpi when time matters. RGBI: 3600 dpi, because there
  resolution is nearly free** — the ~250 s infrared floor dominates, and a
  twelve-fold resolution increase costs about six seconds.

  The old guess in this entry — "~1340 dpi to sample, 2400 as the sweet spot"
  — was not what the measurement found, and is superseded. 3600 dpi is the
  *least* aliased resolution measured, not an over-sample.

  **7200 dpi adds nothing**: in matched 100% crops its edges come out *softer*
  than 3600, which is what oversampling an optical blur looks like, for 149 s
  and 320 MB more per frame. That is an independent reason to avoid 7200 dpi,
  arrived at from the optics rather than from the shading ceiling in
  `docs/7200dpi-plan.md` — the two agree without sharing an argument.

  Re-runnable with no scanner: `tools/dpi_analysis.py` rebuilds it from stored
  raw bytes. Worth re-running when the noise floor changes.

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
- **~~Otsu plus morphological opening in `film_bounds`.~~ The question is the
  wrong one, and the harness that says so is stranded on a branch.**

  `film_bounds` is load-bearing in a second place: metering crops to it, so a
  bad edge mis-exposes rather than only mis-reporting registration. It fails
  safe -- an undetected edge returns the whole window, which is what metering
  did before -- and this entry used to argue that its fixed fraction of the
  clear level should become Otsu, the rule nkscan prefers because a fixed
  fraction "lands in the wrong population" when the proportion of film in the
  pass changes.

  **`tools/film_edge_study.py` measures that, and it is not on `main`.** It sits
  on `analysis/film-edge-study` (2026-09-13), the only local branch never merged
  -- 424 lines, offline, running against the stored prescans, comparing eight
  rules including `fixed`, `otsu` and a scale-free `ratio_gap`. Two things it
  records up front, both of which change what is worth doing:

  * **The fraction almost never fires.** The `CLEAR_RATIO` gate short-circuits
    first on nearly every real prescan, so *whatever decides abstention* is the
    load-bearing part -- not the cut. Replacing the cut with Otsu leaves the
    part that actually decides untouched, and Otsu has no notion of "these are
    one population", which is precisely what the gate provides.
  * **The corpus contains none of the object `film_bounds` is calibrated
    against.** Every bright band in it is clear C-41 *film base*, strongly
    orange at R:B 3-7 -- not the neutral empty aperture at R:B ~0.93 that the
    docstring is written for. They are different physical objects.

    Confirmed 2026-09-21 against walk D's fifteen prescans, and the figure is
    much tighter than the range suggests: base holds **R:B 4.21-4.29**, a
    spread of 1.9%. `docs/whole-roll-plan.md`'s "film reads R/B ~1.2-1.3" is
    describing a different pair of objects and should not be quoted.

    **But the mask does not work as a position detector, and this is the second
    time it has been tried.** The picture's own R:B reaches down to 4.28, so it
    is not a threshold; taken instead as a tight cluster -- columns within 3% of
    the strip's own base ratio -- it locates the edge a median **4 px** from
    where the level detector puts it, spread **-11..0 px**. Red is the soft
    channel, so the ratio's transition at a picture edge is gradual where the
    level's is not. It is nowhere near the 1-3 px a member needs. Do not try a
    third time without a different mechanism.

  So: do not implement Otsu on the strength of the old entry. Land the harness
  first, run it against the 217-entry library, and let the numbers say what the
  rule should be. The harness is incomplete in one visible way -- its docstring
  points at `--render` for the cases needing a human and `main()` defines only
  `--root` and `--json`.

  (The variance-based `detect_frame` this item used to name has been deleted;
  it was documented as unreliable and nothing called it.)

- **A roll from the window can reach the lazy shading calibration that stalls
  this machine.** `ScanSession._roll` never calls `ensure_shading` -- the only
  caller is the `Calibrate` job (`rps7200/session.py:759`) -- and
  `ask_to_calibrate` is deliberately non-modal, so it can be dismissed. If the
  roll's first prescan is what finds `self._shading is None`, `scan()`
  calibrates from inside the scan flow, which is the documented stall: *"bulk
  read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE, right after the
  shading descriptor, and the device stops answering"*, measured twice.

  `tools/scan_roll.py` was fixed for exactly this (see
  `tests/test_scan_roll_calibration.py`); the window was not. The fix is **not**
  to call `ensure_shading` unconditionally: with `reuse=False` it recalibrates
  rather than no-opping, so that would add 3-4 minutes to every roll. It has to
  fire only where `self._shading is None`, and it needs a reference path, which
  `Roll` does not carry. Left until someone decides what that path should be
  rather than guessed at.

- **Nothing bounds a roll's total sub-frame travel on the approved path.**
  `framing.ROLL_TRAVEL_LIMIT_MM` is enforced through `StripWalk`, which only
  exists when `correct` or `correct_dry_run` is set (`rps7200/direct.py:3162`).
  A roll driven from contact-sheet positions has only `hold_plan`'s per-frame
  budget, `|target| + HOLD_HEADROOM_MM`, which nothing sums across frames. A
  nudge does not touch the frame counter, so nothing downstream would notice
  the film creeping. Not reached by any real strip measured so far -- walk E's
  peak cumulative displacement is 0.86 mm against a 12 mm limit -- but the
  path is genuinely unguarded.

- **A dry run through `tools/scan_roll.py` files nothing in the library.**
  `debug=False` is passed deliberately at `tools/scan_roll.py:176` because the
  tool files its own entries -- but it does that only in the real-scan branch.
  The dry-run branch writes `prescanNN.tif` and no raw bytes, so a walk's
  passes cannot be re-decoded later. `ScanSession` does file them ("on a dry
  run the prescans are the entire product"), so the window is right and the
  tool is not. CLAUDE.md's "file every scan in the library, with its raw bytes"
  is what this breaks.

- **A correctly placed frame is invisible to the detector that placed it.**
  Measured 2026-09-21 on film, comparing `rolls/registration-G` (as it came)
  with `rolls/registration-H` (held to the positions proposed from G):
  `picture_start` placed **14 of 15** frames before the correction and **4 of
  15** after it, seven of them reading "no unexposed base in view".

  It is arithmetic, not bad luck. `TARGET_GAP_MM` is `(36.4913 - 36.0)/2 =
  0.2457` mm and `GAP_MIN_MM` is `0.25` mm, so a frame placed exactly where it
  is aimed shows **2.9 px** of gap at each edge and the detector needs 3. The
  better the correction, the less there is to see.

  This did not spoil the run -- delivery was measured independently at
  1.013-1.059 mm per mm commanded, and the median distance from target fell
  0.820 -> 0.053 mm on the four frames still measurable -- but it has
  consequences worth knowing before anything is built on it:

  * a correction cannot be verified with the same detector, which is why
    `_rejudge_for` in the in-walk path abstains on a frame it has just fixed;
  * re-walking a corrected strip proposes almost nothing;
  * "no unexposed base in view" on a corrected frame is weak evidence that it
    is centred, and is not a measurement.

  Do not simply raise `TARGET_GAP_MM`: it is where the frame is centred, and
  moving it decentres every frame to suit the detector. Lowering `GAP_MIN_MM`
  to ~0.15 mm (1.8 px at 300 dpi) would let a centred frame be seen, at the
  cost of calling shorter runs gaps. Either way it wants measuring rather than
  choosing, against the stored walks, which costs no scanner time.

- ~~**A reversed *prescan* makes the window turn a correct scan upside down.**~~
  **Fixed 2026-09-21.** Kept below because the measurement is the useful part.
  The hold loop compares that prescan against a *third* picture -- the approved
  reference -- so it already knows which pass reversed; `_hold_to_approved` now
  reports `row_reversed` and `_note_reversal` declines to turn the scan when
  the prescan is the odd one out. The detector is not switched off: a genuinely
  reversed scan is still caught, which `tests/test_session.py` pins both ways.

  It was **five** frames of fifteen, not four: 3, 7, 9, 11 and 13.

- **The original finding, for the record.**
  Found on film 2026-09-21, scanning `rolls/scan600` at 600 dpi RGBI with each
  frame held to a contact-sheet position. Frame 3's **prescan** came back with
  every row reversed -- 8.85 confidence against that frame's own walk
  reference as it came, **92.84** with the rows flipped -- which is the
  MODE SELECT byte 14 bit 0 hazard CLAUDE.md already names. The **scan** was
  fine: `frame03.tif` reads 91.03 against the same reference as saved and 7.96
  reversed.

  `reversal_against(prescan, scan)` cannot tell those two cases apart. Both
  produce the same relative mismatch, and it blamed the scan: *"reads 180
  mirrored"*, margin **0.40** against a `REVERSAL_MARGIN` of 0.25 -- confidently
  wrong, which is what every detector written for this scanner has been at
  least once. `ScanSession.match_prescan` is `True` by default
  (`rps7200/session.py:570`) and `_note_reversal` writes `reversal=[180, True]`
  into the meta, and by its own docstring *"every file that leaves is turned by
  it"*. So a correct frame would be delivered 180 degrees rotated and mirrored,
  in both the TIFF and the JPEG.

  `tools/scan_roll.py` is unaffected -- it drives `FrameWriter` directly and
  never calls `_note_reversal` -- so the frames scanned by the tool are right.
  The window is the path at risk, and it is the path an operator uses.

  Two ways out, neither chosen yet:

  * **Break the tie with a third opinion.** In the held path there already is
    one: the contact sheet's own prescan of that frame. If the fresh prescan
    disagrees with it *and* the scan agrees with it, the prescan is the pass
    that reversed. That is exactly how this was diagnosed, and it is free
    wherever positions are being held.
  * **Stop it happening.** Byte 14 bit 0 reverses a pass that immediately
    follows another bit-0-set pass; a prescan taken straight after a frame's
    RGBI scan is exactly that. Never sending two bit-0-set passes in a row is
    deterministic and needs no detector -- and it would bump
    `PROTOCOL_REVISION`, which is why it wants deciding rather than doing.

  The same reversal is also why frame 3 was never corrected: the hold read
  `unverified` at confidence 8.85 and sent no command. That half failed safe.

- **Do not shrink `HOLD_TOLERANCE_MM`.** Looked at 2026-09-21 after a roll
  appeared to leave held frames up to 0.24 mm off. It did not: that table was
  measured with a broken yardstick (below). With the yardstick fixed the
  post-hoc numbers agree with the loop's own residuals to **0.046 mm** on all
  eleven held frames, the worst being 0.18 mm. The loop delivered what it said.

  The tempting change -- deadband to half the smallest move, 0.136 -- is wrong
  on four counts, all measured:

  * **The constant has five roles, not one.** It is also the roll-wide
    `wrong_way` abort (`direct.py`), `AGREE_MM` (the ensemble's agreement
    ceiling), and the precision floor of both the causal prior and the strip
    line. Halving it would make members agree less often, which produces *more*
    of the unverified frames that were this roll's actual problem. If the
    deadband moves it needs its own constant.
  * **0.136 is the wrong half.** Break-even is half the *delivered* move, not
    half the asked one: at the measured ratio of 1.013 that is 0.1377, and at
    1.059 it is 0.144. A 0.136 deadband commands moves that are guaranteed to
    make things worse.
  * **It has less margin than the delivery scatter.** 0.136 tolerates a ratio
    error of 8.2%; this roll's own scatter was about 13%.
  * **`param_for_mm` stops rounding below 0.219** (`OVERHEAD_MM + STEP_MM/2`)
    and starts clamping to param 1, which *over*-delivers -- and `nudge` reports
    only under-delivery, so below 0.219 the loop runs into a branch that says
    nothing. 0.219 is the floor for any future value, and in replay it changes
    nothing, because nothing the loop measured exceeded 0.181.

  Also worth knowing: **the limit-cycle comment credits the wrong mechanism.**
  Chatter is impossible for *any* deadband above zero, because `direction`
  latches on the first move and `hold_plan` refuses a reversal, with
  `MAX_HOLD_MOVES` capping the run. What the present value actually buys is the
  two things above -- that `nudge` stays a rounding, and that one move always
  lands inside the deadband.

- **`measure_shift_mm` is biased when the two passes are different widths.**
  A 300 dpi prescan is 428 px and a 600 dpi scan decimated by two is 430;
  `_resample_to` stretches the reference to fill the target about column 0, so
  about a pixel of scale error accumulates by mid-frame -- **0.085 mm**, which
  is exactly the one-signed discrepancy that made a good roll look badly
  adjusted. Replacing the stretch with an exact 2x reference moves every
  reading by +0.085 to +0.127 mm **and raises confidence** (median 116 -> 127),
  which says the stretch was smearing the peak rather than resampling costing
  what its docstring claims.

  The driver is not affected: every reading the hold loop took on this roll
  carries `resampled: false`, because the window pins a commissioned scan to
  the survey's own prescan resolution. It is the offline comparisons that are
  wrong, which is where it bit. Worth fixing before any cross-resolution number
  is trusted, and the fix wants a measurement rather than a guess -- what the
  two passes each actually cover, given a 300 dpi prescan comes back 428 px
  where the aperture is 431.

- **`strip_offsets` can never propose a positive offset.** `want` is
  `TARGET_GAP_MM / mm_px` = 2.88 px and `picture_start` cannot report a start
  below `GAP_MIN_MM` = 3 px, so every proposal is at most -0.010 mm. The
  detector is structurally one-sided: it can say "the frame is too far along"
  and never "not far enough". Harmless while every strip measured drifts the
  same way, and a trap the first time one does not.

- **`CORRECTION_DEADBAND_MM = 0.15` is defined and never read.**
  `rps7200/direct.py`, one occurrence in the repo. Left over from the one-shot
  corrector; it is not the deadband anything uses.

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

- **Driving the scanner through `usbscan.sys` does not work, and the code for
  it has been deleted (2026-09-20).** It would have removed the Zadig step on
  Windows and let CyberView keep working, which is the one real friction point
  in installing this there. It got as far as opening the device unelevated and
  reading real answers out of it, and no further. The code is gone because a
  transport that cannot carry a byte is not worth carrying; what it measured is
  here, because that is the part that cost time. `git log` has the module if it is ever
  wanted -- `git show 6c51249:rps7200/usbscan.py`, on the merge that
  precedes the deletion. Do not go looking for a branch: `docs/usbscan-final`
  was local and is not on the remote.

  What worked: the interface opens unelevated, `IOCTL_GET_VERSION` answers
  1.0.0, `IOCTL_GET_PIPE_CONFIGURATION` returns the same endpoints libusb
  finds (bulk IN 0x81 at 512, bulk OUT 0x02, interrupt IN 0x83), and
  `IOCTL_GET_DEVICE_DESCRIPTOR` returns `05e3:0144 bcdDevice 0302` -- a real
  device request, so the device is awake and answering by this route.

  What never worked: `IOCTL_READ_REGISTERS` and `IOCTL_WRITE_REGISTERS`.
  Twenty-three attempts, every one `ERROR_SEM_TIMEOUT` after the driver's full
  120 s. Nothing wedged, ever -- a descriptor request answered immediately
  before and after each one.

  Eliminated, in order, so nobody repeats them:
  1. **The `IO_BLOCK` shape.** The driver validates input size before acting:
     4, 12 and 64-byte inputs are refused instantly with
     `ERROR_INVALID_PARAMETER`, 24 is accepted and goes on to time out. Those
     are different answers to different questions.
  2. **The request content.** `IOCTL_SEND_USB_REQUEST` takes an explicit
     `bRequest`, so it was handed the exact `0x0C`/`0x40` the macOS path sends
     on every scan. Both struct layouts, both directions, all timed out.
  3. **Contention.** It opens with `dwShareMode = 0`, so the Image Acquisition
     service is not holding it, and the write times out on that exclusive
     handle too.
  4. **The device being asleep.** It answers descriptor requests throughout.
  5. **The device path.** WIA reports the Port as the legacy `Usbscan0`
     symlink; it opens, reaches the same device, and fails identically.

  Two findings worth keeping beyond this:
  - `USBSCAN_TIMEOUT` is three ULONGs, not the three USHORTs the published
    `usbscan.h` declares -- so that header is not the one this driver was built
    from, and nothing else taken from it should be trusted without being tried.
  - Setting that timeout does not shorten these failures anyway. It governs
    `ReadFile`/`WriteFile` on the data pipes; a control IOCTL takes the full
    120 s regardless. Probing this costs two minutes a try.

  **What the captures settled, and what they did not.** Stefan's six CyberView
  captures are now readable here (`rps7200/usbpcap.py`), and they show
  CyberView sending this scanner exactly the three control transfers this
  driver sends -- 107,000 of them, same ports, same wIndex, as
  `URB_FUNCTION_VENDOR_DEVICE`. So the wire format is not in question, and
  transfers identical to ours demonstrably do reach this device on this
  machine.

  They do not settle which IOCTL produced them, because USBPcap sits below the
  class driver. And `ERROR_SEM_TIMEOUT` means our URB *was* submitted and went
  unanswered -- so what is needed is a capture of **our own** attempts, to see
  what usbscan.sys actually put on the wire for them, compared against the
  vendor's bytes that `tools/verify_capture.py` now knows exactly. One run
  would name the difference. That needs USBPcap installed, which is a kernel
  driver and needs administrator rights.

  Still unchecked: `MF5000_x64.dll` is 5.3 MB serving a whole family of Pacific
  Image scanners, so its 59 `WRITE_REGISTERS` calls may belong to a different
  model. The captures cannot distinguish that either.

- **IR dust removal** — NegPy does it, and does it well. The point of this
  driver is handing the infrared plane over untouched.

## What the gates do not cover

Found 2026-09-19, reading the repo rather than the code. `make all` is green --
ruff clean, `make type` clean, 947 passed and 3 skipped in 31 s -- and `make
verify` is red with the sixteen entries at the top of this file. Most of what
follows lives in the gap between those two.

One of them has closed since: there is CI now, over Ubuntu, macOS and Windows.
It would have caught both of the bugs the port turned up -- a window that could
not open on either of the other two platforms, and a spool never freed on one
of them -- because nothing but one Mac had ever run this suite. The two items
below are untouched by that and still stand. A third has been added to them:
**the suite is now green on a platform where the scanner has never been
driven**, which is a new way for a green board to mean less than it looks
(see *Untested*).

- ~~**The `hardware` and `slow` pytest markers are declared, documented, and
  used by nothing.**~~ **Both fixed 2026-09-20.** `slow` now marks the tests
  that read the real captures, and `tests/test_hardware.py` is nine tests that
  open the device, claim interface 0 and send INQUIRY and READ STATE -- nothing
  that calibrates, scans or moves the transport. `tools/check_scanner.py` is
  the same ladder as one command. Both skip rather than fail with no scanner.
  The original entry follows, because the *reason* it mattered has not changed:

  **The `hardware` and `slow` pytest markers are declared, documented, and used
  by nothing.** `pyproject.toml` declares both, `addopts = "-m 'not hardware'"`
  deselects one, CLAUDE.md gives `uv run pytest tests/ -m hardware` as a
  workflow, and the README says "a test that genuinely needs the device is
  marked `hardware` and is skipped by default". Measured: 950 tests collected,
  `-m hardware` collects **0**, `-m slow` collects **0**.

  "The suite stays runnable with no scanner" is true only because there are no
  hardware tests at all, so the deselection reads as a protection that exists.
  Every hardware confirmation is a manual session hand-written into this file --
  a defensible choice for a device that wedges, and not what the docs describe.
  Either write the handful of tests or say plainly that confirmation is manual;
  declared-deselected-empty is the worst of the three.

- **`make type` still checks about a third of the tree, but no longer silences
  anything.** Half fixed 2026-09-20: the seven `--ignore` flags are gone and
  `rps7200/` is clean with every rule on.

  They were buying less than they looked. Measured with every rule on: five of
  the seven silenced nothing at all inside the package, and
  `possibly-missing-attribute` fires zero times anywhere in the repo under any
  scope. What the whole list actually hid was 41 diagnostics over **six lines**,
  and 35 of those were one `**kwargs` splat into `tifffile.imwrite`. The six
  are now fixed rather than ignored -- including a real one, `dpi_analysis.py`
  declaring `dict[int, float]` for a value that is a two-key dict, which made
  four correct lines look like defects.

  What is left is the scope, and it is sized rather than guessed: **27**
  diagnostics in `tools/` without gui.py, and **28** in gui.py alone. The
  tools/ ones need `--extra-search-path tests` and one for
  `.claude/skills/measure-scan-quality/scripts`, because three tools import
  through a runtime `sys.path` insertion the checker cannot see; the largest
  cluster is nine in `uniformity.py` that one TypedDict on
  `solve_components`'s return would clear. The gui.py ones are different in
  kind: several want design changes, not annotations -- five fields are
  monkeypatched onto `session.Result`, which declares none of them.

  The original entry follows. Note two of its figures were wrong: it is 154
  diagnostics not 148, seven ignores not six, and gui.py is 6,573 lines not
  4,840.

  **`make type` checks about a third of the tree and silences the rules that
  find bugs.** The target excludes `tests/` and `tools/` and ignores six rule
  classes, `unresolved-attribute` and `invalid-argument-type` among them. So
  `tools/gui.py` -- 6,573 lines, the largest file here and the one an operator
  actually drives -- gets **zero** type checking. A full `uv run ty check`
  reports 154 diagnostics where `make type` reports none.

  Most of the 148 are numpy and tifffile stub noise, which is presumably why the
  ignores exist. The same silencing hides real ones: `tools/uniformity.py:496`
  subscripts and `.shape`s `solved[key]` on a path where it is `None`. Worth
  either widening the target or writing in the Makefile why it is this narrow.

- **Three TIFF cross-implementation tests are permanently skipped**, because
  they want `scans/negatives/*.tif` and `scans/` is gitignored and not on this
  machine either. They are the only tests that put the built-in reader and
  `tifffile` against *real* scanner output rather than synthetic fixtures --
  which is the claim the README makes for `tests/test_tiff.py`. Cheap to fix:
  217 real `library/*/scan.tif` are sitting right there.

- **The largest file in the repo has no design doc.** `tools/gui.py` is 4,840
  lines against `direct.py`'s 3,229. Twelve plan docs cover decisions as small
  as the gain register being a digital multiplier; none covers the window, and
  the three that mention it do so in passing. Its 2,007-line test file is the
  only specification of what it is supposed to do.

  Not documentation for its own sake: it is why the two window-only divergences
  in "Known problems" above went unnoticed. Nothing states what the window
  should do differently from the command line tools, so nothing makes a
  divergence visible as one.

- **Twenty-seven merged branches are still present locally.** Every local branch
  except `analysis/film-edge-study` is merged into `main`. Harmless in itself,
  and it is how the film-edge harness got stranded: the one branch carrying
  unmerged work is invisible in a list of twenty-eight.

## Do not lose

`library/` and `previews/` are gitignored and hold data that cannot be
re-derived without the scanner — including the vignette study's nine 600 dpi
passes with their raw bytes and shading references. A `git clean -xdf` or a
fresh clone would destroy them. Back `library/` up before anything aggressive.

**`calibration/` is not in that class**, though this file used to list it
alongside. It holds one cached shading reference, it costs ~22 s to
re-measure, and every library entry already keeps its own `shading.npz` beside
its pixels. It is also not on disk at all at the moment, so `--reuse` currently
falls through to a real calibration. Listing a one-file cache beside an
irreplaceable 12 GB library only dilutes the warning that matters.

**`demo/` is not in that class either, and it is 6.3 GB.** `make run-demo` files
into `demo/library`, which now holds 274 entries — more than the real library's
217. Every one is re-derivable by definition, being decoded from a library
entry's raw bytes. The .gitignore comment says the demo "leaves no trace",
which is true of git and reads as "costs nothing". Nothing prunes it and
nothing caps it.

## Process

- **Orientation is unresolved.** The scanner delivers lines in transport order,
  which is upside down for viewing. The vertical flip is currently applied only
  to hand-delivered files, never in `scan()`, so the library keeps what the
  scanner sent and still matches its raw bytes. Whether the flip belongs in the
  driver depends on whether it is inherent to the transport or to how the strip
  was inserted — untested.
