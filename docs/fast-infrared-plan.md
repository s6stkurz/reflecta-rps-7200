# Fast infrared: MODE SELECT quality bit 0x80

## Status: answered 2026-09-16. **The bit removes the infrared floor. The saving is whatever the floor was worth at that resolution.**

```
    time with the flag  =  7.46 s  +  59.88 ms/line          (r^2 = 1.000)
    time without it     =  219.8 s, flat, whatever the line count
```

An ordinary infrared pass costs ~220 s at every resolution -- that is the
documented floor. With the bit set the floor is simply gone, and the pass costs
what its line count costs, exactly like a scan with no infrared in it. The two
curves cross at about **3700 dpi**, which is why 3600 dpi looked like a failure
and 300 dpi looks like magic.

| dpi | lines | without | with | saved | |
|---|---|---|---|---|---|
| 300 | 286 | 219.2 s | 24.6 s | 194.6 s | **-88.8%** |
| 600 | 573 | 219.6 s | 41.7 s | 177.9 s | **-81.0%** |
| 900 | 860 | 219.8 s | 58.9 s | 160.9 s | **-73.2%** |
| 1200 | 1147 | 220.1 s | 76.3 s | 143.8 s | **-65.3%** |
| 1800 | 1721 | 220.3 s | 110.5 s | 109.8 s | **-49.8%** |
| 3600 | 3443 | 221.0 s | 213.6 s | 7.4 s | -3.4% |
| 7200 | 6886 | ~420 s | ~420 s | ~0 | ~0, predicted |

**The film was a red herring.** 1800 dpi on colour negative gave -49.7% and
1800 dpi on slide gives -49.8%. It was resolution the whole time.

### Superseded: the earlier reading of this

This section previously said the result "does not generalise" and that the
saving was unexplained. Both are wrong and are corrected above. The 3600 dpi
slide run was not a failure to reproduce; it was the one resolution measured
where the line-count cost had already caught up with the floor.

Quality was measured at two configurations, eleven passes: 1800 dpi on colour
negative and 3600 dpi on slide. **Nothing degrades** -- picture, infrared plane
and dust all unchanged against same-setting controls.

Both runs also found something about the hardware that outlives this question:
**the carriage start moves between passes**, and the flag itself moves it. Every
comparison here is aligned pair by pair for that reason.

**Still not adopted as a default**, for one reason that is worth stating
precisely: the resolution where quality was properly tested and the flag
actually *does* something is 1800 dpi, and that one is clean. At 3600 the "no
degradation" result is nearly vacuous, because the flag barely changes the pass
there. Below 1800, where the saving is 73-89%, quality is untested.

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

`agreement_z`, green channel, darkest tenth, all fifteen pairs at their own best
alignment, grouped by whether the flag differed:

```
  off/off   n=3   median |z| = 1.34    range 1.33-1.36
    on/on   n=3   median |z| = 1.20    range 1.18-1.38
   off/on   n=9   median |z| = 1.24    range 1.08-1.45
```

The three families are the same, and `off/on` sits between the two controls.

*(These numbers are the corrected ones. This document first reported 2.14 / 2.10
/ 2.00, computed without aligning each pair -- all three were inflated by the
carriage drift below, which is also why they sat so far above the 1.03 repeat
baseline. The conclusion was right; the numbers were not.)*

### The infrared plane is untouched, and keeps its dust

Agreement on the infrared plane itself, which is the sharper question:

```
  off/off   n=3   median |z| = 1.06
    on/on   n=3   median |z| = 1.07
   off/on   n=9   median |z| = 1.05
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

## The second run: 3600 dpi, slide film, 2026-09-16

Taken to answer "does this generalise", on the two axes that mattered most --
resolution, and a different stock. Slide is also a *positive*, so it exercises
the metering path where the visible channels are locked together. Both axes
moved at once deliberately, at the cost that a negative result cannot be
attributed to either.

It is a negative result.

### The saving did not reproduce

```
setting   n   mean ms/line   vs off      per pass
    off   3          64.18    1.000       221.0 s
     on   3          62.12    0.968       213.9 s
```

**-3.2%.** Systematic -- 221.2/220.9/220.8 against 213.4/213.9/214.3, no overlap
-- but 7 seconds, not 124.

The obvious explanation is wrong: the infrared exposure was **7745 in both
runs**, identical, so this is not the bit saving time proportional to an
exposure that happened to be long the first time.

### Nothing degraded there either

Every pair at its own best alignment:

```
            picture                    infrared
  off/off   1.16   on/on 1.01     off/off 1.10   on/on 1.06
   off/on   1.17                   off/on 1.10
```

Dust persists at 0.91-1.00 within each setting. No cross-flag speck comparison
was possible: no `on` pass shared an alignment with any `off` pass, for the
reason below, and the probe declined to invent one.

### The flag moves where the carriage starts

At 3600 dpi the alignment split perfectly along the flag: same-setting pairs
align at 0 or -1 lines, cross-flag pairs at -2, +1 or +2. Every `on` pass landed
a line or two from every `off` pass, consistently.

This is a real property of the bit, and it is not a degradation -- a scan on its
own is unaffected, and it is a couple of lines at 3600 dpi, about 14 um. It
matters only where an `on` pass and an `off` pass are combined or compared, and
it is the reason the second run very nearly produced a false negative.

### The second correction to this plan's method

The families comparison, introduced to fix the first run's flaw, failed on the
second in a new way. Families survive drift only when the drift is uncorrelated
with the flag. Here it was *perfectly* correlated -- every `on` pass at +1, every
`off` pass at 0 or -2 -- so off/on was the only family comparing misaligned
passes, and it duly read worst. The probe reported

```
   picture  off/off 1.41   on/on 1.02   off/on 1.84   -> "MOVED"
  infrared  off/off 1.33   on/on 1.06   off/on 1.62   -> "MOVED"
```

and both verdicts were registration artefacts. Aligned pair by pair the same
data gives 1.16/1.01/1.17 and 1.10/1.06/1.10: no effect whatever.

**Every comparison now aligns first**, over a +-4 line search, and the shifts are
printed because a systematic one is itself the finding above. There is no
arrangement of the ladder that avoids this; only aligning does.

### An unexplained reading, recorded rather than explained

Infrared random noise, from same-setting pairs:

```
  1800 dpi negative:   off 575.6 DN   on 602.6 DN   (on/off 1.047)
  3600 dpi slide:      off 493.2 DN   on 314.1 DN   (on/off 0.637)
```

The bit made the infrared plane *quieter* at 3600 dpi, by a third. A
"faster, lower-quality" mode should not do that. Line binning would explain both
the reduced noise and the reduced saving -- read everything, combine it -- but
speck depth is unchanged at 3600 (7.17-7.25 sigma source, comparable to off),
and binning should blunt a point feature. It is not explained, and nothing here
depends on explaining it.

## The sweep: every resolution, one film, 2026-09-16

Twelve passes planned, one `off` and one `on` at each of 300, 600, 900, 1200,
1800, 3600 and 7200 dpi, on the slide already loaded. One exposure metered once
at 1800 dpi and **held across every resolution** -- scan time tracks exposure, so
metering each resolution separately would have put a different exposure behind
each row and let the difference be called resolution. The order alternated, `off`
first at 300, `on` first at 600 and so on, so that nothing drifting across the
hour could line up with the flag.

Three repeats a side were not taken and were not needed: timing here repeats to
a tenth of a second, and the ladders had already spent their repeats on the
quality questions.

**It ended early**: the scanner was disconnected during the 3600 dpi `off` pass
(`LIBUSB_ERROR_IO`), which cost that pass and the whole 7200 dpi pair. Nothing
was lost -- the 3600 `on` pass had already completed, and the earlier slide
ladder supplies the 3600 `off` figure at an exposure within 2% of this one.

### What the numbers say

The `off` column is flat: 219.2, 219.6, 219.8, 220.1, 220.3 s across a sixfold
resolution range, a spread of 1.1 s. That is the infrared floor, and it is
indifferent to how many lines were asked for -- exactly as
`docs/dpi-tradeoff-plan.md` found.

The `on` column is a straight line in the line count:

```
    dpi   lines   measured    fitted     error
    600     573      41.7s     41.8s    -0.07s
    900     860      58.9s     59.0s    -0.06s
   1200    1147      76.3s     76.1s    +0.16s
   1800    1721     110.5s    110.5s    -0.01s
   3600    3443     213.6s    213.6s    -0.02s
```

`time = 7.46 s + 59.88 ms/line`, fitting every point to within 0.16 s. The fit
was then checked against a pass it was not fitted to -- the 3600 dpi `on` mean
from the earlier ladder, a separate run on a separate exposure -- and predicted
it to **0.24 s**.

### What that means

The bit does not make the infrared acquisition faster. **It removes the floor.**
With it set, an RGBI pass costs what an RGB pass of the same geometry costs; the
~212-250 s the infrared plane has always cost, at every resolution, regardless
of line count, simply is not spent.

The two costs cross where `7.46 + 0.05988 x lines = 219.8`, at **3546 lines,
about 3709 dpi**. Below that the floor is the dominant cost and removing it is
worth almost everything; above it the line count already exceeds the floor and
there is nothing left to remove. 3600 dpi sits just under the crossover, which
is the entire explanation of the second ladder's -3.2%.

7200 dpi was not reached. The model puts it at ~420 s either way, so no saving,
and it is refused for real scans anyway because the device will not produce a
shading reference wide enough to correct it.

## Where this leaves it

The mechanism is understood and the saving is large exactly where this driver
spends most of its time. At 1800 dpi -- the resolution `docs/dpi-tradeoff-plan.md`
recommends when time matters -- an infrared pass goes from 220 s to 110 s, and a
38-frame roll from about 2.3 hours of floor to 1.2. At 900 dpi it is 73% off.

What is still missing before it becomes the default, and it is one ladder:

- **Quality below 1800 dpi is untested.** The two quality ladders ran at 1800
  (clean, and the flag halved the pass there) and 3600 (clean, but the flag
  barely acts there, so it proves little). The band where the saving is 73-89%
  has never been checked for what it costs. A six-pass ladder at 600 or 900 dpi,
  about 15 minutes now that the `on` passes are short, would close it.
- **`PROTOCOL_REVISION` moves** when the default payload does, and not before.

Adopting it above ~3600 dpi is pointless rather than harmful: there is nothing
to save, and it still shifts the carriage start by a line or two.
