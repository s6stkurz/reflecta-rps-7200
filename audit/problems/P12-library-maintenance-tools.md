# P12 -- reconstruct / verify / migrate-raw report or repair the wrong thing

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 3 findings in 3 areas

[Back to the summary](../README.md)

## The problem

`migrate-raw --write` treats *every* mismatch between `scan.tif` and `decode_raw` as
"mislabelled corrected pixels" and rewrites `scan.tif` from the bytes, with no backup and
no check that the stored pixels derive from those bytes -- so it can launder a decode
regression or destroy the only corrected rendition. `reconstruct` counts non-regressions
as changed decodes (7200 dpi, P06), aborts entirely on one missing `scan.tif`, and lets
`ScanReadError` escape. `verify` reports a deliberate `shading=False` scan as "correction
was asked for", which is why `make verify` has been red for a week and no longer read.

## Fix plan

1. `migrate-raw`: only rewrite when `apply_shading(decode_raw(entry), reference, mask)`
   reproduces the stored pixels (i.e. the difference is exactly one shading); otherwise
   report and leave the entry. Move the old file to `scan.tif.bak`.
2. `reconstruct`: per-entry `try`, classify `missing`, `unreadable`, `changed`, `same`,
   `same-after-known-transform`.
3. `verify`: honour `shading=False`/`calibration.skipped` as deliberate, and add a
   `deliberately-uncalibrated` tag for the byte-14 ladder so `make verify` goes green.

## Evidence (from [LIB-15](../areas/library.md#library-lib-15))

**Where:** `tools/library.py:137-216`, `rps7200/library.py:479-491`

```text
`if np.array_equal(plain, stored) and not applied: continue` / `planned.append((path, plain, applied, stored_shape))`, then `tiff.write(str(path / "scan.tif"), plain, ...)` and `image["corrections_applied"] = []`. No check is made that the entry has a reference, that meta['shading'] shows a correction ran, or that reconstruct's verdict was a known category. The shading-labelled 'reference is missing' verdict still returns an image, so it proceeds.
```

**Failure scenario:** A developer changes decode_index, runs `make reconstruct`, sees 'decode CHANGED' on many entries, reads CLAUDE.md, and runs `migrate-raw --write` to 'fix' them. The library now matches the broken decode, and the evidence that anything changed is gone.

**Second reader's check:** migrate-raw (tools/library.py:156-212) plans a rewrite for every entry where decode_raw has the same shape and is not equal (or where applied is non-empty). It ignores reconstruct's verdict, and it never checks that the stored image equals apply_shading(plain, ref, mask) or that a reference exists. Case (b) is reachable: reconstruct returns the decoded image even when its verdict is 'reference is missing' (473-477). The tool's comment says 'Both are repaired ... without guessing', but the code assumes that any difference means correction. A decode regression, a legacy entry without its reference, or a stale-raw debug entry would each have scan.tif irreversibly replaced, with no backup kept.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [LIB-15](../areas/library.md#library-lib-15) | library | high | confirmed | migrate-raw --write treats every mismatch as 'mislabelled corrected pixels': it can launder a decode regression, destroy the only corrected rendition, or overwrite a pass's only pixels | tools/library.py:137-216, rps7200/library.py:479-491 |
| [CLI-17](../areas/cli-operator-tools.md#cli-operator-tools-cli-17) | cli-operator-tools | medium | confirmed | `library.py reconstruct` counts non-regressions as changed decodes and aborts entirely on one missing scan.tif | tools/library.py:96-113, rps7200/library.py:451-503, rps7200/direct.py:1582-1596 |
| [DOC-06](../areas/docs-plans-todo.md#docs-plans-todo-doc-06) | docs-plans-todo | medium | confirmed | `verify` reports a deliberate shading=False scan as 'correction was asked for'; code comments and TODO claim it splits the two cases correctly | rps7200/library.py:794-802, rps7200/direct.py:296-303, rps7200/library.py:359-364 |
