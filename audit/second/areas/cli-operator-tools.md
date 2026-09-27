# Operator CLI tools and build

Area key `cli-operator-tools`. 32 findings: 3 high, 13 medium, 14 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

The operator CLI tools (tools/scan.py, tools/scan_roll.py, tools/library.py, tools/make_comparison.py, tools/filing_load_test.py) and the build/dispatch layer (tasks.py, Makefile, pyproject.toml, packaging/60-rps7200.rules, .github/workflows/test.yml), read in full at HEAD 3995e9a. I followed calls into DirectScanner (scan, scan_bracket, scan_roll, ensure_shading, debug spool/claim/flush, read_planes), session (FrameWriter, RollManifest, seek/rewind, estimate_seconds), library (save, reconstruct, verify, prunable) and console.DeferredInterrupt.


What works: validation before the device opens is broad, and each pass files its raw bytes, raw decode, reference and CCD mask by default. The debug spool/claim design is sound for passes nobody files. make_comparison is faithful to the delivered path. tasks.py is portable.


The main weaknesses are in stop, kill and failure handling, and in what the operator is told:
- **Ctrl-C in scan.py:** it is ignored through calibration and metering, and ignored for a whole --no-library bracket. The operator is invited to press it a second time, which wedges the scanner.
- **Kill signals:** nothing handles SIGTERM or SIGHUP. A comment claims it is handled.
- **Time estimates and the harness kill:** scan_roll.py prints no estimate at all. scan.py's estimate sits below the library medians and ignores exposure.
- **Silent filing failures in a roll:** a roll whose filing fails keeps scanning for hours and reports only at the end.
- **reconstruct false negative:** `library.py reconstruct` reports a decoder that now throws as "not a regression" and exits 0.
- **Library contents:** frame entries hold a corrected prescan.tif with no raw bytes.
- **Resume:** a CLI resume re-scans every frame already done and ignores the earlier run's settings.
- **Defaults differ between tools:** metering (scan.py has none), `--library ''` (skip in one tool, cwd in the other), and mono for B&W.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [CLI-01](#cli-operator-tools-cli-01) | high | error-handling | confirmed | A roll whose library filing fails keeps scanning for hours and says so only at the end; FrameWriter then writes no delivered copy either |
| [CLI-02](#cli-operator-tools-cli-02) | high | hardware-safety | confirmed | scan.py: Ctrl-C is ignored through calibration and metering, and for the whole of a --no-library bracket |
| [CLI-05](#cli-operator-tools-cli-05) | high | bug | confirmed | `library.py reconstruct` calls a decoder that now throws, an unreadable scan.tif and corrupt raw bytes "not a regression", and exits 0 |
| [CLI-03](#cli-operator-tools-cli-03) | medium | hardware-safety | confirmed | scan_roll.py prints no duration estimate or background warning, though almost every roll and full-strip walk passes the 10-minute harness kill |
| [CLI-04](#cli-operator-tools-cli-04) | medium | doc-mismatch | partly | No SIGTERM/SIGHUP/SIGBREAK handling anywhere, though a comment says a SIGTERM becomes SystemExit and is handled |
| [CLI-06](#cli-operator-tools-cli-06) | medium | error-handling | confirmed | scan.py files passes after the device closes in a bare loop: one failed library.save loses every later pass, the bracket merge and the delivered file |
| [CLI-07](#cli-operator-tools-cli-07) | medium | data-integrity | confirmed | Tools claim a pass from debug filing before they have filed it, so with RPS7200_DEBUG=1 a failed save still loses the pass |
| [CLI-08](#cli-operator-tools-cli-08) | medium | doc-mismatch | confirmed | scan.py's estimate uses a fit below the library medians and ignores exposure, warm-up and filing, so it under-warns about the 10-minute kill |
| [CLI-09](#cli-operator-tools-cli-09) | medium | data-integrity | confirmed | Frame entries from a real roll store a corrected prescan.tif, with no raw bytes, no CCD mask and no label |
| [CLI-10](#cli-operator-tools-cli-10) | medium | doc-mismatch | confirmed | A CLI resume re-scans every frame from --start-at to the end: 'done' is never consulted, there is no --only, and the advice omits --frames 1 |
| [CLI-11](#cli-operator-tools-cli-11) | medium | data-integrity | partly | Resuming a roll ignores the earlier run's settings: another --dpi/--ir/--film/--meter is mixed in silently and the recorded settings are overwritten |
| [CLI-12](#cli-operator-tools-cli-12) | medium | user-error | confirmed | --approved proposes positions on the walk's film but scans and meters on --film (default negative), with no warning |
| [CLI-13](#cli-operator-tools-cli-13) | medium | design | confirmed | scan.py does not meter by default, unlike scan_roll.py and the window, so the README's headline command scans at the device's low default exposure |
| [CLI-14](#cli-operator-tools-cli-14) | medium | user-error | confirmed | A low-contrast frame ends the roll silently with exit 0, even when --frames asked for more |
| [CLI-15](#cli-operator-tools-cli-15) | medium | hardware-safety | confirmed | filing_load_test.py drives the scanner with no DeferredInterrupt and no estimate; Ctrl-C mid-pass wedges it, and --rounds 0 crashes after warm-up |
| [CLI-A1](#cli-operator-tools-cli-a1) | medium | data-integrity | found-by-verifier | Ctrl-C during the post-close filing is no longer deferred, and loses the passes or frames still held only in memory |
| [CLI-16](#cli-operator-tools-cli-16) | low | user-error | confirmed | scan_roll: a Ctrl-C before the first frame still calibrates, and on a dry run replaces the folder's survey.json with an empty walk |
| [CLI-17](#cli-operator-tools-cli-17) | low | user-error | confirmed | `--library ''` means skip in scan_roll.py but files into the current directory in scan.py |
| [CLI-18](#cli-operator-tools-cli-18) | low | data-integrity | confirmed | Calibration raw bytes live outside the library behind a cwd-relative pointer, and --reuse drops the link to them |
| [CLI-19](#cli-operator-tools-cli-19) | low | user-error | confirmed | --reuse accepts a cached reference of any age without saying how old it is; --reference into a library entry without --reuse overwrites that entry's reference |
| [CLI-20](#cli-operator-tools-cli-20) | low | hardware-safety | confirmed | --dpi and --prescan-dpi reach MODE SELECT unvalidated beyond positivity and correctability |
| [CLI-21](#cli-operator-tools-cli-21) | low | design | confirmed | A bracket holds every pass's pixels and raw bytes in RAM with no memory estimate; at 7200 dpi --no-shading that is about 10 GB |
| [CLI-22](#cli-operator-tools-cli-22) | low | design | confirmed | scan_roll.py has no mono delivery: B&W roll frames are three-channel, where scan.py and the window deliver one |
| [CLI-23](#cli-operator-tools-cli-23) | low | hardware-safety | confirmed | Dry-run prescans are deflate-compressed on the scanning thread with the device open, which the window deliberately avoids |
| [CLI-24](#cli-operator-tools-cli-24) | low | doc-mismatch | confirmed | scan_roll.py still speaks millimetres for transport distances, and a sizing comment is stale |
| [CLI-25](#cli-operator-tools-cli-25) | low | data-integrity | confirmed | `duplicates --delete` removes entries with rmtree on the records' word, without checking the survivor on disk |
| [CLI-26](#cli-operator-tools-cli-26) | low | data-integrity | confirmed | migrate-raw rewrites labelled entries without checking the new decode against the stored pixels |
| [CLI-27](#cli-operator-tools-cli-27) | low | user-error | confirmed | Default output locations are relative to the current directory while imports are relative to the repo |
| [CLI-28](#cli-operator-tools-cli-28) | low | doc-mismatch | confirmed | scan.py's docstring says the scanner applies its own shading correction |
| [CLI-A2](#cli-operator-tools-cli-a2) | low | library-completeness | found-by-verifier | Debug filing ignores --library: the probes, holds and aim prescans of a run go to ./library or RPS7200_DEBUG_ROOT, apart from the run's own entries |
| [CLI-29](#cli-operator-tools-cli-29) | info | library-completeness | confirmed | No tool compacts plain entries left by a window that was killed before compacting |
| [CLI-A3](#cli-operator-tools-cli-a3) | info | doc-mismatch | found-by-verifier | scan_roll.py's comment says writer.finish() is reached through a finally, but early returns inside the try skip it |

## Findings in full

<a id="cli-operator-tools-cli-01"></a>

### CLI-01 -- A roll whose library filing fails keeps scanning for hours and says so only at the end; FrameWriter then writes no delivered copy either

**Severity** high · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/scan_roll.py:729-776`, `tools/scan_roll.py:803-811`, `rps7200/session.py:1587-1600`, `rps7200/session.py:1637-1668`, `rps7200/session.py:1126-1159`

When library.save raises on the writer thread, the frame is lost: the raw bytes, the raw pixels and the delivered frameNN.tif. Typical causes are a full disk, `--library` pointing somewhere unwritable or at a file, or a NAS that went away. The scanning thread never checks writer.errors. It goes on printing `picture N: rolls/.../frameNN.tif (shape) in Xs` for every frame, as if each had been written. Each frame costs 2-7 minutes of scanner time and every one is discarded. The window reports each filing as it lands (ScanSession._filed); the CLI does not. Each failed save also leaves an INCOMPLETE directory holding partial multi-hundred-MB files, which speeds up a disk-full cascade. Because library.save runs before the copies, a library failure also suppresses frameNN.tif even when the roll folder is on a healthy disk.

**Evidence (from the code):**

```text
scan_roll.py only looks at writer failures after the device is closed: `writer.finish()` ... `for problem in writer.errors: failed += 1; print(f"could not file {problem}", file=sys.stderr)` (803-811). FrameWriter._run swallows per frame: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")` (1594-1595). _write files first and raises before any copy is attempted: `entry = library.save(... **job["capture"])` (1656-1668) precedes `for path in job.get("paths") or ():` (1673). RollManifest.filed on error only sets `record["filing_error"]` (1158-1159), with no print. `writer.notes` is never printed by scan_roll.py.
```

**Failure scenario:** `scan_roll.py --dpi 3600 --ir --library /mnt/nas/library` starts a 38-frame roll. The NAS drops (or the disk fills) at frame 6. Frames 6-38 are scanned (about 2 h), all fail to file, no frameNN.tif is written, and the terminal shows a success line for each. The errors appear in a burst after two hours; the scanner time and the pictures are gone.

**Fix:** Poll `writer.errors` (or an on_done callback) after each yield. Print each failure immediately and stop the roll after N consecutive filing failures, as max_failures does for scan failures. In FrameWriter._write, catch the library.save failure, still attempt the delivered copies, and then report. Check the library and --out paths are writable, with some headroom, before the device opens.

<details><summary>Second reader's check</summary>

Confirmed in code. scan_roll.py reads writer.errors only after writer.finish() (803-811). Its per-frame success line (778-779) is printed right after writer.submit, before anything has been filed. FrameWriter._run catches every exception into self.errors (session.py:1593-1598). In _write, library.save (1656-1668) runs before the copy loop (1673), so a raising save also suppresses frameNN.tif. RollManifest._apply only sets filing_error (1158-1159) and prints nothing, and scan_roll.py never prints writer.notes. library.save creates the entry directory and the INCOMPLETE marker (258-263) and never removes the partial files when it fails. No mid-roll check exists and max_failures counts scan failures only (direct.py:4163).

</details>

<a id="cli-operator-tools-cli-02"></a>

### CLI-02 -- scan.py: Ctrl-C is ignored through calibration and metering, and for the whole of a --no-library bracket

**Severity** high · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/scan.py:270-274`, `tools/scan.py:280-306`, `tools/scan.py:317-360`, `rps7200/console.py:75-81`

**Doc claim:** CLAUDE.md:421-422: "Ctrl-C in `tools/scan.py` and `tools/scan_roll.py` finishes the pass in flight and stops there; a second one aborts."

CLAUDE.md promises that Ctrl-C in tools/scan.py finishes the pass in flight and stops there. What happens instead depends on when it is pressed:
- **During the 3-4 minute calibration:** the calibration finishes, and the tool then meters (up to three probes, if asked) and takes the full scan anyway. That can be 5+ minutes at 7200 dpi RGBI.
- **During a bracket's metering:** metering continues and the first bracket pass is taken.
- **During a bracket run with --no-library:** hold() returns before the check, so every remaining pass is taken (up to 9 x 138 s at 3600 dpi).

In every case the operator has been told "stopping" and then sees a new pass start. The handler's own text invites the second Ctrl-C, which raises KeyboardInterrupt inside read_planes, marks the device suspect and abandons the read: the wedge the handler exists to prevent.

The exit status also differs: 130 for a stopped bracket, 0 for a single pass that ran on, and 0 for a roll stopped by Ctrl-C (scan_roll.py).

**Evidence (from the code):**

```text
The handler announces: `"\nstopping after the pass in flight -- interrupting a read wedges the scanner. Press Ctrl-C again to abort anyway."` (console.py:80-81). scan.py consults the flag in one place only, inside hold(), and that place is unreachable without a library: `def hold(image, meta, capture) -> None: if args.library is None: return` (281-282) ... `if args.bracket and interrupt.requested(): raise _StoppedBetweenPasses(...)` (301-306). Nothing checks `interrupt.requested()` between `s.ensure_shading(...)` (273) and `s.scan(...)` / `s.scan_bracket(...)`.
```

**Failure scenario:** The operator starts `scan.py --dpi 1800 --ir`, realises during calibration that the wrong strip is loaded, and presses Ctrl-C. The calibration ends and a 110 s RGBI pass starts. The operator presses Ctrl-C again; the read is abandoned and the scanner needs a power cycle.

**Fix:** Check `interrupt.requested()` after ensure_shading, before metering and before every pass (single and bracket), whatever the library setting. Move the bracket stop check out of hold() into on_pass, ahead of the `args.library is None` return. Give single-pass and roll stops a consistent exit code. Add tests driving `interrupt._requested=True` at each phase.

<details><summary>Second reader's check</summary>

Confirmed. interrupt.requested() is consulted only inside hold(), at scan.py:301, and hold() returns at 281-282 when args.library is None. Nothing checks it between ensure_shading (273) and s.scan/s.scan_bracket (330/350). scan() and scan_bracket() take no should_stop, and metering runs inside them (direct.py:2793-2794 and 2965). The handler text (console.py:80-81) promises a stop and invites a second Ctrl-C, which raises KeyboardInterrupt inside a read; the BaseException handlers at direct.py:2337 and 3280 then mark the device suspect. Exit codes differ as described: a stopped bracket returns 130 (380), a single pass that ran on returns 0, and a roll stopped by Ctrl-C returns 0 unless something failed (scan_roll.py:846). This contradicts CLAUDE.md:421-422. The --no-library bracket case can ignore the first Ctrl-C for up to 9 passes, which makes the second, wedging Ctrl-C likely.

</details>

<a id="cli-operator-tools-cli-05"></a>

### CLI-05 -- `library.py reconstruct` calls a decoder that now throws, an unreadable scan.tif and corrupt raw bytes "not a regression", and exits 0

**Severity** high · **Category** bug · **Verdict** confirmed

**Where:** `tools/library.py:145-172`, `rps7200/library.py:696-720`, `rps7200/library.py:746-749`, `rps7200/library.py:604-624`

This is the one check CLAUDE.md names for any change to how bytes become pixels, and it guards the owner's central requirement. Its classifier merges three different things with "nothing stored" and excludes them from the exit status:
- a decode change that makes `decode_index` raise (a misread channel tag, or a channel-count mismatch);
- a tiff reader regression that can no longer open stored files;
- raw bytes that were stored and are now corrupt.

A change that breaks decoding for every entry prints "every entry that can be decoded still decodes to exactly what was stored" plus "N had nothing to decode from -- not a regression", and `make reconstruct` exits 0.

**Evidence (from the code):**

```text
`elif ("no raw bytes" in verdict or verdict.startswith("could not") or "cannot be reproduced" in verdict): mark, unreadable = "-", unreadable + 1` ... `print(f"{unreadable} had nothing to decode from -- not a regression, but they cannot be re-corrected either")` ... `return 1 if changed else 0`. library.reconstruct returns `"could not decode: {exc}"` for `(KeyError, ValueError, TypeError, ScanReadError)` raised by decode_index, `"could not read scan.tif: {exc}"` for a TIFF read failure, and `"no raw bytes stored for this entry"` when read_raw returns None because the gzip is truncated or corrupt (read_raw catches `OSError, EOFError, gzip.BadGzipFile`).
```

**Failure scenario:** A refactor changes INDEX_HEADER handling, so decode_index raises ScanReadError("no recognisable channel tags") on every stored pass. `make reconstruct` prints '-' for all 300 entries, the summary says nothing changed, it exits 0, and the change is merged.

**Fix:** Count only a record with no raw file as "nothing stored". Report decode exceptions and scan.tif read failures as regressions (a non-zero exit). Report a raw file that exists but does not read as damage, pointing at verify. Add a test with a deliberately broken decoder.

<details><summary>Second reader's check</summary>

Confirmed. tools/library.py:147-149 files every verdict starting with 'could not' as unreadable ('-'): that covers decode_index raising ('could not decode', library.py:718-720), scan.tif failing to read (746-749) and scan.json failing to parse. It also files 'no raw bytes', which read_raw returns for a raw file that exists but is corrupt or truncated (604-624). The summary then calls these 'not a regression' (164-166) and the exit status is `1 if changed else 0` (170). A decoder change that raises ScanReadError on every entry therefore passes `make reconstruct` with exit 0.

</details>

<a id="cli-operator-tools-cli-03"></a>

### CLI-03 -- scan_roll.py prints no duration estimate or background warning, though almost every roll and full-strip walk passes the 10-minute harness kill

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/scan_roll.py:28-32`, `tools/scan_roll.py:149-151`, `tools/scan_roll.py:545-568`, `tools/scan.py:44-64`

**Doc claim:** CLAUDE.md:355-356 ("Want a roll walked? `tools/scan_roll.py --dry-run` walks one."); CLAUDE.md:389-393; tools/scan_roll.py:149-151 ("Walks a 6-frame strip in about 2.5 minutes")

CLAUDE.md sends Claude to `tools/scan_roll.py --dry-run` to walk a roll, and says any sequence over ~8 minutes must be backgrounded because the harness kills foreground commands at 10 minutes and a killed read wedges the scanner.

scan.py warns about this; scan_roll.py, the tool most likely to cross the line, says nothing. Its own help understates a 6-frame walk by the calibration: about 5.5 minutes, not 2.5. Typical costs:
- a full-strip walk (no --frames) of a 20-38 frame strip: 210 s + N x ~21 s, well past 10 minutes;
- any real roll: minutes per frame.

The per-frame figures exist in `session.FORWARD_FRAME_S` and `estimate_seconds`.

**Evidence (from the code):**

```text
scan.py has `FOREGROUND_S = 8 * 60.0` and say_estimate(); scan_roll.py has no estimate at all (grep: no `estimate`/`FOREGROUND` in the file). The --dry-run help still says `"prescan and advance only -- no full scans. Walks a 6-frame strip in about 2.5 minutes"` (149-151), and the docstring `"it walks the whole strip in a couple of minutes"` (29). Yet a dry run now always calibrates first: `# On a dry run too. A dry run still prescans ...` then `calibrate(s, args)` (555-568), which costs 3-4 minutes.
```

**Failure scenario:** Following CLAUDE.md, Claude runs `RPS7200_DEBUG=1 uv run python tools/scan_roll.py --dry-run` in the foreground on a 30-frame strip. The harness kills it at 10:00 in the middle of a prescan read. The read is abandoned, the scanner wedges, and the walk's queued prescans die with the process.

**Fix:** Give scan_roll.py the same pre-open estimate as scan.py: calibration, plus per frame a prescan, an advance, metering by --meter, and estimate_seconds for the scan. When --frames is absent, assume LAST_PLAUSIBLE_POSITION+1 minus start. Print the background warning past FOREGROUND_S, and fix the stale --dry-run help and docstring.

<details><summary>Second reader's check</summary>

Confirmed. tools/scan_roll.py has no estimate, no FOREGROUND threshold and no background warning (a grep finds none). The --dry-run help (149-151) and the docstring (28-30) promise 2.5 minutes or 'a couple of minutes', yet main() always calibrates first (568) unless --reuse or --no-shading is given, and a calibration is 3-4 minutes. A walk with no --frames runs until a blank frame or the end of the strip, which easily passes 10 minutes. I rate it medium rather than high: CLAUDE.md gives the per-pass figures and the backgrounding rule itself, and a 6-frame walk (about 5.5 minutes) is still under the kill. But the tool CLAUDE.md names for walking a roll understates its own cost and never warns.

</details>

<a id="cli-operator-tools-cli-04"></a>

### CLI-04 -- No SIGTERM/SIGHUP/SIGBREAK handling anywhere, though a comment says a SIGTERM becomes SystemExit and is handled

**Severity** medium · **Category** doc-mismatch · **Verdict** partly

**Where:** `tools/scan_roll.py:783-791`, `rps7200/console.py:89-99`

No handler exists for SIGTERM, SIGHUP or (on Windows) SIGBREAK/console close; only SIGINT is deferred. The comment at scan_roll.py:786-788 claims a SIGTERM becomes SystemExit and is caught, which is false. A kill, a closed terminal or an SSH drop mid-read therefore abandons the read (a wedge) and drops the frames queued in FrameWriter or scan.py's pending list. A deferring handler for these signals, like the first Ctrl-C, would cover the catchable cases.

**Evidence (from the code):**

```text
scan_roll.py:786-788: `BaseException, because a second Ctrl-C (KeyboardInterrupt) and a SIGTERM-turned-SystemExit are exactly the exits that used to skip writer.finish() and lose queued frames.` A repo-wide grep finds no `SIGTERM`/`SIGHUP`/`SIGBREAK` handler; DeferredInterrupt installs only `signal.signal(signal.SIGINT, self._handler)` (console.py:95). The writer thread is `threading.Thread(target=self._run, daemon=True)` (session.py:1584).
```

**Failure scenario:** A two-hour roll is run over SSH and the connection drops at frame 20. SIGHUP kills the process during frame 21's read. The scanner wedges; frames 19-20, still queued or gzipping on the daemon writer, are lost (one leaves an INCOMPLETE directory); roll.json records them as not done.

**Fix:** Install SIGTERM/SIGHUP handlers (and SIGBREAK on Windows) in DeferredInterrupt that behave like the first Ctrl-C: finish the pass, then stop. Make FrameWriter's thread non-daemon, or have finish() run from an atexit hook. Correct the comment until then.

<details><summary>Second reader's check</summary>

There is no SIGTERM, SIGHUP or SIGBREAK handler anywhere outside .venv, and DeferredInterrupt installs SIGINT only (console.py:95). The comment at scan_roll.py:786-788 about a 'SIGTERM-turned-SystemExit' is therefore false: under Python's default, SIGTERM and SIGHUP end the process without unwinding, so neither the except BaseException nor writer.finish() runs. The daemon=True point is moot under a default SIGTERM, which kills every thread whatever its daemon flag. The real consequences are that any termination signal mid-pass abandons the read, and that the queued frames, the in-memory pending passes and the final manifest update die with the process. A handler could protect only SIGTERM, SIGHUP and SIGBREAK, not SIGKILL.

</details>

<a id="cli-operator-tools-cli-06"></a>

### CLI-06 -- scan.py files passes after the device closes in a bare loop: one failed library.save loses every later pass, the bracket merge and the delivered file

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/scan.py:366-375`, `tools/scan.py:377-381`, `tools/scan.py:402-416`

All raw bytes and raw pixels of every pass are held only in memory (`pending`) until this loop. If library.save raises on the first pass, the exception escapes main() as a traceback. That happens with a full disk, `--library` naming a file or read-only directory, or a Windows sharing violation. Every remaining pass is never attempted, and the merge and --out are never written, so the whole bracket's scanner time (up to 9 passes) is lost. Nothing is checked about the library path before the device opens. The window's FrameWriter isolates failures per frame; scan.py does not.

**Evidence (from the code):**

```text
`entries = []\nfor held in pending:\n    entries.append(library.save(held.pop("image"), held.pop("meta"), root=args.library, ...))` has no try/except. It runs outside the `try` that collected `trouble`, and before `export.write(out, delivered, ...)`.
```

**Failure scenario:** `scan.py --bracket 5 --dpi 3600 --library D:/lib` where D: is a full USB drive. Five passes (~12 minutes) run, then library.save for pass 1 raises OSError(ENOSPC). The traceback ends the process, passes 2-5 and the merge are never written, and nothing remains.

**Fix:** Wrap each save, keep going, and report per pass. On failure, fall back to writing that pass's raw bytes and npy pixels to a recovery folder, or keep the debug spool (see CLI-07). Probe the library root for writability and free space before opening the device.

<details><summary>Second reader's check</summary>

Confirmed. scan.py:366-375 is a bare loop over library.save, outside the try that collects trouble, and it runs before export.write (413). A raise on the first pass loses every later pass, the merge and --out. The raw bytes and pixels exist only in `pending` (297-300). With debug on, their spools were already deleted as claimed (see CLI-07). Nothing checks the library root before the device opens.

</details>

<a id="cli-operator-tools-cli-07"></a>

### CLI-07 -- Tools claim a pass from debug filing before they have filed it, so with RPS7200_DEBUG=1 a failed save still loses the pass

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/scan.py:294-296`, `tools/scan_roll.py:675`, `tools/scan_roll.py:729-730`, `rps7200/direct.py:1000-1016`, `rps7200/direct.py:1055-1059`, `rps7200/direct.py:1134-1145`

With debug on, the spool is a complete on-disk copy of each pass. _debug_flush was written so that a failed filing keeps it ("A spool unlinked after a failed save was the only copy of that pass"). But a claimed pass's spool is deleted at close(), which happens before scan.py saves and while scan_roll's FrameWriter may still be filing, or failing to. The claim promises a filing that has not happened yet, so the one mode that exists to never lose a scan loses exactly the passes the tool meant to keep whenever the tool's own save fails (CLI-01, CLI-06).

**Evidence (from the code):**

```text
scan.py hold(): `raw = getattr(s, "last_pixels_raw", None)\nif raw is not None:\n    s.debug_claim(raw)`. This runs inside the session, but library.save runs after close(). scan_roll.py: `if args.library and frame.raw_image is not None: s.debug_claim(frame.raw_image)` just before `writer.submit(...)` to a background thread. _debug_flush, called from close(), does `if item.get("claimed"): ... stuck += self._debug_unlink(item); continue` and deletes the spooled image.npy, raw.bin and meta.json.
```

**Failure scenario:** `RPS7200_DEBUG=1 scan.py --library /readonly/lib` scans a frame. close() unlinks the claimed spool, library.save then raises PermissionError, and the pass exists nowhere, although debug filing was on.

**Fix:** Make the claim conditional on success. Either release claimed spools only after the claimant confirms filing (a `debug_filed(pixels)` call after library.save or on FrameWriter's on_filed), or have close() keep claimed spools and let the tool delete them after its own save succeeds.

<details><summary>Second reader's check</summary>

Confirmed. scan.py:294-296 calls debug_claim on the raw pixels inside the session, and library.save runs only after the with-block closes (366). scan_roll.py:675 and 729-730 claim just before writer.submit. close() calls _debug_flush (direct.py:1134-1145), which unlinks the image, raw, meta and mask files of any claimed item (1055-1059) without regard to whether the claimant's filing succeeded. That filing either has not happened yet (scan.py) or is still running on the writer thread (scan_roll). The protection for failed saves at 1106-1107 applies only to unclaimed passes.

</details>

<a id="cli-operator-tools-cli-08"></a>

### CLI-08 -- scan.py's estimate uses a fit below the library medians and ignores exposure, warm-up and filing, so it under-warns about the 10-minute kill

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan.py:39-64`, `rps7200/session.py:1275-1309`, `rps7200/direct.py:2717-2726`

**Doc claim:** CLAUDE.md:394-406 ("Estimate first, from the medians ... `tools/scan.py` prints its own estimate from these ... Budget above the median, not at it.")

CLAUDE.md says scan.py's estimate comes from the medians and tells the reader to "budget above the median" because time tracks sum(exposure). The code does neither:
- Every RGB estimate is below the median (by 45% at 900 dpi).
- The estimate is exposure-blind, while a bracket deliberately drives its top rung to the 16-bit exposure ceiling, the slowest pass possible.
- It ignores lamp warm-up (up to ~80 s from cold) and the post-close gzip/filing, which a kill also interrupts, losing the in-memory passes.

The 8-minute threshold then sits on an optimistic number.

**Evidence (from the code):**

```text
`print(f"estimated {seconds / 60:.1f} min (an estimate from the library's medians; dense frames run longer)")` but `estimate_seconds` is `rgb = 8.0 + 0.036 * lines` with `lines = resolution * 6888/7200`. That gives 18 s at 300 dpi, 39 s at 900, 70 s at 1800 and 256 s at 7200, against CLAUDE.md's medians of 22, 72, 85 and 314 s. bracket_ladder pins its top pass to the timer ceiling: `headroom = min((65535.0 / e for e in metered if e > 0), default=1.0) ... top = headroom`.
```

**Failure scenario:** `scan.py --dpi 3600 --bracket 2` estimates 2x132+210 = 474 s (7.9 min) and prints no warning. With the top rung at the exposure ceiling, a cold lamp and a dense frame, the run passes 600 s. The harness kills it mid-read and the scanner wedges.

**Fix:** Estimate from the per-dpi medians, or a fit that includes exposure. Scale bracket rungs by their ladder multipliers, add warm-up and filing time, apply a margin (e.g. the 90th percentile), and fix the printed wording.

<details><summary>Second reader's check</summary>

Confirmed by arithmetic. estimate_seconds is rgb = 8 + 0.036*lines with lines = dpi*6888/7200 (session.py:1266 and 1301-1303). That gives about 18, 39, 70, 132 and 256 s at 300, 900, 1800, 3600 and 7200 dpi, against the CLAUDE.md medians of 22, 72, 85, 138 and 314. The printed text still says 'an estimate from the library's medians' (scan.py:58-59). The estimate ignores exposure, and bracket_ladder pins the top rung to the 65535 ceiling (direct.py:2717-2724). It ignores warm-up and the post-close filing. `--dpi 3600 --bracket 2` estimates 474 s, under the 480 s threshold, so no warning is printed.

</details>

<a id="cli-operator-tools-cli-09"></a>

### CLI-09 -- Frame entries from a real roll store a corrected prescan.tif, with no raw bytes, no CCD mask and no label

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/scan_roll.py:752-755`, `rps7200/session.py:2627-2628`, `rps7200/library.py:269-270`, `rps7200/library.py:378-382`, `rps7200/direct.py:3968-3976`

**Doc claim:** CLAUDE.md:116 "The library holds raw pixels; everything else is corrected."

Inside the library, which by the owner's rule holds raw pixels, every roll frame entry carries a flat-fielded 8-bit prescan. It has no raw bytes, no layout, and not the prescan pass's own CCD mask (ccd_mask.bin is the frame pass's, at another resolution). Nothing in the record says it is corrected. The prescan is the evidence for registration, holds and read-direction judgments, and it can be neither re-decoded nor re-corrected. With RPS7200_DEBUG unset (the operator default), the framing prescan's raw bytes are gone. So are the hold/aim prescans, the --dry-run `prescanNN-before.tif` original, and every metering probe. Only corrected TIFFs remain in rolls/.

**Evidence (from the code):**

```text
scan_roll.py passes `prescan=frame.prescan` (752), and frame.prescan is the corrected image returned by `self.prescan(..., shading=shading)`. The raw pixels, `raw_prescan = self.last_pixels_raw`, are available but only used on --dry-run. library.save writes it as given: `tiff.write(str(path / "prescan.tif"), prescan, compress=compress)`, and records only `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}`. The window does the same (`prescan=rf.prescan`, session.py:2627).
```

**Failure scenario:** A later shading fix changes how 8-bit passes are corrected, and a registration study wants the prescans re-corrected. For every frame entry from scan_roll.py or the window, prescan.tif is already corrected with the old code and cannot be redone; `reconstruct` and `verify` do not notice.

**Fix:** File the prescan's raw pixels (frame.raw_prescan) with its own raw bytes and mask: as its own library entry (kind "prescan"), or as prescan_raw.bin.gz plus prescan_ccd_mask.bin in the frame entry. At minimum, record `prescan.corrections_applied: ["shading"]`. Consider making raw filing of probes and holds unconditional rather than gated on RPS7200_DEBUG.

<details><summary>Second reader's check</summary>

Confirmed. frame.prescan is the corrected return of self.prescan(..., shading=shading) (direct.py:3968-3975). scan_roll.py:752 passes it as prescan= and FrameWriter hands it to library.save, which writes it verbatim (library.py:269-270). The record's prescan block holds only file, read_direction and carriage_state (378-382), with no corrections label. frame.raw_prescan is filed only on --dry-run (666-685). For a real roll, the framing prescan's raw pixels and bytes, the -before prescan and the hold/aim prescans exist only if RPS7200_DEBUG is on, because the raw prescan is never claimed and so is debug-filed.

</details>

<a id="cli-operator-tools-cli-10"></a>

### CLI-10 -- A CLI resume re-scans every frame from --start-at to the end: 'done' is never consulted, there is no --only, and the advice omits --frames 1

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan_roll.py:20-26`, `tools/scan_roll.py:574-591`, `tools/scan_roll.py:605-633`, `tools/scan_roll.py:835-842`

**Doc claim:** README.md:652-657 ("A frame is recorded done only once the writer has filed it, so a frame lost to a full disk or a crash is scanned again rather than skipped."); tools/scan_roll.py:22-24 ("--start-at, with the roll's --roll or --out, resumes from the manifest")

Without --frames, `--start-at N` runs to the end of the strip. It re-scans (hours at 3600 dpi) every frame after N that the earlier run already finished, adds a new library entry for each (duplicates) and overwrites frameNN.tif. Several failed frames (3, 9, 15) cannot be retaken in one run. The window's resume offers only unfinished frames (`scanned_frames`); the CLI cannot. The docstring says --start-at "resumes from the manifest", and the README says a frame recorded done is not scanned again. Neither is true of this tool.

**Evidence (from the code):**

```text
The earlier manifest is read only to carry records forward: `manifest["frames"] = list(earlier.get("frames") or [])`. The call `s.scan_roll(frames=args.frames, ..., first_index=max(0, args.start_at - 1), ...)` passes no `only=`. The advice printed is `print(f"resume a failed picture with {again} --start-at N", file=sys.stderr)`.
```

**Failure scenario:** A 30-frame 3600 dpi RGBI roll fails frame 7 and reports failure. The operator follows the advice, `--roll X --start-at 7`, and the tool spends ~1.5 hours re-scanning frames 7-30, duplicating 23 entries in the library.

**Fix:** Add `--only`/`--retry-failed`, built from the earlier manifest's frames whose `done` is false, and pass it as `only=`. Change the advice to `--start-at N --frames 1`, or to the retry flag. Align the docstring and README.

<details><summary>Second reader's check</summary>

Confirmed. The earlier manifest supplies only manifest['frames'] (586). s.scan_roll is called without only= (605-633), and the direct.py scan_roll signature supports `only` (3777). Without --frames, finished() runs to the end of the strip. The advice printed at 841 omits --frames. The docstring's 'resumes from the manifest' (22-24) is not true of frame selection, since done=true frames are re-scanned and re-filed as new entries.

</details>

<a id="cli-operator-tools-cli-11"></a>

### CLI-11 -- Resuming a roll ignores the earlier run's settings: another --dpi/--ir/--film/--meter is mixed in silently and the recorded settings are overwritten

**Severity** medium · **Category** data-integrity · **Verdict** partly

**Where:** `tools/scan_roll.py:414-440`, `tools/scan_roll.py:574-591`

A resume through scan_roll.py (--roll/--out with --start-at) applies whatever flags were typed this time. Nothing compares them with the earlier run's recorded dpi, infrared, film or meter, so a roll can silently mix resolutions, channel sets and metering modes. The manifest's settings block is overwritten with the last run's values only, and fast_infrared, --no-shading, --reuse, --correct and --max-failures are never recorded, so the settings each frame was actually taken with cannot be recovered from roll.json.

**Evidence (from the code):**

```text
The manifest's `settings` is always this run's: `{"dpi": args.dpi, "infrared": args.ir, "meter": args.meter, "film": args.film, "dry_run": ..., "start_at": ..., "frames": ..., "prescan_resolution": ...}`. The earlier manifest contributes only `frames`, and nothing compares its settings. The window's manifest also records fast_infrared, correct, correct_dry_run, reverse_hold, max_failures, mono and rotation.
```

**Failure scenario:** Night 1: `--dpi 3600 --ir --film positive`. Night 2 resume: `--roll X --start-at 12`, with --dpi, --ir and --film forgotten. The rest is scanned at 1800 dpi RGB and metered as negative (orange-mask metering, which removes the slide's cast). The exposure is baked into the raw data, so it cannot be recovered.

**Fix:** On resume, read the earlier settings. Refuse (or require --force) when dpi, infrared, film, meter or shading differ, or default unspecified flags to the earlier values. Write the full settings block the window writes, including fast_infrared and shading.

<details><summary>Second reader's check</summary>

The core is confirmed. settings is always this run's (414-437). The earlier manifest contributes frames only, and nothing compares dpi, infrared, film, meter or shading between runs. fast_infrared, no_shading, reuse, correct and max_failures are not recorded. I could not verify the further claim that a window resume of a CLI roll turns a --no-shading roll into a corrected one: manifest_settings only merges keys, and I found no window read of a shading setting from these files. That part is dropped.

</details>

<a id="cli-operator-tools-cli-12"></a>

### CLI-12 -- --approved proposes positions on the walk's film but scans and meters on --film (default negative), with no warning

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan_roll.py:287-306`, `tools/scan_roll.py:342-369`, `tools/scan_roll.py:605-611`

The tool already adopts the walk's prescan resolution because a mismatch silently ruins the roll. The walk's film matters as much and is ignored. A B&W or slide walk followed by `--approved WALK` without `--film` meters every frame as colour negative: per-channel metering that takes a slide's cast off. It also accepts `--ir` on a B&W walk (refused only if --film bw is typed). The exposure is baked into the raw bytes, so this cannot be fixed later.

**Evidence (from the code):**

```text
hold_from_walk: `film = settings.get("film") or FILM_NEGATIVE` ... `frame_edges.propose_centred(frames, film=film)`, returning `"film": film` in the note. main() uses `held_note.get("film")` only for the edge-reader warning (392-393), then calls `s.scan_roll(..., film=args.film, ...)`. The walk's prescan_resolution, by contrast, is adopted: `args.prescan_dpi = walked_at`.
```

**Failure scenario:** The walk is made in the window with film = positive. `scan_roll.py --approved rolls/slides --dpi 1800` scans the whole strip metered as negative, removing each slide's colour balance, and the error is not re-derivable from the library.

**Fix:** Default --film to the walk's film when --approved is given, as prescan_resolution already is. Refuse an explicit conflicting --film unless confirmed.

<details><summary>Second reader's check</summary>

Confirmed. hold_from_walk proposes positions on the walk's film (288, 293) and returns it in the note. main() uses held_note['film'] only for the edge-reader warning (391-393), then scans and meters with film=args.film (610), which defaults to negative. prescan_resolution, by contrast, is adopted from the walk (361). --ir is refused only for an explicit --film bw (313).

</details>

<a id="cli-operator-tools-cli-13"></a>

### CLI-13 -- scan.py does not meter by default, unlike scan_roll.py and the window, so the README's headline command scans at the device's low default exposure

**Severity** medium · **Category** design · **Verdict** confirmed

**Where:** `tools/scan.py:100`, `tools/scan.py:350-359`, `rps7200/direct.py:2965-2969`, `tools/scan_roll.py:152`, `tools/gui.py:1417`

**Doc claim:** README.md:135-138 ("# calibrate, scan, correct, and file the result with its raw bytes" with no --auto-exposure)

Three front ends to one driver give three different default exposures. The README's first example, "calibrate, scan, correct, and file the result with its raw bytes", produces a scan using a few percent of the ADC range. Exposure cannot be changed after the fact: the raw bytes faithfully preserve an underexposed, noisy pass.

**Evidence (from the code):**

```text
`ap.add_argument("--auto-exposure", action="store_true")` (off) and `exposure_scale=exposure_scale` (1.0), with `auto_exposure=args.auto_exposure and not args.exposure_scale`. The driver's own comment reads: `Scans otherwise run at the scanner's defaults, which land around 3-10% of full scale, most of the 16-bit range unused.` scan_roll defaults to `--meter each`; the window defaults to `self.v_expmode = tk.StringVar(value="auto")`.
```

**Failure scenario:** An operator follows README.md:136-137 (`tools/scan.py --dpi 1800 --ir --stock ...`) and archives a roll's worth of single frames at device default exposure. The shadow noise is baked in, and re-decoding from the library cannot help.

**Fix:** Default scan.py to metering, as the other front ends do, with `--no-auto-exposure` or `--exposure-scale` to opt out. Or at least print a warning when neither --auto-exposure nor --exposure-scale was given. Update the README example.

<details><summary>Second reader's check</summary>

Confirmed. scan.py:100 makes --auto-exposure store_true (off) and the exposure_scale default is 1.0 (213). scan_roll defaults to --meter each (152) and the GUI to v_expmode='auto' (gui.py:1416). The driver's own comment says unmetered passes land around 3-10% of full scale (direct.py:2966-2969). The README's headline example (136-137) meters nothing. Its `--film positive  # a slide: keeps its colour cast` example is inert without --auto-exposure, since --film is 'metering only' per scan.py's own help (121-125).

</details>

<a id="cli-operator-tools-cli-14"></a>

### CLI-14 -- A low-contrast frame ends the roll silently with exit 0, even when --frames asked for more

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/direct.py:3987-3993`, `tools/scan_roll.py:832-846`

An unexposed frame mid-strip (a missed shot, a fogged frame, the leader) reads as "end of film" and ends the roll. With `--frames 36` given, the run can end after 12 frames, print "12 scanned, 0 failed" and exit 0. The only hint is a log line. For an unattended roll this is a silent partial result, and any caller checking the exit status believes the job completed.

**Evidence (from the code):**

```text
In scan_roll: `if contrast < blank_contrast: self._log(... "end of film"); return`. The tool's summary is `print(f"\n{scanned} scanned, {failed} failed, ...")` and `return 1 if trouble is not None or failed or not saved else 0`, with no comparison against args.frames.
```

**Failure scenario:** `scan_roll.py --frames 36 ...` meets a blank frame 13 at 2 a.m. The roll ends and exits 0. In the morning the operator finds 12 frames and, per CLI-10, cannot easily resume just the rest.

**Fix:** When --frames was given and fewer places were covered, print a clear warning naming the frame where it stopped and why, and exit non-zero (or with a distinct code). Consider skipping blank frames rather than ending the roll.

<details><summary>Second reader's check</summary>

Confirmed. scan_roll returns on a contrast below blank_contrast with only a log line (direct.py:3987-3993). The CLI summary and exit status (scan_roll.py:832-846) never compare the frames covered with args.frames, so a roll that asked for 36 and stopped at 12 exits 0.

</details>

<a id="cli-operator-tools-cli-15"></a>

### CLI-15 -- filing_load_test.py drives the scanner with no DeferredInterrupt and no estimate; Ctrl-C mid-pass wedges it, and --rounds 0 crashes after warm-up

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/filing_load_test.py:186-222`

CLAUDE.md asks for this tool to be run before trusting the roll path unattended. Its 2 x rounds full passes run with default SIGINT behaviour, so Ctrl-C raises KeyboardInterrupt inside read_planes and abandons the read. `--rounds 14` (28 x ~22 s plus warm-up) passes 10 minutes with no warning. `--rounds 0` warms the lamp and then crashes with StatisticsError.

**Evidence (from the code):**

```text
`with DirectScanner(verbose=False) as s: s.wait_ready(timeout=180.0); s.wait_warm(timeout=300.0) ... quiet, loaded = run_rounds(one, args.rounds, ...)`. There is no `DeferredInterrupt`, no validation of `--rounds`/`--mb`/`--limit`, and `statistics.mean(quiet)` is called on an empty list when rounds is 0.
```

**Failure scenario:** The operator presses Ctrl-C to cut a long measurement short. The pass in flight is abandoned, the device is marked suspect and then needs a power cycle.

**Fix:** Wrap the run in DeferredInterrupt and stop between rounds. Validate rounds >= MIN_ROUNDS, mb > 0 and limit > 0 before opening the device, and print an estimate with the background warning.

<details><summary>Second reader's check</summary>

Confirmed. main() (186-222) opens DirectScanner without DeferredInterrupt, so SIGINT keeps its default and a Ctrl-C raises inside a read. There is no estimate. --rounds, --mb and --limit are not validated. --rounds 0 leaves quiet empty, and statistics.mean(quiet) raises StatisticsError at 213, after wait_warm. Minor addition: the tool exits 0 whatever the verdict, so a caller cannot act on 'unsafe'.

</details>

<a id="cli-operator-tools-cli-a1"></a>

### CLI-A1 -- Ctrl-C during the post-close filing is no longer deferred, and loses the passes or frames still held only in memory

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `tools/scan.py:265-375`, `tools/scan_roll.py:475`, `tools/scan_roll.py:803`, `rps7200/console.py:101-102`, `rps7200/session.py:1584`

Both tools deliberately do their heavy filing (gzip of hundreds of MB per pass) after the device closes, and both print nothing while they do it. That is exactly when the DeferredInterrupt has been removed. A Ctrl-C then raises KeyboardInterrupt straight into the save loop (scan.py) or into writer.finish()'s join (scan_roll.py):
- scan.py: the pass being saved is left as an INCOMPLETE directory, and every later pass in `pending`, the bracket merge and --out are lost.
- scan_roll.py: the process exits, killing the daemon writer mid-save, so the last one to three queued frames (raw bytes held only in memory) are lost and the final manifest save is skipped.
With debug on, those passes were already claimed and their spools deleted at close() (CLI-07), so no copy survives anywhere.

**Evidence (from the code):**

```text
scan.py: `with interrupt:` ends at 360, and `for held in pending: entries.append(library.save(...))` (366-375) runs after it with the previous SIGINT handler restored (`def __exit__(self, *exc) -> None: self._restore()`). scan_roll.py: `with interrupt, DirectScanner(...)` closes before `writer.finish()` (803), and the writer thread is `daemon=True`.
```

**Failure scenario:** After a 9-pass 3600 dpi bracket the terminal goes quiet for a minute or two while nine entries are gzipped. The operator presses Ctrl-C, thinking the run has hung. Pass 3's entry is left INCOMPLETE and passes 4-9 and the merged file are gone; the scanner time is lost.

**Fix:** Keep DeferredInterrupt (or a filing-specific equivalent) in force until filing ends, and print 'filing N passes, do not interrupt' before it starts. Make FrameWriter's thread non-daemon so an exit waits for it.

<a id="cli-operator-tools-cli-16"></a>

### CLI-16 -- scan_roll: a Ctrl-C before the first frame still calibrates, and on a dry run replaces the folder's survey.json with an empty walk

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan_roll.py:492-512`, `tools/scan_roll.py:568-603`, `rps7200/direct.py:3929-3933`

Stopping during a long rewind or seek still costs a full 3-4 minute calibration. On `--dry-run` into an existing walk folder (`--roll`/`--out`), it also writes an empty survey.json over the walk. The old walk survives only as survey.json.bak, and only for one run; a second aborted attempt overwrites the .bak too.

**Evidence (from the code):**

```text
rewind(), seek(), the nudge loop and `calibrate(s, args)` never consult `interrupt.requested`. The manifest is then written: `record_of = RollManifest(manifest_path, manifest, ...); record_of.write(); placed = True`, where a dry run's `manifest["frames"]` is `[]`. Only then does scan_roll's loop check `should_stop` and return.
```

**Failure scenario:** The operator re-walks `--roll strip7 --dry-run`, realises the wrong strip is in and presses Ctrl-C during the seek. The tool calibrates for 4 minutes, replaces strip7's survey.json with an empty one and exits 0.

**Fix:** Check `interrupt.requested()` after rewind, seek and nudge and before calibrate, and return before anything is written.

<details><summary>Second reader's check</summary>

Confirmed. rewind, seek, the nudge loop and calibrate() (492-568) never check interrupt.requested(). On --dry-run, the manifest's frames stay [] and record_of.write() (599-602) replaces survey.json; write_manifest keeps a .bak (session.py:917-921). Only then does scan_roll's loop see should_stop and return (direct.py:3913). The run exits 0 because failed=0 and trouble is None.

</details>

<a id="cli-operator-tools-cli-17"></a>

### CLI-17 -- `--library ''` means skip in scan_roll.py but files into the current directory in scan.py

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:140-150`, `tools/scan.py:281`, `tools/scan_roll.py:175-178`, `rps7200/library.py:258`, `rps7200/session.py:1638`

The two capture tools spell "do not file" differently, and scan.py's spelling for the roll tool's skip silently files entry directories plus an index.json into the current working directory (usually the repo root). Nothing is lost, but the library is split and the tree is littered. The same scan with --no-library would leave no raw bytes at all.

**Evidence (from the code):**

```text
scan_roll help: `"file every frame in the library ...; --library '' to skip"`, honoured by `if job["library"]:`. scan.py checks only `if args.library is None: return` and calls `library.save(..., root=args.library)`, where `root = Path(root)` makes Path('') == '.'.
```

**Failure scenario:** The operator types `scan.py --library ''`, expecting it to skip filing as scan_roll does. A `20260927T...` entry directory and an index.json appear in the repo root, invisible to `library.py --root library`.

**Fix:** Treat an empty --library as "skip" in scan.py (or refuse it), and accept --no-library in scan_roll.py for symmetry.

<details><summary>Second reader's check</summary>

Confirmed. scan.py checks `if args.library is None` (281) and passes root=args.library to library.save, where Path('') is '.' (library.py:258). keep_raw is `args.library is not None`, so it is True for ''. scan_roll treats '' as falsy (616, 729, and FrameWriter's `if job['library']`).

</details>

<a id="cli-operator-tools-cli-18"></a>

### CLI-18 -- Calibration raw bytes live outside the library behind a cwd-relative pointer, and --reuse drops the link to them

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:720-729`, `rps7200/direct.py:848-860`, `rps7200/direct.py:764-803`, `tools/scan.py:94-97`, `tools/scan_roll.py:159`

The reference in each entry is a reduction of calibration bytes kept outside the library, in a folder chosen by --reference's parent (cwd-relative, possibly a temp directory). The entry points at it by a relative path. No tool verifies it (`verify` checks only entries). Any entry corrected by a `--reuse`d reference loses the pointer entirely, so the calibration bytes behind it can only be found by matching timestamps or hashes by hand.

**Evidence (from the code):**

```text
`archive = self.archive_calibration(result, path.parent)` writes `calibration/<UTC>/data.bin`, and `self._shading_origin["archive"] = str(archive)` stores a relative path. load_shading replaces the origin with `{"action": "loaded", "path": str(path), "file_modified_utc": ..., "loaded_utc": ...}` and no archive. The library docstring says `Entries are self-contained directories: nothing refers out`.
```

**Failure scenario:** A session uses `--reuse`. Months later a better reduction of calibration data is written, but the entries' shading_origin only names `calibration/shading.npz` and an mtime, with no archive id; the matching data.bin must be found by hand, if it still exists.

**Fix:** Store the archive id and hash in the cached shading.npz (or a sidecar) so load_shading can carry it into shading_origin. Resolve archive paths to absolute paths, or relative to the library. Consider copying or hard-linking data.bin into each entry, or into a `library/calibrations/` store that verify checks.

<details><summary>Second reader's check</summary>

Confirmed. archive_calibration(result, path.parent) writes under --reference's parent (direct.py:848). shading_origin['archive'] = str(archive) is relative whenever --reference is relative (859-860). load_shading replaces _shading_origin with a dict that has no archive key (720-729). library.verify never looks at calibration/. The library docstring says 'nothing refers out' (library.py:28).

</details>

<a id="cli-operator-tools-cli-19"></a>

### CLI-19 -- --reuse accepts a cached reference of any age without saying how old it is; --reference into a library entry without --reuse overwrites that entry's reference

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/direct.py:835-845`, `rps7200/direct.py:736-747`, `rps7200/direct.py:864-866`, `tools/scan.py:94-97`

A reference from another power-on, lamp state or even an empty-transport calibration is used silently. Because it is the reference stored in each new entry, it becomes the only reference those entries will ever have. Separately, `--reference library/<id>/shading.npz` given without `--reuse` recalibrates and replaces the checksummed reference inside a library entry (`verify` then reports a mismatch, and the original is gone). It also drops a calibration archive folder into that entry.

**Evidence (from the code):**

```text
`if reuse and path.exists(): reference = self.load_shading(path); return {..., "summary": f"reusing {path} ({reference.pixels_per_line} columns, channels {reference.channels})"}`. The summary gives no age or power-on, and the mtime is only recorded in the entry. Without reuse: `saved = self.save_shading(path)`, then `os.replace(temp, path)` over whatever file is there.
```

**Failure scenario:** The operator copies a command from shell history with `--reference library/2026.../shading.npz` but without `--reuse`. The entry's own shading.npz is overwritten by today's calibration.

**Fix:** Print the cached file's age, warn past N hours or when it predates the current power-on, and refuse a --reference path inside a library root.

<details><summary>Second reader's check</summary>

Confirmed. The --reuse summary reports only columns and channels (direct.py:835-845), and load_shading records the mtime only into entries. Without --reuse, ensure_shading calibrates, save_shading does os.replace over the given path (736-747), and archive_calibration creates a <UTC> folder in path.parent. So `--reference library/<id>/shading.npz` destroys that entry's checksummed reference and plants a folder inside the entry.

</details>

<a id="cli-operator-tools-cli-20"></a>

### CLI-20 -- --dpi and --prescan-dpi reach MODE SELECT unvalidated beyond positivity and correctability

**Severity** low · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/scan.py:184-191`, `tools/scan_roll.py:325-338`, `rps7200/direct.py:1450`

Resolutions nobody has measured (e.g. 5000, 1234) are sent to the device, whose response to them is unknown. The CLAUDE.md timing table and the frame-edge reader cover only specific values. A value above 65535 raises OverflowError after exposure, frame and gain commands have already been sent.

**Evidence (from the code):**

```text
The only checks are `if args.dpi <= 0` and `_Driver.correctable_at(args.dpi)`, which --no-shading skips. set_mode sends `data[2:4] = resolution.to_bytes(2, "little")`.
```

**Failure scenario:** A typo such as `--dpi 18000 --no-shading` passes validation and reaches the device with an untested resolution.

**Fix:** Validate against the set of resolutions the driver has measured (300/600/900/1200/1800/3600/7200, or whatever docs/ lists), with an explicit --unmeasured-dpi override.

<details><summary>Second reader's check</summary>

Confirmed. The only pre-open checks are positivity and correctable_at (scan.py:184-191; scan_roll.py:325-338), and --no-shading bypasses the latter. set_mode does resolution.to_bytes(2, 'little') (direct.py:1450). In scan(), set_exposure_time (3016) and set_gain_offset (3029) run before set_mode (3051), so a value above 65535 raises OverflowError after commands have been sent.

</details>

<a id="cli-operator-tools-cli-21"></a>

### CLI-21 -- A bracket holds every pass's pixels and raw bytes in RAM with no memory estimate; at 7200 dpi --no-shading that is about 10 GB

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/scan.py:255-263`, `tools/scan.py:330-346`, `rps7200/direct.py:1835-1881`

Nine passes at 7200 dpi RGB with --no-shading come to about 9 x (430 MB pixels + 430 MB bytes), plus a transient 2x for the last read. A MemoryError in `chunks.append` abandons the read, which wedges the scanner. One in `b"".join` marks the device suspect although every line is in, because `_read_complete` is set after the join. scan.py warns about time but never about memory.

**Evidence (from the code):**

```text
`pending.append(dict(capture, inquiry=info, meta=meta, image=...raw))`: capture holds the pass's `raw` bytes, and scan_bracket keeps `frames` because `retain=True`. The device stays open for all passes. read_planes collects `chunks` and then `b"".join(chunks)` (2x the raw size at the peak).
```

**Failure scenario:** `scan.py --dpi 7200 --no-shading --bracket 9` on a 16 GB laptop reaches a MemoryError during pass 8's read. The read is abandoned and the device wedges; passes 1-7 are filed.

**Fix:** Estimate peak memory before opening the device and refuse or warn. Spool bracket passes to disk as debug filing does, and set `_read_complete` before the join.

<details><summary>Second reader's check</summary>

Confirmed in shape. pending holds each pass's capture (raw bytes) plus its pixels, scan_bracket retains its returned frames, and there is no memory estimate. read_planes builds chunks and then b''.join (1834-1876), and _read_complete is set after the join (1879), so a MemoryError in the join is treated as an incomplete read. At 7200 dpi this is reachable only with --no-shading. The exact ~10 GB figure is an approximation.

</details>

<a id="cli-operator-tools-cli-22"></a>

### CLI-22 -- scan_roll.py has no mono delivery: B&W roll frames are three-channel, where scan.py and the window deliver one

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/scan_roll.py:731-776`, `rps7200/session.py:1630-1631`, `rps7200/session.py:2631-2632`, `tools/scan.py:405-412`

The same --film bw strip delivers differently depending on the front end. rps7200/mono.py explains why a consumer cannot tell B&W from colour by the pixels. The library is unaffected.

**Evidence (from the code):**

```text
The window's job passes `mono=wants_mono(job.mono, job.film)`, and scan.py does `if args.mono is None: args.mono = args.film == "bw"`. scan_roll.py's writer.submit carries no `mono` key, so FrameWriter's `if job.get("mono")` is false.
```

**Failure scenario:** A B&W roll scanned with scan_roll.py is handed to NegPy as RGB and processed as colour negative.

**Fix:** Add --mono/--no-mono/--mono-channel to scan_roll.py with the wants_mono default, and pass mono and mono_channel to writer.submit.

<details><summary>Second reader's check</summary>

Confirmed. writer.submit in scan_roll.py (731-776) passes no mono or mono_channel, so FrameWriter's job.get('mono') is falsy (session.py:1630-1631). scan.py defaults mono for bw (405-406) and the window passes wants_mono.

</details>

<a id="cli-operator-tools-cli-23"></a>

### CLI-23 -- Dry-run prescans are deflate-compressed on the scanning thread with the device open, which the window deliberately avoids

**Severity** low · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/scan_roll.py:658-661`, `tools/scan_roll.py:686-694`, `rps7200/session.py:2543-2548`, `rps7200/tiff.py:97-103`

The rule "nothing local on the scanning thread with the device open" is broken on the CLI walk path. The amount is small, but it is the same class of work (compression with the device idle) that preceded a wedge, and the two front ends differ.

**Evidence (from the code):**

```text
scan_roll.py does `tiff.write(str(pre), frame.prescan)`, whose default is `compress: bool = True` (deflate on the tifffile path), in the generator's consumer between passes. The window's comment says: `Written by the writer thread, not here: it is only ~370 KB, but nothing local happens on the scanning thread with the device open.`
```

**Failure scenario:** On a slow disk (a network share), each prescan write holds the device idle between passes on the CLI and not in the window.

**Fix:** Hand prescanNN.tif and prescanNN-before.tif to the FrameWriter (paths=[pre]), as the window does, or write them with compress=False.

<details><summary>Second reader's check</summary>

Confirmed. tiff.write's default is compress=True (tiff.py:97-103), and scan_roll.py:660 and 693 call it on the consuming thread between passes with the device open. The window routes the same file through its writer thread (session.py:2543-2548).

</details>

<a id="cli-operator-tools-cli-24"></a>

### CLI-24 -- scan_roll.py still speaks millimetres for transport distances, and a sizing comment is stale

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan_roll.py:117-122`, `tools/scan_roll.py:514-535`, `tools/scan_roll.py:700-714`

**Doc claim:** CLAUDE.md:284-286 ("Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter")

CLAUDE.md prohibits millimetres for transport distances and asks for units of the adjustment parameter. The operator-facing flag and output of this tool still use mm (the README admits it). The comment's 1.0118 mm cap predates param 87.

**Evidence (from the code):**

```text
`ap.add_argument("--nudge", type=float, ... help="move the film this many mm before starting ...")`, `print(f"offset the film by {sent:+.3f} mm ...")`, `said = ... f"offset {offset:+.2f} mm"`, `SHORT BY {short:.2f} mm`. The comment at 519 reads `one command reaches only 1.0118 mm and param_for_mm clamps there silently`, but MAX_CORRECTION_PARAM is 87 (about 9.4 mm).
```

**Failure scenario:** An operator reads a --nudge value off the window, which shows units, types it as mm, and moves the film about 9.5x further than intended.

**Fix:** Take --nudge in units (say_units / protocol conversions) and print the registration lines in units. Update the comment.

<details><summary>Second reader's check</summary>

Confirmed. --nudge is in mm (117-122) and the output prints mm (534-535, 701, 704, 710). The comment at 518-520 cites 1.0118 mm, but MAX_CORRECTION_PARAM is 87 (direct.py:3596). plan_nudges' own docstring (session.py:1208) still says param 1..8. MM_PER_UNIT is 0.1057, so a units value typed as mm moves about 9.5 times as far.

</details>

<a id="cli-operator-tools-cli-25"></a>

### CLI-25 -- `duplicates --delete` removes entries with rmtree on the records' word, without checking the survivor on disk

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/library.py:174-197`, `rps7200/library.py:987-1011`, `rps7200/library.py:1014-1027`

The kept twin is chosen from what its scan.json says, not from what is on disk. If its raw.bin.gz is missing, truncated or bit-rotted (conditions only `verify` detects), `--delete` destroys the only intact copy.

**Evidence (from the code):**

```text
usefulness() ranks by `bool((record.get("raw") or {}).get("file"))`, and same_data compares recorded `sha256` fields. Then `if args.delete: shutil.rmtree(path)`.
```

**Failure scenario:** The survivor's raw.bin.gz was truncated by an earlier crash, and the redundant entry's is fine. `duplicates --delete` removes the good one; `verify` afterwards reports the survivor's raw bytes as damaged, and no intact copy remains.

**Fix:** Before deleting, run the verify checks on the chosen survivor (file present and checksum matches). Skip the group if it fails, or keep the verified copy.

<details><summary>Second reader's check</summary>

Confirmed. usefulness() ranks on recorded fields only (library.py:987-990). same_data compares recorded sha256 values (1014-1027). tools/library.py:190-191 then does shutil.rmtree without checking the survivor's files on disk.

</details>

<a id="cli-operator-tools-cli-26"></a>

### CLI-26 -- migrate-raw rewrites labelled entries without checking the new decode against the stored pixels

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/library.py:226-257`

The comment says a rewrite must not "launder" a decode regression into the library. For entries labelled `corrections_applied: ["shading"]`, a changed decode (which `reconstruct` would call "decode CHANGED") is written as the new scan.tif anyway. The old file survives as scan.before-migrate-raw.tif, but the record then calls the new pixels raw and correct.

**Evidence (from the code):**

```text
`decoded, verdict = library.reconstruct(path)`: the verdict is ignored except for None. The laundering guard runs only `if not applied and not _one_shading_explains(...)`. A labelled entry (`applied` non-empty) goes straight to `planned.append(...)` once the shapes match.
```

**Failure scenario:** With a regressed decoder on the branch, `migrate-raw --write` converts 40 labelled legacy entries to the regressed decode, and `reconstruct` then reports them identical.

**Fix:** For labelled entries, require that the fresh decode shaded once with the entry's reference and mask equals the stored pixels (`_one_shading_explains`), or that the reconstruct verdict starts with "identical", before rewriting.

<details><summary>Second reader's check</summary>

Confirmed. In migrate-raw, the _one_shading_explains guard runs only when `not applied` (tools/library.py:245). A labelled entry whose shapes match goes straight into planned and is rewritten with the plain decode, whatever the reconstruct verdict. The old file is kept as scan.before-migrate-raw.tif and the raw bytes are untouched, so it is recoverable.

</details>

<a id="cli-operator-tools-cli-27"></a>

### CLI-27 -- Default output locations are relative to the current directory while imports are relative to the repo

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan.py:76`, `tools/scan.py:94`, `tools/scan.py:140`, `tools/scan_roll.py:159`, `tools/scan_roll.py:175`, `tools/scan_roll.py:407`, `rps7200/library.py:53`, `tools/make_comparison.py:123-127`

Run from anywhere but the repo root (e.g. `cd tools; uv run python scan.py`), the tools work but create a second library/, rolls/ and calibration/ there. A `--reuse` then silently recalibrates, and debug entries follow the same cwd. The default `--out scan.tif` is overwritten by every run without a prompt. With --no-library that destroys the previous scan outright.

**Evidence (from the code):**

```text
`DEFAULT_ROOT = Path("library")`, `--reference default="calibration/shading.npz"`, `roll_dir("rolls", ...)`, `--out default="scan.tif"`, `--previews default="previews"`, while every tool does `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`.
```

**Failure scenario:** A Windows operator launches scan_roll.py from Explorer's tools folder. A two-hour roll files into tools/library, which `make verify` and `make reconstruct` never see.

**Fix:** Resolve default roots against the repo root (or an RPS7200_HOME), print the absolute paths at start, and refuse to overwrite an existing --out without --overwrite.

<details><summary>Second reader's check</summary>

Confirmed. DEFAULT_ROOT = Path('library') (library.py:53), --reference defaults to calibration/shading.npz, roll_dir('rolls', ...), --out scan.tif and --previews previews are all resolved against the cwd, while imports come from sys.path.insert of the repo root. tasks.py runs with cwd=ROOT, but the tools themselves do not. --out is overwritten without a prompt, and its sidecar ./scan.json with it.

</details>

<a id="cli-operator-tools-cli-28"></a>

### CLI-28 -- scan.py's docstring says the scanner applies its own shading correction

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/scan.py:2`

**Doc claim:** CLAUDE.md "The scanner **never applies its own shading correction**; it hands back a reference and the host divides."

The code computes the host-side flat-field from the reference the scanner returns (apply_shading in DirectScanner.scan). The scanner never applies it, as CLAUDE.md states as a fact that is easy to get wrong. This line is the first thing `--help` prints.

**Evidence (from the code):**

```text
`"""Scan with the scanner's own shading correction applied.`
```

**Failure scenario:** A reader of `scan.py --help` concludes that the device corrects its own output and that raw library pixels are already flat-fielded.

**Fix:** Reword to "Scan, flat-field on the host from the scanner's shading reference, and file the raw pass".

<details><summary>Second reader's check</summary>

Confirmed. scan.py:2 says 'Scan with the scanner's own shading correction applied', which contradicts lines 6-9 of the same docstring and CLAUDE.md. The correction is host-side (apply_shading in scan()).

</details>

<a id="cli-operator-tools-cli-a2"></a>

### CLI-A2 -- Debug filing ignores --library: the probes, holds and aim prescans of a run go to ./library or RPS7200_DEBUG_ROOT, apart from the run's own entries

**Severity** low · **Category** library-completeness · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1049`, `tools/scan.py:140-145`, `tools/scan_roll.py:175-178`

`scan.py --library D:/lib` (or scan_roll with --library elsewhere) files its frames there, but everything debug filing catches from the same run lands in a cwd-relative ./library: metering probes, hold and aim prescans, and a real roll's framing prescan raw pixels. The evidence for a frame's exposure and registration is then in a different library from the frame, and `make verify` / `make reconstruct` see only the repo-root one.

**Evidence (from the code):**

```text
`root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` in _debug_flush. Neither tool passes --library to the scanner or sets the env var.
```

**Failure scenario:** An operator scans a roll to an external drive with `--library /mnt/ext/library` and RPS7200_DEBUG=1. The raw framing prescans and metering probes for every frame end up in <cwd>/library on the laptop disk, and are not found alongside the roll's entries later.

**Fix:** Have the tools set the scanner's debug root to --library, via an attribute or by exporting RPS7200_DEBUG_ROOT, when the user has not set it explicitly.

<a id="cli-operator-tools-cli-29"></a>

### CLI-29 -- No tool compacts plain entries left by a window that was killed before compacting

**Severity** info · **Category** library-completeness · **Verdict** confirmed

**Where:** `tools/library.py:74-77`, `rps7200/library.py:414-453`, `rps7200/session.py:1990-1995`

**Doc claim:** CLAUDE.md:188-192

CLAUDE.md says such entries stay plain, complete and verifiable, which is true. But nothing ever gzips them later, so a crashed window leaves `raw.bin` and uncompressed TIFFs (about 2x the disk) for ever.

**Evidence (from the code):**

```text
The action choices are `["list", "verify", "reconstruct", "reindex", "duplicates", "migrate-raw", "migrate-direction", "tag"]`. library.compact's only caller is ScanSession at shutdown, over `self._writer.uncompressed`.
```

**Failure scenario:** After a crash, a roll's 30 uncompressed 3600 dpi entries stay at twice their size with no command to fix them.

**Fix:** Add `tools/library.py compact [--write]`, which compacts every entry holding raw.bin, checked against its checksum.

<details><summary>Second reader's check</summary>

Confirmed. library.compact's only caller is session.py:1992. tools/library.py's actions (74-77) have no compact, and tasks.py has none either.

</details>

<a id="cli-operator-tools-cli-a3"></a>

### CLI-A3 -- scan_roll.py's comment says writer.finish() is reached through a finally, but early returns inside the try skip it

**Severity** info · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `tools/scan_roll.py:796-803`, `tools/scan_roll.py:498`, `tools/scan_roll.py:512`, `tools/scan_roll.py:528`, `tools/scan_roll.py:543`

No frame has been submitted at any of those returns, so nothing is lost today. But the comment describes a guarantee the code does not make. A future early return placed after the first writer.submit would silently skip writer.finish() and the manifest update.

**Evidence (from the code):**

```text
Comment: `Reached through a finally around the whole scanning block, so it runs whatever came out of it.` The code is `try: ... except BaseException as exc: ...` with no finally, and contains `return 1` / `return 0` at 498, 512, 528 and 543.
```

**Failure scenario:** A maintainer adds a `return 1` inside the frame loop, trusting the comment; the frames queued before it die unfiled on the daemon writer.

**Fix:** Use a real try/finally for writer.finish() and the error report, or correct the comment.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry for each pass filed by scan.py, scan_roll.py (frames and --dry-run prescans) and the debug flush | <--library, default ./library>/<UTC>_<stock>_[f<frame>_]<dpi>dpi[_ir][-N]/{scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif, scan.json, INCOMPLETE while writing} | scan.tif: uint16 (uint8 for prescans) H x W x 3\|4 TIFF, deflate-compressed when tifffile is present, else uncompressed. raw.bin.gz: gzip of the INDEX-format bulk bytes. shading.npz: savez_compressed reference arrays. ccd_mask.bin: raw mask bytes. scan.json: record with sha256 of every file, scan fields, extra.commands, shading_origin, roll_membership, provenance. | scan.tif and raw.bin.gz are raw (decode only, upright, 7200 dpi stagger-realigned and recorded). prescan.tif in a roll frame entry is CORRECTED (flat-fielded 8-bit prescan), with no raw bytes or own mask (CLI-09). | library.save: from scan.py after close() (tools/scan.py:366-375); from FrameWriter's thread during the roll (rps7200/session.py:1656-1668); from DirectScanner._debug_flush at close() (rps7200/direct.py:1063-1074) | tools/library.py (list/verify/reconstruct/duplicates/migrate-*), tools/make_comparison.py (library.load/corrected), the window, library.corrected for exports | Lossless and exact for raw bytes and scan.tif; the record is written atomically last and INCOMPLETE marks a cut-short entry. Not exact for the roll frame prescan (corrected-only). Lost entirely if library.save fails (CLI-01/06/07). |
| Library index | <library>/index.json | JSON summary list | derived metadata | library.reindex after every save, tag, duplicates --delete, migrate-* | nothing in code (derived, safe to delete) | Non-atomic write; may race between the FrameWriter thread and the debug flush; harmless since unread. |
| Cached shading reference | <--reference, default ./calibration/shading.npz> (temp .shading.part.npz while written) | npz (savez_compressed) | reduction of calibration data (not raw) | DirectScanner.save_shading via ensure_shading after each calibration (rps7200/direct.py:736-747, 864-866) | ensure_shading/load_shading on --reuse (scan.py, scan_roll.py, the window) | Written atomically; lossless for the arrays. No age, power-on or archive id is recorded inside, so --reuse loses provenance (CLI-18/19). |
| Calibration archive (the calibration's own bytes) | <parent of --reference, default ./calibration>/<YYYYmmddTHHMMSSZ>[-N]/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | data.bin: every calibration line exactly as read. calibration.json: width, stride, resolution, commands, sha256, protocol_revision. | raw | DirectScanner.archive_calibration inside ensure_shading, with the device open, uncompressed (rps7200/direct.py:749-804) | nothing in code; entries point at it through extra.shading_origin.archive (a cwd-relative path) | Exact bytes. Outside the library, not verified by any tool, and the pointer is lost for --reuse sessions. |
| scan.py delivered file and sidecar | <--out, default ./scan.tif> (or .jpg plus a companion .dng for IR) and <out>.json | TIFF uint16 (or 8-bit JPEG); mono optional; sidecar JSON of the pass meta (plus bracket ratios and stats) | corrected (and merged for a bracket) | export.write and write_text at tools/scan.py:413-416, after library filing | the operator and downstream tools (NegPy) | Lossless TIFF; JPEG is lossy. Overwritten without prompt; the merged bracket result exists only here (re-derivable from the per-pass entries). |
| Roll manifest / walk survey | rolls/<roll>/roll.json or survey.json (+ .bak once per run, .legacy, .unreadable, .<name>.part) | JSON: roll, numbering, started/finished/stopped, settings{dpi, infrared, meter, film, dry_run, start_at, frames, prescan_resolution}, held, and frames[{number, index, transport_position, registration, error, entry, file, done, filing_error, prescan, prescan_before, shape, exposure}] | metadata | RollManifest.record/filed/save via write_manifest (atomic, fsync) from the main thread and FrameWriter (tools/scan_roll.py:599-603, 781, 830) | scan_roll.py resume (carry forward only), hold_from_walk for --approved (json.loads of survey.json, then roll.json), the window | Atomic. Settings are those of the last run only; the CLI omits fast_infrared, shading, correct and max_failures (CLI-11). |
| Delivered roll frames | rolls/<roll>/frameNN.tif | TIFF uint16, 3 or 4 channels (never mono from the CLI) | corrected, by that day's code | FrameWriter._write after library.save (rps7200/session.py:1673-1688) | the operator, the window | Lossless. Not written at all when library.save fails. |
| Walk prescans | rolls/<roll>/prescanNN.tif, prescanNN-before.tif | TIFF uint8 RGB, deflate when tifffile is present | corrected | tiff.write on the scanning thread in the --dry-run branch (tools/scan_roll.py:658-661, 686-694) | hold_from_walk for --approved (references, via preview.unorient), the window's contact sheet | Lossless for the corrected pixels. Overwritten without backup by a re-walk into the same folder; `-before` exists only as corrected TIFF (its raw bytes are kept only with debug on). |
| Debug spool | $TMP/rps7200-debug-*/NNN-{image.npy, raw.bin, meta.json, shading.npz, ccd_mask.bin} | npy pixels, raw bytes, JSON meta sidecar | raw | DirectScanner._debug_capture on every pass while debug is on (rps7200/direct.py:908-998) | _debug_flush at close(), which files unclaimed passes into RPS7200_DEBUG_ROOT or ./library and deletes claimed ones | Exact. Claimed passes are deleted before the claimant has filed them (CLI-07). |
| Comparison files | <--out, default .>/{1_nothing_done.tif, 2_corrected.tif, 3_corrected_inverted.tif}; <--previews, default previews>/{cmp_before.png, cmp_after.png} | TIFF via export.write; half-size 8-bit PNG previews | 1_ raw decode; 2_ library.corrected (today's code); 3_ inverted and stretched | tools/make_comparison.py:159-175 | Stefan, by eye; the tool reads 1_ and 2_ back to measure | Lossless TIFFs; the PNGs are downscaled previews. |
| migrate-raw backups | <entry>/scan.before-migrate-raw.tif, <entry>/.scan.tif.part | TIFF | the previously stored (corrected) pixels | tools/library.py:273-284 | nothing (kept for manual recovery) | Exact copy of the old file. A crash between the two os.replace calls leaves no scan.tif. |

**Second reader's corrections to this table:**

- **scan.py sidecar.** It is `out.with_suffix(".json")`, not `<out>.json`. With the default `--out scan.tif` it is `./scan.json` in the current directory, and it is overwritten on every run with no prompt.
- **Walk prescans (`prescanNN.tif` / `-before.tif`).** The claim is correct. In addition, the library entries a --dry-run files for these prescans hold the raw uint8 prescan in scan.tif, via FrameWriter with raw_image=frame.raw_prescan. The survey.json records for them keep `done: false` for good, and get `entry` only after `writer.finish()`.
- **index.json.** It is written non-atomically (`index.write_text`) by `reindex` after every save. In scan_roll, the debug flush (main thread, inside close()) and FrameWriter (its own thread) can reindex at the same moment. Each reindex also reads every scan.json in the library, so it is O(entries) per save.
- **Calibration archive.** `calibration.json` is written with a plain `write_text`, not atomically. The folder is created inside `--reference`'s parent, which can be a library entry directory if `--reference` points into one (CLI-19).
- **Library entry for a roll frame.** When library.save raises, a partial directory is left with the INCOMPLETE marker and whatever files were written before the failure, possibly hundreds of MB. It is never cleaned up.
- **Debug spool.** Unclaimed passes are filed to `RPS7200_DEBUG_ROOT` or a cwd-relative `./library`, never to the tool's `--library` (CLI-A2).
- **Roll manifest.** `stopped` is also written as "stopped by Ctrl-C after the frame in flight" when the stop was clean. For a stop that came from the scan_roll generator itself (a blank frame, max_failures, the end of the transport), the manifest records nothing about why the roll ended (CLI-14).

## What the operator can do

- Scan one frame with tools/scan.py at any positive --dpi (corrected up to 3600 dpi; 7200 only with --no-shading), RGB or RGBI (--ir, tied by default, --no-fast-ir to untie), with or without --auto-exposure or a fixed --exposure-scale, delivering TIFF or JPEG, optionally mono.
- Take a 2-9 pass RGB bracket (--bracket N --stops S), each pass filed raw and merged into the delivered file.
- Reuse the cached shading reference with --reuse instead of calibrating (3-4 min saved), or skip correction with --no-shading.
- Skip library filing: --no-library in scan.py, --library '' in scan_roll.py.
- Walk a strip with scan_roll.py --dry-run (prescans filed raw with --library), then scan it with --approved WALK_FOLDER to hold each frame to the proposed centred position.
- Scan a roll unattended with metering each/once/none, optional --correct or --correct-dry-run registration, --max-failures, and --rewind/--start-at/--nudge to place the film.
- Resume a roll by naming it again (--roll or --out) with --start-at N; earlier frame records are carried forward.
- Stop a roll between frames with one Ctrl-C (the frame in flight finishes).
- Inspect the library: list, verify (checksums, INCOMPLETE), reconstruct (re-decode), duplicates [--delete --keep N], migrate-raw [--write], migrate-direction [--write], tag --add, reindex.
- Write the three comparison TIFFs from any entry whose correction state is 'applied'.
- Measure background-gzip interference with filing_load_test.py (--rounds, --mb, --limit).
- Run make all/test/test-all/lint/type/fix/run/run-demo/run-sheet/reconstruct/verify/clean, or `python tasks.py <target>` without make.

## What the operator should not do

- Run scan_roll.py (walks included) or any scan.py run near 8 minutes in the foreground of a harness that kills at 10 minutes: the killed read wedges the scanner.
- Press Ctrl-C a second time, or kill, close or hang up the terminal of a running scan or roll: it abandons the read (wedge) and loses queued frames.
- Calibrate with an empty transport (scan.py calibrates wherever the film is, without asking).
- Use --no-library (or --library '' in scan_roll) for anything that might matter: the raw bytes and calibration are gone for good.
- Use --reuse across power cycles or lamp changes, or with a reference of unknown origin.
- Point --reference at a file inside a library entry without --reuse.
- Resume a roll with different --dpi/--ir/--film/--meter/--no-shading than it started with.
- Run `duplicates --delete` without running `verify` first.
- Run make format (not part of make all) without agreement.
- Trust `make reconstruct`'s exit 0 when its '-' lines say 'could not decode' or 'could not read scan.tif'.

## Mistakes nothing guards against

- Pressing Ctrl-C during scan.py's calibration or metering prints 'stopping after the pass in flight' but then takes the full scan; the natural second Ctrl-C wedges the scanner.
- With --no-library, Ctrl-C during a scan.py bracket never stops it; every remaining pass is taken.
- A full disk, a vanished NAS or an unwritable --library during scan_roll.py loses every later frame (entry and frameNN.tif), while the tool prints a success line per frame for hours.
- A failed library.save in scan.py (full disk, --library naming a file) loses every pass of the run and the delivered file, with a traceback.
- With RPS7200_DEBUG=1, a pass the tool claimed is deleted from the debug spool before the tool's own save, so a failed save loses it anyway.
- Resuming with `--start-at N` as the tool advises re-scans every frame after N that was already done, duplicating library entries and overwriting frameNN.tif, for hours.
- Resuming with different --dpi/--ir/--film silently mixes settings in one roll and overwrites the recorded settings.
- `--approved WALK` without --film meters a slide or B&W walk as colour negative; the exposure is baked into the raw data.
- Running scan.py without --auto-exposure (the README's headline example) scans at the device's low default exposure, unlike scan_roll and the window.
- A blank frame mid-strip ends a `--frames 36` roll early with exit 0 and no warning.
- `scan.py --library ''` files into the current directory (and writes index.json there) instead of skipping, as the same flag does in scan_roll.py.
- Running the tools from a directory other than the repo root creates a separate library/, rolls/ and calibration/ there; --reuse then misses the cache.
- The default --out scan.tif and its scan.json sidecar are overwritten without a prompt on every run.
- A re-walk (--dry-run) into an existing folder overwrites prescanNN.tif (the --approved references) with no backup; survey.json.bak keeps only one generation.
- A Ctrl-C during a scan_roll rewind or seek still calibrates for 3-4 minutes, and on a dry run replaces survey.json with an empty walk.
- --nudge takes millimetres, while the window and CLAUDE.md use adjustment units (about 9.5x apart).
- A mistyped resolution (e.g. --dpi 5000 --no-shading) is sent to the device unvalidated.
- filing_load_test.py has no Ctrl-C protection, and `--rounds 0` crashes after warming the lamp.
- `duplicates --delete` can delete the intact twin when the kept one's files are damaged.

## Dataflow notes

**tools/scan.py (main, 71-436)**
- Parse arguments, then validate before the device opens: supports_infrared, bracket range, dpi > 0, _Driver.correctable_at, --out suffix, stops, bracket+ir, exposure-scale count. Print say_estimate (session.estimate_seconds, 39-64).
- Inside DeferredInterrupt + DirectScanner(debug=None, from RPS7200_DEBUG):
  - inquiry.
  - ensure_shading (direct.py:806): either load_shading, or calibrate_shading(keep_data), then archive_calibration to calibration/<UTC>/, then save_shading to the cache.
  - scan() or scan_bracket(on_pass). Each DirectScanner.scan (2842): metering probes (their own passes, spooled if debug), then commands logged by _CommandLog, then _read_pass/read_planes. read_planes keeps last_raw plus last_raw_layout when keep_raw or debug, then decode_index (upright by line tags), then the 7200 stagger realign, then apply_shading (the returned image is corrected), then meta (commands, shading_origin, carriage_state, read_direction), then _debug_capture(raw_pixels) and last_pixels_raw.
  - hold() (280) pairs last_pixels_raw with capture_record() (reference, ccd_mask, raw bytes, layout), calls debug_claim, and appends to the in-memory `pending`.
- After the with block: close(), then _debug_flush files unclaimed passes (probes) to RPS7200_DEBUG_ROOT or ./library. Then library.save for each pending pass (366-375: raw pixels as scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, scan.json). Then merge_bracket (corrected frames, judged on raw sensor frames or rails), then to_monochrome, then export.write(--out) and the sidecar JSON.

**tools/scan_roll.py (main, 309-846)**
- Parse and validate. hold_from_walk (223): walked_prescans + preview.unorient on the walk's corrected prescanNN.tif, then frame_edges.propose_centred, then Approved references.
- Start FrameWriter (daemon thread). Inside DeferredInterrupt + DirectScanner:
  - inquiry, wait_warm, session.rewind, session.seek (FilmNotPlaced refuses), plan_nudges + nudge (mm), calibrate (ensure_shading).
  - mkdir the roll folder, earlier_manifest / keep_first_numbering / renumbered carry-forward, RollManifest.write.
  - DirectScanner.scan_roll generator (3765). Per frame: prescan (corrected; raw kept in last_pixels_raw), frame_contrast (blank ends the roll), registration, _hold_to_approved or _aim_frame (nudge + prescans), metering, scan.
  - It yields RollFrame(image corrected, raw_image, prescan corrected, raw_prescan, prescan_meta, meta with registration).
- Dry-run branch: tiff.write prescanNN.tif on the main thread; raw_bytes_disagree guard; debug_claim; writer.submit (prescan filed raw, no delivered path).
- Scan branch: debug_claim(raw_image); writer.submit(image, raw_image, prescan = the corrected one, capture = capture_record()).
- FrameWriter._write (session.py:1618): library.save(raw) first, then export.write(frameNN.tif, corrected), then on_filed, then RollManifest.filed (done / filing_error).
- After the with block: close() and the debug flush, writer.finish, manifest finish and save, summary, exit code (1 on trouble, failures or an unsaved manifest).

**tools/library.py**
- Offline over library.entries(root).
- reconstruct: read_raw, then decode_index, then (for legacy labelled entries) re-apply shading, then compare with tiff.read(scan.tif); verdict mapped to marks and the exit code at 145-172.
- verify: sha256 and completeness.
- duplicates: signature + same_data, then rmtree.
- migrate-raw: decode_raw, then .scan.tif.part, then os.replace with the old file kept as KEPT, then an atomic record update.
- migrate-direction, tag (atomic add_tags), reindex.

**tools/make_comparison.py**
- library.load gives raw scan.tif; library.corrected gives today's apply_shading with the entry's own reference and mask. Both go through export.write (1_, 2_, 3_ inverted via preview.normalise), are read back from disk for worst_colour, and PNG previews are written.

**tools/filing_load_test.py**
- DirectScanner, wait_ready/wait_warm, then alternating 300 dpi 8-bit shading=False full-frame scans, with and without a Grinder thread gzipping a random payload, then a paired verdict. It files nothing itself; only debug filing does.

**tasks.py / Makefile**
- Each make target is one `uv run python tasks.py <target>`; tasks.py runs the pinned tools from the venv (tool()) without a shell. run, run-demo and run-sheet launch tools/gui.py (run-sheet with --demo --look-only --open-roll rolls/aligned-strip). reconstruct and verify call tools/library.py.
- CI (.github/workflows/test.yml) runs lint, type (ty excludes tools/ and tests/), test and test-no-tifffile on three OSes; it never runs the operator tools against a library.
