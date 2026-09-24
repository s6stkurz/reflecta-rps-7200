# P13 -- The window gzips library entries with the scanner open and idle

**Severity** high · **Group** B: Scanner safety -- wedges, abandoned reads, calibration · **Reported independently by** 8 findings in 7 areas

[Back to the summary](../README.md)

## The problem

CLAUDE.md: "gzipping one with the scanner open and idle preceded a wedge once", and a
single scan "compresses nothing while the device is open". In the GUI, `ScanSession`
keeps the device open for the whole session and every single Scan and Prescan goes
through `FrameWriter`, which gzips `raw.bin.gz` and compresses TIFFs on its thread while
the worker sits in `_jobs.get()` with the device claimed and idle. The roll exception
(device busy while the previous frame gzips) is argued in CLAUDE.md; this case is the
one the rule was written against. Full-resolution loads (float64 shading of hundreds of
MB) also run in the same process with the device claimed.

## Fix plan

Pick one and write it down:

- either defer filing of single scans until the session is idle *and* closed (as
  `tools/scan.py` does), or
- measure it: extend `tools/filing_load_test.py` to the open-and-idle case (and fix its
  verdict, see P30) and run it before trusting the GUI path.

## Evidence (from [SR-06](../areas/session-roll.md#session-roll-sr-06))

**Where:** `rps7200/session.py:10-16`, `rps7200/session.py:1013-1021`, `rps7200/session.py:1311-1331`, `rps7200/session.py:1440-1458`, `rps7200/library.py:186-211`, `rps7200/library.py:310`, `tools/gui.py:4110`

```text
The worker opens the scanner once (`self._scanner.open()`, session.py:1297) and closes it only at shutdown (session.py:1336-1340); between jobs it blocks on `job = self._jobs.get()` (session.py:1314). Each Scan/Prescan job ends with `self._file(...)` -> `self._writer.submit(...)`, and the writer thread runs `library.save` -> `gzip.open(... compresslevel=6)` (library.py:192) and `reindex(root)` over every entry (library.py:310) while the worker is idle with the device open. The same is true for the last frame(s) of every roll and every frame after a stop. The GUI also runs library.corrected() on 142 MB frames (gui.py:4110) with the session open.
```

**Failure scenario:** Operator scans a single 3600 dpi RGBI frame; the writer gzips ~140 MB of raw bytes plus reindexes the library while the scanner sits open and idle for many seconds -- the documented wedge precondition.

**Second reader's check:** The worker opens the device once (session.py:1297) and closes it only in the finally at shutdown (1336-1340). Between jobs it blocks on `self._jobs.get()` (1314). Each Prescan and Scan ends in _file -> writer.submit, and the writer runs library.save -> `gzip.open(..., compresslevel=6)` (library.py:192) followed by `reindex(root)` (310), while the worker sits idle with the device open. The same holds for the last frames of a roll. The FrameWriter docstring's 'the device is busy rather than idle throughout' (session.py:1016-1020) is true only while another frame is scanning. CLAUDE.md's 'A single scan compresses nothing while the device is open' describes DirectScanner's debug spool, not the GUI path.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [SR-06](../areas/session-roll.md#session-roll-sr-06) | session-roll | high | confirmed | The GUI session gzips entries (and does all other heavy local work) with the device open and idle | rps7200/session.py:10-16, rps7200/session.py:1013-1021, rps7200/session.py:1311-1331 |
| [GUI2-A1](../areas/gui-part2.md#gui-part2-gui2-a1) | gui-part2 | high | found-by-verifier | Every GUI single scan and prescan is gzipped into the library on the writer thread while the device is open and idle | rps7200/session.py:1311, rps7200/session.py:1086-1097, rps7200/session.py:1292-1296 |
| [OUT-06](../areas/outputs.md#outputs-out-06) | outputs | high | confirmed | GUI compresses TIFFs and gzips raw bytes while the scanner is open and idle | rps7200/session.py:1311, rps7200/session.py:1013-1020, rps7200/session.py:1059-1101 |
| [D04](../areas/docs-readme-claude.md#docs-readme-claude-d04) | docs-readme-claude | high | confirmed | The GUI gzips library entries with the scanner open and idle after every single scan or prescan | rps7200/session.py:1311, rps7200/session.py:1013-1021, rps7200/session.py:1429-1438 |
| [TP-13](../areas/transport-protocol.md#transport-protocol-tp-13) | transport-protocol | medium | confirmed | GUI single Scan/Prescan gzips library entries with the device open and idle, contrary to the documented rule | rps7200/session.py:1013-1020, rps7200/session.py:1080-1100, rps7200/session.py:1455-1458 |
| [LIB-11](../areas/library.md#library-lib-11) | library | medium | confirmed | GUI single-scan and prescan filing gzips with the device open and idle, contrary to the documented wedge precaution | rps7200/session.py:1013-1025, rps7200/session.py:1311, rps7200/session.py:1429-1438 |
| [GUI1-24](../areas/gui-part1.md#gui-part1-gui1-24) | gui-part1 | medium | confirmed | GUI single scans and prescans are gzipped into the library with the device open and idle | rps7200/session.py:10-16, rps7200/session.py:1013-1025, rps7200/session.py:1311-1331 |
| [GUI2-18](../areas/gui-part2.md#gui-part2-gui2-18) | gui-part2 | medium | partly | Heavy full-resolution work runs in the GUI process with the device claimed: no busy guard, and unbounded full-res loads on every show or rotate | tools/gui.py:3527-3539, tools/gui.py:4100-4120, tools/gui.py:3911-3935 |
