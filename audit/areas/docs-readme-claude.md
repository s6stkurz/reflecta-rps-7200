# README.md and CLAUDE.md vs the code

Area key `docs-readme-claude`. 31 findings: 1 critical, 8 high, 8 medium, 13 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Checked roughly 140 claims in README.md (703 lines) and CLAUDE.md (503 lines) against the code: every command, flag, default, path, constant and behavioural statement, plus the Makefile, tasks.py, pyproject.toml and CI. I read the code paths in full: direct.py for scan, debug filing, calibration, metering, rolls and nudge; session.py; library.py; shading.py; tiff.py; demo.py; usbpcap.py; tools/scan.py, scan_roll.py, library.py, make_comparison.py and filing_load_test.py; and the relevant parts of tools/gui.py.


**Claims that hold up:**
- **Build and test:** the make targets and the tasks.py mapping; `make all` = fix+lint+type+test; addopts `-m 'not hardware'`; the nine read-only tests in `test_hardware.py` that skip when there is no scanner; CI running on three OSes; `test_the_two_phases_are_separated` exists; `FRAME_EDGE_PARITY`; `RPS7200_NO_TIFFFILE`.
- **Protocol constants and commands:** `PROTOCOL_REVISION` (value 6); `EXPOSURE_TARGET` 0.80 with a band of 0.08 under / 0.02 over; `BLUE_RGBI_HEADROOM` 5.2 / 11.0 and `blue_rgbi_headroom(film)`; `param_for_mm` is a staticmethod and the demo reuses it; `MAX_CORRECTION_PARAM` 87; `COMMAND_UNITS` 1.84, giving param 1 = 2.84 units and param 87 = 88.8 units; 0.80 units per 300 dpi pixel; `FRAME_WIDTH_UNITS` 350.6; `CALIBRATION_FRAME`; `SET_SCAN_HEAD`, STOP SCAN and IEEE1284 RESET are never sent on any normal path.
- **Direction and carriage:** `decode_index` turns bottom-up passes upright; `read_direction` and `carriage_state` are recorded; `_note_reversal` spares a pass whose own lines decided its direction.
- **Refusals and film types:** 7200 dpi with shading raises `ShadingUnavailable`; infrared is refused on bw and kodachrome.
- **Demo, library tool and GUI:** the demo is injected at `session._open_scanner` and there is no `if demo` above that seam; the `tools/library.py` subcommands (`migrate-raw` is a dry run unless `--write`); settings env and flags; the shortcut table; typing ABORT for force-abort; the udev rule; the USB ids.

**What does not hold up (30 findings):**
- **Critical:** a single scan from the GUI files shading-corrected pixels labelled as raw. Save As and the 1:1 view then correct them a second time.
- **High:** debug filing can attach no raw bytes, or another pass's stale raw bytes.
- **High:** `RPS7200_DEBUG` is forced off in both tools and the GUI.
- **High:** the GUI gzips entries with the device open and idle.
- **High:** `--no-shading` still triggers a lazy calibration, on the path the code itself says stalled the device.
- **High:** `shading.npz` is a host-derived reduction, not the calibration bytes.
- **High:** `scan_roll.py --dry-run` and roll-frame prescans lose their raw bytes.
- **High:** `make_comparison.py` judges a destripe pipeline the driver never ships.
- **Medium and low:** several stale or contradictory numbers (the mm law, 2.6 vs 2.84 units, frame width vs aperture, the resolution-table widths, `INFRARED_FLOOR_S` "guards a timeout"), unit and privacy claims, and naming or location slips.

The captures, the library and the rolls are not in this checkout, so the capture command counts and the timing medians cannot be checked.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [D01](#docs-readme-claude-d01) | critical | data-integrity | GUI single Scan files shading-CORRECTED pixels as raw; Save As / 1:1 view then correct them a second time | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [D02](#docs-readme-claude-d02) | high | data-integrity | Debug filing attaches NO raw bytes, or the PREVIOUS pass's raw bytes, whenever scan() runs with the default keep_raw=False | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [D03](#docs-readme-claude-d03) | high | doc-mismatch | RPS7200_DEBUG=1 is silently overridden in tools/scan.py, tools/scan_roll.py and the GUI, so intermediate passes are never filed | [P08](../problems/P08-passes-never-filed.md) |
| [D04](#docs-readme-claude-d04) | high | hardware-safety | The GUI gzips library entries with the scanner open and idle after every single scan or prescan | [P13](../problems/P13-gzip-with-device-open.md) |
| [D05](#docs-readme-claude-d05) | high | hardware-safety | --no-shading does not skip calibration: prescan(), metering probes and roll frames calibrate lazily, on the path the code itself says stalled the device | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [D06](#docs-readme-claude-d06) | high | data-integrity | shading.npz is a host-derived reduction; the calibration bytes and calibration-time settings are thrown away | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [D07](#docs-readme-claude-d07) | high | data-integrity | tools/scan_roll.py --dry-run files nothing in the library, and roll-frame prescans lose their raw bytes (CLI and GUI) | [P08](../problems/P08-passes-never-filed.md) |
| [D08](#docs-readme-claude-d08) | high | doc-mismatch | make_comparison.py's '2_corrected.tif' uses a flat-file destripe the driver never ships, not apply_shading or library.corrected | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [DX1](#docs-readme-claude-dx1) | high | demo-divergence | In --demo every filed entry is corrected pixels labelled raw, because DemoScanner publishes no last_pixels_raw and its RollFrames carry no raw_image | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [D09](#docs-readme-claude-d09) | medium | data-integrity | 7200 dpi scan.tif is stagger-realigned (4 rows trimmed), not 'the decode alone'; reconstruct flags it as changed forever | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [D10](#docs-readme-claude-d10) | medium | doc-mismatch | tools/scan_roll.py --start-at overwrites roll.json instead of carrying the earlier run forward | [P19](../problems/P19-roll-manifests.md) |
| [D11](#docs-readme-claude-d11) | medium | doc-mismatch | Transport and geometry numbers in the docs are stale or contradict each other and the code | -- |
| [D13](#docs-readme-claude-d13) | medium | doc-mismatch | INFRARED_FLOOR_S 'guards a timeout', but no timeout in the code uses it | [P16](../problems/P16-timeouts-and-runtime-budget.md) |
| [D14](#docs-readme-claude-d14) | medium | doc-mismatch | usbpcap's public packets() hands back interrupt (keystroke) payloads; the test asserts they are returned | [P32](../problems/P32-usbpcap-returns-keystrokes.md) |
| [D15](#docs-readme-claude-d15) | medium | user-error | The roll Delete dialog and README say only approved.json is lost; manifests and CLI-walk prescans go too | -- |
| [D16](#docs-readme-claude-d16) | medium | demo-divergence | README's 'refuses what the device refuses' and 'decodes raw bytes' claims do not hold for DemoScanner; it also retypes constants | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [D17](#docs-readme-claude-d17) | medium | user-error | Bare Return in the 'Calibrate first' prompt starts a calibration: calibrating does have a key | [P17](../problems/P17-calibration-without-asking.md) |
| [D12](#docs-readme-claude-d12) | low | doc-mismatch | Millimetres are still what is persisted and printed, despite CLAUDE.md's 'Millimetres are prohibited' | -- |
| [D18](#docs-readme-claude-d18) | low | doc-mismatch | README resolution table widths and timing semantics contradict code and tests | -- |
| [D19](#docs-readme-claude-d19) | low | doc-mismatch | README's '--mono G' is not a valid flag; the option is --mono-channel G | -- |
| [D20](#docs-readme-claude-d20) | low | doc-mismatch | TIFFs are deflate-compressed only when tifffile is installed; the built-in writer always writes uncompressed | -- |
| [D21](#docs-readme-claude-d21) | low | doc-mismatch | `make run-sheet` depends on a gitignored local walk and fails on a fresh checkout | -- |
| [D22](#docs-readme-claude-d22) | low | doc-mismatch | CLAUDE.md describes the media flag as READ_STATE 0x40 (0x0d/0x4d); the code uses byte 8 and says byte 6 was wrong | -- |
| [D23](#docs-readme-claude-d23) | low | doc-mismatch | library.verify reports deliberate raw scans as 'correction was asked for', contrary to comments saying it draws that line | -- |
| [D24](#docs-readme-claude-d24) | low | doc-mismatch | Stale '~212 s floor' in user-facing refusal messages and docstrings now that tied IR is the default | -- |
| [D25](#docs-readme-claude-d25) | low | doc-mismatch | Several names, locations and 'nothing else' statements in CLAUDE.md, the Makefile and tasks.py do not match the code | -- |
| [D26](#docs-readme-claude-d26) | low | doc-mismatch | Contradictory statements about whether SET GAIN OFFSET persists | -- |
| [D27](#docs-readme-claude-d27) | low | doc-mismatch | CLAUDE.md says EdgeWatch never reads 'when the sheet opens'; opening a stored roll loads it then | -- |
| [D28](#docs-readme-claude-d28) | low | doc-mismatch | filing_load_test.py does not do the counterbalanced measurement CLAUDE.md credits it with | -- |
| [D29](#docs-readme-claude-d29) | low | doc-mismatch | Rescans overwrite rolls/<roll>/frameNN.tif and prescanNN.tif; only output-folder copies get a suffix | -- |
| [D30](#docs-readme-claude-d30) | info | doc-mismatch | Capture and command counts differ across README, CLAUDE.md, the docs and tools, and cannot be checked here | -- |

## Findings in full

<a id="docs-readme-claude-d01"></a>

### D01 -- GUI single Scan files shading-CORRECTED pixels as raw; Save As / 1:1 view then correct them a second time

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:1085-1100`, `rps7200/session.py:2111-2120`, `rps7200/library.py:373-391`, `tools/gui.py:3864`, `tools/gui.py:4110`, `rps7200/direct.py:2732-2733`, `rps7200/direct.py:2824`

**Doc claim:** CLAUDE.md:114-125 ('The library holds raw pixels ... scan.tif in an entry is the decode alone ... library.save takes raw pixels and corrections= is how a caller admits'); README.md:236-237 ('what reaches library/ is the raw negative'); README.md:396-398

Every Prescan and Roll job passes raw_image=last_pixels_raw, but the GUI's single Scan job (the Scan button and Cmd+Shift+Return) does not. So FrameWriter files the corrected array as scan.tif with corrections_applied=[] and calibration.skipped=None, and the raw bytes and reference sit beside it. CLAUDE.md says the library holds raw pixels and `corrections=` is how a caller admits otherwise; no caller ever passes it. apply_shading is not idempotent: it subtracts the dark row again and multiplies by the per-column gain again.

**Evidence (from the code):**

```text
session._scan: `image, meta = self._scanner.scan(... keep_raw=True)` then `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` with no raw_image=. FrameWriter._write: `raw_image = job.get("raw_image")` / `entry = library.save(job["image"] if raw_image is None else raw_image, ...)`. scan() returns the corrected image: `image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`. library.corrected: `image, report = apply_shading(image, record["reference"], record["ccd_mask"])` whenever corrections_applied lacks 'shading'. `grep -rn "corrections="` finds no caller at all. session._file also deliberately falls back: `filing the corrected pixels instead` without labelling them.
```

**Failure scenario:** The operator calibrates, then presses Scan at 3600 dpi. The entry's scan.tif is already flat-fielded. The GUI's full-resolution view and Save As/Save all call library.corrected(), which subtracts the ~170-count dark floor and re-applies the column gain a second time. The delivered TIFF has crushed shadows and inverse column striping. `make reconstruct` reports 'decode CHANGED: N% differ', the same false alarm CLAUDE.md describes for the 26 prescans. No test catches it: the FakeScanner in tests/test_session.py does not distinguish corrected from raw.

**Fix:** Pass raw_image=getattr(self._scanner,'last_pixels_raw',None) in ScanSession._scan, as _prescan does. Make FrameWriter refuse, or pass corrections=['shading'], when raw_image is None and meta['shading'] is set. Add a session test whose fake scanner returns different corrected and raw arrays. Run `tools/library.py migrate-raw --write` on existing GUI entries.

<details><summary>Second reader's check</summary>

session.py:1440-1458 `_scan` calls `self._file(seq, 0, image, meta, ...)` with no raw_image=, unlike `_prescan`, which passes raw_image=last_pixels_raw at 1415/1434. DirectScanner.scan returns the corrected `image` (direct.py:2732 `image, shading_report = apply_shading(...)`, 2824 `return image, meta`). FrameWriter._write (1089-1091) therefore files job['image'], the corrected array. library.save sets `corrections_applied: list(corrections or [])` = []. The only `corrections=` callers in the repo are tests/test_library.py:344/358. library.corrected (373-391) then re-applies apply_shading. The GUI's 1:1 view (gui.py:4110) and _deliver_one for Save As and Save all (gui.py:3864) both call library.corrected, so the operator gets a double-corrected file. tools/scan.py's hold() comment (scan.py:176-184) describes exactly this bug as already fixed for the CLI, which confirms the mechanism.

</details>

<a id="docs-readme-claude-d02"></a>

### D02 -- Debug filing attaches NO raw bytes, or the PREVIOUS pass's raw bytes, whenever scan() runs with the default keep_raw=False

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:1518-1532`, `rps7200/direct.py:631-646`, `rps7200/direct.py:673-688`, `rps7200/direct.py:2437`, `rps7200/direct.py:2678`, `rps7200/direct.py:2811`, `rps7200/library.py:186-211`, `tools/byte14_probe.py:142-150`, `tools/byte14_probe.py:181-185`

**Doc claim:** CLAUDE.md:104-108 and 136-141 ('DirectScanner files every scan in the library automatically when debug mode is on ... or DirectScanner(debug=True) in code. Either works'); README.md:201-210 and 486-495

CLAUDE.md tells Claude and scripts to rely on DirectScanner debug filing to 'file every scan ... with its raw bytes', and README's Python example calls s.scan(resolution=1800, infrared=True) with keep_raw left False. In debug mode such a pass is filed with either no raw.bin.gz, or the raw bytes and layout left by an earlier keep_raw=True pass: a metering probe, a prescan, or the previous ladder rung. library.save never checks that the bytes describe the pixels.

**Evidence (from the code):**

```text
read_planes: `if keep_raw: ... self.last_raw = blob; self.last_raw_layout = {...}` (no else, never cleared). capture_record: `"raw": self.last_raw, "raw_layout": self.last_raw_layout`. scan(): `keep_raw: bool = False` ... `self._debug_capture(raw_pixels, meta)`. _debug_capture: `raw = record.get("raw"); if raw is not None: raw_path.write_bytes(raw)`. library.save stores raw_path without checking it decodes to `image`. byte14_probe: ladder passes use `keep_raw=True`, and the final pass uses `keep_raw=False` at the same resolution.
```

**Failure scenario:** First case: RPS7200_DEBUG=1 and scan(resolution=1800, infrared=True, auto_exposure=True). The probes are keep_raw=True, so last_raw is the last 300 dpi probe, and the 1800 dpi RGBI entry is filed with the probe's bytes and layout. reconstruct says 'decode CHANGED: now (287,428,3), stored (1722,2584,4)'. Second case: tools/byte14_probe.py's final keep_raw=False pass is filed with the previous rung's bytes at identical shape. reconstruct then reports 'N samples differ', which is indistinguishable from a real decode regression. migrate-raw --write would overwrite scan.tif with the other pass's decode.

**Fix:** Clear last_raw and last_raw_layout at the start of every scan(). In debug mode, force keep_raw=True inside scan(). Make library.save verify, when raw bytes are given, that layout lines/width/channels match image.shape, and refuse or drop them otherwise.

<details><summary>Second reader's check</summary>

read_planes sets last_raw/last_raw_layout only `if keep_raw:` (direct.py:1518-1532) and nothing ever clears them; `grep last_raw =` finds only the __init__ and that assignment. capture_record returns them (631-646), and _debug_capture writes `record.get('raw')` whenever it is non-None (673-678). _debug_flush calls library.save with raw_path and raw_layout and no shape check (716-728). The session's shape guard (session.py:2079-2098) is not on this path. auto_exposure probes use keep_raw=True (2091), so a metered keep_raw=False pass in debug mode is filed with the last probe's bytes. byte14_probe.py:181-185 runs a final keep_raw=False pass at the ladder's resolution. That scenario additionally needs RPS7200_DEBUG=1, since the probe constructs DirectScanner with the default debug=None.

</details>

<a id="docs-readme-claude-d03"></a>

### D03 -- RPS7200_DEBUG=1 is silently overridden in tools/scan.py, tools/scan_roll.py and the GUI, so intermediate passes are never filed

**Severity** high · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan.py:157-160`, `tools/scan_roll.py:344-353`, `rps7200/session.py:1265-1271`, `rps7200/session.py:2051-2060`, `rps7200/direct.py:2083-2092`, `rps7200/direct.py:3494-3498`, `rps7200/direct.py:3528-3536`, `rps7200/direct.py:1946-1966`

**Doc claim:** CLAUDE.md:136-145 ('the environment variable is better because a script inherits it'); README.md:488-490; rps7200/session.py:2057-2060

CLAUDE.md prefers the environment variable 'because a script inherits it'. But every operator path hard-codes debug=False and only files the pass it returns. Metering probes (2-3 per metered frame), hold and aim verification prescans, `prescan_before` (filed with file_entry=False) and the calibration pass are never filed, whatever RPS7200_DEBUG says. The session docstring and the auto_exposure comment claim otherwise.

**Evidence (from the code):**

```text
tools/scan.py: `with DirectScanner(verbose=args.verbose, debug=False) as s:`; scan_roll.py same; session._default_scanner: `return DirectScanner(verbose=self.verbose, debug=False)`. session._file docstring: 'Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway, filed at the instant it was taken'. auto_exposure: 'CLAUDE.md's rule is "file every scan, with its raw bytes" ... keep_raw=True' (a probe is only spooled via _debug_capture).
```

**Failure scenario:** Stefan runs a roll from the GUI with RPS7200_DEBUG=1 set, as CLAUDE.md demands. Later he asks what the metering probe saw on frame 12, or for the pre-correction prescan of a frame the hold loop moved. Neither was ever filed: the probe's raw bytes and the prescan-before pixels existed only in memory.

**Fix:** Either honour the env var (debug=None) with de-duplication by pass id, or file the intermediate passes explicitly: probes via a capture callback, prescan_before with its own capture record. Correct the session._file docstring, the auto_exposure comment and CLAUDE.md to say which passes are filed.

<details><summary>Second reader's check</summary>

tools/scan.py:160, tools/scan_roll.py:353 and session._default_scanner (session.py:1265-1271) all pass debug=False explicitly, which overrides the env var (direct.py:464-465 only reads the env when debug is None). Metering probes (keep_raw=True but never filed outside debug), hold and verification prescans, and prescan_before (session.py:1840-1852, file_entry=False) are therefore never filed. The _file docstring claim 'Under RPS7200_DEBUG=1 that picture already has a correct entry anyway' (session.py:2057-2060) is false on every session path. So is the auto_exposure comment invoking 'file every scan' (direct.py:2087-2091): outside debug mode no probe is filed.

</details>

<a id="docs-readme-claude-d04"></a>

### D04 -- The GUI gzips library entries with the scanner open and idle after every single scan or prescan

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:1311`, `rps7200/session.py:1013-1021`, `rps7200/session.py:1429-1438`, `rps7200/session.py:1456`, `rps7200/session.py:2121-2141`, `rps7200/session.py:1333-1343`, `rps7200/library.py:192-210`, `tools/gui.py:3843-3868`

**Doc claim:** CLAUDE.md:160-172 ('A single scan compresses nothing while the device is open ... gzipped after close()'; 'A roll is the exception'); CLAUDE.md:368-369 ('Do not hold the session open through heavy local work'); README.md:500-502

CLAUDE.md says a single scan compresses nothing while the device is open, that only a roll overlaps gzip with a *busy* device, and not to hold the session open through heavy local work. The window keeps the device open for its whole lifetime, and its single Scan/Prescan jobs hand the entry to FrameWriter at once. The gzip therefore runs while the scanner sits open and idle, which is exactly the state CLAUDE.md says preceded a wedge. Save As, Save all and Export (library.corrected plus a deflate TIFF write) also run with the device open and idle.

**Evidence (from the code):**

```text
session._run: `self._writer = FrameWriter(on_done=self._filed)` at session start; _prescan/_scan call `self._file(...)` → `self._writer.submit(...)`; FrameWriter._run writes immediately (`library.save` → `gzip.open(path / "raw.bin.gz", "wb", compresslevel=6)`). FrameWriter docstring: 'On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.'
```

**Failure scenario:** The operator scans one 3600 dpi RGBI frame, about 250 MB. The worker goes idle while the writer thread spends many seconds gzipping raw.bin.gz with the device claimed and idle. If that correlation with a wedge is real, the next job finds the scanner unresponsive and needs a power cycle. tools/filing_load_test.py only measures a busy device, never this idle case.

**Fix:** For non-roll jobs, spool the entry uncompressed and compress after close() (as DirectScanner._debug_flush does), or document and measure the open-idle case. At minimum correct CLAUDE.md and the FrameWriter docstring to say the GUI gzips with the device open and idle.

<details><summary>Second reader's check</summary>

session._run creates `FrameWriter(on_done=self._filed)` once the device is open (session.py:1311). The worker thread starts at construction (1043-1044) and _write calls library.save, which gzips raw.bin.gz (library.py:192-210) as soon as a job is queued. _prescan and _scan queue a job at the end of the pass, after which the worker goes back to `self._jobs.get()` with the device claimed and idle. The comment in _run's finally (1333-1336) and the FrameWriter docstring (1016-1019) show the authors know the open-idle state is the hazard, but only the roll case keeps the device busy. The link to a wedge is the docs' own correlation claim, which is why this is high and not critical.

</details>

<a id="docs-readme-claude-d05"></a>

### D05 -- --no-shading does not skip calibration: prescan(), metering probes and roll frames calibrate lazily, on the path the code itself says stalled the device

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `tools/scan_roll.py:145-146`, `tools/scan_roll.py:166-173`, `tools/scan_roll.py:433-446`, `rps7200/direct.py:1704-1715`, `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2560-2602`, `rps7200/direct.py:3292-3317`, `rps7200/direct.py:3494-3496`, `rps7200/direct.py:3644-3652`, `tools/scan.py:62-63`, `tools/scan.py:225-234`

**Doc claim:** README.md:140 ('--no-shading    # raw pixels, for comparison'); tools/scan_roll.py:145-146 help text

ensure_shading(skip=True) leaves the session with no reference. Any later pass that goes through prescan(), auto_exposure() or DirectScanner.scan_roll() still asks for shading=True, and so calls calibrate_shading() from inside the pass. That is the lazy path scan_roll.py's own comment says stalled the device twice. The flags promise raw, striped scans with no calibration; what actually happens is a surprise 3-4 minute calibration on the known-bad path, plus corrected pixels.

**Evidence (from the code):**

```text
scan_roll.py help: '--no-shading ... skip calibration entirely; scans come back striped'. prescan(): `self.scan(..., shading=True, ...)` hard-coded. auto_exposure probe: `self.scan(resolution=resolution, infrared=False, exposure_scale=scales, keep_raw=True)` (shading defaults True). scan(): `if self._shading is None or needed > ...: self.calibrate_shading()`. scan_roll() has no shading parameter. scan_roll.py:437-440: 'that lazy calibration stalls: `bulk read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE` ... and the device stops answering'.
```

**Failure scenario:** `uv run python tools/scan_roll.py --dry-run --no-shading --frames 6` (or `tools/scan.py --dpi 1800 --no-shading --auto-exposure`). The first prescan or metering probe triggers calibrate_shading inside scan(). By the file's own record that can end in LIBUSB_ERROR_PIPE, an abandoned read and a power cycle. It also calibrates whatever is in the transport, which the operator never agreed to.

**Fix:** Thread a `shading` flag through scan_roll(), prescan() and auto_exposure(), and honour skip, or refuse --no-shading with prescans and metering. Never calibrate lazily from inside prescan or scan: raise ShadingUnavailable instead and let the caller calibrate up front.

<details><summary>Second reader's check</summary>

ensure_shading(skip=True) leaves _shading None (direct.py:592-594). prescan() hard-codes `shading=True` (1704-1715). auto_exposure probes call self.scan(...) without shading=, so the default shading=True applies (2437, 2083-2092). scan() then calls self.calibrate_shading() lazily (2583-2593). scan_roll.py:433-446 documents that this lazy path stalled with LIBUSB_ERROR_PIPE twice. So `scan_roll.py --no-shading` (any roll, dry run or not, since every frame prescans) and `scan.py --no-shading --auto-exposure` both trigger an unrequested calibration on that path. Both help texts ('skip calibration entirely', 'return raw pixels') are wrong.

</details>

<a id="docs-readme-claude-d06"></a>

### D06 -- shading.npz is a host-derived reduction; the calibration bytes and calibration-time settings are thrown away

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1895-1950`, `rps7200/direct.py:1964-1967`, `rps7200/shading.py:109-182`, `rps7200/shading.py:75-91`, `rps7200/library.py:182-183`, `rps7200/direct.py:1845`

**Doc claim:** CLAUDE.md:59-61 ('Every library entry keeps its raw bytes, its shading reference and its CCD mask, so decoding, correction and merging are all re-runnable offline'); README.md:185-187, 644-646

The docs say every entry keeps 'the session's shading reference' so correction is 're-runnable offline', and that the scanner 'hands back' the reference. What is stored is this driver's own reduction of about 1.66 MB of 16-bit calibration lines. The dark/light split heuristic, the averaging and the channel mapping are all baked in. The raw lines, the descriptor, the CCD mask of the calibration pass and the exposure in force are never filed anywhere (not even in debug mode). A change to calculate_shading, which tests/test_shading.py::test_the_two_phases_are_separated shows is live code, can never be re-applied to past scans.

**Evidence (from the code):**

```text
calibrate_shading: `collected.append(chunk)` ... `data = b"".join(collected)` / `self._shading = calculate_shading(data, width)` / returns `"data": data if keep_data else None` (keep_data defaults False; no caller files it). calculate_shading splits lines by level gap and stores `stack[is_dark].mean(axis=0)` / `stack[~is_dark].mean(axis=0)` as float64. ShadingReference.save writes only ref/dark/mean arrays. The gain/offset/exposure written before calibration (`self.set_gain_offset(self.get_gain_offset())`) is not recorded.
```

**Failure scenario:** A better phase split, or outlier rejection in calculate_shading, is written later. Every existing library entry still carries the old reduction and cannot be re-derived. reconstruct cannot even detect the difference, because it never re-runs calculate_shading.

**Fix:** Keep the calibration pass's raw bytes (gzip), its width/descriptor, its CCD mask and the gain/offset/exposure readback beside calibration/shading.npz. Content-address it (sha256) and reference it from each entry. Have library.corrected optionally rebuild the reference with today's calculate_shading.

<details><summary>Second reader's check</summary>

calibrate_shading collects the calibration lines into `data` (1945), reduces them with calculate_shading (1949) and returns `'data': data if keep_data else None` (1966). keep_data defaults False and no caller passes True (grep). The CCD mask read at 1942 goes into self._ccd_mask and is later overwritten by each pass's own mask (2672). library.save stores only reference.save() (the derived float arrays) and the pass's mask. The raw calibration bytes, the descriptor and the calibration-time gain/offset/exposure are recorded nowhere, even in debug mode, so a change to calculate_shading can never be applied to past entries.

</details>

<a id="docs-readme-claude-d07"></a>

### D07 -- tools/scan_roll.py --dry-run files nothing in the library, and roll-frame prescans lose their raw bytes (CLI and GUI)

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan_roll.py:492-510`, `tools/scan_roll.py:542-568`, `rps7200/session.py:1883-1893`, `rps7200/direct.py:3494-3498`, `rps7200/direct.py:3656-3660`, `rps7200/library.py:180-181`

**Doc claim:** CLAUDE.md:104-105 ('File every scan in the library, with its raw bytes. tools/scan.py and tools/scan_roll.py both default to library/'); README.md:185; tools/scan_roll.py:20-22

CLAUDE.md's founding story is six missing prescans, and it states that tools/scan_roll.py defaults to library/. Yet the documented first step ('Start with --dry-run') writes only corrected prescanNN.tif files into rolls/; their raw bytes, CCD masks and read meta are discarded, and debug is forced off. On real rolls (both CLI and GUI), the frame entry's prescan.tif is the shading-corrected prescan with no raw bytes and no mask, unlabelled, even though DirectScanner carried raw_prescan. The GUI's dry-run walk does file prescans properly, so the two paths diverge.

**Evidence (from the code):**

```text
scan_roll.py dry run: `pre = out / f"prescan{number:02d}.tif"` / `tiff.write(str(pre), frame.prescan)`; no writer.submit and no library.save for dry-run frames. Real frames: `prescan=frame.prescan` (the corrected prescan) with the RollFrame's `raw_prescan` never used. session._roll likewise passes `prescan=rf.prescan` for frames; library.save writes it verbatim: `tiff.write(str(path / "prescan.tif"), prescan)`.
```

**Failure scenario:** A 38-frame walk with scan_roll.py --dry-run. Months later the registration of frame 7 is questioned; the only record is a corrected 8-bit TIFF in rolls/, and deleting the roll folder loses even that. For scanned frames, prescan.tif cannot be re-corrected or re-decoded if apply_shading's 8-bit scaling ever changes.

**Fix:** In scan_roll.py, submit dry-run prescans to FrameWriter with raw_image=frame.raw_prescan and their prescan_meta (as session._roll does). File each frame's prescan as its own entry with raw bytes, or store raw_prescan (and its mask) in the frame entry and label prescan.tif as corrected.

<details><summary>Second reader's check</summary>

On --dry-run, scan_roll.py (492-510) only calls `tiff.write(str(pre), frame.prescan)` and `tiff.write(str(was), frame.prescan_before)` into rolls/<roll>/. Nothing calls writer.submit or library.save, and debug is forced off, so the raw prescans, masks and meta are discarded. For real frames, `prescan=frame.prescan` (the corrected prescan) is passed and frame.raw_prescan is never used. session._roll does the same for frames (1886-1889). library.save writes the prescan verbatim (library.py:180-181) with no label. The GUI's dry run does file prescans with raw_image=rf.raw_prescan (session.py:1817-1832), so the CLI and GUI paths diverge.

</details>

<a id="docs-readme-claude-d08"></a>

### D08 -- make_comparison.py's '2_corrected.tif' uses a flat-file destripe the driver never ships, not apply_shading or library.corrected

**Severity** high · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/make_comparison.py:92-138`, `rps7200/defects.py:154`, `rps7200/library.py:338-391`

**Doc claim:** CLAUDE.md:179-184 ('Regenerate the three comparison files after any significant change to the scan or correction path ... Stefan judges by eye'); README.md:195

CLAUDE.md makes these three files the authoritative by-eye check after 'any significant change to the scan or correction path'. The 'corrected' file is produced by a defect-detection plus destripe pipeline driven by an old flat TIFF (default scans/flat/flat_clearfilm_3600dpi.tif), not by the shading correction everything else ships. The comparison therefore cannot show a regression in apply_shading, the ccd mask mapping or calculate_shading, and can show artefacts the product does not have. It also recomputes rather than measuring the delivered file, against CLAUDE.md's own 'measure the file you delivered'. If previews/ is missing it raises after writing the TIFFs.

**Evidence (from the code):**

```text
`flat = tiff.read(flat_path)` ... `corrected = destripe(raw, defects, margin=12, dilate=5)` / `tiff.write("2_corrected.tif", corrected, ...)`; `grep destripe(` shows make_comparison.py is its only caller. Output also does `Image.fromarray(v)...save(f"previews/{name}.png")` with no mkdir.
```

**Failure scenario:** A change breaks apply_shading's column mapping at 1800 dpi. Claude regenerates the three files as instructed; 2_corrected.tif looks clean because destripe never calls apply_shading. Stefan approves by eye and the regression ships.

**Fix:** Build 2_corrected.tif from library.corrected(entry) (or apply_shading with the entry's reference and mask) and take a library entry as input. Drop the flat-file destripe or label it as a separate experiment, and create previews/ before saving.

<details><summary>Second reader's check</summary>

make_comparison.py builds `corrected = destripe(raw, defects, margin=12, dilate=5)` from flat-file defects plus scan-detected defects (lines 100-117). It never calls apply_shading or library.corrected, and it is destripe's only non-test caller. It also reads arbitrary TIFFs (default scans/negatives/state_1800dpi.tif), not a library entry. The previews/*.png save has no mkdir, so on a clean tree it raises after the three TIFFs are written. CLAUDE.md makes these three files the authoritative by-eye gate for 'the scan or correction path', and the gate does not exercise the shipped correction.

</details>

<a id="docs-readme-claude-dx1"></a>

### DX1 -- In --demo every filed entry is corrected pixels labelled raw, because DemoScanner publishes no last_pixels_raw and its RollFrames carry no raw_image

**Severity** high · **Category** demo-divergence · **Verdict** found-by-verifier · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:176`, `rps7200/demo.py:955-1024`, `rps7200/demo.py:1164-1195`, `rps7200/demo.py:771-779`, `rps7200/session.py:1415`, `rps7200/session.py:1830`, `rps7200/session.py:1886`, `rps7200/session.py:1089-1091`, `rps7200/library.py:373-391`

**Doc claim:** CLAUDE.md:190-200 ('--demo may change what the software is fed. It may not change what the software does'); README.md:221-225 ('it runs the same deinterleave and the same shading correction a real pass runs')

The real driver hands the session raw pixels for prescans and roll frames (last_pixels_raw, RollFrame.raw_image/raw_prescan). The demo stand-in hands none, so every demo prescan, dry-run prescan and roll frame, as well as every single scan (D01), is filed with the shading-corrected decode as scan.tif, corrections_applied=[], and the source entry's raw bytes (when the shape matches) plus reference beside it. Demo prescans read from a stored prescan.tif keep the previous decode's reference and mask in the capture, so they get a reference they were never taken with. This breaks the rule that the demo is the real software with different inputs: the filing path behaves differently from the hardware path, and demo output then looks double-corrected.

**Evidence (from the code):**

```text
`class DemoScanner:` (not a DirectScanner subclass; `grep last_pixels_raw rps7200/demo.py` finds nothing). _decode: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)` and returns `{"reference": reference, "ccd_mask": mask, "raw": raw, ...}`. Demo scan_roll yields `RollFrame(index=..., image=image, meta=meta or {}, prescan=prescan, registration=marks, prescan_meta=prescan_meta)` with no raw_image/raw_prescan. session._prescan: `raw_image = getattr(self._scanner, "last_pixels_raw", None)`; FrameWriter: `library.save(job["image"] if raw_image is None else raw_image, ...)`.
```

**Failure scenario:** make run-demo, walk a roll, and open a frame at 1:1 or Save As. library.corrected() re-applies apply_shading to already-corrected pixels, so the shadows are crushed and the column gain is applied twice. Running `tools/library.py --root demo/library reconstruct` reports every demo entry as 'N samples differ'. Someone judging the driver by the demo sees a correction regression that does not exist on the hardware path for rolls and prescans, and may 'fix' the real path.

**Fix:** Have DemoScanner set last_pixels_raw (the pre-shading decode) on every prescan and scan, and fill RollFrame.raw_image/raw_prescan in demo.scan_roll. Better, make DemoScanner inherit these attributes from DirectScanner's class attributes and share one scan-finalisation helper. When a prescan comes from prescan.tif, clear the capture's reference and mask, or label the entry corrections=['shading'].

<a id="docs-readme-claude-d09"></a>

### D09 -- 7200 dpi scan.tif is stagger-realigned (4 rows trimmed), not 'the decode alone'; reconstruct flags it as changed forever

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1637-1664`, `rps7200/library.py:411-436`, `rps7200/library.py:464-497`, `tools/library.py:95-113`, `tools/library.py:175-182`

**Doc claim:** CLAUDE.md:114-116 ('scan.tif in an entry is the decode alone'); CLAUDE.md:110-112 (reconstruct 'reports what no longer matches')

At 7200 dpi (only possible with shading=False, e.g. `tools/scan.py --dpi 7200 --no-shading`) a host-side geometric correction is baked into scan.tif. It is unlabelled and not re-applied by reconstruct, decode_raw or migrate-raw. The raw bytes survive, so nothing is lost, but the record misdescribes scan.tif and every 7200 dpi entry is a permanent false alarm in the one regression check.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: image = self._realign_native_column_stagger(image)` then `raw_pixels = image`; nothing in meta records the realignment and no caller passes corrections=. library.reconstruct/decode_raw call only `DirectScanner.decode_index`/`_deinterleave` → shape (6888,...) vs stored (6884,...) → `decode CHANGED: now ..., stored ...`; tools/library.py returns 1 when any entry changed.
```

**Failure scenario:** After any 7200 dpi scan, `make reconstruct` always exits 1 with 'decode CHANGED'. A real decode regression elsewhere is buried among these, and `migrate-raw` reports '! decode is (6888,...), stored (6884,...)' for every such entry.

**Fix:** Record `stagger_realigned: 4` in meta and scan.json, or store the plain decode and apply the realignment in library.corrected. Make reconstruct and decode_raw apply the same realignment when the record says so.

<details><summary>Second reader's check</summary>

direct.py:2695-2708 realigns (trims NATIVE_COLUMN_STAGGER_LINES=4 rows) before `raw_pixels = image`, and meta records nothing about it. layout['lines'] is params.lines (1526). library.reconstruct (decode_index) and decode_raw (_deinterleave) do not realign, so shapes differ and reconstruct returns 'decode CHANGED', which tools/library.py counts as changed and exits 1. Reachable only with shading=False (tools/scan.py --dpi 7200 --no-shading, or scripts), because the GUI has no shading-off control (gui.py:1408-1412) and scan() refuses 7200 with shading.

</details>

<a id="docs-readme-claude-d10"></a>

### D10 -- tools/scan_roll.py --start-at overwrites roll.json instead of carrying the earlier run forward

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/scan_roll.py:291-329`, `tools/scan_roll.py:446-453`, `tools/scan_roll.py:584-585`, `tools/scan_roll.py:624-628`, `tools/scan_roll.py:20-24`, `rps7200/session.py:1643-1668`

**Doc claim:** README.md:547-549 ('--start-at resumes a roll that stopped ... a resumed roll carries forward what the earlier session already did instead of overwriting its manifest'); tools/scan_roll.py:22-24

README says a resumed roll carries forward what the earlier session did instead of overwriting its manifest. That is true only for the GUI (ScanSession._roll). The CLI tool starts a fresh manifest and writes it over roll.json as soon as calibration succeeds, so the frame records, registration and entry links of the earlier run are lost. Library entries survive, but the roll's record and the GUI's Rolls table and Export (which read the manifest) forget those frames.

**Evidence (from the code):**

```text
scan_roll.py builds `manifest = {..., "frames": [], }` and `checkpoint()` does `manifest_path.write_text(json.dumps(manifest, ...))`; nothing reads an existing roll.json (no equivalent of session._roll's `earlier = json.loads(manifest_path.read_text(...))` / `renumbered(...)`). Module docstring: '`--start-at` resumes from the manifest'.
```

**Failure scenario:** A 36-frame CLI roll dies at frame 20. The operator resumes with `--start-at 21 --roll same-name`. roll.json now lists only frames 21-36, and the Rolls dialog shows 16 frames and exports 16.

**Fix:** Reuse session.renumbered/merge logic in scan_roll.py, reading the earlier roll.json before the first checkpoint, or write to a new run-specific file. Fix the docstring and README until then.

<details><summary>Second reader's check</summary>

scan_roll.py:298-329 builds a fresh manifest with `'frames': []`, and checkpoint() overwrites manifest_path. The only JSON reads in the file (233-239) serve hold_from_walk for --approved and never merge into the manifest being written. session._roll's merging logic has no counterpart here. The README text at 547-549 sits in the scan_roll.py section, next to --max-failures, and the module docstring (22-24) claims --start-at 'resumes from the manifest'.

</details>

<a id="docs-readme-claude-d11"></a>

### D11 -- Transport and geometry numbers in the docs are stale or contradict each other and the code

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:553-557`, `README.md:573-577`, `CLAUDE.md:264-268`, `CLAUDE.md:418-421`, `CLAUDE.md:460-463`, `rps7200/protocol.py:240-241`, `rps7200/protocol.py:260`, `rps7200/protocol.py:276-279`, `rps7200/protocol.py:287-292`, `rps7200/framing.py:290`, `rps7200/framing.py:586`, `rps7200/framing.py:740`, `rps7200/framing.py:1281`, `rps7200/framing.py:1934-1957`, `rps7200/session.py:91-95`, `rps7200/session.py:672-693`

**Doc claim:** README.md:555-556; README.md:576; CLAUDE.md:264-268, 418-421, 460-463

The mm law in README predates the 1.84 ramp. CLAUDE.md contradicts itself (2.6 vs 2.84 units). The code carries two unit definitions that disagree by about 0.2% (0.1057 mm per unit vs 428 columns / 1.2423). It also carries two frame-width models: the rps7200 strip detector (TARGET_GAP_MM, MAX_CORRECTION_MM) aims for a 0.25 mm gap on a 36.0 mm frame, while CLAUDE.md says a frame is wider than the aperture and shows no base. Only callers that pass tools/frame_edges as edge_reader use FRAME_WIDTH_UNITS; direct DirectScanner.scan_roll(correct=True) callers do not.

**Evidence (from the code):**

```text
Code: `MM_PER_UNIT = 0.1057`, `COMMAND_UNITS = 1.84`, `MM_PER_COMMAND = MM_PER_UNIT * COMMAND_UNITS` (0.1945 mm). README: '`0.1057 × param + 0.1662` mm' (0.1662/0.1057 = 1.572, the value CLAUDE.md says is 'ruled out'); protocol.py comment repeats '0.1057 mm x param + 0.1662 mm'; units_for_param docstring 'param 1 travels 2.57'. CLAUDE.md:420 '`param 1` (~2.6 units)' vs CLAUDE.md:265 'param 1 is 2.84'. framing: `FRAME_WIDTH_MM = 36.0`, `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0` (frame narrower than aperture) vs `FRAME_WIDTH_UNITS = 350.6` (wider). README:576 '0.49 mm of slack' (`MAX_REGISTRATION_MM = 0.49`). CLAUDE.md gives the aperture as 345.2 units (APERTURE_MM/MM_PER_UNIT) and 344.5 units (PRESCAN_COLUMNS 428 / COLUMNS_PER_UNIT 1.2423). plan_nudges docstring 'integer param in 1..8', 'clamps silently at param 8'; session.py:91-92 'for param 1 and param 8'.
```

**Failure scenario:** An operator or Claude sizes a manual SLIDE from README's law and undershoots by 0.03 mm per command (about 0.27 units). A script calling DirectScanner.scan_roll(correct=True) without edge_reader centres frames on the 36.0 mm model and leaves them about 5 units off where the window would put them. The README's '0.49 mm of slack' reasoning is the premise the code comment at direct.py:3108-3115 says 'is gone'.

**Fix:** Replace README's mm law with 'param + 1.84 units'. Fix CLAUDE.md:420 to 2.84. Fix the docstrings for units_for_param, plan_nudges and FINE_MIN/MAX. Pick one unit definition and derive the aperture from it. Retire FRAME_WIDTH_MM/TARGET_GAP_MM or make the in-package fallback use FRAME_WIDTH_UNITS.

<details><summary>Second reader's check</summary>

protocol.py:240 comment and README:555-556 give `0.1057 x param + 0.1662 mm` (0.1662/0.1057 = 1.572, the ruled-out value), while the code uses COMMAND_UNITS=1.84. units_for_param says 'param 1 travels 2.57' but returns 2.84. CLAUDE.md:420 says '~2.6 units' and :265 says 2.84. session.py:91-92 says 'param 1 and param 8' and plan_nudges says '1..8', 'clamps silently at param 8', but MAX_CORRECTION_PARAM = 87 (direct.py:3123). framing has FRAME_WIDTH_MM=36.0 < APERTURE_MM (10344*25.4/7200 = 36.49 mm = 345.2 units) driving TARGET_GAP_MM, and FRAME_WIDTH_UNITS=350.6 > aperture. The aperture is 345.2 units by MM_PER_UNIT but 344.5 by PRESCAN_COLUMNS/COLUMNS_PER_UNIT. edge_reader defaults to None in DirectScanner.scan_roll (3316, 3422), so the in-package detector is used unless a caller supplies one.

</details>

<a id="docs-readme-claude-d13"></a>

### D13 -- INFRARED_FLOOR_S 'guards a timeout', but no timeout in the code uses it

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P16](../problems/P16-timeouts-and-runtime-budget.md)

**Where:** `rps7200/session.py:55-63`, `tests/test_session.py:667-674`, `rps7200/direct.py:1398-1405`, `rps7200/direct.py:1440-1448`, `rps7200/direct.py:1486-1498`

**Doc claim:** CLAUDE.md:360-367 ('212 s survives in the code as INFRARED_FLOOR_S because it guards a timeout'); README.md:458-459

Both docs justify keeping 212 s in the code because it 'guards a timeout' for the untied infrared pass. It guards nothing. The per-pass idle guard is 120 s of NoDataYet, below both the 212 s figure and the measured ~220 s untied floor. Whether an untied (--no-fast-ir) low-resolution IR pass can sit in NoDataYet longer than 120 s cannot be checked without the device. If it can, read_planes raises mid-scan, which is an abandoned read, the documented wedge.

**Evidence (from the code):**

```text
`INFRARED_FLOOR_S = 212.0` is referenced only in tests (`grep INFRARED_FLOOR_S` → session.py:63 and tests/test_session.py:667,674). Actual guards: read_lines `timeout_ms: int = 120_000, max_wait_s: float = 300.0`; read_planes `idle_timeout: float = 120.0` → `raise ScanReadError(f"no data for {idle_timeout:.0f}s ...")`.
```

**Failure scenario:** `tools/scan.py --dpi 300 --ir --no-fast-ir`. If the device answers 'no data yet' for longer than 120 s while it spends its untied IR floor, read_planes raises ScanReadError, scan() drops the pass without finishing it, and the scanner needs a power cycle.

**Fix:** Either wire INFRARED_FLOOR_S into the read-side idle timeout when fast_infrared is False (max(idle_timeout, INFRARED_FLOOR_S + margin)), or remove the claim from README and CLAUDE.md. Measure the NoDataYet duration on an untied pass.

<details><summary>Second reader's check</summary>

INFRARED_FLOOR_S (session.py:63) is referenced only by tests/test_session.py:667,674, which test estimate_seconds and not any timeout. The real read guards are read_lines timeout_ms=120_000/max_wait_s=300 and read_planes idle_timeout=120.0 (direct.py:1447, 1494-1498), which raises ScanReadError mid-pass. No code path raises idle_timeout for an untied (--no-fast-ir) pass. Whether the device can sit in NoDataYet for more than 120 s cannot be checked offline, so this stays medium.

</details>

<a id="docs-readme-claude-d14"></a>

### D14 -- usbpcap's public packets() hands back interrupt (keystroke) payloads; the test asserts they are returned

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P32](../problems/P32-usbpcap-returns-keystrokes.md)

**Where:** `rps7200/usbpcap.py:103-127`, `rps7200/usbpcap.py:8-18`, `tests/test_usbpcap.py:171-189`, `tools/parse_capture.py:69`

**Doc claim:** CLAUDE.md:489-493 ('That reader only ever returns control setup packets ... it never returns an interrupt payload ... asserts it does not come back')

CLAUDE.md states the reader 'only ever returns control setup packets and payloads for a device the caller named; it never returns an interrupt payload' and that the test asserts a keystroke does not come back. Only setups() and scanner_devices() filter. The public packets(), used by tools/parse_capture.py and exported, returns every record's payload including HID interrupt data. The test only checks that device 7's packets lack the keystroke, and positively expects the other device's interrupt packets.

**Evidence (from the code):**

```text
`def packets(raw: bytes) -> Iterator[Packet]: """Every USBPcap record in the file."""` yields `payload=data[header_len:]` for every transfer type. Test: `others = [p for p in packets(capture.read_bytes()) if p.device != 7]` / `assert all(p.transfer == INTERRUPT for p in others)`, which asserts the keyboard's interrupt packets ARE returned.
```

**Failure scenario:** A new analysis script calls rps7200.usbpcap.packets(open(cap).read()) and dumps payloads to a log or a committed JSON. It leaks the recording machine's keystrokes, the exact hazard CLAUDE.md says this module prevents.

**Fix:** Make packets() private (_packets), or filter INTERRUPT payloads (return b'' for them) at the source. Change the test to assert no INTERRUPT payload bytes are ever returned by any public function.

<details><summary>Second reader's check</summary>

usbpcap.packets() is public and imported by tools/parse_capture.py:39. It yields `payload=data[header_len:]` for every transfer type (usbpcap.py:103-127), interrupt included. Only setups() and scanner_devices() filter, and parse_capture itself filters to CONTROL. The test at tests/test_usbpcap.py:185-189 positively asserts that the non-scanner packets returned by packets() are INTERRUPT, so interrupt records do come back. CLAUDE.md's 'it never returns an interrupt payload' holds only for the two filtered functions.

</details>

<a id="docs-readme-claude-d15"></a>

### D15 -- The roll Delete dialog and README say only approved.json is lost; manifests and CLI-walk prescans go too

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2484-2515`, `rps7200/session.py:1834-1854`, `tools/scan_roll.py:492-510`

**Doc claim:** README.md:280-285 ('Delete removes the roll folder and never touches library/: ... the only thing that goes for good is approved.json')

A roll folder also holds survey.json and roll.json, the only mapping from frames to strip positions, registration and entries. Export and the Rolls table need it. For walks made by tools/scan_roll.py the folder also holds the only copies of the prescans (D07), and prescanNN-before.tif is never filed anywhere (file_entry=False). The warning counts only approved.json files that contain turns or flips, so one holding only positions is not mentioned.

**Evidence (from the code):**

```text
Docstring: 'Everything in a roll folder is re-derivable from the library except `approved.json`'. Dialog: 'The library entries are NOT touched: the raw bytes stay, and the frames can be rebuilt from them.' `decided = sum(1 for s in summaries if any(read_approved(s["folder"])[1:3]))  # turns/flips`, then `shutil.rmtree(summary["folder"])`.
```

**Failure scenario:** The operator deletes a CLI-walked roll, trusting 'the frames can be rebuilt'. The survey prescans, the pre-correction prescans and the manifest are gone for good, and the approved positions (no turns) were never warned about.

**Fix:** Make the dialog list what is really lost: the manifests, prescanNN*.tif with no library entry, and approved.json with positions. Count approved.json by existence. Or move the folder to a trash location instead of rmtree.

<details><summary>Second reader's check</summary>

on_delete_rolls (gui.py:2484-2515) counts only approved.json files whose read_approved(...)[1:3] (turns/flips) are non-empty, then shutil.rmtree's the folder. The dialog says 'the frames can be rebuilt from them'. For CLI walks the prescanNN.tif files are the only copies (see D07). prescanNN-before.tif is never filed (session.py:1840-1852 file_entry=False). survey.json/roll.json carry the walk's registration and settings, which are not in the library.

</details>

<a id="docs-readme-claude-d16"></a>

### D16 -- README's 'refuses what the device refuses' and 'decodes raw bytes' claims do not hold for DemoScanner; it also retypes constants

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:530-597`, `rps7200/demo.py:455-477`, `rps7200/demo.py:1176-1184`, `rps7200/demo.py:393`, `rps7200/demo.py:342`, `rps7200/demo.py:459`, `rps7200/demo.py:553-556`, `rps7200/demo.py:61-65`, `rps7200/direct.py:2560-2578`

**Doc claim:** README.md:221-225; CLAUDE.md:197-200 ('A number, cap, constant or decision the stand-in needs is taken from DirectScanner, never retyped')

README says the demo 'decodes each entry's raw bytes, not the TIFF beside them ... refuses what the device refuses'. The demo accepts 7200 dpi with shading, which the driver refuses before scanning. It serves prescans from the stored corrected TIFF. It fakes metering (no probes, no meta['metering']). It retypes MM_PER_UNIT and other numbers, which CLAUDE.md forbids ('never retyped'). A demo prescan also keeps the previous scan's reference and mask in its capture record while its pixels are already corrected.

**Evidence (from the code):**

```text
DemoScanner.scan has no MAX_SHADING_COLUMNS or ShadingUnavailable check (grep: none in demo.py), while DirectScanner.scan raises for 7200 dpi with shading. Prescans: `tif = source / name` ... `image = tiff.read(str(tif))` (stored, already-corrected prescan.tif, not raw bytes). Retyped numbers: `swallowed = min(abs(asked), 2.2 * 0.1057)`, `self._work(7.0)` for an advance (session.FORWARD_FRAME_S = 4.6), `lines=int(resolution * 0.957)`, the `auto-exposure: [1.82, 0.94, 2.11, 1.0]` log with no metering run, and the SPEED comment '3600 dpi infrared pass, 334 s'.
```

**Failure scenario:** In --demo the operator sets 7200 dpi with calibration on and presses Scan. The demo delivers a picture; on the scanner the same click fails with ShadingUnavailable. The demo reports a plausible, wrong result, the failure mode CLAUDE.md warns about.

**Fix:** Take the 7200/shading refusal from DirectScanner (a static check shared by both). Drive demo prescans from raw bytes or drop the capture record. Import MM_PER_UNIT and FORWARD_FRAME_S instead of retyping them. Route demo metering through DirectScanner.auto_exposure logic, or say it is faked.

<details><summary>Second reader's check</summary>

DemoScanner.scan (demo.py:530-597) has no MAX_SHADING_COLUMNS or ShadingUnavailable check, and the GUI offers 7200 (gui.py:118), so the demo delivers a picture where the device refuses. _pair_image serves prescans from the stored `prescan.tif` (demo.py:1176-1184) without updating self._capture, so a prescan carries the previous decode's reference and mask. The demo retypes `2.2 * 0.1057` (393), `_work(7.0)` for advance and retreat against session.FORWARD_FRAME_S=4.6, `int(resolution * 0.957)`, a fake auto-exposure log line with no metering (555-556), and a copy of the infrared refusal string (550 vs direct.py:2506). DemoScanner is a plain class, not a DirectScanner subclass, so none of these come from the driver.

</details>

<a id="docs-readme-claude-d17"></a>

### D17 -- Bare Return in the 'Calibrate first' prompt starts a calibration: calibrating does have a key

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P17](../problems/P17-calibration-without-asking.md)

**Where:** `tools/gui.py:815-839`, `tools/gui.py:1946-1958`, `tools/gui.py:2021-2030`

**Doc claim:** README.md:333-335 ('Moving film and calibrating have no key at all'); CLAUDE.md:270-285 (calibrate with the film loaded)

README says moving film and calibrating have no key at all. Pressing Cmd+Return with no calibration opens a prompt whose default action, bound to a bare Return, starts a 3-4 minute calibration with no question about what is in the transport. CLAUDE.md says calibrating an empty transport preceded a wedge and that only Stefan can see the transport.

**Evidence (from the code):**

```text
`start = ttk.Button(buttons, text="Calibrate now", command=lambda: choose("measure"))` / `start.focus_set()` / `top.bind("<Return>", lambda _e: choose("measure"))`; the prompt is opened by `_confirm_then` → `_calibration_missing()` when a prescan/scan/roll key is pressed uncalibrated.
```

**Failure scenario:** The operator presses Cmd+Return to prescan, the prompt opens, and a second Return (a repeat or a habit) starts calibrate_shading with an empty transport. That is the state CLAUDE.md links to a wedge.

**Fix:** Do not bind Return to 'Calibrate now'. Make the button non-default, and add an explicit 'film is loaded' confirmation to the calibration prompt.

<details><summary>Second reader's check</summary>

ask_to_calibrate binds `top.bind('<Return>', lambda _e: choose('measure'))` and focuses 'Calibrate now' (gui.py:2025-2030). choose('measure') calls on_calibrate(mode) directly, with no question about what is in the transport; the prompt only tells the operator that film should be loaded. The prompt is opened by _confirm_then → _calibration_missing when Cmd+Return, Cmd+Shift+Return or Cmd+B is pressed uncalibrated. So a second Return starts a 3-4 minute calibration, contrary to README:333-335 'Moving film and calibrating have no key at all'.

</details>

<a id="docs-readme-claude-d12"></a>

### D12 -- Millimetres are still what is persisted and printed, despite CLAUDE.md's 'Millimetres are prohibited'

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `tools/gui.py:2943-2953`, `tools/scan_roll.py:102-107`, `tools/scan_roll.py:412-413`, `tools/scan_roll.py:516-526`, `rps7200/direct.py:3126-3151`, `rps7200/protocol.py:227`

**Doc claim:** CLAUDE.md:245-262 ('Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter')

CLAUDE.md prohibits millimetres for transport distances, and protocol.py restates the rule as 'anything an operator reads'. Yet approved.json, Approved.offset_mm, the registration records and nudge/param_for_mm are all in mm, and tools/scan_roll.py's --nudge flag and its progress prints show mm to the operator. The param actually sent is not persisted beside the offset, so a later audit cannot tell which command a stored approval produced.

**Evidence (from the code):**

```text
approved.json: `"offset_mm": round(a.offset_mm, 4)`; `Approved.offset_mm`; `nudge(self, millimetres)` / `param_for_mm(millimetres)`; scan_roll.py: `--nudge` 'move the film this many mm', `print(f"offset the film by {sent:+.3f} mm ...`, `f"offset {offset:+.2f} mm"`, `f", SHORT BY {short:.2f} mm"`. protocol.py narrows the rule to 'Millimetres are prohibited in anything an operator reads'.
```

**Failure scenario:** A walk approved under the 1.572 law is reopened after the 1.84 change. offset_mm is re-snapped by param_for_mm under the new law, so a frame approved as 'param 30' is sent as a different param. Nothing records that the operator's decision moved.

**Fix:** Persist offsets in units (and the param sent) in approved.json, Approved and the registration records, with mm kept only as a derived display. Make scan_roll.py print and accept units. Or update CLAUDE.md to state the actual, narrower rule.

<details><summary>Second reader's check</summary>

The mm persistence and output are real. gui.py:2949 writes `offset_mm`, param_for_mm/nudge take mm, and scan_roll.py prints '{sent:+.3f} mm', 'offset {offset:+.2f} mm' and 'SHORT BY ... mm'. protocol.py:227 narrows the rule to 'anything an operator reads', which scan_roll.py's prints still break. The stated failure scenario is wrong, though. approved.json stores a desired physical displacement, and re-snapping it with a corrected law is the right behaviour: storing the param would preserve the old law's error. The defect is the doc/code mismatch with the owner's explicit rule and the operator-facing mm prints, not silent corruption of approvals.

</details>

<a id="docs-readme-claude-d18"></a>

### D18 -- README resolution table widths and timing semantics contradict code and tests

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:445-455`, `CLAUDE.md:350-356`, `rps7200/session.py:743-777`, `rps7200/demo.py:904-912`, `tests/test_demo.py:235-240`, `rps7200/framing.py:1942`, `rps7200/direct.py:2695-2701`

**Doc claim:** README.md:445-455; CLAUDE.md:350-356

The table's pixel counts are a ratio, not what the device returns (428, 860, 2584 per code and tests). The 7200 dpi height is trimmed. The docs describe the tied-IR formula as an increment over RGB while the code and the table treat it as the total RGBI pass time, so budgets computed from the prose come out about 85 s too high at 1800 dpi. The two docs also disagree on the 600 dpi median.

**Evidence (from the code):**

```text
README: '300 | 431 × 287', '600 | 862 × 574 | ~34 s', '1800 | 2586 × 1722', '7200 | 10344 × 6887'. Code/tests: 'the widths the scanner reports are 428, 860, 1292, 2584, 5172 for 300 to 3600 dpi'; `PRESCAN_COLUMNS = 428.0`; 7200 dpi realign trims 4 rows. estimate_seconds: `tied = 7.5 + 0.0599 * lines; if fast_infrared: return tied` (the whole RGBI pass), whereas README:454 and CLAUDE.md:354 say IR tied 'adds' 7.5 s + 59.9 ms/line. CLAUDE says 32 s at 600 dpi, README 34 s.
```

**Failure scenario:** A consumer sizes buffers or crops from the table (431 columns at 300 dpi) and is off by 3 columns. Claude adds 85 + 110 s for an 1800 dpi RGBI budget instead of about 110 s.

**Fix:** Take the widths from the library or tests (428/860/1292/2584/5172) and note the 4-row 7200 trim. Reword to 'a tied RGBI pass costs 7.5 s + 59.9 ms/line in total'. Reconcile 32 vs 34 s.

<details><summary>Second reader's check</summary>

The README table gives widths of 431/862/2586/10344, while framing.PRESCAN_COLUMNS=428 and tests/test_demo.py:236 give 428/860/1292/2584/5172, and the 7200 dpi height is trimmed by 4 rows. estimate_seconds returns `tied` as the whole RGBI pass time (session.py:774-776), while README:453-454 and CLAUDE.md:353-354 say infrared tied 'adds' 7.5 s + 59.9 ms/line. The two docs also disagree on the 600 dpi median (34 s vs 32 s).

</details>

<a id="docs-readme-claude-d19"></a>

### D19 -- README's '--mono G' is not a valid flag; the option is --mono-channel G

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan.py:87-100`, `rps7200/mono.py:64-73`

**Doc claim:** README.md:159

`--mono` takes no argument, so `--mono G` is rejected by argparse ('unrecognized arguments: G').

**Evidence (from the code):**

```text
`ap.add_argument("--mono", dest="mono", action="store_true", default=None, ...)` and `ap.add_argument("--mono-channel", default=MONO_CHANNEL, choices=list(MONO_CHOICES), ...)`, with MONO_CHOICES = ('avg','R','G','B').
```

**Failure scenario:** The operator types `tools/scan.py --film bw --mono G` from the README table and the tool exits with a usage error before scanning.

**Fix:** README: '(`--mono-channel G` for a single one)'.

<details><summary>Second reader's check</summary>

tools/scan.py:87 defines `--mono` as store_true, and the channel is `--mono-channel` with choices avg/R/G/B. README:159's `--mono G` leaves a stray positional that argparse rejects.

</details>

<a id="docs-readme-claude-d20"></a>

### D20 -- TIFFs are deflate-compressed only when tifffile is installed; the built-in writer always writes uncompressed

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/tiff.py:110-117`, `rps7200/tiff.py:149-153`, `rps7200/tiff.py:167-168`, `rps7200/tiff.py:231`

**Doc claim:** README.md:472-476, 508-515

README states writes are deflate plus predictor and sizes a roll's disk cost 'with deflate-compressed TIFFs'. tifffile is optional (not a runtime dependency), so a bare `pip install -e .` writes every scan.tif, frameNN.tif and export uncompressed, about 15% larger than the table.

**Evidence (from the code):**

```text
'honoured on the tifffile path; the built-in writer ignores it and always writes uncompressed'; `add(_COMPRESSION, _SHORT, [1])`.
```

**Failure scenario:** On an install without tifffile, a 38-frame 3600 dpi roll needs more disk than the README's ~9.3 GB estimate.

**Fix:** Say 'deflate when tifffile is installed, uncompressed otherwise', or make tifffile a runtime dependency.

<details><summary>Second reader's check</summary>

tiff.write docstring (tiff.py:109-117): compression is 'honoured on the tifffile path; the built-in writer ignores it and always writes uncompressed'. tifffile is an optional extra and dev-group dependency only (pyproject.toml:36, 59), so README:472-476's unconditional 'Writes are deflate-compressed' is wrong for a bare install.

</details>

<a id="docs-readme-claude-d21"></a>

### D21 -- `make run-sheet` depends on a gitignored local walk and fails on a fresh checkout

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tasks.py:164-185`, `tools/gui.py:8168-8177`, `.gitignore:45`

**Doc claim:** CLAUDE.md:16-18, 217-218; README.md:217-218

CLAUDE.md presents run-sheet as the no-scanner, no-film exercise of the sheet, alongside run-demo. It only works on the machine that holds rolls/aligned-strip, and elsewhere exits with a usage error.

**Evidence (from the code):**

```text
`DEMO_ROLL = "rolls/aligned-strip"`; gui.main: `ap.error(f"no roll folder at {args.open_roll!r}, and none called that under {rolls_dir}")`; .gitignore contains `rolls/`.
```

**Failure scenario:** A contributor or CI runs `make run-sheet` on a clone and gets 'no roll folder at rolls/aligned-strip'.

**Fix:** Document the dependency, or fall back to the newest walk in rolls/, or ship a small fixture walk.

<details><summary>Second reader's check</summary>

tasks.py:169 `DEMO_ROLL = "rolls/aligned-strip"`. gui.main calls ap.error when the folder is missing (gui.py:8168-8177). rolls/ is gitignored at .gitignore:45 (not :52) and absent in this checkout.

</details>

<a id="docs-readme-claude-d22"></a>

### D22 -- CLAUDE.md describes the media flag as READ_STATE 0x40 (0x0d/0x4d); the code uses byte 8 and says byte 6 was wrong

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/protocol.py:535-575`, `rps7200/direct.py:2543-2552`, `tests/test_hardware.py:205-225`

**Doc claim:** CLAUDE.md:281-285

CLAUDE.md says the 0x40 bit tracked the film ('0x0d empty, 0x4d loaded'). README and the code say byte 8 (1 empty / 0 loaded) is the flag and byte 6's 0x40 is unreliable. The log line reports the wrong byte as its evidence.

**Evidence (from the code):**

```text
State.media_loaded: 'Byte 8, and it is inverted ... This used to read `scanning & MEDIA_PRESENT`, byte 6, and could not be believed: in the same reading that byte said 0x1d, i.e. no film, with a strip demonstrably in the transport.' scan(): `if not state.media_loaded: self._log(f"note: state {state.scanning:#04x} suggests no film ...")` (prints byte 6 while deciding on byte 8).
```

**Failure scenario:** Claude checks byte 6 for film as CLAUDE.md suggests and reaches the wrong conclusion; an operator reading the 'state 0x1d suggests no film' log sees the wrong byte cited.

**Fix:** Update CLAUDE.md to byte 8 inverted, and log state.busy/raw[8] in scan().

<details><summary>Second reader's check</summary>

State.media_loaded uses byte 8, inverted, and its docstring says byte 6's MEDIA_PRESENT 'could not be believed' (protocol.py:555-575). CLAUDE.md:281-285 still describes the 0x40 bit with 0x0d/0x4d. scan() logs `state.scanning` (byte 6) while deciding on byte 8 (direct.py:2543-2552).

</details>

<a id="docs-readme-claude-d23"></a>

### D23 -- library.verify reports deliberate raw scans as 'correction was asked for', contrary to comments saying it draws that line

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/library.py:794-802`, `rps7200/direct.py:296-303`, `rps7200/library.py:359-364`, `tools/library.py:83-88`

**Doc claim:** CLAUDE.md:25 ('make verify # check the library's checksums and completeness'); rps7200/direct.py:299-301

`make verify` exits 1 and mislabels every deliberately raw entry (--no-shading, 7200 dpi) as a shortfall, which is the opposite of what library.corrected distinguishes.

**Evidence (from the code):**

```text
`why = cal.get("skipped")` / `problems.append(f"...no shading reference, so this scan can never be corrected" + (f" -- correction was asked for: {why}" if why else ""))`. For `shading=False`, skipped == SHADING_SKIPPED_EXPLICIT, so the message reads 'correction was asked for: shading=False (explicit)'. The comment in direct.py says '`verify` already draws the same line'.
```

**Failure scenario:** After a `--no-shading` comparison scan, `make verify` fails with 'correction was asked for', and a real shortfall is indistinguishable in the output.

**Fix:** Compare against SHADING_SKIPPED_EXPLICIT in verify and report deliberate raw separately, without affecting the exit code.

<details><summary>Second reader's check</summary>

library.verify (794-802) appends 'no shading reference ... -- correction was asked for: {why}' for any truthy skipped value, including SHADING_SKIPPED_EXPLICIT. So a deliberate raw scan reads 'correction was asked for: shading=False (explicit)' and tools/library.py verify exits 1. That contradicts the direct.py:299-301 comment that verify 'already draws the same line'.

</details>

<a id="docs-readme-claude-d24"></a>

### D24 -- Stale '~212 s floor' in user-facing refusal messages and docstrings now that tied IR is the default

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan.py:121-128`, `tools/scan_roll.py:269-276`, `rps7200/direct.py:2503-2510`, `rps7200/direct.py:1996-1999`, `rps7200/direct.py:2518-2522`, `rps7200/direct.py:3390-3397`, `rps7200/demo.py:547-552`, `rps7200/session.py:3-4`, `rps7200/direct.py:1412-1417`

**Doc claim:** CLAUDE.md:394-400

CLAUDE.md says that reason 'has mostly gone': tied IR costs about 25 s at 300 dpi. Operators still read 212 s in every refusal and docstring, which overstates the cost by up to 8x.

**Evidence (from the code):**

```text
'so the pass would spend its ~212 s floor a frame'; auto_exposure: 'An infrared pass costs its own ~212 s floor however few lines are asked for'; session docstring: 'An infrared pass holds the device for its ~212 s floor'.
```

**Failure scenario:** An operator or Claude budgets a 300 dpi RGBI pass at 212 s from the error text instead of about 25 s.

**Fix:** Reword to 'costs an infrared pass (~25 s at 300 dpi tied, ~220 s untied)'.

<details><summary>Second reader's check</summary>

The '~212 s floor' wording remains in user-facing refusals (scan.py:124, scan_roll.py:272, direct.py:2506, 3395, demo.py:550, 635) and in docstrings (direct.py:1414, 1998, 2347, 2521; session.py:4), although tied infrared is the default and costs about 25 s at 300 dpi per estimate_seconds.

</details>

<a id="docs-readme-claude-d25"></a>

### D25 -- Several names, locations and 'nothing else' statements in CLAUDE.md, the Makefile and tasks.py do not match the code

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:1551-1600`, `rps7200/direction.py`, `rps7200/protocol.py:40`, `tools/gui.py:2077-2147`, `tools/gui.py:2723-2853`, `Makefile:50-53`, `tasks.py:150-154`, `rps7200/session.py:1305-1309`, `rps7200/session.py:1074-1083`

**Doc claim:** CLAUDE.md:117-119, 213-214, 436, 450; Makefile:50-52; tasks.py:151-152

CLAUDE.md contains several small inaccuracies: 'DirectScanner.decode_index (rps7200/direction.py)'; 'Bump PROTOCOL_REVISION in rps7200/direct.py'; on_scan_chosen as 'the sole submitter of a Roll'; library.corrected(entry) 'is what computes' the roll's frame*.tif and rolls/ prescans. The Makefile and tasks.py say opening the window asks who it is 'and nothing else', but it also sends READ_STATE (README does say so).

**Evidence (from the code):**

```text
`def decode_index` is a staticmethod in direct.py (direction.py has read_direction). `PROTOCOL_REVISION = 6` lives in protocol.py and direct.py only re-exports it. `on_roll` also calls `self.session.submit(Roll(` (gui.py:2147). session._run calls `self._report_position()` (READ_STATE) after inquiry. rolls/ frameNN.tif and prescanNN.tif are written by FrameWriter from scan()'s in-session correction, not by library.corrected.
```

**Failure scenario:** An editor bumps a constant in the wrong file, or assumes rolls/ files track today's correction code when they are frozen at scan time.

**Fix:** Fix the file references. Say on_scan_chosen is the sole submitter from the sheet. State that rolls/ files are corrected at scan time and only Save As, Export and the 1:1 view use library.corrected.

<details><summary>Second reader's check</summary>

decode_index is a staticmethod in direct.py:1551-1552. direction.py holds read_direction, encode_index and reverse_lines. PROTOCOL_REVISION = 6 is at protocol.py:40, and direct.py only imports and re-exports it. gui.py:2147 (on_roll) also submits Roll(...), besides 2853 in on_scan_chosen. rolls/ frameNN.tif and prescanNN.tif are written by FrameWriter from the scan-time corrected array, not by library.corrected. session._run calls _report_position (READ_STATE) after inquiry, contrary to 'nothing else' in Makefile:50-52 and tasks.py:151-152.

</details>

<a id="docs-readme-claude-d26"></a>

### D26 -- Contradictory statements about whether SET GAIN OFFSET persists

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2064-2067`, `rps7200/direct.py:3401-3405`, `rps7200/direct.py:3634-3643`, `tools/scan.py:74-81`

**Doc claim:** CLAUDE.md:393-394; README.md:415-417, 656-657

README and CLAUDE.md say it does not persist and that READ returns a fixed reference; a code comment in the metering loop asserts the opposite. The code works either way because it rewrites the base each round, but the documented fact is contradicted in the code that relies on it.

**Evidence (from the code):**

```text
auto_exposure: ':meth:`scan` multiplies whatever the device currently holds, and SET GAIN OFFSET persists, so re-reading it each round would compound the scales.' scan_roll: 'READ GAIN/OFFSET returns a fixed reference rather than a readback'. scan.py help: 'SET GAIN OFFSET does not persist across a scan sequence'.
```

**Failure scenario:** Someone 'simplifies' metering based on the auto_exposure comment and introduces compounding, or removes the per-round rewrite based on the docs.

**Fix:** Reconcile the comment with the measured behaviour, citing the capture evidence at direct.py:3634-3642.

<details><summary>Second reader's check</summary>

The auto_exposure comment (direct.py:2064-2066) says 'SET GAIN OFFSET persists, so re-reading it each round would compound'. The scan_roll comments (3401-3405, 3634-3642) and scan.py:78-79 say READ returns a fixed reference and SET does not persist. The code is safe either way because base is rewritten each round, but the comments contradict each other and the docs.

</details>

<a id="docs-readme-claude-d27"></a>

### D27 -- CLAUDE.md says EdgeWatch never reads 'when the sheet opens'; opening a stored roll loads it then

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `tools/gui.py:2645-2660`, `CLAUDE.md:474-477`

**Doc claim:** CLAUDE.md:476-477

CLAUDE.md:476-477 says EdgeWatch reads 'as prescans arrive, never when the sheet opens'. In fact, opening a stored walk calls edge_watch.load() just before the sheet opens. The reading runs in the background, so nothing blocks, but the sentence is literally false and could lead a maintainer to remove the load() call.

**Evidence (from the code):**

```text
`self.edge_watch.load([(r.number, r.image) for r in self.survey if r.image is not None], self._survey_film or self.v_film.get())` immediately before `self._open_sheet(...)`.
```

**Failure scenario:** A maintainer removes the load() call to satisfy CLAUDE.md, and reopened walks lose their proposals.

**Fix:** Reword CLAUDE.md to 'in the background, as prescans arrive or as a stored walk is loaded; never blocking the sheet'.

<details><summary>Second reader's check</summary>

gui.py:2655-2657 calls edge_watch.load(...) right before _open_sheet, so reading does start when a stored walk's sheet opens. It runs on EdgeWatch's own thread, so the property CLAUDE.md is protecting (the sheet is never blocked) holds. The mismatch is wording only, and README already describes it correctly.

</details>

<a id="docs-readme-claude-d28"></a>

### D28 -- filing_load_test.py does not do the counterbalanced measurement CLAUDE.md credits it with

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/filing_load_test.py:85-99`

**Doc claim:** CLAUDE.md:165-172

Every loaded pass follows a quiet one, so a monotonic warm-up or drift adds a constant bias to 'loaded' rather than cancelling. The test measures only a 300 dpi 8-bit RGB pass duration, gzipping a random 32 MB block. It never exercises long high-dpi reads or the open-and-idle case (D04). It is weak evidence for 'Run it before trusting the roll path unattended'.

**Evidence (from the code):**

```text
Each round: `q = one()` then `with Grinder(payload) as g: under_load = one()`, always quiet first. The pass is `s.scan(resolution=300, infrared=False, depth=DEPTH_8, ..., shading=False)`.
```

**Failure scenario:** The tool reports 'no measurable effect -- overlapping filing looks safe' from four 22 s passes, and a 3600 dpi RGBI roll is then trusted unattended on that basis.

**Fix:** Use an ABBA order, test at the resolutions rolls use, and include an idle-device gzip leg.

<details><summary>Second reader's check</summary>

Each round is `q = one()` then `with Grinder(payload): under_load = one()` (filing_load_test.py:88-91), always quiet first, so drift biases 'loaded'. The docstring's claim that alternating cancels warm-up is only partly true without ABBA ordering. The pass is 300 dpi, 8-bit, shading=False, so the test never covers high-dpi long reads or the open-idle gzip case from D04.

</details>

<a id="docs-readme-claude-d29"></a>

### D29 -- Rescans overwrite rolls/<roll>/frameNN.tif and prescanNN.tif; only output-folder copies get a suffix

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:2064-2071`, `rps7200/session.py:1817`, `rps7200/session.py:1889`, `rps7200/session.py:1074-1081`, `tools/scan_roll.py:499-500`, `tools/scan_roll.py:533`

**Doc claim:** README.md:378-380

README says a frame rescanned after a failure gets a suffix rather than overwriting the first attempt. That is true only for the operator's output folder. The roll's own frameNN.tif, prescanNN.tif and prescanNN-before.tif are overwritten, and for CLI dry runs prescanNN.tif is the only copy.

**Evidence (from the code):**

```text
`paths = [path] if path is not None else []`; only `self.out_dir` copies go through `_unclaimed(...)`. FrameWriter: `export.write(str(path), delivered, ...)` overwrites the roll-folder path.
```

**Failure scenario:** The operator re-walks a strip into the same roll folder and the first walk's prescans (not in the library for CLI walks) are silently replaced.

**Fix:** Apply _unclaimed to roll-folder writes too, or document the difference.

<details><summary>Second reader's check</summary>

session._file builds `paths = [path] if path is not None else []` and applies _unclaimed only to out_dir copies (2067-2071). FrameWriter's export.write overwrites the roll-folder path. scan_roll.py writes prescanNN.tif and frameNN.tif by fixed name (499-500, 533).

</details>

<a id="docs-readme-claude-d30"></a>

### D30 -- Capture and command counts differ across README, CLAUDE.md, the docs and tools, and cannot be checked here

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:568`, `README.md:607`, `CLAUDE.md:383`, `CLAUDE.md:495-500`, `tools/transport_truth.py:30`, `docs/whole-roll-plan.md:464`, `rps7200/direct.py:2488`

**Doc claim:** README.md:568, 607; CLAUDE.md:383, 495-500

Six, seven and nine captures, and 3,955, 3,987 and 8,133 commands, are all quoted. captures/ is gitignored and absent, so none of these can be checked. tests/test_usbpcap.py only asserts total > 3000.

**Evidence (from the code):**

```text
README: '8,133 vendor SCSI commands across nine captures', 'all nine were taken with film in'; CLAUDE.md: '3,955 commands across all six captures' and '3,987 commands parsed'; docs/whole-roll-plan.md: '3,955 commands across seven captures'.
```

**Failure scenario:** A safety claim ('CyberView sends 0xD2 zero times') is cited with a count that does not match the set actually parsed.

**Fix:** Quote one number, generated by tools/verify_capture.py, and state which captures it covers.

<details><summary>Second reader's check</summary>

The counts are inconsistent across the docs: README:607 says 8,133 commands across nine captures, CLAUDE.md:383 says 3,955 across six, CLAUDE.md:499 says 3,987, and docs/whole-roll-plan.md:464 says 3,955 across seven. captures/ is absent from this checkout, so none of these can be checked here.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Scanner raw bytes for a pass | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/raw.bin.gz | gzip (level 6) of the concatenated READ payloads, INDEX format: per line a 2-byte tag ('R','G','B','I') plus bytes_per_line little-endian samples; layout recorded in scan.json raw.layout | raw (as received) | library.save (library.py:186-211) from raw= bytes or a streamed raw_path spool (DirectScanner._debug_flush) | library.read_raw, reconstruct, decode_raw, migrate_direction, verify (sha256), demo._decode | Byte-exact and checksummed when present, but only when the pass ran with keep_raw=True. It is missing for debug scans with keep_raw=False, and can hold ANOTHER pass's bytes in debug mode (stale last_raw, D02). Missing for dry-run prescans from scan_roll.py and for prescans riding with roll frames (D07). |
| Decoded image of the pass | library/<id>/scan.tif | TIFF, uint16 (uint8 for prescans), RGB or RGBI (ExtraSamples=unspecified), deflate+predictor if tifffile is installed, else uncompressed | Meant to be the raw decode, upright (rows reversed if read bottom-up). Actually SHADING-CORRECTED for GUI single Scan jobs, unlabelled (D01). At 7200 dpi it is stagger-realigned with 4 rows trimmed, unlabelled (D09). Legacy entries may be corrected and labelled. | library.save; tools/library.py migrate-raw --write; library.migrate_direction(write=True) | library.load/corrected (re-applies shading unless labelled), reconstruct, verify (sha256), GUI 1:1 view and Save As, demo fallback | Lossless pixels; the image sha256 is checksummed. Whether it equals the decode of raw.bin.gz depends on the path (see D01, D09). |
| Entry record | library/<id>/scan.json | JSON (indent 2, default=str): id, created, image{file,shape,dtype,channels,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{resolution_dpi,frame,width,height,depth,channels,channel_order,bytes_per_line,film,exposure_scale,exposure_metered,duration_s,protocol_revision,rotation,flipped,reversal,read_direction,carriage_state,fast_infrared,filter_offsets}, device_settings{exposure,gain,offset}, metering, registration (offsets in mm), calibration{shading,ccd_mask,pixels_per_line,light_mean,report,skipped}, prescan{file,read_direction,carriage_state}, film, tags, provenance{driver_commit,driver_dirty,versions,platform} | metadata | library.save (written last, non-atomic write_text); rewritten in place by migrate-raw and migrate-direction | library.entries/load/corrected/reconstruct/verify/signature/duplicates, demo, GUI, tools/* | Records what was sent (exposure after scaled(), gain, offset), but not the calibration-time settings, the calibration pass's mask or bytes, or a 7200 stagger flag. `default=str` stringifies any non-JSON value lossily. The debug path stores dict(meta) before callers add bracket or registration keys. |
| Shading reference used for this pass | library/<id>/shading.npz (and session cache calibration/shading.npz) | npz (savez_compressed): pixels_per_line, channels, dark_channels, ref<c>/dark<c> float64 per-column arrays, mean<c>/darkmean<c> float64 | host-derived (calculate_shading reduction of the calibration lines), not device bytes | library.save via ShadingReference.save; DirectScanner.save_shading / ensure_shading (overwrites calibration/shading.npz on every calibration) | library.load/corrected/reconstruct, DirectScanner.load_shading (--reuse, GUI 'Use the cached one'), demo._decode | Lossless for the derived arrays. The ~1.66 MB of raw calibration lines are discarded (D06), so the derivation cannot be re-run. |
| CCD mask for this pass | library/<id>/ccd_mask.bin | raw bytes from SCSI COPY (0x00 = used column, 0x70 = unused), pixels_per_line long | raw | library.save from capture_record()['ccd_mask'] (DirectScanner._ccd_mask of the last pass) | library.load/corrected/reconstruct, demo._decode | Exact for the filed pass. For a roll frame it is the frame scan's mask; the prescan's mask is not kept. |
| Framing prescan stored with a frame | library/<id>/prescan.tif | TIFF uint8 RGB | CORRECTED (prescan() applies shading), unlabelled; no raw bytes or mask of its own | library.save(prescan=...) from session._file (roll frames) and tools/scan_roll.py; may be turned upright by migrate-direction --write | demo (served as a prescan), library.migrate_direction, demo.picture_signature | Lossless pixels of a corrected image; cannot be re-decoded or re-corrected (D07). |
| Library index | library/index.json | JSON list summary (id, created, dpi, channels, film, frame, tags, corrected, notes) | derived | library.reindex after every save, delete, migrate or duplicates --delete (non-atomic; called from the FrameWriter thread and the GUI thread) | humans; nothing depends on it | derived, rebuildable |
| Debug spool | $TMPDIR/rps7200-debug-*/NNN-image.npy, NNN-raw.bin | numpy .npy of raw_pixels; raw bytes | raw (image) plus possibly stale raw bytes | DirectScanner._debug_capture during scan() when debug is on | DirectScanner._debug_flush at close(); files deleted after filing | Lost if the process dies before close(). Gzip happens after the transport closes, but only when DirectScanner owns the transport. |
| Roll and walk manifests | rolls/<roll>/survey.json (dry run), rolls/<roll>/roll.json (scan) | JSON: roll, numbering, dpi, infrared, meter, film, settings{...}, wanted, frames[{number,index,transport_position,registration (mm),error,done,exposure,gain,offset,prescan\|entry,file}] | metadata | ScanSession._roll (merges the earlier manifest, rewritten each frame); tools/scan_roll.py (fresh manifest, overwrites on resume, D10) | GUI Rolls table, open_roll, export (roll_exports), session.renumbered, scan_roll.py hold_from_walk, walked_prescans | Not in the library; deleted with the roll folder (D15); mm units. |
| Roll folder images | rolls/<roll>/frameNN.tif, prescanNN.tif, prescanNN-before.tif | TIFF (always), frames uint16 RGB/RGBI or mono, prescans uint8 | corrected at scan time; frames oriented and optionally mono; GUI prescans oriented by session rotation/flip | FrameWriter._write (session) and tools/scan_roll.py tiff.write / FrameWriter | GUI open_roll / sheet (prescanNN.tif re-read as references), scan_roll.py --approved, EdgeWatch | Overwritten on rescan (D29). For CLI walks prescanNN.tif is the only copy. prescanNN-before.tif is never filed in the library. |
| Operator approvals | rolls/<roll>/approved.json | JSON: roll, numbering, frames[{number, offset_mm, rotation, flipped, reference_entry, source}] | operator decision | ScannerGui._write_approved (from on_scan_chosen) | read_approved / open_roll | Offsets in mm, rounded to 4 dp; the param actually sent is not stored (D12). Non-atomic write. |
| Window settings | gui-settings.json in CWD (or $RPS7200_SETTINGS, --settings, demo/gui-settings.json) | JSON sections controls, film, output, window, presets, shortcuts, rolls, sheet | settings | settings.save (atomic .part then replace) | settings.load (a corrupt or missing file silently gives defaults) | A corrupt file is replaced on the next save, losing presets and contact-sheet decisions. |
| Delivered single-scan file (CLI) | --out (default ./scan.tif or .jpg) plus ./scan.json sidecar | TIFF/JPEG via export.write; JSON meta | corrected (shading), optionally mono or bracket-merged | tools/scan.py after the session | user or NegPy | Not the library record; an --out pointing into a library entry would overwrite its scan.tif and scan.json. |
| Comparison files | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif, previews/cmp_before.png, previews/cmp_after.png | TIFF uint16; PNG | 2_corrected is destripe(flat-file defects), NOT the shipped shading correction (D08) | tools/make_comparison.py | Stefan, by eye | Recomputed, not the delivered file. |
| Demo state | demo/library/*, demo/rolls/*, demo/calibration/*, demo/gui-settings.json, demo/pictures.npz | same as the real library; npz of picture signatures | same split as the real paths; demo prescans may carry mismatched capture data | ScanSession/FrameWriter under --demo; DemoScanner._sign_pictures | the demo window | The demo reads the real library/ and rolls/ (read-only) and writes only under demo/. |

**Second reader's corrections to this table:**

Claims checked: about 75 across README.md, CLAUDE.md, the Makefile and tasks.py. These hold in code: make targets, test-all/test-no-tifffile, the addopts 'not hardware' default, nine tests in test_hardware.py, the tools/library.py subcommands including migrate-raw/--write, param_for_mm as a staticmethod, session._open_scanner as the demo seam, propose_centred plus its test, BLUE_RGBI_HEADROOM 5.2 / UNMEASURED 11.0, MAX_CORRECTION_PARAM 87, COMMAND_UNITS 1.84, aperture 345.2 / 344.5 / FRAME_WIDTH_UNITS 350.6, PROTOCOL_REVISION 6, and FrameWriter's thread.

Corrections to the persisted-state table:
(1) Decoded image library/<id>/scan.tif. Corrected pixels are filed unlabelled for GUI single Scan jobs (D01) AND for every demo-filed entry (prescans, dry-run prescans, roll frames and scans; DX1). The 7200 dpi stagger trim applies only to CLI or script scans with shading=False; the GUI has no shading-off control and refuses 7200 with shading.
(2) Shading reference library/<id>/shading.npz. Demo prescans read from a stored prescan.tif can be filed with the reference and mask of the PREVIOUS demo decode, not their own.
(3) Debug spool. _debug_flush runs inside close() regardless of transport ownership. When a transport was passed in, it therefore gzips while the caller's transport is still open, not only 'after the transport closes'.
(4) .gitignore ignores rolls/ at line 45, not 52.
(5) Roll folder images. GUI dry-run prescans ARE filed in the library with raw_image=rf.raw_prescan. Only CLI (tools/scan_roll.py) walks leave prescanNN.tif as the sole copy. prescanNN-before.tif is never filed on either path.
(6) Raw bytes library/<id>/raw.bin.gz. In the session path a stale last_raw is caught only when lines, width or channels differ (session.py:2079-2098). The debug path (_debug_flush) has no guard at all, so same-shape stale bytes (byte14_probe's final pass) are filed silently.

## What the operator can do

- Run `make test`, `make test-all`, `make lint`, `make type`, `make fix`, `make all` and `make clean` on macOS, Linux or Windows with no scanner attached; `uv run pytest -m hardware` and `tools/check_scanner.py` only read INQUIRY and READ STATE.
- Scan a single frame with `tools/scan.py --dpi N [--ir]`: it calibrates or reuses a reference, scans, and files raw pixels, raw bytes, reference and mask in library/ after closing the device.
- Walk a strip from the GUI's dry run: every prescan is filed in the library with its raw bytes, plus survey.json and prescanNN.tif in rolls/.
- Re-derive stored scans offline with `tools/library.py reconstruct`, repair mislabelled entries with `migrate-raw --write`, and bring old entries' direction records up to date with `migrate-direction --write`.
- Export or Save As from the GUI; these re-correct from the library with today's apply_shading, provided scan.tif really is raw.
- Exercise the window with `make run-demo`, and `make run-sheet` when rolls/aligned-strip exists locally.
- Force-abort a hung pass after typing ABORT, accepting a power cycle.

## What the operator should not do

- Do not rely on RPS7200_DEBUG=1 to file passes made by tools/scan.py, tools/scan_roll.py or the GUI: they force debug=False, so metering probes, hold and aim prescans and pre-correction prescans are never filed.
- Do not call DirectScanner(debug=True).scan(...) without keep_raw=True on every pass; debug entries otherwise have no raw bytes or another pass's bytes.
- Do not use `--no-shading` with `tools/scan_roll.py`, or with `tools/scan.py --auto-exposure`, expecting no calibration: a lazy calibration runs inside the first prescan or probe, on the path documented as having stalled the device.
- Do not judge a change to the correction path from make_comparison.py's 2_corrected.tif: it is a destripe with a flat file, not the shipped shading correction.
- Do not resume a CLI roll with `tools/scan_roll.py --start-at N` into the same folder expecting roll.json to be kept; it is overwritten.
- Do not delete a roll folder walked by tools/scan_roll.py expecting the library to hold its prescans; it holds none.
- Do not trust Save As, Save all or the 1:1 view of a GUI single scan until `tools/library.py migrate-raw --write` has rewritten its scan.tif to raw; it is corrected twice.
- Do not type `--mono G`; use `--mono-channel G`.
- Do not press Return in the 'Calibrate first' prompt unless the film is loaded; it starts a 3-4 minute calibration.
- Do not size manual SLIDE moves from README's `0.1057 × param + 0.1662 mm` law; the code uses param + 1.84 units (0.1945 mm).

## Mistakes nothing guards against

- Pressing Scan in the GUI files shading-corrected pixels labelled as raw, and every later full-resolution view or Save As of that entry is corrected a second time, with no warning.
- The Scan button submits without confirmation and ScanSession.submit does not check busy, so repeated clicks queue several multi-minute scans.
- Cmd+Return with no calibration, followed by Return, starts a calibration whatever is in the transport.
- `tools/scan_roll.py --start-at N --roll <same>` silently replaces roll.json, dropping the earlier frames from the Rolls table and from Export.
- `tools/scan_roll.py --dry-run` files no library entries, so the raw bytes of every survey prescan are discarded.
- The roll Delete dialog says only approved.json is lost; manifests, CLI prescans and prescanNN-before.tif also go, and an approved.json holding only positions is not counted.
- Removing a pass in the GUI and answering 'No' to 'Keep the library entry?' runs rmtree on the raw bytes permanently.
- `tools/scan.py --bracket N --exposure-scale X` with a single X silently brackets from 1.0 and ignores X.
- `tools/scan.py --out` pointed into a library entry overwrites that entry's scan.tif and scan.json.
- A corrupt gui-settings.json opens on defaults and is overwritten on the next save, losing presets, shortcuts and contact-sheet decisions.
- Any 7200 dpi entry makes `make reconstruct` report 'decode CHANGED' and exit 1 on every run.
- A `--no-shading` scan makes `make verify` exit 1 with 'correction was asked for'.

## Dataflow notes

**Data coming in.**
- USB bulk data arrives through Transport._read_payload (usb_transport.py:717-770), which windows the length handshake and raises NoDataYet only when nothing has arrived.
- DirectScanner.read_lines (direct.py:1398) and read_planes (direct.py:1440-1542) pace the reads and join the chunks into `blob`.
- `blob` is kept as last_raw / last_raw_layout ONLY if keep_raw (1518-1532); nothing ever clears it, so it can go stale.
- decode_index (1551-1600) splits lines by tag, truncates to the common height, and reverses rows when direction.read_direction says the pass was read bottom-up.
- In scan() (2423-2824):
  - at 7200 dpi, _realign_native_column_stagger trims 4 rows (2695-2701), and the result is `raw_pixels` (2708);
  - the per-pass CCD mask is read by SCSI COPY (2667) and stored as _ccd_mask;
  - apply_shading(image, self._shading, ccd_mask) (2733; shading.py:196-283) produces the corrected image that is returned.
- The meta (2760-2804) records what was sent (exposure after Settings.scaled, gain, offset), the geometry, protocol_revision, read_direction, carriage_state (the READ STATE before the pass), fast_infrared, the shading report or shading_skipped, and metering (auto_exposure's probes, 2218-2230).
- Side outputs of scan():
  - _debug_capture (650-692) spools raw_pixels plus capture_record(), whose raw bytes may be stale;
  - last_pixels_raw and last_scan_meta (2818-2823).

**Calibration.**
- calibrate_shading (1755-1973) reads 4-line blocks of 16-bit lines over CALIBRATION_FRAME.
- calculate_shading (shading.py:109-182) reduces them to per-column light and dark means.
- The raw lines are discarded unless keep_data.
- ensure_shading (572-629) saves the reduction to calibration/shading.npz.

**Data going out.**
- **tools/scan.py (176-246):** hold() captures capture_record() and last_pixels_raw while the device is open; library.save runs after the `with` block closes the device. export.write(out) writes the corrected (optionally mono or merged) delivered file plus a .json sidecar.
- **GUI via ScanSession:**
  - _prescan (1401-1438) and _roll (1608-1941) pass raw_image=last_pixels_raw / rf.raw_image / rf.raw_prescan (the last only for dry runs); _scan (1440-1458) passes no raw_image.
  - _file (2031-2141) snapshots capture_record() and drops raw bytes whose layout disagrees with the image shape.
  - FrameWriter._write (1060-1103), on its own thread started at session open (1311), writes oriented/mono delivered copies to rolls/<roll>/frameNN.tif / prescanNN.tif and out_dir (via _unclaimed). It then calls library.save(raw_image or the corrected image, **capture) immediately, including for single jobs, with the device open and idle.
- **tools/scan_roll.py (455-585):** frames go through FrameWriter with raw_image=frame.raw_image; dry-run prescans are only tiff.write'd to rolls/.
- **library.save (library.py:131-311):**
  - writes scan.tif (tiff.write: deflate if tifffile is installed), prescan.tif (corrected), shading.npz (the derived reduction), ccd_mask.bin, raw.bin.gz (gzip of bytes or the streamed spool) and scan.json (record, default=str);
  - then reindexes;
  - never checks that the raw bytes decode to `image`.

**Reading it back.**
- library.corrected (338-391) re-applies apply_shading with the stored reference and mask unless corrections_applied contains 'shading' or calibration.skipped is set. It is used by the GUI 1:1 view (gui.py:4110), Save As / Export (3864) and the analysis tools.
- reconstruct (439-515) and decode_raw (411-436) re-run decode_index only, with no stagger realignment and no re-derivation of the shading reference.
- The demo (demo.py:955-1022) re-decodes raw.bin.gz plus apply_shading for scans, but serves the stored corrected prescan.tif for prescans.

**Transport distances.**
- The sheet/adjuster offsets (mm, Approved.offset_mm, approved.json) go through session.plan_nudges (672-717), DirectScanner.param_for_mm (3126-3136) and nudge (3138-3170) as SLIDE `00|01 <param> 00 04`.
- say_units (protocol.py:295-298) converts mm to units for display only. Persisted records stay in mm.
