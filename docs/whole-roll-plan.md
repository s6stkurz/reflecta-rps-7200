# Scan a whole roll

## Context

The driver scanned one frame per invocation: 36 runs for a roll, 36 shading calibrations
at 3–4 minutes each, and someone at the scanner to move the film by hand.

`DirectScanner.slide()` existed and `scan(advance=True)` called it, but the payload was
guessed. `pieusb` was no help — it declines to implement this transport at all
(`option.py:236`: *"option 'advance' is not implemented yet and will be ignored"*), and
the SANE C backend gates film advance on `FLAG_SLIDE_TRANSPORT`, which is `0x00` for model
`0x31`. Neither has ever moved film on this scanner.

`captures/600_ICE_FILM_STRIP_5.pcapng` settles it. It is CyberView scanning a 5-frame
strip end to end: 17 scan cycles, 21 `SLIDE` commands, four film advances.

## How the captures were read

USBPcap, decoded from the control stream rather than by any dissector. The Genesys Logic
bridge carries SCSI over vendor control transfers: port `0x0085` takes the 6-byte CDB and
then any data-out, one byte per transfer; `0x0084` returns the status; `0x0082` announces
a bulk length; `0x0088`/`0x0087` carry the IEEE1284 daisy sequence, whose `0xe0` marks the
start of each command. Reassembling port `0x0085` between successive `0xe0`s gives the
command stream. Note the payload byte lives in `usb.data_fragment`, not `usb.capdata`.

Bulk byte counts in these captures are **not** reliable — USBPcap truncates at 65535, so
any read larger than that is clipped. Command bytes and timings are exact.

## The advance

```
CDB   d1 00 00 00 04 00        (SCSI_SLIDE, 4 bytes of data)
data  04 01 00 01
```

Every `SLIDE` payload in all six captures:

| capture | payloads |
|---|---|
| `600_ICE_FILM_STRIP_5` | `10 15 00 00` ×15, **`04 01 00 01` ×3**, **`04 01 00 02` ×1**, `00 46 00 00`, `01 57 00 03` |
| `frist_open` | `10 13 00 00` ×3, `10 01 00 00`, `00 01 00 04`, `01 46 00 00`, `00 4c 00 01` |
| `300_900_1800_ICE` | `10 15 00 00` ×4 |
| `300_3600` | `10 15 00 00` ×3 |
| `scan2` | `10 14 00 00` |
| `Scan` | `10 16 00 00` |

`04 …` appears nowhere except the four advances. This driver used to send `04 16 00 00` —
a zero in byte 3 where every observed advance carried a 1.

Byte 1 (`param`) varies by session across `0x01`, `0x13`, `0x14`, `0x15`, `0x16` with no
visible consequence; ours sends `0x16` for `SLIDE_INIT` and scans work, so it is left
alone. That also keeps `PROTOCOL_REVISION` at 1: the conversation *inside* a scan is
unchanged, so roll frames stay comparable with every entry already in the library.

## The confirmation

`READ_STATE` byte 2 is the transport position, and it is the only signal in any capture
that says the film actually moved.

| | |
|---|---|
| `600_ICE_FILM_STRIP_5` | `0 → 1 → 2 → 3 → 4`, stepping once per `SLIDE 04 …` |
| `frist_open` | `2` throughout — that session never advanced |

Settling took 1.6 s, 6.2 s, 4.7 s and 1.7 s. The `READ_STATE` issued immediately after
each advance returned **no payload at all**, so the poll has to survive a failed read
rather than treat it as the end.

Distinct states seen (13 bytes), by first appearance:

```
00 00 00 03 1e 00 04 00 00 00 00 02 00     at open, before the lamp settles
00 00 00 00 1e 00 0d 00 00 00 00 02 00     idle, position 0
00 00 00 00 1e 00 8d 00 00 00 00 08 02     armed for a scan
00 00 01 00 1e 00 0d 00 00 00 00 02 00     idle, position 1
...
00 00 04 00 1e 00 b5 00 00 00 00 08 02     position 4
```

Byte 8, which this driver reports as `busy`, is `0` in every state ever observed. Bit
`0x40` of byte 6, which it reports as `media_loaded`, is never set — including with film
demonstrably loaded. Neither is usable. Byte 2 is.

## The per-frame cycle

From the strip capture, per picture:

```
prescan   300 dpi  RGB  8-bit  frame (0,0)-(10343,6887)     16 s
prescan   300 dpi  RGB  8-bit  frame (0,1)-(10343,6888)     16 s
scan      600 dpi  RGBI 16-bit frame (96,71)-(10175,6815)  216 s
SLIDE 04 01 00 01                                          ~7 s
```

The two prescans differ only by a one-line `y` offset and byte 14 of MODE (`0x21` vs
`0x20`); nothing suggests the second carries information the first does not, so this
driver runs one.

Mode quality is `0x0008` — *reuse the calibration already held* — on all 17 passes. The
session recalibrates nothing, and reused a reference acquired in an *earlier* session,
which is what proves the reference is film-independent. One `calibrate_shading()` covers a
whole roll.

## Timing

Full-resolution scan time barely depends on resolution. The carriage traverse dominates.

| pass | vendor | ours |
|---|---|---|
| 600 dpi RGBI 16-bit | 216 s | — |
| 900 dpi RGBI 16-bit | 218 s | 227 s |
| 1800 dpi RGBI 16-bit | 218 s | 227 s |
| 3600 dpi RGBI 16-bit | 217 s | 334 s |
| 900 / 1800 dpi RGB 8-bit | 16 s | 39 s / 70 s |

The fourth channel is what costs: RGB at 900 dpi is 39 s against RGBI's 227 s. So a roll at
3600 dpi costs about what one at 600 dpi costs, and a 300 dpi RGB prescan is cheap enough
to run before every frame.

## Registration

The transport window is 36.5 mm (10344 units at 7200 dpi) and a 35 mm frame is 36 mm.
Half a millimetre of slack. CyberView's own detected windows across its 5-frame strip:

| picture | window |
|---|---|
| 1 | `96,71 → 10175,6815` |
| 2 | `96,71 → 10175,6815` |
| 3 | `96,71 → 10151,6791` |
| 4 | `96,71 → 10199,6815` |
| 5 | `1727,71 → 10199,6791` |

By the fifth the picture had slid 1631 units — 6.1 mm — into the aperture, and the frame
was scanned with 6 mm missing. Registration has to be checked before each full-window
scan. It cannot be seen directly — the prescan covers only the aperture, so a picture
hanging outside it is not there to be found. What shows instead is a picture *narrower*
than a whole frame: 8472 units against 10079. `registration()` reports that shortfall, and
a signed offset, from every prescan.

**Drift is reported, not corrected.** Nothing in six captures moves the film by less than a
whole frame, and `SET_SCAN_HEAD` (`0xD2`) is never sent by anything. Correcting it would
mean inventing a command.

## Measured: the first roll on this driver

`--dry-run` over a 6-picture strip, 2.4 min. **The transport works.** Six advances, the
position stepping `0 → 1 → 2 → 3 → 4 → 5`, each confirmed within the first 0.5 s poll. The
first frame took 63 s including settling, every one after it 12 s.

Incidental, and unchanged from before: `cmd_17` reports a condition on every frame and is
continued through; `get_gain_offset` is refused once per frame with `key=0x05 code=0x26
qual=0x80` and succeeds on the retry; `start_scan` needed two retries on the first frame
only (`calibration disable not granted`, then `aborted command`).

### And a detector that was wrong

The registration numbers from that run were nonsense, and the shape of the nonsense is
what gave it away:

| picture | detected x | shortfall |
|---|---|---|
| 1 | 314..10319 | 0.26 mm |
| 2 | 338..10319 | 0.35 mm |
| 3 | 290..8893 | 5.21 mm |
| 4 | 314..9280 | 3.93 mm |
| 5 | 0..10319 | 0.00 mm |
| 6 | 217..290 | **35.30 mm** |

Drift is cumulative. It cannot go 5.2 → 3.9 → **0.0** → 35.3. The non-monotonicity said
detector, not film, before anything was opened.

Confirmed by looking: picture 6's prescan is a whole frame filling the window edge to edge,
with a narrow empty strip at the left. Its column-variance profile:

```
columns  0-8    std ~2      empty aperture, uniform
columns  9-12   std 36-59   the film edge, skewed a few px across the sensor
columns 13+     std 0.7-10  the photograph, dark and nearly flat
```

`detect_frame` thresholds at 25% of the peak. The peak is the edge, so the cut lands at
14.8 and **four columns of 428** survive — the four border columns. The photograph is
discarded and the frame reported as 35 mm of drift.

Level does not have that failure mode, because it does not depend on picture content:

| region | R | G | B |
|---|---|---|---|
| empty aperture | 142.7 | 152.9 | 153.1 |
| film | 34.0 | 14.9 | 6.7 |

A 5–20× step in every channel, and the orange mask plainly visible in the film row. Any
threshold between 60% and 90% of the clear level returns the same edges. `film_bounds()`
uses it; picture 6 now reads `x266..10319`, 0.10 mm short — which is what it is.

The lesson is the one `column_defect_sigma` already carries in this file: a threshold set
as a fraction of the maximum is set by its worst outlier.

## Measured: the transport reverses

`SLIDE 05 01 00 01` (`SLIDE_PREV`) moves the film **back** one frame. Verified five times
in a row walking a strip from position 5 to position 0: the position counter decremented
each time, and a prescan at each stop matched the contrast recorded for that picture on the
way down.

| step | position | contrast | expected |
|---|---|---|---|
| 1 | 5 → 4 | 0.2364 | 0.2357 (picture 5) |
| 2 | 4 → 3 | 0.3135 | 0.3122 (picture 4) |
| 3 | 3 → 2 | 0.3511 | 0.3448 (picture 3) |
| 4 | 2 → 1 | 0.3183 | 0.3176 (picture 2) |
| 5 | 1 → 0 | 0.2531 | 0.2522 (picture 1) |

This appears in **no capture** — the constant came from `pieusb` and had never been sent to
this scanner. It means a strip can be re-run without handling the film.

It also confirmed the registration fix across all six frames, not just the one that was
looked at: with `film_bounds` every picture reads `x0..10343`, **shortfall 0.00 mm**. There
was never any drift on this strip.

## Measured: metering is RGB, and infrared is never metered

Exposures written before each pass of the strip capture, decoded from the `WRITE GAIN
OFFSET` payloads (R/G/B at bytes 0-5, infrared at 18-19, the infrared flag at byte 16):

| pass | R | G | B | IR | ir flag |
|---|---|---|---|---|---|
| 300 dpi RGB 8-bit | 23840 | 35749 | 65151 | **7745** | 0 |
| 300 dpi RGB 8-bit | 25744 | 38614 | 4872 | **7745** | 0 |
| 600 dpi RGBI 16-bit | 29868 | 43850 | 12015 | **7745** | 1 |

Every metering pass in all 17 is 300 dpi **RGB** 8-bit — never RGBI — and the infrared
exposure is **7745 in every pass in the capture**, prescans and scans alike. Only R, G and
B move. This scanner's own power-on baseline reads `9604-6506-6506-7745`: 7745 is the
device default and CyberView never touches it.

(B swinging 65151 → 4872 between passes is the 16-bit timer wrapping, which
`multi-exposure-plan.md` already documents.)

`auto_exposure` already did all of this, and reading it carelessly cost a roll. Its
`infrared` parameter does **not** choose the probe — the docstring says so outright:
"Always probes in RGB, never in infrared … `infrared` therefore does not change how the
probe is taken." The flag says the *scan* will be RGBI, and what it buys is blue's
headroom: blue returns 2-3.7x brighter in RGBI than in RGB at the same exposure, so
`aims[2]` and `targets[2]` are divided by `infrared_blue_headroom` (4.0).

Passing `infrared=False` from `scan_roll` therefore saved nothing — the probe was already
three-channel — and silently removed that headroom. Measured on the roll it broke: metering
returned `[2.671, 5.729, 10.073]`, and 6506 x 10.073 is 65535 exactly, the 16-bit timer
pinned at its ceiling *before* the RGBI gain is applied. Blue clips and is unrecoverable.

Confirmed on the delivered file, not on a recomputation of it — `rolls/strip6/frame01.tif`,
1800 dpi RGBI:

| channel | max | mean | at the 65535 ceiling |
|---|---|---|---|
| R | 48212 | 20584 | 0.00% |
| G | 50713 | 22238 | 0.00% |
| B | **65535** | **41380** | **22.17%** |
| I | 41324 | 37410 | 0.00% |

Nearly a quarter of the blue channel is blown, and blue's mean is double red's. Nothing
else on the frame is near its ceiling, which is what rules out a general over-exposure and
points at the one channel whose headroom was removed.

The lesson is narrow and worth keeping: a parameter named for a mode is not necessarily a
switch for that mode. `test_infrared_is_never_metered` now asserts both halves — the probe
stays three-channel, *and* an RGBI roll is metered as one.

Checked across every capture, not just the strip: **36 of 36 `WRITE GAIN/OFFSET` payloads
send infrared exposure 7745**, and **36 of 36 `READ GAIN/OFFSET` responses report 7745**.
Never another value, in any session, at any resolution, with or without infrared enabled.

### And the read is a reference, not a readback

That check turned up something larger. `READ GAIN/OFFSET` returns `9604, 6506, 6506, 7745`
on every one of those 36 reads — including immediately after CyberView has written
`25642, 37864, 1475`. Diffing the full 123-byte responses across the strip session, the
**only bytes that ever differ are 66, 67 and 68**: the live R/G/B offsets, which drift by
one or two counts. Every exposure and gain field is constant, and no offset anywhere in the
response ever holds a written exposure.

So the command hands back a fixed reference plus live offset measurements. The exposure
currently in force is not readable at all.

Two things follow.

*Exposure cannot compound.* `Settings.scaled()` is applied to `get_gain_offset()`, which is
always the same reference, so a scan always runs at `base x scale` however many scans
preceded it. An earlier reading of this file claimed `scan(auto_exposure=True)` applied its
scales twice, at `base x scales²`, and "fixed" it by restoring the base afterwards. That bug
does not exist on this hardware and the change has been reverted. It looked real only
because `FakeScanner` in `tests/test_metering.py` returns whatever was last written —
modelling a device that echoes, which this one does not.

*The roll should still not depend on it.* `scan_roll` writes the reference back before each
frame anyway; it costs one command and is correct under both models.
`test_exposure_does_not_compound_across_a_roll` runs against a fake that echoes and one
that does not, so the assumption is exercised rather than assumed.

Note this contradicts a comment in `scan()`, which says an RGB probe cannot predict an
RGBI scan because "the channels, blue especially, behave differently with and without
infrared". The capture says the vendor does precisely that. The mechanism is plain once the
payloads are decoded: infrared is a *separate exposure field* that is not being metered at
all, so there is nothing for the probe to mispredict. `scan(auto_exposure=True)` still
probes in RGBI and has not been changed.

## Measured: `full_17_strip`, a whole roll driven by CyberView

2171 commands, 17 frames, captured 2026-09-03 against this timeline: scanner power-cycled,
CyberView opened, strip inserted, **frames aligned by hand with the scanner's own
Forward/Reverse keys**, one manual prescan, then the automatic whole-roll prescan, then
close.

### The advance carries two values, and we only ever send one

```
CyberView, 16 advances:  [1, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 2, 2, 2, 1]   8 ones, 8 twos
older 5-frame capture:   [1, 1, 2, 1]                                        3 ones, 1 two
this driver, every time:  1
```

`SLIDE 04 01 00 <value>` takes a 1 or a 2 in the last byte. Both step the position counter
by exactly one frame — that was measured, and it is why an earlier reading dismissed the
byte as meaningless. What it evidently changes is *how far* the film moves.

Eight and eight over sixteen advances is not noise, and the runs — one, then five twos, then
six ones, then three twos, then one — are not a fixed alternation either. A fixed dither for
a fractional pitch would come out regular (1,2,1,2 or 1,1,2,1,1,2). Clumping like this is
what a **closed loop** looks like: measure where the frame landed in the prescan, pick the
next advance to correct it, repeat.

`advance()` hardcodes `value=1`. If 1 and 2 are two different step counts, always sending
the same one applies a constant per-frame error — which is exactly the monotonic ~0.2 mm per
advance measured on strip3 (gap 1 → 10 → 36 px). **This is the prime suspect for the drift,
and it is testable with a command already sent thousands of times.**

The experiment, using nothing new: from a known position, advance with `value=1`, prescan,
measure the frame's position; advance with `value=2`, prescan, measure again. If the two
displacements differ, the difference is the correction step, and a loop over the gap
detector can hold registration across a whole roll.

### The buttons are firmware-only — this avenue is closed

Between the strip going in (43 s) and the manual prescan (109 s), Stefan aligned the frames
with the scanner's Forward and Reverse keys. In that entire window the bus carried **nothing
but polling**: 117 `READ_STATE`, 5 `TEST_UNIT_READY`, no other command, and the position
counter never left 0.

The host neither sees the keys nor can trigger them. Every earlier search — the SANE
backend's 26 commands, six other captures, the published literature — pointed the same way;
this settles it. Fine positioning is not exposed over USB, and the vernier the keys perform
has no command behind it.

### Rewind and eject, both first sightings

```
05 01 00 01  x16   SLIDE_PREV, stepping 16 -> 0 one frame at a time, to return the strip
03 f6 dd 00  x1    action 0x03, after the rewind -- the eject
```

`SLIDE_PREV` appears in **no other capture**; this driver had already verified it on hardware
before the vendor was ever seen using it. Its role is end-of-roll rewind, not correction.
Action `0x03` is new, seen once, and is the last command of the session.

### Smaller findings

- `SET_SCAN_HEAD` (0xD2) is sent **zero** times across a full automatic 17-frame roll.
  Nothing has ever driven it. See the hazard note in CLAUDE.md.
- The position counter read **72** before the strip was inserted and reset to 0 on insertion:
  it is absolute and survives power cycles, so it says nothing about where a frame is.
- `SLIDE_INIT` is `10 16 00 00` throughout, the value this driver already sends.
- The session-start pair is `00 01 00 04` and `00 46 00 00`, with `01 47 00 03` before the
  first frame — the same shape as the earlier captures, one param byte apart (`47` vs `57`).
- Every frame gets two full-window 300 dpi RGB prescans, `0,0 -> 10343,6887` and
  `0,1 -> 10343,6888`, before its advance. Unchanged from the earlier captures.

## Measured: seven slides, and the bound that makes the measure honest

A metered 300 dpi pass over slides 1-7, film edge located by the **orange mask**
rather than by brightness: the mask lives in the base, so film reads R/B ~ 1.2-1.3
while an empty aperture reads ~1.0. That is content-independent, unlike every
brightness threshold tried before it, all of which mistook a bright picture region
for a gap at least once.

The bound is arithmetic, not taste. The aperture is 10344 units = **36.49 mm** and
a 35 mm frame is **36.00 mm**, so the film has **0.49 mm** of room. Drift is the
film creeping through that slack. A reading larger than it would mean a visible
slice of the picture was missing, so a detector reporting several millimetres is
reporting picture content and must be rejected rather than believed.

| slide | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| edge (mm) | 0.00 | 0.51 | *5.63* | *6.57* | 0.17 | 0.26 | 0.43 |
| | ok | ok | **rejected** | **rejected** | ok | ok | ok |

Accepted readings span 0.00-0.51 mm -- the whole slack -- and move `+0.51, -0.34,
+0.09, +0.17` between frames. **Bouncing, not accumulating. Over seven slides there
is no drift.**

### What this overturns

The strip3 figures that started this -- an inter-frame gap walking 1 -> 10 -> 36 px,
read as ~0.2 mm per advance -- do not survive. Those steps are 9 then 26, which is
not the linear growth a constant per-frame error produces, and they were taken with
a brightness detector of the kind since shown to fire on picture content. Three
independent measurements now say the transport holds registration:

- round trips repeat to **±0.03 mm** (`NEXT` then `PREV`, n=6), so backlash is
  negligible
- `SLIDE_NEXT` value 1 vs value 2 differ by **less than 0.05 mm**, so the byte the
  vendor alternates is not a vernier and this driver hardcoding 1 costs nothing
- seven slides stay inside **0.49 mm** with no trend

CyberView running 17 frames unattended, sending no correction command of any kind,
fits the same picture.

### The lesson worth keeping

Every detector written for this in one afternoon -- variance-thresholded, level
thresholded, level-and-flatness, mask-ratio -- reported a confident number that was
wrong at least once, and each was corrected only by looking at the frame. What
finally made the measure trustworthy was not a better threshold but a *physical
bound*: knowing that the answer cannot exceed 0.49 mm turns an unbounded number
into one that can be checked. Bound a measurement by what the hardware allows
before tuning what it detects.

## Settled: there is no drift, and the roll mechanism works

A full unattended pass over a 17-slide strip: 16 advances, every one clean, stopping
correctly when the transport would not move. Same length as the roll in
`full_17_strip.pcapng`. Stefan judged the frames by eye and found the registration
sound.

That closes the drift investigation, and it closes it against my own repeated
claims. Four detectors were written for it in one day:

| detector | keyed on | failed by |
|---|---|---|
| `detect_frame` | column variance vs peak | firing on the film's own skewed edge |
| `film_bounds` | level vs empty aperture | blind mid-strip, where film always fills the window |
| level + flatness | brightness and uniformity | firing on a bright picture region |
| mask ratio | orange mask, R/B | frames with a cyan subject reading as "no film at all" |

Every one produced confident numbers. Every one was wrong on some frames, and every
one was caught only by looking at the picture. On the final roll eight of seventeen
readings were junk, including two claiming the window held no film on frames of
ordinary contrast.

The common failure is the same each time: keying on something that varies with the
photograph. A defect at a fixed sensor column can be separated from picture content
with the library, across different film positions -- that is what CLAUDE.md already
says. Registration cannot, because it *is* a property of the picture's position, and
no single frame distinguishes "the film moved" from "this photograph is dark on the
left".

The strip3 figures that started all of this -- a gap walking 1 -> 10 -> 36 px, read
as 0.2 mm per advance -- were one of those artefacts. Their steps were 9 then 26,
not the linear growth a constant per-frame error gives, and three later measurements
disagree: round trips repeat to +/-0.03 mm, `SLIDE_NEXT` value 1 against value 2
differ by under 0.05 mm, and seventeen slides came out sound by eye.

**Do not add drift correction.** There is nothing to correct, the vendor sends no
correction command in 3,955 commands across seven captures, the scanner's own
Forward/Reverse keys are firmware-only and invisible to the host, and every
automatic measure of registration built so far has been wrong often enough to do
more harm than good.

## Open — what `tools/transport_probe.py` answers

Run it on a strip you do not mind handling; it sends commands this scanner has never been
given. Each probe is prescan → command → prescan, and the shift in where the picture sits
is what the command was worth.

| payload | question | result |
|---|---|---|
| `04 01 00 01` | what is one advance worth, in mm? | *unmeasured* |
| `04 01 00 01` | does the step repeat? | *unmeasured* |
| `04 01 00 02` | is byte 3 a step count? (byte 2 still moved by one when the vendor sent this) | *unmeasured* |
| `04 01 00 00` | does byte 3 = 0, as this driver used to send, move anything? | *unmeasured* |
| `05 01 00 01` | does `SLIDE_PREV` go backwards at all? | **yes — one frame per step, verified 5x** |

If none of them moves the film by less than a frame, registration stays reportable only,
and `scan_roll` should keep saying so rather than pretending to fix it.

## Two exposure defects this exposed

Both survivable on one frame, neither over thirty-six.

**Metering applied twice.** `auto_exposure()` leaves the device *metered*, at
`base × scales` — a contract `tests/test_metering.py` pins deliberately. `scan()` then did
`get_gain_offset().scaled(exposure_scale)`, applying the same scales to the already-metered
device, so the pass ran at `base × scales²`. `scan()` now restores the base first.

**Exposure persisting between frames.** `SET GAIN OFFSET` persists on the device, so frame
*N*'s exposure is frame *N+1*'s starting point. `scan_roll` reads the settings once at the
start and writes them back before each frame's metering, so a roll cannot walk steadily
brighter.

**Shading on an 8-bit prescan.** `prescan()` asked for shading correction on an 8-bit pass,
with a reference measured in 16-bit units — subtracting its dark half drives every pixel to
zero. Nothing called `prescan()` before, so it had never bitten. It is off there now.
