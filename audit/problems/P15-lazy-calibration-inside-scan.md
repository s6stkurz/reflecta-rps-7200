# P15 -- `--no-shading` does not skip calibration; the stalling lazy path is live

**Severity** high · **Group** B: Scanner safety -- wedges, abandoned reads, calibration · **Reported independently by** 8 findings in 6 areas

[Back to the summary](../README.md)

## The problem

When a pass wants shading and the session has no reference, `scan()` calibrates lazily
*inside the pass*, a path the code's own comments record as stalling with
`LIBUSB_ERROR_PIPE`. `tools/scan_roll.py --no-shading` only skips the up-front
calibration; `scan_roll` has no `shading` parameter, so the first prescan and every
metering probe (always `shading=True`) calibrate lazily. The same happens after a failed
Calibrate in the window (a queued Scan runs anyway; `calibrated` is set optimistically),
and after a calibration that yielded no reference (`scan.py` prints "scans will be raw",
then the next scan recalibrates). The calibration read loop can also end on its 300 s
deadline mid-pass and silently build a reference from partial data.

## Fix plan

1. Remove the lazy calibration from `scan()`: no reference + `shading=True` raises
   `ShadingUnavailable`; the caller decides.
2. Thread `shading` through `scan_roll`, `prescan` and metering.
3. Calibration that ends early raises instead of reducing partial data.
4. The window sets `calibrated` only from the `calibrated` event.

## Evidence (from [TP-06](../areas/transport-protocol.md#transport-protocol-tp-06))

**Where:** `rps7200/direct.py:2560-2602`, `tools/scan_roll.py:430-446`, `tools/gui.py:1929-1932`, `rps7200/session.py:1381-1399`

```text
direct.py:2579-2592: `if self._shading is None or needed > self._shading.pixels_per_line: ... self._log(f"calibrating before scanning ({reason})"); self.calibrate_shading()`. tools/scan_roll.py:436-440: 'Measured twice on this machine, that lazy calibration stalls: `bulk read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE`, right after the shading descriptor, and the device stops answering.' gui.py:1930-1932: `self.session.submit(Calibrate(...)); self.calibrated = True` -- set on submit, before the job has run.
```

**Failure scenario:** The operator presses Calibrate and then Prescan. The calibration raises (lamp timeout, refused start) or ends with no usable reference. The queued Prescan calls scan(shading=True), which calls calibrate_shading() from inside the scan setup, and the first calibration READ fails with LIBUSB_ERROR_PIPE. The device stops answering and needs a power cycle.

**Second reader's check:** scan() still calls `self.calibrate_shading()` whenever shading=True and `self._shading is None or needed > pixels_per_line` (direct.py:2579-2592). prescan() always passes shading=True (direct.py:1703-1714). GUI on_calibrate sets `self.calibrated = True` as soon as it submits (gui.py:1930-1932), and _needs_calibration returns False when calibrated is set (gui.py:1955), so a Prescan or Scan pressed before the 'calibrated' event arrives (gui.py:3236) is queued behind a Calibrate that may fail or run with mode 'off'. It then reaches the lazy path. More callers depend on this path than the finding lists: tools/hold_probe.py:133-136 prints '(a calibration runs first if this session has none)' and prescans with no ensure_shading; transport_truth.py:125 and roll_registration_walk also prescan without ensuring shading; and uniformity.py:703-706 runs auto_exposure (shaded scan() probes) on a session that has no reference. The stall evidence is a comment in tools/scan_roll.py:436-440, not something observable in code, but the path is plainly reachable.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-06](../areas/transport-protocol.md#transport-protocol-tp-06) | transport-protocol | high | confirmed | The lazy calibration inside scan(), recorded as stalling with LIBUSB_ERROR_PIPE, is still reachable, including from a GUI job queued behind a failed Calibrate | rps7200/direct.py:2560-2602, tools/scan_roll.py:430-446, tools/gui.py:1929-1932 |
| [CLI-05](../areas/cli-operator-tools.md#cli-operator-tools-cli-05) | cli-operator-tools | high | confirmed | scan_roll.py --no-shading does not skip calibration: it moves it into the lazy in-prescan path the tool says stalls the device, and frames are still corrected | tools/scan_roll.py:145-146, tools/scan_roll.py:166-173, tools/scan_roll.py:433-445 |
| [D05](../areas/docs-readme-claude.md#docs-readme-claude-d05) | docs-readme-claude | high | confirmed | --no-shading does not skip calibration: prescan(), metering probes and roll frames calibrate lazily, on the path the code itself says stalled the device | tools/scan_roll.py:145-146, tools/scan_roll.py:166-173, tools/scan_roll.py:433-446 |
| [UT-01](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-01) | uncited-tests-vs-findings | high | confirmed | No test covers --no-shading still calibrating lazily; test_scan_roll_calibration claims the path is fixed, and test_roll pins the lazy call | tests/test_scan_roll_calibration.py:10-15, tests/test_scan_roll_calibration.py:59-70, tests/test_scan_roll_calibration.py:113-115 |
| [CLI-A1](../areas/cli-operator-tools.md#cli-operator-tools-cli-a1) | cli-operator-tools | medium | found-by-verifier | A calibration that yields no reference prints 'scans will be raw', then the next scan silently recalibrates on the lazy in-scan path | rps7200/direct.py:612-626, rps7200/direct.py:1945-1950, rps7200/direct.py:2582-2594 |
| [DOC-16](../areas/docs-plans-todo.md#docs-plans-todo-doc-16) | docs-plans-todo | medium | confirmed | The window-roll lazy-calibration TODO item is partly stale, but a race remains: `calibrated` is set True before the Calibrate job succeeds | TODO.md:688-703, tools/gui.py:1929-1933, tools/gui.py:1946-1958 |
| [TP-12](../areas/transport-protocol.md#transport-protocol-tp-12) | transport-protocol | medium | confirmed | The calibration read loop can end silently mid-calibration and lets non-ScanReadError failures escape | rps7200/direct.py:1915-1944, rps7200/direct.py:830-863, rps7200/direct.py:1398-1438 |
| [DDF-12](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-12) | decode-and-debug-filing | medium | confirmed | calibrate_shading's 300 s deadline can end the read loop mid-pass and silently build a reference from partial data | rps7200/direct.py:1915-1944, rps7200/direct.py:1946-1959 |
