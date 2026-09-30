# P01 -- A single Scan from the window files corrected pixels as the raw scan.tif

**Severity** critical · **Group** A: Library exactness -- the central requirement · **Reported independently by** 12 findings in 10 areas

[Back to the summary](../README.md)

## The problem

`ScanSession._scan` calls `DirectScanner.scan(..., keep_raw=True)`, which returns the
**shading-corrected** image and keeps the uncorrected one in `last_pixels_raw`. `_scan`
then calls `self._file(...)` without `raw_image=`, so `FrameWriter._write` hands the
corrected array to `library.save`, which records `corrections_applied: []` -- the entry
claims to be raw. `_prescan`, the roll path and `tools/scan.py` all pass the raw pixels;
only the Scan job was missed.

Consequences, all silent:

- every Scan-button entry since this path existed holds corrected pixels labelled raw;
- `library.corrected()` sees no `shading` in `corrections_applied`, finds the reference
  and **flat-fields a second time** -- the 1:1 view, Save As, Save all and roll Export are
  all double-corrected;
- `make reconstruct` reports every one of these entries as a changed decode, which is the
  false-alarm failure CLAUDE.md already describes for prescans, and it hides real
  regressions.

The raw bytes (`raw.bin.gz`) in these entries are the true bytes, so the entries are
repairable: `decode_raw(entry)` gives the real raw pixels.

## Fix plan

1. `rps7200/session.py` `_scan`: pass `raw_image=getattr(self._scanner, "last_pixels_raw", None)`
   exactly as `_prescan` does.
2. Make the invariant structural rather than remembered: `FrameWriter._write` (or
   `library.save`) refuses -- or requires `corrections=["shading"]` -- when
   `meta["shading"]` says a correction ran and no `raw_image` came with the job.
3. Test: a session Scan with a fake scanner whose corrected and raw pixels differ; assert
   the filed `scan.tif` equals `decode_raw(entry)` and `corrections_applied == []`.
4. Repair: a `tools/library.py` pass (dry run by default) that finds `gui`-tagged,
   non-prescan entries whose `scan.tif` differs from `decode_raw` and rewrites them from
   the raw bytes -- only where the bytes decode to the same shape and the entry has a
   reference (i.e. the difference is explained by one application of shading).

## Evidence (from [TP-01](../areas/transport-protocol.md#transport-protocol-tp-01))

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:2031-2048`, `rps7200/session.py:1085-1100`, `rps7200/direct.py:2732-2733`, `rps7200/direct.py:2818-2824`

```text
session.py:1441 `image, meta = self._scanner.scan(... shading=job.shading ... keep_raw=True)`; session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` -- no raw_image argument. FrameWriter._write session.py:1090-1091 `entry = library.save(job["image"] if raw_image is None else raw_image, ...)`. scan() returns the corrected array: direct.py:2733 `image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`, while the raw pixels live only in `self.last_pixels_raw` (direct.py:2818). By contrast _prescan passes `raw_image=raw_image` (session.py:1434) and the roll passes `raw_image=rf.raw_image` (session.py:1886).
```

**Failure scenario:** The operator presses Scan (1800 dpi RGBI, shading on). scan.tif holds corrected pixels while raw.bin.gz holds the true bytes. library.corrected() then applies the shading a second time, so Save As, export and the full-resolution view are all double-corrected. `make reconstruct` reports 'decode CHANGED' for every GUI scan, which buries real regressions.

**Second reader's check:** scan() returns the corrected array (`image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`, direct.py:2733/2824) and keeps the uncorrected one only in `self.last_pixels_raw` (2818). ScanSession._scan (session.py:1440-1458) calls `self._file(seq, 0, image, meta, ...)` without raw_image, so `_file` submits raw_image=None and FrameWriter._write files `job["image"] if raw_image is None else raw_image` (session.py:1090-1091), which is the corrected array. library.save records `corrections_applied: list(corrections or [])`, which is [] here. library.corrected() then sees no 'shading' in applied, finds the reference that capture_record supplied, and calls apply_shading again (library.py:374-391): a double correction. tools/scan.py hold() (tools/scan.py:175-192) and ScanSession._prescan (session.py:1415/1434) both carry comments describing exactly this bug and fix it; _scan was missed. No test in tests/ covers the session Scan job's filed pixels (the only last_pixels_raw test hits are test_scan_tool.py and one stub in test_session.py:264).

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-01](../areas/transport-protocol.md#transport-protocol-tp-01) | transport-protocol | critical | confirmed | GUI single Scan files the shading-CORRECTED image into the library as raw pixels | rps7200/session.py:1440-1458, rps7200/session.py:2031-2048, rps7200/session.py:1085-1100 |
| [DDF-05](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-05) | decode-and-debug-filing | critical | confirmed | GUI single scans file the shading-CORRECTED image as scan.tif while the record says raw | rps7200/session.py:1439-1458, rps7200/session.py:1087-1097, rps7200/direct.py:2708-2733 |
| [DP-01](../areas/demo-parity.md#demo-parity-dp-01) | demo-parity | critical | confirmed | GUI single Scan files shading-corrected pixels as raw scan.tif (real scanner and demo); library.corrected() then shades them a second time | rps7200/session.py:1440-1458, rps7200/session.py:1085-1100, rps7200/library.py:373-391 |
| [LIB-01](../areas/library.md#library-lib-01) | library | critical | confirmed | GUI single Scan files the shading-corrected image as raw; Save As / full-res view then correct it a second time | rps7200/session.py:1440-1458, rps7200/session.py:1085-1100, rps7200/library.py:228 |
| [SR-01](../areas/session-roll.md#session-roll-sr-01) | session-roll | critical | confirmed | GUI single Scan files shading-corrected pixels as the raw scan.tif; full-res view and Save As are double-corrected | rps7200/session.py:1440-1458, rps7200/session.py:1085-1100, rps7200/direct.py:2708-2733 |
| [GUI1-01](../areas/gui-part1.md#gui-part1-gui1-01) | gui-part1 | critical | confirmed | A single Scan from the window files shading-corrected pixels as the library's raw scan.tif, so every view and export corrects them twice | tools/gui.py:2047-2061, rps7200/session.py:1440-1458, rps7200/session.py:1089-1091 |
| [GUI2-01](../areas/gui-part2.md#gui-part2-gui2-01) | gui-part2 | critical | confirmed | Scan-button passes are filed with corrected pixels labelled raw; every full-res view, Save As and Save all corrects them a second time | rps7200/session.py:1440-1457, rps7200/session.py:1086-1097, rps7200/library.py:228 |
| [OUT-01](../areas/outputs.md#outputs-out-01) | outputs | critical | confirmed | GUI single scans file shading-corrected pixels as raw; Save As/Save all then double-correct them | rps7200/session.py:1443-1458, rps7200/session.py:1086-1101, rps7200/direct.py:2708 |
| [D01](../areas/docs-readme-claude.md#docs-readme-claude-d01) | docs-readme-claude | critical | confirmed | GUI single Scan files shading-CORRECTED pixels as raw; Save As / 1:1 view then correct them a second time | rps7200/session.py:1440-1458, rps7200/session.py:1085-1100, rps7200/session.py:2111-2120 |
| [T01](../areas/tests.md#tests-t01) | tests | critical | confirmed | GUI Scan job files the shading-corrected image as raw; Save As and 1:1 view then correct it twice; no session test can see it | rps7200/session.py:1441-1458, rps7200/session.py:1085-1098, rps7200/direct.py:2708 |
| [LIB-A1](../areas/library.md#library-lib-a1) | library | high | found-by-verifier | No caller ever passes corrections=; every fallback that files corrected pixels labels them raw | rps7200/library.py:147, rps7200/library.py:228, rps7200/session.py:1088-1092 |
| [D02](../areas/tests.md#tests-d02) | tests | medium | partly | Doc mismatch: 'scan.tif in an entry is the decode alone', and scan_roll's comment that the session always passed raw_image | CLAUDE.md:114-118, rps7200/session.py:1456-1458, rps7200/session.py:1884-1888 |
