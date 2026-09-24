# Recalculating the vignette, independently

## Status: analysis done, nothing changed. 2026-09-20, branch `analysis/vignette-recalc`.

Seven independent analyses, all offline from stored raw bytes and shading
references, none of them reusing `rps7200/uniformity.py` or `tools/uniformity.py`
for the measurement. Scripts are in the session scratchpad; every number below was
recomputed from `library/`, and the published figures were reproduced exactly
before anything was questioned.

## The answer, in one line

**The decision is right and almost every stated reason for it is wrong.** Do not
add a vignette correction. But "there is no vignette" is not what was measured,
the y-axis argument is invalid for this geometry, the only error bar the study has
is inoperative, and the headline 1.4% residual describes an empty transport rather
than anything an operator is delivered.

---

## 1. The new finding: 1.4% is an empty-transport figure, not a delivered one

This is the only item here that changes what an operator gets, and it is the one
thing nobody had looked for. Its *cause* is open -- see below, where two attempts
to attribute it both failed.

`docs/vignette-plan.md` and `CLAUDE.md` both say shading takes the ~39% falloff
down to **1.4%**. That figure is measured on the **empty transport**, at 45-48k DN
— the brightest scene this device ever sees. Put a clear film base in the light
path at the *same exposure* and the same measurement gives:

| | level DN | corrected x span | residual in DN |
|---|---|---|---|
| empty transport | 44782 / 48189 / 47004 | 0.48 / 0.67 / 0.42 % | 214 / 322 / 197 |
| clear film | 13027 / 6340 / 3405 | **2.64 / 4.62 / 7.56 %** | 344 / 293 / 258 |

The percentage moves **18x**; the residual expressed in DN stays inside a factor
of 1.7. And 197-344 DN is the same order as the dark level (160-220 DN).

**The "additive" reading of that is not established, and two attempts to
establish it have failed.** Written up 2026-09-20 after the plan was approved:

- Synthesising darker scenes from the flat's own pixels and re-correcting them
  keeps the residual at a constant *percentage* (0.48/0.67/0.42% from full level
  down to 1/14 of it). That test is **circular** and proves nothing: the synthetic
  scene was built with the same per-column dark the correction then subtracts, so
  `corrected = k x corrected_0` is an algebraic identity and the span, a ratio, is
  invariant by construction. It cannot see a dark *mismatch*, which is what the
  additive hypothesis actually proposes.
- The rotation discriminator is inconclusive. Splitting each corrected residual
  profile about its centre, the clear-film residual is dominated by its **even**
  part (RMS 0.53/0.88/1.36% even against 0.15/0.43/0.84% odd), and an even function
  looks identical after a 180-degree flip — so rotation cannot attribute most of
  it. The odd part disagrees across channels: R flips (r = -0.609, film-like) while
  G and B stay (r = +0.439, +0.864, sensor-like).

So the cause is **open**. It may be the film base's own x non-uniformity, a
correction artefact at low signal, or both. What is *not* open is the delivered
measurement: the corrected residual on a clear film base is 4-18x the
empty-transport figure (total RMS 0.55/0.98/1.60% against 0.13/0.16/0.09%), and
that is read off delivered files without any attribution assumed.

**Consequence, stated only as far as the evidence goes: "39% down to 1.4%"
describes an empty transport, and an operator scanning film is not delivered that
figure.** With a clear base in the path the residual is 2.6-7.6%, worst in blue.
Whether the extra comes from the film or from the correction is unresolved, so the
honest claim is about what is delivered rather than about what causes it.

Either way it does not resurrect the 2-D question and does not argue for a vignette
correction: a residual that changes with each insertion — the two clear-film passes
disagree by 1.3x in blue, and the 2-D test found their non-separable structure
disagreeing by 1.7x — cannot be fixed by any static table.

## 2. What the code actually tested, and what was claimed

`tools/uniformity.py` prints this itself:

```
  pp (even, even)  NOT MEASURABLE by rotation -- a centred radial ...
  note: this rules out an *asymmetric* field only. A centred radial
        vignette would read exactly zero here.
```

The rotation method is blind to a centred radial field by construction. The plan
(`docs/vignette-plan.md:184-200`, `:636-638`) specified a second route to it — fit
each flat, extract its odd components, cross-certify against the IT8's target-free
ground truth, then read off `pp`. **That was never written.** `report_flats` puts
the flats through `profile_span` alone; `decompose`/`fit_field`/`solve_components`
run only on the IT8 set. There is no `report` subcommand and the specified
heatmaps were never produced, so the visual output Stefan judges by eye was never
generated for this study either.

So `pp` — the one component that *is* a centred vignette — **has no bound anywhere
in the study**, and "there is no vignette" is stated one logical step beyond the
evidence.

Fitted offline, it is weakly *detected* rather than excluded: a centred quadratic
radial term on the corrected empty transport is significant at t = -9 to -12 per
channel, 0.59-0.67% frame span, and after dividing out the x profile there is a
centre-versus-edge dome along y of +0.11/0.14/0.12% (per-bin error +-0.008-0.019%).
Small enough that "add no correction" stands; large enough that the claim as
written is unsupported.

## 3. The y-axis argument is invalid, and it is the load-bearing one

`CLAUDE.md` and `docs/vignette-plan.md:21-26`: *"The y column is what makes this
conclusive... An optical vignette falls off in both directions; this falls off in
neither."*

This is a **trilinear line-scan CCD** — R/G/B on separate photosite rows
(`rps7200/defects.py:1-13`), each split into two sub-rows staggered 4 lines along
the scan direction (`rps7200/direct.py:1504-1517`). The lens images **one line**,
at one fixed field height, and y comes from carriage travel. Under rigid
translation the optical term is therefore a function of **column only**, and its
predicted y falloff is exactly **0%**, not the 16.5% a cos⁴ fit would give if y
were a field coordinate.

**So flat-in-y is what an optic predicts here.** It cannot discriminate an optic
from a lamp, because both are pure column functions on this geometry and
per-column shading removes column functions. The y axis is the one axis that
cannot carry the news, and the argument should be withdrawn rather than repaired.

Related mental-model slip worth fixing: `rps7200/framing.py:26-28` calls it "this
scanner's 10344 x 6888 CCD". 6888 is the scan-line count, not a sensor row count,
and that area-sensor picture is exactly what makes the y argument feel right.

The geometric assumption the above rests on — translation rather than a pivoting
mirror — is inferred from `dx = 0` across passes and whole-line-only y shifts, and
is **not documented**. It is testable for free: measure the IT8 patch pitch at the
top versus the bottom of an existing study pass. Constant magnification proves
rigid translation.

## 4. The evidence that does support the conclusion, which the repo does not cite

The falloff is **chromatic and asymmetric, reproducibly**, across 11 independent
calibration sessions spanning three weeks:

- span R 33.9-39.3%, G 30.2-35.9%, B 29.6-33.4% — **R > G > B in every session**.
  R-B = +5.41 +- 0.34 pp, i.e. 16 sigma on a single measurement.
- profile peak position differs per channel by about 1.8 mm (R at u ~ +0.10,
  B at ~0.00).
- mirror asymmetry about the frame centre: R 3.7-5.3%, G 1.8-3.4%, B 0.6-2.0%.
- infrared spans 57.5% against green's 33.7%, and is not the visible dome
  rescaled: fitting IR = dome^k gives k = 0.87 with a 4.35% residual.
- the profile shape is ~99.5% static over three weeks (r >= 0.995 between
  sessions), so most of it is not a lamp thermal effect.

Geometric vignetting is **achromatic and symmetric about the axis**. It cannot
produce a 5-point channel-dependent span difference, a channel-dependent peak
shift, or channel-dependent asymmetry. That is a real argument against an optic,
and it is the one the repo should be making.

**But it does not go as far as the repo's wording either.** Decomposing each
reference into a shared achromatic dome plus per-channel residual gives a dome of
33.8 pp against a total of 37.5 pp — so **~90% of the amplitude is achromatic and
~10% is genuinely spectral**. The achromatic 90% is what a lens vignette looks
like *and* what an extended lamp's geometric end-falloff looks like, and nothing in
the library separates them: the reference measures lamp x optic x QE as one
multiplicative per-column term. The repo is no more entitled to "it is a lamp
profile" than to "it is an optic".

What *is* settled is the sensor's share. The dark reference moves the span by
**+0.25 to +0.34 pp out of 37%** and its shape is anti-correlated with the falloff
(-0.31/-0.01/-0.25). So **"lamp and sensor profile" should be "the illuminated
light path"** — the sensor's own response is about 1% of it.

## 5. The 2-D test the study never ran comes back clean, with power

A two-way median polish in log space over (y-band x column). Its interaction term
is blind to any per-column gain — a per-column gain is additive in x in the log
domain — so the test is valid on raw pixels and needs no reproduction of the
correction. This is the one field shape a per-column reference cannot fix, and
both marginals average it away.

| | interaction | floor | ratio |
|---|---|---|---|
| R | 0.1527% | 0.1502% | 1.02x |
| G | 0.1409% | 0.1373% | 1.03x |
| B | 0.1257% | 0.1217% | 1.03x |

Two independent floors (row-permutation null, interleaved odd/even half-split)
agree to 3%. The excess converts to **~0.2% peak-to-peak** change in the x profile
between top and bottom of frame — about 1/7 of the post-shading residual and
1/160 of the falloff. Most of even that is a lamp warm-up transient in the first
100 rows: drop them and R and G go to a complete null.

The test has demonstrated power, which is what makes the null worth anything:
injecting 0.2% p-p is detected at z = +23, and the same estimator on an IT8 pass
returns 147-159%. It also found real 2-D structure in the clear film (1.4-3.9%
p-p) and then showed it is the **film and holder**, not the instrument — the empty
transport does not show it, two insertions of the same strip disagree by 1.7x, and
the between-pass correlation is only +0.03/+0.06/+0.23.

The plan's stated blind spot — a reference measured over `CALIBRATION_FRAME`
(y 3431..6888) being wrong elsewhere — is a real logical gap and **empirically
empty**: measured band by band on the delivered file, a reference from the lower
half works marginally *better* in the upper half.

**So per-column shading is sufficient, now on evidence rather than on the absence
of a test.**

## 6. The study's error bars do not work

**The parity residual check is inoperative.** `_basis` includes the degree-0 term,
`decompose` sends any constant entirely into `pp`, and `ALLOWED_COMPONENTS`
excludes `pp` for every orientation. So any lamp drift between passes drives the
residual toward 1. Demonstrated: a *perfect* x-odd field plus the measured drift
gives residuals of 0.169 / 0.652 / 0.864 at 0.1 / 0.5 / 1.0% drift — which
brackets all three published values (0.687 / 0.934 / 0.692). De-meaning the
differences drops them to 0.343 / 0.668 / 0.307.

The check can therefore never flag a real problem. Worse, the doc reads the same
three numbers **both ways in the same file**: `:684-687` says residuals that large
make the components suspect, `:28-31` reads them as "what a real absence looks
like". Two further defects on the same line: the printed residual is **red only**
while the printed span is **green only**, and green's residuals (0.953/0.978/0.987)
were never printed at all.

**The "repeat floor of 1.35-1.52%" is a drift, not a noise floor.** Both floors are
measured against the first IT8 pass, which is anomalous: `duration_s` **15.0 s
against 13.2 s** for all five other IT8 passes, and 0.6-1.1% dimmer in a shared
smooth field. The two difference surfaces correlate +0.67/+0.86/+0.94. The honest
machine floor is the two non-base passes against each other: **0.477%**.

That collapses the margin. Threshold `max(3 x floor, 0.5)` falls from 4.57% to
**1.43%**, and the largest recovered component (1.20%, blue) clears it by **1.19x
instead of 3.8x**. The verdict does not flip; the comfort does.

And the floor is **green only** yet applied to every channel. Per-channel floors
are R 1.22/0.60, G 1.53/1.35, B 1.98/2.04 — blue's own threshold should be 6.11%.
The honest sensitivity statement is "no asymmetric field above 4.6% (G) / 6.1%
(B)", not "components of 0.28-1.20% sit below a floor of 1.35-1.52%".

**The threshold is never applied to the flats at all**, and the flats are what the
conclusion rests on. There is exactly one empty-transport pass, so its 1.4% / 0.7%
has no repeat and no floor of any kind.

## 7. The y table row is an estimator artefact

`profile_span` takes the y profile as a **median across x** while x still carries a
40% falloff — an ill-conditioned estimator over a broad distribution. Re-measured
three ways on the empty transport:

| y span | raw | corrected |
|---|---|---|
| median over x (published) | 1.12 / 0.85 / 1.00 % | 0.69 / 0.71 / 0.59 % |
| **mean** over x | 0.91 / 0.85 / 0.85 % | 0.93 / 0.85 / 0.87 % |
| median, after dividing out x | 0.73 / 0.70 / 0.56 % | 0.73 / 0.71 / 0.56 % |

**Shading changes the y non-uniformity by nothing at all** — exactly as the
per-column argument requires. The apparent 1.1% -> 0.7% improvement is the
estimator, not the correction. The sentence in the doc is right; the table's y row
is wrong, and the specific figure the argument leans on is inflated about 1.5x.

Fixing it *strengthens* the doc: the correct row is ~0.7%, unchanged by
correction, which is a cleaner statement of the same point.

The raw y figure is also mostly noise — a noise-only expected span is 0.78-0.84%
against the published 1.12/0.85/1.00% — so it cannot exclude a y falloff below
about 1%. `profile_span` additionally has no error bar, uses `max/min` (an extreme
value statistic that only overstates), silently excludes infrared via
`range(min(3, shape[2]))`, raises on `trim=0`, and moves by 2-3x on the trim
parameter: corrected y reads 0.69% at trim=10, 1.00% at 5, 1.47% at 2, 2.34% at 0.

A better argument is available and nobody used it: the x profile's peak sits
reproducibly at column 425-459 across all three flats and three channels, while
the y peak wanders over rows 17-548 and the fitted curvature flips sign between
entries. **A dome is centred and reproducible in both axes; in y it is neither.**
And the y structure, binned after removing x, is a **monotonic ramp** of
0.3-0.5%, not a dome — the signature of a temporal drift during the pass, not of
geometry. `max/min` of a marginal returns the same number for a ramp and a dome,
which are physically opposite answers.

## 8. Smaller findings

- **A real field written up as nothing.** The `pm` component (odd in y) measured
  0.57/0.52/0.41% and was declared "nothing above the floor" against a 4.57%
  threshold. The empty transport's y residual after removing x is a monotonic ramp
  of ~0.5% — the same number by an independent method. Reproducible,
  cross-confirmed, buried under a threshold 10x its size.
- **`trailing` is applied after `apply_orientation`** (`tools/uniformity.py:185-188`),
  so for `MIRROR_X`/`ROT180` the unshaded columns move to the left while the code
  trims the right. Demonstrated to inject a 4.2% artefact at the leftmost block on
  a synthetic flat — and it has **never executed**: `trailing` is 0 for all nine
  study entries and for all 198 library entries carrying a shading report, at all
  14 resolutions. The docstring justifying it ("at 600 dpi the mask marks 860 used
  pixels while the pass is 862 wide") is false against every entry in the library.
- **Failure paths that return a successful-looking answer.** An empty support mask
  returns 0.0, so `decompose_and_report` prints "nothing above the floor" — the
  confident answer — for a measurement with no data. With no repeat pass,
  `floor = 0.0` gives `threshold = 0.5%` and the tool proceeds to a full verdict
  after a warning. `registration_confidence` is computed, rounded, recorded and
  **never gated on**, despite `register`'s docstring calling it not optional.
  `dpi = meta[base]["dpi"] or 600` silently invents a resolution, which silently
  changes `block_size`.
- **Two real defects unrelated to vignetting**, both in the delivered file: rows
  0-10 are 1.5-2.2% dim (scan start-up, settling by row 15) and the last row is
  3.3-7.3% dark. These are y-*level* errors, so a per-column reference cannot
  address either. The untrimmed raw y figure of 3.9/7.7/4.9% is entirely these
  rows.
- **`rebuild` does decode from raw bytes** as its docstring claims. But the plan's
  requirement to warn if the rebuild differs from the stored `scan.tif` was never
  implemented, so a divergence between what is analysed and what the pipeline
  writes would go unnoticed — the "measure the file you delivered" hazard.

---

# The plan

Ordered by consequence. Nothing here needs the scanner except the last section,
and that section is optional.

## P1 — Characterise the level-dependent residual (§1). Offline.

The only finding with operator-visible consequences. Before any fix:

1. Measure the corrected x residual against light level across every library
   entry that has a reference, not just the three flats — plot residual-in-DN
   against level and establish whether it really is constant in DN across
   resolutions, films and sessions, or whether the three flats are a coincidence.
2. Test the additive hypothesis directly: the residual should scale as 1/level.
   From the two flats it explains R within 1.6x, G within 0.9x, B within 1.3x —
   close but not exact, so something else contributes. Identify it before fixing
   anything.
3. Check whether the dark reference is the culprit: the dark term is measured at
   one gain and offset, and `SET GAIN OFFSET` does not persist across a scan
   sequence. A dark measured under different settings than the pass it corrects
   would produce exactly this.
4. Only then decide whether a fix is warranted, and what it would be. A candidate
   is re-deriving the dark term per pass rather than per calibration, but that is
   a hypothesis, not a plan.

**Deliverable: a number for what an operator is actually delivered at film
levels, per channel, and a yes/no on whether the correction should change.**

## P2 — Bound `pp`, the component nobody measured (§2). Offline.

Either implement the flat cross-certification the plan specified, or adopt the
offline radial fit as the bound and say so. The second is cheaper and probably
sufficient: it gives 0.59-0.67% with a stated error, which is all the conclusion
needs. What is not acceptable is leaving "there is no vignette" resting on a test
that prints "a centred radial vignette would read exactly zero here".

## P3 — Correct the documentation (§2, §3, §4, §7). No measurement needed.

**This is Stefan's call — `CLAUDE.md:334-341` is the stated conclusion and I have
not touched it.** What the analysis says should change:

- Withdraw the y-axis argument entirely. It is invalid for a line-scan sensor, and
  it is currently called "what makes this conclusive".
- "lamp and sensor profile" -> "the illuminated light path": the sensor is ~1%.
- "there is no vignette" -> "no correctable 2-D residual": separate the measured
  claim (true, and what matters) from the optical one (not established).
- Replace the y table row with the mean-over-x figures, which are ~0.7% unchanged
  by correction and make the argument better.
- Add the chromatic and peak-position evidence (§4), which is the actual argument
  against an optic.
- State the sensitivity honestly: "no asymmetric field above 4.6% (G) / 6.1% (B)".
- Fix `rps7200/framing.py:26-28`'s "10344 x 6888 CCD".

## P4 — Repair the measurement path (§6, §7, §8). Offline, with tests.

In descending order of how much each one misleads:

1. De-mean each difference before decomposing, or exclude the degree-0 term from
   the basis. Report every channel's residual, not red's. Until then the residual
   carries no information and should not be quoted.
2. Take the repeat floor from two passes *after* warm-up, never from the first
   pass in a session. Use per-channel floors against per-channel components.
3. Give `profile_span` an error bar, a declared trim, a mean option for the y
   marginal, and the infrared plane. Make `trim=0` work.
4. Make the failure paths fail: an empty support, a missing repeat and a collapsed
   fit must not return values that read as successful measurements. Gate on
   `registration_confidence` or delete it.
5. Move `trailing` before `apply_orientation`, and fix its false docstring. It has
   never executed, so this is cheap and pre-emptive.
6. Implement the plan's rebuild-versus-`scan.tif` warning.

## P5 — The two defects that are not vignetting (§8). Separate work.

The scan start-up ramp (rows 0-10, 1.5-2.2%) and the dark last row are real in
delivered files and cannot be fixed per column. They deserve their own
investigation and their own decision; folding them into the vignette question is
what let them sit unnoticed inside a "y measures 1.1%" figure.

## P6 — Optional hardware, and only with Stefan's agreement.

None of the above needs the scanner. These would settle attribution rather than
the decision:

- **The deferred Phase 2 infrared flat** is the strongest available discriminator:
  geometric vignetting is wavelength-independent and a lamp+filter+QE profile is
  not. `tools/uniformity.py capture --ir --tag vignette-study-ir` is already
  built. Note the plan defers it because "RGB turned out to have no field to
  differ from", which is unsound — RGB has a 34-39% raw field; what it has no
  *residual*.
- **Two empty-transport flats at different lamp thermal states** — one right after
  warm-up, one after ~30 minutes idling — split the lamp term from sensor+optics.
  Two 600 dpi passes. Requires an empty transport, which only Stefan can confirm,
  and note that calibrating an empty transport is itself a documented hazard.
- **A second empty-transport pass**, simply so the flat that carries the headline
  numbers has a repeat and therefore a floor. One 24 s pass, and it closes the
  main gap in §5's null result.

## What would change the decision

Stated plainly, because these are different claims:

- An x-only falloff that shading removes at all light levels -> the decision and
  the numbers stand. **This is not what was found:** §1 shows the removal is
  level-dependent.
- A per-channel profile that is achromatic -> "not an optic" is wrong, though the
  correction still works. **Not found:** R > G > B at 16 sigma in 11 sessions.
- An x-profile whose shape changes with y -> per-column shading is insufficient
  and a real 2-D correction is needed. **Not found:** interaction at 1.02-1.03x
  the noise floor, with a test shown to detect 0.2%.

So: no vignette correction, and the one thing that does need work is the
correction's behaviour at real film levels.
