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

  *Updated 2026-09-25:* it has moved twice more since, to 5 when
  `MAX_CORRECTION_PARAM` went from 8 to 87 (df7d4e6, 2026-09-22) and to 6 when a
  roll first asks the transport where the film is and goes there (2b394b0,
  2026-09-23). The constant lives in `rps7200/protocol.py`; `rps7200/direct.py`
  only re-exports it.

  So the next scan anybody takes is the first exercise of it. Worth doing
  deliberately -- one RGB pass and one RGBI pass, filed with `RPS7200_DEBUG=1`,
  checking that the entries read `protocol_revision: 6` and that the RGB one
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

  ~~There is no way to mark an entry as deliberately uncalibrated. `blue-clipped`
  and `not-a-reference` already mark the other deliberate failures, so the
  mechanism exists and `verify` simply does not consult it. Not fixed here --
  recorded so that a red `verify` is known to mean these sixteen and nothing
  else, until someone makes it green.~~ **The check is fixed; the sixteen are not
  tagged yet (2026-09-24, db9466b).** `verify` no longer reports a scan taken raw
  on purpose (`SHADING_SKIPPED_EXPLICIT`), a demo entry built from a finished
  picture, or an entry tagged `uncalibrated-on-purpose`, which
  `tools/library.py tag ENTRY... --add uncalibrated-on-purpose` sets. What is
  left needs the library: tag the sixteen and see `make verify` go green. It
  now checks more than it did -- every file's checksum, entries still holding
  an `INCOMPLETE` marker, directories with no `scan.json` -- so anything else it
  reports after that is new.

- ~~**`library.corrected()` calls a shortfall a choice, and tells the operator
  so.**~~ **Fixed 2026-09-19.** It treated *any* non-empty
  `calibration.skipped` as a deliberate `shading=False`, where `verify` reads
  the identical field and correctly splits it: a `skipped` reason other than an
  explicit request is "a thing that went wrong". (*2026-09-25:* `verify` did not
  split it either, until db9466b -- it said "correction was asked for" about
  the sentinel that says it was not. See the entry above.) The two read one
  field with opposite meanings, and the wrong one was what Save As printed.

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

  **Superseded 2026-09-23 (622aa5a), and the won't-fix with it: every pass is
  now read upright from its own line tags.** `rps7200/direction.py` reads which
  way the carriage went -- R first and B last is top-down, the reverse is
  bottom-up -- and `DirectScanner.decode_index` turns a bottom-up pass upright,
  so the driver, the demo, `reconstruct` and the tools all deliver it upright;
  the raw bytes are untouched. Each entry records `scan.read_direction`, and
  `tools/library.py migrate-direction` brought the older ones up to date. The
  mechanism below is also corrected there: a pass's bit 0 leaves the carriage at
  the far end, and the *next* pass reads bottom-up whatever its own bit. See
  `docs/byte14-plan.md`. What follows is the record as it was written; the
  `tools/scan.py` example no longer files every second frame upside down.

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
  would move `PROTOCOL_REVISION` in `rps7200/protocol.py`.

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
  ±0.02 mm (the first fit; the law in force is `param + 1.84` units, see
  CLAUDE.md). So sub-frame positioning does exist over USB, contrary to what
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

  **That rule does not hold on the paths an operator uses (audit, 2026-09-24,
  P31).** The window and `tools/scan_roll.py` hand `StripWalk` the
  `tools/frame_edges` reader, so `combine` never runs there. Its vote accepts
  `lone_gap` -- one member's gap with the neighbour, at confidence 0.3, sourced
  `unconfirmed` -- and `_aim_frame` moves the film on any decision within its
  tolerance and budget without looking at the source; `scan_roll --approved`
  holds `unconfirmed` and `neighbours` proposals too. Whether that stands is in
  *Decisions for Stefan* below.
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
  and never applies them. ~~`library.save` then drops them on the floor, the
  same way it was dropping `metering`, so no filed entry has them either.~~ It
  no longer does (checked 2026-09-25): every entry filed since 2026-09-11 keeps
  `scan.filter_offsets`, and all of them read `[12, 12]` (two entries down). The
  decode still aligns the planes by index and applies neither; the raw bytes
  are kept, so a correction here stays re-runnable offline. Start there.

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
  still unexplained. ~~(Prescans do not carry the field yet -- `_prescan`'s meta
  in `rps7200/session.py` does not pass it through -- but prescans are framing
  aids, not the pixels this mystery is about.)~~ They do now (checked
  2026-09-25): a prescan is filed with the pass's own meta, `filter_offsets`
  included.
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

- ~~**`tools/scan.py` and `tools/scan_roll.py` both write the three comparison
  TIFFs to the repo root**, so running them together clobbers one another.~~
  **Not so, checked 2026-09-25:** neither writes them. Only
  `tools/make_comparison.py` does, and since ceb9081 (2026-09-24, audit P30) it
  builds them from one library entry through `library.corrected` and
  `export.write`, into the directory `--out` names. It used to apply `destripe`
  to whatever TIFF it was given, which no delivered file has ever had.

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

  **Half closed 2026-09-24 (f64671f, 2e04b01):** the evidence now exists. Every
  entry says where its reference came from, in `extra.shading_origin`: measured
  this session (when, at what width, and the `calibration/<UTC time>/` archive
  of its bytes) or loaded (which file, modified when, loaded when). The window
  already warns by the file's age. Still open: `tools/scan.py` and
  `tools/scan_roll.py` print "reusing ..." and nothing more, and the `.npz`
  itself carries no timestamp.

- **`apply_shading` silently leaves trailing columns uncorrected** when the CCD
  mask yields fewer used pixels than the image width — it writes only
  `out[:, :loc.size, c]`. `scan()`'s guard catches the gross 7200 dpi case only.
  Latent, not active: at 600 dpi width and columns both came back 860.
  *Updated 2026-09-25:* not only latent. At 600 dpi the device has returned 862
  columns as well as 860 (7c5c185), and the vignette study met a mask of 860
  against a width of 862 (`docs/vignette-plan.md`), so such a pass goes out with
  its last two columns uncorrected and the report's `columns < width` is all
  that says so. The guard `docs/shading-calibration-plan.md` specified --
  compare the width with the mask's used count -- was never built.

- **Millimetres are still held, stored and printed for transport distances**,
  against CLAUDE.md's rule (found by the 2026-09 audit: FU-10, D12, GUI2-A5,
  CLI-21). `approved.json` stores `offset_mm`, `Approved.offset_mm`,
  `param_for_mm`, `nudge` and `plan_nudges` take millimetres, `framing.SEARCH_MM`
  bounds the correlation, and `tools/scan_roll.py`'s `--nudge` and its
  registration lines ("offset ... mm", "SHORT BY ... mm") are what an operator
  reads. The window already says units everywhere (`protocol.say_units`). A
  stored offset re-snapped under a corrected law is the right behaviour, so the
  change is to the unit held, not to what is stored.

- **Two of the window's dialogs promise what the code does not.** Deleting a
  roll says "the frames can be rebuilt from them" and counts only the
  `approved.json` files holding turns or flips (`on_delete_rolls`); nothing
  rebuilds a roll folder, and its manifests, prescans and hand-set positions go
  with it. Reopening one says "It will calibrate again first" (`open_roll`); a
  window that already calibrated this session does not. README says what
  happens. (Audit D15, GUI2-30, GUI1-34.)

- **Comments and messages in the code that the docs now contradict**, left for a
  code branch because this pass changed documentation only:
  `EXPOSURE_TARGET`'s comment in `rps7200/direct.py` still cites 1.5-1.9%
  compression above 75% of scale (measured since at -0.6 to -0.8%,
  `docs/exposure-negative-plan.md`); `auto_exposure` says `SET GAIN OFFSET`
  persists, where `scan_roll`'s own comments and CLAUDE.md say it does not;
  `protocol.units_for_param` says `param 1` travels 2.57 (it returns 2.84) and
  the comment above it gives the superseded `0.1662 mm` law; `plan_nudges`
  describes a param 1..8 lattice clamped at 8 (the cap is
  `MAX_CORRECTION_PARAM`, 87); fourteen refusals and docstrings quote "the
  ~212 s floor" for any infrared pass, where a tied one costs ~25 s at 300 dpi;
  and the adjuster's shortcuts are labelled "Move the film one step" when they
  only set an offset.

## Decisions for Stefan (from the 2026-09 audit)

The fixes for `audit/problems/P01`-`P32` landed 2026-09-24/25. These are what
their authors left alone because the answer changes a measured conclusion,
what moves film, what Delete means, or what is sent to the device -- each with
the proposal as it was made. None is started.

### Framing and the transport

- **Can one detector member move film?** With an edge reader -- the window,
  `tools/scan_roll.py` -- `lone_gap` goes through `decide()` to
  `WalkReader.judge`, `StripWalk` skips `combine`, `_aim_frame` never checks the
  decision's `source`, and `scan_roll --approved` holds `unconfirmed` and
  `neighbours` proposals without review (see the `registration()` entry above).
  It changes the study-validated voting rule and what moves film in normal
  use. *Proposal:* in `_aim_frame`, abstain unless `detail["source"] ==
  "measured"`, or have `WalkReader.judge` return None for `unconfirmed`; in
  `hold_from_walk`, hold only `measured` positions unless a flag says
  otherwise, and print each frame's source before the device opens.
- **Retire the 36 mm frame model.** `framing.FRAME_WIDTH_MM = 36.0`,
  `TARGET_GAP_MM`, `NOMINAL_FRAME_WIDTH`, `right_gap_closure(frame_mm=36.0)` and
  the `MAX_CORRECTION_MM` derived from them still aim every roll with no edge
  reader -- positives, Kodachrome, any caller of `DirectScanner.scan_roll`
  without one -- while `tools/frame_edges` centres on the measured 350.6
  units. It changes fitted, hardware-validated aiming, and tests pin both.
  *Proposal:* make `FRAME_WIDTH_UNITS` the one geometry source for the legacy
  aim point, validate with a walk of a positive strip, then remove the mm
  constants and update `tests/test_frame_position.py` and
  `tests/test_ensemble.py`.
- **`SEARCH_MM` is narrower than the largest move.** Its 9.0 mm is about 85
  units, 105 px at 300 dpi; `param 87`, 88.8 units, is about 110 px, so a hold
  of that size can never verify. Widening the search changes `measure_shift_mm`'s false-peak
  behaviour. *Proposal:* derive the window from `(MAX_CORRECTION_PARAM +
  COMMAND_UNITS)` plus a margin through `units_per_column`, then run holds at
  `param 80-87` on the hardware and check confidence stays above the floor.
- **Before any resolution joins `frame_edges.READ_AT_DPI`** (FE-04):
  `propose.centring` hands `decide()` the 428-column scale while a wider pass's
  edges are in its own columns. Harmless while only 300 dpi is read.
  *Proposal:* decide on the downscaled result, scale the columns by k, and add
  a test that runs `centring` on an 856-column frame.
- **Does a change to the sub-frame law move `PROTOCOL_REVISION`?** Revision 5
  was bumped for the param cap, but `COMMAND_UNITS` 1.572 -> 1.84 (2026-09-22)
  changed the params `param_for_mm` sends without a bump, so an entry's
  revision cannot say which ramp its hold moves used, and
  `docs/step-calibration-plan.md` said it would move. *Proposal:* decide, write
  the rule beside the revision history in `rps7200/protocol.py`, and either bump
  or record the law in each pass's meta.

### Calibration

- **The command-line tools calibrate without asking** (P17 remainder).
  `tools/scan.py` calibrates straight after INQUIRY; `tools/uniformity.py` tells
  the operator to empty the transport and then calibrates; the media flag is
  never recorded at calibration time. Only the window's prompt was in scope.
  *Proposal:* before a measuring `ensure_shading` in `tools/scan.py` and
  `tools/scan_roll.py`, ask with `input()`, with a `--film-loaded` flag to skip
  it (not for `--reuse` with a cache, nor `--no-shading`); in `uniformity.py`,
  drop the empty-transport metering or put it behind `--empty-transport` with a
  warning, and ask for film before `calibrate_shading`; record
  `State.no_media` from the READ STATE `calibrate_shading` already sends into
  the calibration archive -- host side, no new command.

### Rolls and the contact sheet

- **Continuing a reopened roll that has no walk** (P25, GUI1-25/GUI2-20). The
  plain Roll button runs from the first remaining frame to the original last
  one and rescans the done frames between, because it cannot pass `only`. A
  UI decision. *Proposal:* in `open_roll`'s no-sheet path set the last frame to
  the last remaining one, and when the roll box still names the loaded roll
  have `on_roll` pass `only` = the remaining frames in range; or add a
  "Continue this roll" button that submits `Roll(only=remaining, out=folder)`.
- **Delete moves a library entry to a trash rather than removing it?** Deleting
  a frame from the filmstrip still `rmtree`s its entry (outside the demo, after
  asking). Soft delete changes what Delete means for the library. *Proposal:*
  `library.trash(entry)` moving it to `<root>/.trash/<id>` and reindexing, used
  by the window's Delete and by `tools/library.py duplicates --delete`, with a
  purge command.
- **Save the sheet's decisions on every change** (P22). They are kept on Close,
  quit, opening another roll and commissioning; a crash with the sheet open
  still loses them, and `_store_sheet_state` rewrites the whole settings file.
  *Proposal:* from `_changed`, `_FrameAdjuster._set` and `_orient`, schedule
  `_store_sheet_state(self.state())` through `gui._later(500, ...)`, cancelling
  any pending call so rapid edits write once.
- **`--demo` with an explicit `--rolls`, `--library` or `--settings`** still
  points at the real folders (P18, DP-05). An explicit operator choice, made
  less dangerous now that the confirmation names the folder and `_roll_folder`
  never writes back into one opened from outside `session.rolls`. *Proposal:*
  under `--demo`, refuse such paths outside `demo/` unless a new
  `--demo-writes-real` is given, or print one warning line at start-up.

### The demo

- **Should the demo refuse a calibration under `--look-only`?** The scanner
  does not refuse one with no film -- it carries it out, and that is the state
  that preceded a wedge -- so a refusing demo would teach the window an answer
  the hardware never gives. `--look-only` is demo-only now, so no real scanner
  can be calibrated through it. *Proposal, if wanted:* in
  `DemoScanner.ensure_shading`, call `self._need_film("calibrate")` before the
  work unless skipping; the window's failed-job path reports it, and
  `test_gui`'s empty-transport test can add `ensure_shading`.
- ~~**Should `verify` report demo entries' missing reference?**~~ **Settled in
  code 2026-09-24 (4b7ad6d):** a demo entry built from a stored prescan or a
  test card is a finished picture with no calibration, and `verify` leaves it
  out, as it does a scan taken raw on purpose.

### Brackets and the measurement tools

These change a number already quoted -- a merged pixel, a skill's shares, a
documented series -- so each wants re-measuring from stored bytes and Stefan's
eye, not a patch.

- **One relation for three channels, and an assumed noise model** (OUT-05,
  OUT-19). `merge_bracket` fits one slope and intercept on green and applies
  them to R, G and B although their dark levels differ, and weighs with
  `bracket.py`'s fallback alpha/beta; `fit_noise_params` is never called.
  *Proposal:* `solve_relation` per channel, on the sensor frames, each channel
  scaled by its own; alpha/beta per channel from library flats, both recorded
  in `meta["bracket"]`; compare old and new merges of the 2026-09-11 600 dpi
  bracket by eye.
- **`--bracket` with `--no-shading`** merges uncorrected passes and fuses each
  pass's column pattern, which `bracket.py` says must not happen. Refusing it
  moves the test harness too (`tests/test_scan_tool.py`'s `run()` passes
  `--no-shading` to every bracket test). *Proposal:* `ap.error` for the pair in
  `tools/scan.py`, and the bracket tests on the correcting fake
  (`run_correcting`).
- **A bracket is held in RAM** (P29.4, CLI-16): every pass's corrected frame,
  raw pixels and raw bytes; with `--no-library` the raw pixels are now held for
  the merge too, about 1 GB more for nine passes at 3600 dpi. *Proposal:* spool
  each pass plainly in `on_pass`, as `_debug_capture` does, call
  `scan_bracket(retain=False)`, and after `close()` memory-map the spool into
  `merge_bracket`, which already works in `CHUNK_ROWS` bands. Check on the
  hardware that a session held open while spooling stays well.
- **`noise_split` measures random noise unregistered and unfiltered** (MSQ-01).
  `total` is high-passed and `random` is not, so pure white noise reads a
  share of 1.118 -- and `ceiling()` now refuses a share at or above one, which
  a registered, gain-matched pair dominated by random noise can reach.
  *Proposal:* register rows and columns (`rps7200.uniformity.register`), fit
  gain and offset with `solve_relation`, take `random = std(hp(x) - hp(y)) /
  sqrt(2)`, re-run a stored repeat pair and update the shares in the skill (21%
  at 300 dpi, 27% at 1800, and the -3.5% ceiling built on them).
- **`agreement_z`'s baseline** (MSQ-04, MSQ-05). The ideal median `|z|` is
  0.674, so the 1.03 two-repeat baseline means the noise model understates
  sigma about 1.5x; the median also runs over pixels the fit excluded, and the
  rail is judged on whatever domain it is fed. *Proposal:* give it the raw
  arrays for `solve_relation`'s `ref_sensor`/`other_sensor`, take the median
  over the usable mask, report ratios to 0.674, and re-derive the baseline and
  the "agreement holds to x1.7" finding from a stored repeat pair.
- **`tools/exposure_headroom.py` models every film's blue as colour negative's**
  (`MEASURED_BLUE_RATIO` 4.98, `BLUE_RGBI_HEADROOM` 5.2) while the driver meters
  with `blue_rgbi_headroom(film)`. No RGBI ratio is measured for slides or
  Kodachrome. *Proposal:* take the headroom from the record's film, store the
  measured ratios per film (negative 4.98-5.02, B&W ~9.6), and refuse or flag
  RGBI entries of films with none.
- **The dpi series itself** (PA-14, MSQ-02/03). 7200 dpi entries filed before
  2026-09-13 keep the stagger zigzag, which `--domain raw` still compares; the
  default time window drops the last minute by comparing `10:45` against
  `10:45:SS` as text; rungs are not checked to be one frame and film. Each fix
  can change which entries make up the documented series. *Proposal:* replay
  the realignment on those entries when loading raw, compare `created` as
  datetimes, warn when rungs differ in frame or film notes, and re-run with
  `--entries` naming the six ids.
- **Two registration tools validate differently from the driver** (PA-08).
  `tools/registration_margin.py` and `tools/transport_truth.py` read upright
  only, with a differently rounded reach, where `measure_shift_mm` takes the
  better of upright and row-flipped; `transport_truth` retypes 106 and 55.
  Their result decides whether `CONFIDENCE_FLOOR` holds. *Proposal:* factor
  `framing.shift_readings(reference, now, width)` returning confidence, dy, dx
  and `row_reversed` from both readings with `measure_shift_mm`'s reach, call
  it from both tools, and import `CONFIDENCE_FLOOR`.
- **`tools/exposure_probe.py` reads delivered levels from a region re-detected on
  the metered pass**, not the one metering used (PA-11). *Proposal:*
  `auto_exposure` stores its slice bounds in `last_metering["region"]`, the
  probe applies them, and a test puts film at about 0.8 of full scale beside a
  clear aperture. It re-interprets stored probe results.
- **What `tools/filing_load_test.py` measures, and its line.** 300 dpi 8-bit
  passes, whole-pass times rather than read-loop stalls, a 32 MB gzip -- and
  the 5% line was chosen, not measured. Running at the roll's settings changes
  what is sent to the device. *Proposal:* `--dpi`/`--ir`/`--depth` defaulting to
  the roll's settings, the longest gap between chunks and NoDataYet streaks
  recorded per pass, real-sized payloads (140-570 MB); Stefan to confirm or
  replace the 5%.

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

- **The 2026-09-24/25 audit fixes have not run on the scanner.** Every one is
  tested offline and in the demo, and ordinary passes send what they sent
  before, so `PROTOCOL_REVISION` did not move. What differs on the device side,
  and so wants watching the first time: after a pass or calibration stops
  before its last line nothing more that drives the device is sent
  (`DeviceSuspect`); a corrected pass no calibration covers is refused before
  anything is sent, metering included, instead of calibrating inside it; the
  read of an untied infrared pass waits up to 287 s for data where it gave up
  at 120; a calibration writes its 1.7 MB of bytes to `calibration/<UTC time>/`
  with the device open, a plain write; the window files single scans
  uncompressed and gzips them after closing; every pass records its commands
  through `_CommandLog` around the transport. `tools/filing_load_test.py`'s new
  verdict has not been run either.

- **Resuming a roll has never been driven on the scanner.** A roll that dies
  part-way can now be reopened from *Rolls ...*, which brings back its contact
  sheet, marks what is scanned, and restores the roll's own settings from its
  manifest -- resolution, film, infrared, metering, ~~and the
  exposure/gain/offset the scanner was asked for~~ the mono channel, the
  registration options and the first and last frame. *Corrected 2026-09-25:*
  no exposure, gain or offset is restored, and a `Roll` cannot carry one -- it
  meters as its metering setting says, so a resumed `once` roll meters again on
  its first new frame. The remaining frames then go through the ordinary *Scan
  chosen frames* path, which winds back with `SLIDE_PREV` and advances by
  `SLIDE_NEXT` exactly as a fresh roll does, into the roll's own folder (its
  name is put back in the roll box).

  Every part is tested offline against synthetic manifests and driven in the
  demo window, and **no real roll has ever been resumed** -- for the same reason
  nothing else about rolls has: a commissioned multi-frame roll on real hardware
  has still never run. The two things a real run would settle: whether
  re-metering keeps the resumed half consistent with the first a year on, and
  whether the rewind lands where the walk's frame numbers assume when the strip
  has been taken out and put back.

  A resumed roll ~~measures a new shading reference rather than inheriting
  one~~ uses the window's calibration: one that has not calibrated since it
  opened asks first, as for any scan, and one that has uses what it measured
  (*corrected 2026-09-25*; the reopening dialog still says "It will calibrate
  again first"). Not inheriting the roll's old reference is a choice, not a
  limit -- `load_shading` and `--reuse` exist, and every library entry keeps
  the reference it would be corrected with. But a reference
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

  *Updated 2026-09-25:* its verdict could not say "unsafe" -- it called any
  difference under twice the spread of all passes pooled "no measurable
  effect", and that spread contains the difference itself. Since 8fa4d73 it
  judges the paired differences against a stated line, 5% of a quiet pass
  unless `--limit` moves it: safe, unsafe or inconclusive, and only "safe" says
  to overlap. What it still does not measure is in *Decisions for Stefan*. The
  window's single scans no longer compress with the device open at all
  (0bb498f): they are filed plain and gzipped after it closes, so the roll's
  overlap is the one exception left.

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
  quality, 1800 dpi when time matters.** ~~**RGBI: 3600 dpi, because there
  resolution is nearly free** — the ~250 s infrared floor dominates, and a
  twelve-fold resolution increase costs about six seconds.~~ *Retracted there:*
  that was true of the untied infrared pass. Tied to the resolution, the default
  since 2026-09-16, RGBI has the same trade-off as RGB.

  The old guess in this entry — "~1340 dpi to sample, 2400 as the sweet spot"
  — was not what the measurement found, and is superseded. 3600 dpi is the
  *least* aliased resolution measured, not an over-sample.

  **7200 dpi adds nothing**: in matched 100% crops its edges come out *softer*
  than 3600, which is what oversampling an optical blur looks like, for 149 s
  and 320 MB more per frame. That is an independent reason to avoid 7200 dpi,
  arrived at from the optics rather than from the shading ceiling in
  `docs/7200dpi-plan.md` — the two agree without sharing an argument.

  Re-runnable with no scanner: `tools/dpi_analysis.py` rebuilds it from stored
  raw bytes. Worth re-running when the noise floor changes. *Updated
  2026-09-25:* since 3f5cce0 it loads every rung in one domain or refuses the
  series, and it refuses this one in its default, corrected domain: the 7200 dpi
  top rung cannot be corrected, so the recorded figures compared a raw
  reference against corrected rungs. `--domain raw` re-runs the whole series
  raw and gives different numbers; the recommendation has not been re-derived
  from them yet.

## Improvements identified but not applied

From reading [nkscan](https://github.com/activexray/nkscan), a from-scratch
driver for Nikon Coolscans:

- ~~**Metering target 0.70 -> 0.85.**~~ **Done, at 0.80**, and settled by
  measurement rather than by copying pieusb. `tools/exposure_headroom.py`
  simulates a higher exposure on stored raw bytes and runs the real shading
  correction over it: clipping permits well past 0.90 (worst case 0.001% of blue
  at 0.80 over six entries and four frames), but ~~the sensor compresses
  1.5-1.9% above 75% of scale, so linearity binds before clipping does~~ -- see
  the correction below. 0.80 also matches `bracket.py`'s `CLIP_START`, so
  metering no longer aims where another module declines to follow. See
  `EXPOSURE_TARGET` in `rps7200/direct.py`.

  *Corrected 2026-09-20* (`docs/exposure-negative-plan.md`, fifteen colour
  negatives): the 1.5-1.9% does not reproduce. The sensor departs about -0.6% to
  -0.8% above 70% of scale, on negative and slide alike -- the recorded figure
  was the slide ladder's saturation read on corrected pixels. 0.80 stays, on a
  measured reason instead: metering's own frame-to-frame spread (green 0.788 to
  0.813) leaves too little headroom at 0.90. The comment on `EXPOSURE_TARGET`
  still quotes the old figure (see *Known problems*).

  What that change *cost*, and is worth remembering: the acceptance band was
  `abs(level - target) <= 0.08`, which at 0.70 topped out at 0.78 and was
  harmless. Moving the target to 0.80 moved the top of that band to 0.88, past
  the knee, and a B&W frame duly landed at 87% with samples at the rail. The
  band is asymmetric now. Raising a target is not safe unless the band above it
  is looked at too.
- **~~Otsu plus morphological opening in `film_bounds`.~~ The question is the
  wrong one, and the harness that says so has not been run on `main`.**

  `film_bounds` is load-bearing in a second place: metering crops to it, so a
  bad edge mis-exposes rather than only mis-reporting registration. It fails
  safe -- an undetected edge returns the whole window, which is what metering
  did before -- and this entry used to argue that its fixed fraction of the
  clear level should become Otsu, the rule nkscan prefers because a fixed
  fraction "lands in the wrong population" when the proportion of film in the
  pass changes.

  **`tools/film_edge_study.py` measures that.** Written 2026-09-13 on
  `analysis/film-edge-study` and merged 2026-09-24 -- offline, running against
  the stored prescans, comparing eight rules including `fixed`, `otsu` and a
  scale-free `ratio_gap`. Two things it
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

  So: do not implement Otsu on the strength of the old entry. Run the harness
  against the 217-entry library and let the numbers say what the rule should
  be. It is incomplete in one visible way: nothing renders the cases needing a
  human, and `main()` defines only `--root` and `--json`.

  (The variance-based `detect_frame` this item used to name has been deleted;
  it was documented as unreliable and nothing called it.)

- **~~A roll from the window can reach the lazy shading calibration that
  stalls this machine.~~ Closed 2026-09-24: there is no lazy calibration any
  more.** `scan()` refuses a corrected pass that no reference covers
  (`DirectScanner.uncalibrated`) before it sends anything, metering included,
  instead of calibrating inside it -- the path measured twice as *"bulk read of
  16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE"*. A roll from the window
  whose Calibrate was dismissed or failed now fails its first frame with that
  message rather than stalling the device. `--no-shading` reaches every pass of
  a roll (prescans and metering probes too), so it no longer means "calibrate
  lazily later". See `audit/problems/P15-lazy-calibration-inside-scan.md`.

- **Nothing bounds a roll's total sub-frame travel on the approved path.**
  `framing.ROLL_TRAVEL_LIMIT_MM` is enforced through `StripWalk`, which only
  exists when `correct` or `correct_dry_run` is set (`DirectScanner.scan_roll`).
  A roll driven from contact-sheet positions has only `hold_plan`'s per-frame
  budget, `|target| + HOLD_HEADROOM_MM`, which nothing sums across frames. A
  nudge does not touch the frame counter, so nothing downstream would notice
  the film creeping. Not reached by any real strip measured so far -- walk E's
  peak cumulative displacement is 0.86 mm against a 12 mm limit -- but the
  path is genuinely unguarded.

- ~~**A dry run through `tools/scan_roll.py` files nothing in the library**
  unless `RPS7200_DEBUG=1`.~~ **Fixed 2026-09-24 (8061a86, audit P08/P21):**
  `--dry-run` files each walk prescan with its raw pixels and bytes, as the
  window does, labelled `<roll>-<NN>` and recording its `roll_membership`; the
  raw-byte guard both writers use is one function now
  (`session.raw_bytes_disagree`). The entry as it was:

  The tool no longer forces `debug=False` (it claims
  the frames it files itself, `DirectScanner.debug_claim`), so with debug on
  the walk's prescans, probes and holds are filed -- but by default the tool
  files its own entries only in the real-scan branch.
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
  (`rps7200/session.py`) and `_note_reversal` writes `reversal=[180, True]`
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
  on four counts, all measured. (*2026-09-25:* the millimetre figures here are
  under the 1.57-unit ramp. Under 1.84 the smallest move is 2.84 units --
  `HOLD_TOLERANCE_MM` holds exactly that -- half of it is 1.42 units, and
  `param_for_mm` stops rounding below 2.34. The arguments stand.)

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

- ~~**`strip_offsets` can never propose a positive offset.** `want` is
  `TARGET_GAP_MM / mm_px` = 2.88 px and `picture_start` cannot report a start
  below `GAP_MIN_MM` = 3 px, so every proposal is at most -0.010 mm. The
  detector is structurally one-sided: it can say "the frame is too far along"
  and never "not far enough". Harmless while every strip measured drifts the
  same way, and a trap the first time one does not.~~ **No longer so, checked
  2026-09-25 (audit DOC-23):** `strip_offsets` takes its starts from
  `picture_span`, whose right-edge reading can put a start below `want`.

- **`CORRECTION_DEADBAND_MM = 0.15` is defined and never read.**
  `rps7200/direct.py`, one occurrence in the repo. Left over from the one-shot
  corrector; it is not the deadband anything uses.

- **A set scanning bit can be stale, and a power cycle does not always clear
  it.** Measured 2026-09-21. After a 600 dpi roll the state byte sat at `0x9d`
  -- the vendor's own value for "scanning", per the 155 READ_STATE responses of
  the power-on capture -- with nothing running, for over an hour. It read
  **byte for byte identical after a power cycle**, position counter included,
  which is the part nobody has explained.

  Stefan said to disregard it and try. A 14-frame rewind and a full 15-frame
  walk then ran normally, and the flag went to `0x1d` the moment the session
  started. So the bit means what the capture says; what was wrong was treating
  a set bit as a reason to stop.

  `tools/check_scanner.py` now tells the two apart the only way available: a
  real pass changes something within a few seconds -- it finishes, or the
  position moves -- and a stale one does not. It reports a running scan as a
  failure and a settled one as a note.

  Still unexplained, and worth someone's attention: **the position counter
  survived a power cycle.** Either the unit does not lose that state, or the
  power cycle did not reach it. Both matter, because a rewind is computed from
  that counter.

- **The frame is about 35.3 mm across, not the 36.0 `FRAME_WIDTH_MM` assumes.**
  *Superseded 2026-09-23 -- do not lower `FRAME_WIDTH_MM` to 35.3 on the
  strength of this.* The frame-edge study measured the frame directly, on 39
  pairs of prescans of one frame with no pitch assumed: **350.6 units** (435.6
  columns, `framing.FRAME_WIDTH_UNITS`), wider than the 344.5-unit aperture and
  the opposite direction from this entry. The two readings disagree by some
  sixteen units; the later one, with 39 pairs and no pitch assumed, is the one
  the code uses, and the two clusters below remain unexplained.
  `tools/frame_edges` centres with 350.6; the in-package strip detector still
  aims on 36.0 mm, and retiring that is in *Decisions for Stefan*. The entry as
  it was:

  Measured 2026-09-21 from film already on disk, at no scanner cost. Fifteen
  frames across six separate walks show unexposed base at **both** edges, which
  gives the picture width directly: **34.62 to 35.47 mm, median 35.30**.

  Stefan found it by eye before the measurement did. Shown a frame the software
  wanted to move +1.12 mm, he said it looked good and nothing needed doing --
  and he was right: the picture actually visible there was 35.13 mm against a
  real frame of 35.30, so **0.17 mm was being lost, two pixels**, not 1.12. The
  software was measuring an assumption.

  What it costs: `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2` is 0.246
  mm and should be about 0.596, so **every proposal on every strip is biased by
  0.35 mm** -- four pixels at 300 dpi, and in the direction of correcting
  frames that do not need it. It also feeds the right-edge reading added the
  same evening, which converts "the picture ends here" into "it begins there"
  through this number.

  **Not changed yet, deliberately.** The spread is 0.85 mm where the boundary
  uncertainty is about 0.17, and the values fall in two clusters near 34.66 and
  35.35 rather than scattering about one number. That wants explaining before a
  constant this load-bearing moves -- most likely the transition column at each
  boundary being counted as picture on some frames and base on others, which
  would mean the true width is at the top of the range. `base_runs` returns
  every band, so this is re-derivable offline from the stored walks.

- **`EDGE_FRACTION` is about eight times looser than the film allows.** It lets
  a band of base begin **51 columns -- 4.3 mm** -- from the edge and still count
  as the gap that entered there. Unexposed base creeps in from one side; to see
  it starting 4.3 mm in you would have to be seeing 4.3 mm of the *previous*
  frame as well as the gap, and the whole inter-frame gap on 135 is about 2 mm.

  The allowance exists for a real thing -- a sliver of the neighbouring frame
  ahead of the gap, confirmed on four frames of walk A where 2 to 6 columns of
  darker, varying content sit in front of clean flat base. But the largest
  sliver measured is **6 columns, 0.51 mm**. Something near 1 mm would cover
  every case observed and still refuse what physics does not permit.

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
  only specification of what it is supposed to do. (*2026-09-25:* 8,857 lines
  against `direct.py`'s 4,165, and a 5,196-line test file; still no design
  doc.)

  Not documentation for its own sake: it is why the two window-only divergences
  in "Known problems" above went unnoticed. Nothing states what the window
  should do differently from the command line tools, so nothing makes a
  divergence visible as one.

- **Twenty-seven merged branches are still present locally.** Every local branch
  except `analysis/film-edge-study` is merged into `main`. Harmless in itself,
  and it is how the film-edge harness got stranded: the one branch carrying
  unmerged work is invisible in a list of twenty-eight.
  (`analysis/film-edge-study` was merged 2026-09-24, so that harness is on
  `main` now as `tools/film_edge_study.py`.)

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

**Since 2026-09-24 (2e04b01) `calibration/` is back in the first class, for a
different reason.** Every calibration now keeps its own bytes in
`calibration/<UTC time>/` -- `data.bin`, every line as read, and
`calibration.json`, every command and answer -- and those exist nowhere else: an
entry names its archive in `extra.shading_origin` but does not copy it, and
`shading.npz` is only the reduction. The cache beside them is still just a
cache. Back `calibration/` up with `library/`.

## Process

- **Orientation is unresolved.** The scanner delivers lines in transport order,
  which is upside down for viewing. The vertical flip is currently applied only
  to hand-delivered files, never in `scan()`, so the library keeps what the
  scanner sent and still matches its raw bytes. Whether the flip belongs in the
  driver depends on whether it is inherent to the transport or to how the strip
  was inserted — untested.

- **The sheet's "nudge registration between frames" tick cannot act on a
  commissioned scan.** `approved_from_sheet` emits an `Approved` for every
  ticked frame, including the ones left at zero, and `scan_roll` takes the held
  branch for any frame that has one -- so `elif correct` is never reached. It
  is a control offered for something that never happens, which is what the
  sheet's own `OPTIONS` comment says must not be done. Removing it touches
  `OPTIONS`, `_options_note`'s map, the state round trip and one test, so it is
  its own commit.
- **`approved.json` is one-way.** The window writes and reads it; the command
  line neither, using its own `held` note in the manifest that the window never
  reads. So positions set by hand cannot be handed to `scan_roll --approved`,
  and what `--approved` computed is invisible to the window.
