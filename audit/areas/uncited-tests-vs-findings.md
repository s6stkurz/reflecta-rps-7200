# Tests that pin the defects (gap pass)

Area key `uncited-tests-vs-findings`. 16 findings: 1 high, 9 medium, 6 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: test modules on the paths behind high findings that no earlier area cited. Each one was read in full, and its calls were followed into the production code. For each module, the question was whether it pins the defect (a fix would break it), contradicts the finding, or is vacuous.


Verdicts per module:
- **tests/test_scan_roll_calibration.py: vacuous for "--no-shading triggers the lazy in-prescan calibration", and its docstring says the opposite.**
  - Its RecordingScanner replaces scan_roll outright, so no prescan or scan() runs after ensure_shading(skip=True).
  - It only checks that skip=True reached ensure_shading, and the real ensure_shading does nothing with it (rps7200/direct.py:592-598).
  - The first prescan (shading=True hard-coded, direct.py:1709) then calibrates lazily (direct.py:2579-2592). That is the LIBUSB_ERROR_PIPE stall path its own docstring describes.
  - The defect is actively pinned elsewhere:
    - tests/test_roll.py:1444-1453 asserts that "self.calibrate_shading()" is in DirectScanner.scan's source.
    - tests/test_scanner_api.py:71-75 pins the false "expect vertical striping" summary.
    - tests/test_scan_roll_tool.py uses --no-shading as a no-calibration shortcut in 7 places.
- **tests/test_sense.py: vacuous for the single-attempt CHECK CONDITION in read_planes.**
  - Only read_lines is exercised.
  - test_read_lines_retries_before_giving_up documents the one-shot-condition reason that read_planes (retries=1, direct.py:1485) ignores.
  - Its fake always refuses before any payload arrives. A naive fix (retries=3) would therefore pass it, and would silently drop lines when the CHECK arrives after the payload (usb_transport.py:849-856).
- **tests/test_optional_libraries.py: vacuous for TIFF.** It only simulates absent libusb.
  - Pixel losslessness of both writers is covered by tests/test_tiff.py.
  - scan.json image.sha256 is a hash of the encoded file (library.py:229), so it differs between the tifffile path (deflate + predictor) and the built-in path (uncompressed), and across library versions.
  - raw.sha256 is taken over the uncompressed bytes and does not depend on the writer.
  - Nothing tests either hash across writers.
- **tests/test_settings.py: pins that a corrupt gui-settings.json loads as a fresh install, silently.** Nothing tests that the next _remember() then overwrites the corrupt file. That write erases presets, shortcuts, the rolls table, and the sheet decisions for walks that have not been commissioned.
- **tests/test_export.py and tests/test_dng.py: vacuous for the promise "Nothing already there is overwritten".** Every test writes into an empty tmp_path.
  - The DNG sidecar and the Pillow-fallback .tif overwrite existing files without checking.
  - test_export's docstring endorses writing the delivered file before the library entry (a known high finding).
  - DNG pixel exactness is genuinely covered.
- **tests/test_framing.py: vacuous.** It only tests reversal_against.
- **tests/test_frame_position.py: pins the 36 mm frame model.** Its centred-frame test breaks if FRAME_WIDTH_MM ≥ APERTURE_MM. Meanwhile tests/test_frame_edges.py pins the 350.6-unit model.
- **tests/test_registration_walk.py: unrelated to SEARCH_MM and to frame width.** It tests a study tool's move sequencing. Its fake nudge echoes the requested millimetres rather than the snapped distance.
- **SEARCH_MM itself is pinned by tests/test_roll.py:1464-1468:** `== 9.0` and a stale `> 8.08`. The current largest single move is 9.39 mm, which is about 110 px against a reach of ±105 px.
- **tests/test_uniformity.py: vacuous for "rejected passes are included in analyse" and for the dropped session id.** Its fixtures carry neither a rejected tag nor a session, and each fixture represents one capture session. With the default tag, running phase 2 makes analyse refuse outright.
- **tests/test_mono.py: vacuous for the library-entry ordering.** Every FrameWriter test passes library=None, and so does tests/test_roll_writer.py's unwritable-frame test.
- **tests/test_preview.py: vacuous for "clipping is judged on corrected pixels".** It uses synthetic arrays. tests/test_gui.py:692-699 pins that _finest_pixels reads self._levels, and self._levels is filled by library.corrected.
- **tests/test_defects.py: covers destripe, which only tools/make_comparison.py uses.** No delivered path applies it. The test is vacuous for the fact that the comparison files skip shading.
- **tests/test_shortcuts.py: its NEVER_BOUND check is vacuous by construction.** Action ids never equal the handler names it lists. The "Move the film" labels are misleading: those keys only set a planned offset.
- **tests/test_ensemble.py: pins the 36.0 mm model** through its docstring and the reason text right_gap_closure produces. right_gap_closure retypes 36.0 as a default instead of using FRAME_WIDTH_MM.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [UT-01](#uncited-tests-vs-findings-ut-01) | high | test-gap | No test covers --no-shading still calibrating lazily; test_scan_roll_calibration claims the path is fixed, and test_roll pins the lazy call | [P15](../problems/P15-lazy-calibration-inside-scan.md) |
| [UT-02](#uncited-tests-vs-findings-ut-02) | medium | test-gap | test_sense exercises only read_lines; read_planes' single-attempt READ is untested, and a naive retry fix would pass while dropping lines | -- |
| [UT-04](#uncited-tests-vs-findings-ut-04) | medium | test-gap | test_settings pins the silent drop of a corrupt gui-settings.json; nothing tests that the next save then erases it | [P24](../problems/P24-disk-full-and-quitting.md) |
| [UT-05](#uncited-tests-vs-findings-ut-05) | medium | test-gap | The export tests never cover existing files; the DNG sidecar and the Pillow-fallback TIFF break the 'Nothing already there is overwritten' promise | -- |
| [UT-07](#uncited-tests-vs-findings-ut-07) | medium | test-gap | Every FrameWriter test that exercises a failed delivered write passes library=None, so losing the raw bytes when that write fails is never tested | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [UT-08](#uncited-tests-vs-findings-ut-08) | medium | test-gap | SEARCH_MM (±105 px at 300 dpi) is shorter than one param-87 move (~110 px); test_roll pins it with a stale bound, and the cited framing tests do not touch it | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [UT-09](#uncited-tests-vs-findings-ut-09) | medium | test-gap | Tests pin two contradictory frame-width models, 36 mm in framing and 350.6 units in frame_edges; the roll uses the 36 mm one for positive and Kodachrome film | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [UT-10](#uncited-tests-vs-findings-ut-10) | medium | test-gap | test_uniformity has no rejected, multi-session or phase-2 fixture; analyse uses rejected passes as the reference and refuses once phase 2 exists | -- |
| [UT-12](#uncited-tests-vs-findings-ut-12) | medium | test-gap | test_defects validates destripe, which only make_comparison uses; the comparison files skip the shading every delivered file gets | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [UT-A1](#uncited-tests-vs-findings-ut-a1) | medium | error-handling | A failed settings write is silent: _remember ignores settings.save's None, so sheet decisions made before commissioning can be lost without a word | [P24](../problems/P24-disk-full-and-quitting.md) |
| [UT-03](#uncited-tests-vs-findings-ut-03) | low | test-gap | test_optional_libraries does not touch the TIFF paths; the stored image.sha256 depends on the writer, while raw.sha256 does not | -- |
| [UT-06](#uncited-tests-vs-findings-ut-06) | low | bug | Output names are claimed at submit time and written later; two queued jobs with the same name overwrite one another | -- |
| [UT-11](#uncited-tests-vs-findings-ut-11) | low | test-gap | test_preview checks clipping only on synthetic arrays; the GUI feeds it shading-corrected pixels, and test_gui pins that source | -- |
| [UT-13](#uncited-tests-vs-findings-ut-13) | low | doc-mismatch | test_shortcuts' NEVER_BOUND check is vacuous by construction, and the adjuster keys are labelled as moving film when they only set an offset | -- |
| [UT-14](#uncited-tests-vs-findings-ut-14) | low | test-gap | test_registration_walk's fake nudge echoes the request; the walk records requested millimetres, not what the param law delivers | -- |
| [UT-A2](#uncited-tests-vs-findings-ut-a2) | low | bug | _unclaimed silently falls back to overwriting after 998 clashes | -- |

## Findings in full

<a id="uncited-tests-vs-findings-ut-01"></a>

### UT-01 -- No test covers --no-shading still calibrating lazily; test_scan_roll_calibration claims the path is fixed, and test_roll pins the lazy call

**Severity** high · **Category** test-gap · **Verdict** confirmed · **Problem** [P15](../problems/P15-lazy-calibration-inside-scan.md)

**Where:** `tests/test_scan_roll_calibration.py:10-15`, `tests/test_scan_roll_calibration.py:59-70`, `tests/test_scan_roll_calibration.py:113-115`, `tests/test_roll.py:1444-1453`, `tests/test_scanner_api.py:71-75`, `tools/scan_roll.py:145-146`, `tools/scan_roll.py:166-173`, `rps7200/direct.py:592-598`, `rps7200/direct.py:1704-1710`, `rps7200/direct.py:2579-2592`, `rps7200/direct.py:2083-2091`, `rps7200/direct.py:3494-3496`

**Doc claim:** tests/test_scan_roll_calibration.py:12-14 claims --no-shading 'reaches' the calibration and the lazy path no longer ignores it; tools/scan_roll.py:146 help 'skip calibration entirely; scans come back striped'; rps7200/direct.py:597 summary 'shading correction disabled: expect vertical striping'

--no-shading never reaches the pass. It only skips the up-front calibrate, so the first prescan (and on tools/scan.py, the first --auto-exposure probe) runs calibrate_shading() inside scan(). That is the path both the test module and tools/scan_roll.py:430-446 describe as stalling with LIBUSB_ERROR_PIPE. The test module is vacuous because it mocks away every pass, and its docstring states the opposite of what the code does. A fix that removed the lazy calibration, or made scan() refuse when there is no reference, would break tests/test_roll.py:1444-1453, which pins the lazy call by source text. test_scanner_api pins the misleading "expect vertical striping" summary. test_scan_roll_tool.py and test_scan_tool.py use --no-shading as their way of 'not calibrating' against fakes, which encodes the same false belief.

**Evidence (from the code):**

```text
Test docstring: "These pin that the roll tool makes it on every path, and that `--reuse` and `--no-shading` reach it, which they did not before: the flags are read in `calibrate()` and nowhere else, so the lazy path ignored them." The fake overrides scan_roll entirely: `def scan_roll(self, **kw): for index in range(2): yield RollFrame(...)`, and the only assertion is `assert scanner.shading_calls[0]["skip"] is True`. Production: ensure_shading `if skip: return {"action": "skipped", "reference": None, ... "summary": "shading correction disabled: expect vertical striping"}` leaves _shading None. prescan() calls `self.scan(..., shading=True, ...)`. scan() then does `if self._shading is None or needed > self._shading.pixels_per_line: ... self.calibrate_shading()`. DirectScanner.scan_roll has no shading parameter (`prescan_image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)`). Metering probes call `self.scan(resolution=resolution, infrared=False, exposure_scale=scales, keep_raw=True)`, with shading left at its True default. tests/test_roll.py:1452: `assert "self.calibrate_shading()" in source`. tests/test_scanner_api.py:75: `assert "striping" in result["summary"]`. scan_roll help text: "skip calibration entirely; scans come back striped".
```

**Failure scenario:** The operator runs `tools/scan_roll.py --dry-run --no-shading` to save the 3-4 minute calibration. The tool prints "shading correction disabled: expect vertical striping". The first prescan then calls calibrate_shading() lazily, the documented stall path (bulk read failed after 0 bytes: LIBUSB_ERROR_PIPE, then INQUIRY stops answering), so the scanner needs a power cycle. If it does not stall, every frame comes back corrected anyway, and the lazily measured reference is never saved to --reference. The same happens with `tools/scan.py --no-shading --auto-exposure`, where the first metering probe calibrates.

**Fix:** Add a test that drives the real DirectScanner.scan_roll and prescan, with a fake transport or a stubbed calibrate_shading that fails, under --no-shading, and asserts that no calibration happens. Thread a shading flag through scan_roll, prescan and the auto_exposure probes. Or make scan() raise ShadingUnavailable when there is no reference, instead of calibrating inside a pass. Replace the source-text assertion at tests/test_roll.py:1452 with a behavioural one. Correct the test docstring, the ensure_shading skip summary and the --no-shading help text.

<details><summary>Second reader's check</summary>

I confirmed this in the code. ensure_shading(skip=True) returns before calibrating and leaves _shading None (direct.py:592-598). prescan() passes shading=True (direct.py:1704-1710). scan() calibrates lazily when `self._shading is None` (direct.py:2579-2592). scan_roll calls `self.prescan(resolution=prescan_resolution, keep_raw=keep_raw)` with no shading argument (direct.py:3494-3496), and the scan_roll signature (3292-3316) has no shading parameter. The auto_exposure probes call self.scan(...) without shading, so they get the True default (direct.py:2083-2091). tools/scan.py passes `shading=not args.no_shading` only to the final scan() and scan_bracket(), so its --auto-exposure probes calibrate lazily too. test_scan_roll_calibration overrides scan_roll and checks only that ensure_shading saw skip=True. Its docstring (lines 12-14) says --no-shading 'reaches' the calibration, which is false for the pass. test_roll.py:1452 pins `"self.calibrate_shading()" in source`, and test_scanner_api.py:75 pins the 'striping' summary. One nuance: a fix that threads a shading flag through leaves the lazy call in place and would not break test_roll:1452; only a fix that deletes the lazy call breaks it. The scan_roll comment at 430-446 records the lazy path stalling the device twice, so high severity stands.

</details>

<a id="uncited-tests-vs-findings-ut-02"></a>

### UT-02 -- test_sense exercises only read_lines; read_planes' single-attempt READ is untested, and a naive retry fix would pass while dropping lines

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_sense.py:39-56`, `tests/test_sense.py:145-150`, `rps7200/direct.py:1419-1437`, `rps7200/direct.py:1485`, `rps7200/usb_transport.py:849-857`, `rps7200/direct.py:1282`

**Doc claim:** rps7200/direct.py:1407-1409 read_lines docstring: 'a queued one-shot sense condition is reported against whichever command arrives next, so the first attempt can be rejected' -- read_planes passes retries=1

No test calls read_planes with a CHECK CONDITION mid-frame. The module documents exactly the one-shot condition that the live read path gives only one attempt, so on this point it contradicts the design rather than pinning it. A fix that simply passes retries=3 would pass every test here. In the post-payload CHECK case that fix would re-issue READ for the next n lines, and the n lines already transferred would be silently lost: read_planes counts `got += n` only on success, so the stream shifts and the planes decode misaligned. A NOT READY sense during a read is also treated as a hard refusal, although start_scan treats it as 'wait'. The fake cannot express either distinction.

**Evidence (from the code):**

```text
read_planes: `chunk = self.read_lines(n, bpl, retries=1)`. The test's rationale: `def test_read_lines_retries_before_giving_up(): """A queued one-shot condition lands on whichever command arrives next.""" ... read_lines(4, 100, retries=3) ... assert transport.reads == 3`. Its fake always refuses before any data: `if command[0] == SCSI_READ: self.reads += 1; raise CheckCondition(SCSI_READ)`. The transport can also raise CHECK after the payload has been read: `payload = self._read_payload(read_size, timeout_ms) ... if final == UsbStatus.CHECK: raise CheckCondition(command[0])`. Sense.not_ready is acted on only by start_scan/wait_warm (`if last.not_ready: # still becoming ready`), never on the read path.
```

**Failure scenario:** A one-shot unit-attention sense is queued mid-scan, and the next READ is refused. read_planes aborts the pass after minutes of scanning (the current behaviour, which is untested). After a naive 'retry more' fix, a READ whose data phase completed but whose closing status was CHECK is retried. The frame loses n lines in the middle, decode_index realigns the planes by tag index, and the pass is filed with a shifted plane and no error.

**Fix:** Make CheckCondition carry whether any payload was delivered, and whether that payload was complete. Retry in read_planes only on a CHECK before the data phase, and treat NOT READY as 'wait' like NoDataYet. Add read_planes-level tests for three cases: a pre-payload one-shot CHECK (retry), a post-payload CHECK (keep the bytes or fail loudly, never re-read), and NOT READY (wait).

<details><summary>Second reader's check</summary>

read_planes calls `self.read_lines(n, bpl, retries=1)` (direct.py:1485). A CheckCondition there is sensed once and becomes ScanReadError, or EndOfData, which ends the read loop. The transport can raise CheckCondition after `_read_payload` has returned the bytes (usb_transport.py:849-857), and those bytes are then discarded. test_sense only exercises read_lines, and its fake always refuses before any data. The only read_planes test (test_decode.py:359) stubs read_lines and never raises. So the one-shot condition the read_lines docstring describes aborts the live pass on the first refusal, and nothing tests that. Medium, not low: aborting read_planes mid-pass leaves the device holding unread lines, which is the 'abandoned read' hazard, and a naive retries>1 fix could drop a delivered chunk. It would lose `got += n` and misalign the planes, because decode_index appends lines per tag in arrival order (direct.py:1575-1580).

</details>

<a id="uncited-tests-vs-findings-ut-04"></a>

### UT-04 -- test_settings pins the silent drop of a corrupt gui-settings.json; nothing tests that the next save then erases it

**Severity** medium · **Category** test-gap · **Verdict** confirmed · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `tests/test_settings.py:21-31`, `rps7200/settings.py:24`, `rps7200/settings.py:58-79`, `rps7200/settings.py:82-97`, `tools/gui.py:377`, `tools/gui.py:668-697`, `tools/gui.py:2340-2350`

**Doc claim:** rps7200/settings.py:3-6 'Losing this file costs a few seconds of resetting controls' -- it also holds the sheet decisions the same file's lines 38-45 describe as the only record before commissioning

Losing the controls is harmless. But this file is also, by settings.py's own comment (lines 38-45), the only store of the contact sheet's decisions (ticks, per-frame offsets, rotations) for a walk that has not yet been commissioned. It also holds presets and custom shortcuts. A single typo from hand-editing (the file is documented as meant to be edited by hand) makes load() return defaults with no message. The first _remember() after that, on any sheet click or roll open, replaces the file with the default-filled payload, and the original content is lost for good. The test pins the silent drop on load and has no case for preservation. A fix that keeps the corrupt copy and logs a warning would still pass. A fix that raises would break test_rubbish_reads_as_a_fresh_install.

**Evidence (from the code):**

```text
settings.load: `except (OSError, json.JSONDecodeError, ValueError): return blank` and `if not isinstance(stored, dict): return blank`. There is no warning and no backup. test: `assert settings.load(path)["controls"] == {}` for "{ not json at all" and similar input. The GUI loads it once (`self.remembered = settings.load(settings_path)`), and `_remember()` writes the whole payload: `settings.save({... "presets": self.presets, "shortcuts": self.shortcut_overrides, "rolls": self.remembered.get("rolls") or {}, "sheet": self.remembered.get("sheet") or {}}, ...)`. `_store_sheet_state` calls `self._remember()` on every sheet change. `DEFAULT_PATH = Path("gui-settings.json")` is resolved against the current working directory.
```

**Failure scenario:** Stefan hand-edits gui-settings.json and leaves a trailing comma, then launches the window. The window opens on defaults without saying why. He opens the sheet for yesterday's uncommissioned walk: its offsets and ticks are gone, and the first tick he makes overwrites the file, so his presets and remapped keys are also unrecoverable. Launching from a different directory has the same effect, because the file path is relative.

**Fix:** On a parse failure, rename the file to gui-settings.json.corrupt-<timestamp> before the first save, and _say() that it happened. Resolve DEFAULT_PATH against the repository or library root, not the current directory. Add tests: a corrupt file is preserved and reported, and a save after a failed load does not destroy the original bytes.

<details><summary>Second reader's check</summary>

settings.load swallows OSError, JSONDecodeError and ValueError, and wrong-shape JSON, and returns a blank dict with no signal (settings.py:67-72). The GUI loads it once (gui.py:377). _remember writes the whole payload from self.remembered, including `"sheet": self.remembered.get("sheet") or {}` (gui.py:678-695). _store_sheet_state calls _remember on every sheet change (gui.py:2340-2350). So the first sheet interaction after a failed parse overwrites the original file. The overwrite erases the presets, the shortcuts and the uncommissioned sheet decisions, which settings.py:38-45 calls the only record before commissioning. DEFAULT_PATH is Path("gui-settings.json"), relative to the current directory, although its comment says 'Beside the library'. test_rubbish_reads_as_a_fresh_install pins the silent drop and has no preservation case.

</details>

<a id="uncited-tests-vs-findings-ut-05"></a>

### UT-05 -- The export tests never cover existing files; the DNG sidecar and the Pillow-fallback TIFF break the 'Nothing already there is overwritten' promise

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_export.py:113-135`, `tests/test_export.py:155-172`, `tests/test_export.py:195-215`, `rps7200/export.py:100-107`, `rps7200/export.py:144-155`, `rps7200/export.py:183-191`, `rps7200/dng.py:145-146`, `tools/gui.py:3809-3810`, `tools/gui.py:3826`, `tools/gui.py:2420-2421`, `tools/gui.py:2436`

**Doc claim:** tools/gui.py:3809 and tools/gui.py:2420 dialog text 'Nothing already there is overwritten'

_unclaimed chooses a free name only for the requested .jpg. export.write then writes two other names it never checked: <stem>.dng for any 4-channel JPEG delivery, and <stem>.tif when Pillow (an optional extra) is missing. Both overwrite without warning. dng.write also writes in place (open 'wb', no temp file and rename), so a failure mid-write leaves a truncated .dng in place of the old file, while the note says it 'could not be written'. The tests pin the naming (`frame.dng` beside `frame.jpg`, `frame.tif` for the fallback) only in empty directories, so they are vacuous for the promise. A fix that picks unclaimed companion names would still pass them. DNG pixel exactness (test_dng) is genuinely covered.

**Evidence (from the code):**

```text
The dialog text: "Nothing already there is overwritten; a clashing name gets the next free one." Save All: `path = _unclaimed(out / batch_name(result, fmt))`, which checks only the .jpg name. export: `companion = infrared_path(path)` -> `Path(path).with_suffix(dng.SUFFIX)`, then `dng.write(companion, ...)` -> `with open(path, "wb") as fh:` with no existence check. The Pillow fallback: `except ImportError: path = path.with_suffix(SUFFIXES["tiff"]); tiff.write(str(path), image, resolution=resolution)`. Every test uses a fresh tmp_path. The failure test's `refuse()` raises before any file is opened.
```

**Failure scenario:** Case 1: the operator Saves All as TIFF into a folder, then switches the format to JPEG on a machine without Pillow and Saves All into the same folder. Every frame01_1800dpi.jpg name is free, so each is 'unclaimed', and export silently rewrites the earlier frame01_1800dpi.tif. Case 2: a JPEG Save All of RGBI passes into a folder that holds .dng files from an earlier delivery with the same stems replaces those DNGs without warning.

**Fix:** Have export.write return the paths it actually wrote, and choose unclaimed names for the companion and the fallback, for example by passing a claim callback. Write the DNG to a temp file and os.replace it. Add tests that pre-populate the target folder with frame.tif and frame.dng and assert that nothing existing changes.

<details><summary>Second reader's check</summary>

_unclaimed (session.py:2192-2204) checks only the requested name. export.write writes `path.with_suffix(".tif")` on ImportError without checking it (export.py:186-190). _write_infrared writes `Path(path).with_suffix(".dng")` (export.py:100-107, 144-149) through dng.write, which calls `open(path, "wb")` in place (dng.py:145-146). Neither target is checked for an existing file. Both GUI dialogs promise 'Nothing already there is overwritten' (gui.py:3809-3810, 2420-2421), and both route through _unclaimed and then _deliver_one, which calls export.write. The export tests all use fresh tmp_path directories. In the failure test, refuse() raises before any file is opened, so the truncated-DNG case is never exercised.

</details>

<a id="uncited-tests-vs-findings-ut-07"></a>

### UT-07 -- Every FrameWriter test that exercises a failed delivered write passes library=None, so losing the raw bytes when that write fails is never tested

**Severity** medium · **Category** test-gap · **Verdict** partly · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1048-1058`, `rps7200/session.py:1074-1097`, `tests/test_roll_writer.py:67-82`, `tests/test_mono.py:151-156`, `tests/test_mono.py:173-178`, `tests/test_mono.py:195-199`, `tests/test_export.py:155-160`

FrameWriter writes the delivered files before library.save, so an OSError on any delivered path aborts the library entry and the frame's raw bytes, reference and mask are lost. No test pairs a failing delivered path with a library root, so the loss is untested. test_mono's library=None calls never check its claim that 'the library entry keeps all three channels'. The tests are not purely vacuous, though. tests/test_roll_writer.py:67-82 pins that an unwritable delivered path makes the whole frame an error that is not `done`, so a fix that files first and demotes export failures to notes must also change that test. test_export's docstring ('the library entry is still to come') describes the unsafe order.

**Evidence (from the code):**

```text
test_mono: `w.submit(seq=0, number=1, paths=[out], rotate=0, image=img, meta=..., dpi=900, library=None, ...)`. test_roll_writer `job(number, path, library=None, ...)` feeds test_one_unwritable_frame_does_not_end_the_roll, whose docstring says "a frame that cannot be filed costs that frame". test_export's docstring: "The picture is on disk by this point and the library entry is still to come". FrameWriter._write runs `for path in job.get("paths") or (): ... note = export.write(...)` before `entry = library.save(...)`.
```

**Failure scenario:** During a 38-frame roll the output folder is on a USB stick that fills at frame 20. From then on each FrameWriter job raises in export.write, frames 20-38 get no library entry, and their raw bytes are gone. The suite, which includes the 'unwritable frame' test, is green.

**Fix:** Call library.save first (or in a finally block) and export afterwards, collecting export errors as notes. Add a test with library=tmp_path/'lib' and a blocked delivery path, asserting that the entry exists with raw.bin.gz and that the error is reported. Fix the test_export docstring.

<details><summary>Second reader's check</summary>

The core claim holds. FrameWriter._write runs `export.write` for every path before `library.save` (session.py:1074-1097), and _run turns any exception into an error without filing (1048-1058). So a failing delivered path loses the entry and its raw bytes. None of the cited tests combines a failing path with a library root. The claim that the tests are 'vacuous, not pinning' is partly wrong. test_roll_writer.py:67-82 asserts `[n for n, _ in writer.done] == [1, 3]` and `len(writer.errors) == 1` for the blocked frame. The recommended fix (file first, then report export failures as notes) would put frame 2 in `done` with no error, and that test would fail. It therefore pins the raise-on-export contract, although it cannot see the lost entry because library=None.

</details>

<a id="uncited-tests-vs-findings-ut-08"></a>

### UT-08 -- SEARCH_MM (±105 px at 300 dpi) is shorter than one param-87 move (~110 px); test_roll pins it with a stale bound, and the cited framing tests do not touch it

**Severity** medium · **Category** test-gap · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `rps7200/framing.py:854-857`, `rps7200/framing.py:1061-1063`, `rps7200/session.py:93-95`, `rps7200/direct.py:3123`, `tools/gui.py:230`, `tools/gui.py:5298`, `rps7200/direct.py:2909`, `rps7200/framing.py:1197-1198`, `rps7200/direct.py:3539-3546`, `tests/test_roll.py:1197-1206`, `tests/test_roll.py:1456-1468`, `tools/verify_protocol.py:837-838`, `rps7200/framing.py:1969-1997`

**Doc claim:** rps7200/framing.py:854-855 'Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window'; tests/test_roll.py:1466 comment 'Comfortably past what the transport can travel'

Once MAX_CORRECTION_PARAM was raised to 87, one command (and the adjuster's MAX_TRAVEL_MM) reaches 9.39 mm. That is outside the ±9.0 mm window measure_shift_mm searches, so the hold loop cannot confirm the largest move it is allowed to make. The true peak lies outside the window, confidence falls below the 55 floor, the result is None, hold_plan returns 'unverified', and it counts as a miss. test_framing.py (reversal only), test_frame_position.py (gap detectors) and test_registration_walk.py (a study tool's ladder) neither import SEARCH_MM nor call measure_shift_mm, so they are vacuous here. The value is pinned by tests/test_roll.py:1464 (== 9.0). Its supporting assertion, > 8.08, encodes the old maximum of 8 chained commands, so it passes while its stated premise ('past what the transport can travel') is false. A fix that raises SEARCH_MM breaks that test, and it must also re-fit CONFIDENCE_FLOOR, which is a z-score that depends on the reach. framing.MAX_VERIFIABLE_PARAM=160 contradicts DirectScanner's note that param 160 cannot be confirmed; it is dead code.

**Evidence (from the code):**

```text
framing: "Just over MAX_TRAVEL_MM, so any displacement the transport can produce is inside the window." `SEARCH_MM = 9.0`. `mm_per_px = aperture_mm / width; reach = int(SEARCH_MM / max(mm_per_px, 1e-9))` gives 105 px at 428 columns (36.491/428 = 0.08526 mm/px). `FINE_MAX_MM = (DirectScanner.STEP_MM * DirectScanner.MAX_CORRECTION_PARAM + DirectScanner.OVERHEAD_MM)` = 0.1057 x 88.84 = 9.39 mm, about 110 px. `MAX_TRAVEL_MM = MAX_FINE_MM`, and offsets are clamped `max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, ...))`. verify_protocol: "`measure_shift_mm` looks only +-105 px and refused every large move in stage 12". tests/test_roll.py:1464-1468: `assert SEARCH_MM == pytest.approx(9.0)` ... `# Comfortably past what the transport can travel` `assert SEARCH_MM > 8.08`. test_roll.py:1198: "less than the 8 mm the transport can move", and it tests only an 80 px shift. framing also carries `MAX_VERIFIABLE_PARAM = 160` and `command_for`, which nothing calls.
```

**Failure scenario:** The operator sets a frame 9.2 mm (about 87 units) over in the frame-position window, which the adjuster allows. On the roll, the hold loop sends param 85, re-prescans and cannot find the match. The frame is logged 'could not be verified against your picture'. After three such frames in a row, holding is switched off for the rest of the roll (HOLD_GIVE_UP_FRAMES), although every move went where it was asked.

**Fix:** Derive the reach from FINE_MAX_MM plus a margin, or cap MAX_TRAVEL_MM and MAX_CORRECTION_PARAM so that one move stays under SEARCH_MM. Re-fit CONFIDENCE_FLOOR at the new reach with tools/registration_margin.py. Replace `SEARCH_MM > 8.08` with `SEARCH_MM > FINE_MAX_MM + drift margin`, and add a measure_shift_mm test at a param-87 displacement (~110 px). Delete or reconcile MAX_VERIFIABLE_PARAM and command_for.

<details><summary>Second reader's check</summary>

SEARCH_MM = 9.0 (framing.py:857). reach = int(9.0 / (APERTURE_MM/width)), with APERTURE_MM = 10344*25.4/7200 = 36.49 mm, which gives 105 px at 428 columns (framing.py:1061-1063). register() is limited to max_shift=reach, and results below CONFIDENCE_FLOOR return None. FINE_MAX_MM = 0.1057*87 + 0.1057*1.84 = 9.39 mm (session.py:94). MAX_TRAVEL_MM = MAX_FINE_MM (gui.py:230), and the adjuster clamps offsets to it (gui.py:5298). The hold loop measures `measure_shift_mm(approved.reference, image)` (direct.py:2881, 2909), so an approved offset between about 9.0 and 9.39 mm cannot be confirmed. The driver's own comment (direct.py:3117-3122) says param 87 'moved 109 px' and calls 87 'the largest move two prescans can still confirm'. 109 px is past the 105 px that measure_shift_mm searches, and verify_protocol.py:837-838 says the same. test_roll.py:1464-1468 pins 9.0, with `> 8.08` as a stale premise. test_roll.py:1196-1205 tests only 80 px and still says '8 mm'. command_for, MAX_VERIFIABLE_PARAM and LARGEST_VERIFIABLE_MOVE are referenced only inside framing.py (dead). The cited test_framing, test_frame_position and test_registration_walk do not touch SEARCH_MM. The band affected is narrow (offsets over about 85 units), hence medium.

</details>

<a id="uncited-tests-vs-findings-ut-09"></a>

### UT-09 -- Tests pin two contradictory frame-width models, 36 mm in framing and 350.6 units in frame_edges; the roll uses the 36 mm one for positive and Kodachrome film

**Severity** medium · **Category** test-gap · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tests/test_frame_position.py:299-313`, `tests/test_frame_position.py:166-167`, `tests/test_ensemble.py:129-134`, `tests/test_ensemble.py:175-182`, `tests/test_frame_edges.py:107-111`, `rps7200/framing.py:586`, `rps7200/framing.py:740`, `rps7200/framing.py:1366-1368`, `rps7200/framing.py:1957`, `rps7200/framing.py:1733-1747`, `rps7200/direct.py:3419-3423`, `tools/frame_edges/propose.py:39`, `tools/frame_edges/propose.py:249-254`

**Doc claim:** CLAUDE.md:460-464 'A frame is wider than the aperture: 350.6 units ... The window and the roll centre with it.' -- true only for films tools/frame_edges reads

CLAUDE.md says a centred frame shows no base and that 'the window and the roll centre with it'. That is true only when the frame_edges reader runs, which is for negative and B&W film. For positive and Kodachrome, and for any StripWalk without a reader, aiming uses the 36 mm model. It targets 2.3 units of base at the left, which by the 350.6-unit measurement leaves the picture about 5 units off-centre (about 8.4 units lost on the right instead of about 3 on each side). That is beyond HOLD_TOLERANCE (2.84 units). Each model is pinned by its own tests: unifying on 350.6 breaks test_frame_position:299-313 and the test_ensemble reason text, and unifying on 36 mm breaks test_frame_edges:107-111. The cited tests therefore pin the divergence instead of detecting it. right_gap_closure's default 36.0 is a second home for FRAME_WIDTH_MM.

**Evidence (from the code):**

```text
framing: `FRAME_WIDTH_MM = 36.0`, `TARGET_GAP_MM = (APERTURE_MM - FRAME_WIDTH_MM) / 2.0` (+0.246 mm, about 2.3 units of base expected at the left), and `def right_gap_closure(..., frame_mm: float = 36.0)`, a retyped constant. Also `FRAME_WIDTH_UNITS = 350.6` (435.6 columns against the 428-column aperture). test_frame_position: `width_px = int(round(FRAME_WIDTH_MM / (APERTURE_MM / WIDTH))); edge = (WIDTH - width_px) // 2`; with a frame wider than the aperture, edge becomes -4 and the test fails. test_ensemble: "the two agree exactly only for a frame of exactly 36.000 mm", `assert "not the 36.0 mm a frame is" in r.reason or ...`. test_frame_edges: `assert centre.frame_columns(428) == pytest.approx(435.6, abs=0.1)`. The roll: `walk = (StripWalk(reader=edge_reader(film) if edge_reader else None) ...` and `FILM_TYPES = {FILM_NEGATIVE: "c41", FILM_BW: "bw"}`; for other films walk_reader returns None, and StripWalk.judge falls back to `frame_offset_mm(image, self.base)` / `right_gap_closure(...)`.
```

**Failure scenario:** A strip of E-6 slide film is walked with film=positive and aiming on (`scan_roll --correct` or the window). Each frame is driven to show about 0.25 mm of base at the left and is scanned with about 0.8 mm of picture cut at the right. The adjuster's guides (350.6-unit model) show a frame that should overhang both edges.

**Fix:** Choose one frame model, measured in units (FRAME_WIDTH_UNITS). Derive TARGET_GAP and right_gap_closure's frame width from it, removing the literal 36.0. Either read edges for every film type or refuse aiming where no reader exists. Rewrite test_frame_position:299-313 and the test_ensemble reason assertion against the shared constant, and add a test that the fallback and the reader agree on where a centred frame sits.

<details><summary>Second reader's check</summary>

framing.py:586 sets FRAME_WIDTH_MM = 36.0 and :740 sets TARGET_GAP_MM = (APERTURE_MM - 36)/2, about 0.246 mm. right_gap_closure has a retyped `frame_mm: float = 36.0` default (1366-1368). FRAME_WIDTH_UNITS = 350.6 is at 1957. StripWalk.judge uses the reader when one exists and otherwise falls back to frame_offset_mm and right_gap_closure (1728-1747). walk_reader returns None unless the film is in FILM_TYPES = {negative: c41, bw: bw} (propose.py:39, 249-254). Both scan_roll.py:476 and gui.py:8186 pass walk_reader. So positive and Kodachrome rolls aim with the 36 mm model. test_frame_position.py:299-313 derives width_px from FRAME_WIDTH_MM, so a width wider than the aperture gives a negative edge and breaks the test. test_frame_edges pins 435.6 columns and 350.6. Minor: the test_ensemble reason assertion also accepts 'wider than', so it may not break on unification.

</details>

<a id="uncited-tests-vs-findings-ut-10"></a>

### UT-10 -- test_uniformity has no rejected, multi-session or phase-2 fixture; analyse uses rejected passes as the reference and refuses once phase 2 exists

**Severity** medium · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_uniformity.py:470-495`, `tests/test_uniformity.py:498-606`, `tools/uniformity.py:161-172`, `tools/uniformity.py:283-293`, `tools/uniformity.py:454`, `tools/uniformity.py:474-477`, `tools/uniformity.py:608`, `tools/uniformity.py:636-645`, `tools/uniformity.py:92-100`, `tools/uniformity.py:780`, `tools/uniformity.py:800`

**Doc claim:** CLAUDE.md:457-459 'Re-run with tools/uniformity.py analyse --tag vignette-study after any correction change' -- refuses once phase 2 or a rejected mirror is in that tag

The tests are vacuous for both known defects. Nothing builds a rejected entry, a second session, or a phase-2 (4-channel) set under the same tag, so none of the consequences is visible:
- A rejected pass is selected like any other.
- Because it is the older one, it becomes the as-is `base` or the difference pass for its orientation. The redo that replaced it is used only as a 'repeat floor' or ignored.
- A rejected mirror pass makes three mirrors, and analyse refuses outright.
- After phase 2 is captured with the default tag, the IT8 set has four mirrors plus mixed 3- and 4-channel passes. The analyse command CLAUDE.md prescribes then always refuses.

The session id that could separate the passes is not stored.

**Evidence (from the code):**

```text
select(): `if tag in (record.get("tags") or []): out.append(candidate.parent)`, with no check for `"rejected"` or the REJECTED file. A redo writes `(entry / "REJECTED").write_text(...)` and `record["tags"] = list(record.get("tags") or []) + ["rejected"]`. decompose: `base = by_orientation[AS_IS][0]` and `it8[by_orientation[name][0]]`, where ids are sorted oldest first. `meta["uniformity_session"] = session` is dropped by library.save's whitelist. Capture and analyse both default to `--tag vignette-study`. PHASE2 reuses PHASE1's labels ("IT8 as-is", "IT8 180", "IT8 turned-over" ...). The mirror check `if got_mirrors != sorted([MIRROR_X, MIRROR_Y]): problems.append(...)`. build_entry writes only `"tags": [tag]`, and each analyse test builds a single session.
```

**Failure scenario:** During capture Stefan answers 'redo' on 'IT8 as-is' because the target was seated crooked. Later `analyse --tag vignette-study` takes the crooked rejected pass as the reference and differences every orientation against it, reporting a field that comes from handling. Alternatively, after phase 2 (IR), the same command exits 1 with 'the two turned-over passes read as [4 items]'.

**Fix:** Exclude entries tagged 'rejected' (or with a REJECTED file) in select(). Persist the session and phase in a whitelisted field, or as a tag, and group analyse by session, taking the latest complete one. Give phase 2 its own default tag. Add analyse tests with a rejected as-is pass, two sessions, and a phase-2 set.

<details><summary>Second reader's check</summary>

select() (uniformity.py:161-172) includes any entry carrying the tag and ignores the 'rejected' tag and the REJECTED file written on redo (636-645). decompose takes by_orientation[AS_IS][0], the oldest, as base and by_orientation[name][0] for each difference (454, 474-477). An older rejected pass therefore becomes the reference. check_orientations refuses unless the mirror list is exactly two (283-293), so a rejected mirror pass or a phase-2 set under the same default tag (PHASE2 at 92-100 reuses the labels; `--tag` defaults to 'vignette-study' for both capture and analyse at 780 and 800) makes analyse exit 1. meta['uniformity_session'] (608) is not in library.save's fixed `scan` key list (library.py:237-260), so it is never persisted. The test fixtures (build_entry at 468) have no rejected, multi-session or 4-channel cases.

</details>

<a id="uncited-tests-vs-findings-ut-12"></a>

### UT-12 -- test_defects validates destripe, which only make_comparison uses; the comparison files skip the shading every delivered file gets

**Severity** medium · **Category** test-gap · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tests/test_defects.py:148-217`, `tools/make_comparison.py:98-122`, `rps7200/library.py:388`, `rps7200/direct.py:2733`

**Doc claim:** CLAUDE.md:180-185 'Regenerate the three comparison files after any significant change to the scan or correction path'

The tests give confidence in a correction that no operator file ever receives, while the correction that is delivered (flat-fielding) is absent from '2_corrected.tif'. They are vacuous for the known finding that the comparison files do not run the pipeline's correction, and they pin nothing a fix would break. make_comparison also cannot tell whether its input is a raw library scan.tif or an already corrected delivered file, so '1_nothing_done.tif' may already be corrected. It maps flat defects onto the scan assuming both cover FULL_FRAME.

**Evidence (from the code):**

```text
make_comparison: `raw = tiff.read(scan_path)` (any TIFF), `corrected = destripe(raw, defects, margin=12, dilate=5)`, `tiff.write("2_corrected.tif", corrected, ...)`, with no apply_shading, and `resample_reference(flat_defects.astype(float), FULL, raw.shape[1], FULL)` hard-codes FULL frames for both inputs. A grep for `destripe(` finds only tools/make_comparison.py:117. Delivered and corrected pixels come from `apply_shading` (library.py:388, direct.py:2733).
```

**Failure scenario:** After a change to apply_shading, Claude regenerates the three files as CLAUDE.md requires. They are unaffected by the change (no shading runs), and Stefan judges the change by eye on files that do not contain it.

**Fix:** Build the comparison files from a library entry: scan.tif, then library.corrected() (plus destripe if it is meant to ship), then inverted. Refuse input that is not a library entry. Add a smoke test that the '2_corrected' output equals library.corrected() for a synthetic entry.

<details><summary>Second reader's check</summary>

make_comparison reads an arbitrary TIFF (default scans/negatives/state_1800dpi.tif, not a library entry), applies only destripe, and writes 1_nothing_done.tif, 2_corrected.tif and 3_corrected_inverted.tif (make_comparison.py:95-122). apply_shading never runs, and resample_reference assumes FULL for both flat and scan. A grep for destripe( finds only make_comparison.py:117 outside tests. Delivered pixels come from apply_shading via library.corrected. test_defects validates destripe in isolation. I raised severity to medium because CLAUDE.md makes these three files the authoritative by-eye check after any correction change. A change to apply_shading is invisible in them, and the input may already be corrected.

</details>

<a id="uncited-tests-vs-findings-ut-a1"></a>

### UT-A1 -- A failed settings write is silent: _remember ignores settings.save's None, so sheet decisions made before commissioning can be lost without a word

**Severity** medium · **Category** error-handling · **Verdict** found-by-verifier · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `rps7200/settings.py:82-97`, `tools/gui.py:668-697`, `tools/gui.py:2340-2350`

settings.save swallows every write failure and returns None. _remember discards that return value, so the log only reports failures raised outside save(). The sheet section is, by settings.py's own comment (38-45), the only record of ticks, offsets and rotations before a walk is commissioned. On a read-only directory, a full disk, or a settings path whose parent cannot be created, every sheet decision is lost when the window closes and nothing says so. The test for the read-only case (settings.py docstring: 'a read-only directory ... end with the window opening on its defaults') covers not crashing, not telling the operator.

**Evidence (from the code):**

```text
settings.save: `except (OSError, TypeError, ValueError): return None`. The GUI calls `settings.save({...}, self._settings_path)` and never looks at the return value. Its only report sits in `except Exception as exc: self._say(f"could not save the settings: {exc}")`, which save's own swallowing means is never reached for OSError.
```

**Failure scenario:** The window is launched with --settings pointing at a path on a read-only backup volume, or the checkout is read-only. The operator spends twenty minutes placing frames on the contact sheet of an uncommissioned walk and closes the sheet. Every _store_sheet_state call has returned None silently. On the next launch the offsets are gone, and there was never a message.

**Fix:** Have _remember check the return value and _say() once per session when save returns None, naming the path. Consider keeping the sheet state beside the walk in the rolls/ folder as well.

<a id="uncited-tests-vs-findings-ut-03"></a>

### UT-03 -- test_optional_libraries does not touch the TIFF paths; the stored image.sha256 depends on the writer, while raw.sha256 does not

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_optional_libraries.py:1-10`, `tests/test_optional_libraries.py:28-50`, `rps7200/library.py:229`, `rps7200/library.py:191-213`, `rps7200/tiff.py:133-168`, `pyproject.toml:34-36`

The module answers a different question: whether host-side code imports without libusb. Whether both TIFF paths are lossless is answered by tests/test_tiff.py (full writer x reader matrix, array_equal) and by `make test-all`. That part is sound. The stored scan hash is not a content hash: identical pixels filed on a dev install (tifffile, deflate) and on a plain install (built-in, uncompressed) get different image.sha256. It can also change with the tifffile or zlib version. migrate-raw and repair rewrite scan.tif and re-hash it. raw.sha256 is path-independent, and test_library.py:383 checks that for the spooled-versus-in-memory case. Nothing records a digest of the decoded pixels, so the record alone cannot show that two entries, or an entry and a re-decode, hold identical pixels.

**Evidence (from the code):**

```text
The test only blocks libusb: `if os.path.basename(str(path)).startswith("libusb-1.0"): return False` and the `libusb_package` finder. library.save records `"sha256": _sha256(path / "scan.tif")`, a hash of the file bytes. tiff.write uses `kwargs["compression"] = "zlib"; kwargs["predictor"] = True` when tifffile is importable, and `_write_builtin` (uncompressed) otherwise. The raw hash is `digest.update(raw)` over the uncompressed payload. tifffile is only an optional extra (`tifffile = ["tifffile>=2021.7.2"]`) and a dev dependency.
```

**Failure scenario:** Someone compares entries across machines, or checks a re-written scan.tif against its pre-migration hash, using image.sha256. Identical pixels read as different, and the only way to establish pixel identity is to decode both files.

**Fix:** Record a pixel digest (sha256 of the C-contiguous little-endian array plus shape and dtype) next to the file hash, and have verify and reconstruct report both. Add a test that saves the same image through both writers and asserts equal pixel digests and different file digests. Rename or retarget test_optional_libraries, or add a tifffile-absent library.save/load round trip there.

<details><summary>Second reader's check</summary>

library.save records `_sha256(path / "scan.tif")`, which hashes the encoded file (library.py:229). tiff.write uses tifffile with zlib and predictor when it is installed (compress defaults to True), and the built-in uncompressed writer otherwise (tiff.py:133-168). raw.sha256 is taken over the uncompressed payload (library.py:191-213). test_optional_libraries only simulates an absent libusb. Lossless writing is covered by test_tiff.py's writer x reader x compress matrix with array_equal (lines 78-99). verify compares each entry against its own file hash (library.py:787), which is sound per entry. Only comparisons across machines or writers break. Low.

</details>

<a id="uncited-tests-vs-findings-ut-06"></a>

### UT-06 -- Output names are claimed at submit time and written later; two queued jobs with the same name overwrite one another

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:2066-2071`, `rps7200/session.py:2192-2205`, `rps7200/session.py:1074-1082`, `rps7200/session.py:1816-1853`, `rps7200/session.py:2163-2164`

_unclaimed exists so that 'a frame rescanned after a failure would otherwise land on the file the first attempt wrote'. But it checks the disk at submit time, and the first job's file does not exist until the writer thread gets to it. When a frame's prescan was replaced by a correction, the two prescans are submitted back to back with identical names. Both usually resolve to the same path, and the later job overwrites the earlier one in the operator's output folder. The roll folder's own prescanNN.tif and prescanNN-before.tif are distinct and unaffected, as is the library entry. No test submits two same-named jobs.

**Evidence (from the code):**

```text
`paths.append(_unclaimed(where / self._out_name(number, meta, roll)))` runs on the scanning thread, and FrameWriter writes on its own thread later (`for path in job.get("paths") or (): ... export.write(str(path), ...)`). `_unclaimed`: `if not wanted.exists(): return wanted`. On a dry-run roll the session files `rf.prescan` and then, straight after, `rf.prescan_before`. Both have kind="prescan", the same number and roll=name, and both have meta built from `rf.prescan_meta`, so `_out_name` returns the same `f"{_safe(roll)}_frame{number:02d}_{dpi}dpi{ir}{end}"` for both.
```

**Failure scenario:** The operator runs a survey (dry-run roll) with aiming on and an output folder set. On every corrected frame, prescans/<roll>_frameNN_300dpi.tif in the output folder ends up holding the pre-correction picture ('before'), not the corrected prescan the sheet shows.

**Fix:** Claim names inside FrameWriter at write time under a lock, or keep a set of names already handed out per session. Give the 'before' prescan a distinct suffix in _out_name. Add a test that submits two same-named jobs and asserts two files.

<details><summary>Second reader's check</summary>

On a dry run, session.py:1817-1853 calls self._file for rf.prescan and then immediately for rf.prescan_before. Both calls pass kind='prescan', the same number and roll=name, and both use meta built from rf.prescan_meta, so _out_name (2156-2164) returns the same name. _unclaimed runs at submit time (2066-2071), while FrameWriter writes on its own thread later (1074-1082). The second path is therefore normally identical to the first, and the 'before' job, which runs second, overwrites the corrected prescan in <out_dir>/prescans/. The roll-folder files (prescanNN.tif and prescanNN-before.tif) are distinct. file_entry=False does not suppress the out_dir path. Race-dependent and narrow: low.

</details>

<a id="uncited-tests-vs-findings-ut-11"></a>

### UT-11 -- test_preview checks clipping only on synthetic arrays; the GUI feeds it shading-corrected pixels, and test_gui pins that source

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_preview.py:429-462`, `tests/test_gui.py:692-699`, `tools/gui.py:3957-3972`, `tools/gui.py:3980-3987`, `tools/gui.py:4104-4110`, `tools/gui.py:4132-4134`, `rps7200/shading.py:236-279`

preview.clipping counts `== 65535` and `>= 65000` on whatever it is given. The GUI gives it flat-fielded pixels. In edge columns where gain > 1, the correction's own clip becomes 'at full scale'. In columns where gain < 1, sensor-railed raw pixels come out below 65535 and are not counted. The operator's clipping readout therefore measures the correction, not the sensor, while it is labelled 'the scan's own pixels'. The per-channel count the correction reports (report['clipped_per_channel']) is not what is shown. test_preview cannot see this. test_gui pins the source by string, so a fix that measures raw scan.tif would need to change that assertion.

**Evidence (from the code):**

```text
GUI: `pixels, source = self._finest_pixels(result)` -> `clipped = preview.clipping(pixels)`. _finest_pixels returns `self._levels[-1]` labelled `f"the scan's own {array.shape[1]}x{array.shape[0]} pixels"`. _levels comes from `image, _ = library.corrected(entry)` ("Corrected, not raw"). apply_shading: `vals = (image[...] - dark) * gain ... np.clip(vals, 0, maxval, out=vals)`. test_gui: `assert "self._levels" in source` and `assert "the scan's own" in source`.
```

**Failure scenario:** Blue sits at the sensor rail in the centre of the frame, where gain < 1 maps 65535 down to about 63000. The histogram panel reports 0% at full scale, and the operator accepts an exposure that has destroyed highlight information.

**Fix:** Compute clipping on the raw pixels (library.load or last_pixels_raw) and show the correction's clipped_per_channel separately. Change test_gui to assert behaviour, not source text, and add a test in which raw is railed but the corrected pixels are not.

<details><summary>Second reader's check</summary>

_measure_histogram computes preview.clipping on _finest_pixels (gui.py:3959-3972). That is self._levels built from library.corrected(entry) (gui.py:4110, 4130-4134), labelled 'the scan's own ... pixels'. apply_shading computes (raw - dark) * gain, with gain = (mean - dark_mean)/(light - dark), and clips (shading.py:259-279). A railed raw sample in a column where gain < 1 lands below 65535 and may land below the 65000 'near' threshold, while edge columns with gain > 1 clip earlier. The readout therefore reflects the correction rather than the sensor. test_gui.py:691-699 pins the source by string. test_preview only feeds synthetic arrays.

</details>

<a id="uncited-tests-vs-findings-ut-13"></a>

### UT-13 -- test_shortcuts' NEVER_BOUND check is vacuous by construction, and the adjuster keys are labelled as moving film when they only set an offset

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tests/test_shortcuts.py:14-29`, `rps7200/shortcuts.py:146-147`, `rps7200/shortcuts.py:170-173`, `tools/gui.py:6626-6627`, `tools/gui.py:6706-6736`

**Doc claim:** rps7200/shortcuts.py:146-147 labels 'Move the film one step left/right'

The first test can never fail. The real guard is the shallow source-text scan in tests/test_gui.py:2222-2231, which checks only names referenced directly in each `_actions` body. Today no key moves film, as confirmed by reading `_step`, `_go`, `_accept` and `_centre`. But the shortcut editor tells the operator that ←/→ in the adjuster 'Move the film', which invites the belief that the transport has already moved when only a planned offset changed.

**Evidence (from the code):**

```text
test: `ids = {a.id for a in shortcuts.ACTIONS}; assert not ids & set(shortcuts.NEVER_BOUND)`. Action ids are "prescan", "adjust_left" and so on, while NEVER_BOUND holds handler names ("on_nudge", "on_move_frames", ...), so the intersection is empty by naming convention. shortcuts: `Action("adjust_left", "adjuster", "Move the film one step left", "<Left>")`. gui: `"adjust_left": lambda: self._step(-1)` -> `_set` -> `self.sheet.offsets[self.number] = value`, with no transport command.
```

**Failure scenario:** An operator presses → several times in the frame-position window, reads 'Move the film one step right' in the editor, and assumes the film is now positioned. He then takes a single Scan (which does not apply sheet offsets) expecting the new framing.

**Fix:** Relabel the adjuster actions ('Plan this frame one step left'). Change the test to assert that no action's handler reaches Transport, nudge or Move, for example by running the actions against a stub session that records calls.

<details><summary>Second reader's check</summary>

test_shortcuts.py:21-22 intersects action ids ('adjust_left' and so on) with handler names ('on_nudge' and so on). The two are disjoint by naming, so the check cannot fail. The real guard is the source scan at test_gui.py:2222-2231. shortcuts.py:146-147 label the adjuster keys 'Move the film one step left/right'. _FrameAdjuster._actions maps them to _step, then _set, which only edits `self.sheet.offsets` and proposals (gui.py:6706-6736). No transport command is sent.

</details>

<a id="uncited-tests-vs-findings-ut-14"></a>

### UT-14 -- test_registration_walk's fake nudge echoes the request; the walk records requested millimetres, not what the param law delivers

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_registration_walk.py:37-40`, `tests/test_registration_walk.py:81-89`, `tools/roll_registration_walk.py:59-63`, `tools/roll_registration_walk.py:112-128`, `tools/roll_registration_walk.py:146-176`

Under the current law (param + 1.84 units, 0.1057 mm/unit), RUNG_MM = 0.3002 mm. The opening move of -3 rungs (-0.9006 mm) snaps to param 7, which delivers -0.9344 mm. The restore does the same. Each ladder therefore commands a net -0.068 mm (-0.64 units), not zero, and rung 0's recorded abscissa is 0.034 mm (0.32 units) short of what was sent. The test is 'net zero' only because the fake returns the request. The comment still uses the retired 1.57 law (0.816 mm), and the tool works in millimetres against the project rule. The records keep `param`, so the true values can be re-derived, but the stored 'commanded_mm' and 'asked_mm' are wrong. This test module has no bearing on SEARCH_MM or frame width.

**Evidence (from the code):**

```text
fake: `return {"param": 1, "forward": millimetres > 0, "asked_mm": float(millimetres)}`. tool: `sent = self.scanner.nudge(mm) ... return dict(sent, asked_mm=mm)`, which overwrites DirectScanner.nudge's delivered `asked_mm`. The rungs record `"commanded_mm": round(excursion, 4)`. The comment reads "Three is 0.816 mm". The test asserts `sum(scanner.moves) == pytest.approx(0.0, abs=1e-9)`.
```

**Failure scenario:** The study grades a detector against the ladder's recorded 'commanded_mm'. It sees a systematic 0.3-unit error at the first rung and a creeping offset across ladders, and attributes them to the detector or to backlash.

**Fix:** Record DirectScanner.nudge's own delivered distance, in units, next to the request, and do the excursion bookkeeping in delivered units. Make the fake use DirectScanner.param_for_mm and the real law, and assert the delivered net.

<details><summary>Second reader's check</summary>

Walk._nudge returns `dict(sent, asked_mm=mm)` (roll_registration_walk.py:128), overwriting nudge's delivered `asked_mm` (direct.py:3167-3170). `requested_mm` and `param` survive, so the delivered value can be re-derived. With RUNG_MM = FINE_MIN_MM = 0.1057*2.84 = 0.3002 mm, a -0.9006 mm move snaps to param 7 (param_for_mm: round((0.9006-0.1945)/0.1057) = 7), which delivers 0.9344 mm. The same happens on the restore, so the net commanded is about -0.068 mm, not zero. 'commanded_mm' records the requested excursion. The '0.816 mm' comment (59-60) comes from the retired 1.57 law. The fake nudge echoes the request, so the test's net-zero assertion is structural.

</details>

<a id="uncited-tests-vs-findings-ut-a2"></a>

### UT-A2 -- _unclaimed silently falls back to overwriting after 998 clashes

**Severity** low · **Category** bug · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:2192-2204`

When <stem>.ext and <stem>-2 through <stem>-999 all exist, _unclaimed returns the original name, and the caller overwrites it. That breaks the 'Nothing already there is overwritten' promise without warning. Narrow, but it is the fallback taken in exactly the situation the helper exists for.

**Evidence (from the code):**

```text
`for n in range(2, 1000): ... if not candidate.exists(): return candidate` then `return wanted`
```

**Failure scenario:** A long-lived output folder collects many roll-less scans with the same timestamp-free name pattern, or repeated Save All runs of the same passes. The thousandth write silently replaces the original file.

**Fix:** Raise, or pick a timestamped or uuid suffix, instead of returning `wanted`.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Window settings: controls, film, output, window, presets, shortcuts, rolls table, and the contact-sheet decisions (ticks, offsets, rotations, flips, sources) keyed by roll folder name | gui-settings.json (relative to the current directory), or $RPS7200_SETTINGS, or --settings, or demo/gui-settings.json under --demo; a temp gui-settings.json.part during save | JSON object, sections filtered to settings.SECTIONS on load | n/a (operator state, not scan data) | settings.save via ScannerGui._remember (tools/gui.py:668-697), called from _store_sheet_state (2340-2350), roll open, window close and similar; written whole to .part, then os.replace, with no fsync | settings.load at tools/gui.py:377; corrupt or unparsable content silently becomes defaults (settings.py:67-72) | Exact JSON round trip, but a corrupt file is silently discarded and then overwritten by the next _remember(); sheet frame-number keys come back as strings |
| Delivered picture files (output folder, Save as, Save all, Export) | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg; <out_dir>/prescans/...; <folder>/<kind>NN_<dpi>dpi[_ir].<ext>; <folder>/<roll>_<kind>NN_... | TIFF uint8/uint16 (tifffile deflate+predictor, or built-in uncompressed); or JPEG 8-bit (image>>8, quality 95, subsampling 0), with IR dropped from the JPEG | Corrected (flat-fielded), oriented, optionally reduced to mono; never inverted | export.write from FrameWriter._write (rps7200/session.py:1074-1082) and ScannerGui._deliver_one (tools/gui.py:3843-3890); names claimed by _unclaimed at submit time | Nothing in the driver (consumed by NegPy) | TIFF is exact to the corrected array; JPEG is lossy. Written before the library entry; a failure here aborts that entry |
| DNG infrared sidecar for 4-channel JPEG deliveries | <same stem as the .jpg>.dng | Uncompressed classic-TIFF LinearRaw (photometric 34892), 4 samples RGBI, uint8/uint16, 3 ExtraSamples | Corrected RGB plus an IR plane that is not shading-corrected | export._write_infrared -> dng.write, opened in place with 'wb', no existence check, no temp file and rename | NegPy (RawpyLoader); tests read it back through tiff.read | Pixels exact (tests/test_dng.py); silently overwrites an existing .dng; a failed write leaves a truncated file |
| Pillow-missing fallback of a JPEG delivery | <requested stem>.tif | TIFF with all channels | Corrected | export.write, ImportError branch (rps7200/export.py:186-190) | NegPy | Pixels exact; overwrites an existing <stem>.tif; the caller is told only through a note, not given the actual path |
| Library scan pixels and their file checksum | library/<id>/scan.tif; scan.json image.sha256 | TIFF uint8/uint16 (written by whichever writer is installed); sha256 of the encoded file bytes | Raw decode (with exceptions documented by other areas) | library.save (rps7200/library.py:179, 229); rewritten by tools/library.py migrate-raw and library direction repair | library.load, corrected, reconstruct (array_equal), verify (file-hash compare) | Pixels lossless on both writers; image.sha256 depends on the writer (tifffile compressed vs built-in uncompressed, and library versions), so it is not a pixel digest |
| Library raw bytes and their checksum | library/<id>/raw.bin.gz; scan.json raw.sha256 | gzip level 6 of the concatenated READ payloads; sha256 of the uncompressed bytes | Raw as received | library.save (rps7200/library.py:186-213) | library.read_raw, reconstruct, verify, tools/uniformity.py rebuild | Byte-exact; the checksum does not depend on the TIFF writer or on in-memory vs spooled input (test_library.py:383) |
| Uniformity capture rejection marker | library/<id>/REJECTED; scan.json tags += 'rejected' | Text file; scan.json rewritten in place | n/a | tools/uniformity.py one_pass on 'redo' (636-645); non-atomic rewrite of scan.json; index.json not refreshed | Nothing: tools/uniformity.py select() ignores it | The marker is written, but analyse does not honour it |
| Uniformity session id | (intended) scan.json meta.uniformity_session | UTC timestamp string | n/a | tools/uniformity.py:608 sets it in meta | Nobody; library.save's whitelist drops it, so it is never persisted | Lost |
| Session shading reference cache | calibration/shading.npz (--reference); calibration/shading_uniformity.npz | npz of the float64 ShadingReference | Derived | DirectScanner.ensure_shading -> save_shading (rps7200/direct.py:612-629) | ensure_shading(reuse=True) / load_shading | Not written under --no-shading or by a lazy calibration inside scan(); the lazily measured reference exists only in the session and in the entries |
| Comparison files mandated by CLAUDE.md | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif, previews/cmp_before.png, previews/cmp_after.png | uint16 TIFF; 8-bit PNG | Whatever TIFF was given as input, plus destripe only (no shading) | tools/make_comparison.py:120-136, overwritten on every run | Stefan, by eye | Not the delivered correction |
| Registration-walk study corpus | <out>/<label>NN_p1.tif, _p2.tif, _LNN.tif, plus the walk log JSON | TIFF of the 8-bit corrected prescans; JSON with commanded_mm and asked_mm | Corrected prescans; library entries only through RPS7200_DEBUG | tools/roll_registration_walk.py Walk._write/_nudge/ladder | tools/roll_registration_study.py | Images are exact; asked_mm and commanded_mm hold the requested millimetres, not the snapped delivery (the param is kept) |

**Second reader's corrections to this table:**

- **DNG sidecar.** The row says the IR plane is 'not shading-corrected', and that is not established by the code. apply_shading corrects every channel present in reference.ref (shading.py:255-279), and parse_reference builds ref from every tagged line the calibration returned (shading.py:139-176). The IR plane is therefore corrected whenever the calibration produced I-tagged lines, and passed through (counted as 'uncorrected') only when it did not.
- **gui-settings.json.** Add that a failed write is silent. settings.save returns None on OSError (settings.py:96-97), and _remember never checks that value, so neither a corrupt read nor a failed write is reported. The DEFAULT_PATH comment ('Beside the library') is wrong: the path is relative to the current directory.
- **Delivered picture files.** The failure mode is more specific than 'a failure here aborts that entry'. Any exception from export.write on any delivered path propagates out of FrameWriter._write before library.save runs (session.py:1074-1097). tests/test_roll_writer.py:67-82 pins that such a frame is an error and not `done`.
- **Output-folder prescans on a dry run.** The <out_dir>/prescans/<roll>_frameNN_<dpi>dpi file may hold the 'before' prescan, because both prescans are claimed under the same name at submit time (session.py:2066-2071, 1817-1853).
- **Uniformity REJECTED.** index.json not being refreshed was not verified here. Everything else about the row holds: the marker is written and scan.json is rewritten in place, non-atomically, with plain write_text, but select() ignores both.
- **Shading cache.** Correct. save_shading has exactly one caller, ensure_shading (direct.py:615), so a lazily triggered calibrate_shading inside scan() never writes calibration/shading.npz.

## What the operator can do

- Run tools/scan_roll.py (dry run or real) with --no-shading, or tools/scan.py with --no-shading --auto-exposure. Both still calibrate lazily inside the first prescan or probe, although the tool prints that shading is disabled.
- Set a frame offset anywhere up to ±9.39 mm (MAX_TRAVEL_MM, one param-87 command) in the frame-position window. Anything past 9.0 mm cannot be verified by the hold loop.
- Save all or Export into a folder that already holds files, as JPEG, with or without Pillow installed.
- Hand-edit gui-settings.json, and launch the window from any working directory (the default settings path is relative).
- Answer 'redo' during tools/uniformity.py capture, run capture phase 2 and repeat sessions under the default tag, and run analyse on all of it.
- Run tools/make_comparison.py on any TIFF, including a delivered and already corrected file.
- Walk or scan a roll with film=positive or kodachrome and aiming on, which uses the 36 mm frame model instead of the 350.6-unit one.
- Rebind every shortcut except the handlers in NEVER_BOUND; press the adjuster arrows, which change only the planned offset.

## What the operator should not do

- Do not use --no-shading on scan_roll (or on scan.py with --auto-exposure) to avoid calibration. It moves the calibration into the pass, which is the path recorded as stalling with LIBUSB_ERROR_PIPE.
- Do not set approved offsets beyond about 9.0 mm and expect the hold loop to confirm them. Three unverified frames in a row switch holding off for the rest of the roll.
- Do not rely on the 'Nothing already there is overwritten' dialogs for .dng companions or for the TIFF written when Pillow is missing.
- Do not point the output folder at storage that may fill or disappear during a roll. A failed delivered write aborts that frame's library entry, raw bytes included.
- Do not hand-edit gui-settings.json without a backup. A single syntax error silently resets it, and the next click erases presets, shortcuts and uncommissioned sheet decisions.
- Do not reuse the 'vignette-study' tag for phase 2 or a second session, and do not leave rejected passes under that tag if analyse should mean anything.
- Do not judge a correction change by make_comparison output. It does not apply shading.
- Do not read the histogram's 'at full scale' number as sensor clipping. It is measured on shading-corrected pixels.

## Mistakes nothing guards against

- `scan_roll.py --dry-run --no-shading` prints 'shading correction disabled: expect vertical striping' and then calibrates lazily inside the first prescan, which is the documented wedge path. Nothing refuses the flag combination.
- An offset of 9.0-9.39 mm is accepted by the adjuster, sent by the hold loop, and then reported 'could not be verified'. No warning is shown when the offset is set.
- Save all as JPEG without Pillow silently overwrites same-stem .tif files that are already in the folder.
- A JPEG Save all of RGBI passes silently overwrites same-stem .dng files that are already in the folder.
- A corrupt gui-settings.json is silently discarded and then overwritten. No message, no .corrupt copy.
- A dry-run roll with aiming and an output folder can deliver the pre-correction prescan under the frame's name, because both prescans claim the same name before either is written.
- A pass marked 'redo' in uniformity capture is still used by analyse, as the reference if it is the oldest as-is pass.
- Once phase 2 is captured under the default tag, `analyse --tag vignette-study` refuses on every run.
- make_comparison accepts a corrected delivered TIFF as '1_nothing_done', and nothing checks what the input is.
- The shortcut editor says ←/→ 'Move the film'. They do not, and a later single Scan ignores the planned offset.

## Dataflow notes

How data moves through the code paths the uncited tests guard. Each flow gives the path first, then what the tests do and do not see.

1. **Calibration flags (the roll tool)**
   - tools/scan_roll.py main() sends `--reuse`, `--no-shading` and `--reference` to calibrate() (tools/scan_roll.py:166-173), which calls DirectScanner.ensure_shading (rps7200/direct.py:572-629).
   - With skip=True it returns 'skipped' and leaves self._shading at None.
   - DirectScanner.scan_roll (3292) then calls prescan (1682-1710, shading=True hard-coded), which calls scan() (2423).
   - In scan(), the shading block (2560-2602) sees _shading None and runs calibrate_shading() (2592) inside the pass.
   - The metering probes in auto_exposure (2083) and the frame scans (3645) also call scan() with shading left at its True default.
   - test_scan_roll_calibration stops at ensure_shading, because its fake replaces scan_roll.

2. **Line reads**
   - scan() calls read_planes (1440), which loops read_lines(n, bpl, retries=1) (1485).
   - read_lines calls Transport.command, which calls _command (usb_transport.py:799-865).
   - _command's READ status leads to _read_payload, then _wait_not_busy. A CHECK status raises CheckCondition either before or after the payload.
   - read_lines catches CheckCondition, calls read_sense and maps the result to EndOfData or ScanReadError.
   - read_planes treats EndOfData as the end of the frame; ScanReadError propagates and aborts the pass.
   - NoDataYet is the only 'wait' signal. NOT READY is honoured only by start_scan and wait_warm.
   - test_sense exercises read_lines with a pre-payload refusal only.

3. **Filing into the library**
   - library.save (rps7200/library.py:170-264) takes the image and calls tiff.write (tiff.py:97-168). tifffile, if importable, writes deflate+predictor; otherwise the built-in writer writes uncompressed.
   - image.sha256 is the hash of the encoded file (229). raw.bin.gz is gzip of the payload, and raw.sha256 is taken over the uncompressed payload (186-213).
   - verify compares file hashes; reconstruct compares decoded pixels with array_equal.

4. **Delivery**
   - Session._file (rps7200/session.py:2037-2135) claims output names with _unclaimed(out_dir/_out_name) (2071) on the scanning thread and submits a FrameWriter job.
   - FrameWriter._write (1060-1103) runs preview.orient, then to_monochrome if needed, then export.write for each path (1074-1082), and only after that library.save (1083-1097).
   - export.write (export.py:158-193) chooses the format from the file suffix:
     - JPEG goes to _write_jpeg (PIL, image >> 8). If there are more than 3 channels, _write_infrared calls dng.write(<stem>.dng).
     - If Pillow is missing (ImportError), it writes tiff.write(<stem>.tif) instead.
     - TIFF goes to tiff.write.
   - The GUI's Save as, Save all and Export (tools/gui.py:3754-3890, 2405-2450) call library.corrected(entry) (library.py:~380-390, apply_shading) and then export.write.

5. **Settings**
   - settings.load (settings.py:58-79) reads into ScannerGui.remembered (gui.py:377).
   - Sheet changes go through _store_sheet_state (2340), then _remember (668-697), then settings.save (82-97). The save writes .part and then os.replace.

6. **Holding a frame at its approved position**
   - Inside scan_roll, the approved branch (direct.py:3526-3546) calls _hold_to_approved (2840-2960).
   - That calls measure_shift_mm(reference, prescan) (framing.py:1006-1123). The reach is SEARCH_MM / (APERTURE_MM / width), which is 105 px at 428 columns. It calls uniformity.register (uniformity.py:336-390), then checks the result against CONFIDENCE_FLOOR 55.
   - hold_plan (framing.py:1172-1213) then says whether to move, and nudge sends param_for_mm (direct.py:3126-3136, clamped at 87).

7. **Aiming a frame without an approved position**
   - StripWalk.judge (framing.py:1733-1747) uses the frame_edges WalkReader (350.6 units) when edge_reader(film) is not None; that holds only for negative and B&W film (tools/frame_edges/propose.py:39, 249-254).
   - Otherwise it falls back to frame_offset_mm, right_gap_closure and predict_offset, which rely on FRAME_WIDTH_MM=36, TARGET_GAP_MM and a literal 36.0.
   - The result feeds _aim_frame (direct.py:2965-3066), which calls _hold_to_approved.

8. **Uniformity study**
   - cmd_capture calls one_pass (tools/uniformity.py:563-649), which runs scanner.scan and then library.save. The uniformity_session value is dropped there.
   - On 'redo', one_pass writes a REJECTED file and adds a 'rejected' tag.
   - cmd_analyse (370-425) calls select(tag), which ignores 'rejected'. It then calls rebuild (110-158: read_raw, then decode_index, then apply_shading), check_orientations (216-303) and decompose_and_report (442-535). decompose_and_report takes by_orientation[...][0], the oldest pass for each orientation.

9. **GUI histogram**
   - _load_full reads library.corrected(entry) on a worker thread (gui.py:4104-4110). _loaded (4122-4140) builds self._levels from it.
   - _measure_histogram (3937-3977) takes _finest_pixels (3980), then rgb_only, then preview.histogram and preview.clipping (preview.py:349-371). It counts pixels at 0, at 65535 and at ≥65000 in the corrected pixels.

10. **Comparison files**
   - make_comparison.main (tools/make_comparison.py:92-138) reads the scan and the flat with tiff.read.
   - It runs flat_defect_sigma, resample_reference (FULL frames assumed) and find_column_defects, then destripe (defects.py:154-234) and invert (preview.normalise).
   - It writes 1_*, 2_* and 3_*.tif and the PNG previews. No apply_shading runs anywhere in this path.
