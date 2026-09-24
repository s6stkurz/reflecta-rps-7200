# P02 -- Debug filing pairs a pass with the previous pass's raw bytes (or none)

**Severity** critical · **Group** A: Library exactness -- the central requirement · **Reported independently by** 7 findings in 6 areas

[Back to the summary](../README.md)

## The problem

`read_planes` stores `last_raw`/`last_raw_layout` only when `keep_raw=True`, and never
clears them. `capture_record()` returns whatever is there, and `_debug_capture` spools it
beside *this* pass's pixels. So under `RPS7200_DEBUG=1` any pass run with the default
`keep_raw=False` -- a direct `scan()`/`prescan()` call in an ad-hoc script or probe tool,
`byte14_probe`'s last pass, hold passes run outside a roll -- is filed with **another
pass's bytes** or with no bytes at all. (Metering probes are safe: they pass
`keep_raw=True`.) The debug path has no shape
guard (the session's `_file` has one; `_debug_capture` does not), and `library.save`
accepts bytes that cannot decode to the image.

This is the one failure the library exists to make impossible: an entry whose raw bytes
decode to a different photograph.

## Fix plan

1. `rps7200/direct.py`: clear `last_raw`/`last_raw_layout` at the start of every pass
   (`read_planes`/`read_lines`), so a stale value cannot exist.
2. Debug capture should always keep the pass's own bytes: in debug mode force
   `keep_raw=True` for every pass (memory is not the constraint -- the spool is on disk).
3. `library.save`: when `raw`/`raw_path` is given, decode it and refuse (or file without
   bytes and say so in the record) if shape or pixels disagree with `image`.
4. Test: two passes, the second with `keep_raw=False`, debug on; assert the second entry
   has its own bytes and `reconstruct` says `same`.

## Evidence (from [TP-03](../areas/transport-protocol.md#transport-protocol-tp-03))

**Where:** `rps7200/direct.py:1518-1532`, `rps7200/direct.py:631-646`, `rps7200/direct.py:672-688`, `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2437`, `rps7200/library.py:186-211`, `tools/byte14_probe.py:176-186`

```text
last_raw is assigned only in read_planes when `if keep_raw: self.last_raw = blob; self.last_raw_layout = {...}` (direct.py:1518-1532) and is never cleared by scan(). capture_record returns `"raw": self.last_raw, "raw_layout": self.last_raw_layout` (644-645), and _debug_capture spools `raw = record.get("raw")` (679-684). auto_exposure probes use `keep_raw=True` (2091), while scan() defaults to `keep_raw: bool = False` (2437). library.save writes whatever raw it is handed with no layout-vs-image check (library.py:186-211). byte14_probe's `finally` pass uses `keep_raw=False` right after keep_raw=True ladder passes.
```

**Failure scenario:** An ad-hoc script under RPS7200_DEBUG=1 runs `s.scan(resolution=1800, auto_exposure=True)`. The entry's raw.bin.gz is the 300 dpi probe's bytes while scan.tif is the 1800 dpi pass. reconstruct reports 'decode CHANGED: now (≈287,...), stored (...)', and the real pass's bytes were never kept. The byte14_probe 'final default pass' is filed with the last ladder pass's bytes.

**Second reader's check:** last_raw/last_raw_layout are assigned only under `if keep_raw:` in read_planes (direct.py:1518-1532) and are never cleared at the start of scan(). scan() defaults to keep_raw=False (2437). auto_exposure probe passes use keep_raw=True (2091), so `s.scan(resolution=1800, auto_exposure=True)` with keep_raw left at its default leaves the 300 dpi probe's bytes in last_raw. _debug_capture then spools `record.get("raw")` from capture_record (direct.py:672-684), and library.save writes whatever raw/raw_layout it is given, with no check against the image shape (library.py:186-211). Other reachable cases: verify_protocol's shot() (keep_raw not passed), byte14_probe:184 (`keep_raw=False`), and tools/scan.py with --no-library (keep_raw=args.library is not None). Only ScanSession._file has a shape guard (session.py:2072-2098), and that guard cannot tell two same-shaped passes apart.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-03](../areas/transport-protocol.md#transport-protocol-tp-03) | transport-protocol | high | confirmed | Debug filing and capture_record attach a STALE last_raw from an earlier pass (or none) to a scan taken with keep_raw=False | rps7200/direct.py:1518-1532, rps7200/direct.py:631-646, rps7200/direct.py:672-688 |
| [DDF-01](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-01) | decode-and-debug-filing | critical | confirmed | Debug filing files a pass with no raw bytes or with a previous pass's raw bytes (stale last_raw) | rps7200/direct.py:1518-1532, rps7200/direct.py:2653, rps7200/direct.py:631-646 |
| [LIB-02](../areas/library.md#library-lib-02) | library | high | confirmed | Debug filing pairs a pass with the PREVIOUS pass's raw bytes (or none) whenever keep_raw=False | rps7200/direct.py:650-692, rps7200/direct.py:631-646, rps7200/direct.py:1518-1532 |
| [PA-02](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-02) | probe-and-analysis-tools | high | confirmed | A keep_raw=False pass after a keep_raw=True pass is filed with the previous pass's raw bytes (byte14_probe final pass) | rps7200/direct.py:1518-1532, rps7200/direct.py:641-646, rps7200/direct.py:673-684 |
| [D02](../areas/docs-readme-claude.md#docs-readme-claude-d02) | docs-readme-claude | high | confirmed | Debug filing attaches NO raw bytes, or the PREVIOUS pass's raw bytes, whenever scan() runs with the default keep_raw=False | rps7200/direct.py:1518-1532, rps7200/direct.py:631-646, rps7200/direct.py:673-688 |
| [T04](../areas/tests.md#tests-t04) | tests | high | confirmed | Debug filing attaches a stale last_raw from an earlier pass when a pass runs with keep_raw=False; no guard, and the test even spools mismatched bytes | rps7200/direct.py:650-693, rps7200/direct.py:631-646, rps7200/direct.py:1518-1531 |
| [T13](../areas/tests.md#tests-t13) | tests | medium | confirmed | Raw bytes that cannot decode to the image are accepted by library.save and the session guard; several tests encode this and none reconstruct what they filed | rps7200/session.py:2069-2093, rps7200/library.py:186-211, tests/test_session.py:108-116 |
