# USB transport, protocol and command sequence

Area key `transport-protocol`. 34 findings: 1 critical, 5 high, 16 medium, 11 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Transport-protocol area: rps7200/usb_transport.py (libusb ctypes transport, IEEE1284 preamble, windowed bulk reads), rps7200/protocol.py (opcodes, payload layouts, Sense/State/Settings, PROTOCOL_REVISION), rps7200/usbpcap.py (pcapng reader), the command/state/timeout parts of rps7200/direct.py (open/close, inquiry, _query, read_state, start/finish/stop scan, calibrate_shading, scan command sequence, read_lines/read_planes, slide/_whole_frames/nudge, debug spool+flush), tools/check_scanner.py, tools/verify_protocol.py, tools/parse_capture.py, tools/verify_capture.py and docs/protocol.md. Everything below was judged from the code, and I followed calls into session.py, library.py, shading.py, demo.py, scan_roll.py, uniformity.py and gui.py to confirm them. No file was modified and no hardware was touched.


The transport itself is careful in many places. It never sends SET_SCAN_HEAD, no tool or GUI path sends IEEE1284 RESET or STOP SCAN, windowing is 32 KB, a mid-payload zero-length packet is waited out, and a sense read never raises. The serious problems are in what surrounds it:

(1) Paths that file the wrong bytes, or none, into the library:
  - The GUI single Scan files corrected pixels as raw.
  - The debug flush deletes spooled data when filing fails.
  - Debug capture attaches a stale last_raw from an earlier pass.
  - Calibration raw lines are discarded, and only a reduced float reference is kept.
  - Neither INQUIRY nor the per-pass MODE SELECT, gain/offset or sense state is recorded.
  - At 7200 dpi the stagger realign is baked into scan.tif without being recorded, and the session guard then drops the raw bytes.
(2) Failure paths that keep driving a device left mid-read:
  - scan_roll advances and rescans after a UsbError.
  - The session accepts the next job.
  - A lazy calibration still runs inside scan(), and that path is recorded as stalling with LIBUSB_ERROR_PIPE.
  - force_abort closes libusb from the UI thread while the worker is inside a transfer.
  - The CLI roll loses queued frames on Ctrl-C.
(3) Timeout guards that do not match the documented infrared floor. INFRARED_FLOOR_S guards nothing, and a silent pause mid-payload raises immediately instead of being waited out.

(4) Many doc and code mismatches in docs/protocol.md, CLAUDE.md, the README and docstrings, including the claim that the pcap reader never returns interrupt (keystroke) payloads.

(5) Demo divergence: no 7200 dpi refusal, and position never returns None.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [TP-01](#transport-protocol-tp-01) | critical | data-integrity | GUI single Scan files the shading-CORRECTED image into the library as raw pixels | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [TP-02](#transport-protocol-tp-02) | high | data-integrity | Debug flush deletes the only copy of a scan when filing it fails | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [TP-03](#transport-protocol-tp-03) | high | data-integrity | Debug filing and capture_record attach a STALE last_raw from an earlier pass (or none) to a scan taken with keep_raw=False | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [TP-04](#transport-protocol-tp-04) | high | data-integrity | Calibration raw bytes are discarded; only a reduced float reference is stored, and its provenance is not recorded | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [TP-05](#transport-protocol-tp-05) | high | hardware-safety | After a transport failure mid-read, the roll, the session and the tools keep sending commands (SLIDE NEXT, new scans) to a device left mid-scan | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-06](#transport-protocol-tp-06) | high | hardware-safety | The lazy calibration inside scan(), recorded as stalling with LIBUSB_ERROR_PIPE, is still reachable, including from a GUI job queued behind a failed Calibrate | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [TP-08](#transport-protocol-tp-08) | medium | error-handling | A mid-payload pause is waited out only if the device sends a zero-length packet; a silent pause raises at once, after clear_halt, abandoning the read | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-09](#transport-protocol-tp-09) | medium | doc-mismatch | INFRARED_FLOOR_S guards no timeout; the real read guards are 120 s, below the documented 212-227 s untied-IR floor | [P16](../problems/P16-timeouts-and-runtime-budget.md) |
| [TP-10](#transport-protocol-tp-10) | medium | concurrency | force_abort closes the libusb handle and context from the UI thread while the worker is inside a transfer; a crash also loses queued library entries | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-12](#transport-protocol-tp-12) | medium | error-handling | The calibration read loop can end silently mid-calibration and lets non-ScanReadError failures escape | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [TP-13](#transport-protocol-tp-13) | medium | hardware-safety | GUI single Scan/Prescan gzips library entries with the device open and idle, contrary to the documented rule | [P13](../problems/P13-gzip-with-device-open.md) |
| [TP-14](#transport-protocol-tp-14) | medium | data-integrity | 7200 dpi stagger realignment is baked into the 'raw' scan.tif without being recorded, and the session then drops the raw bytes | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [TP-15](#transport-protocol-tp-15) | medium | data-integrity | Per-pass command and response state needed to re-derive a pass is not recorded | [P09](../problems/P09-record-missing-parameters.md) |
| [TP-16](#transport-protocol-tp-16) | medium | data-integrity | INQUIRY (device identity and firmware) is never stored in any library entry | [P09](../problems/P09-record-missing-parameters.md) |
| [TP-18](#transport-protocol-tp-18) | medium | user-error | Calibration never checks the measured media flag, and tools/uniformity.py calibrates right after telling the operator to empty the transport | [P17](../problems/P17-calibration-without-asking.md) |
| [TP-19](#transport-protocol-tp-19) | medium | user-error | Ctrl-C during tools/scan_roll.py loses queued frames and closes the transport under the read | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-20](#transport-protocol-tp-20) | medium | doc-mismatch | usbpcap.packets() returns every record's payload, keyboard interrupt (keystroke) payloads included, although the docs say it never does | [P32](../problems/P32-usbpcap-returns-keystrokes.md) |
| [TP-21](#transport-protocol-tp-21) | medium | demo-divergence | The demo accepts 7200 dpi (and any >3600 dpi shaded pass) that DirectScanner refuses, and its position() never returns None | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [TP-A1](#transport-protocol-tp-a1) | medium | data-integrity | A pass that fails during or after its read is never filed, even in debug mode: its partial raw bytes are discarded | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [TP-A2](#transport-protocol-tp-a2) | medium | hardware-safety | read_planes gives a CHECK CONDITION a single attempt, so one queued one-shot sense aborts a pass mid-scan | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-A3](#transport-protocol-tp-a3) | medium | data-integrity | tools/scan.py holds every pass in memory and files them only after the with-block, so an exception mid-bracket loses all completed passes | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [TP-A4](#transport-protocol-tp-a4) | medium | demo-divergence | DemoScanner has no last_pixels_raw, so demo sessions file corrected pixels labelled raw and the session's raw-vs-corrected path never runs in the demo | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [TP-07](#transport-protocol-tp-07) | low | hardware-safety | Calibration sends an extra, vendor-absent CAL INFO prepare+READ(32) with a different payload, between SLIDE INIT and SCAN | -- |
| [TP-11](#transport-protocol-tp-11) | low | bug | _whole_frames takes a failed READ STATE (None) as its baseline, so any later successful read counts as 'moved' | -- |
| [TP-17](#transport-protocol-tp-17) | low | hardware-safety | session_start moves the film with a forward sub-frame SLIDE, which is not recorded, and its docstring misstates the payload | -- |
| [TP-22](#transport-protocol-tp-22) | low | test-gap | verify_capture's opcode check can never fail, its verdict overclaims, and verify_protocol's restore report is inverted | -- |
| [TP-23](#transport-protocol-tp-23) | low | user-error | RPS7200_MAX_WINDOW is unvalidated: 0 or a negative value loops forever, a bad value breaks every import, and the value is not recorded | -- |
| [TP-24](#transport-protocol-tp-24) | low | bug | A READ answered with status OK returns b"" and read_planes still counts the lines as received | -- |
| [TP-25](#transport-protocol-tp-25) | low | design | A scalar exposure_scale also scales the infrared exposure the vendor treats as a device constant | -- |
| [TP-26](#transport-protocol-tp-26) | low | doc-mismatch | STOP SCAN and IEEE1284 RESET remain callable APIs, and their docstrings recommend them | -- |
| [TP-27](#transport-protocol-tp-27) | low | doc-mismatch | docs/protocol.md contradicts the code and itself on ports, COPY, the slide law, rewinds and the command order | -- |
| [TP-28](#transport-protocol-tp-28) | low | doc-mismatch | Docstrings, comments and CLAUDE.md make claims the code does not do | -- |
| [TP-30](#transport-protocol-tp-30) | low | error-handling | Readiness results are ignored, and the media log reports the wrong byte | -- |
| [TP-29](#transport-protocol-tp-29) | info | data-integrity | The SLIDE law change was not accompanied by a PROTOCOL_REVISION bump | -- |

## Findings in full

<a id="transport-protocol-tp-01"></a>

### TP-01 -- GUI single Scan files the shading-CORRECTED image into the library as raw pixels

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:2031-2048`, `rps7200/session.py:1085-1100`, `rps7200/direct.py:2732-2733`, `rps7200/direct.py:2818-2824`

Every single scan taken from the window with shading on (the default) is filed with flat-fielded pixels in scan.tif. library.save records `corrections_applied: []`, so the entry claims to be raw. This is exactly the failure CLAUDE.md describes for prescans ('26 entries held corrected pixels while their record said raw'), and it is still present on the Scan job.

**Evidence (from the code):**

```text
session.py:1441 `image, meta = self._scanner.scan(... shading=job.shading ... keep_raw=True)`; session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` -- no raw_image argument. FrameWriter._write session.py:1090-1091 `entry = library.save(job["image"] if raw_image is None else raw_image, ...)`. scan() returns the corrected array: direct.py:2733 `image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`, while the raw pixels live only in `self.last_pixels_raw` (direct.py:2818). By contrast _prescan passes `raw_image=raw_image` (session.py:1434) and the roll passes `raw_image=rf.raw_image` (session.py:1886).
```

**Failure scenario:** The operator presses Scan (1800 dpi RGBI, shading on). scan.tif holds corrected pixels while raw.bin.gz holds the true bytes. library.corrected() then applies the shading a second time, so Save As, export and the full-resolution view are all double-corrected. `make reconstruct` reports 'decode CHANGED' for every GUI scan, which buries real regressions.

**Fix:** In ScanSession._scan pass `raw_image=getattr(self._scanner, 'last_pixels_raw', None)`, as _prescan does. Make FrameWriter/library.save refuse, or require `corrections=['shading']`, when meta['shading'] is set and no raw_image was supplied. Add a session test asserting that the filed scan.tif equals decode_raw(entry). Repair existing GUI entries from their raw.bin.gz (migrate-raw style).

<details><summary>Second reader's check</summary>

scan() returns the corrected array (`image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`, direct.py:2733/2824) and keeps the uncorrected one only in `self.last_pixels_raw` (2818). ScanSession._scan (session.py:1440-1458) calls `self._file(seq, 0, image, meta, ...)` without raw_image, so `_file` submits raw_image=None and FrameWriter._write files `job["image"] if raw_image is None else raw_image` (session.py:1090-1091), which is the corrected array. library.save records `corrections_applied: list(corrections or [])`, which is [] here. library.corrected() then sees no 'shading' in applied, finds the reference that capture_record supplied, and calls apply_shading again (library.py:374-391): a double correction. tools/scan.py hold() (tools/scan.py:175-192) and ScanSession._prescan (session.py:1415/1434) both carry comments describing exactly this bug and fix it; _scan was missed. No test in tests/ covers the session Scan job's filed pixels (the only last_pixels_raw test hits are test_scan_tool.py and one stub in test_session.py:264).

</details>

<a id="transport-protocol-tp-02"></a>

### TP-02 -- Debug flush deletes the only copy of a scan when filing it fails

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:713-753`

The spool files (NNN-image.npy raw pixels, NNN-raw.bin exact bulk bytes) are unlinked in `finally`, whether library.save succeeded or raised. A failed filing produces a log line and then permanently destroys the pixels and raw bytes. The debug path exists precisely so that probe scans are never lost.

**Evidence (from the code):**

```text
direct.py:715-753: `try: image = np.load(item["image_path"], mmap_mode="r"); entry = library.save(...)` `except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")` `finally: image = None ... for key in ("image_path", "raw_path"): ... Path(path).unlink(missing_ok=True)`
```

**Failure scenario:** The disk is full, the library path is unwritable (RPS7200_DEBUG_ROOT typo) or tiff.write raises during the flush after a 38-frame probe run. Each frame's spooled raw bytes and pixels are deleted right after its save fails, and only the 'could not file scan' log lines remain.

**Fix:** Unlink only after a successful save. On failure keep the spool directory, record its path and the item meta in a manifest (e.g. spool/pending.json), and say where it is. Add a `library.py file-spool` command to retry.

<details><summary>Second reader's check</summary>

_debug_flush (direct.py:713-753) unlinks `image_path` and `raw_path` in `finally`, whether library.save succeeded or raised in the `except Exception` arm, and then `shutil.rmtree(self._debug_spool, ignore_errors=True)` (757-763) removes the whole spool. A failed save leaves only the log line 'debug: could not file scan n'. That is permanent loss of the raw bytes and pixels. Rated high rather than critical because it needs a prior filing failure (disk full, bad RPS7200_DEBUG_ROOT, TIFF error), but that is exactly the situation this path exists to survive.

</details>

<a id="transport-protocol-tp-03"></a>

### TP-03 -- Debug filing and capture_record attach a STALE last_raw from an earlier pass (or none) to a scan taken with keep_raw=False

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:1518-1532`, `rps7200/direct.py:631-646`, `rps7200/direct.py:672-688`, `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2437`, `rps7200/library.py:186-211`, `tools/byte14_probe.py:176-186`

With RPS7200_DEBUG=1 (mandatory for Claude per CLAUDE.md), any scan taken with keep_raw=False is filed with the raw bytes and layout of whichever earlier pass last kept them, for example the last 300 dpi metering probe or the previous prescan. With no earlier keep_raw pass it is filed with none. Only ScanSession._file has a shape guard. The debug flush, tools/scan.py and scan_roll.py pass capture_record straight through.

**Evidence (from the code):**

```text
last_raw is assigned only in read_planes when `if keep_raw: self.last_raw = blob; self.last_raw_layout = {...}` (direct.py:1518-1532) and is never cleared by scan(). capture_record returns `"raw": self.last_raw, "raw_layout": self.last_raw_layout` (644-645), and _debug_capture spools `raw = record.get("raw")` (679-684). auto_exposure probes use `keep_raw=True` (2091), while scan() defaults to `keep_raw: bool = False` (2437). library.save writes whatever raw it is handed with no layout-vs-image check (library.py:186-211). byte14_probe's `finally` pass uses `keep_raw=False` right after keep_raw=True ladder passes.
```

**Failure scenario:** An ad-hoc script under RPS7200_DEBUG=1 runs `s.scan(resolution=1800, auto_exposure=True)`. The entry's raw.bin.gz is the 300 dpi probe's bytes while scan.tif is the 1800 dpi pass. reconstruct reports 'decode CHANGED: now (≈287,...), stored (...)', and the real pass's bytes were never kept. The byte14_probe 'final default pass' is filed with the last ladder pass's bytes.

**Fix:** Set last_raw/last_raw_layout to None at the start of every scan(). Force keep_raw=True whenever self.debug is on. In library.save, compare raw_layout lines/width/channels with the image shape and refuse (or drop and record why) on mismatch, so every caller is covered and not only the session.

<details><summary>Second reader's check</summary>

last_raw/last_raw_layout are assigned only under `if keep_raw:` in read_planes (direct.py:1518-1532) and are never cleared at the start of scan(). scan() defaults to keep_raw=False (2437). auto_exposure probe passes use keep_raw=True (2091), so `s.scan(resolution=1800, auto_exposure=True)` with keep_raw left at its default leaves the 300 dpi probe's bytes in last_raw. _debug_capture then spools `record.get("raw")` from capture_record (direct.py:672-684), and library.save writes whatever raw/raw_layout it is given, with no check against the image shape (library.py:186-211). Other reachable cases: verify_protocol's shot() (keep_raw not passed), byte14_probe:184 (`keep_raw=False`), and tools/scan.py with --no-library (keep_raw=args.library is not None). Only ScanSession._file has a shape guard (session.py:2072-2098), and that guard cannot tell two same-shaped passes apart.

</details>

<a id="transport-protocol-tp-04"></a>

### TP-04 -- Calibration raw bytes are discarded; only a reduced float reference is stored, and its provenance is not recorded

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1895-1973`, `rps7200/shading.py:75-91`, `rps7200/shading.py:109-182`, `rps7200/direct.py:572-629`, `rps7200/library.py:281-296`

The owner requires the exact bits needed to recompute everything. The correction input (about 1.66 MB of 16-bit calibration lines, dark and light phases interleaved) is reduced before storage by a heuristic that could itself be wrong. It cannot be re-derived, so any future fix to calculate_shading (the phase split, outlier lines, per-block drift) cannot be applied to a single stored scan. A reference reused from a different power-on is filed indistinguishably from a fresh one, although CLAUDE.md says the reference is per session.

**Evidence (from the code):**

```text
direct.py:1946-1966 `data = b"".join(collected)`, `self._shading = calculate_shading(data, width)`, `"data": data if keep_data else None` -- no caller in rps7200/ or tools/ passes keep_data. ShadingReference.save stores only `ref{c}`, `mean{c}`, `dark{c}`, `darkmean{c}`, pixels_per_line (shading.py:80-91), which are float64 means produced by a level-split heuristic (`split_ratio`, widest-gap cut, shading.py:158-178). The calibration info READ(128), shading descriptor, calibration-pass gain/offset, calibration CCD mask, block count, duration and time are logged only. ensure_shading(reuse=True) loads calibration/shading.npz (600-610), and nothing in scan meta or library.save says whether the reference was measured this power-on or loaded from disk.
```

**Failure scenario:** calculate_shading later turns out to mis-split a phase on some calibrations (a light line classed as dark). Every library entry's shading.npz carries the bad average, and `library.corrected()` can never be fixed for them. The GUI 'reuse' mode files a reference measured days earlier, and later analysis of striping cannot tell.

**Fix:** Always keep the calibration bytes. Store them once per calibration (e.g. calibration/<timestamp>/cal_raw.bin.gz plus a JSON with descriptor, info bytes, gain/offset, mask, blocks, duration and sha256), put that calibration id/sha256 in every scan's meta and entry, and copy or hard-link the raw calibration into each entry (or reference it by hash). Record action loaded/calibrated, source path and age in meta.

<details><summary>Second reader's check</summary>

calibrate_shading joins the calibration lines (`data = b"".join(collected)`, direct.py:1946), reduces them with calculate_shading (level-split with a widest-gap cut, shading.py:158-176), and returns `"data": data if keep_data else None`. A grep finds no caller in rps7200/ or tools/ that passes keep_data. ShadingReference.save stores only ref/mean/dark/darkmean/pixels_per_line (shading.py:80-91). The calibration info READ(128), the descriptor, the calibration gain/offset, the block count and the duration are logged only. ensure_shading(reuse=True) loads a stored reference (direct.py:600-610), and nothing in scan meta or library.save records whether the reference in force was measured this power-on or loaded from disk.

</details>

<a id="transport-protocol-tp-05"></a>

### TP-05 -- After a transport failure mid-read, the roll, the session and the tools keep sending commands (SLIDE NEXT, new scans) to a device left mid-scan

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/direct.py:2655-2687`, `rps7200/direct.py:3662-3686`, `rps7200/session.py:1313-1331`, `rps7200/direct.py:1440-1500`, `tools/byte14_probe.py:172-186`

CLAUDE.md and every docstring say an abandoned read costs a power cycle. Yet the code has no concept of 'device suspect'. A frame whose read died mid-payload (UsbError after clear_halt, a 120 s idle timeout, a refused READ) is treated as one bad frame. The roll then drives the transport (SLIDE_NEXT) and runs a full prescan/meter/scan sequence up to max_failures times. The GUI lets the operator press Scan again at once.

**Evidence (from the code):**

```text
scan(): `except BaseException: self._scanning = False; raise` (2679-2685) -- no drain, no marker. scan_roll: `except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc: failures += 1 ... yield RollFrame(...error...)` then falls through to `index += 1 ... if not keep_going(): return` -> `self.advance()` (3666-3686). read_planes raises ScanReadError after `idle_timeout` (1494-1498) and TimeoutError after `timeout` (1478-1481), both mid-scan. The session worker catches every exception and immediately takes the next job (session.py:1319-1331). `_scanning` is written but never read. byte14_probe's finally starts a new scan after a KeyboardInterrupt or CheckCondition.
```

**Failure scenario:** On an unattended 38-frame roll, frame 7's read stalls and raises UsbError. scan_roll yields the failed frame, sends SLIDE 04 01 00 01 to a device still mid-scan, then prescan and scan commands, three times over, each waiting out 30 s control timeouts. At best the rest of the roll is wasted; at worst the film is moved in an undefined state. In the GUI, 'failed' is shown, the operator presses Scan again, and the device (probably wedged) gets a full new sequence.

**Fix:** Classify exceptions raised between start_scan() and the end of read_planes() as 'read abandoned'. Set a sticky flag on DirectScanner that refuses every command except READ STATE/TEST UNIT READY until a new session (power cycle). scan_roll should stop the roll (not count a failure) on that class. ScanSession should emit a 'power-cycle needed' state and refuse new jobs until the operator acknowledges.

<details><summary>Second reader's check</summary>

scan() does `except BaseException: self._scanning = False; raise` (direct.py:2679-2685). scan_roll catches (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError), yields an error RollFrame and falls through to `index += 1 ... if not keep_going(): return`, and keep_going() calls `self.advance()`, a SLIDE_NEXT (direct.py:3433-3442, 3662-3686). read_planes raises ScanReadError on idle and TimeoutError on deadline mid-pass (1478-1498). The ScanSession worker catches every Exception and takes the next job (session.py:1319-1331). A grep shows `_scanning` is only ever assigned, never read. Several exceptions in the tuple (ShadingUnavailable before the pass, CalibrationRequired from start_scan) are raised outside the read, so the recommended classification of 'raised between start_scan and end of read' is the right one.

</details>

<a id="transport-protocol-tp-06"></a>

### TP-06 -- The lazy calibration inside scan(), recorded as stalling with LIBUSB_ERROR_PIPE, is still reachable, including from a GUI job queued behind a failed Calibrate

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `rps7200/direct.py:2560-2602`, `tools/scan_roll.py:430-446`, `tools/gui.py:1929-1932`, `rps7200/session.py:1381-1399`

scan_roll.py fixed its own call order but left the stalling path in the driver. Any scan()/prescan() with shading=True and no session reference still calibrates at that point: after READ STATE polling, TUR, a media READ STATE and three SET EXPOSURE plus three SET HIGHLIGHT writes. The GUI marks the session calibrated optimistically, so Prescan/Scan jobs queued behind a Calibrate that then fails or yields no reference run straight into it. The same applies to any ad-hoc script that skips ensure_shading.

**Evidence (from the code):**

```text
direct.py:2579-2592: `if self._shading is None or needed > self._shading.pixels_per_line: ... self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`. tools/scan_roll.py:436-440: 'Measured twice on this machine, that lazy calibration stalls: `bulk read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE`, right after the shading descriptor, and the device stops answering.' gui.py:1930-1932: `self.session.submit(Calibrate(...)); self.calibrated = True` -- set on submit, before the job has run.
```

**Failure scenario:** The operator presses Calibrate and then Prescan. The calibration raises (lamp timeout, refused start) or ends with no usable reference. The queued Prescan calls scan(shading=True), which calls calibrate_shading() from inside the scan setup, and the first calibration READ fails with LIBUSB_ERROR_PIPE. The device stops answering and needs a power cycle.

**Fix:** Remove the implicit calibration from scan(): raise ShadingUnavailable('no reference in this session; calibrate first') and let callers call ensure_shading() up front, as tools/scan.py and scan_roll.py now do. In the GUI, set `calibrated` only from the 'calibrated' event, and clear or cancel queued picture jobs when a Calibrate fails.

<details><summary>Second reader's check</summary>

scan() still calls `self.calibrate_shading()` whenever shading=True and `self._shading is None or needed > pixels_per_line` (direct.py:2579-2592). prescan() always passes shading=True (direct.py:1703-1714). GUI on_calibrate sets `self.calibrated = True` as soon as it submits (gui.py:1930-1932), and _needs_calibration returns False when calibrated is set (gui.py:1955), so a Prescan or Scan pressed before the 'calibrated' event arrives (gui.py:3236) is queued behind a Calibrate that may fail or run with mode 'off'. It then reaches the lazy path. More callers depend on this path than the finding lists: tools/hold_probe.py:133-136 prints '(a calibration runs first if this session has none)' and prescans with no ensure_shading; transport_truth.py:125 and roll_registration_walk also prescan without ensuring shading; and uniformity.py:703-706 runs auto_exposure (shaded scan() probes) on a session that has no reference. The stall evidence is a comment in tools/scan_roll.py:436-440, not something observable in code, but the path is plainly reachable.

</details>

<a id="transport-protocol-tp-08"></a>

### TP-08 -- A mid-payload pause is waited out only if the device sends a zero-length packet; a silent pause raises at once, after clear_halt, abandoning the read

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/usb_transport.py:636-661`, `rps7200/usb_transport.py:743-770`, `rps7200/usb_transport.py:72-81`, `tests/test_transport_reads.py:27-55`

PARTIAL_READ_TIMEOUT_S (120 s, 'generous: the alternative ... costs a power cycle') protects only against ZLPs. If the bridge simply sends nothing, libusb returns LIBUSB_ERROR_TIMEOUT with 0 bytes after the bulk timeout. The read then raises immediately and sends CLEAR_FEATURE(HALT) in the middle of a payload. The bulk timeout is 30 s for every command except read_lines (120 s). Before the first byte, the same case raises UsbError instead of NoDataYet, so the 'not scanned yet' retry logic does not run either.

**Evidence (from the code):**

```text
_bulk_read_into: `if rc < 0 and not (rc == LIBUSB_ERROR_TIMEOUT and transferred.value > 0): try: self.clear_halt() ... raise UsbError(...)` -- a TIMEOUT with 0 bytes raises. _read_payload's stall loop only handles `if n == 0:` (a successful zero-length transfer): `if now - stalled_since > PARTIAL_READ_TIMEOUT_S: raise`. The tests replace `_bulk_read_into` with a script that returns 0 for a pause (`n = self.script.pop(0) if self.script else 0 ... return n`), so the real TIMEOUT path is never exercised.
```

**Failure scenario:** On an untied infrared pass or a slow 7200 dpi pass, the device goes silent for longer than the bulk timeout partway through a READ window. The transport raises 'bulk read ... failed after N bytes: LIBUSB_ERROR_TIMEOUT', clears the halt and propagates up. scan() re-raises without draining, and the read is abandoned mid-scan.

**Fix:** Treat LIBUSB_ERROR_TIMEOUT with 0 bytes like n == 0: NoDataYet before any byte, and a stall-clock tick after partial delivery. Do not clear_halt on a timeout. Add tests that drive the real `_bulk_read_into` against a fake `_lib.libusb_bulk_transfer` returning TIMEOUT/0.

<details><summary>Second reader's check</summary>

_bulk_read_into raises UsbError after clear_halt() for any rc<0 except TIMEOUT with transferred>0 (usb_transport.py:636-661). _read_payload's NoDataYet and stall-wait logic runs only when `n == 0`, that is a successful zero-length transfer (743-770). A silent TIMEOUT with 0 bytes therefore raises a hard UsbError, both before any byte (instead of NoDataYet) and mid-payload (instead of waiting out PARTIAL_READ_TIMEOUT_S). tests/test_transport_reads.py replaces _bulk_read_into with a script that returns counts, so the libusb TIMEOUT path is never exercised. Image READs use timeout_ms=120000 (direct.py:1403), so the failure needs more than 120 s of silence. Command-path reads use the 30 s default.

</details>

<a id="transport-protocol-tp-09"></a>

### TP-09 -- INFRARED_FLOOR_S guards no timeout; the real read guards are 120 s, below the documented 212-227 s untied-IR floor

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P16](../problems/P16-timeouts-and-runtime-budget.md)

**Where:** `rps7200/session.py:55-63`, `rps7200/direct.py:1398-1418`, `rps7200/direct.py:1440-1500`, `rps7200/usb_transport.py:75`, `CLAUDE.md:363-367`, `README.md:458-459`, `tools/uniformity.py:683-688`

CLAUDE.md:365-366 says '212 s survives in the code as INFRARED_FLOOR_S because it guards a timeout', and README.md:459 repeats it. No timeout uses it. Whether an untied infrared pass (still reachable with --no-fast-ir, and from the GUI when fast IR is unticked) survives depends on the device never going silent for more than 120 s in one stretch. Measured runs suggest it does not, but nothing in the code enforces the documented margin.

**Evidence (from the code):**

```text
`INFRARED_FLOOR_S = 212.0` (session.py:63) is referenced only by tests/test_session.py:667,674. The actual guards: read_planes `idle_timeout: float = 120.0` (direct.py:1447, NoDataYet idle -> `raise ScanReadError(f"no data for {idle_timeout:.0f}s ...")`), `PARTIAL_READ_TIMEOUT_S = 120.0` (usb_transport.py:75), read_lines `timeout_ms: int = 120_000` (1403). scan() does not expose idle_timeout. uniformity.py:686: 'if it stalls, plumb idle_timeout through scan() rather than retrying'.
```

**Failure scenario:** On a --no-fast-ir 300 dpi RGBI pass, the device answers READs with ZLPs while it spends its ~220 s floor. After 120 s, read_planes raises ScanReadError mid-scan: the read is abandoned, the device wedges and needs a power cycle. This is the incident CLAUDE.md describes with a 60 s timeout, now with a 120 s one.

**Fix:** Derive idle_timeout/PARTIAL_READ_TIMEOUT_S for infrared passes from INFRARED_FLOOR_S plus a margin (e.g. max(120, 1.5 x floor) when infrared and not fast_infrared), and plumb it through scan(). Otherwise correct CLAUDE.md and the README so they stop claiming the constant guards anything.

<details><summary>Second reader's check</summary>

`INFRARED_FLOOR_S = 212.0` (session.py:63) is referenced only by tests/test_session.py:667,674. No timeout uses it. The real guards are read_planes `idle_timeout: float = 120.0` (direct.py:1447, 1494-1498), `PARTIAL_READ_TIMEOUT_S = 120.0` (usb_transport.py:75) and read_lines `timeout_ms=120_000` (direct.py:1403). scan() does not expose idle_timeout. CLAUDE.md:365-367 ('212 s survives in the code as INFRARED_FLOOR_S because it guards a timeout') and README.md:458-459 are contradicted by the code. Whether an untied IR pass is actually silent for more than 120 s is not established in code, so medium is right.

</details>

<a id="transport-protocol-tp-10"></a>

### TP-10 -- force_abort closes the libusb handle and context from the UI thread while the worker is inside a transfer; a crash also loses queued library entries

**Severity** medium · **Category** concurrency · **Verdict** partly · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/session.py:1224-1244`, `rps7200/usb_transport.py:558-567`, `rps7200/session.py:1043`, `tools/gui.py:3025-3037`

force_abort closes the libusb handle and exits the context from the UI thread with no lock while the worker is mid-transfer. The ABORT dialog warns that the window may crash, but not that frames already scanned and still queued or gzipping in the daemon FrameWriter are lost if it does.

**Evidence (from the code):**

```text
force_abort (UI thread): `transport = getattr(scanner, "t", None) ... transport.close()`. Transport.close: `_lib.libusb_release_interface(...); _lib.libusb_close(self._handle); self._handle = None ... _lib.libusb_exit(self._ctx)` with no lock. The worker's primitives check `self._require()` and then pass `self._handle` in a separate expression (e.g. 582-593), so a close in between hands NULL to libusb_control_transfer. FrameWriter runs as a `threading.Thread(target=self._run, daemon=True)`.
```

**Failure scenario:** During a roll, the operator types ABORT while frame 12 is reading and frames 10-11 are still in the FrameWriter queue. libusb_exit races the bulk transfer, the process segfaults, and frames 10-11 (already scanned, raw bytes in memory) are never filed.

**Fix:** Give Transport a lock and a `cancel` that uses libusb_cancel_transfer, or at least close only the handle and never libusb_exit from another thread. Make FrameWriter non-daemon and flush it before a force-close. State in the ABORT dialog that queued frames are at risk.

<details><summary>Second reader's check</summary>

force_abort does call transport.close() from the UI thread (session.py:1224-1244), which runs libusb_release_interface/libusb_close/libusb_exit with no lock (usb_transport.py:558-567) while the worker may be inside libusb_bulk_transfer or control_transfer on the same handle, so the race is real. The claim that the dialog does not mention a crash is wrong: gui.py:3027-3031 says 'It can also take this window down with it.' What the dialog does not say is that frames already scanned and queued in the daemon FrameWriter (session.py:1043) die with the process.

</details>

<a id="transport-protocol-tp-12"></a>

### TP-12 -- The calibration read loop can end silently mid-calibration and lets non-ScanReadError failures escape

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `rps7200/direct.py:1915-1944`, `rps7200/direct.py:830-863`, `rps7200/direct.py:1398-1438`

Three problems in the loop. (1) A NoDataYet or UsbError from the per-block READ GAIN/OFFSET escapes the loop and abandons the calibration mid-read. That is the same shape as the 2026-09-13 wedge the _query docstring describes, with IndexError swapped for NoDataYet. (2) Any refusal that is not end-of-data (read_lines turns it into ScanReadError) is logged as 'scanner finished' and produces a reference from a partial set of lines, with no check of the block count against the descriptor's 40. (3) When the 300 s deadline expires while lines are still coming, the loop exits normally, sends COPY and walks away from the pass without any error.

**Evidence (from the code):**

```text
`while time.monotonic() < deadline: try: self.set_gain_offset(self.get_gain_offset()) except (CheckCondition, ScanReadError): pass; try: chunk = self.read_lines(4, bpl, retries=1) except NoDataYet: ... continue except (EndOfData, ScanReadError): self._log(f"  scanner finished after {blocks} blocks"); break ...` then `mask = self.get_ccd_mask(width)` in the same try, and `finally: self.finish_scan()`. _query catches only CheckCondition, so a NoDataYet or UsbError from get_gain_offset escapes.
```

**Failure scenario:** A calibration at resolution=7200 (allowed) or on a slow device runs past 300 s. The loop exits, COPY is sent while calibration lines are pending, and the device is left mid-pass. Alternatively a transient refusal at block 12 yields a 12-block reference that corrects badly, and nothing in the entry says so.

**Fix:** Catch UsbError subclasses in the per-block gain/offset call, or skip that call while data is pending. Treat only EndOfData as completion, and treat a ScanReadError as a failure. On deadline, raise instead of falling through. Record blocks/bytes/duration with the reference and refuse a reference with fewer blocks than declared.

<details><summary>Second reader's check</summary>

In the calibration loop (direct.py:1915-1944) the per-block `set_gain_offset(self.get_gain_offset())` catches only (CheckCondition, ScanReadError). A NoDataYet or other UsbError from the bulk read inside _query (which catches only CheckCondition, 849-862) escapes, and the pass is abandoned (finally: finish_scan). `except (EndOfData, ScanReadError)` logs 'scanner finished' for any refusal, and nothing checks the block count. When `while time.monotonic() < deadline` expires the loop exits normally, reads the mask and calls finish_scan with no error, and calculate_shading then builds a reference from whatever lines arrived.

</details>

<a id="transport-protocol-tp-13"></a>

### TP-13 -- GUI single Scan/Prescan gzips library entries with the device open and idle, contrary to the documented rule

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:1013-1020`, `rps7200/session.py:1080-1100`, `rps7200/session.py:1455-1458`, `CLAUDE.md:160-163`, `README.md:500-503`

For a roll the device is busy while the writer works. For a single GUI scan or prescan there is no next pass: the session holds the device open and idle for the whole gzip of a 60-140 MB entry, which is the state CLAUDE.md names as a wedge precursor. The CLAUDE.md/README statement that single scans are compressed only after close() is true only for DirectScanner's debug filing, which the session disables (`debug=False`, session.py:1270).

**Evidence (from the code):**

```text
FrameWriter docstring: 'On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.' _scan/_prescan call `self._file(...)`, which calls `self._writer.submit(...)`, and the writer thread runs library.save, which gzips raw.bin.gz (library.py:192) while the worker waits for the next job with the transport still open. CLAUDE.md:160: 'A single scan compresses nothing while the device is open... because gzipping one with the scanner open and idle preceded a wedge once.'
```

**Failure scenario:** The operator takes a 3600 dpi RGBI scan in the window. The worker returns to idle with the device open while the writer gzips about 140 MB for tens of seconds. This is the documented precursor to a wedge, and the next job may then find the device unresponsive.

**Fix:** For non-roll jobs, spool uncompressed during the session and gzip after close(), as the debug path does, or write raw.bin uncompressed and compress later. Otherwise measure the hazard with tools/filing_load_test.py for the idle case and correct CLAUDE.md/README.

<details><summary>Second reader's check</summary>

_scan and _prescan submit to FrameWriter (session.py:2121-2141), and its thread runs library.save, which gzips raw.bin.gz (library.py:192), while the worker sits in `self._jobs.get()` with the device open (session.py:1313-1316). The FrameWriter docstring's justification ('overlaps the next frame's scan') holds only for rolls. CLAUDE.md:160-163 ('A single scan compresses nothing while the device is open') describes DirectScanner debug filing only, and the session disables that (session.py:1270). For GUI single scans the documented hazard state is reached.

</details>

<a id="transport-protocol-tp-14"></a>

### TP-14 -- 7200 dpi stagger realignment is baked into the 'raw' scan.tif without being recorded, and the session then drops the raw bytes

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/session.py:2072-2098`, `rps7200/library.py:411-436`, `rps7200/library.py:493-497`, `rps7200/direct.py:1501-1503`

(a) The 7200 dpi scan.tif is not the decode; it has a 4-line even/odd shift applied, and the entry does not say so. (b) The GUI's guard therefore drops raw.bin.gz for every 7200 dpi scan, the costliest scans (about 570 MB of bytes). (c) decode_raw and reconstruct report every such entry as changed. The same guard drops the raw bytes of any pass cut short by EndOfData, because the layout declares params.lines while the decode truncates. That is the pass where the bytes matter most for diagnosis.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: image = self._realign_native_column_stagger(image)` and then `raw_pixels = image`, so height is lines-4. No meta key records it (meta dict 2760-2804), and nothing in library.py or tools/ refers to the stagger. session._file compares `layout["lines"]` with `image.shape[0]` and on disagreement drops the bytes: `capture = dict(capture, raw=None, raw_path=None, raw_layout=None)`. reconstruct: `if image.shape != stored.shape: return image, (f"decode CHANGED: ...")`.
```

**Failure scenario:** A GUI scan at 7200 dpi (shading off, since shading is refused there) logs 'raw bytes do not describe this image (lines 6888 vs 6884)' and files no raw bytes. A tools/scan.py 7200 entry keeps its bytes, but reconstruct calls it 'decode CHANGED' and migrate-raw would overwrite scan.tif with the unrealigned decode.

**Fix:** Store the pure decode in scan.tif and apply the realignment in library.corrected(), or record `native_stagger_realigned: 4` in meta and have decode_raw/reconstruct apply it. Make the session guard compare against layout['lines_received'] (after truncation) plus any recorded realignment. Record `lines_expected` and `truncated` explicitly in meta.

<details><summary>Second reader's check</summary>

At resolution 7200, scan() applies `_realign_native_column_stagger` (direct.py:2695-2701) before `raw_pixels = image`, which trims 4 lines. No meta key records this. raw_layout keeps `lines: params.lines`, so session._file's guard (`layout[k] != actual[k]` for lines) drops raw bytes for every GUI 7200 dpi pass (session.py:2072-2098). reconstruct compares shapes and reports 'decode CHANGED' (library.py:493-497) for tools/scan.py 7200 entries, and decode_raw (used by migrate-raw) returns the unrealigned decode. A pass cut short by EndOfData likewise has layout lines != decoded height, and the session guard drops its bytes too.

</details>

<a id="transport-protocol-tp-15"></a>

### TP-15 -- Per-pass command and response state needed to re-derive a pass is not recorded

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2760-2804`, `rps7200/library.py:237-264`, `rps7200/direct.py:1057-1118`, `rps7200/direct.py:1350-1374`, `rps7200/direct.py:1387-1396`, `rps7200/direct.py:2647`, `tools/byte14_probe.py:142-151`, `rps7200/usb_transport.py:67`

The owner wants every parameter, command and state stored. The driver sends several fields it never records: byte14 (overridden by byte14_probe and verify_protocol), slide_init_param (verify_protocol stage 5) and light. Sense events that explain a pass's behaviour (start_scan retries, cmd_17 conditions) exist only in the log. Entries from byte14_probe under debug filing cannot be told apart by the one variable the probe varies.

**Evidence (from the code):**

```text
The meta keys are resolution, channels, film, protocol_revision, shading, depth, frame, width, height, bytes_per_line, filter_offsets, exposure, gain, offset, exposure_scale, exposure_metered, fast_infrared, duration_s, read_direction and carriage_state. Missing: the MODE SELECT byte14 (`data[14] = byte14 if byte14 is not None else self.byte14_for(passes)`), the quality word and skip_shading, halftone and line threshold, `slide_init_param`, `light` (`data[15] = s.light`), extra_entries/double_times and the IR flag bytes 16/27, the raw 123-byte READ GAIN/OFFSET response (bytes 66-68 are live offsets), the raw 18-byte PARAM (available_lines, bytes 8-13 and 16-17), READ STATE after the pass, every CHECK CONDITION/sense seen during the pass, and MAX_WINDOW/batch. library.save then keeps only a whitelisted subset of meta.
```

**Failure scenario:** byte14_probe runs with RPS7200_DEBUG=1. Every ladder entry is filed with identical meta apart from timing, so a later offline analysis of the timing effect of byte 14 cannot say which entry had which value. A pass that needed 5 SCAN retries for UNIT ATTENTION 0x82 looks identical to one that started at once.

**Fix:** Record the exact bytes sent and received per pass: mode_select hex, scan_frame hex, gain_offset_write hex, gain_offset_read hex, param hex, slide_init hex, read_state_before/after hex, and a list of {opcode, sense hex, t} for every CHECK CONDITION, plus the transport window and batch. Add them to library.save's record verbatim (a 'wire' section) instead of whitelisting.

<details><summary>Second reader's check</summary>

The scan meta (direct.py:2760-2804) lacks MODE SELECT byte14, the quality word, slide_init_param, light, the IR flag bytes 16/27 written by set_gain_offset (1350-1374), the raw READ GAIN/OFFSET and PARAM responses (parsed at 1334-1346 and 1387-1396 and then discarded), READ STATE after the pass, and any sense events. library.save then whitelists meta keys (library.py:237-264), so even extra meta keys are dropped. byte14/slide_init_param are real scan() arguments (2440-2448) that probes vary.

</details>

<a id="transport-protocol-tp-16"></a>

### TP-16 -- INQUIRY (device identity and firmware) is never stored in any library entry

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:131-147`, `rps7200/library.py:213-307`, `rps7200/direct.py:798-828`, `rps7200/direct.py:719-729`, `tools/check_scanner.py:188-195`

check_scanner warns that 'Everything measured in docs/ came off 1.70', yet no entry records the firmware, model, CCD size or frame of the device that produced it. The raw INQUIRY bytes are not kept anywhere, so a later re-parse (for example if the pieusb offsets turn out wrong for a field) is impossible.

**Evidence (from the code):**

```text
library.save signature has `inquiry: Any = None`, and the parameter is never used in the body (grep shows only line 142). Callers pass it: direct.py:728 `inquiry=self._inquiry`, tools/scan.py `inquiry=info`, session.py `inquiry=getattr(self._scanner, "_inquiry", None)`. inquiry() parses fixed offsets into a dataclass and discards `d` (the raw bytes).
```

**Failure scenario:** The scanner is swapped or its firmware updated. Entries from the two devices are mixed in the library, and nothing lets a reconstruction or statistical analysis separate them.

**Fix:** Store `inquiry` in the record: the raw hex plus the parsed fields. Keep the raw bytes on Inquiry (a `raw` field) so they can be re-parsed later.

<details><summary>Second reader's check</summary>

library.save accepts `inquiry: Any = None` (library.py:142) and never uses it in the body: the record has no inquiry key, and provenance() records only git/python versions (library.py:67-102). inquiry() parses fixed offsets and discards the raw buffer `d` (direct.py:798-828). No entry records firmware, model or device identity.

</details>

<a id="transport-protocol-tp-18"></a>

### TP-18 -- Calibration never checks the measured media flag, and tools/uniformity.py calibrates right after telling the operator to empty the transport

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P17](../problems/P17-calibration-without-asking.md)

**Where:** `rps7200/direct.py:1805-1813`, `rps7200/protocol.py:555-575`, `tools/uniformity.py:700-743`, `CLAUDE.md:272-280`

The one signal that can say 'empty' (byte 8) is decoded but not used as a guard or a warning. One tool leads the operator directly into the state CLAUDE.md names as a wedge precursor.

**Evidence (from the code):**

```text
calibrate_shading reads state only for `if not self.read_state().warming_up: break`. State.no_media (byte 8, 'measured, confirmed' 1 = empty) is never consulted before calibrating. uniformity.py:700-702 prints 'Metering on the EMPTY transport' and asks `confirm("    press Enter with the transport empty: ")`, and then at 729-735 runs 'Calibrating shading (3-4 minutes) ...' with `result = scanner.calibrate_shading()` and no prompt to load film. CLAUDE.md:279-280: 'Calibrating an empty transport is a state the vendor never creates, and doing it once preceded a wedge.'
```

**Failure scenario:** The operator follows uniformity.py's prompts: empty transport for metering, Enter, and the tool calibrates the empty transport straight away. This is the documented pre-wedge state.

**Fix:** In calibrate_shading, read state and, when `no_media` is set, refuse (or require an explicit `allow_empty=True`) with a message to load film. In uniformity.py, prompt to load film before calibrating, or calibrate before emptying the transport.

<details><summary>Second reader's check</summary>

calibrate_shading reads state only for warming_up (direct.py:1805-1813) and never consults State.no_media (protocol.py:571-574). uniformity.py:700-706 asks for an EMPTY transport and then runs auto_exposure, whose shaded probe scans trigger scan()'s lazy calibration (direct.py:2579-2592) on that empty transport when the session has no reference. The explicit calibrate_shading at 729-735 follows with no prompt to load film. So the documented wedge precursor is reached twice, once through the lazy path before the explicit calibration.

</details>

<a id="transport-protocol-tp-19"></a>

### TP-19 -- Ctrl-C during tools/scan_roll.py loses queued frames and closes the transport under the read

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/scan_roll.py:342-353`, `tools/scan_roll.py:587-605`, `rps7200/direct.py:777-794`

A KeyboardInterrupt unwinds past writer.finish(). The daemon FrameWriter thread dies at interpreter exit, taking the frames queued or being gzipped (up to depth 2 plus one in progress). The `with` block's close() also releases the device during an active read, which is the abandoned-read wedge. No tool installs a SIGINT handler that defers to a safe point.

**Evidence (from the code):**

```text
`try: with DirectScanner(verbose=args.verbose, debug=False) as s: ... except Exception as exc: trouble = exc` and then `writer.finish()` at 605. The comment at 598 says 'Reached through a `finally` around the whole scanning block, so it runs whatever came out of it', but it is `except Exception`, which does not catch KeyboardInterrupt. `DirectScanner.__exit__` calls `self.t.close()` (libusb close/exit), including mid-read.
```

**Failure scenario:** An operator presses Ctrl-C to stop a long roll. The current read is abandoned (power cycle needed) and the previous two frames, already scanned and queued for filing, are never written.

**Fix:** Use try/finally for writer.finish(). Install a SIGINT handler that sets should_stop (cooperative stop at the next advance), and only a second Ctrl-C forces. Make FrameWriter non-daemon.

<details><summary>Second reader's check</summary>

tools/scan_roll.py wraps the scanning block in `try: ... except Exception as exc: trouble = exc` (342-593), and writer.finish() comes after it (605). A KeyboardInterrupt is not an Exception, so it skips finish(), and the daemon FrameWriter thread (session.py:1043) dies at interpreter exit with its queued frames. The comment at 598-604 says the call is 'Reached through a `finally`', which the code contradicts. On the way out, scan()'s `except BaseException` re-raises and DirectScanner.__exit__ closes the transport (direct.py:777-794) with the pass unfinished.

</details>

<a id="transport-protocol-tp-20"></a>

### TP-20 -- usbpcap.packets() returns every record's payload, keyboard interrupt (keystroke) payloads included, although the docs say it never does

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P32](../problems/P32-usbpcap-returns-keystrokes.md)

**Where:** `rps7200/usbpcap.py:8-18`, `rps7200/usbpcap.py:103-127`, `tests/test_usbpcap.py:137-141`, `CLAUDE.md:488-492`

The privacy line is held by callers (parse_capture filters by scanner device and wValue), not by the module. The public iterator hands HID keystrokes to any new tool, and the keystroke test only checks callers that filter for device 7 themselves.

**Evidence (from the code):**

```text
packets() yields `Packet(device=..., transfer=data[22], ..., payload=data[header_len:])` for every EPB, with no filtering by transfer type or device. The test asserts `[p.transfer for p in got].count(INTERRUPT) == 2`, meaning keystrokes are returned. The module docstring says 'It never returns an interrupt-endpoint payload ... That is not a comment, it is what `setups` and `scanner_devices` actually do', and CLAUDE.md:490 says 'That reader only ever returns control setup packets and payloads for a device the caller named; it never returns an interrupt payload.'
```

**Failure scenario:** Someone writes a quick 'dump all payloads' diagnostic with usbpcap.packets() and pastes the output into an issue or commit. The keystrokes from the capture machine (possibly passwords) are published.

**Fix:** Make packets() private (_packets), or have it drop INTERRUPT/ISOCHRONOUS payloads (return payload=b'' for them) unless a device is named. Correct the docstring and CLAUDE.md, and add a test that the public API never yields an interrupt payload.

<details><summary>Second reader's check</summary>

packets() (usbpcap.py:103-127) yields every EPB record's payload with no filter on transfer type or device. tests/test_usbpcap.py:138-141 asserts it returns two INTERRUPT (keystroke) records. The module docstring (usbpcap.py:8-18) says 'It never returns an interrupt-endpoint payload' (scoped to setups/scanner_devices, which is accurate for those two). CLAUDE.md:488-492 says 'That reader only ever returns control setup packets and payloads for a device the caller named; it never returns an interrupt payload', and packets(), a public import used by parse_capture, contradicts that.

</details>

<a id="transport-protocol-tp-21"></a>

### TP-21 -- The demo accepts 7200 dpi (and any >3600 dpi shaded pass) that DirectScanner refuses, and its position() never returns None

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:530-597`, `rps7200/demo.py:455-477`, `rps7200/demo.py:337-338`, `rps7200/direct.py:2560-2578`, `rps7200/direct.py:1234-1245`, `tools/gui.py:118`

CLAUDE.md: 'A refusal belongs in the stand-in's answers'. The real backend refuses a shaded 7200 dpi pass through the failed-job path; the demo simulates a ~314 s scan and files an entry. The demo also never produces the empty READ STATE that the real device gives after every SLIDE, so the None handling in _ask_position, rewind, place_on_strip and _whole_frames (see TP-11) is never exercised with no device on the bus.

**Evidence (from the code):**

```text
DirectScanner.scan: `if needed > self.MAX_SHADING_COLUMNS: raise ShadingUnavailable(...)` before the pass. DemoScanner.scan/prescan refuse only `if infrared and not supports_infrared(film)`, with no column check. GUI `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`. DemoScanner.position: `return self._position`, never None, whereas the real position() docstring says 'a READ_STATE issued right after a transport command comes back empty every time'.
```

**Failure scenario:** The GUI is judged in --demo mode: 7200 dpi with shading 'works', and the operator later finds the real scanner refuses it. A bug in the None-position paths (TP-11) cannot be reproduced in the demo.

**Fix:** Have DemoScanner.scan/prescan call `DirectScanner._shading_columns_needed(frame or FULL_FRAME, resolution)` against `DirectScanner.MAX_SHADING_COLUMNS` and raise ShadingUnavailable exactly as the driver does. Model an empty READ STATE for the first read after each advance/retreat/nudge.

<details><summary>Second reader's check</summary>

DirectScanner.scan raises ShadingUnavailable before the pass when `needed > self.MAX_SHADING_COLUMNS` (direct.py:2565-2578). DemoScanner.scan/prescan (demo.py:455-597) refuse only infrared on non-IR film, and GUI DPI_LADDER includes 7200 (gui.py:118). DemoScanner.position returns `self._position` and never None (demo.py:337-338), so the None branches in _whole_frames, rewind and _ask_position never run in the demo. The demo also never runs the lazy-calibration branch that the real scan() takes when there is no reference.

</details>

<a id="transport-protocol-tp-a1"></a>

### TP-A1 -- A pass that fails during or after its read is never filed, even in debug mode: its partial raw bytes are discarded

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/direct.py:1466-1516`, `rps7200/direct.py:2677-2685`, `rps7200/direct.py:2715-2728`, `rps7200/direct.py:2808`

The passes that matter most for diagnosis (a read that stalled, a pass that came back with an unexpected width) leave nothing in the library or in the debug spool. Their bytes, the mask read for that pass, the params and the lines received exist only in memory and are dropped. CLAUDE.md's 'file every scan' rule and the debug mode both assume a successful return.

**Evidence (from the code):**

```text
read_planes accumulates `chunks: list[bytes] = []` locally, and they become last_raw only after the loop (`blob = b"".join(chunks); if keep_raw: self.last_raw = blob`). ScanReadError/TimeoutError/UsbError raised inside the loop discard them. scan(): `except BaseException: self._scanning = False; raise`, and `self._debug_capture(raw_pixels, meta)` is reached only on a normal return. The post-read `raise ShadingUnavailable(f"this pass came back {params.width} columns, wider than ...")` also comes after a complete read and before any capture.
```

**Failure scenario:** Under RPS7200_DEBUG=1 a 3600 dpi pass stalls at 60% (ScanReadError 'no data for 120s'). Around 150 MB of received bytes are discarded, and so is the evidence of where the stall happened. The log line is all that remains.

**Fix:** In read_planes, keep the partial blob and layout (with lines_received and an `aborted` reason) on self even when raising, for example in a finally. In scan(), on exception, spool a failure record (partial raw, mask, params, meta with the exception text) to the debug spool, and expose it via capture_record so tools and the session can file it tagged 'failed'.

<a id="transport-protocol-tp-a2"></a>

### TP-A2 -- read_planes gives a CHECK CONDITION a single attempt, so one queued one-shot sense aborts a pass mid-scan

**Severity** medium · **Category** hardware-safety · **Verdict** found-by-verifier · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `rps7200/direct.py:1473`, `rps7200/direct.py:1419-1437`, `rps7200/direct.py:830-848`

The retry that read_lines exists to provide is switched off on the image path. Any CHECK CONDITION on an image READ that is not end-of-data (a queued UNIT ATTENTION, for example) raises ScanReadError out of scan() with the pass still running, which is the abandoned-read state the driver documents as a wedge. A CHECK is answered before any data moves, so re-issuing the READ is safe.

**Evidence (from the code):**

```text
read_planes: `chunk = self.read_lines(n, bpl, retries=1)`. read_lines: `for _ in range(retries): try: return self.t.command(...) except CheckCondition: last = self.read_sense() ...` then `if last.end_of_data: raise EndOfData(...)` / `raise ScanReadError(f"reading {lines} lines ... was refused: {last}")`. The read_lines docstring itself says: 'a queued one-shot sense condition is reported against whichever command arrives next, so the first attempt can be rejected for something that has nothing to do with this read.'
```

**Failure scenario:** During a 1800 dpi RGBI scan a one-shot sense is queued (as start_scan's retries show happens) and reported on READ #40. read_planes raises ScanReadError, scan() re-raises with no drain, and the device is left mid-pass. In a roll, scan_roll then counts a failure and sends SLIDE_NEXT (see TP-05).

**Fix:** Call read_lines with retries>=3 from read_planes (and from the calibration loop), keeping EndOfData as the terminating case. Log each absorbed sense and record it in meta.

<a id="transport-protocol-tp-a3"></a>

### TP-A3 -- tools/scan.py holds every pass in memory and files them only after the with-block, so an exception mid-bracket loses all completed passes

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/scan.py:159-235`, `tools/scan.py:237-247`

on_pass hands every bracket pass to hold() as it lands, precisely because 'only one pass's raw bytes survive', but the filing loop only runs if the whole with-block returns normally. A failure in pass N (ScanReadError, CalibrationRequired, KeyboardInterrupt) propagates out of main(), and passes 1..N-1 (pixels plus raw bytes in RAM) are never written.

**Evidence (from the code):**

```text
`pending: list[dict] = []` ... `def hold(image, meta, capture): ... pending.append(dict(capture, inquiry=info, meta=meta, image=image if raw is None else raw))` inside `with DirectScanner(verbose=args.verbose, debug=False) as s:`. Filing comes after the block: `entries = []\n    for held in pending:\n        entries.append(library.save(...))`. There is no try/finally around the scanning.
```

**Failure scenario:** `tools/scan.py --bracket 5 --dpi 3600` with debug=False: pass 4 fails with a read timeout. Three completed 3600 dpi passes (hundreds of MB of raw bytes, minutes of scanner time) disappear, and the tool exits with a traceback and no entries.

**Fix:** Wrap the scanning block in try/finally, and file `pending` in the finally after the device has closed (as scan_roll's writer.finish() intends), or spool each held pass to disk as debug mode does.

<a id="transport-protocol-tp-a4"></a>

### TP-A4 -- DemoScanner has no last_pixels_raw, so demo sessions file corrected pixels labelled raw and the session's raw-vs-corrected path never runs in the demo

**Severity** medium · **Category** demo-divergence · **Verdict** found-by-verifier · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:955-1021`, `rps7200/demo.py:435-443`, `rps7200/session.py:1415`, `rps7200/session.py:1085-1092`

The real DirectScanner separates the returned (corrected) pixels from last_pixels_raw, and the session's prescan filing depends on that attribute. The stand-in does not provide it, so every demo prescan and scan entry (in demo/library) holds shading-corrected pixels that claim to be raw, and library.corrected() corrects them a second time. The demo is how the window is judged with no device, yet it cannot expose TP-01 or confirm its fix, because both demo code paths file the same wrong thing.

**Evidence (from the code):**

```text
demo._decode: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)` and returns the corrected image with `{"reference": reference, "ccd_mask": mask, "raw": raw, "raw_layout": layout}` as the capture. A grep shows no `last_pixels_raw` in demo.py, so session._prescan's `raw_image = getattr(self._scanner, "last_pixels_raw", None)` is None, and FrameWriter files `job["image"]`, the corrected pixels, with the reference beside them and corrections_applied [].
```

**Failure scenario:** `make run-demo`: prescan and scan, then open the filed entry's full-resolution view. corrected() applies shading twice. `tools/library.py reconstruct --root demo/library` reports 'decode CHANGED' for every entry. A future fix to _scan cannot be checked in the demo, because raw_image stays None there.

**Fix:** Give DemoScanner a `last_pixels_raw` set to the pre-apply_shading decode (and `last_scan_meta` carrying the same shading report the real scan() records), so the seam matches DirectScanner's contract. Add a session test on the demo backend asserting filed scan.tif == library.decode_raw(entry).

<a id="transport-protocol-tp-07"></a>

### TP-07 -- Calibration sends an extra, vendor-absent CAL INFO prepare+READ(32) with a different payload, between SLIDE INIT and SCAN

**Severity** low · **Category** hardware-safety · **Verdict** partly

**Where:** `rps7200/direct.py:1001-1006`, `rps7200/direct.py:1818-1826`, `rps7200/direct.py:1873-1894`, `docs/protocol.md:60`

calibrate_shading sends a second, differently framed CAL INFO prepare plus READ(32) (get_shading_parms) right before SCAN, which the vendor sequence does not contain. The working explicit calibration path sends it too, so it is an undocumented protocol divergence and not the demonstrated cause of the lazy-calibration stall.

**Evidence (from the code):**

```text
get_shading_parms: `prep = bytearray(6); prep[0] = SUB_CALIBRATION_INFO | 0x80` -> payload `95 00 00 00 00 00`, then `self._query(_cmd(SCSI_READ, 32), 32, ...)`. calibrate_shading already sent `prep[0:2] = (SUB_CALIBRATION_INFO | 0x80).to_bytes(2, "little"); prep[2:4] = (2).to_bytes(2, "little")` -> `95 00 02 00 00 00` + READ(128) (1818-1823). get_shading_parms is called at 1875, after MODE SELECT calibrate (1847), SLIDE INIT (1854) and wait_ready (1855) and just before start_scan (1894). docs/protocol.md:60: '`0x95` CAL INFO, prepare read | 2' across all captures.
```

**Failure scenario:** On a calibration, the 32-byte descriptor read sits between SLIDE INIT and SCAN. If the firmware treats the second, differently-framed 0x95 as a state change, the first calibration READ stalls (PIPE). That is a wedge, and it depends on command order rather than on anything the operator did.

**Fix:** Take the column count from the 128-byte calibration info already read, or from the frame, and drop the second prepare/READ. If the descriptor is needed, send it with the same framing as the first and in the vendor's position. Verify against the capture sequence with parse_capture before changing, and bump PROTOCOL_REVISION.

<details><summary>Second reader's check</summary>

The code does send a second 0x95 prepare with a different framing (`95 00 00 00 00 00` in get_shading_parms, direct.py:1001-1006, against `95 00 02 00 00 00` at 1818-1823), and it sends it between SLIDE INIT/wait_ready and SCAN (1854-1894). That differs from the vendor's documented 2 prepares across all captures (docs/protocol.md:60). But ensure_shading(), which tools/scan.py, scan_roll and the GUI's Calibrate job all use, runs the same calibrate_shading with the same second prepare, and tools/scan_roll.py:440-441 says those calls work. So the extra prepare is not what separates the stalling lazy path from the working one. The divergence is real but not shown to be hazardous.

</details>

<a id="transport-protocol-tp-11"></a>

### TP-11 -- _whole_frames takes a failed READ STATE (None) as its baseline, so any later successful read counts as 'moved'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/direct.py:1170-1197`, `rps7200/direct.py:1234-1245`, `rps7200/session.py:143-165`, `rps7200/direct.py:3433-3442`

When the baseline read fails (for example right after a previous SLIDE, or after a Move job), the first successful poll returns the unchanged position as a movement. advance() then reports a move that did not happen, which defeats the 'no movement = end of film' test. retreat() and rewind() miscount in the same way, and a None/None comparison counts a real move as backlash, triggering an extra SLIDE_PREV.

**Evidence (from the code):**

```text
`before = self.position(); self.slide(action, param=0x01, value=steps); ... now = self.position(); if now is not None and now != before: return now`. position() returns None on any failure, and its docstring says 'a READ_STATE issued right after a transport command comes back empty every time'. In rewind(): `before = scanner.position(); landed = scanner.retreat(); now = scanner.position(); if landed is None or now == before:`.
```

**Failure scenario:** At the end of a strip, advance() is called right after a transport command, so the baseline READ STATE is empty. SLIDE_NEXT does nothing, the next poll reads the old position, and advance returns it as 'advanced'. scan_roll carries on, place_on_strip sees position < index and ends the roll with the false message 'it went back over frames ...'. In seek, the rewind sends one SLIDE_PREV too many and then refuses with FilmNotPlaced.

**Fix:** Read the baseline with the patient `_ask_position`-style retry, and refuse to send when no baseline can be obtained. Compare only non-None values and return a distinct 'unknown' outcome instead of treating None as a position.

<details><summary>Second reader's check</summary>

_whole_frames does `before = self.position()` and then accepts `now is not None and now != before` (direct.py:1182-1192), so a None baseline makes the first successful poll count as a move. In rewind (session.py:143-158), `landed is None or now == before` treats None==None as no movement even when retreat() returned a landed position, which costs one extra SLIDE_PREV. The defect is real in code. Reaching it needs a failed READ STATE just before the move: _whole_frames' own polling has usually just had a good read, which makes this narrow. Rated low.

</details>

<a id="transport-protocol-tp-17"></a>

### TP-17 -- session_start moves the film with a forward sub-frame SLIDE, which is not recorded, and its docstring misstates the payload

**Severity** low · **Category** hardware-safety · **Verdict** partly

**Where:** `rps7200/direct.py:1726-1753`, `rps7200/direct.py:1143-1168`, `docs/protocol.md:256-270`

session_start sends a sub-frame forward SLIDE `00 01 00 00`, a payload that appears in no capture, while its docstring claims the vendor's `00 01 00 04`. The move comes before any measurement in its callers and is logged only as 'slide transport: 0x0'.

**Evidence (from the code):**

```text
`self.slide(0x00, param=0x01)` sends payload `00 01 00 00` (value defaults to 0, slide() at 1143-1168). The docstring says 'a SLIDE with `00 01 00 04`'. protocol.md §5: action 0x00 is 'sub-frame fwd', and `value` has 'no measurable effect'. Called by tools/transport_truth.py:122, gain_probe.py:129, fast_ir_probe.py:192/441, byte14_probe.py:127, roll_registration_walk.py:262, exposure_probe.py:237 and hold_probe.py:130.
```

**Failure scenario:** roll_registration_walk calls session_start and then walks the strip. Every frame measures about 2.8 units further out than it was, the offset is attributed to the transport, and the walk's statistics feed CONFIDENCE_FLOOR/registration constants.

**Fix:** Either drop the SLIDE from session_start (it is vendor mimicry of unknown purpose), or log it as 'sub-frame +param 1' and record it in the run's output. Fix the docstring, and send exactly the vendor payload if mimicry is the point.

<details><summary>Second reader's check</summary>

session_start does send `self.slide(0x00, param=0x01)`, value defaulting to 0, i.e. `00 01 00 00` (direct.py:1143-1168, 1749-1753), while its docstring says `00 01 00 04`. docs/protocol.md's SLIDE table (256-270) has no `00 01 00 00`, so this is a payload the vendor never sends. But in every caller (hold_probe.py:130, roll_registration_walk.py:262 and others) the SLIDE is sent before any reference prescan, so later relative measurements are not affected and the failure scenario (every frame measured 2.8 units out) is overstated. The move is logged, if cryptically, as 'slide transport: 0x0'.

</details>

<a id="transport-protocol-tp-22"></a>

### TP-22 -- verify_capture's opcode check can never fail, its verdict overclaims, and verify_protocol's restore report is inverted

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tools/parse_capture.py:80-95`, `tools/verify_capture.py:104-114`, `tools/verify_capture.py:175-181`, `tools/verify_protocol.py:1195-1226`, `tools/verify_protocol.py:1165`, `tools/verify_protocol.py:1323`

The offline check the test suite relies on ('fails for ... an opcode protocol.py does not define') cannot detect an undefined opcode, and resync skips are only a NOTE. Its closing verdict implies full wire equivalence. The probe's results.json misreports restores, and it uses stale and inconsistent unit constants (1.5724 vs 1.84; 1.2423 vs 1.2247 px/unit).

**Evidence (from the code):**

```text
parse(): `if op not in OPS: i += 1; continue`, so any byte not in OPS is skipped as 'lost sync' and never becomes an opcode. verify_capture then checks `missing = sorted(set(opcodes) - known)`, which by construction contains nothing outside OPS (OPS mirrors protocol's SCSI_* set plus 0x95). It prints 'this driver sends what CyberView sends.' after checking only opcodes and MODE SELECT payloads: not SLIDE, WRITE GAIN/OFFSET, COPY size or command order. verify_protocol stage 15: `for attempt in range(10): ... if left < 3.0: break ... else: restored = travel`, so a successful restore leaves `restored` None and a failure to converge sets it. The constants are retyped: `want = (param + 1.5724) * 1.2423` and `remaining / 1.2247 - 1.948`.
```

**Failure scenario:** A capture contains a vendor opcode 0xE8 the driver lacks. parse() skips it as lost sync, verify_capture prints 'this driver sends what CyberView sends', and test_this_driver_sends_what_the_vendor_sends passes.

**Fix:** Make parse() record unknown bytes as ('unknown', byte) with offsets, and fail when skipped bytes exceed zero. Narrow the verdict text to what is actually compared, or add SLIDE/gain/COPY/order comparisons. Fix the for/else, and import the constants from rps7200.protocol.

<details><summary>Second reader's check</summary>

parse() skips any byte not in OPS as lost sync (parse_capture.py:80-84), and OPS covers every SCSI_* constant, so verify_capture's `set(opcodes) - known` (verify_capture.py:104-110) can only ever flag 0x95. An unknown vendor opcode becomes a skipped byte and only a NOTE (98-99). The closing verdict 'this driver sends what CyberView sends.' comes after checking only opcodes, SET_SCAN_HEAD and MODE SELECT. stage15's for/else (verify_protocol.py:1198-1220) leaves restored=None on a successful restore (the break at left<3.0) and sets it when 10 attempts fail. The unit constants are retyped (1.5724/1.2423 at 1165/1203 against 1.948/1.2247 at 1323).

</details>

<a id="transport-protocol-tp-23"></a>

### TP-23 -- RPS7200_MAX_WINDOW is unvalidated: 0 or a negative value loops forever, a bad value breaks every import, and the value is not recorded

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:60-67`, `rps7200/usb_transport.py:740-770`

The probing knob has no bounds. At 0 or below, every data-in command spins forever announcing 0-length reads to the bridge while a command is outstanding. A non-integer value makes `import rps7200.usb_transport` raise, which also kills host-side analysis. Values above 32 KB reproduce the SANE stall the module exists to avoid. Entries do not record the window used.

**Evidence (from the code):**

```text
`MAX_WINDOW = int(os.environ.get("RPS7200_MAX_WINDOW", 0x8000))` at import. In _read_payload: `window = min(self.max_window, size - got); self._announce_length(window); ... while window_got < window: ...; got += window_got`. With a window of 0 or less the inner loop never runs and `got` never advances.
```

**Failure scenario:** A leftover `RPS7200_MAX_WINDOW=0` in a shell profile: the next scan hangs inside the first READ, sending control transfers indefinitely, until the process is killed mid-command.

**Fix:** Clamp to 512..0x8000 (or refuse outside it) with a clear message, parse lazily, and record the effective window/batch in meta.

<details><summary>Second reader's check</summary>

`MAX_WINDOW = int(os.environ.get("RPS7200_MAX_WINDOW", 0x8000))` at import (usb_transport.py:67). With 0 or a negative value, `window = min(self.max_window, size - got)` is at most 0, the inner loop never runs and `got` never advances (744-768), so the loop spins forever sending length handshakes. A non-integer value raises ValueError at import. The value is not recorded in meta.

</details>

<a id="transport-protocol-tp-24"></a>

### TP-24 -- A READ answered with status OK returns b"" and read_planes still counts the lines as received

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:828-842`, `rps7200/direct.py:1398-1427`, `rps7200/direct.py:1483-1508`

_query guards short responses, but the image path does not. If the device answers a READ with OK (no data phase), n lines are counted as delivered with zero bytes. The loop finishes early, decode truncates silently, and progress reads 100%. The raw layout's lines_received is the only trace.

**Evidence (from the code):**

```text
_command: `if status == UsbStatus.OK: if data: ... return b""` ignores read_size. read_lines returns `self.t.command(_cmd(SCSI_READ, lines), read_size=lines * bytes_per_line, ...)` with no length check. read_planes: `chunk = self.read_lines(n, bpl, retries=1) ... chunks.append(chunk); got += n`.
```

**Failure scenario:** The firmware answers a READ with status OK instead of READ during a state transition. That READ's 216 lines are dropped from the image with no error, and the frame is filed short.

**Fix:** In _command, raise UsbError when read_size > 0 and the status is OK. In read_lines, verify `len(payload) == lines * bytes_per_line`.

<details><summary>Second reader's check</summary>

_command returns b"" on status OK regardless of read_size (usb_transport.py:828-842). read_lines returns the payload with no length check (direct.py:1420-1425). read_planes does `chunks.append(chunk); got += n` (1504-1506). decode_index counts lines from len(blob) (1575), so the image comes out silently short while progress reports completion. Whether the firmware ever answers a READ with OK is unknown, hence low.

</details>

<a id="transport-protocol-tp-25"></a>

### TP-25 -- A scalar exposure_scale also scales the infrared exposure the vendor treats as a device constant

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/protocol.py:589-616`, `rps7200/direct.py:2613-2614`, `docs/protocol.md:397-399`

A per-channel list leaves IR at base (the list is padded with 1.0), but a scalar (tools/scan.py or uniformity.py with a single --exposure-scale value) multiplies the IR exposure too. That produces an unmeasured combination and a behaviour change depending on how the same request is spelled.

**Evidence (from the code):**

```text
Settings.scaled: `if isinstance(factor, (int, float)): factors = [float(factor)] * len(self.exposure)`, which covers all four channels including I. scan(): `settings = self.get_gain_offset().scaled(exposure_scale); self.set_gain_offset(settings, infrared=infrared)`. protocol.md §6: 'the host never writes a value it did not read' for IR exposure, and scan_bracket calls it 'a device constant the vendor never meters'.
```

**Failure scenario:** `tools/scan.py --ir --exposure-scale 1.5` writes IR exposure 7745*1.5=11618 where a 3-value spelling writes 7745. The IR planes of the two scans are not comparable, and only the recorded exposure list reveals it.

**Fix:** Expand a scalar to three visible channels and leave IR at base unless it is explicitly given as a 4th value.

<details><summary>Second reader's check</summary>

Settings.scaled with a scalar does `factors = [float(factor)] * len(self.exposure)`, all four channels including I (protocol.py:605-606), while a list is padded with 1.0 (608-609). scan() writes it with set_gain_offset (direct.py:2613-2614), and exposure[3] always goes into data[18:20] (1370). tools/scan.py passes a scalar for a single --exposure-scale value (tools/scan.py:143-146, 228). The recorded meta['exposure'] does show the IR value, so it is detectable after the fact.

</details>

<a id="transport-protocol-tp-26"></a>

### TP-26 -- STOP SCAN and IEEE1284 RESET remain callable APIs, and their docstrings recommend them

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:1298-1332`, `rps7200/usb_transport.py:496-526`, `rps7200/usb_transport.py:686-689`, `CLAUDE.md:374-375`

Neither is called from tools, the GUI or rps7200, but both are public and documented as the right thing to do on a cleanup path. That invites a future caller (or a probe) to add them to an exception handler, which is exactly the wedge CLAUDE.md forbids.

**Evidence (from the code):**

```text
stop_scan docstring: 'Stop scanning. Never raises -- it runs on the cleanup path. Leaving a scan running is what wedges the scanner ... so this always makes the attempt.' It sends `_cmd(SCSI_SCAN, 0)`. finish_scan says STOP SCAN 'is reserved for cancelling a scan that is still running'. Transport.open(reset=True) calls `self.reset()`, which sends `ieee_command(IEEE1284_RESET)`. CLAUDE.md: 'No IEEE1284 RESET, and no STOP SCAN -- ... both leave the device unresponsive.'
```

**Failure scenario:** Someone 'fixes' TP-05 by calling stop_scan() in scan()'s except block, as its docstring suggests. Every failed pass then leaves the device unresponsive for the next session.

**Fix:** Delete stop_scan, or rename it `_forbidden_stop_scan` with a raising guard, and remove the reset path from open(), or gate it behind an explicit env var. Rewrite the docstrings to match CLAUDE.md.

<details><summary>Second reader's check</summary>

stop_scan (direct.py:1315-1332) sends `_cmd(SCSI_SCAN, 0)`. Its docstring says 'Leaving a scan running is what wedges the scanner ... so this always makes the attempt', and finish_scan's docstring reserves STOP SCAN 'for cancelling a scan that is still running' (1302-1305). Transport.open(reset=True) calls reset() and sends IEEE1284_RESET (usb_transport.py:509-526, 686-689). grep finds no callers of either, so this is latent. open()'s docstring does restrict reset to recovering an already-unresponsive device, but stop_scan's docstring actively invites use on a cleanup path, contrary to CLAUDE.md:374-375.

</details>

<a id="transport-protocol-tp-27"></a>

### TP-27 -- docs/protocol.md contradicts the code and itself on ports, COPY, the slide law, rewinds and the command order

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `docs/protocol.md:16-21`, `docs/protocol.md:39`, `docs/protocol.md:289-298`, `docs/protocol.md:353-356`, `docs/protocol.md:436-466`, `docs/protocol.md:271-277`, `docs/protocol.md:856-859`, `docs/protocol.md:3-4`, `rps7200/usb_transport.py:693-701`, `rps7200/direct.py:1376-1385`, `rps7200/direct.py:2655-2678`, `rps7200/protocol.py:259-279`

The protocol reference that CLAUDE.md, tools and docstrings point to disagrees with the code on how commands travel, on COPY size and order (an undocumented vendor/driver divergence), on the slide law the mover actually uses, and with itself on rewinds and capture counts.

**Evidence (from the code):**

```text
§1: 'command out | ... wValue=0x0088, one byte at a time, prefixed by 0xE0', but _send_command writes CDB bytes to PORT_SCSI_CMD 0x85 after the daisy preamble to 0x88. §2: 'COPY | 71 | precedes each scan; payload is constant 70 bytes', but the driver sends COPY after SCAN with size=pixels_per_line (5172) and reads 5172 bytes, and §8 lists 'COPY ... SLIDE INIT ... SCAN ... READ x N ... PARAM' while the driver does SCAN, COPY, PARAM, then READs. §5/§11: law 'distance = 0.1057 mm x param + 0.1662 mm', 'param = round((millimetres - 0.1662) / 0.1057)' and '`DirectScanner.OVERHEAD_MM` (1.57 units)', but the code uses `COMMAND_UNITS = 1.84`, i.e. 0.1945 mm. §8 says the vendor rewinds with 'SLIDE 05 01 00 01 ... then 03 f6 dd 00' and uses 'SLIDE 01 47 00 03', but §5 says neither appears in any capture and the capture holds 01 57 00 03. §12 says a mirrored scan is 'not driven by any byte tried', while §11 says 'Resolved 2026-09-23'. §0: 'seven sessions ... 8,133 SCSI commands', against CLAUDE.md:383 '3,955 commands across all six captures' and :499 '3,987'.
```

**Failure scenario:** A maintainer 'fixes' the driver to send CDBs to 0x88 per §1, or sizes nudges from the §11 law. Either breaks the device path or delivers a different param than planned.

**Fix:** Regenerate §1-§3 and §8 from parse_capture output. State the COPY size/order divergence explicitly (or align the driver). Replace the law with the one protocol.py uses. Remove the contradicted §8 lines and the stale §12 item, and reconcile the capture counts.

<details><summary>Second reader's check</summary>

Checked against code. §1 says CDB bytes go to wValue 0x0088, but _send_command writes the daisy preamble plus 0xE0 to PORT_PAR_DATA 0x88 and the CDB bytes to PORT_SCSI_CMD 0x85 (usb_transport.py:44-46, 693-701). parse_capture's own docstring agrees with the code, not with §1. §2 says COPY 'precedes each scan; payload is constant 70 bytes', but scan() sends COPY after SCAN, sized from the shading width (5172) (direct.py:2655-2672, 1376-1385). §8's order (COPY, SLIDE INIT, SCAN, READ, PARAM) is not the driver's (SLIDE INIT, SCAN, COPY, PARAM, READ). The §5/§11 law uses 0.1662 mm and cites OVERHEAD_MM as 1.57 units, while code uses COMMAND_UNITS=1.84 (protocol.py:276-279). §8 still lists `05 01 00 01`/`03 f6 dd 00`/`01 47 00 03`, which §5 (271-277) says are in no capture. §0 says 8,133 commands in seven captures, against CLAUDE.md's 3,955/3,987 in six.

</details>

<a id="transport-protocol-tp-28"></a>

### TP-28 -- Docstrings, comments and CLAUDE.md make claims the code does not do

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2444-2447`, `rps7200/direct.py:1218-1232`, `rps7200/direct.py:1726-1734`, `rps7200/direct.py:1768-1772`, `rps7200/protocol.py:287-292`, `rps7200/direct.py:3093-3097`, `rps7200/direct.py:2064-2066`, `rps7200/direct.py:3634-3643`, `rps7200/session.py:1562`, `CLAUDE.md:265`, `CLAUDE.md:393`, `CLAUDE.md:420`, `CLAUDE.md:450`, `tools/check_scanner.py:16-17`, `tools/verify_protocol.py:11-13`

Many load-bearing statements contradict the code or each other. Several concern hardware safety: which payloads are 'vendor-sanctioned', whether exposure compounds, and the size of the smallest move.

**Evidence (from the code):**

```text
scan(): 'The command order here is the vendor software's' (see TP-27 for the differences). retreat(): 'This is what the vendor sends to rewind a finished roll', but protocol.md §5 says SLIDE_PREV appears in no capture. session_start: 0xE7 'may be what puts the scanner into a state where calibration is accepted', while calibrate_shading says 'The 0xe7 vendor command is not involved'. units_for_param: '`param 1` travels 2.57', but the code gives 1 + 1.84 = 2.84. STEP_MM/OVERHEAD_MM comment: 'Worst residual 0.0185 mm across ten points' (the 0.1662 fit), but OVERHEAD_MM = 0.1945. auto_exposure: 'SET GAIN OFFSET persists, so re-reading it each round would compound', versus scan_roll 'the read is a fixed reference ... exposure cannot compound' and CLAUDE.md:393 'does not persist'. session._move: 'One SLIDE command tops out at ~1.01 mm' (the cap is 87, about 9 mm). CLAUDE.md:420 'param 1 (~2.6 units)' versus CLAUDE.md:265 '2.84'. CLAUDE.md:450 'Bump PROTOCOL_REVISION in rps7200/direct.py', but it lives in protocol.py:40. check_scanner: 'The only commands it sends are INQUIRY and READ STATE', but read_state via _query sends REQUEST SENSE on CHECK. verify_protocol: 'sends only byte values the vendor sends', but stage 12 sends params 120-255, stage 14 sends param 0, and stages 6/7 send `01 47 00 03` (param 71), which protocol.md says is in no capture.
```

**Failure scenario:** An operator runs `verify_protocol.py 12 14` trusting the module docstring that only vendor values are sent. It sends param 255 and param 0, which are invented payloads.

**Fix:** Correct each statement against the code. Gate verify_protocol stages 12/14 behind an explicit `--invented-payloads` flag with a printed warning and confirmation.

<details><summary>Second reader's check</summary>

Each quoted item checks out in code. units_for_param says 'param 1 travels 2.57' but returns param + 1.84 = 2.84 (protocol.py:282-287). The STEP_MM/OVERHEAD_MM comment cites the 0.0185 mm residual of the old 0.1662 fit, while OVERHEAD_MM = MM_PER_COMMAND = 0.1945 (direct.py:3093-3097). auto_exposure says 'SET GAIN OFFSET persists' (2064-2066), against CLAUDE.md:393 and scan_roll's comment. session._move says 'tops out at ~1.01 mm' (session.py:1562). CLAUDE.md:450 says PROTOCOL_REVISION lives in rps7200/direct.py; it is defined at protocol.py:40. check_scanner's docstring says only INQUIRY and READ STATE are sent (16-17), but read_state goes through _query, which sends REQUEST SENSE on CHECK. verify_protocol's docstring says 'sends only byte values the vendor sends' (11-13), while stage12 loops params up to 255 (779) and stage6/7 send `01 47 00 03` (221, 397).

</details>

<a id="transport-protocol-tp-30"></a>

### TP-30 -- Readiness results are ignored, and the media log reports the wrong byte

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/direct.py:2540-2552`, `rps7200/direct.py:2645-2657`, `rps7200/direct.py:919-947`, `rps7200/direct.py:1855`

A device that never reports ready, or reports a persistent non-warming sense such as a hardware error, proceeds into start_scan as if ready. The media note prints byte 6 while the decision used byte 8, which misleads anyone reading logs to judge the transport.

**Evidence (from the code):**

```text
scan(): `self.wait_warm(); self.test_unit_ready()` (result ignored); `self.wait_ready()` is called twice with its False-on-timeout result ignored. wait_warm: on a non-warming condition, `if time.monotonic() > deadline: return` returns silently after 300 s. Media: `if not state.media_loaded:` (byte 8) logs `f"note: state {state.scanning:#04x} suggests no film"` (byte 6).
```

**Failure scenario:** A persistent UNIT ATTENTION or hardware-error sense: wait_warm spins for 300 s, returns, and the scan sequence is sent anyway. The log then says 'state 0x1d suggests no film' when byte 8 said empty.

**Fix:** Raise when wait_ready or wait_warm time out without readiness (except where deliberately tolerated), and log byte 8 in the media note.

<details><summary>Second reader's check</summary>

scan() ignores test_unit_ready()'s bool (direct.py:2541) and both wait_ready() results (2657, 2679). wait_ready returns False on timeout (966-975). wait_warm returns silently after the deadline on a non-warming sense (937-942). The media note tests `state.media_loaded` (byte 8) but prints `state.scanning:#04x` (byte 6) (2543-2551).

</details>

<a id="transport-protocol-tp-29"></a>

### TP-29 -- The SLIDE law change was not accompanied by a PROTOCOL_REVISION bump

**Severity** info · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/protocol.py:15-40`, `rps7200/protocol.py:276-279`, `rps7200/direct.py:3125-3136`, `rps7200/direct.py:3022`

The SLIDE distance-to-param law changed without a PROTOCOL_REVISION bump, so revision 5 covers two mappings. The per-move param is recorded in registration history, so analysis can still separate them.

**Evidence (from the code):**

```text
git d723896 (2026-09-22 15:29) 'One number for what a command costs, and it is 1.84' changed MM_PER_COMMAND, and with it `param_for_mm`: `n = round((abs(millimetres) - DirectScanner.OVERHEAD_MM) / DirectScanner.STEP_MM)`. The revision stayed 5 (bumped at df7d4e6, 15:07) until revision 6 at 2b394b0 for a different reason, and the revision-history comment does not mention the law change.
```

**Failure scenario:** An offline analysis groups hold-loop outcomes by protocol_revision to evaluate the law and mixes two different mappings from distance to param.

**Fix:** Record the param actually sent for each move (it is in fix history) and the law constants in registration meta. Bump the revision whenever param_for_mm's mapping changes.

<details><summary>Second reader's check</summary>

d723896 (2026-09-22 15:29) changed MM_PER_COMMAND, and with it param_for_mm's mapping (direct.py:3134-3135), while PROTOCOL_REVISION stayed at 5 until 2b394b0 bumped it for an unrelated reason (git log). Whether a changed distance-to-param mapping counts as 'commands sent change' is arguable. The param actually sent is recorded per move in the registration history (`"param": param`, direct.py:3022, appended at 2882/2910) and filed via meta['registration'], so the mixing described can be undone offline.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Raw bulk bytes of a pass (index format: per line a 2-byte tag, 'R'/'G'/'B'/'I' + 0, then LE uint16 or uint8 samples) | library/<id>/raw.bin.gz | gzip (level 6) of the concatenated READ payloads, exactly as received; sha256 of the uncompressed bytes in scan.json raw.sha256 | raw | library.save(raw= or raw_path=), fed from DirectScanner.last_raw (read_planes keep_raw, direct.py:1518-1532) through capture_record(), the debug spool, FrameWriter._write or tools/scan.py | library.read_raw/decode_raw/reconstruct/migrate_direction, demo._decode | Byte-exact when present and when it belongs to the pass. Can be STALE (another pass's bytes, TP-03), absent on a keep_raw=False pass, dropped by session._file at 7200 dpi and on truncated passes (TP-14), or lost when a debug flush fails (TP-02) |
| Raw layout needed to decode raw.bin.gz | library/<id>/scan.json -> raw.layout | JSON {format:'index', bytes_per_line, line_stride, index_header, width, lines (declared by PARAM), channels, byte_order, lines_received} | raw metadata | read_planes (direct.py:1522-1532), via library.save | decode_raw, reconstruct, migrate_direction, session._file shape guard | Exact for the pass that set it. Stale in the same cases as raw.bin.gz. Does not record the 7200 dpi realignment |
| Decoded pixels ('raw' image) | library/<id>/scan.tif | TIFF uint16/uint8 (H,W,C), rows turned upright by decode_index | Intended raw. Actually CORRECTED for GUI single scans (TP-01), and stagger-realigned (4 lines trimmed) at 7200 dpi without a record (TP-14) | library.save from last_pixels_raw, raw_image or the debug spool | library.load/corrected, reconstruct, GUI 1:1 view, demo | Lossless TIFF of what was handed in; what was handed in is not always the decode |
| Per-pass CCD mask | library/<id>/ccd_mask.bin | bytes, pixels_per_line long (5172), from SCSI COPY read after SCAN | raw | scan() sets DirectScanner._ccd_mask = get_ccd_mask(...) (direct.py:2663-2672), and library.save writes it | library.load/corrected -> apply_shading | Byte-exact. The calibration pass's own mask is overwritten and never stored |
| Shading reference | library/<id>/shading.npz and calibration/shading.npz (the session cache used by 'reuse') | npz: ref{c}/dark{c} float64 per-column means, mean{c}/darkmean{c} float64, pixels_per_line, channels | DERIVED (reduced by calculate_shading's level-split heuristic) | ShadingReference.save via ensure_shading/save_shading and library.save | load_shading, library.load/corrected, uniformity.py | Lossless for the reduced arrays, but NOT the calibration bytes: the raw calibration lines (~1.66 MB), calibration info (128 B), descriptor (32 B), calibration gain/offset, block count and duration are discarded (TP-04). Nothing records whether it was measured this power-on or loaded |
| Scan record (subset of meta) | library/<id>/scan.json -> scan, device_settings, calibration, metering, registration, prescan, provenance | JSON | metadata | library.save from the meta built at direct.py:2760-2804, whitelisted at library.py:237-264 | library tools, reconstruct, corrected, registration_margin, demo | Exact for the fields kept. carriage_state keeps the 13-byte READ STATE hex before the pass. MISSING: INQUIRY (the save parameter is ignored), MODE SELECT bytes/byte14/quality, slide_init_param, light/IR flag bytes, raw READ GAIN/OFFSET and PARAM responses, READ STATE after the pass, sense events, transport window/batch (TP-15, TP-16) |
| Debug spool (RPS7200_DEBUG) | $TMPDIR/rps7200-debug-XXXX/NNN-image.npy, NNN-raw.bin | np.save of the raw pixel array; raw bulk bytes uncompressed | raw (raw.bin may be stale) | DirectScanner._debug_capture (direct.py:650-692), during scan() | DirectScanner._debug_flush after close() | Exact copies, but deleted even when filing fails (TP-02), and never filed if close() is not called or the process dies |
| Probe outputs | probe/*.tif, probe/results.json | TIFF 8-bit RGB (shading=False) and merged JSON (read, update, rewrite; non-atomic) | raw pixels, but without raw bytes | tools/verify_protocol.py shot()/main() | verify_protocol stages (e.g. stage16 reads stage15_home.tif), manual analysis | Pixels lossless. No raw bytes, mask or meta: not re-decodable. Not filed in library/ unless RPS7200_DEBUG=1, and then without raw bytes (keep_raw False) |
| Vendor USB captures | captures/*.pcapng (gitignored) | pcapng with USBPcap pseudo-headers; whole-bus, includes keyboard HID | raw | External (Wireshark/USBPcap on the capture machine) | rps7200.usbpcap.packets/setups/scanner_devices, tools/parse_capture.py, tools/verify_capture.py, tests/test_usbpcap.py | Read-only. packets() returns interrupt (keystroke) payloads to any caller (TP-20) |
| Roll manifest and frame files from the CLI | rolls/<name>/roll.json\|survey.json, frameNN.tif, prescanNN.tif | JSON plus corrected TIFF | corrected (deliverables) | tools/scan_roll.py checkpoint(), FrameWriter | GUI sheet, scan_roll --reuse, registration tools | Corrected, not re-derivable on their own. Library entries are the record. Frames queued in FrameWriter are lost on Ctrl-C (TP-19) |

**Second reader's corrections to this table:**

1) library/<id>/scan.tif: GUI single Scan entries hold corrected pixels (TP-01). Demo entries under demo/library hold corrected pixels for both prescans and scans, because DemoScanner exposes no last_pixels_raw (TP-A4). GUI prescans are correctly raw.
2) Probe outputs (verify_protocol shot()): under RPS7200_DEBUG=1 they are filed with STALE raw bytes from the last keep_raw pass, or with none (TP-03), not simply 'without raw bytes'.
3) raw.bin.gz and the debug spool: bytes from a pass that fails mid-read, or raises after its read, are never written anywhere (TP-A1). tools/scan.py keeps pending passes in RAM, not on disk, and loses them all if any pass raises (TP-A3).
4) scan.json does not record INQUIRY at all (the `inquiry` save parameter is unused), whether the shading reference was loaded or measured, the calibration raw bytes, the realignment of 7200 dpi scan.tif, or the transport window/batch.
5) calibration/shading.npz (ensure_shading's path) is overwritten in place by each new calibration, with no timestamp or power-on id. An entry's shading.npz copy is the only durable record of which reference was used, and its provenance is not recorded.
6) Everything is written non-atomically: library.save writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz before scan.json, so a crash leaves a directory with no scan.json. The collision check (`(path / "scan.json").exists()`) then reuses that directory.

## What the operator can do

- Run `uv run python tools/check_scanner.py` at any time. It sends only INQUIRY and READ STATE (plus REQUEST SENSE if READ STATE is refused), never moves the transport, and exits 0 with no scanner attached.
- Run `tools/parse_capture.py <capture>` and `tools/verify_capture.py` offline. They read only the scanner's control transfers, so no device is needed.
- Press Stop in the window. It is cooperative, is checked between frames and before each advance or nudge, and never interrupts a read.
- Quit the window while a scan runs. on_close waits for the worker to finish rather than abandoning the read.
- Use the window's prev/next frame buttons (SLIDE_PREV/NEXT with value 1, one frame per command). These are the measured-safe whole-frame moves.
- Re-derive pixels offline with `make reconstruct`, `library.decode_raw` and `library.corrected`, for entries that actually hold their raw bytes.
- Set RPS7200_DEBUG=1 (Bourne shell) or `$env:RPS7200_DEBUG=1` (PowerShell) so that ad-hoc DirectScanner scripts file every pass. Pass keep_raw=True explicitly (see TP-03).

## What the operator should not do

- Do not use Force abort unless the device is already lost. It closes libusb under an in-flight transfer, almost always wedges the scanner, can crash the process and lose frames still queued for filing.
- Do not press Ctrl-C during tools/scan.py or tools/scan_roll.py passes. The `with` block closes the transport under the read, and scan_roll skips writer.finish(), so queued frames are lost.
- Do not retry a scan or roll right after a job failed with a USB, read or timeout error. Nothing marks the device as suspect, and a power cycle is the documented recovery.
- Do not calibrate with the transport empty. tools/uniformity.py leads straight into this after its 'press Enter with the transport empty' prompt.
- Do not press Prescan or Scan while a Calibrate is still pending. A queued picture job runs even if the calibration fails, and then calibrates lazily inside scan(), the path recorded as stalling with LIBUSB_ERROR_PIPE.
- Do not set RPS7200_MAX_WINDOW. A value of 0 or less hangs every read, a value above 32 KB reproduces the SANE stall, and a non-integer breaks every import.
- Do not run tools/verify_protocol.py stages 12 or 14 without explicit agreement. They send invented SLIDE payloads (param 120-255 and param 0), despite the module docstring.
- Do not rely on --no-fast-ir infrared passes being protected by INFRARED_FLOOR_S. The real read guards are 120 s.
- Do not expect GUI 7200 dpi scans to keep their raw bytes (the session guard drops them), or single GUI scans to hold raw pixels (they hold corrected ones).
- Do not reuse calibration/shading.npz across power cycles expecting the entry to say so. Nothing records the reference's origin.
- Do not run probe tools that call session_start() on film you are registering. Each call nudges the film forward by param 1.

## Mistakes nothing guards against

- Submitting any new job after a mid-read failure: the session worker simply runs it, and the roll advances with SLIDE_NEXT and rescans up to max_failures times.
- Calling DirectScanner.scan(auto_exposure=True) or any keep_raw=False scan with RPS7200_DEBUG=1: the entry gets an earlier pass's raw bytes, or none.
- Forgetting to call close(), or not using `with`, in an ad-hoc debug script: spooled scans are never filed and stay in the temporary directory.
- Running a debug script from a different working directory: library.DEFAULT_ROOT is relative ('library'), so entries land in a stray ./library.
- A filing failure during the debug flush (disk full, bad RPS7200_DEBUG_ROOT): the spooled raw bytes and pixels are deleted anyway.
- Choosing 7200 dpi in --demo: the demo 'scans' it, while the real driver refuses a shaded 7200 dpi pass.
- Calibrating on an empty transport: READ STATE byte 8 (the measured media flag) is never consulted before calibration.
- Spelling --exposure-scale as one number with infrared on: the infrared exposure is scaled too, unlike the three-value form.
- Setting RPS7200_MAX_WINDOW=0 or a negative value: the next READ loops forever with a command outstanding.

## Dataflow notes

Command path (usb_transport.py):
- Transport.command (772-797) calls _command (799-864).
- _send_command (693-701) calls ieee_command (663-671): the 7-byte daisy preamble plus 0xE0 go to port 0x88 (PAR_DATA), then the strobes to 0x87. The 6 CDB bytes then go one control transfer each to 0x85 (PORT_SCSI_CMD), and the status is read from 0x84 (_control_in 597-612).
- Status handling:
  - AGAIN: resend after 1 s.
  - BUSY: poll 0x84.
  - OK: send the data-out bytes to 0x85, then _wait_not_busy.
  - READ: _read_payload (717-770). For each 32 KB window it announces the length (8 bytes LE at offset 4 to 0x82, _announce_length 614-634), then reads 16 KB bulk-IN chunks from ep 0x81 (_bulk_read_into 636-661). A ZLP before any byte raises NoDataYet. A ZLP after partial delivery is waited out for up to 120 s. Any libusb error, including TIMEOUT with 0 bytes, triggers clear_halt and a UsbError. Then _wait_not_busy, and CHECK raises CheckCondition.
- Nothing is logged or recorded at this layer beyond verbose prints.

Driver layer (direct.py):
- _query (830-863) wraps status and parameter reads (READ STATE 13 B, READ GAIN/OFFSET 123 B, PARAM 18 B, COPY mask, calibration READ 32/128) and retries on CHECK with REQUEST SENSE (read_sense -> Sense.parse, protocol.py:425-428).
- read_state (865-879) builds State(raw=13 B); last_state is later recorded as meta['carriage_state'] (carriage_record 881-893).

scan() (2423-2824) sends, in order:
1. READ STATE polling, wait_warm (TUR/SENSE), TUR, READ STATE (media, logged only).
2. SET EXPOSURE x3 and SET HIGHLIGHT x3 (WRITE 0x13/0x14, value 100).
3. [lazy calibrate_shading if there is no reference]
4. SET SCAN FRAME (WRITE 0x12), CMD 17.
5. READ GAIN/OFFSET, then Settings.scaled(exposure_scale), then WRITE GAIN/OFFSET (29 B).
6. MODE SELECT (16 B; byte14 = byte14_for(passes) or override), TUR, SLIDE INIT (10 <param> 00 00), wait_ready.
7. Here carriage_record() is captured.
8. SCAN(1) via start_scan (CHECK: sense, retry), wait_ready.
9. COPY mask (sized to the reference width), stored in _ccd_mask.
10. PARAM, giving ScanParameters.
11. read_planes (1440-1542): READ in batches of up to 216 lines (batch_for), NoDataYet idle 120 s, EndOfData breaks. The blob is kept as last_raw/last_raw_layout only if keep_raw. decode_index (1551-1600) turns the tags into planes, truncates to common height, and reverses rows when read_direction says bottom-up.
12. finish_scan: 3x READ STATE; no STOP SCAN.
After the pass, the host side does:
- At 7200 dpi, _realign_native_column_stagger trims 4 lines.
- The result is raw_pixels. apply_shading(image, _shading, ccd_mask) produces the returned corrected image.
- meta is built (2760-2804).
- _debug_capture(raw_pixels, meta) spools the pixels and capture_record (stale-prone last_raw).
- last_pixels_raw and last_scan_meta are set.

Calibration (calibrate_shading 1755-1973):
1. READ STATE, warm-up, TUR, SET EXPOSURE/HIGHLIGHT.
2. CAL INFO prepare (95 00 02 00 ..) and READ 128, logged only.
3. SET SCAN FRAME (0,3431,10343,6888), CMD 17, READ and WRITE GAIN/OFFSET.
4. MODE SELECT (calibrate quality 0x0800, 8-bit, index format), SLIDE INIT 10 01 00 00, wait_ready.
5. get_shading_parms: a second prepare (95 00 00 00 ..) and READ 32.
6. SCAN, 10 s silence, TUR.
7. Loop until the 300 s deadline, EndOfData or any ScanReadError: READ/WRITE GAIN/OFFSET, then read_lines(4).
8. COPY mask, finish_scan.
The data is reduced by calculate_shading (shading.py:109-182) to a ShadingReference (float means). The raw calibration bytes are dropped.

Transport moves:
- slide() sends SLIDE (4 B).
- _whole_frames/advance/retreat send 04|05 01 00 01 and poll READ STATE byte 2.
- nudge sends 00|01 <param_for_mm> 00 04. The param law comes from protocol.MM_PER_UNIT and COMMAND_UNITS 1.84.
- session_start sends INQUIRY, E7, SENSE and SLIDE 00 01 00 00.

Out of this area into the library:
- DirectScanner debug: _debug_flush calls library.save after close().
- Session: _file shape guard, then FrameWriter._write calls library.save during the session (raw_image, or image when raw_image is missing, which is the GUI Scan case), passing capture_record() (reference, ccd_mask, raw, raw_layout). library.save writes scan.tif, shading.npz, ccd_mask.bin, raw.bin.gz and scan.json (a whitelisted subset of meta; the inquiry argument is ignored).
- tools/scan.py and tools/scan_roll.py build the same library.save calls from last_pixels_raw/raw_image plus capture_record.

Capture analysis (offline):
- usbpcap._blocks and packets (all records, all payloads) feed setups and scanner_devices.
- parse_capture.stream concatenates payload[8:] of scanner control setups to 0x85. parse() splits that into CDBs plus data-out for 0x0A/0x15/0xDC/0xD1 and resyncs on unknown bytes.
- verify_capture counts opcodes, checks for 0xD2, compares MODE SELECT fields and rebuilds MODE SELECT through DirectScanner.set_mode on tests.conftest.FakeTransport.

Demo seam:
- DemoScanner replaces DirectScanner at session._open_scanner. It has no transport and no protocol calls.
- It reuses DirectScanner.decode_index, byte14_for, param_for_mm, roll_ends, place_on_strip and _hold_to_approved.
- Its position() never returns None, and scan/prescan never raise ShadingUnavailable.
