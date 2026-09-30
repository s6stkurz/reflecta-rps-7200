# Status of the first audit's problems at 03aacba

[Back to the summary](README.md)

Each problem file under `audit/problems/` was re-checked against the code at 03aacba by a reader told to judge from the code, not from the commit messages. Every sub-issue a problem file lists was checked, not just its headline, so *mostly fixed* usually means the harmful path is closed and something small is left.

## P01 -- A single Scan from the window files corrected pixels as the raw scan.tif

**Status:** fixed · **First audit:** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) · **Commits:** 338970a

**What the code does now:**

rps7200/session.py:2117 `raw_image = getattr(self._scanner, "last_pixels_raw", None)` read straight after scan(), and session.py:2122-2123 `self._file(seq, 0, image, meta, ..., raw_image=raw_image, ...)`, which is the same as _prescan (session.py:2075/2094) and the roll (session.py:2626). The invariant is now structural in FrameWriter._write (session.py:1642-1665): `if raw_image is None and (job["meta"] or {}).get("shading"): corrections = ["shading"]` and then `library.save(job["image"] if raw_image is None else raw_image, ..., corrections=corrections)`. meta["shading"] is non-None only when apply_shading actually ran (direct.py:3113-3161: `shading_report = None` ... `"shading": shading_report`), so the label is correct. The shape guard in _file (session.py:2863-2868) now sends a mismatched raw array into that labelled path. Existing mislabelled entries are repaired by `tools/library.py migrate-raw`, only where `_one_shading_explains` (tools/library.py:49-67) shows that the stored pixels are the decode shaded once (tools/library.py:213-222), with a dry run by default and the old file kept as `scan.before-migrate-raw.tif`. Tests: tests/test_session.py:264 `test_a_scan_files_the_raw_pixels_not_the_corrected_ones` and :275 `test_corrected_pixels_with_no_raw_beside_them_are_labelled`. CLAUDE.md:114-119 was updated to match.

**What is left:**

Nothing on any path reachable with DirectScanner or DemoScanner. Two leftovers remain. The guard lives only in FrameWriter: library.save itself (library.py:216-321) still accepts corrected pixels with `corrections_applied: []`. And tools/scan.py:297-299 `image=image if raw is None else raw` would still file corrected pixels unlabelled if a scanner ever lacked last_pixels_raw (DirectScanner sets it on every pass, direct.py:3229).

## P02 -- Debug filing pairs a pass with the previous pass's raw bytes (or none)

**Status:** mostly-fixed · **First audit:** [P02](../problems/P02-debug-filing-stale-raw-bytes.md) · **Commits:** 338970a

**What the code does now:**

read_planes now clears the bytes of a pass that keeps none. direct.py:1896-1903 `else: ... self.last_raw = None; self.last_raw_layout = None`. Debug mode forces every pass to keep its own bytes: direct.py:3278-3279 `keep_raw=keep_raw or bool(getattr(self, "debug", False))`. The spool refuses bytes laid out for another width or channel count (direct.py:944-957). The session and the CLI walk share `raw_bytes_disagree` (session.py:734-766, used at session.py:2832-2845 and tools/scan_roll.py:672-674). Test: tests/test_decode.py:376 `test_a_pass_that_keeps_no_bytes_leaves_none_behind`.

**What is left:**

(1) last_raw is cleared only once a read has completed (direct.py:1882-1903), not at the start of a pass (fix step 1). A pass that raises before its last line, or any command that fails before the read, leaves the previous pass's bytes in last_raw. This can be reached in a GUI dry-run walk with holding or aiming. Hold pass 1 completes and pass 2 (the nudge, a UsbError, or its prescan) fails. scan_roll yields an error RollFrame that still carries the original prescan (direct.py:4144-4148, `raw_prescan=raw_prescan`, never updated because _hold_to_approved raised). ScanSession._roll then files rf.prescan for every dry-run frame, errored or not (session.py:2536-2565). `_file` takes `capture_record()` (session.py:2832), which holds hold pass 1's bytes. Both are 300 dpi prescans of the same shape, so raw_bytes_disagree cannot tell them apart, and the entry's bytes decode to a different photograph (the film moved). (2) library.save still never decodes `raw`/`raw_path` to check them against `image` (library.py:274-301; fix step 3 was not done). (3) The debug spool guard compares width and channels only (direct.py:950-952).

## P03 -- The debug spool is deleted on a failed save and lost on a crash

**Status:** partly-fixed · **First audit:** [P03](../problems/P03-debug-spool-not-crash-safe.md) · **Commits:** 338970a

**What the code does now:**

_debug_flush unlinks a spooled pass only after library.save returned: direct.py:1090-1091 `if filed: stuck += self._debug_unlink(item)`. On any failure it keeps the whole directory and logs where (direct.py:1100-1109, `self._debug_spool = None` so later passes spool elsewhere). Each spooled pass also writes `NNN-meta.json` (meta, raw_layout, capture time), `NNN-ccd_mask.bin` and a once-per-reference `NNN-shading.npz` beside its pixels (direct.py:971-995). scan() records `started_utc` (direct.py:2966, 3204), and it lands in the entry's `extra`. Test: tests/test_roll.py:812 `test_a_failed_filing_keeps_the_only_copy`, and :836 for the sidecar.

**What is left:**

(1) The spool is still `tempfile.mkdtemp(prefix="rps7200-debug-")` (direct.py:926-927), which is system temp (often tmpfs) and on a different filesystem from library/. It was not moved to library/.spool (fix step 3). (2) Nothing can file a left-over spool. tools/library.py's actions are list/verify/reconstruct/reindex/duplicates/migrate-raw/migrate-direction/tag (tools/library.py:76-78), and there is no `file-spool`, even though the comment says a spool 'can be filed later by hand'. (3) The per-pass sidecar does not say which `NNN-shading.npz` applies to it: `reference_path` exists only in memory (direct.py:984-990). (4) The entry id and `created` are still the flush time: library.py:257 `when = datetime.now(timezone.utc)` feeds entry_id (library.py:154-165). The capture time is only in extra.started_utc. (5) After ScanSession.force_abort, the worker skips `self._scanner.close()` (session.py:1973 `if not self.dead:`). close() is the only caller of _debug_flush (direct.py:1145), so every pending debug pass of that session (metering probes, hold and aim prescans) is left unfiled in temp with no log line.

**New problem the fix introduced:**

debug_claim (direct.py:1000-1017) marks a spooled pass 'filed by its caller' when the caller merely intends to file it: session.py:2873-2875 before writer.submit, tools/scan.py:295-296 before a library.save that runs after the device closes, and tools/scan_roll.py:675/730. _debug_flush then unlinks claimed items without filing them (direct.py:1055-1059). It runs inside close(), which comes before FrameWriter.finish() (session.py:1970-1980) and before scan.py's save loop (tools/scan.py:365-373). If the caller's own library.save then fails (a full library disk, a bad root), the debug copy of that pass has already been deleted and the only remaining copy is in memory, so the pass is lost. The debug safety net is gone exactly when it is needed.

## P04 -- A failed delivered copy (output folder, rolls/) costs the library entry

**Status:** mostly-fixed · **First audit:** [P04](../problems/P04-delivered-copy-before-library-entry.md) · **Commits:** 338970a

**What the code does now:**

FrameWriter._write now files first. session.py:1631-1670 `entry = None / if job["library"]: ... entry = library.save(...)` comes before the copy loop. Each copy is in its own try (session.py:1673-1690 `except Exception as exc: problems.append(f"could not write {path} ({exc})"); continue`). The job raises only when nothing was kept (session.py:1691-1694 `if problems and entry is None: raise OSError`). Otherwise each problem is reported with 'the library entry is safe and can be exported again' (session.py:1695-1700). Test with a library root and an unwritable path: tests/test_session.py:1248 `test_a_copy_that_cannot_be_written_does_not_cost_the_entry`.

**What is left:**

A failed delivered copy is surfaced only as a log line. The notes are drained to 'log' events in _filed (session.py:2000-2002), and the GUI routes 'log' to `_say` (tools/gui.py:3599-3600): no dialog, status or result-row marker (fix step 2). With the remembered output drive unplugged, a whole roll keeps scanning with one log line per frame. The entries are safe, but the operator's folder silently stays empty.

**New problem the fix introduced:**

The reversed order has a new failure mode. `entry = library.save(...)` (session.py:1656) is not inside a try, so when the library itself cannot be written (a full or unwritable library disk, or a failure part-way through the entry) the job raises before any delivered copy is attempted. A scan whose output folder is on a healthy disk is then lost entirely: no entry, no frameNN.tif, no output-folder copy. Before, the operator's corrected copy was written first and survived. The roll does stop (session.py:2004-2014), but the frame in flight is gone.

## P05 -- Calibration bytes are thrown away; only a derived reference is kept

**Status:** mostly-fixed · **First audit:** [P05](../problems/P05-calibration-not-stored-exactly.md) · **Commits:** 2e04b01, e5929e1

**What the code does now:**

ensure_shading now asks for the bytes and archives them: direct.py:848 `result = self.calibrate_shading(keep_data=True)`, then direct.py:853-860 `archive = self.archive_calibration(result, path.parent)` and `self._shading_origin["archive"] = str(archive)`. archive_calibration (direct.py:749-804) writes `data.bin` (every calibration line as read), `ccd_mask.bin`, `shading.npz` and `calibration.json`. The JSON holds resolution, pixels_per_line, bytes_per_line, byte count, sha256, duration, protocol_revision and `commands`. `commands` comes from _CommandLog (direct.py:474-493) and so includes the 128-byte calibration info READ, the shading descriptor and every gain/offset read-back. Provenance is recorded on every corrected pass (direct.py:3208-3211 `"shading_origin": dict(origin) if shading and ...`): calibrated with resolution, width and measured_utc (direct.py:2352-2356), or loaded with path, file_modified_utc and loaded_utc (direct.py:724-729). The cache write is atomic (direct.py:743-746, `.part.npz` then `os.replace`), and a failed cache write keeps the reference in force (direct.py:864-871). Tests: tests/test_scanner_api.py:262, :279, :292.

**What is left:**

(1) Nothing reads the archive back. library.py has no reference to it, so library.corrected() cannot rebuild a reference from the calibration bytes with today's calculate_shading (fix step 3). (2) A reference loaded from the cache (reuse, 'Use the cached one') records only the path and mtime, not which calibration archive it came from, and the cache file carries no archive id. Joining an entry to its calibration bytes needs guesswork from timestamps. (3) The archive lives outside the library, in `<reference dir>/<UTC stamp>/` (for example calibration/, or ~/calibration from gui.py:8887). An entry holds only a path string in extra.shading_origin.archive. `library verify` does not check that path and it is not copied with an entry, so the library alone no longer holds everything needed to recompute. (4) tools/uniformity.py:733-743 calls `scanner.calibrate_shading()` directly: its bytes are discarded, it writes no archive, and `reference.save(reference_path)` is not atomic. (5) A calibration that times out or fails part-way discards the bytes it did read (direct.py:2317-2334 raises before any archive). (6) archive_calibration's failures are caught only as OSError (direct.py:855-858). Any other exception there escapes ensure_shading after the reference is already in force, and the session's `calibrated` flag is not set.

## P06 -- At 7200 dpi scan.tif is not the plain decode, and nothing records it

**Status:** fixed · **First audit:** [P06](../problems/P06-7200dpi-realignment-not-recorded.md) · **Commits:** 8b01894, 3f5cce0

**What the code does now:**

scan() records the realignment: direct.py:3087-3098 `stagger_realigned = 0 / if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... stagger_realigned = self.NATIVE_COLUMN_STAGGER_LINES`, and meta at direct.py:3203. It is kept in the record's `scan` block (library.py:181 in SCAN_FIELDS). decode_raw and reconstruct replay it: library.py:627-655 `stagger_lines`/`_replay`, decode_raw library.py:683 `return _replay(image, record, stored[0])`, reconstruct library.py:752-754 `image = _replay(image, record, stored.shape[0])`. Legacy 7200 entries are recognised by resolution plus a shortfall of exactly 4 rows (library.py:640-645). reconstruct now catches ScanReadError (library.py:727-730). The session guard subtracts the trimmed rows (session.py:757-759). A shaded 7200 dpi pass is refused before metering or any command (direct.py:2937-2945 `if not self.correctable_at(resolution, frame): raise self.uncorrectable(...)`, ahead of `if auto_exposure:` at 2962), the demo refuses in the same way (demo.py:528-529), and tools/scan.py refuses before opening (tools/scan.py:185-190). dpi_analysis refuses a mixed-domain series (tools/dpi_analysis.py:100-124, 196-205). Tests: tests/test_library.py:706, tests/test_session.py:1323. CLAUDE.md:117-119 now names the realignment.

**What is left:**

Nothing harmful. The GUI ladder still lists 7200 (tools/gui.py:127 `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`), but the refusal now costs no scanner time. The first audit's LIB-09 remark that the realignment sign ignores read direction does not hold. The stagger is a fixed spatial offset between two CCD rows, and decode_index turns rows upright before realigning (direct.py:1966-1969), so the offset has the same sign in the upright image whichever way the carriage ran.

## P07 -- A short or failed pass loses its raw bytes

**Status:** partly-fixed · **First audit:** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) · **Commits:** 8b01894

**What the code does now:**

The short-read half is fixed. raw_bytes_disagree judges the rows the bytes can decode to, not the declared count: session.py:753-759 `received = layout.get("lines_received") ... layout["lines"] = int(received) // int(channels)` minus `stagger_realigned`. It agrees with decode_index's `height = min(len(planes[c]) for c in order)` (direct.py:1965). read_planes still records `lines_received` (direct.py:1895). Test: tests/test_session.py:1310-1340.

**What is left:**

The failed-pass half (TP-A1, DDF-09) is untouched, and no `tags=["failed"]` path exists anywhere. A pass whose bytes were fully read and which then fails is never filed, even under RPS7200_DEBUG=1, because _debug_capture is called only on success (direct.py:3222). Cases: a decode refusal inside read_planes after `last_raw` was set (direct.py:1955-1964, wrong channel set or no recognisable tags); the post-read width refusal (direct.py:3115-3126 `raise ShadingUnavailable(... wider than ...)`); and the realignment's ValueError for too-short frames (direct.py:2034-2038). A read that ends in TimeoutError or ScanReadError mid-pass (direct.py:1846-1860) drops the local `chunks` list, which is the anomalous data most worth keeping.

## P08 -- Whole classes of passes are never filed, and RPS7200_DEBUG is overridden

**Status:** partly-fixed · **First audit:** [P08](../problems/P08-passes-never-filed.md) · **Commits:** 338970a, 8061a86

**What the code does now:**

RPS7200_DEBUG is honoured on the real paths. session.py:1896-1906 `DirectScanner(verbose=..., debug=None)`, tools/scan.py:266 `DirectScanner(verbose=args.verbose, debug=None)` and tools/scan_roll.py:475 do the same, and `debug=None` reads the environment (direct.py:556-561). Passes a caller files itself are claimed so they are not filed twice (session.py:2873-2875, direct.py:1000-1017). Under debug, metering probes (keep_raw=True, direct.py:2492-2504), hold and aim prescans, and the prescan a correction replaced (session.py:2586 `file_entry=False`) are filed by the flush. `tools/scan_roll.py --dry-run` now files each walk prescan with its raw pixels and bytes through FrameWriter (tools/scan_roll.py:666-686). Test: tests/test_scan_roll_tool.py:1132.

**What is left:**

(1) A real roll frame's prescan is still the corrected 8-bit prescan: session.py:2627 `prescan=rf.prescan` and tools/scan_roll.py:752 `prescan=frame.prescan`, where rf.prescan is prescan()'s corrected return (direct.py:2076-2083). It is written as `prescan.tif` (library.py:270) with no raw bytes, mask or resolution tag, and no correction label. Its record block keeps only file, read_direction and carriage_state (library.py:378-382). rf.raw_prescan is still dropped on real rolls in both the GUI and the CLI (fix step 3). (2) Without RPS7200_DEBUG (the default), nothing files metering probes, hold or verification prescans, a real-roll frame's raw prescan, the pre-correction prescan (session.py:2586, tools/scan_roll.py:693 writes only a TIFF), or the prescan of a frame that failed on a real roll (session.py:2536 files prescans only `if job.dry_run`). (3) Under debug, those passes are filed tagged only `debug` (direct.py:1068) and are not tagged probe or hold, nor linked to the frame entry by id (fix step 4). (4) The CLI walk still writes `prescanNN.tif` inline on the scanning thread (tools/scan_roll.py:660, and :693 for -before), and it does not file errored frames' prescans (tools/scan_roll.py:649-651).

**New problem the fix introduced:**

The debug_claim mechanism added to make this fix affordable drops the debug copy before the caller's own filing has succeeded, because the flush in close() runs before FrameWriter.finish() or scan.py's save loop. A failed library.save then loses the pass that debug filing would otherwise have kept (details under P03).

## P09 -- The entry record drops parameters needed to re-derive or evaluate a pass

**Status:** mostly-fixed · **First audit:** [P09](../problems/P09-record-missing-parameters.md) · **Commits:** f64671f, 8061a86, 2e04b01

**What the code does now:**

The fixed whitelist is gone. rps7200/library.py:337 `"extra": {k: v for k, v in meta.items() if k not in _RECORDED}` keeps every meta key the scan block does not name, so bracket_index/ratio/passes/stops (direct.py:2821-2824), roll_index/roll_position (direct.py:4134-4135), uniformity_session and the demo's `"demo": True` (demo.py:903) now reach the record. library.py:340 `"device": _describe_inquiry(inquiry)` stores the INQUIRY fields. scan() records the pass definition: direct.py:3201-3211 `"started_utc"`, `"mode": {"byte14_override": byte14, "skip_shading": ..., "slide_init_param": ...}`, `"commands": commands` (every CDB and reply from `_CommandLog`, direct.py:433-490) and `"shading_origin"`. Provenance: library.py:107 `"driver_dirty": None if head is None else bool(status)` with `--untracked-files=no`, and library.py:111 `"driver_commit_at_import"` taken once at import (library.py:118-138). Roll prescans get the roll's film (direct.py:3968-3973 `film=film`). Still open: the roll loop meters with `self.auto_exposure(...)` (direct.py:4109-4112) and then calls `self.scan(... exposure_scale=scales ...)` with auto_exposure left False, so direct.py:3186 `"exposure_metered": bool(auto_exposure)` writes False and direct.py:3219 `if auto_exposure and self.last_metering is not None: meta["metering"] = ...` never attaches the evidence. scan_bracket does the same (direct.py:2794-2795, then scan() without auto_exposure). The hold loop keeps `asked = self.nudge(want)` only for `clamped` (direct.py:3368-3369) and records `spent_mm` and `moves`, not the SLIDE param bytes. Those nudges and the SLIDE_NEXT advances run outside the `_CommandLog` window (start at direct.py:2990, stop at 3075), so no entry records them. The hold's verification prescan is called without film (direct.py:3377-3378 `self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)`), and its meta becomes the frame's `prescan_meta` (direct.py:4033-4035), with `film` defaulting to "negative". tools/scan.py:405-409 builds `meta["bracket"]` (merge stats) only for the delivered .json sidecar, after filing, and never files the merged result.

**What is left:**

(1) Roll frames and bracket passes that were metered are still recorded as `exposure_metered: false`, with no `metering` block (DDF-08, LIB-13, CLI-12, OUT-13). (2) The merged bracket and its merge stats are never filed, and no bracket group id links members beyond timestamps and bracket_index. (3) The SLIDE params and bytes that placed a frame (hold/aim nudges, SLIDE_NEXT) are not recorded, and the hold record keeps only millimetre-derived `spent_mm` (CIP-5). (4) Hold/aim verification prescans still file `film: negative` whatever the roll's film, both in debug entries and as a held frame's prescan_meta (T12 remainder). (5) Debug-filed probe passes do not say which probe, step or rung they belong to (no argv/tool name), and `_debug_capture` snapshots meta inside scan() (direct.py:3222), so fields a caller adds later never reach a debug entry (PA-03 remainder). (6) Code identity has commit and dirty flags but no hash of the imported module files and no diff (CIP-1/CIP-2 remainder). (7) INQUIRY is stored as parsed fields, not the raw response bytes, and INQUIRY is sent outside the command log. (8) FE-06 (frame-edge member answers in approved.json) was not re-verified here.

**New problem the fix introduced:**

`_CommandLog.start()` is called in scan() before the READ STATE loop, but `stop()` runs only in the `finally` around `_read_pass` (direct.py:3070-3075). A scan() that raises between them (for example a CheckCondition from set_mode) leaves `record` as a live list, and every later command outside a pass (nudges, advances, inquiry) is appended to it until the next start() discards it. The commands of the failed pass are lost with it. Impact is low. `extra` is serialised with `json.dumps(..., default=str)` (library.py:397), so any ndarray a caller puts in meta would be written as numpy's truncated repr rather than exactly. None was observed in current meta.

## P10 -- Entries and manifests are written in place; partial files look complete

**Status:** mostly-fixed · **First audit:** [P10](../problems/P10-non-atomic-writes.md) · **Commits:** db9466b

**What the code does now:**

Id race: library.py:460-481 `_reserve` uses `path.mkdir()` and moves to the next `-N` on FileExistsError, so two writers can no longer share a directory. Crash marker: library.py:263 `(path / INCOMPLETE).write_text(...)`, removed only after library.py:397 `_write_atomic(path / "scan.json", ...)`. `_write_atomic` (library.py:484-491) writes beside, fsyncs and calls os.replace. Checksums: library.py:390-394 `record["files"] = {name: _sha256(path / name) for name in ("shading.npz", "ccd_mask.bin", "prescan.tif") ...}`. verify (library.py:1073-1087) reports INCOMPLETE directories, directories with no scan.json and unreadable scan.json, and checks `files` digests at library.py:1104-1106. Entries are located by directory: library.py:514-521 `entry_path` uses `record["_dir"]`, and verify reports an id/dir mismatch (1090-1091). Both migrations write beside and swap (tools/library.py:273-276, library.py:503-511 `_replace_tiff`). What is left: library.py:1105 `if (path / name).exists() and _sha256(path / name) != digest` does not report a checksummed file (for example prescan.tif) that is missing. Data files are written straight to their final names with no fsync; only scan.json is fsynced. library.py:1064 `index.write_text(...)` (reindex) is still non-atomic and runs from every save. tools/uniformity.py:643-646 still rewrites scan.json in place with `write_text` when tagging `rejected`. tiff.write (rps7200/tiff.py:68-71) writes delivered copies and rolls/*.tif straight to their final path.

**What is left:**

(1) verify does not report a missing prescan.tif (or any other `files` entry that is not referenced from `calibration`). (2) No fsync of scan.tif, raw.bin.gz, shading.npz or the directory. After a power loss the fsynced scan.json can outlive its data, and only a checksum mismatch would show it. (3) Leftover `.part` files inside an entry (`.scan.json.part`, `.raw.bin.gz.part`, `.scan.tif.part`) are never reported. (4) index.json is rewritten non-atomically. It is derived and no code reads it, so the impact is low. (5) tools/uniformity.py:643-646 tags `rejected` with a non-atomic in-place rewrite instead of library.add_tags. (6) Delivered files, rolls/prescanNN.tif and DNG are still written in place (RX-8 part). (7) There is no lock between tools/library.py mutators and a running window or its compact() (CC-08). (8) migrate-direction's "recorded" branch (library.py ~839-842) does not refresh image.sha256. After an interrupted run that already swapped scan.tif, verify reports a checksum mismatch permanently.

**New problem the fix introduced:**

The migrate-raw swap order is: `os.replace(path / "scan.tif", path / KEPT)`, then the fresh scan.tif, then the record (tools/library.py:275-284). A kill between the second replace and `_write_atomic` leaves raw pixels under a record that still says `corrections_applied: ["shading"]`. library.corrected() then returns them as "already" corrected. Re-running migrate-raw plans the entry again and `os.replace(scan.tif, KEPT)` overwrites the only preserved corrected rendition with the raw file. The kept file is not in `files` and has no checksum.

## P11 -- `library.py duplicates --delete` destroys scans of different photographs

**Status:** mostly-fixed · **First audit:** [P11](../problems/P11-duplicates-delete-destroys-scans.md) · **Commits:** 338970a

**What the code does now:**

library.py:996-1011 `prunable` now requires identical data as well as a shared signature: `twin = next((k for k in kept if same_data(record, k)), None); if twin is None: kept.append(record); continue`. same_data (library.py:1014-1027) compares raw sha256 where both entries have raw bytes, otherwise image sha256, and treats an entry with neither as never the same. Different photographs with empty notes, byte-14/gain rungs and walk prescans therefore hold different bytes and are no longer offered. The tool docstring says so (tools/library.py:16-20). Deletion itself is unchanged: tools/library.py:190-191 `if args.delete: shutil.rmtree(path)`, with no confirmation and no trash. signature() (library.py:944-956) still lacks gain, byte14 and capture time.

**What is left:**

(1) `--delete` still calls rmtree with no typed confirmation and no `library/.trash/`. (2) The signature still omits gain, byte14 and started_utc. This no longer matters for deletion, since byte equality gates it, but the dry-run groups are still misleading. (3) Debug/evidence-tagged entries are not excluded. (4) When two entries hold byte-identical raw data, the survivor is chosen only on `(raw, cal, created)` (library.py:987-990). The newest wins ties, so a richer twin (film notes, prescan.tif, registration/roll `extra`, tags) can be deleted in favour of a bare one, for example a debug-flush copy filed after close if debug_claim ever misses.

## P12 -- reconstruct / verify / migrate-raw report or repair the wrong thing

**Status:** mostly-fixed · **First audit:** [P12](../problems/P12-library-maintenance-tools.md) · **Commits:** db9466b, 50bb805

**What the code does now:**

migrate-raw: tools/library.py:247-256 `if not applied and not _one_shading_explains(path, plain, stored): failed.append(... "a decode change, not a mislabelled entry; left alone")`. `_one_shading_explains` (tools/library.py:49-67) requires `apply_shading(plain, reference, mask) == stored`. The old file is kept: tools/library.py:275 `os.replace(path / "scan.tif", path / KEPT)`, with `image["replaced"] = KEPT`. reconstruct: library.py:733-735 catches `(KeyError, ValueError, TypeError, ScanReadError)` as a verdict. A missing or unreadable scan.tif returns `could not read scan.tif` (library.py:752-755). 7200 dpi is replayed via `_replay` (library.py:758). Bottom-up legacy entries get their own verdict (library.py:768-773), which the tool counts apart (tools/library.py:154-158). verify (library.py:1116-1126) no longer reports `SHADING_SKIPPED_EXPLICIT` entries, demo entries or entries tagged `uncalibrated-on-purpose`. Still open: for labelled entries (`applied` non-empty) migrate-raw plans a rewrite with no derivation check. Line 247 guards only `not applied`, so a legacy labelled entry whose bytes belong to another pass (the pre-P02 stale-raw case) gets a decode of a different photograph as scan.tif. `ShadingReference.load` inside reconstruct (library.py:747) and `library.reconstruct(path)` inside the tool loops (tools/library.py:148, 226) have no try, so a corrupt shading.npz (zipfile.BadZipFile is not OSError) aborts the whole run.

**What is left:**

(1) migrate-raw does not check that a *labelled* entry's stored pixels equal `apply_shading(decode_raw)` before replacing them. It relies on the KEPT backup rather than refusing, contrary to fix-plan item 1. (2) One corrupt shading.npz still aborts `reconstruct` and `migrate-raw` for every later entry. (3) `uncalibrated-on-purpose` must be set by hand. No probe applies it, and verify stays quiet for byte-14 entries only because they were taken with shading=False. (4) The KEPT file is not checksummed and a re-run can overwrite it (see P10 new_problems).

## P13 -- The window gzips library entries with the scanner open and idle

**Status:** partly-fixed · **First audit:** [P13](../problems/P13-gzip-with-device-open.md) · **Commits:** 0bb498f

**What the code does now:**

Single Scan/Prescan entries are now filed plain: session.py:2887 `compress=bool(roll)` flows through FrameWriter (session.py:1666 `compress=job.get("compress", True)`) to library.save's `raw.bin` and uncompressed TIFF path (library.py:268-294). They are compacted only after close, in the worker's finally (session.py:1965-1995: `self._scanner.close()` ... `self._writer.finish()` ... `library.compact(entry)`). Still happening with the device open and idle: (a) The last frame(s) of every roll and walk, and frames queued before a Stop, are submitted with `compress=True`. The worker returns to `self._jobs.get()` (session.py:1947) while the writer gzips them. (b) Delivered copies of single scans and prescans go through `export.write` in FrameWriter._write (session.py:1675-1685), which calls `tiff.write(..., compress=True default)` (tiff.py:97-103), JPEG/DNG encoding included, on the writer thread between jobs. (c) Every library.save hashes every file and calls `reindex(root)` (library.py:399), which re-reads every scan.json in the library, also between jobs. (d) The window's own Save all/Export threads (gui.py:3457-3461 `_start_writing`) and full-resolution `library.corrected` loads run with the session open (GUI2-18). tools/filing_load_test.py was not extended to the open-and-idle case.

**What is left:**

(1) The tail of every roll/walk and frames after a Stop are still gzipped with the device open and idle, which is exactly the case the rule targets. (2) Single-scan delivered copies (deflate TIFF, JPEG, DNG) are compressed with the device open and idle whenever an output folder is set. (3) Heavy local work (sha256 of every file, full-library reindex, GUI exports and full-res corrections) still runs with the session open. (4) There is no measurement of the open-and-idle case.

**New problem the fix introduced:**

compact() only runs on a clean window close. If the window is killed, plain entries (a 7200 dpi RGBI raw.bin is ~570 MB) stay uncompressed for good. There is no `tools/library.py compact` action, and verify does not mention them. compact() rewrites scan.tif/prescan.tif and records a fresh sha256 (library.py:437-446) without first checking the plain file against its recorded digest or reading the pixels back. Damage between filing and compact is therefore blessed with a new checksum. tools/collect_vignette_study.py:49 still requires `raw.bin.gz`, so it skips or mis-copies entries left plain.

## P14 -- After a failed or interrupted read, the software keeps talking to the device

**Status:** partly-fixed · **First audit:** [P14](../problems/P14-failure-paths-keep-driving-device.md) · **Commits:** 866eb67

**What the code does now:**

Fixed: direct.py:3284-3290 `_read_pass` marks the device suspect when a pass ends before `_read_complete`: `if not self._read_complete: self._mark_suspect(...)`. The calibration does the same (direct.py:2323-2340, including the deadline case). direct.py:4157-4162 ends the roll instead of advancing: `if self.suspect is not None: ... raise DeviceSuspect(self.suspect)`. tools/scan_roll.py:473-475 uses `DeferredInterrupt`, and tools/scan_roll.py:783-803 catches BaseException and always runs `writer.finish()`. tools/scan.py:282-292 wraps the session and files every held pass after any exception (tools/scan.py:360-369). Not fixed: `_refuse_if_suspect` is called only in slide (direct.py:1523), start_scan (1623) and calibrate_shading (2178). A scan() on a suspect device still sends READ STATE, wait_warm, TEST UNIT READY, set_exposure_time (3016), set_highlight_shadow, set_scan_frame, cmd_17, get/set_gain_offset and set_mode (3051) before SLIDE INIT (3062) refuses. auto_exposure probes do the same, and the session keeps accepting jobs that each do this. tools/byte14_probe.py:176-186 still runs `scanner.scan(...)` in its `finally` after any failure or Ctrl-C, with no DeferredInterrupt. tools/gain_probe.py:209-218 still calls `set_gain_offset` in `finally`. No SIGTERM handler exists anywhere (only `signal.SIGINT` in console.py:95), yet tools/scan_roll.py:787 claims a "SIGTERM-turned-SystemExit" is handled. FrameWriter and ScanSession threads are still `daemon=True` (session.py:1584, 1819). `force_abort` still closes the transport from the UI thread (session.py:1855-1874). read_planes still passes `retries=1` (direct.py:1846). `_bulk_read_into` still calls clear_halt and raises on a silent timeout (usb_transport.py:650-661). After a `closed` event the window calls `_set_busy(False)` and re-enables every run button (gui.py:3692-3694, 3801-3803), so jobs submitted to a dead worker never run.

**What is left:**

(1) A suspect device still receives configuration writes (exposure, frame, gain/offset, MODE SELECT) on every later scan attempt. Only SLIDE, START SCAN and calibration are refused, not "everything except READ STATE/REQUEST SENSE". (2) The session does not refuse jobs or flag itself once the scanner is suspect. (3) byte14_probe's finally-scan and gain_probe's finally-write remain. (4) SIGTERM is unhandled, so scan_roll/scan.py die without writer.finish() or filing, and the code comment claims otherwise (doc-mismatch at tools/scan_roll.py:786-788). (5) tools/scan.py still holds passes only in memory until the `with` exits. A hard kill loses them, and a library.save failure on one pass (tools/scan.py:360-369, no per-pass try) loses every pass after it. (6) Daemon threads, force_abort from the UI thread (TP-10), the dead-worker window (SR-19), read_planes' single CHECK CONDITION attempt (TP-A2) and the silent mid-payload pause (TP-08) are all unchanged.

## P15 -- `--no-shading` does not skip calibration; the stalling lazy path is live

**Status:** mostly-fixed · **First audit:** [P15](../problems/P15-lazy-calibration-inside-scan.md) · **Commits:** 1492d2c, 866eb67

**What the code does now:**

The lazy calibration is gone. direct.py:2947-2960 `if self._shading is None or needed > self._shading.pixels_per_line: ... raise self.uncalibrated(reason)`. This runs before metering and before anything is sent, and the scan() docstring says "never calibrates inside one either". `shading` is threaded through auto_exposure (direct.py:2059, 2496-2497), prescan (prescan(..., shading)), the hold loop (3377-3378), scan_bracket (2794-2795) and scan_roll (4109-4124). tools/scan_roll.py:632 passes `shading=not args.no_shading`. A calibration still sending at its deadline raises and marks the device suspect (direct.py:2321-2332). session.py:2057-2058 sets `self.calibrated` only when a reference exists. Still open: tools/gui.py:2000-2002 `self.session.submit(Calibrate(...)); self.calibrated = True` is still set on submit. The calibration read loop still treats any refused read as the natural end: direct.py:2310 `except (EndOfData, ScanReadError): ... ended = True; break`. read_lines raises ScanReadError for a non-end-of-data refusal after one attempt (retries=1, direct.py:2306), and `calculate_shading(data, width)` (2351) then builds a reference from partial data without complaint (TP-12 remainder).

**What is left:**

(1) The window still sets `calibrated=True` optimistically on submit. The harmful consequence is gone, since a queued scan is now refused by scan() instead of calibrating, but the button text and state lie until the event arrives. (2) A mid-calibration refused READ (one-shot sense, retries=1) is taken as "scanner finished", and a partial reference is built silently. Nothing checks the collected line count against what was expected.

**New problem the fix introduced:**

Removing the lazy path left several tools unable to run at all. They now raise ShadingUnavailable before sending anything, which is safe but non-functional. tools/hold_probe.py:136, tools/transport_truth.py:125 and tools/roll_registration_walk.py:108 call `prescan()` with the default shading=True and never calibrate. tools/byte14_probe.py:134, tools/gain_probe.py:138, tools/fast_ir_probe.py:199/443 and tools/uniformity.py:707 call `auto_exposure()` with its default shading=True and no reference. tools/hold_probe.py:135 still prints "(a calibration runs first if this session has none -- ~2 min)", which no longer happens (doc-mismatch).

## P16 -- Read timeouts and the 10-minute budget are not enforced where they matter

**Status:** mostly-fixed · **First audit:** [P16](../problems/P16-timeouts-and-runtime-budget.md) · **Commits:** 78fba88

**What the code does now:**

direct.py:642-646 `read_idle_s`: `if infrared and not fast_infrared: return max(cls.READ_IDLE_S, cls.UNTIED_INFRARED_IDLE_S)` (287 s), passed to the pass read at direct.py:3071-3073 `idle_timeout=self.read_idle_s(infrared, fast_infrared)`. tools/scan.py:196-222 and tools/scan_roll.py:323-337 validate --dpi > 0, correctability, the --out extension, --stops, --frames and --start-at before opening. tools/scan.py:47-63 `say_estimate` prints an estimate and warns past `FOREGROUND_S = 8 * 60`. Still open: tools/scan_roll.py has no estimate or foreground warning at all (grep finds no `estimate`), and scan.py only warns rather than refusing without an opt-in. `INFRARED_FLOOR_S` (direct.py:515) still guards no timeout: `read_idle_s` uses `UNTIED_INFRARED_IDLE_S`, and session.py:65 only re-exports it for estimates. CLAUDE.md ("212 s survives in the code as INFRARED_FLOOR_S because it guards a timeout") and the direct.py:508-514 comment ("Here, beside the read it guards") are therefore inaccurate. The bulk-transfer timeout `read_lines(timeout_ms=120_000)` (direct.py:~1767) and `PARTIAL_READ_TIMEOUT_S = 120.0` (usb_transport.py:75) are not derived from the pass, so a *silent* untied-IR wait (no ZLP) still gives up at 120 s. tools/fast_ir_probe.py:148 accepts `choices=sorted(FILM_TYPES)` including bw/kodachrome with no supports_infrared check before metering (PA-22).

**What is left:**

(1) scan_roll.py prints no runtime estimate and no >8-minute warning, and neither tool refuses a foreground run without an opt-in flag. (2) The bulk timeout (120 s) and the partial-payload timeout (120 s) remain below the ~220 s untied-IR floor. Only the ZLP/NoDataYet idle path was extended. (3) fast_ir_probe still accepts IR-blind films (PA-22). It now fails at metering for lack of a reference anyway (see P15). (4) Doc-mismatch: CLAUDE.md's INFRARED_FLOOR_S sentence and the direct.py:508-514 comment claim it guards the read. UNTIED_INFRARED_IDLE_S does.

## P17 -- Calibration starts without asking what is in the transport

**Status:** partly-fixed · **First audit:** [P17](../problems/P17-calibration-without-asking.md) · **Commits:** 1ac726f

**What the code does now:**

Window fixed: tools/gui.py:1967-1981 `on_calibrate_pressed` no longer submits and opens `ask_to_calibrate(for_scan=False)` instead (except when "reuse" would only load a cached file). The prompt adds an unticked `ttk.Checkbutton(... text="The film is in the transport")` at tools/gui.py:2094-2097, and `choose()` refuses with `if measures and not loaded.get(): ... return` at tools/gui.py:2110-2116. A bare Return at tools/gui.py:2134 goes through the same refusal. `on_calibrate` (tools/gui.py:1993-2008) is reached only from the prompt.

CLIs unchanged. tools/scan.py:270-274 goes from `info = s.inquiry()` straight to `s.ensure_shading(ref_path, reuse=args.reuse, skip=args.no_shading)` with no question and no --film-loaded flag. tools/scan_roll.py:189-196 `calibrate()` likewise runs `scanner.ensure_shading(...)` unasked, called at tools/scan_roll.py:568 after the seek. The seek (rps7200/session.py `seek`) moves nothing when the counter already reads the target, so with the default --start-at 1 and an empty transport reading 0 it passes, and the calibration runs on an empty transport. tools/uniformity.py:700-702 still prints "Metering on the EMPTY transport" and waits for `confirm("    press Enter with the transport empty: ")`. At 729-735 it still runs `scanner.calibrate_shading()` with no prompt to load film.

Media bit: `calibrate_shading` still reads state only for `if not self.read_state().warming_up: break` (rps7200/direct.py:2185-2192) and never consults `State.media_loaded`/`no_media` (rps7200/protocol.py:568-585). Because `_CommandLog.start()` runs first (direct.py:2181-2183, 455-495), the READ_STATE reply bytes land in `commands` of calibration.json via `archive_calibration` (direct.py:749-800). That happens only on the `ensure_shading` path, and nothing interprets or logs the bit.

**What is left:**

(1) tools/scan.py and tools/scan_roll.py calibrate with no film confirmation and no --film-loaded option. scan_roll's seek-before-calibrate protects only a start_at>1 roll, since a seek to frame 1 on an empty transport moves nothing and succeeds. (2) tools/uniformity.py still tells the operator to empty the transport, and still calls calibrate_shading() with no prompt to load film (reachable with --exposure-scale). It also does not archive that calibration's bytes, unlike ensure_shading. (3) The media bit is never logged or recorded as a field at calibration time. It survives only as a raw READ_STATE reply inside calibration.json's command list, on the ensure_shading path alone.

**New problem the fix introduced:**

Side effect of the P15 fix (direct.py:2948-2963 refuses a shaded pass with no reference, before any command is sent). tools/uniformity.py capture without --exposure-scale now asks for an empty transport, opens a fresh DirectScanner with no reference and calls auto_exposure(shading=True). That raises ShadingUnavailable as an uncaught traceback. The old empty-transport lazy calibration is gone, which is good, but the tool's default metering path is broken.

## P18 -- One roll is scattered across folders; unnamed rolls share one folder

**Status:** mostly-fixed · **First audit:** [P18](../problems/P18-roll-folder-identity.md) · **Commits:** 40bfd83, b4aa22c, 1056451, 0fd90c4, 69b04fc

**What the code does now:**

One derivation. `roll_dir(rolls, name)` (rps7200/session.py:811-834) sanitises through `_safe(name, fallback="")` (session.py:2946-2959: separators, '..' and Windows reserved names) and maps an empty or fully-stripped name to `new_roll_name(rolls)` (session.py:794-808, `%Y-%m-%d-%H%M%S` plus a -N suffix, checked against existing folders). Users: the session `_roll` (session.py:2305-2311, `out = roll_dir(self.rolls, job.name)`, or `job.out` from the window), the window's Roll button (tools/gui.py:2233-2234, `folder = roll_dir(self.session.rolls, typed)`, with `out=str(folder)` passed at 2336-2358), the sheet's `_roll_folder` (gui.py:2449-2470), `_write_approved(approved, folder)` into that same folder (gui.py:3162-3165, 3265-3284), and the CLI (tools/scan_roll.py:403-408). The label is `recorded_roll_name(out) or out.name` in both writers. Reopen restores the box with `self._show_roll_name(folder.name)` (gui.py:2907). The new name is shown in the box (gui.py:2268). The question before a walk or roll names the folder and what it holds (`folder_note`, gui.py:5804-5834: 'This walk replaces its walk -- the old survey is kept beside it as survey.json.bak'). The survey's previous version is kept by `write_manifest(..., keep_previous=True)` (session.py:886-922).

**What is left:**

(1) DP-05 is unchanged. tools/gui.py:8856-8889 pins demo paths only when --library/--rolls/--reference/--settings are absent, so `--demo --rolls rolls` or `--demo --library library` writes a demo walk's survey.json, prescans and approved.json, or demo entries, into the real folders. The comment at gui.py:8868-8874 ('--demo pins that under demo/') holds only for the default. (2) A fresh walk into a folder the operator typed still overwrites prescanNN.tif and replaces survey.json after the warning. The old strip's approved.json stays in the folder (merged on the next commission, read by read_survey on reopen), and its sheet state stored in gui-settings.json under the same folder name is restored onto the new strip (see P22). (3) `_roll_folder` maps a sheet from outside session.rolls onto `roll_dir(rolls, sheet.name)` (gui.py:2469). That returns an existing local folder of that name without checking it holds the same strip, and `carry_walk` returns [] when the target already has a survey.json (gui.py:5782). A roll opened from elsewhere that shares a local folder's name is therefore added to, and commissioned into, an unrelated roll.

**New problem the fix introduced:**

Item (3) above arises from the P18 review change that keeps outside rolls under session.rolls (b4aa22c/1056451). It is narrow: it needs a roll opened from another root whose folder name matches a local one, such as legacy date-named folders.

## P19 -- roll.json / survey.json / approved.json can lie about what was done

**Status:** fixed · **First audit:** [P19](../problems/P19-roll-manifests.md) · **Commits:** 46e60a8, a19da84, 7f0a6c5, 69c8dcb

**What the code does now:**

Done only once filed. `RollManifest.record(record, awaiting=True)` sets `record["done"] = False` (rps7200/session.py:1109-1127). `filed()` / `_apply` set `done=True` with `record["entry"]`, or `done=False` with `filing_error` (session.py:1129-1161). The window's roll passes `awaiting=scanned is not None` with `on_filed=... record_of.filed(n, entry, error)` (session.py:2622-2626, 2683). The CLI does the same (tools/scan_roll.py:771-781). Atomic writes: `write_manifest` writes `.{name}.part`, fsyncs it, copies the old file to `.bak` once per run and then `os.replace`s (session.py:886-922). `read_manifest` falls back to `.bak` and raises rather than returning {} (session.py:925-956). `earlier_manifest` sets an unreadable file aside as `.unreadable` and says so (session.py:969-985). --start-at merge: tools/scan_roll.py:574-590 reads `earlier_manifest(manifest_path)`, then `keep_first_numbering`, then `renumbered`, and carries `manifest["frames"]` forward. Renumbering keeps the original as `<name>.legacy` once (session.py:999-1013). approved.json is merged frame by frame, unreadable files are set aside, and `keep_previous=True` applies (tools/gui.py:3265-3330). `read_approved` reports a damaged file instead of returning silence (gui.py:5439-5450).

**What is left:**

Nothing in the listed sub-issues. Minor: a walk's `record["prescan"]` names prescanNN.tif before the writer thread has written it (session.py:2669-2680, file written asynchronously via `_file`). If that write fails, survey.json names a missing file; `walked_prescans` skips it (session.py:666). tools/scan_roll.py:255-263 (`--approved`) still reads survey.json with a bare `json.loads` and no `.bak` fallback, unlike `read_manifest`.

**New problem the fix introduced:**

`RollManifest._keep` now swallows every OSError on a manifest write (session.py:1061-1071). That is deliberate (7f0a6c5), but it removed the one thing that used to end a CLI roll on a full disk; see P24.

## P20 -- Turning or flipping during a walk corrupts the stored walk

**Status:** fixed · **First audit:** [P20](../problems/P20-rotation-during-walk.md) · **Commits:** 1892778, 85b75ea

**What the code does now:**

`_file` returns the turn and flip it actually applied (rps7200/session.py `_file` ... `return turn, flip`, and the writer receives the same `rotate=turn, flip=flip`). The walk records them per file: `record["prescan_rotation"] = turn; record["prescan_flipped"] = mirrored` from `walked_as` (session.py:2669-2680). Frames record `record["rotation"], record["flipped"] = arranged` (session.py:2656-2663). Earlier walks' frames get the file-level pair via `setdefault` before this walk overwrites it (session.py:2372-2380). Readers use the per-file pair through `prescan_arrangement(manifest, record)` (session.py:672-686): the window's read_survey (tools/gui.py:5189) and `scan_roll --approved` (tools/scan_roll.py:271-273). Export uses the per-frame `arranged` first (gui.py:5358-5367, from roll_summary gui.py:5580-5588). No duplicate survey entries: `remember_arrangement` (gui.py:3968-3989) no longer touches the survey, and `_into_survey` (gui.py:3947-3966) returns early when `any(r is result for r in self.survey)`.

**What is left:**

Nothing. Cosmetic: read_survey's docstring (tools/gui.py:5117-5127) still opens with 'the manifest's single `rotation` is what un-rotates them' before adding that the per-record pair wins, and the manifest's top-level and settings `rotation`/`flipped` are still the start-of-walk values (session.py:2395-2396, 2450-2451).

## P21 -- Roll export can deliver the wrong entry (a 300 dpi prescan as a frame)

**Status:** partly-fixed · **First audit:** [P21](../problems/P21-roll-to-library-join.md) · **Commits:** 8061a86

**What the code does now:**

Prescan-as-frame closed. Entries now carry `roll_membership(roll, number, kind, folder)` (rps7200/session.py:777-787), set with kind 'prescan' for walk prescans (session.py:2550-2552, tools/scan_roll.py:678-680) and 'frame' for frames (session.py:2612-2613, scan_roll.py:751-752). `roll_entry_index` skips `member.get("kind") != "frame"`, and for legacy entries skips `"prescan" in tags` (tools/gui.py:5503-5524). The Film-panel note no longer overrides the label: `notes = replace(job.notes, frame=roll_frame_label(name, number))` (session.py:2607-2611, 2560). There is one label format, `roll_frame_label` (session.py:769-775), used by scan_roll.py:766; the legacy `<roll>/<NN>` is parsed at gui.py:5520.

The join is still by roll *name* only. `summary["entries"] = dict(index.get(summary["roll"], {}))` (gui.py:5654), and `out.setdefault(roll, {})[number] = record_path.parent` over `sorted(root.glob("*/scan.json"))` (gui.py:5498-5512), so the newest entry id (timestamp prefix, rps7200/library.py:154-164) wins. The recorded `folder` in roll_membership and the manifest's per-frame `record["entry"]` (session.py:1155) are both ignored by export (`roll_exports`, gui.py:5335-5373).

**What is left:**

Fix-plan item 2 (export follows roll.json's entry id) is not done, and item 1 only half: membership records the folder but the join ignores it. A duplicated roll keeps its roll name (`recorded_roll_name`, session.py:836-852; `name = recorded_roll_name(out) or out.name`, session.py:2308-2311). Rescanning the duplicate, the use `on_duplicate_roll` documents at gui.py:2778-2784 ('so a strip can be rescanned at different settings'), files entries under the same roll name with newer ids. Export of the ORIGINAL roll then delivers the duplicate's rescans, named with the original's `settings.resolution` (gui.py:5368-5369). The same happens after Rename (the manifest keeps the old name, gui.py:2844-2866) when a new roll is later given the freed folder name. Both rolls join into one index and each frame number goes to whichever entry is newest.

## P22 -- Contact-sheet decisions leak between strips or are lost

**Status:** partly-fixed · **First audit:** [P22](../problems/P22-contact-sheet-state.md) · **Commits:** ec9c6ab

**What the code does now:**

Fixed. A new walk closes the old sheet before resetting state: `if dry: self._close_sheet()` (tools/gui.py:2279-2285), then `self.sheet_state = {}` (gui.py:2311). Explicit zero: `_write_approved` marks `{"as_walked": True}` for operator zeros (gui.py:3319-3323), and `read_approved` keeps it (`placed = bool(record.get("offset_mm")) or bool(record.get("as_walked"))`, gui.py:5461-5463). `_merge_kept` restores an operator source with no offset as 0.0. The Frame position window is `tk.Toplevel(sheet.top)` (gui.py:7229) and `_dismiss` destroys it (gui.py:8615-8617). Quit and open_roll now call `_close_sheet()` (gui.py:3452, 2938), which runs `_store_sheet_state` and writes `remembered["sheet"][<folder name>]` (gui.py:2666-2676).

Still wrong. What is stored is never read back where it matters. `open_roll` builds `self.sheet_state` from approved.json alone (gui.py:2961-2976) and opens the sheet from `out["offsets"]` (gui.py:2985-2991). `_recall_sheet_state` consults `remembered["sheet"]` only when `self.sheet_state` is empty (gui.py:2685-2692), which it never is after open_roll. The tests test_opening_another_roll_keeps_what_the_open_sheet_held / test_quitting_keeps_what_the_open_sheet_held (tests/test_gui.py:4497-4523) assert only that the state was written. And the key is the bare folder name (`_sheet_key`, gui.py:2606-2615). A fresh walk into an existing folder whose name the operator typed resets `sheet_state = {}`, sets `_sheet_roll` to that folder at walk end (gui.py:3712-3713) and auto-opens the sheet. The sheet then recalls the previous strip's ticks, positions and turns from `remembered["sheet"][name]`.

**What is left:**

(1) Decisions on an uncommissioned walk are saved to gui-settings.json on quit or when another roll is opened, but no reopen path reads them back: after a restart, Rolls... / --open-roll show approved.json only. They are lost to the operator, which is what GUI2-16 described. (2) Leak between strips. Keep a typed roll name, e.g. the film stock, for a second strip of the same film and answer 'No -- start a new sheet' in `_ask_keep_sheet` (the folder is still rolls/<typed>). The first strip's decisions are stored under that name by `_close_sheet` and restored onto the second strip's frames at the end of its walk. The old approved.json in the folder is also read on reopen. (3) Fix-plan item 3, 'decisions saved on every change', is not done: a crash or kill with a sheet open loses everything since it was opened.

**New problem the fix introduced:**

Leak (2) is new in its mechanism. The folder-name key was added with the quit/reopen persistence, and it replaces the old stale-sheet leak for the typed-name case.

## P23 -- Busy guards sit on buttons, not on actions; submit() cancels Stop

**Status:** partly-fixed · **First audit:** [P23](../problems/P23-busy-guards-and-stop.md) · **Commits:** 7b9df4d

**What the code does now:**

Fixed. `ScanSession.submit` no longer clears `_stop` (rps7200/session.py:1822-1828). `request_stop` drains the queue and re-queues a shutdown sentinel (session.py:1830-1852). `on_roll` checks `if self.busy:` (tools/gui.py:2181-2188). Moves and aim-clicks go through `_moving_refused` (gui.py:3343-3359, 3361-3363; `_aim` ends in `on_nudge`, gui.py:5070). on_scan_chosen checks busy (gui.py:3087-3095), and `on_reopen_survey` checks busy before opening the browser (gui.py:2555-2560).

Not fixed. The browser's Open, `_RollBrowser._open` -> `self.gui.open_roll(summary["folder"])` (gui.py:7730-7735), and `open_roll` itself (gui.py:2878-2890) have no busy check. A browser opened while idle and used during a walk replaces `self.survey`. The walk's later prescans are appended to it by `_into_survey`, and `_walk_ended` then sets `_sheet_roll` to the walk's folder (gui.py:3712-3713), so a reopened roll's frames get mixed into the running walk's sheet. `on_scan` / `on_prescan` (gui.py:2141-2170) have no busy check at all and rely on the button being disabled. `busy` is set only when the pump polls the worker's 'state' event (`POLL_MS = 120`, gui.py:244; `_set_busy` at 3615), so a double click inside that window queues two scans. The guard was not moved into the session (fix-plan 1): `submit` still accepts anything. `_roll_is_busy` (gui.py:2693-2707) checks only `self.busy` and `_loaded_roll`, not the session's `FrameWriter`/`RollManifest.pending()`, which keep filing into the roll folder after the job's busy=False (session.py:1946-1967, 2327-2340).

**What is left:**

(1) Rolls browser Open during a job (GUI1-22/GUI2-14) is still unguarded. (2) Scan/Prescan buttons are guarded only by widget state, which lags the worker by up to a poll, so double presses still queue duplicates; they can be stopped now. (3) Aim-click still does not check that the prescan clicked shows where the film is now (`_aim` never compares `self.current.position` with `self._transport`, gui.py:5014-5070). (4) Delete/Rename can hit a roll folder the writer is still filing into: the job is finished but the last frames are still queued. They also do not protect the just-walked `_sheet_roll`, which is not `_loaded_roll`, so renaming it strands the open sheet's commission path. (5) The Calibrate prompt is non-modal and its 'Calibrate now' submits without a busy check (gui.py:1993-2001). That only queues, but it is a job submitted while busy.

**New problem the fix introduced:**

Stale comment: tools/gui.py:2324-2325 still says 'The button this handler is behind is disabled while busy, so this cannot race a job that is still running' about on_roll, which is reached by a key. It is true now only because of the explicit check at 2185.

## P24 -- A full disk does not stop a roll; quitting kills writes mid-file

**Status:** partly-fixed · **First audit:** [P24](../problems/P24-disk-full-and-quitting.md) · **Commits:** 7b9df4d, 7f0a6c5, 69c8dcb

**What the code does now:**

Window fixed. `ScanSession._filed` calls `self.request_stop()` when a Roll frame cannot be filed (rps7200/session.py:1995-2015), and the manifest records `filing_error`/`done: False` (session.py:1157-1161). `on_close` offers 'stop after the frame in flight' (tools/gui.py:3430-3456). `_wait_to_quit` waits for Save all / Export threads (gui.py:3463-3485), and the worker's finally joins the FrameWriter after closing the device (session.py:1969-1993). A corrupt gui-settings.json is moved aside (`_keep_aside`, rps7200/settings.py:73-78, 88-101), and a failed save is said (gui.py:724-729).

Not fixed. No free-space check exists anywhere (no disk_usage/statvfs in rps7200/ or tools/). The CLI builds `writer = FrameWriter()` (tools/scan_roll.py:459). Its loop's only stop is `should_stop=interrupt.requested` (scan_roll.py:627), and `writer.errors` is printed only after `writer.finish()` at the end (scan_roll.py:796-811). Library filing failures reach `record_of.filed` silently. `library.save` still calls `reindex(root)` after the entry is complete (rps7200/library.py:396-399), and reindex writes index.json non-atomically (library.py:1062-1064). A reindex ENOSPC therefore raises out of `FrameWriter._write` before the delivered copies. `_run` then reports a complete entry as unfiled (session.py:1587-1597), which stops the roll and marks the frame not done. The session log is still only a Tk text widget (`_say`, gui.py:3874-3878). Nothing is persisted to library/logs.

**What is left:**

(1) tools/scan_roll.py does not stop on a failed filing and says nothing until the end. With the library on a full volume it scans every remaining frame and discards it. (2) No free-space estimate before each frame (fix-plan 1). (3) No error dialog for a failed filing. The roll ends with the final state `stopped after frame N, as asked` (session.py:2689), which says the operator asked for a stop that the disk caused. (4) A reindex failure after a complete library entry is reported as a failed filing, and the frame's delivered copies are not written. (5) No persisted session log (fix-plan 4). Dropped raw bytes ('raw bytes do not describe this image', session.py `_file`) are still only a log line. (6) The unreadable-settings set-aside is silent in the window, since `settings.load` has no reporter.

**New problem the fix introduced:**

Regression in the CLI from 7f0a6c5. Old tools/scan_roll.py rewrote roll.json in the loop with a bare `manifest_path.write_text` (83dbb22 scan_roll.py:326-329, 585). With rolls/ on the full volume that raised ENOSPC and ended the roll. `RollManifest._keep` now catches the OSError and carries on (session.py:1061-1071, 1109-1127), so a CLI roll on a full shared volume scans and discards every remaining frame (about 6 min each at 3600 dpi). Until the end, the only sign is the per-frame manifest message on stderr.

## P25 -- Reopened frames collide, and Delete removes real library entries

**Status:** mostly-fixed · **First audit:** [P25](../problems/P25-reopened-frames.md) · **Commits:** e83f3df, 575e113, ec9c6ab

**What the code does now:**

(1) Seq collision fixed: tools/gui.py:5101 `_REOPENED_SEQ = itertools.count(-1, -1)` and read_survey uses `seq=next(_REOPENED_SEQ)` (tools/gui.py:5191), so two reopened rolls (or one reopened twice) never share a seq. (2) Delete of a real entry under --demo/make run-sheet closed without a demo branch: on_delete (tools/gui.py:4465-4478) `if entry and entry.exists() and not _within(entry, self.session.root): ... entry = None` and `_within` (tools/gui.py:8709-8717) resolves both paths; under --demo session.root is demo/library (tools/gui.py:8856, 8884), so a reopened rolls/aligned-strip frame whose entry is in library/ is left alone. (3) Per-frame turns leaking into the next plain roll: the Roll button now drops them on a real roll -- tools/gui.py:2269-2277 `if not dry: self.orientations = {key: turn for key, turn in self.orientations.items() if key[0] != "frame"}`. (4) No-walk reopen: restorable() now restores a last-frame box instead of a count -- tools/gui.py:5325-5331 `out["last"] = "" if count is None else str(first + int(count) - 1)` -- so the Roll button no longer runs past the roll's own last frame, and open_roll sets `self.v_startat.set(str(remaining[0]))` (tools/gui.py:2919-2920) and points the roll box at the folder (`self._show_roll_name(folder.name)`).

**What is left:**

(a) Fix-plan item 2 is only half done: an entry inside session.root is still removed permanently -- `shutil.rmtree(entry); library.reindex(entry.parent)` (tools/gui.py:4492-4495) -- with no .trash/, one 'No' away. Outside the demo that includes a reopened roll's walk entry in the real library (the dialog only adds 'the entry is the one its walk filed'), and nothing updates that roll's approved.json `reference_entry`, which is left pointing at a deleted folder. (b) Reopening a roll that had no walk still rescans done frames when the done set is not contiguous: remaining = `[n for n in out["wanted"] if n not in done]` (tools/gui.py:2901), start is remaining[0], last is the original last, and the Roll submitted at tools/gui.py:2337-2352 carries no `only=`. The session does not skip frames already done in roll.json either. Example: done {1,2,4,5} of 1-6 scans 3,4,5,6, so 4 and 5 are scanned again. (c) `self.v_startat.set(...)` runs only `if restored:` (tools/gui.py:2919), but the dialog always says '"first frame" is set to frame {remaining[0]}' (tools/gui.py:2926-2927).

**New problem the fix introduced:**

None of substance. The Roll-button orientation drop (tools/gui.py:2269-2277) also clears per-frame turns when the plain Roll continues a sheet-commissioned roll in the same folder. That is intended, but it means a continuation's frames follow the session's arrangement, not the sheet's per-frame turns.

## P26 -- Every demo entry is corrected pixels labelled raw

**Status:** fixed · **First audit:** [P26](../problems/P26-demo-files-corrected-as-raw.md) · **Commits:** a37235b, 86b48b7, b9ac94f, 4b7ad6d

**What the code does now:**

DemoScanner now has the real contract. `self.last_pixels_raw: np.ndarray | None = None` is declared (rps7200/demo.py:295). `_forget_last_pass` (rps7200/demo.py:823-829) empties `_capture`, `last_pixels_raw`, `last_raw` and `last_raw_layout` at the start of every prescan/scan (called at demo.py:584 and 687). `_take` (rps7200/demo.py:846-909) corrects last: `self._capture = {"reference": reference, "ccd_mask": mask, "raw": self.last_raw, ...}; self.last_pixels_raw = raw; ... image, report = apply_shading(raw, reference, mask)`. The pass's bytes are re-encoded from those very pixels and decoded by the driver (`blob = encode_index(raw, reversed=reversed_now); upright, direction = DirectScanner.decode_index(blob, params, channels)`, demo.py:624-626). A picture that was already corrected when stored (prescan.tif / legacy scan.tif) hands back no raw and no bytes, with `report = {"applied": CORRECTED_WHEN_STORED, ...}` (demo.py:876-880). That report is truthy, so session._write files it with `corrections = ["shading"]` (rps7200/session.py:1642-1653). A resized or shifted pass gets a reference re-indexed to its own columns (`_pass_reference`, demo.py:977-1019), so library.corrected reproduces what was shown. Every pass meta carries `"demo": True` and `"demo_source"` (demo.py:903-907), which library.save keeps in `extra` (rps7200/library.py:330-336). Roll frames get raw pixels because the roll is now the driver's loop, whose RollFrame takes `raw_image=self.last_pixels_raw, raw_prescan=raw_prescan` (rps7200/direct.py:4138-4139). Tests go through the session: test_what_a_demo_session_files_is_raw_and_reconstructs (tests/test_demo.py:1231-1274) and test_a_demo_walk_of_calibrated_entries_is_filed_as_raw_reads (tests/test_demo.py:1351-1374) both assert `library.reconstruct(path)[1].startswith("identical")`. The module docstring (demo.py:15-33) now matches the code.

**What is left:**

nothing material. Two labelled divergences remain. (a) A source with no calibration returns uncorrected pixels to a `shading=True` request, with `skipped = UNCALIBRATED_SOURCE` (demo.py:890-891), where the real scanner, once calibrated, always corrects. (b) A CORRECTED_WHEN_STORED source returns corrected pixels even to `shading=False` (demo.py:881-884). It only logs this, and those pixels are still filed labelled corrected.

**New problem the fix introduced:**

Low / info. When a demo pass is resized or shifted, the shading.npz filed with it is a reference synthesized to the pass width, filed with `ccd_mask=None` (demo.py:778-779, 821-824), not a device reference. It is exact for re-correction and the entry is flagged demo, but a demo library's calibration files do not describe any device calibration.

## P27 -- The demo accepts what the scanner refuses and runs its own roll loop

**Status:** mostly-fixed · **First audit:** [P27](../problems/P27-demo-refusals-and-roll-loop.md) · **Commits:** a37235b, 9441aed, 6b6bcc5, b9ac94f

**What the code does now:**

Refusals: `_refuse` (rps7200/demo.py:519-530) uses `if shading and not DirectScanner.correctable_at(resolution, frame): raise DirectScanner.uncorrectable(resolution, frame)` and then `_refuse_uncalibrated` -> `raise DirectScanner.uncalibrated()`, so 7200 dpi is refused in the driver's own words. shading=False now returns the raw pass (`skipped = SHADING_SKIPPED_EXPLICIT`, demo.py:886-887). position() returns None once the transport is closed (demo.py:373-383). Roll loop: `_drivers_roll = DirectScanner.scan_roll` (demo.py:790), and scan_roll does `yield from self._drivers_roll(frames=..., resolution=..., only=only, first_index=first_index, **kw)` (demo.py:751-753). That gives the demo the driver's per-frame failure net, max_failures, blank end, prescan_resolution, stop handling and metering (`_drivers_metering = DirectScanner.auto_exposure`, demo.py:791), along with `_aim_frame`/`_rejudge_for`/`_hold_to_approved` (demo.py:787-796). Scans follow the moved film: `columns = np.roll(..., self._shift(w))` in `_fit` is used for every pass (demo.py:947). RGBI always gives 4 planes (clear IR added, demo.py:958-964). Prescans are 8-bit via `_at_depth(raw, depth)` (demo.py:957). Loop-parity tests exist: test_the_demo_roll_is_the_drivers_loop and test_the_demo_runs_the_drivers_roll_and_metering_not_copies (tests/test_demo.py:1651, 1686), and test_a_roll_at_7200_dpi_fails_frame_by_frame_as_the_scanners_does (tests/test_demo.py:1441).

**What is left:**

(a) Fix-plan item 1 was not done. DemoScanner is still a scanner-level stand-in with its own `scan`/`prescan` (demo.py:567-715), not a fake usb_transport, so the driver's scan()/prescan() command sequence, byte-14 handling and refusal ordering are not exercised by the demo. (b) The infrared refusal is a retyped copy of the driver's message, and it has already drifted: demo.py:666-674 lacks the chromogenic C-41 sentence that rps7200/direct.py:2923-2929 has. (c) The backlash model retypes the unit, `swallowed = min(abs(asked), 2.2 * 0.1057)` (demo.py:451), instead of protocol.MM_PER_UNIT. (d) Film moves are simulated by `np.roll`, which wraps the far edge's picture into the near edge (demo.py:947). The detectors are fed prescans no device produces (FE-07). (e) `scan()` records `frame` in meta but never crops to it, and swallows `depth` into `**kw`, so a caller passing `depth=8` or a window gets a 16-bit full frame. No current GUI path does either.

**New problem the fix introduced:**

none found

## P28 -- --look-only without --demo drives the real scanner

**Status:** mostly-fixed · **First audit:** [P28](../problems/P28-look-only-drives-real-scanner.md) · **Commits:** b8a5584, a37235b

**What the code does now:**

CLI refusal: `if args.look_only and not args.demo: ap.error("--look-only needs --demo: ...")` (tools/gui.py:8850-8854) runs before any session or window is built. Demo refusals: `_need_film` raises `UsbError("there is no film in the transport, ...")` when `self._no_film` (rps7200/demo.py:1023-1041). It is called by prescan (demo.py:583), scan (678), scan_roll (746), advance (386), retreat (408) and nudge (432), so under `--look-only --demo` a prescan no longer returns a stored photograph of absent film. no_film is wired from `no_film=args.look_only` (tools/gui.py:8905).

**What is left:**

Under `--look-only --demo`, calibration is not refused. `DemoScanner.ensure_shading` (rps7200/demo.py:500-508) never calls `_need_film`: it sleeps, sets `_calibrated = True` and reports 'shading calibrated (demo)'. The operator ticks 'film loaded' in ask_to_calibrate (tools/gui.py:2030+) and the demo accepts an empty-transport calibration. CLAUDE.md names that state as one that preceded a wedge. Fix-plan item 2 listed `calibrate` among what should raise. The sheet text under look_only, 'will say there is no film when it reaches for it' (tools/gui.py:7948-7953), is therefore not true of Calibrate.

**New problem the fix introduced:**

none

## P29 -- Bracket merging judges the wrong domain and mixes RGBI with RGB

**Status:** mostly-fixed · **First audit:** [P29](../problems/P29-bracket-merge.md) · **Commits:** 52be760, ced6170

**What the code does now:**

Saturation is now judged on sensor pixels. In rps7200/bracket.py:196-220, `confidence(sample, sensor=None)` does `clip_w = np.minimum(clip_w, _clip_weight(sensor))`. solve_relation takes `ref_sensor`/`other_sensor` with `usable &= np.asarray(sensor).reshape(-1) < CLIP_START` (bracket.py:282-315). merge_bracket takes `sensor_frames` or `sensor_rails` (bracket.py:356-427). tools/scan.py collects each pass's `last_pixels_raw` in on_pass (tools/scan.py:316-328) and merges with `merge_bracket(frames, ratios, sensor_frames=sensor)` or `sensor_rails=sensor` (tools/scan.py:390-393). --ir refused: `if args.bracket and args.ir: ap.error("--bracket with --ir: a bracket is RGB only. ...")` (tools/scan.py:197-212). A single --exposure-scale is refused: `if args.bracket and len(parts) != 3:` -> ap.error (tools/scan.py:217-227). Membership is recorded per pass: `meta["bracket_index"]`, `bracket_ratio`, `bracket_passes`, `bracket_stops` (rps7200/direct.py:2821-2824), and library.save keeps them in `extra`.

**What is left:**

(a) Fix-plan item 4 (spool bracket passes to disk) was not done. With the library on, `pending` holds every pass's raw pixels plus its capture (`"raw": self.last_raw` bytes in memory, rps7200/direct.py:899-904), and scan_bracket's default `retain=True` (direct.py:2748, 2836-2839) also keeps every corrected frame. A high-dpi bracket therefore still needs about N x (raw pixels + raw bytes + corrected) in RAM (tools/scan.py:255-263, 283-296). (b) The per-channel fit was not done. Only tools/scan.py refuses --ir, and `DirectScanner.scan_bracket(infrared=True)` still produces the mixed RGBI/RGB bracket (direct.py:2806-2815), which merge_bracket would merge with one green-fitted relation if any other caller used it. (c) There is no bracket identifier linking one bracket's entries (only index/ratio/passes/stops). The merged image and its MergeStats go only to the delivered file's sidecar JSON (tools/scan.py:395-400, 424-425), never to the library.

**New problem the fix introduced:**

Low: `sensor_rail` (bracket.py:152-182) is a lossy uint8 encoding of the sensor sample used for the merge when the library is off. It is not stored, so nothing is lost on disk, but a --library-off bracket merge cannot be recomputed later: nothing was filed.

## P30 -- The comparison files and several metrics do not measure what ships

**Status:** partly-fixed · **First audit:** [P30](../problems/P30-comparison-files-and-metrics.md) · **Commits:** ceb9081, 8fa4d73, 3f5cce0, f42ca03, 07bbe5a, 15235b0

**What the code does now:**

make_comparison now takes a library entry and uses the delivered path. `raw, record = library.load(entry); corrected, info = library.corrected(entry)`, it refuses when `state != "applied"`, and it writes all three through `export.write(out / name, image, resolution=resolution)`, then measures `tiff.read(str(out / NAMES[0]))` read back from disk (tools/make_comparison.py:147-190). previews/ is created (`folder.mkdir(parents=True, exist_ok=True)`). filing_load_test has a real verdict: `verdict()` judges paired differences against `line = limit * base` and returns 'unsafe' when `lo > line` (tools/filing_load_test.py:115-158). dpi_analysis is kept to one domain and exposure_headroom to 16-bit passes, looked up through the mask (commit 3f5cce0). The metrics input contract is enforced by `_check` (.claude/skills/measure-scan-quality/scripts/metrics.py:18-34). noise_split filters both terms alike (`random_sigma = float(np.std(_highpass(x - y)[mask]) / np.sqrt(2))`, metrics.py:108-110), and `ceiling` raises when `random_sigma >= total_sigma` (metrics.py:134-140).

**What is left:**

(a) noise_split still does not register or gain-match the pair (metrics.py:104-111), and the shares the skill quotes are stale by its own docstring (metrics.py:100-103). (b) MSQ-05 is open: agreement_z calls `solve_relation(a[..., channel], b[..., channel])` with no sensor pixels, so bracket's absolute DN gates are applied to corrected input, and the median is taken over all `mask` pixels, including ones the fit excluded (metrics.py:145-175). (c) MSQ-07 is open: there is no cross-frame sensor test, and colour_deviation is still indexed by output column (metrics.py:178-199). (d) PA-08 is open. tools/registration_margin.py validates `register(a, b, max_shift=reach)` upright only, with a pixel `MAX_DY_PX` gate (tools/registration_margin.py:193-222). measure_shift_mm gates on the stronger of upright and row-flipped, with a millimetre `MAX_DY_MM` (rps7200/framing.py:1091-1117). (e) PA-11 is open: exposure_probe's `levels_of` re-detects `metering_slice(image)` on the delivered pass (tools/exposure_probe.py:161-170), not the region metering used. (f) PA-07 is partly open: exposure_headroom still models every film with the negative's `MEASURED_BLUE_RATIO = 4.98` (tools/exposure_headroom.py:86, 182). (g) filing_load_test measures wall time only. It cannot see the read stall or wedge that CLAUDE.md's hazard is actually about.

**New problem the fix introduced:**

doc-mismatch (low-medium). The make_comparison docstring says '`1_` is `library.load`, the raw decode ... So a change to the decode, to `apply_shading` or to the write path shows up here' (tools/make_comparison.py:13-18). CLAUDE.md:217-220 and README.md:208-210 say 1_ is 'the raw decode'. In the code, `library.load` reads the stored `scan.tif` (rps7200/library.py:531-533), which is the decode made at filing time. Today's decode_index never runs, so a decode regression cannot appear in the three files. Only `tools/library.py reconstruct` would catch it.

## P31 -- Framing: two geometry models, a search window too small, one voter can move film

**Status:** partly-fixed · **First audit:** [P31](../problems/P31-framing-geometry-and-detectors.md) · **Commits:** be3b0ff, 7c5c185, 879151a, d401f37

**What the code does now:**

Fixed sub-issues. (1) Member exceptions are contained: vote `_member` wraps each member in `except Exception as exc` and abstains (tools/frame_edges/vote.py:114-135). (2) Unreadable prescan dpi is now said up front: `READ_AT_DPI = (300,)` and `unread_at(dpi, film)` (tools/frame_edges/propose.py:56, 64). The window adds that sentence to the walk/roll question (tools/gui.py:2225). scan_roll refuses `--correct` at such a dpi, `if unread and args.correct: ap.error(...)` (tools/scan_roll.py:387-388), and warns otherwise. (3) `--approved` prescans at the walk's recorded resolution and refuses a different --prescan-dpi (tools/scan_roll.py:350-362). It un-orients each walk prescan, `preview.unorient(tiff.read(...), *prescan_arrangement(manifest, record))` (tools/scan_roll.py:270-275), and reads the walk's own film (tools/scan_roll.py:286-287).

**What is left:**

(a) One member can still move film. `lone_gap` returns an EDGE from one member (tools/frame_edges/vote.py:92-103), and `vote_v2` uses it (vote.py:106-111). `centring` labels it and still returns the offset: `note.update(source="unconfirmed" if lone else "measured", ...); return columns_to_mm(columns, width), note` (propose.py:174-177). `StripWalk.judge` returns `self.reader.judge(...)` without combine (rps7200/framing.py:1729-1733). `_aim_frame` moves on any non-None decision and never reads `detail["source"]` (rps7200/direct.py:3457-3515). Its log still reads keys the reader note lacks, `agreed = "+".join(detail.get("agreed", []))` (direct.py:3469) and `(from {detail.get('chose', '?')})` (direct.py:3508). (b) SEARCH_MM is unchanged: `SEARCH_MM = 9.0` (rps7200/framing.py:857), and its comment says 'just over MAX_TRAVEL_MM'. But MAX_TRAVEL_MM = FINE_MAX_MM = STEP_MM x (87 + 1.84) ≈ 0.1057 x 88.84 ≈ 9.39 mm (rps7200/session.py:96, tools/gui.py:221, 241), so a full param-87 hold still cannot be verified. (c) Two geometry models remain. `FRAME_WIDTH_MM = 36.0` and `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0` (framing.py:586, 740) drive StripWalk whenever the reader is None, as for slides and Kodachrome: `StripWalk(reader=edge_reader(film) if edge_reader else None)` (direct.py:3896), and walk_reader returns None for non-negatives. frame_edges uses `FRAME_WIDTH_UNITS = 350.6` (framing.py:1957). (d) `scan_roll --approved` still holds every proposal, including 'unconfirmed' and 'neighbours', after printing only counts: `held = {n: Approved(..., source=...) for n, im in frames if n in offsets}` (tools/scan_roll.py:293-297, 364-368). It also still ignores the walk's approved.json (operator-set positions) and re-proposes from scratch.

**New problem the fix introduced:**

none found

## P32 -- The pcap reader hands back keystroke payloads, and the test asserts it does

**Status:** fixed · **First audit:** [P32](../problems/P32-usbpcap-returns-keystrokes.md) · **Commits:** 0f88917

**What the code does now:**

The all-records iterator is now private: `def _records(raw: bytes) -> Iterator[Packet]` (rps7200/usbpcap.py:114-145). The public `packets(raw, devices)` requires device addresses and filters on transfer type: `if packet.device in named and packet.transfer in _PAYLOAD_TRANSFERS: yield packet`, where `_PAYLOAD_TRANSFERS = frozenset({CONTROL, BULK})` (usbpcap.py:43, 148-160). setups() returns only 8-byte setup headers (usbpcap.py:163-181). tools/parse_capture.py:71 calls `packets(raw, devices)` with the scanner's addresses. Tests assert the keystroke never comes back from any public function: test_every_public_function_is_asked, test_a_keystroke_is_never_handed_back, and test_naming_the_keyboard_yields_nothing_of_it, which asserts `list(packets(raw, {KEYBOARD})) == []` and `{p.device for p in packets(raw, range(128))} == {SCANNER}` (tests/test_usbpcap.py:220-260). CLAUDE.md's claim now holds.

**What is left:**

Minor. packets() still returns CONTROL-transfer payloads for any address the caller names, keyboard included (usbpcap.py:157-160). A HID keyboard's control traffic (SET_REPORT LED state, or a GET_REPORT input report on EP0) would come back if someone passes `range(128)`. The synthetic bus in tests/test_usbpcap.py gives the keyboard only an INTERRUPT record (lines 107, 143), so this case is untested. The docstring 'naming the keyboard's address by mistake still yields none of its keystrokes' (usbpcap.py:153-155) is true only for interrupt-endpoint reports.

**New problem the fix introduced:**

none
