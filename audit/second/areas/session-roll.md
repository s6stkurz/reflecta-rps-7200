# ScanSession, FrameWriter and rolls

Area key `session-roll`. 22 findings: 4 high, 5 medium, 12 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

rps7200/session.py is the UI-side scan engine. `ScanSession` owns one daemon worker thread that drives the scanner, and `FrameWriter` owns a second daemon thread that files each picture (library entry first, then the delivered copies). `RollManifest` is the single, lock-guarded writer of roll.json and survey.json, and it marks a frame `done` only once the writer has filed it. Around these sit the roll-folder helpers (roll_dir, new_roll_name, write_manifest/read_manifest with .bak, .legacy and .unreadable), `seek`/`rewind`, the legacy renumbering (`renumbered`) and the nudge planner. Since the first audit a lot has been hardened: numbering by place on the strip, atomic manifests, `done` only after filing, carrying a live manifest across back-to-back rolls, stopping a roll when filing fails, and filing single passes plain until close. Seven problems remain. (1) Debug-filing claims are made when a pass is handed to the writer and are honoured by a flush that runs before the writer has finished, so a pass whose filing fails is also deleted from the debug spool. (2) Nothing waits for the writer when a roll ends. The window keeps the device open, so the last roll frames are gzipped and deflated while the scanner sits open and idle, the state CLAUDE.md names as preceding a wedge. The output-folder copy of a single pass is also deflated or JPEG-encoded in that state, contrary to the code's own comment. (3) Outside debug mode, a real roll's prescans reach the library only as a corrected, unlabelled prescan.tif. Their raw pixels, the pre-correction prescan and a failed frame's prescan are dropped. (4) A library write failure skips every delivered copy, so the frame is lost entirely. (5) A walk frame whose aim or hold loop fails part-way can be filed with the raw bytes of a later verification pass. (6) Legacy walks that get renumbered and are then walked again can have another frame's prescanNN.tif overwritten. (7) Ctrl-C in the terminal that launched the GUI kills both daemon threads mid-read. I found no `if demo` branch above the `_open_scanner` seam. The session reaches the stand-in only by duck-typed getattr.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [SR-01](#session-roll-sr-01) | high | data-integrity | confirmed | Debug-filing claim is made at hand-off, and the spool is flushed before the writer finishes: a pass whose filing fails is deleted from the debug spool too |
| [SR-02](#session-roll-sr-02) | high | hardware-safety | confirmed | The last frames of every roll are gzipped and deflated with the device open and idle; nothing waits for the writer at the end of a roll |
| [SR-04](#session-roll-sr-04) | high | library-completeness | confirmed | Outside debug mode, a real roll keeps no raw data for its prescans: raw_prescan and prescan_before are dropped, the stored prescan.tif is corrected and unlabelled, and a failed frame's prescan is not filed |
| [SR-19](#session-roll-sr-19) | high | hardware-safety | confirmed | Ctrl-C in the terminal that launched the window, or closing that terminal, kills the daemon scanner and writer threads mid-read |
| [SR-03](#session-roll-sr-03) | medium | hardware-safety | confirmed | Single scans and prescans are said to be written uncompressed while the device is open, but the output-folder copy is deflated or JPEG-encoded, and the entry's shading.npz is compressed |
| [SR-05](#session-roll-sr-05) | medium | data-integrity | confirmed | When library.save fails, no delivered copy is attempted, so the frame is lost entirely |
| [SR-06](#session-roll-sr-06) | medium | data-integrity | confirmed | A walk frame whose aim or hold fails part-way is filed with the raw bytes of a later verification prescan |
| [SR-07](#session-roll-sr-07) | medium | data-integrity | confirmed | A renumbered legacy walk keeps its old prescanNN.tif names, and walking frames again overwrites files that other frames' records still point to |
| [SR-10](#session-roll-sr-10) | medium | data-integrity | confirmed | After force_abort the scanner is never closed, so the debug spool is never filed and its location is never reported |
| [SR-08](#session-roll-sr-08) | low | data-integrity | confirmed | Roll-folder TIFFs are overwritten in place, non-atomically, on a rescan or re-walk, and roll.json forgets the earlier take |
| [SR-09](#session-roll-sr-09) | low | bug | confirmed | On a corrected walk with an output folder set, the pre-correction prescan overwrites the frame's final prescan in out_dir/prescans |
| [SR-11](#session-roll-sr-11) | low | error-handling | confirmed | A scanner that opens but fails INQUIRY is left open with its interface claimed until the process exits |
| [SR-12](#session-roll-sr-12) | low | error-handling | confirmed | Deliberately unfiled pictures are logged as 'could not be filed: None' |
| [SR-13](#session-roll-sr-13) | low | concurrency | confirmed | A stale `_current` lets a late filing failure from a finished roll stop and drop jobs queued afterwards |
| [SR-14](#session-roll-sr-14) | low | user-error | confirmed | Roll settings the driver will refuse are checked only after the seek has moved the film and the folder exists; at 7200 dpi three frames are prescanned, metered and passed before the roll gives up |
| [SR-15](#session-roll-sr-15) | low | bug | confirmed | A scan can be judged against the prescan of a different picture: two unknown positions compare equal, and `_last_prescan` never expires |
| [SR-16](#session-roll-sr-16) | low | doc-mismatch | confirmed | Nudge docs still describe the param-8 transport law, and MAX_FINE_STEPS now allows about two frames of chained sub-frame travel |
| [SR-17](#session-roll-sr-17) | low | library-completeness | confirmed | Sub-frame SLIDE commands sent by holds, aims and the window's nudges are recorded only as millimetres derived from today's law; the param bytes are never persisted |
| [SR-18](#session-roll-sr-18) | low | library-completeness | confirmed | The session's roll.json and survey.json do not say how a run ended, and differ from the tool's format for the same files |
| [SR-A1](#session-roll-sr-a1) | low | data-integrity | found-by-verifier | A hold or aim that raises part-way loses every record of the SLIDE moves it already sent, and the walk's travel budget never counts them |
| [SR-A2](#session-roll-sr-a2) | low | hardware-safety | found-by-verifier | tools/scan_roll.py writes walk prescans with TIFF deflate inline on the scanning thread, with the device open |
| [SR-20](#session-roll-sr-20) | info | design | confirmed | The FrameWriter thread has no liveness check; if it ever died, submit and finish would block forever with the device open |

## Findings in full

<a id="session-roll-sr-01"></a>

### SR-01 -- Debug-filing claim is made at hand-off, and the spool is flushed before the writer finishes: a pass whose filing fails is deleted from the debug spool too

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2869-2876`, `rps7200/session.py:2833-2847`, `rps7200/session.py:1968-1979`, `rps7200/session.py:1587-1600`, `rps7200/direct.py:1000-1016`, `rps7200/direct.py:1052-1059`, `rps7200/direct.py:1134-1145`, `tools/scan_roll.py:675`, `tools/scan_roll.py:730`, `tools/scan_roll.py:803`

**Doc claim:** CLAUDE.md:167-171 says the window and tools 'claim each of those passes ... so with it on, debug filing adds only the passes they do not keep ... and files nothing twice'. Nothing is filed twice, but a pass can be filed zero times.

With RPS7200_DEBUG=1, the mode CLAUDE.md makes mandatory for Claude, the spool is the backup copy of every pass. The session tells the driver 'I file this one' when it queues the job, not when library.save succeeds. It then closes the scanner, which flushes and deletes every claimed spool, before the writer has drained. Any pass the FrameWriter failed to file during the session, or had not yet filed at close and then fails, is gone from both places. The claim also fires when the session knowingly files an entry without raw bytes, which discards the one copy that had them. The flush also calls library.save/reindex while the FrameWriter thread is still filing, so index.json is written by two threads without atomicity.

**Evidence (from the code):**

```text
session.py:2872-2876 `claim = getattr(self._scanner, "debug_claim", None)` / `if (raw_image is not None and file_entry and self.root is not None and callable(claim)): claim(raw_image)` / `self._writer.submit(...)`. The claim is made before the writer has done anything. session.py:1973-1979: `if not self.dead: self._scanner.close()` ... `self._writer.finish()`. DirectScanner.close() runs `self._debug_flush()` (direct.py:1145), which for a claimed item does `stuck += self._debug_unlink(item); continue` (direct.py:1055-1059), deleting the spooled image, raw bytes, meta and mask. A writer failure is only collected: `except Exception as exc: self.errors.append(...)` (session.py:1594-1598). The claim is also made after `raw_bytes_disagree` has dropped the capture's bytes (`capture = dict(capture, raw=None, raw_path=None, raw_layout=None)`, 2847), so an entry filed without bytes still releases the spool that held them. tools/scan_roll.py has the same shape: `s.debug_claim(...)` before `writer.submit`, and `with DirectScanner(...) as s` exits (close, then flush) before `writer.finish()` at 803.
```

**Failure scenario:** The library disk fills during a roll with RPS7200_DEBUG=1. library.save raises in FrameWriter._write for frame 12, `_filed` stops the roll, and the frame in flight fails the same way. At session close `_scanner.close()` runs `_debug_flush`, sees both passes claimed, and unlinks their spooled image.npy and raw.bin. Both frames' raw bytes and pixels no longer exist anywhere, although debug filing exists precisely to keep them.

**Fix:** Claim a pass only once it is filed: call debug_claim from the writer's success path, or pass the claim callback in the job and invoke it after library.save returns. Never claim when raw bytes were dropped. In `_run`, finish the writer (without compressing: compress=False for anything left) before `_scanner.close()`, or make `_debug_flush` skip claimed items whose filing has not been confirmed. Apply the same change in tools/scan_roll.py. Add a test that a failed library.save leaves the pass in the debug library.

<details><summary>Second reader's check</summary>

The code does what the finding says. _file calls `claim(raw_image)` (session.py:2872-2875) before `self._writer.submit(...)`, so the claim comes before anything is filed. It also fires after `raw_bytes_disagree` has dropped the capture's bytes (2847): the guard only nulls `capture` and never stops the claim. In `_run`'s finally, `self._scanner.close()` (1974) comes before `self._writer.finish()` (1979). DirectScanner.close() calls `_debug_flush()` (direct.py:1145), and for a claimed item that runs `stuck += self._debug_unlink(item); continue` (1055-1059), which deletes image.npy, raw.bin, meta.json and ccd_mask.bin. FrameWriter._run only appends a save failure to `errors` (1594-1598). So a pass whose library.save fails, whether earlier in the session or after close, survives nowhere. The worst case is the SR-06 walk error path: the entry gets the wrong pass's bytes, which have the same shape, and the claim deletes the spool holding the right ones. tools/scan_roll.py has the same order: it claims before `writer.submit`, and `with ... DirectScanner(...) as s` exits before `writer.finish()` at 803. The two threads calling `reindex` at once (flush on the worker thread, the writer still filing) is also real, but index.json is derived. Loss needs debug on plus a failed save, which is narrow, but it defeats the one backup that exists for exactly that case.

</details>

<a id="session-roll-sr-02"></a>

### SR-02 -- The last frames of every roll are gzipped and deflated with the device open and idle; nothing waits for the writer at the end of a roll

**Severity** high · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/session.py:2880-2887`, `rps7200/session.py:1551-1568`, `rps7200/session.py:1947-1967`, `rps7200/session.py:2692-2701`, `rps7200/library.py:267-304`, `tools/scan_roll.py:792-803`, `tests/test_session.py:1132-1158`

**Doc claim:** session.py:1554-1558 (FrameWriter docstring: 'busy rather than idle throughout') and CLAUDE.md:194-198 ('gzips each frame on its own thread *while the next one scans* ... the device is busy'). Neither holds for the final frame.

The roll exception in CLAUDE.md rests on the device being busy with the next frame while a frame compresses. The last frame has no next frame, and at 3600 dpi RGBI its entry is about 142 MB of pixels plus about as much raw data. That is the '140 MB library entry' CLAUDE.md:423 says preceded a wedge. The same idle-with-gzip state also arises mid-roll whenever the bounded queue (depth 2) fills and `submit` blocks the scanner thread between frames, for example with a slow output drive. It arises at the end of every walk as well, though the prescans there are small.

**Evidence (from the code):**

```text
session.py:2883-2887: `# A roll's frames keep compressing on the writer thread while the next frame scans: the device is busy there` / `compress=bool(roll),`. FrameWriter docstring (1554-1558): 'On this thread the write instead overlaps the next frame's scan, so the device is busy rather than idle throughout.' When `_roll` returns (2701), `_run` emits finished/idle (1962-1967) and blocks on `self._jobs.get()` (1949) with the device open. The GUI holds the session open for its whole life. The writer meanwhile runs library.save(compress=True): tiff deflate of scan.tif and prescan.tif, then `gzip.open(..., compresslevel=6)` over the raw bytes (library.py:283-303), plus deflate of rolls/frameNN.tif and the out_dir copy. By contrast, tools/scan_roll.py closes the device before `writer.finish()` (792-803). The only ordering test covers shutdown after one Scan (test_session.py:1132-1158).
```

**Failure scenario:** The operator commissions a 3600 dpi RGBI roll from the contact sheet. The last frame is yielded, `_roll` returns, and the window shows 'idle'. For the next 20-60 s the writer thread deflates a 142 MB TIFF and gzips 142 MB of raw bytes while the device sits open and idle. This is the host state recorded before the earlier wedge, and it recurs at the end of every roll.

**Fix:** At the end of `_roll`, wait for the writer queue to drain before returning, and do not accept the next job until it has. The device then cannot be open and idle while anything compresses, which is how the tool behaves. Better still, file a roll's last frame (or every frame queued after the final yield) with compress=False and let `library.compact` handle them at close, as single passes already do. Add a test that no compression runs after a roll's final yield while the scanner is open.

<details><summary>Second reader's check</summary>

`compress=bool(roll)` (2887) makes every roll frame gzip plus deflate on the writer thread. `_roll` returns right after the loop (2701) without waiting for the writer; ScanSession has no queue.join or similar (grep for `_writer` shows only submit, finish and notes). `_run` then emits finished and idle and blocks on `self._jobs.get()` with the device open. The window keeps the session open for its whole life. So the last frame's entry, and any backlog, is compressed with the device open and idle. The FrameWriter docstring's 'busy rather than idle throughout' (1554-1558) is false for the final frame. The tool closes the device first (scan_roll.py:792-803). The `_roll` comment at 2338-2341 even says that waiting for the writer was removed so as not to hold the device idle, but it did not add anything that stops compression after the final yield.

</details>

<a id="session-roll-sr-04"></a>

### SR-04 -- Outside debug mode, a real roll keeps no raw data for its prescans: raw_prescan and prescan_before are dropped, the stored prescan.tif is corrected and unlabelled, and a failed frame's prescan is not filed

**Severity** high · **Category** library-completeness · **Verdict** confirmed

**Where:** `rps7200/session.py:2527-2587`, `rps7200/session.py:2594-2595`, `rps7200/session.py:2620-2637`, `rps7200/library.py:269-270`, `rps7200/library.py:376-382`, `rps7200/direct.py:4031-4036`, `rps7200/direct.py:4137-4141`, `rps7200/direct.py:4152-4156`, `rps7200/session.py:2574-2587`, `tools/gui.py:5790-5796`, `tests/test_session.py:828-833`

**Doc claim:** CLAUDE.md:116 ('The library holds raw pixels; everything else is corrected') and CLAUDE.md:140-144 (an entry holds 'a prescan.tif where there was one'), which does not say that this prescan.tif is corrected and irreproducible.

The owner requires exact bit data for every scan. A real roll's prescan is the evidence for registration, holds and aim. `RollFrame` carries `raw_prescan` exactly so the library can store raw pixels, and the session discards it on every real roll. What survives is a corrected prescan.tif inside the frame entry that cannot be re-decoded or re-corrected (its own 300 dpi CCD mask is not stored) and is not marked as corrected. The picture before a hold or aim moved the film is not kept at all on a real roll. With RPS7200_DEBUG off, the operator's default, a real roll files only its frame scans. A roll from the Roll button with no prior walk therefore has no raw prescan anywhere.

**Evidence (from the code):**

```text
Only the walk branch uses the raw prescan: `if job.dry_run:` ... `raw_image=rf.raw_prescan,` (2536-2566), and `if rf.prescan_before is not None:` is inside that branch (2567). A real roll files the frame with `prescan=rf.prescan,` (2627), the corrected 8-bit prescan. library.save writes it as `tiff.write(str(path / "prescan.tif"), prescan, ...)` (library.py:270), and its record holds only `{"file", "read_direction", "carriage_state"}` (376-382): no corrections_applied, no raw, no own CCD mask. A failed frame only gets `if rf.error: self._emit("log", ...)` (2594-2595), so its prescan is never filed. On the hold path the driver replaces `prescan_image = fix["prescan"]` without keeping a before-picture (direct.py:4031-4036). On a walk, the before-picture is written `file_entry=False` (2586), so it has no entry, and the session's survey record never names it, whereas the tool's does (`record["prescan_before"] = was.name`, scan_roll.py:694). As a result gui.carry_walk, which copies only files named in the records (gui.py:5792), leaves it behind. test_a_real_roll_does_not_file_its_prescans_separately asserts the current design.
```

**Failure scenario:** The operator runs a 20-frame roll from the Roll button with 'correct' ticked and debug off. Frame 7's aim moves the film, and frame 9 fails with a ScanReadError after its prescan. Afterwards the library has, for frame 7, a corrected post-aim prescan.tif and nothing of the pre-aim picture; for frame 9 it has nothing. A later question such as 'did the aim make frame 7 worse?' or 'what did frame 9 look like?' cannot be answered, and none of those prescans can be re-decoded with newer code.

**Fix:** On a real roll, file each frame's prescan as its own entry with `raw_image=rf.raw_prescan`, capturing the prescan's bytes, mask and meta when it is taken (carry `raw_prescan_bytes`/`layout`/`ccd_mask` on RollFrame, since `capture_record()` at yield time describes the frame scan). Tag it `roll_membership(kind="prescan")`, as walks do. Or at least pass the raw prescan to library.save and label a corrected prescan.tif in the record. File a failed frame's prescan. Carry the before-picture's raw pixels, and name `prescan_before` in the session's survey records.

<details><summary>Second reader's check</summary>

In `_roll`, prescans are filed as their own entries with `raw_image=rf.raw_prescan` only under `if job.dry_run:` (2536-2587). A real roll passes `prescan=rf.prescan` into the frame's entry (2627). That is the corrected pass: the driver calls `self.prescan(..., shading=shading)` (direct.py:3968). library.save writes it as prescan.tif, and its record holds only `file`, `read_direction` and `carriage_state` (library.py:376-382): no corrections label and no raw bytes or mask of its own. On a real roll a failed frame only produces `self._emit("log", ...)` (2594-2595). prescan_before is handled only inside the dry_run branch, and the real-roll driver path keeps no before-picture on a hold. The session's survey records never name `prescan_before`, while the tool's do (scan_roll.py:694), so gui.carry_walk (5792) does not copy it. test_session.py:828-833 asserts that real-roll prescans are not filed separately. Outside debug mode, none of a real roll's prescans survives in raw form.

</details>

<a id="session-roll-sr-19"></a>

### SR-19 -- Ctrl-C in the terminal that launched the window, or closing that terminal, kills the daemon scanner and writer threads mid-read

**Severity** high · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/session.py:1816-1820`, `rps7200/session.py:1584-1585`, `tools/gui.py:8908-8912`, `tools/scan_roll.py:473-475`, `tools/scan.py:253`

**Doc claim:** CLAUDE.md:413-431 ('Never abandon a read mid-scan'); session.py:18-24 (only request_stop and force_abort are described as ways to end a job)

The window's orderly quit (on_close, which stops after the frame in flight and then waits) is the only guarded way out. A Ctrl-C in the launching terminal (`uv run python tools/gui.py`, `make run-demo`), a closed terminal, or a SIGTERM ends the process with the worker blocked in a bulk read and the writer part-way through an entry. That abandons a read mid-scan, which CLAUDE.md lists as the wedge cause. Queued frames are lost, entries are left INCOMPLETE, and single passes are never compacted.

**Evidence (from the code):**

```text
`self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")` (1819) and `self._thread = threading.Thread(target=self._run, daemon=True)` for FrameWriter (1584). gui.py `main()` just does `root.mainloop(); return 0` with no SIGINT handling (grep finds no signal or KeyboardInterrupt handling in gui.py). _tkinter's mainloop checks signals and returns with KeyboardInterrupt, after which the interpreter exits without joining daemon threads. Both CLI tools wrap their runs in `DeferredInterrupt()`.
```

**Failure scenario:** During a 3600 dpi roll the operator presses Ctrl-C in the terminal, thinking it stops the scan. mainloop raises KeyboardInterrupt, the interpreter exits, and the scanner thread dies inside libusb_bulk_transfer. The scanner needs a power cycle, and the frame being filed has an INCOMPLETE directory.

**Fix:** In the window, install a SIGINT/SIGTERM handler (or catch KeyboardInterrupt around mainloop) that routes to `on_close` with 'stop after the frame in flight' and keeps pumping until the session thread has ended, mirroring DeferredInterrupt. Consider non-daemon session threads plus an atexit hook that calls request_stop, shutdown and join.

<details><summary>Second reader's check</summary>

Both threads are daemon threads (1819, 1584). gui.py `main()` ends with `root.mainloop(); return 0` (8911-8912), and gui.py has no signal or KeyboardInterrupt handling (grep finds only a comment). _tkinter's mainloop calls PyErr_CheckSignals between events, so SIGINT can surface as KeyboardInterrupt out of mainloop, and SIGHUP or SIGTERM kill the process outright. Either way the interpreter exits without joining daemon threads, which abandons any bulk read in flight and any entry being written. The CLI tools use DeferredInterrupt. When the interrupt instead lands inside a Tk callback, tkinter's CallWrapper reports it and carries on, so Ctrl-C does not always kill the window, but closing the terminal or SIGTERM does. Launching the GUI from a terminal is the documented route (make run-demo, uv run python tools/gui.py).

</details>

<a id="session-roll-sr-03"></a>

### SR-03 -- Single scans and prescans are said to be written uncompressed while the device is open, but the output-folder copy is deflated or JPEG-encoded, and the entry's shading.npz is compressed

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/session.py:2827-2831`, `rps7200/session.py:2880-2887`, `rps7200/session.py:1673-1690`, `rps7200/export.py:185-193`, `rps7200/tiff.py:97-103`, `rps7200/tiff.py:149-153`, `rps7200/library.py:271-272`, `rps7200/shading.py:91`, `tools/gui.py:2757-2777`, `tools/gui.py:4288-4307`

**Doc claim:** CLAUDE.md:188-191 ('it files a single scan or prescan plain -- raw.bin and uncompressed TIFFs') and session.py:2880-2886 ('compressing then -- gzip, and TIFF deflate -- is what preceded a wedge ... So those are written plain').

When an output folder is set (the window remembers one), every single Scan or Prescan has its delivered copy compressed on the writer thread straight after the pass, with the device open and idle. The code's own comment names this as the hazard it avoids. At 3600 dpi RGBI that is deflate with a predictor over about 142 MB.

**Evidence (from the code):**

```text
session.py:2880-2886: 'A single scan or prescan is filed with the scanner open and idle between jobs, and compressing then -- gzip, and TIFF deflate -- is what preceded a wedge (CLAUDE.md). So those are written plain'. Only `compress=bool(roll)` reaches library.save. The out_dir copy goes through `note = export.write(str(path), delivered, ...)` (1682), which ends in `tiff.write(str(path), image, resolution=resolution)` (export.py:192) with the default `compress: bool = True`, i.e. `kwargs["compression"] = "zlib"; kwargs["predictor"] = True` (tiff.py:149-153). The JPEG path encodes with `optimize=True` and writes a DNG beside it. library.save(compress=False) still calls `reference.save(path / "shading.npz")`, which is `np.savez_compressed` (shading.py:91). The window's Export and Save-all threads (gui.py:2757-2777, 4288-4307) likewise deflate or encode full-resolution files while the session holds the device open.
```

**Failure scenario:** The operator scans one 3600 dpi RGBI frame from the window with an output folder set. While the device sits open and idle between jobs, the writer thread spends several seconds zlib-compressing the 142 MB delivered TIFF. This contradicts the comment and CLAUDE.md, which say nothing is compressed in that state.

**Fix:** Write the out_dir copy uncompressed (`tiff.write(..., compress=False)`) while the session is open and recompress it at close alongside `library.compact`, or defer the delivered copy of single passes to close. Save shading.npz uncompressed in the compress=False path. Block Export/Save-all while the session holds the device, or let them run only after close. Test that no zlib compression runs for a single pass while the scanner is open.

<details><summary>Second reader's check</summary>

`_file` appends an out_dir path for every Scan and Prescan (2827-2831). FrameWriter._write calls `export.write(str(path), delivered, ...)` (1682), which calls `tiff.write(str(path), image, resolution=resolution)` with the default `compress=True`, i.e. zlib plus predictor (tiff.py:149-153), or a JPEG plus DNG. That happens straight after a single pass, with the device open and idle, which contradicts the comment at 2880-2886 and CLAUDE.md's 'files a single scan or prescan plain'. library.save(compress=False) still writes `reference.save(...)`, which is `np.savez_compressed`, though that is small. The window's Export and Save-all threads (gui.py:2757-2777, 4288-4307) also run while the session holds the device. Deflate is less costly than gzip over the raw bytes, and when tifffile is absent the built-in TIFF writer is uncompressed, so medium is right.

</details>

<a id="session-roll-sr-05"></a>

### SR-05 -- When library.save fails, no delivered copy is attempted, so the frame is lost entirely

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1632-1668`, `rps7200/session.py:1673-1694`, `rps7200/session.py:1587-1600`, `rps7200/session.py:2003-2015`

The comment at 1632-1636 handles the opposite failure, a copy failing before the entry, by writing the entry first. It does not handle the entry failing. Then the corrected picture, which could often still reach another volume (an out_dir on a different drive) or survive a transient Windows sharing violation, is thrown away with the raw one. Nothing is spooled or retried. A half-written entry directory with INCOMPLETE is left behind.

**Evidence (from the code):**

```text
`entry = library.save(...)` (1656) runs first, with no try. If it raises, `_write` exits before `for path in job.get("paths") or ():` (1673), so neither rolls/<roll>/frameNN.tif nor the out_dir copy is written. `_run`'s handler only records `self.errors.append(f"picture {job['number']}: {exc}")` and calls `on_done(..., None, str(exc))` (1594-1598), and `_filed` then stops the roll after the frame in flight (2005-2014), which goes the same way.
```

**Failure scenario:** The library sits on an internal disk that fills up, and the output folder is on an external drive with space. In a 3600 dpi roll, library.save for frame 15 raises ENOSPC while writing raw.bin.gz. frame15 is written neither to rolls/ nor to the output folder, the roll stops, and the frame being scanned meets the same fate. Two frames, and about 7 minutes of scanner time, leave no usable file.

**Fix:** Wrap library.save in its own try, still attempt the delivered copies, and report a combined failure. On a library failure, spool the raw pixels and bytes (uncompressed) to a recovery folder or keep them for a retry at close, rather than dropping the job.

<details><summary>Second reader's check</summary>

`entry = library.save(...)` (1656) is not wrapped in a try. An exception leaves `_write` before the loop over `paths` at 1673, so neither rolls/…/frameNN.tif nor the out_dir copy is attempted. `_run`'s except only records the error and calls on_done/_tell (1594-1599). `_filed` then stops the roll (2005-2014). library.save leaves the directory with INCOMPLETE in it (263, 398). The scenario of a full library disk with the out_dir on another volume is plausible.

</details>

<a id="session-roll-sr-06"></a>

### SR-06 -- A walk frame whose aim or hold fails part-way is filed with the raw bytes of a later verification prescan

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2549-2566`, `rps7200/session.py:2832-2847`, `rps7200/direct.py:3967-3976`, `rps7200/direct.py:3368-3380`, `rps7200/direct.py:4147-4156`, `rps7200/direct.py:1882-1896`

RollFrame carries raw pixels but not raw bytes, so the session pairs a frame's pixels with `last_raw` at filing time. In a walk with correct (or a hold), if a later SLIDE or prescan in the loop raises after at least one verification prescan has completed, the frame is yielded with its first prescan's pixels and meta while `last_raw` holds the verification pass's bytes. Both are 300 dpi RGB prescans of identical shape, the case the `file_entry=False` docstring (2802-2811) admits the guard cannot see. With debug on, SR-01 then deletes the correct bytes as claimed.

**Evidence (from the code):**

```text
The pixels come from the frame: `raw_image=rf.raw_prescan` (2563). The bytes come from whatever the scanner last read: `capture = self._scanner.capture_record()` (2832) returns `"raw": self.last_raw` (direct.py:902). The guard `raw_bytes_disagree(image.shape, capture.get("raw_layout"), meta)` compares only lines, width and channels. In scan_roll's error path the frame is yielded with the original prescan: `yield RollFrame(index, position, None, {}, prescan_image, marks, error=str(exc), raw_prescan=raw_prescan, ...)` (4152-4156). `prescan_image` and `raw_prescan` are replaced only after `fix` returns (4031-4036, 4057-4067), whereas every successful verification prescan inside `_hold_to_approved` (3377-3378) has already rebound `last_raw`.
```

**Failure scenario:** On a walk with 'correct', frame 4's aim moves the film once and the verification prescan P2 succeeds. The second nudge then gets a CheckCondition. scan_roll yields frame 4 with error set and prescan P1, and the session files an entry whose scan.tif holds P1's raw pixels and whose raw.bin.gz holds P2's bytes. `library reconstruct` reports a changed decode, and P1's own bytes are gone.

**Fix:** Carry each prescan's raw bytes and layout (and mask) on the RollFrame alongside raw_prescan, set at the moment the pass is taken, and file from those, never from capture_record() at yield time. Failing that, compare a hash of the decoded bytes against the pixels before filing, or drop the bytes whenever the frame's prescan is not the scanner's last pass.

<details><summary>Second reader's check</summary>

`_hold_to_approved` runs nudge then prescan in a loop, with no try (direct.py:3368-3380), so a UsbError or CheckCondition from a second nudge or prescan propagates to scan_roll's except (4147-4156). That except yields `RollFrame(index, position, None, {}, prescan_image, marks, error=..., raw_prescan=raw_prescan, prescan_meta=prescan_meta)`, still P1's pixels, because they are replaced only after `fix` returns (4031-4036, 4057-4067). On a walk the session files error frames that carry a prescan (the `if rf.prescan is not None` / `if job.dry_run` branch runs regardless of rf.error), with `capture = self._scanner.capture_record()` (2832), which holds the last pass's `last_raw`, i.e. verification prescan P2's bytes. `raw_bytes_disagree` compares only shape and layout, which are identical. The entry pairs P1's pixels with P2's bytes, and with debug on the claim then deletes P1's correct spool (SR-01). This applies to walks only: on a real roll, error frames are not filed.

</details>

<a id="session-roll-sr-07"></a>

### SR-07 -- A renumbered legacy walk keeps its old prescanNN.tif names, and walking frames again overwrites files that other frames' records still point to

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2347-2370`, `rps7200/session.py:583-601`, `rps7200/session.py:2549`, `rps7200/session.py:2584`, `rps7200/session.py:2667-2668`, `rps7200/session.py:641-669`, `tools/gui.py:2351`

**Doc claim:** session.py:1440-1444 (Roll.extend_walk: 'A frame walked again replaces its own record and prescan'). A different frame's prescan can be replaced too.

A pre-'numbering: strip' walk that did not start on frame 1 named its files by its own count. Once renumbered, frame N's record points at prescan{N-shift}.tif. A later walk into the same folder (extend_walk, or walking frames again from the sheet) writes prescan{M}.tif for its own strip numbers M. Any M equal to the file index of a legacy record that is not itself walked again overwrites that record's picture. The survey then shows another frame's photograph under that number, and the positions set on the sheet and the hold references taken from it are wrong. Such legacy walks often never filed their prescans in the library, so the overwritten file can be the only copy.

**Evidence (from the code):**

```text
`renumbered` rewrites only `record["number"]` and `record["index"]` (585-601). A legacy record's `"prescan": "prescan01.tif"` survives unchanged while its number becomes, say, 5. An extending walk (`carried = not job.dry_run or job.extend_walk`, 2330) carries those records forward and writes new files at `surveyed = out / f"prescan{number:02d}.tif"` (2549), in place and with no `_unclaimed`. walked_prescans resolves pictures through `record.get("prescan")` (661-668).
```

**Failure scenario:** rolls/2026-09-23 holds a legacy walk that started on counter 4 (prescan01..06 for strip frames 5..10). The operator opens it and extends it backwards to frames 1..4. The session writes prescan01..04.tif over the files that the records for frames 5..8 still name. The sheet now shows pictures of frames 1..4 as frames 5..8, and approving positions there holds the roll to the wrong photographs.

**Fix:** When carrying a legacy manifest forward, rename its prescan files to their strip numbers first (or give new walks a naming scheme that cannot collide, e.g. a walk id), and refuse to write a prescan file that another carried record names. Never write a roll-folder TIFF over a file some other record references.

<details><summary>Second reader's check</summary>

`renumbered` rewrites only `record["number"]` and `record["index"]` (session.py:585-601) and leaves `record["prescan"]` as it was. An extending walk (`carried = not job.dry_run or job.extend_walk`, 2330, which the GUI sets via `extend_walk=keep`, gui.py:2351) carries the legacy records forward and writes `out / f"prescan{number:02d}.tif"` (2549) without checking whether another record names that file. walked_prescans resolves pictures through `record.get("prescan")` (661-668), so a legacy record whose file index differs from its strip number ends up pointing at a picture of another frame. This needs a pre-'strip'-numbering walk that did not start on frame 1 and then an extension of it, which is narrow but reachable. The `walked_prescans` docstring itself describes such a folder.

</details>

<a id="session-roll-sr-10"></a>

### SR-10 -- After force_abort the scanner is never closed, so the debug spool is never filed and its location is never reported

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1855-1875`, `rps7200/session.py:1968-1976`, `rps7200/direct.py:1134-1145`, `rps7200/direct.py:922-928`, `rps7200/usb_transport.py:558-567`

force_abort is the path most likely to be taken after something went wrong, and it is exactly when the metering probes, hold/aim prescans and unfiled frames in the debug spool matter most. Skipping close() leaves every spooled pass in an anonymous temp directory: not filed, not deleted, and never mentioned in the log. OS temp cleaners may remove it.

**Evidence (from the code):**

```text
session.py:1973-1974 `if not self.dead: self._scanner.close()`. DirectScanner.close() is the only caller of `self._debug_flush()` (direct.py:1145). The spool is `tempfile.mkdtemp(prefix="rps7200-debug-")` (direct.py:926-928). A second close of the transport is harmless: `if self._handle:` / `if self._ctx:` (usb_transport.py:559-566).
```

**Failure scenario:** The operator runs with RPS7200_DEBUG=1, a roll stalls, and they force-abort as instructed. The window closes. The session's hold prescans and metering probes stay in /tmp/rps7200-debug-xxxx, never reach the library, and nothing says where they are.

**Fix:** After a force abort, skip only the device commands: call `self._scanner._debug_flush()` (or close(), which is idempotent on the transport) after the writer has finished, and log the spool path if anything fails.

<details><summary>Second reader's check</summary>

`if not self.dead: self._scanner.close()` (1973-1974) skips close after force_abort. close() is the only caller of `_debug_flush()` (direct.py:1145), so the spool in `tempfile.mkdtemp(prefix="rps7200-debug-")` is never filed or removed, and its path is never logged. The transport's close is guarded (`if self._handle:`), so calling close() or `_debug_flush` after an abort would be safe. The writer.finish and compact steps do still run.

</details>

<a id="session-roll-sr-08"></a>

### SR-08 -- Roll-folder TIFFs are overwritten in place, non-atomically, on a rescan or re-walk, and roll.json forgets the earlier take

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2629`, `rps7200/session.py:2549`, `rps7200/session.py:2584`, `rps7200/session.py:1673-1688`, `rps7200/session.py:1121-1123`, `rps7200/session.py:2961-2973`, `rps7200/session.py:1008-1013`, `rps7200/tiff.py:161-165`

**Doc claim:** session.py:2964-2965 (_unclaimed docstring implies rescans never land on the first attempt's file, but frameNN.tif in rolls/ does)

A resumed roll or a rescanned frame replaces rolls/<roll>/frameNN.tif (the Roll dialog's folder_note does warn about this). A crash or full disk during that write leaves a truncated file where a good one was. roll.json then names only the latest take. The earlier take's library entry still carries the same roll_membership, so two entries claim one frame and the manifest no longer says which is which. Raw data survives in the library, so this is low severity.

**Evidence (from the code):**

```text
`path=out / f"frame{number:02d}.tif"` (2629) is written by `export.write(str(path), ...)`, which calls `tifffile.imwrite(path, ...)` or `open(path, "wb")` directly (tiff.py:161-165). Only the out_dir copy goes through `_unclaimed`, whose docstring reads 'A frame rescanned after a failure would otherwise land on the file the first attempt wrote, and the better of the two is not always the second.' `RollManifest.record` replaces the frame's record: `[f for f in ... if str(f.get("number")) != str(number)] + [record]` (1121-1123), so the first take's `entry` path is dropped. keep_first_numbering copies with `shutil.copyfile(path, legacy)` under `if not legacy.exists()` (1012-1013), so a truncated .legacy is kept for good.
```

**Failure scenario:** The operator rescans frame 5 of a finished roll into the same folder and the disk fills mid-write. frame05.tif, previously a good corrected picture, is truncated, and roll.json's record for frame 5 now carries the new take's filing_error rather than the first take's entry.

**Fix:** Write roll-folder TIFFs beside and rename over (as library._replace_tiff does). Keep earlier takes' entries in the record (e.g. `takes: [...]`) rather than replacing the record. Copy .legacy atomically. Correct the `_unclaimed` docstring to say it covers the output folder only.

<details><summary>Second reader's check</summary>

frameNN.tif (2629) and prescanNN.tif (2549) are written in place through `export.write`, which calls `tiff.write` and then `tifffile.imwrite(path, ...)` or `open(path, "wb")`: no temp file and rename. Only the out_dir path goes through `_unclaimed`. `RollManifest.record` replaces the earlier record for the same number (1121-1123), so the first take's `entry` is dropped from roll.json, although both library entries carry the same roll_membership. keep_first_numbering copies non-atomically under `if not legacy.exists()` (1012-1013). The raw data survives in the library, so low. gui.folder_note does warn that frame files are overwritten.

</details>

<a id="session-roll-sr-09"></a>

### SR-09 -- On a corrected walk with an output folder set, the pre-correction prescan overwrites the frame's final prescan in out_dir/prescans

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:2827-2831`, `rps7200/session.py:2550-2566`, `rps7200/session.py:2574-2587`, `rps7200/session.py:2961-2973`

The file in the operator's prescans folder for every frame the aim moved shows the picture before the correction, under the frame's own name, and nothing says so. The library entry and rolls/prescanNN.tif are unaffected.

**Evidence (from the code):**

```text
`paths.append(_unclaimed(where / self._out_name(number, meta, roll)))` (2831) is evaluated on the scanner thread when the job is submitted. `_unclaimed` returns `wanted` whenever `not wanted.exists()` (2967). The final prescan and `prescan_before` are submitted back to back with the same kind ("prescan"), number, dpi and roll, so both resolve to `{roll}_frameNN_300dpi.tif` before the writer has written either, and the writer writes them in that order.
```

**Failure scenario:** A walk with 'correct' ticked and an output folder set aims frame 3. out_dir/prescans/strip_frame03_300dpi.tif ends up holding the pre-aim picture, and the corrected one written a moment earlier is silently replaced.

**Fix:** Choose the free name on the writer thread at write time (open with O_EXCL or check-and-create there), or give the before-picture its own suffix in `_out_name` (e.g. `-before`).

<details><summary>Second reader's check</summary>

`_unclaimed(where / self._out_name(number, meta, roll))` is evaluated on the scanner thread at submit time (2831). The final prescan and prescan_before are submitted back to back (2549-2587) with the same number, the same meta dpi/channels and the same roll, so both resolve to `{roll}_frameNN_300dpi.tif` in out_dir/prescans. The first is still queued, or sitting in its library.save, when the second is named, so neither exists yet. The writer writes them in queue order, so the before-picture silently replaces the final one.

</details>

<a id="session-roll-sr-11"></a>

### SR-11 -- A scanner that opens but fails INQUIRY is left open with its interface claimed until the process exits

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/session.py:1929-1939`

If the open succeeds but INQUIRY fails (device busy, wedged or answering oddly), the worker thread exits with the libusb handle and interface still claimed. The window reports 'No scanner' and cannot start another session, and no other process (tools/scan.py, check_scanner.py) can claim the device until the window is quit.

**Evidence (from the code):**

```text
`self._scanner.open()` / `info = self._scanner.inquiry()` ... `except Exception as exc: self._emit("failed", text=f"could not open the scanner: {exc}"); self._emit("closed"); return`. There is no `self._scanner.close()` on this path.
```

**Failure scenario:** The scanner is powered on in a bad state and INQUIRY raises. The GUI shows 'could not open the scanner', and running tools/check_scanner.py in parallel to diagnose fails to claim the interface while the window stays open.

**Fix:** Close the scanner in that except branch when open() succeeded (track it in a flag), and log a failure of the close itself.

<details><summary>Second reader's check</summary>

session.py:1929-1939: when `open()` succeeds and `inquiry()` raises, the except branch emits failed/closed and returns without calling `self._scanner.close()`. The session object stays referenced by the window, so the libusb handle and claimed interface persist until the process exits.

</details>

<a id="session-roll-sr-12"></a>

### SR-12 -- Deliberately unfiled pictures are logged as 'could not be filed: None'

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/session.py:2003-2004`, `rps7200/session.py:1637-1638`, `rps7200/session.py:1700-1702`, `rps7200/session.py:2895`

Every prescan_before on a corrected walk produces a log line saying the picture could not be filed, with the reason 'None'. Operators learn to ignore that line, and it is the same line that reports real filing failures.

**Evidence (from the code):**

```text
`if entry is None: self._emit("log", text=f"picture {number} could not be filed: {err}")`. For `file_entry=False` (`library=self.root if file_entry else None`, 2895) or a session with root None, the writer succeeds with `entry=None` and calls `self.on_done(job.get("seq", 0), job["number"], None, None)`.
```

**Failure scenario:** A 16-frame walk with correction moves 9 frames and the log shows 9 'picture N could not be filed: None' lines, although nothing failed.

**Fix:** In `_filed`, log a failure only when `err is not None`. Report a deliberately entry-less write as 'written without a library entry'.

<details><summary>Second reader's check</summary>

For `file_entry=False`, `library=None` (2895), so `_write` leaves `entry=None` and calls `self.on_done(seq, number, None, None)` (1700-1701). `_filed` then logs `picture {number} could not be filed: {err}` unconditionally when entry is None (2003-2004), giving 'could not be filed: None' for every prescan_before on a corrected walk, and for every pass of a session with root None.

</details>

<a id="session-roll-sr-13"></a>

### SR-13 -- A stale `_current` lets a late filing failure from a finished roll stop and drop jobs queued afterwards

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `rps7200/session.py:1952`, `rps7200/session.py:2005-2014`, `rps7200/session.py:1830-1853`

The writer finishes a roll's last frames after `_roll` has returned. If one of those filings fails while the worker is idle, or while it runs a later Roll, the session logs 'stopping the roll after the frame in flight' and drops everything the operator queued since. When the current job is a later roll, it stops that roll for a failure in the earlier one.

**Evidence (from the code):**

```text
`self._current = job` (1952) is never reset after a job ends. `_filed` runs on the writer thread and does `if err is not None and isinstance(self._current, Roll): ... self.request_stop()` (2005-2014). `request_stop` drains every queued job (1839-1848).
```

**Failure scenario:** A roll ends, and the operator queues a walk of the next strip. The previous roll's last frame then fails to file (a transient permission error), and the new walk is stopped after its first frame, or dropped from the queue before it starts, with a message about a roll that is no longer running.

**Fix:** Tag each writer job with the id of the job that queued it, and stop only when that job is still the one running. Clear `_current` when a job ends.

<details><summary>Second reader's check</summary>

`self._current = job` (1952) is never cleared. `_filed` runs on the writer thread and calls `self.request_stop()` whenever `err is not None and isinstance(self._current, Roll)` (2005-2014). A late failure from a finished roll's last frame, arriving while a later Roll is running, therefore stops that roll and drains the queue. While the worker is idle it only sets `_stop` (cleared at the next job start) and logs a misleading message. Narrow timing window.

</details>

<a id="session-roll-sr-14"></a>

### SR-14 -- Roll settings the driver will refuse are checked only after the seek has moved the film and the folder exists; at 7200 dpi three frames are prescanned, metered and passed before the roll gives up

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/session.py:2286-2313`, `rps7200/session.py:2490-2515`, `rps7200/direct.py:3853-3871`, `rps7200/direct.py:4094-4133`, `rps7200/direct.py:2942-2947`, `tools/scan_roll.py:332-338`, `tools/gui.py:1786-1790`

**Doc claim:** session.py:2276-2279 ('a roll that cannot put the film on its first frame must leave nothing behind and scan nothing'). The same principle is not applied to settings that are refused for certain.

An operator error the tool refuses immediately instead spends transport time in the window. The seek may wind back many frames, then the roll is refused, leaving an empty folder. At 7200 dpi it also costs three prescans, three rounds of metering and two advances before `max_failures` ends it, and the result is reported as a finished job.

**Evidence (from the code):**

```text
`_roll` calls `seek(self._scanner, first, ...)` (2288) and `out.mkdir(...)` (2312) before `self._scanner.scan_roll(...)`, a generator whose checks (`locks_white_balance(film)`, infrared-blind film, meter mode, direct.py:3853-3871) run only at the first `next()`. A 7200 dpi roll is refused only inside `self.scan(...)` (`if not self.correctable_at(resolution, frame): raise self.uncorrectable(...)`, direct.py:2943-2947), after that frame's prescan and `auto_exposure` probes, and it counts as a per-frame failure. The tool refuses up front: `if (not args.no_shading and not args.dry_run and not _Driver.correctable_at(args.dpi)): ap.error(...)` (scan_roll.py:333-338). The window accepts 25..7200 (gui.py:1787).
```

**Failure scenario:** The operator picks 7200 dpi in the Roll dialog with the film on frame 12. The session winds back 11 frames, prescans and meters frame 1, is refused, advances, repeats twice, and ends with 'giving up after 3 consecutive failures'. Several minutes of transport and lamp time are spent and nothing is scanned.

**Fix:** At the top of `_roll`, before the seek, run the refusals the driver already has as static checks (`DirectScanner.correctable_at(job.resolution)`, `supports_infrared(job.film)`, `locks_white_balance`, the meter mode) and raise before anything moves, as tools/scan_roll.py does.

<details><summary>Second reader's check</summary>

`_roll` calls `seek(...)` (2288) and `out.mkdir(...)` (2312) before `scan_roll`, whose film and meter checks run only at the first `next()` (direct.py:3853-3871). Nothing in scan_roll, the session or gui.py calls `correctable_at` (grep finds none in session.py or gui.py). At 7200 dpi the refusal comes inside `scan()` (direct.py:2942-2947) after that frame's prescan and metering, and ShadingUnavailable is in the per-frame except tuple, so the roll spends max_failures frames and then returns as 'finished'. The GUI accepts 25..7200 (gui.py:1787). The tool refuses up front (scan_roll.py:333-338).

</details>

<a id="session-roll-sr-15"></a>

### SR-15 -- A scan can be judged against the prescan of a different picture: two unknown positions compare equal, and `_last_prescan` never expires

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/session.py:2079`, `rps7200/session.py:2134-2146`, `rps7200/session.py:2119-2120`

**Doc claim:** session.py:2137-2139

The docstring says 'A prescan of a different frame is a different photograph, and judging a scan against one would be worse than not judging it at all.' When READ_STATE comes back empty both times, or when a new strip resets the counter to the value the old prescan was taken at, a scan whose read direction is unknown is compared with a different photograph and may be turned 180 degrees or mirrored in every delivered file.

**Evidence (from the code):**

```text
`self._last_prescan = (image, self._position(), meta)` (2079). `return (image, meta) if where == self._position() else None` (2146): None == None counts as the same picture. Nothing clears `_last_prescan` after a Move, a Roll, or a strip reload that resets the counter.
```

**Failure scenario:** The operator prescans frame 1 of strip A (counter 0), swaps in strip B (counter back to 0), and scans frame 1 without prescanning. The scan's line tags do not settle its direction. `reversal_against` compares it with strip A's picture, and the delivered TIFF is filed turned.

**Fix:** Treat a None position as 'unknown', never 'same'. Invalidate `_last_prescan` on every Move, Roll and Calibrate, and whenever the counter reads a value it has already passed.

<details><summary>Second reader's check</summary>

`_prescan_here` returns the stored prescan when `where == self._position()` (2146), and `_position()` returns None on any exception (2738-2743), so None == None counts as a match. `_last_prescan` is set only in `_prescan` (2079) and never cleared. The effect is limited: `_note_reversal` acts only when `match_prescan` is on and the pass's own line tags did not settle its direction (2175-2186), so the scenario is narrow.

</details>

<a id="session-roll-sr-16"></a>

### SR-16 -- Nudge docs still describe the param-8 transport law, and MAX_FINE_STEPS now allows about two frames of chained sub-frame travel

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:93-97`, `rps7200/session.py:1197-1201`, `rps7200/session.py:1207-1224`, `rps7200/session.py:2229-2233`

**Doc claim:** session.py:93-94, 1208, 1222, 2229 (stale param-8 / 1.01 mm statements)

MAX_FINE_STEPS was justified as 'a sub-frame move asked to travel further than this is a whole-frame job'. With param 87 per command, the same eight steps span about two whole frames, so the guard no longer means what its comment says. The window caps a nudge at one command (gui.py:3395-3401), but the session API does not.

**Evidence (from the code):**

```text
`#: distance = STEP_MM x param + OVERHEAD_MM, for param 1 and param 8.` (94), while `FINE_MAX_MM = (DirectScanner.STEP_MM * DirectScanner.MAX_CORRECTION_PARAM + ...)` uses 87. plan_nudges says 'for an integer param in 1..8' and '`param_for_mm` already clamps silently at param 8' (1208, 1222). `_move` says 'One SLIDE command tops out at ~1.01 mm' (2229). With MM_PER_UNIT 0.1057, FINE_MAX_MM is about 9.39 mm, and `MAX_FINE_STEPS = 8` accepts about 75 mm (about 710 units, two frames) as a sub-frame Move.
```

**Failure scenario:** A caller submits `Move(millimetres=60)`. The session sends seven param-87 SLIDE commands (about 7 x 88.8 units) instead of refusing and pointing to the frame buttons.

**Fix:** Update the comments to the param-87 law, and redefine MAX_FINE_STEPS as a travel limit in units (e.g. one frame width).

<details><summary>Second reader's check</summary>

session.py:94 says 'for param 1 and param 8' while FINE_MAX_MM uses MAX_CORRECTION_PARAM = 87 (about 9.39 mm = 88.84 units). plan_nudges' docstring says 'integer param in 1..8' and 'clamps silently at param 8' (1208, 1222). `_move` says 'tops out at ~1.01 mm' (2229). MAX_FINE_STEPS = 8 now allows about 75 mm, roughly two frame widths. The window adds its own one-command cap (gui.py:3394-3401), but the session API does not.

</details>

<a id="session-roll-sr-17"></a>

### SR-17 -- Sub-frame SLIDE commands sent by holds, aims and the window's nudges are recorded only as millimetres derived from today's law; the param bytes are never persisted

**Severity** low · **Category** library-completeness · **Verdict** confirmed

**Where:** `rps7200/direct.py:3368-3374`, `rps7200/session.py:2245-2253`, `rps7200/session.py:1345-1347`, `tools/gui.py:3313-3314`

**Doc claim:** CLAUDE.md:284-290 ('Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter')

The transport law has already been re-fitted once (ramp 1.57 changed to 1.84). Records that hold only `spent_mm` computed with the law in force at the time cannot be turned back into the commands the hardware actually executed. That is the exact failure CLAUDE.md gives as the reason to prohibit millimetres. The operator's own nudges before a scan leave no trace in the entry that follows them.

**Evidence (from the code):**

```text
`asked = self.nudge(want)` ... `delivered_mm = asked["asked_mm"] * (1 if want > 0 else -1)`. The returned `param` is dropped, and `out` keeps only `target_mm`, `moves`, `spent_mm` and `clamped` (direct.py:3345-3374). `_move` keeps `moved += abs(out.get("asked_mm", 0.0))` and returns a string (session.py:2251-2253). approved.json stores `"offset_mm": round(a.offset_mm, 4)`. `_CommandLog` records commands only between `start()` and `stop()` inside `scan()`, so SLIDEs between passes appear in no entry.
```

**Failure scenario:** MM_PER_UNIT is revised next month. The 2026-09 registration histories say 'spent 1.23 mm', but whether that was param 9 or param 10 cannot be recovered, so the hold data cannot be re-fitted to the new law.

**Fix:** Record each SLIDE sent between passes (action, param, value, time) in the hold/aim `history`, and log the window's Move nudges into the next pass's record or the roll manifest. Persist distances in param units, as CLAUDE.md requires.

<details><summary>Second reader's check</summary>

`nudge` returns `param` (direct.py:3638-3641), but `_hold_to_approved` keeps only `asked_mm` summed into `spent_mm`, plus `moves` and `clamped` (3368-3374), and per-move values are not kept. `_move` accumulates `moved += abs(out.get("asked_mm", 0.0))` and returns only a string (2245-2253). _CommandLog records commands only between start() and stop() inside a pass, so SLIDEs between passes appear in no entry. approved.json stores `round(a.offset_mm, 4)` in mm (gui.py:3314).

</details>

<a id="session-roll-sr-18"></a>

### SR-18 -- The session's roll.json and survey.json do not say how a run ended, and differ from the tool's format for the same files

**Severity** low · **Category** library-completeness · **Verdict** confirmed

**Where:** `rps7200/session.py:2387-2466`, `rps7200/session.py:2688-2701`, `rps7200/session.py:2639-2680`, `tools/scan_roll.py:822-830`, `tools/scan_roll.py:694`

A year later, roll.json cannot say whether a roll ended at the end of the film, was stopped by the operator, gave up after failures, or died with the device suspect. One file written by two writers in two shapes makes readers (carry_walk, roll_summary) handle each differently.

**Evidence (from the code):**

```text
The session's manifest has no `started`, `finished`, `duration_s` or `stopped` field, and a stop only returns `stopped = f"stopped after frame {number}, as asked"` to the event queue (2689-2691). A DeviceSuspect, max_failures, end of film or exception is recorded nowhere in the file. The tool writes `manifest["finished"]`, `manifest["duration_s"]` and `manifest["stopped"]` (scan_roll.py:822-827) and names `prescan_before` in its records (694). The session's records do not.
```

**Failure scenario:** A resumed roll shows frames 1-9 done and 10-38 missing. Nothing on disk says whether frame 10 was blank (end of strip) or the device went suspect, which decides whether resuming is safe.

**Fix:** Write `started`, `finished`, `ended` (reason) and `error` into the manifest at the end of `_roll` (in a finally), and align the record fields (`prescan_before`, `file`) with tools/scan_roll.py.

<details><summary>Second reader's check</summary>

The session's manifest dict (2387-2466) has no started, finished or stopped fields, and `_roll`'s end (2688-2701) writes nothing about why it ended. A stop is only returned as a note, and DeviceSuspect, max_failures or an exception reach only the event queue. The tool writes `finished`, `duration_s` and `stopped` (scan_roll.py:822-827) and `prescan_before` (694).

</details>

<a id="session-roll-sr-a1"></a>

### SR-A1 -- A hold or aim that raises part-way loses every record of the SLIDE moves it already sent, and the walk's travel budget never counts them

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3368-3380`, `rps7200/direct.py:4001-4011`, `rps7200/direct.py:4040-4046`, `rps7200/direct.py:3585-3595`, `rps7200/direct.py:4147-4156`

The film may already have been moved several commands' worth when the loop fails, and nothing in the survey.json or roll.json record, the library entry or the StripWalk says so. The frame's registration describes the pre-move picture as if the film were still there. The roll-wide travel budget (`walk.affordable`) undercounts, so later frames may be allowed moves the budget was meant to refuse. `misses` is not incremented either.

**Evidence (from the code):**

```text
`_hold_to_approved` accumulates `out["moves"]`, `out["spent_mm"]` and `out["history"]` in a local dict and sends `asked = self.nudge(want)` then `self.prescan(...)` with no try (3368-3380). If either raises, `out` is discarded. scan_roll's except yields `RollFrame(index, position, None, {}, prescan_image, marks, error=str(exc), ...)` (4152-4156) with `marks` lacking `approved`/`correction`, because `marks["approved"] = ...` / `marks["correction"] = ...` run only after `fix` returns (4028, 4046). In `_aim_frame`, `walk.record(index, fix.get("residual_mm"), fix.get("spent_mm", 0.0), ...)` (3585-3587) is skipped too.
```

**Failure scenario:** On a walk with 'correct', frame 6's aim sends two param-40 nudges (about 84 units). The second verification prescan fails with a CheckCondition. survey.json records frame 6 with its original registration and an error, and nothing records that the film moved. The sheet's positions for the frames after it are measured on film shifted by an amount nobody recorded, and the budget check lets the next frame spend travel it should have been denied.

**Fix:** Wrap the hold loop so that on an exception the partial `out` (moves, per-move params, history) is attached to the exception or returned alongside it, and record it in `marks` in scan_roll's except branch. Call `walk.record(..., verified=False)` with the spent travel on that path as well.

<a id="session-roll-sr-a2"></a>

### SR-A2 -- tools/scan_roll.py writes walk prescans with TIFF deflate inline on the scanning thread, with the device open

**Severity** low · **Category** hardware-safety · **Verdict** found-by-verifier

**Where:** `tools/scan_roll.py:657-660`, `tools/scan_roll.py:688-694`, `rps7200/tiff.py:97-103`, `rps7200/session.py:2544-2548`

**Doc claim:** rps7200/session.py:2544-2548 ('nothing local happens on the scanning thread with the device open')

The tool and the window diverge on the rule that nothing is compressed on the scanning thread with the device open. The tool compresses each walk prescan inline between passes. The files are small (about 370 KB), so the exposure is brief, but the last frame's write happens with the device open and idle. The tool's walk also leaves a failed frame's prescan unfiled and unwritten (`if frame.error:` branch), whereas the session files it.

**Evidence (from the code):**

```text
Inside `with interrupt, DirectScanner(...) as s:` the dry-run branch does `tiff.write(str(pre), frame.prescan)` (659-660) and `tiff.write(str(was), frame.prescan_before)` (690-691), with tiff.write's default `compress: bool = True` (zlib plus predictor). The session's equivalent comment says: 'Written by the writer thread, not here: it is only ~370 KB, but nothing local happens on the scanning thread with the device open' (session.py:2544-2548).
```

**Failure scenario:** A 38-frame walk from tools/scan_roll.py --dry-run deflates 38 to 76 small TIFFs on the scanning thread with the scanner open, which is the state CLAUDE.md warns against, and the window's code comment says the window avoids it.

**Fix:** Route the tool's prescanNN.tif and -before.tif writes through the FrameWriter's `paths` as the session does, or write them uncompressed (`compress=False`).

<a id="session-roll-sr-20"></a>

### SR-20 -- The FrameWriter thread has no liveness check; if it ever died, submit and finish would block forever with the device open

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/session.py:1587-1600`, `rps7200/session.py:1705-1711`, `rps7200/session.py:1570-1571`

I found no realistic exception that escapes the handler, so the thread is robust as written. But nothing notices if it does stop. The third submit would then block the scanner thread between frames with the device open, `finish()` would block in `put(None)`, and `_wait_to_quit` in the window (no timeout, by design) would never let the window close.

**Evidence (from the code):**

```text
`queue.Queue(maxsize=depth)` with depth 2. In `_run`'s `except Exception` branch, `self.on_done(job.get("seq", 0), job["number"], None, str(exc))` is unguarded, whereas `_tell` is guarded. `submit` is `self.queue.put(job)` and `finish` is `self.queue.put(None); self._thread.join()`, both without a timeout or an `is_alive()` check.
```

**Failure scenario:** Hypothetical: `_filed` raises inside the writer's failure handler, and the writer thread exits. Two frames later the roll hangs in `submit()` with the device open and idle, and the window can only be killed, which leads to SR-19.

**Fix:** Guard `on_done` in the failure branch like `_tell`. Have `submit`/`finish` check `self._thread.is_alive()` and fall back to writing inline (compress=False) or raise a clear error.

<details><summary>Second reader's check</summary>

`submit` (`queue.put`) and `finish` (`put(None)` then `join()`) have no liveness check or timeout (1705-1711). The `on_done` call in the failure branch is unguarded (1596-1597), unlike `_tell`. As the reader says, no realistic escaping exception was found: `_filed` only emits events and calls request_stop. This is an observation, not a defect.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry for each filed pass (single Scan/Prescan, roll frame, walk prescan) | <library>/<UTC YYYYmmddTHHMMSSZ>_<stock\|unknown-film>[_f<roll>-NN]_<dpi>dpi[_ir][-N]/ | directory: scan.tif (uint16 frame / uint8 prescan; zlib+predictor TIFF for roll passes, uncompressed for single passes until library.compact), raw.bin.gz or raw.bin (bytes exactly as read, including 2-byte index headers), shading.npz (np.savez_compressed of the session's ShadingReference at submit time), ccd_mask.bin (DirectScanner._ccd_mask of the last pass), prescan.tif (roll frames only), scan.json (record incl. scan fields, extra.commands, extra.roll_membership, roll_index/roll_position, registration, rotation/flipped/reversal of the delivered file, calibration.report, sha256 of every file), INCOMPLETE marker while writing | scan.tif raw when the pass supplied last_pixels_raw (otherwise corrected and labelled corrections_applied=['shading']); raw.bin exact; prescan.tif inside a roll frame entry is CORRECTED 8-bit, and the record does not say so | FrameWriter._write -> library.save (session.py:1656-1668), queued by ScanSession._file (session.py:2781-2906); library.compact at session close (session.py:1990-1995) | library.load/corrected/reconstruct/verify, tools/library.py, the window (full-res view, Export, Save As, roll lookup by film.frame '<roll>-NN'), DemoScanner (as picture source), tools/make_comparison.py | Raw bytes are byte-exact with sha256. The raw pixels are exact. Not exact or not complete: raw bytes are taken from capture_record() at filing time and can belong to a later pass in a walk's error path (SR-06); real-roll prescans are stored corrected only, with no raw, no own mask and no label (SR-04); an entry is written non-atomically except scan.json, with INCOMPLETE as the marker |
| Library index | <library>/index.json | JSON list of per-entry summaries, rebuilt from every scan.json on each save | metadata | library.reindex via library.save (FrameWriter thread and, at close, DirectScanner._debug_flush on the worker thread, concurrently) | humans / tools (derived, safe to delete) | Written in place with write_text, not atomically, and can be written by two threads at once at close |
| Roll manifest (real roll) | <rolls>/<roll>/roll.json (+ roll.json.bak once per run, roll.json.legacy once before renumbering, roll.json.unreadable[-N] when set aside, .roll.json.part transient) | JSON: roll, numbering='strip', dpi, infrared, meter, film, dry_run, start_at, prescan_resolution, rotation, flipped, only, settings{resolution, infrared, fast_infrared, film, meter, mono, mono_channel, prescan_resolution, correct, correct_dry_run, reverse_hold, max_failures, frames, start_at, only, rotation, flipped}, wanted, frames[{number, index, transport_position, registration, error, done, entry, filing_error, exposure, gain, offset, rotation, flipped}] | bookkeeping (distances in mm) | RollManifest._write -> write_manifest (session.py:886-922; temp + fsync + os.replace, retries on Windows PermissionError), from the scanner thread (record) and the writer thread (filed), under one lock | ScanSession._roll on resume (earlier_manifest, renumbered, carry_on), the window (roll list/summary, Export, reopen), tools/scan_roll.py, legacy_shift/renumbered | Atomic replace (the directory is not fsynced). A rescanned frame's record replaces the earlier take's (SR-08). It records no end state (SR-18), and hold nudges only as mm (SR-17) |
| Walk manifest | <rolls>/<roll>/survey.json (+ .bak, .legacy, .unreadable) | JSON like roll.json, with frames[{number, index, transport_position, registration, error, done=false, prescan:'prescanNN.tif', prescan_rotation, prescan_flipped}]; replaced by a new walk unless extend_walk | bookkeeping | RollManifest via write_manifest (session.py:2318, 2474-2477, 2686) | the window (read_survey, walked_prescans, carry_walk, contact sheet), tools/scan_roll.py --approved, walk_shift | Atomic. Records name no library entry for their prescan and never name prescanNN-before.tif (SR-04). A legacy record's prescan filename survives renumbering and can then point at an overwritten file (SR-07) |
| Roll frame delivered copy | <rolls>/<roll>/frameNN.tif | TIFF via export.write -> tiff.write (zlib+predictor), 16-bit RGB(I) or single channel when mono | corrected, oriented (session/approved rotation+flip, plus any reversal), possibly mono-reduced | FrameWriter._write (session.py:1673-1690), path set in _roll (2629) | operator / NegPy; the window's roll views | No: corrected and derived. Written in place, non-atomically, and overwritten on a rescan |
| Walk prescan file (hold/approval reference) | <rolls>/<roll>/prescanNN.tif | 8-bit RGB TIFF (zlib) | corrected, oriented by the session's rotation/flip recorded per record | FrameWriter via ScanSession._file(path=surveyed) (session.py:2549-2566) | the window's read_survey/walked_prescans (un-oriented with prescan_arrangement and used as the Approved.reference), tools/scan_roll.py --approved, gui.carry_walk | No: corrected. In place, overwritten on a re-walk, and can overwrite a renumbered legacy record's file (SR-07) |
| Pre-correction walk prescan | <rolls>/<roll>/prescanNN-before.tif | 8-bit RGB TIFF | corrected; NO library entry (file_entry=False) | ScanSession._file(..., file_entry=False) (session.py:2574-2587) | humans only (not named in survey.json by the session, so gui.carry_walk skips it) | No raw exists outside debug mode |
| Operator output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg(+.dng), <out_dir>/<roll>_<HHMMSS>_<dpi>dpi[_ir].*, <out_dir>/<YYYYmmddTHHMMSS>_<dpi>dpi[_ir].*, <out_dir>/prescans/...; '-N' suffix via _unclaimed | TIFF (zlib) or JPEG q95 8-bit with a 4-sample DNG for infrared | corrected, oriented, mono per setting; JPEG lossy | FrameWriter._write via export.write; the name is chosen at submit time on the scanner thread (session.py:2828-2831) | operator / NegPy | No. The name race lets the before-prescan overwrite the after-prescan (SR-09). Compressed while the device is open (SR-03) |
| Approved positions for a commissioned roll | <rolls>/<roll>/approved.json (+ .bak) | JSON: roll, numbering, frames[{number, offset_mm (4 dp), rotation, flipped, reference_entry, source, as_walked?}] | operator decisions (mm) | tools/gui.py _write_approved via session.write_manifest (atomic, keep_previous) before submitting Roll | the window (read_approved on reopen/resume), tools/scan_roll.py --approved. The session only receives in-memory Approved objects, whose reference pixels are never persisted by the session | Offsets rounded to 0.1 um in mm, not in param units. The reference picture is persisted only by path (reference_entry), and that path is empty when the filing event had not arrived |
| Shading reference cache and calibration archive | <calibration>/shading.npz, <calibration>/<UTC>/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | npz (compressed), raw calibration bytes (uncompressed), JSON with commands and sha256 | data.bin raw; shading.npz is a reduction of it | Calibrate job -> DirectScanner.ensure_shading (session.py:2041-2059; direct.py:806-887) | Calibrate(mode='reuse') -> load_shading; humans/tools for re-reduction | data.bin exact with sha256 |
| Debug spool (RPS7200_DEBUG=1) | $TMPDIR/rps7200-debug-XXXX/NNN-{image.npy, raw.bin, meta.json, shading.npz, ccd_mask.bin} | npy raw pixels, raw bytes, JSON meta | raw | DirectScanner._debug_capture on every pass (direct.py:908-998) | DirectScanner._debug_flush at close(), which files unclaimed passes into <RPS7200_DEBUG_ROOT or library> and deletes claimed ones | Exact, but claimed passes are deleted whether or not the session's filing succeeded (SR-01), and after force_abort the spool is never flushed or reported (SR-10) |

**Second reader's corrections to this table:**

- **Library entry compression.** `compress=True` in scan.tif, prescan.tif and every delivered TIFF means zlib plus predictor only when tifffile is installed. The built-in writer ignores `compress` and always writes uncompressed (tiff.py:108-113 docstring, 163-165). So "zlib+predictor TIFF for roll passes" holds only on the tifffile path.
- **Walk prescan library entries** are filed with `compress=bool(roll)`, which is True because a walk has a roll name. They are gzipped and deflated on the writer thread while the walk goes on, and the last one is compressed with the device idle. The session passes no `on_filed` for them, so survey.json records never name the prescan's library entry. Only the prescanNN.tif filename is recorded.
- **Real-roll frame entries** also carry `extra.registration`, which includes the hold/aim record (`approved` or `correction`: target_mm, moves, spent_mm, history, clamped). It is in mm, has no per-command params, and is lost when a hold raises part-way (SR-A1).
- **Debug spool.** shading.npz is written once per reference object, as `NNN-shading.npz` for the first pass that used it, not once per pass. image.npy holds the raw pixels (`_debug_capture(raw_pixels, meta)`, direct.py:3222).
- **Output-folder copies.** For a single Scan the name comes from a timestamp to the second. For walk prescans it comes from roll and frame, and that is what collides in SR-09. A real roll writes no prescan into out_dir at all.
- **Pre-correction walk prescan.** It is oriented with the session's prescan rotation and flip at write time, but no record says so. The survey record carries `prescan_rotation`/`prescan_flipped` only for prescanNN.tif.
- **roll.json frame records** can also carry `filing_error`. `entry` is set only once the writer reports success (RollManifest._apply). If a rescan's earlier take answers late, that answer is swallowed by the `waiting > 1` branch (session.py:1147-1152), so the earlier take's entry path is never recorded anywhere in the manifest.

## What the operator can do

- Calibrate: measure a new shading reference (moves the carriage) or reuse the cached calibration/shading.npz.
- Prescan at any dpi. The pass is filed in the library with its raw pixels and bytes, and copied to <out>/prescans if an output folder is set.
- Scan one frame at 25-7200 dpi, RGB or RGBI, tied or untied infrared, auto or fixed exposure. It is filed raw (compressed at close) and delivered corrected, oriented and optionally mono to the output folder.
- Walk a strip (dry-run Roll) from any start frame, optionally extending the previous walk in the same folder, with or without in-walk correction (correct / correct_dry_run).
- Commission a real roll from the contact sheet with chosen frames (only), per-frame approved positions and orientation, or from the Roll button over a frame range.
- Move the film whole frames forward/back, or nudge it sub-frame (Move.millimetres, planned by plan_nudges).
- Press Stop: the pass in flight completes, a roll ends after the current frame, and queued jobs are dropped.
- Force abort after typing ABORT: closes the USB transport under a running read.
- Quit while busy: 'Yes' stops after the frame in flight and then quits, 'No' waits for all queued work, 'Cancel' keeps working.
- Change rotation, flip, output folder, output format or JPEG quality, or the match-prescan toggle, while a roll runs. Frames written afterwards follow the new values and each record states what its file got.

## What the operator should not do

- Force-abort during a read: the frame is lost, the scanner almost certainly needs a power cycle, and with RPS7200_DEBUG=1 the debug spool is orphaned in the temp directory.
- Press Ctrl-C in, or close, the terminal that launched the window while it scans: the daemon threads die mid-read.
- Reuse a roll name or folder for a different strip: roll.json merges by frame number, frameNN.tif and prescanNN.tif are overwritten in place, and the old strip's approved.json stays beside the new frames.
- Walk again, or extend, a legacy walk folder whose walk did not start on frame 1: renumbered records keep the old prescan filenames and new files can overwrite them.
- Keep the library on a nearly full disk: a failed library.save loses the frame entirely and the in-flight frame with it.
- Start a roll at 7200 dpi: every frame is refused as uncorrectable after its prescan and metering.
- Start another scanner program while the window holds the device open.

## Mistakes nothing guards against

- A roll at 7200 dpi, or with infrared on black-and-white or Kodachrome film, is not refused before the seek winds the film and creates the roll folder. At 7200 dpi three frames are prescanned, metered and advanced past before max_failures ends the roll, and the job is reported as finished.
- Stop pressed during a seek or rewind at the start of a roll is honoured only after the whole wind completes. A Move(frames=N) is never checked for stop between frames.
- A walk with correction and an output folder set silently replaces each corrected frame's output-folder prescan with the pre-correction picture.
- A library write failure with RPS7200_DEBUG=1 deletes the spooled backup of that pass at close, because the session claimed it when queuing.
- Turning or flipping the picture in the window during a roll changes the files of the frames that follow. This is recorded per frame, but no warning is shown.
- Rapid clicks before the worker's 'busy' state reaches the window can queue several moves or nudges, because the refusal reads a flag updated only on the event poll.
- Quitting the window right after a large roll: the last frames are still being gzipped with the device open and idle, and nothing indicates that is happening.

## Dataflow notes

Entry. The window calls ScanSession.submit(job) (session.py:1822), which puts the job on the unbounded `_jobs` queue. The single daemon worker `_run` (1928) opens the scanner through the `_open_scanner` seam (1794; DirectScanner by default at 1896, or DemoScanner injected by gui.py:8904), attaches the log and progress hooks (`_listen`, 1908), sends INQUIRY and reports the transport position. It then dispatches each job (`_dispatch`, 2022) to `_calibrate` (2041), `_prescan` (2061), `_scan` (2100), `_roll` (2275) or `_move` (2203). Pixels enter from DirectScanner.scan/prescan, which return the corrected image and leave the raw pixels in `last_pixels_raw` and the pass meta in `last_scan_meta`. The raw bytes, reference and mask are read later through `capture_record()` (direct.py:889). A roll enters through `seek` (229), which calls wait_warm, `_ask_position`, then `rewind` or advance, then re-reads, followed by the DirectScanner.scan_roll generator (direct.py:3765; the demo runs the same function through `_drivers_roll`). Each `RollFrame` carries the corrected prescan, raw_prescan, prescan_meta, prescan_before, and the image with raw_image and meta. Transformation on the scanner thread: `_note_reversal` (2148) may add `meta['reversal']`. `_deliver` (2705) makes a decimated working copy and emits a 'result' Event. `_file` (2781) chooses the delivered paths (rolls/<roll>/frameNN.tif or prescanNN.tif plus an out_dir name from `_unclaimed`), reads capture_record() and drops it if `raw_bytes_disagree`, composes the orientation (reversal, then per-frame Approved or session rotation/flip), calls debug_claim on the raw pixels, and submits a job dict to FrameWriter (bounded queue, depth 2). `RollManifest.record` (1107) then writes the frame record atomically, with done=false while filing is pending. On the writer thread, FrameWriter._write (1618) orients and mono-reduces the delivered image, calls library.save(raw_image or image, meta, **capture, compress=bool(roll)), then export.write for each path, then on_done calls `_filed` (1998), which emits 'filed', or logs and request_stops on failure, and `_tell` calls `RollManifest.filed` (1126), setting done=true with the entry. Exit: events are drained by the window's `poll` (1885). At shutdown (1968-1996) the order is scanner.close(), which runs DirectScanner._debug_flush and so files unclaimed spooled passes and deletes claimed ones, then writer.finish(), then re-saving of any manifest whose last write failed, then library.compact on single-pass entries, then the 'closed' event. force_abort (1855) closes the transport from the UI thread and makes `_run` skip scanner.close(). Legacy manifests pass through `renumbered` (458) when a roll resumes or a walk extends, and the result is written back under numbering 'strip' (with .legacy kept). approved.json is written by the window before a commissioned Roll is submitted. The session receives Approved by value, and its reference pixels reach `_hold_to_approved` only in memory.
