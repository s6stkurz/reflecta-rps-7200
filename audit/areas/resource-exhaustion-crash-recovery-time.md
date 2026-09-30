# Disk full, crashes, recovery, time (gap pass)

Area key `resource-exhaustion-crash-recovery-time`. 25 findings: 7 high, 11 medium, 6 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: resource exhaustion, crash recovery and time. I traced ENOSPC/EIO, kill -9 and power loss through every writer, and read these in full or in the relevant spans: rps7200/tiff.py, dng.py, export.py, settings.py, library.py, session.py (FrameWriter, ScanSession._run/_roll/_file/_out_name/_safe/_unclaimed), the debug spool/flush and ensure_shading/scan/read_planes/scan_roll in direct.py, shading.py, tools/scan_roll.py, tools/scan.py, tools/library.py, tools/uniformity.py (redo path), and in tools/gui.py the approved.json write, settings save, close/abort, open_roll/read_survey/roll_summary/roll_entry_index/scanned_frames, Save As/Save all/Export and _load_full. Judged from the code only. No file was modified and no hardware was touched.


Headline: nothing in the tree checks free space (no disk_usage or statvfs). No writer uses temp+rename except settings.py, and nothing fsyncs. The recovery story (resume, verify, reconstruct) quietly assumes every file on disk is either complete or absent.


The worst paths are in roll recovery, not in pixels:

(1) A disk that fills during a roll does not stop it. Every frame is scanned and then discarded, the GUI shows only a scrolling log line, the CLI says nothing until the end, and roll.json marks each frame "done" before the writer has run.

(2) roll.json is truncated in place, so a kill or ENOSPC during its per-frame rewrite empties it. The roll then disappears from the Rolls list, will not reopen (even when its walk is intact), and on the next resume is silently treated as absent and overwritten.

(3) The documented GUI resume (Rolls..., then Scan chosen frames) writes into rolls/<roll-name field or today's local date>, not into the folder that was reopened. The name field is never restored, so after any restart the resumed frames land in a different folder under different library keys.

(4) tools/scan_roll.py never reads an existing roll.json. --start-at "resume" replaces the manifest, contrary to README:547 and its own docstring.

(5) Unnamed rolls are local-date folders. A second walk on the same day overwrites the first walk's survey.json and prescanNN.tif. The code knows this happened (the walked_prescans docstring) and only defends the reading side.


Also found:
- Roll names reach paths unsanitised (.., absolute paths, Windows-illegal characters), and the failure comes after the film has been moved.
- Non-atomic in-place migrations can destroy a prescan.tif that has no raw bytes behind it.
- A failed write of the calibration cache throws away a good 3-4 minute calibration.
- approved.json is replaced, not merged, on resume.
- Closing the window during a roll waits for the whole roll, with no stop offered.
- Full-resolution loads start without a concurrency cap, each with float64 shading temporaries.
- The GUI cannot join library entries of CLI-scanned rolls ("/" against "-").
- A CLI dry-run walk files nothing in the library.

The clocks (UTC entry ids, local roll dates, local output names) break no join by themselves, because every join is on the roll name. The midnight and DST harm comes through the date-named default roll and the unrestored name field. The demo shares every writer above the seam, so it behaves identically here; nothing demo-specific was found in this area beyond what is already reported.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [RX-1](#resource-exhaustion-crash-recovery-time-rx-1) | high | error-handling | A full disk does not stop a roll: every frame is scanned and discarded, the GUI only logs it, the CLI says nothing until the end, and roll.json marks the frames done | [P24](../problems/P24-disk-full-and-quitting.md) |
| [RX-2](#resource-exhaustion-crash-recovery-time-rx-2) | high | data-integrity | roll.json/survey.json are truncated in place every frame; a kill or ENOSPC mid-write empties it, the roll disappears from the list, cannot be reopened, and the next resume silently overwrites it | [P19](../problems/P19-roll-manifests.md) |
| [RX-3](#resource-exhaustion-crash-recovery-time-rx-3) | high | bug | Resuming a reopened roll does not write into the reopened folder: the roll name field is never restored, and blank falls back to today's local date | [P18](../problems/P18-roll-folder-identity.md) |
| [RX-4](#resource-exhaustion-crash-recovery-time-rx-4) | high | data-integrity | tools/scan_roll.py --start-at 'resume' replaces the existing roll.json with a fresh manifest, contrary to README and its own docstring | [P19](../problems/P19-roll-manifests.md) |
| [RX-5](#resource-exhaustion-crash-recovery-time-rx-5) | high | data-integrity | Unnamed rolls share one local-date folder: a second walk (or CLI roll) the same day overwrites the first walk's survey.json and prescanNN.tif, and two strips' library entries share one roll key | [P18](../problems/P18-roll-folder-identity.md) |
| [RX-15](#resource-exhaustion-crash-recovery-time-rx-15) | high | data-integrity | A walk made with tools/scan_roll.py --dry-run files nothing in the library: its prescans exist only as corrected 8-bit prescanNN.tif | [P08](../problems/P08-passes-never-filed.md) |
| [RXV-1](#resource-exhaustion-crash-recovery-time-rxv-1) | high | data-integrity | Debug filing deletes the spooled raw bytes and pixels even when library.save failed, so a full or unwritable library destroys the only copy | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [RX-6](#resource-exhaustion-crash-recovery-time-rx-6) | medium | data-integrity | A frame is recorded as done (GUI) or as having its file (CLI) before anything is written; after a crash or a writer failure, resume skips frames that have no file and no entry | [P19](../problems/P19-roll-manifests.md) |
| [RX-7](#resource-exhaustion-crash-recovery-time-rx-7) | medium | user-error | Roll names reach the filesystem unsanitised in the GUI roll and the CLI: separators, '..', absolute paths and Windows-illegal or reserved names; the failure comes after the seek has moved the film | [P18](../problems/P18-roll-folder-identity.md) |
| [RX-8](#resource-exhaustion-crash-recovery-time-rx-8) | medium | data-integrity | Every writer except settings writes its final path in place with no temp+rename and no fsync; partial files survive under ordinary names and some make whole rolls unreadable | [P10](../problems/P10-non-atomic-writes.md) |
| [RX-9](#resource-exhaustion-crash-recovery-time-rx-9) | medium | data-integrity | In-place rewrites of complete library entries (migrate-raw/migrate-direction --write, uniformity 'redo') are not atomic; an interruption makes an entry vanish or destroys the only prescan.tif | [P10](../problems/P10-non-atomic-writes.md) |
| [RX-10](#resource-exhaustion-crash-recovery-time-rx-10) | medium | error-handling | A failed write of the calibration cache discards a successful 3-4 minute calibration and blocks all scanning; a truncated cache makes 'reuse' fail | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [RX-11](#resource-exhaustion-crash-recovery-time-rx-11) | medium | data-integrity | approved.json is rewritten whole with only this commission's ticked frames; on a resume it erases the earlier frames' turns and flips, which roll export uses; a truncated file reads as empty | [P19](../problems/P19-roll-manifests.md) |
| [RX-12](#resource-exhaustion-crash-recovery-time-rx-12) | medium | user-error | Closing the window during a roll waits for the whole roll and every queued job, with no stop offered and no timeout; Save all/Export threads are killed mid-write on close | [P24](../problems/P24-disk-full-and-quitting.md) |
| [RX-13](#resource-exhaustion-crash-recovery-time-rx-13) | medium | design | A full-resolution load starts for every pass shown, with no cap on concurrency, and each re-runs shading in float64; flipping through a 3600 dpi RGBI roll can queue many ~0.7 GB loads | -- |
| [RX-14](#resource-exhaustion-crash-recovery-time-rx-14) | medium | bug | The GUI cannot join library entries of rolls scanned by tools/scan_roll.py: the frame key is 'roll/NN' there and 'roll-NN' in the window's index | [P21](../problems/P21-roll-to-library-join.md) |
| [RXV-2](#resource-exhaustion-crash-recovery-time-rxv-2) | medium | doc-mismatch | The GUI's before-correction prescan is never filed with its bytes; the docstring's RPS7200_DEBUG fallback cannot happen because the session forces debug=False | -- |
| [RXV-3](#resource-exhaustion-crash-recovery-time-rxv-3) | medium | data-integrity | verify cannot detect damage to shading.npz, ccd_mask.bin or prescan.tif: none has a checksum, and prescan.tif is not checked at all | [P10](../problems/P10-non-atomic-writes.md) |
| [RX-16](#resource-exhaustion-crash-recovery-time-rx-16) | low | error-handling | Settings save: rename-atomic against a kill, but not fsynced, shares a fixed .part name between windows, and fails silently, losing the only copy of uncommissioned sheet decisions | -- |
| [RX-17](#resource-exhaustion-crash-recovery-time-rx-17) | low | error-handling | Save As writes on the UI thread with no error handling: a failure goes only to stderr, leaves a partial file, and an overwrite destroys the old file | -- |
| [RX-18](#resource-exhaustion-crash-recovery-time-rx-18) | low | bug | Output-folder names are claimed when the job is queued, not when the file is written: a walk's corrected prescan and its 'before' prescan get the same name and the second overwrites the first | -- |
| [RX-19](#resource-exhaustion-crash-recovery-time-rx-19) | low | design | Clocks: UTC entry ids stamped at filing time, local-date roll folders, zone-less local output names, no timestamps in the GUI's roll.json, an unreachable %H%M%S branch, and st_ctime read as 'created' on Linux | -- |
| [RXV-4](#resource-exhaustion-crash-recovery-time-rxv-4) | low | design | Memory held per roll frame in RAM is unbounded by size: FrameWriter jobs keep pixels, raw pixels and raw bytes; scan.py brackets hold every pass until close | -- |
| [RXV-5](#resource-exhaustion-crash-recovery-time-rxv-5) | low | error-handling | A truncated demo/pictures.npz crashes the demo's signing thread for good (BadZipFile is not caught), silently emptying the second-roll picture pool | -- |
| [RX-20](#resource-exhaustion-crash-recovery-time-rx-20) | info | design | Crash-state matrix: what a kill -9 or power loss leaves in a GUI roll and a CLI roll, and what resume, verify and reconstruct do with each; no tool recovers orphans | -- |

## Findings in full

<a id="resource-exhaustion-crash-recovery-time-rx-1"></a>

### RX-1 -- A full disk does not stop a roll: every frame is scanned and discarded, the GUI only logs it, the CLI says nothing until the end, and roll.json marks the frames done

**Severity** high · **Category** error-handling · **Verdict** partly · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `rps7200/session.py:1046-1056`, `rps7200/session.py:1074-1100`, `rps7200/session.py:1348-1355`, `rps7200/session.py:1895-1926`, `tools/scan_roll.py:343`, `tools/scan_roll.py:581-585`, `tools/scan_roll.py:605-613`, `tools/scan_roll.py:624-628`, `rps7200/export.py:146-153`, `tools/scan.py:238-246`, `rps7200/library.py:308-310`

No free-space check exists anywhere, and a filing failure never reaches the roll loop. When the full volume is the output folder or the library, and rolls/ still has room, the roll keeps scanning. Each frame's export.write fails and library.save never runs, so the raw bytes and pixels are dropped from memory, while roll.json marks every frame done:true. The GUI shows only a log line per frame. The CLI prints a success line per frame and lists the failures at the end. When rolls/ is on the full volume, the roll.json rewrite on the scanner thread itself fails within a few frames. The job then dies with roll.json truncated or empty (see RX-2), and the CLI's final checkpoint raises a traceback instead of printing the summary. export's DNG note claims the library entry keeps all channels before that save has run. scan.py's library-save loop raises on the first failure, so the delivered file is never written. A reindex failure after scan.json has been written reports a complete entry as unfiled.

**Evidence (from the code):**

```text
No free-space check exists anywhere: grep for disk_usage|statvfs|ENOSPC finds nothing. FrameWriter._run swallows every failure per frame: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")`. The GUI's only signal is `self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1354). The roll loop never looks at writer.errors. Meanwhile it writes `"done": bool(rf.error is None and rf.image is not None)` and rewrites roll.json. The CLI builds `writer = FrameWriter()` with no on_done, prints `picture {number}: {path} ...` at submit time, and prints the errors only after `writer.finish()` at the end of the roll. Its final `checkpoint()` (scan_roll.py:628) sits outside any try. export._write_infrared tells the operator `the library entry keeps all {channels} channels`, but library.save runs after it in the same job, on the same full disk. scan.py files in a bare loop, `for held in pending: entries.append(library.save(...))`, so the first failure raises and the delivered file is never written. library.save calls `reindex(root)` after scan.json, so a reindex ENOSPC reports a complete entry as unfiled.
```

**Failure scenario:** A 38-frame 3600 dpi RGBI roll is started with 8 GB free. By the code's own figures each frame writes ~120 MB (frameNN.tif) + ~120 MB (scan.tif) + ~100 MB (raw.bin.gz), plus ~120-142 MB more if an output folder is set. Around frame 20 tiff.write raises ENOSPC. Frames 20-38 are each scanned for ~6 minutes and discarded. The log shows 19 'could not be filed' lines between other messages, and roll.json lists all 38 as done, so Rolls... reports the roll finished and a resume offers nothing.

**Fix:** Before a roll or scan, check shutil.disk_usage against an estimate per frame (the estimate_seconds counterpart for bytes) for rolls/, the library and out_dir, and refuse or warn. Have FrameWriter expose a 'failing' state that the roll loop checks between frames (like should_stop). Stop, or at least pause, after N consecutive filing failures, and raise a visible (non-modal) error state. In the CLI, pass on_done so each failure is printed when it happens, and wrap the final checkpoint. Record 'done' only after filing succeeds (see RX-6). Drop the DNG note's claim, or make it conditional on the library save.

<details><summary>Second reader's check</summary>

Code matches the evidence. FrameWriter._run (session.py:1046-1056) catches every exception and only appends it to errors. _filed (session.py:1353-1355) only emits a log line. The roll loop never reads writer.errors and writes "done": bool(rf.error is None and rf.image is not None) (session.py:1905) right after the async submit. scan_roll.py creates `writer = FrameWriter()` without on_done, prints `picture {number}: {path}` at submit time, and prints the errors only after writer.finish(). Its final checkpoint() (scan_roll.py:628) is outside any try. export._write_infrared promises 'the library entry keeps all channels' before library.save has run. scan.py:238-246 saves in a bare loop ahead of export.write, and library.save calls reindex after scan.json. What the finding gets wrong is 'the roll goes on for hours'. That only holds when the full volume is not the one holding rolls/, for example out_dir on a full USB stick or the library on another disk. That is a realistic setup, and there every frame's library entry is lost because export.write runs first. When rolls/ shares the full volume, the per-frame roll.json truncate-and-rewrite on the scanner thread (session.py:1924) needs a new block every few frames as the file grows. It then raises ENOSPC, fails the Roll job and leaves roll.json truncated or empty, which is the RX-2 outcome. In the CLI the same happens through checkpoint() in the loop, and then again through the unguarded final checkpoint, which ends in a traceback.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-2"></a>

### RX-2 -- roll.json/survey.json are truncated in place every frame; a kill or ENOSPC mid-write empties it, the roll disappears from the list, cannot be reopened, and the next resume silently overwrites it

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `rps7200/session.py:1922-1926`, `rps7200/session.py:1649-1654`, `tools/gui.py:5048-5055`, `tools/gui.py:4686-4694`, `tools/gui.py:2564-2572`, `tools/scan_roll.py:326-329`, `rps7200/session.py:1812-1816`

The one file meant to 'still describe this roll a year from now' is rewritten whole after every frame, by truncate-then-write. A hard kill in that window, ENOSPC, or EIO leaves it empty or cut mid-JSON. From then on:
- the Rolls table hides the folder entirely, walk included, because roll_summary returns None;
- --open-roll or opening the folder refuses it, even when survey.json and every prescan are intact, because read_survey reads roll.json unguarded;
- a resumed Roll with the same name treats the unreadable file as no history (earlier = {}) and writes a manifest with only the new frames, destroying the 'wanted' set and the record of every earlier frame (registration, exposure, transport position) without a word.
The write also runs on the scanner thread with the device open, so a slow or hung network mount stalls the worker between frames with the scanner idle.

**Evidence (from the code):**

```text
session.py:1924: `manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")`. write_text opens with 'w', truncating first, and there is no temp+rename or fsync. Resume: `try: earlier = json.loads(manifest_path.read_text(...)) except (OSError, ValueError): earlier = {}`. Rolls list: roll_summary reads survey.json and roll.json in one try, `except (OSError, ValueError): return None`, and rolls_on_disk skips None. read_survey calls `json.loads(roll_path.read_text(...))` unguarded, so open_roll shows 'does not hold a roll this can read'. The CLI `checkpoint()` uses the same write_text. The comment at session.py:1814-1816 says 'nothing local happens on the scanning thread with the device open', yet this write runs there.
```

**Failure scenario:** A 3-hour GUI roll hits ENOSPC while roll.json is rewritten after frame 25, leaving a 0-byte roll.json. The job fails with OSError. After space is freed the operator opens Rolls...: the roll is not listed. He types the same roll name and scans frames 26-38. The new roll.json holds 13 frames, wanted=[26..38], and reads 'finished'. The records of frames 1-25 are gone (their library entries survive).

**Fix:** Write manifests atomically: write to <name>.tmp in the same directory, flush and fsync, then os.replace (and fsync the directory on POSIX). Treat an unreadable existing manifest as an error that stops a resume (rename it aside, e.g. roll.json.corrupt-<ts>, and tell the operator) instead of `earlier = {}`. Make roll_summary/read_survey degrade per file: list the roll as 'manifest unreadable' rather than hiding it. Consider moving the manifest write onto the writer thread, after the frame's files, which also fixes RX-6.

<details><summary>Second reader's check</summary>

Checked each step in code. session.py:1924-1926 and scan_roll.py:326-329 truncate and rewrite in place, with no temp file, no rename and no fsync. On resume, _roll (session.py:1655-1658) catches (OSError, ValueError) and sets `earlier = {}`. roll_summary (gui.py:5048-5055) returns None when either JSON is unreadable, and rolls_on_disk skips None, so the folder disappears from the list. read_survey (gui.py:4686-4691) calls json.loads without a guard, and open_roll turns the ValueError into 'does not hold a roll this can read'. The write runs on the scanner thread inside _roll's loop, which contradicts the comment at session.py:1814-1816. One caveat: a resume only overwrites the file when it lands in the same folder, which per RX-3 needs the name to be retyped. An ENOSPC during the manifest rewrite is the likely way a full disk ends a roll (see RX-1).

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-3"></a>

### RX-3 -- Resuming a reopened roll does not write into the reopened folder: the roll name field is never restored, and blank falls back to today's local date

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2552-2576`, `tools/gui.py:4780-4792`, `tools/gui.py:1520-1532`, `tools/gui.py:2853-2863`, `tools/gui.py:194`, `rps7200/session.py:1634-1635`, `rps7200/session.py:1880-1882`

README says that 'opening a roll with frames left brings back its contact sheet and approvals ... and restores the roll's own settings'. TODO says the remaining frames 'go through the ordinary Scan chosen frames path'. But the destination of that path is whatever the roll-name box holds, and after any restart (the case resume exists for) it holds nothing. The roll therefore goes to rolls/<today's local date>, or to whatever other roll was last typed. That folder has no earlier roll.json, so nothing is merged. `wanted` becomes only this run's frames. The new frames are filed as '<date>-NN', so roll_entry_index files them under a different roll. The reopened roll stays 'n of m' forever and a new date-named roll appears holding the rest. The same happens without a restart when a walk made before local midnight is commissioned after it with a blank name: the walk is in rolls/D and the scan goes to rolls/D+1. (approved.json going to rolls/_safe(name) is a related defect already reported by the demo-parity audit.)

**Evidence (from the code):**

```text
open_roll only runs `restored = self._restore_roll_settings(out["settings"])`, and RESTORABLE lists resolution, prescan_resolution, infrared, fast_infrared, film, meter, mono_channel, correct, reverse_hold, frames, start_at, but no roll name. The name field is a bare `var = tk.StringVar()` (gui.py:1529) and `REMEMBERED_FILM = ("stock", "process", "tags")`, so it is neither remembered nor restored. The resume submits `Roll(... name=self.fields["roll"].get().strip(), ...)` with no `out=`. The session then does `name = job.name or time.strftime("%Y-%m-%d")` and `out = Path(job.out) if job.out else self.rolls / name`. Library keys use the same name: `frame=job.notes.frame or f"{name}-{number:02d}"`.
```

**Failure scenario:** Roll 'gold200' dies at frame 12 of 36. Next morning the operator opens it from Rolls..., sees '12 of 36 scanned, 24 left', puts the strip back and presses Scan chosen frames, as the dialog says. The roll name box is empty, so frames 13-36 are written to rolls/2026-09-25/ with library frame keys '2026-09-25-13'... Rolls... then shows gold200 unfinished and a separate finished roll '2026-09-25'. Exporting gold200 yields 12 frames.

**Fix:** When a roll is opened, remember its folder and pass it explicitly as Roll(out=<that folder>, name=<manifest roll name>). Do not derive the destination from a free-text field. Fill the name field from the manifest and make it read-only while a roll is loaded, or show the destination folder in the confirm dialog. Never fall back to the current date for a job that continues an existing roll.

<details><summary>Second reader's check</summary>

open_roll (gui.py:2552-2700) restores only the RESTORABLE settings, which have no name key, and never sets fields['roll']. REMEMBERED_FILM excludes 'roll' (gui.py:194). _commission submits Roll(name=self.fields['roll'].get().strip()) with no out= (gui.py:2862). The session falls back to time.strftime('%Y-%m-%d') (session.py:1634). The resume dialog tells the operator to press 'Scan chosen frames' (gui.py:2656-2672) without naming the destination. The library frame key becomes f'{name}-{NN}' (session.py:1880-1882), so the frames are filed under the new date. _write_approved also goes to rolls/_safe('') == rolls/roll.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-4"></a>

### RX-4 -- tools/scan_roll.py --start-at 'resume' replaces the existing roll.json with a fresh manifest, contrary to README and its own docstring

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/scan_roll.py:298-329`, `tools/scan_roll.py:448-453`, `tools/scan_roll.py:20-24`, `tools/scan_roll.py:633-634`

**Doc claim:** README.md:547-549 ('`--start-at` resumes a roll that stopped ... and a resumed roll carries forward what the earlier session already did instead of overwriting its manifest'); tools/scan_roll.py:22-24 ('a crash two hours in should cost the frame it was on, not the roll -- `--start-at` resumes from the manifest')

ScanSession._roll learned to read and merge the earlier roll.json ('Without this the second run replaced the first'). The CLI roll never did. Following the tool's own advice ('resume a failed picture with --start-at N') into the same --roll therefore overwrites the manifest: every earlier frame's registration, exposure, transport position and file name is gone, and 'wanted' and 'finished' describe only the resumed tail. No test covers it: test_a_roll_that_cannot_be_placed_leaves_the_folder_as_it_was covers only the unplaced case.

**Evidence (from the code):**

```text
The manifest is built from scratch, `"frames": [],`, and the first thing written after calibration is `out.mkdir(parents=True, exist_ok=True); checkpoint(); placed = True`, where `checkpoint()` is `manifest_path.write_text(json.dumps(manifest, ...))`. Nowhere in main() is an existing roll.json read (hold_from_walk reads only the --approved folder). The tool itself advises `print("resume a failed picture with --start-at N", file=sys.stderr)`.
```

**Failure scenario:** `scan_roll.py --roll gold200 --frames 36` stops at frame 20 (a transport fault). The operator runs `scan_roll.py --roll gold200 --start-at 21 --frames 16`. After calibration, rolls/gold200/roll.json is rewritten with frames [] and then 21-36 only. The GUI's Rolls... shows the roll as 16 scanned, and the records of 1-20 are lost.

**Fix:** Load and merge an existing roll.json the way session._roll does (renumbered, per-frame replace, union of wanted), or share one implementation between the two. Until then, refuse to overwrite an existing roll.json without an explicit --overwrite flag. Fix the docstring and README:547-549 to match.

<details><summary>Second reader's check</summary>

scan_roll.py builds the manifest with "frames": [] (scan_roll.py:298-324) and calls checkpoint() right after calibration (scan_roll.py:448-453). Nothing in main() reads an existing roll.json, and the tool advises `--start-at N` (scan_roll.py:633-634). README.md:547-549 says a resumed roll 'carries forward what the earlier session already did instead of overwriting its manifest', inside the section on tools/scan_roll.py. The module docstring (scan_roll.py:20-24) says '--start-at resumes from the manifest'. Both claims contradict the code.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-5"></a>

### RX-5 -- Unnamed rolls share one local-date folder: a second walk (or CLI roll) the same day overwrites the first walk's survey.json and prescanNN.tif, and two strips' library entries share one roll key

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1642`, `rps7200/session.py:1650`, `rps7200/session.py:1817-1833`, `rps7200/session.py:640-646`, `tools/gui.py:4999-5008`, `tools/scan_roll.py:291-296`, `tools/scan_roll.py:499-500`

The default roll name is the local calendar date, so every unnamed walk or roll on one day shares a folder. A walk replaces survey.json outright. Its prescanNN.tif files overwrite the earlier walk's files of the same number, and those are the reference pictures that approved offsets were measured against and that open_roll rebuilds the contact sheet from. If the first strip was already commissioned, its roll.json now sits beside a survey of a different strip. Reopening shows the second strip's pictures with the first strip's progress, and the per-frame decisions are keyed by numbers that now name other pictures. Both strips' library entries are filed as '<date>-NN', so roll_entry_index maps frame N to whichever entry the glob visits last, and 'Export' of that roll can mix photographs from two strips. The fix so far only defends the reader (walked_prescans reads by the survey's own records).

**Evidence (from the code):**

```text
GUI: `name = job.name or time.strftime("%Y-%m-%d")` and `manifest_path = out / ("survey.json" if job.dry_run else "roll.json")`. `earlier` is read only `if not job.dry_run`. The walk's reference prescan is written straight to `surveyed = out / f"prescan{number:02d}.tif"` with no `_unclaimed`. CLI: `roll_name = args.roll or datetime.now().strftime("%Y-%m-%d")` and `tiff.write(str(pre), frame.prescan)`. The code records that this has already happened (walked_prescans docstring): 'A second walk into the same date-named folder rewrites `survey.json` and leaves the first walk's extra `prescanNN.tif` behind ... `rolls/2026-09-23` holds prescan01-02 from the walk its survey lists ... beside prescan03-06 from an earlier walk'. Join: `roll, _, number = frame.rpartition("-")` then `out.setdefault(roll, {})[int(number)] = record_path.parent`, the last glob hit winning.
```

**Failure scenario:** Morning: strip A is walked unnamed into rolls/2026-09-24, frames 1-6 are commissioned and scanned. Afternoon: strip B is walked unnamed. survey.json and prescan01-06.tif are replaced by B's, and prescan07+ are added. Reopening rolls/2026-09-24 shows B's pictures marked '6 scanned'. Exporting the roll re-corrects entries for '2026-09-24-01..06', which are A's frames or B's depending on glob order.

**Fix:** Never reuse a folder silently. Make the default name unique (date plus a time, or date-N via the existing duplicate_name logic), or refuse to start a walk or roll into a folder that already holds a survey.json or roll.json from a different run unless it is an explicit resume (RX-3). Put a per-run id in the manifest and in the library frame key so joins cannot cross strips.

<details><summary>Second reader's check</summary>

The GUI default is `name = job.name or time.strftime("%Y-%m-%d")` (session.py:1634). A dry run never reads the earlier manifest (session.py:1655 `if not job.dry_run and ...`), so survey.json is replaced. prescanNN.tif is written to the fixed path `out / f"prescan{number:02d}.tif"` with no _unclaimed (session.py:1817). The CLI does the same (scan_roll.py:291, 499-500). The walked_prescans docstring (session.py:640-646) records that this has already happened on disk. roll_entry_index keeps the last glob hit per (roll, number) (gui.py:5004-5008), so two strips filed as '<date>-NN' collide.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-15"></a>

### RX-15 -- A walk made with tools/scan_roll.py --dry-run files nothing in the library: its prescans exist only as corrected 8-bit prescanNN.tif

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan_roll.py:492-510`, `tools/scan_roll.py:466`, `rps7200/session.py:1804-1833`

**Doc claim:** tools/scan_roll.py:2 ('Scan a whole roll or strip, unattended, filing every frame in the library'); CLAUDE.md 'Scans' ('File every scan in the library, with its raw bytes. `tools/scan.py` and `tools/scan_roll.py` both default to `library/`')

The CLI walk keeps no raw bytes, no uncorrected pixels, no CCD mask and no reference for any prescan. These are the passes that every approved offset and hold-loop reference is later measured against, and their 8-bit prescanNN.tif is shading-corrected and turned. The GUI walk files each one with its bytes. The CLI docstring and CLAUDE.md say every scan is filed.

**Evidence (from the code):**

```text
CLI dry-run branch: `if frame.prescan is not None: pre = out / f"prescan{number:02d}.tif"; tiff.write(str(pre), frame.prescan)`. No writer.submit and no library.save; only the scan branch submits. The GUI walk, by contrast, calls `self._file(seq, number, rf.prescan, ... raw_image=rf.raw_prescan, path=surveyed, ...)` with library=self.root, and comments 'On a dry run the prescans are the entire product ... so they are filed in their own right'.
```

**Failure scenario:** A strip is walked with `scan_roll.py --dry-run`, and a registration question later needs the raw prescan: library/ has nothing for that walk, and `reconstruct` cannot re-derive it.

**Fix:** Submit each dry-run prescan through the FrameWriter with library=args.library, raw_image=frame.raw_prescan, capture=s.capture_record() and path=pre, as the session does. This also moves the prescan write off the scanner thread.

<details><summary>Second reader's check</summary>

The CLI dry-run branch (scan_roll.py:492-510) only calls tiff.write for prescanNN.tif and prescanNN-before.tif, synchronously on the scanner thread with the device open. It calls no writer.submit and no library.save, and the scanner is opened with debug=False (scan_roll.py:349), so nothing is filed. The GUI walk files each one with raw_prescan (session.py:1818-1833). README recommends starting with --dry-run, so this is the documented walk path. Every walk made that way keeps only corrected, rotated 8-bit prescans with no raw bytes, CCD mask or reference, which fails the owner's exact-bits requirement. Raised to high for that reason.

</details>

<a id="resource-exhaustion-crash-recovery-time-rxv-1"></a>

### RXV-1 -- Debug filing deletes the spooled raw bytes and pixels even when library.save failed, so a full or unwritable library destroys the only copy

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:711-752`, `rps7200/direct.py:754-764`

The unlink runs in the `finally` of the per-item try, so it runs whether or not library.save succeeded. After that the whole spool directory is removed. When filing fails (ENOSPC or EIO on the library volume, a read-only library, a permission error, a bug in save), the spool holds the only copy of that pass's raw bytes and uncorrected pixels, and it is deleted straight after the one-line 'could not file' log. The comment says 'Free each frame's spool as soon as it is filed', but it is freed when it was not filed too. This is the path CLAUDE.md requires for every ad-hoc script (RPS7200_DEBUG=1), and the one place where freeing space and retrying could otherwise have recovered the data.

**Evidence (from the code):**

```text
`try: image = np.load(item["image_path"], mmap_mode="r"); entry = library.save(...)` / `except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")` / `finally: image = None ... for key in ("image_path", "raw_path"): ... Path(path).unlink(missing_ok=True)` and afterwards `shutil.rmtree(self._debug_spool, ignore_errors=True)`
```

**Failure scenario:** An ad-hoc script with RPS7200_DEBUG=1 takes six 3600 dpi RGBI passes. At close() the library disk fills during entry 3. Entries 3-6 each log 'could not file scan N (No space left on device)', and their NNN-raw.bin and NNN-image.npy are unlinked. The partial library directory for entry 3 stays behind with no scan.json. Freeing space afterwards recovers nothing.

**Fix:** Unlink only on success: move the unlink into the success branch after library.save returns. On failure keep the spool, keep its meta on disk (write NNN-meta.json at spool time), and log the spool path. Only rmtree the directory when every item was filed. Add a 'tools/library.py file-spool <dir>' action that files a leftover spool.

<a id="resource-exhaustion-crash-recovery-time-rx-6"></a>

### RX-6 -- A frame is recorded as done (GUI) or as having its file (CLI) before anything is written; after a crash or a writer failure, resume skips frames that have no file and no entry

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `rps7200/session.py:1883-1905`, `rps7200/session.py:1922-1923`, `tools/scan_roll.py:532-542`, `tools/scan_roll.py:584-585`, `tools/scan_roll.py:605-610`, `tools/gui.py:5238-5259`, `rps7200/session.py:1032-1033`

**Doc claim:** rps7200/session.py:1922-1923 ('Rewritten after every frame. A roll takes hours and a crash should cost the frame it was on, not the roll.'); tools/scan_roll.py:20-23 ('Every frame goes to disk the moment it exists')

The manifest is written on the scanner thread as soon as a frame is queued, while its TIFF, output copy and library entry are written later on the writer thread. Up to three frames can be in that state at once (two queued plus one being written), and the frame on the scanner is not recorded at all. After kill -9, power loss or a writer failure, these frames are marked done or carry a file name, but frameNN.tif is missing or partial and there is no library entry. scanned_frames counts them finished. The resumed sheet leaves them unticked ('a resumed roll should finish'), and the only hint comes at export time ('N scanned frame(s) have no library entry left'). A CLI roll killed at any point has entry=None for every frame, so its manifest never says which entries belong to it.

**Evidence (from the code):**

```text
session.py: `self._file(...)` only calls `self._writer.submit(...)` into `queue.Queue(maxsize=depth)` with depth 2. The very next statement builds `"done": bool(rf.error is None and rf.image is not None)` and writes roll.json. CLI: `record["file"] = path.name` before `writer.submit(...)`, then `checkpoint()`. `record["entry"]` is filled only after `writer.finish()` at the end of the run. GUI reader: `finished = record.get("done")`, and for CLI manifests without the key, `finished = not record.get("error") and "prescan" not in record`.
```

**Failure scenario:** A GUI roll at 1800 dpi is killed (OOM, power loss) while frame 17 is being scanned and 15-16 are still queued in FrameWriter. roll.json says 15 and 16 are done, but neither has frame15.tif, frame16.tif or a library entry. On resume the sheet offers 17-36 only. The operator finds out at export that 15 and 16 are missing.

**Fix:** Write a frame's record, or at least flip 'done', only from the writer's on_done after the library entry exists, and record the entry path in the manifest. Doing it via a small manifest-writer on the writer thread avoids writing the file from two threads. For the CLI, update record['entry'] and checkpoint from on_done. Make scanned_frames require evidence (entry or file present) for manifests without 'done'.

<details><summary>Second reader's check</summary>

_file only calls submit into queue.Queue(maxsize=2) (session.py:1032, 2105). The record with 'done' is built and roll.json written immediately after (session.py:1895-1926). Up to three frames can be unfiled at once: one being written plus two queued. The CLI sets record['file'] before submit (scan_roll.py:534-535) and fills 'entry' only after writer.finish() (scan_roll.py:605-610). scanned_frames treats a CLI record without 'done' as finished if it has no error and no 'prescan' (gui.py:5252-5254). So after a crash, frames with no file or entry count as done. The docstrings' 'a crash should cost the frame it was on' is contradicted.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-7"></a>

### RX-7 -- Roll names reach the filesystem unsanitised in the GUI roll and the CLI: separators, '..', absolute paths and Windows-illegal or reserved names; the failure comes after the seek has moved the film

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1619-1636`, `rps7200/session.py:2170-2189`, `tools/gui.py:2156`, `tools/gui.py:2862`, `tools/scan_roll.py:291-292`

The roll-name box is free text and goes straight into a path join:
- 'a/b' or 'a\b' makes a nested folder that rolls_on_disk (one level deep) never lists;
- '..' writes roll.json or survey.json and prescanNN.tif into the working directory;
- an absolute string ('/tmp/x', 'D:\scans') replaces the rolls root entirely, because Path('rolls') / '/tmp/x' == '/tmp/x';
- on Windows ':', '*', '?', '"', '<', '>', '|', trailing dots/spaces and CON/PRN/AUX/NUL/COMn/LPTn make mkdir fail.
The mkdir runs after the seek, so the film has already been wound before the job fails. Because approved.json does use _safe(name), a legal but unusual name also splits the roll's files across two folders (that split is already reported).

**Evidence (from the code):**

```text
session.py:1621 runs `seek(self._scanner, first, ...)` first, then `name = job.name or time.strftime("%Y-%m-%d")`, `out = Path(job.out) if job.out else self.rolls / name`, `out.mkdir(parents=True, exist_ok=True)`. `_safe()` exists, with a comment warning that `CON` 'fails as NotADirectoryError from `mkdir`, which reads like a bug in this driver'. It is applied to approved.json and output names but not here. CLI: `out = Path(args.out or f"rolls/{roll_name}")`.
```

**Failure scenario:** On Windows the operator names a roll 'Gold 200: Italy'. He presses Roll, the transport winds back 11 frames to frame 1, then the job fails with 'OSError: [WinError 123] ...'. Or he types '..' and the survey lands in the repo root, where the Rolls list never shows it.

**Fix:** Validate the name before submitting (reject separators, '..', drive or absolute forms, reserved names and Windows-illegal characters), or map it through _safe() consistently for the folder, approved.json and the library key. Do the mkdir and a test write before the seek, so a bad destination costs no film motion. Apply the same check in scan_roll.py.

<details><summary>Second reader's check</summary>

The seek runs first (session.py:1621). The folder is then `self.rolls / name` with the raw name and mkdir(parents=True) (session.py:1634-1636). _safe exists (session.py:2182-2189) but is applied only to approved.json (gui.py:2940) and output names. There is no name validation before submit (gui.py:2156, 2862). The CLI builds `Path(args.out or f"rolls/{roll_name}")` (scan_roll.py:292). Path('rolls') / '/abs' discards 'rolls'. The CLI's mkdir comes after seek and calibration (scan_roll.py:451), so a bad name costs a 3-4 minute calibration as well as film motion.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-8"></a>

### RX-8 -- Every writer except settings writes its final path in place with no temp+rename and no fsync; partial files survive under ordinary names and some make whole rolls unreadable

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/tiff.py:164`, `rps7200/tiff.py:167-168`, `rps7200/dng.py:145-146`, `rps7200/export.py:146-153`, `rps7200/export.py:181-193`, `rps7200/library.py:169-185`, `rps7200/library.py:308-310`, `rps7200/library.py:745-749`, `tools/gui.py:4734-4735`, `tools/gui.py:2943-2955`, `tools/scan.py:278-281`, `tools/gui.py:2477-2481`

A kill, ENOSPC, EIO or power loss during any of these writes leaves a truncated file with a normal name. Nothing in the code removes a partial file. Consequences found in the code:
(a) A truncated prescanNN.tif makes read_survey raise, and open_roll catches ValueError and refuses the whole walk, so one bad prescan out of 36 blocks reopening the strip. hold_from_walk dies the same way.
(b) After a failed DNG the operator is told it 'could not be written', but a truncated .dng with the scan's stem stays beside the JPEG.
(c) Save As and export.write over an existing file truncate it first, so a failure destroys the previous good file.
(d) Without fsync, a power loss shortly after library.save can leave zero-length or partial scan.tif, raw.bin.gz or scan.json on filesystems without ext4-style rename or ordering guarantees (exFAT/NTFS external drives are common for libraries). A zero-length scan.json makes the entry invisible to entries(), verify, reconstruct, duplicates, roll_entry_index and the demo. verify catches damage only when scan.json survived.
(e) Because library.save reuses a directory that has no scan.json, a save landing on the same id (same UTC second, stock, frame and dpi) as an earlier failed save inherits that save's leftover files, e.g. a raw.bin.gz the new record does not reference, which read_raw() will still open.
(f) A Duplicate that runs out of space leaves a partial copy that is then listed as a roll.
The library's non-atomic save and orphan directories are already reported by the library audit; (a)-(f) are the parts not covered there.

**Evidence (from the code):**

```text
`tifffile.imwrite(path, np.ascontiguousarray(out), **kwargs)` and `with open(path, "wb") as fh: _write_builtin(...)`, and dng likewise `with open(path, "wb") as fh: _write(...)`. export._write_infrared catches the DNG failure and returns a note, but never removes the partial `companion`. library.save: `path.mkdir(parents=True, exist_ok=True)` reuses any existing directory whose scan.json is absent, and scan.json is `write_text` last. entries(): `except (OSError, json.JSONDecodeError): continue`. read_survey: `for number, path, record in walked_prescans(folder, manifest): image = tiff.read(str(path))`, while walked_prescans checks only `(folder / name).exists()`. No os.fsync anywhere in the tree.
```

**Failure scenario:** The GUI is killed while FrameWriter writes rolls/strip/prescan09.tif during a walk. survey.json (written before the file) lists prescan09. On restart, Rolls... then Open shows 'strip does not hold a roll this can read. truncated file: ...', and the other 35 prescans cannot be reached from the window.

**Fix:** Add one helper that writes to '<name>.part' (or a tempfile in the same directory), flushes and fsyncs, then os.replace, and use it in tiff.write, dng.write, the JPEG path, library.save (write the entry into '<id>.partial/' and rename the directory last), and every JSON manifest. On failure, unlink the partial file. Let read_survey skip (and report) a prescan that cannot be read instead of failing the whole walk. Have library.save refuse to reuse a directory that already contains files.

<details><summary>Second reader's check</summary>

tiff.write (tiff.py:164-168) and dng.write (dng.py:145-146) open the final path directly. export._write_infrared returns a note on failure and leaves the partial companion in place (export.py:146-153). library.save checks only `(path / "scan.json").exists()` before `mkdir(exist_ok=True)` (library.py:169-175). read_raw opens raw.bin.gz by existence alone, not from the record (library.py:401-406). walked_prescans checks only .exists() (session.py:659), and read_survey calls tiff.read without a guard (gui.py:4735). The built-in reader raises ValueError('truncated file') and tifffile raises TiffFileError, which is a ValueError, so open_roll refuses the whole walk. The Duplicate copytree (gui.py:2477-2481) leaves a partial copy on failure. The tree contains no fsync. Item (e) needs a failed save followed by a new save in the same UTC second with identical stock, frame and dpi, which is very narrow.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-9"></a>

### RX-9 -- In-place rewrites of complete library entries (migrate-raw/migrate-direction --write, uniformity 'redo') are not atomic; an interruption makes an entry vanish or destroys the only prescan.tif

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `tools/library.py:193-207`, `rps7200/library.py:563-577`, `rps7200/library.py:618-623`, `rps7200/library.py:633-635`, `tools/uniformity.py:640-646`

These tools rewrite files of entries that are already complete and correct, in place, by truncate-then-write:
- An interruption or ENOSPC while scan.json is rewritten turns a good entry into one entries() skips silently. It drops out of verify, reconstruct, the Rolls export and the demo, with nothing reporting it.
- A truncated prescan.tif from migrate-direction destroys the only copy of that framing pass. There are no raw bytes behind it, so nothing can rebuild it.
- migrate-direction interrupted between the scan.tif rewrite and the scan.json write leaves the new upright scan.tif with the old sha256. A rerun takes the 'recorded' branch, which never updates the sha, so verify reports a checksum mismatch forever.
- migrate-raw interrupted the same way leaves raw pixels under a record that still says corrections_applied ['shading'], so corrected() returns them as 'already' corrected.

**Evidence (from the code):**

```text
migrate-raw: `tiff.write(str(path / "scan.tif"), plain, ...)` and then `(path / "scan.json").write_text(json.dumps(record, ...))`. migrate_direction: `tiff.write(str(path / "prescan.tif"), np.ascontiguousarray(prescan[::-1]))`, where prescan.tif has no raw bytes of its own ('`prescan.tif` has no raw bytes of its own'). It later rewrites scan.json the same way. uniformity redo: `(entry / "scan.json").write_text(json.dumps(record, indent=2), ...)`. On a rerun, the 'already matches' branch does not refresh the checksum: `if plain and np.array_equal(stored, decoded): done.append(...); upright = decoded`.
```

**Failure scenario:** `tools/library.py migrate-direction --write` over 300 entries is killed by a laptop sleep or battery at entry 140 while writing its prescan.tif. That frame's framing pass is gone for good, and entry 140's scan.json may be truncated so the entry disappears from `list` and `verify`.

**Fix:** Use the same atomic write helper as RX-8 for every rewrite. Write the new data file and the new scan.json both as temporaries and replace them in one step. Rewrite prescan.tif only via temp+replace and keep a backup (prescan.orig.tif) until the record is written. Have migrate-direction refresh image.sha256 in every branch that asserts the stored image.

<details><summary>Second reader's check</summary>

migrate-raw rewrites scan.tif and then scan.json in place (tools/library.py:193-205). migrate_direction rewrites prescan.tif in place (library.py:622-623) and scan.json last (library.py:633-635). uniformity redo rewrites scan.json in place (tools/uniformity.py:640-646). The stale-sha case holds: if migrate_direction is interrupted after scan.tif is rewritten, read_direction is not yet set on the rerun, and stored == decoded takes the 'recorded' branch (library.py:563-565), which does not refresh image.sha256. One nuance for migrate-raw: if interrupted with 'shading' in corrections_applied, a rerun plans the entry again and repairs it. The permanent case is a mislabelled entry (applied == []), which a rerun skips as 'already raw' with a stale sha.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-10"></a>

### RX-10 -- A failed write of the calibration cache discards a successful 3-4 minute calibration and blocks all scanning; a truncated cache makes 'reuse' fail

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:612-629`, `rps7200/direct.py:563-570`, `rps7200/shading.py:75-91`, `rps7200/session.py:1381-1399`, `tools/gui.py:1946-1958`, `tools/scan_roll.py:166-173`, `tools/scan_roll.py:446`

The reference is measured and installed on the scanner object, and then writing the optional cache file (calibration/shading.npz, relative to the current directory) raises on ENOSPC, EACCES or EIO. ensure_shading propagates the error, so the GUI marks the session uncalibrated and refuses every scan until a calibration succeeds, even though the device-side reference is in memory. Each retry costs another 3-4 minutes of lamp and mechanism for the same failure. scan_roll.py and scan.py abort before scanning. A save interrupted part-way leaves a truncated npz. Later, 'reuse' (GUI mode or --reuse) raises BadZipFile or ValueError from load_shading and fails the job, and the operator must know to switch to 'measure'.

**Evidence (from the code):**

```text
`result = self.calibrate_shading()` (which sets self._shading), then `saved = self.save_shading(path)`, which reaches `np.savez_compressed(path, **arrays)` with no try. The GUI job: `except Exception: self._emit("calibrated", done=int(self.calibrated)); raise`. Every picture-taking control goes through `_calibration_missing`: `if self.calibrated: return False; self.ask_to_calibrate(parent); return True`. In the CLI roll, calibrate() raising ends the run with 'nothing was scanned, and nothing was written'.
```

**Failure scenario:** The disk is full, or calibration/ sits in a read-only checkout. The operator presses Calibrate, waits 4 minutes, and gets 'failed: OSError: [Errno 28] No space left on device'. Scan, Prescan and Roll then keep asking him to calibrate first, and every retry repeats the 4-minute calibration.

**Fix:** In ensure_shading, catch the cache write failure, log it, and still return 'calibrated' with path=None: the cache is a convenience, not the calibration. Write the cache atomically (RX-8). In load_shading, turn an unreadable cache into a clear 'cache corrupt, measure instead' message, and optionally fall back to measuring.

<details><summary>Second reader's check</summary>

ensure_shading calls calibrate_shading and then save_shading(path) with no try (direct.py:612-615). save_shading does mkdir plus np.savez_compressed (direct.py:563-570, shading.py:91). The GUI's _calibrate re-raises and emits calibrated=int(self.calibrated), which stays False on a first calibration (session.py:1381-1392). _calibration_missing then blocks every capture (gui.py:1946-1958). The CLI's calibrate() raising leads to 'nothing was scanned' (scan_roll.py:166-173, 446, 612-619). A truncated npz makes load_shading raise BadZipFile in 'reuse' mode.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-11"></a>

### RX-11 -- approved.json is rewritten whole with only this commission's ticked frames; on a resume it erases the earlier frames' turns and flips, which roll export uses; a truncated file reads as empty

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/gui.py:2929-2955`, `tools/gui.py:5818-5837`, `tools/gui.py:4949-4956`, `tools/gui.py:4885-4898`, `tools/gui.py:2487-2491`

On a resumed roll the frames already done are unticked by design, so the second commission's approved.json lists only the remaining frames. The earlier frames' per-frame rotation, flip, offset and source are overwritten. Exporting the roll afterwards orients those frames with the roll default instead of the operator's turn. Their library scan.json records rotation, but export ignores it. The file is also written by truncate-then-write on the UI thread, and a truncated file makes read_approved return nothing without any message. The separate problem that the file goes to rolls/_safe(name) instead of the roll's folder is already reported by the demo-parity audit.

**Evidence (from the code):**

```text
approved_from_sheet: `if number is None or number not in picked: continue`, with the note 'A turn on an unticked frame goes nowhere'. _write_approved replaces the file: `(folder / "approved.json").write_text(json.dumps({... "frames": [... for a in approved]}, ...))`. read_approved: `except (OSError, ValueError, AttributeError): return offsets, rotations, flips, entries, sources` (all empty). roll_exports: `rotation=rotations.get(number, turn), flipped=flips.get(number, mirrored)`. The delete dialog calls this file 'the one thing here the library cannot rebuild'.
```

**Failure scenario:** Frames 1-10 of a roll were individually rotated 180° on the sheet and scanned. The roll is resumed for frames 11-20 and approved.json is rewritten with 11-20 only. 'Export' of the roll then writes frames 1-10 the wrong way up, and the log reports nothing unusual.

**Fix:** Merge with the existing approved.json: keep records for frames not in this commission and replace those that are. Write it atomically. Have read_approved report an unreadable file instead of returning empty. Consider making export take each frame's orientation from its library entry's recorded rotation/flipped when approved.json has no record.

<details><summary>Second reader's check</summary>

approved_from_sheet skips unticked frames (gui.py:5819-5822). open_roll leaves the done frames unticked (dialog text at gui.py:2660-2662). _write_approved replaces the whole file (gui.py:2939-2955). roll_exports takes rotation and flip from read_approved with the roll default as fallback (gui.py:4885-4898). read_approved returns empty dicts on any parse error, with no message (gui.py:4949-4956). One related point: after a restart the resumed commission's approved.json goes to rolls/_safe(name field), usually rolls/roll, so the damage can also land in a different folder.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-12"></a>

### RX-12 -- Closing the window during a roll waits for the whole roll and every queued job, with no stop offered and no timeout; Save all/Export threads are killed mid-write on close

**Severity** medium · **Category** user-error · **Verdict** partly · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `tools/gui.py:3039-3068`, `tools/gui.py:3020-3021`, `rps7200/session.py:1246-1248`, `tools/gui.py:2431-2451`, `tools/gui.py:3787-3841`

Closing during a Roll waits for the whole roll and every job queued behind it, with no timeout. The quit dialog neither offers nor mentions stopping: the Stop button still works during 'closing ...', but nothing points to it, and the dialog talks about 'a scan'. That makes a kill from the task manager likely, which risks a wedge and leaves the partial files of RX-2, RX-6 and RX-8. Separately, on_close ignores a running Save all or Export, whose daemon thread is killed mid-write when the root is destroyed, leaving a truncated delivered file.

**Evidence (from the code):**

```text
on_close: `if self.busy and not messagebox.askokcancel("Quit", "A scan is still running. Quitting waits for it to finish -- abandoning it is what wedges the scanner.\n\nWait and quit?")`, then `self.session.shutdown()` (i.e. `self._jobs.put(None)` behind any queued jobs) and `_wait_to_quit`, which has 'no timeout on purpose'. No request_stop() is issued. on_close never looks at `self._saving`. The Save all/Export workers are `threading.Thread(target=run, daemon=True, ...)`, and `_quit` destroys the root as soon as the scanner thread ends.
```

**Failure scenario:** Frame 3 of a 36-frame roll is scanning. The operator closes the window meaning to stop. After 20 minutes of 'closing ...' he kills it from Task Manager during frame 7's read. The scanner needs a power cycle, roll.json may be truncated, and frames 5-6 were in the writer queue.

**Fix:** In on_close during a Roll, offer 'Stop after this frame and quit', which calls request_stop() and then shutdown(), and say plainly how long 'wait' means. Make on_close also wait for, or ask about, a running Save all/Export, or make those threads non-daemon and join them before destroy.

<details><summary>Second reader's check</summary>

on_close (gui.py:3039-3050) asks only 'Wait and quit?', then calls shutdown() (a None queued behind any pending jobs, session.py:1246-1248) and _wait_to_quit, which has no timeout. It never calls request_stop, never checks self._saving, and the Save all and Export workers are daemon threads (gui.py:2451, 3822-3841) that die when _quit destroys the root. The finding overstates one point: the window stays alive and polling during 'closing ...', and on_stop (gui.py:3020-3021) is not disabled, so the operator can still press Stop. The dialog just never says so, and it names 'a scan' when the wait covers the whole roll and every queued job.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-13"></a>

### RX-13 -- A full-resolution load starts for every pass shown, with no cap on concurrency, and each re-runs shading in float64; flipping through a 3600 dpi RGBI roll can queue many ~0.7 GB loads

**Severity** medium · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:3527-3539`, `tools/gui.py:4092-4120`, `rps7200/shading.py:241-281`, `rps7200/library.py:388`, `tools/gui.py:762-773`

Sizes from the code's own figures: at 3600 dpi RGBI a frame is 5172×~3440×4×uint16 ≈ 142 MB. library.corrected loads it (142 MB), copies it (142 MB), and per channel builds float64 planes of 142 MB each, two to three at a time, so peak is about 0.6-0.7 GB per load. The pyramid then keeps about +50 MB. At 7200 dpi RGBI a pass is about 569 MB; correction is refused there, so a load is about 0.6 GB plus the pyramid. Every arrow-key step starts another thread even while earlier ones are still reading and correcting, and their results are discarded. Holding the key over 38 filed 3600 dpi frames can have tens of these in flight, on top of a running roll (FrameWriter with 2 queued jobs of ~430 MB each plus one in progress, the scanner's current frame, and the stale last_raw and last_pixels_raw of the previous pass). An OOM kill here is a kill -9 in the middle of a roll, with all of RX-2/RX-6 and a likely wedge.

**Evidence (from the code):**

```text
`_show`: `if result.entry is not None and result.image is not None: self._load_full(result)`. The only guard in `_load_full` is `if self._loading == r.seq or self._levels_seq == r.seq: return`, then `threading.Thread(target=work, ...).start()`, so a different seq always starts a new thread and stale ones are never cancelled. apply_shading: `out = image.copy()`, then per channel `vals = (image[:, : loc.size, c].astype(np.float64) - dark) * gain` and `np.floor(vals + 0.5, out=vals)`, which keeps two to three H×W float64 temporaries alive at once. `_walk(1)` (the next-pass key) calls `_show_seq` → `_show`.
```

**Failure scenario:** On an 8 GB laptop during a 3600 dpi RGBI roll, the operator holds → to review the earlier frames. About 12 loads run at once, peak memory passes 8 GB, and the OS kills the process during frame 20's read.

**Fix:** Run a single full-resolution loader with a 'latest wins' slot: cancel or ignore superseded requests before they start work, and start the load after a short debounce. Do the correction per channel into the output dtype with in-place float32 arithmetic, or in row blocks, to bound the temporaries.

<details><summary>Second reader's check</summary>

_show calls _load_full for every result that has an entry (gui.py:3536-3537). _load_full only dedupes the same seq and starts a new daemon thread per seq, with no cancellation (gui.py:4100-4120). _loaded throws stale images away, but only after they have been fully read and corrected. apply_shading copies the image and builds float64 per-channel planes through astype, subtraction, multiplication and +0.5, several temporaries at once (shading.py:241-281). At 3600 dpi RGBI a plane is about 142 MB, so each load peaks around 0.6-0.7 GB, as claimed. I could not confirm the claim that correction is refused at 7200 dpi. The concurrency and memory argument does not depend on it.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-14"></a>

### RX-14 -- The GUI cannot join library entries of rolls scanned by tools/scan_roll.py: the frame key is 'roll/NN' there and 'roll-NN' in the window's index

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `tools/scan_roll.py:570-578`, `tools/gui.py:4979-5008`, `rps7200/session.py:1880-1882`, `tools/gui.py:2396-2405`

'2026-09-24/03' splits at the last '-' into ('2026-09', '24/03') and 'gold200/03' has no '-' at all, so every CLI-scanned frame is skipped. A roll scanned from the command line therefore shows no entries in Rolls..., and 'Export' says 'None of the frames ... has a library entry left' although all of them are filed. The CLI's roll.json holds the entry paths only when the run ended normally (RX-6), so after a crash nothing links the roll to its entries at all.

**Evidence (from the code):**

```text
CLI: `film=FilmNotes(..., frame=f"{roll_name}/{number:02d}", ...)`. GUI session: `frame=job.notes.frame or f"{name}-{number:02d}"`. roll_entry_index: `roll, _, number = frame.rpartition("-")` then `if not roll or not number.isdigit(): continue`. Its docstring says 'The join is on `film.frame`, which `ScanSession._file` sets to "{roll}-{NN}" for every roll frame'.
```

**Failure scenario:** `scan_roll.py --roll gold200 --frames 36` files 36 entries. In the GUI, Rolls... → Export gold200 answers 'None of the frames in gold200 has a library entry left'.

**Fix:** Use one frame-key format (a shared helper in session.py) for both front ends. Better still, store an explicit roll id and frame number in the library record (for example in tags or a 'roll' block that library.save keeps) and join on those instead of parsing a free-text field.

<details><summary>Second reader's check</summary>

The CLI files `frame=f"{roll_name}/{number:02d}"` (scan_roll.py:576). roll_entry_index does `frame.rpartition("-")` and skips any case where roll is empty or number is not a digit (gui.py:5004-5007). 'gold200/03' gives an empty roll and '2026-09-24/03' gives the number '24/03', so every CLI frame is skipped. The docstring (gui.py:4987-4988) says only the session's format is joined.

</details>

<a id="resource-exhaustion-crash-recovery-time-rxv-2"></a>

### RXV-2 -- The GUI's before-correction prescan is never filed with its bytes; the docstring's RPS7200_DEBUG fallback cannot happen because the session forces debug=False

**Severity** medium · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1841-1854`, `rps7200/session.py:2044-2059`, `rps7200/session.py:1266-1271`, `rps7200/direct.py:464-469`

file_entry=False is justified by a debug-mode entry that cannot exist in the GUI: the session passes debug=False explicitly, which overrides RPS7200_DEBUG. The picture of the frame as it arrived before aiming, which the code calls the only independent evidence of whether a correction helped, therefore survives only as a corrected, 8-bit rolls/<name>/prescanNN-before.tif, plus possibly an out_dir copy. It has no raw bytes, no CCD mask and no library entry. The CLI walk keeps it the same way (scan_roll.py:501-510), with no entry either.

**Evidence (from the code):**

```text
`self._file(seq, number, rf.prescan_before, ..., path=out / f"prescan{number:02d}-before.tif", roll=name, file_entry=False)`. The docstring says: 'Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway, filed at the instant it was taken'. But `_default_scanner` returns `DirectScanner(verbose=self.verbose, debug=False)`, and DirectScanner reads the env var only `if debug is None`.
```

**Failure scenario:** A walk with correction on moves frame 7, and later the question is whether the move was right. The only record of the pre-correction pass is an 8-bit shading-corrected TIFF that the current correction code cannot re-derive, and that a later walk into the same folder overwrites (RX-5).

**Fix:** Capture the pre-correction pass's own raw bytes, layout and mask in the driver when it is taken (as last_pixels_raw and last_raw are captured for other passes), carry them on the RollFrame, and file the before-prescan as its own entry with that capture. At minimum, fix the docstring so it does not claim a debug entry exists.

<a id="resource-exhaustion-crash-recovery-time-rxv-3"></a>

### RXV-3 -- verify cannot detect damage to shading.npz, ccd_mask.bin or prescan.tif: none has a checksum, and prescan.tif is not checked at all

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:219-296`, `rps7200/library.py:776-816`

Three of the five files that make an entry re-derivable are checked for existence only, or not at all. prescan.tif is the only copy of the frame's framing pass and has no raw bytes behind it. A truncated or bit-rotted shading.npz (np.load then raises BadZipFile inside library.corrected, which breaks the GUI view, Save As and Export) or a damaged ccd_mask.bin (a wrong column map and silently wrong correction) passes `make verify` as 'library is intact'. Power loss without fsync (RX-8) and an interrupted migrate-direction (RX-9) produce exactly this damage.

**Evidence (from the code):**

```text
The record stores `"sha256": _sha256(path / "scan.tif")` and the raw sha only. `calibration` holds `"shading": "shading.npz"` and `"ccd_mask": "ccd_mask.bin"` with no digest, and `prescan` holds file and direction only. verify: `for key in ("shading", "ccd_mask"): name = cal.get(key); if name and not (path / name).exists(): problems.append(...)`. prescan.tif is never looked at.
```

**Failure scenario:** After a power loss, an entry's shading.npz is zero-length and scan.json intact. `make verify` reports 'library is intact'. Export of that frame fails with 'could not export ... BadZipFile', and nothing ever pointed to the file.

**Fix:** Record the sha256 of shading.npz, ccd_mask.bin and prescan.tif in scan.json at save time. Have verify check each one and actually open shading.npz and prescan.tif. Add the missing digests to existing entries via a migrate step, noting they only certify from that point on.

<a id="resource-exhaustion-crash-recovery-time-rx-16"></a>

### RX-16 -- Settings save: rename-atomic against a kill, but not fsynced, shares a fixed .part name between windows, and fails silently, losing the only copy of uncommissioned sheet decisions

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/settings.py:82-97`, `tools/gui.py:668-697`, `tools/gui.py:2340-2350`

**Doc claim:** rps7200/settings.py:85-86 ('Written whole and replaced at the end, so an interrupted write leaves the previous settings rather than half of the new ones') -- true for a process kill, not for power loss without fsync

Against a process kill the write is atomic, since os.replace is atomic on POSIX and on NTFS. Neither the .part file nor the directory is fsynced, though, so after a power loss a filesystem without ext4's rename heuristics can leave a zero-length gui-settings.json. load() then returns defaults, and presets, shortcut overrides, the Rolls 'last opened' times and the 'sheet' section are lost. The 'sheet' section is the only record of contact-sheet decisions for a walk that has not been commissioned. Two windows writing one file (two real windows, or a real one with RPS7200_SETTINGS pointing at the same file) race on the same '.part' name and overwrite each other's whole payload. On Windows, replace fails with PermissionError while another process holds the file open. In every one of these cases the operator is told nothing.

**Evidence (from the code):**

```text
`temporary = target.with_name(target.name + ".part")`, `temporary.write_text(...)`, `temporary.replace(target)`, `except (OSError, TypeError, ValueError): return None`. The caller `_remember` discards the return value; its `except Exception as exc: self._say(f"could not save the settings: {exc}")` can never fire for an I/O failure, because save() already swallowed it. `_store_sheet_state` keeps sheet decisions only via `_remember()`.
```

**Failure scenario:** The disk is full, or the checkout is read-only. The operator positions 20 frames on the contact sheet and closes it without commissioning. save() returns None silently, and on the next launch the sheet reopens with no positions.

**Fix:** Check save()'s return value and say so in the log when it is None. Flush and fsync the .part file (and the directory on POSIX) before the replace. Use a unique temp name (tempfile.NamedTemporaryFile(dir=target.parent, delete=False)).

<details><summary>Second reader's check</summary>

settings.save writes '<name>.part' and then calls replace, with no fsync, and returns None on OSError, TypeError or ValueError (settings.py:88-97). _remember ignores the return value, and its except clause can only catch errors raised while gathering the values (gui.py:668-697). The fixed .part name is shared between processes. Against a process kill the rename is atomic, as the finding says.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-17"></a>

### RX-17 -- Save As writes on the UI thread with no error handling: a failure goes only to stderr, leaves a partial file, and an overwrite destroys the old file

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/gui.py:3754-3768`, `tools/gui.py:3843-3875`, `tools/gui.py:4092-4099`

Save As re-corrects a 3600 dpi pass at full resolution on the event thread, which freezes the window, the log pump and the progress display (a roll may be running). Any failure (ENOSPC, EACCES, an extension export.format_of rejects, an unreadable entry) goes to Tk's default report_callback_exception, i.e. a traceback on stderr, invisible when the window runs without a console. Nothing appears in the log. tiff/dng/export write the chosen path in place, so saving over an existing file and failing part-way destroys that file (RX-8).

**Evidence (from the code):**

```text
on_save_as calls `said = self._deliver_one(result, path, ...)` directly in the Tk callback with no try. _deliver_one calls `library.corrected(result.entry)` and `export.write(path, full, quality=quality)`. By contrast, _load_full's docstring says reading 142 MB here 'would freeze the window for seconds if it were read here', and on_save_all wraps each pass in `except Exception`.
```

**Failure scenario:** Save As over yesterday's frame07.tif on a nearly full USB stick. The window freezes, then nothing happens. frame07.tif is now truncated, and the log says nothing.

**Fix:** Run Save As on the same worker pattern as Save all (thread plus the _saves queue), wrap it in try/except with a log line, and write via temp+replace so an overwrite is all-or-nothing.

<details><summary>Second reader's check</summary>

on_save_as calls _deliver_one directly in the Tk callback with no try (gui.py:3754-3768). _deliver_one runs library.corrected and export.write (gui.py:3861-3866). An exception goes to Tk's report_callback_exception. The write lands in place on the chosen path. Also, _deliver_one never passes resolution= to export.write, so the full-resolution files have no DPI tag. That is minor and outside this area.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-18"></a>

### RX-18 -- Output-folder names are claimed when the job is queued, not when the file is written: a walk's corrected prescan and its 'before' prescan get the same name and the second overwrites the first

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:2067-2071`, `rps7200/session.py:2192-2204`, `rps7200/session.py:1817-1854`, `rps7200/session.py:2163-2164`

_unclaimed exists so that 'a frame rescanned after a failure would otherwise land on the file the first attempt wrote'. It is a check-then-use with the use deferred to another thread. With an output folder set, a corrected walk frame queues both prescans with the identical 'unclaimed' path in out_dir/prescans/. The writer then writes the corrected prescan and overwrites it with the pre-correction one, so the output folder shows the frame as it arrived, not as it was aimed. The same race applies to any two jobs that compute one name before either is written.

**Evidence (from the code):**

```text
_file: `paths.append(_unclaimed(where / self._out_name(number, meta, roll)))` runs on the scanner thread at submit time. _unclaimed tests `if not wanted.exists(): return wanted`. For a dry-run frame whose aim replaced the prescan, `_file(... rf.prescan ... kind="prescan", roll=name)` and then `_file(... rf.prescan_before ... kind="prescan", ... file_entry=False)` are queued back to back. Both call `_out_name(number, ...)` → `f"{_safe(roll)}_frame{number:02d}_{dpi}dpi{ir}{end}"` before the writer has created the first file.
```

**Failure scenario:** A walk with --out set and correction on moves frame 4. out/prescans/roll_frame04_300dpi.tif ends up holding the 'before' picture, and the corrected one is not in the output folder.

**Fix:** Resolve the unclaimed name on the writer thread immediately before writing, or reserve names in the session (a set of claimed paths). Give the 'before' prescan its own suffix (e.g. _before) in out_dir, as rolls/ already does.

<details><summary>Second reader's check</summary>

_unclaimed is resolved on the scanner thread inside _file (session.py:2067-2071). For a corrected walk frame, both prescans are queued back to back with kind='prescan', the same number and the same roll (session.py:1818-1854), so _out_name gives the same name. Neither file exists at submit time, so both get the same path. The writer then writes the corrected prescan first and the 'before' prescan over it. This only happens with an output folder set and an aim that moved the frame.

</details>

<a id="resource-exhaustion-crash-recovery-time-rx-19"></a>

### RX-19 -- Clocks: UTC entry ids stamped at filing time, local-date roll folders, zone-less local output names, no timestamps in the GUI's roll.json, an unreachable %H%M%S branch, and st_ctime read as 'created' on Linux

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/library.py:118-128`, `rps7200/library.py:166`, `rps7200/session.py:1634`, `rps7200/session.py:2163-2167`, `tools/scan_roll.py:291`, `tools/scan_roll.py:304`, `tools/gui.py:5012-5030`, `tools/gui.py:5211-5215`, `rps7200/session.py:1669-1748`

No code join uses a date: roll to entry is joined on film.frame, the roll list on folder names, duplicates on the 'created' ISO string. So the UTC/local split breaks nothing directly, and the real harm comes through the date-named default roll (RX-3, RX-5). Remaining issues:
- Entry ids are the filing time in UTC, not the capture time. In CEST, frames scanned 00:00-02:00 local sit under the previous day's prefix, beside a roll folder named for the new day.
- Single-scan output names are local time with no zone. They repeat in the DST fall-back hour (resolved only by _unclaimed's -2) and cannot be matched to library ids without knowing the offset.
- The GUI's roll.json records no time at all, so after a crash nothing says when each frame was taken.
- The `roll and not number` %H%M%S branch (no date) is unreachable: every roll frame has number ≥ 1 and single scans pass roll=''.
- folder_created falls back to st_ctime, which on Linux is the directory's last metadata change (every file added), so 'Created' moves for named rolls.

**Evidence (from the code):**

```text
library: `when = datetime.now(timezone.utc)` and `when.strftime("%Y%m%dT%H%M%SZ")`. session: `name = job.name or time.strftime("%Y-%m-%d")` (local) and `_out_name`: `if roll and number: ...`, `if roll: return f"{_safe(roll)}_{time.strftime('%H%M%S')}_..."`, `return f"{time.strftime('%Y%m%dT%H%M%S')}_..."`. folder_created: `time.mktime(time.strptime(folder.name[:10], "%Y-%m-%d"))`, else `getattr(status, "st_birthtime", 0) or status.st_ctime`. when(): `time.localtime(stamp)`. The GUI's roll manifest dict has no time field. The CLI writes UTC `started`/`finished`.
```

**Failure scenario:** A named roll is scanned 23:30-01:30 CEST on 24→25 Sep. Its folder has no date. Its entries are 20260924T2130Z…20260924T2330Z. The out_dir single scans are named 20260925T0015…, and roll.json has no times. Correlating a frame with its delivered file and its entry takes timezone arithmetic, and 'Created' on Linux shows the time of the last frame written.

**Fix:** Record the capture time (UTC ISO with offset) per pass in meta, in scan.json and in each roll.json frame record, and add started/finished to the GUI manifest. Name output files with an explicit zone or in UTC. Remove or fix the unreachable branch. Store a creation time in the manifest instead of reading st_ctime.

<details><summary>Second reader's check</summary>

entry_id uses datetime.now(timezone.utc) at filing time (library.py:118-128, 166). The roll folder name is local time (session.py:1634), and so is _out_name (session.py:2163-2167). The `if roll:` branch at session.py:2166 is unreachable: every _file call for a roll passes number=rf.index+1 >= 1, and the single-scan calls (session.py:1429, 1456) pass no roll. folder_created falls back to st_ctime (gui.py:5029). The GUI manifest dict (session.py:1669-1748) has no time field. One more loss the finding missed: the debug spool records `"captured": time.time()` (direct.py:672), but _debug_flush never passes it on, so a debug entry's 'created' is the flush time after close(), which can be long after the capture.

</details>

<a id="resource-exhaustion-crash-recovery-time-rxv-4"></a>

### RXV-4 -- Memory held per roll frame in RAM is unbounded by size: FrameWriter jobs keep pixels, raw pixels and raw bytes; scan.py brackets hold every pass until close

**Severity** low · **Category** design · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1032`, `rps7200/session.py:2105-2126`, `rps7200/direct.py:641-646`, `tools/scan.py:174-194`, `tools/scan.py:238-246`

CLAUDE.md says of the spooling design that 'Only paths and small metadata stay resident', but the roll path does not spool. Each in-flight FrameWriter job holds the delivered image, the raw pixels (a separate array whenever shading was applied) and the raw byte string. That is 3 jobs (1 writing, 2 queued) at up to about 1.7 GB each at 7200 dpi RGBI, or about 430 MB at 3600 dpi, on top of the pass being read and the GUI's full-resolution loads (RX-13). tools/scan.py --bracket holds every pass's raw pixels and raw bytes until the device closes, about 1.1 GB or more per 7200 dpi RGBI pass. An OOM kill here is a kill -9 with the device open.

**Evidence (from the code):**

```text
`queue.Queue(maxsize=depth)` with depth 2; each job carries `image=image, raw_image=raw_image, ... capture=capture`, where capture_record() returns `"raw": self.last_raw` (the full bytes). scan.py: `pending.append(dict(capture, inquiry=info, meta=meta, image=image if raw is None else raw))`, filed only after the `with` block.
```

**Failure scenario:** A 5-pass bracket at 7200 dpi RGBI on a 8 GB machine holds about 5.5 GB of pending passes before filing starts. The process is killed mid-read of pass 5. All passes are lost and the read is abandoned.

**Fix:** Spool FrameWriter jobs and bracket passes to disk as the debug path does, with a plain sequential write off the scan thread, or cap in-flight bytes rather than job count. Correct CLAUDE.md so it says which paths spool.

<a id="resource-exhaustion-crash-recovery-time-rxv-5"></a>

### RXV-5 -- A truncated demo/pictures.npz crashes the demo's signing thread for good (BadZipFile is not caught), silently emptying the second-roll picture pool

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `rps7200/demo.py:1069-1096`, `rps7200/demo.py:286-290`

The cache is written in place by a daemon thread, which is killed mid-write if the demo window is closed while it runs. The next launch's np.load raises BadZipFile, which escapes the except clause and ends the thread before any signature is stored or the cache rewritten. _pictures_for then joins the dead thread and finds self._signatures empty on every launch until someone deletes the file. The demo quietly stops choosing pictures for a second roll, and nothing says why.

**Evidence (from the code):**

```text
`with np.load(self.cache) as data: ...` / `except (OSError, ValueError, KeyError): known = {}`. zipfile.BadZipFile derives from Exception only. The writer is `np.savez(self.cache, ...)` directly to the final path, on the daemon thread `threading.Thread(target=self._sign_pictures, daemon=True, name="demo-pictures")`.
```

**Failure scenario:** The demo is quit during its first signing pass. On every later `make run-demo`, a traceback from 'demo-pictures' goes to stderr, and the demo's second roll has no pictures to draw on.

**Fix:** Catch Exception (or add zipfile.BadZipFile) around the load and treat it as an empty cache. Write the cache to a temporary file and os.replace it.

<a id="resource-exhaustion-crash-recovery-time-rx-20"></a>

### RX-20 -- Crash-state matrix: what a kill -9 or power loss leaves in a GUI roll and a CLI roll, and what resume, verify and reconstruct do with each; no tool recovers orphans

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/session.py:1060-1103`, `rps7200/session.py:1924-1926`, `rps7200/library.py:169-185`, `rps7200/library.py:741-750`, `rps7200/direct.py:666-692`, `tools/library.py:44-46`, `tools/scan_roll.py:326-329`, `tools/scan_roll.py:605-610`

GUI roll killed mid-run (the session has debug=False):
(1) rolls/<name>/roll.json is either the last complete rewrite, whose frames include up to 3 marked done but unwritten (RX-6), or empty/partial if the kill hit the write (RX-2).
(2) frameNN.tif and the out_dir copy of the frame being written are partial. Queued frames have no files, and the frame being scanned is lost with an abandoned read, so the scanner probably needs a power cycle.
(3) library/<UTC-id>_…/ for the frame being filed holds some of scan.tif, prescan.tif, shading.npz, ccd_mask.bin and a partial raw.bin.gz, with no scan.json or a truncated one. index.json may be truncated (harmless: nothing reads it, and the next save rewrites it).
(4) A walk's survey.json may list a prescanNN.tif that is missing (skipped on reopen) or partial (reopen refused, RX-8).
(5) gui-settings.json is intact (rename). calibration/shading.npz is intact unless the kill hit its save.
CLI roll killed: roll.json has every frame with 'file' set and 'entry': None, the queued frames are lost but listed, and there is the same orphan entry directory. After Ctrl-C the queue is lost too (already reported).
Ad-hoc scripts with RPS7200_DEBUG=1: $TMP/rps7200-debug-XXXX/NNN-image.npy and NNN-raw.bin remain, with their meta gone (already reported).
What the tools do:
- resume (GUI) merges a readable roll.json, silently discards an unreadable one, and skips frames marked done (RX-2, RX-6). It also lands in the wrong folder unless the name is retyped (RX-3).
- resume (CLI) always overwrites (RX-4).
- verify, reconstruct, list, duplicates, roll_entry_index and the demo all go through entries() or the same glob, so an orphan directory or a truncated scan.json is invisible to every one of them. Nothing reports it, deletes it or reclaims its space.
- No tool can recover an orphan entry: its meta, layout and film notes died with the process, so even a complete raw.bin.gz cannot be decoded without guessing the layout. Nothing recovers an orphaned spool.
The orphan directory and the orphaned spool are already reported by the library and decode audits. This entry is the end-to-end inventory the audit asked for.

**Evidence (from the code):**

```text
library.entries: `for candidate in sorted(root.glob("*/scan.json")): try: ... except (OSError, json.JSONDecodeError): continue`. tools/library.py actions: `choices=["list", "verify", "reconstruct", "reindex", "duplicates", "migrate-raw", "migrate-direction"]`; none lists or repairs directories without a readable scan.json. The debug spool is created with `tempfile.mkdtemp(prefix="rps7200-debug-")`, and its meta lives only in `self._debug_pending`.
```

**Failure scenario:** After power loss 2 hours into a roll, the library holds one extra directory of ~350 MB with no scan.json. `make verify` says 'library is intact', `make reconstruct` reports nothing for it, and Rolls... may hide the roll or offer a resume that skips three missing frames.

**Fix:** Add `tools/library.py orphans [--delete]` to list directories under the root that have no readable scan.json (with size and file inventory), and have verify count them. Write the entry's meta (scan.json) first into the staging directory of RX-8 so an interrupted entry is at least self-describing. Add a 'check roll' step to open_roll that compares roll.json 'done' against the files and entries that actually exist.

<details><summary>Second reader's check</summary>

The inventory checks out against the code. The save order is scan.tif, prescan.tif, shading.npz, ccd_mask.bin, raw.bin.gz, then scan.json (library.py:178-308). entries() skips anything unreadable (library.py:745-749), and verify, reconstruct, list, duplicates and roll_entry_index all rely on it or the same glob. tools/library.py offers no orphan action (tools/library.py:44-46). The debug spool meta lives only in memory. The layout that raw.bin.gz needs is recorded only in scan.json, so an orphan cannot be decoded. index.json is read by no code. One addition: with RPS7200_DEBUG=1, a library.save failure (ENOSPC) during _debug_flush deletes the spool anyway (see additional finding RXV-1). The spool therefore does not survive the most common failure, not only a crash.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry pixels | <library>/<YYYYMMDDTHHMMSSZ(UTC, filing time)>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF, uint8/uint16 (H,W[,C]); deflate+predictor via tifffile or uncompressed built-in; classic TIFF 32-bit offsets | raw decode normally; corrected on GUI single Scan and in the demo (reported elsewhere); 7200 dpi realigned | library.save (library.py:179) from FrameWriter._write (session.py:1090), DirectScanner._debug_flush (direct.py:719), tools/scan.py:239, tools/uniformity.py:609; rewritten in place by migrate-raw (tools/library.py:198) and migrate-direction (library.py:573) | library.load/corrected/reconstruct/migrate_*, verify (sha), GUI _load_full/_deliver_one, demo | Lossless for what it was handed. Non-atomic: written in place, no fsync; partial on kill/ENOSPC |
| Raw bytes of the image pass | <entry>/raw.bin.gz | gzip level 6 of the concatenated READ payloads; sha256 and byte count of the uncompressed data in scan.json | raw (exact bytes received) | library.save (library.py:192-211) from `raw` bytes or the streamed `raw_path` spool file | library.read_raw/decode_raw/reconstruct/migrate_direction, verify | Byte-exact when complete. A partial gzip after a crash stays in an orphan directory; read_raw returns None for it |
| Shading reference used for the pass | <entry>/shading.npz and calibration/shading.npz (session cache, relative to CWD) | np.savez_compressed: float64 per-column ref/dark arrays, float64 means, pixels_per_line, channel lists | derived (calculate_shading output), not calibration bytes | ShadingReference.save (shading.py:91) via library.save and DirectScanner.save_shading/ensure_shading (direct.py:563-615) | library.load/corrected/reconstruct, DirectScanner.load_shading (reuse) | Exact float64 of the derived reference. Non-atomic; a truncated cache breaks 'reuse' and a failed cache write aborts the calibration (RX-10) |
| CCD mask of the pass | <entry>/ccd_mask.bin | raw bytes from GET CCD MASK | raw | library.save (library.py:185) | library.load/corrected/reconstruct | Byte-exact; non-atomic |
| Framing prescan stored with a frame | <entry>/prescan.tif | TIFF uint8 (H,W,3) | corrected (and oriented as delivered); no bytes of its own | library.save (library.py:181); rewritten in place by migrate_direction (library.py:622) | migrate_direction, demo, analysis tools | Lossless of the corrected 8-bit pass; the only copy, and an in-place rewrite can destroy it (RX-9) |
| Entry record | <entry>/scan.json | JSON: whitelisted meta keys, sha256s, layout, calibration report (light_mean rounded to 0.1), film notes, tags, provenance; `created` = UTC filing time to the second | metadata | library.save (library.py:308), written last; rewritten in place by migrate-raw, migrate-direction, uniformity 'redo' | library.entries (glob */scan.json, unreadable ones skipped), load, reconstruct, verify, gui roll_entry_index, demo | Not atomic, no fsync; a truncated or empty file makes the entry invisible everywhere |
| Library index | <library>/index.json | JSON summary list | derived | library.reindex after every save, delete and migrate | nothing in code (human convenience) | Rebuildable; non-atomic |
| Capture-time rejection marker | <entry>/REJECTED | text | metadata | tools/uniformity.py:640 | uniformity analysis (via the 'rejected' tag) | n/a |
| Roll progress manifest | <rolls>/<job.name or local YYYY-MM-DD>/roll.json (GUI); rolls/<--roll or local date>/roll.json or --out (CLI) | JSON: settings, wanted, per-frame records (number, index, transport_position, registration, error, done, exposure/gain/offset). CLI: file/entry/shape plus UTC started/finished; the GUI has no timestamps | metadata | ScanSession._roll per frame on the scanner thread (session.py:1924); scan_roll.py checkpoint() per frame (scan_roll.py:326-329, 585, 628) | ScanSession._roll resume merge (session.py:1650-1668); gui read_survey, roll_summary, scanned_frames, wanted_frames; scan_roll hold_from_walk as fallback | Truncate-then-write, no fsync; 'done' is written before filing (RX-6); the CLI never merges (RX-4) |
| Walk manifest | <rolls>/<name or local date>/survey.json | JSON like roll.json plus a per-frame `prescan` file name | metadata | ScanSession._roll dry run (session.py:1924); scan_roll.py --dry-run checkpoint | gui read_survey/roll_summary/_walk_shift; walked_prescans; scan_roll hold_from_walk | Replaced by each walk into the same folder (RX-5); non-atomic |
| Walk reference prescans | <rolls>/<name>/prescanNN.tif, prescanNN-before.tif | TIFF uint8 (H,W,3), turned by the session rotation | corrected, oriented | FrameWriter via export.write (GUI, session.py:1817-1854); tiff.write synchronously on the scanner thread (CLI, scan_roll.py:499-510) | gui read_survey (unoriented), walked_prescans, scan_roll hold_from_walk, the demo sheet | Lossless of corrected 8-bit; overwritten in place by a later walk; a partial file blocks reopening |
| Delivered roll frames | <rolls>/<name>/frameNN.tif | TIFF uint16/uint8, possibly 1-channel mono | corrected, oriented, possibly mono | FrameWriter._write → export.write (session.py:1079), written before library.save | operator/NegPy only | Lossless of corrected; overwritten in place by a rescan; partial on crash |
| Commissioned positions and turns | <rolls>/<_safe(name field) or 'roll'>/approved.json | JSON: roll, numbering, frames[{number, offset_mm(rounded to 4 dp), rotation, flipped, reference_entry, source}] | operator decisions | ScannerGui._write_approved on the UI thread (gui.py:2929-2955) | gui read_approved (open_roll, roll_exports, delete dialog) | Replaced with only this commission's frames (RX-11); non-atomic; its folder can differ from the roll's (reported elsewhere) |
| Output-folder copies | <out>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng), <out>/prescans/<roll>_frameNN_..., <out>/<local YYYYmmddTHHMMSS>_<dpi>dpi[_ir].<ext> for single passes, -N on clash | TIFF, or JPEG 8-bit plus an uncompressed 4-sample LinearRaw DNG for RGBI | corrected, oriented, possibly mono; JPEG is lossy (8-bit) | FrameWriter._write via export.write; names from _out_name/_unclaimed at submit time | operator/NegPy | Partial files survive failures (the DNG after its 'could not be written' note); name claimed before writing (RX-18) |
| Save As / Save all / Export files | <operator-chosen path>, <folder>/<kind>NN_<dpi>dpi..., <folder>/<roll>_<kind>NN_... | TIFF/JPEG(+DNG) | corrected at full resolution from library.corrected, or the 1400-px preview when not yet filed | _deliver_one (gui.py:3843-3886): Save As on the UI thread, others on daemon threads | operator | Overwrites in place; killed mid-write when the window closes (RX-12) |
| Window settings and sheet fallback | gui-settings.json (CWD) or $RPS7200_SETTINGS or --settings, demo/gui-settings.json under --demo; temp gui-settings.json.part | JSON: controls, film, output, window, presets, shortcuts, rolls, sheet | UI state | settings.save via ScannerGui._remember (gui.py:668-697) | settings.load at startup | Temp+replace (kill-safe); no fsync; silent failure (RX-16) |
| Debug spool | $TMP/rps7200-debug-XXXXXX/NNN-image.npy, NNN-raw.bin | .npy of raw_pixels; raw bytes from last_raw | raw (but last_raw can be stale; reported elsewhere) | DirectScanner._debug_capture (direct.py:666-692) | DirectScanner._debug_flush after close() → library.save, then unlinked | Meta held only in memory; orphaned forever after a crash |
| Single-scan CLI outputs | tools/scan.py --out <file>.tif\|.jpg plus <file>.json | delivered image plus a JSON dump of the full meta | corrected (and bracket-merged) | tools/scan.py:278-281, after all library saves | operator | Never written if any library.save before it raises |
| Demo picture signatures | demo/pictures.npz | np.savez: paths, signatures | derived cache | DemoScanner._sign_pictures (demo.py:1090-1096) | DemoScanner._sign_pictures | Non-atomic (already reported) |

**Second reader's corrections to this table:**

- Library entry pixels (scan.tif) on a GUI single Scan: the pixels are not just 'corrected (reported elsewhere)', they are mislabelled. _scan calls _file with no raw_image (session.py:1456), so FrameWriter files the corrected image with corrections_applied [] (library.py:229). library.corrected() then applies shading a second time.
- shading.npz, ccd_mask.bin, prescan.tif: the record stores no checksum for any of them, and verify checks only that shading/ccd_mask exist and never looks at prescan.tif (library.py:789-793). Their 'exact' column should say 'unverifiable after write'.
- scan.json 'created' is the filing time. For debug-filed entries that is the flush after close(), and the spool's own 'captured' timestamp (direct.py:672) is discarded.
- Debug spool: besides being orphaned after a crash, it is deleted even when library.save fails (direct.py:733-752, 758-760), so it does not survive a disk-full filing failure.
- Walk reference prescans: for CLI walks (scan_roll.py --dry-run) prescanNN.tif and prescanNN-before.tif are the only copy. No library entry exists (RX-15), and they are written synchronously on the scanner thread with the device open, not by FrameWriter.
- prescanNN-before.tif (GUI) is written with file_entry=False, so it never has a library entry. The RPS7200_DEBUG fallback its docstring relies on cannot happen, because the session forces debug=False (session.py:1270).
- Roll progress manifest: the GUI roll.json records no 'file' and no 'entry' per frame. The CLI roll.json fills 'entry' only after a normal writer.finish(). A CLI 'frame' key in the library is '<roll>/NN', which the GUI cannot join (RX-14).
- approved.json path: rolls/_safe(<name field>), which is rolls/roll when the field is blank, as after any restart. It is not necessarily the roll's folder.
- demo/pictures.npz: a truncated file is fatal to the signing thread, because BadZipFile is not caught (RXV-5). It is not just non-atomic.
- index.json: confirmed that no code reads it.

## What the operator can do

- Start a roll, walk or single scan with no check that rolls/, library/, the output folder or $TMP has room. At 3600 dpi RGBI a roll frame writes roughly 340 MB (frameNN.tif + scan.tif + raw.bin.gz), plus 120-142 MB per output copy.
- Name a roll anything in the GUI box or with --roll. The text becomes a folder path unsanitised (only approved.json and out_dir names go through _safe).
- Leave the roll name blank. The folder is then today's local date, shared by every unnamed walk and roll that day.
- Reopen a roll from Rolls... and press 'Scan chosen frames'. The destination is whatever the roll-name box holds at that moment, not the reopened folder.
- Resume a CLI roll with scan_roll.py --roll X --start-at N. This replaces roll.json.
- Press Stop (cooperative, after the frame in flight) or Force abort (type ABORT: closes the transport under a read).
- Close the window while a roll runs. It waits for the whole roll and every queued job.
- Save As, Save all and Export rolls while a roll is running. All three re-correct at full resolution; Save As does it on the UI thread.
- Flip through passes with the keyboard while a roll runs. Each step starts a full-resolution load thread.
- Delete library entries from the GUI, delete, duplicate or rename roll folders (refused only while busy or while that roll is open), and run duplicates --delete or migrate-raw/migrate-direction --write.
- Point --library/--rolls/--out/--settings (or RPS7200_SETTINGS/RPS7200_DEBUG_ROOT) at removable drives or network shares. All defaults are relative to the current directory.

## What the operator should not do

- Do not start a long roll without checking free space on every destination. Nothing stops the roll when filing fails.
- Do not kill the process, cut power or close the terminal during a roll. It abandons a read (wedge) and leaves partial roll.json, frame TIFFs and orphan library directories.
- Do not walk two strips on the same day without naming the roll. The second walk overwrites the first walk's survey.json and prescanNN.tif.
- Do not resume a reopened roll without retyping its exact name in the roll box. Otherwise it goes to a new folder, on a new day to today's date.
- Do not use '/', '\', '..', ':', '*', '?', '"', '<', '>', '|', or CON/PRN/AUX/NUL/COMn/LPTn in roll names.
- Do not resume a CLI roll into the same --roll folder with --start-at while the old roll.json is still needed. Copy it aside first.
- Do not interrupt migrate-raw/migrate-direction --write, or uniformity's redo prompt, while they rewrite an entry.
- Do not close the window while Save all or Export is running.
- Do not hold the next/previous-pass key through a 3600/7200 dpi session on a low-memory machine while scanning.
- Do not type a value into the Film 'frame' field before a roll. Every frame then shares one film.frame, which breaks the roll-to-entry join and makes all frames one 'duplicate' signature.
- Do not run two windows against the same gui-settings.json.

## Mistakes nothing guards against

- A full disk during a roll: every later frame is scanned and discarded, shown only as log lines in the GUI and not at all in the CLI until the end, and roll.json marks them done.
- A kill or ENOSPC during the per-frame roll.json rewrite: the roll disappears from Rolls..., cannot be reopened even with its walk intact, and the next resume overwrites it silently.
- The documented GUI resume flow with an empty roll-name box writes to rolls/<today>, splitting the roll's files, manifest and library keys across two folders.
- A same-day unnamed second walk destroys the first walk's reference prescans and survey, and export can then mix two strips' frames.
- A roll name of '..' or an absolute path writes the roll outside rolls/. A Windows-illegal name fails only after the film has been wound to the first frame.
- scan_roll.py --start-at N erases the earlier frames' records from roll.json, although README says it carries them forward.
- A resumed commission rewrites approved.json with only the new frames, so exports orient the earlier frames by the roll default.
- An unwritable or full calibration/ directory discards each successful calibration, and the GUI then refuses every scan as uncalibrated.
- Save As over an existing file on a full or failing disk truncates the old file, and the error appears only on stderr.
- Closing the window mid-roll gives no stop option. It waits hours, which invites a hard kill.
- A CLI dry-run walk leaves nothing in the library: raw prescan bytes are never kept.
- Rolls scanned with tools/scan_roll.py are never matched to their library entries by the GUI, so Export reports no entries.

## Dataflow notes

How data enters, is transformed and leaves this area, with the failure behaviour at each hop.

1. Pixels and bytes. DirectScanner.read_planes (direct.py:1474-1533) collects READ chunks, joins them into one blob (chunks and blob are briefly both resident; at 7200 dpi RGBI that is ~570 MB each), stores it in self.last_raw with its layout when keep_raw, and decodes via decode_index. scan() (direct.py:2678-2824) realigns at 7200 dpi, keeps raw_pixels, applies apply_shading (shading.py:241-281: image.copy() plus float64 per-channel temporaries) and returns the corrected image plus meta. It sets last_pixels_raw and last_scan_meta, and calls _debug_capture (direct.py:650-692), which with debug on spools NNN-image.npy and NNN-raw.bin to tempfile.mkdtemp. Failures there are logged and swallowed.

2. Debug filing. close() (direct.py:777-788) closes the transport, then _debug_flush (694-768) runs library.save for each spooled item and unlinks the spool in a finally, whether or not the save succeeded. A crash before close() orphans the spool with its meta lost.

3. GUI session. The ScanSession worker (session.py:1293-1346) owns the device. For each job, _deliver (1945-1971) emits a ≤1400-px copy to the UI event queue. _file (2031-2141) reads capture_record(), runs the shape guard, claims out_dir names with _unclaimed at submit time, and puts a job on FrameWriter.queue (maxsize 2, 1032-1033). This blocks the scanner thread only if writes fall behind.
   The writer thread (_write, 1060-1103) turns and monos the image, runs export.write for each path (rolls/<name>/frameNN.tif, out_dir copies; TIFF, or JPEG+DNG), then library.save(raw_image or image, meta, **capture). Failures become errors[] and a 'log' event via _filed (1348-1360), with no effect on the roll. On success a 'filed' event gives the GUI the entry path, and _show/_load_full (gui.py:3527-3539, 4092-4120) then starts a thread per shown pass that runs library.corrected.
   Order: frame files first, then the library directory: scan.tif → prescan.tif → shading.npz → ccd_mask.bin → raw.bin.gz → scan.json → index.json (library.py:176-310). Everything is truncate-then-write with no fsync.
   Shutdown: the None sentinel is queued behind every job; the scanner closes, then writer.finish() drains and joins.

4. Roll manifest (GUI). _roll (session.py:1608-1941) seeks first, then computes name = job.name or local date and out = rolls/name (no _safe), mkdirs it, reads and merges an existing roll.json (an unreadable one becomes {}), and builds the manifest. Per yielded RollFrame it queues the files, then appends a record with done computed from the RollFrame (not from filing), and rewrites roll.json or survey.json with write_text on the scanner thread (1924). A dry run never merges and overwrites prescanNN.tif in place.

5. Roll manifest (CLI). scan_roll.main (scan_roll.py:264-638) builds a fresh manifest, then seek → calibrate → mkdir → checkpoint(). That first checkpoint overwrites any earlier roll.json.
   - Per frame: a dry run tiff.writes the prescan synchronously and files nothing in the library. A scan submits to a FrameWriter with no on_done, then records file=frameNN.tif and checkpoints.
   - After the with-block: writer.finish(), then 'entry' is filled into each record, then a final checkpoint() outside any try.
   - KeyboardInterrupt bypasses finish (already reported).

6. Operator decisions. approved.json is written by gui._write_approved (gui.py:2929-2955) on the UI thread before the Roll is submitted, whole and holding only the ticked frames, to rolls/_safe(name field). It is read by read_approved for open_roll, export and delete. The contact sheet state is kept in gui-settings.json 'sheet' via _store_sheet_state → _remember → settings.save (temp .part plus os.replace; failures return None and are ignored).

7. Calibration cache. ensure_shading (direct.py:572-629) runs calibrate_shading, which sets _shading, then save_shading → np.savez_compressed to calibration/shading.npz. A save failure raises out of the job even though the reference was set.

8. Readers after a crash:
   - library.entries (library.py:741-750) globs */scan.json and skips unreadable ones. verify, reconstruct, duplicates and reindex are built on it; gui.roll_entry_index uses the same glob and joins on film.frame split at the last '-'. Orphan directories and truncated records are therefore invisible to all of them.
   - gui.roll_summary and read_survey read survey.json/roll.json. Either file unreadable → the roll is hidden (summary None) or refused (read_survey raises). A truncated prescanNN.tif also makes read_survey refuse the whole walk.
   - scanned_frames trusts 'done', or for CLI manifests the absence of error.

9. Clocks:
   - library entry id and created: datetime.now(UTC) at filing (library.py:166).
   - GUI and CLI default roll name: local date (session.py:1634, scan_roll.py:291).
   - out_dir single-scan names: local time without zone (session.py:2167). The %H%M%S-only branch (2166) is unreachable.
   - GUI roll list: localtime (gui.py:5215); folder_created parses the name as local midnight or falls back to st_birthtime/st_ctime.
   - CLI manifest: UTC started/finished. GUI manifest: no time.
   No join is keyed on a date. Midnight and DST effects arrive only through the date-named default folder and the unrestored name field.

10. Demo. DemoScanner stands below the seam and uses every writer above unchanged: FrameWriter, library.save, the manifests, approved.json and settings, under demo/ by default. Every failure mode here applies to it equally. Its own pictures.npz cache is written non-atomically (already reported).
