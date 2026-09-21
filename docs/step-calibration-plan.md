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


---

# What it measured

Run 2026-09-21 on frame 2, `tools/verify_protocol.py` stages 10 and 11, raw
passes in `probe/step-calibration/`.

## The cost of a command is real, and larger than the law said

Three legs, each totalling `param 10`:

| leg | commands | travelled |
|---|---|---|
| A | 10 x `param 1` | **36.41 px** |
| B | 5 x `param 2` | 24.82 px |
| C | 1 x `param 10` | **15.19 px** |

**Ten small commands travelled 2.40x as far as one large one for the same param
total.** Solving A and C:

    a command travels   param + 1.84  units
    one unit            1.283 prescan pixels

Leg B took no part in that solution and predicts **24.62** against **24.82**
measured -- 0.2 px on an independent check.

Section 11's fit says `param + 1.57` with a unit of 1.240 px. So the unit is
**3.5% larger** and the per-command cost **21% larger** than the fitted law.
The fit was over single commands, which is why the per-command term came out
low: nothing in it ever issued two.

## The smallest command is the least repeatable

    param  1 :  3.93 4.25 4.04 3.92 3.68 3.14 2.90 3.31 3.61 3.62   +-18%
    param  2 :  4.98 4.95 4.91 4.95 5.03                            +-1.2%
    param 12 :  median 16.71, 14.15 to 18.78                        +-14%

`param 1` is the command every fine correction uses and the one `HOLD_TOLERANCE`
is defined by, and it is by far the noisiest. **One command of twenty-eight did
not move the film at all** (0.42 px where 16.7 was expected).

## The chain is sound

An inter-frame gap is fixed on the film, so it must move exactly as far as the
film does. Over the ten commands where only one gap was in view:

    the gap moved 169 px in the window
    the accumulated travel said 169.09 px

**0.09 px over ten commands.** The correlation-derived travel is trustworthy,
and so is everything built on it.

## The film, measured rather than assumed

Tracking a second gap of the same width entering later gives the pitch directly,
with no extrapolation:

| | px | units |
|---|---|---|
| pitch, gap to gap | 432.7 | **337** |
| the scanner aperture | 428 | **334** |
| the inter-frame gap | 15 | **12** |
| **the camera frame** | **418** | **326** |
| slack, aperture less frame | 10 | **8** |

## Why frames come out with black at the right

The slack is **8 units**, not the 4.6 the code derives from its assumed frame.
`TARGET_GAP` aims one edge at **2.3 units**, so the other edge is left with
**5.7** -- and that asymmetry is on every frame by construction.

That is Stefan's "a bit on the right is black", predicted from the geometry
before his verdicts were consulted, and it agrees with the independent estimate
from frames showing base at both edges.

Aiming at **4 units**, half the measured slack, splits it evenly.

## What follows

* `STEP` and `OVERHEAD` are both low. Correcting them changes what
  `param_for_mm` returns, so `PROTOCOL_REVISION` moves.
* **Splitting a move is not free.** Each command costs 1.84 units before it
  moves at all, so `plan_nudges` should prefer one large command to several
  small ones wherever the lattice allows.
* `HOLD_TOLERANCE` is the `param 1` distance, which is 14% larger than the code
  believes and scatters by a fifth of itself. The deadband cannot be tightened
  below that scatter however the arithmetic comes out.
* A command that silently does nothing, at roughly one in thirty, is not in any
  model here. The hold loop re-measures after every move, so it survives one --
  but nothing counts them.
