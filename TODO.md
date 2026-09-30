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
  doing its job, not a fault. (Bracketing was archived on 2026-09-26, with
  `--bracket`: `docs/multi-exposure/`.)
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

### Found by the demo-gui-tester, 2026-09-30

Left open when `fix/gui-tour-findings` was merged. Each was seen in the demo
window (`tools/gui_tour.py`, or a window it served), except where it says it
was read from the code.

**Wrong pictures, wrong counts, lost warnings -- worth doing first:**

- **A reopened frame can show one picture at fit and another at 1:1.** Seen
  on a roll with frames 2 and 4 commissioned, then walked again with "Yes --
  add what this walk finds to it", copied and opened. Frame 2 showed the
  re-walk's `prescan02.tif` at fit. At 1:1 it showed the old walk's library
  entry, 37 columns away and a different photograph.
  - Cause: `read_survey` (`tools/gui.py`) takes the entry from
    `approved.json`'s `reference_entry`, which a re-walk never replaces, and
    the picture from `prescanNN.tif`, which it does.
  - This is the same symptom as the aimed-prescan bug fixed on 2026-09-28.
  - Unverified: a new commission of that frame would likely be held against
    the old prescan as its reference.
- **A roll that fails part way never announces its missed positions.**
  `_report_held` runs only on "finished"; the failed path doesn't call it.
  The stray repeat after the next job, now removed, was the only place such a
  roll's misses ever showed. *Read from the code; the demo can't make a roll
  fail part way.*
- **Save all counts a deleted entry as filed.** A reopened pass keeps
  pointing at a library entry deleted since. Save all then promised that all
  55 passes would be re-corrected, and wrote 3 of them as reduced previews.
  (`on_save_all`, `filed`.)

**Wording still wrong where the 2026-09-30 fixes left off:**

- `folder_note`: "a walk of frames 1 and 1 scanned frame", where it should
  say "frame 1".
- Save all: "1 is the reduced previews on screen, because their ...".
- Delete of one folder: "what each roll has done (roll.json)".
- Export: "None of the frames in those rolls ... The roll's own frame files
  are still in its folder."
- The walk question over a one-frame sheet: "The contact sheet has frames 1
  of ...", and "the new prescans replace the old" for a single frame.
- Log lines that hedge instead of agreeing: "frame edges read on N
  frame(s)", "frame edges: N frame(s) read", "N walked frame(s) have no
  picture".
- A copy is still known by the roll name inside its manifest in three
  places:
  - Export's file names (`<roll>_frameNN_...`): a copy exported first takes
    the original's names, and the original then gets `-2`.
  - The Rolls window's find box can't find a renamed copy by the folder name
    it shows.
  - The Roll column sorts by the hidden name.

**Older wording, found in passing:**

- The Delete question overstates:
  - it says the frames "can still be exported" for a roll the browser marks
    orphaned;
  - it says "set by hand" of positions the detectors set;
  - it speaks of "the walk's prescans" for a folder with no walk.
- `folder_note` says "This walk replaces its walk ... survey.json.bak" for a
  folder with no walk.
- A reopened roll's log counts unturned frames as turned: "6 already turned"
  counts rotation 0. The sheet's "kept ... N flipped" does the same.
- The Scan chosen frames question says "infrared False".
- The "Moving the film" help has a garbled sentence ("and anything the
  calibration stops being trustworthy"). A clause was lost in 46eddc1.

**The demo, where it differs from the scanner:**

- **An abort mid-read files nothing.** The stand-in raises before any pixels
  exist, where the driver spools the lines it read as a pass tagged `failed`.
  The tour checks only that no *whole* entry comes of it.
- **Stored pictures that were already corrected** are delivered labelled
  "(raw -- correction was asked for)". Some roll frames are filed "demo: no
  calibration describes the stored picture" and export raw.

**The tour, `tools/gui_tour.py`:**

- It doesn't yet reach:
  - a re-walk added to a commissioned roll and then reopened, which is where
    the first bug above lives;
  - Save all with a pass that isn't filed;
  - "all of them already scanned" with several frames;
  - a position set by hand counted as "by you".
- Each would be a step of its own.

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

  *Updated 2026-09-28:* and to 7 (0360b0f, 2026-09-27) for which sub-frame
  SLIDEs a roll sends -- no payload changed. Nothing at 5, 6 or 7 has been
  scanned either; see *Try on the scanner first* under *Decisions for Stefan*.

  So the next scan anybody takes is the first exercise of it. Worth doing
  deliberately -- one RGB pass and one RGBI pass, filed with `RPS7200_DEBUG=1`,
  checking that the entries read `protocol_revision: 7` and that the RGB one
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
  *Decisions for Stefan* below. *Half closed 2026-09-27 (2cccdbb):* a walk that
  aims each frame no longer moves film on an `unconfirmed` reading --
  `WalkReader.judge` answers None for it, and the aim logs the note's source.
  `scan_roll --approved` still holds `unconfirmed` and `neighbours` proposals.
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

  ~~Still open: the tools say nothing of its age.~~ **Mostly closed 2026-09-27
  (0a2184d, acd1ded, 468733a):** both tools say how old a reused reference is,
  the window's Calibrate button loads one without asking only when it was
  written since the window opened, and `save_shading` leaves `shading.npz.json`
  beside the cache naming the archive it came from, so a reused reference's
  entry names its calibration too. The `.npz` itself still carries no
  timestamp or device identity.

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
  bounds the correlation~~, and `tools/scan_roll.py`'s `--nudge` and its
  registration lines ("offset ... mm", "SHORT BY ... mm") are what an operator
  reads~~. The window already says units everywhere (`protocol.say_units`). A
  stored offset re-snapped under a corrected law is the right behaviour, so the
  change is to the unit held, not to what is stored. *(2026-09-27: `--nudge`
  and the walk's registration lines are in units since 49498b1, and the probes
  take and print units since adc6c91; their JSON logs keep the driver's
  millimetre keys. What is stored is in *Decisions for Stefan*.)*

- ~~**Two of the window's dialogs promise what the code does not.** Deleting a
  roll says "the frames can be rebuilt from them" and counts only the
  `approved.json` files holding turns or flips (`on_delete_rolls`); nothing
  rebuilds a roll folder, and its manifests, prescans and hand-set positions go
  with it. Reopening one says "It will calibrate again first" (`open_roll`); a
  window that already calibrated this session does not. README says what
  happens. (Audit D15, GUI2-30, GUI1-34.)~~ **Fixed 2026-09-27 (5847728,
  cdd3fe7):** Delete names each thing that goes for good, positions counted
  too, and the reopen message says whichever of "asks for a calibration" and
  "uses the one measured" is true. One sentence of the Delete question has
  gone stale since: it says a `prescanNN-before.tif` "exists nowhere else",
  and one taken since abc3bb3 (2026-09-27) is also filed in the library,
  tagged `before`.

- **Comments and messages in the code that the docs now contradict**, left for a
  code branch because this pass changed documentation only:
  `EXPOSURE_TARGET`'s comment in `rps7200/direct.py` still cites 1.5-1.9%
  compression above 75% of scale (measured since at -0.6 to -0.8%,
  `docs/exposure-negative-plan.md`); ~~`auto_exposure` says `SET GAIN OFFSET`
  persists, where `scan_roll`'s own comments and CLAUDE.md say it does not;
  `protocol.units_for_param` says `param 1` travels 2.57 (it returns 2.84) and
  the comment above it gives the superseded `0.1662 mm` law; `plan_nudges`
  describes a param 1..8 lattice clamped at 8 (the cap is
  `MAX_CORRECTION_PARAM`, 87);~~ fourteen refusals and docstrings quote "the
  ~212 s floor" for any infrared pass, where a tied one costs ~25 s at 300 dpi
  (`DirectScanner.infrared_blind`, `tools/scan.py`, `tools/scan_roll.py`,
  `export.py`, `dng.py`, `session.py`'s header and `tools/gui.py`);
  ~~and the adjuster's shortcuts are labelled "Move the film one step" when they
  only set an offset.~~ *(2026-09-27: struck through are fixed -- a95b93d,
  c4ce418 and 7d7ebc2, comments only; the same commits corrected session_start's
  SLIDE, the 36 mm frame in framing's header and `SEARCH_MM`'s comment.)* Two
  more found since: `session.py`'s `mono_channel` comment says "Green by
  measurement" where `MONO_CHANNEL` is the average, and the window's
  Delete-roll question (above).

## Decisions for Stefan (from the 2026-09 audits)

The fixes for `audit/problems/P01`-`P32` landed 2026-09-24/25, and three more
rounds followed the second audit (`audit/second/`) from 2026-09-26 to 28,
`03aacba..ea58e98`. What is below is what their authors left alone because the
answer changes a measured conclusion, what moves film, what is stored, what
Delete means, what is sent to the device, or what the operator is shown --
merged from `audit/second/fixes3b/raw/triage.json` (`decisions`) and the `left`
lists of the `fix3b-*` and `revfix3b-*` records beside it, with the proposal as
it was made. None is started. Each says what the question is, why it is his,
what was proposed, and where in the code it lives.

### Try on the scanner first

**Nothing in these three rounds has been run on the scanner.** Every change is
tested offline, most of them on `DeviceAtCommands`, the scanner at the level
`Transport.command` speaks, and in the demo; none has met the device. Three
commits change when film moves, and `PROTOCOL_REVISION` is 7 because of them:

- **3358414 -- a hold is no longer mirrored under "reverse the direction".**
  The Transport panel's hand-move tick was handed to every roll a sheet
  commissioned, and `_hold_to_approved` negated each approved position under
  it: on a transport going the way the code assumes, every frame was driven to
  the mirror of where it was set and logged `held`. The hold now drives to the
  position as set, whatever the tick, and an inverted transport is left to the
  first move's direction check (`wrong_way`). Watch the first held roll's
  frames land where the sheet put them.
- **2cccdbb -- an unconfirmed one-member edge reading moves nothing on a
  walk.** A walk that aims each frame used to move on a `lone_gap` reading one
  detector member made alone; `WalkReader.judge` now answers None for it and the
  frame is left as it came, with the aim's log naming the reading's source.
  Expect fewer aims on a walk than before.
- **b9bee37 -- `scan_roll --approved` holds every walked frame, clamped to one
  command.** A frame the detector left unplaced is now held at 0, where the walk
  saw it, where it used to be sent nothing, and every offset is clamped to what
  one command delivers (`FINE_MAX_MM`, 88.8 units), as the sheet's snap does.
  More frames get a hold and its verification prescans.
- **`PROTOCOL_REVISION` is 7** (0360b0f): no payload changed, which SLIDEs a
  roll sends did. Nothing has been scanned at 5, 6 or 7, so the first real pass
  is the first exercise of all three. Worth one walk and one short held roll,
  with `RPS7200_DEBUG=1`, checking the entries read `protocol_revision: 7` and
  their `registration.moves_sent` against where the frames landed.

### Measurements, on the scanner or offline on the library

- **Does a stored shading reference carry an infrared channel?** README and
  CLAUDE.md say the calibration pass is RGB and the infrared plane is delivered
  uncorrected; `docs/shading-calibration-plan.md` and `docs/vignette-plan.md`
  say stored references carry `channels [0 1 2 3]`; and `apply_shading` divides
  the fourth plane whenever the reference has it. His, because only his library
  can answer, and the answer decides what NegPy, the docs and the vignette
  study's phase 2 are told. *Proposal:* read `channels` from
  `calibration/shading.npz` and a few entries' `shading.npz`, settle the docs,
  and record the channels actually divided in the shading report
  (`corrected_channels`). `rps7200/shading.py` (`TAG_TO_CHANNEL`,
  `apply_shading`), `DirectScanner.calibrate_shading`. (audit 2 DOC-01)
- **Refuse or record a partial shading correction?** `apply_shading` corrects
  only the columns the mask maps, and `scan()` refuses a pass wider than the
  reference but not one wider than the mask's used count (the trailing-columns
  entry in *Known problems*). His, because how often it happened is unknown --
  TODO says 860 against 862 has been met, the vignette re-calculation says
  `trailing` is 0 on all 198 entries -- and a refusal costs a pass.
  *Proposal:* first count, offline, the entries whose `extra.shading.columns`
  is less than their width. If none, refuse a partial correction as a
  too-narrow reference is refused (filed tagged failed); otherwise record
  `shading.partial` and have `library.corrected` say so. `rps7200/shading.py`
  (`build_width_to_loc`), `DirectScanner.scan`. (DBG-12)
- **Should a pass the device ends early fail its job, and mark the device?**
  A pass ended by "end of data" before its declared lines is now recorded
  (`lines_declared`, `short_read`) but accepted as whole. His, because whether
  lines ever arrive after that 0x20 needs a hardware observation. *Proposal:*
  check the library for `short_read` entries and how the next pass behaved;
  keep the pass, fail the job and flag the entry when `short_read`; mark the
  device suspect only if a real short read shows lines after the 0x20.
  `DirectScanner.read_planes`. (DBG-5 remainder, TP-02)
- **A READ's payload followed by a trailing CHECK, FAIL or ERROR.**
  `usb_transport` drops the payload when the final status is CHECK and returns
  it as success on FAIL or ERROR. Nothing shows the device ever does either.
  His, because the choice is made from a real roll's logs. *Proposal:* record
  the trailing status of every image READ in the command log (host-side only),
  decide from a real roll; if a trailing CHECK is ever seen, raise a
  `CheckCondition` carrying `.payload` and have `read_lines` keep the bytes
  before reading the sense. `Transport._command`, the READ branch. (DBG-11,
  TP-03)
- **What is safe with the device open and idle, and what `filing_load_test`
  should measure.** A single pass's TIFF copy is plain now (e903387); still done
  with the device open and idle are JPEG and DNG encoding of a single pass, the
  window's Save all, Export and Save As deflating full-resolution frames, a
  sha256 of every file per save and a full reindex. `tools/filing_load_test.py`
  times busy 300 dpi 8-bit passes by wall clock against an in-memory 32 MB
  gzip: no idle arm, no READ STATE check, not the load real filing makes, and a
  5% line that was chosen. His: only the hardware says what is safe, and the
  line is his. *Proposal:* add an idle arm (compress with no pass running, then
  take a pass and check READ STATE and timing); make the background load call
  `library.save` into a scratch library of realistic size; default `--dpi`,
  `--ir` and `--depth` to the roll's; record the longest gap between reads and
  NoDataYet streaks; confirm or replace the 5%. Until it says safe: write the
  window's deliveries plain while it holds the device, or have Save all and
  Export offer to close the scanner first; defer JPEG and DNG encoding of a
  single pass to close, as compaction is; make reindex incremental -- or accept
  the risk and say so in CLAUDE.md. `tools/filing_load_test.py`,
  `session.FrameWriter`, `tools/gui.py` (`_deliver_one`), `library.reindex`.
  (P13 (2)(3)(4), SR-03, CSA-07, GUI2-06, OUT-01, P30 (g), CONC-08)
- **The bracket's noise model and its one relation for three channels.**
  `merge_bracket` fits one slope and intercept on green and applies them to R,
  G and B although their dark levels differ, and weighs with the assumed
  `DEFAULT_ALPHA`/`DEFAULT_BETA`; `fit_noise_params` is never called. The merge
  now records both (bca672f). His, because it changes every merged pixel and
  wants his eye on the result. *Proposal:* `solve_relation` per channel on the
  sensor frames; alpha and beta per channel from `fit_noise_params` on
  `calibration/<time>/data.bin` or a flat pair, recorded in `meta["bracket"]`;
  compare old and new merges of the 2026-09-11 600 dpi bracket by eye.
  `rps7200/bracket.py`. (OUT-05, OUT-06, OUT-19)
- **`CHANNEL_SPREAD_TAU` is an absolute 150 DN**, so the spread gate does
  nothing on orange-masked negatives. His, as above: a merged pixel changes.
  *Proposal:* express the gate in sigma or relative to the per-channel level,
  then re-check fringing on real misregistered pairs. `rps7200/bracket.py`.
  (OUT-07)
- **`SEARCH_MM` is narrower than the largest move.** 9.0 mm is about 105 px at
  300 dpi; one `param 87` command, 88.8 units, is about 110, so a hold near the
  cap cannot be verified. Widening the search changes the fitted confidence
  scale. *Proposal:* run `tools/registration_margin.py` with `SEARCH_MM` about
  9.9 mm (116 px) and confirm `CONFIDENCE_FLOOR` = 55 still separates the
  clusters; if not, cap sheet and detector targets at about 84 units. Then
  holds at `param 80-87` on the hardware. `framing.SEARCH_MM`,
  `CONFIDENCE_FLOOR`. (FR-03, P31 (b))
- **How long one fine move takes.** The window's aim dialog uses a local 1.1 s
  per move, with no measured home. *Proposal:* measure the per-command time
  (possibly from stored command timestamps), put it beside `FORWARD_FRAME_S` in
  `rps7200/session.py`, and have the window import it. (GUI2-15)
- **Should READ STATE byte 8 ever refuse a calibration?** It was measured once,
  and CLAUDE.md says READ STATE is no substitute for asking. The tools ask now
  and record `media_loaded` in `calibration.json`. *Proposal:* keep asking;
  once several calibrations have recorded `media_loaded` beside his answer,
  decide whether byte 8 = 1 refuses unless `--film-loaded` is given.
  `DirectScanner.calibrate_shading`, `console.film_unconfirmed`. (TP-09)
- **Detect a strip swapped for one whose counter reads the same.** A scan can
  still be judged against the prescan of another strip then. *Proposal:* drop
  `_last_prescan` whenever the media flag toggles, once he confirms the flag.
  `ScanSession._last_prescan`. (SR-15 residual)
- **What the vignette study's phase 2 should measure.** `capture --ir` is
  refused up front now: the calibration pass is RGB, so there is no infrared
  reference (but see the first entry above). *Proposal:* analyse the infrared
  plane raw against its own repeat floor (drop the refusal, mark the rows
  `uncorrected`), or remove phase 2 and `PHASE2` from the tool and the docs.
  `tools/uniformity.py`. (LIB-09)
- **The vignette study now inserts the clear film twice**, since it calibrates
  first with film in and then asks for the transport to be emptied.
  *Proposal:* accept it -- the calibration reads the lower transport -- or
  reorder so the empty-transport pass comes last and the calibration uses the
  first subject loaded. `tools/uniformity.py` (`PHASE1`). (LIB-08)
- **Re-derive `EXPOSURE_TARGET`'s evidence.** `tools/exposure_headroom.py` now
  anchors on the film rather than the whole window and models blue per film
  (36ecd63), so the table the 0.80 cites was computed wrongly. *Proposal:* re-run
  it on the library, with the scanner off, before 0.80 is cited again.
  `EXPOSURE_TARGET` in `rps7200/direct.py`. (PAT-06)
- **Re-run the film-edge study.** `tools/film_edge_study.py` now takes the
  round-1 metering probes production meters, corrected, with its crop
  (6a4aa55); the conclusions in *Improvements identified but not applied*
  (Otsu) came from the old corpus. *Proposal:* re-run it on his library, report
  the prescan, roll and probe kinds separately, compute R:B on corrected probes
  only, and update that entry. (MES-01 remainder)
- **The dpi series itself.** 7200 dpi entries filed before 2026-09-13 keep the
  stagger zigzag, which `--domain raw` still compares; the default time window
  compares `10:45` against `10:45:SS` as text; rungs are not checked to be one
  frame and film. The noise floor now comes from a registered pair (93b915a)
  and demo entries are left out (b9eb6f8). Each fix can change which entries
  make up the documented series. *Proposal:* replay the realignment on those
  entries when loading raw, compare `created` as datetimes, warn when rungs
  differ in frame or film, and re-run with `--entries` naming the six ids.
  `tools/dpi_analysis.py`. (PA-14, MSQ-02/03)
- **The skill's noise shares and agreement baseline.** `noise_split` now
  gain-matches the pair and `agreement_z` judges off the rail with the sensor
  arrays (5a66180), but registration is still the caller's, and the shares the
  skill quotes (21% at 300 dpi, 27% at 1800, the -3.5% ceiling built on them)
  and the 1.03 two-repeat baseline behind "agreement holds to x1.7" were
  measured before. *Proposal:* register rows and columns
  (`rps7200.uniformity.register`), take `random = std(hp(x) - hp(y)) / sqrt(2)`,
  re-run a stored repeat pair, report ratios to the ideal 0.674, and update the
  skill. `.claude/skills/measure-scan-quality/scripts/metrics.py`. (MSQ-01,
  MSQ-04, MSQ-05)
- **Recount the vendor captures.** `tools/parse_capture.py` now consumes data
  only after the device said OK (4269538), tested on a synthetic bus only; and
  the docs disagree on the corpus -- six, seven or nine captures; 3,955, 3,987,
  6,158 or 8,133 commands; 110 or 37 SLIDEs; whether `SLIDE_PREV` and an eject
  appear at all. His, because the captures are only on his machine and they
  are the ground truth for every "the vendor never sends" rule. *Proposal:* run
  `uv run python tools/verify_capture.py` and `pytest tests/test_usbpcap.py -m
  slow` where they are, confirm the command count or explain the change, state
  the inventory once in `docs/protocol.md` section 9 and have the other docs
  cite it, and correct `docs/whole-roll-plan.md` and `retreat()`'s docstring.
  (TP-21, DOC-12, DOC-13)

### What is sent to the scanner

- **Retry a refused image READ once?** `read_planes` reads with `retries=1`, so
  one stray UNIT ATTENTION ends or abandons the pass. A retry changes what is
  sent, and how the device behaves there is unmeasured. *Proposal:* first count
  the non-NoDataYet refusals in entries' `extra.commands`; if any appear, allow
  one retry only when the CHECK came on the command status, before any data
  phase, with a readable sense other than 0x20. Bump `PROTOCOL_REVISION` to 8
  and check it once on hardware with him. `DirectScanner.read_planes`.
  (DBG-A3, P14 (6))
- **A single `--exposure-scale` also scales the infrared exposure.** "3" and
  "3 3 3" send different WRITE GAIN/OFFSET payloads: a scalar multiplies the
  device's 7745 too, which the vendor never moves. *Proposal:* treat a scalar as
  R, G and B only and pad infrared with 1.0 as the list form does, bump
  `PROTOCOL_REVISION` to 8 and say so in the window and the tools' help; or
  refuse a scalar with `--ir`. `protocol.Settings.scaled`. (TP-08, DOC-19)
- **The calibration re-reads and re-writes gain and offset on every empty
  poll**, where the vendor writes them after each successful block. It works on
  the hardware. *Proposal:* try once with him: write after each successful
  block only, read the sense when `set_gain_offset` is refused, bump the
  revision, and compare commands and duration with a capture.
  `DirectScanner.calibrate_shading`. (TP-12)
- **Keep `CLEAR_FEATURE(ENDPOINT_HALT)` automatic?** Every failed bulk read
  sends one, which the vendor never sends, into a device a pass just failed in;
  the error now says whether it cleared (834d15f, 142381a). *Proposal:* leave it
  on until a failed pass on the hardware shows whether control transfers still
  work without it; if they do, make it an explicit recovery step.
  `Transport.clear_halt`. (TP-A2)
- **Which SLIDE should the probes' `session_start` send?** It sends
  `00 01 00 00` where the vendor sends `00 01 00 04`, a sub-frame move no probe
  records; the docstring says so now (c4ce418). *Proposal:* send the vendor's
  `00 01 00 04`, or drop the SLIDE, and have each probe log the move; bump the
  revision only if a normal-operation path starts using it.
  `DirectScanner.session_start`. (TP-13)
- **The CAL-INFO prepare payload**, `95 00 00 ...` or `95 00 02 ...`, and sent
  twice. *Proposal:* run `tools/parse_capture.py` on the two calibrating
  captures, send exactly the vendor's prepare once, and bump the revision.
  `DirectScanner.calibrate_shading`. (TP-27)
- **BUSY is polled back to back on port 0x84** (`_wait_not_busy` and
  `_command`'s BUSY loop). *Proposal:* measure the vendor's polling cadence in
  the captures and match it with a sleep, as a protocol change.
  `rps7200/usb_transport.py`. (TP-20)
- **`verify_protocol`'s retyped ramps, and `MAX_DY_MM`'s literal.** Stages 15
  and 16 size their guard and restore with 1.5724/1.2423 and 1.948/1.2247, and
  `MAX_DY_MM = MAX_DY_PX * (24.3053 / 287.0)`. Changing either shifts the
  params a probe sends, or reversed-pass acceptance. *Proposal:* derive the
  stage 15/16 params from `protocol.COMMAND_UNITS` and `units_for_param` the
  next time those stages run with his agreement; leave `MAX_DY_MM`, since the
  verifier showed the hair-trigger case does not occur.
  `tools/verify_protocol.py`, `rps7200/framing.py`. (TP-23, FR-11)
- **Gate `verify_protocol`'s invented payloads behind a flag?** Stages 8a, 12,
  14 and 15 send payloads the vendor never sends; the docstring names them now.
  *Proposal:* require `--invented-payloads` for those stages and refuse before
  the device opens. (TP-22)
- **Does a change to the sub-frame law move `PROTOCOL_REVISION`?** Revision 5
  was bumped for the param cap, but `COMMAND_UNITS` 1.572 -> 1.84 (2026-09-22)
  changed the params `param_for_mm` sends without a bump, and
  the step-calibration plan (now `docs/frame-adjustment-findings.md`) said it
  would move. Each hold's moves are now
  recorded as action and param (`moves_sent`, 1e23bce), so an entry says what
  was sent whatever the law. *Proposal:* decide, write the rule beside the
  revision history in `rps7200/protocol.py`, and either bump or record the law
  in each pass's meta.
- **Should `scan_roll --approved` hold proposals nobody reviewed, and read
  `approved.json`?** It holds `unconfirmed` (one member's) and `neighbours`
  proposals with no one to look at them, and ignores the positions set by hand
  on the sheet, which only the window reads (*Process*, "approved.json is
  one-way"). What moves film on one member's word. *Proposal:* read
  `approved.json` and let the operator's entries win, as `_merge_kept` does;
  hold `unconfirmed` and `neighbours` proposals at 0 unless
  `--trust-unconfirmed`; print each frame's source before the device opens.
  `tools/scan_roll.py` (`hold_from_walk`). (FR-05, P31 (d))
- **Retire, or fence, the 36 mm frame model.** `framing.FRAME_WIDTH_MM = 36.0`,
  `TARGET_GAP_MM`, `NOMINAL_FRAME_WIDTH`, `right_gap_closure` and the
  `MAX_CORRECTION_MM` derived from them still aim every roll with no edge
  reader -- positives, Kodachrome, `DirectScanner.scan_roll` called on its own
  -- when "correct" is on, while `tools/frame_edges` centres on the measured
  350.6 units. It changes fitted, hardware-validated aiming, and tests pin both.
  *Proposal:* make `FRAME_WIDTH_UNITS` the one geometry source for the legacy
  aim point, validate with a walk of a positive strip, then remove the mm
  constants and update `tests/test_frame_position.py` and
  `tests/test_ensemble.py`. Short of that, say in the Roll question and in
  `scan_roll` that edges are not read on this film and the older model aims, or
  refuse "correct" there. (FR-04, P31 (c))
- **Budget a roll's sub-frame travel by net displacement, and on the held path
  too.** `ROLL_TRAVEL_LIMIT_MM` (12.0) sums absolute nudge travel and is enforced
  only through `StripWalk` (`correct`); a roll held to sheet positions has only
  `hold_plan`'s per-frame budget, `|target| + HOLD_HEADROOM_MM`, so a wrong large
  target buys itself more travel (*Improvements identified but not applied*).
  *Proposal:* budget the net signed displacement since the last whole-frame
  advance, re-derive the limit in units for the edge-reader path, apply it on
  the held path as well, and make `hold_plan`'s budget independent of the
  target. `rps7200/framing.py`, `DirectScanner.scan_roll`. (FR-17, DOC-15)
- **Define `MAX_FINE_STEPS` in units.** Eight commands at `param 87` chain
  about 710 units, two frames. *Proposal:* a limit of one frame width
  (`FRAME_WIDTH_UNITS`, 350.6, four commands at `param 87`), defined in units in
  `rps7200/session.py`; and, whatever the decision, the window's retyped copy
  (`tools/gui.py`) should import it now. (SR-16)
- **Should a blank frame end a walk or a roll?** A missed shot or a flat frame
  reads as the end of the film. What the transport does past the strip's end is
  unmeasured. *Proposal:* treat a blank as a skipped frame while frames remain
  to be asked for, or only where the strip continues and the counter advanced;
  file its prescan, end only after two consecutive blanks or an unseen advance,
  and report an early end prominently. `DirectScanner.scan_roll`. (FR-07)

### Storage layout

- **Which passes to file with debug filing off.** Metering probes and the
  verification prescans of a hold or an aim are filed only with
  `RPS7200_DEBUG=1`. *Proposal:* file passes that carry a `pass_role` (probes,
  verification prescans; 300 dpi, a few MB each) from the window and the tools
  whatever the switch says, and keep debug-off the default only for ad-hoc
  scripts. (P08 (2), DBG-3)
- **Where the calibration archive lives.** `calibration/<UTC>/` sits beside the
  cache, outside `library/`, so copying or moving the library loses the lines
  behind every reference, though `rps7200/library.py` calls entries
  self-contained. *Proposal:* keep each calibration once, content-addressed,
  under `library/calibrations/<sha256 of data.bin>/` (1.7 MB each), record that
  id in every entry, and have `verify` and `compact` treat it like an entry
  file; `calibration/` becomes a convenience copy.
  `DirectScanner.archive_calibration`. (DBG-6, P05 rem. 3, CLI-18, DOC-18)
- **How far to check raw bytes against the pixels at filing.**
  `raw_bytes_disagree` compares shape and layout only, and `library.save`
  checks nothing, so bytes of a same-shaped earlier pass would pass; a full
  decode at filing costs a second decode (about 570 MB at 7200 dpi RGBI) with
  the device open in the window. *Proposal (the capture fixer):* a cheap
  shape-only check in `library.save` recording `raw.disagrees` rather than
  refusing, and pixel equality left to `make reconstruct`. *(The tests fixer):*
  have `raw_bytes_disagree` decode (`decode_index`) and compare where both are
  present. (P02 (2), T-05)
- **Keep a frame's earlier takes.** `RollManifest.record` replaces a frame's
  record and a rescan writes over `frameNN.tif`, so an earlier take's entry and
  file are no longer named. *Proposal:* a per-record `takes: [{entry, filed,
  ...}]` list that `record()` appends to; keep the latest as `frameNN.tif`,
  move the earlier to `frameNN-previous.tif`, and say so in `roll.json`.
  (SR-08, OUT-09)
- **Say in `roll.json` whether `frameNN.tif` was written.** The window's
  records name no file; Export re-derives from the entry, so only the folder
  copy can be stale. *Proposal:* pass `written` into the frame's `on_filed` and
  record `file`, as `scan_roll` does, only when it was actually written.
  (PLAT-11, frame half)
- **Delete to a trash rather than for good.** `duplicates --delete` and the
  window's delete (after two questions since 474428e) remove entries for good,
  and `approved.json`'s `reference_entry` is left naming a gone id. It changes
  what Delete means for the library. *Proposal:* move deleted entries to
  `library/.trash/<UTC>/<id>/`, which `verify`, `reindex`, the roll joins and
  `reconstruct` skip, emptied by `tools/library.py empty-trash`; keep
  `reference_entry` as provenance; optionally add gain and byte14 to
  `signature()` for clearer dry-run groups. (P11 rem. 1/2, P25)
- **A deleted frame still counts as done.** `frameNN.tif` survives its entry's
  deletion and `roll.json` still calls the frame done, so a resume skips it.
  *Proposal:* name the dialog's buttons "Keep entry" / "Delete entry and its raw
  bytes" / "Cancel", and when Delete removes a roll frame's entry, write
  `entry: null, entry_deleted: <time>` into that frame's record so a resume can
  offer it again. `tools/gui.py` (`on_delete`). (T-14)
- **Locks between processes.** `compact`, `migrate-*` and
  `duplicates --delete` can run against a library a window is filing into, two
  windows overwrite each other's settings, and nothing stops
  `tools/scan_roll.py` or a second window writing a roll folder the window is
  using (its own guard is in-process only). *Proposal:* take `library/.lock` in
  `compact`, `migrate-*`, `duplicates --delete` and the window's close-time
  compaction; re-read and merge settings sections before writing; and a
  `rolls/<name>/.in-use` marker holding pid and start time, written by
  `ScanSession._roll` and `tools/scan_roll.py`, which `_roll_is_busy` refuses
  while its pid lives -- with `FrameWriter` refusing to recreate a missing roll
  folder. (P10 rem. 7, OUT-16, CONC-02)
- **Paths relative to the working directory, in the OS's own form.**
  `library/`, `calibration/` and `rolls/` resolve against the cwd, and entry
  paths, archive links and `shading_origin` are recorded that way, with
  Windows backslashes (readers now accept both). *Proposal:* resolve the
  defaults against the repo root or `RPS7200_HOME` and print them at start;
  record links as entry ids or POSIX paths relative to the library or rolls
  root, resolved on read, as Export's join already does. (CLI-27, GUI2-A2,
  PLAT-09)
- **Is a roll folder one roll, or a new strip per fresh walk?** A fresh walk
  into an existing folder replaces `survey.json` and leaves the old strip's
  `approved.json`. *Proposal:* when a fresh, not kept, walk replaces the survey,
  move `approved.json` aside as `approved.json.bak` in `_walk_ended` and log it.
  (P18 (2))
- **Keep the window's log.** Nothing persists it. *Proposal:* tee every `_say`
  line to `library/logs/<UTC start>.log`, appended and flushed per line, made on
  first write. (P24 (5))
- **Does a merged bracket belong in the library?** It is filed nowhere; its
  passes are, with the ratios, offsets and constants to redo it. *Proposal:*
  keep it out and rely on the recorded entries; or file it with
  `corrections=['bracket-merge']`, a pointer to its passes and a bracket group
  id. (OUT-08, P09 rem. 2)
- **Millimetres in stored records.** `approved.json`'s `offset_mm`,
  `Approved.offset_mm`, the registration's `*_mm` and the probes' JSON keys
  still hold millimetres, against CLAUDE.md's rule; what the tools print is in
  units now. *Proposal:* persist `offset_units` with the unit model recorded,
  keep mm only as a derived display, and migrate `approved.json`, the
  manifests, `Approved` and the hold loop in one commit with a reader for old
  `offset_mm`; in the probe logs add `*_units` twins first and drop the mm keys
  once readers have moved. Or amend CLAUDE.md to say the rule is display-only.
  (FR-06, PAT-17 remainder)
- **Debug filing for the demo.** `DemoScanner` has no spool, claim, receipt,
  settle or failure paths, so `--demo` never exercises the second library
  writer. With it, and `RPS7200_DEBUG_ROOT` winning, demo probes would be filed
  into the operator's real library. *Proposal:* bind the driver's methods as the
  demo binds `_hold_to_approved` (`_debug_capture`, `debug_claim`,
  `_debug_receipt`, `debug_settle`, `_debug_flush` and their helpers, with the
  lock and pending state), call `_debug_capture` at the end of the demo's scan
  and prescan and `_debug_flush` in `close()` -- after deciding whether the demo
  ignores `RPS7200_DEBUG_ROOT` and always spools under `DEMO_ROOT`.
  (DBG-19, LIB-19, DEMO-18)
- **Record how a reversal was judged.** *Proposal:* `_note_reversal` returns
  `reversal_detail` (the scores, margin and reason from `reversal_against`) and
  `reversal_disagreement` for passes whose own lines decided their direction,
  plus the judged prescan's `started_utc`. (RDM-01 remainder)
- **Name the debug-filed passes a frame used in its own record.** Probes and
  verification prescans now carry roll, index and move in `pass_role`, so they
  can be joined by query. *Proposal, if wanted:* have `_debug_flush` return
  `{pass_role: entry id}` and let the session's `RollManifest` append them under
  `frames[n].debug_entries` after close. (P08 (3), optional half)
- **Say which code reduced a reference.** `calibration.json` has no provenance
  or reduction parameters. *Proposal:* add `library.provenance()` and
  `{"split_ratio": 5.0}` to `archive_calibration`'s record. (RDM-03)
- **Make in-walk detector decisions re-derivable.** The record keeps neither the
  members' answers nor which frames formed the roll context. *Proposal:*
  persist `result.debug["members"]`, the (frame number, prescan sha1) list that
  forms `ctx["roll"]`, and a `DETECTOR_REVISION` constant in the correction
  note; correct the `StripWalk.observe` docstring. (FE-05)
- **Compaction while Export is reading an entry.** On Windows the session's
  close-time compaction can meet a file a Save all or Export thread holds open.
  Holding the device open until the writers finish is the wedge precursor.
  *Proposal:* (a) the session's close skips or defers `library.compact` for
  entries a window thread has open, told by the window; or (b) Export and Save
  all copy the entry's files before reading. Not: hold the device open while
  exporting. (CONC-01, second half)
- **A folder on an unmounted drive is recreated on the system disk.** Every
  writer makes missing roots with `mkdir(parents=True)`. *Proposal:* create only
  the last component, and refuse with "is the drive mounted?" when a configured
  root's parent is missing, unless an explicit option just made it. (CRA-10)
- **Windows path length.** Roll names are uncapped. *Proposal:* cap
  `session._safe` at about 64 characters, compute the longest entry, roll and
  output path before the seek, and refuse with the length and a
  `LongPathsEnabled` hint. (PLAT-03)

### Defaults

- **Should `tools/scan.py` meter by default?** `tools/scan_roll.py` and the
  window meter by default; `tools/scan.py` does not (the README's headline
  command scans at the device's own settings). *Proposal:* default to metering,
  with `--exposure-scale` or `--no-auto-exposure` to opt out; failing that, warn
  when neither was given and update the README example. (CLI-13)
- **Which resolutions count as measured.** `--dpi` and `--prescan-dpi` are
  checked for positivity and correctability only; above 7200 an OverflowError
  comes after commands were sent. *Proposal:* a `DirectScanner` constant of
  offered and measured resolutions, anything else refused before the device
  opens (at least above 7200), with an explicit `--unmeasured-dpi` override.
  (CLI-20)
- **Refuse a foreground run over 8 minutes?** Both tools now print an estimate
  and a warning (c67699d) but run anyway. *Proposal:* refuse when the slow-end
  estimate passes 8 minutes and stdin is not a terminal, unless
  `--background-ok`. (P16 (1))
- **A bracket is held in RAM**: every pass's corrected frame, raw pixels and raw
  bytes, with no estimate (4e3f534 took the merge's float64 copies off).
  *Proposal:* print the estimated peak memory beside the time estimate and
  refuse above available RAM; or spool each pass in `on_pass` as the debug spool
  does, call `scan_bracket(retain=False)`, and memory-map the spool into
  `merge_bracket` after close, checking on the hardware that a session held
  open while spooling stays well. (CLI-21, CLI-16, P29 (a))
- **`--bracket` with `--no-shading`** merges uncorrected passes and fuses each
  pass's column pattern, which `bracket.py` says must not happen. Refusing it
  moves the test harness too (`tests/test_scan_tool.py`'s `run()`). *Proposal:*
  `ap.error` for the pair in `tools/scan.py`, and the bracket tests on the
  correcting fake. (DOC-22)
- **Free space beyond rolls, and its thresholds.** Rolls check before they start
  and before each frame (63c914e); a single scan, a bracket, the window's Scan,
  compaction and the debug spool do not, and `SPACE_REFUSE` = 1 and
  `SPACE_WARN` = 2 were chosen. *Proposal:* reuse `session.frame_bytes` and
  `short_of_space` in `tools/scan.py` before the device opens (passes x 2 x
  `frame_bytes`), in the window before a Scan, in `library.compact` against the
  entry's size, and count the spool's folder while `RPS7200_DEBUG` is on
  (about 2 x `frame_bytes` a pass still spooled); adjust the thresholds if
  wanted. (CRA-01 remainder)
- **`scan_roll --no-shading --correct`.** *Proposal:* refuse `--correct` and
  `--correct-dry-run` with `--no-shading`, as unreadable prescan resolutions
  are refused. (FE-06)

### UI behaviour

- **The sheet's "nudge registration between frames" tick can never act**
  (*Process*). *Proposal:* remove it (`OPTIONS`, the `_options_note` map, the
  state round trip, one test); or give frames with no decision (source `none`,
  offset 0) no `Approved`, so the automatic nudge acts on them -- which changes
  the device path per frame. (GUI2-10)
- **Show a clamped proposal as clamped.** `snap_offset` clamps a detector
  proposal to one command without a word. *Proposal:* keep the unclamped value
  in the note, caption "+88.8 units (clamped from 120)" in the warning colour,
  and have the confirmation count and name clamped frames. (GUI2-28)
- **Snapshot a job's output settings at submit.** The session reads `out_dir`,
  `out_format` and `jpeg_quality` live at filing, so a change mid-roll applies
  to the frames left. *Proposal:* snapshot them, with rotation and flip, into
  `Roll` and `Scan` at submit, and log that a change during a roll applies from
  the next job. (GUI1-12)
- **A sheet reopened during its roll is still editable**; a turn there says it
  reaches only the next commission (f3c41b3). *Proposal:* open a commissioned
  sheet read-only while its roll runs, with a banner saying edits reach the next
  commission. (GUI2-27)
- **The manual exposure factor is shown unclamped**, where `Settings.scaled`
  clamps to 100..65535 and the window does not know the base exposure.
  *Proposal:* show the effective values after inquiry or metering, or bound the
  factor field to what the device's defaults allow. (GUI1-20 (4))
- **7200 dpi on the ladder, and the sheet's infrared box.** A roll at 7200 is
  now refused before the film moves (b665eda); the ladder still offers it, and
  the sheet's IR box is not greyed by film. CLAUDE.md prefers a refusal in the
  answers to a disabled control. *Proposal:* label 7200 "raw only -- not
  correctable", and grey the sheet's IR box by the sheet's film as
  `_sync_infrared` does in the main window. (GUI1-08, GUI1-11)
- **Say it when the computer cannot be kept awake.** The session logs it once
  per job (8ccc706). *Proposal:* show `KeepAwake.problem` in the Roll and Scan
  confirmations, beside the time estimate. (PLAT-01, UI half)
- **Closing the console on Windows, and the daemon threads.** SIGINT, SIGTERM,
  SIGHUP and SIGBREAK are deferred now; a Windows console close, logoff or
  shutdown is not, and the worker and writer are daemon threads.
  *Proposal:* a `SetConsoleCtrlHandler` hook in `DeferredInterrupt` that
  requests the stop and blocks until the pass in flight ends, within the OS's
  roughly 5 s; join the worker at exit rather than relying on daemon threads;
  and say in the Quit dialog that closing the console is covered only as far
  as the OS allows. `rps7200/console.py`. (PLAT-A1 remainder)
- **Key the window's filing-failure marks on the session's event.** The session
  now emits `unfiled` (0024015); the window still recognises failures by their
  wording (`NOT_FILED`, `COPY_NOT_WRITTEN`). *Proposal:* have `_filing_trouble`
  key on the event's `done`, and emit a structured event for the writer's copy
  problems too, then drop the regexes. (P04, session half)
- **An abstaining member on the edge light.** A member that raised is named in
  the note now (f5946d0) but the light stays green. *Proposal:* show
  "abstained" from the note in the sheet's caption or the edge light as amber
  rather than FAILED, and give `roll.summarise`'s `Summary` an `errors` tuple
  that `EdgeWatch` surfaces. (FE-01 remainder)
- **One truncated prescan refuses a whole walk** in `read_survey` and crashes
  `hold_from_walk`. Writes are atomic now, so only older files can be truncated.
  *Proposal:* skip and report a prescan `tiff.read` refuses, rather than failing
  the walk. (CONC-04 remainder)
- **A rename that changes only case is refused**, and a typed spelling of an
  existing roll on macOS is another folder to the code. *Proposal:* allow the
  rename when `target.samefile(source)`, and canonicalise folder spelling in
  `roll_dir` and `--open-roll`. (PLAT-06)
- **Export can take a name the session reserved.** *Proposal:* refuse to export
  into the session's current output folder while a job runs, or export into a
  subfolder of its own. (CONC-05)

### Tooling

- **Retire `tools/transport_probe.py`?** It is an ungated bypass with no
  reference, and its `film_bounds` reads 0.00 on a loaded strip. *Proposal:*
  delete it (`transport_truth` covers the question with a picture-based
  witness), or give it `probing.refuse_unfiled`, `ensure_reference`, `Guard` and
  `measure_shift_mm`. (PAT-14)
- **A regression check for the correction step.** *Proposal:*
  `tools/library.py recorrect`: re-run `corrected()`, compare columns and
  clipped counts with `calibration.report` (optionally a stored hash of the
  corrected pixels), and exit 1 on change. (LIB-21)
- **What `verify` says about leftovers.** Plain entries, spools left in
  `.spool` and half-written calibration archives are not reported; CLAUDE.md
  calls a plain entry complete and verifiable, and a live session's spool is in
  `.spool` too. *Proposal:* an informational section, not problems: "N entries
  still plain (compact --write)", spools with their sizes (file-spool), and
  `calibration/<stamp>/` folders holding `data.bin` without `calibration.json`;
  a cheap version at window start, logged. He chooses whether any should make
  `verify` exit non-zero. (CRA-02, part 2)
- **Can the window tests skip silently in CI?** *Proposal:* a CI step printing
  whether `import tkinter` works, `test_gui`'s `importorskip` a hard failure on
  Linux, and pytest run with `-rs`. (T-12)
- **A per-test timeout.** *Proposal:* add `pytest-timeout` as a dev dependency
  with a generous default (120 s; 600 s for `test_gui`), so a Tk hang fails
  locally instead of stalling. (T-16)
- **The remaining source-reading tests**: 98 `getsource` uses in
  `tests/test_gui.py`, 4 in `tests/test_frame_edges.py`, 1 in
  `tests/test_demo.py`. *Proposal:* continue file by file, starting with the key
  binding plumbing (`_bind_shortcuts`, rebind, `_typing`) driven with
  `event_generate`, then the sheet's painting; leave the demo attribute scan.
  (T-13)
- **The skill's column metrics assume the sensor's orientation**, while the
  delivered files it says to measure carry the operator's turn and mirror.
  *Proposal:* take `(rotation, flipped)` or an entry path and `preview.unorient`
  before any column metric, and say so in `SKILL.md`. (MES-02)
- **`film_edge_study` reads `rolls/*/prescan*.tif` as they lie**, turned or
  mirrored, `-before` files included. *Proposal:* read each roll folder's record,
  un-orient by `prescan_arrangement` as the registration studies now do, and
  give `-before` files a kind of their own. (MES-07)
- **Which of the skill's metrics accept 8-bit or float input.** `_check`
  refuses 8-bit for scale-free metrics, deliberately and tested, and takes
  floats on any scale. *Proposal:* apply the 16-bit gate and a value-range check
  in `agreement_z` only; let `dark_mask`, `colour_deviation`, `relative_noise`
  and `fixed_pattern` take 8-bit; correct `_check`'s docstring. (MES-10)
- **What a vignette-study transfer contains.** *Proposal:* copy `prescan.tif`
  where the record names it, and name in the manifest what was left out on
  purpose (the calibration archive). `tools/collect_vignette_study.py`.
  (MES-05 remainder)
- **`rps7200/defects.py` is unused**, though exported from `direct.__all__`.
  *Proposal:* move it to `research/`, or mark it experimental and drop its names
  from `__all__`; if it is ever wired in, round rather than truncate back to
  integers. (DBG-20)
- **Stand the demo in at the transport?** It stands in at the scanner, so
  `scan()`, byte-14 handling and the refusal order are borrowed piecemeal.
  *Proposal:* answer INQUIRY, READ STATE, SLIDE and the scan reads from stored
  bytes at `usb_transport`. (P27 (a))
- **The demo reads 600 dpi walks the device refuses.** With no 600 dpi entry it
  sizes by ratio (856 columns) where the device returns 860 or 862.
  *Proposal:* one table of measured widths per dpi in `framing` (428, 860, 1292,
  2584, 5172), used by `DemoScanner._fit` where the library has no entry at that
  dpi, so a 600 dpi demo walk is refused as the device's is. (FE-A1)
- **The demo's `shading=False` of a finished source** hands back corrected
  pixels, labelled so (`corrections=['shading']`). *Proposal:* leave it, or
  refuse such a pass as the driver would, if he wants. (DEMO-10 (c))
- **`library.read_raw` misses `zlib.error`**, which `collect_vignette_study`
  now catches itself. *Proposal:* add it to `read_raw`'s except tuple, so every
  caller gets None for a corrupt deflate stream.
- **`library.calibration_of`'s docstring** still says a loaded reference
  "names none". *Proposal:* "a reference loaded from a cache written before
  `shading.npz.json` existed names none".
- **`research/frame-edge/baseline_current.py` names
  `tools/gui.py::_propose_positions`**, removed in 0fd5c34. *Proposal:* leave
  it as the history it is, or add a line saying the function has gone.
- **`tools/frame_edges/propose.py` assumes `SCALE == 428`.** *Proposal:*
  assert it. (FE-09)
- **CLAUDE.md wording that is his to change.** It says `1_nothing_done.tif` is
  "the raw decode (`library.load`)", where it is the decode as filed and
  `reconstruct` is the check on the decode; and "base is known by its colour
  ratio (R/G 2.1-2.3, B/G ~0.53)", which the detector that moves film does not
  use as an absolute. *Proposal:* "`1_` is the stored decode"; reword the base
  line to what the members use. (Triage "Docs", FE-07; the README and TODO
  parts of that item are done.)

### Closed since the first audit's list

- ~~**Can one detector member move film?**~~ **Half closed 2026-09-27
  (2cccdbb):** a walk that aims no longer moves on an `unconfirmed` reading.
  The `--approved` half is above, under *What is sent to the scanner*.
- ~~**Before any resolution joins `frame_edges.READ_AT_DPI`**~~ **Fixed
  2026-09-28 (3755c4c):** `centring` decides a multiple-width pass at the
  detector's own scale, tested at 428 and 856 columns.
- ~~**The command-line tools calibrate without asking** (P17 remainder).~~
  **Fixed 2026-09-27 (3848243, 51e1cb8):** `tools/scan.py` and
  `tools/scan_roll.py` ask at a terminal and refuse where nobody can answer
  unless `--film-loaded`; `tools/uniformity.py capture` asks for film and
  calibrates through `ensure_shading` before metering; byte 8 is logged and
  kept as `media_loaded` in `calibration.json`.
- ~~**Continuing a reopened roll that has no walk** (P25).~~ **Fixed 2026-09-27
  (213f455, 2975bcd):** the Roll button skips the frames `roll.json` calls done
  within the range, and the resumed roll keeps what it wants.
- ~~**Save the sheet's decisions on every change** (P22).~~ **Fixed 2026-09-27
  (4021fa3):** every change books one write 1.5 s later, under the walk it
  belongs to (6d6c4ae).
- ~~**`--demo` with an explicit `--rolls`, `--library` or `--settings`.**~~
  **Fixed 2026-09-27/28 (20f2237, 66ac66a):** `--library`, `--rolls` and
  `--reference` outside `demo/` are refused at launch. `--settings` is still
  taken as given.
- ~~**Should the demo refuse a calibration under `--look-only`?**~~ **Done
  2026-09-27 (685ff39):** it refuses through `_need_film`, as every pass and move
  does, and the refusal arrives as a failed job; loading a cache still works.
- ~~**Should `verify` report demo entries' missing reference?**~~ **Settled in
  code 2026-09-24 (4b7ad6d):** a demo entry built from a stored prescan or a
  test card is a finished picture with no calibration, and `verify` leaves it
  out, as it does a scan taken raw on purpose.
- ~~**`tools/exposure_headroom.py` models every film's blue as colour
  negative's.**~~ **Fixed 2026-09-27 (36ecd63):** the anchor is read inside the
  film over the corrected columns, blue is modelled with the film's own divisor
  (an unmeasured film at the worst it allows), and entries it cannot study are
  skipped. Re-running it for the 0.80 is above.
- ~~**Two registration tools validate differently from the driver**
  (PA-08).~~ **Fixed 2026-09-27 (db185c0, ecbaa46):** `registration_margin`
  scores each pair both ways up, as `measure_shift_mm` does, and
  `transport_truth` takes its reach and floor from `framing`.
- ~~**`tools/exposure_probe.py` reads delivered levels from a region
  re-detected on the metered pass** (PA-11).~~ **Fixed 2026-09-28 (eaeb58f):**
  `last_metering` records the region every round read, and the probe reads
  every rung over it.

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

  *Updated 2026-09-28:* nor have the three rounds after the second audit
  (2026-09-26 to 28, 03aacba..ea58e98). `PROTOCOL_REVISION` went to 7 for the
  sub-frame SLIDEs a roll sends; the rest sends what it sent, but stops sooner
  or waits longer in places a scanner will notice: a SCAN with no answer marks
  the device suspect, a calibration ends only on "no more lines" and installs
  nothing unless every channel split into dark and lit, an empty READ STATE
  before a pass is polled past, a READ that returns short is a refused read,
  a frame move counts only once the counter leaves where it was, a move of
  several frames is one command a frame, the bulk read and a mid-payload pause
  take the pass's own patience, the calibration read gives up only once silent,
  the host is kept awake while a pass reads, and the tools refuse to calibrate
  until told the film is in. What changes when film moves is listed at the top
  of *Decisions for Stefan*.

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
  (*corrected 2026-09-25*; ~~the reopening dialog still says "It will calibrate
  again first"~~ -- it says which since cdd3fe7, 2026-09-27). Not inheriting
  the roll's old reference is a choice, not a limit -- `load_shading` and
  `--reuse` exist, and every library entry keeps the reference it would be
  corrected with. But a reference
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

  **It cost the multi-exposure study too, the same way.** The nine-pass 3600
  dpi bracket walked 2.4 lines and 0.6 columns from first pass to last, and
  because the ladder is shot in ascending order the drift read as an exposure
  effect -- "passes stop agreeing as the bracket widens". Sub-pixel
  registration was built for it and works; with it, merging passes gains
  2-7% shadow noise, which is not visible, so the whole study was archived in
  `docs/multi-exposure/` (code, tests, analysis scripts). What a rigid shift
  leaves is still worth knowing if anything ever compares passes pixel by pixel
  again: the top and bottom bands of that bracket sit -0.25 and +0.17 lines off
  after registering, and a x4 repeat pair -0.31 at the bottom.

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
  overlap is the one exception left. *Updated 2026-09-27 (290c8f0, e903387):*
  a roll's last frame, and anything the writer starts once no roll is running,
  is filed plain too, and a single pass's TIFF copy in the output folder is
  written plain; a JPEG delivery and its DNG are still encoded with the device
  open and idle, which is in *Decisions for Stefan*.

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
  the correction below. 0.80 is also `CLIP_START`, the knee above which a
  sample is not trusted as linear. See `EXPOSURE_TARGET` and `CLIP_START` in
  `rps7200/direct.py`.

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
  path is genuinely unguarded. *(2026-09-28: still so; `scan_roll --approved`
  now holds every walked frame, each clamped to one command (b9bee37). Whether
  to budget net displacement instead is in *Decisions for Stefan*.)*

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

- ~~**`CORRECTION_DEADBAND_MM = 0.15` is defined and never read.**
  `rps7200/direct.py`, one occurrence in the repo. Left over from the one-shot
  corrector; it is not the deadband anything uses.~~ **Deleted 2026-09-27
  (887879c)**, with framing's unused second move law (`command_for`,
  `describe_command`) and the gap-band detector nothing called.

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
re-measure (*2026-09-28: 3-4 minutes, as `ensure_shading` says; 22 s was the
data alone*), and every library entry already keeps its own `shading.npz` beside
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
cache. Back `calibration/` up with `library/`. *(2026-09-27: a failed
calibration's lines are archived too, `verify` checks every archive the entries
name, `tools/library.py calibrations` reduces each again with today's code, and
the cache's `shading.npz.json` names its archive. Whether the archive should move
into the library is in *Decisions for Stefan*.)*

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
  its own commit. (*2026-09-28:* the sheet's header no longer claims the nudge
  covers anything (cdd3fe7); removing or wiring the tick is in *Decisions for
  Stefan*.)
- **`approved.json` is one-way.** The window writes and reads it; the command
  line neither, using its own `held` note in the manifest that the window never
  reads. So positions set by hand cannot be handed to `scan_roll --approved`,
  and what `--approved` computed is invisible to the window. (*2026-09-28:*
  still so, and in *Decisions for Stefan*. `--approved` now reads the walk
  through `session.read_manifest`, scans as the walk's film, and holds every
  frame, clamped to one command -- 3f0f2e2, 8d83f26, b9bee37.)
