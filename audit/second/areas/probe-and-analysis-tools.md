# Probe and analysis tools

Area key `probe-and-analysis-tools`. 23 findings: 7 medium, 15 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Probe and analysis tools (13 files, read in full; calls followed into rps7200/direct.py, framing.py, library.py, shading.py, session.py, protocol.py, uniformity.py, console.py and the measure-scan-quality metrics.py). The eight hardware probes are hold_probe, transport_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth and roll_registration_walk. None of them can complete a pass any more. Since 1492d2c (audit P15), scan() refuses any pass that wants correction when the session has no reference. Every probe's first pass is such a pass, through prescan() or auto_exposure(), whose default is shading=True, and no probe calls ensure_shading. The fakes in the tests hide this. Seven of the probes gate on RPS7200_DEBUG, but they test whether the variable is set at all. DirectScanner only turns filing on for 1/true/yes/on, so RPS7200_DEBUG=0 passes the gate and files nothing. None of the probes uses DeferredInterrupt, so Ctrl-C in any of them abandons a read. The cleanup paths in byte14_probe and gain_probe also send setup and write commands to a device that is already marked suspect. exposure_probe's --only chunking gives frames the wrong numbers, and each chunk overwrites the JSON file that links passes to library entries. No probe sends SET_SCAN_HEAD. transport_truth claims it never sends SLIDE_INIT, but every prescan it takes sends SLIDE 10 16 00 00. Millimetres appear everywhere in operator-facing output, along with stale minimum-step figures. The analysis tools run against the current library format: flat entries with scan.json, raw.layout, calibration.shading/ccd_mask, device_settings and raw.bin(.gz) all readable. Several of them, however, measure something other than what they claim. exposure_headroom anchors on the whole frame, not the metering region. registration_margin checks one orientation with a pixel dy gate, while the production gate checks both orientations with a millimetre gate. dpi_analysis and linearity compare passes without registering them. registration_margin and roll_registration_study read rolls/ prescans as they lie on disk, turned or mirrored. roll_registration_study's delivery ratio divides magnitudes, the mistake CLAUDE.md names. Two tools, registration_margin and linearity, do not check library.corrected's state.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [PAT-01](#probe-and-analysis-tools-pat-01) | medium | bug | confirmed | Every hardware probe fails on its first pass: none acquires a shading reference, and scan() now refuses a corrected pass without one |
| [PAT-03](#probe-and-analysis-tools-pat-03) | medium | hardware-safety | confirmed | No probe defers Ctrl-C: interrupting any of them abandons the read in flight, which risks a wedge |
| [PAT-04](#probe-and-analysis-tools-pat-04) | medium | hardware-safety | confirmed | Cleanup in byte14_probe and gain_probe sends write and setup commands to a device already marked suspect |
| [PAT-05](#probe-and-analysis-tools-pat-05) | medium | user-error | confirmed | exposure_probe --only chunking scans the wrong physical frames under plan numbers, and each chunk overwrites the JSON that joins passes to the library |
| [PAT-06](#probe-and-analysis-tools-pat-06) | medium | bug | confirmed | exposure_headroom anchors 'where metering landed' on the whole window, not the metering region, and models blue with negative-film constants for every film |
| [PAT-09](#probe-and-analysis-tools-pat-09) | medium | bug | confirmed | linearity divides unregistered passes pixel by pixel, and --corrected does not check what library.corrected returned |
| [PAT-10](#probe-and-analysis-tools-pat-10) | medium | bug | confirmed | registration_margin and roll_registration_study read rolls/*/prescan*.tif as they lie on disk, possibly turned or mirrored, and the glob also picks up prescanNN-before.tif |
| [PAT-02](#probe-and-analysis-tools-pat-02) | low | data-integrity | confirmed | The RPS7200_DEBUG gate accepts any non-empty value, but DirectScanner files only for 1/true/yes/on, so RPS7200_DEBUG=0 runs the probe and files nothing |
| [PAT-07](#probe-and-analysis-tools-pat-07) | low | bug | partly | registration_margin, the tool named for re-fitting CONFIDENCE_FLOOR, measures a different statistic from the production gate |
| [PAT-08](#probe-and-analysis-tools-pat-08) | low | bug | partly | dpi_analysis takes its noise floor from an unregistered repeat pair, and its 'one domain' check accepts old-code corrections |
| [PAT-11](#probe-and-analysis-tools-pat-11) | low | bug | confirmed | roll_registration_study.report_held divides magnitudes for the delivery ratio, trusts arrivals below the confidence floor, and hardcodes 428 columns |
| [PAT-12](#probe-and-analysis-tools-pat-12) | low | doc-mismatch | partly | roll_registration_study's geometry uses the retired 36.0 mm frame model and a retyped BASE_TOLERANCE that differs from production |
| [PAT-13](#probe-and-analysis-tools-pat-13) | low | doc-mismatch | confirmed | transport_truth's docstring says it never sends SLIDE_INIT, but every prescan it takes sends SLIDE 10 16 00 00 |
| [PAT-14](#probe-and-analysis-tools-pat-14) | low | design | confirmed | transport_probe is an ungated bypass whose detector cannot see film motion on a loaded strip |
| [PAT-15](#probe-and-analysis-tools-pat-15) | low | hardware-safety | confirmed | gain_probe --ladder and byte14_probe --ladder values are unbounded below and, for gain, above; out-of-range gains wrap and are recorded unwrapped |
| [PAT-16](#probe-and-analysis-tools-pat-16) | low | data-integrity | partly | roll_registration_walk records requested ladder positions, not commanded ones, leaves the film about 0.07 mm off home, overwrites a corpus on label reuse, and cannot be joined to its library entries |
| [PAT-17](#probe-and-analysis-tools-pat-17) | low | doc-mismatch | confirmed | Probes and studies print and take millimetres, and carry stale minimum-step figures |
| [PAT-18](#probe-and-analysis-tools-pat-18) | low | bug | confirmed | exposure_probe re-detects the metering region on each bright delivered pass, which auto_exposure deliberately avoids, and retypes the under-target tolerance |
| [PAT-19](#probe-and-analysis-tools-pat-19) | low | demo-divergence | partly | Offline analysis tools do not exclude demo entries, prescans or metering passes when they select from the library |
| [PAT-A1](#probe-and-analysis-tools-pat-a1) | low | data-integrity | found-by-verifier | roll_registration_walk overwrites the driver's commanded distance with the requested one, and the restore comment claims a measurement the code does not make |
| [PAT-A2](#probe-and-analysis-tools-pat-a2) | low | doc-mismatch | found-by-verifier | hold_probe tells the operator the registration floor is 40 when CONFIDENCE_FLOOR is 55 |
| [PAT-A3](#probe-and-analysis-tools-pat-a3) | low | error-handling | found-by-verifier | exposure_headroom aborts the whole study on one unreadable scan.json |
| [PAT-20](#probe-and-analysis-tools-pat-20) | info | doc-mismatch | confirmed | The docstrings of fast_ir_probe and byte14_probe describe driver behaviour that has since changed |

## Findings in full

<a id="probe-and-analysis-tools-pat-01"></a>

### PAT-01 -- Every hardware probe fails on its first pass: none acquires a shading reference, and scan() now refuses a corrected pass without one

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/hold_probe.py:130-136`, `tools/transport_probe.py:57`, `tools/transport_probe.py:76-81`, `tools/byte14_probe.py:127-135`, `tools/gain_probe.py:129-139`, `tools/fast_ir_probe.py:192-200`, `tools/fast_ir_probe.py:441-444`, `tools/exposure_probe.py:237-253`, `tools/transport_truth.py:122-125`, `tools/roll_registration_walk.py:107-109`, `tools/roll_registration_walk.py:262-263`, `rps7200/direct.py:2053-2059`, `rps7200/direct.py:2397`, `rps7200/direct.py:2948-2963`

**Doc claim:** tools/hold_probe.py:105 and :135 'calibrate if this session has none (~2 min)' / '(a calibration runs first if this session has none -- ~2 min)'; tools/transport_probe.py:14-16 '25 s per probe, no shading calibration needed'; tools/exposure_probe.py:8-9 'calibration happens with it in'

Commit 1492d2c (2026-09-24, audit P15) turned the lazy in-pass calibration into a ShadingUnavailable refusal and made shading reach prescan and auto_exposure. The eight probes were last changed on 2026-09-20/21 and still rely on the removed behaviour. Every probe's first scan is a corrected one. hold_probe, transport_probe, transport_truth and roll_registration_walk start with prescan(); byte14_probe, gain_probe, fast_ir_probe and exposure_probe start with auto_exposure(). byte14, gain and fast_ir pass shading=False only to their ladder scans, not to the metering. So each probe opens the device, runs session_start and waits for warm-up, then raises before its first pass. The tests use fakes (tests/test_registration_walk.py FakeScanner), so nothing catches this.

**Evidence (from the code):**

```text
direct.py:2948-2963 `if self._shading is None or needed > self._shading.pixels_per_line: ... raise self.uncalibrated(reason)`; prescan() default `shading: bool = True` (2059); auto_exposure default `shading: bool = True` (2397) and its probes call `self.scan(..., shading=shading, ...)` (2499). No probe calls ensure_shading/load_shading/calibrate_shading (grep over all eight returns nothing). e.g. hold_probe.py:130-136 `scanner.session_start(); scanner.wait_warm(); ... reference, _ = scanner.prescan(resolution=args.resolution, keep_raw=True)`; byte14_probe.py:134 `scales = list(scanner.auto_exposure(resolution=args.resolution, film=FILM_NEGATIVE, infrared=False))`; transport_probe.py:57 `image, _ = scanner.prescan(resolution=dpi)`.
```

**Failure scenario:** Stefan agrees to a hold_probe run with film loaded. The probe sends INQUIRY, 0xE7, REQUEST SENSE and SLIDE 00 01 00 04, waits out the lamp, and dies with `ShadingUnavailable: no shading reference in this session`. exposure_probe, fast_ir_probe and gain_probe swallow the RuntimeError, print 'probe stopped' and exit 1 with nothing measured. An operator who 'fixes' this by passing shading=False changes what is measured: metering on raw pixels.

**Fix:** Call scanner.ensure_shading(<cache path>, reuse=...) after wait_warm() in every probe, film loaded as CLAUDE.md requires, and state the calibration cost in each --dry-run. Add one test per probe that runs main() against a stand-in whose scan() enforces the same refusal as DirectScanner.scan, for example the DemoScanner seam.

<details><summary>Second reader's check</summary>

direct.py:2934-2963: when shading is true and self._shading is None, scan() raises self.uncalibrated(reason). This happens before metering and before anything is sent, and was introduced by 1492d2c on 2026-09-24. prescan() defaults shading=True (2059) and passes it through to scan(). auto_exposure defaults shading=True (2397), and its probes call self.scan(..., shading=shading) (2499). _shading is set only by calibrate_shading, load_shading, ensure_shading or the setter (701, 720, 2351). None of the eight probes calls any of these; grep for ensure_shading, load_shading and calibrate returns only hold_probe's printed claim at 105/135. Every probe's first corrected pass therefore raises ShadingUnavailable, a RuntimeError subclass (protocol.py:345). Four probes swallow it with 'probe stopped' and exit 1: exposure_probe, fast_ir_probe and its sweep, and gain_probe. roll_registration_walk swallows it as 'failed:'. byte14_probe re-raises it, because RuntimeError is not in its tuple, but only after its finally block raises a NameError on `scales`. hold_probe and transport_truth propagate it. transport_probe raises it from look() before any move. Last touched on 2026-09-20/21, the probes predate the refusal. The tests only exercise planning and report helpers, never main() against a refusing stand-in. No data is lost and no hardware is harmed; the cost is a warm-up and a wasted request for Stefan's time. Medium is right.

</details>

<a id="probe-and-analysis-tools-pat-03"></a>

### PAT-03 -- No probe defers Ctrl-C: interrupting any of them abandons the read in flight, which risks a wedge

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/console.py:50-62`, `tools/roll_registration_walk.py:315-321`, `tools/gain_probe.py:207-208`, `tools/fast_ir_probe.py:252-255`, `tools/exposure_probe.py:312-329`, `tools/byte14_probe.py:172-175`, `tools/hold_probe.py:221-224`, `tools/transport_truth.py:177-181`, `tools/fast_ir_probe.py:173-174`, `tools/exposure_probe.py:213`

**Doc claim:** CLAUDE.md 'Never abandon a read mid-scan ... Ctrl-C in tools/scan.py and tools/scan_roll.py finishes the pass in flight and stops there'

CLAUDE.md says 'Never abandon a read mid-scan' and that Ctrl-C in the scan tools finishes the pass in flight. The probes are the longest runs in the repository, 25-40 minutes, and they install no handler. A single Ctrl-C lands inside read_planes. _read_pass marks the device suspect, and the scanner may be left mid-scan until it is power cycled. The same happens when a foreground run is killed at the harness's 10-minute limit: exposure_probe and fast_ir_probe mention backgrounding only on a dry run, and byte14_probe runs about 8-9 minutes with no warning.

**Evidence (from the code):**

```text
console.py:50-58 `class DeferredInterrupt: Ctrl-C asks the pass in flight to finish rather than abandoning it. A KeyboardInterrupt raised inside a read unwinds ... the abandoned read that needs a power cycle.` A grep for DeferredInterrupt finds only tools/scan.py and tools/scan_roll.py. The walk handles Ctrl-C with `except KeyboardInterrupt: print("\ninterrupted -- the film is wherever the last move left it") ... return 130`. The 'background it' advice appears only in --dry-run output: fast_ir_probe.py:173-174 and exposure_probe.py:213; the real runs print no estimate. byte14_probe (13 ladder passes plus a final pass at 600 dpi, plus metering) prints no estimate at all.
```

**Failure scenario:** Twenty minutes into fast_ir_probe, Stefan presses Ctrl-C to stop it. The untied or tied RGBI read is abandoned, the probe prints 'probe stopped: KeyboardInterrupt', the scanner stays busy, and the next session needs a power cycle. The spooled passes do get filed at close(). The run is lost, and so is the device's state.

**Fix:** Wrap each probe's pass loop in `with DeferredInterrupt() as stop:` and check stop.requested() between passes, as tools/scan.py does. Print the time estimate and the backgrounding advice on the real run too, and give byte14_probe an estimate.

<details><summary>Second reader's check</summary>

The only users of DeferredInterrupt are tools/scan.py:253 and tools/scan_roll.py:473 (console.py:50-100 defines it). No probe installs it. A Ctrl-C inside read_planes reaches _read_pass's `except BaseException` (direct.py:3280-3288), which calls _mark_suspect and re-raises, abandoning the pass. transport_probe (not in the cited list) is the same: its `with DirectScanner(...)` block closes the transport under the read. Estimates appear only on dry runs for exposure_probe (213) and fast_ir_probe (173-174). byte14_probe's dry run prints only the pass count, with no time estimate, and the real run prints none either. Note that PAT-01 currently stops every probe before its first pass, so this becomes live once PAT-01 is fixed.

</details>

<a id="probe-and-analysis-tools-pat-04"></a>

### PAT-04 -- Cleanup in byte14_probe and gain_probe sends write and setup commands to a device already marked suspect

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/byte14_probe.py:176-188`, `tools/gain_probe.py:209-221`, `rps7200/direct.py:1709-1733`, `rps7200/direct.py:3016-3062`, `rps7200/direct.py:662-671`

**Doc claim:** CLAUDE.md 'sets DirectScanner.suspect, and from then on the session refuses everything that would drive the device (DeviceSuspect); status queries still go through'

The suspect state promises to refuse 'everything that would drive the device', and in practice scan() refuses only once it reaches SLIDE_INIT. byte14_probe's unconditional 'final pass ... for the device's sake' therefore sends five writes plus a MODE SELECT after an abandoned read (a Ctrl-C, a ScanReadError timeout) before it is refused. gain_probe's restore sends WRITE GAIN OFFSET to the same mid-scan device. On a clean run, byte14_probe's final pass also costs a full extra 600 dpi pass, and when the failure came before metering finished, `scales` is undefined: the NameError is swallowed and reported as 'final default pass failed'.

**Evidence (from the code):**

```text
byte14_probe.py:181-185, inside `finally:` whatever the exception: `scanner.scan(resolution=args.resolution, infrared=False, frame=FULL_FRAME, exposure_scale=scales, auto_exposure=False, shading=False, keep_raw=False, byte14=None)`. In scan(), before the first suspect check at `self.slide(SLIDE_INIT, ...)` (3062), it runs `self.set_exposure_time()` (3016), `self.set_highlight_shadow()` (3017), `self.set_scan_frame(*frame)` (3019), `self.cmd_17(1)` (3024), `self.set_gain_offset(settings, ...)` (3029) and `self.set_mode(...)` (3051). gain_probe.py:215-218 in `finally`: `real_get()` then `scanner.set_gain_offset(reference)`; set_gain_offset (1709-1733) has no `_refuse_if_suspect` check. The only callers of _refuse_if_suspect are slide (1523), start_scan (1623) and calibrate_shading (2178).
```

**Failure scenario:** A byte14 ladder pass times out (ScanReadError, device suspect). The finally block sends SET EXPOSURE, HIGHLIGHT/SHADOW, SCAN FRAME, cmd 0x17, WRITE GAIN OFFSET and MODE SELECT to a scanner that may still be streaming the abandoned pass, the pattern CLAUDE.md associates with wedges.

**Fix:** Skip cleanup that talks to the device when `scanner.suspect` is set. Better, make scan() call _refuse_if_suspect('a scan') at entry and gate set_gain_offset and the other writes the same way in direct.py. Drop byte14_probe's final pass: its own comment says byte 14 is not persisted.

<details><summary>Second reader's check</summary>

byte14_probe.py:176-188 runs scanner.scan(...) unconditionally in finally. In scan(), the only suspect check is inside slide(SLIDE_INIT) (direct.py:3062 -> 1523). Before it come status polls, then set_exposure_time (3016), set_highlight_shadow (3017), set_scan_frame (3019), cmd_17 (3024), get_gain_offset plus set_gain_offset (3028-3029) and set_mode (3051), all sent to a device that _read_pass has just marked suspect. gain_probe.py:209-221 calls real_get() and then set_gain_offset(reference), and set_gain_offset (1709-1733) has no _refuse_if_suspect check. _refuse_if_suspect is called only at 1523, 1623 and 2178. On a clean run the final pass is a 14th full 600 dpi pass. When auto_exposure raised (as it does today, PAT-01), `scales` is unbound and the NameError is printed as 'final default pass failed'.

</details>

<a id="probe-and-analysis-tools-pat-05"></a>

### PAT-05 -- exposure_probe --only chunking scans the wrong physical frames under plan numbers, and each chunk overwrites the JSON that joins passes to the library

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/exposure_probe.py:180-182`, `tools/exposure_probe.py:196-202`, `tools/exposure_probe.py:240-243`, `tools/exposure_probe.py:297-310`, `tools/exposure_probe.py:317-327`

**Doc claim:** tools/exposure_probe.py:180-182 (help for --only) and :277-283 ('The join key back to the library, and the reason `--json` is load-bearing')

By default each chunk rewinds the strip to where that chunk started. So `--only 5-8` after `--only 1-4` rescans physical frames 1-4 and labels them 5-8, and frame 5 even gets the ladder that plan frame 5 is assigned. With --no-rewind on the previous chunk, the next chunk starts on that chunk's last frame, because the last frame of a schedule is never advanced past, which gives an off-by-one. Reusing the same --json path, the natural choice, replaces the earlier chunk's `passes`, which the code calls 'the join key back to the library, and the reason --json is load-bearing'. write_text is not atomic either, so a kill during the rewrite leaves truncated JSON and every earlier pass's record is gone.

**Evidence (from the code):**

```text
The help text says: `"--only" ... help="walk just these frames of the plan, to chunk a run under the harness's ten-minute foreground kill"`. The filter is `schedule = [(n, r) for n, r in schedule if lo <= n <= hi]`, and the loop starts at whatever the transport holds (`here = scanner.position()`) without advancing to frame `lo`. The finally block rewinds by default: `if not args.no_rewind and positions: back = max(positions) - min(positions)`. JSON: `Path(args.json).write_text(json.dumps({... "passes": passes}, ...))`, rewritten whole after every pass and not atomically.
```

**Failure scenario:** Stefan chunks the 40-minute walk as the help text suggests. linearity --probe then reports 'fifteen frames' of which only four are distinct pictures, or it misses chunk 1 altogether because chunk 2 overwrote probe/exposure.json. The library entries still exist but can no longer be matched to frames.

**Fix:** For --only, advance to frame `lo` first (or refuse unless --no-rewind was used and the transport position is recorded), and record the physical transport position as the frame key. Merge into an existing --json rather than overwriting it, and write it with library._write_atomic.

<details><summary>Second reader's check</summary>

exposure_probe.py:195-199 filters the schedule by plan number, and the loop begins at `here = scanner.position()` (241) with no advance to frame lo. The finally block (317-327) rewinds by max(positions)-min(positions) by default, which returns the strip to the chunk's first frame. The last frame of a schedule is never advanced past (the guard is `index < len(schedule) - 1`), so a --no-rewind chunk leaves the transport on its own last frame. plan() puts ladders on (n-1)%4==0, so `--only 5-8` puts a ladder on physical frame 1 again. The JSON is rewritten whole with Path.write_text after every pass (277-285), not atomically and not merged. tests/test_exposure_probe.py::test_a_chunk_fits_under_the_ten_minute_foreground_kill endorses chunking, so this is the documented way to run it.

</details>

<a id="probe-and-analysis-tools-pat-06"></a>

### PAT-06 -- exposure_headroom anchors 'where metering landed' on the whole window, not the metering region, and models blue with negative-film constants for every film

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/exposure_headroom.py:78-80`, `tools/exposure_headroom.py:84-86`, `tools/exposure_headroom.py:182`, `tools/exposure_headroom.py:209-216`, `tools/exposure_headroom.py:89-108`, `tools/exposure_headroom.py:202-204`, `rps7200/direct.py:2506-2523`, `rps7200/direct.py:262-288`

**Doc claim:** rps7200/direct.py:265-277 ('0.80, chosen by measurement with tools/exposure_headroom.py ... worst case 0.001% of blue at 0.80'); tools/exposure_headroom.py:78-79

This is the tool cited as the measurement that chose EXPOSURE_TARGET = 0.80. Its anchor for 'where metering landed' includes any clear aperture or gate in view, which framing.metering_region measured at 5.9-11.1% high. When the gate saturates, achieved reads about 1.0, every simulated k = target/achieved comes out below 1, and the simulation never raises exposure, so the clipping cost of higher targets is understated. The blue model is right only for colour negative. The tool also duplicates the decode instead of using library.decode_raw, skipping the _replay stagger step. It ignores library.corrected's rules and applies a 5172-column reference to wide passes: apply_shading then corrects only the first loc.size columns, while the clip percentage is divided by every sample.

**Evidence (from the code):**

```text
`achieved = [float(np.percentile(base[..., c], PERCENTILE)) / FULL_SCALE for c in range(channels)]` is computed over the full decoded frame, whereas auto_exposure uses `region = metering_slice(image); crop = image[region]; levels = [float(np.percentile(crop[..., c], percentile)) / full ...]` (direct.py:2506-2523). The tool's own comment is `#: The same one auto_exposure uses, so the number here means the same thing as the number there. PERCENTILE = 99.5`, and its report says 'absolutes include the transport gate beside the film, which is clear and saturates whatever the exposure'. Blue: `MEASURED_BLUE_RATIO = 4.98` and `aims[2] = target * MEASURED_BLUE_RATIO / BLUE_RGBI_HEADROOM` use negative film's 5.2, not `blue_rgbi_headroom(film)` (11.0 for positive and Kodachrome). decode() calls `DirectScanner._deinterleave` directly and applies `calibration()`'s reference even where `calibration.skipped` is set.
```

**Failure scenario:** The four newest 16-bit entries are FULL_FRAME passes with the transport visible above and below the film. achieved[R,G] comes out at about 0.95-1.0, the rows for targets 0.70-0.95 all simulate darker passes, and the table shows almost no cost for 0.95. That answer would support raising EXPOSURE_TARGET, and the scans taken with it would clip.

**Fix:** Anchor on `image[metering_slice(...)]`, detected as auto_exposure does it, or better on the entry's own `metering.rounds[-1].levels` where the record has them. Take the blue divisor from blue_rgbi_headroom(film). Decode with library.decode_raw and skip entries that library.corrected would not correct. Re-run the tool before the documented 0.80 derivation is trusted again.

<details><summary>Second reader's check</summary>

exposure_headroom.py:212-216 takes the 99.5th percentile of the whole corrected frame, whereas auto_exposure crops with metering_slice first (direct.py:2506-2523). The tool's own report says the gate 'saturates whatever the exposure' (269-271), so on a FULL_FRAME pass achieved can read near 1.0. k = target/achieved then comes out below 1, which understates clipping at higher targets. Blue: aims[2] uses MEASURED_BLUE_RATIO 4.98 / BLUE_RGBI_HEADROOM (5.2, negative only). blue_rgbi_headroom(film) gives 11.0 for positive, and positive supports infrared, so an RGBI positive entry is modelled wrongly, and for locked films that also moves the shared factor. That part is narrow. decode() duplicates library.decode_raw without _replay (stagger), which does not matter for percentile and clip statistics. It applies the reference whatever calibration.skipped says. For a full-width 7200 dpi pass with a 5172-column reference, apply_shading corrects only the first loc.size columns (shading.py:237-271), while clipped_pct divides by every sample. The '4 newest entries are FULL_FRAME' scenario cannot be checked: there is no library/ in this checkout.

</details>

<a id="probe-and-analysis-tools-pat-09"></a>

### PAT-09 -- linearity divides unregistered passes pixel by pixel, and --corrected does not check what library.corrected returned

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/linearity.py:258-278`, `tools/linearity.py:102-104`, `tools/linearity.py:34-38`, `tools/fast_ir_probe.py:275-281`

**Doc claim:** tools/linearity.py:34-38 ('`--corrected` measures the delivered pixels instead ... what the *file* does'); rps7200/direct.py:282-284 ('departure from linear ... grows to 1.5-1.9% in the 75-95% band')

A ratio per pixel needs the same film position in both passes. Consecutive passes of one frame drift by a line or more, and the tool's own comment says a ratio across pictures 'measures the subject'. Bands are chosen on the bright pass alone, so edge pixels where misregistration pairs bright y with darker x land in the upper bands with inflated ratios, and the reverse in the lower ones. This biases exactly the 70-95% departures that EXPOSURE_TARGET was chosen on. With --corrected, a series mixing applied and raw entries is labelled 'corrected pixels' without a word, the failure dpi_analysis was fixed for. The option also claims to measure what 'the file' does, when it measures a library.corrected recomputation, not a delivered export.

**Evidence (from the code):**

```text
`usable = (x > FLOOR) & (y > FLOOR) & (y < SATURATED * 65535); ... band = usable & (y >= low * 65535) & (y < high * 65535); ratio = float(np.median(y[band] / x[band]))`, with no alignment between the two passes. `return (library.corrected(entry)[0] if corrected else library.decode_raw(entry))` discards the record, so 'deliberately raw', 'no reference' and 'already' entries are measured as if corrected. fast_ir_probe's report found 'the carriage start crept 0/0/-1/-2/-2/-3 lines across six passes ... Three lines is enough to wreck any per-pixel comparison'.
```

**Failure scenario:** On an exposure_probe ladder whose passes drifted 2 lines, a textured frame reports a -1.5% departure in the 80-90% band. It reads as sensor compression and gets cited for the target, although it is registration error.

**Fix:** Register each adjacent pair (uniformity.register plus align) before taking ratios, and report the shift. In corrected mode, check record['corrected'] and refuse a mixed series, as dpi_analysis.out_of_domain does.

<details><summary>Second reader's check</summary>

linearity.departures (258-278) takes np.median(y[band]/x[band]) on the two passes as stored, with no registration, and the bands are chosen on the bright pass alone. The tool's own docstring (44-46) says a per-pixel ratio needs 'the same picture at the same registration'. fast_ir_probe.report's docstring records carriage-start drift of 0 to -3 lines across consecutive passes. pixels() (102-104) discards library.corrected's record, so --corrected never checks for 'applied' and mixes 'deliberately raw', 'no reference' or 'already' entries without a word. It also measures a recomputation, not a delivered file, contrary to the '--corrected ... what the *file* does' claim. The tool is cited as the linearity evidence behind EXPOSURE_TARGET.

</details>

<a id="probe-and-analysis-tools-pat-10"></a>

### PAT-10 -- registration_margin and roll_registration_study read rolls/*/prescan*.tif as they lie on disk, possibly turned or mirrored, and the glob also picks up prescanNN-before.tif

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/registration_margin.py:145-164`, `tools/roll_registration_study.py:861`, `tools/roll_registration_study.py:230-255`, `tools/roll_registration_study.py:613-647`, `rps7200/session.py:2852-2857`, `rps7200/session.py:2584`, `rps7200/session.py:671-685`

**Doc claim:** tools/registration_margin.py:148-151 ('rolls/*/prescanNN.tif are written rotated for the contact sheet') -- acknowledged there but not handled

A walk made in the window with a 90/180 degree turn or a mirror stores its prescans turned, and per frame at that, because a turn made mid-walk changes the later files. roll_registration_study then runs x-axis detectors over them (registration, gap_edges, registration_error_mm, per-frame mm_per_px from shape[1], StripWalk replay). On a 90-degree file those use the frame height as the transport axis; on a 180-degree file left and right gaps swap and offsets change sign. The prescan*.tif glob also admits `prescanNN-before.tif`, which sorts before `prescanNN.tif`. report_ensemble counts it as a separate frame, so every later frame's walk order, and the prior's 'consecutive advances', are shifted.

**Evidence (from the code):**

```text
`for path in sorted(folder.glob("prescan*.tif")): out.append((path.name, tiff.read(str(path)).astype(np.float64)))`. session._file writes prescans with `turn, flip = self._orientation_for(number, kind)`, composed with any `reversal`, then `rotate=turn, flip=flip`, and records `prescan_rotation`/`prescan_flipped` per frame. session.prescan_arrangement's docstring: 'which both have to un-turn the file into the film's own orientation before it is a reference; the tool used it as it lay on disk'. The before-correction pictures are written as `path=out / f"prescan{number:02d}-before.tif"`.
```

**Failure scenario:** `roll_registration_study.py --root rolls/<walk made rotated 90>` reports 'fired on 0/15', 'asserted registered', gap levels and ensemble moves, all computed along the wrong axis, and the numbers feed decisions about the detectors.

**Fix:** Read the survey.json/roll.json manifest, list frames with session.walk_frames/renumbered, un-turn each file with prescan_arrangement(), and exclude '-before' files, or pair them explicitly with their frame.

<details><summary>Second reader's check</summary>

registration_margin.cohort (158-160) reads every prescan*.tif as it lies on disk, and roll_registration_study uses that cohort (861 via cohort) or a raw glob. session._file (2852-2857, rotate=turn at 2888) writes prescans arranged by _orientation_for, which for prescans returns the session's rotation and flip (2760-2780) composed with any reversal. prescan_arrangement (672-685) exists to undo this, and neither tool calls it. The walk writes prescanNN-before.tif (2584), and '-' sorts before '.', so it lands just before prescanNN.tif. That is harmless in registration_margin (a same-picture pair) but shifts walk order in report_ensemble. The per-frame variation happens only when the session rotation changed mid-walk or a reversal was composed, so it is narrower than 'per frame' suggests.

</details>

<a id="probe-and-analysis-tools-pat-02"></a>

### PAT-02 -- The RPS7200_DEBUG gate accepts any non-empty value, but DirectScanner files only for 1/true/yes/on, so RPS7200_DEBUG=0 runs the probe and files nothing

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/hold_probe.py:114`, `tools/byte14_probe.py:113`, `tools/gain_probe.py:115`, `tools/fast_ir_probe.py:177`, `tools/fast_ir_probe.py:430`, `tools/exposure_probe.py:216`, `tools/transport_truth.py:107`, `tools/roll_registration_walk.py:242`, `rps7200/direct.py:556-560`

**Doc claim:** CLAUDE.md 'Claude: always scan with debug filing on. Always.'; each probe's refusal message 'refusing to run without RPS7200_DEBUG=1'

The gate exists so that a probe never runs without filing. It tests whether the variable exists, while the scanner parses its value. Values such as 0, false, off, 2 or debug pass the gate and leave debug filing off. The probe then runs its full hardware sequence, which exposure_probe says takes 40 minutes and fast_ir_probe 25, and no pass reaches the library.

**Evidence (from the code):**

```text
Probes: `if not os.environ.get("RPS7200_DEBUG"): print("refusing to run without RPS7200_DEBUG=1: a probe that files nothing cannot be re-analysed ...")` (hold_probe.py:114-118; the other files use the same test). Driver: `self.debug = (os.environ.get(self.DEBUG_ENV, "").strip().lower() in {"1", "true", "yes", "on"} if debug is None else bool(debug))` (direct.py:556-560); `_debug_capture` returns at once `if not self.debug` (direct.py:922-923).
```

**Failure scenario:** A shell profile sets RPS7200_DEBUG=0 to keep ordinary use quiet. `RPS7200_DEBUG=0 uv run python tools/exposure_probe.py --json probe/exposure.json` passes the gate and walks 15 frames. linearity --probe then finds 'no entry with exposure [...]' for every rung, because nothing was filed. This is the loss the rule in CLAUDE.md exists to prevent.

**Fix:** Build the scanner first, then gate on `scanner.debug` (or construct it with DirectScanner(debug=True) explicitly). Share one helper so the eight copies cannot drift apart. transport_probe has no gate at all; add one or retire the tool.

<details><summary>Second reader's check</summary>

The code matches the description. The probes gate on `if not os.environ.get("RPS7200_DEBUG")` (hold_probe.py:114, byte14_probe.py:113, gain_probe.py:115, fast_ir_probe.py:177/430, exposure_probe.py:216 via DirectScanner.DEBUG_ENV, transport_truth.py:107, roll_registration_walk.py:242). The driver parses the value against {1,true,yes,on} (direct.py:556-560), and _debug_capture returns early when not self.debug (direct.py:922-923). So RPS7200_DEBUG=0, off or 2 passes the gate while filing stays off. transport_probe has no gate at all. I lowered the severity to low because triggering it needs a falsy but non-empty value already in the environment and the documented `RPS7200_DEBUG=1` prefix left off. When it does trigger, it loses the whole run's evidence.

</details>

<a id="probe-and-analysis-tools-pat-07"></a>

### PAT-07 -- registration_margin, the tool named for re-fitting CONFIDENCE_FLOOR, measures a different statistic from the production gate

**Severity** low · **Category** bug · **Verdict** partly

**Where:** `tools/registration_margin.py:73-75`, `tools/registration_margin.py:193-199`, `tools/registration_margin.py:220-224`, `tools/registration_margin.py:165-177`, `rps7200/framing.py:1061-1063`, `rps7200/framing.py:1095-1099`, `rps7200/framing.py:1117-1118`, `rps7200/framing.py:880-883`, `rps7200/library.py:350-359`

**Doc claim:** rps7200/framing.py:880-882 're-run `tools/registration_margin.py` after any change to `register`...'; rps7200/library.py:357-358 '`tools/registration_margin.py` reads them back'; tools/registration_margin.py:74 '`SEARCH_MM` in pixels at this resolution -- what `register` is given'

In production a match's confidence is the maximum over two orientations, searched at a slightly different reach, and dy is gated in millimetres. The tool scores one orientation at another reach with a pixel dy gate. For different-picture pairs, a maximum over two null draws is stochastically higher than one, so the worst null the tool reports is a lower bound on what the production gate can see, and 'CONFIDENCE_FLOOR holds' does not validate the path that actually moves film. The library cohort mixes raw and corrected entries (deliberately raw ladders, 'no reference' entries), which the tool's own comment says 'measures the shading, not the registration'. It also mixes any 300 dpi frame window, not only full-frame prescans.

**Evidence (from the code):**

```text
Tool: `return int(round(SEARCH_MM / (MM_PER_INCH / dpi)))`, which is 106 px at 300 dpi; `dy, dx, conf = register(a, b, max_shift=reach)`, one orientation only; false-positive gate `if conf >= CONFIDENCE_FLOOR and abs(dy) <= MAX_DY_PX`. Production measure_shift_mm: `mm_per_px = aperture_mm / width; reach = int(SEARCH_MM / max(mm_per_px, 1e-9))`, which is 105 px for 428 columns; `upright = register(reference, now, max_shift=reach); flipped = register(reference, now[::-1], max_shift=reach); reversed_wins = flipped[2] > upright[2]`; the gate is `dy_mm > MAX_DY_MM`. The library cohort is built with `image, _ = library.corrected(record.parent)` and never checks `record['corrected']`. library.py:357-358 says '`tools/registration_margin.py` reads them back', but the tool never reads `record['registration']`.
```

**Failure scenario:** After a change to register, the tool is re-run as framing.py:880 instructs and reports 'No confident match on a pair that is not the same picture'. The production gate's flipped reading on some null pairs scores above 55, a hold moves the film to the wrong place, and the check meant to catch this never modelled it.

**Fix:** Call framing.measure_shift_mm(a, b), or a factored-out scorer it shares, so reach, orientation and dy gate come from one place. Filter the library cohort to `corrected == 'applied'` (or all raw) and to full-frame prescans. Either make the tool read `record['registration']` or correct the comment in library.py.

<details><summary>Second reader's check</summary>

Two differences are real. The tool registers one orientation only (registration_margin.py:194), while measure_shift_mm takes the stronger of upright and flipped (framing.py:1095-1099). The library cohort (165-177) uses library.corrected and never checks record['corrected'], and it also takes every entry at that dpi (prescans, metering probes, frame scans). library.py:357-358 says the tool 'reads them back', but it never reads record['registration']. Two other claimed differences are immaterial. Reach: the tool uses 106 px against production's 105 (int(9/(36.4913/428))), a 1% change in the searched window. The dy gate: MAX_DY_MM = 2*(24.3053/287) and a 300 dpi prescan has 287 rows, so the mm gate is exactly the 2 px gate at 300 dpi. The effect of the max-of-two-orientations on null pairs is plausible but unmeasured, and measure_shift_mm's own comment reports a wrong-way-up genuine pair at 4.9-8.9. Low.

</details>

<a id="probe-and-analysis-tools-pat-08"></a>

### PAT-08 -- dpi_analysis takes its noise floor from an unregistered repeat pair, and its 'one domain' check accepts old-code corrections

**Severity** low · **Category** bug · **Verdict** partly

**Where:** `tools/dpi_analysis.py:231-239`, `tools/dpi_analysis.py:17-20`, `tools/dpi_analysis.py:61-64`, `tools/dpi_analysis.py:338-343`

**Doc claim:** tools/dpi_analysis.py:25-35 ('Every rung in one domain, or none')

Picture content left in a misregistered pair counts as random noise, which inflates `floors`. Section C then divides each resolution step's residual by that inflated sigma (`ratio = resid / sigma`), so the report's rule 'Below 1, it is noise' says more often that higher resolution adds nothing. The domain gate exists because 'a mixed series compares the correction, not the resolution', yet it admits legacy entries corrected by older apply_shading code alongside today's.

**Evidence (from the code):**

```text
`a = pixels(repeats[0], domain); b = pixels(repeats[1], domain); mask = dark_mask(a); for c, name in enumerate(CHANNELS): rnd, total, share = noise_split(a, b, mask, channel=c)`, with no register/align. The tool's own docstring: 'passes sit at different column offsets -- two 3600 dpi passes once correlated at r=0.936 only after a 16-column shift'. noise_split (metrics.py:80-111) differences `x - y` pixel for pixel and assumes 'a registered, gain-matched pair'. `DOMAINS = {"corrected": ("applied", "already"), ...}`, where 'already' means 'corrected, by older code'.
```

**Failure scenario:** On a 3600 dpi top pair offset by a few columns or lines, random sigma reads several times the true figure. Section C reports every step at ratio < 1, and the dpi recommendation in docs/dpi-tradeoff-plan.md favours a lower resolution for a reason unrelated to resolution.

**Fix:** Register the pair (uniformity.register plus align) before noise_split, as fast_ir_probe._aligned_z does, and print the shift. Let the corrected domain accept only 'applied', or report 'already' separately.

<details><summary>Second reader's check</summary>

The noise-floor problem is confirmed. dpi_analysis.py:231-239 passes repeats[0] and repeats[1] straight to noise_split, which differences x - y pixel for pixel (metrics.py:107-110) and whose docstring assumes a registered pair. The tool's own docstring (17-20) says passes sit at column offsets. The inflated sigma feeds section C's ratios (338-343) and section A. The docstring names section B, which is shift-invariant spectra, as 'the one that decides it', and B does not use floors, so the tool's headline conclusion is unaffected. Accepting 'already' in the corrected domain (61-64) is a documented choice, commented as 'corrected, by older code', not a hidden defect, although it does admit old-code corrections. Real but narrower than stated, so low.

</details>

<a id="probe-and-analysis-tools-pat-11"></a>

### PAT-11 -- roll_registration_study.report_held divides magnitudes for the delivery ratio, trusts arrivals below the confidence floor, and hardcodes 428 columns

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/roll_registration_study.py:735`, `tools/roll_registration_study.py:767-776`, `tools/roll_registration_study.py:782-799`, `tools/roll_registration_study.py:710-713`

**Doc claim:** CLAUDE.md 'Do not use millimetres ... a delivery ratio computed against a magnitude rather than a signed distance'

CLAUDE.md lists 'a delivery ratio computed against a magnitude rather than a signed distance' as one of the two mistakes millimetres hid. This line is still exactly that: a frame that moved the wrong way by the commanded amount scores 1.0 'delivered'. Arrivals the correlator refused go into the ratio as noise-peak positions. The scale is valid only for 300 dpi prescans (framing.PRESCAN_COLUMNS), not for a roll prescanned at 600 dpi. A merged or resumed roll.json, or a frame without a hold record, pairs non-consecutive frames in the carry-over check.

**Evidence (from the code):**

```text
`scale = APERTURE_MM / 428.0`; `arrived_mm = (r["arrived_px"] or 0) * scale; ratios.append(abs(r["final_mm"] - arrived_mm) / abs(r["spent_mm"]))`. arrived_px is `history[0].get("px")`, and measure_shift_mm sets `px=int(-dx)` even when it refuses the match below CONFIDENCE_FLOOR (framing.py:1100-1109). The table hides such values as '(refused)'; the ratio does not. The manifest is read raw (`manifest.get("frames", [])`) and paired with `zip(rows, rows[1:])`, with no renumbering and no check that frames are consecutive.
```

**Failure scenario:** A 600 dpi-prescan roll, or one where the first reading was refused, prints 'delivered per mm commanded: 0.98-1.04' when the real delivery was wrong-signed or unmeasured.

**Fix:** Compute the signed delivered distance over the signed commanded distance (sign from `target - arrived` or history direction). Skip rows whose history[0] confidence is below CONFIDENCE_FLOOR. Take the width from the manifest's prescan_resolution (PRESCAN_COLUMNS scaled). Use session.renumbered and require consecutive numbers.

<details><summary>Second reader's check</summary>

roll_registration_study.py:735 `scale = APERTURE_MM / 428.0`. Lines 767-776 compute `abs(r["final_mm"] - arrived_mm) / abs(r["spent_mm"])` with `(r["arrived_px"] or 0) * scale` whatever the confidence; the table marks low-confidence arrivals '(refused)' (752-756), but the ratio ignores that. measure_shift_mm fills px even when it refuses (framing.py:1100-1109). The manifest is read raw (711-713) and paired with zip(rows, rows[1:]), with no renumbering and no consecutiveness check (the carry-over check does skip rows below the floor).

</details>

<a id="probe-and-analysis-tools-pat-12"></a>

### PAT-12 -- roll_registration_study's geometry uses the retired 36.0 mm frame model and a retyped BASE_TOLERANCE that differs from production

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `tools/roll_registration_study.py:95-99`, `tools/roll_registration_study.py:262-270`, `tools/roll_registration_study.py:532-535`, `rps7200/framing.py:407`, `rps7200/framing.py:1941-1957`

**Doc claim:** CLAUDE.md 'A frame is wider than the aperture: 350.6 units against 344.5 ... So a centred frame shows no unexposed base at either edge'

The study prints an 'actionable band' of 0.245-0.49 mm in which nothing is lost. The current measured model says a frame is wider than the aperture, so no such band exists and some picture is always lost. Its picture_start base test uses a 12% tolerance, where the production StripWalk base uses 15%. Both constants are retyped (FRAME_MM duplicates framing.FRAME_WIDTH_MM, and the 428 in report_held duplicates PRESCAN_COLUMNS) instead of imported.

**Evidence (from the code):**

```text
`FRAME_MM = 36.0` with `no_loss = (APERTURE_MM - FRAME_MM) / 2.0`, printed as `nothing lost while |e| <= {no_loss:.4f} mm`; `BASE_TOLERANCE = 0.12` against framing.py:407 `BASE_TOLERANCE = 0.15`. framing.py:1950-1957: 'A frame's width, in units: 435.6 columns ... Wider than the aperture (428 columns), so a centred frame shows no base at all. FRAME_WIDTH_UNITS = 350.6'.
```

**Failure scenario:** A reader of the study concludes frames within 0.25 mm lose nothing and reads gap widths against that. The frame-edge work (FRAME_WIDTH_UNITS) says otherwise.

**Fix:** Import the constants from framing, state the result in units, and either update the no-loss arithmetic to FRAME_WIDTH_UNITS or mark the tool as a study of the retired gap model.

<details><summary>Second reader's check</summary>

FRAME_MM = 36.0 (95-99) and BASE_TOLERANCE = 0.12 (532-535) are retyped, against framing.FRAME_WIDTH_MM = 36.0 (586) and BASE_TOLERANCE = 0.15 (407). The 'nothing lost while |e| <= ...' band (262-270) contradicts FRAME_WIDTH_UNITS = 350.6 (1957). Calling the 36.0 mm model 'retired' overstates it: framing.FRAME_WIDTH_MM and TARGET_GAP_MM still exist and, per CLAUDE.md, still aim rolls with no edge reader. The study describes the older gap model and does not say so.

</details>

<a id="probe-and-analysis-tools-pat-13"></a>

### PAT-13 -- transport_truth's docstring says it never sends SLIDE_INIT, but every prescan it takes sends SLIDE 10 16 00 00

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/transport_truth.py:34-40`, `tools/transport_truth.py:100-101`, `tools/transport_truth.py:125`, `tools/transport_truth.py:147`, `rps7200/direct.py:3062`, `tools/transport_truth.py:71`, `tools/transport_truth.py:153`

The tool gives the operator a safety claim that is false. Seven or more SLIDE_INIT commands go out per run, one per prescan. The hazard framing also conflicts with the driver sending this command on every pass of every scan. The retyped reach and floor drift silently from framing's.

**Evidence (from the code):**

```text
Docstring: 'It does NOT send SLIDE_INIT (`10 <param> 00 00`) ... a mechanism command with an unknown-meaning parameter -- the exact shape of the SET_SCAN_HEAD hazard'; runtime print `"  NOT sent: SLIDE_INIT -- unknown parameter, see this file's docstring"`. scan() sends `self.slide(SLIDE_INIT, param=slide_init_param)` with default 0x16 on every pass, and prescan() calls scan(). Also retyped: `register(..., max_shift=106)` and `if conf < 55:` in place of the reach measure_shift_mm computes and CONFIDENCE_FLOOR.
```

**Failure scenario:** Someone reviewing which commands have reached the device trusts the printed 'NOT sent: SLIDE_INIT' and draws a wrong conclusion about what preceded a fault.

**Fix:** Correct the docstring and the printed line: SLIDE_INIT with param 0x16 is sent by every scan. Use framing.measure_shift_mm, or CONFIDENCE_FLOOR plus SEARCH_MM, instead of the literals.

<details><summary>Second reader's check</summary>

The docstring (34-40) and the runtime print (100-101) both say SLIDE_INIT is 'NOT sent'. Every prescan() calls scan(), and scan() sends self.slide(SLIDE_INIT, param=slide_init_param), default 0x16, on every pass (direct.py:2860, 3062). The tool takes seven prescans, or eight with the restore, so SLIDE_INIT goes out on each. The literals max_shift=106 (71) and `conf < 55` (153) duplicate what measure_shift_mm and CONFIDENCE_FLOOR compute.

</details>

<a id="probe-and-analysis-tools-pat-14"></a>

### PAT-14 -- transport_probe is an ungated bypass whose detector cannot see film motion on a loaded strip

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/transport_probe.py:46-52`, `tools/transport_probe.py:55-61`, `tools/transport_probe.py:64-81`, `tools/transport_probe.py:135-139`, `rps7200/framing.py:230-273`

The probe runs with no gate at all, and with debug off its passes would be filed nowhere. It measures displacement with a detector that on a loaded strip always reports shift 0.00 mm, and it prints a conclusion that the driver's nudge contradicts. Per CLAUDE.md, a finding from a bypass path is provisional. This one would even produce a false negative if it could run (see PAT-01).

**Evidence (from the code):**

```text
No --dry-run, no RPS7200_DEBUG gate, no session_start/wait_warm: `with DirectScanner(verbose=True) as s: info = s.inquiry(); before = look(s, args.dpi)`. It sends `(SLIDE_NEXT, 0x01, 0, ...)`, `(SLIDE_NEXT, 0x01, 2, ...)` and `SLIDE_PREV`. The shift is measured with `registration(image)` (film_bounds), which roll_registration_study.py:18-22 documents as returning the whole window on a loaded strip. The tool prints: 'if no payload moves the film by less than a whole frame, registration drift can only be reported, not corrected'.
```

**Failure scenario:** Run on a loaded strip, it would report every probe as '+0.00 mm', the SLIDE_PREV 'dead' outcome CLAUDE.md warns about.

**Fix:** Retire it in favour of transport_truth (correlation-based) or tools/gui prev/next, or at least add the debug gate, a --dry-run, ensure_shading and measure_shift_mm.

<details><summary>Second reader's check</summary>

transport_probe.py has no --dry-run, no debug gate and no session_start or wait_warm (76-81). It sends non-vendor payloads, SLIDE_NEXT with value 0 and 2 (46-52). It measures with framing.registration, i.e. film_bounds x0 (58, 104), which abstains to the whole window on a loaded strip, so shift reads 0. Today it cannot even reach its first move: look() calls prescan() with default shading and raises ShadingUnavailable (PAT-01). The docstring's 'no shading calibration needed' (14-16) is false for the current driver.

</details>

<a id="probe-and-analysis-tools-pat-15"></a>

### PAT-15 -- gain_probe --ladder and byte14_probe --ladder values are unbounded below and, for gain, above; out-of-range gains wrap and are recorded unwrapped

**Severity** low · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/gain_probe.py:107-108`, `tools/gain_probe.py:158-161`, `rps7200/direct.py:1721`, `tools/byte14_probe.py:100-106`, `rps7200/direct.py:1466`

**Doc claim:** tools/gain_probe.py:22-23 'The top is red's own value, so every rung is a number this device is already known to accept in that field'

The docstring justifies the ladder by 'every rung is a number this device is already known to accept', but the CLI accepts any integer. `--ladder 21,21,300` sends gain 44 to a register the vendor never changes, and device_settings.gain in the library then says 300. That is also a data-integrity issue, although extra.commands keeps the real bytes.

**Evidence (from the code):**

```text
`ladder = (tuple(int(v) for v in args.ladder.split(",")) if args.ladder else LADDER)`, with no range check, feeds `replace(reference, gain=[..., gain, ...])`, and set_gain_offset writes `data[12 + i] = int(s.gain[i]) & 0xFF`. meta records `"gain": settings.gain`, the unmasked value. byte14 refuses only `v > MAX_BYTE14`, so `-1` passes and `data[14] = byte14` raises ValueError inside set_mode after SET SCAN FRAME, cmd 0x17 and WRITE GAIN OFFSET have been sent.
```

**Failure scenario:** A typo such as `--ladder 21,21,25,290` drives blue gain to 34 while the entry records 290. Analysis keyed on device_settings.gain misreads the rung.

**Fix:** Refuse gains outside the values captures show (21-39) unless an explicit override flag is given, and refuse negative byte14 values. Have set_gain_offset reject values that do not fit a byte instead of masking them.

<details><summary>Second reader's check</summary>

gain_probe.py:107-108 parses --ladder with no range check. set_gain_offset writes `int(s.gain[i]) & 0xFF` (direct.py:1721), while scan meta records `"gain": settings.gain` unmasked (direct.py:3174), and library.save copies that into device_settings.gain (library.py:341-342). So 300 is sent as 44 and filed as 300; extra.commands keeps the true bytes. byte14_probe parses with int(v, 0) and refuses only v > 0x31 (100-106), so -1 passes. set_mode then does `data[14] = byte14` on a bytearray (direct.py:1469), which raises ValueError after SET SCAN FRAME, cmd 0x17 and WRITE GAIN OFFSET have gone out. Values 0x00-0x0F also pass, contradicting the docstring's 'nothing invented'.

</details>

<a id="probe-and-analysis-tools-pat-16"></a>

### PAT-16 -- roll_registration_walk records requested ladder positions, not commanded ones, leaves the film about 0.07 mm off home, overwrites a corpus on label reuse, and cannot be joined to its library entries

**Severity** low · **Category** data-integrity · **Verdict** partly

**Where:** `tools/roll_registration_walk.py:57-59`, `tools/roll_registration_walk.py:32-34`, `tools/roll_registration_walk.py:146-178`, `tools/roll_registration_walk.py:102-110`, `tools/roll_registration_walk.py:207`, `tools/roll_registration_walk.py:248`, `tools/roll_registration_walk.py:336-338`, `tools/roll_registration_study.py:447-449`, `rps7200/direct.py:3599-3609`

**Doc claim:** tools/roll_registration_walk.py:57-58 'Every rung is this, so the abscissa is a lattice point and not a rounding of one'; :32-34 '-0.816 mm ... +0.272 mm x 6'

The ladder exists to provide ground truth, yet every rung's recorded `commanded_mm` is off by 0.034 mm (0.32 units), because one 3-rung command pays the per-command ramp once. The true value survives only inside `sent.asked_mm`. The docstring claims 'the abscissa is a lattice point', and its figures (0.272 mm and 0.816 mm) predate the 1.84 ramp. Reusing a --label silently overwrites the earlier walk's TIFFs and walk-X.json. The debug-filed raw entries cannot be matched to A07_p2.tif except by content. A log written with --json elsewhere breaks load_walk, which looks for the TIFFs beside the log.

**Evidence (from the code):**

```text
`sent = self._nudge(-RUNGS * RUNG_MM, excursion); excursion += -RUNGS * RUNG_MM` then `"commanded_mm": round(excursion, 4)`. param_for_mm(0.9006) = round((0.9006-0.1945)/0.1057) = 7, so the command travels 0.1057*7+0.1945 = 0.9344 mm, not 3 x FINE_MIN_MM = 0.9006 mm. The restore `self._nudge(-excursion, excursion)` also sends param 7, giving a net of -0.9344+6*0.3002-0.9344 = -0.068 mm. `out = args.out or Path("rolls") / f"registration-{args.label}"`, `out.mkdir(parents=True, exist_ok=True)`, and the TIFFs are written by tiff.write with no existence check. `_prescan` discards the pass meta (`prescan()` returns ScanParameters), so the log keeps neither started_utc nor an entry id. load_walk takes `folder = log.parent`.
```

**Failure scenario:** A detector's true-positive curve is fitted against commanded_mm and carries a constant 0.4 px bias. Or a second 'walk A' replaces the first corpus.

**Fix:** Record `sent['asked_mm']` cumulatively as the commanded position. Build the -3 rung leg from three FINE_MIN_MM commands, or document that it is one command. Refuse an existing out/label unless --overwrite is given. Record `scanner.last_scan_meta['started_utc']` per pass and the output folder in the log.

<details><summary>Second reader's check</summary>

The arithmetic holds. FINE_MIN_MM = 0.1057*2.84 = 0.30019. param_for_mm(0.90056) = round(6.68) = 7, and a param-7 command travels 0.93439 mm. Every rung's commanded_mm is therefore 0.0338 mm (0.32 units, 0.4 px) short, and the restore nudge(-0.9006) also sends param 7, for a net of about -0.068 mm. Label reuse overwrites the TIFFs and walk-X.json (out.mkdir(exist_ok=True), tiff.write, write_text). _prescan discards the meta, so neither started_utc nor an entry id is kept. load_walk reads TIFFs from log.parent. One point in the finding is wrong: the true command distance does NOT survive in sent.asked_mm. Walk._nudge returns `dict(sent, asked_mm=mm)` (roll_registration_walk.py:128), which overwrites the driver's lattice distance with the requested mm. Only sent.param (7) is left to recover it. Also, the restore comment (166-168) says 'Measured against where the frame started, not summed from the commands', but the code passes the summed `excursion`.

</details>

<a id="probe-and-analysis-tools-pat-17"></a>

### PAT-17 -- Probes and studies print and take millimetres, and carry stale minimum-step figures

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/hold_probe.py:60-68`, `tools/hold_probe.py:73-77`, `tools/hold_probe.py:87-88`, `tools/transport_truth.py:60-66`, `tools/transport_probe.py:116`, `tools/roll_registration_walk.py:61-74`, `tools/roll_registration_study.py:264-270`, `tools/registration_margin.py:203`, `rps7200/protocol.py:226-231`

**Doc claim:** CLAUDE.md 'Do not use millimetres for transport distances ... Millimetres are prohibited'; tools/hold_probe.py:65-67 'this is the script's own stop, so a surprise cannot walk the film across the aperture'

Every probe that moves the transport, and both registration studies, speak millimetres to the operator, against the rule CLAUDE.md states and protocol.say_units exists to enforce. The stated minimum step (0.272/0.2719 mm) is the retired 1.572-ramp value. hold_probe's TOTAL_TRAVEL_LIMIT_MM is described as 'a stop on the whole run' but is compared only with --offset (`if abs(args.offset) > TOTAL_TRAVEL_LIMIT_MM`). The measured `--restore` move `scanner.nudge(-net)` and the hold loops' own travel are never counted against it.

**Evidence (from the code):**

```text
hold_probe `PROBE_MM = 0.5` with comment 'the smallest move the hardware can make (0.272 mm)', `--offset` in mm, and prints `target {held['target_mm']:+.3f} mm`. transport_truth: 'Comfortably above the 0.2719 mm minimum', `STEP_MM = 0.5`. transport_probe: `"shift_mm": round(shift * MM_PER_INCH / COORD_PER_INCH, 2)`. The walk prints `{RUNG_MM:.4f} mm`. protocol.py:226-228: '**Millimetres are prohibited in anything an operator reads** ... say_units'. FINE_MIN_MM is now 0.3002 mm (2.84 units).
```

**Failure scenario:** An operator reads 'hold at +0.500 mm' and asks for 0.25 mm, which cannot exist: 0.3002 mm is the smallest move. A misread restore can travel up to param 87 (about 9.4 mm) with no stop from the script's own limit.

**Fix:** Accept and print units (protocol.units/say_units), import FINE_MIN_MM instead of quoting it, and make hold_probe sum spent_mm plus the restore against its limit.

<details><summary>Second reader's check</summary>

hold_probe: PROBE_MM = 0.5 with the comment '(0.272 mm)' minimum (60-63), --offset and the printed output in mm (74-80), and TOTAL_TRAVEL_LIMIT_MM compared only against args.offset (96-99). The --restore nudge(-net) (208-210) and the hold loops' spent_mm are never counted. transport_truth: 'Comfortably above the 0.2719 mm minimum' (64-66), STEP_MM 0.5, output in mm. transport_probe prints shift_mm. The walk prints RUNG_MM in mm. protocol.py:226-231 prohibits millimetres in operator-facing text and provides say_units. The finding misses one stale figure: hold_probe also tells the operator 'A floor of 40' (docstring 34-35) and prints 'offline data predicted 61-96, and the floor is 40' (196-198), while CONFIDENCE_FLOOR is 55 (framing.py:883).

</details>

<a id="probe-and-analysis-tools-pat-18"></a>

### PAT-18 -- exposure_probe re-detects the metering region on each bright delivered pass, which auto_exposure deliberately avoids, and retypes the under-target tolerance

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/exposure_probe.py:159-168`, `tools/exposure_probe.py:120-122`, `tools/exposure_probe.py:62-66`, `rps7200/direct.py:2506-2518`, `rps7200/direct.py:2392`

**Doc claim:** tools/exposure_probe.py:62-66 'Each pass's delivered level is read the way auto_exposure reads a probe -- metering_slice for the film'

On a metered pass film_bounds can no longer find the film, so levels_of reads the whole window, aperture included, whenever an aperture is in view. The 'did metering land in the band' verdict can then read OVER, and ladder rungs can be read over different regions: the dark ones find the film, the bright ones do not. The docstring says it reads 'the way auto_exposure reads a probe', which it does not. The retyped tolerance drifts if the driver's changes.

**Evidence (from the code):**

```text
`crop = image[metering_slice(image)]` runs on every delivered pass. auto_exposure's comment: 'Measured once, on this first pass, and reused ... by the round that settles the exposure the contrast it depends on is gone. Detecting each round would quietly stop working exactly when it mattered.' `UNDER_TOLERANCE = 0.08` is described as 'repeated rather than imported' from auto_exposure's `tolerance: float = 0.08`.
```

**Failure scenario:** A frame with the aperture visible reports R/G 'OVER' at the shipped exposure, and the report lists it as a metering miss.

**Fix:** Detect the region once per frame, on the first metering probe or on the x0.55 rung, and reuse it for every rung. Import the tolerance, for example as a DirectScanner class constant.

<details><summary>Second reader's check</summary>

exposure_probe.levels_of (159-168) runs metering_slice on every delivered pass. auto_exposure deliberately measures the region once, on the first dark probe, and reuses it (direct.py:2506-2518). framing.metering_region's docstring explains that film_bounds loses its contrast once the film is metered bright, so the bounds fall back to the whole window. UNDER_TOLERANCE = 0.08 (120-122) is a retyped copy of auto_exposure's `tolerance: float = 0.08` (2392). The docstring's claim (62-66) that levels are read 'the way auto_exposure reads a probe' is therefore false.

</details>

<a id="probe-and-analysis-tools-pat-19"></a>

### PAT-19 -- Offline analysis tools do not exclude demo entries, prescans or metering passes when they select from the library

**Severity** low · **Category** demo-divergence · **Verdict** partly

**Where:** `tools/exposure_headroom.py:312-324`, `tools/linearity.py:133-153`, `tools/registration_margin.py:165-177`, `tools/dpi_analysis.py:79-97`, `rps7200/library.py:1119`

The window now files demo runs under demo/, but the library code still expects demo entries inside library/ ('A demo entry built from a finished picture'). Legacy demo entries hold raw bytes that 'belong to some other photograph'. None of the tools that derive EXPOSURE_TARGET, CONFIDENCE_FLOOR or the dpi advice filters them out, and linearity's exposure join can also match a metering probe with the same exposure triple as a rung.

**Evidence (from the code):**

```text
library.verify does recognise them: `demo = bool((record.get("extra") or {}).get("demo"))`. The selectors never test it: exposure_headroom `for path in sorted(root.glob("2*/"), reverse=True)`, linearity `entries_by_exposure` over `directory.iterdir()`, registration_margin `for record in sorted(folder.glob("*/scan.json"))` filtered only by dpi, and dpi_analysis by time window ('takes whatever was filed inside it -- another frame, a prescan, a demo entry').
```

**Failure scenario:** A demo entry among the four newest 16-bit entries goes into exposure_headroom's worst-case table as sensor evidence.

**Fix:** Skip `extra.demo` entries in every selector, and let each tool state which entry kinds (tags debug/prescan/metering) it admits.

<details><summary>Second reader's check</summary>

None of the selectors tests extra.demo, which is true: exposure_headroom 312-324, linearity 133-153, registration_margin 165-177, dpi_analysis 79-97. However, the window now files demo runs under demo/ (gui.py:8783, 8856), and every tool defaults to library/, so this reaches only legacy demo entries in library/ or an explicit --root demo/library. Demo entries can carry a copied real reference and raw bytes (demo.py:880-908), so exposure_headroom could admit one as a duplicate of a real entry. The linearity collision with a metering probe's exposure triple is speculative, and by_frame reports multiple candidates as a note.

</details>

<a id="probe-and-analysis-tools-pat-a1"></a>

### PAT-A1 -- roll_registration_walk overwrites the driver's commanded distance with the requested one, and the restore comment claims a measurement the code does not make

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `tools/roll_registration_walk.py:112-128`, `tools/roll_registration_walk.py:166-169`, `rps7200/direct.py:3611-3640`

**Doc claim:** tools/roll_registration_walk.py:166-168 'Measured against where the frame started, not summed from the commands'

The corpus log is the walk's ground truth. The one field that would hold what the transport was commanded to travel is replaced by what was asked, so each rung's `sent.asked_mm` equals `sent.requested_mm`. The lattice distance can be rebuilt only from `sent.param` and the ramp constants. The restore is not measured at all: no prescan is compared with the frame's p1 before the nudge. With the 3-rung first leg this leaves the film about 0.068 mm (0.64 units) off home per ladder, and the log says nothing about it.

**Evidence (from the code):**

```text
Walk._nudge: `sent = self.scanner.nudge(mm) ... return dict(sent, asked_mm=mm)`. nudge() returns `"asked_mm": round(asked if forward else -asked, 3)`, where asked = STEP_MM*param + OVERHEAD_MM is the lattice distance, and `"requested_mm": round(millimetres, 3)`. The restore: `# Put it back. Measured against where the frame started, not summed from the commands, because backlash means the two differ.` followed by `back = self._nudge(-excursion, excursion)`, where excursion is the running sum of requested mm.
```

**Failure scenario:** An analysis fits a detector's response against `rungs[].sent.asked_mm`, trusting the driver's key name. It gets requested distances with a constant 0.034 mm bias on every rung. A second ladder on a later frame starts from a position already displaced by the first ladder's unrecorded residual.

**Fix:** Keep the driver's asked_mm and store the request under another key, for example `dict(sent, requested_mm=mm)`. Record the cumulative commanded position from the returned asked_mm. Either measure the restore with measure_shift_mm against p1 or correct the comment.

<a id="probe-and-analysis-tools-pat-a2"></a>

### PAT-A2 -- hold_probe tells the operator the registration floor is 40 when CONFIDENCE_FLOOR is 55

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `tools/hold_probe.py:34-35`, `tools/hold_probe.py:193-198`, `rps7200/framing.py:883`

**Doc claim:** tools/hold_probe.py:34-35

The probe's verdict text judges real confidences against a floor the driver no longer uses. With a floor of 55, a true match at 41-54 is refused by the production hold loop, yet the probe's output tells the operator it clears the floor. The 61-96 'offline' range also predates the current SEARCH_MM reach, and the tool's own registration_margin docstring says confidence depends on the reach.

**Evidence (from the code):**

```text
Docstring: 'confidence, which offline data puts at 61-96 for a true match against 4.3-28.8 for two different photographs. A floor of 40 sits in that gap.' Runtime: `print(f"confidence ran ... offline data predicted 61-96, and the floor is 40")`. framing.py:883 `CONFIDENCE_FLOOR = 55.0`.
```

**Failure scenario:** The hold at +0.5 comes back 'not_converged' with confidences of 45-50. The operator reads 'the floor is 40' and concludes the matches were good and the move failed, when the loop actually refused the matches.

**Fix:** Import CONFIDENCE_FLOOR and print it, and drop the hard-coded offline range, or re-derive it with registration_margin.

<a id="probe-and-analysis-tools-pat-a3"></a>

### PAT-A3 -- exposure_headroom aborts the whole study on one unreadable scan.json

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `tools/exposure_headroom.py:89-94`, `tools/exposure_headroom.py:312-324`

An entry directory still marked INCOMPLETE, or with a truncated or half-written scan.json (the library now writes atomically, but legacy or interrupted entries exist, and `make verify` reports them), raises JSONDecodeError or OSError out of main. No other entry is studied. linearity and dpi_analysis catch this per entry.

**Evidence (from the code):**

```text
`record = json.loads((path / "scan.json").read_text(encoding="utf-8"))` sits outside the try in decode(). main() iterates `sorted(root.glob("2*/"), reverse=True)` and calls study(path) with no exception handling. The try in decode covers only the layout and deinterleave.
```

**Failure scenario:** The newest library directory is an INCOMPLETE entry from a killed window. `exposure_headroom.py` dies with a traceback on it instead of skipping it and studying the next four.

**Fix:** Catch OSError and JSONDecodeError in decode() and return None, and skip directories that still hold INCOMPLETE.

<a id="probe-and-analysis-tools-pat-20"></a>

### PAT-20 -- The docstrings of fast_ir_probe and byte14_probe describe driver behaviour that has since changed

**Severity** info · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/fast_ir_probe.py:10-14`, `tools/fast_ir_probe.py:16-19`, `tools/fast_ir_probe.py:37-46`, `tools/byte14_probe.py:13-15`, `rps7200/direct.py:2860`, `rps7200/direct.py:1970-1978`

These are the operator's instructions for when and why to run the probes, and they describe a driver that no longer exists. The risk described is not the current one: reversed passes now come back upright, but consecutive bit-0 passes still change carriage behaviour.

**Evidence (from the code):**

```text
fast_ir_probe: '`QUALITY_FAST_INFRARED` has been defined and reachable since the protocol was first written and has never once been sent', while scan() now defaults to `fast_infrared: bool = True`. byte14_probe: 'running the ladder again will reproduce reversed passes exactly as the first run did', while decode_index now turns bottom-up passes upright from their line tags ('they are turned here, in the decode').
```

**Failure scenario:** An operator skips byte14_probe out of fear of reversed data, or reruns fast_ir_probe believing the flag is untested in production.

**Fix:** Add a dated 'status now' paragraph at the top of each docstring.

<details><summary>Second reader's check</summary>

fast_ir_probe.py:10-14 says QUALITY_FAST_INFRARED 'has never once been sent', but scan() defaults fast_infrared=True (direct.py:2860) and gates it on infrared (3040-3041). byte14_probe.py:13-15 warns that a rerun 'will reproduce reversed passes', but decode_index now turns bottom-up passes upright from their line tags (direct.py:1923-1933). Both docstrings are stale. Neither is a defect in behaviour.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entries for every probe pass (debug filing) | library/<UTC id>_<film>_<dpi>dpi[_ir]/ {scan.tif, raw.bin.gz\|raw.bin, shading.npz, ccd_mask.bin, scan.json} | scan.tif uint16/uint8 (H,W,C) decode, upright and 7200 stagger-realigned; raw.bin.gz gzip of the exact INDEX-format bytes with a sha256; shading.npz the ShadingReference; scan.json the record (scan.*, device_settings.exposure/gain/offset, extra.mode.byte14_override, extra.commands, extra.started_utc, metering, registration, calibration.skipped) | raw (library.save gets raw_pixels from scan(); the correction is recomputed by library.corrected) | DirectScanner._debug_capture (direct.py:908) spools during the session; _debug_flush -> library.save after close() (direct.py:1031-1127). Filed only when DirectScanner.debug is true; see PAT-02 | linearity (decode_raw / corrected, joined on device_settings.exposure), exposure_headroom (read_raw + its own _deinterleave + apply_shading), dpi_analysis (load / corrected, window on `created`), registration_margin and roll_registration_study library cohort (corrected) | Raw bytes are byte-exact (sha256). `created` and the entry name are flush time, not capture time; the capture time is in extra.started_utc. device_settings.gain records the requested value, which can differ from the byte sent when gain > 255 (PAT-15) |
| Debug spool of probe passes not yet filed | $TMP/rps7200-debug-*/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz, NNN-ccd_mask.bin | npy of raw pixels, exact bytes, JSON sidecar | raw | DirectScanner._debug_capture | DirectScanner._debug_flush; by hand if the process died | Exact. Left behind (never filed automatically) when a probe is killed before close() or a save fails |
| Registration walk corpus | rolls/registration-<label>/<label>NN_p1.tif, <label>NN_p2.tif, <label>NN_LSS.tif, walk-<label>.json (or the --json path) | 8-bit RGB TIFF prescans at 300 dpi (tiff.write); the JSON log holds frames[].passes/contrast/position, ladders[].rungs[].commanded_mm/sent/pass, restore, travel_mm, seconds, ok/error | TIFFs corrected (prescan() returns the shading-corrected image), not rotated | tools/roll_registration_walk.py Walk._write / main finally | tools/roll_registration_study.py --walk (load_walk reads TIFFs from log.parent), --glob | TIFF lossless, but corrected only; the raw counterpart exists only as a debug entry that cannot be joined (no started_utc or entry id in the log). commanded_mm is the requested, not the commanded, position (PAT-16). Overwritten on --label reuse |
| Exposure walk result (join key to the library) | <--json path>, e.g. probe/exposure.json | JSON {schedule, target, ladder, passes[{frame, rung, transport_position, exposure[4], clamped, metering, probed_levels, levels, duration_s, wall_s}]} | levels measured on the corrected delivered image; exposure is the device triple | tools/exposure_probe.py main, rewritten whole after every pass (Path.write_text, not atomic) | tools/linearity.py --probe (by_frame joins passes[].exposure[:3] to device_settings.exposure[:3]) | Exposure ints exact. Frame numbers are plan numbers, wrong under --only chunking; the file is overwritten per chunk (PAT-05) |
| Probe summaries | <--json path> of hold_probe, transport_probe, byte14_probe, gain_probe, fast_ir_probe, transport_truth | JSON: hold outcomes in mm with per-look history; per-pass ms/line, exposure, byte14/fast_infrared flags; gain rung stats; drift/agreement/speck summaries | derived statistics (hold histories from corrected prescans; gain/byte14/fast-IR stats from the raw shading=False passes) | each probe at the end (or in its finally) | people / docs; nothing in code | Derived and rounded; not re-derivable except from the library entries, if debug filing was on |
| Roll prescans and manifests read by the studies | rolls/<roll>/prescanNN.tif, prescanNN-before.tif, survey.json \| roll.json | 8-bit TIFF, rotated/flipped per prescan_rotation/prescan_flipped; JSON manifest frames[].registration.approved{target_mm, outcome, moves, spent_mm, final_mm, residual_mm, history[{px, confidence, dy, ...}], clamped} | corrected by that day's code; arranged as the operator viewed them | rps7200/session.py ScanSession._file / RollManifest (and tools/scan_roll.py) | registration_margin.cohort, roll_registration_study (cohort, report_held) | The studies read the files as they lie on disk and do not un-turn them (PAT-10); report_held reads frames without renumbering |
| dpi analysis results | probe/dpi/results.json (or --out) | JSON {domain, aliasing, noise_floor_dn, absolute, steps, series[]} | per the --domain flag (corrected = library.corrected recomputation; raw = library.load) | tools/dpi_analysis.py | people / docs/dpi-tradeoff-plan.md | Derived; the noise floor comes from an unregistered pair (PAT-08) |
| Headroom and study outputs | <--json> of exposure_headroom and roll_registration_study | JSON tables (per-target k, clipped_pct, level; per-frame detector readings, bands, pairs, ensemble rows, held rows) | exposure_headroom simulates on its own raw decode with that entry's reference; the study uses corrected prescans | those tools | people / docs | Derived |

**Second reader's corrections to this table:**

Registration walk corpus: the ladder files are named <label>NN_L00.tif .. <label>NN_L06.tif (f"{label}{number:02d}_L{step:02d}.tif", roll_registration_walk.py:154-155), not '_LSS'. In the walk log, rungs[].sent.asked_mm is the requested millimetres, not the driver's commanded lattice distance, because Walk._nudge overwrites it (`dict(sent, asked_mm=mm)`, :128). The commanded distance survives only as sent.param. The persisted_state entry says the true value is 'inside sent.asked_mm'; that is wrong. travel_mm sums requested |mm|, not delivered distance. The walk log keeps no library join key (started_utc or entry id). hold_probe JSON: held/zero records carry the hold loop's output in mm, with no library entry ids. transport_probe: in its current state it writes nothing and files nothing. It has no debug gate, and with debug on its passes would be filed, but it raises ShadingUnavailable on its first prescan. registration_margin writes no file; it prints only. Library entries written by probes: device_settings.gain comes from meta['gain'] (library.py:341-342, direct.py:3174), which is the unmasked requested value; the byte actually sent is `& 0xFF`. Probe ladder passes (byte14, gain, fast_ir) are filed with calibration.skipped = SHADING_SKIPPED_EXPLICIT ('deliberately raw'), so library.corrected returns them raw. Any analysis using corrected() over them gets raw pixels labelled 'deliberately raw'. Every probe listed as a library writer currently files nothing but its pre-scan status traffic, because each one fails at its first corrected pass (PAT-01).

## What the operator can do

- Run any hardware probe with --dry-run to see the plan (all except transport_probe; fast_ir_probe also has --resolutions sweep dry runs).
- Run hold_probe, byte14_probe, gain_probe, fast_ir_probe, exposure_probe, transport_truth or roll_registration_walk once RPS7200_DEBUG is set to any non-empty value. Today each opens the device, starts a session, waits for warm-up and then stops with ShadingUnavailable (PAT-01).
- Run transport_probe with no gate, no dry run and no debug filing.
- Override byte14_probe's ladder (values above 0x31 are refused, negative values are not) and gain_probe's ladder (any integer).
- Chunk exposure_probe with --only A-B, and skip rewinding with --no-rewind.
- Rewind a strip before a walk with roll_registration_walk --rewind N, and run the displacement ladder on chosen frames with --ladder.
- Run every analysis tool offline against library/ and rolls/: exposure_headroom (--entries/--dpi), linearity (--match or --probe, --corrected), dpi_analysis (--entries, --domain raw|corrected, --after/--before), registration_margin (--rolls, --self-similar), roll_registration_study (--root, --walk, --glob, --held, --ensemble).

## What the operator should not do

- Run any hardware probe without Stefan's agreement, without film loaded, or while the window holds the device.
- Run fast_ir_probe (about 25 min), exposure_probe (about 40 min), a 16-frame walk (about 12 min) or byte14_probe (about 8-9 min) in the foreground; the harness kills at 10 minutes and a killed read is abandoned.
- Press Ctrl-C during a probe pass: no probe defers it, and the read is abandoned (PAT-03).
- Set RPS7200_DEBUG to 0/false/off expecting a probe to refuse; it runs and files nothing (PAT-02).
- Rerun byte14_probe without reading docs/byte14-plan.md, or feed gain_probe values outside 21-39.
- Use exposure_probe --only to chunk a walk while rewinding (the default), or with the same --json path for every chunk (PAT-05).
- Reuse a roll_registration_walk --label, which overwrites the earlier corpus.
- Treat exposure_headroom, registration_margin, dpi_analysis or linearity output as validation of production constants until PAT-06 to PAT-09 are addressed.
- Point roll_registration_study or registration_margin at a roll walked with a rotation or mirror set in the window (PAT-10).

## Mistakes nothing guards against

- RPS7200_DEBUG=0 (or false/off/2) passes every probe's debug gate and turns filing off, so a 40-minute run leaves no library entry.
- A single Ctrl-C in any probe abandons the in-flight read, marks the device suspect, and usually means a power cycle.
- After an abandoned read, byte14_probe's finally-pass and gain_probe's restore still send SET SCAN FRAME, MODE SELECT and WRITE GAIN OFFSET to the suspect device.
- exposure_probe --only 5-8 after a rewound --only 1-4 rescans physical frames 1-4 labelled 5-8, and --json is overwritten.
- gain_probe --ladder 21,21,300 sends gain 44 (300 & 0xFF) while the library records 300; byte14_probe --ladder with a negative value raises inside set_mode after several setup commands.
- hold_probe --restore sends one measured nudge(-net) that its own TOTAL_TRAVEL_LIMIT_MM never checks.
- roll_registration_walk --json elsewhere writes a log that roll_registration_study --walk cannot use, because the TIFFs are sought beside the log.
- roll_registration_study --root on a rotated walk runs x-axis detectors over turned images and counts prescanNN-before.tif as extra frames.
- dpi_analysis's default time window selects by flush time (`created`) and takes whatever was filed in it, prescans and demo entries included.
- linearity --corrected silently mixes raw ('deliberately raw', 'no reference') and corrected entries under the label 'corrected pixels'.

## Dataflow notes

Hardware probes. Data enters through DirectScanner.scan (direct.py:2842), either directly (byte14, gain and fast_ir ladders with shading=False; exposure_probe with shading=True), via prescan() (direct.py:2053; shading=True; used by hold_probe, transport_probe, transport_truth and the walk), or via auto_exposure() (direct.py:2385; RGB metering probes with shading=True and keep_raw=True). scan() refuses a corrected pass without a reference (2948-2963) before sending anything, which no probe arranges for (PAT-01). Inside a pass, _read_pass -> read_planes keeps raw bytes when keep_raw or debug is set (3279), decode_index turns bottom-up passes upright (1923), the 7200 stagger is realigned (3090), and apply_shading is applied when shading is set (3129). meta records exposure, gain, mode.byte14_override, fast_infrared, commands, started_utc and read_direction. _debug_capture(raw_pixels, meta) (3222) spools raw pixels, bytes, reference and mask to a temp directory; close() -> _debug_flush -> library.save files them after the device is closed (1134-1145, 1031). Probes never call debug_claim, so every pass, metering probes included, is filed when debug is on (PAT-02 covers when it is not). The probes' own outputs are derived: hold_probe uses _hold_to_approved (3306) plus framing.measure_shift_mm (1007), mm results to optional JSON; transport_truth uses register on luminance and nudge/advance/retreat (1557-1590, 3611); the walk writes corrected 8-bit prescan TIFFs to rolls/registration-<label> plus walk-<label>.json with requested ladder positions; exposure_probe computes levels_of on the delivered corrected image and writes passes[].exposure as the join key; byte14, gain and fast_ir summarise ms/line and noise from in-memory crops via metrics.noise_split / agreement_z. Transport commands: nudge -> param_for_mm (3599) -> slide(0x00|0x01, param, 0x04); advance/retreat -> _whole_frames -> SLIDE 04/05 01 00 01; every scan also sends SLIDE_INIT 10 16 00 00 (3062). Suspect gating exists only in slide/start_scan/calibrate (1523, 1623, 2178). Offline analysis. exposure_headroom reads library.read_raw + record.raw.layout -> DirectScanner._deinterleave (its own decode, no _replay), then ShadingReference.load + ccd_mask -> apply_shading, and simulates raw' = (raw-dark)*k+dark per channel with k anchored on the whole-frame 99.5th percentile (PAT-06). linearity reads library.decode_raw (raw) or library.corrected()[0] (state unchecked), crops the middle, and takes per-pixel ratios between adjacent rungs; --probe joins exposure_probe JSON to library entries by device_settings.exposure[:3] and drops x1.00 rows. dpi_analysis reads library.load (raw) or library.corrected (applied/already) with a domain check, row-averaged power spectra (B), a 7200 crop spectral floor (A), a noise floor from an unregistered repeat pair via noise_split (PAT-08), and downsample residuals (C) -> probe/dpi/results.json. registration_margin reads cohort() (rolls prescan*.tif as on disk, or 300 dpi library.corrected), runs uniformity.register one orientation with reach round(SEARCH_MM/(25.4/dpi)), arbitrates by aligned_correlation, and gates FPs with MAX_DY_PX; production measure_shift_mm tries both orientations, uses reach int(SEARCH_MM/(APERTURE_MM/width)) and a millimetre dy gate (PAT-07). roll_registration_study reads the same cohort or a walk log, runs framing's registration/gap_edges/registration_error_mm/StripWalk replay along x with FRAME_MM=36.0, and report_held reads the manifest's registration.approved with a magnitude delivery ratio and a 428-column scale (PAT-10, PAT-11, PAT-12). Nothing in the analysis tools writes to library/ or rolls/; they write only to --json/--out paths.
