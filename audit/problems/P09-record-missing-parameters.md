# P09 -- The entry record drops parameters needed to re-derive or evaluate a pass

**Severity** high · **Group** A: Library exactness -- the central requirement · **Reported independently by** 19 findings in 12 areas

[Back to the summary](../README.md)

## The problem

`library.save` builds `scan.json` from a fixed whitelist. Silently dropped or never
recorded:

- the **INQUIRY** (device identity, firmware) -- every caller passes it, `save` ignores it;
- **commands that define the pass**: MODE SELECT byte 14, `slide_init_param`, quality
  bits, gain/offset extras, GET PARAMETERS response, sense data;
- **metering evidence** for roll frames and bracket passes (`exposure_metered=False`
  although every frame was metered);
- **bracket membership** (`bracket_*`) and merge parameters -- a bracket cannot be
  re-merged offline, and the merged result is never filed;
- **roll context**: `roll_index`, `roll_position`, transport position, the SLIDE bytes
  that placed the frame, the frame-edge members' answers;
- the `demo` flag -- demo entries are indistinguishable from real scans;
- **code identity**: provenance names the working tree at filing time (not the code the
  process imported), `driver_dirty` says "clean" when git is absent.

## Fix plan

1. Replace the whitelist with a namespaced pass-through: `record["scan"]` keeps today's
   keys; everything else the caller hands over goes under `record["extra"]` untouched.
2. Record per pass: `commands` (every CDB and payload sent for the pass, hex), `inquiry`,
   `sense` log, `metering` (always, when metering ran), `bracket` (group id, rung,
   ratio), `roll` (name, index, position, slide bytes), `demo: true`.
3. Capture `git rev-parse HEAD` and `git status --porcelain` once at import, and store
   them with a hash of the imported module files.

## Evidence (from [LIB-06](../areas/library.md#library-lib-06))

**Where:** `rps7200/library.py:131-147`, `rps7200/library.py:237-264`, `rps7200/direct.py:2402-2405`, `rps7200/direct.py:3653-3654`, `tools/uniformity.py:608`, `rps7200/demo.py:591`, `rps7200/direct.py:2437-2441`, `rps7200/direct.py:2636-2647`, `rps7200/direct.py:728`, `rps7200/session.py:1098`, `tests/test_library.py:388-420`

```text
`"scan": {k: meta.get(k) for k in ("resolution_dpi", "frame", ..., "fast_infrared", "filter_offsets")}`. Keys set elsewhere and lost: `meta["bracket_index"] = i` / `bracket_ratio` / `bracket_passes` / `bracket_stops`; `meta["roll_index"] = index` / `meta["roll_position"] = position`; `meta["uniformity_session"] = session`; demo `"demo": True`. The `inquiry: Any = None` parameter is accepted and never written. `byte14`, `slide_init_param` and `skip_shading` are scan() arguments passed to set_mode/slide but are absent from meta (direct.py:2760-2804). The test only checks a hand-built meta.
```

**Failure scenario:** The byte14 ladder or verify_protocol stages 3-5, filed via debug mode, produce entries that do not say which byte14 or SLIDE INIT param each pass used. A firmware difference between two sessions cannot be attributed. Demo entries copied into a real library look like real scans.

**Second reader's check:** The 'scan' block is a fixed comprehension over a key list (library.py:237-264). The following are dropped: bracket_index, bracket_ratio, bracket_passes and bracket_stops (direct.py:2402-2405); roll_index and roll_position (3653-3654); uniformity_session (tools/uniformity.py:608); demo True (demo.py:475, 591); and tools/scan.py's meta['bracket'] (262), although that one is added after filing anyway. The `inquiry` parameter is accepted and never referenced in save. scan()'s meta dict (2760-2804) has no byte14, slide_init_param or skip_shading, although scan() takes each as an argument and sends it (set_mode 2633-2641, slide 2645). In addition, debug capture snapshots meta inside scan() (2811), so later additions such as registration and roll_index never reach debug entries either.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [LIB-06](../areas/library.md#library-lib-06) | library | high | confirmed | library.save's fixed key whitelist silently drops meta and parameters: bracket, roll, uniformity session, demo flag, inquiry; command overrides never reach meta | rps7200/library.py:131-147, rps7200/library.py:237-264, rps7200/direct.py:2402-2405 |
| [CLI-11](../areas/cli-operator-tools.md#cli-operator-tools-cli-11) | cli-operator-tools | medium | confirmed | library.save silently drops the INQUIRY every CLI caller passes, plus bracket_* and roll_index/roll_position, so brackets cannot be regrouped from the library | rps7200/library.py:142, rps7200/library.py:204-252, tools/scan.py:191-193 |
| [TP-15](../areas/transport-protocol.md#transport-protocol-tp-15) | transport-protocol | medium | confirmed | Per-pass command and response state needed to re-derive a pass is not recorded | rps7200/direct.py:2760-2804, rps7200/library.py:237-264, rps7200/direct.py:1057-1118 |
| [TP-16](../areas/transport-protocol.md#transport-protocol-tp-16) | transport-protocol | medium | confirmed | INQUIRY (device identity and firmware) is never stored in any library entry | rps7200/library.py:131-147, rps7200/library.py:213-307, rps7200/direct.py:798-828 |
| [DDF-06](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-06) | decode-and-debug-filing | medium | confirmed | Commands and parameters that define a pass are not recorded: byte14, slide_init_param, quality bits, gain-offset extras, GET PARAMETERS bytes, INQUIRY | rps7200/direct.py:2423-2441, rps7200/direct.py:2613-2647, rps7200/direct.py:2760-2804 |
| [DDF-08](../areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-08) | decode-and-debug-filing | medium | confirmed | Roll and bracket frames are recorded as a commanded exposure (exposure_metered=False) and carry no metering evidence | rps7200/direct.py:3615-3652, rps7200/direct.py:2373-2401, rps7200/direct.py:2791 |
| [LIB-13](../areas/library.md#library-lib-13) | library | medium | confirmed | Metering evidence and 'metered' flag are lost for roll frames and brackets | rps7200/direct.py:2805-2809, rps7200/direct.py:2791, rps7200/direct.py:3615-3652 |
| [CLI-12](../areas/cli-operator-tools.md#cli-operator-tools-cli-12) | cli-operator-tools | medium | confirmed | Roll frames are filed as exposure_metered=false with no metering evidence even when every frame was metered | rps7200/direct.py:3616-3652, rps7200/direct.py:2791, rps7200/direct.py:2808-2809 |
| [OUT-13](../areas/outputs.md#outputs-out-13) | outputs | medium | confirmed | Bracket membership, merge parameters and metering are not recorded in the library, so a bracket cannot be re-merged offline | rps7200/direct.py:2402-2405, rps7200/library.py:237-261, tools/scan.py:238-246 |
| [DOC-A2](../areas/docs-plans-todo.md#docs-plans-todo-doc-a2) | docs-plans-todo | medium | found-by-verifier | Bracket membership, ratio and metering evidence are dropped from filed bracket passes; the merged result is never filed | rps7200/direct.py:2373-2378, rps7200/direct.py:2388-2405, rps7200/direct.py:2805-2808 |
| [PA-03](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-03) | probe-and-analysis-tools | high | confirmed | Probe entries lack the parameters that define them: byte14, slide_init_param, capture time, INQUIRY, and which probe, step, frame or rung they belong to | rps7200/direct.py:2760-2804, rps7200/direct.py:1112, rps7200/direct.py:672 |
| [SR-16](../areas/session-roll.md#session-roll-sr-16) | session-roll | medium | confirmed | Library records drop the inquiry, the transport position and the roll index; single scans record no position and nudges are recorded nowhere | rps7200/library.py:131-147, rps7200/library.py:237-261, rps7200/session.py:2137 |
| [DP-06](../areas/demo-parity.md#demo-parity-dp-06) | demo-parity | medium | partly | Demo entries are indistinguishable from real scans: the 'demo' flag is dropped by library.save, and INQUIRY is never recorded | rps7200/library.py:142, rps7200/library.py:237-261, rps7200/library.py:676-698 |
| [T10](../areas/tests.md#tests-t10) | tests | medium | confirmed | Entries drop the device INQUIRY, the calibration bytes and MODE SELECT choices; the 'keeps everything needed' test checks none of them | rps7200/library.py:142, rps7200/session.py:2137, rps7200/direct.py:728 |
| [CIP-1](../areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-1) | code-identity-and-protocol-revision | medium | partly | Provenance names the working tree at filing time, not the code the process imported; lazy imports let one process run two branches | rps7200/library.py:67-101, rps7200/library.py:306, rps7200/session.py:1090 |
| [CIP-2](../areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-2) | code-identity-and-protocol-revision | medium | confirmed | driver_dirty reports 'clean' when git is absent or refuses, is true for any untracked file, and no diff, branch or full sha is kept | rps7200/library.py:76-87, rps7200/library.py:97-101 |
| [CIP-5](../areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-5) | code-identity-and-protocol-revision | medium | confirmed | The SLIDE bytes that placed a frame are never recorded; spent_mm and asked_mm are derived with the unversioned law, so the transport cannot be re-evaluated | rps7200/direct.py:2873-2905, rps7200/direct.py:3138-3168, rps7200/direct.py:3549-3551 |
| [FE-06](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-06) | frame-edges-detectors | medium | partly | Automated edge decisions cannot be re-derived: the record keeps only the voted sides. Member answers, the roll context and the detector code identity are dropped, and approved.json keeps only offset_mm and source | tools/frame_edges/vote.py:123-125, tools/frame_edges/propose.py:113-116, tools/frame_edges/propose.py:240-243 |
| [T12](../areas/tests.md#tests-t12) | tests | medium | confirmed | Roll prescans are filed with film 'negative' whatever the roll's film; the demo passes the film, and FakeRoll.prescan cannot accept one | rps7200/direct.py:3494-3496, rps7200/direct.py:2906-2907, rps7200/direct.py:1682-1714 |
