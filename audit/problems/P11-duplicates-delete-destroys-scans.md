# P11 -- `library.py duplicates --delete` destroys scans of different photographs

**Severity** critical · **Group** A: Library exactness -- the central requirement · **Reported independently by** 5 findings in 5 areas

[Back to the summary](../README.md)

## The problem

`library.signature` defines "the same scan of the same picture" by film notes (stock,
frame, subject), dpi, channels, frame window, depth, film, protocol revision, commanded
exposure and fast-IR. It does **not** include capture time, transport position or
anything about the picture. Entries from the window with empty film notes, or from
same-day rolls under the default name, therefore share one signature although they are
different photographs, and `--delete` `rmtree`s all but one -- with no confirmation.
Gain and byte-14 are not in the signature either, so the byte-14 and gain ladders that
TODO.md says must never be pruned collapse to one entry. Debug-filed probe ladders,
repeat pairs and walk corpora are all at risk.

## Fix plan

1. Disable `--delete` until the signature can prove sameness: require identical raw-byte
   hashes (true duplicates) or an explicit `--i-know` plus a printed list and a typed
   confirmation.
2. Add capture time / transport position / gain / byte14 to the signature, and exclude any
   entry tagged `debug`, `probe`, `ladder` or `evidence`.
3. Move deletions to `library/.trash/` rather than `rmtree`.

## Evidence (from [PA-01](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-01))

**Where:** `rps7200/library.py:686-698`, `rps7200/library.py:714-738`, `tools/library.py:115-129`, `rps7200/direct.py:719-724`, `tools/byte14_probe.py:142-151`, `tools/gain_probe.py:158-183`, `tools/fast_ir_probe.py:97`, `tools/exposure_probe.py:106`, `tools/roll_registration_walk.py:107-110`, `tools/hold_probe.py:136-156`, `tools/transport_truth.py:125-147`

```text
signature() = (film.stock, film.frame, film.subject, resolution_dpi, channels, frame, depth, film, protocol_revision, commanded, fast_infrared) (library.py:686-698). Debug filing hard-codes the film notes and tags for every probe pass: `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]` (direct.py:722-723), so stock, frame and subject are all empty. byte14 is not a recorded field. gain is in device_settings, and signature() ignores it. tools/library.py:127-129: `if args.delete: shutil.rmtree(path)` for every entry `prunable()` returns.
```

**Failure scenario:** An operator runs `uv run python tools/library.py duplicates --delete` to free space after a registration walk and a byte14 ladder. prunable() keeps one entry per group and deletes the rest with shutil.rmtree. That removes about 95 of 96 walk prescans, 12 of 13 byte14 passes, 5 of 6 gain rungs and one pass from every repeat pair, with no way back. The dry-run output even labels them 'same scan of the same picture'.

**Second reader's check:** I checked this in the code. signature() (rps7200/library.py:676-698) keys on film stock, frame and subject, dpi, channels, frame, depth, film, protocol_revision, commanded exposure and fast_infrared. _debug_flush always files with `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]` (direct.py:719-724), so stock, frame and subject are empty for every probe pass. scan()'s meta has no byte14 (direct.py:2760-2804), and gain lives only in device_settings, which signature() ignores. That collapses three groups. First, the byte14 ladder (auto_exposure=False, the same commanded scales and FULL_FRAME) is one signature. Second, the gain ladder is one signature. Third, every prescan is one signature: prescan() calls scan() with auto_exposure=False and exposure_scale=1.0, so commanded=(1.0,), and fast_infrared defaults to True. The third group covers the walk p1/p2 pairs, the ladder rungs, and the prescans from hold_probe and transport_truth. tools/library.py:115-129 runs shutil.rmtree on every entry prunable() returns and labels each 'same scan of the same picture'. The signature docstring even argues that a bracket and a fast-IR ladder must not collapse, and still leaves byte14 and gain out.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [PA-01](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-01) | probe-and-analysis-tools | critical | confirmed | `library.py duplicates --delete` would destroy probe ladders, repeat pairs and whole walk corpora filed by debug mode | rps7200/library.py:686-698, rps7200/library.py:714-738, tools/library.py:115-129 |
| [LIB-07](../areas/library.md#library-lib-07) | library | high | confirmed | duplicates --delete treats different photographs (empty notes) and deliberate ladders as interchangeable | rps7200/library.py:639-698, rps7200/library.py:701-738, tools/library.py:115-135 |
| [SR-A2](../areas/session-roll.md#session-roll-sr-a2) | session-roll | high | found-by-verifier | Unlabelled single scans and prescans from the window share one library signature; `duplicates --delete` destroys distinct pictures | rps7200/session.py:1393-1427, rps7200/session.py:1440-1458, rps7200/library.py:679-698 |
| [CLI-06](../areas/cli-operator-tools.md#cli-operator-tools-cli-06) | cli-operator-tools | high | confirmed | `library.py duplicates --delete` treats scans of different pictures as duplicates when film notes are empty or same-day default roll names collide | rps7200/library.py:639-699, rps7200/library.py:714-738, tools/library.py:115-135 |
| [DOC-02](../areas/docs-plans-todo.md#docs-plans-todo-doc-02) | docs-plans-todo | high | confirmed | library.signature ignores gain and byte14, so `duplicates --delete` would destroy the byte-14 and gain ladders that TODO says must not be pruned | rps7200/library.py:639-698, rps7200/library.py:714-738, tools/library.py:115-135 |
