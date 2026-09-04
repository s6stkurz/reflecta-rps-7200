# The wire protocol, as CyberView actually speaks it

Everything here is decoded from `captures/*.pcapng` — seven sessions of the vendor
software driving a Reflecta RPS 7200, 8,133 SCSI commands in total. Counts in this
document are across all seven captures unless a section says otherwise. Where
something is inferred rather than observed, it says so.

This is a description of the conversation, not of this driver. Where the driver
differs, that is noted, because those are the places bugs live.

## 1. Transport

SCSI over USB, but not bulk-only transport. Commands and their data go out through
**vendor control transfers**, and only the payload comes back over bulk-in.

| direction | mechanism | detail |
|---|---|---|
| command out | control, `bmRequestType=0x40`, `bRequest=12` | `wValue=0x0088`, one byte at a time, prefixed by `0xE0` |
| data out | control, same | `wValue=0x0085` |
| data in | bulk IN, endpoint `0x81` | max packet 512 |
| status | control | polled until the device reports good |

A command is six bytes: `opcode 00 00 <size hi> <size lo> 00`. The size is the
length of the data phase, big-endian, in bytes 3–4.

## 2. The command set

Every opcode the vendor ever sends. Nothing outside this list appears in any capture.

| op | name | count | notes |
|---|---|---|---|
| `0x00` | TEST UNIT READY | 759 | the polling heartbeat |
| `0x03` | REQUEST SENSE | 73 | fetched after a check condition |
| `0x08` | READ | 2765 | image data, CCD mask, calibration |
| `0x0A` | WRITE | 570 | carries a **sub-command**, see §3 |
| `0x0F` | PARAM | 71 | read back width / lines / bytes-per-line |
| `0x12` | INQUIRY | 2 | once per session |
| `0x15` | MODE SELECT | 71 | resolution, channels, depth, direction |
| `0x18` | COPY | 71 | precedes each scan; payload is constant `70` bytes |
| `0x1B` | SCAN | 71 | starts the pass |
| `0xD1` | SLIDE | 110 | the film transport, see §5 |
| `0xD7` | READ GAIN/OFFSET | 79 | see §6 — it is a *reference*, not a readback |
| `0xDC` | WRITE GAIN/OFFSET | 79 | exposure, gain, offset, light |
| `0xDD` | READ STATE | 1435 | 13 bytes; byte 2 is the transport position |
| `0xE7` | vendor | 2 | **not supported by this model** — see §11 |

**`0xD2` SET SCAN HEAD is never sent. Not once.** It exists in the SANE `pieusb`
backend and is a documented hazard here — see CLAUDE.md.

## 3. WRITE sub-commands (`0x0A`)

The first two bytes of a WRITE payload select a sub-command.

| sub | name | count | payload |
|---|---|---|---|
| `0x12` | SET SCAN FRAME | 71 | `12 00 0a 00 <index> <x0> <y0> <x1> <y1>`, all 16-bit LE; index is `0x80` |
| `0x13` | SET EXPOSURE TIME | 213 | `13 00 04 00 <channel mask> <value>`; always value 100 |
| `0x14` | SET HIGHLIGHT/SHADOW | 213 | `14 00 04 00 <channel mask> <value>`; always value 100 |
| `0x17` | CMD 17 | 71 | `17 00 02 00 01 00`; **must follow the scan frame** or the device refuses to skip shading analysis |
| `0x95` | CAL INFO, prepare read | 2 | `0x15 \| 0x80` — the high bit means "prepare to read" |

Exposure and highlight/shadow are written once per channel (masks `0x02`, `0x04`,
`0x08`) before every pass, always with the value 100. They are never used to adjust
anything.

## 4. MODE SELECT (`0x15`) — including scan direction

Sixteen bytes:

```
byte  1     0x0f            length - 1
bytes 2-3   resolution      16-bit LE dpi
byte  4     passes          0x80 = RGB one pass, 0x90 = RGBI one pass
byte  5     depth           0x04 = 8-bit, 0x20 = 16-bit
byte  6     colour format
byte  8     byte order      Intel
bytes 9-10  quality flags   skip-shading, calibrate, sharpen, fast-infrared
byte  12    halftone
byte  13    line threshold
byte  14    see below
```

**Byte 14 does not control the scan direction.** It was read that way here, from a
correlation, and driving the byte on the hardware refutes it — see §11 for the
measurements. What the captures show is real: across the 17-frame roll bit 0
alternates on every pass in lockstep with the scan frame's `y0` shifting by one
line, 35 times without exception. But that pairing is something CyberView *does*,
not something the byte *causes*.

Observed pairings across all seven captures:

| passes | depth | byte 14 | count |
|---|---|---|---|
| `0x80` RGB | 8-bit | `0x10` | 4 |
| `0x80` RGB | 8-bit | `0x11` | 3 |
| `0x80` RGB | 8-bit | `0x20` | 28 |
| `0x80` RGB | 8-bit | `0x21` | 26 |
| `0x80` RGB | 16-bit | `0x20` | 1 |
| `0x90` RGBI | 16-bit | `0x21` | 9 |

The whole byte is unexplained. This driver sets it as
`0x21 if passes == ONE_PASS_RGBI else 0x10`, on the belief that it selects the
channel count, which the table refutes — RGB passes carry `0x21` twenty-six times.
`set_mode` takes a `byte14` override so it can be driven directly; the default is
left alone because nothing measured yet says what it should be.

## 5. SLIDE (`0xD1`) — the film transport

Four bytes: `action param 00 value`. Every payload ever observed:

| payload | count | meaning |
|---|---|---|
| `10 16 00 00` | 37 | INIT — sent before every scan |
| `10 15 00 00` | 22 | INIT, different param |
| `10 13/14/01 00 00` | 6 | INIT, other params; no observed difference |
| `04 01 00 01` | 11 | advance one frame |
| `04 01 00 02` | 9 | advance one frame — the value differs, the movement does not exceed 0.05 mm |
| `05 01 00 01` | 16 | **reverse** one frame; used only to rewind a finished roll |
| `03 f6 dd 00` | 1 | eject, the last command of a session |
| `00 46 00 00`, `00 4c 00 01`, `00 01 00 04` | 4 | **sub-frame movement**; `00 01 00 04` = +0.32 mm, §11 |
| `01 46 00 00`, `01 47 00 03` | 2 | **sub-frame movement**; `01 47 00 03` = −7.47 mm, §11 |

Actions `0x00` and `0x01` occupy the mechanism for 1.5–3 s without changing the
position counter. They are the only remaining candidates for sub-frame movement,
and they are unidentified.

The scanner's physical **Forward/Reverse keys produce no USB traffic at all**. In
`full_17_strip` the window in which they were pressed carries 117 `READ_STATE` polls
and nothing else; in `frist_open` five separate key operations — two long forward
passes, a long reverse, and two rounds of small corrections — appear as **zero**
transport commands in the whole session.

They are not, however, invisible in their *effect*: the scanner updates its own
position counter, and `frist_open` reads position 2 throughout, which is the third
picture the keys had been used to reach. So the host can see **where** the film ended
up, but can neither observe nor command the movement. Fine positioning is not exposed
to the host.

## 6. Gain and offset (`0xD7` read, `0xDC` write)

**`READ GAIN/OFFSET` is a fixed reference, not a readback.** Across 36 responses in
one session it returned `9604, 6506, 6506` for R/G/B every time, including
immediately after `25642, 37864, 1475` had been written. Diffing the full 123-byte
responses, only bytes 66–68 — the live R/G/B offsets — ever change.

Consequences: scaling it always yields `base x scale`, so exposure cannot compound
across scans; and the exposure currently in force is not readable anywhere.

The write payload is 29 bytes:

```
bytes 0-5    R, G, B exposure      16-bit LE each
bytes 6-8    R, G, B offset
bytes 12-14  R, G, B gain
byte  15     light
byte  16     1 when infrared is enabled
bytes 18-19  infrared exposure
byte  20     infrared offset
byte  22     infrared gain
byte  27     1 when infrared is enabled
```

The infrared exposure is **constant within a session** — 7745 across all 36 writes
and 36 reads of one session — and the host never writes a value it did not read. It
does vary across power cycles: this scanner reported 5791 in a later session.

## 7. READ STATE (`0xDD`)

Thirteen bytes. Across 737 responses only these vary:

| byte | values | meaning |
|---|---|---|
| 2 | 0–16 | **transport position**, absolute, survives power cycles |
| 3 | 0, 3 | |
| 4 | 0, 30 | |
| 6 | `0x0d, 0x15, 0x1d, 0x35, 0x8d, 0x95, 0x9d` | status flags |
| 7 | 0, 23 | |
| 11 | 1, 2, 8, 115 | |
| 12 | 0, 2, 32 | |

Byte 8 is listed as constant above because it is 0 in all 737 capture responses —
but every capture had film loaded. **Measured on an empty transport it reads 1**,
which the captures could not have shown. See §11.

**Nothing reports position within a frame.** There is no field a host loop could
read to close a registration loop, which is consistent with the vendor not
attempting one.

## 8. Session structure

What CyberView does, in order.

```
INQUIRY
vendor 0xE7
SLIDE 00 01 00 04          unidentified
READ STATE                 position resets to 0 when a strip is inserted
  [operator aligns the film with the scanner's own keys -- no USB traffic]
SET SCAN FRAME + MODE 3600 dpi + SCAN        one overview pass
SLIDE 00 46 00 00          unidentified
SET SCAN FRAME + MODE 600 dpi + SCAN         two preview passes
SLIDE 01 47 00 03          unidentified
```

then, per frame, twice:

```
WRITE GAIN/OFFSET          exposure for this pass
SET SCAN FRAME             full window, y0 alternating 0 / 1
CMD 17                     must come after the frame
MODE SELECT                300 dpi RGB 8-bit, byte 14 bit 0 alternating
COPY                       constant payload
SLIDE 10 16 00 00          INIT
SCAN
READ x N                   the image
PARAM                      width / lines / bytes-per-line
```

then `SLIDE 04 01 00 01` or `04 01 00 02` to advance, and `READ STATE` polled until
byte 2 changes. At the end of the roll, `SLIDE 05 01 00 01` once per frame to rewind
to position 0, then `SLIDE 03 f6 dd 00` to eject.

Measured on the 17-frame roll: **two passes per frame, ~42 s per frame**, and the
advance value alternates in runs — one, then five `2`s, then six `1`s, then three
`2`s, then one `1`.

## 9. The captures, and what each one is

Every capture was made deliberately, with Stefan writing down what he did. Those
notes are reproduced here and each is checked against what the file actually
contains, because a capture whose contents are only remembered is a capture that
can be misread later.

| capture | cmds | what he said he did | what the file contains | agrees |
|---|---|---|---|---|
| `Scan.pcapng` | 43 | *(no note)* | one 300 dpi RGB 8-bit pass | — |
| `scan2.pcapng` | 312 | "so i have made a scan2" | one 300 dpi **RGBI** 16-bit pass | — |
| `300_900_1800_ICE` | 877 | "300 prescan, 900 scan and 1800 scan, this time ICE enabled" | 300 RGB x2, then 900 RGBI, then 1800 RGBI | yes |
| `300_3600 - Kopie` | 331 | "a 3600 dpi scan. But i dont know if its IR or not" | 300 RGB x2, then 3600 **RGB** 16-bit | **not infrared** |
| `600_ICE_FILM_STRIP_5` | 1828 | five-picture strip, 600 dpi with ICE | 600 RGB x2 at start, then per frame 300 RGB x2 + 600 RGBI; positions 0-4 | yes |
| `frist_open` | 596 | 14-step startup list, below | 3600 RGB, 600 RGB x2, 300 RGB x2, 600 RGBI; position 2 throughout | yes |
| `full_17_strip` | 2171 | 11-step timeline, below | 3600 RGB, 600 RGB x2, then 300 RGB x2 per frame x17; positions 0-16 | yes |

### `frist_open` — the startup, and what the buttons do

> 1. Scanner is on. 2. Scanner is shut off. 3. Scanner is shut on. 4. Film strip is
> inserted. 5. Waiting for scanner to be ready. 6. Long forward pass to picture
> number 2. 7. Long forward pass to picture number 3. 8. Long backwards pass to
> picture number 2. 9. Small correction forwards. 10. Some backward corrections.
> 11. As I seen most things above are not given to the pc. 12. Open cyber view.
> 13. Make a prescan at 300 dpi, **it also knows that I am on dia 3 not 2 or 1**.
> 14. Make a real scan at 600 dpi with ice.

Steps 6-10 are all done with the scanner's own keys, and the capture contains **no
transport command whatsoever** — zero `SLIDE` moves in 596 commands. His step 11 was
right.

But step 13 is the important one, and it refines §5. `READ STATE` byte 2 reads **2**
for the entire session, and 2 zero-indexed is the third picture — exactly the "dia 3"
CyberView displayed. So although the keys send nothing over USB, **the scanner
updates its own position counter when they are used, and the host can read it.**
The keys are invisible; their effect is not.

### `full_17_strip` — the automatic roll

> Scanner was on / put out / put on again / wait a bit / open cyberview / put strip
> in / **changed the position of the strip with the buttons so that they overfine
> perfectly** / made a prescan / started the automatic prescan of the whole roll /
> it does everything automatically / close cyberview

Confirmed: the alignment window carries only polling (117 `READ STATE`, 5
`TEST UNIT READY`), the position counter reset from a stale 72 to 0 when the strip
went in, and the roll then ran 17 frames unattended.

## 10. Two behaviours worth stating outright

**CyberView never makes an infrared prescan.** Across all seven captures, every
prescan is RGB 8-bit; infrared appears only in the final scan. A four-channel pass
carries a ~212 s floor whatever the resolution, so probing in it would cost a full
scan's time per round. This is why metering here stays in RGB.

**The 3600 dpi pass at session start is CyberView's own, not the user's.** It appears
in `frist_open` and `full_17_strip`, both taken right after a power cycle, at 8-bit,
followed by two 600 dpi RGB previews. The 3600 dpi pass in `300_3600 - Kopie` is a
different thing — 16-bit, and the scan that was actually asked for. Depth tells them
apart.

## 11. Driven on the hardware

Claims here were produced by sending the command, not by reading a capture.
`tools/verify_protocol.py` runs these; raw output lands in `probe/`.

### `0xE7` is an invalid opcode on this scanner — *measured*

Sent as the vendor sends it, size 4, it is refused:

```
70 00 05 00 00 00 00 06 00 00 00 00 20 00
      ^^                            ^^
   key 0x05 ILLEGAL REQUEST      ASC 0x20 INVALID COMMAND OPERATION CODE
```

The vendor gets the same answer. In both captures containing it, `0xE7` is followed
immediately by `0x03` REQUEST SENSE — the thing you do after a CHECK CONDITION.
CyberView issues it, is told the opcode does not exist, reads the sense and
continues. It is presumably valid on another model in the family.

This closes it as a lead: it is not what enables calibration, because the device
never executes it.

### `READ STATE` byte 8 is the media flag — *measured, confirmed*

With an **empty transport** it reads **1**. Across all 737 capture responses, every
one with film loaded, it reads **0**. The captures could not have revealed this
because the transport was never empty in one.

This matters because `State.media_loaded` currently reads **byte 6** and its own
docstring concedes the result is untrustworthy — "the bit is clear throughout the
vendor's power-on capture, and has read clear here with film demonstrably loaded, so
a False proves nothing". If byte 8 goes to 0 with film in, it is the flag that was
wanted, and `require_media` could stop being advisory.

**Confirmed with film loaded: byte 8 reads 0.** One variable changed, a strip going
in, and the byte went 1 → 0. In the same reading byte 6 held `0x1d`, which the old
test calls "no film", with a strip demonstrably in the transport — that is the
unreliability its docstring described, in a single measurement.

`State.media_loaded` now reads byte 8, inverted, and `State.no_media` exposes it
directly.

### Sense is one-shot — *measured, and a trap*

Reading it clears the condition. `s.sense()` followed by `s.read_sense()` returns
`key=0x00 code=0x00` for the second call, which decodes as "no sense" and looks like
a device that reported nothing. Parse the bytes from the first read; never ask twice.

### The vertical flip is real, and nothing I can send controls it — *measured*

Scans come back in one of **two orientations, mirrored from each other**: matched
as-is they correlate about **-0.63**, mirrored about **+0.95**. There is no
ambiguity about the two groups existing.

What does *not* determine which one you get:

| tried | result |
|---|---|
| byte 14 bit 0 — `0x20` vs `0x21`, same frame | **identical**, +0.9985 as-is |
| byte 14 all four values `0x10 0x11 0x20 0x21` | split B,A,B,A — but see below |
| `SLIDE INIT` param `0x01, 0x13, 0x14, 0x15, 0x16` | `0x01` differed, the rest agreed |
| six consecutive scans, every parameter identical | **all six the same orientation** |

The last row is what rules the others out. Those six passes used `byte14=0x10` with
`slide_init_param=0x16` — the exact combination that produced the *opposite*
orientation in the byte-14 stage minutes earlier. Same bytes, same frame, same
session length, different answer.

So the flip is **stateful, not commanded**. It varies between runs with identical
parameters, which is also why it appears sporadically in real scans rather than on
alternate frames. The cause is not identified.

The practical consequence: **a scan's orientation cannot be relied upon**. Anything
that cares has to detect it from the image or let the operator correct it. It cannot
be fixed by asking for it.

### `SLIDE` actions `0x00` and `0x01` move the film sub-frame — *measured*

**This is the fine positioning that every other source said did not exist.** The SANE
`pieusb` backend's `slide_action` enum has only `NEXT`, `PREV`, `INIT` and `RELOAD`;
its `sane-pieusb(5)` page exposes two options, neither positional; the scanner's own
Forward/Reverse keys send nothing over USB. All of that is still true. What is also
true is that CyberView sends five further `SLIDE` payloads at session start, and
**all five move the film along the strip**.

Driven on the hardware, each preceded and followed by a 300 dpi prescan:

| payload | busy | frame counter | x | y |
|---|---|---|---|---|
| `00 46 00 00` | 1.1 s | unchanged | −246.80 px | −0.01 px |
| `00 4c 00 01` | 1.1 s | unchanged | +92.86 px | +0.06 px |
| `00 01 00 04` | 1.1 s | unchanged | +3.94 px | +0.01 px |
| `01 46 00 00` | 1.1 s | unchanged | −84.98 px | −0.06 px |
| `01 47 00 03` | 1.1 s | unchanged | −82.04 px | −0.02 px |

Movement is **purely along x**, the axis of the strip — `y` never exceeds 0.06 px in
any of the five. The **frame counter never changes**, which is what makes these
sub-frame: `SLIDE_NEXT` and `SLIDE_PREV` always move it, these never do. Each holds
the mechanism for 1.1 s, matching the 1.5-3 s the captures show.

#### The two that were characterised

Single-shot numbers in a 428-column window cannot be trusted once a shift is a large
fraction of the width — the correlation peak wraps. So the two most useful payloads
were sent five times each, every shift measured against the prescan immediately
before it, keeping each measurement small.

**`00 01 00 04` — fine forward, +0.32 mm**

```
n   busy  pos    dx px    dx mm
1    1.1    4    +0.47   +0.040
2    1.1    4    +2.18   +0.186
3    1.1    4    +4.01   +0.342
4    1.1    4    +3.83   +0.326
5    1.1    4    +3.32   +0.283
```

Always forward, `y` exactly 0.00 throughout. The first two steps falling short and
then settling is **backlash**: the preceding command had moved the film the other
way, so the first steps take up the slack before the film follows. Steps 3-5 are the
figure — **~0.32 mm, repeatable to ±0.03 mm**.

**`01 47 00 03` — coarse backward, −7.47 mm**

```
n   busy  pos    dx px    dx mm
1    1.1    4  +359.78  +30.674   <- aliased, see below
2    1.1    4   -87.97   -7.500
3    1.1    4   -87.75   -7.482
4    1.1    4   -86.92   -7.411
5    1.1    4   -88.15   -7.515
```

Steps 2-5 agree to within 0.6 px: **−7.47 mm, repeatable to ±0.05 mm**. The first
reading is the aliasing this method was designed around — a true −88 px read as +360
by a peak wrapping in a 428-column window. It is left in the table because a
measurement that can fail this way should show what the failure looks like.

#### Why this matters

The aperture has **0.49 mm** of slack (§11, the registration work). A 0.32 mm forward
step moves within it. So registration *is* correctable from the host, in increments
of the right size, using a command the vendor sends in every session.

#### What is not established

- **A fine step backward.** `01 01 00 04` is the obvious candidate — action `0x01`
  for reverse with the `01 00 04` that steps finely forward — and every individual
  byte is one the vendor sends. But **that combination appears in no capture**, and
  the rule after the `SET_SCAN_HEAD` incident is that invented payloads are not sent.
  Coarse-back-then-fine-forward reaches anywhere without it, clumsily.
- **What the bytes mean.** `param` is `0x01` for the fine step and `0x46`/`0x47`/`0x4c`
  for the coarse ones, which looks like a magnitude, but the ratio does not follow:
  0.32 mm against 7.47 mm is 23x, while `0x01` against `0x47` is 71x. Action `0x00`
  is not simply "forward" either — `00 46 00 00` measured negative, though that
  reading is in the untrustworthy range.
- **The three large single-shot figures** in the first table. Direction is probably
  right; magnitude is not, until each is repeated the way the two above were.

## 12. What is still unknown

- MODE SELECT byte 14 entirely. Bit 0 was thought to be scan direction; §11
  refutes that, and nothing has replaced the explanation.
- What makes a scan come back mirrored. It is real and reproducible as a pair of
  groups, but not driven by any byte tried. See §11.
- What the `param` and `value` bytes of the sub-frame SLIDE actions encode. Two of
  the five payloads are characterised (§11); the other three have direction but not
  a trustworthy magnitude, and no rule relates the bytes to the distance.
- Whether a fine step *backward* exists. `01 01 00 04` is the candidate and is not
  sent, being a combination no capture contains.
- SLIDE INIT's `param` byte. `0x13`-`0x16` are interchangeable when measured;
  `0x01` produced a differently oriented image once, in a run where orientation
  was varying for other reasons too, so it is not cleared either way.
- READ STATE bytes 3, 4, 7, 11, 12. (Byte 8 is answered in §11.)
