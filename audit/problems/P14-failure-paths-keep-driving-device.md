# P14 -- After a failed or interrupted read, the software keeps talking to the device

**Severity** critical · **Group** B: Scanner safety -- wedges, abandoned reads, calibration · **Reported independently by** 12 findings in 5 areas

[Back to the summary](../README.md)

## The problem

- After a `UsbError` mid-read, `tools/scan_roll.py` advances the film and scans the next
  frame; the session accepts the next job; there is no "device may be mid-scan" state.
- `byte14_probe` runs a whole extra scan in its `finally` after any failure, Ctrl-C
  included.
- Ctrl-C / SIGTERM in `scan_roll.py` skips `writer.finish()`: queued frames die with the
  daemon writer thread, and the read is abandoned mid-scan -- the documented wedge.
- `tools/scan.py` holds every pass in memory and files after the `with` block, so an
  exception or Ctrl-C mid-bracket loses every pass already scanned.
- The session's worker and writer are daemon threads; exiting the main thread abandons a
  read. `force_abort` closes libusb from the UI thread while the worker is inside a
  transfer. A dead worker leaves a window that accepts jobs that never run.
- `read_planes` gives a CHECK CONDITION one attempt; a silent pause mid-payload raises at
  once after `clear_halt`.

## Fix plan

1. A `DeviceSuspect` state on `DirectScanner`: set on any read failure; while set, refuse
   every command except READ STATE / REQUEST SENSE, and tell the operator to power-cycle.
2. Ctrl-C handling in the CLIs: finish the current read (or let it time out) and always
   run `writer.finish()` in `finally`; never start new device work in `finally`.
3. `tools/scan.py`: file each pass right after the device is closed per pass group, or
   spool to disk as debug does, so an exception loses nothing already read.
4. Non-daemon writer thread, joined on exit; the window shows a dead worker as an error.

## Evidence (from [TP-05](../areas/transport-protocol.md#transport-protocol-tp-05))

**Where:** `rps7200/direct.py:2655-2687`, `rps7200/direct.py:3662-3686`, `rps7200/session.py:1313-1331`, `rps7200/direct.py:1440-1500`, `tools/byte14_probe.py:172-186`

```text
scan(): `except BaseException: self._scanning = False; raise` (2679-2685) -- no drain, no marker. scan_roll: `except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc: failures += 1 ... yield RollFrame(...error...)` then falls through to `index += 1 ... if not keep_going(): return` -> `self.advance()` (3666-3686). read_planes raises ScanReadError after `idle_timeout` (1494-1498) and TimeoutError after `timeout` (1478-1481), both mid-scan. The session worker catches every exception and immediately takes the next job (session.py:1319-1331). `_scanning` is written but never read. byte14_probe's finally starts a new scan after a KeyboardInterrupt or CheckCondition.
```

**Failure scenario:** On an unattended 38-frame roll, frame 7's read stalls and raises UsbError. scan_roll yields the failed frame, sends SLIDE 04 01 00 01 to a device still mid-scan, then prescan and scan commands, three times over, each waiting out 30 s control timeouts. At best the rest of the roll is wasted; at worst the film is moved in an undefined state. In the GUI, 'failed' is shown, the operator presses Scan again, and the device (probably wedged) gets a full new sequence.

**Second reader's check:** scan() does `except BaseException: self._scanning = False; raise` (direct.py:2679-2685). scan_roll catches (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError), yields an error RollFrame and falls through to `index += 1 ... if not keep_going(): return`, and keep_going() calls `self.advance()`, a SLIDE_NEXT (direct.py:3433-3442, 3662-3686). read_planes raises ScanReadError on idle and TimeoutError on deadline mid-pass (1478-1498). The ScanSession worker catches every Exception and takes the next job (session.py:1319-1331). A grep shows `_scanning` is only ever assigned, never read. Several exceptions in the tuple (ShadingUnavailable before the pass, CalibrationRequired from start_scan) are raised outside the read, so the recommended classification of 'raised between start_scan and end of read' is the right one.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-05](../areas/transport-protocol.md#transport-protocol-tp-05) | transport-protocol | high | confirmed | After a transport failure mid-read, the roll, the session and the tools keep sending commands (SLIDE NEXT, new scans) to a device left mid-scan | rps7200/direct.py:2655-2687, rps7200/direct.py:3662-3686, rps7200/session.py:1313-1331 |
| [CLI-01](../areas/cli-operator-tools.md#cli-operator-tools-cli-01) | cli-operator-tools | critical | confirmed | scan_roll.py: Ctrl-C / SIGTERM skips writer.finish(); queued frames die with the daemon writer, and the read is abandoned mid-scan | tools/scan_roll.py:352-353, tools/scan_roll.py:587-605, rps7200/session.py:1043 |
| [CLI-02](../areas/cli-operator-tools.md#cli-operator-tools-cli-02) | cli-operator-tools | critical | confirmed | scan.py files nothing until after the session: any exception or Ctrl-C mid-bracket loses every pass already scanned | tools/scan.py:174-194, tools/scan.py:205-235, tools/scan.py:237-246 |
| [PA-05](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-05) | probe-and-analysis-tools | high | confirmed | Cleanup paths keep talking to a device that may have an abandoned read; byte14_probe runs a whole extra scan in `finally` | tools/byte14_probe.py:172-192, tools/exposure_probe.py:312-333, tools/gain_probe.py:207-231 |
| [MSQ-09](../areas/measure-scan-quality-skill.md#measure-scan-quality-skill-msq-09) | measure-scan-quality-skill | high | confirmed | byte14_probe starts a full scan in its finally block after any failure, including Ctrl-C or a USB error during a read | tools/byte14_probe.py:172-192, tools/byte14_probe.py:140-151 |
| [TP-19](../areas/transport-protocol.md#transport-protocol-tp-19) | transport-protocol | medium | confirmed | Ctrl-C during tools/scan_roll.py loses queued frames and closes the transport under the read | tools/scan_roll.py:342-353, tools/scan_roll.py:587-605, rps7200/direct.py:777-794 |
| [TP-A3](../areas/transport-protocol.md#transport-protocol-tp-a3) | transport-protocol | medium | found-by-verifier | tools/scan.py holds every pass in memory and files them only after the with-block, so an exception mid-bracket loses all completed passes | tools/scan.py:159-235, tools/scan.py:237-247 |
| [SR-18](../areas/session-roll.md#session-roll-sr-18) | session-roll | medium | confirmed | Daemon worker threads: Ctrl+C or any main-thread exit abandons a read mid-scan and drops queued frames; closing the window mid-roll waits for the whole roll | rps7200/session.py:1211, rps7200/session.py:1043, tools/gui.py:3039-3068 |
| [TP-10](../areas/transport-protocol.md#transport-protocol-tp-10) | transport-protocol | medium | partly | force_abort closes the libusb handle and context from the UI thread while the worker is inside a transfer; a crash also loses queued library entries | rps7200/session.py:1224-1244, rps7200/usb_transport.py:558-567, rps7200/session.py:1043 |
| [TP-A2](../areas/transport-protocol.md#transport-protocol-tp-a2) | transport-protocol | medium | found-by-verifier | read_planes gives a CHECK CONDITION a single attempt, so one queued one-shot sense aborts a pass mid-scan | rps7200/direct.py:1473, rps7200/direct.py:1419-1437, rps7200/direct.py:830-848 |
| [TP-08](../areas/transport-protocol.md#transport-protocol-tp-08) | transport-protocol | medium | confirmed | A mid-payload pause is waited out only if the device sends a zero-length packet; a silent pause raises at once, after clear_halt, abandoning the read | rps7200/usb_transport.py:636-661, rps7200/usb_transport.py:743-770, rps7200/usb_transport.py:72-81 |
| [SR-19](../areas/session-roll.md#session-roll-sr-19) | session-roll | medium | confirmed | A dead worker leaves the window accepting jobs that never run; an unvalidated Calibrate.mode kills the worker | rps7200/session.py:1214-1218, rps7200/session.py:1293-1346, rps7200/session.py:1318 |
