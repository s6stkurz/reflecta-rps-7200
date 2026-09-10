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
random per pass; grain, detail and fixed pattern cancel:

    rnd, total, share = noise_split(repeat_a, repeat_b, mask)

Measured on a slide here the random share was 21% at 300 dpi and 27% at 1800 --
so three quarters of what looks like shadow noise is grain, and the ceiling on
*any* multi-pass method was about -3.5%. **Compute this ceiling before spending
scanner time**, because it decides whether the experiment can succeed at all:

    ceiling(rnd, total, n_passes)     # e.g. -3.5% for n=9

A 25-minute bracket was run to confirm a ceiling a 4-minute repeat pair had
already given.

## Comparing scans taken at different exposures

**Solve the relation from the pixels; never trust the commanded exposure.** At a
requested x4.000 the fitted slope was 3.828 with a 1279 DN intercept.

    slope, intercept = solve_relation(a, b)    # from rps7200.bracket

Then agreement in sigma, not DN -- an absolute threshold means different things
at different exposures:

    agreement_z(a, b, mask)     # median |z|; two repeats give ~1.03

Anything near the repeat baseline is consistent with noise. On this scanner
agreement holds to about x1.7 and collapses by x3.7.

## Rules that cost something to learn

- **Measure the file you delivered, never a recomputation of it.** A round was
  lost analysing a recomputed array that was clean while the shipped file had a
  40-column colour ramp -- the artefact was in the write path, which the
  recomputation skipped. Read back from disk.
- **Send 100% crops, not downscaled previews.** A third-size preview cannot show
  a one-pixel line, which is part of why several were missed.
- **Stefan's eye decides.** Report numbers alongside, but when they disagree with
  what he can see, the numbers are wrong. That has been true every time so far.
