# ScanSession, FrameWriter and rolls

Area key `session-roll`. 31 findings: 3 critical, 11 high, 12 medium, 5 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: rps7200/session.py (ScanSession worker, FrameWriter, Roll/_roll, seek/rewind, renumbered, _file/_deliver, approved/roll/survey manifests, _open_scanner seam, edge_reader), followed into direct.py (scan, prescan, scan_roll, _hold_to_approved, capture_record, read_planes), library.py (save/corrected/verify), demo.py (DemoScanner at the seam) and the GUI call sites in tools/gui.py that submit jobs and write approved.json.


Judged from code only. The central requirement (library holds exact raw data for everything) is violated in several concrete paths: (1) every single Scan submitted from the GUI files the shading-CORRECTED image as scan.tif while its record says raw, so library.corrected() applies shading a second time for the full-resolution view and Save As; (2) a failure writing the delivered TIFF/JPEG (output folder full/unplugged) aborts the library entry for that frame entirely; (3) a short/truncated pass has its raw bytes thrown away by the session's shape guard, which compares declared lines against decoded lines; (4) real-roll prescans are stored only as corrected 8-bit prescan.tif with no raw bytes, raw pixels or CCD mask, and failed-frame prescans, arrival prescans and hold-loop verification prescans are never filed (debug filing is forced off so RPS7200_DEBUG cannot rescue them); (5) roll.json marks a frame done before (and regardless of whether) the writer filed it, and is written non-atomically, and an unreadable earlier roll.json is silently discarded and overwritten on resume.


State/numbering: a roll commissioned from the sheet with an empty name writes approved.json to rolls/roll/ while the roll goes to rolls/<today>/; a reopened roll resumed from the GUI is written into today's date folder, not the folder that was reopened; unnamed walks/rolls of one day share one folder, overwrite frameNN.tif/prescanNN.tif and inherit the previous strip's approved.json/roll.json. Typing anything into the Film 'frame' note gives every roll entry the same film.frame, which makes all of them one library.signature (duplicates --delete keeps one) and breaks the GUI's roll-to-entry join.


Safety: the GUI session keeps the device open for the window's lifetime, so every single scan/prescan, and the last frame(s) of every roll, is gzipped by FrameWriter while the device is open and idle -- the state CLAUDE.md and the module docstring say is avoided. Both worker threads are daemons, so Ctrl+C or any main-thread exit abandons a read.


Concurrency/user errors: submit() clears a pending stop; the roll keyboard shortcut (Ctrl/Cmd-B) and aim-click bypass the busy guard, queue a second job and cancel a requested stop; rotating a picture during a walk changes the orientation prescanNN.tif are written in while the manifest keeps the old one; an unvalidated Calibrate.mode from gui-settings.json raises outside the worker's try and kills the worker, after which every submit silently goes nowhere. The session log (the only place dropped raw bytes and failed filings are reported) is not persisted.


Demo: DemoScanner.scan_roll is a separate loop that lacks per-frame error handling/max_failures, blank end-of-film detection, prescan_resolution, should_stop in the hold loop, observe on held frames, and raw_image/raw_prescan/prescan_before -- so the session's error-frame, resume-a-failed-frame and raw-filing paths are never exercised by --demo, and every demo-filed entry holds corrected pixels labelled raw (demo.py's claim that its entries reconstruct like any other is false for any source entry with a shading reference).

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [SR-01](#session-roll-sr-01) | critical | data-integrity | GUI single Scan files shading-corrected pixels as the raw scan.tif; full-res view and Save As are double-corrected | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [SR-02](#session-roll-sr-02) | critical | data-integrity | A failed write of the delivered TIFF/JPEG (output folder or rolls/) aborts the library entry: raw bytes of the scan are lost | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [SR-A1](#session-roll-sr-a1) | critical | data-integrity | Every 7200 dpi pass filed through ScanSession loses its raw bytes: the column-stagger realignment makes the shape guard reject them | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [SR-03](#session-roll-sr-03) | high | data-integrity | roll.json marks a frame done before, and regardless of whether, it was filed | [P19](../problems/P19-roll-manifests.md) |
| [SR-04](#session-roll-sr-04) | high | data-integrity | A short or anomalous pass loses its raw bytes: the shape guard compares declared lines with decoded lines | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [SR-05](#session-roll-sr-05) | high | data-integrity | Real-roll prescans are kept only corrected; failed-frame, arrival and hold-loop prescans are never filed; debug filing is forced off | [P08](../problems/P08-passes-never-filed.md) |
| [SR-06](#session-roll-sr-06) | high | hardware-safety | The GUI session gzips entries (and does all other heavy local work) with the device open and idle | [P13](../problems/P13-gzip-with-device-open.md) |
| [SR-07](#session-roll-sr-07) | high | data-integrity | roll.json/survey.json written non-atomically; an unreadable earlier roll.json is silently discarded and overwritten on resume | [P19](../problems/P19-roll-manifests.md) |
| [SR-08](#session-roll-sr-08) | high | bug | Sheet decisions and resumed rolls land in different folders from the roll they belong to | [P18](../problems/P18-roll-folder-identity.md) |
| [SR-09](#session-roll-sr-09) | high | user-error | Unnamed walks and rolls of one day share rolls/<date>/: frames and prescans overwritten, stale approved.json and roll.json applied to a different strip | [P18](../problems/P18-roll-folder-identity.md) |
| [SR-10](#session-roll-sr-10) | high | data-integrity | A value in the Film 'frame' note replaces every roll frame's label: all entries share one library signature and the roll-entry join breaks | [P21](../problems/P21-roll-to-library-join.md) |
| [SR-11](#session-roll-sr-11) | high | demo-divergence | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in failure handling, end detection, prescan resolution, stop and raw fields | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [SR-A2](#session-roll-sr-a2) | high | data-integrity | Unlabelled single scans and prescans from the window share one library signature; `duplicates --delete` destroys distinct pictures | [P11](../problems/P11-duplicates-delete-destroys-scans.md) |
| [SR-A3](#session-roll-sr-a3) | high | data-integrity | Any walk into an existing roll folder replaces survey.json wholesale: a partial re-walk erases the earlier walk's frames | [P18](../problems/P18-roll-folder-identity.md) |
| [SR-12](#session-roll-sr-12) | medium | demo-divergence | Demo-filed entries hold shading-corrected pixels labelled raw, and demo prescans carry a stale capture record | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [SR-13](#session-roll-sr-13) | medium | concurrency | submit() cancels a requested stop, and two GUI paths queue jobs while a roll runs | [P23](../problems/P23-busy-guards-and-stop.md) |
| [SR-14](#session-roll-sr-14) | medium | data-integrity | Rotating or flipping during a walk changes how prescanNN.tif is written, but the manifest keeps the orientation from the start | [P20](../problems/P20-rotation-during-walk.md) |
| [SR-15](#session-roll-sr-15) | medium | user-error | Roll name is used as a path component without sanitising | [P18](../problems/P18-roll-folder-identity.md) |
| [SR-16](#session-roll-sr-16) | medium | data-integrity | Library records drop the inquiry, the transport position and the roll index; single scans record no position and nudges are recorded nowhere | [P09](../problems/P09-record-missing-parameters.md) |
| [SR-17](#session-roll-sr-17) | medium | data-integrity | Resuming an old roll rewrites its frame numbers in place, destroying the originally recorded numbering | [P19](../problems/P19-roll-manifests.md) |
| [SR-18](#session-roll-sr-18) | medium | hardware-safety | Daemon worker threads: Ctrl+C or any main-thread exit abandons a read mid-scan and drops queued frames; closing the window mid-roll waits for the whole roll | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [SR-19](#session-roll-sr-19) | medium | error-handling | A dead worker leaves the window accepting jobs that never run; an unvalidated Calibrate.mode kills the worker | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [SR-20](#session-roll-sr-20) | medium | error-handling | The only report of dropped raw bytes, failed filings and renumbering doubts is an unpersisted log widget | [P24](../problems/P24-disk-full-and-quitting.md) |
| [SR-21](#session-roll-sr-21) | medium | error-handling | An exception in the injected edge reader or the aiming code costs the frame's scan or ends the roll | -- |
| [SR-22](#session-roll-sr-22) | medium | data-integrity | Partially written library entries are invisible to entries()/verify(); a reindex failure reports a complete entry as unfiled | [P10](../problems/P10-non-atomic-writes.md) |
| [SR-26](#session-roll-sr-26) | medium | data-integrity | The two roll writers label library entries differently; the GUI join only parses the session's form | [P21](../problems/P21-roll-to-library-join.md) |
| [SR-23](#session-roll-sr-23) | low | doc-mismatch | Stop is not checked between a roll frame's prescan, metering and scan, nor during the seek | -- |
| [SR-24](#session-roll-sr-24) | low | bug | False 'could not be filed: None' log for prescan_before and for a session without a library | -- |
| [SR-25](#session-roll-sr-25) | low | bug | Output-folder copy of a corrected walk's prescan is overwritten by its 'before' picture | -- |
| [SR-27](#session-roll-sr-27) | low | doc-mismatch | Stale comments about the SLIDE law, and a millimetre API contrary to the units rule | -- |
| [SR-28](#session-roll-sr-28) | low | concurrency | FrameWriter: bounded queue blocks the scanner thread with the device open; no liveness check; errors drained only at session close | -- |

## Findings in full

<a id="session-roll-sr-01"></a>

### SR-01 -- GUI single Scan files shading-corrected pixels as the raw scan.tif; full-res view and Save As are double-corrected

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:1085-1100`, `rps7200/direct.py:2708-2733`, `rps7200/direct.py:2818`, `rps7200/library.py:228`, `rps7200/library.py:385-391`, `tools/gui.py:2054-2061`, `tools/gui.py:3864`, `tools/gui.py:4110`

**Doc claim:** CLAUDE.md 'The library holds raw pixels; everything else is corrected ... library.save takes raw pixels and corrections= is how a caller admits it is handing over something else'; session.py:1086-1087 comment 'The raw pixels where the job carries them ... CLAUDE.md's rule that the library holds raw'

Every Scan job from the window (on_scan, gui.py:2054) stores the flat-fielded image as scan.tif while the entry claims to be raw and carries shading.npz and ccd_mask.bin. library.corrected() therefore applies the reference a second time (apply_shading subtracts the dark row and multiplies by the gain again -- not idempotent). The GUI uses library.corrected() for both the 1:1 view (gui.py:4110) and Save As/Save All (gui.py:3864). reconstruct() will report 'decode CHANGED' for every such entry, which is the false-alarm pattern CLAUDE.md describes. The raw bytes (raw.bin.gz) are still present so a re-decode is possible, but scan.tif and every delivered file derived from the library are wrong. The test double FakeScanner.scan returns the same array for both, so tests/test_session.py cannot see it.

**Evidence (from the code):**

```text
session.py:1441 `image, meta = self._scanner.scan(... shading=job.shading, ... keep_raw=True)` returns the CORRECTED image (direct.py:2733 `image, shading_report = apply_shading(image, self._shading, ccd_mask)`; raw kept only in `self.last_pixels_raw = raw_pixels`, direct.py:2818). session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` passes no `raw_image=`. FrameWriter then files `job["image"] if raw_image is None else raw_image` (session.py:1090-1091) with no `corrections=` argument, so library.py:228 records `"corrections_applied": list(corrections or [])` = []. `_prescan` (session.py:1415) does read `last_pixels_raw`; `_scan` does not. tools/scan.py:190 does (`raw = getattr(s, "last_pixels_raw", None)`), and tools/scan_roll.py:555-559 carries a comment describing exactly this bug.
```

**Failure scenario:** Operator calibrates, presses Scan at 1800 dpi. Entry is filed with corrected pixels labelled raw. He opens the full-resolution view or uses Save As: the picture is corrected twice -- shadows pulled down by a second dark subtraction, column gain applied twice (re-striping inverted), highlights clipped. `make reconstruct` flags the entry as a changed decode.

**Fix:** In _scan pass `raw_image=getattr(self._scanner, "last_pixels_raw", None)` (read immediately after scan()). In FrameWriter, when falling back to job['image'] because raw_image is None, pass `corrections=["shading"]` if meta['shading'] is set, so a non-raw file is at least labelled. Add a test whose fake scanner returns different corrected and raw arrays and asserts scan.tif equals the raw one.

<details><summary>Second reader's check</summary>

session.py:1440-1458 `_scan` calls `self._scanner.scan(..., shading=job.shading, keep_raw=True)` and passes `image` (which direct.py:2733 has replaced with `apply_shading(image, self._shading, ccd_mask)`) to `self._file(seq, 0, image, meta, ...)` with no `raw_image=`. `last_pixels_raw` (direct.py:2818) is never read in `_scan`, unlike `_prescan` (session.py:1415). FrameWriter then files `job["image"] if raw_image is None else raw_image` (session.py:1090-1091) with no `corrections=`, so library.py:228 records `corrections_applied: []`. library.corrected (library.py:385-391) only skips re-correction when 'shading' is in corrections_applied, so it applies the reference a second time. The capture's raw bytes are kept (same shape), so reconstruct compares a raw decode with corrected pixels and reports 'decode CHANGED'. The same unlabelled fallback also happens in _file when raw_image shape differs (session.py:2115-2120). Reachable from every GUI Scan press with shading on (the default).

</details>

<a id="session-roll-sr-02"></a>

### SR-02 -- A failed write of the delivered TIFF/JPEG (output folder or rolls/) aborts the library entry: raw bytes of the scan are lost

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1060-1103`, `rps7200/session.py:1046-1058`, `rps7200/session.py:2067-2071`, `rps7200/export.py:180-193`

**Doc claim:** CLAUDE.md 'File every scan in the library, with its raw bytes'; session.py:1027-1029 'a frame that cannot be filed should cost that frame, not the thirty after it'

The library entry (raw.bin.gz, shading.npz, ccd_mask.bin, raw pixels) is the only irreplaceable product of a pass; the delivered copies are re-derivable from it. The writer orders them the other way round, so a problem with a deliverable destination (output folder on a USB stick that was unplugged or filled, a network share that dropped, a Windows path too long, a JPEG encoder error) silently costs the raw record of that frame. The roll keeps scanning, and roll.json marks the frame done (see SR-03). export.py's own docstring even says 'the picture is on disk by now and the library entry is still to come'.

**Evidence (from the code):**

```text
FrameWriter._write writes delivered files first: `for path in job.get("paths") or (): Path(path).parent.mkdir(parents=True, exist_ok=True); note = export.write(str(path), delivered, ...)` (session.py:1074-1083), and only afterwards `if job["library"]: ... entry = library.save(...)` (session.py:1085-1100). Any exception from mkdir/export.write propagates to `_run`: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")` (session.py:1053-1054). export.write raises for a full/read-only/missing folder (tiff.write / PIL save are not wrapped, export.py:180-193).
```

**Failure scenario:** Operator sets the output folder to an external disk and starts a 36-frame roll at 3600 dpi; the disk fills at frame 12. Frames 12-36 each spend ~2-5 minutes of scanner time, raise OSError in export.write, and are never filed in library/. Only a log line 'picture N could not be filed' appears; roll.json says done:true for all of them.

**Fix:** Write the library entry first (and independently), then each delivered file in its own try/except that records a note rather than aborting the job. Report delivered-file failures separately from filing failures.

<details><summary>Second reader's check</summary>

FrameWriter._write (session.py:1074-1083) runs `Path(path).parent.mkdir(...)` and `export.write(...)` for every delivered path before `library.save` (session.py:1085-1100). export.write (export.py:180-193) does not catch OSError from tiff.write or the JPEG writer. Any exception goes to `_run`'s except (session.py:1053-1056), which records an error and never files the entry. So a failed output-folder or rolls/ write costs the raw bytes, shading reference and mask of that pass. The roll keeps going and roll.json still says done (SR-03).

</details>

<a id="session-roll-sr-a1"></a>

### SR-A1 -- Every 7200 dpi pass filed through ScanSession loses its raw bytes: the column-stagger realignment makes the shape guard reject them

**Severity** critical · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2701`, `rps7200/direct.py:1638-1662`, `rps7200/direct.py:1519-1531`, `rps7200/session.py:2072-2099`, `rps7200/library.py:464-466`

**Doc claim:** library.py:21-26 'raw.bin.gz is the ground truth ... keeping the bytes means a change to any of that can be re-run'; CLAUDE.md 'File every scan in the library, with its raw bytes'

At 7200 dpi the decoded image is always params.lines - 4 rows after realignment, while the capture layout declares params.lines. The session's shape guard therefore treats the pass's own bytes as another pass's and drops raw.bin.gz and raw.layout on every 7200 dpi Scan and every 7200 dpi roll frame. scan.tif then holds realigned pixels, a host-side transform of the decode, which can no longer be re-derived or re-evaluated when the stagger constant (measured, 4 lines) is revisited. These are the largest and most expensive passes the device makes (314 s RGB), and the only log is a transient 'raw bytes do not describe this image (lines N vs N-4)'. For entries that do keep 7200 dpi bytes (DirectScanner debug filing), library.reconstruct decodes with decode_index and no realignment (library.py:464-466), so it reports 'decode CHANGED: now (H, ...), stored (H-4, ...)' as a false alarm.

**Evidence (from the code):**

```text
direct.py:2695 `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... image = self._realign_native_column_stagger(image)` with NATIVE_COLUMN_STAGGER_DPI = 7200, which returns `np.empty((h - lines, ...))` (4 lines shorter). The layout recorded beside the bytes is `"lines": int(params.lines)` (direct.py:1529). session._file: `actual = {"lines": shape[0], ...}` ... `if disagree: ... capture = dict(capture, raw=None, raw_path=None, raw_layout=None)` (session.py:2076-2099).
```

**Failure scenario:** The operator scans a frame at 7200 dpi from the window or in a roll. The entry has scan.tif (realigned) with no raw.bin.gz. A later change to NATIVE_COLUMN_STAGGER_LINES or to the realignment cannot be applied to any 7200 dpi scan ever taken through the GUI.

**Fix:** Record the realignment in meta (e.g. meta['realigned_stagger_lines']) and have the guard compare against the pre-realignment decode height or lines_received, or file the unrealigned decode as scan.tif and apply the realignment in library.corrected. Teach reconstruct the same step.

<a id="session-roll-sr-03"></a>

### SR-03 -- roll.json marks a frame done before, and regardless of whether, it was filed

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `rps7200/session.py:1895-1926`, `rps7200/session.py:1348-1355`, `tools/gui.py:5240-5258`, `tools/gui.py:5086-5104`

**Doc claim:** session.py:1901-1903 'What a resume needs to know about this frame: whether it is finished.'

A resume is driven by the done flags. A frame whose library entry or frame TIFF failed to write (disk full, SR-02, permissions), or which was still in the writer queue when the process died (daemon writer thread, SR-18), is recorded done:true and will never be offered again. The manifest also records no library entry id per frame (gui.py:4986-4990 acknowledges), so nothing can reconcile the two afterwards except the fragile film.frame label join.

**Evidence (from the code):**

```text
session.py:1904 `"done": bool(rf.error is None and rf.image is not None)` is computed on the scanner thread and written at once (session.py:1924 `manifest_path.write_text(...)`), while the frame is merely queued to the writer (`self._file(...)` session.py:1883). Writer failures only produce `self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1354); nothing updates the manifest. gui.py scanned_frames reads `finished = record.get("done")` (gui.py:5254) and roll_summary computes `"remaining": [n for n in wanted if n not in done]`.
```

**Failure scenario:** Disk fills during frame 20 of a roll; frames 20-24 fail in the writer. The operator reopens the roll after freeing space: the browser shows it complete, 'remaining' is empty, and frames 20-24 exist nowhere.

**Fix:** Record done only after the writer reports success (e.g. have the writer's on_done post a 'filed' record back to the scanner thread, which rewrites the manifest with done and the entry id), or write done:'queued' and upgrade it; count writer failures toward the roll's failure budget.

<details><summary>Second reader's check</summary>

session.py:1904 `"done": bool(rf.error is None and rf.image is not None)` is computed on the scanner thread and written at once (1924-1926), while the frame has only been queued with `self._writer.submit` (via _file at 1883). A writer failure reaches only `_filed` -> `_emit("log", ...could not be filed...)` (1353-1354) and never touches the manifest. The GUI's resume logic reads those done flags.

</details>

<a id="session-roll-sr-04"></a>

### SR-04 -- A short or anomalous pass loses its raw bytes: the shape guard compares declared lines with decoded lines

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/session.py:2072-2099`, `rps7200/direct.py:1492-1494`, `rps7200/direct.py:1519-1533`, `rps7200/direct.py:1595`

**Doc claim:** library.py:21-26 'raw.bin.gz is the ground truth ... keeping the bytes means a change to any of that can be re-run'

The guard was written to stop a previous pass's bytes being filed with this image, but it keys on the declared line count rather than on lines_received, so any pass that ended early, or where one channel plane got fewer tagged lines than params.lines, has its genuine raw bytes discarded. Those are exactly the passes whose bytes are needed to investigate a decode problem later. The only trace is a transient log line (SR-20).

**Evidence (from the code):**

```text
read_planes records `"lines": int(params.lines)` (declared) and `"lines_received": len(blob) // ...` (direct.py:1531-1532), and breaks early on `except EndOfData: ... break` (direct.py:1492-1494). decode_index uses `height = min(len(planes[c]) for c in order)` (direct.py:1595). session._file: `actual = {"lines": shape[0], ...}; disagree = {k: ... if layout.get(k) is not None and layout[k] != actual[k]}` then `capture = dict(capture, raw=None, raw_path=None, raw_layout=None)` (session.py:2076-2099).
```

**Failure scenario:** The scanner signals end of data 12 lines early on a 3600 dpi frame. decode gives height params.lines-12; the session logs 'raw bytes do not describe this image (lines 6590 vs 6578)' and files the entry without raw.bin.gz. The truncation can never be re-examined.

**Fix:** Compare against layout['lines_received'] (or re-derive height from the bytes with the same decode) rather than layout['lines']; better, have the scanner attach a pass id to both the pixels and the capture record and compare ids instead of shapes. Never drop bytes that belong to this pass.

<details><summary>Second reader's check</summary>

direct.py:1492-1494 breaks on EndOfData and keeps the partial blob. last_raw_layout records `"lines": int(params.lines)` (declared, 1529) beside `lines_received`. decode_index uses `height = min(len(planes[c]) ...)` (1595). session._file compares `layout["lines"]` with `image.shape[0]` (2076-2099) and nulls raw/raw_path/raw_layout when they differ. So the genuine bytes of any short pass are dropped. The same comparison also drops the bytes of every 7200 dpi pass: see the additional finding.

</details>

<a id="session-roll-sr-05"></a>

### SR-05 -- Real-roll prescans are kept only corrected; failed-frame, arrival and hold-loop prescans are never filed; debug filing is forced off

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:1804-1854`, `rps7200/session.py:1859-1860`, `rps7200/session.py:1883-1893`, `rps7200/session.py:1265-1271`, `rps7200/session.py:2051-2060`, `rps7200/direct.py:1704-1715`, `rps7200/direct.py:3526-3563`, `rps7200/direct.py:2906-2908`, `rps7200/library.py:180-181`

**Doc claim:** session.py:2057-2060 'Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway'; CLAUDE.md 'you cannot tell in advance which scan will be the one somebody asks for later'; library.py:161-163 'prescan.tif has no raw bytes of its own'

For a real roll the framing pass of each frame survives only as a corrected picture whose own CCD mask (it is a 300 dpi pass, not the scan's resolution) and bytes are gone, so it can never be re-decoded or re-corrected. A frame that failed (the case a resume exists for) loses even that. The picture as a held frame arrived, and every intermediate verification pass of the hold loop (up to MAX_HOLD_MOVES), are not stored anywhere; prescan_before for aimed frames is written only in dry runs and then only as a corrected TIFF with file_entry=False. The `_file` docstring justifies file_entry=False with 'Under RPS7200_DEBUG=1 that picture already has a correct entry anyway' -- but the session forces debug off, so that is never true in the GUI.

**Evidence (from the code):**

```text
Prescans are filed in their own right only `if job.dry_run:` (session.py:1804). On a real roll the frame entry gets `prescan=rf.prescan` (session.py:1887) -- the corrected 8-bit image from `self.prescan()` which runs `shading=True` (direct.py:1709) -- and `rf.raw_prescan` is never passed; library.save just does `tiff.write(str(path / "prescan.tif"), prescan)` (library.py:181) with no raw, no mask. For `rf.error` frames the session only logs (session.py:1859-1860). In scan_roll's held branch there is no `prescan_before = prescan_image` (direct.py:3552-3557, unlike the aim branch at 3583). The hold loop's verification passes `image, _ = self.prescan(...)` (direct.py:2906) are kept only as the last one. `_default_scanner` passes `debug=False` (session.py:1270), which overrides RPS7200_DEBUG (direct.py:464-469).
```

**Failure scenario:** A commissioned roll holds frame 7 to its approved position in three moves; the hold later looks wrong. There is no stored arrival prescan, no intermediate prescans, and the final prescan cannot be re-corrected because its mask and bytes were never kept -- the only evidence is the hold loop's own numbers in registration.

**Fix:** File every prescan pass as its own library entry with raw bytes (capture_record taken immediately after each pass inside the driver, e.g. via an on_pass callback like scan_bracket uses), including failed frames' prescans and hold/aim verification passes; link them from the frame entry. At minimum pass rf.raw_prescan and the prescan's own mask/bytes into the frame entry and label prescan.tif as corrected.

<details><summary>Second reader's check</summary>

Prescans are filed as their own entries only under `if job.dry_run:` (session.py:1804). On a real roll the frame entry gets `prescan=rf.prescan` (1887). That image is the corrected 8-bit output of prescan(), which forces `shading=True` (direct.py:1709), and library.save writes it as prescan.tif with no bytes and no mask of its own (library.py:180-181). For an `rf.error` frame the session only logs (1859-1860). In the held branch of scan_roll (direct.py:3524-3563) prescan_before is never set, and the hold's intermediate prescans (direct.py:2905-2907) overwrite each other in out['prescan']. `_default_scanner` forces `debug=False` (session.py:1270), so the `_file` docstring's claim 'Under RPS7200_DEBUG=1 that picture already has a correct entry anyway' (2057-2060) is never true in the GUI.

</details>

<a id="session-roll-sr-06"></a>

### SR-06 -- The GUI session gzips entries (and does all other heavy local work) with the device open and idle

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:10-16`, `rps7200/session.py:1013-1021`, `rps7200/session.py:1311-1331`, `rps7200/session.py:1440-1458`, `rps7200/library.py:186-211`, `rps7200/library.py:310`, `tools/gui.py:4110`

**Doc claim:** CLAUDE.md 'A single scan compresses nothing while the device is open' and 'Do not hold the session open through heavy local work. Gzipping a 140 MB library entry with the device open and idle preceded one wedge.'; session.py:13-16; session.py:1016-1020

The session docstring says the writer thread exists 'because gzipping a library entry with the device open and idle is the state that preceded a wedge', and FrameWriter's says the write 'overlaps the next frame's scan, so the device is busy rather than idle throughout'. That holds only for mid-roll frames. For every single scan and prescan in the window -- the most common operation -- and for the tail of every roll, the gzip runs with the device open and idle, exactly the precondition CLAUDE.md lists. CLAUDE.md also states 'A single scan compresses nothing while the device is open', which is true of DirectScanner's debug spooling but false of the GUI's path.

**Evidence (from the code):**

```text
The worker opens the scanner once (`self._scanner.open()`, session.py:1297) and closes it only at shutdown (session.py:1336-1340); between jobs it blocks on `job = self._jobs.get()` (session.py:1314). Each Scan/Prescan job ends with `self._file(...)` -> `self._writer.submit(...)`, and the writer thread runs `library.save` -> `gzip.open(... compresslevel=6)` (library.py:192) and `reindex(root)` over every entry (library.py:310) while the worker is idle with the device open. The same is true for the last frame(s) of every roll and every frame after a stop. The GUI also runs library.corrected() on 142 MB frames (gui.py:4110) with the session open.
```

**Failure scenario:** Operator scans a single 3600 dpi RGBI frame; the writer gzips ~140 MB of raw bytes plus reindexes the library while the scanner sits open and idle for many seconds -- the documented wedge precondition.

**Fix:** Either close the device between jobs when idle (reopen on the next job), or defer compression for non-roll jobs (spool raw bytes uncompressed as DirectScanner debug does and gzip them only when the next pass starts or after close), and measure with tools/filing_load_test.py. Correct the docstrings to say what actually happens.

<details><summary>Second reader's check</summary>

The worker opens the device once (session.py:1297) and closes it only in the finally at shutdown (1336-1340). Between jobs it blocks on `self._jobs.get()` (1314). Each Prescan and Scan ends in _file -> writer.submit, and the writer runs library.save -> `gzip.open(..., compresslevel=6)` (library.py:192) followed by `reindex(root)` (310), while the worker sits idle with the device open. The same holds for the last frames of a roll. The FrameWriter docstring's 'the device is busy rather than idle throughout' (session.py:1016-1020) is true only while another frame is scanning. CLAUDE.md's 'A single scan compresses nothing while the device is open' describes DirectScanner's debug spool, not the GUI path.

</details>

<a id="session-roll-sr-07"></a>

### SR-07 -- roll.json/survey.json written non-atomically; an unreadable earlier roll.json is silently discarded and overwritten on resume

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `rps7200/session.py:1649-1654`, `rps7200/session.py:1924-1926`, `tools/gui.py:2943`

**Doc claim:** session.py:1643-1648 'Without this the second run *replaced* the first ... the record of the first two was simply gone -- in the one file whose job is to still describe this roll a year from now.'; session.py:1922-1923 'a crash should cost the frame it was on, not the roll'

A crash, power loss, disk-full or force-abort that kills the process during the write leaves a truncated or empty roll.json. The next run into that folder treats it as an empty history without a word and overwrites it, permanently erasing the record of every earlier frame (positions, registration, exposure, done flags) -- the loss the surrounding comment says this code exists to prevent. approved.json (gui.py:2943) is written the same way.

**Evidence (from the code):**

```text
`manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")` (session.py:1924-1926) truncates then writes in place after every frame. On resume: `try: earlier = json.loads(manifest_path.read_text(encoding="utf-8")) except (OSError, ValueError): earlier = {}` (session.py:1651-1654), then `"frames": list(earlier.get("frames") or [])` and the file is rewritten from that.
```

**Failure scenario:** Disk fills while the manifest is rewritten after frame 15; write_text raises and the roll job fails, leaving roll.json truncated. After freeing space the operator resumes: earlier={} silently, and the new manifest contains only the frames of the resumed run.

**Fix:** Write to a temp file in the same directory, fsync, then os.replace. On resume, if the existing manifest cannot be parsed, refuse (or move it aside as roll.json.corrupt-<time>) and say so, never overwrite it.

<details><summary>Second reader's check</summary>

session.py:1924-1926 rewrites roll.json in place with `write_text` after every frame. On resume, `except (OSError, ValueError): earlier = {}` (1651-1654) silently treats an unreadable file as empty, and the next write replaces it. gui.py:2943 writes approved.json with the same non-atomic `write_text`. Disk-full is exactly the condition that both truncates the file and fails the job.

</details>

<a id="session-roll-sr-08"></a>

### SR-08 -- Sheet decisions and resumed rolls land in different folders from the roll they belong to

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1637`, `rps7200/session.py:1148-1153`, `tools/gui.py:2929-2943`, `tools/gui.py:2853-2864`, `tools/gui.py:2560-2640`, `tools/gui.py:2147-2158`

**Doc claim:** session.py:1148-1152 '...can ask rather than recompute the same name and drift out of step with it'; gui.py:2590-2598 'put the strip in the way it went in before and press Roll'

(a) With the roll name empty (the normal quick-walk case) approved.json is written to rolls/roll/approved.json while the walk and the roll are in rolls/<date>/ -- the operator's positions, turns and sources are not beside the roll, and rolls/roll/approved.json is overwritten by every unnamed commission. (b) A name with characters _safe changes puts approved.json and roll.json in different folders. (c) Reopening an earlier roll (e.g. rolls/2026-09-21) and pressing Scan chosen/Roll writes the resumed frames into rolls/<today> (or rolls/<name typed>), not into the reopened folder, so its roll.json never learns the new frames and today's folder may merge with an unrelated roll. The last_roll_dir docstring says callers should ask for the folder 'rather than recompute the same name and drift out of step with it' -- _write_approved recomputes it.

**Evidence (from the code):**

```text
Session: `name = job.name or time.strftime("%Y-%m-%d")`; `out = Path(job.out) if job.out else self.rolls / name` (session.py:1634-1635). GUI approved.json: `name = _safe(self.fields["roll"].get().strip()); folder = Path(self.session.rolls) / name` (gui.py:2940-2941) -- `_safe("")` returns "roll" (session.py:2184). The Roll is submitted with `name=self.fields["roll"].get().strip()` and no `out=` (gui.py:2862). Reopening a roll sets `self._loaded_roll = folder` / `self._sheet_roll = folder` (gui.py:2563, 2626) but never the roll-name field and never Roll.out.
```

**Failure scenario:** Monday: walk a strip unnamed -> rolls/2026-09-21/survey.json. Wednesday: reopen it, tick frames, Scan chosen. approved.json goes to rolls/roll/, frames and roll.json go to rolls/2026-09-23/. Reopening rolls/2026-09-21 still shows nothing scanned and no positions; rolls/2026-09-23 has frames but no survey or approved.json.

**Fix:** Have the GUI pass the roll folder explicitly (Roll.out = the reopened/walked folder, or last_roll_dir) and write approved.json into exactly that folder (ideally have the session write approved.json from the Roll job itself). Set the roll-name field from the reopened folder.

<details><summary>Second reader's check</summary>

The session writes to `self.rolls / (job.name or date)` (session.py:1634-1635). `_write_approved` writes to `Path(self.session.rolls) / _safe(name)` (gui.py:2940-2941), and `_safe("")` returns 'roll' (session.py:2184). Both Roll submissions pass `name=self.fields["roll"].get().strip()` and no `out=` (gui.py:2156, 2862). open_roll sets `_loaded_roll` and `_sheet_roll` (2563, 2626) and never sets the roll-name field or Roll.out; grep finds no other writer of fields['roll']. The divergence is wider than 'characters _safe changes' suggests: a name with an ordinary space ('Portra 400') already splits roll.json (rolls/Portra 400/) from approved.json (rolls/Portra-400/).

</details>

<a id="session-roll-sr-09"></a>

### SR-09 -- Unnamed walks and rolls of one day share rolls/<date>/: frames and prescans overwritten, stale approved.json and roll.json applied to a different strip

**Severity** high · **Category** user-error · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1642`, `rps7200/session.py:1817`, `rps7200/session.py:1889`, `rps7200/session.py:2192-2204`, `tools/gui.py:4686-4722`

**Doc claim:** session.py:2195-2196 'A frame rescanned after a failure would otherwise land on the file the first attempt wrote'; session.py:407-411 'a roll with no name typed was named by the date, so every such roll of a day went into one file'

Two different strips walked or scanned on the same day without a name go into the same directory. The second overwrites frameNN.tif and prescanNN.tif of the first (walked_prescans' own docstring describes stale prescans left behind), roll.json merges the two strips' frames under one roll and a union 'wanted', and when the folder is reopened the first strip's approved.json supplies positions, rotations and reference_entry for the second strip's frames, and the first strip's roll.json marks the second strip's frames as already scanned. The _unclaimed docstring ('the better of the two is not always the second') applies to the roll folder too but is not used there.

**Evidence (from the code):**

```text
Default folder `time.strftime("%Y-%m-%d")` (session.py:1634). Files are written straight to `out / f"prescan{number:02d}.tif"` and `out / f"frame{number:02d}.tif"` (session.py:1817, 1889) -- `_unclaimed` is applied only to the output-folder copy (session.py:2071). A dry run replaces survey.json wholesale; a real roll merges into any roll.json there. read_survey reads approved.json and roll.json from the folder whatever strip they were written for (gui.py:4686-4722: `offsets, rotations, flips, entries, sources = read_approved(folder, ...)`).
```

**Failure scenario:** Morning: strip A walked and 6 frames scanned unnamed. Afternoon: strip B walked unnamed. Reopening rolls/<date> shows strip B's prescans with strip A's approved offsets and A's frames 1-6 marked done, so frames 1-6 of B are not offered; a commission holds B's frames against A's reference entries.

**Fix:** Never merge into an existing folder implicitly: default to a unique name (date + time or a counter), refuse or ask when survey.json/roll.json already exist for a different strip, and use _unclaimed (or refuse) for frameNN/prescanNN in the roll directory.

<details><summary>Second reader's check</summary>

The folder defaults to `time.strftime("%Y-%m-%d")` (1634). frameNN.tif and prescanNN.tif are written straight to `out / ...` (1817, 1889); `_unclaimed` is applied only to output-folder copies (2071). A real roll merges into any roll.json already there (1649-1668), and a walk replaces survey.json. walked_prescans' own docstring (session.py:640-647) describes a real instance of stale prescans from two walks mixing in rolls/2026-09-23.

</details>

<a id="session-roll-sr-10"></a>

### SR-10 -- A value in the Film 'frame' note replaces every roll frame's label: all entries share one library signature and the roll-entry join breaks

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `rps7200/session.py:1826-1827`, `rps7200/session.py:1880-1882`, `rps7200/library.py:686-698`, `rps7200/library.py:714-738`, `tools/library.py:115-133`, `tools/gui.py:5004-5008`, `tools/scan_roll.py:572-577`

**Doc claim:** tools/scan_roll.py:572-576 comment; library.py:641-645 'Two entries sharing a signature are interchangeable'

The Film panel's 'frame' field is a per-picture note. If it holds anything when a roll starts (e.g. left over from a single scan), every frame of the roll is filed with that same film.frame. All frames of the roll at one dpi then share one signature (same stock, frame, subject, dpi, channels, full-frame window, depth, film, protocol, metered exposure, fast_ir), so `tools/library.py duplicates` reports the whole roll as interchangeable copies and `--delete` destroys all but one. The browser's roll-to-entry join also finds none of them, so the roll cannot be exported.

**Evidence (from the code):**

```text
session.py:1880-1882 `notes = replace(job.notes, frame=job.notes.frame or f"{name}-{number:02d}")` -- the operator's field wins. library.signature includes `(film.get("frame") or "").strip().lower()` and, for metered scans, `commanded = None` (library.py:679-697). tools/library.py `duplicates --delete` removes all but `keep` of each group with `shutil.rmtree(path)`. gui.roll_entry_index joins on `roll, _, number = frame.rpartition("-")`. tools/scan_roll.py:572-576 warns: 'library.signature() includes film.frame, so without it every picture of a roll would register as a duplicate'.
```

**Failure scenario:** Operator types frame '12' for a test scan, then runs a 24-frame roll. Later, cleaning the library with `tools/library.py duplicates --delete`, 23 of the 24 frames are deleted as 'same scan of the same picture'.

**Fix:** Always set film.frame for roll entries to the per-frame label (keep the operator's value in another field, e.g. notes), and make library.signature include something intrinsic (roll name + transport position, or the raw sha256) so distinct pictures can never collide.

<details><summary>Second reader's check</summary>

session.py:1880-1882 `frame=job.notes.frame or f"{name}-{number:02d}"`: a non-empty Film 'frame' field wins for every roll frame. library.signature (library.py:686-698) contains only stock, film.frame, subject, dpi, channels, window, depth, film, protocol, commanded exposure and fast_ir, and for a metered roll commanded is None. So all frames of such a roll share one signature, and `tools/library.py duplicates --delete` rmtrees all but one (tools/library.py:115-133). The problem is broader than a leftover label: see the additional finding on unlabelled single scans and prescans.

</details>

<a id="session-roll-sr-11"></a>

### SR-11 -- DemoScanner.scan_roll diverges from DirectScanner.scan_roll in failure handling, end detection, prescan resolution, stop and raw fields

**Severity** high · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:599-791`, `rps7200/direct.py:3493-3686`, `rps7200/session.py:1761-1786`, `rps7200/session.py:1859-1905`

**Doc claim:** CLAUDE.md 'The demo is the real software with different inputs ... A number, cap, constant or decision the stand-in needs is taken from DirectScanner, never retyped'; demo.py:617-625 'The roll, walked the way DirectScanner.scan_roll walks it'

The session consumes both loops identically, so under --demo: a transport fault (e.g. force abort or no film) fails the whole job instead of yielding an error frame and continuing, so the session's rf.error branch, the done:false records and the resume-a-failed-frame path are never exercised; a blank frame never ends a roll; the prescan resolution the operator picked is ignored while the manifest and delivered meta claim it; Stop during a hold is not seen; the strip walk is fed differently; and every frame is filed through the raw_image=None fallback (SR-12). The CLAUDE.md rule is that the demo 'may change what the software is fed. It may not change what the software does', and that decisions are taken from DirectScanner rather than re-implemented -- here the whole per-frame loop is a second copy.

**Evidence (from the code):**

```text
Real loop: `except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc: failures += 1 ... yield RollFrame(..., error=str(exc), ...)` and `if failures >= max_failures` (direct.py:3666-3678); blank end `if contrast < blank_contrast: ... return` (direct.py:3509-3515); `self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)`; hold gets `should_stop=should_stop`; `walk.observe` for every frame before the held branch (direct.py:3517-3518). Demo loop: no except around a frame (demo.py:707-770 is try/finally only), `prescan, _ = self.prescan(film=film)` (default 300 dpi), `self._hold_to_approved(index, prescan, 300, held, keep_raw=False, reverse=reverse_hold)` (no should_stop), `walk.observe` only in the non-held branch (demo.py:749-750), `max_failures`, `meter`, `fast_infrared`, `prescan_resolution` swallowed by `**kw`, and `RollFrame(...)` without raw_image/raw_prescan/prescan_before (demo.py:771-779).
```

**Failure scenario:** In --demo, force-abort mid-roll: the job ends 'failed' with no record for the frame in roll.json. On hardware the same action yields an error frame, records done:false and ends as stopped. A feature built and checked in the demo against the demo's behaviour is wrong on the scanner.

**Fix:** Run DirectScanner.scan_roll itself against the demo's primitives (position/advance/prescan/scan/nudge/set_gain_offset stubs), as _hold_to_approved already is, instead of a parallel loop; if impossible, share the per-frame body.

<details><summary>Second reader's check</summary>

DemoScanner.scan_roll (demo.py:599-791) is a second copy of the loop. It has no per-frame except (only try/finally at 707-770), so any fault fails the job instead of yielding an error frame. It has no blank-frame end and ignores prescan_resolution, meter and max_failures (they fall into **kw). It calls `self.prescan(film=film)` (300 dpi) and `_hold_to_approved(index, prescan, 300, held, keep_raw=False, reverse=reverse_hold)` without should_stop, and calls walk.observe only in the aim branch. Its RollFrame carries no raw_image, raw_prescan or prescan_before, and meta has no registration or roll_index. The real loop does each of these (direct.py:3493-3678). The session consumes both identically (session.py:1787-1905).

</details>

<a id="session-roll-sr-a2"></a>

### SR-A2 -- Unlabelled single scans and prescans from the window share one library signature; `duplicates --delete` destroys distinct pictures

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P11](../problems/P11-duplicates-delete-destroys-scans.md)

**Where:** `rps7200/session.py:1393-1427`, `rps7200/session.py:1440-1458`, `rps7200/library.py:679-698`, `rps7200/library.py:720-738`, `tools/library.py:115-133`, `tools/gui.py:1724-1731`

**Doc claim:** library.py:641-645 'Two entries sharing a signature are interchangeable: the device was told exactly the same thing about the same frame'

Every prescan the window files at 300 dpi with the same stock and a blank frame field has the same signature, whichever frame of whichever strip it shows. The same goes for every metered single scan at one dpi and channel count. The library's own cleanup therefore classes different photographs as 'same scan of the same picture' and, with --delete, removes all but one. The session supplies no per-picture identity for non-roll passes (not even the transport position it knows in _last_prescan), so the only safeguard is the operator remembering to type a frame label before every pass.

**Evidence (from the code):**

```text
_prescan and _scan file with `notes=job.notes`, whose frame is the Film panel's free-text field (gui.py:1728 `frame=self.fields["frame"].get().strip()`), normally empty. signature() identifies a picture only by `(film.get("frame") or "").strip().lower()`, stock and subject, plus scan settings. For a metered scan `commanded = None`, and for a prescan `exposure_metered` is False with the default exposure_scale 1.0, so every prescan has the same commanded value. No position, time, roll or raw hash is included. prunable() keeps `keep` (default 1) per group, and `duplicates --delete` runs `shutil.rmtree(path)` on the rest.
```

**Failure scenario:** Over a week the operator prescans and scans 40 different frames from the window without typing a frame label. Running `uv run python tools/library.py duplicates --delete` to reclaim space removes 38 of them, raw bytes included, as redundant.

**Fix:** Include an intrinsic identity in signature() (e.g. raw sha256, or the roll/position/time) so that distinct passes never collide. Have the session record the transport position and a per-pass id in every entry. At minimum make prunable refuse groups whose members have different raw sha256 or different image sha256.

<a id="session-roll-sr-a3"></a>

### SR-A3 -- Any walk into an existing roll folder replaces survey.json wholesale: a partial re-walk erases the earlier walk's frames

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1638-1654`, `rps7200/session.py:1740-1748`, `rps7200/session.py:1914-1926`, `rps7200/session.py:634-661`

**Doc claim:** session.py:1638-1641 'writing both into roll.json meant the record of six frames walked was replaced by the record of the three that were then scanned -- losing exactly what the walk was kept for'

Walks are not resumable. If a walk stops or fails at frame 20 and the operator walks again from 'start at 20' into the same folder (the default date folder, or the same roll name), the first frame of the second walk rewrites survey.json with only its own frames. Frames 1-19 disappear from the contact sheet and from reopening, although prescan01-19.tif are still on disk. Any walk into a folder that already holds a commissioned roll also replaces the survey whose prescanNN.tif are that roll's hold references, and it overwrites those prescans too. roll.json merges earlier records deliberately (1643-1648); survey.json has no equivalent.

**Evidence (from the code):**

```text
`earlier` is read only `if not job.dry_run and manifest_path.exists()` (session.py:1649), so a walk starts from `"frames": list(earlier.get("frames") or [])` = [] and rewrites survey.json after its first frame. walked_prescans reads only the frames listed in the manifest ('never from the files in the folder', session.py:638-640).
```

**Failure scenario:** The film jams on frame 20 of a 36-frame walk. The operator clears it and walks frames 20-36 with the same (empty) name. Reopening the day's roll shows only frames 20-36, so frames 1-19 cannot be approved or commissioned without walking them again.

**Fix:** Merge a walk into an existing survey.json the way roll.json is merged (per-frame replace), or refuse or rename when a survey already exists for the folder, and say which frames were kept.

<a id="session-roll-sr-12"></a>

### SR-12 -- Demo-filed entries hold shading-corrected pixels labelled raw, and demo prescans carry a stale capture record

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:15-20`, `rps7200/demo.py:1008-1010`, `rps7200/demo.py:455-477`, `rps7200/demo.py:435-442`, `rps7200/session.py:1415`, `rps7200/session.py:1089-1091`, `rps7200/library.py:142`

**Doc claim:** demo.py:15-20 'An entry the demo files reconstructs like any other.'

Every demo entry whose source had a shading reference (i.e. essentially every real library entry) is filed with corrected pixels, raw bytes of the uncorrected pass and the reference, with corrections_applied=[] -- so reconstruct reports a changed decode and library.corrected double-corrects, contradicting the demo docstring. Demo prescans are filed with another pass's shading.npz/ccd_mask.bin (and its bytes, if shapes coincide). The real scanner's capture_record always describes the last pass. The session's getattr(..., None) defaults hide the gap instead of refusing it. library.save also drops the `inquiry` it is given, so demo entries are not marked as demo in scan.json (meta['demo'] is not whitelisted either); only the folder (demo/library, or whatever --library says) tells them apart. tests/test_demo.py:69-82 passes only because its fixture entry has no reference.

**Evidence (from the code):**

```text
`_decode` applies shading whenever the source entry has a reference: `image, self._shading_report = apply_shading(image, reference, mask)` (demo.py:1010) and stores `self._capture = capture` with the raw bytes and reference. DemoScanner has no `last_pixels_raw` (grep finds none), so `raw_image = getattr(self._scanner, "last_pixels_raw", None)` is None (session.py:1415) and roll frames have raw_image None; FrameWriter files `job["image"]` (session.py:1091). Demo `prescan()` reads `prescan.tif` via `_pair_image` without touching `self._capture`, so capture_record() still returns the previous decode's reference/mask/raw.
```

**Failure scenario:** `make run-demo`, press Scan: the demo/library entry's scan.tif is corrected, raw.bin.gz is the uncorrected decode, and `library.reconstruct` on it says 'decode CHANGED'. With `--demo --library library` the same entries land in the real library indistinguishable from scanner entries.

**Fix:** Give DemoScanner last_pixels_raw/last_scan_meta per pass (the uncorrected decode) and raw_image/raw_prescan on RollFrame; reset _capture on every pass (prescan.tif has no bytes -> raw None, reference/mask of this pass or None); record meta['demo'] and the inquiry in scan.json; add a demo test with a shading reference.

<details><summary>Second reader's check</summary>

demo._decode applies `apply_shading(image, reference, mask)` whenever the source entry has a reference (demo.py:1009-1010), and _capture keeps the uncorrected bytes and the reference. DemoScanner never sets last_pixels_raw (grep), so session._prescan gets None (session.py:1415), Scan never passes one (SR-01), and demo RollFrames carry none. The corrected image is filed with corrections_applied=[] beside raw bytes whose shape matches. Demo prescan() reads prescan.tif through _pair_image without resetting self._capture (demo.py:455-477, 1174-1182), so the previous pass's reference and mask ride along. library.save ignores `inquiry` (the only occurrence is the parameter at library.py:142), and 'demo' is not in the scan whitelist.

</details>

<a id="session-roll-sr-13"></a>

### SR-13 -- submit() cancels a requested stop, and two GUI paths queue jobs while a roll runs

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `rps7200/session.py:1214-1218`, `rps7200/session.py:1317`, `rps7200/session.py:1364`, `tools/gui.py:740`, `tools/gui.py:2077-2158`, `tools/gui.py:2138-2139`, `tools/gui.py:4500-4503`, `tools/gui.py:4531-4587`, `tools/gui.py:3018`

**Doc claim:** gui.py:2138-2139 'The button this handler is behind is disabled while busy, so this cannot race a job that is still running'; gui.py:745-748 '`submit` clears the flag, so a stray press cannot reach the next job'

While a roll runs, Ctrl/Cmd-B (after its OK dialog) or an aim-click on a prescan submits a new job. That (1) clears a Stop the operator already pressed, so the running roll continues to its end; (2) queues the job to run after the roll -- a nudge computed from an old prescan is applied wherever the roll left the film, and a second roll re-seeks and (for a dry run into the same date folder) overwrites survey.json; (3) for a dry-run on_roll, immediately wipes self.survey, orientations, sheet_state and _sheet_roll of the walk still in progress. The clear in submit is redundant with the per-job clear in _run for its stated purpose.

**Evidence (from the code):**

```text
`def submit(self, job): self._stop.clear(); self._jobs.put(job)` (session.py:1214-1218). The worker also does `self._stop.clear()` before each job (session.py:1317), which makes `_dispatch`'s `self._check_stop()` (session.py:1364) effectively dead. The shortcut table maps `"roll": self.on_roll` with no busy check (gui.py:740) while on_roll says 'The button this handler is behind is disabled while busy, so this cannot race a job that is still running' (gui.py:2138-2139). on_press calls `self._aim(event)` for a prescan when aim is ticked (gui.py:4500-4503) and _aim ends in `self.on_nudge(...)` -> `self.session.submit(Move(millimetres=...))` with no busy check.
```

**Failure scenario:** Roll running; operator presses Stop ('finishing what is already running'), then aim-clicks a prescan to check framing and confirms the dialog. The stop flag is cleared, the roll scans the remaining 20 frames, and then the film is nudged at the end of the strip.

**Fix:** Remove `_stop.clear()` from submit() (the worker already clears it per job). Make ScanSession.submit refuse (or the GUI guard) any job while busy, including shortcut and aim paths; have on_roll check self.busy like _confirm_then does.

<details><summary>Second reader's check</summary>

`submit` does `self._stop.clear(); self._jobs.put(job)` (session.py:1214-1218), so any submit during a running job cancels a Stop the operator requested. The worker's per-job clear (1317) already covers the stated purpose. The shortcut table binds `"roll": self.on_roll` (gui.py:740), and on_roll (2077-2158) has no busy check. `_confirm_then` checks busy (828) but on_roll does not. An aim-click on a prescan (on_press -> _aim -> on_nudge, gui.py:4500-4587, 2972-3018) submits a Move with no busy check. The dry-run branch of on_roll clears survey, orientations, sheet_state and _sheet_roll immediately (2112-2129).

</details>

<a id="session-roll-sr-14"></a>

### SR-14 -- Rotating or flipping during a walk changes how prescanNN.tif is written, but the manifest keeps the orientation from the start

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `rps7200/session.py:1686-1688`, `rps7200/session.py:1732-1733`, `rps7200/session.py:2025-2029`, `rps7200/session.py:2106-2110`, `tools/gui.py:3911-3923`, `tools/gui.py:7739-7742`, `tools/gui.py:4718-4719`

**Doc claim:** session.py:2018-2023 'read_survey un-orients it by the single pair the manifest carries'

If the operator turns a prescan while the walk is still running (a natural reaction to sideways prescans), the remaining prescanNN.tif files are written in the new arrangement while survey.json says the old one. Reopening the walk un-orients them wrongly: the sheet shows them turned, and as hold references they no longer correlate against fresh passes of the film, so the hold loop reports unverified or moves on a bad measurement. The per-prescan orientation is recorded only in each library entry's scan.json, which read_survey does not consult. The two attributes are also assigned separately across threads, so a filing can read a torn pair.

**Evidence (from the code):**

```text
Manifest: `"rotation": self.rotation, "flipped": self.flip` captured once at roll start (session.py:1687-1688). Each prescan is oriented at filing time from the live attributes: `_orientation_for` returns `self.rotation, self.flip` for kind=='prescan' (session.py:2029). The GUI sets `self.session.rotation = result.rotation` from the UI thread on any rotate/flip (gui.py:3921-3922, 7741-7742), which the viewing shortcuts allow while busy. read_survey un-orients every prescan with the single `turn = int(manifest.get("rotation") or 0)` (gui.py:4718-4719).
```

**Failure scenario:** Walk of 20 frames; at frame 6 the operator presses rotate-right. prescan07-20.tif are written rotated 90; survey.json says 0. Next day he reopens the walk: frames 7-20 appear sideways and their commissioned holds come back unverified.

**Fix:** Snapshot rotation/flip into the Roll job (and use it for all its prescans), or record the orientation per frame record in the manifest and un-orient per frame in read_survey.

<details><summary>Second reader's check</summary>

The manifest records `"rotation": self.rotation, "flipped": self.flip` once (session.py:1687-1688). `_orientation_for` returns the live `self.rotation, self.flip` for prescans (2029) each time `_file` runs. gui._carry sets `self.session.rotation`/`flip` from the UI thread on any rotate/flip (gui.py:3921-3922, and rotate-all at 7739-7742), and those shortcuts have no busy guard. read_survey un-orients with the single manifest pair (gui.py:4718-4719).

</details>

<a id="session-roll-sr-15"></a>

### SR-15 -- Roll name is used as a path component without sanitising

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1636`, `rps7200/session.py:2170-2189`, `tools/gui.py:2156`, `tools/gui.py:2862`

**Doc claim:** session.py:2170-2173 'CON fails where CON-1 is fine -- and it fails as NotADirectoryError from mkdir, which reads like a bug in this driver'

A roll name containing '/' or '\\' creates nested folders, '..' writes outside rolls/, and on Windows ':', '?', '*', '"' or a device name like 'CON' makes mkdir fail. That failure happens after seek() has already moved the film (seek runs first, session.py:1619-1621), so the job fails having moved the transport. The _RESERVED comment describes exactly this mkdir failure as a thing avoided, but only the output-folder filename is protected. It also makes approved.json (which is _safe'd) and roll.json disagree about the folder (SR-08).

**Evidence (from the code):**

```text
`name = job.name or time.strftime("%Y-%m-%d")`; `out = Path(job.out) if job.out else self.rolls / name`; `out.mkdir(parents=True, exist_ok=True)` (session.py:1634-1636). `_safe` is applied only in `_out_name` (session.py:2164-2166). The GUI passes `name=self.fields["roll"].get().strip()` unchanged.
```

**Failure scenario:** Operator names a roll 'Portra 400 / Italy'. The session writes rolls/Portra 400 /Italy/roll.json while approved.json goes to rolls/Portra-400---Italy/; the roll browser lists neither correctly.

**Fix:** Apply the same _safe() to the folder name in _roll (and use the result everywhere, including approved.json and library tags), validate it in the GUI before the seek, and reject names that resolve outside self.rolls.

<details><summary>Second reader's check</summary>

`out = Path(job.out) if job.out else self.rolls / name; out.mkdir(parents=True, exist_ok=True)` (session.py:1634-1636) uses job.name unsanitised, and it runs after `seek(...)` (1619-1621) has already moved the film. `_safe` is applied only in `_out_name` and in the GUI's approved.json folder.

</details>

<a id="session-roll-sr-16"></a>

### SR-16 -- Library records drop the inquiry, the transport position and the roll index; single scans record no position and nudges are recorded nowhere

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:131-147`, `rps7200/library.py:237-261`, `rps7200/session.py:2137`, `rps7200/direct.py:3653-3655`, `rps7200/session.py:1536-1606`

**Doc claim:** library.py:7-12 'What makes that possible is saving enough beside the pixels'

The self-contained entry cannot say which scanner/firmware produced it, where on the strip it was taken (only roll.json knows, and that file is overwritten per record and non-atomic), or what SLIDE commands were sent between the framing pass and the scan. For re-evaluation of framing/hold behaviour and for re-numbering, these are needed facts. The session passes the inquiry expecting it to be stored.

**Evidence (from the code):**

```text
library.save accepts `inquiry: Any = None` (library.py:142) and never uses it (grep finds no other occurrence). The 'scan' block is a whitelist (library.py:238-260) that omits `roll_index`/`roll_position` which scan_roll sets (`meta["roll_index"] = index; meta["roll_position"] = position`, direct.py:3653-3654) and omits `demo`. `_scan`/`_prescan` put no transport position into meta; `_move` returns a string only, so sub-frame nudges and whole-frame moves are logged to the window and nowhere else.
```

**Failure scenario:** A roll.json is lost or corrupted (SR-07); the frame entries remain but none records its transport position or index, so which strip frame each is can only be guessed from a free-text label.

**Fix:** Store inquiry.describe()/fields, roll_index, roll_position and the demo flag in scan.json; record the transport position (and the nudge history since the last whole-frame move) in single scan/prescan meta.

<details><summary>Second reader's check</summary>

library.save's `inquiry` parameter is never used. The 'scan' whitelist (library.py:237-261) omits roll_index and roll_position, which scan_roll sets at direct.py:3653-3654, and omits 'demo'. `_scan`/`_prescan` put no transport position into meta; the position is kept only in `self._last_prescan` in memory. `_move` returns only a log string.

</details>

<a id="session-roll-sr-17"></a>

### SR-17 -- Resuming an old roll rewrites its frame numbers in place, destroying the originally recorded numbering

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `rps7200/session.py:1655-1668`, `rps7200/session.py:1747`, `rps7200/session.py:575-631`

**Doc claim:** session.py:1665-1666 'What this reads is what gets written back, under "strip", for good.'

renumbered() is a heuristic that has been corrected four times in the recent history (61bd12c, 6d9b770, 6e1f975, 678d9c8) and whose docstring describes earlier versions writing wrong numbers back 'for good'. The migration is still one-way: the counted numbers the file held are overwritten and 'numbering: strip' stops any later, better renumbering from running. A misjudgement becomes permanent on the first resume.

**Evidence (from the code):**

```text
`earlier = renumbered(earlier, fallback=self._walk_shift(out), say=...)` then `"frames": list(earlier.get("frames") or [])` and the manifest is written with `"numbering": NUMBERING` (session.py:1667-1673, 1747, 1924). renumbered sets `record["number"] = numbers[i]` and `record["index"] = record["number"] - 1` (session.py:580, 593) without keeping the old values.
```

**Failure scenario:** A legacy roll with a misread counter (5, 5, 7) is resumed; renumbered picks one reading and writes it back under numbering:strip. A later fix to renumbered can no longer see the original numbers.

**Fix:** Keep the original under e.g. record['counted_number'] (and the manifest's original under 'legacy'), or back up roll.json before the first migrating write.

<details><summary>Second reader's check</summary>

On resume, `earlier = renumbered(earlier, ...)` (session.py:1667-1668) overwrites record['number'] and ['index'] (580, 593) and sets `out["numbering"] = NUMBERING` (630). renumbered returns early when numbering is already 'strip' (session.py:509), so after the first resumed write the original counted numbers are gone and no later fix can revisit them.

</details>

<a id="session-roll-sr-18"></a>

### SR-18 -- Daemon worker threads: Ctrl+C or any main-thread exit abandons a read mid-scan and drops queued frames; closing the window mid-roll waits for the whole roll

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/session.py:1211`, `rps7200/session.py:1043`, `tools/gui.py:3039-3068`, `tools/gui.py:8204-8209`

**Doc claim:** CLAUDE.md 'Never abandon a read mid-scan'

The thread that owns the device is a daemon, so the interpreter does not wait for it: a KeyboardInterrupt in the terminal running the window, or any path where main() returns, kills it inside libusb_bulk_transfer (the documented wedge) and kills the writer mid-gzip (partial entry without scan.json, SR-22) while roll.json already says done. Closing the window during a 3-hour roll offers only 'wait for the whole roll' or cancel; there is no 'stop after this frame and quit', which pushes the operator towards killing the process.

**Evidence (from the code):**

```text
`self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")` (session.py:1211); FrameWriter `threading.Thread(target=self._run, daemon=True)` (session.py:1043). on_close: 'Quitting waits for it to finish' then `self.session.shutdown()` which only appends None behind the running job; no request_stop (gui.py:3040-3049); `_wait_to_quit` has no timeout.
```

**Failure scenario:** Operator closes the window during a long roll, sees 'closing ...' for an hour, and presses Ctrl+C in the terminal: the read is abandoned, the scanner needs a power cycle, and the two queued frames are lost.

**Fix:** Make the scanner thread non-daemon (and have main join it), install a SIGINT handler that calls request_stop, and offer 'Stop after this frame and quit' in on_close (request_stop + shutdown).

<details><summary>Second reader's check</summary>

Both threads are daemons (session.py:1211, 1043). On a normal window close the worker's finally runs writer.finish() before 'closed', so queued frames are filed. Only an interpreter exit (Ctrl+C in the terminal, an exception out of mainloop) kills a read or a gzip mid-way. on_close offers only 'Wait and quit' (full-job wait, no request_stop) or cancel (gui.py:3039-3050), and _wait_to_quit has no timeout (3052-3068).

</details>

<a id="session-roll-sr-19"></a>

### SR-19 -- A dead worker leaves the window accepting jobs that never run; an unvalidated Calibrate.mode kills the worker

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/session.py:1214-1218`, `rps7200/session.py:1293-1346`, `rps7200/session.py:1318`, `rps7200/session.py:2213-2217`, `tools/gui.py:129-131`, `tools/gui.py:627-636`, `tools/gui.py:1930`, `tools/gui.py:3288-3291`

**Doc claim:** gui.py:620-625 '...should cost the one control it got wrong'

An unknown mode in gui-settings.json (hand-edited or from an older version) raises KeyError outside the try on the first Calibrate, exits the worker loop, closes the device and ends the session; the GUI's own restore docstring promises a bad value 'costs that control alone'. In both this case and a scanner that was not present at launch, every later Scan/Roll press silently does nothing -- no failed event, no message.

**Evidence (from the code):**

```text
`self._emit("state", text=_describe(job), busy=True)` is outside the per-job try (session.py:1318-1319); `_describe` does `{"measure": ..., "reuse": ..., "off": ...}[job.mode]` (session.py:2215-2217). `shading` is a REMEMBERED control restored with `variable.set(value)` unvalidated (gui.py:129-131, 633-634) and used as `Calibrate(mode=mode or self.v_shading.get(), ...)` (gui.py:1930). After the worker exits (open failure at session.py:1301-1304, or the KeyError above), 'closed' re-enables the buttons (`self._set_busy(False)`, gui.py:3290) and submit() still puts jobs on a queue nobody reads.
```

**Failure scenario:** gui-settings.json holds "shading": "cached". Operator presses Calibrate: the worker dies, the log says 'scanner closed', and Scan/Roll buttons stay enabled but do nothing until the window is restarted.

**Fix:** Validate Calibrate.mode in the dataclass (or in the GUI restore), move _describe inside the try, and make submit() raise/emit 'failed' when the worker is not alive; disable run buttons after 'closed'.

<details><summary>Second reader's check</summary>

`self._emit("state", text=_describe(job), busy=True)` (session.py:1318) is outside the per-job try, and `_describe` indexes a dict by job.mode (2215-2217), so an unknown mode raises KeyError out of the while loop and ends the session. 'shading' is in REMEMBERED (gui.py:129-131), is restored by `variable.set(value)` unvalidated (633-634), and is used as `Calibrate(mode=mode or self.v_shading.get())` (1930). After 'closed', `_set_busy(False)` re-enables the run buttons (3288-3291), and submit() still queues jobs to a dead worker without a word. Hand-edited or stale settings are needed for the KeyError. The open-failure path (a scanner absent at launch) reaches the same silent state far more easily.

</details>

<a id="session-roll-sr-20"></a>

### SR-20 -- The only report of dropped raw bytes, failed filings and renumbering doubts is an unpersisted log widget

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `rps7200/session.py:2095-2099`, `rps7200/session.py:2116-2120`, `rps7200/session.py:1348-1356`, `rps7200/session.py:1341-1345`, `tools/gui.py:3432-3434`

Whether an entry lacks its raw bytes because they were judged wrong, whether a frame failed to file, and which frames renumbering doubted, are recorded neither in the entry, nor in roll.json, nor in a file. Once the window closes the evidence is gone; `verify` later reports 'no raw bytes' without the reason.

**Evidence (from the code):**

```text
`self._emit("log", text=(f"raw bytes do not describe this image ({detail}); filing it without them ..."))` (session.py:2096-2098); `self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1354); renumbered's say(...) warnings go to `_emit("log")`. The GUI appends them to a tk.Text (`self.log.insert("end", message + "\n")`, gui.py:3433); nothing writes a log file. FrameWriter.errors are drained only at session close (session.py:1344-1345).
```

**Failure scenario:** A roll of 30 frames finishes overnight with three frames filed without raw bytes. The operator closes the window in the morning; nothing on disk says why those three entries have no raw.bin.gz.

**Fix:** Persist the session log (e.g. rolls/<roll>/session.log and a per-day log), and write drop reasons into scan.json ('raw.dropped_because') and filing failures into roll.json.

<details><summary>Second reader's check</summary>

The drop, filing-failure and renumbering messages go only through `_emit("log")`, and the GUI's `_say` inserts them into a tk.Text (gui.py:3431-3435). No log file is written anywhere in gui.py or session.py (grep). scan.json has no field for why raw bytes are absent.

</details>

<a id="session-roll-sr-21"></a>

### SR-21 -- An exception in the injected edge reader or the aiming code costs the frame's scan or ends the roll

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/direct.py:3517-3518`, `rps7200/direct.py:3570-3575`, `rps7200/direct.py:3666-3678`, `rps7200/framing.py:1680-1681`, `rps7200/framing.py:1729-1733`, `rps7200/session.py:1785`, `tools/gui.py:8186`

**Doc claim:** direct.py:3662-3665 'one frame that decodes to an unexpected shape should cost that frame, not the thirty after it'

Aiming is an optional refinement, but a ValueError from the detector (tools/frame_edges, handed in as session.edge_reader) is counted as a frame failure and the frame is not scanned at all; three in a row end the roll. Any other exception type (IndexError, KeyError, ZeroDivisionError) escapes the generator and fails the whole job mid-strip.

**Evidence (from the code):**

```text
StripWalk delegates unguarded: `return dict(self.reader.observe(int(number), image))` and `decision, detail = self.reader.judge(int(number), image)` (framing.py:1681, 1730). These run inside scan_roll's per-frame try whose except catches only `(UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError)` (direct.py:3666-3667), and before the scan (direct.py:3644).
```

**Failure scenario:** With 'correct' ticked, the edge reader raises IndexError on an unusual frame 9; the roll job fails at frame 9 with the film mid-strip, and frames 9-36 are not scanned.

**Fix:** Wrap reader/aim calls so a detector failure records an abstention in marks['correction'] and the frame is still scanned.

<details><summary>Second reader's check</summary>

StripWalk.observe and judge delegate to the injected reader with no guard (framing.py:1680-1681, 1729-1733), and WalkReader in tools/frame_edges/propose.py has no try/except either. walk.observe runs for every frame inside scan_roll's per-frame try (direct.py:3517-3518), before the scan. A ValueError there becomes a failed, unscanned frame counted toward max_failures; any other exception type escapes the generator and fails the roll job. Whether the detector raises on real film is not shown, so the risk is structural.

</details>

<a id="session-roll-sr-22"></a>

### SR-22 -- Partially written library entries are invisible to entries()/verify(); a reindex failure reports a complete entry as unfiled

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:176-211`, `rps7200/library.py:308-311`, `rps7200/library.py:741-750`, `rps7200/library.py:776-786`, `rps7200/session.py:1046-1058`

**Doc claim:** library.py:28-30 'Entries are self-contained directories'

A writer failure or process death part-way through save() (disk full while gzipping, SR-18 kill) leaves a directory of hundreds of MB with no scan.json that no tool reports or cleans. Conversely, if reindex() raises after scan.json is written, the writer reports 'could not be filed' and the GUI never learns the entry path although the entry is complete.

**Evidence (from the code):**

```text
save() makes the directory and writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz before `(path / "scan.json").write_text(...)` and then `reindex(root)` (library.py:176-211, 308-310). entries() and verify() only glob `*/scan.json` (library.py:745, 780).
```

**Failure scenario:** Disk fills while raw.bin.gz of frame 14 is written: library/<id>/ holds scan.tif and a truncated raw.bin.gz, no scan.json; `make verify` says nothing about it.

**Fix:** Write into a temporary sibling directory and rename it into place when complete; make verify report directories without scan.json; treat reindex failure as a warning after a successful save.

<details><summary>Second reader's check</summary>

save() writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz, then scan.json, then reindex (library.py:176-311). entries(), and therefore verify(), only glob `*/scan.json` (745, 779). A reindex exception after scan.json is written propagates to FrameWriter._run, which reports the frame as not filed although the entry is complete. The id-collision check also looks only for scan.json (library.py:170-174), so a later save with the same id would write into a leftover partial directory.

</details>

<a id="session-roll-sr-26"></a>

### SR-26 -- The two roll writers label library entries differently; the GUI join only parses the session's form

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `rps7200/session.py:1880-1882`, `tools/scan_roll.py:577`, `tools/gui.py:5004-5008`

**Doc claim:** gui.py:4986-4987 'The join is on film.frame, which ScanSession._file sets to "{roll}-{NN}" for every roll frame.'

Entries filed by the command-line roll tool never join to their roll in the GUI's browser (for a date-named roll '2026-09-23/05' parses as roll '2026-09', number '23/05'), so those rolls show no library entries and cannot be exported from the window. The only link between a roll and its entries is this free-text label (SR-03, SR-10).

**Evidence (from the code):**

```text
Session: `frame=job.notes.frame or f"{name}-{number:02d}"`. tools/scan_roll.py:577: `frame=f"{roll_name}/{number:02d}"`. gui.roll_entry_index: `roll, _, number = frame.rpartition("-"); if not roll or not number.isdigit(): continue`.
```

**Failure scenario:** A roll scanned with `tools/scan_roll.py` and reopened in the window is listed with no entries and 'cannot be exported'.

**Fix:** Record the roll name and frame number as structured fields in scan.json (e.g. 'roll': {'name', 'number', 'position'}) written by both writers, and join on those.

<details><summary>Second reader's check</summary>

tools/scan_roll.py:577 labels frames `f"{roll_name}/{number:02d}"`, the session labels them `f"{name}-{number:02d}"` (session.py:1880-1882), and gui.roll_entry_index parses with `frame.rpartition("-")` and `number.isdigit()` (gui.py:5004-5008). A CLI-scanned roll therefore never joins, or joins wrongly ('2026-09-23/05' -> roll '2026-09', number '23/05' is not a digit, so it is skipped).

</details>

<a id="session-roll-sr-23"></a>

### SR-23 -- Stop is not checked between a roll frame's prescan, metering and scan, nor during the seek

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:20-24`, `rps7200/direct.py:3493-3660`, `rps7200/session.py:1619-1633`, `rps7200/session.py:115-165`, `rps7200/session.py:1787-1931`

**Doc claim:** rps7200/session.py:20-24 'The worker checks it before starting a pass and between frames of a roll'

A stop pressed during a frame's prescan still pays the metering probes and the full scan (minutes at 3600 dpi RGBI); a stop pressed during a long rewind is ignored until the rewind ends. When the generator itself honours a stop, the job ends with the ordinary 'finished' text, not 'stopped'.

**Evidence (from the code):**

```text
Module docstring: 'The worker checks it before starting a pass and between frames of a roll'. In scan_roll, should_stop is checked before a frame and before an advance (direct.py:3439, 3457) and between hold moves, but not between `self.prescan(...)`, `self.auto_exposure(...)` and `self.scan(...)` (direct.py:3494-3652). seek()/rewind() never look at the stop flag; _roll checks `_stop` only after the seek (session.py:1631).
```

**Failure scenario:** Operator presses Stop during frame 5's prescan at 3600 dpi RGBI; the scanner still runs ~5 minutes of metering and scan before stopping.

**Fix:** Check should_stop after the prescan/hold and before metering/scan (no pass is in flight there), between frames of a seek, and report 'stopped' when the generator stops early.

<details><summary>Second reader's check</summary>

In scan_roll, should_stop is checked at the top of the frame loop (direct.py:3457), in keep_going before an advance, and inside the hold and aim loops. Nothing checks it between `self.prescan(...)` (3494), `self.auto_exposure(...)` (3622) and `self.scan(...)` (3642). seek() takes no stop callback, and `_roll` checks `_stop` only after the seek (session.py:1631). When the generator itself returns on a stop (after an advance), the for loop ends normally and the job reports the ordinary finished text rather than 'stopped'.

</details>

<a id="session-roll-sr-24"></a>

### SR-24 -- False 'could not be filed: None' log for prescan_before and for a session without a library

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:1084-1103`, `rps7200/session.py:1348-1355`, `rps7200/session.py:1841-1854`, `rps7200/session.py:2132`

Every prescanNN-before.tif (file_entry=False) and every pass of a ScanSession(root=None) produces a 'could not be filed: None' line that reads like a failure, training the operator to ignore the one message that reports real filing losses.

**Evidence (from the code):**

```text
`library=self.root if file_entry else None` (session.py:2132); `entry = None; if job["library"]: ...; self.on_done(job.get("seq", 0), job["number"], entry, None)` (session.py:1084-1103); `_filed`: `if entry is None: self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1353-1354).
```

**Failure scenario:** A corrected dry-run walk logs 'picture 4 could not be filed: None' for every aimed frame though nothing failed.

**Fix:** Distinguish 'not asked to file' from 'failed' in on_done (e.g. pass a sentinel) and log only real failures.

<details><summary>Second reader's check</summary>

`library=self.root if file_entry else None` (session.py:2132). In _write, `entry = None` stays None when `job["library"]` is falsy, and `on_done(..., entry, None)` is called (1084-1103). _filed then logs 'picture N could not be filed: None' (1353-1354) for every prescanNN-before.tif, and for every pass when root is None.

</details>

<a id="session-roll-sr-25"></a>

### SR-25 -- Output-folder copy of a corrected walk's prescan is overwritten by its 'before' picture

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:1817-1854`, `rps7200/session.py:2068-2071`, `rps7200/session.py:2192-2204`

With an output folder set and correction moving a frame in a dry run, both jobs are given the same output path; the writer writes the final prescan and then the before-picture over it, so the delivered prescans folder shows where the frame arrived, not where it was left.

**Evidence (from the code):**

```text
Both `_file` calls for the prescan and for `rf.prescan_before` use kind="prescan", the same number, meta resolution and roll, so `_out_name` is identical; `_unclaimed(where / ...)` checks `wanted.exists()` on the scanner thread before the writer has written the first one (session.py:2071, 2198).
```

**Failure scenario:** Dry run with correct=True and an output folder: out/prescans/<roll>_frame05_300dpi.tif ends up being the pre-correction picture.

**Fix:** Give prescan_before its own suffix in _out_name (e.g. '-before'), or resolve unclaimed names on the writer thread at write time.

<details><summary>Second reader's check</summary>

Both _file calls use kind='prescan', the same number, the same meta resolution and the same roll, so `_out_name` returns the same name. `_unclaimed` runs on the scanner thread at submit time (2071), before the writer has created the first file, so both jobs get the same path. The before-picture is submitted second and overwrites the final prescan.

</details>

<a id="session-roll-sr-27"></a>

### SR-27 -- Stale comments about the SLIDE law, and a millimetre API contrary to the units rule

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:91-95`, `rps7200/session.py:672-693`, `rps7200/session.py:1562-1566`, `rps7200/session.py:1581-1585`, `rps7200/session.py:813-818`, `rps7200/session.py:950-951`

**Doc claim:** CLAUDE.md 'Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter'; session.py:1562 'One SLIDE command tops out at ~1.01 mm'

The comments describe the pre-2026-09-22 cap of param 8 and a 1.01 mm ceiling; a reader sizing moves from them is off by a factor of ~9. The 'report' is a forecast. CLAUDE.md prohibits millimetres for transport distances; the session's public API and the persisted approved.json still carry them.

**Evidence (from the code):**

```text
session.py:91-92 'distance = STEP_MM x param + OVERHEAD_MM, for param 1 and param 8' while FINE_MAX_MM uses `DirectScanner.MAX_CORRECTION_PARAM` = 87 (direct.py:3123); plan_nudges docstring 'for an integer param in 1..8' and 'param_for_mm already clamps silently at param 8'; _move comment 'One SLIDE command tops out at ~1.01 mm' (it is ~9.4 mm at param 87); `moved += abs(out.get("asked_mm", 0.0))` is commented as 'what the scanner says it did ... this line is the report' but asked_mm is the same computed command distance the plan predicts. Move.millimetres, Approved.offset_mm, plan_nudges(millimetres) and approved.json 'offset_mm' hold every sub-frame distance in mm.
```

**Failure scenario:** A maintainer relies on the 'tops out at ~1.01 mm' comment and adds a guard that splits every move into 1 mm chunks, paying the ramp per command.

**Fix:** Update the comments to param 87/88.8 units; say that asked_mm is commanded, not measured; migrate the job and file fields to param units as CLAUDE.md requires (keeping mm only as a derived display).

<details><summary>Second reader's check</summary>

session.py:91-92 says 'for param 1 and param 8', the plan_nudges docstring says 'integer param in 1..8' and 'clamps silently at param 8', and _move says 'tops out at ~1.01 mm'. But FINE_MAX_MM uses MAX_CORRECTION_PARAM = 87 (direct.py:3123), and param_for_mm clamps at that. `moved += abs(out.get("asked_mm", 0.0))` sums the commanded distance that nudge computed (direct.py:3168), not a measurement, although the comment calls it 'the report'. Move.millimetres, Approved.offset_mm and approved.json 'offset_mm' still carry millimetres.

</details>

<a id="session-roll-sr-28"></a>

### SR-28 -- FrameWriter: bounded queue blocks the scanner thread with the device open; no liveness check; errors drained only at session close

**Severity** low · **Category** concurrency · **Verdict** partly

**Where:** `rps7200/session.py:1022-1029`, `rps7200/session.py:1032-1058`, `rps7200/session.py:1105-1111`, `rps7200/session.py:1341-1345`

**Doc claim:** session.py:1027-1029 '`errors` is drained by the caller once the roll ends'

FrameWriter.submit blocks the scanner thread (device open, idle, no log line) when two frames are already queued, and neither submit nor finish checks that the writer is alive or times out. Per-frame failures are logged as they happen through on_done, but the collected `errors` list is replayed only at session close, not at roll end as the docstring says.

**Evidence (from the code):**

```text
`self.queue: queue.Queue = queue.Queue(maxsize=depth)` with depth 2; `def submit(self, **job): self.queue.put(job)` blocks when full; `finish()` does `self.queue.put(None); self._thread.join()` with no timeout; errors are emitted only in `_run`'s finally at session close (session.py:1344-1345), while the docstring says '`errors` is drained by the caller once the roll ends'.
```

**Failure scenario:** Output folder on a slow network share; after two frames the queue is full and the scanner thread waits, device open and idle, until a gzip and two TIFF writes finish.

**Fix:** Log when submit blocks and how long; check writer.is_alive() before blocking puts (use put with timeout); drain errors at the end of each roll as documented.

<details><summary>Second reader's check</summary>

The queue is bounded (maxsize=2), submit blocks, and finish() joins with no timeout: confirmed. The claim that errors are 'drained only at session close' overstates it: each failure is also reported as it happens, through `on_done(..., None, str(exc))` -> `_filed`, which logs 'picture N could not be filed: ...' (session.py:1053-1056, 1353-1354). What waits for close is only the summary loop over `self._writer.errors` (1344-1345), which contradicts the docstring's 'drained by the caller once the roll ends'. The writer thread can die only on a BaseException, so a dead writer is unlikely.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry: decoded pixels | <library>/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF, uint16 (scans) or uint8 (prescans), HxWxC, rows upright per line tags, scanner orientation (no rotation/flip) | RAW for GUI Prescan jobs (last_pixels_raw), roll frames (rf.raw_image) and dry-run prescans (rf.raw_prescan). CORRECTED but labelled raw for GUI single Scan jobs (SR-01), for any shape-mismatch fallback (session.py:2115-2120) and for every demo entry (SR-12). | FrameWriter._write -> library.save (writer thread), session.py:1085-1100, library.py:179 | library.load/corrected (GUI full-res view gui.py:4110, Save As gui.py:3864), library.reconstruct/verify, DemoScanner._decode fallback | Lossless when raw; wrong (double-corrected on read) when the corrected image was filed; sha256 recorded |
| Library entry: scanner bytes | <library>/<entry>/raw.bin.gz | gzip level 6 of the exact INDEX-format byte stream (2-byte channel tag per line); layout in scan.json raw.layout (bytes_per_line, line_stride, width, lines, channels, lines_received) | raw | library.save from capture_record()['raw'] taken in ScanSession._file on the scanner thread right after the pass (session.py:2072) | library.read_raw/decode_raw/reconstruct/verify, DemoScanner._decode | Byte-exact with sha256 when present; DROPPED whenever layout lines/width/channels differ from the image shape, including every short/truncated pass (SR-04); never stored for real-roll prescans, failed-frame prescans or hold/aim verification prescans (SR-05) |
| Library entry: shading reference | <library>/<entry>/shading.npz | ShadingReference.save (npz) | n/a (calibration data) | library.save from capture_record()['reference'] (the session's current reference object at filing time) | library.load/corrected/reconstruct, DemoScanner._decode | Exact copy of the reference in force; demo prescans may carry a previous pass's reference (SR-12) |
| Library entry: CCD mask | <library>/<entry>/ccd_mask.bin | raw bytes, one per calibration column | n/a | library.save from capture_record()['ccd_mask'] (this pass's mask) | library.load/corrected | Exact for the scan; the prescan inside a frame entry has a different (300 dpi) mask that is not stored |
| Library entry: framing pass inside a roll frame entry | <library>/<entry>/prescan.tif | TIFF uint8 RGB | CORRECTED (prescan() runs shading=True), not labelled as such; final (post-hold) prescan only | library.save(prescan=rf.prescan) via FrameWriter (session.py:1887) | library.migrate_direction, demo picture_signature/_pair_image, best_pair | Not re-derivable: no raw bytes, no raw pixels, no mask for it |
| Library entry: record | <library>/<entry>/scan.json | JSON (default=str); whitelisted meta keys, device_settings, metering, registration, calibration report/skipped, prescan read_direction/carriage_state, film notes, tags, provenance (git commit/dirty, versions) | n/a | library.save, written last after all other files | library.entries/reindex/verify/duplicates/prunable, gui.roll_entry_index (joins on film.frame), demo | Drops inquiry (accepted but unused), roll_index, roll_position, demo flag (SR-16); film.frame replaced by operator's field when set (SR-10); non-atomic; entries without it are invisible (SR-22) |
| Library index | <library>/index.json | JSON summary list | derived | library.reindex on every save (writer thread) | humans/tools; derivable | Derived; rewritten non-atomically |
| Roll manifest | rolls/<job.name\|YYYY-MM-DD>/roll.json (or Roll.out) | JSON: roll, numbering='strip', dpi, infrared, meter, film, dry_run, start_at, prescan_resolution, rotation, flipped (at roll start), only, settings{...}, wanted (union), frames[{number,index,transport_position,registration,error,done,exposure,gain,offset}] | n/a | ScanSession._roll on the scanner thread after every frame (session.py:1924), merging renumbered(earlier) frames | ScanSession._roll on resume, session.renumbered/legacy_shift, gui.read_survey/roll_summary/scanned_frames/wanted_frames, tools/roll_registration_study.py | Non-atomic; unreadable earlier file silently replaced (SR-07); done set before filing (SR-03); legacy numbers overwritten on resume (SR-17); no library entry ids |
| Walk manifest | rolls/<job.name\|YYYY-MM-DD>/survey.json | JSON, same shape as roll.json with frames[].prescan='prescanNN.tif' | n/a | ScanSession._roll for dry_run (replaced wholesale per walk) | gui.read_survey/roll_summary, session.walked_prescans, ScanSession._walk_shift, tools/scan_roll.py --approved | Non-atomic; a second walk into the same folder replaces it and leaves stale prescanNN.tif (SR-09); rotation recorded once at start (SR-14) |
| Walk prescans | rolls/<roll>/prescanNN.tif and prescanNN-before.tif | TIFF via export.write, uint8 RGB | CORRECTED, oriented with session.rotation/flip read at filing time (plus any reversal); -before only for aimed frames in dry runs, with no library entry | FrameWriter._write (paths) from ScanSession._file | gui.read_survey (un-orients by the manifest's single rotation), walked_prescans, contact sheet, hold references | Derived deliverable; overwritten by a later walk into the same folder; orientation can disagree with the manifest (SR-14) |
| Roll frames | rolls/<roll>/frameNN.tif | TIFF via export.write, uint16, 3 or 4 channels (or 1 if mono) | CORRECTED, oriented (per-frame Approved rotation/flip or session default, composed with reversal), mono if chosen | FrameWriter._write | operator / NegPy | Derived; overwritten on rescan or by another roll in the same folder (no _unclaimed) |
| Operator's sheet decisions | rolls/<_safe(roll field)\|'roll'>/approved.json | JSON {roll, numbering, frames[{number, offset_mm, rotation, flipped, reference_entry, source}]} | n/a | tools/gui.py ScannerGui._write_approved on the UI thread before submitting the Roll | gui.read_approved via read_survey | Non-atomic; folder name can differ from the roll's folder (empty name -> rolls/roll/) (SR-08); stale file applied to a later strip in the same folder (SR-09); distances in mm |
| Output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng for IR with JPEG), <out_dir>/prescans/..., <out_dir>/<YYYYMMDDTHHMMSS>_<dpi>dpi[_ir].ext for single passes | TIFF or JPEG (8-bit, IR plane to DNG) | CORRECTED, oriented, mono if chosen | FrameWriter._write, name chosen by _out_name/_unclaimed on the scanner thread | operator | Derived; JPEG lossy; a failure here aborts the library entry (SR-02); prescan copy can be overwritten by prescan_before (SR-25) |
| Session shading cache | calibration/shading.npz (or --reference; demo/calibration/... under --demo) | ShadingReference npz | n/a | DirectScanner.ensure_shading -> save_shading on a 'measure' Calibrate job | DirectScanner.load_shading on a 'reuse' Calibrate job | Overwritten by each calibration; no history (each entry keeps its own copy) |
| Session log | (none) -- tk.Text widget only | text in memory | n/a | ScanSession._emit('log') from scanner and writer threads | operator on screen | Not persisted; the only record of dropped raw bytes, failed filings and renumbering warnings (SR-20) |

**Second reader's corrections to this table:**

1) raw.bin.gz: besides the short-pass case (SR-04), the bytes are dropped for EVERY 7200 dpi pass filed through ScanSession, because realignment shortens the image by 4 lines against layout['lines'] (SR-A1). scan.tif at 7200 dpi therefore holds realigned pixels (a host-side transform), not the plain decode. 2) survey.json is replaced by any walk into the folder, including a partial re-walk that starts at a later frame (SR-A3), not only by 'a second walk'. 3) approved.json lands in a different folder from roll.json whenever _safe changes the name at all, including an ordinary space ('Portra 400' -> rolls/Portra-400/ versus rolls/Portra 400/), not only for an empty name. 4) FrameWriter errors are not only in memory: each failure is also logged at once through on_done -> _filed; only the collected list is replayed at session close. 5) scan.json 'film.frame' for single GUI scans and prescans is the free-text field, usually empty, so those entries carry no picture identity and collide in library.signature (SR-A2). 6) Output-folder copies from single passes are named with a timestamp to the second, and prescans go to <out_dir>/prescans/; frameNN.tif and prescanNN.tif in rolls/<roll>/ are overwritten in place with no _unclaimed (the table says so for frames only). 7) Demo entries: scan.tif holds shading-corrected pixels labelled raw, while raw.bin.gz holds the uncorrected bytes of the source entry. Neither the 'demo' flag nor the inquiry is written to scan.json.

## What the operator can do

- Calibrate once per power-on with the film loaded (measure), reuse the cached reference, or turn shading off; the session then carries that reference into every entry it files.
- Prescan the whole transport at a chosen dpi; the pass is filed with its raw pixels and raw bytes and kept as the reference a following Scan of the same counter position is judged against.
- Scan the frame in the gate at a chosen dpi, with or without infrared (tied to resolution by default), auto or fixed exposure, mono delivery for B&W.
- Walk a strip (dry-run Roll) to produce survey.json and prescanNN.tif, then open the contact sheet, tick frames, set positions/turns and commission a roll of only those frames (holds each frame to its approved position).
- Start a roll at any strip frame 1..40 ('Start at'); the session seeks there with checked rewinds/advances and refuses (scanning nothing) if the counter is unknown, implausible, or the strip ends first.
- Move the film one whole frame back/forward, or nudge it sub-frame (the counter does not see nudges; a prescan must confirm).
- Press Stop: cooperative, finishes the pass in flight and stops at the next check (between frames / before an advance / between hold moves).
- Force-abort after typing ABORT: closes the transport under a running read; costs the frame and almost certainly a power cycle.
- Reopen a roll folder from disk to rebuild the sheet and resume the remaining frames.
- Set an output folder and format (TIFF/JPEG) for a second, oriented, corrected copy of every pass.
- Rotate/flip pictures; the arrangement carries to files written afterwards (never to the library entry).

## What the operator should not do

- Do not kill the process (Ctrl+C in the terminal, task manager) while a pass or a roll runs: both worker threads are daemons, so the read is abandoned (wedge) and queued frames are lost while roll.json already says done.
- Do not force-abort unless waiting is truly worse than a power cycle.
- Do not leave the roll name empty when more than one strip is walked or scanned in a day, and do not reuse a roll name for a different strip: unnamed rolls share rolls/<date>/ and overwrite/merge each other.
- Do not put '/', '\\', '..', ':' or Windows device names in the roll name.
- Do not point the output folder at storage that can fill up or disappear during a roll (USB stick, network share): a failure there loses the library entry too.
- Do not rotate or flip pictures while a walk is still running.
- Do not leave anything in the Film 'frame' note when starting a roll; and do not run `tools/library.py duplicates --delete` on roll entries whose film notes do not distinguish the frames.
- Do not use the scanner's own transport keys during a roll or between a walk and its commission without re-walking.
- Do not press the roll shortcut (Ctrl/Cmd-B) or aim-click a prescan while a job is running.
- Do not use 'reuse' calibration from another power-on for real scans; the reference describes a different lamp/exposure state.
- Do not hand-edit gui-settings.json values for controls such as 'shading' to anything other than measure/reuse/off.

## Mistakes nothing guards against

- Pressing Ctrl/Cmd-B during a running job: on_roll has no busy check, so after its OK dialog a second Roll is queued, a Stop already requested is cancelled by submit(), and for a dry run the in-progress walk's survey/sheet state in the window is wiped.
- Aim-clicking a prescan (aim ticked) while a roll runs: a Move is queued behind the roll (applied wherever the roll ends) and any requested Stop is cancelled.
- Commissioning from the sheet with the roll name empty: approved.json is written to rolls/roll/ while the roll and roll.json go to rolls/<today>/.
- Resuming a reopened roll: the resumed frames are written into rolls/<today> (or the typed name), not into the folder that was reopened.
- Walking or scanning a second strip on the same day without a name: frameNN.tif/prescanNN.tif are overwritten, survey.json replaced, roll.json merged, and the first strip's approved.json and done flags are applied to the second strip on reopen.
- A roll name with path separators or characters invalid on Windows: nested/escaping folders, or mkdir fails after the film has already been moved by the seek.
- Output folder full/unplugged/unwritable during a roll: each frame's library entry (raw bytes) is lost, the roll continues, and roll.json marks the frames done so a resume skips them.
- Rotating/flipping a prescan while a walk is running: later prescanNN.tif are written in a different orientation than survey.json records; reopened references mis-correlate.
- Text in the Film 'frame' note at roll start: every roll entry gets the same film.frame, so all share one library signature (duplicates --delete keeps one) and the roll browser cannot find them.
- Closing the window during a long roll: the only choices are wait for the whole roll or cancel; there is no 'stop after this frame and quit'.
- Launching the window from another working directory: library/, rolls/ and calibration/ are resolved relative to the CWD, silently starting a new library there.
- A stale or unexpected 'shading' value in gui-settings.json: the first Calibrate kills the worker, and every later button press silently does nothing.
- Starting the window with no scanner attached, then plugging it in: buttons re-enable after 'closed', but jobs go to a queue nobody reads.

## Dataflow notes

Entry and seam. tools/gui.py:8178-8199 builds ScanSession(root, reference, rolls, out_dir), sets session.edge_reader = frame_edges.walk_reader, and under --demo replaces session._open_scanner with a DemoScanner factory (the only demo switch; session.py itself has no demo branch, but its getattr(..., None) fallbacks at session.py:1412-1415 and 2137 quietly accept a stand-in's missing last_pixels_raw/last_scan_meta). ScanSession.start (session.py:1208) spawns the daemon 'scanner' thread running _run (session.py:1293): _open_scanner() -> _listen (log/progress hooks -> _emit) -> open() -> inquiry() -> _report_position() -> FrameWriter(on_done=self._filed) -> loop on self._jobs.

Jobs. UI thread: submit(job) clears _stop and enqueues (session.py:1214). Worker: _stop.clear(); emit state busy; _dispatch (session.py:1362) -> _calibrate / _prescan / _scan / _roll / _move; then _report_position (READ_STATE) and 'finished', or 'failed' with only type and message. Events go through the unbounded _events queue; the GUI polls every 120 ms (gui.py:3131) and _handle maps 'filed' events onto Result.entry.

Single passes. _prescan (session.py:1401): DirectScanner.prescan -> scan(depth 8, shading=True) returns the corrected image; last_scan_meta and last_pixels_raw (raw) are read right away; _last_prescan=(image, position, meta); _deliver makes a downscaled working copy and emits 'result'; _file queues raw_image=last_pixels_raw. _scan (session.py:1440): scan(... keep_raw=True) returns corrected image + meta; _note_reversal may add meta['reversal']; _deliver; _file WITHOUT raw_image, so the corrected image is what gets filed (SR-01).

Filing (_file, session.py:2031). On the scanner thread: builds destination paths (the roll dir path plus _unclaimed(out_dir[/prescans]/_out_name)); capture_record() = {reference object, this pass's ccd_mask bytes, last_raw bytes, raw_layout}; a shape guard drops raw bytes whose declared lines/width/channels differ from the image shape; orientation = per-frame Approved or session.rotation/flip composed with reversal, written into meta; raw_image is dropped if its shape differs. It then submits to the bounded FrameWriter queue (maxsize 2, which blocks the scanner thread when full). Writer thread (session.py:1046-1103): preview.orient + optional to_monochrome -> export.write for each path (TIFF/JPEG+DNG) FIRST, then library.save(raw_image or image, meta, reference, ccd_mask, raw bytes, prescan, prescan_meta) -> tiff scan.tif, prescan.tif, shading.npz, ccd_mask.bin, gzip raw.bin.gz, scan.json last, reindex(). on_done -> _filed emits 'log' and 'filed'(seq, path). Writer errors are logged per frame and drained at close. Gzip runs whenever the writer has work, including while the device is open and idle after single passes and at the end of rolls (SR-06).

Rolls (_roll, session.py:1608). seek(scanner, start_at-1) (session.py:227): wait_warm, _ask_position (8x1 s polls), rewind() one checked retreat at a time allowing 3 backlash no-ops, or checked advances; raises FilmNotPlaced otherwise. Then: folder = rolls/(name or date) (unsanitised), last_roll_dir set, manifest path survey.json|roll.json; on a real run earlier roll.json is read (parse errors -> {}), renumbered() onto strip numbers and merged; per-frame orientations taken from job.approved. scanner.scan_roll(...) is a generator (direct.py:3292). Per frame it reads position -> place_on_strip -> skips unchosen frames -> prescan (keep_raw) -> registration/contrast (a blank frame ends the roll) -> walk.observe -> either _hold_to_approved (nudges + verification prescans; replaces prescan/raw_prescan/prescan_meta; no prescan_before) or _aim_frame (ensemble judge -> hold; sets prescan_before) -> for a real roll, meter (set_gain_offset + auto_exposure probes) -> scan(keep_raw) -> meta roll_index/roll_position/registration -> yield RollFrame(index, position, image(corrected), meta, prescan(corrected), marks, raw_image, raw_prescan, prescan_meta, prescan_before). Listed exceptions yield an error RollFrame; max_failures consecutive failures end the roll. Session side, per yielded frame: 'transport' event; _deliver prescan; for a dry run it files the prescan (raw_prescan, path prescanNN.tif) and prescan_before (TIFF only, file_entry=False); for a real roll the prescan is not filed on its own; for an image: _note_reversal, _deliver frame, _file(raw_image=rf.raw_image, prescan=rf.prescan corrected, path frameNN.tif, film.frame = notes.frame or '<roll>-NN'); then the manifest record (done computed now, not after filing) replaces any record with the same number, and the whole manifest is rewritten in place; then a stop check. The finally block closes the generator and clears the per-frame orientations. DemoScanner.scan_roll (demo.py:599) replaces this entire generator with its own loop (SR-11).

Moves. _move (session.py:1536): whole frames via advance/retreat, with a transport event per frame; sub-frame via plan_nudges (DirectScanner.param_for_mm, cap 87, up to 8 commands) -> scanner.nudge -> SLIDE, then one position read. Nothing is persisted.

Shutdown. shutdown() enqueues None behind any running or queued job; _run's finally calls scanner.close() (skipped after force_abort), then writer.finish() (joins; the GZIPs of queued frames happen here with the device closed), logs writer errors, and emits 'closed'. force_abort (session.py:1224) sets dead and _stop and closes the transport from the UI thread. In the demo, _work raises UsbError; on hardware the read fails and scan_roll yields an error frame, then stops on should_stop.

Leaves the area: library entries (raw except SR-01/SR-12 cases), rolls/<roll>/{roll.json, survey.json, frameNN.tif, prescanNN[-before].tif}, out_dir copies, UI events. approved.json is written by the GUI, not the session (gui.py:2929), to a folder computed independently of last_roll_dir (SR-08).
