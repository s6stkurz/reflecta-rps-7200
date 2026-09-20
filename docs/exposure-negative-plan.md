# Exposure on colour negative: is 0.80 the right target there?

## Status: run 2026-09-20 on 15 negatives. Metering lands in band on all fifteen; the linearity figure that chose the target does **not** reproduce. `EXPOSURE_TARGET` stays at 0.80 on a different, measured justification. See **The answer** at the end.

`tools/linearity.py` is the offline measure, `tools/exposure_probe.py` the run,
`tests/test_exposure_probe.py` and `tests/test_linearity.py` hold both with no
device attached.

Every number below re-derives from the filed raw bytes, on a machine holding the
library:

    uv run python tools/linearity.py --probe probe/exposure.json

`probe/exposure.json` is gitignored, like the `library/` it indexes -- it is what
maps each entry to the frame and rung it came from, since entries are named from
flush time. The numbers themselves live in this document, which is the same
bargain every other plan here makes.

## The question

`EXPOSURE_TARGET = 0.80`. It was chosen over 0.90 on a linearity argument, stated
in `rps7200/direct.py` and repeated in `TODO.md`:

> *Clipping.* ... Measured here over six entries, four frames and three
> resolutions, it costs almost nothing: worst case 0.001% of blue at 0.80, 0.019%
> at 0.90 and 0.116% at 0.95.
> *Linearity.* A CCD compresses before it saturates. Measured on the 9-pass
> 3600 dpi ladder, departure from linear is under 0.1% below a third of scale and
> grows to **1.5-1.9% in the 75-95% band**.
>
> So clipping would allow well past 0.90 and linearity argues for stopping
> sooner. 0.80 takes the second.

Two things were wrong with the state of that. The **1.5-1.9% had no derivation** --
the run's code was never kept, so the number that chose the target could not be
checked. And the ladder it credits is **on a slide**, while this scanner is mostly
pointed at colour negative.

## What the offline half found

`tools/linearity.py` measures the one thing that matters: a linear sensor
multiplies every level by the same factor, so the achieved ratio between adjacent
rungs should be identical in every level band. It reports the departure per band,
where a linear sensor reads 0.00% everywhere.

Three things had to be got right, and each one cost a wrong answer first:

- **Adjacent rungs, not one reference.** A fit made on dark pixels and
  extrapolated into the bright band reported -16% to -26%. That was the
  extrapolation failing, not the sensor bending.
- **Saturation is not compression.** Red pins its median at 0.91 from the fifth
  rung of the slide ladder on. Included, it turns a sub-1% effect into a -16% one.
- **Raw, not corrected.** The correction's per-column gain exceeds 1 wherever the
  lamp falls off, so it pushes near-rail values around and inflates the very band
  under test.

### The slide ladder does not show 1.5-1.9%

Nine rungs, x1.189 apart, raw, largest departure at or above 70% of scale:

```
    R  -0.64%      G  -0.30%
```

and it is consistent: five independent adjacent pairs agree within about 0.2%
across the 40-90% bands. On **corrected** pixels the same ladder reads **-15.96%**
for red -- which is the saturation artefact, and is the most likely source of the
recorded figure.

### But negative departs more than the slide does

The only negative ladder on record is three rungs at 600 dpi, x2.0 apart
(`20260903T2256*`). Raw, its upper pair:

```
    R   70-80%  -0.50%    80-90%  -0.94%    90-97%  -1.20%
    G                                       worst   -1.53%
```

So the effect is real and is **roughly twice the slide's** on the film that
matters -- and it only reaches 1.2-1.5% in the top two bands. The recorded
1.5-1.9% is closer to right for negative than for the slide it was attributed to.

**That is why the hardware run is still worth an hour.** The negative number rests
on three rungs at a x2.0 spacing over a 230x344 crop, and its lower pair is noisy
enough to read positive. It cannot say where the bend sets in, which is exactly
what a target has to be chosen on.

## What the hardware run adds, and what it does not

Nobody has ever walked a strip. Of 243 library entries, 13 carry a `metering`
block, and all seven colour-negative ones are **one frame, one sitting, 2.5
minutes apart** -- a resolution sweep. `README.md` says the point of filing
`last_metering` is that these constants "become checkable from ordinary work".

Read off those seven: red lands 0.78-0.79 and green 0.79-0.80, inside the band,
and **blue lands 0.65 pinned at its x10.073 timer ceiling on every one**. Blue's
shortfall on negative is the hardware rail, not metering. So two things have never
been exercised: nothing has ever landed *above* target, so the `+0.02` over-band
is untested; and no entry has ever spent the third, clipped-channel round.

It does **not** re-measure the clipping cost of each candidate target. That is
`tools/exposure_headroom.py`, offline, and it needs no scanner.

## The design

**300 dpi, RGB.** An exposure question is about levels, not detail, and 300 dpi is
the cheapest pass this scanner takes. RGB only also removes a hazard: RGB's byte
14 default is `0x10`, bit 0 clear, so the consecutive-bit-0-set reversal trap
cannot fire.

**Every frame is metered and scanned once at what metering asked for.** That is
the check against the current setting, fifteen times instead of once.

**A ladder on a spread subset**, default every fourth frame. Linearity is a
property of the sensor, not of the picture, so measuring it fifteen times would
spend half an hour to learn it once; where metering lands *is* a property of the
picture, so that gets all fifteen.

The ladder, relative to each frame's own metered exposure:

```
    x1.00   x0.55   x0.66   x0.80   x0.96   x1.15   x1.00
```

Geometric from 0.55 to 1.15 so the rungs are evenly spaced in exposure, which is
what the adjacent-ratio measure wants. On negative, metering puts red at about
0.79, so those rungs put it at roughly 0.43, 0.52, 0.63, 0.75 and **0.91** --
spanning the 90-97% band without saturating, which the slide ladder could not do.

It opens and closes on `x1.00`, so that pair is three things at once: the
what-ships pass, the repeat pair `noise_split` and `agreement_z` need, and the
drift check across the frame's own ladder.

**Those two are not linearity rungs, and the analysis leaves them out.** The
chain is `RUNGS` -- 0.55 to 1.15, a clean x1.2 a step -- while `x1.00` sits 4%
from `x0.96`, and an adjacent-ratio measure handed a pair that close divides one
noisy number by another and reports the quotient as compression. It is the same
mistake as the slide ladder's saturated top, arriving from the other end. The
split is held by a test rather than left as a note, in
`tests/test_exposure_probe.py`.

**Blue will not move above `x1.00`.** It is already at its timer ceiling on
negative, so `Settings.scaled` clamps it and the brighter rungs are red-and-green
only. The tool says so rather than letting it read as a null result.

## What decides it

1. **Does metering land in the band, frame to frame?** Red and green against
   `0.72 … 0.82`, fifteen frames. Does blue ever come off its ceiling? Does any
   frame land above target or spend the third round?

   Read off **the delivered pass**, not off the probe's last round. The last
   round's `levels` describe the scales that went in *before* the correction
   which produced the returned ones, so they are one step stale -- a report built
   on them would be grading metering on its own working. The probe reads each
   pass the way `auto_exposure` reads a probe, `metering_slice` then the 99.5th
   percentile, so the two numbers mean the same thing.
2. **Where does the bend set in on negative?** `tools/linearity.py` over each
   ladder frame's passes, against the slide's -0.64% and the old bracket's
   -1.20%.
3. **Judged against noise.** `agreement_z` on each frame's `x1.00` pair gives that
   frame's own baseline; a departure smaller than it is not a reading.
4. **Drift first.** First `x1.00` against last, per frame, before anything else is
   believed.

## Cost

Per frame: ~66 s metering, one 33 s pass, ~7 s advance. Ladder frames add six
passes, ~3.3 minutes. Fifteen frames with four ladders is about **40 minutes**,
plus one shading calibration and the rewind.

**Backgrounded**, because the harness kills a foreground command at 10 minutes and
a killed read is an abandoned read. Film stays loaded throughout -- calibration
happens with it in.

## Before it is driven

- `make all` and `make test-all` green -- done.
- `--dry-run` quotes 39 passes, 15 meterings, ~40 minutes. `--only A-B` chunks it
  under the ten-minute foreground kill for a run that is not backgrounded.
- It refuses to start without `RPS7200_DEBUG=1`: a probe that files nothing
  cannot be re-analysed, and forty minutes of hardware is not worth spending
  twice.
- Results are written after every pass, and the film is returned to where it
  started on every exit path, including a raise.
- **Then ask Stefan.** Film loaded, about 40 minutes, backgrounded.

## What actually followed

Run 2026-09-20 on a colour negative strip, 15 frames, 39 ladder passes plus 30
metering probes, all filed. 24 minutes rather than the 40 budgeted -- passes at
300 dpi came in at 15-22 s, not 33.

### Metering lands in the band. Fifteen for fifteen.

```
    R  0.774 - 0.784        G  0.788 - 0.813        B  0.408 - 0.702
```

Every frame, red and green inside `0.72 … 0.82`. Not one miss, and the spread
across fifteen different photographs is under a percent on red. The first thing
this run establishes is the dullest and the most reassuring: metering does what
it claims, and it does it frame to frame, not just on the one frame anybody had
ever checked.

Two things it *still* has not exercised, after fifteen frames. **Nothing landed
above target** -- the highest reading is green at 0.813, inside the `+0.02` band
-- so the over-band remains untested, and metering appears to sit systematically
just under: red averages 0.778 against a target of 0.80. And **no frame spent the
third, clipped-channel round.**

### Blue is the real exposure problem on negative, and it is not metering

Blue hit the 16-bit timer ceiling on **19 of 19** metered passes, and what varies
is how far short the ceiling leaves it:

```
    frame  9  0.453      frame 10  0.441      frame 13  0.408
    frame 15  0.702      frame 14  0.677      frame  7  0.674
```

That is a range of 0.29, from a third of a stop under target to a **full stop
under**. The dense frames are the ones that suffer, which is the expected
direction and a much larger effect than anything linearity contributes. It is
also not fixable by metering: the timer is already at 65535. `CLAUDE.md` already
says the way out is RGBI, where blue is ~5x more sensitive; this is fifteen
frames of evidence for that, where there was one.

### Drift across each ladder is small enough to ignore

First `x1.00` against last, per frame:

```
    frame  1   R -0.27%  G -0.20%        frame  9   R -0.13%  G +0.06%
    frame  5   R +0.10%  G +0.17%        frame 13   R -0.03%  G +0.07%
```

`agreement_z` on each frame's repeat pair reads **1.05-1.24** against a house
baseline of ~1.03, so the pairs are consistent with noise. The random share of
high-frequency content is 10-35%, in line with the 21-27% measured on the slide.

That is fifteen repeat pairs, not the four the ladder frames provide, and it is
the *filing rule* that produced the other eleven: `auto_exposure`'s second probe
round lands on the scales it returns, so the metering probe and the metered pass
are the same commanded exposure, and both are filed. The rule that says file
every scan, including the throwaway ones, is what let the null below be measured
on every frame rather than a quarter of them.

### Linearity: real, consistent, and about half what was recorded

Raw, largest departure at or above 70% of scale, one reading per frame:

```
    R   f1 -0.64%   f5 -0.57%   f9 -0.52%   f13 -0.64%      spread 0.12%
    G   f1 -0.73%   f5 -0.61%   f9 -0.73%   f13 -0.66%      spread 0.12%
```

**Four different photographs agreeing to 0.12% is the sensor.** That is the
library's own sensor-versus-picture test -- a fixed effect across different film
positions -- applied to level instead of to column, and it is the thing three
rungs on one frame could never have said.

Blue reads 0.00% because it never reaches those bands. It is railed at 0.41-0.70,
so the top half of this is a red-and-green measurement, exactly as the design
said it would be.

### The metric has a one-sided bias, and correcting it makes the effect larger

Each frame's two `x1.00` passes are the same commanded exposure, so the metric
must read 0.00% on them. It does not: **26 of 26 red and green null readings came
back positive**, +0.01% to +1.06%. One-sided is not scatter. Banding on the
brighter pass selects pixels whose noise went up, which biases the ratio in a
high band upward relative to the base band.

So the null is a bias to subtract, not a floor to clear. Paired against each
frame's own null, on bands resting on 1500+ pixels:

```
    R   -0.45%  -0.88%  -1.06%      mean -0.80%
    G   -0.44%  -0.49%  -0.72%      mean -0.55%
```

All six negative. The effect is **-0.6% to -0.8%**, and the uncorrected figure
understates it.

An earlier pass at this reported the null as 2.48% and nearly buried the result.
That reading was **blue, on 580 pixels** -- blue has almost nothing above 70% of
scale because it is railed, so whatever squeaked past `MIN_PIXELS` was noise. The
lesson is the population, not the channel: at 300 dpi the analysis crop is
114x171, and a band median resting on a few hundred pixels says nothing. Every
figure above is quoted with the count behind it.

### Corrected pixels agree with raw, which the slide's did not

```
    R   -0.32%  -0.70%  -0.54%  -0.72%        G   -0.95%  -0.71%  -0.71%  -0.86%
```

Same magnitude as raw. On the slide ladder the corrected number was **-15.96%**
against a raw -0.64%, and this is the confirmation that the difference there was
saturation and nothing else: here the brightest rung peaks at 0.89-0.92, nothing
pins, and the two measures agree.

## The answer

**The 1.5-1.9% figure does not reproduce on colour negative, and it is not a
slide-versus-negative difference.** Negative departs about **-0.6% to -0.8%**
above 70% of scale; the slide departs -0.64% raw. The two films are alike, and
both are roughly a third of what was recorded. The recorded number is best
explained as the slide ladder's saturation artefact, measured on corrected
pixels.

So the argument that chose `EXPOSURE_TARGET = 0.80` over 0.90 -- *"linearity
binds before clipping does"* -- is **about half as strong as stated**. Frame 13
reads -0.62% in the 80-90% band and -0.64% in 90-97%, so the marginal linearity
cost of operating at 0.90 instead of 0.80 is a further 0.1-0.2%, against a
recorded clipping cost at 0.90 of 0.019% of blue. On linearity alone, 0.90 would
be defensible.

**But the walk supplies a better reason to stay at 0.80, which linearity was
standing in for.** Metering's own frame-to-frame spread is now measured: green
ranges 0.788 to 0.813 at a 0.80 target, and red 0.774 to 0.784. A 0.90 target
would put green's top frames at ~0.91 with only 0.07 of headroom left for that
spread plus whatever the metering error is on a frame nobody has scanned yet --
and over-exposure is the one direction nothing downstream can undo. That is a
headroom argument from measured spread, not a linearity argument from prose, and
it is the first time the spread has been known.

`EXPOSURE_TARGET` is therefore **unchanged**, with a different and measured
justification. The linearity claim in `rps7200/direct.py` and `TODO.md` should be
corrected to -0.6/-0.8% and re-attributed; that is a separate change, because
those two also state the clipping figures and the whole passage wants rewriting
against this run rather than patching.
