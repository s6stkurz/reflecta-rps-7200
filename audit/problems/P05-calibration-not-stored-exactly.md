# P05 -- Calibration bytes are thrown away; only a derived reference is kept

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 11 findings in 8 areas

[Back to the summary](../README.md)

## The problem

`calibrate_shading` reads the calibration pass line by line, reduces it with
`calculate_shading` to float64 per-column means, and keeps only that `ShadingReference`
(`shading.npz` in each entry, `calibration/shading.npz` as a cache). The raw calibration
lines, the 128-byte calibration-info block, the calibration CCD mask, the gain/offset
read-backs and the settings it ran with are discarded (`keep_data=False` by default and
`ensure_shading` never asks for it). So the shading reference -- the input to every
correction -- **cannot be recomputed with newer code**, which is exactly what the library
promises for everything else.

Nothing records whether an entry's reference was measured this session or loaded from the
cache (`--reuse`, "Use the cached one"), or how old it was. A failed cache write discards
a successful 3-4 minute calibration.

## Fix plan

1. Keep the calibration pass like a scan: store its raw bytes (gzipped) and layout in a
   `calibration/<timestamp>/` entry, with the info block, mask and settings.
2. Each scan entry records `shading.source` = `{calibration_id, measured|reused, age_s}`
   and keeps its `shading.npz` as today (so old readers still work).
3. `library.corrected()` can then rebuild the reference from the calibration bytes with
   today's `calculate_shading` when asked.
4. Write the cache atomically (temp + `os.replace`) and keep the in-memory reference if
   the write fails.

## Evidence (from [TP-04](../areas/transport-protocol.md#transport-protocol-tp-04))

**Where:** `rps7200/direct.py:1895-1973`, `rps7200/shading.py:75-91`, `rps7200/shading.py:109-182`, `rps7200/direct.py:572-629`, `rps7200/library.py:281-296`

```text
direct.py:1946-1966 `data = b"".join(collected)`, `self._shading = calculate_shading(data, width)`, `"data": data if keep_data else None` -- no caller in rps7200/ or tools/ passes keep_data. ShadingReference.save stores only `ref{c}`, `mean{c}`, `dark{c}`, `darkmean{c}`, pixels_per_line (shading.py:80-91), which are float64 means produced by a level-split heuristic (`split_ratio`, widest-gap cut, shading.py:158-178). The calibration info READ(128), shading descriptor, calibration-pass gain/offset, calibration CCD mask, block count, duration and time are logged only. ensure_shading(reuse=True) loads calibration/shading.npz (600-610), and nothing in scan meta or library.save says whether the reference was measured this power-on or loaded from disk.
```

**Failure scenario:** calculate_shading later turns out to mis-split a phase on some calibrations (a light line classed as dark). Every library entry's shading.npz carries the bad average, and `library.corrected()` can never be fixed for them. The GUI 'reuse' mode files a reference measured days earlier, and later analysis of striping cannot tell.

**Second reader's check:** calibrate_shading joins the calibration lines (`data = b"".join(collected)`, direct.py:1946), reduces them with calculate_shading (level-split with a widest-gap cut, shading.py:158-176), and returns `"data": data if keep_data else None`. A grep finds no caller in rps7200/ or tools/ that passes keep_data. ShadingReference.save stores only ref/mean/dark/darkmean/pixels_per_line (shading.py:80-91). The calibration info READ(128), the descriptor, the calibration gain/offset, the block count and the duration are logged only. ensure_shading(reuse=True) loads a stored reference (direct.py:600-610), and nothing in scan meta or library.save records whether the reference in force was measured this power-on or loaded from disk.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-04](../areas/transport-protocol.md#transport-protocol-tp-04) | transport-protocol | high | confirmed | Calibration raw bytes are discarded; only a reduced float reference is stored, and its provenance is not recorded | rps7200/direct.py:1895-1973, rps7200/shading.py:75-91, rps7200/shading.py:109-182 |
| [DDF-04](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-04) | decode-and-debug-filing | high | confirmed | The calibration pass's raw bytes are never stored; the library keeps only the host's derived float64 reference | rps7200/direct.py:1895-1973, rps7200/direct.py:631-646, rps7200/shading.py:75-91 |
| [LIB-04](../areas/library.md#library-lib-04) | library | high | confirmed | Shading reference is stored only as a derived product; the calibration bytes and calibration state are never kept | rps7200/direct.py:1755-1760, rps7200/direct.py:1946-1973, rps7200/shading.py:75-91 |
| [CLI-09](../areas/cli-operator-tools.md#cli-operator-tools-cli-09) | cli-operator-tools | high | confirmed | The calibration pass's raw bytes are discarded; only a derived ShadingReference is stored, and --reuse leaves no record that the reference was stale | rps7200/direct.py:1755-1760, rps7200/direct.py:1945-1970, rps7200/direct.py:600-623 |
| [PA-23](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-23) | probe-and-analysis-tools | high | confirmed | Calibration bytes are never stored: the library's shading.npz is a derived, heuristic parse and cannot be recomputed | rps7200/direct.py:1946-1950, rps7200/direct.py:1966, rps7200/shading.py:158-176 |
| [D06](../areas/docs-readme-claude.md#docs-readme-claude-d06) | docs-readme-claude | high | confirmed | shading.npz is a host-derived reduction; the calibration bytes and calibration-time settings are thrown away | rps7200/direct.py:1895-1950, rps7200/direct.py:1964-1967, rps7200/shading.py:109-182 |
| [DOC-03](../areas/docs-plans-todo.md#docs-plans-todo-doc-03) | docs-plans-todo | high | confirmed | The calibration pass's raw bytes are never stored; the library keeps only the processed ShadingReference | rps7200/direct.py:1755-1760, rps7200/direct.py:1946-1973, rps7200/direct.py:612-629 |
| [DDF-13](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-13) | decode-and-debug-filing | medium | confirmed | A reused shading reference is filed as if measured this session; its origin and age are not recorded | rps7200/direct.py:549-561, rps7200/direct.py:572-629, rps7200/direct.py:563-570 |
| [LIB-A2](../areas/library.md#library-lib-a2) | library | medium | found-by-verifier | Entry does not record where its shading reference came from (reused file vs. calibrated this session) or when | rps7200/direct.py:549-561, rps7200/direct.py:572-602, rps7200/library.py:281-296 |
| [DOC-11](../areas/docs-plans-todo.md#docs-plans-todo-doc-11) | docs-plans-todo | medium | confirmed | `--reuse` loads any cached reference silently, and entries do not record where the reference came from (TODO open item, still present) | rps7200/direct.py:600-610, rps7200/direct.py:549-561, rps7200/shading.py:75-91 |
| [RX-10](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-10) | resource-exhaustion-crash-recovery-time | medium | confirmed | A failed write of the calibration cache discards a successful 3-4 minute calibration and blocks all scanning; a truncated cache makes 'reuse' fail | rps7200/direct.py:612-629, rps7200/direct.py:563-570, rps7200/shading.py:75-91 |
