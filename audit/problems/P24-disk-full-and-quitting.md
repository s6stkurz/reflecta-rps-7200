# P24 -- A full disk does not stop a roll; quitting kills writes mid-file

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 9 findings in 7 areas

[Back to the summary](../README.md)

## The problem

When the disk fills, every remaining frame of a roll is scanned and then discarded: the
window only logs it, the CLI says nothing until the end, and `roll.json` marks the frames
done (P19). Closing the window mid-roll waits for the whole roll and every queued job
with no Stop offered; quitting while Save all / Export runs kills the daemon writer
mid-file, leaving truncated delivered files. A corrupt settings file is discarded and
overwritten silently, and a failed settings write is silent too. The only report of
dropped raw bytes and failed filings is a log widget that is not persisted.

## Fix plan

1. Check free space before each frame (estimate from dpi/channels) and stop the roll with
   an error when the next frame will not fit.
2. A failed filing is an error dialog and stops the roll, not a log line.
3. Quit offers "stop after this frame" and joins writer threads.
4. Persist the session log to `library/logs/<date>.log`.

## Evidence (from [RX-1](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-1))

**Where:** `rps7200/session.py:1046-1056`, `rps7200/session.py:1074-1100`, `rps7200/session.py:1348-1355`, `rps7200/session.py:1895-1926`, `tools/scan_roll.py:343`, `tools/scan_roll.py:581-585`, `tools/scan_roll.py:605-613`, `tools/scan_roll.py:624-628`, `rps7200/export.py:146-153`, `tools/scan.py:238-246`, `rps7200/library.py:308-310`

```text
No free-space check exists anywhere: grep for disk_usage|statvfs|ENOSPC finds nothing. FrameWriter._run swallows every failure per frame: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")`. The GUI's only signal is `self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1354). The roll loop never looks at writer.errors. Meanwhile it writes `"done": bool(rf.error is None and rf.image is not None)` and rewrites roll.json. The CLI builds `writer = FrameWriter()` with no on_done, prints `picture {number}: {path} ...` at submit time, and prints the errors only after `writer.finish()` at the end of the roll. Its final `checkpoint()` (scan_roll.py:628) sits outside any try. export._write_infrared tells the operator `the library entry keeps all {channels} channels`, but library.save runs after it in the same job, on the same full disk. scan.py files in a bare loop, `for held in pending: entries.append(library.save(...))`, so the first failure raises and the delivered file is never written. library.save calls `reindex(root)` after scan.json, so a reindex ENOSPC reports a complete entry as unfiled.
```

**Failure scenario:** A 38-frame 3600 dpi RGBI roll is started with 8 GB free. By the code's own figures each frame writes ~120 MB (frameNN.tif) + ~120 MB (scan.tif) + ~100 MB (raw.bin.gz), plus ~120-142 MB more if an output folder is set. Around frame 20 tiff.write raises ENOSPC. Frames 20-38 are each scanned for ~6 minutes and discarded. The log shows 19 'could not be filed' lines between other messages, and roll.json lists all 38 as done, so Rolls... reports the roll finished and a resume offers nothing.

**Second reader's check:** Code matches the evidence. FrameWriter._run (session.py:1046-1056) catches every exception and only appends it to errors. _filed (session.py:1353-1355) only emits a log line. The roll loop never reads writer.errors and writes "done": bool(rf.error is None and rf.image is not None) (session.py:1905) right after the async submit. scan_roll.py creates `writer = FrameWriter()` without on_done, prints `picture {number}: {path}` at submit time, and prints the errors only after writer.finish(). Its final checkpoint() (scan_roll.py:628) is outside any try. export._write_infrared promises 'the library entry keeps all channels' before library.save has run. scan.py:238-246 saves in a bare loop ahead of export.write, and library.save calls reindex after scan.json. What the finding gets wrong is 'the roll goes on for hours'. That only holds when the full volume is not the one holding rolls/, for example out_dir on a full USB stick or the library on another disk. That is a realistic setup, and there every frame's library entry is lost because export.write runs first. When rolls/ shares the full volume, the per-frame roll.json truncate-and-rewrite on the scanner thread (session.py:1924) needs a new block every few frames as the file grows. It then raises ENOSPC, fails the Roll job and leaves roll.json truncated or empty, which is the RX-2 outcome. In the CLI the same happens through checkpoint() in the loop, and then again through the unguarded final checkpoint, which ends in a traceback.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [RX-1](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-1) | resource-exhaustion-crash-recovery-time | high | partly | A full disk does not stop a roll: every frame is scanned and discarded, the GUI only logs it, the CLI says nothing until the end, and roll.json marks the frames done | rps7200/session.py:1046-1056, rps7200/session.py:1074-1100, rps7200/session.py:1348-1355 |
| [GUI1-16](../areas/gui-part1.md#gui-part1-gui1-16) | gui-part1 | medium | confirmed | Quit during a job waits for every queued job (hours for a roll) without stopping, and kills Save all / Export threads mid-file | tools/gui.py:3039-3068, rps7200/session.py:1246-1248, tools/gui.py:3822-3841 |
| [GUI2-17](../areas/gui-part2.md#gui-part2-gui2-17) | gui-part2 | medium | confirmed | Quitting while Save all or Export is writing kills the daemon writer mid-file with no warning | tools/gui.py:3039-3050, tools/gui.py:3052-3068, tools/gui.py:3840-3841 |
| [CC-09](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-09) | cross-process-and-thread-concurrency | medium | confirmed | Quitting while Save all / Export rolls is running kills the daemon writer thread mid-file and leaves truncated delivered files | tools/gui.py:3039-3050, tools/gui.py:3052-3068, tools/gui.py:3070-3086 |
| [RX-12](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-12) | resource-exhaustion-crash-recovery-time | medium | partly | Closing the window during a roll waits for the whole roll and every queued job, with no stop offered and no timeout; Save all/Export threads are killed mid-write on close | tools/gui.py:3039-3068, tools/gui.py:3020-3021, rps7200/session.py:1246-1248 |
| [SR-20](../areas/session-roll.md#session-roll-sr-20) | session-roll | medium | confirmed | The only report of dropped raw bytes, failed filings and renumbering doubts is an unpersisted log widget | rps7200/session.py:2095-2099, rps7200/session.py:2116-2120, rps7200/session.py:1348-1356 |
| [OUT-11](../areas/outputs.md#outputs-out-11) | outputs | medium | partly | A corrupt settings file is discarded silently and overwritten; save failures are silent | rps7200/settings.py:58-79, rps7200/settings.py:82-97, tools/gui.py:666-697 |
| [UT-A1](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-a1) | uncited-tests-vs-findings | medium | found-by-verifier | A failed settings write is silent: _remember ignores settings.save's None, so sheet decisions made before commissioning can be lost without a word | rps7200/settings.py:82-97, tools/gui.py:668-697, tools/gui.py:2340-2350 |
| [UT-04](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-04) | uncited-tests-vs-findings | medium | confirmed | test_settings pins the silent drop of a corrupt gui-settings.json; nothing tests that the next save then erases it | tests/test_settings.py:21-31, rps7200/settings.py:24, rps7200/settings.py:58-79 |
