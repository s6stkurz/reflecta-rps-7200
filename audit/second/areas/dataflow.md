# Dataflow tracer

29 findings: 2 high, 12 medium, 14 low, 1 info.

**Not verified.** In the first audit every finding was re-read by an adversarial second reader; here that stage did not run (the account's spend limit was reached), so these are one reader's findings. Treat them as leads to confirm against the code.

[Back to the summary](../README.md)

## Findings at a glance

| ID | Severity | Category | Title |
|---|---|---|---|
| [F01](#dataflow-f01) | high | data-integrity | A failed library.save loses the whole picture: delivered copies, raw capture and later bracket passes |
| [F02](#dataflow-f02) | high | hardware-safety | tools/scan.py ignores the first Ctrl-C outside a bracket; the second abandons the read |
| [F03](#dataflow-f03) | medium | data-integrity | Roll frame entries store the corrected prescan as prescan.tif, unlabelled, without its raw pixels, bytes or CCD mask |
| [F04](#dataflow-f04) | medium | data-integrity | Rolls and brackets drop the metering evidence and are recorded as exposure_metered False |
| [F05](#dataflow-f05) | medium | data-integrity | Calibration bytes live outside the library and are unlinked when the reference is loaded from the cache |
| [F06](#dataflow-f06) | medium | hardware-safety | A suspect device is sent configuration writes before scan() refuses |
| [F07](#dataflow-f07) | medium | data-integrity | tools/scan_roll.py keeps scanning after the library stops accepting frames |
| [F08](#dataflow-f08) | medium | data-integrity | The debug spool keeps every pass, claimed or not, on the temp disk until close() |
| [F09](#dataflow-f09) | medium | bug | Library atomic renames have no retry for a file Windows holds briefly |
| [F10](#dataflow-f10) | medium | data-integrity | migrate-raw rewrites labelled entries without checking that the decode reproduces them |
| [F11](#dataflow-f11) | medium | user-error | The CLI tools calibrate without asking whether the film is loaded |
| [F12](#dataflow-f12) | medium | bug | Force abort skips close(), so debug-spooled passes are never filed and their location is never reported |
| [F13](#dataflow-f13) | medium | user-error | Rolls have no up-front refusal of an uncorrectable resolution (window, demo and driver) |
| [F14](#dataflow-f14) | medium | doc-mismatch | Walk evidence exists only in the roll folder, which the Delete dialog calls re-derivable |
| [F15](#dataflow-f15) | low | data-integrity | apply_shading leaves columns beyond the mask's used count uncorrected, and only logs it |
| [F16](#dataflow-f16) | low | data-integrity | The command log lists every NoDataYet poll; index.json is rebuilt from all entries on every save and read by nothing |
| [F17](#dataflow-f17) | low | data-integrity | A payload followed by CHECK CONDITION is thrown away |
| [F18](#dataflow-f18) | low | data-integrity | A tools/scan.py bracket holds every pass three times in RAM until close |
| [F19](#dataflow-f19) | low | dead-code | INFRARED_FLOOR_S guards nothing |
| [F20](#dataflow-f20) | low | demo-divergence | The demo retypes transport constants and diverges in small behaviours |
| [F21](#dataflow-f21) | low | demo-divergence | The demo files uncorrected passes where the driver refuses, and verify has an if-demo branch |
| [F22](#dataflow-f22) | low | dead-code | Analysis tools decode without the stagger replay; the uniformity study calibrates without archiving |
| [F23](#dataflow-f23) | low | user-error | Silent overwrites in the CLI tools |
| [F24](#dataflow-f24) | low | user-error | In the window's Delete, the destructive answer is 'No' |
| [F25](#dataflow-f25) | low | doc-mismatch | Stale transport and unit statements in code and CLAUDE.md |
| [F26](#dataflow-f26) | low | design | Entries left uncompressed by a killed window have no CLI command to compact them |
| [F27](#dataflow-f27) | low | error-handling | Save As, Save all and Export write TIFFs without a resolution tag |
| [F28](#dataflow-f28) | low | data-integrity | Debug entries ignore the caller's library root and film notes |
| [F29](#dataflow-f29) | info | design | session_start's opening SLIDE is the sub-frame forward action and runs only in probes |

## Findings in full

<a id="dataflow-f01"></a>

### F01 -- A failed library.save loses the whole picture: delivered copies, raw capture and later bracket passes

**Severity** high · **Category** data-integrity

**Where:** `rps7200/session.py:1651-1694`, `rps7200/session.py:1587-1600`, `tools/scan.py:367-376`, `tools/scan.py:413-416`, `rps7200/library.py:396-399`

The library entry was moved in front of the delivered copies so that a failing output folder cannot cost the raw bytes. Now the library is the single point of failure: if it fails, the frame's raw bytes and pixels are dropped with the job and no copy is written anywhere, even when the output folder is on a healthy disk. tools/scan.py loses every pass after the first failure and the --out file. A reindex error after a complete save is reported as a failed filing, so the manifest says not done while the entry exists.

**Evidence (from the code):**

```text
FrameWriter._write: `entry = library.save(` (1656) runs before `for path in job.get("paths") or ():` (1675); an exception leaves _write and _run only does `self.errors.append(f"picture {job['number']}: {exc}")`. tools/scan.py:367 `for held in pending: entries.append(library.save(...))` is not guarded and `export.write(out, ...)` (413) comes after it. library.save ends `(path / INCOMPLETE).unlink(missing_ok=True)` then `reindex(root)`, so a reindex error raises after the entry is complete.
```

**Failure scenario:** The library disk is nearly full and out_dir is on another drive. On frame 12 of a window roll, library.save raises ENOSPC while writing scan.tif. The raw bytes and corrected image of frame 12 are discarded, frame12.tif and the out_dir copy are never attempted, and the window stops after the frame in flight, so frame 13 is lost as well. With tools/scan.py --bracket 9, pass 2's save raises: passes 2-9 and the merged --out are lost, ending in a traceback.

**Fix:** Catch the library failure for each job, still write the delivered copies, and spool the capture (raw bytes, raw pixels, reference, mask, meta) to a recovery folder. Guard each save in tools/scan.py and still write --out. Log a reindex failure instead of raising once the entry is complete.

<a id="dataflow-f02"></a>

### F02 -- tools/scan.py ignores the first Ctrl-C outside a bracket; the second abandons the read

**Severity** high · **Category** hardware-safety

**Where:** `tools/scan.py:265-360`, `rps7200/console.py:74-81`

DeferredInterrupt promises that the run stops after the pass in flight, but a single-pass run never asks. After a Ctrl-C during calibration or metering, the tool carries on through the metering probes and the full pass. That invites the second Ctrl-C, which raises KeyboardInterrupt inside the read.

**Evidence (from the code):**

```text
console.py _handler on the first Ctrl-C: `self._requested = True` and prints 'stopping after the pass in flight -- interrupting a read wedges the scanner. Press Ctrl-C again to abort anyway.' tools/scan.py checks the flag only at 301 `if args.bracket and interrupt.requested():`. The single-pass path goes ensure_shading (273) -> s.scan(... auto_exposure ...) (350) without checking it.
```

**Failure scenario:** The operator runs `tools/scan.py --dpi 3600 --ir --auto-exposure` and presses Ctrl-C during the 3-4 minute calibration. The tool says it will stop, then runs 2-3 probes and a ~200 s pass. The operator presses Ctrl-C again as the message told him to: read_planes is abandoned mid-scan, the device is marked suspect and wedged, nothing is filed and a power cycle is needed.

**Fix:** Check interrupt.requested() after ensure_shading and before scan()/scan_bracket, and pass a should_stop into metering, then exit cleanly with status 130.

<a id="dataflow-f03"></a>

### F03 -- Roll frame entries store the corrected prescan as prescan.tif, unlabelled, without its raw pixels, bytes or CCD mask

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/session.py:2616-2628`, `tools/scan_roll.py:743-755`, `rps7200/direct.py:3968-3976`, `rps7200/library.py:268-271`, `rps7200/library.py:376-384`, `rps7200/demo.py:1417-1430`

**Doc claim:** CLAUDE.md:116 'The library holds raw pixels; everything else is corrected.'

The prescan inside every roll-frame entry is the flat-fielded 8-bit picture. Its raw pixels are on the RollFrame and are then dropped; its own 300 dpi CCD mask and its raw bytes were never kept (without debug mode). Nothing in the record says whether it is corrected: a shading=False roll stores it raw with the same record.

**Evidence (from the code):**

```text
scan_roll keeps both `prescan_image, _ = self.prescan(...)` (corrected) and `raw_prescan = self.last_pixels_raw`. session._file passes `raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta` and never rf.raw_prescan; tools/scan_roll.py:752 passes `prescan=frame.prescan`. library.save writes `tiff.write(str(path / "prescan.tif"), prescan, ...)` and records only file, read_direction and carriage_state. demo._stored admits: 'It is the picture as the operator was shown it, **corrected** when it was taken', and _taken_raw has to infer rawness from calibration.skipped.
```

**Failure scenario:** A later fix to apply_shading, such as the uncorrected edge columns in F15, cannot reach any roll prescan. The picture was corrected by that day's code and the inputs to redo it are gone. migrate_direction and the demo correlate or serve it on the assumption that it is corrected, which is wrong for shading=False rolls.

**Fix:** File rf.raw_prescan with its own mask and bytes, either as a linked entry or as prescan_raw.tif plus prescan_ccd_mask.bin taken while it was the last pass. Record the prescan's shading report or corrections_applied in record['prescan'].

<a id="dataflow-f04"></a>

### F04 -- Rolls and brackets drop the metering evidence and are recorded as exposure_metered False

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:4099-4131`, `rps7200/direct.py:2790-2820`, `rps7200/direct.py:3175-3182`, `rps7200/direct.py:3217-3220`

**Doc claim:** rps7200/direct.py:361-364 ':attr:`DirectScanner.last_metering` records blue's achieved level on every metered scan, so these become checkable from ordinary work.'; rps7200/library.py:340-345

The two production multi-pass paths meter outside scan(), so every frame they file carries metering: null and claims a commanded exposure. The probe levels, targets and limited flags exist only as debug-spooled probes.

**Evidence (from the code):**

```text
scan_roll: `scales = self.auto_exposure(...)` then `image, meta = self.scan(..., exposure_scale=scales, ...)` without auto_exposure. scan() records `"exposure_metered": bool(auto_exposure)` and sets metering only `if auto_exposure and self.last_metering is not None`. scan_bracket does the same (2794, 2811).
```

**Failure scenario:** Every frame of a METER_EACH roll (the default in the window and the CLI) has no record of why its exposure is what it is. BLUE_RGBI_HEADROOM cannot be checked from rolls, and a frame whose blue clipped cannot be told apart from one metered against an unusual frame.

**Fix:** After scan() in scan_roll and scan_bracket, attach a copy of last_metering and mark the exposure as metered (or add a 'metered_by' field). For METER_ONCE, record which frame's metering applies.

<a id="dataflow-f05"></a>

### F05 -- Calibration bytes live outside the library and are unlinked when the reference is loaded from the cache

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:713-733`, `rps7200/direct.py:749-805`, `rps7200/direct.py:852-860`, `rps7200/direct.py:3207-3211`

**Doc claim:** rps7200/library.py:27-29 'Entries are self-contained directories: nothing refers out'; rps7200/direct.py:852-855

Each entry keeps the reduced shading.npz. The bytes it was reduced from sit in a CWD-relative, gitignored folder that verify never checks. Sessions using 'Use the cached one' or --reuse do not name their archive at all.

**Evidence (from the code):**

```text
`archive = self.archive_calibration(result, path.parent)` writes calibration/<UTC>/data.bin. The only link is `self._shading_origin["archive"] = str(archive)`, set on a fresh calibration. load_shading sets `{"action": "loaded", "path": ..., "file_modified_utc": ..., "loaded_utc": ...}` with no archive. Nothing in rps7200/ or tools/ reads data.bin or calibration.json (only tests do).
```

**Failure scenario:** calculate_shading is improved, for example the dark/light split. Entries from reuse sessions can only be re-corrected with the old reduction, and their data.bin has to be guessed from the cache's mtime. Moving the checkout or cleaning calibration/ loses every calibration's bytes without a warning.

**Fix:** Copy data.bin (~1.7 MB) into each entry, or store the archive stamp and sha in the cache npz and carry it into shading_origin on load. Teach verify/reconstruct to rebuild shading.npz from it.

<a id="dataflow-f06"></a>

### F06 -- A suspect device is sent configuration writes before scan() refuses

**Severity** medium · **Category** hardware-safety

**Where:** `rps7200/direct.py:2976-3062`, `rps7200/direct.py:2468-2480`, `rps7200/direct.py:1500-1527`, `rps7200/direct.py:522-527`

**Doc claim:** rps7200/direct.py:522-524 'Set, it makes every command that would drive the device raise `DeviceSuspect`'; CLAUDE.md:417-420

Once a pass has been abandoned, the documented contract is that nothing but status queries reaches the device. In practice about ten WRITE/MODE SELECT/vendor commands go out before DeviceSuspect is raised.

**Evidence (from the code):**

```text
scan() has no _refuse_if_suspect at its start. It sends READ STATE, TUR, `self.set_exposure_time()` (3016), `self.set_highlight_shadow()` (3017), `self.set_scan_frame(*frame)` (3019), `self.cmd_17(1)` (3024), `get_gain_offset`/`set_gain_offset` (3028-3029) and `self.set_mode(...)` (3052). Only `self.slide(SLIDE_INIT, ...)` (3062) calls `_refuse_if_suspect`. auto_exposure also sends `self.set_gain_offset(base)` each round before its probe.
```

**Failure scenario:** A 3600 dpi pass times out mid-read in the window, so suspect is set. The operator presses Scan again (metering on): gain/offset writes, exposure/highlight writes, SET FRAME, cmd 0x17 and MODE SELECT all go to a device that may still be streaming the abandoned pass, before the refusal at SLIDE INIT.

**Fix:** Call _refuse_if_suspect at the top of scan(), auto_exposure(), prescan() and scan_roll(), before any non-status command.

<a id="dataflow-f07"></a>

### F07 -- tools/scan_roll.py keeps scanning after the library stops accepting frames

**Severity** medium · **Category** data-integrity

**Where:** `tools/scan_roll.py:459`, `tools/scan_roll.py:743-781`, `rps7200/session.py:1998-2015`

The CLI and the window diverge. The window ends a roll on the first filing failure; the tool scans the rest of the strip, and every one of those frames fails the same way and loses its frameNN.tif too (F01).

**Evidence (from the code):**

```text
The tool builds `writer = FrameWriter()` without on_done and never checks writer.errors inside the loop; failures only reach `record_of.filed(n, entry, error, ...)`. The window's _filed does `self.request_stop()` with 'stopping the roll after the frame in flight: picture N could not be filed'.
```

**Failure scenario:** A 38-frame CLI roll at 1800 dpi RGBI: the library disk fills at frame 10, and frames 10-38 are scanned, metered and lost, about an hour of scanner time. The exit status reports it only at the end.

**Fix:** Give the tool's FrameWriter an on_done that requests a stop (the should_stop the roll already polls) when a filing error arrives.

<a id="dataflow-f08"></a>

### F08 -- The debug spool keeps every pass, claimed or not, on the temp disk until close()

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:908-998`, `rps7200/direct.py:1000-1017`, `rps7200/direct.py:1031-1085`

**Doc claim:** rps7200/direct.py:1076-1085 'At 7200 dpi a frame spools 1.1 GB, so holding all 38 of a roll through the flush would want 43 GB of disk ... the peak is what runs a machine out of space'

Claiming stops a pass being filed twice, but its spool stays on disk for the whole session. The comment claims the peak disk use has been bounded; during a roll, before the flush, it has not.

**Evidence (from the code):**

```text
_debug_capture np.save's the raw pixels and writes raw.bin for every pass. debug_claim only sets `item["claimed"] = True`. Unlinking happens only in _debug_flush, which runs from close().
```

**Failure scenario:** RPS7200_DEBUG=1, which CLAUDE.md requires for Claude, on a 38-frame 3600 dpi RGBI roll: about 38 x 0.4-0.6 GB is spooled to $TMP while FrameWriter writes the same frames to library/. On a single-disk machine library.save then fails and frames are lost (F01).

**Fix:** In debug_claim, unlink the claimed item's spool files at once and keep only unclaimed passes (probes, hold and aim prescans).

<a id="dataflow-f09"></a>

### F09 -- Library atomic renames have no retry for a file Windows holds briefly

**Severity** medium · **Category** bug

**Where:** `rps7200/library.py:484-491`, `rps7200/library.py:503-511`, `rps7200/library.py:414-457`, `rps7200/direct.py:736-747`, `rps7200/session.py:857-884`

scan.json, the compacted TIFFs and the calibration cache are all renamed without the retry the manifests have for the same reason.

**Evidence (from the code):**

```text
_write_atomic ends with one `os.replace(temp, path)`. session.py:861-866 explains that on Windows the rename fails while any handle lacks FILE_SHARE_DELETE, and that 'Defender and the indexer briefly hold every new file as a matter of course'. The retries (REPLACE_RETRY_S) exist only in write_manifest.
```

**Failure scenario:** On Windows, Defender opens the freshly written .scan.json.part of frame 7 and os.replace raises PermissionError. Every data file is on disk, but the entry stays INCOMPLETE and invisible to entries(). The frame is reported unfiled, the window stops the roll, and frame07.tif is not written (F01).

**Fix:** Route library._write_atomic, _replace_tiff, compact and save_shading through one shared retrying replace (session._replace).

<a id="dataflow-f10"></a>

### F10 -- migrate-raw rewrites labelled entries without checking that the decode reproduces them

**Severity** medium · **Category** data-integrity

**Where:** `tools/library.py:170-190`, `tools/library.py:196-215`

The guard against laundering a decode regression covers only mislabelled entries. Labelled legacy entries are rewritten from today's decode whatever reconstruct said.

**Evidence (from the code):**

```text
`if np.array_equal(plain, stored) and not applied: continue`; `if not applied and not _one_shading_explains(...)`: failed; everything else goes to `planned.append(...)`. For corrections_applied=['shading'], the verdict returned by `library.reconstruct(path)` is discarded.
```

**Failure scenario:** After a decode regression, `migrate-raw --write` replaces a labelled entry's scan.tif with the wrong decode and clears corrections_applied. reconstruct then reports 'identical' from then on; the evidence survives only in scan.before-migrate-raw.tif.

**Fix:** For labelled entries, plan the rewrite only when reconstruct's verdict starts with 'identical' (reconstruct already re-applies the entry's shading).

<a id="dataflow-f11"></a>

### F11 -- The CLI tools calibrate without asking whether the film is loaded

**Severity** medium · **Category** user-error

**Where:** `tools/scan.py:269-274`, `tools/scan_roll.py:188-196`, `tools/scan_roll.py:568`, `tools/gui.py:2030-2140`

**Doc claim:** CLAUDE.md:321-329 'Only Stefan can see the transport. Ask him.'

The empty-transport calibration hazard is guarded only in the window. The roll tool's seek checks the counter, not whether film is present.

**Evidence (from the code):**

```text
tools/scan.py prints 'calibrating (about 3-4 minutes...)' and calls s.ensure_shading unconditionally; scan_roll.py's calibrate() does the same after the seek. The window's prompt refuses with 'Nothing started. Tick that the film is in the transport first -- only you can see it.'
```

**Failure scenario:** `uv run python tools/scan.py --dpi 1800` with the strip out runs a full calibration over an empty transport, the state recorded as having preceded a wedge.

**Fix:** Prompt before a measuring calibration in both tools, or require a --film-loaded flag, as the window does.

<a id="dataflow-f12"></a>

### F12 -- Force abort skips close(), so debug-spooled passes are never filed and their location is never reported

**Severity** medium · **Category** bug

**Where:** `rps7200/session.py:1966-1976`, `rps7200/session.py:1855-1875`, `rps7200/direct.py:1134-1145`

After a force abort, the session's metering probes, hold and aim prescans and unclaimed passes stay in $TMP/rps7200-debug-XXXX. No log line names the directory.

**Evidence (from the code):**

```text
The _run finally does `if not self.dead: self._scanner.close()`, and DirectScanner.close() is the only caller of `_debug_flush()`. force_abort sets `self.dead = True` and closes only the transport.
```

**Failure scenario:** With debug on, the operator force-aborts a stalled pass. The evidence of what led up to the stall is exactly what is wanted afterwards, and it is left unfiled in a temp directory the OS may clean.

**Fix:** When dead, still call scanner._debug_flush() (the transport is already closed), or at least log the spool path.

<a id="dataflow-f13"></a>

### F13 -- Rolls have no up-front refusal of an uncorrectable resolution (window, demo and driver)

**Severity** medium · **Category** user-error

**Where:** `tools/gui.py:1786-1787`, `tools/gui.py:2181-2345`, `tools/gui.py:3107-3118`, `rps7200/direct.py:3830-3850`, `rps7200/direct.py:4099-4131`

Each frame is prescanned and metered before scan() raises uncorrectable. The roll catches that as a frame failure and advances.

**Evidence (from the code):**

```text
The window accepts `self._int(self.v_dpi, "Scan dpi", 25, 7200)` and DPI_LADDER contains 7200. on_roll and on_scan_chosen never call correctable_at. scan_roll checks meter, film and IR up front, not the resolution. tools/scan_roll.py:332-338 does refuse it.
```

**Failure scenario:** Scan roll at 7200 dpi: the film is sought, and frames 1-3 are each prescanned, metered (~60 s) and refused. The roll gives up with the film two frames past the start and nothing scanned.

**Fix:** In scan_roll, `if shading and not self.correctable_at(resolution, window): raise self.uncorrectable(...)` before the first move; this covers the window, demo and CLI. Also validate in on_roll and on_scan_chosen.

<a id="dataflow-f14"></a>

### F14 -- Walk evidence exists only in the roll folder, which the Delete dialog calls re-derivable

**Severity** medium · **Category** doc-mismatch

**Where:** `tools/gui.py:2810-2843`, `tools/gui.py:5411-5415`, `rps7200/session.py:2545-2590`, `tools/scan_roll.py:664-694`

**Doc claim:** tools/gui.py:2811-2815 docstring and 2823-2830 dialog

Deleting a walked roll folder destroys aim decisions, confidences (the data CONFIDENCE_FLOOR is fitted from) and pre-aim pictures that exist nowhere else.

**Evidence (from the code):**

```text
Walk prescans are filed with `dict(rf.prescan_meta or {...}, roll_membership=...)`, which has no registration. The marks (contrast, offset, correction/ensemble detail, hold history) go only into survey.json. prescanNN-before.tif is written with `file_entry=False`. The dialog says 'The library entries are NOT touched: the raw bytes stay, and the frames can be rebuilt from them'; the docstring says 'Everything in a roll folder is re-derivable from the library **except `approved.json`**'.
```

**Failure scenario:** The operator deletes an uncommissioned walk folder, trusting the dialog. The per-frame registration and aim records and the pre-aim prescans are gone.

**Fix:** Put the registration marks into the walk prescans' meta, and file prescan_before as an entry with its capture taken at the time. Until then, name survey.json in the dialog.

<a id="dataflow-f15"></a>

### F15 -- apply_shading leaves columns beyond the mask's used count uncorrected, and only logs it

**Severity** low · **Category** data-integrity

**Where:** `rps7200/shading.py:236-241`, `rps7200/shading.py:286-297`, `rps7200/direct.py:3127-3144`, `tools/uniformity.py:141-146`

Corrected deliverables can carry unflat-fielded edge columns, and nothing in the file or the entry says so.

**Evidence (from the code):**

```text
`loc = build_width_to_loc(bytes(ccd_mask), w)` returns `locs[:width]`, and the loop writes only `out[:, : loc.size, c]`. tools/uniformity.py notes: 'At 600 dpi the CCD mask marks 860 used pixels against a width of 862, so apply_shading leaves the last columns raw'. scan() refuses only `params.width > self._shading.pixels_per_line`.
```

**Failure scenario:** Every corrected 600 dpi Save As, Export and frameNN.tif has its last two columns uncorrected.

**Fix:** Map trailing columns to the nearest used reference column, or crop them and record it; flag columns < width in the record.

<a id="dataflow-f16"></a>

### F16 -- The command log lists every NoDataYet poll; index.json is rebuilt from all entries on every save and read by nothing

**Severity** low · **Category** data-integrity

**Where:** `rps7200/direct.py:474-498`, `rps7200/direct.py:1840-1858`, `rps7200/library.py:399`, `rps7200/library.py:1045-1065`

**Doc claim:** rps7200/direct.py:441-443 'Image-data READs are counted, not listed'

Image-data READs are said to be counted, not listed, but refused ones are listed. That bloats each scan.json, and every save re-parses the whole library.

**Evidence (from the code):**

```text
_CommandLog.command: `except Exception as exc: entry["refused"] = type(exc).__name__; self.record.append(entry)`. read_planes re-issues READ every `poll=0.02` s on NoDataYet. save() ends with `reindex(root)`, which json-loads every entry, and nothing reads index.json.
```

**Failure scenario:** An 85 s pass that is mostly polling carries thousands of 'NoDataYet' entries. With a few hundred entries, each writer-thread save and each debug-flush save parses hundreds of MB of JSON.

**Fix:** Count refused image READs as bulk reads are counted. Make reindex incremental or drop it from save().

<a id="dataflow-f17"></a>

### F17 -- A payload followed by CHECK CONDITION is thrown away

**Severity** low · **Category** data-integrity

**Where:** `rps7200/usb_transport.py:840-853`, `rps7200/direct.py:1778-1797`, `rps7200/direct.py:1843-1858`, `rps7200/direct.py:2303-2322`

If the device ever signals end-of-data as a CHECK on the last data-in (not measured either way), bytes already transferred never reach last_raw or the decode.

**Evidence (from the code):**

```text
`payload = self._read_payload(read_size, timeout_ms)` is followed by `if final == UsbStatus.CHECK: raise CheckCondition(command[0])`. read_lines (retries=1) turns that into EndOfData/ScanReadError, and read_planes does `except EndOfData: ... break` without the payload.
```

**Failure scenario:** The final chunk of lines is received and dropped. raw.bin.gz and scan.tif come out short, and lines_received records it as an early end rather than a loss.

**Fix:** Return the payload together with the condition, or keep the bytes, and log it.

<a id="dataflow-f18"></a>

### F18 -- A tools/scan.py bracket holds every pass three times in RAM until close

**Severity** low · **Category** data-integrity

**Where:** `tools/scan.py:280-345`, `rps7200/direct.py:2811-2836`

**Doc claim:** CLAUDE.md:185-188 'Each is spooled to a temporary file as it is taken'

The passes are neither spooled nor written until the device closes.

**Evidence (from the code):**

```text
pending holds the raw pixels and capture['raw'] bytes of each pass, and scan_bracket is called without retain=False, so `frames.append(image)` also keeps every corrected pass.
```

**Failure scenario:** `--bracket 9 --dpi 3600` holds about 9 x (107+107+107) MB, roughly 2.9 GB. An OOM kill mid-read on a smaller machine abandons the read (wedge) and loses every pass.

**Fix:** Spool each pass to disk in on_pass, as debug filing does, and pass retain=False, merging from the spool.

<a id="dataflow-f19"></a>

### F19 -- INFRARED_FLOOR_S guards nothing

**Severity** low · **Category** dead-code

**Where:** `rps7200/direct.py:508-515`, `rps7200/session.py:57-65`

**Doc claim:** rps7200/direct.py:510-514; rps7200/session.py:57-64; CLAUDE.md:414-416

The comments say it sits 'beside the read it guards'; no read uses it.

**Evidence (from the code):**

```text
The only references are the definition and the re-export `INFRARED_FLOOR_S = DirectScanner.INFRARED_FLOOR_S`. read_idle_s uses READ_IDLE_S and UNTIED_INFRARED_IDLE_S.
```

**Failure scenario:** A maintainer raises INFRARED_FLOOR_S expecting a longer read timeout, and nothing changes.

**Fix:** Remove it or use it in read_idle_s, and fix the comments.

<a id="dataflow-f20"></a>

### F20 -- The demo retypes transport constants and diverges in small behaviours

**Severity** low · **Category** demo-divergence

**Where:** `rps7200/demo.py:445-455`, `rps7200/demo.py:410-417`, `rps7200/demo.py:474-486`, `rps7200/demo.py:497-505`, `rps7200/demo.py:894-915`

These are the retyped-constant patterns CLAUDE.md forbids ('never retyped').

**Evidence (from the code):**

```text
`swallowed = min(abs(asked), 2.2 * 0.1057)` retypes MM_PER_UNIT and the backlash count. `self._work(7.0)` per advance where session.FORWARD_FRAME_S is 4.6. `self._work(210.0 ...)` for calibration. _shift uses `APERTURE_MM / width`, which framing.units_per_column calls wrong. ensure_shading answers 'loaded' for reuse=True even when there is no cache; the real one then measures. Demo metas carry no protocol_revision, commands, mode, filter_offsets or shading_origin.
```

**Failure scenario:** After MM_PER_UNIT or the backlash is re-measured, the demo's hold loop converges differently from the hardware, the same drift that happened with nudge.

**Fix:** Take MM_PER_UNIT, BACKLASH_COMMANDS, FORWARD_FRAME_S and units_per_column from where they are defined, and mirror the reuse-without-cache branch.

<a id="dataflow-f21"></a>

### F21 -- The demo files uncorrected passes where the driver refuses, and verify has an if-demo branch

**Severity** low · **Category** demo-divergence

**Where:** `rps7200/demo.py:880-893`, `rps7200/library.py:1112-1125`

A shading=True demo pass drawn from an entry without a reference is shown and filed raw, a path the scanner never takes, and verify hides it.

**Evidence (from the code):**

```text
_take: `elif reference is not None: image, report = apply_shading(...)` else `skipped = UNCALIBRATED_SOURCE`; DirectScanner.scan raises ShadingUnavailable in that case. library.verify: `demo = bool((record.get("extra") or {}).get("demo"))` suppresses the missing-reference problem.
```

**Failure scenario:** --demo on a library with reference-less entries exercises a 'raw despite shading' filing that the real window can never produce.

**Fix:** Skip reference-less sources for corrected demo passes, or raise DirectScanner.uncalibrated() as the driver does.

<a id="dataflow-f22"></a>

### F22 -- Analysis tools decode without the stagger replay; the uniformity study calibrates without archiving

**Severity** low · **Category** dead-code

**Where:** `tools/uniformity.py:120-133`, `tools/exposure_headroom.py:89-104`, `tools/uniformity.py:733-744`

There are two decode paths for stored bytes, and a calibration path that keeps no bytes.

**Evidence (from the code):**

```text
Both call `DirectScanner._deinterleave(raw, params, ...)` directly instead of library.decode_raw, which applies _replay. uniformity calls `result = scanner.calibrate_shading()` (keep_data False) and then `reference.save(reference_path)` in place.
```

**Failure scenario:** A 7200 dpi entry analysed by these tools is read without its column-stagger realignment; the study's calibration bytes are not kept.

**Fix:** Use library.decode_raw, and ensure_shading or keep_data plus archive_calibration.

<a id="dataflow-f23"></a>

### F23 -- Silent overwrites in the CLI tools

**Severity** low · **Category** user-error

**Where:** `tools/scan.py:111-115`, `tools/scan.py:405-416`, `tools/scan_roll.py:450-457`, `tools/scan_roll.py:574-596`, `tools/scan_roll.py:736`

Repeated runs overwrite delivered files without warning.

**Evidence (from the code):**

```text
--out defaults to 'scan.tif', and `out.with_suffix(".json").write_text(...)` follows. scan_roll carries an existing folder's roll.json forward and writes `path = out / f"frame{number:02d}.tif"` without _unclaimed.
```

**Failure scenario:** Running tools/scan.py twice without --out replaces the first scan.tif/scan.json. `--roll gold200` for a second strip merges into the first strip's roll.json and overwrites its frameNN.tif (the library entries survive).

**Fix:** Refuse an existing --out without --force, and warn when --roll names a folder whose manifest describes another strip.

<a id="dataflow-f24"></a>

### F24 -- In the window's Delete, the destructive answer is 'No'

**Severity** low · **Category** user-error

**Where:** `tools/gui.py:4480-4494`

Answering No to a 'Keep?' question removes the raw bytes irreversibly.

**Evidence (from the code):**

```text
`keep = messagebox.askyesnocancel("Delete", question + "\n\nKeep the library entry?")` ... `if not keep: shutil.rmtree(entry)`
```

**Failure scenario:** The operator clicks No meaning 'do not keep this result' and rmtree's the only raw bytes of the scan.

**Fix:** Ask 'Also delete the library entry?' with No as the safe default.

<a id="dataflow-f25"></a>

### F25 -- Stale transport and unit statements in code and CLAUDE.md

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/session.py:94`, `rps7200/session.py:1206-1224`, `tools/scan_roll.py:518-521`, `rps7200/protocol.py:287-291`, `rps7200/session.py:257-260`, `tools/scan_roll.py:534`, `tools/scan_roll.py:700-711`

**Doc claim:** rps7200/protocol.py:227 'Millimetres are prohibited in anything an operator reads'; CLAUDE.md:303 vs 517

The comments describe a cap and a ramp that have since changed; the CLI speaks millimetres to the operator.

**Evidence (from the code):**

```text
plan_nudges: 'an integer param in 1..8' and '`DirectScanner.param_for_mm` already clamps silently at param 8', while MAX_CORRECTION_PARAM = 87. scan_roll: 'one command reaches only 1.0118 mm'. units_for_param: 'param 1 travels 2.57', while COMMAND_UNITS = 1.84. seek: 'scan_roll, which each backend has its own copy of', while the demo uses `_drivers_roll = DirectScanner.scan_roll`. scan_roll prints 'offset the film by ... mm' and 'SHORT BY ... mm'. CLAUDE.md gives the aperture as 345.2 units (303) and as 344.5 (517): protocol units vs framing 428/1.2423.
```

**Failure scenario:** A maintainer sizing moves from the docstring assumes an 8-param cap, and CLI output contradicts the rule against millimetres.

**Fix:** Update the comments and print the tool's distances with say_units.

<a id="dataflow-f26"></a>

### F26 -- Entries left uncompressed by a killed window have no CLI command to compact them

**Severity** low · **Category** design

**Where:** `tools/library.py:88-92`, `rps7200/library.py:414-457`, `rps7200/session.py:1986-1994`

Plain raw.bin and uncompressed TIFFs stay about twice their size for ever.

**Evidence (from the code):**

```text
The action choices are ['list', 'verify', 'reconstruct', 'reindex', 'duplicates', 'migrate-raw', 'migrate-direction', 'tag']; library.compact is called only from ScanSession._run's finally.
```

**Failure scenario:** The window crashes after a day of single scans, and those entries are never compacted.

**Fix:** Add `tools/library.py compact`, which finds entries whose raw.file is raw.bin.

<a id="dataflow-f27"></a>

### F27 -- Save As, Save all and Export write TIFFs without a resolution tag

**Severity** low · **Category** error-handling

**Where:** `tools/gui.py:4331-4342`, `rps7200/session.py:1682-1684`

The same picture carries a DPI when written by a roll and none when exported.

**Evidence (from the code):**

```text
_deliver_one calls `note = export.write(path, full, quality=quality)`; FrameWriter calls `export.write(str(path), delivered, resolution=job["dpi"], ...)`.
```

**Failure scenario:** An exported roll opened in NegPy or a print tool has no physical size.

**Fix:** Pass the entry's scan.resolution_dpi to export.write in _deliver_one.

<a id="dataflow-f28"></a>

### F28 -- Debug entries ignore the caller's library root and film notes

**Severity** low · **Category** data-integrity

**Where:** `rps7200/direct.py:1052-1076`

Probes and hold prescans are separated from the roll they belong to.

**Evidence (from the code):**

```text
`root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT`; `film=FilmNotes(notes="captured with RPS7200_DEBUG on")`; the spooled meta is copied before scan_roll adds roll_index and registration.
```

**Failure scenario:** `tools/scan_roll.py --library /mnt/lib` with RPS7200_DEBUG=1 files the frames into /mnt/lib and the metering probes and hold prescans into ./library, with no stock, roll or frame.

**Fix:** Let the caller set the debug root (session root / --library) and attach roll_membership to spooled items.

<a id="dataflow-f29"></a>

### F29 -- session_start's opening SLIDE is the sub-frame forward action and runs only in probes

**Severity** info · **Category** design

**Where:** `rps7200/direct.py:2099-2126`, `tools/hold_probe.py:130`, `tools/transport_truth.py:122`

Probe sessions open with a command the production paths never send, which may be a param-1 forward move.

**Evidence (from the code):**

```text
`self.slide(0x00, param=0x01)` sends `00 01 00 00`, while nudge sends `0x00 <param> 00 04` forward. session_start() is called only by probe tools, never by ScanSession, tools/scan.py or tools/scan_roll.py.
```

**Failure scenario:** Transport measurements from probes start from a film position, and a command history, that the window's rolls never have, so their findings are provisional per CLAUDE.md.

**Fix:** Either send the vendor opening sequence in production too or drop it from the probes, and record it in the command log.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Scanner raw bytes of one pass | library/<id>/raw.bin.gz (or raw.bin before compact) | gzip level 6 (or plain) of INDEX-format lines: 2-byte tag + bytes_per_line, in the order received (bottom-up passes not reversed) | raw | library.save (library.py:274-300) from capture_record / debug raw_path; library.compact (414) | read_raw (604) -> decode_raw, reconstruct, verify, demo._decode, migrate_direction, tools/uniformity.py, tools/exposure_headroom.py | yes, sha256 of the uncompressed bytes recorded; a payload followed by CHECK CONDITION never reaches it (F17) |
| Decoded pixels | library/<id>/scan.tif | TIFF uint16 (uint8 for prescans) (H,W,C) RGB[I], deflate+predictor with tifffile else uncompressed; uncompressed until compact for window single scans | raw decode, upright, 7200 dpi stagger-trimmed; corrected only when image.corrections_applied=['shading'] (FrameWriter fallback, legacy) | library.save:267, compact, tools/library.py migrate-raw, migrate_direction | load/corrected/reconstruct/verify(sha)/demo/picture_signature/GUI views | lossless |
| Shading reference used for the pass | library/<id>/shading.npz | npz (savez_compressed): ref{c}/dark{c} float64[pixels_per_line], mean/darkmean float64, channels, pixels_per_line | derived reduction of the calibration bytes (which are not in the entry) | library.save:272 via ShadingReference.save | load, corrected, reconstruct (legacy), demo._decode, tools/library.py _one_shading_explains, exposure_headroom | exact float64; the calibration data.bin it was reduced from is outside the entry |
| CCD mask of the pass | library/<id>/ccd_mask.bin | raw bytes (5172, 0x00 used / 0x70 unused) | raw | library.save:274 | load, corrected, demo | yes; absent for demo passes resampled from a stored picture |
| Framing prescan stored with a roll frame | library/<id>/prescan.tif | TIFF uint8 (H,W,3) | CORRECTED (unless the roll ran shading=False), not labelled in the record | library.save:270 from session._file prescan=rf.prescan (session.py:2627) and tools/scan_roll.py:752 | demo._stored/picture_signature/best_pair, migrate_direction | lossless TIFF of corrected pixels; its raw pixels, raw bytes and own CCD mask are not stored (F03) |
| Entry record | library/<id>/scan.json | JSON indent=2, default=str; written via .scan.json.part + fsync + os.replace | metadata: image, raw (layout), scan (SCAN_FIELDS), extra (commands, mode, started_utc, shading_origin, roll_membership, bracket_*, demo...), device, device_settings, metering, registration, calibration (report, skipped, light_mean rounded 0.1), prescan, film, tags, provenance, files (sha256) | library.save:397, compact, add_tags, migrate-raw, migrate_direction | entries, load, verify, reconstruct, demo, gui roll_entry_index/roll_summary, analysis tools | yes except values json cannot carry (stringified) and metering levels rounded to 4 dp; roll/bracket frames lack metering (F04) |
| Entry markers and leftovers | library/<id>/INCOMPLETE, .scan.json.part, .scan.tif.part, scan.before-migrate-raw.tif, REJECTED | text / TIFF | - | library.save (263), _write_atomic, _replace_tiff, migrate-raw, tools/uniformity.py | verify (INCOMPLETE) | - |
| Library index | library/index.json | JSON summary | derived | reindex (library.py:1045) after every save, non-atomic write_text | nothing in code | derived |
| Calibration cache | calibration/shading.npz (CWD-relative; demo/calibration/... under --demo) | npz float64 | derived reference | DirectScanner.save_shading (direct.py:736, temp+replace); tools/uniformity.py:742 in place | load_shading (reuse), GUI mtime check | exact float64 |
| Calibration archive | calibration/<UTC>[-N]/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | raw bytes / npz / JSON (commands, sha256, geometry) | raw calibration lines | archive_calibration (direct.py:749) inside ensure_shading, device open, plain writes | nothing (tests only); linked from entries only via extra.shading_origin.archive on a fresh calibration | yes; not verified, not in the library |
| Debug spool | $TMP/rps7200-debug-*/NNN-{image.npy, raw.bin, meta.json, shading.npz, ccd_mask.bin} | npy / bytes / JSON / npz | raw | _debug_capture (direct.py:908) with the device open | _debug_flush (1031) at close() | yes; claimed passes also spooled and kept until close (F08); never flushed after force abort (F12) |
| Walk manifest | rolls/<roll>/survey.json (+ .bak, .legacy, .unreadable) | JSON via write_manifest (temp+fsync+retried rename) | metadata: settings, frames[number, index, transport_position, registration marks, prescan file, prescan_rotation/flipped] | RollManifest (session.py:1016) / tools/scan_roll.py | ScanSession._roll (carry), gui read_survey/roll_summary, scan_roll hold_from_walk (plain json.loads) | yes; only home of walk registration/aim evidence (F14) |
| Roll manifest | rolls/<roll>/roll.json (+ .bak, .legacy) | JSON via write_manifest | metadata: settings, wanted, frames[registration, exposure/gain/offset, done, entry, filing_error, rotation/flipped] | RollManifest.record/filed (scanner and writer threads, locked) | ScanSession._roll, read_survey, roll_summary, scan_roll resume | yes |
| Walked prescans | rolls/<roll>/prescanNN.tif, prescanNN-before.tif | TIFF uint8 | corrected; GUI oriented by the session rotation/flip, CLI as scanned | FrameWriter export.write (GUI) / tiff.write on the scanning thread (scan_roll.py:660, 693) | read_survey, hold_from_walk (unorient -> Approved.reference) | lossless of corrected pixels; -before files are in no library entry |
| Delivered roll frames | rolls/<roll>/frameNN.tif | TIFF uint16, oriented, optionally mono | corrected by that day's code | FrameWriter export.write (session.py:1682), after library.save | operator/NegPy; roll_summary sizes | not re-derivable byte-identically later (today's correction differs); overwritten on rescan |
| Operator decisions | rolls/<roll>/approved.json | JSON: frames[number, offset_mm (4 dp), rotation, flipped, reference_entry, source, as_walked] | - | gui _write_approved (3265) merged, atomic | read_approved (5410), roll_exports, on_delete_rolls | yes |
| Output-folder copies | <out>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng), <out>/prescans/... | TIFF 16-bit, or JPEG 8-bit + 4-channel DNG | corrected, oriented | FrameWriter export.write | operator | JPEG lossy |
| CLI delivered scan | --out (default scan.tif) + <out>.json | TIFF/JPEG + JSON meta (incl. bracket stats) | corrected (merged for a bracket) | tools/scan.py:413-416 | operator | merged image exists only here; silently overwritten |
| Save As / Save all / Export files | operator-chosen paths | TIFF/JPEG(+DNG), no resolution tag | corrected via library.corrected, oriented | tools/gui.py _deliver_one (4309) | operator | - |
| Window settings and sheet state | gui-settings.json \| $RPS7200_SETTINGS \| demo/gui-settings.json | JSON sections controls/film/output/window/presets/shortcuts/rolls/sheet | - | settings.save (104) from _remember | settings.load at window start, _recall_sheet_state | sheet keyed by roll folder name only |
| Demo picture signatures | demo/pictures.npz | npz paths + grey grids | derived | DemoScanner._sign_pictures (np.savez, non-atomic) | same | cache |
| Comparison files | 1_nothing_done.tif, 2_corrected.tif, 3_corrected_inverted.tif | TIFF | raw decode / corrected / corrected inverted | tools/make_comparison.py | Stefan | - |

## Dataflow notes

["Entry: the only way bytes enter is Transport.command -> _read_payload (usb_transport.py:717/772), which returns exactly read_size bytes or raises. read_planes joins them (direct.py:1878), keeps them as last_raw only when keep_raw or debug, and decodes with decode_index (1923). The raw bytes are never reversed; the decode turns bottom-up rows.", "Every corrected picture is made by apply_shading (shading.py:196): at scan time in scan() (direct.py:3129) and later by library.corrected (library.py:548). Callers get the corrected image back from scan(); the raw one is published as last_pixels_raw (3229) and must be read before the next pass.", "Filing: scan() -> ScanSession._file (session.py:2781) or the CLI hold()/writer.submit -> FrameWriter._write (1618) on the writer thread (GUI and rolls) or after close (tools/scan.py) -> library.save (library.py:216). capture_record (direct.py:889) supplies the reference, the mask of the last pass, and the raw bytes and layout; raw_bytes_disagree (session.py:734) drops bytes of another shape.", "Threads: UI (Tk) submits jobs; one 'scanner' worker owns the device; FrameWriter writes and gzips; EdgeWatch reads frame edges; daemon threads read full-resolution entries and Save all/Export; the demo signs pictures on its own thread. Only the scanner worker touches the device (a force abort closes the transport from the UI thread).", "Leaves: the library (raw scan.tif, raw bytes, reference, mask, record, corrected prescan.tif for roll frames); roll folders (manifests, corrected prescanNN.tif/frameNN.tif, approved.json); calibration/ (cache and archives, outside the library); delivered files (out_dir, Save As/all, Export, tools/scan.py --out); gui-settings.json.", "Not re-derivable from the library: calibration bytes when a cached reference was reused, the raw/mask of roll-frame prescans, the metering of rolls and brackets, walk registration and aim marks, prescanNN-before.tif, and the bracket merge output (re-mergeable from the passes' bracket_* fields)."]

## Flow: 1. Single scan from the GUI (calibrate -> meter -> pass -> USB bytes -> decode -> direction -> shading/mask -> library.save -> corrected view / Save As)

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:1967 on_calibrate_pressed -> 2030 ask_to_calibrate -> 1993 on_calibrate` | A measuring calibration only starts after the operator ticks 'The film is in the transport'; 'reuse' with a cached file skips the question. Submits Calibrate(mode, reference=session.reference) and sets self.calibrated=True optimistically until the 'calibrated' event arrives. | Calibrate(mode='measure'\|'reuse', reference='calibration/shading.npz' or demo/...) (session.py:1363) | UI (Tk main) | none |
| 2 | `rps7200/session.py:1928 _run -> 2022 _dispatch -> 2041 _calibrate -> rps7200/direct.py:806 ensure_shading` | reuse and file exists -> load_shading (direct.py:713); otherwise calibrate_shading(keep_data=True) | Path of the cache | scanner worker | none |
| 3 | `rps7200/direct.py:2128-2375 calibrate_shading` | _CommandLog.start; READ STATE/wait_warm/TUR; exposure+highlight WRITEs; calibration-info READ(128); SET FRAME (0,3431,10343,6888); cmd 0x17; gain/offset read-back+write; MODE SELECT (3600 dpi, 0x80, 8-bit, INDEX, quality 0x0800); SLIDE 10 01 00 00; shading descriptor; START SCAN; 10 s silence; read_lines(4, 2*width+2) until EndOfData/ScanReadError; get_ccd_mask(width); finish_scan; calculate_shading (shading.py:109) splits dark/light lines by level | ~1.66 MB of 16-bit tagged lines (bytes) -> ShadingReference{ref,dark: float64[5172] for channels 0-2, means, pixels_per_line} + 5172-byte CCD mask + command log | scanner worker | in memory: _shading, _ccd_mask, _shading_origin |
| 4 | `rps7200/direct.py:749-805 archive_calibration, 736 save_shading` | Writes calibration/<UTC>/data.bin (exact bytes), ccd_mask.bin, shading.npz, calibration.json (commands, sha256) with plain writes while the device is open; caches calibration/shading.npz via temp + os.replace | bytes / npz float64 / json | scanner worker | calibration/<UTC>[-N]/*, calibration/shading.npz (outside the library) |
| 5 | `rps7200/session.py:2059 emit('calibrated') -> tools/gui.py:3652 _handle` | window learns whether a reference is now held | Event(kind='calibrated', done=0\|1) | scanner -> UI via event queue | none |
| 6 | `tools/gui.py:2151 on_scan` | asks to calibrate if none; validates dpi (25-7200) and exposure; pins the on-screen arrangement; submits Scan(...) with shading left at its default True | Scan(resolution, infrared, fast_infrared, film, auto_exposure, exposure_scale, mono, mono_channel, notes, tags) (session.py:1384) | UI | none |
| 7 | `rps7200/session.py:2100 _scan -> rps7200/direct.py:2842 scan(keep_raw=True)` | Refuses before any command: infrared on B&W/Kodachrome (ValueError, 2895), >5172 columns (uncorrectable, 2947), no/too-narrow reference (uncalibrated, 2963). No suspect check here. | arguments | scanner worker | none |
| 8 | `rps7200/direct.py:2978 -> 2385 auto_exposure` | base=get_gain_offset; per round set_gain_offset(base) and a probe scan() at 300 dpi RGB 16-bit, corrected, keep_raw=True (each probe is a full scan() with its own command log and debug spool); metering_slice region from round 1; 99.5th percentile/65535 per channel; asymmetric tolerance; growth clamped at 65535/base exposure; last_metering dict (levels/scales rounded to 4 dp) | list[float] scales; last_metering dict | scanner worker | probes only through the debug spool |
| 9 | `rps7200/direct.py:3002-3066` | _CommandLog.start; READ STATE x<=4, wait_warm, TUR, READ STATE (media bit logged, not enforced); WRITE exposure/highlight; SET FRAME; cmd 0x17; READ GAIN/OFFSET -> Settings.scaled (protocol.py:601, int, clamp 100..65535) -> WRITE GAIN/OFFSET; MODE SELECT (INDEX, byte14_for(passes), fast-IR bit gated on infrared); TUR; SLIDE INIT 10 16 00 00; wait_ready; carriage_record (last READ STATE hex, stale flag) | Settings ints; command log entries (cdb/out/in hex) | scanner worker | none |
| 10 | `rps7200/direct.py:3237 _read_pass -> 1605 start_scan, 1735 get_ccd_mask, 1746 get_parameters, 1799 read_planes -> 1757 read_lines -> rps7200/usb_transport.py:772 command -> 717 _read_payload` | Paced batch READs; NoDataYet re-polled every 20 ms up to READ_IDLE_S (287 s for untied IR); a transfer returns exactly read_size bytes or raises; chunks joined (direct.py:1878) and kept as last_raw/last_raw_layout (1885) when keep_raw or debug; decode_index (1923) splits lines by tag into <u2/u1 planes, truncates to the common height, read_direction (direction.py:85) from first/last tags, reverses rows if bottom-up; finish_scan; any exception before the last line marks the device suspect | bytes channels*lines*(bpl+2) -> ndarray (H,W,C) uint16 upright; 5172-byte mask; ScanParameters | scanner worker | none |
| 11 | `rps7200/direct.py:3087-3229` | 7200 dpi stagger realignment (trims 4 rows); raw_pixels kept; apply_shading (shading.py:196) two-point per column through the mask, clamped, report; meta dict (3155) incl. commands, mode, shading_origin, carriage_state, read_direction, metering only when this scan metered; _debug_capture (908) spools raw pixels .npy, raw bytes, meta json, reference npz, mask to $TMP when debug; sets last_pixels_raw/last_scan_meta; returns the corrected image | corrected uint16 copy (H,W,C) + meta dict | scanner worker | $TMP/rps7200-debug-*/NNN-* when RPS7200_DEBUG |
| 12 | `rps7200/session.py:2117-2123, 2134 _prescan_here, 2148 _note_reversal, 2705 _deliver` | raw_image = last_pixels_raw; reversal against the last prescan only for passes of unknown direction (adds meta['reversal']); a <=1400 px decimated copy goes to the UI as a Result event | working copy ndarray + dict(meta) | scanner worker | none |
| 13 | `rps7200/session.py:2781 _file` | capture_record (direct.py:889: reference, ccd_mask, raw bytes, layout); raw_bytes_disagree (734) drops bytes of another shape; composes reversal and orientation into meta rotation/flipped; drops a raw_image of another shape; debug_claim(raw_image); FrameWriter.submit(compress=False because not a roll, paths=[out_dir copy] if set) | job dict with corrected image, raw_image, meta, capture | scanner worker | none (queued) |
| 14 | `rps7200/session.py:1618 FrameWriter._write -> rps7200/library.py:216 save` | preview.orient + to_monochrome for delivery only; library.save: _reserve dir, INCOMPLETE marker, scan.tif (raw decode, uncompressed), shading.npz, ccd_mask.bin, raw.bin (plain, streamed sha256), scan.json via _write_atomic, marker removed, reindex; then export.write of the out_dir copy (corrected, oriented); on_done -> ScanSession._filed -> 'filed' event | raw uint16 pixels, bytes, float64 reference, json record | writer (FrameWriter daemon) | library/<id>/{scan.tif,raw.bin,shading.npz,ccd_mask.bin,scan.json}, library/index.json, out_dir copy |
| 15 | `tools/gui.py:3598 _handle -> 3882 _add_result -> 3918 _arrange` | hides a superseded prescan at the same transport position, composes reversal with the photograph's orientation, shows the working copy; 'filed' sets result.entry | Result | UI | none |
| 16 | `tools/gui.py:4575 _load_full -> rps7200/library.py:548 corrected` | reads scan.tif + shading.npz + ccd_mask.bin and applies today's apply_shading; handed back through a queue | full-resolution corrected ndarray | full-res reader daemon | none |
| 17 | `tools/gui.py:4221 on_save_as / 4237 on_save_all -> 4309 _deliver_one` | library.corrected -> preview.orient(result.rotation, result.flipped) -> to_monochrome if mono -> export.write(path) with no resolution; falls back to the reduced preview when not filed yet | corrected uint16/uint8 ndarray | UI (Save As) / save-all daemon | operator-chosen TIFF/JPEG(+DNG) |
| 18 | `rps7200/session.py:1966-1994 (_run finally) -> rps7200/direct.py:1134 close -> 1031 _debug_flush; rps7200/library.py:414 compact` | scanner.close() unless force-aborted: transport closed then unclaimed spooled passes filed (compress=True) into $RPS7200_DEBUG_ROOT or ./library; writer.finish; unsaved manifests re-saved; library.compact for every uncompressed entry (raw.bin -> raw.bin.gz verified by sha256, TIFFs recompressed, record updated) | files | scanner worker + writer | library/<id>/raw.bin.gz, recompressed TIFFs, updated scan.json; debug entries |

## Flow: 2. Single scan from tools/scan.py (single pass, --bracket, --reuse)

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/scan.py:118-253` | argparse and pre-open refusals: IR on B&W/Kodachrome, bracket 2-9, dpi>0, correctable_at unless --no-shading, --out extension, stops>0, --bracket with --ir, exposure-scale arity; fast_ir cleared without --ir; say_estimate warns above 8 min | argparse Namespace | main | none |
| 2 | `tools/scan.py:265-275 -> rps7200/direct.py:806 ensure_shading` | DeferredInterrupt installed; DirectScanner(debug=None) opened; inquiry; ensure_shading(ref_path, reuse, skip=--no-shading): no question about film; reuse loads calibration/shading.npz when present, otherwise Flow 1 steps 3-4 | ShadingReference, mask | main | calibration/<UTC>/*, calibration/shading.npz |
| 3 | `tools/scan.py:346-360 (single)` | s.scan(resolution, infrared, exposure_scale, auto_exposure, film, shading, keep_raw=library is not None, fast_infrared) (Flow 1 steps 7-11); hold() appends raw pixels (last_pixels_raw), meta, reference, mask, raw bytes, layout, inquiry to `pending` in RAM and claims the pass | pending: list[dict] of ndarray + bytes | main | none until close |
| 4 | `tools/scan.py:314-345 -> rps7200/direct.py:2736 scan_bracket, 2681 bracket_ladder` | scales from --exposure-scale / auto_exposure / 1.0; geometric ladder topped at the 16-bit timer ceiling; each pass scan(exposure_scale=scale*k) with meta bracket_index/ratio/passes/stops; on_pass keeps sensor pixels (raw, or a rail byte map without the library) and calls hold(); corrected frames retained (retain default True); hold raises _StoppedBetweenPasses after Ctrl-C | frames (corrected), ratios, metas; pending raw | main | none until close |
| 5 | `tools/scan.py:361-376` | after the context exits (close -> debug flush of unclaimed metering probes) each pending pass is filed with library.save(compress=True): raw.bin.gz, deflate TIFF, shading.npz, ccd_mask.bin, scan.json; not wrapped in try | raw pixels/bytes | main | library/<id>/* |
| 6 | `tools/scan.py:385-420` | bracket: merge_bracket(corrected frames, ratios, sensor_frames\|sensor_rails) and meta['bracket'] stats; mono optional; export.write(--out, resolution=dpi) + --out .json sidecar (meta incl. commands). The merged image is delivered only; the library holds the passes | merged/corrected ndarray; meta json | main | --out file (default scan.tif) and <out>.json |
| 7 | `rps7200/direct.py:835-846, 713-733, 3207-3211` | --reuse: load_shading sets _shading_origin {'action':'loaded', path, file_modified_utc, loaded_utc} (no archive link) and every corrected pass records it in extra.shading_origin | dict | main | in scan.json extra |

## Flow: 3. Roll: walk/prescans -> frame edges -> contact sheet -> approved.json -> Roll -> holds/nudges -> per-frame scan -> FrameWriter -> library + frameNN.tif + roll.json

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:2181 on_roll (dry run) / tools/scan_roll.py:411-610 --dry-run` | validates range, warns when frame_edges cannot read that prescan dpi/film, chooses the folder (session.roll_dir), clears or keeps the sheet, EdgeWatch.begin; submits Roll(dry_run=True, out=folder, prescan_resolution, correct...). No up-front correctable_at check in the window | Roll dataclass (session.py:1410) | UI / CLI main | none |
| 2 | `rps7200/session.py:2275-2488 _roll -> 229 seek` | seek: wait_warm, _ask_position (8 reads), checked rewind/advance, verify arrival else FilmNotPlaced; folder mkdir; survey.json (walk) or roll.json (roll); earlier manifest carried (live RollManifest, or earlier_manifest + keep_first_numbering + renumbered); settings block; RollManifest; per-frame orientations from approved | manifest dict | scanner worker | rolls/<roll>/ (+ .legacy/.unreadable copies) |
| 3 | `rps7200/direct.py:3765-4160 scan_roll(keep_raw=True)` | checks meter/film/IR (not resolution); baseline=get_gain_offset; StripWalk(reader=tools/frame_edges.walk_reader(film)) when correcting; per index: position/place_on_strip, skip unchosen, prescan (2053 -> scan 300 dpi RGB 8-bit, corrected), raw_prescan=last_pixels_raw, prescan_meta=last_scan_meta, frame_contrast (blank ends roll), registration marks, walk.observe; approved -> _hold_to_approved; correct -> _aim_frame; dry run yields RollFrame(prescan, raw_prescan, prescan_meta, marks, prescan_before) | RollFrame (direct.py:379) | scanner worker | none |
| 4 | `rps7200/session.py:2508-2600 (dry run)` | _deliver prescan; _file(corrected prescan, prescan_meta+roll_membership, raw_image=raw_prescan, path=rolls/<roll>/prescanNN.tif) -> FrameWriter (compress=True) -> library entry of the raw prescan with its own mask/bytes + prescanNN.tif (corrected, oriented by the session rotation); prescan_before -> prescanNN-before.tif only (file_entry=False); RollManifest.record -> survey.json (registration marks, prescan file, prescan_rotation/flipped). CLI: tiff.write prescanNN.tif on the scanning thread (scan_roll.py:660) + FrameWriter entry | 8-bit prescans, marks | scanner worker -> writer | library/<id>/* (raw prescan), rolls/<roll>/prescanNN.tif, prescanNN-before.tif, survey.json |
| 5 | `tools/gui.py:3882 _add_result -> _into_survey -> tools/frame_edges/watch.py:125 EdgeWatch.add (214 _run)` | each walked prescan (working copy) is read for frame edges against the other frames' summaries; offsets/notes (source measured/unconfirmed/neighbours/none) published by version | offset_mm per frame + notes | UI + edge-watch thread | none |
| 6 | `tools/gui.py:3699 _walk_ended -> 2486 on_contact_sheet -> 7776 _ContactSheet; tools/gui.py:5104 read_survey on reopen` | sheet from survey results (corrected prescans), proposals, ticks, offsets (typed in units, snapped to the plan_nudges lattice), per-frame rotation/flip; state kept via _store_sheet_state in gui-settings.json 'sheet'[folder name]; reopen re-reads prescanNN.tif (unoriented), survey.json, roll.json, approved.json | sheet state dict | UI | gui-settings.json 'sheet' |
| 7 | `tools/gui.py:8690 _ContactSheet._scan -> 3054 on_scan_chosen -> 6402 approved_from_sheet -> 3265 _write_approved` | validates sheet dpi, pins predpi to the survey's, builds Approved(number, offset_mm, rotation, flipped, reference=working-copy prescan array, reference_entry, source), confirms, merges rolls/<roll>/approved.json (atomic, .bak), submits Roll(only, approved, start_at, frames=span, out=folder). CLI equivalent tools/scan_roll.py:230-310 hold_from_walk -> walked_prescans + preview.unorient(tiff.read(prescanNN.tif)) -> frame_edges.propose_centred | tuple[Approved] | UI | rolls/<roll>/approved.json |
| 8 | `rps7200/direct.py:3306 _hold_to_approved (called at 4006) -> 3611 nudge` | measure_shift_mm(reference, prescan) -> hold_plan -> nudge (param_for_mm clamp 1..87; SLIDE 00\|01 param 00 04) -> sleep 0.4 s -> verification prescan (corrected) -> repeat <= MAX_HOLD_MOVES; wrong-way check turns holding off; marks['approved'] = history/confidence/final_mm/residual_mm/row_reversed; the last verification prescan becomes the frame's prescan | mm floats, prescans | scanner worker | verification prescans only through the debug spool |
| 9 | `rps7200/direct.py:4099-4137` | METER_EACH/ONCE: set_gain_offset(baseline), auto_exposure -> scales; set_gain_offset(baseline); scan(resolution, IR, FULL_FRAME, exposure_scale=scales, keep_raw) recorded as exposure_metered False with no metering record; meta roll_index, roll_position, registration=marks; yield RollFrame(image corrected, raw_image, prescan corrected, raw_prescan, prescan_meta) | RollFrame | scanner worker | none |
| 10 | `rps7200/session.py:2600-2640 -> 2781 _file -> 1618 FrameWriter._write -> rps7200/library.py:216` | _note_reversal vs the frame's prescan; _deliver; _file(image, meta+roll_membership, raw_image, prescan=rf.prescan (corrected; raw_prescan dropped), prescan_meta, path=frameNN.tif, mono, on_filed); capture of the frame pass; FrameWriter gzips while the next frame scans: library.save (raw scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif corrected, scan.json) then export frameNN.tif (corrected, oriented, mono) + out_dir copy; on_filed -> RollManifest.filed; a filing error stops the window's roll | raw + corrected ndarrays, bytes | scanner worker -> writer | library/<id>/*, rolls/<roll>/frameNN.tif, out_dir copy |
| 11 | `rps7200/session.py:2654-2686 -> 1107 RollManifest.record / 1126 filed -> 886 write_manifest` | frame record (number, index, transport_position, registration, error, done False until filed, exposure/gain/offset, rotation/flipped) replaces any earlier record of that number; written whole via temp + fsync + retried rename (.bak on first write). CLI: tools/scan_roll.py:781 and final save after writer.finish | JSON | scanner worker + writer (locked) | rolls/<roll>/roll.json |
| 12 | `rps7200/session.py:2688-2702, 1966-1994` | frames.close() ends the generator; on session close writer.finish and unsaved manifests saved once more | - | scanner worker | manifests |

## Flow: 4. Demo: DemoScanner inputs through the same path

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:8856-8907 main` | --demo: ScanSession(root=demo/library, reference=demo/calibration/shading.npz, rolls=demo/rolls), settings demo/gui-settings.json; session._open_scanner = DemoScanner(source 'library' (real, read-only), entry, no_film=--look-only, libraries_beside, cache=demo/pictures.npz). The window, ScanSession, FrameWriter and library.save are the real ones; GUI uses self.demo only for the title | - | UI | none |
| 2 | `rps7200/demo.py:317-343 open, 1318 _sign_pictures` | lists source entries; picture signatures (grey grids of prescan.tif or <=600 dpi scan.tif) cached on a daemon thread; best_pair | np arrays | scanner + demo-pictures thread | demo/pictures.npz (np.savez, non-atomic) |
| 3 | `rps7200/demo.py:497-505 ensure_shading` | sleeps 210/SPEED s (1 s for reuse, whether or not a cache exists), sets _calibrated, returns no reference; writes nothing | summary dict | scanner | none |
| 4 | `rps7200/demo.py:651-720 scan, 568 prescan` | same refusals in the same order (IR/film ValueError; DirectScanner.correctable_at/uncorrectable; DirectScanner.uncalibrated), then _need_film (UsbError with --look-only); auto_exposure is DirectScanner.auto_exposure on demo probes; _work sleeps estimate_seconds/SPEED with progress | - | scanner | none |
| 5 | `rps7200/demo.py:866 _take -> 1411 _stored -> 1057 _source_for -> 1141 _decode` | picks the entry for the film (nearest 1800 dpi with raw bytes) or the strip entry at the roll position; library.decode_raw of the real raw.bin.gz (upright, stagger replay) with the entry's shading.npz/ccd_mask; fallbacks: stored scan.tif (legacy corrected flagged), prescan.tif (handed over as corrected unless taken raw), synthetic test card | stored uint16 (H,W,C) + reference | scanner | none |
| 6 | `rps7200/demo.py:932-1017 _fit, _pass_reference` | nearest-neighbour index maps to the shape the library recorded for that dpi; column roll by the simulated film position (_shift uses APERTURE_MM/width); depth via to_8bit or *257; a clear IR plane added when missing; reference resampled to the shown columns (mask dropped) | fitted raw ndarray + pass reference | scanner | none |
| 7 | `rps7200/demo.py:604-648 _read_as_carriage -> rps7200/direction.py:117 encode_index -> DirectScanner.decode_index` | pixels encoded as index lines in carriage order (bottom-up when the modelled carriage is at the far end), decoded by the driver; last_raw/layout when keep_raw; capture set; apply_shading last; meta demo=True, demo_source, read_direction, modelled carriage_state, DEVICE_SETTINGS exposure; no protocol_revision/commands/mode | bytes, upright raw, corrected image, meta | scanner | none |
| 8 | `rps7200/session.py:2100-2135, 2781, 1618` | identical to Flow 1 steps 12-18: fitted raw pixels, encoded bytes and pass reference filed into demo/library; views and Save As via library.corrected | as Flow 1 | scanner -> writer -> UI | demo/library/<id>/*, demo/rolls/* |
| 9 | `rps7200/demo.py:722-776 scan_roll -> DirectScanner.scan_roll` | the driver's roll loop, hold and aim run on the stand-in; demo nudge moves _film_mm with DirectScanner.param_for_mm/STEP_MM/OVERHEAD_MM plus a retyped backlash model; strips from _strip_for/_next_strip; one slipping frame per strip | RollFrame | scanner | as Flow 3 under demo/ |

## Flow: 5. Offline: library entry -> corrected / reconstruct / verify / migrate-raw / analysis tools

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `rps7200/library.py:1030 entries, 514 entry_path` | globs */scan.json one level deep; adds _dir | list[dict] | caller | none |
| 2 | `rps7200/library.py:524 load` | tiff.read scan.tif + record + ShadingReference.load + mask bytes | (ndarray, record) | caller | none |
| 3 | `rps7200/library.py:548 corrected` | 'already' when corrections_applied has shading; 'deliberately raw' for SHADING_SKIPPED_EXPLICIT; 'raw -- correction was asked for'; 'no reference'; else apply_shading with today's code. Used by the GUI view, Save As, Save all, Export (tools/gui.py:2709 via roll_exports 5335 / roll_entry_index 5478), tools/make_comparison.py, dpi_analysis, linearity, registration_margin | corrected ndarray | caller | none (callers write deliverables) |
| 4 | `rps7200/library.py:604 read_raw, 657 decode_raw` | gunzip whole raw into memory; ScanParameters from the layout; decode_index; _replay (7200 dpi stagger) | raw upright ndarray | caller | none |
| 5 | `rps7200/library.py:687 reconstruct; tools/library.py:130-151` | decode_index; legacy corrected entries re-shaded; _replay; compares shape, recorded direction, exact equality, legacy bottom-up; exit 1 when any decode changed | verdict strings | CLI main | none |
| 6 | `rps7200/library.py:1068 verify` | INCOMPLETE / missing scan.json dirs; id vs dir; scan.tif sha; reference/mask presence; files{} sha; missing reference (except explicit raw, demo, uncalibrated-on-purpose tag); raw presence and sha of the decompressed bytes | problem strings | CLI main | none |
| 7 | `tools/library.py:152-237 migrate-raw` | reconstruct + decode_raw; mislabelled entries only if one shading explains them, labelled entries unconditionally; rewrite scan.tif to the plain decode (old kept as scan.before-migrate-raw.tif), update image fields, reindex | raw ndarray | CLI main | scan.tif, scan.before-migrate-raw.tif, scan.json |
| 8 | `rps7200/library.py:777 migrate_direction` | records read_direction from raw tags, turns bottom-up scan.tif, judges prescan.tif against the upright scan by correlation (REVERSAL_MARGIN) and turns it when decisive | ndarrays | CLI main | scan.tif/prescan.tif/scan.json with --write |
| 9 | `rps7200/library.py:899-1027 signature/duplicates/prunable/same_data -> tools/library.py duplicates --delete` | rmtree only entries whose raw sha (else image sha) equals a kept entry of the same signature | records | CLI main | deletions |
| 10 | `rps7200/library.py:414 compact` | raw.bin -> raw.bin.gz verified against sha, TIFFs rewritten compressed, record updated; called only from ScanSession close | files | scanner worker | entry files |
| 11 | `tools/uniformity.py:108-150, tools/exposure_headroom.py:89-104` | own decode through DirectScanner._deinterleave (no stagger replay) plus the entry reference | ndarray | CLI main | analysis outputs |

## Flow: 6. Settings and calibration cache

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `rps7200/direct.py:806-887 ensure_shading` | reuse and exists -> load_shading (713) from the cache (origin 'loaded' with file mtime); else calibrate, archive (749) to calibration/<UTC>/ and cache via save_shading (736, .shading.part.npz + os.replace). Paths are CWD-relative (GUI home/calibration/shading.npz; tools --reference) | ShadingReference npz | scanner worker / CLI main | calibration/shading.npz, calibration/<UTC>/* |
| 2 | `tools/gui.py:1984-1991, 2071-2079` | window reads the cache's existence and mtime for 'reuse' and the prompt's age note; CLI --reuse shows no age | stat | UI | none |
| 3 | `rps7200/direct.py:3207-3211 -> rps7200/library.py:329` | every corrected pass records shading_origin (calibrated: resolution, width, measured_utc, archive path; loaded: path, mtimes) in the entry's extra | dict | scanner worker | scan.json extra.shading_origin |
| 4 | `rps7200/settings.py:59 load / 104 save; tools/gui.py:376, 644 _restore, 692 _remember, 2666 _store_sheet_state` | gui-settings.json sections controls/film/output/window/presets/shortcuts/rolls/sheet; unreadable file moved aside; written whole via .part + replace on close and on every sheet change | JSON | UI | gui-settings.json \| $RPS7200_SETTINGS \| demo/gui-settings.json |
| 5 | `tools/uniformity.py:733-744` | calibrate_shading() without keep_data and reference.save(reference_path) written in place (no archive, not atomic) | npz | CLI main | the study's reference path |

## Module dependencies

| Module | Imports | Notes |
|---|---|---|
| `rps7200.protocol` |  | Leaf: SCSI constants, units (MM_PER_UNIT, say_units), Settings, exceptions. |
| `rps7200.usb_transport` |  | ctypes/libusb, loaded lazily. |
| `rps7200.direction` | rps7200.protocol |  |
| `rps7200.shading` |  | numpy only. |
| `rps7200.defects` |  |  |
| `rps7200.uniformity` | rps7200.protocol |  |
| `rps7200.framing` | rps7200.protocol, rps7200.uniformity (lazy, framing.py:1039) |  |
| `rps7200.tiff` |  | optional tifffile |
| `rps7200.dng` |  |  |
| `rps7200.export` | rps7200.dng, rps7200.tiff | optional Pillow |
| `rps7200.mono` | rps7200.protocol |  |
| `rps7200.preview` | rps7200.mono |  |
| `rps7200.bracket` |  |  |
| `rps7200.console` |  |  |
| `rps7200.settings` |  |  |
| `rps7200.shortcuts` |  |  |
| `rps7200.usbpcap` |  |  |
| `rps7200.direct` | rps7200.defects, rps7200.framing, rps7200.protocol, rps7200.direction, rps7200.shading, rps7200.usb_transport, rps7200.library (lazy in _debug_flush, direct.py:1042) | Cycle direct <-> library, broken only by the lazy import. The offline decode (decode_index, _realign_native_column_stagger) lives as static methods on the USB driver class, so every offline reader imports the driver module. |
| `rps7200.library` | rps7200.tiff, rps7200.direct, rps7200.protocol, rps7200.shading, rps7200.direction (lazy), rps7200.framing (lazy) | Top-level import of direct (see cycle). |
| `rps7200.session` | rps7200.export, rps7200.library, rps7200.preview, rps7200.direct, rps7200.direction, rps7200.framing, rps7200.mono, rps7200.protocol | Driver-side orchestration also owns delivery (export/preview) and roll manifests. |
| `rps7200.demo` | rps7200.library, rps7200.tiff, rps7200.direct, rps7200.direction, rps7200.export, rps7200.framing, rps7200.protocol, rps7200.session, rps7200.shading, rps7200.usb_transport | Stand-in borrows DirectScanner.scan_roll/_hold_to_approved/_aim_frame/auto_exposure/param_for_mm; not a subclass. |
| `tools.frame_edges` | rps7200.framing, rps7200.protocol | Internal: propose -> centre/roll/sides/units/vote; watch -> propose/roll/units; roll -> chroma/gapmodel. rps7200 never imports tools (checked: no 'from tools' in rps7200/). |
| `tools.gui` | rps7200.export, rps7200.library, rps7200.preview, rps7200.settings, rps7200.shortcuts, rps7200.tiff, rps7200.console, rps7200.direct, rps7200.framing, rps7200.mono, rps7200.protocol, rps7200.session, rps7200.demo (lazy in main), tools.frame_edges | Hands tools.frame_edges.walk_reader to the session as edge_reader (dependency injection, no layering violation). Retypes APERTURE_MM (gui.py:213). |
| `tools.scan` | rps7200.export, rps7200.library, rps7200.bracket, rps7200.console, rps7200.direct, rps7200.mono, rps7200.session (lazy estimate_seconds) |  |
| `tools.scan_roll` | rps7200.preview, rps7200.tiff, rps7200.console, rps7200.direct, rps7200.library, rps7200.protocol, rps7200.session, tools.frame_edges |  |
| `tools.library` | rps7200.library, rps7200.console, rps7200.shading (lazy), rps7200.tiff (lazy) |  |
| `tools.make_comparison` | rps7200.export, rps7200.library, rps7200.preview, rps7200.tiff, rps7200.console |  |
| `tools.uniformity` | rps7200.library, rps7200.console, rps7200.direct, rps7200.shading, rps7200.uniformity | Drives the scanner itself (calibrate_shading/scan) outside ScanSession. |
| `tools.exposure_headroom` | rps7200.library, rps7200.console, rps7200.direct, rps7200.protocol, rps7200.shading |  |
| `tools probes (byte14_probe, gain_probe, fast_ir_probe, exposure_probe, hold_probe, transport_probe, transport_truth, roll_registration_walk, verify_protocol, filing_load_test, check_scanner)` | rps7200.direct, rps7200.console, rps7200.framing / rps7200.session / rps7200.uniformity / rps7200.usb_transport (varies) | Bypass paths: send commands directly and call session_start(), which no production path calls. |
