# The test suite

Area key `tests`. 18 findings: 1 high, 10 medium, 7 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

The suite is 1,442 tests in 47 files (~25k lines; test_gui.py alone has 336). It is strong where it works on stored bytes and pure functions. test_library.py builds INDEX byte streams and checks that save, load, reconstruct and compact round-trip exactly, including the bottom-up and 7200 dpi cases. The demo tests push stand-in passes through the real ScanSession into a library and check that each entry reconstructs to "identical" and that its corrected pixels equal what the window showed. The manifest, resume and numbering logic is covered in depth.


The weaknesses sit at the two ends that the owner's exactness requirement depends on:
- **The real pass.** `DirectScanner.scan()`, `_read_pass()` and `calibrate_shading()` never run to completion in any test, because no fake answers GET PARAMETERS or the image READ. So the meta, raw pixels, raw bytes and command log that every real entry is built from are checked only by source-text greps and hand-built metas.
- **The plumbing between pass and entry.** The session, the scan tools and FrameWriter are tested with doubles whose `raw` bytes cannot decode to the pixels they file. The only end-to-end "filed entry reconstructs" check goes through `DemoScanner`.

Several tests pin behaviour that is contrary to the requirement: a real roll's prescans are filed without raw bytes. Others are tautological or rest on source text: 126 `inspect.getsource` assertions, two `or True` asserts.


A cluster of tests skips wherever the suite runs automatically: hardware, captures, the stored library, frame-edge parity, and Tk-less Python.


The GUI window fixture reads and rewrites the operator's real repo-root `gui-settings.json`, and the current file carries test residue.


Demo-parity tests check that attributes exist or are identical objects, not that behaviour matches. They miss a retyped `nudge` arithmetic, missing meta keys and a different `ensure_shading(reuse)` answer.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [T-04](#tests-t-04) | high | library-completeness | confirmed | Tests enforce that a real roll's prescans (and every pass only debug filing keeps) are stored without raw bytes, corrected, with their pass record dropped |
| [T-01](#tests-t-01) | medium | test-gap | partly | DirectScanner.scan()/_read_pass()/calibrate_shading() never run to completion in any test; the record every real entry is built from is checked only by source grep and hand-built metas |
| [T-02](#tests-t-02) | medium | data-integrity | confirmed | GUI tests read and rewrite the operator's real repo-root gui-settings.json (test residue is present in the file now) |
| [T-03](#tests-t-03) | medium | data-integrity | confirmed | A failed library filing loses the pass everywhere (no delivered copy, claimed debug spool deleted), and no test covers it |
| [T-05](#tests-t-05) | medium | test-gap | partly | Every non-demo filing path is tested with raw bytes that cannot decode to the filed pixels; no test shows a session/tool/FrameWriter entry reconstructs |
| [T-06](#tests-t-06) | medium | test-gap | partly | Debug filing, the only record of metering probes and hold/aim prescans, is never shown to file raw bytes that reconstruct; two tautological asserts |
| [T-07](#tests-t-07) | medium | test-gap | confirmed | Interrupted writes are simulated by hand, never exercised; compact's write order produces a false checksum alarm that nothing repairs |
| [T-08](#tests-t-08) | medium | library-completeness | confirmed | The kept calibration bytes are never shown to reproduce the reference, and nothing in the code reads them |
| [T-10](#tests-t-10) | medium | test-gap | confirmed | The USB control-plane state machine has no offline test; conftest points to a FakeUsb that does not exist |
| [T-11](#tests-t-11) | medium | test-gap | partly | No test checks that a delivered file holds the corrected pixels; the GUI's single delivery function is untested |
| [T-A1](#tests-t-a1) | medium | data-integrity | found-by-verifier | After a force abort the session never calls close(), so debug-spooled passes are never filed and nothing says where they are |
| [T-09](#tests-t-09) | low | demo-divergence | partly | Demo-parity tests check attribute existence and object identity, not behaviour; the demo retypes nudge's arithmetic, omits meta keys, and answers ensure_shading(reuse) differently |
| [T-12](#tests-t-12) | low | test-gap | partly | Tests that skip wherever the suite runs automatically |
| [T-13](#tests-t-13) | low | test-gap | confirmed | Source-text and tautological assertions stand in for behaviour in the places that matter most |
| [T-14](#tests-t-14) | low | user-error | confirmed | Destructive and quit/abort operator paths are untested: 'No' to keep-entry, quit while busy, force abort mid-read, duplicates --delete |
| [T-15](#tests-t-15) | low | test-gap | confirmed | Tests depend on the developer's environment and working directory |
| [T-16](#tests-t-16) | low | doc-mismatch | confirmed | Test docstrings and comments claim coverage the code does not have |
| [T-A2](#tests-t-a2) | low | doc-mismatch | found-by-verifier | settings.py claims the RPS7200_SETTINGS variable keeps tests off the real file; no test sets it |

## Findings in full

<a id="tests-t-04"></a>

### T-04 -- Tests enforce that a real roll's prescans (and every pass only debug filing keeps) are stored without raw bytes, corrected, with their pass record dropped

**Severity** high · **Category** library-completeness · **Verdict** confirmed

**Where:** `tests/test_session.py:828-833`, `tests/test_session.py:887-890`, `rps7200/session.py:2527-2587`, `rps7200/session.py:2605-2616`, `rps7200/library.py:266-270`, `rps7200/library.py:376-382`, `tools/scan_roll.py:655-700`, `tools/scan_roll.py:728-756`

With RPS7200_DEBUG off, which is the default for `make run` and the tools, a real roll keeps each frame's prescan only as a corrected prescan.tif. Its raw bytes and raw pixels are discarded (although RollFrame.raw_prescan carries them), its correction cannot be recomputed with newer code, and its command log, exposure and shading report are not recorded. The same holds for the replaced pre-correction prescan, the hold/aim verification passes and the metering probes, which are filed only if the operator exported RPS7200_DEBUG=1. The owner requires the exact bit data of every scan, and the tests lock in the opposite.

**Evidence (from the code):**

```text
test_a_real_roll_does_not_file_its_prescans_separately: `run(Roll(frames=3, dry_run=False, name="real"), tmp_path) ... assert len(entries) == 3; assert not any("prescan" in e["tags"] for e in entries)`. session._roll files a prescan with `raw_image=rf.raw_prescan` only under `if job.dry_run:` (2536). A real roll passes `prescan=rf.prescan` (the corrected 8-bit picture) into the frame's entry. library.save keeps only `"read_direction"` and `"carriage_state"` of `prescan_meta` (378-382), dropping the prescan pass's commands, exposure and shading report. The pre-correction prescan is written with `file_entry=False` (2586). tools/scan_roll.py does the same (`prescan=frame.prescan`, with the comment "it has no raw bytes of its own").
```

**Failure scenario:** A registration or edge-detection question comes up months later about a real roll's frame 7. Its prescan.tif was corrected with that day's code and has no raw.bin. It cannot be re-decoded, re-corrected, or checked for read direction from its line tags; the pass's commands are gone. The reference for `--approved` holds on that roll is likewise only the corrected picture.

**Fix:** File every prescan of a real roll as its own entry, with raw bytes, a reference and its own meta, linked by roll_membership kind "prescan". Do the same for the replaced before-prescan while its bytes are the last pass. Alternatively store raw.prescan.bin with its layout inside the frame entry. Replace test_session.py:828-833 and 887-890 with tests asserting that raw bytes exist and reconstruct for each prescan. Record the full prescan_meta in scan.json.

<details><summary>Second reader's check</summary>

session._roll files a prescan with raw_image=rf.raw_prescan only under `if job.dry_run:` (session.py:2536-2565). A real roll passes prescan=rf.prescan (corrected) and prescan_meta into the frame's _file call (2610-2616). library.save records only file, read_direction and carriage_state for the prescan (library.py:376-382), and writes prescan.tif from the corrected array (270). tools/scan_roll.py does the same (prescan=frame.prescan, prescan_meta=frame.prescan_meta at ~745-750); it files raw prescans only on --dry-run (662-686). The prescan-before image is written with file_entry=False (session.py:2586) and in scan_roll.py with a plain tiff.write. test_session.py:828-833 and 887-890 assert this arrangement. With debug off, which is the default, a real roll's prescan raw pixels and bytes are discarded even though RollFrame.raw_prescan holds the raw pixels. That conflicts with the owner's requirement that the exact bits of every scan be kept.

</details>

<a id="tests-t-01"></a>

### T-01 -- DirectScanner.scan()/_read_pass()/calibrate_shading() never run to completion in any test; the record every real entry is built from is checked only by source grep and hand-built metas

**Severity** medium · **Category** test-gap · **Verdict** partly

**Where:** `rps7200/direct.py:2842-3235`, `rps7200/direct.py:2128-2383`, `tests/test_roll.py:724-749`, `tests/test_library.py:423-455`, `tests/test_fast_infrared.py:185-199`, `tests/test_scanner_api.py:229-241`, `tests/conftest.py:70-103`, `tests/test_device_suspect.py:132-136`

DirectScanner.scan(), prescan() and calibrate_shading() never run to completion in the suite. _read_pass is run whole once, but only with its device primitives stubbed (test_device_suspect.py:132-136). The meta dict at direct.py:3155-3212, the raw/corrected split, last_raw_layout (lines_received), the command log, and the debug spool of a real pass are checked only by source grep (test_roll.py:724-749) and by hand-typed metas (test_library.py:423-455).

**Evidence (from the code):**

```text
No fake answers GET PARAMETERS/COPY/READ image data: conftest FakeTransport `reply = self.replies.get(command[0]) ... return reply if reply is not None else b""` ("Anything unlisted answers empty"). The pieces are then asserted by text: test_roll.py:741-749 `source = inspect.getsource(DirectScanner.scan); assert "raw_pixels = image" in source ... assert "self._debug_capture(raw_pixels, meta)" in source` (its docstring: "Pinned at the seam rather than end to end: `scan()` needs a device"). The scans that are driven swallow every failure: test_fast_infrared.py:195-198 `try: s.scan(shading=False, auto_exposure=False, **kw) except Exception: pass`; test_scanner_api.py:238-241 `except Exception: pass  # the fake cannot serve a real pass`. test_library.py:430 claims "This asserts the list keeps up with what scan() puts in meta" but the meta at 433-441 is typed by hand.
```

**Failure scenario:** A refactor stops calling the command log's `stop()` on one path, reorders `stagger_realigned` against `height`, or makes `raw_pixels` alias an array that apply_shading modifies. All 1,442 tests stay green. Every real entry filed afterwards lacks extra.commands, or has `height` disagreeing with the bytes (so raw_bytes_disagree drops the raw bytes), or has corrected pixels labelled raw, which reconstruct reports as "decode CHANGED". This is the 26-entry incident CLAUDE.md describes.

**Fix:** Add a byte-level fake transport to conftest. It should answer READ STATE, TEST UNIT READY, READ GAIN/OFFSET, GET PARAMETERS, COPY (the CCD mask), READ (encode_index bytes, then NoDataYet/EndOfData) and REQUEST SENSE, so that scan(), prescan() and calibrate_shading() complete. Then assert:
- the meta keys are covered by library.SCAN_FIELDS/_RECORDED;
- decode_index(last_raw) equals last_pixels_raw, with the 7200 dpi realignment replayed;
- after library.save(last_pixels_raw, meta, **capture_record()) the entry reconstructs to "identical" and corrected() equals the returned image;
- extra.commands is non-empty;
- the debug spool holds the same bytes.
Delete the source-grep test once this exists.

<details><summary>Second reader's check</summary>

This is mostly accurate. FakeTransport.command answers every opcode it has not been given with b"" (conftest.py:89-103). test_roll.py:724-749 pins scan() by source grep. test_fast_infrared.py:185-199 and test_scanner_api.py:238-241 swallow every exception, and test_library.py:433-441 types its meta by hand. No test drives DirectScanner.scan(), prescan() or calibrate_shading() to the end; test_device_suspect.py:95 only checks that calibrate_shading refuses. One claim is wrong: _read_pass does run to completion. test_device_suspect.py:132-136 (test_the_pass_that_was_read_is_returned_whole) runs it with start_scan/get_parameters/read_planes/get_ccd_mask stubbed out, although that only covers the orchestration. The core point stands. Nothing checks end to end that the meta assembled in scan(), last_raw/last_raw_layout, last_pixels_raw and the _debug_capture spool describe the same pass and reconstruct. I rate it medium rather than high: it is a coverage gap, not a defect anyone has observed.

</details>

<a id="tests-t-02"></a>

### T-02 -- GUI tests read and rewrite the operator's real repo-root gui-settings.json (test residue is present in the file now)

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tests/test_gui.py:1107-1130`, `tools/gui.py:386-388`, `tools/gui.py:692-719`, `tools/gui.py:2591-2602`, `tools/gui.py:3430-3456`, `rps7200/settings.py:25-53`, `tests/test_gui.py:4495-4523`

The GUI window fixture loads and, through open_roll/_note_roll_opened and on_close, rewrites the operator's repo-root gui-settings.json: controls, output, window, rolls and sheet. The live file carries test residue. The comment at settings.py:27-28 claims the RPS7200_SETTINGS variable exists so tests never write the real file, but no test sets it.

**Evidence (from the code):**

```text
The fixture builds `gui_mod.ScannerGui(root, session, demo=True)` with no settings_path. ScannerGui then does `self.remembered = settings.load(settings_path)`, and `settings.path(None)` returns `DEFAULT_PATH = Path("gui-settings.json")` unless RPS7200_SETTINGS is set, which no test module sets (the only setenv is inside test_settings.py). `open_roll` calls `_note_roll_opened`, whose `rolls[Path(folder).name] = {...}; self._remember()` writes the whole payload back. The checkout's live gui-settings.json holds `rolls: ['first', 'walk']`. Those are exactly the folder names `_walked_folder` ("walk") and test_opening_another_roll_keeps_what_the_open_sheet_held (`.rename(tmp_path / "first")`) create. Its `sheet` section also holds dozens of generated walk names such as 2026-09-25-003904.
```

**Failure scenario:** A test sets `app.v_film.set("bw")` or `app.v_dryrun.set(True)` (test_gui.py:1150, 4575) and later triggers `_remember`. On the owner's next real launch the window restores film=bw or dry run. A colour roll is then scanned without infrared and delivered as one channel, or the Roll button walks instead of scanning, with nothing saying why. A test roll named like one of the operator's real uncommissioned walks overwrites its remembered sheet decisions.

**Fix:** In the window fixture, pass `settings_path=tmp_path / "gui-settings.json"`. Also add an autouse conftest fixture that sets RPS7200_SETTINGS (and RPS7200_DEBUG_ROOT) to tmp paths for the whole suite. Add a test asserting that nothing outside tmp_path is modified.

<details><summary>Second reader's check</summary>

The window fixture (test_gui.py:1107-1130) builds ScannerGui(root, session, demo=True) with no settings_path. ScannerGui then calls settings.load(settings_path) (gui.py:387-388), and settings.path(None) falls back to DEFAULT_PATH = Path("gui-settings.json") unless RPS7200_SETTINGS is set (settings.py:25-53). No conftest or test_gui fixture sets that variable; the only autouse fixture in test_gui.py:39 patches DemoScanner._calibrated. The fixture's teardown does not call _remember, but several tests do reach it: open_roll leads to _note_roll_opened, which calls _remember (gui.py:2591-2602; tests at 4374, 4503-4505, 4518, 4930-4991), and on_close calls _remember (test_gui.py:4521, gui.py:3453). The live repo-root gui-settings.json has rolls {'first','walk'} and sheet keys 2026-09-25-003904 and so on, which matches the folder names those tests create. The mechanism is real. The specific failure scenario the finding gives (a test sets v_film to bw and then saves) is speculative, but the controls saved on each open_roll are whatever the test left, so leakage of that kind is possible. There is also a doc-mismatch the reader missed. settings.py:27-28 says PATH_ENV exists "so a test never writes the real one", but no test uses it.

</details>

<a id="tests-t-03"></a>

### T-03 -- A failed library filing loses the pass everywhere (no delivered copy, claimed debug spool deleted), and no test covers it

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1587-1599`, `rps7200/session.py:1650-1673`, `rps7200/session.py:2868-2876`, `rps7200/session.py:1970-1980`, `rps7200/direct.py:1055-1059`, `rps7200/direct.py:1134-1145`, `tools/scan.py:280-300`, `tools/scan.py:366-375`, `tests/test_session.py:469-489`

The design keeps the library entry as the single source and writes copies after it. The consequence is that a library write failure (library drive full or unwritable, a permission error, a bug in save) drops the corrected image and the raw bytes held in the job. With RPS7200_DEBUG=1, the one independent copy is the debug spool, and it was already deleted because the pass had been claimed before its filing succeeded. The only survivor is the downscaled working copy in the window.

**Evidence (from the code):**

```text
FrameWriter._write calls `entry = library.save(...)` (1656) before `for path in job.get("paths") or ():` (1673), so an exception from save skips every delivered copy, including the roll's own frameNN.tif. `_file` claims first, with `claim(raw_image)` (2875), and only then calls `self._writer.submit(...)`. `_run`'s finally runs `self._scanner.close()` (1974) before `self._writer.finish()` (1979). close() runs _debug_flush, which does `if item.get("claimed"): ... stuck += self._debug_unlink(item); continue` without knowing whether the claimer's filing succeeded. tools/scan.py claims inside `with DirectScanner(...)` and runs `library.save` after the context exits, with no try around the loop. test_a_frame_the_writer_could_not_file_is_not_done forces `raise OSError(28, ...)` from library.save but asserts only the manifest's `done`/`filing_error`.
```

**Failure scenario:** The library sits on an external drive that fills at frame 12 of a roll, while the output folder is on the internal disk. Frame 12's library.save raises OSError 28, so no frameNN.tif and no output-folder copy is written. The session stops the roll. At close, _debug_flush deletes the claimed spool of frame 12. The 3600 dpi pass exists nowhere.

**Fix:** Write the delivered copies even when library.save fails, and keep the raw bytes in a salvage spool (next to the output or in the debug spool). Only mark a pass claimed, or unlink its debug spool, after the claimer reports a successful filing: claim in on_filed, not before submit. Add tests where library.save raises and assert that the copies and the spooled raw bytes survive.

<details><summary>Second reader's check</summary>

This matches the code. In FrameWriter._write, library.save (session.py:1650-1662) runs before the copy loop (1667+). An exception from save propagates to _run (1587-1599), so no frameNN.tif and no output copy is written. _file calls claim(raw_image) at 2872-2876 before self._writer.submit. _run's finally calls self._scanner.close() at 1973-1974 before self._writer.finish() at 1979. DirectScanner.close runs _debug_flush (direct.py:1145), which unlinks claimed items unconditionally (direct.py:1055-1059). In tools/scan.py, hold() calls s.debug_claim(raw) inside the device context (294-296). The library.save loop at 366-375 has no try, and --out is written after that loop, so a save failure loses the corrected output as well. test_session.py:469-489 injects OSError(28) but asserts only on the manifest. Without RPS7200_DEBUG=1 there is no spool at all, so the pass is lost in either case.

</details>

<a id="tests-t-05"></a>

### T-05 -- Every non-demo filing path is tested with raw bytes that cannot decode to the filed pixels; no test shows a session/tool/FrameWriter entry reconstructs

**Severity** medium · **Category** test-gap · **Verdict** partly

**Where:** `tests/test_session.py:111-119`, `tests/test_session.py:232-238`, `tests/test_scan_tool.py:73-74`, `tests/test_scan_roll_tool.py:47-48`, `tests/test_roll_writer.py:32-37`, `rps7200/session.py:734-766`, `tests/test_demo.py:1238-1380`

The session's Scan, Prescan and walk-prescan filing paths, through the real ScanSession and FrameWriter, are shown to reconstruct identically, but only with DemoScanner at the seam (test_demo.py:1238-1380). Real roll frames through the session, tools/scan.py, tools/scan_roll.py and roll_writer are tested only with placeholder raw bytes that cannot decode to the filed pixels. raw_bytes_disagree compares shape alone.

**Evidence (from the code):**

```text
test_session FakeScanner.capture_record: `"raw": b"\x00\x01" * 32, "raw_layout": {"format": "index", "width": 36, "lines": 24, "channels": 4}`. That is 64 bytes for a 36x24x4 pass, with no bytes_per_line. test_scan_tool: `self.last_raw = f"pass-{len(self.scans)}".encode()`. test_scan_roll_tool: `self.last_raw = b"raw-bytes"`. test_roll_writer: `"raw": raw` with `raw=b"raw bytes"`. The strongest session assertion is test_a_filed_scan_keeps_the_raw_bytes: `assert library.read_raw(entry) == b"\x00\x01" * 32`. The only `library.reconstruct(...)` calls on session-filed entries are in test_demo.py (1274, 1335, 1374), through DemoScanner.
```

**Failure scenario:** A change makes `_file` read capture_record() one pass late, for example after a verification prescan of identical shape (the case the `_file` docstring admits the guard is blind to), or files an 8-bit prescan's bytes beside a same-shaped 16-bit pass. Entries decode to a different photograph and every test passes. `make reconstruct` on the real library is the first thing that would notice, after the scans are taken.

**Fix:** Make the doubles' capture_record() return encode_index(raw_pixels) with a complete layout. Add at least one test per filing path (session Scan, Prescan, roll frame, walk prescan, tools/scan.py single pass and bracket, tools/scan_roll.py frame and dry-run prescan) asserting that library.reconstruct(entry) starts with "identical" and that corrected() equals the delivered file. Consider strengthening raw_bytes_disagree to decode the bytes and compare them with raw_image.

<details><summary>Second reader's check</summary>

The doubles do carry placeholder bytes: test_session.py:111-119 (64 bytes for a 36x24x4 layout), test_scan_tool.py:73-74, test_scan_roll_tool.py:47-48, test_roll_writer.py:32-37 and test_scan_roll_calibration.py:38-39. raw_bytes_disagree (session.py:734-766) compares lines, width and channels only. The claim that no test shows a session or FrameWriter entry reconstructs is wrong, though. test_demo.py:1238-1279 runs the real ScanSession with DemoScanner at the seam through Prescan, several Scans and an RGBI Scan. It asserts library.reconstruct(path) starts with 'identical' and that corrected() equals the shown image. test_demo.py:1307-1335 does the same for session prescans, and 1352-1380 for a dry-run walk, both through the real FrameWriter. Still uncovered: a real (non-dry-run) roll frame through the session, tools/scan.py single passes and brackets, tools/scan_roll.py frames and dry-run prescans, and DirectScanner's own capture_record pairing.

</details>

<a id="tests-t-06"></a>

### T-06 -- Debug filing, the only record of metering probes and hold/aim prescans, is never shown to file raw bytes that reconstruct; two tautological asserts

**Severity** medium · **Category** test-gap · **Verdict** partly

**Where:** `tests/test_roll.py:752-758`, `tests/test_roll.py:771-791`, `tests/test_roll.py:836-859`, `tests/test_roll.py:899-920`, `rps7200/direct.py:908-998`, `rps7200/direct.py:1031-1128`

_debug_capture/_debug_flush (direct.py:908-1128) are tested for spooling, claiming, cleanup and failure retention. No test files a spooled pass that carries raw bytes, a reference and a mask, and then checks that the entry's raw.bin.gz, raw_layout, shading.npz and ccd_mask.bin match and that it reconstructs. Two asserts in test_roll.py (756, 791) are tautologies.

**Evidence (from the code):**

```text
test_filing_is_off_by_default: `assert os.environ.get("RPS7200_DEBUG") is None or True`. test_nothing_is_written_while_the_device_is_open: `assert (entries[0] / "raw.bin.gz").exists() or True   # raw only when kept`. The `_debug_scanner()` doubles have `last_raw` None, so the flushed entries carry no raw at all. test_scans_are_spooled_to_disk_not_held_in_ram spools `s.last_raw = b"\x00" * 100_000` against a 300x400x4 image but never closes, files or reconstructs it.
```

**Failure scenario:** _debug_capture writes `raw_path` from a stale `record`, or `_debug_flush` passes `raw_layout` from the wrong item after a reordering. Every debug-filed probe entry then has missing or wrong bytes, and the two `or True` asserts cannot fail.

**Fix:** Remove both tautologies; make test_filing_is_off_by_default delenv RPS7200_DEBUG. Add a test that spools a pass built with encode_index (with reference and mask), closes, and asserts the filed entry's read_raw equals the bytes, reconstruct is "identical" and ccd_mask.bin matches.

<details><summary>Second reader's check</summary>

The substance is right. test_roll.py:756 (`is None or True`) and :791 (`.exists() or True`) are tautologies. No test flushes a spool that holds raw bytes and then checks read_raw, raw_layout, shading.npz, ccd_mask.bin or reconstruct on the filed entry. test_scans_are_spooled_to_disk_not_held_in_ram (899-920) spools raw but never closes the scanner. test_a_spooled_pass_describes_itself (836-859) checks only the spool side. test_a_pass_its_caller_files_is_not_filed_twice checks only scan.tif. The code locations are wrong: direct.py:469-559 is the command-recording wrapper and 592-686 is __init__ state. The debug code is at direct.py:908-1128.

</details>

<a id="tests-t-07"></a>

### T-07 -- Interrupted writes are simulated by hand, never exercised; compact's write order produces a false checksum alarm that nothing repairs

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `rps7200/library.py:414-453`, `rps7200/library.py:257-264`, `rps7200/library.py:394-399`, `rps7200/library.py:1045-1064`, `rps7200/direct.py:749-803`, `rps7200/session.py:1986-1994`, `tests/test_library.py:746-763`, `tests/test_library.py:818-842`

The atomicity claims ("whole or not at all", "an interruption leaves a readable entry") hold only as code-reading arguments. A save interrupted mid-file, a compact interrupted between the TIFF swap and the record, and a calibration archive cut short are all untested. The compact case yields a verify error on intact pixels, which is the kind of false alarm that trains people to ignore verify. A killed window's plain entries are never compacted by anything.

**Evidence (from the code):**

```text
The only INCOMPLETE test builds the directory itself: `cut.mkdir(); (cut / library.INCOMPLETE).write_text("", ...); (cut / "scan.tif").write_bytes(b"half")`. No test makes library.save fail part way. compact runs `os.replace(temp, path / RAW_FILE)` (438), then `_replace_tiff(path / name, pixels, ...)` (445), and only then `_write_atomic(path / "scan.json", ...)` (451). An interruption after 445 leaves record image.sha256 describing the old uncompressed TIFF, so verify reports `scan.tif does not match its checksum`. `compact(` has one caller (session.py:1992), limited to the current session's `self._writer.uncompressed`, and there is no CLI to re-run it. compact is tested only on scan.tif, never on prescan.tif or on the checksum-mismatch branch. archive_calibration writes `data.bin` then `calibration.json` with plain write_text and no marker, and verify never looks at calibration archives. reindex uses `index.write_text(...)`.
```

**Failure scenario:** The window is closed and the process killed (for example Windows logoff) while `library.compact` runs over five plain entries. One entry has scan.tif recompressed but the old sha256 in scan.json, and `make verify` reports it as corrupted forever. A calibration archive killed after data.bin has no calibration.json saying its width or bytes_per_line, so its bytes cannot be reduced again.

**Fix:** Add fault-injection tests: monkeypatch tiff.write, ShadingReference.save or gzip to raise inside save, compact and archive_calibration, then assert that INCOMPLETE or the previous state remains and that verify reports it correctly. In compact, update each file's checksum in the record atomically together with its swap, or have verify fall back to comparing decoded pixels. Add `tools/library.py compact` and a verify pass over calibration/ archives with an INCOMPLETE marker there too.

<details><summary>Second reader's check</summary>

compact (library.py:414-453) runs os.replace on raw.bin.gz at 438, then _replace_tiff and an in-memory sha update for each TIFF (440-449), and writes scan.json only at 451. If it is interrupted after a TIFF swap, record image.sha256 or files[prescan.tif] describes the old file, and verify compares file sha256 (library.py:1068+ lines 30-39 of the function), so it reports a checksum mismatch. The plain raw.bin survives and would let compact finish, but compact's only caller is session.py:1986-1994, for the current writer's list, and there is no CLI. archive_calibration writes data.bin, the mask, the npz and then calibration.json with plain writes and no marker (direct.py:781-803). reindex uses index.write_text (library.py:1064), but index.json is derivable. The only INCOMPLETE test builds the directory by hand (test_library.py:746-763). compact is tested only on the happy path (test_library.py:818-842).

</details>

<a id="tests-t-08"></a>

### T-08 -- The kept calibration bytes are never shown to reproduce the reference, and nothing in the code reads them

**Severity** medium · **Category** library-completeness · **Verdict** confirmed

**Where:** `rps7200/direct.py:749-803`, `rps7200/direct.py:806-862`, `rps7200/direct.py:2345-2356`, `rps7200/direct.py:load_shading (shading_origin 'loaded' without archive)`, `tests/test_scanner_api.py:245-275`

No code or test reduces a kept calibration's data.bin again, or checks that it reproduces the archived shading.npz. Entries link to the archive only through extra.shading_origin.archive, which is set only when that session calibrated. An entry corrected by a reused (loaded) reference records only the cache path, which the next calibration overwrites, so its calibration bytes cannot be identified.

**Evidence (from the code):**

```text
ensure_shading comments that the reference is a reduction of the calibration's own bytes and that "Without this no correction in the library could ever be recomputed from scratch" (852). The test archives unrelated data: `_calibrated(s, data=b"\x01\x02" * 100)` with `ref = reference()` built from np.full arrays, and asserts only `read_bytes() == b"\x01\x02" * 100` and that shading.npz exists. A repository-wide grep shows no reader of data.bin, and calculate_shading is called only at direct.py:2351 and in test_shading.py. Entries reach the archive only through `extra.shading_origin.archive`, a path string pointing outside the entry.
```

**Failure scenario:** calculate_shading's line-tag parsing or the bytes_per_line recorded in calibration.json drifts, or archive_calibration stores `bytes_per_line` for a different width. Nothing fails. Years later the recomputation the archive exists for produces a different reference, or cannot run, and no one can tell which archive belongs to which entry once the library has been copied.

**Fix:** Add `tools/library.py recalibrate` (or a library function) that reduces data.bin with today's calculate_shading and compares the result to the stored shading.npz. Test it with calibration bytes synthesised in INDEX format through the real calibrate_shading on the byte-level fake from T-01. Consider copying (or hard-linking) the archive, or at least its sha256, into each entry corrected by it, and have verify check it.

<details><summary>Second reader's check</summary>

A repository-wide grep finds no reader of data.bin. calculate_shading is called only at direct.py:2351, apart from the tests. test_scanner_api.py:245-275 archives b"\x01\x02"*100 unrelated to the reference and checks only the bytes and the files' existence. The finding also understates the problem. An entry corrected by a reused reference gets _shading_origin = {action: loaded, path, file_modified_utc, loaded_utc} from load_shading, with no 'archive' key. Only the calibrating ensure_shading sets 'archive' (direct.py:858-860). The cache file itself is replaced by every new calibration (save_shading). So every entry scanned with a reused reference has no record of which archived calibration bytes produced its reference. calibration.json holds pixels_per_line and bytes_per_line but no split ratio and no reduction code version.

</details>

<a id="tests-t-10"></a>

### T-10 -- The USB control-plane state machine has no offline test; conftest points to a FakeUsb that does not exist

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:693-716`, `rps7200/usb_transport.py:799-866`, `tests/conftest.py:70-77`, `tests/test_transport_reads.py:26-55`, `tests/test_usbpcap.py:333-400`, `pyproject.toml:90`

`_command` decides retry versus busy-poll versus data-out versus data-in versus CHECK CONDITION for every command the driver sends. A mistake there (re-sending a command on BUSY, not draining BUSY after a data-in, treating CHECK as success after data-out) is the class of fault CLAUDE.md associates with wedges. It is exercised only on real hardware, only through INQUIRY and READ STATE, and only when someone opts in.

**Evidence (from the code):**

```text
conftest.py:73-77: "It deliberately implements only `command()`: anything reaching further into the transport should be tested against `FakeUsb` instead, which fakes libusb." A repository-wide grep for FakeUsb returns only that line. No test references `_send_command`, `_wait_not_busy`, `_control_out`, `_control_in`, `ieee_command` or `Transport._command`. test_transport_reads covers only `_read_payload`. The capture comparisons (test_usbpcap.py:333-400) skip when `captures/` is absent ("captures/{name} is not in this checkout"), and captures are gitignored. The hardware tests are deselected by addopts.
```

**Failure scenario:** An edit changes the BUSY branch to `continue` (re-issuing the CDB) instead of polling `_control_in`. Every offline test passes, because every other test replaces `command()` wholesale. The first real scan re-sends START SCAN while the device is busy.

**Fix:** Write the promised FakeUsb as a subclass of Transport overriding _control_out, _control_in, _announce_length and _bulk_read_into with a scripted device. Cover AGAIN, BUSY to OK, BUSY to READ, data-out followed by CHECK, READ followed by BUSY then CHECK, and deadline expiry. Assert the exact control-transfer sequence against the three shapes test_usbpcap expects. Fix the conftest docstring.

<details><summary>Second reader's check</summary>

The conftest docstring (conftest.py:73-77) refers to FakeUsb, and grep finds that name nowhere else in the repository. No test references _command, _send_command, _wait_not_busy, _control_out, _control_in or ieee_command. test_transport_reads.py's ScriptedTransport overrides only _announce_length and _bulk_read_into, to test _read_payload. The retry/BUSY/READ/CHECK state machine in Transport._command (usb_transport.py:799-866) therefore has no offline coverage. The vendor-capture comparisons skip without captures/ (test_usbpcap.py:345, 370, 397), and the hardware tests are deselected by addopts.

</details>

<a id="tests-t-11"></a>

### T-11 -- No test checks that a delivered file holds the corrected pixels; the GUI's single delivery function is untested

**Severity** medium · **Category** test-gap · **Verdict** partly

**Where:** `tools/gui.py:4309-4348`, `tests/test_scan_tool.py:109-123`, `tests/test_scan_tool.py:290-367`, `tests/test_scan_roll_tool.py:27-90`, `tests/test_scan_roll_tool.py:115-125`, `tests/test_demo.py:1352-1380`

FrameWriter's delivered file is checked against library.corrected() only for demo walk prescans (test_demo.py:1352-1380). The GUI's Save As, Save all and Export path (_deliver_one) is untested, tools/scan.py's --out file is checked only for existence, and tools/scan_roll.py's frameNN.tif content is never compared with the corrected pixels.

**Evidence (from the code):**

```text
Every delivered-file test uses the plain FakeScanner, which returns one array and no last_pixels_raw, so the delivered and stored pixels are identical before orientation. Examples: `assert np.array_equal(written, preview.mirror(stored))` and `assert np.array_equal(written, preview.orient(stored, 90, True))`. CORRECTED_LEVEL is asserted only on the frames handed to merge_bracket (test_scan_tool.py:425), never on out.tif. test_scan_roll_tool defines CORRECTED_LEVEL but never reads frameNN.tif back. `ScannerGui._deliver_one` (Save As, Save all, Export: `full, entry_record = library.corrected(result.entry)`) has no test; grep finds no `_deliver_one` in tests.
```

**Failure scenario:** A refactor of FrameWriter._write passes `raw_image` into `delivered` (the names sit side by side at session.py:1629-1664), or `_deliver_one` switches to library.load. Every roll's frameNN.tif and every output-folder copy then ships uncorrected and striped. The whole suite stays green because its fakes make raw and corrected identical.

**Fix:** Run the orientation and output tests with CorrectingScanner (raw different from corrected). Assert that out_dir files and frameNN.tif equal orient(corrected), and that the entry equals raw. Assert the value of tools/scan.py's --out file. Add a window or stub test of `_deliver_one` against an entry with a reference that visibly changes pixels, comparing the written file with library.corrected.

<details><summary>Second reader's check</summary>

_deliver_one (gui.py:4309) has no test; grep finds no reference to it. tools/scan.py's out.tif is checked only for existence (test_scan_tool.py:123, 170, 190), and tools/scan_roll.py's frameNN.tif is checked only by name, never against CORRECTED_LEVEL. The claim that no test checks a delivered file is corrected is wrong for FrameWriter, however. test_demo.py:1352-1380 reads the delivered rolls/walk/prescan0N.tif and asserts np.array_equal(library.corrected(path)[0], picture). The demo's calibrated_entry makes raw and corrected differ (test_demo.py:1036-1071; 1202 asserts a correction ran). FrameWriter._write builds `delivered` the same way for every kind of job, so the example refactor (raw_image passed into delivered) would fail that test.

</details>

<a id="tests-t-a1"></a>

### T-A1 -- After a force abort the session never calls close(), so debug-spooled passes are never filed and nothing says where they are

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1855-1875`, `rps7200/session.py:1969-1976`, `rps7200/direct.py:1134-1145`, `rps7200/direct.py:1031-1128`, `tests/test_session.py:1015-1029`

With RPS7200_DEBUG=1, every pass the window and tools do not keep themselves, such as metering probes, hold/aim prescans and the replaced before-prescan, reaches the library only through _debug_flush, which runs only from DirectScanner.close(). Once the operator has used Force abort, the session skips close() entirely. The transport has already been closed by force_abort, so the flush would be safe. The pending spool in the system temp directory is never filed. No log line names the spool directory, because the 'left unfiled in ...' message exists only inside _debug_flush. No test runs a force abort with pending debug items.

**Evidence (from the code):**

```text
session._run finally: `try:
    if not self.dead:
        self._scanner.close()` (session.py:1972-1974). DirectScanner.close is the only caller of the flush: `if self._own_transport: self.t.close()
# Only now, with the device closed, is it safe to spend time writing.
self._debug_flush()` (direct.py:1142-1145). force_abort sets `self.dead = True` and closes only the transport (session.py:1863-1869). The test asserts the resulting state: `# A dead session does not politely close the device it just yanked.
assert scanner.closed is False` (test_session.py:1028-1029).
```

**Failure scenario:** A roll run with RPS7200_DEBUG=1 wedges mid-read. The operator types ABORT, and the session is marked dead. At shutdown, _run skips close(), so the metering probes and hold prescans spooled earlier in the session stay as NNN-image.npy/raw.bin in /tmp/rps7200-debug-XXXX. They are never filed, the operator is not told about them, and they disappear at the next temp cleanup or reboot. Those are exactly the passes the investigation of the wedge would need.

**Fix:** On a dead session, still run the scanner's filing half. Split close() into device close and _debug_flush, and call _debug_flush (or a public flush()) in the dead branch, or at least log the spool path. Add a test that captures a debug pass, calls force_abort, shuts down, and asserts the pass was filed into RPS7200_DEBUG_ROOT.

<a id="tests-t-09"></a>

### T-09 -- Demo-parity tests check attribute existence and object identity, not behaviour; the demo retypes nudge's arithmetic, omits meta keys, and answers ensure_shading(reuse) differently

**Severity** low · **Category** demo-divergence · **Verdict** partly

**Where:** `tests/test_demo.py:318-414`, `tests/test_demo.py:1695-1710`, `rps7200/demo.py:417-475`, `rps7200/demo.py:500-508`, `rps7200/demo.py:831-914`, `rps7200/direct.py:3155-3212`, `rps7200/direct.py:806-870`, `tools/gui.py:1984-1990`

The demo-parity tests check attribute presence and object identity, not results. Real divergences: DemoScanner's pass meta lacks protocol_revision, filter_offsets, mode, commands and shading_origin. demo.ensure_shading neither checks for nor writes the cached reference, so the window's reuse path behaves differently in the demo. nudge repeats the driver's three-line asked/clamped formula, though its constants and param come from DirectScanner.

**Evidence (from the code):**

```text
test_the_demo_has_everything_the_drivers_roll_reaches_for: `missing = [name for name in sorted(wanted) if not hasattr(demo, name)]`. test_a_nudge_answers_with_everything_the_hold_loop_reads checks only `assert key in got`. test_a_nudge_picks_the_same_param_the_scanner_would compares only param_for_mm. Yet DemoScanner.nudge retypes `asked = self.STEP_MM * param + self.OVERHEAD_MM; short = abs(millimetres) - asked; clamped = short > 1e-9` (demo.py:434-436), which duplicates direct.py:3624-3630. The demo's pass meta (`_take` plus `_settings_meta`) has no protocol_revision, filter_offsets, mode, commands or shading_origin, all of which DirectScanner.scan records. `ensure_shading` answers `"action": "loaded" if reuse else "calibrated"` whatever the path, while DirectScanner calibrates when `not path.exists()` (835-848). Other retyped values: `HOLD_SETTLE_S = 0.0` and a backlash of `2.2 * 0.1057` (MM_PER_UNIT typed as a literal). DemoScanner has no debug filing and no debug_claim.
```

**Failure scenario:** MM_PER_COMMAND or the clamp rule changes in DirectScanner.nudge (for example `asked` gains a per-direction term). DemoScanner.nudge keeps the old formula, and test_demo still passes because param and key names still match. The demo's hold loop then reports `clamped`/`spent_mm` values the hardware would not, which is the "not_converged is a stale copy" failure repeated.

**Fix:** Factor DirectScanner.nudge's plan (param, asked, short, clamped) into a static method and call it from both classes. Add a parity test that runs DirectScanner.nudge on FakeTransport and DemoScanner.nudge over a range of distances and asserts equal dicts. Add a test asserting that DemoScanner.scan/prescan meta keys are a superset of DirectScanner.scan's (via the T-01 fake). Make DemoScanner.ensure_shading(reuse=True) with a missing file behave as a calibration and write the cache.

<details><summary>Second reader's check</summary>

The parity tests are weak: they check hasattr, identity, param_for_mm and key presence (test_demo.py:318-414, 1695-1710). The claim that the demo retypes nudge's arithmetic is overstated. STEP_MM, OVERHEAD_MM, param_for_mm and MAX_CORRECTION_PARAM are taken from DirectScanner (demo.py:811-812; test_demo asserts their identity), and only the three-line asked/short/clamped formula is repeated (demo.py:434-436 vs direct.py:3624-3630). The driver itself repeats that formula at direct.py:3496. The backlash literal `2.2 * 0.1057` (demo.py:451) is a simulation input, not a driver decision, and HOLD_SETTLE_S = 0.0 only affects time. The real divergences are these. The demo's pass meta (_take at 846-914 plus _settings_meta) has no protocol_revision, filter_offsets, mode, commands or shading_origin, all of which scan() records (direct.py:3155-3212). demo.ensure_shading answers 'loaded' for reuse whether or not the file exists, and never writes the cache (demo.py:500-508), while the real one calibrates and caches (direct.py:835-870). The window gates on _cached_reference() (gui.py:1984-1990), so in the demo the reuse path is never reachable after a demo calibration. I rate it low because the hold loop, the roll loop and metering are the driver's own methods borrowed by the demo.

</details>

<a id="tests-t-12"></a>

### T-12 -- Tests that skip wherever the suite runs automatically

**Severity** low · **Category** test-gap · **Verdict** partly

**Where:** `tests/test_demo.py:659-727`, `tests/test_decode.py:300-345`, `tests/test_usbpcap.py:333-400`, `tests/test_frame_edges_parity.py:34-40`, `tests/test_tiff.py:444-464`, `tests/conftest.py:455-466`, `.github/workflows/test.yml:55-58`, `tests/test_roll.py:1403-1436`

Several data-dependent tests skip wherever the suite runs automatically: the demo hold loop, bottom-up detection on stored passes, vendor-capture equivalence, frame-edge parity and the TIFF cross-check. The driver's hold loop itself is covered on synthetic data in test_roll.py. What is lost in CI is the demo's own motion simulation and the real-data checks. Whether the GUI tests run on macOS CI is contradicted between conftest.py and test.yml.

**Evidence (from the code):**

```text
test_the_demo_converges_on_an_approved_position and test_one_frame_is_made_to_miss_on_purpose use `DemoScanner("library", ...)` and `pytest.skip("no library entries in this checkout to register against")`; library/ is gitignored. test_decode's BOTTOM_UP and TOP_DOWN tests skip when `f"{name} is not in this library"`. The usbpcap vendor comparisons skip when captures are absent. Frame-edge parity needs `FRAME_EDGE_PARITY` and the study data. test_tiff skips when scans/ is absent. test_gui runs `pytest.importorskip("tkinter")`; conftest says "GitHub's macOS runner is such a Python" (no Tk), while test.yml says "The other two runners have one of their own" (display). Hardware tests are deselected by `addopts = "-m 'not hardware'"`.
```

**Failure scenario:** A change to `_hold_to_approved` or `measure_shift_mm` breaks convergence. CI on all three platforms is green because the only convergence tests skip there. The break is found in the demo or on the scanner.

**Fix:** Rebuild the two demo hold-loop tests on synthetic library entries in tmp_path (textured pictures with prescans). Add a CI step that reports skip counts and fails if unexpected modules skip. Resolve the macOS Tk contradiction by asserting Tk availability in CI or documenting the skip.

<details><summary>Second reader's check</summary>

The skips are real. test_demo.py:659-727 uses DemoScanner("library") and skips when there are no entries. test_decode's BOTTOM_UP and TOP_DOWN tests skip without stored entries. test_usbpcap skips without captures/, frame_edges_parity needs FRAME_EDGE_PARITY and the study data, and test_tiff skips without scans/. conftest.py:463-466 says GitHub's macOS runner has no Tk, while test.yml:57-58 says the other two runners have a display, and the code cannot settle which is true. The failure scenario is wrong, though. _hold_to_approved convergence and not_converged are tested on synthetic data everywhere, in test_roll.py:1403-1436 (held with moves==1; not_converged with MAX_HOLD_MOVES) and test_roll.py:647-658. So a change that breaks convergence would fail CI. What only the skipping tests cover is the demo-specific nudge, backlash and slipping-frame simulation.

</details>

<a id="tests-t-13"></a>

### T-13 -- Source-text and tautological assertions stand in for behaviour in the places that matter most

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_gui.py:127-128`, `tests/test_session.py:1358-1364`, `tests/test_roll.py:724-749`, `tests/test_roll.py:756`, `tests/test_roll.py:791`, `tests/test_session.py:1090`, `tests/test_session.py:983-1012`, `tests/test_session.py:154-166`, `tests/conftest.py:218-240`

Source greps pass on commented-out, mis-indented or unreachable code, and fail on harmless renames. They guard the debug claim, the stop reaching the driver and the raw/corrected split, which are exactly the behaviours whose failure loses data. The session FakeScanner.scan_roll ignores `should_stop`, so the session-to-driver stop is covered only by the grep.

**Evidence (from the code):**

```text
There are 126 `inspect.getsource` assertions (115 in test_gui.py), for example `assert "debug_claim" in inspect.getsource(ScanSession._file)` and `assert "should_stop=self._stop.is_set" in source`. The repo's own test_gui.py:4049-4053 records a substring test that "went on passing while that line sat one indent outside `if args.demo:` ... A substring cannot see which block it is in." Tautologies: `assert os.environ.get("RPS7200_DEBUG") is None or True`, `assert (entries[0] / "raw.bin.gz").exists() or True`, and `assert max(image.shape[:2]) <= 512 or max(image.shape[:2]) <= 1400` on a 24x36 image. test_a_roll_stops_when_a_frame_cannot_be_filed depends on `time.sleep(0.05)` racing the writer (`assert scanner.produced < 6`).
```

**Failure scenario:** `should_stop=self._stop.is_set` is kept, but the call is moved into a branch that is not taken for rolls with `approved` set. The grep passes, and a Stop pressed mid-roll advances and prescans one more frame.

**Fix:** Replace the greps that guard data paths with behavioural tests: a ScanSession on ScannerOnStrip with request_stop during a frame, a debug-on DirectScanner through a session asserting one entry per pass, and the T-01 scan test. Delete the tautologies. Replace the sleep race with an event-driven failing save.

<details><summary>Second reader's check</summary>

Counting inspect.getsource uses gives 126: test_gui 115, test_frame_edges 4, test_roll 3, test_session 2, test_demo 1, test_scan_tool 1. The cited asserts exist at test_gui.py:127-128, test_session.py:1358-1364 and test_roll.py:724-749. The tautologies are at test_roll.py:756 and 791. test_session.py:1090 is trivially true for a 24x36 image. test_a_roll_stops_when_a_frame_cannot_be_filed relies on time.sleep(0.05) (test_session.py:994-998). The session's FakeScanner.scan_roll (test_session.py:154) takes **kw and ignores should_stop. conftest's strip double accepts should_stop but its loop at conftest.py:218-240 never calls it. The driver's own should_stop is tested in test_roll.py:1006-1039, but the session-to-driver hand-off is covered only by the grep.

</details>

<a id="tests-t-14"></a>

### T-14 -- Destructive and quit/abort operator paths are untested: 'No' to keep-entry, quit while busy, force abort mid-read, duplicates --delete

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:4465-4497`, `tools/gui.py:3416-3485`, `tools/gui.py:5837-5858`, `tools/library.py:167-196`, `tests/test_gui.py:5084-5122`, `tests/test_session.py:1015-1029`

These are the operator actions that destroy raw bytes or risk a wedge. Their guards are dialogs and ordering, and none of them is exercised: the No path, the effect on roll manifests, quitting mid-pass, aborting mid-read (the transport closing under a read, then suspect handling of queued jobs), and the only command-line rmtree over library entries.

**Evidence (from the code):**

```text
on_delete asks `"Keep the library entry?"` through askyesnocancel, and the answer No runs `shutil.rmtree(entry)`. Tests cover only the entry outside the session library and Cancel (`lambda t, m, **k: asked.append(m) or None`). `scanned_frames` decides `finished = record.get("done")` without checking that the entry still exists, so a deleted frame entry keeps its roll 'done'. on_close's busy branch, `_wait_to_quit`'s no-timeout wait and on_abort's typed `ABORT` gate have no tests; the only on_close call stubs `_wait_to_quit`. force_abort is tested only while idle (`for _ in range(200): if scanner.opened: break`). tools/library.py `duplicates --delete` (`shutil.rmtree(path)`) has no CLI test.
```

**Failure scenario:** An operator reads "Remove frame 7 from this session? ... Keep the library entry?" as a confirmation to remove and answers No. The frame's raw bytes are deleted, and the roll still lists frame 7 as done, so a resume does not offer to rescan it. Nothing in the suite would notice a change that made Yes delete too.

**Fix:** Add window tests for on_delete answering No (entry gone, index rebuilt, and roll.json either updated or at least flagged), for on_close while busy (request_stop called, no quit before the worker closes), and for on_abort requiring ABORT. Add a session test that force-aborts during a scripted read and asserts suspect is set and queued jobs are refused. Add a CLI test for `duplicates --delete` on a library of twins and non-twins. Reword the dialog so that the destructive answer is explicit.

<details><summary>Second reader's check</summary>

on_delete (gui.py:4465-4497) runs shutil.rmtree(entry) when askyesnocancel returns False. The only tests are the outside-library refusal and Cancel (test_gui.py:5084-5122). scanned_frames (gui.py:5837-5858) trusts record['done'] and never checks that the entry exists, and on_delete does not touch roll.json. That is mild, because frameNN.tif still exists in the roll folder, so treating the frame as done for resume is arguable. on_close's busy branch (3431-3444), _wait_to_quit (3463-3485) and on_abort (3416-3428) have no behavioural tests. force_abort is tested only while the session is idle (test_session.py:1015-1029). tools/library.py duplicates --delete (167-196, rmtree at 191) has no CLI test; library.prunable and duplicates are tested in test_library.py:210-320. The dialog does state that the raw bytes cannot be recovered, so the mis-read scenario is a UX risk rather than a missing guard.

</details>

<a id="tests-t-15"></a>

### T-15 -- Tests depend on the developer's environment and working directory

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_roll.py:713-758`, `rps7200/direct.py:534-557`, `tests/test_gui.py:1122`, `tests/test_demo.py:647-727`, `rps7200/demo.py:316-323`

The same suite can pass on CI and fail, or take a different path, on the owner's machine: with debug exported, with a 311-entry library in cwd, or with remembered settings. Nothing isolates DEBUG, DEBUG_ROOT, SETTINGS or cwd globally, so a future test that completes a real pass under an exported RPS7200_DEBUG=1 without DEBUG_ROOT would file into the real ./library.

**Evidence (from the code):**

```text
test_filing_is_off_by_default asserts `_debug_scanner().debug is False` with debug=None, and so fails whenever RPS7200_DEBUG=1 is exported. CLAUDE.md tells Claude to always set that variable, and nothing in conftest clears it. The window fixture and the demo tests use `DemoScanner("library", speed=1e9)`, a path relative to cwd, so they read the developer's real library (and start `_sign_pictures` over every entry) or nothing, depending on where pytest runs. The GUI fixture also loads the developer's gui-settings.json (see T-02).
```

**Failure scenario:** The owner runs `RPS7200_DEBUG=1 uv run pytest` and test_filing_is_off_by_default fails. With a large local library, the GUI tests spend minutes signing real entries, and the convergence tests switch from skip to run with results that depend on whichever entries are present.

**Fix:** Add an autouse conftest fixture that deletes RPS7200_DEBUG and sets RPS7200_DEBUG_ROOT, RPS7200_SETTINGS and the demo library to tmp_path. Point DemoScanner in the fixtures at a synthetic tmp library.

<details><summary>Second reader's check</summary>

_debug_scanner() with no debug argument reads RPS7200_DEBUG (direct.py:545-557), so test_filing_is_off_by_default (test_roll.py:752-758) fails whenever RPS7200_DEBUG=1 is exported, which CLAUDE.md tells Claude always to do. conftest has no autouse fixture that clears it; the only autouse fixtures are test_demo.py:58 (calibrated), test_gui.py:39 and test_transport_reads.py:58. The window fixture (test_gui.py:1122) and test_demo.py:653-727 use DemoScanner("library"), which is relative to the working directory. DemoScanner.open starts _sign_pictures on a thread (demo.py:316-323). The settings leak is T-02.

</details>

<a id="tests-t-16"></a>

### T-16 -- Test docstrings and comments claim coverage the code does not have

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tests/test_gui.py:1-11`, `tests/test_gui.py:1099-1104`, `tests/conftest.py:73-77`, `tests/test_scan_roll_calibration.py:1-8`, `tests/test_library.py:423-431`, `tests/conftest.py:463-466`, `.github/workflows/test.yml:26`, `.github/workflows/test.yml:57-58`

A reader auditing coverage from these comments would conclude that the GUI is not driven by pytest (it is), that the transport has a libusb fake (it has none), that lazy calibration still exists (it does not), and that meta completeness against scan() is checked (it is not). If the macOS hang note is still true, the 46 window tests would hang on the owner's Mac.

**Evidence (from the code):**

```text
test_gui.py:1-7 says "tested without opening one ... A Tk window driven from pytest hangs on macOS -- reliably, at the second test ... the widget wiring is checked by running `make run-demo`", yet 46 tests take the Tk `window` fixture, and line 1101 still says "These two need real Tk widgets". conftest.py:76 cites a nonexistent `FakeUsb`. test_scan_roll_calibration.py:3-6 says skipping ensure_shading "moved it inside `prescan()`, where the driver runs it lazily", but scan() now refuses (`raise self.uncalibrated(reason)`, direct.py:2963). test_library.py:430 says "This asserts the list keeps up with what scan() puts in meta" over a hand-typed meta. conftest says the macOS runner has no Tk, while test.yml:57-58 says the window "is really built" and "The other two runners have one of their own".
```

**Failure scenario:** The owner runs `make test` on the Mac where the suite was historically developed. By the file's own claim the second window test hangs, with no timeout configured locally (only CI has timeout-minutes). Or a maintainer trusts "tested against FakeUsb" and skips adding transport tests.

**Fix:** Update the docstrings to match the code, delete the FakeUsb reference or create it (T-10), and state in one place which platforms run test_gui. Add a per-test timeout (pytest-timeout) so a Tk hang fails instead of stalling.

<details><summary>Second reader's check</summary>

test_gui.py:1-7 says the window is tested without being opened and that it hangs on macOS, yet 46 tests use the Tk window fixture, and 1101-1104 still says "These two need real Tk widgets". conftest.py:76 names a FakeUsb that does not exist. test_scan_roll_calibration.py:3-8 describes a lazy calibration inside prescan(); prescan() now calls scan() (direct.py:2076), which raises self.uncalibrated(reason) (direct.py:2963). That docstring reads partly as history, but it describes the mechanism as current. test_library.py:430 claims to track scan()'s meta over a hand-typed dict. conftest.py:463-466 and test.yml:57-58 contradict each other about macOS Tk. The only timeout is CI's timeout-minutes: 40 (test.yml:26); pyproject has no pytest-timeout.

</details>

<a id="tests-t-a2"></a>

### T-A2 -- settings.py claims the RPS7200_SETTINGS variable keeps tests off the real file; no test sets it

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/settings.py:27-28`, `tests/test_gui.py:1107-1130`, `tests/test_settings.py`

**Doc claim:** rps7200/settings.py:27 -- "Environment variable that moves it, so a test never writes the real one."

The code comment asserts a safety property that the test suite does not use. Every window test runs against the working directory's gui-settings.json (see T-02), and the live repo-root file carries test residue.

**Evidence (from the code):**

```text
`#: Environment variable that moves it, so a test never writes the real one.
PATH_ENV = "RPS7200_SETTINGS"` (settings.py:27-28). A grep for RPS7200_SETTINGS or settings_path across tests/ finds uses only inside test_settings.py. The window fixture calls `gui_mod.ScannerGui(root, session, demo=True)` with no settings_path.
```

**Failure scenario:** A maintainer relies on the comment and assumes that `make test` cannot touch their remembered window state. It can: opening a roll in a window test rewrites controls, rolls and sheet in ./gui-settings.json.

**Fix:** Add an autouse fixture in conftest.py that sets RPS7200_SETTINGS (and RPS7200_DEBUG_ROOT, with RPS7200_DEBUG removed) to tmp_path, so that the comment becomes true.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entries produced by tests (the format under test) | <tmp_path>/**/<UTC>_<film>_<dpi>dpi[_ir][-N]/{scan.tif, raw.bin.gz\|raw.bin, shading.npz, ccd_mask.bin, prescan.tif, scan.json, INCOMPLETE} | TIFF (raw decode, 8/16-bit, 3/4 samples, deflate or plain); gzip or plain raw INDEX bytes; npz float64 reference; raw mask bytes; JSON record with sha256 per file | scan.tif raw by contract (corrections_applied=[] unless a caller says otherwise); prescan.tif corrected; raw.bin exact bytes | library.save, called directly by test_library/test_pass_record/test_demo, by ScanSession+FrameWriter with FakeScanner/CorrectingScanner/StripScanner/DemoScanner, by tools/scan.py and tools/scan_roll.py with patched DirectScanner, and by DirectScanner._debug_flush in test_roll | library.load/corrected/reconstruct/verify/entries/prunable in the same tests | Exact only in test_library.py and test_demo.py fixtures (encode_index bytes). Session, tool and FrameWriter tests file placeholder raw bytes (b"\x00\x01"*32, b"pass-N", b"raw-bytes", b"raw bytes") that do not decode to the pixels, so the exactness of those paths is unverified. |
| Operator GUI settings (controls, output folder, presets, shortcuts, rolls last-opened, contact-sheet decisions) | <cwd>/gui-settings.json (settings.DEFAULT_PATH) unless RPS7200_SETTINGS is set | JSON object, sections controls/film/output/window/presets/shortcuts/rolls/sheet, written whole | n/a (operator state) | ScannerGui._remember via settings.save(None) in the test_gui `window` fixture (open_roll -> _note_roll_opened, _store_sheet_state, on_close); NOT isolated to tmp_path | settings.load(None) at ScannerGui.__init__ in the fixture (tests start from the operator's saved controls) and the operator's real window | Not isolated: the live file carries test residue (rolls 'first', 'walk'; generated sheet keys). |
| Real stored library read by tests | <cwd>/library/*/scan.json, raw.bin.gz, prescan.tif | library entries | raw scan.tif plus corrected prescan.tif | the operator's scanning sessions (not tests) | DemoScanner("library") in the test_gui window fixture and test_demo convergence tests; test_decode._stored for BOTTOM_UP/TOP_DOWN | Read-only; its presence switches tests between skip and run. |
| USB captures | <repo>/captures/*.pcapng | pcapng | n/a | capture machine (gitignored) | test_usbpcap slow tests (setups, parse_capture, verify_capture) | Read-only; absent in CI so the vendor-equivalence tests always skip there. |
| Stored reference TIFFs and frame-edge study data | <repo>/scans/negatives/*.tif; <repo>/research/frame-edge/{data,lib2}/... | TIFF; study manifest JSON and prescans | mixed | earlier sessions and studies (gitignored) | test_tiff cross-implementation test; test_frame_edges_parity (also needs FRAME_EDGE_PARITY) | Read-only; skipped where absent. |
| Debug-filing spools | <system temp>/rps7200-debug-*/NNN-{image.npy, raw.bin, meta.json, shading.npz, ccd_mask.bin} | npy (uncompressed pixels), raw bytes, JSON sidecar, npz, mask bytes | raw pixels and raw bytes | DirectScanner._debug_capture in test_roll debug tests | DirectScanner._debug_flush -> library.save to RPS7200_DEBUG_ROOT (tmp) | Plain and exact, but no test ever spools raw bytes and then files and reconstructs them. test_a_failed_filing_keeps_the_only_copy deliberately leaves its spool behind in system temp. |
| Calibration archive | <tmp>/calibration/<UTC>[-N]/{data.bin, calibration.json, shading.npz, ccd_mask.bin} | raw calibration lines; JSON (width, bytes_per_line, commands, sha256); npz; mask bytes | raw | DirectScanner.archive_calibration (non-atomic, no INCOMPLETE marker) in test_scanner_api | only the test's own byte comparison; nothing in rps7200/tools reads data.bin | data.bin byte-exact, but in tests it is arbitrary bytes unrelated to the archived reference; re-derivability is never checked. |
| Roll folders | <tmp>/rolls/<name>/{roll.json, roll.json.bak, survey.json, approved.json, frameNN.tif, prescanNN.tif, prescanNN-before.tif, roll.json.unreadable, roll.json.legacy} | JSON manifests written beside then renamed; TIFF deliverables | frameNN/prescanNN corrected (delivered); manifests are records | ScanSession._roll/RollManifest/FrameWriter; tools/scan_roll.py | session.read_manifest/renumbered, gui.read_survey/scanned_frames in tests | Manifests atomic (tested including Windows refusals). Delivered TIFF content is never asserted to be corrected (fakes make raw == corrected). |

**Second reader's corrections to this table:**

Calibration archive: calibration.json does not record a \"width\". It records measured_utc, resolution, pixels_per_line, bytes_per_line, index_header, bytes, sha256, duration_s, commands, reference, ccd_mask and protocol_revision (direct.py:787-800). It is written last, with a plain write_text, after data.bin, ccd_mask.bin and shading.npz, and with no INCOMPLETE marker. Missing row: the shading cache (<reference path>, normally calibration/shading.npz), written by DirectScanner.save_shading through a write-beside-then-replace, and read by load_shading on reuse. It is exercised in test_scanner_api and replaced by every calibration, so an entry whose shading_origin says \"loaded\" points at a file that may since have been overwritten. Operator GUI settings: besides open_roll and on_close, the file is also written by the presets save and delete actions and by shortcut changes (gui.py:948, 986, 996). Library entries produced by tests: the claim that raw bytes are exact only in the test_library and test_demo fixtures is too narrow. test_demo also runs the real ScanSession and FrameWriter with DemoScanner and files encode_index bytes that reconstruct identically (test_demo.py:1238-1380), so the session and FrameWriter single-scan, prescan and walk paths are verified exact through the demo. Real roll frames, tools/scan.py, tools/scan_roll.py and roll_writer are not. Debug-filing spools: these are also left unfiled in system temp after a force abort, because close() is skipped for a dead session (see T-A1). Roll folders: frameNN.tif content and prescanNN.tif are corrected. A walk's prescanNN.tif is compared against library.corrected() in test_demo.py:1376-1380, so \"never asserted to be corrected\" is too strong.

## What the operator can do

- Run `make test`, `make test-all` or `make all` (tasks.py -> pytest tests/ with coverage over rps7200 only; tools/ and gui.py are not in the coverage report).
- Run `uv run pytest tests/ -m hardware` with the scanner attached: nine tests that open the device and send only INQUIRY and READ STATE.
- Run the frame-edge parity check with FRAME_EDGE_PARITY=1 where research/frame-edge data exists (about three minutes).
- Run the suite from any working directory; DemoScanner('library') and gui-settings.json resolve against cwd.
- Run the suite with RPS7200_NO_TIFFFILE=1 to check the bare-install TIFF path.

## What the operator should not do

- Run the GUI tests (make test with Tk and a display) on a checkout whose repo-root gui-settings.json holds settings or uncommissioned sheet decisions that matter: the window fixture loads and rewrites it.
- Export RPS7200_DEBUG=1 in the shell that runs pytest: test_filing_is_off_by_default fails, and nothing sets RPS7200_DEBUG_ROOT globally.
- Treat a green CI as evidence that the USB control plane, the vendor-capture equivalence, the demo hold-loop convergence, the real-library bottom-up detection or (on macOS) the GUI were tested: all of them skip there.
- Treat the demo tests as proof that DirectScanner.scan() files exact entries: only the DemoScanner path is reconstructed end to end.
- Run the hardware tests without asking first: they open and claim the device.

## Mistakes nothing guards against

- Answering 'No' to the GUI's 'Keep the library entry?' deletes the entry and its raw bytes (shutil.rmtree), while the roll's roll.json still marks the frame done, so a resume never offers it again. Only Cancel and the outside-library case are tested.
- A library drive that is full or unwritable while the output folder is fine: FrameWriter raises before writing any delivered copy, and with debug on the claimed spool is deleted at close. The pass is lost, with no test.
- Scanning a real roll from the window or tools/scan_roll.py without RPS7200_DEBUG=1: every frame's prescan, the replaced before-prescan, hold/aim verification passes and metering probes lose their raw bytes. The tests assert this as correct.
- Killing the window while library.compact runs: the entry is left with a stale scan.tif checksum that verify reports as damage, and no tool re-compacts it.
- Quitting the window mid-pass, or typing ABORT: the protections (askyesnocancel, the no-timeout _wait_to_quit, typed confirmation, suspect after a closed transport) are untested.
- Running `tools/library.py duplicates --delete`: the only command-line rmtree over library entries has no CLI test.
- Running `tools/library.py migrate-raw --write` before `migrate-direction` on bottom-up legacy entries: they are reported as a decode change ('left alone') and the command exits 1. The ordering is untested.

## Dataflow notes

How data enters the tests:
- **Command-level fakes.** conftest.FakeTransport records (opcode, payload) and answers READ_STATE byte 2 from a script. Every unlisted opcode answers b"", so DirectScanner.scan/_read_pass/calibrate_shading always fail before image data (direct.py:3237-3303).
- **Strip-level fakes.** conftest.StripTransport runs DirectScanner's own advance, retreat, position and wait_warm, and ScannerOnStrip stubs prescan and _hold_to_approved.
- **Seam-level stand-ins.** These are injected through ScanSession(open_scanner=...): test_session.FakeScanner, CorrectingScanner, conftest.StripScanner (a miniature copy of scan_roll that ignores should_stop, approved and max_failures), and DemoScanner, which borrows the real scan_roll, auto_exposure, _hold_to_approved and _aim_frame, encodes stored pictures with direction.encode_index and decodes them with DirectScanner.decode_index (demo.py:409-455).
- **Tool doubles.** load_tool() imports tools/*.py, and monkeypatch replaces their DirectScanner with FakeBracketScanner, FakeRollScanner or RecordingScanner. These subclass DirectScanner without running __init__ and hand out placeholder last_raw bytes.
- **Direct calls.** Many tests call library.save and FrameWriter.submit themselves with hand-built meta and capture dicts.

How data is transformed. In production:
1. scan() builds meta (direct.py:3157-3212) and returns the corrected image, keeping last_pixels_raw, last_raw and last_raw_layout.
2. ScanSession._file (session.py:2781-2906) reads capture_record(), checks it with raw_bytes_disagree (shape only), composes the reversal with the chosen orientation, claims the pass from debug filing, and submits it to FrameWriter.
3. FrameWriter._write (session.py:1618-1703) runs library.save(raw_image or image, corrections=...) first, then export.write of the oriented, optionally mono, corrected image to each path, then on_done and on_filed feed RollManifest.
4. _run's finally (session.py:1968-1995) closes the scanner (which runs _debug_flush), finishes the writer, re-saves manifests, and compacts plain entries.

The tests exercise steps 2-4 with fakes whose raw bytes do not match their pixels, and step 1 only by source grep. The only closed loop from bytes to entry to reconstruct to corrected() equal to what was shown runs through DemoScanner (test_demo.py:1231-1379).

How data leaves the tests: entries, rolls and spools go to tmp_path or system temp. The exception is the GUI fixture, which reads and writes <cwd>/gui-settings.json (tools/gui.py:388, 719, 2591-2602) and reads <cwd>/library through DemoScanner('library').
