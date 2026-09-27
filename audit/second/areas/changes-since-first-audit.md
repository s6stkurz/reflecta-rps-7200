# The fixes themselves (83dbb22..03aacba)

Area key `changes-since-first-audit`. 20 findings: 1 high, 9 medium, 8 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

I audited the fixes made between 83dbb22 and 03aacba in rps7200/ (direct, session, library, demo, console, settings, protocol, bracket, usbpcap), tools/ (scan, scan_roll, library, gui hunks, make_comparison, filing_load_test, frame_edges, the new study tools) and CI. The code under rps7200/ and tools/ is byte-identical between 03aacba and HEAD.


Most fixes hold up when read against the code:
- A pass abandoned mid-read now sets `DirectScanner.suspect`.
- An untied infrared read waits 287 s.
- The calibration's raw bytes are archived.
- Library entries are written behind an INCOMPLETE marker, with checksums and the record renamed into place last.
- The debug spool survives a failed save, and stale `last_raw` is cleared.
- Commands per pass are recorded.
- Filing in-pass calibration was removed, and passes are refused before metering.
- `PROTOCOL_REVISION` correctly stays at 6: the passes that still run send the same commands.

The fixes also introduced several regressions:
1. The window and both tools switched from debug=False to RPS7200_DEBUG plus `debug_claim`. Claiming only marks a spooled pass. Every pass's full pixels and raw bytes still pile up in the OS temp directory until close(): for the GUI that is its whole lifetime, and for a 7200 dpi roll it is 43 GB. The spool is also deleted before the caller has actually filed the pass, so a filing failure loses it entirely.
2. `DeferredInterrupt` is not checked between phases (calibration → scan, seek → calibration), and it does not cover the filing done after close. A first Ctrl-C is therefore ignored for minutes, which invites a second Ctrl-C that wedges the scanner or loses queued frames.
3. The suspect guard sits only at SLIDE, START SCAN and calibration. scan()/prescan() still send MODE SELECT, gain/offset and the frame to a device the code believes is mid-scan.
4. The new atomic library writes use os.replace without the Windows sharing-violation retry that session.py already has for exactly this case.
5. The "single scans compress nothing with the device open" fix covers the library entry but not the output-folder copy written in the same job.
6. The command log lists every NoDataYet poll, although its docstring says image READs are counted, not listed.
7. A calibration archive is written but nothing reads or verifies it, and a reused reference is not linked back to its archive.
8. `compact` can leave a false checksum alarm; `library.save` can report failure after the entry is fully written.
9. The demo cannot reach the new suspect path or the debug claim/spool machinery.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [CSA-01](#changes-since-first-audit-csa-01) | high | data-integrity | confirmed | Debug spool keeps every pass (claimed ones included) in the OS temp dir until close(): GUI lifetime / whole roll, tens of GB |
| [CSA-02](#changes-since-first-audit-csa-02) | medium | data-integrity | confirmed | debug_claim deletes the safety copy before the caller has filed; a failed save then loses the pass entirely |
| [CSA-03](#changes-since-first-audit-csa-03) | medium | user-error | confirmed | First Ctrl-C is not honoured between phases: scan.py proceeds from calibration/metering into the scan, scan_roll.py from the seek into a 3-4 min calibration |
| [CSA-04](#changes-since-first-audit-csa-04) | medium | user-error | confirmed | Filing after close() is outside DeferredInterrupt: a Ctrl-C while the last frames gzip kills the daemon writer and loses queued frames |
| [CSA-05](#changes-since-first-audit-csa-05) | medium | hardware-safety | confirmed | DeviceSuspect is enforced only at SLIDE/START SCAN/calibration: scan()/prescan()/metering still send MODE SELECT, gain/offset and frame writes to a device believed mid-scan |
| [CSA-06](#changes-since-first-audit-csa-06) | medium | error-handling | confirmed | New atomic library writes use os.replace without the Windows sharing-violation retry session.py already needs for the same reason |
| [CSA-07](#changes-since-first-audit-csa-07) | medium | doc-mismatch | confirmed | 'Single scans compress nothing with the device open' covers only the library entry; the output-folder copy in the same job is deflated or JPEG-encoded while the window holds the device idle |
| [CSA-09](#changes-since-first-audit-csa-09) | medium | data-integrity | confirmed | Calibration archive is write-only and unlinked from entries corrected with a reused reference |
| [CSA-14](#changes-since-first-audit-csa-14) | medium | data-integrity | confirmed | Real-roll frame prescans: raw never filed without debug, and prescan.tif stored corrected with no record saying so |
| [CSA-17](#changes-since-first-audit-csa-17) | medium | data-integrity | partly | migrate-raw --write swaps scan.tif in two renames; interruption leaves an entry with no scan.tif |
| [CSA-08](#changes-since-first-audit-csa-08) | low | doc-mismatch | confirmed | Command log lists every NoDataYet poll, contrary to its docstring; scan.json and meta grow by thousands of entries per pass |
| [CSA-10](#changes-since-first-audit-csa-10) | low | data-integrity | confirmed | compact() interrupted after replacing scan.tif leaves a record whose checksum no longer matches: verify raises a false corruption alarm |
| [CSA-11](#changes-since-first-audit-csa-11) | low | error-handling | confirmed | library.save can raise after the entry is fully committed (reindex); callers treat a complete entry as failed |
| [CSA-12](#changes-since-first-audit-csa-12) | low | data-integrity | confirmed | force_abort (and any process kill) orphans the debug spool silently; nothing can file it |
| [CSA-13](#changes-since-first-audit-csa-13) | low | data-integrity | confirmed | Calibration ends 'successfully' on any refused read; the new guard catches only the timeout |
| [CSA-15](#changes-since-first-audit-csa-15) | low | demo-divergence | confirmed | Demo cannot reach the new suspect, debug-claim and spool paths, and records less than a real pass |
| [CSA-16](#changes-since-first-audit-csa-16) | low | user-error | partly | settings.load moves a valid settings file aside on any transient read error and silently reverts the window to defaults |
| [CSA-A1](#changes-since-first-audit-csa-a1) | low | doc-mismatch | found-by-verifier | Transport distances are still millimetres throughout the driver and tools, contrary to CLAUDE.md's 'Millimetres are prohibited' rule; new code in this range adds more |
| [CSA-18](#changes-since-first-audit-csa-18) | info | design | confirmed | PROTOCOL_REVISION correctly unchanged |
| [CSA-A2](#changes-since-first-audit-csa-a2) | info | doc-mismatch | found-by-verifier | 'Written uncompressed' calibration archive and plain window entries still run savez_compressed with the device open |

## Findings in full

<a id="changes-since-first-audit-csa-01"></a>

### CSA-01 -- Debug spool keeps every pass (claimed ones included) in the OS temp dir until close(): GUI lifetime / whole roll, tens of GB

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:922-998`, `rps7200/direct.py:1000-1016`, `rps7200/direct.py:1055-1059`, `rps7200/direct.py:1134-1145`, `rps7200/direct.py:3276-3279`, `rps7200/session.py:1896-1906`, `rps7200/session.py:1968-1979`, `tools/scan_roll.py:460-475`, `tools/scan.py:244-266`

The switch from debug=False to RPS7200_DEBUG plus debug_claim was meant to stop '43 GB of duplicate on a 38-frame roll at 7200 dpi' (scan_roll.py:462-464, session.py:1901-1903). It prevents duplicate library entries, but the duplicate data is still written. Every frame, including the claimed ones, is spooled in full: pixels as .npy plus raw bytes. The spool is freed only when the scanner closes. In the window that means the whole window lifetime. In scan_roll.py it means the end of the roll. The writes are synchronous on the scanning thread after each pass, with the device open and idle. The temp directory is on the system volume, and on many Linux distros it is RAM-backed tmpfs.

**Evidence (from the code):**

```text
_debug_capture always writes `np.save(image_path, image)` and `raw_path.write_bytes(raw)` (direct.py:941,961). `_read_pass` forces bytes to be kept: `keep_raw=keep_raw or bool(getattr(self, "debug", False))` (3279). `debug_claim` only does `item["claimed"] = True` (1016). Claimed files are removed only inside `_debug_flush` (`if item.get("claimed"): ... stuck += self._debug_unlink(item)`, 1055-1058), and that runs only from `close()` (1145). The window now uses `debug=None` (session.py:1905) and closes the scanner only in `_run`'s finally when the window quits (1972-1974). scan_roll.py:475 `with interrupt, DirectScanner(verbose=args.verbose, debug=None) as s:`.
```

**Failure scenario:** CLAUDE.md requires RPS7200_DEBUG=1. A 38-frame 3600 dpi RGBI roll spools about 38 × (142 MB pixels + 142 MB raw) ≈ 10.8 GB to temp. At 7200 dpi (shading off) it is about 43 GB. In the window this keeps growing across every scan of the day. When the temp volume (often the same volume as library/) fills: `_debug_capture` swallows its own error, but FrameWriter's `library.save` then fails with ENOSPC. The session stops the roll (`_filed` → request_stop), and the frames that fail to file are lost. On tmpfs, the host runs out of RAM first. After each 7200 dpi pass the scanner also sits open and idle while about 1.1 GB is written.

**Fix:** Do not spool a pass its caller will file. Either let the caller claim before or at capture (e.g. a scan(..., filed_by_caller=True) flag, or a `claim_next` set before the pass), or make `debug_claim` unlink the claimed item's image, raw and meta files immediately. Add a test that runs a multi-pass session with debug on and asserts temp usage does not grow with claimed passes.

<details><summary>Second reader's check</summary>

The code is as described. `_debug_capture` (direct.py:908-998) runs from scan() at direct.py:3222 for every pass. It unconditionally does `np.save(image_path, image)` (941) and `raw_path.write_bytes(raw)` (961). `_read_pass` forces `keep_raw=keep_raw or bool(getattr(self, "debug", False))` (3278-3279). `debug_claim` (1000-1016) only sets `item["claimed"] = True`. Claimed files are unlinked only inside `_debug_flush` (1055-1058), and that runs only from `close()` (1145). The window uses `debug=None` (session.py:1905) and closes the scanner only in `_run`'s finally (1972-1974). scan_roll.py:475 and scan.py:266 hold the device for the whole run. The comments at scan_roll.py:461-464, scan.py:244-248 and session.py:1900-1903 claim the claim mechanism removed the '43 GB of duplicate'. It removed only the duplicate library entries: the duplicate bytes still sit in the spool for the whole session. The `_debug_flush` comment about keeping the 'peak' down (1034-1038) is therefore also misleading, because the peak happens at capture time, not during the flush. The effect is conditional on RPS7200_DEBUG being on, but CLAUDE.md makes that mandatory for Claude-run sessions. ENOSPC inside `_debug_capture` is swallowed, but the library write that follows on the same volume is not.

</details>

<a id="changes-since-first-audit-csa-02"></a>

### CSA-02 -- debug_claim deletes the safety copy before the caller has filed; a failed save then loses the pass entirely

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/scan.py:294-300`, `tools/scan.py:366-375`, `tools/scan_roll.py:675-685`, `tools/scan_roll.py:729-731`, `tools/scan_roll.py:803`, `rps7200/session.py:2872-2875`, `rps7200/session.py:1972-1979`, `rps7200/session.py:1587-1600`, `rps7200/direct.py:1055-1059`

A pass is claimed when it is handed to the caller's filing, not when that filing succeeds. close() then unlinks the claimed spool files: the only on-disk copy of the pixels and raw bytes. close() runs before scan.py files its pending passes, before scan_roll.py's writer.finish(), and before ScanSession's writer.finish(). If the caller's library.save then fails, the pass exists nowhere. Debug filing was the safety net for exactly this case. Before these fixes (debug off) the loss already existed; the claim means debug mode no longer prevents it.

**Evidence (from the code):**

```text
scan.py `hold`: `s.debug_claim(raw)` then `pending.append(...)`. The filing loop `for held in pending: entries.append(library.save(...))` (366-375) runs after the `with DirectScanner` block has closed, and outside any try. session.py `_file`: `claim(raw_image)` then `self._writer.submit(...)`. `_run` finally: `self._scanner.close()` (flush deletes claimed spool) and only then `self._writer.finish()`. FrameWriter `_run`: `except Exception as exc: self.errors.append(...)`, which drops the job.
```

**Failure scenario:** The library drive is unplugged or full, or a Windows sharing violation hits library.save (see CSA-06). In scan.py, the first `library.save` raises after close(). The traceback ends the process, and every bracket pass (already deleted from the spool) is gone. In the window, a roll frame fails in FrameWriter: RollManifest records `filing_error`, and at window close the flush deletes that frame's spooled copy.

**Fix:** Claim only on success: have FrameWriter/_file call `debug_claim` from `on_done` when an entry was written, and in scan.py claim after `library.save` returns. Alternatively, run the debug flush after `writer.finish()` and pass it the set of filed passes. Wrap scan.py's filing loop so one failure does not abandon the remaining passes.

<details><summary>Second reader's check</summary>

scan.py:294-300 claims the pass in `hold` and appends it to `pending`. The filing loop (366-375) runs after both `with` blocks have exited, and it is outside any try. close() has already run `_debug_flush`, which unlinks the claimed spool files. session.py:2872-2875 claims before `self._writer.submit`. In `_run`'s finally, `self._scanner.close()` (1972-1974) runs before `self._writer.finish()` (1978). A FrameWriter job that fails (session.py:1590-1595) is dropped with only an error string. The flush cannot tell a claimed-and-filed pass from a claimed-and-failed one, so the spool copy of a failed frame is deleted anyway. scan_roll.py claims at 675 and 730 and only runs writer.finish() at 803, after close().

</details>

<a id="changes-since-first-audit-csa-03"></a>

### CSA-03 -- First Ctrl-C is not honoured between phases: scan.py proceeds from calibration/metering into the scan, scan_roll.py from the seek into a 3-4 min calibration

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/console.py:50-102`, `tools/scan.py:253-274`, `tools/scan.py:347-360`, `tools/scan_roll.py:473-568`, `CLAUDE.md:420-422`

CLAUDE.md:421 says Ctrl-C 'finishes the pass in flight and stops there'. In scan.py's single-pass mode, a Ctrl-C during the 3-4 minute calibration or the metering probes lets that step finish and then starts the real scan anyway (up to minutes at high dpi). In scan_roll.py, a Ctrl-C during a long seek or rewind is followed by a full 3-4 minute calibration before the roll stops. Either way the operator was just told the tool is stopping and sees it carry on, which is exactly what prompts the second Ctrl-C. That second Ctrl-C raises KeyboardInterrupt mid-read: the device is marked suspect and needs a power cycle.

**Evidence (from the code):**

```text
console.py:79-80 prints "stopping after the pass in flight -- interrupting a read wedges the scanner. Press Ctrl-C again to abort anyway." scan.py checks `interrupt.requested()` only inside `hold` and only `if args.bracket` (301). After `s.ensure_shading(...)` (273) it goes straight to `s.scan(...)` (350). In scan_roll.py, `s.wait_warm()`, `rewind`, `seek`, the nudges and `calibrate(s, args)` (491-568) never look at `interrupt.requested`. Only `scan_roll(should_stop=interrupt.requested)` does, and only after the calibration.
```

**Failure scenario:** The operator runs `tools/scan.py --dpi 3600 --ir`, changes their mind during calibration and presses Ctrl-C once. The calibration completes, then a roughly 4-minute 3600 dpi RGBI pass starts. The operator presses Ctrl-C again, and the read is abandoned mid-pass: wedge.

**Fix:** Check `interrupt.requested()` after every phase that can take minutes: after ensure_shading, after metering, before the first pass of a bracket, and after seek/rewind/nudge before `calibrate()` in scan_roll.py. Exit cleanly (code 130) when it is set. Make the handler's message name the phase actually in flight.

<details><summary>Second reader's check</summary>

`DeferredInterrupt._handler` (console.py:74-81) only sets a flag and prints 'stopping after the pass in flight'. In scan.py the only check is `if args.bracket and interrupt.requested()` inside `hold` (301). Nothing checks after `ensure_shading` (273), and nothing between metering and the pass, because `scan()` takes no should_stop. In scan_roll.py, `wait_warm`, `rewind`, `seek`, the nudge loop and `calibrate(s, args)` (491-568) never consult the flag. Only `scan_roll(should_stop=interrupt.requested)` (627) does. A first Ctrl-C during calibration therefore still leads to a full pass, or in scan_roll to a 3-4 minute calibration. That invites the second Ctrl-C, which raises mid-read and wedges the scanner. CLAUDE.md:421-422 says Ctrl-C 'finishes the pass in flight and stops there'.

</details>

<a id="changes-since-first-audit-csa-04"></a>

### CSA-04 -- Filing after close() is outside DeferredInterrupt: a Ctrl-C while the last frames gzip kills the daemon writer and loses queued frames

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan_roll.py:783-791`, `tools/scan_roll.py:803-830`, `tools/scan.py:361-375`, `rps7200/session.py:1584`

The most likely moment for an impatient Ctrl-C is at the end, while the last one or two frames are still being gzipped. At that point KeyboardInterrupt propagates from `self._thread.join()` out of main() uncaught. The daemon writer is killed at interpreter exit, possibly mid-`library.save`, leaving an INCOMPLETE entry. The rest of the queue is lost, and `record_of.save()` never runs. With debug on, those frames' spooled copies were already deleted by close() (CSA-02). scan.py loses every pending pass after the one being filed.

**Evidence (from the code):**

```text
scan_roll.py:783-788 says 'BaseException, because a second Ctrl-C ... [is] exactly the exits that used to skip `writer.finish()` and lose queued frames'. But `writer.finish()` (803) runs after `with interrupt, DirectScanner(...)` has exited, with the default SIGINT handler back. `FrameWriter._thread = threading.Thread(target=self._run, daemon=True)` (session.py:1584). scan.py's `for held in pending: ... library.save(...)` (366-375) is likewise outside `with interrupt:`.
```

**Failure scenario:** A 3600 dpi roll finishes and the terminal shows nothing while two frames compress. The operator presses Ctrl-C. Frames 37 and 38 are gone: frame 37 as an INCOMPLETE directory, frame 38 was never started. roll.json still says done=False for both, with no final 'finished'/'stopped' fields.

**Fix:** Keep DeferredInterrupt (or a second instance) installed across writer.finish() and scan.py's filing loop, with a message saying files are being written and what a second Ctrl-C will lose. Or make the writer thread non-daemon and catch KeyboardInterrupt around finish() to keep waiting.

<details><summary>Second reader's check</summary>

In scan_roll.py, `with interrupt, DirectScanner(...)` (475) exits, restoring the default SIGINT handler, before `writer.finish()` (803). FrameWriter's thread is `daemon=True` (session.py:1584). A Ctrl-C during `self._thread.join()` raises KeyboardInterrupt out of main(). The daemon thread is then killed at interpreter exit, possibly mid-`library.save`, leaving INCOMPLETE. The queued job (depth 2) is never written, and `record_of.save()` and the finished/stopped fields are never reached. The comment at 783-788 claims a second Ctrl-C can no longer skip `writer.finish()`, but that holds only inside the try. scan.py's filing loop (366-375) is likewise outside `with interrupt`: a Ctrl-C mid-gzip abandons the remaining in-memory passes, and with debug on their spool copies are already gone (CSA-02).

</details>

<a id="changes-since-first-audit-csa-05"></a>

### CSA-05 -- DeviceSuspect is enforced only at SLIDE/START SCAN/calibration: scan()/prescan()/metering still send MODE SELECT, gain/offset and frame writes to a device believed mid-scan

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/direct.py:662-671`, `rps7200/direct.py:1523`, `rps7200/direct.py:1623`, `rps7200/direct.py:2178`, `rps7200/direct.py:2994-3063`, `rps7200/protocol.py:355-364`, `CLAUDE.md:417-420`

After an abandoned pass sets `suspect`, the next Scan or Prescan job, or a metering probe, still sends seven or more configuration commands (SCSI WRITE sub-commands, WRITE GAIN/OFFSET, MODE SELECT) into a device the driver itself says may still be mid-scan. Only then is it refused at SLIDE INIT. CLAUDE.md says only status queries go through. The refusal also names the wrong thing: 'not starting a film move (init)' for a scan request, which misleads the operator.

**Evidence (from the code):**

```text
`_refuse_if_suspect` is called only in `slide` (1523), `start_scan` (1623) and `calibrate_shading` (2178). scan() sends `self.set_exposure_time()`, `self.set_highlight_shadow()`, `self.set_scan_frame(*frame)`, `self.cmd_17(1)`, `self.get_gain_offset()`/`self.set_gain_offset(...)` and `self.set_mode(...)` (3016-3059) before `self.slide(SLIDE_INIT, ...)` (3062), which is where it is finally refused. The DeviceSuspect docstring says it is 'Raised by every command that would drive the device -- a scan, a calibration, a film move'.
```

**Failure scenario:** The window's roll hits a 120 s idle timeout, so the device is marked suspect. The operator presses Scan to try again. The GUI sends MODE SELECT and friends to the half-finished pass before the job fails with 'not starting a film move (init)'. The effect of those writes on a mid-scan device is unmeasured, and this is exactly the state the guard exists to leave alone.

**Fix:** Call `self._refuse_if_suspect("a scan")` at the top of `scan()` (before metering) and of `scan_bracket`/`scan_roll`/`auto_exposure`. Consider guarding set_mode/set_scan_frame/set_gain_offset/_write_sub too. Add a test that, with suspect set, scan() raises before `t.command` is called for anything but status queries.

<details><summary>Second reader's check</summary>

`_refuse_if_suspect` is called only at direct.py:1523 (slide), 1623 (start_scan) and 2178 (calibrate_shading). scan() sends set_exposure_time, set_highlight_shadow, set_scan_frame, cmd_17, get/set_gain_offset and set_mode (3016-3059), plus test_unit_ready, before `self.slide(SLIDE_INIT, ...)` (3062) raises DeviceSuspect. auto_exposure's probes go through the same scan(). ScanSession has no suspect handling at all (no match for 'suspect' in session.py), so the window keeps accepting Scan and Prescan jobs after a roll ended on DeviceSuspect. The refusal text comes from slide(): 'not starting a film move (init)'. The DeviceSuspect docstring (protocol.py:355-364) and CLAUDE.md:418-420 say every driving command is refused.

</details>

<a id="changes-since-first-audit-csa-06"></a>

### CSA-06 -- New atomic library writes use os.replace without the Windows sharing-violation retry session.py already needs for the same reason

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:484-491`, `rps7200/library.py:397-399`, `rps7200/library.py:414-452`, `rps7200/library.py:503-511`, `rps7200/session.py:859-883`, `rps7200/direct.py:744-746`

The atomicity fix introduced a rename for every entry's scan.json, and for compact's raw.bin.gz and TIFFs, _replace_tiff, migrate-raw and save_shading. On Windows, a transient hold on the freshly written `.scan.json.part`, or a reader holding scan.json, raises PermissionError after every data file is already on disk. The entry is left INCOMPLETE with no scan.json, so `entries()` and `reconstruct` cannot see it. The caller reports the whole frame as failed.

**Evidence (from the code):**

```text
library.py `_write_atomic`: `with open(temp, "w"...)...; os.replace(temp, path)`, with no retry. session.py:859-865: 'On Windows the rename fails outright while any handle lacks FILE_SHARE_DELETE ... Defender and the indexer briefly hold every new file as a matter of course', which is why `_replace` retries there. `library.save` ends with `_write_atomic(path / "scan.json", ...)` then `(path / INCOMPLETE).unlink(...)`. The old code wrote scan.json in place with write_text, so it had no rename.
```

**Failure scenario:** Windows with Defender, during a 38-frame roll. Frame 12's `os.replace(.scan.json.part → scan.json)` hits a sharing violation. FrameWriter records `picture 12: [WinError 32]`, ScanSession `_filed` calls request_stop(), and the roll ends. The complete raw bytes sit in library/<id>/ beside `.scan.json.part` and INCOMPLETE, and nothing re-files them. In scan.py the same exception aborts the filing loop and loses later passes.

**Fix:** Move session._replace (bounded PermissionError retries) into a shared helper and use it in library._write_atomic, _replace_tiff, compact, tools/library.py migrate-raw and save_shading. Add a Windows test that holds the temp or target open briefly.

<details><summary>Second reader's check</summary>

`library._write_atomic` (484-491) and `_replace_tiff` (503-511) call plain `os.replace` with no PermissionError retry. So do `compact` (438), `save_shading` (direct.py:746) and tools/library.py:275-276. session.py:859-883 documents the Windows sharing-violation hazard and retries in `_replace`, but only for manifests. `save` writes every data file, then `_write_atomic(scan.json)` (397), then removes INCOMPLETE. A PermissionError at the rename leaves a complete entry that `entries()` cannot see, and FrameWriter reports the frame as failed. The session's `_filed` then stops the roll.

</details>

<a id="changes-since-first-audit-csa-07"></a>

### CSA-07 -- 'Single scans compress nothing with the device open' covers only the library entry; the output-folder copy in the same job is deflated or JPEG-encoded while the window holds the device idle

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:2827-2831`, `rps7200/session.py:2880-2887`, `rps7200/session.py:1673-1690`, `rps7200/export.py:181-193`, `rps7200/tiff.py:97-103`

CLAUDE.md:185-192 and the session comment say the window files a single scan or prescan uncompressed and compacts it after close. The library entry is indeed plain. But whenever an output folder is set (it is remembered in gui-settings.json), the same writer job deflates a full-resolution TIFF, or encodes a JPEG and writes a DNG, with the device open and idle. That is the same class of work, by size and CPU, that the rule exists to keep away from an idle open device. The fix is therefore incomplete while the docs say it is done.

**Evidence (from the code):**

```text
session.py:2880-2886: 'A single scan or prescan is filed with the scanner open and idle between jobs, and compressing then -- gzip, and TIFF deflate -- is what preceded a wedge ... So those are written plain', with `compress=bool(roll)`. FrameWriter._write still does `note = export.write(str(path), delivered, resolution=job["dpi"], ...)` for every path in `paths`, including the output folder added at 2828-2831. `export.write` calls `tiff.write(str(path), image, resolution=resolution)`, whose default is `compress: bool = True` (deflate), or `_write_jpeg` plus a DNG.
```

**Failure scenario:** The operator has an output folder configured and takes single 3600 dpi RGBI scans from the window. After each one, about 280 MB is deflated on the writer thread while the scanner sits open and idle: the pattern CLAUDE.md records as preceding a wedge.

**Fix:** For non-roll jobs, write the delivered copy uncompressed (thread `compress` through export.write to tiff.write) and recompress it after close. Or defer delivered copies to the close path, as `compact` is. Update the session comment and CLAUDE.md to state exactly what is and is not compressed with the device open.

<details><summary>Second reader's check</summary>

session.py:2827-2831 adds an output-folder path for every job. `compress=bool(roll)` (2887) governs only `library.save`. `FrameWriter._write` still calls `export.write(str(path), delivered, ...)` (session.py:1676-1678) for each path. That goes to `tiff.write(..., resolution=resolution)` with its default `compress=True` (export.py:192, tiff.py:102), or to a JPEG plus DNG. For a single window Scan or Prescan the device is open and idle while this runs. The comment at 2880-2886 ('those are written plain') and CLAUDE.md:188-190 describe only the entry. The same pattern exists, pre-existing, in the window's Save all and Export rolls (gui.py:4291-4307, 2757-2777, `_start_writing`), which re-correct from the library and write deflate/JPEG on a thread while the session holds the device open.

</details>

<a id="changes-since-first-audit-csa-09"></a>

### CSA-09 -- Calibration archive is write-only and unlinked from entries corrected with a reused reference

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:713-729`, `rps7200/direct.py:749-804`, `rps7200/direct.py:835-860`, `rps7200/direct.py:3208-3211`, `rps7200/library.py:1068-1140`, `CLAUDE.md:148-151`

The central requirement is that a correction can be recomputed from the calibration's own bytes. For a fresh calibration, the entry points to a folder outside the library via a CWD-relative path. For the common 'Use the cached one' / `--reuse` path there is no pointer at all, only the cache file's mtime. Nothing verifies the archive's sha256, detects a half-written archive, or re-derives a reference from data.bin. Moving or copying library/ loses the link, and `make verify` reports nothing.

**Evidence (from the code):**

```text
`load_shading` sets `_shading_origin = {"action": "loaded", "path": ..., "file_modified_utc": ..., "loaded_utc": ...}` with no archive. Only a fresh calibration gets `self._shading_origin["archive"] = str(archive)` (860). `archive_calibration` writes data.bin, ccd_mask.bin, shading.npz and calibration.json with plain writes, no INCOMPLETE marker, under `path.parent` (default `calibration/`, relative to CWD, outside library/). No code reads calibration.json or data.bin: `verify` checks only library entries.
```

**Failure scenario:** A day of scans uses the window's 'Use the cached one'. A year later, calculate_shading is improved and someone wants to recompute those corrections. The entries say only `action: loaded, path: calibration/shading.npz`, and that cache has since been replaced. Matching an archive folder by timestamp is guesswork. If the archive write was interrupted, data.bin is partial and nothing ever said so.

**Fix:** Store the archive folder (or its sha256) inside shading.npz metadata or beside the cache, and carry it through load_shading into shading_origin. Better: copy data.bin (1.7 MB) or its checksum into each entry, or into a library-level calibration store that verify checks. Write the archive behind an INCOMPLETE marker, and make verify cover calibration archives.

<details><summary>Second reader's check</summary>

`load_shading` (713-734) sets an origin without any archive link. Only the fresh-calibration path adds `archive` (859-860). `archive_calibration` (749-804) writes data.bin, ccd_mask.bin, shading.npz and calibration.json with plain writes and no INCOMPLETE marker, into `path.parent`. No code reads calibration.json or data.bin: grep finds them only in direct.py. `library.verify` (1068-1140) checks library entries only. An entry corrected with a reused reference therefore cannot be tied to the calibration bytes it came from.

</details>

<a id="changes-since-first-audit-csa-14"></a>

### CSA-14 -- Real-roll frame prescans: raw never filed without debug, and prescan.tif stored corrected with no record saying so

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2620-2637`, `tools/scan_roll.py:752-755`, `rps7200/library.py:375-380`

In a commissioned roll, the per-frame framing pass (the evidence for holds and registration) is kept only as corrected 8-bit pixels inside the frame entry. Its correction state is not recorded, and its mask differs from the entry's ccd_mask.bin, which belongs to the frame's resolution. Without RPS7200_DEBUG its raw pixels and bytes are never filed. The fix covered walks only.

**Evidence (from the code):**

```text
Real roll: `prescan=rf.prescan, prescan_meta=rf.prescan_meta` (session.py:2627-2628) and `prescan=frame.prescan` (scan_roll.py:752). `rf.prescan` is prescan()'s corrected return. library records only `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}`. The walk (dry-run) path was fixed to file raw prescans with their bytes (scan_roll.py:662-685); the real-roll path was not.
```

**Failure scenario:** The shading code improves later. Roll-frame prescans cannot be re-corrected or re-decoded, and a reader of the record cannot tell that prescan.tif is corrected.

**Fix:** File the real roll's prescan as its own entry from rf.raw_prescan, as the walk does, with a matching `raw_bytes_disagree` guard. Record `prescan.corrected` (from prescan_meta['shading']) in the frame entry's record.

<details><summary>Second reader's check</summary>

The real-roll paths pass `prescan=rf.prescan` (session.py:2627) and `prescan=frame.prescan` (scan_roll.py:752). That is `prescan()`'s return, i.e. scan()'s corrected image when shading is on. `library.save` writes it as prescan.tif and records only file, read_direction and carriage_state (library.py:375-380). Nothing says it is corrected. Its `files` checksum is kept but its own CCD mask is not, and the entry's ccd_mask.bin belongs to the frame pass. `rf.raw_prescan` is filed only on the walk path (session.py:2563, scan_roll.py:662-685). In a real roll it is kept only if RPS7200_DEBUG leaves it unclaimed. That breaks the 'library holds raw pixels' rule for a stored pass, with no label, so severity is raised to medium.

</details>

<a id="changes-since-first-audit-csa-17"></a>

### CSA-17 -- migrate-raw --write swaps scan.tif in two renames; interruption leaves an entry with no scan.tif

**Severity** medium · **Category** data-integrity · **Verdict** partly

**Where:** `tools/library.py:265-284`, `rps7200/library.py:corrected (corrections_applied short-circuit)`

migrate-raw --write swaps scan.tif in two renames and updates the record last. An interruption leaves either no scan.tif, or raw pixels under a record that still says corrections_applied=['shading']. library.corrected then serves raw pixels as corrected. A re-run overwrites scan.before-migrate-raw.tif (the kept corrected rendition) with the raw file. No rename retries on Windows, and KEPT has no checksum.

**Evidence (from the code):**

```text
`tiff.write(str(fresh), plain, ...)`; `os.replace(path / "scan.tif", path / KEPT)`; `os.replace(fresh, path / "scan.tif")`; then the record is updated. KEPT is not checksummed in `files`.
```

**Failure scenario:** A Ctrl-C or a sharing violation between the two os.replace calls leaves the entry unreadable until someone renames the file back by hand.

**Fix:** Copy scan.tif to KEPT first (hard link or copy), then os.replace(fresh, scan.tif) in one step. Record KEPT's sha in `files`.

<details><summary>Second reader's check</summary>

This is real, and worse than described. tools/library.py:273-284 renames scan.tif to KEPT, then fresh to scan.tif, then computes the sha, and only then writes the record. Between the two renames there is no scan.tif. Between the second rename and `_write_atomic`, which includes hashing a potentially huge TIFF, scan.tif holds raw pixels while the record still says `corrections_applied: ["shading"]` for a labelled entry. `library.corrected` returns stored pixels unchanged when 'shading' is in applied (library.py corrected, line ~+37-40). An interruption in that window therefore makes every export silently deliver uncorrected pixels. A re-run then plans the entry again (the applied branch skips the `_one_shading_explains` guard) and does `os.replace(path/"scan.tif", path/KEPT)`. That overwrites the kept corrected rendition, possibly the only one, with the raw file. KEPT is never checksummed.

</details>

<a id="changes-since-first-audit-csa-08"></a>

### CSA-08 -- Command log lists every NoDataYet poll, contrary to its docstring; scan.json and meta grow by thousands of entries per pass

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:433-497`, `rps7200/direct.py:1844-1861`, `rps7200/direct.py:2182-2184`, `rps7200/direct.py:2990-2992`, `rps7200/library.py:1045-1065`

While the host waits for the scanner to reach the next lines (most of a pass, per the read_planes docstring: 'the vendor software sees it on most of its reads'), each 20 ms poll adds a listed entry. A multi-minute pass accumulates thousands of entries in `meta['commands']`. These land in `extra.commands` in scan.json, in the spool's meta.json, in `last_scan_meta` and in the GUI's Result meta. `reindex()` re-parses every scan.json on every library.save, which for single GUI scans happens with the device open. Separately, calibrate_shading and scan() call `start()` well before the read and only `stop()` after it. An exception in warm-up or setup leaves recording on, so later status polls accumulate until the next pass starts.

**Evidence (from the code):**

```text
_CommandLog docstring: 'Image-data READs are counted, not listed'. But `except Exception as exc: entry["refused"] = type(exc).__name__; self.record.append(entry); raise` runs before the bulk-count branch, so every `NoDataYet` from `read_lines` is appended. read_planes: `except NoDataYet: ... time.sleep(poll)` with `poll: float = 0.02`. tests/test_pass_record.py covers only successful reads.
```

**Failure scenario:** A 7200 dpi pass (314 s) waits most of that time on NoDataYet, adding thousands of entries of about 100 bytes each to scan.json. Across a large library, every save's reindex reads hundreds of MB of JSON.

**Fix:** Count refused bulk READs (NoDataYet) the way successful ones are counted, e.g. `image_reads.waits`, with the first and last timestamps. Keep only non-READ refusals listed. Call stop() in a finally around the whole recorded region. Add a test with a transport that raises NoDataYet.

<details><summary>Second reader's check</summary>

`_CommandLog.command` (direct.py:476-497) appends the entry with `refused` in its `except Exception` branch before the bulk-count test. `NoDataYet` is raised by the transport itself (usb_transport.py:755) when a READ gets zero bytes. `read_planes` retries every `poll=0.02` s (1847-1860). So every wait poll during a pass becomes a listed entry in `meta['commands']`, then in `extra.commands` in scan.json and in the spool meta.json. The docstring says image READs are 'counted, not listed'. The second part is also right. scan() calls start() at ~2991, but stop() sits in the finally around `_read_pass` only (3074-3076). calibrate_shading calls start() at 2182 and stop() only after its try/finally (2345). An exception in between, including DeviceSuspect from slide(), leaves recording on until the next start().

</details>

<a id="changes-since-first-audit-csa-10"></a>

### CSA-10 -- compact() interrupted after replacing scan.tif leaves a record whose checksum no longer matches: verify raises a false corruption alarm

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:414-452`, `rps7200/session.py:1989-1995`

If rewriting prescan.tif fails (Windows sharing violation, full disk, kill), scan.tif has already been replaced by a deflated file with identical pixels but different bytes. scan.json still holds the uncompressed file's sha256, so `verify` reports 'scan.tif does not match its checksum' on a healthy entry. The `.raw.bin.gz.part` / `.scan.tif.part` temp files can also be left behind. The log message claims the entry is untouched.

**Evidence (from the code):**

```text
Order: `os.replace(temp, path / RAW_FILE)`; for scan.tif then prescan.tif `_replace_tiff(...)` and update the in-memory `record` sha; only then `_write_atomic(path / "scan.json", ...)`; then `plain.unlink()`. The session catches `(OSError, ValueError)` and logs 'it stays uncompressed and complete'.
```

**Failure scenario:** The window closes on Windows, and compact of a roll-less scan with a prescan hits a transient lock on prescan.tif. `make verify` then flags scan.tif as corrupt, and someone deletes or re-scans a good entry.

**Fix:** Write the record after each swap, or compute all replacements to temp files first and swap them together, or have verify compare pixels when a checksum mismatch coincides with a raw.bin/raw.bin.gz pair. Clean up .part files on failure.

<details><summary>Second reader's check</summary>

`compact` (library.py:414-452) replaces raw.bin.gz, then each TIFF in turn, updating only the in-memory record. The record is written once, at the end (451). If `_replace_tiff` for prescan.tif raises after scan.tif was swapped, scan.json keeps the uncompressed scan.tif's sha256. `verify` (1098-1099) then reports 'scan.tif does not match its checksum' on pixel-identical data. `.part` temp files are not cleaned up. session.py:1989-1995 logs 'it stays uncompressed and complete', which is not accurate here.

</details>

<a id="changes-since-first-audit-csa-11"></a>

### CSA-11 -- library.save can raise after the entry is fully committed (reindex); callers treat a complete entry as failed

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:397-399`, `rps7200/library.py:1030-1065`, `tools/scan.py:366-375`, `rps7200/session.py:1587-1600`

The derived index is rebuilt inside save, after the commit. Any reindex failure makes save raise even though the entry is complete. In scan.py this aborts filing of every later pass. In FrameWriter the frame is recorded as not done with a filing_error, so a resume rescans it. One damaged scan.json anywhere in the library poisons every later save. The concurrent in-place index writes can also interleave.

**Evidence (from the code):**

```text
`_write_atomic(path / "scan.json", ...)`, `(path / INCOMPLETE).unlink(...)`, `reindex(root)`. entries() catches only `(OSError, json.JSONDecodeError)` and then does `record["_dir"] = candidate.parent.name`, so a scan.json with invalid UTF-8 (UnicodeDecodeError) or a non-object JSON (TypeError) raises. reindex writes index.json with in-place `write_text` from both the FrameWriter and the debug flush thread at session close.
```

**Failure scenario:** A legacy entry has a scan.json corrupted by a disk error. Every new scan's library.save raises UnicodeDecodeError after writing its files. The window reports every frame as 'could not be filed' and stops the roll.

**Fix:** Make reindex best-effort inside save (catch and log). Catch ValueError/TypeError in entries(), and skip non-dict records. Write index.json atomically.

<details><summary>Second reader's check</summary>

`save` calls `reindex(root)` (399) after the commit. `entries()` (1030-1042) catches only `(OSError, json.JSONDecodeError)`. A UnicodeDecodeError is a ValueError, not a JSONDecodeError, so it escapes. A non-dict JSON makes `record["_dir"] = ...` raise TypeError. Either one makes every later save raise after a fully committed entry. `reindex` writes index.json in place (1064). At session close the debug flush (in close()) and the still-running FrameWriter thread can both call it concurrently.

</details>

<a id="changes-since-first-audit-csa-12"></a>

### CSA-12 -- force_abort (and any process kill) orphans the debug spool silently; nothing can file it

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1855-1875`, `rps7200/session.py:1972-1976`, `rps7200/direct.py:969-977`, `rps7200/direct.py:1134-1145`

After Force Abort, the session never calls DirectScanner.close(), so every spooled pass (metering probes, hold/aim prescans, and anything whose writer job failed) stays in $TMP/rps7200-debug-* with no log line naming it. A killed process (e.g. the harness's 10-minute kill) behaves the same way. The spool is kept for 'filing later by hand', but no tool exists for that, and temp cleaners or a reboot on tmpfs remove it.

**Evidence (from the code):**

```text
`_run` finally: `if not self.dead: self._scanner.close()`. `_debug_flush` is called only from `close()`. direct.py:969-971: 'a spool left behind ... still says what each pass was and can be filed later by hand'. grep finds no reader of `NNN-meta.json` or `rps7200-debug-` in rps7200/ or tools/.
```

**Failure scenario:** A roll wedges and the operator uses Force Abort. The hold prescans and metering probes that explain what went wrong exist only in an unnamed temp directory, which is deleted at the next reboot.

**Fix:** On force_abort, still run `_debug_flush` (the transport is closed, so the flush is safe), or at least log the spool path. Add `tools/library.py file-spool DIR`, which reads NNN-meta.json and files each pass.

<details><summary>Second reader's check</summary>

`force_abort` (session.py:1855-1875) sets `dead`, and `_run`'s finally skips `self._scanner.close()` when dead (1973). `_debug_flush` is reachable only from close() (direct.py:1145). No code reads `NNN-meta.json` or knows the `rps7200-debug-` prefix beyond its creation (grep). The spool path is never logged at capture time. The 'filed later by hand' promise at 969-971 has no tool behind it.

</details>

<a id="changes-since-first-audit-csa-13"></a>

### CSA-13 -- Calibration ends 'successfully' on any refused read; the new guard catches only the timeout

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:2254-2271`, `rps7200/direct.py:2305-2313`, `rps7200/direct.py:2323-2332`, `rps7200/direct.py:1779-1797`

The new code says that building a reference from part of a calibration 'would be a partial calibration passed off as a whole one', but it guards only the deadline case. A premature refusal (for example a one-shot UNIT ATTENTION a few blocks in) sets ended=True, and calculate_shading runs on whatever arrived. The archive and the entries then record an ordinary 'calibrated' origin.

**Evidence (from the code):**

```text
`except (EndOfData, ScanReadError): self._log(f"  scanner finished after {blocks} blocks"); ended = True; break`. read_lines raises ScanReadError for any CHECK CONDITION whose sense is not end-of-data. The descriptor's declared line total is only logged (`sum(e.get('lines', 0) for e in parms)`) and never compared with what arrived.
```

**Failure scenario:** A calibration is refused after 6 of about 40 blocks. The reference is built from about 15% of the lines, with no warning, and every scan of the session is corrected with it.

**Fix:** Compare the lines received against the descriptor's declared total. Refuse, or at least flag in `_shading_origin`, a calibration that ended short.

<details><summary>Second reader's check</summary>

calibrate_shading treats `except (EndOfData, ScanReadError)` as a normal end (2309-2312). `read_lines` with retries=1 raises ScanReadError for any non-end-of-data sense (1792-1794). The descriptor's declared line total is only logged (2255-2259) and never compared with `blocks`/`drained`. So a premature refusal builds a reference from partial data, and the origin records `action: calibrated`. The deadline guard (2323-2332) covers only the timeout case.

</details>

<a id="changes-since-first-audit-csa-15"></a>

### CSA-15 -- Demo cannot reach the new suspect, debug-claim and spool paths, and records less than a real pass

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:816-819`, `rps7200/demo.py:500-507`, `rps7200/demo.py:846-909`, `rps7200/session.py:2872-2875`

The largest behavioural changes in this range (DeviceSuspect ending a roll, spooling and claiming under RPS7200_DEBUG, calibration archiving) have no stand-in path. `make run-demo` therefore exercises none of the code in CSA-01, CSA-02 and CSA-05. Demo entries also carry a thinner record, and one refusal is replaced by a soft pass.

**Evidence (from the code):**

```text
`suspect: str | None = None` with the comment 'Nothing here can be'. DemoScanner has no `debug`, `_debug_capture` or `debug_claim`, so `getattr(self._scanner, "debug_claim", None)` is None under --demo. `ensure_shading(reuse=True)` returns 'loaded' whether or not the cache exists. `_take`'s meta lacks protocol_revision, commands, mode, shading_origin, carriage_state and filter_offsets. A stored picture with no calibration comes back uncorrected with UNCALIBRATED_SOURCE 'where the real one would have refused'.
```

**Failure scenario:** A regression in debug_claim identity matching, or in the roll's DeviceSuspect handling, ships with the demo green.

**Fix:** Let the demo inherit the driver's debug capture and claim (they are host-side), and add a way to make a demo pass abandon mid-read (e.g. a scripted failure) so `_mark_suspect` and the roll's DeviceSuspect raise are exercised. Take refusals from DirectScanner.uncalibrated rather than returning uncorrected pixels.

<details><summary>Second reader's check</summary>

`DemoScanner.suspect = None` is a fixed class attribute (demo.py:816-819). The stand-in has no `debug`, `_debug_capture` or `debug_claim`, so `getattr(self._scanner, "debug_claim", None)` in session.py:2872 is None under --demo. `ensure_shading(reuse=True)` returns 'loaded' without checking the cache (500-507), where the real one calibrates when the cache is missing. `_take`'s meta (846-909) carries no commands, mode, shading_origin, carriage_state, filter_offsets or protocol_revision. UNCALIBRATED_SOURCE returns uncorrected pixels where DirectScanner would raise `uncalibrated`. The new suspect, spool and claim paths are therefore unreachable from `make run-demo`.

</details>

<a id="changes-since-first-audit-csa-16"></a>

### CSA-16 -- settings.load moves a valid settings file aside on any transient read error and silently reverts the window to defaults

**Severity** low · **Category** user-error · **Verdict** partly

**Where:** `rps7200/settings.py:68-77`, `rps7200/settings.py:88-101`, `rps7200/settings.py:104-119`, `tools/gui.py:388`

settings.load treats a transient OSError like corruption. If the rename aside also fails, which is likely under the same lock, the window opens on defaults without saying so and the next save overwrites the intact settings file, so it is lost rather than set aside. Two asides within one second collide.

**Evidence (from the code):**

```text
`except (OSError, json.JSONDecodeError, ValueError): _keep_aside(target); return blank`. `_keep_aside` does `target.replace(aside)`, with the aside name at 1-second resolution. The GUI calls `self.remembered = settings.load(settings_path)` and is not told the file was moved.
```

**Failure scenario:** On Windows, Defender holds gui-settings.json at launch. The window opens with default film/IR/output-folder, the operator scans with the wrong settings, and the sheet's unsaved decisions appear to be gone.

**Fix:** Set a file aside only on parse errors, not on OSError other than FileNotFoundError, which should get a short retry instead. Return or log the aside path so the GUI can say so. Make the aside name unique.

<details><summary>Second reader's check</summary>

`load` treats any OSError other than FileNotFoundError like corruption and calls `_keep_aside` (settings.py:72-74). The aside name has 1-second resolution (95-96). The GUI is not told. The description says 'Nothing is lost on disk', but `_keep_aside` swallows its own OSError and returns None. If the file is held so it cannot be read, the rename usually fails too. The window then runs on defaults, and the next `save` (116) replaces the good file, which loses it. The Defender-at-read scenario is itself unlikely, because scanners normally share-read.

</details>

<a id="changes-since-first-audit-csa-a1"></a>

### CSA-A1 -- Transport distances are still millimetres throughout the driver and tools, contrary to CLAUDE.md's 'Millimetres are prohibited' rule; new code in this range adds more

**Severity** low · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3569-3570`, `rps7200/direct.py:3599`, `rps7200/direct.py:3611`, `rps7200/session.py:1204`, `tools/scan_roll.py:117-120`, `tools/scan_roll.py:541-551`, `rps7200/demo.py:417`

**Doc claim:** CLAUDE.md 'Do not use millimetres for transport distances': 'Millimetres are prohibited ... express every sub-frame distance in units of the adjustment parameter'

CLAUDE.md ('Do not use millimetres for transport distances') says every sub-frame distance must be held in units of the SLIDE param, because mm conversions have hidden two mistakes. The executable API still takes and records mm everywhere: nudge, plan_nudges, the --nudge CLI flag, registration/approved `offset_mm` in manifests and records, and the demo's film position. The fixes in this range extended the mm-based state rather than moving off it. This is not a regression in behaviour, but the rule claims a state of the code that does not exist.

**Evidence (from the code):**

```text
`STEP_MM = MM_PER_UNIT`, `OVERHEAD_MM = MM_PER_COMMAND` (direct.py:3569-3570); `def param_for_mm(millimetres: float)` (3599); `def nudge(self, millimetres: float)` (3611); `def plan_nudges(millimetres: float)` (session.py:1204); `ap.add_argument("--nudge", type=float, ... help="move the film this many mm ...")` (scan_roll.py:117-118); `print(f"offset the film by {sent:+.3f} mm ...")` (551). The diff 83dbb22..03aacba adds `self._film_mm`/`self._owed_mm` in demo.py and `offset_mm` fields written by the GUI.
```

**Failure scenario:** An operator or agent passes `--nudge 2` believing it is in units, as CLAUDE.md implies every distance is. They get 2 mm, about 700 units, which `plan_nudges` spreads over several commands. Or a stored `offset_mm` is read as units.

**Fix:** Either migrate the transport API and persisted offsets to param units (keeping mm only for display), or amend CLAUDE.md to say which interfaces still take mm and why.

<a id="changes-since-first-audit-csa-18"></a>

### CSA-18 -- PROTOCOL_REVISION correctly unchanged

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/protocol.py:40`, `rps7200/direct.py:2932-2963`, `rps7200/direct.py:2994-3063`

Every pass that still runs sends the same commands in the same order as before, so the lack of a bump is right. Noted because the task asked for this to be checked.

**Evidence (from the code):**

```text
`PROTOCOL_REVISION = 6` at both 83dbb22 and 03aacba. The only change to the command sequence is removing the in-pass `self.calibrate_shading()` fallback (now `raise self.uncalibrated(reason)` before anything is sent). The shading and suspect checks, film on prescans, idle timeouts and the command log are all host-side.
```

**Failure scenario:** None.

**Fix:** None.

<details><summary>Second reader's check</summary>

`PROTOCOL_REVISION = 6` at protocol.py:40, both in 83dbb22 and now. The command-relevant lines in the direct.py diff are the removal of the in-pass `self.calibrate_shading()` fallback and the move of start_scan/get_ccd_mask/get_parameters into `_read_pass` in the same order (old direct.py ~2655 vs new 3248-3270). The suspect checks, command log, idle timeouts and archive are host-side. No command change needs a bump.

</details>

<a id="changes-since-first-audit-csa-a2"></a>

### CSA-A2 -- 'Written uncompressed' calibration archive and plain window entries still run savez_compressed with the device open

**Severity** info · **Category** doc-mismatch · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:749-760`, `rps7200/direct.py:783-784`, `rps7200/direct.py:735-746`, `rps7200/direct.py:980-989`, `rps7200/library.py:267-268`, `rps7200/shading.py:91`

**Doc claim:** rps7200/direct.py:757-758 'Written uncompressed: the device is still open'; CLAUDE.md:185-190

The payload is a few hundred kB, so the risk is small. But the docstring and CLAUDE.md say that nothing is compressed with the device open and idle, and a small compressed write does happen after every calibration, every window single scan, and the first debug-spooled pass of each reference.

**Evidence (from the code):**

```text
archive_calibration docstring: 'Written uncompressed: the device is still open ...'; then `result["reference"].save(folder / "shading.npz")`, and ShadingReference.save is `np.savez_compressed(path, **arrays)` (shading.py:91). `save_shading` likewise compresses the cache. `library.save(compress=False)` still calls `reference.save(path / "shading.npz")` (267-268). The debug spool saves `NNN-shading.npz` compressed from inside scan() (984-985).
```

**Failure scenario:** None observed. Noted because the wedge rule is stated in absolute terms and the code does not follow it absolutely.

**Fix:** Use np.savez (uncompressed) on those device-open paths, or reword the docstrings to 'nothing large is compressed'.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry record | library/<id>/scan.json (temp .scan.json.part) | JSON (indent=2, default=str): image{file,shape,dtype,sha256,corrections_applied}, raw{file: raw.bin\|raw.bin.gz, bytes, sha256, layout incl. lines_received}, scan{SCAN_FIELDS incl. stagger_realigned, read_direction, carriage_state, fast_infrared, filter_offsets}, extra{commands, mode, shading_origin, started_utc, roll_membership, bracket_*, demo, demo_source, ...}, device (INQUIRY), device_settings, metering, registration, calibration{shading, ccd_mask, report, skipped}, prescan{file, read_direction, carriage_state}, files{sha256 of shading.npz, ccd_mask.bin, prescan.tif}, provenance | metadata | library.save via _write_atomic (library.py:397); compact (451); add_tags (499); migrate_direction (895); tools/library.py migrate-raw (283) | library.entries/verify/reconstruct/load/corrected, tools/library.py, GUI roll lookup (gui.py:5498), collect_vignette_study | Mostly. Non-JSON values are stringified (default=str). extra.commands lists every NoDataYet poll (CSA-08). The rename has no Windows retry (CSA-06). |
| In-progress marker | library/<id>/INCOMPLETE | text | n/a | library.save at start (after _reserve); removed after scan.json | library.verify | yes (a presence flag) |
| Raw bytes as received | library/<id>/raw.bin.gz, or library/<id>/raw.bin (window single scans before compact; temp .raw.bin.gz.part) | index-format lines (2-byte tag + bytes_per_line), gzip level 6 or plain | raw | library.save (compress True/False); library.compact converts plain→gz after checking sha256 | library.read_raw (prefers .gz when both exist), decode_raw, reconstruct, verify, demo | yes, byte-exact with sha256 over the uncompressed bytes |
| Raw decode | library/<id>/scan.tif | TIFF uint8/uint16 (H,W,C); uncompressed when compress=False, else deflate; at 7200 dpi includes the 4-row stagger realignment recorded as scan.stagger_realigned | raw (unless image.corrections_applied says otherwise) | library.save; compact/_replace_tiff; migrate-direction; tools/library.py migrate-raw | library.load/corrected/reconstruct/verify, make_comparison, GUI | Pixels exact. File bytes change on compact, which can leave a stale sha (CSA-10). |
| Frame prescan inside a roll-frame entry | library/<id>/prescan.tif | TIFF uint8 (H,W,3) | corrected (prescan() return), not declared in the record | library.save(prescan=rf.prescan / frame.prescan) | migrate_direction, GUI | No: corrected, with no raw bytes and no matching mask (CSA-14). |
| Shading reference and CCD mask of the pass | library/<id>/shading.npz, library/<id>/ccd_mask.bin | np.savez_compressed float arrays; raw mask bytes | reference = reduction of calibration bytes; mask exact | library.save from capture_record() | library.corrected/reconstruct/verify (checksums in files{}) | The mask is exact; the reference is exact as a reduction, not the calibration's own bytes. |
| Calibration archive | <reference dir>/<YYYYMMDDTHHMMSSZ[-n]>/{data.bin, ccd_mask.bin, shading.npz, calibration.json} (default calibration/, relative to CWD) | raw calibration lines (16-bit + tags); JSON with width, bpl, sha256, commands, protocol_revision | raw | DirectScanner.archive_calibration via ensure_shading (direct.py:749-804) | nothing (no verify, no reconstruct) | The bytes are exact, but the write is not atomic, nothing verifies it, and reuse does not link to it (CSA-09). |
| Cached shading reference | calibration/shading.npz (temp .shading.part.npz) | np.savez_compressed | reduction | DirectScanner.save_shading (os.replace) | load_shading (--reuse, 'Use the cached one'), GUI prompt (mtime) | yes, as a reduction; its provenance is only the file mtime |
| Debug spool | $TMP/rps7200-debug-*/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz, NNN-ccd_mask.bin | npy raw pixels, raw bytes, JSON meta+layout | raw | DirectScanner._debug_capture after every pass when debug is on (direct.py:908-998) | _debug_flush at close() only (meta.json read by nothing) | Yes, but it grows for the whole session (CSA-01), is deleted before the caller files (CSA-02), and is orphaned on force_abort (CSA-12). |
| Roll manifests | rolls/<roll>/roll.json \| survey.json (+ .bak, .legacy, .unreadable[-n], .part) | JSON: roll, numbering, settings, wanted, frames[{number, index, transport_position, registration, error, done, entry, file, filing_error, prescan, prescan_rotation/flipped, exposure...}] | metadata | RollManifest via write_manifest (atomic, fsync, PermissionError retries) from the scanning and writer threads | earlier_manifest/read_manifest/renumbered, GUI reopen, scan_roll --approved | yes |
| Roll deliverables | rolls/<roll>/frameNN.tif, prescanNN.tif, prescanNN-before.tif | TIFF (deflate via export.write / tiff.write) | corrected, oriented | FrameWriter/export.write, scan_roll.py tiff.write | GUI read_survey, scan_roll --approved (unoriented via prescan_arrangement) | n/a (derived) |
| Output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng) | TIFF deflate or JPEG + DNG | corrected | FrameWriter._write via export.write, also for single scans with the device open (CSA-07) | operator / NegPy | n/a |
| Library index | library/index.json | JSON summary | derived | library.reindex (in-place write_text, called inside every save) | informational | derived; non-atomic, concurrent writers possible (CSA-11) |
| migrate-raw backup | library/<id>/scan.before-migrate-raw.tif | TIFF | corrected (the old rendition) | tools/library.py migrate-raw --write | nothing (record image.replaced names it) | yes, but not checksummed |
| Window settings | gui-settings.json (+ .part, .unreadable-<stamp>) | JSON | n/a | settings.save (atomic replace); settings._keep_aside | settings.load → GUI | yes; moved aside on transient errors (CSA-16) |

**Second reader's corrections to this table:**

- **Library `shading.npz`.** It is always np.savez_compressed, including on window entries filed with compress=False. So a "plain" entry is not wholly uncompressed while the device is open. See library.py:267-268 and shading.py:91.
- **Calibration archive.**
  - `shading.npz` there is compressed, which contradicts the archive's "Written uncompressed" docstring.
  - `calibration.json` is written in place with no INCOMPLETE marker.
  - The root is `path.parent` of whatever reference path the caller passes: the default is `calibration/` relative to CWD.
- **Cached shading reference.** `save_shading` compresses it with the device open. Its temp file is `.shading.part.npz`, as claimed.
- **Debug spool.**
  - `NNN-shading.npz` is written once per reference, not once per pass, and compressed.
  - It is removed only by rmtree of the whole spool, never by `_debug_unlink`.
  - A claimed pass's files persist until close(). This is the core of CSA-01.
- **`prescan.tif` in roll-frame entries.** It is the corrected prescan() output, and its record block has no correction flag. The entry's `ccd_mask.bin` belongs to the frame pass, not the prescan. Walk prescans, by contrast, are filed as their own raw entries with bytes (session.py:2563, scan_roll.py:662-685).
- **`scan.before-migrate-raw.tif`.** It is overwritten, by the raw file, if migrate-raw is re-run after an interrupted swap (CSA-17). It is not simply "exact but unchecksummed".
- **`gui-settings.json`.** If the rename aside fails as well, no `.unreadable` file is produced and the next save overwrites the intact file (CSA-16).
- **`index.json`.** It is also rewritten by tools/library.py migrate-raw (`library.reindex(root)`). Its writers can run concurrently: the debug flush inside close() and the FrameWriter thread before `finish()`.
- **Output-folder copies and roll deliverables.** The window's Save all and Export rolls (gui.py `_start_writing`) also write them, re-corrected from the library, with the session's device open.

## What the operator can do

- Run tools/scan.py or tools/scan_roll.py with RPS7200_DEBUG=1. Each pass the tool files itself is claimed, and metering probes and hold/aim prescans are filed by debug filing at close.
- Press Ctrl-C once in tools/scan.py or tools/scan_roll.py: the pass in flight completes. A bracket stops after the current pass, and a roll stops before the next advance.
- Continue after an abandoned pass only by power-cycling and starting a new process or window. Every scan, film move or calibration is refused with DeviceSuspect.
- Resume a failed roll with `--roll <folder name>` or `--out <dir>` plus `--start-at N`. roll.json is carried forward and re-taken frames replace their records.
- Pass `--approved <walk folder>` to scan_roll.py. The prescan resolution is pinned to the walk's, and a conflicting --prescan-dpi is refused.
- Tag deliberately uncalibrated entries with `tools/library.py tag ID --add uncalibrated-on-purpose` so verify stops reporting them.
- Run `tools/library.py duplicates --delete`. Only entries whose raw bytes (or image hash) are identical are offered.
- Quit the window mid-job and choose 'Yes: stop after the frame in flight' or 'No: let everything queued finish'.
- Regenerate the comparison files from any library entry with tools/make_comparison.py <entry>. Entries whose correction state is not 'applied' are refused.

## What the operator should not do

- Press Ctrl-C a second time during a read: the pass is abandoned, the device is marked suspect and needs a power cycle.
- Press Ctrl-C while scan.py or scan_roll.py is filing after the scanner closed: queued passes or frames are lost (CSA-04).
- Run long 3600 or 7200 dpi sessions in the window, or rolls, with RPS7200_DEBUG=1 on a small or tmpfs temp volume: the spool grows by the full size of every pass until close (CSA-01).
- Use Force Abort unless a power cycle is acceptable: the debug spool is left unfiled in temp (CSA-12).
- Keep working in the same window after a DeviceSuspect error: every job is refused until the window is restarted after a power cycle.
- Move or copy library/ without calibration/: the calibration archive lives outside the library and is referenced by a CWD-relative path (CSA-09).
- Interrupt `tools/library.py migrate-raw --write` or the window's close-time compaction (CSA-10, CSA-17).

## Mistakes nothing guards against

- A first Ctrl-C during calibration or metering in tools/scan.py is acknowledged as 'stopping after the pass in flight', but the real scan then starts anyway. The natural second Ctrl-C wedges the scanner (CSA-03).
- A first Ctrl-C during a long seek or rewind in scan_roll.py still leads to a full 3-4 minute calibration before the roll stops (CSA-03).
- After a suspect device, pressing Scan or Prescan in the window still sends MODE SELECT, gain/offset and frame writes before refusing, with a message about a 'film move (init)' (CSA-05).
- With an output folder set, single window scans compress their delivered copy while the device is open and idle, despite the documented rule (CSA-07).
- A full or unplugged library drive while debug is on: the claimed pass's debug copy is deleted at close before the failed filing is noticed, so the pass is lost (CSA-02).
- Launching the window while gui-settings.json is briefly locked moves it aside and opens silently with default settings (CSA-16).
- Choosing 'Use the cached one' gives entries no link back to the calibration bytes the reference came from (CSA-09).

## Dataflow notes

Bytes in:
- Every command goes through `_CommandLog.command` (direct.py:474-497), which wraps the Transport (direct.py:573).
- It records the command list while a pass is being recorded: from `scan()` direct.py:2990-2992 to the `finally` at 3074-3076, and in `calibrate_shading` 2182-2184 / 2345-2346.
- `_read_pass` (3237-3304) sends START SCAN (start_scan 1605, refused if suspect) and reads the CCD mask and GET PARAMETERS.
- `read_planes` (1799-1913) paces reads on NoDataYet with a 20 ms poll and a per-pass idle limit (`read_idle_s`: 120 s, or 287 s for untied IR).
- It sets `_read_complete` once every line is in (1881), stores `last_raw`/`last_raw_layout` or clears them (1882-1903), then `decode_index` (1923-1971) turns a bottom-up read upright.
- Any exception before `_read_complete` sets `suspect` (3289-3300).

Transform in scan() (2842-3235):
- Shading and correctability refusals come before any command (2935-2963).
- Metering probes are separate `scan()` calls (2965-2984).
- At 7200 dpi the stagger is realigned and `stagger_realigned` recorded (3089-3097). `raw_pixels` is the uncorrected decode; `apply_shading(image, _shading, ccd_mask)` produces the returned image (3128-3144).
- The meta adds `commands`, `mode`, `started_utc` and `shading_origin` (3198-3214).
- `_debug_capture(raw_pixels, meta)` (3222 → 908-998) spools pixels, raw bytes, meta, reference and mask when debug is on. Then `last_pixels_raw` and `last_scan_meta` are set.

Calibration:
- `calibrate_shading` (2128-2381) reads 16-bit lines until end-of-data or refusal, marks suspect on a timeout or exception, then runs `calculate_shading` and sets `_shading_origin`.
- `ensure_shading` (806-887) calls `archive_calibration` (749-804) to write calibration/<stamp>/ with the device open, then `save_shading` (736-747, atomic).

Out, window:
- `ScanSession._scan`/`_prescan`/`_roll` call `_file` (2781-2906). It reads `capture_record()` at once, drops mismatched raw bytes via `raw_bytes_disagree` (734-766), calls `debug_claim(raw_image)`, and submits to FrameWriter.
- `FrameWriter._write` (1618-1703) runs `library.save` first: compress=False for single jobs, True for rolls. Then it writes the delivered copies via `export.write`, which are compressed.
- `RollManifest.record`/`filed` (1107-1159) mark frames done only when the writer confirms.
- On session end (1968-1996): `scanner.close()` runs `_debug_flush` (files unclaimed passes, deletes claimed ones), then `writer.finish()`, manifest saves and `library.compact` of the plain entries.

Out, CLI:
- tools/scan.py holds passes in `pending` (claimed), closes the device, then calls `library.save` for each (366-375).
- tools/scan_roll.py submits frames (and walk prescans) to FrameWriter with `on_filed` → `RollManifest.filed`, closes the device, then calls `writer.finish()` (803).

Library:
- `library.save` reserves a directory with `mkdir`, writes INCOMPLETE, scan.tif / prescan.tif / shading.npz / ccd_mask.bin / raw.bin(.gz), checksums them into `files`, renames scan.json into place, removes INCOMPLETE, then `reindex`.
- `read_raw` accepts plain or gzipped bytes. `decode_raw` and `reconstruct` replay the stagger realignment from the record (`stagger_lines`, `_replay`).
- `verify` checks INCOMPLETE directories, record ids, per-file checksums and raw sha. It does not look at calibration archives.

Demo:
- `DemoScanner` substitutes at `session._open_scanner`. It borrows `scan_roll`, `auto_exposure`, the hold and aim loops, and the refusal factories from DirectScanner.
- It has no suspect path, no debug capture and claiming, and no calibration archive.
