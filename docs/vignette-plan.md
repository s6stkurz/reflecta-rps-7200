# Deciding whether this scanner has a vignette

## Result: there is no vignette. Measured 2026-08-30.

Nine passes at 600 dpi RGB, filed under tag `vignette-study`. The delivered
image is uniform to about 1.5%; what looks like a vignette is a per-column lamp
profile that two-point shading already removes.

The empty transport settles it. With nothing in the light path `T = 1` exactly,
so the corrected image *is* the scanner's field -- no target to disentangle, and
none of the blind spot that limits the rotation method:

| empty transport | across the CCD (x) | along the scan (y) |
|---|---|---|
| raw | **39.0 / 35.9 / 34.6 %** | 1.1 / 0.8 / 1.0 % |
| corrected | **1.4 / 1.5 / 1.1 %** | 0.7 / 0.7 / 0.6 % |

There *is* a ~39% falloff, and it would pass for a vignette. But it lives
entirely in x, and shading takes it to 1.4%.

**The y column is what makes this conclusive.** Shading is a per-column
correction: it can only flatten x, and it is measured over `CALIBRATION_FRAME`
(y 3431..6888) while a scan covers y 0..6887 -- so it cannot have produced
y-flatness. y measures 1.1% *before* correction. There was never anything there.
An optical vignette falls off in both directions; this falls off in neither y nor
(after correction) x, so the 39% is a lamp and sensor profile, not an optic.

The IT8 rotation agrees and adds nothing alarming: every recovered component
(0.28-1.20%) sits below the repeat floor (1.35-1.52%), and the parity residuals
(0.687, 0.934, 0.692) say those differences are noise-dominated rather than
carrying a clean field -- which is what a real absence looks like.

**Ignore the clear-film numbers** (3.5-8.8%). Its levels came back
`13005 / 6329 / 3393`: that is colour *negative* base with its orange mask, not a
neutral clear slide. The span is the film's own non-uniformity plus poor blue
signal-to-noise, and it is precisely the ambiguity that sank `scanner_corrections`
in 2b790dd. The empty transport has no such ambiguity, which is why it is the
one to believe.

**No correction is warranted, and adding one would be a regression.** This repo
has already made a scan worse once (`worst_defect` 4.47% -> 5.28%) by correcting
a falloff it could not attribute.

### Phase 2 (infrared) is deferred, not abandoned

Not run, and on this evidence not needed. Phase 2 could only ever ask whether the
field *differs* in infrared, and RGB turned out to have no field to differ from --
so the likely answer is "no difference from nothing". It is also the expensive
half: infrared holds the device busy for a ~212 s floor per pass whatever the
resolution, so five passes is ~18 minutes that 600 dpi cannot shorten. And the
infrared plane is a dust and scratch record rather than a picture, where a smooth
brightness gradient does not matter in the first place.

The code is built and tested, so this is a decision to defer rather than work to
redo. `tools/uniformity.py capture --ir --tag vignette-study-ir` runs it, IT8
only, nothing in the transport changing. Worth reaching for only if infrared ever
starts behaving oddly in a way a smooth field would explain -- and the guards
below still apply if it does, particularly the one about an infrared plane that
came back unshaded.

### What the real data corrected in this plan

Four things below were written from synthetic reasoning and were wrong on
hardware. They are fixed in the code and kept here because each was a plausible
mistake:

- **Block flatness** was calibrated on noiseless synthetic patches at 2% relative
  standard deviation. Real patch interiors run 3.5-6% and borders 25%+, so 2%
  kept **7 blocks of 3337** and made every fit meaningless. Now 10%, set from the
  gap between the two measured populations.
- **The polynomial extrapolated past the target.** An IT8 covers about u
  -0.96..0.94, v -0.90..0.90, and a degree-4 surface evaluated over the full
  frame invents corners: 2.32% against 1.42% where blocks actually were. Spans
  are now reported over the fitted support only.
- **The reference pass was chosen by position**, and entries sort
  alphabetically, so `180` preceded `as-is` and became the origin, inverting
  every other label. It is now chosen by claim.
- **Orientation detection needed a second gate.** Monotonicity does not separate
  the populations: a real IT8 greyscale row scores |rho| 0.834-0.838, while film
  negatives in `library/` reach 0.934. The greyscale row is a *staircase*
  (step fraction 0.012-0.017) and a picture gradient is not, and the margin over
  the next band separates better (IT8 0.374-0.386, negatives <= 0.309). Even so
  no single-image statistic is strong here, so the real gate is set-level: six
  passes of one target must give exactly the four (edge, direction)
  combinations.

### And one thing the plan got right

The two mirror insertions were labelled `turned-over` and `turned-over-180`, but
which mirror a front-to-back flip produces depends on whether you flip about the
vertical or the horizontal axis. On the day, the flip produced the *top-bottom*
mirror while the label assumed left-right. Because orientation is read from the
greyscale row rather than trusted from the label, this was detected, reported,
and used -- no pass had to be redone.

---

## Context

Two-point shading correction now works (f309004). It divides each output column by
that column's light reference, so it flattens the lamp's **left-to-right** falloff as
a side effect — the reference already carries ~25% centre-to-edge in RGB
(`calibration/shading.npz`).

Scope, in two phases. **Phase 1 is the study proper: RGB only, 600 dpi**, with all
three subjects. **Phase 2 is a much smaller follow-on for IR**, run only once phase 1
is complete, using the IT8 alone so nothing in the transport has to change — it asks
only whether the field *differs* in IR, not what it is in absolute terms.

What it cannot touch:

- **Anything varying along y.** The reference is one value per column, and it was
  measured over `CALIBRATION_FRAME = (0, 3431, 10343, 6888)` — the *lower half* of
  the transport (`rps7200/direct.py:678`). Scans run over `FULL_FRAME` y=0..6887. So
  the correction flattens x at roughly one y position and leaves any y-dependence in.
- **A 2D optical vignette.** If the falloff is radial rather than separable, a
  per-column reference measured at one y band is the wrong correction everywhere else.

The repo has been here before and got burned: `scanner_corrections()`
(`rps7200/direct.py:195`) found a "smooth ~22% falloff" from a clear-film flat, and
was dropped from the default pipeline in 2b790dd because applying it made
`worst_defect` go 4.47% → 5.28%. The flat had been captured at a different exposure,
and nothing could tell whether the falloff belonged to the scanner or to the film.

**That ambiguity is the whole problem, and rotating an IT8 target solves it exactly.**
This plan builds the measurement. Per your answer, it stops at the diagnosis: it
characterises the field and saves it, and writes no correction code.

## Why rotation works, and precisely what it can and cannot see

Model the post-shading image multiplicatively, in log space:

```
m(x,y) = s(x,y) + t(x,y)      s = scanner field (what we want), t = target
```

Insert the target in orientation `g`, scan, then un-rotate the *pixels* back into
target coordinates:

```
n_g(u,v) = t(u,v) + s(g(u,v))
```

Subtract two orientations and **`t` cancels exactly** — no assumption whatsoever that
the target is uniform, flat, or known:

```
d_g = n_g - n_e = s∘g - s
```

You have four insertions, which form the Klein four-group
`{e, flip-x, flip-y, rot180}`. `e` is the identity — the slide inserted the normal
way, unmodified — and it is the reference every other orientation is differenced
against. The other three: turned over is a mirror, turned over + 180° is the other
mirror, and 180° in the plane is the two combined. Split `s` into its four symmetry
components:

| component | parity in x | parity in y | example |
|---|---|---|---|
| `s₊₊` | even | even | **a centred radial vignette** |
| `s₋₊` | odd | even | lamp brighter on one side |
| `s₊₋` | even | odd | lamp drift down the scan |
| `s₋₋` | odd | odd | a tilt / skewed optic |

Then

```
d_fx = -2(s₋₊ + s₋₋)      s₋₋ = -(d_fx + d_fy - d_r) / 4
d_fy = -2(s₊₋ + s₋₋)  ⇒   s₋₊ = -d_fx/2 - s₋₋
d_r  = -2(s₋₊ + s₊₋)      s₊₋ = -d_fy/2 - s₋₋
```

**Three components recovered exactly, target-free. `s₊₊` cancels in every difference
and is invisible.** That is the honest limitation, and it is unfortunate because a
centred radial vignette — the most likely shape — is exactly `s₊₊`. This is not a
weakness of the analysis; no symmetry operation the target admits can reveal it.
Hence the flats below.

There is also a **free validity check**. `d_fx` is by construction purely x-odd. Any
even-in-x energy measured in it is not physics — it is misregistration, focus shift
from turning the slide over, or lamp drift between passes. Same for `d_fy` (must be
y-odd) and `d_r` (must be point-odd). These residuals are the study's own error bars.

## The two flats, and how the IT8 certifies them

Both of your flats see `s₊₊`, but each is confounded:

- **Empty transport** — `T ≡ 1` exactly, so post-shading it *is* `s`. But it has no
  film, no mount, no base scatter: a different light path from a real scan.
- **Clear film / blank mount** — the real light path, but you cannot tell a scanner
  falloff from a non-uniform medium or a mount shadowing the frame edges.

The IT8 result breaks the tie. Fit each flat, extract its three odd components, and
compare them against the IT8's target-free ground truth. If a flat's odd part
**matches**, that flat is measuring the scanner and not itself — which certifies its
even part too, and `s₊₊` follows. If a flat's odd part **disagrees**, that flat is
contaminated and its even part must not be believed.

If both flats certify but their `s₊₊` differ, that difference is the film-path
contribution (mount + base) — a separately useful number, not an error.

## Where the measurement is taken

The question is whether the **delivered image** has a vignette, so the field is
measured on the pipeline's output, not on what came off the scanner. Two consequences,
both of which shape the tooling:

**The measurement point is the fully-corrected linear image** — the
`2_corrected.tif` stage in `tools/make_comparison.py`: shading applied, defect
interpolation applied, whatever else the pipeline grows.

**Not** `3_corrected_inverted.tif`. `invert()` in `tools/make_comparison.py` is a
per-channel *percentile stretch*, computed independently per image. It is non-linear,
and its parameters differ between two scans of the same target in different
orientations. The whole method rests on `m = s + t` in log space, which needs a linear
image; running it after the stretch would make `t` fail to cancel and would
manufacture a field out of nothing. Measure before inversion, always.

**Re-run the pipeline at analysis time, from the stored raw bytes.** Corrections and
calibrations are still being worked on, so a TIFF written today answers a question
about today's pipeline only. `analyse` should therefore not read a saved TIFF: it
should take library entries and rebuild each image through the *current* code —
`library.read_raw()` → `DirectScanner._deinterleave` → the correction chain, which is
exactly the path `library.reconstruct()` (`rps7200/library.py:265`) already walks. Then
every future pipeline change is re-measured by re-running one command, with no
rescanning. This is what the raw bytes are in the library for.

It follows that **the verdict is versioned against the pipeline, not just the
scanner.** Record the git commit in the report — `library.provenance()`
(`rps7200/library.py:67`) already captures it — and re-run the analysis whenever a
correction lands. "Is there a vignette?" is a question about a pipeline, and this one
is still moving.

## Capture protocol — phase 1, RGB

Everything hinges on one thing: **the exposure must be identical across every pass,
and the shading reference must come from that same exposure.** The repo has measured
what happens otherwise — a channel calibrated 3× off its scan exposure corrected
13.0% → 1.4%, but 10× off got *worse*, 10.0% → 11.2% (`rps7200/direct.py:1785-1791`).

The order below is by **what is in the transport**, so each subject is loaded once and
never revisited: empty → clear film → IT8. Handling is the largest uncontrolled
variable in the whole study, and this is the sequence that minimises it.

Setup, with the transport still empty:

1. **Meter once, on the empty transport**, `--auto-exposure --film positive`, to ~0.65
   of full scale. Write down the `exposure_scale` and use it verbatim for every pass
   from here on. Metering on the *empty* path rather than on clear film is what the
   new order buys: empty is the brightest subject, so anything loaded afterwards is
   darker and nothing can clip. `--film positive` keeps the channels locked together,
   so the balance does not drift between subjects.
2. **Calibrate shading once** at that exposure (`calibrate_shading(exposure_scale=…)`,
   `rps7200/direct.py:1720`), save it, and `--reuse` it for every pass afterwards.
   The calibration frame is the lower transport, which is empty regardless, so this
   also happens before anything is loaded. Then check `meta["shading"]["clipped"]` is
   ~0 on every pass; if not, drop the exposure and redo this step.
3. **600 dpi, RGB (no IR), 16-bit, `FULL_FRAME`.** 600 dpi gives 862 × 574 — matching
   the existing `600_raw.tif` / `600_shaded.tif` pair — and about 3 MB per pass. Far
   more resolution than a smooth field needs, and the fastest useful setting.

The four insertions, in plain terms — `e` in the maths above is the first one:

| orientation | what you physically do | symbol |
|---|---|---|
| as-is | insert it the way you normally would | `e` |
| 180° | rotate it half a turn in its own plane, same face toward the lens | `r` |
| turned over | flip it front-to-back, so the other face points at the lens | `fx` |
| turned over + 180° | flip it front-to-back, then rotate half a turn | `fy` |

| # | in the transport | subject | orientation | purpose |
|---|---|---|---|---|
| 1 | *nothing* | empty transport | — | `s₊₊` candidate, `T ≡ 1` |
| 2 | clear film | clear film | as-is | `s₊₊` candidate, real light path |
| 3 | ↳ | clear film | 180° | certifies #2 |
| 4 | IT8 | IT8 | as-is | reference pass |
| 5 | ↳ | IT8 | as-is, **do not touch it** | machine repeat floor |
| 6 | ↳ | IT8 | as-is, **take out, put back** | handling floor |
| 7 | ↳ | IT8 | 180° | `d_r` |
| 8 | ↳ | IT8 | turned over | `d_fx` |
| 9 | ↳ | IT8 | turned over + 180° | `d_fy` |
| 10-11 | ↳ | IT8 | as-is, then 180° **at 1800 dpi** | does the field depend on resolution? |

Passes 5 and 6 are not optional — they set the threshold everything else is judged
against. A component only counts as real if it clears them. Note they now sit *after*
the first IT8 pass in loading order, which is also the right order causally: pass 5
follows pass 4 with nothing touched at all, and pass 6 is the first deliberate
re-insertion.

Passes 10-11 stay in the list, and 600 dpi makes them *more* worth having, not less:
600 dpi samples only 860 of the CCD's 5172 columns, so if the measured field differs
between 600 and 1800 dpi it is an artefact of the column mapping rather than optics.
Two extra passes to rule that out, and the IT8 is already loaded.

### Trap: at 600 dpi the last columns come back uncorrected

`apply_shading` writes only `out[:, :loc.size, c]` (`rps7200/shading.py:252`). At
600 dpi the CCD mask marks 860 used pixels while `get_parameters()` reports a width
of 862, so the trailing columns are silently left **unshaded** — right at the frame
edge, which is exactly where a vignette is largest. The `width > pixels_per_line`
guard in `scan()` does not catch this; it only catches the gross 7200 dpi case.

Check `meta["shading"]["columns"]` against `["width"]` on every pass and **drop the
trailing `width - columns` columns before fitting.** Reporting an unshaded edge column
as a vignette would be the easiest possible way to get a false positive here.

**Measured during implementation, and it softens this:** the block flatness filter
already rejects the real case unaided. Two unshaded columns inside a 12 px block make
that block wildly non-uniform, so it is dropped and contributes a log-ratio of exactly
0.0 — verified in `test_a_narrow_unshaded_edge_is_rejected_without_help`. `trailing`
earns its place for an artefact *wider* than a block, where a whole block can sit
inside the bad region and be flat but wrong; that case is
`test_a_wide_unshaded_edge_needs_trailing`. Keep both, but the flatness filter is the
primary guard and `trailing` is defence in depth.

**File every pass in the library** with `keep_raw=True`, per the scan-library
convention, so the whole study can be re-analysed after any decode change without
rescanning.

### Trap: orientation must go in `subject`, not `tags`

`library.signature()` (`rps7200/library.py:308`) keys on
`(stock, frame, subject, dpi, channels, frame, depth, film, protocol_revision)` and
deliberately excludes tags, notes, and exposure. Six IT8 passes that differ only by
orientation would share a signature, so `duplicates()` would call them redundant and
`prunable()` would offer to delete five of them.

Put the orientation in `--subject` — `"IT8 180"`, `"IT8 turned-over"`, `"IT8 as-is rep1"` —
and use `--tag vignette-study` for selection.

Phase 2 needs nothing extra here: `signature()` also keys on `channels`, so a 4-channel
IR pass of `"IT8 180"` and the 3-channel phase-1 pass of the same name already
differ. Reuse the same subject strings and add `--tag vignette-study-ir`.

## Capture protocol — phase 2, IR

> **Deferred, 2026-08-30.** Phase 1 found no field in RGB, so there is nothing
> for infrared to differ from. Kept because the code is built and tested; see
> *Phase 2 (infrared) is deferred* above for why it is not worth ~18 minutes now.

Run this **only after phase 1 is complete and its verdict is in**. It is a comparison,
not a characterisation: the question is narrowly *does the field change in IR*, and it
is answered by holding everything fixed except the channel.

**IT8 only, no flats.** Nothing in the transport changes for the whole phase — the IT8
stays where phase 1 left it. That means phase 2 gets the three odd components and, as
always, cannot see `s₊₊`. That is fine and it is the right trade: with no flat there is
no absolute `s₊₊` for IR, but comparing IR's *odd* components against RGB's already
answers "does it change", and it costs no handling at all.

| # | subject | orientation | purpose |
|---|---|---|---|
| 12 | IT8 | as-is | reference pass |
| 13 | IT8 | as-is, **do not touch it** | IR repeat floor — IR has its own noise |
| 14 | IT8 | 180° | `d_r` |
| 15 | IT8 | turned over | `d_fx` |
| 16 | IT8 | turned over + 180° | `d_fy` |

Passes 15 and 16 are the only handling in the phase, and both are re-inserting a slide
that is already characterised — the phase-1 handling floor (#6) still applies.

**Verdict for phase 2:** compare each IR odd component against the corresponding RGB
component from phase 1, on the same normalised coordinates. "The vignette also changes
in IR" means an IR component differs from the RGB ones by more than the IR repeat floor
(#13). Report it as a *difference from RGB*, not as a standalone number.

### Four things that make IR passes different, and one that would wedge the scanner

1. **Metering is cheap, and must stay in RGB.** `auto_exposure(infrared=True)` already
   probes three-channel only, in at most two rounds, and divides blue's target by
   `infrared_blue_headroom = 4.0` (`rps7200/direct.py:2188-2198`, commit 4d53901).
   Meter once at the start of phase 2 with `--ir` and lock the result. Do **not**
   reach for an infrared probe: it costs the ~212 s floor per round, which is the
   ten-minute mistake 4d53901 removed.
2. **A new exposure is required.** Blue comes back 2-3.7× brighter in RGBI than in RGB
   at the same exposure, so phase 1's locked `exposure_scale` will clip in RGBI. Phase
   2 gets its own metering round and its own locked value — and therefore its own
   shading calibration at that exposure, exactly as in phase 1.
3. **The shading reference does cover IR.** Verified: both `calibration/shading.npz`
   and `shading_matched.npz` carry `channels [0 1 2 3]` with dark references for all
   four, so the IR plane is two-point corrected like the rest. Assert this before
   analysing — `3 in reference.ref` and `3 in reference.dark`. If a future calibration
   ever yields only RGB, `apply_shading` silently leaves IR *unshaded*
   (`report["uncorrected"] += 1`, `rps7200/shading.py:234`), and an unshaded IR plane
   carries the full ~34% falloff. That would read as an enormous IR vignette and be
   pure artefact.
4. **Budget ~212 s per pass, floor, regardless of dpi.** Five passes is ~18 minutes
   minimum. Dropping to 600 dpi buys nothing here — the floor is fixed — so phase 2 is
   not the cheap part of the study, and there is no point trying to make it cheaper by
   asking for fewer lines.

**The wedge risk, and the canary.** CLAUDE.md's hardest operational rule is *never
abandon a read mid-scan*, and the recorded wedge was exactly this case: a
low-resolution IR pass expiring a timeout. At 600 dpi an IR pass asks for only 574
lines but the device still holds busy for its ~212 s floor, so lines may arrive in a
burst rather than a stream. `scan()` calls `read_planes` with the defaults and exposes
no override, so `idle_timeout = 120.0` applies (`rps7200/direct.py:1724-1733`).

Before committing to five IR passes, **run one throwaway IR pass as a canary** at the
exact intended settings. If it completes, proceed. If it stalls, plumb `idle_timeout`
through `scan()` as a prep item rather than retrying — a wedge costs a power cycle and
the whole locked-exposure session with it.

## Verify from the image that the film actually went in that way

A mislabelled orientation does not add noise — it swaps `d_fx` with `d_fy` and the
solver returns a confident, wrong decomposition. Nothing downstream would catch it,
because every equation still balances. So the label in `--subject` is a *claim*, and
each pass has to prove it from its own pixels before analysis will use it.

### The greyscale row settles it, with no OCR

An IT8 target carries a 24-step greyscale row (GS0-GS23) along **one** edge, ramping
light to dark in **one** direction. That is exactly a two-bit signature, and the two
bits are precisely the two mirrors:

| detected | GS row edge | ramp direction | orientation |
|---|---|---|---|
| bottom, light→dark | bottom | light → dark | as-is (`e`) |
| top, dark→light | top | dark → light | 180° (`r`) |
| bottom, dark→light | bottom | dark → light | turned over (`fx`) |
| top, light→dark | top | light → dark | turned over + 180° (`fy`) |

All four are distinct, so `detect_orientation(image)` is: find the row band whose
luminance is monotonic across the frame, note which edge it sits on and which way it
runs. No OCR, no dependency, no registration.

**Monotonicity alone is not enough, which the real data showed immediately.** Run
against the nine film negatives already in `library/` — photographs, with no greyscale
row anywhere in them — the most monotonic band still scored |rho| = 0.81 to 0.93. A
gradient in a picture is monotonic too, so a 0.9 threshold would have confidently
assigned an orientation to two of them.

What separates them is that **a greyscale row is a staircase and a gradient is not**.
The GS row changes at 23 places and is flat everywhere else, so the fraction of its
neighbour-to-neighbour differences that are large is ~0.03 (0.09 with sensor noise); a
smooth ramp spreads its change across every column and scores 1.00. Both gates are
therefore applied — |rho| >= 0.97 **and** step fraction <= 0.3 — and the band is
*selected* among stepped candidates rather than merely filtered afterwards, so a
picture gradient cannot outrank the real row. Re-checked against all nine negatives:
0/9 false positives.

**This also removes an assumption I should not have made.** The plan calls the two
mirror insertions `fx` and `fy`, but which physical flip gives which mirror depends on
whether you turn the slide about its vertical or horizontal axis. Detection settles
that per pass, so the maths never has to assume it. The labels are just names for "the
two mirror insertions"; the image says which is which.

### The colours check something the greyscale cannot

The GS row is neutral, so it proves orientation but says nothing about channel
assignment — an R/B swap would sail straight through it. Sample a few known patches
from the IT8 grid and assert the obvious: the red patch is brightest in R, the blue
patch in B. Cheap, and it catches a class of bug the rest of the study is blind to.

### And a crop for your eyes

Per the repo's standing convention that your read is authoritative, `capture` should
write a small crop of the target's printed text band (manufacturer, `IT8.7/1`, the
batch and date) to `previews/orientation_<pass>.png` after each pass, and print the
detected orientation to the terminal **while the slide is still in your hand**. If the
detection and the text disagree with what you just did, you can redo that pass in
seconds instead of discovering it an hour later.

### What `analyse` does with it

Refuse to run on a mismatch, rather than quietly relabelling. A wrong label may mean
the *protocol* went wrong, not just the metadata — doing 180° twice and never doing
turned-over yields a duplicate and a missing orientation, and silently trusting
detection would hide that. So require the IT8 set to be a **complete permutation**:
exactly one pass of each of the four orientations, and every repeat-floor pass
detecting as-is. Print the detected-vs-claimed table and stop if it does not hold.

### The one pass this cannot check

Clear film is featureless by design, so pass 3's "180°" is unverifiable from its own
pixels. It is the only such pass, and the consequence is bounded: if the clear film's
odd components disagree with the IT8's, "pass 3 was mislabelled" belongs on the
candidate list *alongside* "the flat is contaminated", and the two are told apart by
re-running pass 3. The empty-transport pass has no orientation at all, so it is
unaffected.

## `capture` walks you through it, one confirmed step at a time

The study is 16 passes across two phases, most of them differing only by how a slide
is seated. Getting one of them wrong is easy and — until the orientation check above —
was invisible. So `capture` is not a batch script that runs 16 scans; it is a prompted
sequence where nothing happens until you say it has.

Per pass, in order:

1. **Print the physical instruction**, spelled out, not the symbol: *"Take the IT8 out,
   turn it over front-to-back so the other face points at the lens, and put it back
   in."* For pass 5 the instruction is the opposite and needs the emphasis: *"Do NOT
   touch the scanner or the slide. This pass measures what the machine does when
   nothing changes."*
2. **Wait for your confirmation** that you have done it. Not a bare Enter — that is
   too easy to hit on reflex halfway through a 16-step sequence. Require typing the
   orientation back (`as-is`, `180`, `turned-over`, `turned-over-180`), so confirming
   requires having read the instruction.
3. **Run the pass.**
4. **Detect the orientation from the pixels** and print detected vs expected, plus the
   path to the text crop.
5. **Ask you to accept, redo, or abort.** A redo re-prompts from step 1 and discards
   the rejected pass; it must not silently overwrite, since a rejected pass is
   evidence about handling. File it with `--tag rejected` and move on.

### The prompting forces the device closed, which is what the wedge rule wants anyway

This is the part that would bite if it were not designed for. Prompting means the
device sits open and idle while a human handles a slide — possibly for minutes — and
that is close to the recorded wedge: *"Gzipping a 140 MB library entry with the device
open and idle preceded one wedge"* (CLAUDE.md). Library saving gzips the raw bytes,
and orientation detection is host-side work on top of it.

So `capture` must **close the device between passes**: `close()` after the read, then
do the library save, the detection and the crop, then prompt, then `open()` again for
the next pass. `DirectScanner.open`/`close` (`rps7200/direct.py:1140-1145`) are
separate from `__enter__`/`__exit__`, so this is a supported shape rather than a
workaround.

Closing the USB handle is **not** a power cycle, so the scanner keeps its calibrated
state; only the host-side `self._shading` is lost with the object, and that reloads
from disk exactly as `--reuse` already does in `tools/scan.py:73-76`. This costs
nothing and it removes both the idle-open window and the heavy-work-while-open window
in one move.

### One phase, one power-on

Exposure is locked and the shading reference is acquired once per phase, and both are
properties of this power-on. If the scanner does wedge and needs a power cycle
mid-phase, the passes before and after it are **not directly comparable**: the
re-acquired reference differs, and that difference is fixed-pattern rather than
symmetric, so it leaks into the components as a spurious signal rather than averaging
out.

`capture` should therefore record a session id per pass and refuse to analyse across a
boundary without being told to. If a phase genuinely has to be split, re-run the
machine-repeat pair (#4/#5) after the restart so the *cross-session* floor is measured
rather than assumed — it will be larger than the within-session one, and that larger
number is the threshold the split study has to clear.

## Prep work in existing code

Small, and needed before any capture:

- **`tools/scan.py`** — add `--exposure-scale` (pass straight through to
  `DirectScanner.scan(exposure_scale=…)`, which already accepts a scalar or a
  4-sequence, `rps7200/direct.py:2060`). There is currently no way to lock exposure
  across passes; the flag is the difference between a controlled study and eleven
  unrelated scans. Optionally `--frame` too.
- **Record `filter_offset1` / `filter_offset2`** in the scan metadata. They are read
  by `get_parameters()` (`rps7200/direct.py:1444`) and then discarded.
  `docs/shading-calibration-plan.md` already asks for this, and they are the prime
  suspect for the open r = +0.936-at-lag-−16 anomaly, which pass 2 will re-measure.
- Do **not** touch `DirectScanner.calibrate()` (`rps7200/direct.py:1410`) — it
  references an undefined `exposure_scale` and raises `NameError` on any call. It is
  dead code, unreferenced, and out of scope here; just do not reach for it.

## What to build

### `rps7200/uniformity.py` — new module

Named for what it measures, not for the answer it might give.

- `Orientation` — the four group elements as **exact** array reversals
  (`img[::-1, ::-1]`, `img[:, ::-1]`, `img[::-1]`), plus composition. No
  interpolation, so un-rotating introduces nothing.
- `register(a, b, max_shift)` — integer-pixel alignment by phase correlation
  (numpy FFT, no new dependency). Returns `(dy, dx)` and a peak-sharpness confidence.
  **Mandatory, not optional**: the repo has already seen two identical passes
  correlate at lag −16 rather than 0.
- `block_ratios(a, b, dpi, reject_std=…)` → `(y, x, log_ratio)` per channel.
  The target-cancelling core. Divide the aligned pair, tile into blocks, and **keep
  only blocks that are uniform in both scans** — those are patch interiors. Edge
  blocks are rejected outright, so patch borders never enter, and registration then
  only needs to be good to a fraction of a patch rather than sub-pixel. This is what
  makes the method robust; do not pixel-difference.

  Size the block **physically, not in pixels**: ~0.5 mm, i.e.
  `block = max(8, round(dpi * 0.5 / 25.4))` — 12 px at 600 dpi, 35 px at 1800. An
  IT8 patch is roughly 1.3 mm, so a half-millimetre block sits inside one with margin
  at any resolution, and the 600/1800 comparison in passes 10-11 stays like-for-like.
  At 600 dpi this still yields ~3300 candidate blocks against 15 fit coefficients —
  no shortage of samples. (`MM_PER_INCH` is already defined at
  `rps7200/direct.py:729` and currently unused anywhere; this is its first caller.)
- `fit_field(blocks)` — robust (IRLS) least squares of a total-degree-4 2D polynomial
  per channel, in **frame-normalised coordinates** so a 600 dpi and an 1800 dpi
  measurement are directly comparable. Follow the intent of `resample_reference`
  (`rps7200/direct.py:530`): map by scanner position, never by width ratio.
- `solve_components(d_fx, d_fy, d_r)` — the closed form above. Returns the three odd
  components **and an explicit `even=None` marker** — the blind spot must be
  reported, never silently returned as zero.
- `symmetry_residuals(...)` — the leaked-parity validity check, as a fraction of the
  odd magnitude.
- `detect_orientation(image)` → one of the four, from the greyscale row: locate the
  band whose luminance is monotonic across the frame, report which edge it is on and
  which way it runs. Pure numpy, no OCR. Returns the detection plus a confidence, so a
  target that is cropped or badly seated reports "unsure" rather than guessing.
- `check_channels(image)` — the colour sanity check: known-red patch brightest in R,
  known-blue in B. Catches a channel swap, which `detect_orientation` cannot see
  because the greyscale row is neutral.
- `save` / `load` as npz, mirroring `ShadingReference.save/load`
  (`rps7200/shading.py:74-99`).

### `tools/uniformity.py` — new tool

- `capture` — the prompted sequence described above: meters once, calibrates once,
  then for each pass prints the physical instruction, waits for you to type the
  orientation back, scans, **closes the device**, files the entry, detects the
  orientation, prints detected-vs-expected with a text crop, and asks you to accept /
  redo / abort before reopening for the next one. Holds exposure, dpi and frame fixed
  throughout and writes the `subject` itself. Sixteen passes with hand-typed metadata
  is where a study like this quietly goes wrong.
- `analyse <library> --tag vignette-study` — loads the set, **rebuilds each image from
  its stored raw bytes through the current correction pipeline** (see *Where the
  measurement is taken*), registers, decomposes, cross-checks the flats, prints the
  verdict table. Re-runnable against any future pipeline with no rescanning; it should
  print the `provenance()` commit it ran under, and warn if the reconstruction differs
  from the stored `scan.tif` — that difference is the pipeline change, and it is
  information, not an error.

  Before any of that, it **re-checks every orientation from the pixels** and refuses to
  run unless the IT8 set is a complete permutation of the four with all repeat passes
  detecting as-is. The check runs at analysis time as well as capture time on purpose:
  entries get re-analysed months later, and the claim in `subject` is only as good as
  the pass that wrote it.
- `report` — writes the field maps.

### Output

Both numeric and visual, since you judge these by eye:

- A table for R, G and B: peak-to-peak of `s₋₊`, `s₊₋`, `s₋₋` in % of frame mean; the
  two noise floors; the parity residuals; each flat's odd-part agreement with the IT8;
  and `s₊₊` from each certified flat.
- Heatmaps to `previews/uniformity_<component>_<channel>.png` (PIL, as
  `tools/make_comparison.py` already does — there is no matplotlib in this repo and no
  reason to add one).

**Verdict rule:** a component is real if its peak-to-peak exceeds `max(3 × the
re-insertion floor, 0.5%)`. Give the verdict per channel rather than pooled — the
three channels are metered independently and reach quite different exposures
(`exposure [27569, 39445, 52048]` in `scans/negatives/negative_3600dpi.json`), so they
can legitimately disagree.

**Phase 2 output is a delta, not a second report.** Add an `--ir` mode to `analyse`
that loads the phase-2 set, computes its three odd components, and prints them
*alongside* the phase-1 RGB components on the same normalised coordinates, with the
difference and the IR repeat floor. One extra row per component, and one sentence:
whether the field in IR differs from the field in RGB by more than that floor. Phase 2
deliberately produces no `s₊₊` and no flat cross-check — it has no flats — so it must
not be presented as a standalone characterisation of the IR field.

### A concrete prediction worth checking

Shading is calibrated over y = 3431..6888 and applied over y = 0..6887. If the field
has real y-dependence, the empty-transport flat (#1) should look flattest in x around
y ≈ 5160 and progressively less flat away from it. If it does, that is both the
mechanism and a strong confirmation. If it is uniformly flat, y-dependence is small.

## Verification

- `python3 -m pytest tests/ -q` stays green.
- New `tests/test_uniformity.py`, synthetic and offline, following the `block()`
  fixture pattern in `tests/test_shading.py:23`:
  - synthesise a known field with all four components plus a random target, generate
    the four orientations, assert `solve_components` recovers the three odd
    components to float tolerance;
  - assert a **purely even** field is reported as *unmeasured*, not as zero — a test
    that pins the blind spot in place;
  - `register` recovers a known integer shift;
  - `block_ratios` rejects blocks straddling a patch edge;
  - the same synthetic field sampled at two resolutions fits the same coefficients.
- On real data: pass 5 vs pass 4 (machine repeat, untouched) must come out near the
  noise floor with all components below threshold. **If the machine-repeat pair shows
  structure, stop** — the measurement is not yet trustworthy and the anomaly is the
  finding. Same for pass 13 vs 12 before trusting anything in phase 2.
- Phase 2 is gated on phase 1: do not capture IR passes until the RGB verdict is in.
  It reuses phase 1's components as its baseline, so a phase-1 result that is still
  moving makes the IR comparison meaningless.
- Parity residuals must be small relative to the recovered components. If `d_fx`
  carries substantial even-in-x energy, the turned-over passes are not clean
  (focus shift or seating) and the flip-derived components are suspect; the rot180
  result stands on its own regardless.
- `detect_orientation` gets its own tests: synthesise a target with a greyscale row
  along one edge, generate all four orientations, assert each is identified; assert a
  featureless image reports "unsure" rather than guessing; assert `analyse` raises on
  a set that is not a complete permutation.
- Assert in a test that the analysis path refuses a tone-mapped input, or at minimum
  never calls `invert()`. Measuring after the stretch is the one mistake that would
  silently produce a confident, wrong answer.
- No change to any scan output, so `1_nothing_done.tif` / `2_corrected.tif` /
  `3_corrected_inverted.tif` should reconstruct byte-identical. Regenerate them and
  confirm nothing moved — this study must be observation-only.
- Re-run `analyse` once with a deliberately altered correction (e.g. shading off) and
  confirm the verdict moves. That proves the measurement is actually reading the
  pipeline rather than a stale TIFF, which is the property that makes it worth
  re-running as the corrections land.

## Deferred: the correction

Out of scope by your choice. Noted so the measurement lands in a usable shape: the
saved field is per-channel polynomial coefficients in frame-normalised coordinates,
which is directly what a later `apply` step would consume — applied after
`apply_shading`, normalised to mean 1 so it does not shift metering, behind a
default-off flag until the rotation differences and both flats collapse. The old
`scanner_corrections()` vignette path and `tools/make_comparison.py --vignette` stay
exactly as they are for now.
