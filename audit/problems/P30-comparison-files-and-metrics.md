# P30 -- The comparison files and several metrics do not measure what ships

**Severity** high · **Group** E: Outputs, measurement and framing · **Reported independently by** 15 findings in 6 areas

[Back to the summary](../README.md)

## The problem

CLAUDE.md makes `1_nothing_done.tif / 2_corrected.tif / 3_corrected_inverted.tif` the
standing acceptance test, and Stefan's eye authoritative. `tools/make_comparison.py`
reads an arbitrary TIFF (default `scans/negatives/state_1800dpi.tif`) and applies
`destripe` -- a flat-file correction the driver never ships -- not `apply_shading` or
`library.corrected`. So the file Stefan judges is not what an operator gets. Also:

- `filing_load_test.py`'s verdict reports "safe" whenever passes vary at all, so the
  measurement CLAUDE.md relies on for the roll's gzip-while-scanning cannot say "unsafe";
- `noise_split` measures "random" from an unregistered, un-gain-matched difference against
  a high-passed total (share can exceed 100%);
- `dpi_analysis` selects its series by a hard-coded time window and compares uncorrected
  7200 dpi passes with corrected ones;
- `exposure_headroom` treats 8-bit prescans as 16-bit; `registration_margin` validates a
  different statistic from the one the roll gates on.

## Fix plan

1. `make_comparison.py` takes a library entry id and writes `decode` /
   `library.corrected(entry)` / inverted -- the delivered path, measured from the files it
   writes.
2. `filing_load_test.py`: a verdict with a stated threshold and a real "unsafe" outcome.
3. Fix the metric input contracts (corrected uint16 (H,W,C)) and enforce them.

## Evidence (from [CLI-08](../areas/cli-operator-tools.md#cli-operator-tools-cli-08))

**Where:** `tools/make_comparison.py:1-15`, `tools/make_comparison.py:94-120`, `tools/make_comparison.py:136`, `rps7200/library.py:338-391`

```text
`corrected = destripe(raw, defects, margin=12, dilate=5)`; `tiff.write("1_nothing_done.tif", raw, ...)` where `raw = tiff.read(scan_path)` and the default is `scan_path = ... "scans/negatives/state_1800dpi.tif"`. grep: destripe/find_column_defects/flat_defect_sigma are called only from tools/make_comparison.py. The production correction is `apply_shading(image, record["reference"], record["ccd_mask"])` in library.corrected(). Line 136: `.save(f"previews/{name}.png")` with no mkdir.
```

**Failure scenario:** A regression lands in apply_shading or decode_index. Claude regenerates the three files as instructed, sends them to Stefan, and they look unchanged, because neither function is on this tool's path.

**Second reader's check:** make_comparison.py:117 builds 2_corrected.tif with `destripe()`. grep shows destripe, find_column_defects and flat_defect_sigma called only here, while production correction is apply_shading via scan() and library.corrected() (library.py:338-391). Its input is an arbitrary TIFF path (default scans/negatives/state_1800dpi.tif), not a library entry, so a regression in decode_index, apply_shading or library.corrected cannot appear in the three files. Argument parsing drops anything starting with '-' (line 94). previews/ is gitignored and absent in this checkout, and line 136 writes into it without mkdir, so the run raises after writing the TIFFs. README.md:195 names this tool as the raw/corrected/inverted generator.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [CLI-08](../areas/cli-operator-tools.md#cli-operator-tools-cli-08) | cli-operator-tools | high | confirmed | make_comparison.py judges a destripe correction the pipeline never uses, on an arbitrary TIFF, under names that claim raw and corrected | tools/make_comparison.py:1-15, tools/make_comparison.py:94-120, tools/make_comparison.py:136 |
| [D08](../areas/docs-readme-claude.md#docs-readme-claude-d08) | docs-readme-claude | high | confirmed | make_comparison.py's '2_corrected.tif' uses a flat-file destripe the driver never ships, not apply_shading or library.corrected | tools/make_comparison.py:92-138, rps7200/defects.py:154, rps7200/library.py:338-391 |
| [DOC-01](../areas/docs-plans-todo.md#docs-plans-todo-doc-01) | docs-plans-todo | high | confirmed | The standing acceptance test (make_comparison.py) applies destripe, not the shading correction the driver ships | tools/make_comparison.py:24-29, tools/make_comparison.py:106-122, docs/vignette-plan.md:208-210 |
| [MSQ-A1](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-a1) | measure-scan-quality-skill | high | found-by-verifier | The mandated comparison files show a correction production never applies (destripe on an arbitrary scans/ TIFF), not the shading path through library.corrected | tools/make_comparison.py:95-122, rps7200/defects.py:154, rps7200/library.py:338-390 |
| [UT-12](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-12) | uncited-tests-vs-findings | medium | confirmed | test_defects validates destripe, which only make_comparison uses; the comparison files skip the shading every delivered file gets | tests/test_defects.py:148-217, tools/make_comparison.py:98-122, rps7200/library.py:388 |
| [CLI-07](../areas/cli-operator-tools.md#cli-operator-tools-cli-07) | cli-operator-tools | medium | confirmed | filing_load_test.py's verdict is a tautology: it reports 'safe' whenever passes vary at all, so the measurement CLAUDE.md relies on cannot fail | tools/filing_load_test.py:101-111, tools/filing_load_test.py:81-88, tools/filing_load_test.py:4 |
| [MSQ-01](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-01) | measure-scan-quality-skill | high | confirmed | noise_split measures 'random' from an unregistered, un-gain-matched, unfiltered difference against a high-passed 'total'; share can exceed 100% and ceiling() silently turns that into the most optimistic ceiling | .claude/skills/measure-scan-quality/scripts/metrics.py:51-66, .claude/skills/measure-scan-quality/scripts/metrics.py:69-78, .claude/skills/measure-scan-quality/scripts/metrics.py:27-34 |
| [MSQ-05](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-05) | measure-scan-quality-skill | medium | confirmed | agreement_z judges noise floor and saturation with bracket's absolute DN gates on whatever domain it is fed (corrected, per its contract) and takes the median over pixels the fit excluded | .claude/skills/measure-scan-quality/scripts/metrics.py:92-102, rps7200/bracket.py:39-46, rps7200/bracket.py:214-226 |
| [MSQ-06](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-06) | measure-scan-quality-skill | medium | confirmed | The metrics' input contract (corrected uint16 (H,W,C)) is neither enforced nor honoured by any caller; FULL_SCALE is dead | .claude/skills/measure-scan-quality/scripts/metrics.py:7, .claude/skills/measure-scan-quality/scripts/metrics.py:13, .claude/skills/measure-scan-quality/scripts/metrics.py:16-24 |
| [MSQ-07](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-07) | measure-scan-quality-skill | medium | confirmed | The SKILL's cross-frame sensor test has no implementation, and colour_deviation is indexed by output column, which maps to different sensor columns across entries | .claude/skills/measure-scan-quality/SKILL.md:37-43, .claude/skills/measure-scan-quality/scripts/metrics.py:105-124, rps7200/shading.py:185-193 |
| [MSQ-03](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-03) | measure-scan-quality-skill | medium | confirmed | dpi_analysis selects its series by a hardcoded filing-time window, not by tag, frame or source | tools/dpi_analysis.py:46-66, tools/dpi_analysis.py:115-118, rps7200/library.py:166 |
| [PA-06](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-06) | probe-and-analysis-tools | medium | confirmed | exposure_headroom treats 8-bit prescans as 16-bit (the uint8 cast wraps), and its default selection picks them | tools/exposure_headroom.py:78, tools/exposure_headroom.py:127-137, tools/exposure_headroom.py:183-189 |
| [PA-07](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-07) | probe-and-analysis-tools | medium | confirmed | exposure_headroom's dark-floor simulation ignores the CCD mask and depth scaling, and applies negative-film blue constants to every film | tools/exposure_headroom.py:119-124, tools/exposure_headroom.py:82, tools/exposure_headroom.py:155-160 |
| [PA-08](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-08) | probe-and-analysis-tools | medium | confirmed | registration_margin (and transport_truth) validate a different statistic from the one measure_shift_mm gates on | tools/registration_margin.py:73-75, tools/registration_margin.py:193-199, tools/registration_margin.py:220-222 |
| [PA-11](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-11) | probe-and-analysis-tools | medium | confirmed | exposure_probe reads delivered levels from a region re-detected on the metered pass, not from the region metering used | tools/exposure_probe.py:62-67, tools/exposure_probe.py:159-168, rps7200/direct.py:2104-2106 |
