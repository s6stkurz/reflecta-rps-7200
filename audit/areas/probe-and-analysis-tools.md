# Probe and analysis tools

Area key `probe-and-analysis-tools`. 27 findings: 1 critical, 5 high, 10 medium, 10 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Probe and analysis tools (13 files, about 4.6k lines). Eight of them drive the device: hold_probe, transport_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth and roll_registration_walk. Each builds a DirectScanner directly, so none of them goes through ScanSession, session._open_scanner or the demo seam. They rely on DirectScanner debug filing (RPS7200_DEBUG) to put their passes in the library. Five tools work offline: exposure_headroom, linearity, dpi_analysis, registration_margin and roll_registration_study. They read library entries through library.decode_raw, library.corrected or _deinterleave, or read rolls/ TIFFs and manifests.


The central problem is that probe passes do reach the library, but they are not self-describing. Several things are missing from the entry: the variable each probe exists to vary (byte14, blue gain), the probe, step or rung it belongs to, the capture time, the INQUIRY data and the calibration bytes.


That gap causes concrete failures:
- library.signature() treats a whole byte14 or gain ladder, every repeat pair and every walk prescan as interchangeable. `tools/library.py duplicates --delete` would destroy them.
- byte14_probe's final pass is filed with the previous pass's raw bytes, because `last_raw` is stale when `keep_raw=False`.
- A long probe that is killed rather than interrupted files nothing: meta, reference and mask live only in RAM until close().
- byte14_probe runs a whole extra scan in its `finally` block after any failure or Ctrl-C, including one that interrupted a read. Other cleanup paths also keep talking to a device that may have an abandoned read.

Transport probes restore by the distance they commanded, not the distance they measured. They use millimetres, which CLAUDE.md prohibits, and they carry stale figures: a confidence floor of 40 against the real 55, and minimum moves of 0.272 and 0.2719 mm against the real 0.3002. transport_probe is a stale, ungated tool that measures shift with film_bounds, and film_bounds reports 0 mm on a loaded strip.


The analysis tools have these defects:
- exposure_headroom wraps 8-bit prescans through 16-bit arithmetic. Prescans are what its default "newest 4 entries" selection picks up. It also ignores the CCD mask when it simulates the dark floor.
- registration_margin validates CONFIDENCE_FLOOR against a statistic that is not the one measure_shift_mm uses: upright only, versus the better of upright and flipped.
- roll_registration_study --ensemble replays a detector that neither production path uses any more; both hand frame_edges.walk_reader in instead.
- dpi_analysis compares uncorrectable, pre-realignment 7200 dpi raw passes against corrected lower-dpi passes.
- exposure_probe's `--only` chunking rewinds the strip by default, so chunk 2 re-walks the same frames under new numbers.

Ten of the 13 tools have no tests.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [PA-01](#probe-and-analysis-tools-pa-01) | critical | data-integrity | `library.py duplicates --delete` would destroy probe ladders, repeat pairs and whole walk corpora filed by debug mode | [P11](../problems/P11-duplicates-delete-destroys-scans.md) |
| [PA-02](#probe-and-analysis-tools-pa-02) | high | data-integrity | A keep_raw=False pass after a keep_raw=True pass is filed with the previous pass's raw bytes (byte14_probe final pass) | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [PA-03](#probe-and-analysis-tools-pa-03) | high | data-integrity | Probe entries lack the parameters that define them: byte14, slide_init_param, capture time, INQUIRY, and which probe, step, frame or rung they belong to | [P09](../problems/P09-record-missing-parameters.md) |
| [PA-04](#probe-and-analysis-tools-pa-04) | high | data-integrity | A probe killed rather than interrupted files nothing: meta, reference, mask and layout exist only in RAM until close() | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [PA-05](#probe-and-analysis-tools-pa-05) | high | hardware-safety | Cleanup paths keep talking to a device that may have an abandoned read; byte14_probe runs a whole extra scan in `finally` | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [PA-23](#probe-and-analysis-tools-pa-23) | high | data-integrity | Calibration bytes are never stored: the library's shading.npz is a derived, heuristic parse and cannot be recomputed | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [PA-06](#probe-and-analysis-tools-pa-06) | medium | bug | exposure_headroom treats 8-bit prescans as 16-bit (the uint8 cast wraps), and its default selection picks them | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [PA-07](#probe-and-analysis-tools-pa-07) | medium | bug | exposure_headroom's dark-floor simulation ignores the CCD mask and depth scaling, and applies negative-film blue constants to every film | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [PA-08](#probe-and-analysis-tools-pa-08) | medium | bug | registration_margin (and transport_truth) validate a different statistic from the one measure_shift_mm gates on | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [PA-10](#probe-and-analysis-tools-pa-10) | medium | user-error | exposure_probe `--only` chunking rewinds by default, so the next chunk re-walks the same frames under new numbers; --json is overwritten per chunk | -- |
| [PA-11](#probe-and-analysis-tools-pa-11) | medium | bug | exposure_probe reads delivered levels from a region re-detected on the metered pass, not from the region metering used | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [PA-12](#probe-and-analysis-tools-pa-12) | medium | hardware-safety | transport_probe is stale and ungated: no ask/dry-run/debug gate, sends value bytes of unknown meaning, and film_bounds reads 0 mm on a loaded strip | -- |
| [PA-13](#probe-and-analysis-tools-pa-13) | medium | bug | Transport probes restore by the distance they commanded, not the distance measured, across a direction reversal; the walk log overwrites what was actually sent | -- |
| [PA-14](#probe-and-analysis-tools-pa-14) | medium | bug | dpi_analysis compares uncorrectable, pre-realignment 7200 dpi raw passes against corrected lower-dpi passes | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [PA-A1](#probe-and-analysis-tools-pa-a1) | medium | demo-divergence | Only the probes call session_start(): they run a different device sequence from the product (0xE7, plus a SLIDE 00 01 00 00 that moves the film) while their docstrings say nothing moves | -- |
| [PA-A2](#probe-and-analysis-tools-pa-a2) | medium | data-integrity | The library's re-decode paths skip the 7200 dpi stagger realignment that scan() bakes into scan.tif, so every such entry fails reconstruct, and the entry does not record that it was realigned | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [PA-09](#probe-and-analysis-tools-pa-09) | low | design | roll_registration_study --ensemble replays a detector that production no longer uses (the frame_edges reader replaced it) | -- |
| [PA-15](#probe-and-analysis-tools-pa-15) | low | user-error | The probes' debug gate accepts any non-empty RPS7200_DEBUG value, but DirectScanner files only for 1/true/yes/on | -- |
| [PA-16](#probe-and-analysis-tools-pa-16) | low | bug | byte14_probe: a custom --ladder without 0x10 crashes before the JSON is written; the guard is an upper bound only | -- |
| [PA-17](#probe-and-analysis-tools-pa-17) | low | bug | gain_probe places blue from a metering level that can be stale; exposure_probe's docstring claims the opposite staleness | -- |
| [PA-18](#probe-and-analysis-tools-pa-18) | low | doc-mismatch | Probes carry stale transport figures and a stale confidence floor, printed at runtime, and use millimetres despite CLAUDE.md | -- |
| [PA-19](#probe-and-analysis-tools-pa-19) | low | bug | hold_probe's 'total travel' stop only checks the requested offset; --restore sends a single reversing command | -- |
| [PA-20](#probe-and-analysis-tools-pa-20) | low | bug | linearity --probe joins on an exposure triple across the whole library and picks the oldest match | -- |
| [PA-21](#probe-and-analysis-tools-pa-21) | low | bug | roll_registration_study hard-codes a 428-px 300 dpi geometry and 8-bit absolute thresholds, and shadows framing's BASE_TOLERANCE | -- |
| [PA-22](#probe-and-analysis-tools-pa-22) | low | user-error | fast_ir_probe accepts bw/kodachrome and refuses only after metering at 1800 dpi; its docstring says it meters in RGBI | [P16](../problems/P16-timeouts-and-runtime-budget.md) |
| [PA-25](#probe-and-analysis-tools-pa-25) | low | doc-mismatch | Contradictory claims about whether the vendor ever sends SLIDE_PREV | -- |
| [PA-24](#probe-and-analysis-tools-pa-24) | info | test-gap | Device probes bypass ScanSession, tools and demo, and 10 of 13 area tools have no tests | -- |

## Findings in full

<a id="probe-and-analysis-tools-pa-01"></a>

### PA-01 -- `library.py duplicates --delete` would destroy probe ladders, repeat pairs and whole walk corpora filed by debug mode

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P11](../problems/P11-duplicates-delete-destroys-scans.md)

**Where:** `rps7200/library.py:686-698`, `rps7200/library.py:714-738`, `tools/library.py:115-129`, `rps7200/direct.py:719-724`, `tools/byte14_probe.py:142-151`, `tools/gain_probe.py:158-183`, `tools/fast_ir_probe.py:97`, `tools/exposure_probe.py:106`, `tools/roll_registration_walk.py:107-110`, `tools/hold_probe.py:136-156`, `tools/transport_truth.py:125-147`

Every entry from one probe run shares a single signature. The byte14 ladder (13 passes, same frame, dpi and commanded exposure; only byte14 differs) is one group. So is the gain ladder, where only blue gain differs. Each fast-IR on/off triple is a group. exposure_probe's two x1.00 anchors are a group. All two-per-frame walk prescans and ladder rungs are one group, because they are 8-bit, FULL_FRAME and metered. signature() assumes that entries sharing a signature are interchangeable ('neither holds anything the other does not'). Repeat pairs contradict that: their differing random noise is the whole measurement behind noise_split and agreement_z.

**Evidence (from the code):**

```text
signature() = (film.stock, film.frame, film.subject, resolution_dpi, channels, frame, depth, film, protocol_revision, commanded, fast_infrared) (library.py:686-698). Debug filing hard-codes the film notes and tags for every probe pass: `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]` (direct.py:722-723), so stock, frame and subject are all empty. byte14 is not a recorded field. gain is in device_settings, and signature() ignores it. tools/library.py:127-129: `if args.delete: shutil.rmtree(path)` for every entry `prunable()` returns.
```

**Failure scenario:** An operator runs `uv run python tools/library.py duplicates --delete` to free space after a registration walk and a byte14 ladder. prunable() keeps one entry per group and deletes the rest with shutil.rmtree. That removes about 95 of 96 walk prescans, 12 of 13 byte14 passes, 5 of 6 gain rungs and one pass from every repeat pair, with no way back. The dry-run output even labels them 'same scan of the same picture'.

**Fix:** Record the varied parameters in meta and in the scan.json 'scan' block, and put them in signature(): byte14, slide_init_param and device_settings.gain. Let debug filing take a caller-supplied label or tag (probe name, step, rung, frame), or give each pass a unique capture sequence id, and put that in signature(). Never treat entries tagged debug as interchangeable. Make `--delete` refuse debug-tagged entries unless an explicit flag is given.

<details><summary>Second reader's check</summary>

I checked this in the code. signature() (rps7200/library.py:676-698) keys on film stock, frame and subject, dpi, channels, frame, depth, film, protocol_revision, commanded exposure and fast_infrared. _debug_flush always files with `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]` (direct.py:719-724), so stock, frame and subject are empty for every probe pass. scan()'s meta has no byte14 (direct.py:2760-2804), and gain lives only in device_settings, which signature() ignores. That collapses three groups. First, the byte14 ladder (auto_exposure=False, the same commanded scales and FULL_FRAME) is one signature. Second, the gain ladder is one signature. Third, every prescan is one signature: prescan() calls scan() with auto_exposure=False and exposure_scale=1.0, so commanded=(1.0,), and fast_infrared defaults to True. The third group covers the walk p1/p2 pairs, the ladder rungs, and the prescans from hold_probe and transport_truth. tools/library.py:115-129 runs shutil.rmtree on every entry prunable() returns and labels each 'same scan of the same picture'. The signature docstring even argues that a bracket and a fast-IR ladder must not collapse, and still leaves byte14 and gain out.

</details>

<a id="probe-and-analysis-tools-pa-02"></a>

### PA-02 -- A keep_raw=False pass after a keep_raw=True pass is filed with the previous pass's raw bytes (byte14_probe final pass)

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:1518-1532`, `rps7200/direct.py:641-646`, `rps7200/direct.py:673-684`, `tools/byte14_probe.py:181-185`, `tools/transport_probe.py:57`

With debug filing on, the final default-byte14 pass is filed together with the raw.bin.gz and layout of the pass before it. The layout (lines, width, channels) matches exactly, so even the shape check that session.py:2083-2098 applies to its own filing would not catch it. The debug path has no such check at all. The entry's raw bytes decode to a different photograph than its scan.tif: different noise, and it could have a different read direction. transport_probe's prescans take the other branch: they never set keep_raw, so they are filed with no raw bytes at all.

**Evidence (from the code):**

```text
read_planes: `if keep_raw: self.last_raw = blob; self.last_raw_layout = {...}`. Nothing clears it when keep_raw is False (direct.py:1518-1532). capture_record returns `"raw": self.last_raw, "raw_layout": self.last_raw_layout` (641-646). _debug_capture spools `raw = record.get("raw")` as NNN-raw.bin without any check (679-684). byte14_probe's final pass: `scanner.scan(resolution=args.resolution, infrared=False, frame=FULL_FRAME, exposure_scale=scales, auto_exposure=False, shading=False, keep_raw=False, byte14=None)` runs right after 13 keep_raw=True passes at the same geometry.
```

**Failure scenario:** After a byte14 run, `tools/library.py reconstruct` reports 'decode CHANGED: N samples differ' for the last entry, a false regression alarm. Worse, an offline analysis that re-decodes that entry's raw bytes gets the previous pass's pixels under the final pass's metadata (byte14=default, not 0x31).

**Fix:** Clear last_raw and last_raw_layout at the start of every scan(), or in read_planes when keep_raw is False. In _debug_capture, file raw only if it was captured by this pass (tag it with a pass counter). Pass keep_raw=True in byte14_probe's final pass and in transport_probe.look().

<details><summary>Second reader's check</summary>

last_raw is assigned only at direct.py:490 (None) and at direct.py:1521 inside `if keep_raw:`, so nothing clears it. capture_record() returns it unconditionally (641-646), and _debug_capture spools it whenever it is not None (679-683). byte14_probe's `finally` pass (byte14_probe.py:182-185) uses keep_raw=False, the same resolution and FULL_FRAME, and depth 16 RGB, straight after 13 keep_raw=True passes. So it is filed with pass 13's bytes and a layout that matches exactly. The comment at direct.py:2814 ('never a stale leftover the way `last_raw` once was') concedes that last_raw can go stale. The same mechanism catches any scan(keep_raw=False) that follows auto_exposure, because the metering probes use keep_raw=True (direct.py:2091). In that case the layout differs, which is a louder failure. transport_probe prescans run with last_raw still None, so they are filed with no raw bytes, as the finding says.

</details>

<a id="probe-and-analysis-tools-pa-03"></a>

### PA-03 -- Probe entries lack the parameters that define them: byte14, slide_init_param, capture time, INQUIRY, and which probe, step, frame or rung they belong to

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2760-2804`, `rps7200/direct.py:1112`, `rps7200/direct.py:672`, `rps7200/direct.py:719-729`, `rps7200/library.py:142`, `rps7200/library.py:237-261`, `tools/byte14_probe.py:150`, `tools/fast_ir_probe.py:216`, `tools/exposure_probe.py:187`, `tools/exposure_probe.py:277-283`, `tools/roll_registration_walk.py:102-110`

The owner's central requirement is that every entry can be recalculated and evaluated later. For probe runs that does not hold. A byte14 ladder entry cannot say which byte14 it used. A fast-IR probe pass cannot say it used 0x20 rather than the RGBI default 0x21. Capture time is lost, which breaks drift or time analysis and any ordering by capture. Device identity and firmware are dropped. Which frame, rung or step a pass belongs to exists only in an optional side JSON (exposure_probe, fast_ir, byte14, gain) or in a TIFF corpus outside the library (roll_registration_walk). Nothing links that corpus to the raw entries, so it cannot be re-derived when the correction improves.

**Evidence (from the code):**

```text
The meta dict in scan() (direct.py:2760-2804) has no byte14 or slide_init_param key, although set_mode writes `data[14] = byte14 if byte14 is not None else self.byte14_for(passes)` (1112). `item = {"meta": dict(meta), "captured": time.time()}` (672), but `captured` is never read: library.save(...) at 719-729 does not receive it, and entry ids and `created` are flush time. library.save accepts `inquiry: Any = None` (142) and never writes it. exposure_probe itself says the JSON is 'the join key back to the library ... Entries are filed after close() and named from flush time', yet `--json` defaults to None (187). roll_registration_walk writes corrected TIFFs named A01_p1.tif with no library entry id in walk-X.json.
```

**Failure scenario:** exposure_probe is run without --json. It spends 40 minutes and files about 60 entries named by flush time with identical notes. linearity.py --probe then cannot group them by frame. Separately, a later reader of the byte14 entries cannot tell the 0x21 passes (row reversal) from the 0x10 ones.

**Fix:** Add byte14 (the effective value, not only the override), slide_init_param and quality bits to meta and to library.save's 'scan' block. Pass item['captured'] into library.save and record it. Persist the inquiry. Give DirectScanner a filing context (for example `scanner.debug_context = {"probe": ..., "step": ...}`) that _debug_capture copies into tags or notes. Make --json mandatory in exposure_probe. Have the walk log record each pass's library entry id.

<details><summary>Second reader's check</summary>

set_mode writes `data[14] = byte14 if byte14 is not None else self.byte14_for(passes)` (direct.py:1112), and slide_init_param is sent at direct.py:2647. Neither appears in meta (2760-2804) or in library.save's 'scan' key list (library.py:237-261). The default byte14 can be re-derived from the channel count, but the overrides that byte14_probe (every value) and fast_ir_probe (0x20) send cannot. `captured` is set at direct.py:672 but never passed to library.save. save()'s `inquiry` parameter (library.py:142) never reaches `record`, although _debug_flush passes `inquiry=self._inquiry`. exposure_probe's --json defaults to None (187), yet its own comment at 277-283 calls it 'the join key back to the library ... load-bearing'. The walk log records only TIFF names and no library ids.

</details>

<a id="probe-and-analysis-tools-pa-04"></a>

### PA-04 -- A probe killed rather than interrupted files nothing: meta, reference, mask and layout exist only in RAM until close()

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:650-692`, `rps7200/direct.py:777-788`, `tools/fast_ir_probe.py:4`, `tools/exposure_probe.py:4-5`, `tools/roll_registration_walk.py:232-236`

A Python process that is terminated by the harness's 10-minute kill (SIGTERM or SIGKILL), by the OS or by a crash never runs its finally block. The result is an orphaned rps7200-debug-* directory of pixel and raw files with no layout, meta, reference or mask. Those cannot be filed or decoded (raw bytes without bytes_per_line or width). Nothing cleans that directory up or recovers from it. A KeyboardInterrupt does flush, which makes the gap easy to miss.

**Evidence (from the code):**

```text
_debug_capture writes only `NNN-image.npy` and `NNN-raw.bin` to the spool, and keeps `item["meta"]`, `item["reference"]`, `item["ccd_mask"]` and `item["raw_layout"]` in `self._debug_pending` (672-690). Filing happens only in `close()` -> `self._debug_flush()` (788). Probe docstrings expect long runs: fast_ir_probe 'about 25 minutes'; exposure_probe 'About 40 minutes ... background it'.
```

**Failure scenario:** fast_ir_probe is started in the foreground and killed at 10 minutes, after 2 of 6 RGBI 1800 dpi passes plus metering. No library entry exists. About 150 MB is left orphaned in /tmp. The scanner time is lost, and CLAUDE.md warns that a killed read may also have wedged the device.

**Fix:** Write a small per-item JSON sidecar (meta, layout, mask bytes, reference .npz) into the spool at capture time, which is cheap and uncompressed. Add a recovery path, `tools/library.py recover-spool`, that files leftovers. Probes that expect to run over 8 minutes should refuse to run in the foreground, or at least warn at start rather than only in the docstring.

<details><summary>Second reader's check</summary>

_debug_capture writes only NNN-image.npy and NNN-raw.bin (direct.py:675-683). meta, reference, ccd_mask and raw_layout stay in self._debug_pending (672, 684-690), and they are filed only by close() -> _debug_flush() (777-788). Python runs no `finally` on SIGKILL, and none on SIGTERM under the default handler, so the metadata is lost. Nothing in the codebase recovers an orphaned rps7200-debug-* directory. Without the layout (bytes_per_line, width, channels) and the reference, the spooled raw bytes cannot be decoded or corrected. fast_ir_probe (about 25 min) and exposure_probe (about 40 min) warn to background only in their docstrings and dry-run text.

</details>

<a id="probe-and-analysis-tools-pa-05"></a>

### PA-05 -- Cleanup paths keep talking to a device that may have an abandoned read; byte14_probe runs a whole extra scan in `finally`

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/byte14_probe.py:172-192`, `tools/exposure_probe.py:312-333`, `tools/gain_probe.py:207-231`, `rps7200/direct.py:2679-2685`

A Ctrl-C or ScanReadError/TimeoutError in the middle of read_planes leaves the device mid-pass with unread lines. That is the 'abandoned read' CLAUDE.md names as a wedge cause. Each probe's cleanup then issues more commands. byte14_probe issues a whole new scan sequence: READ STATE, set_scan_frame, MODE SELECT, SLIDE INIT, START SCAN and about 30 s of reads. exposure_probe sends up to N SLIDE_PREV commands, each followed by a 30 s poll, and it does not check that the counter readings are plausible, so a stale position of 72 would mean 72 retreats. gain_probe writes the gain register. An operator who presses Ctrl-C to stop the scanner gets another scan from byte14_probe. gain_probe also exits 0 for a failed run when some results exist.

**Evidence (from the code):**

```text
byte14_probe: `except BaseException as exc: ... if not isinstance(exc, (KeyboardInterrupt, CheckCondition)): raise` then `finally: try: scanner.scan(resolution=args.resolution, infrared=False, frame=FULL_FRAME, ...)`, a full pass on every exit path. exposure_probe finally: `for _ in range(back): if scanner.retreat() is None: break` after catching KeyboardInterrupt. gain_probe: `except BaseException` then `real_get(); scanner.set_gain_offset(reference)`, and it returns `0 if results else 1` even after a failure. scan() on an interrupted read only does `self._scanning = False; raise` (direct.py:2679-2685).
```

**Failure scenario:** byte14_probe is interrupted with Ctrl-C at pass 7 during read_planes. The finally block immediately issues MODE SELECT and START SCAN on a device still streaming pass 7. The device stops responding and needs a power cycle, and CheckCondition/NameError output is swallowed as 'final default pass failed'.

**Fix:** Run the 'for the device's sake' pass only when the ladder finished normally, never after an exception. After a read failure, do nothing except close(). Gate exposure_probe's rewind on normal completion, and use DirectScanner.plausible_position() on the positions. Make gain_probe re-raise non-CheckCondition exceptions and exit non-zero.

<details><summary>Second reader's check</summary>

In byte14_probe.py:172-192 the `except` re-raises non-KeyboardInterrupt/CheckCondition errors, but the `finally` block still runs a full scan() on every exit path, including a ScanReadError or TimeoutError out of read_planes. scan() only does `self._scanning = False; raise` on a mid-read exception (direct.py:2679-2685). If auto_exposure itself raised, `scales` is unbound, so that pass dies with NameError, which is swallowed and printed as 'final default pass failed'. exposure_probe.py:316-327 sends SLIDE_PREV `back` times on every exit path, KeyboardInterrupt included, and never validates the positions with plausible_position. gain_probe.py:207-231 re-reads the register and writes it back after any exception, and returns `0 if results else 1`, so a run that failed partway exits 0. The same pattern appears in hold_probe.py:225-232: its `finally` issues read_state() after any exception, including an interrupted read, although that is milder than a new scan.

</details>

<a id="probe-and-analysis-tools-pa-23"></a>

### PA-23 -- Calibration bytes are never stored: the library's shading.npz is a derived, heuristic parse and cannot be recomputed

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1946-1950`, `rps7200/direct.py:1966`, `rps7200/shading.py:158-176`, `rps7200/library.py:182-183`, `tools/exposure_headroom.py:119-124`

The owner requires 'the exact bit data ... so that everything can be recalculated'. The shading reference every probe entry carries comes out of a heuristic (a level split with a ratio threshold, averaging), and the 1.66 MB of calibration lines it came from are discarded. A change to calculate_shading, for example how dark and light phases are separated, which the module itself calls unsettled, cannot be re-run on any stored entry. exposure_headroom's dark-floor model depends directly on that derived split. This is cross-cutting, but every probe in this area inherits it.

**Evidence (from the code):**

```text
`data = b"".join(collected); self._shading = calculate_shading(data, width)` and `"data": data if keep_data else None` (keep_data defaults to False). calculate_shading splits dark from light 'by level', at `cut = ranked[int(np.argmax(gaps))]` with split_ratio=5.0. library.save stores only `reference.save(path / "shading.npz")`.
```

**Failure scenario:** The two-phase split is later found to misclassify a few lines. Every stored reference keeps the error for good, and exposure_headroom's and every correction's dark floors cannot be rebuilt.

**Fix:** Keep the calibration bytes (keep_data=True) plus the descriptor and bytes-per-line with the session. Store them next to shading.npz (for example calibration.bin.gz, deduplicated by sha256 across entries) and let library.corrected re-derive the reference from them.

<details><summary>Second reader's check</summary>

calibrate_shading joins the calibration bytes, keeps only `calculate_shading(data, width)` (direct.py:1946-1950), and returns `"data": data if keep_data else None` with keep_data defaulting to False. The auto-calibrate call at direct.py:2592 passes no keep_data. calculate_shading's dark/light split is a heuristic based on a ratio gap (shading.py:158-176). library.save stores only reference.save(shading.npz) (library.py:182-183). The source bytes are therefore never kept, so no change to calculate_shading can be re-run on a stored entry. Raised to high because it strikes directly at the owner's 'exact bit data, everything recalculable' requirement, for every entry, not only probe entries.

</details>

<a id="probe-and-analysis-tools-pa-06"></a>

### PA-06 -- exposure_headroom treats 8-bit prescans as 16-bit (the uint8 cast wraps), and its default selection picks them

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/exposure_headroom.py:78`, `tools/exposure_headroom.py:127-137`, `tools/exposure_headroom.py:183-189`, `tools/exposure_headroom.py:283-295`

Debug filing and the roll path put 8-bit 300 dpi prescans in the library, and they are usually the newest entries. For a uint8 image, achieved is at most 255/65535 = 0.004, so wanted() asks for k of about 200. scale_exposure clips at 65535 and then casts to uint8, which wraps modulo 256 (or is undefined). The dark floor is subtracted in 16-bit counts from 8-bit samples. The clipping percentages and levels reported are meaningless, and nothing says so.

**Evidence (from the code):**

```text
`FULL_SCALE = 65535.0` (78). scale_exposure: `out = np.empty_like(image)` ... `np.clip(vals, 0, FULL_SCALE, out=vals); out[..., c] = vals.astype(image.dtype)` (131-136). achieved: `float(np.percentile(base[..., c], PERCENTILE)) / FULL_SCALE` (184-187). The selection is `for path in sorted(root.glob("2*/"), reverse=True)` with no depth filter (283), and without --dpi it takes the newest 4 entries.
```

**Failure scenario:** `uv run python tools/exposure_headroom.py` after a roll or walk studies 4 prescans and prints garbage clip% for each target. Someone moves EXPOSURE_TARGET on that basis.

**Fix:** Filter on record['scan']['depth'] == 16 (and skip calibration.skipped entries). Derive the full scale from the image dtype, as apply_shading does (np.iinfo). Never cast clipped floats back to a narrower dtype without clipping to that dtype's max.

<details><summary>Second reader's check</summary>

_deinterleave -> decode_index chooses uint8 when bytes_per_line/width == 1 (direct.py:1575-1577), so an 8-bit prescan decodes to uint8. `achieved` divides by FULL_SCALE=65535 (exposure_headroom.py:184-187), which gives at most 0.0039, so wanted() returns k of about 200. scale_exposure clips at 65535 and then casts to image.dtype, uint8 (131-136); the cast wraps or is undefined for values above 255. dark_floor subtracts 16-bit-unit dark counts from 8-bit samples without depth_scale. The selection (283-295) has no depth filter and takes the newest 4 entries. Downgraded to medium: the printed 'landed at R=0%' line makes the garbage fairly visible, and the tool only informs a manual decision.

</details>

<a id="probe-and-analysis-tools-pa-07"></a>

### PA-07 -- exposure_headroom's dark-floor simulation ignores the CCD mask and depth scaling, and applies negative-film blue constants to every film

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/exposure_headroom.py:119-124`, `tools/exposure_headroom.py:82`, `tools/exposure_headroom.py:155-160`, `rps7200/shading.py:185-193`, `rps7200/shading.py:260-263`, `rps7200/direct.py:351-372`

Below native resolution, output column j corresponds to the j-th used CCD element, not reference column j. So the simulated raw' = (raw - dark)*k + dark uses the wrong column's dark offset, which the shading module says varies 12-15% from column to column. Blue's aim for RGBI entries is modelled with the colour-negative ratio and headroom whatever the film, which contradicts the per-film blue_rgbi_headroom the driver meters with. These are retyped constants that have drifted.

**Evidence (from the code):**

```text
`dark = reference.dark[channel]; return dark[:width] if dark.size >= width else np.zeros(width)` (119-124). apply_shading instead maps columns with `loc = build_width_to_loc(bytes(ccd_mask), w)` and `dark = reference.dark[c][loc] * depth_scale` (shading.py:239,262). `MEASURED_BLUE_RATIO = 4.98` and `aims[2] = target * MEASURED_BLUE_RATIO / BLUE_RGBI_HEADROOM` (82,159), whereas the driver uses `blue_rgbi_headroom(film)` (11.0 for positive and kodachrome).
```

**Failure scenario:** A 600 dpi RGBI slide entry: the simulated lift puts a column-pattern error into the dark term, and blue is aimed at 0.80*4.98/5.2 when the driver would aim it at 0.80/11. Blue clipping is then over-reported for slides.

**Fix:** Reuse build_width_to_loc(mask, w) and depth_scale for the dark floor, or factor the per-column dark lookup out of apply_shading and call it. Take the blue headroom from blue_rgbi_headroom(record film). Store the measured ratio per film rather than as one constant.

<details><summary>Second reader's check</summary>

dark_floor uses `dark[:width]` (exposure_headroom.py:119-124), but apply_shading maps output columns through build_width_to_loc(mask, w) and scales dark by depth_scale (shading.py:239-263). Below native resolution, column j is therefore paired with the wrong reference column. The tool also hard-codes MEASURED_BLUE_RATIO=4.98 and BLUE_RGBI_HEADROOM=5.2 for every film (82, 159), whereas the driver meters with blue_rgbi_headroom(film), which is 11.0 for positive, kodachrome and bw (direct.py:351-372). calibration() loads the mask and then study() uses it only in apply_shading, never in the dark-floor model.

</details>

<a id="probe-and-analysis-tools-pa-08"></a>

### PA-08 -- registration_margin (and transport_truth) validate a different statistic from the one measure_shift_mm gates on

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/registration_margin.py:73-75`, `tools/registration_margin.py:193-199`, `tools/registration_margin.py:220-222`, `rps7200/framing.py:1061-1063`, `rps7200/framing.py:1095-1098`, `rps7200/framing.py:1117-1118`, `tools/transport_truth.py:69-72`, `tools/transport_truth.py:153`

The confidence that production compares against CONFIDENCE_FLOOR is the better of an upright and a row-flipped registration. For different-picture (null) pairs that maximum has a heavier upper tail than a single registration. registration_margin is documented as the tool to re-run after any change to register or SEARCH_MM, but it measures only the upright one, so it under-states the false-positive risk it exists to bound. The reach also differs by rounding: 106 against 105 at a 428-column prescan, and the docstring says the z-score scales with reach. transport_truth retypes both the reach and the floor instead of importing them.

**Evidence (from the code):**

```text
The tool's reach is `int(round(SEARCH_MM / (MM_PER_INCH / dpi)))`, and it runs `register(a, b, max_shift=reach)` once, upright. The production code has `reach = int(SEARCH_MM / max(mm_per_px, 1e-9))` with `mm_per_px = aperture_mm / width`, and then `upright = register(...); flipped = register(reference, now[::-1], ...); reversed_wins = flipped[2] > upright[2]`. Its dy gate is in mm (`dy_mm > MAX_DY_MM`), while the tool uses `abs(dy) <= MAX_DY_PX`. transport_truth hard-codes `max_shift=106` and `if conf < 55:`.
```

**Failure scenario:** After a change to register, registration_margin reports 'CONFIDENCE_FLOOR = 55 holds'. Meanwhile a different-picture pair whose flipped reading scores 57 would pass measure_shift_mm and move the film to the wrong place, and the validator never evaluates that reading.

**Fix:** Call framing.measure_shift_mm, or a factored-out function returning (confidence, dy, dx, row_reversed) from both readings, in registration_margin and transport_truth instead of register(). Import CONFIDENCE_FLOOR and derive the reach from the same function.

<details><summary>Second reader's check</summary>

measure_shift_mm computes both `upright` and `flipped` registrations and gates on the stronger (framing.py:1095-1098). Its reach is int(SEARCH_MM / (aperture_mm/width)) (1061-1063), and its dy gate is in mm (1117-1118). registration_margin.separation calls register() once, upright, with reach round(SEARCH_MM/(25.4/dpi)) (73-75, 193), and gates on abs(dy) <= MAX_DY_PX (220). At 300 dpi and a 428-column width that is 106 against 105. transport_truth hard-codes max_shift=106 and `conf < 55` (71, 153) instead of importing CONFIDENCE_FLOOR. So the validator never samples the max-of-two null distribution that production actually thresholds.

</details>

<a id="probe-and-analysis-tools-pa-10"></a>

### PA-10 -- exposure_probe `--only` chunking rewinds by default, so the next chunk re-walks the same frames under new numbers; --json is overwritten per chunk

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/exposure_probe.py:180-182`, `tools/exposure_probe.py:188-190`, `tools/exposure_probe.py:240-310`, `tools/exposure_probe.py:316-327`, `tools/exposure_probe.py:299-305`

The documented way to fit the 40-minute run under the 10-minute kill is `--only 1-4`, then `--only 5-8`, and so on. Each chunk ends rewound to its start, so chunk 2 measures physical frames 1-4 again and labels them 5-8. Frame 5 is also a ladder frame under the default plan, so the same photograph gets a second ladder. linearity --probe then counts one photograph as two 'different frames', which is the evidence it uses to separate sensor from picture. Reusing one --json path across chunks silently discards the earlier chunks' rows. The rewind also trusts READ_STATE positions without DirectScanner.plausible_position.

**Evidence (from the code):**

```text
`--only A-B: walk just these frames of the plan, to chunk a run under the harness's ten-minute foreground kill` (180-182). `--no-rewind: leave the film where the walk ended` (188-190), and without it the finally block runs `back = max(positions) - min(positions); for _ in range(back): scanner.retreat()` (320-324). The schedule is filtered by number, but nothing moves the film to frame `lo`. `Path(args.json).write_text(json.dumps({... "passes": passes}))` replaces the whole file (301-305).
```

**Failure scenario:** Following the help text, the operator runs `--only 1-4 --json probe/e.json`, then `--only 5-8 --json probe/e.json`. The JSON now holds only 'frames 5-8', which are really frames 1-4, and linearity reports a 'sensor' departure that is one frame measured twice.

**Fix:** Make --only imply --no-rewind, or refuse --only without it, or position the film explicitly with advance() to frame lo. Append to or merge the JSON by (frame, rung) instead of overwriting, or refuse an existing path. Validate positions with plausible_position before rewinding.

<details><summary>Second reader's check</summary>

--only filters the schedule by plan number (exposure_probe.py:196-202), but nothing positions the film at frame `lo`. Without --no-rewind, the `finally` block retreats max(positions)-min(positions) frames (320-324). So chunk 2 starts on the same physical frame as chunk 1 and labels it 5, and plan frame 5 is a ladder frame ((5-1)%4==0). The JSON is rewritten whole from this run's `passes` (299-305), so reusing the same path across chunks keeps only the last chunk. The positions are never passed through DirectScanner.plausible_position.

</details>

<a id="probe-and-analysis-tools-pa-11"></a>

### PA-11 -- exposure_probe reads delivered levels from a region re-detected on the metered pass, not from the region metering used

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/exposure_probe.py:62-67`, `tools/exposure_probe.py:159-168`, `rps7200/direct.py:2104-2106`, `rps7200/framing.py:176-182`, `tests/test_exposure_probe.py:146-157`

On a metered pass the film sits near 0.80 of full scale, so film_bounds' CLEAR_RATIO gate (2.0) fails. metering_slice then returns the whole window minus a 5% inset, including any visible aperture that the metering excluded. The per-frame 'in band / OVER / under' verdict then compares a different region from the one metering aimed. The docstring claims the level is read 'the way auto_exposure reads a probe', and the test fixture cannot show the difference.

**Evidence (from the code):**

```text
`crop = image[metering_slice(image)]` on each delivered pass (166). auto_exposure detects once on round 1: `if region is None: region = metering_slice(image)` (direct.py:2104-2105). framing.py:176-182 says of this: 'by the round that settles the exposure, the contrast this depends on is gone and the bounds come back as the whole window. Detecting once, while the film is still dark ... is the only time it works.' The test uses film at 20000/65535 against a 65535 aperture, a ratio above CLEAR_RATIO, which is not the metered case.
```

**Failure scenario:** At the start or end of a strip, where the aperture is visible, the delivered p99.5 includes clipped aperture columns. The report marks the frame OVER and lists it as a metering miss, when metering landed correctly inside the film.

**Fix:** Record the metering region: have auto_exposure put the slice bounds into last_metering. exposure_probe should apply that saved region to the delivered passes. Add a test with film at around 0.8 next to an aperture.

<details><summary>Second reader's check</summary>

levels_of runs metering_slice on each delivered, shading-corrected pass (exposure_probe.py:159-168). auto_exposure detects its region once, on round 1, and reuses it (direct.py:2104-2105). framing.metering_region's docstring (176-182) says re-detecting on a bright pass returns the whole window, because the clear_ratio gate in film_bounds.span fails (framing.py:123-124). With aperture in view, the delivered p99.5 therefore includes the saturated aperture, while metering excluded it. The claim in exposure_probe's docstring (62-64) that it reads the level 'the way auto_exposure reads a probe' is false for exactly that reason.

</details>

<a id="probe-and-analysis-tools-pa-12"></a>

### PA-12 -- transport_probe is stale and ungated: no ask/dry-run/debug gate, sends value bytes of unknown meaning, and film_bounds reads 0 mm on a loaded strip

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/transport_probe.py:13-19`, `tools/transport_probe.py:46-52`, `tools/transport_probe.py:55-61`, `tools/transport_probe.py:76`, `tools/transport_probe.py:104-116`, `tools/transport_probe.py:135-139`, `rps7200/framing.py:108-126`, `rps7200/direct.py:1704-1715`

This is the kind of bypass probe CLAUDE.md warns about. It reports a 0.00 mm shift for every move on a loaded strip, because x0 is the window edge before and after. It sends transport payloads whose value byte has an unknown meaning without asking and without --dry-run. It leaves the film about 3 frames advanced (4 x SLIDE_NEXT against 1 x SLIDE_PREV) with no rewind. Unless RPS7200_DEBUG happens to be set its passes are never filed, and even when it is set they are filed without raw bytes. It also skips session_start(), whose docstring says 0xE7 'may be what puts the scanner into a state where calibration is accepted', and the calibration then runs anyway.

**Evidence (from the code):**

```text
There is no --dry-run and no RPS7200_DEBUG check, and it starts with `with DirectScanner(verbose=True) as s:` (76). PROBES sends `SLIDE_NEXT, 0x01, 2` and `0` ('last byte 2: a step count, or something else?') (46-52). `image, _ = scanner.prescan(resolution=dpi)` does not set keep_raw (57). `shift = after["x0"] - before["x0"]` comes from registration() -> film_bounds, which `return lo, hi  # no empty aperture in view` on a loaded strip (framing.py:125-126). The docstring says 'no shading calibration needed', but prescan() runs `scan(... shading=True ...)` (direct.py:1708), which calibrates when there is no reference. The closing text (135-139) says sub-frame correction is impossible, which the driver's nudge() has since made false.
```

**Failure scenario:** Someone runs `uv run python tools/transport_probe.py` as its docstring shows. It sends `04 01 00 00` and `04 01 00 02`, prints '+0.00 mm' for real whole-frame moves, leaves the strip advanced, and files nothing. The printed conclusion argues against the sub-frame correction the driver now depends on.

**Fix:** Retire the tool, or bring it in line: add --dry-run, require debug filing, set keep_raw=True, call session_start, measure shift with measure_shift_mm or register instead of film_bounds, drop the undefined value bytes, and rewind at the end. transport_truth already covers the useful question.

<details><summary>Second reader's check</summary>

transport_probe.py has no --dry-run, no RPS7200_DEBUG gate and no session_start/wait_warm, and opens with `with DirectScanner(verbose=True) as s:` (76). PROBES sends SLIDE_NEXT with value bytes 2 and 0 (46-52). look() calls prescan() with keep_raw left False (57), and prescan() runs scan(shading=True) (direct.py:1704-1715), which calibrates when no reference exists. That contradicts the docstring's 'no shading calibration needed' (15). The shift is `after["x0"] - before["x0"]` from registration() -> film_bounds, which returns the window edges whenever no clear aperture is in view (framing.py:123-124), so a loaded strip reads 0. The JSON is written only after a clean exit. The closing message (135-139) predates nudge().

</details>

<a id="probe-and-analysis-tools-pa-13"></a>

### PA-13 -- Transport probes restore by the distance they commanded, not the distance measured, across a direction reversal; the walk log overwrites what was actually sent

**Severity** medium · **Category** bug · **Verdict** partly

**Where:** `tools/roll_registration_walk.py:112-128`, `tools/roll_registration_walk.py:151-152`, `tools/roll_registration_walk.py:167-169`, `tools/transport_truth.py:132-141`, `tools/transport_truth.py:168-176`, `rps7200/direct.py:3126-3136`, `rps7200/direct.py:3167-3170`

roll_registration_walk and transport_truth put the film back with the negated commanded sum, in one direction-reversing command, and do not iterate on measurement. The walk's comment claims the opposite. The walk's rung-0 leg is a single param-7 command (0.934 mm), not 3 x FINE_MIN_MM. The walk log overwrites nudge's commanded asked_mm with the requested figure; param is kept, so the value can be recovered. Its excursion and travel limits count requested, not commanded or delivered, distance. transport_truth measures and reports the post-restore offset but does not correct it. hold_probe's restore distance is measured, not commanded; see PA-19 for its single reversing command.

**Evidence (from the code):**

```text
transport_truth: `scanner.nudge(mm); net += mm` and then `scanner.nudge(-net)`, with the MAX_DRIFT_MM guard also on commanded `net`. The walk: `sent = self.scanner.nudge(mm); self.travel += abs(mm); ... return dict(sent, asked_mm=mm)`, which overwrites nudge's `"asked_mm": round(asked if forward else -asked, 3)` (the distance actually commanded) with the requested value. `back = self._nudge(-excursion, excursion)` sits under the comment '# Put it back. Measured against where the frame started, not summed from the commands', but it is the negated commanded sum. hold_probe: `scanner.nudge(-net)` is a single command against the previous direction.
```

**Failure scenario:** After a ladder on frame 1, the film is left about 0.07 mm plus backlash (up to about 0.3-0.9 mm) from its start. Frames 2-16 of the 'sound strip' corpus then carry that offset, and the study treats them as unmoved.

**Fix:** Restore by measurement: re-prescan, measure_shift_mm against the frame's first pass, and iterate like _hold_to_approved (or call it with target 0). Build the ladder from single param-1 commands so each rung really is FINE_MIN_MM. Keep nudge's asked_mm and store the requested value under a separate key. Enforce the envelope on delivered distance.

<details><summary>Second reader's check</summary>

The walk and transport_truth claims hold. Walk._nudge returns `dict(sent, asked_mm=mm)` (roll_registration_walk.py:128), which overwrites nudge's `asked_mm` (the commanded distance, direct.py:3167-3170). The first leg, -3*0.3002 mm, becomes param_for_mm(0.9006)=7, which is 0.934 mm, so rung 0 is not at the lattice point the docstring promises. The restore `self._nudge(-excursion, excursion)` (169) is the commanded sum, and it reverses direction, despite the comment 'Measured against where the frame started, not summed from the commands'. Net: -0.934+6*0.300-0.934 = -0.067 mm, before backlash. transport_truth's restore `scanner.nudge(-net)` (168-170) is a commanded sum and also reverses direction, although it does then measure and report net_from_start_mm. The finding goes wrong on hold_probe and on what the walk loses. hold_probe restores by the MEASURED net from measure_shift_mm (hold_probe.py:168, 204-206), not a commanded one; the single reversing command is PA-19's point. And the walk's `sent` still carries `param` and `forward`, so the commanded distance can be re-derived from param; only the `asked_mm` field is misleading.

</details>

<a id="probe-and-analysis-tools-pa-14"></a>

### PA-14 -- dpi_analysis compares uncorrectable, pre-realignment 7200 dpi raw passes against corrected lower-dpi passes

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `tools/dpi_analysis.py:69-72`, `tools/dpi_analysis.py:116-117`, `tools/dpi_analysis.py:131`, `tools/dpi_analysis.py:176`, `tools/dpi_analysis.py:216`, `rps7200/library.py:366-384`, `rps7200/direct.py:2695-2701`

Measurement B, aliasing against the 7200 dpi reference, divides corrected lower-dpi spectra by the spectrum of a raw 7200 pass. That pass carries the uncorrected column fixed pattern, and the even/odd 4-line stagger zigzag is still in its stored pixels, both of which add high-frequency row power. Measurement A's noise floor and C's residuals come from the same raw crop. The tool never checks record['corrected'], and its docstring says 'delivered file' when the pixels are a recomputation by library.corrected.

**Evidence (from the code):**

```text
`def plane(entry, channel): """One channel, read back from the delivered file.""" a, _ = library.corrected(entry["dir"])`. The reference is `top = max(rgb, key=lambda e: e["dpi"])` and the default window is 2026-09-11T10:19-10:45. library.corrected's own docstring says the four 2026-09-11 entries include 'two 7200 dpi passes' that come back 'raw -- correction was asked for'. The 7200 dpi stagger realignment was added 2026-09-13 (direct.py:1624-1635, 2695).
```

**Failure scenario:** The report puts ratios near Nyquist below 1.0 for 1800 and 3600 dpi ('resolving') partly because the reference is inflated by stripes and zigzag. The resolution recommendation in docs/dpi-tradeoff-plan.md is then based on artefacts.

**Fix:** Refuse or flag entries whose corrected status is not 'applied', or compare raw with raw throughout. Apply _realign_native_column_stagger to 7200 dpi entries filed before the realignment, or exclude them. Print each entry's corrected status in the series table.

<details><summary>Second reader's check</summary>

plane() calls library.corrected(entry['dir']) and never looks at record['corrected'] (dpi_analysis.py:69-72). The reference is `top = max(rgb, key=dpi)` (116), and measurement A's crop and C's residuals come from that same pass (216-222). The default window is 2026-09-11T10:19-10:45. library.corrected returns the stored pixels raw for 'raw -- correction was asked for' and 'deliberately raw' (library.py:376-383), and its docstring names two 7200 dpi entries from that date as filed raw. The realignment runs only in scan() (direct.py:2695-2701), so older entries keep the zigzag. The library is not present in this checkout, so I could not check which entries fall in the window. The code defect stands regardless: raw and corrected passes are compared without any check. The docstring's 'read back from the delivered file' is also wrong, since this is a recomputation.

</details>

<a id="probe-and-analysis-tools-pa-a1"></a>

### PA-A1 -- Only the probes call session_start(): they run a different device sequence from the product (0xE7, plus a SLIDE 00 01 00 00 that moves the film) while their docstrings say nothing moves

**Severity** medium · **Category** demo-divergence · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1726-1753`, `rps7200/direct.py:1143-1168`, `tools/hold_probe.py:130`, `tools/byte14_probe.py:127`, `tools/byte14_probe.py:17`, `tools/gain_probe.py:129`, `tools/fast_ir_probe.py:5`, `tools/fast_ir_probe.py:192`, `tools/exposure_probe.py:237`, `tools/transport_truth.py:122`, `tools/roll_registration_walk.py:262`

**Doc claim:** rps7200/direct.py:1729-1730 'a SLIDE with `00 01 00 04`'; tools/byte14_probe.py:17 and tools/fast_ir_probe.py:5 'Nothing moves'; CLAUDE.md 'Drive the scanner through this software, not around it'

Every probe opens with a vendor command (0xE7) and a transport command that the operator's path (ScanSession, GUI, scan.py, scan_roll) never sends. So what the probes measure is a device state the product does not create, which CLAUDE.md's 'drive the scanner through this software' rule calls provisional. The opening SLIDE is action 0x00 param 1, the finest forward sub-frame move, and it is sent with value 0x00, a value no capture pairs with that action according to the protocol.md table. That contradicts session_start's own docstring. Each probe run can therefore creep the film about 2.8 units forward with nothing recorded in any log or library entry, while the probe docstrings promise that nothing moves. Repeated walks (--rewind) and repeated hold or transport probes on one strip accumulate this unlogged offset.

**Evidence (from the code):**

```text
session_start: `self.t.command(_cmd(SCSI_VENDOR_E7, 4))` ... `self.slide(0x00, param=0x01)`. slide()'s signature is `def slide(self, action=SLIDE_INIT, param=0x16, value=0)`, so the payload sent is `00 01 00 00`, while the docstring says 'a SLIDE with `00 01 00 04`'. Action 0x00 is the forward sub-frame move that nudge() uses (`self.slide(0x00 if forward else 0x01, param=param, value=0x04)`), and docs/protocol.md:647 measures `00 01 00 04` as 'fine forward, +0.32 mm'. `grep session_start()` finds calls only in the 8 probe tools; rps7200/session.py, tools/scan.py, tools/scan_roll.py and tools/gui.py never call it. byte14_probe.py:17 says 'Nothing moves: the transport is untouched', and fast_ir_probe.py:5 says 'Nothing moves -- the transport is untouched'.
```

**Failure scenario:** The operator runs hold_probe three times on one frame. Each session_start moves the film about 0.3 mm forward before the reference prescan, so the frame ends near 1 mm from where it was approved, and nothing in the JSON or the library records the moves. Separately, a probe finding that 'calibration is accepted' depends on 0xE7, which production never sends.

**Fix:** Either make session_start part of the real open path (DirectScanner.open or ScanSession) so probes and product share one sequence, or remove it from the probes. Send the captured payload (value=0x04) if the opening slide is kept, log it as a sub-frame move in the probe JSON, and correct the 'nothing moves' docstrings.

<a id="probe-and-analysis-tools-pa-a2"></a>

### PA-A2 -- The library's re-decode paths skip the 7200 dpi stagger realignment that scan() bakes into scan.tif, so every such entry fails reconstruct, and the entry does not record that it was realigned

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/library.py:411-436`, `rps7200/library.py:439-499`, `tools/exposure_headroom.py:85-104`, `rps7200/library.py:218`

scan.tif for a 7200 dpi entry is not the decode of its raw bytes; it is the decode plus a host-side geometric correction. Nothing in scan.json says that correction was applied. The library's own verification path does not reproduce it, so `tools/library.py reconstruct` flags every such entry as a changed decode. That is the same false-alarm pattern CLAUDE.md describes for the 26 prescans, and it hides a real regression among 7200 entries. The correction can be re-derived only by knowing the rule 'realign iff dpi == 7200 and filed after 2026-09-13', which is recorded nowhere. Offline tools that decode raw bytes, such as exposure_headroom and linearity's raw mode, get the zigzagged picture.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: image = self._realign_native_column_stagger(image)` and then `raw_pixels = image` is what _debug_capture files. That trims 4 rows and shifts the odd columns. library.reconstruct decodes with `DirectScanner.decode_index(raw, params, ...)` and compares `if image.shape != stored.shape: return image, f"decode CHANGED: now {image.shape}, stored {stored.shape}"`. decode_raw and exposure_headroom.decode call `_deinterleave` without realigning either. `"corrections_applied": list(corrections or [])` stays empty for realigned entries. migrate_direction (library.py:579-582) already knows 'a realigned 7200 dpi pass' is not a plain decode.
```

**Failure scenario:** After a decode change, the operator runs `tools/library.py reconstruct`. Every 7200 dpi entry reports 'decode CHANGED: now (H, ...), stored (H-4, ...)', so a genuine regression in those entries cannot be seen among the alarms.

**Fix:** Move the realignment into decode_index or a shared decode function used by scan(), reconstruct and decode_raw, gated on the layout or resolution. Or keep scan.tif as the plain decode and apply the realignment in library.corrected. Either way, record `stagger_realigned: 4` in the entry.

<a id="probe-and-analysis-tools-pa-09"></a>

### PA-09 -- roll_registration_study --ensemble replays a detector that production no longer uses (the frame_edges reader replaced it)

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/roll_registration_study.py:613-651`, `tools/roll_registration_study.py:833-836`, `rps7200/framing.py:1725-1747`, `rps7200/direct.py:3422`, `tools/gui.py:8186`, `tools/scan_roll.py:476`

The replay re-implements StripWalk.judge's base-level branch. It skips the `aiming` gate, affordable() and record(). It also ignores the reader path that both the GUI and scan_roll now take. Its 'would move / in place / refused' counts therefore describe a code path no operator runs, and the copy will drift from judge() on the next change.

**Evidence (from the code):**

```text
The study builds `walk = StripWalk()` with no reader, then copies judge() inline: `left = frame_offset_mm(image, walk.base); right = right_gap_closure(image, walk.base); prior = predict_offset(walk.history(order), order); decision, detail = combine([left, right, prior])`. Production uses `StripWalk(reader=edge_reader(film) if edge_reader else None)` (direct.py:3422), with `session.edge_reader = frame_edges.walk_reader` (gui.py:8186) and `edge_reader=frame_edges.walk_reader` (scan_roll.py:476). The help text says 'replay the walk through the ensemble that decides corrections'.
```

**Failure scenario:** The study reports '12 would move, 3 refused' for a walk, and that is used to judge roll correction quality. The window, using frame_edges, would have decided differently on those frames.

**Fix:** Replay through StripWalk(reader=frame_edges.walk_reader(film)).judge(...) (with observe()), which is the production object, and offer the base-level fallback only behind an explicit flag. Do not copy judge().

<details><summary>Second reader's check</summary>

report_ensemble builds `StripWalk()` with no reader and inlines the base-level branch of judge() (roll_registration_study.py:613-651). Production builds StripWalk(reader=edge_reader(film)) (direct.py:3422), with the GUI setting session.edge_reader = frame_edges.walk_reader (gui.py:8186) and scan_roll passing edge_reader=frame_edges.walk_reader (scan_roll.py:476). With a reader, judge() delegates to reader.judge and never reaches the base-level code (framing.py:1729-1733). The --ensemble help, 'the ensemble that decides corrections', is therefore stale. Downgraded to low: this is an offline study whose output misleads, and it cannot corrupt data or the device.

</details>

<a id="probe-and-analysis-tools-pa-15"></a>

### PA-15 -- The probes' debug gate accepts any non-empty RPS7200_DEBUG value, but DirectScanner files only for 1/true/yes/on

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/hold_probe.py:114`, `tools/byte14_probe.py:113`, `tools/gain_probe.py:115`, `tools/fast_ir_probe.py:177`, `tools/fast_ir_probe.py:430`, `tools/exposure_probe.py:216`, `tools/transport_truth.py:107`, `tools/roll_registration_walk.py:242`, `rps7200/direct.py:464-469`

The gate is a second copy of a decision DirectScanner already makes, and the two copies disagree. RPS7200_DEBUG=0, =false or =2 passes the probe's 'refusing to run without RPS7200_DEBUG=1' check while filing stays off. That is the exact failure the gate exists to prevent: a long probe that files nothing. RPS7200_DEBUG_ROOT can also redirect filing, and no probe says where entries went.

**Evidence (from the code):**

```text
The probes check `if not os.environ.get("RPS7200_DEBUG"):`. DirectScanner has `os.environ.get(self.DEBUG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}`. Every probe then builds `DirectScanner(verbose=True)` without `debug=True`.
```

**Failure scenario:** The operator exports RPS7200_DEBUG=false from an old shell profile. fast_ir_probe runs for 25 minutes and the library gets no entries.

**Fix:** Construct DirectScanner(debug=True) in every probe, or check `scanner.debug` after construction and refuse on False. Print the resolved library root at start.

<details><summary>Second reader's check</summary>

Every probe gates on `if not os.environ.get("RPS7200_DEBUG")`, and exposure_probe on the same test via DirectScanner.DEBUG_ENV. DirectScanner, however, files only when the value is in {'1','true','yes','on'} (direct.py:464-469). So RPS7200_DEBUG=0 or =false passes the gate and files nothing. None of the probes passes debug=True or checks scanner.debug after construction.

</details>

<a id="probe-and-analysis-tools-pa-16"></a>

### PA-16 -- byte14_probe: a custom --ladder without 0x10 crashes before the JSON is written; the guard is an upper bound only

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/byte14_probe.py:100-106`, `tools/byte14_probe.py:156`, `tools/byte14_probe.py:201`, `tools/byte14_probe.py:226-234`, `tools/byte14_probe.py:1-16`

After a custom run the summary crashes and the JSON results are lost. They were printed, but not written. The docstring promises 'nothing invented', yet any value up to 0x31 is sent, including values no capture contains. The docstring's warning that re-running 'will reproduce reversed passes exactly as the first run did' is also stale, since decode_index now turns bottom-up passes upright from their line tags.

**Evidence (from the code):**

```text
`if byte14 == LADDER[0] and baseline_ms is None` and `base_group = [... if r["byte14"] == LADDER[0]]` use the module constant, not `ladder`. Line 227 `[r for r in results if r['byte14']==LADDER[0]][-1]` raises IndexError when no 0x10 pass exists, and that happens before `if args.json: ... write_text` (230-233). The guard is `over = [v for v in ladder if v > MAX_BYTE14]`, so 0x00-0x31 values never captured, and negative ints, pass through.
```

**Failure scenario:** `--ladder 0x20,0x20,0x21,0x21` runs about 5 minutes of passes, then raises IndexError at line 227. No JSON is written, and the library entries do not record byte14 (PA-03), so the run cannot be analysed.

**Fix:** Use `ladder[0]` and guard the drift print. Restrict --ladder to the set of captured values. Write the JSON before the summary.

<details><summary>Second reader's check</summary>

byte14_probe.py uses the module constant LADDER[0], not `ladder`, at lines 156, 201 and 227. At 227, `[...][-1]` raises IndexError when the custom ladder has no 0x10, and that runs before the JSON write at 230-233. The guard only rejects values above 0x31 (102). int(v, 0) accepts negative values, so -1 passes the guard.

</details>

<a id="probe-and-analysis-tools-pa-17"></a>

### PA-17 -- gain_probe places blue from a metering level that can be stale; exposure_probe's docstring claims the opposite staleness

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gain_probe.py:141-151`, `tools/exposure_probe.py:62-67`, `rps7200/direct.py:2145-2200`

The two tools make opposite assumptions about the same field. gain_probe is wrong when the last round clipped: blue is then sited from a pre-retreat level of about 1.0 and lands near 0.09 rather than 0.35. The underlying cause is that last_metering does not record the level the returned scales produce, or whether the loop exited via break.

**Evidence (from the code):**

```text
gain_probe: `landed = scanner.last_metering["rounds"][-1]["levels"][2]; ... scales[2] *= BLUE_START / landed`, with the comment 'Blue's *achieved* level'. auto_exposure updates `scales` after the final round's measurement when a channel was clipped at round == budget (no break), so the returned scales differ from the last measured level. exposure_probe's docstring says the last round's levels are 'always one step stale', which is only true in that clipped case.
```

**Failure scenario:** On a frame where blue clipped on round 3, the gain ladder starts at about 9% of scale, which is too dark for noise_split to be meaningful, and the run reports the gain field as ineffective.

**Fix:** Add to last_metering an explicit 'final_scales_measured: bool' or 'exit_reason'. Have gain_probe take one measuring pass at the returned scales before siting the ladder. Correct exposure_probe's docstring.

<details><summary>Second reader's check</summary>

In auto_exposure (direct.py:2145-2200), both `break` paths leave `scales` equal to the scales that were just measured, so the last round's levels are current. Only when a channel clips on the final budget round are the scales retreated after measurement. That makes exposure_probe's docstring claim of 'always one step stale' (62-67) wrong. gain_probe (141-151) takes rounds[-1] levels[2] as 'achieved', which is wrong in exactly that clipped case, and last_metering records no exit reason that would tell the two cases apart.

</details>

<a id="probe-and-analysis-tools-pa-18"></a>

### PA-18 -- Probes carry stale transport figures and a stale confidence floor, printed at runtime, and use millimetres despite CLAUDE.md

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/hold_probe.py:34-36`, `tools/hold_probe.py:60-63`, `tools/hold_probe.py:198-200`, `tools/transport_truth.py:64-66`, `tools/roll_registration_walk.py:32-33`, `tools/roll_registration_walk.py:61`, `rps7200/framing.py:883`, `rps7200/framing.py:1151`

**Doc claim:** CLAUDE.md 'Do not use millimetres for transport distances: Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter'; tools/hold_probe.py:35 'A floor of 40'; tools/roll_registration_walk.py:32-33,61

The runtime verdict line in hold_probe tells the operator the floor is 40 when the code refuses below 55. Confidences of 40-55 will be refused while the printout suggests they pass. Transport figures still come from the retired 1.572-unit ramp. Every probe interface is in mm, against the project's own rule, which is the same class of conversion that CLAUDE.md says hid two mistakes.

**Evidence (from the code):**

```text
hold_probe prints `f"the floor is 40"` (200), and its docstring says 'A floor of 40 sits in that gap' (35). The code has `CONFIDENCE_FLOOR = 55.0` (framing.py:883). hold_probe says 'smallest move the hardware can make (0.272 mm)', transport_truth '0.2719 mm minimum', and roll_registration_walk 'off -0.816 mm', '+0.272 mm x 6', 'Three is 0.816 mm'. The code has `HOLD_TOLERANCE_MM = 0.3002` and FINE_MIN_MM = 0.3002. CLAUDE.md, 'Do not use millimetres for transport distances', calls them 'prohibited', yet --offset, --step, RUNG_MM, MAX_EXCURSION_MM and TOTAL_TRAVEL_LIMIT_MM are all in mm, as is every printed distance.
```

**Failure scenario:** hold_probe reports confidence 48 and 'the floor is 40'. The operator reads that as a pass, but the loop returned 'unverified' because 48 < 55.

**Fix:** Import CONFIDENCE_FLOOR and HOLD_TOLERANCE_MM and print them. Take CLI distances in param units (protocol.units and say_units). Update the docstrings to 0.3002 mm (2.84 units).

<details><summary>Second reader's check</summary>

hold_probe.py:198-200 prints 'the floor is 40', and its docstring (34-36) says 'A floor of 40'. framing.py:883 has CONFIDENCE_FLOOR = 55.0. The docstring figures 0.272 mm (hold_probe:61, walk:32-33, 61) and 0.2719 (transport_truth:64-65) do not match FINE_MIN_MM = STEP_MM + OVERHEAD_MM = 0.3002 (session.py:93). Every CLI distance and log field in these probes is in mm, despite CLAUDE.md's 'Millimetres are prohibited' rule for transport distances.

</details>

<a id="probe-and-analysis-tools-pa-19"></a>

### PA-19 -- hold_probe's 'total travel' stop only checks the requested offset; --restore sends a single reversing command

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/hold_probe.py:65-68`, `tools/hold_probe.py:98-101`, `tools/hold_probe.py:204-212`, `rps7200/framing.py:1210-1212`

The documented safety stop does not exist at runtime. Travel can reach |offset| + 2 mm per hold, twice, plus the restore. The restore reverses direction, where backlash swallows commands, and it can overshoot for a small net.

**Evidence (from the code):**

```text
The comment says 'Refuse to run if the film has travelled further than this in total ... a surprise cannot walk the film across the aperture while nobody is counting'. The code is only `if abs(args.offset) > TOTAL_TRAVEL_LIMIT_MM:` before opening the device. The zero-hold, the offset hold (budget `abs(target_mm) + HOLD_HEADROOM_MM`) and --restore are never summed. `if args.restore and net: scanner.nudge(-net)` sends one command for any non-zero net, and param_for_mm clamps to at least param 1 (0.30 mm) even for a tiny net.
```

**Failure scenario:** The zero-hold moves 1.5 mm chasing a mis-registration, and the offset hold spends another 2.5 mm, all without the 3 mm 'total' limit firing.

**Fix:** Accumulate spent_mm from both holds and the restore, and stop when the sum passes the limit. Restore through _hold_to_approved with target 0, or iterate on measurement.

<details><summary>Second reader's check</summary>

The only use of TOTAL_TRAVEL_LIMIT_MM is `if abs(args.offset) > TOTAL_TRAVEL_LIMIT_MM` before the device is opened (hold_probe.py:98-101). The spent_mm of the two holds is never summed. The restore at 204-206 is one nudge(-net) against the previous direction, and param_for_mm clamps it to at least param 1 (0.30 mm).

</details>

<a id="probe-and-analysis-tools-pa-20"></a>

### PA-20 -- linearity --probe joins on an exposure triple across the whole library and picks the oldest match

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/linearity.py:133-153`, `tools/linearity.py:179-191`

The join key (device exposure R,G,B) is not unique across sessions. Re-running exposure_probe on the same strip, or any other 300 dpi scan at the same metered triple, collides, and the tool silently uses the oldest entry, most likely from a previous run. The note is printed but the analysis goes ahead. The join also depends on PA-03's missing identifiers.

**Evidence (from the code):**

```text
`for entry in sorted(directory.iterdir()): ... out.setdefault(tuple(int(v) for v in exposure[:3]), []).append(entry)`, followed by `used {candidates[0].name}`. The oldest entry wins, with no restriction to the probe run's time span or to debug entries.
```

**Failure scenario:** The second exposure_probe run on the same film meters the same triples for some rungs. linearity chains frame 5's x0.66 pass from run 1 with x0.80 from run 2, which divides two passes taken hours apart and measures lamp drift as non-linearity.

**Fix:** Restrict candidates to entries created in the probe run's window (record start and end times in the JSON), or better, join on a probe/run id recorded in the entry (PA-03). On an ambiguous match, prefer the newest entry or refuse.

<details><summary>Second reader's check</summary>

entries_by_exposure keys the whole library on device_settings.exposure[:3] (linearity.py:133-153). On a collision by_frame uses candidates[0], the oldest by sorted name (179-191), even though its own comment says 'so say so rather than pick'. The join is not scoped to the probe run or to debug entries, and a metering-probe entry or an earlier run at the same triple collides with it.

</details>

<a id="probe-and-analysis-tools-pa-21"></a>

### PA-21 -- roll_registration_study hard-codes a 428-px 300 dpi geometry and 8-bit absolute thresholds, and shadows framing's BASE_TOLERANCE

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/roll_registration_study.py:735`, `tools/roll_registration_study.py:168`, `tools/roll_registration_study.py:535`, `tools/roll_registration_study.py:555`, `tools/roll_registration_study.py:815-821`, `rps7200/framing.py:407`

A 600 dpi prescan roll makes the 'does a nudge survive the advance' predictions wrong by a factor of 2. Pointing --root at the library mixes 16-bit 300 dpi scans (metering probes) with 8-bit prescans, and the absolute-count flatness and level thresholds are meaningless on the 16-bit ones. A same-named constant with a different value from framing invites a wrong import.

**Evidence (from the code):**

```text
`scale = APERTURE_MM / 428.0` in report_held, whatever resolution the roll prescanned at. `BASE_FLAT = 2.0` counts, and `spread < 3.0`. `BASE_TOLERANCE = 0.12` is defined locally while framing.py:407 has `BASE_TOLERANCE = 0.15`. The --root help says 'a roll folder holding prescanNN.tif, or a library', and cohort() filters a library only by dpi, not by depth.
```

**Failure scenario:** `--held rolls/<600dpi roll>` reports 'The correction does NOT survive the advance' because the predicted pixel shifts are half the real ones.

**Fix:** Derive the scale from the manifest's prescan resolution or width via framing.units_per_column. Filter a library cohort to depth 8. Rename the local constant.

<details><summary>Second reader's check</summary>

report_held has `scale = APERTURE_MM / 428.0` (roll_registration_study.py:735) whatever the resolution, even though main() computes mm_per_px(width) from the images (874). The file defines BASE_TOLERANCE = 0.12 (535) while framing.py:407 has 0.15, and uses the absolute thresholds BASE_FLAT = 2.0 and spread < 3.0 (168, 555). cohort() is imported from registration_margin and filters library entries by dpi only.

</details>

<a id="probe-and-analysis-tools-pa-22"></a>

### PA-22 -- fast_ir_probe accepts bw/kodachrome and refuses only after metering at 1800 dpi; its docstring says it meters in RGBI

**Severity** low · **Category** user-error · **Verdict** confirmed · **Problem** [P16](../problems/P16-timeouts-and-runtime-budget.md)

**Where:** `tools/fast_ir_probe.py:148-152`, `tools/fast_ir_probe.py:199-200`, `tools/fast_ir_probe.py:29`, `rps7200/direct.py:2492-2510`, `rps7200/direct.py:1996-1999`

Choosing a blind film costs up to 3 rounds of 1800 dpi RGB metering, roughly 4 minutes and filed entries, before the refusal. Metering at the scan resolution rather than 300 dpi is itself a large cost the dry-run budget does not show.

**Evidence (from the code):**

```text
`ap.add_argument("--film", default="negative", choices=sorted(FILM_TYPES), help="... Infrared is refused outright for the stocks blind to it.")`. `scales = list(scanner.auto_exposure(resolution=args.resolution, ...))` meters at 1800 dpi before scan() raises `ValueError("infrared is blind to ...")`. The docstring says 'meters once, RGBI', but auto_exposure 'Always probes in RGB, never in infrared'.
```

**Failure scenario:** `--film bw` runs 4 minutes of metering, then raises ValueError with a traceback.

**Fix:** Check supports_infrared(args.film) before opening the device. Meter at 300 dpi, as auto_exposure's docstring says the vendor does. Fix the docstring.

<details><summary>Second reader's check</summary>

--film accepts every FILM_TYPES value (fast_ir_probe.py:148-152). auto_exposure(infrared=True) does not refuse blind films; it only chooses blue_rgbi_headroom(film). It meters in RGB at args.resolution, 1800 by default, and filed passes result. scan() then raises ValueError('infrared is blind to ...') (direct.py:2492-2510). The ValueError is not in the caught tuple, so it propagates as a traceback. The docstring says 'meters once, RGBI', but auto_exposure always probes in RGB.

</details>

<a id="probe-and-analysis-tools-pa-25"></a>

### PA-25 -- Contradictory claims about whether the vendor ever sends SLIDE_PREV

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/transport_truth.py:29-30`, `rps7200/direct.py:1223-1226`, `docs/protocol.md:274-275`, `docs/whole-roll-plan.md:358-362`, `docs/protocol.md:465`

**Doc claim:** tools/transport_truth.py:29-30; rps7200/direct.py:1223-1226; docs/protocol.md:274-275 vs docs/protocol.md:465 and docs/whole-roll-plan.md:358-362

The code and docs disagree on the evidence behind the retreat() command that exposure_probe's rewind and roll_registration_walk's --rewind depend on. The captures are gitignored and absent here, so the code cannot settle it.

**Evidence (from the code):**

```text
transport_truth: 'SLIDE_PREV -- which ... the vendor sends ZERO times in 3,987 commands across six captures'. direct.retreat: 'This is what the vendor sends to rewind a finished roll'. docs/protocol.md:274-275: 'neither appears in any capture'. docs/whole-roll-plan.md:358: '05 01 00 01 x16 SLIDE_PREV, stepping 16 -> 0'. docs/protocol.md:465: 'At the end of the roll, SLIDE 05 01 00 01 once per frame'.
```

**Failure scenario:** A reader trusting transport_truth avoids retreat() as unverified by the vendor. A reader trusting direct.py assumes vendor precedent that protocol.md says does not exist.

**Fix:** Re-run tools/parse_capture.py across the six captures and state one count in one place. Have the other documents reference it.

<details><summary>Second reader's check</summary>

The sources contradict each other. transport_truth.py:29-30 says the vendor sends SLIDE_PREV zero times. direct.py:1223-1226 says 'This is what the vendor sends to rewind a finished roll'. docs/protocol.md:273-276 says neither SLIDE_PREV nor eject 'appears in any capture'. docs/protocol.md:465-466 and docs/whole-roll-plan.md:358-362 describe 05 01 00 01 x16 in a capture. The captures are gitignored and absent here, so the code cannot settle the question.

</details>

<a id="probe-and-analysis-tools-pa-24"></a>

### PA-24 -- Device probes bypass ScanSession, tools and demo, and 10 of 13 area tools have no tests

**Severity** info · **Category** test-gap · **Verdict** confirmed

**Where:** `tools/hold_probe.py:121`, `tools/hold_probe.py:142-145`, `tools/transport_probe.py:76`, `tools/byte14_probe.py:121`, `tools/gain_probe.py:122`, `tools/gain_probe.py:172`, `tools/fast_ir_probe.py:186`, `tools/exposure_probe.py:224`, `tools/transport_truth.py:112`, `tools/roll_registration_walk.py:12-16`, `tools/roll_registration_walk.py:249`

Under CLAUDE.md's 'Drive the scanner through this software', every finding these probes produce is provisional until it is seen through tools/ or the window. None can run under --demo, since they never pass through session._open_scanner, so they are untested with no device on the bus. Several of the defects above would have been caught by a unit test: the 8-bit wrap, the LADDER[0] IndexError, the stale raw, the restore arithmetic.

**Evidence (from the code):**

```text
Each probe builds `DirectScanner(verbose=True)` directly. hold_probe calls the private `scanner._hold_to_approved(...)`, and gain_probe monkeypatches `scanner.get_gain_offset = patched`. roll_registration_walk says 'It does not touch `scan_roll`'. Only test_exposure_probe.py, test_linearity.py and test_registration_walk.py load a tool from this area. hold_probe, transport_probe, byte14_probe, gain_probe, fast_ir_probe, transport_truth, exposure_headroom, dpi_analysis, registration_margin and roll_registration_study have none.
```

**Failure scenario:** A regression in byte14_probe's summary or in exposure_headroom's dtype handling ships unnoticed and is found only after scanner time has been spent.

**Fix:** Add offline tests that use conftest fakes, as test_registration_walk does, for each probe's control flow and each analysis tool's numerics on synthetic entries at both depths. Record in each probe's JSON that it was a bypass path.

<details><summary>Second reader's check</summary>

All device probes construct DirectScanner(verbose=...) directly. hold_probe calls the private _hold_to_approved, and gain_probe monkeypatches get_gain_offset. Only tests/test_exposure_probe.py, test_linearity.py and test_registration_walk.py exercise tools from this area. test_roll.py only mentions hold_probe and registration_margin in docstrings. No probe can run through session._open_scanner or the demo.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry per probe pass (debug filing): decoded pixels | library/<flush-time YYYYmmddTHHMMSSZ>_unknown-film_<dpi>dpi[_ir][-N]/scan.tif (or $RPS7200_DEBUG_ROOT/...) | TIFF, uint8 (300 dpi prescans, depth 8) or uint16 (<u2) H x W x C, channel order R,G,B[,I] | raw (decode only, no shading) but not byte-identical to the bytes: rows turned upright by decode_index, and 7200 dpi realigned by _realign_native_column_stagger (direct.py:2695-2708) | DirectScanner._debug_flush -> library.save (direct.py:719-729, library.py:179) for every scan() pass in hold_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth, roll_registration_walk, auto_exposure metering probes, and transport_probe when RPS7200_DEBUG is set | library.load/corrected (registration_margin.cohort, roll_registration_study library root, dpi_analysis.plane, linearity --corrected), library.reconstruct/verify | Lossless for the decoded array; sha256 in scan.json. Derived from raw, so not ground truth. |
| Raw bytes as received from READ (INDEX format with 2-byte channel tags) | library/<id>/raw.bin.gz | gzip level 6 of the concatenated READ payloads; sha256 and byte count in scan.json raw.*; layout {format, bytes_per_line, line_stride, index_header, width, lines, channels, byte_order, lines_received} | raw (ground truth) | library.save from the spooled NNN-raw.bin (direct.py:679-683), only when this pass or an EARLIER pass set keep_raw=True. It can hold the previous pass's bytes (PA-02: byte14_probe final pass). Missing for transport_probe prescans. | library.read_raw/decode_raw (linearity default raw mode), library.reconstruct, exposure_headroom.decode via DirectScanner._deinterleave | Byte-exact when present and belonging to this pass; stale-pass bytes are possible (PA-02). |
| Session shading reference | library/<id>/shading.npz | np.savez_compressed: ref<c>, mean<c>, dark<c>, darkmean<c> float64, pixels_per_line, channels, dark_channels | derived: calculate_shading's level-split parse of the calibration lines; the calibration bytes themselves are not stored (PA-23) | library.save(reference=self._shading), filed for every pass, including shading=False probe passes | library.corrected, exposure_headroom.calibration/dark_floor, reconstruct (legacy corrected entries) | Lossless float64 of a derived quantity; not re-derivable because the source bytes are discarded. |
| Per-pass CCD mask | library/<id>/ccd_mask.bin | raw bytes (0x00 used / 0x70 unused), length = reference width (normally 5172) | raw | library.save(ccd_mask=self._ccd_mask) read in scan() via get_ccd_mask (direct.py:2663-2672) | library.corrected -> apply_shading; exposure_headroom.calibration (loaded, but dark_floor ignores it: PA-07) | exact |
| Entry record | library/<id>/scan.json and library/index.json | JSON: image{shape,dtype,channels,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{resolution_dpi, frame, width, height, depth, channels, channel_order, bytes_per_line, film, exposure_scale (requested, pre-clamp), exposure_metered, duration_s, protocol_revision, read_direction, carriage_state, fast_infrared, filter_offsets}, device_settings{exposure (post-clamp), gain, offset}, metering (only when scan(auto_exposure=True), never for probe passes), registration (never for probes), calibration{shading, ccd_mask, pixels_per_line, light_mean, report, skipped}, film notes 'captured with RPS7200_DEBUG on', tags ['debug'], provenance | metadata | library.save, last (it acts as the commit marker); index.json is rebuilt by reindex after each save | every analysis tool; library.signature/duplicates/prunable | Missing: byte14, slide_init_param, capture time (item['captured'] is dropped), inquiry (ignored by save), probe/step/rung/frame identity (PA-03) |
| Debug spool (transient) | $TMPDIR/rps7200-debug-XXXXXX/NNN-image.npy, NNN-raw.bin | npy of the raw pixel array; raw READ bytes | raw | DirectScanner._debug_capture during each scan() (device open) | DirectScanner._debug_flush at close(); deleted per item after filing | Exact, but meta, reference, mask and layout live only in RAM, so a killed process leaves unfileable orphans (PA-04) |
| hold_probe result | --json <path> | JSON {offset_mm, resolution, state_before, zero{hold record minus prescan}, held{...history}, net_mm, net_detail, verdict, confidence_range, after_restore_mm, duration_s, error} | measurements (mm) | tools/hold_probe.py:238-241 in finally | humans/docs | rounded (4 dp mm) |
| transport_probe result | --json <path> | JSON list of {payload, question, error, position_before/after, position_changed, settle_s, x0_before/after, shift, shift_mm, contrast_after} | measurements | tools/transport_probe.py:141-144, only on success | docs | shift from film_bounds, meaningless on a loaded strip (PA-12) |
| byte14/gain/fast-IR probe results | --json <path> | byte14: {passes[{index, byte14, resolution, height, duration_s, ms_per_line, exposure}], summary}; gain: [{median, p99_5, at_rail_pct, rung, gain, seconds, exposure, gain_written}]; fast_ir: {passes, summary{drift_lines, ms_per_line_*, agreement_*, specks}} or sweep {passes, table} | statistics from raw (shading=False) centre crops | byte14_probe.py:230-233 (after the summary, lost on IndexError: PA-16); gain_probe.py:227-230; fast_ir_probe.py:392-396 / 513-517 | docs/analog-gain-plan.md, docs/byte14-plan.md, docs/fast-infrared-plan.md | This is the only place byte14 (and the probe's step) is recorded; the join to library entries is by time only |
| exposure_probe walk | --json <path> (optional) | JSON {schedule, target, ladder, passes[{frame, rung, transport_position, resolution, duration_s, exposure, clamped, metering, probed_levels, levels, wall_s}]} | levels from corrected delivered passes | tools/exposure_probe.py:299-305 after every pass (overwrites the whole file; chunks overwrite each other: PA-10) | tools/linearity.py --probe (joins on device_settings.exposure[:3]) | Join key not unique (PA-20) |
| transport_truth steps | --json <path> | JSON {steps[{label, asked_mm, measured_mm, confidence, dy, position, verdict}], resolution, net_from_start_mm} | measurements (mm) | tools/transport_truth.py:182-184 in finally | docs | rounded |
| Registration walk corpus | rolls/registration-<label>/<label>NN_p1.tif, <label>NN_p2.tif, <label>NN_LSS.tif, walk-<label>.json | TIFF uint8 300 dpi prescans with a resolution tag; JSON log {label, resolution, frames[{number, passes, contrast, position}], ladders[{number, rungs[{step, commanded_mm, pass, contrast, sent}], restore, commanded_travel_mm, max_excursion_mm}], state_before, rewind, stopped_early, ok, seconds, travel_mm} | CORRECTED (prescan() output), with that day's correction code | tools/roll_registration_walk.py:102-105, 336-338 | tools/roll_registration_study.py --walk / --glob | Not linked to the raw library entries; sent.asked_mm is overwritten with the requested value (PA-13) |
| Roll manifests read by the study | rolls/<date>/roll.json or survey.json; rolls/*/prescanNN.tif | JSON frames[].registration.approved{target_mm, outcome, moves, spent_mm, final_mm, residual_mm, history[{px, confidence,...}], clamped}; TIFF prescans rotated for the contact sheet | corrected and oriented | scan_roll / ScanSession (outside this area) | roll_registration_study.report_held, registration_margin.cohort | n/a |
| Offline analysis outputs | exposure_headroom --json; dpi_analysis probe/dpi/results.json (always written, --out); roll_registration_study --json | JSON summaries | derived | the respective tools | docs | derived; registration_margin prints to stdout only |

**Second reader's corrections to this table:**

- **hold_probe JSON:** values are not rounded to 4 dp mm. `out` holds the raw floats and dicts from _hold_to_approved and measure_shift_mm, written with `default=str` in `finally`, so it is also written on failure.
- **Registration walk log:** `sent.asked_mm` is overwritten with the requested value, but `sent.param` and `sent.forward` survive, so the commanded distance can be re-derived from param. `sent.requested_mm` also equals the requested value. The walk JSON is written in `finally`, including after a failure or interrupt; on an interrupt it carries `ok=False, interrupted=True`.
- **raw.bin.gz:** besides the byte14_probe final-pass case, any scan(keep_raw=False) after an auto_exposure() in the same session inherits the last metering probe's bytes, because metering probes use keep_raw=True (direct.py:2091) and last_raw is never cleared. For 7200 dpi entries, raw.bin.gz decodes to something that does not match scan.tif: scan.tif is realigned and the raw decode paths do not realign (PA-A2).
- **Entry record (scan.json):** `inquiry` is accepted by library.save but never written. `metering` is present only on scan(auto_exposure=True) passes, so the metering probes themselves and every probe pass taken with auto_exposure=False carry none. Probe passes do record `exposure_metered=false` and the commanded `exposure_scale`, which is what signature() keys on.
- **shading.npz:** it is filed with every probe pass, including shading=False passes, and it is the session reference derived at calibration time. The calibration source bytes are discarded (keep_data=False, direct.py:2592).
- **transport_probe JSON:** written only when the `with` block exits cleanly. Its prescans are filed only if RPS7200_DEBUG is set to one of 1/true/yes/on, and then without raw bytes.
- **exposure_probe JSON:** written after every pass, overwriting the whole file. Its `levels` come from a region re-detected on the corrected delivered pass, not the metering region (PA-11).
- **Missing debug-spool row:** a killed process leaves NNN-image.npy and NNN-raw.bin in $TMPDIR/rps7200-debug-*, and nothing reads or cleans them.
- **Missing row, probe side effect:** session_start sends SLIDE 00 01 00 00 at the start of every probe. It moves the film and is recorded nowhere (PA-A1).

## What the operator can do

- Run any probe with --dry-run to see the plan and a time estimate without opening the device (hold_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth, roll_registration_walk; transport_probe has no dry run).
- Run the offline tools (exposure_headroom, linearity, dpi_analysis, registration_margin, roll_registration_study) at any time with no scanner; they only read library/ and rolls/.
- Chunk exposure_probe with --only A-B, but only together with --no-rewind and a distinct --json per chunk.
- Restrict exposure_headroom to 16-bit scans with --dpi (for example --dpi 3600) to stay away from 8-bit prescans.
- Use `tools/library.py duplicates` without --delete to see what would be pruned, and --keep 2 to keep repeat pairs.
- Redirect debug filing to a scratch library with RPS7200_DEBUG_ROOT.

## What the operator should not do

- Do not run `tools/library.py duplicates --delete` on a library that holds probe or walk entries: byte14 and gain ladders, repeat pairs and all walk prescans share one signature and will be removed (PA-01).
- Do not press Ctrl-C during a byte14_probe pass: its finally block starts another full scan on a device with an abandoned read (PA-05).
- Do not run fast_ir_probe (about 25 min), exposure_probe (about 40 min), byte14_probe or a 16-frame roll_registration_walk in the foreground: a kill at 10 minutes files nothing to the library and may wedge the device (PA-04).
- Do not run tools/transport_probe.py: it is ungated, sends SLIDE payloads with value bytes of unknown meaning, reports 0 mm for real moves, files no raw bytes and leaves the strip advanced (PA-12).
- Do not set RPS7200_DEBUG to anything but 1/true/yes/on; other non-empty values pass the probes' gate while filing stays off (PA-15).
- Do not treat registration_margin's 'CONFIDENCE_FLOOR holds' as validating the production gate, which also scores a row-flipped reading (PA-08).
- Do not read roll_registration_study --ensemble output as what the window or scan_roll would decide; both use the frame_edges reader (PA-09).
- Do not trust hold_probe's printed 'the floor is 40'; the code uses 55 (PA-18).
- Do not calibrate or run any probe with an empty transport (CLAUDE.md); the probes do not check it, beyond warnings based on READ_STATE.

## Mistakes nothing guards against

- Running exposure_probe without --json: 40 minutes of passes filed with no frame or rung identity, so linearity --probe cannot use them.
- Running exposure_probe --only 5-8 after --only 1-4 without --no-rewind: the same physical frames are re-measured and labelled 5-8, and reusing one --json path erases the first chunk.
- Passing a byte14_probe --ladder without 0x10: IndexError after the scans, before the JSON is written; values up to 0x31 never seen in a capture are accepted.
- Passing --film bw or kodachrome to fast_ir_probe: several minutes of 1800 dpi metering are spent before the ValueError refusal.
- Running exposure_headroom with no --dpi after a roll: it studies the newest entries, usually 8-bit prescans, and prints garbage clip percentages.
- Running roll_registration_study --held on a 600 dpi-prescanned roll: the scale is hard-coded for 428 px at 300 dpi, so predictions are off by 2x.
- Pointing roll_registration_study --root at the library: 16-bit scans mix with 8-bit prescans under absolute-count thresholds.
- hold_probe --restore, transport_truth's automatic put-back and roll_registration_walk's ladder restore all send the commanded sum in reverse direction, so the film is usually not back where it started, and nothing reports the residual except hold_probe's final measure.
- An exception mid-ladder in roll_registration_walk (for example an excursion refusal) aborts with the film displaced by up to 1.2 mm and no restore.

## Dataflow notes

DEVICE PROBES: data in
- Each probe builds `DirectScanner(verbose=True)`, calls `open()`, then usually `read_state()`, `session_start()` and `wait_warm()`. They never go through ScanSession or session._open_scanner, so DemoScanner cannot stand in.
- Pixels arrive through `DirectScanner.scan()` (direct.py:2423).
  - The scan sets up with `set_scan_frame`, `cmd_17`, `get_gain_offset().scaled()`, `set_gain_offset`, `set_mode(byte14=...)` (direct.py:1112) and `SLIDE_INIT`, then `start_scan`.
  - The mask comes from `get_ccd_mask` (2667) and the pixels from `read_planes` (1440).
  - `read_planes` concatenates the READ chunks into `blob`. Only when `keep_raw` is set does it keep them, as `last_raw` and `last_raw_layout` (1518-1532).
  - `decode_index` (1552) then splits the planes by tag and turns bottom-up passes upright using line tags (`direction.read_direction`).
  - At 7200 dpi, `_realign_native_column_stagger` follows (2695).
  - That result is `raw_pixels` (2708). `apply_shading` is applied only when `shading=True`.
  - The meta dict is built at 2760-2804. It has no byte14 and no slide_init_param.
- Prescans go through `prescan()` (1682): `scan(depth=8, shading=True)`, which calibrates via `calibrate_shading()` (1755) when there is no reference.
  - Calibration keeps only the `ShadingReference` parsed by `calculate_shading`. The calibration bytes are discarded (1946-1950).
- Metering (`auto_exposure`, 1977) runs 2-3 RGB 16-bit passes with `keep_raw=True`. It leaves `last_metering`, but the probes call it separately, so `meta['metering']` is never attached to their passes.
- Transport moves: `nudge(mm)` (3138) converts through `param_for_mm` (3126: round((|mm| - 0.1945) / 0.1057), clamped to 1..87) and sends SLIDE 00/01 <param> 00 04. `advance()` and `retreat()` send SLIDE 04/05 01 00 01 and poll READ_STATE byte 2.
- Holds: `_hold_to_approved` (2836) loops `hold_plan` -> `nudge` -> `prescan` -> `measure_shift_mm` (framing.py:1007). `measure_shift_mm` takes the better of an upright and a row-flipped register, checks the z-score against CONFIDENCE_FLOOR 55 at a SEARCH_MM reach, and gates dy in mm.

DEVICE PROBES: filing
- `scan()` calls `_debug_capture(raw_pixels, meta)` (2811). When debug is on, that spools image.npy, plus raw.bin from `capture_record()['raw']` (possibly stale), to a temp directory. Meta, reference, mask and layout stay in RAM.
- `close()` (777-788) closes the transport, then `_debug_flush` (694) calls `library.save` (library.py:131) for each item. `save` writes scan.tif, raw.bin.gz (gzip of spooled bytes plus sha256), shading.npz, ccd_mask.bin and scan.json, and runs reindex.
  - Tags are ['debug'] and the notes a fixed string.
  - `created` and the entry id are flush time. `captured` is dropped, and `inquiry` is ignored.

DEVICE PROBES: data out
- Each probe prints results and optionally writes a side JSON (see persisted_state). That JSON is the only place byte14, rung, frame and probe step are recorded, and it joins to library entries by time or by exposure triple only.
- roll_registration_walk also writes corrected 8-bit TIFFs through `tiff.write`, while the device is open, to rolls/registration-<label>/ plus walk-<label>.json.

OFFLINE TOOLS
- exposure_headroom:
  - `decode()` does `read_raw` + `_deinterleave`, which turns rows upright but does not realign 7200 dpi.
  - `calibration()` loads shading.npz and ccd_mask.bin.
  - `achieved` is p99.5 of `apply_shading(image, ref, mask)` / 65535.
  - `wanted()` uses the negative-film blue constants, then `scale_exposure()` simulates (raw - dark[:W]) * k + dark, ignoring the mask and depth, and casts back to the image dtype.
  - Finally `apply_shading`'s clipped_per_channel is reported.
- linearity:
  - Raw mode uses `library.decode_raw` (`_deinterleave`, no correction, no stagger realign). Corrected mode uses `library.corrected`, a recomputation with today's code.
  - The middle-crop 30% is compared band by band against adjacent rungs.
  - `--probe` groups the probe JSON by frame and joins on `device_settings.exposure[:3]` across the whole library, taking the oldest candidate.
- dpi_analysis:
  - Selects entries by `created` (flush time) between --after and --before.
  - Reads everything through `library.corrected`; 7200 dpi entries come back raw and un-realigned.
  - Computes row power spectra, noise_split and agreement_z from the skill's metrics.py, and writes probe/dpi/results.json.
- registration_margin:
  - `cohort()` reads rolls/*/prescan*.tif (corrected and rotated), or library 300 dpi entries through `library.corrected`.
  - Then `register` (upright only, reach = round(9 mm / (25.4 / dpi))), `aligned_correlation` as the arbiter, and a single-reading report against CONFIDENCE_FLOOR.
- roll_registration_study:
  - Reads rolls TIFFs, `cohort()` or a walk JSON's pass list.
  - Runs framing's `registration`, `gap_edges`, `registration_error_mm`, its own `gap_runs`/`edge_band` (absolute 8-bit thresholds) and `register`.
  - `--ensemble` re-implements `StripWalk.judge` without the frame_edges reader.
  - `--held` reads roll.json or survey.json `frames[].registration.approved`, with the scale hard-coded to 428 px.

CROSS-MODULE CONSTANTS
- Taken correctly from source: EXPOSURE_TARGET, OVER_TARGET_TOLERANCE, FINE_MIN_MM (session.py:93) and CONFIDENCE_FLOOR in registration_margin and the study.
- Retyped copies:
  - hold_probe: floor 40.
  - transport_truth: 55 and reach 106.
  - exposure_headroom: 4.98 and FULL_SCALE.
  - dpi_analysis: MM_PER_INCH.
  - roll_registration_study: 428.0, and a BASE_TOLERANCE of 0.12 that shadows framing's 0.15.
  - exposure_probe: UNDER_TOLERANCE 0.08, which it acknowledges.
