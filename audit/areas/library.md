# The library store

Area key `library`. 28 findings: 1 critical, 8 high, 12 medium, 6 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Library area (rps7200/library.py, tools/library.py, rps7200/uniformity.py, tools/uniformity.py), checked against the executable paths that feed it: DirectScanner.scan/_debug_capture/_debug_flush, ScanSession._file/FrameWriter, tools/scan.py, tools/scan_roll.py, tools/uniformity.py and DemoScanner.



**What one entry contains, byte for byte**

One entry is a directory library/<YYYYMMDDTHHMMSSZ>_<stock-slug|unknown-film>[_f<frame-slug>]_<dpi>dpi[_ir][-N]/. The timestamp is when the entry was filed, not when it was scanned. The directory holds:
- raw.bin.gz (optional). gzip level 6 of the exact concatenation of the image pass's SCSI READ payloads: every line is a 2-byte channel tag plus bytes_per_line, in the order received (including bottom-up order). This part is truly byte-exact. The transport returns full windows and nothing is padded or trimmed. The record holds the sha256 of the uncompressed bytes and a layout dict.
- scan.tif. The raw pixels after decode_index, turned upright. Three exceptions:
  - A GUI single Scan files the corrected pixels here.
  - At 7200 dpi the image is realigned for the column stagger, with 4 rows trimmed.
  - Legacy entries may still be stored the way the pass was read.
  The write uses tifffile deflate+predictor or an uncompressed built-in writer; both are lossless.
- shading.npz (optional). The derived ShadingReference (float64 per-column light/dark means) from calculate_shading. The calibration bytes themselves are never kept.
- ccd_mask.bin (optional). The exact GET CCD MASK bytes of the last pass.
- prescan.tif (optional). The corrected 8-bit prescan as delivered. It has no bytes, mask, resolution or checksum of its own.
- scan.json. A whitelisted subset of meta, written last and non-atomically.
- index.json, at the library root. A derived summary, rewritten after every save.


**Is it exact? re-derivable?**

The raw bytes are exact, but several paths break the promise that "the library holds raw pixels + everything needed":
1. The GUI single Scan files corrected pixels labelled raw. Save As and the full-resolution view then correct them a second time.
2. Debug filing pairs a pass with the previous pass's raw bytes whenever keep_raw=False.
3. The calibration bytes, the prescan's bytes, the metering probes, the hold-loop passes, INQUIRY, byte14/slide_init overrides, bracket/roll/uniformity fields and the capture time are never stored.
4. The 7200 dpi realignment is baked in and not recorded, so reconstruct cries wolf on those entries.
5. FrameWriter writes the delivered copies before the library entry. A failing output folder therefore loses the raw bytes.


**Robustness**

- Saving is not atomic. There is no temp+rename, and scan.json is written last. A crash leaves an orphan directory that entries(), verify and reconstruct silently ignore.
- The collision check (-2/-3 suffixes) is a TOCTOU check on scan.json. In practice it is safe, because each process files from a single thread.
- corrections= is never passed by any production caller.
- reconstruct compares bit-exactly (np.array_equal after a shape check). However, it reports corrupt raw bytes as "no raw bytes", aborts on ScanReadError or a missing scan.tif, and never re-applies the 7200 realignment.
- verify cannot tell a deliberately raw entry from a shortfall. It labels the explicit sentinel "correction was asked for".
- duplicates --delete keys on operator-typed notes, so with empty notes it deletes different photographs. It also collapses byte14 and gain ladders.
- migrate-raw --write treats every mismatch as "mislabelled". It can launder a decode regression, and it can destroy the only corrected rendition or the only copy of a pass's pixels.


**Demo**

The DemoScanner files into demo/library and diverges from the real scanner in several ways:
- It has no last_pixels_raw, so prescans are filed corrected.
- It reuses stale reference/mask values from an earlier decode.
- It ignores shading=False.
- It does not refuse 7200 dpi.
- Its "demo" flag is dropped by the whitelist.


**Uniformity**

- rps7200/uniformity.py is pure analysis and has no persistence defects.
- tools/uniformity.py drops its session id (the whitelist removes it).
- analyse includes passes that were rejected at capture time.
- It prints a wrong provenance key.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [LIB-01](#library-lib-01) | critical | data-integrity | GUI single Scan files the shading-corrected image as raw; Save As / full-res view then correct it a second time | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [LIB-02](#library-lib-02) | high | data-integrity | Debug filing pairs a pass with the PREVIOUS pass's raw bytes (or none) whenever keep_raw=False | [P02](../problems/P02-debug-filing-stale-raw-bytes.md) |
| [LIB-03](#library-lib-03) | high | error-handling | FrameWriter writes delivered copies before the library entry; any export failure loses the raw bytes | [P04](../problems/P04-delivered-copy-before-library-entry.md) |
| [LIB-04](#library-lib-04) | high | data-integrity | Shading reference is stored only as a derived product; the calibration bytes and calibration state are never kept | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [LIB-05](#library-lib-05) | high | data-integrity | prescan.tif in a frame entry is the corrected prescan with no bytes, mask, resolution or meta; real-roll prescan bytes are never filed | [P08](../problems/P08-passes-never-filed.md) |
| [LIB-06](#library-lib-06) | high | data-integrity | library.save's fixed key whitelist silently drops meta and parameters: bracket, roll, uniformity session, demo flag, inquiry; command overrides never reach meta | [P09](../problems/P09-record-missing-parameters.md) |
| [LIB-07](#library-lib-07) | high | user-error | duplicates --delete treats different photographs (empty notes) and deliberate ladders as interchangeable | [P11](../problems/P11-duplicates-delete-destroys-scans.md) |
| [LIB-15](#library-lib-15) | high | user-error | migrate-raw --write treats every mismatch as 'mislabelled corrected pixels': it can launder a decode regression, destroy the only corrected rendition, or overwrite a pass's only pixels | [P12](../problems/P12-library-maintenance-tools.md) |
| [LIB-A1](#library-lib-a1) | high | doc-mismatch | No caller ever passes corrections=; every fallback that files corrected pixels labels them raw | [P01](../problems/P01-gui-scan-files-corrected-as-raw.md) |
| [LIB-08](#library-lib-08) | medium | doc-mismatch | verify cannot tell a deliberate raw scan from a shortfall; it labels the explicit sentinel 'correction was asked for' | -- |
| [LIB-09](#library-lib-09) | medium | data-integrity | 7200 dpi stagger realignment is baked into scan.tif, unrecorded, and not reproduced by reconstruct/decode_raw/migrate-raw; sign ignores read direction | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [LIB-10](#library-lib-10) | medium | data-integrity | Entries are written non-atomically; a crash leaves an orphan directory that verify and reconstruct never see, and the collision check can reuse it | [P10](../problems/P10-non-atomic-writes.md) |
| [LIB-11](#library-lib-11) | medium | hardware-safety | GUI single-scan and prescan filing gzips with the device open and idle, contrary to the documented wedge precaution | [P13](../problems/P13-gzip-with-device-open.md) |
| [LIB-12](#library-lib-12) | medium | data-integrity | The session's layout guard drops the raw bytes of any truncated (short-read) pass | [P07](../problems/P07-failed-and-short-passes-lose-bytes.md) |
| [LIB-13](#library-lib-13) | medium | data-integrity | Metering evidence and 'metered' flag are lost for roll frames and brackets | [P09](../problems/P09-record-missing-parameters.md) |
| [LIB-16](#library-lib-16) | medium | bug | Tools locate entries by record['id'], not by their directory; copied or renamed entries break verify/reconstruct/migrate-raw/duplicates | [P10](../problems/P10-non-atomic-writes.md) |
| [LIB-17](#library-lib-17) | medium | doc-mismatch | Metering probes, hold/aim passes and tools/scan_roll.py dry-run prescans are never filed outside debug mode | -- |
| [LIB-18](#library-lib-18) | medium | demo-divergence | Demo stand-in diverges in what it files: no last_pixels_raw, stale reference/mask on prescans, ignores shading=False, no 7200 dpi refusal, demo flag dropped | [P26](../problems/P26-demo-files-corrected-as-raw.md) |
| [LIB-22](#library-lib-22) | medium | bug | tools/uniformity.py: analyse includes passes rejected at capture; the session id is dropped; provenance key typo | -- |
| [LIB-23](#library-lib-23) | medium | data-integrity | Debug spool is unrecoverable if the process dies before close(), and lives in system temp (possibly tmpfs/RAM) | [P03](../problems/P03-debug-spool-not-crash-safe.md) |
| [LIB-A2](#library-lib-a2) | medium | data-integrity | Entry does not record where its shading reference came from (reused file vs. calibrated this session) or when | [P05](../problems/P05-calibration-not-stored-exactly.md) |
| [LIB-14](#library-lib-14) | low | error-handling | reconstruct misreports a corrupt raw.bin.gz as 'no raw bytes' and aborts the whole run on ScanReadError or a missing scan.tif | -- |
| [LIB-19](#library-lib-19) | low | error-handling | corrected() silently maps columns 1:1 when the CCD mask is missing and ignores any correction label other than 'shading' | -- |
| [LIB-20](#library-lib-20) | low | data-integrity | Entry id and 'created' are the filing time, not the capture time; debug's captured timestamp is discarded | -- |
| [LIB-21](#library-lib-21) | low | data-integrity | verify has no integrity check for shading.npz, ccd_mask.bin or prescan.tif, and skips raw checks when scan.tif is missing | -- |
| [LIB-24](#library-lib-24) | low | user-error | duplicates --keep accepts 0 and negative values | -- |
| [LIB-25](#library-lib-25) | low | user-error | Library root is relative to the working directory, and verify/reconstruct/index cover only one directory level | -- |
| [LIB-26](#library-lib-26) | info | doc-mismatch | README and library.py give contradictory gzip ratios for raw.bin.gz | -- |

## Findings in full

<a id="library-lib-01"></a>

### LIB-01 -- GUI single Scan files the shading-corrected image as raw; Save As / full-res view then correct it a second time

**Severity** critical · **Category** data-integrity · **Verdict** confirmed · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/session.py:1440-1458`, `rps7200/session.py:1085-1100`, `rps7200/library.py:228`, `rps7200/library.py:373-391`, `tools/gui.py:3864`, `tools/gui.py:4110`

The window's Scan job is the only filing path that does not pass raw_image. Its entry therefore stores the corrected image with meta['shading'] set and corrections_applied empty, which is exactly the '26 mislabelled prescans' failure CLAUDE.md:114-133 describes, now on every GUI single scan. Save As and the full-resolution view call library.corrected(), which applies the per-column gain a second time and reports 'applied'. reconstruct compares a raw decode with corrected pixels and reports 'decode CHANGED' on every such entry, false alarms that bury real regressions. The tests miss it because FakeScanner.scan returns a single array (tests/test_session.py:54-134), and test_demo's reconstruct tests use entries with no reference.

**Evidence (from the code):**

```text
session.py:1456 `self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",), mono=wants_mono(job.mono, job.film), mono_channel=job.mono_channel)` -- no raw_image, although scan() returns the corrected image and keeps the raw one in last_pixels_raw (direct.py:2708-2733, 2818). FrameWriter._write session.py:1089-1091 `raw_image = job.get("raw_image")` / `entry = library.save(job["image"] if raw_image is None else raw_image, ...)` and no `corrections=` is passed, so library.py:228 records `"corrections_applied": list(corrections or [])` == []. library.corrected() then does `image, report = apply_shading(image, record["reference"], record["ccd_mask"])` (library.py:388) on already-corrected pixels; gui.py:3864 `full, entry_record = library.corrected(result.entry)` (Save As) and gui.py:4110 (1:1 view).
```

**Failure scenario:** The operator clicks Scan in the window at 1800 dpi with a calibrated session. The entry's scan.tif holds flat-fielded pixels labelled raw. Save As writes a file whose columns carry gain squared (over-corrected stripes), and the log says nothing unusual. `make reconstruct` flags the entry as a changed decode. The same happens in --demo whenever the source entry has a reference.

**Fix:** Pass `raw_image=getattr(self._scanner, 'last_pixels_raw', None)` in ScanSession._scan exactly as _prescan does. Make FrameWriter refuse (or pass corrections=['shading']) when raw_image is None and meta['shading'] is set. Add a session test with a scanner whose returned image differs from last_pixels_raw. Run `tools/library.py migrate-raw` on existing GUI entries.

<details><summary>Second reader's check</summary>

session.py:1456 `self._file(seq, 0, image, meta, ...)` passes no raw_image, while `_prescan` (1415) and the roll paths (1830, 1886) do. DirectScanner.scan applies shading at direct.py:2728 and returns the corrected image, keeping the uncorrected one only in last_pixels_raw (2818). FrameWriter._write (1089-1091) therefore saves job['image'], the corrected pixels, with no corrections= argument, so corrections_applied is []. meta['shading'] is still set, so calibration.report is filled. library.corrected (373-391) finds no 'shading' in applied and no skipped value, and calls apply_shading a second time. Save As (gui.py:3864) and the 1:1 view (gui.py:4110) both go through corrected(). The capture's raw bytes are this pass's own (keep_raw=True at 1449), so reconstruct decodes raw and compares it with corrected pixels, which gives 'decode CHANGED'. tools/scan.py:184-194 and tools/scan_roll.py:555-558 both have comments describing exactly this bug and fixing it for their own call sites. The GUI single-scan path was missed.

</details>

<a id="library-lib-02"></a>

### LIB-02 -- Debug filing pairs a pass with the PREVIOUS pass's raw bytes (or none) whenever keep_raw=False

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P02](../problems/P02-debug-filing-stale-raw-bytes.md)

**Where:** `rps7200/direct.py:650-692`, `rps7200/direct.py:631-646`, `rps7200/direct.py:1518-1532`, `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2437`, `tools/byte14_probe.py:182-185`, `tools/verify_protocol.py:47-53`

In debug mode a pass taken with keep_raw=False is filed with whatever last_raw holds from an earlier keep_raw=True pass, or with no bytes at all. The common trigger is any metered scan with keep_raw=False: its own auto_exposure probes kept raw at 300 dpi, so the main pass's entry carries the probe's bytes and layout. The worst case has the same shape (byte14_probe's final pass), where nothing, including reconstruct's shape check, can tell the bytes are wrong.

**Evidence (from the code):**

```text
_debug_capture: `record = self.capture_record()` ... `raw = record.get("raw")` / `if raw is not None: ... raw_path.write_bytes(raw)` / `item["raw_layout"] = record.get("raw_layout")`. capture_record returns `"raw": self.last_raw`, and last_raw is only assigned in read_planes `if keep_raw: self.last_raw = blob` and is never cleared. scan() and prescan() default `keep_raw: bool = False`. byte14_probe.py:182 final pass: `scanner.scan(..., shading=False, keep_raw=False, byte14=None)` after a ladder of keep_raw=True passes; verify_protocol.py `img, _ = s.scan(resolution=DPI, ..., shading=False, require_media=False, **kw)` never keeps raw.
```

**Failure scenario:** `RPS7200_DEBUG=1 uv run python tools/byte14_probe.py`. The final 0x10 pass is filed with the 0x31 ladder pass's raw.bin.gz. Both are the same shape, so nothing notices. reconstruct reports 'decode CHANGED: N samples differ'. `migrate-raw --write` then classifies it 'mislabelled' and overwrites scan.tif with the 0x31 pass's decode, destroying the only copy of the final pass. The demo decodes raw first, so it shows the wrong picture for that entry.

**Fix:** Clear last_raw/last_raw_layout at the start of every scan() (or set them from every read_planes and drop keep_raw). In _debug_capture, attach raw only when it was produced by this pass (e.g. a per-pass sequence number or identity check). Record lines/width/channels from meta and refuse a mismatch as _file does.

<details><summary>Second reader's check</summary>

last_raw is assigned only at direct.py:1521 under `if keep_raw`, and nothing ever clears it (grep: the only other assignment is in __init__). _debug_capture (650-692) takes capture_record()['raw'] with no check that it belongs to this pass. There is no shape guard of the kind ScanSession._file has. Two real paths lead here. (1) byte14_probe.py:182-185 runs a keep_raw=False final pass after keep_raw=True ladder passes, so the entries have the same shape and the mismatch cannot be detected. (2) A scan(auto_exposure=True, keep_raw=False) under RPS7200_DEBUG=1: the auto_exposure probes run with keep_raw=True (2091), so the main pass is filed with the last 300 dpi probe's bytes. verify_protocol, transport_probe and filing_load_test never keep raw, so their entries get none, or stale bytes if a metering pass ran earlier. Severity is lowered to high because the path is reachable only in debug-filed ad-hoc scripts, not in the GUI or tools. It is still silent corruption of the ground truth, and migrate-raw --write would overwrite the genuine pixels in the same-shape case.

</details>

<a id="library-lib-03"></a>

### LIB-03 -- FrameWriter writes delivered copies before the library entry; any export failure loses the raw bytes

**Severity** high · **Category** error-handling · **Verdict** confirmed · **Problem** [P04](../problems/P04-delivered-copy-before-library-entry.md)

**Where:** `rps7200/session.py:1060-1103`, `rps7200/session.py:1046-1058`, `rps7200/session.py:2067-2071`, `tools/gui.py:644-645`, `tools/scan_roll.py:542-580`

The library entry is the irreplaceable record, and the raw bytes live only in the job's in-memory capture. The output-folder copy, the roll's frameNN.tif, mono conversion and JPEG export all run first. If any of them raises, library.save is never reached, the capture is garbage-collected and the scan's raw bytes, reference and mask are gone. Examples: a remembered output folder on an unplugged drive, permission denied, a full output disk, or an exception in to_monochrome.

**Evidence (from the code):**

```text
`for path in job.get("paths") or (): Path(path).parent.mkdir(parents=True, exist_ok=True); note = export.write(str(path), delivered, ...)` precedes `entry = None / if job["library"]: ... entry = library.save(...)`. _run: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")` and the job is dropped. The output folder is remembered across launches (gui.py:644 `if self.remembered["output"] and self.session.out_dir is None: self._set_outdir(...)`).
```

**Failure scenario:** The GUI remembers an output folder on E:\ (a USB disk). Stefan scans a 38-frame roll with the disk unplugged. Every frame fails with a mkdir/OSError. The log says 'picture N could not be filed' and not one library entry exists, although every pass completed.

**Fix:** Call library.save first (inside its own try) and the delivered copies afterwards. Report the two failures separately, so a lost export never costs the entry.

<details><summary>Second reader's check</summary>

session.py:1066-1082 runs preview.orient, to_monochrome and, for each path, mkdir plus export.write before `if job['library']: ... library.save` at 1085. Any exception in those steps propagates to _run (1049-1058), which records the error and drops the job. The capture dict (raw bytes, reference, mask) is lost with it. For a roll, paths includes the roll directory's frameNN.tif and the output folder copy (_file 2082-2086). A full disk, a permission error or a missing drive on the remembered output folder therefore costs the library entry of every frame.

</details>

<a id="library-lib-04"></a>

### LIB-04 -- Shading reference is stored only as a derived product; the calibration bytes and calibration state are never kept

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:1755-1760`, `rps7200/direct.py:1946-1973`, `rps7200/shading.py:75-91`, `rps7200/shading.py:109-182`, `rps7200/library.py:182-183`, `rps7200/library.py:281-296`

calculate_shading is host-side and unsettled: it applies a level-based dark/light split (split_ratio=5.0, cut at the widest ratio gap) and plain row means. Its 1.66 MB input is discarded, so a change to how the dark phase is separated, how lines are averaged or how outliers are rejected can never be applied to any stored scan. The same is true of the calibration context:
- calibration resolution;
- gain/offset written before the pass;
- the 128-byte calibration-info block;
- the shading descriptor;
- the calibration pass's own CCD mask;
- the calibration duration and time.

This contradicts the library docstring's premise that everything but raw.bin.gz is derived and re-derivable (library.py:21-26).

**Evidence (from the code):**

```text
calibrate_shading(..., keep_data: bool = False): `data = b"".join(collected)` / `self._shading = calculate_shading(data, width)` / `"data": data if keep_data else None`. No caller passes keep_data=True (grep). ShadingReference.save stores only `ref{c}`, `mean{c}`, `dark{c}`, `darkmean{c}`, pixels_per_line. library.save: `if reference is not None: reference.save(path / "shading.npz")`.
```

**Failure scenario:** A better dark/light separation is written next month. Every library entry still holds the old float64 means, and none can be re-corrected from calibration data. The vignette and shading studies cannot be re-run against a new reference model.

**Fix:** Keep the calibration bytes (gzip, ~1.7 MB, once per session) in a content-addressed calibration store referenced by each entry, or in each entry as shading_raw.bin.gz, together with the descriptor, calibration info, mask, resolution and gain/offset. Record the calculate_shading version or parameters in scan.json.

<details><summary>Second reader's check</summary>

calibrate_shading (direct.py:1755-1973) joins the calibration bytes, calls calculate_shading(data, width), and returns data only when keep_data=True. grep finds no caller that passes keep_data. ShadingReference.save (shading.py:75-91) stores only the derived ref/mean/dark/darkmean arrays. library.save writes only reference.save. calculate_shading has tunable host-side logic (split_ratio=5.0, level-based split), so the reference cannot be re-derived when that logic changes. The calibration's own mask, descriptor and resolution are not recorded in the entry either. This contradicts library.py:21-26 'raw.bin.gz is the ground truth and everything else is derived from it': shading.npz is derived from bytes that are not kept.

</details>

<a id="library-lib-05"></a>

### LIB-05 -- prescan.tif in a frame entry is the corrected prescan with no bytes, mask, resolution or meta; real-roll prescan bytes are never filed

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:1883-1893`, `rps7200/direct.py:3494-3498`, `rps7200/direct.py:3656-3660`, `rps7200/library.py:180-181`, `rps7200/library.py:297-303`, `tools/scan_roll.py:562-565`

Only a dry run files prescans as their own entries. On a real roll, the prescan's raw bytes, its per-pass CCD mask, its meta and its shading report are overwritten by the hold/aim passes, the probes and the frame scan, and are lost. The corrected 8-bit picture stored beside the frame is 'as delivered' (research/frame-edge/build_dataset.py:94 treats it so), cannot be re-corrected, and carries no label saying it is corrected, which contradicts CLAUDE.md:114 'The library holds raw pixels'. migrate_direction may also rewrite it in place (library.py:621-623).

**Evidence (from the code):**

```text
session.py:1883 `self._file(seq, number, rf.image, frame_meta, notes, ..., raw_image=rf.raw_image, prescan=rf.prescan, prescan_meta=rf.prescan_meta, ...)`, where rf.prescan is the corrected return of `prescan_image, _ = self.prescan(...)` (direct.py:3494) and rf.raw_prescan is ignored. library.py:181 `tiff.write(str(path / "prescan.tif"), prescan)` (no resolution). The record keeps only `"file": "prescan.tif", "read_direction": ..., "carriage_state": ...`.
```

**Failure scenario:** A shading or decode improvement is made. `library.corrected()` and `reconstruct` cover the frame, but the 114 roll prescans the frame-edge detector was trained on stay frozen at the old correction. A prescan whose read direction was misjudged cannot be re-read from its line tags, because it has none.

**Fix:** Carry rf.raw_prescan plus the prescan's capture_record (bytes, layout, mask), taken immediately after the prescan in scan_roll. Store them as prescan.raw.bin.gz and prescan_ccd_mask.bin with a prescan block that holds resolution, sha256, corrections_applied and the shading report. At minimum, label prescan.tif 'corrected' in the record.

<details><summary>Second reader's check</summary>

On a real roll the session files the frame with `prescan=rf.prescan` (session.py:1888). rf.prescan is the corrected return of self.prescan() (direct.py:3493, which runs shading=True). rf.raw_prescan is used only in the dry-run branch (1830). tools/scan_roll.py:562 does the same. library.save:180-181 writes prescan.tif with no resolution. The record's prescan block (297-303) holds only file, read_direction and carriage_state: no raw bytes, mask, meta, sha256 or corrections label. prescan_before is written with file_entry=False (session.py:1850), so its bytes are also lost outside debug mode.

</details>

<a id="library-lib-06"></a>

### LIB-06 -- library.save's fixed key whitelist silently drops meta and parameters: bracket, roll, uniformity session, demo flag, inquiry; command overrides never reach meta

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:131-147`, `rps7200/library.py:237-264`, `rps7200/direct.py:2402-2405`, `rps7200/direct.py:3653-3654`, `tools/uniformity.py:608`, `rps7200/demo.py:591`, `rps7200/direct.py:2437-2441`, `rps7200/direct.py:2636-2647`, `rps7200/direct.py:728`, `rps7200/session.py:1098`, `tests/test_library.py:388-420`

The record is built from a list rather than from what scan and the tools recorded, so anything new is dropped without error. That is the failure that already cost `metering` and `filter_offsets` (tests/test_library.py:388). The losses today:
- bracket membership and its ladder ratio;
- the roll index and transport position;
- which uniformity phase or session a pass belongs to;
- firmware, model and max resolution (INQUIRY, which three callers pass in);
- the demo provenance flag;
- per-pass protocol overrides (byte14, slide_init_param, skip_shading), which change what the device did while protocol_revision stays the same.

These cannot be re-derived later.

**Evidence (from the code):**

```text
`"scan": {k: meta.get(k) for k in ("resolution_dpi", "frame", ..., "fast_infrared", "filter_offsets")}`. Keys set elsewhere and lost: `meta["bracket_index"] = i` / `bracket_ratio` / `bracket_passes` / `bracket_stops`; `meta["roll_index"] = index` / `meta["roll_position"] = position`; `meta["uniformity_session"] = session`; demo `"demo": True`. The `inquiry: Any = None` parameter is accepted and never written. `byte14`, `slide_init_param` and `skip_shading` are scan() arguments passed to set_mode/slide but are absent from meta (direct.py:2760-2804). The test only checks a hand-built meta.
```

**Failure scenario:** The byte14 ladder or verify_protocol stages 3-5, filed via debug mode, produce entries that do not say which byte14 or SLIDE INIT param each pass used. A firmware difference between two sessions cannot be attributed. Demo entries copied into a real library look like real scans.

**Fix:** Store meta verbatim (e.g. scan.json 'meta' block with a JSON-safe copy) in addition to the curated view, and write the inquiry fields. Put byte14, slide_init_param, skip_shading and the MODE SELECT/SET SCAN FRAME payload hex into scan() meta. Extend the whitelist test to use DirectScanner.scan's real meta plus the bracket and roll additions.

<details><summary>Second reader's check</summary>

The 'scan' block is a fixed comprehension over a key list (library.py:237-264). The following are dropped: bracket_index, bracket_ratio, bracket_passes and bracket_stops (direct.py:2402-2405); roll_index and roll_position (3653-3654); uniformity_session (tools/uniformity.py:608); demo True (demo.py:475, 591); and tools/scan.py's meta['bracket'] (262), although that one is added after filing anyway. The `inquiry` parameter is accepted and never referenced in save. scan()'s meta dict (2760-2804) has no byte14, slide_init_param or skip_shading, although scan() takes each as an argument and sends it (set_mode 2633-2641, slide 2645). In addition, debug capture snapshots meta inside scan() (2811), so later additions such as registration and roll_index never reach debug entries either.

</details>

<a id="library-lib-07"></a>

### LIB-07 -- duplicates --delete treats different photographs (empty notes) and deliberate ladders as interchangeable

**Severity** high · **Category** user-error · **Verdict** confirmed · **Problem** [P11](../problems/P11-duplicates-delete-destroys-scans.md)

**Where:** `rps7200/library.py:639-698`, `rps7200/library.py:701-738`, `tools/library.py:115-135`, `rps7200/direct.py:720-723`, `tools/gui.py:1724-1731`, `tools/scan.py:113-114`

The 'picture' part of the signature is only the operator-typed stock/frame/subject. Several kinds of entry collapse to a single signature:
- every metered GUI single scan with an empty Frame field at one dpi and channel count;
- every GUI prescan;
- every debug-filed probe or hold prescan at 300 dpi;
- every pass of a byte14 ladder (byte14 is not recorded);
- every pass of a gain ladder (gain is not in the signature).

prunable keeps the newest and --delete removes the rest with rmtree, including raw bytes of different frames. Tags such as 'rejected', 'blue-clipped' and 'not-a-reference', which TODO.md:329-332 says must not be pruned, are not consulted.

**Evidence (from the code):**

```text
signature = `(stock, frame, subject, resolution_dpi, channels, tuple(frame), depth, film, protocol_revision, commanded, fast_infrared)` where `commanded = None` for metered scans. Debug entries use `film=FilmNotes(notes="captured with RPS7200_DEBUG on")` (stock/frame/subject empty). GUI notes come from free-text fields that default empty. tools/library.py:128-129 `if args.delete: shutil.rmtree(path)`.
```

**Failure scenario:** Stefan scans ten different frames from the window at 1800 dpi with auto-exposure and leaves Frame blank. `tools/library.py duplicates` reports nine as 'same scan of the same picture'. With --delete, nine different photographs and their raw bytes are removed irreversibly.

**Fix:** Include a picture identity that does not depend on typing: transport position/roll index, a raw sha256 or a pixel signature (demo.picture_signature exists). Add gain and byte14 to the signature. Never offer entries whose notes are all empty or that carry protective tags. Require --delete to name the ids.

<details><summary>Second reader's check</summary>

signature() (library.py:676-698) identifies the picture only by the typed stock, frame and subject. gui._notes (gui.py:1724-1731) reads free-text fields, and debug entries use FilmNotes(notes=...) with frame empty (direct.py:720-723). GUI metered single scans at one dpi, channel count and window get commanded=None and collapse into one group. GUI prescans all have exposure_scale 1.0, commanded, and collapse too. Gain and byte14 are not in the signature. prunable (701-738) ignores tags. tools/library.py:128-129 calls rmtree. Distinct photographs with their raw bytes can therefore be deleted.

</details>

<a id="library-lib-15"></a>

### LIB-15 -- migrate-raw --write treats every mismatch as 'mislabelled corrected pixels': it can launder a decode regression, destroy the only corrected rendition, or overwrite a pass's only pixels

**Severity** high · **Category** user-error · **Verdict** confirmed · **Problem** [P12](../problems/P12-library-maintenance-tools.md)

**Where:** `tools/library.py:137-216`, `rps7200/library.py:479-491`

migrate-raw assumes any difference between stored and decoded means 'corrected pixels'. Three things break that assumption:
- (a) After a decode regression, --write replaces every scan.tif with the buggy decode and labels it raw. The regression baseline is destroyed and reconstruct then reports 'identical'.
- (b) A legacy entry labelled 'shading' whose shading.npz is missing loses its only corrected rendition. It becomes raw with no reference, and corrected() returns 'no reference'.
- (c) A debug entry with stale raw bytes (LIB-02) has its genuine pixels overwritten by another pass's decode.

CLAUDE.md:131-133 directs using this tool on legacy entries.

**Evidence (from the code):**

```text
`if np.array_equal(plain, stored) and not applied: continue` / `planned.append((path, plain, applied, stored_shape))`, then `tiff.write(str(path / "scan.tif"), plain, ...)` and `image["corrections_applied"] = []`. No check is made that the entry has a reference, that meta['shading'] shows a correction ran, or that reconstruct's verdict was a known category. The shading-labelled 'reference is missing' verdict still returns an image, so it proceeds.
```

**Failure scenario:** A developer changes decode_index, runs `make reconstruct`, sees 'decode CHANGED' on many entries, reads CLAUDE.md, and runs `migrate-raw --write` to 'fix' them. The library now matches the broken decode, and the evidence that anything changed is gone.

**Fix:** Plan a rewrite only when the stored image equals apply_shading(plain, stored reference, mask) (i.e. it is provably the corrected form of these bytes) or equals a known transform. Refuse when the reference is missing. Keep the replaced scan.tif as scan.pre-migrate.tif. Print reconstruct's verdict per entry.

<details><summary>Second reader's check</summary>

migrate-raw (tools/library.py:156-212) plans a rewrite for every entry where decode_raw has the same shape and is not equal (or where applied is non-empty). It ignores reconstruct's verdict, and it never checks that the stored image equals apply_shading(plain, ref, mask) or that a reference exists. Case (b) is reachable: reconstruct returns the decoded image even when its verdict is 'reference is missing' (473-477). The tool's comment says 'Both are repaired ... without guessing', but the code assumes that any difference means correction. A decode regression, a legacy entry without its reference, or a stale-raw debug entry would each have scan.tif irreversibly replaced, with no backup kept.

</details>

<a id="library-lib-a1"></a>

### LIB-A1 -- No caller ever passes corrections=; every fallback that files corrected pixels labels them raw

**Severity** high · **Category** doc-mismatch · **Verdict** found-by-verifier · **Problem** [P01](../problems/P01-gui-scan-files-corrected-as-raw.md)

**Where:** `rps7200/library.py:147`, `rps7200/library.py:228`, `rps7200/session.py:1088-1092`, `rps7200/session.py:2111-2120`, `rps7200/session.py:1415`, `rps7200/demo.py:455-477`

CLAUDE.md says '`corrections=` is how a caller admits it is handing over something else', and library.save's docstring says the same. In the code, every path that falls back to the delivered (corrected) image files it with corrections_applied=[]:
- FrameWriter whenever raw_image is None: GUI single scans (LIB-01), and demo prescans, because DemoScanner has no last_pixels_raw.
- session._file's shape-mismatch fallback, which files corrected pixels deliberately.

The label mechanism exists and is simply never used. corrected() then shades these entries twice, and reconstruct reports them as decode changes.

**Evidence (from the code):**

```text
grep -rn 'corrections=' over rps7200/, tools/ and research/ finds no call site. FrameWriter: `raw_image = job.get("raw_image")` / `library.save(job["image"] if raw_image is None else raw_image, ...)` with no corrections. session._file: `if raw_image is not None and raw_image.shape != image.shape: ... "filing the corrected pixels instead"); raw_image = None` with the comment `better to file the corrected pixels and have reconstruct say so`.
```

**Failure scenario:** A roll frame whose raw_image shape disagrees with image hits the fallback. The log says 'filing the corrected pixels instead', but the entry says raw. Save As applies shading again and writes visibly over-corrected columns.

**Fix:** In FrameWriter, when raw_image is None and meta['shading'] is truthy, pass corrections=['shading'] (or refuse to file). In session._file's fallback, set a flag that FrameWriter turns into corrections=['shading']. Add a test asserting that no filing path produces meta.shading != None together with corrections_applied == [] unless the stored pixels equal the plain decode.

<a id="library-lib-08"></a>

### LIB-08 -- verify cannot tell a deliberate raw scan from a shortfall; it labels the explicit sentinel 'correction was asked for'

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/library.py:794-802`, `rps7200/direct.py:2757-2758`, `rps7200/direct.py:288-303`, `rps7200/library.py:359-364`, `tests/test_library.py:165-170`, `tools/scan.py:164-168`

Every deliberately raw entry without a reference counts as a problem, for example `tools/scan.py --no-shading` (ensure_shading(skip=True) leaves no reference) or a 7200 dpi pass, which can only be taken with shading=False. verify then prints the explicit sentinel as 'correction was asked for: shading=False (explicit)', the opposite of the truth. It also exits 1, so `make verify` stays permanently red (TODO.md:64-82 already reports 16 standing problems).

**Evidence (from the code):**

```text
verify: `if not cal.get("shading"): why = cal.get("skipped"); problems.append(f"...can never be corrected" + (f" -- correction was asked for: {why}" if why else ""))`. scan() with shading=False sets `shading_skipped = SHADING_SKIPPED_EXPLICIT` (= "shading=False (explicit)"). The test for the deliberate case saves meta WITHOUT shading_skipped, so it never exercises the sentinel.
```

**Failure scenario:** After a `--no-shading` comparison scan, `make verify` prints '... can never be corrected -- correction was asked for: shading=False (explicit)' and fails. A real regression reported alongside it goes unnoticed because verify is always red.

**Fix:** In verify, compare `skipped == SHADING_SKIPPED_EXPLICIT` and report such entries as informational (or not at all). Only other reasons should count as problems. Add a test that saves with the real sentinel. Support an explicit 'deliberately-uncalibrated' tag as TODO.md suggests.

<details><summary>Second reader's check</summary>

verify (library.py:794-802) appends a problem for every entry without a shading reference, and prints `correction was asked for: {why}` for any non-empty skipped value, including SHADING_SKIPPED_EXPLICIT ('shading=False (explicit)', direct.py:303, 2758). The corrected() docstring (library.py:357-359) says '`verify` already draws that line', which verify does not do for the sentinel. The test at tests/test_library.py:163-168 saves with no shading_skipped at all, so the sentinel case is never exercised against verify. tools/scan.py --no-shading reaches this path (ensure_shading skip leaves no reference, and scan(shading=False) sets the sentinel). Severity is medium, not high: it is a reporting defect that keeps verify red and inverts the message. No data is lost.

</details>

<a id="library-lib-09"></a>

### LIB-09 -- 7200 dpi stagger realignment is baked into scan.tif, unrecorded, and not reproduced by reconstruct/decode_raw/migrate-raw; sign ignores read direction

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `rps7200/direct.py:2695-2708`, `rps7200/direct.py:1637-1664`, `rps7200/library.py:493-497`, `tools/library.py:175-178`, `tools/scan.py:218-227`

At 7200 dpi scan.tif holds the stagger-realigned image (H-4 rows), and the entry has no record of that transform or its constant. reconstruct therefore reports every such entry as 'decode CHANGED' for ever, migrate-raw fails on it, and a change to NATIVE_COLUMN_STAGGER_LINES cannot be applied to stored entries. Reachable via tools/scan.py --no-shading and debug filing. The session cannot take 7200 dpi at all because it always asks for shading. The realignment ignores read direction, and whether that is correct for a bottom-up pass has not been measured.

**Evidence (from the code):**

```text
scan(): `if resolution == self.NATIVE_COLUMN_STAGGER_DPI: image = self._realign_native_column_stagger(image)` then `raw_pixels = image`. The realign always does `aligned[:, 1::2, ...] = image[lines:, 1::2, ...]` regardless of `direction.reversed`. reconstruct: `if image.shape != stored.shape: return image, f"decode CHANGED: now {image.shape}, stored {stored.shape}"`. migrate-raw: `if plain.shape != stored.shape: failed.append(...)`.
```

**Failure scenario:** `tools/scan.py --dpi 7200 --no-shading` files an entry and `make reconstruct` reports 'decode CHANGED: now (6891, 10344, 3), stored (6887, 10344, 3)' forever. An RGBI 7200 dpi pass that follows a bit-0 pass is read bottom-up and filed with a zigzag that is twice as bad and looks 'corrected'.

**Fix:** Record the host-side transform in the entry (e.g. scan.host_transforms=[{'realign_stagger': 4}]) and make reconstruct/decode_raw apply the same transform from that record. Better still, keep scan.tif as the plain decode and apply the realignment in corrected(). Verify the stagger sign on a bottom-up 7200 dpi pass before trusting it.

<details><summary>Second reader's check</summary>

This part is confirmed: scan() realigns at direct.py:2695-2701 before `raw_pixels = image` (2708), and the record does not say so. reconstruct (library.py:493-497) then reports a shape change, and migrate-raw (tools/library.py:175-178) lists the entry as failed and exits 1. The realignment runs on the upright decode whatever direction.reversed says, so a bottom-up pass plausibly gets the wrong sign. That part is unmeasured. This part is refuted: 'through ScanSession the layout guard drops the raw bytes'. The GUI never passes shading=False (gui.py:2054-2061, Scan.shading defaults to True), and scan_roll also uses shading=True. A 7200 dpi pass through the session therefore raises ShadingUnavailable at direct.py:2566-2578 before any pass, and the session never files a 7200 dpi entry. The reachable paths are tools/scan.py --dpi 7200 --no-shading and debug-filed scripts, and both keep the H-row raw bytes next to the (H-4)-row scan.tif.

</details>

<a id="library-lib-10"></a>

### LIB-10 -- Entries are written non-atomically; a crash leaves an orphan directory that verify and reconstruct never see, and the collision check can reuse it

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:165-176`, `rps7200/library.py:178-211`, `rps7200/library.py:308-310`, `rps7200/library.py:741-750`, `rps7200/library.py:776-786`, `rps7200/library.py:633-635`, `tools/library.py:194-206`, `tools/uniformity.py:643-646`, `rps7200/library.py:770-772`

Nothing is written to a temporary name and renamed. Three failure modes follow:
- If the process dies during the gzip (minutes at 3600-7200 dpi), from disk-full or a kill, the directory holds scan.tif and a partial raw.bin.gz with no scan.json. It is invisible to entries(), list, verify, reconstruct, index.json and the demo, and nothing reports it.
- A truncated scan.json (a crash during a rewrite) makes a complete entry vanish silently from every check.
- The suffix check looks only for scan.json. Two writers with the same id and second can both pass it, and a later save can land in a crashed directory and inherit stale files (e.g. a leftover raw.bin.gz or prescan.tif) that its record does not mention; reconstruct uses raw.bin.gz by existence, not by the record.

index.json is rewritten non-atomically by FrameWriter and by the GUI's delete (gui.py:4012) concurrently.

**Evidence (from the code):**

```text
`if (path / "scan.json").exists(): ... while (path / "scan.json").exists(): path = base.with_name(f"{base.name}-{n}")` / `path.mkdir(parents=True, exist_ok=True)`. Then scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz are written straight to their final names, and `(path / "scan.json").write_text(...)` comes last. entries(): `for candidate in sorted(root.glob("*/scan.json")): try: ... except (OSError, json.JSONDecodeError): continue`. The migrations and uniformity's REJECTED also rewrite scan.json in place.
```

**Failure scenario:** The disk fills during frame 30 of a 3600 dpi roll. The entry directory is left with a 60 MB half raw.bin.gz and no scan.json. `make verify` says 'library is intact' and the orphan occupies space for ever.

**Fix:** Write into `<id>.partial/` and rename it to the final id only after scan.json is flushed. Write scan.json and index.json via a temp file plus os.replace. Allocate the id with an exclusive mkdir (exist_ok=False) in a retry loop. Make verify report directories without a readable scan.json, and unreadable scan.json files, instead of skipping them.

<details><summary>Second reader's check</summary>

library.save (165-230) mkdirs with exist_ok=True and writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz directly under their final names. scan.json comes last, written with write_text and no temp file or rename. entries() (741-750) globs */scan.json and silently skips files that fail to parse. verify therefore never sees an orphan directory or a truncated scan.json. The collision loop checks only scan.json, so a later save can reuse a crashed directory and inherit its stale files. reconstruct and read_raw open raw.bin.gz because it exists, not because the record names it. Concurrent same-id writers within one process are unlikely (FrameWriter is a single thread) but possible across processes. index.json is rewritten non-atomically from both FrameWriter and the GUI thread (gui.py:4012).

</details>

<a id="library-lib-11"></a>

### LIB-11 -- GUI single-scan and prescan filing gzips with the device open and idle, contrary to the documented wedge precaution

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P13](../problems/P13-gzip-with-device-open.md)

**Where:** `rps7200/session.py:1013-1025`, `rps7200/session.py:1311`, `rps7200/session.py:1429-1438`, `rps7200/session.py:1456`, `rps7200/library.py:192-211`

The overlap argument holds only inside a roll. After a single Scan or Prescan in the window there is no next pass, so library.save gzips 60-250 MB on a background thread while the device is open and idle, which is precisely the state CLAUDE.md says preceded a wedge. The 'single scan' rule is implemented only for tools/scan.py and debug mode, not for the window, which is the path Stefan actually uses.

**Evidence (from the code):**

```text
FrameWriter docstring: `On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.` The writer is created at session start (`self._writer = FrameWriter(on_done=self._filed)`) and every Prescan/Scan job submits to it, while the worker returns to `job = self._jobs.get()` with the scanner open. CLAUDE.md:160 says `A single scan compresses nothing while the device is open.`
```

**Failure scenario:** Stefan scans one 3600 dpi RGBI frame from the window. For roughly 20-40 s the writer compresses 250 MB while the device sits open with nothing to do. If the earlier wedge was causal, the next command fails and needs a power cycle.

**Fix:** Decide whether the hazard is real (tools/filing_load_test.py) and make the documentation and code agree. Either spool single GUI scans and file them when the session closes or idles past a guard, or document that the window deliberately files while open.

<details><summary>Second reader's check</summary>

ScanSession._run creates FrameWriter (session.py:1311) and keeps the scanner open between jobs. _prescan and _scan submit to the writer, which gzips inside library.save (192-211) while the worker waits idle on `self._jobs.get()` with the device open. The FrameWriter docstring (1016-1020) claims the write 'overlaps the next frame's scan, so the device is busy rather than idle throughout'. That holds only inside a roll. CLAUDE.md's statement that 'A single scan compresses nothing while the device is open' is true only of debug filing and tools/scan.py.

</details>

<a id="library-lib-12"></a>

### LIB-12 -- The session's layout guard drops the raw bytes of any truncated (short-read) pass

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P07](../problems/P07-failed-and-short-passes-lose-bytes.md)

**Where:** `rps7200/session.py:2072-2099`, `rps7200/direct.py:1501-1503`, `rps7200/direct.py:1522-1532`, `rps7200/direct.py:1595`

When the scanner ends early (EndOfData) the image is shorter than params.lines, so the guard reports 'raw bytes do not describe this image' and files the pass with no raw bytes. A truncated pass is exactly where the raw bytes are the evidence (which lines and tags arrived, in which order). The layout even carries lines_received, but the guard ignores it.

**Evidence (from the code):**

```text
The guard compares `actual = {"lines": shape[0], ...}` against `layout["lines"]`, which is `int(params.lines)` (requested), not `lines_received`. read_planes: `except EndOfData: self._log(...); break`, and the image height is `min(len(planes[c]) for c in order)`.
```

**Failure scenario:** A 1800 dpi pass stops 200 lines early. The entry is filed as a shortened scan.tif with 'no raw bytes, so it cannot be re-decoded', and the partial stream is lost.

**Fix:** Judge lines against lines_received (or against the decode of the bytes themselves). Better still, decode the capture's bytes and compare the result with raw_image exactly, which also covers the same-shape case the docstring at session.py:2049-2060 admits the guard is blind to.

<details><summary>Second reader's check</summary>

read_planes breaks on EndOfData (direct.py:1501-1503), and decode_index trims to the common height (1595). The layout's 'lines' is int(params.lines), the requested count (1528), and lines_received is recorded separately (1531). session._file (2072-2099) compares layout['lines'] with image.shape[0], so a short pass has its raw bytes dropped, which is exactly the case where the bytes are the evidence.

</details>

<a id="library-lib-13"></a>

### LIB-13 -- Metering evidence and 'metered' flag are lost for roll frames and brackets

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2805-2809`, `rps7200/direct.py:2791`, `rps7200/direct.py:3615-3652`, `rps7200/direct.py:2374-2400`, `rps7200/library.py:676-684`

Metering is the default for rolls (meter='each'), yet every roll frame is filed with exposure_metered=false and no metering block, and so is every bracket pass. The probe levels, the targets, blue's headroom and the 'limited' flags, which CLAUDE.md and the metering docstring rely on to check BLUE_RGBI_HEADROOM from ordinary work, never reach those entries. signature() also misreads such frames as commanded exposures.

**Evidence (from the code):**

```text
scan(): `if auto_exposure and self.last_metering is not None: meta["metering"] = self.last_metering` and `"exposure_metered": bool(auto_exposure)`. scan_roll: `scales = self.auto_exposure(target=..., infrared=infrared, film=film)` then `self.scan(..., exposure_scale=scales, ...)` with auto_exposure left False. scan_bracket does the same (`scales = self.auto_exposure(film=film, infrared=infrared)`, then `self.scan(... exposure_scale=pass_scale ...)`).
```

**Failure scenario:** A roll's blue channel clips on frame 12. The entry says the exposure was commanded and holds no record of what the probe measured, so the headroom constant cannot be checked from the roll.

**Fix:** Attach last_metering (and exposure_metered=True, with the base scales) to meta in scan_roll and scan_bracket after the explicit auto_exposure call, or pass a 'metered_by' record into scan().

<details><summary>Second reader's check</summary>

scan_roll calls self.auto_exposure explicitly (direct.py:3629-3632) and then scan(exposure_scale=scales) without auto_exposure, so meta gets exposure_metered=False (2791) and no 'metering' block (2805-2809 is gated on auto_exposure). scan_bracket does the same (2374-2376, then 2391-2401). Every roll frame and every bracket pass therefore lacks the probe evidence, and signature() treats roll frames as commanded exposures.

</details>

<a id="library-lib-16"></a>

### LIB-16 -- Tools locate entries by record['id'], not by their directory; copied or renamed entries break verify/reconstruct/migrate-raw/duplicates

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:28-30`, `rps7200/library.py:741-750`, `rps7200/library.py:780-781`, `tools/library.py:97`, `tools/library.py:123-129`, `tools/library.py:161`

If an entry directory is renamed, or copied under another name or into a sibling library, its record['id'] no longer matches the directory. The copy is then misreported:
- verify says 'scan.tif is missing' for the intact copy;
- reconstruct reads root/id and reports 'no raw bytes';
- duplicates deletes root/<id> (the original) when it meant the copy.

With a missing id the path becomes root/'None', and an empty id would make it root itself (rmtree of the whole library).

**Evidence (from the code):**

```text
The docstring says `Entries are self-contained directories: nothing refers out, so one can be copied or deleted on its own.` entries() returns only the parsed JSON, and callers rebuild the path with `path = root / str(record.get("id"))`. duplicates: `path = root / str(record.get("id"))` ... `shutil.rmtree(path)`.
```

**Failure scenario:** Stefan copies an entry to `library/<id>-keep` for safety. `duplicates --delete` finds both records with id <id>, removes `library/<id>` and leaves the copy, and verify now reports the copy as missing its files.

**Fix:** Have entries() return (directory, record) pairs and use the directory everywhere. Report id/directory mismatches in verify. Guard rmtree so the target is a direct child of root whose scan.json is the record being deleted.

<details><summary>Second reader's check</summary>

entries() returns only parsed records. verify (781), tools reconstruct (97), duplicates (123) and migrate-raw (161) all rebuild the path as root/record['id']. A copied or renamed directory therefore points at the original: duplicates rmtree's root/<id>, and verify reports the copy's files as missing. An id of '' would make the path root itself, and rmtree(root) would follow if that entry were ever pruned, though that needs a hand-edited record. migrate-direction correctly uses the glob path.

</details>

<a id="library-lib-17"></a>

### LIB-17 -- Metering probes, hold/aim passes and tools/scan_roll.py dry-run prescans are never filed outside debug mode

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:2083-2092`, `rps7200/direct.py:2906-2907`, `rps7200/session.py:1265-1271`, `tools/scan.py:160`, `tools/scan_roll.py:353`, `tools/scan_roll.py:492-510`

**Doc claim:** CLAUDE.md:104 'File every scan in the library, with its raw bytes. tools/scan.py and tools/scan_roll.py both default to library/'; README.md:185 'Every scan is filed in library/ by default'; direct.py:2087-2090 comment 'CLAUDE.md's rule is file every scan ... keep_raw=True'

The rule 'file every scan' is met only for passes the caller files explicitly. Several kinds of pass are lost in the window and both tools:
- auto-exposure probe passes (2-3 per metered scan or frame);
- every hold-loop and aim verification prescan;
- the prescans of a tools/scan_roll.py walk (TODO.md:715 acknowledges this one).

keep_raw=True on the probes only overwrites last_raw.

**Evidence (from the code):**

```text
auto_exposure: `image, _ = self.scan(..., keep_raw=True)`, but only _debug_capture would file it, and session/scan.py/scan_roll.py all construct `DirectScanner(..., debug=False)`. scan_roll.py dry-run: `tiff.write(str(pre), frame.prescan)`, which writes the corrected prescan to the roll folder with no library.save.
```

**Failure scenario:** A metering decision looks wrong on frame 7. The probes it was based on do not exist anywhere, and only their summarised levels (for single scans) survive.

**Fix:** Give ScanSession and the tools a filing hook for probe and hold passes (tagged 'probe'), or enable spooled debug filing for them. File dry-run prescans in scan_roll.py with raw_image=frame.raw_prescan, as the session does. Correct README.md:185.

<details><summary>Second reader's check</summary>

The auto_exposure probes run with keep_raw=True (direct.py:2083-2092), but only _debug_capture files them. ScanSession._default_scanner (session.py:1265-1271), tools/scan.py:160 and tools/scan_roll.py:344 all construct DirectScanner with debug=False. Hold and aim verification prescans are never filed. scan_roll.py dry-run writes the corrected prescan with tiff.write (492-500) and makes no library entry. README.md:185 says 'Every scan is filed in library/ by default', which the code contradicts for these passes.

</details>

<a id="library-lib-18"></a>

### LIB-18 -- Demo stand-in diverges in what it files: no last_pixels_raw, stale reference/mask on prescans, ignores shading=False, no 7200 dpi refusal, demo flag dropped

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed · **Problem** [P26](../problems/P26-demo-files-corrected-as-raw.md)

**Where:** `rps7200/demo.py:455-477`, `rps7200/demo.py:1164-1200`, `rps7200/demo.py:530-597`, `rps7200/demo.py:1002-1010`, `rps7200/session.py:1412-1415`, `rps7200/direct.py:2566-2578`, `rps7200/library.py:237-264`

The demo is meant to be the real software with different inputs. Instead:
- Its prescan entries in demo/library hold corrected pixels labelled raw, together with another entry's shading.npz and a CCD mask of another resolution, so library.corrected() applies a mismatched correction to them.
- A 7200 dpi scan succeeds in the demo where DirectScanner.scan raises ShadingUnavailable before any pass.
- Because the 'demo' marker is dropped, demo-filed entries are indistinguishable from real ones if --library points at the real library.

**Evidence (from the code):**

```text
DemoScanner is not a DirectScanner subclass and never sets last_pixels_raw, so session `_prescan` gets `getattr(self._scanner, "last_pixels_raw", None)` -> None and files the corrected prescan. `_pair_image("prescan.tif")` returns the stored TIFF without refreshing `self._capture`, so capture_record() hands back the reference and mask of the previously decoded entry. `_decode` applies shading whenever a reference exists, regardless of scan(shading=False). No `_shading_columns_needed`/MAX_SHADING_COLUMNS check is made. The meta `"demo": True` is not in library.save's whitelist.
```

**Failure scenario:** In --demo: Scan at 1800 dpi, then Prescan. The prescan entry carries the 1800 dpi pass's ccd_mask.bin, and its full-resolution view re-shades the 300 dpi corrected pixels with the wrong column map. Choosing 7200 dpi in the demo completes a scan that the real window would refuse.

**Fix:** Set last_pixels_raw and last_scan_meta for both passes and reset _capture per pass (clearing reference/mask when serving a stored prescan.tif). Honour shading=False. Call DirectScanner._shading_columns_needed with MAX_SHADING_COLUMNS and raise ShadingUnavailable. Keep 'demo' in the record.

<details><summary>Second reader's check</summary>

DemoScanner is a plain class (demo.py:176) and never sets last_pixels_raw (grep), so session._prescan (1415) gets None and files the image it returned. That image is the source's corrected prescan.tif or a shading-corrected decode, with no corrections label. _pair_image returns stored prescan.tif early (1175-1182) without resetting self._capture, so capture_record hands back the previous decode's reference and mask. The shape guard drops only the raw bytes, so the entry keeps another pass's shading.npz and ccd_mask.bin, and since its meta has no 'shading' or 'shading_skipped', corrected() re-applies them. _decode (1008-1010) corrects whenever a reference exists, regardless of scan(shading=False), and scan's meta then says shading None. There is no MAX_SHADING_COLUMNS refusal. The 'demo' key is dropped by the whitelist.

</details>

<a id="library-lib-22"></a>

### LIB-22 -- tools/uniformity.py: analyse includes passes rejected at capture; the session id is dropped; provenance key typo

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:161-171`, `tools/uniformity.py:636-647`, `tools/uniformity.py:608`, `tools/uniformity.py:377`, `tools/uniformity.py:454-476`

A pass that the operator rejected and redid is still analysed, and because entries sort by timestamp it is preferred as the reference or orientation pass. Handling mistakes the operator explicitly discarded therefore feed the repeat floors and the decomposition. Phases or sessions cannot be separated from the record, and the 'pipeline:' line always says 'unknown'. `duplicates --delete` would also treat the rejected pass and its redo as duplicates (same subject label), deleting the evidence that the REJECTED marker exists to keep.

**Evidence (from the code):**

```text
select(): `if tag in (record.get("tags") or []): out.append(candidate.parent)`, with no exclusion of the 'rejected' tag or the REJECTED file that one_pass writes on 'redo'. `base = by_orientation[AS_IS][0]` and `it8[by_orientation[name][0]]` take the oldest entry, i.e. the rejected one. `meta["uniformity_session"] = session` is dropped by library.save's whitelist. `library.provenance().get('commit', 'unknown')` uses a key provenance never writes ('driver_commit').
```

**Failure scenario:** The operator redoes 'IT8 180' because the slide slipped. analyse uses the slipped pass as d_rot180, and the verdict is computed from it.

**Fix:** Exclude entries tagged 'rejected' (or with a REJECTED file) in select(). Store the session in film notes or tags until the whitelist is fixed. Use provenance()['driver_commit'].

<details><summary>Second reader's check</summary>

select() (uniformity.py:161-171) includes every tagged entry, rejected ones among them. one_pass's redo (636-646) adds the 'rejected' tag and a REJECTED file, which nothing reads. decompose_and_report uses by_orientation[...][0], the oldest pass, so a rejected pass that was redone is preferred as the base or orientation pass (454-476). uniformity_session is dropped by the whitelist. cmd_analyse prints provenance().get('commit'), but the key is 'driver_commit' (library.py:98), so it always prints 'unknown'. Severity is raised to medium because this produces wrong analytical verdicts from a documented operator action (redo).

</details>

<a id="library-lib-23"></a>

### LIB-23 -- Debug spool is unrecoverable if the process dies before close(), and lives in system temp (possibly tmpfs/RAM)

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P03](../problems/P03-debug-spool-not-crash-safe.md)

**Where:** `rps7200/direct.py:650-692`, `rps7200/direct.py:777-788`

Debug filing is the mechanism CLAUDE.md relies on for every ad-hoc scan. If the script is killed (for example by the harness's 10-minute limit, which CLAUDE.md warns about), or crashes outside a with/finally, every spooled pass of the session is lost from the library. The .npy and .bin files stay orphaned in /tmp with no metadata, because the meta was held in memory. On hosts where /tmp is tmpfs, the spool 'held on disk, not in memory' is in fact in RAM.

**Evidence (from the code):**

```text
`self._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))`. Filing happens only in `close()` -> `self._debug_flush()`, and no code ever re-reads an orphaned spool.
```

**Failure scenario:** A 12-minute probe run in the foreground is killed at 10 minutes. The scans that completed before the kill were paid for in scanner time and are not filed.

**Fix:** Write each item's meta JSON next to its spool files. Place the spool under the library root (e.g. library/.spool). Add a `tools/library.py recover-spool` command, or file pending items at the next DirectScanner start.

<details><summary>Second reader's check</summary>

The spool is tempfile.mkdtemp (direct.py:666-668). Meta, reference and mask are kept only in self._debug_pending (in memory), and filing happens only in close() via _debug_flush (777-788). A kill or crash outside a with/finally loses every spooled pass of the session, including the harness kill at 10 minutes that CLAUDE.md warns about. The orphaned .npy/.bin files have no metadata and no recovery tool reads them. Raised to medium because this is the only filing path for every ad-hoc script.

</details>

<a id="library-lib-a2"></a>

### LIB-A2 -- Entry does not record where its shading reference came from (reused file vs. calibrated this session) or when

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P05](../problems/P05-calibration-not-stored-exactly.md)

**Where:** `rps7200/direct.py:549-561`, `rps7200/direct.py:572-602`, `rps7200/library.py:281-296`, `rps7200/direct.py:631-646`

With --reuse (tools/scan.py) or a session that loaded calibration/shading.npz, an entry carries a reference measured in another power-on session, possibly days earlier and at a different lamp state. Nothing in scan.json says so. A reference measured seconds before the pass looks identical, and there is no content hash to group entries that share one calibration. Pass-to-pass and drift studies cannot separate calibration age from other effects.

**Evidence (from the code):**

```text
load_shading: `self._shading = ShadingReference.load(Path(path))`; ensure_shading(reuse=True) loads calibration/shading.npz when it exists. capture_record returns only `"reference": self._shading`. The calibration block in the record: `shading`, `ccd_mask`, `pixels_per_line`, `light_mean` (rounded to 0.1), `report`, `skipped`, with no source, timestamp, hash or calibration resolution.
```

**Failure scenario:** Stefan scans with --reuse after a week. Stripes appear, and the entries do not show that the reference was a week-old file rather than a fresh calibration.

**Fix:** Carry reference provenance on the ShadingReference or in the capture: action (calibrated or loaded), source path, calibration timestamp, calibration resolution and gain/offset. Record it together with a sha256 of shading.npz in calibration{}.

<a id="library-lib-14"></a>

### LIB-14 -- reconstruct misreports a corrupt raw.bin.gz as 'no raw bytes' and aborts the whole run on ScanReadError or a missing scan.tif

**Severity** low · **Category** error-handling · **Verdict** partly

**Where:** `rps7200/library.py:394-408`, `rps7200/library.py:493`, `tools/library.py:95-113`

reconstruct reports a corrupt raw.bin.gz as 'no raw bytes' and exits 0, although verify separately flags its checksum. A missing scan.tif crashes the whole reconstruct run, and the same goes for migrate-raw via reconstruct. Uncaught ScanReadError in reconstruct and decode_raw is a latent gap that current filing paths make hard to reach.

**Evidence (from the code):**

```text
read_raw: `except (OSError, EOFError, gzip.BadGzipFile): return None`, so reconstruct returns `"no raw bytes stored for this entry"`, which tools/library.py counts as `unreadable` ('not a regression') and exits 0. reconstruct catches only `(KeyError, ValueError, TypeError)`, while decode_index raises ScanReadError (a RuntimeError) on 'no recognisable channel tags' or a channel mismatch. `stored = tiff.read(str(path / "scan.tif"))` sits outside any try. decode_raw likewise does not catch ScanReadError.
```

**Failure scenario:** One entry's raw.bin.gz is cut short by the orphan scenario in LIB-10. `make reconstruct` reports it as '- nothing to decode from' and exits 0. Another entry holds a 0-byte raw (a pass that hit EndOfData at once), and the run aborts with ScanReadError halfway through 300 entries.

**Fix:** Distinguish a missing file from an unreadable one in read_raw (raise, or return a reason). Catch ScanReadError and OSError per entry in reconstruct and decode_raw, and report them as failures that make the exit status non-zero.

<details><summary>Second reader's check</summary>

Confirmed: read_raw (library.py:394-408) returns None for a corrupt or truncated gzip, and reconstruct then reports 'no raw bytes stored', which tools/library.py:103-104 counts as benign with exit 0. verify does catch this case as a checksum mismatch (812-816), which softens it. Also confirmed: `stored = tiff.read(...)` (library.py:493) sits outside any try, and tools/library.py:96-99 has no try, so a missing scan.tif aborts `make reconstruct` with a traceback. The ScanReadError path is hard to reach: gzip's CRC rejects garbled bytes, raw and layout are always stored as a matching pair, and a pass with zero lines cannot be filed because read_planes' own decode raises first. That part is largely theoretical.

</details>

<a id="library-lib-19"></a>

### LIB-19 -- corrected() silently maps columns 1:1 when the CCD mask is missing and ignores any correction label other than 'shading'

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:373-391`, `rps7200/library.py:330-334`, `rps7200/shading.py:235-239`

The reference spans the whole CCD. Without the per-pass mask, a 300-1800 dpi entry is corrected against the wrong columns and still reported as 'applied'. This happens when ccd_mask.bin is lost, or when the record names a mask that is absent, which verify does flag but corrected() does not. A caller that labels corrections=['destripe'] (or any other name) gets shading applied on top anyway.

**Evidence (from the code):**

```text
`record["ccd_mask"] = ((path / mask_file).read_bytes() if mask_file and (path / mask_file).exists() else None)` then `apply_shading(image, record["reference"], record["ccd_mask"])`, and apply_shading `if ccd_mask is None: loc = np.arange(min(w, reference.pixels_per_line))`. `if "shading" in applied: record["corrected"] = "already"`.
```

**Failure scenario:** An entry's ccd_mask.bin is deleted. Save As writes a visibly striped 'applied' file without any warning.

**Fix:** Return 'no mask' (raw, labelled) when the reference's width differs from the pass width and no mask exists. Treat any non-empty corrections_applied as 'already' or refuse.

<details><summary>Second reader's check</summary>

corrected() passes record['ccd_mask'] (None when the file is absent) to apply_shading, which then maps columns 1:1 (shading.py:235-236). This is wrong for any pass below 3600 dpi, and it is still labelled 'applied'. Only 'shading' in corrections_applied short-circuits the call (library.py:373-376). No caller in the repo passes corrections= at all today, so the second half is latent.

</details>

<a id="library-lib-20"></a>

### LIB-20 -- Entry id and 'created' are the filing time, not the capture time; debug's captured timestamp is discarded

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:166`, `rps7200/library.py:214-215`, `rps7200/direct.py:672`, `rps7200/direct.py:713-729`, `tools/scan.py:237-246`

For debug entries and tools/scan.py (single passes and brackets) the timestamp in the id and in 'created' can be minutes after the pass. Ordering stays roughly correct, but correlating entries with lamp warm-up, logs or captures uses a wrong time. No field records when the pass was scanned.

**Evidence (from the code):**

```text
`when = datetime.now(timezone.utc)` inside save. `item: dict[str, Any] = {"meta": dict(meta), "captured": time.time()}`, but 'captured' is never passed to save. tools/scan.py files `pending` only after the `with DirectScanner(...)` block closes.
```

**Failure scenario:** A 9-pass bracket is scanned from 10:00 to 10:12. All nine entries read 10:12:xx, so drift over the bracket cannot be placed in time.

**Fix:** Record the capture time in meta in scan() (e.g. meta['captured_utc']) and use it for the id and 'created'. Keep the filing time as a separate field.

<details><summary>Second reader's check</summary>

save() uses datetime.now() (library.py:166) for both the id and 'created'. The debug item's 'captured' time (direct.py:672) is never passed on. tools/scan.py files entries only after the with-block closes (237-246), and nothing in scan() meta records the capture time.

</details>

<a id="library-lib-21"></a>

### LIB-21 -- verify has no integrity check for shading.npz, ccd_mask.bin or prescan.tif, and skips raw checks when scan.tif is missing

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:776-816`, `rps7200/library.py:281-303`

A silently altered or corrupted shading.npz or mask changes every corrected output and cannot be detected. A missing prescan.tif named in the record is not reported. An entry whose derived scan.tif was lost but whose raw bytes are intact is not checked for raw integrity, although it is fully recoverable from them.

**Evidence (from the code):**

```text
Only `image.sha256` and `raw.sha256` exist. verify: `if not scan.exists(): problems.append(...); continue`, so the raw bytes of that entry are never checked. For calibration files it checks only `if name and not (path / name).exists()`. The prescan block has no sha256 and no existence check.
```

**Failure scenario:** A sync tool rewrites shading.npz partially. verify says intact, and corrected() raises or produces wrong gains.

**Fix:** Store sha256 values for shading.npz, ccd_mask.bin and prescan.tif. Verify every listed file, and continue to check the raw bytes after a scan.tif problem.

<details><summary>Second reader's check</summary>

The record carries sha256 only for image and raw (228, 234). verify checks only that the shading and mask files exist (788-792), never checks prescan.tif, and `continue`s after a missing scan.tif (784-786), which skips the raw checks for an entry that is fully recoverable from its bytes.

</details>

<a id="library-lib-24"></a>

### LIB-24 -- duplicates --keep accepts 0 and negative values

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/library.py:53-55`, `rps7200/library.py:728-737`

--keep 0 crashes with IndexError on the first group. --keep -1 keeps all but the last entry and deletes one entry per group, which is neither documented nor intended.

**Evidence (from the code):**

```text
`ap.add_argument("--keep", type=int, default=1, ...)`. prunable: `kept = ranked[:keep]; for record in ranked[keep:]: best = kept[0].get("id")`.
```

**Failure scenario:** `duplicates --delete --keep -1`, meant as 'keep all', deletes the least useful entry of every group.

**Fix:** Validate keep >= 1 in argparse.

<details><summary>Second reader's check</summary>

--keep is a plain type=int (tools/library.py:53-55). In prunable, kept = ranked[:0] = [], and kept[0] raises IndexError. With keep=-1, ranked[:-1] is kept and the last entry of every group is deleted.

</details>

<a id="library-lib-25"></a>

### LIB-25 -- Library root is relative to the working directory, and verify/reconstruct/index cover only one directory level

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/library.py:51`, `rps7200/direct.py:711`, `rps7200/session.py:1133`, `tools/library.py:47`, `rps7200/library.py:745`, `rps7200/demo.py:77-95`

A script or tool started from another directory creates and fills a new ./library there without any warning. `make verify` and `make reconstruct` look only at ./library/*/, so they never check the nested sub-libraries and sibling libraries that the demo reads and that this machine evidently holds.

**Evidence (from the code):**

```text
`DEFAULT_ROOT = Path("library")`, `root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT`, and `for candidate in sorted(root.glob("*/scan.json"))`. The demo, by contrast, walks `library 2` and nested `300dpi` folders (libraries_beside).
```

**Failure scenario:** `cd tools && RPS7200_DEBUG=1 uv run python byte14_probe.py` files into tools/library, and `make verify` never sees those entries.

**Fix:** Resolve the default root against the repository (or a configured absolute path). Make verify and reconstruct optionally recursive and report nested libraries.

<details><summary>Second reader's check</summary>

DEFAULT_ROOT = Path('library') (library.py:51). ScanSession root defaults to 'library' (session.py:1133), and debug uses it too (direct.py:711). All of these are relative to the cwd. entries() globs only */scan.json, while demo.libraries_beside (demo.py:77-95) walks sibling 'library *' directories and nested subfolders.

</details>

<a id="library-lib-26"></a>

### LIB-26 -- README and library.py give contradictory gzip ratios for raw.bin.gz

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `README.md:508`, `rps7200/library.py:188-190`

**Doc claim:** README.md:508 'the median raw.bin.gz across the library is about 94% of the raw size' vs rps7200/library.py:188-190 'gzip takes it to 72% of its size'

Two measured claims disagree. This does not affect correctness, but disk budgeting (README's roll-size table) depends on it.

**Evidence (from the code):**

```text
library.py: `# Compressed, but byte-exact: measured on a real pass, gzip takes it to 72% of its size`. README: `the median raw.bin.gz across the library is about 94% of the raw size`.
```

**Failure scenario:** A roll is budgeted from the 72% figure and runs out of disk.

**Fix:** Re-measure across the library and keep one number.

<details><summary>Second reader's check</summary>

library.py:188-190 says gzip takes a pass to 72%, and README.md:508 says the median is about 94%. The two are contradictory measured claims.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Entry pixels (the 'decode') | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.tif | Strip TIFF, HxWxC uint16 (8-bit for prescans), C=3 or 4 (IR as ExtraSamples=unspecified). With tifffile: zlib + horizontal predictor. Without it: uncompressed. Resolution tag = resolution_dpi. | Intended: raw decode_index output, upright. Actually corrected for GUI single Scan (session._scan) and for demo scans/prescans. Stagger-realigned (H-4 rows) at 7200 dpi. Legacy entries may be bottom-up or shading-corrected ('shading' in corrections_applied). | library.save (library.py:179); rewritten by tools/library.py migrate-raw (:198) and library.migrate_direction (:573) | library.load/corrected/reconstruct/verify(sha); migrate-raw; migrate_direction; demo._decode (fallback when no raw); demo.picture_signature; research/frame-edge; GUI full-res view and Save As via corrected() | Lossless for the pixels written. sha256 of the file is stored in image.sha256. Not atomic. |
| Raw scanner bytes | library/<id>/raw.bin.gz | gzip level 6 of the concatenated SCSI READ payloads: INDEX lines, each a 2-byte channel tag plus bytes_per_line, little-endian samples, in received order. Layout in scan.json raw.layout {format:index, bytes_per_line, line_stride, index_header, width, lines, channels, byte_order, lines_received}. | raw (ground truth) | library.save from raw= (tools/scan.py, uniformity, session capture) or raw_path= (debug spool) | library.read_raw -> reconstruct, decode_raw, verify (sha), migrate_direction; demo._decode; tools/uniformity.rebuild/raw_image; exposure_headroom; linearity | Byte-exact, with sha256 and byte count of the uncompressed stream. Can belong to a DIFFERENT pass in debug mode with keep_raw=False (LIB-02). Dropped for short reads (LIB-12) and for 7200 dpi via the session. Not atomic. |
| Shading reference | library/<id>/shading.npz | np.savez_compressed: pixels_per_line (int), channels, dark_channels, ref{c} float64[ppl], mean{c} float64, dark{c}, darkmean{c} | Derived from the calibration pass by calculate_shading. The calibration bytes are not stored. | library.save via ShadingReference.save; the same session reference is written into every entry of that session | library.load/corrected; reconstruct (legacy corrected entries); demo._decode; tools/uniformity.rebuild | Exact for the derived float64 arrays. No checksum. Cannot be re-derived (LIB-04). |
| CCD mask | library/<id>/ccd_mask.bin | Raw bytes of GET CCD MASK (COPY) for the last pass, one byte per calibration column (0x00 used, 0x70 unused), 5172 bytes at the 3600 dpi reference width | raw | library.save | library.load/corrected; reconstruct; demo._decode; tools/uniformity.rebuild | Exact. No checksum. In demo prescans it can be a stale mask from another entry (LIB-18). |
| Frame's prescan | library/<id>/prescan.tif | 8-bit RGB TIFF, no resolution tag (defaults to 72 dpi in the built-in writer) | corrected (as delivered), unlabelled | library.save(prescan=rf.prescan) from session._roll and tools/scan_roll.py; rows reversed in place by migrate_direction --write | migrate_direction; demo (prescan answers, picture signatures, strip pools, best_pair); research/frame-edge build_dataset | Pixels lossless, but no raw bytes, mask, meta or checksum, so it cannot be re-derived (LIB-05). |
| Entry record | library/<id>/scan.json | JSON (indent 2, default=str). id, created (filing time), image{file, shape, dtype, channels, corrections_applied, sha256}, raw{file, bytes, sha256, layout}, scan{whitelisted meta keys incl. read_direction, carriage_state (READ STATE hex), filter_offsets, fast_infrared, rotation, flipped, reversal}, device_settings{exposure, gain, offset}, metering, registration, calibration{shading, ccd_mask, pixels_per_line, light_mean (rounded to 0.1), report, skipped}, prescan{file, read_direction, carriage_state}, film{stock, format, process, frame, subject, notes}, tags, provenance{driver_commit, driver_dirty, versions, platform} | metadata | library.save (written last); rewritten by migrate-raw, migrate_direction, tools/uniformity.py redo (adds the 'rejected' tag) | every library function; demo; tools/uniformity; research; registration_margin | Whitelisted: meta keys outside the list are silently dropped (LIB-06). inquiry is ignored. Values JSON-stringified via default=str. Written non-atomically. |
| Rejected-pass marker | library/<id>/REJECTED | text | n/a | tools/uniformity.py one_pass on 'redo' | nothing (analyse does not exclude it) | n/a |
| Library index | library/index.json | JSON list of {id, created, dpi, channels, film, frame, tags, corrected, notes} | derived | library.reindex after every save; tools/library.py duplicates/migrate/reindex; GUI delete | nobody in code (for humans) | Derived and top-level only. Non-atomic, with concurrent writers possible (FrameWriter plus the GUI thread). |
| Debug spool | $TMPDIR/rps7200-debug-*/NNN-image.npy, NNN-raw.bin | npy of raw_pixels; raw bytes uncompressed. Meta, reference and mask are held in memory only. | raw pixels + (possibly stale) raw bytes | DirectScanner._debug_capture | DirectScanner._debug_flush (on close), which deletes the files after filing | Exact while present. Lost with its meta on crash or kill (LIB-23). |
| Demo library and signature cache | demo/library/<id>/..., demo/pictures.npz | Same entry format; npz of {paths, signatures float32 24x48} | Demo entries: corrected-as-raw pixels, re-filed source raw bytes, and possibly stale reference/mask | ScanSession/FrameWriter with DemoScanner; DemoScanner._sign_pictures | DemoScanner (when --demo-source points there); GUI | Not trustworthy as a record. 'demo' provenance is dropped (LIB-18). |
| Session shading cache | calibration/shading.npz (GUI/tools), calibration/shading_uniformity.npz (uniformity) | ShadingReference npz | derived | DirectScanner.ensure_shading/save_shading; tools/uniformity.cmd_capture | load_shading (--reuse); tools/uniformity one_pass | Derived only; overwritten per calibration. |
| Uniformity report and orientation crops | <--out>.json; previews/orientation_<id>.png | JSON report (provenance, floors, components, flats); 8-bit PNG strip | derived from corrected rebuilds | tools/uniformity.py analyse/one_pass | humans | Derived. |

**Second reader's corrections to this table:**

- **Entry pixels and raw bytes rows.** These say raw bytes are 'dropped for 7200 dpi via the session'. That cannot happen. The GUI's Scan job always asks for shading=True (gui.py:2054-2061), and so does scan_roll, so a 7200 dpi pass through ScanSession raises ShadingUnavailable before any pass (direct.py:2566-2578). Stagger-realigned 7200 dpi entries come only from tools/scan.py --no-shading and from debug filing, and those keep raw bytes that no longer match scan.tif's height.
- **Frame's prescan row.** Two corrections:
  - prescan_before (session.py:1840-1852) is written only to the roll folder, with file_entry=False, so it never gets a library entry or bytes outside debug mode.
  - In the dry-run GUI roll, prescans ARE filed as their own entries with raw_image=rf.raw_prescan and raw bytes, which the row does not mention.
- **Shading reference row.** The written_by column should say that the reference may be a file loaded from calibration/shading.npz from an earlier session (ensure_shading reuse / load_shading), with nothing in the record saying which (LIB-A2).
- **REJECTED row.** Besides tools/uniformity.py, the redo also rewrites scan.json (adding the 'rejected' tag), without default=str and without reindexing index.json.
- **Debug spool row.** The meta is a copy snapshotted inside scan(). Keys callers add afterwards (registration, roll_index, bracket_*) never reach debug entries.
- **Demo row.** Demo prescan entries' meta has no 'shading' key, so calibration.report is None and corrected() re-applies the stale reference and mask to already-corrected 8-bit pixels.
- **corrections_applied.** No filing path in the repository ever sets it. It is always [] except on legacy entries.

## What the operator can do

- Survey the library with `tools/library.py list` (or `make verify`, `make reconstruct`, `tools/library.py reindex`), all read-only apart from index.json.
- Run `tools/library.py duplicates`, `migrate-raw` and `migrate-direction` as dry runs. They only print unless given --delete or --write.
- Delete a library entry from the window's Delete action (tools/gui.py:3999-4014), after a confirmation dialog that says the raw bytes cannot be recovered.
- Get a corrected full-resolution file of any entry via Save As or the 1:1 view. Both go through library.corrected() with today's correction code.
- Choose where entries go: `--library DIR` for gui.py and tools/scan.py, `--library ''`/`--no-library` to skip filing, and RPS7200_DEBUG=1 with RPS7200_DEBUG_ROOT for ad-hoc scripts.
- Run the vignette study with `tools/uniformity.py capture` (prompted, with accept/redo/abort per pass), then `analyse --tag ...`, which rebuilds from stored raw bytes.
- Run `gui.py --demo`, which files into demo/library by default and draws pictures from `--demo-source`.

## What the operator should not do

- Do not run `tools/library.py duplicates --delete` unless every listed pair is known to be the same frame. With an empty Frame/Stock/Subject, or for ladder passes (byte14, gain), it removes different photographs and their raw bytes.
- Do not run `migrate-raw --write` after any change to the decode until reconstruct is understood. It rewrites every mismatching scan.tif to today's decode and labels it raw.
- Do not rename, copy or move entry directories inside a library. The tools address entries by record['id'], not by folder name.
- Do not point `--demo` at the real library (`--library library`). Demo entries hold corrected-as-raw pixels and lose their 'demo' marker.
- Do not run tools or scripts from outside the repository root. The default library path is relative, and a new library silently appears in the current directory.
- Do not hand-edit scan.json. A syntax error makes the entry vanish from every check without a report.
- Do not kill a script running under RPS7200_DEBUG=1 before it closes the scanner. Everything spooled so far is lost, and long runs should be backgrounded.
- Do not leave a remembered output folder on a removable or network drive that may be absent. A failing copy prevents the library entry from being written.
- Do not run migrations or duplicates while a window session is filing into the same library.
- Do not expect `make verify` to be clean after a deliberate `--no-shading` or 7200 dpi scan. verify reports it as a failure.

## Mistakes nothing guards against

- Scanning from the window with the Scan button: the entry silently stores corrected pixels labelled raw, and Save As then double-corrects them (LIB-01).
- Leaving the Frame field empty: every metered scan at that dpi then shares one duplicate signature (LIB-07).
- Running a probe tool under RPS7200_DEBUG=1 whose passes use keep_raw=False (byte14_probe's final pass, verify_protocol, transport_probe, filing_load_test): the entries are filed with no raw bytes or with the previous pass's bytes, with no warning (LIB-02).
- Running tools/uniformity.py with RPS7200_DEBUG=1: every pass is filed twice, once by debug flush and once by the tool.
- Choosing 'redo' in tools/uniformity.py capture: the rejected pass is still analysed and even preferred as the reference (LIB-22).
- `duplicates --keep 0` (crash) or `--keep -1` (deletes one entry per group) are accepted (LIB-24).
- `migrate-raw --write` on a legacy corrected entry whose shading.npz is missing destroys the only corrected rendition (LIB-15).
- A disk-full or crash during save leaves an orphan entry directory that verify reports as 'intact' (LIB-10).
- An unwritable output folder (unplugged drive, permissions) makes every scan skip its library entry, and the raw bytes are gone (LIB-03).
- Scanning a single frame from the window leaves the device open and idle while its entry gzips (LIB-11).

## Dataflow notes

HOW BYTES ENTER
The pass's bytes enter at DirectScanner.read_planes (direct.py:1440-1542). read_lines calls Transport._read_payload (usb_transport.py:717), which returns exactly the requested bytes. The chunks are joined into `blob`. Only when keep_raw is set are they copied to self.last_raw and self.last_raw_layout (direct.py:1518-1532), and those are never cleared. The blob goes to decode_index (direct.py:1552-1600): it splits lines by tag, truncates to the common height and reverses rows when read_direction (direction.py:85) says the pass was read bottom-up. The result is `image`, and self.last_read_direction is set.

HOW scan() BUILDS THE RESULT
scan() (direct.py:2423-2824) proceeds as follows:
1. It reads the per-pass CCD mask into self._ccd_mask (direct.py:2667-2672).
2. At 7200 dpi it realigns the column stagger (direct.py:2695-2701).
3. It sets raw_pixels = image (direct.py:2708).
4. It applies apply_shading with the session's self._shading (derived by calculate_shading from calibration bytes it then discards, direct.py:1946-1966). The corrected image is what scan() returns.
5. It builds meta (direct.py:2760-2804), adding metering only when auto_exposure=True (direct.py:2808).
6. It calls _debug_capture(raw_pixels, meta) (direct.py:2811), which spools the raw pixels and whatever capture_record() holds at that moment: last_raw, _shading, _ccd_mask.
7. It publishes last_pixels_raw and last_scan_meta (direct.py:2818-2823).

prescan() (direct.py:1682-1724) is scan() at 8-bit with shading. Callers add meta keys afterwards: scan_bracket adds the bracket_* keys (direct.py:2402-2405) and scan_roll adds roll_index, roll_position and registration (direct.py:3653-3655).

HOW ENTRIES LEAVE: THE FIVE FILING PATHS
(1) Debug mode. close() calls _debug_flush (direct.py:694-768), which calls library.save(np.load(mmap), meta, raw_path=spool, raw_layout, reference, ccd_mask, film=FilmNotes(notes='captured with RPS7200_DEBUG on'), tags=['debug']).

(2) tools/scan.py. For each pass it collects last_pixels_raw together with capture_record() (tools/scan.py:176-194; per pass via on_pass for brackets). After the device closes it calls library.save (tools/scan.py:237-246).

(3) ScanSession, the window.
- _prescan files raw_image=last_pixels_raw with meta=last_scan_meta (session.py:1401-1438).
- _scan files ONLY the corrected image (session.py:1456).
- _roll files each frame with raw_image=rf.raw_image and prescan=rf.prescan, which is corrected (session.py:1883-1893). On a dry run it files the prescans with raw_image=rf.raw_prescan (session.py:1818-1833).
- _file (session.py:2031-2141) takes capture_record() immediately, drops the raw bytes when the layout's lines/width/channels differ from the image, adds rotation, flipped and reversal to meta, then calls FrameWriter.submit.
- FrameWriter._write (session.py:1060-1103), on its own thread, first writes the delivered copies (orient, mono, export.write), then calls library.save(raw_image or image, meta, **capture, prescan, prescan_meta, inquiry).

(4) tools/scan_roll.py submits to the same FrameWriter with raw_image=frame.raw_image (tools/scan_roll.py:542-580). Dry-run prescans only go to rolls/<roll>/prescanNN.tif.

(5) tools/uniformity.py one_pass (tools/uniformity.py:563-649) calls library.save(last_pixels_raw, meta, raw, layout, reference, mask) after close.

INSIDE library.save (library.py:131-311)
save makes the id from the filing time plus film notes, dpi and IR (entry_id, library.py:118-128), then checks for a suffix by testing only for scan.json. It then writes, straight to the final names:
- scan.tif (tiff.write, tiff.py:97-168)
- prescan.tif
- shading.npz (ShadingReference.save, shading.py:75-91)
- ccd_mask.bin
- raw.bin.gz (gzip level 6, streaming a sha256 over the uncompressed bytes)

Next it builds the record from a fixed key whitelist (library.py:213-307), writes scan.json last, and calls reindex(root), which re-reads every */scan.json and rewrites index.json.

HOW DATA IS READ BACK
- load (library.py:314-335) returns the TIFF with the reference and mask.
- corrected (library.py:338-391) returns raw 'already' when 'shading' is in corrections_applied, returns raw when calibration.skipped is set, returns raw when there is no reference, and otherwise apply_shading(image, reference, mask). It is consumed by gui.py:3864 (Save As/export), gui.py:4110 (1:1 view), dpi_analysis, registration_margin, linearity and research.
- read_raw and decode_raw (library.py:394-436) gunzip and call _deinterleave, which is decode_index, turned upright.
- reconstruct (library.py:439-515) re-decodes with decode_index, re-applies shading only for legacy 'shading' entries, compares the recorded read direction, then compares with np.array_equal (exact). It never re-applies the 7200 realignment.
- verify (library.py:776-816) checks scan.tif existence and sha, that listed calibration files exist, whether a reference is absent (a 'problem' even when the absence was deliberate), and the raw sha over the whole decompressed stream.
- signature, duplicates and prunable (library.py:639-738) group entries by film notes plus scan settings, and tools/library.py duplicates --delete rmtrees root/record['id'].
- migrate-raw (tools/library.py:137-216) rewrites scan.tif to decode_raw whenever it differs from the stored file.
- migrate_direction (library.py:518-636) records the direction and may turn scan.tif or prescan.tif upright.

DEMO (rps7200/demo.py)
_decode (demo.py:955-1022) takes raw.bin.gz, runs _deinterleave and apply_shading with the entry's own reference and mask, and sets self._capture to that entry's bytes, reference and mask. _read_as_carriage (demo.py:479-528) re-encodes the image as index lines and decodes it with DirectScanner.decode_index, reversing the stored raw bytes when bottom-up. A prescan served from a stored prescan.tif (demo.py:1176-1184) leaves the previous _capture in place. The session then files the corrected image (last_pixels_raw is absent) into demo/library.

UNIFORMITY
tools/uniformity.py analyse selects entries by tag (including rejected ones), rebuilds each as _deinterleave(raw) plus apply_shading(reference, mask), and runs rps7200/uniformity.py: orientation_signature/classify_relative, register (phase correlation), align, block_ratios, fit_field (Huber IRLS, degree 4), solve_components and parity_residual. It writes an optional JSON report. Nothing in rps7200/uniformity.py persists except Field.save/load, which nothing calls.

WHAT CANNOT BE RECOMPUTED FROM AN ENTRY TODAY
- the calibration bytes and calibration context
- the prescan's raw bytes and mask
- the metering probe and hold/aim passes
- INQUIRY
- the byte14, slide_init_param and skip_shading overrides
- the bracket, roll and uniformity-session fields
- the capture time
- whether and how the 7200 dpi stagger was realigned
- metering for roll frames and brackets
- GUI single scans' raw pixels as a stored TIFF (recoverable only by re-decoding raw.bin.gz)
