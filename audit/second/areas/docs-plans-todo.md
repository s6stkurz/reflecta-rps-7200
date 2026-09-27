# docs/*.md and TODO.md vs the code

Area key `docs-plans-todo`. 25 findings: 6 medium, 18 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Documentation vs code for docs/*.md (19 plans), TODO.md, and research/frame-edge/{README,REPORT}.md where they describe tools/frame_edges. Every doc was read in full and its present-tense claims were checked against the executable path in rps7200/, tools/ and tests/.


Most "Fixed" claims in TODO.md hold in code:
- corrected() and verify() split SHADING_SKIPPED_EXPLICIT.
- fast_infrared is gated on infrared, and scan_bracket takes fast_infrared.
- The uncalibrated/uncorrectable refusals happen before anything is sent.
- The dry-run filing and direction decode are in place.
- filter_offsets is kept on scans and prescans.
- The untied-IR idle is 287 s.
- The command log is kept per pass.

Most "still open" items are also genuinely still open:
- The trailing-columns shading gap.
- A one-member lone_gap vote moves film.
- The approved path has no roll-wide travel cap.
- SEARCH_MM is smaller than a param-87 move.
- Millimetres are still stored and printed.
- Two misleading GUI dialogs, and the inert "nudge registration" tick.
- The --reuse cache carries no timestamp.
- CORRECTION_DEADBAND_MM is unused.
- The stale comments TODO lists, plus several it does not.

Findings the docs do not record:
- **IR shading contradiction.** Three plans say the reference carries IR and IR is corrected. README and CLAUDE.md say IR is delivered uncorrected. The code corrects IR whenever the reference has channel 3.
- **tools/uniformity.py capture is broken.** It meters before any calibration, so the first probe raises ShadingUnavailable.
- **The same tool does not archive its calibration.** It calibrates through bare calibrate_shading(), so the calibration's raw bytes are never kept, contradicting TODO's "every calibration keeps its bytes". The documented protocol also calibrates an empty transport.
- **Hold/aim moves leave no record of what was sent.** The SLIDE bytes they send are logged nowhere: only mm derived through a law that changed without a revision bump.
- **advance/retreat misname their argument.** `steps` is sent as the SLIDE value byte, which was measured to move one frame; the demo moves N frames.
- **Numbers disagree across docs.** Capture counts, command counts, and whether SLIDE_PREV and eject appear in any capture.
- Several stale file and function references.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [DOC-01](#docs-plans-todo-doc-01) | medium | doc-mismatch | confirmed | Docs contradict each other on whether the infrared plane is shading-corrected; code corrects IR whenever the reference carries channel 3 |
| [DOC-02](#docs-plans-todo-doc-02) | medium | bug | confirmed | tools/uniformity.py capture meters before any calibration, so the documented study cannot run without --exposure-scale |
| [DOC-03](#docs-plans-todo-doc-03) | medium | hardware-safety | confirmed | tools/uniformity.py calibrates an empty transport, bypasses the calibration-bytes archive, and writes its cache non-atomically |
| [DOC-04](#docs-plans-todo-doc-04) | medium | bug | confirmed | apply_shading still leaves trailing columns uncorrected when the mask's used count < width; guard never built; docs disagree whether it occurs |
| [DOC-05](#docs-plans-todo-doc-05) | medium | design | partly | The software's vote lets one member's lone_gap move film; research REPORT describes a vote where anything without two agreeing members is refused |
| [DOC-06](#docs-plans-todo-doc-06) | medium | data-integrity | confirmed | The SLIDE bytes of hold/aim moves are recorded nowhere; entries keep only mm derived through a law that changed without a revision bump |
| [DOC-07](#docs-plans-todo-doc-07) | low | doc-mismatch | confirmed | command_for is described as 'built and tested' but no test references it; protocol.py claims one home for the command cost while framing keeps its own literal |
| [DOC-08](#docs-plans-todo-doc-08) | low | design | confirmed | SEARCH_MM cannot see the largest allowed correction; its comment cites a MAX_TRAVEL_MM that no longer exists |
| [DOC-09](#docs-plans-todo-doc-09) | low | doc-mismatch | confirmed | Present-tense '~212 s infrared floor, whatever the resolution' persists in docs and operator-facing refusals, though tied IR is the default |
| [DOC-10](#docs-plans-todo-doc-10) | low | doc-mismatch | confirmed | Every stale code comment TODO lists is still present, and several more contradict the docs |
| [DOC-11](#docs-plans-todo-doc-11) | low | demo-divergence | confirmed | advance()/retreat() send `steps` as the SLIDE value byte, which docs measured as one frame regardless; the demo moves N frames |
| [DOC-12](#docs-plans-todo-doc-12) | low | doc-mismatch | confirmed | Docs disagree whether CyberView ever sends SLIDE_PREV or an eject; retreat()'s docstring relies on the refuted version |
| [DOC-13](#docs-plans-todo-doc-13) | low | doc-mismatch | confirmed | Capture and command counts are inconsistent across docs (six, seven or nine captures; 3,955, 3,987, 6,158 or 8,133 commands; 110 or 37 SLIDEs) |
| [DOC-14](#docs-plans-todo-doc-14) | low | doc-mismatch | confirmed | vignette-audit.md reports a fixed filing bug in the present tense; its `trailing` bug is still open and untested |
| [DOC-15](#docs-plans-todo-doc-15) | low | design | partly | Hold budget still scales with the target, and nothing caps a roll's total travel on the approved path |
| [DOC-16](#docs-plans-todo-doc-16) | low | user-error | confirmed | tools/scan.py calibrates straight after INQUIRY without asking; --reuse loads a cache with no timestamp or identity |
| [DOC-17](#docs-plans-todo-doc-17) | low | doc-mismatch | confirmed | scanner-options-survey says the driver hands raw pixels, reference and mask to the consumer; delivery is corrected pixels only |
| [DOC-18](#docs-plans-todo-doc-18) | low | data-integrity | confirmed | The library docstring promises self-contained entries, but the calibration bytes behind shading.npz live outside and loaded references are not linked to them |
| [DOC-19](#docs-plans-todo-doc-19) | low | design | confirmed | A scalar exposure_scale also scales the infrared exposure, which the docs say is a device constant the driver never moves |
| [DOC-20](#docs-plans-todo-doc-20) | low | user-error | confirmed | Open window/roll problems listed in TODO are all still present (misleading dialogs, inert nudge tick, one-way approved.json, millimetres stored and printed) |
| [DOC-21](#docs-plans-todo-doc-21) | low | doc-mismatch | confirmed | Stale file/function references and self-contradictions inside plan docs and TODO |
| [DOC-22](#docs-plans-todo-doc-22) | low | design | confirmed | The bracket and measurement-tool decisions TODO lists are all still open in code |
| [DOC-A1](#docs-plans-todo-doc-a1) | low | data-integrity | found-by-verifier | A roll frame entry's prescan.tif holds the corrected prescan, and its record does not say so |
| [DOC-A2](#docs-plans-todo-doc-a2) | low | doc-mismatch | found-by-verifier | session_start's docstring gives SLIDE 00 01 00 04, but the code sends 00 01 00 00, a sub-frame action with an unmeasured value byte |
| [DOC-23](#docs-plans-todo-doc-23) | info | doc-mismatch | confirmed | docs/stefan-judgement.json is cited as the acceptance scorer but no code reads it |

## Findings in full

<a id="docs-plans-todo-doc-01"></a>

### DOC-01 -- Docs contradict each other on whether the infrared plane is shading-corrected; code corrects IR whenever the reference carries channel 3

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/shading.py:36`, `rps7200/shading.py:256-281`, `docs/shading-calibration-plan.md:16`, `docs/shading-calibration-plan.md:141-142`, `docs/shading-calibration-plan.md:325-326`, `docs/vignette-plan.md:387-394`, `docs/vignette-recalculation-plan.md:143-144`, `README.md:482-485`, `tools/uniformity.py:746-748`

**Doc claim:** README.md:482-485 and CLAUDE.md ('The infrared plane is delivered uncorrected everywhere') vs docs/shading-calibration-plan.md:141-142, docs/vignette-plan.md:387-394, docs/vignette-recalculation-plan.md:143

The repo's normative docs (README, CLAUDE.md) state the IR plane is never corrected. The plans that looked at stored references say the device's calibration returns an I channel and IR is corrected. The code does whichever the data says: it corrects IR in scan(), library.corrected() and every export whenever the reference has channel 3. One of the two sets of docs is describing the delivered pixels wrongly.

**Evidence (from the code):**

```text
shading.py:36 `TAG_TO_CHANNEL = {0x52: 0, 0x47: 1, 0x42: 2, 0x49: 3}`. shading.py:256-258 `for c in range(nc): if c not in reference.ref: report["uncorrected"] += 1; continue`, so channel 3 is divided by the reference whenever calculate_shading found 'I'-tagged lines. shading-calibration-plan.md:141-142: "channels interleaved `B R G I`. IR is included in the reference." Its :16 status table: "IR (RGBI pass) 4.67% -> 0.89%". vignette-plan.md:387-389: "The shading reference does cover IR. Verified: both `calibration/shading.npz` and `shading_matched.npz` carry `channels [0 1 2 3]` ... so the IR plane is two-point corrected like the rest." vignette-recalculation-plan.md:143: "infrared spans 57.5% against green's 33.7%" (read off 11 sessions' references). README.md:482-484: "The infrared plane is not corrected. The calibration pass is RGB, so the reference has no infrared channel ... counts it as `uncorrected`." CLAUDE.md says the same. tools/uniformity.py:746 refuses when `3 not in reference.ref`, which treats an IR reference as the normal case.
```

**Failure scenario:** An operator or consumer (NegPy dust thresholding, a later analysis) is told the delivered IR plane still carries its ~57% column profile. Per the plans' own measurements it has actually been divided by the reference. Anyone implementing 'correct IR later once an IR reference exists', as README invites, would correct it twice. An analysis of IR noise or speck depth would also be run on the wrong assumption.

**Fix:** Read `channels` from one stored shading.npz and settle the fact. Fix README/CLAUDE.md or the plans accordingly. Make the entry record state per channel explicitly (e.g. `corrected_channels`), rather than relying on the `uncorrected` count.

<details><summary>Second reader's check</summary>

shading.py:36 maps tag 0x49 to channel 3, and apply_shading (shading.py:256-281) corrects every channel present in reference.ref, so IR is divided whenever calculate_shading saw I-tagged lines. README.md:482-485 and CLAUDE.md:125 say the infrared plane is always uncorrected because the reference has no IR channel. docs/shading-calibration-plan.md:142 ('IR is included in the reference'), :16 and vignette-plan.md:387-389 ('carry channels [0 1 2 3]') say the opposite. calibrate_shading's own docstring (direct.py:2168, 'three channels') sides with README. tools/uniformity.py:746 refuses when 3 is absent, so it treats an IR reference as the norm. No library is on disk to settle which is true. Code behaviour depends on the data, and the two sets of docs contradict each other.

</details>

<a id="docs-plans-todo-doc-02"></a>

### DOC-02 -- tools/uniformity.py capture meters before any calibration, so the documented study cannot run without --exposure-scale

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:699-711`, `tools/uniformity.py:731-735`, `rps7200/direct.py:2397`, `rps7200/direct.py:2491-2498`, `rps7200/direct.py:2951-2963`, `docs/vignette-plan.md:55-56`, `docs/vignette-recalculation-plan.md:391-394`

**Doc claim:** docs/vignette-plan.md:55-56 ('tools/uniformity.py capture --ir ... runs it'); docs/vignette-recalculation-plan.md:393-394 ('is already built')

Since in-scan calibration was removed (audit P15), a corrected pass with no reference is refused. The capture flow's metering step was never updated: it runs in a session that has not calibrated and asks for shading. Every invocation without --exposure-scale dies at the first probe. It fails safe: only READ/WRITE GAIN OFFSET is sent before the refusal.

**Evidence (from the code):**

```text
uniformity.py:703-708 opens a fresh scanner with no reference loaded: `scanner = factory(); ... scanner.open(); exposure_scale = scanner.auto_exposure(target=args.target, infrared=args.ir, film="positive")`. auto_exposure's default is `shading: bool = True` (direct.py:2397), and its probe calls `self.scan(..., shading=shading)` (2491-2498). scan() then does `if self._shading is None or needed > ...: ... raise self.uncalibrated(reason)` (2951-2963). The calibration comes only afterwards (uniformity.py:731-735). No test exercises `capture`. vignette-plan.md:55: "`tools/uniformity.py capture --ir --tag vignette-study-ir` runs it". vignette-recalculation-plan.md:393: "is already built".
```

**Failure scenario:** Stefan empties the transport as the tool instructs and presses Enter. auto_exposure raises ShadingUnavailable('no shading reference in this session. Calibrate first ...'). The phase-1 or phase-2 study documented as re-runnable cannot be run as written.

**Fix:** Calibrate (through ensure_shading) or load the reference before metering, or pass shading=False to the metering probes explicitly. Add a test that drives `capture` against a fake scanner.

<details><summary>Second reader's check</summary>

cmd_capture (tools/uniformity.py:699-711) builds a fresh DirectScanner, calls open() (no reference is loaded there) and then auto_exposure with no shading argument, so shading=True (direct.py:2397). The first probe calls scan(shading=True) (direct.py:2491-2498), which raises uncalibrated('no shading reference in this session') at direct.py:2951-2963 before any pass is sent. Calibration comes only at uniformity.py:731-735. The one test that touches uniformity (tests/test_scan_tool.py:492) inspects one_pass's source, not capture. So the documented study cannot run without --exposure-scale. It fails safe.

</details>

<a id="docs-plans-todo-doc-03"></a>

### DOC-03 -- tools/uniformity.py calibrates an empty transport, bypasses the calibration-bytes archive, and writes its cache non-atomically

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/uniformity.py:58-59`, `tools/uniformity.py:700-702`, `tools/uniformity.py:735`, `tools/uniformity.py:743`, `tools/uniformity.py:589`, `rps7200/direct.py:848-858`, `rps7200/direct.py:744-746`, `docs/vignette-plan.md:248-261`, `docs/multi-exposure-plan.md:395-396`, `TODO.md:1479-1485`, `TODO.md:564-574`

**Doc claim:** TODO.md:1479-1485 (every calibration keeps its bytes); CLAUDE.md 'Calibrate with the film loaded' vs docs/vignette-plan.md:248-261 and docs/multi-exposure-plan.md:395

CLAUDE.md records that calibrating an empty transport preceded a wedge. The uniformity tool and two plans still drive exactly that state. The tool's calibration also escapes the archive every other calibration path now keeps, so the raw bytes behind every vignette-study reference are discarded. And a kill during `reference.save` leaves a truncated cache that later `--reuse` loads.

**Evidence (from the code):**

```text
uniformity.py:59: "Take everything out of the transport. Leave it empty." :700: "Metering on the EMPTY transport". Then :735 `result = scanner.calibrate_shading()`: keep_data defaults to False, so result['data'] is None, and archive_calibration is only reached from ensure_shading (direct.py:848-858). :743 `reference.save(reference_path)`, with no temp file and replace; save_shading uses `temp = path.with_name(...); os.replace(temp, path)` (direct.py:744-746). Passes then `scanner.load_shading(reference_path)` (:589), so each entry's shading_origin says 'loaded' from a cache whose calibration bytes were never kept. TODO.md:1479-1482: "Every calibration now keeps its own bytes in `calibration/<UTC time>/`". vignette-plan.md:248-259 instructs "Setup, with the transport still empty ... Calibrate shading once ... this also happens before anything is loaded". multi-exposure-plan.md:395: "With **no film loaded**" for a tools/scan.py run, which calibrates at open.
```

**Failure scenario:** The operator follows the tool's prompts. The empty-transport calibration may wedge the scanner (power cycle, session lost). If it succeeds, the 1.66 MB of calibration lines are thrown away, so shading.npz for every study entry can never be recomputed from its source bytes. A Ctrl-C during the cache write corrupts calibration/shading_uniformity.npz.

**Fix:** Route through ensure_shading (archive plus atomic cache), and ask for film before calibrating. Fix vignette-plan.md's setup order and multi-exposure-plan.md's 'no film loaded' instruction. TODO lists this under Decisions (P17); the archive bypass is not listed.

<details><summary>Second reader's check</summary>

uniformity.py:700-708 prompts 'press Enter with the transport empty' and meters. Right after, with no further prompt, :735 calls scanner.calibrate_shading(), whose keep_data defaults to False (direct.py:2132, 2372 'data': data if keep_data else None). archive_calibration is reached only from ensure_shading (direct.py:848-858). :743 reference.save(reference_path) is a plain np.savez_compressed to the final path (shading.py:91), unlike save_shading's temp file and os.replace (direct.py:744-746). Passes then load_shading that cache (:589), so entries record 'loaded' with no archive. vignette-plan.md:257-259 still tells the reader to calibrate 'before anything is loaded', and multi-exposure-plan.md:395 says 'with no film loaded'. TODO.md:564-574 lists only the empty-transport part. The archive bypass and the non-atomic write are unlisted.

</details>

<a id="docs-plans-todo-doc-04"></a>

### DOC-04 -- apply_shading still leaves trailing columns uncorrected when the mask's used count < width; guard never built; docs disagree whether it occurs

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/shading.py:193`, `rps7200/shading.py:251`, `rps7200/shading.py:281`, `rps7200/direct.py:3115`, `rps7200/library.py:598`, `TODO.md:471-480`, `docs/shading-calibration-plan.md:216-224`, `docs/vignette-plan.md:299-305`, `docs/vignette-recalculation-plan.md:284-287`

**Doc claim:** TODO.md:471-480 vs docs/vignette-recalculation-plan.md:284-287; docs/shading-calibration-plan.md:220-224 ('the comparison with the mask's used count was never made')

The defect TODO lists as open is present exactly as described. The two docs disagree on whether any stored pass has ever hit it, so its real incidence is unknown. The report records `columns` < `width`, but nothing refuses or warns, and delivered files carry the raw edge columns without comment.

**Evidence (from the code):**

```text
shading.py:193 `return locs[:width]`. :281 `out[:, : loc.size, c] = vals.astype(image.dtype)`. The report records `"columns": int(loc.size), "width": w` (:251). The only guard is direct.py:3115 `if shading and self._shading is not None and params.width > self._shading.pixels_per_line:`, a comparison against the reference width, not the mask. library.corrected() calls the same apply_shading (library.py:598). TODO.md:475-480: "the device has returned 862 columns as well as 860 ... such a pass goes out with its last two columns uncorrected ... The guard ... was never built." vignette-recalculation-plan.md:284-287: "`trailing` is 0 for all nine study entries and for all 198 library entries carrying a shading report, at all 14 resolutions. The docstring ... is false against every entry in the library."
```

**Failure scenario:** A 600 dpi pass comes back 862 wide against a mask with 860 used pixels. scan(), library.corrected(), Save As and a roll's frameNN.tif all deliver columns 860-861 raw, with the ~39% lamp falloff at the frame edge, silently. The raw data is kept, so it is re-derivable.

**Fix:** Warn or refuse in scan() when mask.used < width. Correct the edge columns from the nearest reference column, or trim them explicitly and record it. Reconcile TODO.md with vignette-recalculation-plan.md by querying `calibration.report` across the library.

<details><summary>Second reader's check</summary>

build_width_to_loc returns locs[:width] (shading.py:193), and apply_shading writes only out[:, :loc.size, c] (shading.py:281), so columns past the mask's used count keep their raw values. The only guard (direct.py:3115) compares params.width with the reference's pixels_per_line, not with the mask. library.corrected uses the same function. TODO.md:471-480 says the guard was never built, which is still true. vignette-recalculation-plan.md:284-287 says trailing was 0 in all 198 entries, which contradicts TODO's 'not only latent'. Incidence is unknown. The raw data is kept, so this is re-derivable.

</details>

<a id="docs-plans-todo-doc-05"></a>

### DOC-05 -- The software's vote lets one member's lone_gap move film; research REPORT describes a vote where anything without two agreeing members is refused

**Severity** medium · **Category** design · **Verdict** partly

**Where:** `tools/frame_edges/vote.py:92-111`, `tools/frame_edges/propose.py:170-178`, `rps7200/framing.py:1729-1733`, `rps7200/direct.py:3457-3517`, `tools/scan_roll.py:293-297`, `TODO.md:249-256`, `TODO.md:523-532`

**Doc claim:** research/frame-edge/REPORT.md:153-160 ('Anything else is refused'); TODO.md:249-256

On the operator's paths (window, scan_roll --correct, scan_roll --approved), one member's lone_gap reading, labelled source='unconfirmed', moves film with no source gate: _aim_frame never reads detail['source'], and hold_from_walk holds unconfirmed and neighbours proposals. research/frame-edge/REPORT.md does describe the lone-gap rule (ensemble_v2, :112-116), so this is not a doc contradiction. It is an undecided design question, still live in code and listed in TODO.md.

**Evidence (from the code):**

```text
vote.py:100 `return Side(EDGE, x=s.x, lo=s.lo, hi=s.hi, conf=0.3, ... note=f"gap with neighbour, one vote: {name}")`. `vote_v2` returns `lone_gap(sides) or v` when the round-1 vote is not an edge. propose.centring sets `source="unconfirmed" if lone else "measured"` but still returns `columns_to_mm(columns, width)`. WalkReader.judge returns `mm, note`. direct.py:3457 `decision, detail = walk.judge(index, image)`: the film is moved if `abs(decision) >= HOLD_TOLERANCE_MM` and `walk.affordable(decision)`, and `detail['source']` is never read. scan_roll.py:294-297 holds every frame `if n in offsets`, including unconfirmed and neighbours. REPORT.md:153-160: "An edge stands when at least two members agree within one column ... Anything else is refused. Nothing in the vote was fitted."
```

**Failure scenario:** On a frame where one member mistakes a gap-shaped silhouette for base beside the neighbour (REPORT itself records chroma putting an edge 32 columns off on gold200_14), the roll moves the film up to param 87 on that one reading. It then holds the wrong position, and later frames inherit the displacement.

**Fix:** Update REPORT.md/README.md to describe vote_v2 and lone_gap. Decide per TODO's proposal: gate `_aim_frame` and `hold_from_walk` on `source == 'measured'`, or have WalkReader.judge return None for unconfirmed readings.

<details><summary>Second reader's check</summary>

The code behaviour is real. vote_v2 falls back to lone_gap (vote.py:106-111) at conf=0.3. centring labels it 'unconfirmed' but still returns mm (propose.py:175-178). StripWalk.judge passes the reader's decision straight through (framing.py:1729-1733). _aim_frame moves on any decision within tolerance and budget without reading detail['source'] (direct.py:3457-3517), and scan_roll.py:293-297 holds every proposed offset whatever its source. The doc-mismatch half is refuted: REPORT.md:112-116 explicitly describes ensemble_v2's one-member gap-with-neighbour rule. The 'Anything else is refused' list at :153-157 describes the base vote in algos/ensemble.py, and the round-2 section already qualifies it. What remains is the open design question TODO records (P31) at TODO.md:249-256 and 523-532.

</details>

<a id="docs-plans-todo-doc-06"></a>

### DOC-06 -- The SLIDE bytes of hold/aim moves are recorded nowhere; entries keep only mm derived through a law that changed without a revision bump

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:455-458`, `rps7200/direct.py:474-476`, `rps7200/direct.py:3345-3350`, `rps7200/direct.py:3368-3375`, `rps7200/direct.py:4029`, `rps7200/direct.py:4136`, `rps7200/protocol.py:16-39`, `TODO.md:554-560`, `docs/step-calibration-plan.md:219-223`

**Doc claim:** TODO.md:554-560; docs/step-calibration-plan.md:219-223 ('Correcting them changes what param_for_mm returns, so PROTOCOL_REVISION moves. (It did not ...)')

The owner requires every command and parameter needed to re-evaluate a scan to be kept. For sub-frame holds and aims, the only record is `spent_mm`/`asked_mm`: millimetres computed from the param through the law in force that day. The law changed without a revision bump, so an entry's protocol_revision cannot tell which law, or which param bytes, applied. Whole-frame advances and rewinds between passes are also unrecorded.

**Evidence (from the code):**

```text
_CommandLog records only while `record` is a list: `def start(self): object.__setattr__(self, "record", [])`. `command()` begins `if self.record is None: return self._inner.command(...)`. start() is called only inside scan() (direct.py:2990) and calibrate_shading (2182). _hold_to_approved calls `asked = self.nudge(want)` (3368) between passes, so outside any record. `nudge` returns `{"param": param, ...}`, but only `asked["asked_mm"]` and `clamped` are kept. The out dict (3345-3350) has target_mm, spent_mm, history (measurements), clamped, and no param or action. `marks["approved"] = {k: v for k, v in fix.items() ...}` becomes `meta["registration"]`. PROTOCOL_REVISION history (protocol.py:16-39) has no entry for COMMAND_UNITS 1.572 -> 1.84.
```

**Failure scenario:** Months later someone re-fits the transport law from stored rolls, or audits why frame 9 overshot. spent_mm values from before and after 2026-09-22 were computed with different ramps (1.572 vs 1.84 units). The params actually sent (e.g. 87 vs 86) cannot be recovered, and the SLIDE commands are in no entry's extra.commands.

**Fix:** Store each nudge's {action, param, value, asked_mm, COMMAND_UNITS} in the hold history, or run the command log around hold/aim loops. Decide TODO's 'does a law change move PROTOCOL_REVISION' question and write the rule beside the revision history.

<details><summary>Second reader's check</summary>

_CommandLog.command passes through unrecorded whenever record is None (direct.py:474-476), and start() is called only inside scan() and calibrate_shading. _hold_to_approved calls self.nudge(want) between passes (direct.py:3368) and keeps only asked['asked_mm'] and clamped. nudge returns param (direct.py:3640), but out/history hold measurements only, with no param or action. No COMMAND_UNITS or law is written into meta (grep finds none in library, session or direct). The PROTOCOL_REVISION history (protocol.py:16-39) has no entry for d723896, which moved COMMAND_UNITS to 1.84. TODO.md:554-560 and step-calibration-plan.md:219-223 confirm this is open.

</details>

<a id="docs-plans-todo-doc-07"></a>

### DOC-07 -- command_for is described as 'built and tested' but no test references it; protocol.py claims one home for the command cost while framing keeps its own literal

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/frame-measurement-plan.md:187-197`, `rps7200/framing.py:1939`, `rps7200/framing.py:2000-2045`, `rps7200/protocol.py:253-255`, `rps7200/protocol.py:273-276`

**Doc claim:** docs/frame-measurement-plan.md:187-197; rps7200/protocol.py:273-275

command_for and describe_command are dead and untested, although the doc's 'Done and measured' heading presents them as the adjustment implementation. The command cost lives in two places, against the repo's own one-home rule. The comments describing that are internally inconsistent.

**Evidence (from the code):**

```text
frame-measurement-plan.md:196-197: "`command_for` is built and tested, and only `describe_command` calls it, which nothing calls." A grep of tests/ for command_for or describe_command finds nothing. framing.py:1939 `COMMAND_COST = 1.84` and protocol.py:276 `COMMAND_UNITS = 1.84` are two independent literals. protocol.py:273-275 says "1.84 is kept because it is the value `framing` already carried, so the two modules now describe one command with one number instead of two". protocol.py:253-255 still says framing's value is "from a later session using a different correlation estimator. **Display code must use the numbers here**".
```

**Failure scenario:** A future law change edits COMMAND_UNITS but not COMMAND_COST, or the reverse. Then SMALLEST_MOVE, LARGEST_VERIFIABLE_MOVE and tools/frame_edges (which imports SMALLEST_MOVE) disagree with what param_for_mm and nudge actually send.

**Fix:** Make framing.COMMAND_COST an alias of protocol.COMMAND_UNITS. Either delete command_for and describe_command or test and use them. Correct the doc's 'tested' claim.

<details><summary>Second reader's check</summary>

No file in tests/ references command_for or describe_command. The only callers are framing.py:2036 (describe_command calling command_for) and nothing calls describe_command. So frame-measurement-plan.md:196 ('built and tested') is false. framing.py:1939 COMMAND_COST = 1.84 and protocol.py:276 COMMAND_UNITS = 1.84 are separate literals. protocol.py:253-255 still calls framing's figure the one 'from a later session using a different correlation estimator', which contradicts :273-275's 'one number instead of two'.

</details>

<a id="docs-plans-todo-doc-08"></a>

### DOC-08 -- SEARCH_MM cannot see the largest allowed correction; its comment cites a MAX_TRAVEL_MM that no longer exists

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/framing.py:854-857`, `rps7200/framing.py:1060-1062`, `rps7200/direct.py:3596`, `docs/frame-measurement-plan.md:62`, `TODO.md:543-548`

**Doc claim:** rps7200/framing.py:854 comment; TODO.md:543-548

The comment's safety claim is false since the cap went to 87. An approved or proposed offset beyond about 9.0 mm is moved by the hold loop, and the verification prescan can then never find the true peak inside the searched window.

**Evidence (from the code):**

```text
framing.py:854-857: "Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window ... SEARCH_MM = 9.0". MAX_TRAVEL_MM is defined nowhere. measure_shift_mm computes `reach = int(SEARCH_MM / max(mm_per_px, 1e-9))`, which is 105 px at 428 columns. direct.py:3596 `MAX_CORRECTION_PARAM = 87` gives 88.84 units x 0.1057 = 9.39 mm, about 110 px. frame-measurement-plan.md:62: "`SEARCH_MM` 9.0 is about **param 84**".
```

**Failure scenario:** A sheet position near 88 units is held. After one param-87 command the verifying correlation's true peak is outside ±105 px. The result is 'unverified' or a sub-floor refusal: the film moved and the residual is unknown. Holding may then give up after HOLD_GIVE_UP_FRAMES.

**Fix:** Derive the reach from (MAX_CORRECTION_PARAM + COMMAND_UNITS) plus a margin, re-fit CONFIDENCE_FLOOR at the new reach (tools/registration_margin.py), and fix the comment.

<details><summary>Second reader's check</summary>

framing.py:854-857 justifies SEARCH_MM = 9.0 as 'Just over MAX_TRAVEL_MM'. The only MAX_TRAVEL_MM left is tools/gui.py:241 (= MAX_FINE_MM), and rps7200 does not import it. With a 428-column prescan, reach = int(9.0 / (36.49/428)) is about 105 px. MAX_CORRECTION_PARAM 87 gives (87+1.84)*0.1057 = 9.39 mm, about 110 px. So a hold near the cap cannot be verified. TODO.md:543-548 lists this as open.

</details>

<a id="docs-plans-todo-doc-09"></a>

### DOC-09 -- Present-tense '~212 s infrared floor, whatever the resolution' persists in docs and operator-facing refusals, though tied IR is the default

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/protocol.md:547-550`, `docs/analog-gain-plan.md:138-140`, `docs/vignette-plan.md:48-50`, `docs/vignette-plan.md:395-398`, `docs/multi-exposure-plan.md:323-325`, `rps7200/direct.py:2926`, `rps7200/direct.py:3869`, `rps7200/demo.py:672`, `tools/scan.py:163`, `tools/scan_roll.py:317`, `rps7200/export.py:21-22`, `rps7200/direct.py:2760`

**Doc claim:** docs/protocol.md:547-550; docs/vignette-plan.md:48-50, 395-398; docs/analog-gain-plan.md:138-140; docs/multi-exposure-plan.md:323-325

TODO.md:510-511 already lists the 'fourteen refusals and docstrings' as stale. The same stale statement is also present tense in five plan docs, and protocol.md still gives it as the reason metering is RGB-only.

**Evidence (from the code):**

```text
protocol.md:549: "A four-channel pass carries a ~212 s floor whatever the resolution, so probing in it would cost a full scan's time per round. This is why metering here stays in RGB." vignette-plan.md:49-50: "infrared holds the device busy for a ~212 s floor per pass whatever the resolution, so five passes is ~18 minutes that 600 dpi cannot shorten". direct.py:2926 refusal text: "absorbs infrared, so the pass would spend its ~212 s floor". Meanwhile scan() defaults to `fast_infrared: bool = True`, and session.estimate_seconds returns `7.5 + 0.0599 * lines` for tied IR (about 25 s at 300 dpi).
```

**Failure scenario:** An operator told that an IR pass on B&W 'would spend its ~212 s floor' over-estimates the cost roughly 9x at 300 dpi. A planner reading vignette-plan budgets ~18 minutes for a phase that costs about 4.

**Fix:** Say 'untied' where the floor is meant, and quote the tied figure beside it. Fix the refusal messages in one place.

<details><summary>Second reader's check</summary>

The strings are present as quoted: direct.py:2926 and 3869, demo.py:672, scan.py:163, scan_roll.py:317 and export.py:21-22. direct.py:2760 gives the ~212 s floor as the reason infrared is not bracketed. protocol.md:547-550, vignette-plan.md:395-398, analog-gain-plan.md:138-140 and multi-exposure-plan.md:323-325 all state it in the present tense. scan() defaults to fast_infrared=True (direct.py:2858). TODO.md:505-511 already lists the code strings. The doc occurrences are additional.

</details>

<a id="docs-plans-todo-doc-10"></a>

### DOC-10 -- Every stale code comment TODO lists is still present, and several more contradict the docs

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/protocol.py:239-241`, `rps7200/protocol.py:290`, `rps7200/session.py:1207-1221`, `rps7200/direct.py:275-279`, `rps7200/direct.py:2473-2475`, `rps7200/shortcuts.py:148-149`, `rps7200/session.py:2229`, `tools/gui.py:5929`, `tools/scan_roll.py:519`, `rps7200/direct.py:2137-2142`, `rps7200/direct.py:2099-2124`, `rps7200/session.py:1402`, `rps7200/direct.py:3010-3013`, `TODO.md:500-512`

**Doc claim:** TODO.md:500-512 ('Comments and messages in the code that the docs now contradict')

The comment debt TODO lists is untouched, and the same class of drift (the 1.57 ramp, the param-8 cap, the ~1 mm max move, byte 6 vs byte 8) appears in six further places TODO does not list. These comments guide the next edit: the plan_nudges docstring still describes a ceiling 10x lower than the real one.

**Evidence (from the code):**

```text
protocol.py:290 "`param 1` travels 2.57" (returns 2.84). protocol.py:240-241 cites the law "`distance = 0.1057 mm x param + 0.1662 mm`". plan_nudges docstring (session.py:1208, 1219-1220): "integer param in 1..8", "`DirectScanner.param_for_mm` already clamps silently at param 8". direct.py:278-279 "grows to 1.5-1.9% in the 75-95% band". direct.py:2474-2475 "SET GAIN OFFSET persists". shortcuts.py:148 "Move the film one step left". Not in TODO: session.py:2229 "One SLIDE command tops out at ~1.01 mm". gui.py:5929 "the lattice is 2.57 units off zero". scan_roll.py:519 "one command reaches only 1.0118 mm". calibrate_shading docstring (2137, 2142): "Whether it actually improves striping is UNVERIFIED" and "The width comes from the frame instead", although the code reads the descriptor and shading-calibration-plan.md:3 says DONE and verified. session_start docstring: "a SLIDE with `00 01 00 04`" and 0xE7 "may be what puts the scanner into a state where calibration is accepted", while code sends `self.slide(0x00, param=0x01)` with value 0, i.e. `00 01 00 00`, and protocol.md §11 closes 0xE7 as a lead. session.py:1402 mono_channel "Green by measurement" while `MONO_CHANNEL = MONO_AVERAGE` (mono.py:70). require_media's log cites byte 6: `f"note: state {state.scanning:#04x} suggests no film, but that bit is not reliable"`, while media_loaded now reads byte 8.
```

**Failure scenario:** A maintainer trusts plan_nudges' docstring and adds splitting logic for moves above about 1 mm. Or a log reader sees 'state 0x1d suggests no film' and concludes the new byte-8 flag fired on byte 6.

**Fix:** Fix these in one code-comment pass and extend TODO's list with the unlisted ones.

<details><summary>Second reader's check</summary>

Each cited comment was checked. protocol.py:290 says 'param 1 travels 2.57' but the code returns 2.84. protocol.py:240-241 still gives the 0.1662 mm law. session.py:1208 and 1220 still describe 'param in 1..8' and 'clamps silently at param 8'. direct.py:2474 says 'SET GAIN OFFSET persists'. shortcuts.py:148 says 'Move the film one step'. session.py:2229 says '~1.01 mm'. gui.py:5929 says '2.57 units off zero'. scan_roll.py:519 says '1.0118 mm'. The calibrate_shading docstring says 'UNVERIFIED' and 'width comes from the frame', while the code reads the descriptor. session_start's docstring gives '00 01 00 04', but slide(0x00, param=0x01) uses the default value=0 (direct.py:1501), i.e. 00 01 00 00. session_start is called only by probe tools. session.py:1402 says 'Green by measurement', but mono.py:70 sets MONO_CHANNEL = MONO_AVERAGE. require_media logs state.scanning (byte 6), while media_loaded reads byte 8 (protocol.py:568).

</details>

<a id="docs-plans-todo-doc-11"></a>

### DOC-11 -- advance()/retreat() send `steps` as the SLIDE value byte, which docs measured as one frame regardless; the demo moves N frames

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/direct.py:1543`, `rps7200/direct.py:1557-1590`, `rps7200/demo.py:385-415`, `rps7200/session.py:120-123`, `docs/whole-roll-plan.md:331-333`, `docs/whole-roll-plan.md:419-422`

**Doc claim:** docs/whole-roll-plan.md:331-333, 419-422

The parameter name and the session comment promise N frames. The device, per the measurement, moves one frame, and a value byte above 2 has never been sent. The demo implements N frames, so it would report a multi-frame rewind succeeding where the hardware moves one frame (or does something unmeasured). No caller passes steps>1 today, so this is latent.

**Evidence (from the code):**

```text
direct.py:1543 `self.slide(action, param=0x01, value=steps)`, so `advance(steps=2)` sends `04 01 00 02`. whole-roll-plan.md:331-332: "`SLIDE 04 01 00 <value>` takes a 1 or a 2 in the last byte. Both step the position counter by exactly one frame". :421: "`SLIDE_NEXT` value 1 vs value 2 differ by **less than 0.05 mm**". demo.py:391 `self._position += steps` and :413 `self._position = max(0, self._position - steps)`. session.py:120-122: "`retreat(steps=N)` exists, but the wait underneath only watches for the position to *change* ... it cannot tell sixteen frames from one", which assumes N frames.
```

**Failure scenario:** A future rewind written as `retreat(steps=frames)` passes in the demo and in tests against DemoScanner. On the device it sends `05 01 00 10`, an invented payload, moves at most one frame, and returns as soon as the counter changes. The roll then scans mis-numbered frames.

**Fix:** Remove `steps` from DirectScanner.advance/retreat (always value=1), or rename it `value` and make the demo match DirectScanner by taking the decision from it. Fix the session.py comment.

<details><summary>Second reader's check</summary>

_whole_frames sends slide(action, param=0x01, value=steps) (direct.py:1543). DemoScanner.advance and retreat do position +/- steps (demo.py:391, 413). whole-roll-plan.md:331-332 and 421 measure the value byte as not changing the distance. No caller in rps7200 or tools passes steps>1 (transport_truth uses the defaults, and roll_registration_walk.py:269 explicitly avoids it). So the divergence is latent.

</details>

<a id="docs-plans-todo-doc-12"></a>

### DOC-12 -- Docs disagree whether CyberView ever sends SLIDE_PREV or an eject; retreat()'s docstring relies on the refuted version

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/whole-roll-plan.md:363-372`, `docs/whole-roll-plan.md:219`, `docs/protocol.md:279-285`, `docs/protocol.md:486-491`, `rps7200/direct.py:1579-1583`

**Doc claim:** docs/whole-roll-plan.md:363-372 vs docs/protocol.md:279-285, 486-491

The evidence base for SLIDE_PREV (which every rewind and resume depends on) is stated both ways. The code docstring asserts vendor precedent that protocol.md says does not exist.

**Evidence (from the code):**

```text
whole-roll-plan.md:363-372: "### Rewind and eject, both first sightings ... `05 01 00 01  x16   SLIDE_PREV` ... `03 f6 dd 00  x1` ... the eject". protocol.md:282-284: "It listed 16 x `05 01 00 01` (SLIDE_PREV) and one `03 f6 dd 00` (eject) — **neither appears in any capture**, so *our evidence that `SLIDE_PREV` works is our own measurement*". direct.py retreat docstring: "``05 01 00 01``, the mirror of :meth:`advance`. This is what the vendor sends to rewind a finished roll". whole-roll-plan.md:383-384 flags only the `47`/`57` disagreement, not this one.
```

**Failure scenario:** A reviewer deciding whether a multi-frame rewind or eject is 'vendor-proven' reads whole-roll-plan or the docstring and treats an unobserved payload as captured. That is exactly the invented-payload risk the SET_SCAN_HEAD rule guards against.

**Fix:** Re-parse full_17_strip with tools/parse_capture.py, record the result once, and update whole-roll-plan.md and the retreat() docstring.

<details><summary>Second reader's check</summary>

whole-roll-plan.md:363-372 presents 16x 05 01 00 01 and 03 f6 dd 00 as vendor sightings. protocol.md:282-284 and 486-491 say neither appears in any capture. The retreat() docstring (direct.py:1579-1581) says 'This is what the vendor sends to rewind a finished roll'. whole-roll-plan.md:383-384 annotates only the 47/57 disagreement.

</details>

<a id="docs-plans-todo-doc-13"></a>

### DOC-13 -- Capture and command counts are inconsistent across docs (six, seven or nine captures; 3,955, 3,987, 6,158 or 8,133 commands; 110 or 37 SLIDEs)

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/protocol.md:3-5`, `docs/protocol.md:49`, `docs/protocol.md:261-262`, `docs/protocol.md:504-512`, `docs/scanner-options-survey.md:227-228`, `docs/scanner-options-survey.md:276-277`, `docs/dpi-tradeoff-plan.md:233-234`, `docs/whole-roll-plan.md:473-474`, `rps7200/direct.py:2908`

**Doc claim:** docs/protocol.md:3-5, 49, 261-262; docs/scanner-options-survey.md:227; docs/dpi-tradeoff-plan.md:233

The vendor captures are the ground truth for 'the vendor never sends X' claims (SET_SCAN_HEAD, fast IR, SLIDE_PREV). The docs disagree on how many captures and commands those claims were checked against. protocol.md's own §2 table contradicts its §5 recount.

**Evidence (from the code):**

```text
protocol.md:3-4: "seven sessions ... 8,133 SCSI commands in total". Its §9 table lists seven captures whose cmds sum to 6,158, while §4 separately uses bw.pcapng (585) and slide.pcapng. protocol.md:49 gives `0xD1 | SLIDE | 110`, but §5:262 says "**37 SLIDE commands**". scanner-options-survey.md:227-228: "3,955 commands across all six captures". :276-277 and dpi-tradeoff-plan.md:233-234: "none of the nine CyberView captures". whole-roll-plan.md:474: "3,955 commands across seven captures". CLAUDE.md: "3,987 commands parsed".
```

**Failure scenario:** A 'zero in N commands' safety claim, such as SET_SCAN_HEAD never sent, cannot be re-verified against a stated corpus. A reader cannot tell whether bw.pcapng and slide.pcapng were included.

**Fix:** State the capture inventory once (file names, command counts) in protocol.md §9, and have the other docs cite it rather than retype the figures.

<details><summary>Second reader's check</summary>

The protocol.md:3-4 header says 'seven sessions ... 8,133 SCSI commands'. The §9 table's cmds (43+312+877+331+1828+596+2171) sum to 6,158. The §2 table has SLIDE at 110, while §5:261-262 says 37 recounted 'from all six captures'. scanner-options-survey.md:227 says 3,955 across six, whole-roll-plan.md:474 says 3,955 across seven, and dpi-tradeoff-plan.md:233 and scanner-options-survey.md:276 say nine captures.

</details>

<a id="docs-plans-todo-doc-14"></a>

### DOC-14 -- vignette-audit.md reports a fixed filing bug in the present tense; its `trailing` bug is still open and untested

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/vignette-audit.md:200-225`, `docs/vignette-audit.md:227-270`, `docs/vignette-audit.md:325-333`, `tools/scan.py:285-300`, `tools/uniformity.py:600-611`, `tools/uniformity.py:186-189`, `rps7200/uniformity.py:447-460`

**Doc claim:** docs/vignette-audit.md §5 (:200), §7 (:227), §10 (:325-333)

Half of §7/§10 has been fixed and the doc does not say so. The other half (§5) is a real, open analysis bug with no test for the mirrored case, recorded only in docs rather than TODO.md.

**Evidence (from the code):**

```text
vignette-audit §7 (:227+): "two tools file corrected pixels as raw ... `tools/scan.py:225` ... `tools/uniformity.py:604`". Code now: scan.py hold() files `image=image if raw is None else raw` with `raw = getattr(s, "last_pixels_raw", None)`, and uniformity.py:611 passes `image if raw_pixels is None else raw_pixels`. §5: uniformity.py:186-189 `turned = un.apply_orientation(a, orientation) ... samples = un.block_ratios(ca, cb, dpi=dpi, trailing=trailing)`: `trailing` is still applied after orientation, so for MIRROR_X/ROT180 the unshaded columns are on the left while block_ratios drops columns from the right. §10: "The `trailing` bug (§5) and the mislabelling call sites (§7) are ordinary fixes needing no decision."
```

**Failure scenario:** A future vignette re-analysis with a non-zero `trailing` (a pass with mask < width) trims good columns from the right and leaves the raw edge columns on the left of the mirrored differences. That can inject a ~4% artefact that reads as an odd-in-x field.

**Fix:** Mark §7 fixed. Move `trailing` before apply_orientation, or trim in the original coordinates. Add the mirrored-case test and list it in TODO.md.

<details><summary>Second reader's check</summary>

vignette-audit.md §7 (:227+) still describes tools/scan.py and tools/uniformity.py filing corrected pixels. Both now file last_pixels_raw (scan.py:285-294 and uniformity.py:602-611, `image if raw_pixels is None else raw_pixels`), and the audit carries no 'fixed' note. uniformity.difference (tools/uniformity.py:186-189) still applies apply_orientation before block_ratios(trailing=...), so the §5 bug is present. tests/test_uniformity.py:321-333 tests trailing only for the un-mirrored case. TODO.md does not list it, although vignette-recalculation-plan.md:280-287 does.

</details>

<a id="docs-plans-todo-doc-15"></a>

### DOC-15 -- Hold budget still scales with the target, and nothing caps a roll's total travel on the approved path

**Severity** low · **Category** design · **Verdict** partly

**Where:** `rps7200/framing.py:1210`, `rps7200/framing.py:1169`, `rps7200/framing.py:1764-1775`, `rps7200/direct.py:3896-3897`, `rps7200/direct.py:4005-4033`, `docs/registration-accuracy-plan.md:44-49`, `docs/registration-accuracy-plan.md:113-116`, `TODO.md:1024-1032`

**Doc claim:** docs/registration-accuracy-plan.md:44-49, 113-116; TODO.md:1024-1032

hold_plan's per-frame budget scales with the target, so a wrong large target buys itself more travel (the fix registration-accuracy-plan recommends is not applied). The roll-wide ROLL_TRAVEL_LIMIT_MM applies only on the automatic --correct path, not to approved/held rolls. Placement drift is still bounded per frame, because each hold is verified against that frame's walk reference.

**Evidence (from the code):**

```text
framing.py:1210 `budget = abs(target_mm) + HOLD_HEADROOM_MM`. ROLL_TRAVEL_LIMIT_MM is read only inside StripWalk (1764, 1775), and StripWalk exists only `if (correct or correct_dry_run)` (direct.py:3896-3897). The approved/held branch (4005-4033) runs _hold_to_approved with no roll-wide sum. registration-accuracy-plan.md:113-116: "The budget should not scale with the target ... confirmed by frames 9 and 10 and costs nothing to correct."
```

**Failure scenario:** A sheet with a wrong large target (a false band 23 units out) authorises itself more travel than a correct small target. Over a 36-frame commissioned roll, per-frame holds of about 1.5 mm each walk the strip 50+ mm with nothing noticing, because the frame counter does not see nudges.

**Fix:** Make the budget independent of the target (e.g. a fixed MAX plus residual), and apply ROLL_TRAVEL_LIMIT_MM on the held path as well.

<details><summary>Second reader's check</summary>

Both code facts are real. hold_plan's budget is abs(target_mm) + HOLD_HEADROOM_MM (framing.py:1210). ROLL_TRAVEL_LIMIT_MM is enforced only through StripWalk, which exists only with correct/correct_dry_run (direct.py:3896-3897), so the held branch (direct.py:4005-4033) has no roll-wide sum. registration-accuracy-plan.md:113-116's recommended fix is not applied. The failure scenario is overstated, though. Each held frame is measured against its own walk reference prescan (measure_shift_mm against approved.reference), so cumulative creep is measured and corrected per frame, not 'unnoticed'. The unbounded quantity is total mechanical travel, not misplacement. TODO.md:1024-1032 records this and notes the observed peak was 0.86 mm.

</details>

<a id="docs-plans-todo-doc-16"></a>

### DOC-16 -- tools/scan.py calibrates straight after INQUIRY without asking; --reuse loads a cache with no timestamp or identity

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:267-274`, `rps7200/direct.py:824-835`, `rps7200/shading.py:79-91`, `rps7200/direct.py:713-729`, `TODO.md:445-469`, `TODO.md:564-574`

**Doc claim:** TODO.md:445-469, 564-574

Both are listed as open in TODO and remain so in code. The only evidence of a stale reference is the file mtime recorded in extra.shading_origin, and a copy or restore of calibration/ resets that mtime.

**Evidence (from the code):**

```text
scan.py:267-274: `info = s.inquiry() ... if not args.no_shading and not (args.reuse and ref_path.exists()): print("calibrating ...") ; print(s.ensure_shading(ref_path, reuse=args.reuse, skip=args.no_shading)["summary"])`, with no confirmation and no media-state record. ShadingReference.save writes only `pixels_per_line`, `channels`, `ref{c}`, `mean{c}` and the dark arrays: no timestamp, power-on or device id. ensure_shading's summary for a load is only `f"reusing {path} ({reference.pixels_per_line} columns, channels ...)"`.
```

**Failure scenario:** The operator runs `tools/scan.py` with the strip out, and the tool immediately calibrates an empty transport (the precursor of a wedge). Or they run `--reuse` the day after a power cycle: the scan is corrected with the previous power-on's reference and nothing flags it.

**Fix:** Per TODO's proposal: prompt before a measuring ensure_shading (with a --film-loaded flag), and write measured_utc and inquiry identity into the .npz. Warn when reusing across a power cycle.

<details><summary>Second reader's check</summary>

tools/scan.py:266-274 goes from inquiry() straight to ensure_shading with no input() prompt. ShadingReference.save (shading.py:75-91) writes arrays only, with no timestamp or identity. For a loaded reference, load_shading's origin records only path, file mtime and load time (direct.py:724-729). Both issues are open in TODO.md:445-469 and 564-574.

</details>

<a id="docs-plans-todo-doc-17"></a>

### DOC-17 -- scanner-options-survey says the driver hands raw pixels, reference and mask to the consumer; delivery is corrected pixels only

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/scanner-options-survey.md:289-293`, `rps7200/export.py:1-7`, `rps7200/library.py:222-229`

**Doc claim:** docs/scanner-options-survey.md:289-293

The consumer (NegPy) receives corrected pixels. The reference and mask stay in the library and are never handed over. The survey describes a design the code deliberately does not follow.

**Evidence (from the code):**

```text
scanner-options-survey.md:290-292: "this driver hands over linear raw pixels, the per-session shading reference, the per-pass CCD mask and the infrared plane untouched, and a consumer that has all four can do better". export.py:3-7: "what leaves through here is a finished file for somebody to use". library.save docstring: "everything the operator sees, exports or saves is corrected while what is kept here is not".
```

**Failure scenario:** A consumer author reads the survey, expects raw pixels plus reference, and applies flat-fielding a second time.

**Fix:** Reword to 'delivers shading-corrected linear pixels; the raw pixels, reference and mask stay in the library'.

<details><summary>Second reader's check</summary>

scanner-options-survey.md:290-292 says the driver 'hands over linear raw pixels, the per-session shading reference, the per-pass CCD mask and the infrared plane untouched'. export.py:3-7 and library.save's docstring (library.py:246-249) say what is delivered is corrected. The reference and mask stay in the library. Per DOC-01, the IR plane may itself be corrected.

</details>

<a id="docs-plans-todo-doc-18"></a>

### DOC-18 -- The library docstring promises self-contained entries, but the calibration bytes behind shading.npz live outside and loaded references are not linked to them

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:28-30`, `rps7200/direct.py:724-729`, `rps7200/direct.py:853-854`, `rps7200/direct.py:749-804`, `TODO.md:1479-1485`

**Doc claim:** rps7200/library.py:28-30 vs TODO.md:1479-1485

Re-deriving a reference from its source bytes requires calibration/, which the library docstring says nothing about. For --reuse scans no field connects the entry to its calibration archive, so matching is only possible by guessing timestamps. A crash between writing data.bin and calibration.json leaves an unlabelled archive.

**Evidence (from the code):**

```text
library.py:28-30: "Entries are self-contained directories: nothing refers out, so one can be copied or deleted on its own." TODO.md:1482-1484: "an entry names its archive in `extra.shading_origin` but does not copy it, and `shading.npz` is only the reduction." direct.py:853-854 sets `self._shading_origin["archive"] = str(archive)` only on the calibrated path. The loaded origin (724-729) records only `path`, `file_modified_utc` and `loaded_utc`, not the archive the cache came from. archive_calibration writes data.bin before calibration.json with no INCOMPLETE marker.
```

**Failure scenario:** Someone backs up only library/ (the docstring says entries are self-contained). The calibration lines behind every shading.npz are lost. A better calculate_shading (e.g. the dark-term work in vignette-recalculation P1) can then never be re-run on those scans.

**Fix:** Copy data.bin into the entry, or at least record the archive path and sha256 in shading.npz and in the loaded origin. Fix the library docstring. Mark archive folders incomplete until calibration.json is written.

<details><summary>Second reader's check</summary>

library.py:28-29 says 'Entries are self-contained directories: nothing refers out'. The calibration bytes behind shading.npz exist only in calibration/<stamp>/ (direct.py:749-804) and are not copied into entries. The archive path is recorded only on the calibrated path (direct.py:853-854). A loaded origin (direct.py:724-729) names no archive. archive_calibration writes data.bin (:781) before calibration.json (:802) with no INCOMPLETE marker. No code reads the archive back.

</details>

<a id="docs-plans-todo-doc-19"></a>

### DOC-19 -- A scalar exposure_scale also scales the infrared exposure, which the docs say is a device constant the driver never moves

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/protocol.py:612-620`, `rps7200/direct.py:3027`, `tools/scan.py:213-217`, `tools/uniformity.py:694-697`, `docs/multi-exposure-plan.md:325-327`, `docs/whole-roll-plan.md:237-240`, `docs/protocol.md:416-418`

**Doc claim:** docs/multi-exposure-plan.md:325-327; docs/whole-roll-plan.md:237-240

Metering and brackets pass three-element lists, which leave IR at 1.0. A hand-given single scale on an RGBI pass writes an IR exposure the vendor never sends. It is recorded in meta['exposure'], but it is unmeasured territory the docs say does not occur.

**Evidence (from the code):**

```text
Settings.scaled: `if isinstance(factor, (int, float)): factors = [float(factor)] * len(self.exposure)`, where exposure is R,G,B,I. scan() does `settings = self.get_gain_offset().scaled(exposure_scale)`. tools/scan.py:216 and uniformity.py:697 turn a single `--exposure-scale` value into a scalar. multi-exposure-plan.md:325-326: "Our infrared exposure is a device constant (7745 ...; the vendor never meters it), so it is unaffected by the visible ladder anyway."
```

**Failure scenario:** `tools/scan.py --ir --exposure-scale 3` writes IR exposure 23235 instead of 7745. The IR plane's level and the pass duration change in an unmeasured way. An untied pass (--no-fast-ir) whose time scales with IR exposure could run past UNTIED_INFRARED_IDLE_S. That timeout risk is speculative and unmeasured.

**Fix:** Scale only the visible channels when a scalar is given, or document and test the IR behaviour explicitly.

<details><summary>Second reader's check</summary>

Settings.scaled with a scalar builds [factor]*len(self.exposure), which includes IR (protocol.py:612-613). tools/scan.py:216 and uniformity.py:697 turn a single --exposure-scale into a scalar, and scan() applies it via get_gain_offset().scaled(exposure_scale) (direct.py:3028). multi-exposure-plan.md:325-326 calls IR exposure a device constant. The timeout consequence the reader mentions is speculative, as they say.

</details>

<a id="docs-plans-todo-doc-20"></a>

### DOC-20 -- Open window/roll problems listed in TODO are all still present (misleading dialogs, inert nudge tick, one-way approved.json, millimetres stored and printed)

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2829`, `tools/gui.py:3009`, `tools/gui.py:6402-6440`, `tools/gui.py:8066`, `rps7200/direct.py:4005-4051`, `tools/scan_roll.py:117-118`, `tools/scan_roll.py:534`, `tools/scan_roll.py:700-705`, `TODO.md:482-498`, `TODO.md:1496-1507`

**Doc claim:** TODO.md:482-498, 1496-1507

Every item TODO's Known problems and Process sections list for the window and roll path is unchanged in code. They mislead the operator about what Delete destroys, whether a calibration will run, and what a tick does. They also keep millimetres in stored and printed data against the stated rule.

**Evidence (from the code):**

```text
gui.py:2829 "stay, and the frames can be rebuilt from them." gui.py:3009 "It will calibrate again first, which is the right default". approved_from_sheet: "Every ticked frame gets one, including those left at zero". scan_roll's held branch is taken for any frame with an Approved, so `elif correct or correct_dry_run:` (direct.py:4051) is never reached for ticked frames, while gui.py:8066 offers ("correct", "nudge registration between frames"). scan_roll.py:118 `--nudge` "move the film this many mm". :534 `print(f"offset the film by {sent:+.3f} mm ...")`. :701 `f"offset {offset:+.2f} mm"`.
```

**Failure scenario:** The operator deletes a roll believing its frames 'can be rebuilt', and loses the manifests, prescans and hand-set positions. Or they tick 'nudge registration between frames', commission from the sheet, and nothing nudges.

**Fix:** As TODO proposes: fix the two dialog texts, remove or wire the tick, and store units instead of mm.

<details><summary>Second reader's check</summary>

gui.py:2828-2829 still says 'the frames can be rebuilt from them', and the count at :2822-2823 considers only turns and flips (read_approved(...)[1:3]). gui.py:3009 says 'It will calibrate again first'. gui.py:8066 still offers the 'correct' tick, and approved_from_sheet (gui.py:6402-6406) emits an Approved for every ticked frame, so direct.py:4051 `elif correct` is unreachable for them. approved.json is read and written only in tools/gui.py. scan_roll.py:118 --nudge is in mm, and :534 and :701 print mm. All of this matches TODO.md:482-498 and 1496-1507.

</details>

<a id="docs-plans-todo-doc-21"></a>

### DOC-21 -- Stale file/function references and self-contradictions inside plan docs and TODO

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/dpi-tradeoff-plan.md:148-154`, `docs/tiff-compression-plan.md:116`, `docs/vignette-plan.md:564-566`, `docs/analog-gain-plan.md:40-54`, `docs/analog-gain-plan.md:203-204`, `TODO.md:1466-1467`, `rps7200/direct.py:2226-2229`, `tools/scan.py:39-41`, `research/frame-edge/REPORT.md:238-246`

**Doc claim:** see locations

These are small, but each is a present-tense pointer a reader will follow, to a file that does not exist or to a behaviour described two opposite ways.

**Evidence (from the code):**

```text
dpi-tradeoff-plan.md:148 names `tools/dpi_series.py`, which does not exist. tiff-compression-plan.md:116 names `rps7200/cli.py`, which does not exist. vignette-plan.md:564 names `DirectScanner.calibrate()`, which is gone. analog-gain-plan.md:40-54 records CyberView writing gain 44/38/26 for calibration, then :203-204 says the register stays "exactly as the vendor leaves it in 36 of 36 captures". The driver writes back the read reference (`self.set_gain_offset(self.get_gain_offset())`, direct.py:2226) and never raises gain as the vendor does. TODO.md:1467 says calibration "costs ~22 s to re-measure", while tools/scan.py:41 `CALIBRATION_S = 210.0` and ensure_shading says 3-4 minutes. REPORT.md:238 heading "Moving it into the software (not done yet)" and its plan to use `command_for`, although README says it is in tools/frame_edges and the move path uses columns_to_mm -> param_for_mm.
```

**Failure scenario:** A future session budgets 22 s for a recalibration inside a foreground run near the 10-minute kill, or searches for dpi_series.py and cli.py.

**Fix:** Sweep the plans for file and function names that no longer resolve, and mark superseded sections.

<details><summary>Second reader's check</summary>

tools/dpi_series.py and rps7200/cli.py do not exist. DirectScanner has no calibrate() method. analog-gain-plan.md:40-54 (the vendor raises gain for calibration) contradicts :203-204 ('as the vendor leaves it in 36 of 36 captures'). calibrate_shading writes back get_gain_offset() unchanged (direct.py:2226). TODO.md:1466 says '~22 s to re-measure', while scan.py:41 has CALIBRATION_S = 210.0 and ensure_shading/load_shading say 3-4 minutes. REPORT.md:238 is headed 'Moving it into the software (not done yet)', although tools/frame_edges exists.

</details>

<a id="docs-plans-todo-doc-22"></a>

### DOC-22 -- The bracket and measurement-tool decisions TODO lists are all still open in code

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/scan.py:195-211`, `rps7200/bracket.py:233`, `rps7200/bracket.py:356-364`, `tools/exposure_headroom.py:86`, `tools/exposure_headroom.py:182`, `tools/transport_truth.py:71`, `tools/transport_truth.py:153`, `TODO.md:625-694`

**Doc claim:** TODO.md:619-694

These are recorded as 'Decisions for Stefan' and are unchanged. They are listed here so a doc-vs-code comparison shows them as still live, not fixed. The retyped constants in transport_truth break the 'never retype' rule for a tool whose output decides CONFIDENCE_FLOOR.

**Evidence (from the code):**

```text
scan.py refuses `--bracket` with `--ir` but has no check for `--bracket` with `--no-shading`. `fit_noise_params` (bracket.py:233) has no caller in rps7200/ or tools/. merge_bracket defaults `alpha: float = DEFAULT_ALPHA, beta: float = DEFAULT_BETA`. exposure_headroom.py:182 `aims[2] = target * MEASURED_BLUE_RATIO / BLUE_RGBI_HEADROOM` uses one film's figures for all films. transport_truth.py:71 `register(..., max_shift=106)` and :153 `if conf < 55:` are retyped constants.
```

**Failure scenario:** `tools/scan.py --bracket 3 --no-shading` fuses each pass's column pattern into the merge. Or a CONFIDENCE_FLOOR change is validated by transport_truth against a stale 55.

**Fix:** As TODO proposes: refuse --bracket with --no-shading, import CONFIDENCE_FLOOR and the reach, and decide the noise model.

<details><summary>Second reader's check</summary>

scan.py:195-211 refuses --bracket with --ir, but nothing rejects --bracket with --no-shading. fit_noise_params (bracket.py:233) has no caller outside bracket.py docs. exposure_headroom.py:182 uses one film's blue ratio. transport_truth.py:71 retypes max_shift=106 and :153 retypes 55 rather than importing them. All are listed under TODO decisions and all are unchanged.

</details>

<a id="docs-plans-todo-doc-a1"></a>

### DOC-A1 -- A roll frame entry's prescan.tif holds the corrected prescan, and its record does not say so

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3968-3975`, `rps7200/direct.py:4033-4035`, `rps7200/session.py:2618-2628`, `tools/scan_roll.py:752-755`, `rps7200/library.py:269-270`, `rps7200/library.py:377-382`

**Doc claim:** CLAUDE.md 'The library holds raw pixels; everything else is corrected'; rps7200/library.py:19 ('prescan.tif  the framing pass') and :240-249

CLAUDE.md ('The library holds raw pixels; everything else is corrected') and library.save's contract (image must be raw; `corrections` labels anything else) set the rule. Inside a frame entry, prescan.tif is the shading-corrected pixel array computed by that day's code, and after a hold it is the last hold pass. The record carries no flag saying it is corrected. The raw version of the same pass is filed as a separate prescan entry (session.py:2550-2565 with raw_image=rf.raw_prescan; scan_roll.py:666-684), but nothing links the two entries' files.

**Evidence (from the code):**

```text
direct.py:3968 `prescan_image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading, ...)`, and `raw_prescan = self.last_pixels_raw` is kept separately. The frame is filed with `prescan=rf.prescan, prescan_meta=rf.prescan_meta` (session.py:2627; scan_roll.py:752 `prescan=frame.prescan`). library.save then does `tiff.write(str(path / "prescan.tif"), prescan, ...)` and records only `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}`, with no correction state.
```

**Failure scenario:** A registration re-analysis, or migrate-direction (library.py:854-890), reads a frame entry's prescan.tif as though it were raw, or compares it with a raw scan.tif. Its column gain has already been divided out, so the comparison mixes corrected and raw pictures. When the correction code improves, this prescan.tif cannot be re-corrected in place.

**Fix:** Pass the raw prescan (rf.raw_prescan) as the entry's prescan.tif, or record `prescan.corrections` / `prescan.corrected: true` and the id of the prescan's own entry.

<a id="docs-plans-todo-doc-a2"></a>

### DOC-A2 -- session_start's docstring gives SLIDE 00 01 00 04, but the code sends 00 01 00 00, a sub-frame action with an unmeasured value byte

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:2099-2124`, `rps7200/direct.py:1500-1502`, `tools/hold_probe.py:130`, `tools/transport_truth.py:122`

**Doc claim:** rps7200/direct.py:2102-2103 (session_start docstring)

Every probe tool that opens with session_start (hold_probe, transport_truth, exposure_probe, gain_probe, fast_ir_probe, byte14_probe, roll_registration_walk) sends a forward sub-frame SLIDE whose value byte differs from the documented vendor payload. Its effect on film position is unmeasured. Measurements taken after it may start from a film position that was nudged, or not, without either being recorded.

**Evidence (from the code):**

```text
The docstring says "a SLIDE with `00 01 00 04`". The code is `self.slide(0x00, param=0x01)`, and `def slide(self, action: int = SLIDE_INIT, param: int = 0x16, value: int = 0)` makes the payload `00 01 00 00`. The slide docstring itself says `value` matters and that the vendor pairs 0x04 with small sub-frame params.
```

**Failure scenario:** transport_truth or hold_probe measures travel from a baseline taken after session_start's move. If 00 01 00 00 moves the film by one param-1 ramp (about 2.84 units), the first delta is biased. If it does not, the docstring's claim that this replicates the vendor is false.

**Fix:** Send value=0x04 as documented, or correct the docstring and state that the value byte differs. Record in the probes' output whether the opening move happened.

<a id="docs-plans-todo-doc-23"></a>

### DOC-23 -- docs/stefan-judgement.json is cited as the acceptance scorer but no code reads it

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/stefan-judgement.json`, `docs/frame-measurement-plan.md:106-107`, `docs/frame-measurement-plan.md:273-277`, `docs/registration-accuracy-plan.md:154-159`

**Doc claim:** docs/registration-accuracy-plan.md:154-159

The verification the plans prescribe is manual. No harness scores detector output against the recorded verdicts, and the rolls it names are not on this machine per research/frame-edge/README.md.

**Evidence (from the code):**

```text
A grep of tools/, rps7200/, tests/ and research/ for 'stefan-judgement' finds nothing. registration-accuracy-plan.md:154-157: "replay `propose_offsets` (now `frame_edges.propose_centred` ...) over `rolls/registration-{A,D,E,G,I,J,K}` ... and score the result against `docs/stefan-judgement.json`".
```

**Failure scenario:** A detector change is described as 'verified against Stefan's verdicts' when no such check exists in code.

**Fix:** Either add a scorer (tools/ or tests/ gated on the rolls being present) or reword the plans as a manual procedure.

<details><summary>Second reader's check</summary>

No .py file in the repo (excluding .venv) mentions stefan-judgement. The plans (frame-measurement-plan.md:106 and 274, registration-accuracy-plan.md:157) prescribe scoring against it, so that scoring is a manual procedure.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Raw scanner bytes of a pass | library/<id>/raw.bin.gz (or raw.bin before library.compact) | gzip (level 6) of the exact bulk bytes, INDEX-format lines with 2-byte tags | raw | library.save (raw= or streamed raw_path=), library.compact | library.read_raw, reconstruct, verify (sha256), decode_raw, tools/library.py, tools/uniformity.py rebuild, dpi_analysis | yes: byte-exact with sha256 in scan.json raw.sha256 |
| Decoded pixels | library/<id>/scan.tif | TIFF uint16/uint8, deflate+horizontal predictor via tifffile, else uncompressed | raw decode, but turned upright if read bottom-up (decode_index) and stagger-realigned at 7200 dpi (recorded as scan.read_direction and scan.stagger_realigned) | library.save | library.load/corrected, export, make_comparison, verify (sha256) | lossless; re-derivable from raw bytes |
| Shading reference used for the pass | library/<id>/shading.npz; calibration/shading.npz (cache); calibration/shading_uniformity.npz (uniformity tool) | np.savez_compressed: pixels_per_line, channels, ref{c}/mean{c}, dark{c}/darkmean{c} float64 | a reduction of the calibration lines (dark/light split by level); may include channel 3 (IR) | ShadingReference.save via library.save; DirectScanner.save_shading (atomic temp+replace); tools/uniformity.py:743 (non-atomic) | library.corrected, reconstruct, load_shading/--reuse, uniformity rebuild | exact for the reduction. No timestamp, session or device identity in the file |
| Calibration source bytes | <ref_path.parent>/calibration/<YYYYmmddTHHMMSSZ>[-n]/{data.bin,calibration.json,shading.npz,ccd_mask.bin} | data.bin exact lines; calibration.json with width, stride, resolution, commands, sha256, protocol_revision | raw | DirectScanner.archive_calibration, only via ensure_shading. NOT tools/uniformity.py, which calls calibrate_shading() with keep_data=False | nothing in code; entries name it in extra.shading_origin.archive only when calibrated this session | exact bytes. Non-atomic (no INCOMPLETE marker); not copied into entries |
| CCD mask of the pass | library/<id>/ccd_mask.bin | 5172 (or calibration width) bytes, 0x00 used / 0x70 unused | raw | library.save | apply_shading via library.corrected/reconstruct | yes, with sha256 in scan.json files |
| Entry record | library/<id>/scan.json (+ INCOMPLETE marker while writing) | JSON: image, raw, scan (SCAN_FIELDS incl. protocol_revision, filter_offsets, read_direction, stagger_realigned, fast_infrared), extra (commands, shading_origin, roll_membership), calibration (report, skipped), metering, registration, files checksums, provenance | metadata | library.save (atomic, written last) | entries/verify/corrected/reindex/tools | yes for recorded fields. Hold/aim SLIDE params and between-pass transport commands are NOT recorded (only mm) |
| Stored framing pass | library/<id>/prescan.tif | TIFF 8-bit | whatever the caller passed; no raw bytes of its own; direction recorded in prescan.read_direction | library.save(prescan=...) | migrate-direction, registration studies | not re-derivable (no raw bytes) |
| Library summary | library/index.json | JSON list | derived | library.reindex (every save) | listing | derived, rebuildable |
| Contact-sheet decisions | rolls/<roll>/approved.json | JSON with offset_mm (millimetres), rotation, flip, source | operator decisions | tools/gui.py (on_scan_chosen) | tools/gui.py only (scan_roll.py never reads it) | mm values re-snapped under the current law; the unit held contradicts the no-millimetres rule |
| Walk/roll manifests and delivered frames | rolls/<roll>/{survey.json,roll.json,prescanNN.tif,frameNN.tif} | JSON; TIFF | frames and prescans corrected by that day's code | ScanSession/FrameWriter, tools/scan_roll.py | window open_roll, scan_roll --approved (hold_from_walk) | corrected, not re-derivable except via library entries |
| Exposure probe map | probe/exposure.json | JSON | index into library entries | tools/exposure_probe.py | tools/linearity.py --probe | gitignored; the docs' numbers depend on it |
| Eye verdicts | docs/stefan-judgement.json | JSON | human judgement | hand | no code (plans say to 'score against' it) | n/a |

**Second reader's corrections to this table:**

- **prescan.tif**: in roll frame entries, prescan.tif is not "whatever the caller passed" in general. It is always the shading-corrected prescan: rf.prescan comes from self.prescan() with shading on, and after a hold it is the last hold pass. The record's `prescan` block carries no correction flag. The raw pixels of that pass go to a separate prescan entry (kind="prescan", raw_image=rf.raw_prescan), with no cross-link.
- **Shading reference**: the claim that calibration/shading_uniformity.npz is written non-atomically by tools/uniformity.py:743 is correct. Also, the entries those passes produce record shading_origin 'loaded' with no archive, because calibrate_shading() there runs with keep_data=False, so no calibration/<stamp>/ archive is written at all.
- **Calibration source bytes**: the path is `<cache path's parent>/<stamp>`, i.e. calibration/<stamp>/ when the cache is calibration/shading.npz. The record's measured_utc comes from self._shading_origin.
- **approved.json**: read and written only by tools/gui.py (read_approved, gui.py:5410, and the writer near gui.py:3282). scan_roll.py and session.py never open it. This confirms the claim.
- **raw.bin.gz**: gzip level 6 is confirmed (library.py:283, 430).

## What the operator can do

- Run `uv run python tools/library.py verify|reconstruct|duplicates|migrate-raw|migrate-direction|tag ENTRY --add uncalibrated-on-purpose` offline against stored entries.
- Scan with `tools/scan.py` (calibrates on open unless --reuse with an existing cache or --no-shading) and file raw bytes, reference and mask.
- Walk a strip with `tools/scan_roll.py --dry-run` (prescans filed with raw bytes and roll_membership), then hold it with `--approved`, or aim with `--correct` at 300 dpi prescans.
- Untie infrared with `--no-fast-ir`, `scan(fast_infrared=False)`, or the window box.
- Re-run `tools/dpi_analysis.py --domain raw`, `tools/linearity.py`, `tools/registration_margin.py`, and `FRAME_EDGE_PARITY=1 pytest tests/test_frame_edges_parity.py` offline.

## What the operator should not do

- Calibrate with an empty transport (CLAUDE.md). docs/vignette-plan.md:248-261, docs/multi-exposure-plan.md:395 and tools/uniformity.py's prompts still lead there.
- Use `--reuse` across a power cycle: the cache carries no timestamp or identity, only the file mtime in shading_origin.
- Pass a single `--exposure-scale` on an RGBI pass: it also scales the IR exposure.
- Commission a sheet roll containing 'unconfirmed' or 'neighbours' positions without reviewing them: they move film on one detector member's word.
- Call advance/retreat with steps>1: the value byte is sent, not N frames, and the demo behaves differently.
- Back up only library/: the calibration bytes live in calibration/<UTC>/.

## Mistakes nothing guards against

- `tools/uniformity.py capture` without `--exposure-scale` always fails at the first metering probe (ShadingUnavailable), after the operator has already emptied the transport.
- `tools/uniformity.py capture` calibrates without archiving the calibration's raw bytes, and writes calibration/shading_uniformity.npz non-atomically.
- `tools/scan.py` calibrates immediately after INQUIRY with no confirmation that film is loaded, and records no media flag at calibration time.
- `tools/scan.py --bracket N --no-shading` is accepted and merges uncorrected passes.
- The window's Delete-roll dialog says frames 'can be rebuilt'; nothing rebuilds a roll folder's manifests, prescans or hand-set positions.
- The window's reopen-roll dialog says 'It will calibrate again first'; a window that already calibrated does not.
- Ticking 'nudge registration between frames' on the sheet does nothing on a commissioned scan.
- A commissioned (approved) roll has no roll-wide travel cap, and a large wrong target buys itself a larger per-frame budget.
- An approved offset beyond about 9.0 mm (about 85 units) is moved but can never be verified within SEARCH_MM's reach.
- A 600 dpi pass 862 wide against an 860-pixel mask is delivered with its last 2 columns uncorrected, and nothing warns.

## Dataflow notes

**Bytes in.** `DirectScanner.scan()` (direct.py:2842):
1. Refusals come before anything is sent (direct.py:2912-2963): no IR on B&W (the `supports_infrared` check), `correctable_at`/`uncorrectable`, `uncalibrated`.
2. Optional RGB metering through `auto_exposure` (2385), which probes with `scan(..., shading=shading)`.
3. `_CommandLog.start()` (2990): only commands sent inside a pass are recorded.
4. `set_gain_offset(get_gain_offset().scaled(exposure_scale))` (3027). A scalar scale also scales IR.
5. `set_mode(..., fast_infrared=fast_infrared and infrared)` (3049-3058), then `_read_pass`.
6. `decode_index` (1923) reads the direction from the tags (direction.py) and turns bottom-up passes upright.
7. At 7200 dpi, `_realign_native_column_stagger` (2009) runs (recorded as `stagger_realigned`).
8. `raw_pixels = image` is kept, then `apply_shading(image, self._shading, ccd_mask)` (shading.py:196). It corrects every channel the reference has, IR included when it carries tag 0x49, and only the first `loc.size` columns.
9. `meta` (3159+) records protocol_revision, filter_offsets, read_direction, carriage_state, commands, fast_infrared and shading_skipped.

**Calibration.** `ensure_shading` (806) calls `calibrate_shading(keep_data=True)` (2128), whose `calculate_shading` (shading.py:127) splits dark/light by level. `archive_calibration` (749) writes calibration/<UTC>/, then `save_shading` (736, atomic). `load_shading` (713) records only path, mtime and load time in `_shading_origin`. tools/uniformity.py:735 calls `calibrate_shading()` directly, with no archive.

**Filing.** `library.save` (library.py:216) takes raw pixels plus raw bytes, reference, mask and meta, and writes scan.tif, raw.bin(.gz), shading.npz, ccd_mask.bin, prescan.tif, then scan.json atomically, then reindexes. `library.corrected` (library.py:~560-600) re-applies today's `apply_shading` from the entry's own reference and mask; `export.write` delivers the result.

**Framing and movement.**
1. `tools/frame_edges.propose.detect` → `vote.detect` (`vote_v2`: two-member vote, else `lone_gap`).
2. `centring` → `decide` → units → columns → `columns_to_mm` (APERTURE_MM/width) → `Approved.offset_mm`, or `WalkReader.judge`.
3. `_aim_frame` (3434) / `_hold_to_approved` (3306): `measure_shift_mm` (SEARCH_MM reach, both orientations) → `hold_plan` (budget |target|+2 mm) → `nudge` → `param_for_mm` (MM_PER_UNIT, MM_PER_COMMAND from protocol.COMMAND_UNITS) → `slide()`.
4. None of the nudge params are logged into the entry. Only target_mm, spent_mm and the history reach `meta['registration']` (4136).

**Out.** Delivered TIFF/JPEG/DNG are corrected. The library keeps raw data. calibration/ holds the calibration source bytes, and no entry copies them.
