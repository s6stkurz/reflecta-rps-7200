# P27 -- The demo accepts what the scanner refuses and runs its own roll loop

**Severity** high · **Group** D: The demo is not yet the real software with different inputs · **Reported independently by** 14 findings in 8 areas

[Back to the summary](../README.md)

## The problem

- `DemoScanner` accepts scans above 3600 dpi that `DirectScanner` refuses with
  `ShadingUnavailable`, ignores `shading=False` (always corrects), and its `position()`
  never returns `None`.
- `DemoScanner.scan_roll` is its own loop that borrows helpers from `DirectScanner` but
  diverges in failure handling (no per-frame `except`, no `max_failures`), end detection
  (no blank-contrast end), prescan resolution (always 300 dpi -- `prescan_resolution` is
  swallowed by `**kw`), stop handling (not passed to hold/aim), metering (none), and the
  fields it yields.
- Scans ignore where the film was moved (only prescans are shifted); an RGBI request can
  return 3 channels; demo prescans are uint16 labelled depth 8.
- The parity tests check constants only, never the loop or the filing.

## Fix plan

1. Make `DemoScanner` a *transport* stand-in rather than a scanner stand-in: subclass or
   wrap `DirectScanner` and replace only the USB layer (`usb_transport`) with a fake that
   answers CDBs from library entries. Then `scan_roll`, refusals, metering and shading are
   the real code by construction.
2. Until then: delegate `scan_roll` to `DirectScanner.scan_roll` with the demo's
   `prescan`/`scan`, and take refusals from the same checks
   (`_shading_columns_needed`).
3. A parity test that runs the same roll through both loops with fakes and compares the
   yielded frames' fields.

## Evidence (from [DP-04](../areas/demo-parity.md#demo-parity-dp-04))

**Where:** `rps7200/direct.py:2560-2578`, `rps7200/direct.py:1615`, `rps7200/demo.py:530-597`, `tools/gui.py:118`, `tools/gui.py:1748-1752`, `tools/gui.py:2781-2787`

```text
direct.py: `needed = self._shading_columns_needed(frame, resolution)`; `if needed > self.MAX_SHADING_COLUMNS: raise ShadingUnavailable(... 'Scan at 3600 dpi or below, or pass shading=False ...')`. FULL_FRAME = (0,0,10343,6887), so at 7200 dpi needed = 10344 > 5172. DemoScanner.scan has no such check; it resamples via `_shape_for(dpi)` / `_rescale`. GUI: `DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)`, `_dpi()` accepts 25..7200, and the sheet accepts `25 <= dpi <= 7200`.
```

**Failure scenario:** The operator tries 7200 dpi in make run-demo. The roll completes, frames are filed and the ETA looks sane. They then commission the same roll on the scanner and every frame fails with 'cannot be corrected at all', after calibration and prescans have spent minutes.

**Second reader's check:** DirectScanner.scan raises ShadingUnavailable when _shading_columns_needed(frame,res) > MAX_SHADING_COLUMNS (direct.py:2560-2578). With FULL_FRAME=(0,0,10343,6887) and COORD_PER_INCH=7200, 7200 dpi needs 10344 > 5172. The GUI never sets shading=False: there is no shading kwarg in the on_scan Scan() call (gui.py:2054-2061), Scan.shading defaults to True (session.py:866), and DPI_LADDER includes 7200 (gui.py:118). DemoScanner.scan has no such check and rescales instead (_shape_for/_rescale). In a roll the driver catches ShadingUnavailable per frame and yields error frames. The demo returns plausible pictures.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [DP-04](../areas/demo-parity.md#demo-parity-dp-04) | demo-parity | high | confirmed | Demo accepts scans above 3600 dpi (7200 in the GUI's ladder) that DirectScanner refuses with ShadingUnavailable before any pass | rps7200/direct.py:2560-2578, rps7200/direct.py:1615, rps7200/demo.py:530-597 |
| [SR-11](../areas/session-roll.md#session-roll-sr-11) | session-roll | high | confirmed | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in failure handling, end detection, prescan resolution, stop and raw fields | rps7200/demo.py:599-791, rps7200/direct.py:3493-3686, rps7200/session.py:1761-1786 |
| [TP-21](../areas/transport-protocol.md#transport-protocol-tp-21) | transport-protocol | medium | confirmed | The demo accepts 7200 dpi (and any >3600 dpi shaded pass) that DirectScanner refuses, and its position() never returns None | rps7200/demo.py:530-597, rps7200/demo.py:455-477, rps7200/demo.py:337-338 |
| [DDF-22](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-22) | decode-and-debug-filing | medium | partly | Demo: shading=False still returns corrected pixels, and the demo never raises ShadingUnavailable (e.g. at 7200 dpi) | rps7200/demo.py:530-597, rps7200/demo.py:1003-1010, rps7200/direct.py:2560-2580 |
| [DP-08](../areas/demo-parity.md#demo-parity-dp-08) | demo-parity | medium | confirmed | Demo prescans ignore the requested resolution and are mislabelled; the demo roll always prescans at 300 dpi whatever prescan_resolution is | rps7200/demo.py:1164-1184, rps7200/demo.py:455-477, rps7200/demo.py:599-616 |
| [DP-09](../areas/demo-parity.md#demo-parity-dp-09) | demo-parity | medium | confirmed | Demo roll loop diverges from DirectScanner.scan_roll: holds logged as 'operator', stop not passed to hold/aim, StripWalk not fed from held frames, no per-frame failure path | rps7200/demo.py:720-759, rps7200/direct.py:3517-3536, rps7200/direct.py:3571-3575 |
| [DP-10](../areas/demo-parity.md#demo-parity-dp-10) | demo-parity | medium | confirmed | Demo scans ignore where the film was moved; only prescans are shifted | rps7200/demo.py:414-427, rps7200/demo.py:469, rps7200/demo.py:565-597 |
| [DP-11](../areas/demo-parity.md#demo-parity-dp-11) | demo-parity | medium | confirmed | An RGBI request in the demo can return 3 channels; the hardware always returns 4 | rps7200/demo.py:565-579, rps7200/demo.py:593-594, rps7200/demo.py:862-902 |
| [FU-09](../areas/framing-units.md#framing-units-fu-09) | framing-units | medium | confirmed | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in its framing steps | rps7200/demo.py:597-613, rps7200/demo.py:708, rps7200/demo.py:720-726 |
| [FE-A1](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-a1) | frame-edges-detectors | medium | found-by-verifier | DemoScanner.scan_roll ignores the prescan resolution and always walks at 300 dpi, so the demo reads edges at settings where the hardware reads none. It also skips the blank-frame end check and observe for held frames, and has no per-frame error net | rps7200/demo.py:615, rps7200/demo.py:708, rps7200/demo.py:721-724 |
| [FE-07](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-07) | frame-edges-detectors | medium | partly | The demo feeds the detectors prescans the device never produces: uint16 pixels labelled depth 8, mixed dtypes within one walk, and film moves simulated by np.roll wrap-around | rps7200/demo.py:414-427, rps7200/demo.py:1036-1061, rps7200/demo.py:98-113 |
| [T17](../areas/tests.md#tests-t17) | tests | medium | confirmed | The demo's scan and scan_roll swallow or diverge on parameters, refusals and failure handling; parity tests check only constants | rps7200/demo.py:530-597, rps7200/demo.py:599-791, rps7200/direct.py:2505-2526 |
| [DP-19](../areas/demo-parity.md#demo-parity-dp-19) | demo-parity | medium | partly | Demo tests never exercise filing through the session, or loop parity with DirectScanner.scan_roll | tests/test_demo.py:69-82, tests/test_demo.py:548-573, tests/test_demo.py:605-616 |
| [D16](../areas/docs-readme-claude.md#docs-readme-claude-d16) | docs-readme-claude | medium | confirmed | README's 'refuses what the device refuses' and 'decodes raw bytes' claims do not hold for DemoScanner; it also retypes constants | rps7200/demo.py:530-597, rps7200/demo.py:455-477, rps7200/demo.py:1176-1184 |
