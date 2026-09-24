# Outputs: export, TIFF, DNG, preview, mono, bracket, settings

Area key `outputs`. 29 findings: 2 critical, 4 high, 8 medium, 14 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Output area: rps7200/tiff.py (hand-written TIFF reader/writer plus an optional tifffile path), export.py (delivered TIFF/JPEG plus a DNG companion for infrared), dng.py (4-sample LinearRaw writer), preview.py (display stretch, orientation, histogram and clipping), mono.py (single-plane delivery), bracket.py (inverse-variance bracket merge), settings.py (GUI JSON settings), console.py (UTF-8 stdout) and __init__.py. I also followed their callers: session.FrameWriter, tools/gui.py Save As / Save all / histogram, tools/scan.py, tools/make_comparison.py, and library.save / corrected / migrate_direction.


The pixel codecs hold up. Reads and writes are lossless on both TIFF paths, byte order is handled, predictor-2 reads wrap correctly, and JPEG is a plain >>8 shift.


The serious problems are in how these writers are used:
1. A GUI single scan files its shading-corrected pixels in the library labelled as raw. Save As and Save all then correct them a second time.
2. FrameWriter writes the delivered files before the library entry. Any delivery failure (full or unplugged output drive, a bad name, a bad rotation) therefore drops the frame's raw bytes for good.
3. The bracket merge judges saturation on shading-corrected values. With --ir it also mixes in an RGBI pass whose blue is about 5x different from the RGB passes.
4. The GUI compresses and gzips every entry while the scanner is open and idle. That contradicts CLAUDE.md's hardware rule.

Secondary: the clipping readout is measured on corrected pixels. Save As writes no dpi. tiff.write and dng.write are not atomic and are used to rewrite prescan.tif in place, a file that has no checksum and no raw backup. The JPEG-to-TIFF fallback and the DNG companion overwrite existing files silently. A corrupt settings file is dropped and overwritten silently. make_comparison does not run the production correction. Several docstring claims are stale.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [OUT-01](#outputs-out-01) | critical | data-integrity | GUI single scans file shading-corrected pixels as raw; Save As/Save all then double-correct them | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [OUT-02](#outputs-out-02) | critical | data-integrity | FrameWriter writes delivered files before the library entry, so any delivery failure loses the raw bytes | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [OUT-03](#outputs-out-03) | high | data-integrity | Debug flush deletes the spooled raw data even when library.save (tiff.write) failed | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [OUT-04](#outputs-out-04) | high | bug | Bracket merge judges saturation on shading-corrected values | [P29](../problems/P29-bracket-merge.md) |
| [OUT-05](#outputs-out-05) | high | bug | --bracket with --ir merges one RGBI pass (blue about 5x brighter) using a ratio fitted on green only | [P29](../problems/P29-bracket-merge.md) |
| [OUT-06](#outputs-out-06) | high | hardware-safety | GUI compresses TIFFs and gzips raw bytes while the scanner is open and idle | [P13](../problems/P13-gzip-with-device-open.md) |
| [OUT-07](#outputs-out-07) | medium | bug | Clipping and histogram readout counts corrected pixels, so it does not show sensor saturation | -- |
| [OUT-09](#outputs-out-09) | medium | data-integrity | Non-atomic tiff.write is used to rewrite library files in place; prescan.tif has no checksum | [P10](../problems/P10-non-atomic-writes.md) |
| [OUT-10](#outputs-out-10) | medium | doc-mismatch | make_comparison.py does not run the production correction, although it is the by-eye acceptance test | -- |
| [OUT-11](#outputs-out-11) | medium | data-integrity | A corrupt settings file is discarded silently and overwritten; save failures are silent | [P24](../problems/P24-disk-full-and-quitting.md) |
| [OUT-12](#outputs-out-12) | medium | user-error | tools/scan.py --bracket silently ignores a single-value --exposure-scale | [P29](../problems/P29-bracket-merge.md) |
| [OUT-13](#outputs-out-13) | medium | library-completeness | Bracket membership, merge parameters and metering are not recorded in the library, so a bracket cannot be re-merged offline | [P09](../problems/P09-record-missing-parameters.md) |
| [OUT-14](#outputs-out-14) | medium | demo-divergence | The demo files corrected prescans and roll frames as raw, a branch the real scanner does not take | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [OUT-15](#outputs-out-15) | medium | data-integrity | A roll frame's library prescan.tif is the corrected prescan, unlabelled | [P08](../problems/P08-passes-never-filed.md) |
| [OUT-08](#outputs-out-08) | low | bug | Save As / Save all write no dpi, and the two TIFF writers disagree on the default resolution | -- |
| [OUT-16](#outputs-out-16) | low | user-error | JPEG-to-TIFF fallback and the DNG companion overwrite existing files; _unclaimed and Save all's promise do not cover them | -- |
| [OUT-17](#outputs-out-17) | low | user-error | tools/scan.py checks the output name only after scanning, and the default --out overwrites the previous scan | -- |
| [OUT-18](#outputs-out-18) | low | design | _z_medians casts every full frame to float64 before subsampling | -- |
| [OUT-19](#outputs-out-19) | low | doc-mismatch | The bracket noise model always uses the fallback constants; fit_noise_params is never called | -- |
| [OUT-20](#outputs-out-20) | low | bug | Merged bracket output is truncated rather than rounded | -- |
| [OUT-21](#outputs-out-21) | low | doc-mismatch | The built-in TIFF/DNG writer copies the whole image into memory, defeating the mmap in debug flush | -- |
| [OUT-22](#outputs-out-22) | low | bug | The built-in TIFF writer can place the IFD at an odd offset | -- |
| [OUT-23](#outputs-out-23) | low | bug | The tifffile version floor predates the resolutionunit keyword that tiff.write uses (not verified locally) | -- |
| [OUT-24](#outputs-out-24) | low | doc-mismatch | Stale '~212 s floor' cost claims for infrared | -- |
| [OUT-25](#outputs-out-25) | low | doc-mismatch | The DNG companion lacks ColorMatrix1, which the DNG spec requires for non-monochrome files | -- |
| [OUT-26](#outputs-out-26) | low | user-error | Mono delivery drops the infrared plane without a note | -- |
| [OUT-V01](#outputs-out-v01) | low | user-error | --bracket with --no-shading merges uncorrected passes, which the merge module says must not happen | -- |
| [OUT-V02](#outputs-out-v02) | low | design | tools/scan.py keeps every bracket pass (corrected, raw pixels and raw bytes) in memory until the session closes | -- |
| [OUT-27](#outputs-out-27) | info | library-completeness | Delivery parameters are not recorded in the library entry | -- |

## Findings in full

<a id="outputs-out-01"></a>

### OUT-01 -- GUI single scans file shading-corrected pixels as raw; Save As/Save all then double-correct them

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1443-1458`, `rps7200/session.py:1086-1101`, `rps7200/direct.py:2708`, `rps7200/direct.py:2733`, `rps7200/library.py:373-391`, `tools/gui.py:3864`, `tools/gui.py:4110`

**Doc claim:** CLAUDE.md:108-128 ('The library holds raw pixels ... library.save takes raw pixels and corrections= is how a caller admits it is handing over something else'); session.py:1060-1066 ('the library entry below carries neither'); preview.py:3-5 ('The scans this driver files are raw negatives and stay that way')

Unlike _prescan (session.py:1415) and the roll path (raw_image=rf.raw_image), the GUI's single Scan job never passes the pass's last_pixels_raw. FrameWriter falls back to job["image"], which is the corrected picture, and files it with corrections_applied: []. The capture still carries the pass's raw bytes, so the entry's scan.tif and raw.bin.gz disagree. library.corrected() believes the pixels are raw and divides by the shading reference a second time. That feeds Save As, Save all and the full-resolution 1:1 view. The shape-mismatch fallback at session.py:2115-2120 knowingly does the same thing ('filing the corrected pixels instead') without labelling it.

**Evidence (from the code):**

```text
session._scan: `image, meta = self._scanner.scan(..., shading=job.shading, ..., keep_raw=True)` then session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=wants_mono(job.mono, job.film), mono_channel=job.mono_channel)` -- no raw_image. DirectScanner.scan returns the corrected array (direct.py:2708 `raw_pixels = image`, 2733 `image, shading_report = apply_shading(image, self._shading, ccd_mask)`, 2830 `return image, meta`). FrameWriter: `entry = library.save(job["image"] if raw_image is None else raw_image, job["meta"], ..., **job["capture"])` with no `corrections=`. library.corrected: `if "shading" in applied: ... return image` else `image, report = apply_shading(image, record["reference"], record["ccd_mask"])`.
```

**Failure scenario:** The operator calibrates and presses Scan in the GUI at 1800 dpi. The output-folder TIFF (FrameWriter) is corrected once. The library scan.tif is corrected but recorded as raw. Save As on that pass writes a double-corrected file: edge columns get gain^2, and more highlights clip. It no longer matches the output-folder copy of the same scan. `tools/library.py reconstruct` reports a changed decode on every GUI scan, and `migrate-raw` lists them as mislabelled. tests/test_session.py never checks that a Scan job's entry equals last_pixels_raw.

**Fix:** In _scan, read `raw = getattr(self._scanner, 'last_pixels_raw', None)` right after scan() and pass `raw_image=raw` to _file, as _prescan does. When FrameWriter has to file job["image"] and meta["shading"] is set, pass `corrections=["shading"]` to library.save; the same applies to the shape-mismatch fallback. Add a test that a GUI Scan entry's scan.tif equals the scanner's last_pixels_raw. Repair existing entries with `migrate-raw --write`.

<details><summary>Second reader's check</summary>

session.py:1443-1458 `_scan` calls `self._file(seq, 0, image, meta, job.notes, ...)` with no raw_image, while `_prescan` (1415) reads `last_pixels_raw`. direct.py:2708 `raw_pixels = image`, 2733 `image, shading_report = apply_shading(...)`, 2830 `return image, meta`, so `image` is the corrected picture. FrameWriter (session.py:1086-1091) falls back to `job["image"]` and calls library.save without `corrections=`, so corrections_applied is []. library.corrected (library.py:373-391) then applies shading a second time. Save As and Save all (gui.py:3864-3868) go through library.corrected. I found no guard. tools/scan.py:179-193 carries a comment about fixing exactly this bug for the CLI; the GUI path was not fixed.

</details>

<a id="outputs-out-02"></a>

### OUT-02 -- FrameWriter writes delivered files before the library entry, so any delivery failure loses the raw bytes

**Severity** critical · **Category** data-integrity · **Verdict** partly · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1066-1095`, `rps7200/session.py:1045-1056`, `rps7200/export.py:181-193`, `tests/test_roll_writer.py:67-83`

**Doc claim:** export.py:133-138 ('the picture is on disk by now and the library entry is still to come, so a full disk or a read-only folder must cost the infrared plane and not the scan'); FrameWriter docstring session.py:1026-1028 ('a frame that cannot be filed should cost that frame')

FrameWriter._write writes the delivered files (orient, to_monochrome, mkdir, export.write) before library.save. Any exception on the delivery side, such as a full or disconnected output volume, an OSError, or a ValueError from preview.rotate on a non-quarter rotation, aborts the job before the library entry is written. The raw bytes, held only in memory in job['capture'], are then discarded. A persistent delivery fault repeats this for every later frame of the roll while scanning carries on. The code conflicts with the FrameWriter docstring's aim ('a frame that cannot be filed should cost that frame') and with CLAUDE.md's 'file every scan'. export.py's docstring describes this same ordering; it does not contradict it.

**Evidence (from the code):**

```text
FrameWriter._write: `turned = preview.orient(...)`; `delivered = (to_monochrome(...) if job.get("mono") else turned)`; `for path in job.get("paths") or (): Path(path).parent.mkdir(parents=True, exist_ok=True); note = export.write(str(path), delivered, ...)` and only afterwards `if job["library"]: ... entry = library.save(...)`. _run: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")`.
```

**Failure scenario:** The output folder is on a USB stick or network share. Mid-roll it fills up or disconnects. From then on every frame's export.write raises OSError, and library.save never runs, so the raw bytes and raw pixels of those frames are discarded while the roll keeps scanning. Other triggers: a mono_channel the scan lacks (to_monochrome ValueError), or a hand-edited sheet rotation of 45 in gui-settings.json. _clean_sheet_state casts it with int() and never checks for quarter turns, so preview.rotate raises. The test for an unwritable frame (test_roll_writer.py:67) runs with library=None, so it never sees the lost entry.

**Fix:** File the library entry first, then deliver. Wrap each delivered path in its own try so one bad destination costs one file, and report delivery failures as notes or errors without skipping the filing.

<details><summary>Second reader's check</summary>

The ordering is real. FrameWriter._write (session.py:1066-1080) orients, converts to mono, runs mkdir and export.write for every path, and only then calls library.save (1082-1095). Any exception goes to _run (1045-1056), which records an error and discards the job, including the in-memory raw bytes in job['capture']. test_roll_writer.py:67 does run without a library. The cited doc contradiction is wrong, though: export.py:133-138 describes the same order ('the picture is on disk by now and the library entry is still to come') and uses it as the reason _write_infrared must not raise. It does not promise the reverse order.

</details>

<a id="outputs-out-03"></a>

### OUT-03 -- Debug flush deletes the spooled raw data even when library.save (tiff.write) failed

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:694-760`, `rps7200/library.py:179`, `rps7200/tiff.py:164-168`

Debug filing (RPS7200_DEBUG=1, which CLAUDE.md requires for Claude's scripts) spools each pass to a temp dir and files it after close(). If library.save raises, for example because tiff.write fails on a full disk, a permission error or a tifffile incompatibility, the finally block still unlinks the spooled image and raw bytes. A failure in the output writer is thereby turned into permanent data loss.

**Evidence (from the code):**

```text
`except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")` followed by `finally: image = None; for key in ("image_path", "raw_path"): ... Path(path).unlink(missing_ok=True)`.
```

**Failure scenario:** A 7200 dpi roll under RPS7200_DEBUG=1 fills the library disk during the flush. tiff.write raises on scan.tif, and the finally clause deletes the 1.1 GB spool of that frame. The same happens for every remaining frame, each logged as 'could not file scan'.

**Fix:** Unlink the spool only after a successful save. On failure, keep it and log its path so it can be filed later. Consider a `library.py file-spool` recovery command.

<details><summary>Second reader's check</summary>

direct.py:716-760: `except Exception as exc: self._log(f"debug: could not file scan {n} ({exc})")` and then an unconditional `finally` that unlinks image_path and raw_path. On top of that, shutil.rmtree(self._debug_spool) runs at the end regardless. A failed library.save (tiff.write or gzip raising) therefore destroys the only spooled copy. library.save may also leave behind a half-written entry directory with no scan.json.

</details>

<a id="outputs-out-04"></a>

### OUT-04 -- Bracket merge judges saturation on shading-corrected values

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `rps7200/bracket.py:37-46`, `rps7200/bracket.py:125-134`, `rps7200/bracket.py:214-217`, `rps7200/bracket.py:315-326`, `rps7200/shading.py:260-280`, `rps7200/direct.py:2392-2401`, `tools/scan.py:205-221`

**Doc claim:** docs/exposure-negative-plan.md:53-55 ('The correction's per-column gain exceeds 1 wherever the lamp falls off, so it pushes near-rail values around'); docs/multi-exposure-plan.md:153 ('c_i = confidence(raw_i)'); docs/multi-exposure-plan.md:367 ('one pass fully clipped (ignored, not averaged in)')

The sensor rails at raw 65535. After per-column shading a railed sample becomes 65535*gain_j. The raw lamp falloff is about 39%, and columns brighter than the mean have gain < 1. There a railed sample lands at roughly 0.8-0.9 of full scale and keeps confidence of about 0.5-1.0 instead of 0, and solve_relation fits it as if it were linear. In edge columns with gain > 1, unsaturated samples are zero-weighted, and the correction's own clamp is taken for sensor saturation. The later gates (luminance z, misalignment) average the three channels, so single-channel (blue) railing is diluted.

**Evidence (from the code):**

```text
`CLIP_START = 0.80 * FULL_SCALE`; `clip_w = 1.0 - _smoothstep((raw - CLIP_START) / max(CLIP_END - CLIP_START, 1e-12))`; solve_relation `usable = (a > SNR_FLOOR) & (a < CLIP_START) & (b > SNR_FLOOR) & (b < CLIP_START)`; the frames come from `self.scan(..., shading=shading)`, i.e. `vals = (image[...] - dark) * gain` with `gain = (mean - dark_mean) / (light - dark)` and then `np.clip(vals, 0, maxval)`.
```

**Failure scenario:** `tools/scan.py --bracket 3 --stops 2` on a thin colour negative. The longest pass rails in the thin areas. In the centre columns those railed samples carry about r^2 times the reference's weight and pull the merged highlights down by a column-dependent amount, giving banded, biased highlights. MergeStats still reports a normal-looking merge. The plan's requirement that a fully clipped pass be 'ignored, not averaged in' holds only on the synthetic, unshaded test data.

**Fix:** Compute confidence and the fit mask from each pass's raw pixels (last_pixels_raw, captured in on_pass, or the library scan.tif) and apply them to the corrected values. Alternatively, carry a saturation mask (raw >= rail) out of scan() for each pass.

<details><summary>Second reader's check</summary>

tools/scan.py calls scan_bracket with shading on (default), so each frame is the output of apply_shading. shading.py:260-280 computes `vals = (image - dark) * gain` with gain = (mean - dark_mean)/(light - dark), then rounds and clips to maxval. bracket.confidence (125-134) and solve_relation (216) judge saturation on those corrected values with CLIP_START=0.80*FS. In a centre column with gain of about 0.8-0.85, a railed raw 65535 becomes roughly 52-56k and keeps confidence of 0.9-1.0. The longest pass is deliberately pinned to the exposure-timer ceiling (bracket_ladder 2304-2313), so railed samples are common. The residual gate does not rescue them either: its fallback `prefer` is argmax(weight), which is again the railed long pass. bracket.py:26-29 states the corrected-input design explicitly, but that is the flaw here, not a defence.

</details>

<a id="outputs-out-05"></a>

### OUT-05 -- --bracket with --ir merges one RGBI pass (blue about 5x brighter) using a ratio fitted on green only

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `rps7200/direct.py:2373-2401`, `rps7200/direct.py:2518-2522`, `rps7200/bracket.py:291-301`, `rps7200/bracket.py:315-326`, `tools/scan.py:252-258`

**Doc claim:** docs/multi-exposure-plan.md:348 ('Phase 2 — infrared, only after Phase 1 passes'); direct.py:2347-2351

Metering for an RGBI scan lowers blue by blue_rgbi_headroom (5.2 on negative, 11 on other films), and those scales are reused on every RGB pass of the bracket. Only the last pass is RGBI, and in it blue returns about 5x brighter at the same exposure. merge_bracket solves one slope and intercept on green and applies them to R, G and B. The RGBI pass's blue therefore lands about 5x too high on the reference scale, or is railed without metering. Meanwhile every RGB pass's blue is roughly 2.4 stops underexposed. Separately, the fitted intercept (the 425*(r-1) over-subtraction in the docstring) comes from dark levels that differ per channel, yet green's intercept is used for all three.

**Evidence (from the code):**

```text
scan_bracket: `scales = self.auto_exposure(film=film, infrared=infrared)`; `pass_scale = [s * k for s in scales[:3]]`; `infrared=infrared and last`. merge_bracket: `slope, intercept = solve_relation(ref[..., 1], np.asarray(frame)[..., 1])` and then `scaled.append(raw / r)` for all three channels. direct.py:2518-2520: 'Blue ... coming back about 5x brighter at the same exposure ... handled by metering blue lower when an RGBI scan follows'.
```

**Failure scenario:** `tools/scan.py --bracket 3 --ir --auto-exposure --film negative`. The merged blue is wrong wherever the RGBI pass carries weight, and it is the highest-weight pass. Where the luminance gate does fire, the pixel falls back to the darkest pass, whose blue was deliberately underexposed. The delivered file shows a blue cast or blotches while `bracket:` prints a normal summary.

**Fix:** Refuse --bracket together with --ir until Phase 2 is done. Otherwise, fit slope and intercept per channel, take only the IR plane from the RGBI pass, and meter the RGB passes without the RGBI blue headroom.

<details><summary>Second reader's check</summary>

scan_bracket (direct.py:2373-2401) meters with `auto_exposure(film=film, infrared=infrared)`, which lowers blue's target by blue_rgbi_headroom (2121-2122). It reuses those scales for every pass and takes only the last pass as RGBI (`infrared=infrared and last`). merge_bracket (bracket.py:296-301) fits slope and intercept on green only (`ref[..., 1]`) and applies them to all channels (315-326). The RGBI pass's blue (about 5x brighter) therefore enters the merge about 5x high, and as the highest-weight pass it also wins the `prefer` fallback. The RGB passes' blue is under-exposed by the headroom factor. tools/scan.py:254 merges `f[..., :3]` without any per-channel handling.

</details>

<a id="outputs-out-06"></a>

### OUT-06 -- GUI compresses TIFFs and gzips raw bytes while the scanner is open and idle

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:1311`, `rps7200/session.py:1013-1020`, `rps7200/session.py:1059-1101`, `rps7200/session.py:1329-1333`, `rps7200/tiff.py:149-153`, `rps7200/library.py:186-205`, `tools/gui.py:3820-3839`

**Doc claim:** CLAUDE.md:160-163 ('A single scan compresses nothing while the device is open ... gzipped after close()'); CLAUDE.md:240-241 ('Do not hold the session open through heavy local work'); session.py:1017-1020 ('overlaps the next frame's scan, so the device is busy rather than idle throughout')

On a roll, FrameWriter's work overlaps the next frame's scan. For single GUI scans and prescans there is no next scan: the device stays open and idle while tens to hundreds of MB are deflated and gzipped. That is the state CLAUDE.md names as having preceded a wedge. Save all also re-corrects and compresses every pass with the session open.

**Evidence (from the code):**

```text
`self._writer = FrameWriter(on_done=self._filed)` is created after `self._scanner.open()` and lives for the whole session. _scan and _prescan submit to it immediately, and _write runs export.write (tifffile zlib + predictor) and library.save (`gzip.open(..., compresslevel=6)`) right away. The comment at session.py:1329 says 'Order matters: the device closes first, and only then does the writer get to spend time gzipping', but that ordering only covers what is still queued at shutdown.
```

**Failure scenario:** The operator scans one 3600 dpi RGBI frame in the window and then studies it. For tens of seconds the writer thread compresses a ~140 MB TIFF and gzips the raw bytes while the scanner sits open and idle.

**Fix:** For non-roll jobs, spool the raw data uncompressed and compress after close, as debug filing does. Or measure the idle case with tools/filing_load_test.py before trusting it. At minimum, correct the FrameWriter docstring and CLAUDE.md.

<details><summary>Second reader's check</summary>

session.py:1311 creates FrameWriter after `self._scanner.open()`, and the session stays open for the whole window lifetime. _scan and _prescan submit immediately, and _write then runs export.write (tifffile zlib) and library.save (gzip compresslevel=6, library.py:186) right away. After a single scan no next scan follows, so the device sits open and idle during the compression. The ordering comment at 1329-1331 only covers the final drain. This contradicts CLAUDE.md's 'A single scan compresses nothing while the device is open'. Save all's re-correct-and-write loop also runs with the session open.

</details>

<a id="outputs-out-07"></a>

### OUT-07 -- Clipping and histogram readout counts corrected pixels, so it does not show sensor saturation

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/preview.py:349-371`, `tools/gui.py:3949-3976`, `tools/gui.py:4110`, `rps7200/shading.py:273-280`

**Doc claim:** preview.py:352-358 ('*At* full scale is information already destroyed')

After shading, a value of 65535 means the correction clamped a column with gain > 1, not that the sensor saturated. A raw-railed sample in a gain < 1 column reads about 0.8-0.9 of full scale and is not even counted as 'near'. The shading report's `clipped` count is also taken after correction. No readout reports raw rail counts.

**Evidence (from the code):**

```text
`clipping`: `[(planes[..., c] == 0).sum(), (planes[..., c] == top).sum(), (planes[..., c] >= near).sum()]`. The GUI calls `pixels = rgb_only(pixels)`; `clipped = preview.clipping(pixels)` on result.image or on `library.corrected(entry)`.
```

**Failure scenario:** With a fixed exposure set too high, blue rails across the centre of the frame. The histogram panel shows well under 1% at or near full scale, and the operator keeps the setting and loses highlight detail on every frame of the roll.

**Fix:** Count the rail on raw pixels (last_pixels_raw or the library scan.tif) at scan time, record it in meta, and show it in the panel.

<details><summary>Second reader's check</summary>

preview.clipping (349-371) counts `== top` and `>= int(top*0.99)` on whatever it is given. gui.py:3949-3976 passes result.image or the library.corrected pixels, both shading-corrected. A raw-railed sample in a column with gain < 1 lands below 0.99*FS and is not counted. The shading report's clipped count (shading.py:275-278) is `vals > maxval` after the gain, which measures the correction's clamp, not sensor saturation. No raw rail count is recorded or shown anywhere.

</details>

<a id="outputs-out-09"></a>

### OUT-09 -- Non-atomic tiff.write is used to rewrite library files in place; prescan.tif has no checksum

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/tiff.py:164`, `rps7200/tiff.py:167-168`, `rps7200/dng.py:145-146`, `rps7200/library.py:620-623`, `rps7200/library.py:572-574`, `tools/library.py:196-198`, `rps7200/library.py:297-301`, `rps7200/library.py:776-817`

Opening for write truncates the destination before the new data exists. scan.tif rewrites can be regenerated from raw.bin.gz. prescan.tif, however, has no raw bytes of its own (library.save docstring), so an interrupted rewrite destroys the only copy. Because the record carries no checksum, `verify` cannot detect the damage. An interruption between the TIFF write and the scan.json write also leaves pixels and record out of step.

**Evidence (from the code):**

```text
tiff.write: `tifffile.imwrite(path, ...)` or `with open(path, "wb") as fh: _write_builtin(...)`, writing straight to the final name. migrate_direction: `if write: tiff.write(str(path / "prescan.tif"), np.ascontiguousarray(prescan[::-1]))`, with scan.json written afterwards. The prescan record is `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}` with no sha256, and verify() never inspects prescan.tif.
```

**Failure scenario:** `tools/library.py migrate-direction --write` over a large library is stopped with Ctrl-C, hits a full disk, or crashes while turning a prescan. That entry's prescan.tif is left truncated or empty, and `verify` still reports the entry as fine.

**Fix:** In tiff.write and dng.write, write to a temporary sibling and os.replace it into place, as settings.save already does. Record and verify sha256 for prescan.tif, shading.npz and ccd_mask.bin.

<details><summary>Second reader's check</summary>

tiff.write writes straight to the final path (tifffile.imwrite(path) at 164, or open(path,'wb') at 167). migrate_direction rewrites prescan.tif in place (library.py:620-623) and scan.tif (572-576), and scan.json only afterwards (632-634). migrate-raw (tools/library.py:196-206) does the same. The prescan record (library.py:297-301) carries no sha256, and verify (776-817) checks only scan.tif, shading/ccd_mask existence and raw. A truncated prescan.tif is therefore undetectable and unrecoverable, since there are no raw bytes for it in a frame entry.

</details>

<a id="outputs-out-10"></a>

### OUT-10 -- make_comparison.py does not run the production correction, although it is the by-eye acceptance test

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/make_comparison.py:92-122`, `rps7200/direct.py:30-32`

**Doc claim:** CLAUDE.md:178-185 ('Regenerate the three comparison files after any significant change to the scan or correction path'); docs/vignette-plan.md:208-210 ('2_corrected.tif stage: shading applied, defect interpolation applied'); make_comparison.py:4 ('the scan as it came off the scanner')

The three comparison files are made by column destriping of whatever TIFF is given. The shading correction the product actually applies is not run, the file the product delivers is not measured, and '1_nothing_done.tif' is only raw if the input happens to be raw. A delivered TIFF is already shading-corrected.

**Evidence (from the code):**

```text
`raw = tiff.read(scan_path)` (any TIFF; default 'scans/negatives/state_1800dpi.tif'); `corrected = destripe(raw, defects, margin=12, dilate=5)`; then `tiff.write("2_corrected.tif", corrected, ...)`. There is no apply_shading or library.corrected, and destripe is imported by direct.py but never called in the scan path.
```

**Failure scenario:** After a change to apply_shading, the files are regenerated as CLAUDE.md asks, from a delivered TIFF. They show neither the change nor what the software ships, and Stefan's authoritative by-eye verdict is given on a different pipeline.

**Fix:** Build the files from a library entry: raw = its scan.tif; corrected = library.corrected(entry), written through export.write so the delivered write path is included. Keep destripe as an optional, clearly labelled extra stage.

<details><summary>Second reader's check</summary>

make_comparison.py:95-122 reads any TIFF (default scans/negatives/state_1800dpi.tif), computes `corrected = destripe(raw, defects, margin=12, dilate=5)` and writes 1_/2_/3_. apply_shading, library.corrected and export.write are never called. destripe( is called nowhere else in rps7200/ or tools/, so the product never applies it. docs/vignette-plan.md:208-210 claims the 2_ stage is 'shading applied, defect interpolation applied'. The code applies no shading.

</details>

<a id="outputs-out-11"></a>

### OUT-11 -- A corrupt settings file is discarded silently and overwritten; save failures are silent

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `rps7200/settings.py:58-79`, `rps7200/settings.py:82-97`, `tools/gui.py:666-697`, `tools/gui.py:3039-3049`, `tools/gui.py:2264-2275`, `tools/gui.py:2340-2350`

**Doc claim:** settings.py:3-6 ('Losing this file costs a few seconds of resetting controls'); settings.py:12-13 ('meant to be readable and editable by hand'); settings.py:22-23 ('Beside the library ... belongs to this checkout')

A settings file that fails to parse is silently replaced by defaults, and the next _remember (on close, roll open, sheet change or preset change) overwrites it. That loses presets, shortcut overrides, the rolls table and the contact sheet's uncommissioned decisions without a word. settings.save swallows OSError/TypeError/ValueError and returns None, and _remember never checks the return value, so failed saves are silent too. Concurrent windows share one '.part' temp name. The default path is CWD-relative, but so is the library's, so that part is not a doc mismatch.

**Evidence (from the code):**

```text
load: `except (OSError, json.JSONDecodeError, ValueError): return blank`. save: `temporary = target.with_name(target.name + ".part")` ... `except (OSError, TypeError, ValueError): return None`. gui._remember calls `settings.save({...}, self._settings_path)` and ignores the return value; it runs on close, on preset and shortcut changes, on roll open, and on sheet state changes.
```

**Failure scenario:** Stefan hand-edits gui-settings.json and leaves a trailing comma. The next launch opens on defaults without comment, and on quit every preset, shortcut and unsaved contact-sheet decision is gone for good.

**Fix:** On a parse failure, rename the file to *.corrupt-<timestamp> and tell the operator. Have _remember report a None return. Use a unique temp name. Resolve the default path relative to the library root.

<details><summary>Second reader's check</summary>

Confirmed: settings.load (58-79) returns blank defaults on any JSON or OS error without saying so, and _remember (gui.py:666-697) writes the whole payload on close, on roll open, on sheet-state change and elsewhere, so the corrupt file is overwritten. That loses presets, shortcuts, rolls and uncommissioned sheet decisions. save returns None on OSError/TypeError/ValueError, and _remember ignores the return value; its own except only catches exceptions raised before save. The fixed '.part' name is also real. Not confirmed: the claim that DEFAULT_PATH contradicts 'Beside the library'. library.DEFAULT_ROOT is also CWD-relative (`Path("library")`, library.py:51), so the two do sit side by side relative to the working directory.

</details>

<a id="outputs-out-12"></a>

### OUT-12 -- tools/scan.py --bracket silently ignores a single-value --exposure-scale

**Severity** medium · **Category** user-error · **Verdict** confirmed · **Problem** [P29](../problems/P29-bracket-merge.md)

**Where:** `tools/scan.py:143-146`, `tools/scan.py:205-218`, `rps7200/direct.py:2373-2378`

A single float is dropped and metering is switched off, so the bracket runs at the device's base exposure. There is no warning, although the help text promises to 'hold exposure at this multiple'.

**Evidence (from the code):**

```text
`exposure_scale = parts[0] if len(parts) == 1 else parts`; `auto_exposure=args.auto_exposure and not args.exposure_scale`; `exposure_scale=(list(exposure_scale) if isinstance(exposure_scale, list) else None)`. scan_bracket then falls to `else: scales = [1.0, 1.0, 1.0]`.
```

**Failure scenario:** `--bracket 3 --exposure-scale 1.5` scans a ladder around 1.0x. The passes are not the requested measurement, and only the per-pass exposure_scale in meta reveals it.

**Fix:** Pass `[exposure_scale] * 3` when a float was given, or refuse the combination.

<details><summary>Second reader's check</summary>

tools/scan.py:143-146 turns a single value into a float. At 211-215 the bracket gets `auto_exposure=args.auto_exposure and not args.exposure_scale` (False) and `exposure_scale=None` when that value is not a list. scan_bracket (direct.py:2373-2378) then takes `scales = [1.0, 1.0, 1.0]`. No warning is printed; line 147 only covers the auto-exposure override.

</details>

<a id="outputs-out-13"></a>

### OUT-13 -- Bracket membership, merge parameters and metering are not recorded in the library, so a bracket cannot be re-merged offline

**Severity** medium · **Category** library-completeness · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2402-2405`, `rps7200/library.py:237-261`, `tools/scan.py:238-246`, `tools/scan.py:261-265`, `tools/scan.py:280-281`, `rps7200/direct.py:2805-2806`

**Doc claim:** docs/multi-exposure-plan.md:345-346 ('Every pass files in the library ... so a bracket can be re-merged offline later without rescanning')

The passes are filed, but nothing says which entries form one bracket, their order, the commanded ratios or the metering behind them. The merged picture and its parameters live only in the delivered sidecar, which the next run with the default --out overwrites.

**Evidence (from the code):**

```text
scan_bracket sets `meta["bracket_index"]`, `["bracket_ratio"]`, `["bracket_passes"]`, `["bracket_stops"]`, but the library `scan` whitelist ('resolution_dpi', 'frame', ... 'filter_offsets') omits all four. Entries are saved with `tags=args.tags`. The merge record `meta["bracket"] = {passes, ratios, stops, stats}` exists only in `out.with_suffix(".json")`. Each pass runs scan() with auto_exposure=False, so `if auto_exposure and self.last_metering` never attaches the bracket's metering.
```

**Failure scenario:** A year later someone wants to re-merge a bracket with a fixed merge_bracket. Two brackets of the same frame on the same day are indistinguishable except by timestamps, and the metering evidence is gone.

**Fix:** Add the bracket_* fields and a bracket id to the record whitelist, tag the entries, attach last_metering to every pass, and record the merge parameters.

<details><summary>Second reader's check</summary>

scan_bracket sets bracket_index, bracket_ratio, bracket_passes and bracket_stops (direct.py:2402-2405), but the library.save record whitelist (library.py:237-261) contains none of them. tools/scan.py saves with tags=args.tags and adds no bracket id. The merge record goes only into `out.with_suffix('.json')` (280-281), which the default --out overwrites on every run. scan() is called with auto_exposure at its default of False, so `if auto_exposure and self.last_metering` (direct.py:2805-2806) never attaches the bracket's metering to any pass.

</details>

<a id="outputs-out-14"></a>

### OUT-14 -- The demo files corrected prescans and roll frames as raw, a branch the real scanner does not take

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:176`, `rps7200/demo.py:455-477`, `rps7200/demo.py:771-779`, `rps7200/demo.py:1001-1003`, `rps7200/session.py:1415`, `rps7200/session.py:1086-1091`

**Doc claim:** CLAUDE.md:190-196 ('--demo may change what the software is fed. It may not change what the software does.')

In the demo every prescan and roll frame reaches FrameWriter's `job["image"] if raw_image is None` fallback. Corrected pixels are filed as raw, next to raw bytes that decode to the uncorrected image. The real path files raw_image, so the demo exercises different filing code, contrary to the rule that the demo only changes the inputs.

**Evidence (from the code):**

```text
DemoScanner is not a DirectScanner subclass and never sets last_pixels_raw, so `raw_image = getattr(self._scanner, "last_pixels_raw", None)` gives None. The demo builds `RollFrame(index=..., image=image, meta=..., prescan=prescan, ...)` without raw_image or raw_prescan. Its images are corrected: `image, self._shading_report = apply_shading(image, reference, mask)`.
```

**Failure scenario:** `make run-demo` fills demo/library with entries that `reconstruct` calls changed decodes. A regression in the real raw_image filing path cannot show up in the demo.

**Fix:** Have DemoScanner publish last_pixels_raw (its decode before apply_shading) and fill RollFrame.raw_image and raw_prescan the way DirectScanner does.

<details><summary>Second reader's check</summary>

DemoScanner never sets last_pixels_raw (grep finds none in demo.py). Its RollFrame (demo.py:771-779) omits raw_image and raw_prescan. _decode applies shading whenever a reference exists (demo.py:1009-1010). capture_record hands back the stored raw bytes (435-442). So wherever the shapes agree, session._file files corrected pixels labelled as raw next to raw bytes that decode to the uncorrected picture. The real prescan and roll paths file raw_image. There is also a further divergence: demo.scan corrects even when shading=False, while meta['shading'] is reported as None.

</details>

<a id="outputs-out-15"></a>

### OUT-15 -- A roll frame's library prescan.tif is the corrected prescan, unlabelled

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/direct.py:3493-3497`, `rps7200/direct.py:3656-3660`, `rps7200/session.py:1880-1890`, `rps7200/library.py:180-181`, `rps7200/library.py:297-301`

The frame entry stores the shading-corrected prescan while the rest of the entry is raw, and nothing in the record says so. The raw prescan (rf.raw_prescan) is available but is not what gets stored there.

**Evidence (from the code):**

```text
`prescan_image, _ = self.prescan(...)` (prescan calls `self.scan(..., shading=True)`), `RollFrame(..., prescan_image, marks, raw_image=..., raw_prescan=raw_prescan, ...)`, then `self._file(..., prescan=rf.prescan, ...)` and `tiff.write(str(path / "prescan.tif"), prescan)`. The prescan record has no corrections field.
```

**Failure scenario:** Later analysis compares scan.tif (raw) with prescan.tif (corrected) as like with like, and migrate_direction judges orientation on mixed data.

**Fix:** Store rf.raw_prescan, or record prescan.corrections_applied = ['shading'].

<details><summary>Second reader's check</summary>

scan_roll stores `prescan_image` (from prescan(), which is shading-corrected, direct.py:3494/1704-1709) as RollFrame.prescan. session.py:1886 files `prescan=rf.prescan`, and library.save writes it to prescan.tif (181) with no corrections field (297-301). rf.raw_prescan is available but used only on the dry-run path (session.py:1830). I raised the severity to medium because, against the owner's exact-bits requirement, the raw prescan pixels and bytes of every real roll frame are discarded although they are in hand.

</details>

<a id="outputs-out-08"></a>

### OUT-08 -- Save As / Save all write no dpi, and the two TIFF writers disagree on the default resolution

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/gui.py:3868`, `tools/gui.py:3879-3882`, `rps7200/tiff.py:146-148`, `rps7200/tiff.py:241-245`, `rps7200/dng.py:216-218`, `rps7200/library.py:181`

**Doc claim:** tiff.py:15-18 ('*Equivalent* is the load-bearing word ... the two must not disagree'); gui.py:3848-3853 ('Save as ... and Save all ... cannot disagree')

Every Save As and Save all file, and its DNG companion, is written without the scan's dpi. The built-in writer then stamps 72 dpi/inch; the tifffile path leaves tifffile's own default. The same pass written by FrameWriter gets resolution=job["dpi"], so two deliveries of one pass carry different metadata. Library prescan.tif is written the same way.

**Evidence (from the code):**

```text
`note = export.write(path, full, quality=quality)` passes no resolution. The built-in writer does `res = int(resolution) if resolution else 72`. The tifffile path only sets `kwargs["resolution"]` and `"resolutionunit"` `if resolution:`.
```

**Failure scenario:** Save As on a 3600 dpi frame produces a TIFF that says 72 dpi, or nothing, depending on whether tifffile happens to be installed. A consumer that sizes by dpi gets a frame 50x too large, and the TIFF's metadata differs between machines.

**Fix:** Pass `resolution=(result.meta or {}).get('resolution_dpi')` in _deliver_one, and pass the prescan dpi in library.save. Make both writers emit identical resolution tags when none is given.

<details><summary>Second reader's check</summary>

gui.py:3868 and 3879 call `export.write(path, full, quality=quality)` with no resolution. The built-in writer (tiff.py:241) writes 72 dpi, and the DNG writer (dng.py:216) also writes 72. The tifffile path (146-148) sets no resolution, so tifffile's default applies (1/1, no unit). FrameWriter passes resolution=job['dpi']. Library prescan.tif is also written without resolution (library.py:181). This is metadata only, so the pixels are unaffected; I lowered the severity to low. It does break the 'the two must not disagree' claim in tiff.py:15-18.

</details>

<a id="outputs-out-16"></a>

### OUT-16 -- JPEG-to-TIFF fallback and the DNG companion overwrite existing files; _unclaimed and Save all's promise do not cover them

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/export.py:146-149`, `rps7200/export.py:186-190`, `rps7200/session.py:2071`, `tools/gui.py:3805-3810`, `tools/gui.py:3826`

Only the requested name is checked for a clash. The TIFF the export falls back to and the .dng written beside a JPEG replace whatever already sits at those names.

**Evidence (from the code):**

```text
`path = path.with_suffix(SUFFIXES["tiff"]); tiff.write(str(path), image, resolution=resolution)`; `companion = infrared_path(path)` followed by `dng.write(companion, ...)`; `paths.append(_unclaimed(where / self._out_name(number, meta, roll)))` probes only the .jpg name. Save all tells the operator 'Nothing already there is overwritten'.
```

**Failure scenario:** Frames were delivered as TIFF, then the output is switched to JPEG on a machine without Pillow and a frame is rescanned. The .jpg name is free, so the fallback .tif silently replaces the earlier delivery. Likewise, an x.dng kept after x.jpg was deleted is replaced.

**Fix:** Work out every file a delivery will create (fallback .tif, companion .dng) before choosing names, and apply _unclaimed to all of them.

<details><summary>Second reader's check</summary>

session._unclaimed (2192-2204) probes only the requested name. export.write's fallback without Pillow writes `path.with_suffix('.tif')` (186-188), and the JPEG path writes `infrared_path(path)` (.dng, 146-149), both with no existence check. Save all's dialog promises that nothing is overwritten (gui.py:3805-3810). _unclaimed also returns `wanted` itself after 999 candidates.

</details>

<a id="outputs-out-17"></a>

### OUT-17 -- tools/scan.py checks the output name only after scanning, and the default --out overwrites the previous scan

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:40-43`, `tools/scan.py:267-281`, `rps7200/export.py:66-79`, `tools/gui.py:3754-3768`

A bad extension is found only after minutes of scanning, and the delivered file is then missing (the library entries are already filed). Re-running with the default --out silently replaces scan.tif and scan.json. In the GUI, typing an unsupported extension in Save As raises inside a Tk callback: a traceback goes to stderr and the window says nothing.

**Evidence (from the code):**

```text
`ap.add_argument("--out", default="scan.tif", ...)`; after the scanner closes: `note = export.write(out, delivered, ...)` (format_of raises ValueError on '.png'), then `out.with_suffix(".json").write_text(...)`. on_save_as calls _deliver_one without try, and there is no report_callback_exception handler.
```

**Failure scenario:** `tools/scan.py --dpi 3600 --ir --out frame.png` spends about 4 minutes and then dies with ValueError. With --no-library, a second default run destroys the first delivered scan.

**Fix:** Call export.format_of(args.out) straight after argument parsing. Refuse an existing --out, or pick a free name. Catch errors in on_save_as and show them in the window.

<details><summary>Second reader's check</summary>

tools/scan.py:40 sets default --out scan.tif. format_of runs only inside export.write at 278, after scanning and filing. The .json sidecar is overwritten unconditionally. on_save_as (gui.py:3754-3768) calls _deliver_one without a try, and there is no report_callback_exception anywhere in gui.py, so a '.png' name raises ValueError into Tk's default handler (stderr only).

</details>

<a id="outputs-out-18"></a>

### OUT-18 -- _z_medians casts every full frame to float64 before subsampling

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/bracket.py:304-307`, `rps7200/bracket.py:229-233`, `rps7200/bracket.py:73-75`

This allocates one full-size float64 copy per pass, plus temporaries. That defeats the CHUNK_ROWS banding meant to avoid 'several full-frame float32 planes at once'.

**Evidence (from the code):**

```text
`medians = _z_medians([np.asarray(f).astype(np.float64) - o for f, o in zip(frames, offsets)], ratios, alpha, beta)`; _z_medians subsamples only afterwards.
```

**Failure scenario:** A 9-pass 3600 dpi bracket (about 52M samples per pass) needs about 3.8 GB just for this step and can hit MemoryError after roughly 25 minutes of scanning. The passes are already filed, but there is no delivered file.

**Fix:** Subsample each frame first and then cast and subtract the offset.

<details><summary>Second reader's check</summary>

bracket.py:304-307 builds `np.asarray(f).astype(np.float64) - o` for every full frame before _z_medians subsamples it (245). That is one full float64 copy per pass plus a temporary, against the banding meant by CHUNK_ROWS.

</details>

<a id="outputs-out-19"></a>

### OUT-19 -- The bracket noise model always uses the fallback constants; fit_noise_params is never called

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/bracket.py:12-15`, `rps7200/bracket.py:52-56`, `tools/scan.py:254`

**Doc claim:** bracket.py:13-15 ('whose two constants are measured from our own flats by fit_noise_params rather than assumed')

The weights use DEFAULT_ALPHA=1.0 and DEFAULT_BETA=4096, which the module itself calls the no-flats fallback. The chosen alpha and beta are not recorded either.

**Evidence (from the code):**

```text
The only caller is `merged, stats = merge_bracket([f[..., :3] for f in frames], ratios)`, with no alpha or beta. fit_noise_params is referenced only in tests/test_bracket.py.
```

**Failure scenario:** Merges are weighted with made-up noise constants while the documentation says they are measured, and results cannot be reproduced against a later fitted model.

**Fix:** Fit from library flats, or pass stored measured constants, and record them in meta["bracket"]. Otherwise, fix the docstring.

<details><summary>Second reader's check</summary>

The only production call is tools/scan.py:254 `merge_bracket([...], ratios)`, with the default alpha=1.0 and beta=4096. fit_noise_params appears only in tests/test_bracket.py. The module docstring (12-15) says the constants are measured by fit_noise_params 'rather than assumed'. alpha and beta are not recorded in meta['bracket'].

</details>

<a id="outputs-out-20"></a>

### OUT-20 -- Merged bracket output is truncated rather than rounded

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/bracket.py:372`, `rps7200/mono.py:103-106`, `rps7200/shading.py:274-276`

Every merged sample is biased by -0.5 DN on average, inconsistent with the rounding rule used elsewhere.

**Evidence (from the code):**

```text
`out[y0:y1] = np.clip(chunk, 0, FULL_SCALE).astype(np.uint16)` truncates, while the rest of the driver uses `np.floor(vals + 0.5)`.
```

**Failure scenario:** A small systematic offset between merged and single-pass deliveries of the same frame.

**Fix:** Use np.floor(chunk + 0.5) before the cast.

<details><summary>Second reader's check</summary>

bracket.py:372 does `np.clip(chunk, 0, FULL_SCALE).astype(np.uint16)`, which truncates. shading.py:274 and mono.py:103-105 use floor(x+0.5).

</details>

<a id="outputs-out-21"></a>

### OUT-21 -- The built-in TIFF/DNG writer copies the whole image into memory, defeating the mmap in debug flush

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/tiff.py:190`, `rps7200/tiff.py:273`, `rps7200/dng.py:252`, `rps7200/direct.py:716-718`

tobytes() builds a full in-memory copy (570 MB for a 7200 dpi RGBI frame), so on a machine without tifffile the mmap saves nothing.

**Evidence (from the code):**

```text
`data = np.ascontiguousarray(image, dtype=...)`; `fh.write(data.tobytes())`. direct.py:716: 'mmap the image rather than loading it: tiff.write walks it once, so a 570 MB frame need not be resident.'
```

**Failure scenario:** Filing a 7200 dpi roll through debug flush without tifffile peaks at the frame's full size in RAM for every frame.

**Fix:** Use data.tofile(fh), or write strip by strip.

<details><summary>Second reader's check</summary>

tiff.py:190 does `np.ascontiguousarray(image, dtype='<u2')` and 273 does `fh.write(data.tobytes())`. tobytes materialises a full in-memory copy of the mmapped frame, which contradicts the comment at direct.py:716-718 about not making a 570 MB frame resident. dng.py:252 does the same.

</details>

<a id="outputs-out-22"></a>

### OUT-22 -- The built-in TIFF writer can place the IFD at an odd offset

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/tiff.py:193-194`, `rps7200/tiff.py:255-257`

For uint8 images where H*W*C is odd, such as 3-channel prescans with odd width and height, the IFD and the out-of-line values start on an odd byte. TIFF 6.0 requires a word boundary.

**Evidence (from the code):**

```text
`ifd_offset = data_offset + data_size`, with data_size = H*W*C*bytes and no padding.
```

**Failure scenario:** Strict TIFF readers warn about or refuse prescan.tif files and delivered prescans written on a machine without tifffile.

**Fix:** Pad the pixel data to an even length before the IFD.

<details><summary>Second reader's check</summary>

tiff.py:255 sets `ifd_offset = data_offset + data_size` with no padding. A 300 dpi uint8 RGB prescan is about 431 px wide (10343*300/7200), so H*W*3 is odd whenever H is odd, and the IFD then starts on an odd offset. TIFF 6.0 requires word alignment. Most readers tolerate it. The tifffile path is unaffected.

</details>

<a id="outputs-out-23"></a>

### OUT-23 -- The tifffile version floor predates the resolutionunit keyword that tiff.write uses (not verified locally)

**Severity** low · **Category** bug · **Verdict** partly

**Where:** `pyproject.toml:36`, `pyproject.toml:59`, `rps7200/tiff.py:146-148`

The declared tifffile floor (2021.7.2) probably predates the `resolutionunit` keyword that tiff.write passes whenever a resolution is given, so an environment satisfying the floor with a pre-2022.7 tifffile would raise on every library.save. This is unverified here; uv.lock pins 2025+.

**Evidence (from the code):**

```text
`tifffile = ["tifffile>=2021.7.2"]`; `kwargs["resolution"] = (resolution, resolution); kwargs["resolutionunit"] = "inch"`.
```

**Failure scenario:** With tifffile 2021.7-2022.6, every tiff.write with a resolution raises TypeError. library.save fails for every scan; in debug flush the spool is then deleted (OUT-03), and in FrameWriter the entry is lost.

**Fix:** Raise the floor to the first version with resolutionunit and test that floor in CI, or fall back to the 3-tuple form on TypeError.

<details><summary>Second reader's check</summary>

pyproject.toml:36/59 declares `tifffile>=2021.7.2`, and tiff.py:146-148 passes `resolutionunit="inch"`. As far as I recall, the resolutionunit parameter arrived in tifffile 2022.7.28. I could not check this locally because tifffile is not installed. The lock file pins 2025.5+ for every Python, so only a non-lock install reaches it. Plausible but not demonstrated.

</details>

<a id="outputs-out-24"></a>

### OUT-24 -- Stale '~212 s floor' cost claims for infrared

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/export.py:21-23`, `rps7200/dng.py:3-4`, `tools/scan.py:120-128`

**Doc claim:** CLAUDE.md:220-227 (IR tied to resolution is the default; untied ~220 s)

Infrared is tied to the resolution by default, costing about 25 s at 300 dpi and 110 s at 1800. The 212 s figure applies only to --no-fast-ir.

**Evidence (from the code):**

```text
export.py: 'the fourth plane is the reason an infrared pass costs its ~212 s floor'; tools/scan.py ap.error: 'the pass would spend its ~212 s floor a frame'. Yet `--fast-ir` defaults to True in tools/scan.py (`default=True`).
```

**Failure scenario:** Operators and reviewers overestimate the cost of infrared and make decisions on a wrong number.

**Fix:** Update the wording to describe tied versus untied cost.

<details><summary>Second reader's check</summary>

export.py:21-23 ('the reason an infrared pass costs its ~212 s floor') and dng.py:3-4 still use the old figure. tools/scan.py:124 has 'the pass would spend its ~212 s floor a frame' while --fast-ir defaults to True (48-49), and its own help text at 50-53 gives 110 s at 1800 and 25 s at 300. The same stale claim appears in the scan_bracket docstring (direct.py:2347-2349) and the auto_exposure docstring (direct.py:1997-1999).

</details>

<a id="outputs-out-25"></a>

### OUT-25 -- The DNG companion lacks ColorMatrix1, which the DNG spec requires for non-monochrome files

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/dng.py:19-21`, `rps7200/dng.py:33-37`, `rps7200/dng.py:199-230`

The module argues that 'a file that only one reader in the world accepts should not be wearing the .dng extension', yet it emits a non-monochrome LinearRaw file without the required colour matrix. In practice only NegPy's peek path is known to read it.

**Evidence (from the code):**

```text
The tag list covers NewSubfileType through UniqueCameraModel, with PhotometricInterpretation 34892 (LinearRaw), 4 samples, ExtraSamples [0,0,0], and no ColorMatrix1.
```

**Failure scenario:** The operator opens the delivered .dng in Lightroom or another DNG SDK-based tool and it is rejected or misread, so the infrared plane is effectively reachable only through NegPy.

**Fix:** Either add a neutral ColorMatrix1 and AsShotNeutral, or document that this file is a NegPy-only container.

<details><summary>Second reader's check</summary>

dng.py:199-230 emits no ColorMatrix1 and no AsShotNeutral for a 4-sample LinearRaw file, which the DNG spec requires for non-monochrome images. The module's own argument (dng.py:33-37) against writing a file that 'only one reader in the world accepts' is undercut by this.

</details>

<a id="outputs-out-26"></a>

### OUT-26 -- Mono delivery drops the infrared plane without a note

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/session.py:1072-1073`, `rps7200/export.py:144-145`, `tools/scan.py:272-277`, `rps7200/mono.py:94-99`

An RGBI scan delivered as mono (for example chromogenic B&W scanned as 'negative' with mono ticked) loses its IR plane from the delivered file, and no note says so. The library keeps it.

**Evidence (from the code):**

```text
`delivered = (to_monochrome(turned, ...) if job.get("mono") else turned)` returns (H, W); `_write_infrared`: `if image.ndim != 3 or image.shape[2] <= JPEG_MAX_CHANNELS: return ""`.
```

**Failure scenario:** The operator expects to run dust removal on a C-41 B&W frame, and the delivered TIFF has no IR.

**Fix:** When mono is chosen and IR is present, write the IR as a sidecar, or at least return a note.

<details><summary>Second reader's check</summary>

to_monochrome returns (H, W) and drops channel 3 (mono.py:94-115). export._write_infrared returns '' when image.ndim != 3 (144-145), so no DNG is written and no note is produced. This happens in FrameWriter (session.py:1072-1073), tools/scan.py:272-277 and _deliver_one.

</details>

<a id="outputs-out-v01"></a>

### OUT-V01 -- --bracket with --no-shading merges uncorrected passes, which the merge module says must not happen

**Severity** low · **Category** user-error · **Verdict** found-by-verifier

**Where:** `tools/scan.py:205-218`, `tools/scan.py:254`, `rps7200/bracket.py:26-29`

Nothing refuses or warns about combining --bracket and --no-shading. The merge then runs on raw passes, fusing each pass's column pattern, and the delivered file looks like a normal merged scan.

**Evidence (from the code):**

```text
tools/scan.py passes `shading=not args.no_shading` into scan_bracket and then `merge_bracket([f[..., :3] for f in frames], ratios)` unconditionally. bracket.py:26-29: 'Correct each pass with its own reference and CCD mask first ... because the passes carry different masks and an uncorrected bracket fuses the sensor's column pattern along with the picture.'
```

**Failure scenario:** An operator runs `tools/scan.py --bracket 3 --no-shading` to compare raw pixels. The merged output carries a blend of per-pass column patterns, and it is written to scan.tif with a normal 'bracket:' summary.

**Fix:** Refuse --bracket together with --no-shading in argument parsing, or skip the merge and deliver only the passes.

<a id="outputs-out-v02"></a>

### OUT-V02 -- tools/scan.py keeps every bracket pass (corrected, raw pixels and raw bytes) in memory until the session closes

**Severity** low · **Category** design · **Verdict** found-by-verifier

**Where:** `tools/scan.py:176-194`, `tools/scan.py:205-220`, `rps7200/direct.py:2409-2420`

For each pass the tool holds the corrected frame, the raw pixel array and the capture's raw bytes until close(). merge_bracket then adds full float64 copies (OUT-18). scan_bracket's docstring offers retain=False for exactly this case, but the tool does not use it.

**Evidence (from the code):**

```text
`pending.append(dict(capture, inquiry=info, meta=meta, image=image if raw is None else raw))` for each pass, while scan_bracket is called with the default `retain=True`, so `frames.append(image)` also keeps each corrected pass.
```

**Failure scenario:** A 9-pass 3600 dpi bracket keeps roughly 3 GB resident before the merge starts. On a smaller machine this can raise MemoryError inside on_pass, in the middle of the bracket while the device is open.

**Fix:** Spool each pass to disk in on_pass (as debug filing does), use retain=False, and have the merge read the spooled passes band by band.

<a id="outputs-out-27"></a>

### OUT-27 -- Delivery parameters are not recorded in the library entry

**Severity** info · **Category** library-completeness · **Verdict** confirmed

**Where:** `rps7200/session.py:2111`, `rps7200/library.py:237-261`

From the library you can recover the raw data but not what was actually delivered, or where.

**Evidence (from the code):**

```text
`meta = dict(meta, rotation=turn, flipped=flip)`. The record whitelist has rotation, flipped and reversal, but no mono, mono_channel, format, JPEG quality or delivered paths.
```

**Failure scenario:** You cannot trace a delivered JPEG back to its entry, or regenerate it identically.

**Fix:** Record the delivery (format, quality, mono choice, paths) in the entry, or in a small delivered.json beside it.

<details><summary>Second reader's check</summary>

session.py:2111 adds only rotation and flipped to meta. The library record whitelist (library.py:237-261) holds no delivery format, quality, mono choice or delivered paths.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Delivered picture (TIFF) | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif, <out_dir>/<PRESCAN_SUBDIR>/..., rolls/<roll>/frameNN.tif, rolls/<roll>/prescanNN[-before].tif, Save As/Save all destinations, tools/scan.py --out (default ./scan.tif) | TIFF, classic, little-endian, chunky. tifffile present: deflate (8) + horizontal predictor. tifffile absent: uncompressed. uint16 (scans) or uint8 (prescans). (H,W) mono, (H,W,3) RGB, or (H,W,4) RGB plus 1 ExtraSample 'unspecified' for IR. Software='rps7200'; X/YResolution only when passed (built-in writer defaults to 72 dpi) | corrected (shading), oriented (rotation/flip/reversal), optionally mono | export.write -> tiff.write (session.FrameWriter._write session.py:1079; gui._deliver_one gui.py:3868/3879; tools/scan.py:278; scan_roll dry-run prescans via tiff.write directly) | NegPy; tiff.read in tools/scan_roll.py:243 (read_survey), demo, research tools | Lossless for the corrected array. Not atomic (truncate-then-write). Overwrites the fallback .tif and roll-dir files without checks. dpi missing on Save As / Save all |
| Delivered picture (JPEG) | same names with .jpg (or .jpeg) | JPEG via Pillow, 8-bit from a plain >>8 shift, quality default 95 (GUI clamps to 60-100; CLI unclamped), 4:4:4, optimize; first 3 channels only | corrected, not inverted or stretched | export._write_jpeg | NegPy / viewers | Lossy (8-bit, JPEG). Without Pillow it falls back to a same-stem .tif (overwrites) |
| Infrared companion DNG | <stem>.dng beside a JPEG delivery of a 4-channel pass | Uncompressed LinearRaw (34892) TIFF/DNG 1.4, 4 samples R,G,B,IR, uint16/uint8, ExtraSamples [0,0,0], Make/Model/UniqueCameraModel, Orientation 1, resolution or 72; no ColorMatrix1 | corrected, oriented | export._write_infrared -> dng.write (never raises; failure becomes a note) | NegPy RawpyLoader._peek_linearraw_4ch; tiff.read | Lossless for the corrected array; not atomic; overwrites an existing .dng |
| Library scan.tif | library/<YYYYmmddTHHMMSSZ>_<stock>_[f<frame>_]<dpi>dpi[_ir][-n]/scan.tif | tiff.write (deflate+predictor when tifffile is present), uint16/uint8, (H,W,C); sha256 in scan.json image.sha256 | Meant to be raw. GUI single scans actually hold CORRECTED pixels with corrections_applied [] (OUT-01). Demo prescans and roll frames are also corrected (OUT-14) | library.save (library.py:179); rewritten in place by library.migrate_direction (library.py:573) and tools/library.py migrate-raw (198) | library.load/corrected/reconstruct/verify, demo._decode fallback, GUI 1:1 view and Save As via library.corrected | Pixel-lossless; not atomic |
| Library prescan.tif | library/<id>/prescan.tif | tiff.write, uint8 (H,W,3), no resolution, no checksum | corrected for roll frames (rf.prescan) and unlabelled (OUT-15) | library.save (library.py:181); rewritten in place (row-reversed) by migrate_direction (library.py:622) | migrate_direction, demo._pair_image | Lossless but no raw bytes and no checksum, and in-place rewrites are non-atomic (OUT-09) |
| GUI settings | ./gui-settings.json (relative to CWD), $RPS7200_SETTINGS, demo/gui-settings.json under --demo, --settings; temp gui-settings.json.part | JSON object with sections controls, film, output (str), window, presets, shortcuts, rolls, sheet (per roll folder name: ticks/offsets/rotations/flips/sources/options, int keys stored as strings) | n/a (operator state) | settings.save via ScannerGui._remember (on close, preset save/delete, shortcut change, roll open, sheet state) | settings.load in ScannerGui.__init__; _restore, _clean_sheet_state | Written atomically (.part + replace). Corrupt file silently treated as empty and later overwritten. Save failure (None) silently ignored. Unknown sections dropped on load |
| tools/scan.py delivered sidecar | <out stem>.json (default ./scan.json) | JSON of the last pass's meta (default=str), plus bracket {passes, ratios, stops, stats} and, for mono, mono_channel/channel_order/channels | metadata | tools/scan.py:280-281 | humans | Only record of a bracket merge. Overwritten by the next run with the same --out; not atomic |
| Comparison files | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif, previews/cmp_before.png, previews/cmp_after.png | tiff.write uint16; PNG 8-bit half size | whatever TIFF was given; 2_ is a destripe of it, not shading; 3_ is a percentile-stretched inversion | tools/make_comparison.py | Stefan by eye | Lossless TIFF, but not the product's correction path (OUT-10) |

**Second reader's corrections to this table:**

- **Delivered TIFF, tifffile path, no resolution given.** tifffile writes its own default (XResolution/YResolution 1/1, no absolute unit). It does not simply omit the tags. Either way it differs from the built-in writer's 72 dpi.
- **Roll directory files.** rolls/<roll>/frameNN.tif and prescanNN.tif are written at fixed names without _unclaimed (session.py:1817, 1889). Only the output-folder copy is de-duplicated. Rescanning a frame into the same roll folder (the default name is today's date) silently replaces the earlier delivered frameNN.tif.
- **Library prescan.tif.** For real roll frames it is shading-corrected, uint8 and unlabelled. For GUI and dry-run prescan entries, the prescan is scan.tif itself (raw), and for GUI single scans scan.tif is corrected (OUT-01).
- **Library entry: missing rows.** The table leaves out several files and fields library.save writes that matter for exactness:
  - shading.npz and ccd_mask.bin, with no checksums (verify checks existence only).
  - raw.bin.gz (gzip -6, byte-exact, sha256 over the uncompressed bytes).
  - scan.json, written non-atomically after the data files.
  - The index, rewritten by reindex.
  - The failure mode: a library.save that raises part-way leaves an entry directory with scan.tif but no scan.json.
- **GUI settings.** 'Written atomically' holds only per process: two windows share the same '.part' temp name.
- **Comparison files.** make_comparison writes the three TIFFs before the PNGs, so a missing previews/ directory makes it fail after the TIFFs already exist.
- **tools/scan.py sidecar.** For a bracket it carries metas[-1], the brightest (possibly RGBI) pass's meta. The passes' own bracket_* fields exist only there and in memory, never in the library record.

## What the operator can do

- Choose TIFF or JPEG for the output folder and set JPEG quality. The GUI clamps quality to 60-100; tools/scan.py --quality is unclamped and Pillow clamps it.
- Save As a single pass to any .tif/.tiff/.jpg/.jpeg path, or Save all passes into a folder. Passes that are not yet filed are written from the reduced on-screen copy (at most 1400 px, or 512 for archived results).
- Rotate or flip a pass, which carries over to later passes. Delivered files are turned; the library entry keeps the scanner's orientation.
- Deliver black and white as one plane (average, R, G or B) with --mono/--mono-channel or the GUI control; the library keeps all channels.
- Run tools/scan.py --bracket 2-9 --stops X to take and merge an exposure bracket; every pass is filed in the library before the merge.
- Hand-edit or relocate gui-settings.json (RPS7200_SETTINGS or --settings); the demo uses demo/gui-settings.json.
- Run tools/make_comparison.py on any two TIFFs to write the three comparison files into the current directory.

## What the operator should not do

- Do not point the output folder at a removable, network or nearly full drive during a roll. A failed delivery currently discards the frame's library entry and raw bytes (OUT-02).
- Do not use Save As or Save all on passes from GUI single scans and treat the result as correct: it is double shading-corrected (OUT-01).
- Do not combine --bracket with --ir (OUT-05), and do not trust bracket highlights while saturation is judged on corrected pixels (OUT-04).
- Do not use --no-library with tools/scan.py; the raw bytes and calibration are then gone for good.
- Do not read the histogram panel's 'at/near full scale' figures as sensor saturation; they are measured after shading (OUT-07).
- Do not interrupt `tools/library.py migrate-direction --write`; it rewrites prescan.tif in place and non-atomically (OUT-09).
- Do not run two GUI windows against the same settings file, and do not hand-edit it without a backup (OUT-11).
- Do not keep 'reuse the cached reference' selected across power cycles. It is remembered between launches, while shading is meant to be measured per session.
- Do not judge a correction change by make_comparison output built from a delivered TIFF (OUT-10).

## Mistakes nothing guards against

- tools/scan.py --out with an unsupported extension (.png) is only rejected after the whole scan, leaving no delivered file (OUT-17).
- Running tools/scan.py twice with the default --out overwrites the previous scan.tif and scan.json without warning.
- tools/scan.py --bracket with a single-value --exposure-scale silently scans at 1.0x base exposure (OUT-12).
- Delivering JPEG without Pillow silently overwrites an existing same-stem .tif; a JPEG with IR silently overwrites an existing same-stem .dng (OUT-16).
- Typing an unsupported extension in the Save As dialog raises inside a Tk callback; the window shows nothing and the traceback goes to stderr.
- A syntax error in gui-settings.json silently resets presets, shortcuts and contact-sheet decisions and is written back over the file on quit (OUT-11).
- A hand-edited sheet rotation that is not a quarter turn passes _clean_sheet_state (int cast) and later makes preview.rotate raise inside FrameWriter, losing the library entry (OUT-02).
- Ticking mono on an RGBI scan drops IR from the delivered file without any note (OUT-26).
- Save all of passes not yet filed writes reduced previews under names that carry the full dpi (batch_name uses meta resolution_dpi).

## Dataflow notes

Capture to library and delivery (GUI):
- DirectScanner.scan (direct.py:2423) decodes the pass into raw_pixels (direct.py:2708), applies shading with apply_shading (direct.py:2733; shading.py:196-283: (raw-dark)*gain, floor(x+0.5), clamp to 65535) and returns the corrected image plus meta.
- It also publishes last_pixels_raw (direct.py:2818) and, via capture_record (direct.py:631), the last_raw bytes, shading reference and CCD mask.
- ScanSession passes the result to _deliver (a reduced copy for the window) and to _file (session.py:2032). _file builds the delivered paths with _unclaimed(out_dir/_out_name) plus any roll path, checks the capture layout against the image shape, composes the reversal with the operator's orientation, and submits a job to FrameWriter.
- The FrameWriter thread (session.py:1059) does, in order: preview.orient (mirror, then quarter turn), mono.to_monochrome if mono (floor(mean+0.5) of R,G,B, or one plane), export.write for each path, then library.save(raw_image or image, ...).
- library.save writes scan.tif via tiff.write, prescan.tif, shading.npz, ccd_mask.bin, raw.bin.gz (gzip level 6, sha256) and scan.json (whitelisted meta fields), then reindex.
- _prescan and roll jobs pass raw_image; _scan does not (OUT-01).
- export.write (export.py:158) picks the format from the suffix. TIFF goes to tiff.write: with tifffile installed, tifffile.imwrite with zlib and predictor (tiff.py:133-165); otherwise _write_builtin (tiff.py:171-279), uncompressed with 8 MiB strips, '<' byte order, and pixels written before the IFD. JPEG goes to _write_jpeg (to_8bit >>8, first 3 channels, Pillow q95 4:4:4) and, if more than 3 channels, _write_infrared -> dng.write of R,G,B,IR (dng.py:119). Without Pillow it falls back to tiff.write under the .tif name.

Display: session results give result.image (corrected and reduced), which goes through preview.levels (exact percentiles 0.5/99.5 per channel), preview.render (LUT stretch, invert for display only), preview.to_ppm and then Tk. The histogram and clipping panel (gui.py:3949) measure the same corrected pixels, or library.corrected(entry) at full resolution (gui.py:4110).

Save As / Save all (gui.py:3848): library.corrected(entry) (library.py:338: tiff.read scan.tif, then apply_shading unless labelled) -> orient -> mono -> export.write with no resolution.

tools/scan.py:
- Opens DirectScanner (debug=False) and calls scan or scan_bracket (direct.py:2323: meter once, geometric ladder capped at the timer ceiling, only the last pass RGBI).
- hold() captures last_pixels_raw and capture_record for each pass (scan.py:176-194). After the device closes, it saves library entries (scan.py:238-246).
- Then merge_bracket (bracket.py:257) runs on the corrected frames[..., :3], using a green-only fitted slope and intercept, confidence on corrected values, IVW, residual and misalignment gates, and a uint16 clip. The IR plane of the last pass is dstacked, mono applied, and the result goes through export.write(out) plus the out.json sidecar.

Readback: tiff.read (tiff.py:282) uses tifffile.imread when installed, otherwise _read_builtin (strip/deflate/predictor-2 cumsum in dtype, big-endian converted to native, a trailing length-1 axis squeezed). It is used by library.load, the demo, scan_roll read_survey and the migrate tools.

Settings: settings.load (settings.py:58) at ScannerGui.__init__ (gui.py:377) feeds _restore (Tk vars; bad Tcl types skipped), _clean_sheet_state and presets. _remember (gui.py:666) writes the whole payload through settings.save (settings.py:82: .part then replace) on close and on preset, shortcut, roll or sheet changes.

Console: use_utf8_stdout (console.py:31) reconfigures stdout/stderr to UTF-8 with errors='replace'. It is called at the top of each tools/*.py main and in gui.py:8114, but not by the rps7200 package itself.
