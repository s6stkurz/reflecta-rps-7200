# What the operator can do, should not do, and what nothing guards

[Back to the summary](README.md)

Consolidated from what the readers traced in the window, the CLI tools and the library
tools. The first part is a short set of **operating rules until the fixes land**. The
second part lists the guard each rule needs, so that the rule can be retired. The
readers' full per-area lists are at the bottom.

## Operating rules until the fixes land

### The scanner

1. **Never interrupt a pass.** No Ctrl-C, closing the terminal, killing the window, or
   letting a harness time out a foreground run. The read is abandoned (the documented
   wedge), `scan.py` loses every pass not yet filed, and `scan_roll.py` loses queued
   frames. Background anything over about 8 minutes. ([P14](problems/P14-failure-paths-keep-driving-device.md))
2. **After any USB, read or timeout error, stop and power-cycle.** Do not press Scan
   again, and do not let a roll continue. Nothing in the software marks the device as
   suspect; the roll advances and rescans on its own. (P14)
3. **Do not use Force abort** unless the device is already lost. It closes libusb under a
   running transfer. (P14)
4. **Do not run `tools/scan_roll.py --no-shading`.** It does not skip calibration; it
   moves it into the in-scan path recorded as stalling. The same happens with Prescan or
   Scan queued behind a Calibrate that fails. ([P15](problems/P15-lazy-calibration-inside-scan.md))
5. **Do not rely on `--no-fast-ir`** being protected by `INFRARED_FLOOR_S`. The real read
   guard is 120 s, below the ~220 s an untied IR pass takes. ([P16](problems/P16-timeouts-and-runtime-budget.md))
6. **Make sure film is in the transport before *any* calibration.** The window's
   Calibrate button, a bare Return in "Calibrate first", `tools/scan.py` and
   `tools/uniformity.py` all calibrate without asking. ([P17](problems/P17-calibration-without-asking.md))
7. **Do not set `RPS7200_MAX_WINDOW`.** `0` or less hangs every read, and more than 32 KB
   reproduces the SANE stall.
8. **Do not pick dpi above 3600** in the window or the CLIs. The host refuses only after
   calibration and metering have run. ([P06](problems/P06-7200dpi-realignment-not-recorded.md))

### The library

9. **Do not run `tools/library.py duplicates --delete`.** It deletes different
   photographs (empty film notes, same-day unnamed rolls) and deliberate ladders.
   ([P11](problems/P11-duplicates-delete-destroys-scans.md))
10. **Do not run `migrate-raw --write`** until `reconstruct` output is understood. It
    overwrites any mismatching `scan.tif` with today's decode. ([P12](problems/P12-library-maintenance-tools.md))
11. **Keep the output folder on a disk that is always there and has room.** A failed
    delivered copy costs the *library entry* too, and a full disk does not stop a roll.
    ([P04](problems/P04-delivered-copy-before-library-entry.md), [P24](problems/P24-disk-full-and-quitting.md))
12. **Always start the window and the tools from the repository root.** Every default
    path is relative, and a new library silently appears elsewhere.
13. **Do not rename, copy or move entry directories, or edit `scan.json` by hand.** Tools
    find entries by `record["id"]`, and a JSON syntax error makes an entry vanish from
    every check. ([P10](problems/P10-non-atomic-writes.md))
14. **In ad-hoc debug scripts, always use `with DirectScanner(...)`** and let it close.
    A killed script loses every spooled scan. Pass `keep_raw=True` to every `scan()` and
    `prescan()`, or the entry may get another pass's bytes.
    ([P02](problems/P02-debug-filing-stale-raw-bytes.md), [P03](problems/P03-debug-spool-not-crash-safe.md))
15. **Know that `RPS7200_DEBUG=1` does nothing** for the window, `tools/scan.py` and
    `tools/scan_roll.py`. They force debug off. ([P08](problems/P08-passes-never-filed.md))

### Rolls and walks

16. **Give every strip its own roll name**, made only of `A-Z a-z 0-9 . _ -`, and keep
    it the same from walk to commission. An empty name shares `rolls/<today>/` with
    every other unnamed strip of the day. `approved.json` then goes to `rolls/roll/`, and
    other characters send it to yet another folder. ([P18](problems/P18-roll-folder-identity.md))
17. **When continuing a reopened roll, retype its exact folder name** into the roll box.
    Reopening does not restore it. (P18)
18. **Leave the Film panel's "frame" note empty before a roll.** It is stamped on every
    frame. That breaks Export and makes every frame one "duplicate".
    ([P21](problems/P21-roll-to-library-join.md))
19. **Do not rotate or flip while a walk or roll runs.** Arrange afterwards, in the sheet.
    ([P20](problems/P20-rotation-during-walk.md))
20. **Close the old contact sheet before walking a new strip.** Do not open a roll from
    Rolls... while a walk runs, and do not open two rolls in one session.
    ([P22](problems/P22-contact-sheet-state.md), [P25](problems/P25-reopened-frames.md))
21. **Do not press Ctrl/Cmd-B, aim-click a prescan, or double-press a button while
    anything runs.** These bypass the busy guard, queue a second job, and **cancel a Stop
    you already pressed**. ([P23](problems/P23-busy-guards-and-stop.md))
22. **Press Stop and wait before quitting.** Quitting mid-roll waits for the whole roll,
    and quitting during Save all or Export truncates files. (P24)
23. **Resume a CLI roll only after copying `roll.json` aside.** `--start-at` overwrites it.
    ([P19](problems/P19-roll-manifests.md))
24. **Use `scan_roll.py --approved` only with a walk taken at the same prescan dpi and no
    rotation or flip.** ([P31](problems/P31-framing-geometry-and-detectors.md))

### The demo

25. **Never combine `--demo` with a real `--library`, `--rolls` or `--out`.** Demo files
    enter real folders unmarked and can overwrite a real walk. **Never use `--look-only`
    without `--demo`.** It drives the real scanner. **Never Delete a reopened frame in
    the demo.** It removes a real entry. ([P26](problems/P26-demo-files-corrected-as-raw.md),
    [P28](problems/P28-look-only-drives-real-scanner.md), P25)
26. **Do not use demo entries as evidence.** Their pixels are corrected, and their bytes
    belong to another entry. (P26)

### Measuring

27. **Do not judge a correction by `2_corrected.tif`.** It is `destripe` on an arbitrary
    TIFF, not what the driver ships. Do not trust `filing_load_test.py`'s "safe".
    ([P30](problems/P30-comparison-files-and-metrics.md))
28. **Do not use `--bracket` with `--ir`, or with a single `--exposure-scale`.**
    ([P29](problems/P29-bracket-merge.md))

## The guard each rule needs

| Rule | Guard that retires it |
|---|---|
| 1-3 | Device-suspect state after a failed read; Ctrl-C finishes the read and always joins the writer; non-daemon threads; no libusb close from the UI thread (P14) |
| 4 | Remove the lazy calibration from `scan()`; thread `shading` through `scan_roll`/`prescan`/metering (P15) |
| 5, 8 | Per-pass timeout from the pass; validate dpi before opening the device; do not offer 7200 until it works (P16, P06) |
| 6 | One "Film in the transport?" confirmation before every calibration; log the media bit (P17) |
| 7 | Validate `RPS7200_MAX_WINDOW` (1..32768) at import |
| 9-10 | `--delete` only for identical raw-byte hashes, to `.trash/`; `migrate-raw` only where one shading explains the difference (P11, P12) |
| 11 | Library entry first, delivered copies second; free-space check per frame; a failed filing stops the roll (P04, P24) |
| 12 | Default paths anchored to the repository (or a configured home), not the CWD |
| 13 | Locate entries by directory; `verify` reports unreadable `scan.json` (P10) |
| 14-15 | Clear `last_raw` per pass; debug keeps every pass's own bytes; self-describing spool; `debug=None` honours the environment (P02, P03, P08) |
| 16-18 | One `roll_dir()`; unique default names; reopen restores the name; join on a recorded roll id, not the frame note (P18, P21) |
| 19 | Per-file orientation in the manifest, or orientation frozen during a walk (P20) |
| 20, 22 | Sheet keyed to its walk; unique seq for reopened frames; Quit offers "stop after this frame" (P22, P25, P24) |
| 21 | Busy check in the session; `submit()` never clears Stop; Stop clears the queue (P23) |
| 23 | `--start-at` merges the manifest; atomic manifests (P19) |
| 24 | Un-orient and check the walk's dpi in `--approved` (P31) |
| 25-26 | Transport-level demo; refuse real paths under `--demo`; refuse `--look-only` without `--demo`; demo entries tagged (P26-P28) |
| 27-28 | Comparison files from `library.corrected`; a real "unsafe" verdict; refuse `--bracket --ir` (P30, P29) |

## Every user-error finding

| Finding | Severity | Title |
|---|---|---|
| [LIB-07](areas/library.md#library-lib-07) | high | duplicates --delete treats different photographs (empty notes) and deliberate ladders as interchangeable |
| [LIB-15](areas/library.md#library-lib-15) | high | migrate-raw --write treats every mismatch as 'mislabelled corrected pixels': it can launder a decode regression, destroy the only corrected rendition, or overwrite a pass's only pixels |
| [SR-09](areas/session-roll.md#session-roll-sr-09) | high | Unnamed walks and rolls of one day share rolls/<date>/: frames and prescans overwritten, stale approved.json and roll.json applied to a different strip |
| [GUI2-21](areas/gui-part2.md#gui-part2-gui2-21) | high | A dry-run walk or roll with an empty roll name reuses rolls/<today>/, overwriting the earlier walk's survey.json and prescans and merging roll.json, with no warning |
| [TP-18](areas/transport-protocol.md#transport-protocol-tp-18) | medium | Calibration never checks the measured media flag, and tools/uniformity.py calibrates right after telling the operator to empty the transport |
| [TP-19](areas/transport-protocol.md#transport-protocol-tp-19) | medium | Ctrl-C during tools/scan_roll.py loses queued frames and closes the transport under the read |
| [DP-05](areas/demo-parity.md#demo-parity-dp-05) | medium | --demo pins nothing when --library/--rolls/--out/--settings are given; a demo run can overwrite a real walk's survey.json, prescanNN.tif and approved.json |
| [DP-13](areas/demo-parity.md#demo-parity-dp-13) | medium | --look-only without --demo drives the real scanner while the window says scanning will refuse for lack of film |
| [SR-15](areas/session-roll.md#session-roll-sr-15) | medium | Roll name is used as a path component without sanitising |
| [GUI1-12](areas/gui-part1.md#gui-part1-gui1-12) | medium | Clicking a prescan in aim mode can queue a film move during a running job, or aim from a stale prescan of another position |
| [GUI1-16](areas/gui-part1.md#gui-part1-gui1-16) | medium | Quit during a job waits for every queued job (hours for a roll) without stopping, and kills Save all / Export threads mid-file |
| [GUI1-17](areas/gui-part1.md#gui-part1-gui1-17) | medium | A manual exposure set in the window is silently ignored by every roll |
| [GUI2-11](areas/gui-part2.md#gui-part2-gui2-11) | medium | Deleting a reopened frame from the filmstrip can rmtree a real library entry, even under --demo whose promise is that nothing touches the real library |
| [GUI2-17](areas/gui-part2.md#gui-part2-gui2-17) | medium | Quitting while Save all or Export is writing kills the daemon writer mid-file with no warning |
| [GUI2-19](areas/gui-part2.md#gui-part2-gui2-19) | medium | Aim-click moves film with no busy guard and no check that the prescan clicked shows where the film is now |
| [OUT-12](areas/outputs.md#outputs-out-12) | medium | tools/scan.py --bracket silently ignores a single-value --exposure-scale |
| [CLI-14](areas/cli-operator-tools.md#cli-operator-tools-cli-14) | medium | Arguments are validated only after the device is opened, calibrated and metered; any integer --dpi is accepted |
| [CLI-A2](areas/cli-operator-tools.md#cli-operator-tools-cli-a2) | medium | Neither CLI tool estimates its runtime or warns before a run that will outlive the 10-minute foreground kill |
| [PA-10](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-10) | medium | exposure_probe `--only` chunking rewinds by default, so the next chunk re-walks the same frames under new numbers; --json is overwritten per chunk |
| [D15](areas/docs-readme-claude.md#docs-readme-claude-d15) | medium | The roll Delete dialog and README say only approved.json is lost; manifests and CLI-walk prescans go too |
| [D17](areas/docs-readme-claude.md#docs-readme-claude-d17) | medium | Bare Return in the 'Calibrate first' prompt starts a calibration: calibrating does have a key |
| [DOC-16](areas/docs-plans-todo.md#docs-plans-todo-doc-16) | medium | The window-roll lazy-calibration TODO item is partly stale, but a race remains: `calibrated` is set True before the Calibrate job succeeds |
| [T15](areas/tests.md#tests-t15) | medium | A second walk under the same roll name (the default is today's date) overwrites survey.json and prescanNN.tif |
| [CC-10](areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-10) | medium | Second process vs the scanner: no device state is disturbed, but on Windows the error blames the driver and sends the operator to Zadig mid-scan; a GUI that failed to open stays enabled and silently drops jobs |
| [RX-7](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-7) | medium | Roll names reach the filesystem unsanitised in the GUI roll and the CLI: separators, '..', absolute paths and Windows-illegal or reserved names; the failure comes after the seek has moved the film |
| [RX-12](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-12) | medium | Closing the window during a roll waits for the whole roll and every queued job, with no stop offered and no timeout; Save all/Export threads are killed mid-write on close |
| [TP-23](areas/transport-protocol.md#transport-protocol-tp-23) | low | RPS7200_MAX_WINDOW is unvalidated: 0 or a negative value loops forever, a bad value breaks every import, and the value is not recorded |
| [DDF-21](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-21) | low | Exposure never wraps, but clamping is silent; a NaN scale fails only after calibration |
| [LIB-24](areas/library.md#library-lib-24) | low | duplicates --keep accepts 0 and negative values |
| [LIB-25](areas/library.md#library-lib-25) | low | Library root is relative to the working directory, and verify/reconstruct/index cover only one directory level |
| [FU-15](areas/framing-units.md#framing-units-fu-15) | low | Shortcut settings: a malformed hand-edited sequence prevents the window opening after the scanner thread has started; conflicts are unchecked on load; misleading adjuster label |
| [GUI1-26](areas/gui-part1.md#gui-part1-gui1-26) | low | The 'Calibrate: reuse' choice is remembered across launches, so later sessions silently correct with an old reference |
| [GUI2-32](areas/gui-part2.md#gui-part2-gui2-32) | low | Save As on a pass whose filing is pending or failed writes the reduced working copy (as small as 512 px) under the user's chosen name |
| [GUI2-A4](areas/gui-part2.md#gui-part2-gui2-a4) | low | 'Scan chosen frames' closes the sheet and adjuster before any refusal or cancel, and the 'over the sheet' prompt parent is dead code |
| [OUT-16](areas/outputs.md#outputs-out-16) | low | JPEG-to-TIFF fallback and the DNG companion overwrite existing files; _unclaimed and Save all's promise do not cover them |
| [OUT-17](areas/outputs.md#outputs-out-17) | low | tools/scan.py checks the output name only after scanning, and the default --out overwrites the previous scan |
| [OUT-26](areas/outputs.md#outputs-out-26) | low | Mono delivery drops the infrared plane without a note |
| [OUT-V01](areas/outputs.md#outputs-out-v01) | low | --bracket with --no-shading merges uncorrected passes, which the merge module says must not happen |
| [CLI-20](areas/cli-operator-tools.md#cli-operator-tools-cli-20) | low | `scan.py --library ''` files into the current directory, while scan_roll documents `--library ''` as 'skip'; all data paths are CWD-relative |
| [CLI-23](areas/cli-operator-tools.md#cli-operator-tools-cli-23) | low | A roll that ends early on a blank frame or a stalled transport exits 0 with fewer frames than --frames asked; the default --start-at 1 silently rewinds |
| [CLI-25](areas/cli-operator-tools.md#cli-operator-tools-cli-25) | low | scan.py: a single --exposure-scale value is dropped for a bracket and also disables --auto-exposure |
| [PA-15](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-15) | low | The probes' debug gate accepts any non-empty RPS7200_DEBUG value, but DirectScanner files only for 1/true/yes/on |
| [PA-22](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-22) | low | fast_ir_probe accepts bw/kodachrome and refuses only after metering at 1800 dpi; its docstring says it meters in RGBI |
| [DOC-26](areas/docs-plans-todo.md#docs-plans-todo-doc-26) | low | `tools/scan.py` scans unmetered by default, though docs present auto-exposure as the default |
| [T22](areas/tests.md#tests-t22) | low | Delete dialog: answering 'No' permanently removes the library entry and its raw bytes; untested |
| [FE-13](areas/frame-edges-detectors.md#frame-edges-detectors-fe-13) | low | Commissioning while the edge reader is still reading uses preliminary readings of already-placed frames, and the warning mentions only unplaced frames |
| [FE-A2](areas/frame-edges-detectors.md#frame-edges-detectors-fe-a2) | low | Frame-edge reads on walks at any prescan resolution other than 300 dpi fail silently: nothing refuses or warns when the walk is set up |

## Each reader's lists, by area

### USB transport, protocol and command sequence

**Can do**

- Run `uv run python tools/check_scanner.py` at any time. It sends only INQUIRY and READ STATE (plus REQUEST SENSE if READ STATE is refused), never moves the transport, and exits 0 with no scanner attached.
- Run `tools/parse_capture.py <capture>` and `tools/verify_capture.py` offline. They read only the scanner's control transfers, so no device is needed.
- Press Stop in the window. It is cooperative, is checked between frames and before each advance or nudge, and never interrupts a read.
- Quit the window while a scan runs. on_close waits for the worker to finish rather than abandoning the read.
- Use the window's prev/next frame buttons (SLIDE_PREV/NEXT with value 1, one frame per command). These are the measured-safe whole-frame moves.
- Re-derive pixels offline with `make reconstruct`, `library.decode_raw` and `library.corrected`, for entries that actually hold their raw bytes.
- Set RPS7200_DEBUG=1 (Bourne shell) or `$env:RPS7200_DEBUG=1` (PowerShell) so that ad-hoc DirectScanner scripts file every pass. Pass keep_raw=True explicitly (see TP-03).

**Should not do**

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

**Unguarded mistakes**

- Submitting any new job after a mid-read failure: the session worker simply runs it, and the roll advances with SLIDE_NEXT and rescans up to max_failures times.
- Calling DirectScanner.scan(auto_exposure=True) or any keep_raw=False scan with RPS7200_DEBUG=1: the entry gets an earlier pass's raw bytes, or none.
- Forgetting to call close(), or not using `with`, in an ad-hoc debug script: spooled scans are never filed and stay in the temporary directory.
- Running a debug script from a different working directory: library.DEFAULT_ROOT is relative ('library'), so entries land in a stray ./library.
- A filing failure during the debug flush (disk full, bad RPS7200_DEBUG_ROOT): the spooled raw bytes and pixels are deleted anyway.
- Choosing 7200 dpi in --demo: the demo 'scans' it, while the real driver refuses a shaded 7200 dpi pass.
- Calibrating on an empty transport: READ STATE byte 8 (the measured media flag) is never consulted before calibration.
- Spelling --exposure-scale as one number with infrared on: the infrared exposure is scaled too, unlike the three-value form.
- Setting RPS7200_MAX_WINDOW=0 or a negative value: the next READ loops forever with a command outstanding.

### Decode, direction, shading and debug filing

**Can do**

- Turn on automatic filing for any script with RPS7200_DEBUG=1 (or DirectScanner(debug=True)). Every scan()/prescan() pass is then spooled to system temp and filed in library/ after close().
- Redirect debug filing to another library with RPS7200_DEBUG_ROOT=<dir>.
- Choose per pass whether raw USB bytes are kept (scan(keep_raw=True) / prescan(keep_raw=True)).
- Request raw pixels deliberately with scan(shading=False). The entry records SHADING_SKIPPED_EXPLICIT and library.corrected() leaves it raw.
- Reuse a saved shading reference instead of calibrating (GUI 'reuse the cached reference', tools --reuse, DirectScanner.load_shading).
- Override MODE SELECT byte14, the SLIDE INIT param, skip_shading and fast_infrared per scan() call, and pass explicit per-channel exposure_scale values.
- Re-decode any stored entry offline with library.decode_raw / library.reconstruct / tools/library.py reconstruct, and re-correct it with library.corrected() using today's code.
- Meter a frame (auto_exposure) and read what the probes saw on DirectScanner.last_metering right after.

**Should not do**

- Call scan()/prescan() with the default keep_raw=False while debug filing is on. The entry gets no raw bytes or another pass's bytes.
- Interrupt, kill or time out a debug session before close() has finished. All pending entries are lost and the spool is left orphaned in temp.
- Let the library disk or /tmp (often tmpfs) run full during a debug session. Failed spool writes and failed filings discard the pass.
- Rely on 'reuse the cached reference' across power cycles for scans meant to be evaluated later. The entries do not say the reference was reused.
- Use byte14 / slide_init_param overrides and expect the library entry to record them.
- Scan at 7200 dpi with shading=False and expect `reconstruct` to confirm the entry. It is always reported as changed.
- Run the window with --demo and --library pointing at the real library.
- Call calibrate_shading(resolution=...) at anything but 3600 dpi. The docstring calls it untested, and nothing guards it.
- Treat 2_corrected.tif from tools/make_comparison.py as the pipeline's correction. It runs destripe, not apply_shading.

**Unguarded mistakes**

- With debug on, scan(keep_raw=False) after any pass that kept its bytes, including the metering probes of auto_exposure=True, files the earlier pass's raw.bin.gz under this pass's pixels. Nothing compares the two.
- Running a probe script from a different working directory files debug entries into <cwd>/library, a separate library that verify/reconstruct never see.
- An --exposure-scale above the 65535 ceiling (or below the floor of 100) is silently clamped. The record keeps the requested scale and nothing flags the difference. A NaN scale raises only after calibration has run.
- A filing failure at close() (disk full, unwritable root, tifffile error) deletes the spooled pixels and bytes. With verbose=False and no log hook, nothing is printed.
- A process killed during a long debug run loses every entry's meta, reference and mask. Only unlabelled .npy/.bin files remain in temp.
- The window's Scan button files the shading-corrected image as the entry's raw scan.tif. The operator cannot avoid this.
- ensure_shading(skip=True) or Calibrate 'off' does not give raw scans. The next shading=True scan silently spends minutes calibrating.
- --demo together with --library library puts demo entries (corrected pixels, reused or reversed bytes, missing device fields) into the real library with no demo tag.

### Demo scanner vs the real scanner

**Can do**

- Run `make run-demo` (tools/gui.py --demo) with no scanner; every job (Calibrate, Prescan, Scan, Roll/walk, Move, sheet 'Scan chosen frames') runs through the real ScanSession against DemoScanner, and output goes to demo/library, demo/rolls, demo/gui-settings.json when no path flags are given.
- Choose the picture source with --demo-source <library dir>; later rolls also draw from 'library *' siblings and their nested entry folders.
- Run `make run-sheet` (--demo --look-only --open-roll rolls/aligned-strip) to open a stored walk read-only; motion and scan jobs fail with the demo's 'no film in the transport' UsbError through the failed-job path.
- Press Stop (cooperative) or force-abort in the demo; force_abort closes the _FakeTransport and the next _work step raises UsbError, with no hardware at risk.
- Set sheet positions and commission in the demo to exercise on_scan_chosen, approved.json writing, session.seek and DirectScanner._hold_to_approved/_aim_frame against the modelled film.

**Should not do**

- Pass --library library or --rolls rolls (or any real path) together with --demo: demo entries and manifests go there, unmarked, and a demo walk or commission can overwrite a real walk's survey.json, prescanNN.tif and approved.json.
- Use --look-only without --demo: the real scanner is driven while the window says scanning will refuse for lack of film.
- Treat demo/library entries as evidence (reconstruct, timing medians, registration-margin fits): their scan.tif is corrected or resampled or shifted, and their raw bytes, reference and mask may belong to another pass.
- Point --demo-source at demo/library: the demo then draws on its own mislabelled output.
- Judge from the demo whether >3600 dpi works, whether holds improve framing, how backlash behaves, whether RGBI returns four channels, or what calibration costs; the demo diverges on each.
- Commission ('Scan chosen frames') a real walk reopened in the demo without --look-only: the demo's strip is library pictures, not that walk, so the holds correlate unrelated pictures.
- Leave the roll name blank when a sheet's decisions matter (real or demo): approved.json goes to rolls/roll/ and not beside the roll.

**Unguarded mistakes**

- `--demo --library library`: no refusal; demo entries enter the real library with no demo marker and fail reconstruct.
- `--demo --rolls rolls` with the same roll name as a real walk, or a blank name on the day of a blank-named real walk: survey.json and prescanNN.tif are silently replaced, and approved.json is overwritten on commission.
- `--demo --out <real folder>`: demo frames are written beside real deliverables (renamed -2.. only on collision).
- `--look-only` without `--demo`: accepted; the real scanner moves, calibrates (possibly an empty transport) and scans.
- Choosing 7200 dpi (or anything >3600) in the demo: accepted and completes; on the hardware every pass raises ShadingUnavailable.
- Setting prescan dpi other than 300 in the demo: pixels stay 300 dpi but are labelled and filed at the requested dpi, which persists into survey.json.
- `--demo-entry <path>` mistyped, or on a film with raw entries: silently ignored while the log claims it is shown.
- A blank or punctuated roll name when commissioning from the sheet (real and demo): approved.json lands in a different folder from roll.json, reopening loses the positions, and blank-named rolls overwrite each other's approved.json.
- Closing the demo during the first-launch signature pass: a possibly truncated demo/pictures.npz makes every later launch's second roll repeat the first strip, with no message in the window.
- Calibrate 'off' then Scan in the demo: instant, corrected pictures; on the hardware the first pass silently calibrates for minutes.

### The library store

**Can do**

- Survey the library with `tools/library.py list` (or `make verify`, `make reconstruct`, `tools/library.py reindex`), all read-only apart from index.json.
- Run `tools/library.py duplicates`, `migrate-raw` and `migrate-direction` as dry runs. They only print unless given --delete or --write.
- Delete a library entry from the window's Delete action (tools/gui.py:3999-4014), after a confirmation dialog that says the raw bytes cannot be recovered.
- Get a corrected full-resolution file of any entry via Save As or the 1:1 view. Both go through library.corrected() with today's correction code.
- Choose where entries go: `--library DIR` for gui.py and tools/scan.py, `--library ''`/`--no-library` to skip filing, and RPS7200_DEBUG=1 with RPS7200_DEBUG_ROOT for ad-hoc scripts.
- Run the vignette study with `tools/uniformity.py capture` (prompted, with accept/redo/abort per pass), then `analyse --tag ...`, which rebuilds from stored raw bytes.
- Run `gui.py --demo`, which files into demo/library by default and draws pictures from `--demo-source`.

**Should not do**

- Do not run `tools/library.py duplicates --delete` unless every listed pair is known to be the same frame. With an empty Frame/Stock/Subject, or for ladder passes (byte14, gain), it removes different photographs and their raw bytes.
- Do not run `migrate-raw --write` after any change to the decode until reconstruct is understood. It rewrites every mismatching scan.tif to today's decode and labels it raw.
- Do not rename, copy or move entry directories inside a library. The tools address entries by record['id'], not by folder name.
- Do not point `--demo` at the real library (`--library library`). Demo entries hold corrected-as-raw pixels and lose their 'demo' marker.
- Do not run tools or scripts from outside the repository root. The default library path is relative, and a new library silently appears in the current directory.
- Do not hand-edit scan.json. A syntax error makes the entry vanish from every check without a report.
- Do not kill a script running under RPS7200_DEBUG=1 before it closes the scanner. Everything spooled so far is lost, and long runs should be backgrounded.
- Do not leave a remembered output folder on a removable or network drive that may be absent. A failing copy prevents the library entry from being written.
- Do not run migrations or duplicates while a window session is filing into the same library.
- Do not expect `make verify` to be clean after a deliberate `--no-shading` or 7200 dpi scan. verify reports it as a failure.

**Unguarded mistakes**

- Scanning from the window with the Scan button: the entry silently stores corrected pixels labelled raw, and Save As then double-corrects them (LIB-01).
- Leaving the Frame field empty: every metered scan at that dpi then shares one duplicate signature (LIB-07).
- Running a probe tool under RPS7200_DEBUG=1 whose passes use keep_raw=False (byte14_probe's final pass, verify_protocol, transport_probe, filing_load_test): the entries are filed with no raw bytes or with the previous pass's bytes, with no warning (LIB-02).
- Running tools/uniformity.py with RPS7200_DEBUG=1: every pass is filed twice, once by debug flush and once by the tool.
- Choosing 'redo' in tools/uniformity.py capture: the rejected pass is still analysed and even preferred as the reference (LIB-22).
- `duplicates --keep 0` (crash) or `--keep -1` (deletes one entry per group) are accepted (LIB-24).
- `migrate-raw --write` on a legacy corrected entry whose shading.npz is missing destroys the only corrected rendition (LIB-15).
- A disk-full or crash during save leaves an orphan entry directory that verify reports as 'intact' (LIB-10).
- An unwritable output folder (unplugged drive, permissions) makes every scan skip its library entry, and the raw bytes are gone (LIB-03).
- Scanning a single frame from the window leaves the device open and idle while its entry gzips (LIB-11).

### ScanSession, FrameWriter and rolls

**Can do**

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

**Should not do**

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

**Unguarded mistakes**

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

### Framing and transport units

**Can do**

- Walk a strip as a dry-run Roll from the window (Roll with 'dry run') or `tools/scan_roll.py --dry-run`, at prescan dpi 300/600/900 from the GUI ladder or any typed 25-7200.
- Open the contact sheet with the button or Cmd/Ctrl-K. The key works even while a walk is still running, as long as some frames exist.
- Rotate or flip any prescan with keys or the menu, in the main window or the sheet, at any time, including during a walk.
- Set per-frame positions in the adjuster: arrows (finest, small = param 3, medium = 8, large = 20), drag, Cmd-C 'as surveyed' (0), or reset to the detector. Values snap to reachable positions up to ±88.8 units (one param-87 command).
- Tick or untick frames, change the sheet's scan options (dpi, prescan dpi, film, meter, IR, correct), and commission 'Scan chosen frames'. It asks first, and the prescan dpi is pinned to the survey's.
- Tick 'correct' or 'correct dry run' so the roll aims frames itself with the frame-edge reader, or with the legacy strip detector for films the reader refuses.
- Reopen a stored walk with --open-roll or make run-sheet. Machine positions are re-measured on every launch; operator positions are kept.
- CLI: `--approved <folder>` holds to positions proposed from a walk's prescanNN.tif; `--nudge <mm>` moves the film before a run; `--prescan-dpi`; `--correct`/`--correct-dry-run`.
- Click in the main preview to aim a point at the nearer aperture edge. It asks first, then moves film immediately.
- Edit shortcuts in the editor, or by hand in gui-settings.json.

**Should not do**

- Do not rotate or flip prescans while a dry-run walk is running. It duplicates survey entries and makes later prescanNN.tif files disagree with the manifest's single rotation (FU-02).
- Do not walk at 600 or 900 dpi prescan if you want frame-edge proposals or in-roll aiming. The detector refuses those widths (FU-05).
- Do not use `tools/scan_roll.py --approved` on a folder walked in the window with a rotation or flip set (FU-03). Do not pass a `--prescan-dpi` different from the walk's.
- Do not set positions near the adjuster's maximum (above about 85 units) and expect them to be verified (FU-08).
- Do not rely on Cmd-C ('as surveyed') surviving a sheet close/reopen or a roll reopen when the detector proposes a move (FU-04).
- Do not commission from the sheet while the edge light is still blue if every frame should be placed. Unread frames get 0, attributed to 'operator'.
- Do not enable 'correct' on slide or Kodachrome film. The legacy 36 mm-frame detector runs there instead of the refusing frame-edge reader (FU-07).
- Do not hand-edit shortcut sequences in gui-settings.json unless they are valid Tk sequences (FU-15).

**Unguarded mistakes**

- Rotate, flip or sheet turns during a walk: no guard. remember_arrangement appends the survey entry again and re-adds it to EdgeWatch, and session.rotation changes mid-roll without the manifest.
- Opening the contact sheet mid-walk with Cmd/Ctrl-K: only `self.survey` non-empty is checked, not whether a walk is still running.
- Choosing 600 or 900 dpi prescan: the GUI offers it and nothing warns that the frame-edge detector will refuse every frame.
- Setting an adjuster position between about 85 and 88.8 units: accepted and snapped, but unverifiable by the hold loop.
- Resetting a frame to 'as surveyed' (0): stored as 'absent', so the detector's value comes back on reopen with no prompt.
- `--approved` pointing at a GUI-walked folder with rotation or flip set: no check against the manifest's rotation or flipped.
- `--nudge` in millimetres with no unit check: a value meant in units (for example 20) is taken as 20 mm and refused only if the planner raises.
- 'correct' on film the frame-edge reader refuses: silently falls back to the legacy detector, with no film gate.
- A malformed shortcut in gui-settings.json: the window fails to build after the scanner thread has started.
- Commissioning while EdgeWatch is still reading: the dialog mentions it, but frames without readings are recorded with source 'operator'.

### The window (tools/gui.py, first half)

**Can do**

- Calibrate once per session with the film loaded (Calibrate button, or the 'Calibrate now' prompt that every picture action raises until a calibration has been queued).
- Prescan (button, or Cmd/Ctrl-Return with a confirm), Scan (button or key with a confirm), walk a strip (Scan roll with 'dry run'), and commission ticked frames from the contact sheet. All go through ScanSession jobs.
- Move whole frames (prev/next slide) or nudge sub-frame (1 command max, param 1..87), or click-to-aim on a prescan; the frame counter does not see sub-frame moves.
- Change any setting while a job runs; it applies to the next job (except the output folder, format and orientation, which apply to the next file written -- see should_not_do).
- Arrange a pass (rotate, flip): changes the files written from now on, never the library entry.
- Save as / Save all / Export (re-corrected from library entries with today's code); Duplicate, Rename, Delete or Reveal roll folders from Rolls...
- Stop: finishes the running pass (single) or the running frame (roll). Force abort: types ABORT; abandons the read and needs a power cycle.
- Reopen a walk or unfinished roll from Rolls... and see its sheet, ticks and settings.
- Run make run-demo / make run-sheet with no scanner; outputs go under demo/.

**Should not do**

- Do not rotate or flip prescans while a dry-run walk is running. Later prescanNN.tif files are written in the new orientation, survey.json keeps the old one, and reopened references come back mis-oriented; the frame is also duplicated in the survey.
- Do not leave the roll name empty when scanning more than one strip per day. Everything lands in rolls/<date> and overwrites the previous strip's walk and frames, and approved.json goes to rolls/roll/.
- Do not change the roll name, or use characters _safe rewrites (spaces, '/', ':'), between walking and commissioning: approvals and the roll then live in different folders.
- Do not rely on 'press Scan chosen frames' to finish a reopened roll unless the Film panel's roll field is set to that roll's exact folder name; otherwise it writes a new folder.
- Do not leave an old contact sheet open or close it during a new walk; do not open a roll from Rolls... while a walk runs.
- Do not set a removable or network output folder that may be absent at the next launch; a failed delivered write loses the library entry.
- Do not choose 7200 dpi (or any dpi above ~3600): the host refuses it after metering, and rolls fail frame by frame.
- Do not type into 'Save scans to'; use Choose/Clear.
- Do not leave text in the Film panel's 'frame' field before a roll.
- Do not use --look-only without --demo with the scanner attached; it does not refuse anything.
- Do not delete reopened frames' library entries in the demo; they are real entries.
- Do not quit during a roll expecting it to stop; press Stop first. Do not quit during Save all/Export.
- Do not click the picture in aim mode during a job or on a prescan that is not of the frame now in the gate.
- Do not press Cmd/Ctrl-B (roll) while anything is running.

**Unguarded mistakes**

- Roll shortcut during a running job queues a second roll and wipes the running walk's survey and ETA state (no busy check in on_roll).
- Double-click on Scan/Prescan/slide buttons queues duplicate jobs, and Stop cannot cancel the queued one.
- Aim click during a roll queues a film move that runs after the roll; aim on a stale or reopened prescan moves the film by a distance measured on a different picture.
- Missing or unwritable output folder: every scan's library entry (raw bytes) is skipped with only a log line.
- Empty roll name: approved.json is filed under rolls/roll/, walks of the same day overwrite each other, and the previous strip's sheet decisions are restored onto the next strip.
- Reopened roll continued via the sheet or the Roll button: written to a new folder; the no-sheet path via Roll rescans done frames and scans past the wanted set.
- Each commission replaces approved.json with only the ticked frames, losing earlier frames' turns and positions.
- Opening two rolls (or one twice): seq collisions attach the wrong full-resolution pixels and delete both copies.
- Contact-sheet options: infrared on B&W/Kodachrome is accepted until the backend refuses after the seek and approved.json write.
- Manual exposure is silently ignored for rolls.
- Typed output path is not applied this session but is applied, unvalidated, at the next launch.
- The 'frame' field is stamped on all roll frames, breaking the roll-to-library join and Export.
- 'Use the cached one' makes 'reuse' the remembered default for every later session.
- Save as / Save all / Export mono follows the window's current film, not the pass's.
- Delete key/menu under --demo on a reopened real walk frame removes a real library entry after one Yes/No/Cancel dialog.
- Keyboard and sheet can still submit jobs after Force abort (they fail with 'transport is not open').

### The window (tools/gui.py, second half)

**Can do**

- Prescan, scan, walk (dry-run Scan roll) and commission a roll from the contact sheet. Each asks first; the sheet commission also checks calibration and busy.
- Rotate or flip any pass (filmstrip menu, keys) or any sheet cell; rotate or flip all frames; show an earlier prescan again.
- Tick/untick frames, select them with the keyboard, open the Frame position window, drag or step a frame's intended position (nothing moves), Centre, Reset one, Reset positions for all.
- Change the roll's scan options on the sheet (dpi, prescan dpi, film, meter, IR, IR at scan resolution, 'nudge' tick). These win over the main window for that commission.
- Open rolls from the Rolls table; export, duplicate, rename, reveal or delete roll folders (delete, rename and duplicate refuse while busy or while that roll is the one opened).
- Save as ... one pass, Save all passes into a folder, zoom/pan the big view, hover for pixel values, watch the histogram.
- With 'aim' ticked, click a prescan to nudge the film so that point reaches the nearer aperture edge (asks first); use prev/next slide and fine nudges (buttons disabled while busy).
- Rebind every key, including the prescan/scan/roll keys (which ask first) and delete-pass.

**Should not do**

- Do not commission a roll with the Film panel's roll-name field empty, or with spaces or other non [A-Za-z0-9._-] characters in it: approved.json and the roll land in different folders.
- Do not type anything into the Film panel's 'frame' field before a roll: every frame's entry gets that value and roll Export can no longer find the entries.
- Do not rotate or flip prescans while a dry-run walk or a roll is running. Arrange them after the walk, in the sheet.
- Do not start a new dry-run walk while an older contact sheet is open; close it first.
- Do not open a second roll, or the same roll again, in one session. Restart the window between rolls.
- Do not use Delete with 'Keep the library entry? No' on reopened frames, and never in --demo.
- Do not run --look-only without --demo, and do not accept 'Calibrate now' with an empty transport.
- Do not quit while Save all or Export is writing, and close the contact sheet before quitting.
- Do not browse quickly through 3600/7200 dpi passes, run Save all or Export, or duplicate large rolls while a scan is running.
- Do not aim-click on a prescan that is not the latest one of the frame now in the gate.
- Do not walk two strips on the same day without giving each its own roll name.
- Do not rely on the sheet's 'nudge registration between frames' tick. It never acts on a commissioned roll.
- Do not resume a roll that has no walk with the plain Roll button when its remaining frames are not contiguous: it rescans and overscans.

**Unguarded mistakes**

- Pressing Centre on a frame, then closing and reopening the sheet (or reopening the roll), silently replaces the operator's zero with the detector's position (GUI2-10).
- Closing the contact sheet with Close/X/Escape leaves the Frame position window open. Further edits there raise TclError and are lost (GUI2-15).
- Quitting the app with the sheet open loses every tick, position and turn made since the sheet was last closed (GUI2-16).
- Opening a roll from the Rolls table while a walk runs mixes the reopened survey with the walk's frames (GUI2-14).
- Walking a new strip with an old sheet open: the old sheet is raised at the end and its decisions are filed under the new walk (GUI2-07).
- Rotating the first prescan during a walk duplicates it in the survey and desyncs every later prescanNN.tif from survey.json (GUI2-08, GUI2-09).
- Commissioning from a reopened roll writes into a new date-named folder rather than the reopened roll, and approved.json into rolls/roll/ (GUI2-03).
- Walking a second strip on the same day with no roll name overwrites the first walk's survey.json and prescans (GUI2-21).
- Exporting a walked-only or walked-and-scanned roll can deliver 300 dpi walk prescans labelled as scan-resolution frames (GUI2-05).
- Exporting a roll uses the window's current mono setting, not the roll's (GUI2-24).
- In --demo, deleting a reopened frame from the filmstrip can remove a real library entry (GUI2-11).
- After opening two rolls, clicking one roll's frame shows or deletes the other roll's frame of the same number (GUI2-12).
- An aim-click during a roll queues a film move that runs when the roll ends (GUI2-19).
- Save As on a pass whose filing failed writes a 1400- or 512-px preview under the chosen name (GUI2-32).
- A JPEG export can overwrite an existing .dng (or .tif without Pillow) despite the 'nothing is overwritten' promise (GUI2-26).

### Outputs: export, TIFF, DNG, preview, mono, bracket, settings

**Can do**

- Choose TIFF or JPEG for the output folder and set JPEG quality. The GUI clamps quality to 60-100; tools/scan.py --quality is unclamped and Pillow clamps it.
- Save As a single pass to any .tif/.tiff/.jpg/.jpeg path, or Save all passes into a folder. Passes that are not yet filed are written from the reduced on-screen copy (at most 1400 px, or 512 for archived results).
- Rotate or flip a pass, which carries over to later passes. Delivered files are turned; the library entry keeps the scanner's orientation.
- Deliver black and white as one plane (average, R, G or B) with --mono/--mono-channel or the GUI control; the library keeps all channels.
- Run tools/scan.py --bracket 2-9 --stops X to take and merge an exposure bracket; every pass is filed in the library before the merge.
- Hand-edit or relocate gui-settings.json (RPS7200_SETTINGS or --settings); the demo uses demo/gui-settings.json.
- Run tools/make_comparison.py on any two TIFFs to write the three comparison files into the current directory.

**Should not do**

- Do not point the output folder at a removable, network or nearly full drive during a roll. A failed delivery currently discards the frame's library entry and raw bytes (OUT-02).
- Do not use Save As or Save all on passes from GUI single scans and treat the result as correct: it is double shading-corrected (OUT-01).
- Do not combine --bracket with --ir (OUT-05), and do not trust bracket highlights while saturation is judged on corrected pixels (OUT-04).
- Do not use --no-library with tools/scan.py; the raw bytes and calibration are then gone for good.
- Do not read the histogram panel's 'at/near full scale' figures as sensor saturation; they are measured after shading (OUT-07).
- Do not interrupt `tools/library.py migrate-direction --write`; it rewrites prescan.tif in place and non-atomically (OUT-09).
- Do not run two GUI windows against the same settings file, and do not hand-edit it without a backup (OUT-11).
- Do not keep 'reuse the cached reference' selected across power cycles. It is remembered between launches, while shading is meant to be measured per session.
- Do not judge a correction change by make_comparison output built from a delivered TIFF (OUT-10).

**Unguarded mistakes**

- tools/scan.py --out with an unsupported extension (.png) is only rejected after the whole scan, leaving no delivered file (OUT-17).
- Running tools/scan.py twice with the default --out overwrites the previous scan.tif and scan.json without warning.
- tools/scan.py --bracket with a single-value --exposure-scale silently scans at 1.0x base exposure (OUT-12).
- Delivering JPEG without Pillow silently overwrites an existing same-stem .tif; a JPEG with IR silently overwrites an existing same-stem .dng (OUT-16).
- Typing an unsupported extension in the Save As dialog raises inside a Tk callback; the window shows nothing and the traceback goes to stderr.
- A syntax error in gui-settings.json silently resets presets, shortcuts and contact-sheet decisions and is written back over the file on quit (OUT-11).
- A hand-edited sheet rotation that is not a quarter turn passes _clean_sheet_state (int cast) and later makes preview.rotate raise inside FrameWriter, losing the library entry (OUT-02).
- Ticking mono on an RGBI scan drops IR from the delivered file without any note (OUT-26).
- Save all of passes not yet filed writes reduced previews under names that carry the full dpi (batch_name uses meta resolution_dpi).

### Operator CLI tools and build

**Can do**

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

**Should not do**

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

**Unguarded mistakes**

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

### Probe and analysis tools

**Can do**

- Run any probe with --dry-run to see the plan and a time estimate without opening the device (hold_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth, roll_registration_walk; transport_probe has no dry run).
- Run the offline tools (exposure_headroom, linearity, dpi_analysis, registration_margin, roll_registration_study) at any time with no scanner; they only read library/ and rolls/.
- Chunk exposure_probe with --only A-B, but only together with --no-rewind and a distinct --json per chunk.
- Restrict exposure_headroom to 16-bit scans with --dpi (for example --dpi 3600) to stay away from 8-bit prescans.
- Use `tools/library.py duplicates` without --delete to see what would be pruned, and --keep 2 to keep repeat pairs.
- Redirect debug filing to a scratch library with RPS7200_DEBUG_ROOT.

**Should not do**

- Do not run `tools/library.py duplicates --delete` on a library that holds probe or walk entries: byte14 and gain ladders, repeat pairs and all walk prescans share one signature and will be removed (PA-01).
- Do not press Ctrl-C during a byte14_probe pass: its finally block starts another full scan on a device with an abandoned read (PA-05).
- Do not run fast_ir_probe (about 25 min), exposure_probe (about 40 min), byte14_probe or a 16-frame roll_registration_walk in the foreground: a kill at 10 minutes files nothing to the library and may wedge the device (PA-04).
- Do not run tools/transport_probe.py: it is ungated, sends SLIDE payloads with value bytes of unknown meaning, reports 0 mm for real moves, files no raw bytes and leaves the strip advanced (PA-12).
- Do not set RPS7200_DEBUG to anything but 1/true/yes/on; other non-empty values pass the probes' gate while filing stays off (PA-15).
- Do not treat registration_margin's 'CONFIDENCE_FLOOR holds' as validating the production gate, which also scores a row-flipped reading (PA-08).
- Do not read roll_registration_study --ensemble output as what the window or scan_roll would decide; both use the frame_edges reader (PA-09).
- Do not trust hold_probe's printed 'the floor is 40'; the code uses 55 (PA-18).
- Do not calibrate or run any probe with an empty transport (CLAUDE.md); the probes do not check it, beyond warnings based on READ_STATE.

**Unguarded mistakes**

- Running exposure_probe without --json: 40 minutes of passes filed with no frame or rung identity, so linearity --probe cannot use them.
- Running exposure_probe --only 5-8 after --only 1-4 without --no-rewind: the same physical frames are re-measured and labelled 5-8, and reusing one --json path erases the first chunk.
- Passing a byte14_probe --ladder without 0x10: IndexError after the scans, before the JSON is written; values up to 0x31 never seen in a capture are accepted.
- Passing --film bw or kodachrome to fast_ir_probe: several minutes of 1800 dpi metering are spent before the ValueError refusal.
- Running exposure_headroom with no --dpi after a roll: it studies the newest entries, usually 8-bit prescans, and prints garbage clip percentages.
- Running roll_registration_study --held on a 600 dpi-prescanned roll: the scale is hard-coded for 428 px at 300 dpi, so predictions are off by 2x.
- Pointing roll_registration_study --root at the library: 16-bit scans mix with 8-bit prescans under absolute-count thresholds.
- hold_probe --restore, transport_truth's automatic put-back and roll_registration_walk's ladder restore all send the commanded sum in reverse direction, so the film is usually not back where it started, and nothing reports the residual except hold_probe's final measure.
- An exception mid-ladder in roll_registration_walk (for example an excursion refusal) aborts with the film displaced by up to 1.2 mm and no restore.

### README.md and CLAUDE.md vs the code

**Can do**

- Run `make test`, `make test-all`, `make lint`, `make type`, `make fix`, `make all` and `make clean` on macOS, Linux or Windows with no scanner attached; `uv run pytest -m hardware` and `tools/check_scanner.py` only read INQUIRY and READ STATE.
- Scan a single frame with `tools/scan.py --dpi N [--ir]`: it calibrates or reuses a reference, scans, and files raw pixels, raw bytes, reference and mask in library/ after closing the device.
- Walk a strip from the GUI's dry run: every prescan is filed in the library with its raw bytes, plus survey.json and prescanNN.tif in rolls/.
- Re-derive stored scans offline with `tools/library.py reconstruct`, repair mislabelled entries with `migrate-raw --write`, and bring old entries' direction records up to date with `migrate-direction --write`.
- Export or Save As from the GUI; these re-correct from the library with today's apply_shading, provided scan.tif really is raw.
- Exercise the window with `make run-demo`, and `make run-sheet` when rolls/aligned-strip exists locally.
- Force-abort a hung pass after typing ABORT, accepting a power cycle.

**Should not do**

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

**Unguarded mistakes**

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

### docs/*.md and TODO.md vs the code

**Can do**

- Scan once from the CLI: `tools/scan.py --dpi N [--ir] [--auto-exposure] [--reuse|--no-shading] [--bracket N --stops X] [--exposure-scale X|R,G,B] [--mono] [--out f.tif|.jpg]`. It files in library/ by default.
- Walk or scan a roll from the CLI: `tools/scan_roll.py [--dry-run] [--frames N] [--start-at N] [--rewind N] [--nudge mm] [--approved <walk-folder>] [--correct|--correct-dry-run] [--meter each|once|none] [--reuse|--no-shading]`.
- In the window: Calibrate (measure/reuse/off), Prescan, Scan, Roll or walk, then open the contact sheet, set offsets, rotations and ticks, and 'Scan chosen frames'. 'Rolls ...' reopens and resumes a roll. Save As delivers library.corrected() pixels.
- Maintain the library: `tools/library.py list|verify|reconstruct|reindex|duplicates [--delete --keep N]|migrate-raw [--write]|migrate-direction [--write]`.
- Turn on debug filing for ad-hoc scripts with RPS7200_DEBUG=1 or DirectScanner(debug=True). Redirect it with RPS7200_DEBUG_ROOT.
- Regenerate the comparison TIFFs with `tools/make_comparison.py scan.tif flat.tif` (destripe-only today; see DOC-01).

**Should not do**

- Do not run `tools/library.py duplicates --delete` on this library. Byte-14 and gain ladders share one signature and would be deleted (DOC-02).
- Do not use `--reuse` with a cache from another power-on. The entry will not record that the reference was stale (DOC-11).
- Do not resume a CLI roll with `tools/scan_roll.py --start-at N --roll <same>`. It overwrites roll.json. Resume from the window, or use a new --roll name (DOC-10).
- Do not use `--bracket` together with `--ir` and trust the merged file's blue channel (DOC-05).
- Do not pass a single-value `--exposure-scale` with `--bracket`. It is ignored (DOC-27).
- Do not judge a shading change by 2_corrected.tif from make_comparison.py. Use library.corrected() output (DOC-01).
- Do not rely on a CLI `--dry-run` walk as library evidence. It files nothing re-decodable (DOC-04).
- Do not run `tools/scan.py` without `--auto-exposure` (or an explicit scale) for real work. It scans at about 3-10% of full scale (DOC-26).
- Do not press Roll or Scan in the window while a Calibrate job is still queued or running. The window already believes it is calibrated (DOC-16).
- Do not scan at 7200 dpi expecting `make reconstruct` to stay green (DOC-07).
- Do not follow TODO.md:886-910 (lower FRAME_WIDTH_MM to 35.3) or protocol.md §5's 0.1662 intercept. Both are superseded (DOC-14, DOC-19).

**Unguarded mistakes**

- `duplicates --delete` shows no warning that the entries it removes differ in device gain or byte14. Only commanded exposure and fast_infrared are protected.
- `--start-at` on an existing roll name silently truncates that roll's manifest to the new run's frames.
- `--bracket 3 --exposure-scale 2.0` silently scans at x1.0 base times the ladder.
- `--bracket --ir` silently mis-scales blue in the delivered merge.
- `--reuse` loads any shading.npz at the path, of any age and from any device, with only a 'reusing ...' line on stdout.
- Calibrate(mode='off') followed quickly by Roll in the window lets the roll reach the lazy in-scan calibration that TODO records as stalling the device.
- The default `--out scan.tif` in the current directory is overwritten on every tools/scan.py run (the library copy survives).
- A 7200 dpi window narrowed to at most 5172 columns passes the shading guard and is corrected through an unverified mask mapping, with no warning (scanner-options-survey.md:147-155).
- Choosing 'nudge registration between frames' on the sheet is accepted and never acts on ticked frames.
- A `--no-shading` CLI scan makes `make verify` report 'correction was asked for', which misleads the operator into thinking a correction failed.

### The test suite

**Can do**

- Run `make test`, `uv run python tasks.py test` or `make test-all` with no scanner: hardware tests are deselected by addopts, and tests that need library/, scans/, captures/ or the frame-edge data skip.
- Run `uv run pytest tests/ -m hardware` with the scanner attached and Stefan's agreement: nine read-only INQUIRY and READ STATE tests.
- Run `FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py` on a machine that has research/frame-edge/data.
- In the window: calibrate (measure, reuse or off), prescan, scan, walk or scan a roll, approve positions on the contact sheet, move whole frames or nudge, Stop (lands between frames), Force abort (after typing ABORT), Save As, remove a result and optionally its library entry.
- Run `tools/library.py list|verify|reconstruct|duplicates|migrate-raw|migrate-direction`; the rewriting actions are dry runs unless given --write or --delete.

**Should not do**

- Do not export RPS7200_DEBUG=1 in the shell used for `make test`: test_filing_is_off_by_default fails.
- Do not treat a green suite as proof that GUI single scans are filed raw, that demo entries reconstruct, or that DirectScanner.scan() works end to end; none of these is exercised (T01, T02, T06).
- Do not use `--demo --library library`: demo entries (corrected pixels, borrowed raw, no demo marker) would mix into the real library.
- Do not answer 'No' to 'Keep the library entry?' unless the pass's raw bytes are truly unwanted.
- Do not point the output folder at removable or network storage for a long roll: a failed copy stops that frame from being filed (T03).
- Do not walk a strip twice under the same roll name, including the default date name: the first survey.json and its prescans are replaced.
- Do not click-to-aim or nudge while a roll runs, especially after pressing Stop.
- Do not expect `tools/scan_roll.py --dry-run` to leave library entries, or `tools/library.py reconstruct` to pass 7200 dpi entries.
- Do not rely on debug filing when calling scan() or prescan() without keep_raw=True after an earlier keep_raw pass (T04).

**Unguarded mistakes**

- Pressing Scan in the window files the corrected image as raw. Save As and the 1:1 view then correct it a second time, and nothing warns (T01).
- Clicking a roll prescan with 'aim' ticked during a roll queues a Move behind the roll and silently cancels a Stop already pressed; the Stop button stays disabled (T14).
- A second same-day walk with no name overwrites the first walk's survey.json and prescanNN.tif, and leaves approved.json pointing at pictures that are gone (T15).
- An output folder that becomes unwritable mid-roll drops every later frame's library entry, with only a log line per frame (T03).
- A crash while roll.json is rewritten makes the next resume treat the roll as new and lose the done-frame record (T16).
- A crash during library.save leaves a partial entry that list, verify and reconstruct never report (T08).
- In --demo, a 7200 dpi scan with shading on succeeds, although the scanner refuses it, and a scan with shading off is still corrected, so the demo teaches the wrong behaviour (T17).
- Answering 'No' in the delete dialog permanently rmtree's the entry, including raw.bin.gz (T22).
- With RPS7200_DEBUG=1, a script calling scan(auto_exposure=True) or prescan() without keep_raw files the entry with another pass's raw bytes (T04). If filing fails at close(), all spooled scans are deleted (T05).
- A B&W or slide walk files its prescans labelled film 'negative' (T12).
- Launching the window from another working directory silently starts a new library/, calibration/ and rolls/ there, because all three default paths are relative to cwd (gui.py main: `home = Path(".")`).

### Frame-edge detectors (gap pass)

**Can do**

- Walk a strip in the window (dry run). Each prescan is read in the background by EdgeWatch as it arrives, and the contact sheet shows the positions (measured / unconfirmed / neighbours / none) with edge lines.
- Open the contact sheet before the reader has finished. It fills in as readings arrive, and the header light shows reading, done or failed.
- Override any proposal on the sheet. An operator position is kept and never re-proposed; machine positions are re-read on every reopen.
- Reopen a stored walk (Open roll, or make run-sheet). Positions are measured again from rolls/<roll>/prescanNN.tif with today's detector.
- Tick 'correct' in the window or pass --correct to let the in-walk reader (WalkReader) move each unapproved frame through _hold_to_approved. --correct-dry-run only logs the would-send command.
- Run tools/scan_roll.py --approved <walk folder> to propose and hold every frame without the window.
- Choose the film. Negatives and bw are read; positive and kodachrome are skipped (no positions, no in-walk reading).
- Type any prescan dpi from 25 to 7200 in the window, or pass --prescan-dpi. Only 300 dpi produces readings.
- Run FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py where the study frames exist.

**Should not do**

- Rotate or flip a prescan (keyboard shortcut or buttons) while a dry-run walk is running. Every later prescanNN.tif is written in the new arrangement while survey.json keeps the old one.
- Run tools/scan_roll.py --approved on a folder walked in the window with any rotation or flip set. The CLI does not un-orient the stored prescans.
- Rely on edge positions or 'correct' with a prescan dpi other than 300. 600 and 900 dpi passes (860/862 and 1292 columns) are refused frame by frame.
- Enable 'correct' on an unattended roll expecting two-member agreement. One member's gap-with-neighbour can move the film by up to the roll's 12 mm budget.
- Enable 'correct' on strips that begin or end with fogged or opaque leader. A near-black frame raises ZeroDivisionError in gapmodel and aborts the roll.
- Commission from the sheet while the edge light is still blue (reading). Already-placed frames may still change once they are re-read against the whole walk.
- Change anything under tools/frame_edges on a machine without the study frames and trust make test. The parity test is skipped there.
- Tick blank or partly blank frames that show a 'neighbours' position. The detector did not place them; the position is an interpolation.

**Unguarded mistakes**

- Turning or flipping during a walk: nothing warns, and the pictures still look right on reopen (the display re-applies the same turn). The detector and the hold references get mirrored or rotated frames: wrong-signed 'measured' proposals, or refusals filled from neighbours. Each turn also appends the frame to self.survey again.
- Using --approved on a window-walked, oriented folder: no check that the manifest's rotation/flipped is non-zero before the TIFFs are used as detector input and references.
- Setting the prescan dpi to 600 or 900: no warning that the edge detector will refuse every frame. The sheet just shows refused/none and the roll scans uncorrected.
- A near-black frame judged in-walk (correct on): the exception is not in scan_roll's except tuple, so the whole roll ends, possibly after the film has been nudged, and nothing records the move.
- 'Unconfirmed' (single-member) and 'neighbours' (no reading) positions are held automatically by --approved and by the in-walk correction, with no confirmation step.
- Commissioning during READING freezes preliminary readings into approved.json. The dialog only mentions frames not yet placed.
- Reopening an old survey.json with no 'film' key uses the window's current film selector, which may not be the film that was walked.

### Code identity and PROTOCOL_REVISION (gap pass)

**Can do**

- Run the window, tools/scan.py, tools/scan_roll.py or an ad-hoc DirectScanner(debug=True) script. Every filed entry gets scan.protocol_revision from the imported code and a provenance block computed at filing time.
- Run `tools/library.py reconstruct`, `verify`, `migrate-raw` and `migrate-direction`. None of them look at protocol_revision or provenance.
- Run `tools/library.py duplicates [--delete]`. This is the only command whose outcome depends on protocol_revision, through signature().
- Commit changes to COMMAND_UNITS, HOLD_TOLERANCE_MM, MAX_CORRECTION_PARAM, the frame-edge proposer or a tool's command order. Nothing forces a PROTOCOL_REVISION bump.

**Should not do**

- Do not switch, create or pull branches in the working tree while the window, a roll or a debug script is running from it. Entries filed afterwards record the new HEAD, and lazily imported modules (rps7200.uniformity at the first hold, rps7200.library and tiff at the debug flush) come from the new branch.
- Do not scan from a machine or shell where git is not on PATH, from a ZIP checkout, or from a repo git refuses as dubious ownership, if provenance matters. The entries record commit null and dirty false.
- Do not leave untracked scratch scripts in the repo while scanning. They make driver_dirty true on every entry and hide a real local modification.
- Do not scan with uncommitted edits to driver constants and then discard them. The entry says only 'dirty', and the code is lost.
- Do not delete experiment/ branches whose commits are named by library entries. The short sha then resolves to nothing.
- Do not edit an existing revision note in rps7200/protocol.py. Bump and append.
- Do not run `duplicates --delete` across revision 5 (the SLIDE law changed within it) or across the revision-6 scan_roll reorder.

**Unguarded mistakes**

- A parallel Claude session runs `git checkout -b feat/x` (as CLAUDE.md instructs) in the tree Stefan's window is running from. Every later entry is attributed to the feature branch, with no warning.
- A branch whose library.py needs a name the running process's direct.py lacks is checked out mid-run. At close() the lazy `from . import library` fails and every pending debug entry of the run is lost ('library unavailable ... lost').
- A frame is filed while someone runs `git commit` in the same tree. The commit can fail on .git/index.lock, and a `git status` killed at the 10 s timeout can leave a stale lock that blocks all git writes.
- A change to param_for_mm's constants or the hold deadband merges without a bump. Entries before and after share a revision and signature, and duplicates --delete can remove one of a pair placed by different SLIDE bytes.
- migrate-direction --write is interrupted after rewriting scan.tif (for example by an unreadable prescan.tif). The rerun never refreshes image.sha256, and verify reports a checksum mismatch forever.
- A demo run with --library pointing at the real library files real raw bytes again under the demo's provenance with protocol_revision null and no source id.

### Cross-process and thread concurrency (gap pass)

**Can do**

- Run `tools/library.py list`, `verify` or `reconstruct` while the GUI is open. They only read. An entry whose scan.json is being written at that moment is skipped for that run.
- Use Save as or Save all during a roll. Passes not yet filed are written as the reduced preview, and the message says so.
- Change the output folder, format or JPEG quality mid-roll. Frames already queued keep the old values and later frames use the new ones.
- Rotate or flip passes and use the sheet's rotate-all after a walk or roll has finished (not while it runs).
- Run tools/check_scanner.py on Linux or macOS while the GUI holds the scanner. The claim is refused before any command is sent, and the message says something else holds it.
- Open the contact sheet and the 1:1 view of filed passes at any time. Full-resolution reads only ever see complete entries in this process.

**Should not do**

- Run two processes that file into the same library at once. This includes two RPS7200_DEBUG=1 scripts whose flushes overlap, a `--demo --library library` window beside real scanning, or the GUI beside tools/scan.py, scan_roll.py or uniformity.py filing.
- Run `tools/library.py duplicates --delete`, `migrate-raw --write` or `migrate-direction --write` while the GUI or any scan tool is open or filing, and never interrupt migrate-raw --write.
- Rotate, flip or rotate-all while a walk or roll is running.
- Press Ctrl-B, open a roll from an already-open roll browser, or aim-click with 'aim' ticked while the scanner is busy.
- Leave a contact sheet open from one strip and walk another.
- Rename, delete or duplicate a roll folder right after its roll reports done, or while another window or tool uses it.
- Close the window while Save all or Export is still writing.
- Open two GUI windows on one checkout. They share library/, rolls/ and gui-settings.json.
- On Windows, reinstall the driver with Zadig because a second program reported 'Image/WIA driver' while another program might be holding the scanner.

**Unguarded mistakes**

- Pressing Ctrl-B (the default roll key) during a walk and confirming wipes the running walk's survey, orientations and sheet decisions and starts a new EdgeWatch generation. It queues a second walk whose prescans are never collected, and cancels any pending Stop.
- Double-clicking a roll in a browser that was opened before a walk started replaces the survey mid-walk. The live strip's prescans overwrite the reopened roll's frames of the same numbers in the frame-edge reader.
- Aim-clicking a prescan during a walk or roll queues a sub-frame Move that runs after the job ends, at a position it was not computed for, and clears any pending Stop.
- Pressing Stop, then anything that submits a job before the current frame ends, silently cancels the stop while the Stop button stays greyed.
- Rotating a prescan during a walk makes later prescanNN.tif disagree with survey.json's rotation. The reopened survey then shows and uses those frames turned, so detector offsets and hold references are wrong.
- An old contact sheet is brought forward after a new walk, and 'Scan chosen frames' there scans strip B with strip A's positions and references.
- In a corrected walk with an output folder set, each moved frame's delivered prescan copy is silently replaced by its pre-correction picture, and the log says 'picture N could not be filed: None'.
- Deleting or renaming a roll folder in the few seconds after 'done' makes the writer recreate the old folder with the last frames. Doing it from a second window aborts the other window's running roll.
- Quitting during Save all or Export leaves a truncated file under a final name without asking.
- A second GUI whose scanner open failed shows enabled buttons and an idle light. Calibrate, Scan and Roll then do nothing, with no message.
- `duplicates --delete` removing an entry that the open session shows makes Save as deliver a reduced preview while saying the scan 'is not filed yet'.
- Exporting a walked-then-scanned roll can deliver walk prescans as full-resolution frames. Which entry is chosen depends on directory order.

### Disk full, crashes, recovery, time (gap pass)

**Can do**

- Start a roll, walk or single scan with no check that rolls/, library/, the output folder or $TMP has room. At 3600 dpi RGBI a roll frame writes roughly 340 MB (frameNN.tif + scan.tif + raw.bin.gz), plus 120-142 MB per output copy.
- Name a roll anything in the GUI box or with --roll. The text becomes a folder path unsanitised (only approved.json and out_dir names go through _safe).
- Leave the roll name blank. The folder is then today's local date, shared by every unnamed walk and roll that day.
- Reopen a roll from Rolls... and press 'Scan chosen frames'. The destination is whatever the roll-name box holds at that moment, not the reopened folder.
- Resume a CLI roll with scan_roll.py --roll X --start-at N. This replaces roll.json.
- Press Stop (cooperative, after the frame in flight) or Force abort (type ABORT: closes the transport under a read).
- Close the window while a roll runs. It waits for the whole roll and every queued job.
- Save As, Save all and Export rolls while a roll is running. All three re-correct at full resolution; Save As does it on the UI thread.
- Flip through passes with the keyboard while a roll runs. Each step starts a full-resolution load thread.
- Delete library entries from the GUI, delete, duplicate or rename roll folders (refused only while busy or while that roll is open), and run duplicates --delete or migrate-raw/migrate-direction --write.
- Point --library/--rolls/--out/--settings (or RPS7200_SETTINGS/RPS7200_DEBUG_ROOT) at removable drives or network shares. All defaults are relative to the current directory.

**Should not do**

- Do not start a long roll without checking free space on every destination. Nothing stops the roll when filing fails.
- Do not kill the process, cut power or close the terminal during a roll. It abandons a read (wedge) and leaves partial roll.json, frame TIFFs and orphan library directories.
- Do not walk two strips on the same day without naming the roll. The second walk overwrites the first walk's survey.json and prescanNN.tif.
- Do not resume a reopened roll without retyping its exact name in the roll box. Otherwise it goes to a new folder, on a new day to today's date.
- Do not use '/', '\', '..', ':', '*', '?', '"', '<', '>', '|', or CON/PRN/AUX/NUL/COMn/LPTn in roll names.
- Do not resume a CLI roll into the same --roll folder with --start-at while the old roll.json is still needed. Copy it aside first.
- Do not interrupt migrate-raw/migrate-direction --write, or uniformity's redo prompt, while they rewrite an entry.
- Do not close the window while Save all or Export is running.
- Do not hold the next/previous-pass key through a 3600/7200 dpi session on a low-memory machine while scanning.
- Do not type a value into the Film 'frame' field before a roll. Every frame then shares one film.frame, which breaks the roll-to-entry join and makes all frames one 'duplicate' signature.
- Do not run two windows against the same gui-settings.json.

**Unguarded mistakes**

- A full disk during a roll: every later frame is scanned and discarded, shown only as log lines in the GUI and not at all in the CLI until the end, and roll.json marks them done.
- A kill or ENOSPC during the per-frame roll.json rewrite: the roll disappears from Rolls..., cannot be reopened even with its walk intact, and the next resume overwrites it silently.
- The documented GUI resume flow with an empty roll-name box writes to rolls/<today>, splitting the roll's files, manifest and library keys across two folders.
- A same-day unnamed second walk destroys the first walk's reference prescans and survey, and export can then mix two strips' frames.
- A roll name of '..' or an absolute path writes the roll outside rolls/. A Windows-illegal name fails only after the film has been wound to the first frame.
- scan_roll.py --start-at N erases the earlier frames' records from roll.json, although README says it carries them forward.
- A resumed commission rewrites approved.json with only the new frames, so exports orient the earlier frames by the roll default.
- An unwritable or full calibration/ directory discards each successful calibration, and the GUI then refuses every scan as uncalibrated.
- Save As over an existing file on a full or failing disk truncates the old file, and the error appears only on stderr.
- Closing the window mid-roll gives no stop option. It waits hours, which invites a hard kill.
- A CLI dry-run walk leaves nothing in the library: raw prescan bytes are never kept.
- Rolls scanned with tools/scan_roll.py are never matched to their library entries by the GUI, so Export reports no entries.

### The measure-scan-quality metrics (gap pass)

**Can do**

- Run `uv run python tools/dpi_analysis.py --root library --after <ISO> --before <ISO> --out <dir>` offline, with no scanner, to recompute the resolution study from stored entries.
- Run tools/byte14_probe.py or tools/fast_ir_probe.py with --dry-run to print the pass plan and time budget without opening the device.
- Run the probes for real only with RPS7200_DEBUG=1 (they refuse otherwise) and after asking Stefan. They file every pass through DirectScanner debug filing.
- Import dark_mask, relative_noise, noise_split, ceiling, agreement_z, colour_deviation and fixed_pattern from .claude/skills/measure-scan-quality/scripts/metrics.py when the working directory is the repo root.
- Run tools/make_comparison.py <scan.tif> <flat.tif> to regenerate 1_nothing_done.tif, 2_corrected.tif and 3_corrected_inverted.tif.
- Redirect or pipe any tool's output on a non-UTF-8 Windows console; every tool reconfigures stdout/stderr to UTF-8 with errors='replace' first.

**Should not do**

- Do not feed noise_split or agreement_z a repeat pair that has not been registered in rows and columns. The carriage start drifts 1-3 lines between passes, and a 16-column offset has been seen.
- Do not treat a noise_split random share near or above 100% as a result, or pass it to ceiling(). ceiling() clamps and returns its most optimistic answer.
- Do not compare colour_deviation arrays from two entries index by index unless frame, resolution and CCD mask all match; output column j is not the same sensor column otherwise.
- Do not quote dpi_analysis's B, A or C numbers without checking each rung's correction state. The 7200 dpi reference and its noise floor are always uncorrected.
- Do not press Ctrl-C during a byte14_probe pass: its finally block then starts another full scan on a device left mid-read.
- Do not run byte14_probe with a custom --ladder that lacks 0x10.
- Do not feed 8-bit prescans or mixed raw/corrected pairs to agreement_z; its DN thresholds and noise model assume 16-bit samples in one domain.
- Do not use make_comparison's 'worst column defect' / 'worst coloured column' numbers or its WARNING as a quality verdict; the skill forbids exactly these figures.
- Do not pass an even window to colour_deviation or fixed_pattern.
- Do not read the '~1.03 baseline' as meaning the noise model is nearly right. The ideal is 0.674.

**Unguarded mistakes**

- dpi_analysis silently includes any entry filed inside its time window (another frame, a GUI scan, a prescan, a demo entry) and treats the first two top-dpi entries as a repeat pair.
- dpi_analysis never tells the operator that the 7200 dpi reference is raw while the other rungs are corrected, or that a GUI entry was shaded twice.
- noise_split accepts misregistered and gain-mismatched pairs without warning, and ceiling() clamps a share above 1 silently.
- agreement_z accepts any dtype and domain. In fast_ir_probe, a NaN result becomes inf and then the verdict 'unchanged'.
- byte14_probe with a --ladder lacking 0x10 prints ratio 1.000 for every value and then crashes after the scans, so the only byte14-to-pass record (the JSON) is never written.
- byte14_probe's finally block issues a new scan after KeyboardInterrupt, CheckCondition or a USB/timeout error mid-read, which risks a wedge. That pass is filed with keep_raw=False, i.e. with stale raw bytes.
- make_comparison crashes after writing the three TIFFs if previews/ does not exist, and its default inputs are fixed paths under scans/.
- Copying the SKILL.md import line from a directory other than the repo root raises ImportError, and calling ceiling() after that import raises NameError.

### Tests that pin the defects (gap pass)

**Can do**

- Run tools/scan_roll.py (dry run or real) with --no-shading, or tools/scan.py with --no-shading --auto-exposure. Both still calibrate lazily inside the first prescan or probe, although the tool prints that shading is disabled.
- Set a frame offset anywhere up to ±9.39 mm (MAX_TRAVEL_MM, one param-87 command) in the frame-position window. Anything past 9.0 mm cannot be verified by the hold loop.
- Save all or Export into a folder that already holds files, as JPEG, with or without Pillow installed.
- Hand-edit gui-settings.json, and launch the window from any working directory (the default settings path is relative).
- Answer 'redo' during tools/uniformity.py capture, run capture phase 2 and repeat sessions under the default tag, and run analyse on all of it.
- Run tools/make_comparison.py on any TIFF, including a delivered and already corrected file.
- Walk or scan a roll with film=positive or kodachrome and aiming on, which uses the 36 mm frame model instead of the 350.6-unit one.
- Rebind every shortcut except the handlers in NEVER_BOUND; press the adjuster arrows, which change only the planned offset.

**Should not do**

- Do not use --no-shading on scan_roll (or on scan.py with --auto-exposure) to avoid calibration. It moves the calibration into the pass, which is the path recorded as stalling with LIBUSB_ERROR_PIPE.
- Do not set approved offsets beyond about 9.0 mm and expect the hold loop to confirm them. Three unverified frames in a row switch holding off for the rest of the roll.
- Do not rely on the 'Nothing already there is overwritten' dialogs for .dng companions or for the TIFF written when Pillow is missing.
- Do not point the output folder at storage that may fill or disappear during a roll. A failed delivered write aborts that frame's library entry, raw bytes included.
- Do not hand-edit gui-settings.json without a backup. A single syntax error silently resets it, and the next click erases presets, shortcuts and uncommissioned sheet decisions.
- Do not reuse the 'vignette-study' tag for phase 2 or a second session, and do not leave rejected passes under that tag if analyse should mean anything.
- Do not judge a correction change by make_comparison output. It does not apply shading.
- Do not read the histogram's 'at full scale' number as sensor clipping. It is measured on shading-corrected pixels.

**Unguarded mistakes**

- `scan_roll.py --dry-run --no-shading` prints 'shading correction disabled: expect vertical striping' and then calibrates lazily inside the first prescan, which is the documented wedge path. Nothing refuses the flag combination.
- An offset of 9.0-9.39 mm is accepted by the adjuster, sent by the hold loop, and then reported 'could not be verified'. No warning is shown when the offset is set.
- Save all as JPEG without Pillow silently overwrites same-stem .tif files that are already in the folder.
- A JPEG Save all of RGBI passes silently overwrites same-stem .dng files that are already in the folder.
- A corrupt gui-settings.json is silently discarded and then overwritten. No message, no .corrupt copy.
- A dry-run roll with aiming and an output folder can deliver the pre-correction prescan under the frame's name, because both prescans claim the same name before either is written.
- A pass marked 'redo' in uniformity capture is still used by analyse, as the reference if it is the oldest as-is pass.
- Once phase 2 is captured under the default tag, `analyse --tag vignette-study` refuses on every run.
- make_comparison accepts a corrected delivered TIFF as '1_nothing_done', and nothing checks what the input is.
- The shortcut editor says ←/→ 'Move the film'. They do not, and a later single Scan ignores the planned offset.

