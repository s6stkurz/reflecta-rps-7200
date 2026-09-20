# Re-reading the vignette verdict

An audit of `docs/vignette-plan.md`, `rps7200/uniformity.py` and
`tools/uniformity.py`, prompted by "look at the vignette again, independently".

**The operational decision survives. The stated reason for it does not.**

`CLAUDE.md` records "There is no vignette, and no vignette correction should be
added" as settled fact, and the recommendation here is *not* to reverse that.
It is to replace the argument underneath it, which does not hold for this
hardware, with one that does -- and to fix three real defects found on the way.

Nothing in this document required the scanner, and nothing required the
library. It is all re-reading.

---

## 1. The central argument is invalid for a line-scan sensor

`docs/vignette-plan.md:25-26`:

> An optical vignette falls off in both directions; this falls off in neither y
> nor (after correction) x, so the 39% is a lamp and sensor profile, not an
> optic.

and `profile_span`'s own docstring (`tools/uniformity.py:310-314`) makes the
same move, which is how the reasoning reached the code:

> a y number is something the correction could not have produced either way --
> which makes y the axis that carries the news about a genuine 2D vignette.

This scanner has a **linear** CCD. `CCD_MASK_SIZE = 5172`
(`rps7200/framing.py:33`) is one line of elements, and `ShadingReference` holds
`ref: dict[int, np.ndarray]` -- one array per channel indexed by column, with
`pixels_per_line` documented as "the CCD-native width". There is no row
dimension anywhere in the reference, because there is no row dimension in the
sensor.

So the y axis of the image is **transport time**, not field angle. Every scan
line is imaged through the same point of the lens field. A lens vignette on
this device is therefore a function of x only, **constant in y by
construction**.

Which means:

- An optic and a lamp profile predict *exactly the same observation*: falloff
  in x, flat in y. The measurement has no power to separate them.
- A centred x-symmetric falloff is even in x and even in y -- precisely `pp`,
  the component `solve_components` returns as `None` because it cancels out of
  every rotation difference (`rps7200/uniformity.py:675-678`). The rotation
  study is structurally blind to it, and says so honestly.
- Per-column shading removes it identically either way.

"It falls off in x but not y" is not evidence against an optic. It is what an
optic looks like on a line scanner. The study as designed cannot attribute the
39%, and the attribution recorded in `CLAUDE.md` rests on an inference that
does not follow.

### What should replace it

The decision is still right, for a reason the data does support:

> The 39% falloff lives entirely in x. Per-column shading takes it to 1.4%,
> and that holds whatever its origin -- lamp, sensor or lens -- because on a
> line-scan sensor all three are functions of x alone, and a per-column
> reference measured anywhere in y is therefore valid for every y. No
> structure in y was detected above the measurement floor. A vignette
> correction would be a second, redundant x-correction.

That formulation makes the same operational call without claiming to have
separated lamp from optic, which nothing here did.

One real thing it does not cover: a lens falls off in **sharpness** at the
field edge as well as brightness, and shading does not touch MTF. That is out
of scope for a vignette study, but it is the part of "is there an optic" that
remains genuinely open.

## 2. The load-bearing numbers come from twenty lines with no error bar

The four numbers the conclusion quotes are `profile_span`
(`tools/uniformity.py:306-325`) -- median marginal profiles, `max/min - 1`,
with `trim=10`. No fit, no noise estimate, no confidence interval, no
goodness-of-fit anywhere in the function or its callers.

None of the careful machinery -- the Klein-group parity algebra, the robust
degree-4 fit, the re-derived orientations -- contributes to the published
conclusion. `report_flats` prints these numbers for a human; they enter no
programmatic decision (`tools/uniformity.py:340-367`, verdict at `:505`).

Three specific problems:

- **`max/min` of a profile is a pair of extreme order statistics** -- the most
  noise-inflated summary available -- used as the sole evidence for a null.
- **`trim=10` is underived** and removes the ten outermost columns on each
  side, which is exactly where a falloff is largest. It biases the x span
  *down*. It happens to also remove the 2 unshaded trailing columns at 600 dpi,
  which is luck rather than design.
- **x and y are not commensurable.** The x profile spans the half-width
  (18.25 mm at 600 dpi), y the half-height (12.15 mm). For any centred radial
  field the expected y span is smaller by roughly `(18.25/12.15)^2 ~ 2.3` in
  the leading term, before anything else. They are printed side by side under
  "across the CCD (x)" / "along the scan (y)" and read as directly comparable.

Additionally the y profile takes its median **over x**, and in the raw image
that axis carries the 39% falloff. A median over a strongly non-uniform set
lands at a mid-radius, so the y variation is measured along an off-axis chord
where the radial gradient in y is weakest -- attenuating any genuine 2-D
y-dependence further.

## 3. A failed validity check was read as a positive result

The plan sets its own rule at `docs/vignette-plan.md:684-687`:

> Parity residuals must be small relative to the recovered components. If
> `d_fx` carries substantial even-in-x energy, the turned-over passes are not
> clean [...] and the flip-derived components are suspect

The result at `:28-31` reports residuals of **0.687, 0.934, 0.692** and
concludes:

> the parity residuals [...] say those differences are noise-dominated rather
> than carrying a clean field -- which is what a real absence looks like.

The module's own definition (`rps7200/uniformity.py:709-710`) says "1.0 is one
carrying no legitimate signal at all". A residual of 0.934 means 87% of the
surface's energy sits in a parity it cannot legitimately have. By the plan's
stated rule these three differences are *suspect*; they were instead read as
confirming absence.

The correct reading is **"these differences do not constrain the odd
components"**, which is a different claim from "the odd components are zero".

**And there is a likely mechanical cause.** `decompose`
(`rps7200/uniformity.py:645-660`) sends a constant entirely into `pp`, and
`ALLOWED_COMPONENTS` (`:692-696`) excludes `pp` for every orientation --
verified: it appears in none of the three rows. Meanwhile `fit_field` has a
free degree-0 term that absorbs the whole mean log-ratio between two passes.
So **any** exposure or lamp-level drift between passes drives the residual
toward 1.0, and that DC term is exactly what the rest of the analysis
deliberately ignores (`peak_to_peak_percent` is `max - min`, DC-free).

Subtracting the mean before decomposing would settle whether these residuals
are misregistration or just lamp drift. One line; the code does not do it.

Also: the residual is computed on **channel 0 only** and on the unmasked full
grid including the extrapolated corners the rest of the code excludes.

## 4. The table contradicts the sentence above it

`docs/vignette-plan.md:21-24`:

> Shading is a per-column correction: it can only flatten x [...] so it cannot
> have produced y-flatness. y measures 1.1% *before* correction. There was
> never anything there.

The table directly above shows y going **1.1 / 0.8 / 1.0 -> 0.7 / 0.7 / 0.6**.
Shading reduced y by a third. It can: each row's median is taken over columns
that shading rescaled non-uniformly, so a per-column correction does move the
per-row statistics.

Separately, **raw y of 1.1% is below the study's own repeat floor of
1.35-1.52%**. "There was never anything there" converts a null result into a
measured zero. The honest statement is that y-structure, if present, is below
what this study could resolve.

## 5. A real bug: `trailing` is applied to the wrong edge

Verified firsthand. `tools/uniformity.py:186-189`:

```python
turned = un.apply_orientation(a, orientation)
dy, dx, confidence = un.register(turned, b)
ca, cb = un.align(turned, b, dy, dx)
samples = un.block_ratios(ca, cb, dpi=dpi, trailing=trailing)
```

`block_ratios` drops from the right of both arrays
(`rps7200/uniformity.py:459-460`):

```python
if trailing > 0:
    fa, fb = fa[:, :-trailing], fb[:, :-trailing]
```

But `ca` has already been x-reversed for `MIRROR_X` and `ROT180`. The unshaded
columns of `a` are on the **left** of `ca`, so `trailing` discards good columns
and leaves the artefact in place. Correct only for `MIRROR_Y` and the repeat
pairs.

Mitigated in practice -- at 600 dpi `trailing == 2` inside 12 px blocks and the
flatness filter rejects those blocks anyway -- but the plan describes `trailing`
as "defence in depth" (`docs/vignette-plan.md:308-315`), and the defence points
at the wrong edge for the two differences that carry the x-odd information,
which is the axis the conclusion is about. No test covers the mirrored case.

## 6. Two silent failure modes, both triggered by moving the data

These matter now, because a re-analysis means copying entries to another
machine.

- **`ccd_mask.bin` absent** -> `apply_shading` falls back to
  `np.arange(min(w, reference.pixels_per_line))` (`rps7200/shading.py:236-239`),
  mapping output column j to CCD element j. At 600 dpi that is the leftmost 862
  of 5172 elements -- the far edge of the lamp profile stretched across the
  whole frame. No exception, no warning.
- **`shading.npz` absent** -> `rebuild` returns the raw decode with
  `shaded: False` (`tools/uniformity.py:144-150`). One unshaded pass
  differenced against shaded ones carries the full 39% into the result. The only
  signal is the word `RAW` in a log line.

`tools/collect_vignette_study.py` refuses an entry missing either, rather than
copying it, and verifies `raw.bin.gz` against its stored digest.

## 7. Smaller findings

- **Span and floor use different hard-coded channels.** `parity_residual` reads
  `surface[:, :, 0]` (R), `span` reads `surface[:, :, 1]` (G)
  (`tools/uniformity.py:202`, `:207`). No comment, no constant. The repeat floor
  is therefore green-only, and `threshold = max(3*floor, 0.5)` is applied to R,
  G, B and I alike -- a G-derived error bar on blue, whose SNR the plan itself
  flags as poor.
- **Shading guarantees a small corrected x number.** Every column is divided by
  its own reference and multiplied by the global mean, so a small corrected x
  span is what the operation is defined to produce. "39% -> 1.4%" demonstrates
  that the division worked, not anything about the field's origin.
- **Registration confidence is computed, printed and never gated on.** A failed
  registration yields a confident fake field and the run proceeds. The shift is
  also never checked against `max_shift=64`, so a shift pinned at the search
  boundary is indistinguishable from a clean lock.
- **The clipping count is discarded.** `apply_shading` returns `clipped`;
  `rebuild` reads only `width` and `columns`. The plan explicitly instructs
  checking it. Clipping flattens the peak of a falloff -- biasing toward the
  conclusion.
- **No goodness-of-fit anywhere.** Every reported number is the peak-to-peak of
  a fitted surface. A fit that describes the data badly and a genuinely flat
  field produce the same small span.
- **`support_box` is a bounding box, not a hull** (`:632-634`), so corner
  extrapolation is still permitted.
- **The flatness filter is level-dependent.** `rsd = std/mean` on raw counts
  means Poisson noise alone gives 0.07 at 200 counts against a 0.10 budget, and
  `keep = flat.all(axis=2)` lets the noisiest channel decide for all of them.
  Surviving blocks are biased toward bright patches, and the fit is unweighted.
- **Robust IRLS attenuates edge-confined signal.** Huber downweighting plus a
  total-degree-4 basis is biased against a falloff confined to the outer
  10-15% of the frame -- the shape being looked for. The docstring justifies
  degree 4 against the opposite risk only.
- **`claimed_orientation` substring-matches `"180"`**, which collides with
  `"1800 dpi"` (`tools/uniformity.py:436-438`). The plan's passes 10-11 are
  specified as *"180 at 1800 dpi"*. Latent.
- **`dpi` silently defaults to 600** on a missing `resolution_dpi`
  (`tools/uniformity.py:455`), which silently changes `block_size` and the
  entire flatness filter. Should raise.
- **Dead code the documentation relies on.** `log_image` is called by nothing
  but a test, yet the module docstring's most emphatic warning points at it;
  `channel_report` is never called, so the channel sanity check the plan
  required does not run; `Field.save`/`load` are never called, so no field is
  ever written to disk.
- **The 1800 dpi resolution control was dropped.** The plan specified 11 passes;
  9 were executed. At 600 dpi the mask samples 860 of 5172 CCD elements, so a
  fixed-sensor-column effect is aliased.

## 8. What an independent recalculation would settle

Not yet run: **the library is not on this machine**. `tools/collect_vignette_study.py`
gathers what it needs.

Two hard gates first, so that a disagreement is a finding rather than a bug in
the re-implementation:

1. An independent decode of `raw.bin.gz` must reproduce `scan.tif` exactly.
2. An independent shading must reproduce `library.corrected(entry)` exactly.

Then the questions this audit cannot answer by reading:

- A **2-D radial fit** to the empty-transport flat, rather than two marginals,
  with a residual statistic -- does any structure survive that is not a pure
  function of x? This is the question that actually decides whether per-column
  shading is sufficient, and no existing code asks it.
- **Centre-line profiles** (`f[h//2, :, c]`, `f[:, w//2, c]`) beside the median
  marginals, to size the off-axis-chord attenuation in §2.
- **Parity residuals recomputed with the DC term removed**, to decide §3.
- The **repeat floor per channel** rather than green-only.
- The same x-profile measured **in bands of y** -- the one shape that a
  per-column reference cannot correct, and the one both marginals are blind to
  by construction. The calibration frame is `y 3431..6888` and a scan is
  `y 0..6887`, so this is also the test of whether measuring the reference on
  the lower half is safe.
- Fixed-sensor-column effects across entries from **different film positions and
  different days**, which is what separates sensor from picture -- hence the
  spread of non-study entries the collector includes.

## 9. Recommended changes, not yet made

`CLAUDE.md:334-341` and `docs/vignette-plan.md:25-26` state the lamp-not-optic
attribution as settled. Changing a load-bearing convention file is Stefan's
call, so this document records the finding and stops there.

The suggested edit is narrow: keep the rule ("no vignette correction should be
added"), replace the reason with §1's formulation, and drop "an optic falls off
in both directions" -- which is true of an area sensor and false here.

The `trailing` bug (§5) is an ordinary fix and needs no decision.
