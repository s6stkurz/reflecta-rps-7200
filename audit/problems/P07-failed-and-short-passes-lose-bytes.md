# P07 -- A short or failed pass loses its raw bytes -- the case where they matter most

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 6 findings in 5 areas

[Back to the summary](../README.md)

## The problem

The session's layout guard in `_file` compares `raw_layout["lines"]` (lines *declared*
by GET PARAMETERS) with the decoded image height. A pass that ended early (EndOfData, a
stall) decodes fewer lines, so the guard concludes the bytes belong to another pass and
files the image **without** them. Separately, a pass that fails during or after its read
(a decode error, a late check, `ShadingUnavailable` after the read) is never filed at
all, even in debug mode; its bytes are discarded. Anomalous passes are exactly the ones
someone will want to re-decode later.

## Fix plan

1. Compare against `raw_layout["lines_received"]` (already recorded) instead of the
   declared line count, and compare width/channels as today.
2. Wrap decode/late checks so that when bytes were read, they are spooled/filed with
   `tags=["failed"]` and the exception text in the record before the exception
   propagates.

## Evidence (from [SR-04](../areas/session-roll.md#session-roll-sr-04))

**Where:** `rps7200/session.py:2072-2099`, `rps7200/direct.py:1492-1494`, `rps7200/direct.py:1519-1533`, `rps7200/direct.py:1595`

```text
read_planes records `"lines": int(params.lines)` (declared) and `"lines_received": len(blob) // ...` (direct.py:1531-1532), and breaks early on `except EndOfData: ... break` (direct.py:1492-1494). decode_index uses `height = min(len(planes[c]) for c in order)` (direct.py:1595). session._file: `actual = {"lines": shape[0], ...}; disagree = {k: ... if layout.get(k) is not None and layout[k] != actual[k]}` then `capture = dict(capture, raw=None, raw_path=None, raw_layout=None)` (session.py:2076-2099).
```

**Failure scenario:** The scanner signals end of data 12 lines early on a 3600 dpi frame. decode gives height params.lines-12; the session logs 'raw bytes do not describe this image (lines 6590 vs 6578)' and files the entry without raw.bin.gz. The truncation can never be re-examined.

**Second reader's check:** direct.py:1492-1494 breaks on EndOfData and keeps the partial blob. last_raw_layout records `"lines": int(params.lines)` (declared, 1529) beside `lines_received`. decode_index uses `height = min(len(planes[c]) ...)` (1595). session._file compares `layout["lines"]` with `image.shape[0]` (2076-2099) and nulls raw/raw_path/raw_layout when they differ. So the genuine bytes of any short pass are dropped. The same comparison also drops the bytes of every 7200 dpi pass: see the additional finding.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [SR-04](../areas/session-roll.md#session-roll-sr-04) | session-roll | high | confirmed | A short or anomalous pass loses its raw bytes: the shape guard compares declared lines with decoded lines | rps7200/session.py:2072-2099, rps7200/direct.py:1492-1494, rps7200/direct.py:1519-1533 |
| [DDF-10](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-10) | decode-and-debug-filing | medium | confirmed | The session's shape guard drops a pass's own raw bytes on every short read (and at 7200 dpi) | rps7200/session.py:2073-2099, rps7200/direct.py:1501-1503, rps7200/direct.py:1522-1532 |
| [LIB-12](../areas/library.md#library-lib-12) | library | medium | confirmed | The session's layout guard drops the raw bytes of any truncated (short-read) pass | rps7200/session.py:2072-2099, rps7200/direct.py:1501-1503, rps7200/direct.py:1522-1532 |
| [AF1](../areas/tests.md#tests-af1) | tests | medium | found-by-verifier | Session shape guard discards the raw bytes of every short read (EndOfData) and every 7200 dpi pass, because it compares layout.lines (requested) with the decoded or realigned height | rps7200/direct.py:1497-1499, rps7200/direct.py:1518-1531, rps7200/direct.py:1594 |
| [TP-A1](../areas/transport-protocol.md#transport-protocol-tp-a1) | transport-protocol | medium | found-by-verifier | A pass that fails during or after its read is never filed, even in debug mode: its partial raw bytes are discarded | rps7200/direct.py:1466-1516, rps7200/direct.py:2677-2685, rps7200/direct.py:2715-2728 |
| [DDF-09](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-09) | decode-and-debug-filing | medium | confirmed | A pass whose bytes were read but that fails decode or a late check is never filed, losing its raw bytes | rps7200/direct.py:1517-1533, rps7200/direct.py:1582-1593, rps7200/direct.py:2656-2687 |
