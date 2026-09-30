# Decode, direction, shading and debug filing

Area key `decode-and-debug-filing`. 24 findings: 3 high, 7 medium, 12 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Decode and debug filing (rps7200/direct.py read path, decode_index, _debug_capture/_debug_flush/debug_claim, calibration; direction.py; shading.py; defects.py), checked against the code at 03aacba. HEAD 0fc837d only adds audit/second files. The decode itself is sound. Every byte a completed READ returns is concatenated unchanged, 2-byte tags and trailing partial lines included, and kept as last_raw whenever keep_raw or debug is set. raw_layout records stride, width, lines, channels and lines_received, and decode_index is deterministic and re-runnable from those. Read direction comes from the line tags and is recorded on every pass. The 7200 dpi stagger trim is recorded and replayed. Exposure is clamped to the 16-bit timer. The shading reference and per-pass CCD mask are stored losslessly. The weak points are around the decode rather than in it:

(1) debug_claim throws away the spooled copy before the caller's own filing is confirmed, and callers only file after close(). A failed save therefore loses the pass.

(2) Claimed passes are still spooled to the system temp dir and kept until close(). A roll with debug on holds every frame twice, so the peak-disk problem the flush comments say is fixed is still there.

(3) With debug off (the default), metering probes, hold/aim prescans and every roll frame's own prescan are never stored with raw bytes. The frame entry's prescan.tif holds corrected 8-bit pixels and no label says so.

(4) A pass that was fully read but fails to decode, realign or correct is never filed, and its bytes are discarded. That is exactly the case the raw bytes exist for.

(5) An ambiguous ASC 0x20 during the read is taken as a normal end of data. The image is silently truncated and the device is not marked suspect.

(6) Calibration raw bytes are archived outside the library. Entries corrected with a reused reference are not linked to them, and no code reads them back.

(7) The spool is left behind with no recovery path after force_abort, a crash or a missing close(). Debug entries go to a CWD-relative library rather than the session's.

defects.py is dead in every delivered path.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [DBG-1](#decode-and-debug-filing-dbg-1) | high | data-integrity | confirmed | debug_claim deletes the spooled copy before the claimant's own filing is confirmed |
| [DBG-2](#decode-and-debug-filing-dbg-2) | high | design | confirmed | Claimed passes are still spooled and held in the temp dir until close(), so a debug-on roll keeps every frame twice |
| [DBG-3](#decode-and-debug-filing-dbg-3) | high | data-integrity | confirmed | With debug off (the default), prescans, before-prescans, metering probes and hold/aim passes never reach the library with raw bytes |
| [DBG-4](#decode-and-debug-filing-dbg-4) | medium | error-handling | confirmed | A pass that was read completely but fails to decode, realign or correct is never filed; its raw bytes and command log are discarded |
| [DBG-5](#decode-and-debug-filing-dbg-5) | medium | error-handling | partly | ASC 0x20 during an image read is taken as end of data: pass silently truncated, device not marked suspect |
| [DBG-6](#decode-and-debug-filing-dbg-6) | medium | data-integrity | confirmed | Calibration raw bytes are archived outside the library, unlinked for reused references, and read by nothing |
| [DBG-7](#decode-and-debug-filing-dbg-7) | medium | error-handling | confirmed | Calibration treats any refused read as 'finished' and builds a reference from however many lines arrived |
| [DBG-8](#decode-and-debug-filing-dbg-8) | medium | data-integrity | confirmed | Debug spool is orphaned after force_abort, a crash, or a script that never calls close(); no tool files it and its sidecars are incomplete |
| [DBG-10](#decode-and-debug-filing-dbg-10) | medium | user-error | partly | Probe tools accept RPS7200_DEBUG=0/false as 'on' while DirectScanner treats it as off |
| [DBG-A1](#decode-and-debug-filing-dbg-a1) | medium | data-integrity | found-by-verifier | Debug-filed metering probes and hold prescans record film 'negative' whatever is loaded, and carry no role or link to the frame they served |
| [DBG-9](#decode-and-debug-filing-dbg-9) | low | user-error | partly | Debug entries are filed into RPS7200_DEBUG_ROOT or ./library, not the library the session or tool is using |
| [DBG-11](#decode-and-debug-filing-dbg-11) | low | data-integrity | partly | Payload followed by CHECK CONDITION is discarded by the transport, losing the last chunk of a pass or calibration |
| [DBG-12](#decode-and-debug-filing-dbg-12) | low | bug | confirmed | apply_shading silently corrects only part of the frame when the mask maps fewer columns than the pass has |
| [DBG-13](#decode-and-debug-filing-dbg-13) | low | data-integrity | confirmed | Debug capture copies meta before callers add bracket, roll and registration fields |
| [DBG-14](#decode-and-debug-filing-dbg-14) | low | data-integrity | confirmed | set_gain_offset silently wraps gain and offset above 255 while the record keeps the unwrapped value |
| [DBG-15](#decode-and-debug-filing-dbg-15) | low | doc-mismatch | confirmed | _CommandLog lists every NoDataYet-refused image READ, bloating each entry's commands, contrary to its docstring |
| [DBG-16](#decode-and-debug-filing-dbg-16) | low | doc-mismatch | confirmed | auto_exposure's comment says SET GAIN OFFSET persists; the rest of the code and CLAUDE.md say the read-back is a fixed reference |
| [DBG-17](#decode-and-debug-filing-dbg-17) | low | doc-mismatch | partly | CLAUDE.md says a single scan compresses nothing while the device is open; the spool compresses the shading reference with the device open |
| [DBG-18](#decode-and-debug-filing-dbg-18) | low | hardware-safety | confirmed | stop_scan() is a public method that sends STOP SCAN, which CLAUDE.md forbids; it is unused and unguarded |
| [DBG-19](#decode-and-debug-filing-dbg-19) | low | demo-divergence | confirmed | The demo stand-in has no debug spooling, no command log and no read_planes, so make run-demo never exercises debug filing |
| [DBG-A3](#decode-and-debug-filing-dbg-a3) | low | error-handling | found-by-verifier | read_planes reads image data with retries=1, so a queued one-shot sense on an image READ abandons the pass (suspect) or ends it as 'end of data' |
| [DBG-A4](#decode-and-debug-filing-dbg-a4) | low | data-integrity | found-by-verifier | A failed pass's command log keeps recording after scan() or calibrate_shading() raise before the logger is stopped |
| [DBG-20](#decode-and-debug-filing-dbg-20) | info | dead-code | confirmed | defects.py (destripe, column-defect detection, resample_reference) is not used by any delivered path |
| [DBG-A2](#decode-and-debug-filing-dbg-a2) | info | design | found-by-verifier | Exposure 16-bit timer wrap is guarded; gain/offset are not (observation) |

## Findings in full

<a id="decode-and-debug-filing-dbg-1"></a>

### DBG-1 -- debug_claim deletes the spooled copy before the claimant's own filing is confirmed

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:1000-1016`, `rps7200/direct.py:1055-1059`, `rps7200/direct.py:1134-1145`, `tools/scan.py:266`, `tools/scan.py:295-300`, `tools/scan.py:366-375`, `tools/scan_roll.py:475`, `tools/scan_roll.py:730`, `tools/scan_roll.py:803`, `rps7200/session.py:2872-2875`, `rps7200/session.py:1972-1979`

A claim is only a promise to file, but _debug_flush treats it as done. It unlinks the claimed pass's spooled image, raw bytes and meta at close(). Every claimant (tools/scan.py, tools/scan_roll.py, ScanSession) does its own filing after close() or on a writer thread that finishes after close(). If that filing fails, neither copy exists. The protection added so that a spool survives a failed save does not cover claimed passes, which are the main scans.

**Evidence (from the code):**

```text
direct.py:1055 `if item.get("claimed"):` / `# Its caller filed it, with these same bytes and pixels.` / `stuck += self._debug_unlink(item)`; close(): `self.t.close()` then `self._debug_flush()`. tools/scan.py:296 `s.debug_claim(raw)` inside `with DirectScanner(...) as s:` (266), and only after the with-block closes: 367 `for held in pending: entries.append(library.save(...))`, with no per-item try. scan_roll.py:730 `s.debug_claim(frame.raw_image)` then `writer.submit(...)`, and `writer.finish()` (803) runs after the with at 475 has exited. session.py:2875 `claim(raw_image)` before `self._writer.submit(...)`, and _run's finally calls `self._scanner.close()` (1974) before `self._writer.finish()` (1979).
```

**Failure scenario:** RPS7200_DEBUG=1 and `tools/scan.py --bracket 5 --library /mnt/full`. All five passes are claimed and close() unlinks their spools. The first library.save hits ENOSPC and raises out of main(). All five passes' raw bytes and pixels are gone, and the temp spool is rmtree'd because the flush itself saw no failure. The same happens to the last roll frame when FrameWriter fails in the window.

**Fix:** Make the claim conditional: keep the spooled files until the claimant reports success (e.g. debug_filed(pixels, entry_path) from FrameWriter/library.save callbacks). Alternatively flush after the claimant's filing and only unlink claimed items whose entry exists and verifies. In tools/scan.py, file each pending item in its own try so one failure does not lose the rest.

<details><summary>Second reader's check</summary>

direct.py:1055-1059 unlinks a claimed item's spool in _debug_flush unconditionally (`if item.get("claimed"): ... stuck += self._debug_unlink(item); continue`). close() (1134-1145) runs the flush right after t.close(). tools/scan.py claims in hold() (s.debug_claim(raw), ~296) inside the with-block (266) and only files in the loop at 366-375 after the with has exited, with no per-item try; a library.save failure raises out of main with the pixels/bytes only in memory. session.py:2872-2875 claims before self._writer.submit, and FrameWriter._write (1618-1705) keeps no copy when library.save raises (_run only appends to errors). In the window the claimed spool is unlinked at close regardless of whether the writer failed hours earlier. So the one redundant copy is dropped on a promise, not on confirmation. Without debug the same failure would also lose the data, but debug is the stated safety net and the claim defeats it.

</details>

<a id="decode-and-debug-filing-dbg-2"></a>

### DBG-2 -- Claimed passes are still spooled and held in the temp dir until close(), so a debug-on roll keeps every frame twice

**Severity** high · **Category** design · **Verdict** confirmed

**Where:** `rps7200/direct.py:922-998`, `rps7200/direct.py:3222`, `rps7200/direct.py:1090-1100`, `rps7200/direct.py:925-928`

The per-item unlink only helps unclaimed passes. On a roll the frames are claimed by the caller, which files them itself, yet each frame is spooled uncompressed and kept until the session closes. That peak is the one the comment describes as solved. The spool also lives in the system temp dir, which is tmpfs (RAM-backed) on several Linux distributions and sits on the system disk on Windows. When it fills, _debug_capture fails and the error is swallowed. Every later unclaimed pass (metering probes, hold/aim prescans, roll prescans) then goes unfiled with one log line. If temp and library share a disk, the FrameWriter's library writes fail as well, and together with DBG-1 those frames are lost.

**Evidence (from the code):**

```text
scan() always calls `self._debug_capture(raw_pixels, meta)` (3222), which writes `np.save(image_path, image)` and `raw_path.write_bytes(raw)` to `tempfile.mkdtemp(prefix="rps7200-debug-")`. The claim arrives later and only sets `item["claimed"] = True`. Files are unlinked only in `_debug_flush`, at close(). The comment at 1090-1095 says: "At 7200 dpi a frame spools 1.1 GB, so holding all 38 of a roll through the flush would want 43 GB of disk ... the peak is what runs a machine out of space". The capture failure path is `except Exception as exc: self._log(f"debug: could not spool this scan ({exc})")`.
```

**Failure scenario:** A 38-frame 3600 dpi RGBI roll from the window with RPS7200_DEBUG=1 grows the temp dir by ~142 MB of pixels plus ~142 MB of raw bytes per frame (≈11 GB). At 7200 dpi shading=False it is ≈43 GB. On a 16 GB machine with /tmp on tmpfs, spooling fails around frame 20. The remaining frames' prescans and metering probes are never filed, and memory pressure rises during the scan.

**Fix:** Let the caller claim before the spool write: a flag on scan(), or check a claim registry keyed by pass id. Alternatively unlink a claimed item's files as soon as the claimant confirms filing. Spool beside the debug root (same volume as the library), not in tempfile's default dir. Surface spool failures as an event, not only a log line.

<details><summary>Second reader's check</summary>

scan() always calls self._debug_capture(raw_pixels, meta) (direct.py:3222), which np.save()s the pixels and write_bytes() the raw blob into tempfile.mkdtemp(prefix="rps7200-debug-") (925-962). debug_claim (1000-1016) only sets item["claimed"]=True; files are removed only in _debug_flush at close(). The window's ScanSession keeps one DirectScanner open for its whole life (session.py _run loop, close only in finally at 1972-1974), so every claimed frame of every roll stays spooled until the window quits. The comment at 1090-1100 names exactly this peak as what runs a machine out of space. The capture's `except Exception as exc: self._log(f"debug: could not spool this scan ({exc})")` swallows ENOSPC, so later unclaimed passes (the only ones debug exists for) silently go unfiled.

</details>

<a id="decode-and-debug-filing-dbg-3"></a>

### DBG-3 -- With debug off (the default), prescans, before-prescans, metering probes and hold/aim passes never reach the library with raw bytes

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:556-561`, `rps7200/direct.py:922-923`, `rps7200/direct.py:3975-3976`, `rps7200/direct.py:4137-4141`, `rps7200/session.py:2574-2587`, `rps7200/session.py:2620-2628`, `rps7200/library.py:269-270`, `rps7200/library.py:378-382`, `tools/scan_roll.py:752`

A pass that no caller files is filed only through debug mode, which is off unless the environment variable is set. A real roll stores each frame's framing prescan only as the corrected 8-bit picture inside the frame entry (prescan.tif). It has no raw bytes and no raw pixels, and nothing in the record says it is corrected. The prescan before an aim correction, the hold loop's verification prescans and every metering probe are discarded outright. The owner's requirement (exact bits of every scan) and CLAUDE.md's "file every scan, with its raw bytes" are therefore not met by the default GUI and CLI configuration. The pictures the registration and hold decisions were made from cannot be re-decoded or re-corrected.

**Evidence (from the code):**

```text
`self.debug = (os.environ.get(self.DEBUG_ENV, "").strip().lower() in {"1", "true", "yes", "on"} if debug is None else bool(debug))`; `_debug_capture`: `if not self.debug: return`. Roll: `prescan_image, _ = self.prescan(...)` (corrected) → `RollFrame(..., prescan_image, ..., raw_prescan=raw_prescan)`. The session files the frame with `raw_image=rf.raw_image, prescan=rf.prescan`. The before-prescan is filed with `file_entry=False`. library.save: `tiff.write(str(path / "prescan.tif"), prescan, ...)` and records only `{"file": "prescan.tif", "read_direction", "carriage_state"}`. RollFrame carries no prescan raw bytes.
```

**Failure scenario:** An operator scans a roll from the window without RPS7200_DEBUG. A later change to apply_shading (or a question about why frame 7 was nudged) needs frame 7's prescan as the device sent it. Only a corrected 8-bit prescan.tif exists, corrected by that day's code, and no record says so.

**Fix:** File the roll's prescan raw pixels and raw bytes with the frame entry (carry `last_raw`/`last_raw_layout` on RollFrame like raw_prescan). Mark prescan.tif as corrected in the record, or store the raw one. Consider making low-cost passes (300 dpi probes and prescans, ~1 MB) always filed regardless of debug.

<details><summary>Second reader's check</summary>

DirectScanner.__init__ enables debug only on the env var or debug=True (direct.py:556-561); _debug_capture returns immediately when off (922-923). On a real (non-dry-run) roll the frame's prescan reaches the library only as library.save(prescan=rf.prescan) (session.py:2618-2622; tools/scan_roll.py:748-751), and library.save writes it as tiff prescan.tif (library.py:269-270) with a record of only {file, read_direction, carriage_state} (378-382): no raw pixels, no raw bytes, no corrections flag, no exposure/commands. rf.prescan is prescan()'s return, which is the shading-corrected 8-bit picture (prescan passes shading=shading, default True, direct.py:2076-2087). RollFrame does carry raw_prescan pixels (direct.py:4134-4136) but no caller files them on a real roll (session files raw_prescan only under `if job.dry_run`, 2535-2566; scan_roll.py:667 likewise only on the dry-run branch). prescan_before is filed with file_entry=False (session.py:2585). Metering probes (auto_exposure scan at 2492) and hold verification prescans (3377) are never filed by any caller. With debug off (the default for the operator) none of these passes' bytes survive.

</details>

<a id="decode-and-debug-filing-dbg-4"></a>

### DBG-4 -- A pass that was read completely but fails to decode, realign or correct is never filed; its raw bytes and command log are discarded

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/direct.py:1878-1904`, `rps7200/direct.py:1953-1970`, `rps7200/direct.py:3070-3076`, `rps7200/direct.py:3090-3097`, `rps7200/direct.py:3115-3151`, `rps7200/direct.py:3222`, `rps7200/direct.py:3280-3294`, `rps7200/direct.py:4147-4156`

Raw bytes are kept so the decode can be fixed later, yet the one case where the decode visibly fails drops them, debug mode included. The bytes are still in self.last_raw, but no caller (scan_roll, ScanSession, tools/scan.py) files them on failure, and the next pass overwrites them. The failure also costs the pass's commands record and skips the vendor-style READ STATE settle (finish_scan). A MemoryError from decode_index's per-line copies (blob plus per-line bytes plus np.array plus np.stack, about 4x a 570 MB 7200 dpi RGBI pass) ends the same way.

**Evidence (from the code):**

```text
read_planes: `self._read_complete = True` ... `self.last_raw = blob` ... `image, direction = self.decode_index(blob, params, channels)`. decode_index raises `ScanReadError("no recognisable channel tags ...")` / `ScanReadError(f"expected {channels} channels ...")`. np.frombuffer raises ValueError on an odd 16-bit line. Also `_realign_native_column_stagger` raises ValueError when `h <= lines`, and scan() raises `ShadingUnavailable(f"this pass came back {params.width} columns ...` after the pass. `_debug_capture(raw_pixels, meta)` is only reached at line 3222. scan()'s `finally: commands = stopper()` value is dropped when the exception propagates. The roll catches `(UsbError, ScanReadError, ..., ValueError)` and yields `RollFrame(..., error=str(exc), ...)` with no bytes. `_read_pass`'s except path skips `self.finish_scan()`.
```

**Failure scenario:** Firmware sends an unexpected tag, or an RGBI pass comes back with three planes. decode_index raises ScanReadError after all 5+ minutes of data are in. The roll yields an error frame and moves on. The bytes that would show what the scanner actually sent are gone even with RPS7200_DEBUG=1.

**Fix:** In scan(), wrap post-read processing: on any exception after `_read_complete`, spool/file last_raw + raw_layout + commands + params as a 'decode-failed' debug entry (and hand them to the caller via the exception or RollFrame). Call finish_scan() whenever the read completed. Decode without per-line bytes copies (np.frombuffer over the blob with a strided view).

<details><summary>Second reader's check</summary>

read_planes sets _read_complete=True and last_raw=blob (1878-1896) before decode_index (1904), which raises ScanReadError on missing/extra channel tags (1950-1961); _realign_native_column_stagger raises ValueError (2031-2035); scan() raises ShadingUnavailable after the pass (3115-3126). _debug_capture is reached only at 3222, so none of these paths spools anything, even with debug on. _read_pass's except path (3280-3291) skips finish_scan (the else branch), and scan()'s `finally: commands = stopper()` value is dropped. scan_roll's except (4147-4156) yields RollFrame(error=...) with no bytes; no caller reads last_raw on failure and the next pass clears or overwrites it. Real, but it needs an anomalous pass (unexpected tags, 3-plane RGBI, wider pass than predicted), so medium rather than high.

</details>

<a id="decode-and-debug-filing-dbg-5"></a>

### DBG-5 -- ASC 0x20 during an image read is taken as end of data: pass silently truncated, device not marked suspect

**Severity** medium · **Category** error-handling · **Verdict** partly

**Where:** `rps7200/direct.py:1787-1797`, `rps7200/direct.py:1862-1864`, `rps7200/direct.py:1878-1880`, `rps7200/direct.py:3155-3162`, `rps7200/protocol.py:412-414`

read_planes accepts end-of-data (ASC 0x20, which the protocol module itself says is indistinguishable from 'invalid command') at any point in a pass as a normal finish: it marks the read complete (no suspect), decodes whatever arrived and returns it as an ordinary pass. The pass meta does not record the expected line count (params.lines), so without raw bytes a truncated pass is indistinguishable in the library from a complete one; only a log line says so. If the 0x20 did not really mean exhaustion, the device is left mid-scan with no suspect flag.

**Evidence (from the code):**

```text
protocol.py: `#: ASC reported once a scan is exhausted. Indistinguishable by sense alone from a genuinely invalid command` / `ASC_END_OF_DATA = 0x20`. read_lines: `if last.end_of_data: raise EndOfData(...)`. read_planes: `except EndOfData: self._log(f"end of data at {got}/{total_lines} lines"); break` then `self._read_complete = True`. calibrate_shading's comment: "Polling here instead appears to disturb the calibration: doing so left every read refused with ASC 0x20." meta records `"height": int(image.shape[0])` but not params.lines.
```

**Failure scenario:** A one-shot condition makes one READ fail with sense 0x20 halfway through a 3600 dpi frame. The frame is filed at half height as an ordinary entry. The scanner is still mid-scan, and the next frame's SLIDE, MODE SELECT and START SCAN go to it. That is the abandoned-read state that needs a power cycle.

**Fix:** Treat EndOfData before `got == total_lines` as suspicious. Retry once after TEST UNIT READY, then either mark suspect or flag the pass as short (`meta['lines_expected']`, `meta['lines_received']`, `meta['short_read']`) and raise to the caller rather than returning a normal image. Always record params.lines and received lines in meta, not only in raw_layout.

<details><summary>Second reader's check</summary>

Code is as described: read_lines raises EndOfData whenever the sense ASC is 0x20 (1791-1794, protocol.py:412-414 says 0x20 is indistinguishable from 'invalid command'), read_planes catches it at any line count and breaks (1862-1864), then marks the read complete (1880) so no suspect is set, decodes a short image and returns it as a normal pass. The meta records only height=image.shape[0] (3161); the expected line count params.lines appears only in raw.layout, which exists only when bytes were kept. An empty first read gives decode_index's 'no recognisable channel tags' with _read_complete already True, so again no suspect. What is speculative is the failure scenario: nothing in the code or captures shows a 0x20 arriving mid-image-read while the device is still scanning (the calibration comment is about calibration reads after polling). So the defect is a silent acceptance and non-recording of a short pass, and the wedge is a possibility rather than a demonstrated path.

</details>

<a id="decode-and-debug-filing-dbg-6"></a>

### DBG-6 -- Calibration raw bytes are archived outside the library, unlinked for reused references, and read by nothing

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:713-734`, `rps7200/direct.py:749-804`, `rps7200/direct.py:854-860`, `rps7200/direct.py:3208-3211`, `rps7200/shading.py:75-91`, `tools/uniformity.py:735`, `rps7200/library.py:28-30`

Each entry stores the reduced reference (shading.npz), not the bytes it was reduced from. calculate_shading's level split and averaging is exactly the code that may improve, and re-running it needs data.bin. That file lives in `<reference dir>/<stamp>/`, outside the library and not copied into entries. Entries corrected with a reused cache have no link to it at all. The archive is written non-atomically, has no INCOMPLETE marker and no loader. The library's own claim that entries are self-contained is false for the calibration input.

**Evidence (from the code):**

```text
`self.archive_calibration(result, path.parent)` writes data.bin under the cache folder. The entry only gets `"shading_origin": dict(origin)` with `origin["archive"] = str(archive)`, a CWD-relative path string. load_shading sets `{"action": "loaded", "path": str(path), "file_modified_utc": ..., "loaded_utc": ...}`, with no archive or hash. ShadingReference.save stores only ref/mean/dark/pixels_per_line. tools/uniformity.py: `result = scanner.calibrate_shading()` (keep_data False, no archive). grep finds no reader of data.bin or calibration.json.
```

**Failure scenario:** The dark/light split in calculate_shading is found to misclassify a block. To re-correct last month's --reuse scans, one must guess which calibration/<stamp>/data.bin produced the cached shading.npz by comparing modification times. After the calibration folder is cleaned or the library is moved to another machine, it cannot be done at all.

**Fix:** Copy (or hard-link) data.bin and calibration.json into each entry, or store a content hash of data.bin inside shading.npz and in shading_origin, and keep the calibration archive under the library root. Add a loader that rebuilds a reference from an archive. Write the archive with the same INCOMPLETE/atomic pattern as library.save.

<details><summary>Second reader's check</summary>

ensure_shading archives via archive_calibration(result, path.parent) (direct.py:854-860) into <reference dir>/<stamp>/ with plain write_bytes/write_text (no temp+rename, no INCOMPLETE marker, 781-803), and links it from entries only as `self._shading_origin["archive"] = str(archive)` (859-860), a path relative to the CWD when the reference path is relative (GUI default home=Path('.')). load_shading's origin (722-729) has no archive link or hash, so every entry corrected with a reused cache has no pointer to the calibration bytes. grep finds no reader of data.bin/calibration.json in rps7200/ or tools/. tools/uniformity.py:735 calls calibrate_shading() directly with keep_data False, so nothing is archived there.

</details>

<a id="decode-and-debug-filing-dbg-7"></a>

### DBG-7 -- Calibration treats any refused read as 'finished' and builds a reference from however many lines arrived

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/direct.py:2305-2313`, `rps7200/direct.py:2323-2332`, `rps7200/direct.py:2351`, `rps7200/shading.py:135-182`

A calibration cut short by an unrelated refusal is not marked suspect, even though the device may still be mid-calibration. It also yields a reference built from a partial pass. Because dark lines come first, a pass stopped during the dark phase gives a ratio below split_ratio. Every line is then treated as 'light', and the 'reference' is the ~170-count dark floor. The session then corrects every scan with it, and each entry stores that reference as its calibration. The block count (40 expected) and the light mean (~47,000) are logged but never checked.

**Evidence (from the code):**

```text
`except (EndOfData, ScanReadError): self._log(f"  scanner finished after {blocks} blocks"); ended = True; break`. read_lines raises ScanReadError for any refusal whose sense is not 0x20, unreadable sense included. Then `self._shading = calculate_shading(data, width)`. calculate_shading returns a reference from a single line per channel: `if stride <= 2 or len(data) < stride: return None`, with no minimum line count and no light-level check.
```

**Failure scenario:** The 6th READ of the calibration is refused with a unit-attention sense. The calibration 'finishes' with 5 blocks of dark lines and installs a dark-only single-point reference. Scans come out with the dark pattern divided in, and are filed and marked 'applied'.

**Fix:** Only EndOfData ends the calibration. Other refusals should retry or mark suspect. Reject a reference with fewer lines than declared by the descriptor, without a two-phase split, or with a light mean far from the expected level. Keep the previous reference in that case.

<details><summary>Second reader's check</summary>

calibrate_shading's read loop catches `(EndOfData, ScanReadError)` and sets ended=True (2311-2314), so any refused read (read_lines raises ScanReadError for every non-0x20 sense with retries=1, 1795-1797) ends the calibration as 'finished' with no suspect. calculate_shading (shading.py:135-182) has no minimum line count or level check: fewer than split_ratio between min and max makes every line 'light', so a calibration cut short during the dark phase yields a dark-floor 'reference'. The block count and means are only logged (2362-2366).

</details>

<a id="decode-and-debug-filing-dbg-8"></a>

### DBG-8 -- Debug spool is orphaned after force_abort, a crash, or a script that never calls close(); no tool files it and its sidecars are incomplete

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1855-1875`, `rps7200/session.py:1972-1974`, `rps7200/direct.py:925-928`, `rps7200/direct.py:969-977`, `rps7200/direct.py:983-990`, `rps7200/direct.py:1039`, `rps7200/direct.py:1134-1145`

The comment says a spool left behind 'can be filed later by hand', but no code does it. The sidecar does not say which NNN-shading.npz applies to a pass (only the first pass under each reference has one). It also does not say whether the pass was claimed and filed elsewhere, so filing by hand risks the wrong reference and duplicate entries. In the GUI, force_abort (the recovery for a stuck pass, and the moment evidence matters most) guarantees the session's probes and prescans stay in the temp dir. That dir is cleared at reboot on many systems.

**Evidence (from the code):**

```text
force_abort: `self.dead = True` ... `transport.close()`. The finally block runs `if not self.dead: self._scanner.close()`, so DirectScanner.close() and its `_debug_flush()` never run. The sidecar holds `{"meta": ..., "raw_layout": ..., "captured": ...}` only. The shading file is written once per reference (`f"{n:03d}-shading.npz"`), claims are held only in memory (`item["claimed"] = True`), and there is no atexit/weakref.finalize. grep finds no tool referencing `rps7200-debug`. _debug_flush swaps `pending, self._debug_pending = self._debug_pending, []` before filing, so an interrupted flush leaves `_debug_spool` set while pending is empty. A reused object then restarts at `n = 0` and overwrites `000-image.npy`.
```

**Failure scenario:** A roll hangs and the operator uses force-abort, then power-cycles and reboots. Every metering probe and hold prescan of the session was spooled under /tmp/rps7200-debug-*. None were filed, and the reboot wipes them.

**Fix:** Write reference/mask paths and a 'claimed' marker into each sidecar. Add `tools/library.py file-spool DIR` to file an orphaned spool. In force_abort, run `scanner._debug_flush()` after closing the transport. Register a weakref.finalize/atexit flush or at least a warning. Spool under the debug library root rather than the system temp.

<details><summary>Second reader's check</summary>

force_abort sets self.dead=True and closes the transport (session.py:1855-1875); _run's finally does `if not self.dead: self._scanner.close()` (1972-1974), so _debug_flush never runs and the spool stays in the system temp dir. No atexit/finalizer exists (grep), and no tool references 'rps7200-debug'. The sidecar (direct.py:969-977) holds only meta, raw_layout and captured: not the claimed flag (in-memory only, 1015-1016) and not which NNN-shading.npz applies (reference_path is in the item, not the sidecar, 983-990). Filing it by hand risks duplicates and the wrong reference. The n=0 overwrite needs _debug_flush to return early after swapping pending out (library import failure, 1041-1047), which is narrow but real.

</details>

<a id="decode-and-debug-filing-dbg-10"></a>

### DBG-10 -- Probe tools accept RPS7200_DEBUG=0/false as 'on' while DirectScanner treats it as off

**Severity** medium · **Category** user-error · **Verdict** partly

**Where:** `rps7200/direct.py:556-561`, `tools/gain_probe.py:115`, `tools/fast_ir_probe.py:177`, `tools/fast_ir_probe.py:430`, `tools/byte14_probe.py:113`, `tools/roll_registration_walk.py:242`, `tools/hold_probe.py:114`, `tools/transport_truth.py:107`, `tools/exposure_probe.py:216`

Every probe tool's 'refusing to run without RPS7200_DEBUG=1' guard (including exposure_probe) tests only that the variable is non-empty, while DirectScanner turns filing on only for 1/true/yes/on. Any other non-empty value (0, false, 2, y) passes the guard and the probe's hardware passes are then left unfiled, which is the loss the guard exists to prevent.

**Evidence (from the code):**

```text
Probe guards: `if not os.environ.get("RPS7200_DEBUG"): print("refusing to run without RPS7200_DEBUG=1: a probe that files ...")`, then `scanner = DirectScanner(verbose=True)` (debug=None). DirectScanner enables debug only for `in {"1", "true", "yes", "on"}`. exposure_probe has no guard at all.
```

**Failure scenario:** `RPS7200_DEBUG=2 uv run python tools/byte14_probe.py` runs a full ladder on the hardware and files nothing.

**Fix:** Guard on the scanner's own decision: construct the scanner first and refuse if `not scanner.debug`. Or share one parser (a DirectScanner.debug_from_env() classmethod) between the guards and __init__. Add the guard to exposure_probe.

<details><summary>Second reader's check</summary>

The mismatch is real: the probe guards test `os.environ.get("RPS7200_DEBUG")` for non-empty (gain_probe.py:115, fast_ir_probe.py:177/430, byte14_probe.py:113, roll_registration_walk.py:242, hold_probe.py:114, transport_truth.py:107, exposure_probe.py:216), then build DirectScanner(verbose=...) with debug=None, which enables filing only for {'1','true','yes','on'} (direct.py:556-561). RPS7200_DEBUG=0/2/y passes the guard and files nothing. But the claim that exposure_probe has no guard at all is wrong: exposure_probe.py:216-218 has the same guard (using DirectScanner.DEBUG_ENV).

</details>

<a id="decode-and-debug-filing-dbg-a1"></a>

### DBG-A1 -- Debug-filed metering probes and hold prescans record film 'negative' whatever is loaded, and carry no role or link to the frame they served

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:2492-2503`, `rps7200/direct.py:3377-3378`, `rps7200/direct.py:3155-3160`, `rps7200/direct.py:1063-1074`, `rps7200/demo.py:230-233`

These passes exist only in the library through debug filing, and the record is their only description. Every metering probe and hold/aim verification prescan says film=negative even on a slide or B&W roll. Nothing says the entry is a metering probe (or which round) or a hold prescan, and nothing links it to the roll, frame number or the frame entry whose `metering`/`registration` it is the evidence for. roll_prescan passes do pass film (3968-3973), so the error is limited to the passes that only debug files.

**Evidence (from the code):**

```text
auto_exposure's probe: `image, _ = self.scan(resolution=resolution, infrared=False, exposure_scale=scales, shading=shading, keep_raw=True,)` with no film argument, so scan's default `film: str = FILM_NEGATIVE` goes into `meta = {..., "film": film, ...}`. The hold loop: `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)`, again without film. _debug_flush files them with `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]`. demo.py itself notes: 'The driver's probes are `scan` calls with no film'.
```

**Failure scenario:** A B&W roll is scanned with RPS7200_DEBUG=1. Later a question about blue headroom (per-film BLUE_RGBI_HEADROOM) is answered from the library's metering probes. Each probe entry says film 'negative' and 'debug', so they are sorted under the wrong film, and matching them to their frames needs guessing from timestamps.

**Fix:** Pass film through auto_exposure's probe scan and the hold/aim prescans, and add a role field (e.g. meta['pass_role'] = 'metering-probe' with round number / 'hold-prescan' with roll and frame index) before _debug_capture copies the meta.

<a id="decode-and-debug-filing-dbg-9"></a>

### DBG-9 -- Debug entries are filed into RPS7200_DEBUG_ROOT or ./library, not the library the session or tool is using

**Severity** low · **Category** user-error · **Verdict** partly

**Where:** `rps7200/direct.py:1049`, `rps7200/library.py:53`, `tools/gui.py:8856`, `tools/gui.py:8886`, `tools/scan.py:140`, `tools/scan_roll.py:175`

Debug filing always goes to RPS7200_DEBUG_ROOT or ./library, ignoring the library the caller was given. With the defaults these coincide, but when --library DIR is passed to the window, scan.py or scan_roll.py (or in the demo, whose root is under demo/), the claimed frames go to DIR and the debug-only passes (metering probes, hold/aim prescans, unclaimed passes) go to a CWD-relative library/, silently.

**Evidence (from the code):**

```text
`root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` where `DEFAULT_ROOT = Path("library")` (CWD-relative). The window: `root=args.library or str(home / "library")`. The tools take `--library DIR`.
```

**Failure scenario:** `uv run python tools/gui.py --library D:/scans/lib` launched from C:/Users/stefan with RPS7200_DEBUG=1. Frames land in D:/scans/lib, and the probes and prescans that explain them land in C:/Users/stefan/library.

**Fix:** Give DirectScanner a debug_root attribute that ScanSession and the tools set to their own library root. Fall back to the env var or DEFAULT_ROOT only when no caller sets one, and resolve it to an absolute path at construction.

<details><summary>Second reader's check</summary>

_debug_flush uses `os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` (direct.py:1049) with DEFAULT_ROOT=Path('library') (library.py:53); nothing sets RPS7200_DEBUG_ROOT. But the defaults agree: the GUI's home is Path('.') outside the demo (gui.py:8856) so its root is ./library, and tools/scan.py and scan_roll.py default --library to 'library'. The split only happens when the operator passes an explicit --library DIR (or launches the demo, where home is DEMO_ROOT). Nothing is lost; the evidence is filed in a different library from the frames it explains, with no warning.

</details>

<a id="decode-and-debug-filing-dbg-11"></a>

### DBG-11 -- Payload followed by CHECK CONDITION is discarded by the transport, losing the last chunk of a pass or calibration

**Severity** low · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/usb_transport.py:849-857`, `rps7200/direct.py:1779-1797`, `rps7200/direct.py:1862-1864`

The transport discards a payload it has fully read when the status after the data-in phase is CHECK CONDITION. If the device ever attaches a (possibly queued) sense to a successful READ, those bytes never reach last_raw or raw.bin.gz, and read_planes either ends the pass short (0x20) or abandons it. Whether the device does this is unmeasured.

**Evidence (from the code):**

```text
`payload = self._read_payload(read_size, timeout_ms)` / `final = self._wait_not_busy(...)` / `if final == UsbStatus.CHECK: raise CheckCondition(command[0])`. The payload already transferred is dropped. read_lines then `last = self.read_sense()` and, with retries=1, raises EndOfData (0x20) or ScanReadError. read_planes then `break`s, and calibrate_shading treats either as 'finished'.
```

**Failure scenario:** The final READ of a pass returns its 216 lines with the end-of-data sense attached as CHECK. The last batch is discarded, the image is 216/channels rows short, and raw.bin.gz holds less than the device delivered.

**Fix:** Return the payload together with the pending-check flag, e.g. a (payload, check) result or an exception carrying `.payload`. Have read_lines append the bytes and only then read the sense.

<details><summary>Second reader's check</summary>

usb_transport.py:849-857 does read the payload and then raise CheckCondition when the post-transfer status is CHECK, discarding bytes already transferred. In read_planes (retries=1) that becomes EndOfData (silent break) or ScanReadError (suspect). But there is no evidence in the code or captures that the device ever reports CHECK after a completed data-in phase; the scenario ('final READ returns its lines with end-of-data attached') is conjecture. It is a latent loss path, not a demonstrated one.

</details>

<a id="decode-and-debug-filing-dbg-12"></a>

### DBG-12 -- apply_shading silently corrects only part of the frame when the mask maps fewer columns than the pass has

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/shading.py:185-193`, `rps7200/shading.py:236-240`, `rps7200/shading.py:267-281`, `rps7200/direct.py:3115-3144`

scan()'s own comment says 'correcting half a frame is worse than correcting none', but the only guard is on width against the reference width. A mask with fewer used entries than the pass width (e.g. read at the wrong size or partly 0x70) leaves the right-hand columns uncorrected. The pass is still returned and filed as 'applied'. library.corrected has the same behaviour.

**Evidence (from the code):**

```text
`locs = np.flatnonzero(... == MASK_USED); return locs[:width]`; `out[:, : loc.size, c] = vals.astype(image.dtype)`. scan() refuses only `params.width > self._shading.pixels_per_line` and otherwise logs `f"shading corrected: {shading_report['columns']}/{shading_report['width']} columns"`.
```

**Failure scenario:** A cached reference with a different pixels_per_line sizes the mask read, and the mask covers fewer used columns than the pass. The delivered TIFF has a hard boundary where correction stops, and the record says 'applied'.

**Fix:** In apply_shading, raise (or mark the report 'partial') when `loc.size < w`. In scan(), refuse a partial correction the way it refuses a too-narrow reference.

<details><summary>Second reader's check</summary>

build_width_to_loc returns locs[:width] (shading.py:185-193); apply_shading writes only out[:, :loc.size, c] (267-281) and reports columns vs width. scan()'s guards are only params.width > pixels_per_line (3115-3126), so a mask with fewer MASK_USED entries than the pass width leaves the right-hand columns uncorrected while the pass is returned and filed with a shading report (i.e. as corrected). Reachability is narrow: the mask is read sized from the reference's pixels_per_line (3258-3262) and all known calibrations are 5172 columns.

</details>

<a id="decode-and-debug-filing-dbg-13"></a>

### DBG-13 -- Debug capture copies meta before callers add bracket, roll and registration fields

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:930`, `rps7200/direct.py:3222`, `rps7200/direct.py:2821-2824`, `rps7200/direct.py:4134-4136`

Any pass filed by debug rather than by its caller loses the membership and registration fields added after capture. Examples are a bracket from `tools/scan.py --no-library` with debug on, or a roll frame whose claimant did not claim it. library.py's own comment says that without these 'a bracket could not be re-merged'.

**Evidence (from the code):**

```text
`item: dict[str, Any] = {"meta": dict(meta), ...}` at capture inside scan(). After scan() returns, scan_bracket adds `meta["bracket_index"] = i`, `meta["bracket_ratio"] = float(k)`, ... and scan_roll adds `meta["roll_index"] = index`, `meta["registration"] = marks`.
```

**Failure scenario:** `RPS7200_DEBUG=1 tools/scan.py --bracket 5 --no-library`: five debug entries are filed with no bracket_index/ratio, so the bracket cannot be identified or re-merged from the library.

**Fix:** Let scan() accept extra meta (bracket/roll fields) before capture. Alternatively have the spool item hold a reference to the live meta and copy it at flush time, rewriting the sidecar.

<details><summary>Second reader's check</summary>

_debug_capture stores `"meta": dict(meta)` (direct.py:930) inside scan() at 3222. scan_bracket adds bracket_index/ratio/passes/stops to the returned meta afterwards (2821-2824), and scan_roll adds roll_index/roll_position/registration (4131-4133). The spooled copy never sees them. With tools/scan.py --no-library (hold() returns before claiming) or scan_roll without --library, debug is the only filer and its entries lack those fields.

</details>

<a id="decode-and-debug-filing-dbg-14"></a>

### DBG-14 -- set_gain_offset silently wraps gain and offset above 255 while the record keeps the unwrapped value

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:1716-1730`, `rps7200/direct.py:3173-3175`, `tools/gain_probe.py:101`, `tools/gain_probe.py:157-161`

Exposure is protected: to_bytes(2) raises above 65535 and scaled() clamps. Gain and offset are masked instead, so an out-of-range value is sent as something else. The entry then records a value the device never received, and a record that disagrees with the commands is exactly what re-evaluation must not have. The MODE and GAIN payload in `commands` still shows the true bytes.

**Evidence (from the code):**

```text
`data[6 + i] = int(s.offset[i]) & 0xFF`, `data[12 + i] = int(s.gain[i]) & 0xFF`, `data[22] = int(s.gain[3]) & 0xFF`. meta records `"gain": settings.gain, "offset": settings.offset`. gain_probe takes `--ladder` 'comma-separated blue gains' and writes them via `replace(reference, gain=[..., gain, ...])`.
```

**Failure scenario:** `gain_probe.py --ladder 21,128,256` sends gain 0 for the last rung. The library entry says gain 256.

**Fix:** Validate 0..255 and raise, as the exposure path does, rather than masking.

<details><summary>Second reader's check</summary>

set_gain_offset masks offset/gain with & 0xFF (direct.py:1716-1730). In ordinary scan() the settings come from get_gain_offset() (device bytes, <256), so this is only reachable when a caller substitutes settings: gain_probe.py patches get_gain_offset to return replace(reference, gain=[..., gain, ...]) (157-166) with gains taken from --ladder unchecked (106-107). scan() then records meta gain=settings.gain (3173-3175), the unmasked value, while the payload sent (in commands) holds the masked byte.

</details>

<a id="decode-and-debug-filing-dbg-15"></a>

### DBG-15 -- _CommandLog lists every NoDataYet-refused image READ, bloating each entry's commands, contrary to its docstring

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:440-443`, `rps7200/direct.py:484-489`, `rps7200/direct.py:1845-1861`

**Doc claim:** rps7200/direct.py:440-443 -- 'Image-data READs are counted, not listed'

Every poll that finds no data is recorded as a full entry with cdb and timestamp. On a long pass that waits on the carriage this is thousands of entries in meta['commands'], copied into scan.json 'extra', last_scan_meta and the debug sidecar. Command volume is not what the log is for, and the noise buries the real refusals.

**Evidence (from the code):**

```text
Docstring: "Image-data READs are counted, not listed: a 7200 dpi pass makes thousands". Code: `except Exception as exc: entry["refused"] = type(exc).__name__; self.record.append(entry); raise`. read_planes polls every `poll=0.02` s on NoDataYet.
```

**Failure scenario:** A 7200 dpi RGBI pass polls for most of its ~5 minutes, and scan.json grows by hundreds of kB of identical `{"cdb": "08...", "refused": "NoDataYet"}` entries.

**Fix:** Count NoDataYet refusals on image READs in `image_reads` (e.g. `waits`) rather than listing them.

<details><summary>Second reader's check</summary>

_CommandLog.command appends an entry for every exception (`entry["refused"] = type(exc).__name__; self.record.append(entry); raise`, direct.py:484-489). NoDataYet is raised by the inner transport (usb_transport.py:755) and read_planes re-polls every 0.02 s (1847-1861), so each empty poll becomes a listed entry, contrary to the class docstring's 'Image-data READs are counted, not listed' (440-443). The list goes into meta['commands'], scan.json extra, last_scan_meta and the debug sidecar.

</details>

<a id="decode-and-debug-filing-dbg-16"></a>

### DBG-16 -- auto_exposure's comment says SET GAIN OFFSET persists; the rest of the code and CLAUDE.md say the read-back is a fixed reference

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2473-2476`, `rps7200/direct.py:3875-3880`, `rps7200/direct.py:4114-4122`

**Doc claim:** CLAUDE.md:448 -- 'SET GAIN OFFSET does not persist across a scan sequence'; contradicts direct.py:2473-2475

Both comments explain the same get_gain_offset().scaled() arithmetic with opposite premises. The docs/whole-roll-plan.md history shows a 'fix' built on the persistence premise was applied and then reverted. A stale premise here invites the same wrong fix again.

**Evidence (from the code):**

```text
direct.py:2473: "`scan` multiplies whatever the device currently holds, and SET GAIN OFFSET persists, so re-reading it each round would compound the scales." direct.py:4114-4121: "the exposure fields hold 9604/6506/6506/7745 however different the value just written. So the read is a fixed reference ... exposure cannot compound".
```

**Failure scenario:** A maintainer reads auto_exposure and 'fixes' scan() to restore base before scaling, or divides out the probe's scale. On the real device that changes nothing, but it adds a second, divergent notion of the exposure base.

**Fix:** Rewrite the auto_exposure comment to match the measured behaviour: READ GAIN/OFFSET returns a fixed reference, and the write-back of base is defensive.

<details><summary>Second reader's check</summary>

auto_exposure's comment (direct.py:2473-2475) says 'SET GAIN OFFSET persists, so re-reading it each round would compound the scales', while scan_roll's comment (4114-4121) says the read-back is a fixed reference so exposure cannot compound, and CLAUDE.md:448 says SET GAIN OFFSET does not persist. Comment-only contradiction; code behaviour is unaffected.

</details>

<a id="decode-and-debug-filing-dbg-17"></a>

### DBG-17 -- CLAUDE.md says a single scan compresses nothing while the device is open; the spool compresses the shading reference with the device open

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `rps7200/shading.py:91`, `rps7200/direct.py:983-990`, `rps7200/library.py:273`, `rps7200/direct.py:757-758`, `rps7200/direct.py:786-787`, `rps7200/direct.py:745`

**Doc claim:** CLAUDE.md 'A single scan compresses nothing while the device is open. Each is spooled to a temporary file ... gzipped after close()'

The rule that nothing is compressed with the device open is broken in several places, not only the debug spool: ShadingReference.save always uses np.savez_compressed, and it is called with the device open by _debug_capture, by library.save even when compress=False (the window's single scans and prescans), by archive_calibration (whose docstring claims the archive is written uncompressed), and by save_shading from ensure_shading. Each write is small (hundreds of kB).

**Evidence (from the code):**

```text
`# ... saving it compresses -- small, but the device is open.` / `item["reference"].save(ref_path)`, which calls `np.savez_compressed(path, **arrays)`.
```

**Failure scenario:** None beyond the rule being untrue as written; the risk is the one the rule was made for.

**Fix:** Use np.savez (uncompressed) for the spool copy, or correct the documentation.

<details><summary>Second reader's check</summary>

Confirmed that _debug_capture calls item['reference'].save(ref_path), which is np.savez_compressed (shading.py:91), with the device open (direct.py:983-990), against CLAUDE.md:185. But the reader understated it: the same compressed write with the device open also happens in (a) library.save's reference.save(path/'shading.npz') (library.py:273), which the window calls with compress=False precisely because the device is open, so every window single-scan/prescan entry compresses its shading.npz with the device open; (b) archive_calibration's result['reference'].save(folder/'shading.npz') (direct.py:786-787), whose docstring (757-758) says 'Written uncompressed: the device is still open'; (c) save_shading (745), called from ensure_shading with the device open.

</details>

<a id="decode-and-debug-filing-dbg-18"></a>

### DBG-18 -- stop_scan() is a public method that sends STOP SCAN, which CLAUDE.md forbids; it is unused and unguarded

**Severity** low · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/direct.py:1674-1691`

**Doc claim:** CLAUDE.md 'The scanner wedges' -- 'no STOP SCAN'; contradicts direct.py:1675-1678

CLAUDE.md: 'No IEEE1284 RESET, and no STOP SCAN — the vendor sends neither, and both leave the device unresponsive.' finish_scan's docstring says the same. This method's docstring says the opposite and invites a script author to call it on a cleanup path.

**Evidence (from the code):**

```text
`def stop_scan(self) -> None: """Stop scanning. Never raises -- it runs on the cleanup path. Leaving a scan running is what wedges the scanner badly enough to need a power cycle, so this always makes the attempt."""` ... `self.t.command(_cmd(SCSI_SCAN, 0))`. No `_refuse_if_suspect`. grep finds no caller.
```

**Failure scenario:** An ad-hoc probe wraps its scan in try/finally: s.stop_scan() because the docstring recommends it. The next session finds the scanner unresponsive.

**Fix:** Delete it, or make it raise with a pointer to CLAUDE.md.

<details><summary>Second reader's check</summary>

stop_scan (direct.py:1674-1691) sends _cmd(SCSI_SCAN, 0) with no _refuse_if_suspect and a docstring saying leaving a scan running is what wedges it, so 'this always makes the attempt'. It contradicts CLAUDE.md:425 ('no STOP SCAN ... both leave the device unresponsive') and finish_scan's own docstring (1657-1665). grep finds no caller in rps7200/, tools/ or tests/.

</details>

<a id="decode-and-debug-filing-dbg-19"></a>

### DBG-19 -- The demo stand-in has no debug spooling, no command log and no read_planes, so make run-demo never exercises debug filing

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:196`, `rps7200/demo.py:342-344`, `rps7200/demo.py:604-650`, `rps7200/demo.py:846-909`, `rps7200/session.py:2872`

Everything in the debug-filing chain (spool, claim, flush after close, unlink on success) runs only on the hardware. So does everything in read_planes (keep_raw forcing, last_raw clearing, EndOfData handling) and in scan()'s post-read path (stagger, belt-and-braces refusal, _CommandLog). Demo entries carry protocol_revision=None and no commands. DBG-1, DBG-2 and DBG-4 can therefore not be seen offline.

**Evidence (from the code):**

```text
`class DemoScanner:` (not a DirectScanner subclass) with no `debug`, `_debug_capture`, `debug_claim` or `_debug_flush`. close() is `self.t.close(); self._decoded = None`. `_take` meta has no `commands`, `mode`, `protocol_revision`, `filter_offsets` or `shading_origin`. The session does `claim = getattr(self._scanner, "debug_claim", None)`.
```

**Failure scenario:** A change breaks _debug_flush ordering or claim handling. `make run-demo` and `make run-sheet` stay green, and the first sign is a lost pass on the hardware.

**Fix:** Give the stand-in the real debug machinery (share the spool/claim/flush code as a mixin used by both). Feed its encoded blob through read_planes-equivalent code rather than calling decode_index directly.

<details><summary>Second reader's check</summary>

DemoScanner (demo.py:196) is not a DirectScanner subclass and has no debug, _debug_capture, debug_claim or _debug_flush. Its close() (342) only closes the fake transport. session._file uses getattr(self._scanner, 'debug_claim', None) (session.py:2872), so the claim path is skipped in the demo. The debug-filing chain lives below the seam, so this is not an 'if demo' violation, but make run-demo/run-sheet cannot exercise DBG-1/2/4/8.

</details>

<a id="decode-and-debug-filing-dbg-a3"></a>

### DBG-A3 -- read_planes reads image data with retries=1, so a queued one-shot sense on an image READ abandons the pass (suspect) or ends it as 'end of data'

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1766-1797`, `rps7200/direct.py:1845`

The retry that read_lines exists to provide is switched off for every image read. A stray queued sense (unit attention, a condition left by cmd_17 or SET GAIN OFFSET) on any READ of a pass therefore raises ScanReadError at once. That marks the device suspect and loses the pass, or, if its ASC is 0x20, silently truncates the pass (DBG-5). No raw bytes are filed either way (DBG-4). Retrying a refused READ is safe by the driver's own account ('A refusal ... is answered before any transfer and is harmless').

**Evidence (from the code):**

```text
read_lines docstring: 'Retries like _query does: a queued one-shot sense condition is reported against whichever command arrives next, so the first attempt can be rejected for something that has nothing to do with this read.' read_planes: `chunk = self.read_lines(n, bpl, retries=1)`.
```

**Failure scenario:** A one-shot condition queued by an earlier command in the pass is reported on the first image READ. The 20-minute roll frame is abandoned, the session goes DeviceSuspect and the roll ends, although the device was fine.

**Fix:** Allow at least one retry for a refusal whose sense is neither end-of-data nor a hard error, and record the sense in the pass's commands; treat a 0x20 before any line has arrived as an error rather than end-of-data.

<a id="decode-and-debug-filing-dbg-a4"></a>

### DBG-A4 -- A failed pass's command log keeps recording after scan() or calibrate_shading() raise before the logger is stopped

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:2976-2979`, `rps7200/direct.py:3070-3076`, `rps7200/direct.py:2182-2184`, `rps7200/direct.py:2351-2352`

The commands of a pass that failed before its read are never returned or filed. _CommandLog.record stays a live list, so every later command (moves, READ STATE, nudges) accumulates into it until the next scan's start() silently discards them. A failed pass therefore leaves no record of what it sent, which is the evidence needed to diagnose it.

**Evidence (from the code):**

```text
scan(): `logger = getattr(self.t, "start", None); if callable(logger): logger()` well before `try: image, params, ccd_mask = self._read_pass(...) finally: stopper = ...`. Any exception between them (wait_warm, set_mode, SLIDE INIT, wait_ready) skips stop(). calibrate_shading calls stopper only after the try/finally (2351), so any raise skips it.
```

**Failure scenario:** MODE SELECT is refused on a new resolution. scan() raises, the commands that led to the refusal are never surfaced, and the next successful pass's record starts fresh with no trace of them.

**Fix:** Wrap the whole pass from start() in try/finally and stop the logger on every exit. Attach the partial log to the raised exception or to last_failed_commands so a caller can file it.

<a id="decode-and-debug-filing-dbg-20"></a>

### DBG-20 -- defects.py (destripe, column-defect detection, resample_reference) is not used by any delivered path

**Severity** info · **Category** dead-code · **Verdict** confirmed

**Where:** `rps7200/defects.py:1-262`, `rps7200/direct.py:32-39`, `rps7200/direct.py:244-260`, `tools/make_comparison.py:20-25`

This is a maintained-looking correction module that no scan, export, library.corrected or GUI path calls. Its presence in direct's __all__ suggests it is part of the pipeline.

**Evidence (from the code):**

```text
Only direct.py imports and re-exports them (`from .defects import (column_defect_sigma, destripe, ...)` and `__all__`). make_comparison.py: "`destripe`, a flat-file column interpolation nothing delivered has ever run". destripe also truncates instead of rounding: `np.clip(out, 0, ...).astype(image.dtype)`.
```

**Failure scenario:** None today.

**Fix:** Move to research/ or mark it experimental. If it is ever wired in, round rather than truncate on the way back to integers.

<details><summary>Second reader's check</summary>

grep shows defects.py symbols are imported only by direct.py (32-39) and re-exported in __all__ (244-260). tools/make_comparison.py:23 and tools/film_edge_study.py:156 only mention it in text. No scan, export, library.corrected or GUI path calls destripe, find_column_defects or resample_reference.

</details>

<a id="decode-and-debug-filing-dbg-a2"></a>

### DBG-A2 -- Exposure 16-bit timer wrap is guarded; gain/offset are not (observation)

**Severity** info · **Category** design · **Verdict** found-by-verifier

**Where:** `rps7200/protocol.py:601-628`, `rps7200/direct.py:1716-1730`

Every exposure scan() sends goes through scaled(), which clamps to [100, 65535], and to_bytes would raise rather than wrap if anything bypassed it. So the wrap CLAUDE.md warns about cannot happen through scan(). meta records the clamped value in 'exposure' and the requested multiplier in 'exposure_scale'. Gain and offset are masked rather than checked (see DBG-14).

**Evidence (from the code):**

```text
Settings.scaled: `int(max(100, min(65535, round(e * f))))`; set_gain_offset: `int(s.exposure[i]).to_bytes(2, "little")` (raises OverflowError above 65535) but `data[12 + i] = int(s.gain[i]) & 0xFF`.
```

**Failure scenario:** None for exposure.

**Fix:** None for exposure; see DBG-14 for gain/offset.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Debug spool: one pass's raw pixels | <tempfile.gettempdir()>/rps7200-debug-XXXXXXXX/NNN-image.npy | NumPy .npy of the decoded pass (H,W,C) uint16 or uint8, upright, after the 7200 dpi stagger trim | raw (before shading) | DirectScanner._debug_capture (direct.py:940-942), during scan() with the device open | DirectScanner._debug_flush via np.load(mmap_mode='r') -> library.save (direct.py:1063-1074) | yes (lossless npy); unlinked after filing or when claimed (claimed = before the claimant's filing is confirmed, DBG-1) |
| Debug spool: raw USB bytes | <temp>/rps7200-debug-*/NNN-raw.bin | bytes exactly as returned by the READs, concatenated, 2-byte channel tags and any trailing partial line included | raw | _debug_capture (direct.py:959-962) | library.save(raw_path=...) streamed into raw.bin.gz | yes, but only bytes the transport delivered: a payload followed by CHECK is discarded (DBG-11); dropped if the layout guard trips (direct.py:951-958) |
| Debug spool sidecar | <temp>/rps7200-debug-*/NNN-meta.json | JSON {meta, raw_layout, captured}, json.dumps(default=str) | n/a (record) | _debug_capture (direct.py:972-977) | nothing in code (for recovery by hand) | no: non-JSON types become str; lacks the claimed flag and which shading file applies (DBG-8); a shallow copy taken before callers add bracket/roll fields (DBG-13) |
| Debug spool shading reference and CCD mask | <temp>/rps7200-debug-*/NNN-shading.npz (once per reference object), NNN-ccd_mask.bin (per pass) | np.savez_compressed float64 ref/mean/dark/dark_mean + pixels_per_line; mask as bytes (1 byte per calibration column, 0x00 used / 0x70 unused) | reference is a reduction of calibration data; mask raw | _debug_capture (direct.py:983-994) | nothing (the flush uses the in-memory objects) | yes |
| Library entry from debug flush | <RPS7200_DEBUG_ROOT or ./library>/<UTC>_unknown-film_<dpi>dpi[_ir][-N]/{scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, scan.json, INCOMPLETE(transient)} + <root>/index.json | scan.tif lossless TIFF (zlib+predictor via tifffile or uncompressed builtin); raw.bin.gz gzip level 6 with sha256 of the uncompressed bytes in scan.json; scan.json written atomically (temp+fsync+os.replace) | raw pixels + raw bytes; corrected is recomputed by library.corrected | _debug_flush -> library.save (library.py:216-400), after close() | library.load/corrected/decode_raw/reconstruct/migrate_direction/verify; DemoScanner as source | yes for bytes and pixels; root is CWD-relative unless the env var is set (DBG-9) |
| Per-pass record fields relevant to re-decode and re-correct | scan.json: raw.layout, scan.{read_direction, stagger_realigned, carriage_state, frame, width, height, bytes_per_line, depth, channels, filter_offsets, fast_infrared, protocol_revision}, device_settings.{exposure,gain,offset}, metering, calibration.{report,skipped,pixels_per_line}, extra.{commands, mode, shading_origin, started_utc} | JSON | record | DirectScanner.scan meta (direct.py:3155-3215) + read_planes raw_layout (1886-1896) -> library.save | library.decode_raw/reconstruct (layout, stagger), library.corrected (calibration.skipped), signature/duplicates | mostly: params.lines is only in raw.layout (absent when raw not kept); gain/offset recorded unmasked though sent &0xFF (DBG-14); metering levels/scales rounded to 4 dp; commands include thousands of NoDataYet polls (DBG-15) |
| Shading cache | <reference path>, e.g. calibration/shading.npz (GUI default, CWD-relative) | np.savez_compressed float64 ref/mean/dark/dark_mean/pixels_per_line/channels | derived (reduction of calibration bytes) | DirectScanner.save_shading (direct.py:736-747), atomic via .<stem>.part.npz + os.replace; tools/uniformity.py writes it directly | load_shading / ensure_shading(reuse=True) | lossless for the reduction; carries no link to the calibration bytes it came from (DBG-6) |
| Calibration archive | <reference dir>/<UTC stamp>[-N]/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | data.bin = every calibration line exactly as read (16-bit LE, 2-byte tags), uncompressed; calibration.json with width, bytes_per_line, resolution, sha256, commands, protocol_revision | raw | DirectScanner.archive_calibration via ensure_shading (direct.py:749-804, 854-860); not by tools/uniformity.py's direct calibrate_shading | nothing in the code | bytes exact; written non-atomically without an INCOMPLETE marker; outside the library; referenced from entries only by a relative path string in extra.shading_origin.archive, and not at all for a reused cache (DBG-6) |
| Framing prescan inside a frame entry | <library>/<entry>/prescan.tif | TIFF 8-bit RGB | corrected (rf.prescan from prescan() with shading=True); the record does not say so | library.save(prescan=...) from ScanSession._file / tools/scan_roll.py | migrate_direction, demo picture_signature, window | no raw bytes or raw pixels stored for it unless debug is on (DBG-3) |

**Second reader's corrections to this table:**

1) 'Debug spool shading reference': the npz also holds 'channels' and 'dark_channels' arrays. It is written with np.savez_compressed while the device is open (see DBG-17). 'read_by: nothing' is right: the flush uses the in-memory object. 2) 'Library entry from debug flush': the shading.npz inside every library entry is always savez_compressed, even for the window's compress=False entries written with the device open (library.py:273). compress=False otherwise gives raw.bin and uncompressed TIFFs; the debug flush itself always uses compress=True. 3) 'Calibration archive': its shading.npz is savez_compressed (the docstring's 'written uncompressed' is false for that file). data.bin holds only the chunks of successful reads; the refusal that ended the calibration contributes nothing. calibration.json is written with plain write_text (non-atomic). 4) 'Framing prescan inside a frame entry': this applies to real (non-dry-run) rolls only. On a dry run (window walk, scan_roll.py --dry-run with --library) each final prescan is filed as its own entry with raw pixels and, when the layout agrees, raw bytes (session.py:2549-2566, tools/scan_roll.py:666-686). prescan_before and the hold/aim intermediate prescans are never filed in the library (session.py:2585 file_entry=False) and exist only as corrected TIFFs in rolls/<roll>/ or not at all. 5) Missing rows: rolls/<roll>/prescanNN.tif and prescanNN-before.tif (corrected 8-bit TIFF, written by tools/scan_roll.py:660/686 and the session's writer, not re-derivable). The record fields also omit params.lines: the expected line count is persisted only inside raw.layout.lines, so an entry without raw bytes cannot show that it was truncated (DBG-5). 6) 'Per-pass record ... gain/offset recorded unmasked': this happens only when a caller substitutes settings (gain_probe). In ordinary scans gain/offset are the device's read-back bytes.

## What the operator can do

- Turn automatic filing of every pass on with RPS7200_DEBUG=1/true/yes/on or DirectScanner(debug=True). Passes are spooled during the session and filed after close().
- Redirect debug entries with RPS7200_DEBUG_ROOT.
- Take a raw pass deliberately with shading=False (--no-shading). The entry records SHADING_SKIPPED_EXPLICIT and library.corrected returns it as 'deliberately raw'.
- Reuse a cached shading reference (--reuse / load_shading / window 'reuse') to skip the 3-4 minute calibration.
- Choose tied infrared (default) or untied infrared (--no-fast-ir). The read's idle timeout follows the choice (read_idle_s).
- Re-decode every stored pass with current code (tools/library.py reconstruct, library.decode_raw) and re-correct with today's apply_shading (library.corrected).
- Force-abort a stuck job from the window (ScanSession.force_abort), accepting the loss of the frame and a power cycle.

## What the operator should not do

- Force-abort, kill or crash the process with debug on: the spool is never filed and stays in the system temp dir.
- Write an ad-hoc script with debug on without `with DirectScanner(...)` or an explicit close(): nothing it scanned is filed.
- Run a long roll with debug on where the temp dir is small or RAM-backed (tmpfs). Claimed frames are spooled too and held until close.
- Reuse a cached shading reference across a power cycle or long gap. It describes another sensor state, nothing checks, and the entry cannot be tied back to its calibration bytes.
- Rely on debug filing to land beside a custom --library.
- Call DirectScanner.stop_scan(): it sends STOP SCAN.
- Set RPS7200_DEBUG to anything other than 1/true/yes/on and expect probes to file.

## Mistakes nothing guards against

- RPS7200_DEBUG=0, false or 2 passes every probe tool's guard (`os.environ.get(...)` is truthy) while DirectScanner treats it as off, so a full probe run files nothing.
- `--library D:/lib` in the window or tools sends frames there, while debug-filed probes and prescans go to ./library under the current directory.
- After force_abort the session's spooled probes and prescans are never filed, and no tool files an orphaned spool.
- A filing failure (full disk, bad library path) after a pass was claimed loses that pass entirely, because the debug copy was unlinked at close().
- With debug off (the default), a roll keeps no raw bytes of any frame's prescan, and the aim 'before' prescan and metering probes are not kept at all.
- An ambiguous ASC 0x20 mid-read yields a short image filed as a normal pass, with the device not marked suspect.
- A calibration cut short by a non-end-of-data refusal installs a partial (possibly dark-only) reference that corrects every later scan.
- gain_probe --ladder values above 255 are sent masked (&0xFF) while the entry records the unmasked value.

## Dataflow notes

Bytes in: Transport._read_payload (usb_transport.py:717-770) repeats the length handshake and bulk read per 32 KB window until exactly read_size bytes arrive. Nothing at all → NoDataYet. A stall mid-payload is waited out. _command (849-857) returns the payload unless the post-read status is CHECK; in that case the payload is dropped (DBG-11).
Command log: _CommandLog.command (direct.py:474-497) wraps every command while a pass is recorded (scan() start at 2990-2992, stop in the finally at 3075-3076). SCSI_READ replies of 256 bytes or more are counted in image_reads. Everything else is logged as hex cdb/out/in, including each NoDataYet poll.
Read: read_lines (1757-1797) turns a CHECK with sense 0x20 into EndOfData and any other sense into ScanReadError. read_planes (1799-1913) asks for batch_for(bpl) lines at a time and appends every chunk. On EndOfData it breaks early (1862-1864). It joins the chunks into `blob`, sets _read_complete, and when keep_raw (forced by debug at 3277-3279) stores last_raw=blob plus last_raw_layout {format, bytes_per_line, line_stride=bpl+2, index_header, width, lines, channels, byte_order, lines_received}. Otherwise it clears them.
Decode: decode_index (1922-1971) steps the blob at bpl+2 bytes and keys each line on line[0] (line[1] is ignored). dtype is '<u2' if bytes_per_line//width == 2, else u8. Lines with a tag outside RGBI are dropped. It raises if the plane count differs from channels, truncates every plane to the shortest, and takes the direction from read_direction (direction.py:85-114): the first R against the first B, cross-checked against the last R/B when the read is complete. It reverses rows only for REVERSED, never for UNKNOWN. Columns are never reversed.
_read_pass (3237-3294): START SCAN, wait_ready, then the per-pass CCD mask via SCSI COPY, sized to the reference's pixels_per_line or 5172 (3259-3268), then GET PARAMETERS and read_planes. An exception before _read_complete marks the device suspect.
scan() (2842-3235): refuses shading problems before sending anything (2935-2963). Optional auto_exposure probes are full scan() calls, keep_raw=True. Exposure goes get_gain_offset() (a fixed reference) → Settings.scaled, clamped to 100..65535 (protocol.py:601-628) → set_gain_offset (to_bytes(2) raises above 65535; gain and offset are masked with &0xFF). Then MODE SELECT (byte14_for, fast_infrared gated on infrared), SLIDE INIT, carriage_record, _read_pass. At 7200 dpi _realign_native_column_stagger trims 4 rows (3090-3097) and the result becomes raw_pixels. apply_shading (shading.py:196-283) scales the reference to the pass depth and applies the two-point or single-point gain per mask column. It rounds with floor(x+0.5), clips and counts clipping, and leaves channel 3 (I) uncorrected. meta is built (3155-3215). _debug_capture(raw_pixels, meta) spools .npy, raw.bin, meta.json and mask (reference once) to tempfile.mkdtemp (908-998). last_pixels_raw and last_scan_meta are then set and the corrected image returned.
Filing: callers read capture_record() (889-904: reference, ccd_mask, last_raw, last_raw_layout) and last_pixels_raw, call debug_claim(raw_pixels), and file through library.save, via FrameWriter or after close(). close() (1134-1145) closes the transport and then runs _debug_flush (1031-1125). For every unclaimed item it calls library.save(image mmap, meta, raw_path, raw_layout, reference, ccd_mask, inquiry) into RPS7200_DEBUG_ROOT or ./library, unlinks the item on success, and keeps the spool if anything failed. Claimed items are unlinked unconditionally.
Calibration: calibrate_shading (2128-2381) sends set_mode(calibrate=True, ONE_PASS_COLOR, 8-bit) and SLIDE INIT 0x01, reads the width from the shading descriptor, waits 10 s in silence, then reads 4-line blocks of 2*width+2 bytes until EndOfData or ScanReadError. It reads the CCD mask and runs calculate_shading (shading.py:109-182), which splits dark and light lines by level ratio (≥5) and averages them to float64. ensure_shading (806-887) archives data.bin, mask, reference and calibration.json under the cache dir (749-804) and saves the cache atomically (736-747).
Library replay: library.decode_raw/reconstruct (library.py:657-774) read raw.bin.gz, rebuild ScanParameters from raw.layout, call DirectScanner.decode_index and replay the stagger trim from scan.stagger_realigned. They compare shape, recorded direction and pixels. library.corrected (548-601) applies today's apply_shading with the entry's shading.npz and ccd_mask.bin.
Demo: DemoScanner._read_as_carriage (demo.py:604-650) encodes stored pixels with encode_index (reversed when its modelled carriage is at the far end) and runs DirectScanner.decode_index on the result. It never goes through read_planes, _CommandLog, the stagger trim or debug filing.
