# Whole-code audit -- summary

A read of the whole driver (`rps7200/`, `tools/`, `tests/`, the build) done **from the
code, not from the documentation**, at commit `83dbb22` (2026-09-23). The questions were
Stefan's:

1. Where do problems occur?
2. Does the demo behave like the real software, with only its inputs changed?
3. How does data flow through the code, and what is saved, where, and in which state?
4. Does the library hold the **exact bit data**, so every scan can be recalculated and
   evaluated later?
5. What can an operator do, what should they not do, and what is unguarded?
6. Where do README / CLAUDE.md / docs / TODO disagree with the code?

Nothing was changed in the code. No scanner was touched.

## The documents

| File | What it answers |
|---|---|
| **this file** | Summary, the verdict, what to fix first, the index |
| [library-exactness.md](library-exactness.md) | Q4 -- what one entry contains, what is exact, what is lost or mislabelled |
| [dataflow.md](dataflow.md) | Q3 -- how data moves through the code, by thread, with every point where it is stored |
| [persisted-state.md](persisted-state.md) | Q3 -- every file on disk: format, raw or corrected, writer, reader, exact or not |
| [demo-vs-direct.md](demo-vs-direct.md) | Q2 -- every place the demo does something the real path does not |
| [user-errors.md](user-errors.md) | Q5 -- rules for the operator until the fixes land, and the guards each needs |
| [doc-mismatches.md](doc-mismatches.md) | Q6 -- every documentation claim the code contradicts |
| [problems/](problems/) | One file per problem: what, where, evidence, failure scenario, **fix plan** |
| [areas/](areas/) | Every one of the 537 findings in full, by subsystem, with the second reader's check |
| [raw/](raw/) | The readers' structured results as JSON (the source of everything above) |

## Verdict

The transport layer is careful. It never sends `SET_SCAN_HEAD`, and no product path sends
`STOP SCAN` or an IEEE1284 reset. It windows reads at 32 KB and waits out zero-length
packets. The decode is sound: the raw bytes in `raw.bin.gz` are byte-exact and checksummed,
and `decode_index` turns bottom-up passes upright from their own line tags.

**The library does not yet keep the promise that everything can be recalculated.** Five
defects put wrong or missing bits into it, and none of them makes a sound:

- **Single scans from the window file corrected pixels labelled raw** (P01). Every view,
  Save As and Export then flat-fields them a *second* time, and `reconstruct` reports each
  one as a changed decode.
- **Debug filing can attach the previous pass's raw bytes to a pass** (P02). That is an
  entry that decodes to a different photograph.
- **A failed filing deletes the only spooled copy** (P03). A failed copy to the output
  folder costs the library entry (P04).
- **The calibration bytes are thrown away** (P05), so no correction can be recomputed from
  scratch.
- **Whole classes of passes are never filed**, and `RPS7200_DEBUG=1` is overridden on every
  real path (P08). That covers metering probes, hold prescans and CLI walks.
- **`duplicates --delete` treats different photographs as duplicates and deletes them**
  (P11).

The second concern is **device safety on failure paths** (P13-P17). After a failed or
interrupted read, the software keeps commanding the device. Ctrl-C abandons reads. The
lazy calibration inside `scan()`, recorded as stalling, is still reachable, including
through `--no-shading`. The window gzips entries with the device open and idle.

Third, **roll state on disk** (P18-P25). One roll's files are scattered across up to
three folders. Unnamed rolls of one day overwrite each other. Manifests can mark frames
done that were never filed. Export can deliver a 300 dpi prescan as a full-resolution
frame.

Fourth, **the demo is not yet "the real software with different inputs"** (P26-P28). It
never exposes raw pixels, so every demo entry is corrected-as-raw. It accepts what the
scanner refuses, and it runs its own roll loop. Because the demo takes a branch the real
scanner does not, it cannot catch P01. It hides it.

537 findings: 18 critical, 95 high, 216 medium, 193 low, 15 info. Verdicts: 437 confirmed, 42 partly (re-described), 58 added by the second reader, 0 refuted.

## What to fix first

Ordered by *bits at risk per line of change*. The first block is small, local and removes
the silent data loss. Each item links to its fix plan.

**1. Stop losing and mislabelling data (small changes, do now)**

| Problem | Change |
|---|---|
| [P01](problems/P01-gui-scan-files-corrected-as-raw.md) | One argument: `_scan` passes `raw_image=last_pixels_raw`, plus a structural guard and a test |
| [P02](problems/P02-debug-filing-stale-raw-bytes.md) | Clear `last_raw` at the start of every pass; debug keeps every pass's own bytes |
| [P03](problems/P03-debug-spool-not-crash-safe.md) | Unlink the spool only after a successful save; self-describing spool files |
| [P04](problems/P04-delivered-copy-before-library-entry.md) | File the library entry *before* writing delivered copies |
| [P11](problems/P11-duplicates-delete-destroys-scans.md) | Disable `duplicates --delete` until sameness is provable (raw-byte hash) |
| [P08](problems/P08-passes-never-filed.md) | Honour `RPS7200_DEBUG` (`debug=None`) in the session and both CLIs |

**2. Stop driving a device that may be mid-read**

[P14](problems/P14-failure-paths-keep-driving-device.md) (a device-suspect state, Ctrl-C
handling), [P15](problems/P15-lazy-calibration-inside-scan.md) (remove the lazy
calibration), [P13](problems/P13-gzip-with-device-open.md) (decide and measure gzip with
the device open), [P16](problems/P16-timeouts-and-runtime-budget.md) (timeouts for untied
IR, runtime estimates), [P17](problems/P17-calibration-without-asking.md) (ask before
calibrating).

**3. Make the library complete**

[P05](problems/P05-calibration-not-stored-exactly.md) (store calibration bytes),
[P09](problems/P09-record-missing-parameters.md) (record commands, INQUIRY, metering,
bracket and roll context, code identity), [P07](problems/P07-failed-and-short-passes-lose-bytes.md),
[P06](problems/P06-7200dpi-realignment-not-recorded.md),
[P10](problems/P10-non-atomic-writes.md) (atomic entries), and
[P12](problems/P12-library-maintenance-tools.md), which makes `verify`/`reconstruct`
trustworthy again so they catch the next regression.

**4. Roll state**: P18 -> P19 -> P21 -> P20 -> P24 -> P22 -> P23 -> P25.

**5. The demo**: P26, then P27. P27's durable fix is to replace the transport rather than
the scanner, so the demo runs `DirectScanner` itself and cannot drift. Then P28.

**6. Measurement and outputs**: P30 first, because the comparison files Stefan judges
by eye do not show what the driver ships. Then P29, P31, P32.

**7. Documentation**: [doc-mismatches.md](doc-mismatches.md).

### Entries already in the library that are affected

- **Single scans from the window** (tag `gui`, not `prescan`) hold corrected pixels.
  Their `raw.bin.gz` is the true bytes, so they are repairable (P01, fix step 4).
- **Debug entries of passes run with `keep_raw=False`** (direct `scan()`/`prescan()`
  calls in ad-hoc scripts and probe tools) may carry another pass's bytes (P02). Find them with `reconstruct`: shape or
  pixels will disagree.
- **Demo entries** (`demo/library`) are corrected-as-raw with a *source* entry's bytes
  (P26). Do not use them as evidence.
- **Every entry** lacks its calibration bytes (P05). That cannot be repaired
  retroactively.
- **7200 dpi entries** are realigned, with the realignment unrecorded (P06).
  `reconstruct` will always flag them.

## Problems

| # | Problem | Severity | Reports |
|---|---|---|---|
| **A** | **Library exactness -- the central requirement** | | |
| [P01](problems/P01-gui-scan-files-corrected-as-raw.md) | A single Scan from the window files corrected pixels as the raw scan.tif | critical | 12 |
| [P02](problems/P02-debug-filing-stale-raw-bytes.md) | Debug filing pairs a pass with the previous pass's raw bytes (or none) | critical | 7 |
| [P03](problems/P03-debug-spool-not-crash-safe.md) | The debug spool is deleted on a failed save and lost on a crash | high | 11 |
| [P04](problems/P04-delivered-copy-before-library-entry.md) | A failed delivered copy (output folder, rolls/) costs the library entry | critical | 6 |
| [P05](problems/P05-calibration-not-stored-exactly.md) | Calibration bytes are thrown away; only a derived reference is kept | high | 11 |
| [P06](problems/P06-7200dpi-realignment-not-recorded.md) | At 7200 dpi scan.tif is not the plain decode, and nothing records it | high | 12 |
| [P07](problems/P07-failed-and-short-passes-lose-bytes.md) | A short or failed pass loses its raw bytes -- the case where they matter most | high | 6 |
| [P08](problems/P08-passes-never-filed.md) | Whole classes of passes are never filed, and RPS7200_DEBUG is overridden | high | 15 |
| [P09](problems/P09-record-missing-parameters.md) | The entry record drops parameters needed to re-derive or evaluate a pass | high | 19 |
| [P10](problems/P10-non-atomic-writes.md) | Entries and manifests are written in place; partial files look complete | high | 11 |
| [P11](problems/P11-duplicates-delete-destroys-scans.md) | `library.py duplicates --delete` destroys scans of different photographs | critical | 5 |
| [P12](problems/P12-library-maintenance-tools.md) | reconstruct / verify / migrate-raw report or repair the wrong thing | high | 3 |
| **B** | **Scanner safety -- wedges, abandoned reads, calibration** | | |
| [P13](problems/P13-gzip-with-device-open.md) | The window gzips library entries with the scanner open and idle | high | 8 |
| [P14](problems/P14-failure-paths-keep-driving-device.md) | After a failed or interrupted read, the software keeps talking to the device | critical | 12 |
| [P15](problems/P15-lazy-calibration-inside-scan.md) | `--no-shading` does not skip calibration; the stalling lazy path is live | high | 8 |
| [P16](problems/P16-timeouts-and-runtime-budget.md) | Read timeouts and the 10-minute budget are not enforced where they matter | medium | 5 |
| [P17](problems/P17-calibration-without-asking.md) | Calibration starts without asking what is in the transport | medium | 3 |
| **C** | **Rolls, walks and their state on disk** | | |
| [P18](problems/P18-roll-folder-identity.md) | One roll is scattered across folders; unnamed rolls share one folder | high | 16 |
| [P19](problems/P19-roll-manifests.md) | roll.json / survey.json / approved.json can lie about what was done | high | 12 |
| [P20](problems/P20-rotation-during-walk.md) | Turning or flipping during a walk corrupts the stored walk | high | 8 |
| [P21](problems/P21-roll-to-library-join.md) | Roll export can deliver the wrong entry (a 300 dpi prescan as a frame) | high | 7 |
| [P22](problems/P22-contact-sheet-state.md) | Contact-sheet decisions leak between strips or are lost | high | 7 |
| [P23](problems/P23-busy-guards-and-stop.md) | Busy guards sit on buttons, not on actions; submit() cancels Stop | medium | 11 |
| [P24](problems/P24-disk-full-and-quitting.md) | A full disk does not stop a roll; quitting kills writes mid-file | high | 9 |
| [P25](problems/P25-reopened-frames.md) | Reopened frames collide, and Delete removes real library entries | medium | 7 |
| **D** | **The demo is not yet the real software with different inputs** | | |
| [P26](problems/P26-demo-files-corrected-as-raw.md) | Every demo entry is corrected pixels labelled raw | high | 12 |
| [P27](problems/P27-demo-refusals-and-roll-loop.md) | The demo accepts what the scanner refuses and runs its own roll loop | high | 14 |
| [P28](problems/P28-look-only-drives-real-scanner.md) | `--look-only` without `--demo` drives the real scanner | medium | 4 |
| **E** | **Outputs, measurement and framing** | | |
| [P29](problems/P29-bracket-merge.md) | Bracket merging judges the wrong domain and mixes RGBI with RGB | high | 6 |
| [P30](problems/P30-comparison-files-and-metrics.md) | The comparison files and several metrics do not measure what ships | high | 15 |
| [P31](problems/P31-framing-geometry-and-detectors.md) | Framing: two geometry models, a search window too small, one voter can move film | high | 11 |
| [P32](problems/P32-usbpcap-returns-keystrokes.md) | The pcap reader hands back keystroke payloads, and the test asserts it does | medium | 2 |

## Findings by area

| Area | Findings | critical | high | medium | low | info |
|---|---|---|---|---|---|---|
| [USB transport, protocol and command sequence](areas/transport-protocol.md) | 34 | 1 | 5 | 16 | 11 | 1 |
| [Decode, direction, shading and debug filing](areas/decode-and-debug-filing.md) | 36 | 2 | 3 | 15 | 14 | 2 |
| [Demo scanner vs the real scanner](areas/demo-parity.md) | 22 | 1 | 4 | 8 | 8 | 1 |
| [The library store](areas/library.md) | 28 | 1 | 8 | 12 | 6 | 1 |
| [ScanSession, FrameWriter and rolls](areas/session-roll.md) | 31 | 3 | 11 | 12 | 5 | 0 |
| [Framing and transport units](areas/framing-units.md) | 21 | 0 | 1 | 9 | 11 | 0 |
| [The window (tools/gui.py, first half)](areas/gui-part1.md) | 41 | 2 | 5 | 19 | 14 | 1 |
| [The window (tools/gui.py, second half)](areas/gui-part2.md) | 37 | 1 | 8 | 15 | 13 | 0 |
| [Outputs: export, TIFF, DNG, preview, mono, bracket, settings](areas/outputs.md) | 29 | 2 | 4 | 8 | 14 | 1 |
| [Operator CLI tools and build](areas/cli-operator-tools.md) | 34 | 2 | 7 | 12 | 12 | 1 |
| [Probe and analysis tools](areas/probe-and-analysis-tools.md) | 27 | 1 | 5 | 10 | 10 | 1 |
| [README.md and CLAUDE.md vs the code](areas/docs-readme-claude.md) | 31 | 1 | 8 | 8 | 13 | 1 |
| [docs/*.md and TODO.md vs the code](areas/docs-plans-todo.md) | 33 | 0 | 6 | 12 | 14 | 1 |
| [The test suite](areas/tests.md) | 28 | 1 | 4 | 14 | 9 | 0 |
| [Frame-edge detectors (gap pass)](areas/frame-edges-detectors.md) | 15 | 0 | 2 | 5 | 7 | 1 |
| [Code identity and PROTOCOL_REVISION (gap pass)](areas/code-identity-and-protocol-revision.md) | 14 | 0 | 0 | 3 | 11 | 0 |
| [Cross-process and thread concurrency (gap pass)](areas/cross-process-and-thread-concurrency.md) | 18 | 0 | 3 | 11 | 4 | 0 |
| [Disk full, crashes, recovery, time (gap pass)](areas/resource-exhaustion-crash-recovery-time.md) | 25 | 0 | 7 | 11 | 6 | 1 |
| [The measure-scan-quality metrics (gap pass)](areas/measure-scan-quality-skill.md) | 17 | 0 | 3 | 7 | 5 | 2 |
| [Tests that pin the defects (gap pass)](areas/uncited-tests-vs-findings.md) | 16 | 0 | 1 | 9 | 6 | 0 |

### By category

| Category | Findings |
|---|---|
| data-integrity | 142 |
| doc-mismatch | 97 |
| bug | 78 |
| user-error | 47 |
| demo-divergence | 37 |
| design | 31 |
| error-handling | 26 |
| test-gap | 26 |
| hardware-safety | 24 |
| concurrency | 20 |
| library-completeness | 6 |
| dead-code | 3 |

## How this was done, and its limits

- 15 readers, one per subsystem, plus one tracing the dataflow, each reading its files in
  full. A second reader then re-opened every cited line and tried to refute each finding.
  A completeness critic named six gaps (code identity, frame-edge detectors, concurrency,
  crash recovery, the metrics, tests vs defects), each covered by another reader/checker
  pair. 43 agents in all.
- **No finding was refuted.** 42 were corrected in their description or location, 58 were
  added by the second reader, and severities were adjusted. So the second pass worked as a
  correction pass more than a filter. Nothing was executed: no tests were run, and no
  scanner was touched. The critical and high findings behind P01-P04 and P11 were
  re-checked by hand against the code before these documents were written. The others
  rest on the two readers.
- Many findings are the same defect seen from different subsystems. The problem files
  group them. The area files keep every report, so nothing is lost in the grouping.
- Line numbers are as of `83dbb22`. A script checked all 1,939 `path:line` references in
  these files. All files exist except `research/frame-edge/data/manifest.json`, the
  study's data, which is not on disk. 13 references point past the end of their file.
  All of them are in four frame-edge findings (FE-02, FE-08, FE-10, FE-13), in
  `tools/frame_edges/centre.py`, `watch.py`, plus one each in `library.py` and
  `settings.py` off by one or two lines. Read those locations as approximate. FE-02's
  substance was re-checked by hand: `vote.lone_gap` lets one member's gap stand, and
  `propose` passes it on as `unconfirmed` rather than refusing.
