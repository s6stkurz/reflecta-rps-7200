# P03 -- The debug spool is deleted on a failed save and lost on a crash

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 11 findings in 8 areas

[Back to the summary](../README.md)

## The problem

`_debug_flush` unlinks each spooled `NNN-image.npy` / `NNN-raw.bin` in `finally`, whether
`library.save` succeeded or raised, then `rmtree`s the spool. A full disk, a bad
`RPS7200_DEBUG_ROOT` or a TIFF error during the flush therefore destroys the only copy
and leaves a log line. Separately, everything except the two big files -- meta,
layout, shading reference, CCD mask, capture time -- lives only in memory until
`close()`; a killed process (not an exception) leaves an unrecoverable spool in the
system temp directory, which on many Linux systems is RAM-backed `tmpfs`. The entry's
id and `created` are the flush time, not the capture time.

## Fix plan

1. Unlink a spooled item only after its `library.save` returned.
2. Write a small `NNN-meta.json` (meta, layout, capture time) and `NNN-reference.npz`
   / `NNN-mask.bin` beside each spooled pass as it is captured, so a spool is
   self-describing and `tools/library.py file-spool <dir>` can file it later.
3. Spool under the library root (`library/.spool/`), not `tempfile`, so it survives a
   reboot and is on the same filesystem as the entries.
4. Record the capture time in the entry and use it for the id.

## Evidence (from [TP-02](../areas/transport-protocol.md#transport-protocol-tp-02))

**Where:** `rps7200/direct.py:713-753`

```text
direct.py:715-753: `try: image = np.load(item["image_path"], mmap_mode="r"); entry = library.save(...)` `except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")` `finally: image = None ... for key in ("image_path", "raw_path"): ... Path(path).unlink(missing_ok=True)`
```

**Failure scenario:** The disk is full, the library path is unwritable (RPS7200_DEBUG_ROOT typo) or tiff.write raises during the flush after a 38-frame probe run. Each frame's spooled raw bytes and pixels are deleted right after its save fails, and only the 'could not file scan' log lines remain.

**Second reader's check:** _debug_flush (direct.py:713-753) unlinks `image_path` and `raw_path` in `finally`, whether library.save succeeded or raised in the `except Exception` arm, and then `shutil.rmtree(self._debug_spool, ignore_errors=True)` (757-763) removes the whole spool. A failed save leaves only the log line 'debug: could not file scan n'. That is permanent loss of the raw bytes and pixels. Rated high rather than critical because it needs a prior filing failure (disk full, bad RPS7200_DEBUG_ROOT, TIFF error), but that is exactly the situation this path exists to survive.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-02](../areas/transport-protocol.md#transport-protocol-tp-02) | transport-protocol | high | confirmed | Debug flush deletes the only copy of a scan when filing it fails | rps7200/direct.py:713-753 |
| [DDF-02](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-02) | decode-and-debug-filing | high | confirmed | A filing failure during _debug_flush deletes the only copy of the pass (spool unlinked in finally) | rps7200/direct.py:713-753, rps7200/direct.py:694-699, rps7200/library.py:176-211 |
| [OUT-03](../areas/outputs.md#outputs-out-03) | outputs | high | confirmed | Debug flush deletes the spooled raw data even when library.save (tiff.write) failed | rps7200/direct.py:694-760, rps7200/library.py:179, rps7200/tiff.py:164-168 |
| [DOC-A1](../areas/docs-plans-todo.md#docs-plans-todo-doc-a1) | docs-plans-todo | high | found-by-verifier | Debug filing deletes a scan's only spooled copy (raw bytes and pixels) even when library.save failed | rps7200/direct.py:713-752 |
| [T05](../areas/tests.md#tests-t05) | tests | high | confirmed | _debug_flush deletes the spooled pixels and raw bytes even when library.save failed; the test only asserts that nothing raised | rps7200/direct.py:694-765, tests/test_roll.py:793-803 |
| [RXV-1](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rxv-1) | resource-exhaustion-crash-recovery-time | high | found-by-verifier | Debug filing deletes the spooled raw bytes and pixels even when library.save failed, so a full or unwritable library destroys the only copy | rps7200/direct.py:711-752, rps7200/direct.py:754-764 |
| [DDF-03](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-03) | decode-and-debug-filing | high | confirmed | If close() never runs, every pending debug entry is lost and the spool is orphaned without its meta | rps7200/direct.py:470-474, rps7200/direct.py:666-692, rps7200/direct.py:777-788 |
| [PA-04](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-04) | probe-and-analysis-tools | high | confirmed | A probe killed rather than interrupted files nothing: meta, reference, mask and layout exist only in RAM until close() | rps7200/direct.py:650-692, rps7200/direct.py:777-788, tools/fast_ir_probe.py:4 |
| [LIB-23](../areas/library.md#library-lib-23) | library | medium | confirmed | Debug spool is unrecoverable if the process dies before close(), and lives in system temp (possibly tmpfs/RAM) | rps7200/direct.py:650-692, rps7200/direct.py:777-788 |
| [DDF-15](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-15) | decode-and-debug-filing | medium | confirmed | The debug spool lives in system temp (often tmpfs/RAM) and grows unbounded until close(); spool failures are swallowed silently | rps7200/direct.py:666-692, rps7200/direct.py:509-519, rps7200/direct.py:740-746 |
| [DDF-14](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-14) | decode-and-debug-filing | medium | confirmed | The capture time is recorded at spool time and then discarded; debug entries are named and dated by flush time | rps7200/direct.py:672, rps7200/direct.py:719-729, rps7200/library.py:166-168 |
