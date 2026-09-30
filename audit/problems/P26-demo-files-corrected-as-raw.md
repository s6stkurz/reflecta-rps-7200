# P26 -- Every demo entry is corrected pixels labelled raw

**Severity** high · **Group** D: The demo is not yet the real software with different inputs · **Reported independently by** 12 findings in 10 areas

[Back to the summary](../README.md)

## The problem

`DemoScanner` never publishes `last_pixels_raw`, and its `RollFrame`s carry no
`raw_image`/`raw_prescan`. So everything the demo files through the unchanged session
path is corrected pixels labelled raw, beside the *source* entry's raw bytes, reference
and mask. `library.corrected()` then shades them a second time. Demo prescans served
from a source's `prescan.tif` leave a stale capture record from the previous decode.
This is the rule "the demo may change what the software is fed, not what it does"
broken in the other direction: the demo exercises a raw-vs-corrected branch the real
scanner never takes, so it cannot catch P01 and hides it.

## Fix plan

1. `DemoScanner.scan/prescan` return the corrected image *and* set `last_pixels_raw`
   (the decode before `apply_shading`), exactly like `DirectScanner.scan`.
2. Reset the capture record on every pass; a prescan served from `prescan.tif` has no
   bytes and must say so.
3. Tag demo entries (`demo: true` in the record, which needs P09's pass-through).
4. Test: a demo session files an entry for which `reconstruct` says `same`.

## Evidence (from [DP-02](../areas/demo-parity.md#demo-parity-dp-02))

**Where:** `rps7200/demo.py:257-264`, `rps7200/demo.py:1008-1010`, `rps7200/demo.py:771-779`, `rps7200/session.py:1415`, `rps7200/session.py:1830`, `rps7200/session.py:1886`, `rps7200/demo.py:15-19`, `tests/test_demo.py:69-82`

```text
DemoScanner.__init__ sets `self.last_raw = None`, `self.last_raw_layout = None`, `self.last_scan_meta = None` but never `last_pixels_raw`. It is not a DirectScanner subclass, so the class attribute does not apply. _decode 1009-1010: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)`. The demo roll yields `RollFrame(index=..., position=..., image=image, meta=meta or {}, prescan=prescan, registration=marks, prescan_meta=prescan_meta)` with no raw_image/raw_prescan. The session files `raw_image=getattr(self._scanner, "last_pixels_raw", None)` (1415), `raw_image=rf.raw_prescan` (1830) and `raw_image=rf.raw_image` (1886), all None for the demo.
```

**Failure scenario:** make run-demo on a library whose entries have shading.npz. Prescan, then Scan 1800 dpi. demo/library/<id>/scan.tif is corrected. `tools/library.py reconstruct --root demo/library` reports 'decode CHANGED' for every entry, and the 1:1 view in the demo shows doubly shaded pixels. A walk's hold-loop prescans are filed as np.roll-wrapped synthetic images labelled raw.

**Second reader's check:** DemoScanner is not a DirectScanner subclass and never sets last_pixels_raw (demo.py:257-264). _decode applies shading whenever a reference exists (demo.py:1008-1010). The demo roll yields RollFrame with no raw_image or raw_prescan (demo.py:771-779; the defaults are None per direct.py:397-398). The session therefore files the demo's corrected, resampled or np.roll-shifted pixels as scan.tif, and when the shapes match it also files the source entry's raw.bin.gz and shading.npz (capture from _decode). test_what_it_files_can_be_reconstructed (test_demo.py:69-82) calls library.save directly on a fixture with no reference, so apply_shading never runs, which is why it passes. The module docstring claim at demo.py:15-19 is contradicted when filing goes through the session. Single scans in the demo are also hit by DP-01 independently; prescans and roll frames are demo-specific.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [DP-02](../areas/demo-parity.md#demo-parity-dp-02) | demo-parity | high | confirmed | DemoScanner never exposes pre-correction pixels, so every demo prescan and roll frame is filed with corrected, resampled or shifted pixels labelled raw | rps7200/demo.py:257-264, rps7200/demo.py:1008-1010, rps7200/demo.py:771-779 |
| [DP-03](../areas/demo-parity.md#demo-parity-dp-03) | demo-parity | high | confirmed | Demo capture_record() is stale for prescans served from prescan.tif, and dropping raw keeps a reference and CCD mask that no longer describe the pixels | rps7200/demo.py:1176-1184, rps7200/demo.py:429-433, rps7200/demo.py:1191-1199 |
| [GUI2-02](../areas/gui-part2.md#gui-part2-gui2-02) | gui-part2 | high | confirmed | DemoScanner hands the session corrected pixels plus a shading reference and never any raw pixels, so demo entries are corrected-as-raw and the demo's full-res view and exports double-correct | rps7200/demo.py:1009-1010, rps7200/demo.py:435-442, rps7200/demo.py:771-779 |
| [DX1](../areas/docs-readme-claude.md#docs-readme-claude-dx1) | docs-readme-claude | high | found-by-verifier | In --demo every filed entry is corrected pixels labelled raw, because DemoScanner publishes no last_pixels_raw and its RollFrames carry no raw_image | rps7200/demo.py:176, rps7200/demo.py:955-1024, rps7200/demo.py:1164-1195 |
| [T02](../areas/tests.md#tests-t02) | tests | high | confirmed | Demo files corrected pixels with the source entry's raw bytes and reference; the test that says it reconstructs uses a fixture with no shading reference and bypasses the session | rps7200/demo.py:15-19, rps7200/demo.py:995-1022, rps7200/demo.py:530-597 |
| [TP-A4](../areas/transport-protocol.md#transport-protocol-tp-a4) | transport-protocol | medium | found-by-verifier | DemoScanner has no last_pixels_raw, so demo sessions file corrected pixels labelled raw and the session's raw-vs-corrected path never runs in the demo | rps7200/demo.py:955-1021, rps7200/demo.py:435-443, rps7200/session.py:1415 |
| [DDF-23](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-23) | decode-and-debug-filing | medium | confirmed | Demo entries hold corrected pixels as 'raw', lack device-settings meta, and are not marked as demo | rps7200/demo.py:176, rps7200/demo.py:553-596, rps7200/demo.py:15-19 |
| [SR-12](../areas/session-roll.md#session-roll-sr-12) | session-roll | medium | confirmed | Demo-filed entries hold shading-corrected pixels labelled raw, and demo prescans carry a stale capture record | rps7200/demo.py:15-20, rps7200/demo.py:1008-1010, rps7200/demo.py:455-477 |
| [LIB-18](../areas/library.md#library-lib-18) | library | medium | confirmed | Demo stand-in diverges in what it files: no last_pixels_raw, stale reference/mask on prescans, ignores shading=False, no 7200 dpi refusal, demo flag dropped | rps7200/demo.py:455-477, rps7200/demo.py:1164-1200, rps7200/demo.py:530-597 |
| [OUT-14](../areas/outputs.md#outputs-out-14) | outputs | medium | confirmed | The demo files corrected prescans and roll frames as raw, a branch the real scanner does not take | rps7200/demo.py:176, rps7200/demo.py:455-477, rps7200/demo.py:771-779 |
| [GUI1-A1](../areas/gui-part1.md#gui-part1-gui1-a1) | gui-part1 | medium | found-by-verifier | DemoScanner publishes no raw pixels, so every demo entry files corrected pixels beside a reference and is corrected twice when viewed. The demo also hides GUI1-01 | rps7200/demo.py:1006-1010, rps7200/demo.py:525-597, rps7200/demo.py:771-779 |
| [D01](../areas/tests.md#tests-d01) | tests | medium | confirmed | Doc mismatch: demo.py says an entry it files reconstructs like any other | rps7200/demo.py:15-19, rps7200/demo.py:1009-1010, tests/test_demo.py:69-82 |
