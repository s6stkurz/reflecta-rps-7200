# Does the library hold the exact bits?

[Back to the summary](README.md)

The owner's central requirement: every scan in the library with its exact raw bytes, its
shading reference, its CCD mask and every parameter and command needed, so that everything
can be recalculated and evaluated later with newer code.

## Where it stands at 03aacba

**Met** for the passes the software files itself -- a window Scan or Prescan, a
`tools/scan.py` pass and bracket, a roll frame, a walk prescan:

- the raw bytes as read (`raw.bin.gz`, or `raw.bin` until compacted);
- the raw decode as `scan.tif`, with its read direction and, at 7200 dpi, the stagger
  realignment it replays;
- the reference and mask;
- every command sent with what came back;
- when the pass began, where its reference came from, and the calibration's own bytes.

Entries are atomic and checksummed; `reconstruct` re-decodes them with today's code, and
`verify` finds cut-short entries, orphans and damaged files.

**Not met** for these kinds of pass:

- **A real roll frame's prescans.** The frame entry keeps the corrected 8-bit prescan with no
  bytes, and the raw prescan and the pre-move prescan are dropped (LIB-01, SR-04, FR-02).
  The framing decisions made from them cannot be re-derived.
- **Metering probes and hold/aim verification prescans.** They reach the library only through
  debug filing, which is off by default (DBG-3). With it on, they are filed with
  `film = negative` whatever is loaded (DBG-A1, FR-08).
- **A pass that fails after its bytes were read**, and a truncated one. The first is never
  filed (DBG-4, TP-A1); the second is filed as if complete (TP-02).
- **Hold and aim moves.** Their SLIDE bytes are recorded nowhere; only millimetres derived
  through a law that has since changed (DOC-06 in docs-plans-todo).
- **The calibration archive.** It is written but read by nothing, and linked from an entry
  only by a path relative to the working directory, and not at all for a reused reference
  (TP-07, LIB-05, CSA-09).

A **failed save** loses the pass outright (LIB-02, SR-05), and **`reconstruct`** can pass a
decode that now raises (LIB-03). Both undermine the promise from the other side.

## Every finding about exactness or completeness

114 findings in the categories data-integrity and library-completeness, all areas:

| Finding | Severity | Title | Problem |
|---|---|---|---|
| [DBG-1](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-1) | high | debug_claim deletes the spooled copy before the claimant's own filing is confirmed | -- |
| [DBG-3](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-3) | high | With debug off (the default), prescans, before-prescans, metering probes and hold/aim passes never reach the library with raw bytes | -- |
| [LIB-01](areas/library.md#library-lib-01) | high | Real-roll frame entries file the prescan CORRECTED, without raw bytes or mask, and discard the raw prescan and prescan_before | -- |
| [LIB-02](areas/library.md#library-lib-02) | high | A library.save failure loses the whole frame: in-memory raw bytes dropped and delivered copies never attempted | -- |
| [SR-01](areas/session-roll.md#session-roll-sr-01) | high | Debug-filing claim is made at hand-off, and the spool is flushed before the writer finishes: a pass whose filing fails is deleted from the debug spool too | -- |
| [SR-04](areas/session-roll.md#session-roll-sr-04) | high | Outside debug mode, a real roll keeps no raw data for its prescans: raw_prescan and prescan_before are dropped, the stored prescan.tif is corrected and unlabelled, and a failed frame's prescan is not filed | -- |
| [FR-02](areas/framing-units.md#framing-units-fr-02) | high | The prescans that framing decisions are made from are not kept raw; the pre-move prescan is discarded and the frame entry's prescan.tif is corrected but unlabelled | -- |
| [GUI1-01](areas/gui-part1.md#gui-part1-gui1-01) | high | Export joins entries to a roll by roll name only: a duplicated roll and its original export each other's (newest) frames | -- |
| [GUI2-01](areas/gui-part2.md#gui-part2-gui2-01) | high | Export joins library entries to rolls by roll name only: a Duplicate, a reused name or a renamed-and-reused name exports another roll's frames | -- |
| [DOC-01](areas/docs-readme-claude.md#docs-readme-claude-doc-01) | high | Roll frames' prescan.tif is stored corrected, without raw bytes, mask, commands or a label | -- |
| [CSA-01](areas/changes-since-first-audit.md#changes-since-first-audit-csa-01) | high | Debug spool keeps every pass (claimed ones included) in the OS temp dir until close(): GUI lifetime / whole roll, tens of GB | -- |
| [T-04](areas/tests.md#tests-t-04) | high | Tests enforce that a real roll's prescans (and every pass only debug filing keeps) are stored without raw bytes, corrected, with their pass record dropped | -- |
| [CRA-A1](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-a1) | high | debug_claim marks a pass as filed when it is only queued; the debug flush at close() then deletes the spool copy of a pass whose library.save failed, and close runs before the writer has finished | -- |
| [TP-02](areas/transport-protocol.md#transport-protocol-tp-02) | medium | EndOfData mid-read marks the pass complete: truncated pass returned as success, device not marked suspect | -- |
| [TP-06](areas/transport-protocol.md#transport-protocol-tp-06) | medium | A failed or abandoned pass leaves no record: its command log, sense bytes and partial raw bytes are discarded | -- |
| [TP-07](areas/transport-protocol.md#transport-protocol-tp-07) | medium | Raw calibration bytes are kept outside the library, read by nothing, and not kept at all on the non-ensure_shading path | -- |
| [TP-10](areas/transport-protocol.md#transport-protocol-tp-10) | medium | Force Abort skips DirectScanner.close(), so the session's debug spool is never filed and its location is never reported | -- |
| [TP-A1](areas/transport-protocol.md#transport-protocol-tp-a1) | medium | A pass that was read completely but that the current decoder rejects is discarded with its raw bytes, so newer code can never re-decode it | -- |
| [DBG-6](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-6) | medium | Calibration raw bytes are archived outside the library, unlinked for reused references, and read by nothing | -- |
| [DBG-8](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-8) | medium | Debug spool is orphaned after force_abort, a crash, or a script that never calls close(); no tool files it and its sidecars are incomplete | -- |
| [DBG-A1](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-a1) | medium | Debug-filed metering probes and hold prescans record film 'negative' whatever is loaded, and carry no role or link to the frame they served | -- |
| [DEMO-03](areas/demo-parity.md#demo-parity-demo-03) | medium | The driver's hold and aim verification prescans record film='negative' whatever the roll's film, and the demo copies it | -- |
| [LIB-05](areas/library.md#library-lib-05) | medium | The calibration's raw bytes are not in the entry, and the link to them is CWD-relative, unverified, and missing for loaded references and for tools/uniformity.py | -- |
| [LIB-06](areas/library.md#library-lib-06) | medium | migrate-raw rewrites labelled entries without the laundering guard, and can leave the only corrected rendition where nothing reads it | -- |
| [SR-05](areas/session-roll.md#session-roll-sr-05) | medium | When library.save fails, no delivered copy is attempted, so the frame is lost entirely | -- |
| [SR-06](areas/session-roll.md#session-roll-sr-06) | medium | A walk frame whose aim or hold fails part-way is filed with the raw bytes of a later verification prescan | -- |
| [SR-07](areas/session-roll.md#session-roll-sr-07) | medium | A renumbered legacy walk keeps its old prescanNN.tif names, and walking frames again overwrites files that other frames' records still point to | -- |
| [SR-10](areas/session-roll.md#session-roll-sr-10) | medium | After force_abort the scanner is never closed, so the debug spool is never filed and its location is never reported | -- |
| [FR-08](areas/framing-units.md#framing-units-fr-08) | medium | Hold and aim verification prescans are filed with film='negative' whatever the roll's film | -- |
| [GUI1-05](areas/gui-part1.md#gui-part1-gui1-05) | medium | Deleting a roll folder destroys data that exists nowhere else, while the dialog says only approved.json is at risk | -- |
| [GUI2-03](areas/gui-part2.md#gui-part2-gui2-03) | medium | Contact-sheet decisions are stored per folder name in gui-settings and come back onto a new strip walked into a folder with that name | -- |
| [OUT-A1](areas/outputs.md#outputs-out-a1) | medium | tools/scan.py files its held passes outside the Ctrl-C guard and without per-pass error handling, so a Ctrl-C or one failed save during filing loses the remaining raw bytes | -- |
| [CLI-07](areas/cli-operator-tools.md#cli-operator-tools-cli-07) | medium | Tools claim a pass from debug filing before they have filed it, so with RPS7200_DEBUG=1 a failed save still loses the pass | -- |
| [CLI-09](areas/cli-operator-tools.md#cli-operator-tools-cli-09) | medium | Frame entries from a real roll store a corrected prescan.tif, with no raw bytes, no CCD mask and no label | -- |
| [CLI-11](areas/cli-operator-tools.md#cli-operator-tools-cli-11) | medium | Resuming a roll ignores the earlier run's settings: another --dpi/--ir/--film/--meter is mixed in silently and the recorded settings are overwritten | -- |
| [CLI-A1](areas/cli-operator-tools.md#cli-operator-tools-cli-a1) | medium | Ctrl-C during the post-close filing is no longer deferred, and loses the passes or frames still held only in memory | -- |
| [DOC-06](areas/docs-readme-claude.md#docs-readme-claude-doc-06) | medium | debug_claim deletes the spooled copy before the claiming caller has filed; tools/scan.py's filing loop is unguarded | -- |
| [DOC-07](areas/docs-readme-claude.md#docs-readme-claude-doc-07) | medium | The debug spool lives in the system temp directory, so 'kept after a crash' depends on the OS not clearing it | -- |
| [DOC-17](areas/docs-readme-claude.md#docs-readme-claude-doc-17) | medium | A pass whose decode or post-read check raises is never filed, even with debug on | -- |
| [DOC-06](areas/docs-plans-todo.md#docs-plans-todo-doc-06) | medium | The SLIDE bytes of hold/aim moves are recorded nowhere; entries keep only mm derived through a law that changed without a revision bump | -- |
| [CSA-02](areas/changes-since-first-audit.md#changes-since-first-audit-csa-02) | medium | debug_claim deletes the safety copy before the caller has filed; a failed save then loses the pass entirely | -- |
| [CSA-09](areas/changes-since-first-audit.md#changes-since-first-audit-csa-09) | medium | Calibration archive is write-only and unlinked from entries corrected with a reused reference | -- |
| [CSA-14](areas/changes-since-first-audit.md#changes-since-first-audit-csa-14) | medium | Real-roll frame prescans: raw never filed without debug, and prescan.tif stored corrected with no record saying so | -- |
| [CSA-17](areas/changes-since-first-audit.md#changes-since-first-audit-csa-17) | medium | migrate-raw --write swaps scan.tif in two renames; interruption leaves an entry with no scan.tif | -- |
| [T-02](areas/tests.md#tests-t-02) | medium | GUI tests read and rewrite the operator's real repo-root gui-settings.json (test residue is present in the file now) | -- |
| [T-03](areas/tests.md#tests-t-03) | medium | A failed library filing loses the pass everywhere (no delivered copy, claimed debug spool deleted), and no test covers it | -- |
| [T-08](areas/tests.md#tests-t-08) | medium | The kept calibration bytes are never shown to reproduce the reference, and nothing in the code reads them | -- |
| [T-A1](areas/tests.md#tests-t-a1) | medium | After a force abort the session never calls close(), so debug-spooled passes are never filed and nothing says where they are | -- |
| [CRA-03](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-03) | medium | A window walk writes survey.json naming prescanNN.tif before the writer has written it; a failed filing or kill leaves the survey pointing at a missing file or at the previous walk's picture | -- |
| [CRA-04](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-04) | medium | Contact-sheet decisions reach disk only when the sheet closes; a crash or kill while it is open loses them, and a save that fails at quit is reported into the window being destroyed | -- |
| [CRA-05](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-05) | medium | Duplicate roll is a copytree on the UI thread; a failure or force-quit leaves a partial copy that lists as a roll, and Delete's rmtree can half-delete one | -- |
| [CONC-03](areas/thread-and-process-concurrency.md#thread-and-process-concurrency-conc-03) | medium | Roll frames never file the metering that set their exposure, and every metered roll frame is recorded as exposure_metered=False | -- |
| [RDM-01](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-01) | medium | Decisions drawn from other passes are recorded by outcome only: no metering region, no reversal evidence, no 'check this frame' flag, no hold reference identity | -- |
| [RDM-A1](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-a1) | medium | Every metered roll frame, and every auto-exposed bracket pass, is filed as 'not metered' with no metering evidence | -- |
| [PLAT-02](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-02) | medium | A debug spool that the OS temp cleaner deletes mid-session is never recreated, and the flush reports deleted passes as 'kept in' the spool | -- |
| [MES-03](areas/unread-analysis-and-measurement-code.md#unread-analysis-and-measurement-code-mes-03) | medium | collect_vignette_study verifies only the source's raw bytes: it ignores the record's other checksums and never checks the copies | -- |
| [TP-03](areas/transport-protocol.md#transport-protocol-tp-03) | low | Transport drops a fully received READ payload when the trailing status is CHECK; FAIL/ERROR trailing statuses are ignored | -- |
| [TP-19](areas/transport-protocol.md#transport-protocol-tp-19) | low | Command log fills with refused NoDataYet READs and omits every successful image READ | -- |
| [TP-31](areas/transport-protocol.md#transport-protocol-tp-31) | low | A failed CCD-mask read after a complete calibration throws away the whole calibration, bytes included | -- |
| [TP-A4](areas/transport-protocol.md#transport-protocol-tp-a4) | low | The INQUIRY reply is kept only as parsed fields; its raw bytes are never recorded | -- |
| [DBG-11](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-11) | low | Payload followed by CHECK CONDITION is discarded by the transport, losing the last chunk of a pass or calibration | -- |
| [DBG-13](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-13) | low | Debug capture copies meta before callers add bracket, roll and registration fields | -- |
| [DBG-14](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-14) | low | set_gain_offset silently wraps gain and offset above 255 while the record keeps the unwrapped value | -- |
| [DBG-A4](areas/decode-and-debug-filing.md#decode-and-debug-filing-dbg-a4) | low | A failed pass's command log keeps recording after scan() or calibrate_shading() raise before the logger is stopped | -- |
| [DEMO-13](areas/demo-parity.md#demo-parity-demo-13) | low | Demo entries do not record how the pass was derived from its source, so they cannot be re-derived and synthetic content is not marked | -- |
| [LIB-15](areas/library.md#library-lib-15) | low | save() writes the irreplaceable raw bytes last, in place, and without fsync | -- |
| [LIB-A2](areas/library.md#library-lib-a2) | low | A refused command is recorded only by its exception class name | -- |
| [SR-08](areas/session-roll.md#session-roll-sr-08) | low | Roll-folder TIFFs are overwritten in place, non-atomically, on a rescan or re-walk, and roll.json forgets the earlier take | -- |
| [SR-17](areas/session-roll.md#session-roll-sr-17) | low | Sub-frame SLIDE commands sent by holds, aims and the window's nudges are recorded only as millimetres derived from today's law; the param bytes are never persisted | -- |
| [SR-18](areas/session-roll.md#session-roll-sr-18) | low | The session's roll.json and survey.json do not say how a run ended, and differ from the tool's format for the same files | -- |
| [SR-A1](areas/session-roll.md#session-roll-sr-a1) | low | A hold or aim that raises part-way loses every record of the SLIDE moves it already sent, and the walk's travel budget never counts them | -- |
| [GUI1-14](areas/gui-part1.md#gui-part1-gui1-14) | low | 'Nothing already there is overwritten' holds only for the main file; DNG companions and the Pillow-less TIFF fallback write to unchecked names | -- |
| [GUI1-17](areas/gui-part1.md#gui-part1-gui1-17) | low | Force abort leaves the RPS7200_DEBUG spool unfiled | -- |
| [GUI2-13](areas/gui-part2.md#gui-part2-gui2-13) | low | A reopened walk loses the link between each reference prescan and its library entry: approved.json gets reference_entry "" | -- |
| [GUI2-14](areas/gui-part2.md#gui-part2-gui2-14) | low | Commissioning a sheet opened from outside session.rolls scans into a new folder without carrying the walk, so the scanned roll has no survey | -- |
| [GUI2-20](areas/gui-part2.md#gui-part2-gui2-20) | low | 'Nothing already there is overwritten' is untrue for the JPEG sidecar DNG, for the no-Pillow TIFF fallback, and after 999 name clashes | -- |
| [GUI2-A2](areas/gui-part2.md#gui-part2-gui2-a2) | low | Library entry paths written into approved.json, roll.json and roll_membership are relative to the window's working directory | -- |
| [OUT-08](areas/outputs.md#outputs-out-08) | low | A bracket's delivered JSON describes the longest pass while the pixels are on the shortest pass's scale; merge parameters are not recorded | -- |
| [OUT-17](areas/outputs.md#outputs-out-17) | low | library.compact rewrites the TIFFs and records fresh checksums without checking the old ones | -- |
| [CLI-18](areas/cli-operator-tools.md#cli-operator-tools-cli-18) | low | Calibration raw bytes live outside the library behind a cwd-relative pointer, and --reuse drops the link to them | -- |
| [CLI-25](areas/cli-operator-tools.md#cli-operator-tools-cli-25) | low | `duplicates --delete` removes entries with rmtree on the records' word, without checking the survivor on disk | -- |
| [CLI-26](areas/cli-operator-tools.md#cli-operator-tools-cli-26) | low | migrate-raw rewrites labelled entries without checking the new decode against the stored pixels | -- |
| [CLI-A2](areas/cli-operator-tools.md#cli-operator-tools-cli-a2) | low | Debug filing ignores --library: the probes, holds and aim prescans of a run go to ./library or RPS7200_DEBUG_ROOT, apart from the run's own entries | -- |
| [PAT-02](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pat-02) | low | The RPS7200_DEBUG gate accepts any non-empty value, but DirectScanner files only for 1/true/yes/on, so RPS7200_DEBUG=0 runs the probe and files nothing | -- |
| [PAT-16](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pat-16) | low | roll_registration_walk records requested ladder positions, not commanded ones, leaves the film about 0.07 mm off home, overwrites a corpus on label reuse, and cannot be joined to its library entries | -- |
| [PAT-A1](areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pat-a1) | low | roll_registration_walk overwrites the driver's commanded distance with the requested one, and the restore comment claims a measurement the code does not make | -- |
| [DOC-09](areas/docs-readme-claude.md#docs-readme-claude-doc-09) | low | Walk and correction behaviour: the archived calibration bytes are never read or verified, and have no link from entries corrected by a reused reference | -- |
| [DOC-A1](areas/docs-readme-claude.md#docs-readme-claude-doc-a1) | low | An interrupted library.compact leaves an entry that verify reports as damaged, and nothing ever compacts a plain entry later | -- |
| [DOC-18](areas/docs-plans-todo.md#docs-plans-todo-doc-18) | low | The library docstring promises self-contained entries, but the calibration bytes behind shading.npz live outside and loaded references are not linked to them | -- |
| [DOC-A1](areas/docs-plans-todo.md#docs-plans-todo-doc-a1) | low | A roll frame entry's prescan.tif holds the corrected prescan, and its record does not say so | -- |
| [CSA-10](areas/changes-since-first-audit.md#changes-since-first-audit-csa-10) | low | compact() interrupted after replacing scan.tif leaves a record whose checksum no longer matches: verify raises a false corruption alarm | -- |
| [CSA-12](areas/changes-since-first-audit.md#changes-since-first-audit-csa-12) | low | force_abort (and any process kill) orphans the debug spool silently; nothing can file it | -- |
| [CSA-13](areas/changes-since-first-audit.md#changes-since-first-audit-csa-13) | low | Calibration ends 'successfully' on any refused read; the new guard catches only the timeout | -- |
| [CRA-06](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-06) | low | A delivered copy that fails or is interrupted part-way stays under its final name; the next export or rescan routes the good copy to `-2` | -- |
| [CRA-07](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-07) | low | An INCOMPLETE entry cannot be completed: nothing describing the pass (layout, meta, commands) reaches disk before the last step, and the marker says nothing about which pass it was | -- |
| [CRA-09](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-09) | low | The `.legacy` numbering copy and the per-run `.bak` are plain copyfiles; an interrupted `.legacy` is never redone, so the original numbering can be lost | -- |
| [CRA-A2](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-a2) | low | A kill during quit-time compaction leaves an entry whose scan.tif no longer matches its recorded checksum, and nothing ever finishes the compaction | -- |
| [CRA-A3](areas/crash-recovery-atomicity-inventory.md#crash-recovery-atomicity-inventory-cra-a3) | low | tools/uniformity.py 'redo' rewrites scan.json in place with write_text, bypassing _write_atomic | -- |
| [CONC-04](areas/thread-and-process-concurrency.md#thread-and-process-concurrency-conc-04) | low | A walk's survey.json names prescanNN.tif (fsynced) before the writer thread has written it; a missing prescan is silently dropped and a truncated one makes the whole walk unopenable | -- |
| [FE-05](areas/frame-edges-member-detectors.md#frame-edges-member-detectors-fe-05) | low | In-walk detector decisions cannot be re-derived: the record keeps neither the member answers nor which frames formed the roll context | -- |
| [RDM-03](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-03) | low | No record says which code reduced a shading reference: calibration.json has no provenance or reduction parameters, and a loaded reference records only a path and an mtime | -- |
| [RDM-A2](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-a2) | low | Hold and aim verification prescans are taken as film='negative' whatever the roll is, and that meta becomes the filed prescan's record | -- |
| [RDM-A3](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-a3) | low | Passes kept only by debug filing (metering probes, hold/aim prescans) go to ./library or RPS7200_DEBUG_ROOT rather than the caller's library, carry no role, and are not linked to the frame they served | -- |
| [RDM-A5](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-a5) | low | A deliberately uncorrected pass (shading=False) is filed with the session's shading.npz but no shading_origin | -- |
| [PLAT-03](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-03) | low | No path-length handling: Windows MAX_PATH failures surface as misleading ENOENT after the scan (and after the seek has moved the film), and roll names are unbounded | -- |
| [PLAT-09](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-09) | low | Links between rolls, entries and calibrations are stored as OS-native, cwd-relative path strings, and one of them is read back as a Path | -- |
| [PLAT-10](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-10) | low | Provenance depends on a `git` on PATH that accepts the checkout; without one every entry silently records driver_commit null | -- |
| [PLAT-11](areas/platform-portability-windows-macos.md#platform-portability-windows-macos-plat-11) | low | On Windows, a roll's frameNN.tif or prescanNN.tif held open by another program is not rewritten; the old picture stays under the name the new take's manifest records | -- |
| [MES-05](areas/unread-analysis-and-measurement-code.md#unread-analysis-and-measurement-code-mes-05) | low | collect_vignette_study refuses plain raw.bin entries, does not check raw.layout, and leaves out prescan.tif | -- |
| [LIB-22](areas/library.md#library-lib-22) | info | The record is serialised with `default=str`: non-JSON values in meta are silently stringified | -- |
| [FR-19](areas/framing-units.md#framing-units-fr-19) | info | approved.json records only the snapped offset; the detector's reading behind a sheet proposal is not persisted | -- |
| [OUT-18](areas/outputs.md#outputs-out-18) | info | The library's TIFF path is bit-exact on both implementations | -- |
| [CLI-29](areas/cli-operator-tools.md#cli-operator-tools-cli-29) | info | No tool compacts plain entries left by a window that was killed before compacting | -- |
| [RDM-04](areas/re-derivation-matrix-per-pass-kind.md#re-derivation-matrix-per-pass-kind-rdm-04) | info | Serialisation check: no traced meta field reaches json.dumps(default=str) as numpy, bytes or Path; the risk is latent | -- |
