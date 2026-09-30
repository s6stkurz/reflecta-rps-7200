# How data flows through the code

[Back to the summary](README.md)

Traced from the code by one reader, then every step's location, dtype, thread and
persistence re-checked by a second. Line numbers as of `83dbb22`.

## The whole picture

```text
                 operator
                    |
   tools/gui.py (Tk thread)          tools/scan.py / tools/scan_roll.py (main thread)
        | Calibrate / Prescan / Scan / Roll / Move jobs          |
        v                                                         |
   rps7200/session.py ScanSession  -- worker thread "scanner" --  |
        | _open_scanner()  <== the demo seam: DirectScanner or DemoScanner
        v                                                         v
   rps7200/direct.py DirectScanner ---------------------------------------------
     ensure_shading -> calibrate_shading -> shading.calculate_shading
                          |                    -> ShadingReference (float64)  --> calibration/shading.npz
                          '-- calibration bytes: DISCARDED
     scan(): [auto_exposure: up to 3 metering probes, NOT filed]
             set_scan_frame, SET GAIN OFFSET, MODE SELECT, SLIDE INIT, START SCAN
             usb_transport.Transport  -- bulk READs, 32 KB windows -->  bytes
             read_planes: bytes -> last_raw (only if keep_raw; never cleared)
             decode_index + direction.read_direction -> raw pixels (H,W,C) uint16/uint8
             [7200 dpi: _realign_native_column_stagger, unrecorded]
             raw_pixels -> last_pixels_raw
             shading.apply_shading(raw, reference, ccd_mask) -> corrected
             [debug: _debug_capture(raw_pixels, meta) -> temp spool, filed after close()]
             returns (corrected, meta)
        |
        v
   ScanSession._deliver -> working copy (<=1400 px) -> event queue -> window shows it
   ScanSession._file    -> capture_record() (reference, mask, last_raw, layout)
                           shape guard (drops bytes whose layout disagrees)
                           -> FrameWriter.submit(image, raw_image, meta, capture, paths)
        |
        v  FrameWriter thread (daemon)
   1. export.write every delivered path (out_dir, rolls/<name>/frameNN.tif)  <-- first
   2. library.save(raw_image or image, meta, reference, mask, raw bytes)      <-- second
        -> library/<id>/{scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif, scan.json}
        -> library/index.json
        |
        v  later, any thread
   library.corrected(entry) = apply_shading(scan.tif, shading.npz, ccd_mask.bin)
        -> 1:1 view, Save As, Save all, Export rolls  (orient, mono, export.write)
```

The three places where the design and the code part ways are visible in the picture:

- `_scan` hands FrameWriter the *corrected* image with no `raw_image`, so step 2 files
  corrected pixels and `library.corrected` corrects them again
  ([P01](problems/P01-gui-scan-files-corrected-as-raw.md)).
- FrameWriter's step 1 can fail and skip step 2
  ([P04](problems/P04-delivered-copy-before-library-entry.md)).
- `last_raw` survives into the next pass
  ([P02](problems/P02-debug-filing-stale-raw-bytes.md)).

## Threads

| Thread | Runs | Touches the device? |
|---|---|---|
| Tk main thread (`tools/gui.py`) | widgets, job submission, Save As (on this thread), settings | No, except `force_abort` which closes libusb from here mid-transfer (P14) |
| `scanner` worker (`ScanSession._run`, daemon) | every job: calibrate, prescan, scan, roll, move | **Yes** -- the only thread that should |
| FrameWriter (daemon) | delivered copies, `library.save` (gzip, TIFF compression) | No, but runs while the device is open and idle between jobs (P13) |
| frame-edges (`EdgeWatch`) | detector members over walk prescans | No |
| full-resolution loaders, Save all, Export rolls (daemon) | `library.corrected` in float64, `export.write` | No; unbounded concurrency and killed mid-file on quit (P24) |
| demo-pictures (demo only) | signs source pictures into `demo/pictures.npz` | No |
| CLI main thread | everything in `tools/scan.py`; `scan_roll.py` plus its FrameWriter | Yes |

## Where each kind of pass ends up

| Pass | Library entry? | Raw bytes? | What `scan.tif` holds | Other copies |
|---|---|---|---|---|
| Calibration | no | **discarded** | -- | `calibration/shading.npz` (derived) |
| Metering probe (up to 3) | no -- the session and CLIs force debug off (in ad-hoc debug scripts: yes, own bytes) | -- | -- | -- |
| Window Prescan | yes | yes | raw | `out/prescans/` copy (corrected) |
| Window Scan | yes | yes | **corrected** (P01) | `out/` copy (corrected) |
| Window dry-run walk prescan | yes | yes | raw | `rolls/<name>/prescanNN.tif` (corrected, oriented) |
| CLI dry-run walk prescan | **no** | **no** | -- | `rolls/<roll>/prescanNN.tif` only |
| Hold / aim / verification prescans | no | no | -- | only the last survives, as the frame's `prescan.tif`, corrected |
| Roll frame | yes | yes | raw | `rolls/<name>/frameNN.tif` + `out/` copy (corrected) |
| `tools/scan.py` single / bracket pass | yes, after close | yes | raw (realigned at 7200) | `--out` file (merged, corrected) + `<out>.json` |
| Demo prescan / scan / frame | yes (demo library) | the *source* entry's | **corrected** (P26) | as the real paths |

## Dependencies between modules

`rps7200` never imports `tools`: the frame-edge detector is injected as
`ScanSession.edge_reader`, and the demo as `session._open_scanner`. The one cycle is
`library` <-> `direct`: `library` imports `DirectScanner` at module level (for the static
decode), and `direct` imports `library` lazily inside `_debug_flush`. `demo` is not a
`DirectScanner` subclass. It borrows `_hold_to_approved`, `_aim_frame`, `param_for_mm`,
`roll_ends` and `place_on_strip` by assignment and reimplements `scan`, `prescan` and
`scan_roll`, which is where it drifts ([P27](problems/P27-demo-refusals-and-roll-loop.md)).
The GUI imports the session's private helpers `_safe` and `_unclaimed`, and uses `_safe`
to pick a folder the session does not use ([P18](problems/P18-roll-folder-identity.md)).

| Module | Imports | Notes |
|---|---|---|
| `rps7200.protocol` | stdlib only | Constants, dataclasses (ScanParameters, Settings, State), MM_PER_UNIT=0.1057, COMMAND_UNITS=1.84, Settings.scaled clamps 100..65535 |
| `rps7200.usb_transport` | stdlib (ctypes, libusb lazily) | Transport.command/_read_payload |
| `rps7200.direction` | numpy, rps7200.protocol | read_direction, encode_index, reverse_lines |
| `rps7200.shading` | numpy | calculate_shading/apply_shading/ShadingReference |
| `rps7200.defects` | numpy | used by make_comparison via direct re-exports |
| `rps7200.framing` | rps7200.protocol, rps7200.uniformity (lazy) |  |
| `rps7200.direct` | rps7200.defects, rps7200.framing, rps7200.protocol, rps7200.direction, rps7200.shading, rps7200.usb_transport, rps7200.library (lazy in _debug_flush, direct.py:705-706) | Import cycle: library imports direct at module level (library.py:48) for SHADING_SKIPPED_EXPLICIT, DirectScanner (static decode) and ScanParameters |
| `rps7200.tiff` | numpy, tifffile (optional) |  |
| `rps7200.export` | rps7200.dng, rps7200.tiff, PIL (lazy) |  |
| `rps7200.library` | rps7200.tiff, rps7200.direct, rps7200.shading, rps7200.direction (lazy), rps7200.framing (lazy, private _comparable, library.py:538) |  |
| `rps7200.session` | rps7200.export, rps7200.library, rps7200.preview, rps7200.direct, rps7200.direction, rps7200.framing, rps7200.mono, rps7200.protocol | Hosts FrameWriter, used by the GUI and tools/scan_roll.py |
| `rps7200.demo` | rps7200.library, rps7200.tiff, rps7200.direct, rps7200.direction, rps7200.protocol, rps7200.framing, rps7200.session (estimate_seconds), rps7200.shading, rps7200.usb_transport | Not a DirectScanner subclass; borrows _hold_to_approved/_aim_frame/_rejudge_for/param_for_mm/roll_ends/place_on_strip and constants by assignment (demo.py:799-822); reimplements scan/prescan/scan_roll |
| `tools.frame_edges` | rps7200.framing, rps7200.protocol | Injected via ScanSession.edge_reader; rps7200 does not import tools |
| `tools/gui.py` | rps7200.export, rps7200.library, rps7200.preview, rps7200.settings, rps7200.shortcuts, rps7200.tiff, rps7200.console, rps7200.direct, rps7200.framing, rps7200.mono, rps7200.protocol, rps7200.session (incl. private _safe, _unclaimed), tools.frame_edges, rps7200.demo (lazy) |  |
| `tools/scan.py` | rps7200.export, rps7200.library, rps7200.console, rps7200.direct, rps7200.mono, rps7200.bracket (lazy) |  |
| `tools/scan_roll.py` | rps7200.tiff, rps7200.console, rps7200.direct, rps7200.library, rps7200.protocol, rps7200.session, tools.frame_edges |  |
| `tools/library.py` | rps7200.library, rps7200.console, rps7200.tiff (lazy), numpy (lazy) |  |
| `tools/make_comparison.py` | rps7200.preview, rps7200.tiff, rps7200.console, rps7200.direct (defects re-exports), PIL | No library/shading use |
| `tools/*_probe.py, verify_protocol.py, check_scanner.py` | rps7200.direct, rps7200.usb_transport, rps7200.framing, rps7200.session, rps7200.console | Rely on RPS7200_DEBUG (DirectScanner debug=None) for filing |

## Every flow, step by step

### 1. Single scan from the GUI (Calibrate -> Scan -> library -> view / Save As)

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:1929-1936 on_calibrate` | Submits Calibrate(mode=mode or v_shading ('measure'\|'reuse'\|'off'), reference=session.reference) with no confirmation about film, and sets self.calibrated=True optimistically; the 'calibrated' event (gui.py:3235-3237) later sets the real value | Calibrate dataclass | Tk UI thread | - |
| 2 | `rps7200/session.py:1381-1399 _calibrate -> rps7200/direct.py:572-629 ensure_shading` | skip -> no reference; reuse and file exists -> load_shading (direct.py:549-561); otherwise calibrate_shading (direct.py:1755-1975): READ_STATE/wait_warm, SET EXPOSURE/HIGHLIGHT, 128-byte calibration-info read (first 12 bytes logged), set_scan_frame(CALIBRATION_FRAME), cmd_17, SET GAIN OFFSET echo, MODE SELECT 3600 dpi RGB 8-bit INDEX calibrate, SLIDE INIT param 0x01, START SCAN, 10 s silence, 4-line reads of 16-bit lines (bpl=2*width+2) with a gain/offset echo before each, get_ccd_mask(width), finish_scan; session.calibrated = action in (loaded, calibrated) and reference not None | calibration bytes -> calculate_shading (shading.py:109-182) -> ShadingReference{ref/dark: dict[int, float64[5172]], mean, dark_mean, pixels_per_line} or None; calibration ccd mask bytes[width] | 'scanner' worker thread (session.py:1208-1212) | calibration/shading.npz via save_shading (direct.py:563-570, np.savez_compressed, non-atomic) only when a reference resulted. Raw calibration bytes, shading descriptor, calibration-info block, calibration mask and gain readbacks are not persisted (keep_data=False by default and ensure_shading never asks). Nothing records whether the reference was measured or loaded from cache |
| 3 | `tools/gui.py:2047-2060 on_scan` | If calibrated, builds Scan(resolution, infrared, fast_infrared, film, auto_exposure=(expmode=='auto'), exposure_scale, mono, mono_channel, notes=_notes(), tags); shading left at default True | Scan dataclass | Tk UI thread | - |
| 4 | `rps7200/session.py:1440-1451 _scan -> rps7200/direct.py:2423 scan(keep_raw=True)` | One pass | arguments | scanner thread | - |
| 5 | `rps7200/direct.py:2519-2530 -> auto_exposure (1977-2266)` | Up to rounds+1 (=3) RGB probes, each a recursive scan(300 dpi, 16-bit, FULL_FRAME, shading=True, film default 'negative', keep_raw=True) preceded by set_gain_offset(base); metering_slice on the first probe; 99.5th percentile per channel; proportional step, timer ceiling 65535/base; last_metering dict | probe images (H,W,3) uint16 discarded; scales list[float]; last_metering | scanner thread | Nothing: session scanner is debug=False (session.py:1266-1271). last_metering reaches this scan's meta['metering'] only on this auto_exposure path |
| 6 | `rps7200/direct.py:2533-2600` | READ_STATE polling, wait_warm, TEST UNIT READY, media note, SET EXPOSURE/HIGHLIGHT; needed=round((x1-x0+1)*dpi/7200); >5172 -> ShadingUnavailable before any pass (so every 7200 dpi GUI scan is refused here); no/too-narrow reference -> lazy calibrate_shading() at 3600 dpi | needed columns int | scanner thread | - |
| 7 | `rps7200/direct.py:2602-2653` | set_scan_frame, cmd_17, get_gain_offset().scaled(exposure_scale) (protocol.py:589-616, clamped 100..65535), set_gain_offset (29-byte payload), set_mode (byte14 via byte14_for unless overridden; fast_infrared gated on infrared), TEST UNIT READY, SLIDE INIT(param 0x16), wait_ready, carriage_record() | Settings{exposure[4], gain, offset, light}; carriage record | scanner thread | - |
| 8 | `rps7200/direct.py:2655-2678 -> read_planes (1440-1541) -> usb_transport.py:772-857 / _read_payload 717-770` | START SCAN; get_ccd_mask(reference.pixels_per_line) stored in self._ccd_mask; GET PARAMETERS; READs of `batch` lines (NoDataYet -> retry, EndOfData -> stop early); keep_raw -> last_raw/last_raw_layout; last_raw is never cleared, so a keep_raw=False pass leaves the previous pass's bytes there | blob bytes = channels*lines lines of (2-byte tag + bytes_per_line LE); layout{format:index, bytes_per_line, line_stride, index_header, width, lines=params.lines, channels, byte_order, lines_received} | scanner thread | memory only |
| 9 | `rps7200/direct.py:1552-1600 decode_index + rps7200/direction.py:84-114 read_direction` | Split lines by tag into planes (uint16 LE or uint8 by bpl//width), truncate to common height, reverse rows when the first R comes after the first B (and a complete read's last lines agree) | image (H,W,3\|4) uint16/uint8; ReadDirection -> meta.read_direction | scanner thread | - |
| 10 | `rps7200/direct.py:2692-2702` | At exactly 7200 dpi _realign_native_column_stagger trims 4 rows and shifts odd columns (log line only). Reachable only with shading=False (scan.py --no-shading, API); never from the GUI, which refuses 7200 dpi above | image (H-4,W,C) | scanner thread | - |
| 11 | `rps7200/direct.py:2708-2758 -> rps7200/shading.py:196-283 apply_shading` | raw_pixels=image; two-point per-column flat field with this pass's mask (build_width_to_loc), reference scaled to pass depth, floor(x+0.5), clip; IR plane uncorrected (reference is RGB) | corrected (H,W,C); shading_report{columns,width,clipped,uncorrected,clipped_per_channel,two_point} | scanner thread | - |
| 12 | `rps7200/direct.py:2760-2823` | Builds meta (resolution_dpi, channels, film, protocol_revision, shading, shading_skipped, channel_order, depth, frame, width, height, bytes_per_line, filter_offsets, exposure/gain/offset, exposure_scale, exposure_metered=bool(auto_exposure), fast_infrared, duration_s, read_direction, carriage_state, metering if auto_exposure); _debug_capture (no-op when debug=False); last_pixels_raw=raw_pixels; last_scan_meta; returns the CORRECTED image. byte14, slide_init_param, skip_shading, base settings are not recorded | (corrected ndarray, meta dict) | scanner thread | - |
| 13 | `rps7200/session.py:1452-1455, 1481-1534 _note_reversal, 1945-1972 _deliver` | _prescan_here issues a position() READ_STATE and compares with the last prescan's; a pass whose line tags decided its direction is only logged; unknown direction may get meta['reversal']; Result with preview.downscale(image, 1400) copy | Result{seq, kind='scan', working copy <=1400 px, meta copy} | scanner thread -> event queue -> UI | - |
| 14 | `rps7200/session.py:1456-1458 -> _file (2031-2141)` | capture_record() (reference, ccd_mask, last_raw, last_raw_layout); guard drops raw bytes when layout lines/width/channels disagree with the image (keeps reference and mask); composes reversal+rotation; submits to FrameWriter WITHOUT raw_image (_scan never passes last_pixels_raw, unlike _prescan at 1415) | job dict {image: corrected, raw_image: None, meta+rotation/flipped, capture, paths=[out_dir copy]} | scanner thread | - |
| 15 | `rps7200/session.py:1060-1103 FrameWriter._write -> rps7200/library.py:131-311 save` | Writes delivered copy (orient, optional mono, export.write) to out_dir; library.save(job['image']) because raw_image is None; scan.json written last; reindex | corrected uint16 pixels recorded with corrections_applied=[] | FrameWriter thread (daemon) | library/<ts>_<stock>_<dpi>dpi[_ir]/scan.tif (CORRECTED, labelled raw), raw.bin.gz (exact bytes, gzip 6), shading.npz, ccd_mask.bin, scan.json, library/index.json; <out>/...tif\|.jpg (+.dng). Gzip runs with the device open and idle (worker waits in _jobs.get()) |
| 16 | `rps7200/session.py:1348-1360 _filed -> tools/gui.py:3238-3241` | 'filed' event sets result.entry | entry Path | writer thread -> UI | - |
| 17 | `tools/gui.py:4092-4118 _load_full and 3843-3877 _deliver_one (Save As / Save all / roll Export)` | library.corrected(entry) (library.py:338-391): corrections_applied is empty and a reference exists, so apply_shading runs AGAIN on already-corrected pixels; then preview.orient, optional to_monochrome, export.write | doubly flat-fielded (H,W,C) uint16 | background thread (_load_full, Save all, Export) or UI thread (Save As) | user-chosen file (TIFF/JPEG) |

### 2. Single scan from tools/scan.py (incl. --bracket, --reuse, --no-shading)

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/scan.py:119-155` | Parses args; --exposure-scale -> float (one value) or list; clears fast_ir without --ir; validates bracket 2-9; --no-library sets library None | exposure_scale float\|list | main | - |
| 2 | `tools/scan.py:160-168 -> direct.py:572-629` | DirectScanner(debug=False) (RPS7200_DEBUG overridden); inquiry printed; ensure_shading(reference, reuse=--reuse, skip=--no-shading) | ShadingReference from calibration or cache | main | calibration/shading.npz rewritten on measure; nothing in the entry says the reference was reused |
| 3 | `rps7200/direct.py:2519-2600 (with --no-shading --auto-exposure)` | The metering probes call scan() with shading=True; with no reference in the session this triggers a lazy calibrate_shading() inside the first probe although calibration was skipped | - | main | - |
| 4 | `tools/scan.py:222-235 (single)` | s.scan(..., shading=not --no-shading, keep_raw=library is not None); hold(): last_pixels_raw (raw) + capture_record() + meta + inquiry into `pending` in memory | raw (H,W,C), raw bytes, reference, mask, meta | main | nothing yet (device open) |
| 5 | `tools/scan.py:196-221 -> direct.py:2323-2421 scan_bracket, 2268-2321 bracket_ladder` | scales = explicit list \| auto_exposure \| [1,1,1] (a single-float --exposure-scale becomes None and auto_exposure False -> [1,1,1]); geomspace ladder to timer ceiling; one scan per multiplier (IR only on the last); bracket_* meta keys; on_pass -> hold(capture_record()); retain keeps every corrected pass | frames list[(H,W,C)], ratios, metas | main | nothing yet |
| 6 | `tools/scan.py:237-246 -> library.save` | After the with-block closes the device, files each pending pass (raw pixels, meta, FilmNotes from CLI, tags, reference, ccd_mask, raw, raw_layout; inquiry passed and ignored) | per-pass entry | main | library/<id>/{scan.tif raw (realigned at 7200 dpi), raw.bin.gz, shading.npz (absent with --no-shading), ccd_mask.bin, scan.json}; bracket_* and inquiry dropped |
| 7 | `tools/scan.py:248-285` | merge_bracket on corrected visible channels (+IR plane of last pass); optional mono; export.write --out (default ./scan.tif, overwritten); <out>.json of meta | merged image | main | --out file and <out>.json (not in the library) |

### 3. Roll: walk -> frame edges -> contact sheet -> approved.json -> commissioned roll -> FrameWriter -> export

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:2077-2158 on_roll (button or 'roll' key; the key path has no busy check)` | Confirms; for a dry run clears survey/orientations/sheet_state/_sheet_roll and edge_watch.begin(film); submits Roll(dry_run, frames, start_at, prescan_resolution, name=raw 'roll' field, notes, tags); submit() clears any pending stop | Roll dataclass | UI | - |
| 2 | `rps7200/session.py:1608-1760 _roll` | seek() to start_at; name = job.name or date (unsanitised); out = rolls/<name>; last_roll_dir=out; dry run -> survey.json never merged; real -> earlier roll.json read (unreadable -> {} silently) and renumbered; manifest dict; scanner.scan_roll(keep_raw=True, edge_reader) | manifest dict | scanner thread | rolls/<name>/ created (after the film has already been moved by seek) |
| 3 | `rps7200/direct.py:3292-3690 scan_roll` | baseline=get_gain_offset(); per frame: position()/place_on_strip; prescan(resolution=prescan_resolution, keep_raw) with film defaulting to 'negative' (roll film not passed); raw_prescan=last_pixels_raw, prescan_meta; frame_contrast (blank -> end); registration marks; walk.observe only when correct/correct_dry_run; approved -> _hold_to_approved (2836-2961; verification prescans, the last replaces prescan and raw_prescan, no prescan_before); else correct -> _aim_frame (2963-3059, prescan_before kept); dry run -> yield RollFrame(prescan, raw_prescan, prescan_meta, prescan_before) | RollFrame; prescans (H,W,3) uint8 corrected + raw | scanner thread (generator) | - |
| 4 | `rps7200/session.py:1776-1845` | _deliver prescan Result; dry run: _file(rf.prescan, prescan_meta, raw_image=rf.raw_prescan, frame label '<name>-NN', path=rolls/<name>/prescanNN.tif, kind='prescan'); prescan_before -> _file(file_entry=False, path=prescanNN-before.tif) | job dicts | scanner -> writer thread | via FrameWriter: rolls/<name>/prescanNN.tif (corrected, oriented by session rotation/flip, overwritten on a second walk), prescanNN-before.tif (no library entry), one library entry per walk prescan (raw 8-bit scan.tif + raw.bin.gz + shading.npz + ccd_mask.bin), out_dir/prescans copy |
| 5 | `rps7200/session.py:1894-1926` | Frame record (number, index, transport_position, registration, error, done, prescan name) replaces any earlier one; manifest rewritten with write_text after every frame | JSON | scanner thread | rolls/<name>/survey.json (replaced wholesale by every walk into the folder; non-atomic) |
| 6 | `tools/gui.py:3497-3505 remember_arrangement -> tools/frame_edges/watch.py EdgeWatch` | Each surveyed prescan working copy (unoriented, <=1400 px) appended to self.survey and handed to EdgeWatch.add | (number, working copy) | UI -> frame-edges thread | - |
| 7 | `tools/gui.py:3243-3260, sheet code, tools/frame_edges/propose.py propose_centred` | On 'finished' of a walk: _sheet_roll = session.last_roll_dir; sheet opens; proposals (mm) merged with operator positions | offsets dict[int,float mm], sources | UI | gui-settings.json 'sheet' section |
| 8 | `tools/gui.py:2723-2864 on_scan_chosen, 2929-2967 _write_approved` | Busy check; Approved(number, offset_mm, rotation, flipped, reference=working copy, reference_entry, source); approved.json written to rolls/_safe(roll field or '') -- not _sheet_roll/last_roll_dir; submits Roll(only, approved, start_at, predpi pinned to survey, name=raw roll field) | Approved tuple | UI | rolls/<_safe(name)\|'roll'>/approved.json (offset_mm 4 dp); non-atomic |
| 9 | `rps7200/direct.py:3612-3660` | meter: set_gain_offset(baseline) + auto_exposure (probes not filed); set_gain_offset(baseline); scan(exposure_scale=scales, keep_raw) -> meta exposure_metered=False and no 'metering'; roll_index/roll_position/registration added; yield RollFrame(image corrected, raw_image=last_pixels_raw, prescan corrected, raw_prescan) | (H,W,3\|4) uint16 corrected + raw | scanner thread | - |
| 10 | `rps7200/session.py:1846-1893 -> _file -> FrameWriter._write` | _note_reversal; _deliver; _file(raw_image=rf.raw_image, prescan=rf.prescan (corrected), prescan_meta, frame label '<name>-NN', path=rolls/<name>/frameNN.tif, mono) | job dict | scanner -> writer thread (gzip overlaps next frame) | rolls/<name>/frameNN.tif (corrected, oriented, optional mono, overwritten on rescan), out_dir copy (_unclaimed), library entry (scan.tif raw, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif CORRECTED 8-bit unlabelled, scan.json with registration; roll_index/roll_position dropped); raw_prescan not filed |
| 11 | `rps7200/session.py:1894-1926` | Per-frame record incl. exposure/gain/offset; manifest rewritten per frame; no library entry id | JSON | scanner thread | rolls/<name>/roll.json (merged with earlier; non-atomic) |
| 12 | `tools/gui.py:4979-5008 roll_entry_index, 5104-5128 rolls_on_disk, 4871-4906 roll_exports, 2383-2451 on_export_rolls` | Globs library/*/scan.json and keys entries by film.frame.rpartition('-') -> (roll name, number); the last entry in glob order wins, and walk-prescan entries share the key with frame entries; export re-corrects the chosen entry with library.corrected | dict[roll][number] -> entry path | UI (index) / export-rolls thread | exported TIFF/JPEG files |
| 13 | `tools/scan_roll.py:274-605 (CLI equivalent)` | Fresh manifest every run (roll.json/survey.json overwritten after seek+calibrate); --no-shading only skips the up-front calibration (scan_roll has no shading parameter) so the first prescan calibrates lazily; dry run writes prescanNN.tif / -before.tif with tiff.write and files NOTHING in the library; real frames -> FrameWriter.submit(raw_image, prescan corrected, capture=s.capture_record(), frame='<roll>/<NN>') with no shape guard; --approved -> hold_from_walk reads prescans without un-orienting; writer.finish reached only after normal exit or an Exception (not KeyboardInterrupt) | as above | main + FrameWriter thread | rolls/<roll>/{roll.json\|survey.json, frameNN.tif, prescanNN.tif}; library entries for real frames only; manifest records entry paths after finish |

### 4. Demo (--demo): DemoScanner at the session seam

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `tools/gui.py:8156-8199 main` | home=demo/: session root demo/library (or --library), reference demo/calibration/shading.npz, rolls demo/rolls (or --rolls); session._open_scanner = DemoScanner(source, entry, no_film=--look-only, libraries_beside, cache=demo/pictures.npz). --look-only without --demo only reaches the window | - | UI | demo/gui-settings.json |
| 2 | `rps7200/demo.py:281-305 open, _sign_pictures` | Lists source entries, picks best_pair, signs pictures on a thread | 24x48 grey grids | demo-pictures thread | demo/pictures.npz |
| 3 | `rps7200/demo.py:444-452 ensure_shading` | Sleeps; returns 'loaded'/'calibrated' with no 'reference' key (session treats it as calibrated) and reads/writes no file | summary dict | scanner thread | nothing |
| 4 | `rps7200/demo.py:454-477 prescan -> 1164-1190 _pair_image` | Reads the source entry's prescan.tif (corrected, no bytes) WITHOUT resetting self._capture, or decodes the scan's raw and resamples (drops raw, keeps reference/mask); _as_positioned rolls columns by the simulated offset; _read_as_carriage encodes and decodes via DirectScanner.decode_index; meta has no shading report/exposure/protocol_revision | (H,W,3) already corrected; no last_pixels_raw attribute | scanner thread | - |
| 5 | `rps7200/demo.py:530-597 scan -> 955-1022 _decode` | library.read_raw -> DirectScanner._deinterleave (or scan.tif when no raw) -> apply_shading whenever a reference exists and calibration.skipped is unset, regardless of shading=False or corrections_applied; capture = source entry's raw/layout/reference/mask; no _as_positioned; no ShadingUnavailable width check | (H,W,3\|4) corrected; meta{demo:True, shading report or None} | scanner thread | - |
| 6 | `rps7200/session.py:1407-1458 / 2031-2141` | Unchanged session path: raw_image None (scan) or getattr(last_pixels_raw)=None (prescan), so corrected pixels go to library.save as raw; guard drops raw when layout disagrees but keeps the possibly stale reference and mask | job dict | scanner -> writer thread | demo/library/<id>/ with corrected scan.tif labelled raw, the SOURCE entry's raw.bin.gz when shapes match, shading.npz, ccd_mask.bin (possibly from a previous decode) |
| 7 | `rps7200/demo.py:599-797 scan_roll` | Own loop borrowing roll_ends/place_on_strip/_hold_to_approved/_aim_frame/param_for_mm: prescan at default 300 dpi (prescan_resolution swallowed by **kw), hold called with 300 and no should_stop, no blank-contrast end, no per-frame except/max_failures, no metering, walk.observe only on unheld frames; RollFrame without raw_image/raw_prescan/prescan_before and meta without registration | RollFrame | scanner thread | through session as above |

### 5. Offline: library entry -> corrected / reconstruct / verify / migrate / duplicates

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `rps7200/library.py:314-335 load` | scan.json + tiff.read(scan.tif) + ShadingReference.load(shading.npz) + ccd_mask.bin bytes | (image, record{reference, ccd_mask}) | caller | - |
| 2 | `rps7200/library.py:338-391 corrected` | 'shading' in corrections_applied -> 'already'; calibration.skipped -> raw; no reference -> raw; else apply_shading(image, reference, mask or 1:1) | corrected image + record['corrected'] | caller (GUI load/save/export threads, analysis tools) | - |
| 3 | `rps7200/library.py:394-436 read_raw / decode_raw` | gunzip raw.bin.gz (errors -> None); ScanParameters from raw.layout with filter offsets 0; DirectScanner._deinterleave (= decode_index, upright; no 7200 realignment) | bytes / (H,W,C) | caller | - |
| 4 | `rps7200/library.py:439-515 reconstruct; tools/library.py:90-113` | decode_index of raw; legacy 'shading' entries re-shaded; compares shape, recorded direction, equality or rows-reversed equality | verdict str | main | - |
| 5 | `rps7200/library.py:776-816 verify; tools/library.py:83-88` | scan.tif sha256, existence of shading/ccd_mask/raw, raw sha256; reports explicit shading=False entries as 'correction was asked for'; no checksum for shading.npz/ccd_mask.bin/prescan.tif | problem strings | main | - |
| 6 | `tools/library.py:137-216 migrate-raw` | Rewrites scan.tif with decode_raw whenever it differs and shapes match, without checking the stored pixels derive from these bytes and without backup | (H,W,C) | main | scan.tif and scan.json rewritten in place (non-atomic) |
| 7 | `rps7200/library.py:518-636 migrate_direction; tools/library.py:218-242` | Records read_direction; turns bottom-up scan.tif/prescan.tif upright when decisive | - | main | scan.tif/prescan.tif/scan.json rewritten with --write |
| 8 | `rps7200/library.py:639-738 signature/duplicates/prunable; tools/library.py:115-135` | Groups by (stock, frame, subject, dpi, channels, frame window, depth, film, protocol_revision, commanded exposure, fast_infrared); --delete shutil.rmtree's all but the most useful, no confirmation | groups | main | entries deleted; index.json rebuilt |
| 9 | `tools/make_comparison.py:92-139` | Reads an arbitrary TIFF (default scans/negatives/state_1800dpi.tif) and a flat, destripes; does not touch the library, raw bytes or apply_shading | TIFFs | main | 1_nothing_done.tif, 2_corrected.tif, 3_corrected_inverted.tif, previews/cmp_*.png |

### 6. Settings and calibration cache

| # | Where | What happens | Data | Thread | Persists |
|---|---|---|---|---|---|
| 1 | `rps7200/direct.py:563-570 save_shading (from ensure_shading 612-615: session._calibrate, tools/scan.py:167, tools/scan_roll.py:166-173)` | np.savez_compressed of the current ShadingReference when one exists | float64 ref/dark per channel, means, pixels_per_line | scanner thread / main | calibration/shading.npz (cwd-relative; overwritten each measured calibration; no mask, no provenance; non-atomic) |
| 2 | `rps7200/direct.py:549-561 load_shading` | Loads the cached reference; the mask comes from the next pass | ShadingReference | scanner thread / main | - |
| 3 | `tools/gui.py:1995-2004 ask_to_calibrate` | Reads cache mtime to show its age and offers 'Use the cached one' | age hours | UI | - |
| 4 | `rps7200/settings.py:58-97; tools/gui.py _remember` | Load/save window state; save is .part + replace (atomic) | controls, film, output, window, presets, shortcuts, rolls, sheet | UI | gui-settings.json \| $RPS7200_SETTINGS \| demo/gui-settings.json |


## Findings the dataflow reader made along the way

These are covered by the problem files; listed here for completeness.

- **critical** GUI single scans are filed with CORRECTED pixels labelled raw; the full-resolution view, Save As, Save all and Export flat-field them a second time (`rps7200/session.py:1440-1458, rps7200/session.py:1413-1415`)
- **high** Roll export joins library entries by film.frame and cannot tell a walk's prescan from the frame scan; the winner is arbitrary glob order (`tools/gui.py:4979-5008, tools/gui.py:5119-5128`)
- **high** scan_roll.py --no-shading (and scan.py --no-shading --auto-exposure) forces a lazy calibration inside the first pass -- the path the tool itself records as stalling the device (`tools/scan_roll.py:145-146, tools/scan_roll.py:166-173`)
- **high** Calibration bytes are thrown away; stored shading.npz cannot be recomputed with newer code (`rps7200/direct.py:1755-1760, rps7200/direct.py:1946-1975`)
- **high** Roll frames and bracket passes lose their metering evidence and are recorded as commanded (exposure_metered=False) (`rps7200/direct.py:3627-3652, rps7200/direct.py:2791`)
- **high** Debug filing attaches a previous pass's raw bytes to any pass that did not ask keep_raw (`rps7200/direct.py:1518-1531, rps7200/direct.py:631-647`)
- **high** `tools/library.py duplicates --delete` deletes scans of different photographs when film notes are empty (`rps7200/library.py:639-698, rps7200/library.py:714-738`)
- **high** approved.json is written to a different folder than the roll it belongs to (`tools/gui.py:2929-2967, rps7200/session.py:1634-1637`)
- **high** tools/scan_roll.py overwrites roll.json when a roll is resumed (`tools/scan_roll.py:293-329, tools/scan_roll.py:446-449`)
- **high** CLI dry-run walks file nothing in the library (`tools/scan_roll.py:490-510, rps7200/session.py:1804-1831`)
- **medium** Entries do not record which calibration produced their reference, or that it was reused from the cache (`rps7200/direct.py:595-606, rps7200/library.py:281-295`)
- **medium** A frame's prescan is stored corrected and unlabelled; arrival and verification prescans of held frames are never kept (`rps7200/session.py:1885-1889, tools/scan_roll.py:562`)
- **medium** Roll and hold-loop prescans record film 'negative' whatever the roll's film (`rps7200/direct.py:1682-1687, rps7200/direct.py:3494-3496`)
- **medium** 7200 dpi scan.tif is realigned but the record does not say so; reconstruct and migrate-raw can never validate it (`rps7200/direct.py:2692-2702, rps7200/direct.py:2566-2581`)
- **medium** ScanSession guard drops the raw bytes of a short read, the case where they matter most (`rps7200/session.py:2072-2099, rps7200/direct.py:1508-1510`)
- **medium** library.save discards inquiry, caller meta and capture time; scan() does not record protocol overrides (`rps7200/library.py:131-147, rps7200/library.py:166`)
- **medium** GUI single scans and prescans gzip their raw bytes while the device is open and idle (`rps7200/session.py:1013-1030, rps7200/session.py:1085-1101`)
- **medium** Manifests and entry records are written non-atomically; a corrupt roll.json is silently discarded on resume (`rps7200/session.py:1650-1654, rps7200/session.py:1924-1926`)
- **medium** scan_roll.py: Ctrl-C abandons the read and skips writer.finish(), losing queued frames (`tools/scan_roll.py:587-605, tools/scan_roll.py:350-353`)
- **medium** A second walk into the same roll folder overwrites survey.json and the prescans (`rps7200/session.py:1634-1650, rps7200/session.py:1817`)
- **medium** The roll keyboard shortcut bypasses the busy guard, and any submit clears a pending stop (`tools/gui.py:740, tools/gui.py:2077-2158`)
- **medium** DemoScanner runs its own roll loop that diverges from DirectScanner.scan_roll (`rps7200/demo.py:599-797, rps7200/demo.py:690`)
- **medium** Demo passes return corrected pixels without last_pixels_raw, ignore shading=False and the 7200 dpi refusal, and leave stale calibration in the capture (`rps7200/demo.py:454-477, rps7200/demo.py:1174-1190`)
- **medium** The comparison files do not exercise the driver's correction path (`tools/make_comparison.py:92-139`)
- **medium** scan_roll.py --approved does not un-orient GUI walk prescans (`tools/scan_roll.py:243-252, tools/gui.py:4724-4740`)
- **medium** GUI roll export cannot find CLI-scanned roll entries (film.frame format differs) (`tools/scan_roll.py:577, tools/gui.py:4979-5008`)
- **medium** 'File every scan' is not true of the tools or the window: metering probes and hold passes are never filed, even with RPS7200_DEBUG=1 (`rps7200/session.py:1265-1271, tools/scan.py:160`)
- **medium** Debug spool is unrecoverable after a hard kill (`rps7200/direct.py:650-692, rps7200/direct.py:694-770`)
- **low** Demo scan ignores the simulated film position; backlash constant retyped (`rps7200/demo.py:565-592, rps7200/demo.py:469`)
- **low** Roll folder name is used unsanitised, and only after the film has already been moved (`rps7200/session.py:1618-1636, tools/gui.py:2156`)
- **low** verify reports deliberately raw scans as 'correction was asked for' (`rps7200/library.py:794-802, rps7200/library.py:359-364`)
- **low** --look-only without --demo drives the real scanner while the window says there is no film (`tools/gui.py:8146-8148, tools/gui.py:8194-8199`)
- **low** README describes reversal-by-prescan turning that the code no longer does (`rps7200/session.py:1481-1534`)
- **low** Transport-distance documentation disagrees with the code and with itself (`rps7200/protocol.py:260-292, tools/scan_roll.py:108-114`)
- **low** scan.py --bracket silently ignores a single-value --exposure-scale (`tools/scan.py:143-147, tools/scan.py:211-215`)
- **low** 7200 dpi is offered but always refused, and a roll only refuses per frame after prescan, metering and holds (`tools/gui.py:1748-1749, tools/gui.py:2780-2781`)
- **low** No checksums for shading.npz, ccd_mask.bin or prescan.tif (`rps7200/library.py:182-185, rps7200/library.py:281-303`)
- **low** Calibrate button starts a calibration without asking whether film is loaded (`tools/gui.py:1929-1936`)
- **low** Library, calibration cache and roll paths are relative to the current directory (`rps7200/library.py:51, rps7200/session.py:1131-1136`)
- **low** Quitting mid-roll waits for the whole roll and every queued job (`tools/gui.py:3039-3050, rps7200/session.py:1246-1248`)
- **info** auto_exposure comment says SET GAIN OFFSET readback persists and compounds, contradicting the measured protocol notes (`rps7200/direct.py:2063-2067, rps7200/direct.py:3632-3640`)
- **info** Layering: library depends on direct, with a lazy import cycle back (`rps7200/library.py:48, rps7200/direct.py:705-706`)
