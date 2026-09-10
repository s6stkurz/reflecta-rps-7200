# Byte 14 of MODE SELECT: does it change the line rate?

## Status: designed, not run. Needs Stefan at the scanner.

Written down before the hardware is touched so the scanner time is spent on a
decided question. **Do not drive this without his go-ahead**, as with
`docs/analog-gain-plan.md`.

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

## What follows from each outcome

- **No timing effect.** Record it, leave the default alone, and strike the
  correlation from `docs/protocol.md` §4 so nobody re-derives it. The finding is
  the value.
- **A faster value that costs noise.** Record the trade; do not change the
  default. It belongs, if anywhere, as an explicit argument with the measurement
  beside it — not as something metering reaches for on its own.
- **A faster value that costs nothing.** Then the default is wrong and 3600 dpi
  scans have been taking longer than they need to. Change it, and re-run
  `tools/library.py reconstruct`: the byte reaches the device, so
  `PROTOCOL_REVISION` in `rps7200/direct.py` has to move with it.
