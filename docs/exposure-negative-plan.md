# Exposure on colour negative: is 0.80 the right target there?

## Status: the offline half is done and it moved the question. The hardware run is designed, costed and **not yet driven** -- it needs Stefan, film loaded, about 40 minutes.

`tools/linearity.py` is the offline measure; `tools/exposure_probe.py` is the run,
and `tests/test_exposure_probe.py` holds its shape with no device attached.
Nothing has been driven.

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

Not yet run.
