# P29 -- Bracket merging judges the wrong domain and mixes RGBI with RGB

**Severity** high · **Group** E: Outputs, measurement and framing · **Reported independently by** 6 findings in 3 areas

[Back to the summary](../README.md)

## The problem

`merge_bracket` judges saturation on shading-corrected values, so its clipping gate does
not see sensor saturation. `--bracket N --ir` merges one RGBI pass -- whose blue comes
back about five times brighter -- with RGB passes using a ratio fitted on green only.
A single `--exposure-scale` value is silently ignored with `--bracket`. `scan.py` holds
every pass in RAM; high-dpi brackets need several to 10+ GB. Bracket membership is not
recorded (P09), so none of this can be redone offline.

## Fix plan

1. Judge saturation on raw pixels (`last_pixels_raw`), merge corrected.
2. Refuse `--bracket` with `--ir`, or fit per channel.
3. Reject a single `--exposure-scale` with `--bracket`.
4. Spool bracket passes to disk as debug filing does.

## Evidence (from [OUT-04](../areas/outputs.md#outputs-out-04))

**Where:** `rps7200/bracket.py:37-46`, `rps7200/bracket.py:125-134`, `rps7200/bracket.py:214-217`, `rps7200/bracket.py:315-326`, `rps7200/shading.py:260-280`, `rps7200/direct.py:2392-2401`, `tools/scan.py:205-221`

```text
`CLIP_START = 0.80 * FULL_SCALE`; `clip_w = 1.0 - _smoothstep((raw - CLIP_START) / max(CLIP_END - CLIP_START, 1e-12))`; solve_relation `usable = (a > SNR_FLOOR) & (a < CLIP_START) & (b > SNR_FLOOR) & (b < CLIP_START)`; the frames come from `self.scan(..., shading=shading)`, i.e. `vals = (image[...] - dark) * gain` with `gain = (mean - dark_mean) / (light - dark)` and then `np.clip(vals, 0, maxval)`.
```

**Failure scenario:** `tools/scan.py --bracket 3 --stops 2` on a thin colour negative. The longest pass rails in the thin areas. In the centre columns those railed samples carry about r^2 times the reference's weight and pull the merged highlights down by a column-dependent amount, giving banded, biased highlights. MergeStats still reports a normal-looking merge. The plan's requirement that a fully clipped pass be 'ignored, not averaged in' holds only on the synthetic, unshaded test data.

**Second reader's check:** tools/scan.py calls scan_bracket with shading on (default), so each frame is the output of apply_shading. shading.py:260-280 computes `vals = (image - dark) * gain` with gain = (mean - dark_mean)/(light - dark), then rounds and clips to maxval. bracket.confidence (125-134) and solve_relation (216) judge saturation on those corrected values with CLIP_START=0.80*FS. In a centre column with gain of about 0.8-0.85, a railed raw 65535 becomes roughly 52-56k and keeps confidence of 0.9-1.0. The longest pass is deliberately pinned to the exposure-timer ceiling (bracket_ladder 2304-2313), so railed samples are common. The residual gate does not rescue them either: its fallback `prefer` is argmax(weight), which is again the railed long pass. bracket.py:26-29 states the corrected-input design explicitly, but that is the flaw here, not a defence.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [OUT-04](../areas/outputs.md#outputs-out-04) | outputs | high | confirmed | Bracket merge judges saturation on shading-corrected values | rps7200/bracket.py:37-46, rps7200/bracket.py:125-134, rps7200/bracket.py:214-217 |
| [OUT-05](../areas/outputs.md#outputs-out-05) | outputs | high | confirmed | --bracket with --ir merges one RGBI pass (blue about 5x brighter) using a ratio fitted on green only | rps7200/direct.py:2373-2401, rps7200/direct.py:2518-2522, rps7200/bracket.py:291-301 |
| [DOC-05](../areas/docs-plans-todo.md#docs-plans-todo-doc-05) | docs-plans-todo | high | confirmed | `tools/scan.py --bracket N --ir` merges the RGBI pass's blue with RGB passes using a ratio fitted on green | rps7200/direct.py:2385-2401, tools/scan.py:248-260, rps7200/bracket.py:291-301 |
| [OUT-12](../areas/outputs.md#outputs-out-12) | outputs | medium | confirmed | tools/scan.py --bracket silently ignores a single-value --exposure-scale | tools/scan.py:143-146, tools/scan.py:205-218, rps7200/direct.py:2373-2378 |
| [DOC-27](../areas/docs-plans-todo.md#docs-plans-todo-doc-27) | docs-plans-todo | medium | confirmed | `tools/scan.py --bracket` silently ignores a single-value `--exposure-scale` | tools/scan.py:143-148, tools/scan.py:205-218, rps7200/direct.py:2373-2378 |
| [CLI-16](../areas/cli-operator-tools.md#cli-operator-tools-cli-16) | cli-operator-tools | medium | partly | scan.py holds every pass (raw pixels, raw bytes and corrected frame) in RAM; high-dpi brackets need several to 10+ GB | tools/scan.py:174-194, tools/scan.py:205-221, rps7200/direct.py:2408-2420 |
