# Framing and transport units

Area key `framing-units`. 21 findings: 1 high, 9 medium, 11 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Framing/units area: rps7200/framing.py (legacy strip detectors, hold loop maths, measure_shift_mm, unit constants, unused command law), tools/frame_edges/* (the study detector: detect/vote/centring/decide, WalkReader, EdgeWatch background reader), rps7200/shortcuts.py, and how direct.scan_roll, demo.scan_roll, session._roll and the GUI sheet/adjuster use them.


What works: EdgeWatch is thread-safe as written. It uses one worker, a Condition wrapping an RLock, generation and version checks that discard superseded work, and per-frame error capture. StripWalk keys everything by frame number. The sign conventions agree between decide, centring, measure_shift_mm and hold_plan. The hold loop and the detector share one scale (APERTURE_MM/width).


The main problems:
1. Framing decisions cannot be recomputed from the library. Prescans in a real roll are kept only as corrected 8-bit TIFFs with no raw bytes. The prescan the frame arrived with, and the hold loop's intermediate prescans, are not kept at all unless RPS7200_DEBUG is on.
2. Orientation of the stored prescanNN.tif is not recorded per file. `tools/scan_roll.py --approved` does not un-orient it at all, and rotating during a walk makes the survey and the manifest disagree.
3. An operator's explicit zero ("as surveyed") position is dropped on reopen and replaced by the detector's proposal.
4. 600/900 dpi prescans, both offered in the GUI, are silently refused by the edge detector, although its docstring says they are handled. The code path meant for them computes wrong, sign-inverted moves if it is ever reached.
5. Two contradictory frame-geometry models live side by side: a 36 mm frame narrower than the aperture (legacy path), and 350.6 units, about 37.1 mm, wider than the aperture (frame_edges).
6. SEARCH_MM (9.0 mm) is below the largest single command (9.39 mm) that the sheet can ask for, so the largest moves cannot be verified.
7. The demo roll pins prescans to 300 dpi and skips several of the driver's framing steps.
8. Millimetres are still the internal and persisted unit everywhere, and the CLI prints and accepts mm.
9. Several constants have two homes, three unit scales coexist, and a lot of framing code is dead, including a second 'command law' whose cap contradicts the driver's.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [FU-01](#framing-units-fu-01) | high | data-integrity | Framing inputs are not stored exactly: roll prescans have no raw bytes; arrival and intermediate hold prescans are discarded | [P08](../problems/P08-passes-never-filed.md) |
| [FU-02](#framing-units-fu-02) | medium | data-integrity | prescanNN.tif orientation is not recorded per file; rotating or flipping during a walk corrupts the stored walk and duplicates survey entries | [P20](../problems/P20-rotation-during-walk.md) |
| [FU-03](#framing-units-fu-03) | medium | bug | tools/scan_roll.py --approved uses prescanNN.tif without un-orienting it | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FU-04](#framing-units-fu-04) | medium | bug | An operator's explicit 'as surveyed' (zero) position is lost on sheet or roll reopen and replaced by the detector's proposal | [P22](../problems/P22-contact-sheet-state.md) |
| [FU-05](#framing-units-fu-05) | medium | doc-mismatch | 600/900 dpi prescans (offered in the GUI) are silently never read by the frame-edge detector; docstring says they are | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FU-07](#framing-units-fu-07) | medium | design | Two contradictory frame-geometry models coexist; the legacy path aims to a 36 mm frame that the current measurement says is wrong | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FU-08](#framing-units-fu-08) | medium | bug | SEARCH_MM (9.0 mm) is smaller than the largest single command the sheet can ask for (param 87, 9.39 mm), so such holds can never verify | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FU-09](#framing-units-fu-09) | medium | demo-divergence | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in its framing steps | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [FU-A1](#framing-units-fu-a1) | medium | doc-mismatch | GUI and scan_roll hard-code debug=False, so RPS7200_DEBUG cannot rescue arrival or intermediate prescans, contrary to the _file docstring and CLAUDE.md | -- |
| [FU-A2](#framing-units-fu-a2) | medium | data-integrity | CLI dry-run walk (tools/scan_roll.py --dry-run) files no library entry for its prescans; the hold references for --approved exist only as corrected TIFFs | [P08](../problems/P08-passes-never-filed.md) |
| [FU-06](#framing-units-fu-06) | low | bug | centring() applies decide() at 428-column width to edges already scaled to the full width: wrong, sign-inverted moves for any exact-multiple width | -- |
| [FU-10](#framing-units-fu-10) | low | doc-mismatch | Millimetres remain the internal, persisted and CLI unit for transport distances despite the prohibition | -- |
| [FU-11](#framing-units-fu-11) | low | design | Constants with two homes and three coexisting unit scales | -- |
| [FU-12](#framing-units-fu-12) | low | dead-code | framing.command_for is an unused second command law whose 'verifiable' cap (160) contradicts the driver's measured cap (87) | -- |
| [FU-13](#framing-units-fu-13) | low | bug | registration() asserts 'offset 0, short by 0' when film_bounds abstains (most real prescans); logged and persisted every frame | -- |
| [FU-14](#framing-units-fu-14) | low | error-handling | Detector exceptions other than ValueError abort a whole roll; combine() can raise TypeError on a tie | -- |
| [FU-15](#framing-units-fu-15) | low | user-error | Shortcut settings: a malformed hand-edited sequence prevents the window opening after the scanner thread has started; conflicts are unchecked on load; misleading adjuster label | -- |
| [FU-16](#framing-units-fu-16) | low | doc-mismatch | Stale statements about the command law, caps and widths in docs and docstrings | -- |
| [FU-17](#framing-units-fu-17) | low | design | Refused frames get positions extrapolated from the strip's line with no bound | -- |
| [FU-18](#framing-units-fu-18) | low | bug | 'N frames in a row did not reach the position' counts misses across in-place and abstained frames | -- |
| [FU-A3](#framing-units-fu-a3) | low | bug | Scan reversal check ignores the aim loop's row_reversed; only the approved hold's is passed | -- |

## Findings in full

<a id="framing-units-fu-01"></a>

### FU-01 -- Framing inputs are not stored exactly: roll prescans have no raw bytes; arrival and intermediate hold prescans are discarded

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:1804-1853`, `rps7200/session.py:1880-1893`, `rps7200/session.py:1265-1271`, `rps7200/direct.py:2908`, `rps7200/direct.py:3549-3558`, `rps7200/direct.py:3578-3590`, `tools/scan_roll.py:353`, `tools/scan_roll.py:495-508`, `rps7200/library.py:161-163`

Every framing input except the kept prescan of a GUI dry run is lost or stored only in corrected form. Real rolls from the GUI and the CLI keep the last hold or aim prescan of each frame only as the corrected prescan.tif inside the frame entry, with no raw bytes. The arrival prescan and intermediate verification prescans are never written. The GUI and scan_roll hard-code debug=False, so RPS7200_DEBUG cannot rescue them either. A GUI dry run files the kept prescan with its raw bytes, but prescanNN-before.tif has no entry and carries the replacement pass's meta. A CLI dry run files no library entry for any prescan. As a result, approved/correction histories (confidence, dx, dy, row_reversed per pass) and frame-edge proposals cannot be recomputed from raw data.

**Evidence (from the code):**

```text
session.py:1804 `if job.dry_run:` is the only branch that passes `raw_image=rf.raw_prescan,` (1830). For a real roll the frame is filed with `raw_image=rf.raw_image, prescan=rf.prescan,` (1886-1887) and the prescan's raw is dropped. library.py:161-162: "`prescan.tif` has no raw bytes of its own". direct.py:2908 `out["prescan"] = image` overwrites each hold pass, so only the last survives. direct.py:3553 `prescan_image = fix["prescan"]` in the approved branch keeps no copy of the arrival prescan. Only the correction branch sets `prescan_before = prescan_image` (3583), and session files it only inside the dry-run block, with `dict(rf.prescan_meta ...)` (the replacement pass's meta) and `file_entry=False` (1834-1853).
```

**Failure scenario:** A 38-frame commissioned roll holds each frame to its sheet position, and two moves each. Months later the frame-edge detector or `register` is improved and the owner wants to re-evaluate whether each hold landed. The library has one corrected 8-bit prescan per frame, the last one. The arrival pass and the second pass are gone, and the kept prescan's raw bytes were never stored. The hold records cannot be re-derived, and the question cannot be answered.

**Fix:** Pass `raw_image=rf.raw_prescan` (and the capture record) for the frame's prescan in real rolls too, and store it as its own library entry or as prescan raw beside the frame. In `_hold_to_approved` and `_aim_frame`, keep every pass: the arrival prescan and each verification prescan, with raw bytes and meta, as a list on RollFrame, and file them all. File prescan_before in real rolls as well, with its own meta and not the replacement's.

<details><summary>Second reader's check</summary>

The code does what the finding says. session.py:1804 `if job.dry_run:` is the only path that passes `raw_image=rf.raw_prescan` (1830). The real-roll frame is filed at 1880-1893 with `raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta`, so the prescan's raw is dropped even though DirectScanner.scan_roll yields it (direct.py:3658). direct.py:2908 `out["prescan"] = image` overwrites each hold pass. The approved branch (3553) keeps no arrival copy. Only the aim branch sets `prescan_before` (3583), and session files it only when dry_run, with `dict(rf.prescan_meta ...)` (the replacement pass's meta) and `file_entry=False`. The finding understates one part. The 'unless debug filing is on' escape does not exist on the operator's paths: session.py:1265-1271 builds `DirectScanner(verbose=..., debug=False)`, which overrides RPS7200_DEBUG, and tools/scan_roll.py:353 does the same. On top of that, a CLI dry run (scan_roll.py:495-508) writes prescanNN.tif and prescanNN-before.tif with plain `tiff.write` and files no library entry at all.

</details>

<a id="framing-units-fu-02"></a>

### FU-02 -- prescanNN.tif orientation is not recorded per file; rotating or flipping during a walk corrupts the stored walk and duplicates survey entries

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `tools/gui.py:3498-3505`, `tools/gui.py:3911-3923`, `tools/gui.py:719-723`, `tools/gui.py:744`, `rps7200/session.py:1687`, `rps7200/session.py:2010-2029`, `tools/gui.py:4712-4740`

The rotate, flip and contact-sheet keys all work while a dry-run walk is running. The docstrings encourage it: 'rotating a prescan is how you say which way up the film is'. Each rotate or flip of a walked prescan during the walk does three things. (1) It appends the same Result to `self.survey` again, so the sheet gets duplicate cells. (2) It bumps the frame's version in EdgeWatch, so the frame is re-read for nothing. (3) It changes session.rotation mid-roll: prescanNN.tif files written afterwards carry the new orientation, while survey.json keeps the old one. On reopen, those later references are un-oriented wrongly. At 90/270 degrees the detector refuses them (287 columns is not a multiple of 428). At 180 degrees or flipped, the left and right edges swap, so the proposed offset's sign is inverted. `measure_shift_mm` against a mirrored reference falls below the floor, so every such frame reads 'unverified' and is not corrected.

**Evidence (from the code):**

```text
gui.py:3503-3505 `if self._surveying and result.kind == "prescan" and result.number: self.survey.append(result); self.edge_watch.add(result.number, result.image)` runs on every `remember_arrangement`, which `_carry` (rotate/flip, 3922-3923: `self.session.rotation = result.rotation ... self.remember_arrangement(result)`) and the sheet's turns call. session.py:1687 records `"rotation": self.rotation` once, at roll start. `_orientation_for` (2029) writes each prescanNN.tif with the session's *current* `self.rotation, self.flip`. `read_survey` un-orients every file with the manifest's single pair: `image=preview.unorient(image, turn, mirrored)` (4740).
```

**Failure scenario:** The operator starts a 36-frame walk. At frame 3 he sees the film is mirrored and presses Cmd-M. Frames 4-36 are written flipped, and the manifest says flipped=false. Survey entry 3 appears twice. Next day he reopens the roll: frames 4-36 come back mirrored. The sheet proposes positions with inverted sign and draws edges on the wrong side. The commissioned roll reports 'unverified' for them and scans them uncorrected.

**Fix:** Record rotation and flip per prescanNN.tif in each frame record of survey.json, or write prescanNN.tif un-oriented as the scanner's own orientation, since it is a reference and not a deliverable. In `remember_arrangement`, append to the survey and call `edge_watch.add` only when the result is new (for example, key by seq), not on every re-arrangement.

<details><summary>Second reader's check</summary>

gui.py:3503-3505 appends to `self.survey` and calls `edge_watch.add` on every `remember_arrangement` while `_surveying` is true, for any prescan result. This method is called from the arrival path (3496), from `_carry` (3924, the rotate/flip keys) and from the sheet's turn (7677). The sheet can be opened mid-walk because `self.survey` is non-empty. `_ContactSheet.__init__` does not deduplicate (7153 `self.frames = [r for r in frames if r.image is not None]`), so a re-arranged frame gets a duplicate cell. EdgeWatch.add bumps the version (watch.py:116-118), which forces a re-read. `_carry` sets `self.session.rotation/flip` (3920-3921). `_file` computes the orientation at call time via `_orientation_for`, and prescans use the session pair (session.py:2029, 2106). The manifest records `rotation`/`flipped` once at roll start (1687-1688, 1732-1733), and read_survey un-orients every file with that single pair (gui.py:4721-4722, 4740). One mitigation: the library entry of each dry-run prescan does record its own `rotation`/`flipped` in meta (session.py:2110), but read_survey never consults it.

</details>

<a id="framing-units-fu-03"></a>

### FU-03 -- tools/scan_roll.py --approved uses prescanNN.tif without un-orienting it

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/scan_roll.py:243-258`, `tools/gui.py:4740`, `rps7200/session.py:2026-2029`

`hold_from_walk` claims to be 'the window's contact-sheet path with the window taken off', but it skips the un-orient step. A walk made in the window with any session rotation or flip therefore feeds rotated or mirrored prescans to the frame-edge detector, and hands them to the hold loop as references. With 90/270 degrees, every frame is refused (the width is not a multiple of 428). With a flip or 180 degrees, the offsets have inverted sign and the reference cannot correlate. It also does not snap offsets, and reads the prescan resolution from `--prescan-dpi` rather than from the manifest.

**Evidence (from the code):**

```text
scan_roll.py:243 `frames = [(number, tiff.read(str(path))) for number, path, _ in walked_prescans(folder, manifest, say=print)]` then 252 `offsets, notes = frame_edges.propose_centred(frames, film=film)` and `Approved(number=n, offset_mm=float(offsets[n]), reference=im, ...)`. The window's reader does `image=preview.unorient(image, turn, mirrored)` (gui.py:4740) because 'prescanNN.tif is written turned the way the screen had it'.
```

**Failure scenario:** A walk is made in the GUI with the strip loaded mirrored and 'flip' set. `uv run python tools/scan_roll.py --approved rolls/X` prints 'holding 30 frame(s)'. Every hold then reads 'unverified' and nothing is corrected. The manifest's `held.offsets` records sign-inverted proposals as if they were measured.

**Fix:** Share one reader: have `hold_from_walk` go through the same un-orient (`manifest rotation/flipped`) as `read_survey`, and take `prescan_resolution` from the manifest, refusing a conflicting --prescan-dpi.

<details><summary>Second reader's check</summary>

scan_roll.py:243-245 reads `tiff.read(str(path))` and passes the result straight to `propose_centred` (252) and into `Approved(reference=im)` (254). There is no `preview.unorient`, and the file has no reference to manifest rotation or flipped. The window's reader un-orients (gui.py:4740), and session writes GUI-walk prescans oriented by the session pair (session.py:2029, 2106). The prescan resolution comes from `args.prescan_dpi` (scan_roll.py:320, 462), not from the manifest. The offsets are not passed through snap_offset. Impact requires a GUI walk made with a non-default session rotation or flip. That is plausible, because any rotate or flip of any pass carries over to the session (`_carry`). CLI walks write unoriented prescans (tiff.write at 501), so they are unaffected.

</details>

<a id="framing-units-fu-04"></a>

### FU-04 -- An operator's explicit 'as surveyed' (zero) position is lost on sheet or roll reopen and replaced by the detector's proposal

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:4963-4964`, `tools/gui.py:5510-5534`, `tools/gui.py:6716-6721`, `tools/gui.py:7203-7205`, `tools/gui.py:5835`, `rps7200/session.py:790-795`

Since the sheet pre-fills every frame with the detector's reading, 'absent' now means 'use the detector'. Zero offsets are dropped in three places: sheet.offsets, sheet_state/gui-settings, and read_approved. The 'operator' protection lives only in `proposals`, which is rebuilt from offsets on reopen. So when the operator rejects a machine proposal by pressing 'Put it back where it was surveyed' (Cmd-C), his decision survives only while that sheet window lives. Closing and reopening the sheet, or reopening the roll, applies the EdgeWatch reading again. Relatedly, `approved_from_sheet` defaults `source` to 'operator' for frames the reader has not placed yet (5835). A roll commissioned while the edge light is still blue therefore logs 'operator +0.0 units' for frames he never touched.

**Evidence (from the code):**

```text
Adjuster `_set`: `if value: self.sheet.offsets[self.number] = value else: self.sheet.offsets.pop(self.number, None)` then `proposals[...] = {"source": "operator"}` (6716-6721). `read_approved`: `if record.get("offset_mm"): offsets[number] = ...` (4963), a truthiness test, although the same function uses `is not None` for rotation 'because an explicit zero is a decision'. `_merge_kept` loops only `for number, value in kept.items():` (5520), so a `sources` entry without an offset is ignored. gui.py:7203-7205: 'unlike `offsets`, where an explicit zero and an absent entry mean the same thing'. session.py:794: zero '... is not the same as having no approval at all'.
```

**Failure scenario:** The detector proposes -14 units for frame 5, which the operator can see is a silhouette and not base. He presses Cmd-C (0, 'you set this one') and closes the sheet to check something. He reopens it, or reopens the roll the next day to 'continue this roll'. Frame 5 shows -14 units again, labelled 'measured'. He commissions, and the film is moved 14 units the wrong way.

**Fix:** Store explicit zeros: keep offsets with source 'operator' even at 0.0, and use `is not None` in read_approved. Have `_merge_kept` treat any number whose recorded source is 'operator' as kept, with its offset defaulting to 0.0. Leave `source` None rather than 'operator' for frames with no note.

<details><summary>Second reader's check</summary>

Adjuster `_set` (gui.py:6706-6723) pops a zero offset and records `source: operator`. `state()` saves `sources` including that operator entry (7903-7909). But `_open_sheet` calls `_merge_kept({}, {}, offsets, sources)` (2201), which only iterates `kept.items()` (5520), so an operator source without an offset produces no note. `take_readings` then applies the detector to any frame whose proposal source is not 'operator' (7832-7833). `read_approved` uses a truthiness test `if record.get("offset_mm"):` (4963), while `_write_approved` does write zeros (2949). `approved_from_sheet` defaults `source` to 'operator' when the frame has no note (5835). The failure scenario (Cmd-C, close, reopen, detector value back) follows directly from this code.

</details>

<a id="framing-units-fu-05"></a>

### FU-05 -- 600/900 dpi prescans (offered in the GUI) are silently never read by the frame-edge detector; docstring says they are

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/frame_edges/propose.py:14-17`, `tools/frame_edges/propose.py:51-65`, `tools/frame_edges/propose.py:95-98`, `tools/gui.py:124`, `rps7200/demo.py:905-909`, `README.md:447-448`

Real 600 and 900 dpi prescans are 860 (or 862) and 1292 columns wide. Neither is a multiple of 428, so `detect` returns REFUSE on both sides for every frame, and `summary` returns None. The whole walk ends with no proposals: fill_from_neighbours needs 3 placed frames. With `correct` on, `_aim_frame` abstains on every frame. The GUI offers exactly these resolutions and the docstring promises they work. The only signal is a 'not a multiple' note per frame. README.md:447 also says a 300 dpi prescan is 431 wide, while the detector requires exactly 428 (PRESCAN_COLUMNS). If that doc were right, nothing would be read at all.

**Evidence (from the code):**

```text
propose.py:15-16: 'a pass whose width is a whole multiple of it (600, 900 dpi) is averaged down first and its positions scaled back'. `_downscaled`: `if w % SCALE: return None` (60). demo.py:907-908: 'the widths the scanner reports are 428, 860, 1292, 2584, 5172 for 300 to 3600 dpi'. README.md:448 says 600 dpi is 862 wide. gui.py:124 `PRESCAN_LADDER = (300, 600, 900)`.
```

**Failure scenario:** The operator walks at 600 dpi for a sharper sheet. EdgeWatch finishes green and 'not placed' for all frames. The sheet shows no positions, the commissioned roll holds every frame to 'as surveyed', and no centring happens. He has no reason to connect this to the prescan dpi.

**Fix:** Either resample non-multiple widths to 428 (area-average with a fractional factor) and map positions back by width/428, or remove 600/900 from PRESCAN_LADDER and warn when a walk is started at them with correction or proposals expected. Fix the docstring and the README width table.

<details><summary>Second reader's check</summary>

`_downscaled` returns None when `w % SCALE` (propose.py:60), and SCALE = 428. `detect` then refuses both sides (95-98), and `summary` returns None (108-110). The module docstring (14-17) claims 600 and 900 dpi are 'averaged down first', and the test test_a_600_dpi_prescan... (tests/test_frame_edges.py:190) uses an 856-wide kron image, which is not a real 600 dpi width. demo.py:907-908 records real widths of 428, 860 and 1292. uniformity.py:448 mentions a 600 dpi pass 862 wide with 860 masked pixels. README.md:447-449 gives 431/862/2586. None of these widths is a multiple of 428 except 428 itself. gui.py:124 `PRESCAN_LADDER = (300, 600, 900)` is offered at 1335 and 7375. With every frame refused, fill_from_neighbours (least=3) fills nothing. Note that README's 431 for 300 dpi conflicts with the detector's hard 428 requirement and with demo.py's 428.

</details>

<a id="framing-units-fu-07"></a>

### FU-07 -- Two contradictory frame-geometry models coexist; the legacy path aims to a 36 mm frame that the current measurement says is wrong

**Severity** medium · **Category** design · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `rps7200/framing.py:3-7`, `rps7200/framing.py:42`, `rps7200/framing.py:285-290`, `rps7200/framing.py:586`, `rps7200/framing.py:740`, `rps7200/framing.py:1281`, `rps7200/framing.py:1368`, `rps7200/framing.py:1957`, `rps7200/direct.py:3419-3423`, `tools/frame_edges/propose.py:39`

The legacy StripWalk detector aims each frame to leave TARGET_GAP_MM (about 0.25 mm, about 2.9 columns) of base at the left. That is about 6.7 columns, about 5.4 units, away from the centre the frame_edges model defines, and it loses picture at the right. The legacy path runs whenever `correct` is on and `edge_reader(film)` returns None: on positive or Kodachrome film, where frame_edges deliberately refuses because the gap is opaque, and in any script that does not pass edge_reader. `registration()`'s shortfall compares against a 10080-unit frame that can never be seen whole in a 10344-unit aperture under the newer model. Three frame widths (35.56, 36.0 and about 37.1 mm) live in one module, and MAX_CORRECTION_MM (the reject bound also used by `_rejudge_for` on the new path) is derived from the old one.

**Evidence (from the code):**

```text
framing.py:3-4 'The aperture is 36.5 mm and a 35 mm frame is 36 mm, so there is half a millimetre of slack'. `NOMINAL_FRAME_WIDTH = 10080` (42, 35.56 mm). `MAX_REGISTRATION_MM = 0.49` (290). `FRAME_WIDTH_MM = 36.0` (586). `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0` (740). `right_gap_closure(..., frame_mm: float = 36.0)` (1368). The same file has `FRAME_WIDTH_UNITS = 350.6` (1957), 435.6 prescan columns, about 37.1 mm, 'Wider than the aperture ... so a centred frame shows no base at all'.
```

**Failure scenario:** A slide roll is scanned with 'correct' ticked. walk_reader('positive') returns None, so StripWalk falls back to film_base/picture_start, built for bright C-41 base, and aims frames to show 0.25 mm of 'gap'. Where two members agree on dark interframe bands, it moves film by a model the owner has since measured to be wrong.

**Fix:** Pick one geometry: derive TARGET, the right-edge conversion and MAX_CORRECTION from FRAME_WIDTH_UNITS, or retire the legacy StripWalk detectors. Gate `correct` by film type exactly as frame_edges does, so the fallback never aims film that the current detector refuses. Correct the module docstring.

<details><summary>Second reader's check</summary>

framing.py:3-4 (the 36.5/36 mm docstring), 42 `NOMINAL_FRAME_WIDTH = 10080`, 290 `MAX_REGISTRATION_MM = 0.49`, 586 `FRAME_WIDTH_MM = 36.0`, 740 `TARGET_GAP_MM`, 1281 `MAX_CORRECTION_MM = MAX_GAP_MM - TARGET_GAP_MM`, 1368 `frame_mm: float = 36.0` and 1957 `FRAME_WIDTH_UNITS = 350.6` all coexist in one module. `StripWalk.judge` falls back to `frame_offset_mm`/`right_gap_closure`/`predict_offset` when `self.reader is None` (1725-1747). `walk_reader` returns None for non-negative films (propose.py:249-253), and the GUI's 'aim each frame while prescanning' checkbox (gui.py:1496-1498) is offered for any film. `_rejudge_for` uses MAX_CORRECTION_MM on the new path too (direct.py:3083).

</details>

<a id="framing-units-fu-08"></a>

### FU-08 -- SEARCH_MM (9.0 mm) is smaller than the largest single command the sheet can ask for (param 87, 9.39 mm), so such holds can never verify

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `rps7200/framing.py:841-857`, `rps7200/framing.py:1061-1063`, `tools/gui.py:217-230`, `rps7200/session.py:93-95`, `rps7200/direct.py:3123`

When the cap was raised from 8 to 87, MAX_TRAVEL_MM grew past SEARCH_MM, and the comment was not revisited. `snap_offset` lets the sheet and adjuster set positions up to ±88.8 units. After the hold loop delivers such a move, the true displacement lies outside `register`'s search window, so the correlation peak cannot be found. The frame reads 'unverified' and counts as a miss, and three in a row turn holding off for the roll. The advance error at arrival adds to the displacement, so the practical limit is below 85 units. SEARCH_MM cannot simply be raised, because CONFIDENCE_FLOOR is calibrated at this reach.

**Evidence (from the code):**

```text
framing.py:854-857: 'Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window.' ... `SEARCH_MM = 9.0`. gui.py:230 `MAX_TRAVEL_MM = MAX_FINE_MM`, where session.py:94 `FINE_MAX_MM = (STEP_MM * MAX_CORRECTION_PARAM + OVERHEAD_MM)`, which is 0.1057*87 + 0.1945 = 9.39 mm. direct.py:3123 `MAX_CORRECTION_PARAM = 87`. measure_shift_mm: `reach = int(SEARCH_MM / max(mm_per_px, 1e-9))`, which is 105 px at 428 columns, while param 87 is about 110 px.
```

**Failure scenario:** CyberView-style 6 mm drift on a strip. The operator sets 88 units on three consecutive frames. Each is moved correctly but logged 'unverified'. The third miss switches holding off, so later frames that needed ordinary corrections are scanned uncorrected.

**Fix:** Clamp sheet and adjuster positions to what can be verified (below SEARCH_MM minus a margin for advance error), or re-fit CONFIDENCE_FLOOR at a reach above LARGEST command + margin with tools/registration_margin.py. Fix the comment either way.

<details><summary>Second reader's check</summary>

The numbers check out. FINE_MAX_MM = 0.1057*87 + 0.1057*1.84 = 9.39 mm (session.py:94-95), `MAX_TRAVEL_MM = MAX_FINE_MM` (gui.py:230), and `snap_offset` clamps to ±MAX_TRAVEL_MM (gui.py:5298). `SEARCH_MM = 9.0` (framing.py:857), and `reach = int(SEARCH_MM / (APERTURE_MM/width))` is 105 px at 428 columns (framing.py:1062-1063). The param 87 move is 88.84*1.2423 ≈ 110 columns, and direct.py:3118 itself records 'param 87 moved 109 px'. `register(..., max_shift=reach)` (1097-1098) cannot find a peak beyond ±105. The unverified outcome counts as a miss (direct.py:3539-3546), and three misses turn holding off. The comment at 854-856 ('Just over MAX_TRAVEL_MM') is stale.

</details>

<a id="framing-units-fu-09"></a>

### FU-09 -- DemoScanner.scan_roll diverges from DirectScanner.scan_roll in its framing steps

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:597-613`, `rps7200/demo.py:708`, `rps7200/demo.py:720-726`, `rps7200/demo.py:747-754`, `rps7200/demo.py:765-777`, `rps7200/direct.py:3493-3530`, `rps7200/direct.py:3509`, `rps7200/direct.py:3653-3655`, `rps7200/direct.py:3664-3677`

CLAUDE.md requires the demo to change only what is fed, not what is done. The framing-relevant differences are these. (1) A walk or roll at 600/900 dpi prescan runs at 300 in the demo, so the demo shows proposals and aiming working where the hardware's detector refuses (FU-05), and holds use a different resolution than real. (2) StripWalk/WalkReader context differs when some frames are approved. (3) A blank frame does not end the demo roll. (4) One detector or decode exception ends the demo roll instead of costing one frame. (5) Demo library entries lack the registration block and roll index that real ones carry. (6) Session code that files prescan_before cannot be reached from `make run-demo`. (7) Machine positions are logged as 'operator'.

**Evidence (from the code):**

```text
demo.py:708 `prescan, _ = self.prescan(film=film)`: the default 300 dpi; `prescan_resolution` and `max_failures` fall into `**kw`. demo.py:723 `index, prescan, 300, held, keep_raw=False,` and 752 `index, prescan, 300, walk,` hard-code 300, pass no `should_stop` and no `source=`. demo.py:750 `marks["base"] = walk.observe(index, prescan)` runs only in the `elif walk is not None:` branch, whereas direct.py:3518 observes every frame before branching. The demo has no `contrast < blank_contrast` end (direct.py:3509), no per-frame try/except or max_failures (direct.py:3664-3677), does not set `meta["roll_index"]` or `meta["registration"]` (direct.py:3653-3655), and never yields `prescan_before`.
```

**Failure scenario:** Stefan tests '600 dpi prescan + correct' in `--demo` and sees frames being centred. On the scanner, every frame abstains, because 860 is not a multiple of 428. The demo reported a plausible and wrong behaviour, which is the failure CLAUDE.md describes.

**Fix:** Take the prescan resolution from the job: pass `prescan_resolution` through, and let DemoScanner.prescan serve the library's shape for that dpi via `_shape_for`. Call walk.observe for every frame, as the driver does. Share the blank-end, failure-count and meta-stamping steps, ideally by running DirectScanner.scan_roll's loop body against the demo's primitives.

<details><summary>Second reader's check</summary>

demo.py:708 `self.prescan(film=film)` uses the default resolution of 300 (demo.py:456). `prescan_resolution` and `max_failures` land in `**kw` (615). 723 and 752 hard-code 300. The approved branch passes neither `should_stop` nor `source=`, so `_hold_to_approved` defaults `source='operator'` (direct.py:2846), while direct passes `getattr(held,'source',None) or 'operator'` (3531-3534). `walk.observe` runs only in the `elif walk is not None` branch (demo.py:750), whereas direct observes every frame (3518-3519). The demo has no blank-contrast end (direct.py:3509-3515), no per-frame try/except or max_failures (direct.py:3664-3677), no `meta['roll_index'/'roll_position'/'registration']` (direct.py:3653-3655), and no `prescan_before` or raw arrays in its RollFrame (demo.py:765-773). Each point is visible in the code.

</details>

<a id="framing-units-fu-a1"></a>

### FU-A1 -- GUI and scan_roll hard-code debug=False, so RPS7200_DEBUG cannot rescue arrival or intermediate prescans, contrary to the _file docstring and CLAUDE.md

**Severity** medium · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1265-1271`, `rps7200/session.py:2057-2060`, `tools/scan_roll.py:344-353`, `rps7200/direct.py:464-469`

The only mechanism that files every individual pass (the arrival prescan, each hold or aim verification prescan, prescan_before) with its own raw bytes is DirectScanner's debug spool. Both operator tools pass debug=False explicitly, which beats the environment variable. The _file docstring justifies `file_entry=False` for prescanNN-before.tif by claiming the debug path already filed it, and in the GUI that is never true. CLAUDE.md's 'RPS7200_DEBUG=1 ... Either works' is also false for the two tools that actually run rolls.

**Evidence (from the code):**

```text
session.py:1266-1270 `return DirectScanner(verbose=self.verbose, debug=False,)`. direct.py:464-469 only consults the env var `if debug is None`. scan_roll.py:353 `with DirectScanner(verbose=args.verbose, debug=False) as s:`. The _file docstring (session.py:2057-2060) says: 'Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway, filed at the instant it was taken'.
```

**Failure scenario:** The owner sets RPS7200_DEBUG=1 before a GUI roll with 'aim each frame' on, expecting every pass in the library. Afterwards the library holds one prescan per frame (corrected, no raw). The arrival passes and prescan-before pictures, the evidence of whether each correction helped, have no raw bytes anywhere.

**Fix:** Let the session and scan_roll file every framing pass themselves: arrival, each verification and before, each with its own capture record taken at the instant of the pass. Alternatively, honour RPS7200_DEBUG by passing debug=None and deduplicating against the entries the session files. Fix the _file docstring.

<a id="framing-units-fu-a2"></a>

### FU-A2 -- CLI dry-run walk (tools/scan_roll.py --dry-run) files no library entry for its prescans; the hold references for --approved exist only as corrected TIFFs

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan_roll.py:495-508`, `tools/scan_roll.py:243-254`, `tools/scan_roll.py:466`

A CLI walk is the input to `--approved` (hold_from_walk reads these TIFFs as references and proposes offsets from them). Unlike the GUI dry run (session.py:1818-1833), it keeps no raw bytes, no shading reference, no mask and no capture record for any prescan. The walk's pixels cannot be re-decoded or re-corrected, and the proposals and holds derived from them cannot be re-derived from raw data. The TIFF is also written on the scanning thread with the device open, which CLAUDE.md and session.py:1813-1816 say must not happen.

**Evidence (from the code):**

```text
Dry-run branch: `pre = out / f"prescan{number:02d}.tif"; tiff.write(str(pre), frame.prescan)` and `tiff.write(str(was), frame.prescan_before)`. No `writer.submit` or `library.save` exists in that branch. The only writer.submit is in the non-dry-run else branch (531-579). `keep_raw=bool(args.library)` (466) is set, but the raw prescan the generator yields (`frame.raw_prescan`) is never used.
```

**Failure scenario:** After a decode or shading fix, `tools/library.py reconstruct` is run to re-derive everything. The CLI walks that fed a commissioned roll's holds are absent from the library, so neither their prescans nor the held offsets computed from them can be recomputed.

**Fix:** Submit dry-run prescans through FrameWriter with `raw_image=frame.raw_prescan`, `prescan_meta` and the capture record, as the GUI's session does, and write the roll-directory copy from the writer thread.

<a id="framing-units-fu-06"></a>

### FU-06 -- centring() applies decide() at 428-column width to edges already scaled to the full width: wrong, sign-inverted moves for any exact-multiple width

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/propose.py:68-78`, `tools/frame_edges/propose.py:102`, `tools/frame_edges/propose.py:129-143`, `tools/frame_edges/centre.py:103-112`, `tests/test_frame_edges.py:190-196`

When k>1 (for example an 856-column pass), the left base width is k times too large, which gives about a 1.76x overshoot for a 12-column gap. The right base width becomes 428 - (856 - 2b), which is about -420 columns, so a frame needing a small move right is told to move about -325 units, i.e. left. The one test for the scaled path checks only `detect`'s x and never the resulting offset. Today this path is unreachable because real widths are 860/1292 (FU-05). Making prescans exact multiples, the obvious fix for FU-05, would activate it.

**Evidence (from the code):**

```text
`detect` returns `_scaled(vote.detect(small, ctx), k)` (102), so x is in full-width columns. `centring` then does `dec = decide(result, SCALE, frame_columns(SCALE, frame_units))` (130). In decide, `b = s.base_width(name, width)` is `self.x if side == "left" else width - self.x`, with width=428.
```

**Failure scenario:** Someone crops 600 dpi prescans to 856 columns so they 'are multiples'. A frame with 10 columns of base on the right is proposed at -34 mm. snap_offset clamps that to -88.8 units, the sheet shows a full backward command, and the hold loop drives the film the wrong way by a quarter of the aperture.

**Fix:** Run decide on the unscaled 428-column result (scale x back by k, or call vote.detect before _scaled and scale only the notes), then convert units to columns at the real width. Add a test that the offset from `read_frame` on an np.kron-upscaled prescan equals the offset from the 428 original.

<details><summary>Second reader's check</summary>

`detect` returns `_scaled(vote.detect(small, ctx), k)` (propose.py:102), so x is multiplied by k. `centring` then calls `decide(result, SCALE, frame_columns(SCALE, ...))` (130). `Side.base_width` returns `width - self.x` for the right side (sides.py:62) with width=428, which gives 428 - k*(428-b). The arithmetic in the finding holds. The only test (test_frame_edges.py:190-196) checks `detect`'s x, never the offset. It is latent, though: no real prescan width (428/860/1292/2584/5172) is a multiple of 428 above k=1, so no production path reaches it today. That is why severity is lowered to low.

</details>

<a id="framing-units-fu-10"></a>

### FU-10 -- Millimetres remain the internal, persisted and CLI unit for transport distances despite the prohibition

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/protocol.py:229-234`, `tools/scan_roll.py:102-107`, `tools/scan_roll.py:412`, `tools/scan_roll.py:517-526`, `tools/gui.py:2952`, `rps7200/session.py:814`, `rps7200/framing.py:263-273`, `rps7200/framing.py:1151-1169`

**Doc claim:** CLAUDE.md:245-262 'Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter'; rps7200/protocol.py:229 reinterprets it as 'prohibited in anything an operator reads'

CLAUDE.md prohibits millimetres for sub-frame distances as a rule, not a preference, because each conversion has hidden mistakes. The code keeps mm as the working unit of the whole hold, aim and approve chain, and in every persisted record. protocol.py narrows the rule to operator-visible text. The CLI still prints mm and takes `--nudge` in mm. Legacy detector reasons (for example 'a N-column band is X.XX mm') are persisted in roll.json member details.

**Evidence (from the code):**

```text
scan_roll.py:102 `ap.add_argument("--nudge", type=float, ... help="move the film this many mm ...` ; 412 `print(f"offset the film by {sent:+.3f} mm in ...`; 517 `f"offset {offset:+.2f} mm"`; 520 `SHORT BY {short:.2f} mm`; 526 `f" {aimed:+.2f} mm"`. approved.json writes `"offset_mm": round(a.offset_mm, 4)` (gui.py:2952). `Approved.offset_mm` (session.py:814). registration() returns offset_mm/shortfall_mm/margin_mm. HOLD_TOLERANCE_MM, HOLD_HEADROOM_MM, ROLL_TRAVEL_LIMIT_MM and SEARCH_MM are all in mm.
```

**Failure scenario:** An operator reads 'offset the film by +0.300 mm' from scan_roll.py and compares it with the window's '+2.8 units'. Or a later reader of approved.json treats offset_mm as the transport's unit and converts it with a different law (0.1057 vs APERTURE/428*1.2423), which reintroduces the 0.2% drift the rule exists to prevent.

**Fix:** Hold distances as param units internally, including Approved and approved.json (with a versioned key such as offset_units), convert once at the edges, and make scan_roll.py print and accept units. Or amend CLAUDE.md to state the narrower rule that the code actually follows.

<details><summary>Second reader's check</summary>

scan_roll.py:102-107 `--nudge` takes mm, 412 prints `{sent:+.3f} mm`, 517/520/526 print mm. approved.json persists `offset_mm` (gui.py:2949), and Approved.offset_mm, registration(), HOLD_*_MM and SEARCH_MM are all mm. protocol.py:229 narrows CLAUDE.md's rule to 'anything an operator reads', but scan_roll.py's printed mm contradicts even that narrower rule. protocol.py:241 also still quotes the old law `0.1057 mm x param + 0.1662 mm`, while MM_PER_COMMAND is 0.1945.

</details>

<a id="framing-units-fu-11"></a>

### FU-11 -- Constants with two homes and three coexisting unit scales

**Severity** low · **Category** design · **Verdict** partly

**Where:** `rps7200/framing.py:18`, `rps7200/framing.py:902`, `rps7200/framing.py:1141-1151`, `rps7200/framing.py:1939`, `rps7200/framing.py:1990`, `rps7200/protocol.py:276`, `tools/gui.py:202`, `rps7200/demo.py:393`, `rps7200/direct.py:3102`, `tests/test_roll.py:1263`

**Doc claim:** CLAUDE.md:264 'the aperture is 345.2 units' vs CLAUDE.md:460 '350.6 units against 344.5'

Several transport constants still have two homes: the command ramp (framing.COMMAND_COST vs protocol.COMMAND_UNITS), MM_PER_UNIT (retyped in demo.py:393), the aperture height (framing.py:902 literal) and APERTURE_MM (gui.py:202). Two column/unit laws also coexist (MM_PER_UNIT vs COLUMNS_PER_UNIT, giving 345.2 vs 344.5 units for the aperture), and there is a dead CORRECTION_DEADBAND_MM. HOLD_TOLERANCE_MM is test-pinned to the mover's law. COMMAND_COST, and therefore the frame-edge deadband SMALLEST_MOVE, is not, so re-measuring the ramp in protocol would leave the detector's deadband on the old value.

**Evidence (from the code):**

```text
framing.py:1151 `HOLD_TOLERANCE_MM = 0.3002`, with the comment 'Duplicated rather than imported because `direct` imports this module', although framing already imports protocol, where MM_PER_UNIT and COMMAND_UNITS live. framing.py:1939 `COMMAND_COST = 1.84` and protocol.py:277 `COMMAND_UNITS = 1.84`. framing.py:902 `MAX_DY_MM = MAX_DY_PX * (24.3053 / 287.0)`, while APERTURE_HEIGHT_MM computes 24.2992. gui.py:202 recomputes `APERTURE_MM`. demo.py:393 `swallowed = min(abs(asked), 2.2 * 0.1057)`. direct.py:3102 `CORRECTION_DEADBAND_MM = 0.15` is never used. The scales are MM_PER_UNIT = 0.1057 (mover and say_units), COLUMNS_PER_UNIT = 1.2423 (decide, frame_overhang), and APERTURE_MM/width (measure_shift_mm, columns_to_mm).
```

**Failure scenario:** Someone updates protocol.COMMAND_UNITS to 1.948 after re-measurement. The mover and the log follow, but frame_edges.decide's deadband (framing.SMALLEST_MOVE) and HOLD_TOLERANCE_MM (a literal) do not. The hold loop's no-limit-cycle guarantee ('tolerance equals the smallest move') then quietly stops holding.

**Fix:** Make protocol the single home: import COMMAND_UNITS into framing, derive HOLD_TOLERANCE_MM and MAX_DY_MM from their definitions, import APERTURE_MM into the GUI and MM_PER_UNIT into the demo, delete CORRECTION_DEADBAND_MM, and decide one mm/column/unit law.

<details><summary>Second reader's check</summary>

The two homes are real. framing.py:1939 `COMMAND_COST = 1.84` and protocol.py:276 `COMMAND_UNITS = 1.84`. framing.py:902 uses a literal 24.3053 while APERTURE_HEIGHT_MM computes to 24.2993. gui.py:202 recomputes APERTURE_MM. demo.py:393 `2.2 * 0.1057`. direct.py:3102 `CORRECTION_DEADBAND_MM` has no users. framing.py:18 already imports from `.protocol`, so the 'duplicated because direct imports this module' rationale at 1141-1143 does not hold. The two aperture figures (345.2 via MM_PER_UNIT, 344.5 via COLUMNS_PER_UNIT) both appear in CLAUDE.md. The failure scenario is overstated for HOLD_TOLERANCE_MM: tests/test_roll.py:1263 pins it to STEP_MM+OVERHEAD_MM, so a COMMAND_UNITS change would fail the test there. framing.COMMAND_COST, SMALLEST_MOVE (the frame_edges deadband) and the demo's 0.1057 are not pinned to protocol and would drift silently.

</details>

<a id="framing-units-fu-12"></a>

### FU-12 -- framing.command_for is an unused second command law whose 'verifiable' cap (160) contradicts the driver's measured cap (87)

**Severity** low · **Category** dead-code · **Verdict** confirmed

**Where:** `rps7200/framing.py:1959-1969`, `rps7200/framing.py:1988-2046`, `rps7200/direct.py:3104-3123`

`command_for` calls itself 'The one SLIDE command' with 'the only rounding in this whole module', but the mover uses `DirectScanner.param_for_mm` with different rounding, a different deadband and a different cap. A future caller who takes framing's word would send param 88-160 moves that direct.py says cannot be verified. The dead detectors (`edge_band` and friends) also embody abandoned thresholds.

**Evidence (from the code):**

```text
framing.py:1969 `MAX_VERIFIABLE_PARAM = 160`: 'The largest param whose move can still be *verified*'. direct.py:3117-3121: 'param 160 moved 196 px at confidence **27** against a floor of 55 -- it goes somewhere and cannot say where'. `command_for`, `describe_command`, `LARGEST_MOVE`, `MAX_PARAM` (except in verify_protocol), `columns_per_unit`, `edge_band`, `Band` and `_row_runs` have no production callers, and `propose_offsets` is used only by tests.
```

**Failure scenario:** A contributor wires the sheet's 'large' step through `framing.command_for` because its docstring says it is the law. A correction of 150 units is sent in one command, and every one comes back unverified.

**Fix:** Delete command_for, describe_command and the unused constants and detectors, or make command_for the one law and have param_for_mm call it with cap 87.

<details><summary>Second reader's check</summary>

A grep finds `command_for` and `describe_command` referenced only inside framing.py, with no test and no tool calling them. MAX_VERIFIABLE_PARAM = 160 (framing.py:1969) contradicts direct.py:3117-3121 (param 160 confidence 27 < floor 55), and the driver caps at 87 (3123). `edge_band` is only used in tools/roll_registration_study.py (its own copy).

</details>

<a id="framing-units-fu-13"></a>

### FU-13 -- registration() asserts 'offset 0, short by 0' when film_bounds abstains (most real prescans); logged and persisted every frame

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/framing.py:114-139`, `rps7200/framing.py:254-273`, `rps7200/framing.py:836-839`, `rps7200/direct.py:3500-3507`, `rps7200/direct.py:3592-3603`, `rps7200/demo.py:1146-1160`

**Doc claim:** tools/scan_roll.py:511-514 comment: '`registration` abstains on a loaded strip -- and once it says so honestly rather than returning a fallback zero, these keys go missing'

This is the failure framing.py itself warns about ('a positive assertion of correctness ... nothing downstream can tell from a measurement'). registration() never returns None, so on most frames it records a precise-looking 'offset +0.0 units, short by 0.0 units' into roll.json and library meta, and `drift_warning` can never fire on a loaded strip. The scan_roll.py comment describes an abstention that the code does not implement.

**Evidence (from the code):**

```text
film_bounds: `if median <= 0 or clear < median * clear_ratio: return lo, hi  # no empty aperture in view`. registration: `width = x1 - x0; offset = ...; shortfall = max(0, NOMINAL_FRAME_WIDTH - width)`, which gives offset 0 and shortfall 0 for the full window. framing.py:838-839: 'on real film `film_bounds` abstains on 97% of prescans'. direct.py:3502-3507 logs `offset {say_units(marks['offset_mm'])}, short by ...` for every frame.
```

**Failure scenario:** An auditor reads roll.json and sees every frame with offset 0 and margin 0, i.e. 'registered'. In fact the detector could not see the frame.

**Fix:** Return None or an explicit 'abstained' flag from registration() when film_bounds finds no aperture, and log 'offset --'. Otherwise stop logging and persisting it.

<details><summary>Second reader's check</summary>

In film_bounds `span()`, `return lo, hi  # no empty aperture in view` gives the full frame (framing.py:124-125). registration() then computes offset (0+10343)/2 - (0+10343)/2 = 0 and shortfall max(0, 10080-10343) = 0 (framing.py:254-258). It never returns None. direct.py:3500-3507 logs it for every frame and stores it in marks, which are persisted. The scan_roll.py:511-514 comment describes an abstention that registration() does not implement.

</details>

<a id="framing-units-fu-14"></a>

### FU-14 -- Detector exceptions other than ValueError abort a whole roll; combine() can raise TypeError on a tie

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/direct.py:3664-3677`, `tools/frame_edges/propose.py:234-246`, `rps7200/framing.py:1583-1588`, `rps7200/framing.py:1284`

EdgeWatch catches every exception per frame, but the roll path does not. An IndexError, TypeError or ZeroDivisionError from a detector member on one odd prescan, or the rare tie in combine on the legacy path, propagates out of the scan_roll generator. It ends an unattended multi-hour roll instead of costing the frame. It is not a wedge, because it happens between prescan and scan.

**Evidence (from the code):**

```text
direct.py:3666 `except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc:`. `walk.observe`/`walk.judge` call `WalkReader._read` -> `detect` -> four ported members with no try/except (only `roll.summarise` guards). framing.py:1584 `worst = max((abs(a.mm - b.mm) - gate(a, b), a, b) ...)`. When two pairs tie on the first element, Python compares `Reading` objects, and `@dataclass(frozen=True)` without order raises TypeError.
```

**Failure scenario:** Frame 17 of 38 has a prescan on which a member indexes an empty array. The roll ends with 'failed' and frames 18-38 are not scanned overnight.

**Fix:** Wrap walk.observe, walk.judge and rejudge in a broad except that records the error in marks and abstains for that frame. Compare by key in combine (`max(..., key=lambda t: t[0])`).

<details><summary>Second reader's check</summary>

direct.py:3666-3667 catches only `(UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError)`. `walk.observe`/`walk.judge` -> `WalkReader._read` -> `detect` -> `vote.detect` has no broad guard (unlike EdgeWatch at watch.py:219). In framing.py:1584-1588, `max()` over tuples `(float, Reading, Reading)` falls back to comparing Reading objects on a float tie. Reading is `@dataclass(frozen=True)` (1284) with no order, so that raises TypeError. A tie is rare, but it is reachable on the legacy path.

</details>

<a id="framing-units-fu-15"></a>

### FU-15 -- Shortcut settings: a malformed hand-edited sequence prevents the window opening after the scanner thread has started; conflicts are unchecked on load; misleading adjuster label

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/shortcuts.py:183-195`, `rps7200/shortcuts.py:148-149`, `tools/gui.py:542-543`, `tools/gui.py:857-875`

**Doc claim:** rps7200/shortcuts.py:187-190 'this file is meant to be edited by hand and a mistake in it should cost a key rather than the window'

A typo like '<Contrl-Key-r>' in gui-settings.json raises TclError during window construction. The window never appears, while the session thread is already opening the device and running INQUIRY. Two actions set by hand to the same key in one scope are not reported: the later bind wins silently. The adjuster's arrow label says it moves the film, which contradicts the module rule that no key moves film and misleads the operator about what the key does.

**Evidence (from the code):**

```text
`resolve` accepts any `str` for a known id. `_bind_shortcuts` does `self.root.bind(sequence, self._runner(run, sequence))` with no TclError guard (only unbind is guarded). In `__init__` it runs right after `self.session.start()` (542-543), which starts the thread that opens the scanner. shortcuts.py:148 `Action("adjust_left", "adjuster", "Move the film one step left", "<Left>")`, but `_step` only changes the planned offset.
```

**Failure scenario:** Stefan hand-edits a shortcut and mistypes a modifier. Next launch, the GUI crashes with a Tcl traceback while the scanner thread is mid-open.

**Fix:** Guard each bind with try/except TclError, drop the bad entry and log it. Run `conflicts()` on the resolved keys at load. Relabel adjust_left/right as 'Set the position one step left/right'.

<details><summary>Second reader's check</summary>

`resolve` accepts any str for a known id (shortcuts.py:192-195). `_bind_shortcuts` calls `self.root.bind(sequence, ...)` unguarded (gui.py:873), right after `self.session.start()` (542-543). A malformed Tk sequence raises TclError in __init__. `conflicts()` exists (shortcuts.py:243) but is not called on load, and `in_scope` maps sequence to id, so a duplicate silently wins. `adjust_left` is labelled 'Move the film one step left' (shortcuts.py:148), but `_step` only calls `_set` on the planned offset (gui.py:6735-6736).

</details>

<a id="framing-units-fu-16"></a>

### FU-16 -- Stale statements about the command law, caps and widths in docs and docstrings

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/protocol.py:253-257`, `rps7200/protocol.py:287-291`, `rps7200/session.py:672-693`, `tools/gui.py:5288-5296`, `tools/gui.py:5318-5320`, `tools/gui.py:4531`, `tools/gui.py:4565-4570`, `tools/scan_roll.py:396-401`, `tests/test_roll.py:1041-1046`, `CLAUDE.md:420`, `README.md:447`

**Doc claim:** CLAUDE.md:420 '`param 1` (~2.6 units) is the finest move' vs CLAUDE.md:265 '2.84'; README.md:447 '300 | 431 x 287'

The prose around the unit law was not updated when COMMAND_UNITS moved from 1.572 to 1.84 and MAX_CORRECTION_PARAM from 8 to 87. Several messages shown to the operator (the `_aim` refusal text) and many docstrings now describe limits that no longer exist. That is how the next change gets reasoned from a wrong premise.

**Evidence (from the code):**

```text
protocol.py:290 'so `param 1` travels 2.57' (the code gives 2.84). protocol.py:253 '`framing.COMMAND_COST` carries 1.84 for the same term, from a later session' (both are now 1.84). session.py:674-676 'for an integer param in 1..8' and '`DirectScanner.param_for_mm` already clamps silently at param 8' (the cap is 87). gui.py:5293-5295 'Clamped to what eight commands can chain, which is `MAX_TRAVEL_MM`' (now one command). gui.py:5319 'the lattice is 2.57 units off zero'. gui.py:4531 `_aim` 'becomes the centre of the frame' (it aims at an edge). gui.py:4566 'would take more than {MAX_FINE_STEPS} sub-frame moves'. scan_roll.py:397 'one command reaches only 1.0118 mm'. test_roll.py:1043 '17% larger ramp'.
```

**Failure scenario:** A reader trusts session.plan_nudges' docstring ('param in 1..8') and builds a UI that chains commands for moves over 1 mm, the pattern framing.command_for says 'should not come back'.

**Fix:** Update or remove these statements, and derive numeric text from the constants instead of quoting numbers.

<details><summary>Second reader's check</summary>

All the quoted stale statements are present. protocol.py:290 says 'param 1 travels 2.57' (units_for_param(1) = 2.84, and the test at test_roll.py:1036 asserts 2.84). session.py:674-676 and 689-690 say '1..8' and 'clamps silently at param 8' (the cap is 87). gui.py:5293-5295 says 'eight commands can chain'. gui.py:5319 says '2.57 units off zero'. gui.py:4531 says 'centre of the frame'. gui.py:4566 says 'more than {MAX_FINE_STEPS} sub-frame moves' (it guards MAX_TRAVEL_MM = one command). scan_roll.py:397 says '1.0118 mm'. test_roll.py:1045 says '17% larger ramp'. CLAUDE.md:420 says '~2.6 units' against 2.84. protocol.py:241 still quotes '+ 0.1662 mm'.

</details>

<a id="framing-units-fu-17"></a>

### FU-17 -- Refused frames get positions extrapolated from the strip's line with no bound

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/frame_edges/propose.py:153-166`, `rps7200/framing.py:800-829`, `tools/frame_edges/watch.py:261`

A strip whose frames 1-8 were read with a small slope gets frames 30-38 extrapolated far past the data. The result is snapped to up to ±88.8 units and proposed as 'read from the frames either side', although nothing lies on one side. The hold loop then drives the film there. EdgeWatch applies the same fill to partial walks while they are still arriving.

**Evidence (from the code):**

```text
`fill_refused`: `filled = fill_from_neighbours(offsets, unplaced)`, then `offsets[n] = filled[n]` with source 'neighbours'. `fill_from_neighbours` returns `slope * float(n) + intercept` for any n, including frames outside the measured range, with no magnitude check. The legacy combine() bound (MAX_CORRECTION_MM) is not applied on the frame_edges path.
```

**Failure scenario:** The reader refuses the last 10 frames of a roll (dense night scenes). A 0.08 mm/frame slope fitted on frames 1-12 proposes -2.6 mm for frame 38, which the operator may not inspect.

**Fix:** Interpolate only between placed frames (or allow at most one frame beyond them), bound the result by the spread of measured offsets, and label extrapolations distinctly.

<details><summary>Second reader's check</summary>

`fill_from_neighbours` returns `slope*n + intercept` for every requested n with no range or magnitude check (framing.py:828-829). `fill_refused` applies it to every 'refused' frame and labels the result 'filled from the line through the frames it placed' (propose.py:153-166). EdgeWatch._progress applies it to partial walks (watch.py:261). The only bound is snap_offset's ±9.39 mm clamp in the GUI.

</details>

<a id="framing-units-fu-18"></a>

### FU-18 -- 'N frames in a row did not reach the position' counts misses across in-place and abstained frames

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/direct.py:2992-3005`, `rps7200/direct.py:3053-3056`, `rps7200/framing.py:1649-1664`

A frame that is already in place, or that the detector abstains on, does not reset the consecutive-miss counter. Three misses spread over, say, eight frames with successes of the in-place kind in between switch aiming off for the rest of the roll, with a message that claims the misses were consecutive.

**Evidence (from the code):**

```text
`_aim_frame` returns early for `decision is None` (2992) and `abs(decision) < HOLD_TOLERANCE_MM` (2998) without calling `walk.landed()`. Only a held move resets `misses` (3054). `missed()` stops aiming at `self.misses >= give_up` with the message 'frames in a row'.
```

**Failure scenario:** On a strip with a slipping frame every few frames and correctly placed frames between them, aiming is switched off at the third miss, and the rest of the roll scans uncorrected.

**Fix:** Call walk.landed() for 'in_place', and decide explicitly whether 'abstained' resets or neutrally skips the counter.

<details><summary>Second reader's check</summary>

`_aim_frame` returns early at 2992-2996 (abstained) and 2998-3005 (in_place) and at the budget check, without `walk.landed()`. Only `fix['outcome'] == 'held'` resets (3053-3054). `StripWalk.missed` says 'frames in a row' (framing.py:1661). An in-place frame is a success that does not reset the counter.

</details>

<a id="framing-units-fu-a3"></a>

### FU-A3 -- Scan reversal check ignores the aim loop's row_reversed; only the approved hold's is passed

**Severity** low · **Category** bug · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1867-1873`, `rps7200/direct.py:3036-3041`, `rps7200/direct.py:3574-3575`

When a frame is moved by the automatic aim path rather than an operator's approved position, the hold loop's evidence that the kept prescan was the reversed one is written under `registration.correction.row_reversed` and never reaches `_note_reversal`. For a pass whose direction is not known from its line tags, `_note_reversal` then blames the scan and records a half-turn that every delivered file applies.

**Evidence (from the code):**

```text
session.py:1871-1873 `prescan_reversed=bool(((rf.registration or {}).get("approved") or {}).get("row_reversed"))`. `_aim_frame` copies the hold result including `row_reversed` into `out` (`out.update({k: v for k, v in fix.items() if k != "prescan"})`), which scan_roll stores as `marks["correction"]` (3574-3575).
```

**Failure scenario:** On an aim-corrected frame whose verification prescan came back bottom-up with unknown direction, the scan is judged against that prescan, recorded as reversed and delivered upside down. The approved path, given the same evidence, would have spared it.

**Fix:** Read row_reversed from either `approved` or `correction`: `prescan_reversed=any((reg.get(k) or {}).get("row_reversed") for k in ("approved", "correction"))`.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Operator- and machine-decided per-frame positions for a commissioned roll | <rolls>/<roll>/approved.json | JSON {roll, numbering, frames:[{number, offset_mm (float, round 4), rotation, flipped, reference_entry, source}]} | decision (not derivable); mm, in the APERTURE_MM/width scale | tools/gui.py:_write_approved (2930-2967), plain write_text, not atomic, only when a roll is commissioned from the sheet | tools/gui.py:read_approved (4924-4971) via read_survey and export; not by tools/scan_roll.py, which recomputes from the prescans | No. Zero offsets are dropped on read (truthiness, 4963). Rounded to 1e-4 mm. No record of which detector, constants (FRAME_WIDTH_UNITS, COLUMNS_PER_UNIT) or edges produced a machine position. No prescan resolution. Unread frames are labelled 'operator'. |
| Per-frame framing marks: registration(), contrast, StripWalk/WalkReader observe detail, hold-loop outcome (target_mm, history of measure_shift details, spent_mm, final_mm, residual_mm, clamped, source), aim outcome (decision_mm, ensemble notes incl. edges, units, columns) | <rolls>/<roll>/survey.json (dry run) or roll.json; frames[].registration | JSON, json.dumps(indent=2, default=str), rewritten whole after every frame | derived measurements (rounded: confidence 2 dp, mm 4 dp, units/columns 3 dp, contrast 4 dp) | rps7200/session.py:_roll (1895-1926); tools/scan_roll.py checkpoint() for CLI runs | tools/gui.py:read_survey (Result.registration), session.renumbered/walked_prescans, tools/scan_roll.py | No. Not atomic, so a crash mid-write corrupts the manifest. default=str turns non-JSON numpy scalars into strings. The prescans these numbers were computed from are only partly kept (FU-01), so the numbers cannot be recomputed. |
| Walk prescans used as the sheet's pictures, the frame-edge detector's input and the hold loop's references | <rolls>/<roll>/prescanNN.tif | TIFF, uint8 RGB, about 428x287 at 300 dpi | corrected (shading applied in prescan) and oriented by the session's rotation/flip at write time | rps7200/session.py:_roll dry-run branch (1818-1833) -> FrameWriter (orient 1067) | tools/gui.py:read_survey (un-orients with the manifest's single rotation, 4740); tools/scan_roll.py:hold_from_walk (does NOT un-orient, 243); EdgeWatch.load; demo picture signatures | No. Orientation is not recorded per file, and a mid-walk rotate breaks un-orienting (FU-02). The library copy of the same pass (dry run only) does carry raw bytes. |
| The prescan a correction replaced | <rolls>/<roll>/prescanNN-before.tif | TIFF uint8 RGB | corrected; no library entry, no raw | rps7200/session.py:1834-1853 (dry run only, file_entry=False); tools/scan_roll.py:502-508 | nobody in code (for humans) | No. It is filed with the replacement pass's prescan_meta, has no raw bytes, and is never written for real rolls. |
| The frame's final framing prescan inside its library entry | <library>/<entry>/prescan.tif + scan.json 'prescan' {file, read_direction, carriage_state} + scan meta 'registration', 'roll_index', 'roll_position' | TIFF uint8 + JSON | corrected, 'no raw bytes of its own' (library.py:161-162) | rps7200/library.save via session._file (frame entries); registration/roll_index added by direct.scan_roll (3653-3655), absent in demo entries | library.migrate/reversal checks, demo picture_signature, anything re-deriving framing | No. Only the last hold pass is kept. The arrival prescan and intermediate verification passes are lost unless RPS7200_DEBUG is on (FU-01). A library migration may flip it in place (library.py:622). |
| Sheet state per walked roll: ticks, offsets, sources, rotations, flips, options | gui-settings.json -> 'sheet' -> <roll key> | JSON (string frame keys, cast back by _clean_sheet_state) | decisions | tools/gui.py:_store_sheet_state -> settings.save (whole payload) | tools/gui.py:_recall_sheet_state/_clean_sheet_state -> _open_sheet -> _merge_kept | No. Zero offsets are not stored as offsets, so an operator's 'as surveyed' zero is lost on reopen (FU-04). |
| Keyboard shortcut overrides | gui-settings.json -> 'shortcuts' | JSON {action_id: Tk sequence string} | n/a | tools/gui.py:set_keys -> _remember (shortcuts.overrides_from, only differences from defaults) | tools/gui.py:_restore -> shortcuts.resolve | Yes as stored. Unknown ids and non-strings are dropped, but a malformed string is not validated and breaks window construction (FU-15). |
| CLI hold note | <rolls>/<roll>/roll.json or survey.json -> 'held' {offsets (round 4 mm), sources, walked, from} | JSON | derived | tools/scan_roll.py:hold_from_walk/main checkpoint() | humans; manifest_settings merges the settings block | No. The offsets were computed on possibly mis-oriented prescans (FU-03). |
| Frame-edge detector state (EdgeWatch frames, summaries, readings; WalkReader summaries; StripWalk bands, placed, settled, travel) | in memory only | python objects | derived | tools/frame_edges/watch.py, propose.py:WalkReader, rps7200/framing.py:StripWalk | GUI sheet (take_readings), DirectScanner._aim_frame | Not persisted by design: proposals are recomputed on every launch from prescanNN.tif. The only record of a machine proposal is the snapped offset and source in approved.json. |

**Second reader's corrections to this table:**

- **prescanNN.tif**: this file is also written by tools/scan_roll.py in its dry-run branch (scan_roll.py:499-508). That path uses plain `tiff.write` on the scanning thread, writes the image unoriented and files no library entry. The claim "The library copy of the same pass (dry run only) does carry raw bytes" holds only for GUI (session) dry runs. There, the entry's scan meta also records that pass's own `rotation`/`flipped` (session.py:2110), so the orientation of each file can be recovered from the library even though survey.json holds only one pair and read_survey ignores the entry.
- **Library frame entry (prescan.tif)**: "lost unless RPS7200_DEBUG is on" is wrong. The GUI (session.py:1270) and scan_roll (scan_roll.py:353) pass debug=False, which overrides the environment variable, so the arrival and intermediate prescans are lost in every configuration. DirectScanner.scan_roll does yield `raw_prescan` for real rolls (direct.py:3658); the session simply does not pass it to `_file` (session.py:1885-1893).
- **approved.json**: explicit zero offsets are written (`round(a.offset_mm, 4)`, gui.py:2949). The truthiness test in read_approved (4963) drops them on read.
- **prescanNN-before.tif**: the session writes it with `file_entry=False` and with the replacement pass's meta, and only in GUI dry runs. scan_roll writes it only in CLI dry runs, with no meta at all.
- **survey.json / roll.json rotation**: the orientation is recorded twice, at the top level (session.py:1687-1688) and inside `settings` (1732-1733). Both copies are taken once, at roll start.

## What the operator can do

- Walk a strip as a dry-run Roll from the window (Roll with 'dry run') or `tools/scan_roll.py --dry-run`, at prescan dpi 300/600/900 from the GUI ladder or any typed 25-7200.
- Open the contact sheet with the button or Cmd/Ctrl-K. The key works even while a walk is still running, as long as some frames exist.
- Rotate or flip any prescan with keys or the menu, in the main window or the sheet, at any time, including during a walk.
- Set per-frame positions in the adjuster: arrows (finest, small = param 3, medium = 8, large = 20), drag, Cmd-C 'as surveyed' (0), or reset to the detector. Values snap to reachable positions up to ±88.8 units (one param-87 command).
- Tick or untick frames, change the sheet's scan options (dpi, prescan dpi, film, meter, IR, correct), and commission 'Scan chosen frames'. It asks first, and the prescan dpi is pinned to the survey's.
- Tick 'correct' or 'correct dry run' so the roll aims frames itself with the frame-edge reader, or with the legacy strip detector for films the reader refuses.
- Reopen a stored walk with --open-roll or make run-sheet. Machine positions are re-measured on every launch; operator positions are kept.
- CLI: `--approved <folder>` holds to positions proposed from a walk's prescanNN.tif; `--nudge <mm>` moves the film before a run; `--prescan-dpi`; `--correct`/`--correct-dry-run`.
- Click in the main preview to aim a point at the nearer aperture edge. It asks first, then moves film immediately.
- Edit shortcuts in the editor, or by hand in gui-settings.json.

## What the operator should not do

- Do not rotate or flip prescans while a dry-run walk is running. It duplicates survey entries and makes later prescanNN.tif files disagree with the manifest's single rotation (FU-02).
- Do not walk at 600 or 900 dpi prescan if you want frame-edge proposals or in-roll aiming. The detector refuses those widths (FU-05).
- Do not use `tools/scan_roll.py --approved` on a folder walked in the window with a rotation or flip set (FU-03). Do not pass a `--prescan-dpi` different from the walk's.
- Do not set positions near the adjuster's maximum (above about 85 units) and expect them to be verified (FU-08).
- Do not rely on Cmd-C ('as surveyed') surviving a sheet close/reopen or a roll reopen when the detector proposes a move (FU-04).
- Do not commission from the sheet while the edge light is still blue if every frame should be placed. Unread frames get 0, attributed to 'operator'.
- Do not enable 'correct' on slide or Kodachrome film. The legacy 36 mm-frame detector runs there instead of the refusing frame-edge reader (FU-07).
- Do not hand-edit shortcut sequences in gui-settings.json unless they are valid Tk sequences (FU-15).

## Mistakes nothing guards against

- Rotate, flip or sheet turns during a walk: no guard. remember_arrangement appends the survey entry again and re-adds it to EdgeWatch, and session.rotation changes mid-roll without the manifest.
- Opening the contact sheet mid-walk with Cmd/Ctrl-K: only `self.survey` non-empty is checked, not whether a walk is still running.
- Choosing 600 or 900 dpi prescan: the GUI offers it and nothing warns that the frame-edge detector will refuse every frame.
- Setting an adjuster position between about 85 and 88.8 units: accepted and snapped, but unverifiable by the hold loop.
- Resetting a frame to 'as surveyed' (0): stored as 'absent', so the detector's value comes back on reopen with no prompt.
- `--approved` pointing at a GUI-walked folder with rotation or flip set: no check against the manifest's rotation or flipped.
- `--nudge` in millimetres with no unit check: a value meant in units (for example 20) is taken as 20 mm and refused only if the planner raises.
- 'correct' on film the frame-edge reader refuses: silently falls back to the legacy detector, with no film gate.
- A malformed shortcut in gui-settings.json: the window fails to build after the scanner thread has started.
- Commissioning while EdgeWatch is still reading: the dialog mentions it, but frames without readings are recorded with source 'operator'.

## Dataflow notes

1. Acquisition.
- DirectScanner.prescan (direct.py:1682) calls scan(depth 8, FULL_FRAME, shading=True).
- The result is a shading-corrected uint8 RGB array, 428 wide at 300 dpi and 860/1292 at 600/900 per demo.py:907. last_pixels_raw holds the pre-shading decode.

2. Per-frame measurement in DirectScanner.scan_roll (direct.py:3493-3530).
- frame_contrast (framing.py:51): below BLANK_CONTRAST the roll ends.
- registration (framing.py:230, via film_bounds framing.py:86) produces marks in scanner coordinates and *_mm, logged through say_units.
- If correct or correct_dry_run is set, StripWalk.observe (framing.py:1671) runs.
  - With a reader: WalkReader.observe (propose.py:228) -> summary -> _downscaled -> roll.summarise.
  - Without one: edge_bands/film_base_from.

3. Approved branch: _hold_to_approved (direct.py:2837).
- measure_shift_mm(reference, prescan) (framing.py:1007) calls uniformity.register at SEARCH_MM reach, upright and row-flipped, applies the floor and the dy gate, and returns mm in the APERTURE_MM/width scale.
- hold_plan (framing.py:1172) decides the next step.
- nudge (direct.py:3138) -> param_for_mm (round, clamp 1..87) -> slide 00/01 param 00 04.
- The loop re-prescans up to MAX_HOLD_MOVES times. Its out dict becomes marks['approved'], and the last prescan replaces the frame's prescan.

4. Aim branch: _aim_frame (direct.py:2960) -> walk.judge (framing.py:1725).
- Reader path: propose.WalkReader.judge -> detect (vote.detect over 4 members, scaled by k) -> centring (propose.py:119) -> centre.decide (units via COLUMNS_PER_UNIT) -> columns -> columns_to_mm (APERTURE_MM/width).
- Legacy path: combine(frame_offset_mm, right_gap_closure, predict_offset).
- Then affordable (ROLL_TRAVEL_LIMIT_MM), then _hold_to_approved(_Aim(reference=same image)) with _rejudge_for.
- Afterwards walk.record/landed/missed run. marks['correction'] is filled, and prescan_before is set.

5. Filing: RollFrame -> session._roll (session.py:1793-1926).
- _deliver sends a downscaled working copy to the UI.
- Dry run: _file(prescan, raw_image=raw_prescan) makes a library entry and prescanNN.tif oriented by the current session.rotation/flip. prescan_before is written as a TIFF only.
- Real roll: _file(frame, raw_image, prescan=final corrected prescan). The prescan raw is dropped.
- The manifest record, with registration marks, is rewritten after every frame (write_text, default=str).

6. GUI, live walk.
- Event 'result' -> remember_arrangement (gui.py:3498): if surveying, survey.append and EdgeWatch.add (watch.py:110).
- The EdgeWatch worker thread runs summaries first, then read_frame against the frames already summarised.
- After finish(), frames are re-read against all others.
- _progress runs fill_refused (fill_from_neighbours) and builds Progress(offsets_mm, notes).
- The pump calls _edges_changed (gui.py:3088) -> sheet.take_readings (7805) -> _snap_proposals (snap_offset -> session.plan_nudges -> DirectScanner.param_for_mm).
- Machine offsets go into sheet.offsets and proposals. Adjuster _set (6706) snaps, pops zeros and marks the frame 'operator'.
- _scan -> approved_from_sheet (5791) -> on_scan_chosen -> _write_approved (approved.json) -> Roll(approved, prescan_resolution pinned to the survey).
- session: approved keyed by number-1 -> scan_roll.

7. Reopen.
- read_survey (gui.py:4655) -> walked_prescans -> tiff.read prescanNN.tif -> preview.unorient(manifest rotation) -> Results.
- read_approved drops zero offsets.
- EdgeWatch.load re-measures, then _open_sheet -> _merge_kept, where only non-zero kept offsets win.

8. CLI: scan_roll.py --approved -> hold_from_walk -> walked_prescans -> tiff.read (no unorient) -> propose_centred -> Approved (unsnapped) -> DirectScanner.scan_roll(approved, edge_reader=walk_reader).

9. Demo: DemoScanner.scan_roll (demo.py:597).
- Its prescans are library prescan.tif, np.roll-shifted by _film_mm through APERTURE_MM/width, always at 300 dpi.
- _marks uses the real registration.
- It borrows _hold_to_approved, _aim_frame, _rejudge_for and param_for_mm from DirectScanner.
- nudge models backlash with a retyped 0.1057.

10. Unit chain.
- Detector columns -> units (framing.units_per_column, COLUMNS_PER_UNIT 1.2423) -> columns -> mm (APERTURE_MM/width) -> hold loop mm -> param (protocol MM_PER_UNIT 0.1057 plus MM_PER_COMMAND) -> byte.
- Display uses say_units (MM_PER_UNIT) in logs and units_per_column in the sheet's overhang caption, which differ by about 0.2%.

11. Threads and shortcuts.
- EdgeWatch: all state is under a Condition(RLock). The worker releases the lock while computing and discards results if the generation or version changed. version and generation are read without the lock (ints). No shared mutable dicts escape to the GUI.
- StripWalk is used only on the scanner thread.
- shortcuts.py is pure data. gui._bind_shortcuts binds the resolved table on root and toplevels. NEVER_BOUND is enforced by tests, not by code.
