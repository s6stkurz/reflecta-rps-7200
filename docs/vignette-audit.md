# Re-reading the vignette verdict

An audit of `docs/vignette-plan.md`, `rps7200/uniformity.py` and
`tools/uniformity.py`, prompted by "look at the vignette again, independently".

**The operational rule stands: no vignette correction should be added.** What
this document disputes is the *reason* recorded for it, which claims more than
the study established — and it flags one decisive test that was never run, plus
a separate bug in how two tools file scans.

Nothing here required the scanner or the library. It is all re-reading.

> **Revision note.** An earlier version of this file argued that a lens
> vignette on this device is x-only *by construction*, on the premise that the
> sensor has no row dimension. That premise is wrong — see §1 — and the
> argument has been replaced with an empirical one. The over-strong version was
> committed in `67f8d7a`; do not quote it.

---

## 1. The attribution is not established, but not for the reason first given

`docs/vignette-plan.md:25-26`:

> An optical vignette falls off in both directions; this falls off in neither y
> nor (after correction) x, so the 39% is a lamp and sensor profile, not an
> optic.

and `profile_span`'s docstring (`tools/uniformity.py:310-314`) carries the same
reasoning into the code:

> a y number is something the correction could not have produced either way --
> which makes y the axis that carries the news about a genuine 2D vignette.

### What is true about the geometry

This is a line-scan instrument, and that does bias the expected field strongly
toward x. Within one row of photosites, the row occupies a fixed position in
the lens field; as film transports, every successive line is imaged onto that
same row. So the dominant term of any lens falloff is expected to be a function
of x, and a y-invariant one.

### What is not true

**The sensor does have a row dimension.** It is a *staggered trilinear* CCD:
red, green and blue sit on separate rows of photosites
(`rps7200/defects.py:3-4`), and each is split into two element rows offset
along the scan direction (`rps7200/direct.py:1531-1535`,
`NATIVE_COLUMN_STAGGER_LINES = 4`). The device itself reports the inter-row
spacing as `filter_offset1`/`filter_offset2`
(`rps7200/protocol.py:407-408`). So "no row dimension" is false.

**`CCD_MASK_SIZE = 5172` is not a sensor census.** It is the calibration-mask
width — one byte per calibration column (`rps7200/direct.py:1513-1515`) — and
it is one of the two staggered element rows. The native frame is 10344 columns
(`rps7200/framing.py:27,30`). A consequence worth knowing on its own: a 7200
dpi pass writes only `out[:, :loc.size, c]` (`rps7200/shading.py:281`), so
columns 5172..10343 come back **entirely uncorrected**.

**`ShadingReference` being 1-D is a modelling choice, not evidence.**
`calculate_shading` collects ~20 lines per phase per channel and averages them
away (`rps7200/shading.py:171-175`). The y information is measured and then
discarded, because the correction model is per-column, inherited from the SANE
backend (`:11-13,23`).

**And "constant in y" rests on unstated premises the repo itself questions**:
that illuminator, lens and sensor translate as a rigid body past a flat object
plane, and that the lamp is stable across the traverse. Against that: lamp
warm-up is ~80 s (`rps7200/direct.py:865-866`) while a 7200 dpi pass runs 314 s;
the aperture holds a 36 mm frame with half a millimetre of slack
(`rps7200/framing.py:3-4`), so film curl along transport is unconstrained; the
carriage home is not repeatable (`docs/fast-infrared-plan.md:298-310`); and the
plan's own component table names a y-odd component "lamp drift down the scan"
(`docs/vignette-plan.md:162`). The plan also lists the contrary case as live:
*"A 2D optical vignette. If the falloff is radial rather than separable, a
per-column reference measured at one y band is the wrong correction everywhere
else"* (`:116-118`).

### So what is actually wrong with the published argument

Not that it reaches a false conclusion, but that **it treats a weak
discriminator as a decisive one**. Both a lamp profile and a lens falloff are
expected to be x-dominated here, so "falls off in x, flat in y" separates them
only weakly — and the y arm of it is, besides, below the study's own noise
floor (§4).

### The separators that do exist and were never used

The study did not need to leave the attribution open. Four discriminators sit
in data already on disk, none needing the scanner:

- **Per-channel profile shape.** Natural and mechanical vignetting are
  achromatic to first order; lamp emission and sensor QE are not. The published
  raw x spans already differ across channels — **39.0 / 35.9 / 34.6 %**, a
  4.4 pp spread, monotonic with wavelength. That is a lamp/QE signature, and it
  is arguably the strongest evidence in the whole study *for* the conclusion
  that was reached — and nobody pointed at it. Normalise each channel's
  reference and overlay them: one screen.
- **Light against dark.** `calculate_shading` returns both phases
  (`rps7200/shading.py:172,174`). Dark is the sensor with no illumination and
  no light through the optic, so light ÷ dark separates *sensor* from
  *lamp+optic* — a third of the claim "lamp **and sensor** profile" — and was
  never computed.
- **The deferred IR phase** (`docs/vignette-plan.md:45-56`) is ~900 nm against
  ~450 nm, the strongest chromatic lever on the machine. It was deferred
  because "RGB turned out to have no field to differ from", which is the
  conclusion under audit.
- **cos⁴θ is a one-parameter family** fixed by the conjugate distance, and the
  geometry is known. A measured profile either fits it or does not.

### Recommended wording

> The 39% falloff lives in x. Per-column shading takes it to 1.4%, and that
> holds whatever its origin, because every candidate source — lamp, sensor QE,
> lens — is expected to be dominated by an x-only term on a line-scan geometry.
> No structure in y was detected above the 1.35-1.52% repeat floor, and no 2-D
> test has yet been run that could see an x-profile whose shape *changes* with
> y. A vignette correction would be a second, redundant x-correction unless
> that test finds something.

That keeps the rule, drops the claim to separate lamp from optic, and leaves
the one open test named rather than buried.

One thing no version of this covers: a lens falls off in **sharpness** at the
field edge as well as brightness, and shading does not touch MTF.

## 2. The load-bearing numbers come from twenty lines with no error bar

The four numbers the conclusion quotes are `profile_span`
(`tools/uniformity.py:306-325`) — median marginal profiles, `max/min - 1`,
`trim=10`. No fit, no noise estimate, no goodness-of-fit anywhere.

None of the careful machinery — the Klein-group parity algebra, the robust
degree-4 fit, the re-derived orientations — contributes to the published
conclusion. `report_flats` prints these for a human; they enter no programmatic
decision (verdict at `:505`).

- **`max/min` is a pair of extreme order statistics** — the most
  noise-inflated summary available — used as the sole evidence for a null.
- **`trim=10` is underived** and discards the ten outermost columns each side,
  exactly where a falloff is largest, biasing x *down*.
- **x and y are not commensurable.** x spans the half-width (18.25 mm at
  600 dpi), y the half-height (12.15 mm); for a centred radial field the
  expected y span is smaller by ~2.3× in the leading term before anything else.
- **The y profile takes its median over x**, and in the raw image that axis
  carries the 39%. A median over a strongly non-uniform set lands at a
  mid-radius, so y is measured along an off-axis chord where the radial
  gradient in y is weakest.
- **IR is silently excluded**: the loop is `range(min(3, shape[2]))` (`:317`).

## 3. A failed validity check was read as a positive result

The plan's rule at `docs/vignette-plan.md:684-687`:

> Parity residuals must be small relative to the recovered components. If
> `d_fx` carries substantial even-in-x energy [...] the flip-derived components
> are suspect

The result at `:28-31` reports **0.687, 0.934, 0.692** and concludes:

> the parity residuals [...] say those differences are noise-dominated rather
> than carrying a clean field -- which is what a real absence looks like.

The module's own definition (`rps7200/uniformity.py:709-710`) says 1.0 is "one
carrying no legitimate signal at all". By the plan's stated rule these are
*suspect*; they were read as confirming absence. The correct reading is **"these
differences do not constrain the odd components"** — a different claim from
"the odd components are zero".

**Likely mechanical cause.** `decompose` (`:645-660`) sends a constant entirely
into `pp`; `ALLOWED_COMPONENTS` (`:692-696`) excludes `pp` for every
orientation — verified, it is in none of the three rows; and `fit_field` has a
free degree-0 term absorbing the mean log-ratio between passes. So any exposure
or lamp drift drives the residual toward 1.0, and that DC term is exactly what
the rest of the analysis ignores (`peak_to_peak_percent` is `max - min`).

Subtracting the mean before decomposing would settle which it is. Note this
diagnoses rather than repairs: if the residual falls, the components were
recoverable all along; if it stays high, the passes really are inconsistent.

The residual is also computed on **channel 0 only** and on the unmasked full
grid including the extrapolated corners the rest of the code excludes.

## 4. The table contradicts the sentence near it

`docs/vignette-plan.md:21-24`:

> Shading is a per-column correction: it can only flatten x [...] so it cannot
> have produced y-flatness. y measures 1.1% *before* correction. There was
> never anything there.

The table shows y going **1.1 / 0.8 / 1.0 → 0.7 / 0.7 / 0.6**. Shading reduced
y by a third. It can: each row's median is taken over columns that shading
rescaled non-uniformly.

And **raw y of 1.1% is below the study's own repeat floor of 1.35-1.52%**.
"There was never anything there" states a null result as a measured zero. The
honest form is that y-structure, if present, is below what this study resolved.

## 5. A real bug: `trailing` is applied to the wrong edge

`tools/uniformity.py:186-189` calls `apply_orientation` *before*
`block_ratios(..., trailing=trailing)`, and `block_ratios` drops from the right
(`rps7200/uniformity.py:459-460`). Unshaded columns are on the right of the
original (`rps7200/shading.py:281` writes only `[:loc.size]`), so after an
x-reversal for `MIRROR_X` or `ROT180` they are on the **left** — `trailing`
discards good columns and leaves the artefact.

Correct only for `MIRROR_Y` and the repeat pairs. Mitigated at 600 dpi
(`trailing == 2` inside 12 px blocks, which the flatness filter rejects anyway)
but the wrong edge on exactly the two differences carrying the x information.
No test covers the mirrored case.

## 6. Two silent failure modes

Both produce a confident wrong answer with no exception:

- **`ccd_mask.bin` absent** → `apply_shading` falls back to
  `np.arange(min(w, reference.pixels_per_line))` (`rps7200/shading.py:236-239`),
  mapping output column j to CCD element j. At 600 dpi that is the leftmost 862
  of 5172 elements.
- **`shading.npz` absent** → `rebuild` returns the raw decode with
  `shaded: False` (`tools/uniformity.py:144-150`). One unshaded pass
  differenced against shaded ones carries the full 39% into the result. The
  only signal is the word `RAW` in a log line.

## 7. Separate finding: two tools file corrected pixels as raw

Found while checking whether an independent decode could be gated against
`scan.tif`. **This is independent of the vignette question and matters more.**

`library.save`'s contract is explicit (`rps7200/library.py:152-160`):

> ``image`` must be the **raw** pixels -- what the scanner sent, before
> flat-fielding. [...] `corrections` names anything a caller has nonetheless
> baked into ``image``, so a file that is not raw is at least labelled as such.

But `DirectScanner.scan()` **returns the corrected image**: `raw_pixels = image`
is taken before correction (`rps7200/direct.py:2601`), `image` is rebound by
`apply_shading` (`:2626`), and `return image, meta` (`:2710`) hands back the
corrected one. The raw array is exposed only as `self.last_pixels_raw`
(`:2704`), which `tools/` never reads.

Two of the four `library.save` callers pass that returned image straight
through, with no `corrections=`:

- `tools/scan.py:225` — the main scanning tool
- `tools/uniformity.py:604` — which filed all nine `vignette-study` entries

The other two are correct: `rps7200/direct.py:2697` passes `raw_pixels` (so the
mandated `RPS7200_DEBUG=1` path is sound), and `rps7200/session.py:481`
explicitly prefers `raw_image`, citing this rule.

**Why it is not self-announcing.** `corrections_applied` is set from the
parameter alone — `list(corrections or [])` (`:223`) — and nothing infers it
from `meta`, although `meta["shading"]` is present and is recorded two fields
away as `calibration.report` (`:281`). So an affected entry says "a correction
was computed, and nothing is baked into the pixels" when in fact the correction
*is* in the pixels. That is indistinguishable from a correct entry.

**Consequence.** `library.corrected()` (`:357`) applies shading whenever
`corrections_applied` is empty — so on these entries it shades a **second
time**. `tools/library.py verify` does not catch it (checksums only); it passed
on the 2026-09-11 `tools/scan.py` entries recorded in TODO.md. `reconstruct`
*would* flag them, as a changed decode.

This is the same class as the prescan drift CLAUDE.md describes, now in the two
capture tools rather than in `session.py`. `tools/library.py migrate-raw` is
the remedy for existing entries (dry run unless given `--write`); the call
sites are the fix for new ones.

**It does not invalidate the published vignette numbers.** `report_flats` reads
`raw_image()` and `rebuild()` (`tools/uniformity.py:358-360`), which decode
`raw.bin.gz` directly and never touch `scan.tif`.

## 8. Smaller findings

- **Span and floor use different hard-coded channels**: `parity_residual` reads
  channel 0, `span` reads channel 1 (`tools/uniformity.py:202`, `:207`), no
  comment. The repeat floor is green-only while `threshold = max(3*floor, 0.5)`
  applies to R, G, B and I alike.
- **Shading guarantees a small corrected x number.** Each column is divided by
  its own reference and multiplied by the global mean, so "39% → 1.4%"
  demonstrates the division worked, not anything about origin.
- **Registration confidence is computed, printed and never gated on**; the
  shift is never checked against `max_shift=64`.
- **The clipping count is discarded** although the plan instructs checking it.
  An empty transport is the brightest scene this device sees, and clipping
  flattens a peak — biasing toward the conclusion.
- **No goodness-of-fit anywhere.** Every reported number is the peak-to-peak of
  a fitted surface.
- **`support_box` is a bounding box, not a hull** (`:632-634`).
- **The flatness filter is level-dependent** (`rsd = std/mean` on raw counts),
  and `keep = flat.all(axis=2)` lets the noisiest channel decide for all.
- **Robust IRLS plus a degree-4 basis is biased against edge-confined signal**
  — the shape being looked for.
- **`claimed_orientation` substring-matches `"180"`**, colliding with
  `"1800 dpi"` (`:436-438`).
- **`dpi` silently defaults to 600** on a missing `resolution_dpi` (`:455`),
  changing `block_size` and the whole flatness filter.
- **Dead code the documentation relies on**: `log_image` called by nothing but
  a test, though the module docstring's strongest warning points at it;
  `channel_report` never called, so the channel sanity check the plan required
  does not run; `Field.save`/`load` never called.
- **The 1800 dpi resolution control was dropped** — 11 passes planned, 9 run.

## 9. What would settle it

Not yet run: the library is not on the machine this was written on.

- The **per-channel reference overlay** and **light ÷ dark** from §1 — cheapest
  and most informative, and they need no gate at all.
- **The 2-D test nobody ran**: split into y-bands and ask whether the column
  profile's *shape* changes with y. This is the one field shape a per-column
  reference cannot correct, and both marginals are blind to it by
  construction. It is also the test of whether measuring the reference over
  `CALIBRATION_FRAME` (y 3431..6888) is safe for a scan covering y 0..6887.
- **Parity residuals with the DC term removed** — settles §3.
- **A per-channel clipping census** — see §8.
- **The repeat floor per channel** rather than green-only.
- **Fixed-sensor-column structure across many entries**, mapped through each
  entry's own CCD mask: a sensor effect sits at the same sensor column across
  different film positions, picture content does not.

## 10. Recommended changes, not yet made

`CLAUDE.md:334-341` forbids *adding a correction* and explicitly invites
re-running the measurement; it is the stated conclusion, not the rule, that
this audit disputes. Changing a load-bearing convention file is Stefan's call,
so this records the wording (§1) and stops.

The `trailing` bug (§5) and the mislabelling call sites (§7) are ordinary fixes
needing no decision.
