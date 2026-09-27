# The window (tools/gui.py, second half)

Area key `gui-part2`. 32 findings: 1 high, 11 medium, 18 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

tools/gui.py lines 4456-8916: the big view (full-resolution loading, zoom/pan, pixel readout, histogram), the aim click (on_press/_aim → on_nudge), Delete of a pass, read_survey/read_approved/roll_summary/roll_entry_index/roll_exports (reading a roll folder back and joining it to the library), the transport-lattice helpers (snap_offset, step_offset, fine_preview), approved_from_sheet, _FrameAdjuster (the position window), _RollBrowser (open, export, duplicate, rename, delete of roll folders), _ContactSheet (ticks, options, arrangement, detector readings, commissioning), and main(). I followed calls into part 1 (on_scan_chosen, _write_approved, open_roll, on_export_rolls, on_delete_rolls, _deliver_one, _pump/_handle) and into rps7200/session.py, library.py, direct.py, export.py and tiff.py to confirm behaviour.


No Tk call is made from a worker thread in this area. Histogram and full-resolution reads go through queues (_measured, _reads), and Export and Save all use _saves. Nothing branches on `if demo` above the seam: self.demo only sets the window title, and look_only only changes two texts.


The main problems:
- Export joins library entries to rolls by the roll *name* alone. After a Duplicate, a rename-and-reuse, or a deleted-and-recreated name, it delivers another roll's frames.
- Open in the roll browser is not guarded against a running job.
- Contact-sheet decisions are stored in gui-settings under the folder name and come back onto a new strip walked into a folder with the same name.
- A double-click on a thumbnail toggles its tick.
- Return in the position window, and All, re-tick frames that are already scanned, and the confirm dialog says nothing about it.
- Export applies the window's current mono setting, not the roll's.
- The roll-in-use guard checks the last roll opened from the browser, not the sheet's own roll.
- Aim moves the film from whatever prescan is on screen, even a stale one.
- Full-resolution loads run without a limit, and Export, Save all and Save As compress with the scanner open and idle.
- The histogram measures corrected pixels, not the raw rail.
- The sheet's "nudge" option does nothing on a roll commissioned from the sheet.
- The sheet's options allow combinations the driver refuses only after the film has moved.
- Several texts shown in the UI are stale.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [GUI2-01](#gui-part2-gui2-01) | high | data-integrity | confirmed | Export joins library entries to rolls by roll name only: a Duplicate, a reused name or a renamed-and-reused name exports another roll's frames |
| [GUI2-02](#gui-part2-gui2-02) | medium | user-error | confirmed | Roll browser 'Open' has no busy guard: opening a roll during a walk mixes two strips in one sheet and files decisions in the wrong folder |
| [GUI2-03](#gui-part2-gui2-03) | medium | data-integrity | confirmed | Contact-sheet decisions are stored per folder name in gui-settings and come back onto a new strip walked into a folder with that name |
| [GUI2-04](#gui-part2-gui2-04) | medium | user-error | confirmed | Double-clicking a thumbnail (the documented way to set a position) toggles its tick |
| [GUI2-05](#gui-part2-gui2-05) | medium | user-error | confirmed | Return in the position window, and All, re-tick frames already scanned, and the confirm dialog doesn't say they will be rescanned |
| [GUI2-06](#gui-part2-gui2-06) | medium | hardware-safety | confirmed | Heavy local work runs with the scanner held open: full-resolution loads without a limit, and deflate-compressed Export, Save all and Save As |
| [GUI2-07](#gui-part2-gui2-07) | medium | user-error | confirmed | Aim moves the film from whatever prescan is on screen, even one that no longer shows where the film is |
| [GUI2-08](#gui-part2-gui2-08) | medium | user-error | confirmed | The roll-in-use guard checks only the last roll opened from the browser; the sheet's own roll can be deleted or renamed and is then recreated empty by Scan chosen frames |
| [GUI2-09](#gui-part2-gui2-09) | medium | bug | confirmed | Roll Export (and Save all) applies the window's current mono setting, not the roll's own |
| [GUI2-10](#gui-part2-gui2-10) | medium | doc-mismatch | confirmed | The sheet's 'nudge registration between frames' option does nothing on a roll commissioned from the sheet, and the header claims it applies to the frames he did not adjust |
| [GUI2-11](#gui-part2-gui2-11) | medium | design | confirmed | The histogram's 'at full' and 'near full' figures are measured on shading-corrected pixels, not on the raw values that hit the rail |
| [GUI2-A1](#gui-part2-gui2-a1) | medium | user-error | found-by-verifier | A contact sheet opened while a walk is still running is never rebuilt: walk end only raises it, so frames walked after it opened have no cell and cannot be ticked |
| [GUI2-12](#gui-part2-gui2-12) | low | user-error | partly | The sheet's option panel allows combinations the driver refuses only after the film has been wound (7200 dpi; infrared on B&W or Kodachrome) |
| [GUI2-13](#gui-part2-gui2-13) | low | data-integrity | confirmed | A reopened walk loses the link between each reference prescan and its library entry: approved.json gets reference_entry "" |
| [GUI2-14](#gui-part2-gui2-14) | low | data-integrity | confirmed | Commissioning a sheet opened from outside session.rolls scans into a new folder without carrying the walk, so the scanned roll has no survey |
| [GUI2-15](#gui-part2-gui2-15) | low | doc-mismatch | confirmed | Stale transport-limit texts: the aim refusal cites '8 sub-frame moves', snap_offset says 'eight commands', and the lattice note says 2.57 units; aim retypes the planner's arithmetic and a 1.1 s per move figure |
| [GUI2-16](#gui-part2-gui2-16) | low | doc-mismatch | confirmed | The shortcut editor says no shortcut starts a scan, but Prescan, Scan and Roll are bound by default |
| [GUI2-17](#gui-part2-gui2-17) | low | doc-mismatch | confirmed | The sheet header says the film is 'rewound to the start of the strip first'; the roll actually goes to the first ticked frame |
| [GUI2-18](#gui-part2-gui2-18) | low | doc-mismatch | confirmed | The Delete-roll dialog calls everything in a roll folder except approved.json re-derivable, but no tool rebuilds a walk, and the decision count ignores positions |
| [GUI2-19](#gui-part2-gui2-19) | low | error-handling | confirmed | Save As runs on the UI thread without error handling |
| [GUI2-20](#gui-part2-gui2-20) | low | data-integrity | confirmed | 'Nothing already there is overwritten' is untrue for the JPEG sidecar DNG, for the no-Pillow TIFF fallback, and after 999 name clashes |
| [GUI2-21](#gui-part2-gui2-21) | low | bug | partly | Exports of rolls made by tools/scan_roll.py are named '0dpi' |
| [GUI2-22](#gui-part2-gui2-22) | low | design | confirmed | The full-resolution view ignores library.corrected's correction state, so a raw entry is shown as 'the scan's own pixels' without saying so |
| [GUI2-23](#gui-part2-gui2-23) | low | concurrency | confirmed | Delete-pass rebuilds the library index on the UI thread, racing the writer thread's reindex; a failed rmtree leaves a half-deleted entry |
| [GUI2-24](#gui-part2-gui2-24) | low | bug | confirmed | Turning frames in the sheet moves the window's 'where is the film' forecast to those frames' walk positions, and 'rotate all' redraws and reloads once per frame |
| [GUI2-25](#gui-part2-gui2-25) | low | demo-divergence | confirmed | --demo with an explicit --library or --rolls writes demo scans and rolls into the real library or rolls, contradicting the comment that --demo pins rolls under demo/ |
| [GUI2-26](#gui-part2-gui2-26) | low | user-error | confirmed | Scan chosen frames closes the sheet before asking; Cancel, busy or a calibration prompt leaves it closed |
| [GUI2-27](#gui-part2-gui2-27) | low | concurrency | confirmed | Turns made in a sheet reopened during a running roll change how arriving frames are shown but not how they are filed |
| [GUI2-28](#gui-part2-gui2-28) | low | design | confirmed | snap_offset silently clamps detector proposals and stored positions to one command |
| [GUI2-A2](#gui-part2-gui2-a2) | low | data-integrity | found-by-verifier | Library entry paths written into approved.json, roll.json and roll_membership are relative to the window's working directory |
| [GUI2-29](#gui-part2-gui2-29) | info | demo-divergence | confirmed | The demo seam holds in this area: no `if demo` in the scan path |
| [GUI2-30](#gui-part2-gui2-30) | info | dead-code | confirmed | _propose_positions and roll_line are used only by tests |

## Findings in full

<a id="gui-part2-gui2-01"></a>

### GUI2-01 -- Export joins library entries to rolls by roll name only: a Duplicate, a reused name or a renamed-and-reused name exports another roll's frames

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:5478-5525`, `tools/gui.py:5631-5656`, `tools/gui.py:5335-5376`, `tools/gui.py:2779-2808`, `tools/gui.py:2757-2777`, `rps7200/session.py:778-787`, `rps7200/session.py:837-852`, `rps7200/session.py:2306-2311`, `rps7200/session.py:1151-1156`

Every roll folder whose manifest carries the same roll name gets the same entry map, and for each frame number the newest entry wins (entry ids start with a UTC timestamp, and the glob is sorted). Export re-corrects from that map. It is not restricted to the roll's own `done` frames or to the entries its roll.json names. So after the Duplicate workflow the dialog recommends (rescan the original), exporting the *copy* delivers the rescanned frames rather than the ones the copy was made to keep. The copy's own `arranged` rotation and `settings.resolution` are then applied to the new frames, so file names and orientation can also be wrong. The same happens when a typed roll name is reused after a Delete or a Rename: old entries of that name are exported as frames of the new roll.

**Evidence (from the code):**

```text
roll_entry_index keys only on the membership's roll name and ignores its recorded folder: `roll, number = str(member.get("roll") or ""), member.get("number")` ... `out.setdefault(roll, {})[number] = record_path.parent` (gui.py:5510-5513). rolls_on_disk: `summary["entries"] = dict(index.get(summary["roll"], {}))` (5654), where `"roll": progress.get("roll") or manifest.get("roll") or folder.name` (5612). roll_exports: `for number in sorted(summary.get("entries") or {}):` (5357). roll_membership records `"folder": str(folder)` (session.py:786-787), but nothing reads it. A duplicate keeps the original's name: `name = job.name or recorded_roll_name(out) or out.name` (session.py:2308), and recorded_roll_name reads the manifest's roll name. roll.json records each frame's own entry (`record["entry"] = str(entry)`, session.py:1155), and the export ignores that too. The Duplicate dialog promises: 'The copy keeps the walk, the approvals and the frames already scanned, so the original is safe to rescan over.' (gui.py:2797-2800).
```

**Failure scenario:** 1. Scan roll R at 1800 dpi. 2. In Rolls..., Duplicate R to R-2 (its manifest still says roll R). 3. Rescan R at 3600 dpi. 4. Export R-2. Every frame comes out as the 3600 dpi rescan, named `R_frame01_1800dpi.tif`, with R-2's recorded rotation. The 1800 dpi scan the copy existed to preserve is not exported. Second case: delete roll 'Portra', walk and scan a new strip typed 'Portra' covering frames 1-6. Export includes old frames 7-12 from the deleted roll.

**Fix:** Join on the folder, not the name. Use the per-frame `entry` that roll.json already records, and fall back to roll_membership's `folder` (compared by resolved path) only for older rolls. Restrict exports to the roll's own `done` frames. Add tests for a duplicate and for a reused name.

<details><summary>Second reader's check</summary>

roll_entry_index (gui.py:5497-5525) keys only on roll_membership.roll / film.frame, and the sorted glob over UTC-stamped entry ids makes the newest entry win per number. rolls_on_disk assigns `summary["entries"] = dict(index.get(summary["roll"], {}))` (5654), and summary['roll'] comes from the manifest (5612). recorded_roll_name (session.py:837-852) keeps a duplicate's or renamed folder's original name, and session.py:2306-2311 labels new frames with it. The membership's `folder` (session.py:786) and roll.json's per-frame `entry` (session.py:1155) are never consulted. roll_exports (5357) iterates every indexed number, not `done`, and applies the copy's settings.resolution, arranged and approved rotations. The Duplicate dialog's promise (2797-2800) is therefore false for Export. The reused-name case is also reachable, because roll_dir returns the existing or recreated folder of a typed name.

</details>

<a id="gui-part2-gui2-02"></a>

### GUI2-02 -- Roll browser 'Open' has no busy guard: opening a roll during a walk mixes two strips in one sheet and files decisions in the wrong folder

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:7730-7735`, `tools/gui.py:2878-2898`, `tools/gui.py:2938-2957`, `tools/gui.py:2565-2570`, `tools/gui.py:3907-3914`, `tools/gui.py:3713-3714`, `tools/gui.py:3666-3669`

Export, Duplicate, Rename and Delete in the browser all go through `_roll_is_busy`. Open does not. If the browser was left open, or opened before a walk or roll was started from the main window, Open replaces the survey, the sheet and `_sheet_roll` mid-job. The remaining prescans of the running walk join the reopened roll's survey, possibly under the same frame numbers. The walk's end then points `_sheet_roll` at the new walk's folder, while the open sheet shows the reopened roll.

**Evidence (from the code):**

```text
on_reopen_survey refuses while busy: `if self.busy: messagebox.showinfo("Open a roll", "The scanner is working. ... opening a roll replaces whatever is loaded now.")` (2565-2570). The browser is a non-modal Toplevel, though, and its `_open` goes straight to `self.top.destroy(); self.gui.open_roll(summary["folder"])` (7734-7735). open_roll has no busy check. It sets `self.survey = out["results"]` (2939) and `self._sheet_roll = folder` (2957). Prescans still arriving from the walk are appended to whatever survey is current: `if self._surveying and result.kind == "prescan" and result.number: self._into_survey(result)` (3907-3913). When the walk ends: `self._sheet_roll = getattr(self.session, "last_roll_dir", None)` (3714), then `on_contact_sheet()` (3669).
```

**Failure scenario:** Operator opens Rolls..., starts a dry-run walk from the main window, and while it runs double-clicks an old roll in the still-open browser. The old roll's sheet opens. The walk's later prescans are appended to `self.survey` (two frames can now share a number, colliding in `ticks`, `_rings` and approved_from_sheet). When the walk finishes, `_sheet_roll` is the new walk's folder. Pressing Scan chosen frames on the old roll's sheet writes its approved.json into the new walk's folder and scans there, and the old roll's decisions are stored in gui-settings under the new folder's name.

**Fix:** Check `self.busy` (and `self._surveying`) in open_roll itself, not only in on_reopen_survey, so every entry point is covered. Consider disabling the browser's Open while busy.

<details><summary>Second reader's check</summary>

_RollBrowser is a non-modal transient Toplevel (7602), and _open (7730-7735) calls gui.open_roll directly. open_roll (2878-2957) has no busy check; only on_reopen_survey checks busy (2565). Mid-walk, open_roll replaces self.survey and _sheet_roll. Later walk prescans still go through `_into_survey` because `_surveying` stays True (3907-3913). _walk_ended then sets `_sheet_roll = session.last_roll_dir` (3713-3714) and on_contact_sheet lifts the reopened roll's sheet. open_roll also calls _restore_roll_settings, which rewrites the window controls mid-job.

</details>

<a id="gui-part2-gui2-03"></a>

### GUI2-03 -- Contact-sheet decisions are stored per folder name in gui-settings and come back onto a new strip walked into a folder with that name

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:2606-2615`, `tools/gui.py:2678-2691`, `tools/gui.py:2296-2313`, `tools/gui.py:3713-3714`, `tools/gui.py:2486-2517`, `tools/gui.py:2519-2532`, `tools/gui.py:6112-6141`, `tools/gui.py:7898-7905`, `tools/gui.py:2810-2863`

The ticks, the 'operator' positions, and the rotations and flips of the last strip filed under a folder name are restored onto whatever strip is walked next into a folder of that name. That happens when a name is typed again (folder_note only warns that the old walk's survey is replaced), after a Delete, or after a Rename followed by reuse of the old name. The positions are offsets relative to where the *old* walk surveyed each frame, so on a new walk they mean nothing, and yet they are held as 'yours' and not re-read by the detector. The only notice is a log line, e.g. 'contact sheet: ... -- kept 3 positioned, 2 turned'.

**Evidence (from the code):**

```text
`_sheet_key` returns `Path(self._sheet_roll).name if self._sheet_roll else None` (2615). A fresh walk clears only the in-memory copy: `self.sheet_state = {}` (2308), `self._sheet_roll = None` (2313). When the walk ends, `_sheet_roll` becomes the walk's folder (3714), and `on_contact_sheet` calls `_recall_sheet_state()`, which falls back to the settings file: `if self.sheet_state: ... key = self._sheet_key() ... return self._clean_sheet_state((self.remembered.get("sheet") or {}).get(key))` (2685-2691). `_merge_kept` keeps every offset whose source is `operator` (6136-6140), and the sheet applies stored rotations and flips to the new results (7901-7905). on_delete_rolls and on_rename_roll never touch `remembered["sheet"]` (2837-2842, 2859).
```

**Failure scenario:** Walk strip A into a roll typed 'Portra'. Set frame 4 to +20 units and turn frame 7 by 90°. Close the sheet. Delete the roll (or re-walk the same name with a different strip). Walk strip B with 'Portra' in the roll box. The sheet opens with frame 4 at +20 units marked 'moved ... (yours)' and frame 7 sideways. Commissioning moves B's frame 4 by 20 units and writes frame 7 rotated.

**Fix:** Key the stored state by something unique to the walk (e.g. the survey's creation stamp or a UUID written into survey.json), or discard it when a fresh walk replaces survey.json. Drop `remembered["sheet"][name]` on Delete, and move it on Rename.

<details><summary>Second reader's check</summary>

_sheet_key is the folder name alone (2615). A fresh walk sets `sheet_state = {}` (2308), so at walk end _recall_sheet_state falls back to `remembered['sheet'][name]` (2685-2691). on_contact_sheet then applies the stored ticks, rotations, flips and 'operator' offsets (2496-2517), and _merge_kept keeps operator offsets (6122-6140). on_delete_rolls and on_rename_roll never touch remembered['sheet'] (2810-2863). A typed name that already exists (roll_dir returns it), or a deleted name reused, reaches this. The only notice is the 'kept N positioned' log line.

</details>

<a id="gui-part2-gui2-04"></a>

### GUI2-04 -- Double-clicking a thumbnail (the documented way to set a position) toggles its tick

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:8219-8221`, `tools/gui.py:8147-8150`, `tools/gui.py:7366-7368`, `tools/gui.py:7945-7947`

In Tk the first press of a double-click fires <Button-1>, and only the second press matches <Double-Button-1>. A double-click therefore flips the frame's tick once and then opens the position window, which loads the flipped tick (`self.v_tick.set(self.sheet.ticks[self.number].get())`). If the operator then leaves with Done or Escape rather than Return, the frame stays unticked and is not scanned. This is the 'frame silently unscanned' failure the sheet's own comments say cost two rolls. No test exercises it.

**Evidence (from the code):**

```text
`picture.bind("<Button-1>", lambda _e, n=number, i=index: self._clicked(n, i))` and `picture.bind("<Double-Button-1>", lambda _e, i=index: self.adjust(i))` on the same widget (8219-8221). `_clicked` does `self._select(index); self._toggle(number)` (8147-8150). The sheet's header says: 'Click a picture to tick it, double-click or press Return to set where the film should sit' (7945-7946).
```

**Failure scenario:** All 12 frames are ticked. The operator double-clicks frame 3 to nudge it, drags it and presses Done. Frame 3 is now unticked (its ring turns dark and 'not scanning' appears under a nearly black negative). The roll scans 11 frames.

**Fix:** Don't toggle on a single click that turns out to start a double-click: defer the toggle with after() and cancel it on <Double-Button-1>, or toggle only through the checkbox and Space. Add a test.

<details><summary>Second reader's check</summary>

Both <Button-1> (calls _clicked, which does _select and _toggle) and <Double-Button-1> (adjust) are bound on the same Label (8219-8221). In Tk the first press of a double-click fires <Button-1>, so the tick flips once before the adjuster opens. _load (7366-7368) copies the flipped tick into the adjuster, and Done/Escape (top.destroy) leave it flipped. The header text (7945-7946) advertises double-click as the way to set a position.

</details>

<a id="gui-part2-gui2-05"></a>

### GUI2-05 -- Return in the position window, and All, re-tick frames already scanned, and the confirm dialog doesn't say they will be rescanned

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:7332-7350`, `tools/gui.py:7992-7995`, `tools/gui.py:8625-8628`, `tools/gui.py:8197-8198`, `tools/gui.py:3149-3161`, `rps7200/session.py:2629`

On a resumed roll, done frames open unticked so hours of scanning are not spent twice. The keyboard flow the position window offers (Return = 'keep this frame and go to the next'), the All button and Cmd-A all re-tick them silently. The confirm dialog neither counts nor names the already-scanned frames, and the earlier frameNN.tif is replaced (the library keeps both entries).

**Evidence (from the code):**

```text
`_accept`: `self.v_tick.set(True); self._tick_changed()` (7344-7345), whatever `number in self.sheet.done`. `_set_all(True)` ticks every var (8625-8628). Done frames start unticked (`value=self._initial_ticks.get(number, number not in self.done)`, 8198), but the confirmation only lists numbers: `f"Scan {len(numbers)} of the {walked} frames walked: {', '.join(str(n) for n in numbers)}.` (3151-3152). A rescan writes `path=out / f"frame{number:02d}.tif"` (session.py:2629), replacing the earlier file.
```

**Failure scenario:** A 24-frame roll is resumed with 11 done. The operator reviews positions with Return, Return, ... through the whole strip. All 24 are ticked. The dialog says 'Scan 24 of the 24 frames walked'. 11 frames (about 11 × 5 minutes at 3600 dpi RGBI) are rescanned and their frameNN.tif files overwritten.

**Fix:** Have `_accept` keep a done frame's tick as it was. Have 'All' exclude done frames, or ask first. In on_scan_chosen, name the chosen frames that are already scanned and ask.

<details><summary>Second reader's check</summary>

_accept sets `v_tick.set(True)` unconditionally (7344-7345). _set_all(True) (8625-8628) and sheet_all (Cmd-A, 8107; shortcuts.py:132) tick every frame, done ones included. Done frames start unticked only by default (8197-8198). The confirm in on_scan_chosen lists numbers and count only (3149-3161), and nothing says which are already scanned. The roll writes `path=out / f"frame{number:02d}.tif"` (session.py:2629) with no _unclaimed, so the earlier frame file is replaced. The library keeps both entries.

</details>

<a id="gui-part2-gui2-06"></a>

### GUI2-06 -- Heavy local work runs with the scanner held open: full-resolution loads without a limit, and deflate-compressed Export, Save all and Save As

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/gui.py:4575-4603`, `tools/gui.py:3994-4006`, `tools/gui.py:2709-2777`, `tools/gui.py:4237-4307`, `tools/gui.py:4221-4235`, `tools/gui.py:4309-4341`, `rps7200/tiff.py:97-103`, `rps7200/shading.py:270-280`, `rps7200/session.py:1966-1970`

**Doc claim:** CLAUDE.md 'The scanner wedges': 'Do not hold the session open through heavy local work. Gzipping a 140 MB library entry with the device open and idle preceded one wedge.' And 'A single scan compresses nothing while the device is open'.

The window holds the device open for its whole life. Export and Save all re-correct every full-resolution frame and deflate-compress it, and Save As does the same on the UI thread, all while the device is open and idle, and during a roll for Export. That is the load pattern the library's filing code was changed to avoid. Separately, clicking through the filmstrip starts one un-cancelled full-resolution correction thread per pass. At 7200 dpi RGBI each needs roughly 1.5-2 GB (uint16 image, output and a float64 plane), so a quick run through several frames can take several GB and swap, or get the process killed while a pass is in flight. A killed process is an abandoned read.

**Evidence (from the code):**

```text
`_load_full` deduplicates only on the same pass: `if self._loading == r.seq or self._levels_seq == r.seq: return` (4583-4585). Every `_show` of another filed pass starts another thread calling `library.corrected(entry)` (4593). apply_shading makes a float64 copy per channel: `vals = image[:, : loc.size, c].astype(np.float64) * gain` (shading.py:271). on_export_rolls checks `self._saving` but not `self.busy` (2717-2721). `_deliver_one` calls `export.write(path, full, ...)`, which reaches `tiff.write(..., compress: bool = True)` (tiff.py:102, deflate). The session holds the device open from `_run` to shutdown, and its own comment says: 'the device closes first, and only then does the writer get to spend time gzipping. The other way round is the open-and-idle state that preceded a wedge' (session.py:1967-1969).
```

**Failure scenario:** During a 3600 dpi roll the operator clicks through eight finished frames in the filmstrip: eight concurrent library.corrected threads, 4 GB or more of float64 temporaries on a 16 GB laptop. Or, with the scanner idle, the operator exports three rolls (about 100 frames × 142 MB, deflated) in the same process that holds the device open and idle.

**Fix:** Cap full-resolution loads at one in flight (cancel or skip stale requests), or free the previous one before starting the next. Refuse or defer Export and Save all while busy. Either write exports uncompressed while the session is open, or close the device first (e.g. offer to close the session before a large export). Measure it with tools/filing_load_test.py before relying on it.

<details><summary>Second reader's check</summary>

_load_full dedupes only the same seq (4583-4585) and starts an uncancelled thread per shown filed pass. _show calls it on every selection (3993-4005), and remember_arrangement calls _show. library.corrected goes through apply_shading's per-channel float64 temporaries (shading.py:270-283). on_export_rolls checks `_saving` but not `busy` (2717-2721). export.write reaches tiff.write with the default compress=True (tiff.py:97-103). Save As runs all of this on the UI thread (4221-4235). The device stays open for the session's life. That this load can wedge the device is inferred from the documented gzip-while-open hazard, not measured.

</details>

<a id="gui-part2-gui2-07"></a>

### GUI2-07 -- Aim moves the film from whatever prescan is on screen, even one that no longer shows where the film is

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:4983-4987`, `tools/gui.py:5014-5070`, `tools/gui.py:3343-3354`, `tools/gui.py:3361-3409`

`_moving_refused`'s own docstring names the failure: 'a distance measured on one frame applied to another'. Aim still accepts any prescan in the filmstrip, including an older frame's, a reopened roll's walk prescan (which could be from days ago), or the prescan it has just aimed from. The dialog only advises: 'prescan again to check it landed'. A second click on the same stale prescan applies the correction twice.

**Evidence (from the code):**

```text
`on_press`: `if self.v_aim.get() and self.current is not None and self.current.kind == "prescan": self._aim(event)` (4984-4986). `_aim` computes `want = aim_millimetres(fraction)` from `self._source()` (5025-5036) and ends in `self.on_nudge(1 if want > 0 else -1, millimetres=abs(want))` (5070). The only guard is `_moving_refused`, which checks `self.busy` alone (3350-3354). Nothing compares `self.current.position` with `self._transport`, or notices that the film has moved since that prescan was taken.
```

**Failure scenario:** The operator aims on frame 5's prescan (the film moves 40 units), then without prescanning again clicks the same edge once more. The film moves another 40 units, now 40 past the intended position. Or: with aim ticked, the operator clicks back to frame 2's walk prescan while the film sits on frame 9, and frame 9 is moved by frame 2's error.

**Fix:** Allow aim only on the newest prescan whose `position` equals the transport's last reported position, and only if no Move has run since. Otherwise refuse and say why.

<details><summary>Second reader's check</summary>

on_press routes any prescan to _aim when aim is ticked (4983-4987). _aim computes the move from the clicked fraction of whatever prescan is current (5014-5070). The only guard is _moving_refused, which checks `busy` alone (3343-3354). Nothing compares current.position with _transport or checks for an intervening Move. Each aim asks for confirmation, but the dialog text does not flag a stale prescan.

</details>

<a id="gui-part2-gui2-08"></a>

### GUI2-08 -- The roll-in-use guard checks only the last roll opened from the browser; the sheet's own roll can be deleted or renamed and is then recreated empty by Scan chosen frames

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2693-2707`, `tools/gui.py:2887-2891`, `tools/gui.py:3713-3714`, `tools/gui.py:2448-2470`, `tools/gui.py:3265-3282`, `tools/gui.py:2810-2863`

After a walk in this session, that walk's folder, which the open sheet belongs to, isn't protected. Delete removes survey.json, the prescans and roll.json, and Rename moves them. The next Scan chosen frames recreates the old path and scans into a folder with no walk (and, after a Rename, with its sheet state orphaned under the old name). Meanwhile a roll opened earlier from the browser stays 'the roll open in this window' until restart, even after it was replaced or failed to read.

**Evidence (from the code):**

```text
`loaded = self._loaded_roll and Path(self._loaded_roll).resolve()` ... `if loaded and Path(summary["folder"]).resolve() == loaded:` (2700-2702). `_loaded_roll` is assigned only in open_roll (`self._loaded_roll = folder`, 2889, before the read that may fail) and never cleared. A walk's folder goes to `_sheet_roll` (3714), not `_loaded_roll`. `_roll_folder` returns `sheet if inside else ...` for the stored path (2469), and `_write_approved` runs `folder.mkdir(parents=True, exist_ok=True)` (3281).
```

**Failure scenario:** The operator walks strip X (folder rolls/2026-09-27-101500) and, still deciding in the sheet, opens Rolls... and renames that folder to 'holiday'. The rename is allowed. Scan chosen frames writes approved.json and frames into a recreated rolls/2026-09-27-101500 with no survey.json. The walk and its prescans sit in 'holiday'. Reopening either folder shows half a roll.

**Fix:** Also protect `_sheet_roll` (and `session.last_roll_dir` while a job runs). Clear or replace `_loaded_roll` when a new walk or sheet takes over, and set it only after read_survey succeeds.

<details><summary>Second reader's check</summary>

_roll_is_busy protects only `_loaded_roll` (2700-2702). That is set in open_roll before read_survey can fail (2889) and is never cleared (grep: lines 508, 2700, 2889 only). A walk's folder goes to `_sheet_roll` (3714), which the guard ignores. After a rename or delete, _roll_folder still returns the old path, because `resolve()` is non-strict and its parent is still `rolls` (2462-2469). _write_approved then runs `folder.mkdir(parents=True, exist_ok=True)` (3281), and the roll scans into a recreated folder with no walk.

</details>

<a id="gui-part2-gui2-09"></a>

### GUI2-09 -- Roll Export (and Save all) applies the window's current mono setting, not the roll's own

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:2751-2752`, `tools/gui.py:2762-2766`, `tools/gui.py:4332-4333`, `tools/gui.py:5365-5375`, `tools/gui.py:4284`, `rps7200/session.py:2440-2441`

A roll's frameNN.tif files were written mono or not according to that roll's film (`wants_mono(job.mono, job.film)`). Export re-derives them with whatever film the main window happens to be set to now. A colour roll exported while the window is on B&W comes out as single-channel files. A B&W roll exported while the window is on colour comes out as 3- or 4-channel files, unlike its own frames. Save all does the same for every pass in the session, whatever film each was scanned on.

**Evidence (from the code):**

```text
on_export_rolls: `mono, channel = self.v_mono.get(), self.v_mono_channel.get()` (2752), passed to `self._deliver_one(item, path, quality, mono, channel)` (2765). `_deliver_one`: `if mono: full = to_monochrome(full, mono_channel)` (4332-4333). The export items built by roll_exports carry no mono field (5365-5375), although every roll records `"mono": job.mono, "mono_channel": job.mono_channel` in its settings (session.py:2440-2441).
```

**Failure scenario:** The operator scans a B&W roll, switches the window to colour negative for the next strip, then exports the B&W roll from Rolls...: it comes out RGB. Or, with B&W selected, exports last week's colour roll: every frame is reduced to one channel, and the log says only ', one channel'.

**Fix:** Carry `mono` and `mono_channel` from the roll's settings (and for single passes, from each pass's own meta) into the export items, and use those in `_deliver_one`.

<details><summary>Second reader's check</summary>

on_export_rolls reads `self.v_mono.get(), self.v_mono_channel.get()` (2752) and passes them to _deliver_one, which applies `to_monochrome` (4332-4333). roll_exports' items carry no mono (5365-5375), although the roll's settings record `mono`/`mono_channel` (session.py:2440-2441), and the roll's own frames used `wants_mono(job.mono, job.film)` (session.py:2631). Save all does the same for all passes (4284).

</details>

<a id="gui-part2-gui2-10"></a>

### GUI2-10 -- The sheet's 'nudge registration between frames' option does nothing on a roll commissioned from the sheet, and the header claims it applies to the frames he did not adjust

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:8064-8072`, `tools/gui.py:7956-7962`, `tools/gui.py:3257-3263`, `tools/gui.py:6431-6453`, `rps7200/direct.py:4004-4005`, `rps7200/direct.py:4049`

**Doc claim:** tools/gui.py:7958-7960 (the sheet's header text shown to the operator) and 8066 (the option's label)

Every ticked frame carries an approval, including 'as walked' zeros and frames the detector could not read (source 'none'). So the automatic nudge never runs on any frame of a sheet roll. The sheet still offers the option, stores and restores it, reports it as a difference from the main window, and the header implies it covers the unadjusted frames. An operator relying on it for frames the reader could not place gets them held where they were walked.

**Evidence (from the code):**

```text
Sheet option: `("correct", "nudge registration between frames")` (8066). Header: 'Positions you set are used as given -- nothing moves until you commission the scan, and the automatic nudge does not apply to frames you adjust.' (7957-7960). approved_from_sheet emits an `Approved` for every ticked frame (6434-6453). The driver runs `held = approved.get(index) if approved else None; if held is not None and holding:` ... `elif correct or correct_dry_run:` (direct.py:4004-4049). The GUI's own comment admits: '`correct` cannot act on a single frame of a commissioned roll ... See TODO.md: the tick itself should go.' (3257-3262).
```

**Failure scenario:** The edge reader reads 10 of 15 frames. The operator leaves 'nudge registration' ticked, expecting the other 5 to be corrected automatically. All 15 are held to their approvals, and the 5 unread frames are scanned exactly where the walk left them.

**Fix:** Remove the option from the sheet, or give frames without a decision (source 'none' and offset 0) no Approved, so `correct` applies to them. Correct the header text.

<details><summary>Second reader's check</summary>

approved_from_sheet emits an Approved for every ticked frame, including offset 0 / source 'none' (6434-6453). In scan_roll, `held = approved.get(index)` and `if held is not None and holding` / `elif held is not None` both run before `elif correct or correct_dry_run` (direct.py:4004-4049). So `correct` never acts on a sheet roll's frame, even after holding is switched off. The GUI's own _approved_note comment admits this (3257-3262). The sheet still offers the option (8066), and its header claims the nudge applies to the frames the operator did not adjust (7957-7960).

</details>

<a id="gui-part2-gui2-11"></a>

### GUI2-11 -- The histogram's 'at full' and 'near full' figures are measured on shading-corrected pixels, not on the raw values that hit the rail

**Severity** medium · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:4404-4444`, `tools/gui.py:4446-4454`, `tools/gui.py:4590-4593`, `tools/gui.py:6859-6873`, `rps7200/preview.py:349-371`, `rps7200/shading.py:270-280`, `rps7200/session.py:2705-2730`

**Doc claim:** tools/gui.py:6866-6868 and rps7200/preview.py:356-358

With about 39% column fall-off, bright centre columns have gain < 1, so raw pixels at 65535 (true sensor clipping) come out below full scale after correction and are not counted. Dim edge columns have gain > 1, so correction clamps unclipped raw values at 65535 and they are counted as 'at full'. The panel that exists to judge exposure against the rail therefore under-reports real rail clipping in the middle of the frame and reports correction clamps as sensor clipping at the edges. The readout labelled 'exact' (pixel_readout) likewise shows corrected values, not what the sensor delivered.

**Evidence (from the code):**

```text
The working copy is `preview.downscale(image, ...)` of the delivered, corrected pass (session.py:2722). The full-resolution level is `image, _ = library.corrected(entry)` (gui.py:4593). Correction scales each column by `gain = mean / light` and clamps: `vals = image[:, : loc.size, c].astype(np.float64) * gain` ... `np.clip(vals, 0, maxval, out=vals)` (shading.py:270-280). The panel's docstring says: 'Blue reaches the rail first on this scanner ... which is the whole reason these numbers exist' (gui.py:6866-6868), and preview.clipping says '*At* full scale is information already destroyed' (preview.py:356-357).
```

**Failure scenario:** A blue channel clipped at the rail across the centre of a frame reads 0.00% at full because those pixels are scaled to about 52000. The operator keeps the exposure. The raw bytes in the library confirm the clipping, but nothing on screen showed it.

**Fix:** Measure clipping on the raw pixels (the library entry's scan.tif via library.load, or the raw copy the session already has) and label it as the sensor rail. If a delivered-file histogram is wanted, show it separately and say it is corrected.

<details><summary>Second reader's check</summary>

The histogram measures result.image, the downscaled delivered copy (session.py:2722, via _deliver of the corrected pass), or the output of library.corrected (4593). Both are shading-corrected: per-column gain = mean/light, then clip (shading.py:270-283). So raw 65535 in bright centre columns (gain<1) lands below the rail and is not counted. Edge columns with gain>1 are clamped to 65535 and counted. The panel's docstring (6859-6873) and preview.clipping's (356-358) present this as the sensor rail. It is a design and labelling defect, not data loss: raw pixels are intact in the library.

</details>

<a id="gui-part2-gui2-a1"></a>

### GUI2-A1 -- A contact sheet opened while a walk is still running is never rebuilt: walk end only raises it, so frames walked after it opened have no cell and cannot be ticked

**Severity** medium · **Category** user-error · **Verdict** found-by-verifier

**Where:** `tools/gui.py:1543`, `tools/gui.py:3736`, `tools/gui.py:774`, `tools/gui.py:2486-2517`, `tools/gui.py:3666-3669`, `tools/gui.py:7834`

During a fresh walk, once the first prescan arrives, the Contact sheet button (still enabled from the previous walk) or its shortcut opens a sheet with only the frames walked so far. When the walk finishes, on_contact_sheet finds the sheet alive and just lifts it. The frames walked after it opened never get a cell, a tick or an approval. Nothing on screen says the sheet is partial, apart from the '{n} frames walked' header, which was counted when it was built.

**Evidence (from the code):**

```text
b_sheet is only ever set from `self.b_sheet.configure(state="normal" if self.survey else "disabled")` (3736) or `state="normal"` (2944); it is not disabled when a walk starts, and the `contact_sheet` shortcut calls on_contact_sheet directly (774). on_contact_sheet checks only `if not self.survey` and `if self.sheet is not None and self.sheet.alive(): self.sheet.top.lift(); ... return` (2486-2500). The sheet copies its frames at build time: `self.frames = [r for r in frames if r.image is not None]` (7834). At walk end: `if self.survey: self.on_contact_sheet()` (3668-3669).
```

**Failure scenario:** The operator opens the sheet at frame 5 of a 12-frame walk to start ticking. At the end of the walk the same sheet comes to the front, still showing frames 1-5. He commissions it, and frames 6-12 are not scanned.

**Fix:** Rebuild the sheet (via _close_sheet and _open_sheet with the kept state) at walk end, or refuse to open it while `_surveying`. Disable b_sheet when a fresh walk clears the survey.

<a id="gui-part2-gui2-12"></a>

### GUI2-12 -- The sheet's option panel allows combinations the driver refuses only after the film has been wound (7200 dpi; infrared on B&W or Kodachrome)

**Severity** low · **Category** user-error · **Verdict** partly

**Where:** `tools/gui.py:8042-8072`, `tools/gui.py:3103-3118`, `tools/gui.py:1787`, `tools/gui.py:1859-1877`, `rps7200/direct.py:607-618`, `rps7200/direct.py:2943-2947`, `rps7200/direct.py:3864-3871`, `rps7200/direct.py:4146-4167`, `rps7200/session.py:2286-2290`

Neither the sheet nor the main window's Roll refuses an uncorrectable resolution (7200 dpi over FULL_FRAME needs 10344 columns, above MAX_SHADING_COLUMNS 5172) before submitting. The sheet also lets infrared through for B&W and Kodachrome, which the main window blocks (1870-1872). Either way the roll seeks first, and a 7200 dpi roll then prescans, holds and fails up to max_failures (3) frames before giving up.

**Evidence (from the code):**

```text
The sheet offers `pick("scan dpi", "dpi", [str(d) for d in DPI_LADDER], ...)` with `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`, and independent checkboxes for `("ir", "infrared (RGBI)")`. on_scan_chosen checks only `not 25 <= dpi <= 7200` (3113). The main window forces IR off for blind films (`if blind: self.v_ir.set(False); self.c_ir.configure(state="disabled")`, 1870-1872), but the sheet doesn't. `DirectScanner.correctable_at` exists 'for a caller that wants to refuse before opening the device', and the GUI never calls it. The session seeks first (`seek(self._scanner, first, ...)`, session.py:2288) and only then iterates scan_roll, whose IR check raises (direct.py:3864-3871). At 7200 dpi each frame is prescanned and held before scan() refuses it.
```

**Failure scenario:** The operator picks 7200 in the sheet for 'the best quality'. The roll seeks, prescans and holds frame 1, and scan() raises ShadingUnavailable. The same happens on each frame until the failure limit, spending minutes of transport and prescans for nothing.

**Fix:** In on_scan_chosen, refuse up front using `DirectScanner.correctable_at(dpi)` and `supports_infrared(film)`, and apply the main window's `_sync_infrared` rule to the sheet's controls.

<details><summary>Second reader's check</summary>

Confirmed that the sheet offers 7200 dpi (DPI_LADDER, 127) and independent IR checkboxes (8064-8072). on_scan_chosen checks only 25..7200 (3113). correctable_at is never called anywhere in tools/gui.py. The session seeks first (session.py:2286-2290), and scan_roll then raises for IR on a blind film (direct.py:3864-3871) or fails each frame's scan() via uncorrectable() (direct.py:2943-2947) until max_failures=3 (session.py:1469; direct.py:4148-4167). The finding is wrong that this bypasses a guard the main window enforces, except for IR: the main window's Roll does not check correctable_at either (on_roll uses `_dpi` 25..7200, 1787).

</details>

<a id="gui-part2-gui2-13"></a>

### GUI2-13 -- A reopened walk loses the link between each reference prescan and its library entry: approved.json gets reference_entry ""

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:5176-5200`, `tools/gui.py:6443-6447`, `tools/gui.py:3317`, `rps7200/session.py:2549-2566`, `rps7200/session.py:2666-2675`, `rps7200/direct.py:3346-3349`

A walk closed without commissioning and reopened in a later session has results with no entry. The approvals written for it name no library entry for the reference picture the hold was checked against. That picture is the prescanNN.tif in the roll folder (corrected, arranged), which Delete removes as 're-derivable'. Re-evaluating a hold later then relies on joining by roll name and number, which is ambiguous once a frame has been walked twice.

**Evidence (from the code):**

```text
read_survey: `entry=Path(entries[number]) if number in entries else None` (5196), where `entries` come only from approved.json's `reference_entry` (read_approved 5471-5472). approved_from_sheet: `reference_entry=str(getattr(result, "entry", "") or "")` (6447). A dry-run walk files each prescan with `self._file(...)` and no `on_filed`, so survey.json's record carries no entry (session.py:2549-2566). The hold record the driver files has `"target_mm": ..., "source": source` and no reference identity (direct.py:3346-3349).
```

**Failure scenario:** Walk a strip, close the window, reopen the roll the next day and commission it. approved.json has `"reference_entry": ""` for every frame. Months later the reference used for frame 7's hold cannot be identified with certainty from the library.

**Fix:** Record the prescan entry in survey.json's frame record (on_filed for dry-run prescans). In read_survey, fall back to roll_membership(kind=prescan) with the folder. Also copy the reference entry into the frame's `approved` mark in the library record.

<details><summary>Second reader's check</summary>

read_survey takes `entry` only from approved.json's reference_entry (5176, 5196). A dry-run prescan is filed with no on_filed (session.py:2549-2566), and survey.json records `prescan`, `prescan_rotation` and `prescan_flipped` but no entry (session.py:2666-2675). So a walk reopened in a later session yields results with entry=None, and approved_from_sheet writes `reference_entry` "" (6447). The link is recoverable only by joining on roll name, number and kind=prescan, which is ambiguous after a re-walk.

</details>

<a id="gui-part2-gui2-14"></a>

### GUI2-14 -- Commissioning a sheet opened from outside session.rolls scans into a new folder without carrying the walk, so the scanned roll has no survey

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:2448-2470`, `tools/gui.py:3162-3166`, `tools/gui.py:2248-2266`, `tools/gui.py:5765-5801`

A roll opened with `--open-roll /some/other/rolls/X` (or after `--rolls` changed) and then commissioned writes approved.json, roll.json and the frames into rolls/X here, but survey.json and prescanNN.tif stay where they were read from. Reopening rolls/X later says 'scanned without walking the strip first, so there is no contact sheet', and the positions cannot be reviewed against their prescans.

**Evidence (from the code):**

```text
`_roll_folder`: `return sheet if inside else roll_dir(rolls, sheet.name)` (2469), whose docstring says 'A walk added to it copies that walk across first (`carry_walk`)'. on_scan_chosen calls `folder = self._roll_folder()` and `self._write_approved(approved, folder)` (3162-3166) and never calls carry_walk. Only on_roll's keep-walk branch does (2253-2255).
```

**Failure scenario:** The operator opens a walk from a backup disk with --open-roll and commissions frames 1-5. rolls/<name> holds frames and approved.json but no walk. A resume from the browser shows no sheet.

**Fix:** Call carry_walk(self._sheet_roll, folder) in on_scan_chosen, as on_roll does, before writing approved.json.

<details><summary>Second reader's check</summary>

_roll_folder returns `roll_dir(rolls, sheet.name)` for a sheet outside session.rolls (2469). on_scan_chosen writes approved.json there and submits the roll (3162-3200) without calling carry_walk. Only on_roll's keep branch calls it (2253-2255). A further consequence: if rolls/<same name> already exists as an unrelated roll, the commission merges approved.json into it and overwrites its frameNN.tif.

</details>

<a id="gui-part2-gui2-15"></a>

### GUI2-15 -- Stale transport-limit texts: the aim refusal cites '8 sub-frame moves', snap_offset says 'eight commands', and the lattice note says 2.57 units; aim retypes the planner's arithmetic and a 1.1 s per move figure

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:5046-5055`, `tools/gui.py:5064-5065`, `tools/gui.py:5887-5897`, `tools/gui.py:5928-5930`, `tools/gui.py:7460-7461`, `tools/gui.py:228-241`, `rps7200/protocol.py:275`

**Doc claim:** tools/gui.py:5050-5052 (dialog text), 5893-5894, 5929-5930

The refusal tells the operator a wrong reason: the limit is one command (about 88.8 units), not eight chained moves. The inline docs contradict the constants they sit beside. The retyped step arithmetic and time constant are a second home that will drift, which is the pattern CLAUDE.md warns about.

**Evidence (from the code):**

```text
`_aim`: `f"would take more than {MAX_FINE_STEPS} sub-frame moves. Past that the calibration goes sub-linear"` (5050-5052), while `MAX_TRAVEL_MM = MAX_FINE_MM` is 'exactly one command' (228-241). `steps = max(1, -(-int(abs(want) * 1000) // int(MAX_FINE_MM * 1000)))` (5055) is a copy of plan_nudges' arithmetic. `f"{steps * 1.1:.0f} s"` (5065) and `seconds = moves * 1.1` (7461) use a constant that isn't in DirectScanner. snap_offset: 'Clamped to what eight commands can chain, which is `MAX_TRAVEL_MM`' (5893-5894). ADJUST_STEPS comment: 'the lattice is 2.57 units off zero' (5929), whereas COMMAND_UNITS = 1.84 (protocol.py:275) makes param 1 = 2.84 units.
```

**Failure scenario:** The operator clicks 100 units from an edge and is told it would take more than 8 moves and go sub-linear. It is actually refused because one command reaches 88.8 units.

**Fix:** Say 'more than one command delivers (88.8 units)'. Use `len(plan_nudges(want))` for the step count. Correct the snap_offset and lattice comments.

<details><summary>Second reader's check</summary>

_aim refuses above MAX_TRAVEL_MM, which equals MAX_FINE_MM, one command (221, 241). The message still says 'more than {MAX_FINE_STEPS} sub-frame moves... sub-linear' (5050-5052). MAX_FINE_STEPS is itself retyped as 8 in gui.py:226, beside session.py:1201. The step arithmetic (5055) and the 1.1 s constant (5065, 7461) are local copies. snap_offset's docstring says 'eight commands' (5893-5894). The ADJUST_STEPS comment says '2.57 units' (5929), while param 1 = 1 + COMMAND_UNITS 1.84 = 2.84. plan_nudges' docstring (session.py:1221-1222) also still says param_for_mm 'clamps silently at param 8'.

</details>

<a id="gui-part2-gui2-16"></a>

### GUI2-16 -- The shortcut editor says no shortcut starts a scan, but Prescan, Scan and Roll are bound by default

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:7027-7033`, `tools/gui.py:765-772`, `rps7200/shortcuts.py:112-117`

**Doc claim:** tools/gui.py:7031-7033 (text shown in the Shortcuts window)

The keys do ask before acting, but the statement is false as written and describes a guarantee the code doesn't give.

**Evidence (from the code):**

```text
The editor text reads: 'No shortcut starts a scan, calibrates, or moves film. Those cost minutes of the scanner or move your negative, and a slip on the keyboard is not a decision to do either.' (7031-7033). `_actions` has `"prescan": lambda: self._confirm_then(...)`, `"scan": ...`, `"roll": self.on_roll` (765-772), with defaults `<{ACCEL}-Return>`, `<{ACCEL}-Shift-Return>`, `<{ACCEL}-Key-b>` (shortcuts.py:112-117).
```

**Failure scenario:** An operator who trusts the text rebinds Prescan to a key he presses often, assuming it cannot start a pass.

**Fix:** Say that Prescan, Scan and Roll have keys and always ask first, and that calibration and film moves have none.

<details><summary>Second reader's check</summary>

The editor text (7031-7033) says no shortcut starts a scan or moves film. _actions binds prescan, scan and roll (765-772) to defaults in shortcuts.py:112-117. They do ask first (_confirm_then 846-870; on_roll asks). Separately, shortcuts.py:148-149 labels adjust_left and adjust_right 'Move the film one step left/right', although those only change the planned offset.

</details>

<a id="gui-part2-gui2-17"></a>

### GUI2-17 -- The sheet header says the film is 'rewound to the start of the strip first'; the roll actually goes to the first ticked frame

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:7956-7962`, `tools/gui.py:3145`, `tools/gui.py:6481-6490`, `tools/gui.py:3188-3190`

**Doc claim:** tools/gui.py:7960-7961

The seek goes to the first ticked frame, forward or backward, not to the start of the strip. The text contradicts on_scan_chosen's own docstring and seek_note.

**Evidence (from the code):**

```text
Header: 'The film is rewound to the start of the strip first, and every frame nobody ticked costs its advance only.' (7960-7962). on_scan_chosen: `start_at, span = chosen_span(numbers)` (3145), where chosen_span returns `chosen[0], chosen[-1] - chosen[0] + 1` (6489-6490), and `Roll(frames=span, start_at=start_at, ...)` (3188-3189).
```

**Failure scenario:** The operator expects a rewind (and times the run by it) and sees the film advance instead. The cost estimate in the confirm dialog is right; the sheet text is wrong.

**Fix:** Reword it: the film goes to the first ticked frame, and unticked frames after that cost their advance.

<details><summary>Second reader's check</summary>

The header says 'rewound to the start of the strip first' (7960-7961). on_scan_chosen uses chosen_span (6489-6490) and `Roll(frames=span, start_at=start_at)` (3188-3189), so the seek goes to the first ticked frame.

</details>

<a id="gui-part2-gui2-18"></a>

### GUI2-18 -- The Delete-roll dialog calls everything in a roll folder except approved.json re-derivable, but no tool rebuilds a walk, and the decision count ignores positions

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:2810-2835`

**Doc claim:** tools/gui.py:2813-2816 and dialog text 2826-2834

No code rebuilds survey.json, roll.json (wanted/done/start_at, transport positions, per-prescan arrangement) or prescanNN.tif from the library. Export re-corrects frames, but a walk deleted this way can never be reopened as a sheet. The contact-sheet decisions of a walk not yet commissioned (kept in gui-settings) are also not mentioned. The count of hand decisions skips positions, although in practice rotation is written for every approved frame, which hides this.

**Evidence (from the code):**

```text
Docstring: 'Everything in a roll folder is re-derivable from the library **except `approved.json`** -- the frames and prescans can be rebuilt' (2813-2815). Dialog: 'the frames can be rebuilt from them' (2828-2829). `decided = sum(1 for s in summaries if any(read_approved(s["folder"])[1:3]))  # turns/flips` (2822-2823), which counts rotations and flips but not offsets (index 0).
```

**Failure scenario:** The operator deletes a walked but uncommissioned roll, believing the dialog that it can be rebuilt. The walk's survey is gone for good, and the prescans survive only as loose library entries.

**Fix:** Name survey.json and roll.json as not rebuildable, include offsets in the count, and mention stored sheet decisions.

<details><summary>Second reader's check</summary>

The docstring and dialog say the frames and prescans can be rebuilt (2813-2815, 2828-2829). No code rebuilds survey.json, roll.json or prescanNN.tif (the only survey.json writers are session, scan_roll and carry_walk). `read_approved(...)[1:3]` counts rotations and flips only; offsets are index 0 (2822-2823). Sheet state held in gui-settings is not mentioned.

</details>

<a id="gui-part2-gui2-19"></a>

### GUI2-19 -- Save As runs on the UI thread without error handling

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:4221-4235`, `tools/gui.py:4309-4341`

A full disk, a permission problem, or a missing or corrupt library entry raises into Tk's callback handler (stderr). The operator gets no message and no file. Correcting and deflating a 142-570 MB frame also freezes the window for seconds.

**Evidence (from the code):**

```text
`said = self._deliver_one(result, path, jpeg_quality(self.v_jpegq.get()), self.v_mono.get(), self.v_mono_channel.get())` (4232-4233), with no try/except. `_deliver_one` calls `library.corrected(result.entry)` and `export.write(...)`, which can raise OSError or ValueError. on_save_all wraps the same call: `except Exception as exc: self._saves.put(("line", f"could not save ..."))` (4296-4300).
```

**Failure scenario:** The operator Saves As onto a full USB stick. Nothing appears in the log, no file is written, and he assumes it saved.

**Fix:** Catch and report failures as on_save_all does, and run it on the writer thread with `_start_writing`.

<details><summary>Second reader's check</summary>

on_save_as calls _deliver_one with no try/except (4232-4233). No report_callback_exception override exists in gui.py (grep). The exception goes to Tk's default stderr handler, and the operator sees nothing. The correction and deflate run on the UI thread.

</details>

<a id="gui-part2-gui2-20"></a>

### GUI2-20 -- 'Nothing already there is overwritten' is untrue for the JPEG sidecar DNG, for the no-Pillow TIFF fallback, and after 999 name clashes

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:2746-2747`, `tools/gui.py:4276-4277`, `tools/gui.py:2762-2764`, `tools/gui.py:4293`, `rps7200/export.py:181-192`, `rps7200/session.py:2961-2973`

_unclaimed checks the .jpg name, but the file written can be a .tif, or a .jpg plus a .dng. An existing TIFF or DNG with that stem is replaced without a word.

**Evidence (from the code):**

```text
`_unclaimed` checks only the primary name and ends with `return wanted` when 2..999 are taken (session.py:2973). export.write: `path = path.with_suffix(SUFFIXES["tiff"]); tiff.write(str(path), image, ...)` when Pillow is missing (export.py:187-188), and `return _write_infrared(path, image, resolution)` writes `<stem>.dng` beside the JPEG (191). The dialogs promise 'Nothing already there is overwritten' (2746-2747, 4276-4277).
```

**Failure scenario:** The operator exports a roll as TIFF, later switches to JPEG on a machine without Pillow, and exports into the same folder. Each `X.jpg` is free, so the write falls back to `X.tif` and overwrites the earlier export.

**Fix:** Resolve the final path, including the fallback suffix and the sidecar, before writing, and check all of them for clashes. Never return `wanted` once every candidate is taken.

<details><summary>Second reader's check</summary>

_unclaimed (session.py:2961-2973) tests only the requested name and returns `wanted` after 999 clashes. export.write can switch to a .tif (export.py:185-190) or add `<stem>.dng` via _write_infrared (export.py:191, 130-150). Neither alternate path is checked, contradicting 'Nothing already there is overwritten' (2746-2747, 4276-4277).

</details>

<a id="gui-part2-gui2-21"></a>

### GUI2-21 -- Exports of rolls made by tools/scan_roll.py are named '0dpi'

**Severity** low · **Category** bug · **Verdict** partly

**Where:** `tools/gui.py:5351`, `tools/gui.py:5373`, `tools/gui.py:5577`, `tools/gui.py:5619`, `tools/scan_roll.py:421-422`, `rps7200/session.py:692`

roll_exports reads settings['resolution'] where tools/scan_roll.py writes settings['dpi'], ignoring SETTING_ALIASES/manifest_settings (which roll_summary already applies for its own 'resolution' field). Every exported frame of a CLI-made roll is therefore named '..._0dpi'.

**Evidence (from the code):**

```text
roll_exports: `settings = summary.get("settings") or {}` ... `meta={"resolution_dpi": settings.get("resolution") or 0, ...}` (5351, 5373). scan_roll.py writes `"dpi": args.dpi` inside settings (scan_roll.py:422), and the alias table `SETTING_ALIASES = {"resolution": "dpi"}` (session.py:692) is not consulted here, although roll_summary uses manifest_settings for its 'resolution' column (5619).
```

**Failure scenario:** The operator exports a roll scanned with tools/scan_roll.py at 1800 dpi and gets `rollname_frame01_0dpi.tif`.

**Fix:** Take the resolution from summary['resolution'] (manifest_settings) or from each entry's own scan.resolution_dpi.

<details><summary>Second reader's check</summary>

Confirmed that scan_roll.py writes the resolution only as settings['dpi'] (421-422). roll_summary's `settings` is the raw block (5577), and roll_exports reads `settings.get('resolution') or 0` (5373), so batch_name yields `_0dpi` (5261-5268). The rotation part is not a key mismatch: scan_roll.py writes no rotation at all, so 0 is the only value available, not a wrongly keyed one.

</details>

<a id="gui-part2-gui2-22"></a>

### GUI2-22 -- The full-resolution view ignores library.corrected's correction state, so a raw entry is shown as 'the scan's own pixels' without saying so

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:4587-4601`, `tools/gui.py:4612-4619`, `rps7200/library.py:583-601`

_deliver_one reports a non-'applied' state, but the viewer doesn't. For legacy or reference-less entries (e.g. reopened ones), the big view and the histogram switch to uncorrected pixels without a word, which CLAUDE.md calls the thing this driver stopped delivering.

**Evidence (from the code):**

```text
`image, _ = library.corrected(entry)` (4593) discards the record. `_loaded` says `f"{self.current.label}: now showing the scan's own {image.shape[1]}x{image.shape[0]} pixels"` (4618-4619). library.corrected returns raw pixels with `record["corrected"]` set to 'no reference', 'deliberately raw' or 'raw -- correction was asked for' (library.py:589-596).
```

**Failure scenario:** A reopened old entry with no shading.npz. The view changes from the corrected working copy to a raw frame with visible column fall-off, and the log says only 'now showing the scan's own pixels'.

**Fix:** Carry the record's `corrected` value through `_reads` and say it in the log and caption whenever it isn't 'applied' or 'already'.

<details><summary>Second reader's check</summary>

`image, _ = library.corrected(entry)` discards the record (4593), and _loaded logs 'now showing the scan's own ... pixels' (4618-4619) whatever `record['corrected']` was ('no reference', 'deliberately raw', ...). _deliver_one does surface a non-'applied' state (4338).

</details>

<a id="gui-part2-gui2-23"></a>

### GUI2-23 -- Delete-pass rebuilds the library index on the UI thread, racing the writer thread's reindex; a failed rmtree leaves a half-deleted entry

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `tools/gui.py:4492-4498`, `rps7200/library.py:399`, `rps7200/library.py:1045-1065`

Deleting a pass while a roll is filing can interleave two non-atomic writes of index.json (derived, so recoverable with `make verify` or reindex). On Windows a file held open, for example by the `_load_full` thread that started when the pass was selected, makes rmtree fail part-way. Some files are then gone, scan.json may remain, and roll_entry_index or Export can still join the broken entry.

**Evidence (from the code):**

```text
`shutil.rmtree(entry); library.reindex(entry.parent)` (4494-4495), with no busy check. library.save ends in `reindex(root)` (library.py:399) on the FrameWriter thread. reindex writes with `index.write_text(json.dumps(summary, indent=2), encoding="utf-8")` (1064), which is neither atomic nor locked.
```

**Failure scenario:** The operator selects a 3600 dpi frame (its full-resolution read starts) and immediately deletes it with its entry. On Windows rmtree removes raw.bin.gz, then fails on the open scan.tif. The log says 'could not delete', and the entry is left without its raw bytes.

**Fix:** Rename the entry aside first (atomically), then delete it. Serialise index writes with the writer, or make reindex write atomically.

<details><summary>Second reader's check</summary>

on_delete runs `shutil.rmtree(entry); library.reindex(entry.parent)` on the UI thread with no busy check (4492-4498). reindex uses a plain write_text of index.json (library.py:1064), and FrameWriter's library.save also calls reindex. rmtree is not atomic, so a partial failure leaves a half entry that roll_entry_index still joins (it reads only scan.json). The Windows open-file case is narrow, because library.corrected holds files only briefly.

</details>

<a id="gui-part2-gui2-24"></a>

### GUI2-24 -- Turning frames in the sheet moves the window's 'where is the film' forecast to those frames' walk positions, and 'rotate all' redraws and reloads once per frame

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:8330-8360`, `tools/gui.py:3968-3989`, `tools/gui.py:3148`, `tools/gui.py:6569-6613`, `tools/gui.py:8407-8427`

`_transport` should be what the transport last reported. After arranging an old or reopened walk's frames, it holds the last-turned frame's walk position, so the confirm dialog's 'The transport last said the film is on frame N' and its time are wrong (the seek itself re-reads the counter). 'Rotate all' on a 36-frame sheet also makes 36 filmstrip redraws, 36 `_show` calls and up to 36 full-resolution load threads.

**Evidence (from the code):**

```text
`_orient` calls `self.gui.remember_arrangement(result)` (8359). remember_arrangement does `if plausible(result.position): self._transport = result.position` (3975-3976), then `self._show(result)` and `self._redraw_strip()` (3988-3989). on_scan_chosen forecasts from it: `move, move_s = seek_note(self._transport, start_at)` (3148).
```

**Failure scenario:** The film sits on frame 17. The operator turns frame 3 in the sheet. The dialog then says the film is on frame 3 and that nothing moves before the first frame, while the roll actually winds back 14 frames.

**Fix:** Don't update `_transport` from remember_arrangement when it is called for arrangement only. Batch the per-frame redraws in `_all`.

<details><summary>Second reader's check</summary>

_orient calls gui.remember_arrangement (8359). That sets `_transport = result.position` when plausible, then calls _show (which starts _load_full for filed passes) and _redraw_strip (3968-3989). seek_note uses _transport (3148). _all loops _orient over every frame (8420-8421). _orient's own docstring (8337-8340) claims it 'leaves the filmstrip alone -- the callers below do that once each', which the code contradicts.

</details>

<a id="gui-part2-gui2-25"></a>

### GUI2-25 -- --demo with an explicit --library or --rolls writes demo scans and rolls into the real library or rolls, contradicting the comment that --demo pins rolls under demo/

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `tools/gui.py:8816-8818`, `tools/gui.py:8856-8889`, `tools/gui.py:8868-8874`, `tools/gui.py:8779-8783`, `rps7200/demo.py:903`

**Doc claim:** tools/gui.py:8868-8874

An explicit --rolls rolls with --demo --open-roll aligned-strip makes `_roll_folder` return the real walk folder (it is under session.rolls). A demo commission then writes approved.json, roll.json and frames into the real walk. --library library files demo entries into the real library. Those entries are marked `"demo": True` in meta, so they are distinguishable, but they take part in reconstruct, verify and Export joins.

**Evidence (from the code):**

```text
`root=args.library or str(home / "library")`, `rolls=args.rolls or str(home / "rolls")` (8886-8888). The comment says: 'a roll commissioned from it goes to `_roll_folder`, which is always under `session.rolls`, and --demo pins that under `demo/`, so nothing the window does afterwards can write back into the walk it is showing' (8871-8874). DEMO_ROOT's comment says the demo 'must not file into' the real library (8779-8782).
```

**Failure scenario:** `uv run python tools/gui.py --demo --rolls rolls --open-roll aligned-strip`, then Scan chosen frames. The real rolls/aligned-strip gets a roll.json of demo frames and an approved.json merged with demo decisions.

**Fix:** With --demo, refuse --library or --rolls paths that resolve to the real library or rolls (or anything outside demo/), or at least warn.

<details><summary>Second reader's check</summary>

main() builds `root=args.library or str(home/'library')` and `rolls=args.rolls or str(home/'rolls')` (8885-8889) with no check under --demo. The comment at 8868-8874 says --demo pins rolls under demo/, but an explicit --rolls rolls makes _roll_folder resolve to the real walk (2462-2469). The same holds for --reference, whose cached shading reference path is also not pinned under demo.

</details>

<a id="gui-part2-gui2-26"></a>

### GUI2-26 -- Scan chosen frames closes the sheet before asking; Cancel, busy or a calibration prompt leaves it closed

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:8690-8700`, `tools/gui.py:3086-3102`, `tools/gui.py:3149-3161`

Decisions survive in sheet_state, but the sheet disappears on any refusal or Cancel, and the busy message tells him to 'press this again' on a window that no longer exists. The calibration prompt is parented on nothing, because `self.sheet.alive()` is already False.

**Evidence (from the code):**

```text
`_scan`: `self._dismiss()` then `self.gui.on_scan_chosen(picked, approved, options)` (8699-8700). on_scan_chosen can then return at the busy check, whose message says '... then press this again -- the ticks stay where they are.' (3092-3095), at `_calibration_missing`, at the dpi check, or when the confirm is cancelled (3149-3161).
```

**Failure scenario:** The operator presses Scan chosen frames, reads the time estimate and cancels. The sheet is gone and must be reopened from the main window, and the edge light and scroll position reset.

**Fix:** Ask first, then dismiss only when the roll is actually submitted.

<details><summary>Second reader's check</summary>

_ContactSheet._scan calls `self._dismiss()` before `gui.on_scan_chosen(...)` (8699-8700). on_scan_chosen can then return at busy (3086-3095, whose message says 'press this again'), at _calibration_missing (parent None because the sheet is no longer alive, 3097-3100), at the dpi check (3113-3118), or on Cancel (3149-3161). Decisions survive in sheet_state, but the sheet window is gone.

</details>

<a id="gui-part2-gui2-27"></a>

### GUI2-27 -- Turns made in a sheet reopened during a running roll change how arriving frames are shown but not how they are filed

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `tools/gui.py:3789-3791`, `tools/gui.py:8342-8359`, `tools/gui.py:3180-3182`, `tools/gui.py:3928-3944`, `rps7200/session.py:2486-2488`

The preview and Save As follow the new turn, while frameNN.tif, approved.json and Export follow the old one. The sheet doesn't say that changes made after commissioning don't reach the running roll.

**Evidence (from the code):**

```text
`_run_buttons` doesn't include b_sheet (3790-3791), so the sheet can be reopened mid-roll. `_orient` updates `self.orientations[("frame", n)]` through remember_arrangement (8359 → 3972), and `_arrange` uses that for arriving frames (3932). The running Roll's per-frame turns were fixed at submission: `self._frame_rotation = {a.number: a.rotation for a in job.approved}` (session.py:2486).
```

**Failure scenario:** During a roll the operator reopens the sheet and straightens frame 9 before it is scanned. The filmstrip shows frame 9 upright, but frame09.tif is written sideways.

**Fix:** Show commissioned sheets as read-only while their roll runs, or warn that turns apply to later commissions only.

<details><summary>Second reader's check</summary>

_run_buttons omits b_sheet (3789-3791). A turn in the sheet goes through remember_arrangement into `orientations[('frame', n)]` (3972), which _arrange uses for arriving frames (3932). The session fixed `_frame_rotation` from job.approved at submit (session.py:2487), and that is what _file uses for frameNN.tif (session.py:2776).

</details>

<a id="gui-part2-gui2-28"></a>

### GUI2-28 -- snap_offset silently clamps detector proposals and stored positions to one command

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:5897`, `tools/gui.py:6087-6109`, `tools/gui.py:6440`, `rps7200/session.py:1219-1224`

A frame the detector reads as 120 units off is captioned and delivered as 88.8 '(measured)', with nothing to say it was truncated. The hold then reports 'held' at the clamped target, and the frame remains about 31 units off.

**Evidence (from the code):**

```text
`want = max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, float(millimetres)))` (5897), applied to every detector proposal (`landed = snap_offset(value)`, 6099) and to every approval (`offset_mm=snap_offset(offsets.get(number, 0.0))`, 6440). plan_nudges' docstring says it raises 'deliberately rather than clamping ... a caller that cannot see its request was truncated will keep issuing commands against a ceiling it does not know is there' (session.py:1219-1224).
```

**Failure scenario:** A badly loaded strip with one frame far out. The caption shows '+88.8 units (measured)', the scan is held there, and part of the picture is still outside the aperture.

**Fix:** Keep the unclamped proposal in the note, and mark clamped frames in the caption and the confirm dialog.

<details><summary>Second reader's check</summary>

snap_offset clamps to ±MAX_TRAVEL_MM (5897). It is applied to detector proposals (_snap_proposals 6097) and to approvals (6440), with no marker that a value was clamped. plan_nudges' docstring argues against silent clamping (session.py:1219-1224).

</details>

<a id="gui-part2-gui2-a2"></a>

### GUI2-A2 -- Library entry paths written into approved.json, roll.json and roll_membership are relative to the window's working directory

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `tools/gui.py:8886`, `rps7200/library.py:258-259`, `rps7200/session.py:2020`, `tools/gui.py:3658`, `tools/gui.py:6447`, `tools/gui.py:5196`, `rps7200/session.py:1155`, `rps7200/session.py:786`

The recorded links from decisions and roll records to library entries are 'library/2026...' strings, and they resolve only when the process runs in the repo root. A window started from another directory (a desktop shortcut, or `--rolls` on another disk) reopens a roll whose results' entries do not exist. _deliver_one then silently falls back to the reduced preview (`(result.entry / "scan.tif").exists()` fails), the full-resolution view never loads, and on_delete treats the entry as absent. The provenance the library exists to preserve then depends on the CWD.

**Evidence (from the code):**

```text
ScanSession is built with `root=args.library or str(home / "library")` (8886), a relative path. library.save does `root = Path(root); path = _reserve(root, ...)` without resolving. The session emits `text=str(entry)` (session.py:2020), the GUI stores `r.entry = Path(event.text)` (3658), approved_from_sheet writes `reference_entry=str(getattr(result, "entry", "") or "")` (6447), and read_survey reads it back as `Path(entries[number])` (5196). roll.json stores `record["entry"] = str(entry)` (session.py:1155), and roll_membership stores `"folder": str(folder)` (session.py:786).
```

**Failure scenario:** The roll is walked and commissioned from the repo root. Weeks later the window is launched from another directory with --rolls and --library pointing at the repo. Reopening the roll gives results whose entry paths point nowhere, and Save As from the reopened sheet writes the ~430-px preview instead of the scan.

**Fix:** Resolve the library root once (Path(root).resolve()) in ScanSession/library.save, or store entry ids and resolve them against the library root when read.

<a id="gui-part2-gui2-29"></a>

### GUI2-29 -- The demo seam holds in this area: no `if demo` in the scan path

**Severity** info · **Category** demo-divergence · **Verdict** confirmed

**Where:** `tools/gui.py:377`, `tools/gui.py:545`, `tools/gui.py:7948-7954`, `tools/gui.py:3023-3026`, `tools/gui.py:3065-3069`, `tools/gui.py:4466-4480`

The second requirement is met in this area: the sheet-to-roll path (on_scan_chosen, _write_approved, Roll submission) is the same code in --demo and --look-only.

**Evidence (from the code):**

```text
`self.demo` is read only for the title (`root.title("Reflecta RPS 7200" + ("  --  demo" if demo else ""))`, 545). `look_only` changes only the sheet header text (7948) and one open_roll message (3023-3026). The scan button is not greyed, and on_scan_chosen runs and lets the backend refuse (3065-3069). on_delete's guard is a library-root check, not a demo branch (4470-4480).
```

**Failure scenario:** None.

**Fix:** Keep it this way. Consider a test asserting that `demo` and `look_only` are read only for text.

<details><summary>Second reader's check</summary>

`self.demo` is read only for the title (545), and `look_only` only for text (3026, 7948). No demo branch gates the scan button or on_scan_chosen. on_delete's guard is a library-root check (4466-4480).

</details>

<a id="gui-part2-gui2-30"></a>

### GUI2-30 -- _propose_positions and roll_line are used only by tests

**Severity** info · **Category** dead-code · **Verdict** confirmed

**Where:** `tools/gui.py:6039-6084`, `tools/gui.py:5745-5762`

Tests exercise a whole-survey path the window doesn't use, so they can pass while the EdgeWatch path diverges. The docstring's claim that both reach the same positions is tested elsewhere (tests/test_frame_edges.py per CLAUDE.md).

**Evidence (from the code):**

```text
The _propose_positions docstring says: 'The window no longer calls this: it reads a walk in the background ... (`frame_edges.EdgeWatch`)' (6050-6052). grep finds callers only in tests/test_gui.py (3534-3990) and research/. roll_line is called only in tests/test_gui.py:3078. The browser uses roll_cells.
```

**Failure scenario:** A change to `_merge_kept` or `_snap_proposals` is tested via `_propose_positions`, while the live `take_readings` path goes untested.

**Fix:** Move the tests to take_readings, `_open_sheet` or `_merge_kept`, or delete the unused helpers.

<details><summary>Second reader's check</summary>

`_propose_positions` (6039) and `roll_line` (5745) have no callers in tools/gui.py outside comments. All 31 references are in tests/test_gui.py.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| approved.json: the operator's and the detector's per-frame positions, turns and flips for a roll | <rolls>/<roll folder>/approved.json (+ approved.json.bak, the previous version) | JSON {roll, numbering:'strip', frames:[{number, offset_mm (float, millimetres, rounded to 4 dp), rotation (int), flipped (bool), reference_entry (CWD-relative library path or ""), source ('operator'\|'measured'\|'unconfirmed'\|'neighbours'\|'none'), as_walked?}]} | decision data (not pixels); the reference picture itself is not stored, only a path to it | ScannerGui._write_approved (gui.py:3265) via session.write_manifest (temp + fsync + os.replace, keep_previous=True); merged frame by frame with earlier commissions | read_approved (gui.py:5410) from read_survey, roll_exports and on_delete_rolls; tools/scan_roll.py --approved | Offsets are rounded to 0.1 µm (fine). reference_entry is empty for walks reopened in a later session (GUI2-13). Paths are CWD-relative. Deleting the roll folder deletes it for good. |
| Walk and roll manifests | <rolls>/<roll folder>/survey.json, roll.json (+ .bak) | JSON: roll, numbering, dpi, settings{resolution\|dpi, prescan_resolution, infrared, fast_infrared, film, meter, mono, mono_channel, correct, reverse_hold, frames, start_at, only, rotation, flipped}, wanted, frames[{number, transport_position, registration, done, entry, prescan, prescan_rotation, prescan_flipped, rotation, flipped, exposure, gain, offset}] | metadata | ScanSession._roll / RollManifest (session.py). In this area only carry_walk (gui.py:5765) writes a survey.json copy, re-serialised from read_manifest's parse rather than byte-copied | read_survey (gui.py:5104), roll_summary (5549), folder_note (5804), scanned_frames and wanted_frames | Not rebuildable from the library, despite on_delete_rolls' claim (GUI2-18). A dry-run prescan's record carries no library entry. |
| Walk prescans | <rolls>/<roll folder>/prescanNN.tif, prescanNN-before.tif | TIFF uint16 (deflate when tifffile is present), arranged (turned and flipped) as the screen had it when written | corrected by that day's code; the raw bytes are in the library's prescan entry | ScanSession._file (dry run); copied byte-exact by carry_walk (gui.py:5799, shutil.copyfile) | read_survey: tiff.read then preview.unorient(image, prescan_rotation, prescan_flipped). This becomes the contact sheet's picture and Approved.reference, the image a hold is checked against | Lossless 16-bit, but corrected, so it is not a raw record. The hold reference identity is recorded only through approved.json's reference_entry. |
| Library entries (read, deleted, indexed from this area) | <library>/<UTC-stamp>_<film>_f<roll>-NN_<dpi>dpi[_ir]/{scan.json, scan.tif, raw.bin(.gz), shading.npz, ccd_mask.bin, prescan.tif}; <library>/index.json | scan.json JSON (extra.roll_membership{roll, number, kind, folder}, film.frame '<roll>-NN'); scan.tif raw uint16 decode | raw (scan.tif and raw bytes); corrected on read by library.corrected with today's code | session FrameWriter / library.save. This area deletes via on_delete: shutil.rmtree + library.reindex (gui.py:4494-4495), and rewrites index.json via reindex (non-atomic write_text) | roll_entry_index (json only, gui.py:5478), _load_full → library.corrected (4593), _deliver_one → library.corrected (4330) | The entries are exact. The join to rolls is by name only (GUI2-01). rmtree is not atomic (GUI2-23). |
| Window settings, including contact-sheet decisions and when each roll was last opened | gui-settings.json (settings.DEFAULT_PATH), $RPS7200_SETTINGS, --settings, or demo/gui-settings.json under --demo | JSON: controls, film, output, window, presets, shortcuts, rolls{<folder name>: {opened: epoch}}, sheet{<folder name>: {ticks{n:bool}, offsets{n: float mm}, rotations{n:int}, flips{n:bool}, sources{n:str}, options{dpi, predpi, film, meter, ir, fast_ir, correct}}} | decision and UI state | _ContactSheet._dismiss → _store_sheet_state (gui.py:2666) → _remember → settings.save (temp file + replace); _note_roll_opened (2591) | _recall_sheet_state/_clean_sheet_state (2678/2618), on_reopen_survey and _RollBrowser._reload ('opened' column) | Keyed by folder name only, never pruned, and not moved on Rename or cleared on Delete (GUI2-03). For a walk not yet commissioned this is the only record of its ticks, positions and turns. |
| Exported and saved pictures | <chosen folder>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ <stem>.dng for 4-channel JPEG); Save all: <kind>NN_<dpi>dpi[_ir].<ext> or <kind>_<seq>_...; Save As: the chosen path | TIFF uint16 deflate (tifffile) or uncompressed (built-in writer); JPEG 8-bit plus a DNG holding RGBI; one channel when the window's mono flag is on | corrected with today's code via library.corrected, then oriented (preview.orient) and optionally reduced to mono; the reduced working copy for passes not yet filed | _deliver_one (gui.py:4309) via export.write, from on_save_as (UI thread), on_save_all and on_export_rolls (writer threads tracked by _start_writing) | the operator and NegPy; not read back by the driver | TIFF keeps full 16-bit precision of the corrected image. mono follows the window, not the roll (GUI2-09). Names can say 0dpi for CLI rolls (GUI2-21). The sidecar and fallback files can overwrite (GUI2-20). |
| Roll folder operations | <rolls>/<name>-N (duplicate), <rolls>/<new name> (rename), deleted folders | filesystem copytree, rename, rmtree | whatever the folder held | on_duplicate_roll (shutil.copytree), on_rename_roll (Path.rename), on_delete_rolls (shutil.rmtree), all from _RollBrowser | rolls_on_disk / roll_summary | A duplicate keeps the original's roll name inside its manifests, so the library join conflates the two (GUI2-01). A rename does not move the stored sheet state. |

**Second reader's corrections to this table:**

- Roll frames are missing from the table. <rolls>/<roll>/frameNN.tif is written by ScanSession._file with `path=out / f"frame{number:02d}.tif"` (session.py:2629). It is not _unclaimed, so a rescan (GUI2-05) overwrites the earlier file. It is corrected by that day's code, arranged by the per-frame approval rotation (`_frame_rotation`), and mono according to `wants_mono(job.mono, job.film)`.
- approved.json `offset_mm` is in millimetres, rounded to 4 dp (gui.py:3313), which contradicts the project's transport-unit rule.
- approved.json `reference_entry`, roll.json `frames[].entry` and the library's `extra.roll_membership.folder` are relative to the process working directory, not to the library or rolls root (see GUI2-A2). They are not merely "CWD-relative paths" as a formatting detail: they break when the window runs from another directory.
- approved.json is written only if `approved` is non-empty. A write failure is logged and the roll proceeds (gui.py:3326-3336), so the operator's decisions then survive only in gui-settings and in each entry's `marks["approved"]`.
- survey.json written by carry_walk is re-serialised from read_manifest (not byte-copied, gui.py:5800). The prescan TIFFs are byte-copied with shutil.copyfile.
- library/index.json is rewritten non-atomically by reindex (library.py:1064), from both the UI thread (on_delete) and the writer thread (library.save).
- Export/Save all: mono follows the window's current v_mono (GUI2-09). Export names come from settings['resolution'], which is 0 for rolls made by scan_roll.py (GUI2-21). Rotation for an exported roll frame comes from roll.json's per-frame `rotation`/`flipped` (`arranged`), else approved.json, else settings.rotation.

## What the operator can do

- Open the contact sheet after a walk (it opens automatically when the walk finishes, or with the Contact sheet ... button or key), and tick or untick frames by clicking the picture, the checkbox, Space, All or None.
- Open the position window by double-click, Return or a click on the caption. Drag the picture, step it with the arrows (finest, small, medium or large), Centre it (as walked, marked his), Reset it (the detector's reading), or press Return to tick and move to the next frame.
- Rotate or flip one frame or all frames from the sheet's right-click menu or keys; 'all' also sets the session default for later scans.
- Set per-roll scan options on the sheet (scan dpi, prescan dpi, film, meter, infrared, IR tied to resolution, nudge) and press Scan chosen frames: confirm, approved.json is written and merged, and a Roll with an approval for every ticked frame is submitted.
- In Rolls ..., sort and filter rolls, then Open (resume), Export (re-correct from the library), Duplicate, Rename, Show in file manager, or Delete roll folders.
- In the big view, zoom, pan, double-click for fit or 1:1, read the pixel under the pointer and the clipping histogram, show a superseded prescan, delete a pass (optionally with its library entry), Save As or Save all.
- With 'aim' ticked, click an edge of a prescan to move the film by one sub-frame command (after a confirm).
- Launch with --demo, --look-only (demo only), --open-roll, --library, --rolls, --out, --settings or --reference.

## What the operator should not do

- Open a roll from the Rolls browser while a walk or roll is running.
- Double-click a thumbnail expecting only to open the position window: it also toggles the tick.
- Walk through a resumed roll's frames with Return, or press All, without re-checking the already-scanned frames.
- Walk a new strip into a roll name used before (typed again, or after Delete or Rename) and trust the sheet that opens.
- Rescan into a duplicated roll, or reuse a roll name, and then Export either copy.
- Delete or rename the folder of the roll whose contact sheet is currently open.
- Aim-click on a prescan that no longer shows where the film is (an older frame, a reopened walk, or the prescan just aimed from).
- Choose 7200 dpi, or infrared with B&W or Kodachrome, in the sheet's options.
- Export rolls or Save all with the main window set to a different film (mono) from the roll's.
- Export or Save all large batches, or click rapidly through many 3600/7200 dpi frames, while the scanner session is open, especially during a roll.
- Combine --demo with --library or --rolls pointing at the real library or rolls.

## Mistakes nothing guards against

- Open in the roll browser bypasses the busy check that the Rolls ... button makes. A roll opened mid-walk merges the walk's remaining prescans into it, and its decisions are later filed in the walk's folder.
- A double-click on a sheet thumbnail unticks (or ticks) the frame. Leaving the position window with Done or Escape leaves the frame out of the roll.
- Return in the position window and the All button re-tick already-scanned frames. The confirm dialog neither names nor counts them, and the rescan replaces frameNN.tif.
- A fresh walk into a folder name that has stored sheet state inherits the old strip's ticks, 'yours' positions and turns, which are then applied to the new strip's frames.
- Export of a duplicated or name-reusing roll delivers another roll's library entries, named and turned by this roll's settings.
- Delete or Rename in the roll browser is allowed on the roll the open sheet belongs to. Scan chosen frames then recreates the old folder and scans into it without the walk.
- Aim moves the film by a distance measured on whatever prescan is displayed, and can be applied twice from the same stale prescan.
- The sheet accepts 7200 dpi and IR on IR-blind films. The roll winds the film (and at 7200 dpi prescans and holds each frame) before the driver refuses.
- Roll Export and Save all reduce every frame to one channel, or keep all channels, according to the window's current film, not the roll's.
- Save As failures (full disk, missing entry) produce no message in the window.
- JPEG export without Pillow writes .tif files that can overwrite existing TIFF exports, and the JPEG's .dng sidecar can overwrite an existing DNG.
- --demo --rolls rolls lets a demo commission write roll.json, approved.json and frames into a real walk folder.

## Dataflow notes

How data enters:
- Live passes arrive as session events. _pump (gui.py:3548) polls session.poll(), and _handle (3598) passes each 'result' to _add_result (3882). The Result.image is a downscaled copy of the delivered, shading-corrected pass (session._deliver, session.py:2705-2731; prescans are under 1400 px and so stay full size). A 'filed' event later sets r.entry to the library entry path (3655-3658).
- Walked prescans also go into self.survey (_into_survey, 3949) and to frame_edges.EdgeWatch (edge_watch.add, 3914).
- Reopened rolls come through open_roll (2878) → read_survey (5104):
  - survey.json and roll.json are read with read_manifest, then renumbered and manifest_settings.
  - prescanNN.tif is read with tiff.read, then preview.unorient per prescan_arrangement.
  - approved.json is read with read_approved (5410) → offsets, rotations, flips, entries (reference_entry) and sources.
- The browser lists rolls_on_disk (5631) → roll_summary (5549, JSON and stat only), joined to roll_entry_index (5478), which globs <library>/*/scan.json and keys on extra.roll_membership.roll or film.frame.
- Stored sheet decisions come from gui-settings through _recall_sheet_state/_clean_sheet_state (2678/2618).

Big view:
- _show (3994) → _load_full (4575) starts a thread running library.corrected(entry). That is load (raw scan.tif + shading.npz + ccd_mask.bin) then apply_shading with today's code. The result goes on the _reads queue.
- _pump → _loaded (4605) builds a decimation pyramid (preview.pyramid).
- _redraw (4824) → _pixels → preview.sample → preview.render → _paint (PPM into a reused PhotoImage) → _place.
- The histogram comes from _measure_histogram (4404): a thread running preview.histogram and preview.clipping on rgb_only(corrected pixels), then the _measured queue, then _HistogramPanel.show.
- The pixel readout comes from _pixel_at (4955), reading the corrected full-resolution array.
- Tk is touched only on the UI thread: every worker hands its result back through a queue.

Contact sheet to roll:
- _open_sheet (2519) → _merge_kept → _ContactSheet(offsets, proposals, rotations, flips, ticks, options, readings).
- Detector answers arrive via _pump → _edges_changed (3505) → take_readings (8487) → _snap_proposals (snap_offset, 5887) → _apply_detected, unless the source is 'operator'.
- The operator's edits go through _FrameAdjuster._set (7387), which snaps the offset and marks the source 'operator', and through _orient (8330), which updates rotations and flips and calls gui.remember_arrangement (3968), and that also moves _transport.
- Scan chosen frames: _ContactSheet._scan (8690) → approved_from_sheet (6402), which builds Approved(number, snapped offset_mm, rotation, flipped, reference=result.image, reference_entry=str(result.entry), source) → _dismiss (state to gui-settings) → on_scan_chosen (3054). That validates dpi, pins predpi to the survey's, confirms, and then:
  - _write_approved (3265) → write_manifest(approved.json, merged, .bak).
  - orientations[('frame', n)] is set for each approved frame.
  - session.submit(Roll(start_at=first ticked, frames=span, only=ticked, approved=..., out=_roll_folder())).
- The session seeks, then DirectScanner.scan_roll holds each frame to its Approved (direct.py:4004).

How data leaves:
- Aim: _aim (5014) → aim_millimetres → on_nudge (3361) → session.submit(Move(millimetres)).
- Save As, Save all and Export: _deliver_one (4309) → library.corrected → preview.orient → to_monochrome (window flag) → export.write (tiff.write, deflate, or JPEG plus DNG). Save As runs on the UI thread. Save all and Export run on threads that report through _saves and are tracked by _start_writing, so quitting waits for them.
- Delete pass: shutil.rmtree(entry) + library.reindex (4494).
- Roll folder operations: copytree, rename or rmtree (2779-2863).
- Sheet state: _store_sheet_state → _remember → settings.save.
- Nothing in this area writes pixels into the library. It reads raw entries only through library.load/corrected, so the raw record is not altered here, apart from the deliberate Delete.
