# Operator CLI tools and build

Area key `cli-operator-tools`. 34 findings: 2 critical, 7 high, 12 medium, 12 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Operator CLI and build surface: tools/scan.py (single scan or bracket), tools/scan_roll.py (walk or roll through the shared session.FrameWriter), tools/library.py (list/verify/reconstruct/duplicates/migrate-*), tools/make_comparison.py, tools/filing_load_test.py, tasks.py + Makefile, pyproject.toml, packaging/60-rps7200.rules, .github/workflows/test.yml. I judged everything from the executable path and followed calls into rps7200/direct.py, session.py, library.py, export.py and shading.py.


The main scan passes are filed raw, byte-exact and after close() in scan.py. In scan_roll they are filed on a writer thread. Four things go wrong around them:

(a) Interrupts and exceptions lose data. scan.py keeps every pass in RAM and files only after the `with` block ends. scan_roll only catches `Exception`, so on Ctrl-C the daemon writer thread dies with frames still queued. Both cases also abandon the read mid-scan, which is the wedge hazard.

(b) Many passes never reach the library. `debug=False` is hard-coded, so RPS7200_DEBUG has no effect in these tools. Never filed: metering probes, every prescan of a CLI dry-run walk, hold/aim verification prescans, and the calibration pass's raw bytes (only a derived ShadingReference is kept).

(c) Several library-side records are lossy:
  - library.save drops `inquiry`, the bracket_* keys and roll_index/roll_position.
  - Roll frames claim `exposure_metered: false` and carry no metering evidence.
  - 7200 dpi scan.tif has a host-side stagger realignment baked in, which reconstruct does not replay.
  - prescan.tif is stored corrected, with no raw and no label.
(d) The tools' own claims are wrong in several places:
  - Resuming with --start-at overwrites roll.json.
  - scan_roll --no-shading does not skip calibration. It triggers the lazy in-prescan calibration the tool's own comment says stalls the device.
  - `duplicates --delete` will treat scans of different pictures as duplicates.
  - filing_load_test's verdict can mathematically never say "unsafe".
  - make_comparison evaluates a destripe correction that no production path uses.
Argument validation is mostly done after the device has been opened and calibrated. The build and CI files are sound; the only notes there are small comment and doc drift.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [CLI-01](#cli-operator-tools-cli-01) | critical | data-integrity | scan_roll.py: Ctrl-C / SIGTERM skips writer.finish(); queued frames die with the daemon writer, and the read is abandoned mid-scan | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [CLI-02](#cli-operator-tools-cli-02) | critical | data-integrity | scan.py files nothing until after the session: any exception or Ctrl-C mid-bracket loses every pass already scanned | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [CLI-03](#cli-operator-tools-cli-03) | high | data-integrity | CLI tools never file walk prescans, metering probes, hold/aim prescans or verification passes; debug=False hard-coded, so RPS7200_DEBUG is ignored | [P08](../problems/P08-passes-never-filed.md) |
| [CLI-04](#cli-operator-tools-cli-04) | high | doc-mismatch | Resuming a roll with --start-at overwrites roll.json; docs say it resumes from and carries forward the manifest | [P19](../problems/P19-roll-manifests.md) |
| [CLI-05](#cli-operator-tools-cli-05) | high | hardware-safety | scan_roll.py --no-shading does not skip calibration: it moves it into the lazy in-prescan path the tool says stalls the device, and frames are still corrected | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [CLI-06](#cli-operator-tools-cli-06) | high | data-integrity | `library.py duplicates --delete` treats scans of different pictures as duplicates when film notes are empty or same-day default roll names collide | [P11](../problems/P11-duplicates-delete-destroys-scans.md) |
| [CLI-08](#cli-operator-tools-cli-08) | high | design | make_comparison.py judges a destripe correction the pipeline never uses, on an arbitrary TIFF, under names that claim raw and corrected | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [CLI-09](#cli-operator-tools-cli-09) | high | data-integrity | The calibration pass's raw bytes are discarded; only a derived ShadingReference is stored, and --reuse leaves no record that the reference was stale | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [CLI-10](#cli-operator-tools-cli-10) | high | data-integrity | 7200 dpi scan.tif has a host-side stagger realignment baked in that reconstruct/decode_raw never replay; every 7200 dpi entry reads 'decode CHANGED' | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [CLI-07](#cli-operator-tools-cli-07) | medium | bug | filing_load_test.py's verdict is a tautology: it reports 'safe' whenever passes vary at all, so the measurement CLAUDE.md relies on cannot fail | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [CLI-11](#cli-operator-tools-cli-11) | medium | data-integrity | library.save silently drops the INQUIRY every CLI caller passes, plus bracket_* and roll_index/roll_position, so brackets cannot be regrouped from the library | [P09](../problems/P09-record-missing-parameters.md) |
| [CLI-12](#cli-operator-tools-cli-12) | medium | data-integrity | Roll frames are filed as exposure_metered=false with no metering evidence even when every frame was metered | [P09](../problems/P09-record-missing-parameters.md) |
| [CLI-13](#cli-operator-tools-cli-13) | medium | demo-divergence | --approved diverges from the window: ignores the walk's prescan dpi, does not un-orient GUI-walk prescans, and ignores approved.json | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [CLI-14](#cli-operator-tools-cli-14) | medium | user-error | Arguments are validated only after the device is opened, calibrated and metered; any integer --dpi is accepted | [P16](../problems/P16-timeouts-and-runtime-budget.md) |
| [CLI-15](#cli-operator-tools-cli-15) | medium | hardware-safety | scan.py calibrates straight after INQUIRY, with no check or question about film in the transport | [P17](../problems/P17-calibration-without-asking.md) |
| [CLI-16](#cli-operator-tools-cli-16) | medium | design | scan.py holds every pass (raw pixels, raw bytes and corrected frame) in RAM; high-dpi brackets need several to 10+ GB | [P29](../problems/P29-bracket-merge.md) |
| [CLI-17](#cli-operator-tools-cli-17) | medium | bug | `library.py reconstruct` counts non-regressions as changed decodes and aborts entirely on one missing scan.tif | [P12](../problems/P12-library-maintenance-tools.md) |
| [CLI-18](#cli-operator-tools-cli-18) | medium | data-integrity | `library.py verify` cannot see half-written entries or prescan.tif; says 'library is intact' after an interrupted save | [P10](../problems/P10-non-atomic-writes.md) |
| [CLI-19](#cli-operator-tools-cli-19) | medium | data-integrity | Roll entries store prescan.tif as corrected 8-bit pixels with no raw bytes, no checksum and no 'corrected' label; the CLI drops raw_prescan | [P08](../problems/P08-passes-never-filed.md) |
| [CLI-A1](#cli-operator-tools-cli-a1) | medium | hardware-safety | A calibration that yields no reference prints 'scans will be raw', then the next scan silently recalibrates on the lazy in-scan path | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [CLI-A2](#cli-operator-tools-cli-a2) | medium | user-error | Neither CLI tool estimates its runtime or warns before a run that will outlive the 10-minute foreground kill | [P16](../problems/P16-timeouts-and-runtime-budget.md) |
| [CLI-20](#cli-operator-tools-cli-20) | low | user-error | `scan.py --library ''` files into the current directory, while scan_roll documents `--library ''` as 'skip'; all data paths are CWD-relative | -- |
| [CLI-21](#cli-operator-tools-cli-21) | low | doc-mismatch | Operator-facing distances are still in millimetres, with a stale single-command limit comment and a stale README formula | -- |
| [CLI-22](#cli-operator-tools-cli-22) | low | doc-mismatch | Dry-run help and docstring promise a 2.5-minute walk, but a dry run now calibrates first (3-4 minutes) | -- |
| [CLI-23](#cli-operator-tools-cli-23) | low | user-error | A roll that ends early on a blank frame or a stalled transport exits 0 with fewer frames than --frames asked; the default --start-at 1 silently rewinds | -- |
| [CLI-24](#cli-operator-tools-cli-24) | low | data-integrity | The roll manifest omits settings needed to understand or resume it (fast_ir, nudge, correct, reuse, no_shading, stock) | -- |
| [CLI-25](#cli-operator-tools-cli-25) | low | user-error | scan.py: a single --exposure-scale value is dropped for a bracket and also disables --auto-exposure | -- |
| [CLI-26](#cli-operator-tools-cli-26) | low | error-handling | Manifest and migration writes are in place and non-atomic; migrate-raw holds every decoded image in RAM; migrate-direction exits 0 on errors | -- |
| [CLI-27](#cli-operator-tools-cli-27) | low | doc-mismatch | Makefile/tasks comments overstate: the GUI also sends READ_STATE at launch, and run-sheet needs a gitignored folder | -- |
| [CLI-28](#cli-operator-tools-cli-28) | low | hardware-safety | A mid-scan read failure is caught per frame and the roll keeps commanding the transport | -- |
| [CLI-30](#cli-operator-tools-cli-30) | low | test-gap | Test gaps: no test of Ctrl-C, resume, duplicates identity or reconstruct classification; type checking excludes all of tools/ | -- |
| [CLI-A3](#cli-operator-tools-cli-a3) | low | design | scan_roll.py --dry-run writes prescan TIFFs on the scanning thread with the device open; the window routes the same writes through FrameWriter | -- |
| [CLI-A4](#cli-operator-tools-cli-a4) | low | doc-mismatch | tests/test_gui.py docstring claims scan.py and scan_roll.py write the 1_/2_/3_ comparison files; neither does | -- |
| [CLI-29](#cli-operator-tools-cli-29) | info | design | The operator tools never send the vendor session opening (0xE7 ...) that every hardware probe sends | -- |

## Findings in full

<a id="cli-operator-tools-cli-01"></a>

### CLI-01 -- scan_roll.py: Ctrl-C / SIGTERM skips writer.finish(); queued frames die with the daemon writer, and the read is abandoned mid-scan

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/scan_roll.py:352-353`, `tools/scan_roll.py:587-605`, `rps7200/session.py:1043`, `rps7200/session.py:1108-1111`, `rps7200/direct.py:2677-2685`, `rps7200/direct.py:777-788`

KeyboardInterrupt and SystemExit are BaseException, not Exception, so they propagate past the handler and writer.finish() never runs. FrameWriter's thread is daemon=True, so interpreter shutdown kills it. The queue (depth 2) plus the frame being written are lost: no frameNN.tif, no library entry, and possibly a half-written entry directory with scan.tif/raw.bin.gz but no scan.json. In the same unwind, `with DirectScanner` closes the transport while read_planes is mid-scan. That is the 'abandoned read' CLAUDE.md names as a wedge cause. There is no SIGINT/SIGTERM handling anywhere in rps7200/ or the operator tools. A plain SIGTERM (harness 10-minute kill, closed terminal) skips even the close(). The comment claiming a `finally` is false.

**Evidence (from the code):**

```text
scan_roll.py:587 `except Exception as exc:  # noqa: BLE001` then 605 `writer.finish()`; comment 597-598: "Reached through a `finally` around the whole scanning block, so it runs whatever came out of it." session.py:1043 `self._thread = threading.Thread(target=self._run, daemon=True)`. direct.py:2679 `except BaseException: ... self._scanning = False; raise`. direct.py:777 close(): `if self._own_transport: self.t.close()` (no drain).
```

**Failure scenario:** A 36-frame roll at 3600 dpi RGBI is on frame 12 and the operator presses Ctrl-C. Frames 10-11 are still queued or gzipping, and frame 12's read is in progress. Result: frames 10-11 are gone from both rolls/ and library/, roll.json lists them with entry=null, one orphan directory without scan.json remains, and the scanner needs a power cycle.

**Fix:** Turn the handler into `try: ... except BaseException as exc: trouble = exc; ... finally: writer.finish()`, or catch KeyboardInterrupt explicitly, re-raise after finishing, and exit non-zero. Install a SIGINT handler that sets should_stop, so the roll stops cooperatively between frames (scan_roll already supports `should_stop`) instead of mid-read. Make the FrameWriter thread non-daemon, or join it in an atexit hook. Correct the comment. Add a test that raises KeyboardInterrupt from the fake generator and asserts the queued frames are filed.

<details><summary>Second reader's check</summary>

The handler at tools/scan_roll.py:587 is `except Exception`, and no `finally` exists anywhere in main(). That makes the comments at 347 ('Wrapped so writer.finish() below runs whatever comes out of this') and 598 ('Reached through a `finally`') false. KeyboardInterrupt and SystemExit therefore skip writer.finish() at 605. FrameWriter's thread is `daemon=True` (session.py:1043), so it is killed at interpreter exit: queued jobs are lost, and a library.save interrupted mid-way leaves a directory without scan.json. direct.py:2679 re-raises BaseException after clearing `_scanning`, and `__exit__` then close()s the transport mid-read. Neither tool installs a signal handler (grep finds no signal/atexit), and scan_roll.py never passes `should_stop`, so Ctrl-C is the only stop the CLI has. A default SIGTERM terminates without running `__exit__` at all.

</details>

<a id="cli-operator-tools-cli-02"></a>

### CLI-02 -- scan.py files nothing until after the session: any exception or Ctrl-C mid-bracket loses every pass already scanned

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/scan.py:174-194`, `tools/scan.py:205-235`, `tools/scan.py:237-246`

Each pass's raw bytes, raw pixels, reference and mask are held in a Python list and written only after the `with DirectScanner` block exits normally. If scan_bracket raises part way, the exception propagates past the filing loop and every completed pass is discarded. Triggers include ScanReadError, UsbError, ShadingUnavailable, MemoryError, KeyboardInterrupt or a harness kill. That is exactly the data the on_pass hook was added to rescue ('Only one pass's raw bytes survive on the scanner'). A library.save failure on pass k (disk full) also aborts passes k+1..N and the delivered file.

**Evidence (from the code):**

```text
scan.py:174 `pending: list[dict] = []`; hold() only does `pending.append(dict(capture, inquiry=info, meta=meta, image=image if raw is None else raw))`; filing happens only after the with block: 237-246 `for held in pending: entries.append(library.save(...))`. No try/finally around the scan or the filing.
```

**Failure scenario:** `tools/scan.py --dpi 3600 --bracket 9 --ir` fails on pass 7 with a USB error or a Ctrl-C. About 15 minutes of passes 1-6 were taken and captured in `pending`, but nothing reaches library/. The process exits with a traceback, and the device may need a power cycle.

**Fix:** Wrap the scanning block in try/finally. Close the device first, then file whatever is in `pending`, then re-raise. For large passes, spool each pass to a temp file in on_pass (as `_debug_capture` does) instead of holding it in RAM. File each pending entry independently so one failure does not drop the rest.

<details><summary>Second reader's check</summary>

tools/scan.py:160-235 has no try/finally. `pending` is filed only in the loop at 237-246, after the `with` block exits normally. Any exception out of scan_bracket or scan() propagates past it, including ScanReadError, UsbError, ShadingUnavailable, ValueError from bracket_ladder, MemoryError and KeyboardInterrupt, and every captured pass is dropped. The filing loop has no per-entry guard either, so one library.save failure aborts the remaining passes and the delivered file.

</details>

<a id="cli-operator-tools-cli-03"></a>

### CLI-03 -- CLI tools never file walk prescans, metering probes, hold/aim prescans or verification passes; debug=False hard-coded, so RPS7200_DEBUG is ignored

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan.py:157-160`, `tools/scan_roll.py:344-353`, `tools/scan_roll.py:492-510`, `rps7200/direct.py:2083-2092`, `rps7200/session.py:1798-1826`

**Doc claim:** CLAUDE.md:104 'File every scan in the library, with its raw bytes'; CLAUDE.md:143 'Either works; the environment variable is better because a script inherits it'; README.md:185 'Every scan is filed in `library/` by default'; README.md:488-489

An explicit `debug=False` overrides the environment variable (direct.py:464-469 only reads the env when debug is None). The only passes these tools file are the ones they file themselves: the main scan, the bracket passes, and roll frames. Everything else the scanner produced in a CLI session is discarded:
- metering probes (RGB, two rounds per frame with `--meter each`)
- every prescan of a `--dry-run` walk, kept only as a corrected 8-bit prescanNN.tif with no raw bytes, no shading reference and no mask
- `prescanNN-before.tif`
- the extra verification prescans of `_hold_to_approved`/`_aim_frame`
- the calibration pass
The window's dry run files each prescan with its raw pixels. The same walk from the CLI leaves no library trace, and its corrected prescans are later used as `--approved` references.

**Evidence (from the code):**

```text
scan.py:160 and scan_roll.py:353 `DirectScanner(verbose=args.verbose, debug=False)`. Dry run, scan_roll.py:499-500: `pre = out / f"prescan{number:02d}.tif"; tiff.write(str(pre), frame.prescan)`, with no library call and frame.raw_prescan unused. direct.py:2086-2092 probe comment: "CLAUDE.md's rule is 'file every scan, with its raw bytes' ... keep_raw=True". The GUI files the same dry-run prescans: session.py:1812 `self._file(seq, number, rf.prescan, ... kind="prescan", raw_image=rf.raw_prescan, path=surveyed, ...)`.
```

**Failure scenario:** The operator (or Claude, following CLAUDE.md's 'RPS7200_DEBUG=1 ... Always') walks a 38-frame strip with `RPS7200_DEBUG=1 tools/scan_roll.py --dry-run`. The library gains zero entries. A later change to the decode, direction or shading code cannot be re-run on any of those prescans. Once a second walk into rolls/<date> overwrites the folder, they are gone.

**Fix:** File dry-run prescans (and prescan_before) through FrameWriter with raw_image=frame.raw_prescan and capture_record(), as the GUI does. Either honour RPS7200_DEBUG for passes the tool does not file itself, or add a per-pass hook for probes and verification passes. As a minimum, change the docs to say which passes are not filed.

<details><summary>Second reader's check</summary>

Both tools construct `DirectScanner(verbose=..., debug=False)` (scan.py:160, scan_roll.py:353). The environment variable is consulted only when `debug is None` (direct.py:464-469), and _debug_capture returns immediately when debug is off (direct.py:664). The dry-run branch (scan_roll.py:498-510) writes prescanNN.tif and prescanNN-before.tif with tiff.write and never touches the library; frame.raw_prescan is discarded. The window's dry run files each prescan through `self._file(..., raw_image=rf.raw_prescan)` (session.py:1818-1830). Metering probes set keep_raw=True (direct.py:2090) but are filed only by debug filing, which these tools force off. Neither doc names these exceptions.

</details>

<a id="cli-operator-tools-cli-04"></a>

### CLI-04 -- Resuming a roll with --start-at overwrites roll.json; docs say it resumes from and carries forward the manifest

**Severity** high · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P19](../problems/P19-roll-manifests.md)

**Where:** `tools/scan_roll.py:22-24`, `tools/scan_roll.py:291-329`, `tools/scan_roll.py:451-452`, `tools/scan_roll.py:634`

**Doc claim:** tools/scan_roll.py:22-24; README.md:547-549 'a resumed roll carries forward what the earlier session already did instead of overwriting its manifest'

Every non-dry-run invocation builds a fresh manifest and writes it over `<out>/roll.json` as soon as calibration succeeds. So `--start-at N` after a crash replaces the record of frames 1..N-1: entry paths, registration, errors, transport positions and exposures. Those records are the only durable per-frame registration history outside the library, and roll_index/roll_position are not in library entries (see CLI-11). The same overwrite happens when a second roll is started on the same day under the default `--roll` (today's date), and with `--frames -1`, which calibrates and then writes an empty manifest. The frameNN.tif files of the first roll are also overwritten frame by frame. The GUI's `_roll` replaces per-frame records inside the existing manifest instead (session.py:1908-1911).

**Evidence (from the code):**

```text
Docstring 22-24: "a crash two hours in should cost the frame it was on, not the roll -- `--start-at` resumes from the manifest." Code 298: `manifest = {... "frames": [], }`; 326-329 `def checkpoint(): manifest_path.write_text(json.dumps(manifest, ...))`; 452 `checkpoint()`. Nothing reads an existing roll.json (only hold_from_walk reads survey.json/roll.json, for --approved).
```

**Failure scenario:** The roll crashes at frame 20. The operator runs `scan_roll.py --start-at 20 --roll 2026-09-23 ...` as the tool itself advises ('resume a failed picture with --start-at N'). roll.json now lists only frames 20+, and the manifest of frames 1-19 is lost.

**Fix:** When roll.json exists, load it. Keep records for other frame numbers, replace records for re-scanned numbers, and append a 'runs' list with each invocation's settings. Refuse or require --force when the existing manifest's settings disagree (dpi, IR, film). Write the file atomically (temp file plus os.replace).

<details><summary>Second reader's check</summary>

`manifest` is always built fresh with `"frames": []` (scan_roll.py:298-324) and written over `<out>/roll.json` by checkpoint() at 452, as soon as calibration succeeds. Nothing in the tool reads an existing roll.json except hold_from_walk, and only for --approved. The docstring at 22-24 ('--start-at resumes from the manifest') and README.md:547-549 ('carries forward what the earlier session already did instead of overwriting its manifest') are both contradicted. The window's roll replaces per-frame records instead (session.py:1895-1915).

</details>

<a id="cli-operator-tools-cli-05"></a>

### CLI-05 -- scan_roll.py --no-shading does not skip calibration: it moves it into the lazy in-prescan path the tool says stalls the device, and frames are still corrected

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `tools/scan_roll.py:145-146`, `tools/scan_roll.py:166-173`, `tools/scan_roll.py:433-445`, `tools/scan_roll.py:455-477`, `rps7200/direct.py:3494-3496`, `rps7200/direct.py:3644-3652`, `rps7200/direct.py:2576-2594`

**Doc claim:** tools/scan_roll.py:146 help 'skip calibration entirely; scans come back striped'

The flag only suppresses the up-front ensure_shading call. The first prescan then finds `self._shading is None` and calibrates lazily inside scan(), which is the path the file documents as having stalled the device twice. After that, prescans and frames are shading-corrected anyway, so the flag's stated effect never happens. scan.py `--no-shading --auto-exposure` reaches the same lazy path: auto_exposure's probes call scan() with shading=True.

**Evidence (from the code):**

```text
Help: `"--no-shading", ... help="skip calibration entirely; scans come back striped"`. calibrate() passes `skip=args.no_shading`, but s.scan_roll(...) takes no shading argument. The roll's `self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)` uses `shading=True`, and its `self.scan(resolution=..., frame=window, exposure_scale=scales, film=film, keep_raw=keep_raw, fast_infrared=fast_infrared)` defaults to shading=True. scan() then does `self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`. The tool's own comment at 436-440: "that lazy calibration stalls: `bulk read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE`, right after the shading descriptor, and the device stops answering."
```

**Failure scenario:** The operator wants a raw comparison roll and runs `scan_roll.py --no-shading --dry-run`. The tool skips the safe calibration, starts the first prescan, calibrates lazily, hits LIBUSB_ERROR_PIPE and the scanner stops answering (per the comment's own measurement). If it survives, every frame is corrected anyway.

**Fix:** Plumb `shading` through DirectScanner.scan_roll into prescan() and scan(), and file with shading_skipped. Alternatively, refuse --no-shading in scan_roll.py. Under scan.py, make auto_exposure probes honour shading=False, or refuse the --no-shading + --auto-exposure combination.

<details><summary>Second reader's check</summary>

`--no-shading` reaches only `ensure_shading(skip=True)` (scan_roll.py:172-173). DirectScanner.scan_roll takes no shading argument. Its prescan() hard-codes `shading=True` (direct.py:1705), and its frame scan() call (direct.py:3644-3652) leaves the default `shading=True`. The first prescan therefore finds `self._shading is None` and runs calibrate_shading() inside scan() (direct.py:2582-2594). That is the lazy path the tool's own comment (scan_roll.py:433-441) says stalled with LIBUSB_ERROR_PIPE. All frames are then corrected anyway, so the help text 'scans come back striped' is false. scan.py `--no-shading --auto-exposure` hits the same path, because auto_exposure's probes call scan() with the default shading=True (direct.py:2082-2091).

</details>

<a id="cli-operator-tools-cli-06"></a>

### CLI-06 -- `library.py duplicates --delete` treats scans of different pictures as duplicates when film notes are empty or same-day default roll names collide

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P11](../problems/P11-duplicates-delete-destroys-scans.md)

**Where:** `rps7200/library.py:639-699`, `rps7200/library.py:714-738`, `tools/library.py:115-135`, `tools/scan.py:112-116`, `tools/scan_roll.py:291`, `tools/scan_roll.py:570-578`, `rps7200/direct.py:747-750`

**Doc claim:** tools/library.py:15-18 docstring; rps7200/library.py:642-646 signature docstring

The docstring promises 'the same scan of the same picture ... neither holds anything the other does not', but the signature cannot see the picture. The following all share one signature:
- any two scan.py scans at the same settings with no --stock/--frame, of different film frames
- any two debug-filed scans at the same settings
- frame N of two different strips rolled on the same day under the default --roll
`duplicates --delete` then removes all but the 'most useful' entry: raw bytes, reference and mask, irreversibly. Even true repeats of one frame are independent noise realisations. The measure-scan-quality workflow needs exactly those (random/fixed split from a repeat pair), and `--keep 1` is the default.

**Evidence (from the code):**

```text
signature() identifies the picture only by `(film.get("stock") or "")...`, `(film.get("frame") or "")...` and `(film.get("subject") or "")...` plus dpi/channels/frame/depth/film/protocol/commanded exposure/fast_ir. scan.py defaults: `--stock default=""`, `--frame default=""`, `--subject default=""`. Debug filing uses `FilmNotes(notes="captured with RPS7200_DEBUG on")`, and notes are not in the signature. Roll frames get `frame=f"{roll_name}/{number:02d}"` with `roll_name = args.roll or datetime.now().strftime("%Y-%m-%d")`. tools/library.py:129 `shutil.rmtree(path)`.
```

**Failure scenario:** Through the day, Stefan scans frames 3, 7 and 12 of a strip with `tools/scan.py --dpi 1800 --ir` and no --frame. Later `tools/library.py duplicates` reports two of them as 'same scan of the same picture', and `--delete` destroys the raw bytes of two different photographs.

**Fix:** Treat entries with empty stock+frame+subject as never duplicates. Use the READ_STATE position recorded in carriage_state, or the roll folder and transport position, as part of the picture identity. Default --keep to 2 and require an explicit confirmation listing both paths. Never delete an entry whose raw sha256 differs from the kept one's unless the operator names it.

<details><summary>Second reader's check</summary>

signature() (rps7200/library.py:639-699) identifies the picture only by the stock, frame and subject strings. scan.py defaults all three to '' (112-114). Without --auto-exposure, exposure_metered=False and the commanded scale is 1.0, so any two plain scans of different film frames at the same dpi, channels and IR setting share one signature. prunable() then marks all but `keep` (default 1) as redundant, and `--delete` calls shutil.rmtree (tools/library.py:129) with no confirmation. One nuance: for roll frames the metered scales are filed as a commanded exposure (CLI-12), so same-day default-roll collisions occur mainly with `--meter none|once`, where the scales coincide, not with `--meter each`. The scan.py case alone is enough to destroy the raw bytes of different photographs, and true repeat pairs (needed for the random/fixed split) are also collapsed.

</details>

<a id="cli-operator-tools-cli-08"></a>

### CLI-08 -- make_comparison.py judges a destripe correction the pipeline never uses, on an arbitrary TIFF, under names that claim raw and corrected

**Severity** high · **Category** design · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/make_comparison.py:1-15`, `tools/make_comparison.py:94-120`, `tools/make_comparison.py:136`, `rps7200/library.py:338-391`

**Doc claim:** CLAUDE.md:179 'Regenerate the three comparison files after any significant change to the scan or correction path'; tools/make_comparison.py:4 '1_nothing_done.tif  the scan as it came off the scanner'; README.md:195

CLAUDE.md makes these three files the eye test Stefan uses after 'any significant change to the scan or correction path'. But 2_corrected.tif is destripe() with flat and scan column-defect detection, which no delivery path applies. The GUI, Save As, roll frames and scan.py all use apply_shading via scan() or library.corrected(). So a change to shading, decode or library.corrected() does not show up here at all. The input is any TIFF on disk, not a library entry. scan.py and roll outputs are already shading-corrected, so '1_nothing_done.tif ... the scan as it came off the scanner' is false for them. The resolution tag assumes a full-width frame (`raw.shape[1] * 7200 / 10343`; the width is 10344 coordinates). A fresh checkout has no previews/, so the tool raises after writing the TIFFs. Dash-arguments, including --help, are silently dropped.

**Evidence (from the code):**

```text
`corrected = destripe(raw, defects, margin=12, dilate=5)`; `tiff.write("1_nothing_done.tif", raw, ...)` where `raw = tiff.read(scan_path)` and the default is `scan_path = ... "scans/negatives/state_1800dpi.tif"`. grep: destripe/find_column_defects/flat_defect_sigma are called only from tools/make_comparison.py. The production correction is `apply_shading(image, record["reference"], record["ccd_mask"])` in library.corrected(). Line 136: `.save(f"previews/{name}.png")` with no mkdir.
```

**Failure scenario:** A regression lands in apply_shading or decode_index. Claude regenerates the three files as instructed, sends them to Stefan, and they look unchanged, because neither function is on this tool's path.

**Fix:** Take a library entry. Write 1 = library.load() (raw decode), 2 = library.corrected() (today's production correction) and 3 = the inversion of 2. Offer destripe only as an optional fourth file clearly labelled experimental. Create previews/ and use argparse.

<details><summary>Second reader's check</summary>

make_comparison.py:117 builds 2_corrected.tif with `destripe()`. grep shows destripe, find_column_defects and flat_defect_sigma called only here, while production correction is apply_shading via scan() and library.corrected() (library.py:338-391). Its input is an arbitrary TIFF path (default scans/negatives/state_1800dpi.tif), not a library entry, so a regression in decode_index, apply_shading or library.corrected cannot appear in the three files. Argument parsing drops anything starting with '-' (line 94). previews/ is gitignored and absent in this checkout, and line 136 writes into it without mkdir, so the run raises after writing the TIFFs. README.md:195 names this tool as the raw/corrected/inverted generator.

</details>

<a id="cli-operator-tools-cli-09"></a>

### CLI-09 -- The calibration pass's raw bytes are discarded; only a derived ShadingReference is stored, and --reuse leaves no record that the reference was stale

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1755-1760`, `rps7200/direct.py:1945-1970`, `rps7200/direct.py:600-623`, `rps7200/shading.py:75-91`, `tools/scan.py:58-61`, `tools/scan.py:164-168`, `tools/scan_roll.py:140-144`, `tools/scan_roll.py:166-173`

**Doc claim:** CLAUDE.md:104-107 'its shading reference is acquired per session, the CCD mask per pass — none of it recoverable from a TIFF'

The owner requires the exact bytes. The scanner's calibration block (dark and light phases interleaved, 16-bit) is parsed by calculate_shading, using a split_ratio heuristic to separate phases, into per-column averages, and then thrown away. Every library entry stores only that derived shading.npz. A later fix to calculate_shading, for example the phase split, outlier rejection or dark handling, can therefore never be applied to any stored scan. The calibration pass's own CCD mask is not stored either (only the per-pass mask). With `--reuse`, the reference is loaded from calibration/shading.npz: a file overwritten by whichever tool last calibrated, possibly before a power cycle, with an unknown lamp state. Nothing in the entry distinguishes it from a fresh one (meta has no 'reference source' or age).

**Evidence (from the code):**

```text
calibrate_shading(... keep_data: bool = False) ... `self._shading = calculate_shading(data, width)` ... `"data": data if keep_data else None`. ensure_shading calls `result = self.calibrate_shading()` with no keep_data. `if reuse and path.exists(): reference = self.load_shading(path)`. ShadingReference.save stores only pixels_per_line, channels, ref/mean/dark/darkmean, with no timestamp, lamp state, calibration resolution or source.
```

**Failure scenario:** A bug is found in calculate_shading's dark/light split. `make reconstruct` can re-decode every scan's pixels but not their references, so every stored 'correctable' scan stays corrected with the buggy reference forever. Separately, a scan taken with `--reuse` after a power cycle looks exactly like a freshly calibrated one in the library.

**Fix:** Call calibrate_shading(keep_data=True) from ensure_shading. Store the gzipped calibration block, its bytes_per_line/pixels_per_line and the calibration CCD mask beside shading.npz, in calibration/ and in every entry (dedup by sha256 if size matters). Record in meta how the reference was obtained (calibrated now or loaded from a path, with mtime and sha256), and warn on --reuse of a file older than the current process or power-on.

<details><summary>Second reader's check</summary>

ensure_shading calls `self.calibrate_shading()` with the default keep_data=False (direct.py:613). calibrate_shading joins the blocks, runs calculate_shading (split_ratio heuristic, shading.py:109-161) and returns `"data": data if keep_data else None`; nothing else ever passes keep_data. ShadingReference.save stores only pixels_per_line, channels, dark_channels and the ref/mean/dark/darkmean arrays, with no provenance. The calibration pass's own mask is stored only as `self._ccd_mask`, which the next scan overwrites with the per-pass mask. `--reuse` goes through load_shading, and library.save records nothing that distinguishes a loaded reference from a fresh one.

</details>

<a id="cli-operator-tools-cli-10"></a>

### CLI-10 -- 7200 dpi scan.tif has a host-side stagger realignment baked in that reconstruct/decode_raw never replay; every 7200 dpi entry reads 'decode CHANGED'

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1638-1663`, `rps7200/library.py:411-436`, `rps7200/library.py:439-509`, `tools/library.py:96-113`, `tools/library.py:175-182`

**Doc claim:** CLAUDE.md 'scan.tif in an entry is the decode alone, with no flat-fielding'

CLAUDE.md says scan.tif 'is the decode alone'. At 7200 dpi it is the decode plus a 4-line even/odd column shift that trims 4 rows. The entry records `corrections_applied: []` and nothing about the realignment. The raw bytes remain exact, but today's re-decode path does not reproduce scan.tif. `tools/library.py reconstruct` (and `make reconstruct`) therefore flags every 7200 dpi entry as a changed decode and exits 1: the false-alarm pattern CLAUDE.md warns about. `migrate-raw` lists them under 'failed' (shape mismatch). Reachable from `tools/scan.py --dpi 7200 --no-shading`, the only way the CLI can take a 7200 dpi pass.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... image = self._realign_native_column_stagger(image)` and then `raw_pixels = image` (what library.save stores as scan.tif). _realign trims `h - lines` rows. reconstruct/decode_raw only call `DirectScanner.decode_index(raw, params, ...)` / `_deinterleave`, with no realignment, then `if image.shape != stored.shape: return image, (f"decode CHANGED: now {image.shape}, stored {stored.shape}")`.
```

**Failure scenario:** Stefan takes 7200 dpi raw scans with scan.py and later runs `make reconstruct` after an unrelated change. Each 7200 dpi entry prints '! ... decode CHANGED: now (H,W,C), stored (H-4,W,C)'. The command exits 1 and real regressions are buried.

**Fix:** Record the realignment in meta and raw_layout (for example `native_stagger_lines: 4`) and apply it in library.decode_raw/reconstruct when the layout says so. Better still, store the plain decode in scan.tif and treat the realignment as a correction applied in library.corrected().

<details><summary>Second reader's check</summary>

At resolution == NATIVE_COLUMN_STAGGER_DPI (7200, direct.py:1622), scan() realigns and trims `lines` rows (direct.py:2695-2702, 1638-1663) before `raw_pixels = image`. That array is what scan.py files through `last_pixels_raw`. library.reconstruct and decode_raw call only decode_index/_deinterleave, and there is no reference to the stagger anywhere in library.py, so the shape comparison at library.py:487-490 returns 'decode CHANGED'. The CLI counts that as a regression and exits 1 (tools/library.py:103-113); migrate-raw lists these entries as failed (179-182). Reachable via `tools/scan.py --dpi 7200 --no-shading`, since a shaded full-frame 7200 dpi pass raises ShadingUnavailable.

</details>

<a id="cli-operator-tools-cli-07"></a>

### CLI-07 -- filing_load_test.py's verdict is a tautology: it reports 'safe' whenever passes vary at all, so the measurement CLAUDE.md relies on cannot fail

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/filing_load_test.py:101-111`, `tools/filing_load_test.py:81-88`, `tools/filing_load_test.py:4`

**Doc claim:** CLAUDE.md:170 '`tools/filing_load_test.py` is the measurement ... Run it before trusting the roll path unattended.'

With two equal-sized groups, the pooled population variance equals the mean within-group variance W plus (d/2)², where d = lm - qm. So 2*spread = sqrt(4W + d²), which is greater than |d| whenever W > 0. The 'unsafe' branch is reachable only if every pass within each group times identically to the float, so the verdict says 'safe' however large the slowdown. The test also only times 300 dpi 8-bit FULL_FRAME passes with shading=False (a few MB read). It measures duration rather than read timeouts or stalls, while the FrameWriter risk is gzipping 140-570 MB while a 1800-3600 dpi 16-bit pass streams. Its usage line says `RPS7200_DEBUG=0`, which contradicts CLAUDE.md's 'always scan with debug filing on'.

**Evidence (from the code):**

```text
`spread = statistics.pstdev(quiet + loaded)` ... `verdict = ("no measurable effect -- overlapping filing looks safe" if abs(lm - qm) < 2 * spread else "reads slow under load -- do NOT overlap filing")`
```

**Failure scenario:** Background gzip adds a consistent +6 s (27%) to every 22 s pass with 1 s of jitter. The tool prints 'no measurable effect -- overlapping filing looks safe', and the unattended roll path is trusted on that basis.

**Fix:** Compare the paired differences (loaded minus quiet per round) against their own standard error, for example a paired t or sign test, not against the pooled spread. Run at the roll's real dpi, depth and IR settings, and gzip real-sized payloads. Also record read-loop stalls (NoDataYet streaks, max inter-chunk gap), not just wall time. Drop the RPS7200_DEBUG=0 instruction.

<details><summary>Second reader's check</summary>

The algebra holds. With equal group sizes, pstdev(quiet+loaded)^2 = W + (d/2)^2, so 2*spread = sqrt(4W + d^2) > |d| whenever there is any within-group variance, and the 'do NOT overlap' branch (filing_load_test.py:108-110) is effectively unreachable. The test also times only 300 dpi 8-bit FULL_FRAME passes with shading=False against a 32 MB gzip, and the usage line suggests `RPS7200_DEBUG=0` (Bourne syntax, and contrary to the 'always debug' rule). Severity is lowered to medium: the printed per-round times and mean difference are still correct and visible, and no code path gates on the verdict. The defect is in the one-line conclusion, which CLAUDE.md:170 tells readers to trust.

</details>

<a id="cli-operator-tools-cli-11"></a>

### CLI-11 -- library.save silently drops the INQUIRY every CLI caller passes, plus bracket_* and roll_index/roll_position, so brackets cannot be regrouped from the library

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:142`, `rps7200/library.py:204-252`, `tools/scan.py:191-193`, `tools/scan_roll.py:567`, `rps7200/direct.py:2402-2405`, `rps7200/direct.py:3653-3655`, `tools/scan.py:261-265`

The device identity (vendor, product, firmware, max_resolution, ccd_width, frame) never reaches any entry even though both tools pass it, so a firmware difference cannot be attributed later. Bracket passes are filed with no bracket id, index or ratio. Their only link is a timestamp and whatever film notes the operator typed, so merge_bracket cannot be reliably recomputed from the library. The merged result exists only in the delivered file and sidecar. Roll frames lose their transport index and position, which then exist only in roll.json (overwritten on resume, CLI-04).

**Evidence (from the code):**

```text
library.save signature `inquiry: Any = None,`, and `grep -n inquiry rps7200/library.py` finds only that line. The record's 'scan' block keeps a fixed key list (resolution_dpi ... filter_offsets) that omits `bracket_index`, `bracket_ratio`, `bracket_passes`, `bracket_stops`, `roll_index` and `roll_position` set by scan_bracket/scan_roll. The merged bracket's `meta["bracket"] = {"passes", "ratios", "stops", "stats"}` goes only to the `--out` .json sidecar.
```

**Failure scenario:** Two 5-pass brackets of the same frame are taken minutes apart with identical notes. Months later, re-merging from the library cannot tell which five entries belong together or in what ladder order, other than by guessing from timestamps and exposure_scale.

**Fix:** Write `device: dataclasses.asdict(inquiry)` into the record. Keep the bracket_* keys plus a bracket group id (uuid shared by every pass), and roll_index/roll_position/roll name, in the scan block. Consider filing a small 'bracket' manifest listing the member entry ids and the merge parameters.

<details><summary>Second reader's check</summary>

library.save accepts `inquiry` (library.py:142) and never uses it in the body. The 'scan' block is a fixed key list (204-230) that omits bracket_index, bracket_ratio, bracket_passes and bracket_stops (set at direct.py:2402-2405) and roll_index and roll_position (direct.py:3653-3654). The merged bracket's meta['bracket'] goes only to the --out sidecar (scan.py:261-281).

</details>

<a id="cli-operator-tools-cli-12"></a>

### CLI-12 -- Roll frames are filed as exposure_metered=false with no metering evidence even when every frame was metered

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:3616-3652`, `rps7200/direct.py:2791`, `rps7200/direct.py:2808-2809`, `rps7200/library.py:639-699`, `tools/scan_roll.py:133-137`

With `--meter each` (the default) or `once`, the metering happens outside scan(). Every roll frame's entry therefore claims its exposure was commanded rather than metered, and carries no `metering` block: no probe percentiles and no rounds. The library's own docstring calls that block 'the evidence for' the exposure. signature() then includes the metered scales as a commanded exposure, the opposite of its stated rule. The probe passes themselves are not filed either (CLI-03).

**Evidence (from the code):**

```text
scan_roll: `scales = self.auto_exposure(target=exposure_target, infrared=infrared, film=film)` then `image, meta = self.scan(... exposure_scale=scales, ...)` with auto_exposure left False. scan(): `"exposure_metered": bool(auto_exposure),` and `if auto_exposure and self.last_metering is not None: meta["metering"] = self.last_metering`.
```

**Failure scenario:** A roll frame comes out with blue clipped. Its entry says `exposure_metered: false`, `metering: null`, so it is impossible to tell whether metering misjudged it or the operator asked for that exposure.

**Fix:** Have scan_roll copy `self.last_metering` into meta and set `meta['exposure_metered'] = True` (plus meter mode) whenever it metered for that frame. For `once`, record the frame the metering came from.

<details><summary>Second reader's check</summary>

scan_roll meters with `self.auto_exposure(...)` (direct.py:3627-3629), then calls scan() with `exposure_scale=scales` and without auto_exposure (3644-3652). scan() sets `"exposure_metered": bool(auto_exposure)` (False, direct.py:2791) and attaches metering only `if auto_exposure` (2808-2809). Every metered roll frame is therefore filed as commanded, with metering=null, and signature() treats the metered scales as a commanded exposure.

</details>

<a id="cli-operator-tools-cli-13"></a>

### CLI-13 -- --approved diverges from the window: ignores the walk's prescan dpi, does not un-orient GUI-walk prescans, and ignores approved.json

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/scan_roll.py:200-261`, `tools/scan_roll.py:108-114`, `tools/gui.py:4722-4744`, `tools/gui.py:2809-2813`, `rps7200/session.py:1687-1688`

The docstring says 'This is the window's contact-sheet path with the window taken off ... what runs here is what runs there'. Three differences break that:
1. A walk taken at 600 or 900 dpi (the window's PRESCAN_LADDER) and held with the CLI's default `--prescan-dpi 300` silently resamples the references. The tool's own help says every frame then reads unverified and nothing moves.
2. A window walk with a session rotation or flip writes rotated prescanNN.tif files, and the CLI correlates them unrotated. A mirrored reference inverts the sign of the offset.
3. The operator's positions in approved.json are ignored and replaced by fresh machine proposals.

**Evidence (from the code):**

```text
hold_from_walk: `frames = [(number, tiff.read(str(path))) for number, path, _ in walked_prescans(folder, manifest, say=print)]` then `offsets, notes = frame_edges.propose_centred(frames, film=film)`. It never reads `settings.prescan_resolution`, `rotation`/`flipped` or approved.json. The window's read_survey does `image=preview.unorient(image, turn, mirrored)` with `turn = int(manifest.get("rotation") or 0)`, uses `read_approved(folder, ...)`, and pins `predpi = self._survey_predpi`.
```

**Failure scenario:** Stefan walks and approves a strip in the window at 600 dpi prescan with 180° rotation, then runs `scan_roll.py --approved rolls/2026-09-23 --dpi 1800`. Every hold compares a 300 dpi fresh pass against a rotated 600 dpi reference. Frames read unverified or are driven the wrong way, the run ends with exit 0, and none of his approvals are used.

**Fix:** Reuse read_survey's logic: move it from tools/gui.py into rps7200/session.py and call it from both. Un-orient using the manifest's rotation/flipped, and default --prescan-dpi to the walk's recorded prescan_resolution (refuse on mismatch). Honour approved.json when present and log which source each offset came from.

<details><summary>Second reader's check</summary>

hold_from_walk (scan_roll.py:200-261) reads prescans with tiff.read and calls propose_centred directly. It never reads settings.prescan_resolution (main uses args.prescan_dpi, default 300), never reads rotation/flipped and never reads approved.json. The window un-orients with `preview.unorient(image, turn, mirrored)` (gui.py:4722-4744), uses read_approved(), and pins predpi to `_survey_predpi` (gui.py:2809-2813). Window walks write oriented prescans, because _file composes orientation into meta and FrameWriter orients the delivered paths. This is a CLI-versus-window divergence rather than a demo divergence, so 'bug' would be the better category; the substance stands.

</details>

<a id="cli-operator-tools-cli-14"></a>

### CLI-14 -- Arguments are validated only after the device is opened, calibrated and metered; any integer --dpi is accepted

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P16](../problems/P16-timeouts-and-runtime-budget.md)

**Where:** `tools/scan.py:39`, `tools/scan.py:70-73`, `tools/scan.py:143-146`, `tools/scan.py:267-279`, `tools/scan_roll.py:73`, `tools/scan_roll.py:108`, `rps7200/direct.py:2520-2575`, `rps7200/direct.py:2291-2296`, `rps7200/export.py:64-79`, `tools/gui.py:118`

The following mistakes are all caught only after calibration (3-4 min) and, with --auto-exposure, after metering too:
- `--dpi 4000`/`7200` without --no-shading
- `--stops 0`
- `--out scan.png`: the scan and library filing complete, then the delivered file and sidecar are lost to a traceback
`scan_roll.py --dpi 7200` has no raw escape at all (CLI-05). It calibrates, then for each of three frames prescans, meters, advances the film and fails with ShadingUnavailable, ending after max_failures with the film moved. Non-ladder resolutions (`--dpi 1000`, `--prescan-dpi 450`) go straight to MODE SELECT as a resolution never measured on this device. Other unvalidated inputs: `--exposure-scale` accepts 2 or 5 values (padded or truncated in Settings.scaled) and zero or negative values (clamped to 100). `--frames -1` calibrates and writes an empty manifest. `--start-at 0` is silently frame 1.

**Evidence (from the code):**

```text
`ap.add_argument("--dpi", type=int, default=1800)` with no choices; the window uses `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`. In scan(), the auto_exposure block (2520-2531) runs before `needed = self._shading_columns_needed(...)` / `raise ShadingUnavailable(... Scan at 3600 dpi or below ...)`. bracket_ladder: `if stops <= 0: raise ValueError(...)`, reached after calibration and metering. `note = export.write(out, ...)` → format_of raises `cannot tell what format ...` only after the scan.
```

**Failure scenario:** `tools/scan.py --dpi 7200 --auto-exposure` spends about 4 minutes calibrating and about 45 s metering, then dies with ShadingUnavailable. `scan_roll.py --dpi 7200` calibrates, advances the strip three frames and exits 1 with nothing scanned.

**Fix:** Validate everything before opening the device:
- dpi against a shared ladder constant taken from DirectScanner, not retyped
- dpi > 3600 requires shading off (and a raw option in scan_roll)
- stops > 0
- --out suffix via export.format_of
- exposure-scale length 1/3/4 and > 0
- frames ≥ 0 and start-at ≥ 1
- quality in 60-100

<details><summary>Second reader's check</summary>

`--dpi` is a bare int with no choices (scan.py:39, scan_roll.py:73). In scan(), the auto_exposure block (direct.py:2512-2531) runs before the MAX_SHADING_COLUMNS refusal (2566-2575), and ensure_shading (a 3-4 minute calibration) runs before either. bracket_ladder's stops check runs after metering (direct.py:2373-2379). export.write/format_of raises on an unknown suffix only after library filing (scan.py:237-246 before 278). In scan_roll, ShadingUnavailable is caught per frame and keep_going() advances the film (direct.py:3662-3686), so `--dpi 7200` prescans, meters and advances up to max_failures times.

</details>

<a id="cli-operator-tools-cli-15"></a>

### CLI-15 -- scan.py calibrates straight after INQUIRY, with no check or question about film in the transport

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P17](../problems/P17-calibration-without-asking.md)

**Where:** `tools/scan.py:160-168`, `rps7200/direct.py:2543-2553`

**Doc claim:** CLAUDE.md:272-279 'Calibrate with the film loaded'

CLAUDE.md records that calibrating an empty transport 'is a state the vendor never creates, and doing it once preceded a wedge'. scan_roll at least seeks first and refuses when the counter is implausible. scan.py goes straight into a 3-4 minute calibration with no prompt, no `--film-loaded` acknowledgement and no READ_STATE evidence check. The media bit is only logged, inside the later scan.

**Evidence (from the code):**

```text
`with DirectScanner(verbose=args.verbose, debug=False) as s: info = s.inquiry() ... print(s.ensure_shading(ref_path, reuse=args.reuse, skip=args.no_shading)["summary"])`. scan(): `if require_media: ... # Reported, not enforced`.
```

**Failure scenario:** The operator types `tools/scan.py --dpi 1800` after removing the previous strip. The tool calibrates on an empty transport, the state that preceded a wedge, and then scans nothing.

**Fix:** Before calibrating, read READ_STATE and print what it suggests. Require an explicit `--film-loaded` flag or an interactive y/N when stdin is a TTY. At minimum, mirror scan_roll's seek-based plausibility check.

<details><summary>Second reader's check</summary>

scan.py goes from inquiry() straight to ensure_shading (160-168), with no READ_STATE check, no prompt and no seek plausibility check. The only media check is the logged, unenforced one inside scan() (direct.py:2543-2553), and that comes after calibration. scan_roll.py by contrast seeks, with the FilmNotPlaced refusal, before calibrating (385-390, 446).

</details>

<a id="cli-operator-tools-cli-16"></a>

### CLI-16 -- scan.py holds every pass (raw pixels, raw bytes and corrected frame) in RAM; high-dpi brackets need several to 10+ GB

**Severity** medium · **Category** design · **Verdict** partly · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `tools/scan.py:174-194`, `tools/scan.py:205-221`, `rps7200/direct.py:2408-2420`, `rps7200/direct.py:650-692`

**Doc claim:** CLAUDE.md 'A single scan compresses nothing while the device is open. Each is spooled to a temporary file as it is taken'

scan.py holds every pass's raw pixels, raw bytes and (via scan_bracket retain=True) corrected frame in RAM until after close(). High-dpi brackets therefore need several GB, and an OOM kill loses every pass and abandons the read. Debug filing's spool-to-disk approach (_debug_capture) is not used because the tool forces debug=False.

**Evidence (from the code):**

```text
hold(): `pending.append(dict(capture, inquiry=info, meta=meta, image=image if raw is None else raw))`, where capture holds `"raw": self.last_raw` bytes. scan_bracket is called with the default `retain=True`, so `frames.append(image)` keeps every corrected frame too.
```

**Failure scenario:** On a 16 GB laptop, `tools/scan.py --dpi 7200 --no-shading --bracket 6` is OOM-killed during pass 5. The scanner is left mid-read, and no pass is filed.

**Fix:** In on_pass, spool raw bytes and raw pixels to a temp dir (reuse _debug_capture's approach) and keep only paths. Pass retain=False and reload the corrected frames for the merge from the spool.

<details><summary>Second reader's check</summary>

The RAM behaviour is real. Each pending dict holds the raw pixel array plus capture['raw'] bytes and the reference, and scan_bracket's default retain=True keeps every corrected frame as well (direct.py:2415-2418). Nothing is spooled. The CLAUDE.md sentence cited, however, sits in the debug-filing section and describes debug filing, so it is not contradicted as such. The finding is the missing spool, not a doc mismatch.

</details>

<a id="cli-operator-tools-cli-17"></a>

### CLI-17 -- `library.py reconstruct` counts non-regressions as changed decodes and aborts entirely on one missing scan.tif

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P12](../problems/P12-library-maintenance-tools.md)

**Where:** `tools/library.py:96-113`, `rps7200/library.py:451-503`, `rps7200/direct.py:1582-1596`, `rps7200/protocol.py:341`

`library.py reconstruct` counts needs-migration and missing-reference verdicts as decode regressions and exits 1. One entry with a missing scan.tif, an unreadable scan.json, or raw bytes whose channel tags do not match (ScanReadError is a RuntimeError, which reconstruct's except clause does not catch) aborts the whole run with a traceback, and migrate-raw fails the same way.

**Evidence (from the code):**

```text
CLI: `if verdict.startswith("identical"): mark = " " elif "no raw bytes" in verdict or verdict.startswith("could not"): ... else: mark, changed = "!", changed + 1`. library.reconstruct can return `"stored as it was read, bottom-up; today's decode turns it upright -- see migrate-direction"` (its own comment: 'Not a regression') and `"stored image is shading-corrected but its reference is missing"`. It also calls `stored = tiff.read(str(path / "scan.tif"))` unguarded.
```

**Failure scenario:** After a decode change, `make reconstruct` prints '! ...' for 38 bottom-up legacy entries and one real regression, then crashes on an entry with a deleted scan.tif, so later entries are never checked.

**Fix:** Return a structured verdict (kind in {identical, regression, needs-migration, unreproducible, unreadable}) and count only 'regression' as changed. Wrap each entry in try/except and report it as unreadable.

<details><summary>Second reader's check</summary>

The CLI's classifier (tools/library.py:99-104) marks anything that is not 'identical', 'no raw bytes' or 'could not' as '!' and counts it as changed. That includes library.reconstruct's 'stored as it was read, bottom-up ... see migrate-direction' (its own comment calls this 'Not a regression'), 'stored image is shading-corrected but its reference is missing', and every 7200 dpi entry (CLI-10). reconstruct also calls `json.loads(scan.json)` and `tiff.read(scan.tif)` without guards (library.py:462, 487). Its except clause catches only (KeyError, ValueError, TypeError), while decode_index raises ScanReadError, a RuntimeError (protocol.py:341), on unrecognised or mismatched channel tags. The CLI loop has no per-entry try, so one bad entry aborts the whole run. migrate-raw calls reconstruct first and inherits the same crash.

</details>

<a id="cli-operator-tools-cli-18"></a>

### CLI-18 -- `library.py verify` cannot see half-written entries or prescan.tif; says 'library is intact' after an interrupted save

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:741-750`, `rps7200/library.py:776-816`, `rps7200/library.py:163-309`, `tools/library.py:83-88`

**Doc claim:** CLAUDE.md:21 'make verify # check the library's checksums and completeness'

An entry interrupted by Ctrl-C, a killed writer thread (CLI-01), disk full or a crash has its raw.bin.gz (possibly complete) but no scan.json. It is invisible to list, verify, reconstruct and duplicates, and verify still prints 'library is intact'. A truncated or corrupt scan.json is also silently skipped. prescan.tif has no checksum and is never verified. Every save writes in place with no temp-dir-and-rename, so a partial entry is indistinguishable from a complete one until scan.json appears.

**Evidence (from the code):**

```text
entries(): `for candidate in sorted(root.glob("*/scan.json")):` and unreadable JSON is `continue`. save() writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz first, and `(path / "scan.json").write_text(...)` last. verify() checks scan.tif, shading, ccd_mask and raw only, with no prescan check and no directory scan.
```

**Failure scenario:** After a Ctrl-C mid-roll (CLI-01), library/ holds 20260923T101500Z_... with scan.tif and a 40 MB partial raw.bin.gz. `make verify` reports 'library is intact'.

**Fix:** Write each entry into `<id>.partial/` and rename it when complete. Make verify list directories under root without a readable scan.json, and scan.json files that fail to parse. Record and verify a sha256 for prescan.tif.

<details><summary>Second reader's check</summary>

entries() globs `*/scan.json` and silently skips JSON that fails to parse (library.py:741-750). save() writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz before scan.json (library.py:176-297), with no temp dir or rename. verify() iterates only entries(), so directories without scan.json are invisible, and it never checks prescan.tif, which has no checksum. save()'s collision check tests only `scan.json`, so a later save with the same id silently reuses and overwrites an orphan directory.

</details>

<a id="cli-operator-tools-cli-19"></a>

### CLI-19 -- Roll entries store prescan.tif as corrected 8-bit pixels with no raw bytes, no checksum and no 'corrected' label; the CLI drops raw_prescan

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan_roll.py:560-566`, `rps7200/session.py:1883-1890`, `rps7200/session.py:1090-1098`, `rps7200/library.py:176-177`, `rps7200/library.py:290-296`, `rps7200/direct.py:1700-1711`

Every roll entry, from the CLI (scan_roll.py:562) and from the window (session.py:1887) alike, stores prescan.tif as the shading-corrected 8-bit prescan. It has no corrections label, no checksum, no raw bytes and no mask. RollFrame.raw_prescan is available and discarded on both paths.

**Evidence (from the code):**

```text
scan_roll submit: `prescan=frame.prescan, prescan_meta=frame.prescan_meta,` (RollFrame.raw_prescan exists but is never passed). FrameWriter: `prescan=job["prescan"]`. prescan() returns `self.scan(..., shading=True, ...)` output, i.e. corrected. library.save: `tiff.write(str(path / "prescan.tif"), prescan)` and the record is `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}`.
```

**Failure scenario:** A shading fix changes the 8-bit scaling in apply_shading. Every stored roll prescan keeps the old correction baked in, indistinguishable from raw, and registration studies built on library prescans silently mix old and new corrections.

**Fix:** Pass raw_prescan through FrameWriter and store it as prescan.tif, with a `prescan.corrections_applied` field, a sha256 and ideally the prescan's own raw bytes and mask. Otherwise label it `corrections_applied: ["shading"]`.

<details><summary>Second reader's check</summary>

This holds as described for the CLI: scan_roll.py:562 passes `prescan=frame.prescan`, which is prescan()'s corrected output (shading=True, direct.py:1700-1711); RollFrame.raw_prescan is unused, and library.save labels it only by file, read_direction and carriage_state. It is not CLI-specific, though. The window's roll does the same (session.py:1883-1890, `prescan=rf.prescan`), so every roll entry from either path carries a corrected, unlabelled prescan.

</details>

<a id="cli-operator-tools-cli-a1"></a>

### CLI-A1 -- A calibration that yields no reference prints 'scans will be raw', then the next scan silently recalibrates on the lazy in-scan path

**Severity** medium · **Category** hardware-safety · **Verdict** found-by-verifier · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `rps7200/direct.py:612-626`, `rps7200/direct.py:1945-1950`, `rps7200/direct.py:2582-2594`, `tools/scan_roll.py:166-173`, `tools/scan_roll.py:446-453`, `tools/scan.py:164-168`

When the calibration pass returns no usable lines, both tools carry on. The message 'scans will be raw' is false: every later scan() has shading=True and `_shading is None`, so it runs calibrate_shading() inside the scan. scan_roll.py's own comment (433-441) documents that path stalling the device with LIBUSB_ERROR_PIPE. If that second calibration also fails, scan() raises ShadingUnavailable, so the pass is refused rather than delivered raw.

**Evidence (from the code):**

```text
ensure_shading: `summary += (f", saved {saved}" if result["reference"] is not None else " -- no usable shading reference; scans will be raw")`. calibrate_shading: `self._shading = calculate_shading(data, width); if self._shading is None: self._log("calibration returned no usable shading lines")` (no raise). scan(): `if self._shading is None or needed > ...: self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`. scan_roll.py calibrate() only prints the summary, and main sets `placed = True` regardless.
```

**Failure scenario:** The first calibration of a roll returns no usable lines, and the tool prints '-- no usable shading reference; scans will be raw'. The roll begins. The first prescan calibrates lazily, which is the documented stall, and the device stops answering mid-roll.

**Fix:** In both tools, treat `result['reference'] is None` after a non-skipped ensure_shading as a hard stop before anything else is scanned, and exit non-zero. Remove the 'scans will be raw' wording.

<a id="cli-operator-tools-cli-a2"></a>

### CLI-A2 -- Neither CLI tool estimates its runtime or warns before a run that will outlive the 10-minute foreground kill

**Severity** medium · **Category** user-error · **Verdict** found-by-verifier · **Problem** [P16](../problems/P16-timeouts-and-runtime-budget.md)

**Where:** `tools/scan.py:196-235`, `tools/scan_roll.py:455-477`, `rps7200/session.py:743`, `rps7200/session.py:191`

CLAUDE.md makes backgrounding anything over ~8 minutes mandatory, because a killed foreground read is an abandoned read, which wedges the device. The CLI tools print nothing before starting that would let a caller (Claude in particular) see that a run is long. Combinations like `scan.py --dpi 3600 --bracket 9 --ir`, which is about 3-4 min of calibration plus 8×138 s plus one RGBI pass, or any multi-frame roll, silently exceed the limit. Taken with CLI-01 and CLI-02, such a kill also loses every pass.

**Evidence (from the code):**

```text
grep 'estimate' in tools/scan.py and tools/scan_roll.py returns nothing, while `def estimate_seconds(resolution: int, infrared: bool, ...` exists at session.py:743 and `FORWARD_FRAME_S = 4.6` at session.py:191 for the window.
```

**Failure scenario:** Claude runs `RPS7200_DEBUG=1 uv run python tools/scan.py --dpi 3600 --bracket 5 --ir` in the foreground. Calibration plus five passes take about 16 minutes. The harness kills it at 10 minutes mid-read: the scanner wedges and passes 1-3 are lost unfiled.

**Fix:** Before opening the device, compute and print an estimate with session.estimate_seconds (plus calibration and advance costs). When the estimate exceeds 8 minutes and stdout is not a TTY, or unless a `--long-ok` flag is given, refuse or warn loudly.

<a id="cli-operator-tools-cli-20"></a>

### CLI-20 -- `scan.py --library ''` files into the current directory, while scan_roll documents `--library ''` as 'skip'; all data paths are CWD-relative

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:101-111`, `tools/scan.py:129-130`, `tools/scan.py:177-178`, `tools/scan_roll.py:152-155`, `rps7200/library.py:163-165`, `tools/scan.py:58`, `tools/scan_roll.py:292`

The two sibling tools give the same idiom opposite meanings. In scan.py, `--library ''` keeps raw and scatters entry directories plus an index.json into the working directory. Running either tool from anywhere but the repo root (for example `cd tools; uv run python scan.py`) creates a second library/, calibration/ and rolls/ there, and `--reuse` then misses the real reference. `tools/library.py` then reports 'no library at library' or shows the wrong one.

**Evidence (from the code):**

```text
scan.py: `--library nargs="?", const="library", default="library"`, and hold() skips only `if args.library is None`. scan_roll help: `"--library '' to skip"`. library.save: `root = Path(root); path = root / entry_id(...)`, and `Path('')/x` resolves to `x` in the CWD. Defaults `"calibration/shading.npz"`, `f"rolls/{roll_name}"` and `"library"` are all relative.
```

**Failure scenario:** The operator copies the roll tool's skip idiom: `tools/scan.py --library ''`. The entry lands as ./20260923T..._unknown-film_1800dpi/ in the repo root, outside library/, invisible to `make verify` and `make reconstruct`.

**Fix:** Treat '' as None in scan.py, or reject it, and give scan_roll a `--no-library` for symmetry. Resolve default roots relative to the repo, or to an RPS7200_HOME setting, rather than the CWD, and print the absolute library path at start.

<details><summary>Second reader's check</summary>

scan.py treats only None as 'skip' (hold() at 177; keep_raw uses `is not None`), so `--library ''` becomes library.save(root='') and Path('') resolves to the CWD. scan_roll.py uses truthiness (`bool(args.library)`; FrameWriter checks `if job["library"]`), so '' skips there, as its help says. All default roots are CWD-relative in both tools, and in the GUI (`home = Path('.')`, gui.py:8150).

</details>

<a id="cli-operator-tools-cli-21"></a>

### CLI-21 -- Operator-facing distances are still in millimetres, with a stale single-command limit comment and a stale README formula

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan_roll.py:102-107`, `tools/scan_roll.py:396-413`, `tools/scan_roll.py:516-521`, `rps7200/protocol.py:260-279`, `rps7200/direct.py:3123`, `rps7200/session.py:672-693`

**Doc claim:** CLAUDE.md:245 'Millimetres are prohibited'; README.md:555-556 'move `0.1057 × param + 0.1662` mm'

CLAUDE.md makes millimetres a prohibited unit for transport distances, and the driver's log lines use say_units(). The operator CLI still takes and prints millimetres, and hard-codes a 0.85 mm drift threshold that is a second copy of drift_warning. The 1.0118 mm comment describes the param-8 era. The README's movement formula uses the old 1.572-unit ramp (0.1662 mm) where the code now uses 1.84 (0.1945 mm).

**Evidence (from the code):**

```text
`ap.add_argument("--nudge", type=float, ... help="move the film this many mm ...")`; comment: "one command reaches only 1.0118 mm and `param_for_mm` clamps there"; prints `offset the film by {sent:+.3f} mm`, `offset {offset:+.2f} mm` and `SHORT BY {short:.2f} mm` (`short > 0.85`). The code has MAX_CORRECTION_PARAM = 87, MM_PER_UNIT = 0.1057 and COMMAND_UNITS = 1.84, so one command reaches 0.1057 × 88.84 ≈ 9.39 mm. plan_nudges' docstring still says 'param in 1..8' and 'clamps silently at param 8'.
```

**Failure scenario:** The operator reads '--nudge 2' as a small move, and the log prints 'offset ... mm' next to driver lines in units. Comparing the two needs the conversion that CLAUDE.md says has already hidden two mistakes.

**Fix:** Accept `--nudge` in units, or in params, and convert with protocol.units. Print with say_units. Take the drift threshold from DirectScanner/scan_roll's drift_warning, and update the comment and the README formula.

<details><summary>Second reader's check</summary>

--nudge is in mm (scan_roll.py:102-107), the prints are in mm (412, 517-521), and there is a hard-coded 0.85 mm threshold. The comment at 396-397 says one command reaches '1.0118 mm', while MAX_CORRECTION_PARAM=87 (direct.py:3123), and plan_nudges' docstring still says 'param in 1..8' and 'clamps silently at param 8' (session.py:676, 690). OVERHEAD_MM = MM_PER_UNIT*COMMAND_UNITS = 0.1057*1.84 ≈ 0.1945 (protocol.py:260-279), against README.md:555-556's 0.1662.

</details>

<a id="cli-operator-tools-cli-22"></a>

### CLI-22 -- Dry-run help and docstring promise a 2.5-minute walk, but a dry run now calibrates first (3-4 minutes)

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan_roll.py:26-28`, `tools/scan_roll.py:130-132`, `tools/scan_roll.py:433-446`

**Doc claim:** tools/scan_roll.py:131-132

Unless --reuse is given, a dry run spends 3-4 minutes calibrating before the first prescan, so the stated cost is wrong by more than 100%. That matters for the 8-minute backgrounding rule and for the operator's expectations.

**Evidence (from the code):**

```text
Help: "prescan and advance only -- no full scans. Walks a 6-frame strip in about 2.5 minutes"; docstring: "it walks the whole strip in a couple of minutes". Code 446: `calibrate(s, args)` runs on dry runs too ('On a dry run too').
```

**Failure scenario:** Claude estimates a 6-frame dry run at 2.5 minutes and runs it in the foreground next to other work. It actually takes about 6.5 minutes, closer to the 10-minute kill than budgeted.

**Fix:** Update the text, or better, print an estimate at start using session.estimate_seconds and FORWARD_FRAME_S.

<details><summary>Second reader's check</summary>

The help at scan_roll.py:131-132 and the docstring at 26-27 promise a walk of about 2.5 minutes, but calibrate(s, args) at 446 runs on dry runs too, adding 3-4 minutes unless --reuse is given.

</details>

<a id="cli-operator-tools-cli-23"></a>

### CLI-23 -- A roll that ends early on a blank frame or a stalled transport exits 0 with fewer frames than --frames asked; the default --start-at 1 silently rewinds

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/direct.py:3509-3515`, `tools/scan_roll.py:630-638`, `tools/scan_roll.py:86-95`, `tools/scan_roll.py:379-390`

An unexposed or very thin frame in the middle of a strip ends the roll as 'end of film'. `--frames 36` can scan 12 and report success. The exit code and summary never compare against the request. Separately, running the tool 'on the frame in the gate' (the pre-seek habit) now winds the strip back to frame 1 without asking.

**Evidence (from the code):**

```text
direct.py: `if contrast < blank_contrast: self._log(... "end of film"); return`. scan_roll.py: `return 1 if trouble is not None or failed else 0` with no comparison of `scanned` against `args.frames`. `--start-at` default=1, and `seek(s, max(0, args.start_at - 1), ...)`.
```

**Failure scenario:** The operator runs `scan_roll.py --frames 36`. Frame 13 is a blank exposure, so the roll stops, prints '12 scanned, 0 failed', exits 0, and the operator walks away believing the roll is done.

**Fix:** When --frames was given and fewer were scanned, say so on stderr and exit non-zero. Make the blank-frame stop configurable (skip versus end). When the seek would move more than a frame or two, log a line up front.

<details><summary>Second reader's check</summary>

`if contrast < blank_contrast: ... return` (direct.py:3509-3515) ends the generator. The exit code (scan_roll.py:638) and the summary never compare `scanned` with args.frames. The default --start-at 1 seeks to counter 0 from wherever the film is (386-387).

</details>

<a id="cli-operator-tools-cli-24"></a>

### CLI-24 -- The roll manifest omits settings needed to understand or resume it (fast_ir, nudge, correct, reuse, no_shading, stock)

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/scan_roll.py:305-321`, `tools/scan_roll.py:391-418`, `tools/gui.py:4773-4790`

A deliberate `--nudge` displacement is recorded nowhere durable: not in the manifest, not in library entries. Every registration number in that roll is then misattributed to the film. fast_ir/correct/correct_dry_run/reuse/no_shading/stock/process are missing too, so reopening a CLI roll in the window cannot restore them, and a later reader cannot tell a --reuse roll from a freshly calibrated one.

**Evidence (from the code):**

```text
`"settings": {"dpi", "infrared", "meter", "film", "dry_run", "start_at", "frames", "prescan_resolution"}`. The --nudge comment says "Deliberately, and said out loud: ... a displacement nobody knows about would read as the film's own error", but the value only goes to stdout. The window's RESTORABLE includes `"fast_infrared"`, `"correct"` and `"reverse_hold"`.
```

**Failure scenario:** Stefan sets a strip 3 mm off with `--nudge 3` to test correction. Weeks later tools/registration_margin.py reads those entries' registration as natural drift.

**Fix:** Write every parsed argument (vars(args)) into manifest['settings'], and put nudge/correct into each frame's library meta alongside registration.

<details><summary>Second reader's check</summary>

manifest['settings'] (scan_roll.py:305-321) omits fast_ir, nudge, correct, correct_dry_run, reuse, no_shading, stock, process and max_failures. The --nudge amount goes only to stdout (412-413), and library entries carry no nudge either.

</details>

<a id="cli-operator-tools-cli-25"></a>

### CLI-25 -- scan.py: a single --exposure-scale value is dropped for a bracket and also disables --auto-exposure

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:143-148`, `tools/scan.py:211-215`, `rps7200/direct.py:2373-2379`

With `--bracket N --exposure-scale 0.9 --auto-exposure`, neither setting reaches the bracket. The scalar becomes None and metering is switched off, so the ladder is built on the device's unmetered defaults with no per-channel balance. That is the orange mask left in for a negative, while the tool's message claims the scalar is in force. The absolute scalar is irrelevant anyway, because the ladder pins its top to the timer ceiling. Only the per-channel ratio matters, which the message does not explain.

**Evidence (from the code):**

```text
`auto_exposure=args.auto_exposure and not args.exposure_scale, exposure_scale=(list(exposure_scale) if isinstance(exposure_scale, list) else None)`. scan_bracket: `if exposure_scale is not None: ... elif auto_exposure: ... else: scales = [1.0, 1.0, 1.0]`. The tool prints "--exposure-scale overrides --auto-exposure".
```

**Failure scenario:** A negative is bracketed with `--auto-exposure --exposure-scale 1`. All passes are taken at [1,1,1] ratios, blue sits far down the range, and the merge gains little.

**Fix:** For brackets, reject a scalar --exposure-scale (or keep --auto-exposure when only a scalar is given), and say plainly that only per-channel ratios matter.

<details><summary>Second reader's check</summary>

For a scalar --exposure-scale, `exposure_scale=(list(...) if isinstance(exposure_scale, list) else None)` passes None, and `auto_exposure=args.auto_exposure and not args.exposure_scale` passes False (scan.py:211-215). scan_bracket then falls to `scales = [1.0, 1.0, 1.0]` (direct.py:2373-2377), while the tool prints '--exposure-scale overrides --auto-exposure'.

</details>

<a id="cli-operator-tools-cli-26"></a>

### CLI-26 -- Manifest and migration writes are in place and non-atomic; migrate-raw holds every decoded image in RAM; migrate-direction exits 0 on errors

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/scan_roll.py:326-329`, `tools/library.py:159-207`, `tools/library.py:218-246`

A crash or power loss while roll.json or survey.json is being rewritten truncates it, and hold_from_walk then refuses the walk ('cannot be read'). migrate-raw --write overwrites scan.tif and then scan.json. An interruption between the two leaves corrected-looking metadata over raw pixels, or a checksum mismatch. In both modes it keeps every planned decode in memory, which is multiple GB on a large legacy library. migrate-direction returns success even when some entries could not be migrated.

**Evidence (from the code):**

```text
checkpoint(): `manifest_path.write_text(json.dumps(manifest, ...))`. migrate-raw: `planned.append((path, plain, applied, stored_shape))` for every entry before any write, then `tiff.write(str(path / "scan.tif"), plain, ...)` and `(path / "scan.json").write_text(...)` over the originals. migrate-direction counts `left += 1` on exceptions but `return 0`.
```

**Failure scenario:** `tools/library.py migrate-raw --write` on 200 legacy 1800-3600 dpi entries is OOM-killed while collecting. Or a laptop sleeps mid-roll during checkpoint() and survey.json is left as 0 bytes.

**Fix:** Write to a temp file and os.replace. Stream migrate-raw: decode, compare and write one entry at a time. Return 1 from migrate-direction when any entry was left because of an error.

<details><summary>Second reader's check</summary>

checkpoint() uses write_text in place (scan_roll.py:326-329). migrate-raw accumulates `(path, plain, applied, stored_shape)` for every entry before writing (tools/library.py:185), then overwrites scan.tif and then scan.json non-atomically (198-206). migrate-direction counts errors in `left` but falls through to `return 0` (246).

</details>

<a id="cli-operator-tools-cli-27"></a>

### CLI-27 -- Makefile/tasks comments overstate: the GUI also sends READ_STATE at launch, and run-sheet needs a gitignored folder

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `Makefile:50-53`, `tasks.py:150-152`, `tasks.py:168`, `rps7200/session.py:1293-1307`, `rps7200/session.py:1996-2009`

**Doc claim:** Makefile:50-53; CLAUDE.md:16 and CLAUDE.md:217 '`make run-demo` and `make run-sheet` are the exercises'

At launch the window sends INQUIRY and READ_STATE. That is harmless per CLAUDE.md, but it is not 'nothing else'. `make run-sheet`, which CLAUDE.md lists as one of the two exercises reaching code with no device, only works on a machine that has rolls/aligned-strip; on a fresh clone or in CI it exits through ap.error.

**Evidence (from the code):**

```text
Makefile: "Opening the window claims the device and asks it who it is, and nothing else". ScanSession._run: `info = self._scanner.inquiry()` followed by `self._report_position()`, which calls `self._scanner.position()` → `read_state(retries=1)`. tasks.py: `DEMO_ROLL = "rolls/aligned-strip"`, and .gitignore has `rolls/`.
```

**Failure scenario:** A contributor on a fresh checkout runs `make run-sheet` as CLAUDE.md suggests and gets 'no roll folder at rolls/aligned-strip'. The exercise meant to catch demo regressions cannot run anywhere but Stefan's machine.

**Fix:** Correct the comment. Ship a small synthetic walk fixture, or make run-sheet fall back to a generated one, so the exercise runs everywhere.

<details><summary>Second reader's check</summary>

ScanSession._run sends inquiry() and then _report_position(), which reads READ_STATE (session.py:1293-1307, 1996-2009), so 'and nothing else' (Makefile:50-51, tasks.py:150-152) is inaccurate. That is harmless per CLAUDE.md. DEMO_ROLL = 'rolls/aligned-strip' (tasks.py:168). rolls/ is gitignored and absent in this checkout, and gui.py:8171-8177 calls ap.error when the folder is missing, so run-sheet cannot run on a fresh clone or in CI.

</details>

<a id="cli-operator-tools-cli-28"></a>

### CLI-28 -- A mid-scan read failure is caught per frame and the roll keeps commanding the transport

**Severity** low · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/direct.py:3662-3686`, `rps7200/direct.py:1490-1499`, `tools/scan_roll.py:160-161`

A read abandoned mid-pass is the documented wedge. The roll records a failure, advances the film and prescans the next frame, up to `--max-failures` (default 3). So after a read failure the operator loses film position (advances that may or may not land), time, and possibly a clean shutdown. There is no distinction between 'frame failed cleanly' and 'read abandoned; device state unknown'.

**Evidence (from the code):**

```text
`except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc: failures += 1 ... yield RollFrame(..., error=str(exc), ...)`, and then `if not keep_going(): return`, where keep_going calls `self.advance()`. read_planes raises `ScanReadError(f"no data for {idle_timeout:.0f}s ...")` in the middle of a pass.
```

**Failure scenario:** The idle timeout fires during frame 8's pass. The roll sends SLIDE_NEXT and a prescan to a device still mid-scan, both time out, and the roll only stops at the third failure.

**Fix:** Stop the roll immediately when a read failed after data had begun to arrive, or when ScanReadError/TimeoutError came from read_planes. Report it as 'device state unknown, power-cycle before resuming at frame N'.

<details><summary>Second reader's check</summary>

The per-frame except (direct.py:3662-3686) catches ScanReadError and TimeoutError from a mid-pass read the same way as a clean refusal, and then keep_going() calls advance(). There is no 'device state unknown' abort. Damage is bounded: a dead device makes advance() return None, which ends the roll.

</details>

<a id="cli-operator-tools-cli-30"></a>

### CLI-30 -- Test gaps: no test of Ctrl-C, resume, duplicates identity or reconstruct classification; type checking excludes all of tools/

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_scan_roll_tool.py:187-227`, `tests/test_scan_tool.py`, `tests/test_library_tool.py:43-81`, `tasks.py:61-65`, `pyproject.toml:[tool.coverage.run]`

Several paths have no test:
- Ctrl-C (CLI-01), exception mid-bracket in scan.py (CLI-02) and resume preserving roll.json (CLI-04)
- `--no-shading` on a roll (CLI-05)
- duplicates with empty film notes (CLI-06)
- the reconstruct CLI's classification and per-entry robustness (CLI-17)
The operator tools are also outside ty and coverage, so a renamed keyword (the historical `path`→`paths` bug) is caught only if a fake happens to exercise it.

**Evidence (from the code):**

```text
test_a_shading_failure_files_the_frames_already_scanned only raises ShadingUnavailable (an Exception). test_library_tool.py covers only `list`. `TY_ARGS = ["check", "--exclude", "tests/", "--exclude", "tools/"]`; coverage `source = ["rps7200"]`.
```

**Failure scenario:** A future change reintroduces an unfiled-frames path through a BaseException, and the suite stays green.

**Fix:** Add fake-driven tests for each scenario above. Bring tools/scan.py, scan_roll.py and library.py under ty, even if gui.py stays excluded.

<details><summary>Second reader's check</summary>

test_library_tool.py tests only `list`. No test raises KeyboardInterrupt or BaseException (grep). TY_ARGS excludes tools/ (tasks.py:61-65), and coverage source is ['rps7200'] (pyproject.toml:116-117).

</details>

<a id="cli-operator-tools-cli-a3"></a>

### CLI-A3 -- scan_roll.py --dry-run writes prescan TIFFs on the scanning thread with the device open; the window routes the same writes through FrameWriter

**Severity** low · **Category** design · **Verdict** found-by-verifier

**Where:** `tools/scan_roll.py:498-510`, `rps7200/session.py:1805-1817`

The CLI and the window disagree on the rule about local I/O with the device open. The write is small and uncompressed, so the practical risk is low. It remains a divergence from the shared code path the tools claim to follow, and these writes also bypass the library (CLI-03).

**Evidence (from the code):**

```text
scan_roll.py: `pre = out / f"prescan{number:02d}.tif"; tiff.write(str(pre), frame.prescan)` inside the `for frame in s.scan_roll(...)` loop. session.py comment: "Written by the writer thread, not here: it is only ~370 KB, but nothing local happens on the scanning thread with the device open."
```

**Failure scenario:** Writing to a slow or network rolls/ folder stalls the generator between passes while the session is open and idle, which is the state CLAUDE.md associates with wedges.

**Fix:** Submit dry-run prescans to FrameWriter with paths=[pre] and library filing (raw_image=frame.raw_prescan), as ScanSession does.

<a id="cli-operator-tools-cli-a4"></a>

### CLI-A4 -- tests/test_gui.py docstring claims scan.py and scan_roll.py write the 1_/2_/3_ comparison files; neither does

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `tests/test_gui.py:101-106`

The rationale for the test is stale. Only make_comparison.py writes these files. A reader looking for which tool regenerates the eye-test files is pointed at the wrong two.

**Evidence (from the code):**

```text
"`tools/scan.py` and `tools/scan_roll.py` both write 1_/2_/3_*.tif to the repo root and clobber each other." grep for '1_nothing_done' finds it only in tools/make_comparison.py, CLAUDE.md, README and docs.
```

**Failure scenario:** A contributor regenerates the comparison files by running scan.py, as the test docstring implies, and finds nothing written.

**Fix:** Correct the docstring to name tools/make_comparison.py.

<a id="cli-operator-tools-cli-29"></a>

### CLI-29 -- The operator tools never send the vendor session opening (0xE7 ...) that every hardware probe sends

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/direct.py:1726-1753`, `tools/scan.py:160-168`, `tools/scan_roll.py:353-369`, `rps7200/session.py:1293-1300`

Measurements behind many of the constants (fast IR, gain, exposure headroom, hold behaviour) were taken by probes that open the device differently from scan.py, scan_roll.py and the window. By CLAUDE.md's own rule, a finding from a bypass path is provisional until reproduced through tools/, and here the opening sequence itself differs. It is also a candidate explanation for the lazy-calibration stall noted in scan_roll.py.

**Evidence (from the code):**

```text
`grep session_start()` finds it only in tools/transport_truth.py, gain_probe.py, fast_ir_probe.py, byte14_probe.py, roll_registration_walk.py, exposure_probe.py and hold_probe.py. session_start's docstring: "0xE7 ... appears at the start of every captured session and only in the two captures that contain a successful calibration -- so it may be what puts the scanner into a state where calibration is accepted."
```

**Failure scenario:** A behaviour measured by a probe (for example, calibration accepted and stable) does not reproduce in scan_roll.py because the operator path skipped 0xE7. The discrepancy gets chased as a driver bug.

**Fix:** Decide on one opening sequence. Either call session_start() from the operator paths, or drop it from the probes, and record in meta which one was used.

<details><summary>Second reader's check</summary>

grep shows session_start() called only from the probe tools (transport_truth, gain_probe, fast_ir_probe, byte14_probe, roll_registration_walk, exposure_probe, hold_probe), never from scan.py, scan_roll.py or ScanSession. calibrate_shading's own docstring (direct.py:1768-1771) says 0xE7 'is refused with ASC 0x20', which sits oddly with session_start's speculation that 0xE7 enables calibration. Either way the opening sequences differ between the probes and the operator paths.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry pixels | <--library>/<YYYYmmddTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-n]/scan.tif | TIFF, uint16 (uint8 for 8-bit passes), (H,W,3\|4) R,G,B[,I] | raw decode (upright via decode_index); at 7200 dpi also stagger-realigned with 4 rows trimmed, which is not recorded | library.save (library.py:167) via tools/scan.py:239 after close(), and via session.FrameWriter._write (session.py:1090) for scan_roll frames; rewritten in place by `tools/library.py migrate-raw --write` (library.py CLI:198) and migrate-direction | library.load/corrected, library.reconstruct, verify (sha256), migrate-raw, GUI/demo | Lossless for the decode of the stored bytes; not reproducible by reconstruct at 7200 dpi (CLI-10) |
| Raw bytes as received | <entry>/raw.bin.gz | gzip level 6 of the exact concatenated READ payloads (index format: 2-byte channel tag + line), sha256 of the uncompressed bytes in scan.json raw.sha256; layout in raw.layout | raw, byte-exact | library.save from capture_record()['raw'] (last_raw set in read_planes when keep_raw) | library.read_raw/decode_raw/reconstruct, verify | yes, byte-exact; only for passes the tool files (not probes, dry-run prescans, verification prescans or calibration) |
| Shading reference | <entry>/shading.npz and calibration/shading.npz (--reference) | np.savez_compressed: pixels_per_line, channels, ref<c>, mean<c>, dark<c>, darkmean<c>; no timestamp or provenance | derived (calculate_shading output); the calibration pass's raw bytes are discarded | library.save (reference.save); DirectScanner.save_shading via ensure_shading after each calibration (overwrites calibration/shading.npz) | library.corrected/reconstruct (legacy); ensure_shading(reuse=True) for --reuse | exact copy of the derived arrays, but not re-derivable from source (CLI-09) |
| CCD mask of the pass | <entry>/ccd_mask.bin | raw bytes from GET CCD MASK, one byte per calibration column (5172) | raw | library.save from capture_record()['ccd_mask'] (the per-pass mask read in scan()) | library.corrected/reconstruct | yes; the calibration pass's own mask is not stored |
| Roll frame prescan | <entry>/prescan.tif | TIFF uint8 (H,W,3), 300 dpi by default | CORRECTED (shading applied), and unlabelled | library.save(prescan=frame.prescan) via FrameWriter from tools/scan_roll.py:562 | GUI, registration studies | no raw bytes, no raw pixels, no sha256, not verified (CLI-19) |
| Entry record | <entry>/scan.json | JSON: id, created, image{shape,dtype,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{fixed key subset}, device_settings, metering, registration, calibration{report,skipped,...}, prescan{read_direction,carriage_state}, film, tags, provenance | metadata | library.save, written last and non-atomically; rewritten by migrate-raw / migrate-direction | library.entries (glob */scan.json), all tools/library.py actions, GUI | drops inquiry, bracket_*, roll_index/roll_position; roll frames record exposure_metered=false with no metering (CLI-11, CLI-12) |
| Library index | <--library>/index.json | JSON list summary | derived | library.reindex after every save, duplicates --delete, migrate-*, reindex | humans; nothing load-bearing (entries() globs) | derived, safe to delete |
| Roll manifest | <--out\|rolls/<roll>>/roll.json | JSON: roll, numbering, started, settings{dpi,infrared,meter,film,dry_run,start_at,frames,prescan_resolution}, held, frames[{number,index,transport_position,registration,error,entry,file,shape,duration_s,exposure}], finished, duration_s, stopped | metadata | tools/scan_roll.py checkpoint() after placement and after every frame; overwritten wholesale by any later run into the same folder | hold_from_walk (--approved fallback), GUI read_survey, session.renumbered | not atomic; lost on resume (CLI-04); omits fast_ir/nudge/correct/reuse (CLI-24) |
| Walk manifest | <--out\|rolls/<roll>>/survey.json | JSON, same shape as roll.json with frames[].prescan / prescan_before | metadata | tools/scan_roll.py --dry-run checkpoint() | hold_from_walk, walked_prescans, GUI read_survey | overwritten by the next walk into the same folder; not atomic |
| Walk prescans | <roll dir>/prescanNN.tif, prescanNN-before.tif | TIFF uint8 (H,W,3) | CORRECTED; from the CLI, not oriented | tools/scan_roll.py:499-510 (dry run only, on the main thread with the device open) | hold_from_walk as --approved references; GUI read_survey (un-orients) | no library entry, raw bytes or reference from the CLI path; the only copy (CLI-03) |
| Roll delivered frames | <roll dir>/frameNN.tif | TIFF via export.write, uint16, 3/4 channels | corrected (not mono even for --film bw in the CLI) | FrameWriter._write from tools/scan_roll.py submit(paths=[path]) | operator / NegPy | delivery copy; overwritten by a later roll of the same name |
| Single-scan delivery + sidecar | --out (default ./scan.tif \| .jpg [+ .dng for IR]) and <out stem>.json | TIFF uint16/uint8 or JPEG 8-bit (+ 4-sample DNG); sidecar JSON = scan meta (+bracket{passes,ratios,stops,stats}, mono_channel) | corrected; bracket-merged; optionally monochrome | tools/scan.py:278-281 after library filing | operator; the sidecar is the only record of a bracket merge | no (8-bit JPEG, merge); overwritten silently |
| Comparison files | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif, previews/cmp_before.png, previews/cmp_after.png | TIFF uint16; PNG uint8 half-size | input TIFF as given / destripe output / inverted destripe | tools/make_comparison.py:120-136 (CWD) | Stefan by eye | not the production correction (CLI-08) |
| Debug spool (not used by these CLI tools) | $TMP/rps7200-debug-*/NNN-image.npy, NNN-raw.bin | npy + raw bytes | raw | DirectScanner._debug_capture when debug is on (filing_load_test with RPS7200_DEBUG=1) | _debug_flush → library.save after close | yes; scan.py/scan_roll.py force debug=False |
| Build caches | .pytest_cache, .ruff_cache, .coverage, build, dist, **/__pycache__ | tool caches | n/a | pytest/ruff/coverage | tools; removed by tasks.py clean (rglob also walks .venv and library/) | n/a |

**Second reader's corrections to this table:**

Corrections to the persisted_state table:

- **Roll frame prescan (`<entry>/prescan.tif`)** is corrected and unlabelled on BOTH paths. The window's roll also passes `prescan=rf.prescan` (session.py:1887), so this is not only 'from tools/scan_roll.py:562'.
- **Shading reference npz** also stores a `dark_channels` array (shading.py:79-81). `calibration/shading.npz` is not overwritten when a calibration yields no reference (save_shading returns None), but the tools still carry on (see CLI-A1). It is written on dry runs too, because scan_roll calibrates before walking.
- **Library index.json** is rewritten non-atomically by every library.save (reindex re-reads every scan.json), including from the FrameWriter thread. Interrupting it can truncate index.json; that is harmless because nothing load-bearing reads it.
- **Entry scan.json** is also rewritten in place by migrate-direction (library.py:633-635), which can also rewrite `prescan.tif`. save() reuses an existing directory that lacks scan.json (collision check tests only scan.json), so an orphan from an interrupted save is silently overwritten.
- **scan.tif at 7200 dpi** is realigned and trimmed by NATIVE_COLUMN_STAGGER_LINES=4 rows, and raw.layout.lines still says the untrimmed count. Reachable only via `scan.py --no-shading`: a full-frame shaded 7200 dpi pass raises ShadingUnavailable, and the GUI never passes shading=False.
- **Single-scan delivery**: filing into the library happens BEFORE export.write. A bad --out suffix therefore leaves the entries filed while the delivered file and sidecar are never written. The sidecar path is `out.with_suffix('.json')`, so with the default `--out scan.tif` it is `./scan.json` in the CWD.
- **Walk prescans**: from the window they are written oriented (rotation/flip composed in session._file). The CLI writes them unoriented and never un-orients when reading (CLI-13).
- **Comparison files**: previews/ is not created, so the PNGs fail on a fresh checkout. The inputs are arbitrary TIFFs, not library entries.
- **Debug spool**: filing_load_test's documented invocation sets RPS7200_DEBUG=0, so in practice it does not spool either.

## What the operator can do

- Scan one frame with `uv run python tools/scan.py --dpi 1800 [--ir] --stock ... --frame N`. The main pass is filed raw in library/ after the device closes, and the corrected file goes to --out with a .json sidecar.
- Take a 2-9 pass exposure bracket with `tools/scan.py --bracket N --stops S`. Each pass is filed raw, and a merged corrected file is delivered.
- Walk a strip without full scans using `tools/scan_roll.py --dry-run` (prescans plus survey.json in rolls/<date>). Then roll it with `tools/scan_roll.py --dpi D [--ir] --frames K --roll NAME --stock ...`.
- Start or restart a roll at a strip position with `--start-at N`. The tool winds or advances the film there by the transport counter and refuses if it cannot tell.
- Hold each frame to positions proposed from an earlier walk with `--approved <walk folder>`, or let the roll aim itself with `--correct` / `--correct-dry-run`.
- Choose metering per roll with `--meter each|once|none`, reuse the cached reference with `--reuse`, and cap consecutive failures with `--max-failures`.
- Inspect and maintain the library: `tools/library.py list | verify | reconstruct | duplicates [--delete --keep N] | migrate-raw [--write] | migrate-direction [--write] | reindex`, or `make verify` / `make reconstruct`.
- Run the developer targets identically on macOS, Linux and Windows: `make test | test-all | lint | type | fix | all | run | run-demo | run-sheet | clean`, or `python tasks.py <target>`.
- Install packaging/60-rps7200.rules on Linux to use the scanner without root.
- Measure CPU-load interference with `tools/filing_load_test.py` (its verdict is not usable; see CLI-07).

## What the operator should not do

- Do not press Ctrl-C, close the terminal, or let a harness kill scan.py or scan_roll.py while a pass is being read. The read is abandoned (wedge), scan.py loses every pass not yet filed, and scan_roll loses up to three queued frames.
- Do not run `tools/scan_roll.py --no-shading`. It does not skip calibration; it forces the lazy in-prescan calibration the tool's own comment says stalls the device, and frames are still corrected.
- Do not run `tools/library.py duplicates --delete` on a library containing scan.py scans without --stock/--frame, debug-filed entries, or two same-day rolls under the default --roll. Different pictures are reported as duplicates and their raw bytes are deleted.
- Do not resume a roll into its existing folder expecting the earlier roll.json to survive: `--start-at N` with the same --roll rewrites it from scratch.
- Do not hold a roll to a walk with `--approved` when the walk was taken at a different prescan dpi than --prescan-dpi (default 300), or in the window with a rotation or flip set.
- Do not run a `--dry-run --approved X` into the same folder as X (the default is rolls/<today>): it overwrites the walk's survey.json and prescans, which the CLI never filed in the library.
- Do not ask for --dpi above 3600 without --no-shading in scan.py, or at all in scan_roll.py. The refusal comes only after calibration (and metering).
- Do not calibrate with an empty transport: scan.py calibrates immediately without asking.
- Do not use `--reuse` across a power cycle or lamp change. Entries will not say the reference was stale.
- Do not use `--library ''` with scan.py (it files into the current directory), and do not run the tools from any directory but the repo root.
- Do not judge a correction change by make_comparison.py's 2_corrected.tif. It is destripe, which nothing ships.
- Do not rely on filing_load_test.py's 'looks safe' verdict.

## Mistakes nothing guards against

- A KeyboardInterrupt, SystemExit or SIGTERM in scan_roll.py bypasses writer.finish(), and the daemon FrameWriter thread is killed with frames queued.
- Any exception in scan.py during a bracket (USB error, MemoryError, Ctrl-C) discards every already-scanned pass held in `pending`.
- scan_roll --no-shading triggers lazy calibration inside the first prescan (the documented stall), and frames are still shading-corrected.
- `--dpi` accepts any integer (1000, 4000, 70000). Values above 3600 fail only after calibration and metering; values off the ladder are sent to the device.
- `--stops 0` and a bad `--out` extension (e.g. .png) are rejected only after the scan has run.
- `--exposure-scale` accepts the wrong number of values or zero and negative values without complaint. A scalar with --bracket is silently dropped and disables --auto-exposure.
- `--frames -1` calibrates and overwrites the day's roll.json with an empty manifest; `--start-at 0` is silently frame 1.
- Resuming with `--start-at` overwrites roll.json. A resume on a different day without `--roll` goes to a new rolls/<date> and a new film.frame label.
- `--approved` ignores the walk's prescan_resolution, rotation, flip and approved.json.
- A blank mid-roll frame ends the roll with exit 0 and fewer frames than --frames.
- scan.py `--library ''` writes entries and index.json into the current directory, while scan_roll treats '' as 'do not file'.
- `tools/library.py duplicates --keep 0` crashes with IndexError, and `--keep -1` deletes one per group; neither is validated.
- `tools/library.py reconstruct` stops at the first entry with a missing scan.tif, and it reports entries awaiting migrate-direction as regressions.
- make_comparison.py raises after writing its TIFFs when previews/ does not exist, and ignores `--help`.
- filing_load_test.py with `--rounds 0` raises StatisticsError, and with fewer than 3 rounds prints no verdict at all.

## Dataflow notes

SINGLE SCAN (tools/scan.py):
1. argparse (scan.py:37-119). Up-front checks only for IR vs film (120-128) and bracket count (131-141); exposure_scale is parsed without validation (143-146).
2. DirectScanner(debug=False) (160) → Transport.open → inquiry (161).
3. ensure_shading (167 → direct.py:572):
   - skip: no reference.
   - reuse: ShadingReference.load(calibration/shading.npz) (direct.py:600).
   - otherwise calibrate_shading (direct.py:1755): the calibration block is read, parsed by shading.calculate_shading into dark/light per-column arrays (direct.py:1950), and the raw block is discarded (keep_data False). save_shading overwrites calibration/shading.npz.
4. scan() (direct.py:2423):
   - optional auto_exposure: RGB probe passes via scan(keep_raw=True), whose bytes are overwritten by the next pass and never filed.
   - READ_STATE → shading width check (refuses >5172 columns) → set_scan_frame/cmd_17/set_gain_offset(scaled)/set_mode/SLIDE_INIT → start_scan.
   - read_planes (1440): paced READs; the byte blob goes to last_raw + last_raw_layout when keep_raw; decode_index gives upright (H,W,C) plus read_direction.
   - At 7200 dpi, _realign_native_column_stagger (2695) runs; then raw_pixels = image (2708) → apply_shading(image, reference, per-pass ccd_mask) → corrected image returned. last_pixels_raw = raw_pixels.
5. hold() (scan.py:176) appends capture_record() (reference, ccd_mask, raw bytes, raw_layout), inquiry, meta and the raw pixels to the in-memory `pending`. Brackets do this through the on_pass callback per pass (direct.py:2406).
6. with-exit → close() releases the interface (no drain).
7. For each pending item, library.save (library.py:131): scan.tif raw, prescan none, shading.npz (derived), ccd_mask.bin, raw.bin.gz (gzip of exact bytes + sha256), then scan.json (fixed key subset; inquiry and bracket_* dropped), then reindex.
8. Bracket: merge_bracket over the corrected RGB frames plus the IR plane of the last pass (scan.py:248-265).
9. Optional to_monochrome → export.write(--out) (tif, or jpg + dng) → <out>.json sidecar holding the full meta including the bracket merge record.

ROLL (tools/scan_roll.py):
1. argparse → optional hold_from_walk (200-261): survey.json/roll.json → session.walked_prescans → tiff.read(prescanNN.tif) with no un-orient → frame_edges.propose_centred → session.Approved(offset_mm, reference).
2. A manifest dict is built in memory (298-324), and FrameWriter() starts its daemon thread (343; session.py:1032).
3. DirectScanner(debug=False) → inquiry → wait_warm → optional session.rewind → session.seek(start_at-1) (reads READ_STATE, advances or retreats, re-reads) → optional nudge via session.plan_nudges/param_for_mm (in mm) → calibrate() → ensure_shading → mkdir + checkpoint() writes roll.json or survey.json.
4. DirectScanner.scan_roll generator (direct.py:3292), per frame:
   - position() → place_on_strip → prescan() (scan(shading=True, 8-bit); raw_prescan kept on the RollFrame) → frame_contrast/registration.
   - Optional _hold_to_approved/_aim_frame: nudges and verification prescans, none of them filed.
   - auto_exposure per --meter (probes not filed; meta gets exposure_metered False and no metering) → scan(shading=True) → yield RollFrame(image corrected, raw_image, meta, prescan corrected, raw_prescan, registration).
5. The CLI consumes each yielded frame:
   - Dry run: tiff.write prescanNN.tif (+ -before) on the main thread with the device open. No library entry.
   - Real roll: writer.submit(paths=[frameNN.tif], image, raw_image, meta, prescan (corrected), prescan_meta, capture=s.capture_record() read before the generator resumes, inquiry, tags, FilmNotes(frame='<roll>/<NN>')).
   - In both cases the frame record is appended and checkpoint() rewrites the manifest.
6. FrameWriter thread (session.py:1060): export.write(frameNN.tif, corrected, oriented), then library.save(raw_image, ..., prescan=corrected, **capture) while the device scans the next frame.
7. After the with block, but only on normal exit or an Exception: writer.finish() joins the thread → entry paths merged into the manifest → final checkpoint → exit code 1 on any failed frame or trouble.

LIBRARY CLI (tools/library.py):
- entries() globs <root>/*/scan.json.
- list prints them; verify checks sha256 of scan.tif and raw, and the presence of the reference and mask.
- reconstruct uses read_raw → decode_index (no 7200 realignment) → compares with scan.tif.
- duplicates uses signature() (film notes + scan settings) → prunable → shutil.rmtree.
- migrate-raw uses decode_raw → tiff.write over scan.tif → rewrites scan.json.
- migrate-direction delegates to library.migrate_direction. All actions end with reindex.

OTHER TOOLS AND BUILD:
- make_comparison.py: reads two TIFF paths → flat_defect_sigma/find_column_defects → destripe → writes three TIFFs to the CWD and two PNGs to previews/. There is no library or shading path.
- filing_load_test.py: DirectScanner(debug from env) → alternating 300 dpi 8-bit FULL_FRAME passes with shading=False, with and without a gzip thread → wall-time means → tautological verdict.
- Build: `make <t>` → `uv run python tasks.py <t>` → subprocess without a shell (pytest/ruff/ty from the venv; gui.py, library.py). CI runs lint, ty (rps7200 only), then pytest with and without tifffile on ubuntu, macos and windows. The udev rule tags 05e3:0144 with uaccess before 73-seat-late.
