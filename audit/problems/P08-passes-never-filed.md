# P08 -- Whole classes of passes are never filed, and RPS7200_DEBUG is overridden

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 15 findings in 11 areas

[Back to the summary](../README.md)

## The problem

"File every scan" does not hold for:

- **metering probes** (up to three 300 dpi RGB passes before every auto-exposed scan and
  every roll frame);
- **hold / aim / verification prescans** in a roll (only the last one survives, as a
  corrected 8-bit `prescan.tif`);
- **roll-frame prescans** -- `prescan.tif` in a frame entry is the *corrected* prescan with
  no bytes, mask, resolution or meta, and the raw prescan (`raw_prescan`) is dropped;
- **`tools/scan_roll.py --dry-run`** -- files nothing; the walk exists only as corrected
  `prescanNN.tif` in the roll folder;
- the prescan a correction replaced (`file_entry=False`), whose docstring says debug
  filing covers it.

The escape hatch does not work either: `ScanSession`, `tools/scan.py` and
`tools/scan_roll.py` construct `DirectScanner(debug=False)` explicitly, so
`RPS7200_DEBUG=1` -- which CLAUDE.md makes mandatory -- is silently ignored on every
real path.

## Fix plan

1. Honour the environment: construct `DirectScanner(debug=None)` and let `None` mean
   "read `RPS7200_DEBUG`".
2. File walk prescans from `scan_roll.py --dry-run` through `FrameWriter` like the GUI.
3. Store a frame's prescan as its own raw entry (or raw pixels + bytes inside the frame
   entry), not as a corrected 8-bit `prescan.tif`.
4. Metering probes and hold passes: file them in debug mode at least (they are ~370 KB
   each), tagged `probe`/`hold`, linked to the frame entry by id.

## Evidence (from [LIB-05](../areas/library.md#library-lib-05))

**Where:** `rps7200/session.py:1883-1893`, `rps7200/direct.py:3494-3498`, `rps7200/direct.py:3656-3660`, `rps7200/library.py:180-181`, `rps7200/library.py:297-303`, `tools/scan_roll.py:562-565`

```text
session.py:1883 `self._file(seq, number, rf.image, frame_meta, notes, ..., raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta, ...)`, where rf.prescan is the corrected return of `prescan_image, _ = self.prescan(...)` (direct.py:3494) and rf.raw_prescan is ignored. library.py:181 `tiff.write(str(path / "prescan.tif"), prescan)` (no resolution). The record keeps only `"file": "prescan.tif", "read_direction": ..., "carriage_state": ...`.
```

**Failure scenario:** A shading or decode improvement is made. `library.corrected()` and `reconstruct` cover the frame, but the 114 roll prescans the frame-edge detector was trained on stay frozen at the old correction. A prescan whose read direction was misjudged cannot be re-read from its line tags, because it has none.

**Second reader's check:** On a real roll the session files the frame with `prescan=rf.prescan` (session.py:1888). rf.prescan is the corrected return of self.prescan() (direct.py:3493, which runs shading=True). rf.raw_prescan is used only in the dry-run branch (1830). tools/scan_roll.py:562 does the same. library.save:180-181 writes prescan.tif with no resolution. The record's prescan block (297-303) holds only file, read_direction and carriage_state: no raw bytes, mask, meta, sha256 or corrections label. prescan_before is written with file_entry=False (session.py:1850), so its bytes are also lost outside debug mode.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [LIB-05](../areas/library.md#library-lib-05) | library | high | confirmed | prescan.tif in a frame entry is the corrected prescan with no bytes, mask, resolution or meta; real-roll prescan bytes are never filed | rps7200/session.py:1883-1893, rps7200/direct.py:3494-3498, rps7200/direct.py:3656-3660 |
| [SR-05](../areas/session-roll.md#session-roll-sr-05) | session-roll | high | confirmed | Real-roll prescans are kept only corrected; failed-frame, arrival and hold-loop prescans are never filed; debug filing is forced off | rps7200/session.py:1804-1854, rps7200/session.py:1859-1860, rps7200/session.py:1883-1893 |
| [FU-01](../areas/framing-units.md#framing-units-fu-01) | framing-units | high | confirmed | Framing inputs are not stored exactly: roll prescans have no raw bytes; arrival and intermediate hold prescans are discarded | rps7200/session.py:1804-1853, rps7200/session.py:1880-1893, rps7200/session.py:1265-1271 |
| [CLI-03](../areas/cli-operator-tools.md#cli-operator-tools-cli-03) | cli-operator-tools | high | confirmed | CLI tools never file walk prescans, metering probes, hold/aim prescans or verification passes; debug=False hard-coded, so RPS7200_DEBUG is ignored | tools/scan.py:157-160, tools/scan_roll.py:344-353, tools/scan_roll.py:492-510 |
| [D07](../areas/docs-readme-claude.md#docs-readme-claude-d07) | docs-readme-claude | high | confirmed | tools/scan_roll.py --dry-run files nothing in the library, and roll-frame prescans lose their raw bytes (CLI and GUI) | tools/scan_roll.py:492-510, tools/scan_roll.py:542-568, rps7200/session.py:1883-1893 |
| [DOC-04](../areas/docs-plans-todo.md#docs-plans-todo-doc-04) | docs-plans-todo | high | confirmed | Several kinds of pass are never filed outside debug mode, and a roll frame's prescan.tif holds corrected pixels | rps7200/direct.py:2080-2092, rps7200/direct.py:2905-2908, rps7200/direct.py:664-665 |
| [RX-15](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-15) | resource-exhaustion-crash-recovery-time | high | confirmed | A walk made with tools/scan_roll.py --dry-run files nothing in the library: its prescans exist only as corrected 8-bit prescanNN.tif | tools/scan_roll.py:492-510, tools/scan_roll.py:466, rps7200/session.py:1804-1833 |
| [D03](../areas/docs-readme-claude.md#docs-readme-claude-d03) | docs-readme-claude | high | confirmed | RPS7200_DEBUG=1 is silently overridden in tools/scan.py, tools/scan_roll.py and the GUI, so intermediate passes are never filed | tools/scan.py:157-160, tools/scan_roll.py:344-353, rps7200/session.py:1265-1271 |
| [FU-A2](../areas/framing-units.md#framing-units-fu-a2) | framing-units | medium | found-by-verifier | CLI dry-run walk (tools/scan_roll.py --dry-run) files no library entry for its prescans; the hold references for --approved exist only as corrected TIFFs | tools/scan_roll.py:495-508, tools/scan_roll.py:243-254, tools/scan_roll.py:466 |
| [T07](../areas/tests.md#tests-t07) | tests | medium | confirmed | Roll frame entries store the corrected prescan as prescan.tif; the session roll path is never tested with raw_image, raw_prescan or prescan_meta | rps7200/session.py:1874-1891, tools/scan_roll.py:549-566, rps7200/library.py:180-181 |
| [T18](../areas/tests.md#tests-t18) | tests | medium | confirmed | tools/scan_roll.py --dry-run files nothing in the library and forces debug off; the test never checks library entries | tools/scan_roll.py:353, tools/scan_roll.py:489-525, tests/test_scan_roll_tool.py:159-165 |
| [CLI-19](../areas/cli-operator-tools.md#cli-operator-tools-cli-19) | cli-operator-tools | medium | partly | Roll entries store prescan.tif as corrected 8-bit pixels with no raw bytes, no checksum and no 'corrected' label; the CLI drops raw_prescan | tools/scan_roll.py:560-566, rps7200/session.py:1883-1890, rps7200/session.py:1090-1098 |
| [OUT-15](../areas/outputs.md#outputs-out-15) | outputs | medium | confirmed | A roll frame's library prescan.tif is the corrected prescan, unlabelled | rps7200/direct.py:3493-3497, rps7200/direct.py:3656-3660, rps7200/session.py:1880-1890 |
| [DDF-A1](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-a1) | decode-and-debug-filing | medium | found-by-verifier | A roll prescan that a correction replaced is never filed, and the docstring's claim that debug filing covers it is false for every front end | rps7200/session.py:1841-1853, rps7200/session.py:2051-2060, rps7200/session.py:1265-1271 |
| [CC-A2](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-a2) | cross-process-and-thread-concurrency | medium | found-by-verifier | The pre-correction prescan's raw bytes are never filed; the _file docstring says a debug entry covers it, but ScanSession forces debug off | rps7200/session.py:2047-2058, rps7200/session.py:1265-1271, rps7200/session.py:1834-1854 |
