# Fast infrared: MODE SELECT quality bit 0x80

## Status: run 2026-09-16. **It halves the infrared pass, and nothing measurable pays for it.**

250.5 s to 126.1 s at 1800 dpi, three passes of each, with the picture, the
infrared plane and the dust all unchanged against a same-setting control. The
ladder also found something it was not looking for: the carriage start creeps a
few lines across a long run, which confounded the first reading and is worth
knowing on its own.

Adopting it as the default is a design change -- it moves the payload every scan
sends -- and **wants Stefan's agreement**. Nothing about the default has been
changed; `fast_infrared` is still off unless asked for.

## The question

Does quality bit `0x80` shorten an infrared pass, and what does it cost?

`QUALITY_FAST_INFRARED` has been defined in `rps7200/protocol.py` and reachable
through `set_mode` since the protocol was first written, and **nothing has ever
passed it True**. The reference backend describes it as acquiring the infrared
plane *"in a faster, lower-quality pass"*, and refuses it together with
`sharpen` — *"sharpening is only effective with fast infrared off"*
(`pieusb/option.py:299, 325`, quoting `pieusb_scancmd.h:180`).

## Why it is worth asking

The infrared floor is the dominant cost of everything this driver does, and it
does not move with resolution:

```
1800 dpi     RGB   69.6 s        RGBI  227.2 s        docs/dpi-tradeoff-plan.md
```

A 17-frame infrared roll is about an hour of floor and nothing else; a 38-frame
roll is over two. It is the reason 3600 dpi RGBI is recommended at all — there
resolution is nearly free because the floor swamps it. Anything that moves the
floor moves more wall-clock time than every other optimisation in this repo put
together.

**And it is the one place a worse answer might be acceptable.** Every other
speed-for-quality trade on this scanner was refused because it degraded the
delivered picture — the gain register (`docs/analog-gain-plan.md`), the
greyscale mode, multi-exposure. Here the degraded thing is the *infrared plane*,
which is a dust mask and not a picture. Nothing looks at it directly; NegPy
thresholds it. A loss that is unacceptable in R, G or B may cost nothing at all
in I.

## What cuts against it, before the run rather than after

- **CyberView never sends it.** Across 33 scan cycles in six captures the
  quality field is `0x0008` thirty-two times and `0x0800` once
  (`rps7200/protocol.py:55-58`). Bit `0x80` appears in none of them, and neither
  does `sharpen`. The vendor either does not want it or does not trust it.
- **"Lower-quality" is the vendor's own word for it**, via the SANE header.
- **There is no capture to check the bytes against.** Every other field this
  driver sends was read out of a capture of working software; this one was read
  out of a header. That is why `tests/test_fast_infrared.py` asserts the payload
  byte by byte: if the bit is not where this says it is, a run measuring "no
  difference" has measured nothing at all.

## `PROTOCOL_REVISION` is deliberately **not** bumped

CLAUDE.md asks for a bump "when the commands sent to the device change", and at
first reading this qualifies. It does not, and the distinction is worth keeping.

`fast_infrared` defaults to False everywhere, so the payload an ordinary scan
sends is byte-identical to what it sent before — `tests/test_fast_infrared.py`
asserts exactly that. Nothing about the conversation with the scanner has moved
for any scan that does not ask for the bit.

Bumping would have cost something real. `library.signature` includes the
revision precisely so that entries stop reducing to each other once the payload
moves; bumping here would declare every future entry a different measurement
from all 189 already filed, for no change in what was sent, and `duplicates`
would go quiet across the whole library.

What genuinely distinguishes a fast-infrared pass is the flag itself, so **that**
is what went into the signature — where it separates exactly the passes that
differ and nothing else. Bump the revision when the *default* payload changes,
which is what will happen if this bit is ever adopted.

## Why this is safe to drive

It is a bit in a payload this driver already sends before every scan, not a
mechanism. Nothing moves that does not already move. The failure mode is a pass
that comes back wrong-looking, which costs one pass.

Same shape as `docs/byte14-plan.md`, driven safely on 2026-09-11. **Not**
`SET_SCAN_HEAD`. No IEEE1284 RESET, no `STOP SCAN`.

## The design

Six RGBI passes at 1800 dpi on one frame, transport untouched, one metered
exposure held throughout. About 25 minutes, which is the infrared floor and
almost nothing else. 1800 dpi because the floor dominates there while the
visible half is still short.

The ladder is `off on off on on off` — three of each, so both sides have a
same-setting pair for `noise_split` and `agreement_z`; interleaved, so drift
does not line up with the variable; `off` at both ends, so drift across 25
minutes is visible rather than mistaken for an effect.

No seventh pass to restore anything. The bit is sent fresh with every MODE
SELECT rather than persisting in a register, and the ladder already ends on
`off`; a restore pass would cost four minutes to prove what the sixth pass
proved.

### Byte 14 is forced to 0x20, and that is load-bearing

This driver's default for RGBI is `0x21` — bit 0 set, which skips the carriage
re-home. **A bit-0-set pass that immediately follows another bit-0-set pass
comes back top-and-bottom reversed, with nothing in the response to say so**
(`docs/byte14-plan.md`). Six consecutive RGBI passes is precisely that case, so
the driver's own default would have reversed passes 2 through 6 and every
comparison here would have been made against an upside-down frame — silently.

So the probe sends `byte14=0x20`, bit 0 clear, on every pass. It costs the
bidirectional speed uniformly, which does not matter when the comparison is
on-against-off rather than against an absolute.

Ordinary `scan_roll` avoids this trigger only by the shape of the code — an RGB
prescan always precedes the RGBI capture. A ladder has no such prescan, which is
exactly why it has to be arranged deliberately.

### Guards

1. **Byte 14 bit 0 clear on every pass**, as above, and `reversal_against`
   re-checks each pass against the first regardless. A pass that arrives
   reversed stops the run.
2. **Abort if a pass returns no image**, rather than trying the next value.
3. **Abort on a sense condition** that is not the one `cmd_17` routinely queues.
4. **Never abandon a read.** The floor holds the device busy ~212 s however few
   lines are asked for, so the run must be backgrounded — a killed read is an
   abandoned read, and that is what costs a power cycle.
5. **`RPS7200_DEBUG=1` or it refuses to start.** Twenty-five minutes of floor is
   not worth spending twice, and a filed pass is re-analysable without the
   scanner.
6. **Film loaded, and Stefan says so first.** Only he can see the transport.

## What decides it

Three readings. The second can kill it outright, and the third is the one that
makes the question worth asking at all.

**Time.** ms/line, `on` against the `off` mean. Under about 15% is not worth a
protocol change; the floor is the whole point.

**Do R, G and B survive?** The bit is documented as affecting the infrared plane,
but it lives in a field that governs the whole pass. `agreement_z` on an off/off
pair is the control — the same pair-at-one-exposure comparison that gives **1.03**
in `docs/multi-exposure-plan.md` — and `agreement_z` on an off/on pair is the
test. If the picture degrades at all, the answer is no and nothing else matters.
This driver does not trade delivered image quality for time.

**Does the dust survive?** Not "is the plane noisier" but "can the specks still
be found", which is the only thing the plane is for. The specks are located once
in an `off` plane and then the *same pixels* are measured in both, as depth in
sigmas below their local base. A plane that is noisier but keeps its specks
proportionally deep is still a usable mask; one whose specks sink into the noise
is not. `noise_split` on each side's pair is reported beside it.

The verdict is a table of time against those three. A bit that shortens the pass
and leaves the mask usable is worth having and should become an option. One that
shortens the pass and costs the picture is dead. One that saves 5% is dead.

This is the trap `docs/analog-gain-plan.md` walked into — a register that looked
like free brightness and turned out to be a digital multiplier worth 0.5%. The
pairs are in the ladder so the same test settles this one.

### If the frame has no dust

The probe says so and declines to report a number, rather than computing speck
depth from the noise floor. Pick a dustier frame and run it again — 25 minutes
spent on a clean frame answers two of the three questions and not the one that
matters.

## What actually followed

Six RGBI passes at 1800 dpi, one frame, one held exposure, `off on off on on
off`, byte 14 forced to `0x20`. No pass came back reversed and none failed. All
six are filed with their raw bytes, so everything below re-derives offline.

### Time: it halves the pass

```
setting   n   mean ms/line   vs off      per pass
    off   3         145.54    1.000       250.5 s
     on   3          73.27    0.503       126.1 s
```

**-49.7%**, and not a noisy -49.7%: the three `off` passes came in at 250.5,
250.5 and 250.4 s and the three `on` passes at 126.1 s each. The drift check
holds -- first `off` pass 145.55 ms/line, last 145.50.

The line count is identical either way: 1721 lines both times. Whatever the bit
does, it does not do it by returning less.

### The picture is untouched

`agreement_z`, green channel, darkest tenth, all fifteen pairs grouped by
whether the flag differed:

```
  off/off   n=3   median |z| = 2.14    range 2.05-2.29
    on/on   n=3   median |z| = 2.10    range 1.18-2.15
   off/on   n=9   median |z| = 2.00    range 1.17-2.20
```

The three families are the same, and `off/on` is if anything the best of them.
Removing the drift (below) moves all three together -- 2.33 / 2.23 / 2.31 -- and
changes nothing about the conclusion.

### The infrared plane is untouched, and keeps its dust

Agreement on the infrared plane itself, which is the sharper question:

```
  off/off   n=3   median |z| = 1.35
    on/on   n=3   median |z| = 1.38
   off/on   n=9   median |z| = 1.31
```

And the dust, compared only between passes whose measured drift matches, with a
same-setting control beside every cross-flag comparison:

```
 drift     kind  specks   source   measured   ratio
    -2   on->on    4716     7.39       7.28    0.98
    -2   on->on    4697     7.32       7.38    1.01
     0  off->on    4682     7.46       7.26    0.97
     0  on->off    4692     7.39       7.30    0.99
```

Specks persist across the flag exactly as well as between two passes at the same
setting: **0.98x against a control of 1.00x**. That the picks are real film
features and not the noise tail is settled by moving the same pixel set 37
columns, which reads -0.06 sigma.

Infrared random noise rises from **593 to 623 DN**, about 5%, which is the only
cost found anywhere and is invisible beside a speck depth of 7 sigma.

### What it was not looking for: the carriage start creeps

Registered against the first pass, the six came in at

```
  +0  +0  -1  -2  -2  -3     lines, dx = 0 throughout
```

Three lines -- about 42 um at 1800 dpi -- monotonic across roughly twenty
minutes, **with byte 14 bit 0 clear, so a re-home before every pass**. The
re-home does not land in the same place twice.

That matters beyond this run: anything that assumes two passes of one frame are
pixel-aligned is wrong over a long sequence, and a point feature a pixel or two
across loses most of its contrast to a half-line shift. It is why the speck
table above is grouped by drift rather than pooled.

### A correction to this plan's own method

As first written, the probe compared one `off/off` pair against one `off/on`
pair and called the difference an effect of the flag. It was not: the control
pair sat further apart in the run than the test pair, and the drift above did
the rest. It printed "the visible channels are unchanged" for the wrong reason
and would have printed it whatever the flag did.

`report()` now measures drift first, compares the three families rather than two
pairs, and compares specks only within a matched alignment. The conclusion did
not change; the evidence for it did.

### Where this leaves it

The bit halves the infrared pass -- about an hour off a 17-frame roll, over two
off a 38-frame one -- and every reading taken says the pass that comes back is
the same one. Two things to decide before it becomes the default, neither of
which is this document's to settle:

- **`PROTOCOL_REVISION` moves** if the default payload changes. That is the
  right time to bump it, and the reason it was left alone for the wiring.
- **One frame, one film, one resolution.** The measurement is clean but narrow.
  A second frame at 3600 dpi, and one on a different stock, would cost 25
  minutes each and would be what turns this from a result into a default.
