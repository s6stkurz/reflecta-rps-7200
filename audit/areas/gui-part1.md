# The window (tools/gui.py, first half)

Area key `gui-part1`. 41 findings: 2 critical, 5 high, 19 medium, 14 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

tools/gui.py lines 1-4100: the operator's Tk window (ScannerGui). Every scanner action (Calibrate, Prescan, Scan, Roll/walk, Scan chosen frames, prev/next slide, fine nudge, aim-by-click) builds a frozen job dataclass from Tk variables on the Tk thread and hands it to ScanSession.submit. None of them talks to the device directly. A single worker thread owns the scanner and a FrameWriter thread writes files. Events come back through session.poll() in a 120 ms _pump. Full-resolution reads, histograms, Save all and Export run on daemon threads and report through queues. I found no Tk calls from worker threads. Demo/look-only never branches the scan path; only wording differs.


The serious problems are in what reaches disk:


1. **Single scans are filed corrected.** A single Scan from the window stores the shading-CORRECTED pixels as the library's "raw" scan.tif, because session._scan never passes raw_image. Every later view, Save as, Save all and Export then corrects those pixels a second time.
2. **An output-folder failure loses the library entry.** FrameWriter writes the delivered copies before library.save. So an unwritable or missing output folder (a remembered one included) silently costs the library entry and its raw bytes.
3. **Roll folders and approved.json go to different places.** Roll names and folders are derived two ways: _safe(name) or "roll" for approved.json, and the raw name or today's date for the roll itself. Resume does not restore the name. Two strips on one day with the default empty name collide in rolls/<date>.
4. **A mid-walk rotate or flip corrupts reopened walks.** It changes session.rotation for the prescanNN.tif files still to come, but survey.json records only the start value. read_survey then un-rotates those references wrongly.
5. **Stale contact-sheet state reaches a new strip.** It comes back through an open or recently closed sheet and through the gui-settings fallback.
6. **Unguarded paths.** The Roll keyboard shortcut and aim-by-click skip the busy guard. The Rolls browser's Open does too. Stop cannot cancel a job already queued. Quit waits for every queued job and kills Save-all threads mid-write.
7. **Demo and look-only gaps.** Under --demo, Delete on a reopened real walk's frame deletes a real library entry. --look-only without --demo drives real hardware while the text promises a refusal. 7200 dpi is offered, refused by the host on hardware, and accepted by the demo.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [GUI1-01](#gui-part1-gui1-01) | critical | data-integrity | A single Scan from the window files shading-corrected pixels as the library's raw scan.tif, so every view and export corrects them twice | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [GUI1-02](#gui-part1-gui1-02) | critical | data-integrity | An unwritable or missing output folder (or roll folder) silently costs the library entry, raw bytes included | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [GUI1-03](#gui-part1-gui1-03) | high | data-integrity | approved.json is written to a different folder than the roll it belongs to (empty or unsanitised roll names) | [P18](../problems/P18-roll-folder-identity.md) |
| [GUI1-04](#gui-part1-gui1-04) | high | data-integrity | With the default empty roll name every strip of the day shares rolls/<date>: walks overwrite each other, frames overwrite frameNN.tif, and the previous strip's sheet decisions are restored onto the next | [P18](../problems/P18-roll-folder-identity.md) |
| [GUI1-05](#gui-part1-gui1-05) | high | bug | Continuing a reopened roll scans into a different folder: the roll's name is never restored | [P18](../problems/P18-roll-folder-identity.md) |
| [GUI1-06](#gui-part1-gui1-06) | high | data-integrity | Turning or flipping a prescan during a walk changes the orientation of the prescanNN.tif files still to be written, while survey.json keeps the start value, so reopened references come back mis-oriented | [P20](../problems/P20-rotation-during-walk.md) |
| [GUI1-07](#gui-part1-gui1-07) | high | bug | A contact sheet from the previous strip survives a new walk: it is raised instead of rebuilt, or its state is put back by closing it mid-walk | [P22](../problems/P22-contact-sheet-state.md) |
| [GUI1-08](#gui-part1-gui1-08) | medium | concurrency | The Roll keyboard shortcut bypasses the busy guard: it queues a second roll and resets the running walk's survey mid-walk | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI1-09](#gui-part1-gui1-09) | medium | data-integrity | Each commission replaces approved.json with only the frames ticked this time, so earlier frames' positions and turns are lost (written non-atomically) | [P19](../problems/P19-roll-manifests.md) |
| [GUI1-10](#gui-part1-gui1-10) | medium | bug | Roll export ignores each entry's recorded arrangement (reversal half-turn, per-frame rotation) and uses the window's current mono setting | -- |
| [GUI1-11](#gui-part1-gui1-11) | medium | bug | Save as / Save all deliver one channel or three by the window's current film setting, not by the pass's own | -- |
| [GUI1-12](#gui-part1-gui1-12) | medium | user-error | Clicking a prescan in aim mode can queue a film move during a running job, or aim from a stale prescan of another position | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI1-13](#gui-part1-gui1-13) | medium | bug | Reopened frames use seq = -number, so two reopened rolls collide: wrong full-resolution pixels can be shown for a frame, and Delete removes both | [P25](../problems/P25-reopened-frames.md) |
| [GUI1-14](#gui-part1-gui1-14) | medium | bug | Typing into the 'Save scans to' box does nothing this session but is saved and applied at the next launch | -- |
| [GUI1-15](#gui-part1-gui1-15) | medium | concurrency | Stop cannot cancel a job already queued, and double-presses or keyboard repeats queue duplicate scans | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI1-16](#gui-part1-gui1-16) | medium | user-error | Quit during a job waits for every queued job (hours for a roll) without stopping, and kills Save all / Export threads mid-file | [P24](../problems/P24-disk-full-and-quitting.md) |
| [GUI1-17](#gui-part1-gui1-17) | medium | user-error | A manual exposure set in the window is silently ignored by every roll | -- |
| [GUI1-18](#gui-part1-gui1-18) | medium | demo-divergence | 7200 dpi is offered but always refused by the host on hardware (after metering), while the demo accepts it | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [GUI1-19](#gui-part1-gui1-19) | medium | demo-divergence | Delete on a reopened frame deletes a real library entry, even under --demo / make run-sheet | [P25](../problems/P25-reopened-frames.md) |
| [GUI1-20](#gui-part1-gui1-20) | medium | doc-mismatch | --look-only without --demo drives the real scanner while the window promises a refusal | [P28](../problems/P28-look-only-drives-real-scanner.md) |
| [GUI1-21](#gui-part1-gui1-21) | medium | data-integrity | The Film panel's 'frame' field is stamped on every frame of a roll, which breaks the roll-to-library join | [P21](../problems/P21-roll-to-library-join.md) |
| [GUI1-22](#gui-part1-gui1-22) | medium | concurrency | Rolls... 'Open' works while the scanner is busy and can replace the survey of a walk in progress | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI1-23](#gui-part1-gui1-23) | medium | bug | Turning or flipping a walked prescan during the walk appends it to the survey again | [P20](../problems/P20-rotation-during-walk.md) |
| [GUI1-24](#gui-part1-gui1-24) | medium | hardware-safety | GUI single scans and prescans are gzipped into the library with the device open and idle | [P13](../problems/P13-gzip-with-device-open.md) |
| [GUI1-25](#gui-part1-gui1-25) | medium | bug | Resuming a roll that has no sheet via the Roll button rescans done frames and scans past the wanted ones | [P25](../problems/P25-reopened-frames.md) |
| [GUI1-A1](#gui-part1-gui1-a1) | medium | demo-divergence | DemoScanner publishes no raw pixels, so every demo entry files corrected pixels beside a reference and is corrected twice when viewed. The demo also hides GUI1-01 | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [GUI1-26](#gui-part1-gui1-26) | low | user-error | The 'Calibrate: reuse' choice is remembered across launches, so later sessions silently correct with an old reference | -- |
| [GUI1-27](#gui-part1-gui1-27) | low | error-handling | The contact sheet can commission infrared for B&W or Kodachrome, and the refusal comes only after approved.json is written and the film is seeked | -- |
| [GUI1-28](#gui-part1-gui1-28) | low | error-handling | on_nudge calls deliverable_mm outside its try, so very long nudges raise an uncaught ValueError in the Tk callback | -- |
| [GUI1-29](#gui-part1-gui1-29) | low | bug | Rotating or flipping an old or reopened pass overwrites the window's last-known transport position | -- |
| [GUI1-30](#gui-part1-gui1-30) | low | error-handling | After Force abort, the keyboard, the contact sheet and the Roll shortcut can still submit jobs | -- |
| [GUI1-31](#gui-part1-gui1-31) | low | bug | Delivered files from Save as, Save all and Export carry no resolution tag and can overwrite a sidecar despite 'Nothing already there is overwritten' | -- |
| [GUI1-32](#gui-part1-gui1-32) | low | design | The full-resolution view shows raw pixels without a label when an entry has no usable reference | -- |
| [GUI1-33](#gui-part1-gui1-33) | low | doc-mismatch | The stop button's label is wrong for rolls commissioned from the contact sheet | -- |
| [GUI1-34](#gui-part1-gui1-34) | low | doc-mismatch | The resume dialog and README say a resumed roll 'will calibrate again first'; the code only prompts when this session has not calibrated | -- |
| [GUI1-35](#gui-part1-gui1-35) | low | doc-mismatch | DPI_LADDER comment contradicts the list (1200) and the validation | -- |
| [GUI1-36](#gui-part1-gui1-36) | low | doc-mismatch | Aim and dry-run dialogs state things the code does not do | -- |
| [GUI1-37](#gui-part1-gui1-37) | low | demo-divergence | The demo stand-in answers some GUI actions differently from the hardware (cached-reference reuse, untied infrared timing) | -- |
| [GUI1-39](#gui-part1-gui1-39) | low | error-handling | _remember cannot report a failed settings write | -- |
| [GUI1-A2](#gui-part1-gui1-a2) | low | doc-mismatch | The session's close-order comment claims gzipping waits until the device is closed, but the writer thread gzips throughout the session | -- |
| [GUI1-38](#gui-part1-gui1-38) | info | doc-mismatch | TODO.md's 'dismissed calibrate prompt lets a roll reach the lazy calibration' is stale as written (the gate blocks it); the remaining route is the Roll shortcut race | -- |

## Findings in full

<a id="gui-part1-gui1-01"></a>

### GUI1-01 -- A single Scan from the window files shading-corrected pixels as the library's raw scan.tif, so every view and export corrects them twice

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `tools/gui.py:2047-2061`, `rps7200/session.py:1440-1458`, `rps7200/session.py:1089-1091`, `rps7200/direct.py:2708-2733`, `rps7200/direct.py:2818`, `rps7200/library.py:373-391`, `tools/gui.py:3858-3864`, `tools/gui.py:4104-4118`

**Doc claim:** tools/gui.py:11-13 ('Files are written exactly as the command-line tools write them: raw negatives, filed in the library with their raw bytes...'); README.md:237 ('what reaches library/ is the raw negative'); CLAUDE.md:114-115 ('scan.tif in an entry is the decode alone, with no flat-fielding').

Every Scan-button or scan-key pass is filed with corrected pixels in scan.tif, while corrections_applied is empty and calibration.report is set. The record therefore claims raw pixels plus a reference to correct them with. library.corrected() applies the reference a second time: the full-resolution view (_load_full), Save as, Save all and roll Export all deliver double-flat-fielded pixels. The entry reports "applied", so nobody is warned. `library.reconstruct` will also report a changed decode for every such entry. That is exactly the false-alarm class CLAUDE.md describes for prescans. Prescans and roll frames are filed correctly, and tools/scan.py files last_pixels_raw, so the window's single scan is the outlier.

**Evidence (from the code):**

```text
session._scan: `image, meta = self._scanner.scan(..., keep_raw=True)` then `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` -- no `raw_image=`. FrameWriter: `raw_image = job.get("raw_image")` / `entry = library.save(job["image"] if raw_image is None else raw_image, ...)`. DirectScanner.scan returns the corrected image (`image, shading_report = apply_shading(image, self._shading, ccd_mask)`) and publishes the raw one only on `self.last_pixels_raw = raw_pixels`. Compare _prescan: `raw_image = getattr(self._scanner, "last_pixels_raw", None)`. library.corrected: `if "shading" in applied: ...` else `image, report = apply_shading(image, record["reference"], record["ccd_mask"])`.
```

**Failure scenario:** The operator calibrates, presses Scan at 1800 dpi and later zooms to 1:1 or uses Save as. The file on disk is apply_shading(apply_shading(raw)): striping is re-introduced or inverted and the levels are scaled. Any later decode/correction improvement cannot start from the true raw pixels via scan.tif, and reconstruct reports 'decode CHANGED'.

**Fix:** In ScanSession._scan pass `raw_image=getattr(self._scanner, 'last_pixels_raw', None)` (read immediately after scan()), as _prescan does. Add a session test that a Scan files raw pixels. Run `tools/library.py migrate-raw` (or an equivalent) over GUI single-scan entries already filed: they are tagged 'gui' and lack the 'prescan'/'roll' tags.

<details><summary>Second reader's check</summary>

session.py:1440-1458. _scan calls self._file(seq, 0, image, meta, ...) with no raw_image, so FrameWriter._write (session.py:1089-1091) files job['image']. That image is what DirectScanner.scan returns, which is the corrected image (direct.py:2733 `image, shading_report = apply_shading(...)`, returned at 2824). The raw copy exists only as self.last_pixels_raw (direct.py:2818). _prescan does read last_pixels_raw (session.py:1415) and _scan does not. library.corrected (library.py:374-389) sees empty corrections_applied plus a reference and applies shading a second time. _load_full, _deliver_one (Save as, Save all, Export) all go through library.corrected. tools/scan.py:190 does file last_pixels_raw, so the window's single Scan is the outlier. I found nothing that prevents this.

</details>

<a id="gui-part1-gui1-02"></a>

### GUI1-02 -- An unwritable or missing output folder (or roll folder) silently costs the library entry, raw bytes included

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1074-1100`, `rps7200/session.py:1046-1058`, `tools/gui.py:643-647`, `tools/gui.py:1893-1895`, `tools/gui.py:1924-1927`

**Doc claim:** CLAUDE.md:92-97 ('File every scan in the library, with its raw bytes').

The delivered copies (output folder, roll folder frameNN.tif/prescanNN.tif) are written before the library entry, all inside one try. Any OSError there (unmounted removable drive, network share gone, read-only folder, disk full on the output volume, an invalid remembered path) skips library.save completely. The only trace is a log line 'picture N could not be filed'. The output folder is remembered across launches and never validated, so the loss repeats on every scan of every launch until the folder comes back.

**Evidence (from the code):**

```text
FrameWriter._write: `for path in job.get("paths") or (): Path(path).parent.mkdir(parents=True, exist_ok=True); note = export.write(str(path), delivered, ...)` and only afterwards `if job["library"]: ... entry = library.save(...)`. _run: `except Exception as exc: self.errors.append(...)`. GUI: `if self.remembered["output"] and self.session.out_dir is None: self._set_outdir(str(self.remembered["output"]))` with no existence/writability check.
```

**Failure scenario:** The output folder was set to a USB disk (E:\scans or /Volumes/USB/scans) last week. Today it is not plugged in. Each Scan/roll frame raises in mkdir or export.write, and the raw bytes, shading reference and CCD mask for every pass are never filed: minutes to hours of scanner time with no re-derivable record.

**Fix:** File the library entry first, in its own try, and write delivered copies afterwards in a separate try whose failure is reported but can never skip library.save. Validate out_dir in _set_outdir/_restore (exists and writable) and warn loudly or clear it when invalid.

<details><summary>Second reader's check</summary>

FrameWriter._write (session.py:1074-1100) does mkdir and export.write for every path first. library.save comes after, inside the same call. _run (1046-1058) catches any exception, appends to errors and calls on_done with entry=None, and _filed only logs 'could not be filed'. _restore (gui.py:643-644) re-applies the remembered output folder with no existence or writability check, and _set_outdir (1893-1895) does none either. For a roll frame, the rolls/ path is written first and then the out_dir copy, so a dead out_dir still costs the entry. Reachable on every scan kind.

</details>

<a id="gui-part1-gui1-03"></a>

### GUI1-03 -- approved.json is written to a different folder than the roll it belongs to (empty or unsanitised roll names)

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2929-2967`, `tools/gui.py:2853-2864`, `tools/gui.py:2147-2158`, `rps7200/session.py:1634-1637`, `rps7200/session.py:2181-2189`, `tools/gui.py:4924-4957`

**Doc claim:** README.md:349-350 ('Turns survive closing the window, in approved.json'); tools/gui.py:2484-2491 (on_delete_rolls docstring: approved.json is the one thing in a roll folder not derivable).

The two writers name the roll folder differently. With the roll field empty -- its state at every launch, since 'roll' is deliberately not remembered -- approved.json goes to rolls/roll/approved.json while survey.json, roll.json and frameNN.tif go to rolls/<today>. Any name that _safe changes (spaces, '/', ':', '..') also splits them. A raw name with '/' or '..' even nests or escapes the rolls directory and hides the roll from the browser, which lists only top-level folders. read_approved(rolls/<today>) then finds nothing, so reopening loses every per-frame offset, turn, flip, source and reference entry. Export uses the roll-wide rotation. Delete says there is no approved.json. rolls/roll/approved.json is overwritten by every unnamed commission.

**Evidence (from the code):**

```text
GUI: `name = _safe(self.fields["roll"].get().strip())` / `folder = Path(self.session.rolls) / name` / `(folder / "approved.json").write_text(...)`. Session: `name = job.name or time.strftime("%Y-%m-%d")` / `out = Path(job.out) if job.out else self.rolls / name`. _safe: `cleaned = "".join(kept).strip("-.") or "roll"`. Roll(name=self.fields["roll"].get().strip()) is passed unsanitised.
```

**Failure scenario:** Walk a strip with the roll field empty, turn two portrait frames in the sheet, commission. Tomorrow open rolls/2026-09-23 from Rolls...: no turns, no positions, and 'Export' writes the portrait frames sideways. Name a roll 'Gold 200' and approved.json lands in rolls/Gold-200 while the roll is in 'rolls/Gold 200'.

**Fix:** Derive the folder once: have the session compute and expose the roll folder (e.g. via last_roll_dir or a shared helper that applies _safe and the date fallback). Write approved.json there, or let the Roll job write approved.json itself. Sanitise Roll.name in the session.

<details><summary>Second reader's check</summary>

_write_approved (gui.py:2940-2943) uses _safe(roll field), and _safe maps '' to 'roll' (session.py:2183). The session (session.py:1634-1635) uses `job.name or time.strftime('%Y-%m-%d')` with the raw, unsanitised name that on_roll and on_scan_chosen pass (gui.py:2156, 2862). The roll field is not in REMEMBERED_FILM (gui.py:194), so it starts empty. With an empty name, approved.json lands in rolls/roll/ and the frames in rolls/<date>/. read_approved(folder) reads only folder/approved.json (gui.py:4949-4951).

</details>

<a id="gui-part1-gui1-04"></a>

### GUI1-04 -- With the default empty roll name every strip of the day shares rolls/<date>: walks overwrite each other, frames overwrite frameNN.tif, and the previous strip's sheet decisions are restored onto the next

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1654`, `rps7200/session.py:1817-1833`, `rps7200/session.py:1889`, `rps7200/session.py:1918-1926`, `tools/gui.py:2112-2133`, `tools/gui.py:2340-2365`, `tools/gui.py:3258-3262`

**Doc claim:** tools/gui.py:2116-2124 (comments: 'A fresh strip has no arrangements yet, and the last one's would be applied to whatever pictures happen to land on the same frame numbers').

A 36-exposure film is scanned as several strips, and the roll field starts empty every launch. Each strip's walk replaces the previous survey.json and prescanNN.tif in the same date folder. A second strip's roll overwrites frame03.tif etc. and merges its records into the first strip's roll.json under the same frame numbers. roll_entry_index then keys both strips' library entries as '<date>-03', last one wins. Worse, when the new walk finishes, _sheet_roll is the same folder name. The sheet is rebuilt from the settings-file copy of the previous strip's ticks, offsets, rotations and flips. Those are handed in as 'kept' operator positions, which beat the detector. This is exactly what the on_roll comment says clearing sheet_state prevents.

**Evidence (from the code):**

```text
`name = job.name or time.strftime("%Y-%m-%d")`; dry run: `manifest_path = out / ("survey.json" ...)` with no merge (`if not job.dry_run and manifest_path.exists()`); `surveyed = out / f"prescan{number:02d}.tif"`; frames: `path=out / f"frame{number:02d}.tif"` (no _unclaimed); roll.json merge `manifest["frames"] = [f for f in manifest["frames"] if str(f.get("number")) != str(number)] + [record]`. GUI on_roll dry clears `self.sheet_state = {}` but not `self.remembered["sheet"]`; `_recall_sheet_state` falls back to `(self.remembered.get("sheet") or {}).get(key)` with key = folder name.
```

**Failure scenario:** Strip 1 is walked, frames 2 and 5 are hand-positioned and turned, and it is commissioned. Strip 2 goes in and is walked, unnamed, same day. The sheet opens with strip 1's offsets and turns on strip 2's frames 2 and 5, labelled as the operator's. Commissioning holds strip 2's frames to strip 1's offsets and writes them sideways. rolls/<date>/frame02.tif of strip 1 is overwritten, and roll.json now mixes both strips.

**Fix:** Never default two walks into one folder: give an unnamed walk a unique folder (date + time, or _unclaimed on the folder) and carry that folder on the job and into on_scan_chosen. When a dry run starts, also drop remembered['sheet'][key] for the target folder, or key sheet state by a walk id rather than a folder name. Use _unclaimed or refuse when frameNN.tif/prescanNN.tif exist from a different walk.

<details><summary>Second reader's check</summary>

A dry run writes survey.json with no merge (session.py:1649-1654 merges only when not dry_run). prescanNN.tif and frameNN.tif use fixed names (1817, 1889), and roll.json replaces records by number (1918-1921). on_roll clears self.sheet_state but not remembered['sheet'] (gui.py:2123). When the walk finishes, _sheet_roll = last_roll_dir (3257), which has the same folder name, and _recall_sheet_state then falls back to remembered['sheet'][name] (2364-2365). That entry was stored by the previous strip's _dismiss under the same key. roll_entry_index keys by film.frame '<date>-NN', last one wins (gui.py:5005-5008).

</details>

<a id="gui-part1-gui1-05"></a>

### GUI1-05 -- Continuing a reopened roll scans into a different folder: the roll's name is never restored

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2552-2563`, `tools/gui.py:2574-2601`, `tools/gui.py:2697-2721`, `tools/gui.py:4780-4792`, `tools/gui.py:2862`, `tools/gui.py:2667-2684`

**Doc claim:** README.md:287-291 ('Opening a roll with frames left brings back its contact sheet and approvals ... restores the roll's own settings'); tools/gui.py:2552-2559 (open_roll docstring).

The resume flow ('Put the strip in ... and press "Scan chosen frames"') uses whatever the Film panel's roll field holds: usually empty, which means today's date. So the remaining frames go into a new folder with only them in `wanted`. The original roll.json is never merged or updated and stays 'unfinished' in the browser for good. The earlier `renumbered`/`earlier` merge logic in _roll never runs for it. The sheet's decisions are stored under the old folder name, the approvals under the new one.

**Evidence (from the code):**

```text
open_roll restores only `restorable(settings)`. RESTORABLE maps resolution, prescan_resolution, infrared, fast_infrared, film, meter, mono_channel, correct, reverse_hold, frames and start_at, but not the roll name or folder. on_scan_chosen then submits `Roll(..., name=self.fields["roll"].get().strip(), ...)` and `_write_approved` uses the same field. Nothing in the file writes `self.fields["roll"]`.
```

**Failure scenario:** A roll 'kodak-2026-09-20' died at frame 11 of 24. The operator opens it from Rolls..., ticks the remaining 13 and scans. The frames land in rolls/2026-09-23, split from the first 11 with separate manifests. The old roll still reads '11 of 24', and exporting either one gives half a roll.

**Fix:** In open_roll set the roll field (or a dedicated _resume_folder) from out['roll']/the folder name. Make on_scan_chosen/on_roll pass that folder as Roll.out so the session writes into the reopened folder. Add a test that resume writes into the opened folder.

<details><summary>Second reader's check</summary>

open_roll restores only restorable(settings) (gui.py:2574, 2697-2721). RESTORABLE (4780-4792) has no roll name or folder, and nothing writes fields['roll']: the only uses are reads at 2156, 2862 and 2940. on_scan_chosen submits Roll(name=fields['roll']) with no out=, so the session writes into rolls/<field or today's date> (session.py:1634-1635), not into the reopened folder.

</details>

<a id="gui-part1-gui1-06"></a>

### GUI1-06 -- Turning or flipping a prescan during a walk changes the orientation of the prescanNN.tif files still to be written, while survey.json keeps the start value, so reopened references come back mis-oriented

**Severity** high · **Category** data-integrity · **Verdict** partly · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `tools/gui.py:3911-3921`, `rps7200/session.py:1687-1688`, `rps7200/session.py:1732-1733`, `rps7200/session.py:2011-2029`, `tools/gui.py:4722-4747`

If the operator turns or flips a prescan while a walk runs, every later prescanNN.tif is written in the new arrangement. survey.json keeps the arrangement from the start of the walk. On reopen, read_survey un-orients all frames by that one start value and gives each the same display rotation. The frames filed after the change therefore come back turned (for example 90 degrees) relative to the earlier ones, in the sheet and as reference pictures for a reopened commission.

**Evidence (from the code):**

```text
_carry: `self.session.rotation = result.rotation` / `self.session.flip = result.flipped` (any time, from the Tk thread). _roll builds the manifest once: `"rotation": self.rotation, "flipped": self.flip` and rewrites that same dict after every frame. _orientation_for for prescans: `return self.rotation, self.flip` (read at filing time, per frame). read_survey: `turn = int(manifest.get("rotation") or 0)` ... `image=preview.unorient(image, turn, mirrored)`.
```

**Failure scenario:** The dry run starts at rotation 0 and the operator rotates frame 1's prescan 90 degrees as it lands. prescan02-06.tif are written rotated 90 while survey.json says 0. Next day, reopening shows frames 2-6 turned 180 relative to frame 1 (90 un-rotated by 0, plus the 90 applied as result.rotation). The references are rotated, so every held frame reads unverified and nothing moves.

**Fix:** Record rotation/flipped per frame in the survey record, which _file already computes into meta, and have read_survey un-orient each prescanNN.tif by its own values (or read them from the prescan's library entry). Alternatively write prescanNN.tif un-oriented always, since it is a reference and not a deliverable. Snapshot session.rotation/flip into the job at submit time rather than reading a mutable attribute on the worker.

<details><summary>Second reader's check</summary>

The mechanism is real. _carry sets session.rotation/flip at any time (gui.py:3919-3920). The manifest's rotation and flipped are captured once at walk start (session.py:1687-1688, 1732-1733) and never updated. _orientation_for returns self.rotation/self.flip for prescans at filing time (session.py:2025-2029). read_survey un-orients every prescan by the single manifest pair (gui.py:4722-4723, 4739). The failure-scenario arithmetic is wrong, though. read_survey sets result.rotation = turn (the manifest's 0) for every reopened frame (4747), not the operator's 90. So frames 2-6 reopen turned 90 degrees relative to frame 1, not 180. The consequence stands: those arrays are the sheet's pictures and the references on a reopened commission.

</details>

<a id="gui-part1-gui1-07"></a>

### GUI1-07 -- A contact sheet from the previous strip survives a new walk: it is raised instead of rebuilt, or its state is put back by closing it mid-walk

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:2112-2133`, `tools/gui.py:2160-2191`, `tools/gui.py:2340-2350`, `tools/gui.py:3249-3262`, `tools/gui.py:2723-2771`

The sheet is non-modal and stays open. When the next strip's walk finishes, _handle('finished') calls on_contact_sheet, which just lifts the old sheet showing the old strip. Its 'Scan chosen frames' is enabled once the walk is idle. It commissions the old strip's frame numbers, positions, turns and reference pictures against the new strip in the transport. If the old sheet is instead closed during the new walk, _dismiss writes its state back into self.sheet_state. The new sheet then opens with the previous strip's ticks, offsets and turns, as in GUI1-04 but without needing the same folder.

**Evidence (from the code):**

```text
on_roll (dry) clears `self.survey`, `self.orientations`, `self.sheet_state`, `self._sheet_roll` but does not close `self.sheet`. on_contact_sheet: `if self.sheet is not None and self.sheet.alive(): self.sheet.top.lift(); ... return`. _dismiss -> `_store_sheet_state(self.state())` -> `self.sheet_state = state` (set even when key is None). on_scan_chosen checks only `self.busy`.
```

**Failure scenario:** The operator leaves strip 1's sheet open, loads strip 2, ticks dry run and presses Scan roll. When it ends, the sheet that pops up is strip 1's. They tick frames and press Scan chosen frames: frames of strip 2 are held to strip 1's prescans and written with strip 1's orientations.

**Fix:** In on_roll (dry) destroy any open sheet and adjuster before the walk (as open_roll does), and make _store_sheet_state ignore states from a sheet whose generation or survey is not the current one (tag each sheet with the walk generation and compare).

<details><summary>Second reader's check</summary>

The on_roll dry branch (gui.py:2112-2133) resets survey, orientations, sheet_state and _sheet_roll but never destroys self.sheet (open_roll does, at 2620-2624). on_contact_sheet lifts a live sheet and returns (2170-2173). The old _ContactSheet holds the old survey list object. _dismiss calls _store_sheet_state, which sets self.sheet_state = state even when _sheet_key() is None (2342-2345), so closing it mid-walk puts the old decisions back for the next sheet. on_scan_chosen checks only busy and calibration.

</details>

<a id="gui-part1-gui1-08"></a>

### GUI1-08 -- The Roll keyboard shortcut bypasses the busy guard: it queues a second roll and resets the running walk's survey mid-walk

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:736-741`, `tools/gui.py:2077-2146`, `tools/gui.py:815-833`, `rps7200/shortcuts.py:117`

**Doc claim:** tools/gui.py:2138-2139 (comment claims this cannot race a running job).

Pressing Cmd/Ctrl-B during any job opens the Roll dialog. Confirming queues another Roll behind the running job and immediately rewrites the window's walk state. If a walk is running, its survey is emptied mid-walk and later frames go into a list that 'finished' treats as the first walk's. The second walk then runs with _surveying already False and produces no sheet. The ETA/frame-status of the running roll is also reset. During a Calibrate the optimistic `self.calibrated = True` lets it through, and if the calibration then fails the queued roll reaches the lazy in-scan calibration TODO.md warns about.

**Evidence (from the code):**

```text
`"roll": self.on_roll,` (default key `<ACCEL-Key-b>`). _confirm_then for prescan/scan starts with `if self.busy: ... return`, but on_roll has no busy check, and its comment says `# The button this handler is behind is disabled while busy, so this cannot race a job that is still running.` A dry run then does `self.survey = []`, `self.orientations = {}`, `self.sheet_state = {}`, `self._sheet_roll = None`, `self.edge_watch.begin(...)` and resets `_roll_wall_start`/`_roll_frames_total`.
```

**Failure scenario:** A walk is on frame 7 of 12. The operator presses Cmd-B intending to check settings and clicks OK. The contact sheet that opens has only frames 8-12, and a second walk starts afterwards with no sheet.

**Fix:** Add `if self.busy: self._say(...); return` at the top of on_roll, or route the 'roll' action through the same busy check as _confirm_then. Fix the comment.

<details><summary>Second reader's check</summary>

The 'roll' action maps straight to self.on_roll (gui.py:740). _runner adds no guard (891-896), and on_roll has no busy check. Its own comment at 2138-2139 says it cannot race, which is false for the key <ACCEL-Key-b> (shortcuts.py:117). A dry run mutates survey, sheet_state, _sheet_roll and edge_watch immediately (2112-2133), while the first walk's 'finished' still clears _surveying (3249-3250).

</details>

<a id="gui-part1-gui1-09"></a>

### GUI1-09 -- Each commission replaces approved.json with only the frames ticked this time, so earlier frames' positions and turns are lost (written non-atomically)

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/gui.py:2929-2955`, `tools/gui.py:2831`, `tools/gui.py:4871-4899`, `tools/gui.py:4924-4957`

On resume the sheet opens with already-scanned frames unticked, so approved.json is rewritten with only the remaining frames. The same happens when a strip is commissioned in two batches. The done frames' per-frame turns, flips, offsets, sources and reference entries disappear from the one file the code itself calls non-derivable. Export then orients those frames by the roll-wide rotation, and reopening loses their decisions. write_text is also not atomic, and read_approved turns a truncated file into 'no approvals' without a word.

**Evidence (from the code):**

```text
`(folder / "approved.json").write_text(json.dumps({"roll": name, "numbering": NUMBERING, "frames": [{...} for a in approved]}, ...))`: a whole-file replace with the current `approved` only. roll_exports: `_, rotations, flips, _, _ = read_approved(folder)` then `rotation=rotations.get(number, turn)`.
```

**Failure scenario:** Frames 1-4 are commissioned with frame 3 turned 90. Later frames 5-8 are commissioned from the same sheet. approved.json now holds 5-8 only. Export writes frame 3 sideways, and reopening shows frame 3 unturned.

**Fix:** Merge into the existing approved.json by frame number instead of replacing it: read, update the ticked frames, write to a temp file and os.replace. Or record per-frame decisions in roll.json, which the session already merges.

<details><summary>Second reader's check</summary>

_write_approved writes the whole file from only the current `approved` (gui.py:2943-2955) with a plain write_text. read_approved returns empty maps on ValueError (4952-4956). roll_exports takes rotations and flips only from that file, falling back to the roll-wide turn (4886-4898).

</details>

<a id="gui-part1-gui1-10"></a>

### GUI1-10 -- Roll export ignores each entry's recorded arrangement (reversal half-turn, per-frame rotation) and uses the window's current mono setting

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:2383-2451`, `tools/gui.py:4871-4906`, `tools/gui.py:3843-3875`, `rps7200/session.py:2105-2110`

Each library entry's scan.json already records the final arrangement its delivered file got, reversal half-turn included, in scan.rotation/scan.flipped. Export reconstructs it from approved.json plus the roll-level rotation and never composes the reversal, so a frame the session turned 180 to match its prescan is exported upside down relative to its own frameNN.tif. Frames whose approvals were lost (GUI1-03/09) fall back to the roll default. Mono comes from whatever film the main window shows now: a colour roll exported while the window is set to B&W comes out single-channel (throwing colour away), and a B&W roll exported with the window on colour comes out three-channel.

**Evidence (from the code):**

```text
roll_exports: `rotation=rotations.get(number, turn), flipped=flips.get(number, mirrored)` from approved.json/settings. _deliver_one: `full, entry_record = library.corrected(result.entry)`; `full = preview.orient(full, result.rotation, result.flipped)`; `if mono: full = to_monochrome(full, mono_channel)`. on_export_rolls: `mono, channel = self.v_mono.get(), self.v_mono_channel.get()`. The session composes the reversal into the filed record: `turn, flip = preview.compose((int(reversal[0]), bool(reversal[1])), (turn, flip))` / `meta = dict(meta, rotation=turn, flipped=flip)`.
```

**Failure scenario:** The roll's frame 7 came back reversed, and its frame07.tif is correctly turned 180. Export writes frame 7 upside down. If the window was left on 'bw' after scanning a B&W strip, exporting last week's colour roll produces greyscale files.

**Fix:** Export (and Save all/Save as for filed passes) should take rotation/flipped/reversal from the entry record (entry_record['scan']['rotation'/'flipped']) and mono from the entry's film/meta or the roll's settings (settings['mono']/['film']), not from the window's controls.

<details><summary>Second reader's check</summary>

roll_exports builds rotation and flipped from approved.json and settings (gui.py:4886-4898). _deliver_one applies result.rotation/flipped to library.corrected output (3858-3864) and never reads entry_record['scan'] rotation, flipped or reversal. The session composes the reversal into the filed meta and the frameNN.tif (session.py:2105-2110). on_export_rolls takes mono from the window's current v_mono (gui.py:2425).

</details>

<a id="gui-part1-gui1-11"></a>

### GUI1-11 -- Save as / Save all deliver one channel or three by the window's current film setting, not by the pass's own

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:3754-3768`, `tools/gui.py:3814-3828`, `tools/gui.py:1777-1800`

'Deliver one channel' is a property of the film, and each pass knows its film (meta['film']). The delivery path instead reads today's window state. The same pass saved twice under different window settings produces different files, and a colour pass can be silently reduced to one channel.

**Evidence (from the code):**

```text
on_save_as: `self._deliver_one(result, path, jpeg_quality(self.v_jpegq.get()), self.v_mono.get(), self.v_mono_channel.get())`; on_save_all: `mono, channel = self.v_mono.get(), self.v_mono_channel.get()`; `_sync_mono`: `bw = self.v_film.get() == FILM_BW; self.v_mono.set(bw)`.
```

**Failure scenario:** The operator scans a colour negative, switches the film to bw for the next strip, then uses Save all: every colour pass of the session is written as a single green plane.

**Fix:** Decide mono per pass from result.meta['film'] (wants_mono(None, film)) or from the entry record. Keep the window's value only as the default for new scans.

<details><summary>Second reader's check</summary>

on_save_as passes self.v_mono.get() (gui.py:3766-3767), and on_save_all reads mono from the window (3815). _deliver_one applies it to every pass regardless of that pass's meta['film'].

</details>

<a id="gui-part1-gui1-12"></a>

### GUI1-12 -- Clicking a prescan in aim mode can queue a film move during a running job, or aim from a stale prescan of another position

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:4500-4504`, `tools/gui.py:4531-4587`, `tools/gui.py:2972-3018`

The aim tick is remembered across launches (REMEMBERED includes 'aim'). A click on any prescan -- including a roll's own prescans arriving mid-roll, an old prescan picked from the filmstrip, or a reopened walk's frame -- computes a move from that picture and applies it relative to wherever the film is now. During a roll the Move is queued and runs when the roll ends. Aiming twice on the same prescan moves twice. The only guard is the confirm dialog, which does not say the picture is stale or that the move is queued.

**Evidence (from the code):**

```text
on_press: `if self.v_aim.get() and self.current is not None and self.current.kind == "prescan": self._aim(event)`. _aim -> `self.on_nudge(1 if want > 0 else -1, millimetres=abs(want))` -> `self.session.submit(Move(millimetres=millimetres * direction))`. on_nudge has no `self.busy` check (only the nudge buttons are disabled in _set_busy), and nothing compares current.position with self._transport.
```

**Failure scenario:** During a roll the operator clicks a delivered prescan to pan and confirms the dialog out of habit. After the roll ends the film moves by up to 88.8 units, and the next single scan is mis-framed. Or they aim on yesterday's reopened frame 5 while the film sits on frame 1.

**Fix:** Refuse aim while busy. Refuse (or warn) when current.position differs from self._transport, or when current is a reopened result (seq < 0). Consider not remembering 'aim' across launches.

<details><summary>Second reader's check</summary>

on_press goes to _aim whenever aim is ticked and the current result is a prescan (gui.py:4500-4504). _aim reaches on_nudge (4587), which submits Move with no busy or dead check (2972-3018) and no comparison of current.position against _transport. 'aim' is in REMEMBERED (gui.py:130). The job is queued behind a running roll.

</details>

<a id="gui-part1-gui1-13"></a>

### GUI1-13 -- Reopened frames use seq = -number, so two reopened rolls collide: wrong full-resolution pixels can be shown for a frame, and Delete removes both

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:4734-4746`, `tools/gui.py:2603-2609`, `tools/gui.py:4092-4102`, `tools/gui.py:4122-4136`, `tools/gui.py:4023`, `tools/gui.py:3643-3648`

Opening a second roll (or the same one twice) appends frames whose seq equals the first roll's. Clicking frame 3 of roll A and then frame 3 of roll B before A's read lands skips B's read (same _loading seq). A's pixels then arrive and are installed as B's full-resolution image, so the 1:1 view, the pixel readout marked exact, and the histogram all describe the wrong photograph. _show_seq/_at pick the first match, and Delete drops every result with that seq from the session.

**Evidence (from the code):**

```text
read_survey: `result = Result(seq=-number, ...)`. open_roll: `for result in out["results"]: self.results.append(result)` (never de-duplicated). _load_full: `if self._loading == r.seq or self._levels_seq == r.seq: return`. _loaded: `if self.current is None or self.current.seq != seq or image is None: return` then `self._full, self._full_seq = image, seq`. on_delete: `self.results = [r for r in self.results if r.seq != result.seq]`.
```

**Failure scenario:** Rolls 'A' and 'B' are both opened in one session. Zooming B's frame 3 shows A's frame 3 grain, and the readout values come from the wrong negative. Deleting B's frame 3 also removes A's frame 3 from the strip.

**Fix:** Give reopened results unique negative seqs (e.g. a running counter), or key results by (roll, number). Clear or replace the previous reopened roll's results on open_roll, and reset _loading in _show.

<details><summary>Second reader's check</summary>

read_survey gives every reopened frame seq=-number (gui.py:4735), and open_roll appends without de-duplication (2603-2604). _show resets _levels_seq but not _loading (3527-3532), so _load_full for roll B frame N returns early while A's frame N is loading (4100). _loaded then installs A's pixels because current.seq matches (4122-4134). on_delete filters by seq (4023), so both frames go.

</details>

<a id="gui-part1-gui1-14"></a>

### GUI1-14 -- Typing into the 'Save scans to' box does nothing this session but is saved and applied at the next launch

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:1534-1545`, `tools/gui.py:1893-1895`, `tools/gui.py:678-681`, `tools/gui.py:643-647`

The field shows a path the session is not using. Deleting its text by hand (instead of pressing Clear) leaves copies going to the old folder, and typing a new path leaves them going to the old one or to none. The typed text is written to gui-settings.json and becomes the real output folder, unvalidated, on the next launch (see GUI1-02).

**Evidence (from the code):**

```text
`ttk.Entry(box, textvariable=self.v_outdir).pack(fill="x")` has no trace or binding. Only `_set_outdir` (Choose/Clear/_restore) sets `self.session.out_dir = Path(path) if path else None`. _remember persists `"output": self.v_outdir.get()`.
```

**Failure scenario:** The operator types D:\negatives into the box and scans a roll. No files appear in D:\negatives; they went to the previously chosen folder or nowhere. Next launch D:\negatives is live whether or not it exists.

**Fix:** Trace v_outdir (or bind FocusOut/Return) to _set_outdir with validation, or make the entry read-only so only Choose/Clear change it.

<details><summary>Second reader's check</summary>

The Entry is bound to v_outdir with no trace (gui.py:1539). The only writers of session.out_dir are _set_outdir (1893-1895) and the constructor. _remember persists v_outdir.get() (681), and _restore applies it via _set_outdir at the next launch (643-644).

</details>

<a id="gui-part1-gui1-15"></a>

### GUI1-15 -- Stop cannot cancel a job already queued, and double-presses or keyboard repeats queue duplicate scans

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `rps7200/session.py:1214-1218`, `rps7200/session.py:1313-1318`, `tools/gui.py:3020-3023`, `tools/gui.py:3350-3365`, `tools/gui.py:2969-2970`

Run buttons stay enabled until the worker's busy event is polled, so a fast double-click on Scan, Prescan or next-slide queues two jobs. Stop only sets a flag that the worker clears at the start of every job. The queued second job therefore always runs, and a multi-minute scan cannot be withdrawn short of Force abort, which costs a power cycle. There is no 'clear queue'.

**Evidence (from the code):**

```text
submit: `self._stop.clear(); self._jobs.put(job)`; worker loop: `job = self._jobs.get() ... self._stop.clear()`. Buttons are disabled only when the 'state' busy event is drained by the 120 ms _pump (_set_busy).
```

**Failure scenario:** A double-click on Scan at 3600 dpi RGBI: two ~6-minute passes. Pressing Stop during the first still runs the second in full.

**Fix:** Disable the run buttons synchronously in each on_* handler before submit (set self.busy=True locally). Give the session a cancel_pending() that drains queued jobs, called by Stop.

<details><summary>Second reader's check</summary>

submit clears _stop and enqueues (session.py:1213-1217). The worker clears _stop at the start of every job (1316). Nothing drains the queue. on_scan and on_prescan submit without setting busy locally (gui.py:2037-2061). Buttons are disabled only when the busy 'state' event is pumped.

</details>

<a id="gui-part1-gui1-16"></a>

### GUI1-16 -- Quit during a job waits for every queued job (hours for a roll) without stopping, and kills Save all / Export threads mid-file

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `tools/gui.py:3039-3068`, `rps7200/session.py:1246-1248`, `tools/gui.py:3822-3841`, `tools/gui.py:2431-2451`, `rps7200/export.py:181-193`

'Waits for it to finish' means the whole roll plus everything queued behind it, with the window stuck on 'closing ...'. An operator who reads that as a frozen app and kills it abandons a read, which is the documented wedge. Separately, quitting while Save all or Export runs destroys the root, and the process exits and kills the daemon thread mid-TIFF, leaving a truncated file under its final name.

**Evidence (from the code):**

```text
on_close: `if self.busy and not messagebox.askokcancel("Quit", "A scan is still running. Quitting waits for it to finish ...")` then `self.session.shutdown()` (puts None at the end of the queue). It does not call request_stop and does not check `self._saving`. Save threads: `threading.Thread(target=run, daemon=True, name="save-all").start()`; export.write writes straight to the final path.
```

**Failure scenario:** Quit is pressed at frame 4 of 36 at 1800 dpi RGBI: the window sits 'closing' for over an hour. Or Quit during Save all leaves pass_017.tif half written.

**Fix:** On quit during a job, offer 'stop after this pass' (request_stop plus draining the queue) and say how long the wait is. Refuse or delay quitting while _saving, or make save threads non-daemon and join them. Write delivered files to a temp name and rename.

<details><summary>Second reader's check</summary>

on_close only asks, then calls shutdown(), which appends None after all queued jobs (session.py:1246-1248). It never calls request_stop and never checks self._saving (gui.py:3039-3050). The Save all and Export threads are daemon threads (3840, 2451). export.write writes directly to the final path (export.py:181-193).

</details>

<a id="gui-part1-gui1-17"></a>

### GUI1-17 -- A manual exposure set in the window is silently ignored by every roll

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:1754-1763`, `tools/gui.py:2147-2158`, `tools/gui.py:2853-2864`, `rps7200/session.py:877-935`

**Doc claim:** tools/gui.py:7112-7116 (_ContactSheet.OPTIONS doc: 'exposure and shading are session-wide rather than per-roll').

'set by hand' with a typed multiplier looks like a scan setting, but only the single Scan honours it. Scan roll and Scan chosen frames meter automatically (or use device defaults with meter='none'). Neither the Roll dialog nor the sheet mentions this.

**Evidence (from the code):**

```text
`_exposure()` feeds only `Scan(... auto_exposure=self.v_expmode.get() == "auto", exposure_scale=exposure ...)`. `Roll(...)` has no exposure field, and the roll meters by `meter` ('each'/'once'/'none').
```

**Failure scenario:** A dense negative needs x2.5 exposure. The operator sets it by hand, tests one Scan (fine), then runs the roll. Every frame is metered afresh or scanned at device defaults, not at x2.5.

**Fix:** Pass exposure to Roll (with meter='none' meaning 'use this exposure'), or grey the manual exposure controls and say 'single scans only' whenever a roll is started while expmode is manual.

<details><summary>Second reader's check</summary>

The Roll dataclass has no exposure field (session.py:877-935), and on_roll and on_scan_chosen pass none. _exposure feeds only Scan (gui.py:2047-2061). Nothing warns about it.

</details>

<a id="gui-part1-gui1-18"></a>

### GUI1-18 -- 7200 dpi is offered but always refused by the host on hardware (after metering), while the demo accepts it

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `tools/gui.py:102-118`, `tools/gui.py:1748-1749`, `tools/gui.py:2781-2782`, `rps7200/direct.py:2512-2578`, `rps7200/demo.py:530-597`

**Doc claim:** README.md:360-362 ('accepts any whole number from 25 to 7200 ... the device refuses what it dislikes with sense 0x26/0x82 before a byte of image data moves'); tools/gui.py:114-117.

On hardware any scan whose frame needs more than 5172 columns (anything above ~3600 dpi, 7200 included) raises ShadingUnavailable, and the window always asks for shading. For a single scan that comes after the two RGB metering rounds. For a roll it happens per frame after each prescan and advance, until max_failures stops it. The demo scans 7200 without complaint, so the demo shows a working path the hardware refuses: the 'demo is the real software' rule broken in the stand-in's answers.

**Evidence (from the code):**

```text
`DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`; `_int(self.v_dpi, "Scan dpi", 25, 7200)`. DirectScanner.scan: auto-exposure runs first (`exposure_scale = self.auto_exposure(...)`), then `if needed > self.MAX_SHADING_COLUMNS: raise ShadingUnavailable(...)`. DemoScanner.scan has no such check and returns a 7200 dpi picture.
```

**Failure scenario:** The operator picks 7200 from the list and starts a roll. Three frames are prescanned, metered and advanced, each fails 'cannot be corrected at all', and the roll stops. The same steps in make run-demo succeed.

**Fix:** Take the limit from DirectScanner (MAX_SHADING_COLUMNS/_shading_columns_needed) in the window: remove 7200 from DPI_LADDER or grey it, and validate typed dpi before submit. Make DemoScanner.scan/scan_roll raise the same ShadingUnavailable using DirectScanner's staticmethod. Move the refusal ahead of auto_exposure in scan().

<details><summary>Second reader's check</summary>

DPI_LADDER includes 7200 (gui.py:118) and validation allows up to 7200. DirectScanner.scan runs auto_exposure (direct.py:2512-2527) before the MAX_SHADING_COLUMNS refusal (2566-2577). DemoScanner.scan (demo.py:530-597) has no column check and no ShadingUnavailable, so the demo accepts 7200.

</details>

<a id="gui-part1-gui1-19"></a>

### GUI1-19 -- Delete on a reopened frame deletes a real library entry, even under --demo / make run-sheet

**Severity** medium · **Category** demo-divergence · **Verdict** partly · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:4742`, `tools/gui.py:3999-4011`, `tools/gui.py:8165-8171`, `tasks.py:167-185`

Under --demo, --open-roll takes a real roll folder. Reopened frames whose approved.json names a reference_entry carry that real library/ path. Delete answered 'No' to 'Keep the library entry?' then rmtrees the real entry and reindexes the real library, although the demo's comments promise that nothing reaches the real walk or library.

**Evidence (from the code):**

```text
read_survey: `entry=Path(entries[number]) if number in entries else None` (from approved.json reference_entry, e.g. 'library/<id>'). on_delete: `if not keep: shutil.rmtree(entry); library.reindex(entry.parent)`. main(): 'It is safe because opening a roll only reads it -- `_write_approved` derives its folder from `session.rolls`, which --demo pins under demo/'.
```

**Failure scenario:** While exploring make run-sheet the operator deletes 'frame 3 (reopened)' and clicks No, thinking it is the demo copy. The real library entry with the raw bytes of that prescan is gone for good.

**Fix:** Do not offer library deletion for reopened results (seq < 0) or for entries outside session.root. Under --demo never rmtree outside DEMO_ROOT.

<details><summary>Second reader's check</summary>

The code path is real. read_survey sets entry from approved.json's reference_entry (gui.py:4742), and on_delete rmtrees it and reindexes its parent when the answer to 'Keep the library entry?' is No (3999-4011). There is no check for demo, seq<0, or a path outside session.root. main()'s comment (8165-8171) claims nothing the window does can write back. Two corrections. make run-sheet opens rolls/aligned-strip (tasks.py:167, 184), not registration-D. And only frames whose approved.json carries a reference_entry get an entry. The deletion also sits behind an explicit dialog that says the raw bytes are unrecoverable. So medium rather than high.

</details>

<a id="gui-part1-gui1-20"></a>

### GUI1-20 -- --look-only without --demo drives the real scanner while the window promises a refusal

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P28](../problems/P28-look-only-drives-real-scanner.md)

**Where:** `tools/gui.py:8145-8147`, `tools/gui.py:8187-8203`, `tools/gui.py:7266-7272`, `tools/gui.py:367-374`

Without --demo the session opens DirectScanner and nothing refuses. The contact sheet's 'Scan chosen frames' seeks, advances and scans real hardware, under wording that says it will refuse.

**Evidence (from the code):**

```text
help: `"there is no film in the transport. Every control still works; anything that reaches for film says so"`. `no_film=args.look_only` is passed only inside `if args.demo:`, while `ScannerGui(..., look_only=args.look_only)` is set regardless. Sheet text: 'Scanning is offered as it always is, and will say there is no film when it reaches for it.'
```

**Failure scenario:** `uv run python tools/gui.py --look-only --open-roll rolls/X` to look at yesterday's walk with the scanner plugged in, then pressing Scan chosen frames: the transport winds and scans whatever is loaded.

**Fix:** Make --look-only imply --demo, or refuse --look-only without --demo in main(), or pass the refusal into the real path too (a wrapper that raises on transport and scan calls).

<details><summary>Second reader's check</summary>

no_film=args.look_only is passed only inside `if args.demo` (gui.py:8189-8199), while look_only reaches ScannerGui unconditionally (8203). Without --demo the session opens DirectScanner. The sheet's text (7266-7272) and the --look-only help (8145-8147) promise a refusal that never comes.

</details>

<a id="gui-part1-gui1-21"></a>

### GUI1-21 -- The Film panel's 'frame' field is stamped on every frame of a roll, which breaks the roll-to-library join

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `tools/gui.py:1724-1731`, `tools/gui.py:2157`, `tools/gui.py:2863`, `rps7200/session.py:1826-1827`, `rps7200/session.py:1880-1882`, `tools/gui.py:4979-5009`

A per-shot field left over from a single scan in the same session gives every roll frame the same film.frame. All entries of the roll then claim one frame and get the same f<slug> in their ids. roll_entry_index can no longer find them, so the browser marks the roll orphaned and Export says there is nothing to re-correct from.

**Evidence (from the code):**

```text
`_notes()` includes `frame=self.fields["frame"].get().strip()`; the session uses `replace(job.notes, frame=job.notes.frame or f"{name}-{number:02d}")`; roll_entry_index joins on `frame.rpartition("-")`.
```

**Failure scenario:** The operator types '7' in frame for a test scan, then walks and scans a roll. Every library entry of the roll says frame '7'. The Rolls table shows it orphaned, and Export refuses.

**Fix:** Do not pass the window's frame field into Roll jobs (the roll sets its own), or store the roll/number join in dedicated fields (tags already carry the roll name) rather than overloading film.frame.

<details><summary>Second reader's check</summary>

_notes includes the frame field (gui.py:1728), and the roll uses `job.notes.frame or f'{name}-{number:02d}'` (session.py:1826-1827, 1880-1882). roll_entry_index joins on film.frame.rpartition('-') (gui.py:5005-5008). The frame field is not remembered across launches but persists within a session.

</details>

<a id="gui-part1-gui1-22"></a>

### GUI1-22 -- Rolls... 'Open' works while the scanner is busy and can replace the survey of a walk in progress

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:7049-7054`, `tools/gui.py:2226-2244`, `tools/gui.py:2552-2660`, `tools/gui.py:3503-3505`

The browser is non-modal and can be left open when a walk is started. Opening a roll mid-walk replaces self.survey and starts a new edge-watch generation. The running walk's remaining prescans are then appended to the reopened roll's survey. On finish, _sheet_roll becomes the live walk's folder, and the sheet mixes two strips' frames, decisions and references.

**Evidence (from the code):**

```text
on_reopen_survey refuses `if self.busy` only when the browser is opened; `_RollBrowser._open` -> `self.gui.open_roll(summary["folder"])` without a busy check. open_roll sets `self.survey = out["results"]` and calls `self.edge_watch.load(...)`, while `_surveying` stays True.
```

**Failure scenario:** The browser is left open, a dry run is started, and the operator double-clicks an old roll to look at it. The walk's sheet now lists the old roll's frames plus the new strip's later frames under one folder.

**Fix:** Check `self.busy` (and `self._surveying`) in open_roll itself, not only when the browser opens.

<details><summary>Second reader's check</summary>

on_reopen_survey refuses only when busy at the moment the browser opens (gui.py:2226-2231). _RollBrowser._open calls gui.open_roll with no check (7049-7054). open_roll has no busy or _surveying check and replaces self.survey (2601). remember_arrangement keeps appending live walk prescans while _surveying (3503-3505).

</details>

<a id="gui-part1-gui1-23"></a>

### GUI1-23 -- Turning or flipping a walked prescan during the walk appends it to the survey again

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `tools/gui.py:3498-3505`, `tools/gui.py:3911-3936`, `tools/gui.py:5791-5837`

While a dry run is running, every turn or flip of an already-walked prescan adds that result to self.survey a second time. The contact sheet then shows duplicate cells sharing one tick variable. 'Scan N of the M frames walked' overcounts, and approved_from_sheet emits duplicate Approved records into approved.json.

**Evidence (from the code):**

```text
remember_arrangement (called by _carry for every rotate/flip): `if self._surveying and result.kind == "prescan" and result.number: self.survey.append(result)`, with no membership check.
```

**Failure scenario:** The operator rotates frame 1's prescan twice while the walk continues. The sheet shows frame 1 three times and the dialog says 14 frames walked on a 12-frame strip.

**Fix:** Append only when the result is not already in self.survey (by identity or number), e.g. do the survey append in _add_result rather than remember_arrangement.

<details><summary>Second reader's check</summary>

_carry calls remember_arrangement (gui.py:3921). That appends to self.survey whenever _surveying and the result is a numbered prescan, with no membership test (3503-3505).

</details>

<a id="gui-part1-gui1-24"></a>

### GUI1-24 -- GUI single scans and prescans are gzipped into the library with the device open and idle

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:10-16`, `rps7200/session.py:1013-1025`, `rps7200/session.py:1311-1331`, `tools/gui.py:3770-3841`, `tools/gui.py:2383-2451`

**Doc claim:** CLAUDE.md:160-163 and 368-369 ('A single scan compresses nothing while the device is open'; 'Do not hold the session open through heavy local work').

The overlap argument holds only inside a roll. After a single Scan or Prescan there is no next pass: the device stays open and idle for the whole window lifetime while FrameWriter gzips the raw bytes (seconds at 1800 dpi, more at 3600). Save all and Export also do heavy local work with the device open and idle. That is precisely the state CLAUDE.md says preceded a wedge. It is unmeasured on this path.

**Evidence (from the code):**

```text
Session docstring: 'A second thread writes finished frames to disk, because gzipping a library entry with the device open and idle is the state that preceded a wedge'. FrameWriter: 'On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.' The worker keeps the device open between jobs (`while True: job = self._jobs.get()`).
```

**Failure scenario:** A 3600 dpi RGBI single scan followed by nothing: ~140 MB gzipped with the device open and idle -- the conditions of the recorded wedge.

**Fix:** Either measure it (tools/filing_load_test.py for the idle case) or defer gzip for single passes: spool the raw bytes uncompressed and compress on close, as DirectScanner debug filing does. Correct the docstrings to say which case they cover.

<details><summary>Second reader's check</summary>

FrameWriter's thread starts with the session (session.py:1311) and gzips as soon as a job arrives (library.py:192). After a single Scan or Prescan, the worker idles in `self._jobs.get()` with the device open. The FrameWriter docstring's overlap argument (1017-1021) covers only a following pass. The same holds for the last frame of any roll.

</details>

<a id="gui-part1-gui1-25"></a>

### GUI1-25 -- Resuming a roll that has no sheet via the Roll button rescans done frames and scans past the wanted ones

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:2578-2601`, `tools/gui.py:4780-4792`, `tools/gui.py:2077-2158`

The dialog says N are left and tells the operator to press Roll. Roll then scans `frames` consecutive frames from the first remaining one: already-done frames after it are scanned again, and frames beyond the original set too. With frames=None it runs to the end of the strip. It also goes into a new folder (GUI1-05).

**Evidence (from the code):**

```text
`if restored: self.v_startat.set(str(remaining[0]))` while `frames` is restored to the roll's original count; on_roll submits `Roll(frames=frames or None, start_at=start_at, ...)` with no `only`.
```

**Failure scenario:** A 6-frame roll has done frames [1,2,3,5] and remaining [4,6]. Pressing Roll scans 4,5,6,7,8,9: frame 5 again and three unwanted frames, hours at high dpi.

**Fix:** Submit Roll(only=tuple(remaining), start_at=remaining[0], frames=span) from open_roll's no-sheet path (as on_scan_chosen does), or set frames to the span and pass `only`.

<details><summary>Second reader's check</summary>

In the no-results path, open_roll sets only v_startat (gui.py:2589-2590) after restoring 'frames' to the original count. on_roll submits Roll(frames, start_at) with no `only` (2147-2158), so done frames after the first remaining one are rescanned.

</details>

<a id="gui-part1-gui1-a1"></a>

### GUI1-A1 -- DemoScanner publishes no raw pixels, so every demo entry files corrected pixels beside a reference and is corrected twice when viewed. The demo also hides GUI1-01

**Severity** medium · **Category** demo-divergence · **Verdict** found-by-verifier · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:1006-1010`, `rps7200/demo.py:525-597`, `rps7200/demo.py:771-779`, `rps7200/session.py:1415`, `rps7200/session.py:1830`, `rps7200/session.py:1886`, `rps7200/library.py:374-389`

**Doc claim:** CLAUDE.md 'The demo is the real software with different inputs' (the stand-in must answer as DirectScanner does); demo.py:436-440 (capture_record docstring: 'the session files a complete entry -- one that library.reconstruct can re-decode like any other').

DirectScanner returns corrected pixels and hands the raw ones over through last_pixels_raw or RollFrame.raw_image. DemoScanner returns corrected pixels and never hands raw ones over. Every demo prescan, single scan and roll frame is therefore filed with corrected pixels in scan.tif, real raw bytes plus the reference beside them, and an empty corrections_applied. library.corrected then flat-fields demo pictures a second time in the 1:1 view, Save as, Save all and Export. reconstruct would call every demo entry a changed decode. Because the demo files the wrong thing for every pass kind, it cannot show that the real single-Scan path (GUI1-01) differs from the prescan and roll paths. The seam contract is that the stand-in answers like DirectScanner, and here it does not.

**Evidence (from the code):**

```text
demo._decode: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)` and returns capture `{"reference": reference, "ccd_mask": mask, "raw": raw, "raw_layout": layout}`. DemoScanner defines no `last_pixels_raw` (grep finds none), and `RollFrame(index=..., image=image, meta=..., prescan=prescan, registration=marks, prescan_meta=...)` sets no raw_image or raw_prescan. The session reads `getattr(self._scanner, "last_pixels_raw", None)` and `rf.raw_image` / `rf.raw_prescan`.
```

**Failure scenario:** make run-demo: prescan, scan, zoom to 1:1. The full-resolution view is apply_shading applied twice. A test built on the demo would pass a session that forgets raw_image, as _scan does today.

**Fix:** Have DemoScanner keep the decoded raw image beside the corrected one. Set last_pixels_raw and last_scan_meta on every pass, and raw_image and raw_prescan on each RollFrame, exactly as DirectScanner does. Then add a demo-backed session test that every filed scan.tif equals the raw decode.

<a id="gui-part1-gui1-26"></a>

### GUI1-26 -- The 'Calibrate: reuse' choice is remembered across launches, so later sessions silently correct with an old reference

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:129-131`, `tools/gui.py:2012-2015`, `tools/gui.py:1929-1931`, `tools/gui.py:1407-1412`

**Doc claim:** CLAUDE.md 'Facts' and README.md:127 (one calibration per power-on).

Choosing the cached reference once makes 'reuse' the default of the main Calibrate button on every later launch. There the age warning shown in the prompt is absent. Library entries then carry a reference from another power-on, so the per-session measurement the driver insists on is never captured for those scans.

**Evidence (from the code):**

```text
REMEMBERED includes "shading"; the prompt's 'Use the cached one' does `self.v_shading.set(mode)` with mode 'reuse'; on_calibrate submits `Calibrate(mode=mode or self.v_shading.get(), ...)`.
```

**Failure scenario:** A reuse click on Monday means Tuesday's session loads Monday's shading.npz when the operator presses Calibrate, and Tuesday's entries never get a reference that describes Tuesday's sensor.

**Fix:** Do not persist 'reuse' (reset v_shading to 'measure' at launch), or show the cached reference's age on the button when reuse is selected.

<details><summary>Second reader's check</summary>

'shading' is in REMEMBERED (gui.py:130). The prompt sets v_shading to 'reuse' (2014), and on_calibrate uses v_shading.get() (1930). The main button shows no age note.

</details>

<a id="gui-part1-gui1-27"></a>

### GUI1-27 -- The contact sheet can commission infrared for B&W or Kodachrome, and the refusal comes only after approved.json is written and the film is seeked

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:2793-2801`, `tools/gui.py:2831`, `rps7200/session.py:1619-1637`, `rps7200/direct.py:3390-3397`

The main window disables infrared for blind films, but the sheet's own options do not, and on_scan_chosen never validates. The job fails, but only after approved.json is overwritten and the film has been wound to the first ticked frame.

**Evidence (from the code):**

```text
`infrared = bool(chose("ir", self.v_ir))`; `film = str(chose("film", self.v_film))` with no supports_infrared check; `self._write_approved(approved)` then submit. The session seeks and mkdirs before `scan_roll` raises at first next().
```

**Failure scenario:** The sheet's film is changed to bw while its IR box is still ticked. The operator presses Scan chosen frames: the film winds, approved.json is replaced, and the job fails 'infrared is blind to bw'.

**Fix:** In on_scan_chosen force infrared=False (or refuse before writing anything) when film is in INFRARED_IS_BLIND_TO, reusing DirectScanner's supports_infrared.

<details><summary>Second reader's check</summary>

on_scan_chosen never checks supports_infrared (gui.py:2793-2801). _write_approved runs before submit (2831). The session seeks first (session.py:1619-1626), and scan_roll's infrared refusal (direct.py:3390-3397) comes after the seek.

</details>

<a id="gui-part1-gui1-28"></a>

### GUI1-28 -- on_nudge calls deliverable_mm outside its try, so very long nudges raise an uncaught ValueError in the Tk callback

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:2972-3002`, `rps7200/session.py:694-704`, `rps7200/session.py:720-727`

Typing more than ~710 units in the fine-move box and pressing back or forward raises in deliverable_mm before the guarded call. The button does nothing and the traceback goes to stderr, which the documentation elsewhere calls 'a button that did nothing at all'.

**Evidence (from the code):**

```text
`if deliverable_mm(millimetres) == 0:` precedes `try: plan_nudges(millimetres) except ValueError`; deliverable_mm is `float(sum(plan_nudges(millimetres)))`, and plan_nudges raises past MAX_FINE_STEPS.
```

**Failure scenario:** The operator types 1000 in 'units' and presses forward: nothing happens and no message appears.

**Fix:** Move the deliverable_mm check inside the same try, or check MAX_TRAVEL_MM first.

<details><summary>Second reader's check</summary>

deliverable_mm calls plan_nudges, which raises past MAX_FINE_STEPS (session.py:696-702, 720-727). The call at gui.py:2986 sits outside the try at 2995. There is no report_callback_exception override.

</details>

<a id="gui-part1-gui1-29"></a>

### GUI1-29 -- Rotating or flipping an old or reopened pass overwrites the window's last-known transport position

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:3506-3509`, `tools/gui.py:3911-3924`, `tools/gui.py:5892-5936`

_transport is documented as 'only ever what it last said', but turning a filmstrip pass from earlier, or a reopened roll frame, sets it to that pass's recorded position. The Roll and sheet dialogs then forecast a wind or advance from the wrong frame. The seek re-reads the counter, so this is misleading text only.

**Evidence (from the code):**

```text
remember_arrangement: `if plausible(result.position): self._transport = result.position` (called from _carry for any pass).
```

**Failure scenario:** The operator turns yesterday's reopened frame 9 while the film is on frame 1. The Roll dialog says 'The transport last said the film is on frame 9, so it winds back 8 frames'.

**Fix:** Update _transport only in _add_result for new results (not in _carry), or only from 'transport' events.

<details><summary>Second reader's check</summary>

remember_arrangement sets _transport from result.position whenever it is plausible (gui.py:3508-3509). It is called from _carry for any result, including reopened ones, whose position comes from transport_position (4743).

</details>

<a id="gui-part1-gui1-30"></a>

### GUI1-30 -- After Force abort, the keyboard, the contact sheet and the Roll shortcut can still submit jobs

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:3350-3358`, `tools/gui.py:815-839`, `tools/gui.py:2723-2771`, `tools/gui.py:2077-2090`

The window says 'aborted -- power-cycle ... then start this window again', yet the keys and the sheet still queue work. It fails with 'transport is not open' (Transport._require), but only after the GUI writes approved.json and resets its roll and walk state.

**Evidence (from the code):**

```text
_set_busy disables buttons when `self.session.dead`, but _confirm_then, on_roll and on_scan_chosen check only `self.busy`/calibration, never `self.session.dead`.
```

**Failure scenario:** After Force abort the operator presses Cmd-Return by habit: another failed job and log noise. From the sheet, approved.json is rewritten for a roll that cannot run.

**Fix:** Treat session.dead as a hard gate in _confirm_then, on_roll, on_prescan, on_scan, on_scan_chosen, on_move_frames and on_nudge.

<details><summary>Second reader's check</summary>

_set_busy disables buttons on session.dead (gui.py:3352-3358). _confirm_then, on_roll and on_scan_chosen check only busy and calibration, so keys and the sheet still submit, and on_scan_chosen writes approved.json first.

</details>

<a id="gui-part1-gui1-31"></a>

### GUI1-31 -- Delivered files from Save as, Save all and Export carry no resolution tag and can overwrite a sidecar despite 'Nothing already there is overwritten'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:3868`, `tools/gui.py:3879-3882`, `tools/gui.py:2420-2421`, `tools/gui.py:3809-3810`, `rps7200/export.py:108-114`, `rps7200/export.py:181-190`

The 'same picture' saved from the window lacks the DPI the scan was taken at, unlike the output-folder copy. For JPEG deliveries of RGBI passes, an existing same-stem .dng (or .tif in the Pillow-less fallback) is silently replaced.

**Evidence (from the code):**

```text
`note = export.write(path, full, quality=quality)` (no `resolution=`), whereas FrameWriter uses `export.write(str(path), delivered, resolution=job["dpi"], ...)`. `_unclaimed` checks only the .jpg name, while export.write also writes `<stem>.dng` for 4-channel JPEG, or `<stem>.tif` when Pillow is missing.
```

**Failure scenario:** Save all as JPEG into a folder that already holds scan_003_1800dpi_ir.dng from an earlier run: that DNG is replaced without asking.

**Fix:** Pass resolution=meta['resolution_dpi'] from _deliver_one, and have _unclaimed check the companion paths export.write will create.

<details><summary>Second reader's check</summary>

_deliver_one calls export.write(path, full, quality=quality) with no resolution (gui.py:3868, 3879-3882). export.write also writes <stem>.dng for 4-channel JPEG (export.py:100-107, 152-153) or falls back to .tif. _unclaimed checks only the main name.

</details>

<a id="gui-part1-gui1-32"></a>

### GUI1-32 -- The full-resolution view shows raw pixels without a label when an entry has no usable reference

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:4104-4118`, `tools/gui.py:4135-4136`, `rps7200/library.py:378-387`

**Doc claim:** CLAUDE.md:127-129 ('a raw file shown to a person is the uncorrected picture this driver stopped delivering').

Save as reports the correction status, but the 1:1 view and the histogram silently switch to the stored raw decode for such entries, including legacy and 'correction was asked for' entries.

**Evidence (from the code):**

```text
`image, _ = library.corrected(entry)` discards record['corrected'] ('no reference', 'deliberately raw', 'raw -- correction was asked for'); the log says only 'now showing the scan's own WxH pixels'.
```

**Failure scenario:** An operator inspects a 2026-09-11 entry at 1:1, sees stripes and judges the scan bad, when it was never corrected.

**Fix:** Carry record['corrected'] through _reads and show it in the caption or log when it is not 'applied'.

<details><summary>Second reader's check</summary>

_load_full discards the record (gui.py:4114). library.corrected returns raw pixels with record['corrected'] set to 'no reference' or 'deliberately raw' (library.py:378-387). _loaded logs only the dimensions.

</details>

<a id="gui-part1-gui1-33"></a>

### GUI1-33 -- The stop button's label is wrong for rolls commissioned from the contact sheet

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:6015-6024`, `tools/gui.py:3362-3363`, `rps7200/session.py:2223-2229`

A sheet-commissioned roll shows 'Stop (finishes this pass)', while the session actually stops after the frame and before the next advance.

**Evidence (from the code):**

```text
`return "Stop after this frame" if "roll" in job else "Stop (finishes this pass)"`; _describe for a Roll with `only`: `f"{what} {n} chosen frame{'s' if n != 1 else ''}"` (no 'roll').
```

**Failure scenario:** The operator reads 'finishes this pass' and expects the roll to end once the running pass is done; it may take the rest of the frame, including hold moves.

**Fix:** Pass the job type rather than parsing the text, e.g. carry isinstance(job, Roll) in the state event.

<details><summary>Second reader's check</summary>

stop_label tests `'roll' in job` (gui.py:6023). _describe for a Roll with `only` returns 'scanning N chosen frames' (session.py:2224-2227), which has no 'roll'.

</details>

<a id="gui-part1-gui1-34"></a>

### GUI1-34 -- The resume dialog and README say a resumed roll 'will calibrate again first'; the code only prompts when this session has not calibrated

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:2678-2682`, `tools/gui.py:1946-1958`, `rps7200/direct.py:2579-2592`

**Doc claim:** README.md:291-293 ('It calibrates again first').

If the session already calibrated, the resumed roll reuses the session reference: no new calibration. The v_shading radio affects only the Calibrate button, not the roll.

**Evidence (from the code):**

```text
Dialog: 'It will calibrate again first ... Set Calibrate to "reuse" before starting if you would rather load the saved one.' Code: `_calibration_missing` returns False once `self.calibrated`, and the roll path never calls ensure_shading; scan() calibrates only `if self._shading is None or needed > ...`.
```

**Failure scenario:** An operator who relies on the promised fresh calibration for a resume, in a session where calibration already ran, gets none.

**Fix:** Reword to 'it needs a calibration in this session', or actually submit a Calibrate before a resumed roll.

<details><summary>Second reader's check</summary>

The dialog text (gui.py:2678-2682) and README.md:292 promise a new calibration. _calibration_missing returns False once calibrated (1946-1958). The roll path never calibrates unless _shading is None (direct.py:2579-2592).

</details>

<a id="gui-part1-gui1-35"></a>

### GUI1-35 -- DPI_LADDER comment contradicts the list (1200) and the validation

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:102-118`, `tools/gui.py:1748-1749`

**Doc claim:** tools/gui.py:102-117.

1200 is offered although the comment says it has no evidence behind it, and there is client-side validation (a range check).

**Evidence (from the code):**

```text
The comment says '300, 600, 900, 1800 and 3600 ... 7200 ... Nothing else has evidence behind it' and 'there is no client-side validation', while the code has `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)` and `_int(self.v_dpi, "Scan dpi", 25, 7200)`.
```

**Failure scenario:** The dropdown offers 1200 as if tested.

**Fix:** Either drop 1200 or document its evidence; fix the validation sentence.

<details><summary>Second reader's check</summary>

The comment lists 300/600/900/1800/3600 plus 7200 as the only evidenced values and says there is 'no client-side validation' (gui.py:105-117). The tuple includes 1200 (118), and _dpi range-checks 25-7200 (1748-1749).

</details>

<a id="gui-part1-gui1-36"></a>

### GUI1-36 -- Aim and dry-run dialogs state things the code does not do

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:4563-4571`, `tools/gui.py:230`, `tools/gui.py:2090-2110`

The aim message names the old 8-command limit, while MAX_TRAVEL_MM has been one command since param 87. The walk dialog shows the scan dpi for a walk that runs at the prescan dpi, with a fixed 23 s/frame even at 600 or 900 dpi prescans.

**Evidence (from the code):**

```text
The aim refusal says 'which would take more than {MAX_FINE_STEPS} sub-frame moves', but the guard is `abs(want) > MAX_TRAVEL_MM` (= one command). The dry-run dialog says `at {dpi} dpi` (the scan dpi) and uses `per = (23.0 if dry ...)` whatever the prescan resolution.
```

**Failure scenario:** The operator is told a click is refused because it needs more than 8 moves when it needs 2. A 900 dpi walk is forecast at 23 s/frame.

**Fix:** Word the aim message from MAX_TRAVEL_MM. Show predpi in the dry-run dialog and estimate from estimate_seconds(predpi, False) plus FORWARD_FRAME_S.

<details><summary>Second reader's check</summary>

The aim refusal text names MAX_FINE_STEPS (8) sub-frame moves (gui.py:4566-4568), but the guard is MAX_TRAVEL_MM = MAX_FINE_MM, which is one command (230, 4563). The on_roll dialog shows `at {dpi} dpi` and per=23.0 for a dry run whatever predpi is (2090-2103).

</details>

<a id="gui-part1-gui1-37"></a>

### GUI1-37 -- The demo stand-in answers some GUI actions differently from the hardware (cached-reference reuse, untied infrared timing)

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:446-453`, `rps7200/demo.py:560-564`, `rps7200/direct.py:600-629`, `tools/gui.py:1407-1415`

The 'reuse the cached reference' radio with no cache is a 3-4 minute calibration on hardware and a 1-second 'loaded' in the demo. Unticking 'infrared at scan resolution' costs ~220 s on hardware but is timed as tied in the demo, so the demo's ETA and progress disagree with the window's own estimate.

**Evidence (from the code):**

```text
DemoScanner.ensure_shading: `self._work(210.0 if not reuse else 1.0); return {"action": "loaded" if reuse else "calibrated", ...}` even when no cached file exists (the real one falls through to calibrate_shading when `not path.exists()`). DemoScanner.scan: `self._work(estimate_seconds(resolution, infrared), ...)` ignoring fast_infrared (swallowed by **kw).
```

**Failure scenario:** make run-demo appears to show that 'reuse' is instant with no cached file, and that untied IR at 300 dpi takes 25 s.

**Fix:** Have DemoScanner.ensure_shading check path.exists() like the real one, and pass fast_infrared into estimate_seconds.

<details><summary>Second reader's check</summary>

DemoScanner.ensure_shading returns 'loaded' after 1 s whenever reuse is set (demo.py:446-453). The real one calibrates when the path is missing (direct.py:600-629). DemoScanner.scan swallows fast_infrared in **kw and calls estimate_seconds(resolution, infrared) with the default fast=True (demo.py:560-564).

</details>

<a id="gui-part1-gui1-39"></a>

### GUI1-39 -- _remember cannot report a failed settings write

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:668-697`, `rps7200/settings.py:83-99`

The window's promise that a failed settings save is logged never holds. The sheet decisions kept only in gui-settings.json (the pre-commission fallback) can be lost without a word, e.g. on a read-only checkout.

**Evidence (from the code):**

```text
settings.save: `except (OSError, TypeError, ValueError): return None`; _remember ignores the return and only catches exceptions: `except Exception as exc: self._say(f"could not save the settings: {exc}")`.
```

**Failure scenario:** gui-settings.json sits in a read-only directory: every sheet decision for uncommissioned walks is lost on restart, and nothing was logged.

**Fix:** Check `settings.save(...) is None` and _say the failure.

<details><summary>Second reader's check</summary>

settings.save swallows OSError, TypeError and ValueError and returns None (settings.py:96-97). _remember ignores the return value (gui.py:676-697), so its except branch never sees write failures.

</details>

<a id="gui-part1-gui1-a2"></a>

### GUI1-A2 -- The session's close-order comment claims gzipping waits until the device is closed, but the writer thread gzips throughout the session

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1332-1346`, `rps7200/session.py:1311`, `rps7200/session.py:1046-1058`

**Doc claim:** rps7200/session.py:1332-1334; CLAUDE.md:160-163 ('A single scan compresses nothing while the device is open').

The finally block only waits for jobs still queued at shutdown. Every earlier job was gzipped the moment it was filed, including the last pass before a long idle period with the device open. The comment describes a guarantee the code gives only at shutdown, which is how GUI1-24 went unnoticed.

**Evidence (from the code):**

```text
`# Order matters: the device closes first, and only then does the writer get to spend time gzipping. The other way round is the open-and-idle state that preceded a wedge.` Yet `self._writer = FrameWriter(on_done=self._filed)` starts its thread at session open, and `_run` processes each job the moment it is queued.
```

**Failure scenario:** A reader who trusts the comment believes a single GUI scan never gzips with the device open, and skips the measurement CLAUDE.md asks for.

**Fix:** Reword the comment to say it covers only what is still queued at close, or make single-pass jobs defer compression until close.

<a id="gui-part1-gui1-38"></a>

### GUI1-38 -- TODO.md's 'dismissed calibrate prompt lets a roll reach the lazy calibration' is stale as written (the gate blocks it); the remaining route is the Roll shortcut race

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:1946-1958`, `tools/gui.py:1929-1932`, `tools/gui.py:736-741`

**Doc claim:** TODO.md:688-703.

Dismissing the prompt no longer lets any roll through. The lazy in-scan calibration is still reachable when a picture job is queued behind a Calibrate that then fails. With buttons disabled, the practical route is the unguarded Roll shortcut (GUI1-08).

**Evidence (from the code):**

```text
`_calibration_missing`: `if self.calibrated: return False; self.ask_to_calibrate(parent); return True`. Every picture action returns when it is True; 'Not now' submits nothing. on_calibrate sets `self.calibrated = True` before the job has run.
```

**Failure scenario:** Cmd-B during a Calibrate that later fails (reference None): the queued roll runs with _shading None and calibrates inside scan().

**Fix:** Update TODO.md, and set self.calibrated only from the 'calibrated' event (keep a separate 'calibration queued' flag for gating).

<details><summary>Second reader's check</summary>

_calibration_missing blocks every picture action until calibrated (gui.py:1946-1958), so TODO.md:688-703's dismissed-prompt route is stale. on_calibrate sets calibrated=True before the job runs (1932). A roll queued in that window (via the unguarded Cmd-B, or a click before the busy event is pumped) can still reach scan()'s lazy calibration if the Calibrate fails.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Window settings: controls, film stock/process/tags, output folder, geometry/sashes, presets, shortcut overrides, rolls{folder:{opened}}, sheet{folder:{ticks,offsets,rotations,flips,sources,options}} | gui-settings.json (or $RPS7200_SETTINGS, --settings, demo/gui-settings.json under --demo) | JSON; offsets are float millimetres; frame keys become strings on round-trip | n/a (not scan data) | ScannerGui._remember -> settings.save (temp .part + replace, atomic); also after set_keys, presets, _note_roll_opened, _store_sheet_state | settings.load in ScannerGui.__init__ -> _restore; _recall_sheet_state (fallback, keyed by folder NAME); on_reopen_survey (opened column) | Atomic but lossy as a record: sheet state is keyed by folder name, so it is shared by every walk into the same folder (GUI1-04). Write failures are swallowed (GUI1-39). The typed output path is persisted even though it was never applied (GUI1-14). |
| Operator/ensemble approvals per ticked frame | <rolls>/<_safe(roll field) or 'roll'>/approved.json | JSON {roll, numbering:'strip', frames:[{number, offset_mm (round 4 dp, mm), rotation, flipped, reference_entry (CWD-relative str path), source}]} | decision metadata | ScannerGui._write_approved (Tk thread, write_text, not atomic, whole-file replace) | read_approved via read_survey/open_roll, roll_exports (Export), on_delete_rolls | No. Its folder differs from the roll folder for an empty or unsanitised name (GUI1-03). Only the latest commission's frames are kept (GUI1-09). The file is truncatable, and reference_entry is relative to the launch CWD. |
| Walk manifest | <rolls>/<raw roll name or YYYY-MM-DD>/survey.json | JSON: roll, numbering, dpi, infrared, meter, film, start_at, prescan_resolution, rotation/flipped (captured once at walk start), settings{...}, frames[{number,index,transport_position,registration,error,done,prescan}] | metadata | ScanSession._roll (worker; rewritten after every frame, non-atomic; a dry run replaces any earlier survey without merge) | read_survey/open_roll, roll_summary (browser), tools/scan_roll.py | Not for orientation: rotation and flipped are the start values while the prescans may be written differently (GUI1-06). It is overwritten by the next walk into the same folder (GUI1-04). |
| Roll manifest | <rolls>/<raw roll name or YYYY-MM-DD>/roll.json | JSON: as survey.json plus wanted (union), per-frame exposure/gain/offset, done flags | metadata | ScanSession._roll (worker; merges earlier records; replaces the per-frame record by number) | read_survey, roll_summary, wanted_frames/scanned_frames | Merges different strips' frames under one number when two strips share a folder (GUI1-04). A resume from the window writes a new folder instead of merging (GUI1-05). |
| Walk prescans (sheet source and hold references on reopen) | <rolls>/<name\|date>/prescanNN.tif (+ prescanNN-before.tif) | 8-bit RGB TIFF | corrected (shading applied), turned by session.rotation/flip at filing time | FrameWriter via ScanSession._file(path=...) on dry runs; -before has no library entry | read_survey (un-oriented by the manifest's single rotation), walked_prescans | Lossless 8-bit, but orientation varies per file if rotated mid-walk (GUI1-06). Overwritten by a re-walk into the same folder. |
| Roll frame deliverables | <rolls>/<name\|date>/frameNN.tif | 16-bit TIFF (3 or 4 channels, or 1 if mono) with resolution tag | corrected, oriented (+reversal), mono per job | FrameWriter (worker writer thread) | operator/NegPy; not read back by code (Export re-derives from library) | Derivable from library. Overwritten in place by another roll into the same folder. |
| Output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+.dng), <out_dir>/<timestamp>_<dpi>dpi[_ir].*, <out_dir>/prescans/... | TIFF 16-bit or JPEG 8-bit + 4-channel LinearRaw DNG for IR | corrected, oriented, mono per job | FrameWriter, names from ScanSession._out_name via _unclaimed | operator | Lossy for JPEG by design. A failure here also drops the library entry (GUI1-02). |
| Save as / Save all / Export deliverables | user-chosen path; <folder>/<kind>NN_<dpi>dpi[_ir].tif\|.jpg; <folder>/<roll>_frameNN_... | TIFF/JPEG(+DNG), no resolution tag | corrected via library.corrected (today's code) or the reduced 1400/512-px working copy if not yet filed; mono from the window's current setting | _deliver_one (Tk thread for Save as; daemon threads for Save all/Export) | operator | No. Double-corrected for GUI single scans (GUI1-01). Export orientation ignores the entry's reversal and rotation (GUI1-10). Mono comes from the window (GUI1-11). A file can be truncated on quit (GUI1-16). |
| Library entry for a GUI single Scan | <library>/<id>/{scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, scan.json}, index.json | scan.tif 16-bit; raw.bin.gz gzip of exact USB bytes + sha256; scan.json record | scan.tif is CORRECTED while the record says raw (bug); raw.bin.gz is exact | FrameWriter -> library.save (from session._scan with raw_image=None) | _load_full, _deliver_one, library.reconstruct, roll_entry_index, demo | raw.bin.gz, reference and mask are exact and re-derivable. scan.tif is wrong (GUI1-01). No prescan.tif, even when a matching prescan exists. |
| Library entries for prescans (single and walk) and roll frames | <library>/<id>/... ; roll frames also prescan.tif | as above | scan.tif raw (last_pixels_raw / rf.raw_image); a roll frame's prescan.tif is the CORRECTED 8-bit prescan with no raw bytes of its own | FrameWriter -> library.save | same as above; read_survey via approved.json reference_entry | Frame and prescan raw bytes are exact. On a commissioned (non-dry) roll the framing prescans and hold-loop verification passes get no entry of their own, so only a corrected copy rides along. |
| Cached shading reference | calibration/shading.npz (demo/calibration/shading.npz under --demo) | npz ShadingReference | reference data | DirectScanner.ensure_shading (measure) | ensure_shading(reuse); ask_to_calibrate reads its mtime for the age note | Exact. 'reuse' can persist across launches (GUI1-26). |
| Demo tree | demo/{library,rolls,calibration,gui-settings.json,pictures.npz} | as the real ones | as real | session/FrameWriter/settings under --demo | same | Isolated, except that Delete on reopened real-roll frames reaches the real library/ (GUI1-19). |

**Second reader's corrections to this table:**

Demo tree: DemoScanner.ensure_shading never writes calibration/shading.npz (demo.py:446-453). So demo/calibration/shading.npz is never produced, and 'reuse' in the demo is not backed by any file. Demo library entries are not 'as real': their scan.tif holds corrected pixels beside a reference (GUI1-A1), so demo views are double-corrected. Library entries for prescans and roll frames: this holds on hardware only. Under --demo, prescan and roll-frame scan.tif are corrected as well. Walk prescans: the claimed per-file orientation variance holds, but reopened frames all get the manifest's single rotation as their display rotation (gui.py:4747), not the operator's later value. Delete under --demo reaches the real library only for reopened frames whose approved.json names a reference_entry, and make run-sheet opens rolls/aligned-strip, not registration-D. Save as, Save all and Export: _deliver_one runs on the Tk thread for Save as and on daemon threads for Save all and Export, as stated, and touches no widget there. It also writes a companion .dng for 4-channel JPEG and a .tif fallback without Pillow, which _unclaimed does not check.

## What the operator can do

- Calibrate once per session with the film loaded (Calibrate button, or the 'Calibrate now' prompt that every picture action raises until a calibration has been queued).
- Prescan (button, or Cmd/Ctrl-Return with a confirm), Scan (button or key with a confirm), walk a strip (Scan roll with 'dry run'), and commission ticked frames from the contact sheet. All go through ScanSession jobs.
- Move whole frames (prev/next slide) or nudge sub-frame (1 command max, param 1..87), or click-to-aim on a prescan; the frame counter does not see sub-frame moves.
- Change any setting while a job runs; it applies to the next job (except the output folder, format and orientation, which apply to the next file written -- see should_not_do).
- Arrange a pass (rotate, flip): changes the files written from now on, never the library entry.
- Save as / Save all / Export (re-corrected from library entries with today's code); Duplicate, Rename, Delete or Reveal roll folders from Rolls...
- Stop: finishes the running pass (single) or the running frame (roll). Force abort: types ABORT; abandons the read and needs a power cycle.
- Reopen a walk or unfinished roll from Rolls... and see its sheet, ticks and settings.
- Run make run-demo / make run-sheet with no scanner; outputs go under demo/.

## What the operator should not do

- Do not rotate or flip prescans while a dry-run walk is running. Later prescanNN.tif files are written in the new orientation, survey.json keeps the old one, and reopened references come back mis-oriented; the frame is also duplicated in the survey.
- Do not leave the roll name empty when scanning more than one strip per day. Everything lands in rolls/<date> and overwrites the previous strip's walk and frames, and approved.json goes to rolls/roll/.
- Do not change the roll name, or use characters _safe rewrites (spaces, '/', ':'), between walking and commissioning: approvals and the roll then live in different folders.
- Do not rely on 'press Scan chosen frames' to finish a reopened roll unless the Film panel's roll field is set to that roll's exact folder name; otherwise it writes a new folder.
- Do not leave an old contact sheet open or close it during a new walk; do not open a roll from Rolls... while a walk runs.
- Do not set a removable or network output folder that may be absent at the next launch; a failed delivered write loses the library entry.
- Do not choose 7200 dpi (or any dpi above ~3600): the host refuses it after metering, and rolls fail frame by frame.
- Do not type into 'Save scans to'; use Choose/Clear.
- Do not leave text in the Film panel's 'frame' field before a roll.
- Do not use --look-only without --demo with the scanner attached; it does not refuse anything.
- Do not delete reopened frames' library entries in the demo; they are real entries.
- Do not quit during a roll expecting it to stop; press Stop first. Do not quit during Save all/Export.
- Do not click the picture in aim mode during a job or on a prescan that is not of the frame now in the gate.
- Do not press Cmd/Ctrl-B (roll) while anything is running.

## Mistakes nothing guards against

- Roll shortcut during a running job queues a second roll and wipes the running walk's survey and ETA state (no busy check in on_roll).
- Double-click on Scan/Prescan/slide buttons queues duplicate jobs, and Stop cannot cancel the queued one.
- Aim click during a roll queues a film move that runs after the roll; aim on a stale or reopened prescan moves the film by a distance measured on a different picture.
- Missing or unwritable output folder: every scan's library entry (raw bytes) is skipped with only a log line.
- Empty roll name: approved.json is filed under rolls/roll/, walks of the same day overwrite each other, and the previous strip's sheet decisions are restored onto the next strip.
- Reopened roll continued via the sheet or the Roll button: written to a new folder; the no-sheet path via Roll rescans done frames and scans past the wanted set.
- Each commission replaces approved.json with only the ticked frames, losing earlier frames' turns and positions.
- Opening two rolls (or one twice): seq collisions attach the wrong full-resolution pixels and delete both copies.
- Contact-sheet options: infrared on B&W/Kodachrome is accepted until the backend refuses after the seek and approved.json write.
- Manual exposure is silently ignored for rolls.
- Typed output path is not applied this session but is applied, unvalidated, at the next launch.
- The 'frame' field is stamped on all roll frames, breaking the roll-to-library join and Export.
- 'Use the cached one' makes 'reuse' the remembered default for every later session.
- Save as / Save all / Export mono follows the window's current film, not the pass's.
- Delete key/menu under --demo on a reopened real walk frame removes a real library entry after one Yes/No/Cancel dialog.
- Keyboard and sheet can still submit jobs after Force abort (they fail with 'transport is not open').

## Dataflow notes

STARTUP: main() at tools/gui.py:8113 builds ScanSession(root=library|demo/library, reference=calibration/shading.npz, rolls, out_dir). It sets session.edge_reader=frame_edges.walk_reader, and under --demo replaces session._open_scanner with DemoScanner(no_film=--look-only) at 8197. ScannerGui.__init__ (360) loads settings (settings.load), then runs _build, _take_defaults (defaults captured from the built widgets) and _restore (620). _restore puts the remembered controls into the Tk vars. It pushes the output folder into session.out_dir via _set_outdir (1893) and format/quality into session.out_format/jpeg_quality via _sync_format (1577). It then calls session.start(), whose worker (session.py:1293) does open(), inquiry(), then _report_position (READ_STATE) and starts FrameWriter. The _pump timer starts at 544.

JOBS (Tk thread -> worker): each button reads the Tk vars and submits a frozen dataclass. on_calibrate(1929)->Calibrate(mode, reference) and sets self.calibrated=True optimistically. on_prescan(2037)->Prescan. on_scan(2047)->Scan after _pin_arrangement copies current.rotation/flipped into session.rotation/flip. on_roll(2077)->Roll(frames, start_at, dpi, predpi, ir, fast_ir, film, meter, dry_run, correct, mono, name=roll field, notes incl. frame). on_scan_chosen(2723) first runs _write_approved(approved) to rolls/<_safe(name)>/approved.json, then submits Roll(only, approved=Approved(offset_mm, reference array, rotation, flipped, source), reverse_hold). on_move_frames/on_nudge/_aim(2969-3018, 4531)->Move. The worker's _dispatch (session.py:1362) calls DirectScanner or DemoScanner.

IN THE WORKER: _deliver (1945) downsizes each pass to a 1400-px working copy and emits a 'result' Event. _file (2031) computes paths (roll folder + out_dir via _unclaimed) and the orientation (session.rotation/flip, or per-frame from Approved, composed with any meta['reversal']). It takes capture_record() (reference, ccd_mask, raw bytes, layout) and queues a FrameWriter job. FrameWriter._write (1060) writes the delivered files first (orient, mono, export.write), then library.save(raw_image or image, meta, reference, mask, raw). For a single Scan raw_image is missing, so the corrected image is filed (GUI1-01). Any delivered-write exception skips library.save (GUI1-02). _filed emits 'filed'(seq, entry path). _roll (1608) calls seek(), then mkdir rolls/<job.name or date>, and rewrites survey.json/roll.json after each frame from a manifest dict built once (rotation captured at start).

UI SIDE: _pump(3131) drains the _saves queue (save threads), the _reads queue (_load_full threads calling library.corrected), the _measured queue (histogram threads) and session.poll(). _handle(3181): 'state' calls _set_busy (buttons disabled only here). 'result' calls _add_result, which supersedes a prescan at the same transport position, then _arrange, which composes the orientations dict, the superseded prescan and meta['reversal'], then remember_arrangement. remember_arrangement stores orientations, appends to survey and edge_watch.add during a walk, sets _transport, and archive-downscales old results except survey ones. _show then triggers _load_full(entry) and _measure_histogram. 'filed' sets r.entry. 'transport' sets v_position/_transport. 'calibrated' sets self.calibrated. 'finished' ends the ETA; for a walk it sets _sheet_roll=session.last_roll_dir and runs on_contact_sheet, otherwise _report_held. 'failed' is logged, plus the 'No scanner' modal if there is no inquiry.

ORIENTATION STATE is split three ways: per-result (rotation/flipped), the window's orientations dict keyed ('frame', n) or ('at', position), and session.rotation/flip. The last is global and mutated from the Tk thread by _carry and _pin_arrangement, and read on the worker at filing time. It is recorded once per manifest, and per file in scan.json meta rotation/flipped.

OUTPUTS FROM THE WINDOW: on_save_as (Tk thread), on_save_all and on_export_rolls (daemon threads) all call _deliver_one(3843). _deliver_one uses library.corrected(entry) (re-correction with today's code) when scan.tif exists, otherwise the in-memory working copy. It orients by the result's arrangement (roll_exports takes it from approved.json and the roll settings) and applies the window's current mono before export.write (no resolution tag).

ROLLS ON DISK: on_reopen_survey(2226) calls rolls_on_disk, which runs roll_summary (JSON + stat only) and roll_entry_index (joins library entries on film.frame '<roll>-NN'), and opens _RollBrowser. open_roll(2552) calls read_survey(4653). read_survey reads survey.json + roll.json (renumbered), runs manifest_settings, reads approved.json, and un-orients each prescanNN.tif by the manifest's single rotation into Results (seq=-number, entry=reference_entry). open_roll then appends them to self.results, sets survey/_sheet_roll/sheet_state from approved.json, calls edge_watch.load, and _open_sheet builds a _ContactSheet. The roll name is not restored.

THREADS AND Tk: no Tk calls from worker, writer, save, histogram, full-read or edge threads were found. All hand back through queues or session events. Unsynchronised shared mutable state: session.out_dir/out_format/jpeg_quality/rotation/flip are written from Tk and read by the worker per frame. result.rotation/flipped/entry are read by save threads.
