# Second audit, 2026-09-27

**Code audited:** `03aacba` on `claude/clever-mayer-dy1j3o` -- the driver after two rounds of
fixes for the [first audit](../README.md) (`83dbb22`). Read-only, judged from the code and not
from the docs, with the first audit's readers and prompts: one reader per subsystem, every
finding then re-read against the code by a second, adversarial reader, a completeness critic,
and gap readers for what the areas did not cover. New for this one: a status check of every
first-audit problem, and a reader for the fixes themselves (`83dbb22..03aacba`).

Nothing here was run on the scanner.

## In one paragraph

The first audit's worst problems are closed: nothing silently files corrected pixels as raw
any more, a failed pass no longer leads the software to keep driving the device, the library
is written atomically and checksummed, calibrations are kept byte for byte, the demo files
what it is, and none of the 32 problems is still open or made worse. What is left is mostly
*coverage*: several kinds of pass still reach the library only as corrected pixels or not at
all -- above all a real roll frame's prescans -- the debug spool that is meant to catch them
has holes of its own, a failed library save still costs the picture, and a handful of Ctrl-C
and busy-guard paths remain. No finding is critical.

## Where the first audit's problems stand

6 fixed, 14 mostly-fixed, 12 partly-fixed -- of 32; none open, none regressed. Every sub-issue still open, per
problem, with the code it was checked against: **[status.md](status.md)**.

| # | Problem (first audit) | Status | First thing still open |
|---|---|---|---|
| [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) | A single Scan from the window files corrected pixels as the raw scan.tif | **fixed** | Nothing on any path reachable with DirectScanner or DemoScanner. |
| [P02](../problems/P02-debug-filing-stale-raw-bytes.md) | Debug filing pairs a pass with the previous pass's raw bytes (or none) | **mostly-fixed** | (1) last_raw is cleared only once a read has completed (direct.py:1882-1903), not at the start of a pass (fix step 1). |
| [P03](../problems/P03-debug-spool-not-crash-safe.md) | The debug spool is deleted on a failed save and lost on a crash | **partly-fixed** | (1) The spool is still `tempfile.mkdtemp(prefix="rps7200-debug-")` (direct.py:926-927), which is system temp (often tmpfs) and on a different filesystem from library/. |
| [P04](../problems/P04-delivered-copy-before-library-entry.md) | A failed delivered copy (output folder, rolls/) costs the library entry | **mostly-fixed** | A failed delivered copy is surfaced only as a log line. |
| [P05](../problems/P05-calibration-not-stored-exactly.md) | Calibration bytes are thrown away; only a derived reference is kept | **mostly-fixed** | (1) Nothing reads the archive back. |
| [P06](../problems/P06-7200dpi-realignment-not-recorded.md) | At 7200 dpi scan.tif is not the plain decode, and nothing records it | **fixed** | Nothing harmful. |
| [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) | A short or failed pass loses its raw bytes | **partly-fixed** | The failed-pass half (TP-A1, DDF-09) is untouched, and no `tags=["failed"]` path exists anywhere. |
| [P08](../problems/P08-passes-never-filed.md) | Whole classes of passes are never filed, and RPS7200_DEBUG is overridden | **partly-fixed** | (1) A real roll frame's prescan is still the corrected 8-bit prescan: session.py:2627 `prescan=rf.prescan` and tools/scan_roll.py:752 `prescan=frame.prescan`, where rf.prescan is prescan()'s corrected return (direct.py:2076-2083). |
| [P09](../problems/P09-record-missing-parameters.md) | The entry record drops parameters needed to re-derive or evaluate a pass | **mostly-fixed** | (1) Roll frames and bracket passes that were metered are still recorded as `exposure_metered: false`, with no `metering` block (DDF-08, LIB-13, CLI-12, OUT-13). |
| [P10](../problems/P10-non-atomic-writes.md) | Entries and manifests are written in place; partial files look complete | **mostly-fixed** | (1) verify does not report a missing prescan.tif (or any other `files` entry that is not referenced from `calibration`). |
| [P11](../problems/P11-duplicates-delete-destroys-scans.md) | `library.py duplicates --delete` destroys scans of different photographs | **mostly-fixed** | (1) `--delete` still calls rmtree with no typed confirmation and no `library/.trash/`. |
| [P12](../problems/P12-library-maintenance-tools.md) | reconstruct / verify / migrate-raw report or repair the wrong thing | **mostly-fixed** | (1) migrate-raw does not check that a *labelled* entry's stored pixels equal `apply_shading(decode_raw)` before replacing them. |
| [P13](../problems/P13-gzip-with-device-open.md) | The window gzips library entries with the scanner open and idle | **partly-fixed** | (1) The tail of every roll/walk and frames after a Stop are still gzipped with the device open and idle, which is exactly the case the rule targets. |
| [P14](../problems/P14-failure-paths-keep-driving-device.md) | After a failed or interrupted read, the software keeps talking to the device | **partly-fixed** | (1) A suspect device still receives configuration writes (exposure, frame, gain/offset, MODE SELECT) on every later scan attempt. |
| [P15](../problems/P15-lazy-calibration-inside-scan.md) | `--no-shading` does not skip calibration; the stalling lazy path is live | **mostly-fixed** | (1) The window still sets `calibrated=True` optimistically on submit. |
| [P16](../problems/P16-timeouts-and-runtime-budget.md) | Read timeouts and the 10-minute budget are not enforced where they matter | **mostly-fixed** | (1) scan_roll.py prints no runtime estimate and no >8-minute warning, and neither tool refuses a foreground run without an opt-in flag. |
| [P17](../problems/P17-calibration-without-asking.md) | Calibration starts without asking what is in the transport | **partly-fixed** | (1) tools/scan.py and tools/scan_roll.py calibrate with no film confirmation and no --film-loaded option. |
| [P18](../problems/P18-roll-folder-identity.md) | One roll is scattered across folders; unnamed rolls share one folder | **mostly-fixed** | (1) DP-05 is unchanged. |
| [P19](../problems/P19-roll-manifests.md) | roll.json / survey.json / approved.json can lie about what was done | **fixed** | Nothing in the listed sub-issues. |
| [P20](../problems/P20-rotation-during-walk.md) | Turning or flipping during a walk corrupts the stored walk | **fixed** | Nothing. |
| [P21](../problems/P21-roll-to-library-join.md) | Roll export can deliver the wrong entry (a 300 dpi prescan as a frame) | **partly-fixed** | Fix-plan item 2 (export follows roll.json's entry id) is not done, and item 1 only half: membership records the folder but the join ignores it. |
| [P22](../problems/P22-contact-sheet-state.md) | Contact-sheet decisions leak between strips or are lost | **partly-fixed** | (1) Decisions on an uncommissioned walk are saved to gui-settings.json on quit or when another roll is opened, but no reopen path reads them back: after a restart, Rolls... |
| [P23](../problems/P23-busy-guards-and-stop.md) | Busy guards sit on buttons, not on actions; submit() cancels Stop | **partly-fixed** | (1) Rolls browser Open during a job (GUI1-22/GUI2-14) is still unguarded. |
| [P24](../problems/P24-disk-full-and-quitting.md) | A full disk does not stop a roll; quitting kills writes mid-file | **partly-fixed** | (1) tools/scan_roll.py does not stop on a failed filing and says nothing until the end. |
| [P25](../problems/P25-reopened-frames.md) | Reopened frames collide, and Delete removes real library entries | **mostly-fixed** | (a) Fix-plan item 2 is only half done: an entry inside session.root is still removed permanently -- `shutil.rmtree(entry); library.reindex(entry.parent)` (tools/gui.py:4492-4495) -- with no .trash/, one 'No' away. |
| [P26](../problems/P26-demo-files-corrected-as-raw.md) | Every demo entry is corrected pixels labelled raw | **fixed** | nothing material. |
| [P27](../problems/P27-demo-refusals-and-roll-loop.md) | The demo accepts what the scanner refuses and runs its own roll loop | **mostly-fixed** | (a) Fix-plan item 1 was not done. |
| [P28](../problems/P28-look-only-drives-real-scanner.md) | --look-only without --demo drives the real scanner | **mostly-fixed** | Under `--look-only --demo`, calibration is not refused. |
| [P29](../problems/P29-bracket-merge.md) | Bracket merging judges the wrong domain and mixes RGBI with RGB | **mostly-fixed** | (a) Fix-plan item 4 (spool bracket passes to disk) was not done. |
| [P30](../problems/P30-comparison-files-and-metrics.md) | The comparison files and several metrics do not measure what ships | **partly-fixed** | (a) noise_split still does not register or gain-match the pair (metrics.py:104-111), and the shares the skill quotes are stale by its own docstring (metrics.py:100-103). |
| [P31](../problems/P31-framing-geometry-and-detectors.md) | Framing: two geometry models, a search window too small, one voter can move film | **partly-fixed** | (a) One member can still move film. |
| [P32](../problems/P32-usbpcap-returns-keystrokes.md) | The pcap reader hands back keystroke payloads, and the test asserts it does | **fixed** | Minor. |

## What this audit found

361 findings: 0 critical, 22 high, 113 medium, 205 low, 21 info. Verdicts: 284 confirmed, 41 partly (re-described), 36 added by the second reader, 1 refuted and dropped, 0 unverified.

### High severity

| Finding | Area | Severity | Verdict | Title |
|---|---|---|---|---|
| [TP-01](areas/transport-protocol.md#transport-protocol-tp-01) | transport-protocol | high | confirmed | Calibration treats any read refusal as 'scanner finished': partial reference adopted, cached and used; device not marked suspect |
| [DBG-1](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-1) | decode-and-debug-filing | high | confirmed | debug_claim deletes the spooled copy before the claimant's own filing is confirmed |
| [DBG-2](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-2) | decode-and-debug-filing | high | confirmed | Claimed passes are still spooled and held in the temp dir until close(), so a debug-on roll keeps every frame twice |
| [DBG-3](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-3) | decode-and-debug-filing | high | confirmed | With debug off (the default), prescans, before-prescans, metering probes and hold/aim passes never reach the library with raw bytes |
| [LIB-01](areas/library.md#library-lib-01) | library | high | confirmed | Real-roll frame entries file the prescan CORRECTED, without raw bytes or mask, and discard the raw prescan and prescan_before |
| [LIB-02](areas/library.md#library-lib-02) | library | high | confirmed | A library.save failure loses the whole frame: in-memory raw bytes dropped and delivered copies never attempted |
| [LIB-03](areas/library.md#library-lib-03) | library | high | confirmed | `reconstruct` counts a decode that now raises as 'nothing to decode from' and exits 0 |
| [SR-01](areas/session-roll.md#session-roll-sr-01) | session-roll | high | confirmed | Debug-filing claim is made at hand-off, and the spool is flushed before the writer finishes: a pass whose filing fails is deleted from the debug spool too |
| [SR-02](areas/session-roll.md#session-roll-sr-02) | session-roll | high | confirmed | The last frames of every roll are gzipped and deflated with the device open and idle; nothing waits for the writer at the end of a roll |
| [SR-04](areas/session-roll.md#session-roll-sr-04) | session-roll | high | confirmed | Outside debug mode, a real roll keeps no raw data for its prescans: raw_prescan and prescan_before are dropped, the stored prescan.tif is corrected and unlabelled, and a failed frame's prescan is not filed |
| [SR-19](areas/session-roll.md#session-roll-sr-19) | session-roll | high | confirmed | Ctrl-C in the terminal that launched the window, or closing that terminal, kills the daemon scanner and writer threads mid-read |
| [FR-01](areas/framing-units.md#framing-units-fr-01) | framing-units | high | confirmed | 'reverse the direction' checkbox (remembered across launches) silently mirrors every approved hold target in a commissioned roll |
| [FR-02](areas/framing-units.md#framing-units-fr-02) | framing-units | high | confirmed | The prescans that framing decisions are made from are not kept raw; the pre-move prescan is discarded and the frame entry's prescan.tif is corrected but unlabelled |
| [GUI1-01](areas/gui-part1.md#gui-part1-gui1-01) | gui-part1 | high | confirmed | Export joins entries to a roll by roll name only: a duplicated roll and its original export each other's (newest) frames |
| [GUI1-02](areas/gui-part1.md#gui-part1-gui1-02) | gui-part1 | high | confirmed | Roll browser 'Open' is not guarded while the scanner works; opening a roll mid-walk mixes two strips into one survey and sheet |
| [GUI2-01](areas/gui-part2.md#gui-part2-gui2-01) | gui-part2 | high | confirmed | Export joins library entries to rolls by roll name only: a Duplicate, a reused name or a renamed-and-reused name exports another roll's frames |
| [CLI-01](areas/cli-operator-tools.md#cli-operator-tools-cli-01) | cli-operator-tools | high | confirmed | A roll whose library filing fails keeps scanning for hours and says so only at the end; FrameWriter then writes no delivered copy either |
| [CLI-02](areas/cli-operator-tools.md#cli-operator-tools-cli-02) | cli-operator-tools | high | confirmed | scan.py: Ctrl-C is ignored through calibration and metering, and for the whole of a --no-library bracket |
| [CLI-05](areas/cli-operator-tools.md#cli-operator-tools-cli-05) | cli-operator-tools | high | confirmed | `library.py reconstruct` calls a decoder that now throws, an unreadable scan.tif and corrupt raw bytes "not a regression", and exits 0 |
| [DOC-01](areas/docs-readme-claude.md#docs-readme-claude-doc-01) | docs-readme-claude | high | confirmed | Roll frames' prescan.tif is stored corrected, without raw bytes, mask, commands or a label |
| [CSA-01](areas/changes-since-first-audit.md#changes-since-first-audit-csa-01) | changes-since-first-audit | high | confirmed | Debug spool keeps every pass (claimed ones included) in the OS temp dir until close(): GUI lifetime / whole roll, tens of GB |
| [T-04](areas/tests.md#tests-t-04) | tests | high | confirmed | Tests enforce that a real roll's prescans (and every pass only debug filing keeps) are stored without raw bytes, corrected, with their pass record dropped |

They come down to a few themes, each reported independently by several areas:

1. **A real roll frame's prescans are not kept raw** (LIB-01, SR-04, FR-02, DOC-01, CSA-14,
   CLI-09, T-04). The frame entry stores the corrected 8-bit prescan as `prescan.tif` with no
   bytes, mask or label, and the raw prescan and the pre-move prescan are dropped -- yet these
   are what every framing decision was made from.
2. **The debug spool has holes** (DBG-1, DBG-2, SR-01, CSA-01, CSA-02, CLI-07, DBG-8, TP-10).
   A caller's claim deletes the spooled copy before the caller has filed it; claimed passes
   stay in the OS temp directory until the device closes; a force-abort or crash leaves the
   spool unfiled and unannounced.
3. **A failed library save loses the picture** (LIB-02, SR-05, CLI-01, CLI-06, T-03): no
   delivered copy is attempted, the in-memory raw data is dropped, and a roll keeps scanning
   for hours before saying so.
4. **Passes that fail after their bytes were read are never filed** (DBG-4, TP-A1, TP-06,
   DOC-17), and a truncated read counts as complete (TP-02, DBG-5).
5. **Calibration can end early and be believed** (TP-01, DBG-7): any refused read is taken as
   "finished", and the partial reference is adopted and cached.
6. **Ctrl-C and heavy work with the device open** (CLI-02, SR-19, SR-02, CSA-03, CSA-04,
   OUT-01, GUI2-06, TP-11, PAT-03): the first Ctrl-C is not honoured between phases, the
   window's threads die with its terminal, and the last frames of a roll -- and every delivered
   copy -- are compressed with the device open and idle.
7. **`reconstruct` passes what it cannot check** (LIB-03, CLI-05): a decoder that now raises
   is counted as "nothing to decode from" and the run exits 0.
8. **Export joins a roll to its entries by name** (GUI1-01, GUI2-01), and the roll browser's
   Open is not guarded while the scanner works (GUI1-02, GUI2-02).
9. **"Reverse the direction"**, remembered across launches, mirrors every approved hold
   target of a commissioned roll (FR-01).

### By area

| Area | Findings | critical | high | medium | low | info |
|---|---|---|---|---|---|---|
| [USB transport, protocol and command sequence](areas/transport-protocol.md) | 35 | 0 | 1 | 10 | 23 | 1 |
| [Decode, direction, shading and debug filing](areas/decode-and-debug-filing.md) | 24 | 0 | 3 | 7 | 12 | 2 |
| [Demo scanner vs the real scanner](areas/demo-parity.md) | 21 | 0 | 0 | 3 | 14 | 4 |
| [The library store](areas/library.md) | 24 | 0 | 3 | 7 | 12 | 2 |
| [ScanSession, FrameWriter and rolls](areas/session-roll.md) | 22 | 0 | 4 | 5 | 12 | 1 |
| [Framing and transport units](areas/framing-units.md) | 20 | 0 | 2 | 4 | 13 | 1 |
| [The window (tools/gui.py, first half)](areas/gui-part1.md) | 24 | 0 | 2 | 7 | 14 | 1 |
| [The window (tools/gui.py, second half)](areas/gui-part2.md) | 32 | 0 | 1 | 11 | 18 | 2 |
| [Outputs: export, TIFF, DNG, preview, mono, bracket, settings](areas/outputs.md) | 20 | 0 | 0 | 6 | 13 | 1 |
| [Operator CLI tools and build](areas/cli-operator-tools.md) | 32 | 0 | 3 | 13 | 14 | 2 |
| [Probe and analysis tools](areas/probe-and-analysis-tools.md) | 23 | 0 | 0 | 7 | 15 | 1 |
| [README.md and CLAUDE.md vs the code](areas/docs-readme-claude.md) | 21 | 0 | 1 | 8 | 12 | 0 |
| [docs/*.md and TODO.md vs the code](areas/docs-plans-todo.md) | 25 | 0 | 0 | 6 | 18 | 1 |
| [The fixes themselves (83dbb22..03aacba)](areas/changes-since-first-audit.md) | 20 | 0 | 1 | 9 | 8 | 2 |
| [The test suite](areas/tests.md) | 18 | 0 | 1 | 10 | 7 | 0 |

### By category

| Category | Findings |
|---|---|
| data-integrity | 79 |
| doc-mismatch | 62 |
| user-error | 45 |
| bug | 41 |
| design | 29 |
| hardware-safety | 28 |
| error-handling | 27 |
| demo-divergence | 23 |
| test-gap | 11 |
| library-completeness | 9 |
| concurrency | 4 |
| dead-code | 3 |

### Refuted

1 finding(s) the second reader could not confirm in the code, dropped from
the areas and listed here so nothing disappears silently:

| Area | Finding | Title | Why the second reader refuted it |
|---|---|---|---|
| decode-and-debug-filing | DBG-21 | The infrared plane is never shading-corrected, although CLAUDE.md says everything an operator sees is corrected | The claimed doc-mismatch does not exist. CLAUDE.md:125-127 states directly, in the same section: 'The infrared plane is delivered uncorrected everywhere: the calibration pass is RGB, so there is no infrared reference to divide by.' The code (apply_shading skipping channels absent from reference.ref, shading.py:256-259) matches the doc. |

## Files

- [status.md](status.md) -- every first-audit problem: what the code does now, what is left.
- [areas/](areas/) -- every finding in full, per area: evidence quoted from the code, failure
  scenario, fix, and the second reader's check; plus what the area persists and what an
  operator can and should not do.
- [dataflow.md](dataflow.md) -- how data moves through the code at `03aacba`, verified.
- [persisted-state.md](persisted-state.md) -- every file on disk: format, raw or corrected,
  writer, reader, exact or not.
- [library-exactness.md](library-exactness.md) -- whether each kind of pass can be re-derived
  from what the library keeps.
- [demo-vs-direct.md](demo-vs-direct.md), [user-errors.md](user-errors.md),
  [doc-mismatches.md](doc-mismatches.md).
- [PROGRESS.md](PROGRESS.md) -- how this run was done and restarted; [run/](run/) the scripts,
  [raw/](raw/) every agent's result as returned.

## After this audit

Fix round 3 is running on the findings above; see [PROGRESS.md](PROGRESS.md).
