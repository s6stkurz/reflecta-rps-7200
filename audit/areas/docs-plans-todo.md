# docs/*.md and TODO.md vs the code

Area key `docs-plans-todo`. 33 findings: 6 high, 12 medium, 14 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Audit of docs/*.md, TODO.md and research/frame-edge/{README,REPORT}.md against the executable code (rps7200/, tools/). The docs are largely historical plans with dated status headers. Many present-tense statements are stale because recent commits landed after the last TODO.md edit (2026-09-22): the read-direction decode (622aa5a, 2026-09-23), PROTOCOL_REVISION 5 and 6, the 1.84-unit ramp, and the frame_edges integration. The most serious mismatches affect the owner's three requirements.


(1) Library exactness:
- The shading reference is stored only in processed form. The calibration pass bytes are never kept.
- Metering probes, hold-loop verification prescans, CLI dry-run walks and before-correction prescans are never filed outside debug mode.
- A roll frame's prescan.tif holds corrected pixels.
- library.signature ignores gain and MODE SELECT byte14, so `duplicates --delete` would destroy the byte-14 and gain ladders that TODO says must never be pruned.
- The 7200 dpi column realignment is baked into scan.tif, is not recorded anywhere, and `reconstruct` cannot reproduce it.
- `verify` mislabels a deliberately raw scan as 'correction was asked for', although code comments and TODO claim it splits the two cases correctly.

(2) Acceptance and evaluation: `tools/make_comparison.py`, named in CLAUDE.md and several plans as the standing by-eye acceptance test, applies the obsolete destripe path and no shading at all. Its `2_corrected.tif` does not show the correction the driver ships.


(3) Open defects TODO lists correctly that are still present:
- `apply_shading` leaves trailing columns uncorrected.
- `--reuse` leaves no record of where or when the reference came from.
- Sub-frame travel is unbounded across a roll on the approved path.
- The CLI dry run files nothing in the library.
- CORRECTION_DEADBAND_MM is dead code.
- The sheet's nudge tick is a dead control, and `approved.json` is one-way.
- The TIFF cross-implementation tests are permanently skipped.

(4) Bugs found in the audit and not recorded in TODO:
- `--bracket --ir` merges the RGBI pass's blue channel using a green-fitted ratio.
- `--bracket` silently drops a scalar `--exposure-scale`.
- The CLI `--start-at` 'resume' overwrites roll.json.
- `library.save` accepts `inquiry` and discards it.
- The window's resume does not restore exposure, contrary to TODO. Rolls take no exposure input at all.

(5) Superseded constants and claims still presented as current, across protocol.md, fast-infrared-plan, whole-roll-plan, 7200dpi-plan, step-calibration/registration-accuracy/frame-measurement plans and the frame-edge REPORT: ramp 1.57 vs 1.84, param cap 8 vs 87, frame width (36 mm, 35.3 mm, 326/334/350.6 units), fast-IR 'not default', 'probes in RGBI', 'one blue constant', 'integration not done'.


No hardware or library data is present in this checkout (library/, rolls/, calibration/ absent), so numeric claims about stored entries could not be re-derived. Only code paths were verified.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [DOC-01](#docs-plans-todo-doc-01) | high | doc-mismatch | The standing acceptance test (make_comparison.py) applies destripe, not the shading correction the driver ships | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [DOC-02](#docs-plans-todo-doc-02) | high | data-integrity | library.signature ignores gain and byte14, so `duplicates --delete` would destroy the byte-14 and gain ladders that TODO says must not be pruned | [P11](../problems/P11-duplicates-delete-destroys-scans.md) |
| [DOC-03](#docs-plans-todo-doc-03) | high | data-integrity | The calibration pass's raw bytes are never stored; the library keeps only the processed ShadingReference | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [DOC-04](#docs-plans-todo-doc-04) | high | data-integrity | Several kinds of pass are never filed outside debug mode, and a roll frame's prescan.tif holds corrected pixels | [P08](../problems/P08-passes-never-filed.md) |
| [DOC-05](#docs-plans-todo-doc-05) | high | bug | `tools/scan.py --bracket N --ir` merges the RGBI pass's blue with RGB passes using a ratio fitted on green | [P29](../problems/P29-bracket-merge.md) |
| [DOC-A1](#docs-plans-todo-doc-a1) | high | data-integrity | Debug filing deletes a scan's only spooled copy (raw bytes and pixels) even when library.save failed | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [DOC-06](#docs-plans-todo-doc-06) | medium | bug | `verify` reports a deliberate shading=False scan as 'correction was asked for'; code comments and TODO claim it splits the two cases correctly | [P12](../problems/P12-library-maintenance-tools.md) |
| [DOC-07](#docs-plans-todo-doc-07) | medium | data-integrity | 7200 dpi column realignment is baked into scan.tif, not recorded, and not reproducible by reconstruct or decode_raw | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [DOC-08](#docs-plans-todo-doc-08) | medium | doc-mismatch | TODO's reversal 'won't-fix' and 'orientation unresolved' entries contradict the code: the decode now turns reversed passes upright | -- |
| [DOC-09](#docs-plans-todo-doc-09) | medium | doc-mismatch | PROTOCOL_REVISION is 6 and lives in protocol.py; TODO's first known problem and several docs say 4, or direct.py | -- |
| [DOC-10](#docs-plans-todo-doc-10) | medium | bug | `tools/scan_roll.py --start-at N` 'resume' overwrites the earlier roll.json instead of resuming from it | [P19](../problems/P19-roll-manifests.md) |
| [DOC-11](#docs-plans-todo-doc-11) | medium | data-integrity | `--reuse` loads any cached reference silently, and entries do not record where the reference came from (TODO open item, still present) | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [DOC-12](#docs-plans-todo-doc-12) | medium | bug | `apply_shading` silently leaves trailing columns uncorrected; the plan marked DONE says scan() refuses in that case, and two docs disagree on whether 600 dpi triggers it | -- |
| [DOC-14](#docs-plans-todo-doc-14) | medium | doc-mismatch | Frame width has contradictory values across code and docs; the driver's own path still centres on 36.0 mm | -- |
| [DOC-15](#docs-plans-todo-doc-15) | medium | doc-mismatch | TODO's safety rule 'no single detector may move film' is bypassed on the live path: with an edge reader, one frame_edges member can decide | -- |
| [DOC-16](#docs-plans-todo-doc-16) | medium | user-error | The window-roll lazy-calibration TODO item is partly stale, but a race remains: `calibrated` is set True before the Calibrate job succeeds | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [DOC-27](#docs-plans-todo-doc-27) | medium | bug | `tools/scan.py --bracket` silently ignores a single-value `--exposure-scale` | [P29](../problems/P29-bracket-merge.md) |
| [DOC-A2](#docs-plans-todo-doc-a2) | medium | data-integrity | Bracket membership, ratio and metering evidence are dropped from filed bracket passes; the merged result is never filed | [P09](../problems/P09-record-missing-parameters.md) |
| [DOC-13](#docs-plans-todo-doc-13) | low | design | Roll-wide sub-frame travel is unbounded on the approved (contact-sheet) path, and the per-frame budget still scales with the target | -- |
| [DOC-17](#docs-plans-todo-doc-17) | low | doc-mismatch | Resume does not restore 'the exposure/gain/offset the scanner was asked for'; a code comment references a manifest `device` block that is never written | -- |
| [DOC-18](#docs-plans-todo-doc-18) | low | doc-mismatch | fast-infrared-plan still says, in present tense, that fast IR is not the default and that PROTOCOL_REVISION is not bumped | -- |
| [DOC-19](#docs-plans-todo-doc-19) | low | doc-mismatch | protocol.md: SLIDE law, ramp, param cap, backward fine step, §12 and §1 contradict the code or the rest of the document | -- |
| [DOC-20](#docs-plans-todo-doc-20) | low | doc-mismatch | whole-roll-plan and other plans state superseded metering and exposure behaviour as current | -- |
| [DOC-21](#docs-plans-todo-doc-21) | low | doc-mismatch | 7200dpi-plan says scan() calibrates at the pass's own resolution; the code always calibrates at 3600 | -- |
| [DOC-22](#docs-plans-todo-doc-22) | low | doc-mismatch | exposure-negative-plan's correction of the linearity figure was never applied to code or TODO | -- |
| [DOC-23](#docs-plans-todo-doc-23) | low | doc-mismatch | TODO open-problem entries that are already fixed or obsolete, and stale line references | -- |
| [DOC-24](#docs-plans-todo-doc-24) | low | dead-code | frame-measurement-plan lists single-command adjustment (`command_for`) as done; it is dead code, and moves still go through the multi-command plan_nudges | -- |
| [DOC-25](#docs-plans-todo-doc-25) | low | doc-mismatch | research/frame-edge README and REPORT still say integration is not done and describe a different target module and frame width | -- |
| [DOC-26](#docs-plans-todo-doc-26) | low | user-error | `tools/scan.py` scans unmetered by default, though docs present auto-exposure as the default | -- |
| [DOC-28](#docs-plans-todo-doc-28) | low | data-integrity | `library.save` accepts `inquiry` from every caller and never writes it | -- |
| [DOC-29](#docs-plans-todo-doc-29) | low | design | Open design gaps TODO records accurately: the sheet's dead 'nudge' tick, one-way approved.json, and permanently skipped real-TIFF tests | -- |
| [DOC-A3](#docs-plans-todo-doc-a3) | low | doc-mismatch | plan_nudges docstring still describes the param 1..8 lattice and a param-8 clamp | -- |
| [DOC-30](#docs-plans-todo-doc-30) | info | library-completeness | Red plane 'one line out' (TODO) is unaddressed in decode, and filter_offsets are recorded but never applied | -- |

## Findings in full

<a id="docs-plans-todo-doc-01"></a>

### DOC-01 -- The standing acceptance test (make_comparison.py) applies destripe, not the shading correction the driver ships

**Severity** high · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/make_comparison.py:24-29`, `tools/make_comparison.py:106-122`, `docs/vignette-plan.md:208-210`, `docs/shading-calibration-plan.md:293-296`, `CLAUDE.md:180-186`, `rps7200/shading.py:196-283`

**Doc claim:** docs/vignette-plan.md:209 'shading applied, defect interpolation applied'; docs/shading-calibration-plan.md:293-296 'primary acceptance test'; CLAUDE.md 'Regenerate the three comparison files after any significant change to the scan or correction path'

The three comparison TIFFs Stefan judges by eye are made by a tool that runs the pre-shading destripe and flat-defect interpolation that shading-calibration-plan §4 says was demoted. It never applies the per-column shading reference, the correction every delivered file actually gets through apply_shading or library.corrected. Its inputs are a TIFF plus a flat, so it cannot apply an entry's own shading.npz and ccd_mask.bin even if pointed at a library scan.tif. The docs call its output 'shading applied' and the primary acceptance test.

**Evidence (from the code):**

```text
make_comparison.py: `from rps7200.direct import (destripe, find_column_defects, flat_defect_sigma, resample_reference)` ... `corrected = destripe(raw, defects, margin=12, dilate=5)` ... `tiff.write("2_corrected.tif", corrected, ...)`. It never imports apply_shading, library.corrected or a ShadingReference. Defaults: `scan_path = ... "scans/negatives/state_1800dpi.tif"`, `flat_path = ... "scans/flat/flat_clearfilm_3600dpi.tif"`. vignette-plan.md:209: "the `2_corrected.tif` stage in `tools/make_comparison.py`: shading applied, defect interpolation applied". shading-calibration-plan.md:295: "This is the primary acceptance test; Stefan judges by eye".
```

**Failure scenario:** After a change to apply_shading or calculate_shading, Claude regenerates 1_/2_/3_*.tif as CLAUDE.md demands and sends them. The files show destriped raw pixels, unaffected by the change. A regression in the shipped correction passes review by eye, or a real fix appears to do nothing. Pointing the tool at a raw library scan.tif gives a 'corrected' image with the full lamp falloff and no shading.

**Fix:** Rewrite make_comparison.py to take a library entry and write 1_nothing_done.tif = library.load (the raw decode), 2_corrected.tif = library.corrected(entry) and 3 = the inversion of that. Drop the destripe path, or keep it under an explicit --legacy-destripe flag. Update vignette-plan.md:208-210 and shading-calibration-plan.md:293-296.

<details><summary>Second reader's check</summary>

tools/make_comparison.py:24-29 imports only destripe/find_column_defects/flat_defect_sigma/resample_reference (they come from rps7200.defects, re-exported by direct.py:30-37), and line 117 writes `corrected = destripe(raw, defects, margin=12, dilate=5)` to 2_corrected.tif. Nothing in it calls apply_shading, library.corrected or loads shading.npz/ccd_mask.bin. Its inputs are two TIFF paths, with defaults under scans/ (lines 95-96). vignette-plan.md:209 says 'shading applied' and shading-calibration-plan.md:295 calls it 'the primary acceptance test'. If the input TIFF is a raw library scan.tif, output 2 carries no flat-field. If the input is an already-corrected CLI output, file 1 is not 'nothing done' either. In both cases the files do not test the shipped correction.

</details>

<a id="docs-plans-todo-doc-02"></a>

### DOC-02 -- library.signature ignores gain and byte14, so `duplicates --delete` would destroy the byte-14 and gain ladders that TODO says must not be pruned

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P11](../problems/P11-duplicates-delete-destroys-scans.md)

**Where:** `rps7200/library.py:639-698`, `rps7200/library.py:714-738`, `tools/library.py:115-135`, `rps7200/direct.py:2760-2804`, `tools/byte14_probe.py:140-151`, `tools/gain_probe.py:157-182`, `rps7200/direct.py:722-723`

**Doc claim:** TODO.md:70 'They are evidence and must not be pruned'; docs/analog-gain-plan.md:204-205; rps7200/library.py:642-645 signature docstring 'the device was told exactly the same thing'

Two things combine. (a) What the device was sent is incompletely recorded: MODE SELECT byte14 overrides, the SLIDE INIT param, skip_shading, and the full gain/offset payload fields such as light, extra_entries and double_times are absent from scan.json. (b) The duplicate detector treats anything not in its tuple as immaterial. Every pass of the byte-14 ladder has the same film notes, dpi, frame, depth, film, protocol revision, commanded exposure and fast_infrared, and so one signature. The same holds for every rung of the blue-gain ladder (analog-gain-plan). `prunable` keeps one per group ranked by raw, calibration, then created, and `duplicates --delete` rmtree's the rest.

**Evidence (from the code):**

```text
signature() returns `(stock, frame, subject, resolution_dpi, channels, frame, depth, film, protocol_revision, commanded, fast_infrared)`. The docstring says "Two entries sharing a signature are interchangeable: the device was told exactly the same thing about the same frame". scan() meta has no `byte14`, `slide_init_param`, `skip_shading` or `light` key. The byte14 probe calls `scanner.scan(..., exposure_scale=scales, auto_exposure=False, shading=False, keep_raw=True, byte14=byte14)` and files through debug with `film=FilmNotes(notes="captured with RPS7200_DEBUG on")`. The gain probe varies only `gain` (stored in device_settings, not in the signature). tools/library.py: `if args.delete: shutil.rmtree(path)`.
```

**Failure scenario:** An operator tidying the library runs `uv run python tools/library.py duplicates --delete` (the default --keep 1). 15 of the 16 byte-14 ladder entries (TODO.md:64-82 'evidence and must not be pruned') and all but one rung of the gain ladder (analog-gain-plan.md:204-205 'filed ... so this is re-analysable') are deleted irreversibly. Their raw bytes go with them. Nothing in the dry-run output says they differed in byte14 or gain, because nothing recorded it.

**Fix:** Record every commanded field in meta and scan.json: byte14 as sent (override or default), slide_init_param, skip_shading, quality word, and the full 29-byte SET GAIN OFFSET payload (or its hex). Add device gain, offset and byte14 to signature(), mirroring how commanded exposure and fast_infrared were added. Have `duplicates` refuse to delete entries tagged debug or probe unless --force.

<details><summary>Second reader's check</summary>

signature() (library.py:639-698) is the tuple as quoted. scan() meta (direct.py:2760-2804) has no byte14, slide_init_param, skip_shading or quality key, and library.save copies only the listed scan keys. byte14_probe.py:140-151 scans with a fixed `scales` and auto_exposure=False, so exposure_metered=False, and commanded is the same tuple on every rung. The gain probe varies only the gain written through a patched get_gain_offset, which lands in device_settings and not in the signature. Debug filing (direct.py:716-727) gives every entry FilmNotes(notes=...) with empty stock, frame and subject. prunable keeps one per group and `duplicates --delete` calls shutil.rmtree (tools/library.py:130-131). Worse than described: because debug-filed entries carry no stock, frame or subject, every metered debug entry (commanded=None) at the same dpi, channels, frame window, depth, film type and revision shares one signature. Unrelated pictures scanned on different days would be reduced to one entry.

</details>

<a id="docs-plans-todo-doc-03"></a>

### DOC-03 -- The calibration pass's raw bytes are never stored; the library keeps only the processed ShadingReference

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1755-1760`, `rps7200/direct.py:1946-1973`, `rps7200/direct.py:612-629`, `rps7200/shading.py:109-182`, `rps7200/shading.py:75-91`, `rps7200/library.py:182-183`, `rps7200/library.py:14-26`

**Doc claim:** rps7200/library.py:21-26 'raw.bin.gz is the ground truth and everything else is derived from it'; CLAUDE.md 'Every library entry keeps its raw bytes, its shading reference and its CCD mask, so decoding, correction and merging are all re-runnable offline'; docs/shading-calibration-plan.md:36-40

The ~1.66 MB calibration block the device returns (dark and light phases, per channel, 16-bit, with tags) is reduced by calculate_shading to per-column means. A level-gap heuristic with split_ratio=5.0 decides dark versus light, and the raw block is then discarded. Also discarded: the shading descriptor, the 128-byte calibration_info, and the gain/offset in force during calibration. shading.npz is therefore not 'exact bit data', and it is not derivable from raw.bin.gz as the library docstring implies. shading-calibration-plan.md:36-40 already records that the dark/light split 'does not transfer' and is kept on principle. It is the part most likely to change, and it cannot be re-run on any stored entry.

**Evidence (from the code):**

```text
calibrate_shading(keep_data: bool = False) ... `self._shading = calculate_shading(data, width)` ... `"data": data if keep_data else None`. No caller passes keep_data=True (grep: only direct.py:1759/1966). ensure_shading calls `self.calibrate_shading()` with no arguments. library.save: `if reference is not None: reference.save(path / "shading.npz")`. ShadingReference.save writes only `ref{c}`, `mean{c}`, `dark{c}`, `darkmean{c}`, `pixels_per_line`, `channels`. library.py docstring: "`raw.bin.gz` is the ground truth and everything else is derived from it."
```

**Failure scenario:** calculate_shading is improved (a different phase split, outlier-line rejection, or keeping the dark phase per line). None of the 217+ library entries can be re-corrected with the new reference computation, because the lines it would be computed from were thrown away at scan time. `reconstruct` cannot detect this, because it only re-decodes scan bytes.

**Fix:** Call calibrate_shading(keep_data=True) from ensure_shading. Store the calibration bytes (gzipped, with sha256 and the descriptor and calibration_info) once per session, and reference them from every entry, or copy them per entry as raw bytes already are. Record calculate_shading's parameters in shading.npz. Amend library.py's docstring accordingly.

<details><summary>Second reader's check</summary>

calibrate_shading (direct.py:1946-1973) reduces `data` through calculate_shading (shading.py:109-182: level-gap split with split_ratio=5.0, then per-column means) and returns data only when keep_data=True. No caller passes it: ensure_shading at direct.py:613, scan()'s lazy path at 2592 and uniformity.py:735 all call it bare. ShadingReference.save (shading.py:75-91) stores only derived float arrays and means. library.py:21 says raw.bin.gz is the ground truth that 'everything else is derived from', and shading.npz is not derivable from it. reconstruct only re-decodes scan bytes, so it cannot detect this.

</details>

<a id="docs-plans-todo-doc-04"></a>

### DOC-04 -- Several kinds of pass are never filed outside debug mode, and a roll frame's prescan.tif holds corrected pixels

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/direct.py:2080-2092`, `rps7200/direct.py:2905-2908`, `rps7200/direct.py:664-665`, `rps7200/session.py:1265-1271`, `tools/scan_roll.py:353`, `tools/scan_roll.py:492-510`, `rps7200/session.py:1804-1854`, `rps7200/session.py:1883-1893`, `rps7200/library.py:180-181`

**Doc claim:** CLAUDE.md 'File every scan in the library, with its raw bytes'; TODO.md:715-722; rps7200/direct.py:2087-2091 comment; docs/exposure-negative-plan.md:243-248 ('the filing rule ... both are filed' — true only under RPS7200_DEBUG=1)

The owner requires every scan's exact bytes in the library. In the paths an operator actually uses (window, tools/scan.py, tools/scan_roll.py, all with debug=False), the following are lost:
- every metering probe (up to 3 per metered scan; keep_raw only sets last_raw, which the next pass overwrites);
- every hold-loop and aim verification prescan except the last;
- the before-correction prescan on a real roll;
- every prescan of a CLI dry-run walk (TODO:715-722, still open; the line reference there is stale — debug=False is now at scan_roll.py:353).
In a filed roll frame, prescan.tif is the shading-corrected 8-bit prescan with no raw bytes and no `corrections_applied` marker. That breaks the 'library holds raw pixels' rule for that file.

**Evidence (from the code):**

```text
auto_exposure: `image, _ = self.scan(... keep_raw=True,)` with the comment "CLAUDE.md's rule is 'file every scan, with its raw bytes', without an exception for throwaway passes". Only `_debug_capture` files it: `if not self.debug: return`. The window forces `DirectScanner(... debug=False)` and both tools do too (`with DirectScanner(verbose=args.verbose, debug=False)`). The CLI dry run writes only `tiff.write(str(pre), frame.prescan)`. The hold loop takes `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)` per move. The window's real roll files the frame with `prescan=rf.prescan` (the corrected image; `raw_prescan` is unused on that branch), and prescan_before is handled only under `if job.dry_run:`.
```

**Failure scenario:** A question arises later about why metering landed where it did, or whether a hold move was mis-measured. The probe and verification passes that answer it exist nowhere. A CLI `--dry-run` walk of 15 frames leaves 15 corrected 8-bit TIFFs in rolls/ and nothing re-decodable. A future change to apply_shading cannot be applied to a roll frame's prescan.tif, which is already corrected and stored as if it were the framing pass.

**Fix:** File probes and verification passes through the same FrameWriter path as frames (they are small at 300 dpi), or keep them in a per-session spool that is filed after close. Make scan_roll.py's dry run submit prescans to FrameWriter with raw_image and capture, as ScanSession does. Store roll frames' prescan as raw (rf.raw_prescan) with its own raw bytes, or record `prescan.corrections_applied`.

<details><summary>Second reader's check</summary>

auto_exposure's probe passes set keep_raw=True (direct.py:2080-2092) but are filed only by _debug_capture, which returns early when debug is off (664-665). The window's session (session.py:1265-1271), tools/scan.py:160 and tools/scan_roll.py:353 all force debug=False, so they override even RPS7200_DEBUG=1. The scan_roll dry run writes only tiff.write of the corrected prescan (scan_roll.py:492-510). On the real-roll branch the window files the frame with `prescan=rf.prescan` (session.py:1883-1893), which is the corrected image, and raw_prescan is not passed. library.save writes prescan.tif with no corrections label (library.py:180-181, 286-290). The hold path (direct.py:3548-3553) replaces the prescan without keeping prescan_before, and intermediate hold and aim prescans are never filed.

</details>

<a id="docs-plans-todo-doc-05"></a>

### DOC-05 -- `tools/scan.py --bracket N --ir` merges the RGBI pass's blue with RGB passes using a ratio fitted on green

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `rps7200/direct.py:2385-2401`, `tools/scan.py:248-260`, `rps7200/bracket.py:291-301`, `rps7200/bracket.py:315-326`, `docs/multi-exposure-plan.md:319-321`, `docs/multi-exposure-plan.md:355`

**Doc claim:** docs/multi-exposure-plan.md:319-321 'one pass of the bracket is taken as RGBI ... serves as a bracket member'; :355 'Phase 2 does not begin until 1-6 pass'; :184 'Do not ship it'

With --ir the brightest bracket pass is RGBI and the rest are RGB. Blue in the RGBI pass is on a different scale (film-dependent, ~5-10x), but the merge divides every channel of that pass by the green-fitted slope. Unclipped blue from the RGBI pass therefore enters the inverse-variance blend about 5x too bright. IVW gives it the heaviest weight, because the longest pass has the least scaled variance. The multi-exposure plan says Phase 2 (infrared) must not begin until verification 6 passes, and it recommends not shipping. The CLI nonetheless exposes the combination.

**Evidence (from the code):**

```text
scan_bracket: `image, meta = self.scan(... infrared=infrared and last, ...)`. scan.py: `merged, stats = merge_bracket([f[..., :3] for f in frames], ratios)`. merge_bracket: `slope, intercept = solve_relation(ref[..., 1], np.asarray(frame)[..., 1])` ... `scaled.append(raw / r)`, so one green-derived ratio is applied to R, G and B. direct.py:316-351: blue comes back 4.98-5.02x (negative) or ~9.6x (B&W) brighter in an RGBI pass at the same exposure.
```

**Failure scenario:** `uv run python tools/scan.py --dpi 1800 --ir --bracket 3 --auto-exposure` on colour negative. The delivered scan.tif has a blue channel biased several-fold upward in the shadows and midtones, wherever the RGBI pass's blue is below CLIP_START. Nothing warns. The library passes are raw and intact, but the merged delivered file is wrong and is not filed.

**Fix:** Merge only RGB passes. Take the RGBI pass for its infrared plane only, and exclude its RGB from the merge or scale its blue by the measured per-film ratio. Alternatively, solve the relation per channel. Refuse --bracket with --ir until Phase 2 is validated, as the plan says.

<details><summary>Second reader's check</summary>

Only the last bracket pass is RGBI (`infrared=infrared and last`, direct.py:2385-2396). merge_bracket fits slope and intercept on green only (bracket.py:291-301), then divides every channel by that ratio (315-326). With infrared, auto_exposure meters blue down by blue_rgbi_headroom(film), about 5.2 for negative and 11 otherwise. The RGB passes' blue is therefore held low, while the RGBI pass's blue is about 5-10x brighter than the green-derived ratio predicts. tools/scan.py:248-260 merges `f[..., :3]` of every frame with no per-channel handling and does not refuse --bracket together with --ir. The merged file is written to --out and not filed.

</details>

<a id="docs-plans-todo-doc-a1"></a>

### DOC-A1 -- Debug filing deletes a scan's only spooled copy (raw bytes and pixels) even when library.save failed

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:713-752`

**Doc claim:** CLAUDE.md 'Claude: always scan with debug filing on' / direct.py:694-699 docstring

_debug_flush unlinks each item's spooled NNN-image.npy and NNN-raw.bin in a `finally` block, so it runs whether or not library.save succeeded, and then rmtree's the whole spool directory. If filing fails, for example with a full disk (a 7200 dpi entry is over 1 GB), a permissions error, an unwritable RPS7200_DEBUG_ROOT or a TIFF write error, the only surviving copy of that pass's raw bytes is deleted after a one-line log. The docstring's justification ('losing the record is better than losing the session') does not cover deleting the data itself. This is the path CLAUDE.md mandates for every ad-hoc and probe scan.

**Evidence (from the code):**

```text
for n, item in enumerate(pending, 1): ... try: ... entry = library.save(...) ... except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})") finally: ... image = None ... for key in ("image_path", "raw_path"): path = item.get(key) ... Path(path).unlink(missing_ok=True) ... then shutil.rmtree(self._debug_spool, ignore_errors=True)
```

**Failure scenario:** A byte-14 or gain ladder is run with RPS7200_DEBUG=1, and the library disk fills during the flush after close(). Every remaining pass logs 'could not file scan N' and its spool files are unlinked immediately afterwards. The scanner time is spent and the raw evidence is gone, with no entry and no spool left to recover from.

**Fix:** Unlink the spool files only when library.save returned successfully. On failure, keep the spool directory, log its path, and (optionally) provide a re-flush helper that files leftover spool items.

<a id="docs-plans-todo-doc-06"></a>

### DOC-06 -- `verify` reports a deliberate shading=False scan as 'correction was asked for'; code comments and TODO claim it splits the two cases correctly

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P12](../problems/P12-library-maintenance-tools.md)

**Where:** `rps7200/library.py:794-802`, `rps7200/direct.py:296-303`, `rps7200/library.py:359-364`, `rps7200/library.py:378-384`

**Doc claim:** TODO.md:84-90 'verify reads the identical field and correctly splits it'; rps7200/direct.py:299-302; rps7200/library.py:362-363

Only corrected() consults SHADING_SKIPPED_EXPLICIT. verify appends 'correction was asked for: <reason>' for any non-empty skipped reason, including 'shading=False (explicit)', which means the opposite. Every deliberately raw scan filed without a reference is therefore reported as a failed correction:
- `tools/scan.py --no-shading`, where ensure_shading(skip) leaves no reference;
- probes that pass shading=False without calibrating.
This also keeps `make verify` permanently red. There is still no way to mark an entry as deliberately uncalibrated (TODO:78-82, open).

**Evidence (from the code):**

```text
verify: `why = cal.get("skipped")` ... `f"{path.name}: no shading reference, so this scan can never be corrected" + (f" -- correction was asked for: {why}" if why else "")`. It never compares against SHADING_SKIPPED_EXPLICIT. direct.py:299-302: "library.corrected compares against this constant to tell the two apart, and `verify` already draws the same line." TODO.md:86-88: "verify reads the identical field and correctly splits it".
```

**Failure scenario:** `tools/scan.py --no-shading` is run for a raw comparison. `make verify` then prints '<id>: no shading reference, so this scan can never be corrected -- correction was asked for: shading=False (explicit)' and exits 1. The operator is told a correction was wanted and lost, and the check that exists to catch a real library regression stays red.

**Fix:** In verify, branch on `why == SHADING_SKIPPED_EXPLICIT` and report it as a note, or not at all. Let a tag such as `deliberately-raw` or `uncalibrated-evidence` suppress the message, as TODO suggests. Correct the claims at direct.py:299-302 and TODO.md:86-88.

<details><summary>Second reader's check</summary>

verify (library.py:794-802) appends 'correction was asked for: {why}' for any truthy `skipped`, including SHADING_SKIPPED_EXPLICIT. Only corrected() (378-384) compares against the constant. The case is reachable: `tools/scan.py --no-shading` calls ensure_shading(skip=True) (scan.py:167, direct.py:591-597), which leaves no reference, and scan() sets shading_skipped=SHADING_SKIPPED_EXPLICIT (direct.py:2757-2758). The entry therefore has no shading.npz and verify reports it as a shortfall. The claims at direct.py:299-302 and TODO.md:86-88 that verify 'draws the same line' are false.

</details>

<a id="docs-plans-todo-doc-07"></a>

### DOC-07 -- 7200 dpi column realignment is baked into scan.tif, not recorded, and not reproducible by reconstruct or decode_raw

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1637-1664`, `rps7200/library.py:411-436`, `rps7200/library.py:439-515`, `tools/library.py:173-182`, `docs/7200dpi-plan.md:55-64`, `docs/7200dpi-plan.md:235-239`, `TODO.md:560-568`

**Doc claim:** docs/7200dpi-plan.md:235-239 ('reconstruct -- clean against every stored entry except one ...'); TODO.md:560-568 ('Fixed in _realign_native_column_stagger'); CLAUDE.md 'library.corrected(entry) is what computes it, with today's correction code'

Since the stagger fix, every 7200 dpi pass (necessarily shading=False, because 7200 dpi with shading is refused) is stored with 4 fewer rows and odd columns shifted. That is a host-side transform, but the entry claims it holds the plain decode. Nothing records that the realignment was applied. The library's re-decode paths do not apply it:
- `reconstruct` reports every such entry as 'decode CHANGED';
- `migrate-raw` lists them as failed;
- `migrate-direction` leaves them 'not a plain decode'.
Conversely, the two 2026-09-11 entries stored before the fix are un-realigned, and library.corrected() hands them to the operator with the zigzag, because today's view path never applies the realignment. That contradicts 'library.corrected ... with today's correction code'.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... image = self._realign_native_column_stagger(image)` then `raw_pixels = image` (filed as the raw decode, with `corrections_applied` empty). reconstruct(): `image, direction = DirectScanner.decode_index(raw, params, ...)` ... `if image.shape != stored.shape: return image, (f"decode CHANGED: now {image.shape}, stored {stored.shape}")`. decode_raw uses `_deinterleave` with no realignment. The meta records `"height": int(image.shape[0])` (lines-4), while raw_layout records `"lines": int(params.lines)`.
```

**Failure scenario:** One 7200 dpi shading=False scan is filed. From then on `make reconstruct` exits 1 with a permanent false alarm. This is the same failure mode as the 26 prescan false alarms CLAUDE.md describes, and it masks a real decode regression. Opening either old 7200 dpi entry via Save As delivers the comb artefact the fix exists to remove.

**Fix:** Move the realignment into the decode path used by both scan() and the library (for example, decode_index(..., resolution) or a library-side 'derive' step keyed on scan.resolution_dpi). Alternatively, record `corrections_applied: ["column_stagger"]` and have reconstruct re-apply it before comparing, as it does for legacy shading. Make corrected() apply it to legacy entries.

<details><summary>Second reader's check</summary>

scan() realigns 7200 dpi images before `raw_pixels = image` (direct.py:2695-2708). The filed scan.tif is therefore lines-4 tall with odd columns shifted, and neither corrections_applied nor any other key records it. reconstruct (library.py:439-515) and decode_raw (411-436) decode with decode_index/_deinterleave and never realign, so the shape check at 492-495 returns 'decode CHANGED'. library.py:581 itself names 'a realigned 7200 dpi pass' as not a plain decode. corrected() never realigns, so entries filed before the fix are delivered unrealigned. docs/7200dpi-plan.md:235-239 predates any post-fix entry and does not mention this.

</details>

<a id="docs-plans-todo-doc-08"></a>

### DOC-08 -- TODO's reversal 'won't-fix' and 'orientation unresolved' entries contradict the code: the decode now turns reversed passes upright

**Severity** medium · **Category** doc-mismatch · **Verdict** partly

**Where:** `TODO.md:117-178`, `rps7200/direct.py:1551-1600`, `rps7200/direction.py:85-114`, `rps7200/library.py:496-510`

**Doc claim:** TODO.md:117-178, 752-799, 1134-1139

TODO.md:117-178 still describes per-pass row reversal as a won't-fix that tools/scan.py files upside down. Since 622aa5a, decode_index turns every bottom-up pass upright from its line tags, and the direction is recorded as scan.read_direction and checked by reconstruct. The 'Orientation is unresolved' item at TODO.md:1134-1139 concerns the transport's viewing orientation and remains accurate.

**Evidence (from the code):**

```text
TODO.md:117-125: "Closed 2026-09-13 as won't-fix ... auto-detecting the tag-lead signature adds a decode-time guess to every scan. Neither is worth it"; :164-168: "tools/scan.py --dpi 1800 --ir --frame 2  # reversed ... gives every second frame upside down, filed that way"; :1136: "The vertical flip is currently applied only to hand-delivered files, never in `scan()`". Code: `direction = read_direction(blob, bpl, ...)`, `step = -1 if direction.reversed else 1`, `image = np.stack([np.array(planes[c][:height][::step]) ...` (decode_index), and `_note_reversal`: "A pass whose own lines said which way it was read is never turned here."
```

**Failure scenario:** Someone reading TODO as the list of open problems adds a flip to delivered files, or a manual flip step. That double-turns passes the decode already righted. Alternatively they re-open a protocol change (forcing bit 0 clear) to fix a problem already handled host-side, and bump PROTOCOL_REVISION for nothing.

**Fix:** Rewrite TODO.md:117-178 and 1134-1139 to state the implemented behaviour (decode-time correction from line tags, recorded as read_direction, raw bytes untouched). Keep the measurement history under a 'resolved 2026-09-23' heading. Also update the stale 'session.py:570' reference at TODO.md:776.

<details><summary>Second reader's check</summary>

TODO.md:117-178 is contradicted by code. decode_index (direct.py:1551-1600) reads the direction from the line tags and reverses rows (`step = -1 if direction.reversed`), and reconstruct checks read_direction. That is exactly the 'decode-time guess' TODO says was rejected, and the tools/scan.py example ('every second frame upside down, filed that way') no longer holds. commit 622aa5a (2026-09-23) is newer than TODO's last edit (6eac892, 2026-09-22). TODO.md:1134-1139 ('Orientation is unresolved') is about the systematic transport orientation for viewing (the rotation/flip preference), not per-pass bottom-up reversal, and scan() still applies no viewing flip, so that part is not contradicted.

</details>

<a id="docs-plans-todo-doc-09"></a>

### DOC-09 -- PROTOCOL_REVISION is 6 and lives in protocol.py; TODO's first known problem and several docs say 4, or direct.py

**Severity** medium · **Category** doc-mismatch · **Verdict** partly

**Where:** `rps7200/protocol.py:16-40`, `TODO.md:43-62`, `TODO.md:156`, `docs/byte14-plan.md:295`, `docs/step-calibration-plan.md:207-208`, `CLAUDE.md:450`

**Doc claim:** TODO.md:43-62, 156; docs/byte14-plan.md:295; docs/step-calibration-plan.md:207-208; CLAUDE.md:450

TODO's first known-problem check expects protocol_revision 4, but the code writes 6. Docs name direct.py as the constant's home, but it is defined in protocol.py and only re-exported by direct.py. The 1.572->1.84 ramp change (d723896) altered the sub-frame SLIDE params sent without a bump, although revision 5 was bumped for the analogous MAX_CORRECTION_PARAM change. protocol_revision therefore cannot tell offline analysis which ramp a hold move used.

**Evidence (from the code):**

```text
protocol.py: `PROTOCOL_REVISION = 6` with history entries '6: the transport is asked where the film is' and '5: MAX_CORRECTION_PARAM 8 -> 87'. TODO.md:57-58: "checking that the entries read `protocol_revision: 4`". TODO.md:156 and byte14-plan.md:295: "PROTOCOL_REVISION in rps7200/direct.py". step-calibration-plan.md:207-208: "Correcting them changes what param_for_mm returns, so PROTOCOL_REVISION moves." Commit d723896 changed COMMAND_UNITS 1.572 -> 1.84 (changing param_for_mm output) with no revision bump.
```

**Failure scenario:** The first scan after this checkout is checked against TODO's instruction. It reads protocol_revision 6, so the operator either believes something is wrong or edits the constant back. Offline analysts cannot tell from protocol_revision whether hold-loop moves used the 1.572 or the 1.84 ramp.

**Fix:** Update TODO.md:43-62 to revision 6, and point every doc at rps7200/protocol.py. Decide and document whether sub-frame SLIDE law changes bump the revision, then apply the rule consistently (either bump for d723896 or state that SLIDE params are outside it and un-bump the reasoning for 5).

<details><summary>Second reader's check</summary>

PROTOCOL_REVISION = 6 at protocol.py:40. TODO.md:57-58 still says to check for revision 4. direct.py imports and re-exports the name (direct.py:91,188), so 'in rps7200/direct.py' is misleading rather than false: editing it there would not change the value. d723896 changed MM_PER_COMMAND from 0.1662 to 0.1945 mm and so changed param_for_mm output (direct.py:3134 through OVERHEAD_MM=MM_PER_COMMAND), with no revision bump. Revision 5 was bumped for a comparable sub-frame change, and step-calibration-plan.md:207-208 says such a correction moves the revision. The inconsistency is real.

</details>

<a id="docs-plans-todo-doc-10"></a>

### DOC-10 -- `tools/scan_roll.py --start-at N` 'resume' overwrites the earlier roll.json instead of resuming from it

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/scan_roll.py:20-24`, `tools/scan_roll.py:296-329`, `tools/scan_roll.py:451-453`, `tools/scan_roll.py:633-634`, `rps7200/session.py:1643-1668`

**Doc claim:** tools/scan_roll.py:22-24 docstring; TODO.md:452-466 (resume)

The CLI never reads the existing manifest. Following its own advice to resume replaces rolls/<roll>/roll.json with a manifest holding only the new run's frames. The earlier frames' records are lost from the one file meant to describe the roll: entry paths, registration, transport positions and errors. Library entries survive, but the roll-to-entry mapping and the per-frame registration evidence do not. The window path merges correctly, so the two tools diverge.

**Evidence (from the code):**

```text
Module docstring: "a crash two hours in should cost the frame it was on, not the roll -- `--start-at` resumes from the manifest." Code: `manifest = {"roll": roll_name, ... "frames": [],}`, then `out.mkdir(...); checkpoint(); placed = True`, where checkpoint writes `manifest_path.write_text(json.dumps(manifest ...))`. There is no read of an existing roll.json. The failure message is `"resume a failed picture with --start-at N"`. By contrast, ScanSession._roll reads `earlier = json.loads(manifest_path.read_text(...))` and merges.
```

**Failure scenario:** A 38-frame roll dies at frame 20. The operator runs the suggested `--start-at 20 --roll same-name`. roll.json now lists frames 20-38 only. Frames 1-19's registration and entry links are gone, and the window's 'Rolls ...' reopen shows those frames as unscanned, inviting a rescan.

**Fix:** Load and merge an existing roll.json exactly as ScanSession._roll does (renumbered + replace per frame), or refuse to write into a folder whose roll.json exists without --append. Fix the docstring.

<details><summary>Second reader's check</summary>

scan_roll.py builds a fresh manifest with `"frames": []` (296-329), and checkpoint() overwrites manifest_path once the roll is placed (451-453). No code reads an existing roll.json. The failure hint 'resume a failed picture with --start-at N' (633-634) and the docstring (22-24, '--start-at resumes from the manifest') promise otherwise. ScanSession._roll does read and merge the earlier manifest (session.py:1643-1668). Also, the default roll name is today's date, so a resume on another day without --roll writes to a different folder.

</details>

<a id="docs-plans-todo-doc-11"></a>

### DOC-11 -- `--reuse` loads any cached reference silently, and entries do not record where the reference came from (TODO open item, still present)

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:600-610`, `rps7200/direct.py:549-561`, `rps7200/shading.py:75-91`, `rps7200/library.py:182-183`, `rps7200/library.py:281-296`, `tools/gui.py:1999-2004`

**Doc claim:** TODO.md:397-413

TODO.md:397-413 is still accurate. The window now warns by file mtime before offering a cached reference (gui.py:2000-2004), but the CLI tools do not. More importantly for re-evaluation, no library entry records whether its shading.npz was measured this session or loaded from a cache (and from when). A stale reference is indistinguishable from a fresh one forever after.

**Evidence (from the code):**

```text
`if reuse and path.exists(): reference = self.load_shading(path); return {"action": "loaded", ...}`. ShadingReference.save stores only arrays (no timestamp, session id, device, exposure). The library 'calibration' block records `shading`, `ccd_mask`, `pixels_per_line`, `light_mean`, `report` and `skipped`, but not the ensure_shading action or source path.
```

**Failure scenario:** `tools/scan.py --reuse` a week after the cache was written. The scan is corrected with last week's sensor state and filed. Months later an analysis comparing column residuals across entries treats it as a same-session reference and draws wrong conclusions. Nothing in scan.json says otherwise.

**Fix:** Write created time, session or power-on marker, device inquiry, and gain/offset into shading.npz. Record `calibration.source` = measured|loaded and the load path and age in scan.json. Warn in the CLI when the cache predates the current power-on.

<details><summary>Second reader's check</summary>

ensure_shading(reuse=True) loads any file at the path (direct.py:600-610). ShadingReference.save writes arrays only, with no time or device identity (shading.py:75-91). library.save's calibration block has no source, action or age (library.py:269-286). The window shows an mtime-based age note (gui.py:1999-2004). tools/scan.py and tools/scan_roll.py only print the 'reusing ...' summary.

</details>

<a id="docs-plans-todo-doc-12"></a>

### DOC-12 -- `apply_shading` silently leaves trailing columns uncorrected; the plan marked DONE says scan() refuses in that case, and two docs disagree on whether 600 dpi triggers it

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/shading.py:235-281`, `rps7200/shading.py:185-193`, `rps7200/direct.py:2719-2733`, `docs/shading-calibration-plan.md:212-214`, `docs/vignette-plan.md:296-302`, `TODO.md:415-418`

**Doc claim:** docs/shading-calibration-plan.md:212-214; docs/vignette-plan.md:296-302; TODO.md:415-418

The guard the shading plan specified (compare width to the mask's used count) was never implemented. The implemented guard compares width to the reference width, which catches only the 7200 dpi case. When the mask marks fewer used pixels than the pass width, the rightmost columns are returned with raw values beside corrected ones. That is a visible edge band in the delivered file, and library.corrected() reproduces it. The report records columns < width, but nothing acts on it. The two docs make contradictory factual claims about whether this happens at 600 dpi (860 vs 862).

**Evidence (from the code):**

```text
`loc = build_width_to_loc(bytes(ccd_mask), w)` returns `locs[:width]` (can be shorter than w) ... `out[:, : loc.size, c] = vals.astype(image.dtype)`. The only guard in scan() is `params.width > self._shading.pixels_per_line`. shading-calibration-plan.md:212-214: "`scan()` must refuse to correct when `width > len(used pixels in mask)` ... Log and return raw". TODO.md:418: "at 600 dpi width and columns both came back 860". vignette-plan.md:299-300: "At 600 dpi the CCD mask marks 860 used pixels while `get_parameters()` reports a width of 862".
```

**Failure scenario:** A pass at a resolution where the mask's used count is below the returned width. The delivered TIFF's last few columns carry the full uncorrected lamp falloff and column pattern, at the frame edge where it is easiest to mistake for vignette or film edge. The scan is filed as successfully corrected.

**Fix:** In scan() (and corrected()), raise ShadingUnavailable, or at least mark the report, when loc.size < width. Alternatively extend loc using the mask's pitch. Resolve the 860/862 contradiction from stored entries (`shading.report.columns` vs `width`) and correct whichever doc is wrong.

<details><summary>Second reader's check</summary>

build_width_to_loc returns `locs[:width]`, which can be shorter than w (shading.py:185-193). apply_shading then writes only `out[:, :loc.size, c]` and leaves trailing columns raw (235-281). The report records columns < width, but nothing acts on it. scan()'s guard compares params.width only to reference.pixels_per_line (direct.py:2719-2733), not to the mask's used count that shading-calibration-plan.md:212-214 specified. vignette-plan.md:299-300 (860 vs 862) and TODO.md:418 (860 vs 860) contradict each other on whether 600 dpi triggers it, so real-world reachability is unproven.

</details>

<a id="docs-plans-todo-doc-14"></a>

### DOC-14 -- Frame width has contradictory values across code and docs; the driver's own path still centres on 36.0 mm

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/framing.py:584-586`, `rps7200/framing.py:740`, `rps7200/framing.py:1950-1957`, `rps7200/framing.py:1264-1271`, `tools/frame_edges/centre.py:25-37`, `TODO.md:886-910`, `docs/step-calibration-plan.md:185-191`, `docs/registration-accuracy-plan.md:55-59`, `docs/frame-measurement-plan.md:217-223`, `research/frame-edge/REPORT.md:204-209`

**Doc claim:** TODO.md:886-910; docs/step-calibration-plan.md:185-203; docs/registration-accuracy-plan.md:12,55-66; research/frame-edge/REPORT.md:208,227-228

Five incompatible frame widths are stated as measured: 326, 334, 340.6 (36 mm), 350.6 units, 35.3 mm, and 438-440 columns. Two of them live in code. The frame_edges proposals centre with 350.6 units. StripWalk without a reader, picture_span's right-edge conversion, strip_offsets and FRAME_WIDTH_SPREAD_MM still use 36.0 mm and TARGET_GAP_MM. TODO recommends lowering the width to 35.3 mm, which is the opposite direction from the later 350.6-unit measurement. step-calibration-plan's pitch 337 contradicts PITCH_UNITS 366.5. registration-accuracy-plan cites 'rolls/stefan-judgement.json' where the file is docs/stefan-judgement.json.

**Evidence (from the code):**

```text
framing.py: `FRAME_WIDTH_MM = 36.0` ("Assumed rather than measured") -> `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0`; `FRAME_WIDTH_UNITS = 350.6` (= 37.06 mm, used by tools/frame_edges). TODO.md:886: "The frame is about 35.3 mm across, not the 36.0". step-calibration-plan.md:190: frame "326" units, pitch "337". registration-accuracy-plan.md:57: "316 to 336 units, median 334". REPORT.md:227-228: "The film says about 440 columns; the code says 425".
```

**Failure scenario:** A session with no edge_reader set (a ScanSession not created by the window, or any fallback) aims frames using TARGET_GAP_MM from 36 mm. The window's sheet aims with 350.6 units. The same strip gets different centring depending on path. Someone following TODO.md:886-910 lowers FRAME_WIDTH_MM to 35.3 and moves the fallback further from the frame_edges answer.

**Fix:** Pick one measured width (FRAME_WIDTH_UNITS), derive FRAME_WIDTH_MM and TARGET_GAP_MM from it (or retire the mm constants), and mark TODO.md:886-922, step-calibration-plan's results table and registration-accuracy-plan:55-66 as superseded with a pointer to research/frame-edge.

<details><summary>Second reader's check</summary>

framing.py:586 `FRAME_WIDTH_MM = 36.0` ('Assumed rather than measured') feeds TARGET_GAP_MM (740), strip_offsets (789), picture_span's right-edge reading (1354-1359, 1436) and MAX_CORRECTION_MM (1281). FRAME_WIDTH_UNITS = 350.6 (1957, about 37.06 mm) is used by tools/frame_edges. walk_reader returns None for films it does not read (propose.py:250-254), and StripWalk then falls back to the 36 mm path, so two paths centre on different widths. The doc values differ as described.

</details>

<a id="docs-plans-todo-doc-15"></a>

### DOC-15 -- TODO's safety rule 'no single detector may move film' is bypassed on the live path: with an edge reader, one frame_edges member can decide

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `TODO.md:214-218`, `rps7200/framing.py:1725-1733`, `tools/frame_edges/vote.py:90-109`, `tools/gui.py:8186`, `tools/scan_roll.py:476`

**Doc claim:** TODO.md:214-218

The window and scan_roll.py always hand StripWalk the frame_edges reader, so `combine`'s two-member agreement never runs on the paths an operator uses. ensemble_v2's lone_gap rule lets a single member's gap-with-neighbour stand. That may be a sound decision (it is pre-registered in the REPORT), but TODO presents the opposite as the property that closed the registration problem.

**Evidence (from the code):**

```text
TODO.md:216-217: "plus the rule that no single detector may move film. `combine` requires two members that agree in millimetres". StripWalk.judge: `if self.reader is not None: decision, detail = self.reader.judge(int(number), image) ... return decision, dict(detail)`, which skips combine. vote.lone_gap: `return Side(EDGE, x=s.x, ..., conf=0.3, ..., note=f"gap with neighbour, one vote: {name}")`. The window sets `session.edge_reader = frame_edges.walk_reader`.
```

**Failure scenario:** A reviewer relying on TODO's statement treats an in-walk correction as two-detector-confirmed when it rested on one member (conf 0.3). A single-member false gap moves film with no second opinion.

**Fix:** Update TODO.md:203-218 to describe the current decision rule (frame_edges ensemble_v2 including lone_gap), or gate lone_gap decisions from moving film in-walk (proposal only, not auto-aim).

<details><summary>Second reader's check</summary>

With a reader, StripWalk.judge returns reader.judge's decision directly (framing.py:1725-1730). WalkReader._read uses detect -> vote_v2, which accepts lone_gap (vote.py:90-109), and centring returns a non-None offset tagged source 'unconfirmed' (propose.py:139-143). _aim_frame moves the film on any non-None decision within tolerance and budget, without checking source (direct.py:2985-3041). The window always sets session.edge_reader = frame_edges.walk_reader (gui.py:8186), and scan_roll.py passes it too. TODO.md:216-217 says no single detector may move film.

</details>

<a id="docs-plans-todo-doc-16"></a>

### DOC-16 -- The window-roll lazy-calibration TODO item is partly stale, but a race remains: `calibrated` is set True before the Calibrate job succeeds

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `TODO.md:688-703`, `tools/gui.py:1929-1933`, `tools/gui.py:1946-1958`, `tools/gui.py:3235-3237`, `rps7200/session.py:1381-1399`, `rps7200/session.py:1608-1631`, `rps7200/direct.py:2579-2592`

**Doc claim:** TODO.md:688-703

TODO says ask_to_calibrate is non-modal and can be dismissed, letting a roll reach the lazy in-scan calibration. Since then every picture-taking control calls _calibration_missing, so dismissing the prompt starts nothing. But on_calibrate marks the window calibrated optimistically. Between submitting Calibrate and its 'calibrated' event, Roll/Scan/Prescan are allowed and queue behind it. If the calibration fails, returns no usable reference, or was mode 'off' (ensure_shading(skip=True)), the queued roll's first prescan calls calibrate_shading() inside the scan flow. TODO records that path as stalling this machine with LIBUSB_ERROR_PIPE.

**Evidence (from the code):**

```text
on_calibrate: `self.session.submit(Calibrate(mode=mode or self.v_shading.get(), ...))` then immediately `self.calibrated = True`. _calibration_missing: `if self.calibrated: return False`. ScanSession._roll still never calls ensure_shading. scan(): `if self._shading is None or needed > ...: self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`.
```

**Failure scenario:** The operator presses Calibrate with the mode set to 'off' (or the calibration errors), then immediately presses Roll. The window accepts it, because calibrated is True until the event arrives. The Roll job runs with no reference and lazily calibrates inside prescan(), which is the documented stall and power-cycle risk.

**Fix:** Set calibrated only on the 'calibrated' event. Have ScanSession._roll and _scan refuse (raise) rather than lazily calibrate when no reference is held, or call ensure_shading with the session's reference path there. Update TODO.md:688-703 to the current state.

<details><summary>Second reader's check</summary>

on_calibrate sets self.calibrated = True right after submit (gui.py:1929-1933). The real value arrives later via the 'calibrated' event (3235-3237). _calibration_missing returns False while the flag is True (1946-1958), so a Roll or Scan pressed in that window is accepted. If the Calibrate job was mode 'off' (v_shading can be set to it) or failed, session._calibrate emits done=0, but the queued Roll's prescan (shading=True) reaches scan()'s lazy calibrate_shading (direct.py:2579-2592). ScanSession._roll never checks self.calibrated or calls ensure_shading.

</details>

<a id="docs-plans-todo-doc-27"></a>

### DOC-27 -- `tools/scan.py --bracket` silently ignores a single-value `--exposure-scale`

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `tools/scan.py:143-148`, `tools/scan.py:205-218`, `rps7200/direct.py:2373-2378`

**Doc claim:** TODO.md:366-381 (sister bug recorded as fixed)

A scalar --exposure-scale disables auto-exposure and is then dropped, because it is a float rather than a list, so the bracket runs from the device base exposure. This is the same class of bug as the --no-fast-ir drop TODO.md:366-381 records as fixed, on the neighbouring flag, and it is not recorded.

**Evidence (from the code):**

```text
`exposure_scale = parts[0] if len(parts) == 1 else parts` ... scan_bracket(`auto_exposure=args.auto_exposure and not args.exposure_scale`, `exposure_scale=(list(exposure_scale) if isinstance(exposure_scale, list) else None)`) -> scan_bracket: `elif auto_exposure: ... else: scales = [1.0, 1.0, 1.0]`.
```

**Failure scenario:** `tools/scan.py --bracket 3 --exposure-scale 2.5` to hold a known exposure across a comparison bracket. The passes run at x1.0 of base times the ladder. The metadata's exposure_scale reveals it only if someone reads it, and the bracket is not the measurement that was asked for.

**Fix:** Pass `[exposure_scale]*3` when scalar (or accept float in scan_bracket), and add a test like the --no-fast-ir one.

<details><summary>Second reader's check</summary>

scan.py:145-146 turns a single value into a float. Line 211 then disables auto-exposure (`args.auto_exposure and not args.exposure_scale`), and lines 212-215 pass None because the value is not a list. scan_bracket (direct.py:2373-2378) falls through to [1.0,1.0,1.0]. The requested scale is silently dropped. The entries record exposure_scale truthfully, but the measurement is not the one asked for.

</details>

<a id="docs-plans-todo-doc-a2"></a>

### DOC-A2 -- Bracket membership, ratio and metering evidence are dropped from filed bracket passes; the merged result is never filed

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2373-2378`, `rps7200/direct.py:2388-2405`, `rps7200/direct.py:2805-2808`, `rps7200/library.py:236-262`, `tools/scan.py:239-266`

**Doc claim:** docs/multi-exposure-plan.md (bracket filing); library.py:131-160 save docstring 'everything needed to use it again'

Each bracket pass is filed as an ordinary entry. The record does not say that it belongs to a bracket, which member it is, the ladder ratio, the pass count or the stops, and it does not carry the metering rounds that set the base exposure. Group membership can only be inferred from timestamps and exposure_scale. tools/scan.py writes the merged image and its bracket stats only to --out and its .json sidecar, not to the library, so the merge cannot be re-evaluated against the filed passes without reconstructing the grouping by hand.

**Evidence (from the code):**

```text
scan_bracket: `meta["bracket_index"] = i; meta["bracket_ratio"] = float(k); meta["bracket_passes"] = passes; meta["bracket_stops"] = float(stops)`; library.save copies only the fixed key list (resolution_dpi ... filter_offsets), which contains no bracket_* key. scan_bracket meters with `self.auto_exposure(film=film, infrared=infrared)` and then calls scan() without auto_exposure, and scan() attaches metering only `if auto_exposure and self.last_metering is not None`. grep: bracket_index appears nowhere else in the repo.
```

**Failure scenario:** Months later someone re-runs merge_bracket with improved code over library entries. Nothing identifies which entries formed one bracket or in which order. Two brackets of the same frame taken the same day, one with --ir, are indistinguishable except by timestamps, and the metering that chose the base exposure is not stored anywhere.

**Fix:** Add bracket_index, bracket_ratio, bracket_passes, bracket_stops and a shared bracket id to library.save's scan block, attach the bracket's metering record to each member, and file (or at least reference) the merged output with corrections=['bracket_merge'].

<a id="docs-plans-todo-doc-13"></a>

### DOC-13 -- Roll-wide sub-frame travel is unbounded on the approved (contact-sheet) path, and the per-frame budget still scales with the target

**Severity** low · **Category** design · **Verdict** partly

**Where:** `rps7200/direct.py:3422-3423`, `rps7200/direct.py:3529-3547`, `rps7200/framing.py:1210-1213`, `rps7200/framing.py:1160-1169`, `TODO.md:705-713`

**Doc claim:** TODO.md:705-713; docs/registration-accuracy-plan.md:104-107

On the contact-sheet (approved) path, nothing sums sub-frame travel across a roll: ROLL_TRAVEL_LIMIT_MM is enforced only through StripWalk, which exists only with correct/correct_dry_run. The per-frame budget scales with |target|. Because each frame is held against its own walk prescan, accumulated creep is re-measured per frame. The exposure is unbounded total travel, not silent mis-registration. TODO's line reference is stale.

**Evidence (from the code):**

```text
`walk = (StripWalk(...) if (correct or correct_dry_run) else None)`. On the approved branch, `fix = self._hold_to_approved(...)` consults no walk or travel budget. hold_plan: `budget = abs(target_mm) + HOLD_HEADROOM_MM`. ROLL_TRAVEL_LIMIT_MM is enforced only in StripWalk.record/affordable.
```

**Failure scenario:** A sheet with several large proposed offsets of the same sign (for example from a detector bias) is commissioned. Each frame moves up to |target|+2 mm, the film creeps several mm across the strip, and later frames' whole-frame advances land on the wrong picture area. No abort fires, because holding only stops on wrong_way or three consecutive misses.

**Fix:** Carry a roll-level travel total on the approved path (reuse StripWalk.travel_mm or a small accumulator) and stop holding past ROLL_TRAVEL_LIMIT_MM. Base the per-frame budget on a constant rather than |target|.

<details><summary>Second reader's check</summary>

This part is real: StripWalk, the only holder of ROLL_TRAVEL_LIMIT_MM, is built only when correct or correct_dry_run is set (direct.py:3422-3423). The approved branch (3529-3547) calls _hold_to_approved with no roll-level accumulator, and hold_plan's budget is |target|+HOLD_HEADROOM_MM (framing.py:1210). This part is overstated: the hold loop measures each frame against that frame's own walk prescan and aims at the operator's offset from it. Creep from earlier frames is therefore re-measured and corrected rather than compounding into 'later frames land on the wrong picture area'. The gap is total mechanical travel and wear, not silent mis-registration. TODO.md:705-713 describes it accurately apart from a stale line reference (3162 -> 3422).

</details>

<a id="docs-plans-todo-doc-17"></a>

### DOC-17 -- Resume does not restore 'the exposure/gain/offset the scanner was asked for'; a code comment references a manifest `device` block that is never written

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `TODO.md:452-458`, `rps7200/session.py:1697-1701`, `rps7200/session.py:1716-1734`, `rps7200/session.py:877-935`, `tools/gui.py:4780-4792`

**Doc claim:** TODO.md:452-458

No exposure, gain or offset is restored on resume, and a Roll cannot carry one: it either meters, or scans at the device default (scales 1.0) for meter 'none'. Per-frame records do store the metered exposure, gain and offset, but nothing reads them back. The comment describing a `device` block describes code that does not exist.

**Evidence (from the code):**

```text
TODO.md:453-456: "restores the roll's own settings from its manifest -- resolution, film, infrared, metering, and the exposure/gain/offset the scanner was asked for". session.py:1697: "`device` holds what the scanner was *asked* for", but the manifest has no 'device' key. RESTORABLE = {resolution, prescan_resolution, infrared, fast_infrared, film, meter, mono_channel, correct, reverse_hold, frames, start_at}. The Roll dataclass has no exposure field.
```

**Failure scenario:** A roll scanned with meter 'once' is resumed a year later. It re-meters on its first new frame, so the resumed frames are not on the same exposure as the earlier half, contrary to what TODO says a resume guarantees.

**Fix:** Either implement the device block (write the commanded scales and restore them via an explicit exposure_scale on Roll) or correct TODO.md:452-458 and session.py:1697-1701.

<details><summary>Second reader's check</summary>

The manifest dict in session.py has no 'device' key (grep finds none), although the comment at 1697-1701 describes one. RESTORABLE (gui.py:4780-4792) has no exposure, gain or offset. The Roll dataclass has no exposure_scale field (only Scan has one, at session.py:865-866). TODO.md:453-456's claim that resume restores exposure/gain/offset is false.

</details>

<a id="docs-plans-todo-doc-18"></a>

### DOC-18 -- fast-infrared-plan still says, in present tense, that fast IR is not the default and that PROTOCOL_REVISION is not bumped

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/fast-infrared-plan.md:61-65`, `docs/fast-infrared-plan.md:114-133`, `docs/fast-infrared-plan.md:479-486`, `rps7200/direct.py:2439`, `rps7200/direct.py:2635`, `rps7200/session.py:862`, `rps7200/session.py:891`

**Doc claim:** docs/fast-infrared-plan.md:61-65,114-133,479-486

The plan's own status header (line 3) says the bit was adopted and the revision moved to 3. Three later sections still assert the opposite as current behaviour.

**Evidence (from the code):**

```text
fast-infrared-plan.md:61: "**Still not adopted as a default**"; :119: "`fast_infrared` defaults to False everywhere, so the payload an ordinary scan sends is byte-identical"; :479: "What is still missing before it becomes the default". Code: `fast_infrared: bool = True` (scan, scan_bracket, Scan, Roll), gated by `fast_infrared = bool(fast_infrared and infrared)`.
```

**Failure scenario:** A reader of the section titled '`PROTOCOL_REVISION` is deliberately not bumped' concludes that RGBI entries at revisions 3+ are byte-identical to revision 2 and treats them as duplicates or comparable.

**Fix:** Mark lines 61-65, 114-133 and 479-486 as superseded, or rewrite them in the past tense.

<details><summary>Second reader's check</summary>

fast_infrared defaults to True in scan (direct.py:2439), scan_bracket (2336), scan_roll (3314), Scan and Roll (session.py:862,891). The plan's lines 61, 114-120 and 479-486 still say it is not the default and that the revision is not bumped, contradicting its own header at line 3.

</details>

<a id="docs-plans-todo-doc-19"></a>

### DOC-19 -- protocol.md: SLIDE law, ramp, param cap, backward fine step, §12 and §1 contradict the code or the rest of the document

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/protocol.md:18-19`, `docs/protocol.md:290-294`, `docs/protocol.md:348`, `docs/protocol.md:355`, `docs/protocol.md:790-792`, `docs/protocol.md:811-813`, `docs/protocol.md:841-845`, `docs/protocol.md:856-859`, `docs/protocol.md:465-466`, `docs/protocol.md:272-276`, `rps7200/protocol.py:260-279`, `rps7200/direct.py:3123`, `rps7200/direct.py:3126-3136`, `rps7200/direct.py:3138-3159`, `rps7200/usb_transport.py:693-701`

**Doc claim:** docs/protocol.md:18,290-294,348,355,790-792,841-845,856-859,465-466

The wire-protocol reference, the document most likely to be consulted before sending a command, carries the superseded 0.1662 mm intercept and 1.57-unit ramp, the old param-8 cap, and a statement that backward param-1 moves are not sent (the code sends them routinely). §12 still lists byte 14 and the mirrored scans as unexplained after §4 and §11 resolved them. Capture counts also disagree across docs: 'seven sessions, 8,133 commands' (protocol.md:3-4), 'six captures, 3,955' (CLAUDE.md, scanner-options-survey:227-228), 'nine captures' (dpi-tradeoff-plan:222-223).

**Evidence (from the code):**

```text
protocol.md:290/294/812: `distance = 0.1057 mm x param + 0.1662 mm`, `param = round((millimetres - 0.1662) / 0.1057)`. Code: `MM_PER_COMMAND = MM_PER_UNIT * COMMAND_UNITS` with COMMAND_UNITS=1.84 (0.1945 mm). :355: "`DirectScanner.OVERHEAD_MM` (1.57 units)"; code `OVERHEAD_MM = MM_PER_COMMAND` (1.84). :348: "`param 1` at about 2.6 units", against 2.84 in code. :790-791: "Correcting drift uses `param 1` to about `8`", against `MAX_CORRECTION_PARAM = 87`. :841-845: "`01 01 00 04` ... appears in no capture, and ... invented payloads are not sent", yet nudge sends `self.slide(0x00 if forward else 0x01, param=param, value=0x04)` with param floored at 1. :856-859 (§12): "MODE SELECT byte 14 entirely ... nothing has replaced the explanation", contradicting §4 'Corrected 2026-09-23'. :18: command out "`wValue=0x0088`, one byte at a time", whereas code sends CDB bytes on PORT_SCSI_CMD 0x0085 (`self._control_out(PORT_SCSI_CMD, byte)`). :465-466 says CyberView ends with `05 01 00 01` and `03 f6 dd 00`, while :272-276 says neither appears in any capture.
```

**Failure scenario:** A probe or tool written from protocol.md §5 computes params with the 0.1662 intercept and disagrees with param_for_mm by a whole param for some distances. Someone reads §11 and believes backward fine moves are forbidden, or reads §12 and re-investigates byte 14.

**Fix:** Update §5 and §11 to the 1.84-unit law with a pointer to protocol.py. Remove the 'not established' backward fine step or record that it is now sent. Rewrite §12 and fix §1's port table and §8's end-of-roll sequence. Settle one capture count.

<details><summary>Second reader's check</summary>

protocol.md:290-294 and 812 give the 0.1662 mm intercept, but code uses MM_PER_COMMAND = 0.1057*1.84 (protocol.py:278-279; direct.py:3097). :355 says OVERHEAD_MM is 1.57 units, but it is 1.84. :790-791 says corrections use param 1 to about 8, but MAX_CORRECTION_PARAM = 87 (direct.py:3123). :841-845 says `01 01 00 04` is not sent, but nudge sends `slide(0x01, param>=1, value=0x04)` (direct.py:3166). §12 still lists byte 14 as unknown. §1 puts command bytes on wValue 0x0088, but _send_command writes the E0 prefix through ieee_command and the six CDB bytes to PORT_SCSI_CMD=0x0085 (usb_transport.py:44-46, 693-701). §8 (465-466) contradicts §5 (272-276) on SLIDE_PREV/eject appearing in captures.

</details>

<a id="docs-plans-todo-doc-20"></a>

### DOC-20 -- whole-roll-plan and other plans state superseded metering and exposure behaviour as current

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/whole-roll-plan.md:245`, `docs/whole-roll-plan.md:305-306`, `docs/whole-roll-plan.md:515-523`, `docs/whole-roll-plan.md:80-82`, `docs/shading-calibration-plan.md:54-56`, `docs/shading-calibration-plan.md:68-72`, `rps7200/direct.py:2064-2067`, `rps7200/direct.py:2080-2092`, `rps7200/direct.py:2613-2614`, `rps7200/direct.py:351-372`

**Doc claim:** docs/whole-roll-plan.md:245,305-306,515-523; docs/shading-calibration-plan.md:54-56,68-72

Several plan sections describe behaviour that was later reverted or replaced, without a superseded marker. The persistence of SET GAIN OFFSET is asserted both ways across docs and code comments. Blue RGBI headroom is described as a single constant (4.0, 5.2) though the code is per-film.

**Evidence (from the code):**

```text
whole-roll-plan.md:245: "divided by `infrared_blue_headroom` (4.0)", where code uses per-film 5.2/11.0 (`blue_rgbi_headroom(film)`). :305-306: "`scan(auto_exposure=True)` still probes in RGBI and has not been changed", whereas code probes `infrared=False` always. :518: "`scan()` now restores the base first", while scan() does `settings = self.get_gain_offset().scaled(exposure_scale)` with no restore (and :288-292 of the same doc says that change was reverted). :520: "`SET GAIN OFFSET` persists on the device", which contradicts shading-calibration-plan.md:54-56 "does not persist across a scan sequence" and CLAUDE.md, while direct.py:2065 says "SET GAIN OFFSET persists". shading-calibration-plan.md:68-69: "one constant works, and it is `BLUE_RGBI_HEADROOM`".
```

**Failure scenario:** Someone 'fixes' metering to restore the base in scan(), or to probe in RGBI, on the strength of these sections. That reintroduces the double-scaling or the ~220 s probe cost that later measurements removed.

**Fix:** Add superseded markers or corrections at the cited lines. Reconcile the SET GAIN OFFSET persistence statement in whole-roll-plan, shading-calibration-plan and the auto_exposure comment with protocol.md §6 (READ GAIN/OFFSET is a fixed reference).

<details><summary>Second reader's check</summary>

auto_exposure probes RGB only (infrared=False, direct.py:2083-2085) and uses per-film blue_rgbi_headroom (5.2 or 11.0). scan() applies get_gain_offset().scaled(exposure_scale) with no restore (direct.py:2613-2614). whole-roll-plan.md:245, 305-306 and 518 state otherwise, and :288-292 of the same doc says the restore was reverted. SET GAIN OFFSET persistence is asserted both ways: whole-roll-plan.md:520 and the direct.py:2065 comment say it persists, while shading-calibration-plan.md:54-56 and CLAUDE.md say it does not.

</details>

<a id="docs-plans-todo-doc-21"></a>

### DOC-21 -- 7200dpi-plan says scan() calibrates at the pass's own resolution; the code always calibrates at 3600

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/7200dpi-plan.md:102-108`, `rps7200/direct.py:2585-2600`

**Doc claim:** docs/7200dpi-plan.md:102-108

7200dpi-plan.md:102-108 describes calibrating at the pass's own resolution, but scan() always calibrates at the 3600 dpi default. scan()'s own ShadingUnavailable message ('calibrating at {resolution} dpi') misstates what it did in the same way.

**Evidence (from the code):**

```text
7200dpi-plan.md:104-106: "it calibrates at the pass's own resolution first. This is what makes a 7200 dpi request self-sufficient". Code: "At the default 3600 dpi, not this pass's resolution" ... `self.calibrate_shading()`.
```

**Failure scenario:** A reader of the bullet expects a 7200 dpi scan with shading to self-calibrate wider. It is refused with ShadingUnavailable before scanning.

**Fix:** Strike or annotate 7200dpi-plan.md:102-108.

<details><summary>Second reader's check</summary>

7200dpi-plan.md:102-108 says scan() calibrates at the pass's own resolution. scan() calls self.calibrate_shading() with defaults, commented 'At the default 3600 dpi, not this pass's resolution' (direct.py:2585-2592). The ShadingUnavailable message just after it still says `calibrating at {resolution} dpi did not produce...` (direct.py:2594-2595), so the operator-facing error repeats the doc's error.

</details>

<a id="docs-plans-todo-doc-22"></a>

### DOC-22 -- exposure-negative-plan's correction of the linearity figure was never applied to code or TODO

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:273-278`, `TODO.md:626-628`, `docs/exposure-negative-plan.md:305-334`

**Doc claim:** docs/exposure-negative-plan.md:330-334

The documented justification for EXPOSURE_TARGET in code and TODO still cites a figure the later measurement found about 2-3x too large, and attributes it to linearity. The measured justification (metering spread and headroom) appears only in the plan.

**Evidence (from the code):**

```text
direct.py:274-275: "departure from linear is under 0.1% below a third of scale and grows to 1.5-1.9% in the 75-95% band". TODO.md:627: "the sensor compresses 1.5-1.9% above 75% of scale". exposure-negative-plan.md:307-332: "The 1.5-1.9% figure does not reproduce ... about -0.6% to -0.8% ... The linearity claim in `rps7200/direct.py` and `TODO.md` should be corrected".
```

**Failure scenario:** A future target review reads the code comment and reasons from a wrong linearity cost.

**Fix:** Update the EXPOSURE_TARGET comment and TODO.md:623-637 to the -0.6/-0.8% figure and the headroom justification.

<details><summary>Second reader's check</summary>

direct.py:273-275 and TODO.md:626-628 still cite 1.5-1.9% compression above 75%. exposure-negative-plan.md:305-334 reports -0.6 to -0.8% and asks for both to be corrected. Neither was.

</details>

<a id="docs-plans-todo-doc-23"></a>

### DOC-23 -- TODO open-problem entries that are already fixed or obsolete, and stale line references

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `TODO.md:383-384`, `TODO.md:240-244`, `TODO.md:326-328`, `TODO.md:853-858`, `TODO.md:600-603`, `TODO.md:690`, `TODO.md:707`, `TODO.md:716`, `TODO.md:776`, `TODO.md:816-826`, `tests/test_gui.py:101-106`, `rps7200/library.py:255-259`, `rps7200/session.py:1412`, `rps7200/framing.py:710-723`, `docs/dpi-tradeoff-plan.md:198-201`

**Doc claim:** TODO.md:240-244,326-328,383-384,600-603,690,707,716,776,816-826,853-858

Several 'Known problems' or 'Improvements' entries describe defects that no longer exist. tests/test_gui.py:101-102 repeats the comparison-TIFF claim in a docstring. Line references are stale throughout the section. The noise makes it harder to see which entries are real open work.

**Evidence (from the code):**

```text
TODO.md:383: "`tools/scan.py` and `tools/scan_roll.py` both write the three comparison TIFFs to the repo root"; grep finds 1_nothing_done only in tools/make_comparison.py. TODO.md:242: "`library.save` then drops them [filter_offsets] on the floor", but library.save lists "filter_offsets" in the scan block. TODO.md:326: "Prescans do not carry the field yet", but _prescan files `last_scan_meta` from scan(), which includes filter_offsets. TODO.md:853: "`strip_offsets` can never propose a positive offset", but strip_offsets now uses picture_span, whose right-edge reading can. TODO.md:601-602: "RGBI: 3600 dpi, because there resolution is nearly free", while dpi-tradeoff-plan.md:199-201 retracts it. The line references session.py:759, direct.py:3162, scan_roll.py:176 and session.py:570 point at unrelated code now. The 0.136/0.219 deadband figures predate HOLD_TOLERANCE_MM=0.3002 and OVERHEAD 1.84.
```

**Failure scenario:** Effort is spent re-fixing closed issues, or a real open item is overlooked among stale ones.

**Fix:** Close or rewrite each cited entry against current code, and replace line numbers with symbol names.

<details><summary>Second reader's check</summary>

The comparison-TIFF names appear only in tools/make_comparison.py, so TODO.md:383-384 and the tests/test_gui.py:101-102 docstring are stale. library.save copies filter_offsets (library.py:260), which contradicts TODO.md:242. _prescan files the scanner's last_scan_meta, which includes filter_offsets (session.py:1412-1414), contradicting TODO.md:326. strip_offsets now takes starts from picture_span, whose right-edge reading can yield starts below `want`, so TODO.md:853-858's 'can never propose a positive offset' premise is gone. The cited line numbers are stale.

</details>

<a id="docs-plans-todo-doc-24"></a>

### DOC-24 -- frame-measurement-plan lists single-command adjustment (`command_for`) as done; it is dead code, and moves still go through the multi-command plan_nudges

**Severity** low · **Category** dead-code · **Verdict** confirmed

**Where:** `docs/frame-measurement-plan.md:187-194`, `rps7200/framing.py:2000-2046`, `rps7200/session.py:672`, `rps7200/framing.py:1062`, `rps7200/direct.py:3099-3102`

**Doc claim:** docs/frame-measurement-plan.md:187-194; TODO.md:860-862

The documented design (one SLIDE per correction, floating-point units) is not what moves the film. The mover remains param_for_mm + nudge with MAX_CORRECTION_PARAM 87, chained by plan_nudges for manual moves. The hold loop's measurement scale is still APERTURE_MM/width.

**Evidence (from the code):**

```text
Plan: "Done and measured: The units and the single-command adjustment are in `rps7200/framing.py`: ... `command_for`". grep shows command_for called only by describe_command, which nothing calls. The command_for docstring says "Splitting a correction is strictly worse and the old `plan_nudges` shape should not come back", yet plan_nudges is used by session._move, gui.py (5 sites) and scan_roll.py --nudge. measure_shift_mm still uses `mm_per_px = aperture_mm / width`, which the plan says was replaced. Separately, `CORRECTION_DEADBAND_MM = 0.15` is still defined and never read (TODO.md:860-862, accurate).
```

**Failure scenario:** A reader assumes corrections are single-command and units-based and analyses logs accordingly. Changes to command_for have no effect on hardware behaviour.

**Fix:** Either wire command_for into nudge and _hold_to_approved, or mark it as unused in the plan. Delete CORRECTION_DEADBAND_MM.

<details><summary>Second reader's check</summary>

command_for (framing.py:2000) is called only by describe_command (2034-2036), and nothing in rps7200/ or tools/ calls either. Moves go through plan_nudges (session.py:672; gui.py 5 sites; scan_roll.py:403) and param_for_mm/nudge. measure_shift_mm still uses `mm_per_px = aperture_mm / width` (framing.py:1061-1062). CORRECTION_DEADBAND_MM is defined once (direct.py:3102) and never read.

</details>

<a id="docs-plans-todo-doc-25"></a>

### DOC-25 -- research/frame-edge README and REPORT still say integration is not done and describe a different target module and frame width

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `research/frame-edge/README.md:8-9`, `research/frame-edge/REPORT.md:204-209`, `research/frame-edge/REPORT.md:224-231`, `research/frame-edge/REPORT.md:238-249`, `tools/frame_edges/__init__.py:1-13`, `rps7200/framing.py:1950-1957`

**Doc claim:** research/frame-edge/README.md:8-9; research/frame-edge/REPORT.md:208,227-228,238-249

Integration happened (2ce1167) as a copy in tools/frame_edges, handed to the driver as edge_reader, rather than moved into framing.py. propose_offsets is no longer called by the window or tools. The REPORT's open-decision and integration sections read as current, and its frame width (438-440) differs from the constant in code (435.6).

**Evidence (from the code):**

```text
README.md:8-9: "Integration into the software is a separate, later step." REPORT.md:238: "## Moving it into the software (not done yet)" ... "Move the four members and the vote into `rps7200/framing.py`" ... "Replace `picture_start` / `picture_end` in `propose_offsets`". REPORT.md:208: "the code assumes 425 (36 mm)". tools/frame_edges/__init__.py: "It lives in `tools/` by Stefan's choice; `rps7200` never imports it". framing.py: `FRAME_WIDTH_UNITS = 350.6` (435.6 columns), while the REPORT says the dev2 gaps 'settle' 438 columns.
```

**Failure scenario:** Someone follows the REPORT's steps and moves the detectors into rps7200/framing.py, creating a second copy that drifts from tools/frame_edges (the parity test covers only the tools copy).

**Fix:** Add a status note to README and REPORT pointing at tools/frame_edges and tests/test_frame_edges_parity.py, and record which width was adopted and why.

<details><summary>Second reader's check</summary>

README.md:8-9 and REPORT.md:238-249 ('not done yet', move into rps7200/framing.py, replace picture_start in propose_offsets) contradict tools/frame_edges/__init__.py:1-13 (a copy living in tools/, never imported by rps7200, handed in as walk_reader and propose_centred). The width figures (425 vs about 440 columns) differ from FRAME_WIDTH_UNITS = 350.6, which is 435.6 columns.

</details>

<a id="docs-plans-todo-doc-26"></a>

### DOC-26 -- `tools/scan.py` scans unmetered by default, though docs present auto-exposure as the default

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:64`, `tools/scan.py:225-234`, `rps7200/direct.py:2432`, `rps7200/direct.py:2512-2516`, `rps7200/session.py:864`, `docs/scanner-options-survey.md:32`

**Doc claim:** docs/scanner-options-survey.md:32

Only the window's Scan job (auto_exposure=True) and rolls (meter each) meter by default. The CLI tool that TODO's own examples use (`tools/scan.py --dpi 1800 --ir --frame 1`) scans at the device reference exposure unless --auto-exposure is given.

**Evidence (from the code):**

```text
scanner-options-survey.md:32: "`auto_exposure=True` by default". tools/scan.py: `ap.add_argument("--auto-exposure", action="store_true")` -> `auto_exposure=args.auto_exposure and not args.exposure_scale`. scan() default `auto_exposure: bool = False`, and its comment: "Scans otherwise run at the scanner's defaults, which land around 3-10% of full scale".
```

**Failure scenario:** An operator follows the documented CLI examples and gets heavily underexposed scans (most of the 16-bit range unused, with more quantisation noise in the shadows) that are filed as normal work.

**Fix:** Make metering the default in tools/scan.py (with --no-auto-exposure), or correct the survey table and document the CLI default prominently.

<details><summary>Second reader's check</summary>

tools/scan.py:64 makes --auto-exposure store_true (default off), and scan() defaults auto_exposure=False (direct.py:2432). Only the window's Scan job defaults to True (session.py:864). scanner-options-survey.md:32 says 'auto_exposure=True by default' without qualification.

</details>

<a id="docs-plans-todo-doc-28"></a>

### DOC-28 -- `library.save` accepts `inquiry` from every caller and never writes it

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:142`, `rps7200/library.py:213-307`, `rps7200/direct.py:728`, `rps7200/session.py:1098`, `tools/scan.py:192`

**Doc claim:** TODO.md:240-244 (same failure class, for filter_offsets and metering)

The device identity (vendor, product, firmware, ccd geometry, max resolution) that every caller hands over is dropped silently. This is the same 'dropped on the floor' pattern TODO documents for metering and filter_offsets.

**Evidence (from the code):**

```text
Signature `inquiry: Any = None,`. The record dict has no inquiry, device or firmware field. grep 'inquiry' in library.py matches only line 142. Callers pass `inquiry=self._inquiry`, `inquiry=job["inquiry"]` and `inquiry=info`.
```

**Failure scenario:** Entries from a second scanner or a firmware revision cannot be told apart from this one's, so cross-device comparisons of shading or noise are silently pooled.

**Fix:** Record `device: dataclasses.asdict(inquiry)` (or its describe()) in scan.json.

<details><summary>Second reader's check</summary>

grep shows `inquiry` only at library.py:142, the parameter. The record dict (213-307) has no device or firmware field. Callers pass inquiry through FrameWriter (session.py:1098), debug flush (direct.py:728) and scan.py (via `inquiry=info` in held).

</details>

<a id="docs-plans-todo-doc-29"></a>

### DOC-29 -- Open design gaps TODO records accurately: the sheet's dead 'nudge' tick, one-way approved.json, and permanently skipped real-TIFF tests

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `TODO.md:1141-1152`, `TODO.md:1088-1093`, `tools/gui.py:7124`, `tools/gui.py:5791-5831`, `rps7200/direct.py:3526-3575`, `tools/scan_roll.py:200-261`, `tests/test_tiff.py:436-446`

**Doc claim:** TODO.md:1141-1152,1088-1093

These are still true in code. The 'correct' option is offered on the sheet but can never act on a commissioned frame. Hand-set sheet positions cannot be run from the CLI. The only real-scanner-output TIFF interop tests always skip.

**Evidence (from the code):**

```text
OPTIONS = ("dpi", "predpi", "film", "meter", "ir", "fast_ir", "correct"). approved_from_sheet emits an Approved for every ticked frame. scan_roll: `if held is not None and holding: ... elif correct or correct_dry_run:`, which is never reached for ticked frames. scan_roll.py --approved re-proposes via `frame_edges.propose_centred` and never reads approved.json. test_tiff.py wants `scans/negatives/...`, which is gitignored and absent.
```

**Failure scenario:** An operator ticks 'nudge registration between frames' expecting in-walk aiming and gets none. Positions hand-set in the window are lost when the roll is run from the CLI.

**Fix:** Remove the dead option. Have scan_roll --approved read approved.json when present. Point the TIFF tests at library/*/scan.tif, or ship a small real fixture.

<details><summary>Second reader's check</summary>

The sheet OPTIONS include 'correct' (gui.py:7124). approved_from_sheet creates an Approved for every ticked frame (5791-5835), and the roll loop's `if held is not None and holding` branch comes before `elif correct or correct_dry_run` (direct.py:3529-3576), so correct never acts on commissioned frames. scan_roll's hold_from_walk re-proposes via frame_edges.propose_centred and does not read approved.json. test_tiff.py's TestStoredScans reads gitignored scans/ paths, which are absent in this checkout (no scans/ directory).

</details>

<a id="docs-plans-todo-doc-a3"></a>

### DOC-A3 -- plan_nudges docstring still describes the param 1..8 lattice and a param-8 clamp

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:672-693`, `rps7200/session.py:93-95`, `rps7200/direct.py:3123`

**Doc claim:** rps7200/session.py:676-691

The planner's docstring, shared by the mover, the sheet adjuster and the correction budget, states the pre-revision-5 cap. The code derives its step size from MAX_CORRECTION_PARAM = 87, so one command reaches about 88.8 units, not about 10.

**Evidence (from the code):**

```text
session.py:676-677: "One `SLIDE` command delivers ``STEP_MM x param + OVERHEAD_MM`` for an integer param in 1..8"; :690-691: "`DirectScanner.param_for_mm` already clamps silently at param 8". Code: FINE_MAX_MM = STEP_MM * DirectScanner.MAX_CORRECTION_PARAM + OVERHEAD_MM, with MAX_CORRECTION_PARAM = 87.
```

**Failure scenario:** A reader sizing a manual move or analysing a log assumes each chained command is at most param 8 and misreads how many SLIDE commands a large move produced.

**Fix:** Update the docstring to reference MAX_CORRECTION_PARAM rather than a literal, and express it in units per the CLAUDE.md rule.

<a id="docs-plans-todo-doc-30"></a>

### DOC-30 -- Red plane 'one line out' (TODO) is unaddressed in decode, and filter_offsets are recorded but never applied

**Severity** info · **Category** library-completeness · **Verdict** confirmed

**Where:** `TODO.md:228-244`, `rps7200/direct.py:1570-1600`, `rps7200/direct.py:2773-2777`

**Doc claim:** TODO.md:228-244

TODO reports R is consistently one line off G across 14 entries. The decode applies no inter-plane offset. Because raw bytes are stored, this is correctable offline later. Recorded as an open observation, not a regression.

**Evidence (from the code):**

```text
decode_index aligns planes purely by index: `height = min(len(planes[c]) for c in order)`, `np.stack([np.array(planes[c][:height][::step]) ...])`. The filter offsets are only stored: `"filter_offsets": [int(params.filter_offset1), int(params.filter_offset2)]`.
```

**Failure scenario:** Slight colour fringing on horizontal edges persists in every delivered file until the decode is changed and `reconstruct` or migrate is run.

**Fix:** Once measured, apply the offset in decode_index (recorded in meta and raw_layout) and re-derive the library.

<details><summary>Second reader's check</summary>

decode_index aligns planes purely by index (`planes[c][:height]`, direct.py:1596-1599). filter_offsets are only recorded (direct.py:2773-2777; library.py:260), never applied. Because raw bytes are kept, this can be corrected later offline. It is an observation, not a regression.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry raw bytes | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/raw.bin.gz | gzip (level 6) of the concatenated INDEX-format lines exactly as read over bulk-in (2-byte tag + samples, LE) | raw, byte-exact | rps7200/library.py:save (from DirectScanner.last_raw via capture_record, or a spooled raw_path in debug filing) | library.read_raw/decode_raw/reconstruct/migrate_direction; tools/uniformity.py; tools/dpi_analysis.py | yes for passes that were filed. Not written for metering probes, hold/aim verification prescans, CLI dry-run prescans, or a roll frame's prescan.tif (DOC-04). Dropped if the raw_layout shape disagrees with the image (session._file guard). |
| Library entry pixels | library/<id>/scan.tif | TIFF uint16 (or uint8 for prescans), (H,W,3\|4), deflate+predictor when tifffile is installed | raw decode turned upright from its line tags. For 7200 dpi, also column-stagger realigned (4 rows trimmed) without any record (DOC-07). Legacy entries may hold corrected pixels (corrections_applied ['shading']). A session._file fallback can file corrected pixels with no corrections label when raw_image shape mismatches. | library.save; tools/library.py migrate-raw/migrate-direction (rewrite) | library.load/corrected/reconstruct; GUI full-res view and Save As via corrected() | lossless relative to what was passed in. Not always equal to decode(raw.bin.gz): row order is turned for reversed passes, and 7200 dpi is realigned. |
| Entry record | library/<id>/scan.json | JSON: id, created, image{shape,dtype,channels,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{resolution_dpi,frame,width,height,depth,channels,channel_order,bytes_per_line,film,exposure_scale,exposure_metered,duration_s,protocol_revision,rotation,flipped,reversal,read_direction,carriage_state,fast_infrared,filter_offsets}, device_settings{exposure,gain,offset}, metering, registration, calibration{shading,ccd_mask,pixels_per_line,light_mean,report,skipped}, prescan{file,read_direction,carriage_state}, film, tags, provenance | metadata | library.save; migrate-raw/migrate-direction | library.entries/verify/signature/duplicates/corrected/reconstruct; tools/registration_margin.py; GUI | no. Missing: byte14 as sent, slide_init_param, skip_shading and quality word, light/extra_entries/double_times, INQUIRY (passed but dropped), shading source (measured vs --reuse loaded, and when), realignment applied. The json.dumps default=str stringifies any non-JSON values in registration or metering. |
| Shading reference per entry | library/<id>/shading.npz | np.savez_compressed: pixels_per_line, channels, dark_channels, ref{c} float64, mean{c}, dark{c}, darkmean{c} | derived (processed) from the discarded calibration bytes | library.save via ShadingReference.save | library.load/corrected/reconstruct(legacy); tools/uniformity.py | no. The calibration pass bytes, descriptor and calibration_info are never kept (DOC-03). There is no timestamp or provenance. |
| CCD mask per pass | library/<id>/ccd_mask.bin | bytes, 0x00 used / 0x70 unused, length = reference pixels_per_line | raw | library.save (from DirectScanner._ccd_mask, read per pass in scan()) | library.load/corrected; apply_shading | yes |
| Framing pass stored beside a roll frame | library/<id>/prescan.tif | TIFF uint8 (H,W,3) | CORRECTED (rf.prescan), with no raw bytes and no corrections label | library.save(prescan=...) from session._file and scan_roll.py FrameWriter jobs | migrate_direction; research/frame-edge dataset builder; anchors | no (DOC-04) |
| Library index | library/index.json | JSON summary list | derived | library.reindex (on every save; quadratic, accepted) | humans/tools | n/a, rebuildable |
| Cached session shading reference | calibration/shading.npz (default --reference / Calibrate.reference) | same npz as above | derived | DirectScanner.ensure_shading -> save_shading | ensure_shading(reuse=True)/load_shading; GUI age check by mtime | no timestamp or session identity inside the file. Loaded silently by --reuse (DOC-11). |
| Roll manifests | rolls/<name>/roll.json and rolls/<name>/survey.json | JSON: roll, numbering, dpi, infrared, meter, film, settings{...}, wanted, frames[{number,index,transport_position,registration,error,done,exposure,gain,offset,prescan}] | metadata | ScanSession._roll (merges an earlier roll.json); tools/scan_roll.py main (overwrites; no merge, DOC-10) | gui.read_survey/manifest_settings; session.walked_prescans; scan_roll.hold_from_walk | rewritten after every frame. The CLI resume path loses earlier frames. There is no 'device' block, contrary to the session.py comment (DOC-17). |
| Walk and roll pictures | rolls/<name>/prescanNN.tif, prescanNN-before.tif, frameNN.tif | TIFF | corrected, oriented as on screen (rotation/flip, reversal applied) | FrameWriter._write (window); scan_roll.py dry-run tiff.write and FrameWriter | gui.read_survey (un-rotated), scan_roll --approved, research/frame-edge | not raw. For CLI dry runs this is the only copy of the walk (DOC-04). |
| Operator-approved positions | rolls/<name>/approved.json | JSON offsets, rotations, flips, sources, entries | metadata | tools/gui.py on_scan_chosen | tools/gui.py read_approved only (scan_roll --approved ignores it) | yes, one-way (DOC-29) |
| Comparison TIFFs for review by eye | <repo root>/1_nothing_done.tif, 2_corrected.tif, 3_corrected_inverted.tif; previews/cmp_before.png, cmp_after.png | TIFF uint16 / PNG | 2_corrected is DESTRIPED, not shading-corrected (DOC-01) | tools/make_comparison.py only | Stefan by eye | n/a. Does not represent the shipped correction. |
| CLI single-scan output | <--out, default ./scan.tif> and its .json sidecar | TIFF/JPEG + JSON meta | corrected (bracket: merged; mono: one channel) | tools/scan.py | user/NegPy | overwritten silently on each run with the default name. A merged bracket is not filed. |
| Debug spool | $TMP/rps7200-debug-*/NNN-image.npy, NNN-raw.bin | npy + raw bytes | raw | DirectScanner._debug_capture (only when RPS7200_DEBUG=1 or debug=True) | _debug_flush -> library.save (root RPS7200_DEBUG_ROOT or library/) | yes. Lost if the process dies before close(). |

**Second reader's corrections to this table:**

1. Debug spool: the claim "exact: yes. Lost if the process dies before close()" is incomplete. _debug_flush (direct.py:713-765) also deletes the spooled npy and raw.bin in a `finally` block when library.save fails, then rmtree's the spool (DOC-A1). A failed filing destroys the only copy.

2. Entry record (scan.json): add bracket_index, bracket_ratio, bracket_passes and bracket_stops to the fields in meta that library.save drops (DOC-A2).

3. Entry record, write order: scan.json is written last, after scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz. None of these writes is atomic.
   - An interrupted save leaves a directory with no scan.json. entries(), verify() and reindex() glob only */scan.json, so that directory is invisible to them and is never reported.
   - A later save with the same id sees no scan.json and writes into the same partial directory.

4. Library entry pixels: the "session._file fallback" that files corrected pixels without a label also exists in FrameWriter._write (session.py:1087-1099). When raw_image is None, it files job["image"] (corrected) with no `corrections=`. It is reached from session._file's shape-mismatch branch and from any submitter that omits raw_image.

5. Cached session reference (calibration/shading.npz): when a calibration returns no usable reference, ensure_shading's save_shading returns None without touching the file (direct.py:563-570, 613-615). An older cached reference therefore survives a failed recalibration, and a later --reuse loads it silently.

6. Roll manifests: also recorded is that the window's ScanSession._roll merges an earlier roll.json only for non-dry runs. survey.json is always rewritten from scratch, on both the window and CLI paths.

7. Walk pictures: on the window's dry run, prescanNN-before.tif is written with file_entry=False, so it is a file with no library entry (session.py:1828-1854). On a real roll with holding, prescan_before is never produced (direct.py:3548-3553), so the pre-hold picture exists nowhere.

## What the operator can do

- Scan once from the CLI: `tools/scan.py --dpi N [--ir] [--auto-exposure] [--reuse|--no-shading] [--bracket N --stops X] [--exposure-scale X|R,G,B] [--mono] [--out f.tif|.jpg]`. It files in library/ by default.
- Walk or scan a roll from the CLI: `tools/scan_roll.py [--dry-run] [--frames N] [--start-at N] [--rewind N] [--nudge mm] [--approved <walk-folder>] [--correct|--correct-dry-run] [--meter each|once|none] [--reuse|--no-shading]`.
- In the window: Calibrate (measure/reuse/off), Prescan, Scan, Roll or walk, then open the contact sheet, set offsets, rotations and ticks, and 'Scan chosen frames'. 'Rolls ...' reopens and resumes a roll. Save As delivers library.corrected() pixels.
- Maintain the library: `tools/library.py list|verify|reconstruct|reindex|duplicates [--delete --keep N]|migrate-raw [--write]|migrate-direction [--write]`.
- Turn on debug filing for ad-hoc scripts with RPS7200_DEBUG=1 or DirectScanner(debug=True). Redirect it with RPS7200_DEBUG_ROOT.
- Regenerate the comparison TIFFs with `tools/make_comparison.py scan.tif flat.tif` (destripe-only today; see DOC-01).

## What the operator should not do

- Do not run `tools/library.py duplicates --delete` on this library. Byte-14 and gain ladders share one signature and would be deleted (DOC-02).
- Do not use `--reuse` with a cache from another power-on. The entry will not record that the reference was stale (DOC-11).
- Do not resume a CLI roll with `tools/scan_roll.py --start-at N --roll <same>`. It overwrites roll.json. Resume from the window, or use a new --roll name (DOC-10).
- Do not use `--bracket` together with `--ir` and trust the merged file's blue channel (DOC-05).
- Do not pass a single-value `--exposure-scale` with `--bracket`. It is ignored (DOC-27).
- Do not judge a shading change by 2_corrected.tif from make_comparison.py. Use library.corrected() output (DOC-01).
- Do not rely on a CLI `--dry-run` walk as library evidence. It files nothing re-decodable (DOC-04).
- Do not run `tools/scan.py` without `--auto-exposure` (or an explicit scale) for real work. It scans at about 3-10% of full scale (DOC-26).
- Do not press Roll or Scan in the window while a Calibrate job is still queued or running. The window already believes it is calibrated (DOC-16).
- Do not scan at 7200 dpi expecting `make reconstruct` to stay green (DOC-07).
- Do not follow TODO.md:886-910 (lower FRAME_WIDTH_MM to 35.3) or protocol.md §5's 0.1662 intercept. Both are superseded (DOC-14, DOC-19).

## Mistakes nothing guards against

- `duplicates --delete` shows no warning that the entries it removes differ in device gain or byte14. Only commanded exposure and fast_infrared are protected.
- `--start-at` on an existing roll name silently truncates that roll's manifest to the new run's frames.
- `--bracket 3 --exposure-scale 2.0` silently scans at x1.0 base times the ladder.
- `--bracket --ir` silently mis-scales blue in the delivered merge.
- `--reuse` loads any shading.npz at the path, of any age and from any device, with only a 'reusing ...' line on stdout.
- Calibrate(mode='off') followed quickly by Roll in the window lets the roll reach the lazy in-scan calibration that TODO records as stalling the device.
- The default `--out scan.tif` in the current directory is overwritten on every tools/scan.py run (the library copy survives).
- A 7200 dpi window narrowed to at most 5172 columns passes the shading guard and is corrected through an unverified mask mapping, with no warning (scanner-options-survey.md:147-155).
- Choosing 'nudge registration between frames' on the sheet is accepted and never acts on ticked frames.
- A `--no-shading` CLI scan makes `make verify` report 'correction was asked for', which misleads the operator into thinking a correction failed.

## Dataflow notes

How bytes enter:
- The Transport (usb_transport.py) sends each 6-byte CDB. The command bytes go one per control transfer to port 0x85 (usb_transport.py:693-701), after the IEEE1284 daisy sequence and 0xE0 on 0x88. Payload data comes back over bulk-in 0x81.
- Setup: DirectScanner.scan (direct.py:2423) sends READ_STATE polls, then wait_warm/TUR and exposure/highlight writes. It checks shading width against MAX_SHADING_COLUMNS (refuses 7200 dpi) and lazily calls calibrate_shading() if no reference is held (direct.py:2579-2592). Then it sends set_scan_frame, cmd_17, SET GAIN OFFSET (get_gain_offset().scaled(exposure_scale)), and set_mode (byte14_for(passes), fast IR gated on infrared). After SLIDE INIT it records carriage_record (the last READ STATE), then sends SCAN, get_ccd_mask (per pass, stored in _ccd_mask) and get_parameters.
- Reading: read_planes (direct.py:1440) paces reads against NoDataYet. With keep_raw it keeps the joined blob in last_raw and last_raw_layout.
- Decode: decode_index (direct.py:1551) splits lines by tag. read_direction (direction.py:85) decides the direction from the first R/B tags, and a reversed pass is turned upright.

Transform inside scan():
- If resolution == 7200, _realign_native_column_stagger trims 4 rows (direct.py:2695).
- The result is raw_pixels, which go to last_pixels_raw and are what the library stores.
- apply_shading(image, _shading, ccd_mask) (shading.py:196) builds width_to_loc from the mask, applies the two-point or one-point correction, clamps, and counts clipping. Columns beyond loc.size are left raw.
- The corrected image is returned to the caller with meta (direct.py:2760-2804; metering attached only for self-metered scans). _debug_capture spools raw_pixels, meta and the capture_record only when debug is on.

Calibration:
- calibrate_shading (direct.py:1755) reads 4-line blocks of 16-bit tagged lines over CALIBRATION_FRAME. calculate_shading (shading.py:109) splits dark from light by the level gap and returns a ShadingReference.
- The raw block is discarded (keep_data False).
- ensure_shading (direct.py:572) either loads calibration/shading.npz (--reuse) or measures and saves one.

Metering: auto_exposure (direct.py:1977) runs up to 3 RGB probes through scan(keep_raw=True). Their bytes are overwritten and never filed unless debug is on. It leaves last_metering in the meta.

Rolls:
- scan_roll (direct.py:3292) yields RollFrame(image=corrected, raw_image=last_pixels_raw, prescan=corrected, raw_prescan, prescan_meta).
- Its hold loop _hold_to_approved (direct.py:2836) takes extra prescans, keeping only the last.

Filing:
- ScanSession._file (session.py:2031) runs a shape guard on the raw capture, composes the reversal/rotation into the meta, and submits a FrameWriter job. The writer thread (session.py:1060) writes delivered files (oriented, mono-reduced, corrected) and calls library.save(raw_image or image, meta, prescan=corrected prescan, **capture) after the device closes or while it is busy.
- tools/scan.py holds raw captures and calls library.save after close. tools/scan_roll.py uses FrameWriter for frames only; its dry run writes corrected prescanNN.tif only.

Leaving the library:
- library.corrected (library.py:338) is load, then today's apply_shading, unless the entry is legacy-corrected or skipped. No 7200 dpi realignment is applied.
- reconstruct (library.py:439) re-decodes raw.bin.gz with decode_index and compares it to scan.tif (and read_direction). It cannot reproduce the 7200 dpi realignment.
- verify (library.py:776) checks sha256s and presence, and mislabels explicit-raw entries.
- signature/duplicates/prunable (library.py:639-738) group entries by an incomplete command tuple.
- tools/make_comparison.py sits outside this flow: TIFF plus flat, then destripe, then 1_/2_/3_*.tif. It never touches shading.npz.
