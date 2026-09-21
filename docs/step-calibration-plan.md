# Measure the transport in its own units

> **Millimetres are prohibited in this work.** Not discouraged -- prohibited.
> Every distance here is in units of the adjustment parameter, and anything
> that arrives in millimetres is converted before it is written down or
> reported. The reason is in "Why" below: a millimetre is a unit this transport
> has never once moved, and holding the work in it has let conversions hide
> mistakes twice already.

## Why

Every distance in this driver is held in millimetres, and every one of them is a
conversion away from what the hardware actually does. The transport does not
move millimetres. It executes `SLIDE <action> <param> 00 04`, and what it
delivers is a function of `param`.

Stefan's instruction: **stop using millimetres, measure in units of the
adjustment parameter.** Nothing in this document is in millimetres.

## The unit

`docs/protocol.md` §11 fitted the law over ten points in both directions, worst
residual well under a prescan pixel:

    one command travels   param + 1.57  units

where **one unit is the distance one increment of `param` adds**. The `1.57` is
a fixed cost per *command*, independent of `param`.

That second term is the reason this measurement is worth scanner time. If it is
real, then ten commands of `param 1` travel **25.7 units** while one command of
`param 10` travels **11.6 units** — the same param total, more than twice the
distance. If it is an artefact of fitting single commands, param units are a
clean linear scale and a great deal simplifies.

**It has never been tested.** The fit used single commands only; nothing has
ever checked whether commands compose the way the law says.

## What the code currently believes, restated

| | units |
|---|---|
| the scanner aperture | **345.2** |
| the camera frame, as the code assumes it | 340.6 |
| the camera frame, as measured indirectly from frames showing base at both edges | 334.0 |
| the smallest possible command, `param 1` | 2.57 |
| the largest the registration code will send, `param 8` | 9.57 |
| the deadband `HOLD_TOLERANCE` | 2.57 |
| where the code aims the picture, `TARGET_GAP` | 2.32 |
| one prescan pixel at 300 dpi | **0.81** |

The last line is what makes this readable: the prescan resolves about four
fifths of a unit, finer than the smallest move the transport can make.

`param` is not limited to 8. That is our registration ceiling, not the
hardware's — the vendor sends `SLIDE 00 46 00 00`, **param 70**. §11 calls the
law good to param 12, bending above about 20, and repeatability collapsing at
the vendor's largest.

## The experiment

Frame 2, because it is well exposed and has clear lines. Everything forward, so
backlash is spent once at the start rather than between legs. A prescan after
**every** command — the frame counter does not move for a sub-frame command, so
the picture is the only witness.

### Three ways, each totalling `param 10`

| leg | commands | the law predicts |
|---|---|---|
| A | 10 × `param 1` | 25.7 units |
| B | 5 × `param 2` | 17.9 units |
| C | 1 × `param 10` | 11.6 units |

Same param total, three different command counts, so the spread between them
**is** the per-command overhead, with nothing assumed:

    overhead = (A - C) / 9        step = (C - overhead) / 10

and B is an independent check on both. If A, B and C land together, the
overhead is zero and param units are linear.

### Then the frame, by traverse

From one extreme, forward command by command with a prescan each time, until
the picture has crossed the aperture completely. Counting commands and reading
each prescan gives, measured rather than assumed:

* the **aperture** in units
* the **camera frame** in units — the quantity the code assumes to be 340.6 and
  which an indirect measurement puts at 334.0
* whether the step stays constant over a whole frame of travel, or drifts

### The self-check

The same crossing in the fewest commands, at `param 12`. Agreement confirms the
law holds across the range; disagreement locates where it stops.

## What it costs, and what could go wrong

About fifteen minutes of scanner time, in chunks so no single command can
overrun the limit that wedged this device once before.

* **Total travel is about 40 units × 10 — a whole frame of sub-frame movement.**
  The most ever done here is three commands on one frame. Same motor as a frame
  advance, so it should be routine, but it is unprecedented in this project.
* **Backlash.** Two throwaway forward commands first, so the first measured leg
  is not eaten by a direction change.
* **Frame 2 is thirteen frames back**, so a rewind comes first, and a rewind is
  where backlash bites hardest. `--rewind` already tolerates it and verifies
  each step landed.

## What follows from it

If the overhead is real, `param_for_mm`'s inverse is right and the lattice of
reachable positions is what the code thinks, but **many small commands are not
interchangeable with one large one** and anything that plans a move must count
commands, not sum params.

If the overhead is not real, the smallest possible move is smaller than 2.57
units, which is the constant behind `HOLD_TOLERANCE` — and the deadband, which
is the floor on how well any frame can be placed, drops with it.

Either way the frame width comes out measured, and that number feeds
`TARGET_GAP` directly, which every proposal on every strip is derived from.
