# Byte 14 of MODE SELECT: does it change the line rate?

## Status 2026-09-23: reopened, and answered by reading each pass

The condition below for reopening this ("something automated depending on
orientation") has been met twice.

- **The hold loop.** It compared each frame's prescan against a reference and
  refused the ones that came back reversed, until it learned to read both ways
  up.
- **`session._note_reversal`.** It blamed the *scan* whenever a reversed
  prescan disagreed with it, and would have shipped correct frames upside
  down.

### The mechanism, corrected

The trigger stated further down is wrong. It is not "a bit-0-set pass after a
bit-0-set pass". **A pass's bit 0 decides whether the carriage waits at the
far end after it; the next pass then reads bottom-up, whatever its own bit.**

- **The ladder** fits both readings; it never sent a bit-0-clear pass straight
  after a forward bit-0-set one.
- **CyberView's captures** do. Its roll is 0x21 / 0x20 prescan pairs, and
  every 0x20 pass came back reversed. It knows in advance: READ STATE byte 6
  bit 7 and byte 11 were set before all 32 reversed passes and clear before
  all 48 others (`docs/protocol.md` §7). It lowers the scan frame's y0 by one
  line on exactly those passes.
- **This driver's RGBI rolls:** 38 of 114 frame prescans came back reversed.
  The RGB-only rolls had none.
  - It was always the first prescan after an RGBI scan, and often every other
    frame. CyberView's 600 dpi capture alternates the same way.
  - Why the carriage sometimes goes home between passes is not known. The
    advance does not reliably do it: the same `SLIDE 04 01 00 01` is followed
    by both directions.
- **What that means for a roll:**
  - Nothing is sent after a roll's last frame, so the carriage waits wherever
    the last RGBI pass left it. The next pass can come back reversed: a single
    prescan, a walk's first frame, or a second roll's first frame.
  - Calibration also moves the carriage. In all three vendor sessions, the
    first pass after it came back reversed.

### What is true of every reversed pass

- **Only rows reverse; columns never do.** Checked by picture on CyberView's
  own pairs: 0.72-0.98 with rows reversed, never with columns reversed. So
  the transport's direction, frame edges and holds are unaffected, and the
  edge detector reads a reversed prescan identically (77 prescans, 0.00
  columns).
- **The colour planes stay aligned.** R, G and B are 0 rows apart, as on a
  forward pass, so turning the whole picture is the whole correction.
- **The line tags say so.** A top-down pass starts with R and ends with B; a
  bottom-up one starts with B and ends with R. This agrees with every known
  case in the library and on the vendor pairs.
- **Residual:** turned back, a reversed pass sits 3 rows and 1 column off a
  forward pass of the same frame, at 600 dpi (measured twice, identical). It
  is not corrected: it is far below the smallest move, and correcting it
  would invent rows at one edge.

### What was done (option A)

- `rps7200/direction.py` reads each pass's direction from its line tags.
  `DirectScanner.decode_index` turns a bottom-up pass upright, so every path
  from bytes to pixels delivers it upright: the driver, the demo, `reconstruct`
  and the tools.
- Every library entry records `scan.read_direction`, and `prescan.read_direction`
  for a frame's stored prescan. It also records `carriage_state`, the READ
  STATE bytes before the pass, as evidence only.
- `tools/library.py migrate-direction` brings older entries up to date: 4
  scans and 38 stored prescans turned upright, and every other entry recorded.
- `_note_reversal` never turns a pass whose own lines said which way it was
  read.
- The demo's carriage reads bottom-up the way the scanner does, so the path is
  exercised without a device.
- Nothing sent to the device changed; `PROTOCOL_REVISION` did not move.

Still open:

- whether READ STATE's bits hold for this driver's own passes (the carriage
  record will show);
- the offset at resolutions other than 600 dpi;
- a reversed 7200 dpi pass, which has never been seen, so its stagger
  realignment after turning is reasoned, not measured.

---

## Status: run 2026-09-11. The question asked was answered "no" — and the
## ladder found something the question never anticipated.

**The upper nibble has no timing effect.** 0x10, 0x20 and 0x30, at fixed
exposure, fixed frame, 600 dpi: mean ms/line ratios 1.000 / 1.000 / 1.001. The
2.8x spread in the captures was not this. Struck from `docs/protocol.md` §4
below.

**What actually moves is bit 0 -- and it is not a speed control, it is
bidirectional scanning, and it comes with a hazard this driver has never
known about.**

A pass sent with bit 0 set (0x11/0x21/0x31) comes back **with its rows in
reverse order** -- but only when it immediately follows another bit-0-set
pass. The first such pass in a run is normal; the second is reversed; a third
in a row was not tested, but the pattern held identically across all three
values tried (0x11, 0x21, 0x31), each showing exactly this: first rep normal,
second rep reversed. All six bit0=0 passes, including two full pairs and a
final drift check, were normal without exception.

Confirmed as a genuine row reversal, not a decode artefact: flipping the
second pass of each affected pair recovers correlation with its sibling from
~0.51 (chance) to ~0.96-0.98, in all three visible channels independently
(R 0.954, G 0.957, B 0.960 on one pair), and the raw tag order itself carries
the signature -- the trilinear CCD's three physical rows lead and trail in a
fixed order (R first, B last) on every normal pass and the *opposite* order
(B first, R last) on every reversed one. Nothing in `READ_STATE`, the MODE
SELECT acknowledgement, or `GET PARAMETERS` says which happened.

**The physical picture this fits**: byte 14 bit 0 = 0 forces the carriage to
re-home to the top before scanning, always producing a normal top-to-bottom
read at the cost of the return trip. Bit 0 = 1 permits scanning from wherever
the carriage currently sits without re-homing -- free, if the carriage is
already at the far end from the previous pass, which is exactly bidirectional
scanning. The data comes back in whatever order it was physically captured,
which is reversed when the carriage was already at the bottom. This is what
`docs/protocol.md` already suspected from the vendor captures -- "bit 0
alternates on every pass in lockstep with the scan frame's y0 shifting by one
line" -- except that note called it something CyberView *does*, not something
the byte *causes*. It causes it. CyberView's own y0 shift is presumably
compensating for the small offset between where a forward and a reverse read
actually start.

**Why this matters beyond the ladder**: `set_mode`'s default byte 14 is
`0x21` for every RGBI scan -- bit 0 set, unconditionally, and has been since
this driver's first version. Ordinary use has not been hitting the reversed
case by accident, not by design: `scan_roll` always prescans in RGB (bit 0
clear) immediately before the RGBI capture, and `auto_exposure` always probes
in RGB first, so the RGBI pass is normally the *first* bit-0-set command
since the last reset and comes back normal. But nothing enforces that. Two
consecutive RGBI scans with nothing bit-0-clear between them -- a manual
`scan(infrared=True)` called twice, a retry after a failure that skips the
prescan, anything not yet imagined -- would silently deliver a reversed
frame, with nothing in the file or the metadata to say so.

**It has already happened once, for real.** A library-wide check for the
tag-lead signature -- cheap, and needs no scanner -- flags
`library/20260828T012327Z_unknown-film_1800dpi_ir` (tags: clean, rgbi,
shading-test; 2026-08-28) alongside the three passes from today's ladder that
are known-reversed by construction. Its siblings from the same session
(`010052Z`, `011439Z`) do not carry the signature. No same-content sibling
survives to confirm it by image correlation the way today's ladder passes
can, so this rests on the tag-lead signature alone -- but that signature is a
property of the physical sensor geometry, not of picture content, and it
disagreed with every one of the six intentionally-normal passes and agreed
with every one of the three intentionally-reversed ones in today's run
without exception.

**Nothing has been changed in the driver.** The default byte 14 is
unchanged; the hazard was already live before today, this only found it.

**Closed 2026-09-13 as won't-fix, by Stefan's decision.** A reversed pass is
obvious on sight and flipping it back is trivial, so it does not justify a
protocol change. Both candidate fixes cost something real and permanent --
forcing bit 0 clear surrenders the free bidirectional speed on every RGBI
pass, and detecting the tag-lead signature adds a decode-time guess to every
scan -- to save a flip. The candidates below are kept as a record of what was
considered, not as a plan.

The one thing that would reopen it: something *automated* depending on
orientation. Roll framing and `gap_edges` read where the picture sits, and a
human flipping a delivered file is not the same as a reversed pass going
through registration. It has not bitten, and `scan_roll`/`auto_exposure`
avoid the trigger today by the shape of the code rather than by design.

`tools/byte14_probe.py` is the tool that found this and can reproduce it; it
also leaves every pass filed, so the run above is fully re-analysable
offline.

---

Written down before the hardware was touched so the scanner time would be
spent on a decided question, in the original form below.

## The question

`docs/protocol.md` §4 has called byte 14 unexplained since the reading that it
selects the channel count was refuted. It still is — but it is no longer
uncorrelated.

Thirteen 3600 dpi RGB scans in the library, all sending `byte14 = 0x10`, fit

    ms/line = 2.60 + 4.851e-4 x sum(exposure)        r^2 = 1.0000

Exactly one of the vendor's three single-stock sessions falls on that line, and
it is the one that sends the same byte 14:

| capture | byte 14 | sum(exposure) | predicted | measured | ratio |
|---|---|---|---|---|---|
| `slide.pcapng` | `0x10` | 22616 | 13.6 ms | 14.0 ms | **1.04** |
| `bw.pcapng` | `0x30` | 143522 | 72.2 ms | 43.0 ms | **0.60** |
| `300_3600 - Kopie` | `0x20` | 73089 | 38.1 ms | 64.4 ms | **1.69** |

All three are 3600 dpi, RGB, 16-bit, within 0.4% on geometry. The black and
white pass carries the **largest** exposure of the three and runs faster than
the negative, which is what rules exposure out as the explanation on its own.

**0.60 against 1.69 is a factor of 2.8 in how long a scan takes.** At 3600 dpi
that is minutes a frame. One pass per value, so it is a correlation and not a
result — which is the reason to measure it rather than to act on it.

## Why this is safe to drive

`set_mode` already takes a `byte14` override. It was added for exactly this and
nothing in normal operation passes it:

> ``byte14`` overrides the last meaningful byte, whose default below is known to
> be wrong -- see the comment there. It exists so the byte can be driven
> directly and measured; nothing in normal operation passes it.

It is a field in a payload this driver sends before every single scan, not a
mechanism. The failure mode is a pass that comes back wrong-looking, which costs
one pass. Unlike `SET_SCAN_HEAD`, nothing moves.

Every value on the ladder below appears in a capture. Nothing is invented.

## The design

About ten passes at 600 dpi, ~5 minutes, on one frame with the transport
untouched.

- **600 dpi, RGB, fixed exposure throughout.** Byte 14 is the only variable.
  600 dpi is short enough that ten passes is minutes, long enough that a 2.8x
  difference in line rate is unmistakable.
- **The ladder: `0x10, 0x20, 0x30, 0x11, 0x21, 0x31`** — every value the seven
  captures contain. `0x10` first and last, so drift over the run is visible
  rather than mistaken for an effect.
- **Two repeats at each value**, which is what `noise_split` needs. That is not
  optional here; see below.
- **`RPS7200_DEBUG=1`**, so every pass is filed with its raw bytes and the whole
  thing is re-analysable without a second run.

### Guards

1. **Abort if a pass fails to return an image**, rather than trying the next
   value. An unknown field that breaks a read is one to stop on.
2. **Abort on a sense condition** that is not the one `cmd_17` routinely
   queues.
3. **Restore the default** — `0x21` for RGBI, `0x10` for RGB — at the end and
   on every exit path.
4. **Nothing above `0x31`.** The captures contain no larger value and there is
   no reason to invent one.

### What decides it

Both halves, and the second is the one that matters.

**Time.** ms/line at each value against the `0x10` baseline, from the library's
own `duration_s` and `height`. A ratio near 1.00 at every value means the byte
does nothing to timing and the correlation above was three coincidences. Ratios
near 0.60 and 1.69 confirm the captures.

**Noise.** A value that is *faster* is presumably faster for a reason — fewer
samples per line, less settling time. `noise_split` on the repeat pairs at each
value, from the `measure-scan-quality` skill, separates the random part from the
grain and says what the speed cost.

The verdict is a table of *time against noise per unit signal*, and only a value
that buys time for nothing is worth having. A byte that halves the pass and
doubles the noise is an exposure control by another name, and this scanner
already has one.

That is precisely the trap `docs/analog-gain-plan.md` walked into: the gain
register looked like free brightness and turned out to be a digital multiplier,
worth 0.5%. The same test settles this one, and it is the reason the repeats are
in the ladder rather than being added later.

## What actually followed

None of the three anticipated outcomes fit. The upper nibble did nothing
(struck above). Bit 0 is not "a faster value" in the sense the plan meant --
it is a different scan mode with a correctness hazard, not a speed/noise
trade to weigh. What follows is now a design question rather than a
measurement one:

- **Confirm the flagged real entry** — read it back, look at it, decide
  whether `20260828T012327Z_unknown-film_1800dpi_ir` should be marked or
  removed. It is tagged `shading-test`, not delivered work, which limits the
  damage but does not erase the question of what else might carry the same
  signature undetected in scans the tag check has not been run against yet
  (the check above covers every entry with raw bytes; nothing outside the
  library was checked, because nothing outside it can be).
- **Decide on a defence.** Candidates, not a decision: (a) check the tag-lead
  signature after every scan and flip automatically if it disagrees with the
  expected order, cheap and specific to this hazard; (b) force `byte14`'s bit
  0 clear on every RGBI scan, unconditionally, giving up whatever speed
  bidirectional scanning was buying and never seeing the case at all; (c)
  leave it, now that it is documented and `scan_roll`'s own structure already
  avoids it in the common path. Whichever is chosen changes what the device
  is sent, so `PROTOCOL_REVISION` in `rps7200/direct.py` moves with it and
  `tools/library.py reconstruct` has to be re-run.
- **Given the mechanism, more of the ladder is not obviously worth running.**
  A third consecutive bit-0-set pass, or trying this at a resolution other
  than 600, would sharpen the picture but the core finding -- reversal,
  triggered by consecutive bit-0-set passes, undetectable from the response
  -- does not need a bigger sample to be true. The open questions now are
  about response, not about measuring more.
