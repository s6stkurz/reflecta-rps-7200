# The test suite

Area key `tests`. 28 findings: 1 critical, 4 high, 14 medium, 9 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Audit of tests/*.py and tests/conftest.py against the code (read only; no scanner; nothing run). The suite is large (about 20k lines, 38 modules) and good at the transport's command bytes, roll numbering, seek/rewind, hold-loop decisions, TIFF round trips and tool argument wiring. It is weakest at the owner's central requirement: that a library entry holds the exact bytes and raw pixels of the pass. No test runs DirectScanner.scan() or prescan() to completion. Almost every double overrides the passes, and the raw/corrected split is pinned by inspect.getsource string matching (test_roll.py:723-748). The session doubles return one array for both "raw" and "corrected" and hand every pass the same undecodable 64-byte blob. So ScanSession._scan, the path behind the window's Scan button, can file the shading-corrected image as raw without any test noticing. It does exactly that, and Save As and the 1:1 view then flat-field it a second time (critical). The same blind spot hides three things. The demo files corrected pixels beside borrowed raw bytes: its "reconstructs" test uses a fixture with no shading reference and bypasses the session. The FrameWriter writes the operator's copy before the library entry, and its failure tests all use library=None. Debug filing attaches a stale last_raw from an earlier pass, and a failed flush deletes the spool; the test for that failure only asserts that nothing raised. Other gaps: nothing tests atomicity (library.save, roll.json) or verify's blindness to partial entries. Nothing tests reconstruct for 7200 dpi or for ScanReadError. Roll-level filing of raw_image, raw_prescan or prescan_meta is untested, and frame entries actually store the corrected prescan. No test checks demo parity beyond constant identity. Roll prescans are filed with film "negative" whatever the film, and FakeRoll.prescan cannot even accept a film argument. GUI user errors are not exercised as behaviour: delete, aim-click while busy, submit() clearing a pending Stop, and a same-name re-walk. Every test that uses real scanner data skips outside Stefan's machine. There are about 124 source-text assertions, and several assertions are tautological or depend on the environment.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [T01](#tests-t01) | critical | data-integrity | GUI Scan job files the shading-corrected image as raw; Save As and 1:1 view then correct it twice; no session test can see it | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [T02](#tests-t02) | high | demo-divergence | Demo files corrected pixels with the source entry's raw bytes and reference; the test that says it reconstructs uses a fixture with no shading reference and bypasses the session | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [T03](#tests-t03) | high | data-integrity | FrameWriter writes the operator's copy before the library entry, so a failing output path loses the raw bytes; the failure tests use library=None | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [T04](#tests-t04) | high | data-integrity | Debug filing attaches a stale last_raw from an earlier pass when a pass runs with keep_raw=False; no guard, and the test even spools mismatched bytes | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [T05](#tests-t05) | high | data-integrity | _debug_flush deletes the spooled pixels and raw bytes even when library.save failed; the test only asserts that nothing raised | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [T06](#tests-t06) | medium | test-gap | DirectScanner.scan()/prescan() never run to completion in any test; the raw/corrected split and meta content are pinned only by source text | -- |
| [T07](#tests-t07) | medium | data-integrity | Roll frame entries store the corrected prescan as prescan.tif; the session roll path is never tested with raw_image, raw_prescan or prescan_meta | [P08](../problems/P08-passes-never-filed.md) |
| [T08](#tests-t08) | medium | data-integrity | library.save is not atomic, verify cannot see partial entries, and prescan/shading/mask have no checksums; none of it is tested | [P10](../problems/P10-non-atomic-writes.md) |
| [T09](#tests-t09) | medium | bug | reconstruct misreports every 7200 dpi entry and lets ScanReadError escape; no test covers either | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [T10](#tests-t10) | medium | library-completeness | Entries drop the device INQUIRY, the calibration bytes and MODE SELECT choices; the 'keeps everything needed' test checks none of them | [P09](../problems/P09-record-missing-parameters.md) |
| [T12](#tests-t12) | medium | demo-divergence | Roll prescans are filed with film 'negative' whatever the roll's film; the demo passes the film, and FakeRoll.prescan cannot accept one | [P09](../problems/P09-record-missing-parameters.md) |
| [T13](#tests-t13) | medium | test-gap | Raw bytes that cannot decode to the image are accepted by library.save and the session guard; several tests encode this and none reconstruct what they filed | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [T14](#tests-t14) | medium | concurrency | submit() clears a pending Stop, and the aim-click, fine-move and frame-move handlers submit while busy; untested | [P23](../problems/P23-busy-guards-and-stop.md) |
| [T15](#tests-t15) | medium | user-error | A second walk under the same roll name (the default is today's date) overwrites survey.json and prescanNN.tif | [P18](../problems/P18-roll-folder-identity.md) |
| [T17](#tests-t17) | medium | demo-divergence | The demo's scan and scan_roll swallow or diverge on parameters, refusals and failure handling; parity tests check only constants | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [T18](#tests-t18) | medium | data-integrity | tools/scan_roll.py --dry-run files nothing in the library and forces debug off; the test never checks library entries | [P08](../problems/P08-passes-never-filed.md) |
| [D01](#tests-d01) | medium | doc-mismatch | Doc mismatch: demo.py says an entry it files reconstructs like any other | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [D02](#tests-d02) | medium | doc-mismatch | Doc mismatch: 'scan.tif in an entry is the decode alone', and scan_roll's comment that the session always passed raw_image | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [AF1](#tests-af1) | medium | data-integrity | Session shape guard discards the raw bytes of every short read (EndOfData) and every 7200 dpi pass, because it compares layout.lines (requested) with the decoded or realigned height | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [T11](#tests-t11) | low | test-gap | test_the_scan_block_carries_everything_scan_records only checks keys it typed itself; roll_index, roll_position and the demo marker are silently dropped | -- |
| [T16](#tests-t16) | low | data-integrity | roll.json is rewritten in place, and a corrupt manifest is read as empty on resume; untested | -- |
| [T19](#tests-t19) | low | test-gap | Tautological and environment-sensitive assertions | -- |
| [T20](#tests-t20) | low | test-gap | Every test on real scanner data skips outside the owner's machine, and CI never runs them | -- |
| [T21](#tests-t21) | low | design | About 124 source-text assertions stand in for behaviour, and GUI user-error paths have no behavioural tests | -- |
| [T22](#tests-t22) | low | user-error | Delete dialog: answering 'No' permanently removes the library entry and its raw bytes; untested | -- |
| [D03](#tests-d03) | low | doc-mismatch | Doc mismatch: 'Every library entry keeps its raw bytes, its shading reference and its CCD mask' and host-side testing 'against stored bytes' | -- |
| [D04](#tests-d04) | low | doc-mismatch | Doc mismatch: test_roll says the uncorrected pixels reach the library, but only the debug-filing path is pinned | -- |
| [AF2](#tests-af2) | low | test-gap | Every conftest scanner double returns a capture_record with raw=None, so no session or roll test ever files real INDEX bytes | -- |

## Findings in full

<a id="tests-t01"></a>

### T01 -- GUI Scan job files the shading-corrected image as raw; Save As and 1:1 view then correct it twice; no session test can see it

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1441-1458`, `rps7200/session.py:1085-1098`, `rps7200/direct.py:2708`, `rps7200/direct.py:2735`, `rps7200/direct.py:2818`, `rps7200/library.py:228`, `rps7200/library.py:385-391`, `tools/gui.py:2054-2062`, `tools/gui.py:3864`, `tools/gui.py:4110`, `tests/test_session.py:128-149`, `tests/test_session.py:219-236`

The window's Scan button submits a Scan job. ScanSession._scan files the corrected image returned by scan() as scan.tif. The record then says corrections_applied=[] and stores shading.npz beside it. apply_shading is not idempotent ((x-dark)*gain), so every consumer that goes through library.corrected() flat-fields these pixels a second time. The suite cannot detect this because the session doubles make raw and corrected the same array.

**Evidence (from the code):**

```text
session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=wants_mono(job.mono, job.film), mono_channel=job.mono_channel)` passes no `raw_image=`. FrameWriter._write:1089-1091 `raw_image = job.get("raw_image")` / `entry = library.save(job["image"] if raw_image is None else raw_image, job["meta"], ...)`. DirectScanner.scan returns the corrected array: `raw_pixels = image` ... `image, shading_report = apply_shading(image, self._shading, ccd_mask)` ... `return image, meta`, with meta["shading"]=report and no `corrections=`. library.corrected:388 `image, report = apply_shading(image, record["reference"], record["ccd_mask"])`. gui.py:3864 `full, entry_record = library.corrected(result.entry)` (Save As) and gui.py:4110 (full-res view). test_session FakeScanner.scan returns one `picture(...)` and never sets `last_pixels_raw`. Its tests only check `library.read_raw(entry) == b"\x00\x01" * 32`. The identical bug was fixed and tested for tools/scan.py (test_scan_tool.py:319-344, FakeCorrectingScanner with RAW_LEVEL 111 and CORRECTED_LEVEL 222), but no equivalent test exists for ScanSession.
```

**Failure scenario:** The operator calibrates, then presses Scan at 1800 dpi. The out_dir copy is correct (corrected once). The library entry holds corrected pixels labelled raw. Save As and the 1:1 view apply shading again and report status 'applied', so the exported TIFF has inverse column striping and shifted levels, with no warning. `tools/library.py reconstruct` reports every GUI single scan as 'decode CHANGED', the same false-alarm pattern CLAUDE.md warns about. A later correction improvement can never be applied, because the raw pixels are gone.

**Fix:** In _scan, pass raw_image=getattr(self._scanner, 'last_pixels_raw', None), read immediately after scan(). Add a session test whose double returns CORRECTED_LEVEL, sets last_pixels_raw to RAW_LEVEL and hands back real INDEX bytes. Assert that library.load()[0] is raw, that library.corrected() equals a single correction, and that library.reconstruct() says 'identical'.

<details><summary>Second reader's check</summary>

session.py:1441-1458: _scan calls self._scanner.scan(..., keep_raw=True) and then `self._file(seq, 0, image, meta, ...)` with no raw_image=. The only other place raw pixels come in, FrameWriter._write:1087-1091, falls back to job["image"] when raw_image is None. DirectScanner.scan returns the corrected array (direct.py:2708 `raw_pixels = image`, 2735 `image, shading_report = apply_shading(...)`) and keeps the raw one only in last_pixels_raw (2818). library.save records corrections_applied=[] (corrections not passed), and the capture_record reference goes to shading.npz. So library.corrected (library.py:388) and reconstruct (which only re-shades when 'shading' is in corrections_applied) treat the entry as raw. GUI on_scan (gui.py:2054) submits exactly this Scan job, and gui.py:3864/4110 use library.corrected. The session test FakeScanner.scan (test_session.py:129-149) returns a single array and never sets last_pixels_raw, so no test can tell the two apart. Compare _prescan (1415), _roll (1886) and tools/scan.py:190, which do route the raw pixels.

</details>

<a id="tests-t02"></a>

### T02 -- Demo files corrected pixels with the source entry's raw bytes and reference; the test that says it reconstructs uses a fixture with no shading reference and bypasses the session

**Severity** high · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:15-19`, `rps7200/demo.py:995-1022`, `rps7200/demo.py:530-597`, `rps7200/demo.py:771-779`, `tests/test_demo.py:22-51`, `tests/test_demo.py:69-82`, `tests/test_demo.py:742-756`

**Doc claim:** rps7200/demo.py:18-19 'An entry the demo files reconstructs like any other.'

Every real library entry has shading.npz. So with --demo, every scan, frame and roll prescan filed through ScanSession stores corrected pixels next to raw bytes that decode to the uncorrected picture, plus the reference that will be applied again. Contrary to demo.py's docstring, such an entry does not reconstruct. The test passes only because its fixture has nothing to correct with, and it re-does the filing by hand instead of measuring what the session filed. The demo therefore also cannot expose T01.

**Evidence (from the code):**

```text
demo._decode:1009 `if reference is not None and not (record.get("calibration") or {}).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)`, then it returns capture `{"reference": reference, "ccd_mask": mask, "raw": raw, "raw_layout": layout}`. DemoScanner is not a DirectScanner subclass and never sets last_pixels_raw. Its RollFrame(...) at 771-779 carries no raw_image or raw_prescan. The test fixture `entry()` calls `library.save(image, meta, ..., raw=bytes(raw), raw_layout=...)` with no `reference=`. test_what_it_files_can_be_reconstructed then calls `library.save(image, meta, root=tmp_path / "out", film=FilmNotes(), **capture)` directly instead of going through ScanSession.
```

**Failure scenario:** `make run-demo` over the real library: take a Scan, then run `tools/library.py reconstruct --root demo/library`. The result is 'decode CHANGED: ~99% of samples differ', and Save As from the demo double-corrects. With shading=False the demo still returns corrected pixels and omits shading_skipped, where the hardware returns raw pixels and sets SHADING_SKIPPED_EXPLICIT.

**Fix:** Keep the uncorrected decode in DemoScanner.last_pixels_raw and in RollFrame.raw_image/raw_prescan, and honour shading=False. Change the fixture to include a non-flat ShadingReference, drive the filing through ScanSession, and assert reconstruct 'identical' and corrected()==apply_shading(raw).

<details><summary>Second reader's check</summary>

demo._decode:1009-1010 applies the entry's reference to the decode, and 1019-1022 returns a capture holding that reference, the mask and the uncorrected raw bytes. DemoScanner defines no last_pixels_raw (grep finds none in demo.py), so session._prescan's getattr gives None and the corrected pixels are filed. _scan never passes raw anyway, and the demo's RollFrame (771-779) carries no raw_image or raw_prescan. For a scan, meta['shading'] is set only when shading=True, but the pixels are corrected regardless, and shading_skipped is never set. The test fixture entry() saves with no reference=, and test_what_it_files_can_be_reconstructed files the result by hand with library.save instead of going through ScanSession, so it passes for the wrong reason.

</details>

<a id="tests-t03"></a>

### T03 -- FrameWriter writes the operator's copy before the library entry, so a failing output path loses the raw bytes; the failure tests use library=None

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1060-1103`, `rps7200/session.py:1046-1058`, `tests/test_session.py:727-744`, `tests/test_roll_writer.py:67-83`

The session's own docstring calls the library entry 'the record' and the out_dir file 'the file they actually wanted'. Yet any exception while writing a delivered copy (out_dir, rolls/frameNN.tif, JPEG/DNG) skips library.save, and the job, including its in-memory raw bytes, is dropped. Every writer-failure test disables the library, so the test cannot see that ordering.

**Evidence (from the code):**

```text
_write: `for path in job.get("paths") or (): Path(path).parent.mkdir(...); note = export.write(str(path), delivered, ...)` followed by `entry = None; if job["library"]: ... entry = library.save(...)`. _run: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")`. Tests: `writer.submit(number=2, paths=[blocker / "no.tif"], ..., library=None, ...)` and `job(2, blocker / "frame02.tif")` with the default library=None.
```

**Failure scenario:** The output folder is a USB stick or network share that disconnects, or a disk fills, during a 38-frame roll. From that frame on, export.write raises, every frame's raw bytes, reference and mask are discarded, and the log says only 'picture N could not be filed'. The scanner time is spent and the library has nothing.

**Fix:** Call library.save first, then write each delivered path in its own try, collecting notes. Add a test with library set and one unwritable path, asserting that the entry exists and reconstructs 'identical'.

<details><summary>Second reader's check</summary>

FrameWriter._write (session.py:1074-1098) loops over job['paths'] calling mkdir and export.write before `if job["library"]: ... library.save(...)`. Any exception propagates to _run:1052, which appends to errors and drops the job, so library.save is never reached. Both writer-failure tests (test_session.py:727-744 and test_roll_writer.py:67-83 via job() with default library=None) disable the library, so the ordering is untested. The out_dir copy and rolls/frameNN.tif both come before filing.

</details>

<a id="tests-t04"></a>

### T04 -- Debug filing attaches a stale last_raw from an earlier pass when a pass runs with keep_raw=False; no guard, and the test even spools mismatched bytes

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:650-693`, `rps7200/direct.py:631-646`, `rps7200/direct.py:1518-1531`, `rps7200/direct.py:2083-2091`, `rps7200/direct.py:2437`, `tests/test_roll.py:812-833`, `tests/test_roll.py:1431-1441`

ScanSession._file has a layout-versus-shape guard for exactly this failure, but the automatic debug filing does not. CLAUDE.md requires Claude to run every ad-hoc script with RPS7200_DEBUG=1, and such scripts rarely pass keep_raw=True.

**Evidence (from the code):**

```text
_debug_capture:673 `record = self.capture_record()`, then 679 `raw = record.get("raw")` / `if raw is not None: raw_path.write_bytes(raw)`, with no comparison to `image`. read_planes:1518 `if keep_raw: self.last_raw = blob`; nothing clears it otherwise. scan() defaults to `keep_raw: bool = False`, while the auto_exposure probes use `keep_raw=True`. Test: `s.last_raw = b"\x00" * 100_000; s.last_raw_layout = {"width": 400, "lines": 300, "channels": 4, "bytes_per_line": 3200}; s._debug_capture(big, {"resolution_dpi": 300})` asserts only that it was spooled. The docstring at test_roll.py:1432 admits: 'last_raw is only written when keep_raw is set and is never cleared'.
```

**Failure scenario:** RPS7200_DEBUG=1 with `s.scan(resolution=1800, auto_exposure=True)`: the metering probes leave the last 300 dpi probe's bytes in last_raw. The 1800 dpi scan's entry is filed with that probe's raw.bin.gz and layout, and the scan's own bytes were never kept. Likewise `s.prescan()` after any keep_raw pass. The entry decodes to a different photograph, which is the one failure the library exists to prevent. It is only noticed if someone later runs reconstruct.

**Fix:** Clear last_raw/last_raw_layout at the start of every pass (or stamp them with a pass id), and apply the _file layout/shape guard inside _debug_capture. Test it: a keep_raw pass, then a keep_raw=False pass with debug on; the second entry must have no raw (or its own raw).

<details><summary>Second reader's check</summary>

read_planes sets last_raw/last_raw_layout only `if keep_raw:` (direct.py:1518-1531), and nothing clears them per pass (only __init__ at 490-491). scan() defaults keep_raw=False (2437), while the auto_exposure probes call self.scan(..., keep_raw=True) (2083-2091). So DirectScanner(debug=True).scan(resolution=1800, auto_exposure=True) runs _debug_capture(raw_pixels, meta) (2812) with capture_record() returning the last 300 dpi probe's bytes and layout. _debug_capture (673-685) spools them with no shape or layout check, and library.save does no check either. The session-level guard in _file does not apply to debug filing. test_roll.py:1431-1433's docstring acknowledges that last_raw is never cleared.

</details>

<a id="tests-t05"></a>

### T05 -- _debug_flush deletes the spooled pixels and raw bytes even when library.save failed; the test only asserts that nothing raised

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:694-765`, `tests/test_roll.py:793-803`

Losing a record is traded for keeping the session, but the spool is the only copy, and it is removed whether or not filing succeeded. The test encodes 'must not raise' and would pass whether the data were deleted or kept.

**Evidence (from the code):**

```text
`except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")`, then `finally: ... for key in ("image_path", "raw_path"): ... Path(path).unlink(missing_ok=True)`, and after the loop `shutil.rmtree(self._debug_spool, ignore_errors=True)`. Test: RPS7200_DEBUG_ROOT is set under a regular file, then `s.close()  # must not raise` and `assert s._debug_pending == []`. Nothing checks that the spool survives.
```

**Failure scenario:** The disk is full, the library path is on an unmounted drive, or permissions are wrong at close(). Every scan of the session (for example an hour of probe passes) is deleted from $TMP/rps7200-debug-* after one log line per scan.

**Fix:** Unlink only after a successful save. On failure keep the files, write a small manifest so they can be re-filed, and log the spool path. The test should assert the spool files still exist after a failed flush.

<details><summary>Second reader's check</summary>

_debug_flush (direct.py:717-765): the except at 730-731 only logs, and the finally at 732-754 unconditionally unlinks image_path and raw_path. The spool dir is then rmtree'd (757). A failed library.save therefore deletes the only copy of the raw bytes, and the uncorrected pixels, of every scan in the session. test_a_filing_failure_never_breaks_the_session (test_roll.py:793-803) asserts only that close() does not raise and that _debug_pending == [].

</details>

<a id="tests-t06"></a>

### T06 -- DirectScanner.scan()/prescan() never run to completion in any test; the raw/corrected split and meta content are pinned only by source text

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_roll.py:723-748`, `tests/test_roll.py:1444-1453`, `tests/test_fast_infrared.py:184-202`, `tests/test_scanner_api.py:222-236`, `tests/conftest.py:70-107`, `rps7200/direct.py:2423-2820`

Several things are never executed by a test: the path from bytes read to (corrected image, meta, last_pixels_raw, last_raw, debug capture), the 7200 dpi realign before raw_pixels, the meta keys library.save keeps, and keep_raw handling. conftest.FakeTransport can script replies, but no test scripts GET PARAMETERS, CCD mask, gain/offset and READ data.

**Evidence (from the code):**

```text
test_roll: `source = inspect.getsource(DirectScanner.scan)`; `assert "raw_pixels = image" in source`; `assert "self._debug_capture(raw_pixels, meta)" in source`. test_fast_infrared docstring: 'The fake carries the pass as far as MODE SELECT and then fails on the image read', with `except Exception: pass`. FakeRoll, FakeScanner (test_metering and test_session), StripScanner, ScannerOnStrip, FakeBracketScanner and FakeCorrectingScanner all override scan/prescan.
```

**Failure scenario:** A refactor that realigns after `raw_pixels = image`, aliases last_pixels_raw to the corrected array, stops recording read_direction or filter_offsets, or breaks the scan-to-library hand-off keeps the suite green, as long as the literal strings remain.

**Fix:** Add scripted-transport end-to-end tests: an INDEX blob from direction.encode_index (top-down and bottom-up), mask, params and gain/offset replies. Assert image == apply_shading(decode), that last_pixels_raw equals the decode, that last_raw decodes to it, the full meta key set, that debug filing reconstructs 'identical', and cover a 7200 dpi shading=False case.

<details><summary>Second reader's check</summary>

No test drives DirectScanner.scan() to a return. test_fast_infrared.py:184-202 and test_scanner_api.py:222-236 wrap it in `except Exception: pass`. test_metering.py:559-570 expects it to fail after the guard. Every other caller uses a double that overrides scan/prescan. test_roll.py:723-748 and 1444-1453 check source text only. read_planes is exercised directly in test_decode.py:348-361, but the hand-off from read_planes through realign and apply_shading to last_pixels_raw, meta and _debug_capture is not.

</details>

<a id="tests-t07"></a>

### T07 -- Roll frame entries store the corrected prescan as prescan.tif; the session roll path is never tested with raw_image, raw_prescan or prescan_meta

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:1874-1891`, `tools/scan_roll.py:549-566`, `rps7200/library.py:180-181`, `rps7200/library.py:297-303`, `tests/conftest.py:218-243`, `tests/test_session.py:151-186`, `tests/test_scan_roll_tool.py:139-153`

Dry-run prescan entries hold raw pixels, while frame entries hold a flat-fielded prescan.tif with no bytes and no label saying it is corrected. That violates the rule that the library holds raw pixels. Because no session-level test feeds distinct raw and corrected arrays, neither this nor a regression in raw_image routing would show.

**Evidence (from the code):**

```text
session.py:1884-1888: `self._file(seq, number, rf.image, frame_meta, notes, ..., raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta, ...)`. rf.raw_prescan exists but is not passed. scan_roll.py: `prescan=frame.prescan`. library.save: `tiff.write(str(path / "prescan.tif"), prescan)`, and the record's prescan block has only file, read_direction and carriage_state. A grep finds no RollFrame with raw_image, raw_prescan or prescan_meta in test_session or test_gui. The scan_roll tool test checks only scan.tif, although its fake gives raw_prescan=30 and prescan=40.
```

**Failure scenario:** Every real roll frame entry's prescan.tif is corrected with that day's code, and nothing says so. A later shading fix cannot be applied to it. migrate_direction and the demo read it as if it were raw.

**Fix:** File rf.raw_prescan (falling back to rf.prescan with prescan.corrections_applied recorded). Add a session test whose scan_roll yields distinct raw and corrected image and prescan, and assert the contents of scan.tif and prescan.tif and the prescan_meta that reaches scan.json.

<details><summary>Second reader's check</summary>

session.py:1884-1888 passes prescan=rf.prescan and never rf.raw_prescan. DirectScanner.scan_roll's prescan_image comes from prescan(), which calls scan(shading=True) and returns the corrected image (direct.py:1704-1714, 3494-3497). tools/scan_roll.py:564 passes prescan=frame.prescan in the same way. library.save writes it as prescan.tif (180-181), and the prescan block (297-303) records only file, read_direction and carriage_state, with no corrections label. No session or tool test feeds distinct raw and corrected prescans.

</details>

<a id="tests-t08"></a>

### T08 -- library.save is not atomic, verify cannot see partial entries, and prescan/shading/mask have no checksums; none of it is tested

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:166-311`, `rps7200/library.py:741-750`, `rps7200/library.py:776-816`, `tests/test_library.py:129-135`

A crash or kill during the multi-hundred-MB gzip leaves a directory with no scan.json. list, verify and reconstruct cannot see it, and a later save in the same second reuses it. Silent corruption of shading.npz, ccd_mask.bin or prescan.tif passes verify.

**Evidence (from the code):**

```text
`if (path / "scan.json").exists(): ...` (collision check), then `path.mkdir(parents=True, exist_ok=True)`, which reuses a directory that has no scan.json. The data files are written first and `(path / "scan.json").write_text(...)` last. `entries()` globs `root.glob("*/scan.json")`. verify checks sha256 only for scan.tif and raw, checks mere existence for shading/ccd_mask, and never looks at prescan.tif. The only verify test corrupts raw.bin.gz.
```

**Failure scenario:** The session is killed while gzipping a 3600 dpi RGBI entry. library/<id>/ holds scan.tif and a truncated raw.bin.gz, `tools/library.py verify` prints 'library is intact', and the frame is lost unnoticed.

**Fix:** Write into '<id>.partial' and os.replace it into place. Record sha256 for every file. Make verify report directories without scan.json and check every hash. Add tests that raise inside save partway through.

<details><summary>Second reader's check</summary>

library.save (166-311): the collision check looks only for scan.json, mkdir uses exist_ok=True, the data files are written first and scan.json last with a plain write_text. entries() globs */scan.json and silently skips unparseable JSON (745-749). verify (776-816) hashes only scan.tif and raw, checks shading and ccd_mask for existence only, and never mentions prescan.tif. A directory without scan.json is invisible to verify, list and reconstruct.

</details>

<a id="tests-t09"></a>

### T09 -- reconstruct misreports every 7200 dpi entry and lets ScanReadError escape; no test covers either

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1638-1664`, `rps7200/direct.py:1582-1592`, `rps7200/library.py:439-515`, `rps7200/library.py:411-436`, `rps7200/protocol.py:341`, `tools/library.py:90-113`

A 7200 dpi entry (shading=False) stores h-4 rows while the decode gives h, so reconstruct reports 'decode CHANGED' and migrate-raw lists the entry as failed. Raw bytes whose tags lack a channel make decode_index raise ScanReadError, which aborts the whole library survey.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: ... image = self._realign_native_column_stagger(image)` runs before `raw_pixels = image`. reconstruct only calls `DirectScanner.decode_index(raw, params, int(layout["channels"]))` and catches `except (KeyError, ValueError, TypeError)`. `class ScanReadError(RuntimeError)`. decode_raw has the same shape. tools/library.py reconstruct has no try, and migrate-direction catches only (OSError, ValueError, KeyError).
```

**Failure scenario:** After any decode change, `make reconstruct` flags every 7200 dpi entry as changed, the false-alarm pattern that hides a real regression. One malformed entry stops `tools/library.py reconstruct` part way through the library.

**Fix:** Record the stagger realignment in raw_layout and apply it in reconstruct and decode_raw, and catch ScanReadError as 'could not decode'. Add tests with a 7200 dpi entry and with mis-tagged raw bytes.

<details><summary>Second reader's check</summary>

At 7200 dpi, _realign_native_column_stagger (1638-1664) trims 4 lines before `raw_pixels = image` (2695-2708), while raw_layout.lines is params.lines. reconstruct (439-515) only runs decode_index, so the shape comparison at 492-495 reports 'decode CHANGED' for every such entry (reachable via tools/scan.py --no-shading and debug filing). decode_index raises ScanReadError (1584-1592), a RuntimeError (protocol.py:341) that reconstruct's `except (KeyError, ValueError, TypeError)` does not catch. tools/library.py:96-98 has no try, so one bad entry aborts the survey. Also relevant: through ScanSession the same line mismatch trips the _file shape guard, so the bytes are dropped rather than misreported (see additional finding AF1).

</details>

<a id="tests-t10"></a>

### T10 -- Entries drop the device INQUIRY, the calibration bytes and MODE SELECT choices; the 'keeps everything needed' test checks none of them

**Severity** medium · **Category** library-completeness · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:142`, `rps7200/session.py:2137`, `rps7200/direct.py:728`, `tools/scan.py:192`, `tools/scan_roll.py:567`, `rps7200/direct.py:1945-1970`, `rps7200/direct.py:2760-2804`, `tests/test_library.py:79-91`

Only a derived ShadingReference is kept. The 1.66 MB calibration block it was computed from, and the calibration pass's own CCD mask, are discarded. So a change to calculate_shading's dark/light split cannot be re-run on stored scans. Device identity (model, firmware) and the byte-14 or skip-shading bits sent are not in scan.json.

**Evidence (from the code):**

```text
library.save takes `inquiry: Any = None,` and never refers to it, although every caller passes one (`inquiry=getattr(self._scanner, "_inquiry", None)`, `inquiry=self._inquiry`, `inquiry=info`). calibrate_shading: `self._shading = calculate_shading(data, width)` ... `"data": data if keep_data else None,`. The scan meta dict has no byte14, skip_shading, slide_init_param or mask size. The test asserts only film, tags, dpi, exposure and provenance.
```

**Failure scenario:** A bug is found in calculate_shading's phase split. Every stored entry keeps the old, wrong reference forever. A firmware question cannot be answered from any entry.

**Fix:** Persist the inquiry fields. Keep the calibration block (gzipped) and its mask once per session and reference them from each entry. Record the MODE SELECT fields. Extend the test to assert all of these.

<details><summary>Second reader's check</summary>

library.save's signature takes inquiry (142), but the body never refers to it. Callers pass it (session.py:2137, direct.py:728, scan_roll inquiry=info). calibrate_shading keeps the calibration block only when keep_data (1966). The calibration mask stored in self._ccd_mask (1954) is overwritten by each pass's mask (2672), so the calibration's own mask is not kept. The scan meta (2760-2804) has no MODE SELECT byte14, skip_shading or mask_size. test_an_entry_keeps_everything_needed_to_use_it_again (test_library.py:79-91) checks only film, tags, dpi, exposure and provenance.

</details>

<a id="tests-t12"></a>

### T12 -- Roll prescans are filed with film 'negative' whatever the roll's film; the demo passes the film, and FakeRoll.prescan cannot accept one

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:3494-3496`, `rps7200/direct.py:2906-2907`, `rps7200/direct.py:1682-1714`, `rps7200/demo.py:708`, `tests/test_roll.py:202`, `rps7200/session.py:1818-1831`

On the hardware, a B&W or slide walk files every prescan with scan.film='negative'. The demo labels them correctly, so the divergence is invisible there. The test double's signature would raise TypeError if the driver were fixed to pass film=.

**Evidence (from the code):**

```text
scan_roll: `prescan_image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)`. _hold_to_approved: `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)`. prescan signature: `film: str = FILM_NEGATIVE` → `film=film` into meta. demo: `prescan, _ = self.prescan(film=film)`. FakeRoll: `def prescan(self, resolution=300, frame=None, keep_raw=False):`.
```

**Failure scenario:** A B&W walk's prescan entries are indexed as colour negative. The demo's _source_for and _pool_for, and any later analysis filtering by scan.film, pick the wrong entries.

**Fix:** Pass film= in both prescan calls. Add film to FakeRoll.prescan and assert that prescan_meta['film'] equals the roll's film.

<details><summary>Second reader's check</summary>

Neither call passes film=: scan_roll's self.prescan(resolution=prescan_resolution, keep_raw=keep_raw) at direct.py:3494-3496, and _hold_to_approved at 2906-2907. prescan defaults film=FILM_NEGATIVE (1687) and forwards it into meta (1714). Session dry-run filing uses rf.prescan_meta as the entry's meta (session.py:1823), so a B&W or slide walk's prescan entries say scan.film='negative'. The demo passes film=film (demo.py:708). FakeRoll.prescan (test_roll.py:202) has no film or **kw parameter.

</details>

<a id="tests-t13"></a>

### T13 -- Raw bytes that cannot decode to the image are accepted by library.save and the session guard; several tests encode this and none reconstruct what they filed

**Severity** medium · **Category** test-gap · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/session.py:2069-2093`, `rps7200/library.py:186-211`, `tests/test_session.py:108-116`, `tests/test_session.py:784-795`, `tests/test_roll_writer.py:20-64`, `tests/test_scanner_api.py:135-154`, `tests/test_scan_tool.py:72-73`

Nothing checks len(raw) against lines_received×(bpl+2), depth, or a trial decode. The guard is blind to a same-shape earlier pass, as its own docstring admits, and to an 8-bit pass beside a 16-bit one. No filing test ends with library.reconstruct.

**Evidence (from the code):**

```text
Guard: 'Only fields the layout actually declares are judged', comparing only lines, width and channels. Test: `"raw": b"\x00\x01" * 32, "raw_layout": {"format": "index", "width": 36}` → `assert (entry / "raw.bin.gz").exists()`. The session FakeScanner gives the same 64-byte blob for every pass. Other tests file `b"raw bytes"`, `b"RRdata"` and `b"pass-1"` as raw.
```

**Failure scenario:** A regression that files the wrong pass's bytes, or a truncated blob, passes the suite. The demo prescan-from-TIFF path, which keeps the previous _decode capture, can file a 16-bit 300 dpi scan's bytes beside an 8-bit 300 dpi prescan.

**Fix:** Validate in library.save: check the byte count against the layout, or decode and compare against image, and record the verdict. Have fakes emit real INDEX bytes via direction.encode_index, and end each filing test with reconstruct()=='identical...'.

<details><summary>Second reader's check</summary>

The _file guard (session.py:2072-2099) compares only lines, width and channels, and only where the layout declares them. library.save validates nothing about raw. Tests file placeholder blobs (b'\x00\x01'*32 with layout {'width':36} at test_session.py:784-795, b'raw bytes' in test_roll_writer.py job()), and none ends with reconstruct(). The demo prescan-from-TIFF path (demo.py:1176-1182) returns without resetting self._capture, so the previous pass's capture (raw, reference, mask) is what capture_record hands over. Its raw is usually caught by the width and lines check, but the reference and mask are not. The specific '16-bit 300 dpi beside 8-bit prescan' case is speculative.

</details>

<a id="tests-t14"></a>

### T14 -- submit() clears a pending Stop, and the aim-click, fine-move and frame-move handlers submit while busy; untested

**Severity** medium · **Category** concurrency · **Verdict** partly · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `rps7200/session.py:1213-1217`, `rps7200/session.py:1317`, `tools/gui.py:4500-4504`, `tools/gui.py:4531-4587`, `tools/gui.py:2972-3018`, `tools/gui.py:3020-3023`, `tools/gui.py:3346-3348`

ScanSession.submit() unconditionally clears _stop. The window's canvas 'aim' click (on_press, then _aim, then on_nudge) has no busy guard, unlike the slide and fine buttons, which _set_busy disables. So with 'aim' ticked, a click on a roll prescan during a running roll, confirmed in the dialog, queues a Move and cancels a Stop the operator already pressed, with the Stop button already greyed. No test covers stop-then-submit mid-job or aim-while-busy.

**Evidence (from the code):**

```text
`def submit(self, job: Job) -> None: self._stop.clear(); self._jobs.put(job)`. The worker already runs `self._stop.clear()` at each job start (1317). `on_move_frames: self.session.submit(Move(frames=frames))` and on_nudge have no `if self.busy`. on_press: `if self.v_aim.get() and self.current is not None and self.current.kind == "prescan": self._aim(event)`. on_stop disables b_stop.
```

**Failure scenario:** During a 38-frame roll the operator presses Stop, then clicks a prescan to aim and confirms. The roll continues to its end (hours), and afterwards the film is nudged on whatever frame the roll ended on.

**Fix:** Do not clear _stop in submit (the worker already does per job), or clear it only when idle. Gate _aim, on_nudge and on_move_frames on self.busy. Add a session test: start a FakeScanner roll, request_stop, submit a Move mid-frame, and assert the roll stopped.

<details><summary>Second reader's check</summary>

submit() clears _stop (session.py:1213-1217), and the worker clears it again at each job start (1317), so a queued submit mid-job erases a pending Stop. on_stop disables b_stop (gui.py:3020-3023). However, on_move_frames and on_nudge are behind b_prev, b_next, b_fine_back and b_fine_fwd, and _run_buttons() (gui.py:3346-3348) disables all four while busy. Only the canvas aim click (on_press, then _aim, then on_nudge at gui.py:4500-4504 and 4587) reaches submit with no busy check.

</details>

<a id="tests-t15"></a>

### T15 -- A second walk under the same roll name (the default is today's date) overwrites survey.json and prescanNN.tif

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1656`, `rps7200/session.py:1817-1831`, `rps7200/session.py:1924-1926`, `tests/test_session.py:436-446`

A re-walk replaces the first walk's registration record and its reference prescans. approved.json in the same folder then refers to positions measured on pictures that no longer exist there.

**Evidence (from the code):**

```text
`name = job.name or time.strftime("%Y-%m-%d")`. `if not job.dry_run and manifest_path.exists(): ... earlier = ...`, so a dry run never merges. `surveyed = out / f"prescan{number:02d}.tif"` is passed as path= (not _unclaimed). The only test covers a walk followed by a roll, not walk twice.
```

**Failure scenario:** Two unnamed walks on the same day: the second silently replaces rolls/2026-09-24/survey.json and every prescanNN.tif. Commissioning from the first walk's sheet holds frames against the wrong reference.

**Fix:** Refuse, suffix, or merge when survey.json exists. Add a test that runs two dry runs with the same name.

<details><summary>Second reader's check</summary>

session.py:1634 `name = job.name or time.strftime("%Y-%m-%d")`. The GUI passes name=self.fields['roll'].get().strip() (gui.py:2159), which may be empty. manifest_path for a dry run is survey.json, and earlier is loaded only `if not job.dry_run` (1650), so a second same-name walk writes a fresh survey.json and overwrites prescanNN.tif (path=surveyed with no _unclaimed, 1817-1831). There is no existence check or warning in on_roll.

</details>

<a id="tests-t17"></a>

### T17 -- The demo's scan and scan_roll swallow or diverge on parameters, refusals and failure handling; parity tests check only constants

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:530-597`, `rps7200/demo.py:599-791`, `rps7200/direct.py:2505-2526`, `rps7200/direct.py:3664-3680`, `tests/test_demo.py:265-303`

CLAUDE.md requires that refusals live in the stand-in and that decisions are taken from DirectScanner. As written, the demo accepts a 7200 dpi corrected scan, ignores the roll's prescan resolution and metering, and never produces a failed frame. So the session's rf.error branch and the manifest 'done: False' resume path are never exercised without hardware.

**Evidence (from the code):**

```text
The demo has no ShadingUnavailable for 7200 dpi. `shading` only changes meta["shading"]; the pixels come from `_decode`, which corrects whenever a reference exists. scan_roll(..., `**kw: Any`) swallows prescan_resolution, meter, max_failures, blank_contrast and exposure_target. `self._hold_to_approved(index, prescan, 300, held, keep_raw=False, ...)`. A frame exception propagates (the loop has only try/finally), whereas the driver yields `RollFrame(index, position, None, {}, ..., error=str(exc))` and continues. The tests assert `DemoScanner.param_for_mm is DirectScanner.param_for_mm` and hasattr only.
```

**Failure scenario:** In --demo, a 7200 dpi scan succeeds where the scanner raises. A roll set to 600 dpi prescans holds against 300 dpi references. A transient error ends the demo roll instead of showing a failed frame, which reads as a fragile roll loop.

**Fix:** Stand in only for the passes and the transport (as conftest.ScannerOnStrip does) and run the real scan_roll, or reuse the driver's refusal checks. Add parity tests that feed identical inputs through a ScannerOnStrip-style DirectScanner and through DemoScanner, and compare refusals, failed-frame yields and prescan resolution.

<details><summary>Second reader's check</summary>

DemoScanner.scan (demo.py:530-597) has no MAX_SHADING_COLUMNS refusal (the driver's is at direct.py:2566-2577), so a 7200 dpi shading=True scan succeeds in the demo. The shading flag only changes meta. scan_roll takes **kw (615), which swallows prescan_resolution, meter, max_failures and so on. It calls `self._hold_to_approved(index, prescan, 300, ...)` with a hard-coded 300 (721-722). A frame exception propagates through try/finally, whereas the driver yields RollFrame(error=...) and continues (direct.py:3665-3677). The parity tests (test_demo.py:265-303) check only identity and hasattr.

</details>

<a id="tests-t18"></a>

### T18 -- tools/scan_roll.py --dry-run files nothing in the library and forces debug off; the test never checks library entries

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `tools/scan_roll.py:353`, `tools/scan_roll.py:489-525`, `tests/test_scan_roll_tool.py:159-165`

**Doc claim:** CLAUDE.md:104-106 'File every scan in the library, with its raw bytes. tools/scan.py and tools/scan_roll.py both default to library/'; CLAUDE.md:312 'Want a roll walked? tools/scan_roll.py --dry-run walks one.'

The CLI walk, which CLAUDE.md names as the way to walk a roll, leaves corrected prescans in rolls/ and no library entry, no raw bytes and no calibration. debug=False overrides an exported RPS7200_DEBUG=1, so even the safety net is off. The window's walk does file its prescans.

**Evidence (from the code):**

```text
`with DirectScanner(verbose=args.verbose, debug=False) as s:`. The dry-run branch does only `tiff.write(str(pre), frame.prescan)` and `tiff.write(str(was), frame.prescan_before)`, with no writer.submit or library.save. The test asserts that prescan*.tif and survey.json exist.
```

**Failure scenario:** A walk done from the command line cannot be re-decoded or re-corrected. When the prescans are later needed as evidence, only corrected TIFFs exist: the 'week of probe scans left no library entries' failure again.

**Fix:** Submit dry-run prescans to the FrameWriter with raw_image=frame.raw_prescan and capture_record(). Add a test that asserts N library entries after --dry-run --frames N.

<details><summary>Second reader's check</summary>

tools/scan_roll.py:353 opens `DirectScanner(verbose=..., debug=False)`, and debug=False overrides RPS7200_DEBUG (direct.py:464 applies the env only when debug is None). The dry-run branch (499-515) only tiff.writes frame.prescan and frame.prescan_before (corrected pixels), with no writer.submit and no library.save. The comment at 344-346 justifies debug=False by 'this tool files its own library entries', which is false for --dry-run. The CLAUDE.md:104-106 and 312 claims are contradicted.

</details>

<a id="tests-d01"></a>

### D01 -- Doc mismatch: demo.py says an entry it files reconstructs like any other

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:15-19`, `rps7200/demo.py:1009-1010`, `tests/test_demo.py:69-82`

**Doc claim:** rps7200/demo.py:18-19

The claim holds only for source entries without a shading reference, which is the test fixture's case and never a real library's.

**Evidence (from the code):**

```text
'...and `capture_record` can hand the session genuine bytes to file. An entry the demo files reconstructs like any other.' versus `image, self._shading_report = apply_shading(image, reference, mask)` returning corrected pixels, with the uncorrected bytes in the capture.
```

**Failure scenario:** A reader relies on demo-filed entries as a reconstruct check and gets 'decode CHANGED'.

**Fix:** Fix the demo (T02), or qualify the docstring.

<details><summary>Second reader's check</summary>

The demo.py:15-19 docstring claims demo-filed entries reconstruct like any other. _decode corrects whenever the source entry has a reference (1009-1010), while the capture holds the uncorrected bytes. Only the reference-less test fixture reconstructs.

</details>

<a id="tests-d02"></a>

### D02 -- Doc mismatch: 'scan.tif in an entry is the decode alone', and scan_roll's comment that the session always passed raw_image

**Severity** medium · **Category** doc-mismatch · **Verdict** partly · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `CLAUDE.md:114-118`, `rps7200/session.py:1456-1458`, `rps7200/session.py:1884-1888`, `tools/scan_roll.py:555-558`

**Doc claim:** CLAUDE.md:114-118; tools/scan_roll.py:558

CLAUDE.md:114-118 says every entry's scan.tif is the raw decode. That is false for GUI single Scan jobs (session._scan files the corrected image), for every demo-filed entry, and for the corrected prescan.tif in roll frame entries. The scan_roll.py:558 comment is accurate for the session roll path but cites a stale line number (1110, now 1886).

**Evidence (from the code):**

```text
CLAUDE.md: 'The library holds raw pixels; everything else is corrected. `scan.tif` in an entry is the decode alone, with no flat-fielding'. scan_roll.py:558: '`session.py:1110` has always passed this; this tool never did.' ScanSession._scan passes no raw_image (T01), and roll frame entries file the corrected prescan.tif (T07).
```

**Failure scenario:** Analyses that trust library.load() as raw measure corrected, or doubly corrected, data.

**Fix:** Fix T01, T02 and T07. Until then, document the exceptions.

<details><summary>Second reader's check</summary>

The CLAUDE.md:114-118 claim that 'scan.tif ... is the decode alone' is contradicted by session._scan (T01), all demo entries (T02) and, for prescan.tif, frame entries (T07). The tools/scan_roll.py:558 comment ('session.py:1110 has always passed this') is about roll frames, and session._roll does pass raw_image=rf.raw_image (session.py:1886). Only the line reference is stale; the comment is not contradicted for the roll path.

</details>

<a id="tests-af1"></a>

### AF1 -- Session shape guard discards the raw bytes of every short read (EndOfData) and every 7200 dpi pass, because it compares layout.lines (requested) with the decoded or realigned height

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/direct.py:1497-1499`, `rps7200/direct.py:1518-1531`, `rps7200/direct.py:1594`, `rps7200/direct.py:2695-2708`, `rps7200/session.py:2072-2099`

The layout declares the requested line count, not the lines received. A pass that ends early (EndOfData is accepted as normal termination), or a 7200 dpi pass trimmed by 4 lines in the stagger realignment, therefore always disagrees with the image height. The session then files the entry without raw bytes, although the bytes are exactly the pass's own. The guard ignores the layout's own `lines_received` field.

**Evidence (from the code):**

```text
read_planes: `except EndOfData: self._log(f"end of data at {got}/{total_lines} lines"); break`. The layout is then recorded as `"lines": int(params.lines), ... "lines_received": len(blob) // (...)`, and decode_index uses `height = min(len(planes[c]) for c in order)`. In session._file: `actual = {"lines": shape[0], ...}` / `disagree = {k: ... if layout.get(k) is not None and layout[k] != actual[k]}` / `capture = dict(capture, raw=None, raw_path=None, raw_layout=None)`.
```

**Failure scenario:** The device ends a 1800 dpi roll frame a few lines short. The entry is filed with raw pixels but no raw.bin.gz, logged as 'raw bytes do not describe this image', and can never be re-decoded. A truncated pass is exactly the one whose bytes would be wanted as evidence. No test files a short read through ScanSession.

**Fix:** Compare against lines_received (and the realignment trim) rather than requested lines, or record the realignment in raw_layout. Add a session test with a scripted short read that asserts raw.bin.gz is kept and reconstruct says 'identical'.

<a id="tests-t11"></a>

### T11 -- test_the_scan_block_carries_everything_scan_records only checks keys it typed itself; roll_index, roll_position and the demo marker are silently dropped

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_library.py:388-420`, `rps7200/library.py:237-261`, `rps7200/direct.py:3653-3654`, `rps7200/demo.py:475`, `rps7200/demo.py:591`

The guard meant to catch 'scan() recorded it and the sidecar dropped it' uses its own list, so it can never fail for a key scan(), scan_roll or DemoScanner adds.

**Evidence (from the code):**

```text
The test builds `meta = {"resolution_dpi": 900, ... "metering": ...}` by hand and checks `scan_facts = {k: v for k, v in meta.items() if k not in (...)}`. The driver adds `meta["roll_index"] = index; meta["roll_position"] = position`, and the demo adds `"demo": True`. None of these are in library.save's key tuple.
```

**Failure scenario:** Demo-filed entries (corrected pixels plus borrowed bytes) copied into, or filed into, a real library via --library carry no marker that they are fake. Roll position evidence is missing from entries.

**Fix:** Generate meta from the real producers (end-to-end scan, scan_roll, DemoScanner.scan) and assert that each key is either persisted or listed as a deliberate exclusion.

<details><summary>Second reader's check</summary>

test_library.py:388-420 builds meta by hand and checks only keys it typed itself. library.save's fixed key tuple (237-261) lacks roll_index, roll_position (added at direct.py:3653-3654) and 'demo' (demo.py:475, 591), so all three are silently dropped. The test cannot fail for a key that a producer adds.

</details>

<a id="tests-t16"></a>

### T16 -- roll.json is rewritten in place, and a corrupt manifest is read as empty on resume; untested

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1650-1656`, `rps7200/session.py:1924-1926`, `tests/test_session.py:339-356`

A kill or power loss during the per-frame rewrite leaves truncated JSON. The resume then silently starts from {} and writes back only its own frames, losing the record of which frames were done.

**Evidence (from the code):**

```text
`try: earlier = json.loads(manifest_path.read_text(encoding="utf-8")) except (OSError, ValueError): earlier = {}`, then `manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")` after every frame.
```

**Failure scenario:** The machine sleeps or crashes while writing after frame 20 of 24. On resume, roll.json lists only the resumed frames, 'wanted' loses the earlier choice, and nothing is logged.

**Fix:** Write via a temp file and os.replace. On a parse error, back the file up and refuse or say so. Add a test with a truncated roll.json.

<details><summary>Second reader's check</summary>

session.py:1650-1656 swallows (OSError, ValueError) into {} with no log. manifest_path.write_text (1924-1926) is an in-place, non-atomic rewrite after every frame. A torn file on resume silently drops the prior frames' records. The window is small (a few kB write), and the library entries themselves survive, so the harm is a lost roll record and re-offered frames rather than lost scans. Hence low.

</details>

<a id="tests-t19"></a>

### T19 -- Tautological and environment-sensitive assertions

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_roll.py:751-757`, `tests/test_roll.py:790`, `tests/test_session.py:582-589`, `tests/test_hardware.py:228-239`

Two of these assertions can never fail. One test fails when the developer follows CLAUDE.md and exports RPS7200_DEBUG=1 in the shell. The downscale test's input is already smaller than any limit. The hardware test named for the whole module observes one command.

**Evidence (from the code):**

```text
`assert os.environ.get("RPS7200_DEBUG") is None or True`. `assert _debug_scanner().debug is False` without monkeypatch.delenv. `assert (entries[0] / "raw.bin.gz").exists() or True`. `assert max(image.shape[:2]) <= 512 or max(image.shape[:2]) <= 1400` on a 24x36 fake. test_nothing_in_this_module_moves_the_transport brackets a single `scanner.inquiry(refresh=True)`.
```

**Failure scenario:** `RPS7200_DEBUG=1 make test` goes red on test_filing_is_off_by_default. A regression that stops writing raw.bin.gz in debug filing, or breaks preview downscaling, stays green.

**Fix:** Use monkeypatch.delenv. Assert real conditions. Use an input larger than preview.PREVIEW_MAX_SIDE. Record the position in the module fixture's setup and compare at teardown.

<details><summary>Second reader's check</summary>

Two assertions can never fail: test_roll.py:754 `... is None or True` and 790 `... .exists() or True`. test_roll.py:755 `_debug_scanner().debug is False` reads RPS7200_DEBUG through DirectScanner.__init__ (debug=None, then env), so it fails when the env var is exported. test_session.py:582-589 has a tautological size bound. test_hardware.py:228-239 brackets only one inquiry.

</details>

<a id="tests-t20"></a>

### T20 -- Every test on real scanner data skips outside the owner's machine, and CI never runs them

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_decode.py:300-345`, `tests/test_tiff.py:435-465`, `tests/test_demo.py:605-670`, `tests/test_frame_edges_parity.py:34-40`, `tests/test_usbpcap.py:240-307`, `tests/test_hardware.py:62-72`, `tests/test_gui.py:31`, `tests/test_gui.py:1092-1101`, `pyproject.toml:addopts`

**Doc claim:** CLAUDE.md:59-61 'Test the host side against stored bytes ... decoding, correction and merging are all re-runnable offline.'

The bottom-up passes on stored entries, demo convergence on real prescans, frame-edge parity and capture replay run only by hand where the data exists. Everything else uses synthetic bytes. The skips are silent in a green run.

**Evidence (from the code):**

```text
`if raw is None: pytest.skip(f"{name} is not in this library")`; `pytest.skip("no library entries in this checkout to register against")`; `pytest.mark.skipif(not os.environ.get("FRAME_EDGE_PARITY"), ...)`; `pytest.skip("captures/ is not in this checkout")`; `addopts = "-m 'not hardware'"`. library/, scans/, captures/ and research/frame-edge/data are absent in this checkout, and .github/workflows/test.yml notes that reconstruct and verify are 'Deliberately not run here'.
```

**Failure scenario:** A decode or direction regression that only shows on real passes (for example uneven plane counts) is merged from a green CI and found when Stefan next runs pytest with his library.

**Fix:** Commit a small anonymised fixture set (a few INDEX passes including a bottom-up one, a prescan pair) so these run everywhere. Report skip counts in CI and fail on unexpected skips.

<details><summary>Second reader's check</summary>

library/, scans/, captures/ and research/frame-edge/data are absent in this checkout. pyproject addopts is "-m 'not hardware'", and .github/workflows/test.yml:61 says reconstruct and verify are deliberately not run. The cited tests skip on missing data. This is an observation about coverage, not a code defect.

</details>

<a id="tests-t21"></a>

### T21 -- About 124 source-text assertions stand in for behaviour, and GUI user-error paths have no behavioural tests

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tests/test_gui.py:1-12`, `tests/test_gui.py:2247-2253`, `tests/test_gui.py:3530-3547`, `tests/test_session.py:797-803`, `tests/test_frame_edges.py:238-243`, `tests/test_scan_tool.py:359-369`, `tests/test_roll.py:1232-1234`

String presence is satisfied by comments, dead branches or a moved call. The GUI's user-error handling (delete entry, force abort, close while busy, submitting while busy) is checked only by hand, against a demo that diverges (T02, T17). CI already provides xvfb, so the `window` fixture could exercise them.

**Evidence (from the code):**

```text
`assert "should_stop=self._stop.is_set" in source`; `assert "askokcancel" in inspect.getsource(gui.ScannerGui.on_roll)`; `assert "StripWalk(reader=edge_reader(film)" in inspect.getsource(demo.DemoScanner.scan_roll)`; `assert "image if raw_pixels is None else raw_pixels" in source`. test_gui.py:6-7: 'the widget wiring is checked by running `make run-demo`'. on_delete, on_abort, on_close and aim-while-busy have no test (grep).
```

**Failure scenario:** A refactor moves a guarded call behind a condition that never fires. The string is still present, so the test passes and the guard is gone.

**Fix:** Replace these with behavioural tests at the seams (ScanSession with fakes; the Tk `window` fixture under xvfb) that drive the handlers and assert what was submitted or filed.

<details><summary>Second reader's check</summary>

`grep -c getsource tests/*.py` sums to 124. test_gui.py:1-12 states that the widget wiring is checked by `make run-demo`. on_delete, on_abort and aim-while-busy have no behavioural test. A `window` fixture exists at test_gui.py:1091, so behavioural GUI tests are feasible.

</details>

<a id="tests-t22"></a>

### T22 -- Delete dialog: answering 'No' permanently removes the library entry and its raw bytes; untested

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:3999-4016`

This is the only in-window path that destroys raw bytes. It hangs on one easy-to-misread Yes/No, has no trash or undo, and runs reindex on the UI thread concurrently with the FrameWriter's reindex.

**Evidence (from the code):**

```text
`keep = messagebox.askyesnocancel("Delete", question + "\n\nKeep the library entry?", parent=self.root)` ... `if not keep: shutil.rmtree(entry); library.reindex(entry.parent)`.
```

**Failure scenario:** The operator tidies the filmstrip, reads the dialog as 'delete?' and answers No. The pass's raw.bin.gz, shading.npz and mask are gone.

**Fix:** Move entries to a trash folder, or require explicit confirmation, and add a behavioural test.

<details><summary>Second reader's check</summary>

gui.py:3999-4016: askyesnocancel 'Keep the library entry?', where No leads to shutil.rmtree(entry) plus library.reindex on the Tk thread. The dialog does state that the raw bytes cannot be recovered, so this is an easy-to-misread, irreversible design choice rather than a silent bug. There is no trash or undo, and no test.

</details>

<a id="tests-d03"></a>

### D03 -- Doc mismatch: 'Every library entry keeps its raw bytes, its shading reference and its CCD mask' and host-side testing 'against stored bytes'

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `CLAUDE.md:59-61`, `rps7200/session.py:2088-2093`, `tools/scan_roll.py:489-525`, `rps7200/direct.py:1966`, `tests/test_decode.py:300-320`

**Doc claim:** CLAUDE.md:59-61

The testing claim describes the intent, not what the suite exercises. The completeness claim does not hold for several filing paths.

**Evidence (from the code):**

```text
Entries lose raw bytes when the shape guard drops them ('filing it without them rather than filing the wrong ones'). CLI walks file no entries at all (T18). The calibration block behind shading.npz is discarded (`"data": data if keep_data else None`). The stored-bytes tests skip in every checkout without library/.
```

**Failure scenario:** Someone trusts that any scan can be re-derived from the library, and finds CLI walks and guard-dropped passes cannot be.

**Fix:** Correct the wording, or close T10, T18 and T20.

<details><summary>Second reader's check</summary>

CLAUDE.md:59-61 says every entry keeps its raw bytes, reference and mask. The session guard drops raw (session.py:2088-2099). CLI walks file nothing (T18). Deliberately raw scans have no reference. The calibration block is discarded (direct.py:1966). The stored-bytes tests skip without library/.

</details>

<a id="tests-d04"></a>

### D04 -- Doc mismatch: test_roll says the uncorrected pixels reach the library, but only the debug-filing path is pinned

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tests/test_roll.py:723-748`, `rps7200/session.py:1456-1458`

**Doc claim:** tests/test_roll.py:731-735

The test's name and docstring promise library-wide behaviour, but it pins one call inside scan(). The session path, which is what files GUI scans, is not raw (T01).

**Evidence (from the code):**

```text
Docstring: 'the assertion is that the two arrays handed out are different objects and that it is the *uncorrected* one that reaches the library.' The test checks only for the string `self._debug_capture(raw_pixels, meta)` in DirectScanner.scan's source.
```

**Failure scenario:** Readers take this test as coverage of the filing contract.

**Fix:** Rename it, or replace it with a behavioural test covering ScanSession._scan, _prescan and _roll.

<details><summary>Second reader's check</summary>

The test_roll.py:731-735 docstring promises that the uncorrected array is what reaches the library, but the test only checks for substrings in DirectScanner.scan's source (the debug-capture call). The session filing path (_scan) is not covered and is in fact wrong (T01).

</details>

<a id="tests-af2"></a>

### AF2 -- Every conftest scanner double returns a capture_record with raw=None, so no session or roll test ever files real INDEX bytes

**Severity** low · **Category** test-gap · **Verdict** found-by-verifier

**Where:** `tests/conftest.py:193-195`, `tests/conftest.py:357-359`, `tests/conftest.py:206-211`, `tests/conftest.py:369-372`

The shared fakes that drive seek, roll and walk tests carry no bytes, reference or mask. The raw_image / raw_prescan / capture routing through ScanSession._roll, and the shape guard, are never exercised on those paths. Only test_session's FakeScanner supplies a (non-decodable) 64-byte blob.

**Evidence (from the code):**

```text
`def capture_record(self): return {"reference": None, "ccd_mask": None, "raw": None, "raw_layout": None}` in both StripScanner and ScannerOnStrip. Their prescan and scan return synthetic arrays with no last_pixels_raw.
```

**Failure scenario:** A regression that drops raw_image or capture on roll frames, or files the wrong pass's bytes, passes every strip-based test.

**Fix:** Give the conftest doubles real INDEX bytes via direction.encode_index, a non-flat ShadingReference and distinct raw/corrected arrays. Assert reconstruct and corrected() on what the session filed.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry pixels | library/<YYYYMMDDTHHMMSSZ>_<stock>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF, deflate+predictor when tifffile is installed, else uncompressed; uint16 (H,W,3\|4) for scans, uint8 (H,W,3) for prescan entries | Intended raw decode (upright, 7200 dpi realigned). Actually CORRECTED for ScanSession._scan (GUI Scan button) and for every demo-filed entry; raw for tools/scan.py, tools/scan_roll.py frames, session roll frames, Prescan jobs and debug filing | library.save via session.FrameWriter._write, tools/scan.py, DirectScanner._debug_flush; rewritten by library.migrate_direction(write=True) and tools/library.py migrate-raw --write; tests write only under tmp_path | library.load/corrected/reconstruct/migrate_direction; tools/gui.py 1:1 view (4110) and Save As (3864) via library.corrected; demo._decode fallback and picture_signature; tools/uniformity, linearity, dpi_analysis | Lossless storage of whatever array was passed. Not guaranteed raw (T01, T02). sha256 in scan.json. Tests verify raw content only for tools/scan.py and scan_roll frames |
| Scanner bytes as received | library/<id>/raw.bin.gz | gzip level 6 of INDEX-format lines (2-byte channel tag + bytes_per_line), in read order (bottom-up passes not reversed) | raw | library.save (raw= bytes, or raw_path= streamed from the debug spool) | library.read_raw/decode_raw/reconstruct/migrate_direction/verify; demo._decode | Byte-exact when present (sha256 plus byte count). May belong to a different pass under debug filing with keep_raw=False (T04). Omitted when the session shape guard trips. Session and writer tests file undecodable placeholder blobs and never reconstruct them |
| Entry record | library/<id>/scan.json | JSON: id, created, image{file,shape,dtype,channels,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{fixed key list}, device_settings{exposure,gain,offset}, metering, registration, calibration{shading,ccd_mask,pixels_per_line,light_mean rounded to 0.1,report,skipped}, prescan{file,read_direction,carriage_state}, film, tags, provenance | metadata | library.save (written last, non-atomic), migrate_direction, tools/library.py migrate-raw | library.entries/load/verify/reconstruct/signature/prunable; demo; tools/* | Not complete: drops inquiry (the parameter is ignored), roll_index, roll_position, the demo marker, byte14/skip_shading/slide param and the calibration block. A partial entry without scan.json is invisible (T08) |
| Shading reference in force for the pass | library/<id>/shading.npz | np.savez_compressed: ref<c>, mean<c>, dark<c>, darkmean<c>, channels, dark_channels, pixels_per_line (float64 arrays) | derived (from the calibration block by calculate_shading; the block itself is not stored) | ShadingReference.save via library.save | library.load/corrected/reconstruct (legacy entries); demo._decode | Exact for the derived arrays; not re-derivable if calculate_shading changes (T10); no checksum; verify checks existence only |
| Per-pass CCD mask | library/<id>/ccd_mask.bin | raw bytes, one per calibration column (5172 at 3600-dpi calibration) | raw | library.save | library.load/corrected; demo._decode | Byte-exact; no checksum. The calibration pass's own mask is not stored |
| Framing pass stored with a frame | library/<id>/prescan.tif | TIFF uint8 (H,W,3) | CORRECTED for session roll frames and scan_roll frames (rf.prescan); unlabelled | library.save(prescan=...) | demo._pair_image, demo.picture_signature, best_pair, migrate_direction (may flip it in place) | No raw bytes, no checksum, not verified (T07, T08) |
| Library index | library/index.json | JSON summary list | derived | library.reindex after every save, delete, migrate or dedupe (non-atomic; also called from the GUI thread in on_delete) | nothing load-bearing | Rebuildable |
| Roll and walk manifests | rolls/<name or YYYY-MM-DD>/roll.json, survey.json | JSON: roll, numbering, settings{...}, wanted, frames[{number,index,transport_position,registration,error,done,exposure,gain,offset,prescan}] | metadata | ScanSession._roll after every frame (in-place write_text); tools/scan_roll.py checkpoint() | ScanSession._roll resume (renumbered; a corrupt file is read as {}), gui read_survey/open_roll, gui summaries | Non-atomic (T16); survey.json is replaced by a re-walk of the same name (T15) |
| Roll delivered files | rolls/<name>/frameNN.tif, prescanNN.tif, prescanNN-before.tif | TIFF (always), oriented by rotation, flip and reversal; frames monochrome for B&W | corrected | FrameWriter._write (export.write), tools/scan_roll.py (tiff.write for dry-run prescans) | gui read_survey (prescans reopened as hold references, un-oriented), humans, NegPy | Lossless, but derived and not re-derivable once the entry is missing |
| Contact-sheet decisions | rolls/<name>/approved.json | JSON [{number, offset_mm, rotation, flipped, reference_entry, source}] | metadata | tools/gui.py _write_approved (on_scan_chosen) | gui reopen, tools/scan_roll.py --approved | Can go stale if survey.json and prescans are overwritten by a same-name re-walk |
| Operator output copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng with the IR plane), <out_dir>/prescans/...; tools/scan.py --out file + .json | TIFF/JPEG/DNG | corrected once (correct), oriented | FrameWriter._write, before library.save (T03); tools/scan.py | operator | Lossy for JPEG; a failure here prevents library filing |
| Debug filing spool | $TMP/rps7200-debug-XXXX/NNN-image.npy, NNN-raw.bin | np.save of the raw pixels; the raw last_raw bytes | raw pixels; bytes may be stale from an earlier pass | DirectScanner._debug_capture (debug=True or RPS7200_DEBUG) | DirectScanner._debug_flush at close() | Deleted after flush even when filing failed (T05) |
| Session shading cache | calibration/shading.npz (demo: demo/calibration/shading.npz) | ShadingReference npz | derived | DirectScanner.ensure_shading/save_shading | ensure_shading(reuse=True), load_shading, gui prompt (mtime) | Derived only; the demo reports 'loaded' even when the file is absent |
| Demo state | demo/library/<id>/*, demo/pictures.npz, demo/gui-settings.json, demo/rolls/ | as the library; npz of paths and signature grids; JSON | demo scan.tif is corrected with borrowed raw bytes (T02) | ScanSession/FrameWriter under --demo; DemoScanner._sign_pictures | demo, gui | No demo marker survives into scan.json |
| Test-side disk use | pytest tmp_path/**; reads of cwd-relative library/, scans/, captures/, research/frame-edge/data (skip when absent) | as above | synthetic fixtures (INDEX bytes via direction.encode_index or hand-built; placeholder raw blobs in session and writer tests) | tests (library.save, ScanSession, FrameWriter, tool main()) | tests via library.entries/load/reconstruct and tiff.read | Real-data tests skip everywhere but the owner's machine. RPS7200_DEBUG=1 in the environment breaks test_filing_is_off_by_default |

**Second reader's corrections to this table:**

1) raw.bin.gz: besides stale bytes under debug filing (T04), raw bytes are also omitted by the session shape guard on every short read (EndOfData) and every 7200 dpi pass. The guard compares layout.lines (requested params.lines) with the decoded or realigned image height (AF1), and does not only trip on 'different pass' mismatches. 2) Library entry pixels: add 'demo Prescan jobs' to the corrected list. DemoScanner has no last_pixels_raw, so session._prescan files the corrected image with the source entry's reference. 3) Framing prescan.tif: tools/scan_roll.py --dry-run writes rolls/<name>/prescanNN.tif only; it files no library entry, and debug is forced off (T18). The session dry run does file entries with raw_prescan. 4) Session shading cache: the demo never writes calibration/shading.npz (DemoScanner.ensure_shading only returns a dict), so 'written_by' is DirectScanner only. 5) scan.json 'scan' block: roll_index, roll_position and the demo marker are dropped (T11). scan.film for roll prescans is always 'negative' on the hardware path (T12). 6) ccd_mask.bin is the per-pass mask (direct.py:2667-2672). The calibration pass's own mask is overwritten and never stored.

## What the operator can do

- Run `make test`, `uv run python tasks.py test` or `make test-all` with no scanner: hardware tests are deselected by addopts, and tests that need library/, scans/, captures/ or the frame-edge data skip.
- Run `uv run pytest tests/ -m hardware` with the scanner attached and Stefan's agreement: nine read-only INQUIRY and READ STATE tests.
- Run `FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py` on a machine that has research/frame-edge/data.
- In the window: calibrate (measure, reuse or off), prescan, scan, walk or scan a roll, approve positions on the contact sheet, move whole frames or nudge, Stop (lands between frames), Force abort (after typing ABORT), Save As, remove a result and optionally its library entry.
- Run `tools/library.py list|verify|reconstruct|duplicates|migrate-raw|migrate-direction`; the rewriting actions are dry runs unless given --write or --delete.

## What the operator should not do

- Do not export RPS7200_DEBUG=1 in the shell used for `make test`: test_filing_is_off_by_default fails.
- Do not treat a green suite as proof that GUI single scans are filed raw, that demo entries reconstruct, or that DirectScanner.scan() works end to end; none of these is exercised (T01, T02, T06).
- Do not use `--demo --library library`: demo entries (corrected pixels, borrowed raw, no demo marker) would mix into the real library.
- Do not answer 'No' to 'Keep the library entry?' unless the pass's raw bytes are truly unwanted.
- Do not point the output folder at removable or network storage for a long roll: a failed copy stops that frame from being filed (T03).
- Do not walk a strip twice under the same roll name, including the default date name: the first survey.json and its prescans are replaced.
- Do not click-to-aim or nudge while a roll runs, especially after pressing Stop.
- Do not expect `tools/scan_roll.py --dry-run` to leave library entries, or `tools/library.py reconstruct` to pass 7200 dpi entries.
- Do not rely on debug filing when calling scan() or prescan() without keep_raw=True after an earlier keep_raw pass (T04).

## Mistakes nothing guards against

- Pressing Scan in the window files the corrected image as raw. Save As and the 1:1 view then correct it a second time, and nothing warns (T01).
- Clicking a roll prescan with 'aim' ticked during a roll queues a Move behind the roll and silently cancels a Stop already pressed; the Stop button stays disabled (T14).
- A second same-day walk with no name overwrites the first walk's survey.json and prescanNN.tif, and leaves approved.json pointing at pictures that are gone (T15).
- An output folder that becomes unwritable mid-roll drops every later frame's library entry, with only a log line per frame (T03).
- A crash while roll.json is rewritten makes the next resume treat the roll as new and lose the done-frame record (T16).
- A crash during library.save leaves a partial entry that list, verify and reconstruct never report (T08).
- In --demo, a 7200 dpi scan with shading on succeeds, although the scanner refuses it, and a scan with shading off is still corrected, so the demo teaches the wrong behaviour (T17).
- Answering 'No' in the delete dialog permanently rmtree's the entry, including raw.bin.gz (T22).
- With RPS7200_DEBUG=1, a script calling scan(auto_exposure=True) or prescan() without keep_raw files the entry with another pass's raw bytes (T04). If filing fails at close(), all spooled scans are deleted (T05).
- A B&W or slide walk files its prescans labelled film 'negative' (T12).
- Launching the window from another working directory silently starts a new library/, calibration/ and rolls/ there, because all three default paths are relative to cwd (gui.py main: `home = Path(".")`).

## Dataflow notes

Production flow (as the tests should see it). The device's bytes enter at DirectScanner.read_planes (direct.py:1440). The INDEX blob is joined, and with keep_raw it is stored in self.last_raw/last_raw_layout (1518-1531). decode_index (1552) turns it upright and records last_read_direction. scan() (2423) realigns 7200 dpi (2697), binds raw_pixels (2708), applies shading (apply_shading, shading.py:196) to the returned image, builds meta (2760-2804), calls _debug_capture(raw_pixels, meta) (2811; that step reads capture_record(), which may be stale, T04), and publishes last_pixels_raw and last_scan_meta (2818-2825). prescan() (1682) wraps scan() at 8-bit/300 dpi. scan_roll (3292) copies last_pixels_raw and last_scan_meta onto RollFrame.raw_image, raw_prescan and prescan_meta (3497-3498, 3656-3660), leaving the raw bytes on the scanner for capture_record().

ScanSession owns the device on a worker thread. The seam is ScanSession(open_scanner=...) (session.py:1191), where DemoScanner or the test doubles stand in. _prescan (1401) passes raw_image=last_pixels_raw. _scan (1440) does not (T01). _roll (1608) passes rf.raw_image for frames, rf.raw_prescan for dry-run prescans, and the corrected rf.prescan as the frame entry's prescan (T07). _file (2031) reads capture_record(), drops raw bytes whose declared lines, width or channels disagree with the image (2069-2093), and queues a job on FrameWriter. FrameWriter._write (1060) writes the oriented, corrected delivered files first, then library.save (131) with raw_image or the image (T03). library.save writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz, then scan.json last (non-atomic), then reindex. Out of the library: library.corrected (338) re-applies shading for the GUI Save As and 1:1 view (gui.py:3864, 4110); library.reconstruct (439) re-decodes raw and compares, without the 7200 realign (T09).

Where the tests cut in. (1) At the transport: conftest.FakeTransport, StripTransport and test_session.FakeTransport answer command(); they are used for command payloads, advance, retreat, seek and set_mode, and the pass always fails at the image read (test_fast_infrared.py:184). (2) At the pass: FakeRoll, FakeScanner (test_metering), ScannerOnStrip, StripScanner, FakeBracketScanner and FakeCorrectingScanner override scan/prescan, so decode, shading and meta in scan() are never exercised (T06). (3) At the session seam: test_session.FakeScanner returns one array for raw and corrected and a constant 64-byte capture, so raw-versus-corrected filing and byte-to-pixel agreement are not checked (T01, T13). (4) At FrameWriter.submit or library.save directly: test_roll_writer and test_library, with hand-built meta and placeholder raw; writer failure tests use library=None (T03). (5) Tools through load_tool plus monkeypatched DirectScanner: tools/scan.py and tools/scan_roll.py are the only places the raw/corrected distinction is asserted on what was filed. (6) The demo is tested standalone, re-filing by hand with library.save(**capture), plus identity checks on borrowed methods and constants (T02, T17). (7) The GUI is mostly tested through pure functions and inspect.getsource; about 25 tests use a real Tk window with DemoScanner('library') and skip without a display. Real-data tests (stored library entries, scans/, captures/, frame-edge data, hardware) skip or are deselected everywhere but the owner's machine (T20).
