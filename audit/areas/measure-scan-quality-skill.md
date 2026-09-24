# The measure-scan-quality metrics (gap pass)

Area key `measure-scan-quality-skill`. 17 findings: 3 high, 7 medium, 5 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: the measure-scan-quality skill, plus everything that consumes it or claims to follow it:
- .claude/skills/measure-scan-quality/SKILL.md (91 lines) and scripts/metrics.py (148 lines)
- its three importers: tools/dpi_analysis.py, tools/byte14_probe.py, tools/fast_ir_probe.py
- the solve_relation it borrows from rps7200/bracket.py
- the pixels library.corrected / apply_shading hand it
- tools/make_comparison.py, which writes the comparison files CLAUDE.md mandates and carries its own copy of these metrics
- rps7200/console.py (use_utf8_stdout)

Everything was judged from code. No file was modified, nothing was run against hardware, and no library is on disk.


Answers to the four questions:


(1) What callers actually feed the metrics. Nobody feeds what metrics.py:7 declares ("shading-corrected linear samples, (H, W, C) uint16"):
- byte14_probe and fast_ir_probe pass shading=False raw pixels, held in memory as centre crops (fast_ir_probe's are 4-channel RGBI, and it uses the IR plane).
- dpi_analysis passes library.corrected(). That is corrected for rungs up to 3600 dpi but necessarily raw for the 7200 dpi reference and its repeat pair: shading is refused above 5172 columns, and corrected() returns skipped entries raw. A GUI single-scan entry that falls in the time window would be shaded twice (the root cause was already reported).
- No current caller feeds 8-bit prescans or the un-realigned decode_raw at 7200. Nothing guards against either.

(2) What agreement_z inherits from bracket. It does NOT inherit merge_bracket's green-only ratio: it fits the relation on the channel it is asked for. It DOES inherit solve_relation's absolute SNR_FLOOR/CLIP_START gates. Under its own contract those are applied to corrected values, where the per-column gain moves the sensor knee. It also takes the median |z| over pixels the fit excluded. Its noise model is bracket's unmeasured fallback, typed in again as literals.


(3) Do the metrics match what CLAUDE.md prescribes?
- colour_deviation is correctly signed and channel-relative, but nothing calls it.
- fixed_pattern and relative_noise are also uncalled.
- noise_split compares two passes pixel for pixel with no registration and no gain match, and it filters "random" and "total" differently. Its share therefore reads about 1.12 on pure white noise and can exceed 100% (fast_ir_probe's own note says so). ceiling() then clamps silently and returns the most optimistic answer.
- The code claims the ideal median |z| is 1.0. For a correct model it is 0.674, so the 1.03 baseline means the model understates sigma by about 1.5x, not "slightly".
- make_comparison, whose files Stefan judges, reports the np.abs worst-column figure the skill forbids, plus a copy of colour_deviation normalised differently.

(4) Are the skill's thresholds consistent with rps7200's constants?
- alpha=1.0 and beta=4096 duplicate bracket.DEFAULT_ALPHA/DEFAULT_BETA instead of importing them, and fit_noise_params has no production caller.
- metrics.FULL_SCALE is dead.
- The windows are not forced odd, as defects.py forces them.
- CLIP_START 0.80 is the rail-side gate for both the metering target and agreement_z, but it is applied in different domains.

Console: use_utf8_stdout() is the first statement of main() in all 23 tools, and the first line of parse_capture's __main__ block. It is safe when stdout/stderr are None (pythonw): getattr returns None and print is a no-op. The driver's own print/_log strings are ASCII-only. The only mismatch is console.py's claim that the GUI log pane replaces stdout; it does not.


Further defects found:
- byte14_probe starts a full scan in its finally block even after Ctrl-C or a USB error mid-read.
- byte14_probe crashes, losing its JSON, when a custom --ladder has no 0x10.
- dpi_analysis selects entries by a hardcoded filing-time window rather than a tag.
- fast_ir_probe turns an unmeasurable agreement into the verdict "unchanged".
- The SKILL's cross-frame sensor test has no implementation, and comparing output columns across entries mixes up sensor columns.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [MSQ-01](#measure-scan-quality-skill-msq-01) | high | bug | noise_split measures 'random' from an unregistered, un-gain-matched, unfiltered difference against a high-passed 'total'; share can exceed 100% and ceiling() silently turns that into the most optimistic ceiling | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-09](#measure-scan-quality-skill-msq-09) | high | hardware-safety | byte14_probe starts a full scan in its finally block after any failure, including Ctrl-C or a USB error during a read | [P14](../problems/P14-failure-paths-keep-driving-device.md) |
| [MSQ-A1](#measure-scan-quality-skill-msq-a1) | high | doc-mismatch | The mandated comparison files show a correction production never applies (destripe on an arbitrary scans/ TIFF), not the shading path through library.corrected | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-02](#measure-scan-quality-skill-msq-02) | medium | bug | dpi_analysis compares corrected lower-dpi passes against an uncorrected 7200 dpi reference, takes its noise floor from an uncorrected pair, and never checks or records correction state | [P06](../problems/P06-7200dpi-realignment-not-recorded.md) |
| [MSQ-03](#measure-scan-quality-skill-msq-03) | medium | bug | dpi_analysis selects its series by a hardcoded filing-time window, not by tag, frame or source | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-04](#measure-scan-quality-skill-msq-04) | medium | doc-mismatch | agreement_z's noise model: the ideal median \|z\| is 0.674, not 1.0; the model understates sigma about 1.5x; alpha/beta are bracket's unmeasured fallback typed in again | -- |
| [MSQ-05](#measure-scan-quality-skill-msq-05) | medium | design | agreement_z judges noise floor and saturation with bracket's absolute DN gates on whatever domain it is fed (corrected, per its contract) and takes the median over pixels the fit excluded | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-06](#measure-scan-quality-skill-msq-06) | medium | design | The metrics' input contract (corrected uint16 (H,W,C)) is neither enforced nor honoured by any caller; FULL_SCALE is dead | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-07](#measure-scan-quality-skill-msq-07) | medium | design | The SKILL's cross-frame sensor test has no implementation, and colour_deviation is indexed by output column, which maps to different sensor columns across entries | [P30](../problems/P30-comparison-files-and-metrics.md) |
| [MSQ-08](#measure-scan-quality-skill-msq-08) | medium | doc-mismatch | make_comparison, which writes the mandated comparison files, reports the metrics the skill forbids, using its own differently normalised copy of colour_deviation, and measures in memory instead of reading back what it wrote | -- |
| [MSQ-10](#measure-scan-quality-skill-msq-10) | low | bug | byte14_probe hardcodes LADDER[0] as baseline and drift reference; a custom --ladder without 0x10 prints ratio 1.000 everywhere, then crashes with IndexError after all scans, before the JSON is written | -- |
| [MSQ-11](#measure-scan-quality-skill-msq-11) | low | bug | fast_ir_probe turns an unmeasurable agreement (NaN) into inf and then into the verdict 'unchanged' | -- |
| [MSQ-12](#measure-scan-quality-skill-msq-12) | low | test-gap | metrics.py is untested and half unused; an even window crashes | -- |
| [MSQ-13](#measure-scan-quality-skill-msq-13) | low | doc-mismatch | Doc and comment claims in this area that the code contradicts | -- |
| [MSQ-A2](#measure-scan-quality-skill-msq-a2) | low | doc-mismatch | SKILL.md's solve_relation example on whole images fits one relation across all channels mixed together | -- |
| [MSQ-14](#measure-scan-quality-skill-msq-14) | info | design | Pixel-defined high-pass makes noise figures resolution-dependent, yet the skill and docs compare them across dpi | -- |
| [MSQ-15](#measure-scan-quality-skill-msq-15) | info | design | use_utf8_stdout is called first in every tool and is safe under pythonw; the gap is ad-hoc scripts, which are harmless today | -- |

## Findings in full

<a id="measure-scan-quality-skill-msq-01"></a>

### MSQ-01 -- noise_split measures 'random' from an unregistered, un-gain-matched, unfiltered difference against a high-passed 'total'; share can exceed 100% and ceiling() silently turns that into the most optimistic ceiling

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:51-66`, `.claude/skills/measure-scan-quality/scripts/metrics.py:69-78`, `.claude/skills/measure-scan-quality/scripts/metrics.py:27-34`, `tools/byte14_probe.py:209-218`, `tools/dpi_analysis.py:150-159`

**Doc claim:** SKILL.md:50-65 ("Two scans at one exposure differ solely by what is random per pass; grain, detail and fixed pattern cancel"); metrics.py:55-58 ("the only honest way")

The random/fixed split rests on grain, detail and fixed pattern cancelling in x-y. Three things in the code break that:
(a) There is no registration. Any row or column offset between the repeats leaves grain and detail in the difference. The repo measures 1-3 line carriage drift even with a re-home, and a 16-column offset at 3600 dpi. With pixel-uncorrelated grain at 3x the random sigma, a 1-line shift alone makes the measured share about 1.12 against a true value of about 0.32 (analytic).
(b) The filters differ. 'random' is unfiltered; 'total' is the horizontal high-pass, which keeps 0.8 of white-noise variance. Pure white noise therefore reads a share of 1/sqrt(0.8) = 1.118. A per-row random component (line-to-line lamp flicker) is counted fully in 'random' and removed from 'total'.
(c) There is no gain or offset match between repeats, so an exposure drift leaks scene structure into the difference. The dpi study reports 1.3-1.4% drift across its series.
All three bias the share upward. ceiling() clamps 'fixed' to 0 once random >= total, so the worse the measurement, the more improvement it promises: a share of 1.118 with n=9 gives -62.7%, where the skill's slide figure is -3.5%. In addition, the probes feed raw pixels, and the CCD mask is read per pass (direct.py:2667). If two passes' masks differ, output column j is a different CCD element in each, and the raw column pattern does not cancel either.

**Evidence (from the code):**

```text
metrics.py:64-65 `random_sigma = float(np.std((x - y)[mask]) / np.sqrt(2))` / `total_sigma = float(np.std(_highpass(x)[mask]))`. _highpass (27-34) is `np.apply_along_axis(lambda r: np.convolve(np.pad(r, pad, mode="edge"), np.ones(k) / k, "valid"), 1, plane)`, a horizontal 5-tap box only. ceiling (76) is `fixed = np.sqrt(max(total_sigma**2 - random_sigma**2, 0.0))`. fast_ir_probe.py:387-390 prints: "`noise_split`'s random share ... comes out above 100%". TODO.md:520-532 says passes registered at "0, 0, -1, -2, -2, -3 lines ... with byte 14 bit 0 clear" and "Anything comparing passes pixel by pixel should register first". dpi_analysis.py:17-19 itself says "passes sit at different column offsets -- two 3600 dpi passes once correlated at r=0.936 only after a 16-column shift". Yet byte14_probe.py:214 calls `noise_split(pair[0], pair[1], mask, channel=1)` and dpi_analysis.py:156 calls `noise_split(a, b, mask, channel=c)`, both with no alignment.
```

**Failure scenario:** Following SKILL.md:59-62 ('Compute this ceiling before spending scanner time'), a repeat pair whose carriage start differs by one line gives a share near 1.0, and ceiling() predicts about -60% for 9 passes. A 25-minute multi-pass run is booked that can deliver about -3%. Separately, dpi_analysis's 'floors' come out inflated, so C's residual/sigma ratios shrink toward 'this step adds nothing'.

**Fix:** Register both rows and columns first, as fast_ir_probe does with uniformity.register. Fit gain and offset (solve_relation) before differencing. Apply the same high-pass to both: random = std(hp(x)-hp(y))/sqrt(2). Make ceiling() refuse or flag share >= 1 instead of clamping. Require corrected inputs with each pass's own mask. Add the registration requirement to SKILL.md.

<details><summary>Second reader's check</summary>

Checked against the code. metrics.py:64 computes random as std(x-y)/sqrt2 on the unfiltered difference. metrics.py:65 computes total as std(_highpass(x)), and _highpass (27-34) is a horizontal 5-tap box. For white noise, x - box5(x) keeps (1 - 2/5 + 1/5) = 0.8 of the variance, so a purely random plane reads share 1/sqrt(0.8) = 1.118. ceiling():76 clamps fixed to 0, so share 1.118 with n=9 gives 1.118/3 - 1 = -62.7%. byte14_probe.py:214 and dpi_analysis.py:156 call noise_split on unregistered pairs. The ladder mixes byte14 bit-0 values that change carriage behaviour. dpi_analysis.py:17-19 itself documents a 16-column offset between 3600 dpi passes. No gain or offset matching exists anywhere. The CCD mask is re-read per pass (direct.py:2667). Two qualifications. ceiling() has no caller in code, so the -60% booking scenario runs only through the SKILL's instructions. The recommendation's 'as fast_ir_probe does with uniformity.register' is only half right: fast_ir_probe uses register for drift (295) and a row-only np.roll search (_aligned_z, 565-573), with no column registration.

</details>

<a id="measure-scan-quality-skill-msq-09"></a>

### MSQ-09 -- byte14_probe starts a full scan in its finally block after any failure, including Ctrl-C or a USB error during a read

**Severity** high · **Category** hardware-safety · **Verdict** confirmed · **Problem** [P14](../problems/P14-failure-paths-keep-driving-device.md)

**Where:** `tools/byte14_probe.py:172-192`, `tools/byte14_probe.py:140-151`

**Doc claim:** byte14_probe.py:177-180 ("end on the default value regardless, as a guard") vs CLAUDE.md 'Never abandon a read mid-scan'

The finally block runs on every exit. After a KeyboardInterrupt, a UsbError/TimeoutError, or a CheckCondition raised inside a pass's READ loop, it immediately sends a new MODE SELECT and START SCAN on the same handle. For exceptions it re-raises, the new scan runs before the exception propagates. A Ctrl-C mid-read abandons that read (CLAUDE.md: 'Never abandon a read mid-scan'), and this adds a fresh scan on top of the abandoned one. Nothing checks device state first. If metering itself failed, `scales` is unbound, and the NameError is swallowed. The extra pass is filed under the mandatory RPS7200_DEBUG=1 with keep_raw=False, so by the already-reported stale-last_raw mechanism it gets pass 13's raw bytes. This belongs to the reported family 'tools keep sending commands after a mid-read failure', but this tool was not named there.

**Evidence (from the code):**

```text
byte14_probe.py:172-185:
```
except BaseException as exc:
    print(f"\nprobe stopped: ...")
    if not isinstance(exc, (KeyboardInterrupt, CheckCondition)):
        raise
finally:
    ...
    try:
        scanner.scan(resolution=args.resolution, infrared=False,
                     frame=FULL_FRAME, exposure_scale=scales,
                     auto_exposure=False, shading=False, keep_raw=False,
                     byte14=None)
```
```

**Failure scenario:** The operator presses Ctrl-C during pass 7 (byte14 0x11) to stop a run that looks wrong. The read is abandoned, and the finally block issues another 600 dpi scan to the device mid-read. The scanner wedges and needs a power cycle.

**Fix:** Run the restore pass only when the ladder finished normally. Never run it after KeyboardInterrupt, UsbError, TimeoutError or anything raised during a read. If it runs at all, use keep_raw=True. Or drop it, since byte14 is not persisted, which is fast_ir_probe's own reasoning at fast_ir_probe.py:257-260.

<details><summary>Second reader's check</summary>

byte14_probe.py:172-185: the finally block always calls scanner.scan(...) with no state check, including after a KeyboardInterrupt or CheckCondition raised mid-read, and before any re-raise of other exceptions (UsbError, TimeoutError). DirectScanner has no guard that refuses a new MODE SELECT/START after an abandoned read: a grep finds no abandoned-read flag. If open() or metering failed, scales is unbound, and the NameError is swallowed by the inner except. keep_raw=False plus debug filing: _debug_capture (direct.py:650-690) takes capture_record()['raw'] = self.last_raw, which is set only when keep_raw is True (1518-1521). The final pass is therefore filed with the previous pass's raw bytes.

</details>

<a id="measure-scan-quality-skill-msq-a1"></a>

### MSQ-A1 -- The mandated comparison files show a correction production never applies (destripe on an arbitrary scans/ TIFF), not the shading path through library.corrected

**Severity** high · **Category** doc-mismatch · **Verdict** found-by-verifier · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/make_comparison.py:95-122`, `rps7200/defects.py:154`, `rps7200/library.py:338-390`

**Doc claim:** CLAUDE.md:179-184 ('Regenerate the three comparison files after any significant change to the scan or correction path'); make_comparison.py:2-8 ('2_corrected.tif corrections applied')

CLAUDE.md:179-184 says to regenerate 1_nothing_done/2_corrected/3_corrected_inverted after any change to the scan or correction path, and treats Stefan's reading of them as authoritative. The tool does not exercise that path. '2_corrected' is a column-defect destripe that no operator-facing output uses. The flat-field correction everything else delivers (apply_shading via library.corrected) is never applied. The input is any TIFF path, by default one under scans/, not a library entry. A delivered scans/ TIFF is already shading-corrected, so '1_nothing_done' is not necessarily the raw decode either. A change to apply_shading, decode or the reference therefore produces identical comparison files, and a destripe change that production never runs changes them.

**Evidence (from the code):**

```text
make_comparison.py:117 `corrected = destripe(raw, defects, margin=12, dilate=5)`. :98 `scan_path = args[0] if args else "scans/negatives/state_1800dpi.tif"`; :101 `raw = tiff.read(scan_path)`. A grep for destripe( finds calls only in make_comparison.py and tests/test_defects.py; rps7200/direct.py only re-exports it (lines 32, 243). Nothing in scan(), session or library.corrected calls destripe or find_column_defects.
```

**Failure scenario:** After a change to apply_shading, Claude regenerates the three files as instructed and Stefan judges them unchanged and clean. The shipped correction was never in them, so the regression reaches the GUI, Save As and roll frames without being seen.

**Fix:** Build the comparison from a library entry: 1 = library.load (raw decode), 2 = library.corrected (today's correction), 3 = its inversion. Keep destripe out, or label it as a separate experimental output. Read the written files back before reporting any metric.

<a id="measure-scan-quality-skill-msq-02"></a>

### MSQ-02 -- dpi_analysis compares corrected lower-dpi passes against an uncorrected 7200 dpi reference, takes its noise floor from an uncorrected pair, and never checks or records correction state

**Severity** medium · **Category** bug · **Verdict** partly · **Problem** [P06](../problems/P06-7200dpi-realignment-not-recorded.md)

**Where:** `tools/dpi_analysis.py:69-72`, `tools/dpi_analysis.py:131-159`, `tools/dpi_analysis.py:175-196`, `tools/dpi_analysis.py:216-221`, `tools/dpi_analysis.py:269-274`, `rps7200/library.py:372-390`, `rps7200/direct.py:2566-2578`, `rps7200/direct.py:1667-1678`, `rps7200/session.py:1440-1458`

**Doc claim:** docs/dpi-tradeoff-plan.md:334-336 ("Re-runnable. Everything came from stored raw bytes. When the shading correction lowers the noise floor, run tools/dpi_analysis.py again"); dpi_analysis.py:22-23; TODO.md:615-616

dpi_analysis loads every rung through library.corrected() and neither checks nor records the per-entry correction state. Any full-width 7200 dpi pass exceeds MAX_SHADING_COLUMNS=5172 and can only be filed raw, and the documented series' top rung was. That makes the B reference, the method A crop, the C crop and the noise-floor pair uncorrected, while the <=3600 dpi rungs are flat-fielded. GUI single-scan entries in the window are shaded twice, because session._scan files corrected pixels with no raw_image and no corrections= marker. Re-running after a shading change moves only the lower rungs.

**Evidence (from the code):**

```text
dpi_analysis.py:71 `a, _ = library.corrected(entry["dir"])`, which discards record['corrected']. Line 131 `top = max(rgb, key=lambda e: e["dpi"])`, line 132 `repeats = [e for e in rgb if e["dpi"] == top["dpi"]]`. library.py:379-384 `if skipped: record["corrected"] = ("deliberately raw" ... ); return image, record`. direct.py:2567-2571 `if needed > self.MAX_SHADING_COLUMNS: raise ShadingUnavailable(... "pass shading=False to accept raw pixels deliberately.")`. library.py:366-370 names "two 7200 dpi passes" from 2026-09-11 filed raw; the tool's default window is `--after 2026-09-11T10:19 --before 2026-09-11T10:45`. docs/dpi-tradeoff-plan.md:285-287 says the sanity band "reads 0.75-0.98".
```

**Failure scenario:** The 3600 dpi quality default and the finding that '7200 adds nothing' rest on the B ratios in docs/dpi-tradeoff-plan.md:275-283, computed against a raw reference. After a shading change the tool is re-run as advised, the lower rungs' spectra change, and the ratios shift. The shift reads as a change in resolving power.

**Fix:** Load every rung in the same domain: raw for all (library.load or decode_raw), or corrected for all with the reference taken at 3600 dpi. Print and store record['corrected'] per entry, and refuse a mixed series. Detect and exclude entries whose pixels are already corrected (GUI-tagged).

<details><summary>Second reader's check</summary>

The mixed domain is real. dpi_analysis.plane():71 and :152/:153/:216 use library.corrected, which returns raw for skipped entries (library.py:378-384) and applies shading otherwise (388), and results.json records no correction state. The GUI double-correction is also real: session._scan (1456) calls _file with no raw_image, so FrameWriter._write files job['image'], the corrected pixels, with no corrections= argument. library.corrected then shades them again. The overclaim: 'No 7200 dpi entry can be shading-corrected' is not what the code enforces. scan() refuses only when _shading_columns_needed(frame,res) > 5172 (direct.py:2566-2567, 1677-1678). A 7200 dpi pass over a frame narrower than about 18 mm, i.e. less than 5172 columns, can be corrected. Every full-width 7200 dpi entry, including the 2026-09-11 series, is raw. The effect on B's ordering is unmeasured: the reference's extra column-pattern power inflates each rung's denominator by a band-dependent amount. 'Sanity band consistent with this' is conjecture. Medium rather than high, because this is an offline analysis tool.

</details>

<a id="measure-scan-quality-skill-msq-03"></a>

### MSQ-03 -- dpi_analysis selects its series by a hardcoded filing-time window, not by tag, frame or source

**Severity** medium · **Category** bug · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `tools/dpi_analysis.py:46-66`, `tools/dpi_analysis.py:115-118`, `rps7200/library.py:166`, `rps7200/library.py:215`, `docs/dpi-tradeoff-plan.md:145-157`

**Doc claim:** docs/dpi-tradeoff-plan.md:145-157 (dpi_series.py, tag 'dpi-series', 'Reuses rps7200.library.load()', log-log plot and 100% crop comparison): none of it exists in code

Any entry with resolution_dpi and channels that was filed inside the window joins the ladder: another frame, a prescan, a GUI scan, a bracket member, or a demo entry in the same root. Nothing checks that the rungs share a frame, film, notes or scan frame. The 'repeat pair' is simply the first two top-dpi entries in created order. 'created' is the filing time, which for debug-filed entries and tools/scan.py batches is after the session ends, so a window chosen from scan times can include or miss passes. On any other library the defaults select nothing and the tool exits 1.

**Evidence (from the code):**

```text
dpi_analysis.py:52 `if not (after <= d["created"] <= before): continue`. Lines 116-117 `default="2026-09-11T10:19"`, `default="2026-09-11T10:45"`. library.py:166 `when = datetime.now(timezone.utc)` and :215 `"created": when.isoformat(timespec="seconds")`, i.e. filing time. The plan says "runs Part 3 over the library entries carrying that tag" and `tags=["dpi-series"]`, but neither tools/dpi_series.py nor the tag 'dpi-series' exists anywhere in the code.
```

**Failure scenario:** A 7200 dpi entry of a different frame is filed inside the window and becomes repeats[1]. noise_split then subtracts two different pictures, and the 'noise floor' used by method C is scene difference.

**Fix:** Select by explicit entry ids or a tag written at capture time. Assert that all rungs share frame coordinates, film and notes. Write the selected entry ids and their correction states into results.json.

<details><summary>Second reader's check</summary>

dpi_analysis.py:52 filters only by created time, and the defaults are at 116-117. library.py:166/215 set created to the filing time, not the scan time. No tools/dpi_series.py exists and no 'dpi-series' tag appears anywhere in the code, so docs/dpi-tradeoff-plan.md:145-157 describes tools that do not exist. repeats[0..1] are simply the first two top-dpi entries. One more boundary defect: the lexicographic comparison against '2026-09-11T10:45' excludes any created value of '2026-09-11T10:45:SS+00:00', so the whole final minute drops out. A missing scan.tif raises in the stat() at :63.

</details>

<a id="measure-scan-quality-skill-msq-04"></a>

### MSQ-04 -- agreement_z's noise model: the ideal median |z| is 0.674, not 1.0; the model understates sigma about 1.5x; alpha/beta are bracket's unmeasured fallback typed in again

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:81-102`, `rps7200/bracket.py:52-56`, `rps7200/bracket.py:147-193`, `.claude/skills/measure-scan-quality/SKILL.md:74-80`

**Doc claim:** metrics.py:88-90 vs docs/multi-exposure-plan.md:143-147; SKILL.md:77 ("two repeats give ~1.03")

For z ~ N(0,1), median|z| = 0.6745. A repeat baseline of 1.03 means the model's sigma is about 1.53x too small (variance about 2.3x), not 'slightly'. The code docstring and the plan disagree on what the ideal is. The constants are bracket's unmeasured fallback, typed in again as literals rather than imported: a second home for them. No production path fits them (fit_noise_params has no non-test caller; tools/scan.py's merge_bracket call also uses the defaults). On the declared corrected input, var(corrected) = gain^2 * var(raw), with the gain varying per column, so one alpha/beta pair is wrong in a column-dependent way. Because var = var_x + var_y/slope^2, the size of the understatement also depends on the slope whenever the real shot/read split differs from 1.0/4096. Nothing establishes the claim that the bias is 'equal for every comparison'.

**Evidence (from the code):**

```text
metrics.py:82 `alpha: float = 1.0, beta: float = 4096.0`. Docstring 88-90: "Two repeats at one exposure give about 1.03 ... not 1.0, because the noise model slightly understates the truth, equally for every comparison." bracket.py:52-56: "Fallback Poisson-Gaussian constants, used only when no flats are available to fit ... Measure instead: see fit_noise_params". fit_noise_params is called only in tests/test_bracket.py. docs/multi-exposure-plan.md:145 contradicts the docstring: "It exceeds the ideal ~0.67".
```

**Failure scenario:** Bracket pairs at x1.4 and x3.7 are both judged against a 1.03 baseline with a model whose error changes with the ratio. The boundary of 'agreement holds to x1.7' moves with an unmeasured constant rather than with the scanner.

**Fix:** Import the constants from bracket, and fit alpha/beta per channel with fit_noise_params or from the repeat pair itself. Correct the docstring: the ideal is 0.674, and baselines should be reported as ratios to it. Model the per-column gain when the input is corrected.

<details><summary>Second reader's check</summary>

For z ~ N(0,1), median|z| = 0.6745, so a repeat baseline of 1.03 means sigma is understated about 1.53x, which is not 'slightly'. The metrics.py:88-90 docstring implies the ideal is 1.0 ('not 1.0, because...'), while docs/multi-exposure-plan.md:145 says the ideal is ~0.67, so the two disagree. metrics.py:82 retypes bracket.DEFAULT_ALPHA/DEFAULT_BETA (bracket.py:55-56) as literals. fit_noise_params has test callers only, and tools/scan.py:254 calls merge_bracket with the default alpha/beta. bracket.py:13-15 claims the constants 'are measured from our own flats by fit_noise_params rather than assumed', which production code contradicts. That is a further doc mismatch.

</details>

<a id="measure-scan-quality-skill-msq-05"></a>

### MSQ-05 -- agreement_z judges noise floor and saturation with bracket's absolute DN gates on whatever domain it is fed (corrected, per its contract) and takes the median over pixels the fit excluded

**Severity** medium · **Category** design · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:92-102`, `rps7200/bracket.py:39-46`, `rps7200/bracket.py:214-226`, `rps7200/shading.py:252-276`, `rps7200/shading.py:171-176`

**Doc claim:** metrics.py:7 ("Everything works on shading-corrected linear samples"); bracket.py:26-29

This answers question 2. The relation is fitted on the requested channel only, so agreement_z does not inherit merge_bracket's green-only ratio (bracket.py:297). It does inherit the absolute gates, which describe the raw sensor's knee. On the shading-corrected samples metrics.py:7 requires, the column gain is mean/light. With the ~39% x-falloff CLAUDE.md cites, that is about 0.8-0.85 at the centre and 1.3-1.4 at the edges (an estimate). So 0.80 FS corrected corresponds to about 0.95-0.98 FS raw at centre columns, inside the knee the gate exists to exclude, and about 0.6 FS at the edges. Railed raw samples in columns with gain < 1 come out below the rail and look usable. The z median then runs over the whole mask, including pixels where either pass was clipped or below the floor. For the infrared plane (fast_ir_probe channel=3), the visible-channel gates are applied to a plane with its own exposure. The only current caller feeds raw pixels, where the gates are physically right, but that caller is itself breaking the contract.

**Evidence (from the code):**

```text
bracket.py:216 `usable = (a > SNR_FLOOR) & (a < CLIP_START) & (b > SNR_FLOOR) & (b < CLIP_START)`. bracket.py:42-45 `CLIP_START = 0.80 * FULL_SCALE`, commented "It starts well below the 16-bit rail because a CCD goes non-linear before it saturates". shading.py:258 `gain = np.where(span > 0, (mean - dark_mean) / ..., 1.0)`, with mean the mean over all CCD columns (shading.py:175), and :275 `np.clip(vals, 0, maxval, out=vals)`. metrics.py:96 `solve_relation(a[..., channel], b[..., channel])`, and :102 `return float(np.median(z[mask]))` with no usable filter.
```

**Failure scenario:** Following the skill, Claude runs agreement_z on two corrected library entries. Highlights in the bright centre columns, raw-clipped or in the knee, enter the fit and the z median as if linear, and the pair reads as disagreeing more than it does.

**Fix:** Evaluate the floor and clip on raw samples: pass the raw arrays, or divide the gain back out using each entry's reference and mask. Exclude non-usable pixels from the z median. Document which domain the thresholds refer to.

<details><summary>Second reader's check</summary>

bracket.solve_relation (bracket.py:216) gates on absolute SNR_FLOOR/CLIP_START. agreement_z fits per channel (metrics.py:96), so it does not inherit merge_bracket's green-only ratio. metrics.py:102 takes the median over the whole mask with no usable filter, so clipped and floor pixels are included. For fast_ir_probe's IR channel the mask is all ones (fast_ir_probe.py:570-571). On corrected input, apply_shading (shading.py:256-276) is a two-point dark-subtract-and-gain with a clip at 0/maxval, so the 0.8 FS gate no longer marks the raw knee. The per-column gain magnitudes (0.8-1.4) are estimates from a doc claim, not code. The only live caller (fast_ir_probe) feeds raw, where the gates fit the sensor, but the median-over-unusable problem hits it too.

</details>

<a id="measure-scan-quality-skill-msq-06"></a>

### MSQ-06 -- The metrics' input contract (corrected uint16 (H,W,C)) is neither enforced nor honoured by any caller; FULL_SCALE is dead

**Severity** medium · **Category** design · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:7`, `.claude/skills/measure-scan-quality/scripts/metrics.py:13`, `.claude/skills/measure-scan-quality/scripts/metrics.py:16-24`, `tools/byte14_probe.py:142-151`, `tools/fast_ir_probe.py:207-218`, `tools/dpi_analysis.py:69-72`

**Doc claim:** metrics.py:7

This answers question 1.
- byte14_probe: 600 dpi RGB raw centre crops.
- fast_ir_probe: 1800 dpi RGBI 4-channel raw centre crops, with the IR plane used as channel 3.
- dpi_analysis: library.corrected(). That is corrected up to 3600 dpi, raw and realigned at 7200, and doubly corrected for GUI entries.
- No current caller passes 8-bit prescans or un-realigned decode_raw at 7200.

Nothing checks dtype, correction state or domain. On 8-bit data, agreement_z's SNR_FLOOR=131, CLIP_START=52428 and beta=4096 are meaningless: every 255-clipped sample counts as usable. On raw input, the horizontal high-pass keeps the column-to-column fixed pattern that shading removes, so 'total', and with it the random share, are not comparable with the skill's 21%/27% figures. dark_mask excludes nothing: not zero-clipped corrected samples (apply_shading clips at 0), not railed samples, not opaque transport. 4-channel input is handled via [..., :3]. 2-D input fails with an AxisError.

**Evidence (from the code):**

```text
metrics.py:7 "Everything works on shading-corrected linear samples, `(H, W, C)` uint16." byte14_probe.py:148-149 and fast_ir_probe.py:214-215 both pass `shading=False, keep_raw=True`. direct.py:2757-2758: `else: shading_skipped = SHADING_SKIPPED_EXPLICIT`, so the returned image is left uncorrected. metrics.py:13 `FULL_SCALE = 65535.0` is referenced nowhere. dark_mask (23-24) `lum = image.astype(np.float64)[..., :3].mean(axis=2); return lum < np.percentile(lum, percentile)`.
```

**Failure scenario:** byte14_probe's 'random share' per byte14 value is read against the skill's 21-27% baseline, but its 'total' includes the raw column pattern. The share comes out systematically lower, and the probe appears to show a scanner more dominated by fixed pattern than it is.

**Fix:** Validate dtype (uint16) and ndim. Take an explicit domain ('raw' or 'corrected') or the library record, and refuse mixed pairs. Have the tools pass library entries so the correction state is visible. Remove FULL_SCALE or use it for the gates.

<details><summary>Second reader's check</summary>

metrics.py:7 declares a corrected uint16 contract that nothing enforces. byte14_probe.py:148-149 and fast_ir_probe.py:214-215 pass shading=False, which yields SHADING_SKIPPED_EXPLICIT and uncorrected pixels (direct.py:2757-2758). dpi_analysis goes through library.corrected, which gives a mixed domain as in MSQ-02. FULL_SCALE (metrics.py:13) is referenced nowhere. dark_mask (23-24) excludes neither clipped nor zero samples. A 2-D input fails at [..., :3].mean(axis=2). The effect of the fixed pattern on byte14_probe's share is qualitative: it plausibly lowers the share, but the size is not measured. Still a real contract gap.

</details>

<a id="measure-scan-quality-skill-msq-07"></a>

### MSQ-07 -- The SKILL's cross-frame sensor test has no implementation, and colour_deviation is indexed by output column, which maps to different sensor columns across entries

**Severity** medium · **Category** design · **Verdict** confirmed · **Problem** [P30](../problems/P30-comparison-files-and-metrics.md)

**Where:** `.claude/skills/measure-scan-quality/SKILL.md:37-43`, `.claude/skills/measure-scan-quality/scripts/metrics.py:105-124`, `rps7200/shading.py:185-193`, `rps7200/bracket.py:26-29`

**Doc claim:** SKILL.md:37-43; CLAUDE.md:231-233 ("Two entries from different frames settle it")

CLAUDE.md:231-233 prescribes this as the way to separate sensor from picture, but nothing in metrics.py or any tool maps output columns to sensor columns. The obvious use is to compare colour_deviation arrays from two entries index by index. That compares different CCD elements whenever the entries differ in frame x0, in resolution, or in the per-pass CCD mask. A real sensor defect can then be dismissed as picture content, or a coincidence promoted to a defect. The sign check the SKILL asks for (line 42-43) does not rescue a column mismatch.

**Evidence (from the code):**

```text
SKILL.md:39-41: "A sensor defect sits at a fixed **sensor** column across *different film positions* ... Two library entries from different frames settle it, and nothing else does." metrics.py:119 `col = np.median(image[..., c].astype(np.float64), axis=0)` works per output column. shading.py:185-193 build_width_to_loc: "The j-th *used* pixel in the mask is the reference column for output column j." bracket.py:27-28: "the passes carry different masks". The CCD mask is read per pass (direct.py:2667).
```

**Failure scenario:** Two entries of different frames at 1800 dpi whose masks differ by one used element: a violet line at sensor column k appears at output column j in one entry and j+1 in the other. The index-by-index comparison reports no common column, and the defect is attributed to the film.

**Fix:** Add a helper that projects each entry's colour_deviation onto sensor columns using its ccd_mask.bin, frame and resolution (build_width_to_loc), then compares sign-matched deviations. Document it in SKILL.md.

<details><summary>Second reader's check</summary>

colour_deviation (metrics.py:119) works on output columns, and no function in metrics.py or tools maps output columns to sensor columns. shading.build_width_to_loc (shading.py:185-193) shows that output column j maps to the j-th used mask element, and the mask is re-read per pass (direct.py:2667). The SKILL's cross-frame test (37-43) therefore has no implementation that accounts for frame x0, resolution or mask differences. Comparing index by index misattributes defects exactly as described.

</details>

<a id="measure-scan-quality-skill-msq-08"></a>

### MSQ-08 -- make_comparison, which writes the mandated comparison files, reports the metrics the skill forbids, using its own differently normalised copy of colour_deviation, and measures in memory instead of reading back what it wrote

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/make_comparison.py:36-55`, `tools/make_comparison.py:58-78`, `tools/make_comparison.py:120-136`

**Doc claim:** SKILL.md:21-35 and :84-89; CLAUDE.md:223-231 ("Use signed, channel-relative deviation"; "Measure the file you delivered")

SKILL.md:27-35 says np.abs destroys the violet/green signal and to 'Never use worst column deviation as a quality figure'. This tool prints exactly that ('worst column defect'). Its 'coloured' metric is a separate copy of colour_deviation that differs in three ways:
- It is normalised by one level shared by all channels, not per channel, which under-weights the orange-masked negative's weaker channel.
- For an RGBI TIFF it averages over all four channels, so the IR plane enters the channel-relative mean.
- It reduces to a worst-column maximum, which the skill says picture content dominates.
The WARNING compares two such maxima. Two more rules are broken: 'Read back from disk' (SKILL.md:84-87), since it measures the arrays and never re-reads 2_corrected.tif; and 'Send 100% crops, not downscaled previews' (SKILL.md:88-89), since the previews are half size. If previews/ does not exist, the save raises after the three TIFFs are already written.

**Evidence (from the code):**

```text
make_comparison.py:52-53 `dev = np.abs(col - smooth) / np.median(col)`, `interior = max(interior, 100 * dev[edge:-edge].max())`. Lines 73-76 `level = float(np.median(prof, axis=0).mean())`, `dev = (prof - smooth) / level`, `colour = dev - dev.mean(axis=1, keepdims=True)`, `strength = np.abs(colour).max(axis=1)`. Line 131 `if fix_c > raw_c * 1.2: print("  WARNING: ...")`. Lines 124-129 measure `raw` and `corrected` in memory after the tiff.write calls. Line 136 `.resize((v.shape[1] // 2, v.shape[0] // 2)).save(f"previews/{name}.png")`.
```

**Failure scenario:** A correction that swaps a violet line for a green one of the same magnitude scores unchanged on worst_defect and the same on worst_colour's max, so no warning fires. Or a single hard vertical edge in the picture dominates both 'worst' numbers and hides a real 5% coloured line.

**Fix:** Import colour_deviation from the skill, restricted to RGB. Report signed per-column statistics over the interior rather than worst values. Read the written TIFFs back before measuring. Write 100% crops. Create previews/ or write next to the TIFFs.

<details><summary>Second reader's check</summary>

make_comparison.worst_defect:52 uses np.abs and prints 'worst column defect', which the SKILL forbids as a quality figure. worst_colour (58-78) normalises by a single level shared across channels (73) and includes all channels, including IR on a 4-channel TIFF (70-75). It reduces to a worst-column max (76-78). The WARNING (131) compares two maxima. The metrics are computed on the in-memory raw and corrected arrays (124-129), and the written TIFFs are never re-read. The previews are half size (136). previews/ is never created, so the save raises after the TIFFs are written. A bigger related problem is reported separately as MSQ-A1.

</details>

<a id="measure-scan-quality-skill-msq-10"></a>

### MSQ-10 -- byte14_probe hardcodes LADDER[0] as baseline and drift reference; a custom --ladder without 0x10 prints ratio 1.000 everywhere, then crashes with IndexError after all scans, before the JSON is written

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/byte14_probe.py:156-158`, `tools/byte14_probe.py:201-207`, `tools/byte14_probe.py:226-228`, `tools/byte14_probe.py:230-234`

**Doc claim:** byte14_probe.py:91-94 (--ladder help: "repeat a value for the pair noise_split needs")

With `--ladder 0x20,0x20,0x21,0x21`, baseline_ms is never set, so every line prints ratio=1.000 and the summary stores ratio_to_0x10=1.0. The drift line then takes [-1] of an empty list, raises IndexError, and args.json is never written. The passes are debug-filed, but byte14 is not recorded in library entries (already reported), so the JSON was the only record linking each pass to its byte14 value.

**Evidence (from the code):**

```text
Line 156 `if byte14 == LADDER[0] and baseline_ms is None:`. Line 201 `base_group = [r["ms_per_line"] for r in results if r["byte14"] == LADDER[0]]`. Line 227 `f"ms/line, last {[r for r in results if r['byte14']==LADDER[0]][-1]['ms_per_line']:.2f} "`. The JSON write only happens after that, at 230-234.
```

**Failure scenario:** Five minutes of scanner time for a four-value ladder ends in a traceback, and the filed entries cannot say which byte14 each pass used.

**Fix:** Use ladder[0] as the baseline, guard the drift line, and write the JSON before any derived printing.

<details><summary>Second reader's check</summary>

byte14_probe.py:156 and :201 hardcode LADDER[0] rather than the ladder actually used. With a --ladder that lacks 0x10, every ratio prints as 1.0, and :227 indexes [-1] of an empty list. That raises IndexError before the JSON write at 230-234. The meta dict (direct.py ~2760-2810) has no byte14 field, so the JSON is the only record linking each pass to its byte14 value.

</details>

<a id="measure-scan-quality-skill-msq-11"></a>

### MSQ-11 -- fast_ir_probe turns an unmeasurable agreement (NaN) into inf and then into the verdict 'unchanged'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/fast_ir_probe.py:565-573`, `tools/fast_ir_probe.py:328-341`, `.claude/skills/measure-scan-quality/scripts/metrics.py:96-98`, `rps7200/bracket.py:216-218`

**Doc claim:** fast_ir_probe.py:55-59 ("If the picture degrades at all, the answer is no")

agreement_z returns NaN when fewer than 64 pixels are usable, for example an infrared plane above CLIP_START across the crop, or a channel under SNR_FLOOR. (nan, s) never compares below (inf, 0), so every pair records inf with shift 0. The family medians become inf, and inf <= inf*1.15 prints 'unchanged'. The summary JSON gets Infinity, which is not valid strict JSON.

**Evidence (from the code):**

```text
fast_ir_probe.py:566 `best = (float("inf"), 0)`, :572 `best = min(best, (agreement_z(left, right, mask, channel=channel), shift))`. metrics.py:97-98 `if not np.isfinite(slope): return float("nan")`. bracket.py:217-218 `if usable.sum() < 64: return float("nan"), 0.0`. fast_ir_probe.py:338-340 `verdict = ("unchanged" if np.median(fam["off/on"]) <= np.median(control) * 1.15 else ...)`.
```

**Failure scenario:** The IR plane is too bright to fit, and the probe reports 'off/on against a same-setting control: unchanged' for infrared. That supports adopting the flag with no measurement behind it.

**Fix:** Propagate NaN, print 'not measurable', and require finite control and test values before issuing a verdict.

<details><summary>Second reader's check</summary>

_aligned_z starts at (inf, 0) (fast_ir_probe.py:566). A (nan, s) tuple never compares less, so if every shift is NaN (solve_relation returns NaN when fewer than 64 pixels are usable, bracket.py:217-218, and agreement_z propagates it at metrics.py:97-98), the result is (inf, 0). With control and test both inf, the check at 338-340 evaluates inf <= inf*1.15, which is True, and prints 'unchanged'. json.dumps with default=float writes Infinity.

</details>

<a id="measure-scan-quality-skill-msq-12"></a>

### MSQ-12 -- metrics.py is untested and half unused; an even window crashes

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:37-48`, `.claude/skills/measure-scan-quality/scripts/metrics.py:69-78`, `.claude/skills/measure-scan-quality/scripts/metrics.py:105-148`, `rps7200/defects.py:18-20`

**Doc claim:** CLAUDE.md:237-240

The metrics CLAUDE.md singles out ('colour-opposed column deviation', 'the ceiling that split implies') are exercised by nothing. With window=24, the 'valid' convolution returns W+1 values against col's W, so a broadcast ValueError is raised. When a channel's median is 0, the division gives inf/NaN with no guard. No test pins any of the numeric claims (1.03, 0.897->0.265, -3.5%, share <= 1).

**Evidence (from the code):**

```text
A repo-wide grep finds no caller in tools/, rps7200/ or tests/ for relative_noise, ceiling, colour_deviation or fixed_pattern, and no test imports metrics. metrics.py:116-121 `pad = window // 2 ... np.convolve(np.pad(col, pad, mode="reflect"), np.ones(window) / window, "valid")`, whereas defects.py:20 has `k = max(3, int(window) | 1)`.
```

**Failure scenario:** A refactor changes _highpass or colour_deviation and no test fails. Or an analysis passes window=24 and crashes.

**Fix:** Add synthetic tests: white-noise share, a shifted pair, the sign of a violet/green pair, ceiling monotonicity, a 4-channel input. Force odd windows as defects.py does.

<details><summary>Second reader's check</summary>

A grep finds no caller of relative_noise, ceiling, colour_deviation or fixed_pattern outside .claude/, and no test imports metrics: test_linearity and test_exposure_probe mention the names in docstrings only. With an even window such as 24, pad=12 and the 'valid' convolution yields W+1 values against col's W, so a broadcast error is raised. defects.py forces an odd window. A zero channel median divides to inf/NaN with no guard.

</details>

<a id="measure-scan-quality-skill-msq-13"></a>

### MSQ-13 -- Doc and comment claims in this area that the code contradicts

**Severity** low · **Category** doc-mismatch · **Verdict** partly

**Where:** `.claude/skills/measure-scan-quality/SKILL.md:16-17`, `tools/dpi_analysis.py:22-23`, `tools/dpi_analysis.py:69-72`, `tools/exposure_probe.py:69-71`, `tools/gain_probe.py:19-21`, `tools/gain_probe.py:86-93`, `rps7200/console.py:34-36`, `rps7200/usb_transport.py:413-415`, `rps7200/direct.py:941`

**Doc claim:** SKILL.md:16-17; dpi_analysis.py:22-23,70; TODO.md:615-616; exposure_probe.py:69-70; gain_probe.py:19-21; console.py:34-36; CLAUDE.md:237-240

Doc and comment claims that the code contradicts: SKILL.md's import line omits ceiling and depends on the cwd. dpi_analysis's docstrings claim it reads the delivered file and raw bytes, but it reads scan.tif through library.corrected. exposure_probe promises an offline agreement_z and noise split that nothing implements. gain_probe collects a repeat pair it never analyses. console.py claims the GUI log pane replaces stdout, but gui.py does not, and transport and warm-up prints bypass log_hook. The fast_ir_probe note is not a mismatch.

**Evidence (from the code):**

```text
- SKILL.md:17 `from metrics import dark_mask, relative_noise, noise_split, agreement_z, colour_deviation` omits `ceiling`, which is used at :62; :16 inserts the cwd-relative path ".claude/skills/measure-scan-quality/scripts".
- dpi_analysis.py:70 "One channel, read back from the delivered file", but the body is `library.corrected(...)`.
- dpi_analysis.py:22-23 "which is the point of keeping the raw bytes" and TODO.md:615 "rebuilds it from stored raw bytes": raw.bin.gz is never read, only scan.tif.
- exposure_probe.py:69-70 "`agreement_z` on each frame's x1.00 pair, the fixed/random noise split -- runs offline from the filed raw bytes": no such code exists.
- gain_probe.py:19-21 and :66 take the repeat at 21 as "what noise_split needs", but measure() (86-93) computes only median, p99.5 and at_rail.
- console.py:34-36 "under a captured or replaced stdout ... which is what the test suite and the GUI's log pane provide": gui.py never references sys.stdout.
- fast_ir_probe.py:387-390 blames the >100% share on 'a plane that is mostly smooth base'.
- byte14_probe.py:13-15 "running the ladder again will reproduce reversed passes exactly".
```

**Failure scenario:** Claude follows these docs: it copies the skill's import from audit/raw and gets an ImportError, then pastes the example and gets a NameError on ceiling(); it trusts the exposure/gain probes' pairs to have been analysed; it expects the GUI pane to show USB errors.

**Fix:** Fix the import line, or make it absolute from __file__. Correct the docstrings. Either implement the offline agreement/noise analysis for the exposure and gain probes or remove the claim. Route transport and warm-up messages through log_hook.

<details><summary>Second reader's check</summary>

Sub-items (1)-(4) are confirmed. SKILL.md:17 omits ceiling, and :16 inserts a cwd-relative path. dpi_analysis.py:70 says 'read back from the delivered file', but it recomputes via library.corrected, and it never reads raw.bin.gz. exposure_probe.py:69-70 promises an offline agreement_z and noise split, and no code implements them (linearity.py does not call them). gain_probe.measure (86-93) computes no noise split. console.py:34-36 claims the GUI log pane replaces stdout, but gui.py never touches sys.stdout/stderr, and usb_transport._log (413-415) and wait_warm's print (direct.py:941) bypass log_hook. Sub-item (5) is refuted. fast_ir_probe's note that the box high-pass 'understates total high-frequency content on a plane that is mostly smooth base' is consistent with the filter-mismatch mechanism: on a smooth plane total is close to random, and the 0.8 retention pushes the share past 1. Sub-item (6) is weak. The docstring's claim that the device reproduces reversed reads is still about device behaviour; decode_index (direct.py:1533) now turns such passes upright, so only the analytic consequence is stale.

</details>

<a id="measure-scan-quality-skill-msq-a2"></a>

### MSQ-A2 -- SKILL.md's solve_relation example on whole images fits one relation across all channels mixed together

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `.claude/skills/measure-scan-quality/SKILL.md:69-72`, `rps7200/bracket.py:196-226`

**Doc claim:** .claude/skills/measure-scan-quality/SKILL.md:69-72

solve_relation flattens its input. Given (H,W,C) scans, as the SKILL's surrounding text implies, it pools R, G, B (and IR if present) into one least-squares fit. The channels sit at very different levels on an orange-masked negative, and blue has a different RGBI ratio, so the fitted slope and intercept describe a channel mixture rather than any one channel's exposure relation. agreement_z avoids this by slicing a channel first (metrics.py:96). The SKILL's standalone example does not.

**Evidence (from the code):**

```text
SKILL.md:72 `slope, intercept = solve_relation(a, b)    # from rps7200.bracket`, where a and b are scans. bracket.py:214-215 `a = np.asarray(ref, dtype=np.float64).reshape(-1)` / `b = np.asarray(other, ...).reshape(-1)`.
```

**Failure scenario:** Following the SKILL, Claude solves the relation on two full RGB passes, gets a slope dominated by the brightest channel, and then applies it to blue, where it is wrong.

**Fix:** Show a per-channel call in the SKILL (solve_relation(a[...,c], b[...,c])), or make solve_relation reject ndim > 2.

<a id="measure-scan-quality-skill-msq-14"></a>

### MSQ-14 -- Pixel-defined high-pass makes noise figures resolution-dependent, yet the skill and docs compare them across dpi

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `.claude/skills/measure-scan-quality/scripts/metrics.py:27-48`, `.claude/skills/measure-scan-quality/SKILL.md:57-59`

**Doc claim:** SKILL.md:57-59; docs/multi-exposure-plan.md:25-26

Five pixels is 423 um of film at 300 dpi and 35 um at 3600 dpi. relative_noise and noise_split's 'total' therefore measure different physical frequency bands at each resolution, and comparing them across dpi mixes grain being resolved with a real change in noise. This is not a defect when used at a single resolution.

**Evidence (from the code):**

```text
metrics.py:27 `def _highpass(plane, k: int = 5)` uses a fixed 5 pixels. SKILL.md:57 "the random share was 21% at 300 dpi and 27% at 1800". docs/multi-exposure-plan.md:25 "Shadow noise measures 12.63% at 1800 dpi and 5.76% at 3600".
```

**Failure scenario:** Someone concludes that 3600 dpi 'halves shadow noise' from relative_noise numbers that come partly from the kernel shrinking in physical size.

**Fix:** Express the kernel in physical units (e.g. µm, derived from dpi), or state in SKILL.md that the figures compare only at one resolution.

<details><summary>Second reader's check</summary>

_highpass has k=5 pixels fixed (metrics.py:27), so the physical band changes with dpi. Cross-dpi comparisons of relative_noise and total are not like for like. This is an observation, not a defect at a single resolution.

</details>

<a id="measure-scan-quality-skill-msq-15"></a>

### MSQ-15 -- use_utf8_stdout is called first in every tool and is safe under pythonw; the gap is ad-hoc scripts, which are harmless today

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/console.py:31-47`, `tools/gui.py:8113-8114`, `tools/parse_capture.py:97-98`, `rps7200/direct.py:509-511`, `rps7200/direct.py:941`

**Doc claim:** console.py:22-24 ("Called at the top of each tool's main()")

Confirmed: under pythonw, print() to a None stream is a no-op, and argparse swallows AttributeError. Scripts that import DirectScanner directly, as CLAUDE.md's 'RPS7200_DEBUG=1 uv run python your_script.py' encourages, never call use_utf8_stdout. That is currently harmless, because the driver's own messages are ASCII. The protection lives in each tool rather than in the library, so the first non-ASCII character added to a _log line brings back the UnicodeEncodeError-mid-read hazard console.py describes, for every ad-hoc script.

**Evidence (from the code):**

```text
All 23 tools/*.py main() functions begin with `use_utf8_stdout()`; parse_capture calls it first in `if __name__ == "__main__":`. console.py:39-41 `reconfigure = getattr(stream, "reconfigure", None); if reconfigure is None: continue` handles stdout=None. A grep for non-ASCII inside print()/_log() in rps7200/*.py finds nothing; framing.py:1003's '°' goes through session log events, not print.
```

**Failure scenario:** Someone adds '→' to a DirectScanner._log message, and an ad-hoc probe with redirected output on a cp1252 Windows host dies mid-READ.

**Fix:** Also call use_utf8_stdout() (idempotent) from DirectScanner.__init__ or open(), or keep a test asserting that direct.py/usb_transport.py log strings are ASCII.

<details><summary>Second reader's check</summary>

Every tools/*.py main() calls use_utf8_stdout() as its first statement: gui.py:8114, and parse_capture:98 under __main__. console.py:39-41 skips streams without reconfigure, including None under pythonw. There is no library-level call, so ad-hoc scripts rely on the driver's messages staying ASCII.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| dpi study results | <--out, default probe/dpi>/results.json | JSON: aliasing{channel:{dpi:{ratio,sanity}}}, noise_floor_dn{channel:float}, absolute{channel:{limit_cycles_per_mm,dpi_to_sample,floor_density}}, steps{dpi:{channel:{residual_dn,noise_sigma,ratio}}}, series[{dir,dpi,channels,seconds,created,exposure,bytes}]. Python json writes NaN tokens | Derived from library.corrected(): corrected up to 3600 dpi, raw at 7200 dpi, doubly corrected for GUI entries. The per-entry correction state is NOT recorded | tools/dpi_analysis.py:269-274 (non-atomic write_text, at the end) | Nothing in code; humans and docs/dpi-tradeoff-plan.md | No: derived floats, with no entry ids beyond the directory path, no correction state and no code revision |
| byte14 probe summary | <--json path> | JSON: passes[{index,byte14,resolution,height,duration_s,ms_per_line (rounded to 4 dp),exposure}], summary[{byte14,n,mean_ms_per_line,ratio_to_0x10,random_dn,total_dn,random_share}] | Derived from raw (shading=False) in-memory centre crops | tools/byte14_probe.py:230-234, only if the report completes (IndexError at :227 prevents it for ladders without 0x10) | Nothing in code | No. It is also the only record of each pass's byte14, because library entries do not store byte14 (already reported) |
| fast-IR probe summary | <--json path> | JSON: passes[...], summary{drift_lines, ms_per_line_off/on, time_saved, agreement_1/3 {family: median\|z\|}, specks[...]}; may contain Infinity | Derived from raw (shading=False) RGBI in-memory centre crops | tools/fast_ir_probe.py:392-396 and sweep_report 513-517 | Nothing in code | No: derived statistics; inf is used as a sentinel for 'no measurement' |
| Library entries of probe passes | library/<UTC-filing-time>_<film>_<dpi>dpi[_ir]/ {raw.bin.gz, scan.tif, ccd_mask.bin, shading.npz?, scan.json} | As library.save writes them; scan.json has shading_skipped='shading=False (explicit)' and fast_infrared; no byte14, no probe tag | Raw (deliberately uncorrected) | DirectScanner debug filing at close(). byte14_probe and fast_ir_probe refuse to run without RPS7200_DEBUG (byte14_probe.py:113-117, fast_ir_probe.py:177-181) | library tools; dpi_analysis only if inside its time window | raw.bin.gz is exact for keep_raw=True passes. byte14_probe's final keep_raw=False pass inherits stale raw bytes (already-reported mechanism). The varied parameter byte14 is not recorded |
| Comparison files Stefan judges | ./1_nothing_done.tif, ./2_corrected.tif, ./3_corrected_inverted.tif (cwd) | uint16 TIFF via rps7200.tiff.write, lossless, with a resolution tag computed from width | 1 = the input TIFF as read; 2 = destripe()d (not apply_shading); 3 = inverted by preview.normalise | tools/make_comparison.py:120-122 | Stefan by eye; the tool's own metrics measure the in-memory arrays, not these files | The TIFFs are lossless copies of the arrays. The input comes from scans/ TIFFs, not library raw bytes |
| Comparison previews | previews/cmp_before.png, previews/cmp_after.png | 8-bit PNG, inverted, downscaled 2x | Derived for display | tools/make_comparison.py:134-136 (raises if previews/ is missing) | Humans | No: 8-bit, half resolution |
| Library entry fields consumed by the metrics path | library/*/scan.json, scan.tif, shading.npz, ccd_mask.bin | scan.json keys: created (filing time, ISO UTC), scan.resolution_dpi, scan.channels, scan.duration_s, device_settings.exposure, image.corrections_applied, calibration.skipped/shading/ccd_mask; scan.tif uint16 (H,W,C) | Stored raw. Corrected on read by library.corrected() unless 'already' or skipped | rps7200/library.py save() | tools/dpi_analysis.py:46-72 via library.corrected (library.py:338-390) | scan.tif is the decode, realigned at 7200 dpi. Corrected output is recomputed with today's code; raw.bin.gz is never consulted by this path |

**Second reader's corrections to this table:**

- **byte14 probe summary.** The ms_per_line values come from meta['duration_s'], and direct.py rounds that to 0.1 s. So 'rounded to 4 dp' is spurious precision. The real resolution is 0.1 s over the pass height.
- **Library entries of probe passes.**
  - The path is library/<YYYYmmddTHHMMSSZ filing time>_unknown-film_<dpi>dpi[_ir]. debug filing passes FilmNotes(notes='captured with RPS7200_DEBUG on') with no stock, and the tags are ['debug'].
  - The root can be redirected by the DirectScanner.DEBUG_ROOT_ENV environment variable.
  - 'created' is the time _debug_flush ran after close(), so every pass of one probe run carries nearly the same created value.
- **Comparison files.**
  - Input 1 is whatever TIFF path is given, by default scans/negatives/state_1800dpi.tif. That may itself be a delivered, already shading-corrected file, so '1_nothing_done' is not guaranteed raw.
  - Output 2 is destripe(), which production never applies. Nothing in this path touches library raw bytes or apply_shading.
  - The previews are made as (invert(img)/256).astype(uint8) and resized half-size with PIL. They are written only if previews/ already exists.
- **Library entry fields consumed by the metrics path.** GUI single-scan entries (tags include 'gui') hold corrected pixels with an empty corrections_applied, because session._scan never passes raw_image. So 'stored raw' is false for them, and library.corrected() corrects them a second time.
- **dpi results.json.** The per-entry fields include 'bytes' (scan.tif size), 'dir' (stringified path) and 'created' (filing time). The 'exposure' field is the device_settings exposure list, not exposure_scale.

## What the operator can do

- Run `uv run python tools/dpi_analysis.py --root library --after <ISO> --before <ISO> --out <dir>` offline, with no scanner, to recompute the resolution study from stored entries.
- Run tools/byte14_probe.py or tools/fast_ir_probe.py with --dry-run to print the pass plan and time budget without opening the device.
- Run the probes for real only with RPS7200_DEBUG=1 (they refuse otherwise) and after asking Stefan. They file every pass through DirectScanner debug filing.
- Import dark_mask, relative_noise, noise_split, ceiling, agreement_z, colour_deviation and fixed_pattern from .claude/skills/measure-scan-quality/scripts/metrics.py when the working directory is the repo root.
- Run tools/make_comparison.py <scan.tif> <flat.tif> to regenerate 1_nothing_done.tif, 2_corrected.tif and 3_corrected_inverted.tif.
- Redirect or pipe any tool's output on a non-UTF-8 Windows console; every tool reconfigures stdout/stderr to UTF-8 with errors='replace' first.

## What the operator should not do

- Do not feed noise_split or agreement_z a repeat pair that has not been registered in rows and columns. The carriage start drifts 1-3 lines between passes, and a 16-column offset has been seen.
- Do not treat a noise_split random share near or above 100% as a result, or pass it to ceiling(). ceiling() clamps and returns its most optimistic answer.
- Do not compare colour_deviation arrays from two entries index by index unless frame, resolution and CCD mask all match; output column j is not the same sensor column otherwise.
- Do not quote dpi_analysis's B, A or C numbers without checking each rung's correction state. The 7200 dpi reference and its noise floor are always uncorrected.
- Do not press Ctrl-C during a byte14_probe pass: its finally block then starts another full scan on a device left mid-read.
- Do not run byte14_probe with a custom --ladder that lacks 0x10.
- Do not feed 8-bit prescans or mixed raw/corrected pairs to agreement_z; its DN thresholds and noise model assume 16-bit samples in one domain.
- Do not use make_comparison's 'worst column defect' / 'worst coloured column' numbers or its WARNING as a quality verdict; the skill forbids exactly these figures.
- Do not pass an even window to colour_deviation or fixed_pattern.
- Do not read the '~1.03 baseline' as meaning the noise model is nearly right. The ideal is 0.674.

## Mistakes nothing guards against

- dpi_analysis silently includes any entry filed inside its time window (another frame, a GUI scan, a prescan, a demo entry) and treats the first two top-dpi entries as a repeat pair.
- dpi_analysis never tells the operator that the 7200 dpi reference is raw while the other rungs are corrected, or that a GUI entry was shaded twice.
- noise_split accepts misregistered and gain-mismatched pairs without warning, and ceiling() clamps a share above 1 silently.
- agreement_z accepts any dtype and domain. In fast_ir_probe, a NaN result becomes inf and then the verdict 'unchanged'.
- byte14_probe with a --ladder lacking 0x10 prints ratio 1.000 for every value and then crashes after the scans, so the only byte14-to-pass record (the JSON) is never written.
- byte14_probe's finally block issues a new scan after KeyboardInterrupt, CheckCondition or a USB/timeout error mid-read, which risks a wedge. That pass is filed with keep_raw=False, i.e. with stale raw bytes.
- make_comparison crashes after writing the three TIFFs if previews/ does not exist, and its default inputs are fixed paths under scans/.
- Copying the SKILL.md import line from a directory other than the repo root raises ImportError, and calling ceiling() after that import raises NameError.

## Dataflow notes

OFFLINE PATH (tools/dpi_analysis.py)
1. ladder() (dpi_analysis.py:46-66) globs <root>/*/scan.json. It keeps entries whose `created` (filing time, library.py:166/215) lies inside --after/--before and that have scan.resolution_dpi and scan.channels. It sorts them by (channels, dpi, created).
2. plane() (69-72) calls library.corrected(dir) (library.py:338-390), which does the following:
   - library.load (314-335) reads scan.tif, shading.npz (ShadingReference.load) and ccd_mask.bin.
   - If image.corrections_applied contains 'shading', the stored pixels are returned ('already').
   - If calibration.skipped is set, they are returned raw. This is the case for every 7200 dpi entry, because direct.py:2567 refuses to shade beyond 5172 columns.
   - With no reference, they are returned raw.
   - Otherwise apply_shading runs (shading.py:196-278): (raw - dark_j)*(mean - dark_mean)/(light_j - dark_j), rounded, clipped to [0, 65535].
   - GUI single-scan entries are filed already corrected but not marked, so they are corrected a second time here.
3. The one channel returned is converted to float64. Then:
   - radial_spectrum (75-102) computes the row-averaged Hann-windowed rfft power density, and band_density (105-109) gives method B's ratios against the top-dpi pass.
   - The repeat pair at the top dpi goes through metrics.dark_mask (metrics.py:16-24) and metrics.noise_split (51-66). Both passes are loaded via corrected() with no registration, and the result is `floors`.
   - Method A uses a centre crop of the top pass. Method C downsamples that crop, restores it, and compares the residual against `floors`.
4. The output goes to stdout and to <out>/results.json (269-274).

LIVE PROBES (byte14_probe, fast_ir_probe)
1. DirectScanner.open, session_start, wait_warm, auto_exposure (metered once, then held).
2. For each pass: scan(shading=False, keep_raw=True, byte14=..., fast_infrared=...). Bytes are read, decode_index turns the pass upright from its line tags, the 7200 realign runs where it applies, and there is no flat-field (direct.py:2757-2758). The result is a raw uint16 (H,W,C) array.
3. A centre crop is kept in memory (byte14_probe.py:81-84, fast_ir_probe.py:119-122).
4. byte14_probe: dark_mask then noise_split on the first two passes per byte14 value, unaligned (209-218).
5. fast_ir_probe:
   - Drift is measured with uniformity.register (294-296).
   - _aligned_z (547-573) tries row shifts of -4..4 via np.roll and calls metrics.agreement_z (81-102) for each.
   - agreement_z calls bracket.solve_relation (bracket.py:196-226), a per-channel OLS on pixels inside SNR_FLOOR..CLIP_START. It then computes z with alpha=1 and beta=4096 and takes the median over the dark mask (or all pixels for IR).
   - The results are grouped into families, and a verdict is issued with a 1.15 factor.
   - speck_pixels and speck_depth use metrics._highpass(k=9).
6. The debug spool files every pass into library/ at close() through library.save. The results also go to stdout and the optional --json.

COMPARISON FILES (tools/make_comparison.py)
1. tiff.read(scan_path, flat_path), taken from scans/ by default.
2. flat_defect_sigma, resample_reference and find_column_defects produce a defect mask, and destripe applies it.
3. tiff.write writes 1_nothing_done.tif, 2_corrected.tif and 3_corrected_inverted.tif.
4. worst_defect (np.abs) and worst_colour (a local copy of colour_deviation, normalised by a level shared by all channels) run on the in-memory arrays, not on the written files.
5. Half-size 8-bit PNGs go to previews/.

SKILL.md
Ad-hoc analysis scripts insert the cwd-relative skill path into sys.path and import metrics. agreement_z pulls rps7200.bracket, which works because the rps7200 package is installed.

CONSOLE
Every tool's main() calls rps7200.console.use_utf8_stdout() first: it reconfigures stdout/stderr to UTF-8 with errors='replace' and skips None streams under pythonw. DirectScanner._log prints only when verbose and also forwards to log_hook, which is the GUI pane. usb_transport._log (413-415) and wait_warm's print (direct.py:941) go to stdout only.
