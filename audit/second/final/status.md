# Status of the first audit's problems after all three fix rounds

[Back to the summary](../README.md)

Each problem file under `audit/problems/` re-checked against the code at `ea58e98` -- after fix round 3 and its follow-up -- by a reader told to judge from the code, not from the commit messages, and to check everything [../status.md](../status.md) listed as still open at `03aacba`.

| # | Problem | At 03aacba | At ea58e98 | What is left |
|---|---|---|---|---|
| [P01](../../problems/P01-gui-scan-files-corrected-as-raw.md) | A single Scan from the window files corrected pixels as the raw scan.tif | fixed | **fixed** | Nothing on any path DirectScanner or DemoScanner can reach. |
| [P02](../../problems/P02-debug-filing-stale-raw-bytes.md) | Debug filing pairs a pass with the previous pass's raw bytes (or none) | mostly-fixed | **mostly-fixed** | (2) library.save still never decodes `raw`/`raw_path` to check them against `image` (rps7200/library.py:318-350 writes the bytes without any check); fix step 3 was not done. |
| [P03](../../problems/P03-debug-spool-not-crash-safe.md) | The debug spool is deleted on a failed save and lost on a crash | partly-fixed | **fixed** | Nothing harmful. |
| [P04](../../problems/P04-delivered-copy-before-library-entry.md) | A failed delivered copy (output folder, rolls/) costs the library entry | mostly-fixed | **fixed** | Nothing harmful. |
| [P05](../../problems/P05-calibration-not-stored-exactly.md) | Calibration bytes are thrown away; only a derived reference is kept | mostly-fixed | **mostly-fixed** | (3, partly) The archive still lives outside the library, in `calibration/<UTC>` (library.py:64 DEFAULT_CALIBRATIONS = Path("calibration")) or beside a --reference. |
| [P06](../../problems/P06-7200dpi-realignment-not-recorded.md) | At 7200 dpi scan.tif is not the plain decode, and nothing records it | fixed | **fixed** | Nothing harmful. |
| [P07](../../problems/P07-failed-and-short-passes-lose-bytes.md) | A short or failed pass loses its raw bytes | partly-fixed | **fixed** | Nothing in the problem as written. |
| [P08](../../problems/P08-passes-never-filed.md) | Whole classes of passes are never filed, and RPS7200_DEBUG is overridden | partly-fixed | **mostly-fixed** | (2, partly) With RPS7200_DEBUG off, the default, metering probes and a hold's intermediate verification prescans (all but the last, which becomes the frame's prescan) are still not filed. |
| [P09](../../problems/P09-record-missing-parameters.md) | The entry record drops parameters needed to re-derive or evaluate a pass | mostly-fixed | **mostly-fixed** | (a) The merged bracket and its merge stats are never filed in the library. |
| [P10](../../problems/P10-non-atomic-writes.md) | Entries and manifests are written in place; partial files look complete | mostly-fixed | **mostly-fixed** | (a) No lock or coordination exists between the tools/library.py mutators (migrate-raw --write, compact --write, duplicates --delete, tag) and a running window or tool that is filing or compacting into the same library... |
| [P11](../../problems/P11-duplicates-delete-destroys-scans.md) | `library.py duplicates --delete` destroys scans of different photographs | mostly-fixed | **mostly-fixed** | `--delete` still calls `shutil.rmtree` with no typed confirmation and no `library/.trash/` (tools/library.py:260-261). |
| [P12](../../problems/P12-library-maintenance-tools.md) | reconstruct / verify / migrate-raw report or repair the wrong thing | mostly-fixed | **fixed** | No defect is left. |
| [P13](../../problems/P13-gzip-with-device-open.md) | The window gzips library entries with the scanner open and idle | partly-fixed | **mostly-fixed** | (a) Idle is judged only when a job starts. |
| [P14](../../problems/P14-failure-paths-keep-driving-device.md) | After a failed or interrupted read, the software keeps talking to the device | partly-fixed | **mostly-fixed** | (a) FrameWriter and the ScanSession worker are still `daemon=True` (session.py:2139, 2597). |
| [P15](../../problems/P15-lazy-calibration-inside-scan.md) | `--no-shading` does not skip calibration; the stalling lazy path is live | mostly-fixed | **fixed** | No defect is left. |
| [P16](../../problems/P16-timeouts-and-runtime-budget.md) | Read timeouts and the 10-minute budget are not enforced where they matter | mostly-fixed | **mostly-fixed** | Neither tool refuses a foreground run past about 8 minutes without an opt-in such as `--background-ok`. |
| [P17](../../problems/P17-calibration-without-asking.md) | Calibration starts without asking what is in the transport | partly-fixed | **mostly-fixed** | The debug probes still calibrate with no film question and no --film-loaded: tools/probing.py:98-117 ensure_reference, called from byte14_probe.py:162, exposure_probe.py:342, fast_ir_probe.py:216/469, gain_probe.py:15... |
| [P18](../../problems/P18-roll-folder-identity.md) | One roll is scattered across folders; unnamed rolls share one folder | mostly-fixed | **mostly-fixed** | (a) A fresh walk into an existing folder whose name was typed (GUI, or scan_roll --roll/--out) leaves the old strip's approved.json in place. |
| [P19](../../problems/P19-roll-manifests.md) | roll.json / survey.json / approved.json can lie about what was done | fixed | **fixed** | nothing |
| [P20](../../problems/P20-rotation-during-walk.md) | Turning or flipping during a walk corrupts the stored walk | fixed | **fixed** | nothing. |
| [P21](../../problems/P21-roll-to-library-join.md) | Roll export can deliver the wrong entry (a 300 dpi prescan as a frame) | partly-fixed | **fixed** | Minor edge: the name fallback applies per frame, not only to legacy rolls. |
| [P22](../../problems/P22-contact-sheet-state.md) | Contact-sheet decisions leak between strips or are lost | partly-fixed | **mostly-fixed** | (a) open_roll uses the stored sheet only when no approved.json exists (gui.py:3482-3483). |
| [P23](../../problems/P23-busy-guards-and-stop.md) | Busy guards sit on buttons, not on actions; submit() cancels Stop | partly-fixed | **mostly-fixed** | The guard lives in the window, not the session: submit() still accepts anything, which is fix-plan item 1 and a design decision for the owner. |
| [P24](../../problems/P24-disk-full-and-quitting.md) | A full disk does not stop a roll; quitting kills writes mid-file | partly-fixed | **mostly-fixed** | (a) Fix-plan item 4 is not done: the session log is still only the Tk text widget (_say, gui.py:4651-4655), and nothing is persisted under library/logs. |
| [P25](../../problems/P25-reopened-frames.md) | Reopened frames collide, and Delete removes real library entries | mostly-fixed | **mostly-fixed** | (a) Fix-plan item 2 is still half done. |
| [P26](../../problems/P26-demo-files-corrected-as-raw.md) | Every demo entry is corrected pixels labelled raw | fixed | **fixed** | Nothing material. |
| [P27](../../problems/P27-demo-refusals-and-roll-loop.md) | The demo accepts what the scanner refuses and runs its own roll loop | mostly-fixed | **mostly-fixed** | (a) Fix-plan item 1 is not done. |
| [P28](../../problems/P28-look-only-drives-real-scanner.md) | --look-only without --demo drives the real scanner | mostly-fixed | **fixed** | Nothing. |
| [P29](../../problems/P29-bracket-merge.md) | Bracket merging judges the wrong domain and mixes RGBI with RGB | mostly-fixed | **mostly-fixed** | (a) Fix-plan item 4 (spool bracket passes to disk) is still not done. |
| [P30](../../problems/P30-comparison-files-and-metrics.md) | The comparison files and several metrics do not measure what ships | partly-fixed | **mostly-fixed** | (a) noise_split still does not register the pair; the docstring leaves that to the caller. |
| [P31](../../problems/P31-framing-geometry-and-detectors.md) | Framing: two geometry models, a search window too small, one voter can move film | partly-fixed | **partly-fixed** | (a') One member can still move film through `tools/scan_roll.py --approved`. |
| [P32](../../problems/P32-usbpcap-returns-keystrokes.md) | The pcap reader hands back keystroke payloads, and the test asserts it does | fixed | **fixed** | Nothing. |

## P01 -- A single Scan from the window files corrected pixels as the raw scan.tif

**Status:** fixed (was fixed at 03aacba) · **Commits:** 338970a, 38405eb

**What the code does now:**

Every reachable path now files raw pixels. rps7200/session.py:2989 `raw_image = getattr(self._scanner, "last_pixels_raw", None)` and :2994-2997 `self._file(..., raw_image=raw_image, ...)` in _scan; _prescan does the same at :2947/:2966. The invariant is structural in FrameWriter._write, rps7200/session.py:2219-2234: `if raw_image is None and (job["meta"] or {}).get("shading"): corrections = ["shading"]` (with a note), then `pixels = raw_image`, then library.save(..., corrections=corrections) at :2245. DirectScanner clears last_pixels_raw at the start of every pass (direct.py:4042) and sets it on every pass (direct.py:4344), so it cannot go stale. The repair `tools/library.py migrate-raw` is still there (tools/library.py:71-92 `_one_shading_explains`, :269). Tests: tests/test_session.py:283 and :294.

**What is left:**

Nothing on any path DirectScanner or DemoScanner can reach. Two latent leftovers are unchanged from the second audit. (a) library.save (rps7200/library.py:266-285, 402-411) still takes corrected pixels labelled `corrections_applied: []` from any caller that does not say otherwise; the guard exists only in FrameWriter. (b) tools/scan.py:386 `image=image if raw is None else raw` would file corrected pixels unlabelled if a scanner had no last_pixels_raw. Related, not the P01 defect itself: ScanSession._prescan still has a hand-built fallback meta (session.py:2944-2946 `or {"resolution_dpi": ..., "film": ..., "channel_order": ...}`) for a stand-in with no last_scan_meta, which CLAUDE.md forbids. The roll path refuses to file in that case (_unpublished, session.py:3891).

## P02 -- Debug filing pairs a pass with the previous pass's raw bytes (or none)

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 7fb75d4, 69039d7, 3e96647, 2aded37, 9b9a15f

**What the code does now:**

(1) Fixed. scan() now clears last_raw, last_raw_layout, last_pixels_raw and last_scan_meta at the start of every pass, after metering and before any command: rps7200/direct.py:4035-4044 ("Nothing a pass before this one left may describe it"). read_planes also clears them for a pass that keeps no bytes (direct.py:2699-2708). The hold/aim scenario from status.md is closed: scan_roll takes each prescan's own record as the pass lands (direct.py:5210, 5277, 5312 `prescan_capture = self.capture_record()`) and the RollFrame carries it (direct.py:474-484 `_prescans_kept`). The session files a roll prescan with `capture = rf.prescan_capture` (session.py:3526), and tools/scan_roll.py:1105-1111 does the same. A later same-shaped pass is caught by identity (session.py:909-923 `bytes_are_another_pass`, used at :3991 and tools/scan_roll.py:1107). Debug mode forces keep_raw (direct.py:4392-4394). (3) Fixed. The debug spool guard now uses raw_bytes_disagree, which judges height too (direct.py:1391-1402), and raw_bytes_disagree judges the received lines less the stagger (direct.py:438-471).

**What is left:**

(2) library.save still never decodes `raw`/`raw_path` to check them against `image` (rps7200/library.py:318-350 writes the bytes without any check); fix step 3 was not done. Every caller guards before calling, but the library itself does not. Minor residual: a pass that raises after read_planes has set `last_raw = blob` (direct.py:2699-2701, before decode_index at :2709) leaves its own bytes in last_raw. _after_a_failed_pass (direct.py:1475) does not clear them, so a capture_record() read after a failed pass hands them out. No current caller does that: prescans use their own snapshot, and the scan_roll fallback runs only when the first prescan never completed. So this is latent, not reachable.

## P03 -- The debug spool is deleted on a failed save and lost on a crash

**Status:** fixed (was partly-fixed at 03aacba) · **Commits:** 4685464, f758694, f4ef116, cab6b13, 75d7956, 5f4b43e, d5b0a5e

**What the code does now:**

Unlink only after a successful save: direct.py:1810-1829 (`if filed: stuck += self._debug_unlink(item)`); a failure keeps the spool and forgets it for later passes (direct.py:1838-1846). (1) The spool is under the library: direct.py:1535-1545, `parent = self._debug_root() / self.DEBUG_SPOOL_DIR` with DEBUG_SPOOL_DIR = ".spool" (direct.py:799). It is pointed at the caller's library by session.py:2373-2388 debug_filing_into (called at session.py:2696, tools/scan.py:317, tools/scan_roll.py:820). (2) `tools/library.py file-spool` exists (tools/library.py:168-186) and drives direct.py:595 file_spool. (3) Each sidecar names its own files, the reference included (direct.py:1455-1467, `"shading": name("reference_path")`). (4) The entry id and `created` are the capture time: direct.py:1693-1697 `created = datetime.fromtimestamp(captured)` passed to library.save(created=...) (library.py:284, 318). (5) After force_abort, the worker now calls self._scanner.close() and debug_settle regardless of `dead` (session.py:2791-2812). Test: tests/test_session.py:2008. The new problem from the second audit (a claim deleting the copy before the claimant filed) is fixed: debug_claim returns a receipt, and the spooled copy goes only when the caller answers with an entry (direct.py:1593-1650). close() files only unclaimed passes; claimed ones wait for settle (direct.py:1700-1725). tools/scan.py:504-520 answers each receipt and then settles. Tests: tests/test_roll.py:818, 902, 918, 936, 986.

**What is left:**

Nothing harmful. Small: when the library cannot be written, the spool falls back to system temp (direct.py:1546-1551). `file-spool` with no argument looks only in `<library>/.spool` (tools/library.py:171-172), so that spool is found only by the path the log names. A claimed pass left in a spool is skipped by `file-spool` unless `--claimed` is given, by design (direct.py:606-607).

## P04 -- A failed delivered copy (output folder, rolls/) costs the library entry

**Status:** fixed (was mostly-fixed at 03aacba) · **Commits:** 9c636a9, a0f6e66, 86807e8, 3d3067b, ffdfaf4

**What the code does now:**

The entry is filed first, and library.save is now inside a try: session.py:2203-2254 (`try: entry = library.save(...) except Exception as exc: refused = exc`). Each copy is in its own try (session.py:2276-2298). The one-channel conversion is part of the copies, so a refusal there does not cost the entry (:2268-2274). If the library refused, the copies are still written and the raw data is kept elsewhere with keep_unfiled before NotFiled is raised (session.py:2303-2322). This closes the second audit's new problem, where a library failure lost the frame entirely. A failed copy with a filed entry becomes 'picture N: could not write ...; the library entry is safe and can be exported again' in errors and notes (session.py:2326-2330). The window now surfaces it: tools/gui.py:262-264 NOT_FILED and COPY_NOT_WRITTEN; gui.py:4410-4447 `_filing_trouble` marks the filmstrip result, sets the progress line and opens a non-modal notice. The CLI prints the notes as they happen (tools/scan_roll.py:750-755). Tests: tests/test_session.py:2293, 2347, 2360, 2376, 2406.

**What is left:**

Nothing harmful. The window recognises both failures by regex over log text (tools/gui.py:258-264). A reworded message would silently stop the marking; a test ties them together, and a structured event would be sturdier. tools/scan_roll.py:1294-1296 prints each writer error, including 'copy not written, entry safe', as 'could not file ...' and counts it in `failed`. That misstates what happened, though the message it wraps says the entry is safe.

## P05 -- Calibration bytes are thrown away; only a derived reference is kept

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 2dc8a14, 468733a, c2e6ac7, 4666493, 51e1cb8, f227873, 1e24ce1

**What the code does now:**

The bytes are archived on every calibration (direct.py:1244 `calibrate_shading(keep_data=True)`, :1252-1262 archive_calibration). The archive is written like an entry: INCOMPLETE marker, atomic record, sha256, commands (direct.py:1091-1182). (1) The archive is now read back: library.py:1768-1792 read_calibration and rebuild_reference with today's calculate_shading; :1804-1836 recalibrate; :1839-1882 calibration_of; `tools/library.py calibrations` (tools/library.py:500-520). (2) A reused reference names its calibration: save_shading writes a sidecar with archive, archive_sha256 and reference_sha256 (direct.py:1033-1066), and load_shading adopts it only when the checksum matches (direct.py:1019-1026, 1072-1089). Content matching is the fallback (library.py:1860-1882). Provenance on every pass: direct.py:4306-4309 `shading_origin`, with action calibrated/loaded and times (direct.py:1012-1017, 3251-3254). (3) verify now checks every named archive for presence and checksum (library.py:1567-1580, 1620-1633). (4) tools/uniformity.py:790 now goes through ensure_shading; there is no direct calibrate_shading call left in tools/. (5) A failed calibration's lines are archived tagged failed (direct.py:1245-1247, 1183-1195). (6) Any Exception from the archive is caught, and the reference is still adopted and cached (direct.py:1254-1262). The cache write is atomic (direct.py:1040-1053).

**What is left:**

(3, partly) The archive still lives outside the library, in `calibration/<UTC>` (library.py:64 DEFAULT_CALIBRATIONS = Path("calibration")) or beside a --reference. An entry holds only a path string, and copying the library alone does not carry the calibration bytes. verify now reports them missing. Whether archives belong inside library/ is an owner decision. (1, partly) library.corrected() (library.py:860-900) always corrects with the entry's stored shading.npz and offers no way to use a re-reduced reference. rebuild_reference is a building block a caller must combine with apply_shading by hand; no view or export applies it, so fix step 3 ('corrected() can rebuild ... when asked') is only half there.

## P06 -- At 7200 dpi scan.tif is not the plain decode, and nothing records it

**Status:** fixed (was fixed at 03aacba) · **Commits:** 8b01894, 3f5cce0

**What the code does now:**

The realignment is recorded: direct.py:4174-4185 `stagger_realigned = self.NATIVE_COLUMN_STAGGER_LINES`, meta at :4293, and the flight meta for a failed pass at :4180-4181. It is in the record's scan block (library.py:229-231 SCAN_FIELDS). decode_raw and reconstruct replay it: library.py:964-991 stagger_lines/_replay, :1019, :1186; legacy entries are recognised by a shortfall of exactly 4 rows (:977-982). The shape guard subtracts it (direct.py:462-464). A shaded 7200 dpi pass is refused before metering or any command (direct.py:3966-3978, ahead of `_stop_before_pass(..., metered=auto_exposure)` at :4026). The demo refuses the same way (demo.py:694-695), as do tools/scan.py:203 and tools/scan_roll.py:519.

**What is left:**

Nothing harmful. The window's ladder still offers 7200 (tools/gui.py:134 DPI_LADDER), but it is refused before anything is sent. Whether to drop the rung is the owner's call.

## P07 -- A short or failed pass loses its raw bytes

**Status:** fixed (was partly-fixed at 03aacba) · **Commits:** 2aded37, 3e96647, cf52b76, 88c6d62

**What the code does now:**

Short reads: raw_bytes_disagree judges the lines received, not the lines declared (direct.py:458-464). The pass records short_read and lines_declared (tests/test_failed_pass.py:272). Failed passes: scan() and calibrate_shading are wrapped by `_keeps_what_a_failed_pass_left` (direct.py:692-713, applied at :2959 and :3861). The pass in flight holds its lines as they arrive (direct.py:2629-2634), and its joined bytes and layout before the decode (:2690-2697). Its decoded pixels are kept after decode (:2710-2711) and after realignment (:4179-4181). On any raise, `_after_a_failed_pass` (direct.py:1469-1491) keeps what arrived, including a read given up part way (cut_short). `_keep_failed_pass` (:1493-1558) spools it with failed=True, filed tagged `failed` with stage and error, even with debug off (direct.py:1349, 1365). This covers every case the second audit listed: a decode refusal (stage 'in the decode', the lines stored as rows), the post-read width refusal and the realignment ValueError (stage 'after the decode'), and TimeoutError/ScanReadError mid-read (stage 'during the read'). Tests: tests/test_failed_pass.py:125, 148, 172, 209, 230.

**What is left:**

Nothing in the problem as written. By design, a failed pass is kept only where its bytes were kept at all: keep_raw=True or debug (direct.py:2632, 2693, 4392-4394). A caller running with keep_raw=False and debug off keeps nothing of a failed pass (tests/test_failed_pass.py:230). Every library-filing path passes keep_raw=True.

**New problem a fix introduced:**

A failed pass whose bytes did not decode (stage 'in the decode') is filed with a record that says so. Every run of `tools/library.py reconstruct` then reports it as a FAILED decode: library.reconstruct returns `Verdict(f"could not decode: {exc}", FAILED)` (rps7200/library.py:1134-1140). The CLI counts that as 'a decode regression until shown otherwise' and exits 1 (tools/library.py:222-223, 235-237, 244). Nothing in reconstruct or the CLI reads `extra.failed` or the `failed` tag. So each such entry keeps `make reconstruct` red for good. This is the permanent false alarm in the regression gate that CLAUDE.md warns about.

## P08 -- Whole classes of passes are never filed, and RPS7200_DEBUG is overridden

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** abc3bb3, 1ee856f, a95b93d, 9b9a15f, 8d83f26, e28038f, f4ef116, cab6b13, 1dbc0ff

**What the code does now:**

RPS7200_DEBUG is honoured: session.py:2683 `debug=None`, tools/scan.py:312, tools/scan_roll.py:812. (1) Every prescan a roll takes is filed raw in its own entry, with its own bytes, mask and reference, on a walk and on a real roll alike, and for a frame that failed. GUI: session.py:3490-3570, `raw_image=rf.raw_prescan, capture=rf.prescan_capture`. CLI: tools/scan_roll.py:955-1011 file_prescan and :1072-1113. The frame entry's prescan.tif is labelled (library.py:455-470 prescan.corrections_applied). (2) The pre-correction prescan is now its own entry (session.py:3583-3622; tools/scan_roll.py:1076-1085), with debug on or off. (3) Debug-filed probes and verification passes are tagged by role: direct.py:749 `_ROLE_TAGS = {"metering probe": "probe", "verification prescan": "hold"}`, applied at :1369-1374. pass_role carries roll and roll_index (direct.py:3477-3478, 4543-4545). (4) The CLI walk writes prescanNN.tif and -before on the writer thread and files errored frames' prescans (tools/scan_roll.py:1004-1011, 1072-1113). The second audit's new problem, the debug_claim loss, is fixed by receipts (direct.py:1593-1650; session.py:4031-4039 and `answering`; tools/scan.py:379-381, 504-520).

**What is left:**

(2, partly) With RPS7200_DEBUG off, the default, metering probes and a hold's intermediate verification prescans (all but the last, which becomes the frame's prescan) are still not filed. The fix plan asked for this 'in debug mode at least', and CLAUDE.md makes debug off by default deliberate, so filing them always is an owner decision. (3, partly) Probes and hold passes are linked to their frame by roll and roll_index in `pass_role`, not by the frame entry's id as fix step 4 asked. A join on roll and number works; a direct id link does not exist.

## P09 -- The entry record drops parameters needed to re-derive or evaluate a pass

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** eaeb58f, 1e23bce, bd818a5, 1ee856f, 02fe823, 5478591, 222b49e, 8c3b4cd, bca672f, c2e6ac7, 82cfd46

**What the code does now:**

Items status.md left open at 03aacba, checked in the code at ea58e98:
(1) Metered roll and bracket passes are now recorded as metered. scan() takes `metering=` and writes `"exposure_metered": bool(auto_exposure) or metering is not None` (rps7200/direct.py:4281), then attaches the block (4320-4327). The roll hands over `decided_by` (direct.py:5374-5387). scan_bracket attaches `meta["metering"]` to every rung (direct.py:3800-3839). By design, bracket rungs keep exposure_metered false because their exposure is the commanded one.
(2) Every bracket pass now carries `bracket_id` (tools/scan.py:297, 383), and `meta["bracket"]` records ratios, stops, the merge stats as `dataclasses.asdict(stats)` and the member entries (tools/scan.py:546-556). This is written only to the delivered sidecar (tools/scan.py:579). The merged picture is never filed in the library.
(3) Every nudge records its SLIDE bytes in units through `move_record` (direct.py:4815-4843). They reach the next pass as `moves_before` (direct.py:4315) and a hold's `moves_sent` (direct.py:4476-4479, 4533). `_moves_left_behind` drops, with a log line, any moves that no pass saw before a whole-frame move (direct.py:4861-4874).
(4) Verification and roll prescans now pass the roll's film (direct.py:4546-4548, 5200-5206).
(5) Debug spooling keeps the pass's meta by reference, so fields added later reach the entry (direct.py:1356-1363). The `pass_role` of metering probes and verification prescans becomes tags (direct.py:749, 1371-1374, 3477, 4543).
(6) Provenance adds `driver_source_sha256_at_import` (rps7200/library.py:122-127). `_source_digest` hashes only `rps7200/*.py` (library.py:154-158).
(7) The INQUIRY reply is kept whole as `raw_hex` (rps7200/protocol.py:526-531) and stored through `_describe_inquiry` (library.py:252-263).
(8) approved.json now carries `reading` and `read_by` for machine positions (tools/gui.py:3848-3900). The note holds the voted `edges`, the width, the reason and any abstentions (tools/frame_edges/propose.py:172-183). Per-member answers exist only in `EdgeResult.debug["members"]` (tools/frame_edges/vote.py:149-159).
Both new problems status.md recorded are fixed. The command log is stopped on every exit by the `_keeps_what_a_failed_pass_left` wrapper (direct.py:692-713, 1477-1481). `extra` is serialised through `_plain`, which writes ndarrays with tolist (library.py:169-187).

**What is left:**

(a) The merged bracket and its merge stats are never filed in the library. They live only in the delivered .json sidecar (tools/scan.py:546-579). Re-merging offline from the members is possible, because they carry bracket_id and ratio. Whether the library should hold a derived picture at all is a decision for the owner, given CLAUDE.md's raw-only rule.
(b) approved.json keeps the voted sides and the abstentions, not each detector member's own left/right answer (propose.py:172; vote.py:149-159). FE-06 is therefore only partly met.
(c) The code-identity hash covers only rps7200/*.py (library.py:158). It does not cover tools/, which holds frame_edges, the GUI and the CLIs. `read_by` in approved.json is commit+dirty only. No diff is kept.
(d) Ad-hoc probe passes (byte14_probe, gain_probe, fast_ir_probe) set no `pass_role` and no tool or step name. Only metering probes and verification prescans are tagged (direct.py:749). byte14 and gain can still be read from `mode` and the device settings.
(e) Sub-frame moves that no pass saw before a whole-frame move are only logged, not recorded (direct.py:4861-4874). A hold record still carries `target_mm`/`spent_mm` beside the unit-based `moves_sent` (direct.py:4472-4473, 4535), against CLAUDE.md's no-millimetres rule.

**New problem a fix introduced:**

None found.

## P10 -- Entries and manifests are written in place; partial files look complete

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 110502e, 5710ea0, ffdfaf4, 728ba70, 35494fe, 393604a, 3cd517e, d9ff07f, 4778233, 9383e58

**What the code does now:**

Each item status.md left open was re-checked.
(1) `damage()` now reports any file that the record names or checksums and that is missing, prescan.tif included (rps7200/library.py:1661-1676).
(2) Every data file is fsynced before the record: `_sync` on raw, scan.tif, prescan.tif, shading.npz and ccd_mask.bin (library.py:365-380). The directory is fsynced with `_sync_dir` (library.py:491-493, 774-790).
(3) Leftover `.part` files are reported: `for part in sorted(path.glob(".*.part"))` (library.py:1691-1692).
(4) reindex writes through `_write_atomic`, whose temp name is unique per writer (library.py:715-731, 1535-1538). A failed reindex can no longer fail a filed entry (library.py:503-508).
(5) uniformity's `rejected` tag goes through `library.add_tags`, which is atomic (tools/uniformity.py:698; library.py:793-799).
(6) Delivered files are written beside and renamed through `export._whole` for JPEG, DNG and TIFF (rps7200/export.py:196, 238-251). Roll manifests use a temp file, fsync and replace, and the .bak is also renamed into place (rps7200/session.py:1043-1085).
(8) migrate-direction refreshes image.sha256 once the pixels are proven against the bytes (library.py:1266-1276, 1282-1285).
The new problem status.md recorded, the migrate-raw swap order, is fixed. The KEPT copy is made by copyfile and replace and only when it is absent (tools/library.py:405-414). A stopped run is detected and finished (tools/library.py:313-346). KEPT is checksummed in `files` (tools/library.py:423-424), and `_stopped_rewrite` recognises the half-done state (library.py:1706-1729).

**What is left:**

(a) No lock or coordination exists between the tools/library.py mutators (migrate-raw --write, compact --write, duplicates --delete, tag) and a running window or tool that is filing or compacting into the same library (CC-08). A grep finds no lock in rps7200/library.py or tools/library.py.
(b) tools/scan.py writes its delivered .json sidecar with a plain in-place `write_text` (tools/scan.py:579).
(c) Some probe tools still write TIFFs straight to their final names, for example tools/roll_registration_walk.py:112 and tools/verify_protocol.py:62. Neither is library or delivered output, so the impact is low.

**New problem a fix introduced:**

None found. migrate-raw writes its record with `default=str` rather than `_plain` (tools/library.py:425-426). This is harmless, because the record was read from JSON.

## P11 -- `library.py duplicates --delete` destroys scans of different photographs

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 5710ea0

**What the code does now:**

Deletion requires byte identity. `prunable` offers an entry only if `same_data(record, k)` holds for a survivor k and `damage()` finds nothing wrong with that survivor on disk (rps7200/library.py:1449-1456). `same_data` compares raw sha256 where both entries have raw bytes, otherwise image sha256, and never matches an entry that has neither (library.py:1464-1478). Different photographs, byte-14 and gain rungs, and walk prescans therefore hold different bytes and are never offered.
The survivor is chosen on `usefulness`: raw bytes, calibration, prescan, film notes, tags and `extra` count before age (library.py:1428-1440). A richer twin, such as a roll entry with its roll_membership, outranks a bare debug copy, which closes item (4) of status.md.
The tool prints only entries that were proven byte-identical, labelled 'same scan of the same picture as <id>' (tools/library.py:252-261), so that label is now true. The missing gain, byte14 and started_utc in `signature()` (library.py:1349-1400) and the lack of a debug/evidence exclusion no longer decide anything.

**What is left:**

`--delete` still calls `shutil.rmtree` with no typed confirmation and no `library/.trash/` (tools/library.py:260-261). TODO.md:579-583 lists a soft delete ('library.trash(entry)') under Decisions for Stefan, so this is a decision for the owner, not a defect. signature() still omits gain, byte14 and capture time. That is now cosmetic, since grouping alone never deletes.

**New problem a fix introduced:**

None found.

## P12 -- reconstruct / verify / migrate-raw report or repair the wrong thing

**Status:** fixed (was mostly-fixed at 03aacba) · **Commits:** 3e90919, ddadafc, 35494fe, 393604a, 9383e58

**What the code does now:**

(1) migrate-raw requires `_one_shading_explains`, meaning that apply_shading(plain decode, the entry's own reference, mask) equals the stored pixels exactly. This now applies to labelled entries too. Anything else is 'left alone' as a decode change (tools/library.py:71-90, 348-363). `decode_raw` replays the 7200 dpi realignment (rps7200/library.py `decode_raw` → `_replay`).
(2) A corrupt shading.npz becomes a DAMAGED verdict instead of aborting the run. `ShadingReference.load` is wrapped against `(OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile)` (library.py:1159-1166). Unreadable scan.json, missing or corrupt raw bytes, an unreadable scan.tif and decode exceptions each become a per-entry verdict (library.py:1093-1143, 1170-1173). The tool counts IDENTICAL, NOTHING, BEHIND, FAILED, DAMAGED and CHANGED separately and exits non-zero only on changed, failed or damaged (tools/library.py:198-245). The same `_one_shading_explains` load catches BadZipFile (tools/library.py:84-86).
(3) verify stays quiet for SHADING_SKIPPED_EXPLICIT, demo entries and entries tagged `uncalibrated-on-purpose` (library.py:1582-1604). A `tag` action now applies that tag (tools/library.py:161-173).
(4) KEPT is copied only where absent, checksummed in `files`, and a re-run refuses to overwrite a KEPT that holds another picture (tools/library.py:364-374, 405-424).

**What is left:**

No defect is left. The `uncalibrated-on-purpose` tag is still set by hand (`tools/library.py tag ... --add uncalibrated-on-purpose`), and no probe applies it. Passes taken with shading=False are already exempt through SHADING_SKIPPED_EXPLICIT, so this is a working-practice choice.

**New problem a fix introduced:**

None found.

## P13 -- The window gzips library entries with the scanner open and idle

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 290c8f0, e903387, 9316baf, 1a80179, 9c6cbec, 728ba70, d697d2a

**What the code does now:**

Single Scan and Prescan entries are filed plain (`compress=bool(roll) and not plain`, rps7200/session.py:4046-4055) and compacted only after close (session.py:2816-2822).
A roll's last frame is filed plain: `last = ends(rf.index + 1) or self._stop.is_set()` passes `plain=last` (session.py:3474, 3566, 3606, 3666). The writer asks `idle` as each job starts. The session sets `idle=lambda: not self._rolling` (session.py:2739), and `_rolling` goes False in the roll's finally (session.py:3776). A job taken after an unforeseen end is therefore written plain too (session.py:2211-2214).
Delivered copies from a single pass use the same `compress` flag (session.py:2282-2296; export.write `compress=` at rps7200/export.py:205-251).
The new problems status.md recorded are fixed. There is a `tools/library.py compact` action (tools/library.py:466-476). compact checks each TIFF's checksum and the raw digest before rewriting (library.py `compact`, 'does not match its checksum; left as it is'). collect_vignette_study accepts `raw.bin` too (tools/collect_vignette_study.py:50-53, 127).

**What is left:**

(a) Idle is judged only when a job starts. A roll frame that the writer began gzipping while the roll was still running keeps gzipping if the roll then ends unexpectedly (failure, blank frame, end of strip), with the device open and idle.
(b) Heavy local work still runs between jobs with the session open: fsync and sha256 of every file, plus a full-library `reindex` on every `library.save` (library.py:481-508). JPEG encoding (optimize=True) and the DNG companion of single-pass delivered copies also run then (export.py:165-173, 196, 238-240).
(c) The window's own Save As, Save all and Export run `library.corrected()` on full-resolution entries and write deflate TIFFs (compress defaults to True) with the session open (tools/gui.py:5162-5175) (GUI2-18).
(d) tools/filing_load_test.py still measures only the overlap with a busy device, not the open-and-idle case (tools/filing_load_test.py:1-33). TODO.md lists what it measures as a decision for Stefan.

**New problem a fix introduced:**

None found.

## P14 -- After a failed or interrupted read, the software keeps talking to the device

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** b2c26b2, 1500c2e, ba09010, 6ff0a59, e24ccdf, 834d15f, 0eb9692, 3582c1e, 8ccc706

**What the code does now:**

(1) A suspect device is refused before anything configures a pass. There are checks at the top of scan() (rps7200/direct.py:4000), of metering (direct.py:3433), in start_scan (2401), in set_gain_offset (2514), in slide (2273) and in calibrate_shading (3010). A read that stops early marks the device suspect (direct.py:4404), and so does a calibration that stops early (direct.py:3193, 3215). The calibration's `finish_scan` sends only READ STATE.
(2) The session refuses every job on a suspect scanner in `_dispatch` (rps7200/session.py:2884-2896). The window reports a closed or aborted session instead of queueing (tools/gui.py:2420-2437, 4396-4399), which closes the dead-worker case (SR-19).
(3) byte14_probe's final pass is skipped on a suspect device (tools/byte14_probe.py:221-225). It runs before `guard.release()`, so after Ctrl-C the guarded `scan` raises `Stopped` (tools/probing.py:120-165). gain_probe skips its restore on a suspect device (tools/gain_probe.py:242-245), and set_gain_offset itself refuses when suspect.
(4) SIGTERM, SIGHUP and SIGBREAK are handled like Ctrl-C by DeferredInterrupt (rps7200/console.py:78-83, 141-161). scan_roll always runs `writer.finish()` under a deferred interrupt (tools/scan_roll.py:1227-1256).
(5) tools/scan.py files each held pass in its own try under `filing_interrupt`. A refused pass is kept with keep_unfiled and does not stop the passes behind it (tools/scan.py:420-470).
(6) TP-08: the bulk read and a pause part way through the payload now get the pass's idle budget (`timeout_ms=int(idle_timeout*1000)`, direct.py:2650-2651; `stall_limit = max(PARTIAL_READ_TIMEOUT_S, timeout_ms/1000)`, usb_transport.py:872).

**What is left:**

(a) FrameWriter and the ScanSession worker are still `daemon=True` (session.py:2139, 2597). Normal exits are covered, because the window waits for the session and the CLIs call writer.finish in finally.
(b) `force_abort` still closes the transport from the UI thread while a transfer may be in flight (session.py:2633-2653). TODO.md ('force_abort under Windows is unknown') leaves this deliberately, as a decision for the owner.
(c) TP-A2 is still open: the pass read calls `read_lines(..., retries=1)` (direct.py:2650) and the calibration read does the same (direct.py:3157). One queued one-shot CHECK CONDITION therefore ends the pass, and the device is marked suspect.
(d) tools/scan.py still holds passes in memory until the device closes. With RPS7200_DEBUG off (the default), a hard kill (SIGKILL, power loss) loses them. With debug on, the spool survives and can be recovered with `file-spool`.

**New problem a fix introduced:**

None found.

## P15 -- `--no-shading` does not skip calibration; the stalling lazy path is live

**Status:** fixed (was mostly-fixed at 03aacba) · **Commits:** 284a727, 5fe8d1c, 35c3906, 0a2184d, 3848243, 1e24ce1

**What the code does now:**

There is no lazy calibration left. `calibrate_shading` has one caller, `ensure_shading` (rps7200/direct.py:1244), and scan() raises `uncalibrated` when it has no reference. `shading` is threaded through scan_roll, prescan, the hold loop and the bracket (direct.py:4546-4548, 5200-5206, 5387). tools/scan_roll.py passes `shading=not args.no_shading` (tools/scan_roll.py:699, 1049).
The calibration loop ends only on EndOfData. Any other refusal propagates, marks the device suspect and archives the partial lines as failed. A silent timeout raises and builds no reference (direct.py:3150-3228). The line count is not compared with an expected number, but a partial reference can no longer be built silently.
The new problem status.md recorded is fixed: the probes that had lost their lazy calibration now call `probing.ensure_reference` (tools/hold_probe.py:172, tools/transport_truth.py:160, tools/roll_registration_walk.py:311, tools/gain_probe.py:156, tools/fast_ir_probe.py:216/469, tools/exposure_probe.py:342, tools/byte14_probe.py:162). uniformity calls ensure_shading (tools/uniformity.py:786-790). The stale hold_probe message is gone.
In the window, `calibrated` is still set on submit (tools/gui.py:2207), but now beside `_calibration_pending`. It is reconciled from `session.calibrated` when the job ends without a 'calibrated' event (gui.py:4305-4311) and from the event itself (gui.py:4353-4356). A scan queued behind a failed Calibrate is refused by the driver rather than calibrating.

**What is left:**

No defect is left. While a calibration is pending, the button reads 'Calibrate again' (gui.py `_sync_calibration`), because `calibrated` is set optimistically on submit so that the next scan queues behind the calibration. That is a deliberate UI choice with no path to the device.

**New problem a fix introduced:**

None found.

## P16 -- Read timeouts and the 10-minute budget are not enforced where they matter

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 0eb9692, 3a8dd1b, 321c94f

**What the code does now:**

(1) tools/scan_roll.py now prints an estimate before the device opens (`say_roll_estimate`, tools/scan_roll.py:394-437, called at 716) through `session.say_estimate`. That function warns when the slow end passes FOREGROUND_S (rps7200/session.py:1748-1766). tools/scan.py and the probes do the same, and the probes count the calibration.
(2) The bulk transfer timeout and the mid-payload stall now follow the pass's idle budget: `read_lines(n, bpl, retries=1, timeout_ms=int(idle_timeout * 1000))` (rps7200/direct.py:2650-2651) and `stall_limit = max(PARTIAL_READ_TIMEOUT_S, timeout_ms / 1000.0)` (rps7200/usb_transport.py:872). `read_idle_s` gives an untied IR pass `UNTIED_INFRARED_IDLE_S` (direct.py:770, 905-909). read_lines' `max_wait_s=300` covers the 287 s idle.
(3) fast_ir_probe refuses IR-blind film before opening the device (tools/fast_ir_probe.py:177-180).
(4) The doc mismatch is fixed. The comment on INFRARED_FLOOR_S says 'It guards no read ... Changing this moves no timeout' (direct.py:755-760), and CLAUDE.md now says the same.

**What is left:**

Neither tool refuses a foreground run past about 8 minutes without an opt-in such as `--background-ok`. Both only warn (session.py:1762-1765). Whether to refuse is a decision for the owner, since a tool cannot reliably tell that it is running in the foreground under the harness.

**New problem a fix introduced:**

None found.

## P17 -- Calibration starts without asking what is in the transport

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 3848243, 51e1cb8

**What the code does now:**

Window: the Calibrate button opens the prompt (tools/gui.py:2131-2157). A measurement is refused until the unticked 'The film is in the transport' box is ticked (gui.py:2311-2324), and a bare Return goes through the same refusal (gui.py:2340). on_calibrate also refuses while a job runs (gui.py:2191). CLIs: film_unconfirmed (rps7200/console.py:193-224) asks at a TTY and refuses non-interactively unless --film-loaded is given. It runs before the device opens in tools/scan.py:254-259 and tools/scan_roll.py:529-535; the latter's condition mirrors the calibrate() call at scan_roll.py:918. uniformity.py now calibrates first through ensure_shading, after 'press Enter with film in the transport' (tools/uniformity.py:776-790). Only then does it ask for the empty transport, for metering that only loads the reference (uniformity.py:815-826). That also fixes the ShadingUnavailable crash the second audit found. Media bit: calibrate_shading keeps the READ STATE it began with, logs when byte 8 says empty (rps7200/direct.py:3018-3038), returns media_loaded (direct.py:3284-3286) and archives it in calibration.json (direct.py:1148).

**What is left:**

The debug probes still calibrate with no film question and no --film-loaded: tools/probing.py:98-117 ensure_reference, called from byte14_probe.py:162, exposure_probe.py:342, fast_ir_probe.py:216/469, gain_probe.py:156 and hold_probe.py:172. Each only prints media_loaded first; exposure_probe says a clear bit is 'a note, not a refusal'. These probes were not in the original findings, but they are CLIs that calibrate unasked, which the fix plan covered. The media bit is logged only when it reads empty. That is deliberate, since it is recorded either way.

## P18 -- One roll is scattered across folders; unnamed rolls share one folder

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 20f2237, c76d146, 6d6c4ae

**What the code does now:**

All paths use one derivation, roll_dir (rps7200/session.py:968-991), which sanitises names and gives an empty name a unique new_roll_name (session.py:951-965). It is used by the session (session.py:3227), the Roll button (tools/gui.py:2563-2565), the sheet's _roll_folder (gui.py:2807-2847), _write_approved into that same folder (gui.py:3848-3874) and the CLI (tools/scan_roll.py:610-621). Reopen restores the roll box (open_roll -> _show_roll_name). Left item (1), DP-05: --demo now refuses --library, --rolls and --reference outside demo/ (gui.py:10048-10056). Settings default to demo/gui-settings.json (gui.py:10069-10070). Left item (3): a sheet from outside session.rolls no longer lands in a same-named unrelated local folder. It gets that name or -2, -3, ... unless same_walk says the folder holds this walk, and the choice is memoised (gui.py:2822-2846). Before a walk into a folder that exists, folder_note says what it holds and what is replaced (gui.py:6942-6972, shown at gui.py:2565). Stored sheet state is keyed by walk_stamp, so it no longer leaks onto a new strip (see P22).

**What is left:**

(a) A fresh walk into an existing folder whose name was typed (GUI, or scan_roll --roll/--out) leaves the old strip's approved.json in place. Nothing sets it aside: the session's _roll never touches it (session.py:3156-3411), and folder_note does not mention it. On reopen, read_survey applies its offsets, turns and flips to the new strip. Its reference_entry values override the new walk's prescan entries, because approved entries are merged last (gui.py:6139-6144). _write_approved keeps its unticked frames in the next commission (gui.py:3894-3900). (b) Overwriting prescanNN.tif after the warning is by design and is the owner's call. (c) DP-05 residue: --settings with --demo is not refused (gui.py:10048-10070 checks only --library, --rolls and --reference), so a demo run can write the real gui-settings.json; --out is also unpinned, which may be intended. (d) A roll reopened from outside session.rolls that has no walk puts its bare folder name in the roll box (open_roll). The plain Roll button then scans into roll_dir(rolls, name), an unrelated local folder of that name if one exists, with only folder_note's warning. That is the Roll-button analogue of the old item (3) and is narrow.

**New problem a fix introduced:**

none beyond the above

## P19 -- roll.json / survey.json / approved.json can lie about what was done

**Status:** fixed (was fixed at 03aacba) · **Commits:** 4778233, e28038f, 2975bcd

**What the code does now:**

A frame is recorded done=False while awaiting its filing (RollManifest.record, rps7200/session.py:1366-1383). It becomes done=True with its entry, or done=False with filing_error, only on the writer's answer (session.py:1385-1459). The CLI records the same way, through on_filed=answering(... record_of.filed(...)) (tools/scan_roll.py:1233-1239, 1242). Manifests are written atomically with a .bak (write_manifest, session.py:1043). An unreadable file falls back to .bak or is set aside and reported (read_manifest/earlier_manifest, session.py:1089-1148). --start-at merges the earlier roll.json (scan_roll.py:924-940). approved.json is merged frame by frame, and an unparseable one is kept aside (tools/gui.py:3868-3930). Both minor residues from 03aacba are closed. A walk's record now names prescanNN.tif and its entry only once the writer has written them (RollManifest.prescan_told/amend, session.py:1334-1352 and 1412-1450; used at session.py:3569 and scan_roll.py:969-1000). scan_roll --approved reads survey.json through read_manifest with its .bak fallback (scan_roll.py:328-334). A retired manifest's late answers no longer overwrite a newer walk's survey.json (retire, session.py:1322-1332 and 3411).

**What is left:**

nothing

## P20 -- Turning or flipping during a walk corrupts the stored walk

**Status:** fixed (was fixed at 03aacba) · **Commits:** f3c41b3, a98ee17

**What the code does now:**

Each walk record carries the turn and flip its prescanNN.tif was actually written with (record['prescan_rotation'] = turn, rps7200/session.py:3717-3721, from walked_as at 3575). Earlier walks' records get the file-level pair before it is overwritten (session.py:3290-3297). Readers un-orient each file by its own pair through prescan_arrangement (session.py:802-816): read_survey (tools/gui.py:6159) and scan_roll --approved (tools/scan_roll.py:342). Export uses the per-frame arranged pair first (gui.py:6400-6407). _into_survey skips a result already in the survey (gui.py:4742-4743), and remember_arrangement no longer touches the survey (gui.py:4753 onward). The read_survey docstring now describes the per-file pair (gui.py:6076-6085), which clears the cosmetic note from 03aacba.

**What is left:**

nothing. The manifest's top-level rotation/flipped stays the start-of-walk value by design, and is used only for walks older than the per-record fields.

## P21 -- Roll export can deliver the wrong entry (a 300 dpi prescan as a frame)

**Status:** fixed (was partly-fixed at 03aacba) · **Commits:** 521e2e2, fe51b56

**What the code does now:**

roll_entry_index builds three joins: ids (every entry by directory name), folders (frame-kind roll_membership by the recorded folder) and names (legacy labels only). Prescans are skipped by membership kind or by the 'prescan' tag (tools/gui.py:6545-6607). roll_entries follows roll.json's own per-frame entry id first. Only frames with no recorded entry fall back to the folder, then the name (gui.py:6622-6649), and PureWindowsPath handles ids written on Windows. roll_summary collects 'recorded' from done records that carry an entry (gui.py:6711-6720). Export names each file by the entry's own resolution and channel count via _scanned_as (gui.py:6374-6412). Both writers label frames with roll_frame_label (rps7200/session.py:926; tools/scan_roll.py:1207), and the Film panel's frame note no longer overrides it. Frames record roll_membership with kind and folder (scan_roll.py:1183-1184). The duplicate, rename and name-reuse cases from 03aacba are closed by the id join: a duplicate's roll.json names the original entries until it is rescanned, and then its own.

**What is left:**

Minor edge: the name fallback applies per frame, not only to legacy rolls. A done frame with no recorded entry (filed with no library, or copies only because the pass published no meta) falls back to by_folder and then to by_name (gui.py:6645). By name, it can pick up another same-named roll's frame of that number, such as a duplicate or a renamed or reused name. The docstring says this fallback is only for rolls 'written before it named its entries', and the code does not enforce that.

## P22 -- Contact-sheet decisions leak between strips or are lost

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 6d6c4ae, 4021fa3, f5a43dd, d3881f4

**What the code does now:**

A new walk closes the old sheet first (tools/gui.py:2642) and resets sheet_state. An explicit zero is stored as as_walked and read back (gui.py:3917-3920, 6519-6528). The position window is destroyed with the sheet (_close_sheet, gui.py:2849-2861). The leak through a typed name is closed: _store_sheet_state files the decisions under the folder name plus walk_stamp, the survey.json mtime (gui.py:3046-3064, 6927-6939), and _stored_sheet returns them only for that same walk (gui.py:3100-3116). Decisions are filed on every change through _keep_sheet_soon, 1.5 s after a burst (gui.py:3066-3087, called at 9514, 9635 and 9827). They are also filed on quit and on opening another roll (gui.py:4069, 4092, 3439). Reopen now reads them back for a walk never commissioned (open_roll, gui.py:3477-3491).

**What is left:**

(a) open_roll uses the stored sheet only when no approved.json exists (gui.py:3482-3483). Once any frames have been commissioned, a later reopen or a restart shows only approved.json. Ticks, positions and turns set afterwards on frames not yet commissioned are stored but never read back, so GUI2-16 persists for a partly commissioned roll. (b) The stamp is survey.json's mtime, and the writer can still rewrite that file after the walk has ended (RollManifest.amend -> _keep, rps7200/session.py:1334-1352). A decision stored before the last prescan's amendment lands can therefore be orphaned after a crash. Low risk, since the next change, closing or quitting stores again with the current stamp.

**New problem a fix introduced:**

The walk_stamp keying in (b) is a mild fragility the fix introduced. I have not seen it fail.

## P23 -- Busy guards sit on buttons, not on actions; submit() cancels Stop

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 86069ea, 27f448f

**What the code does now:**

submit() no longer clears _stop (rps7200/session.py:2600-2606), and request_stop drains the queue (session.py:2608-2631). The window checks _working() (busy or any non-Calibrate job handed over, tools/gui.py:2386-2394) in on_prescan and on_scan (gui.py:2442, 2455), which closes the double-press window, and in on_roll (2492) and on_scan_chosen (3622). Moves and aim-clicks go through _moving_refused (gui.py:3949-3967), and on_calibrate checks too (2191). Every item left at 03aacba is closed. (1) open_roll refuses while working or surveying (gui.py:3371), so the browser's Open is covered. (3) An aim-click only works on the prescan taken where the film is now (_aim_from, set at gui.py:4320 and cleared on every move or roll at 2408, 3973 and 4024; checked at 5929). (4) _roll_is_busy refuses Delete, Rename and Duplicate for a folder whose manifest is still ahead of disk, and for the open sheet's folder (gui.py:3118-3150; used at 3235, 3272 and 3318). (5) The Calibrate prompt now checks as well. on_stop clears the window's _queued (gui.py:4026-4031). The stale comment in on_roll is corrected.

**What is left:**

The guard lives in the window, not the session: submit() still accepts anything, which is fix-plan item 1 and a design decision for the owner. There is no guard against another process touching a roll folder (the cross-process half of CC-07). on_close and _confirm_then check self.busy rather than _working() (gui.py:4048, 963). Quitting within one poll of handing over a job is therefore not offered 'stop after this frame', and the queued job runs to its end first. That is a small window.

## P24 -- A full disk does not stop a roll; quitting kills writes mid-file

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 63c914e, 142aaae, 0024015, 9c636a9, 110502e, 274ea9d, b05723c

**What the code does now:**

Free space is checked before a roll starts: refused if it does not fit, warned under 2x (rps7200/session.py:3187-3204). It is checked again before each frame, counting the frames still with the writer (session.py:3734), through short_of_space and roll_space (session.py:1698-1745). The CLI makes the same checks up front (tools/scan_roll.py:646-663) and per frame (room_for_next, scan_roll.py:769-789). The CLI now stops on a failed filing: filing_failed, set in the writer's on_done, feeds should_stop (scan_roll.py:752-764, 791, 1042-1044), and the reason goes into roll.json's stopped field and the exit status (1311-1316, 1394). The window stops the roll on a failed filing and says why, not 'as asked' (_filed/_stop_reason, session.py:2865-2877; _stopped_how 3804-3811). A failure is shown with a marked thumbnail and a non-modal notice, not only a log line (NOT_FILED/COPY_NOT_WRITTEN, tools/gui.py:262-263 and 4284). A failed reindex no longer fails a filed entry; it only warns (rps7200/library.py:503-508). An unreadable settings file is moved aside and reported in the window (settings.load say=, gui.py:449-450 and 652), and a failed save is reported (gui.py:822-827). Quit offers 'stop after the frame in flight' and waits for Save all and Export threads (gui.py:4047-4121).

**What is left:**

(a) Fix-plan item 4 is not done: the session log is still only the Tk text widget (_say, gui.py:4651-4655), and nothing is persisted under library/logs. Dropped raw bytes ('raw bytes do not describe this image', session.py:3986-3990) are still only a log line, though roll.json now keeps filing_error and stopped. (b) The space check uses an uncompressed full-transport ceiling at SPACE_REFUSE = 1.0 (session.py:1680-1696). It errs toward refusing a roll that would fit after compression, which is the owner's call. (c) on_close checks self.busy rather than _working() (see P23), a small timing gap.

**New problem a fix introduced:**

none found. The 03aacba CLI regression (RollManifest._keep swallowing ENOSPC so the roll ran on) is closed by the per-frame space check and filing_failed.

## P25 -- Reopened frames collide, and Delete removes real library entries

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** 474428e, 213f455, 6d6c4ae

**What the code does now:**

Seq collision stays fixed: `_REOPENED_SEQ = itertools.count(-1, -1)` (tools/gui.py:6056), used by read_survey (tools/gui.py:6161). Demo/foreign-library delete is closed: on_delete drops an entry outside `self.session.root` (tools/gui.py:5326-5337, `_within` at 9888-9896). Deleting now needs two confirmations. The first is askyesnocancel defaulting to No/keep (tools/gui.py:5350-5353), then a WARNING askokcancel defaulting to Cancel inside `_delete_entry` (tools/gui.py:5397-5402). Delete is also refused while the scanner works (5390-5395) and renames the entry aside before rmtree (5403-5411). Status (b) is fixed: on_roll now computes `only = tuple(n for n in span if n not in done)` for the reopened roll (tools/gui.py:2572-2591), so done {1,2,4,5} of 1-6 scans only 3 and 6. Status (c) is fixed: the reopen dialog is conditional on `restored` (tools/gui.py:3412-3421). Per-frame turns are still dropped for a real roll (tools/gui.py:2635).

**What is left:**

(a) Fix-plan item 2 is still half done. An entry inside session.root is still deleted permanently: rename-aside then `shutil.rmtree(aside)` (tools/gui.py:5405-5411), with no .trash/ and no undo, though it now takes two deliberate confirmations. Deleting a reopened frame's entry also leaves the roll's approved.json `reference_entry` pointing at the removed folder. Nothing rewrites it (read back at tools/gui.py:6537-6538 and 6167). (b') For an open-ended range ('to the end of the strip', frames None), done frames ahead are still rescanned. They are now named in the question (tools/gui.py:2587-2591), not skipped. Whether a trash folder is wanted, beyond the two-step confirmation, is a decision for the owner.

**New problem a fix introduced:**

None found.

## P26 -- Every demo entry is corrected pixels labelled raw

**Status:** fixed (was fixed at 03aacba) · **Commits:** b18a011, 032d09f, 1042a8d, 66ac66a

**What the code does now:**

DemoScanner declares `last_pixels_raw` (rps7200/demo.py:376). `_forget_last_pass` clears capture, last_pixels_raw, last_raw and last_raw_layout at every pass (rps7200/demo.py:1047-1052; called at 750 and 867). `_take` sets `last_pixels_raw = raw` and the capture from the pass's own bytes, then corrects last with `apply_shading(raw, reference, mask)` (rps7200/demo.py:1106-1115). A source corrected when stored returns no raw, and its report is `CORRECTED_WHEN_STORED` (rps7200/demo.py:1098-1104). The bytes are re-encoded and decoded by the driver's `decode_index` (rps7200/demo.py:786-789). Every pass meta carries `demo: True`, `demo_source` and `demo_fit` (rps7200/demo.py:1127-1140).

**What is left:**

Nothing material. Two labelled divergences remain by design. An uncalibrated source returns uncorrected pixels to shading=True, with `UNCALIBRATED_SOURCE` (rps7200/demo.py:1114-1115). A CORRECTED_WHEN_STORED source returns corrected pixels to shading=False, logged only (rps7200/demo.py:1098-1104). Both are recorded honestly in the meta.

**New problem a fix introduced:**

Low/info, carried over: a resized or shifted pass files a reference resampled to its columns with ccd_mask None (`_pass_reference`, rps7200/demo.py:1235-1237). So a demo library's calibration files describe no real device calibration. The entry is flagged demo.

## P27 -- The demo accepts what the scanner refuses and runs its own roll loop

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** b5def15, 8754d49, b9e43c0, dd06576, 1042a8d, a59eafc

**What the code does now:**

Refusals use the driver's own checks: `_refuse` calls `DirectScanner.correctable_at`/`uncorrectable`, then `uncalibrated` (rps7200/demo.py:676-697). Status (b) is fixed: the infrared refusal is `raise DirectScanner.infrared_blind(film)`, not a retyped message (rps7200/demo.py:851). Status (c) is fixed: `BACKLASH_MM = BACKLASH_UNITS * MM_PER_UNIT` from protocol (rps7200/demo.py:1005). STEP_MM and OVERHEAD_MM are taken from DirectScanner (rps7200/demo.py:1022-1023), and `nudge = DirectScanner.nudge` (990). Status (d) is fixed: film moves no longer wrap. `shown = np.clip(np.arange(w) - self._shift(w), 0, w - 1)` repeats the edge column (rps7200/demo.py:1193-1199). Prescans come back 8-bit via `_at_depth` (1217). Status (e) is half fixed: `depth` is honoured (`bits = 8 if depth == DEPTH_8 else 16`, rps7200/demo.py:845), and the defaults match the driver's. The roll is the driver's loop (`_drivers_roll = DirectScanner.scan_roll`, rps7200/demo.py:982; delegated at 944-946), and so is metering (983).

**What is left:**

(a) Fix-plan item 1 is not done. DemoScanner is still a scanner-level stand-in with its own scan/prescan (rps7200/demo.py:733-901), not a fake usb_transport. The driver's command sequence, byte-14 handling and refusal ordering inside scan()/prescan() are therefore not exercised by the demo. This is an architectural choice for the owner. (e) `scan()` records `frame=list(frame)` in the meta (rps7200/demo.py:886) but never crops to it, because `_fit` takes no frame. It still ignores `advance`/`byte14` in **kw, as documented. No current GUI or session caller passes a window (tools/gui.py:2465-2472), so this is latent.

**New problem a fix introduced:**

None found. The edge-repeat band that replaces wrap-around is a synthetic column band no device produces. It is documented as matching nothing (rps7200/demo.py:1193-1198).

## P28 -- --look-only without --demo drives the real scanner

**Status:** fixed (was mostly-fixed at 03aacba) · **Commits:** 685ff39

**What the code does now:**

The CLI refuses `--look-only` without `--demo` (tools/gui.py:10033-10034), and no_film is wired from args.look_only (tools/gui.py:10112). `_need_film` raises UsbError (rps7200/demo.py:1285-1303). It is now called by advance (467), retreat (497), slide (526), prescan (749), scan (855), scan_roll (937) and calibration. In `ensure_shading`, a measurement calls `self._need_film("calibrate against: ...")` before `_calibrated = True` (rps7200/demo.py:626-633). Only loading an existing cached reference, which touches nothing, still succeeds (rps7200/demo.py:613-624). The sheet's 'will say there is no film when it reaches for it' (tools/gui.py:9084-9090) is now true of Calibrate too.

**What is left:**

Nothing.

**New problem a fix introduced:**

None found.

## P29 -- Bracket merging judges the wrong domain and mixes RGBI with RGB

**Status:** mostly-fixed (was mostly-fixed at 03aacba) · **Commits:** f91a85e, bca672f, d77912f, 4e3f534, c1470e1, 6ff0a59

**What the code does now:**

Saturation is judged on sensor pixels. on_pass collects `last_pixels_raw` (or `sensor_rail` with the library off) (tools/scan.py:405-415), and the merge uses `sensor_frames=`/`sensor_rails=` (tools/scan.py:538-541). Status (b) is fixed at the driver: `DirectScanner.scan_bracket` raises ValueError on `infrared=True` before metering or any pass (rps7200/direct.py:3794-3799). tools/scan.py also refuses `--bracket --ir` (tools/scan.py:219-231) and a single --exposure-scale (tools/scan.py:239-247). Status (c) is mostly fixed: every pass carries `meta["bracket_id"] = bracket_id` (tools/scan.py:297, 382-383) as well as bracket_index/ratio/passes/stops (rps7200/direct.py:3828-3831). The delivered sidecar records the id, merge stats and the entry names (tools/scan.py:548-555), so a bracket can be regrouped and re-merged from its library entries.

**What is left:**

(a) Fix-plan item 4 (spool bracket passes to disk) is still not done. With the library on, `pending` holds every pass's raw pixels plus its capture record, including `raw` bytes (tools/scan.py:292, 384-387; capture_record at rps7200/direct.py `"raw": self.last_raw`). `scan_bracket`'s default `retain=True` (rps7200/direct.py:3750, 3855-3856) also keeps every corrected frame. So a high-dpi bracket needs roughly N x (raw pixels + raw bytes + corrected) in RAM, and there is no memory warning. (c') The merged image and its MergeStats are written only to the delivered file and its sidecar, never to the library. That is arguably correct, since the merge is derived and re-derivable from the filed raw passes, and is a decision for the owner.

**New problem a fix introduced:**

None new. The earlier low note stands: with --no-library, `sensor_rail` is a lossy uint8 sample used only for the merge, and nothing is filed from which to recompute it. Also `infrared and last` in the scan_bracket loop (rps7200/direct.py:3818-3823) is now dead code after the up-front refusal. It is harmless.

## P30 -- The comparison files and several metrics do not measure what ships

**Status:** mostly-fixed (was partly-fixed at 03aacba) · **Commits:** 5a66180, db185c0, eaeb58f, 36ecd63, 7d7ebc2

**What the code does now:**

(a) noise_split now gain-matches b onto a with `solve_relation`, then applies one high-pass to both terms (.claude/skills/measure-scan-quality/scripts/metrics.py:114-125). Registration is stated as the caller's job (105-112). The stale shares are flagged in the docstring (101-103) and SKILL.md:73-76. (b) MSQ-05: agreement_z takes `sensor_a`/`sensor_b`, fits with `ref_sensor`/`other_sensor`, and takes the median only over unrailed pixels (metrics.py:159-213). (c) MSQ-07: the cross-frame test `persistent_deviation` exists and refuses differing widths (metrics.py:240-267). (d) PA-08: registration_margin now scores both orientations and takes the stronger, as measure_shift_mm does (tools/registration_margin.py:248-257; rps7200/framing.py:1115-1118). (e) PA-11: levels_of reads over the recorded metering region, `region_slices(region, ...)` (tools/exposure_probe.py:194-220, 377), and the region is now recorded in last_metering (rps7200/direct.py:3640-3644). (f) PA-07: `MEASURED_BLUE_RATIO = {FILM_NEGATIVE: 4.98, FILM_BW: 9.6}`, with unmeasured films at the divisor (tools/exposure_headroom.py:91, 185-186). The make_comparison docstring now says `1_` is the stored decode and that a decode change is reconstruct's job (tools/make_comparison.py:4, 21-24).

**What is left:**

(a) noise_split still does not register the pair; the docstring leaves that to the caller. The 21%/27%/-3.5% figures in SKILL.md:63-68 have not been re-measured with the new estimator, only flagged. That needs a stored repeat pair and is work for the owner. (d') registration_margin still gates dy in pixels (`abs(dy) <= MAX_DY_PX`, tools/registration_margin.py:284) where production gates in mm (rps7200/framing.py:1135-1138). The two agree at the default --dpi 300 and diverge at other --dpi. (g) filing_load_test still measures wall time per pass only (tools/filing_load_test.py:240-243, verdict at 122). It cannot observe a read stall, a suspect flag or a wedge. CLAUDE.md now states that scope, so this is a known limit and an owner decision rather than a hidden defect. colour_deviation remains indexed by output column. That is now documented, and persistent_deviation checks width only (metrics.py:251-263).

**New problem a fix introduced:**

Low. noise_split's new gain match calls `solve_relation(x, y)` on corrected input with no sensor pixels (metrics.py:120). The bracket's absolute-DN SNR and clip gates are therefore applied to corrected values, the same domain issue MSQ-05 fixed for agreement_z. When the fit fails (non-finite slope), noise_split silently proceeds unmatched (metrics.py:121-122).

## P31 -- Framing: two geometry models, a search window too small, one voter can move film

**Status:** partly-fixed (was partly-fixed at 03aacba) · **Commits:** 2cccdbb, b9bee37, ea2430b, 3f0f2e2, 807acdd, 3755c4c, 20eab4d, f5946d0

**What the code does now:**

(a) On the walk/aim path it is fixed. `WalkReader.judge` returns `None` when `note["source"] == "unconfirmed"`, reasoning 'a walk does not move film on one vote' (tools/frame_edges/propose.py:317-328). `_aim_frame` therefore abstains (rps7200/direct.py:4628-4638). `reread` can still return a lone-vote value (propose.py:330-331), but `_rejudge_for` uses it only to stop a frame, never to move (rps7200/direct.py:4729-4744). The aim log now falls back to `detail["source"]` instead of printing '?' (rps7200/direct.py:4640-4641, 4675). Member exceptions are contained (tools/frame_edges/vote.py:113-133). unread-dpi warnings and refusals remain (tools/scan_roll.py:597-606). scan_roll --approved now clamps offsets to FINE_MAX_MM and holds every walked frame (tools/scan_roll.py:369-377).

**What is left:**

(a') One member can still move film through `tools/scan_roll.py --approved`. hold_from_walk builds an Approved from every propose_centred offset, including `source == "unconfirmed"` (one member) and `"neighbours"` (tools/scan_roll.py:369-377). The driver holds to it (tools/scan_roll.py:1037), and the operator is shown only counts per source (tools/scan_roll.py:561-566). It also still ignores the walk's approved.json (operator-set positions) and re-proposes from scratch. (b) SEARCH_MM is still 9.0 (rps7200/framing.py:877). The comment now admits that a param-87 command (88.8 units, ~110 px) exceeds the 105 px window, so a hold beyond ~84.7 units reads 'unverified'. Widening it is deferred until registration_margin is re-run, which is owner work. (c) Two geometry models remain. `FRAME_WIDTH_MM = 36.0` / `TARGET_GAP_MM` (rps7200/framing.py:601, 755) aim any roll without an edge reader (`StripWalk(reader=edge_reader(film) if edge_reader else None)`, rps7200/direct.py:5124), and frame_edges uses `FRAME_WIDTH_UNITS = 350.6` (framing.py:1992). `_rejudge_for`'s gross-miss bound `MAX_CORRECTION_MM = MAX_GAP_MM - TARGET_GAP_MM` (framing.py:1303; used at direct.py:4738) is also 36 mm-model-derived even when the reader is frame_edges. CLAUDE.md documents the split, so retiring the 36 mm model for positives and Kodachrome is a decision for the owner.

**New problem a fix introduced:**

None found.

## P32 -- The pcap reader hands back keystroke payloads, and the test asserts it does

**Status:** fixed (was fixed at 03aacba) · **Commits:** 3c4d518, 10f480d

**What the code does now:**

The all-records iterator is private, `_records` (rps7200/usbpcap.py:122-153). `packets(raw, devices)` requires addresses, admits only CONTROL/BULK (`_PAYLOAD_TRANSFERS`, usbpcap.py:43, 186), and now also drops control transfers whose setup is a class request. It keeps only standard/vendor (`_PAYLOAD_REQUEST_TYPES = frozenset({0x00, 0x40})`, usbpcap.py:44-50, 187-191), so a HID GET_REPORT input report on EP0 no longer comes back. `_is_setup` accepts only host-to-device records (usbpcap.py:156-168), so a keyboard reply can no longer be read as a setup. The synthetic bus now includes a keyboard GET_REPORT over control (tests/test_usbpcap.py:144-150). test_a_keystroke_is_never_handed_back, test_naming_the_keyboard_yields_nothing_of_it and test_a_devices_reply_is_never_read_as_a_setup cover it (tests/test_usbpcap.py:247-285). The only external user is tools/parse_capture.py:45.

**What is left:**

Nothing.

**New problem a fix introduced:**

None found.
