# Decode, direction, shading and debug filing

Area key `decode-and-debug-filing`. 36 findings: 2 critical, 3 high, 15 medium, 14 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area covered: decode and debug filing. I read in full rps7200/direct.py (3686 lines), direction.py, shading.py, defects.py and protocol.py. I followed the calls into library.save/reconstruct/decode_raw, usb_transport._read_payload/_command, the session's filing path (session.py _prescan/_scan/_file/FrameWriter._write), demo.py (decode, scan, prescan, _read_as_carriage) and the tools that rely on debug filing.


What works:
- The decode itself is sound: index-format lines, a 2-byte tag, samples as little-endian uint16 or uint8.
- read_direction takes each pass's direction from its own line tags, and a pass read bottom-up is turned upright in decode_index. The raw bytes stay untouched.
- The per-pass CCD mask is read on every pass and filed.
- Exposure never wraps past 16 bits: Settings.scaled clamps it to [100, 65535].

What does not work (the owner's "exact bit data" requirement):
1. Debug filing takes the raw bytes from `last_raw`. That is only written when keep_raw=True and is never cleared. A debug-filed pass can therefore carry no bytes at all, or another pass's bytes. keep_raw=False is the default of scan()/prescan() and of the README example, and nothing checks the bytes against the image.
2. When filing fails, the spool is deleted anyway. If close() never runs, the spool is orphaned and its meta is lost.
3. The calibration pass's raw bytes are never stored anywhere. Only the host's derived float64 reference (shading.npz) survives, so the light/dark split cannot be redone with newer code.
4. Several things sent to the device are not recorded: MODE SELECT byte14, slide_init_param, the quality bits, light/extra_entries/double_times, the raw GET PARAMETERS bytes, INQUIRY (library.save ignores it) and the capture time.
5. At 7200 dpi the stored pixels are not the plain decode, and nothing records that. Every such entry is then a false alarm in reconstruct.
6. Roll and bracket frames are recorded as exposure_metered=False and carry no metering evidence.
7. Cross-area, but checked here: GUI single scans file the shading-corrected image as scan.tif while the record says raw.

Demo: DemoScanner is not a subclass of DirectScanner and does not match it in this area:
- It returns corrected pixels even when shading=False.
- It never raises ShadingUnavailable (for example at 7200 dpi).
- It has no last_pixels_raw.
- Its auto-exposure numbers are made up.
- Its meta lacks the device-settings fields.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [DDF-01](#decode-and-debug-filing-ddf-01) | critical | data-integrity | Debug filing files a pass with no raw bytes or with a previous pass's raw bytes (stale last_raw) | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [DDF-05](#decode-and-debug-filing-ddf-05) | critical | data-integrity | GUI single scans file the shading-CORRECTED image as scan.tif while the record says raw | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [DDF-02](#decode-and-debug-filing-ddf-02) | high | data-integrity | A filing failure during _debug_flush deletes the only copy of the pass (spool unlinked in finally) | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-03](#decode-and-debug-filing-ddf-03) | high | data-integrity | If close() never runs, every pending debug entry is lost and the spool is orphaned without its meta | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-04](#decode-and-debug-filing-ddf-04) | high | data-integrity | The calibration pass's raw bytes are never stored; the library keeps only the host's derived float64 reference | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [DDF-06](#decode-and-debug-filing-ddf-06) | medium | data-integrity | Commands and parameters that define a pass are not recorded: byte14, slide_init_param, quality bits, gain-offset extras, GET PARAMETERS bytes, INQUIRY | [P09](../problems/P09-record-missing-parameters.md) |
| [DDF-07](#decode-and-debug-filing-ddf-07) | medium | data-integrity | At 7200 dpi scan.tif is not the plain decode, nothing records the realignment, and reconstruct flags every such entry | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [DDF-08](#decode-and-debug-filing-ddf-08) | medium | data-integrity | Roll and bracket frames are recorded as a commanded exposure (exposure_metered=False) and carry no metering evidence | [P09](../problems/P09-record-missing-parameters.md) |
| [DDF-09](#decode-and-debug-filing-ddf-09) | medium | error-handling | A pass whose bytes were read but that fails decode or a late check is never filed, losing its raw bytes | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [DDF-10](#decode-and-debug-filing-ddf-10) | medium | data-integrity | The session's shape guard drops a pass's own raw bytes on every short read (and at 7200 dpi) | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [DDF-12](#decode-and-debug-filing-ddf-12) | medium | hardware-safety | calibrate_shading's 300 s deadline can end the read loop mid-pass and silently build a reference from partial data | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [DDF-13](#decode-and-debug-filing-ddf-13) | medium | data-integrity | A reused shading reference is filed as if measured this session; its origin and age are not recorded | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [DDF-14](#decode-and-debug-filing-ddf-14) | medium | data-integrity | The capture time is recorded at spool time and then discarded; debug entries are named and dated by flush time | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-15](#decode-and-debug-filing-ddf-15) | medium | design | The debug spool lives in system temp (often tmpfs/RAM) and grows unbounded until close(); spool failures are swallowed silently | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-17](#decode-and-debug-filing-ddf-17) | medium | doc-mismatch | Metering probes and calibration are never filed by the GUI, scan.py or scan_roll.py, although a comment says probes follow the file-every-scan rule | -- |
| [DDF-22](#decode-and-debug-filing-ddf-22) | medium | demo-divergence | Demo: shading=False still returns corrected pixels, and the demo never raises ShadingUnavailable (e.g. at 7200 dpi) | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DDF-23](#decode-and-debug-filing-ddf-23) | medium | demo-divergence | Demo entries hold corrected pixels as 'raw', lack device-settings meta, and are not marked as demo | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [DDF-25](#decode-and-debug-filing-ddf-25) | medium | doc-mismatch | The documented debug-filing workflow (README example, ad-hoc scripts) produces entries without raw bytes | -- |
| [DDF-26](#decode-and-debug-filing-ddf-26) | medium | doc-mismatch | The comparison files CLAUDE.md mandates do not run the shading correction the pipeline uses | -- |
| [DDF-A1](#decode-and-debug-filing-ddf-a1) | medium | data-integrity | A roll prescan that a correction replaced is never filed, and the docstring's claim that debug filing covers it is false for every front end | [P08](../problems/P08-passes-never-filed.md) |
| [DDF-11](#decode-and-debug-filing-ddf-11) | low | data-integrity | A READ whose payload fully arrived but whose closing status is CHECK is discarded, so raw.bin.gz is not every byte received | -- |
| [DDF-16](#decode-and-debug-filing-ddf-16) | low | data-integrity | Metering probe passes are filed with film='negative' whatever the film is | -- |
| [DDF-18](#decode-and-debug-filing-ddf-18) | low | data-integrity | Bracket and roll membership fields are not in debug entries or in scan.json | -- |
| [DDF-19](#decode-and-debug-filing-ddf-19) | low | data-integrity | decode_index silently drops lines with unknown tags, never checks the second tag byte or the interleave, and does not record per-plane counts | -- |
| [DDF-20](#decode-and-debug-filing-ddf-20) | low | data-integrity | apply_shading leaves columns past the mask's used entries uncorrected without refusing | -- |
| [DDF-21](#decode-and-debug-filing-ddf-21) | low | user-error | Exposure never wraps, but clamping is silent; a NaN scale fails only after calibration | -- |
| [DDF-24](#decode-and-debug-filing-ddf-24) | low | demo-divergence | Demo: the read_direction record can contradict its raw bytes when the source entry was itself read bottom-up | -- |
| [DDF-28](#decode-and-debug-filing-ddf-28) | low | doc-mismatch | Smaller documentation claims in this area that the code contradicts | -- |
| [DDF-29](#decode-and-debug-filing-ddf-29) | low | test-gap | Filing tests check source text and include no-op assertions; the stale-raw path is untested | -- |
| [DDF-30](#decode-and-debug-filing-ddf-30) | low | dead-code | Dead code in this area: defects._smooth and calibrate_shading(keep_data) | -- |
| [DDF-A2](#decode-and-debug-filing-ddf-a2) | low | doc-mismatch | The infrared plane is never shading-corrected, and the report's 'uncorrected' count is never surfaced | -- |
| [DDF-A3](#decode-and-debug-filing-ddf-a3) | low | error-handling | A calibration that yields no usable lines replaces the session's good reference with None | -- |
| [DDF-A4](#decode-and-debug-filing-ddf-a4) | low | data-integrity | duration_s includes host-side correction and finish-scan polling, not only device time | -- |
| [DDF-A5](#decode-and-debug-filing-ddf-a5) | low | data-integrity | prescan.tif in roll-frame entries holds shading-corrected 8-bit pixels with no raw bytes and no marker | -- |
| [DDF-27](#decode-and-debug-filing-ddf-27) | info | doc-mismatch | Three conflicting statements about whether SET GAIN OFFSET persists | -- |
| [DDF-A6](#decode-and-debug-filing-ddf-a6) | info | design | Debug filing's default root is a path relative to the current directory | -- |

## Findings in full

<a id="decode-and-debug-filing-ddf-01"></a>

### DDF-01 -- Debug filing files a pass with no raw bytes or with a previous pass's raw bytes (stale last_raw)

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:1518-1532`, `rps7200/direct.py:2653`, `rps7200/direct.py:631-646`, `rps7200/direct.py:679-684`, `rps7200/direct.py:2811`, `rps7200/direct.py:2437`, `rps7200/direct.py:1685`, `rps7200/direct.py:2083-2092`, `rps7200/library.py:187-211`, `tools/byte14_probe.py:182-185`

**Doc claim:** CLAUDE.md 'File every scan in the library, with its raw bytes' and 'DirectScanner files every scan in the library automatically when debug mode is on'; README.md:488-490; library.py:14 'raw.bin.gz the scanner's bytes, exactly as they arrived'

`_debug_capture` spools whatever `self.last_raw` holds, and read_planes only sets that when keep_raw=True. It is never reset per pass. So a debug-filed pass taken with the default keep_raw=False gets one of two things. (a) No raw.bin.gz at all, if no earlier pass kept bytes. (b) The raw bytes and layout of the most recent earlier pass that did keep them, filed as if they were this pass's. Neither _debug_capture nor library.save checks the bytes/layout against the image. The session has a shape guard for this exact failure (session.py:2073-2099), but the debug path has none. Even that guard would not catch a same-shape case such as byte14_probe's final pass, which has the same dpi and frame as the pass before it.

**Evidence (from the code):**

```text
read_planes: `if keep_raw:\n    self.last_raw = blob\n    self.last_raw_layout = {...}` (never cleared otherwise; scan() only resets `self.last_read_direction = None` at 2653). capture_record: `"raw": self.last_raw, "raw_layout": self.last_raw_layout`. _debug_capture: `raw = record.get("raw")\nif raw is not None:\n    raw_path.write_bytes(raw)\n    item["raw_path"] = raw_path\nitem["raw_layout"] = record.get("raw_layout")`. scan() signature: `keep_raw: bool = False`. auto_exposure probes: `self.scan(resolution=resolution, infrared=False, exposure_scale=scales, keep_raw=True)`. library.save writes raw_path to raw.bin.gz with no comparison against `image`. byte14_probe final pass: `scanner.scan(... shading=False, keep_raw=False, byte14=None)`.
```

**Failure scenario:** An ad-hoc script under RPS7200_DEBUG=1 runs `s.scan(resolution=1800, infrared=True, auto_exposure=True)`. The metering probes keep their 300 dpi RGB bytes, and the 1800 dpi RGBI pass does not keep its own. The entry is filed with scan.tif at 1800 dpi RGBI and raw.bin.gz holding the 300 dpi probe. reconstruct reports 'decode CHANGED'. DemoScanner._decode prefers raw bytes, so the demo shows the metering probe instead of the scan. With byte14_probe, the final default-byte14 pass is filed with the previous ladder rung's bytes. The shapes are identical, so the entry decodes to a different exposure of the same frame, and only a sample-level diff reveals it.

**Fix:** In scan(), set `self.last_raw = None; self.last_raw_layout = None` before start_scan, so capture_record can never return a previous pass's bytes. When debug is on, force keep_raw=True: filing a pass without its bytes defeats the purpose of the library. In _debug_capture and library.save, refuse raw whose layout lines_received/width/channels do not decode to image.shape (allowing for the 7200 dpi stagger trim, see DDF-07).

<details><summary>Second reader's check</summary>

read_planes (direct.py:1518-1532) writes last_raw/last_raw_layout only `if keep_raw:`. Nothing clears them: scan() resets only last_read_direction (2653). capture_record (631-646) returns them unconditionally. _debug_capture (679-684) spools whatever is there, and neither _debug_flush nor library.save (187-211) checks it against the image. scan() defaults keep_raw=False (2437). The auto_exposure probes call scan(..., keep_raw=True) (2083-2092). So `scan(auto_exposure=True)` under RPS7200_DEBUG=1 files the last 300 dpi probe's bytes with the main pass. byte14_probe runs with debug from the environment (its docstring shows RPS7200_DEBUG=1). Its final pass at 184 uses keep_raw=False with the same shape, so it is filed with the previous rung's bytes, and no shape check could catch that. There is also a third route: if decode_index raises, last_raw has already been set to the failed pass's blob (1521 runs before the 1533 decode), and a later keep_raw=False pass files those bytes.

</details>

<a id="decode-and-debug-filing-ddf-05"></a>

### DDF-05 -- GUI single scans file the shading-CORRECTED image as scan.tif while the record says raw

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1439-1458`, `rps7200/session.py:1087-1097`, `rps7200/direct.py:2708-2733`, `rps7200/library.py:373-391`

**Doc claim:** CLAUDE.md 'scan.tif in an entry is the decode alone, with no flat-fielding'; README.md:396-397

Every other caller passes the uncorrected pixels (`last_pixels_raw`): _prescan, the roll frames and tools/scan.py. The window's Scan job does not. The library entry therefore holds flat-fielded pixels, with corrections_applied=[], calibration.report set and shading.npz stored. library.corrected() will divide by the reference a second time. reconstruct compares the raw decode with corrected pixels and reports 'decode CHANGED' on every GUI scan. This is the same drift CLAUDE.md records for 26 prescans, now on the main scan path. The raw.bin.gz bytes are still correct, so the pixels can be recovered by migrate-raw, but scan.tif is wrong and exported/Save-As images are double-corrected.

**Evidence (from the code):**

```text
session._scan: `image, meta = self._scanner.scan(... keep_raw=True)` then `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)`, with no raw_image. FrameWriter._write: `raw_image = job.get("raw_image")\nentry = library.save(job["image"] if raw_image is None else raw_image, ...)`. scan() returns the corrected image: `image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`.
```

**Failure scenario:** The operator presses Scan in the window at 1800 dpi. The entry's scan.tif is already shading-corrected. Save As or library.corrected() applies shading again, visibly over-correcting the edge columns, and `make reconstruct` flags the entry, hiding a real decode regression among such false alarms.

**Fix:** In session._scan pass `raw_image=getattr(self._scanner, "last_pixels_raw", None)` to _file, as _prescan does. Add a behavioural test: a Scan job whose fake scanner returns distinct corrected and raw arrays must file the raw one.

<details><summary>Second reader's check</summary>

session._scan (1439-1458) calls `self._file(seq, 0, image, meta, ...)` without raw_image, and `image` is scan()'s corrected return value (direct.py:2733, 2821). FrameWriter._write (1089-1097) then files job["image"]. library.save records corrections_applied=[] (and calibration.skipped is None), so library.corrected (373-391) applies shading a second time, and reconstruct compares a raw decode with corrected pixels. _prescan (1415) and roll frames (1886) pass raw_image; the window's main Scan job does not. The GUI Scan job defaults to shading=True and auto_exposure=True, so every GUI single scan takes this path.

</details>

<a id="decode-and-debug-filing-ddf-02"></a>

### DDF-02 -- A filing failure during _debug_flush deletes the only copy of the pass (spool unlinked in finally)

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:713-753`, `rps7200/direct.py:694-699`, `rps7200/library.py:176-211`, `tests/test_roll.py:790-803`

**Doc claim:** direct.py:697-699 'losing the record is better than losing the session that produced it': the session is already closed at this point, so nothing is protected by losing the record

The spooled image and raw bytes are unlinked in `finally` whether or not library.save succeeded, and the spool directory is then removed. If save fails, for example because the disk is full while gzipping a 1 GB raw file, the destination is not writable, or tifffile raises, the scan's pixels and raw bytes are destroyed. Only a log line remains. That line is printed only when verbose is on or a log_hook exists. library.save also writes non-atomically: scan.tif first, then shading.npz, ccd_mask.bin and raw.bin.gz, and scan.json last. A failure part-way therefore leaves an entry directory without scan.json. entries() skips it, verify never reports it, and a later save in the same second reuses the directory, because the collision check looks only for scan.json.

**Evidence (from the code):**

```text
`try:\n    image = np.load(item["image_path"], mmap_mode="r")\n    entry = library.save(...)\nexcept Exception as exc:\n    self._log(f"debug: could not file scan {n} ({exc})")\nfinally:\n    image = None\n    for key in ("image_path", "raw_path"):\n        ...Path(path).unlink(missing_ok=True)` and afterwards `shutil.rmtree(self._debug_spool, ignore_errors=True)`. The test `test_a_filing_failure_never_breaks_the_session` asserts `s._debug_pending == []` after a failed filing.
```

**Failure scenario:** A debug probe session files eight 3600 dpi RGBI passes. The library disk fills during the third raw.bin.gz. Passes 3-8 each fail with ENOSPC, and each one's spool is deleted in `finally`. Six scanner passes, each several minutes of hardware time, are gone, and the library holds three orphan directories with no scan.json.

**Fix:** Only unlink a spool item after library.save returns. On failure, keep the spool and write a small manifest beside it (meta, raw_layout, reference and mask saved to files) so it can be refiled later. Make library.save write into a temporary directory and rename it into place once scan.json is written.

<details><summary>Second reader's check</summary>

_debug_flush (direct.py:713-753) unlinks image_path and raw_path in `finally` whether or not save succeeded, then rmtree's the spool (757). tests/test_roll.py:790-800 asserts the queue is emptied after a failed filing. library.save writes scan.tif, prescan, shading, mask and raw.bin.gz before scan.json (176-306). The collision check looks only at `(path / "scan.json").exists()` and uses mkdir(exist_ok=True), so a partial directory has no scan.json and a later save in the same second reuses it. The data is lost only when filing fails (disk full, bad root), so I rate it high rather than critical.

</details>

<a id="decode-and-debug-filing-ddf-03"></a>

### DDF-03 -- If close() never runs, every pending debug entry is lost and the spool is orphaned without its meta

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:470-474`, `rps7200/direct.py:666-692`, `rps7200/direct.py:777-788`

**Doc claim:** CLAUDE.md 'Background any scan sequence over ~8 minutes. The harness kills a foreground command at 10 minutes'

Debug filing is deferred to close(). Until then the only durable pieces are NNN-image.npy and NNN-raw.bin in the system temp directory. The meta (resolution, frame, exposure, read_direction, carriage_state, metering), the shading reference, the CCD mask and the raw layout exist only in the Python process. A SIGTERM/SIGKILL, a crash, a harness timeout, a closed terminal or a REPL session that never calls close() loses all of them. The spool files that remain cannot be turned into entries: no tool knows the directory, and the bytes are uninterpretable without their layout. The same happens to items not yet filed when a KeyboardInterrupt lands inside _debug_flush, because only Exception is caught there.

**Evidence (from the code):**

```text
`self._debug_pending: list[dict[str, Any]] = []` holds meta, reference, ccd_mask and raw_layout only in memory; `self._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))`; filing happens only in `close(): ... self._debug_flush()`. There is no atexit/signal handler and no on-disk manifest (grep finds no atexit or other reference to 'rps7200-debug-').
```

**Failure scenario:** CLAUDE.md warns that the harness kills foreground commands at 10 minutes. A probe script under RPS7200_DEBUG=1 that runs 12 minutes of passes is killed at 10. Nothing reaches library/, and /tmp/rps7200-debug-xxxx holds gigabytes of .npy/.bin files with no record of what they were.

**Fix:** Write each item's meta, layout, reference (npz) and mask into the spool directory at capture time, as a small JSON plus files. Add a `tools/library.py refile-spool` command that assembles orphaned spools. Register an atexit handler that flushes, and consider spooling under the library root rather than system temp so leftovers are found.

<details><summary>Second reader's check</summary>

Only close() calls _debug_flush (777-788). No atexit or signal handler exists (grep finds none). Meta, reference, mask and raw_layout exist only in _debug_pending (in memory). The spool is a mkdtemp in system temp with only NNN-image.npy/NNN-raw.bin. pending is detached from self before the loop (715), so a KeyboardInterrupt (not an Exception) during the flush loses every item not yet filed.

</details>

<a id="decode-and-debug-filing-ddf-04"></a>

### DDF-04 -- The calibration pass's raw bytes are never stored; the library keeps only the host's derived float64 reference

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1895-1973`, `rps7200/direct.py:631-646`, `rps7200/shading.py:75-91`, `rps7200/shading.py:109-182`, `rps7200/library.py:182-183`

**Doc claim:** CLAUDE.md 'its shading reference is acquired per session ... none of it recoverable from a TIFF. A scan saved without them can never be re-decoded or re-corrected'; library.py:8-12 'the calibration travels with the scan'

The per-session calibration is about 1.66 MB of 16-bit tagged lines. It is reduced on the spot by calculate_shading: lines are split by level at the widest ratio gap (split_ratio=5.0), each population is averaged, and a single-point fallback is used below the ratio. The raw bytes are then discarded. The 128-byte calibration-info block, the shading descriptor (get_shading_parms), the block count and the gain/offset written between reads are also discarded. The library's shading.npz holds only the result. So the split, the averaging, and which lines were considered dark or light can never be re-run with newer code, and a truncated or contaminated calibration cannot be recognised afterwards. The docs still treat the two-point versus single-point question as open ('on real scans it measures the same as the single-point form'), and that cannot be revisited per entry.

**Evidence (from the code):**

```text
calibrate_shading: `data = b"".join(collected)\nself._shading = calculate_shading(data, width)` ... `"data": data if keep_data else None` (no caller anywhere passes keep_data=True). capture_record returns `"reference": self._shading` only. ShadingReference.save stores `ref{c}`, `mean{c}`, `dark{c}`, `darkmean{c}` (per-column averages) plus pixels_per_line.
```

**Failure scenario:** The calibration loop ends early (see DDF-12) or a transitional line lands on the wrong side of the level cut. calculate_shading builds its reference from dark lines, or from a light average contaminated by a transitional line. Every entry filed in that session carries that reference, and nothing in shading.npz or scan.json (no line counts, no split level, no raw lines) shows it or lets it be recomputed.

**Fix:** Keep the raw calibration bytes (plus bpl, width, descriptor, calibration_info and block count) on the scanner as `last_calibration`. File them with each entry, as calibration.bin.gz once per session (shared or duplicated), next to shading.npz. Have library.corrected() optionally rebuild the reference from them with the current calculate_shading.

<details><summary>Second reader's check</summary>

calibrate_shading (1946-1973) joins the collected bytes, calls calculate_shading and returns `"data": data if keep_data else None`. No caller passes keep_data. The 128-byte calibration_info is only logged (`info[:12]`), and the shading descriptor and block count are not kept. capture_record exposes only the ShadingReference, and shading.npz stores only the averaged ref/dark arrays (shading.py:75-91). The level split (shading.py:151-172) therefore cannot be re-run on any library entry.

</details>

<a id="decode-and-debug-filing-ddf-06"></a>

### DDF-06 -- Commands and parameters that define a pass are not recorded: byte14, slide_init_param, quality bits, gain-offset extras, GET PARAMETERS bytes, INQUIRY

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2423-2441`, `rps7200/direct.py:2613-2647`, `rps7200/direct.py:2760-2804`, `rps7200/direct.py:1357-1374`, `rps7200/direct.py:1387-1396`, `rps7200/direct.py:728`, `rps7200/library.py:142`, `rps7200/library.py:237-264`, `tools/byte14_probe.py:142-150`, `tools/verify_protocol.py:147-186`

**Doc claim:** library.py:16 'scan.json every setting, the device state, and the provenance'; CLAUDE.md 'Bump PROTOCOL_REVISION ... when the commands sent to the device change'

The record keeps resolution, frame, depth, channels, exposure/gain/offset, fast_infrared, filter_offsets, read_direction and carriage_state. It does not keep the MODE SELECT payload: byte14 (which decides where the carriage waits and so the next pass's direction), the quality word or halftone/threshold. It also misses the SLIDE INIT param, the full SET GAIN/OFFSET payload, the raw GET PARAMETERS response and the device INQUIRY (firmware, model, CCD geometry). PROTOCOL_REVISION does not change when a caller overrides byte14 or slide_init_param. So entries from the byte14 ladder, fast_ir_probe (byte14=BYTE14_REHOME throughout) and verify_protocol stages 3-5 cannot be told apart from ordinary passes in the library. The value exists only in the probe's own JSON.

**Evidence (from the code):**

```text
scan() uses `skip_shading=skip_shading, byte14=byte14, fast_infrared=fast_infrared` and `self.slide(SLIDE_INIT, param=slide_init_param)`, but meta has no byte14/slide_init_param/skip_shading/quality key. set_gain_offset writes `data[15] = s.light`, `data[16] = ... extra_entries`, `data[17] = s.double_times`, `data[27] = 1`, while meta records only `"exposure", "gain", "offset"`. get_parameters drops `available_lines` and bytes 8-13/16-17. library.save takes `inquiry: Any = None` and never uses it; _debug_flush passes `inquiry=self._inquiry`.
```

**Failure scenario:** A byte14_probe ladder is filed under RPS7200_DEBUG=1. Months later someone wants ms/line against byte14 from the library, as docs/byte14-plan.md proposes ('from the library's own duration_s and height'). Every entry reports the same resolution and frame, and nothing says which byte14 it was taken with.

**Fix:** Record the exact command payloads per pass in meta, as hex strings: MODE SELECT, SET GAIN/OFFSET, SCAN FRAME, SLIDE INIT, and the raw GET PARAMETERS and INQUIRY responses. Have library.save persist them under a `commands` block. Actually use the `inquiry` argument (asdict) in the record.

<details><summary>Second reader's check</summary>

Verified. scan() takes byte14, slide_init_param and skip_shading (2437-2440) and none of them appears in meta (2760-2804). set_gain_offset writes light, extra_entries, double_times and byte 27 (1363-1369), while meta keeps only exposure/gain/offset. get_parameters drops bytes 8-13 and 16-17. library.save accepts `inquiry` (142) and never references it. fast_ir_probe (216, 456) and verify_protocol stages 3-5 (147-186) pass non-default byte14/slide_init_param. Re-decoding does not need these values (the direction is recorded from the line tags), so this is an evaluation and provenance gap, not a decode gap. Medium rather than high.

</details>

<a id="decode-and-debug-filing-ddf-07"></a>

### DDF-07 -- At 7200 dpi scan.tif is not the plain decode, nothing records the realignment, and reconstruct flags every such entry

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/library.py:411-436`, `rps7200/library.py:464-497`, `rps7200/direct.py:2567-2580`

**Doc claim:** CLAUDE.md 'scan.tif in an entry is the decode alone'; README.md:396

A native 7200 dpi pass (possible only with shading=False: tools/scan.py --no-shading, or a debug-filed script) is stored in scan.tif stagger-realigned and 4 rows short. Nothing records this, and reconstruct/decode_raw do not reproduce it, so every such entry is a permanent 'decode CHANGED' false alarm. Through ScanSession the shape guard would also drop the bytes, but the window cannot produce an unshaded 7200 dpi pass.

**Evidence (from the code):**

```text
`if resolution == self.NATIVE_COLUMN_STAGGER_DPI:\n    image = self._realign_native_column_stagger(image)` happens before `raw_pixels = image`; meta records only `"height": int(image.shape[0])`. reconstruct: `image, direction = DirectScanner.decode_index(raw, params, ...)` then `if image.shape != stored.shape: return image, f"decode CHANGED: ..."`. The session guard compares `layout["lines"]` (params.lines) with `image.shape[0]`.
```

**Failure scenario:** `tools/scan.py --dpi 7200 --no-shading` files an entry. `make reconstruct` reports it as a changed decode every run. A real decode regression elsewhere is then easier to dismiss as 'the 7200 ones again'.

**Fix:** Store the plain decode in scan.tif and apply the stagger realignment in library.corrected()/the delivered image. Alternatively, record `realigned: {"stagger_lines": 4}` in meta and have decode_raw/reconstruct apply the same function when that is present.

<details><summary>Second reader's check</summary>

The core claim is true. At 7200 dpi scan() realigns and trims 4 rows before `raw_pixels = image` (2695-2708), no meta field records it, and decode_raw/reconstruct (411-497) never apply it, so reconstruct reports `decode CHANGED: now (H..), stored (H-4..)`. It is reachable only with shading=False, via tools/scan.py --no-shading or a debug script, because shading=True at 7200 dpi raises before the pass (2567-2580). The session-guard sub-claim is unreachable from the window: the GUI never builds a Scan job with shading=False, so a 7200 dpi GUI pass raises ShadingUnavailable before any filing.

</details>

<a id="decode-and-debug-filing-ddf-08"></a>

### DDF-08 -- Roll and bracket frames are recorded as a commanded exposure (exposure_metered=False) and carry no metering evidence

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:3615-3652`, `rps7200/direct.py:2373-2401`, `rps7200/direct.py:2791`, `rps7200/direct.py:2805-2809`, `rps7200/direct.py:358-361`, `rps7200/session.py:893`, `rps7200/library.py:678-684`

**Doc claim:** direct.py:358-361 'DirectScanner.last_metering records blue's achieved level on every metered scan, so these become checkable from ordinary work'; direct.py:2033-2035

Metering happens outside scan() in both scan_roll and scan_bracket, so the frame's scan() sees auto_exposure=False. Every metered roll frame, which is every frame of a default GUI roll, is filed with `exposure_metered: false` and without the `metering` block (probe levels, targets, blue headroom, ceilings hit). library.signature() then treats the metered scales as a commanded exposure. The docstrings state the metering record is filed with every metered scan, precisely so that BLUE_RGBI_HEADROOM can be checked from ordinary work. Rolls are that ordinary work and file none of it.

**Evidence (from the code):**

```text
scan_roll: `scales = self.auto_exposure(target=exposure_target, infrared=infrared, film=film)` then `image, meta = self.scan(..., exposure_scale=scales, ...)` with auto_exposure left False. scan(): `"exposure_metered": bool(auto_exposure)` and `if auto_exposure and self.last_metering is not None: meta["metering"] = self.last_metering`. The window's Roll defaults to `meter: str = METER_EACH`.
```

**Failure scenario:** A 38-frame colour-negative RGBI roll is scanned from the window. None of the 38 entries records what the probe measured or whether blue hit the timer ceiling. BLUE_RGBI_HEADROOM cannot be re-checked from them, and each is labelled 'commanded' although every exposure was metered.

**Fix:** Pass `metering=self.last_metering` (and exposure_metered=True) into scan() from scan_roll/scan_bracket, or have scan() accept a `metered_by` argument. Alternatively, attach last_metering to the meta in scan_roll right after scan() returns, and have library.save persist it (it already does for meta['metering']).

<details><summary>Second reader's check</summary>

scan_roll meters with self.auto_exposure(...) and then calls self.scan(..., exposure_scale=scales) without auto_exposure (3622-3652), so scan() records exposure_metered=False and attaches no `metering` (2791, 2805-2809). scan_bracket does the same (2373-2401). Roll.meter defaults to METER_EACH (session.py:893), and session.py never adds metering. The claim is real, but it concerns missing evidence and a mislabel, not pixels, and the extra signature component only makes duplicates() more conservative. Medium.

</details>

<a id="decode-and-debug-filing-ddf-09"></a>

### DDF-09 -- A pass whose bytes were read but that fails decode or a late check is never filed, losing its raw bytes

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/direct.py:1517-1533`, `rps7200/direct.py:1582-1593`, `rps7200/direct.py:2656-2687`, `rps7200/direct.py:2719-2756`, `rps7200/direct.py:2811`, `rps7200/direct.py:3666-3675`

**Doc claim:** library.py:21-26 'raw.bin.gz is the ground truth ... The decode is not settled'

Debug capture, last_pixels_raw and the tools' filing all run only after a successful decode and correction. If decode_index rejects the data (an unexpected channel count, no recognisable tags), or the stagger realignment raises ValueError, or the post-pass width check raises ShadingUnavailable, a pass that cost minutes of scanner time is discarded. That includes its complete raw byte stream, which is exactly the evidence a future decoder would need. The library docstring calls raw.bin.gz the ground truth because 'the decode is not settled'. Yet a pass the current decoder cannot handle is the one pass that is never kept.

**Evidence (from the code):**

```text
read_planes sets `self.last_raw = blob` and then calls `image, direction = self.decode_index(blob, params, channels)`, which raises `ScanReadError(f"expected {channels} channels ... but the scanner produced {len(order)}")`. scan(): `except BaseException: self._scanning = False; raise`. The post-pass `raise ShadingUnavailable(...)` paths come before `self._debug_capture(raw_pixels, meta)`. scan_roll catches `(UsbError, ScanReadError, ..., ValueError)` and yields `RollFrame(..., error=str(exc))` with no bytes.
```

**Failure scenario:** Firmware sends an RGBI pass whose I lines carry an unexpected tag. decode_index raises 'expected 4 channels ... produced 3'. The roll logs the frame as failed and moves on. The 600 MB of bytes that show the new tag are dropped, and the next kept pass overwrites last_raw.

**Fix:** When debug is on (or keep_raw is set), spool the blob and layout inside read_planes before decode. On a decode or late-check failure, file a 'failed' entry: raw.bin.gz plus meta and error, no scan.tif, tagged 'decode-failed'. Let reconstruct retry these entries.

<details><summary>Second reader's check</summary>

decode_index raises ScanReadError on a channel-count mismatch or missing tags (1576-1587). _realign_native_column_stagger raises ValueError, and the post-pass ShadingUnavailable check (2719-2731) raises before _debug_capture (2811). scan_roll's except (3666-3675) yields a RollFrame with no bytes. The blob is lost, except that it lingers in last_raw (set at 1521 before decode), where it can then be filed against a later pass (see DDF-01).

</details>

<a id="decode-and-debug-filing-ddf-10"></a>

### DDF-10 -- The session's shape guard drops a pass's own raw bytes on every short read (and at 7200 dpi)

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/session.py:2073-2099`, `rps7200/direct.py:1501-1503`, `rps7200/direct.py:1522-1532`, `rps7200/direct.py:1595`

**Doc claim:** session.py:2089-2094 comment says the guard exists to reject a *previous* pass's bytes

The guard compares the image height with the declared line count (params.lines), not with what was received. Any pass that ends early through EndOfData, which read_planes treats as normal, decodes to fewer rows than params.lines. The guard then declares the bytes 'do not describe this image' and files the entry without raw.bin.gz, although the bytes are exactly this pass's. The same applies to the 4-row-shorter 7200 dpi realigned image. A short read is precisely the anomaly the raw bytes should be kept for.

**Evidence (from the code):**

```text
Guard: `actual = {"lines": shape[0], ...}` compared with `layout[k]`, where layout is `"lines": int(params.lines)`. read_planes: `except EndOfData: self._log(f"end of data at {got}/{total_lines} lines"); break`. decode: `height = min(len(planes[c]) for c in order)`. The layout also records `"lines_received": len(blob) // (...)`, but the guard ignores it.
```

**Failure scenario:** A 3600 dpi GUI scan ends 12 lines early with ASC 0x20. The window files the entry, logs 'raw bytes do not describe this image (lines 5172 vs 5160)', and drops the raw bytes, so the short read can never be investigated.

**Fix:** Compare against `lines_received // channels` and channel count rather than params.lines. Better, re-decode the bytes (decode_index is cheap relative to a pass) and compare with the raw pixels, including the known stagger trim.

<details><summary>Second reader's check</summary>

The session guard (2073-2099) compares layout['lines'] (int(params.lines), 1525-1530) with image.shape[0]. read_planes treats EndOfData as a normal end (1501-1503), and decode truncates to the shortest plane (1589). So any short read disagrees with the layout, and the pass's own bytes are dropped. lines_received is recorded but ignored by the guard. The 7200 dpi half of the claim is unreachable through the window (see DDF-07).

</details>

<a id="decode-and-debug-filing-ddf-12"></a>

### DDF-12 -- calibrate_shading's 300 s deadline can end the read loop mid-pass and silently build a reference from partial data

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `rps7200/direct.py:1915-1944`, `rps7200/direct.py:1946-1959`

**Doc claim:** CLAUDE.md 'Never abandon a read mid-scan'

There are three problems. (1) If the deadline passes while the device is still producing calibration lines, the loop exits without any log line. It then issues SCSI COPY and READ_STATE polls with the calibration still running, which is the 'abandoned read mid-scan' pattern CLAUDE.md ties to wedges. (2) Any ScanReadError, not only end-of-data, is logged as 'scanner finished' and ends calibration. (3) In both cases calculate_shading builds a reference from whatever was collected. If the lit phase was cut, the level split sees only dark lines (ratio < split_ratio) and averages them as the light reference. The dark-current pattern (12-15% column variation) is then applied as a flat field, and no field in the reference or meta records the block count or that the pass was cut short. Tools describe calibration as '3-4 minutes' in total, so the margin to 300 s is not established.

**Evidence (from the code):**

```text
`deadline = time.monotonic() + timeout` (timeout=300.0) `while time.monotonic() < deadline: ... except (EndOfData, ScanReadError): self._log(f"  scanner finished after {blocks} blocks"); break` then `mask = self.get_ccd_mask(width)` and `finally: self.finish_scan()`; `self._shading = calculate_shading(data, width)`.
```

**Failure scenario:** A slow calibration (a cold lamp after wait_warm) is still delivering lit lines at 300 s. The loop exits, the mask read and state polls go to a busy device (a risk of wedging), and every scan in the session is 'corrected' with a partial reference, with nothing recorded to show it.

**Fix:** Treat an exit on the deadline as an error rather than a completion, and keep reading while data keeps arriving (an idle-based timeout, like read_planes). Only accept EndOfData as 'finished'. Record blocks, lines per channel, the split level and whether the pass completed in the ShadingReference, and file them.

<details><summary>Second reader's check</summary>

The loop is `while time.monotonic() < deadline` (1917) and simply falls out at the deadline with no log. It then reads the CCD mask and runs finish_scan. `except (EndOfData, ScanReadError)` logs 'scanner finished' for any refusal (1928-1930). calculate_shading uses whatever was collected: with only dark lines the ratio stays below split_ratio and they are averaged as the light reference (shading.py:153-174). No block or line count is kept on the reference.

</details>

<a id="decode-and-debug-filing-ddf-13"></a>

### DDF-13 -- A reused shading reference is filed as if measured this session; its origin and age are not recorded

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:549-561`, `rps7200/direct.py:572-629`, `rps7200/direct.py:563-570`, `rps7200/session.py:1383-1387`, `tools/gui.py:1408-1412`, `rps7200/library.py:281-296`

**Doc claim:** shading.py:47-50 'Outlives a single scan but not the session'; CLAUDE.md 'its shading reference is acquired per session'

The window, tools/scan.py --reuse and tools/scan_roll.py --reuse can load calibration/shading.npz from an earlier session, possibly days old and from another power-on. Every entry then stores that reference as its shading.npz. Nothing in the entry says it was loaded rather than measured, from which file, or when it was measured. The file itself carries no timestamp or lamp state. The docs say the reference describes one power-on's sensor state and that exposure matters to it. An entry cannot be judged later if it does not say which kind of reference it holds. The single shared cache file is also overwritten in place, so a crash mid-write leaves a truncated npz that the next reuse loads or fails on.

**Evidence (from the code):**

```text
`if reuse and path.exists(): reference = self.load_shading(path)`; load_shading: `self._shading = ShadingReference.load(Path(path))`; save_shading writes `calibration/shading.npz` with np.savez_compressed (overwritten, non-atomic). The GUI offers `("reuse", "reuse the cached reference")`. The library record's calibration block has shading/ccd_mask/pixels_per_line/light_mean/report/skipped and no source or time.
```

**Failure scenario:** The operator ticks 'reuse the cached reference' after a power cycle to save 3-4 minutes. Thirty frames are filed with a reference measured under a different lamp warm-up. A later analysis of residual striping cannot tell these entries from correctly calibrated ones.

**Fix:** Store provenance inside ShadingReference (measured_at, session id, calibration resolution, block count, source path when loaded). Record `calibration.source = measured|loaded` and the age in scan.json. Write the cache atomically (temp file plus os.replace).

<details><summary>Second reader's check</summary>

ensure_shading(reuse=True) calls load_shading (549-561). The GUI offers 'reuse the cached reference' (gui.py:1408-1412), and scan.py/scan_roll.py offer --reuse. ShadingReference has no provenance field. library.save's calibration block has no source or time. save_shading writes np.savez_compressed straight onto the shared cache path, which is not atomic.

</details>

<a id="decode-and-debug-filing-ddf-14"></a>

### DDF-14 -- The capture time is recorded at spool time and then discarded; debug entries are named and dated by flush time

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:672`, `rps7200/direct.py:719-729`, `rps7200/library.py:166-168`, `tools/linearity.py:48-51`

**Doc claim:** README.md 'The library entry keeps its own timestamped id, which is what makes it findable years later'

All entries from a debug session get ids and `created` stamps from when the flush ran after close(). That can be minutes or hours after each pass. The true capture time, which _debug_capture records, is thrown away, and scan.json holds only duration_s. Relating an entry to a log line, a probe's JSON, lamp warm-up or a transport event by time becomes impossible. tools/linearity.py already had to work around this by joining on exposure triples.

**Evidence (from the code):**

```text
`item: dict[str, Any] = {"meta": dict(meta), "captured": time.time()}`, but `captured` is never passed on. library.save: `when = datetime.now(timezone.utc)` is used for both id and `created`. tools/linearity.py: 'entries are filed after close() and named from flush time, so their order says nothing about capture order'.
```

**Failure scenario:** A two-hour debug roll of 38 frames is filed at the end. Every entry's created time is within the last few minutes. Drift against time since warm-up cannot be measured from the library, and two frames metering to the same exposure triple cannot be told apart (linearity.py reports them as 'ambiguous').

**Fix:** Add a `captured` field to meta in scan() (UTC ISO timestamp at start_scan, plus the end time). Have library.save accept `when=` so that debug and deferred filing name entries by capture time.

<details><summary>Second reader's check</summary>

_debug_capture stores `"captured": time.time()` (672) and _debug_flush never passes it on (719-729). library.save uses datetime.now() for both the id and `created` (166-168). tools/linearity.py:48-51 documents the workaround. Entries are flushed in capture order, so their relative order survives, but the absolute capture time is lost.

</details>

<a id="decode-and-debug-filing-ddf-15"></a>

### DDF-15 -- The debug spool lives in system temp (often tmpfs/RAM) and grows unbounded until close(); spool failures are swallowed silently

**Severity** medium · **Category** design · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:666-692`, `rps7200/direct.py:509-519`, `rps7200/direct.py:740-746`

**Doc claim:** README.md:500-504 'spooled rather than held in memory'; direct.py:742-746

Every pass of a session stays in the spool until close(), so the peak spool is the whole session: 1.1 GB per 7200 dpi frame, 43 GB for a 38-frame roll. tempfile.mkdtemp uses TMPDIR or /tmp, which is tmpfs (RAM-backed, about 50% of RAM) on Fedora, Arch and many other Linux systems. On those machines spooling still consumes RAM, the thing it was meant to avoid, and hits ENOSPC early. A failed np.save or write_bytes is caught and logged. With verbose=False and no log_hook, which is how transport_truth.py, verify_protocol.py and filing_load_test.py construct the scanner, nothing is printed and the pass is simply not filed. The flush comment about freeing per frame to avoid a '43 GB peak' does not apply to the capture phase, where the peak already occurs.

**Evidence (from the code):**

```text
`self._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))`; `except Exception as exc: self._log(f"debug: could not spool this scan ({exc})")`; `_log` prints only `if self.verbose` or through log_hook.
```

**Failure scenario:** On a laptop with 16 GB RAM and /tmp on tmpfs, a debug session of several 3600 dpi RGBI passes fills /tmp at the fifth pass. The spool write fails and is swallowed (verbose=False), and passes 5 onwards never reach the library. Nobody is told.

**Fix:** Spool under the library root, for example `<root>/.spool/`, on the disk the entries will occupy. Check free space before spooling. Make spool and flush failures loud regardless of verbose: print to stderr and record them in a sentinel file in the spool.

<details><summary>Second reader's check</summary>

The spool is tempfile.mkdtemp (669) and holds every pass until close(). The spool except (690-691) only calls _log, which prints only if verbose or log_hook is set. transport_truth.py:112, verify_protocol.py:1394 and filing_load_test.py:81 construct with verbose=False. Whether /tmp is tmpfs depends on the host, but the unbounded growth and the silent failure are real.

</details>

<a id="decode-and-debug-filing-ddf-17"></a>

### DDF-17 -- Metering probes and calibration are never filed by the GUI, scan.py or scan_roll.py, although a comment says probes follow the file-every-scan rule

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2083-2092`, `rps7200/session.py:1265-1271`, `tools/scan.py:157-160`, `tools/scan_roll.py:344-353`

**Doc claim:** direct.py:2087-2091; CLAUDE.md 'File every scan in the library, with its raw bytes'

keep_raw=True on the probe only fills last_raw. The probe is filed only in debug mode, which all three production front ends switch off explicitly. So in normal use neither the probe pixels/bytes nor the calibration pass (see DDF-04) ever reach the library. Only the rounded summary in meta['metering'] survives, and for rolls not even that (DDF-08).

**Evidence (from the code):**

```text
The comment in auto_exposure reads 'CLAUDE.md's rule is "file every scan, with its raw bytes", without an exception for throwaway passes -- and a metering probe is only throwaway until someone asks what it saw.' Every production path constructs `DirectScanner(..., debug=False)`, and only `_debug_capture` would file a probe.
```

**Failure scenario:** A roll's exposure comes out wrong. There is no stored probe to re-meter offline, only the rounded levels, and for roll frames not those either.

**Fix:** Either file probes from the front ends (the session could file them tagged 'metering-probe' through the same writer), or correct the comment and the docs to say probes are filed only with RPS7200_DEBUG=1.

<details><summary>Second reader's check</summary>

session._default_scanner (1265-1271), tools/scan.py:160 and tools/scan_roll.py:353 all force debug=False, so neither probes nor calibration are ever filed in production. The auto_exposure comment (2087-2091) presents keep_raw=True as satisfying 'file every scan', but keep_raw only fills last_raw, and the main pass overwrites it. RPS7200_DEBUG=1 has no effect on the window at all.

</details>

<a id="decode-and-debug-filing-ddf-22"></a>

### DDF-22 -- Demo: shading=False still returns corrected pixels, and the demo never raises ShadingUnavailable (e.g. at 7200 dpi)

**Severity** medium · **Category** demo-divergence · **Verdict** partly · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:530-597`, `rps7200/demo.py:1003-1010`, `rps7200/direct.py:2560-2580`, `tools/gui.py:118`

**Doc claim:** CLAUDE.md 'A refusal belongs in the stand-in's answers'; README.md:221-224 'refuses what the device refuses'

DemoScanner.scan does not apply DirectScanner's pre-pass refusal (needed columns > MAX_SHADING_COLUMNS), so a 7200 dpi Scan from the window succeeds in the demo while the hardware raises ShadingUnavailable. The failed-job path for that refusal is never exercised by run-demo. In addition, the demo ignores shading=False (it always corrects and never records SHADING_SKIPPED_EXPLICIT), a divergence that the window's own jobs never trigger.

**Evidence (from the code):**

```text
demo._decode: `if reference is not None and not (record.get("calibration") or {}).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)` regardless of the scan's `shading` argument. demo.scan meta: `"shading": self._shading_report if shading else None`, with no `shading_skipped`. The real scan raises `ShadingUnavailable` when `needed > self.MAX_SHADING_COLUMNS` and returns raw with `shading_skipped = SHADING_SKIPPED_EXPLICIT` for shading=False.
```

**Failure scenario:** Someone checks in `make run-demo` that 7200 dpi from the window fails cleanly. The demo happily delivers a 7200 dpi picture, so a broken failed-job path for ShadingUnavailable goes unnoticed until the hardware refuses.

**Fix:** Take the checks from DirectScanner rather than retyping them: factor the pre-pass shading decision (_shading_columns_needed against MAX_SHADING_COLUMNS) into a static method both classes call. In the demo, honour shading=False by returning the uncorrected decode with SHADING_SKIPPED_EXPLICIT.

<details><summary>Second reader's check</summary>

Real: demo.scan never checks _shading_columns_needed against MAX_SHADING_COLUMNS, and the window offers 7200 dpi (gui.py:118 DPI_LADDER, 1749). A 7200 dpi GUI scan therefore raises ShadingUnavailable on hardware but succeeds in the demo. demo._decode applies shading whenever a reference exists (demo.py:1009-1010), regardless of the shading argument, and records no shading_skipped. The shading=False sub-claim is unreachable from the window, though: the GUI never builds Scan(shading=False) and Roll does not pass shading, so it matters only to non-GUI callers of the demo.

</details>

<a id="decode-and-debug-filing-ddf-23"></a>

### DDF-23 -- Demo entries hold corrected pixels as 'raw', lack device-settings meta, and are not marked as demo

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:176`, `rps7200/demo.py:553-596`, `rps7200/demo.py:15-19`, `rps7200/session.py:1415`, `rps7200/library.py:237-261`, `tools/gui.py:8120`, `tools/gui.py:8177-8181`

**Doc claim:** demo.py:18-19 'An entry the demo files reconstructs like any other'; CLAUDE.md 'The demo is the real software with different inputs'

Pixels come from _decode, which applies shading. capture_record hands over the source entry's raw bytes (uncorrected), and the session files the corrected pixels (DDF-05 on _scan; _prescan gets last_pixels_raw=None through getattr). A demo entry therefore stores corrected pixels next to raw bytes, contradicting the module's claim that 'An entry the demo files reconstructs like any other'. Its record also lacks the fields a real pass writes, and auto-exposure is a fixed string rather than DirectScanner.auto_exposure. Only carriage_state.modelled hints that it is synthetic. With `--demo --library library`, which is accepted, these entries go into the real library looking genuine.

**Evidence (from the code):**

```text
`class DemoScanner:` does not subclass DirectScanner and has no `last_pixels_raw`. demo.scan meta contains resolution/channels/film/depth/width/height/shading/exposure_scale/duration/`"demo": True`, with no exposure/gain/offset/protocol_revision/frame/bytes_per_line/metering. The fake metering is `self._log("auto-exposure: [1.82, 0.94, 2.11, 1.0]")`. library.save persists neither `demo` nor any tag for it. gui: `root=args.library or str(home / "library")`.
```

**Failure scenario:** `uv run python tools/gui.py --demo --library library`, then a few scans. The real library gains entries whose scan.tif is corrected and whose raw.bin.gz is another entry's bytes (possibly reversed by the demo). reconstruct flags them and duplicates() may group them with the real source entry.

**Fix:** Give DemoScanner `last_pixels_raw` (the pre-shading decode) and a meta built by the same helper DirectScanner uses. Have library.save persist a `demo` flag and add a 'demo' tag. Refuse `--demo` with a --library that resolves to the real library.

<details><summary>Second reader's check</summary>

DemoScanner has no last_pixels_raw, so session._prescan's getattr returns None and _scan passes none. The corrected pixels from _decode (apply_shading) are filed, while capture_record hands over the source entry's raw bytes, which the session's shape guard does not reject when shapes match. The demo meta lacks exposure/gain/offset/protocol_revision/frame/bytes_per_line. library.save's key list drops `demo`. --library is honoured under --demo (gui.py:8177-8178). The default root is demo/library, so polluting the real library takes an explicit flag.

</details>

<a id="decode-and-debug-filing-ddf-25"></a>

### DDF-25 -- The documented debug-filing workflow (README example, ad-hoc scripts) produces entries without raw bytes

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:205-209`, `README.md:486-499`, `rps7200/direct.py:2437`, `rps7200/direct.py:488-491`, `rps7200/direct.py:2812-2815`

**Doc claim:** README.md:185-187 'Every scan is filed ... with the raw bytes the scanner sent'

The README's own Python example, run with RPS7200_DEBUG=1, files an entry with no raw.bin.gz, because keep_raw defaults to False. With a preceding kept pass, it files wrong bytes (DDF-01). The comments describe last_raw as 'the last pass's bytes' and its staleness as a thing of the past ('once was'). It is still only written on request and never cleared.

**Evidence (from the code):**

```text
README: `with DirectScanner() as s:\n    s.calibrate_shading()\n    image, meta = s.scan(resolution=1800, infrared=True)` and 'Anything else -- an ad-hoc script, a probe -- files too if RPS7200_DEBUG=1 is set'. direct.py:488-489: 'The last pass's bytes exactly as the scanner sent them, kept only when asked'. direct.py:2812-2815: 'Set on every pass, so it is never a stale leftover the way `last_raw` once was'.
```

**Failure scenario:** Following README/CLAUDE.md literally (debug on, plain scan()) produces a library entry that cannot be re-decoded, while the docs say it is filed 'with its raw bytes'.

**Fix:** Fix the code (DDF-01), or change the example to `keep_raw=True` and state the requirement in README/CLAUDE.md. Correct the two comments.

<details><summary>Second reader's check</summary>

The README Python example (205-209) calls scan() with keep_raw left at its default False. README 486-499 says ad-hoc scripts 'file too' under RPS7200_DEBUG=1. The direct.py:488-491 comment says 'kept only when asked', and 2812-2815 claims staleness is historical ('the way last_raw once was'), yet last_raw is still never cleared (see DDF-01).

</details>

<a id="decode-and-debug-filing-ddf-26"></a>

### DDF-26 -- The comparison files CLAUDE.md mandates do not run the shading correction the pipeline uses

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/make_comparison.py:93-125`, `rps7200/defects.py:154-234`

**Doc claim:** CLAUDE.md 'Regenerate the three comparison files after any significant change to the scan or correction path'

CLAUDE.md says to regenerate 1_nothing_done/2_corrected/3_corrected_inverted after any change to the scan or correction path, because Stefan judges by eye. The tool applies only defects.destripe to whatever TIFF it is given. destripe is used nowhere in the scan or library path; it is referenced only by this tool. So a change to apply_shading, calculate_shading or the decode is not reflected in '2_corrected.tif' unless the caller happens to pass an already-corrected file. Given a library scan.tif (raw), it shows destripe-only output that the pipeline never produces. destripe also casts with `.astype(image.dtype)`, which truncates only in the corrected columns: a -0.5 LSB bias per corrected column, visible at 8 bits.

**Evidence (from the code):**

```text
make_comparison: `raw = tiff.read(scan_path)` ... `corrected = destripe(raw, defects, margin=12, dilate=5)` then `tiff.write("2_corrected.tif", corrected, ...)`. Nothing calls apply_shading or library.corrected. The defaults are `scans/negatives/state_1800dpi.tif` and `scans/flat/flat_clearfilm_3600dpi.tif`.
```

**Failure scenario:** After changing calculate_shading, the three files are regenerated from a library entry's scan.tif. They show destripe on raw pixels, identical before and after the change, and Stefan is shown something that does not include the change.

**Fix:** Build 2_corrected.tif with library.corrected(entry) (today's correction code) from a library entry id. Keep destripe as an optional extra stage. Round with floor(x+0.5) in destripe, as apply_shading does.

<details><summary>Second reader's check</summary>

make_comparison.py (93-125) reads a TIFF, runs defects.destripe and writes 2_corrected.tif. It never calls apply_shading or library.corrected. destripe is only re-exported by direct.py (32, 243) and used by this tool alone. destripe converts back with `.astype(image.dtype)` after np.clip (234), which truncates the modified columns; untouched columns round-trip exactly.

</details>

<a id="decode-and-debug-filing-ddf-a1"></a>

### DDF-A1 -- A roll prescan that a correction replaced is never filed, and the docstring's claim that debug filing covers it is false for every front end

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:1841-1853`, `rps7200/session.py:2051-2060`, `rps7200/session.py:1265-1271`, `tools/scan_roll.py:344-353`

**Doc claim:** rps7200/session.py:2057-2060 'Under RPS7200_DEBUG=1 that picture already has a correct entry anyway'; CLAUDE.md 'File every scan in the library'

The 'before' prescan of a frame the hold/correction loop moved is a real scanner pass. It is written only as a corrected TIFF in rolls/ (no library entry, no raw bytes, no raw pixels). The justification is that debug filing already filed it. ScanSession and tools/scan_roll.py both hard-code debug=False, so RPS7200_DEBUG=1 is ignored and that pass reaches the library in no form.

**Evidence (from the code):**

```text
_file docstring: "Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway, filed at the instant it was taken". The call is `self._file(seq, number, rf.prescan_before, ..., path=out / f"prescan{number:02d}-before.tif", roll=name, file_entry=False)`, while _default_scanner builds `DirectScanner(verbose=self.verbose, debug=False)`.
```

**Failure scenario:** The operator runs a roll from the window with RPS7200_DEBUG=1, as CLAUDE.md instructs. A frame is corrected. The prescan showing where the frame arrived exists only as a shading-corrected prescanNN-before.tif, with no raw bytes and no reference, so it cannot be re-decoded or re-corrected, which is the evidence the comment says is kept.

**Fix:** File prescan_before as its own library entry with its own raw pixels and capture record, taken at the moment it was scanned (as scan_roll does with raw_prescan). Alternatively, correct the docstring and CLAUDE.md to say the window ignores RPS7200_DEBUG.

<a id="decode-and-debug-filing-ddf-11"></a>

### DDF-11 -- A READ whose payload fully arrived but whose closing status is CHECK is discarded, so raw.bin.gz is not every byte received

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:844-857`, `rps7200/direct.py:1419-1438`, `rps7200/direct.py:1484-1503`

**Doc claim:** library.py:14 'raw.bin.gz the scanner's bytes, exactly as they arrived'

If the device reports CHECK CONDITION after delivering a full READ payload, for example with the end-of-data sense on the final batch, the transport throws those bytes away. read_planes then treats the pass as ended at the previous batch. Those lines are lost from both the image and raw.bin.gz, and nothing records that a payload was received and discarded. Whether the hardware does this is unmeasured. The code path makes 'the scanner's bytes, exactly as they arrived' untrue whenever it does.

**Evidence (from the code):**

```text
_command: `payload = self._read_payload(read_size, timeout_ms)\nfinal = self._wait_not_busy(...)\nif final == UsbStatus.CHECK:\n    raise CheckCondition(command[0])\nreturn payload`. read_lines on CheckCondition reads sense and, with retries=1 from read_planes, raises EndOfData or ScanReadError; read_planes then `break`s on EndOfData.
```

**Failure scenario:** The last 104-line batch of a 1800 dpi pass returns its data followed by CHECK (ASC 0x20). The frame is filed 104/3 rows short, the raw bytes stop at the same point, and the log says 'end of data at N/M lines' as if the scanner had sent nothing more.

**Fix:** Return the payload together with the post-transfer status (for example raise a CheckCondition subclass carrying `payload`). Have read_lines keep those bytes and record the event in the raw layout.

<details><summary>Second reader's check</summary>

usb_transport._command (844-857) reads the payload, then raises CheckCondition and discards it if the closing status is CHECK. read_lines(retries=1) then raises EndOfData or ScanReadError, and read_planes breaks or aborts. The code path is real, but whether the device ever sends data followed by CHECK is unmeasured, so this is potential rather than observed loss. Low.

</details>

<a id="decode-and-debug-filing-ddf-16"></a>

### DDF-16 -- Metering probe passes are filed with film='negative' whatever the film is

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2436`, `rps7200/library.py:694`

**Doc claim:** direct.py:1711-1714 'It is carried so the entry says what was in the transport'

The metering probes are scans and are filed under debug mode. Their meta records the default film type, so B&W, slide and Kodachrome probes are filed as 'negative'. scan.film is part of library.signature() and is what DemoScanner uses to pick pictures by film.

**Evidence (from the code):**

```text
`image, _ = self.scan(resolution=resolution, infrared=False, exposure_scale=scales, keep_raw=True,)` passes no `film=` although auto_exposure has `film`; scan() defaults `film: str = FILM_NEGATIVE` and records `"film": film`.
```

**Failure scenario:** A B&W roll metered under RPS7200_DEBUG=1 files its probes as colour negative. The demo's `_source_for('negative')` can then pick a B&W probe for a colour-negative demo.

**Fix:** Pass `film=film` into the probe's scan() call.

<details><summary>Second reader's check</summary>

The probe scan() call (2083-2092) passes no film=, and scan() defaults film=FILM_NEGATIVE (2436) and records it in meta. demo._source_for filters on scan.film (demo.py:888). This matters only for debug-filed probes.

</details>

<a id="decode-and-debug-filing-ddf-18"></a>

### DDF-18 -- Bracket and roll membership fields are not in debug entries or in scan.json

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:672`, `rps7200/direct.py:2402-2405`, `rps7200/direct.py:3653-3655`, `rps7200/library.py:237-261`

A bracket's pass order, ratio, pass count and stops are dropped from every filed entry on every path. So are a roll frame's transport position and index. Some can be inferred from exposure_scale or film.frame, but the ratio actually used by merge_bracket and the counter reading are lost.

**Evidence (from the code):**

```text
_debug_capture takes `dict(meta)` inside scan(), before scan_bracket adds `meta["bracket_index"] = i; meta["bracket_ratio"] = float(k)` and scan_roll adds `meta["roll_index"] = index; meta["roll_position"] = position`. library.save's `scan` block key list contains none of these keys.
```

**Failure scenario:** A 9-pass bracket is re-merged offline from the library. The ratios must be reconstructed from exposure_scale and rounding, and the pass count and stops are not in the record.

**Fix:** Add the bracket_* and roll_* keys to library.save's persisted set. Take the debug copy of meta at return time, or let callers amend the queued item.

<details><summary>Second reader's check</summary>

_debug_capture copies meta inside scan() (672), before scan_bracket adds bracket_* (2402-2405) and scan_roll adds roll_index/roll_position/registration (3653-3655). library.save's scan key list (237-261) has no bracket_* or roll_* keys, so those are lost on every path. `registration` is persisted through the session path but not through the debug path.

</details>

<a id="decode-and-debug-filing-ddf-19"></a>

### DDF-19 -- decode_index silently drops lines with unknown tags, never checks the second tag byte or the interleave, and does not record per-plane counts

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:1570-1600`

Lines are assigned to planes by their first byte alone. A line whose tag is not R/G/B/I is dropped without a count. A dropped or duplicated line shifts every later row of that plane relative to the others, because rows are aligned by index. Plane lengths that differ are silently truncated to the shortest. The meta records neither per-plane line counts nor the number of lines discarded, so a misregistered colour plane would ship silently. The raw bytes, when kept, allow re-analysis, but nothing flags the need.

**Evidence (from the code):**

```text
`tag = chr(line[0])\nplanes.setdefault(tag, []).append(...)`; `order = [c for c in CHANNEL_ORDER if c in planes]`; `height = min(len(planes[c]) for c in order)`.
```

**Failure scenario:** One corrupted tag byte mid-pass drops a G line. From that row down, G is misregistered by one row against R and B, which shows as a colour fringe. The entry records nothing unusual and reconstruct reproduces it exactly.

**Fix:** Verify line[1] == line[0] and the expected R,G,B(,I) cadence, count unknown or out-of-order lines, and put `plane_lines` and `discarded_lines` in meta and the raw layout. Refuse the pass (or flag it) when plane counts differ by more than the lead/trail lines.

<details><summary>Second reader's check</summary>

decode_index (1570-1592) keys lines on line[0] only, never checks line[1] or the cadence, drops unknown tags silently when the channel count still matches, and truncates to the minimum plane height. Neither meta nor raw_layout records per-plane counts.

</details>

<a id="decode-and-debug-filing-ddf-20"></a>

### DDF-20 -- apply_shading leaves columns past the mask's used entries uncorrected without refusing

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/shading.py:185-193`, `rps7200/shading.py:236-281`, `rps7200/direct.py:2719-2748`

**Doc claim:** direct.py:2720-2724

If the pass's CCD mask marks fewer used pixels than the image width, columns loc.size..width are returned uncorrected. The only sign is the 'columns/width' figure in the log and report. scan() refuses a pass wider than the reference ('correcting half a frame is worse than correcting none') but does not refuse this equivalent case. The same applies in library.corrected(). With a missing ccd_mask.bin, it falls back to a one-to-one column mapping that is wrong below 3600 dpi.

**Evidence (from the code):**

```text
`locs = np.flatnonzero(...== MASK_USED)\nreturn locs[:width]`; `out[:, : loc.size, c] = vals.astype(image.dtype)`; scan() only checks `params.width > self._shading.pixels_per_line`.
```

**Failure scenario:** A pass at an unusual typed dpi gets a mask with fewer used entries than its width. The right edge of every delivered image is uncorrected and striped, and the report records the shortfall without anyone reading it.

**Fix:** Raise ShadingUnavailable in scan() when report['columns'] < report['width']. In library.corrected(), refuse or flag when the mask is missing but the pass is below native width.

<details><summary>Second reader's check</summary>

build_width_to_loc returns locs[:width] (shading.py:185-193), and apply_shading writes only `out[:, : loc.size, c]` (281). scan() refuses only on params.width > pixels_per_line (2719). With mask=None, library.corrected falls back to arange(min(w, ppl)) (shading.py:244-245).

</details>

<a id="decode-and-debug-filing-ddf-21"></a>

### DDF-21 -- Exposure never wraps, but clamping is silent; a NaN scale fails only after calibration

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/protocol.py:589-616`, `rps7200/direct.py:2613-2621`, `rps7200/direct.py:1357-1371`, `tools/scan.py:143-146`

**Doc claim:** CLAUDE.md 'Exposure is a 16-bit timer; past 65535 it wraps'

The 16-bit wrap is guarded for scaled exposures: they are clamped to 65535 and never fold. A requested scale above the ceiling or below the floor of 100 is changed without a warning or a flag in meta. exposure_scale records what was asked, device_settings what was sent, and nothing says they differ because of clamping. A hand-built Settings with gain or offset above 255 wraps silently through `& 0xFF`. A NaN or inf `--exposure-scale` raises ValueError in round() only after ensure_shading has spent 3-4 minutes calibrating.

**Evidence (from the code):**

```text
`exposure=[int(max(100, min(65535, round(e * f)))) ...]`; set_gain_offset: `int(s.exposure[i]).to_bytes(2, "little")` (raises OverflowError above 65535) and `int(s.gain[i]) & 0xFF` (wraps silently); tools/scan.py parses `--exposure-scale` with bare float() and no range check.
```

**Failure scenario:** `tools/scan.py --exposure-scale 12` on red (base 9604) sends 65535, about 6.8x. The operator believes they got 12x, and nothing in the output says otherwise.

**Fix:** Return a `clamped` per-channel flag from Settings.scaled, log it, and record it in meta. Validate exposure scales (finite, > 0) at argument parsing. Range-check gain and offset in set_gain_offset instead of masking.

<details><summary>Second reader's check</summary>

Settings.scaled clamps to [100, 65535] (protocol.py:606-609) with no flag. set_gain_offset masks gain and offset with & 0xFF (1360-1361). tools/scan.py parses float() with no validation (145), and ensure_shading (calibration) runs before scan(), where round(nan) raises. The 16-bit wrap itself is guarded.

</details>

<a id="decode-and-debug-filing-ddf-24"></a>

### DDF-24 -- Demo: the read_direction record can contradict its raw bytes when the source entry was itself read bottom-up

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:490-528`

**Doc claim:** demo.py:484-488 'so what is filed decodes to what is shown and its record agrees with its bytes'

The recorded direction comes from re-encoding the upright image, not from the bytes that are filed. If the source entry's own raw bytes were bottom-up (4 scans and 38 prescans in the library per docs), the demo files those bytes unchanged with a 'forward' record, or reversed with a 'reversed' record that is now top-down. reconstruct then reports 'read direction CHANGED'.

**Evidence (from the code):**

```text
`blob = encode_index(a, reversed=reversed_now)\nupright, direction = DirectScanner.decode_index(blob, params, channels)` decides the recorded direction from a synthetic re-encode, while the filed bytes are `self._capture["raw"]`, reversed with `reverse_lines(...)` only when `reversed_now`.
```

**Failure scenario:** A demo scan draws a source entry recorded as reversed at the same dpi. The filed entry claims forward while its bytes lead with B, and reconstruct reports a direction change on the demo entry.

**Fix:** Derive the recorded direction from the bytes actually filed (read_direction(raw, layout)), or drop the raw bytes whenever the source direction and the modelled carriage disagree.

<details><summary>Second reader's check</summary>

_read_as_carriage decides `direction` from encode_index(upright image) (demo.py:497-498) and reverses the stored raw only when reversed_now (507-513). A source entry whose own raw.bin.gz is bottom-up is filed with bytes whose tags disagree with the recorded direction, and reconstruct then reports 'read direction CHANGED'.

</details>

<a id="decode-and-debug-filing-ddf-28"></a>

### DDF-28 -- Smaller documentation claims in this area that the code contradicts

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:587-589`, `rps7200/direct.py:2560-2592`, `rps7200/shading.py:15-20`, `rps7200/direct.py:1833-1845`, `rps7200/direct.py:740-746`, `rps7200/library.py:14-19`

**Doc claim:** rps7200/direct.py:587-589; rps7200/shading.py:15-20; rps7200/library.py:14-19

(a) Skipping calibration does not produce raw scans. The next shading=True scan silently spends 3-4 minutes calibrating, while ensure_shading's summary says 'expect vertical striping'. (b) The shading module docstring claims the reference is measured at the pass's exposure, which the calibration code says the device does not allow. (c) The spool-freeing comment implies the 43 GB peak is avoided, but the capture phase already holds the whole session in the spool. (d) The library docstring's 'exactly as they arrived' does not cover DDF-01/DDF-11.

**Evidence (from the code):**

```text
ensure_shading: '``skip`` leaves the session with no reference at all, which returns raw pixels'. scan(shading=True) instead calibrates: `self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`. shading.py: 'The reference is measured by the scanner ... *at the exposure and gain the pass will use*', while direct.py:1835-1837 says 'the device meters the calibration pass itself and returns the same ~48000 light level whatever is written here'. library.py:17 'shading.npz  the reference this pass would be corrected with' (derived), and line 14 'raw.bin.gz the scanner's bytes, exactly as they arrived'.
```

**Failure scenario:** The operator chooses shading 'off' expecting a fast raw scan. The first scan calibrates for minutes anyway.

**Fix:** Correct the docstrings. Make ensure_shading(skip=True) set a session flag that scan() honours by recording SHADING_SKIPPED_EXPLICIT, or state that skip only defers calibration.

<details><summary>Second reader's check</summary>

(a) ensure_shading's docstring says skip 'returns raw pixels', but scan(shading=True) calibrates when _shading is None (2581-2592). tools/scan.py pairs skip with shading=False, so only API callers are misled. (b) shading.py:15-20 says the reference is measured 'at the exposure and gain the pass will use', which contradicts direct.py:1833-1845. (c) is only partly right: the flush comment is about the peak spool plus library during filing, but the capture phase already holds the whole session's spool. (d) matches DDF-01 and DDF-11.

</details>

<a id="decode-and-debug-filing-ddf-29"></a>

### DDF-29 -- Filing tests check source text and include no-op assertions; the stale-raw path is untested

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_roll.py:722-747`, `tests/test_roll.py:752-757`, `tests/test_roll.py:790`, `tests/test_roll.py:796-803`

Debug filing is pinned by string matching against scan()'s source. Two assertions are tautologies (`or True`). No test drives scan() twice through a fake transport with keep_raw True then False under debug and checks what raw.bin.gz holds. The failure test encodes the data-loss behaviour of DDF-02 as correct.

**Evidence (from the code):**

```text
`assert "self._debug_capture(raw_pixels, meta)" in source`; `assert os.environ.get("RPS7200_DEBUG") is None or True`; `assert (entries[0] / "raw.bin.gz").exists() or True   # raw only when kept`; test_a_filing_failure_never_breaks_the_session asserts the pending queue is emptied (i.e. data discarded).
```

**Failure scenario:** A refactor renames `raw_pixels` and the string test fails, while the real stale-raw bug (DDF-01) passes every test.

**Fix:** Add behavioural tests with FakeTransport. (1) debug on, a keep_raw=True pass, then a keep_raw=False pass, asserting the second entry has no raw or its own. (2) Filing failure keeps the spool. (3) The GUI Scan job files the raw pixels (DDF-05). Remove the `or True` assertions.

<details><summary>Second reader's check</summary>

tests/test_roll.py:722-747 is source-string matching. Line 752 (`is None or True`) and line 790 (`.exists() or True`) are tautologies. test_a_filing_failure_never_breaks_the_session asserts the pending queue is emptied. No test drives keep_raw=True followed by keep_raw=False under debug, and none checks that the GUI Scan job files raw pixels.

</details>

<a id="decode-and-debug-filing-ddf-30"></a>

### DDF-30 -- Dead code in this area: defects._smooth and calibrate_shading(keep_data)

**Severity** low · **Category** dead-code · **Verdict** confirmed

**Where:** `rps7200/defects.py:18-26`, `rps7200/direct.py:1759`, `rps7200/direct.py:1966`

_smooth is unused. keep_data is the one hook that could expose the raw calibration bytes (DDF-04), yet nothing uses it, including the filing paths.

**Evidence (from the code):**

```text
`def _smooth(profile, window)` has no caller anywhere in the repository. `keep_data: bool = False` ... `"data": data if keep_data else None`: no caller passes keep_data=True.
```

**Failure scenario:** n/a (maintenance)

**Fix:** Remove _smooth. Replace keep_data with always keeping the calibration bytes on the scanner for filing.

<details><summary>Second reader's check</summary>

defects._smooth (18-26) has no caller in the repository (grep). calibrate_shading's keep_data is never passed True.

</details>

<a id="decode-and-debug-filing-ddf-a2"></a>

### DDF-A2 -- The infrared plane is never shading-corrected, and the report's 'uncorrected' count is never surfaced

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1850-1856`, `rps7200/shading.py:255-259`, `rps7200/direct.py:2733-2748`

**Doc claim:** CLAUDE.md 'Everything an operator sees, exports or saves is corrected'

On every RGBI scan the I plane is delivered and exported as raw, uncorrected sensor output next to corrected RGB, with its column fixed pattern intact. The only trace is `uncorrected: 1` inside calibration.report, which nothing reads. CLAUDE.md and README state that everything an operator sees or exports is corrected. The raw bytes are kept, so this is re-correctable later if an IR reference is ever acquired. No data is lost.

**Evidence (from the code):**

```text
calibrate_shading uses `self.set_mode(resolution=resolution, passes=ONE_PASS_COLOR, ...)`, so the reference holds only R/G/B tags. apply_shading does `if c not in reference.ref: report["uncorrected"] += 1; continue`. scan()'s log prints only columns/width and clipped; nothing reads report['uncorrected'] (grep).
```

**Failure scenario:** A dust/scratch detector run on the delivered IR plane sees the IR column pattern as vertical defects, while the operator believes the delivered file is flat-fielded.

**Fix:** State the limitation where the correction is documented and log it per pass when report['uncorrected'] > 0. Consider whether an RGBI calibration pass is possible.

<a id="decode-and-debug-filing-ddf-a3"></a>

### DDF-A3 -- A calibration that yields no usable lines replaces the session's good reference with None

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1946-1953`, `rps7200/session.py:1388-1392`

**Doc claim:** rps7200/session.py:1389-1390

The session comment is true only when calibrate_shading raises. If the pass returns and calculate_shading gives None (empty or short data), the previous valid reference is overwritten with None, while _ccd_mask keeps the old calibration's mask. The next shading=True scan then recalibrates or raises ShadingUnavailable, and the good reference is gone for the session. The cache file on disk is not overwritten (save_shading returns None).

**Evidence (from the code):**

```text
`self._shading = calculate_shading(data, width)` runs unconditionally. session._calibrate comment: "A calibration that failed part way leaves the reference the session already had, if it had one."
```

**Failure scenario:** The operator presses Calibrate a second time mid-session. The pass ends after zero blocks (see DDF-12's catch-all), the session loses its working reference, and the next Scan spends another 3-4 minutes calibrating or fails.

**Fix:** Assign the new reference only when calculate_shading returns non-None, or state explicitly that a failed recalibration discards the old reference.

<a id="decode-and-debug-filing-ddf-a4"></a>

### DDF-A4 -- duration_s includes host-side correction and finish-scan polling, not only device time

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:2654`, `rps7200/direct.py:2686-2748`, `rps7200/direct.py:2794`

**Doc claim:** CLAUDE.md 'Estimate first, from the medians across the library'

The one timing field in the record mixes device time with host CPU time. CLAUDE.md's per-dpi time budgets and docs/byte14-plan.md's ms/line analyses are derived 'from the library's own duration_s'. apply_shading at 3600 dpi RGB is a full float64 copy of a multi-hundred-MB array, so the host share varies with the machine and inflates the recorded scan cost. No raw timestamps are stored to separate the two.

**Evidence (from the code):**

```text
`started = time.monotonic()` is taken before start_scan, and `"duration_s": round(time.monotonic() - started, 1)` is computed after finish_scan (three READ_STATE polls with 0.2 s sleeps), the optional advance(), the 7200 dpi realignment and apply_shading's float64 pass over the whole image.
```

**Failure scenario:** A byte14 or fast-infrared ladder compares ms/line across passes with shading on and off. The shading-on passes include seconds of host arithmetic and appear slower on the device.

**Fix:** Record the device interval (start_scan to last READ) separately, e.g. read_s, and leave host processing out of it, or record the start and end timestamps.

<a id="decode-and-debug-filing-ddf-a5"></a>

### DDF-A5 -- prescan.tif in roll-frame entries holds shading-corrected 8-bit pixels with no raw bytes and no marker

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1883-1888`, `rps7200/library.py:178-179`, `rps7200/library.py:288-293`

**Doc claim:** CLAUDE.md 'The library holds raw pixels; everything else is corrected'

Each roll frame's entry carries a prescan.tif that is the delivered (flat-fielded) framing pass. It has no raw bytes and no flag saying it was corrected, which contradicts 'the library holds raw pixels'. The same prescan is also filed as its own raw entry, so it is recoverable by cross-reference. Within the frame entry, however, it looks raw. The demo serves it directly as the prescan picture.

**Evidence (from the code):**

```text
The call is `self._file(seq, number, rf.image, frame_meta, ..., raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta, ...)`, where rf.prescan is the corrected image prescan() returns. library.save writes `tiff.write(str(path / "prescan.tif"), prescan)`, and its record says only `{file, read_direction, carriage_state}`.
```

**Failure scenario:** An offline analysis loads an entry's prescan.tif as raw sensor data and applies shading from shading.npz, double-correcting it.

**Fix:** Store the raw prescan (rf.raw_prescan) as prescan.tif, or record `prescan.corrections_applied: ['shading']` and link to the prescan's own entry id.

<a id="decode-and-debug-filing-ddf-27"></a>

### DDF-27 -- Three conflicting statements about whether SET GAIN OFFSET persists

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2064-2067`, `rps7200/direct.py:3634-3643`, `rps7200/direct.py:3401-3406`

**Doc claim:** CLAUDE.md 'Facts that are easy to get wrong'

scan() always computes `self.get_gain_offset().scaled(exposure_scale)`. Whether a metered scan compounds its exposure depends on which of the three statements is true. The main scan after auto_exposure does not re-write the base, while scan_roll does. Correctness currently rests on READ GAIN/OFFSET returning a fixed reference, which only the scan_roll comment states. The recorded device_settings.exposure is what was sent, so the record is right either way.

**Evidence (from the code):**

```text
auto_exposure: ':meth:`scan` multiplies whatever the device currently holds, and SET GAIN OFFSET persists, so re-reading it each round would compound the scales.' scan_roll: 'the read is a fixed reference, `scaled()` always yields base x scale, and exposure cannot compound frame to frame.' CLAUDE.md: '`SET GAIN OFFSET` does not persist across a scan sequence.'
```

**Failure scenario:** If READ GAIN/OFFSET ever returns the live value, `scan(auto_exposure=True)` sends base x probe_scale x final_scale. Only the roll path is protected by its explicit `set_gain_offset(baseline)`.

**Fix:** Settle the fact once in one place and make scan() independent of it. Pass the base Settings explicitly, or have scan() re-write the base before scaling, as scan_roll does.

<details><summary>Second reader's check</summary>

auto_exposure's comment says SET GAIN OFFSET persists (2064-2067), and scan_roll's says the read is a fixed reference (3634-3643). CLAUDE.md says it does not persist. scan() always scales get_gain_offset(), and only scan_roll rewrites the baseline before its frame scans. The record is correct either way, since device_settings holds what was sent.

</details>

<a id="decode-and-debug-filing-ddf-a6"></a>

### DDF-A6 -- Debug filing's default root is a path relative to the current directory

**Severity** info · **Category** design · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:50`, `rps7200/direct.py:712`

A debug-mode script started from any directory other than the repository root files its entries into a new ./library there. They are then invisible to `make verify`/`reconstruct` and to the demo.

**Evidence (from the code):**

```text
`DEFAULT_ROOT = Path("library")`; `root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT`
```

**Failure scenario:** `cd tools && RPS7200_DEBUG=1 uv run python byte14_probe.py` files the ladder into tools/library/.

**Fix:** Resolve the default root against the package or repository location, or log the absolute filing path at flush time.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Debug spool: one pass's raw decoded pixels | $TMPDIR/rps7200-debug-XXXXXX/NNN-image.npy | NumPy .npy, (H,W,C) uint8 or uint16, C=3 or 4 (R,G,B[,I]); at 7200 dpi already trimmed by 4 rows and stagger-realigned | raw (pre-shading) but after the 7200 dpi stagger realignment | DirectScanner._debug_capture (direct.py:675-677) inside scan() when debug is on | DirectScanner._debug_flush via np.load(mmap_mode='r') (direct.py:718); unlinked in finally (direct.py:747-753) | Exact copy of raw_pixels, but volatile: deleted even when filing fails (DDF-02) and orphaned without meta if close() never runs (DDF-03) |
| Debug spool: raw USB bytes attributed to the pass | $TMPDIR/rps7200-debug-XXXXXX/NNN-raw.bin | binary: concatenated READ payloads, index format (2-byte tag 'RR'/'GG'/'BB'/'II' + samples LE) | raw | DirectScanner._debug_capture from capture_record()['raw'] = self.last_raw (direct.py:679-683) | library.save streams it into raw.bin.gz (library.py:205-210) | Byte-exact, but may belong to an earlier pass (stale last_raw) or be absent when keep_raw=False (DDF-01) |
| Debug pending queue (meta, capture time, shading reference object, CCD mask, raw layout) | in memory: DirectScanner._debug_pending | list of dicts; meta is a shallow copy taken inside scan() | n/a | _debug_capture | _debug_flush at close() | Lost on crash/kill. 'captured' is recorded and never persisted; bracket_*/roll_* keys added after scan() are missing |
| Library raw bytes | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/raw.bin.gz | gzip (level 6) of the joined READ payloads; sha256 of the uncompressed stream in scan.json raw.sha256; layout in scan.json raw.layout {format:'index', bytes_per_line, line_stride, index_header, width, lines, channels, byte_order, lines_received} | raw | library.save (library.py:187-211), called from _debug_flush, session FrameWriter, tools/scan.py, tools/scan_roll.py | library.read_raw/decode_raw/reconstruct/migrate_direction; DemoScanner._decode; tools/library.py verify/reconstruct | Lossless for what read_planes accepted. Excludes calibration bytes and any payload discarded on a post-data CHECK (DDF-11). Can be another pass's bytes via debug filing (DDF-01). Dropped by the session guard on short reads (DDF-10) |
| Library pixels | library/<id>/scan.tif | TIFF uint8/uint16, (H,W,3) RGB or (H,W,4) with ExtraSamples=unspecified for IR; deflate+predictor with tifffile, else uncompressed | Intended raw decode. Actually: shading-corrected for GUI single scans (DDF-05) and demo entries (DDF-23); stagger-realigned at 7200 dpi (DDF-07) | library.save -> tiff.write | library.load/corrected/reconstruct/verify, demo (fallback when no raw), tools | Lossless container; sha256 in scan.json image.sha256 |
| Shading reference per entry | library/<id>/shading.npz | np.savez_compressed: pixels_per_line (int), channels (int array), dark_channels (int64 array), ref{c}/dark{c} float64 per column, mean{c}/darkmean{c} float64 | derived (host-computed averages from calculate_shading), not the calibration bytes | ShadingReference.save via library.save (library.py:182-183) | ShadingReference.load in library.load/corrected/reconstruct and DemoScanner._decode | Exact for the derived arrays. The raw calibration lines, the level split, block count and whether the reference was loaded or measured are not recorded (DDF-04, DDF-13) |
| CCD mask per entry | library/<id>/ccd_mask.bin | raw bytes of SCSI COPY response, length = reference pixels_per_line (5172) or CCD_MASK_SIZE; 0x00 = used, 0x70 = unused | raw | library.save from capture_record()['ccd_mask'] = self._ccd_mask (set per pass in scan(), direct.py:2667-2672) | library.load/corrected/reconstruct, shading.build_width_to_loc, demo | Byte-exact for the pass that set it |
| Entry record | library/<id>/scan.json | JSON: id, created (flush time), image{shape,dtype,channels,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{resolution_dpi, frame, width, height, depth, channels, channel_order, bytes_per_line, film, exposure_scale, exposure_metered, duration_s, protocol_revision, rotation, flipped, reversal, read_direction, carriage_state, fast_infrared, filter_offsets}, device_settings{exposure,gain,offset}, metering, registration, calibration{shading, ccd_mask, pixels_per_line, light_mean(rounded 0.1), report, skipped}, prescan{...}, film, tags, provenance | metadata | library.save (library.py:213-309); rewritten by migrate_direction | everything in library.py, demo, tools/library.py, tools/linearity.py | Not complete. Missing: byte14, slide_init_param, quality bits, light/extra_entries/double_times, raw GET PARAMETERS, INQUIRY (accepted and ignored), capture time, bracket_*/roll_* fields, stagger realignment. exposure_metered is wrong on roll and bracket frames. Metering levels rounded to 4 dp |
| Library index | library/index.json | JSON summary list | derived | library.reindex on every save | humans/tools listing | Derived, rebuildable |
| Session shading cache | calibration/shading.npz (demo: demo/calibration/shading.npz) | same as shading.npz | derived | DirectScanner.save_shading via ensure_shading after a calibration (direct.py:563-570, 615) | DirectScanner.load_shading when reuse=True (GUI 'reuse the cached reference', tools --reuse) | Written non-atomically, overwritten each calibration, no timestamp or provenance. Entries carrying a loaded copy are not marked (DDF-13) |
| Calibration raw data | in memory only: calibrate_shading local `collected`/`data` | 16-bit LE tagged lines, 2 + 2*width bytes each | raw | calibrate_shading read loop (direct.py:1917-1936) | calculate_shading, then discarded (returned only if keep_data=True, which nothing passes) | Never persisted (DDF-04) |

**Second reader's corrections to this table:**

1) Missing row: library/<id>/prescan.tif (roll-frame entries). It is the corrected (flat-fielded) 8-bit framing pass, written by library.save from rf.prescan via session.py:1887. It has no raw bytes and no corrections marker; the raw version exists only as the separate prescan entry. 2) Missing row: rolls/<roll>/prescanNN-before.tif. It is a corrected TIFF of a real pass that is never filed in the library in any form (session.py:1841-1853, file_entry=False). 3) scan.json `created`: flush time only for debug entries. For session entries it is the FrameWriter write time. For tools/scan.py it is after close(). It is capture time on no path. 4) scan.json `metering`: present only when scan() itself metered. Absent on every roll and bracket frame, not only rounded. 5) The 'Library pixels' row should say 'corrected' also for demo prescans, and note the uncorrected IR plane in delivered/exported images. 6) The 'Debug spool raw' row: the stale bytes can also be a pass whose decode failed (last_raw is set before decode_index runs). 7) The calibration cache row: when a recalibration yields None, the file is left in place while the in-memory reference is discarded (DDF-A3). 8) duration_s in scan.json includes host shading time and finish_scan polling (DDF-A4).

## What the operator can do

- Turn on automatic filing for any script with RPS7200_DEBUG=1 (or DirectScanner(debug=True)). Every scan()/prescan() pass is then spooled to system temp and filed in library/ after close().
- Redirect debug filing to another library with RPS7200_DEBUG_ROOT=<dir>.
- Choose per pass whether raw USB bytes are kept (scan(keep_raw=True) / prescan(keep_raw=True)).
- Request raw pixels deliberately with scan(shading=False). The entry records SHADING_SKIPPED_EXPLICIT and library.corrected() leaves it raw.
- Reuse a saved shading reference instead of calibrating (GUI 'reuse the cached reference', tools --reuse, DirectScanner.load_shading).
- Override MODE SELECT byte14, the SLIDE INIT param, skip_shading and fast_infrared per scan() call, and pass explicit per-channel exposure_scale values.
- Re-decode any stored entry offline with library.decode_raw / library.reconstruct / tools/library.py reconstruct, and re-correct it with library.corrected() using today's code.
- Meter a frame (auto_exposure) and read what the probes saw on DirectScanner.last_metering right after.

## What the operator should not do

- Call scan()/prescan() with the default keep_raw=False while debug filing is on. The entry gets no raw bytes or another pass's bytes.
- Interrupt, kill or time out a debug session before close() has finished. All pending entries are lost and the spool is left orphaned in temp.
- Let the library disk or /tmp (often tmpfs) run full during a debug session. Failed spool writes and failed filings discard the pass.
- Rely on 'reuse the cached reference' across power cycles for scans meant to be evaluated later. The entries do not say the reference was reused.
- Use byte14 / slide_init_param overrides and expect the library entry to record them.
- Scan at 7200 dpi with shading=False and expect `reconstruct` to confirm the entry. It is always reported as changed.
- Run the window with --demo and --library pointing at the real library.
- Call calibrate_shading(resolution=...) at anything but 3600 dpi. The docstring calls it untested, and nothing guards it.
- Treat 2_corrected.tif from tools/make_comparison.py as the pipeline's correction. It runs destripe, not apply_shading.

## Mistakes nothing guards against

- With debug on, scan(keep_raw=False) after any pass that kept its bytes, including the metering probes of auto_exposure=True, files the earlier pass's raw.bin.gz under this pass's pixels. Nothing compares the two.
- Running a probe script from a different working directory files debug entries into <cwd>/library, a separate library that verify/reconstruct never see.
- An --exposure-scale above the 65535 ceiling (or below the floor of 100) is silently clamped. The record keeps the requested scale and nothing flags the difference. A NaN scale raises only after calibration has run.
- A filing failure at close() (disk full, unwritable root, tifffile error) deletes the spooled pixels and bytes. With verbose=False and no log hook, nothing is printed.
- A process killed during a long debug run loses every entry's meta, reference and mask. Only unlabelled .npy/.bin files remain in temp.
- The window's Scan button files the shading-corrected image as the entry's raw scan.tif. The operator cannot avoid this.
- ensure_shading(skip=True) or Calibrate 'off' does not give raw scans. The next shading=True scan silently spends minutes calibrating.
- --demo together with --library library puts demo entries (corrected pixels, reused or reversed bytes, missing device fields) into the real library with no demo tag.

## Dataflow notes

How a pass enters, is transformed and leaves (rps7200/direct.py unless noted):

1. **Setup.** scan() (2423) optionally meters first. auto_exposure (1977) runs RGB probe scans at 300 dpi with keep_raw=True, film left at its default (2083-2092). It measures the 99.5th percentile inside metering_slice and computes per-channel scales against the ceilings 65535/base (2155-2200). The result is left on last_metering (2218).
   - scan() then polls READ_STATE (2533-2541), which sets last_state and so the carriage record.
   - It checks the shading reference width before the pass and may call calibrate_shading (2560-2602).
   - It sends SCAN FRAME, cmd_17, and get_gain_offset().scaled(exposure_scale). Settings.scaled clamps exposure to [100, 65535] (protocol.py:589-616).
   - It sends set_gain_offset (1350), set_mode (1057; byte14 from byte14_for or the override; quality bits), SLIDE INIT, and takes carriage_record (2652).
   - After start_scan it reads the per-pass CCD mask with SCSI COPY into self._ccd_mask (2663-2672) and get_parameters (1387; only width, lines, bpl and filter offsets kept).

2. **Bytes.** read_planes (1440) pulls lines in batch_for(bpl) batches through read_lines (1398) and Transport.command/_read_payload (usb_transport.py:717-770, 32 KB windows). A payload followed by CHECK is discarded (usb_transport.py:849-857). The chunks are joined into `blob`. Only when keep_raw=True does it set last_raw and last_raw_layout (1518-1532); otherwise last_raw keeps the previous pass's bytes.

3. **Decode.** decode_index (1552):
   - Lines are split by their first tag byte into planes (1575-1580).
   - Unknown tags are ignored; all planes are truncated to the shortest.
   - read_direction (direction.py:85) decides forward, reversed or unknown from the first R versus first B tag, cross-checked against the last tags when the read is complete.
   - Reversed passes get their rows turned (1596-1599).
   - The result is an (H,W,C) uint8/uint16 image, dtype from bytes_per_line//width.
   - At 7200 dpi, _realign_native_column_stagger trims 4 rows and shifts odd columns (2695-2701). This is unrecorded.

4. **Correction.** raw_pixels = image (2708). apply_shading (shading.py:196) maps columns through build_width_to_loc(mask). It uses two-point correction when a dark reference exists, scales the reference to the pass's depth, rounds with floor(x+0.5), and clips and counts overflows. It returns the corrected image and a report (2733). The reference itself comes from calibrate_shading (1755): 4-line reads of 16-bit tagged lines under a 300 s deadline. calculate_shading (shading.py:109) splits the lines by level at the widest gap, averages them into float64 ref/dark/mean, and discards the raw bytes (1946-1950).

5. **Meta.** meta (2760-2804) records resolution, channels, film, protocol_revision, the shading report or skip, depth, frame, width, height (after trim), bytes_per_line, filter_offsets, exposure/gain/offset as sent, exposure_scale, exposure_metered (=auto_exposure argument), fast_infrared, duration, read_direction.as_record(), carriage_state, and metering only when scan() metered.

6. **Leaving.**
   - (a) Debug path: _debug_capture(raw_pixels, meta) (2811). It saves NNN-image.npy and NNN-raw.bin (from last_raw, possibly stale) in a mkdtemp spool, and keeps the reference, mask and layout in memory. close() (777) closes the transport, then _debug_flush (694) calls library.save per item. The spool is unlinked whatever the outcome.
   - (b) Caller path: last_pixels_raw and last_scan_meta are set (2818-2823) and the corrected image is returned. tools/scan.py reads last_pixels_raw and capture_record(). scan_roll copies last_pixels_raw onto RollFrame.raw_image/raw_prescan (3497, 3556, 3587, 3657). session._prescan uses last_pixels_raw, but session._scan does NOT (session.py:1456), so the window files corrected pixels. session._file runs a shape guard comparing layout['lines'] (declared) with the image height and drops the raw bytes when they differ (session.py:2073-2099).
   - library.save (library.py:131) writes scan.tif, prescan.tif, shading.npz (derived), ccd_mask.bin, raw.bin.gz (gzip plus sha256) and scan.json. It ignores `inquiry`, stamps the flush time, and writes non-atomically.

7. **Offline.** library.decode_raw/reconstruct re-run decode_index on raw.bin.gz with layout width/lines/bpl/channels (no stagger realign). reconstruct compares with scan.tif and the recorded read_direction. library.corrected() re-applies apply_shading with the stored shading.npz and ccd_mask.bin. DemoScanner._decode (demo.py:955) decodes stored raw bytes and always applies shading. _read_as_carriage re-encodes the corrected image with encode_index and decodes it again to model direction, reversing the stored bytes with reverse_lines when the modelled carriage is at the far end.

8. **read_direction.** Recorded consistently on every scan() pass and prescan: scan.read_direction and prescan.read_direction via prescan_meta. The demo writes carriage_state.modelled=True and takes the direction from a synthetic re-encode rather than from the bytes it files.

9. **defects.py.** Used only by tools/make_comparison.py (destripe, find_column_defects, flat_defect_sigma, resample_reference). It is never on the scan or library path.
