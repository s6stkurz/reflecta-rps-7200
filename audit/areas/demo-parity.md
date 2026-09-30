# Demo scanner vs the real scanner

Area key `demo-parity`. 22 findings: 1 critical, 4 high, 8 medium, 8 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Demo seam audit (rps7200/demo.py DemoScanner vs rps7200/direct.py DirectScanner, injected at ScanSession._open_scanner by tools/gui.py main() under --demo). The seam itself is clean: nothing in session.py or the GUI scan path branches on demo (ScannerGui.demo only changes the window title, gui.py:521; look_only only changes wording, gui.py:2695/7266). The borrowed decisions are real (param_for_mm, roll_ends, place_on_strip, _hold_to_approved, _aim_frame, _rejudge_for, byte14_for, decode_index are the driver's own objects). The problems are in what the stand-in answers and in what then gets filed:


1. Filing (the owner's central requirement): DemoScanner never publishes pre-flat-field pixels (no last_pixels_raw; its RollFrames carry no raw_image/raw_prescan), so the session files whatever the demo returns. That is shading-corrected, sometimes also resampled or np.roll-shifted, and the record says raw. The capture_record() handed to the session can also be stale: a prescan served from prescan.tif leaves the previous pass's raw bytes, reference and CCD mask in place. When raw bytes are dropped, the reference and mask are kept even though they no longer describe the pixels. The same raw_image omission exists in ScanSession._scan for the REAL scanner: every GUI single scan is filed corrected-as-raw and then shaded a second time by library.corrected() in the 1:1 view and in Save As (critical).
2. Nothing marks a demo entry. The meta key "demo": True is dropped by library.save's whitelist, and INQUIRY ("DEMO MF Scanner") is accepted by save() but never written. --demo pins nothing when --library, --rolls, --out or --settings is given. So a demo run can put indistinguishable entries into the real library, and it can overwrite a real walk's survey.json, prescanNN.tif and approved.json.
3. Refusals and behaviour that diverge from the hardware:
   - Scans above 3600 dpi: DirectScanner raises ShadingUnavailable, the demo accepts them.
   - A prescan at any dpi other than the stored one gets the 300 dpi prescan.tif, labelled with the dpi that was asked for.
   - An RGBI request can come back with 3 channels.
   - Scans ignore film moves; only prescans show them.
   - The demo's copy of the roll loop omits source= and should_stop, observes the StripWalk only on frames without a hold, and has no per-frame failure path or blank-frame end.
   - The backlash model is much smaller than the one documented in demo.py itself.
   - Implicit calibration is not modelled.
   - The empty-library test card breaks the hold loop and the resolution contract.
4. User errors:
   - --look-only without --demo drives the real scanner under wording that promises refusals the real backend never makes.
   - --demo-entry is silently ignored.
   - In both real and demo modes, approved.json goes to rolls/_safe(name), and to "roll" when the name is blank, while the roll writes to rolls/<name or date>. The one file that cannot be re-derived therefore lands beside no roll, and reopening loses it.

Tests (tests/test_demo.py) check filing only by calling library.save directly, on fixtures with no shading reference, so none of the filing defects are visible to them.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [DP-01](#demo-parity-dp-01) | critical | data-integrity | GUI single Scan files shading-corrected pixels as raw scan.tif (real scanner and demo); library.corrected() then shades them a second time | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [DP-02](#demo-parity-dp-02) | high | demo-divergence | DemoScanner never exposes pre-correction pixels, so every demo prescan and roll frame is filed with corrected, resampled or shifted pixels labelled raw | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [DP-03](#demo-parity-dp-03) | high | data-integrity | Demo capture_record() is stale for prescans served from prescan.tif, and dropping raw keeps a reference and CCD mask that no longer describe the pixels | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [DP-04](#demo-parity-dp-04) | high | demo-divergence | Demo accepts scans above 3600 dpi (7200 in the GUI's ladder) that DirectScanner refuses with ShadingUnavailable before any pass | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-07](#demo-parity-dp-07) | high | bug | approved.json is written to rolls/_safe(name) ('roll' when blank) while the roll writes to rolls/<name or date>; reopening loses the approved positions | [P18](../problems/P18-roll-folder-identity.md) |
| [DP-05](#demo-parity-dp-05) | medium | user-error | --demo pins nothing when --library/--rolls/--out/--settings are given; a demo run can overwrite a real walk's survey.json, prescanNN.tif and approved.json | [P18](../problems/P18-roll-folder-identity.md) |
| [DP-06](#demo-parity-dp-06) | medium | library-completeness | Demo entries are indistinguishable from real scans: the 'demo' flag is dropped by library.save, and INQUIRY is never recorded | [P09](../problems/P09-record-missing-parameters.md) |
| [DP-08](#demo-parity-dp-08) | medium | demo-divergence | Demo prescans ignore the requested resolution and are mislabelled; the demo roll always prescans at 300 dpi whatever prescan_resolution is | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-09](#demo-parity-dp-09) | medium | demo-divergence | Demo roll loop diverges from DirectScanner.scan_roll: holds logged as 'operator', stop not passed to hold/aim, StripWalk not fed from held frames, no per-frame failure path | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-10](#demo-parity-dp-10) | medium | demo-divergence | Demo scans ignore where the film was moved; only prescans are shifted | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-11](#demo-parity-dp-11) | medium | demo-divergence | An RGBI request in the demo can return 3 channels; the hardware always returns 4 | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-13](#demo-parity-dp-13) | medium | user-error | --look-only without --demo drives the real scanner while the window says scanning will refuse for lack of film | [P28](../problems/P28-look-only-drives-real-scanner.md) |
| [DP-19](#demo-parity-dp-19) | medium | test-gap | Demo tests never exercise filing through the session, or loop parity with DirectScanner.scan_roll | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [DP-12](#demo-parity-dp-12) | low | demo-divergence | Calibration state not modelled: the demo always shades with each entry's own reference, never calibrates implicitly, and reports 'loaded' for a reference that does not exist | -- |
| [DP-14](#demo-parity-dp-14) | low | demo-divergence | Demo retypes constants and models backlash far smaller than its own docstring (and the hardware) says | -- |
| [DP-15](#demo-parity-dp-15) | low | doc-mismatch | --demo-entry is ignored whenever the film has any raw-bearing entry; open() logs that it shows the pair when it does not | -- |
| [DP-16](#demo-parity-dp-16) | low | demo-divergence | Empty library: the test card ignores resolution and depth, and the hold loop can never converge | -- |
| [DP-17](#demo-parity-dp-17) | low | error-handling | demo/pictures.npz is written non-atomically from a daemon thread, and a truncated cache kills the signing thread | -- |
| [DP-18](#demo-parity-dp-18) | low | doc-mismatch | Session docstrings still describe the transport law as param 1..8 / clamping at 8 | -- |
| [DP-A1](#demo-parity-dp-a1) | low | demo-divergence | Under --look-only the demo's prescan() does not refuse: it returns a stored photograph of film that is not there | [P28](../problems/P28-look-only-drives-real-scanner.md) |
| [DP-A2](#demo-parity-dp-a2) | low | demo-divergence | Demo timing ignores fast_infrared and metering, so an untied IR pass and a metered roll look far cheaper than on the scanner | -- |
| [DP-20](#demo-parity-dp-20) | info | demo-divergence | Paths the demo can never reach: counter silent, wrong-way direction, error frames; plus one frame per roll forced to miss | -- |

## Findings in full

<a id="demo-parity-dp-01"></a>

### DP-01 -- GUI single Scan files shading-corrected pixels as raw scan.tif (real scanner and demo); library.corrected() then shades them a second time

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:1085-1100`, `rps7200/library.py:373-391`, `tools/gui.py:3864`, `tools/gui.py:4110`, `rps7200/direct.py:2732-2733`, `rps7200/direct.py:2818`

**Doc claim:** CLAUDE.md 'The library holds raw pixels; everything else is corrected' and rps7200/session.py:1086-1087 comment 'The raw pixels where the job carries them, the delivered ones otherwise -- CLAUDE.md's rule that the library holds raw' (the fallback contradicts the rule)

_prescan reads getattr(scanner,'last_pixels_raw') and _roll passes rf.raw_image. _scan passes nothing, so every single scan from the window stores the corrected image in scan.tif. The same entry holds raw.bin.gz, which decodes to the uncorrected image, and the reference and mask, and its record claims `corrections_applied: []`. The GUI's full-resolution view (_load_full) and Save As / Save all (_deliver_one) read entries through library.corrected(), which applies the reference again. The demo reproduces this exactly because it runs the same session code.

**Evidence (from the code):**

```text
session.py:1441 `image, meta = self._scanner.scan(...)` then 1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=..., mono_channel=...)` -- no raw_image. FrameWriter 1089-1091: `raw_image = job.get("raw_image")` / `entry = library.save(job["image"] if raw_image is None else raw_image, ...)` with no `corrections=`. DirectScanner.scan returns `image, shading_report = apply_shading(image, self._shading, ccd_mask)` (2733) and keeps the raw in `self.last_pixels_raw = raw_pixels` (2818), which _scan never reads. library.corrected: `if record.get("reference") is None: ... image, report = apply_shading(image, record["reference"], record["ccd_mask"])` (385-388) runs because corrections_applied is [] and calibration.skipped is None.
```

**Failure scenario:** The operator presses Scan (1800 dpi RGBI, calibrated). The entry's scan.tif holds corrected pixels. Zooming to 1:1, or Save As, shows (corrected - dark) x gain applied twice: wrong levels and columns, silently. `make reconstruct` reports 'decode CHANGED: N of M samples differ' on every GUI single scan, false alarms that hide a real decode regression, exactly the 26-entry incident CLAUDE.md describes. The raw data is still there (raw.bin.gz), but scan.tif and the record disagree about what it is.

**Fix:** In ScanSession._scan, pass raw_image=getattr(self._scanner, 'last_pixels_raw', None), read immediately after scan() returns, the same way _prescan does. Add a session test in which the fake scanner returns corrected pixels and last_pixels_raw differs, and assert scan.tif equals the raw array. Consider making FrameWriter refuse, or pass corrections=['shading'], when raw_image is None and meta['shading'] is set, rather than silently filing delivered pixels as raw.

<details><summary>Second reader's check</summary>

I checked the code. ScanSession._scan (session.py:1440-1458) calls self._file(seq, 0, image, meta, ...) with no raw_image, while _prescan reads getattr(scanner,'last_pixels_raw') (1415) and _roll passes rf.raw_image/rf.raw_prescan (1830, 1886). _file passes raw_image=None to FrameWriter, and FrameWriter (1089-1091) then saves job['image'], the corrected array from DirectScanner.scan (`image, shading_report = apply_shading(image, self._shading, ccd_mask)` at direct.py:2733), with no corrections=. library.save therefore records corrections_applied [] while also storing the reference, the mask and raw.bin.gz (capture_record, direct.py:641-646). library.corrected (library.py:373-391) sees no 'shading' in applied, no skipped, and a reference, so it applies shading again. That is the path used by GUI _load_full (gui.py:4110) and _deliver_one/Save As (gui.py:3864). reconstruct (library.py:479-500) compares the raw decode with the corrected scan.tif and reports a mismatch. The git history (0f7c21d) shows raw_image was added to _prescan and _roll but never to _scan. No test covers it: the conftest fakes return reference None and no last_pixels_raw (conftest.py:193-194, 359-360). The raw bytes survive in raw.bin.gz, so the loss is recoverable, but the delivered pixels are wrong without any warning.

</details>

<a id="demo-parity-dp-02"></a>

### DP-02 -- DemoScanner never exposes pre-correction pixels, so every demo prescan and roll frame is filed with corrected, resampled or shifted pixels labelled raw

**Severity** high · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:257-264`, `rps7200/demo.py:1008-1010`, `rps7200/demo.py:771-779`, `rps7200/session.py:1415`, `rps7200/session.py:1830`, `rps7200/session.py:1886`, `rps7200/demo.py:15-19`, `tests/test_demo.py:69-82`

**Doc claim:** rps7200/demo.py:15-19 ('capture_record can hand the session genuine bytes to file. An entry the demo files reconstructs like any other'); rps7200/demo.py:438-441; README.md:221-225

The demo returns pixels after apply_shading, and after _resample, _as_positioned's np.roll and channel slicing. It gives the session no raw counterpart, so FrameWriter files those delivered pixels as scan.tif. When the shapes match, the entry also receives raw.bin.gz and shading.npz from the source entry, so its raw bytes decode to a different (uncorrected) image and library.corrected() shades it again. The module docstring's claim that 'An entry the demo files reconstructs like any other' is false through the session. test_what_it_files_can_be_reconstructed passes only because it calls library.save directly on a fixture with no shading reference.

**Evidence (from the code):**

```text
DemoScanner.__init__ sets `self.last_raw = None`, `self.last_raw_layout = None`, `self.last_scan_meta = None` but never `last_pixels_raw`. It is not a DirectScanner subclass, so the class attribute does not apply. _decode 1009-1010: `if reference is not None and not (...).get("skipped"): image, self._shading_report = apply_shading(image, reference, mask)`. The demo roll yields `RollFrame(index=..., position=..., image=image, meta=meta or {}, prescan=prescan, registration=marks, prescan_meta=prescan_meta)` with no raw_image/raw_prescan. The session files `raw_image=getattr(self._scanner, "last_pixels_raw", None)` (1415), `raw_image=rf.raw_prescan` (1830) and `raw_image=rf.raw_image` (1886), all None for the demo.
```

**Failure scenario:** make run-demo on a library whose entries have shading.npz. Prescan, then Scan 1800 dpi. demo/library/<id>/scan.tif is corrected. `tools/library.py reconstruct --root demo/library` reports 'decode CHANGED' for every entry, and the 1:1 view in the demo shows doubly shaded pixels. A walk's hold-loop prescans are filed as np.roll-wrapped synthetic images labelled raw.

**Fix:** Give DemoScanner the same contract as DirectScanner. Keep the decoded pre-shading array and set self.last_pixels_raw on every pass, transformed in the same way when resized or shifted, or else set it to None and pass corrections=... so the entry says it is not raw. Populate RollFrame.raw_image and raw_prescan. Change test_demo to file through ScanSession with a fixture that has a reference, and assert that reconstruct returns 'identical'.

<details><summary>Second reader's check</summary>

DemoScanner is not a DirectScanner subclass and never sets last_pixels_raw (demo.py:257-264). _decode applies shading whenever a reference exists (demo.py:1008-1010). The demo roll yields RollFrame with no raw_image or raw_prescan (demo.py:771-779; the defaults are None per direct.py:397-398). The session therefore files the demo's corrected, resampled or np.roll-shifted pixels as scan.tif, and when the shapes match it also files the source entry's raw.bin.gz and shading.npz (capture from _decode). test_what_it_files_can_be_reconstructed (test_demo.py:69-82) calls library.save directly on a fixture with no reference, so apply_shading never runs, which is why it passes. The module docstring claim at demo.py:15-19 is contradicted when filing goes through the session. Single scans in the demo are also hit by DP-01 independently; prescans and roll frames are demo-specific.

</details>

<a id="demo-parity-dp-03"></a>

### DP-03 -- Demo capture_record() is stale for prescans served from prescan.tif, and dropping raw keeps a reference and CCD mask that no longer describe the pixels

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:1176-1184`, `rps7200/demo.py:429-433`, `rps7200/demo.py:1191-1199`, `rps7200/demo.py:508-518`, `rps7200/session.py:2072-2099`, `rps7200/shading.py:185-193`

**Doc claim:** rps7200/demo.py:21-25 ('the bytes are dropped and only the calibration is kept ... filing them would make an entry whose raw decodes to a different picture, which is the one thing the library exists to prevent'); README.md:223-225

A demo prescan read from a stored prescan.tif is filed with whatever reference, CCD mask and raw bytes the last _decode left behind, which may come from a different entry, resolution and picture. The shape guard compares only lines, width and channels. Every 300 dpi full-frame prescan has the same shape, so when the stale bytes come from a 300 dpi source the wrong raw bytes are filed with a different photograph. _read_as_carriage can also line-reverse those stale bytes. Even when raw is dropped (resized, channel-sliced or stale), the source's reference and resolution-specific CCD mask are kept. apply_shading then maps columns silently with a mask from another pass.

**Evidence (from the code):**

```text
_pair_image: `if name.startswith("prescan"): tif = source / name; if tif.exists(): ... return image` returns before `self._capture = capture`, so _capture still holds the previous pass. _drop_raw: `self._capture = dict(self._capture, raw=None, raw_layout=None)` ('Keep the calibration, forget the bytes'). Session guard: `if capture.get("raw") is not None ...: ... if disagree: ... capture = dict(capture, raw=None, raw_path=None, raw_layout=None)`; the reference and ccd_mask always pass through. build_width_to_loc: `locs = np.flatnonzero(... == MASK_USED); return locs[:width]`, with no check that the mask belongs to this resolution.
```

**Failure scenario:** In one demo session: Scan at 1800 dpi (decodes entry X and keeps X's shading.npz and 1800 dpi ccd_mask), then walk a roll. Every walk prescan (from other entries' prescan.tif) is filed with X's reference and 1800 dpi mask, and the record says it is correctable. library.corrected() applies them (the GUI's 1:1 view of each prescan), which gives wrong columns and double shading. If the source for the film was a 300 dpi walk-prescan entry, the prescans are also filed with that entry's raw.bin.gz, whose raw decodes to a different picture.

**Fix:** Have every demo pass set _capture explicitly: all None when the pixels come from a TIFF, and never inherited. When the pixels are resized, shifted or sliced, drop the reference and mask along with the raw bytes, or transform all three. In ScanSession._file, drop the reference and mask together with raw when the layout disagrees, and compare dtype as well as shape.

<details><summary>Second reader's check</summary>

_pair_image (demo.py:1176-1184) returns a stored prescan.tif before `self._capture = capture`, so capture_record() returns whatever the previous _decode left. _drop_raw (429-433) keeps the reference and ccd_mask. Session _file (2072-2099) compares only lines, width and channels and keeps the reference and mask when it drops raw. build_width_to_loc (shading.py:185-193) takes the first `width` used columns of whatever mask it is given, with no resolution check. Two consequences follow. A demo dry-run walk after a Scan files every prescan with the scanned entry's reference and mask. A Scan at a dpi other than the source's is resampled, keeps the source's mask, and library.corrected() re-applies it with the wrong column map. The stale-raw-with-matching-shape case needs a source of the same shape as the prescan (for example a 300 dpi RGB entry), so that part is narrower than the reference/mask part.

</details>

<a id="demo-parity-dp-04"></a>

### DP-04 -- Demo accepts scans above 3600 dpi (7200 in the GUI's ladder) that DirectScanner refuses with ShadingUnavailable before any pass

**Severity** high · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/direct.py:2560-2578`, `rps7200/direct.py:1615`, `rps7200/demo.py:530-597`, `tools/gui.py:118`, `tools/gui.py:1748-1752`, `tools/gui.py:2781-2787`

**Doc claim:** README.md:222-223 ('refuses what the device refuses'); rps7200/demo.py:27-29

Every GUI Scan and Roll uses shading=True (Scan.shading default, and scan_roll calls scan() without shading). On the hardware, any resolution needing more than 5172 columns (above about 3600 dpi) raises ShadingUnavailable: a single Scan fails, and a Roll yields an error frame per frame and gives up after max_failures. The demo returns a plausible 7200 dpi (or resampled) picture. This is the case CLAUDE.md forbids: a demo that reports something plausible and wrong about a whole resolution the software refuses.

**Evidence (from the code):**

```text
direct.py: `needed = self._shading_columns_needed(frame, resolution)`; `if needed > self.MAX_SHADING_COLUMNS: raise ShadingUnavailable(... 'Scan at 3600 dpi or below, or pass shading=False ...')`. FULL_FRAME = (0,0,10343,6887), so at 7200 dpi needed = 10344 > 5172. DemoScanner.scan has no such check; it resamples via `_shape_for(dpi)` / `_rescale`. GUI: `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`, `_dpi()` accepts 25..7200, and the sheet accepts `25 <= dpi <= 7200`.
```

**Failure scenario:** The operator tries 7200 dpi in make run-demo. The roll completes, frames are filed and the ETA looks sane. They then commission the same roll on the scanner and every frame fails with 'cannot be corrected at all', after calibration and prescans have spent minutes.

**Fix:** Take the refusal from DirectScanner rather than retyping it. Make _shading_columns_needed/MAX_SHADING_COLUMNS usable by DemoScanner.scan and raise the same ShadingUnavailable when shading and needed > MAX_SHADING_COLUMNS. Mirror scan_roll's per-frame catch so the roll yields error frames the way the driver does. Optionally stop offering 7200 with shading on in the GUI, while keeping the refusal in the backend.

<details><summary>Second reader's check</summary>

DirectScanner.scan raises ShadingUnavailable when _shading_columns_needed(frame,res) > MAX_SHADING_COLUMNS (direct.py:2560-2578). With FULL_FRAME=(0,0,10343,6887) and COORD_PER_INCH=7200, 7200 dpi needs 10344 > 5172. The GUI never sets shading=False: there is no shading kwarg in the on_scan Scan() call (gui.py:2054-2061), Scan.shading defaults to True (session.py:866), and DPI_LADDER includes 7200 (gui.py:118). DemoScanner.scan has no such check and rescales instead (_shape_for/_rescale). In a roll the driver catches ShadingUnavailable per frame and yields error frames. The demo returns plausible pictures.

</details>

<a id="demo-parity-dp-07"></a>

### DP-07 -- approved.json is written to rolls/_safe(name) ('roll' when blank) while the roll writes to rolls/<name or date>; reopening loses the approved positions

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:2929-2955`, `rps7200/session.py:1634-1637`, `rps7200/session.py:2181-2189`, `tools/gui.py:4727-4728`, `rps7200/session.py:1148-1153`

**Doc claim:** tools/gui.py:2734-2737 ('This is the sole writer of `approved.json`'); tools/gui.py:2931-2933 ('Record the positions beside the roll')

on_scan_chosen is the path the demo and --look-only exist to exercise (the only writer of approved.json). With a blank roll name, which is the default, the roll goes to rolls/<today> while approved.json goes to rolls/roll/, and every blank-named roll overwrites that same shared approved.json. A name containing spaces or punctuation ('Roll 1') gives rolls/Roll 1/ for the roll and rolls/Roll-1/ for approved.json. After --open-roll of an older walk, a blank name files the resumed roll.json under today's date, not in the walk's folder. This happens in both real and demo modes.

**Evidence (from the code):**

```text
_write_approved: `name = _safe(self.fields["roll"].get().strip())`; `folder = Path(self.session.rolls) / name`. session._safe: `cleaned = "".join(kept).strip("-.") or "roll"`. session._roll: `name = job.name or time.strftime("%Y-%m-%d")`; `out = Path(job.out) if job.out else self.rolls / name`, not sanitised. read_survey reads `read_approved(folder, ...)` from the survey/roll folder. `self.last_roll_dir` exists 'so a caller that wants to file something beside that manifest ... can ask rather than recompute the same name and drift out of step with it', and _write_approved does not use it. open_roll never sets fields['roll'].
```

**Failure scenario:** The operator walks a strip with the name field blank (folder rolls/2026-09-23), sets positions in the sheet and commissions. approved.json lands in rolls/roll/. The next day they reopen rolls/2026-09-23: read_approved finds nothing, positions and turns are gone, and a later blank-named roll overwrites rolls/roll/approved.json.

**Fix:** Derive one folder in one place: have the session compute and _safe() the roll directory (store it before the job runs, e.g. expose ScanSession.roll_dir(name)), and have _write_approved use it. Alternatively, move approved.json writing into ScanSession._roll so it lands beside roll.json. When a roll is reopened, set fields['roll'] from the manifest.

<details><summary>Second reader's check</summary>

_write_approved uses `_safe(self.fields['roll'].get().strip())` under session.rolls (gui.py:2940-2941), and _safe returns 'roll' for an empty string (session.py:2184). The Roll job is submitted with name=self.fields['roll'].get().strip() unsanitised (gui.py:2862), and _roll uses `job.name or time.strftime('%Y-%m-%d')` with no _safe (session.py:1634-1635). The roll name is not a remembered field (REMEMBERED_FILM, gui.py:194), and open_roll never sets it (every fields['roll'] use is a .get()), so a blank name is the default case. approved.json is then read back from the roll's own folder via read_approved(folder) (gui.py:4727). session.last_roll_dir exists but is set only when the roll runs, after _write_approved.

</details>

<a id="demo-parity-dp-05"></a>

### DP-05 -- --demo pins nothing when --library/--rolls/--out/--settings are given; a demo run can overwrite a real walk's survey.json, prescanNN.tif and approved.json

**Severity** medium · **Category** user-error · **Verdict** partly · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `tools/gui.py:8117-8122`, `tools/gui.py:8132`, `tools/gui.py:8150`, `tools/gui.py:8162-8183`, `rps7200/session.py:1634-1643`, `rps7200/session.py:1817-1833`, `tools/gui.py:2939-2955`

**Doc claim:** tools/gui.py:8162-8167 ('which --demo pins under demo/'); tools/gui.py:8083-8086 ('it must not file into it'); README.md:225 ('Its output goes under demo/'); .gitignore 'so trying the window out leaves no trace'

Under --demo, only the defaults move to demo/. An explicit --library library files demo entries, which carry no demo marker (see DP-06), into the real library. An explicit --rolls rolls makes a demo walk with the same roll name (or a blank name on the same day, since the date is the default) rewrite the real survey.json and overwrite the real prescanNN.tif. 'Scan chosen frames' overwrites the real approved.json, the one roll file that cannot be re-derived. --out sends demo frames into the real output folder, where NegPy picks them up. Nothing refuses or warns, and the in-code guarantee depends on --rolls not being passed.

**Evidence (from the code):**

```text
`session = ScanSession(root=args.library or str(home / "library"), reference=..., rolls=args.rolls or str(home / "rolls"), out_dir=args.out)`; the comment at 8165-8167 says 'nothing the window does afterwards can write back into the walk it is showing' because '`_write_approved` derives its folder from `session.rolls`, which --demo pins under `demo/`'. Session: `name = job.name or time.strftime("%Y-%m-%d")`, `out = ... self.rolls / name`; for dry_run `earlier` is not read, so survey.json is rewritten; `surveyed = out / f"prescan{number:02d}.tif"` is passed as `path=` and is not `_unclaimed`. `_write_approved` writes `(folder / "approved.json").write_text(...)` unconditionally.
```

**Failure scenario:** `uv run python tools/gui.py --demo --rolls rolls --open-roll aligned-strip` to try commissioning a real walk. The roll name field says 'aligned-strip' (restored from demo settings). Scan chosen frames writes rolls/aligned-strip/approved.json with the demo's decisions and destroys the real ones. A demo dry-run walk under that name replaces survey.json and prescan01..NN.tif of the real walk.

**Fix:** Under --demo, refuse (ap.error), or force under demo/, any --library/--rolls that resolve outside DEMO_ROOT, unless an explicit --demo-writes-real flag is given. Have ScanSession refuse to overwrite an existing survey.json or approved.json it did not create in this session. Fix the comment at gui.py:8162-8167.

<details><summary>Second reader's check</summary>

The mechanism is real. main() (gui.py:8150, 8177-8182) uses args.library / args.rolls / args.out verbatim under --demo, only the defaults move under demo/, and the comment at 8162-8167 claims --demo pins session.rolls, which is true only when --rolls is absent. _write_approved (2939-2955) writes unconditionally into session.rolls/_safe(name). A dry run does not read `earlier` and rewrites survey.json, and prescanNN.tif is written in place. The failure scenario is partly wrong, though: the roll name field is NOT restored from settings. REMEMBERED_FILM = ('stock','process','tags') (gui.py:194), so the field starts blank, and the harm then goes to rolls/roll/approved.json (see DP-07) or to a folder named after whatever the operator types. The documented entry points (make run-demo/run-sheet, tasks.py:157-185) never pass --rolls/--library, so this needs an explicit flag.

</details>

<a id="demo-parity-dp-06"></a>

### DP-06 -- Demo entries are indistinguishable from real scans: the 'demo' flag is dropped by library.save, and INQUIRY is never recorded

**Severity** medium · **Category** library-completeness · **Verdict** partly · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:142`, `rps7200/library.py:237-261`, `rps7200/library.py:676-698`, `rps7200/session.py:2137`, `rps7200/demo.py:475`, `rps7200/demo.py:591`

**Doc claim:** rps7200/library.py:7-12 ('saving enough beside the pixels ... provenance'); rps7200/library.py:16 (scan.json 'every setting, the device state, and the provenance')

library.save drops the demo meta's 'demo' flag and ignores `inquiry=`, so no entry records which device (or the demo) produced it. Demo entries can be distinguished only by incidental nulls (protocol_revision, frame, exposure) and carriage_state.modelled, which no tool reads. Separately, and not specific to the demo, signature() cannot tell apart different pictures scanned with blank film notes at identical settings.

**Evidence (from the code):**

```text
The demo meta carries `"demo": True`, but library.save copies only whitelisted keys: `"scan": {k: meta.get(k) for k in ("resolution_dpi", "frame", ... "filter_offsets")}`, which does not include 'demo'. save() takes `inquiry: Any = None` and never uses it (grep: only the parameter line). The session passes `inquiry=getattr(self._scanner, "_inquiry", None)`, the demo's `_Inquiry` ('DEMO MF Scanner ... (no scanner attached)').
```

**Failure scenario:** A demo run filed into the real library (DP-05), or a demo/library copied or merged by hand. reconstruct, verify, the dpi timing analysis and the registration-margin fit then treat synthetic, modelled, resampled passes as scanner evidence, and nothing in scan.json lets a tool or a person exclude them.

**Fix:** Persist provenance: write record['device'] = inquiry fields (describe() at least) in library.save, and add 'demo' to the whitelisted keys, or a top-level record['source'] = 'demo'. Have reconstruct, verify and duplicates report or skip demo entries explicitly.

<details><summary>Second reader's check</summary>

Confirmed: the demo meta's 'demo': True is not in library.save's whitelisted scan keys (library.py:237-261), and the `inquiry` parameter is accepted but never used (grep shows only line 142), so no entry records the device, real or demo. The duplicates/prune claim is mis-attributed. signature() (library.py:676-698) keys on film stock/frame/subject, dpi, channels, frame, depth, film, protocol_revision, commanded exposure and fast_infrared. Blank-notes single scans at the same settings collapse whether they are demo or real (real ones share protocol_revision and FULL_FRAME just as demo ones share None), so this is a general signature weakness, not a demo-specific one. The duration_s pollution of dpi_analysis (tools/dpi_analysis.py:60) is real only if demo entries reach the analysed library.

</details>

<a id="demo-parity-dp-08"></a>

### DP-08 -- Demo prescans ignore the requested resolution and are mislabelled; the demo roll always prescans at 300 dpi whatever prescan_resolution is

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:1164-1184`, `rps7200/demo.py:455-477`, `rps7200/demo.py:599-616`, `rps7200/demo.py:708`, `rps7200/demo.py:722-725`, `rps7200/demo.py:1202-1224`, `rps7200/session.py:1796-1800`, `rps7200/session.py:1686`

**Doc claim:** README.md:222 ('answers at the resolution asked for'); rps7200/demo.py:935-938 ('A pass reported as 900 dpi that hands back 1800 dpi pixels is a stand-in that lies')

A Prescan job at 600/900 dpi, or a walk with prescan dpi 600, gets the stored (usually 300 dpi) prescan.tif labelled with the requested dpi. The demo roll ignores prescan_resolution entirely, while the session labels results and survey.json with job.prescan_resolution. Filed demo entries and manifests therefore say 600 dpi for 300 dpi pixels. The on_scan_chosen logic that holds predpi to the survey's resolution to avoid 'about half the correlation confidence' cannot be observed in the demo. The fallback _pixels path breaks the demo's own rule that a pass must come back at its resolution.

**Evidence (from the code):**

```text
_pair_image returns a stored prescan.tif immediately: `if tif.exists(): ... image = tiff.read(str(tif)); ... return image`. The dpi argument is only used after `_decode`. prescan() meta: `"resolution_dpi": resolution, ... "width": image.shape[1], "height": image.shape[0]`. scan_roll accepts prescan_resolution only via **kw and calls `self.prescan(film=film)` (default 300) and `self._hold_to_approved(index, prescan, 300, ...)`. _pixels returns `image` at the source entry's native size, or `_test_card` (574x862), with no rescale.
```

**Failure scenario:** Set prescan dpi to 600 and walk in the demo. survey.json has prescan_resolution 600, the prescans are 428 px wide, and demo/library entries say resolution_dpi 600. On reopen, _survey_predpi = 600 and approved references are mislabelled. The sheet's size arithmetic (units_per_column etc.) runs on the wrong dpi.

**Fix:** In _pair_image, resample the prescan.tif result to _shape_for(dpi) (and drop capture) when its stored resolution differs. Honour prescan_resolution in DemoScanner.scan_roll and in the hold call. Rescale in _pixels as well.

<details><summary>Second reader's check</summary>

_pair_image returns prescan.tif as stored, whatever the dpi (demo.py:1176-1184). prescan() labels the meta with the requested resolution (demo.py:472-476). scan_roll takes prescan_resolution only through **kw, calls self.prescan(film=film) with the 300 default (708) and holds at 300 (722-725), while the session passes prescan_resolution (session.py:1763) and labels the results and survey.json with job.prescan_resolution (1796-1800, manifest). _pixels returns the source's native size or a 574x862 card without rescaling.

</details>

<a id="demo-parity-dp-09"></a>

### DP-09 -- Demo roll loop diverges from DirectScanner.scan_roll: holds logged as 'operator', stop not passed to hold/aim, StripWalk not fed from held frames, no per-frame failure path

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:720-759`, `rps7200/direct.py:3517-3536`, `rps7200/direct.py:3571-3575`, `rps7200/direct.py:3509-3515`, `rps7200/direct.py:3662-3678`, `rps7200/demo.py:667-791`

**Doc claim:** rps7200/demo.py:617-626 ('The roll, walked the way `DirectScanner.scan_roll` walks it'); CLAUDE.md 'A number, cap, constant or decision the stand-in needs is taken from DirectScanner, never retyped'

The demo's scan_roll is its own copy of the loop, and it has drifted:
- Positions the ensemble proposed (Approved.source 'measured', 'unconfirmed' etc.) are recorded as 'operator' in registration['approved']['source'] and in the log, the misattribution _hold_to_approved's docstring says `source` exists to prevent.
- Stop pressed during a hold or aim loop is ignored until the frame ends, so the 'stopped' outcome is unreachable.
- The StripWalk prior learns from fewer frames than on the hardware, so _aim_frame decisions differ.
- Error frames, 'done': False manifest records and the resume path are never produced by the demo.
- Frame meta lacks roll_index, roll_position and registration, so demo entries file no registration evidence.

**Evidence (from the code):**

```text
Demo: `fix = self._hold_to_approved(index, prescan, 300, held, keep_raw=False, reverse=reverse_hold,)` with no should_stop and no source. Driver: `should_stop=should_stop, source=getattr(held, "source", None) or "operator"`. The driver runs `if walk is not None: marks["base"] = walk.observe(index, prescan_image)` for every frame before the held branch. The demo calls walk.observe only in `elif walk is not None:` (unapproved frames), and after a replaced prescan it does `marks = self._marks(prescan)`, dropping 'base'. The driver catches `(UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError)` per frame and yields RollFrame(error=...). The demo has no except clause, no blank_contrast end-of-film and no drift_warning check.
```

**Failure scenario:** In a demo commissioned from the sheet, the log says 'frame 4: operator +12.3 units -> held' for a detector's proposal. The operator pressing Stop mid-hold sees two more nudges and prescans. A developer judges _aim_frame's decisions in the demo and gets different numbers on the hardware, because the walk saw held frames there.

**Fix:** Pass should_stop and source= exactly as the driver does. Move walk.observe before the held branch. Add the driver's per-frame except/yield error frame and max_failures, the blank_contrast end and the meta keys. Better, factor the per-frame body of DirectScanner.scan_roll into a shared function that both backends call.

<details><summary>Second reader's check</summary>

Compared line by line with DirectScanner.scan_roll (direct.py:3492-3680). The driver runs walk.observe for every frame (3517-3518) and passes should_stop and source= to _hold_to_approved (3526-3533). The demo passes neither (demo.py:722-725), so _hold_to_approved's source defaults and logs 'operator'. The demo calls walk.observe only in the unapproved branch, and `marks = self._marks(prescan)` after an aim replacement discards 'base'. The demo also lacks the per-frame except clause and error RollFrame, max_failures, the blank_contrast end-of-film check, the drift_warning log and meta roll_index/roll_position/registration. It never yields prescan_before, so prescanNN-before.tif is unreachable, and it ignores meter/fast_infrared/max_failures (swallowed by **kw).

</details>

<a id="demo-parity-dp-10"></a>

### DP-10 -- Demo scans ignore where the film was moved; only prescans are shifted

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:414-427`, `rps7200/demo.py:469`, `rps7200/demo.py:565-597`, `rps7200/demo.py:688`

**Doc claim:** rps7200/demo.py:414-420 ('a pass has to come back showing where the film actually is')

A nudge (Move millimetres), or a hold or aim inside a roll, moves the simulated film. The next prescan shows it, but the scan that follows returns the unmoved source picture. On the hardware the scan shows the same film position as the verification prescan. The demo therefore shows 'held' outcomes whose frame scans never reflect the hold, and _note_reversal compares a shifted prescan against an unshifted scan.

**Evidence (from the code):**

```text
prescan(): `image, read = self._read_as_carriage(self._as_positioned(image), ONE_PASS_COLOR)`. scan(): `image = self._pair_image("scan.tif", film, resolution)` ... `image, read = self._read_as_carriage(image, ...)`, with no _as_positioned.
```

**Failure scenario:** The operator sets a +20-unit position on a frame in the demo sheet and commissions. The log reports held after 1 move and the prescan is shifted, but the filed frame is pixel-identical to an uncorrected scan, so the operator concludes that holding does nothing, or trusts framing the hardware will not reproduce.

**Fix:** Apply _as_positioned in scan() as well, scaled to the scan's width. Drop the raw bytes, reference and mask when a shift is applied (see DP-03).

<details><summary>Second reader's check</summary>

prescan() applies self._as_positioned (demo.py:469). scan() calls _pair_image and then _read_as_carriage with no _as_positioned (565-594). _film_mm is set by nudge and reset only at the start of each roll frame (demo.py:700-702), so after a hold the scan shows the unshifted source.

</details>

<a id="demo-parity-dp-11"></a>

### DP-11 -- An RGBI request in the demo can return 3 channels; the hardware always returns 4

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:565-579`, `rps7200/demo.py:593-594`, `rps7200/demo.py:862-902`, `rps7200/direct.py:2623-2624`

**Doc claim:** README.md:221-223

When the per-film source entry is RGB, Scan with infrared returns an RGB image with channels 3. The carriage model then uses ONE_PASS_COLOR, whose byte-14 bit 0 is clear, so the next prescan is not modelled as bottom-up although the hardware's RGBI pass would leave the carriage at the far end. The infrared channel view, the '_ir' file names and the roll manifest's infrared: true all disagree with what was delivered.

**Evidence (from the code):**

```text
scan(): `image = self._pair_image("scan.tif", film, resolution)`; `if image is None: image = self._pixels(channels=4 if infrared else 3)`; `elif not infrared and image.shape[2] > 3:` (only the other direction is handled). meta `"channels": image.shape[2]`; `_read_as_carriage(image, ONE_PASS_RGBI if image.shape[2] > 3 else ONE_PASS_COLOR)`. _source_for picks the entry nearest 1800 dpi of the film without regard to channel count.
```

**Failure scenario:** Demo with a library whose nearest-1800 dpi negative is RGB. Tick infrared and scan: no I plane, the file is named without _ir, the entry id has no 'ir', and the bottom-up prescan path is not exercised.

**Fix:** In _source_for, prefer entries whose channel count matches the request, or synthesise the extra plane. Always return the channel count the pass asked for, drop raw when it cannot, and model the carriage from the requested passes (infrared), not from the array.

<details><summary>Second reader's check</summary>

_source_for filters on film and raw.bin.gz existence only, not channels (demo.py:874-902). scan() handles only the 4→3 slice and never 3→4 (577-579). The meta channels and the carriage model follow image.shape[2] (593-594). The session's _out_name and library entry_id both key '_ir' / 'ir' on meta channels >= 4 (session.py:2157-2158, library.py:126-127). A roll's _frame_source (strip entries with a prescan) has the same issue.

</details>

<a id="demo-parity-dp-13"></a>

### DP-13 -- --look-only without --demo drives the real scanner while the window says scanning will refuse for lack of film

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P28](../problems/P28-look-only-drives-real-scanner.md)

**Where:** `tools/gui.py:8145-8147`, `tools/gui.py:8187-8203`, `tools/gui.py:7266-7271`, `rps7200/direct.py:2543-2552`, `rps7200/demo.py:826-844`

**Doc claim:** tools/gui.py:8146-8147 help text; tools/gui.py:7266-7271 sheet text; CLAUDE.md 'Calibrate with the film loaded' / 'Ask before driving the scanner'

Only DemoScanner refuses when there is no film; DirectScanner never raises for an empty transport. With --look-only and a real scanner attached, nothing rejects the combination. The sheet promises a refusal, yet 'Scan chosen frames', Calibrate and Move run on the real device. Per CLAUDE.md, calibrating an empty transport preceded a wedge, and movement needs Stefan's agreement.

**Evidence (from the code):**

```text
`ap.add_argument("--look-only", ... help="there is no film in the transport. Every control still works; anything that reaches for film says so")`; `no_film=args.look_only` is passed only inside `if args.demo:`, while `ScannerGui(..., look_only=args.look_only, ...)` is unconditional. Sheet text: 'There is no film in the transport ... Scanning is offered as it always is, and will say there is no film when it reaches for it.' DirectScanner.scan: 'Reported, not enforced ... continuing'; NoMediaLoaded is imported but never raised anywhere.
```

**Failure scenario:** Someone runs `uv run python tools/gui.py --look-only --open-roll rolls/X` (forgetting --demo) to browse a walk with the scanner plugged in. Pressing 'Scan chosen frames' as the text invites seeks, moves the transport, calibrates an empty transport and scans.

**Fix:** ap.error when --look-only is given without --demo, or give DirectScanner a no_film guard (a SessionGuard) that raises NoMediaLoaded for any motion or pass when look_only is set.

<details><summary>Second reader's check</summary>

no_film=args.look_only is passed only inside `if args.demo:` (gui.py:8187-8199), while look_only=args.look_only reaches ScannerGui unconditionally (8202-8203) and changes the sheet's text to promise that scanning 'will say there is no film' (7266-7271). NoMediaLoaded is defined and exported but never raised (grep of rps7200/ and tools/). DirectScanner.scan only logs the media bit ('Reported, not enforced', direct.py:2543-2552). An askokcancel dialog does stand before 'Scan chosen frames' (gui.py:2817-2829), but it does not mention the missing film.

</details>

<a id="demo-parity-dp-19"></a>

### DP-19 -- Demo tests never exercise filing through the session, or loop parity with DirectScanner.scan_roll

**Severity** medium · **Category** test-gap · **Verdict** partly · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `tests/test_demo.py:69-82`, `tests/test_demo.py:548-573`, `tests/test_demo.py:605-616`, `tests/conftest.py:193-194`, `tests/conftest.py:359-360`, `tests/test_session.py:248-270`

**Doc claim:** tests/test_demo.py:1-8 module docstring

No test asserts what the session files from the demo (or from a single GUI Scan): the only session-driven demo test checks survey numbering. The filing tests bypass ScanSession/FrameWriter and use fixtures with no shading reference, the conftest fakes publish no reference and no last_pixels_raw, and the hold-convergence tests need an untracked library/. So DP-01 to DP-03 and DP-09 would all pass the suite.

**Evidence (from the code):**

```text
test_what_it_files_can_be_reconstructed: `out = library.save(image, meta, root=tmp_path / "out", film=FilmNotes(), **capture)`. It calls library.save directly, and the `entry()` fixture saves no reference, so apply_shading never runs. The convergence tests use `DemoScanner("library")`, the real cwd library, and skip when it is empty. No test runs `--look-only` without `--demo`, and none compares the demo loop's hold call arguments or walk.observe placement with the driver's.
```

**Failure scenario:** A regression in what the demo or the GUI files (corrected-as-raw, stale reference) ships unnoticed. The two tests that would exercise holds are always skipped on a clean checkout and in CI.

**Fix:** Add hermetic fixtures with shading.npz and a mask. Run Prescan, Scan and Roll through ScanSession with DemoScanner and assert that reconstruct returns 'identical' and that the filed reference and mask belong to the filed pixels. Add a parametrised test that runs DirectScanner.scan_roll (on ScannerOnStrip) and DemoScanner.scan_roll with the same approved/source/should_stop and compares registration['approved']['source'] and stop behaviour. Add a synthetic library for the convergence tests.

<details><summary>Second reader's check</summary>

Mostly accurate. The filing tests (test_demo.py:69-82, 742-756) call library.save directly, and the entry() fixture stores no reference or mask, so neither raw-vs-corrected nor stale calibration is exercised. One test does run DemoScanner through ScanSession (test_the_demo_is_wound_back_by_the_sessions_own_seek, test_demo.py:548-573), but it asserts only survey numbering, not what was filed. test_gui.py:1107 uses DemoScanner('library'), which depends on untracked data. No test checks _scan's raw_image, and the conftest fakes return reference None and no last_pixels_raw (conftest.py:193-194, 359-360), which is also why DP-01 is not caught.

</details>

<a id="demo-parity-dp-12"></a>

### DP-12 -- Calibration state not modelled: the demo always shades with each entry's own reference, never calibrates implicitly, and reports 'loaded' for a reference that does not exist

**Severity** low · **Category** demo-divergence · **Verdict** partly

**Where:** `rps7200/demo.py:446-453`, `rps7200/demo.py:995-1010`, `rps7200/direct.py:598-629`, `tools/gui.py:1408-1412`, `tools/gui.py:1946-1958`

**Doc claim:** README.md:221-222 ('the same shading correction a real pass runs')

The demo does not model the calibration contract. 'reuse' with no cached reference reports 'loaded' instantly, where the driver would calibrate for about 3.5 minutes. The summary has no 'reference' key. _decode applies an entry's reference even to legacy entries whose stored pixels are already shading-corrected, which double-shades them in the demo view. Implicit calibration inside scan() and shading=False are not reachable from the GUI (the _calibration_missing guard, and no shading control), so those differences have no practical effect.

**Evidence (from the code):**

```text
Demo ensure_shading: `self._work(210.0 if not reuse else 1.0); return {"action": "loaded" if reuse else "calibrated", ...}`, with no path check and no 'reference' key. Driver: `if reuse and path.exists(): ... load` else calibrate. Driver scan(): `if self._shading is None or needed > ...: ... self.calibrate_shading()`. Demo _decode applies the entry's reference whenever present, regardless of the `shading` argument or corrections_applied. The demo meta sets `"shading": self._shading_report if shading else None` and never `shading_skipped`.
```

**Failure scenario:** The operator sets Calibrate to 'off' in the demo and scans instantly with clean pictures. On the scanner the first scan silently spends minutes calibrating. A demo shown on an older library displays legacy entries darker or striped from double shading.

**Fix:** Model the session reference: ensure_shading must honour reuse only when the path exists and return 'reference'. scan() should spend the calibration time when the session has none. Return uncorrected pixels, with SHADING_SKIPPED_EXPLICIT in meta, when shading=False. In _decode, skip apply_shading when corrections_applied contains 'shading' (reuse library.corrected's decision table).

<details><summary>Second reader's check</summary>

The GUI guard _calibration_missing (gui.py:1946-1958) is called by every control that takes a picture and refuses unless self.calibrated. A 'skipped' calibration leaves calibrated False (session.py:1395-1397), and the GUI never passes shading=False, so the scenario 'Calibrate off, then scan instantly' is blocked in both modes and the demo's missing implicit calibration and shading=False handling are unreachable from the window. Two differences remain real. The main panel's 'reuse the cached reference' radio (gui.py:1408-1412) is always offered: the demo's ensure_shading answers 'loaded' in 1/120 s with no file check and no 'reference' key (demo.py:446-453), while DirectScanner calibrates for minutes when the cache is missing (direct.py:598-629). And _decode shades legacy entries whose scan.tif is already corrected (no raw, corrections_applied ['shading']) a second time (demo.py:1008-1010), where library.corrected would return them as 'already'.

</details>

<a id="demo-parity-dp-14"></a>

### DP-14 -- Demo retypes constants and models backlash far smaller than its own docstring (and the hardware) says

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:246-250`, `rps7200/demo.py:390-395`, `rps7200/demo.py:459`, `rps7200/demo.py:561-564`, `rps7200/demo.py:342`, `rps7200/demo.py:449`, `rps7200/demo.py:555`, `rps7200/demo.py:547-552`, `rps7200/session.py:102`, `rps7200/session.py:191`, `rps7200/session.py:734`, `rps7200/framing.py:1151`

**Doc claim:** rps7200/demo.py:246-248 ('Backlash, as the transport really has it: two to three commands are swallowed after a direction change'); CLAUDE.md 'never retyped'

The backlash model swallows 2.2 units (0.23 mm) once. A single command is at least 2.84 units, so 'two to three commands' is 5.7 units or more. 0.23 mm is below HOLD_TOLERANCE_MM (0.3002), so in the demo a reversal is always absorbed inside tolerance, whereas the hardware loses whole commands (session.BACKLASH_COMMANDS = 3). The other retyped numbers are second homes that can drift from session/DirectScanner, which is the pattern CLAUDE.md prohibits.

**Evidence (from the code):**

```text
`swallowed = min(abs(asked), 2.2 * 0.1057)` (0.1057 is MM_PER_UNIT/STEP_MM retyped) against the comment 'two to three commands are swallowed after a direction change'. `lines=int(resolution * 0.957)` retypes session._LINES_PER_DPI = 6888/7200. `_work(7.0)` per advance against the measured FORWARD_FRAME_S = 4.6. `_work(210.0)` calibration and `_work(48.0)` metering are invented. The infrared refusal message is retyped and lacks the driver's '(Chromogenic C-41 ...)' sentence.
```

**Failure scenario:** A hold that reverses direction converges in one move in the demo and needs two or three on the scanner, which misleads anyone tuning MAX_HOLD_MOVES or judging convergence from the demo.

**Fix:** Import MM_PER_UNIT/STEP_MM and session._LINES_PER_DPI, and FORWARD_FRAME_S for the advance cost. Express backlash in commands (e.g. swallow the next N commands after a reversal, with N from session.BACKLASH_COMMANDS). Raise the refusal through a shared helper so the message is not a copy.

<details><summary>Second reader's check</summary>

`swallowed = min(abs(asked), 2.2 * 0.1057)` retypes MM_PER_UNIT (protocol.py:260 = 0.1057), and 2.2 units = 0.2325 mm, which is below HOLD_TOLERANCE_MM 0.3002 (framing.py:1151). The comment says two to three commands are swallowed, and session.BACKLASH_COMMANDS = 3. `int(resolution * 0.957)` retypes _LINES_PER_DPI = 6888/7200 (session.py:734). `_work(7.0)` per advance is used where session.FORWARD_FRAME_S = 4.6 (session.py:191). The IR refusal text is duplicated. All of these affect timing or a simplified model only, not filed data.

</details>

<a id="demo-parity-dp-15"></a>

### DP-15 -- --demo-entry is ignored whenever the film has any raw-bearing entry; open() logs that it shows the pair when it does not

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/demo.py:238`, `rps7200/demo.py:291-296`, `rps7200/demo.py:874-902`, `tools/gui.py:8127-8130`

**Doc claim:** tools/gui.py:8128-8130 help; rps7200/demo.py:234-237 docstring

The operator's explicit choice of entry, and best_pair's pick, are used only when no entry of the requested film has raw bytes. Otherwise the demo shows a different entry than the log announces. A mistyped --demo-entry path is accepted silently.

**Evidence (from the code):**

```text
_source_for: `for path in self._entries: ... if scan.get("film") != film or not (path / "raw.bin.gz").exists(): continue; ... nearest 1800 ...`; then `self._by_film[film] = best or self.pair`, so pair is only a fallback. open() logs `demo mode: showing {self.pair.name}` / 'its own prescan answers Prescan, its scan answers Scan'. The --demo-entry help says 'a specific library entry for --demo to show'. The path is never validated.
```

**Failure scenario:** `--demo --demo-entry library/20260911T103600Z_...` to demonstrate a specific 3600 dpi frame: the window shows the 1800 dpi entry nearest 1800 instead, and the log claims otherwise.

**Fix:** Consult self.pair first in _source_for when it matches the film (or always, when it was given explicitly). Validate the path exists and has scan.json in open(). Log what _source_for actually chose.

<details><summary>Second reader's check</summary>

_source_for prefers any entry of that film with raw.bin.gz nearest 1800 dpi and falls back to self.pair only when there is none (demo.py:891-902). open() logs 'demo mode: showing {pair}' and 'its own prescan answers Prescan' (291-296) regardless of that choice. --demo-entry is not validated: a bad path reaches _decode, which logs 'could not decode' and returns None.

</details>

<a id="demo-parity-dp-16"></a>

### DP-16 -- Empty library: the test card ignores resolution and depth, and the hold loop can never converge

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:1202-1224`, `rps7200/demo.py:1259-1275`, `rps7200/demo.py:472-474`, `rps7200/demo.py:304`, `tests/test_demo.py:611-616`

**Doc claim:** tasks.py:157-161 ('or from a generated test card where the library is empty')

This checkout has no library/, so make run-demo runs on test cards:
- Every pass comes back 574x862, uint16 even for an '8-bit 300 dpi' prescan, whatever the dpi.
- measure_shift_mm correlates two unrelated cards, so holds report unverified or not_converged, which reads as a broken hold loop.
- The test-card pictures are filed into demo/library as entries with no raw bytes and no reference, and scan.depth 8 disagrees with image.dtype uint16.

**Evidence (from the code):**

```text
`_test_card`: `h, w = 574, 862` ... `.astype(np.uint16)` for every request. prescan meta still says `"depth": 8, "resolution_dpi": resolution`. `_pixels` increments `self._next` per call, so each verification prescan in _hold_to_approved is a new random card (different seed), not the same film moved. test_the_demo_converges_on_an_approved_position skips in exactly this case.
```

**Failure scenario:** A fresh clone runs make run-demo and commissions a sheet roll: every held frame misses, which an evaluator reads as a hold-loop regression.

**Fix:** Make the test card deterministic per strip position (seed by position, not by call count), size it with the shape for the requested dpi, and return uint8 for prescans. Or refuse holds with an explicit 'no pictures to register against' message.

<details><summary>Second reader's check</summary>

This checkout has no library/, demo/ or rolls/ (ls). _test_card is fixed at 574x862 uint16 (demo.py:1259-1275). prescan() still reports depth 8 and the requested dpi (472-476). _pixels increments self._next on every call, so each verification prescan in a hold is a different random card with a phase-shifted sine, and correlation measures a spurious shift. test_the_demo_converges_on_an_approved_position skips when there are no entries (test_demo.py:611-616).

</details>

<a id="demo-parity-dp-17"></a>

### DP-17 -- demo/pictures.npz is written non-atomically from a daemon thread, and a truncated cache kills the signing thread

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/demo.py:1069-1096`, `rps7200/demo.py:288-290`, `rps7200/demo.py:1105-1107`, `rps7200/demo.py:1059-1060`

**Doc claim:** README.md:227-231

Closing the demo during the first-launch signature pass can leave a truncated cache. Each later launch's signing thread then dies on an uncaught exception (printed to stderr), _signatures stays empty, and _next_strip falls back to `list(self._pool_for(film))`. Every later roll repeats the first strip, silently, until the user deletes demo/pictures.npz.

**Evidence (from the code):**

```text
`with np.load(self.cache) as data: ...` is guarded by `except (OSError, ValueError, KeyError)`; a truncated zip raises zipfile.BadZipFile and an empty file raises EOFError, neither of which is caught. `np.savez(self.cache, paths=..., signatures=...)` writes in place from `threading.Thread(target=self._sign_pictures, daemon=True)`.
```

**Failure scenario:** First make run-demo against a large library; the window is closed after a minute. From then on 'Every roll started from the Roll button after that loads another strip' (README) is false.

**Fix:** Catch Exception around the load. Write to a temporary file and os.replace. Log when the cache is discarded.

<details><summary>Second reader's check</summary>

_sign_pictures catches only (OSError, ValueError, KeyError) around np.load (demo.py:1071-1076). A truncated npz that begins with the PK magic raises zipfile.BadZipFile, and an empty file raises EOFError, neither of which is caught, so the daemon thread dies before any signature is stored. np.savez writes the cache in place from that daemon thread (1090-1096). Afterwards _pictures_for gets no signatures, _next_strip falls back to `list(self._pool_for(film))` (demo.py:1051-1052), and every later roll repeats the first strip.

</details>

<a id="demo-parity-dp-18"></a>

### DP-18 -- Session docstrings still describe the transport law as param 1..8 / clamping at 8

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:91-95`, `rps7200/session.py:672-692`, `rps7200/direct.py:3123`

**Doc claim:** rps7200/session.py:91-92, 676, 690-691

This is the transport law the demo borrows (param_for_mm, FINE_MAX_MM via plan_nudges for Move). The prose states the pre-2026-09-22 cap of 8, which is the stale value whose retyped copy in demo.py caused the documented not_converged incident.

**Evidence (from the code):**

```text
session.py:91-92: 'distance = STEP_MM x param + OVERHEAD_MM, for param 1 and param 8.' The plan_nudges docstring: 'for an integer param in 1..8' and '`DirectScanner.param_for_mm` already clamps silently at param 8'. The code has `MAX_CORRECTION_PARAM = 87`, and `FINE_MAX_MM = STEP_MM * MAX_CORRECTION_PARAM + OVERHEAD_MM`.
```

**Failure scenario:** A reader, or a future stand-in author, retypes 8 from the docstring, and the demo drifts exactly as it did before.

**Fix:** Update the comments to refer to MAX_CORRECTION_PARAM by name and not a number.

<details><summary>Second reader's check</summary>

session.py:91-92 says 'for param 1 and param 8', but FINE_MAX_MM is computed with MAX_CORRECTION_PARAM = 87 (direct.py:3123). plan_nudges' docstring says 'integer param in 1..8' and 'param_for_mm already clamps silently at param 8' (session.py:676, 690-691), while param_for_mm clamps at MAX_CORRECTION_PARAM (direct.py:3136).

</details>

<a id="demo-parity-dp-a1"></a>

### DP-A1 -- Under --look-only the demo's prescan() does not refuse: it returns a stored photograph of film that is not there

**Severity** low · **Category** demo-divergence · **Verdict** found-by-verifier · **Problem** [P28](../problems/P28-look-only-drives-real-scanner.md)

**Where:** `rps7200/demo.py:455-477`, `rps7200/demo.py:826-844`, `tools/gui.py:7266-7271`

**Doc claim:** tools/gui.py:8145-8147 help 'anything that reaches for film says so'; tools/gui.py:7266-7271

With no_film=True, the window's Prescan button returns a real stored photograph, and that photograph is also filed into demo/library as a prescan entry. The sheet text says anything that reaches for film says there is none. On the hardware an empty-transport prescan returns an image of the bare light path, not a photograph. So the look-only demo shows a plausible picture that the software could never have produced.

**Evidence (from the code):**

```text
scan(), advance(), retreat(), nudge() and scan_roll() call `self._need_film(...)`, but `def prescan(self, resolution=300, ...)` goes straight to `self._work(...)` and `image = self._pair_image("prescan.tif", film, resolution)`. ensure_shading also has no _need_film.
```

**Failure scenario:** make run-sheet, then Calibrate and Prescan: a stored negative appears as though the gate held film, and an entry is filed tagged 'prescan' with that picture.

**Fix:** Call self._need_film('prescan') in DemoScanner.prescan (and say so in ensure_shading if calibrating with no film is to be discouraged), or return a blank light-path frame so the answer matches what an empty transport gives.

<a id="demo-parity-dp-a2"></a>

### DP-A2 -- Demo timing ignores fast_infrared and metering, so an untied IR pass and a metered roll look far cheaper than on the scanner

**Severity** low · **Category** demo-divergence · **Verdict** found-by-verifier

**Where:** `rps7200/demo.py:459`, `rps7200/demo.py:561-564`, `rps7200/demo.py:599-616`, `rps7200/session.py:743-744`, `rps7200/session.py:1761-1768`

With 'infrared at scan resolution' unticked, the real pass costs a flat ~220 s even at 300 dpi (CLAUDE.md calls it 'the one that surprises you'). The demo times it as tied, about 25 s at 300 dpi. A roll with meter 'every frame' spends no probe time per frame in the demo. The demo's progress bars and its per-pass durations filed as duration_s therefore understate the real cost of exactly those options.

**Evidence (from the code):**

```text
`self._work(estimate_seconds(resolution, infrared), lines=int(resolution * 0.957))` never passes the third argument of `estimate_seconds(resolution, infrared, fast_infrared=True)`. scan() takes fast_infrared only through **kw, and scan_roll receives meter=, fast_infrared= and max_failures= from the session (session.py:1761-1768) only through **kw, never meters, and so never spends the metering time a real per-frame meter costs.
```

**Failure scenario:** An operator rehearses a low-dpi RGBI roll with fast IR off in the demo, sees each frame take ~0.2 s (25 s scaled), and then budgets the real roll from it. On the scanner every frame takes ~220 s more.

**Fix:** Pass fast_infrared through to estimate_seconds in scan() and scan_roll, and spend a metering interval per frame when meter asks for it, using the driver's or session's constants rather than a retyped 48 s.

<a id="demo-parity-dp-20"></a>

### DP-20 -- Paths the demo can never reach: counter silent, wrong-way direction, error frames; plus one frame per roll forced to miss

**Severity** info · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:337-338`, `rps7200/demo.py:253`, `rps7200/demo.py:384-389`, `rps7200/demo.py:427`, `rps7200/session.py:204-219`, `rps7200/direct.py:2912-2929`

Several of the real software's paths are unreachable with no device on the bus:
- the seek's 'transport would not say' refusal;
- the hold loop's wrong_way/roll_abort path, because the demo's direction sense is fixed and always agrees;
- the roll's error frames.
The demo also injects a deterministic not_converged on strip frame 3 of every roll, which is intended but is a failure the operator did not cause. By CLAUDE.md's own rule these paths are untested with no device.

**Evidence (from the code):**

```text
`def position(self) -> int | None: return self._position` never returns None. `self._slipping_index = 2`: `if self._index == self._slipping_index: delivered = 0.0`. The film sense is fixed by `np.roll(image, pixels, axis=1)`.
```

**Failure scenario:** A change that breaks the wrong_way abort or the silent-counter refusal passes every demo exercise (make run-demo / make run-sheet).

**Fix:** Add opt-in demo knobs (e.g. DemoScanner(faults={...})) to exercise a silent counter, an inverted sense and a failing frame, and keep the default demo free of injected failures, or say in the window that frame 3 is a deliberate slip.

<details><summary>Second reader's check</summary>

position() always returns an int (demo.py:337-338). _slipping_index = 2 makes the third frame of every roll deliver 0 (253, 384-389). The film sense is fixed by np.roll (427), so wrong_way/roll_abort and the counter-silent refusal cannot be reached in the demo, and there is no per-frame error path (see DP-09).

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Demo-filed scan pixels | demo/library/<YYYYMMDDTHHMMSSZ>_<stock>_[f<frame>_]<dpi>dpi[_ir][-N]/scan.tif (or <--library>/... if given) | TIFF, uint16 (scans) or uint8/uint16 (prescans, test cards), HxWxC | Recorded as raw (corrections_applied []), but actually the demo's returned image: shading-corrected by _decode, possibly resampled (_resample), channel-sliced, np.roll-shifted (prescans after nudges), or a synthetic test card | FrameWriter._write -> library.save (rps7200/session.py:1085-1100), fed by ScanSession._file from DemoScanner.prescan/scan/scan_roll | library.corrected/load (GUI _load_full tools/gui.py:4110, _deliver_one :3864), library.reconstruct/verify, DemoScanner._decode fallback and picture_signature when --demo-source points at it | No: not the raw decode of raw.bin.gz; library.corrected() shades it again |
| Demo-filed raw bytes | demo/library/<id>/raw.bin.gz | gzip of index-format lines (2-byte tag + 16/8-bit little-endian samples), layout in scan.json raw.layout; sha256 recorded | Raw: a byte copy of the SOURCE library entry's raw.bin.gz, line-reversed by reverse_lines when the modelled carriage was far | library.save from DemoScanner.capture_record() (demo.py:435-442) after ScanSession._file's shape guard (session.py:2072-2099) | library.read_raw/reconstruct/decode_raw/verify; DemoScanner._decode | Byte-exact copy, but may be STALE (a previous pass's bytes, when the prescan came from prescan.tif and shapes coincide) and does not decode to scan.tif (which is corrected) |
| Demo-filed shading reference and CCD mask | demo/library/<id>/shading.npz, demo/library/<id>/ccd_mask.bin | npz (ref<c>/mean<c>/dark<c>/darkmean<c> float64 arrays, pixels_per_line, channels); mask 1 byte per calibration column (0x00 used) | Calibration data of the source entry, or of whatever entry was last decoded | library.save from demo capture; kept by _drop_raw and by the session guard even when raw is dropped | library.corrected/load/reconstruct; DemoScanner._decode when the demo reads its own output | Lossless copies, but frequently describe a different pass or resolution than scan.tif (stale capture, resampled pixels) |
| Demo entry record | demo/library/<id>/scan.json | JSON: image{shape,dtype,corrections_applied,sha256}, raw{file,bytes,sha256,layout}, scan{whitelisted meta}, device_settings, metering, registration, calibration{shading,ccd_mask,report,skipped}, prescan{read_direction,carriage_state}, film, tags, provenance | Claims raw; the meta key 'demo' is dropped; protocol_revision/frame/exposure/gain/offset/fast_infrared/exposure_metered/registration null; carriage_state.modelled true; duration_s is demo wall-clock (/120); resolution_dpi may not match the pixels (prescans) | library.save (library.py:213-309) | library.entries/signature/duplicates/verify/reconstruct/corrected; tools/dpi_analysis.py (duration_s); DemoScanner (_source_for, _shape_for, _pool_for, _pictures_for) | No: several fields missing or mislabelled; INQUIRY never recorded (save ignores inquiry=) |
| Prescan stored beside a roll frame | <library>/<id>/prescan.tif | TIFF uint8 (real) / uint8 or uint16 (demo) | Corrected (rf.prescan) for real rolls; for demo, the stored prescan.tif of the source entry, possibly np.roll-shifted | library.save(prescan=rf.prescan) via FrameWriter | DemoScanner._pair_image, _pool_for, best_pair, picture_signature; library.migrate_direction | No raw bytes of its own; corrected pixels only; read_direction in record.prescan |
| Library index | demo/library/index.json | JSON list summary | derived | library.reindex after every save | humans/tools | Derived, rebuildable |
| Roll/walk manifests | demo/rolls/<name or YYYY-MM-DD>/survey.json (dry run, rewritten fresh) \| roll.json (merged with earlier) | JSON: roll, numbering, dpi, infrared, meter, film, dry_run, start_at, prescan_resolution, rotation, flipped, only, settings{...}, wanted, frames[{number,index,transport_position,registration,error,done,exposure/gain/offset,prescan}] | n/a (metadata); under demo, prescan_resolution states job value while demo prescans are always 300 dpi; exposure/gain/offset absent | ScanSession._roll (session.py:1669-1926), rewritten after every frame | tools/gui.py read_survey/open_roll, session.renumbered/legacy_shift, tools/scan_roll.py --approved | Written non-atomically (write_text) per frame; the demo-mislabelled resolution is persisted |
| Walk prescans in the roll folder | demo/rolls/<name>/prescanNN.tif (and prescanNN-before.tif, never produced by demo) | TIFF, oriented by session rotation/flip | Delivered image (corrected on hardware; demo's returned pixels), rotated/flipped | FrameWriter._write paths (session.py:1817-1833), overwriting an existing file (no _unclaimed) | read_survey/walked_prescans (un-oriented by the manifest's rotation/flip), contact sheet, EdgeWatch | Overwrites silently; orientation baked in |
| Approved positions | <session.rolls>/<_safe(roll name field) or 'roll'>/approved.json | JSON {roll, numbering:'strip', frames[{number, offset_mm (4 dp), rotation, flipped, reference_entry, source}]} | Operator/ensemble decisions (not derivable) | ScannerGui._write_approved (tools/gui.py:2929-2967) from on_scan_chosen | read_approved via read_survey(folder) from the roll's own folder (tools/gui.py:4727) | Values exact to 4 dp, but written to a folder that differs from the roll's folder for blank or unsafe names, and overwritten by any later roll with the same sanitised name |
| Delivered frame files | demo/rolls/<name>/frameNN.tif; <--out>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg; <--out>/prescans/... | TIFF/JPEG via export.write, oriented, optionally mono | Corrected (delivered) | FrameWriter._write | operator / NegPy | Lossy for JPEG; --out is not pinned under demo/ |
| Demo picture-signature cache | demo/pictures.npz | npz: paths (unicode array of posix entry paths), signatures (float32 N x 24 x 48 grey grids) | derived | DemoScanner._sign_pictures (demo.py:1090-1096) on a daemon thread, np.savez in place | DemoScanner._sign_pictures on later launches | Derived; non-atomic write; corrupt file not tolerated (EOFError/BadZipFile uncaught) |
| Demo window settings | demo/gui-settings.json (or --settings path) | JSON (geometry, presets, controls, rolls opened, sheet state) | n/a | ScannerGui._remember / _note_roll_opened | settings.load at launch | n/a |
| Source library read by the demo | <--demo-source, default ./library>/*/{scan.json,raw.bin.gz,scan.tif,prescan.tif,shading.npz,ccd_mask.bin} plus 'library *' siblings and nested entry folders (libraries_beside) | as library entries | raw scan.tif + raw.bin.gz (modern), corrected legacy scan.tif, corrected prescan.tif | real sessions/tools | DemoScanner.open/_source_for/_decode/_pair_image/_pool_for/_sign_pictures/best_pair | Read-only; the demo never writes to the source library unless --library points there |
| Shading reference cache | demo/calibration/shading.npz (session.reference) | npz | n/a | never, under the demo (DemoScanner.ensure_shading ignores path); DirectScanner.ensure_shading writes it on a real calibration | DirectScanner.ensure_shading(reuse); GUI prompt shows its age | Demo never creates it, so 'Use the cached one' never appears in the demo |

**Second reader's corrections to this table:**

1) Shading reference cache (demo/calibration/shading.npz): the claim that "'Use the cached one' never appears in the demo" is true only of the calibrate prompt (gui.py:2020-2022). The main panel's "reuse the cached reference" radio (gui.py:1408-1412) is always offered. Choosing it in the demo returns action 'loaded' with no file present (demo.py:446-453) and writes nothing, whereas DirectScanner calibrates and writes the file when it is missing (direct.py:598-629).
2) Approved positions: the roll name field is NOT a remembered setting (REMEMBERED_FILM = stock, process, tags; gui.py:194), so it starts blank on every launch. The common path is <session.rolls>/roll/approved.json, shared by every blank-named roll, while the roll itself goes to rolls/<YYYY-MM-DD>, which is also not _safe()-sanitised (session.py:1634-1635).
3) Demo entry record: exposure_scale IS recorded, because the demo meta carries it. For demo roll frames, registration is null because the demo meta lacks the 'registration' key the driver adds (direct.py:3662-3664), not because none was measured. For a test-card prescan, scan.depth says 8 while image.dtype is uint16.
4) Walk prescans in the roll folder: for dry-run demo walks, each prescanNN.tif is also filed as a library entry that can carry a stale reference, mask and possibly raw bytes from an earlier pass (DP-03). prescanNN-before.tif is unreachable in the demo because DemoScanner.scan_roll never sets RollFrame.prescan_before.
5) Demo-filed scan pixels: the same corrected-as-raw condition applies to real (non-demo) single GUI Scans in <library>/<id>/scan.tif, because of ScanSession._scan (DP-01). It is not demo-specific for single scans.
6) scan.json provenance: `inquiry` is accepted by library.save and never written for real entries either (library.py:142), so no entry, real or demo, records the device identity.

## What the operator can do

- Run `make run-demo` (tools/gui.py --demo) with no scanner; every job (Calibrate, Prescan, Scan, Roll/walk, Move, sheet 'Scan chosen frames') runs through the real ScanSession against DemoScanner, and output goes to demo/library, demo/rolls, demo/gui-settings.json when no path flags are given.
- Choose the picture source with --demo-source <library dir>; later rolls also draw from 'library *' siblings and their nested entry folders.
- Run `make run-sheet` (--demo --look-only --open-roll rolls/aligned-strip) to open a stored walk read-only; motion and scan jobs fail with the demo's 'no film in the transport' UsbError through the failed-job path.
- Press Stop (cooperative) or force-abort in the demo; force_abort closes the _FakeTransport and the next _work step raises UsbError, with no hardware at risk.
- Set sheet positions and commission in the demo to exercise on_scan_chosen, approved.json writing, session.seek and DirectScanner._hold_to_approved/_aim_frame against the modelled film.

## What the operator should not do

- Pass --library library or --rolls rolls (or any real path) together with --demo: demo entries and manifests go there, unmarked, and a demo walk or commission can overwrite a real walk's survey.json, prescanNN.tif and approved.json.
- Use --look-only without --demo: the real scanner is driven while the window says scanning will refuse for lack of film.
- Treat demo/library entries as evidence (reconstruct, timing medians, registration-margin fits): their scan.tif is corrected or resampled or shifted, and their raw bytes, reference and mask may belong to another pass.
- Point --demo-source at demo/library: the demo then draws on its own mislabelled output.
- Judge from the demo whether >3600 dpi works, whether holds improve framing, how backlash behaves, whether RGBI returns four channels, or what calibration costs; the demo diverges on each.
- Commission ('Scan chosen frames') a real walk reopened in the demo without --look-only: the demo's strip is library pictures, not that walk, so the holds correlate unrelated pictures.
- Leave the roll name blank when a sheet's decisions matter (real or demo): approved.json goes to rolls/roll/ and not beside the roll.

## Mistakes nothing guards against

- `--demo --library library`: no refusal; demo entries enter the real library with no demo marker and fail reconstruct.
- `--demo --rolls rolls` with the same roll name as a real walk, or a blank name on the day of a blank-named real walk: survey.json and prescanNN.tif are silently replaced, and approved.json is overwritten on commission.
- `--demo --out <real folder>`: demo frames are written beside real deliverables (renamed -2.. only on collision).
- `--look-only` without `--demo`: accepted; the real scanner moves, calibrates (possibly an empty transport) and scans.
- Choosing 7200 dpi (or anything >3600) in the demo: accepted and completes; on the hardware every pass raises ShadingUnavailable.
- Setting prescan dpi other than 300 in the demo: pixels stay 300 dpi but are labelled and filed at the requested dpi, which persists into survey.json.
- `--demo-entry <path>` mistyped, or on a film with raw entries: silently ignored while the log claims it is shown.
- A blank or punctuated roll name when commissioning from the sheet (real and demo): approved.json lands in a different folder from roll.json, reopening loses the positions, and blank-named rolls overwrite each other's approved.json.
- Closing the demo during the first-launch signature pass: a possibly truncated demo/pictures.npz makes every later launch's second roll repeat the first strip, with no message in the window.
- Calibrate 'off' then Scan in the demo: instant, corrected pictures; on the hardware the first pass silently calibrates for minutes.

## Dataflow notes

Entry: tools/gui.py main() (8113-8205) builds ScanSession(root=args.library or demo/library, reference=..., rolls=args.rolls or demo/rolls, out_dir=args.out) and, only when args.demo is set, replaces session._open_scanner with `lambda: DemoScanner(source, entry=..., no_film=args.look_only, libraries=libraries_beside(source), cache=demo/pictures.npz)` (8187-8199). ScanSession._run (session.py:1293-1346) calls the opener, then _listen (sets log_hook and progress_hook), DemoScanner.open() (demo.py:281-305: lists root/*/scan.json, starts the _sign_pictures daemon thread, picks best_pair) and inquiry().describe(). A FrameWriter thread files each result.

Jobs:
- Calibrate: session._calibrate calls DemoScanner.ensure_shading, which is a timed sleep; it returns a summary with no reference or path. The session marks itself calibrated.
- Prescan: session._prescan (1401-1438) calls DemoScanner.prescan (455-477). It spends _work(estimate_seconds), then _pair_image('prescan.tif', film, dpi) (1164-1200). _source_for(film) returns the roll's _frame_source, or a cached per-film entry nearest 1800 dpi with raw.bin.gz, or pair. The source's prescan.tif is read and returned as-is, with the dpi ignored and _capture untouched. Otherwise _decode(source) (955-1022) runs library.read_raw, DirectScanner._deinterleave(raw, layout), then apply_shading(image, entry's shading.npz, ccd_mask.bin), and sets _capture to {reference, mask, raw, layout}. The image is then resampled to _shape_for(dpi) and _drop_raw keeps the reference and mask. The pass continues through [..., :3], _as_positioned (np.roll by _film_mm/APERTURE_MM), and _read_as_carriage (479-528): encode_index, then DirectScanner.decode_index round trip, reverse_lines on the raw when far and the shape matches, and the carriage modelled by DirectScanner.byte14_for. last_scan_meta is set with demo:true, read_direction and carriage_state.modelled. The session reads last_scan_meta, and getattr(last_pixels_raw) returns None. It calls _deliver (preview.downscale into a Result event), then _file (2031-2141): capture_record() is taken, the layout-vs-shape guard drops raw only, and FrameWriter._write calls library.save(image, i.e. the demo's returned pixels, with the meta whitelist, reference, ccd_mask, raw, raw_layout) into <root>/<id>/.
- Scan: session._scan (1440-1465) calls DemoScanner.scan (530-597). It checks _need_film and the supports_infrared refusal, fakes metering, and runs _pair_image('scan.tif'), which decodes as above; _pixels or _test_card is the fallback. There is no _as_positioned. It builds meta and runs _read_as_carriage. Back in the session, _note_reversal, _deliver, then _file WITHOUT raw_image, so the corrected pixels are filed as scan.tif. The same happens with DirectScanner, whose raw sits unused in last_pixels_raw (direct.py:2818).
- Roll: session._roll (1608-1941) calls seek (227-319: wait_warm, position, retreat/advance on the demo's integer _position), mkdir rolls/<name or date>, then iterates DemoScanner.scan_roll (599-791). Per frame:
  - place_on_strip / roll_ends are the driver's.
  - _frame_source = strip[position % len], where the strip comes from _pool_for (root/*/prescan.tif entries of the film) or _next_strip (signature groups across libraries).
  - prescan(film), then _marks (framing.registration + frame_contrast).
  - If an approval exists: DirectScanner._hold_to_approved bound to the demo. It loops through the demo's nudge (param_for_mm, STEP_MM/OVERHEAD_MM, _film_mm plus the backlash model) and prescan. It is called without source= or should_stop.
  - Otherwise, if a walk is set: walk.observe, then DirectScanner._aim_frame.
  - scan(keep_raw=True).
  - A RollFrame is yielded with no raw_image, raw_prescan or prescan_before.
  The session delivers the frame, then files it: on a dry run the prescans go to library entries and to rolls/<name>/prescanNN.tif via FrameWriter. Otherwise the frame is filed with raw_image=None, prescan=rf.prescan and capture_record() (the frame's scan decode). The manifest record is appended and survey.json/roll.json rewritten after every frame.
- Move: session._move calls DemoScanner.advance/retreat (a _position counter capped at LAST_POSITION=37), or plan_nudges followed by DemoScanner.nudge.
- Sheet commission: ScannerGui.on_scan_chosen (tools/gui.py:2723-2864) runs _write_approved into session.rolls/_safe(name field)/approved.json, then submits Roll(only=..., approved=...). Roll output goes to session.rolls/<name or date>, which can be a different folder.

Exits: library entries (scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif, scan.json, index.json); rolls/<name>/{survey.json|roll.json, prescanNN.tif, frameNN.tif}; approved.json; --out copies; demo/pictures.npz; the GUI's Result events. The GUI's 1:1 view and Save As re-read each entry through library.corrected() (tools/gui.py:3864, 4110), which re-applies the stored reference to the already-corrected scan.tif.

Nothing in session.py or the GUI's scan path branches on demo. The divergences are all inside DemoScanner's answers (demo.py) and in what the session files from them.
