# README.md and CLAUDE.md vs the code

Area key `docs-readme-claude`. 21 findings: 1 high, 8 medium, 12 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: README.md and CLAUDE.md, checked claim by claim against the code (read-only). I checked about 170 concrete claims: every make target and its tasks.py body, the pyproject addopts/markers, the nine @pytest.mark.hardware tests and their skip behaviour, the CI matrix, every tools/scan.py, tools/scan_roll.py, tools/library.py and tools/make_comparison.py flag and subcommand named, and the env vars (RPS7200_DEBUG, RPS7200_DEBUG_ROOT, RPS7200_SETTINGS, RPS7200_NO_TIFFFILE, FRAME_EDGE_PARITY). I also checked each constant: INFRARED_FLOOR_S 212, READ_IDLE_S 120, UNTIED_INFRARED_IDLE_S 287, PROTOCOL_REVISION 6 re-exported by direct.py, COMMAND_UNITS/COMMAND_COST 1.84, MM_PER_UNIT 0.1057, MAX_CORRECTION_PARAM 87 (88.8 units), 2.84, 345.2, 344.5, 350.6, 0.80, CONFIDENCE_FLOOR 55, MAX_SHADING_COLUMNS 5172, stagger 4 lines, EXPOSURE_TARGET 0.80 with bands -0.08/+0.02, BLUE_RGBI_HEADROOM 5.2/11.0 per film, and the calibration frame (0,3431,10343,6888). Named functions and fields exist and do what the docs say: param_for_mm is a @staticmethod, session._open_scanner is the demo seam with no `if demo` above it, and decode_index, direction.py, _note_reversal, carriage_state (recorded, acted on by nothing), debug_claim, DeviceSuspect/suspect, library.save(compress=, corrections=), library.compact, library.corrected, FrameWriter, RollManifest (.bak/.unreadable/.legacy), roll_membership, extra.commands, extra.shading_origin, INCOMPLETE, calibration/<UTC>/data.bin+calibration.json, EdgeWatch, propose_centred, READ_AT_DPI, vote._member, and rps7200 never importing tools/frame_edges.


About 130 claims hold. The mismatches that matter:
- A roll frame's prescan.tif in the library is corrected pixels with no raw bytes and no label saying so, against "the library holds raw pixels".
- tools/scan.py's first Ctrl-C does not stop a single scan: the pass runs anyway and the tool exits 0, which invites the second, wedging Ctrl-C.
- The pre-run time estimate that CLAUDE.md says comes from the library medians comes from a linear fit that sits below every stated median, by up to 46%.
- DeviceSuspect does not stop scan() from sending WRITE, MODE SELECT and gain writes to a device that may still be mid-scan.
- The window compresses and exports with the device open and idle, against the "no heavy local work with the session open" rule.
- debug_claim deletes the debug spool before the claiming tool has filed, so the spool cannot serve as a backup in the full-disk case the README promises it covers.
- The debug spool lives in the system temp directory.
- The Delete dialog in the roll list says everything but approved.json can be rebuilt, which is not true of prescanNN-before.tif or of older walks.
- Smaller stale numbers and wording are listed in the low findings.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [DOC-01](#docs-readme-claude-doc-01) | high | data-integrity | confirmed | Roll frames' prescan.tif is stored corrected, without raw bytes, mask, commands or a label |
| [DOC-02](#docs-readme-claude-doc-02) | medium | hardware-safety | confirmed | tools/scan.py ignores the first Ctrl-C on a single scan; scan_roll finishes the whole frame, not 'the pass' |
| [DOC-03](#docs-readme-claude-doc-03) | medium | doc-mismatch | confirmed | The pre-run estimate said to come from the library medians is a linear fit that sits below every stated median |
| [DOC-04](#docs-readme-claude-doc-04) | medium | hardware-safety | confirmed | A DeviceSuspect session still sends WRITE, MODE SELECT and gain writes before scan() refuses |
| [DOC-05](#docs-readme-claude-doc-05) | medium | hardware-safety | partly | The window compresses and exports with the device open and idle |
| [DOC-06](#docs-readme-claude-doc-06) | medium | data-integrity | confirmed | debug_claim deletes the spooled copy before the claiming caller has filed; tools/scan.py's filing loop is unguarded |
| [DOC-07](#docs-readme-claude-doc-07) | medium | data-integrity | confirmed | The debug spool lives in the system temp directory, so 'kept after a crash' depends on the OS not clearing it |
| [DOC-08](#docs-readme-claude-doc-08) | medium | user-error | confirmed | The roll Delete dialog says everything but approved.json can be rebuilt; prescanNN-before.tif and old walks' prescans are only in the folder |
| [DOC-17](#docs-readme-claude-doc-17) | medium | data-integrity | confirmed | A pass whose decode or post-read check raises is never filed, even with debug on |
| [DOC-09](#docs-readme-claude-doc-09) | low | data-integrity | partly | Walk and correction behaviour: the archived calibration bytes are never read or verified, and have no link from entries corrected by a reused reference |
| [DOC-10](#docs-readme-claude-doc-10) | low | user-error | confirmed | The window offers 7200 dpi and does not refuse an uncorrectable roll before it starts |
| [DOC-11](#docs-readme-claude-doc-11) | low | doc-mismatch | confirmed | README places the DNG companion beside the TIFF; the code writes it only beside a JPEG |
| [DOC-12](#docs-readme-claude-doc-12) | low | doc-mismatch | confirmed | Sheet turns and positions survive a restart in gui-settings.json, not in approved.json, until frames are scanned |
| [DOC-13](#docs-readme-claude-doc-13) | low | doc-mismatch | partly | README attributes the phase-correlation confidence floor to film_bounds() |
| [DOC-14](#docs-readme-claude-doc-14) | low | doc-mismatch | confirmed | Capture and command counts contradict each other across README and CLAUDE.md |
| [DOC-15](#docs-readme-claude-doc-15) | low | doc-mismatch | confirmed | CLAUDE.md prohibits millimetres; tools/scan_roll.py still takes and prints them |
| [DOC-16](#docs-readme-claude-doc-16) | low | doc-mismatch | partly | Stale in-code claims contradict the numbers CLAUDE.md gives as current |
| [DOC-18](#docs-readme-claude-doc-18) | low | demo-divergence | partly | The demo's infrared refusal is a retyped copy, not 'the driver's own words' |
| [DOC-19](#docs-readme-claude-doc-19) | low | doc-mismatch | partly | Smaller README and CLAUDE.md inaccuracies |
| [DOC-A1](#docs-readme-claude-doc-a1) | low | data-integrity | found-by-verifier | An interrupted library.compact leaves an entry that verify reports as damaged, and nothing ever compacts a plain entry later |
| [DOC-A2](#docs-readme-claude-doc-a2) | low | demo-divergence | found-by-verifier | The demo's nudge re-implements the driver's travel formula and retypes MM_PER_UNIT for backlash |

## Findings in full

<a id="docs-readme-claude-doc-01"></a>

### DOC-01 -- Roll frames' prescan.tif is stored corrected, without raw bytes, mask, commands or a label

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2627`, `tools/scan_roll.py:752`, `rps7200/direct.py:2076`, `rps7200/library.py:269`, `rps7200/library.py:378`, `rps7200/session.py:2531`, `rps7200/demo.py:1476`

**Doc claim:** CLAUDE.md:116 "The library holds raw pixels; everything else is corrected."; CLAUDE.md:141-146 entry holds "a `prescan.tif` where there was one" and the record carries "every command the pass sent"; README.md:185-187 "Every scan is filed in library/ by default, with the raw bytes the scanner sent ... and that pass's CCD mask"; README.md:476

CLAUDE.md and README present the library as holding raw pixels plus everything needed to re-derive them, and list prescan.tif among an entry's contents. For every frame of a non-dry-run roll (window or tools/scan_roll.py), the framing prescan is kept only as an 8-bit image corrected by that day's apply_shading. Its raw bytes, its own CCD mask, its command record, shading report, exposure and started_utc are not stored anywhere unless RPS7200_DEBUG=1, and nothing in scan.json says those pixels are corrected.

**Evidence (from the code):**

```text
session.py:2627 `prescan=rf.prescan,` and scan_roll.py:752 `prescan=frame.prescan,` hand library.save the image `prescan()` returned, and prescan() is `self.scan(... shading=shading ...)` (direct.py:2076-2082), i.e. flat-fielded. library.py:269-270 `tiff.write(str(path / "prescan.tif"), prescan, compress=compress)`. The record keeps only `"prescan": {"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}` (library.py:378-382), and the rest of prescan_meta is dropped. `rf.raw_prescan` / `frame.raw_prescan` exist and are not passed. session.py:2531-2535: on a real roll the prescans "ride along with the frame instead" and are not filed on their own. demo.py:1476-1479 concedes: "a prescan kept for the operator was corrected unless someone asked otherwise".
```

**Failure scenario:** A 36-frame roll from the window with debug off. Each frame's prescan is the reference the hold loop and `_note_reversal` judged the scan against, and afterwards it exists only as a corrected 8-bit prescan.tif. A later fix to 8-bit shading (the prescan docstring records an earlier bug that drove every pixel to zero) cannot be re-applied, the prescan's read direction cannot be re-derived from its tags, and a tool that assumes library pixels are raw would correct it a second time.

**Fix:** File each frame's prescan from rf.raw_prescan/frame.raw_prescan, either as its own kind="prescan" entry with its capture record (as a walk already does) or with raw bytes, mask and full meta inside the frame entry. Until then, record `prescan.corrections_applied: ["shading"]` and keep the whole prescan_meta. Update CLAUDE.md to say what prescan.tif actually is.

<details><summary>Second reader's check</summary>

session.py:2627 passes `prescan=rf.prescan` (the image returned by prescan(), which calls scan(... shading=shading ...) at direct.py:2076-2087, so it is corrected) and never passes rf.raw_prescan to the frame's _file. On a real roll the prescan is filed separately only when `job.dry_run` (session.py:2536). tools/scan_roll.py:752 does the same thing, and it files raw prescans only in its dry_run branch (660-684). library.py:269-270 writes the corrected array as prescan.tif. The record keeps only file/read_direction/carriage_state (library.py:378-382) and has no corrections label. The claimer (session.py:2872-2874) claims only raw_image, so with RPS7200_DEBUG=1 the raw prescan does get a debug entry. With debug off, which is the default, a real roll's framing prescans exist only as corrected 8-bit pixels. demo.py:1476-1479 concedes that a kept prescan is corrected.

</details>

<a id="docs-readme-claude-doc-02"></a>

### DOC-02 -- tools/scan.py ignores the first Ctrl-C on a single scan; scan_roll finishes the whole frame, not 'the pass'

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/scan.py:249`, `tools/scan.py:270`, `tools/scan.py:301`, `tools/scan.py:350`, `rps7200/console.py:80`, `rps7200/direct.py:3913`, `rps7200/direct.py:3931`

**Doc claim:** CLAUDE.md:421-422 "Ctrl-C in `tools/scan.py` and `tools/scan_roll.py` finishes the pass in flight and stops there; a second one aborts."

The documented contract is that the first Ctrl-C finishes the pass in flight and stops. In tools/scan.py (non-bracket), a Ctrl-C during calibration or metering is recorded and then ignored: the full scan pass still runs, gets filed, and the tool exits 0 with trouble None. In tools/scan_roll.py the stop takes effect only after the current frame's prescan, metering probes and full scan, which is minutes, while the console says 'after the pass in flight'.

**Evidence (from the code):**

```text
scan.py:249-250 comment: "Ctrl-C finishes the pass in flight instead of abandoning its read ... and a bracket stops after it." interrupt.requested() is checked only in `hold()` under `if args.bracket and interrupt.requested():` (scan.py:301). Nothing is checked between `s.ensure_shading(...)` (270-274) and `image, meta = s.scan(...)` (350-359), and scan() runs its auto-exposure probes and the pass in one call. console.py:80-81 prints "stopping after the pass in flight -- interrupting a read wedges the scanner. Press Ctrl-C again to abort anyway." In DirectScanner.scan_roll, should_stop is read only at the top of a frame (direct.py:3913) and before an advance (3931), so prescan, metering and the scan of the current frame all run.
```

**Failure scenario:** `tools/scan.py --dpi 3600 --ir --auto-exposure`, and Ctrl-C during the 3-4 minute calibration. The tool says it will stop after the pass in flight. Calibration ends, then 'scanning at 3600 dpi with IR ...' starts three probes and a ~214 s pass. The operator, seeing the stop ignored, presses Ctrl-C again. KeyboardInterrupt is raised inside the bulk read, the read is abandoned, the device is marked suspect and needs a power cycle.

**Fix:** In scan.py, check interrupt.requested() after ensure_shading and before s.scan(), and pass a should_stop into scan()/auto_exposure so it is checked between probes and before the main pass. Make DeferredInterrupt's message say 'after the frame in flight' for rolls. Exit non-zero when a stop was requested. Fix the CLAUDE.md wording.

<details><summary>Second reader's check</summary>

In tools/scan.py the only `interrupt.requested()` check is inside `hold()` under `if args.bracket` (scan.py:301). A non-bracket run goes from ensure_shading (270-274) straight into s.scan(...) (350-359), which includes the auto-exposure probes. After the `with`, trouble is None and main returns 0. DirectScanner.scan_roll checks should_stop only at the top of a frame (direct.py:3913), before an advance (3931) and inside the hold/aim loops (3363). Nothing checks it between metering and `self.scan(...)` (4094-4121), so a roll finishes the frame's prescan, metering and full scan. console.py:80-81 promises 'after the pass in flight'. Severity lowered to medium: a wedge needs the operator to press Ctrl-C a second time after an explicit warning. The core defect is that the stop is silently deferred for minutes and the documentation misstates it.

</details>

<a id="docs-readme-claude-doc-03"></a>

### DOC-03 -- The pre-run estimate said to come from the library medians is a linear fit that sits below every stated median

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan.py:58`, `tools/scan.py:47`, `rps7200/session.py:1302`, `README.md:532`, `README.md:544`

**Doc claim:** CLAUDE.md:394-402 "Estimate first, from the medians ... RGB is about 22 s ... 72 s at 900, 85 s at 1800 ... `tools/scan.py` prints its own estimate from these"; CLAUDE.md:404 "Budget above the median, not at it"; README.md:532-537 vs README.md:544

CLAUDE.md tells an agent to estimate from the medians and background anything over ~8 minutes, and says tools/scan.py prints its estimate 'from these'. It does not. Every RGB estimate is below the stated median: -46% at 900 dpi, -18% at 1800, -18% at 7200. The 8-minute FOREGROUND_S line is then applied to an under-estimate, although CLAUDE.md says to 'budget above the median, not at it'. README and CLAUDE.md also disagree with each other (600 dpi: 34 s vs 32 s).

**Evidence (from the code):**

```text
scan.py:58-59 `print(f"estimated {seconds / 60:.1f} min (an estimate from the library's " f"medians; dense frames run longer)")`. The number actually comes from session.py:1302-1303 `lines = max(1.0, resolution * _LINES_PER_DPI)`, `rgb = 8.0 + 0.036 * lines`, which gives 18.3/28.7/39.0/70.0/132.0/256.0 s at 300/600/900/1800/3600/7200 dpi. CLAUDE.md gives the medians as 22/32/72/85/138/314 s. The README table gives ~85 s at 1800 and ~34 s at 600, while its own formula at README.md:544 gives 70 s and 29 s. METERING_S = 3 * 22.0 (scan.py:43), but README.md:625 says metering is 'up to 48 s'.
```

**Failure scenario:** `tools/scan.py --dpi 1800 --bracket 3` with no --reuse. The estimate is 3x70 + 210 = 420 s (7.0 min), so there is no 'run it in the background' warning. At the stated median it is 465 s. On a dense frame, with 1800 dpi RGB passes measured up to 162 s, it is about 696 s. A harness that kills a foreground command at 10 minutes then abandons the read and wedges the scanner.

**Fix:** Make estimate_seconds either use the recorded medians or add explicit headroom above them (for example the upper quartile), and change the printed wording to match. Make the README table and formula agree, and bring README and CLAUDE.md to one set of numbers.

<details><summary>Second reader's check</summary>

session.py:1302-1303 computes `lines = resolution * 6888/7200` and `rgb = 8.0 + 0.036 * lines`, which gives 18.3/39.0/70.0/256 s at 300/900/1800/7200 dpi, against CLAUDE.md:394-402's medians of 22/72/85/314 s. scan.py:58-59 labels the result 'an estimate from the library's medians'. FOREGROUND_S = 480 s is compared with this under-estimate and has no headroom, although CLAUDE.md:404 says to budget above the median. README's table (534-537: 600 dpi ~34 s, 1800 dpi ~85 s) disagrees with its own formula at 544 (29 s, 70 s) and with CLAUDE.md (32 s at 600 dpi). README:625 says metering takes 'up to 48 s', but METERING_S = 66 s (scan.py:43).

</details>

<a id="docs-readme-claude-doc-04"></a>

### DOC-04 -- A DeviceSuspect session still sends WRITE, MODE SELECT and gain writes before scan() refuses

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/direct.py:524`, `rps7200/direct.py:1523`, `rps7200/direct.py:1623`, `rps7200/direct.py:2178`, `rps7200/direct.py:3016`, `rps7200/direct.py:3029`, `rps7200/direct.py:3051`, `rps7200/direct.py:3062`, `tests/test_device_suspect.py:86`

**Doc claim:** CLAUDE.md:417-420 "sets `DirectScanner.suspect`, and from then on the session refuses everything that would drive the device (`DeviceSuspect`); status queries still go through"; README.md:418-420

After an abandoned read, the documentation says nothing more that could drive the device is sent, status queries aside. In code, every scan or prescan request on a suspect session still sends several parameter WRITEs, a vendor command, a WRITE GAIN/OFFSET and a MODE SELECT to a device that may still be mid-scan, and only then is refused at SLIDE INIT. The window does not consult `suspect`, so its Scan/Prescan/Roll buttons stay live and each press repeats this.

**Evidence (from the code):**

```text
The class comment at direct.py:524-527 says: "Set, it makes every command that would drive the device raise `DeviceSuspect`." _refuse_if_suspect is called only in slide() (1523), start_scan() (1623) and calibrate_shading() (2178). scan() has no guard at its top and sends set_exposure_time() (3016), set_highlight_shadow() (3017), set_scan_frame() (3019), cmd_17(1) (3024), set_gain_offset(...) (3029) and set_mode(...) (3051) before `self.slide(SLIDE_INIT, ...)` (3062) finally raises. auto_exposure sends set_gain_offset(base) before each probe's scan(). tests/test_device_suspect.py:86-95 only asserts _read_pass, slide and calibrate_shading.
```

**Failure scenario:** A window scan times out with the device still streaming, so suspect is set. The operator presses Scan with auto-exposure on. auto_exposure writes gain/offset, and the probe's scan() sends WRITE frame/exposure, cmd_17 and MODE SELECT into the device mid-scan, then raises DeviceSuspect. Whatever those writes do to a busy device is untested, and the only reason it was refused is that SLIDE INIT happens to be guarded.

**Fix:** Call self._refuse_if_suspect("a scan") at the top of scan(), auto_exposure(), prescan() and scan_bracket(), before any command. Have the session disable the capture controls once suspect is set. Extend test_device_suspect to assert scan() sends nothing.

<details><summary>Second reader's check</summary>

_refuse_if_suspect is called only at direct.py:1523 (slide), 1623 (start_scan) and 2178 (calibrate_shading). scan() starts at 2842 with no guard. Before the SLIDE_INIT at ~3062 raises, it sends set_exposure_time, set_highlight_shadow, set_scan_frame, cmd_17, set_gain_offset and set_mode (offsets 137-183 in the 2880+ window). scan_roll calls set_gain_offset(baseline) and auto_exposure before scan() (4104-4118). Neither rps7200/session.py nor tools/gui.py references `suspect` or DeviceSuspect, so the window keeps accepting jobs. The class comment at direct.py:524-525 ('makes every command that would drive the device raise') and README.md:417-420 ('sends nothing more that could drive the device') are contradicted.

</details>

<a id="docs-readme-claude-doc-05"></a>

### DOC-05 -- The window compresses and exports with the device open and idle

**Severity** medium · **Category** hardware-safety · **Verdict** partly

**Where:** `rps7200/session.py:1553`, `rps7200/session.py:1946`, `rps7200/session.py:1968`, `rps7200/session.py:2885`, `rps7200/session.py:1683`, `rps7200/export.py:191`, `rps7200/tiff.py:102`, `tools/gui.py:2758`, `tools/gui.py:3457`, `rps7200/direct.py:757`, `rps7200/direct.py:785`

**Doc claim:** CLAUDE.md:423-424 "Do not hold the session open through heavy local work. Gzipping a 140 MB library entry with the device open and idle preceded one wedge."; CLAUDE.md:185-198; README.md:605-609 "it files a single scan or prescan plain ... A roll's frames are the exception: ... with the device busy rather than idle"

In the window only (the CLI tools file after close), heavy local work happens with the device open and idle: the gzip and deflate of a roll's last queued frames after the roll job ends, deflate of single-scan output-folder copies, and the Export and Save all re-correction threads. The FrameWriter docstring ('busy rather than idle throughout') and CLAUDE.md's 'a single scan compresses nothing while the device is open' are therefore not true of the window. The small savez_compressed writes (archive and debug spool reference) contradict the archive_calibration docstring but carry negligible risk.

**Evidence (from the code):**

```text
The FrameWriter docstring (session.py:1553-1557) says the gzip "overlaps the next frame's scan, so the device is busy rather than idle throughout". But the window's writer lives for the whole session (session.py:1946), and after a roll's last frame the worker waits in `self._jobs.get()` with the device open while up to three queued frames are gzipped and deflated (`compress=bool(roll)`, session.py:2876). A single scan's output-folder copy goes through export.write -> tiff.write(..., compress=True default) (export.py:192, tiff.py:102), which is deflate with tifffile present, on the writer thread with the device open and idle. Export and Save all (gui.py:2758-2776, `_start_writing` 3457) re-correct whole rolls with the device open. archive_calibration's docstring (direct.py:757-758) says "Written uncompressed: the device is still open", yet it calls reference.save, which is np.savez_compressed (shading.py:91).
```

**Failure scenario:** A 3600 dpi RGBI roll finishes in the window. Its last two or three frames (~246 MB entries plus a deflated frameNN.tif each) are gzipped and deflated for tens of seconds while the scanner sits open and idle. Or the operator runs Rolls > Export on a 38-frame roll between scans: minutes of decompress, correct and deflate with the device open.

**Fix:** Write output-folder copies uncompressed (compress=False) while the device is open, and deflate or copy them after close. After a Roll job, wait for the writer queue to drain before accepting the next job, or document the tail as unmeasured. Either close the device for Export/Save all or state the exception explicitly in CLAUDE.md. Fix the archive_calibration docstring.

<details><summary>Second reader's check</summary>

This holds for the window but not for the CLI: tools/scan_roll.py calls writer.finish() only after the device block has exited (scan_roll.py:791-803), and tools/scan.py files after close. In the window, FrameWriter is created once for the session (session.py:1946) and flushed only in the finally after close (1968-1996). The tail of a roll is therefore gzipped (compress=bool(roll), 2885) with the device open and idle. Single-scan output copies go through export.write -> tiff.write with the default compress=True (export.py:191-192, tiff.py:102). Export and Save all run on threads while the session is open (gui.py:2758-2776, 3457). archive_calibration's docstring says 'Written uncompressed', but it calls ShadingReference.save -> np.savez_compressed (direct.py:785, shading.py:91). _debug_capture also savez_compresses the reference with the device open (direct.py ~980-985), which contradicts CLAUDE.md's 'A single scan compresses nothing while the device is open'. The npz writes are a few hundred kB and trivial. The roll tail, the deflated output copies and Export are the substantive part.

</details>

<a id="docs-readme-claude-doc-06"></a>

### DOC-06 -- debug_claim deletes the spooled copy before the claiming caller has filed; tools/scan.py's filing loop is unguarded

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:1000`, `rps7200/direct.py:1058`, `tools/scan.py:294`, `tools/scan.py:366`, `tools/scan_roll.py:730`, `rps7200/session.py:2872`

**Doc claim:** README.md:601-603 "A spool that could not be filed — a full disk, a crash before the session closed — is kept, with each pass's record, mask and reference beside its pixels"; CLAUDE.md:167-171 (claiming "files nothing twice")

README promises that a spool that could not be filed, for example because of a full disk, is kept. For every pass a tool claims, the spool is deleted at close() whether or not the claimer's own library.save later succeeds. In tools/scan.py, one failing save raises out of the loop, and the remaining held passes and the delivered file are lost with it. So the debug spool gives no protection in exactly the failure case it exists for.

**Evidence (from the code):**

```text
The flush does `if item.get("claimed"): ... self._log("... was filed by its caller"); stuck += self._debug_unlink(item); continue` (direct.py:~1058-1063) and runs in close(). The claimer in tools/scan.py (`s.debug_claim(raw)`, 294-296) files only after the `with DirectScanner` block exits, in a loop that is not in any try: `for held in pending: entries.append(library.save(...))` (366-375). tools/scan_roll.py (730) and ScanSession._file (2872) claim before FrameWriter has filed, and in the window the writer finishes after close().
```

**Failure scenario:** `RPS7200_DEBUG=1 tools/scan.py --dpi 3600 --bracket 5 --library /Volumes/full`. Five passes are held and claimed. close() deletes their spooled .npy/.bin files. The first library.save raises OSError (no space), the traceback ends the process, and all five passes' raw bytes are gone.

**Fix:** Release a claimed spool item only after the claimer confirms filing (for example debug_release(pixels, entry) from FrameWriter/scan.py), or unlink claimed items after the caller's filing rather than in close(). Wrap scan.py's save loop so one failure neither stops the others nor skips the delivered file, and fall back to a spool on failure.

<details><summary>Second reader's check</summary>

_debug_flush unlinks claimed items unconditionally (direct.py:1057-1061: `if item.get("claimed"): ... stuck += self._debug_unlink(item); continue`). It runs from close() (1144-1145). tools/scan.py claims in hold() (294-296) and files after the `with` in an unguarded loop (366-375), so the first library.save exception propagates and skips the remaining pending passes and the delivery. In the window, _file claims at submit (session.py:2872-2874) and scanner.close() runs before writer.finish() (1973-1979), so a FrameWriter library.save failure leaves no spool copy. The README.md:601-603 promise does not hold for claimed passes.

</details>

<a id="docs-readme-claude-doc-07"></a>

### DOC-07 -- The debug spool lives in the system temp directory, so 'kept after a crash' depends on the OS not clearing it

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:926`, `rps7200/direct.py:1135`

**Doc claim:** README.md:601-603 "A spool that could not be filed — ... a crash before the session closed — is kept ... and the log says where"; CLAUDE.md:185-187

With RPS7200_DEBUG=1 the window files metering probes, hold/aim prescans and unclaimed roll prescans only when it quits, so their raw bytes spend the whole session, often hours, in the OS temp directory. On a crash nothing durable records where the spool is (only the dying process's log), and /tmp is tmpfs or cleaned at boot on many Linux systems, while macOS purges /var/folders periodically.

**Evidence (from the code):**

```text
`self._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))` (direct.py:926-928). The flush happens only in close() (direct.py:1144-1145 `self._debug_flush()`), which for the window is at quit.
```

**Failure scenario:** A day-long window session with debug on crashes, and the machine is rebooted before anyone looks. Every hold and aim prescan and metering probe of the day was in /tmp/rps7200-debug-*, is gone, and no entry says it existed.

**Fix:** Spool under the library root (for example library/.spool/<session>) or under RPS7200_DEBUG_ROOT, and write a small manifest naming the spool, so a later run or `tools/library.py` can find and file orphaned spools.

<details><summary>Second reader's check</summary>

direct.py:926-928 uses tempfile.mkdtemp(prefix="rps7200-debug-") under the OS temp directory. The spool path is logged only inside _debug_flush (1044-1047, 1104-1115), which runs from close(). _debug_capture never logs where it spooled, and the only other reference is the attribute at direct.py:566. So in the 'crash before the session closed' case README.md:601-603 names, the log does not say where the spool is, which contradicts 'and the log says where'. The spool also sits in a location the OS may clear at reboot. The per-pass meta.json does make recovery possible if the directory is found.

</details>

<a id="docs-readme-claude-doc-08"></a>

### DOC-08 -- The roll Delete dialog says everything but approved.json can be rebuilt; prescanNN-before.tif and old walks' prescans are only in the folder

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2811`, `tools/gui.py:2826`, `rps7200/session.py:2566`, `tools/scan_roll.py:686`

**Doc claim:** README.md:340-347 "never touches library/: the frames' and the walk's entries stay there with their raw bytes ... though nothing in the window rebuilds a roll folder from them"

The README is honest that nothing rebuilds a roll folder and that pre-2026-09-24 CLI walks hold the only copies of their prescans. The window's own Delete confirmation tells the operator the opposite. What actually exists only in the folder: prescanNN-before.tif (the frame as it arrived before a correction moved it), the prescans of old CLI walks, and survey.json's per-frame contrast and registration record for walks. Uncommitted sheet positions and turns are not in approved.json either (see DOC-12).

**Evidence (from the code):**

```text
Docstring at gui.py:2811-2814: "Everything in a roll folder is re-derivable from the library **except `approved.json`** -- the frames and prescans can be rebuilt". Dialog at gui.py:2826-2831: "The library entries are NOT touched: the raw bytes stay, and the frames can be rebuilt from them. ... that is the one thing here the library cannot rebuild." But the window writes prescan_before with `file_entry=False` (session.py:2566-2583), and scan_roll.py writes `tiff.write(str(was), frame.prescan_before)` (686-694). Neither gets a library entry. Nothing rebuilds a roll folder (README.md:344-345).
```

**Failure scenario:** An operator walks a strip with correction on, then deletes the roll folder from Rolls..., reassured by the dialog. The only record of each frame's position before correction (prescanNN-before.tif, which with debug off has no raw bytes anywhere) is gone, and so is the evidence needed to judge whether the corrections helped.

**Fix:** File prescan_before as a library entry (with a capture record taken at the time of that pass), or list in the dialog the files that exist nowhere else and require explicit confirmation for them. Correct the docstring.

<details><summary>Second reader's check</summary>

gui.py:2811-2814 (docstring) and 2826-2831 (dialog) tell the operator that everything but approved.json can be rebuilt from the library. The window writes prescanNN-before.tif with file_entry=False (session.py:2567-2583), and scan_roll.py:686-694 writes it with a bare tiff.write, so neither gets an entry. The pass is only in the library under RPS7200_DEBUG=1, as an unclaimed aim prescan. Pre-2026-09-24 CLI walks' prescans exist only in the folder, which README.md:344-346 admits. The unticked sheet state is in gui-settings.json, not approved.json (see DOC-12), so the 'decided' count (gui.py:2822-2823) can also undercount.

</details>

<a id="docs-readme-claude-doc-17"></a>

### DOC-17 -- A pass whose decode or post-read check raises is never filed, even with debug on

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:1881`, `rps7200/direct.py:1904`, `rps7200/direct.py:3102`, `rps7200/direct.py:3228`

**Doc claim:** CLAUDE.md:155-157 "`DirectScanner` files every scan in the library automatically when debug mode is on"; library.py:21-26 ("The decode is not settled")

CLAUDE.md says DirectScanner files every scan when debug is on, and the library's premise is that the decode is not settled. Yet the passes current code rejects, which are exactly the ones newer code might decode, have their complete raw bytes discarded: the pass finished, the device is fine, and the bytes are in memory in last_raw.

**Evidence (from the code):**

```text
read_planes sets `self._read_complete = True` and `self.last_raw = blob` (1881-1886), then `image, direction = self.decode_index(blob, params, channels)` (1904), which can raise ScanReadError (unknown tags, wrong channel count). The post-read `raise ShadingUnavailable("this pass came back ... wider ...")` is at 3102-3112. `_debug_capture(raw_pixels, meta)` runs only on the success path (3228), and roll/session callers file nothing for a failed frame.
```

**Failure scenario:** A firmware quirk sends an unexpected line tag at 1800 dpi. decode_index raises 'no recognisable channel tags', the roll logs a failed frame, and the only bytes that could show what the scanner sent are dropped.

**Fix:** On any exception after _read_complete, spool last_raw and its layout together with the error (debug or not) and file them as an entry tagged undecodable.

<details><summary>Second reader's check</summary>

read_planes sets _read_complete and last_raw (direct.py:1881-1896) and then calls decode_index (1904), which can raise ScanReadError or ValueError (direct.py:1955, 1960 and a ValueError in the same function). The late ShadingUnavailable is raised at 3102-3112. _debug_capture runs only on the success path (3222). scan_roll's except yields a failed RollFrame with no raw bytes (4142-4155). Because _read_complete is True, the device is not marked suspect, so the bytes are cleanly held in memory and then dropped. CLAUDE.md:155-157 says debug mode files every scan.

</details>

<a id="docs-readme-claude-doc-09"></a>

### DOC-09 -- Walk and correction behaviour: the archived calibration bytes are never read or verified, and have no link from entries corrected by a reused reference

**Severity** low · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/direct.py:724`, `rps7200/direct.py:856`, `rps7200/direct.py:781`, `rps7200/library.py:1068`

**Doc claim:** CLAUDE.md:149-151 "Each calibration's own bytes are kept too, because `shading.npz` is a reduction of them: `data.bin` and `calibration.json` in `calibration/<UTC time>/`"

The calibration archive (data.bin, calibration.json) is never read, re-derived from or verified by any code. `make verify` does not cover calibration/. An entry corrected by a reference loaded from the cache (--reuse or the window's cached option) records only the cache path and mtime, with no archive folder or reference checksum. Tracing it back needs a content comparison against each archive's shading.npz. Sessions that calibrate do record extra.shading_origin.archive.

**Evidence (from the code):**

```text
archive_calibration writes data.bin and calibration.json (direct.py:781, 802, the latter with write_text and not atomic). A grep finds no reader of data.bin or calibration.json anywhere in rps7200/ or tools/. load_shading's origin is `{"action": "loaded", "path": ..., "file_modified_utc": ..., "loaded_utc": ...}` (direct.py:724-729) with no `archive` key and no checksum of the loaded reference. library.verify only walks the library root.
```

**Failure scenario:** Months later someone wants to recompute the reference for a batch scanned with --reuse. extra.shading_origin names only calibration/shading.npz and its mtime, the cache has since been overwritten, and which calibration/<UTC>/ folder produced it has to be guessed.

**Fix:** Record the archive folder (or the reference's sha256) inside the cached shading.npz and carry it into shading_origin on load. Add archive verification to library verify, and a tool that rebuilds a reference from data.bin.

<details><summary>Second reader's check</summary>

It is true that nothing reads data.bin or calibration.json (a grep finds only the writes at direct.py:781 and 802), that library.verify walks only the library root (library.py:1068-1140), and that load_shading's origin has no archive key (direct.py:724-729). However, a session that calibrates does record the link: ensure_shading sets `self._shading_origin["archive"] = str(archive)` (direct.py:854-860), so entries corrected in that session name their archive. The gap is confined to references loaded from the cache (--reuse, 'Use the cached one'). Those can still be matched by content, not only by timestamps, because each archive folder holds its own shading.npz and every entry holds a copy of the reference.

</details>

<a id="docs-readme-claude-doc-10"></a>

### DOC-10 -- The window offers 7200 dpi and does not refuse an uncorrectable roll before it starts

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:127`, `tools/gui.py:1787`, `rps7200/direct.py:4062`, `rps7200/direct.py:4142`, `tools/scan_roll.py:332`

**Doc claim:** README.md:554-557 "`scan()` refuses a 7200 dpi pass with shading on rather than shipping it uncorrected"; README.md:658-660 (the up-front refusal is claimed only for tools/scan_roll.py)

README says 7200 dpi cannot be corrected and that scan() refuses it. In the window a single Scan is refused at once, but a Roll at 7200 dpi seeks, prescans, meters, fails, advances, and repeats until max_failures.

**Evidence (from the code):**

```text
`DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)` (gui.py:127), and `self._int(self.v_dpi, "Scan dpi", 25, 7200)` (1787). Neither tools/gui.py nor rps7200/session.py calls correctable_at. In scan_roll, each frame is prescanned and metered (direct.py:4062-4069) before scan() raises ShadingUnavailable, which is caught as a frame failure (4142-4165). Only tools/scan_roll.py pre-refuses (332-338).
```

**Failure scenario:** The operator picks 7200 from the resolution box and presses Roll. Three frames each spend a ~16 s prescan and 2-3 metering probes, the film advances twice, and the roll gives up with nothing scanned.

**Fix:** Refuse in the window when a Roll or Scan is submitted, using DirectScanner.correctable_at, as the CLI does. Do the same for --prescan-dpi in tools/scan_roll.py.

<details><summary>Second reader's check</summary>

gui.py:127 offers 7200 in DPI_LADDER. correctable_at is used only by direct.scan (2943) and tools/scan_roll.py:333, and not by gui.py or session.py. In scan_roll the frame is prescanned (3968), metered (4104-4108) and then refused in scan() (2943-2947, before any command). The ShadingUnavailable is caught as a frame failure (4142-4155) and the loop advances until max_failures (default 3, session.py:1469). This is a wasted-time issue, not a data or safety one.

</details>

<a id="docs-readme-claude-doc-11"></a>

### DOC-11 -- README places the DNG companion beside the TIFF; the code writes it only beside a JPEG

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/export.py:21`, `rps7200/export.py:183`, `rps7200/export.py:192`

**Doc claim:** README.md:574-575 "A DNG companion can be written beside the TIFF with the same stem; it is uncompressed"

No code path writes a DNG beside a TIFF delivery.

**Evidence (from the code):**

```text
export.py:183-192: for `fmt == "jpeg"` it calls `_write_infrared(path, image, resolution)`, which writes `<stem>.dng`. For TIFF it only calls `tiff.write(str(path), image, resolution=resolution)`. The module doc (export.py:21-28): "Infrared leaves in a DNG beside the JPEG ... A TIFF delivery needs none of this".
```

**Failure scenario:** A user who expects a .dng next to each TIFF, for example to feed a DNG-only tool, finds none.

**Fix:** Change the README to say the DNG is written beside a JPEG of a four-channel pass, carrying the infrared plane.

<details><summary>Second reader's check</summary>

export.write calls _write_infrared only on the JPEG path (export.py:183-191). The TIFF path is `tiff.write(...)` alone (192). dng.py and export.py are the only modules that mention DNG. README.md:574-575 says the DNG is written 'beside the TIFF'.

</details>

<a id="docs-readme-claude-doc-12"></a>

### DOC-12 -- Sheet turns and positions survive a restart in gui-settings.json, not in approved.json, until frames are scanned

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:2666`, `tools/gui.py:718`, `tools/gui.py:3166`

**Doc claim:** README.md:427-428 "Turns survive closing the window, in `approved.json`."

README says turns survive closing the window in approved.json. Before 'scan chosen' is pressed they live only in gui-settings.json under the sheet key. That file is per machine and per settings path (demo/ vs real), and is not part of the roll folder that Duplicate or Delete handle.

**Evidence (from the code):**

```text
`_store_sheet_state` puts the sheet state into `self.remembered["sheet"][key]` and calls `_remember()` (gui.py:2666-2676), which writes it into gui-settings.json (718). approved.json is written only by `_write_approved`, called from on_scan_chosen (3166).
```

**Failure scenario:** The operator arranges and turns a sheet, quits, and copies the roll folder to another machine. The turns are not there. Or they run with a different --settings path and the sheet opens unarranged.

**Fix:** Either write approved.json (or a sheet.json) in the roll folder whenever the sheet changes, or correct the README.

<details><summary>Second reader's check</summary>

_store_sheet_state writes into self.remembered['sheet'][key] and calls _remember() (gui.py:2666-2676). _recall_sheet_state reads it back from the settings file (2684-2690). approved.json is written only by _write_approved from on_scan_chosen (3166). README.md:427-428 attributes this persistence to approved.json.

</details>

<a id="docs-readme-claude-doc-13"></a>

### DOC-13 -- README attributes the phase-correlation confidence floor to film_bounds()

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `README.md:688`, `rps7200/framing.py:86`, `rps7200/framing.py:883`

**Doc claim:** README.md:688-691 "`film_bounds()` finds the frame in the aperture ... Across 3850 pairs no wrong match ever beat a confidence of 55, and the weakest right match scored 54.9"

README.md:688-691 credits film_bounds() with the phase-correlation confidence floor, which belongs to the registration correlation (CONFIDENCE_FLOOR, framing.py:883). It also describes film_bounds as looking for 'the bright clear gap', while the code finds the film by how much light it stops. The 55/54.9 figures are numerically consistent with the code comment.

**Evidence (from the code):**

```text
film_bounds (framing.py:86-106) finds the film by a step in level and has no confidence at all. CONFIDENCE_FLOOR = 55.0 (framing.py:883) belongs to the registration correlation: "3754 pairs that are not the same picture reach **30.2** at worst; 96 that are start at **54.9**" (862-865).
```

**Failure scenario:** Someone tuning film_bounds looks for a confidence threshold that does not exist, or loosens CONFIDENCE_FLOOR believing it guards only the metering crop.

**Fix:** Attribute the 3850-pair measurement to register/measure_shift_mm, and quote 30.2 as the worst wrong match.

<details><summary>Second reader's check</summary>

film_bounds (framing.py:86-106) is a level-step detector with no confidence. The 3850-pair measurement and CONFIDENCE_FLOOR=55 belong to register/measure_shift_mm (framing.py:858-883), so README.md:688-691 attributes them to the wrong function. The claim that 'no wrong match ever beat a confidence of 55' is not wrong, though: the worst wrong match was 30.2, which is below 55. That part is only imprecise, since it hides a 24.8-point margin.

</details>

<a id="docs-readme-claude-doc-14"></a>

### DOC-14 -- Capture and command counts contradict each other across README and CLAUDE.md

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:683`, `README.md:725`, `CLAUDE.md:438`, `CLAUDE.md:565`

**Doc claim:** README.md:683, README.md:725, CLAUDE.md:438, CLAUDE.md:569

The captures are gitignored and cannot be checked from code, and the two files disagree on how many captures and commands back the SET_SCAN_HEAD and media-flag claims.

**Evidence (from the code):**

```text
README.md:683 "all nine were taken with film in"; README.md:725 "8,133 vendor SCSI commands across nine captures"; CLAUDE.md:438 "3,955 commands across all six captures"; CLAUDE.md:565-570 "across all six ... 3,987 commands parsed, zero `SET_SCAN_HEAD`".
```

**Failure scenario:** A reader cannot tell which corpus a 'zero in N commands' claim covers, or whether the three extra captures were checked for SET_SCAN_HEAD at all.

**Fix:** Re-run tools/parse_capture.py over the full set and state one count, with the number of captures, in both files.

<details><summary>Second reader's check</summary>

README.md:683 and 725 say nine captures and 8,133 commands. CLAUDE.md:438 says '3,955 commands across all six captures' and CLAUDE.md:569 says '3,987 commands parsed'. CLAUDE.md therefore disagrees with itself as well as with README. The captures are gitignored (.gitignore:29, 37), so none of these numbers can be checked from the repository.

</details>

<a id="docs-readme-claude-doc-15"></a>

### DOC-15 -- CLAUDE.md prohibits millimetres; tools/scan_roll.py still takes and prints them

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan_roll.py:117`, `tools/scan_roll.py:534`, `tools/scan_roll.py:701`

**Doc claim:** CLAUDE.md:284-286 "**Millimetres are prohibited.** ... express every sub-frame distance in units of the adjustment parameter"

CLAUDE.md states the ban as a rule, not a preference. The CLI roll tool's input and log lines are still in millimetres. README concedes this (README.md:670-671); CLAUDE.md does not.

**Evidence (from the code):**

```text
`--nudge` help: "move the film this many mm" (117-118). `print(f"offset the film by {sent:+.3f} mm in {len(steps)} command(s) ...")` (534). `said = ... f"offset {offset:+.2f} mm"` and `SHORT BY {short:.2f} mm -- the film has drifted` (701-705).
```

**Failure scenario:** An operator reading a scan_roll.py log compares 'offset +0.42 mm' with the window's '+4.0 units', which are different scales, exactly the conversion hazard the rule exists to remove.

**Fix:** Take --nudge in units and print through protocol.say_units, or record the exception in CLAUDE.md.

<details><summary>Second reader's check</summary>

scan_roll.py:117-118 has `--nudge` taking mm. Line 534 prints `offset the film by {sent:+.3f} mm`, and lines 701-705 print `offset {offset:+.2f} mm` and `SHORT BY {short:.2f} mm`. README.md:670-671 admits this, while CLAUDE.md:284-286 states the ban without the exception.

</details>

<a id="docs-readme-claude-doc-16"></a>

### DOC-16 -- Stale in-code claims contradict the numbers CLAUDE.md gives as current

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `rps7200/protocol.py:289`, `rps7200/session.py:2229`, `tools/scan_roll.py:518`, `rps7200/direct.py:2974`, `rps7200/direct.py:3011`

**Doc claim:** CLAUDE.md:303-306 (param 1 = 2.84, param 87 = 88.8 units), CLAUDE.md:325-326 (byte 8 is the flag), CLAUDE.md:456-458

Stale comments and log text: protocol.py:289 (param 1 = 2.57, actually 2.84), session.py:2229 and scan_roll.py:518-519 (a 1.01 mm single-command cap, actually param 87, 88.8 units), and direct.py:2974 (the 212 s IR-probe rationale that CLAUDE.md says has gone). The no-film note at direct.py:3011-3013 prints byte 6 (`state.scanning`) while the decision uses byte 8 (`media_loaded`).

**Evidence (from the code):**

```text
protocol.py:290 "so `param 1` travels 2.57" (the code computes 1 + 1.84 = 2.84). session.py:2229 "One SLIDE command tops out at ~1.01 mm" and scan_roll.py:518-519 "one command reaches only 1.0118 mm and `param_for_mm` clamps there silently" (the cap is now param 87, about 9.4 mm). direct.py:518-519 UNTIED_INFRARED_IDLE_S is "the floor plus the 227 s top ... and a minute" but the value is 227+60. direct.py:2974-2975 "an infrared probe costs its own ~212 s floor per round", which CLAUDE.md:456-458 says 'has mostly gone'. direct.py:3004-3013 logs `f"note: state {state.scanning:#04x} suggests no film"`, byte 6, although the test is byte 8 via media_loaded.
```

**Failure scenario:** A maintainer trusts the 1.01 mm comment and re-adds chunking or a cap, or reads the no-film note's byte-6 value as the deciding evidence.

**Fix:** Update the comments and the log line: print byte 8 and say it is byte 8.

<details><summary>Second reader's check</summary>

Confirmed: protocol.py:289 says 'param 1 travels 2.57' while the code computes 1 + COMMAND_UNITS (1.84) = 2.84. session.py:2229 ('~1.01 mm') and scan_roll.py:518-519 ('1.0118 mm') are stale against MAX_CORRECTION_PARAM = 87 (direct.py:3596). direct.py:2974-2975 still gives the 212 s probe cost as the reason. direct.py:3011-3013 prints `state.scanning` (byte 6, direct.py:1229) as the evidence, while the test is `media_loaded` (byte 8, protocol.py:568). Refuted: the UNTIED_INFRARED_IDLE_S comment (direct.py:518-521). Its value 227+60 matches 'the 227 s top ... and a minute', and read_idle_s (641-646) uses it as documented.

</details>

<a id="docs-readme-claude-doc-18"></a>

### DOC-18 -- The demo's infrared refusal is a retyped copy, not 'the driver's own words'

**Severity** low · **Category** demo-divergence · **Verdict** partly

**Where:** `rps7200/demo.py:665`, `rps7200/direct.py:2921`, `README.md:247`

**Doc claim:** README.md:247-249 "refuses what the device refuses, in the driver's own words: infrared on black and white or Kodachrome"; CLAUDE.md:236-238

DemoScanner's infrared refusal uses the shared supports_infrared() gate, but its message is a retyped copy that has already drifted from DirectScanner.scan's (it omits the chromogenic C-41 sentence). That contradicts README's 'in the driver's own words'. The uncalibrated and uncorrectable refusals are shared static methods; this one is not.

**Evidence (from the code):**

```text
demo.py:665-674 builds its own ValueError ending "...hand back the picture rather than the dust. Scan it RGB.". The driver's message at direct.py:2921-2928 continues "(Chromogenic C-41 black and white does clean properly -- scan that as a negative.)". There are four separate copies of this refusal: direct.scan, direct.scan_roll, tools/scan.py and tools/scan_roll.py.
```

**Failure scenario:** The demo shows a different refusal text from the scanner, and a later change to the driver's gate (for example allowing chromogenic B&W) is not reflected in the demo.

**Fix:** Add DirectScanner.infrared_refused(film) as a static method, as uncalibrated() is, and raise it from all four sites.

<details><summary>Second reader's check</summary>

The decision is shared: both sites gate on `supports_infrared(film)` (demo.py:665 and the driver). What is retyped is the message text (demo.py:669-674 vs direct.py:2921-2929), which has already lost the chromogenic sentence. CLAUDE.md:236-238 concerns numbers and decisions, so this is text drift rather than divergent behaviour. README.md:247-249's 'in the driver's own words' is literally false for this refusal.

</details>

<a id="docs-readme-claude-doc-19"></a>

### DOC-19 -- Smaller README and CLAUDE.md inaccuracies

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `README.md:652`, `README.md:377`, `README.md:613`, `rps7200/library.py:278`, `README.md:445`, `CLAUDE.md:16`

**Doc claim:** README.md:652, README.md:445, README.md:377, README.md:613, CLAUDE.md:16-18

Minor README and CLAUDE.md mismatches: --max-failures counts consecutive failures, not three bad frames. Restore settings also leaves the output folder alone. The code comment (72%) and README (94%) disagree on the raw gzip ratio. gui-settings.json lives in the working directory, which is 'beside the library' only while --library is left at its default. CLAUDE.md's '`--open-roll` names a different one' next to `make run-sheet` refers to a tools/gui.py flag, which the make target cannot take.

**Evidence (from the code):**

```text
(a) scan_roll.py:183-184: `--max-failures` is "consecutive failed frames" (direct.py:4163 resets on success), but README.md:652 says it "gives up after three bad frames". (b) settings.py:25 `DEFAULT_PATH = Path("gui-settings.json")`, relative to the working directory, but README.md:445 says "gui-settings.json beside the library". (c) gui.py:1252-1254: Restore settings leaves the output folder alone, but README.md:377-378 says it "puts everything back". (d) library.py:278-280 comment "gzip takes it to 72% of its size" vs README.md:613 "about 94% of the raw size". (e) CLAUDE.md:16-18 puts "`--open-roll` names a different one" beside `make run-sheet`, but tasks.run_sheet takes no arguments (tasks.py:199-201) and make rejects --open-roll.
```

**Failure scenario:** The operator runs `make run-sheet --open-roll rolls/x` and gets a make error; runs --library elsewhere and cannot find the settings; expects a strip with scattered failures to stop at three.

**Fix:** Say 'consecutive'; say 'in the working directory, or $RPS7200_SETTINGS'; mention the output folder exception; reconcile the compression ratio; give the full `uv run python tools/gui.py --demo --look-only --open-roll ...` command.

<details><summary>Second reader's check</summary>

(a) Confirmed: help at scan_roll.py:183-184 says 'consecutive', and direct.py:4121/4163 resets on success. (c) Confirmed: gui.py:1253-1255 excludes the output folder, while README.md:377-378 says 'puts everything back' and names only shortcuts and presets. (d) Confirmed: library.py:278-280 says 72%, README.md:613 says ~94%. (b) Weak: settings.DEFAULT_PATH and library.DEFAULT_ROOT are both relative to the cwd, so with defaults the file is beside the library. It is wrong only when --library is moved. (e) Partly: the `make run-sheet` target takes no argument (Makefile:66-67, tasks.run_sheet at 171). CLAUDE.md's 'names a different one' is ambiguous, and tasks.py's docstring gives the full gui.py command.

</details>

<a id="docs-readme-claude-doc-a1"></a>

### DOC-A1 -- An interrupted library.compact leaves an entry that verify reports as damaged, and nothing ever compacts a plain entry later

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:436`, `rps7200/library.py:445`, `rps7200/library.py:451`, `rps7200/session.py:1990`, `tools/library.py:75`

**Doc claim:** CLAUDE.md:188-192 "An entry the window was killed before compacting stays plain, complete and verifiable."

CLAUDE.md says an entry the window was killed before compacting 'stays plain, complete and verifiable'. If the window is killed during compact, after scan.tif has been rewritten deflated and before scan.json is replaced, the record still holds the old image.sha256, so `make verify` reports 'scan.tif does not match its checksum' although the pixels are identical. The stray raw.bin.gz beside raw.bin is not reported. No tool re-runs compact on entries a previous session left plain, so they stay uncompressed (~2x size) for good.

**Evidence (from the code):**

```text
compact: `os.replace(temp, path / RAW_FILE)` (436), then `_replace_tiff(path / name, pixels, ...)` for scan.tif/prescan.tif with the new digest held only in memory (443-449), then `_write_atomic(path / "scan.json", ...)` (451) and `plain.unlink()` (452). The only caller is the session's close path, `for entry in self._writer.uncompressed: library.compact(entry)` (session.py:1990-1992). tools/library.py's actions are `list, verify, reconstruct, reindex, duplicates, migrate-raw, migrate-direction, tag` (75-77); there is no compact.
```

**Failure scenario:** The operator quits the window after a batch of 3600 dpi single scans and force-kills it while 'filing what is still queued' is compacting. The next `make verify` flags an intact entry as a checksum mismatch, which is a false alarm in the integrity check. The entry is never compacted.

**Fix:** Write the record with the new TIFF digests before, or atomically with, each TIFF swap, or have verify accept either the plain or the compressed digest while raw.bin is still present. Add `tools/library.py compact` to finish entries a killed session left plain.

<a id="docs-readme-claude-doc-a2"></a>

### DOC-A2 -- The demo's nudge re-implements the driver's travel formula and retypes MM_PER_UNIT for backlash

**Severity** low · **Category** demo-divergence · **Verdict** found-by-verifier

**Where:** `rps7200/demo.py:433`, `rps7200/demo.py:452`, `rps7200/direct.py:3622`

**Doc claim:** CLAUDE.md:231-233 "A number, cap, constant or decision the stand-in needs is taken from DirectScanner, never retyped."

The constants are shared through class attributes (demo.py:805-813), but the asked/short/clamped arithmetic and the return shape are a second copy of DirectScanner.nudge, and the backlash term hard-codes the unit size. CLAUDE.md names this exact pattern ('nudge was retyped into demo.py') as the cause of a past divergence.

**Evidence (from the code):**

```text
demo.py:433-434 `param = self.param_for_mm(millimetres)` / `asked = self.STEP_MM * param + self.OVERHEAD_MM` duplicates direct.py:3622-3624 rather than calling it. demo.py:452 `swallowed = min(abs(asked), 2.2 * 0.1057)` retypes protocol.MM_PER_UNIT (0.1057, protocol.py:260) as a literal.
```

**Failure scenario:** MM_PER_UNIT is re-measured. The driver's travel updates, but the demo's backlash stays at 0.1057 mm per unit, and its hand-copied result dict can lose a field the driver adds (for example a new key that the hold loop reads), so the demo reports hold outcomes that the hardware would not.

**Fix:** Factor the travel computation into a DirectScanner static/class method used by both, and replace the literal with MM_PER_UNIT.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry decode | library/<YYYYMMDDTHHMMSSZ>_<stock>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF, uint16 (or uint8 for prescans), RGB/RGBI; deflate+predictor when tifffile is present and compress=True, uncompressed from the window before compact | raw decode (upright rows; plus the 4-line stagger trim at 7200 dpi, recorded in scan.stagger_realigned); corrected only for legacy or corrections_applied entries | library.save (library.py:267) via tools/scan.py, FrameWriter._write (session.py:1656), DirectScanner._debug_flush; rewritten by library.compact, tools/library.py migrate-raw, library.migrate_direction | library.load/corrected/reconstruct/verify, demo.DemoScanner, gui Save As/Export, make_comparison | lossless for the decoded pixels; reproducible from raw bytes via reconstruct |
| Raw bytes as received (bulk image READs concatenated, index format with 2-byte tag per line) | library/<id>/raw.bin.gz (or raw.bin before compact) | gzip level 6 of the byte stream; sha256 of the uncompressed bytes in scan.json raw.sha256, layout in raw.layout | raw | library.save (library.py:277-304), library.compact (414) | library.read_raw/decode_raw/reconstruct/verify, migrate-raw, migrate_direction, tools/uniformity.py, demo | yes, byte-exact; missing for a roll frame's prescan, for prescanNN-before, and for any pass whose decode raised |
| Shading reference in force for the pass | library/<id>/shading.npz | np.savez_compressed: ref<c>, mean<c>, dark<c>, darkmean<c>, pixels_per_line, channels | reference (a reduction of calibration data) | ShadingReference.save via library.save | library.load/corrected/reconstruct, migrate-raw | yes, arrays are lossless; the provenance of a loaded reference is only path and mtime (extra.shading_origin) |
| Per-pass CCD mask | library/<id>/ccd_mask.bin | raw bytes, length = the reference's pixels_per_line (normally 5172) | raw | library.save | library.load/corrected/reconstruct | yes |
| Framing prescan riding with a roll frame | library/<id>/prescan.tif | TIFF uint8 RGB | CORRECTED (flat-fielded by that day's code), with no label in the record | library.save(prescan=rf.prescan) from session.py:2627 and tools/scan_roll.py:752 | demo (as a fallback picture), library.migrate_direction | no: no raw bytes, mask, commands or meta beyond read_direction/carriage_state |
| Entry record | library/<id>/scan.json | JSON: image{shape,dtype,sha256,corrections_applied}, raw{file,bytes,sha256,layout}, scan{SCAN_FIELDS}, extra{commands,shading_origin,mode,started_utc,roll_membership,...}, device, device_settings, metering, registration, calibration{report,skipped,light_mean rounded to 0.1}, prescan{read_direction,carriage_state}, film, tags, provenance, files{sha256} | metadata | library.save via _write_atomic (fsync + os.replace); add_tags, compact, migrate-* | library.entries/load/corrected/verify/duplicates, gui roll joins, demo | atomic; light_mean is rounded but the npz holds exact values |
| In-progress marker | library/<id>/INCOMPLETE | text | n/a | library.save (first), removed after scan.json | library.verify | n/a |
| Library index | library/index.json | JSON summary | derived | library.reindex (plain write_text, not atomic) | humans; derivable | derived, rebuildable |
| Kept pre-migration image | library/<id>/scan.before-migrate-raw.tif | TIFF | corrected legacy pixels | tools/library.py migrate-raw --write | nothing (named in image.replaced) | not checksummed in record.files |
| Cached shading reference | calibration/shading.npz (demo/calibration/shading.npz under --demo) | np.savez_compressed | reference | DirectScanner.save_shading (via .part and os.replace) | load_shading (--reuse, the window's 'Use the cached one') | yes; no link to the archive it came from |
| Calibration archive | calibration/<YYYYMMDDTHHMMSSZ>[-N]/{data.bin,calibration.json,shading.npz,ccd_mask.bin} | data.bin raw calibration lines; calibration.json with width, stride, commands, sha256 | raw | DirectScanner.archive_calibration (direct.py:749), only from ensure_shading | nothing in code; not verified by make verify | yes (calibration.json written non-atomically) |
| Roll and walk manifests | rolls/<roll>/{survey.json,roll.json,approved.json}[.bak\|.unreadable[-N]\|.legacy] | JSON | metadata (registration, done, entry, filing_error, rotation, flipped, as_walked) | session.write_manifest/RollManifest (atomic); approved.json only by gui on_scan_chosen | session.read_manifest/walked_prescans, gui Rolls table and contact sheet, scan_roll.py --approved and resume | atomic rename; the .bak is kept once per run |
| Roll delivered files | rolls/<roll>/{frameNN.tif,prescanNN.tif,prescanNN-before.tif} | TIFF (deflate with tifffile), oriented, mono for B&W frames | corrected, by that day's code | FrameWriter/export.write; scan_roll.py tiff.write for prescans | gui contact sheet, walked_prescans, frame_edges, scan_roll.py --approved | no (corrected); prescanNN-before.tif has no library entry |
| Window settings and uncommitted sheet state | gui-settings.json in cwd (or $RPS7200_SETTINGS, --settings, demo/gui-settings.json), gui-settings.json.unreadable-<time> | JSON: controls, film, output, window, presets, shortcuts, rolls, sheet | n/a | settings.save (.part then replace) | ScannerGui at start | atomic |
| Debug spool | $TMPDIR/rps7200-debug-*/NNN-{image.npy,raw.bin,meta.json,shading.npz,ccd_mask.bin} | npy raw pixels, raw bytes, JSON meta | raw | DirectScanner._debug_capture (direct.py:907) | DirectScanner._debug_flush at close() | yes, but in OS temp; claimed items are deleted at close before the claimer files |
| tools/scan.py delivered file and sidecar | <--out, default scan.tif>, <stem>.json | TIFF or JPEG (+ .dng for IR JPEG), JSON meta | corrected (unless --no-shading), mono for bw | export.write, write_text | user | no |
| Window output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg, <out_dir>/prescans/..., plus .dng beside an IR JPEG | TIFF (deflate) or JPEG | corrected, oriented | FrameWriter via export.write | NegPy and users | no |
| Comparison files | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif, previews/cmp_before.png, previews/cmp_after.png | TIFF, PNG | 1_ raw, 2_ corrected, 3_ inverted | tools/make_comparison.py | Stefan by eye | n/a |
| Demo tree | demo/{library,rolls,calibration,gui-settings.json,pictures.npz} | as above | as above | the window under --demo | the window under --demo | n/a |

**Second reader's corrections to this table:**

- Calibration archive: the table says there is 'no link to the archive it came from'. That is true only for references loaded from the cache. A calibrating session sets extra.shading_origin.archive (direct.py:854-860), so its entries do name their archive folder. The archive's shading.npz is np.savez_compressed (shading.py:91), even though archive_calibration's docstring says 'written uncompressed'.
- Debug spool: NNN-shading.npz is written once per reference, not once per pass (direct.py:~976-986), and it is savez_compressed while the device is open. The spool directory is logged only at flush, never at capture.
- Roll delivered files: in tools/scan_roll.py, prescanNN.tif and prescanNN-before.tif are written by a bare tiff.write (deflate by default) on the scanning thread with the device open (scan_roll.py:660, 693), not by FrameWriter. In the window, both go through FrameWriter/_file, with prescan-before at file_entry=False.
- The roll-frame prescan.tif row is correct, with one clarification: under RPS7200_DEBUG=1 the raw framing prescan is filed as a separate debug entry, because it is never claimed (session.py:2872-2874 claims only raw_image). With debug off, which is the default, no raw copy exists.
- Library entry scan.json 'files': this map covers only shading.npz, ccd_mask.bin and prescan.tif (library.py:390-394). scan.tif is checksummed in image.sha256, and the raw bytes in raw.sha256, which is computed over the uncompressed stream.
- Entries filed plain (raw.bin, uncompressed TIFF) by the window are compacted only by the same session's close (session.py:1990). There is no CLI compact, so an entry left plain by a killed session stays plain permanently.
- About 70 claims from README.md and CLAUDE.md were checked against code: make targets and tasks.py bodies, the addopts hardware skip, the nine hardware tests, the tools/library.py actions and the migrate-raw --write default, the library defaults of scan.py and scan_roll.py, debug=None env handling, debug_claim, library.compact, INCOMPLETE and verify, the constants 345.2/344.5/350.6/0.80/2.84/88.8/1.84/87, READ_IDLE_S 120, UNTIED 287, INFRARED_FLOOR_S 212, INFRARED_UNTIED_S 219.8, PROTOCOL_REVISION in protocol.py re-exported by direct.py, BLUE_RGBI_HEADROOM 5.2/11.0, param_for_mm as a staticmethod shared with the demo, session._open_scanner, carriage_state recorded and unused, _note_reversal skipping known directions, frame_edges READ_AT_DPI=(300,), EdgeWatch, vote._member, unread_at refusal of --correct, FRAME_EDGE_PARITY, the make_comparison 'applied' refusal, filing_load_test's 5% limit and 300 dpi 8-bit passes, verify_protocol stage14, the uniformity analyse --tag, and the .gitignore entries. Everything outside the verdicts above matched.

## What the operator can do

- Run `make all|test|test-all|lint|type|fix|clean|reconstruct|verify|run|run-demo|run-sheet` (all go through tasks.py; `python tasks.py <target>` also works).
- Scan once with `tools/scan.py --dpi N [--ir] [--no-fast-ir] [--auto-exposure] [--reuse] [--no-shading] [--film ...] [--bracket N --stops S] [--library DIR|--no-library]`; it prints an estimate before opening the device.
- Walk or scan a roll with `tools/scan_roll.py [--dry-run] [--roll NAME|--out DIR] [--start-at N] [--frames N] [--approved DIR] [--correct|--correct-dry-run] [--nudge MM] [--max-failures N]`.
- Inspect and maintain the library with `tools/library.py list|verify|reconstruct|reindex|duplicates [--delete --keep N]|migrate-raw [--write]|migrate-direction [--write]|tag ENTRY --add TAG`.
- Turn on debug filing with RPS7200_DEBUG=1, move it with RPS7200_DEBUG_ROOT, and move window settings with RPS7200_SETTINGS or --settings.
- In the window: calibrate (only after ticking 'The film is in the transport') or load the cached reference; prescan, scan, walk, roll; use the contact sheet; Rolls... export/duplicate/rename/delete; Save As / Save all; Stop; Force abort after typing ABORT.
- Run `tools/make_comparison.py <entry>` for the raw/corrected/inverted triple, and `tools/check_scanner.py` or `pytest -m hardware` for read-only device checks.

## What the operator should not do

- Calibrate with an empty transport; the only guard is a tick the operator sets.
- Press Ctrl-C a second time in tools/scan.py or tools/scan_roll.py: it abandons the read and needs a power cycle.
- Run a foreground scan sequence near 8-10 minutes on the strength of tools/scan.py's estimate, which is below the documented medians.
- Run Export or Save all, or leave a large roll's tail compressing, while the window holds the scanner open and idle.
- Delete a roll folder expecting everything but approved.json to be rebuildable.
- Use --no-fast-ir casually (a flat ~220 s per IR pass), or 7200 dpi with shading (refused, or wasted in a window roll).
- Send SET_SCAN_HEAD, STOP SCAN or an IEEE1284 reset from any script.

## Mistakes nothing guards against

- A single Ctrl-C during tools/scan.py's calibration or metering is ignored: the full pass runs, is filed, and the tool exits 0, which invites the wedging second Ctrl-C.
- Starting a Roll at 7200 dpi from the window (the value is in the resolution ladder) is not refused up front. Frames are prescanned and metered and the film advanced before each scan() refusal, until max_failures.
- `tools/scan_roll.py --prescan-dpi 7200` (or any uncorrectable prescan resolution) is not pre-refused: calibration is spent and every frame fails.
- After a timeout marks the device suspect, pressing Scan or Prescan again in the window still sends WRITE, MODE SELECT and gain writes to the device before it is refused.
- Deleting a roll from Rolls... destroys prescanNN-before.tif, old CLI walks' prescans and survey.json data that exist nowhere else, while the dialog says the frames can be rebuilt.
- `tools/scan.py --library <full or read-only path>`: the first library.save raises. Remaining held passes and the delivered file are lost, and with debug on their spool was already deleted at close().
- A crash followed by a reboot during a long debug-on window session loses every spooled probe and hold pass kept in the OS temp directory.
- `make run-sheet --open-roll X` fails; the flag has to go to tools/gui.py directly.
- Running a roll with debug off keeps each frame's prescan only as a corrected 8-bit TIFF, and its raw bytes are gone.

## Dataflow notes

Entry: DirectScanner.scan (direct.py:2842) refuses before sending anything when infrared is blind to the film (2911) or no reference covers the pass (2944-2965, via correctable_at/uncorrectable/uncalibrated); there is no suspect check there (DOC-04). Optional auto_exposure then runs RGB probes that are passes of their own through scan() (direct.py:2385, budget rounds+1), metering inside metering_slice (framing.py:191). _CommandLog.start (direct.py:455) records every non-bulk command. Setup writes are at 3016-3062. _read_pass (3243) -> start_scan (guarded) -> get_ccd_mask -> get_parameters -> read_planes (keeps blob in last_raw when keep_raw or debug) -> decode_index (1923; tag-based row reversal via direction.py). stop() then yields meta['commands']. The 7200 dpi stagger realignment is at 3079-3089. raw_pixels is kept; apply_shading (shading.py) gives the returned image. meta is built at 3139-3216 (commands, shading_origin, carriage_state, read_direction, stagger_realigned, mode). _debug_capture (3228) spools the raw pixels, raw bytes, mask, reference and meta to $TMPDIR; last_pixels_raw/last_scan_meta are set for callers.

Calibration: ensure_shading (806) -> calibrate_shading (2128, guarded, frame (0,3431,10343,6888)) -> archive_calibration (749, calibration/<UTC>/data.bin + calibration.json + npz + mask; nothing reads it) -> save_shading (736, atomic cache). load_shading (716) records only path and mtime.

Leaving through the CLI: tools/scan.py hold() claims last_pixels_raw and queues it; after close(), library.save (library.py:216) runs outside any try (scan.py:366), then export.write and the .json sidecar. tools/scan_roll.py: DirectScanner.scan_roll (direct.py:3767) yields RollFrame with raw_image, raw_prescan and prescan_meta. Frames go to FrameWriter.submit (session.py:1705), which files on a thread while the device is open (library.save compress=True, then export.write to rolls/<roll>/frameNN.tif). prescan=frame.prescan, which is corrected, becomes the entry's prescan.tif (DOC-01). Dry-run prescans are filed as their own entries with their raw bytes. The manifest goes through RollManifest (atomic), and writer.finish runs after close.

Leaving through the window: ScanSession._run (session.py:1927) opens once, then INQUIRY and READ_STATE, and one FrameWriter serves the whole session. _prescan/_scan/_roll call _file (2780), which claims raw_image, sets compress=bool(roll), and writes output-folder copies through export.write (deflate) on the writer thread. At quit it closes the device, then writer.finish, then library.compact of uncompressed entries (1965-1996), then DirectScanner._debug_flush from close(). Export/Save all (gui.py:2709, 4221) read library.corrected with the device still open.

Re-derivation: library.load/corrected (524/548), read_raw/decode_raw/reconstruct (604/657/687), verify (1068), prunable/same_data (974/1014), compact (414), migrate_direction (777), and tools/library.py migrate-raw (199). The demo substitutes DemoScanner at session._open_scanner (gui.py:8904) and borrows DirectScanner.scan_roll, auto_exposure, _hold_to_approved, param_for_mm, STEP_MM, OVERHEAD_MM and MAX_CORRECTION_PARAM by reference (demo.py:787-813).
