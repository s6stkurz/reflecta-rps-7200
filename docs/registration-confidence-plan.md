# Is the confidence floor safe on self-similar frames?

**Asked 2026-09-14, after the hold loop shipped.** The worry, in Stefan's
words: *grass and sky frames, where the pictures from left to right are almost
the same*. If a frame looks alike wherever you slide it, can `register` be
confidently wrong about where it sits -- and would the hold loop then drive the
film to the wrong place?

**Answer: no, and the intuition inverts. Self-similar frames are the best case
for this measurement, not the worst.** No new statistic is warranted. What was
missing was never a better number; it was the data to check the one we have,
which `tools/registration_margin.py` now produces and `library.save()` now
keeps.

Looking for that data did turn up something, though it is not the thing that
was asked about: measured over 3850 pairs instead of one roll, the floor clears
the worst wrong match by 24.8 points but sits only **3.9** below the weakest
right one. It is still where it should be -- the two errors are not equally
costly and the margin belongs where it is -- but it is not the comfortable
chasm a single roll suggested, and genuine matches will sometimes be refused.
Section 1 has the numbers.

## What `confidence` actually is

`register` (`rps7200/uniformity.py`) computes a **z-score**: how far the
correlation peak stands above the mean of the searched window, in standard
deviations *of that window*.

    confidence = (window.max() - window.mean()) / window.std()

Three docstrings called it "the peak height over the mean", which is a ratio
and a different quantity with a different scale. They have been corrected. The
distinction is not pedantic: the `std` term is why the score depends on how far
the match was searched -- the same pair scores **23.8 at a 16 px reach and
129.7 at 200 px** -- and anyone re-fitting `CONFIDENCE_FLOOR` from the old
wording would have computed the wrong thing and concluded the floor was wildly
misplaced. Hence `SEARCH_MM = 9.0`, fixed: every number below is only
meaningful at that reach.

## The measurements

Real film: `rolls/2026-09-14` (17 surveyed frames) and the `library/` entries
beside it (5 repeat passes of one frame, 6 of another, 4 of a third). Both
directories are gitignored, so these numbers cannot be committed with this
document -- `tools/registration_margin.py` re-derives them.

**Labels are not ground truth, and assuming they were is what made the first
pass at this wrong.** A library entry's `frame` is its number within one run,
and two runs start at different places on the strip: five entries labelled
frame 01 turned out to hold three distinct photographs. Pairs labelled
identically scored 4.6. The arbiter throughout is therefore the pixels --
align the pair at the lag `register` chose, then correlate. Over 3850 pairs
that measure is sharply bimodal and **nothing at all lands between 0.75 and
0.85**: different photographs pile up near 0 and tail off by 0.75, the same
picture twice starts at 0.89. The cut is 0.80, the middle of the empty band.

That threshold had to be measured too. An initial 0.95, picked by eye from a
handful of pairs, called **22 genuine same-picture pairs false positives** --
two library passes of one frame differ in exposure and metering, which costs
real correlation without making them different pictures. Had that stood, this
document would have reported the floor as broken. `tools/registration_margin.py`
now flags any pair landing within 0.05 of the cut, because nothing did on the
film it was fitted on.

### 1. The separation holds, but the floor is not centred in it

Every pair of every roll and every 300 dpi library entry -- **3850 pairs**:

| | n | confidence |
|---|---|---|
| different photographs | 3754 | 4.1 - **30.2** |
| the same picture twice | 96 | **54.9** - 203.5 |
| the same picture, within an hour | 74 | **58.9** - 203.5 |
| `CONFIDENCE_FLOOR` | | 55 |

**No pair that is not the same picture has ever scored above the floor.** The
safety property -- the one that would move film to the wrong place -- holds
with 24.8 points to spare.

The other side is tight, and the first pass at this got it wrong by looking at
one roll. `rolls/2026-09-14` alone shows nulls topping out at 11.4 and true
matches starting at 93.5, which reads as a chasm. Over everything, the worst
null is **30.2** and the weakest true match is **54.9** -- or 58.9 restricting
to passes less than an hour apart, which is the comparison the hold loop
actually makes. So the floor stands **3.9** points below the weakest match it
will meet in practice, not 38.

**55 is kept anyway, and the asymmetry is the point.** The two errors are not
equally bad. A false positive moves the film to the wrong place; a false
negative refuses to measure, so the film is not moved, the frame is scanned
where it lies and flagged. The floor should therefore hoard margin on the
false-positive side and spend it on the other, which is exactly what 55 does.
Anything in roughly 35-55 is defensible on this data; 55 is the conservative
end of that range and the conservative end is the right one. The cost is
honest: an occasional genuine match refused, and a frame scanned unheld.

### 2. The apparent counter-example was a correct match

An earlier look flagged `prescan10` vs `prescan11` scoring 120 -- a null pair
above the floor, which would have discredited the metric outright. It is not a
null pair. Aligned at the lag `register` found, those two correlate at
**0.9890**; the three other high scorers (`01`/`08`, `02`/`09`, `03`/`12`) at
0.9953-0.9971. The survey passed over the same piece of film twice. Every one
of the four is right, and **there is no counter-example in the data.**

### 3. Grass and sky score highest

Each of the 17 real frames displaced by a known +-3/8/24 px, with a **real**
pass-to-pass residual added (2.8-3.4 DN, measured from a genuine repeat pair,
with the frames that donated it excluded):

```
        frame  detail    -24     -8     -3     +3     +8    +24
prescan05.tif    0.65    153    156    156    157    156    154   <- flattest
prescan09.tif    0.73    161    167    168    168    169    168
      ...
prescan14.tif    1.69    183    186    186    186    186    183
prescan15.tif    1.70    185    188    189    189    189    187   <- the grass frame
```

**Every trial recovered the exact lag** -- 102 of 102 over the full set, 90 of
90 with the residual's donors excluded. Confidence rises monotonically with
detail. Frames 10, 11, 14, 15 and 17 carry the strongest autocorrelation
sidelobes in the set (periods of 67-104 px, i.e. 5.7-8.9 mm, squarely inside
the 9 mm reach) -- the literal grass-and-sky case -- and they are the *highest*
scorers.

**Why the intuition inverts.** Phase correlation normalises every frequency to
unit magnitude before transforming back. It does not measure how similar the
texture *looks*; it measures whether the whole phase spectrum lines up. A grass
field has the densest, broadest phase spectrum available: it is the ideal
input. Texture self-similarity would defeat a plain cross-correlation, which is
precisely the reason `register` does not use one.

### 4. The ambiguity that does exist never competes

For every real same-picture pair, the tallest rival peak more than 3 px away:

| pair | true peak z | best rival | margin |
|---|---|---|---|
| lib f02 pass2v3 | 164.9 | 2.8 | **59x** |
| lib f01 pass2v4 | 128.6 | 3.6 | 36x |
| survey 10v11 | 120.0 | 6.5 | 18x |
| lib f03 pass2v3 | 99.6 | 6.2 | 16x |

Across all 96 same-picture pairs the margin runs **9x to 167x**, and no rival
on any surface exceeds z = 6.5: the entire surface away from the true peak sits
in the same band as a pure null.

### 5. The failure mode is a collapse, not a lie

Fading the most self-similar frame toward featureless, displaced +8 px:

| contrast kept | confidence | recovered | |
|---|---|---|---|
| 1.00 | 161.0 | -8 correct | |
| 0.25 | 67.0 | -8 correct | still verified |
| 0.10 | 29.7 | -8 correct | **refused, though right** |
| 0.02 | 7.4 | -8 correct | refused |
| 0.00 | 10.5 | +105 wrong | refused |

Confidence falls smoothly with content and crosses the floor long before the
answer goes wrong. **There is no regime where the measurement is wrong and
confident** -- which is the only regime worth engineering against, because the
ordinary failure is already benign: cannot measure, so do not move, so scan
anyway and flag loudly. The floor errs toward refusing correct answers, which
is the safe direction.

## What was discredited

**A peak-to-sidelobe ratio**, the obvious alternative. Measurement 4 kills it:
it separates the same cases by a *smaller* margin than `confidence` already
does, and costs a second number nobody can interpret. A naive global PSR is
worse still -- it scored a genuine same-frame repeat at 17.12 and a
same-picture pair at 18.47, overlapping, where `confidence` puts 15x between
them.

**Two synthetic experiments**, recorded because both failed convincingly and
would otherwise be repeated:

- Displacing a frame with `np.roll` and comparing against the original moves
  the *noise* with the content. Everything matches perfectly; every pair scored
  ~210. The residual must be real and independent, or there is nothing to fail.
- Gaussian noise plus a synthetic sensor pattern collapsed *everything* to ~5,
  because phase correlation whitens and synthetic content has energy at only a
  few frequencies. The content must be real film.

Only measurement 3's construction survives both traps: real film, displaced, and
a residual measured from a genuine repeat pair.

## Decisions

- **No second statistic.** See above; recorded so it is not re-proposed.
- **`CONFIDENCE_FLOOR` stays 55**, with the tighter headroom above recorded
  rather than smoothed over: it is 3.9 points below the weakest match the loop
  will meet, so genuine matches will occasionally be refused. That failure is
  benign by construction and the opposite one is not.
- **`SEARCH_MM` stays 9.0.** Every number here is only comparable at that reach.
- **`library.save()` now keeps `meta["registration"]`.** It was dropped from the
  record, so every confidence the driver had ever measured survived only in the
  roll's own `roll.json` -- gitignored and rewritten per frame. One value
  existed on disk when this was written. That is what made the floor
  un-re-fittable, and it is the real gap this investigation found.

## Re-running

    uv run python tools/registration_margin.py                  # the separation
    uv run python tools/registration_margin.py --self-similar   # grass and sky

Exit status is non-zero if any pair that is *not* the same picture scores above
the floor -- the one failure that would move the film wrongly. Run it after any
change to `register`, to the prescan resolution, or to `SEARCH_MM`.

## Still untested

None of the above needs the scanner. The genuinely untested thing is unchanged
by it: **a commissioned multi-frame roll on the hardware**, the only way to
exercise `HOLD_GIVE_UP_FRAMES` and cross-frame backlash.
