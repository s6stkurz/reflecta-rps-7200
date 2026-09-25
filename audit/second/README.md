# Second audit (after the fixes), 2026-09-25

**Code audited:** `03aacba` on `claude/clever-mayer-dy1j3o`, after the fixes for the first audit
(audit at `83dbb22`, written up one level up in [`audit/`](../README.md)). Read-only, judged from
the code, same prompts as the first audit.

## This audit is incomplete -- read this first

The run stopped part-way when the account reached its **monthly spend limit**. What finished:

| Stage | Planned | Finished |
|---|---|---|
| Status check of every first-audit problem P01-P32 | 4 readers | **all 4 -- complete** |
| Area readers (find) | 15 | 2: transport/protocol, decode/debug filing |
| Dataflow tracer | 1 | 1 |
| Adversarial verification of each area's findings | 16 | **0** |
| Completeness critic and gap readers | up to 13 | 0 |

So the **status table below is complete and is the main result.** The area findings are one
reader's each and **unverified** -- in the first audit roughly one finding in ten was re-described
by the second reader, so treat them as leads. Areas not re-read at all: library, session/roll,
framing, the window (both halves), outputs, CLI tools, probe tools, docs, tests, and the
review of the fixes themselves. Their `raw/find-*.json` records say `failed`. The run can be
resumed from where it stopped (the finished readers replay from cache) once the limit resets.

## Where the first audit's problems stand

6 fixed, 14 mostly-fixed, 12 partly-fixed, 0 open, 0 regressed -- of 32. Full evidence and every remaining
sub-issue per problem: **[status.md](status.md)**.

| # | Problem (first audit) | Status now | First thing still open |
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

### New problems the fixes themselves introduced

The status readers were asked for these too. 21 of the 32 name one; the ones that matter most:

- **Debug filing (P03):** `debug_claim` deletes a spooled pass as soon as a caller *says* it
  will file it. If that caller's own `library.save` then fails (full disk), the debug copy is
  already gone.
- **Filing (P04):** now that the library entry is written first, a `library.save` that raises
  is not caught, so the delivered copies of that picture are not written either.

The rest are in [status.md](status.md) under each problem.

## High-severity findings from the readers that finished (unverified)

Transport/protocol: {'high': 1, 'medium': 10, 'low': 19, 'info': 1} · decode/debug filing: {'high': 5, 'medium': 6, 'low': 8, 'info': 2} ·
dataflow: {'high': 2, 'medium': 12, 'low': 14, 'info': 1}.

| Area | Finding | Title |
|---|---|---|
| USB transport, protocol and command sequence | [TP-01](areas/transport-protocol.md#find-transport-protocol-tp-01) | Calibration treats any read refusal as 'scanner finished': partial reference adopted, cached and used; device not marked suspect |
| Decode, direction, shading and debug filing | [DBG-1](areas/decode-and-debug-filing.md#find-decode-and-debug-filing-dbg-1) | debug_claim deletes the spooled copy before the claimant's own filing is confirmed |
| Decode, direction, shading and debug filing | [DBG-2](areas/decode-and-debug-filing.md#find-decode-and-debug-filing-dbg-2) | Claimed passes are still spooled and held in the temp dir until close(), so a debug-on roll keeps every frame twice |
| Decode, direction, shading and debug filing | [DBG-3](areas/decode-and-debug-filing.md#find-decode-and-debug-filing-dbg-3) | With debug off (the default), prescans, before-prescans, metering probes and hold/aim passes never reach the library with raw bytes |
| Decode, direction, shading and debug filing | [DBG-4](areas/decode-and-debug-filing.md#find-decode-and-debug-filing-dbg-4) | A pass that was read completely but fails to decode, realign or correct is never filed; its raw bytes and command log are discarded |
| Decode, direction, shading and debug filing | [DBG-5](areas/decode-and-debug-filing.md#find-decode-and-debug-filing-dbg-5) | ASC 0x20 during an image read is taken as end of data: pass silently truncated, device not marked suspect |
| Dataflow tracer | [F01](areas/dataflow.md#dataflow-f01) | A failed library.save loses the whole picture: delivered copies, raw capture and later bracket passes |
| Dataflow tracer | [F02](areas/dataflow.md#dataflow-f02) | tools/scan.py ignores the first Ctrl-C outside a bracket; the second abandons the read |

## Files

- [status.md](status.md) -- every first-audit problem: what the code does now, what is left.
- [areas/transport-protocol.md](areas/transport-protocol.md), [areas/decode-and-debug-filing.md](areas/decode-and-debug-filing.md) -- findings in full, with persisted state and operator actions.
- [areas/dataflow.md](areas/dataflow.md) -- the flows traced end to end at 03aacba, module dependencies, and the dataflow reader's findings.
- `raw/*.json` -- every agent result as returned.

## What was fixed between the two audits

Branch `claude/clever-mayer-dy1j3o`, `83dbb22..03aacba`. The commit messages name the problem
numbers. Nothing was tried on the scanner; every change is tested offline, and CI is green on
Ubuntu, macOS and Windows. Windows CI had been hanging in the test step (a `mkdir` retry loop
in the calibration archive, fixed in `e5929e1`) and now has a 40-minute job timeout.
