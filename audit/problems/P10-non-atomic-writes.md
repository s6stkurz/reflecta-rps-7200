# P10 -- Entries and manifests are written in place; partial files look complete

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 11 findings in 7 areas

[Back to the summary](../README.md)

## The problem

Every writer except `settings.save` writes its final path directly, with no temporary
file, rename or fsync. `library.save` writes `scan.tif`, `raw.bin.gz`, `shading.npz`,
`ccd_mask.bin` and finally `scan.json`; a crash in between leaves a directory that
`entries()`/`verify` never see (they key on `scan.json`), yet whose name is taken. The id
reservation is a non-atomic "does scan.json exist" check, so two writers in the same
second (FrameWriter + debug flush, GUI + CLI) can write into **one** directory.
`migrate-raw --write` and `migrate-direction --write` rewrite `scan.tif` before `scan.json`
in place. `shading.npz`, `ccd_mask.bin` and `prescan.tif` have no checksum, so `verify`
cannot see damage to them. Tools locate entries by `record["id"]`, so a copied or renamed
entry breaks them.

## Fix plan

1. `library.save`: write into `library/.tmp-<uuid>/`, fsync, then `os.rename` to the final
   id (atomic on one filesystem; retry with a suffix on `FileExistsError`). This also fixes
   the id race.
2. Checksums for every file in the entry (`files: {name: sha256}`), verified by `verify`.
3. `verify` reports orphan directories (no `scan.json`) and `.tmp-*` leftovers.
4. In-place migrations: write the new file beside, then replace, `scan.json` last.
5. Locate entries by directory, not by `record["id"]`.

## Evidence (from [LIB-10](../areas/library.md#library-lib-10))

**Where:** `rps7200/library.py:165-176`, `rps7200/library.py:178-211`, `rps7200/library.py:308-310`, `rps7200/library.py:741-750`, `rps7200/library.py:776-786`, `rps7200/library.py:633-635`, `tools/library.py:194-206`, `tools/uniformity.py:643-646`, `rps7200/library.py:770-772`

```text
`if (path / "scan.json").exists(): ... while (path / "scan.json").exists(): path = base.with_name(f"{base.name}-{n}")` / `path.mkdir(parents=True, exist_ok=True)`. Then scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz are written straight to their final names, and `(path / "scan.json").write_text(...)` comes last. entries(): `for candidate in sorted(root.glob("*/scan.json")): try: ... except (OSError, json.JSONDecodeError): continue`. The migrations and uniformity's REJECTED also rewrite scan.json in place.
```

**Failure scenario:** The disk fills during frame 30 of a 3600 dpi roll. The entry directory is left with a 60 MB half raw.bin.gz and no scan.json. `make verify` says 'library is intact' and the orphan occupies space for ever.

**Second reader's check:** library.save (165-230) mkdirs with exist_ok=True and writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz directly under their final names. scan.json comes last, written with write_text and no temp file or rename. entries() (741-750) globs */scan.json and silently skips files that fail to parse. verify therefore never sees an orphan directory or a truncated scan.json. The collision loop checks only scan.json, so a later save can reuse a crashed directory and inherit its stale files. reconstruct and read_raw open raw.bin.gz because it exists, not because the record names it. Concurrent same-id writers within one process are unlikely (FrameWriter is a single thread) but possible across processes. index.json is rewritten non-atomically from both FrameWriter and the GUI thread (gui.py:4012).

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [LIB-10](../areas/library.md#library-lib-10) | library | medium | confirmed | Entries are written non-atomically; a crash leaves an orphan directory that verify and reconstruct never see, and the collision check can reuse it | rps7200/library.py:165-176, rps7200/library.py:178-211, rps7200/library.py:308-310 |
| [SR-22](../areas/session-roll.md#session-roll-sr-22) | session-roll | medium | confirmed | Partially written library entries are invisible to entries()/verify(); a reindex failure reports a complete entry as unfiled | rps7200/library.py:176-211, rps7200/library.py:308-311, rps7200/library.py:741-750 |
| [CLI-18](../areas/cli-operator-tools.md#cli-operator-tools-cli-18) | cli-operator-tools | medium | confirmed | `library.py verify` cannot see half-written entries or prescan.tif; says 'library is intact' after an interrupted save | rps7200/library.py:741-750, rps7200/library.py:776-816, rps7200/library.py:163-309 |
| [T08](../areas/tests.md#tests-t08) | tests | medium | confirmed | library.save is not atomic, verify cannot see partial entries, and prescan/shading/mask have no checksums; none of it is tested | rps7200/library.py:166-311, rps7200/library.py:741-750, rps7200/library.py:776-816 |
| [RX-8](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-8) | resource-exhaustion-crash-recovery-time | medium | confirmed | Every writer except settings writes its final path in place with no temp+rename and no fsync; partial files survive under ordinary names and some make whole rolls unreadable | rps7200/tiff.py:164, rps7200/tiff.py:167-168, rps7200/dng.py:145-146 |
| [RX-9](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-9) | resource-exhaustion-crash-recovery-time | medium | confirmed | In-place rewrites of complete library entries (migrate-raw/migrate-direction --write, uniformity 'redo') are not atomic; an interruption makes an entry vanish or destroys the only prescan.tif | tools/library.py:193-207, rps7200/library.py:563-577, rps7200/library.py:618-623 |
| [CC-01](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-01) | cross-process-and-thread-concurrency | medium | confirmed | library.save reserves an entry id with a non-atomic scan.json check; two concurrent savers in the same second write into ONE directory | rps7200/library.py:118-128, rps7200/library.py:166-176, rps7200/library.py:179-211 |
| [OUT-09](../areas/outputs.md#outputs-out-09) | outputs | medium | confirmed | Non-atomic tiff.write is used to rewrite library files in place; prescan.tif has no checksum | rps7200/tiff.py:164, rps7200/tiff.py:167-168, rps7200/dng.py:145-146 |
| [RXV-3](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rxv-3) | resource-exhaustion-crash-recovery-time | medium | found-by-verifier | verify cannot detect damage to shading.npz, ccd_mask.bin or prescan.tif: none has a checksum, and prescan.tif is not checked at all | rps7200/library.py:219-296, rps7200/library.py:776-816 |
| [CC-08](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-08) | cross-process-and-thread-concurrency | medium | confirmed | tools/library.py mutators run uncoordinated with a running GUI; migrate-raw --write rewrites scan.tif before scan.json, so a concurrent read or a kill leaves raw pixels labelled 'shading already applied' | tools/library.py:115-135, tools/library.py:193-207, rps7200/library.py:373-377 |
| [LIB-16](../areas/library.md#library-lib-16) | library | medium | confirmed | Tools locate entries by record['id'], not by their directory; copied or renamed entries break verify/reconstruct/migrate-raw/duplicates | rps7200/library.py:28-30, rps7200/library.py:741-750, rps7200/library.py:780-781 |
