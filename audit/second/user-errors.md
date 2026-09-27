# What an operator can do, should not do, and is not stopped from doing

[Back to the summary](README.md)

Collected from every area at `03aacba`: the findings filed as user errors, then each area's
lists of what an operator can do, should not do, and the mistakes nothing guards against.

## What changed since the first audit

- Busy guards now sit on the actions, not only on the buttons.
- Stop drops queued jobs, and quitting offers to stop after the frame in flight.
- Rolls get their own folders.
- A turn during a walk is recorded with what it reached.
- Calibration asks what is in the transport, in the window.
- Arguments that cannot work are refused before the device is opened.

## What is still open

- **"Reverse the direction"** is remembered across launches and silently mirrors every
  approved hold target of a commissioned roll (FR-01).
- **Ctrl-C:**
  - The first press is ignored through calibration and metering in `tools/scan.py`, and is not
    honoured between phases (CLI-02, CSA-03).
  - Filing after close is outside the guard (CSA-04).
  - Most probes and the filing load test abandon the read in flight (TP-11, PAT-03, CLI-15).
  - Closing the terminal that launched the window kills its threads mid-pass (SR-19).
- **The roll browser:**
  - Open is not guarded while the scanner works (GUI1-02, GUI2-02).
  - Rename and Delete protect only a roll reopened from the browser (GUI1-10, GUI2-08).
  - Delete's dialog understates what is lost (GUI1-05, DOC-08).
- **The contact sheet:**
  - Double-clicking a thumbnail toggles its tick (GUI2-04).
  - Return and All re-tick scanned frames (GUI2-05).
  - Decisions come back onto a new strip walked into a used folder name (GUI2-03, GUI1-03).
- **Calibration from the CLI** on an empty transport is unguarded (TP-09), and `tools/uniformity.py` calibrates in an unprompted state (LIB-08).
- **7200 dpi** is offered in the window and refused only once a pass is due (GUI1-08).
- **A flat or blank frame** ends a walk or roll as "end of film", with exit 0 (FR-07, CLI-14).
- **A CLI resume** mixes the earlier run's settings with new ones and re-scans everything from
  `--start-at` (CLI-11, CLI-10).

## Findings filed as user errors

| Finding | Severity | Title |
|---|---|---|
| [FR-01](areas/framing-units.md#framing-units-fr-01) | high | 'reverse the direction' checkbox (remembered across launches) silently mirrors every approved hold target in a commissioned roll |
| [TP-09](areas/transport-protocol.md#transport-protocol-tp-09) | medium | CLI calibration on an empty transport is unguarded; calibrate_shading reads READ STATE but ignores the measured byte-8 media flag |
| [TP-11](areas/transport-protocol.md#transport-protocol-tp-11) | medium | Ctrl-C in most tools abandons the read in flight; only scan.py and scan_roll.py defer it |
| [DBG-10](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-10) | medium | Probe tools accept RPS7200_DEBUG=0/false as 'on' while DirectScanner treats it as off |
| [DEMO-02](areas/demo-parity.md#demo-parity-demo-02) | medium | `--demo` accepts `--library` and `--rolls` overrides without a guard, so synthetic demo entries and walks can land in the real library and real roll folders |
| [FR-07](areas/framing-units.md#framing-units-fr-07) | medium | A blank or very flat frame ends a walk or roll as 'end of film' |
| [GUI1-08](areas/gui-part1.md#gui-part1-gui1-08) | medium | 7200 dpi is offered and accepted, but every corrected pass there is refused; a roll moves film and meters three frames before giving up |
| [GUI1-10](areas/gui-part1.md#gui-part1-gui1-10) | medium | Rename/Delete in the roll browser protect only a roll reopened with open_roll, not the walk this session is deciding on |
| [GUI2-02](areas/gui-part2.md#gui-part2-gui2-02) | medium | Roll browser 'Open' has no busy guard: opening a roll during a walk mixes two strips in one sheet and files decisions in the wrong folder |
| [GUI2-04](areas/gui-part2.md#gui-part2-gui2-04) | medium | Double-clicking a thumbnail (the documented way to set a position) toggles its tick |
| [GUI2-05](areas/gui-part2.md#gui-part2-gui2-05) | medium | Return in the position window, and All, re-tick frames already scanned, and the confirm dialog doesn't say they will be rescanned |
| [GUI2-07](areas/gui-part2.md#gui-part2-gui2-07) | medium | Aim moves the film from whatever prescan is on screen, even one that no longer shows where the film is |
| [GUI2-08](areas/gui-part2.md#gui-part2-gui2-08) | medium | The roll-in-use guard checks only the last roll opened from the browser; the sheet's own roll can be deleted or renamed and is then recreated empty by Scan chosen frames |
| [GUI2-A1](areas/gui-part2.md#gui-part2-gui2-a1) | medium | A contact sheet opened while a walk is still running is never rebuilt: walk end only raises it, so frames walked after it opened have no cell and cannot be ticked |
| [CLI-12](areas/cli-operator-tools.md#cli-operator-tools-cli-12) | medium | --approved proposes positions on the walk's film but scans and meters on --film (default negative), with no warning |
| [CLI-14](areas/cli-operator-tools.md#cli-operator-tools-cli-14) | medium | A low-contrast frame ends the roll silently with exit 0, even when --frames asked for more |
| [PAT-05](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pat-05) | medium | exposure_probe --only chunking scans the wrong physical frames under plan numbers, and each chunk overwrites the JSON that joins passes to the library |
| [DOC-08](areas/docs-readme-claude.md#docs-readme-claude-doc-08) | medium | The roll Delete dialog says everything but approved.json can be rebuilt; prescanNN-before.tif and old walks' prescans are only in the folder |
| [CSA-03](areas/changes-since-first-audit.md#changes-since-first-audit-csa-03) | medium | First Ctrl-C is not honoured between phases: scan.py proceeds from calibration/metering into the scan, scan_roll.py from the seek into a 3-4 min calibration |
| [CSA-04](areas/changes-since-first-audit.md#changes-since-first-audit-csa-04) | medium | Filing after close() is outside DeferredInterrupt: a Ctrl-C while the last frames gzip kills the daemon writer and loses queued frames |
| [FE-02](areas/frame-edges-member-detectors.md#frame-edges-member-detectors-fe-02) | medium | A slide read as a negative (the default film) comes out 'measured, centred'; stepline's 'not a negative' refusal is outvoted |
| [PLAT-04](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-04) | medium | On Windows, 'another process holds the scanner' is diagnosed as the wrong driver and sends the operator to Zadig; on macOS the helpful text is attached to the wrong failure |
| [TP-18](areas/transport-protocol.md#transport-protocol-tp-18) | low | RPS7200_MAX_WINDOW is not validated: 0 or a negative value loops forever mid-read; a non-integer breaks import |
| [DBG-9](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-9) | low | Debug entries are filed into RPS7200_DEBUG_ROOT or ./library, not the library the session or tool is using |
| [DEMO-V01](areas/demo-parity.md#demo-parity-demo-v01) | low | In `make run-sheet` (--look-only) the sheet's scan path can be reached only by ticking 'The film is in the transport', which is false there |
| [LIB-17](areas/library.md#library-lib-17) | low | Debug filing ignores the caller's library root, and tools/uniformity.py never claims its passes (double filing with debug on) |
| [SR-14](areas/session-roll.md#session-roll-sr-14) | low | Roll settings the driver will refuse are checked only after the seek has moved the film and the folder exists; at 7200 dpi three frames are prescanned, metered and passed before the roll gives up |
| [FR-13](areas/framing-units.md#framing-units-fr-13) | low | GUI lets a 'correct' roll run at a prescan dpi the edge reader cannot read (warning only); the CLI refuses |
| [GUI1-06](areas/gui-part1.md#gui-part1-gui1-06) | low | Double-pressing Scan or Prescan queues two passes; the busy guard arrives only with the worker's next event |
| [GUI1-07](areas/gui-part1.md#gui-part1-gui1-07) | low | Typing a folder into 'Save scans to' has no effect until the next launch |
| [GUI1-12](areas/gui-part1.md#gui-part1-gui1-12) | low | Output format, output folder and arrangement changed while a roll runs apply to the remaining frames |
| [GUI1-15](areas/gui-part1.md#gui-part1-gui1-15) | low | Pass Delete makes 'No' the answer that permanently deletes the raw library entry |
| [GUI2-12](areas/gui-part2.md#gui-part2-gui2-12) | low | The sheet's option panel allows combinations the driver refuses only after the film has been wound (7200 dpi; infrared on B&W or Kodachrome) |
| [GUI2-26](areas/gui-part2.md#gui-part2-gui2-26) | low | Scan chosen frames closes the sheet before asking; Cancel, busy or a calibration prompt leaves it closed |
| [OUT-09](areas/outputs.md#outputs-out-09) | low | 'Nothing already there is overwritten' does not hold for the DNG companion, the no-Pillow TIFF fallback or a roll's own frame files |
| [OUT-11](areas/outputs.md#outputs-out-11) | low | A reduced preview is delivered under a full-resolution file name |
| [OUT-13](areas/outputs.md#outputs-out-13) | low | tools/scan.py overwrites --out and its .json without asking, and --quality is not clamped |
| [OUT-15](areas/outputs.md#outputs-out-15) | low | Mono delivery silently drops the infrared plane, and to_monochrome accepts 'I' |
| [CLI-16](areas/cli-operator-tools.md#cli-operator-tools-cli-16) | low | scan_roll: a Ctrl-C before the first frame still calibrates, and on a dry run replaces the folder's survey.json with an empty walk |
| [CLI-17](areas/cli-operator-tools.md#cli-operator-tools-cli-17) | low | `--library ''` means skip in scan_roll.py but files into the current directory in scan.py |
| [CLI-19](areas/cli-operator-tools.md#cli-operator-tools-cli-19) | low | --reuse accepts a cached reference of any age without saying how old it is; --reference into a library entry without --reuse overwrites that entry's reference |
| [CLI-27](areas/cli-operator-tools.md#cli-operator-tools-cli-27) | low | Default output locations are relative to the current directory while imports are relative to the repo |
| [DOC-10](areas/docs-readme-claude.md#docs-readme-claude-doc-10) | low | The window offers 7200 dpi and does not refuse an uncorrectable roll before it starts |
| [DOC-16](areas/docs-plans-todo.md#docs-plans-todo-doc-16) | low | tools/scan.py calibrates straight after INQUIRY without asking; --reuse loads a cache with no timestamp or identity |
| [DOC-20](areas/docs-plans-todo.md#docs-plans-todo-doc-20) | low | Open window/roll problems listed in TODO are all still present (misleading dialogs, inert nudge tick, one-way approved.json, millimetres stored and printed) |
| [CSA-16](areas/changes-since-first-audit.md#changes-since-first-audit-csa-16) | low | settings.load moves a valid settings file aside on any transient read error and silently reverts the window to defaults |
| [T-14](areas/tests.md#tests-t-14) | low | Destructive and quit/abort operator paths are untested: 'No' to keep-entry, quit while busy, force abort mid-read, duplicates --delete |
| [CRA-10](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-10) | low | Every writer creates missing roots with mkdir(parents=True), so a folder on an unmounted drive can be recreated on the system disk and filled there |
| [CONC-07](areas/thread-and-process-concurrency.md#thread-and-process-concurrency-conc-07) | low | A window whose session could not claim the scanner accepts every job into a queue no thread reads, marks itself calibrated, starts a roll countdown and still writes approved.json |
| [FE-06](areas/frame-edges-member-detectors.md#frame-edges-member-detectors-fe-06) | low | scan_roll --no-shading with --correct (or a --no-shading walk proposed later) feeds uncorrected prescans to a detector validated only on corrected ones, unguarded |
| [PLAT-06](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-06) | low | Case-insensitive filesystems: case-only Rename is refused as 'already there', and a typed spelling of an existing roll splits keys and guards on macOS and makes a new roll on Linux |

## Per area: can do, should not do, unguarded

### USB transport, protocol and command sequence

**Can do**

- Run tools/check_scanner.py at any time: it opens and claims the device and sends only INQUIRY and READ STATE (plus REQUEST SENSE if a condition is queued). It exits 0 when no scanner is present.
- In the window: Calibrate (asks whether film is loaded first), Prescan, Scan, Roll or Walk, Move (sub-frame nudge through DirectScanner.nudge, capped at param 87), prev/next slide, Stop (takes effect between passes), and Force Abort (requires typing ABORT).
- Run tools/scan.py and tools/scan_roll.py, where the first Ctrl-C finishes the pass in flight and a second aborts (DeferredInterrupt).
- Set RPS7200_DEBUG=1 to spool every pass and file it after close(), RPS7200_DEBUG_ROOT to redirect filing, RPS7200_MAX_WINDOW to change the bulk window, and LIBUSB_PATH to pick a libusb.
- Run tools/parse_capture.py and tools/verify_capture.py offline against captures/*.pcapng. They never return interrupt (keyboard) payloads.
- Run tools/verify_protocol.py stages 1-16 against the hardware (with Stefan's agreement).

**Should not do**

- Press Force Abort, or Ctrl-C twice, while a pass or calibration is reading. That abandons the read and needs a power cycle.
- Call DirectScanner.stop_scan(), Transport.open(reset=True) or Transport.reset(). They send STOP SCAN and IEEE1284 RESET, which leave the device unresponsive.
- Send SET_SCAN_HEAD (0xD2). No code path does, but the opcode is exported from rps7200.direct.
- Calibrate with an empty transport.
- Keep scanning in the same window session after a pass failed mid-read (DeviceSuspect). Power-cycle and reopen instead.
- Run verify_protocol stages 8a, 12, 14 or 15 (invented SLIDE payloads, params up to 255, param 0) or any probe tool without asking.
- Set RPS7200_MAX_WINDOW to 0, a negative number or a non-integer.
- Run two programs against the scanner at once.

**Unguarded mistakes**

- tools/scan.py calibrates immediately with no question about film. calibrate_shading reads READ STATE but ignores the byte-8 empty-transport flag, so an empty-transport calibration (which preceded a wedge) is one command away.
- After a pass is abandoned (suspect), pressing Scan or Prescan in the same window session still sends READ STATE, TEST UNIT READY, six WRITE sub-commands, SET SCAN FRAME, CMD 17, READ and WRITE GAIN OFFSET and MODE SELECT before DeviceSuspect is raised at SLIDE INIT.
- Typing a single manual exposure value with infrared ticked also multiplies the infrared exposure; '2' and '2 2 2' send different payloads.
- A single Ctrl-C in any tool other than scan.py and scan_roll.py (verify_protocol, filing_load_test, uniformity, the probes) abandons the read in flight.
- Force Abort (or a crash) leaves every debug-spooled pass of the session unfiled in a system temp directory, with no message giving its path.
- RPS7200_MAX_WINDOW=0 makes the first READ (INQUIRY at open) loop forever. A non-integer value breaks import of rps7200.usb_transport, including for offline decoding.
- Running transport_truth, hold_probe, roll_registration_walk or exposure_probe moves the film +2.84 units (session_start's SLIDE 00 01 00 00) and then fails with ShadingUnavailable at the first corrected prescan.
- A recalibration that is cut short by any refused read is accepted as complete, overwrites the good cached reference, and is used for every later scan (TP-01).

### Decode, direction, shading and debug filing

**Can do**

- Turn automatic filing of every pass on with RPS7200_DEBUG=1/true/yes/on or DirectScanner(debug=True). Passes are spooled during the session and filed after close().
- Redirect debug entries with RPS7200_DEBUG_ROOT.
- Take a raw pass deliberately with shading=False (--no-shading). The entry records SHADING_SKIPPED_EXPLICIT and library.corrected returns it as 'deliberately raw'.
- Reuse a cached shading reference (--reuse / load_shading / window 'reuse') to skip the 3-4 minute calibration.
- Choose tied infrared (default) or untied infrared (--no-fast-ir). The read's idle timeout follows the choice (read_idle_s).
- Re-decode every stored pass with current code (tools/library.py reconstruct, library.decode_raw) and re-correct with today's apply_shading (library.corrected).
- Force-abort a stuck job from the window (ScanSession.force_abort), accepting the loss of the frame and a power cycle.

**Should not do**

- Force-abort, kill or crash the process with debug on: the spool is never filed and stays in the system temp dir.
- Write an ad-hoc script with debug on without `with DirectScanner(...)` or an explicit close(): nothing it scanned is filed.
- Run a long roll with debug on where the temp dir is small or RAM-backed (tmpfs). Claimed frames are spooled too and held until close.
- Reuse a cached shading reference across a power cycle or long gap. It describes another sensor state, nothing checks, and the entry cannot be tied back to its calibration bytes.
- Rely on debug filing to land beside a custom --library.
- Call DirectScanner.stop_scan(): it sends STOP SCAN.
- Set RPS7200_DEBUG to anything other than 1/true/yes/on and expect probes to file.

**Unguarded mistakes**

- RPS7200_DEBUG=0, false or 2 passes every probe tool's guard (`os.environ.get(...)` is truthy) while DirectScanner treats it as off, so a full probe run files nothing.
- `--library D:/lib` in the window or tools sends frames there, while debug-filed probes and prescans go to ./library under the current directory.
- After force_abort the session's spooled probes and prescans are never filed, and no tool files an orphaned spool.
- A filing failure (full disk, bad library path) after a pass was claimed loses that pass entirely, because the debug copy was unlinked at close().
- With debug off (the default), a roll keeps no raw bytes of any frame's prescan, and the aim 'before' prescan and metering probes are not kept at all.
- An ambiguous ASC 0x20 mid-read yields a short image filed as a normal pass, with the device not marked suspect.
- A calibration cut short by a non-end-of-data refusal installs a partial (possibly dark-only) reference that corrects every later scan.
- gain_probe --ladder values above 255 are sent masked (&0xFF) while the entry records the unmasked value.

### Demo scanner vs the real scanner

**Can do**

- Run `make run-demo` (tools/gui.py --demo). It reads library/ plus any 'library *' folders beside it and writes demo/library, demo/rolls, demo/gui-settings.json and demo/pictures.npz, with no scanner on the bus.
- Run `make run-sheet` (--demo --look-only --open-roll rolls/aligned-strip). It shows a real stored walk read-only, recomputes edge proposals and lets the operator tick frames and commission a roll; every pass, move or roll then fails with 'there is no film in the transport'.
- Use `--demo-source <dir>` to choose which library supplies pictures; a second roll started from the Roll button draws other photographs from every library beside it.
- Use `--demo-entry <path>` to ask for a specific entry. It only takes effect when no raw-byte entry of the chosen film exists (DEMO-04).
- Calibrate (measure/reuse/off), prescan, scan RGB or RGBI with auto exposure, walk or scan a roll, set positions on the contact sheet, nudge, step frames, Stop, and Force abort (after typing ABORT). All of these go through the real session and borrowed driver loops.
- Override --library, --rolls, --reference, --settings and --out together with --demo. Nothing refuses any combination (DEMO-02).
- Delete a result. The library entry is removed only when it lies inside the session's library (gui.py on_delete _within check).

**Should not do**

- Pass `--library library` or `--rolls rolls` (or any real path) together with `--demo`: synthetic entries and walks are filed as if real.
- Point `--demo-source` at demo/library. The demo then draws from its own synthetic entries, and its shape table (_shape_for) and pools feed on themselves.
- Treat demo outcomes as evidence about the hardware: hold or aim convergence (backlash is not modelled at frame entry, and a deliberate slip at strip position 3), metering scales, progress line counts, timings (divided by 120), or calibration behaviour.
- Use demo/library entries with analysis tools (registration_margin, dpi_analysis, uniformity, duplicates/prune), or merge them into a real library.
- Run two demo windows at once, or quit during the first launch's signature pass. Both share and non-atomically write demo/pictures.npz.
- Launch from another working directory. `demo/`, `library` and `rolls/aligned-strip` are all relative to CWD.
- Tick 'The film is in the transport' under --look-only just to get past the calibration prompt. It is false, and on hardware calibrating with an empty transport preceded a wedge.

**Unguarded mistakes**

- `--demo --library library` silently files demo entries into the real library. They use normal ids and tags, reconstruct as 'identical', are skipped by verify's missing-reference check, and are later drawn on by the demo itself and by analysis tools.
- `--demo --rolls rolls --open-roll rolls/X` makes `_roll_folder` return the real walk folder, so approved.json, roll.json and frames are written back into the walk that was only meant to be shown.
- With `--library` pointing at the real library under --demo, Delete in the window removes real entries, raw bytes included, after one confirmation.
- A mistyped `--demo-entry` is accepted: open() logs 'demo mode: showing <name>' and pictures come from other entries.
- A mistyped `--demo-source` or a missing library silently gives 574x862 test cards at every resolution, uncorrected even with shading on.
- Calibrate with shading set to 'reuse' and no cache: the demo reports 'shading loaded (demo)' in about 1 s, where the scanner would run a 3-4 minute calibration.
- Quitting the demo while pictures.npz is being written leaves a truncated cache. The signature thread then dies on every later launch and second-roll strips silently stop drawing on other libraries.
- Under --look-only, a roll whose start is not the current frame fails at the seek's advance with a UsbError, not a FilmNotPlaced. The roll folder and approved.json written by on_scan_chosen are left behind in demo/rolls.

### The library store

**Can do**

- List, verify, reconstruct, reindex, find duplicates, migrate-raw, migrate-direction and tag entries with tools/library.py (make verify / make reconstruct).
- Delete redundant entries with `tools/library.py duplicates --delete [--keep N]`. Only entries with the same signature AND the same raw (or image) checksum are offered.
- Mark an entry deliberately uncalibrated with `tools/library.py tag ENTRY --add uncalibrated-on-purpose` to silence verify.
- Copy or rename an entry directory: entry_path() follows the directory, and verify reports the id mismatch.
- Run the vignette study: `tools/uniformity.py capture [--ir] [--reuse] [--exposure-scale S] [--dpi N] [--tag T]`, answering accept/redo/abort per pass, then `tools/uniformity.py analyse --tag T [--out file]`.
- Export or Save As any entry from the window; it is re-corrected by library.corrected() with today's code and the recorded rotation/flip.
- Set RPS7200_DEBUG=1 (and RPS7200_DEBUG_ROOT) so probes and hold/aim prescans are filed too.

**Should not do**

- Delete or move calibration/: entries link to their calibration bytes only through a CWD-relative path there, and nothing else in the library holds those bytes.
- Run migrate-raw --write on labelled legacy entries without first checking reconstruct: a changed decode or a missing reference is rewritten anyway.
- Tag an entry 'uncalibrated-on-purpose' when its calibration.skipped says correction was asked for: it hides a real shortfall from verify.
- Kill the window while it closes: plain entries then stay uncompressed for good, and a partial compact leaves false checksum alarms.
- Run tools from a directory other than the repository root: library/, calibration/ and the debug root are all relative to the current directory.
- Use `uniformity.py capture --reuse` across a power cycle, or calibrate for the study with an empty transport.
- Edit scan.json by hand: it has no checksum, and verify trusts it.

**Unguarded mistakes**

- Scanning a real roll with debug off loses every frame's raw prescan bytes, and the prescan_before of corrected frames. Only the corrected prescan.tif survives, unlabelled.
- A full or locked library disk loses the whole frame (raw bytes and delivered copies), even when the output folder has room. A locked index.json alone does the same to a completely filed entry.
- Trusting a green `make reconstruct` whose lines show 'could not decode' or 'could not read scan.tif': the exit code is 0.
- Answering 'redo' during a uniformity capture leaves the rejected pass in the analysis, where it is used as the base or orientation pass.
- Running `uniformity.py capture` without --exposure-scale crashes at metering after the operator emptied the transport. `--ir` is always refused, and only after a 3-4 minute calibration.
- The uniformity capture flow calibrates with the transport just emptied, which CLAUDE.md says preceded a wedge.
- Running a tool with `--library elsewhere` and RPS7200_DEBUG=1 files the unclaimed passes in ./library. tools/uniformity.py with debug on files every pass twice.
- Deleting prescan.tif from an entry goes unnoticed by verify.
- Running migrate-raw --write twice after an interruption overwrites scan.before-migrate-raw.tif with the raw decode.

### ScanSession, FrameWriter and rolls

**Can do**

- Calibrate: measure a new shading reference (moves the carriage) or reuse the cached calibration/shading.npz.
- Prescan at any dpi. The pass is filed in the library with its raw pixels and bytes, and copied to <out>/prescans if an output folder is set.
- Scan one frame at 25-7200 dpi, RGB or RGBI, tied or untied infrared, auto or fixed exposure. It is filed raw (compressed at close) and delivered corrected, oriented and optionally mono to the output folder.
- Walk a strip (dry-run Roll) from any start frame, optionally extending the previous walk in the same folder, with or without in-walk correction (correct / correct_dry_run).
- Commission a real roll from the contact sheet with chosen frames (only), per-frame approved positions and orientation, or from the Roll button over a frame range.
- Move the film whole frames forward/back, or nudge it sub-frame (Move.millimetres, planned by plan_nudges).
- Press Stop: the pass in flight completes, a roll ends after the current frame, and queued jobs are dropped.
- Force abort after typing ABORT: closes the USB transport under a running read.
- Quit while busy: 'Yes' stops after the frame in flight and then quits, 'No' waits for all queued work, 'Cancel' keeps working.
- Change rotation, flip, output folder, output format or JPEG quality, or the match-prescan toggle, while a roll runs. Frames written afterwards follow the new values and each record states what its file got.

**Should not do**

- Force-abort during a read: the frame is lost, the scanner almost certainly needs a power cycle, and with RPS7200_DEBUG=1 the debug spool is orphaned in the temp directory.
- Press Ctrl-C in, or close, the terminal that launched the window while it scans: the daemon threads die mid-read.
- Reuse a roll name or folder for a different strip: roll.json merges by frame number, frameNN.tif and prescanNN.tif are overwritten in place, and the old strip's approved.json stays beside the new frames.
- Walk again, or extend, a legacy walk folder whose walk did not start on frame 1: renumbered records keep the old prescan filenames and new files can overwrite them.
- Keep the library on a nearly full disk: a failed library.save loses the frame entirely and the in-flight frame with it.
- Start a roll at 7200 dpi: every frame is refused as uncorrectable after its prescan and metering.
- Start another scanner program while the window holds the device open.

**Unguarded mistakes**

- A roll at 7200 dpi, or with infrared on black-and-white or Kodachrome film, is not refused before the seek winds the film and creates the roll folder. At 7200 dpi three frames are prescanned, metered and advanced past before max_failures ends the roll, and the job is reported as finished.
- Stop pressed during a seek or rewind at the start of a roll is honoured only after the whole wind completes. A Move(frames=N) is never checked for stop between frames.
- A walk with correction and an output folder set silently replaces each corrected frame's output-folder prescan with the pre-correction picture.
- A library write failure with RPS7200_DEBUG=1 deletes the spooled backup of that pass at close, because the session claimed it when queuing.
- Turning or flipping the picture in the window during a roll changes the files of the frames that follow. This is recorded per frame, but no warning is shown.
- Rapid clicks before the worker's 'busy' state reaches the window can queue several moves or nudges, because the refusal reads a flag updated only on the event poll.
- Quitting the window right after a large roll: the last frames are still being gzipped with the device open and idle, and nothing indicates that is happening.

### Framing and transport units

**Can do**

- Walk a strip (dry run) at any prescan dpi. Frame edges are read only on 300 dpi (428-column) negative/B&W prescans, and other dpi get a warning before the walk.
- Open the contact sheet while the edges are still being read, and watch positions and edge lines update until the light goes green.
- Set a frame's position in the adjuster (drag, arrow steps, 'Centre' = as surveyed, reset to the detector). Positions are snapped to the SLIDE lattice and clamped at 88.8 units.
- Commission 'Scan chosen frames'. Every ticked frame gets an Approved (the operator's, the detector's, or 0 'none'), approved.json is written, and the roll holds each frame to it.
- Tick 'aim each frame while prescanning' (correct) or its dry-run variant on any film and any prescan dpi.
- Tick 'reverse the direction' in the Transport panel. It is remembered across launches and also applies to commissioned rolls.
- Reopen a roll. Positions are re-proposed from prescanNN.tif with today's detector; stored operator positions win.
- Run tools/scan_roll.py --approved <walk> (re-proposes and holds), --correct (refused at unreadable dpi) or --correct-dry-run.
- Edit shortcuts in the editor, or hand-edit gui-settings.json and approved.json.

**Should not do**

- Leave 'reverse the direction' ticked when commissioning a roll: every approved position is mirrored.
- Use 'aim each frame' on positive or Kodachrome film: it runs the legacy negative-only base detector on the disproven 36 mm model.
- Walk at 600 or 900 dpi and expect proposals, or run a correct roll there.
- Set or accept offsets beyond about 84.7 units: the hold loop cannot verify moves larger than its 105-column search.
- Commission while the edge light is still blue: unread frames are held at 'as surveyed' and later readings do not reach the running roll.
- Expect scan_roll --approved to use the positions and turns set in the sheet (approved.json is ignored).
- Hand-edit shortcut sequences or offset values: a malformed sequence stops the window from opening, and NaN becomes a maximum move.
- Start a roll over a strip with a blank or unexposed frame in it and expect the rest to be scanned: it ends there as 'end of film'.

**Unguarded mistakes**

- The 'reverse the direction' checkbox silently negates all hold targets of a commissioned roll. It is not shown in the confirm dialog, and the wrong_way check cannot see it (gui.py:3194, direct.py:3344).
- correct/aim on non-negative film runs the legacy detector with no warning (propose.py:74 returns None for such films; direct.py:3896).
- The GUI allows a correct roll at an unreadable prescan dpi after a single OK (gui.py:2224-2229), while the CLI refuses the same thing.
- An operator or detector offset of 85-88.8 units is accepted by snap_offset, but the verification cannot see it (framing.py:857, gui.py:5897).
- A blank frame mid-strip ends a walk or roll with only a log line (direct.py:3987-3993).
- scan_roll --approved on a folder whose sheet positions were hand-set discards them without saying so (scan_roll.py:293-297).
- A NaN in approved.json or gui-settings.json offsets becomes +88.8 units (gui.py:5897, 5463).
- A malformed shortcut string in gui-settings.json raises TclError at startup (gui.py:567, 907).
- Hold/aim prescans and the pre-move prescan are not filed raw unless RPS7200_DEBUG=1, so the evidence for every move is lost by default (FR-02).

### The window (tools/gui.py, first half)

**Can do**

- Calibrate by measuring, which requires ticking 'The film is in the transport' in the non-modal prompt, or load the cached reference, which asks nothing and moves nothing.
- Prescan and Scan from the panel buttons, which ask no confirmation, or from keys, which confirm with a time estimate. The resolution can be any typed integer from 25 to 7200.
- Walk a strip (dry run), optionally with in-walk aiming or aim-dry-run, then add further walks to the same sheet or start a new one.
- Tick frames on the contact sheet, adjust positions and turns, override dpi/prescan/film/meter/IR/correct in the sheet's own panel, and commission 'Scan chosen frames', which writes approved.json first and then submits a Roll that seeks and holds each frame.
- Scan a range of frames with the Roll button (first and last frame boxes; an empty last frame means to the end of the strip).
- Move the film by whole frames (prev/next slide), by fine nudges in param units (one command at most), or by clicking a prescan in aim mode (always confirmed).
- Stop, which finishes the running pass or frame and drops queued jobs. Force abort, which requires typing ABORT and closes the transport under the read.
- Rotate, flip, straighten and 'show prescan' on any pass. These carry into subsequent files and the session default.
- Save As a single pass, Save all visible passes, or Export whole rolls. All are re-corrected from the library with today's code.
- Delete a pass from the session and optionally its library entry, which means rmtree of the raw bytes.
- In the Rolls browser: open, export, duplicate, rename, reveal, and delete roll folders.
- Save and delete presets, restore controls to their shipped defaults, reset a single control or panel, and rebind keyboard shortcuts.
- Choose the output folder and format (TIFF, or JPEG with a quality from 60 to 100).
- Quit while busy: stop after the frame in flight, wait for the whole queue, or cancel.

**Should not do**

- Calibrate with an empty transport. The prompt asks, but only the operator can see the transport.
- Keep 'reuse the cached reference' selected across power-ons. The Calibrate button then silently loads a stale reference.
- Force abort a running read. It abandons the read, wedges the scanner and skips debug filing.
- Open another roll from a Rolls browser left open while a walk or roll is running.
- Duplicate a roll, rescan either copy, and then Export either one.
- Re-walk a strip into a typed existing roll name and expect a fresh sheet.
- Rename or delete the roll folder whose walk the contact sheet is currently deciding on.
- Change the output format or folder, or rotate or flip old passes, while a roll is scanning.
- Pick 7200 dpi (scan or prescan) for a roll or commission.
- Answer 'No' to 'Keep the library entry?' unless the raw bytes are really meant to go.
- Delete roll folders that hold prescanNN-before.tif or walk registration records that are needed later.
- Type an output folder path into the box and expect it to be used in this session.

**Unguarded mistakes**

- Double-clicking Scan or Prescan queues two full passes, because the busy greying arrives only after the next 120 ms poll of the worker's state event and on_scan/on_prescan do not check busy (GUI1-06).
- Double-clicking a roll in an already-open Rolls browser during a walk replaces the survey and sheet state mid-walk and mixes two strips' frames and decisions into one sheet (GUI1-02).
- Answering 'No -- start a new sheet' while the roll box holds a typed existing name brings back the old sheet's ticks, turns and operator positions onto the new walk (GUI1-03).
- Duplicating a roll and rescanning one copy makes Export deliver the newest entries for both copies, silently (GUI1-01).
- Exporting or saving while the window's film is set differently from the passes' film delivers colour frames as one grey channel, or B&W frames as three channels (GUI1-04).
- Typing or pasting a folder into 'Save scans to' is ignored until the next launch, and clearing it by hand does not stop the copies (GUI1-07).
- Choosing 7200 dpi for a roll or commission winds, holds, meters and advances three frames before the roll gives up (GUI1-08).
- Pressing Calibrate with 'reuse' remembered loads a reference of any age with no warning (GUI1-09).
- Renaming or deleting the current walk's folder from the browser splits the subsequent commission into a recreated folder (GUI1-10).
- Commissioning from the sheet with film set to bw or kodachrome and IR ticked moves the film before the driver refuses (GUI1-11).
- Rotating any thumbnail during a Roll-button roll rotates every remaining frame's delivered files. Changing the format or folder mid-roll splits the deliveries (GUI1-12).
- Reading the Delete dialog's title and answering 'No' permanently deletes the pass's library entry (GUI1-15).
- Pressing Stop while a Calibrate is queued leaves the window believing it is calibrated, so later scans fail instead of prompting (GUI1-16).
- Save As can target a file inside library/<entry>/ (for example scan.tif) and overwrite raw pixels with corrected ones. There is no guard against delivering into the library tree beyond the OS overwrite prompt.
- A remembered 'aim' tick means an ordinary click on a prescan opens a film-moving dialog; it asks, but the tick persists across launches unnoticed.

### The window (tools/gui.py, second half)

**Can do**

- Open the contact sheet after a walk (it opens automatically when the walk finishes, or with the Contact sheet ... button or key), and tick or untick frames by clicking the picture, the checkbox, Space, All or None.
- Open the position window by double-click, Return or a click on the caption. Drag the picture, step it with the arrows (finest, small, medium or large), Centre it (as walked, marked his), Reset it (the detector's reading), or press Return to tick and move to the next frame.
- Rotate or flip one frame or all frames from the sheet's right-click menu or keys; 'all' also sets the session default for later scans.
- Set per-roll scan options on the sheet (scan dpi, prescan dpi, film, meter, infrared, IR tied to resolution, nudge) and press Scan chosen frames: confirm, approved.json is written and merged, and a Roll with an approval for every ticked frame is submitted.
- In Rolls ..., sort and filter rolls, then Open (resume), Export (re-correct from the library), Duplicate, Rename, Show in file manager, or Delete roll folders.
- In the big view, zoom, pan, double-click for fit or 1:1, read the pixel under the pointer and the clipping histogram, show a superseded prescan, delete a pass (optionally with its library entry), Save As or Save all.
- With 'aim' ticked, click an edge of a prescan to move the film by one sub-frame command (after a confirm).
- Launch with --demo, --look-only (demo only), --open-roll, --library, --rolls, --out, --settings or --reference.

**Should not do**

- Open a roll from the Rolls browser while a walk or roll is running.
- Double-click a thumbnail expecting only to open the position window: it also toggles the tick.
- Walk through a resumed roll's frames with Return, or press All, without re-checking the already-scanned frames.
- Walk a new strip into a roll name used before (typed again, or after Delete or Rename) and trust the sheet that opens.
- Rescan into a duplicated roll, or reuse a roll name, and then Export either copy.
- Delete or rename the folder of the roll whose contact sheet is currently open.
- Aim-click on a prescan that no longer shows where the film is (an older frame, a reopened walk, or the prescan just aimed from).
- Choose 7200 dpi, or infrared with B&W or Kodachrome, in the sheet's options.
- Export rolls or Save all with the main window set to a different film (mono) from the roll's.
- Export or Save all large batches, or click rapidly through many 3600/7200 dpi frames, while the scanner session is open, especially during a roll.
- Combine --demo with --library or --rolls pointing at the real library or rolls.

**Unguarded mistakes**

- Open in the roll browser bypasses the busy check that the Rolls ... button makes. A roll opened mid-walk merges the walk's remaining prescans into it, and its decisions are later filed in the walk's folder.
- A double-click on a sheet thumbnail unticks (or ticks) the frame. Leaving the position window with Done or Escape leaves the frame out of the roll.
- Return in the position window and the All button re-tick already-scanned frames. The confirm dialog neither names nor counts them, and the rescan replaces frameNN.tif.
- A fresh walk into a folder name that has stored sheet state inherits the old strip's ticks, 'yours' positions and turns, which are then applied to the new strip's frames.
- Export of a duplicated or name-reusing roll delivers another roll's library entries, named and turned by this roll's settings.
- Delete or Rename in the roll browser is allowed on the roll the open sheet belongs to. Scan chosen frames then recreates the old folder and scans into it without the walk.
- Aim moves the film by a distance measured on whatever prescan is displayed, and can be applied twice from the same stale prescan.
- The sheet accepts 7200 dpi and IR on IR-blind films. The roll winds the film (and at 7200 dpi prescans and holds each frame) before the driver refuses.
- Roll Export and Save all reduce every frame to one channel, or keep all channels, according to the window's current film, not the roll's.
- Save As failures (full disk, missing entry) produce no message in the window.
- JPEG export without Pillow writes .tif files that can overwrite existing TIFF exports, and the JPEG's .dng sidecar can overwrite an existing DNG.
- --demo --rolls rolls lets a demo commission write roll.json, approved.json and frames into a real walk folder.

### Outputs: export, TIFF, DNG, preview, mono, bracket, settings

**Can do**

- Choose TIFF or JPEG for the output folder (outfmt) and a JPEG quality (clamped to 60-100 in the window).
- Save as one pass: the dialog picks the path and the suffix picks the format; a JPEG of an RGBI pass also writes <stem>.dng.
- Save all passes of the session into a folder, or Export the frames of chosen rolls; both are re-corrected from the library at full resolution.
- Turn or mirror a pass; only delivered files follow, never the library entry.
- Deliver one channel (average, R, G or B) for black and white; the library keeps all three.
- Run tools/scan.py with --out *.tif|*.jpg, --quality, --bracket 2-9 --stops, --mono/--no-mono, --mono-channel, --no-library.
- Press Ctrl-C once in tools/scan.py or tools/scan_roll.py to ask for a stop between passes, or twice to abort.
- Edit gui-settings.json by hand or move it with RPS7200_SETTINGS or --settings.
- Regenerate the three comparison files from a library entry with tools/make_comparison.py.

**Should not do**

- Press Ctrl-C a second time while a pass is being read: it raises inside the read and wedges the scanner.
- Rely on the first Ctrl-C to stop tools/scan.py during calibration, metering or a single pass: it does not (OUT-02).
- Use --no-library for anything that matters: a failed export or a bracket MemoryError then loses the only copy.
- Run a 9-pass bracket at 3600 dpi or above on a machine without several GB of free RAM (OUT-05).
- Trust the window's clipping panel to say whether the sensor railed: it measures corrected pixels (OUT-04).
- Save all or Export immediately after a roll while the writer is still filing: unfiled passes are written as 1400 px previews under full-dpi names (OUT-11).
- Run two windows against the same gui-settings.json.
- Use the output folder's prescans/ copies of an aimed walk as the framing record: they hold the before-aim picture (OUT-03).

**Unguarded mistakes**

- tools/scan.py: first Ctrl-C during calibration or metering is acknowledged as 'stopping' but the full scan still runs.
- Typing a .png (or any unknown) extension in Save as fails silently in the window: traceback on stderr only.
- Save all or Export into a folder with an earlier export: the .dng companion and the no-Pillow .tif fallback overwrite existing files despite the 'nothing is overwritten' promise.
- Two runs of tools/scan.py with the default --out overwrite ./scan.tif and ./scan.json.
- --quality 0 (or any value outside 60-100) is passed to the JPEG encoder unclamped.
- Turning on mono for an RGBI scan silently drops the infrared from the delivered file.
- Rescanning a roll frame silently replaces rolls/<roll>/frameNN.tif.
- Launching the window from a different directory silently uses a different settings file (and loses uncommissioned sheet decisions).
- Two rolls with the same folder name share their stored contact-sheet decisions.

### Operator CLI tools and build

**Can do**

- Scan one frame with tools/scan.py at any positive --dpi (corrected up to 3600 dpi; 7200 only with --no-shading), RGB or RGBI (--ir, tied by default, --no-fast-ir to untie), with or without --auto-exposure or a fixed --exposure-scale, delivering TIFF or JPEG, optionally mono.
- Take a 2-9 pass RGB bracket (--bracket N --stops S), each pass filed raw and merged into the delivered file.
- Reuse the cached shading reference with --reuse instead of calibrating (3-4 min saved), or skip correction with --no-shading.
- Skip library filing: --no-library in scan.py, --library '' in scan_roll.py.
- Walk a strip with scan_roll.py --dry-run (prescans filed raw with --library), then scan it with --approved WALK_FOLDER to hold each frame to the proposed centred position.
- Scan a roll unattended with metering each/once/none, optional --correct or --correct-dry-run registration, --max-failures, and --rewind/--start-at/--nudge to place the film.
- Resume a roll by naming it again (--roll or --out) with --start-at N; earlier frame records are carried forward.
- Stop a roll between frames with one Ctrl-C (the frame in flight finishes).
- Inspect the library: list, verify (checksums, INCOMPLETE), reconstruct (re-decode), duplicates [--delete --keep N], migrate-raw [--write], migrate-direction [--write], tag --add, reindex.
- Write the three comparison TIFFs from any entry whose correction state is 'applied'.
- Measure background-gzip interference with filing_load_test.py (--rounds, --mb, --limit).
- Run make all/test/test-all/lint/type/fix/run/run-demo/run-sheet/reconstruct/verify/clean, or `python tasks.py <target>` without make.

**Should not do**

- Run scan_roll.py (walks included) or any scan.py run near 8 minutes in the foreground of a harness that kills at 10 minutes: the killed read wedges the scanner.
- Press Ctrl-C a second time, or kill, close or hang up the terminal of a running scan or roll: it abandons the read (wedge) and loses queued frames.
- Calibrate with an empty transport (scan.py calibrates wherever the film is, without asking).
- Use --no-library (or --library '' in scan_roll) for anything that might matter: the raw bytes and calibration are gone for good.
- Use --reuse across power cycles or lamp changes, or with a reference of unknown origin.
- Point --reference at a file inside a library entry without --reuse.
- Resume a roll with different --dpi/--ir/--film/--meter/--no-shading than it started with.
- Run `duplicates --delete` without running `verify` first.
- Run make format (not part of make all) without agreement.
- Trust `make reconstruct`'s exit 0 when its '-' lines say 'could not decode' or 'could not read scan.tif'.

**Unguarded mistakes**

- Pressing Ctrl-C during scan.py's calibration or metering prints 'stopping after the pass in flight' but then takes the full scan; the natural second Ctrl-C wedges the scanner.
- With --no-library, Ctrl-C during a scan.py bracket never stops it; every remaining pass is taken.
- A full disk, a vanished NAS or an unwritable --library during scan_roll.py loses every later frame (entry and frameNN.tif), while the tool prints a success line per frame for hours.
- A failed library.save in scan.py (full disk, --library naming a file) loses every pass of the run and the delivered file, with a traceback.
- With RPS7200_DEBUG=1, a pass the tool claimed is deleted from the debug spool before the tool's own save, so a failed save loses it anyway.
- Resuming with `--start-at N` as the tool advises re-scans every frame after N that was already done, duplicating library entries and overwriting frameNN.tif, for hours.
- Resuming with different --dpi/--ir/--film silently mixes settings in one roll and overwrites the recorded settings.
- `--approved WALK` without --film meters a slide or B&W walk as colour negative; the exposure is baked into the raw data.
- Running scan.py without --auto-exposure (the README's headline example) scans at the device's low default exposure, unlike scan_roll and the window.
- A blank frame mid-strip ends a `--frames 36` roll early with exit 0 and no warning.
- `scan.py --library ''` files into the current directory (and writes index.json there) instead of skipping, as the same flag does in scan_roll.py.
- Running the tools from a directory other than the repo root creates a separate library/, rolls/ and calibration/ there; --reuse then misses the cache.
- The default --out scan.tif and its scan.json sidecar are overwritten without a prompt on every run.
- A re-walk (--dry-run) into an existing folder overwrites prescanNN.tif (the --approved references) with no backup; survey.json.bak keeps only one generation.
- A Ctrl-C during a scan_roll rewind or seek still calibrates for 3-4 minutes, and on a dry run replaces survey.json with an empty walk.
- --nudge takes millimetres, while the window and CLAUDE.md use adjustment units (about 9.5x apart).
- A mistyped resolution (e.g. --dpi 5000 --no-shading) is sent to the device unvalidated.
- filing_load_test.py has no Ctrl-C protection, and `--rounds 0` crashes after warming the lamp.
- `duplicates --delete` can delete the intact twin when the kept one's files are damaged.

### Probe and analysis tools

**Can do**

- Run any hardware probe with --dry-run to see the plan (all except transport_probe; fast_ir_probe also has --resolutions sweep dry runs).
- Run hold_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth or roll_registration_walk once RPS7200_DEBUG is set to any non-empty value. Today each opens the device, starts a session, waits for warm-up and then stops with ShadingUnavailable (PAT-01).
- Run transport_probe with no gate, no dry run and no debug filing.
- Override byte14_probe's ladder (values above 0x31 are refused, negative values are not) and gain_probe's ladder (any integer).
- Chunk exposure_probe with --only A-B, and skip rewinding with --no-rewind.
- Rewind a strip before a walk with roll_registration_walk --rewind N, and run the displacement ladder on chosen frames with --ladder.
- Run every analysis tool offline against library/ and rolls/: exposure_headroom (--entries/--dpi), linearity (--match or --probe, --corrected), dpi_analysis (--entries, --domain raw|corrected, --after/--before), registration_margin (--rolls, --self-similar), roll_registration_study (--root, --walk, --glob, --held, --ensemble).

**Should not do**

- Run any hardware probe without Stefan's agreement, without film loaded, or while the window holds the device.
- Run fast_ir_probe (about 25 min), exposure_probe (about 40 min), a 16-frame walk (about 12 min) or byte14_probe (about 8-9 min) in the foreground; the harness kills at 10 minutes and a killed read is abandoned.
- Press Ctrl-C during a probe pass: no probe defers it, and the read is abandoned (PAT-03).
- Set RPS7200_DEBUG to 0/false/off expecting a probe to refuse; it runs and files nothing (PAT-02).
- Rerun byte14_probe without reading docs/byte14-plan.md, or feed gain_probe values outside 21-39.
- Use exposure_probe --only to chunk a walk while rewinding (the default), or with the same --json path for every chunk (PAT-05).
- Reuse a roll_registration_walk --label, which overwrites the earlier corpus.
- Treat exposure_headroom, registration_margin, dpi_analysis or linearity output as validation of production constants until PAT-06 to PAT-09 are addressed.
- Point roll_registration_study or registration_margin at a roll walked with a rotation or mirror set in the window (PAT-10).

**Unguarded mistakes**

- RPS7200_DEBUG=0 (or false/off/2) passes every probe's debug gate and turns filing off, so a 40-minute run leaves no library entry.
- A single Ctrl-C in any probe abandons the in-flight read, marks the device suspect, and usually means a power cycle.
- After an abandoned read, byte14_probe's finally-pass and gain_probe's restore still send SET SCAN FRAME, MODE SELECT and WRITE GAIN OFFSET to the suspect device.
- exposure_probe --only 5-8 after a rewound --only 1-4 rescans physical frames 1-4 labelled 5-8, and --json is overwritten.
- gain_probe --ladder 21,21,300 sends gain 44 (300 & 0xFF) while the library records 300; byte14_probe --ladder with a negative value raises inside set_mode after several setup commands.
- hold_probe --restore sends one measured nudge(-net) that its own TOTAL_TRAVEL_LIMIT_MM never checks.
- roll_registration_walk --json elsewhere writes a log that roll_registration_study --walk cannot use, because the TIFFs are sought beside the log.
- roll_registration_study --root on a rotated walk runs x-axis detectors over turned images and counts prescanNN-before.tif as extra frames.
- dpi_analysis's default time window selects by flush time (`created`) and takes whatever was filed in it, prescans and demo entries included.
- linearity --corrected silently mixes raw ('deliberately raw', 'no reference') and corrected entries under the label 'corrected pixels'.

### README.md and CLAUDE.md vs the code

**Can do**

- Run `make all|test|test-all|lint|type|fix|clean|reconstruct|verify|run|run-demo|run-sheet` (all go through tasks.py; `python tasks.py <target>` also works).
- Scan once with `tools/scan.py --dpi N [--ir] [--no-fast-ir] [--auto-exposure] [--reuse] [--no-shading] [--film ...] [--bracket N --stops S] [--library DIR|--no-library]`; it prints an estimate before opening the device.
- Walk or scan a roll with `tools/scan_roll.py [--dry-run] [--roll NAME|--out DIR] [--start-at N] [--frames N] [--approved DIR] [--correct|--correct-dry-run] [--nudge MM] [--max-failures N]`.
- Inspect and maintain the library with `tools/library.py list|verify|reconstruct|reindex|duplicates [--delete --keep N]|migrate-raw [--write]|migrate-direction [--write]|tag ENTRY --add TAG`.
- Turn on debug filing with RPS7200_DEBUG=1, move it with RPS7200_DEBUG_ROOT, and move window settings with RPS7200_SETTINGS or --settings.
- In the window: calibrate (only after ticking 'The film is in the transport') or load the cached reference; prescan, scan, walk, roll; use the contact sheet; Rolls... export/duplicate/rename/delete; Save As / Save all; Stop; Force abort after typing ABORT.
- Run `tools/make_comparison.py <entry>` for the raw/corrected/inverted triple, and `tools/check_scanner.py` or `pytest -m hardware` for read-only device checks.

**Should not do**

- Calibrate with an empty transport; the only guard is a tick the operator sets.
- Press Ctrl-C a second time in tools/scan.py or tools/scan_roll.py: it abandons the read and needs a power cycle.
- Run a foreground scan sequence near 8-10 minutes on the strength of tools/scan.py's estimate, which is below the documented medians.
- Run Export or Save all, or leave a large roll's tail compressing, while the window holds the scanner open and idle.
- Delete a roll folder expecting everything but approved.json to be rebuildable.
- Use --no-fast-ir casually (a flat ~220 s per IR pass), or 7200 dpi with shading (refused, or wasted in a window roll).
- Send SET_SCAN_HEAD, STOP SCAN or an IEEE1284 reset from any script.

**Unguarded mistakes**

- A single Ctrl-C during tools/scan.py's calibration or metering is ignored: the full pass runs, is filed, and the tool exits 0, which invites the wedging second Ctrl-C.
- Starting a Roll at 7200 dpi from the window (the value is in the resolution ladder) is not refused up front. Frames are prescanned and metered and the film advanced before each scan() refusal, until max_failures.
- `tools/scan_roll.py --prescan-dpi 7200` (or any uncorrectable prescan resolution) is not pre-refused: calibration is spent and every frame fails.
- After a timeout marks the device suspect, pressing Scan or Prescan again in the window still sends WRITE, MODE SELECT and gain writes to the device before it is refused.
- Deleting a roll from Rolls... destroys prescanNN-before.tif, old CLI walks' prescans and survey.json data that exist nowhere else, while the dialog says the frames can be rebuilt.
- `tools/scan.py --library <full or read-only path>`: the first library.save raises. Remaining held passes and the delivered file are lost, and with debug on their spool was already deleted at close().
- A crash followed by a reboot during a long debug-on window session loses every spooled probe and hold pass kept in the OS temp directory.
- `make run-sheet --open-roll X` fails; the flag has to go to tools/gui.py directly.
- Running a roll with debug off keeps each frame's prescan only as a corrected 8-bit TIFF, and its raw bytes are gone.

### docs/*.md and TODO.md vs the code

**Can do**

- Run `uv run python tools/library.py verify|reconstruct|duplicates|migrate-raw|migrate-direction|tag ENTRY --add uncalibrated-on-purpose` offline against stored entries.
- Scan with `tools/scan.py` (calibrates on open unless --reuse with an existing cache or --no-shading) and file raw bytes, reference and mask.
- Walk a strip with `tools/scan_roll.py --dry-run` (prescans filed with raw bytes and roll_membership), then hold it with `--approved`, or aim with `--correct` at 300 dpi prescans.
- Untie infrared with `--no-fast-ir`, `scan(fast_infrared=False)`, or the window box.
- Re-run `tools/dpi_analysis.py --domain raw`, `tools/linearity.py`, `tools/registration_margin.py`, and `FRAME_EDGE_PARITY=1 pytest tests/test_frame_edges_parity.py` offline.

**Should not do**

- Calibrate with an empty transport (CLAUDE.md). docs/vignette-plan.md:248-261, docs/multi-exposure-plan.md:395 and tools/uniformity.py's prompts still lead there.
- Use `--reuse` across a power cycle: the cache carries no timestamp or identity, only the file mtime in shading_origin.
- Pass a single `--exposure-scale` on an RGBI pass: it also scales the IR exposure.
- Commission a sheet roll containing 'unconfirmed' or 'neighbours' positions without reviewing them: they move film on one detector member's word.
- Call advance/retreat with steps>1: the value byte is sent, not N frames, and the demo behaves differently.
- Back up only library/: the calibration bytes live in calibration/<UTC>/.

**Unguarded mistakes**

- `tools/uniformity.py capture` without `--exposure-scale` always fails at the first metering probe (ShadingUnavailable), after the operator has already emptied the transport.
- `tools/uniformity.py capture` calibrates without archiving the calibration's raw bytes, and writes calibration/shading_uniformity.npz non-atomically.
- `tools/scan.py` calibrates immediately after INQUIRY with no confirmation that film is loaded, and records no media flag at calibration time.
- `tools/scan.py --bracket N --no-shading` is accepted and merges uncorrected passes.
- The window's Delete-roll dialog says frames 'can be rebuilt'; nothing rebuilds a roll folder's manifests, prescans or hand-set positions.
- The window's reopen-roll dialog says 'It will calibrate again first'; a window that already calibrated does not.
- Ticking 'nudge registration between frames' on the sheet does nothing on a commissioned scan.
- A commissioned (approved) roll has no roll-wide travel cap, and a large wrong target buys itself a larger per-frame budget.
- An approved offset beyond about 9.0 mm (about 85 units) is moved but can never be verified within SEARCH_MM's reach.
- A 600 dpi pass 862 wide against an 860-pixel mask is delivered with its last 2 columns uncorrected, and nothing warns.

### The fixes themselves (83dbb22..03aacba)

**Can do**

- Run tools/scan.py or tools/scan_roll.py with RPS7200_DEBUG=1. Each pass the tool files itself is claimed, and metering probes and hold/aim prescans are filed by debug filing at close.
- Press Ctrl-C once in tools/scan.py or tools/scan_roll.py: the pass in flight completes. A bracket stops after the current pass, and a roll stops before the next advance.
- Continue after an abandoned pass only by power-cycling and starting a new process or window. Every scan, film move or calibration is refused with DeviceSuspect.
- Resume a failed roll with `--roll <folder name>` or `--out <dir>` plus `--start-at N`. roll.json is carried forward and re-taken frames replace their records.
- Pass `--approved <walk folder>` to scan_roll.py. The prescan resolution is pinned to the walk's, and a conflicting --prescan-dpi is refused.
- Tag deliberately uncalibrated entries with `tools/library.py tag ID --add uncalibrated-on-purpose` so verify stops reporting them.
- Run `tools/library.py duplicates --delete`. Only entries whose raw bytes (or image hash) are identical are offered.
- Quit the window mid-job and choose 'Yes: stop after the frame in flight' or 'No: let everything queued finish'.
- Regenerate the comparison files from any library entry with tools/make_comparison.py <entry>. Entries whose correction state is not 'applied' are refused.

**Should not do**

- Press Ctrl-C a second time during a read: the pass is abandoned, the device is marked suspect and needs a power cycle.
- Press Ctrl-C while scan.py or scan_roll.py is filing after the scanner closed: queued passes or frames are lost (CSA-04).
- Run long 3600 or 7200 dpi sessions in the window, or rolls, with RPS7200_DEBUG=1 on a small or tmpfs temp volume: the spool grows by the full size of every pass until close (CSA-01).
- Use Force Abort unless a power cycle is acceptable: the debug spool is left unfiled in temp (CSA-12).
- Keep working in the same window after a DeviceSuspect error: every job is refused until the window is restarted after a power cycle.
- Move or copy library/ without calibration/: the calibration archive lives outside the library and is referenced by a CWD-relative path (CSA-09).
- Interrupt `tools/library.py migrate-raw --write` or the window's close-time compaction (CSA-10, CSA-17).

**Unguarded mistakes**

- A first Ctrl-C during calibration or metering in tools/scan.py is acknowledged as 'stopping after the pass in flight', but the real scan then starts anyway. The natural second Ctrl-C wedges the scanner (CSA-03).
- A first Ctrl-C during a long seek or rewind in scan_roll.py still leads to a full 3-4 minute calibration before the roll stops (CSA-03).
- After a suspect device, pressing Scan or Prescan in the window still sends MODE SELECT, gain/offset and frame writes before refusing, with a message about a 'film move (init)' (CSA-05).
- With an output folder set, single window scans compress their delivered copy while the device is open and idle, despite the documented rule (CSA-07).
- A full or unplugged library drive while debug is on: the claimed pass's debug copy is deleted at close before the failed filing is noticed, so the pass is lost (CSA-02).
- Launching the window while gui-settings.json is briefly locked moves it aside and opens silently with default settings (CSA-16).
- Choosing 'Use the cached one' gives entries no link back to the calibration bytes the reference came from (CSA-09).

### The test suite

**Can do**

- Run `make test`, `make test-all` or `make all` (tasks.py -> pytest tests/ with coverage over rps7200 only; tools/ and gui.py are not in the coverage report).
- Run `uv run pytest tests/ -m hardware` with the scanner attached: nine tests that open the device and send only INQUIRY and READ STATE.
- Run the frame-edge parity check with FRAME_EDGE_PARITY=1 where research/frame-edge data exists (about three minutes).
- Run the suite from any working directory; DemoScanner('library') and gui-settings.json resolve against cwd.
- Run the suite with RPS7200_NO_TIFFFILE=1 to check the bare-install TIFF path.

**Should not do**

- Run the GUI tests (make test with Tk and a display) on a checkout whose repo-root gui-settings.json holds settings or uncommissioned sheet decisions that matter: the window fixture loads and rewrites it.
- Export RPS7200_DEBUG=1 in the shell that runs pytest: test_filing_is_off_by_default fails, and nothing sets RPS7200_DEBUG_ROOT globally.
- Treat a green CI as evidence that the USB control plane, the vendor-capture equivalence, the demo hold-loop convergence, the real-library bottom-up detection or (on macOS) the GUI were tested: all of them skip there.
- Treat the demo tests as proof that DirectScanner.scan() files exact entries: only the DemoScanner path is reconstructed end to end.
- Run the hardware tests without asking first: they open and claim the device.

**Unguarded mistakes**

- Answering 'No' to the GUI's 'Keep the library entry?' deletes the entry and its raw bytes (shutil.rmtree), while the roll's roll.json still marks the frame done, so a resume never offers it again. Only Cancel and the outside-library case are tested.
- A library drive that is full or unwritable while the output folder is fine: FrameWriter raises before writing any delivered copy, and with debug on the claimed spool is deleted at close. The pass is lost, with no test.
- Scanning a real roll from the window or tools/scan_roll.py without RPS7200_DEBUG=1: every frame's prescan, the replaced before-prescan, hold/aim verification passes and metering probes lose their raw bytes. The tests assert this as correct.
- Killing the window while library.compact runs: the entry is left with a stale scan.tif checksum that verify reports as damage, and no tool re-compacts it.
- Quitting the window mid-pass, or typing ABORT: the protections (askyesnocancel, the no-timeout _wait_to_quit, typed confirmation, suspect after a closed transport) are untested.
- Running `tools/library.py duplicates --delete`: the only command-line rmtree over library entries has no CLI test.
- Running `tools/library.py migrate-raw --write` before `migrate-direction` on bottom-up legacy entries: they are reported as a decode change ('left alone') and the command exits 1. The ordering is untested.

### Every writer: atomicity, crashes, full disks (gap pass)

**Can do**

- Kill the window or a CLI tool at any moment (Force Quit, closing the terminal, logoff, or a Force Abort that takes the process down); whatever was mid-write stays on disk as INCOMPLETE entries, truncated roll or delivered files, `.part` temps and orphan spool directories.
- Run `make verify` (tools/library.py verify) to list INCOMPLETE directories, directories without scan.json and checksum mismatches.
- Run `tools/library.py reindex` to rebuild index.json.
- Duplicate, Rename or Delete a roll folder from the Rolls browser while the window holds the device open.
- Set the library, rolls, reference and output folders to removable or network paths, including one the window remembers from a previous launch.
- Re-walk frames into an existing walk's folder, or type an existing roll name for a new walk.
- Start a roll, bracket or long series of single scans on a nearly full disk; nothing checks free space first.

**Should not do**

- Start a long roll without checking free space on the library, roll folder, output folder and (with RPS7200_DEBUG=1) the OS temp volume; nothing checks, and the first failure costs the frame in flight too.
- Kill the window while it says 'closing ...': the debug flush, the writer's last frames and the compaction of single scans are running then.
- Force-quit the window while Duplicate is copying (it looks hung because the copy runs on the UI thread).
- Leave hours of contact-sheet decisions in an open sheet during a walk or roll; they reach disk only when the sheet closes.
- Rely on 'can be exported again' after a delivered copy failed: the truncated file keeps its name and the retry lands at -2.
- Delete library entries or roll folders by hand to free space mid-session without running verify afterwards.

**Unguarded mistakes**

- Starting a roll the disk cannot hold: no estimate of space, no check, and approved.json failing on the full disk only logs 'scanning anyway'.
- Using a remembered output folder, or a --library or --rolls path, on a drive that is not mounted: on POSIX mount points owned by the user it is silently recreated on the system disk.
- Quitting on a full settings disk: the 'could not save the window's settings' line goes into a log that is destroyed at once, and the sheet decisions are lost.
- A walk whose prescan filing failed, reopened later, shows the previous walk's prescanNN.tif under the new record and proposes positions from it.
- A failed or interrupted Duplicate leaves `<roll>-N` listed as a roll with only part of its files.
- Relying on `make verify` after a crash: it does not see `.part` leftovers (a failed compact's `.raw.bin.gz.part` can be hundreds of MB), orphan rps7200-debug-* spools, calibration archives without calibration.json, or uncompacted plain entries.
- A roll.json.legacy truncated by a failed copy is taken as done for good, and the original numbering is later lost.

### Threads and processes sharing files (gap pass)

**Can do**

- Open a second window, make run-sheet or a --demo window while a window or tools/scan_roll.py holds the scanner. The second session fails to claim interface 0, but the window and its roll browser stay fully usable.
- Rename, Delete or Duplicate a roll folder from the roll browser as soon as the status reads idle, including while FrameWriter is still filing that roll's last frames.
- Rename, Delete or Duplicate a roll folder that tools/scan_roll.py or another window is scanning into.
- Export a roll (or Save all), then close the window while the export is still writing.
- Export a roll into the session's output folder (the dialog's default) while the same roll is being rescanned.
- Run make verify, make reconstruct, tools/library.py tag, migrate-raw, migrate-direction or duplicates --delete while a window or scan_roll is filing into the same library.
- Open a roll from the browser while this window's own walk is filing into it (GUI1-02), and read its survey before the prescans are written.
- Delete a pass (rmtree of its library entry) while Save all, Export or a full-resolution load is reading that entry.
- Press Calibrate, Scan, Prescan, Roll or 'Scan chosen frames' in a window whose session reported 'No scanner'.

**Should not do**

- Touch (rename, delete, duplicate) a roll folder until every frame of it has been filed, and never while another process is scanning into it.
- Close the window before Save all or Export reports 'saved N of N'.
- Run library maintenance or verification while any window or tool is filing, or act on its 'did not finish' and checksum reports then.
- Export into the live output folder while a roll that delivers there is running.
- Kill the window (terminal Ctrl-C, or closing the terminal) during a walk: survey.json already names prescans that are not yet written.
- Use a window that could not open the scanner for anything but browsing.

**Unguarded mistakes**

- _roll_is_busy checks only `self.busy`, which clears when a job returns while FrameWriter is still writing frameNN.tif and roll.json into the folder. It sees nothing of other processes, so Rename recreates a split roll folder, Delete removes survey.json and approved.json of a live roll, and Duplicate copies a truncated last frame.
- Quitting during Save all or Export: the 'closed' event handler calls _quit() without waiting for the writing threads, so the daemon export thread is killed and leaves a truncated file under its final name (CONC-01).
- A scanner-less window enqueues every job into a queue no thread reads, marks itself calibrated, shows a roll countdown that never ends, and writes approved.json (CONC-07).
- make verify reports an entry that another process is still writing as 'was being written and did not finish', and a mid-compaction entry as checksum-mismatched or missing raw.bin.
- tools/library.py tag racing the window's close-time compact on the same entry loses one side's scan.json update.
- duplicates --delete on Windows raises on the first file another process holds, leaving that entry half-deleted and the index unrebuilt.
- One truncated prescanNN.tif (kill or disk-full mid-write) makes the whole walk unopenable in the window and crashes scan_roll --approved.
- Export and FrameWriter can pick the same free output name (TOCTOU widened by library.save), and one silently overwrites the other.

### The frame-edge detectors themselves (gap pass)

**Can do**

- Choose the film type in the window or with scan_roll --film. negative and bw get the four-member reader; positive and kodachrome get none (legacy 36 mm aiming, or SKIPPED on the sheet).
- Walk a strip at 300 dpi and have EdgeWatch propose centred positions while the prescans arrive, or reopen a stored walk and have it re-proposed from rolls/<roll>/prescanNN.tif.
- Extend a walk. The old frames stay and are re-read against the new ones once the walk finishes.
- Run scan_roll --correct or --correct-dry-run to aim each frame in-walk through WalkReader, or --approved <walk> to hold frames to propose_centred's positions for an earlier walk.
- Prescan at 600 or 900 dpi. Every frame is refused by width; the window warns (FR-13) and scan_roll refuses --correct.

**Should not do**

- Scan slides or positives with the film left at its default 'negative'. The detector then reports frames as measured and centred (FE-02) instead of refusing.
- Combine --no-shading with --correct, or propose a sheet from a --no-shading walk. The detector was only ever fitted on shading-corrected prescans (FE-06).
- Treat the green 'frame edges N/N' light as proof that all four members ran. A member that raises is dropped silently (FE-01).
- Change framing.PRESCAN_COLUMNS, or relax propose._downscaled, without re-deriving the members' 428-column constants (FE-09) and fixing centring's k>1 scaling (FE-03).
- Merge two walks with overlapping frame numbers into one roll folder and then use scan_roll --approved on it. Duplicates validate themselves (FE-08).

**Unguarded mistakes**

- scan_roll.py defaults --film to negative and the window defaults v_film to negative. A slide walk run without changing it gets 'measured, 0' proposals, and nothing warns.
- Reopening a walk whose manifest has no recorded film uses whatever film the window is currently set to.
- --no-shading together with --correct or --dry-run is accepted without comment.
- A member failure, for example after a dependency upgrade, produces no message anywhere: not the log, not the light, not the manifest, not CI.
- A blank or all-base frame that passes the contrast test is given an interpolated 'from neighbours' position rather than 'not placed' (FE-04).

### Can each kind of pass be re-derived? (gap pass)

**Can do**

- Run `uv run python tools/library.py reconstruct` to re-decode every stored pass (turned upright, with the 7200 dpi stagger replayed) with current code, and `tools/library.py verify` to check each file's checksum.
- Recompute any entry's corrected picture with today's correction code through `library.corrected(entry)`, from its raw scan.tif, shading.npz and ccd_mask.bin.
- Set RPS7200_DEBUG=1 before a window session, `tools/scan.py` or `tools/scan_roll.py`, so that metering probes and hold/aim verification prescans are filed with their raw bytes and command logs.
- Re-reduce a calibration by hand: `calculate_shading(open('calibration/<UTC>/data.bin','rb').read(), pixels_per_line)` with pixels_per_line from calibration.json (no tool does it).
- Read what the scanner was sent for any scan() pass in scan.json `extra.commands`: MODE SELECT, frame, gain read-back and write, SLIDE INIT, mask, and PARAM replies.

**Should not do**

- Do not treat the prescan.tif inside a roll-frame entry as raw data; it is the corrected 8-bit picture with no bytes behind it.
- Do not delete, move or rename the calibration/ folder or run the tools from a different working directory: `extra.shading_origin.archive` is a relative string, and verify never checks it.
- Do not delete a walked roll folder: survey.json, approved.json (including reference_entry) and prescanNN-before.tif are the only record of walk and aim decisions.
- Do not use 'Use the cached one' or `--reuse` when the reference's origin matters: such entries carry no archive link and no record of the reducing code.
- Do not scan rolls or brackets for metering evidence: their entries carry no metering record.

**Unguarded mistakes**

- Metering each frame of a roll, or metering a bracket, files every frame with `exposure_metered: false` and `metering: null`, and nothing warns that the probe evidence was dropped.
- Scanning a roll with tools/scan_roll.py instead of the window silently skips the reversal check for frames whose read direction is unknown (RDM-02).
- A 'check this frame' warning (a scan that disagrees with its own prescan) is only logged. Closing the window loses it, and no entry records it (RDM-01).
- Scanning at 7200 dpi requires `--no-shading`, which also skips calibration. The entry is filed with no shading reference at all and a CCD mask read at the 5172-byte default, and verify reports nothing because the skip is explicit.
- Stopping a bracket with Ctrl-C between passes files the passes taken so far. Nothing marks the bracket incomplete, and no bracket id groups them (the missing id is reported elsewhere).
- Nudging the film in the window before a Scan leaves no trace in the scan entry. Only READ STATE byte 2, inside `carriage_state.read_state`, shows which whole frame the film was on.

### Windows and macOS on the data paths (gap pass)

**Can do**

- Type a roll name of any length, and any capitalisation, into the roll box; it becomes the folder name and the prefix of every delivered file.
- Rename a roll folder from the roll browser (a case-only rename is refused on Windows and macOS as 'already there').
- Keep the window open for days with RPS7200_DEBUG=1, spooling every unclaimed pass to the OS temp directory until quit.
- Commission a multi-hour roll from the contact sheet and leave the machine unattended.
- Run tools/check_scanner.py, pytest -m hardware or a second window while the first window holds the scanner.
- Put the checkout, library, rolls and output folder anywhere, including deep OneDrive for Business paths, exFAT drives and network shares.
- Copy library/ and rolls/ between Windows, macOS (APFS or HFS+) and Linux and open them there.
- Open --open-roll with a spelling that differs in case from the folder.
- Open frameNN.tif or prescanNN.tif in a viewer (or the Explorer preview pane) while re-scanning or re-walking into the same roll.

**Should not do**

- Let the host sleep, enter Modern Standby or restart for updates while a pass or roll runs: nothing prevents it, and it abandons the read in flight.
- Follow the Windows 'replace the driver with Zadig' advice when another window or a leftover python.exe may hold the scanner.
- Use a long roll name under a deep Windows root without LongPathsEnabled.
- Rely on the OS temp dir to keep a debug spool across a long-running window session.
- Match a delivered single-scan file to its library entry by the time in its name (local vs UTC).

**Unguarded mistakes**

- A 30-frame roll left running on a laptop with default power settings: the host sleeps mid-read, the frame is lost, and the scanner may need a power cycle (PLAT-01).
- A roll name long enough to push the entry or roll folder past MAX_PATH: the failure comes after the seek has moved the film and after the scan. The first frame is lost (or filed invisibly as INCOMPLETE) and the roll stops (PLAT-03).
- A roll name of 233 or more characters: every delivered and exported copy fails on every OS while the roll carries on (PLAT-03).
- A window open over a weekend with debug on under macOS: Friday's probes and hold prescans are purged by the OS, the spool is not recreated, and the flush says they are 'kept' (PLAT-02).
- Running check_scanner.py while the window holds the scanner on Windows yields 'replace the driver with Zadig'; doing so mid-roll reinstalls the driver under a live session (PLAT-04).
- Typing 'portra' for an existing 'Portra' adds to that roll on Windows and macOS but makes a new roll on Linux. On macOS it also defeats the roll-in-use guard for a roll opened with that spelling (PLAT-06).
- Previewing a roll's prescan in Explorer during a re-walk leaves the old strip's picture under the new walk's record (PLAT-11).
- Moving a Windows-written library to a Mac leaves every reference_entry and calibration archive link unresolvable (backslash separators) (PLAT-09).
- Filing from a checkout without git on PATH (GitHub Desktop) or on an exFAT drive: every entry records driver_commit null, with no warning (PLAT-10).

### Measurement code no area read (gap pass)

**Can do**

- Import dark_mask, relative_noise, noise_split, ceiling, agreement_z, colour_deviation and fixed_pattern from .claude/skills/measure-scan-quality/scripts and call them on any (H, W, >=3) array that is 16-bit or float.
- Run `uv run python tools/film_edge_study.py [--root R] [--json out.json]` offline, with no scanner; it reads library/ and rolls/ under R.
- Run `uv run python tools/collect_vignette_study.py --out DIR [--root library] [--tag T] [--extra N] [--no-checksum] [--dry-run]` to copy a tagged study plus N spread entries to another machine.

**Should not do**

- Run column metrics (colour_deviation, fixed_pattern) on a delivered file that was turned or mirrored, or compare two deliveries with different per-frame orientations, without first un-orienting to sensor coordinates.
- Pass library.load pixels (raw) or a library.corrected result whose record['corrected'] is not 'applied' (7200 dpi entries, 'no reference') to metrics documented as working on corrected samples, and then quote the number as corrected.
- Call noise_split or agreement_z on a pair that has not been registered and gain-matched, or agreement_z on corrected bracket passes where one pass is near the rail.
- Treat film_edge_study's probe statistics, meter_delta or R:B verdicts as what production metering sees.
- Use --no-checksum and then treat the transfer as verified.
- Reuse an --out directory from an earlier run with another tag: old entry folders stay, and only the manifest is replaced.

**Unguarded mistakes**

- A mistyped --tag makes collect_vignette_study copy only spread entries and exit 0.
- One truncated or corrupt raw.bin.gz anywhere in the library, even an unrelated entry, crashes collect_vignette_study with EOFError or zlib.error.
- collect_vignette_study refuses a complete plain (uncompacted) entry as 'missing raw.bin.gz', and copies entries without raw.layout that later make `analyse` exit.
- A corrupted shading.npz, ccd_mask.bin or scan.tif, in the source or in the copy, is transferred without detection although the record carries their checksums.
- A float array normalised to 0-1 passes metrics._check, and agreement_z then applies 16-bit gates to it.
- A 90-degree delivered frame reads 'clean' in colour_deviation while its lines are visible.
- One unreadable or truncated TIFF (an INCOMPLETE entry's prescan.tif, or a half-written roll prescan) aborts film_edge_study.
- film_edge_study includes rotated window-walk prescans and demo entries in its corpus without warning.
- film_edge_study --json overwrites the named file without asking.

