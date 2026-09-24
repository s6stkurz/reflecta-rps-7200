# The demo vs the real scanner

[Back to the summary](README.md)

CLAUDE.md's rule: **`--demo` may change what the software is fed. It may not change what
the software does.** `DemoScanner` stands where `DirectScanner` stands, injected at
`session._open_scanner`, and everything above that seam runs unmodified.

## What holds

- The seam is real. `tools/gui.py` swaps `session._open_scanner` (`tools/gui.py:8187-8199`).
  There is no `demo` branch in `ScanSession`, `FrameWriter` or `library.save`. The
  window's own `look_only` branches (`tools/gui.py:2695`, `7266`) change explanatory text
  only, not controls.
- Most transport constants are taken from `DirectScanner` rather than retyped:
  `param_for_mm` is borrowed as a static method, as are `_hold_to_approved`,
  `_aim_frame`, `roll_ends` and `place_on_strip`.
- `rps7200` never imports `tools`, so the demo cannot reach the window's internals.

## What does not

The seam is in the right place, but the thing plugged into it is a **reimplementation of
the scanner**, not a stand-in for the device. `DemoScanner` is not a `DirectScanner`
subclass. It writes its own `scan`, `prescan`, `scan_roll`, `ensure_shading` and
`position`, and each of them differs from the real one somewhere:

| Real `DirectScanner` | `DemoScanner` | Effect | Problem |
|---|---|---|---|
| `scan()` returns corrected pixels **and** sets `last_pixels_raw` | never sets `last_pixels_raw`; `RollFrame`s carry no `raw_image` / `raw_prescan` | every demo entry is corrected-as-raw, beside the **source** entry's bytes; viewed and exported, it is corrected twice | [P26](problems/P26-demo-files-corrected-as-raw.md) |
| capture record describes *this* pass | stale record from the previous decode when a prescan is served from `prescan.tif` | reference/mask/bytes that do not describe the filed pixels | P26 |
| `shading=False` returns raw pixels | always corrects when a reference exists | the raw path is unreachable in the demo | [P27](problems/P27-demo-refusals-and-roll-loop.md) |
| refuses > 3600 dpi with shading (`ShadingUnavailable`) | accepts 7200 dpi | the demo "scans" what the hardware always refuses | P27 |
| `position()` can return `None` | never `None` | the unknown-position branches never run | P27 |
| `scan_roll`: per-frame `except`, `max_failures`, blank-contrast end, `prescan_resolution`, stop passed to hold/aim, metering per frame | own loop: none of those; prescans always 300 dpi (`prescan_resolution` swallowed by `**kw`) | the roll logic the demo exists to exercise is not the one that ships | P27 |
| a scan comes from where the film is | only prescans are shifted by the simulated position | holds and scans disagree about position | P27 |
| RGBI always returns 4 channels | can return 3 | -- | P27 |
| prescans uint8 depth 8 | uint16 labelled depth 8; mixed dtypes within one walk | the detectors see input the device never produces | P27 |
| `ensure_shading` measures and writes the cache | sleeps, returns "calibrated", writes nothing | Calibrate-off-then-Scan is instant in the demo, minutes of lazy calibration on the hardware | P27, P15 |
| no film: device refuses | `--look-only`: `prescan()` returns a stored photograph; without `--demo`, `--look-only` drives the real scanner | the refusal the flag promises does not happen | [P28](problems/P28-look-only-drives-real-scanner.md) |

The other direction matters just as much. **Because the demo takes a branch the real
scanner never takes, it cannot catch P01** (the window's Scan filing corrected pixels as
raw). It produces the same symptom for a different reason, so it hides the real bug
rather than exposing it.

## Also: the demo is not isolated from real data

- `--demo --library library` or `--demo --rolls rolls` is accepted without a warning.
  Demo entries enter the real library with no demo marker (`library.save`'s whitelist
  drops the flag, P09), and a demo walk can overwrite a real walk's `survey.json`,
  `prescanNN.tif` and `approved.json`.
- Delete on a reopened frame under `--demo` / `make run-sheet` removes a **real** library
  entry ([P25](problems/P25-reopened-frames.md)).
- `--demo-source demo/library` makes the demo feed on its own mislabelled output.

## The fix, in one step

Move the seam one layer down. Keep `DirectScanner` and fake the **transport**: a
`DemoTransport` that answers INQUIRY, READ STATE, MODE SELECT, GET PARAMETERS and READ
from library entries' raw bytes, and that simulates the transport position for SLIDE.
Then `scan`, `prescan`, `scan_roll`, metering, shading, refusals, `last_pixels_raw` and
filing are the real code **by construction**. The demo cannot drift, and it would have
caught P01. Refusals ("no film", "no scanner") become answers the fake transport gives,
which is what CLAUDE.md asks for.

Until then, the interim steps in [P26](problems/P26-demo-files-corrected-as-raw.md) and
[P27](problems/P27-demo-refusals-and-roll-loop.md) apply. They are: set `last_pixels_raw`,
delegate `scan_roll` to `DirectScanner.scan_roll`, take the refusals from the same checks,
and tag demo entries. Also refuse `--demo` combined with any real library/rolls/output
path unless forced.

## Every demo-divergence finding

| Finding | Severity | Title | Where |
|---|---|---|---|
| [DP-02](areas/demo-parity.md#demo-parity-dp-02) | high | DemoScanner never exposes pre-correction pixels, so every demo prescan and roll frame is filed with corrected, resampled or shifted pixels labelled raw | rps7200/demo.py:257-264, rps7200/demo.py:1008-1010 |
| [DP-04](areas/demo-parity.md#demo-parity-dp-04) | high | Demo accepts scans above 3600 dpi (7200 in the GUI's ladder) that DirectScanner refuses with ShadingUnavailable before any pass | rps7200/direct.py:2560-2578, rps7200/direct.py:1615 |
| [SR-11](areas/session-roll.md#session-roll-sr-11) | high | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in failure handling, end detection, prescan resolution, stop and raw fields | rps7200/demo.py:599-791, rps7200/direct.py:3493-3686 |
| [GUI2-02](areas/gui-part2.md#gui-part2-gui2-02) | high | DemoScanner hands the session corrected pixels plus a shading reference and never any raw pixels, so demo entries are corrected-as-raw and the demo's full-res view and exports double-correct | rps7200/demo.py:1009-1010, rps7200/demo.py:435-442 |
| [DX1](areas/docs-readme-claude.md#docs-readme-claude-dx1) | high | In --demo every filed entry is corrected pixels labelled raw, because DemoScanner publishes no last_pixels_raw and its RollFrames carry no raw_image | rps7200/demo.py:176, rps7200/demo.py:955-1024 |
| [T02](areas/tests.md#tests-t02) | high | Demo files corrected pixels with the source entry's raw bytes and reference; the test that says it reconstructs uses a fixture with no shading reference and bypasses the session | rps7200/demo.py:15-19, rps7200/demo.py:995-1022 |
| [TP-21](areas/transport-protocol.md#transport-protocol-tp-21) | medium | The demo accepts 7200 dpi (and any >3600 dpi shaded pass) that DirectScanner refuses, and its position() never returns None | rps7200/demo.py:530-597, rps7200/demo.py:455-477 |
| [TP-A4](areas/transport-protocol.md#transport-protocol-tp-a4) | medium | DemoScanner has no last_pixels_raw, so demo sessions file corrected pixels labelled raw and the session's raw-vs-corrected path never runs in the demo | rps7200/demo.py:955-1021, rps7200/demo.py:435-443 |
| [DDF-22](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-22) | medium | Demo: shading=False still returns corrected pixels, and the demo never raises ShadingUnavailable (e.g. at 7200 dpi) | rps7200/demo.py:530-597, rps7200/demo.py:1003-1010 |
| [DDF-23](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-23) | medium | Demo entries hold corrected pixels as 'raw', lack device-settings meta, and are not marked as demo | rps7200/demo.py:176, rps7200/demo.py:553-596 |
| [DP-08](areas/demo-parity.md#demo-parity-dp-08) | medium | Demo prescans ignore the requested resolution and are mislabelled; the demo roll always prescans at 300 dpi whatever prescan_resolution is | rps7200/demo.py:1164-1184, rps7200/demo.py:455-477 |
| [DP-09](areas/demo-parity.md#demo-parity-dp-09) | medium | Demo roll loop diverges from DirectScanner.scan_roll: holds logged as 'operator', stop not passed to hold/aim, StripWalk not fed from held frames, no per-frame failure path | rps7200/demo.py:720-759, rps7200/direct.py:3517-3536 |
| [DP-10](areas/demo-parity.md#demo-parity-dp-10) | medium | Demo scans ignore where the film was moved; only prescans are shifted | rps7200/demo.py:414-427, rps7200/demo.py:469 |
| [DP-11](areas/demo-parity.md#demo-parity-dp-11) | medium | An RGBI request in the demo can return 3 channels; the hardware always returns 4 | rps7200/demo.py:565-579, rps7200/demo.py:593-594 |
| [LIB-18](areas/library.md#library-lib-18) | medium | Demo stand-in diverges in what it files: no last_pixels_raw, stale reference/mask on prescans, ignores shading=False, no 7200 dpi refusal, demo flag dropped | rps7200/demo.py:455-477, rps7200/demo.py:1164-1200 |
| [SR-12](areas/session-roll.md#session-roll-sr-12) | medium | Demo-filed entries hold shading-corrected pixels labelled raw, and demo prescans carry a stale capture record | rps7200/demo.py:15-20, rps7200/demo.py:1008-1010 |
| [FU-09](areas/framing-units.md#framing-units-fu-09) | medium | DemoScanner.scan_roll diverges from DirectScanner.scan_roll in its framing steps | rps7200/demo.py:597-613, rps7200/demo.py:708 |
| [GUI1-18](areas/gui-part1.md#gui-part1-gui1-18) | medium | 7200 dpi is offered but always refused by the host on hardware (after metering), while the demo accepts it | tools/gui.py:102-118, tools/gui.py:1748-1749 |
| [GUI1-19](areas/gui-part1.md#gui-part1-gui1-19) | medium | Delete on a reopened frame deletes a real library entry, even under --demo / make run-sheet | tools/gui.py:4742, tools/gui.py:3999-4011 |
| [GUI1-A1](areas/gui-part1.md#gui-part1-gui1-a1) | medium | DemoScanner publishes no raw pixels, so every demo entry files corrected pixels beside a reference and is corrected twice when viewed. The demo also hides GUI1-01 | rps7200/demo.py:1006-1010, rps7200/demo.py:525-597 |
| [OUT-14](areas/outputs.md#outputs-out-14) | medium | The demo files corrected prescans and roll frames as raw, a branch the real scanner does not take | rps7200/demo.py:176, rps7200/demo.py:455-477 |
| [CLI-13](areas/cli-operator-tools.md#cli-operator-tools-cli-13) | medium | --approved diverges from the window: ignores the walk's prescan dpi, does not un-orient GUI-walk prescans, and ignores approved.json | tools/scan_roll.py:200-261, tools/scan_roll.py:108-114 |
| [PA-A1](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-a1) | medium | Only the probes call session_start(): they run a different device sequence from the product (0xE7, plus a SLIDE 00 01 00 00 that moves the film) while their docstrings say nothing moves | rps7200/direct.py:1726-1753, rps7200/direct.py:1143-1168 |
| [D16](areas/docs-readme-claude.md#docs-readme-claude-d16) | medium | README's 'refuses what the device refuses' and 'decodes raw bytes' claims do not hold for DemoScanner; it also retypes constants | rps7200/demo.py:530-597, rps7200/demo.py:455-477 |
| [T12](areas/tests.md#tests-t12) | medium | Roll prescans are filed with film 'negative' whatever the roll's film; the demo passes the film, and FakeRoll.prescan cannot accept one | rps7200/direct.py:3494-3496, rps7200/direct.py:2906-2907 |
| [T17](areas/tests.md#tests-t17) | medium | The demo's scan and scan_roll swallow or diverge on parameters, refusals and failure handling; parity tests check only constants | rps7200/demo.py:530-597, rps7200/demo.py:599-791 |
| [FE-07](areas/frame-edges-detectors.md#frame-edges-detectors-fe-07) | medium | The demo feeds the detectors prescans the device never produces: uint16 pixels labelled depth 8, mixed dtypes within one walk, and film moves simulated by np.roll wrap-around | rps7200/demo.py:414-427, rps7200/demo.py:1036-1061 |
| [FE-A1](areas/frame-edges-detectors.md#frame-edges-detectors-fe-a1) | medium | DemoScanner.scan_roll ignores the prescan resolution and always walks at 300 dpi, so the demo reads edges at settings where the hardware reads none. It also skips the blank-frame end check and observe for held frames, and has no per-frame error net | rps7200/demo.py:615, rps7200/demo.py:708 |
| [DDF-24](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-24) | low | Demo: the read_direction record can contradict its raw bytes when the source entry was itself read bottom-up | rps7200/demo.py:490-528 |
| [DP-12](areas/demo-parity.md#demo-parity-dp-12) | low | Calibration state not modelled: the demo always shades with each entry's own reference, never calibrates implicitly, and reports 'loaded' for a reference that does not exist | rps7200/demo.py:446-453, rps7200/demo.py:995-1010 |
| [DP-14](areas/demo-parity.md#demo-parity-dp-14) | low | Demo retypes constants and models backlash far smaller than its own docstring (and the hardware) says | rps7200/demo.py:246-250, rps7200/demo.py:390-395 |
| [DP-16](areas/demo-parity.md#demo-parity-dp-16) | low | Empty library: the test card ignores resolution and depth, and the hold loop can never converge | rps7200/demo.py:1202-1224, rps7200/demo.py:1259-1275 |
| [DP-A1](areas/demo-parity.md#demo-parity-dp-a1) | low | Under --look-only the demo's prescan() does not refuse: it returns a stored photograph of film that is not there | rps7200/demo.py:455-477, rps7200/demo.py:826-844 |
| [DP-A2](areas/demo-parity.md#demo-parity-dp-a2) | low | Demo timing ignores fast_infrared and metering, so an untied IR pass and a metered roll look far cheaper than on the scanner | rps7200/demo.py:459, rps7200/demo.py:561-564 |
| [GUI1-37](areas/gui-part1.md#gui-part1-gui1-37) | low | The demo stand-in answers some GUI actions differently from the hardware (cached-reference reuse, untied infrared timing) | rps7200/demo.py:446-453, rps7200/demo.py:560-564 |
| [CIP-8](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-8) | low | Demo entries carry no protocol_revision, and raw bytes copied from a real entry are re-filed under the demo's provenance with no link to their source | rps7200/demo.py:576-596, rps7200/demo.py:955-1021 |
| [DP-20](areas/demo-parity.md#demo-parity-dp-20) | info | Paths the demo can never reach: counter silent, wrong-way direction, error frames; plus one frame per roll forced to miss | rps7200/demo.py:337-338, rps7200/demo.py:253 |
