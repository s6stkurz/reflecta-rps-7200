# Framing and transport units

Area key `framing-units`. 20 findings: 2 high, 4 medium, 13 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Framing covers four pieces. The first is rps7200/framing.py, which holds the legacy mm-based detectors (film_bounds, gap_edges, base-level picture_start/right_gap_closure/predict_offset/combine), the StripWalk memory for in-walk aiming, the hold loop's math (measure_shift_mm, hold_plan) and a unit-based section (COLUMNS_PER_UNIT 1.2423, COMMAND_COST 1.84, FRAME_WIDTH_UNITS 350.6, units_per_column, the unused command_for). The second is tools/frame_edges, a copy of the research ensemble: four members, a vote, decide/centring and EdgeWatch. EdgeWatch is a background thread that reads a walk's prescans and hands the sheet offset_mm plus notes. It is internally sound: everything is under one Condition, work is versioned and generation-checked, and failures abstain. The third is rps7200/shortcuts.py, which is keymap data only. The fourth is how the window (sheet, adjuster, approved.json), ScanSession and tools/scan_roll.py consume all of this.


Main problems:
- The "reverse the direction" checkbox in the Transport panel is remembered between launches and silently mirrors every approved hold target of a commissioned roll.
- The prescans that aiming and hold decisions are computed from are not kept raw. The pre-move prescan is dropped outright on the approved path and on real rolls, and the frame entry's prescan.tif holds corrected pixels without saying so.
- The hold loop's correlation reach (SEARCH_MM 9.0 mm = 105 columns) is smaller than the largest move the sheet and nudge allow (param 87 = 9.39 mm = 110 columns).
- Two frame-width models still coexist. The legacy 36.0 mm model is used silently for positive and Kodachrome rolls with "correct" on.
- scan_roll --approved diverges from the sheet path it claims to be.
- Internally every sub-frame distance is still millimetres, persisted as offset_mm, against the owner's rule.
- A blank or very flat frame ends a walk or roll as "end of film".
- A NaN reaching snap_offset becomes the maximum forward move.
- Plus several retyped constants, dead code and stale docs.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [FR-01](#framing-units-fr-01) | high | user-error | confirmed | 'reverse the direction' checkbox (remembered across launches) silently mirrors every approved hold target in a commissioned roll |
| [FR-02](#framing-units-fr-02) | high | data-integrity | confirmed | The prescans that framing decisions are made from are not kept raw; the pre-move prescan is discarded and the frame entry's prescan.tif is corrected but unlabelled |
| [FR-03](#framing-units-fr-03) | medium | bug | confirmed | Hold-loop correlation reach (SEARCH_MM 9.0 mm = 105 columns) is smaller than the largest move the sheet and nudge allow (9.39 mm = 110 columns) |
| [FR-06](#framing-units-fr-06) | medium | doc-mismatch | confirmed | Every sub-frame distance is still carried and persisted in millimetres, with two unit definitions 0.2% apart |
| [FR-07](#framing-units-fr-07) | medium | user-error | confirmed | A blank or very flat frame ends a walk or roll as 'end of film' |
| [FR-08](#framing-units-fr-08) | medium | data-integrity | confirmed | Hold and aim verification prescans are filed with film='negative' whatever the roll's film |
| [FR-04](#framing-units-fr-04) | low | design | partly | Two frame-width models coexist; legacy 36.0 mm model (with a closure member that can only fire on misdetections) silently aims positive/Kodachrome rolls |
| [FR-05](#framing-units-fr-05) | low | doc-mismatch | partly | scan_roll --approved is not the sheet's path: ignores approved.json, does not snap, and holds nothing for frames the detector refused |
| [FR-09](#framing-units-fr-09) | low | error-handling | confirmed | No finite-value guard between detector, sheet, approved.json and the mover; NaN snaps to the maximum forward move |
| [FR-10](#framing-units-fr-10) | low | dead-code | confirmed | Dead unit-based move law in framing contradicts the driver's cap and its own claims |
| [FR-11](#framing-units-fr-11) | low | design | partly | Transport and geometry constants retyped in several places, one with a hair-trigger margin |
| [FR-12](#framing-units-fr-12) | low | doc-mismatch | confirmed | Stale documentation of the move law and cap across framing's consumers |
| [FR-13](#framing-units-fr-13) | low | user-error | confirmed | GUI lets a 'correct' roll run at a prescan dpi the edge reader cannot read (warning only); the CLI refuses |
| [FR-14](#framing-units-fr-14) | low | error-handling | confirmed | A malformed shortcut in gui-settings.json stops the window from opening |
| [FR-15](#framing-units-fr-15) | low | demo-divergence | confirmed | Demo film movement wraps around (np.roll), so moved prescans correlate near-perfectly and edges read wrapped content |
| [FR-16](#framing-units-fr-16) | low | bug | confirmed | combine(): tie in the 'none agree' diagnostic compares Reading objects (TypeError), and the message names the furthest pair 'closest' |
| [FR-17](#framing-units-fr-17) | low | design | confirmed | Roll travel budget sums absolute nudge travel, which per-frame centring legitimately accumulates |
| [FR-18](#framing-units-fr-18) | low | bug | confirmed | EdgeWatch keeps a frame's error after a later successful reading, so the light can stay FAILED |
| [FR-A1](#framing-units-fr-a1) | low | design | found-by-verifier | The 'reverse' flag is honoured by one path only: sheet commissions mirror their targets, while scan_roll --approved and in-walk aiming ignore it |
| [FR-19](#framing-units-fr-19) | info | data-integrity | confirmed | approved.json records only the snapped offset; the detector's reading behind a sheet proposal is not persisted |

## Findings in full

<a id="framing-units-fr-01"></a>

### FR-01 -- 'reverse the direction' checkbox (remembered across launches) silently mirrors every approved hold target in a commissioned roll

**Severity** high · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:1502-1504`, `tools/gui.py:140-142`, `tools/gui.py:3194`, `tools/gui.py:5243`, `tools/gui.py:7805`, `rps7200/direct.py:3344`, `rps7200/direct.py:3386-3391`

The checkbox reads like a setting for manual fine moves. In the code it is also passed as Roll.reverse_hold for every commissioned roll, and there it negates each Approved.offset_mm before the hold loop runs. The flag is saved in gui-settings.json and restored from a reopened roll's settings, and the Scan chosen frames dialog never shows it. The one runtime check, wrong_way, compares measured motion against the commanded direction. Both follow the negated target, so the mirror position is reached and reported as 'held'.

**Evidence (from the code):**

```text
gui.py:1502 `self.v_reverse = tk.BooleanVar(value=False)` / 1504 `text="reverse the direction"` (Transport panel, beside the fine adjustment); gui.py:140-142 REMEMBERED = (..., "fine", "aim", "reverse", ...); gui.py:3194 `approved=tuple(approved), reverse_hold=self.v_reverse.get(),`; gui.py:5243 RESTORABLE `"reverse_hold": ("reverse", bool),`; direct.py:3344 `target = -approved.offset_mm if reverse else approved.offset_mm`; direct.py:3390 `if abs(went) > HOLD_TOLERANCE_MM and (went > 0) != (want > 0):`; the sheet's OPTIONS (gui.py:7805) = ("dpi", "predpi", "film", "meter", "ir", "fast_ir", "correct") has no reverse, and the confirm text in on_scan_chosen is built from _approved_note/_options_note/_edges_pending, none of which mentions it.
```

**Failure scenario:** The operator ticks 'reverse the direction' once for a manual nudge, or reopens a roll that was scanned with it on. Next session he sets or accepts sheet positions (for example +40 units to centre a frame) and presses Scan chosen frames. Every frame is driven to -40 units, about 80 units (a quarter of the frame) from where he placed it. The log reports 'held' and _report_held lists nothing, so pictures are cut silently for the whole roll.

**Fix:** Decouple reverse_hold from the manual-move checkbox, or drop it from REMEMBERED/RESTORABLE. Show it in the Scan chosen frames confirmation, and log it per frame when it is applied.

<details><summary>Second reader's check</summary>

Checked at HEAD f193920. gui.py:1502-1504 creates the Transport-panel 'reverse the direction' tick. gui.py:141 puts it in REMEMBERED and gui.py:5243 in RESTORABLE, so it comes back from gui-settings.json and from a reopened roll's settings.reverse_hold (session.py:2445). gui.py:3194 hands it to every sheet-commissioned Roll as reverse_hold. session.py:2512 passes it on, and direct.py:4008 gives it to _hold_to_approved, where direct.py:3344 negates the target: `target = -approved.offset_mm if reverse else approved.offset_mm`.

The hold loop is closed-loop in picture space. measure_shift_mm compares the current prescan with the approved reference, so negating the target drives the picture to the mirror position whatever the transport's physical sense. The wrong_way check at direct.py:3390 compares motion with the commanded `want`, which follows the negated target, so it never trips, and the outcome reads 'held'.

The tick is advertised as a fix for manual moves: the help text at gui.py:1959 says 'If it goes the wrong way, tick reverse the direction', and gui.py:3403 and 5057 apply it to Move and Aim. _approved_note (3226-3263) never mentions it, and the sheet's OPTIONS (7805) omit it.

Two small corrections to the reader's account:
- It is not wholly unrecorded. Each frame's registration.approved carries reverse_applied, and target_mm already negated (direct.py:3346-3347).
- The same flag is honoured inconsistently. _aim_frame never passes it (direct.py:3509-3514), and tools/scan_roll.py:605-632 never passes reverse_hold. So the GUI sheet path is the only one that mirrors.

</details>

<a id="framing-units-fr-02"></a>

### FR-02 -- The prescans that framing decisions are made from are not kept raw; the pre-move prescan is discarded and the frame entry's prescan.tif is corrected but unlabelled

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:4031-4036`, `rps7200/direct.py:4061-4068`, `rps7200/direct.py:3377-3378`, `rps7200/session.py:2536`, `rps7200/session.py:2567-2590`, `rps7200/session.py:2625-2629`, `rps7200/library.py:269-270`, `rps7200/library.py:377-382`

**Doc claim:** CLAUDE.md 'File every scan in the library, with its raw bytes' and 'The library holds raw pixels; everything else is corrected ... What an entry holds ... a prescan.tif where there was one'

The owner requires the library to hold exact raw data for every pass so decisions can be recomputed later. Aiming (_aim_frame) and holding (_hold_to_approved) are decided on the arrival prescan and then on each verification prescan, and none of those passes is filed raw unless RPS7200_DEBUG=1, which is off by default:
- On the approved-hold path the arrival prescan is dropped outright, even on a walk.
- On a real roll the correct path's prescan_before is dropped too; only walks write prescanNN-before.tif, corrected and without an entry.
- The frame entry's prescan.tif holds the corrected post-move pixels with no raw bytes, and its record does not say it is corrected.
The history numbers (confidence, dx) survive in the registration record, but the images behind them do not.

**Evidence (from the code):**

```text
direct.py:4031-4032 (approved branch) `if fix.get("prescan") is not None:` / `prescan_image = fix["prescan"]`, where no prescan_before is kept; direct.py:4062-4063 (correct branch) `prescan_before = prescan_image` / `prescan_image = fix.pop("prescan")`; session.py:2536 `if job.dry_run:` encloses 2567 `if rf.prescan_before is not None:` … 2586 `file_entry=False,`, so the before-prescan is written only on a walk and only as a corrected TIFF with no entry; session.py:2627 frame entry `prescan=rf.prescan,` with no raw prescan passed; direct.py:3377 `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)` (every verification pass, never filed); library.py:270 `tiff.write(str(path / "prescan.tif"), prescan, ...)`, and the record's prescan block (377-382) carries only file/read_direction/carriage_state.
```

**Failure scenario:** Later, a better shading correction or edge detector casts doubt on why frame 12 of a roll was moved 30 units, or held 'unverified'. The pass it was judged on exists only as numbers in scan.json. The arrival prescan is gone, and the entry's prescan.tif is the post-move pass, already corrected with that day's code. Neither `reconstruct` nor a re-run of measure_shift_mm or the detector on the original input is possible.

**Fix:** File the arrival prescan (raw, with its capture record) whenever a hold or aim will replace it, on walks and real rolls alike, and file the verification passes too. Store prescan.tif raw with its bytes, or at least label it with the corrections applied. Alternatively, make debug filing of hold and aim prescans the default for rolls.

<details><summary>Second reader's check</summary>

Approved branch, direct.py:4031-4036: the arrival prescan_image is overwritten with fix['prescan'], and prescan_before is not set. Correct branch, direct.py:4061-4063: prescan_before is kept, but session.py:2567-2590 writes it only inside `if job.dry_run:`, as a corrected TIFF with file_entry=False and no raw bytes.

The hold and aim verification passes (direct.py:3377) are never filed unless debug filing is on. For the frame entry, session.py:2627 passes prescan=rf.prescan, the corrected image from DirectScanner.prescan (shading=True by default, direct.py:2053-2083), and no raw prescan. library.py:269-270 writes it, and the record's prescan block (377-382) holds only file, read_direction and carriage_state, with nothing marking it corrected.

library.save's docstring admits 'prescan.tif has no raw bytes of its own', but the record does not label the pixels as corrected. The numeric history survives in registration.approved.history. The images behind it do not.

</details>

<a id="framing-units-fr-03"></a>

### FR-03 -- Hold-loop correlation reach (SEARCH_MM 9.0 mm = 105 columns) is smaller than the largest move the sheet and nudge allow (9.39 mm = 110 columns)

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/framing.py:854-857`, `rps7200/framing.py:1061-1063`, `rps7200/framing.py:1095-1096`, `rps7200/uniformity.py:378-383`, `tools/gui.py:241`, `tools/gui.py:5897`, `rps7200/session.py:96-97`

**Doc claim:** rps7200/framing.py:854-855 'Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window.'

SEARCH_MM was sized 'just over MAX_TRAVEL_MM' when a move chained param-8 commands. MAX_CORRECTION_PARAM is now 87, so one command, and the largest offset the adjuster, snap_offset or a detector proposal can carry, reaches 9.39 mm, about 110 prescan columns. register() excludes any shift past ±105 columns. A target beyond about 8.95 mm (about 84.7 units) therefore moves the picture outside the searched window. The verification pass cannot see the true peak: it reads 'unverified' after an open-loop move, or locks onto a false peak inside the window and moves again (the budget allows |target|+2 mm).

**Evidence (from the code):**

```text
framing.py:854-857 `Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window... SEARCH_MM = 9.0`; framing.py:1063 `reach = int(SEARCH_MM / max(mm_per_px, 1e-9))` (at 300 dpi 36.4913/428 = 0.08526 mm/px, so reach = 105); uniformity.py:379 `reach_x = min(max_shift, w // 2)` and 381-383 only fill `window` for dx in ±reach; session.py:96 `FINE_MAX_MM = (DirectScanner.STEP_MM * DirectScanner.MAX_CORRECTION_PARAM + DirectScanner.OVERHEAD_MM)` = 0.1057×88.84 = 9.390 mm; gui.py:241 `MAX_TRAVEL_MM = MAX_FINE_MM`; gui.py:5897 `want = max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, float(millimetres)))`.
```

**Failure scenario:** A frame is set, or proposed, at +87 units, as when the neighbour's gap is far in. The hold loop sends param 87 and re-prescans. The picture has moved about 110 columns, but register only searches ±105. The frame is logged unverified, or it is driven on by a spurious match up to 2 mm further.

**Fix:** Either cap proposals and snap_offset at the verifiable range, or size SEARCH_MM from MAX_CORRECTION_PARAM and re-fit CONFIDENCE_FLOOR at that reach (the confidence scale depends on it, per its own docstring). Fix the 'just over MAX_TRAVEL_MM' claim.

<details><summary>Second reader's check</summary>

The arithmetic checks out:
- APERTURE_MM = 10344 × 25.4 / 7200 = 36.4913 mm (framing.py:373). At 428 columns that is 0.08526 mm per pixel, so framing.py:1063 gives reach = int(9.0 / 0.08526) = 105.
- uniformity.register fills its window only for dx in ±min(max_shift, w // 2) = ±105 (uniformity.py:378-383).
- FINE_MAX_MM = 0.1057 × 87 + 0.1057 × 1.84 = 9.390 mm, about 110 columns (session.py:96-97). gui.py:241 sets MAX_TRAVEL_MM to that, and snap_offset clamps to it (gui.py:5897).

direct.py:3592's own comment records that param 87 moved 109 px, which is outside a ±105 search. A target near the top of the allowed range therefore lands the true peak outside the correlation window. The framing.py:854-855 claim 'Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window' is stale from the param-8 era.

tools/scan_roll.py hold_from_walk (293-297) does not clamp at all, so its targets can exceed even 9.39 mm.

</details>

<a id="framing-units-fr-06"></a>

### FR-06 -- Every sub-frame distance is still carried and persisted in millimetres, with two unit definitions 0.2% apart

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/frame_edges/centre.py:296-303`, `tools/frame_edges/centre.py:323-325`, `tools/frame_edges/propose.py:171-177`, `rps7200/session.py:1318-1347`, `tools/gui.py:3314`, `rps7200/framing.py:260-273`, `rps7200/framing.py:1151-1169`, `rps7200/framing.py:1933-1934`, `rps7200/protocol.py:228-229`, `rps7200/protocol.py:260`

**Doc claim:** CLAUDE.md 'Do not use millimetres for transport distances': 'express every sub-frame distance in units of the adjustment parameter'

CLAUDE.md says to express every sub-frame distance in param units, and gives the reason: conversions hide mistakes. The code does exactly those conversions. The edge detector decides in units (framing's column model), converts to columns, then to mm through APERTURE_MM/width, and stores the result as offset_mm (approved.json, Approved, sheet state). The mover converts that mm back to param through protocol's MM_PER_UNIT. The two unit definitions put the aperture at 344.5 and 345.2 units, so a detector move of u units is commanded as about 1.002u. The hold loop, StripWalk budgets and registration marks are all in mm, and protocol.py narrows the rule to 'anything an operator reads'. The numerical effect is small (about 0.18 units at param 87, under one param), but the rule as written is not what the code does, and the persisted records carry mm.

**Evidence (from the code):**

```text
centre.py:325 `return float(columns) * APERTURE_MM / float(width)`; propose.py:177 `return columns_to_mm(columns, width), note`; session.py:1346 `offset_mm: float = 0.0`; gui.py:3314 `"offset_mm": round(a.offset_mm, 4),`; framing.py:263-273 registration persists `"offset_mm": mm(offset)`, `"shortfall_mm"`, `"margin_mm"` rounded to 0.01 mm; framing.py:1151 `HOLD_TOLERANCE_MM = 0.3002`, 1169 `ROLL_TRAVEL_LIMIT_MM = 12.0`; framing.py:1934 `COLUMNS_PER_UNIT = 1.2423` vs protocol.py:260 `MM_PER_UNIT = 0.1057`; protocol.py:228 'Millimetres are prohibited in anything an operator reads'.
```

**Failure scenario:** A later reader of approved.json or scan.json registration sees offset_mm and must know which of two unit models produced it to recover the param. A future change to either constant (MM_PER_UNIT or COLUMNS_PER_UNIT) silently shifts every stored offset's meaning.

**Fix:** Carry offsets in param units end to end (centring already has `units`) and persist them as units along with the constant used. Or amend CLAUDE.md to state the actual rule (display only) and record the unit model in approved.json.

<details><summary>Second reader's check</summary>

Offsets are carried and persisted in mm:
- centre.py:325 columns_to_mm
- the Approved.offset_mm field
- gui.py:3314 writes offset_mm to approved.json
- framing.registration's *_mm keys (framing.py:260-273)
- HOLD_TOLERANCE_MM and ROLL_TRAVEL_LIMIT_MM

protocol.py:228 itself narrows CLAUDE.md's rule to 'anything an operator reads'. The two unit models differ as described:
- 428 / 1.2423 = 344.5 units per aperture
- 36.4913 / 0.1057 = 345.2 units per aperture
centre.py's docstring deliberately uses the APERTURE_MM/width scale so that the hold loop verifies in the same scale. Landing is therefore correct, and only the open-loop param picked is about 0.2% larger. The substance, stored records in mm against the owner's stated rule and with no unit model recorded, stands.

</details>

<a id="framing-units-fr-07"></a>

### FR-07 -- A blank or very flat frame ends a walk or roll as 'end of film'

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/framing.py:44-48`, `rps7200/framing.py:51-72`, `rps7200/direct.py:3985-3993`

Any prescan whose median column spread is under 2% of its level ends the whole generator. That covers an unexposed frame mid-strip (clear base on a negative), a lens-cap or misfired frame, or a nearly uniform fog or sky frame. Frames beyond it are never walked or scanned. The blank frame's prescan is not yielded, so it is not filed either, and the only trace is a log line. Nothing checks the transport counter or the requested `frames` before concluding the film has run out.

**Evidence (from the code):**

```text
framing.py:48 `BLANK_CONTRAST = 0.02`; framing.py:72 `return float(np.median(grey.std(axis=0)) / level)`; direct.py:3987-3993 `if contrast < blank_contrast:` / `self._log(... "end of film")` / `return`.
```

**Failure scenario:** The operator presses Scan roll for frames 1-36. Frame 7 was a misfire (unexposed), so the roll stops at frame 7 saying 'end of film'. Frames 8-36 are never scanned, and the operator only finds out by reading the log or counting outputs.

**Fix:** Treat a blank frame as a skipped frame while the counter and `frames` say the strip continues, for example by ending only after N consecutive blanks or when an advance does not move. Report an early end prominently, and file the blank prescan.

<details><summary>Second reader's check</summary>

direct.py:3985-3993 returns from the generator whenever frame_contrast < BLANK_CONTRAST (0.02), with no check of `frames`, the counter or `only`. The blank prescan is never yielded, so it is never filed (session.py files only yielded RollFrames).

In addition, frame_contrast returns 0.0 when the mean level is ≤ 0 (framing.py:69-71). A fully black prescan (dense slide, lamp problem) therefore also ends the roll as 'end of film'.

The demo inherits this unchanged via _drivers_roll = DirectScanner.scan_roll.

</details>

<a id="framing-units-fr-08"></a>

### FR-08 -- Hold and aim verification prescans are filed with film='negative' whatever the roll's film

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:2052-2058`, `rps7200/direct.py:3377-3378`, `rps7200/direct.py:4031-4036`, `rps7200/direct.py:4061-4068`, `rps7200/session.py:2549-2566`, `rps7200/demo.py:1096`, `rps7200/demo.py:1326`

The fix that records the roll's film on a frame's prescan covers only the first prescan. After a hold or aim, prescan_meta is replaced by the verification pass's meta, which says negative. On a walk that meta goes into the library entry for prescanNN; on a real roll it goes into the frame's prescan_meta. B&W and positive rolls therefore file prescan entries labelled negative. The demo picks its strips per film from that field.

**Evidence (from the code):**

```text
direct.py:2057 `film: str = FILM_NEGATIVE,` (prescan default); direct.py:3377-3378 `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)` (no film); direct.py:4035 `prescan_meta = dict(self.last_scan_meta or {})` after the hold replaced the prescan; the scan_roll comment at 3973 says 'The roll's film, so the prescan's entry says what was in the transport; it was recorded as "negative" whatever it was' (fixed for the first prescan only); demo.py:1326 selects entries by `(record.get("scan") or {}).get("film") == film`.
```

**Failure scenario:** A B&W walk is run with 'aim each frame' on, or scan_roll --approved --dry-run is run on B&W. The replacement prescans are filed as film 'negative', so library queries by film are wrong, and the demo shows B&W frames in a colour-negative strip.

**Fix:** Pass the roll's film through _hold_to_approved and _aim_frame into every prescan they take.

<details><summary>Second reader's check</summary>

The only prescan call that passes film is the first one (direct.py:3968-3974). _hold_to_approved's verification prescan at direct.py:3377 takes the default FILM_NEGATIVE (2057), and direct.py:3158 writes film into scan meta.

After a hold or aim, direct.py:4035 and 4066 replace prescan_meta with that pass's meta. On a dry run session.py:2555-2566 files it as the walk prescan's library entry, so scan.film reads 'negative' on a B&W walk.

One correction to the reader's account: for a real roll's frame entry, the film in scan.json comes from the frame scan's own meta, and the prescan block records only read_direction and carriage_state. The frame entry is therefore not mislabelled; only walk prescan entries are.

demo.py:1326 does select pools by scan.film.

</details>

<a id="framing-units-fr-04"></a>

### FR-04 -- Two frame-width models coexist; legacy 36.0 mm model (with a closure member that can only fire on misdetections) silently aims positive/Kodachrome rolls

**Severity** low · **Category** design · **Verdict** partly

**Where:** `rps7200/framing.py:3-7`, `rps7200/framing.py:586`, `rps7200/framing.py:720`, `rps7200/framing.py:740`, `rps7200/framing.py:1366-1368`, `rps7200/framing.py:1426-1434`, `rps7200/direct.py:3896`, `tools/frame_edges/propose.py:74`, `tools/gui.py:2224-2229`, `tools/scan_roll.py:387-389`

**Doc claim:** rps7200/framing.py:3-5 'The aperture is 36.5 mm and a 35 mm frame is 36 mm, so there is half a millimetre of slack' (contradicted by framing.py:1950-1957)

The legacy 36.0 mm frame model, still used when there is no edge reader, disagrees with FRAME_WIDTH_UNITS. The pieces are FRAME_WIDTH_MM (framing.py:586), TARGET_GAP_MM (740), the right-edge reading through frame_mm (720) and right_gap_closure's 36.0±0.3 mm closure test (1366-1368, 1430). The framing.py:3-7 module header still describes a 36 mm frame in a 36.5 mm aperture with half a millimetre of slack.

CLAUDE.md openly documents that rolls with no reader aim on the 36.0 mm model, so the fallback itself is disclosed. What is unguarded is the operator side. unread_at returns None for positive and Kodachrome (propose.py:74), so neither the Roll dialog (gui.py:2224-2229) nor scan_roll (387-389) says that edges are not read on this film when 'correct' is ticked. The roll silently aims with a detector calibrated on C-41 bright base.

**Evidence (from the code):**

```text
framing.py:586 `FRAME_WIDTH_MM = 36.0` (≈340 units, 422 cols) vs framing.py:1957 `FRAME_WIDTH_UNITS = 350.6` ('Wider than the aperture (428 columns), so a centred frame shows no base at all'); framing.py:740 `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0` (aims for 2.9 cols of base at the left); framing.py:720 `return int(round(end - frame_mm / mm_px))` (right-edge reading through 36.0 mm); framing.py:1368 `frame_mm: float = 36.0,` and 1430 `if abs(residual) > FRAME_WIDTH_SPREAD_MM:` (closure accepts only a 36.0±0.3 mm picture between two bands); direct.py:3896 `walk = (StripWalk(reader=edge_reader(film) if edge_reader else None)`; propose.py:286-287 `if film_type(film) is None: return None`; propose.py:74 `if film_type(film) is None or dpi is None ... return None` (no warning for positive film).
```

**Failure scenario:** The operator walks or scans a slide-film strip with 'aim each frame while prescanning' ticked. The unvalidated base detector calibrates on dark gaps. The left-gap and closure (or prior) members agree on a reading built on the 36 mm model, and the film is nudged by several units per frame towards a target about 5 units off centre. Nothing in the dialog said edges are not read on this film.

**Fix:** Refuse or warn about correct/correct_dry_run on films walk_reader does not read, as it does for unreadable dpi. Retire right_gap_closure and the FRAME_WIDTH_MM/TARGET_GAP_MM target, or derive them from FRAME_WIDTH_UNITS. Update the framing.py module header, which still describes a 36 mm frame in a 36.5 mm aperture with half a millimetre of slack.

<details><summary>Second reader's check</summary>

The code does what the finding describes: StripWalk(reader=None) falls back to frame_offset_mm, right_gap_closure and predict_offset (framing.py:1796-1802). But CLAUDE.md states this fallback explicitly ('A roll with no edge reader ... still aims on the older 36.0 mm model'), so the design is disclosed rather than hidden.

The failure scenario, in which dark slide gaps calibrate a base and produce agreeing votes that nudge the film, is speculative. film_base_from may simply fail to arm on a positive, and nothing in the code shows it arming. What is real is the missing warning or refusal for 'correct' on films the edge reader does not read, and the stale module header.

</details>

<a id="framing-units-fr-05"></a>

### FR-05 -- scan_roll --approved is not the sheet's path: ignores approved.json, does not snap, and holds nothing for frames the detector refused

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `tools/scan_roll.py:134-140`, `tools/scan_roll.py:293-297`, `tools/gui.py:6436-6452`

The --approved help text openly says positions are 'proposed for the whole strip', so ignoring approved.json matches what it advertises. The real divergences from the sheet's commission are three:
- hold_from_walk's offsets are neither snapped (snap_offset) nor clamped to MAX_TRAVEL_MM, so they can exceed one command and the ±105-column verification reach.
- Frames the detector left at 'none' get no Approved (scan_roll.py:293-297). They are not held to their survey position at all, where the sheet would hold them at 0 relative to the reference, which corrects rewind error.
- reverse_hold is never passed.
So the claim that 'what runs here is what runs there' is only approximately true.

**Evidence (from the code):**

```text
scan_roll.py:134-140 help: 'Its prescans are re-read, positions proposed for the whole strip, and each frame held to its own -- the same path the window's contact sheet drives'; scan_roll.py:293-297 `offsets, notes = frame_edges.propose_centred(frames, film=film)` / `n: Approved(number=n, offset_mm=float(offsets[n]), reference=im,` / `for n, im in frames if n in offsets`, where approved.json is never opened; gui.py:6438-6440 `out.append(Approved(` ... `offset_mm=snap_offset(offsets.get(number, 0.0)),` for every ticked frame, source 'none' where unread.
```

**Failure scenario:** The operator corrects three frames by hand in the sheet and closes the window, then runs `scan_roll.py --approved rolls/X` expecting his positions. The roll holds each frame to a fresh detector proposal instead. His three corrections, and any frame the detector refuses, are not honoured, and nothing says so.

**Fix:** Read approved.json in hold_from_walk and let its operator entries win, as _merge_kept does. Build Approved through the same snap_offset and every-ticked-frame logic, or rename and redocument the flag as 'propose afresh'.

<details><summary>Second reader's check</summary>

Checked hold_from_walk (scan_roll.py:223-307): it never opens approved.json, never calls snap_offset, and only includes `n in offsets`. The sheet builds an Approved for every ticked frame with snap_offset(offsets.get(n, 0.0)) (gui.py:6438-6440). The help text promises re-proposal rather than replaying the operator's positions, so the approved.json part is documented behaviour rather than a mismatch.

</details>

<a id="framing-units-fr-09"></a>

### FR-09 -- No finite-value guard between detector, sheet, approved.json and the mover; NaN snaps to the maximum forward move

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/frame_edges/centre.py:396-423`, `tools/frame_edges/propose.py:167-177`, `tools/gui.py:5887-5897`, `tools/gui.py:5461-5463`, `tools/gui.py:2632-2639`, `rps7200/framing.py:2021`

If a member ever returns a non-finite x, or approved.json or gui-settings.json holds NaN (hand-edited, or written by json.dumps from a NaN), decide/centring pass it through. snap_offset then turns NaN into the largest forward move, which becomes an Approved target labelled measured or operator.

**Evidence (from the code):**

```text
centre.py:401 `u = float(np.mean(points))` / 421 `if abs(u) < deadband:` / 423 `return Decision("right" if u > 0 else "left", u, ...)` (no isfinite); gui.py:5897 `want = max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, float(millimetres)))`, where Python's min(M, nan) returns M, so NaN becomes +MAX_TRAVEL_MM (88.8 units); gui.py:5462-5463 `placed = bool(record.get("offset_mm")) ...` / `offsets[number] = float(record.get("offset_mm") or 0.0)` (json.loads accepts NaN); only the unused framing.command_for checks `np.isfinite(wanted)` (framing.py:2021).
```

**Failure scenario:** A hand-edited approved.json contains "offset_mm": NaN. On reopen and commission the frame is held to +88.8 units, a quarter of the frame, without complaint.

**Fix:** Reject non-finite values in centring/decide, in snap_offset, in read_approved and in _clean_sheet_state.

<details><summary>Second reader's check</summary>

Python evaluates min(M, nan) to M, so max(-M, min(M, nan)) = M: NaN becomes +MAX_TRAVEL_MM (gui.py:5897).

Nothing checks for non-finite values on the way in:
- read_approved uses bool(nan), which is True, then float(...) (gui.py:5461-5463).
- _clean_sheet_state casts with float() (2632-2639).
- centring and decide have no isfinite check.

The realistic source is a hand-edited file, because detector outputs are built from integer base widths. Low.

</details>

<a id="framing-units-fr-10"></a>

### FR-10 -- Dead unit-based move law in framing contradicts the driver's cap and its own claims

**Severity** low · **Category** dead-code · **Verdict** confirmed

**Where:** `rps7200/framing.py:1959-1997`, `rps7200/framing.py:2000-2046`, `rps7200/framing.py:2049-2169`, `rps7200/direct.py:3572-3575`, `rps7200/direct.py:3586-3596`

framing.py carries a second, unused model of the SLIDE law: command_for with ceilings of 160 and 255, plus a 'look down the gap' band detector. That model disagrees with the live mover, whose cap of 87 is justified precisely because 160 is unverifiable. It is a second home for the transport law, the drift CLAUDE.md warns about, and it will mislead whoever reaches for it.

**Evidence (from the code):**

```text
framing.py:1962 `MAX_PARAM = 255`, 1969 `MAX_VERIFIABLE_PARAM = 160` ('The largest param whose move can still be verified'); direct.py:3592-3596 'param 160 moved 196 px at confidence **27** against a floor of 55 -- it goes somewhere and cannot say where ... MAX_CORRECTION_PARAM = 87'; framing.py:2011-2012 command_for 'the only rounding in this whole module'; grep finds no caller of command_for/describe_command/edge_band/Band/_row_runs in rps7200, tools or tests; direct.py:3575 `CORRECTION_DEADBAND_MM = 0.15` unused.
```

**Failure scenario:** A future change wires command_for into the aim loop, trusting its docstring, and single commands up to param 160 are sent that the hold loop cannot verify (confidence 27 against a floor of 55).

**Fix:** Delete command_for/describe_command/MAX_PARAM/MAX_VERIFIABLE_PARAM/LARGEST_*, edge_band/Band/_row_runs and CORRECTION_DEADBAND_MM, or wire them in with the 87 cap taken from DirectScanner.

<details><summary>Second reader's check</summary>

A grep over rps7200, tools and tests finds no caller of command_for, describe_command, framing.edge_band, Band or _row_runs. tools/roll_registration_study.py has its own edge_band. CORRECTION_DEADBAND_MM (direct.py:3575) is never read.

The dead code carries MAX_VERIFIABLE_PARAM = 160 (framing.py:1969), which direct.py:3592-3596 explicitly says is unverifiable (confidence 27 against a floor of 55).

</details>

<a id="framing-units-fr-11"></a>

### FR-11 -- Transport and geometry constants retyped in several places, one with a hair-trigger margin

**Severity** low · **Category** design · **Verdict** partly

**Where:** `rps7200/framing.py:902`, `rps7200/framing.py:1117`, `rps7200/framing.py:1151`, `rps7200/framing.py:1368`, `rps7200/framing.py:1939`, `rps7200/protocol.py:276`, `rps7200/demo.py:451`, `tools/verify_protocol.py:484`, `tools/verify_protocol.py:1203`, `tests/test_roll.py:1129-1138`

These constants have a second, typed home:
- COMMAND_COST (framing.py:1939) and COMMAND_UNITS (protocol.py:276) are both 1.84, and nothing pins them equal. The only test that names them, tests/test_roll.py:1129-1138, still says COMMAND_COST 'describes the same command with a 17% larger ramp', which is stale.
- demo.py:451 retypes 0.1057.
- framing.py:1368 retypes 36.0.
- verify_protocol.py:484 keeps the retired 0.1662 ramp, and 1203 picks a SLIDE param with the retired 1.5724.
- MAX_DY_MM uses 24.3053 rather than APERTURE_HEIGHT_MM (24.2993), leaving a 0.00004 mm margin at dy = 2 on 287 rows.
- HOLD_TOLERANCE_MM's comment gives a stale reason: framing already imports protocol.

The reader's failure scenario for MAX_DY_MM is wrong. Replacing 24.3053 with APERTURE_HEIGHT_MM would make both sides the same float expression, 2 × (A / 287), and the gate is `dy_mm > MAX_DY_MM`, so equality passes. It would not flip on rounding. HOLD_TOLERANCE_MM is pinned by tests/test_roll.py:1350 (abs = 1e-3).

**Evidence (from the code):**

```text
framing.py:902 `MAX_DY_MM = MAX_DY_PX * (24.3053 / 287.0)` while APERTURE_HEIGHT_MM = 6888*25.4/7200 = 24.2993 and 1117 `dy_mm = abs(dy) * (APERTURE_HEIGHT_MM / max(now.shape[0], 1))`, so the reversed-pass case dy=2 at 287 rows gives 0.169333 against a gate of 0.169375 (a margin of 0.00004 mm); framing.py:1151 `HOLD_TOLERANCE_MM = 0.3002` ('Duplicated rather than imported because direct imports this module', yet framing already imports protocol, where MM_PER_UNIT*(1+COMMAND_UNITS)=0.300188); framing.py:1939 `COMMAND_COST = 1.84` and protocol.py:276 `COMMAND_UNITS = 1.84` (two homes, no test pins them equal); framing.py:1368 `frame_mm: float = 36.0,` (literal, not FRAME_WIDTH_MM); gui.py:212 recomputes APERTURE_MM; demo.py:451 `swallowed = min(abs(asked), 2.2 * 0.1057)`; verify_protocol.py:484 `MM_PER_UNIT, OVERHEAD_MM = 0.1057, 0.1662` (stale ramp) and 1203 `param = min(87, max(1, int(round(left / 1.2423 - 1.5724))))` (stale 1.5724 ramp used to pick a SLIDE param).
```

**Failure scenario:** Someone 'fixes' MAX_DY_MM to use APERTURE_HEIGHT_MM, and every row-reversed prescan at 300 dpi (5 of 15 on one roll) flips between accepted and 'matched ... off the film axis' on float rounding. Separately, COMMAND_COST and COMMAND_UNITS drift apart and the detector's deadband stops matching the mover's smallest move.

**Fix:** Derive HOLD_TOLERANCE_MM, COMMAND_COST and MAX_DY_MM (with explicit headroom) from protocol and APERTURE_HEIGHT_MM. Import FRAME_WIDTH_MM and APERTURE_MM rather than retyping them. Use protocol.MM_PER_UNIT in the demo, and use protocol/framing constants in verify_protocol.

<details><summary>Second reader's check</summary>

All the retyped copies are verified in code. The claimed hair-trigger failure does not happen with the `>` comparison at framing.py:1118. The unpinned COMMAND_COST/COMMAND_UNITS pair and the stale verify_protocol ramp are the real items.

</details>

<a id="framing-units-fr-12"></a>

### FR-12 -- Stale documentation of the move law and cap across framing's consumers

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/protocol.py:290`, `tools/gui.py:5929`, `rps7200/session.py:94`, `rps7200/session.py:1208`, `rps7200/session.py:1222`, `tools/gui.py:5893`, `tools/gui.py:5050`, `rps7200/framing.py:854-855`, `rps7200/shortcuts.py:7-8`, `rps7200/shortcuts.py:148-149`, `rps7200/shortcuts.py:189-190`

Docstrings and dialogs still describe the 1.572-era ramp (2.57), the param-8 cap and eight-command chains. The Aim dialog tells the operator a click past 88.8 units needs more than 8 moves, when it needs 2. The adjuster's key label says it moves film when it only edits the sheet's target.

**Evidence (from the code):**

```text
protocol.py:290 'so `param 1` travels 2.57' (1+1.84 = 2.84); gui.py:5929 'the lattice is 2.57 units off zero'; session.py:94 'for param 1 and param 8'; session.py:1208 'for an integer param in 1..8'; session.py:1222 '`DirectScanner.param_for_mm` already clamps silently at param 8' (cap is 87); gui.py:5893 'Clamped to what eight commands can chain, which is `MAX_TRAVEL_MM`' (MAX_TRAVEL_MM = one command); gui.py:5050 Aim dialog 'would take more than {MAX_FINE_STEPS} sub-frame moves' for anything past one command; shortcuts.py:148 `Action("adjust_left", "adjuster", "Move the film one step left", "<Left>")` against its own docstring 'nothing moves film ... by key at all' (the key only changes the planned offset, gui.py:7406-7417); shortcuts.py:189-190 resolve(): 'a mistake in it should cost a key rather than the window'.
```

**Failure scenario:** An operator or maintainer trusts '2.57 units' or 'param 8' when reasoning about a deadband or cap. Or he reads 'Move the film one step left' in the shortcut editor and believes the key drives the transport.

**Fix:** Update the texts to 2.84 units, param 87 and a single command. Relabel adjust_left/right as 'Set the frame one step left/right'.

<details><summary>Second reader's check</summary>

Each stale text is in the code:
- protocol.py:290 says 'param 1 travels 2.57'; it is 2.84.
- gui.py:5929 says 'lattice is 2.57 units off zero'.
- session.py:94 and 1208 say 1..8.
- session.py:1222 says 'clamps silently at param 8'.
- gui.py:5893 says 'what eight commands can chain', but MAX_TRAVEL_MM is one command (gui.py:241).
- The Aim dialog at gui.py:5050 says 'more than {MAX_FINE_STEPS}' (8) moves for anything past one command.
- shortcuts.py:148-149 labels the adjuster keys 'Move the film one step left/right', while gui.py:7400-7417 shows _step only sets the sheet offset.

</details>

<a id="framing-units-fr-13"></a>

### FR-13 -- GUI lets a 'correct' roll run at a prescan dpi the edge reader cannot read (warning only); the CLI refuses

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2219-2229`, `tools/scan_roll.py:387-389`, `tools/frame_edges/propose.py:85-99`

At 600 or 900 dpi every frame is refused by the detector, so 'aim each frame' does nothing, as the warning itself admits. The window still allows the roll after a single OK, while the CLI treats the same combination as an error. The GUI and CLI guard the same mistake differently.

**Evidence (from the code):**

```text
gui.py:2224-2229 'A warning rather than a refusal' / `unread = (frame_edges.unread_at(predpi, self.v_film.get()) if dry or self.v_correct.get() ...)` / `question += unread`; scan_roll.py:388-389 `if unread and args.correct: ap.error(...)`; propose.py:94-95 `if w % SCALE: return None` (860/1292-column passes refused).
```

**Failure scenario:** The operator OKs past the warning text and runs a 36-frame roll at 600 dpi prescans with 'aim' on, believing frames are being centred. None are, and each frame's caption says 'left as it came'.

**Fix:** Refuse 'correct' at an unreadable prescan dpi in the window, as scan_roll does, and keep the warning for walks only.

<details><summary>Second reader's check</summary>

gui.py:2219-2229 only appends the unread_at text to the question; a single OK proceeds. scan_roll.py:388-389 calls ap.error for --correct at an unreadable dpi. propose._downscaled returns None for 860- or 1292-column passes (propose.py:94-95), so every frame is refused.

</details>

<a id="framing-units-fr-14"></a>

### FR-14 -- A malformed shortcut in gui-settings.json stops the window from opening

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/shortcuts.py:183-195`, `rps7200/shortcuts.py:236-240`, `tools/gui.py:567`, `tools/gui.py:661-662`, `tools/gui.py:902-908`

resolve() promises that a hand-editing mistake costs a key rather than the window. A syntactically bad Tk sequence such as '<Foo>' passes resolve and makes root.bind raise TclError during construction. Duplicate sequences within a scope are also not rejected on load: one action silently loses its key.

**Evidence (from the code):**

```text
shortcuts.py:193 `if action_id in keys and isinstance(sequence, str):` (any string accepted); gui.py:662 `self.keys = shortcuts.resolve(stored ...)`; gui.py:907 `self.root.bind(sequence, self._runner(run, sequence))` with no try, called from __init__ at gui.py:567; shortcuts.py:238 in_scope builds `{sequence: action_id}`, so two actions with one sequence in one scope silently keep only the last.
```

**Failure scenario:** Stefan edits gui-settings.json by hand to rebind a key and mistypes it. On the next launch the window dies with a TclError instead of dropping that one key.

**Fix:** Wrap each bind in try/except tk.TclError and fall back to the default for that action. Run conflicts() on load and drop the later duplicate with a message.

<details><summary>Second reader's check</summary>

shortcuts.resolve accepts any str (193). _bind_shortcuts calls self.root.bind(sequence, ...) with no try (gui.py:907), from __init__ at gui.py:567 after session.start(). An invalid Tk sequence raises TclError there. in_scope builds a dict keyed by sequence, so a duplicate silently drops an action (shortcuts.py:236-240).

</details>

<a id="framing-units-fr-15"></a>

### FR-15 -- Demo film movement wraps around (np.roll), so moved prescans correlate near-perfectly and edges read wrapped content

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:474-484`, `rps7200/demo.py:947`, `rps7200/demo.py:451`

The demo runs the real hold, aim and edge code, but feeds it a circularly shifted picture. After a nudge, columns leaving one edge reappear at the other instead of the gap and the neighbour frame entering. Phase correlation on a pure circular shift scores near its maximum, so the demo cannot reach the confidence loss that real moves produce. The edge reader's re-read also sees the frame's far edge wrapped next to its near edge. This is an input simplification, not an `if demo`, but it makes the demo systematically more optimistic than the hardware about verification.

**Evidence (from the code):**

```text
demo.py:947 `columns = np.roll((np.arange(w) * width) // w, self._shift(w))`; demo.py:484 `return int(round(self._film_mm / (APERTURE_MM / width)))`; demo.py:451 `swallowed = min(abs(asked), 2.2 * 0.1057)`.
```

**Failure scenario:** A change that weakens measure_shift_mm on real, partially overlapping passes still shows every frame 'held' at high confidence in make run-demo, so the regression is invisible with no scanner attached.

**Fix:** Shift with edge fill from the neighbouring strip entry (or base-level padding) rather than np.roll, and use protocol.MM_PER_UNIT for the backlash distance.

<details><summary>Second reader's check</summary>

demo.py:947 builds the column map with np.roll, so the picture wraps. demo.py:451 retypes 0.1057 instead of using MM_PER_UNIT. The real hold and aim code runs on this input (demo.py:787, 795). It is an input simplification rather than an `if demo` branch, but it makes verification optimistic.

</details>

<a id="framing-units-fr-16"></a>

### FR-16 -- combine(): tie in the 'none agree' diagnostic compares Reading objects (TypeError), and the message names the furthest pair 'closest'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/framing.py:1583-1595`, `rps7200/framing.py:1284-1306`, `rps7200/direct.py:4180-4183`

On the legacy (no-reader) path, if two pairs ever tie on the float, max() compares the Reading dataclasses and raises TypeError. That error is outside scan_roll's per-frame except list, so the roll generator ends. The ranking is also inverted: max picks the most disagreeing pair, while the log calls it the 'closest'.

**Evidence (from the code):**

```text
framing.py:1584-1588 `worst = max((abs(a.mm - b.mm) - gate(a, b), a, b) for i, a in enumerate(usable) for j, b in enumerate(usable) if i < j)`; `@dataclass(frozen=True) class Reading` (no order=True); message `f"... closest are {worst[1].source} ..."`; scan_roll catches only `(UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError)`.
```

**Failure scenario:** The failure is rare. It needs three usable members with symmetric disagreement: the roll ends mid-strip with a TypeError from inside judge. More commonly, the log misleads whoever reads why a frame was refused.

**Fix:** Use min() with key=lambda t: t[0] (the closest pair), or add a tiebreak index.

<details><summary>Second reader's check</summary>

framing.py:1584-1588 takes max over tuples (float, Reading, Reading). Reading is @dataclass(frozen=True) without order (1284), so a tie on the float compares Readings and raises TypeError. That is outside the per-frame except at direct.py:4147. Ties need exact float equality among three members, so the crash is very rare.

The message is certainly wrong: max picks the pair that disagrees most and calls it 'closest'.

</details>

<a id="framing-units-fr-17"></a>

### FR-17 -- Roll travel budget sums absolute nudge travel, which per-frame centring legitimately accumulates

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/framing.py:1160-1169`, `rps7200/framing.py:1763-1775`, `rps7200/direct.py:3480-3490`

The guard is meant to stop net creep, since sub-frame moves are invisible to the frame counter. But it counts |travel|, and it was set under the legacy detector. With the edge reader, each frame is re-centred after its advance, so a steady per-advance error re-corrected every frame adds to the sum while the net position stays centred. 12 mm is about 113 units, which is 23 frames at 5 units each or a single frame needing 80+ units followed by a couple more. After that, aiming silently switches off for the rest of the roll.

**Evidence (from the code):**

```text
framing.py:1169 `ROLL_TRAVEL_LIMIT_MM = 12.0`; framing.py:1763 `self.travel_mm += abs(float(travelled_mm))`; 1775 `return self.travel_mm + abs(offset_mm) <= ROLL_TRAVEL_LIMIT_MM`; the docstring's justification is net creep ('Fifteen frames each corrected -1.5 mm walks the strip 22 mm').
```

**Failure scenario:** On a 36-frame roll with 'aim' on, a transport whose advance runs about 4 units long per frame stops aiming around frame 28. The later frames drift, and the only notice is a log line.

**Fix:** Budget net displacement (a signed sum since the last whole-frame advance, or measured against the frame's own reference) rather than absolute travel, or re-derive the limit in units for the edge-reader path.

<details><summary>Second reader's check</summary>

StripWalk.record adds abs(travelled_mm) (framing.py:1763), and affordable compares against ROLL_TRAVEL_LIMIT_MM = 12.0, about 113 units (1775, 1169). _aim_frame stops aiming when it is exceeded (direct.py:3480-3490). The justifying comment is about net creep. Per-frame re-centring against a systematic advance error accumulates absolute travel while the net position stays centred.

</details>

<a id="framing-units-fr-18"></a>

### FR-18 -- EdgeWatch keeps a frame's error after a later successful reading, so the light can stay FAILED

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/watch.py:136`, `tools/frame_edges/watch.py:243-256`, `tools/frame_edges/watch.py:279-284`

An error is cleared only when a new prescan of that frame is added. If a frame's summary fails but its read later succeeds, or a first-pass read fails and the re-read after finish() succeeds, _errors still holds the old failure. Progress then reports FAILED and re-lists the stale error even though every frame has a reading.

**Evidence (from the code):**

```text
watch.py:136 `self._errors.pop(int(number), None)` (only in add); watch.py:244 `self._errors[number] = error`; watch.py:281-282 `else: state = FAILED if self._errors else DONE`.
```

**Failure scenario:** Every frame has a position, but the window shows a red light and 'failed on some' with a stale error line. The operator distrusts a good reading, or re-walks for nothing.

**Fix:** Pop _errors[number] whenever a read for the current version completes without error, and keep summary errors separate from read errors.

<details><summary>Second reader's check</summary>

_errors is popped only in add (watch.py:136) and in begin. _run sets _errors[number] on a failed summary or read (244), and a later successful read of the same version, such as the finish-time re-read against a larger context, never clears it. _progress then reports FAILED whenever _errors is non-empty (281-282).

Thread safety is otherwise sound. The Condition wraps an RLock, so load → begin/add re-entry is fine, and superseded work is discarded by generation and version checks.

</details>

<a id="framing-units-fr-a1"></a>

### FR-A1 -- The 'reverse' flag is honoured by one path only: sheet commissions mirror their targets, while scan_roll --approved and in-walk aiming ignore it

**Severity** low · **Category** design · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3344`, `rps7200/direct.py:3509-3514`, `tools/scan_roll.py:605-632`, `tools/gui.py:3194`

One setting, three meanings. A sheet commission from the window negates every approved target when the tick is on. The same walk held through tools/scan_roll.py --approved does not negate them, and neither does the in-walk 'aim each frame' hold. Because the hold loop verifies in picture space, the negation is the wrong behaviour in any case (see FR-01). The split also means the window and the CLI place the same frames differently for the same walk.

**Evidence (from the code):**

```text
direct.py:3509-3514 `fix = self._hold_to_approved(index, image, prescan_resolution, _Aim(offset_mm=decision, reference=image), keep_raw=keep_raw, should_stop=should_stop, source="ensemble", ...)` (no reverse); tools/scan_roll.py:605-632 s.scan_roll(... approved={n - 1: a for n, a in held.items()}, ...) with no reverse_hold; gui.py:3194 `reverse_hold=self.v_reverse.get()`.
```

**Failure scenario:** With the tick on, a roll commissioned from the sheet lands every frame at the mirror of its target. The same walk run through scan_roll --approved lands them correctly. Comparing the two outputs looks like transport nondeterminism rather than a flag.

**Fix:** Remove reverse_hold from the hold path, since the loop is closed in picture space. If a transport-sense switch is really needed, apply it to the command in nudge, consistently for every caller.

<a id="framing-units-fr-19"></a>

### FR-19 -- approved.json records only the snapped offset; the detector's reading behind a sheet proposal is not persisted

**Severity** info · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:3304-3322`, `tools/gui.py:6118-6134`, `tools/frame_edges/propose.py:147-177`

For frames placed by the sheet, the persisted record says where the frame was sent and that a detector decided it. It does not say what the detector saw: edges, units, frame_units, the member votes, or the detector's commit. On reopen the sheet deliberately re-proposes with today's code. The reasoning behind a scanned frame's position therefore cannot be audited later except by re-running the current detector on the corrected prescanNN.tif, which is not the raw input (see FR-02). The in-walk 'correct' path does persist its note in registration.correction.ensemble, so the two paths differ.

**Evidence (from the code):**

```text
gui.py:3312-3318 frames records carry only `number, offset_mm, rotation, flipped, reference_entry, source` (+as_walked); centring's note (edges x per side, width, units, columns, reason) lives only in memory (EdgeWatch / sheet); gui.py:6128-6134 a machine position is 're-read' on reopen, 'read again rather than replayed'.
```

**Failure scenario:** After a detector change, someone asks why frame 9 was held at -23 units. approved.json says 'measured', and today's detector gives a different answer, so the original edges are gone.

**Fix:** Write the centring note (edges, width, units, columns, frame_units) and library.provenance's commit beside each machine-sourced entry in approved.json.

<details><summary>Second reader's check</summary>

_write_approved records only number, offset_mm, rotation, flipped, reference_entry, source and as_walked (gui.py:3312-3322). EdgeWatch notes (edges, units, columns) exist only in memory (watch.py:268-284). The in-walk correct path does persist its note under registration.correction.ensemble (direct.py:4057-4058 via _aim_frame's 'ensemble' key), so the two paths differ as described.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Commissioned frame positions and turns | rolls/<roll>/approved.json (+ approved.json.bak) | JSON {roll, numbering, frames:[{number (1-based strip place), offset_mm (float mm, round 4 dp, lattice-snapped), rotation, flipped, reference_entry (library path str), source (operator\|measured\|unconfirmed\|neighbours\|none), as_walked?}]} | derived decision (not pixels); distances in mm | tools/gui.py:_write_approved (3265-3341) via session.write_manifest (atomic replace, keep_previous .bak) | tools/gui.py:read_approved (5436-5475) on reopen/Export; NOT read by tools/scan_roll.py --approved | Lossless for the snapped value (0.0001 mm vs a 0.1057 mm lattice). Omits the detector note, unit model and commit (FR-19). Accepts NaN on read (FR-09). |
| Per-frame framing record of a walk/roll | rolls/<roll>/survey.json (walk) and rolls/<roll>/roll.json (scan), frames[].registration | JSON dict. framing.registration(): x0,x1,width,offset,shortfall,margin in 1/7200-inch units plus *_mm rounded to 0.01. contrast (4 dp). base (reader observe dict or legacy base detail). approved (hold: target_mm, outcome, moves, spent_mm, history[] of measure_shift_mm detail {confidence 2dp, dy, dx, px, resampled, row_reversed, confidence_other, reason}, final_mm, residual_mm, clamped, source, reverse_applied, roll_abort). correction (decision_mm, ensemble note incl. edges/units/columns/width or legacy members, would_send, outcome, moved) | measurements derived from corrected 8-bit prescans | rps7200/session.py roll loop record_of.record (2640-2690); tools/scan_roll.py manifest; content built in direct.scan_roll (3979-4083), _hold_to_approved, _aim_frame | tools/gui.py read_survey (5104-5220) into Result.registration; _report_held; session.walked_prescans | Numbers rounded as noted, and the images behind history[] are not kept (FR-02). Mixed units: scanner coordinates, mm and 2-dp confidences. |
| Walk prescans used as sheet input and hold references | rolls/<roll>/prescanNN.tif | TIFF, 8-bit RGB (428x287 at 300 dpi), turned/mirrored per record prescan_rotation/prescan_flipped | corrected (that day's shading), arranged | rps7200/session.py _file(path=prescanNN.tif) on dry runs (2549-2566) via FrameWriter; tools/scan_roll.py 659 | tools/gui.py read_survey -> preview.unorient -> EdgeWatch.load and Approved.reference; tools/scan_roll.py hold_from_walk -> propose_centred | Pixel-exact to what was corrected then. Not raw; the raw is in the prescan's library entry (dry runs only). |
| Pre-correction prescan of an aimed walk frame | rolls/<roll>/prescanNN-before.tif | TIFF, 8-bit RGB | corrected, no library entry, no raw bytes | rps7200/session.py 2567-2590 (_file file_entry=False), dry runs with correct only | nothing in code (evidence for a person) | Not raw. Absent entirely on real rolls and on the approved-hold path (FR-02). |
| Framing record inside library entries | library/<entry>/scan.json 'registration' (frame entries) | JSON copy of RollFrame.registration (marks) as above | derived | rps7200/library.py save (line 359 `"registration": meta.get("registration")`) from session frame meta | tools/registration_margin.py and offline analysis | As in survey/roll.json. Walk prescan entries carry no registration (prescan_meta has none). |
| Frame entry's framing pass | library/<entry>/prescan.tif (+ scan.json prescan{file, read_direction, carriage_state}) | TIFF 8-bit RGB | CORRECTED pixels of the last (post-hold/aim) prescan, unlabelled | rps7200/library.py:269-270 from session._file(prescan=rf.prescan) (session.py:2627) | library.fix_orientation (788-890), demo pairs | Not raw and not labelled as corrected. Raw prescan bytes dropped (FR-02). Film label may be wrong after a hold (FR-08). |
| Sheet decisions and transport controls | gui-settings.json: sheet[<roll>] {ticks, offsets(mm float), rotations, flips, sources, options}; controls reverse/correct/fine/aim/adjuststep; shortcuts overrides | JSON | operator/detector decisions, mm | tools/gui.py _store_sheet_state (2666-2676), _remember; shortcuts.overrides_from | tools/gui.py _recall_sheet_state/_clean_sheet_state (2617-2690), __init__ (655-663) | Floats exact in JSON. The 'reverse' control persists and silently affects rolls (FR-01). Malformed shortcut strings break startup (FR-14). |
| EdgeWatch readings (offsets, notes with edges) | (memory only) | Progress{offsets mm, notes{source, reason, edges, width, units, columns}} | derived from corrected prescans | tools/frame_edges/watch.py _run | tools/gui.py _edges_changed -> sheet.take_readings | Not persisted; re-derived on every reopen with the current code. |

**Second reader's corrections to this table:**

1. Frame entry `prescan.tif` film label: FR-08 does not reach the frame entry. Its scan.json film comes from the frame scan's own meta (session.py:2622-2626), and the prescan block stores only file, read_direction and carriage_state (library.py:377-382). The mislabelled film='negative' lands on walk prescan library entries (dry runs, session.py:2555-2566) whose prescan was replaced by a hold or aim pass.
2. Missing row: reverse_hold is persisted in rolls/<roll>/roll.json and survey.json under settings.reverse_hold (session.py:2445). It is read back into the window's 'reverse' control through RESTORABLE and restorable() (gui.py:5243, 5296-5320), and in gui-settings.json as the 'reverse' control (gui.py:141). Per frame, registration.approved.reverse_applied and target_mm, already negated, are written (direct.py:3346-3347). approved.json's offset_mm is the un-negated sheet value, so the two files disagree in sign whenever reverse was on.
3. Walk prescan library entries (dry runs): the raw bytes and meta filed are those of the last pass, the post-hold or post-aim replacement (direct.py:4034-4035, 4065-4066). They are consistent with prescanNN.tif, but the arrival pass is not stored raw anywhere unless debug filing is on.
4. prescanNN-before.tif is written only on a dry run that took the correct/aim branch, never on the approved-hold branch and never on a real roll (session.py:2536, 2567). The claimed table says this; the 'dry runs with correct only' wording is right.
5. tools/scan_roll.py --approved reads rolls/<roll>/survey.json or roll.json and prescanNN.tif via walked_prescans and prescan_arrangement (scan_roll.py:262-283). It never reads approved.json; the table states that correctly.

## What the operator can do

- Walk a strip (dry run) at any prescan dpi. Frame edges are read only on 300 dpi (428-column) negative/B&W prescans, and other dpi get a warning before the walk.
- Open the contact sheet while the edges are still being read, and watch positions and edge lines update until the light goes green.
- Set a frame's position in the adjuster (drag, arrow steps, 'Centre' = as surveyed, reset to the detector). Positions are snapped to the SLIDE lattice and clamped at 88.8 units.
- Commission 'Scan chosen frames'. Every ticked frame gets an Approved (the operator's, the detector's, or 0 'none'), approved.json is written, and the roll holds each frame to it.
- Tick 'aim each frame while prescanning' (correct) or its dry-run variant on any film and any prescan dpi.
- Tick 'reverse the direction' in the Transport panel. It is remembered across launches and also applies to commissioned rolls.
- Reopen a roll. Positions are re-proposed from prescanNN.tif with today's detector; stored operator positions win.
- Run tools/scan_roll.py --approved <walk> (re-proposes and holds), --correct (refused at unreadable dpi) or --correct-dry-run.
- Edit shortcuts in the editor, or hand-edit gui-settings.json and approved.json.

## What the operator should not do

- Leave 'reverse the direction' ticked when commissioning a roll: every approved position is mirrored.
- Use 'aim each frame' on positive or Kodachrome film: it runs the legacy negative-only base detector on the disproven 36 mm model.
- Walk at 600 or 900 dpi and expect proposals, or run a correct roll there.
- Set or accept offsets beyond about 84.7 units: the hold loop cannot verify moves larger than its 105-column search.
- Commission while the edge light is still blue: unread frames are held at 'as surveyed' and later readings do not reach the running roll.
- Expect scan_roll --approved to use the positions and turns set in the sheet (approved.json is ignored).
- Hand-edit shortcut sequences or offset values: a malformed sequence stops the window from opening, and NaN becomes a maximum move.
- Start a roll over a strip with a blank or unexposed frame in it and expect the rest to be scanned: it ends there as 'end of film'.

## Mistakes nothing guards against

- The 'reverse the direction' checkbox silently negates all hold targets of a commissioned roll. It is not shown in the confirm dialog, and the wrong_way check cannot see it (gui.py:3194, direct.py:3344).
- correct/aim on non-negative film runs the legacy detector with no warning (propose.py:74 returns None for such films; direct.py:3896).
- The GUI allows a correct roll at an unreadable prescan dpi after a single OK (gui.py:2224-2229), while the CLI refuses the same thing.
- An operator or detector offset of 85-88.8 units is accepted by snap_offset, but the verification cannot see it (framing.py:857, gui.py:5897).
- A blank frame mid-strip ends a walk or roll with only a log line (direct.py:3987-3993).
- scan_roll --approved on a folder whose sheet positions were hand-set discards them without saying so (scan_roll.py:293-297).
- A NaN in approved.json or gui-settings.json offsets becomes +88.8 units (gui.py:5897, 5463).
- A malformed shortcut string in gui-settings.json raises TclError at startup (gui.py:567, 907).
- Hold/aim prescans and the pre-move prescan are not filed raw unless RPS7200_DEBUG=1, so the evidence for every move is lost by default (FR-02).

## Dataflow notes

Data enters as prescans:
- DirectScanner.scan_roll (rps7200/direct.py:3969-3976) takes a 300-dpi 8-bit RGB prescan, corrected by shading and upright from its line tags. framing.frame_contrast and framing.registration (framing.py:51-72, 230-273) turn it into `marks`. A contrast below 0.02 ends the roll (direct.py:3987).
- With aiming on, StripWalk.observe (framing.py:1671-1711) runs. With the window's or CLI's edge_reader it calls WalkReader.observe -> propose.summary -> roll.summarise (chroma/gapmodel summaries per frame). Otherwise it uses the legacy edge_bands/film_base_from.
- An approved frame goes to _hold_to_approved (direct.py:3306-3432): target = offset_mm, negated if reverse_hold. measure_shift_mm (framing.py:1007-1125) phase-correlates the reference prescan against the current one within ±SEARCH_MM (105 px at 300 dpi); hold_plan (1172-1213) sizes a residual in mm; nudge (direct.py:3611-3642) converts it with param_for_mm = round((|mm| - 0.1057*1.84)/0.1057), capped at 87, into SLIDE 00/01 <param> 00 04. The loop re-prescans up to 3 times.
- An unapproved frame with correct on goes to _aim_frame (3434-3532): StripWalk.judge -> WalkReader.judge -> propose.detect (vote of changepoint/chroma/stepline/gapmodel on the 428-column float32 image, members failing closed via vote._member) -> centring -> decide (centre.py:358-423). decide works in units via units_per_column = 428/w/1.2423, with deadband SMALLEST_MOVE 2.84 and agreement within 2 units. The answer goes back to columns and then to mm via columns*APERTURE_MM/width (centre.py:323-325), and is delivered by the same hold loop with a rejudge that may only stop.
- RollFrame yields the final prescan (the pre-move one is dropped except as prescan_before on the correct path).

The session and window:
- The ScanSession roll loop (session.py:2518-2690) delivers a working copy (preview.downscale ≤1400 px) to the window and files it. On a dry run that means prescanNN.tif (corrected, arranged) plus a library entry with raw. On a real roll the prescan rides along in the frame entry as a corrected prescan.tif without raw. The per-frame registration goes into survey.json/roll.json.
- The window adds each walked prescan to EdgeWatch (gui.py:3914). The EdgeWatch thread (watch.py:214-257) summarises every frame, reads each against the others' summaries, re-reads after finish(), and fills refused frames with framing.fill_from_neighbours (a Theil-Sen line over frame numbers).
- _pump -> _edges_changed -> sheet.take_readings -> _snap_proposals/snap_offset (gui.py:6087-6110, 5887-5909) puts proposals on the reachable lattice, capped at 88.8 units. The operator overrides in the adjuster (offset in mm).
- on_scan_chosen builds Approved(offset_mm=snap_offset(...), reference=result.image, source) for every ticked frame (gui.py:6436-6452), writes approved.json (3265-3341) and submits Roll(approved, reverse_hold=v_reverse). The session keys it {number-1: Approved} for the driver (session.py:2511).
- On reopen, read_survey (gui.py:5104-5220) un-orients prescanNN.tif, EdgeWatch.load re-reads with today's code, and stored operator positions win (_merge_kept).
- The CLI path tools/scan_roll.py hold_from_walk (223-307) calls propose_centred directly and builds unsnapped Approved only for frames with offsets.

What leaves this area as persisted output:
- approved.json offsets (mm).
- registration dicts (mm and scanner units) in manifests and scan.json.
- Corrected prescan TIFFs.

Units: the detector's unit (1.2423 columns) and the mover's (0.1057 mm) differ by 0.2%, bridged by APERTURE_MM/width. shortcuts.py only maps keys to window callbacks: no key reaches calibrate, nudge, move or abort (NEVER_BOUND), and the adjuster arrows only edit the planned offset (gui.py:7406-7417).
