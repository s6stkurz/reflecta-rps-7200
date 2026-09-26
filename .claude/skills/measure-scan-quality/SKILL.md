---
name: measure-scan-quality
description: Measure noise, column defects and pass-to-pass agreement in scans from this driver. Use when judging whether a correction helped, hunting coloured lines or stripes, comparing scans, or deciding whether more passes are worth the scanner time. Carries the metrics that work here and the ones that have repeatedly lied.
---

# Measuring scan quality

Most of the wrong turns on this driver came from a metric, not from the code it
was judging. A metric said "corrected" where Stefan could see lines; another
read 96-169% on one frame and 2% on another and meant nothing either time. What
follows is what survived being checked against his eye and against repeat scans.

`scripts/metrics.py` beside this file has all of it as functions. Import it
rather than re-deriving:

    import sys; sys.path.insert(0, ".claude/skills/measure-scan-quality/scripts")
    from metrics import dark_mask, relative_noise, noise_split, agreement_z, colour_deviation

## Finding coloured lines

**Use signed, channel-relative deviation.** A visible line is one channel
departing from the others, so subtract the mean deviation across channels and
keep the sign:

    colour_deviation(image)      # (3, W), signed, channel-relative

`np.abs` destroys this: a violet line and a green one are opposite deviations
and absolute values make them look like two ordinary bumps. Measuring channels
independently cannot express "green here, violet there" at all. Both mistakes
were made here, and both hid 5-10% defects that a colour-opposed metric found
immediately.

**Never use "worst column deviation" as a quality figure.** It is dominated by
picture content -- a hard vertical edge reads 96-169%, a flat frame 2%, and
neither says anything about the scanner.

## Separating the sensor from the picture

A sensor defect sits at a fixed **sensor** column across *different film
positions*; picture content sits at a fixed **film** position. Two library
entries from different frames settle it, and nothing else does. Beware
coincidence: check the *sign* matches too. Four columns once appeared in both
frames carrying opposite tints, which is chance, not a defect.

The other discriminator, within one frame: a fixed pattern reproduces between
the top and bottom halves. Shading correction took red from r=0.897 to 0.265
that way. Content-heavy frames defeat it -- full-height vertical structure
correlates too -- so prefer the cross-frame test when a second frame exists.

## Noise: what can actually be removed

**Only the random part.** Two scans at one exposure differ solely by what is
random per pass; grain, detail and fixed pattern cancel -- **once the two are
registered** (`docs/multi-exposure/code/passes.py`, archived). Unregistered,
the difference also holds the grain shifted against itself, and "random" comes
out inflated: a x4 repeat pair 2.3 lines apart read a random share of 334%.

    rnd, total, share = noise_split(repeat_a, repeat_b, mask)

The share depends on resolution. On a slide it was 21% at 300 dpi and 27% at
1800, a ceiling of about -3.5% on *any* multi-pass method -- but 53-66% at 3600
dpi on registered pairs, a ceiling of -13% to -22% for nine passes, where a pair
of registered repeats achieved -1.6% to -5.6% against the better of the two.
**Compute this ceiling at the resolution you mean to use, before spending
scanner time**, because it decides whether the experiment can succeed at all:

    ceiling(rnd, total, n_passes)     # -3.5% at 1800 dpi, -13 to -22% at 3600

A 25-minute bracket was run to confirm a ceiling a 4-minute repeat pair had
already given. And a ceiling is not a verdict: the -3% to -7% that registered
merging actually reached at 3600 dpi was invisible side by side at 100%, which
is why the whole study is archived (`docs/multi-exposure/`).

## Comparing scans taken at different exposures

**Solve the relation from the pixels; never trust the commanded exposure.** At a
requested x4.000 the fitted slope was 3.828 with a 1279 DN intercept.

    slope, intercept = solve_relation(a, b)    # metrics.py, per channel

Then agreement in sigma, not DN -- an absolute threshold means different things
at different exposures:

    agreement_z(a, b, mask)     # median |z|; two repeats give ~1.03

Anything near the repeat baseline is consistent with noise. **Register the
passes before comparing them.** This file used to say agreement "holds to about
x1.7 and collapses by x3.7" -- it was the carriage, not the exposure. The
bracket was shot in ascending order and the passes drifted 2.4 lines over it;
registered, its x3.84 pass agrees at 1.30 where unregistered it read 5.65, and
the x4 pass of a three-pass run sits 0.1 line from its first.
`docs/multi-exposure/analysis/merge_library.py` prints both, before and after.

And compare noise **on one scale**. `relative_noise` divides by the mean, and a
longer pass's mean carries the offset `solve_relation` finds (377 DN in green at
x4), so on its own scale it reads a few percent quieter than it is. Scale every
candidate onto the reference with the per-channel fit first.

## Rules that cost something to learn

- **Measure the file you delivered, never a recomputation of it.** A round was
  lost analysing a recomputed array that was clean while the shipped file had a
  40-column colour ramp -- the artefact was in the write path, which the
  recomputation skipped. Read back from disk.
- **Send 100% crops, not downscaled previews.** A third-size preview cannot show
  a one-pixel line, which is part of why several were missed.
- **Stefan's eye decides.** Report numbers alongside, but when they disagree with
  what he can see, the numbers are wrong. That has been true every time so far.
