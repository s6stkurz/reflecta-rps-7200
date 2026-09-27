# The demo against the real scanner

[Back to the summary](README.md)

CLAUDE.md's rule: `--demo` may change what the software is fed, never what it does.
`DemoScanner` stands where `DirectScanner` stands, and everything above that seam runs
unmodified.

## Where it stands at 03aacba

The first audit's demo problems (P26-P28) are fixed or mostly fixed:

- The demo files what it is. A pass drawn from raw bytes is filed raw with its reference; a
  stored prescan that was corrected is labelled so.
- It refuses what the scanner refuses, with the driver's own words: an uncalibrated corrected
  pass, no film in the transport.
- `scan_roll` runs the driver's own loop.
- `--look-only` without `--demo` is refused.

What the second audit found is narrower, and none of it is high:

- **Backlash is not applied** to the first move of a roll frame, so a backward hold converges
  in one command where the scanner needs more (DEMO-01).
- **`--library` and `--rolls` are accepted under `--demo`**, so synthetic entries and walks
  can land in the real library or rolls folder (DEMO-02).
- **Hold and aim prescans record `film = negative`** whatever the roll's film -- the driver's
  own bug, which the demo copies faithfully (DEMO-03, FR-08).

The remaining low findings are in [areas/demo-parity.md](areas/demo-parity.md).

## Every demo divergence

| Finding | Severity | Title | Where |
|---|---|---|---|
| [DEMO-01](areas/demo-parity.md#demo-parity-demo-01) | medium | Demo backlash is never applied to the first move of a roll frame, so backward holds converge in one command where the hardware loses the first ones | rps7200/demo.py:392-393, rps7200/demo.py:397-405 |
| [FE-A1](areas/frame-edges-member-detectors.md#frame-edges-member-detectors-fe-a1) | medium | The demo reads and moves 600 dpi walks the device refuses: a 300 dpi source fitted to 600 dpi becomes 856 columns, a multiple of 428, and reaches the wrong-move path of FE-03 | rps7200/demo.py:935-943, rps7200/demo.py:1110-1135 |
| [TP-28](areas/transport-protocol.md#transport-protocol-tp-28) | low | advance(steps) and retreat(steps) put the step count in the value byte, which the device ignores; the demo moves `steps` frames and retypes MM_PER_UNIT | rps7200/direct.py:1543, rps7200/direct.py:1557 |
| [DBG-19](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-19) | low | The demo stand-in has no debug spooling, no command log and no read_planes, so make run-demo never exercises debug filing | rps7200/demo.py:196, rps7200/demo.py:342-344 |
| [DEMO-05](areas/demo-parity.md#demo-parity-demo-05) | low | The demo's infrared refusal is a retyped copy of the driver's message and has already drifted | rps7200/demo.py:665-674, rps7200/direct.py:2912-2930 |
| [DEMO-06](areas/demo-parity.md#demo-parity-demo-06) | low | The ensure_shading stand-in behaves differently from the real one in reuse, caching and outcome | rps7200/demo.py:500-508, rps7200/direct.py:835-887 |
| [DEMO-07](areas/demo-parity.md#demo-parity-demo-07) | low | Demo nudge still re-implements the driver's distance arithmetic, clamped decision and return shape | rps7200/demo.py:432-437, rps7200/demo.py:451 |
| [DEMO-08](areas/demo-parity.md#demo-parity-demo-08) | low | The demo's progress totals differ from the device's by the channel count and use a retyped lines-per-dpi constant | rps7200/demo.py:587, rps7200/demo.py:691-694 |
| [DEMO-10](areas/demo-parity.md#demo-parity-demo-10) | low | With an empty library or no usable source, the demo returns shapes and correction states the device never produces, and the prescan and scan can come from different photographs | rps7200/demo.py:935-943, rps7200/demo.py:1373-1389 |
| [DEMO-14](areas/demo-parity.md#demo-parity-demo-14) | low | _shape_for takes the first entry at a dpi regardless of its scan window, so one partial-frame entry distorts every demo pass at that resolution | rps7200/demo.py:1110-1135 |
| [LIB-19](areas/library.md#library-lib-19) | low | DemoScanner has no debug filing, and verify has a demo carve-out: the second library writer is never exercised by --demo | rps7200/demo.py:196, rps7200/demo.py:342-344 |
| [FR-15](areas/framing-units.md#framing-units-fr-15) | low | Demo film movement wraps around (np.roll), so moved prescans correlate near-perfectly and edges read wrapped content | rps7200/demo.py:474-484, rps7200/demo.py:947 |
| [GUI2-25](areas/gui-part2.md#gui-part2-gui2-25) | low | --demo with an explicit --library or --rolls writes demo scans and rolls into the real library or rolls, contradicting the comment that --demo pins rolls under demo/ | tools/gui.py:8816-8818, tools/gui.py:8856-8889 |
| [PAT-19](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pat-19) | low | Offline analysis tools do not exclude demo entries, prescans or metering passes when they select from the library | tools/exposure_headroom.py:312-324, tools/linearity.py:133-153 |
| [DOC-18](areas/docs-readme-claude.md#docs-readme-claude-doc-18) | low | The demo's infrared refusal is a retyped copy, not 'the driver's own words' | rps7200/demo.py:665, rps7200/direct.py:2921 |
| [DOC-A2](areas/docs-readme-claude.md#docs-readme-claude-doc-a2) | low | The demo's nudge re-implements the driver's travel formula and retypes MM_PER_UNIT for backlash | rps7200/demo.py:433, rps7200/demo.py:452 |
| [DOC-11](areas/docs-plans-todo.md#docs-plans-todo-doc-11) | low | advance()/retreat() send `steps` as the SLIDE value byte, which docs measured as one frame regardless; the demo moves N frames | rps7200/direct.py:1543, rps7200/direct.py:1557-1590 |
| [CSA-15](areas/changes-since-first-audit.md#changes-since-first-audit-csa-15) | low | Demo cannot reach the new suspect, debug-claim and spool paths, and records less than a real pass | rps7200/demo.py:816-819, rps7200/demo.py:500-507 |
| [T-09](areas/tests.md#tests-t-09) | low | Demo-parity tests check attribute existence and object identity, not behaviour; the demo retypes nudge's arithmetic, omits meta keys, and answers ensure_shading(reuse) differently | tests/test_demo.py:318-414, tests/test_demo.py:1695-1710 |
| [MES-06](areas/unread-analysis-and-measurement-code.md#unread-analysis-and-measurement-code-mes-06) | low | Neither film_edge_study nor collect_vignette_study excludes demo entries; collect's 'when' is always empty and a mistyped --tag still 'succeeds' | tools/film_edge_study.py:243-253, tools/collect_vignette_study.py:90 |
| [DEMO-17](areas/demo-parity.md#demo-parity-demo-17) | info | Demo scan() silently drops driver arguments and differs in defaults and hook error handling | rps7200/demo.py:652-664, rps7200/direct.py:2842-2860 |
| [DEMO-18](areas/demo-parity.md#demo-parity-demo-18) | info | Real driver paths the demo cannot reach | rps7200/demo.py:816-819, rps7200/session.py:2872-2875 |
| [DEMO-19](areas/demo-parity.md#demo-parity-demo-19) | info | Demo metering records probe rounds that cannot respond to exposure | rps7200/demo.py:549-565, rps7200/direct.py:2489-2565 |
| [GUI1-21](areas/gui-part1.md#gui-part1-gui1-21) | info | --demo with --library/--rolls/--reference writes the demo's stand-in scans into real stores | tools/gui.py:8779-8783, tools/gui.py:8885-8890 |
| [GUI2-29](areas/gui-part2.md#gui-part2-gui2-29) | info | The demo seam holds in this area: no `if demo` in the scan path | tools/gui.py:377, tools/gui.py:545 |
