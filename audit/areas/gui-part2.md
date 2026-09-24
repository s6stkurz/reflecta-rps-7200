# The window (tools/gui.py, second half)

Area key `gui-part2`. 37 findings: 1 critical, 8 high, 15 medium, 13 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

tools/gui.py 4100-8209 (plus the ScannerGui handlers it drives: on_scan_chosen/_write_approved/open_roll/on_export_rolls/on_delete etc. in 2037-3990, read for context and confirmed against rps7200/session.py, demo.py, library.py, export.py). The area holds the big view (full-resolution load, zoom/pan, pixel readout, histogram, aim-click nudges), the roll persistence helpers (read_survey, read_approved, roll_entry_index, roll_summary, roll_exports), the contact sheet (_ContactSheet), the frame position window (_FrameAdjuster), the Rolls table (_RollBrowser), the shortcut editor and main(). No Tk call is made from a worker thread (full-res reads, histograms and batch saves all return through queues polled by _pump), and the demo is wired only at session._open_scanner. The serious problems are elsewhere: (1) single scans from the Scan button are filed with corrected pixels labelled raw, so every full-resolution view, Save As and Save all flat-fields them a second time. The demo does the same for every pass. (2) approved.json, the one record the code itself calls non-re-derivable, is written to rolls/_safe(<roll-name field>) while the roll goes to rolls/<name or today's date>. With the default empty name it always lands in rolls/roll/. It is replaced wholesale on every commission and never read beside its roll. (3) Several paths apply one strip's per-frame decisions to another strip, or lose them: a contact sheet left open across a new walk; rotating a prescan during a walk (it is re-appended to the survey, and prescanNN.tif is written in an orientation survey.json does not record); a "Centre" decision dropped on reopen; reopened rolls whose Result.seq collide. (4) Roll export can deliver a walk's 300 dpi prescan entries in place of scanned frames, because roll_entry_index keys both by the same film.frame. (5) Unguarded operator actions: Delete on a reopened frame can rmtree a real library entry even under --demo; --look-only without --demo promises refusals the real scanner never makes; quitting during Save all/Export truncates files; heavy full-resolution work runs in-process with the device claimed and no busy guard.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [GUI2-01](#gui-part2-gui2-01) | critical | data-integrity | Scan-button passes are filed with corrected pixels labelled raw; every full-res view, Save As and Save all corrects them a second time | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [GUI2-02](#gui-part2-gui2-02) | high | demo-divergence | DemoScanner hands the session corrected pixels plus a shading reference and never any raw pixels, so demo entries are corrected-as-raw and the demo's full-res view and exports double-correct | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [GUI2-03](#gui-part2-gui2-03) | high | data-integrity | approved.json is written to rolls/_safe(<roll field>) but the roll goes to rolls/<roll field or today's date>. With the default empty name it always lands in rolls/roll/ | [P18](../problems/P18-roll-folder-identity.md) |
| [GUI2-05](#gui-part2-gui2-05) | high | bug | roll_entry_index keys walk-prescan entries and scanned-frame entries identically, so Export can deliver 300 dpi prescans as frames; a value in the Film 'frame' field breaks the join altogether | [P21](../problems/P21-roll-to-library-join.md) |
| [GUI2-07](#gui-part2-gui2-07) | high | bug | A contact sheet left open across a new dry-run walk is lifted in place of the new walk's sheet, and its decisions are filed under the new walk | [P22](../problems/P22-contact-sheet-state.md) |
| [GUI2-09](#gui-part2-gui2-09) | high | data-integrity | Rotating during a walk or roll changes the orientation of later prescanNN.tif files while survey.json keeps the start-time rotation, so a reopened walk un-orients those references wrongly | [P20](../problems/P20-rotation-during-walk.md) |
| [GUI2-10](#gui-part2-gui2-10) | high | bug | An explicit 'Centre' (zero) position is forgotten when the sheet is closed and reopened or the roll is reopened, and the detector's number silently takes its place | [P22](../problems/P22-contact-sheet-state.md) |
| [GUI2-21](#gui-part2-gui2-21) | high | user-error | A dry-run walk or roll with an empty roll name reuses rolls/<today>/, overwriting the earlier walk's survey.json and prescans and merging roll.json, with no warning | [P18](../problems/P18-roll-folder-identity.md) |
| [GUI2-A1](#gui-part2-gui2-a1) | high | hardware-safety | Every GUI single scan and prescan is gzipped into the library on the writer thread while the device is open and idle | [P13](../problems/P13-gzip-with-device-open.md) |
| [GUI2-04](#gui-part2-gui2-04) | medium | data-integrity | Each commission replaces approved.json wholesale, non-atomically, and a damaged file reads back as 'no decisions' with nothing said | [P19](../problems/P19-roll-manifests.md) |
| [GUI2-06](#gui-part2-gui2-06) | medium | bug | Roll export orients frames from approved.json and the roll-wide setting, not from the orientation each entry recorded, so a composed reversal and misfiled per-frame turns are lost | -- |
| [GUI2-08](#gui-part2-gui2-08) | medium | bug | remember_arrangement appends to the survey on every arrangement change during a walk: rotating a prescan duplicates it, and rotating an older walk's prescan injects it into the new walk | [P20](../problems/P20-rotation-during-walk.md) |
| [GUI2-11](#gui-part2-gui2-11) | medium | user-error | Deleting a reopened frame from the filmstrip can rmtree a real library entry, even under --demo whose promise is that nothing touches the real library | [P25](../problems/P25-reopened-frames.md) |
| [GUI2-12](#gui-part2-gui2-12) | medium | bug | Reopened frames are numbered seq=-frame, so two opened rolls (or one opened twice) collide: the wrong picture is shown, keyboard walking can raise, and Delete removes both | [P25](../problems/P25-reopened-frames.md) |
| [GUI2-13](#gui-part2-gui2-13) | medium | hardware-safety | --look-only without --demo promises refusals the real scanner never makes, and offers an empty-transport calibration | [P28](../problems/P28-look-only-drives-real-scanner.md) |
| [GUI2-14](#gui-part2-gui2-14) | medium | concurrency | Opening a roll from the Rolls table is not guarded against a running job, so a walk in progress mixes with the reopened survey | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI2-15](#gui-part2-gui2-15) | medium | bug | The Frame position window outlives its contact sheet; later edits go into a destroyed sheet, raise TclError and are lost | [P22](../problems/P22-contact-sheet-state.md) |
| [GUI2-16](#gui-part2-gui2-16) | medium | data-integrity | Decisions in an open sheet are lost when the main window quits or another roll is opened | [P22](../problems/P22-contact-sheet-state.md) |
| [GUI2-17](#gui-part2-gui2-17) | medium | user-error | Quitting while Save all or Export is writing kills the daemon writer mid-file with no warning | [P24](../problems/P24-disk-full-and-quitting.md) |
| [GUI2-18](#gui-part2-gui2-18) | medium | hardware-safety | Heavy full-resolution work runs in the GUI process with the device claimed: no busy guard, and unbounded full-res loads on every show or rotate | [P13](../problems/P13-gzip-with-device-open.md) |
| [GUI2-19](#gui-part2-gui2-19) | medium | user-error | Aim-click moves film with no busy guard and no check that the prescan clicked shows where the film is now | [P23](../problems/P23-busy-guards-and-stop.md) |
| [GUI2-20](#gui-part2-gui2-20) | medium | bug | Reopening a roll that has no walk restores the original frame count and moves start-at, so the Roll button rescans done frames or runs past the roll | [P25](../problems/P25-reopened-frames.md) |
| [GUI2-22](#gui-part2-gui2-22) | medium | doc-mismatch | The sheet's 'nudge registration between frames' tick has no effect, and its header describes a rewind and a partial nudge that do not happen | -- |
| [GUI2-23](#gui-part2-gui2-23) | medium | bug | Per-frame turns from a commissioned roll persist in the window and are applied to the next plain roll's frames of the same number, on screen and in Save As but not in the delivered files | [P25](../problems/P25-reopened-frames.md) |
| [GUI2-24](#gui-part2-gui2-24) | low | bug | Roll export and Save all apply the window's current mono setting, not each roll's or pass's own | -- |
| [GUI2-25](#gui-part2-gui2-25) | low | data-integrity | Frames the detector has not reached yet are recorded in approved.json with source 'operator' | -- |
| [GUI2-26](#gui-part2-gui2-26) | low | doc-mismatch | 'Nothing already there is overwritten' is false for the DNG sidecar and the no-Pillow JPEG fallback | -- |
| [GUI2-27](#gui-part2-gui2-27) | low | design | Constants retyped instead of taken from the driver, and an aim message that states the wrong limit | -- |
| [GUI2-28](#gui-part2-gui2-28) | low | bug | Re-arranging an old or reopened pass overwrites the 'last said' transport position used by confirm dialogs | -- |
| [GUI2-29](#gui-part2-gui2-29) | low | doc-mismatch | The shortcut editor says no shortcut starts a scan, but default keys start a prescan, a scan and a roll | -- |
| [GUI2-30](#gui-part2-gui2-30) | low | doc-mismatch | The Delete-roll dialog and README promise the roll can be rebuilt from the library; nothing rebuilds a roll folder | -- |
| [GUI2-31](#gui-part2-gui2-31) | low | design | Renaming or duplicating a roll leaves window state keyed by the old folder; duplicates share one library key; copytree runs on the UI thread | -- |
| [GUI2-32](#gui-part2-gui2-32) | low | user-error | Save As on a pass whose filing is pending or failed writes the reduced working copy (as small as 512 px) under the user's chosen name | -- |
| [GUI2-A2](#gui-part2-gui2-a2) | low | bug | Save As, Save all and roll Export write files without the scan resolution, so TIFFs are tagged 72 dpi | -- |
| [GUI2-A3](#gui-part2-gui2-a3) | low | error-handling | Save As re-corrects the full-resolution entry on the UI thread with no error handling | -- |
| [GUI2-A4](#gui-part2-gui2-a4) | low | user-error | 'Scan chosen frames' closes the sheet and adjuster before any refusal or cancel, and the 'over the sheet' prompt parent is dead code | -- |
| [GUI2-A5](#gui-part2-gui2-a5) | low | doc-mismatch | approved.json, the Move job and the window's internals still hold transport distances in millimetres | -- |

## Findings in full

<a id="gui-part2-gui2-01"></a>

### GUI2-01 -- Scan-button passes are filed with corrected pixels labelled raw; every full-res view, Save As and Save all corrects them a second time

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1457`, `rps7200/session.py:1086-1097`, `rps7200/library.py:228`, `rps7200/library.py:374-388`, `tools/gui.py:3858-3868`, `tools/gui.py:4104-4118`

**Doc claim:** CLAUDE.md:114-126 ('The library holds raw pixels ... `library.save` takes raw pixels and `corrections=` is how a caller admits it is handing over something else'); README.md:237 ('what reaches `library/` is the raw negative'); tools/gui.py:11-13 module docstring ('Files are written exactly as the command-line tools write them: raw negatives')

Every single scan taken with the Scan button is stored as the corrected decode, next to the shading reference and CCD mask, and its record claims raw. The library therefore does not hold the exact decode for these passes, contrary to the owner's central requirement and CLAUDE.md. Anything that re-derives from the entry divides by the flat field twice: the big view once full resolution arrives, the histogram re-measure, the hover readout ('exact'), Save As, Save all and roll Export. `reconstruct` also reports a changed decode for each such entry, the false-alarm pattern CLAUDE.md describes.

**Evidence (from the code):**

```text
session._scan: `image, meta = self._scanner.scan(..., keep_raw=True)` then `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)`. No raw_image is passed, unlike _prescan (`raw_image = getattr(self._scanner, "last_pixels_raw", None)`, session.py:1415). FrameWriter: `library.save(job["image"] if raw_image is None else raw_image, job["meta"], ..., **job["capture"])` with no `corrections=`, so library.py:228 records `"corrections_applied": list(corrections or [])` = []. DirectScanner.scan returns the shading-corrected image (direct.py:2708 `raw_pixels = image`, 2733 `image, shading_report = apply_shading(...)`, 2818 `self.last_pixels_raw = raw_pixels`). library.corrected then runs `image, report = apply_shading(image, record["reference"], record["ccd_mask"])` (388) on those already-corrected pixels. The GUI reads through exactly that: _load_full `image, _ = library.corrected(entry)` (4110) and _deliver_one `full, entry_record = library.corrected(result.entry)` (3864).
```

**Failure scenario:** Operator scans one frame at 1800 dpi with the Scan button and then presses Save as .... _deliver_one calls library.corrected, which applies the per-column reference to pixels DirectScanner already corrected. The delivered TIFF carries the inverse of the ~39% column falloff (bright/dark banding across x), while the working copy on screen looked right until the full-res load replaced it. The log line says 'at full resolution' with no warning.

**Fix:** In ScanSession._scan, pass `raw_image=getattr(self._scanner, "last_pixels_raw", None)`, read immediately after scan(), exactly as _prescan does. Make FrameWriter pass `corrections=["shading"]` whenever it falls back to job["image"] while the capture carries a reference. Run `tools/library.py migrate-raw`/`reconstruct` over the GUI-filed single scans. Add a session test asserting a Scan job's entry holds last_pixels_raw.

<details><summary>Second reader's check</summary>

session.py:1440-1457 `_scan` calls `self._file(seq, 0, image, meta, ...)` with no raw_image, unlike `_prescan` (1415 `raw_image = getattr(self._scanner, "last_pixels_raw", None)`). DirectScanner.scan returns the corrected image (direct.py:2708 `raw_pixels = image`, 2733 `image, shading_report = apply_shading(...)`, 2828 `return image, meta`), and only `last_pixels_raw` holds the decode. FrameWriter._write (session.py:1088-1097) saves `job["image"] if raw_image is None else raw_image` with no `corrections=`, so library.py:228 records `corrections_applied: []`. capture_record (direct.py:641-646) still hands over the reference and mask, so library.corrected (388) runs apply_shading a second time. The GUI reads only through library.corrected (_load_full 4110, _deliver_one 3864). No guard anywhere prevents it.

</details>

<a id="gui-part2-gui2-02"></a>

### GUI2-02 -- DemoScanner hands the session corrected pixels plus a shading reference and never any raw pixels, so demo entries are corrected-as-raw and the demo's full-res view and exports double-correct

**Severity** high · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:1009-1010`, `rps7200/demo.py:435-442`, `rps7200/demo.py:771-779`, `rps7200/session.py:1415`, `tools/gui.py:4110`, `tools/gui.py:3864`

**Doc claim:** CLAUDE.md:189-211 ('--demo may change what the software is fed. It may not change what the software does')

For prescans and roll frames the real path files raw pixels (last_pixels_raw, rf.raw_image). The demo files the corrected image together with a reference, so library.corrected corrects it again. What the demo shows at full resolution, and what it saves or exports, is not what the real software would produce from the same bytes. This is the 'plausible and wrong' divergence CLAUDE.md warns about, and it hides GUI2-01, because in the demo every pass looks equally wrong.

**Evidence (from the code):**

```text
demo._decode: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)`; capture_record: `return dict(self._capture)` with `"reference": reference, "ccd_mask": mask`. DemoScanner is not a DirectScanner subclass and defines no `last_pixels_raw`, so session._prescan's `getattr(self._scanner, "last_pixels_raw", None)` is None. Its roll yields `RollFrame(index=..., image=image, meta=..., prescan=prescan, registration=marks, prescan_meta=prescan_meta)` with no raw_image/raw_prescan.
```

**Failure scenario:** make run-demo, then Prescan. When the full-resolution read lands, the big view changes from the working copy (singly corrected) to a doubly flat-fielded picture. Save as ... writes the doubled version. Someone judging striping or the correction by eye in the demo sees an artefact the hardware path does not have for prescans and roll frames.

**Fix:** Give DemoScanner a `last_pixels_raw` set to the pre-apply_shading decode on every pass. Carry `raw_image`/`raw_prescan` on its RollFrames, or subclass/borrow the attribute contract from DirectScanner as it already does for _hold_to_approved. Add a demo test that a filed entry's scan.tif equals decode_raw(entry).

<details><summary>Second reader's check</summary>

demo._decode (1009-1010) applies shading and returns the reference in the capture. DemoScanner defines no `last_pixels_raw`, so session._prescan's getattr gets None, and demo.scan_roll's RollFrame (771-779) carries no raw_image/raw_prescan. Every demo pass is therefore filed corrected, with a reference beside it and `corrections_applied` empty. It gets worse: a demo prescan read from `prescan.tif` (_pair_image 1176-1182) does not refresh `self._capture`, so it is filed with the reference and mask of whichever entry was decoded last. Also, demo.scan corrects even when shading=False, because _decode corrects unconditionally.

</details>

<a id="gui-part2-gui2-03"></a>

### GUI2-03 -- approved.json is written to rolls/_safe(<roll field>) but the roll goes to rolls/<roll field or today's date>. With the default empty name it always lands in rolls/roll/

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2940-2943`, `tools/gui.py:2862`, `tools/gui.py:2156`, `rps7200/session.py:1634-1635`, `rps7200/session.py:2181-2184`, `tools/gui.py:1520-1531`, `tools/gui.py:2552-2660`, `tools/gui.py:2630-2644`

**Doc claim:** README.md:287 ('Opening a roll with frames left brings back its contact sheet and approvals'); README.md:349-350 ('Turns survive closing the window, in `approved.json`'); tools/gui.py:4927-4930 read_approved docstring ('approved.json is the one thing in a roll folder that is not derivable')

The GUI and the session derive the roll folder by different rules. In the three ordinary cases the operator's non-re-derivable decisions (per-frame offsets, turns, flips, reference_entry, source) are not beside the roll: (a) empty name, the default after every launch, puts approved.json in rolls/roll/, shared and overwritten by every roll ever commissioned, while the roll is in rolls/YYYY-MM-DD; (b) a name _safe changes ('Portra 400' -> rolls/Portra-400 vs rolls/Portra 400); (c) a roll reopened from the Rolls table is commissioned under whatever the field says, so the resume goes to a new folder and the original roll.json never learns the frames were done. read_survey/open_roll then find no approved.json and reopen with no decisions. rolls/roll has no manifest, so roll_summary returns None and nothing ever reads that file.

**Evidence (from the code):**

```text
_write_approved: `name = _safe(self.fields["roll"].get().strip())`, `folder = Path(self.session.rolls) / name`. on_scan_chosen submits `Roll(..., name=self.fields["roll"].get().strip(), ...)`. session._roll: `name = job.name or time.strftime("%Y-%m-%d")`, `out = Path(job.out) if job.out else self.rolls / name` (not sanitised). `_safe`: `cleaned = "".join(kept).strip("-.") or "roll"`. The 'roll' field is a plain StringVar that is deliberately not remembered (REMEMBERED_FILM = stock, process, tags). open_roll never sets it, and sets sheet_state from `out["offsets"]`/`out["rotations"]` read from approved.json only.
```

**Failure scenario:** Operator walks a strip (rolls/2026-09-23), turns frames 3 and 7 upright, drags frame 5 by +12 units and presses Scan chosen frames with the roll-name field empty. approved.json goes to rolls/roll/approved.json. The next day he opens 2026-09-23 from Rolls: '0 with a position already set', frames 3/7 come back unturned, and Export arranges them from the roll-level rotation. The next commission of any other roll overwrites rolls/roll/approved.json.

**Fix:** Have a single source of truth for the roll folder: when commissioning from a sheet, pass `out=str(self._sheet_roll)` (or the reopened `_loaded_roll`) on the Roll, and have _write_approved use the same path, never the text field. Sanitise the name identically on both sides (call _safe in session._roll too). Refuse or warn when the resolved folder differs from the walk's folder. Add a test that approved.json and roll.json land in the same directory for an empty name and for a name containing a space.

<details><summary>Second reader's check</summary>

_write_approved (2940-2941): `name = _safe(self.fields["roll"].get().strip())`, `folder = Path(self.session.rolls) / name`. session._roll (1634-1635): `name = job.name or time.strftime("%Y-%m-%d")`; `out = ... self.rolls / name`, with no _safe. session._safe returns 'roll' for an empty string (2184). fields['roll'] is used only at 2156, 2862 and 2940, and open_roll never sets it, so a reopened roll is commissioned under whatever the field holds. read_survey/roll_exports read approved.json from the roll's own folder, so the decisions are not found.

</details>

<a id="gui-part2-gui2-05"></a>

### GUI2-05 -- roll_entry_index keys walk-prescan entries and scanned-frame entries identically, so Export can deliver 300 dpi prescans as frames; a value in the Film 'frame' field breaks the join altogether

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `tools/gui.py:4999-5008`, `tools/gui.py:4892-4905`, `rps7200/session.py:1817-1827`, `rps7200/session.py:1880-1882`, `tools/gui.py:1724-1731`, `tools/gui.py:6997-6998`

**Doc claim:** README.md:280-282 ('so is one whose library entries have gone, because that is the one that cannot be exported')

A walked-then-scanned roll has two entries per chosen frame under one key: the walk's prescan and the scan. Which one Export re-corrects depends on directory order. A walked-only roll shows as exportable, and exports its prescans named at the scan resolution (e.g. `..._frame05_3600dpi.tif` holding a 431-px prescan). The browser's 'orphaned' marker (`summary["done"] and not summary["entries"]`) is defeated by the prescan entries. If the operator had typed anything into the Film panel's 'frame' box, every roll frame gets that literal value, the join finds nothing, and the roll reads as orphaned although its entries exist.

**Evidence (from the code):**

```text
roll_entry_index: `frame = str(((record.get("film") or {}).get("frame") or "")).strip()`; `roll, _, number = frame.rpartition("-")`; `out.setdefault(roll, {})[int(number)] = record_path.parent` (last glob hit wins; `root.glob("*/scan.json")` order is filesystem-dependent). A dry-run walk files each prescan as its own entry with `replace(job.notes, frame=job.notes.frame or f"{name}-{number:02d}")` (session.py:1827), and a roll frame with the same key (1881). _notes() passes `frame=self.fields["frame"].get().strip()`. roll_exports labels every item `kind="frame"` with `"resolution_dpi": settings.get("resolution") or 0`.
```

**Failure scenario:** On Linux (ext4 hash order), Rolls -> Export on a roll walked at 300 dpi and scanned at 3600 dpi writes some frames from the 300 dpi walk-prescan entries. The log says 'exported ... at full resolution' and the file name says 3600dpi.

**Fix:** Filter by the entry's tags (skip 'prescan'), or by scan.resolution_dpi == the roll's resolution, and prefer the newest entry deterministically (sort entry ids). Better still, record each frame's entry path in roll.json from the writer's on_done callback. Never let FilmNotes.frame override the roll key for roll frames: store the roll/frame key in its own record field.

<details><summary>Second reader's check</summary>

session.py:1827 files dry-run prescan entries with `frame=job.notes.frame or f"{name}-{number:02d}"`, and 1880-1882 does the same for roll frames. roll_entry_index (4999-5008) keeps the last glob hit per key and does not filter by tags or resolution. roll_exports labels every item with the roll's resolution_dpi. A walk and a commission on the same day with an empty name share the key (both default to today's date). FilmNotes.frame from the Film panel overrides the key for every frame.

</details>

<a id="gui-part2-gui2-07"></a>

### GUI2-07 -- A contact sheet left open across a new dry-run walk is lifted in place of the new walk's sheet, and its decisions are filed under the new walk

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:2112-2133`, `tools/gui.py:3249-3262`, `tools/gui.py:2170-2173`, `tools/gui.py:7917-7927`, `tools/gui.py:2340-2350`, `tools/gui.py:2359-2360`

The code's own comment at 2121-2124 says ticks, positions and turns keyed by frame number must not reach a new strip. The sheet is non-modal, though, and survives the walk. At the end of walk B the window raises sheet A (A's thumbnails and references) instead of building B's. Closing A, even during the walk, stores A's state as `sheet_state`, and under B's folder key once the walk has finished. The next 'Contact sheet ...' then opens B with A's ticks, operator positions, rotations and flips. Pressing 'Scan chosen frames' on A commissions B's strip with A's prescans as references.

**Evidence (from the code):**

```text
on_roll (dry): `self.survey = []`, `self.orientations = {}`, `self.sheet_state = {}`, `self._sheet_roll = None` but nothing touches `self.sheet`. At walk end: `self._sheet_roll = getattr(self.session, "last_roll_dir", None)` ... `if self.survey: self.on_contact_sheet()`, and on_contact_sheet does `if self.sheet is not None and self.sheet.alive(): self.sheet.top.lift(); ...; return`. _dismiss: `self.gui._store_sheet_state(self.state())`, which sets `self.sheet_state = state` and `sheets[self._sheet_key()] = state`.
```

**Failure scenario:** Operator walks strip A and looks at the sheet. Without closing it, he loads strip B and presses Scan roll with dry run. When B finishes, sheet A comes to the front. He closes it and reopens: B's frames 3 and 7 are turned 90 degrees (A's decisions) and scanned sideways, and A's hand positions are held on B's frames.

**Fix:** In on_roll's dry branch (and anywhere survey is replaced), close any open sheet and adjuster without storing. Tag the sheet with the generation or folder it was built for, and make _dismiss/_store_sheet_state refuse to store when that differs from the current _sheet_roll.

<details><summary>Second reader's check</summary>

on_roll's dry branch (2112-2133) resets survey, orientations, sheet_state and _sheet_roll but never closes self.sheet. At walk end (3258-3262), `_sheet_roll = last_roll_dir` is set and on_contact_sheet only lifts an alive sheet (2170-2173). _dismiss -> _store_sheet_state (2340-2350) sets sheet_state, and after the walk ends it also stores under the new walk's key. _recall_sheet_state then prefers sheet_state (2359-2360).

</details>

<a id="gui-part2-gui2-09"></a>

### GUI2-09 -- Rotating during a walk or roll changes the orientation of later prescanNN.tif files while survey.json keeps the start-time rotation, so a reopened walk un-orients those references wrongly

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `rps7200/session.py:1687-1688`, `rps7200/session.py:1732-1733`, `rps7200/session.py:2029`, `tools/gui.py:3920-3923`, `tools/gui.py:7739-7742`, `tools/gui.py:4724-4749`

prescanNN.tif is the stored reference a reopened roll is decided and held against. Frames filed after an arrangement change are written in the new orientation but un-oriented by the old one. Reopened, they appear turned or mirrored in the sheet. The frame-edge detector reads the wrong axis and proposes 'measured' positions from garbage, and _hold_to_approved correlates a mis-oriented reference against a fresh pass. The library entries record each prescan's own `scan.rotation`, but nothing reads it.

**Evidence (from the code):**

```text
The manifest captures `"rotation": self.rotation, "flipped": self.flip` once when the roll starts, and the same dict is rewritten after each frame. Each prescan is filed with `_orientation_for(number, "prescan")`, which does `return self.rotation, self.flip` at filing time. The GUI mutates that mid-run: `_carry`: `self.session.rotation = result.rotation; self.session.flip = result.flipped`; sheet `_all`: `self.gui.session.rotation = last.rotation`. read_survey: `turn = int(manifest.get("rotation") or 0)` ... `image=preview.unorient(image, turn, mirrored)` for every frame.
```

**Failure scenario:** The first prescan of a walk is sideways and the operator rotates it (a natural action). Frames 2..17's prescanNN.tif are stored rotated 90 degrees while survey.json says 0. Next day he opens the walk: 16 frames are sideways on the sheet, their proposed positions are wrong, and a commission holds them to those positions.

**Fix:** Record the orientation used per frame in the survey record (the value _file computed), and un-orient each prescan by its own record in read_survey. Alternatively write prescanNN.tif unarranged, since it is a reference and not a deliverable.

<details><summary>Second reader's check</summary>

The manifest captures `"rotation": self.rotation` once at roll start (session.py:1687-1688, 1732-1733). Each prescan is filed with `_orientation_for(number, 'prescan')`, which returns the current `self.rotation, self.flip` (2029). FrameWriter writes prescanNN.tif oriented by that value (1068-1069). _carry mutates session.rotation/flip from the UI thread mid-walk (3920-3921). read_survey un-orients every prescan by the single manifest value (4722-4723, 4739).

</details>

<a id="gui-part2-gui2-10"></a>

### GUI2-10 -- An explicit 'Centre' (zero) position is forgotten when the sheet is closed and reopened or the roll is reopened, and the detector's number silently takes its place

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:6715-6721`, `tools/gui.py:7898-7910`, `tools/gui.py:5519-5534`, `tools/gui.py:4963-4964`, `tools/gui.py:7832-7833`, `tools/gui.py:5739-5751`

The docstrings (Approved: 'Zero is a real answer ... not the same as having no approval at all'; _propose_positions: 'His number is the authority here and stays it') are not honoured for zero. Within one sheet the operator note protects it. After any close/reopen, via settings or approved.json, the recorded source is ignored because the offset key is missing, and the detector's reading is applied and captioned '(measured)'. Before that the caption for a centred frame shows only 'contrast x.xx', indistinguishable from an untouched frame.

**Evidence (from the code):**

```text
_FrameAdjuster._set: `if value: self.sheet.offsets[self.number] = value else: self.sheet.offsets.pop(self.number, None)` then `self.sheet.proposals[self.number] = {"source": "operator", ...}`. state() stores offsets (without the zero) and sources (with 'operator'). _merge_kept iterates only `for number, value in kept.items()`, so a zero never re-creates the operator note. read_approved: `if record.get("offset_mm"): offsets[number] = ...` drops 0.0. take_readings then applies the detector because `(self.proposals.get(number) or {}).get("source") != "operator"`.
```

**Failure scenario:** The detector proposes +12 units for frame 5 and the operator disagrees and presses Centre. He closes the sheet to look at the preview and reopens it: frame 5 reads '+12.0 units (measured)'. He commissions, and the roll holds frame 5 twelve units off where he left it.

**Fix:** Store explicit zeros (offsets[n] = 0.0 with source 'operator'), keep them through state()/_clean_sheet_state/read_approved (use `is not None`, as rotations already do), and have _merge_kept honour `remembered[n] == "operator"` even without an offset. Caption an operator zero as 'as walked (yours)'.

<details><summary>Second reader's check</summary>

_FrameAdjuster._set (6715-6721) pops a zero offset but marks the source operator. state() stores offsets without the zero. _merge_kept (5520) iterates only kept offsets, so an operator zero never recreates the note. _open_sheet (2202) passes the result as proposals, and take_readings (7832-7833) then applies the detector's value because the source is not 'operator'. read_approved (4963) also drops 0.0 through `if record.get("offset_mm")`.

</details>

<a id="gui-part2-gui2-21"></a>

### GUI2-21 -- A dry-run walk or roll with an empty roll name reuses rolls/<today>/, overwriting the earlier walk's survey.json and prescans and merging roll.json, with no warning

**Severity** high · **Category** user-error · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2156`, `tools/gui.py:2102-2111`, `rps7200/session.py:1634-1650`, `rps7200/session.py:1817`, `rps7200/session.py:1074-1081`, `rps7200/session.py:640-646`

The confirm dialog names neither the folder nor a collision. A second strip walked the same day overwrites the first walk's survey.json (transport positions, registration and the frame list the sheet is built from) and its prescanNN.tif. A second strip scanned the same day merges its frames into the first strip's roll.json by frame number, and frameNN.tif files are overwritten.

**Evidence (from the code):**

```text
on_roll: `name=self.fields["roll"].get().strip()` (empty by default). session: `name = job.name or time.strftime("%Y-%m-%d")`; `manifest_path = out / ("survey.json" if job.dry_run else "roll.json")`; `earlier` is read only `if not job.dry_run and manifest_path.exists()`. Prescans go to `surveyed = out / f"prescan{number:02d}.tif"`, written by FrameWriter's `export.write(str(path), ...)` with no _unclaimed. The walked_prescans docstring records that this has happened: '`rolls/2026-09-23` holds prescan01-02 from the walk its survey lists ... beside prescan03-06 from an earlier walk'.
```

**Failure scenario:** Morning: walk strip 1. Afternoon: walk strip 2 without typing a name. Strip 1 can no longer be reopened as a sheet, and its survey records are gone (the library keeps the raw prescans but not the walk's positions).

**Fix:** When the resolved folder already holds a survey.json/roll.json for a different walk, ask or choose the next free name (duplicate_name already exists). Show the target folder in the Scan roll dialog.

<details><summary>Second reader's check</summary>

on_roll passes the empty field (2156). session._roll defaults to today's date (1634-1635). survey.json is written fresh for a dry run, and `earlier` is read only for non-dry runs (1649). prescanNN.tif paths go straight to FrameWriter/export.write with no _unclaimed (1817, 1067). The confirm dialog (2102-2110) names neither the folder nor a collision.

</details>

<a id="gui-part2-gui2-a1"></a>

### GUI2-A1 -- Every GUI single scan and prescan is gzipped into the library on the writer thread while the device is open and idle

**Severity** high · **Category** hardware-safety · **Verdict** found-by-verifier · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:1311`, `rps7200/session.py:1086-1097`, `rps7200/session.py:1292-1296`, `rps7200/library.py:186-205`, `rps7200/session.py:1013-1020`

**Doc claim:** CLAUDE.md:156-159 ('A single scan compresses nothing while the device is open ... gzipping one with the scanner open and idle preceded a wedge once') and CLAUDE.md:368-369

The overlap argument covers rolls only. After a Scan-button pass or a Prescan the worker has no next job, so the scanner sits open and idle while tens to hundreds of MB are gzipped in the same process. CLAUDE.md says gzipping a library entry with the device open and idle preceded a wedge, and that 'a single scan compresses nothing while the device is open'. That statement describes DirectScanner's debug filing, not the GUI path every operator uses.

**Evidence (from the code):**

```text
session._run opens the scanner once (`self._scanner.open()`, 1295) and holds it for the window's life. `self._writer = FrameWriter(on_done=self._filed)` (1311). Every _file goes to `library.save(...)`, which does `with gzip.open(path / "raw.bin.gz", "wb", compresslevel=6)`. FrameWriter's own docstring: 'On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.'
```

**Failure scenario:** The operator presses Scan at 3600 dpi RGBI. The pass returns, and the writer thread gzips ~140 MB of raw bytes for several seconds while the device is open and idle, which is the state recorded as preceding a wedge.

**Fix:** Either measure it (tools/filing_load_test.py for the idle case) or defer compression of single passes until the session closes or the next job starts. At minimum, correct CLAUDE.md so it describes the GUI path.

<a id="gui-part2-gui2-04"></a>

### GUI2-04 -- Each commission replaces approved.json wholesale, non-atomically, and a damaged file reads back as 'no decisions' with nothing said

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/gui.py:2831`, `tools/gui.py:2943-2955`, `tools/gui.py:4952-4956`, `tools/gui.py:5818-5824`

**Doc claim:** tools/gui.py:2487-2490 on_delete_rolls docstring ('Everything in a roll folder is re-derivable from the library except approved.json')

Resuming a roll ticks only the frames left, so the file is rewritten with just those, and the done frames' positions, turns, reference_entry and provenance are erased. It is rewritten even when the roll then refuses at the seek and scans nothing. A crash or full disk during write_text leaves truncated JSON, which every reader treats silently as an empty file.

**Evidence (from the code):**

```text
`(folder / "approved.json").write_text(json.dumps({... "frames": [{...} for a in approved]}, indent=2, default=str), encoding="utf-8")`. `approved` holds only the currently ticked frames (approved_from_sheet: `if number is None or number not in picked: continue`). It is written before the Roll is submitted (`self._write_approved(approved)` at 2831). read_approved: `except (OSError, ValueError, AttributeError): return offsets, rotations, flips, entries, sources` with no log.
```

**Failure scenario:** A roll of 24 frames dies after 11. The operator reopens it (with a matching roll name), the sheet ticks 12-24, he commissions. approved.json now holds only 12-24. A later Export or reopen arranges frames 1-11 from the roll default and has lost their reference entries. If the seek refused (strip put in the wrong way), the loss happened for nothing.

**Fix:** Merge into the existing file by frame number (as session._roll does for roll.json) instead of replacing it. Write it via a temporary file plus os.replace. Have read_approved log, or return a flag, when the file exists but cannot be parsed.

<details><summary>Second reader's check</summary>

approved_from_sheet (5818-5820) keeps only ticked frames. _write_approved replaces the file with write_text (2943) before the Roll is submitted, and runs even when the roll then refuses at the seek. read_approved (4952-4956) returns empty dicts on a parse error without logging. Offsets are also passed through snap_offset, which clamps them, before the rounding.

</details>

<a id="gui-part2-gui2-06"></a>

### GUI2-06 -- Roll export orients frames from approved.json and the roll-wide setting, not from the orientation each entry recorded, so a composed reversal and misfiled per-frame turns are lost

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:4885-4898`, `rps7200/library.py:243`, `rps7200/session.py:2102-2110`

The entry knows exactly how its delivered frameNN.tif was arranged, including a carriage-reversal half turn. Export ignores that and rebuilds it from approved.json, which is usually not beside the roll (GUI2-03) or has been truncated (GUI2-04), and from the roll's start-time rotation, which a mid-roll rotate changes (GUI2-09). Exported files can therefore be turned or mirrored differently from the files the roll delivered.

**Evidence (from the code):**

```text
roll_exports: `_, rotations, flips, _, _ = read_approved(folder)`; `turn = int(settings.get("rotation") or 0)`; `rotation=rotations.get(number, turn), flipped=flips.get(number, mirrored)`. The session files the delivered orientation into the entry: `turn, flip = preview.compose((int(reversal[0]), bool(reversal[1])), (turn, flip))`; `meta = dict(meta, rotation=turn, flipped=flip)`, and library.save keeps `"rotation", "flipped", "reversal"` under record['scan'].
```

**Failure scenario:** A frame came back reversed and the session composed a 180 degree turn into frame07.tif. Export reads rotation 0 from approved.json and writes frame 7 upside down.

**Fix:** In roll_exports, read record['scan']['rotation'/'flipped'] from each entry's scan.json (already opened by roll_entry_index) and use approved.json only as a fallback for legacy entries without them.

<details><summary>Second reader's check</summary>

roll_exports (4885-4898) orients from read_approved plus settings['rotation'/'flipped']. session._file (2102-2110) composes `reversal` into rotation/flipped, and library.save stores them under record['scan'] (library.py:243). roll_entry_index reads scan.json but keeps only the folder. A reversed frame therefore loses its composed half-turn on Export, and GUI2-03 and GUI2-04 make approved.json unreliable as a source.

</details>

<a id="gui-part2-gui2-08"></a>

### GUI2-08 -- remember_arrangement appends to the survey on every arrangement change during a walk: rotating a prescan duplicates it, and rotating an older walk's prescan injects it into the new walk

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `tools/gui.py:3498-3505`, `tools/gui.py:3911-3924`, `tools/gui.py:7660-7677`, `tools/gui.py:7163-7164`, `tools/gui.py:7515-7517`

This side effect was meant for newly arrived prescans, but it also fires when a pass is rotated or flipped while a walk runs. Rotating the first prescan of a walk, which on_rotate's docstring invites, puts that frame in `self.survey` twice. The sheet then builds two cells for one number, `self.ticks[number]` and `_rings[number]` point at the second, and the first cell's checkbox no longer controls anything. Rotating a numbered prescan from an earlier walk (still in the filmstrip) appends it to the new survey, and EdgeWatch.add replaces the new frame's picture with it, so the detector proposes a position for frame N from a different photograph.

**Evidence (from the code):**

```text
remember_arrangement: `if self._surveying and result.kind == "prescan" and result.number: self.survey.append(result); self.edge_watch.add(result.number, result.image)`. It is called from _arrange (new results), from `_carry` (on_rotate/on_flip, line 3924) and from the sheet's `_orient` (`self.gui.remember_arrangement(result)`, 7677).
```

**Failure scenario:** During a walk, frame 1's prescan arrives sideways and the operator presses Rotate right. The contact sheet shows frame 1 twice. Unticking the first cell's checkbox has no effect on what is scanned, and approved.json gets two records for frame 1.

**Fix:** Split the survey append out of remember_arrangement and call it only from _add_result for results whose number is not already in the survey and which belong to the running walk (e.g. by seq > walk start seq).

<details><summary>Second reader's check</summary>

remember_arrangement (3498-3505) appends to self.survey whenever `_surveying and kind == 'prescan' and number`, with no duplicate check. It is called from _arrange, from _carry (on_rotate/on_flip, 3924) and from the sheet's orient handler. Rotating any numbered prescan during a walk appends it again and calls edge_watch.add with it.

</details>

<a id="gui-part2-gui2-11"></a>

### GUI2-11 -- Deleting a reopened frame from the filmstrip can rmtree a real library entry, even under --demo whose promise is that nothing touches the real library

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:4742`, `tools/gui.py:3999-4015`, `tools/gui.py:8083-8087`, `tools/gui.py:8162-8167`, `tasks.py:184-185`

The Delete action is always on the filmstrip menu (and Cmd/Ctrl-BackSpace). For a reopened frame its target is the prescan entry that approved.json points at, i.e. real raw bytes in the real library. In the demo, whose window title and help say it writes only under demo/, one 'No' deletes a real entry and rewrites the real library index. In normal mode it deletes the reference entry that roll's approved.json names, which nothing re-creates.

**Evidence (from the code):**

```text
read_survey: `entry=Path(entries[number]) if number in entries else None`, where entries come from approved.json `reference_entry` (cwd-relative 'library/...' paths written by a real session). on_delete: `keep = messagebox.askyesnocancel("Delete", question + "\n\nKeep the library entry?", ...)`, then `if not keep: shutil.rmtree(entry); library.reindex(entry.parent)`. main(): 'It is safe because opening a roll only reads it ... nothing the window does afterwards can write back into the walk it is showing.' `make run-sheet` = `--demo --look-only --open-roll rolls/registration-D`.
```

**Failure scenario:** Trying out `make run-sheet`, the operator deletes a frame from the filmstrip to tidy up and answers 'No' to 'Keep the library entry?'. A real prescan entry, raw bytes included, is removed from library/ and library/index is rewritten.

**Fix:** Under demo (or whenever entry is outside session.root), never offer to delete the entry. In general, only offer entry deletion for passes filed in this session (entry under session.root and seq > 0), and say which roll and frame it belongs to.

<details><summary>Second reader's check</summary>

read_survey sets a reopened frame's entry from approved.json `reference_entry` (4742), a cwd-relative real-library path written by a real session. on_delete (3999-4015) offers rmtree plus library.reindex(entry.parent) for any result with an entry, with no check against session.root or demo. main()'s comment (8174-8180) claims the demo cannot write back into the real data. The cited tasks.py:184-185 is right, but DEMO_ROLL is rolls/aligned-strip (168); registration-D is only the docstring example. Severity lowered to medium because the dialog names the entry and warns that the raw bytes cannot be recovered. The harm is that it reaches the real library from the demo, and deletes a roll's reference entry.

</details>

<a id="gui-part2-gui2-12"></a>

### GUI2-12 -- Reopened frames are numbered seq=-frame, so two opened rolls (or one opened twice) collide: the wrong picture is shown, keyboard walking can raise, and Delete removes both

**Severity** medium · **Category** bug · **Verdict** partly · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:4737`, `tools/gui.py:2606-2607`, `tools/gui.py:3643-3664`, `tools/gui.py:4009-4023`, `tools/gui.py:762-773`, `rps7200/session.py:960-979`

Reopened results get seq=-frame and a label shared across rolls. After two rolls (or the same roll twice) are opened in one session, thumbnails, 'Show in preview' and the strip menu resolve to the first roll's frame with that number. The big view, full-res read and readouts then describe the wrong photograph, and Delete targets the first roll's result and entry while removing both from the session. Arrow-key walking onto the later duplicate hits a ValueError inside list.index, which _walk catches, so the view jumps to the first or last pass.

**Evidence (from the code):**

```text
`seq=-number,  # negative: never a live pass's seq`; open_roll appends `for result in out["results"]: self.results.append(result)` and never removes a previous roll's results. _show_seq/_at return the first `r.seq == seq`. on_delete: `self.results = [r for r in self.results if r.seq != result.seq]`. _walk: `at = shown.index(self.current)`, and Result is a plain @dataclass whose __eq__ compares the numpy `image` field when seq/kind/label match (label is 'frame N (reopened)' for every roll). _load_full: `if self._loading == r.seq or self._levels_seq == r.seq: return`; _loaded accepts any read whose seq matches.
```

**Failure scenario:** Operator opens roll 2026-09-14 from Rolls, then 2026-09-20. In the filmstrip he right-clicks 2026-09-20's frame 4 and chooses Delete, answering 'No' to keep. The prescan entry of 2026-09-14 frame 4 is deleted.

**Fix:** Give reopened results unique seqs (e.g. a decreasing counter), remove the previous roll's reopened results when another roll is opened, and give Result `eq=False` so identity is used.

<details><summary>Second reader's check</summary>

Confirmed: seq=-number (4737), open_roll appends without removing earlier reopened results (2606-2607), _show_seq/_at return the first seq match, and on_delete filters by seq, so it removes both. However, _walk wraps `shown.index(self.current)` in `except ValueError` (767-772). The ambiguous-truth-value ValueError from dataclass __eq__ comparing ndarray images is caught, so the arrow keys jump to the first/last pass instead of raising.

</details>

<a id="gui-part2-gui2-13"></a>

### GUI2-13 -- --look-only without --demo promises refusals the real scanner never makes, and offers an empty-transport calibration

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P28](../problems/P28-look-only-drives-real-scanner.md)

**Where:** `tools/gui.py:8145-8147`, `tools/gui.py:8187-8199`, `tools/gui.py:7266-7272`, `tools/gui.py:2692-2695`, `tools/gui.py:1946-1958`, `tools/gui.py:2026-2030`

**Doc claim:** CLAUDE.md:273-280 ('Calibrate with the film loaded ... Calibrating an empty transport ... preceded a wedge')

With the real DirectScanner, --look-only refuses nothing. Believing the sheet's sentence, an operator who presses Scan chosen frames gets the calibrate prompt (Return = measure) and then a real seek and scan. Calibrating an empty transport is the state CLAUDE.md says preceded a wedge. main() does not reject or warn about the combination.

**Evidence (from the code):**

```text
Help: `"there is no film in the transport. Every control still works; anything that reaches for film says so"`. The flag reaches a backend only inside `if args.demo:` (`DemoScanner(..., no_film=args.look_only, ...)`). Otherwise it only sets `self.look_only`, which changes wording: the sheet says 'Scanning is offered as it always is, and will say there is no film when it reaches for it.' on_scan_chosen still calls `_calibration_missing`, whose prompt defaults to 'Calibrate now' (`top.bind("<Return>", lambda _e: choose("measure"))`).
```

**Failure scenario:** `uv run python tools/gui.py --look-only --open-roll rolls/X` with the scanner attached and no film loaded. He presses Scan chosen frames to 'see what happens', hits Return on the calibrate prompt, and the scanner calibrates an empty transport.

**Fix:** In main(), refuse `--look-only` without `--demo` (ap.error), or make it wrap the real scanner in a no-film refusal at the same seam. The sheet wording should come from the backend's answer, not the flag.

<details><summary>Second reader's check</summary>

look_only reaches a backend only inside `if args.demo:` (8195-8199). Elsewhere it changes only the sheet header (7266-7272) and a dialog sentence (2695). on_scan_chosen still calls _calibration_missing, whose prompt binds Return to 'measure' (2026-2030). main() neither rejects nor warns about --look-only without --demo.

</details>

<a id="gui-part2-gui2-14"></a>

### GUI2-14 -- Opening a roll from the Rolls table is not guarded against a running job, so a walk in progress mixes with the reopened survey

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:7049-7054`, `tools/gui.py:2552-2626`, `tools/gui.py:2239-2247`, `tools/gui.py:3503-3505`, `tools/gui.py:3258`

If the table was opened before a walk started, it stays usable during the walk. Opening a roll then replaces the survey, edge reader and sheet key under a running walk, and the two strips' frames (same numbers, different film) end up in one survey and one sheet.

**Evidence (from the code):**

```text
on_reopen_survey refuses `if self.busy`, but _RollBrowser is non-modal and `_open` calls `self.gui.open_roll(summary["folder"])` with no busy check. open_roll sets `self.survey = out["results"]`, calls `edge_watch.load(...)` and `self._sheet_roll = folder`, while `_surveying` stays True, so arriving walk prescans are appended to the reopened survey (remember_arrangement) and `_sheet_roll` is overwritten with last_roll_dir at walk end.
```

**Failure scenario:** Rolls table left open, dry-run walk started, then a double-click on an old roll. The sheet that opens after the walk contains the old roll's frames plus the new walk's under colliding numbers, and its decisions are filed under the new walk's folder.

**Fix:** Guard open_roll itself (not only on_reopen_survey) with `if self.busy or self._surveying`, and disable the table's Open/Delete/Rename while busy.

<details><summary>Second reader's check</summary>

on_reopen_survey refuses while busy (2239-2244), but _RollBrowser is a separate non-modal Toplevel, and _open (7049-7053) calls gui.open_roll with no busy or _surveying check. _set_busy (3350-3365) toggles only _run_buttons. open_roll replaces the survey, the edge_watch contents and _sheet_roll while `_surveying` can still be True.

</details>

<a id="gui-part2-gui2-15"></a>

### GUI2-15 -- The Frame position window outlives its contact sheet; later edits go into a destroyed sheet, raise TclError and are lost

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:6548`, `tools/gui.py:7917-7932`, `tools/gui.py:8012-8013`, `tools/gui.py:2620-2624`, `tools/gui.py:6706-6723`, `tools/gui.py:7783-7797`, `tools/gui.py:6696-6698`

After the sheet is closed, the adjuster still redraws itself (its own _refresh runs first), so drags and arrow steps look accepted. They are written into the dead sheet's dicts, whose state was stored at dismiss time, and each raises a TclError to stderr. Nothing reaches settings or approved.json.

**Evidence (from the code):**

```text
_FrameAdjuster: `self.top = tk.Toplevel(gui.root)` (child of the root, not of the sheet). Only `_scan` closes it (`if self._adjuster is not None and self._adjuster.alive(): self._adjuster.top.destroy()`). `_dismiss` (Close, title-bar X, Escape) and open_roll's `self.sheet.top.destroy()` do not. _set then calls `self.sheet._refresh_caption(self.number)`, which does `caption.configure(...)` on a destroyed ttk.Label; `_tick_changed` calls `self.sheet._changed()`, which configures destroyed rings.
```

**Failure scenario:** Operator double-clicks frame 6, the adjuster opens, and he presses Close on the sheet behind it. He keeps positioning frames 6-9 in the adjuster, reopens the sheet, and none of those positions are there.

**Fix:** Destroy the adjuster in _dismiss and before open_roll destroys a sheet, or parent it on the sheet's Toplevel, and have the adjuster check `self.sheet.alive()` before writing.

<details><summary>Second reader's check</summary>

_FrameAdjuster.top = tk.Toplevel(gui.root) (6548). Only the sheet's _scan destroys it (8012-8013); _dismiss (7917-7932) and open_roll's `self.sheet.top.destroy()` do not. _set writes into sheet.offsets/proposals and then calls sheet._refresh_caption on destroyed widgets.

</details>

<a id="gui-part2-gui2-16"></a>

### GUI2-16 -- Decisions in an open sheet are lost when the main window quits or another roll is opened

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:3039-3050`, `tools/gui.py:3070-3086`, `tools/gui.py:2620-2624`, `tools/gui.py:7917-7927`, `tools/gui.py:2280-2289`

_dismiss's docstring says every way out goes through it. Quitting the app (or a crash) with the sheet open is another way out and does not. For a walk not yet commissioned, the sheet's state is the only record of ticks, hand positions and turns. open_roll's stated reason for not storing is wrong: storing at 2624 would still be keyed to the outgoing roll.

**Evidence (from the code):**

```text
State is stored only in `_ContactSheet._dismiss` (`self.gui._store_sheet_state(self.state())`). on_close calls `self._remember()`, which writes `"sheet": self.remembered.get("sheet") or {}` (only states from earlier dismisses), then `_quit` destroys the root. open_roll: `self.sheet.top.destroy()` with the comment 'Destroyed rather than dismissed: `_loaded_roll` already names the roll being opened'. `_sheet_key` actually uses `self._sheet_roll`, which still names the outgoing roll at that point (it is reassigned at 2626).
```

**Failure scenario:** Operator spends 20 minutes positioning 17 frames, quits the app from the main window with the sheet open, and reopens the next day: the positions are gone.

**Fix:** In on_close (before _remember) and in open_roll (before destroying), call `self.sheet._dismiss()` when the sheet is alive.

<details><summary>Second reader's check</summary>

on_close (3039-3050) calls _remember and shutdown and never _dismisses an open sheet. open_roll destroys the sheet without storing it (2620-2624). At that point `_sheet_roll` still names the outgoing roll, because it is reassigned at 2626, so the comment's rationale does not hold.

</details>

<a id="gui-part2-gui2-17"></a>

### GUI2-17 -- Quitting while Save all or Export is writing kills the daemon writer mid-file with no warning

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `tools/gui.py:3039-3050`, `tools/gui.py:3052-3068`, `tools/gui.py:3840-3841`, `tools/gui.py:2451`

A batch of 38 full-resolution re-corrections takes minutes. Closing the window during it ends the process and kills the daemon thread inside export.write/tiff.write, leaving a truncated TIFF/JPEG/DNG in the chosen folder that looks like a delivered file.

**Evidence (from the code):**

```text
on_close only checks the scanner: `if self.busy and not messagebox.askokcancel(...)`. _wait_to_quit waits only for `self.session._thread`. The batch writers are `threading.Thread(target=run, daemon=True, name="save-all")` / `name="export-rolls"`, and `self._saving` is never consulted.
```

**Failure scenario:** Operator starts Export of a 38-frame roll, sees files appearing, closes the app. frame17's TIFF is half-written and frames 18-38 are missing. The final 'saved N of M' never appears.

**Fix:** In on_close, if `self._saving`, ask, then wait for the save thread (make it non-daemon or join it in _wait_to_quit). Write each file to a temporary name and rename it.

<details><summary>Second reader's check</summary>

on_close checks only self.busy. _wait_to_quit waits only for session._thread. The save-all and export threads are daemon threads (3840-3841, 2451), and _saving is never consulted on quit. export.write/tiff.write write directly to the final path.

</details>

<a id="gui-part2-gui2-18"></a>

### GUI2-18 -- Heavy full-resolution work runs in the GUI process with the device claimed: no busy guard, and unbounded full-res loads on every show or rotate

**Severity** medium · **Category** hardware-safety · **Verdict** partly · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `tools/gui.py:3527-3539`, `tools/gui.py:4100-4120`, `tools/gui.py:3911-3935`, `tools/gui.py:3770-3841`, `tools/gui.py:2383-2451`, `tools/gui.py:2453-2482`

**Doc claim:** CLAUDE.md:368-369 ('Do not hold the session open through heavy local work')

Full-resolution reads are started on every show, rotate or flip with no bound or cancellation, and Save all/Export may run during a roll. All of this runs in the process that holds the USB device open. Duplicate is refused while busy, but its multi-GB copytree runs on the UI thread.

**Evidence (from the code):**

```text
The session opens the device at start and holds it for the window's life (`self._scanner.open()` in _run; closed only at shutdown). _show calls `self._load_full(result)` for any pass with an entry; its only guard is `if self._loading == r.seq or self._levels_seq == r.seq: return`, `_show` resets `_levels_seq = None`, and `_loaded` sets `self._loading = None` for any finished read. _carry (every rotate/flip) calls `self._show(result)`, so each turn re-reads and re-corrects the whole entry. on_save_all/on_export_rolls check only `self._saving`, not `self.busy`. on_duplicate_roll runs `shutil.copytree` on the UI thread.
```

**Failure scenario:** During a 7200 dpi roll, the operator arrow-keys through ten filed frames to check them. Ten concurrent full-res corrections exhaust memory, the OS kills the process mid-read, and the scanner needs a power cycle.

**Fix:** Keep one full-res read in flight and cancel or ignore superseded ones (a single worker with a 'latest wanted' slot). Do not reload on rotate (orientation is applied at sampling time anyway). Refuse Save all, Export and Duplicate while `self.busy`, or queue them until idle. Consider running them in a subprocess.

<details><summary>Second reader's check</summary>

Confirmed: _show calls _load_full for any result with an entry (3538-3539), resets _levels_seq, and _load_full guards only `_loading == r.seq`, so each show or rotate (via _carry -> _show) starts a new, uncancelled full-res read-and-correct thread. on_save_all and on_export_rolls check only _saving, not busy, so they can run during a roll with the device open. However, on_duplicate_roll is guarded: _roll_is_busy refuses while self.busy (2372-2375). Its copytree only freezes the UI; it does not overlap a scan. The OOM-to-abandoned-read chain is plausible but unmeasured.

</details>

<a id="gui-part2-gui2-19"></a>

### GUI2-19 -- Aim-click moves film with no busy guard and no check that the prescan clicked shows where the film is now

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:4500-4504`, `tools/gui.py:4531-4587`, `tools/gui.py:2972-3018`, `tools/gui.py:3346-3348`

With 'aim' ticked, a click on any prescan in the history computes a distance from that picture and nudges the film wherever it is now. That could be days-old reopened prescans, or a prescan of frame 3 while the film is on frame 7. During a roll the Move is queued and runs when the roll ends, moving the film after the operator may have changed the strip.

**Evidence (from the code):**

```text
on_press: `if self.v_aim.get() and self.current is not None and self.current.kind == "prescan": self._aim(event)`. _aim ends with `self.on_nudge(1 if want > 0 else -1, millimetres=abs(want))`, and on_nudge ends `self.session.submit(Move(millimetres=millimetres * direction))`. Neither checks `self.busy`, while the fine-nudge buttons are in `_run_buttons()` and disabled while busy. Any prescan qualifies, including reopened ones and those of other frames/positions.
```

**Failure scenario:** During a long roll the operator clicks a prescan in the big view (aim still ticked from earlier) and confirms. When the roll finishes hours later, the film moves by that amount.

**Fix:** Refuse aim while busy. Only allow it on the most recent prescan whose `position` equals the current transport position (`self._transport`) and which was taken since the last Move.

<details><summary>Second reader's check</summary>

on_press (4500-4504) calls _aim for any current prescan when v_aim is set. _aim ends in on_nudge, and on_nudge (2972-3018) submits a Move with no busy check and no check that the prescan shows the current transport position. Reopened prescans (kind 'prescan') qualify.

</details>

<a id="gui-part2-gui2-20"></a>

### GUI2-20 -- Reopening a roll that has no walk restores the original frame count and moves start-at, so the Roll button rescans done frames or runs past the roll

**Severity** medium · **Category** bug · **Verdict** partly · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:2576`, `tools/gui.py:2581-2598`, `tools/gui.py:4780-4792`, `tools/gui.py:2150-2159`

Reopening a roll with no walk restores its original frame count and moves start-at to the first remaining frame. The Roll button then scans that count from there, and neither 'only' nor the done set is carried. Done frames between and after the gaps are rescanned, and the span can extend past the roll's own frames. The reopen dialog tells the operator simply to press Roll.

**Evidence (from the code):**

```text
RESTORABLE includes `"frames": ("frames", str)` and `"start_at": ("startat", str)`, and open_roll then sets `self.v_startat.set(str(remaining[0]))`. The dialog says '{len(done)} frames are done and {len(remaining)} are left ... press Roll'. on_roll submits `Roll(frames=frames or None, start_at=start_at, ...)` with no `only`.
```

**Failure scenario:** A 24-frame roll died at 10; frame 14 errored. Reopen, press Roll: frames 11-34 (to the strip's end) are scanned, including done 12-13 and 15-24 again.

**Fix:** Restore `frames = max(remaining) - remaining[0] + 1` and pass `only=tuple(remaining)`, or make the no-walk resume path submit the Roll itself with only=remaining.

<details><summary>Second reader's check</summary>

RESTORABLE (4780-4792) restores 'frames' and 'start_at'. open_roll's no-walk branch then sets start-at to remaining[0] (2588-2589) without adjusting frames or restoring `only`, and on_roll submits Roll(frames=..., start_at=...) with no only. So done frames are rescanned and the span can run past the roll. However, on_roll's own confirm dialog does print 'Scan N frames, from frame K', so the claim that the cost is unpredicted is overstated. It is the reopen dialog ('press Roll') that misleads.

</details>

<a id="gui-part2-gui2-22"></a>

### GUI2-22 -- The sheet's 'nudge registration between frames' tick has no effect, and its header describes a rewind and a partial nudge that do not happen

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:7274-7280`, `tools/gui.py:7382-7390`, `tools/gui.py:2877-2879`, `tools/gui.py:2921-2926`, `tools/gui.py:2814`, `tools/gui.py:2853-2858`, `rps7200/direct.py:3526-3567`

**Doc claim:** tools/gui.py:7274-7280 (contact-sheet header text)

The control still works in the UI and `_options_note` lists changes to it as 'the automatic nudge', but it never influences a commissioned roll. The text tells the operator it applies to unadjusted frames and that the strip is rewound first; neither is what the code does.

**Evidence (from the code):**

```text
Header: 'the automatic nudge does not apply to frames you adjust. The film is rewound to the start of the strip first'. Code: every ticked frame gets an Approved (approved_from_sheet), and direct.py takes `held = approved.get(index)` ... `elif correct or correct_dry_run:` only when there is no approval. The GUI's own comment: 'so `correct` cannot act on a single frame of a commissioned roll'. The roll seeks to `start_at, span = chosen_span(numbers)`, the first ticked frame, not the start of the strip.
```

**Failure scenario:** The operator ticks 'nudge registration between frames' so that frames he did not position get automatic aiming, and they are held at the detector's or as-walked positions instead.

**Fix:** Remove the tick from the sheet (TODO.md already says so) and correct the header: 'the film is wound to the first ticked frame', 'every ticked frame is held to the position shown'.

<details><summary>Second reader's check</summary>

The sheet still offers 'nudge registration between frames' (7382-7390). approved_from_sheet gives every ticked frame an Approved. direct.py scan_roll takes the `held` branch, or `elif held is not None` once holding is off, before `elif correct` (3526-3570), so correct never acts. The GUI's own comment (2921-2926) admits this. The roll seeks to chosen_span's first ticked frame (5872-5873), not the start of the strip, which contradicts the header text (7274-7280).

</details>

<a id="gui-part2-gui2-23"></a>

### GUI2-23 -- Per-frame turns from a commissioned roll persist in the window and are applied to the next plain roll's frames of the same number, on screen and in Save As but not in the delivered files

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P25](../problems/P25-reopened-frames.md)

**Where:** `tools/gui.py:2845-2847`, `tools/gui.py:2112-2124`, `tools/gui.py:3477-3482`, `rps7200/session.py:1931-1935`, `rps7200/session.py:2025-2029`

After a sheet roll with frame 3 turned 90 degrees, a later Scan roll (not a dry run) on a different strip shows its frame 3 turned 90 degrees. Save As writes it turned, while frame03.tif in the roll folder uses the session default. The window and the files disagree about the same pass.

**Evidence (from the code):**

```text
on_scan_chosen: `self.orientations[("frame", record.number)] = (record.rotation, bool(record.flipped))`. on_roll clears `self.orientations = {}` only `if dry:`. _arrange: `known = self.orientations.get(picture_of(result))` with `picture_of` = ('frame', number). The session resets `_frame_rotation = {}` after each roll, so the next roll files with `self.rotation, self.flip`.
```

**Failure scenario:** Roll A: frame 3 portrait, turned. Roll B loaded and scanned via Scan roll: B's frame 3 appears sideways in the preview and Save As, but frame03.tif is landscape.

**Fix:** Clear the ('frame', n) orientations at every roll submission (dry or not), or key them by roll folder and frame.

<details><summary>Second reader's check</summary>

on_scan_chosen stores orientations[('frame', n)] (2845-2847), and on_roll clears orientations only for dry runs. _arrange reads orientations.get(picture_of(result)), and picture_of keys by ('frame', number) (5432-5433). The session resets _frame_rotation after the roll (1939), so the next roll's files use the session default.

</details>

<a id="gui-part2-gui2-24"></a>

### GUI2-24 -- Roll export and Save all apply the window's current mono setting, not each roll's or pass's own

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:2426`, `tools/gui.py:3817`, `tools/gui.py:3866-3867`

Exporting a B&W roll while the window is set to colour negative writes three-channel files, and exporting a colour roll with the window on B&W writes one channel. Either way the export differs from what the roll delivered, and nothing says so.

**Evidence (from the code):**

```text
on_export_rolls: `mono, channel = self.v_mono.get(), self.v_mono_channel.get()`; on_save_all the same; _deliver_one: `if mono: full = to_monochrome(full, mono_channel)`. The roll's manifest records `"mono": job.mono, "mono_channel": job.mono_channel` in its settings.
```

**Failure scenario:** The window is set to colour for today's film and yesterday's B&W roll is exported: the files come out RGB.

**Fix:** Take mono/mono_channel from summary['settings'] (with the film type) for roll exports, and from each result's meta/film for Save all.

<details><summary>Second reader's check</summary>

on_export_rolls (2426) and on_save_all (3817) read v_mono/v_mono_channel from the window. _deliver_one applies to_monochrome from those values (3866-3867), not from the manifest's settings or the pass's film.

</details>

<a id="gui-part2-gui2-25"></a>

### GUI2-25 -- Frames the detector has not reached yet are recorded in approved.json with source 'operator'

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:5835`, `tools/gui.py:5811-5816`, `tools/gui.py:2953`

Commissioning while the edge reader is still running (the dialog allows it) stamps untouched frames as operator decisions. The driver log and approved.json then attribute 'as walked, 0' to the operator, the provenance confusion the `source` field was added to prevent.

**Evidence (from the code):**

```text
approved_from_sheet: `source=(labels.get(number) or {}).get("source") or "operator"` for frames with no note. _write_approved: `"source": str(a.source or "operator")`.
```

**Failure scenario:** Commission at 'frame edges 9/17'. approved.json says frames 10-17 were positioned by the operator at 0.

**Fix:** Use a distinct source such as 'unread' or 'as-walked' when there is no note, and count it separately in _approved_note.

<details><summary>Second reader's check</summary>

approved_from_sheet: `source=(labels.get(number) or {}).get("source") or "operator"` (5835), and _write_approved defaults to 'operator' too (2953). A frame the edge reader has not reached has no note, so it is stamped as the operator's.

</details>

<a id="gui-part2-gui2-26"></a>

### GUI2-26 -- 'Nothing already there is overwritten' is false for the DNG sidecar and the no-Pillow JPEG fallback

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/export.py:146-149`, `rps7200/export.py:185-188`, `tools/gui.py:3809-3810`, `tools/gui.py:3826`, `tools/gui.py:2420-2421`, `tools/gui.py:2436-2438`

**Doc claim:** tools/gui.py:3809-3810 and 2420-2421 (dialog text)

An RGBI JPEG export writes `<stem>.dng` over any existing file of that name. Without Pillow, a JPEG export writes `<stem>.tif` over an earlier TIFF export in the same folder. Save As's OS dialog confirms the .jpg overwrite only, not the sidecar.

**Evidence (from the code):**

```text
Save all/Export call `_unclaimed(out / batch_name(result, fmt))` on the .jpg name only. export._write_infrared then does `companion = infrared_path(path); dng.write(companion, ...)` and the fallback does `path = path.with_suffix(SUFFIXES["tiff"]); tiff.write(str(path), ...)`, neither checking existence.
```

**Failure scenario:** Export a roll as TIFF into ~/out, then again as JPEG on a machine without Pillow: every TIFF from the first export is overwritten.

**Fix:** Run the companion and fallback paths through _unclaimed as well, or refuse when they exist.

<details><summary>Second reader's check</summary>

export.write's ImportError fallback (185-188) rewrites the path as .tif and writes it unchecked. _write_infrared (146-149) writes <stem>.dng unchecked. Only the primary .jpg name passes through _unclaimed (3826, 2436-2438).

</details>

<a id="gui-part2-gui2-27"></a>

### GUI2-27 -- Constants retyped instead of taken from the driver, and an aim message that states the wrong limit

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:7553-7554`, `tools/gui.py:215`, `tools/gui.py:4563-4570`, `tools/gui.py:4581-4582`, `tools/gui.py:6779-6780`

**Doc claim:** CLAUDE.md:195-198 ('A number, cap, constant or decision ... is taken from DirectScanner, never retyped')

These are second homes for driver numbers, the drift pattern CLAUDE.md forbids, and the aim dialog misstates the rule it is enforcing (one command, not eight).

**Evidence (from the code):**

```text
`short = (result.registration or {}).get("shortfall_mm") or 0.0; if short > 0.85:` ('The same 0.85 mm the driver calls drift') while the driver uses `drift_warning: int = 240` (direct.py:3302). `MAX_FINE_STEPS = 8` is retyped from session.MAX_FINE_STEPS. `seconds = moves * 1.1` and `{steps * 1.1:.0f} s` appear twice. _aim refuses at `abs(want) > MAX_TRAVEL_MM` (= one command, line 230) but says 'which would take more than {MAX_FINE_STEPS} sub-frame moves'.
```

**Failure scenario:** drift_warning is retuned in direct.py; the sheet's 'drifted' badge keeps using 0.85 mm and disagrees with the roll log.

**Fix:** Import the drift threshold and MAX_FINE_STEPS (or expose them from session), take move time from the planner, and reword the aim message to 'further than one sub-frame command reaches'.

<details><summary>Second reader's check</summary>

gui.py:215 `MAX_FINE_STEPS = 8` duplicates session.py:669. `if short > 0.85` (7554) is a retyped threshold, while the driver uses `drift_warning: int = 240` (direct.py:3302). `* 1.1` appears at 4582 and 6780. _aim refuses at `abs(want) > MAX_TRAVEL_MM`, which is one command (230), but the message says 'more than {MAX_FINE_STEPS} sub-frame moves' (4563-4570).

</details>

<a id="gui-part2-gui2-28"></a>

### GUI2-28 -- Re-arranging an old or reopened pass overwrites the 'last said' transport position used by confirm dialogs

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:3506-3509`, `tools/gui.py:2094`, `tools/gui.py:2817`

Rotating frame 2 of a reopened walk makes the Roll and Scan chosen dialogs claim the transport last reported frame 2 and forecast the wind from there. The transport never said so.

**Evidence (from the code):**

```text
remember_arrangement: `if plausible(result.position): self._transport = result.position`. It is called from _carry and the sheet's _orient for any result, not only new ones. seek_note(self._transport, start_at) builds the 'The transport last said the film is on frame N' sentence and its time cost.
```

**Failure scenario:** After rotating a reopened frame, the confirm dialog forecasts 'winds back 5 frames' when the film is actually at the start.

**Fix:** Update `_transport` only from 'transport' events and from newly arrived results, not from arrangement changes.

<details><summary>Second reader's check</summary>

remember_arrangement sets `self._transport = result.position` for any plausible position (3506-3509). It is reached from _carry and the sheet's orient handler for old or reopened results, and seek_note(self._transport, ...) builds the dialogs' 'last said' sentence (2094, 2817).

</details>

<a id="gui-part2-gui2-29"></a>

### GUI2-29 -- The shortcut editor says no shortcut starts a scan, but default keys start a prescan, a scan and a roll

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:6354-6356`, `tools/gui.py:709-711`, `tools/gui.py:734-740`, `rps7200/shortcuts.py:112-117`

These keys ask first, but the text an operator reads while choosing keys says the opposite, and a modified key fires even while typing (_runner).

**Evidence (from the code):**

```text
Editor text: 'No shortcut starts a scan, calibrates, or moves film.' _actions docstring: 'Nothing starts a scan, moves film or calibrates', then maps `"prescan": lambda: self._confirm_then(...)`, `"scan": ...`, `"roll": self.on_roll`. The shortcuts file binds Prescan to <ACCEL-Return> and Roll to <ACCEL-Key-b>.
```

**Failure scenario:** The operator binds Cmd-Return to something else, believing it harmless, and later hits the default elsewhere.

**Fix:** Say 'Keys that start a prescan, scan or roll always ask first; none calibrates or moves film'.

<details><summary>Second reader's check</summary>

The editor text (6354-6356) and the _actions docstring (709-711) say no shortcut starts a scan. _actions maps prescan, scan and roll (734-740), and shortcuts.py binds <ACCEL-Return>, <ACCEL-Shift-Return> and <ACCEL-Key-b> (112-117). They do ask first.

</details>

<a id="gui-part2-gui2-30"></a>

### GUI2-30 -- The Delete-roll dialog and README promise the roll can be rebuilt from the library; nothing rebuilds a roll folder

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:2487-2490`, `tools/gui.py:2502-2507`, `tools/gui.py:2374-2380`

**Doc claim:** README.md:283-285 ('Delete removes the roll folder and never touches `library/`: the raw bytes stay and the frames can be rebuilt')

survey.json/roll.json hold transport positions, registration and hold outcomes, and `wanted`/`done`. None of that is in any library entry, and without the folder the Rolls table cannot list or export the roll at all. A walk made in this session can be deleted while its sheet is open, after which the commission recreates a folder holding only approved.json.

**Evidence (from the code):**

```text
Docstring: 'Everything in a roll folder is re-derivable from the library except approved.json'. Dialog: 'the raw bytes stay, and the frames can be rebuilt from them'. No tool recreates survey.json, roll.json or prescanNN.tif (tools/library.py offers list/verify/reconstruct/reindex/duplicates/migrate-*). _roll_is_busy's 'open' check covers only `_loaded_roll`, not a walk made in this session (`_sheet_roll`).
```

**Failure scenario:** The operator deletes an unfinished roll trusting the dialog. It can no longer be resumed or exported from the window, and its per-frame hold records are gone.

**Fix:** Say what is actually lost (the walk, the resume state and hold records), and include `_sheet_roll` in _roll_is_busy.

<details><summary>Second reader's check</summary>

tools/library.py offers only list/verify/reconstruct/reindex/... (45). Nothing rebuilds survey.json, roll.json or prescanNN.tif. The Delete dialog (2502-2507) and README.md:283-285 say the frames can be rebuilt. _roll_is_busy checks only `_loaded_roll` (2376-2381), not `_sheet_roll`.

</details>

<a id="gui-part2-gui2-31"></a>

### GUI2-31 -- Renaming or duplicating a roll leaves window state keyed by the old folder; duplicates share one library key; copytree runs on the UI thread

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:2518-2537`, `tools/gui.py:2453-2482`, `tools/gui.py:2280-2289`, `tools/gui.py:2272-2275`, `tools/gui.py:5127`

After a rename the stored sheet decisions and 'Last opened' are orphaned. A duplicate and its original resolve to the same library entries, so after rescanning the original (the dialog says it is 'safe to rescan over'), exporting the duplicate may export the new scans. A failed copy leaves a partial folder that lists as a roll, and a multi-GB copy freezes the window.

**Evidence (from the code):**

```text
on_rename_roll: `source.rename(target)` only. Sheet state is stored under `Path(self._sheet_roll).name` and 'opened' under `Path(folder).name` in gui-settings. on_duplicate_roll: `shutil.copytree(source, target)` synchronously, with no cleanup on OSError. rolls_on_disk: `summary["entries"] = dict(index.get(summary["roll"], {}))` keyed by the manifest roll name, which the copy keeps.
```

**Failure scenario:** Duplicate 2026-09-14 and rescan the original. Exporting 2026-09-14-2 (meant to preserve the first scan) writes the rescans.

**Fix:** Rewrite the manifests' roll name in the copy (or key entries by folder), move gui-settings keys on rename, copy into a temporary name on a thread and rename it on success.

<details><summary>Second reader's check</summary>

on_rename_roll only renames (2533). Sheet state and 'opened' are keyed by folder name. on_duplicate_roll runs copytree synchronously with no cleanup on failure (2477-2481). rolls_on_disk joins entries by summary['roll'] from the manifest (5127, 5086), which a copy keeps.

</details>

<a id="gui-part2-gui2-32"></a>

### GUI2-32 -- Save As on a pass whose filing is pending or failed writes the reduced working copy (as small as 512 px) under the user's chosen name

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:3876-3885`, `tools/gui.py:3515-3520`, `rps7200/session.py:1348-1356`

The only notice is one log line, and its wording ('not filed yet') is wrong when filing failed or the entry was deleted. Save as ... then produces a 512-px or 1400-px file the operator believes is the scan.

**Evidence (from the code):**

```text
_deliver_one: `if result.image is not None: ... export.write(path, ...)` returns '... reduced preview, the full-resolution file is not filed yet'. Old results are shrunk in place: `old.image = preview.downscale(old.image, ARCHIVE_MAX_SIDE)` (512). If filing failed, `_filed` logs 'could not be filed' and entry stays None forever.
```

**Failure scenario:** The library disk is full, so a scan's entry fails. Minutes later the operator uses Save As and gets a 1400-px TIFF named like a full scan.

**Fix:** For a single Save As, refuse or ask ('only a reduced preview is available') instead of writing silently, and distinguish 'pending' from 'failed' or 'deleted'.

<details><summary>Second reader's check</summary>

_deliver_one falls back to result.image (3876-3885) when there is no entry, with the message 'not filed yet', whatever the reason. session._filed logs a failure and never emits 'filed' (1353-1355), so entry stays None. remember_arrangement downscales old results to ARCHIVE_MAX_SIDE (3515-3520).

</details>

<a id="gui-part2-gui2-a2"></a>

### GUI2-A2 -- Save As, Save all and roll Export write files without the scan resolution, so TIFFs are tagged 72 dpi

**Severity** low · **Category** bug · **Verdict** found-by-verifier

**Where:** `tools/gui.py:3869`, `tools/gui.py:3880-3883`, `rps7200/tiff.py:216`, `rps7200/session.py:1080-1082`

The same pass delivered by the roll (frameNN.tif) and by Export or Save As carries different resolution metadata: the scanned dpi versus 72. Downstream tools that size or print by the tag get the wrong physical size.

**Evidence (from the code):**

```text
_deliver_one: `note = export.write(path, full, quality=quality)` and `export.write(path, to_monochrome(...) if mono else turned, quality=quality)`, with no `resolution=`. tiff.py: `res = int(resolution) if resolution else 72`. FrameWriter passes `resolution=job["dpi"]`.
```

**Failure scenario:** A 3600 dpi frame exported from the Rolls table opens in NegPy or an editor as a 72 dpi image roughly 50 times its physical size, while frame05.tif from the roll says 3600.

**Fix:** Pass `resolution=(result.meta or {}).get("resolution_dpi")`, or better the entry record's scan.resolution_dpi, in both _deliver_one branches.

<a id="gui-part2-gui2-a3"></a>

### GUI2-A3 -- Save As re-corrects the full-resolution entry on the UI thread with no error handling

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `tools/gui.py:3754-3768`, `tools/gui.py:3858-3869`

on_save_all's own docstring says re-correcting at full resolution on the UI thread would freeze the window. Save As does exactly that for one pass (142 to 570 MB at 3600/7200 dpi). Any OSError, MemoryError or a corrupt entry propagates out of the Tk callback, so only a stderr traceback appears. The operator sees neither the 'saved' line nor a failure.

**Evidence (from the code):**

```text
on_save_as: `said = self._deliver_one(result, path, jpeg_quality(...), self.v_mono.get(), self.v_mono_channel.get())` is called directly, with no thread and no try/except. _deliver_one calls `library.corrected(result.entry)` and export.write.
```

**Failure scenario:** Save As to a full disk: the window freezes for seconds, then nothing is said. A partial file may remain under the chosen name.

**Fix:** Run it on the same worker/queue path as Save all (the _saves queue), and catch and report failures in the log or a dialog.

<a id="gui-part2-gui2-a4"></a>

### GUI2-A4 -- 'Scan chosen frames' closes the sheet and adjuster before any refusal or cancel, and the 'over the sheet' prompt parent is dead code

**Severity** low · **Category** user-error · **Verdict** found-by-verifier

**Where:** `tools/gui.py:8005-8015`, `tools/gui.py:2753-2771`

Every refusal or cancel leaves the operator with the sheet gone and must reopen it. The state is stored by _dismiss. The code that parents the calibrate prompt over the sheet can never see a live sheet, so its comment 'Over the sheet when the sheet asked' is false.

**Evidence (from the code):**

```text
_ContactSheet._scan: `self._dismiss()` then `self.gui.on_scan_chosen(picked, approved, options)`. on_scan_chosen then refuses when busy, refuses on calibration missing (`self._calibration_missing(self.sheet.top if self.sheet is not None and self.sheet.alive() else None)`), refuses on invalid dpi, or is cancelled at the confirm dialog.
```

**Failure scenario:** The operator presses Scan chosen frames, reads the time estimate and presses Cancel. The sheet and his adjuster window have closed.

**Fix:** Validate and confirm in on_scan_chosen first, and dismiss the sheet only once the Roll is actually submitted.

<a id="gui-part2-gui2-a5"></a>

### GUI2-A5 -- approved.json, the Move job and the window's internals still hold transport distances in millimetres

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `tools/gui.py:2949`, `tools/gui.py:2977-2979`, `tools/gui.py:3018`, `tools/gui.py:4963-4964`

**Doc claim:** CLAUDE.md:292-300 ('Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter')

CLAUDE.md prohibits millimetres for transport distances. The operator-facing text uses units, but the persisted decisions file and the session interface store mm, which is a conversion away from what the hardware executed. Any later change to MM_PER_UNIT silently reinterprets every stored approved.json.

**Evidence (from the code):**

```text
_write_approved: `"offset_mm": round(a.offset_mm, 4)`. on_nudge: 'The field is in the transport's own unit. Everything below this line, and the whole session interface, stays in millimetres.' `millimetres = abs(values[0]) * MM_PER_UNIT`, then `Move(millimetres=millimetres * direction)`.
```

**Failure scenario:** The unit calibration is revised (as happened with 1.57 -> 1.84). Stored approved.json offsets in mm now map to different param values than the operator chose.

**Fix:** Store offsets in parameter units (or param commands) in approved.json, together with the conversion used, and move the Move interface to units.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| approved.json: the operator's per-frame decisions for a commissioned roll | <session.rolls>/<_safe(Film panel 'roll' field) or 'roll'>/approved.json. This is not necessarily the roll's own folder <rolls>/<name or YYYY-MM-DD>/ (GUI2-03). | JSON {roll: str, numbering: NUMBERING, frames: [{number: int, offset_mm: float (mm, rounded to 4 dp), rotation: int 0/90/180/270, flipped: bool, reference_entry: str (cwd-relative library path or ''), source: 'operator'\|'measured'\|'unconfirmed'\|'neighbours'\|'none'}]}, indent 2 | not pixels (decisions). The reference picture itself (Approved.reference) is passed by value to the Roll and not persisted here. | ScannerGui._write_approved (tools/gui.py:2929-2967), called from on_scan_chosen (2831), which is called from _ContactSheet._scan (8005-8015) | read_approved (tools/gui.py:4924-4976), via read_survey (4727), roll_exports (4886) and on_delete_rolls (2497) | No. The whole file is replaced with only the currently ticked frames, written non-atomically with write_text, and offsets are rounded to 1e-4 mm. Explicit zero offsets are dropped on read (`if record.get("offset_mm")`), and a parse error reads silently as empty. |
| Contact-sheet state and roll 'last opened' times in the window settings | gui-settings.json (settings.DEFAULT_PATH, $RPS7200_SETTINGS, --settings, or demo/gui-settings.json under --demo), keys 'sheet' {<roll folder name>: state} and 'rolls' {<roll folder name>: {opened: epoch}} | JSON. The sheet state is {ticks: {n: bool}, offsets: {n: float mm}, rotations: {n: int}, flips: {n: bool}, sources: {n: str}, options: {dpi, predpi, film, meter, ir, fast_ir, correct}}. Integer keys become strings on round trip and are re-cast by _clean_sheet_state (2291-2338). | not pixels | _ContactSheet._dismiss -> ScannerGui._store_sheet_state (2340-2350); _note_roll_opened (2265-2276); both via _remember -> settings.save (atomic temp + replace) | _recall_sheet_state (2352-2365) for 'Contact sheet ...'; _RollBrowser/_reload and on_reopen_survey for 'opened'. open_roll ignores the stored sheet state and uses approved.json. | Keyed by folder name, so a rename orphans it. Explicit zero offsets are not stored as offsets. Only stored on _dismiss, so it is lost on quit or open_roll while the sheet is open. Can be written under the wrong walk's key (GUI2-07). |
| Save As / Save all / roll Export deliverables | User-chosen file (Save As); <chosen folder>/<batch_name> made free by _unclaimed (Save all); <chosen folder>/<_safe(roll)>_<batch_name> (Export). Plus <stem>.dng beside a 4-channel JPEG. | TIFF (tiff.write, 16-bit for scans / 8-bit for prescans, RGBI as a 4th sample) or JPEG 8-bit (quality 60-100) plus DNG (RGB+IR); one channel when the window's mono is on | Corrected: library.corrected(entry) with today's code, oriented by result.rotation/flipped (roll export: approved.json/settings). Scan-button entries are doubly corrected (GUI2-01). The fallback is the reduced corrected working copy (<=1400 px, or 512 px once archived) when no entry exists. | ScannerGui._deliver_one (3843-3886) from on_save_as (UI thread), on_save_all and on_export_rolls (daemon threads) | nothing in the app (NegPy downstream) | No, derived by design. It is not atomic, is truncated if the app quits mid-batch, and the DNG sidecar and no-Pillow TIFF fallback can overwrite existing files. |
| Walked roll folder read back to rebuild a contact sheet | <rolls>/<folder>/survey.json, roll.json, prescanNN.tif | JSON manifests (settings, frames[number, transport_position, registration, prescan, done], rotation/flipped captured at roll start); prescanNN.tif is 8-bit RGB TIFF | prescanNN.tif is corrected and arranged by the session rotation at filing time; read_survey un-orients it by the manifest's single start-time rotation | rps7200/session.py ScanSession._roll and FrameWriter (not this area) | read_survey (4653-4773), roll_summary (5033-5101), manifest_settings (4619-4650), walked_prescans | prescanNN.tif orientation can disagree with survey.json after a mid-walk rotate (GUI2-09). A same-day second walk with an empty name overwrites both (GUI2-21). |
| Library entries read for full-resolution view, histogram, pixel readout and exports | <library>/<YYYYMMDDTHHMMSSZ_stock[_fFRAME]_NNNdpi[_ir]>/scan.json, scan.tif, shading.npz, ccd_mask.bin (raw.bin.gz not read here) | scan.tif uint8/uint16 TIFF; shading.npz; ccd_mask.bin bytes; scan.json record (film.frame is the roll join key) | Stored scan.tif should be raw. The GUI always reads it through library.corrected (re-applied shading). For Scan-button passes and all demo passes the stored pixels are already corrected, so the reads double-correct. | FrameWriter/library.save (session) | _load_full (4104-4120), _deliver_one (3864), roll_entry_index (4979-5009) which reads only scan.json | roll_entry_index keys walk prescans and roll frames identically, so the chosen entry is arbitrary (GUI2-05). |
| Deletion and mutation of library entries and roll folders from the window | <library>/<entry> (rmtree) and <library>/index; <rolls>/<folder> (rmtree, rename, copytree to <folder>-N) | filesystem operations | destroys raw bytes (entry delete) | on_delete (3999-4032: shutil.rmtree(entry) and library.reindex(entry.parent)); on_delete_rolls (2511-2516); on_rename_roll (2533); on_duplicate_roll (2478) | n/a | Entry delete follows reopened reference_entry paths into the real library, even under --demo (GUI2-11). copytree is not atomic. |

**Second reader's corrections to this table:**

approved.json: offset_mm is first passed through snap_offset, which snaps to the reachable lattice and clamps to ±MAX_TRAVEL_MM, and only then rounded to 4 dp. It is stored in millimetres, which CLAUDE.md forbids. The file is written before the Roll is submitted and even if the roll then refuses. Save As / Save all / Export deliverables: _deliver_one never passes resolution, so TIFFs carry 72 dpi (tiff.py:216). Save As runs on the UI thread, not on a daemon thread, and has no error handling. Library entries read by the GUI: demo prescans served from an entry's prescan.tif are filed with a stale capture, i.e. the shading reference and CCD mask of whichever entry was decoded last (demo.py:1176-1182 does not refresh _capture). Their raw bytes are dropped only by the shape guard, so the reference attached to those entries belongs to another pass. Walked roll folder: prescanNN.tif holds the corrected pixels (rf.prescan from DirectScanner, not raw) oriented by the session rotation at filing time. prescanNN-before.tif is written with file_entry=False, i.e. with no library entry. The library-entry deletion row should also say it can be triggered by a keyboard shortcut ('delete_pass' in _actions) as well as the strip menu.

## What the operator can do

- Prescan, scan, walk (dry-run Scan roll) and commission a roll from the contact sheet. Each asks first; the sheet commission also checks calibration and busy.
- Rotate or flip any pass (filmstrip menu, keys) or any sheet cell; rotate or flip all frames; show an earlier prescan again.
- Tick/untick frames, select them with the keyboard, open the Frame position window, drag or step a frame's intended position (nothing moves), Centre, Reset one, Reset positions for all.
- Change the roll's scan options on the sheet (dpi, prescan dpi, film, meter, IR, IR at scan resolution, 'nudge' tick). These win over the main window for that commission.
- Open rolls from the Rolls table; export, duplicate, rename, reveal or delete roll folders (delete, rename and duplicate refuse while busy or while that roll is the one opened).
- Save as ... one pass, Save all passes into a folder, zoom/pan the big view, hover for pixel values, watch the histogram.
- With 'aim' ticked, click a prescan to nudge the film so that point reaches the nearer aperture edge (asks first); use prev/next slide and fine nudges (buttons disabled while busy).
- Rebind every key, including the prescan/scan/roll keys (which ask first) and delete-pass.

## What the operator should not do

- Do not commission a roll with the Film panel's roll-name field empty, or with spaces or other non [A-Za-z0-9._-] characters in it: approved.json and the roll land in different folders.
- Do not type anything into the Film panel's 'frame' field before a roll: every frame's entry gets that value and roll Export can no longer find the entries.
- Do not rotate or flip prescans while a dry-run walk or a roll is running. Arrange them after the walk, in the sheet.
- Do not start a new dry-run walk while an older contact sheet is open; close it first.
- Do not open a second roll, or the same roll again, in one session. Restart the window between rolls.
- Do not use Delete with 'Keep the library entry? No' on reopened frames, and never in --demo.
- Do not run --look-only without --demo, and do not accept 'Calibrate now' with an empty transport.
- Do not quit while Save all or Export is writing, and close the contact sheet before quitting.
- Do not browse quickly through 3600/7200 dpi passes, run Save all or Export, or duplicate large rolls while a scan is running.
- Do not aim-click on a prescan that is not the latest one of the frame now in the gate.
- Do not walk two strips on the same day without giving each its own roll name.
- Do not rely on the sheet's 'nudge registration between frames' tick. It never acts on a commissioned roll.
- Do not resume a roll that has no walk with the plain Roll button when its remaining frames are not contiguous: it rescans and overscans.

## Mistakes nothing guards against

- Pressing Centre on a frame, then closing and reopening the sheet (or reopening the roll), silently replaces the operator's zero with the detector's position (GUI2-10).
- Closing the contact sheet with Close/X/Escape leaves the Frame position window open. Further edits there raise TclError and are lost (GUI2-15).
- Quitting the app with the sheet open loses every tick, position and turn made since the sheet was last closed (GUI2-16).
- Opening a roll from the Rolls table while a walk runs mixes the reopened survey with the walk's frames (GUI2-14).
- Walking a new strip with an old sheet open: the old sheet is raised at the end and its decisions are filed under the new walk (GUI2-07).
- Rotating the first prescan during a walk duplicates it in the survey and desyncs every later prescanNN.tif from survey.json (GUI2-08, GUI2-09).
- Commissioning from a reopened roll writes into a new date-named folder rather than the reopened roll, and approved.json into rolls/roll/ (GUI2-03).
- Walking a second strip on the same day with no roll name overwrites the first walk's survey.json and prescans (GUI2-21).
- Exporting a walked-only or walked-and-scanned roll can deliver 300 dpi walk prescans labelled as scan-resolution frames (GUI2-05).
- Exporting a roll uses the window's current mono setting, not the roll's (GUI2-24).
- In --demo, deleting a reopened frame from the filmstrip can remove a real library entry (GUI2-11).
- After opening two rolls, clicking one roll's frame shows or deletes the other roll's frame of the same number (GUI2-12).
- An aim-click during a roll queues a film move that runs when the roll ends (GUI2-19).
- Save As on a pass whose filing failed writes a 1400- or 512-px preview under the chosen name (GUI2-32).
- A JPEG export can overwrite an existing .dng (or .tif without Pillow) despite the 'nothing is overwritten' promise (GUI2-26).

## Dataflow notes

Entry into this area: all device data arrives as ScanSession events that _pump (gui.py:3131-3179) drains every 120 ms on the Tk thread. A 'result' event goes to _add_result (3439): levels are measured, a matching prescan is superseded, then _arrange (3467) chooses rotation/flip (superseded prescan > self.orientations[picture_of] > session default, with meta['reversal'] composed first). remember_arrangement (3498) then stores the orientation, appends to self.survey and edge_watch.add during a walk, sets _transport, downscales passes beyond WORKING_COPIES to 512 px (surveyed ones exempt) and calls _show. A 'filed' event sets Result.entry. The pixels on a Result are the session's corrected working copy (session._deliver: preview.downscale(image, 1400)).

Big view: _show (3527) resets the pyramid and calls _load_full (4092), which runs library.corrected(entry) on a daemon thread and returns it through the _reads queue to _loaded (4122). _loaded builds preview.pyramid levels and re-measures the histogram (thread, _measured queue). _redraw (4341) goes _geometry -> _pixels(level choice) -> preview.sample -> preview.render(channel, invert, cuts) -> preview.to_ppm -> PhotoImage reused by _paint/_place. on_hover/_pixel_at reads the corrected full array ('exact') or the working copy ('approx'). Aim: on_press -> _aim (4531) -> preview.unorient_point -> aim_millimetres (4593, fraction x APERTURE_MM) -> on_nudge (2972: deliverable_mm/plan_nudges checks, reverse tick) -> session.submit(Move(millimetres)). Everything below the entry field is millimetres, displayed as units via say_units.

Contact sheet: on_contact_sheet/_open_sheet (2160-2206) merges the kept state (_recall_sheet_state or approved.json) with _merge_kept and builds _ContactSheet(frames=self.survey). EdgeWatch readings arrive via _edges_changed -> take_readings (7805) -> _snap_proposals(snap_offset) into offsets/proposals/edges; an 'operator' source is never overwritten. _FrameAdjuster._set/_step/_drag (6706-6871) write snapped mm offsets and operator notes into the sheet. _orient writes result.rotation/flipped, sheet.rotations/flips and gui.orientations; _all also sets session.rotation/flip. _dismiss stores state() in gui-settings. _scan -> approved_from_sheet (5791: Approved(number, snap_offset(offset_mm), rotation, flipped, reference=result.image by value, reference_entry=str(entry), source)) -> on_scan_chosen (2723: sheet options override the window, predpi pinned to the survey's, chosen_span -> start_at/span) -> _write_approved (approved.json under rolls/_safe(roll field)) -> self.orientations for display -> session.submit(Roll(only, approved, start_at, frames=span, name=roll field, reverse_hold)). From there session._roll -> seek -> DirectScanner.scan_roll -> _hold_to_approved (every ticked frame is held; `correct` never runs). Frames come back as 'result' events and are filed by FrameWriter into library/ and rolls/<name or date>/frameNN.tif.

Reopen: _RollBrowser (rolls_on_disk -> roll_summary + roll_entry_index over library/*/scan.json keyed by film.frame '{roll}-{NN}') -> open_roll (2552) -> read_survey (4653): survey.json/roll.json -> renumbered/legacy_shift -> manifest_settings; read_approved gives offsets/rotations/flips/entries/sources; walked_prescans -> tiff.read(prescanNN.tif) -> preview.unorient(manifest rotation) -> Result(seq=-number, entry=reference_entry). The results are appended to self.results and replace self.survey, then edge_watch.load and _open_sheet follow. _restore_roll_settings puts the manifest settings back into the controls.

Leaving the area: Save As (UI thread) and Save all/Export (daemon threads, _saves queue) run _deliver_one (3843): library.corrected(entry) -> preview.orient -> optional to_monochrome (window setting) -> export.write (TIFF, or JPEG + DNG sidecar), falling back to the working copy when there is no entry. Roll export items come from roll_exports (4871), oriented from approved.json/roll settings rather than the entry record. Deletions: on_delete rmtree(entry) + library.reindex; roll folder rmtree/rename/copytree. No worker thread in this area touches a Tk object: results cross via _reads, _measured, _saves and EdgeWatch.version, all polled in _pump. The demo is wired only in main() (session._open_scanner = DemoScanner). There is no `if demo` above the seam; the look_only flag changes wording only. The divergences found are in what DemoScanner hands the session: no raw pixels, corrected images plus a reference.
