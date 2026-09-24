# Does the library hold the exact bit data?

[Back to the summary](README.md)

The requirement: every scan is kept as the exact bits the scanner sent, plus everything
needed to turn them into pixels and correct them again later with newer code, so that
everything can be recalculated and evaluated. This file answers it from the code.

**Short answer: the raw bytes are exact when they are there. But some entries hold the
wrong bytes, some hold no bytes, some hold corrected pixels labelled raw, the correction's
own input (the calibration) is never kept, and several kinds of passes are never filed at
all.**

## What one entry is, byte for byte

`library/<YYYYMMDDTHHMMSSZ>_<stock|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/`, written by
`library.save` (`rps7200/library.py:131-311`):

| File | Content | Exact? | Problems |
|---|---|---|---|
| `raw.bin.gz` | gzip (level 6) of the concatenated READ payloads: each line a 2-byte channel tag + `bytes_per_line`, little-endian, in the order received (bottom-up passes stay bottom-up). Layout in `scan.json` `raw.layout`. sha256 and length recorded. | **Yes**, byte-exact, when it is the pass's own bytes | May be **another pass's** bytes in debug mode (P02); dropped for short reads (P07); absent for every walk prescan from the CLI, every roll-frame prescan, metering probe and hold pass (P08) |
| `scan.tif` | Meant to be `decode_index` of those bytes, turned upright, uint16 (uint8 for prescans), C = 3 or 4. Lossless TIFF (tifffile deflate+predictor, or uncompressed built-in writer). sha256 recorded. | Lossless, but not always the decode | **Corrected pixels** for every single Scan from the window (P01) and every demo entry (P26); **realigned** (4 rows trimmed, odd columns shifted) at 7200 dpi, unrecorded (P06) |
| `shading.npz` | `ShadingReference`: float64 per-column light and dark means per channel, as reduced by `calculate_shading` | Exact copy of a *derived* product | The calibration bytes it came from are discarded, as is whether it was measured or reused, and its age (P05). No checksum (P10) |
| `ccd_mask.bin` | The exact GET CCD MASK bytes of the pass | Yes | No checksum (P10) |
| `prescan.tif` (roll frames) | The **corrected** 8-bit prescan as delivered | No | No bytes, mask, resolution, checksum, or "corrected" label (P08) |
| `scan.json` | A whitelisted subset of the meta, written last | -- | Drops INQUIRY, command bytes, metering evidence, bracket/roll context, the demo flag, capture time (P09). Written non-atomically (P10) |
| `library/index.json` | Derived summary, rewritten after each save | -- | -- |

## What would be needed to recalculate everything, and what is missing

| To redo... | You need | Kept? |
|---|---|---|
| the decode | raw bytes + layout | Yes, when present and the pass's own (P02, P07, P08) |
| the shading correction | raw pixels + reference + mask | Reference and mask yes. Raw pixels are wrong for P01/P26 entries (repairable from bytes) |
| the **reference itself** | calibration bytes, calibration info block, calibration mask, settings | **No** (P05) |
| exposure / metering decisions | metering probe passes + the metering record | Probes never filed; `metering` recorded only for single auto-exposed scans (P08, P09) |
| a bracket merge | every member, ratios, membership | Members filed separately; membership and ratios dropped (P09, P29) |
| framing / holds | every prescan the hold loop judged, the SLIDE bytes sent, detector members' answers | Only the last prescan, corrected; commands and member answers not stored (P08, P09) |
| "which code, which device" | git commit and dirty state *of the imported code*, INQUIRY | Working tree at filing time; INQUIRY dropped (P09) |
| "is this entry intact" | checksums of every file, atomic writes | Only `scan.tif` and raw bytes checksummed; writes non-atomic (P10) |

## How bits get lost or mislabelled -- by path

- **Single Scan in the window** -> corrected pixels as raw -> double correction on view
  and export. [P01](problems/P01-gui-scan-files-corrected-as-raw.md)
- **Debug filing (`RPS7200_DEBUG=1`)** -> stale bytes
  ([P02](problems/P02-debug-filing-stale-raw-bytes.md)), spool deleted on a failed save or
  lost on a kill ([P03](problems/P03-debug-spool-not-crash-safe.md)).
- **FrameWriter** -> a failed delivered copy prevents the library entry.
  [P04](problems/P04-delivered-copy-before-library-entry.md)
- **Calibration** -> reduced, bytes discarded.
  [P05](problems/P05-calibration-not-stored-exactly.md)
- **7200 dpi** -> realigned and unrecorded.
  [P06](problems/P06-7200dpi-realignment-not-recorded.md)
- **Short / failed passes** -> bytes dropped by the guard or never filed.
  [P07](problems/P07-failed-and-short-passes-lose-bytes.md)
- **Probes, holds, walks, roll prescans** -> not filed, or filed corrected.
  [P08](problems/P08-passes-never-filed.md)
- **`scan.json`** -> whitelist drops context.
  [P09](problems/P09-record-missing-parameters.md)
- **Crash / concurrent writers** -> partial or merged entries.
  [P10](problems/P10-non-atomic-writes.md)
- **Maintenance tools** -> `duplicates --delete` deletes distinct photographs
  ([P11](problems/P11-duplicates-delete-destroys-scans.md)); `migrate-raw --write` can
  launder a decode regression, and `verify`/`reconstruct` are too noisy to catch one
  ([P12](problems/P12-library-maintenance-tools.md)).

## The target design, in one paragraph

Each pass is filed as it is captured, into a temporary directory that is renamed into
place when complete, and filed *first*, before any delivered copy. The entry holds the
pass's own bytes (the stale-byte path removed), the decode as `scan.tif`, the mask, and a
link to a **calibration entry** that holds the calibration's own bytes. `scan.json` keeps
everything the caller handed over (namespaced, no whitelist): the command bytes sent, the
INQUIRY, the metering record, bracket/roll/demo context, capture time, and the code
identity. Every file is checksummed. `verify` knows deliberate exceptions. `reconstruct`
replays every recorded transform, so a "changed" verdict means a real regression again.

## Every data-integrity finding (148)

| Finding | Severity | Title | Problem |
|---|---|---|---|
| [TP-01](areas/transport-protocol.md#transport-protocol-tp-01) | critical | GUI single Scan files the shading-CORRECTED image into the library as raw pixels | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [DDF-01](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-01) | critical | Debug filing files a pass with no raw bytes or with a previous pass's raw bytes (stale last_raw) | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [DDF-05](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-05) | critical | GUI single scans file the shading-CORRECTED image as scan.tif while the record says raw | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [DP-01](areas/demo-parity.md#demo-parity-dp-01) | critical | GUI single Scan files shading-corrected pixels as raw scan.tif (real scanner and demo); library.corrected() then shades them a second time | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [LIB-01](areas/library.md#library-lib-01) | critical | GUI single Scan files the shading-corrected image as raw; Save As / full-res view then correct it a second time | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [SR-01](areas/session-roll.md#session-roll-sr-01) | critical | GUI single Scan files shading-corrected pixels as the raw scan.tif; full-res view and Save As are double-corrected | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [SR-02](areas/session-roll.md#session-roll-sr-02) | critical | A failed write of the delivered TIFF/JPEG (output folder or rolls/) aborts the library entry: raw bytes of the scan are lost | [P04](problems/P04-delivered-copy-before-library-entry.md) |
| [SR-A1](areas/session-roll.md#session-roll-sr-a1) | critical | Every 7200 dpi pass filed through ScanSession loses its raw bytes: the column-stagger realignment makes the shape guard reject them | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [GUI1-01](areas/gui-part1.md#gui-part1-gui1-01) | critical | A single Scan from the window files shading-corrected pixels as the library's raw scan.tif, so every view and export corrects them twice | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [GUI1-02](areas/gui-part1.md#gui-part1-gui1-02) | critical | An unwritable or missing output folder (or roll folder) silently costs the library entry, raw bytes included | [P04](problems/P04-delivered-copy-before-library-entry.md) |
| [GUI2-01](areas/gui-part2.md#gui-part2-gui2-01) | critical | Scan-button passes are filed with corrected pixels labelled raw; every full-res view, Save As and Save all corrects them a second time | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [OUT-01](areas/outputs.md#outputs-out-01) | critical | GUI single scans file shading-corrected pixels as raw; Save As/Save all then double-correct them | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [OUT-02](areas/outputs.md#outputs-out-02) | critical | FrameWriter writes delivered files before the library entry, so any delivery failure loses the raw bytes | [P04](problems/P04-delivered-copy-before-library-entry.md) |
| [CLI-01](areas/cli-operator-tools.md#cli-operator-tools-cli-01) | critical | scan_roll.py: Ctrl-C / SIGTERM skips writer.finish(); queued frames die with the daemon writer, and the read is abandoned mid-scan | [P14](problems/P14-failure-paths-keep-driving-device.md) |
| [CLI-02](areas/cli-operator-tools.md#cli-operator-tools-cli-02) | critical | scan.py files nothing until after the session: any exception or Ctrl-C mid-bracket loses every pass already scanned | [P14](problems/P14-failure-paths-keep-driving-device.md) |
| [PA-01](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-01) | critical | `library.py duplicates --delete` would destroy probe ladders, repeat pairs and whole walk corpora filed by debug mode | [P11](problems/P11-duplicates-delete-destroys-scans.md) |
| [D01](areas/docs-readme-claude.md#docs-readme-claude-d01) | critical | GUI single Scan files shading-CORRECTED pixels as raw; Save As / 1:1 view then correct them a second time | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [T01](areas/tests.md#tests-t01) | critical | GUI Scan job files the shading-corrected image as raw; Save As and 1:1 view then correct it twice; no session test can see it | [P01](problems/P01-gui-scan-files-corrected-as-raw.md) |
| [TP-02](areas/transport-protocol.md#transport-protocol-tp-02) | high | Debug flush deletes the only copy of a scan when filing it fails | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [TP-03](areas/transport-protocol.md#transport-protocol-tp-03) | high | Debug filing and capture_record attach a STALE last_raw from an earlier pass (or none) to a scan taken with keep_raw=False | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [TP-04](areas/transport-protocol.md#transport-protocol-tp-04) | high | Calibration raw bytes are discarded; only a reduced float reference is stored, and its provenance is not recorded | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [DDF-02](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-02) | high | A filing failure during _debug_flush deletes the only copy of the pass (spool unlinked in finally) | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-03](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-03) | high | If close() never runs, every pending debug entry is lost and the spool is orphaned without its meta | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-04](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-04) | high | The calibration pass's raw bytes are never stored; the library keeps only the host's derived float64 reference | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [DP-03](areas/demo-parity.md#demo-parity-dp-03) | high | Demo capture_record() is stale for prescans served from prescan.tif, and dropping raw keeps a reference and CCD mask that no longer describe the pixels | [P26](problems/P26-demo-files-corrected-as-raw.md) |
| [LIB-02](areas/library.md#library-lib-02) | high | Debug filing pairs a pass with the PREVIOUS pass's raw bytes (or none) whenever keep_raw=False | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [LIB-04](areas/library.md#library-lib-04) | high | Shading reference is stored only as a derived product; the calibration bytes and calibration state are never kept | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [LIB-05](areas/library.md#library-lib-05) | high | prescan.tif in a frame entry is the corrected prescan with no bytes, mask, resolution or meta; real-roll prescan bytes are never filed | [P08](problems/P08-passes-never-filed.md) |
| [LIB-06](areas/library.md#library-lib-06) | high | library.save's fixed key whitelist silently drops meta and parameters: bracket, roll, uniformity session, demo flag, inquiry; command overrides never reach meta | [P09](problems/P09-record-missing-parameters.md) |
| [SR-03](areas/session-roll.md#session-roll-sr-03) | high | roll.json marks a frame done before, and regardless of whether, it was filed | [P19](problems/P19-roll-manifests.md) |
| [SR-04](areas/session-roll.md#session-roll-sr-04) | high | A short or anomalous pass loses its raw bytes: the shape guard compares declared lines with decoded lines | [P07](problems/P07-failed-and-short-passes-lose-bytes.md) |
| [SR-05](areas/session-roll.md#session-roll-sr-05) | high | Real-roll prescans are kept only corrected; failed-frame, arrival and hold-loop prescans are never filed; debug filing is forced off | [P08](problems/P08-passes-never-filed.md) |
| [SR-07](areas/session-roll.md#session-roll-sr-07) | high | roll.json/survey.json written non-atomically; an unreadable earlier roll.json is silently discarded and overwritten on resume | [P19](problems/P19-roll-manifests.md) |
| [SR-10](areas/session-roll.md#session-roll-sr-10) | high | A value in the Film 'frame' note replaces every roll frame's label: all entries share one library signature and the roll-entry join breaks | [P21](problems/P21-roll-to-library-join.md) |
| [SR-A2](areas/session-roll.md#session-roll-sr-a2) | high | Unlabelled single scans and prescans from the window share one library signature; `duplicates --delete` destroys distinct pictures | [P11](problems/P11-duplicates-delete-destroys-scans.md) |
| [SR-A3](areas/session-roll.md#session-roll-sr-a3) | high | Any walk into an existing roll folder replaces survey.json wholesale: a partial re-walk erases the earlier walk's frames | [P18](problems/P18-roll-folder-identity.md) |
| [FU-01](areas/framing-units.md#framing-units-fu-01) | high | Framing inputs are not stored exactly: roll prescans have no raw bytes; arrival and intermediate hold prescans are discarded | [P08](problems/P08-passes-never-filed.md) |
| [GUI1-03](areas/gui-part1.md#gui-part1-gui1-03) | high | approved.json is written to a different folder than the roll it belongs to (empty or unsanitised roll names) | [P18](problems/P18-roll-folder-identity.md) |
| [GUI1-04](areas/gui-part1.md#gui-part1-gui1-04) | high | With the default empty roll name every strip of the day shares rolls/<date>: walks overwrite each other, frames overwrite frameNN.tif, and the previous strip's sheet decisions are restored onto the next | [P18](problems/P18-roll-folder-identity.md) |
| [GUI1-06](areas/gui-part1.md#gui-part1-gui1-06) | high | Turning or flipping a prescan during a walk changes the orientation of the prescanNN.tif files still to be written, while survey.json keeps the start value, so reopened references come back mis-oriented | [P20](problems/P20-rotation-during-walk.md) |
| [GUI2-03](areas/gui-part2.md#gui-part2-gui2-03) | high | approved.json is written to rolls/_safe(<roll field>) but the roll goes to rolls/<roll field or today's date>. With the default empty name it always lands in rolls/roll/ | [P18](problems/P18-roll-folder-identity.md) |
| [GUI2-09](areas/gui-part2.md#gui-part2-gui2-09) | high | Rotating during a walk or roll changes the orientation of later prescanNN.tif files while survey.json keeps the start-time rotation, so a reopened walk un-orients those references wrongly | [P20](problems/P20-rotation-during-walk.md) |
| [OUT-03](areas/outputs.md#outputs-out-03) | high | Debug flush deletes the spooled raw data even when library.save (tiff.write) failed | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [CLI-03](areas/cli-operator-tools.md#cli-operator-tools-cli-03) | high | CLI tools never file walk prescans, metering probes, hold/aim prescans or verification passes; debug=False hard-coded, so RPS7200_DEBUG is ignored | [P08](problems/P08-passes-never-filed.md) |
| [CLI-06](areas/cli-operator-tools.md#cli-operator-tools-cli-06) | high | `library.py duplicates --delete` treats scans of different pictures as duplicates when film notes are empty or same-day default roll names collide | [P11](problems/P11-duplicates-delete-destroys-scans.md) |
| [CLI-09](areas/cli-operator-tools.md#cli-operator-tools-cli-09) | high | The calibration pass's raw bytes are discarded; only a derived ShadingReference is stored, and --reuse leaves no record that the reference was stale | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [CLI-10](areas/cli-operator-tools.md#cli-operator-tools-cli-10) | high | 7200 dpi scan.tif has a host-side stagger realignment baked in that reconstruct/decode_raw never replay; every 7200 dpi entry reads 'decode CHANGED' | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [PA-02](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-02) | high | A keep_raw=False pass after a keep_raw=True pass is filed with the previous pass's raw bytes (byte14_probe final pass) | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [PA-03](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-03) | high | Probe entries lack the parameters that define them: byte14, slide_init_param, capture time, INQUIRY, and which probe, step, frame or rung they belong to | [P09](problems/P09-record-missing-parameters.md) |
| [PA-04](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-04) | high | A probe killed rather than interrupted files nothing: meta, reference, mask and layout exist only in RAM until close() | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [PA-23](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-23) | high | Calibration bytes are never stored: the library's shading.npz is a derived, heuristic parse and cannot be recomputed | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [D02](areas/docs-readme-claude.md#docs-readme-claude-d02) | high | Debug filing attaches NO raw bytes, or the PREVIOUS pass's raw bytes, whenever scan() runs with the default keep_raw=False | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [D06](areas/docs-readme-claude.md#docs-readme-claude-d06) | high | shading.npz is a host-derived reduction; the calibration bytes and calibration-time settings are thrown away | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [D07](areas/docs-readme-claude.md#docs-readme-claude-d07) | high | tools/scan_roll.py --dry-run files nothing in the library, and roll-frame prescans lose their raw bytes (CLI and GUI) | [P08](problems/P08-passes-never-filed.md) |
| [DOC-02](areas/docs-plans-todo.md#docs-plans-todo-doc-02) | high | library.signature ignores gain and byte14, so `duplicates --delete` would destroy the byte-14 and gain ladders that TODO says must not be pruned | [P11](problems/P11-duplicates-delete-destroys-scans.md) |
| [DOC-03](areas/docs-plans-todo.md#docs-plans-todo-doc-03) | high | The calibration pass's raw bytes are never stored; the library keeps only the processed ShadingReference | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [DOC-04](areas/docs-plans-todo.md#docs-plans-todo-doc-04) | high | Several kinds of pass are never filed outside debug mode, and a roll frame's prescan.tif holds corrected pixels | [P08](problems/P08-passes-never-filed.md) |
| [DOC-A1](areas/docs-plans-todo.md#docs-plans-todo-doc-a1) | high | Debug filing deletes a scan's only spooled copy (raw bytes and pixels) even when library.save failed | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [T03](areas/tests.md#tests-t03) | high | FrameWriter writes the operator's copy before the library entry, so a failing output path loses the raw bytes; the failure tests use library=None | [P04](problems/P04-delivered-copy-before-library-entry.md) |
| [T04](areas/tests.md#tests-t04) | high | Debug filing attaches a stale last_raw from an earlier pass when a pass runs with keep_raw=False; no guard, and the test even spools mismatched bytes | [P02](problems/P02-debug-filing-stale-raw-bytes.md) |
| [T05](areas/tests.md#tests-t05) | high | _debug_flush deletes the spooled pixels and raw bytes even when library.save failed; the test only asserts that nothing raised | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [CC-11](areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-11) | high | Export rolls can export a walk's 300 dpi prescan entry as 'frame NN at full resolution': roll_entry_index joins on film.frame, which walk prescans share, and the last entry in directory order wins | [P21](problems/P21-roll-to-library-join.md) |
| [CC-A1](areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-a1) | high | The roll's folder, approved.json's folder and a reopened roll's folder are derived three different ways, so commissioning scatters one roll across folders and the non-derivable approved.json can land in a shared 'rolls/roll/' | [P18](problems/P18-roll-folder-identity.md) |
| [RX-2](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-2) | high | roll.json/survey.json are truncated in place every frame; a kill or ENOSPC mid-write empties it, the roll disappears from the list, cannot be reopened, and the next resume silently overwrites it | [P19](problems/P19-roll-manifests.md) |
| [RX-4](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-4) | high | tools/scan_roll.py --start-at 'resume' replaces the existing roll.json with a fresh manifest, contrary to README and its own docstring | [P19](problems/P19-roll-manifests.md) |
| [RX-5](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-5) | high | Unnamed rolls share one local-date folder: a second walk (or CLI roll) the same day overwrites the first walk's survey.json and prescanNN.tif, and two strips' library entries share one roll key | [P18](problems/P18-roll-folder-identity.md) |
| [RX-15](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-15) | high | A walk made with tools/scan_roll.py --dry-run files nothing in the library: its prescans exist only as corrected 8-bit prescanNN.tif | [P08](problems/P08-passes-never-filed.md) |
| [RXV-1](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rxv-1) | high | Debug filing deletes the spooled raw bytes and pixels even when library.save failed, so a full or unwritable library destroys the only copy | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [TP-14](areas/transport-protocol.md#transport-protocol-tp-14) | medium | 7200 dpi stagger realignment is baked into the 'raw' scan.tif without being recorded, and the session then drops the raw bytes | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [TP-15](areas/transport-protocol.md#transport-protocol-tp-15) | medium | Per-pass command and response state needed to re-derive a pass is not recorded | [P09](problems/P09-record-missing-parameters.md) |
| [TP-16](areas/transport-protocol.md#transport-protocol-tp-16) | medium | INQUIRY (device identity and firmware) is never stored in any library entry | [P09](problems/P09-record-missing-parameters.md) |
| [TP-A1](areas/transport-protocol.md#transport-protocol-tp-a1) | medium | A pass that fails during or after its read is never filed, even in debug mode: its partial raw bytes are discarded | [P07](problems/P07-failed-and-short-passes-lose-bytes.md) |
| [TP-A3](areas/transport-protocol.md#transport-protocol-tp-a3) | medium | tools/scan.py holds every pass in memory and files them only after the with-block, so an exception mid-bracket loses all completed passes | [P14](problems/P14-failure-paths-keep-driving-device.md) |
| [DDF-06](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-06) | medium | Commands and parameters that define a pass are not recorded: byte14, slide_init_param, quality bits, gain-offset extras, GET PARAMETERS bytes, INQUIRY | [P09](problems/P09-record-missing-parameters.md) |
| [DDF-07](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-07) | medium | At 7200 dpi scan.tif is not the plain decode, nothing records the realignment, and reconstruct flags every such entry | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [DDF-08](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-08) | medium | Roll and bracket frames are recorded as a commanded exposure (exposure_metered=False) and carry no metering evidence | [P09](problems/P09-record-missing-parameters.md) |
| [DDF-10](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-10) | medium | The session's shape guard drops a pass's own raw bytes on every short read (and at 7200 dpi) | [P07](problems/P07-failed-and-short-passes-lose-bytes.md) |
| [DDF-13](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-13) | medium | A reused shading reference is filed as if measured this session; its origin and age are not recorded | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [DDF-14](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-14) | medium | The capture time is recorded at spool time and then discarded; debug entries are named and dated by flush time | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [DDF-A1](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-a1) | medium | A roll prescan that a correction replaced is never filed, and the docstring's claim that debug filing covers it is false for every front end | [P08](problems/P08-passes-never-filed.md) |
| [DP-06](areas/demo-parity.md#demo-parity-dp-06) | medium | Demo entries are indistinguishable from real scans: the 'demo' flag is dropped by library.save, and INQUIRY is never recorded | [P09](problems/P09-record-missing-parameters.md) |
| [LIB-09](areas/library.md#library-lib-09) | medium | 7200 dpi stagger realignment is baked into scan.tif, unrecorded, and not reproduced by reconstruct/decode_raw/migrate-raw; sign ignores read direction | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [LIB-10](areas/library.md#library-lib-10) | medium | Entries are written non-atomically; a crash leaves an orphan directory that verify and reconstruct never see, and the collision check can reuse it | [P10](problems/P10-non-atomic-writes.md) |
| [LIB-12](areas/library.md#library-lib-12) | medium | The session's layout guard drops the raw bytes of any truncated (short-read) pass | [P07](problems/P07-failed-and-short-passes-lose-bytes.md) |
| [LIB-13](areas/library.md#library-lib-13) | medium | Metering evidence and 'metered' flag are lost for roll frames and brackets | [P09](problems/P09-record-missing-parameters.md) |
| [LIB-23](areas/library.md#library-lib-23) | medium | Debug spool is unrecoverable if the process dies before close(), and lives in system temp (possibly tmpfs/RAM) | [P03](problems/P03-debug-spool-not-crash-safe.md) |
| [LIB-A2](areas/library.md#library-lib-a2) | medium | Entry does not record where its shading reference came from (reused file vs. calibrated this session) or when | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [SR-14](areas/session-roll.md#session-roll-sr-14) | medium | Rotating or flipping during a walk changes how prescanNN.tif is written, but the manifest keeps the orientation from the start | [P20](problems/P20-rotation-during-walk.md) |
| [SR-16](areas/session-roll.md#session-roll-sr-16) | medium | Library records drop the inquiry, the transport position and the roll index; single scans record no position and nudges are recorded nowhere | [P09](problems/P09-record-missing-parameters.md) |
| [SR-17](areas/session-roll.md#session-roll-sr-17) | medium | Resuming an old roll rewrites its frame numbers in place, destroying the originally recorded numbering | [P19](problems/P19-roll-manifests.md) |
| [SR-22](areas/session-roll.md#session-roll-sr-22) | medium | Partially written library entries are invisible to entries()/verify(); a reindex failure reports a complete entry as unfiled | [P10](problems/P10-non-atomic-writes.md) |
| [SR-26](areas/session-roll.md#session-roll-sr-26) | medium | The two roll writers label library entries differently; the GUI join only parses the session's form | [P21](problems/P21-roll-to-library-join.md) |
| [FU-02](areas/framing-units.md#framing-units-fu-02) | medium | prescanNN.tif orientation is not recorded per file; rotating or flipping during a walk corrupts the stored walk and duplicates survey entries | [P20](problems/P20-rotation-during-walk.md) |
| [FU-A2](areas/framing-units.md#framing-units-fu-a2) | medium | CLI dry-run walk (tools/scan_roll.py --dry-run) files no library entry for its prescans; the hold references for --approved exist only as corrected TIFFs | [P08](problems/P08-passes-never-filed.md) |
| [GUI1-09](areas/gui-part1.md#gui-part1-gui1-09) | medium | Each commission replaces approved.json with only the frames ticked this time, so earlier frames' positions and turns are lost (written non-atomically) | [P19](problems/P19-roll-manifests.md) |
| [GUI1-21](areas/gui-part1.md#gui-part1-gui1-21) | medium | The Film panel's 'frame' field is stamped on every frame of a roll, which breaks the roll-to-library join | [P21](problems/P21-roll-to-library-join.md) |
| [GUI2-04](areas/gui-part2.md#gui-part2-gui2-04) | medium | Each commission replaces approved.json wholesale, non-atomically, and a damaged file reads back as 'no decisions' with nothing said | [P19](problems/P19-roll-manifests.md) |
| [GUI2-16](areas/gui-part2.md#gui-part2-gui2-16) | medium | Decisions in an open sheet are lost when the main window quits or another roll is opened | [P22](problems/P22-contact-sheet-state.md) |
| [OUT-09](areas/outputs.md#outputs-out-09) | medium | Non-atomic tiff.write is used to rewrite library files in place; prescan.tif has no checksum | [P10](problems/P10-non-atomic-writes.md) |
| [OUT-11](areas/outputs.md#outputs-out-11) | medium | A corrupt settings file is discarded silently and overwritten; save failures are silent | [P24](problems/P24-disk-full-and-quitting.md) |
| [OUT-13](areas/outputs.md#outputs-out-13) | medium | Bracket membership, merge parameters and metering are not recorded in the library, so a bracket cannot be re-merged offline | [P09](problems/P09-record-missing-parameters.md) |
| [OUT-15](areas/outputs.md#outputs-out-15) | medium | A roll frame's library prescan.tif is the corrected prescan, unlabelled | [P08](problems/P08-passes-never-filed.md) |
| [CLI-11](areas/cli-operator-tools.md#cli-operator-tools-cli-11) | medium | library.save silently drops the INQUIRY every CLI caller passes, plus bracket_* and roll_index/roll_position, so brackets cannot be regrouped from the library | [P09](problems/P09-record-missing-parameters.md) |
| [CLI-12](areas/cli-operator-tools.md#cli-operator-tools-cli-12) | medium | Roll frames are filed as exposure_metered=false with no metering evidence even when every frame was metered | [P09](problems/P09-record-missing-parameters.md) |
| [CLI-18](areas/cli-operator-tools.md#cli-operator-tools-cli-18) | medium | `library.py verify` cannot see half-written entries or prescan.tif; says 'library is intact' after an interrupted save | [P10](problems/P10-non-atomic-writes.md) |
| [CLI-19](areas/cli-operator-tools.md#cli-operator-tools-cli-19) | medium | Roll entries store prescan.tif as corrected 8-bit pixels with no raw bytes, no checksum and no 'corrected' label; the CLI drops raw_prescan | [P08](problems/P08-passes-never-filed.md) |
| [PA-A2](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-a2) | medium | The library's re-decode paths skip the 7200 dpi stagger realignment that scan() bakes into scan.tif, so every such entry fails reconstruct, and the entry does not record that it was realigned | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [D09](areas/docs-readme-claude.md#docs-readme-claude-d09) | medium | 7200 dpi scan.tif is stagger-realigned (4 rows trimmed), not 'the decode alone'; reconstruct flags it as changed forever | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [DOC-07](areas/docs-plans-todo.md#docs-plans-todo-doc-07) | medium | 7200 dpi column realignment is baked into scan.tif, not recorded, and not reproducible by reconstruct or decode_raw | [P06](problems/P06-7200dpi-realignment-not-recorded.md) |
| [DOC-11](areas/docs-plans-todo.md#docs-plans-todo-doc-11) | medium | `--reuse` loads any cached reference silently, and entries do not record where the reference came from (TODO open item, still present) | [P05](problems/P05-calibration-not-stored-exactly.md) |
| [DOC-A2](areas/docs-plans-todo.md#docs-plans-todo-doc-a2) | medium | Bracket membership, ratio and metering evidence are dropped from filed bracket passes; the merged result is never filed | [P09](problems/P09-record-missing-parameters.md) |
| [T07](areas/tests.md#tests-t07) | medium | Roll frame entries store the corrected prescan as prescan.tif; the session roll path is never tested with raw_image, raw_prescan or prescan_meta | [P08](problems/P08-passes-never-filed.md) |
| [T08](areas/tests.md#tests-t08) | medium | library.save is not atomic, verify cannot see partial entries, and prescan/shading/mask have no checksums; none of it is tested | [P10](problems/P10-non-atomic-writes.md) |
| [T10](areas/tests.md#tests-t10) | medium | Entries drop the device INQUIRY, the calibration bytes and MODE SELECT choices; the 'keeps everything needed' test checks none of them | [P09](problems/P09-record-missing-parameters.md) |
| [T18](areas/tests.md#tests-t18) | medium | tools/scan_roll.py --dry-run files nothing in the library and forces debug off; the test never checks library entries | [P08](problems/P08-passes-never-filed.md) |
| [AF1](areas/tests.md#tests-af1) | medium | Session shape guard discards the raw bytes of every short read (EndOfData) and every 7200 dpi pass, because it compares layout.lines (requested) with the decoded or realigned height | [P07](problems/P07-failed-and-short-passes-lose-bytes.md) |
| [FE-06](areas/frame-edges-detectors.md#frame-edges-detectors-fe-06) | medium | Automated edge decisions cannot be re-derived: the record keeps only the voted sides. Member answers, the roll context and the detector code identity are dropped, and approved.json keeps only offset_mm and source | [P09](problems/P09-record-missing-parameters.md) |
| [CIP-1](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-1) | medium | Provenance names the working tree at filing time, not the code the process imported; lazy imports let one process run two branches | [P09](problems/P09-record-missing-parameters.md) |
| [CIP-2](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-2) | medium | driver_dirty reports 'clean' when git is absent or refuses, is true for any untracked file, and no diff, branch or full sha is kept | [P09](problems/P09-record-missing-parameters.md) |
| [CIP-5](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-5) | medium | The SLIDE bytes that placed a frame are never recorded; spent_mm and asked_mm are derived with the unversioned law, so the transport cannot be re-evaluated | [P09](problems/P09-record-missing-parameters.md) |
| [CC-A2](areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-a2) | medium | The pre-correction prescan's raw bytes are never filed; the _file docstring says a debug entry covers it, but ScanSession forces debug off | [P08](problems/P08-passes-never-filed.md) |
| [RX-6](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-6) | medium | A frame is recorded as done (GUI) or as having its file (CLI) before anything is written; after a crash or a writer failure, resume skips frames that have no file and no entry | [P19](problems/P19-roll-manifests.md) |
| [RX-8](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-8) | medium | Every writer except settings writes its final path in place with no temp+rename and no fsync; partial files survive under ordinary names and some make whole rolls unreadable | [P10](problems/P10-non-atomic-writes.md) |
| [RX-9](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-9) | medium | In-place rewrites of complete library entries (migrate-raw/migrate-direction --write, uniformity 'redo') are not atomic; an interruption makes an entry vanish or destroys the only prescan.tif | [P10](problems/P10-non-atomic-writes.md) |
| [RX-11](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-11) | medium | approved.json is rewritten whole with only this commission's ticked frames; on a resume it erases the earlier frames' turns and flips, which roll export uses; a truncated file reads as empty | [P19](problems/P19-roll-manifests.md) |
| [RXV-3](areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rxv-3) | medium | verify cannot detect damage to shading.npz, ccd_mask.bin or prescan.tif: none has a checksum, and prescan.tif is not checked at all | [P10](problems/P10-non-atomic-writes.md) |
| [DDF-11](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-11) | low | A READ whose payload fully arrived but whose closing status is CHECK is discarded, so raw.bin.gz is not every byte received | -- |
| [DDF-16](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-16) | low | Metering probe passes are filed with film='negative' whatever the film is | -- |
| [DDF-18](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-18) | low | Bracket and roll membership fields are not in debug entries or in scan.json | -- |
| [DDF-19](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-19) | low | decode_index silently drops lines with unknown tags, never checks the second tag byte or the interleave, and does not record per-plane counts | -- |
| [DDF-20](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-20) | low | apply_shading leaves columns past the mask's used entries uncorrected without refusing | -- |
| [DDF-A4](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-a4) | low | duration_s includes host-side correction and finish-scan polling, not only device time | -- |
| [DDF-A5](areas/decode-and-debug-filing.md#decode-and-debug-filing-ddf-a5) | low | prescan.tif in roll-frame entries holds shading-corrected 8-bit pixels with no raw bytes and no marker | -- |
| [LIB-20](areas/library.md#library-lib-20) | low | Entry id and 'created' are the filing time, not the capture time; debug's captured timestamp is discarded | -- |
| [LIB-21](areas/library.md#library-lib-21) | low | verify has no integrity check for shading.npz, ccd_mask.bin or prescan.tif, and skips raw checks when scan.tif is missing | -- |
| [GUI2-25](areas/gui-part2.md#gui-part2-gui2-25) | low | Frames the detector has not reached yet are recorded in approved.json with source 'operator' | -- |
| [CLI-24](areas/cli-operator-tools.md#cli-operator-tools-cli-24) | low | The roll manifest omits settings needed to understand or resume it (fast_ir, nudge, correct, reuse, no_shading, stock) | -- |
| [DOC-28](areas/docs-plans-todo.md#docs-plans-todo-doc-28) | low | `library.save` accepts `inquiry` from every caller and never writes it | -- |
| [T16](areas/tests.md#tests-t16) | low | roll.json is rewritten in place, and a corrupt manifest is read as empty on resume; untested | -- |
| [CIP-4](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-4) | low | PROTOCOL_REVISION was not bumped when the bytes or sequence sent changed, and revision 6's definition was rewritten in place after entries were filed under it | -- |
| [CIP-7](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-7) | low | image.sha256 hashes the TIFF container, not the pixels; migrate-direction can leave it permanently stale | -- |
| [CIP-11](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-11) | low | Roll manifests, approved.json, delivered TIFFs and scan.py sidecars carry no code identity; a resumed roll merges runs from different code | -- |
| [CIP-A1](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-a1) | low | The INQUIRY (vendor, product, firmware, CCD geometry) is passed to every library.save and silently discarded | -- |
| [CIP-A3](areas/code-identity-and-protocol-revision.md#code-identity-and-protocol-revision-cip-a3) | low | provenance() runs git in the package's parent directory, so a non-editable install records an unrelated repository's commit | -- |
| [CC-14](areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-14) | low | Roll/survey manifests, approved.json and the shared calibration cache are rewritten in place; a crash during the per-frame rewrite makes a resume discard every earlier frame's record | -- |
| [TP-29](areas/transport-protocol.md#transport-protocol-tp-29) | info | The SLIDE law change was not accompanied by a PROTOCOL_REVISION bump | -- |
| [OUT-27](areas/outputs.md#outputs-out-27) | info | Delivery parameters are not recorded in the library entry | -- |
| [DOC-30](areas/docs-plans-todo.md#docs-plans-todo-doc-30) | info | Red plane 'one line out' (TODO) is unaddressed in decode, and filter_offsets are recorded but never applied | -- |
