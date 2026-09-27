# Every writer: atomicity, crashes, full disks (gap pass)

Area key `crash-recovery-atomicity-inventory`. 14 findings: 1 high, 5 medium, 7 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

This area covers every writer that puts something on disk, checked for atomicity, fsync, what a kill or a full disk leaves behind, and what the next launch does about it. Judged from the code at 03aacba and later.


**How the writers behave today**
- Three things are written as temp file plus os.replace: a library entry's scan.json (`_write_atomic`, with fsync), the roll manifests survey.json, roll.json and approved.json (`write_manifest`, with fsync, and it deletes its temp file when it fails), and the calibration cache (`save_shading`).
- gui-settings.json is also temp plus replace, but without fsync.
- `compact`, `_replace_tiff` and migrate-raw swap files in with os.replace.
- Everything else is written in place:
  - each entry's scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin(.gz). These sit inside a freshly reserved directory behind an INCOMPLETE marker.
  - index.json
  - the whole calibration archive (data.bin, ccd_mask.bin, shading.npz, calibration.json), with no marker
  - the debug spool
  - every roll-folder TIFF (prescanNN.tif, prescanNN-before.tif, frameNN.tif)
  - every delivered copy and DNG
  - the demo signature cache
  - uniformity's REJECTED marker and its scan.json rewrite
  - the .bak and .legacy manifest copies (copyfile)
- No directory is ever fsynced.
- `_write_atomic`, `_replace_tiff`, `compact`, `save_shading` and `settings.save` all leave their `.part` file behind when they fail. Only `write_manifest` deletes its own.

**What happens on the next launch**
- Neither the window nor any tool checks anything at startup.
- `tools/library.py verify` (= `make verify`) reports only INCOMPLETE directories, directories with no scan.json, and checksum problems.
- Nothing on disk reports or repairs any of these:
  - `.part` leftovers inside entries (a failed compact can leave a `.raw.bin.gz.part` of hundreds of MB)
  - orphan `rps7200-debug-*` spools in the OS temp directory
  - calibration/<UTC>/ folders without calibration.json
  - plain entries that were never compacted
- No free-space check exists anywhere in rps7200/ or tools/: no shutil.disk_usage, no statvfs.

**New in this area**
- A roll or walk runs into a full disk with no warning beforehand, and each failure leaves more partial files behind.
- A window walk writes survey.json naming prescanNN.tif before the writer has written that file. After a failed filing or a kill, a reopened sheet shows the previous walk's picture under that name.
- Contact-sheet decisions reach disk only when the sheet closes, so a crash while it is open loses them. A settings save that fails at quit is reported into the window that is being destroyed.
- Duplicate roll is a copytree on the UI thread. A failure or a force-quit leaves a partial copy that is listed as a roll.
- A delivered copy that fails part-way stays under its final name, and the next export or rescan puts the good file at `-2`.
- An INCOMPLETE entry cannot be completed, because nothing describing the pass reaches disk before the last step.
- `compact()` never compresses a plain entry that has no raw.bin.
- The `.legacy` numbering copy is non-atomic and is never redone once a truncated copy exists.
- `mkdir(parents=True)` on every output root can recreate an unmounted drive's path on the system disk. The README says a gone folder stops the roll; in that case it does not.

Many related atomicity defects were already reported by other areas and are not repeated here: CSA-06, CSA-10, CSA-11, CSA-12, CSA-16, CSA-17, LIB-04, LIB-11, LIB-15, DBG-2, DBG-8, DOC-07, DEMO-09, SR-08, OUT-09, OUT-16, GUI2-23, CLI-29, T-07.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [CRA-A1](#crash-recovery-atomicity-inventory-cra-a1) | high | data-integrity | found-by-verifier | debug_claim marks a pass as filed when it is only queued; the debug flush at close() then deletes the spool copy of a pass whose library.save failed, and close runs before the writer has finished |
| [CRA-01](#crash-recovery-atomicity-inventory-cra-01) | medium | error-handling | confirmed | No free-space check anywhere: a roll, bracket, single scan, compaction or debug spool runs into a full disk after the scanner time is spent, and every failure leaves more partial files |
| [CRA-02](#crash-recovery-atomicity-inventory-cra-02) | medium | error-handling | confirmed | Nothing on the next launch, and nothing in `verify`, finds what a crash or full disk leaves: `.part` temps (up to hundreds of MB), orphan spools, half calibration archives and plain entries are invisible |
| [CRA-03](#crash-recovery-atomicity-inventory-cra-03) | medium | data-integrity | confirmed | A window walk writes survey.json naming prescanNN.tif before the writer has written it; a failed filing or kill leaves the survey pointing at a missing file or at the previous walk's picture |
| [CRA-04](#crash-recovery-atomicity-inventory-cra-04) | medium | data-integrity | confirmed | Contact-sheet decisions reach disk only when the sheet closes; a crash or kill while it is open loses them, and a save that fails at quit is reported into the window being destroyed |
| [CRA-05](#crash-recovery-atomicity-inventory-cra-05) | medium | data-integrity | confirmed | Duplicate roll is a copytree on the UI thread; a failure or force-quit leaves a partial copy that lists as a roll, and Delete's rmtree can half-delete one |
| [CRA-06](#crash-recovery-atomicity-inventory-cra-06) | low | data-integrity | confirmed | A delivered copy that fails or is interrupted part-way stays under its final name; the next export or rescan routes the good copy to `-2` |
| [CRA-07](#crash-recovery-atomicity-inventory-cra-07) | low | data-integrity | confirmed | An INCOMPLETE entry cannot be completed: nothing describing the pass (layout, meta, commands) reaches disk before the last step, and the marker says nothing about which pass it was |
| [CRA-08](#crash-recovery-atomicity-inventory-cra-08) | low | bug | confirmed | compact() never compresses a plain entry that has no raw.bin, so its TIFFs stay uncompressed for good |
| [CRA-09](#crash-recovery-atomicity-inventory-cra-09) | low | data-integrity | confirmed | The `.legacy` numbering copy and the per-run `.bak` are plain copyfiles; an interrupted `.legacy` is never redone, so the original numbering can be lost |
| [CRA-10](#crash-recovery-atomicity-inventory-cra-10) | low | user-error | confirmed | Every writer creates missing roots with mkdir(parents=True), so a folder on an unmounted drive can be recreated on the system disk and filled there |
| [CRA-A2](#crash-recovery-atomicity-inventory-cra-a2) | low | data-integrity | found-by-verifier | A kill during quit-time compaction leaves an entry whose scan.tif no longer matches its recorded checksum, and nothing ever finishes the compaction |
| [CRA-A3](#crash-recovery-atomicity-inventory-cra-a3) | low | data-integrity | found-by-verifier | tools/uniformity.py 'redo' rewrites scan.json in place with write_text, bypassing _write_atomic |
| [CRA-11](#crash-recovery-atomicity-inventory-cra-11) | info | design | confirmed | Inventory: temp+rename covers the record, manifests, cache and settings; every pixel and byte file, the archive, index.json and delivered copies are written in place; only scan.json and manifests are fsynced, and no directory ever is |

## Findings in full

<a id="crash-recovery-atomicity-inventory-cra-a1"></a>

### CRA-A1 -- debug_claim marks a pass as filed when it is only queued; the debug flush at close() then deletes the spool copy of a pass whose library.save failed, and close runs before the writer has finished

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:2872-2875`, `rps7200/session.py:1966-1978`, `rps7200/direct.py:1000-1017`, `rps7200/direct.py:1055-1060`, `rps7200/direct.py:1134-1143`, `tools/scan_roll.py:675`, `tools/scan_roll.py:730`, `tools/scan_roll.py:803`, `tools/scan.py:295-296`, `tools/scan.py:366-375`

**Doc claim:** CLAUDE.md 'Claude: always scan with debug filing on': the tools 'claim each of those passes (`DirectScanner.debug_claim`), so with it on, debug filing adds only the passes they do not keep ... and files nothing twice'. The claim is also used to delete, and it deletes passes that were never filed. direct.py:1093-1097 promises that a spool is not unlinked after a failed save.

With RPS7200_DEBUG=1, which CLAUDE.md makes mandatory for Claude and which the window and tools honour, every pass is spooled to the temp dir as it is taken. That spool copy is the backup that survives a failed library filing: _debug_flush deliberately keeps unclaimed items whose save failed. But a pass the session or a tool files itself is claimed when it is queued, not when its entry exists, and the flush unlinks claimed items without asking whether the caller's filing succeeded. In all three callers the flush also runs before the caller's filing has finished. The session closes the scanner before writer.finish(), scan_roll leaves its with-block before writer.finish(), and scan.py saves only after close. A frame whose library.save fails has its only remaining copy deleted. Examples: a library on an external drive that was unplugged, a full library volume while the temp dir is on another volume, a Windows rename refusal, or a save still queued when close runs and failing later. The writer's in-memory bytes are dropped on the exception too. The raw bytes and pixels of that pass are lost, although a complete copy was on disk until close().

**Evidence (from the code):**

```text
session.py:2872-2875: `claim = getattr(self._scanner, "debug_claim", None)` / `if (raw_image is not None and file_entry and self.root is not None and callable(claim)): claim(raw_image)`. This runs before `self._writer.submit(...)`, and the library.save it vouches for has not run yet. Close order, session.py:1966-1978: `self._scanner.close()` (which ends in `self._debug_flush()`, direct.py:1143), and only then `self._writer.finish()`. direct.py:1055-1060: `if item.get("claimed"): # Its caller filed it, with these same bytes and pixels.` / `stuck += self._debug_unlink(item)` / `continue`. Compare the unclaimed path's own rule, direct.py:1093-1097: 'A spool unlinked after a failed save was the only copy of that pass -- a full disk or a mistyped RPS7200_DEBUG_ROOT deleted every scan it could not file.' tools/scan_roll.py claims at 675/730 and calls `writer.finish()` at 803, after the `with DirectScanner(...)` block has closed and flushed. tools/scan.py claims at 296 inside the with block and calls library.save only after it (366-375).
```

**Failure scenario:** The window, with RPS7200_DEBUG=1, the library on a USB disk and /tmp on the system disk, runs a 20-frame roll at 3600 dpi. At frame 9 the USB disk drops off the bus. library.save raises for frames 9 and 10, and the roll stops. Both frames' spooled image.npy and raw.bin in /tmp/rps7200-debug-*/ are intact. At quit, scanner.close() runs _debug_flush. Frames 9 and 10 are marked claimed, so it logs 'was filed by its caller' and unlinks their image, raw, meta and mask files. Nothing of frames 9 and 10 exists anywhere afterwards. The same happens in tools/scan.py when the post-close library.save loop hits ENOSPC on a bracket: the spool was already emptied at close.

**Fix:** Claim only on success. Give FrameWriter (and scan.py's save loop) the claim and call it after library.save returns, or record the claimed item and have the caller confirm it with debug_filed(pixels) after filing. In _debug_flush, treat an item that was claimed but not confirmed as unfiled: file it or keep it. Alternatively, in all three callers, call writer.finish() before the scanner's close()/flush. The flush must not unlink anything the writer has not confirmed. Add a test that makes library.save raise for a claimed pass and asserts that its spool files survive close().

<a id="crash-recovery-atomicity-inventory-cra-01"></a>

### CRA-01 -- No free-space check anywhere: a roll, bracket, single scan, compaction or debug spool runs into a full disk after the scanner time is spent, and every failure leaves more partial files

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/session.py:2003-2014`, `tools/gui.py:3147-3160`, `tools/gui.py:3327-3336`, `rps7200/library.py:259-264`, `rps7200/library.py:428-438`, `rps7200/direct.py:997-998`, `rps7200/session.py:1989-1995`, `rps7200/session.py:2880-2887`, `tools/scan_roll.py:459-476`, `tools/scan.py:366-375`

**Doc claim:** README.md:414-416 says 'A roll that cannot file a frame — a full disk, a folder gone — stops after the frame in flight instead of scanning on and discarding the rest'. True, but the frame in flight is discarded, and nothing prevents starting a roll the disk cannot hold.

No free-space estimate or check exists before a roll, a bracket, a walk, compaction or the debug flush. The first sign of a full disk is a failed library.save on the writer thread. The roll then stops after the frame in flight (session.py:2005-2014), and that frame usually fails the same way. Every failed save, including each debug-flush item at close, leaves a partial INCOMPLETE directory, and a failed compaction leaves a .raw.bin.gz.part. Neither is cleaned up, so the disk stays full.

**Evidence (from the code):**

```text
No free-space query exists: a search of rps7200/ and tools/ for disk_usage|statvfs|GetDiskFree finds nothing. The only disk-full handling is reactive, after a frame has failed. session.py:2005-2014 `if err is not None and isinstance(self._current, Roll): # A disk that is full, or an output folder that has gone, fails every frame after this one the same way. ... self.request_stop()`. The roll confirmation gives time only: gui.py:3155 `f"roughly {_duration(per * len(numbers) + move_s)}. The frames nobody ticked cost their advance only."`. A refused approved.json starts the roll anyway: gui.py:3335-3336 `self._say(f"could not write approved.json in {folder} ({exc}); " "scanning anyway")`. Each failed save keeps its partial directory: library.py:263 `(path / INCOMPLETE).write_text(...)`, with no cleanup path. The spool swallows ENOSPC: direct.py:997 `except Exception as exc:  # never break a scan`. Compaction needs extra space beside the plain files and only logs when it fails: library.py:428-438 writes `.raw.bin.gz.part` beside raw.bin; session.py:1994 `"it stays uncompressed and complete"`.
```

**Failure scenario:** The operator commissions 30 frames at 3600 dpi RGBI from the contact sheet with an output folder set and RPS7200_DEBUG=1. The library and temp dir share a 200 GB disk with 15 GB free. Around frame 18 the disk fills. approved.json was written, the seek and calibration ran, and 17 frames were filed. Frame 18's library.save raises ENOSPC mid scan.tif, the roll stops after frame 19, and 19 is lost the same way. At quit the debug flush tries every metering probe and hold prescan, leaving about 40 empty INCOMPLETE directories. Compaction of the day's single scans fails and leaves a `.raw.bin.gz.part` in each. About 25 minutes of scanner time gives two frames nothing, and the disk stays full until someone cleans it by hand.

**Fix:** Add one preflight helper, e.g. `library.space_needed(dpi, channels, frames, debug, out_dir)`, from the per-dpi sizes the code already knows. Check it with shutil.disk_usage on each distinct volume (library root, roll folder, out_dir, tempfile.gettempdir() when debug is on) before the device opens in tools/scan.py and tools/scan_roll.py, and in the window before a Roll or commission is submitted. Refuse, or ask, with the numbers. Re-check between roll frames on the scanning thread (cheap), and stop before scanning a frame that cannot be filed. Before compaction at close, check free space against the entry size and skip with a message rather than leaving `.part` files.

<details><summary>Second reader's check</summary>

No disk_usage/statvfs/GetDiskFree anywhere in rps7200/ or tools/ (rg finds nothing). The only disk-full handling is reactive: session.py:2005-2014 stops a Roll after the first failed filing, and the frame in flight is then filed into the same full disk. library.save (library.py:259-305) writes INCOMPLETE, then the data files in place, and never cleans up on an exception, so each failed save leaves a partial directory. The debug flush (direct.py:1052-1081) calls library.save for every unclaimed pending item, so on a full disk each one leaves another INCOMPLETE directory. compact (library.py:428-438) removes its .part only on a checksum mismatch. The approved.json and confirmation-dialog quotes are accurate (gui.py:3149-3158, 3334-3336). I rate it medium, not high. The existing stop bounds the loss to the failing frame and the one in flight. The 'about 40 empty INCOMPLETE directories' is a guess: the directories hold whatever partial files fitted, not nothing. The root cause and the recommendation stand.

</details>

<a id="crash-recovery-atomicity-inventory-cra-02"></a>

### CRA-02 -- Nothing on the next launch, and nothing in `verify`, finds what a crash or full disk leaves: `.part` temps (up to hundreds of MB), orphan spools, half calibration archives and plain entries are invisible

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:1076-1087`, `rps7200/library.py:484-491`, `rps7200/library.py:503-511`, `rps7200/library.py:428-438`, `rps7200/direct.py:736-747`, `rps7200/direct.py:771-803`, `rps7200/direct.py:925-928`, `rps7200/settings.py:110-119`, `rps7200/session.py:906-916`, `tools/library.py:74-77`, `tools/gui.py:8809-8911`, `rps7200/session.py:1989-1995`

**Doc claim:** CLAUDE.md: 'A directory still holding `INCOMPLETE` was cut short while being written, and `make verify` names it.' README.md:601-603: 'A spool that could not be filed — a full disk, a crash before the session closed — is kept ... and the log says where.' After a crash the log never said where, and nothing on the next launch looks.

These leftovers are created by design or by ordinary failures, and none is ever looked for:
- **INCOMPLETE directories.** `verify` names them; nothing repairs or removes them.
- **`.scan.json.part`.** On a Windows rename refusal (CSA-06) it holds the complete fsynced record of an entry verify only calls unfinished.
- **`.raw.bin.gz.part`, `.scan.tif.part`, `.prescan.tif.part`** from a compact or migrate that failed. For a 3600 dpi pass the first is ~100 MB and is invisible to verify.
- **`.shading.part.npz`** in calibration/ and **`gui-settings.json.part`**.
- **calibration/<UTC>/ folders** holding data.bin but no calibration.json, so the bytes have no width or stride.
- **Orphaned `$TMPDIR/rps7200-debug-*` spools** from a kill, a force abort or a failed flush. These can be tens of GB, and their only pointer was a log line.
- **Plain entries** that a killed window never compacted.

The window starts cleanly over all of it. `make verify` says 'library is intact' when the only damage is `.part` files, a half archive or a spool. The individual producers are reported elsewhere (CSA-06, CSA-10, CSA-12, DBG-8, LIB-11, CLI-29, T-07). What is new here is the recovery side: no detection at launch, verify's blind spots, and the helpers that fail without removing their temp.

**Evidence (from the code):**

```text
verify skips dot-folders and never lists the files inside an entry: library.py:1076-1078 `for folder in sorted(p for p in root.iterdir() if p.is_dir()) ...: if folder.name.startswith("."): continue`. The temp-writing helpers do not remove their temp file when they fail. _write_atomic, library.py:486-491: `temp = path.with_name(f".{path.name}.part")` / `with open(temp, "w", ...) as fh: fh.write(text) ...` / `os.replace(temp, path)`, with no try. _replace_tiff, 509-511: `tiff.write(str(temp), image, **kw)` / `os.replace(temp, path)`. compact, 428-433 `temp = path / f".{RAW_FILE}.part"` ... `gzip.open(temp, "wb", ...)`, where temp is removed only on a checksum mismatch (435). save_shading, direct.py:744-746 `temp = path.with_name(f".{path.stem}.part.npz")` / `self._shading.save(temp)` / `os.replace(temp, path)`. settings.py:113-116. Compare write_manifest, session.py:912-916 `except BaseException: ... temp.unlink(missing_ok=True); raise`. The spool location is random and is logged only at flush: direct.py:926-927 `tempfile.mkdtemp(prefix="rps7200-debug-")`. The calibration archive is written in place with no marker (direct.py:781-803). tools/library.py:74-77 offers `["list", "verify", "reconstruct", "reindex", "duplicates", "migrate-raw", "migrate-direction", "tag"]`, with no compact, sweep or clean. gui.py main (8809-8911) builds the session and opens the window with no check of the library, the reference dir or the temp dir.
```

**Failure scenario:** The window is closed on a nearly full disk. Compaction of three single 3600 dpi scans fails with ENOSPC half-way through gzip, leaving three `.raw.bin.gz.part` files of 60-100 MB each (`could not compress ...; it stays uncompressed and complete`). Earlier that week a force abort left a 9 GB `rps7200-debug-k3j2` in /tmp. Next morning the window opens with no word, `make verify` reports 'library is intact', and the 9 GB spool and 250 MB of `.part` files keep the disk full. The next roll fails at frame 3 (CRA-01).

**Fix:** (1) Make `_write_atomic`, `_replace_tiff`, `compact` and `save_shading` remove their temp file in an except/finally, as `write_manifest` does. (2) Extend `library.verify` to report `.part` files inside entries (with sizes), plain entries (raw.bin without raw.bin.gz), and, when given the reference dir, calibration/<stamp>/ folders without calibration.json or whose data.bin does not match its recorded sha256. (3) Add `tools/library.py sweep`: compact plain entries, delete `.part` leftovers, list INCOMPLETE entries (and complete them where a `.scan.json.part` parses), and list `rps7200-debug-*` spools in tempfile.gettempdir() with their sizes and sidecars. (4) Run a cheap version of that check at window start and at CLI tool start, and say what it found in the log before the first job.

<details><summary>Second reader's check</summary>

verify (library.py:1076-1087) iterates only directories, skips dot-names and never lists the files inside an entry, so .part files are invisible to it. _write_atomic (484-491), _replace_tiff (509-511), compact (428-438; the temp is unlinked only at 435 on a mismatch), save_shading (direct.py:744-746) and settings.save (settings.py:112-119) all leave their temp file behind on failure. write_manifest (session.py:907-916) is the only writer that unlinks its temp. rg finds no mention of INCOMPLETE, rps7200-debug or gettempdir outside library.save/verify and direct.py:927. tools/library.py:74-77 offers no compact or sweep action. The only caller of compact is session.py:1992, for the entries of the session that is closing, so plain entries left by a killed window are never compacted. The calibration archive (direct.py:781-803) writes data.bin, ccd_mask.bin, shading.npz and then calibration.json in place, with no marker. Nothing reads it back, so a half archive is never detected.

</details>

<a id="crash-recovery-atomicity-inventory-cra-03"></a>

### CRA-03 -- A window walk writes survey.json naming prescanNN.tif before the writer has written it; a failed filing or kill leaves the survey pointing at a missing file or at the previous walk's picture

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2549-2566`, `rps7200/session.py:2667-2686`, `rps7200/session.py:1637-1673`, `rps7200/session.py:641-669`, `tools/gui.py:5183`, `tools/scan_roll.py:274`, `tools/gui.py:3715-3730`, `tools/scan_roll.py:658-661`, `tools/scan_roll.py:717-721`

In the window, a walk frame's survey record names `prescanNN.tif` as soon as it is queued. The file appears only if the writer's library.save succeeds and the in-place export.write completes. That fails with a full disk, with a reindex failure on a complete entry (LIB-04), with a Windows rename refusal (CSA-06), or when the process is killed while the writer is behind. The survey is then never corrected. If the folder had no such file, the frame silently drops out of the reopened sheet. If the folder was walked before, which the window supports ('walked frames N again: their new prescans replace the old', gui.py:3727-3730) and warns about for a typed name (folder_note), the old prescanNN.tif is still there. Reopening the roll (Rolls browser, --open-roll, make run-sheet) or `scan_roll --approved` pairs the new record (transport position, registration, prescan_rotation) with the old picture. Edges and positions are then proposed from, and holds referenced to, a picture of a different pass. The CLI's roll tool fixed exactly this; the window path did not.

**Evidence (from the code):**

```text
The window only queues the file: session.py:2549-2566 `surveyed = out / f"prescan{number:02d}.tif"` / `walked_as = self._file(seq, number, rf.prescan, ..., path=surveyed, roll=name)`. The writer thread writes it later, and only after library.save succeeds (session.py:1656-1673: `entry = library.save(...)`, then `for path in job.get("paths") or ():`). The scanning thread writes the survey at once: session.py:2667-2668 `if job.dry_run and rf.prescan is not None: record["prescan"] = f"prescan{number:02d}.tif"` and 2686 `record_of.record(record, awaiting=scanned is not None)`. On a walk `scanned` is None, so the record is written at once and never corrected; walk prescans have no `on_filed`. The reader trusts any file of that name: session.py:666 `if not name or not (folder / name).exists(): continue`. The CLI does it in the other order and documents why: scan_roll.py:660-661 `tiff.write(str(pre), frame.prescan)` / `record["prescan"] = pre.name`; scan_roll.py:718-721 "It used to be set here, naming a TIFF nothing had written yet -- and after a crash or a full disk, one nothing ever would."
```

**Failure scenario:** On Windows with the library in a OneDrive folder, the operator re-walks frames 4-6 of an existing walk because frame 5 looked off. For frame 5, library.save writes the complete entry but the reindex write of index.json is refused (LIB-04). The writer raises before writing rolls/<roll>/prescan05.tif, and the walk stops. survey.json already says frame 5 is prescan05.tif, with the new transport position. The next day the sheet is reopened from the Rolls browser. Frame 5 shows the old, badly placed prescan, the edge reader proposes a position from it, and the commissioned roll holds frame 5 against the wrong reference.

**Fix:** Record a walk frame's `prescan` only once the file exists. Pass an `on_filed` for walk prescans that adds `prescan`, `prescan_rotation` and `prescan_flipped` to the record (as `file` is added for roll frames in scan_roll.py:771-775). Or write the file first, as tools/scan_roll.py does. Before re-walking a frame, move or delete its old prescanNN.tif so a failed replacement cannot leave the old picture under the new record. Write roll-folder TIFFs beside the target and rename them over it.

<details><summary>Second reader's check</summary>

On a window walk, _file only submits the prescan to the writer (session.py:2549-2566), with no on_filed. The scanning thread then sets record['prescan'] (2667-2668) and writes the record at once, because awaiting=scanned is not None is False on a dry run (2686). FrameWriter._write runs library.save first and raises out of _write before the paths loop if it fails (session.py:1656-1673), so prescanNN.tif is never written, or the old one stays. walked_prescans (session.py:666) accepts any existing file of that name. tools/scan_roll.py:658-661 writes the TIFF before recording it, and its comment at 718-721 describes this exact hazard. Re-walking frames into the same folder is supported (gui.py:3715-3730).

</details>

<a id="crash-recovery-atomicity-inventory-cra-04"></a>

### CRA-04 -- Contact-sheet decisions reach disk only when the sheet closes; a crash or kill while it is open loses them, and a save that fails at quit is reported into the window being destroyed

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:8600-8617`, `tools/gui.py:2666-2676`, `tools/gui.py:692-727`, `tools/gui.py:3430-3455`, `tools/gui.py:3874-3878`, `tools/gui.py:3416-3428`, `rps7200/settings.py:104-119`

**Doc claim:** tools/gui.py:8603-8607 (`_dismiss` docstring) says the decisions 'used to die with' the window and are now kept. They are kept only on an orderly close. settings.py:39-46 calls the `sheet` section the record for 'the gap before a walk has been commissioned'.

Until a walk is commissioned, the sheet's ticks, hand-set positions and turns exist only in memory. They reach gui-settings.json when the sheet is closed or the window quits cleanly (the comments at 8603-8607 and settings.py:39-46 call this file their only record). A sheet can stay open for a long session, and during a running walk or roll. Any end that skips `_dismiss` loses everything decided since it opened: Force abort taking the process down (the dialog warns of this), a Ctrl-C or closed terminal (SR-19), a crash, a logoff, a power cut. On a clean quit with a full or read-only settings location, the failure is written into a log widget that is destroyed within ~150 ms, so the operator never sees it. A `.part` file is left beside the settings file. The approved.json on disk is written only at commission.

**Evidence (from the code):**

```text
The sheet's state is serialised in exactly one place: gui.py:8610 `self.gui._store_sheet_state(self.state())`, inside `_dismiss`. A search for `self.state()` finds only this call, which runs when the sheet window closes. `_store_sheet_state` (2666-2676) then calls `self._remember()` → `settings.save(...)`. A failed save is reported only into the log widget: gui.py:720-725 `if saved is None: self._say("could not save the window's settings -- the contact sheet's decisions and the setup were not kept")`, and `_say` (3874-3878) only inserts into `self.log`. At quit, on_close runs `self._close_sheet(); self._remember(); self.session.shutdown(); self._wait_to_quit()` (3452-3455), and `_quit` destroys the root as soon as the worker has stopped. settings.save leaves `gui-settings.json.part` on failure (113-119, no unlink). The Force-abort dialog warns: 'It can also take this window down with it.' (3421-3422).
```

**Failure scenario:** The operator walks a 36-frame strip. While the next strip walks, he spends 40 minutes in the sheet un-ticking blanks, turning a dozen frames and hand-setting eight positions. The walk hangs, he types ABORT, and the libusb teardown takes the Python process down. On relaunch the sheet opens with the detector's proposals and none of his decisions. In a variant, the disk is full at quit: the window closes normally, and the only record of the failure was a log line he never saw.

**Fix:** Persist sheet state as decisions are made, debounced to about one write per second after a tick, turn or position change. Use `settings.save`, or better the roll folder's own atomic `sheet.json` via write_manifest, so the decisions live beside the walk. When the save at quit fails, show a blocking messagebox before destroying the root, or refuse to quit. Remove the `.part` in settings.save on failure.

<details><summary>Second reader's check</summary>

self.state() is called only in _ContactSheet._dismiss (gui.py:8610). The other call to _store_sheet_state (3734) runs after a walk, not after a tick or a turn, so there is no persistence as decisions are made. _remember (692-727) reports a failed save only through _say into the log widget. On an idle quit, on_close (3452-3455) goes on to _wait_to_quit/_quit, which destroys the root almost at once. settings.save (settings.py:112-119) leaves the .part behind on failure. The loss needs an unclean end while the sheet is open, and the Force-abort dialog admits that one can happen.

</details>

<a id="crash-recovery-atomicity-inventory-cra-05"></a>

### CRA-05 -- Duplicate roll is a copytree on the UI thread; a failure or force-quit leaves a partial copy that lists as a roll, and Delete's rmtree can half-delete one

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/gui.py:2779-2808`, `tools/gui.py:2837-2842`, `tools/gui.py:5549-5563`, `tools/gui.py:5379-5391`

Duplicate exists to protect a roll's walk, approvals and frames before it is rescanned. A roll folder holds up to 38 frames at 142 MB (gui.py:5553), so the copy can be several GB. It runs synchronously on the UI thread, so the window stops responding for the whole copy. It writes each file in place, with no marker and no cleanup:
- **Disk full:** an error dialog appears, but the partial `<roll>-2` stays. It holds whichever files copytree reached (scandir order, so possibly survey.json without its prescans, or roll.json without approved.json) and is listed in the browser like any roll.
- **Force-quit:** the operator ends the window that is 'Not Responding' during the copy. No message appears, and the partial copy looks like a finished duplicate.
- **Retry:** a second Duplicate gets `-3` (duplicate_name) and leaves the partial `-2` beside it.

Delete roll's rmtree is equally non-atomic. On Windows a file held by another reader can stop it part-way, leaving a roll with some manifests or prescans gone and others present.

**Evidence (from the code):**

```text
gui.py:2795-2807: `messagebox.askokcancel("Duplicate", f"Copy {source.name} to {target.name} -- {human_size(summary['size'])}.\n\nThe copy keeps the walk, the approvals and the frames already scanned, so the original is safe to rescan over.\n\nCopy?")` then `try: shutil.copytree(source, target) except OSError as exc: messagebox.showerror("Duplicate", f"Could not copy: {exc}"); return`. The partial target is not removed, and the copy runs on the Tk callback thread. roll_summary lists any folder holding either manifest: 5562 `if not survey_path.exists() and not roll_path.exists(): return None`. Delete: 2838-2842 `shutil.rmtree(summary["folder"])` / `except OSError as exc: self._say(f"could not delete {summary['roll']}: {exc}")`.
```

**Failure scenario:** The operator duplicates a 5 GB roll before rescanning it at 3600 dpi. The window freezes, and after a minute he force-quits it. `rolls/Portra-2` holds roll.json, survey.json and 20 of 38 frames, but no approved.json. Trusting the dialog's 'the original is safe to rescan over', he rescans the original. approved.json is merged by `_write_approved`, but the frame files are overwritten. The duplicate he relied on is missing half the frames and all the hand-set positions of the first commission.

**Fix:** Copy on a writer thread (tracked by `_start_writing`) into a hidden temp name beside the target, e.g. `.<roll>-2.part`, then rename it to the final name when done. Delete the temp on failure. Have roll_summary ignore dot-prefixed folders. Do the same for Delete: rename the folder to `.<roll>.deleting`, then rmtree, so a failed delete cannot leave a half roll under its real name. Show progress and size in the log.

<details><summary>Second reader's check</summary>

on_duplicate_roll (gui.py:2779-2808) runs shutil.copytree synchronously in a Tk callback. On OSError it only shows showerror and does not remove the partial target. roll_summary (5549-5563) lists any folder with survey.json or roll.json. duplicate_name (5379-5391) picks the next free -N, so a retry leaves the partial -2 in place. on_delete_rolls (2837-2842) calls shutil.rmtree directly and only logs a failure part-way. The window's session worker runs on its own thread, so the scanner is not affected.

</details>

<a id="crash-recovery-atomicity-inventory-cra-06"></a>

### CRA-06 -- A delivered copy that fails or is interrupted part-way stays under its final name; the next export or rescan routes the good copy to `-2`

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1673-1699`, `rps7200/export.py:181-193`, `rps7200/export.py:146-153`, `rps7200/tiff.py:164-168`, `rps7200/session.py:2827-2831`, `rps7200/session.py:2961-2973`, `tools/gui.py:2757-2771`, `tools/gui.py:4289-4301`

A copy that runs out of space, loses its drive, or is killed mid-write stays as a truncated or garbled TIFF, JPEG or DNG under the canonical name, e.g. `<roll>_frame07_3600dpi.tif`. Because `_unclaimed` sees that name as taken, the recovery the log suggests puts the good file at `-2`: exporting again, re-running Save all, or rescanning the frame. The broken file keeps the name that a folder-watching consumer such as NegPy or a sync job picks up first. Nothing removes it, and nothing says it is there beyond the one 'could not write' line. The library is unaffected, so this is delivery-only. OUT-09 notes the in-place writes; the `-2` recovery trap is not reported.

**Evidence (from the code):**

```text
FrameWriter keeps the failure but leaves the file: session.py:1676-1687 `try: Path(path).parent.mkdir(...)` / `note = export.write(str(path), delivered, ...)` / `except Exception as exc: problems.append(f"could not write {path} ({exc})"); continue`, with no unlink. export.write writes in place: 192 `tiff.write(str(path), image, resolution=resolution)`, which is tifffile.imwrite or `open(path, "wb")` (tiff.py:164, 167); the DNG likewise (export.py:149). Names are chosen by existence: session.py:2967-2972 `if not wanted.exists(): return wanted` / `candidate = wanted.with_name(f"{wanted.stem}-{n}{wanted.suffix}")`, used for out_dir copies (2831) and for Save all and Export (gui.py:2762, 4293). The log line says only: session.py:1696-1697 `"...; the library entry is safe and can be exported again"`.
```

**Failure scenario:** The output folder is on a USB stick that fills during frame 7 of a roll. The log says 'could not write .../strip_frame07_3600dpi.tif ...; the library entry is safe and can be exported again'. The operator frees space and uses Export. The good file lands as `strip_frame07_3600dpi-2.tif`, and NegPy, pointed at the folder, still opens the truncated `strip_frame07_3600dpi.tif` as frame 7.

**Fix:** Write every delivered file (and the DNG companion) to a `.part` name beside it and rename on success. On failure, unlink the partial file so the canonical name stays free for the retry. The window's Save all, Export, Save As and FrameWriter copies all go through export.write, so one change there covers them.

<details><summary>Second reader's check</summary>

FrameWriter._write (session.py:1673-1687) catches the export failure and continues without unlinking the file. export.write (export.py:181-193) and tiff.write (tiff.py:164, 167) write the final name in place, and the DNG companion (export.py:149) likewise. _unclaimed (session.py:2961-2973) picks -2 when the name exists, and it is used for out_dir copies (2831) and for Save all and Export (gui.py:2760-2762, 4291-4293). The frameNN.tif in the roll folder is not routed through _unclaimed and is simply overwritten on a retake. The -2 trap applies to out_dir, Save all and Export targets only.

</details>

<a id="crash-recovery-atomicity-inventory-cra-07"></a>

### CRA-07 -- An INCOMPLETE entry cannot be completed: nothing describing the pass (layout, meta, commands) reaches disk before the last step, and the marker says nothing about which pass it was

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:259-305`, `rps7200/library.py:306-399`, `rps7200/session.py:1587-1598`, `rps7200/direct.py:969-977`

scan.json is the only place an entry's raw layout (width, lines, bytes_per_line, channels) and its whole pass record are kept. save() writes it last. A save cut short after the data files leaves an INCOMPLETE directory with a complete decode, the reference and mask, and often complete raw bytes, but no description. That happens with ENOSPC on the record itself, a rename refusal (CSA-06), a kill, or the writer thread dying at interpreter exit (CSA-04). In the rename-refusal case the fsynced `.scan.json.part` does hold the record, but nothing knows to look there. Otherwise the resolution, exposure, gain and offset, film, roll frame, command log, shading origin and registration of that pass are gone. The marker does not say which roll, frame or time it was either. `verify` can only say 'was being written and did not finish', and no tool can finish it.

**Evidence (from the code):**

```text
library.py:263-264 writes the marker with fixed text, `"this entry was being written and did not finish\n"`. Then scan.tif, prescan.tif, shading.npz, ccd_mask.bin and the raw bytes are written (267-304). Only then is the record, with `"raw": {..., "layout": raw_layout}`, `"scan"`, `"extra"` (commands, roll_membership, started_utc), metering and registration, built from memory and written (306-397). On an exception the caller drops the job: session.py:1594-1598 `except Exception as exc: self.errors.append(...)`, and the meta and raw bytes go with it. By contrast the debug spool writes a record beside each pass: direct.py:972-976 `side = self._debug_spool / f"{n:03d}-meta.json"; side.write_text(json.dumps({"meta": item["meta"], "raw_layout": ...}))`.
```

**Failure scenario:** Frame 12 of a roll: raw.bin.gz finishes as the disk fills, and `_write_atomic` raises ENOSPC. library/20260927T101500Z_unknown-film_3600dpi_ir/ holds 300 MB of complete bytes and a correct scan.tif. Months later, `make verify` lists it as unfinished. No record says it was frame 12 of which roll, at what exposure, or which command sequence produced it, and roll.json has the frame as not done with a filing_error.

**Fix:** Write a draft record first, e.g. the full record minus checksums into `scan.json.part` or into the INCOMPLETE marker as JSON, right after `_reserve`. Update it as each file lands, then finalise. Teach verify to show the draft's roll, frame and started_utc, and add a `tools/library.py complete ENTRY` that checksums the present files and promotes the draft.

<details><summary>Second reader's check</summary>

library.save writes a fixed-text INCOMPLETE marker (263-264), then the data files (267-304), and builds and writes the record last (306-397). No draft record exists earlier. FrameWriter._run (1587-1598) records only the error string, and the in-memory meta and raw bytes are dropped. The debug spool does write a meta.json sidecar per pass (direct.py:972-976), as quoted. No tool completes an entry.

</details>

<a id="crash-recovery-atomicity-inventory-cra-08"></a>

### CRA-08 -- compact() never compresses a plain entry that has no raw.bin, so its TIFFs stay uncompressed for good

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/library.py:422-426`, `rps7200/session.py:1669-1670`, `rps7200/session.py:2833-2847`, `rps7200/session.py:2887`, `rps7200/demo.py:870-880`

The window files single passes with `compress=False` and relies on `library.compact` at close to deflate the TIFFs and gzip the bytes. compact returns early when raw.bin is absent, so an entry filed plain without raw bytes keeps its uncompressed scan.tif and prescan.tif permanently. No other tool compacts (CLI-29). This hits the real window when raw_bytes_disagree drops the capture. In the demo it hits every scan and prescan drawn from a stored prescan or a legacy corrected scan, so `make run-demo` never exercises TIFF compaction for those. The cost is disk only; the pixels are exact.

**Evidence (from the code):**

```text
library.py:424-426: `record = json.loads((path / "scan.json").read_text(...))` / `if not plain.exists(): return False`. The TIFF rewrite below (440-450) is never reached. FrameWriter queues every plain entry for compaction whether or not it has bytes: session.py:1669-1670 `if not job.get("compress", True): self.uncompressed.append(entry)`. Single scans and prescans are filed plain (`compress=bool(roll)`, 2887). Entries without raw arise when the guard drops mismatched bytes (2843-2847 `capture = dict(capture, raw=None, raw_path=None, raw_layout=None)`), and in the demo for every 'finished' source (demo.py:876-880: no `last_pixels_raw` and no bytes).
```

**Failure scenario:** A window prescan's bytes are refused by the shape guard ('raw bytes do not describe this image ...'). The entry is filed with an uncompressed prescan-sized scan.tif. At quit compact() returns False without a word, and the entry stays uncompressed while every other entry of the session is deflated. In the demo, every demo prescan entry under demo/library stays uncompressed.

**Fix:** In compact(), gzip the raw bytes only when raw.bin exists, but always rewrite any TIFF that is not already compressed. Or return early only when neither step has anything to do. Add a test with a `compress=False` entry that has no raw bytes.

<details><summary>Second reader's check</summary>

compact returns False when raw.bin is absent (library.py:424-426), before the TIFF rewrite. FrameWriter appends every compress=False entry to uncompressed (session.py:1669-1670). Single scans and prescans get compress=bool(roll), which is False (2887). The shape guard can drop the capture (2843-2847). The demo hands over no bytes for sources that were corrected when stored (demo.py:870-880, with the capture emptied at 825). Disk cost only; the pixels are exact.

</details>

<a id="crash-recovery-atomicity-inventory-cra-09"></a>

### CRA-09 -- The `.legacy` numbering copy and the per-run `.bak` are plain copyfiles; an interrupted `.legacy` is never redone, so the original numbering can be lost

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:999-1013`, `rps7200/session.py:917-921`, `rps7200/session.py:2352-2370`, `tools/scan_roll.py:582-585`, `rps7200/session.py:944-954`

keep_first_numbering promises that 'the numbering it was written with is never lost'. It is the one-time guard before a resume rewrites a legacy manifest's frame numbers for good. If copyfile fails part-way (ENOSPC) or the process dies during it, the `.legacy` file is truncated. Every later run sees `legacy.exists()` and skips the copy, then writes the renumbered manifest over roll.json. The original survives only in `.bak`, until the next run's first write copies the renumbered file over `.bak` too. The `.bak` copy itself fails silently on OSError and can be left truncated. It is only the undo copy, but it is also what read_manifest reads when the main file is damaged.

**Evidence (from the code):**

```text
session.py:1011-1013: `legacy = path.with_name(path.name + ".legacy")` / `if not legacy.exists(): shutil.copyfile(path, legacy)`. The existence test alone decides that the copy is done. write_manifest's backup, 917-921: `if keep_previous and path.exists(): try: shutil.copyfile(path, path.with_name(path.name + PREVIOUS)) except OSError: pass`. copyfile writes the destination in place, so an error or kill leaves a truncated file. read_manifest falls back to `.bak` when the main file cannot be read (944-953).
```

**Failure scenario:** A legacy roll.json from before NUMBERING is resumed on a nearly full disk. copyfile writes 2 KB of a 9 KB `.legacy` and raises. The job fails, and the next run skips the copy because `.legacy` exists and writes the renumbered file. A week later a second resume replaces `.bak`. The numbering the roll was scanned under now exists only as an unreadable fragment in roll.json.legacy.

**Fix:** Copy to a temp name and os.replace it (or reuse write_manifest with the parsed data). Treat a `.legacy` that does not parse as absent. Do the same for `.bak`, and remove a partial `.bak` on failure rather than passing.

<details><summary>Second reader's check</summary>

keep_first_numbering (session.py:999-1013) copies with shutil.copyfile only when legacy does not exist, so a truncated .legacy is never redone. The .bak copy (917-921) uses copyfile in place and swallows OSError, and read_manifest falls back to .bak (944-953). The scenario needs a failure during a small copy, so it is narrow.

</details>

<a id="crash-recovery-atomicity-inventory-cra-10"></a>

### CRA-10 -- Every writer creates missing roots with mkdir(parents=True), so a folder on an unmounted drive can be recreated on the system disk and filled there

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/library.py:469`, `rps7200/session.py:1677`, `rps7200/session.py:2312`, `rps7200/direct.py:741`, `rps7200/direct.py:771`, `tools/gui.py:3281`, `tools/gui.py:668-669`, `tools/gui.py:8885-8890`

**Doc claim:** README.md:414-416: 'A roll that cannot file a frame — a full disk, a folder gone — stops after the frame in flight'. session.py:2006-2007 says the same. A folder that is gone is recreated on such a mount point, so nothing fails and the roll does not stop.

None of the roots (library, rolls, reference dir, output folder) is checked to exist at launch or before a roll. Missing ones are created along with every missing parent. Where the parent is writable while its drive is unmounted, the whole path is quietly recreated on the system disk. Examples: a user-owned mount point such as /mnt/scans, or a tool run with sudo against /Volumes/X. Frames, entries and approved.json then land there instead of on the drive. The roll does not stop, because nothing failed. The files are hidden once the drive is mounted over them, and the system disk can fill (CRA-01). On Windows a missing drive letter or share fails instead, which is the behaviour the README describes. POSIX-only and dependent on permissions, hence low.

**Evidence (from the code):**

```text
library.py:469 `root.mkdir(parents=True, exist_ok=True)`; session.py:1677 `Path(path).parent.mkdir(parents=True, exist_ok=True)` for every delivered copy; session.py:2312 `out.mkdir(parents=True, exist_ok=True)` for roll folders; direct.py:741 and 771 for the reference cache and calibration archive; gui.py:3281 for approved.json. The remembered output folder is applied at launch without checking it exists: gui.py:668-669 `if self.remembered["output"] and self.session.out_dir is None: self._set_outdir(str(self.remembered["output"]))`.
```

**Failure scenario:** On Linux the operator's library lives on an external disk at /mnt/scans (mount point owned by him), and the window remembers /mnt/scans/out as the output folder. He starts the window before plugging the disk in. A 20-frame roll runs normally, and every entry and copy is written to /mnt/scans/... on the root filesystem. When the disk is mounted afterwards those directories vanish under it, and the root filesystem is 6 GB fuller.

**Fix:** At launch, and before a roll, verify that each configured root, or its nearest existing ancestor, is on the expected device. Create only the leaf (`mkdir(parents=False)`) for roots the operator configured, and refuse with a message when the parent is missing. At minimum, warn when the remembered output folder does not exist at launch instead of recreating it on first use.

<details><summary>Second reader's check</summary>

The cited mkdir(parents=True, exist_ok=True) calls exist: library.py:469, session.py:1677 and 2312, direct.py:741 and 771, and gui.py:3281. The remembered output folder is applied without an existence check (gui.py:668-669 -> _set_outdir 1931-1933, which only sets the Path). This only bites on POSIX, with a user-writable mount point and the drive not mounted.

</details>

<a id="crash-recovery-atomicity-inventory-cra-a2"></a>

### CRA-A2 -- A kill during quit-time compaction leaves an entry whose scan.tif no longer matches its recorded checksum, and nothing ever finishes the compaction

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:438-452`, `rps7200/session.py:1989-1995`, `rps7200/library.py:1092-1093`

**Doc claim:** rps7200/library.py:417-421 compact docstring: 'Each file is swapped in whole and the record last, so an interruption leaves a readable entry.'

compact swaps the deflated scan.tif and prescan.tif in before the record carries their new checksums. The window runs compaction at quit, and _wait_to_quit shows 'closing ...' while it does. A kill in that window, from a closed terminal, a force-quit of a slow close or a logoff, leaves an entry whose pixels are intact but whose record holds the uncompressed file's sha256. raw.bin also stays beside raw.bin.gz. verify then reports 'scan.tif does not match its checksum' on a sound entry. That is a false alarm in the one check meant to find real damage, and only a full decode comparison can tell it from real corruption. compact is never called for that entry again, because only the closing session's own list is compacted. The docstring's 'an interruption leaves a readable entry' holds only for readability, not for verify.

**Evidence (from the code):**

```text
compact: `os.replace(temp, path / RAW_FILE)` (438), then for each TIFF `_replace_tiff(path / name, pixels, ...)` and `record.setdefault("image", {})["sha256"] = digest_now` (440-450). Only then is `_write_atomic(path / "scan.json", ...)` called (451), followed by `plain.unlink()` (452). The sole caller is the session worker's finally at close (session.py:1989-1995). verify: `if image.get("sha256") and _sha256(scan) != image["sha256"]: problems.append(f"{path.name}: {scan.name} does not match its checksum")` (1092-1093).
```

**Failure scenario:** The operator quits the window after five 3600 dpi single scans. While compaction is on the third entry, the terminal is closed and the process dies after _replace_tiff(scan.tif) and before _write_atomic. The next `make verify` reports '<entry>: scan.tif does not match its checksum'. The entry keeps both raw.bin and raw.bin.gz for good.

**Fix:** Record the new TIFF checksums in the record before swapping the files, as pending fields verify accepts, or verify pixel equality rather than the file hash when the record says the entry was plain. Make compaction resumable from a 'compacting' note, and add a `tools/library.py compact` that finds plain entries (raw.bin present) and finishes them.

<a id="crash-recovery-atomicity-inventory-cra-a3"></a>

### CRA-A3 -- tools/uniformity.py 'redo' rewrites scan.json in place with write_text, bypassing _write_atomic

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `tools/uniformity.py:639-647`

This is the one writer of an entry record that truncates and rewrites it in place. library.add_tags exists for this job and is atomic (library.py:494-500). A kill or a full disk during the write leaves a truncated scan.json. The only copy of the raw layout and the pass record is then gone, entries() drops the entry, and verify can only say 'scan.json cannot be read'. It also appends a duplicate 'rejected' tag on a repeat, where add_tags would de-duplicate.

**Evidence (from the code):**

```text
`(entry / "REJECTED").write_text(...)` / `record = json.loads((entry / "scan.json").read_text(...))` / `record["tags"] = list(record.get("tags") or []) + ["rejected"]` / `(entry / "scan.json").write_text(json.dumps(record, indent=2), encoding="utf-8")`
```

**Failure scenario:** During a vignette study the operator answers 'redo' on a disk with a few kB free. write_text truncates scan.json and fails part-way. The entry's raw bytes can no longer be decoded, because the layout lived only in that record.

**Fix:** Call library.add_tags(entry, ["rejected"]) instead of rewriting the record by hand.

<a id="crash-recovery-atomicity-inventory-cra-11"></a>

### CRA-11 -- Inventory: temp+rename covers the record, manifests, cache and settings; every pixel and byte file, the archive, index.json and delivered copies are written in place; only scan.json and manifests are fsynced, and no directory ever is

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/library.py:259-305`, `rps7200/library.py:484-491`, `rps7200/library.py:1062-1064`, `rps7200/session.py:886-922`, `rps7200/direct.py:736-747`, `rps7200/direct.py:749-804`, `rps7200/direct.py:922-998`, `rps7200/settings.py:104-119`, `rps7200/export.py:181-193`, `rps7200/demo.py:1275-1281`, `tools/uniformity.py:639-647`, `tools/library.py:265-285`

The per-writer answers are in persisted_state. In summary:
- **Library entries** are protected by a fresh directory reserved with an atomic mkdir, an INCOMPLETE marker, and a record written last with fsync. The data files inside are not fsynced, so a power cut can leave the record durable while scan.tif or raw.bin.gz blocks are not (LIB-15).
- **Manifests** are atomic and clean up after themselves. The cache and settings are atomic, but leave their `.part` on failure and are not fsynced.
- **In place, with no marker:** the calibration archive, index.json, the debug spool, roll-folder TIFFs, delivered copies and the demo cache.
- **Recovery:** the INCOMPLETE marker plus `make verify` is the only mechanism. A kill mid-write leaves a truncated file under the final name for the in-place writers, and a `.part` for the atomic ones.

**Evidence (from the code):**

```text
fsync appears only in library.py:490 (`os.fsync(fh.fileno())` in `_write_atomic`) and session.py:911 (write_manifest). os.replace is used in library.py:438/491/511, session.py:877/965, direct.py:746, settings.py:116 (`temporary.replace(target)`) and tools/library.py:275-276. Everything else writes the final name directly: `tiff.write`, `gzip.open(path / raw_name)`, `reference.save`, `write_bytes`, `write_text`, `np.save`, `np.savez`, `shutil.copyfile`, `shutil.copytree`.
```

**Failure scenario:** Not a defect on its own; the concrete failures are CRA-01 to CRA-10 and the cross-referenced findings.

**Fix:** Use one helper for 'write beside, fsync, rename, fsync the directory, remove the temp on failure' for every file that is final (library, calibration archive, roll folder, delivered copies). Use one marker convention for multi-file units (entry, calibration archive, duplicate roll) that verify understands.

<details><summary>Second reader's check</summary>

fsync appears only at library.py:490 and session.py:911. os.replace/Path.replace appear at library.py:438/491/511, direct.py:746, session.py:877/965, settings.py:98/116 and tools/library.py:275-276. Everything else writes the final name in place: the calibration archive (direct.py:781-803), reindex write_text (library.py:1064), the debug spool (np.save/write_bytes at direct.py:945-985), export.write, uniformity.py:640-646 and the demo np.savez (demo.py:1275-1281). No directory is ever fsynced. The inventory is accurate.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry image: the raw decode, with the 7200 dpi column-stagger realignment where it ran | <library>/<UTC>_<film>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF uint8/uint16 (H,W,C). With tifffile: deflate plus predictor unless compress=False; the built-in writer is always uncompressed | raw (corrections_applied names anything baked in; legacy entries are corrected) | library.save (library.py:267) in place inside the reserved directory under INCOMPLETE; replaced atomically by compact/_replace_tiff (511), migrate_direction (832) and migrate-raw (tools/library.py:273-276, two renames) | library.load/corrected/reconstruct/decode_raw/verify, gui full view and exports, demo sources, tools/make_comparison.py | Lossless pixels. The sha256 in scan.json is recomputed by compact without checking the old one (OUT-17). A kill mid-save leaves it truncated behind INCOMPLETE. Not fsynced. |
| Scanner bytes exactly as received | <library>/<entry>/raw.bin.gz (or raw.bin before compaction; .raw.bin.gz.part during compact) | gzip level 6 of the concatenated READ payloads; or the plain bytes | raw | library.save (library.py:281-304), last of the data files, in place, streamed from the spool when raw_path is given; compact() (428-452) gzips to .part, checks the sha256, replaces, then unlinks raw.bin after the record | library.read_raw (plain preferred only when no .gz), reconstruct, decode_raw, verify, migrate_direction | Byte-exact, sha256 recorded. Not fsynced. A failed compact leaves .raw.bin.gz.part, which verify never reports. Both files can coexist after a kill between replace and unlink. |
| Shading reference the pass is corrected with (a reduction of the calibration lines) | <library>/<entry>/shading.npz | np.savez_compressed: ref<c>/dark<c> float64 arrays, mean<c>/darkmean<c>, pixels_per_line, channels, dark_channels | derived (reduction by calculate_shading), not raw | library.save via ShadingReference.save (library.py:272), in place | library.load/corrected/reconstruct, _one_shading_explains | Exact float64 of the reduction, sha256 in `files`. The calibration bytes behind it live outside the library (DBG-6). |
| CCD mask for the pass | <library>/<entry>/ccd_mask.bin | raw bytes from COPY | raw | library.save (library.py:274) write_bytes, in place | library.load/corrected/reconstruct | Byte-exact, sha256 in `files`. |
| Framing prescan kept with a frame entry | <library>/<entry>/prescan.tif | TIFF uint8 | corrected on real-roll frames and not labelled (LIB-01); rewritten upright by migrate_direction | library.save (270), in place; compact/_replace_tiff; migrate_direction (881) | migrate_direction, demo, gui | No raw bytes of its own; sha256 in `files`. A missing file is not flagged by verify (LIB-14). |
| Entry record: layout, scan fields, extra (commands, roll_membership, started_utc, shading_origin), device, metering, registration, calibration, provenance, checksums | <library>/<entry>/scan.json (temp .scan.json.part) | JSON indent 2, default=str | metadata | library.save via _write_atomic (397: temp, fsync, os.replace, written last); compact (451); add_tags (499); migrate_direction (895); migrate-raw (tools/library.py:283); NON-atomic in-place rewrite by tools/uniformity.py:645 on 'redo' | library.entries/load/verify/reconstruct/decode_raw/compact, gui roll_entry_index, demo signing | The only copy of the raw layout and the pass record. The temp file is not removed on failure. No directory fsync. |
| Unfinished-entry marker | <library>/<entry>/INCOMPLETE | one line of text | n/a | library.save (263) before any data; removed at 398 after the record | library.verify only (1079) | It says only 'did not finish' (CRA-07). Never cleaned or repaired. |
| Rejected uniformity pass marker | <library>/<entry>/REJECTED | text | n/a | tools/uniformity.py:640, in place (scan.json is rewritten in place right after) | nothing in code | n/a |
| Corrected rendition kept by migrate-raw | <library>/<entry>/scan.before-migrate-raw.tif | TIFF | corrected (legacy) | tools/library.py:275 os.replace of the old scan.tif | nothing | Not checksummed; overwritten by a rerun (LIB-06). |
| Derived library index | <library>/index.json | JSON list of summaries | derived | library.reindex (1064) plain write_text, called by every save and by tools | nothing in code | Non-atomic and written from two threads; rebuildable. |
| Session shading cache (--reference) | calibration/shading.npz (cwd-relative; demo/calibration under --demo), temp .shading.part.npz | np.savez_compressed | derived | DirectScanner.save_shading (direct.py:744-746): temp then os.replace, no fsync; tools/uniformity.py:743 in place | load_shading / ensure_shading(reuse=True), tools/uniformity.py --reuse | Exact reduction. The temp file is left on failure. It carries no link to its archive beyond mtime. |
| Calibration archive: every calibration line as read, plus its record | calibration/<UTC>[-N]/data.bin, ccd_mask.bin, shading.npz, calibration.json | raw bytes; npz; JSON (width, stride, commands, data sha256) | raw (data.bin) | DirectScanner.archive_calibration (direct.py:771-803), in place in the order data, mask, npz, json, no marker; only via ensure_shading | nothing | data.bin is byte-exact with a sha256 in calibration.json. The mask and npz are not checksummed. A kill before the json leaves undescribed bytes that nothing detects. |
| Debug spool (RPS7200_DEBUG=1) | $TMPDIR/rps7200-debug-XXXXXX/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz, NNN-ccd_mask.bin | np.save raw pixels; raw bytes; JSON sidecar; npz; mask | raw | DirectScanner._debug_capture (direct.py:922-998) on the scanning thread, in place, errors swallowed; an item is queued only after all of its files are written | DirectScanner._debug_flush at close() (mmap, then library.save); nothing after a crash | Exact. Claimed items are unlinked at close; filed ones after their save; the directory is removed with rmtree(ignore_errors) only when nothing failed. Orphans are never found (CRA-02). |
| Roll and walk manifests | rolls/<roll>/survey.json, roll.json, approved.json (+ .bak, .legacy, .unreadable[-N], temps .<name>.part) | JSON | metadata | session.write_manifest (886-922: temp, fsync, _replace with Windows retry, temp unlinked on failure) via RollManifest (scanning and writer threads) and gui._write_approved; .bak and .legacy via shutil.copyfile in place (919, 1013); set_aside os.replace | read_manifest (falls back to .bak), earlier_manifest, gui roll_summary/read_survey/read_approved, scan_roll --approved, carry_walk | Atomic. survey.json can name a prescan not yet or never written (CRA-03). A partial .legacy is never redone (CRA-09). |
| Roll-folder pictures | rolls/<roll>/prescanNN.tif, prescanNN-before.tif, frameNN.tif | TIFF (deflate), oriented | corrected | FrameWriter._write -> export.write (session.py:1673-1687) after library.save (window); tiff.write on the scanning thread before the record (tools/scan_roll.py:660, 693) | gui read_survey, EdgeWatch, scan_roll --approved, registration studies | In place and overwritten on a retake (SR-08). A failed write leaves a truncated file. |
| Delivered copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng), <out_dir>/prescans/..., Save As, Save all and Export targets, tools/scan.py --out (+ .json) | TIFF 8/16-bit or JPEG (+ 4-sample DNG) | corrected | export.write (181-193) from FrameWriter, gui._deliver_one, tools/scan.py:413-416; names from _unclaimed at submit time | operator and NegPy | JPEG is lossy by design. In place: a failed copy stays under its name and a retry goes to -2 (CRA-06). |
| Window settings, including uncommissioned contact-sheet decisions | gui-settings.json (cwd or RPS7200_SETTINGS; demo/gui-settings.json), .part, .unreadable-<stamp> | JSON | n/a | settings.save (104-119): temp then replace, no fsync, temp not removed on failure; gui._remember on sheet dismiss, quit, presets, shortcuts, roll opened | settings.load at launch (moves unreadable aside) | Sheet decisions are persisted only on dismiss (CRA-04). |
| Demo picture-signature cache | demo/pictures.npz | np.savez (paths, signatures) | derived | DemoScanner._sign_pictures (demo.py:1275-1281) in place on a daemon thread | the same function at the next launch | Non-atomic (DEMO-09). |
| Uniformity orientation crops | previews/orientation_<entry>.png | PNG | display only | tools/uniformity.py write_crop (652-665) | operator | n/a |

**Second reader's corrections to this table:**

- **Debug spool.** The table does not mention that claimed items are unlinked at close() before the caller's writer has filed them, and whatever the outcome of that filing (CRA-A1). "Claimed items are unlinked at close" is therefore not safe as stated. An item whose spooling fails part-way is not appended to _debug_pending. Its index n is reused by the next pass, which overwrites the partial files. If it was the last pass, the files are removed by the rmtree when nothing else failed. A half-written item is never flushed.
- **migrate-raw (tools/library.py:273-283).** Its scan.json write is atomic (_write_atomic). The scan.tif swap is two os.replace calls in sequence, so a kill between them leaves the entry with no scan.tif and the corrected file under scan.before-migrate-raw.tif.
- **compact.** A kill after the TIFF swap and before the record rewrite leaves the record's sha256 values stale (CRA-A2).
- **Roll-folder pictures from the window.** They are written by the writer only after library.save succeeds. A failed save means the file is never written, which is why the survey can name a missing or stale prescan (CRA-03).
- **Delivered copies.** The frameNN.tif in the roll folder is overwritten in place and never routed through _unclaimed. Only out_dir, Save all and Export targets get -N names.
- **index.json.** It can be written concurrently: by the writer thread's library.save, and by the debug flush's library.save on the session thread during close(), which runs before writer.finish(). It is still read by nothing in code.

## What the operator can do

- Kill the window or a CLI tool at any moment (Force Quit, closing the terminal, logoff, or a Force Abort that takes the process down); whatever was mid-write stays on disk as INCOMPLETE entries, truncated roll or delivered files, `.part` temps and orphan spool directories.
- Run `make verify` (tools/library.py verify) to list INCOMPLETE directories, directories without scan.json and checksum mismatches.
- Run `tools/library.py reindex` to rebuild index.json.
- Duplicate, Rename or Delete a roll folder from the Rolls browser while the window holds the device open.
- Set the library, rolls, reference and output folders to removable or network paths, including one the window remembers from a previous launch.
- Re-walk frames into an existing walk's folder, or type an existing roll name for a new walk.
- Start a roll, bracket or long series of single scans on a nearly full disk; nothing checks free space first.

## What the operator should not do

- Start a long roll without checking free space on the library, roll folder, output folder and (with RPS7200_DEBUG=1) the OS temp volume; nothing checks, and the first failure costs the frame in flight too.
- Kill the window while it says 'closing ...': the debug flush, the writer's last frames and the compaction of single scans are running then.
- Force-quit the window while Duplicate is copying (it looks hung because the copy runs on the UI thread).
- Leave hours of contact-sheet decisions in an open sheet during a walk or roll; they reach disk only when the sheet closes.
- Rely on 'can be exported again' after a delivered copy failed: the truncated file keeps its name and the retry lands at -2.
- Delete library entries or roll folders by hand to free space mid-session without running verify afterwards.

## Mistakes nothing guards against

- Starting a roll the disk cannot hold: no estimate of space, no check, and approved.json failing on the full disk only logs 'scanning anyway'.
- Using a remembered output folder, or a --library or --rolls path, on a drive that is not mounted: on POSIX mount points owned by the user it is silently recreated on the system disk.
- Quitting on a full settings disk: the 'could not save the window's settings' line goes into a log that is destroyed at once, and the sheet decisions are lost.
- A walk whose prescan filing failed, reopened later, shows the previous walk's prescanNN.tif under the new record and proposes positions from it.
- A failed or interrupted Duplicate leaves `<roll>-N` listed as a roll with only part of its files.
- Relying on `make verify` after a crash: it does not see `.part` leftovers (a failed compact's `.raw.bin.gz.part` can be hundreds of MB), orphan rps7200-debug-* spools, calibration archives without calibration.json, or uncompacted plain entries.
- A roll.json.legacy truncated by a failed copy is taken as done for good, and the original numbering is later lost.

## Dataflow notes

**Scan path (window and tools)**
1. `DirectScanner.scan()` reads the pass into memory (`_read_pass`), then decodes and corrects it.
2. `_debug_capture` (direct.py:922-998) then spools raw pixels, raw bytes, a meta sidecar, the reference and the mask to `$TMPDIR/rps7200-debug-*` on the scanning thread. This happens only with debug on; errors are swallowed, and an item is queued only when all of its files were written.
3. `ScanSession._file` (session.py:2781-2905) takes `capture_record()`, drops mismatched bytes, claims the pass for debug filing and submits a job to `FrameWriter`. Output-folder names are fixed by `_unclaimed` at submit time.
4. `FrameWriter._write` (1618-1703) runs `library.save` first:
   - `_reserve` makes the directory with an atomic mkdir (460-481); the INCOMPLETE marker is written.
   - scan.tif, prescan.tif, shading.npz and ccd_mask.bin are written, then raw.bin(.gz) last. All in place, none fsynced.
   - The record is built from memory and written with `_write_atomic` (temp, fsync, replace).
   - The marker is unlinked, then `reindex` rewrites index.json in place.
5. Only then are the delivered copies written, each with `export.write` in place. A failed copy is logged and left on disk.
6. `on_filed` → `RollManifest.filed` → `write_manifest` (temp, fsync, replace with retry, `.bak` once per run by copyfile).
7. The scanning thread writes the frame's record before the writer has finished: `done=False` for frames, and immediately for walk prescans (CRA-03).

**At close** (session.py:1968-1996, direct.py:1134-1145)
- `DirectScanner.close()` closes the transport, then `_debug_flush` files each unclaimed spooled pass with `library.save(raw_path=...)` and unlinks it. Claimed passes are unlinked unfiled; the spool is removed only when nothing failed.
- Then `FrameWriter.finish()` drains the queue.
- Then manifests whose last write failed are saved again.
- Then `library.compact()` runs on each plain entry the window filed. It gzips to `.raw.bin.gz.part`, verifies the sha256 and replaces; rewrites the TIFFs through `.part` and replace; writes the record atomically; unlinks raw.bin. It skips entries with no raw.bin (CRA-08).
- The window waits for all of this (`_wait_to_quit`), with no timeout and no progress beyond log lines.

**CLI tools**
- tools/scan.py holds passes in memory and files them after close in a bare loop.
- tools/scan_roll.py uses the same FrameWriter and RollManifest. Dry-run prescans are written with `tiff.write` in place before the record names them.

**Calibration**
- `ensure_shading` → `calibrate_shading` → `archive_calibration`, which writes calibration/<UTC>/ in place with no marker (failures are logged).
- Then `save_shading` writes the cache as temp plus replace (failures are logged).

**GUI-only writers**
- `settings.save`: temp plus replace, no fsync. The sheet's state is written only on `_dismiss` or quit.
- `_write_approved`: `write_manifest`, merged into the existing file.
- Roll folder operations: copytree, rmtree and rename on the UI thread.
- Delete pass: rmtree plus reindex.

**Next launch**
- tools/gui.py main (8809-8911) and the CLI tools check nothing.
- `tools/library.py verify` sees only INCOMPLETE directories, directories without scan.json, and checksum mismatches.

**Free space**
Nothing anywhere measures it.
