# P06 -- At 7200 dpi scan.tif is not the plain decode, and nothing records it

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 12 findings in 11 areas

[Back to the summary](../README.md)

## The problem

At exactly 7200 dpi `scan()` runs `_realign_native_column_stagger`, which trims 4 rows
and shifts odd columns, and bakes that into the "raw" `scan.tif`. Nothing in the record
says so. `decode_raw`, `reconstruct` and `migrate-raw` never replay it, so every 7200 dpi
entry reads "decode CHANGED" for ever. If such a pass is ever filed through
`ScanSession._file`, the 4-row difference also trips the layout guard, which then drops
the pass's raw bytes entirely.

Reachability (the readers disagreed on this; the verifier's reading is the one that
holds): `scan()` refuses 7200 dpi with shading on (`ShadingUnavailable`, the reference is
5172 columns wide), and the window and `scan_roll` always ask for shading, so the GUI's
7200 rung is always refused -- after metering has already run. Realigned 7200 dpi entries
come from `tools/scan.py --no-shading`, the API and debug filing, and those keep raw bytes
that no longer match `scan.tif`'s height. `dpi_analysis` then compares these
uncorrectable, pre-realignment passes against corrected lower-dpi passes.

## Fix plan

1. Keep `scan.tif` the plain decode at every resolution; move the stagger realignment into
   the corrected path (`library.corrected`/delivery), or record it as a named correction
   (`corrections_applied: ["stagger"]`) that `decode_raw`/`reconstruct` replay.
2. Make the session guard compare against the pre-realignment shape.
3. Stop offering 7200 dpi in the GUI's ladder while the host always refuses it, or refuse
   before metering.

## Evidence (from [CLI-10](../areas/cli-operator-tools.md#cli-operator-tools-cli-10))

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1638-1663`, `rps7200/library.py:411-436`, `rps7200/library.py:439-509`, `tools/library.py:96-113`, `tools/library.py:175-182`

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... image = self._realign_native_column_stagger(image)` and then `raw_pixels = image` (what library.save stores as scan.tif). _realign trims `h - lines` rows. reconstruct/decode_raw only call `DirectScanner.decode_index(raw, params, ...)` / `_deinterleave`, with no realignment, then `if image.shape != stored.shape: return image, (f"decode CHANGED: now {image.shape}, stored {stored.shape}")`.
```

**Failure scenario:** Stefan takes 7200 dpi raw scans with scan.py and later runs `make reconstruct` after an unrelated change. Each 7200 dpi entry prints '! ... decode CHANGED: now (H,W,C), stored (H-4,W,C)'. The command exits 1 and real regressions are buried.

**Second reader's check:** At resolution == NATIVE_COLUMN_STAGGER_DPI (7200, direct.py:1622), scan() realigns and trims `lines` rows (direct.py:2695-2702, 1638-1663) before `raw_pixels = image`. That array is what scan.py files through `last_pixels_raw`. library.reconstruct and decode_raw call only decode_index/_deinterleave, and there is no reference to the stagger anywhere in library.py, so the shape comparison at library.py:487-490 returns 'decode CHANGED'. The CLI counts that as a regression and exits 1 (tools/library.py:103-113); migrate-raw lists these entries as failed (179-182). Reachable via `tools/scan.py --dpi 7200 --no-shading`, since a shaded full-frame 7200 dpi pass raises ShadingUnavailable.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [CLI-10](../areas/cli-operator-tools.md#cli-operator-tools-cli-10) | cli-operator-tools | high | confirmed | 7200 dpi scan.tif has a host-side stagger realignment baked in that reconstruct/decode_raw never replay; every 7200 dpi entry reads 'decode CHANGED' | rps7200/direct.py:2695-2708, rps7200/direct.py:1638-1663, rps7200/library.py:411-436 |
| [SR-A1](../areas/session-roll.md#session-roll-sr-a1) | session-roll | critical | found-by-verifier | Every 7200 dpi pass filed through ScanSession loses its raw bytes: the column-stagger realignment makes the shape guard reject them | rps7200/direct.py:2695-2701, rps7200/direct.py:1638-1662, rps7200/direct.py:1519-1531 |
| [TP-14](../areas/transport-protocol.md#transport-protocol-tp-14) | transport-protocol | medium | confirmed | 7200 dpi stagger realignment is baked into the 'raw' scan.tif without being recorded, and the session then drops the raw bytes | rps7200/direct.py:2695-2708, rps7200/session.py:2072-2098, rps7200/library.py:411-436 |
| [DDF-07](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-07) | decode-and-debug-filing | medium | partly | At 7200 dpi scan.tif is not the plain decode, nothing records the realignment, and reconstruct flags every such entry | rps7200/direct.py:2695-2708, rps7200/library.py:411-436, rps7200/library.py:464-497 |
| [LIB-09](../areas/library.md#library-lib-09) | library | medium | partly | 7200 dpi stagger realignment is baked into scan.tif, unrecorded, and not reproduced by reconstruct/decode_raw/migrate-raw; sign ignores read direction | rps7200/direct.py:2695-2708, rps7200/direct.py:1637-1664, rps7200/library.py:493-497 |
| [PA-A2](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-a2) | probe-and-analysis-tools | medium | found-by-verifier | The library's re-decode paths skip the 7200 dpi stagger realignment that scan() bakes into scan.tif, so every such entry fails reconstruct, and the entry does not record that it was realigned | rps7200/direct.py:2695-2708, rps7200/library.py:411-436, rps7200/library.py:439-499 |
| [D09](../areas/docs-readme-claude.md#docs-readme-claude-d09) | docs-readme-claude | medium | confirmed | 7200 dpi scan.tif is stagger-realigned (4 rows trimmed), not 'the decode alone'; reconstruct flags it as changed forever | rps7200/direct.py:2695-2708, rps7200/direct.py:1637-1664, rps7200/library.py:411-436 |
| [DOC-07](../areas/docs-plans-todo.md#docs-plans-todo-doc-07) | docs-plans-todo | medium | confirmed | 7200 dpi column realignment is baked into scan.tif, not recorded, and not reproducible by reconstruct or decode_raw | rps7200/direct.py:2695-2708, rps7200/direct.py:1637-1664, rps7200/library.py:411-436 |
| [T09](../areas/tests.md#tests-t09) | tests | medium | confirmed | reconstruct misreports every 7200 dpi entry and lets ScanReadError escape; no test covers either | rps7200/direct.py:2695-2708, rps7200/direct.py:1638-1664, rps7200/direct.py:1582-1592 |
| [PA-14](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-14) | probe-and-analysis-tools | medium | confirmed | dpi_analysis compares uncorrectable, pre-realignment 7200 dpi raw passes against corrected lower-dpi passes | tools/dpi_analysis.py:69-72, tools/dpi_analysis.py:116-117, tools/dpi_analysis.py:131 |
| [MSQ-02](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-02) | measure-scan-quality-skill | medium | partly | dpi_analysis compares corrected lower-dpi passes against an uncorrected 7200 dpi reference, takes its noise floor from an uncorrected pair, and never checks or records correction state | tools/dpi_analysis.py:69-72, tools/dpi_analysis.py:131-159, tools/dpi_analysis.py:175-196 |
| [GUI1-18](../areas/gui-part1.md#gui-part1-gui1-18) | gui-part1 | medium | confirmed | 7200 dpi is offered but always refused by the host on hardware (after metering), while the demo accepts it | tools/gui.py:102-118, tools/gui.py:1748-1749, tools/gui.py:2781-2782 |
