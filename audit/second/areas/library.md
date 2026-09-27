# The library store

Area key `library`. 24 findings: 3 high, 7 medium, 12 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

The library store (rps7200/library.py, tools/library.py) plus the uniformity study (rps7200/uniformity.py, tools/uniformity.py), judged from the code only. One entry is a directory created with an atomic mkdir (`_reserve`, with -2/-3 suffixes). It holds: scan.tif (the raw decode, turned upright, with the 7200 dpi stagger trim; uint8/uint16 (H,W,C), deflate+predictor or plain); raw.bin.gz or raw.bin (the exact INDEX-line bytes as received, SHA-256 taken over the uncompressed content); shading.npz (the session reference, already reduced to float64); ccd_mask.bin (this pass's SCSI COPY mask); an optional prescan.tif; and scan.json (written atomically and fsynced, last), with an INCOMPLETE marker that is present until the record lands. Name collisions are handled correctly, and `corrections=` is honoured by `corrected()` and `reconstruct()`. `reconstruct` compares shape and values exactly (not dtype). `verify` does tell deliberately raw entries apart (sentinel, demo flag, ON_PURPOSE tag).


The significant gaps:

(1) On a real roll the frame entry's prescan.tif holds the CORRECTED 8-bit prescan, with no raw bytes, no mask of its own and no label saying it is corrected. The raw prescan pixels and prescan_before are thrown away unless debug filing is on.

(2) A failure in library.save, including a failure only to rewrite the derived index.json, costs the whole frame: the raw bytes held in memory and the operator's delivered copies, which are never attempted.

(3) `tools/library.py reconstruct` counts a decode that now RAISES as "nothing to decode from" and exits 0, so the regression gate passes over a broken decode.

(4) The calibration's raw bytes are never in the entry. They are reachable only through a CWD-relative path, and not at all for loaded references or for tools/uniformity.py.

(5) migrate-raw rewrites labelled entries without the laundering guard that it applies to mislabelled ones.


tools/uniformity.py capture has several broken paths. Metering always raises ShadingUnavailable. `--ir` is always refused, and only after a calibration. The calibration runs with the transport emptied, which is the state CLAUDE.md says preceded a wedge. Passes rejected with "redo" are still used by `analyse`, and as the base pass. The math in rps7200/uniformity.py checks out: the solve_components algebra, the register/align sign convention and the parity split are all consistent.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [LIB-01](#library-lib-01) | high | data-integrity | confirmed | Real-roll frame entries file the prescan CORRECTED, without raw bytes or mask, and discard the raw prescan and prescan_before |
| [LIB-02](#library-lib-02) | high | data-integrity | confirmed | A library.save failure loses the whole frame: in-memory raw bytes dropped and delivered copies never attempted |
| [LIB-03](#library-lib-03) | high | bug | confirmed | `reconstruct` counts a decode that now raises as 'nothing to decode from' and exits 0 |
| [LIB-04](#library-lib-04) | medium | error-handling | confirmed | A failure to rewrite the derived index.json makes a complete save() raise |
| [LIB-05](#library-lib-05) | medium | data-integrity | confirmed | The calibration's raw bytes are not in the entry, and the link to them is CWD-relative, unverified, and missing for loaded references and for tools/uniformity.py |
| [LIB-06](#library-lib-06) | medium | data-integrity | confirmed | migrate-raw rewrites labelled entries without the laundering guard, and can leave the only corrected rendition where nothing reads it |
| [LIB-07](#library-lib-07) | medium | bug | confirmed | tools/uniformity.py capture meters before any reference exists, so metering always raises ShadingUnavailable |
| [LIB-08](#library-lib-08) | medium | hardware-safety | confirmed | tools/uniformity.py calibrates with the transport emptied or in an unprompted state |
| [LIB-09](#library-lib-09) | medium | bug | confirmed | The uniformity study's IR phase can never run: the RGB calibration has no channel 3, and the refusal comes only after metering and a 3-4 minute calibration |
| [LIB-10](#library-lib-10) | medium | bug | confirmed | Uniformity passes rejected with 'redo' are still analysed, and as the oldest they become the base or the orientation used |
| [LIB-11](#library-lib-11) | low | error-handling | confirmed | Nothing compacts left-over plain entries, and an interrupted compact or migrate-direction leaves checksum mismatches that no rerun repairs |
| [LIB-12](#library-lib-12) | low | bug | confirmed | reconstruct compares values and shape but not dtype |
| [LIB-13](#library-lib-13) | low | error-handling | confirmed | reconstruct misreports raw-byte corruption: corrupt gzip reads as 'no raw bytes stored', corrupt plain raw.bin as a decode change |
| [LIB-14](#library-lib-14) | low | bug | confirmed | verify says nothing when a file listed in `files` (prescan.tif) is missing |
| [LIB-15](#library-lib-15) | low | data-integrity | confirmed | save() writes the irreplaceable raw bytes last, in place, and without fsync |
| [LIB-16](#library-lib-16) | low | error-handling | confirmed | decode_raw lets ScanReadError escape, and migrate-direction aborts mid-run on it |
| [LIB-17](#library-lib-17) | low | user-error | confirmed | Debug filing ignores the caller's library root, and tools/uniformity.py never claims its passes (double filing with debug on) |
| [LIB-18](#library-lib-18) | low | bug | confirmed | corrected() applies a reference with no mask one-to-one even when the reference is wider than the pass, and labels it 'applied' |
| [LIB-19](#library-lib-19) | low | demo-divergence | confirmed | DemoScanner has no debug filing, and verify has a demo carve-out: the second library writer is never exercised by --demo |
| [LIB-20](#library-lib-20) | low | bug | confirmed | `analyse` always prints 'pipeline: unknown' (reads a provenance key that does not exist) |
| [LIB-A1](#library-lib-a1) | low | error-handling | found-by-verifier | A corrupt shading.npz aborts reconstruct, migrate-raw and every export of the entry with an uncaught BadZipFile |
| [LIB-A2](#library-lib-a2) | low | data-integrity | found-by-verifier | A refused command is recorded only by its exception class name |
| [LIB-21](#library-lib-21) | info | test-gap | confirmed | No regression check covers the correction step: reconstruct tests only the decode |
| [LIB-22](#library-lib-22) | info | data-integrity | confirmed | The record is serialised with `default=str`: non-JSON values in meta are silently stringified |

## Findings in full

<a id="library-lib-01"></a>

### LIB-01 -- Real-roll frame entries file the prescan CORRECTED, without raw bytes or mask, and discard the raw prescan and prescan_before

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2620-2628`, `rps7200/session.py:2536-2541`, `rps7200/session.py:2567-2587`, `tools/scan_roll.py:752-755`, `tools/scan_roll.py:686-694`, `rps7200/direct.py:3968-3976`, `rps7200/direct.py:2076-2088`, `rps7200/library.py:269-270`, `rps7200/library.py:376-382`, `rps7200/demo.py:1473-1485`

**Doc claim:** CLAUDE.md 'Scans': "The library holds raw pixels; everything else is corrected"; "`library.save` takes raw pixels and `corrections=` is how a caller admits it is handing over something else"; "They file their own frames and prescans whatever it says". rps7200/library.py:241-250 ("``image`` must be the **raw** pixels").

As described, with one refinement: prescan_before is not a library entry on any path. A dry run writes it only as a corrected TIFF in the roll directory (file_entry=False, or tiff.write in scan_roll.py). A real roll writes nothing.

**Evidence (from the code):**

```text
session.py:2626-2628 `raw_image=rf.raw_image,
 prescan=rf.prescan,
 prescan_meta=rf.prescan_meta,` -- rf.prescan is `prescan_image, _ = self.prescan(... shading=shading ...)` (direct.py:3968), i.e. the corrected return of scan(); the raw one is `raw_prescan = self.last_pixels_raw` (3975), carried as rf.raw_prescan and never filed on a real roll. library.py:269 `tiff.write(str(path / "prescan.tif"), prescan, compress=compress)` and the record keeps only `{"file": "prescan.tif", "read_direction": ..., "carriage_state": ...}` (378-382). The session comments (2536-2541): "On a real roll they ride along with the frame instead ... that would file every one of them twice." prescan_before is written only inside `if job.dry_run:` (2567) and `elif args.dry_run:` (scan_roll.py:652/686). demo.py:1477-1479 relies on the unrecorded convention: "a prescan kept for the operator was corrected unless someone asked otherwise."
```

**Failure scenario:** Stefan runs a 38-frame roll from the window with debug off, which is the default. Every frame entry's prescan.tif is a corrected 8-bit picture with no raw.bin, no mask and no label. The registration numbers stored in the entry were measured on that prescan. A later fix to shading, to the decode of 8-bit passes, or to the frame-edge reader cannot be re-run on the prescans, and after an aim correction the pre-correction picture is gone for good.

**Fix:** File rf.raw_prescan as prescan.tif (or file the prescan as its own entry with its capture record, taken right after the prescan pass), and record the prescan's own meta, mask and bytes. Label the prescan's correction state in the record, as `corrections_applied` does for scan.tif. Keep prescan_before on real rolls as well.

<details><summary>Second reader's check</summary>

session.py:2620-2628 passes `prescan=rf.prescan, prescan_meta=rf.prescan_meta` and `raw_image=rf.raw_image`, so the frame gets its raw pixels and the prescan does not. rf.prescan is `prescan_image, _ = self.prescan(... shading=shading ...)` (direct.py:3968-3975), which is the corrected return, while `raw_prescan = self.last_pixels_raw` is carried and never filed on a real roll. The prescan's raw bytes are gone as well, because the frame pass overwrites last_raw. library.save writes the prescan with `tiff.write(... "prescan.tif", prescan ...)` (269), and the record keeps only file, read_direction and carriage_state (376-382). No field labels its correction state. scan_roll.py:752-755 does the same. demo._taken_raw (demo.py:1473-1485) states the unrecorded convention in its own docstring. prescan_before is never a library entry on either path. A dry run writes it only as a corrected TIFF in the roll directory: session.py:2567-2587 with `file_entry=False`, and scan_roll.py:686-694 with plain `tiff.write`. On a real roll it is not written at all. With debug on, the prescan pass is debug-filed with its bytes, because only the frame's raw pixels are claimed (session.py:2872-2875).

</details>

<a id="library-lib-02"></a>

### LIB-02 -- A library.save failure loses the whole frame: in-memory raw bytes dropped and delivered copies never attempted

**Severity** high · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1632-1668`, `rps7200/session.py:1671-1694`, `rps7200/session.py:1587-1600`, `rps7200/session.py:2003-2015`, `rps7200/session.py:2872-2875`, `rps7200/direct.py:1055-1059`, `tools/scan.py:294-300`, `tools/scan.py:366-375`

**Doc claim:** rps7200/session.py:1632-1636 ("Written the other way round, an unplugged drive or a full output folder raised before `library.save` ran, and the scan's raw bytes were lost with it.")

Filing the entry first protects the raw bytes only when filing succeeds. When library.save raises, FrameWriter never writes the output-folder copy or rolls/frameNN.tif, so nothing of the picture survives: not the raw bytes (they were held only in memory), not the corrected copy, and not a retry. `_filed` then stops the roll. In tools/scan.py, one failing save aborts the loop, and every later bracket pass still in `pending` is lost with the process. With debug on, those passes were already claimed, so the debug spool skipped them.

**Evidence (from the code):**

```text
session.py:1656 `entry = library.save(` sits outside any try; the copies loop starts at 1673 `for path in job.get("paths") or ():`, after it. `_run` (1594-1598) catches the exception, records `self.errors.append(f"picture {job['number']}: {exc}")` and moves on, so the job dict holding `capture['raw']` is dropped. The comment at 1632-1636 claims the opposite ordering protects data: "The library entry first, the operator's copies after. A copy can be written again from the entry at any time". tools/scan.py:367-375 `for held in pending: entries.append(library.save(...))` has no per-pass guard.
```

**Failure scenario:** The library sits on an almost-full system drive and the output folder on an external disk with plenty of space. Frame 12 of a 3600 dpi roll hits ENOSPC while scan.tif is being written, or index.json is locked (LIB-03). The frame's raw bytes are discarded and its TIFF is never written to the external disk, even though that disk had room. A 9-pass bracket in tools/scan.py whose third save fails loses passes 3-9.

**Fix:** Catch the save failure inside `_write`, then still write the delivered copies, and spool the raw bytes and meta to a recoverable location, as `_debug_flush` does. In tools/scan.py, guard each save individually and keep going.

<details><summary>Second reader's check</summary>

In FrameWriter._write, `entry = library.save(...)` (session.py:1656) runs before the `for path in job.get("paths")` loop (1673), with no try around it. `_run` (1594-1598) catches the exception, appends an error and drops the job, which held the raw bytes in `capture`. `_filed` then calls request_stop for a Roll (2003-2015). The protection is also weaker than it looks with debug on. `_file` calls `claim(raw_image)` at submit time (session.py:2872-2875), before the save is known to succeed, and `_debug_flush` unlinks claimed spool files without filing them (direct.py:1055-1059). So even with RPS7200_DEBUG=1 the spooled copy is deleted. In tools/scan.py:366-375, `for held in pending: entries.append(library.save(...))` has no per-pass guard, and the scanner has already been closed and flushed with those passes claimed (hold() calls debug_claim, scan.py:294-296).

</details>

<a id="library-lib-03"></a>

### LIB-03 -- `reconstruct` counts a decode that now raises as 'nothing to decode from' and exits 0

**Severity** high · **Category** bug · **Verdict** confirmed

**Where:** `tools/library.py:145-172`, `rps7200/library.py:701-720`, `rps7200/library.py:746-749`

**Doc claim:** CLAUDE.md 'Scans': "After any change to how bytes become pixels, run `tools/library.py reconstruct`: it re-decodes every stored pass with current code and reports what no longer matches."

`make reconstruct` is the one gate CLAUDE.md names for any change to how bytes become pixels. Stored bytes that decoded yesterday and now make decode_index raise ('expected 4 channels ... produced 3', a ValueError from a reshaped path) are a decode regression. The tool files them under 'unreadable', says it is not a regression, and exits 0. The same happens to a truncated or corrupt scan.tif ('could not read scan.tif') and an unparsable scan.json.

**Evidence (from the code):**

```text
tools/library.py:151-153 `elif ("no raw bytes" in verdict or verdict.startswith("could not")
      or "cannot be reproduced" in verdict):
    mark, unreadable = "-", unreadable + 1`, then 164-168 prints "every entry that can be decoded still decodes to exactly what was stored" ... "had nothing to decode from -- not a regression" and `return 1 if changed else 0`. library.py:719-720 `except (KeyError, ValueError, TypeError, ScanReadError) as exc: return None, f"could not decode: {exc}"`; 748-749 `return image, f"could not read scan.tif: {exc}"`.
```

**Failure scenario:** A change to CHANNEL_ORDER or to the INDEX tag handling makes decode_index raise ScanReadError for every RGBI pass. `make reconstruct` prints '-' next to each of them, then 'every entry that can be decoded still decodes to exactly what was stored', and exits 0. The regression lands on main.

**Fix:** Make the reconstruct verdict structured (a status enum rather than a string). Count 'could not decode' and 'could not read scan.tif/scan.json' as failures with a non-zero exit, and keep only 'no raw bytes stored' as benign.

<details><summary>Second reader's check</summary>

tools/library.py:151-153 counts any verdict that starts with 'could not' as unreadable. reconstruct returns `could not decode: {exc}` for KeyError, ValueError, TypeError and ScanReadError (library.py:719-720), and `could not read scan.tif` (748-749) and `could not read scan.json` (700) for the other failures. The summary (164-168) then says 'every entry that can be decoded still decodes to exactly what was stored' and 'had nothing to decode from -- not a regression', and `return 1 if changed else 0` exits 0. A decode_index change that raises ScanReadError for every RGBI pass therefore passes the one named regression gate. Each line does print a '-' with the error, but the exit code and the summary are both wrong.

</details>

<a id="library-lib-04"></a>

### LIB-04 -- A failure to rewrite the derived index.json makes a complete save() raise

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:395-400`, `rps7200/library.py:1045-1065`, `rps7200/direct.py:1060-1080`

**Doc claim:** rps7200/library.py:29-30 ("`index.json` is a derived summary for finding things and can be rebuilt from the entries at any time.")

The entry is complete once scan.json is renamed into place. After that, save() rebuilds index.json by parsing every scan.json in the library and writing it with a plain write_text. If that write fails (disk full, a Windows AV/OneDrive/indexer lock, permissions), save() raises. Every caller then treats a successfully filed entry as a failure. FrameWriter skips the delivered copies and stops the roll (LIB-02). `_debug_flush` counts it as failed and keeps the spool, so a later manual re-file duplicates the entry. The FrameWriter thread and the debug flush (which runs in `close()` while the writer is still draining) can also write index.json at the same moment and tear it.

**Evidence (from the code):**

```text
library.py:397-399 `_write_atomic(path / "scan.json", ...)
(path / INCOMPLETE).unlink(missing_ok=True)
reindex(root)`; reindex ends `index.write_text(json.dumps(summary, indent=2), encoding="utf-8")` (1064), a plain non-atomic write. Nothing in the code reads index.json (grep finds no reader), and library.py:1046 describes it as "Derived: safe to delete."
```

**Failure scenario:** The library lives in a OneDrive-synced folder on Windows. The sync client holds index.json for a moment while frame 7 is filed. write_text raises PermissionError, and the roll reports 'picture 7 could not be filed' and stops, although library/<id>/ is complete.

**Fix:** Rebuild the index best-effort (catch, then log) or lazily, and write it with `_write_atomic`. Never let it decide whether save() succeeded.

<details><summary>Second reader's check</summary>

save() calls `reindex(root)` after the record rename and the INCOMPLETE unlink (library.py:397-399). reindex writes with a plain `index.write_text(...)` (1064), and no code reads index.json (grep finds none). A failed write raises out of save(). FrameWriter then skips the copies and stops the roll, and _debug_flush sets `failed += 1` and keeps the spool, so re-filing it by hand duplicates the entry. A concurrent reindex is reachable. The session's finally block calls `self._scanner.close()`, which runs _debug_flush and so library.save, before `self._writer.finish()`, while the writer thread may still be filing (session.py:1972-1979). _reserve prevents directory collisions but not a torn index.json.

</details>

<a id="library-lib-05"></a>

### LIB-05 -- The calibration's raw bytes are not in the entry, and the link to them is CWD-relative, unverified, and missing for loaded references and for tools/uniformity.py

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:28-30`, `rps7200/library.py:271-272`, `rps7200/direct.py:720-729`, `rps7200/direct.py:749-804`, `rps7200/direct.py:854-860`, `rps7200/direct.py:3208-3211`, `tools/uniformity.py:726-744`, `rps7200/library.py:1068-1140`

**Doc claim:** rps7200/library.py:28-30 "Entries are self-contained directories: nothing refers out"; CLAUDE.md "Each calibration's own bytes are kept too ... in `calibration/<UTC time>/`" and "Re-run with `tools/uniformity.py analyse --tag vignette-study` after any correction change: it rebuilds from stored raw bytes, so the answer tracks the current pipeline."

shading.npz is the output of calculate_shading's level split and averaging. A better reduction cannot be applied without the calibration lines, and those never go into the entry. For the common 'reuse the cached reference' path the entry cannot even say which archive produced it. The vignette study's calibrations are discarded outright. The archive path is relative to the process CWD and outside the library, so copying an entry, or running from another directory, breaks the link, and `make verify` never checks the archive's own sha256.

**Evidence (from the code):**

```text
The entry stores only the reduction: `reference.save(path / "shading.npz")` (library.py:272). The bytes are archived under `archive = self.archive_calibration(result, path.parent)`, with `self._shading_origin["archive"] = str(archive)` (direct.py:856-860), a relative 'calibration/<stamp>'. A reused cache gets `{"action": "loaded", "path": str(path), "file_modified_utc": ..., "loaded_utc": ...}` and no archive (724-729). The origin is recorded only `if shading and (origin := self._shading_origin)` (3209-3211). tools/uniformity.py:735 calls `scanner.calibrate_shading()` directly (keep_data=False, no archive) and then `load_shading` on every pass. verify() never looks at calibration/.
```

**Failure scenario:** calculate_shading gets a better dark/light split. The window's entries filed with 'Use the cached one', and every vignette-study entry, cannot be re-corrected from the calibration's own lines. CLAUDE.md's promise that the uniformity analysis 'tracks the current pipeline' does not hold for the reference half of the correction.

**Fix:** Copy (or hard-link) the calibration's data.bin and calibration.json into each entry, or give every reference an id and store it in a content-addressed calibration store inside the library. Carry that id through load_shading, and have verify check it.

<details><summary>Second reader's check</summary>

The entry stores only `reference.save(path / "shading.npz")` (library.py:271-272). archive_calibration runs only from ensure_shading (direct.py:848-860), and its `str(archive)` is `Path(job.reference).parent/<stamp>`, which is relative to the CWD with the default 'calibration/shading.npz' (session.py:1367). load_shading records `{"action": "loaded", "path": ...}` with no archive and no hash (direct.py:724-729). That path is the cache, which every calibration overwrites, so after the next calibration the link names a file that no longer holds this reference. The origin is recorded only when the pass was shaded (3209-3211). tools/uniformity.py:735 calls `calibrate_shading()` without keep_data, so nothing is archived, and it saves the reference with a plain `reference.save` (743). verify() never looks at calibration/. Nothing in the code reads data.bin or calibration.json. The library.py:28 claim that 'nothing refers out' is true only in that the entry can be corrected without the archive; re-reducing the calibration needs it, and it lives outside the library.

</details>

<a id="library-lib-06"></a>

### LIB-06 -- migrate-raw rewrites labelled entries without the laundering guard, and can leave the only corrected rendition where nothing reads it

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/library.py:241-257`, `tools/library.py:265-285`, `rps7200/library.py:732-744`

**Doc claim:** tools/library.py:212-216 ("Both are repaired the same way and without guessing ... a corrected image is never un-corrected") and 249-251 ("rewriting would launder it into the library for good").

For mislabelled entries, the tool refuses to launder a decode change into the library. For labelled entries it never checks that the stored pixels equal shade(decode). A labelled entry whose decode has regressed, or whose reference is missing, is rewritten to today's decode and relabelled raw, and reconstruct then reports it 'identical'. When the reference is missing, `corrected()` goes from 'already' (the operator saw corrected pixels) to 'no reference' (raw, striped). The corrected picture survives only as scan.before-migrate-raw.tif, which nothing checksums, verifies or reads. The record update comes after the two renames, so an interruption can leave no scan.tif, or leave raw pixels labelled ['shading']. A second run then overwrites KEPT with the raw decode.

**Evidence (from the code):**

```text
tools/library.py:245-257: `if np.array_equal(plain, stored) and not applied: continue` / `if not applied and not _one_shading_explains(path, plain, stored): failed.append(... "a decode change, not a mislabelled entry; left alone")`. The guard runs only when `not applied`, so every `applied == ["shading"]` entry goes to `planned`, including those whose reconstruct verdict was 'decode CHANGED' or 'reference is missing, so it cannot be reproduced'. Write step: `os.replace(path / "scan.tif", path / KEPT)` / `os.replace(fresh, path / "scan.tif")` / `image["corrections_applied"] = []`.
```

**Failure scenario:** The operator runs `tools/library.py migrate-raw --write` on a library with legacy labelled entries, one of which lost its shading.npz. That entry's Save As output silently becomes the striped raw decode, and the regression check can no longer see a decode change on any labelled entry.

**Fix:** Require `np.array_equal(apply_shading(plain, ref, mask), stored)` for labelled entries as well, and refuse entries with no reference. Checksum KEPT in `files` and write the record in the same crash-safe order as compact.

<details><summary>Second reader's check</summary>

For `applied == ["shading"]`, the guard at tools/library.py:247 is skipped, and every entry whose reconstruct returned a non-None image is planned. That includes the 'reference is missing, so it cannot be reproduced' verdict, which returns `image` (library.py:738-742), and any 'decode CHANGED' verdict. The write sequence is `os.replace(scan.tif -> KEPT)`, then `os.replace(fresh -> scan.tif)`, then the record (273-284). A crash between the two replaces leaves no scan.tif. A crash after the second leaves raw pixels labelled ['shading'] with a stale sha. On a rerun that entry is still 'applied' and is planned again, and `os.replace(scan.tif, KEPT)` overwrites the kept corrected file with the raw decode. KEPT is not in `files`, is not checksummed, and nothing reads it. When the reference is missing, corrected() goes from 'already' to 'no reference'.

</details>

<a id="library-lib-07"></a>

### LIB-07 -- tools/uniformity.py capture meters before any reference exists, so metering always raises ShadingUnavailable

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:699-711`, `tools/uniformity.py:726-744`, `rps7200/direct.py:2385-2398`, `rps7200/direct.py:2492-2505`, `rps7200/direct.py:2935-2963`

**Doc claim:** tools/uniformity.py:4-7 (module docstring gives `capture --tag vignette-study` as the phase-1 command)

Without --exposure-scale, the documented path for phase 1 ('Metering on the EMPTY transport') dies at the first probe with ShadingUnavailable, after the operator has been asked to empty the transport. Only --exposure-scale gets past it. tests/test_uniformity.py covers analyse only, never capture.

**Evidence (from the code):**

```text
tools/uniformity.py:704-708 `scanner = factory()
scanner.open()
exposure_scale = scanner.auto_exposure(
    target=args.target, infrared=args.ir, film="positive")`. This is a fresh DirectScanner with no load_shading, and the calibration follows later (731-743). auto_exposure defaults `shading: bool = True` (2397) and probes with `self.scan(... shading=shading, keep_raw=True)` (2492-2505). scan() raises `self.uncalibrated(reason)` when `self._shading is None` (2948-2963).
```

**Failure scenario:** `uv run python tools/uniformity.py capture --tag vignette-study` then Enter at 'press Enter with the transport empty' ends in a traceback: 'no shading reference in this session. Calibrate first ...'.

**Fix:** Calibrate (or load the reference) before metering, with film loaded (see LIB-08), or pass shading=False to the metering probe explicitly. Add a capture test with a fake scanner.

<details><summary>Second reader's check</summary>

cmd_capture builds a fresh DirectScanner (`_shading = None`, direct.py:578; open() loads nothing, 1129-1132) and calls `auto_exposure(target=..., infrared=..., film="positive")` with the default `shading=True` (2397). The probe calls `self.scan(... shading=shading ...)` (2492-2505), and scan() raises `self.uncalibrated(reason)` when `self._shading is None` (2948-2963). One set_gain_offset is sent before that. No test runs cmd_capture: tests/test_scan_tool.py:485-492 only inspects the source of one_pass.

</details>

<a id="library-lib-08"></a>

### LIB-08 -- tools/uniformity.py calibrates with the transport emptied or in an unprompted state

**Severity** medium · **Category** hardware-safety · **Verdict** confirmed

**Where:** `tools/uniformity.py:699-711`, `tools/uniformity.py:726-744`, `tools/uniformity.py:57-60`

**Doc claim:** CLAUDE.md 'Calibrate with the film loaded': "Calibrating an empty transport is a state the vendor never creates, and doing it once preceded a wedge."

CLAUDE.md says to calibrate with the film loaded, because calibrating an empty transport is a state the vendor never creates and doing it once preceded a wedge. The capture flow tells the operator to empty the transport and then, if metering worked (LIB-07), calibrates straight away. With --exposure-scale it calibrates with whatever happens to be loaded, just before the study's own empty-transport pass.

**Evidence (from the code):**

```text
tools/uniformity.py:700-703 `print("\nMetering on the EMPTY transport ...")` / `if confirm("    press Enter with the transport empty: ") == "abort":`, then with no further prompt 731-737 `print("\nCalibrating shading (3-4 minutes) ...") ... result = scanner.calibrate_shading()`. With --exposure-scale, calibration runs before any instruction about the transport; PHASE1's first step is 'Take everything out of the transport. Leave it empty.' (58-60).
```

**Failure scenario:** Once LIB-07 is fixed by adding a reference or making metering raw, the documented flow calibrates an empty transport for 3-4 minutes: the state recorded as preceding a wedge, which costs a power cycle and the locked-exposure session.

**Fix:** Calibrate first, after an explicit prompt to load the film or target (as CyberView does), and only then ask for the empty transport for metering and the first pass.

<details><summary>Second reader's check</summary>

tools/uniformity.py:700-703 asks for an empty transport for metering, and 731-737 then calibrates with no prompt in between. With --exposure-scale, which is today the only way past LIB-07, calibration runs before any transport instruction has been printed, so the transport is in whatever state it happens to be. CLAUDE.md records empty-transport calibration as preceding a wedge. The metering path to it is currently blocked by LIB-07, but the --exposure-scale path is live.

</details>

<a id="library-lib-09"></a>

### LIB-09 -- The uniformity study's IR phase can never run: the RGB calibration has no channel 3, and the refusal comes only after metering and a 3-4 minute calibration

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:746-751`, `tools/uniformity.py:682-689`, `tools/uniformity.py:9-12`, `rps7200/direct.py:2227-2233`

**Doc claim:** tools/uniformity.py:9-12 ("Phase 2 is a smaller follow-on that asks only whether the field *differs* in infrared ... capture --ir --tag vignette-study-ir"); CLAUDE.md: "the calibration pass is RGB, so there is no infrared reference to divide by."

`capture --ir` (phase 2) is refused on every run. The check sits after the canary prompt, the metering and the calibration, so it spends scanner time (and, per LIB-08, drives the device) before refusing. The prompt text is stale: with the default fast_infrared, IR is tied to the resolution (about 25 s at 300 dpi), not a 212 s floor.

**Evidence (from the code):**

```text
tools/uniformity.py:746 `if args.ir and 3 not in reference.ref:` then `print("REFUSING: the calibration produced no infrared reference ...")`, `return 1`. The calibration pass is `self.set_mode(resolution=resolution, passes=ONE_PASS_COLOR, depth=DEPTH_8, ...)` (direct.py:2227-2233), which is RGB, so calculate_shading never sees an 'I' tag. The prompt at 683-687 still says "IR passes hold the device busy for a ~212 s floor each, whatever the resolution" and advises a canary pass.
```

**Failure scenario:** The operator runs phase 2 as documented, answers the canary prompt, waits through a 3-4 minute calibration, and gets REFUSING. The same happens on every retry.

**Fix:** Decide what phase 2 should measure. The IR plane is delivered uncorrected everywhere, so either drop the refusal and analyse IR raw, or remove phase 2. At minimum, check before driving the device, and fix the stale timing text.

<details><summary>Second reader's check</summary>

The check `if args.ir and 3 not in reference.ref:` (746) comes after the canary prompt (683-689), the metering and the calibration. The calibration pass is `passes=ONE_PASS_COLOR` (direct.py:2227-2233), so calculate_shading only ever sees RGB tags, and the project's own statement is that the calibration pass is RGB. Phase 2 is therefore refused on every run, after scanner time has been spent. The prompt text still describes a ~212 s IR floor, while scan() defaults to fast_infrared=True. PHASE2 also says nothing in the transport changes, yet the metering step still asks for it to be emptied.

</details>

<a id="library-lib-10"></a>

### LIB-10 -- Uniformity passes rejected with 'redo' are still analysed, and as the oldest they become the base or the orientation used

**Severity** medium · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:639-647`, `tools/uniformity.py:161-171`, `tools/uniformity.py:248`, `tools/uniformity.py:454`, `tools/uniformity.py:474-476`

**Doc claim:** tools/uniformity.py:641-642 ("rejected at capture time; kept because how a pass went wrong is evidence about handling")

A pass the operator rejected at capture time keeps the study tag and the same subject label as its redo. `analyse` includes it, and because it is older it is chosen over the redo as the reference pass or as the pass for its orientation. If it was rejected for being inserted wrong, check_orientations refuses the whole study. If it was rejected for dust or a bad seat, its field goes silently into the decomposition. The redo path also rewrites scan.json non-atomically (not add_tags/_write_atomic) and never reindexes.

**Evidence (from the code):**

```text
The redo path (640-646) writes a REJECTED marker and `record["tags"] = list(record.get("tags") or []) + ["rejected"]`, then `(entry / "scan.json").write_text(json.dumps(record, indent=2), ...)`, keeping the study tag. `select()` keeps any entry with `if tag in (record.get("tags") or []):` and never excludes 'rejected'. It sorts oldest first, and decompose uses `base = by_orientation[AS_IS][0]` (454) and `it8[by_orientation[name][0]]` (476). check_orientations takes `origin = next((n for n in ids if claims[n] == AS_IS), None)` (248).
```

**Failure scenario:** The operator inserts the IT8 as-is badly, answers 'redo', and re-seats it. analyse picks the rejected pass as `base`, so every orientation difference and repeat floor is measured against the bad pass, and the vignette verdict is computed from it.

**Fix:** Have select() skip entries tagged 'rejected' or carrying REJECTED, and use library.add_tags for the redo tag.

<details><summary>Second reader's check</summary>

The redo branch (640-646) adds a 'rejected' tag and a REJECTED file and keeps args.tag. select() (161-171) keeps any entry carrying the tag and never checks for 'rejected'. It sorts oldest first (by directory name, which is the filing time), so the rejected pass comes before its redo. decompose uses `base = by_orientation[AS_IS][0]` (454) and `it8[by_orientation[name][0]]` (476). check_orientations takes the first as-is claim as origin (248), and a rejected, mis-inserted pass adds problems that refuse the whole study. The redo rewrites scan.json with a plain write_text, not _write_atomic, and never reindexes.

</details>

<a id="library-lib-11"></a>

### LIB-11 -- Nothing compacts left-over plain entries, and an interrupted compact or migrate-direction leaves checksum mismatches that no rerun repairs

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:414-453`, `rps7200/session.py:1989-1995`, `tools/library.py:74-77`, `rps7200/library.py:824-826`, `rps7200/library.py:831-836`

**Doc claim:** CLAUDE.md: "`library.compact` gzips each once the device has closed. An entry the window was killed before compacting stays plain, complete and verifiable."

Entries the window filed plain stay plain for good if the window dies, because no tool compacts them later. If the process dies inside compact() after a TIFF has been swapped but before the record is written, verify reports 'scan.tif does not match its checksum' on pixels that are fine, and no rerun is possible. A kill after the gz swap but before `plain.unlink()` leaves raw.bin and raw.bin.gz side by side. migrate_direction has the same shape: once scan.tif has been turned, a crash before the record is written leaves a stale checksum, and on the rerun the 'recorded' branch does not refresh it.

**Evidence (from the code):**

```text
compact() does `os.replace(temp, path / RAW_FILE)`, then `_replace_tiff(path / name, ...)` for each TIFF, and only then `_write_atomic(path / "scan.json", ...)` and `plain.unlink()`. The only caller is the session close loop `for entry in self._writer.uncompressed:`. The CLI choices are `["list", "verify", "reconstruct", "reindex", "duplicates", "migrate-raw", "migrate-direction", "tag"]`, with no compact. In migrate_direction, the branch `if plain and np.array_equal(stored, decoded): done.append(...); upright = decoded` never refreshes image.sha256.
```

**Failure scenario:** The window is closed while it compacts a 3600 dpi scan, and the process is killed. `make verify` then reports damage for ever on an intact entry, and the operator cannot tell it from real corruption.

**Fix:** Add `tools/library.py compact`, which finds entries with raw.bin, finishes partial compactions and refreshes checksums. In migrate_direction, update image.sha256 whenever it differs from the file.

<details><summary>Second reader's check</summary>

compact() swaps the gzip in, then each TIFF, then writes the record, then unlinks raw.bin (library.py:434-453). The only caller is the session close loop (session.py:1989-1995), and the CLI has no compact action (tools/library.py:74-77). A kill after a TIFF swap and before the record is written leaves image.sha256 stale, so verify reports a checksum mismatch on intact pixels. In migrate_direction, a crash after `_replace_tiff` and before `_write_atomic` means the rerun takes the `plain and np.array_equal(stored, decoded)` branch (824-826), which records the entry without refreshing image.sha256.

</details>

<a id="library-lib-12"></a>

### LIB-12 -- reconstruct compares values and shape but not dtype

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/library.py:753-763`

A decode change that alters only the sample type (8-bit prescans coming back as uint16 with the same values) is reported 'identical'. apply_shading derives `depth_scale` from the dtype, so every corrected view of those entries would change: a uint16 image holding 0-255 has the ~170-count dark floor subtracted and goes almost black.

**Evidence (from the code):**

```text
`if image.shape != stored.shape: ...` then `if np.array_equal(image, stored): return image, "identical to the stored image"`. np.array_equal ignores dtype.
```

**Failure scenario:** A refactor of decode_index's `depth_bytes` logic returns uint16 for 8-bit passes. `make reconstruct` stays green while every prescan's corrected view goes dark.

**Fix:** Also require `image.dtype == stored.dtype`, and report a dtype change as its own verdict.

<details><summary>Second reader's check</summary>

The comparison at library.py:753-763 is a shape check followed by `np.array_equal(image, stored)`, and array_equal compares values regardless of dtype. apply_shading derives depth_scale from `np.iinfo(image.dtype).max` (shading.py:241-243), so a dtype-only change would move every corrected view while reconstruct still says 'identical'.

</details>

<a id="library-lib-13"></a>

### LIB-13 -- reconstruct misreports raw-byte corruption: corrupt gzip reads as 'no raw bytes stored', corrupt plain raw.bin as a decode change

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:604-624`, `rps7200/library.py:696-699`, `rps7200/library.py:770-774`

A truncated or corrupt raw.bin.gz is reported as 'no raw bytes stored', and tools/library.py counts it as benign. A bit-flipped plain raw.bin (window entries not yet compacted) decodes and is reported as 'decode CHANGED: N samples differ', which blames the decoder for storage damage. Only verify tells the two apart.

**Evidence (from the code):**

```text
read_raw: `except (OSError, EOFError, gzip.BadGzipFile): return None`. reconstruct: `raw = read_raw(path)
if raw is None:
    return None, "no raw bytes stored for this entry"`. raw.sha256 is never checked before decoding.
```

**Failure scenario:** Disk corruption in one plain raw.bin makes `make reconstruct` report a decode regression. Someone then 'fixes' the decoder to match corrupted bytes.

**Fix:** Verify raw.sha256 in reconstruct before decoding, and report checksum failures as their own verdict.

<details><summary>Second reader's check</summary>

read_raw returns None on `(OSError, EOFError, gzip.BadGzipFile)` (library.py:604-624), and reconstruct maps None to 'no raw bytes stored for this entry' (696-699), which the CLI counts as benign. reconstruct never checks raw.sha256, so a corrupted plain raw.bin decodes and is reported as 'decode CHANGED'. Only verify compares the raw checksum.

</details>

<a id="library-lib-14"></a>

### LIB-14 -- verify says nothing when a file listed in `files` (prescan.tif) is missing

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/library.py:1104-1106`, `rps7200/library.py:376-382`

**Doc claim:** CLAUDE.md: "`make verify` # check the library's checksums and completeness"; "The record carries a checksum for every file"

A deleted or lost prescan.tif passes `make verify` as intact, although the record names it and carries its checksum.

**Evidence (from the code):**

```text
`for name, digest in (record.get("files") or {}).items():
    if (path / name).exists() and _sha256(path / name) != digest:` A missing file is skipped. Only shading/ccd_mask get an existence check (1100-1103), and `record["prescan"]["file"]` is never checked.
```

**Failure scenario:** The operator deletes prescan.tif to save space, or a sync tool drops it. verify still says 'library is intact'.

**Fix:** Report a missing file in `files`, and one named by record.prescan, as a problem.

<details><summary>Second reader's check</summary>

`if (path / name).exists() and _sha256(path / name) != digest:` (library.py:1104-1106) skips missing files. Only calibration.shading and calibration.ccd_mask get existence checks (1099-1103), and record.prescan.file is never checked.

</details>

<a id="library-lib-15"></a>

### LIB-15 -- save() writes the irreplaceable raw bytes last, in place, and without fsync

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:259-305`, `rps7200/library.py:484-491`

**Doc claim:** rps7200/library.py:21 ("`raw.bin.gz` is the ground truth and everything else is derived from it.")

Every derived file is written before the raw bytes, so an exception while writing one of them (tiff.write refusing an empty or odd-dtype image, a failing reference.save, a full disk mid scan.tif) aborts the save before the ground-truth bytes are attempted. On power loss the fsynced scan.json and the removed INCOMPLETE marker can persist while delayed-allocation data blocks of raw.bin.gz or scan.tif do not. verify catches that with checksums, but the marker no longer does.

**Evidence (from the code):**

```text
The order is `tiff.write(... "scan.tif" ...)`, `tiff.write(... "prescan.tif" ...)`, `reference.save(...)`, `(path / "ccd_mask.bin").write_bytes(...)`, and only then the raw block (277-304). `_write_atomic` fsyncs only scan.json; the data files and the directory are never fsynced.
```

**Failure scenario:** A pass that ended with 0-4 usable rows makes tiff.write raise 'cannot write an empty image'. The partial raw bytes, the only evidence of what went wrong, are never written, and FrameWriter then drops them (LIB-02).

**Fix:** Write (and fsync) the raw bytes first, then the derived files. fsync the data files and the directory before the record, and before unlinking INCOMPLETE.

<details><summary>Second reader's check</summary>

save() writes scan.tif, prescan.tif, shading.npz and ccd_mask.bin before the raw block (library.py:268-304). tiff.write raises on empty, odd-dtype or non-2D/3D images (tiff.py:125-131), and any such raise aborts the save before the raw bytes are written. Only _write_atomic fsyncs, and only scan.json. Neither the data files nor the directory are fsynced before INCOMPLETE is unlinked. How reachable an empty-image pass is depends on scan() guards upstream, so this is low.

</details>

<a id="library-lib-16"></a>

### LIB-16 -- decode_raw lets ScanReadError escape, and migrate-direction aborts mid-run on it

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/library.py:657-684`, `tools/library.py:300-307`, `rps7200/protocol.py:341`

**Doc claim:** rps7200/library.py:663-664 ("None when there are no bytes or the layout cannot drive a decode.")

The docstring says decode_raw returns None when a decode is impossible, but a pass with unrecognisable or wrong-count tags raises instead. `migrate-direction --write` stops at the first such entry, after rewriting the ones before it, and the reindex never runs.

**Evidence (from the code):**

```text
decode_raw: `except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError): return None`. ScanReadError subclasses RuntimeError (protocol.py:341) and is raised by decode_index for missing or extra tags. tools/library.py migrate-direction catches only `except (OSError, ValueError, KeyError) as exc:`.
```

**Failure scenario:** One legacy entry with a 3-tag RGBI blob makes `migrate-direction --write` end in a traceback part-way through the library.

**Fix:** Catch ScanReadError in decode_raw and in the CLI loop.

<details><summary>Second reader's check</summary>

ScanReadError subclasses RuntimeError (protocol.py:341). decode_index raises it for missing or wrong-count tags (direct.py:1952-1965). decode_raw catches `(OSError, KeyError, ValueError, TypeError, json.JSONDecodeError)` only (library.py:683). migrate_direction calls decode_index directly (library.py:817-818), and the CLI catches only `(OSError, ValueError, KeyError)` (tools/library.py:304), so one bad entry aborts the run before reindex. In migrate-raw the reconstruct call screens such entries first, so the impact there is small.

</details>

<a id="library-lib-17"></a>

### LIB-17 -- Debug filing ignores the caller's library root, and tools/uniformity.py never claims its passes (double filing with debug on)

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/direct.py:1049`, `tools/uniformity.py:586-621`, `rps7200/direct.py:1000-1016`

**Doc claim:** CLAUDE.md: "with it on, debug filing adds only the passes they do not keep ... and files nothing twice."

With RPS7200_DEBUG=1, which CLAUDE.md requires of Claude, a tool run with `--library X` or the window's own root sends its unclaimed passes to ./library instead of X, which splits one session across two libraries. It also creates a new library/ wherever the process was started. tools/uniformity.py does not claim its passes, so each one is filed twice (a 'debug' entry with no film notes, plus the study entry), and the pair is invisible to `duplicates` because the film notes differ.

**Evidence (from the code):**

```text
direct.py:1049 `root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` (a CWD-relative Path("library")). tools/uniformity.py one_pass files `library.save(... root=args.library ...)` after `scanner.close()` and never calls `scanner.debug_claim(raw_pixels)`.
```

**Failure scenario:** Claude runs the vignette capture with debug on: nine passes produce eighteen entries. A roll run with `--library /mnt/big/library` leaves its metering probes and hold prescans in ./library in the repository.

**Fix:** Give the scanner the filing root used by the caller, and call debug_claim in tools/uniformity.py.

<details><summary>Second reader's check</summary>

_debug_flush uses `root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` (direct.py:1049), which is CWD-relative Path('library'), whatever --library or session.root says. Nothing sets RPS7200_DEBUG_ROOT from a caller's root (grep finds it only in direct.py). tools/uniformity.py one_pass closes the scanner, which flushes an unclaimed spool item, and then files the same pass with library.save (586-621). It never calls debug_claim, so every pass is filed twice, and the two entries' film notes differ, which keeps them out of the same signature group.

</details>

<a id="library-lib-18"></a>

### LIB-18 -- corrected() applies a reference with no mask one-to-one even when the reference is wider than the pass, and labels it 'applied'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/library.py:595-599`, `rps7200/shading.py:236-237`, `rps7200/library.py:1099-1103`

An entry with shading.npz but no ccd_mask.bin (a legacy entry, or one whose record never named a mask) at a resolution below the reference's 5172 columns is corrected with reference columns 0..w-1 instead of the columns the pass sampled. The falloff and column pattern applied are wrong. The GUI, Save As and make_comparison all report it as 'applied', and verify stays quiet.

**Evidence (from the code):**

```text
`image, report = apply_shading(image, record["reference"], record["ccd_mask"])` then `record["corrected"] = "applied"`. In apply_shading: `if ccd_mask is None: loc = np.arange(min(w, reference.pixels_per_line), dtype=np.intp)`. verify flags a missing mask only when the record names one.
```

**Failure scenario:** A 1800 dpi legacy entry without a mask is exported, and its left half is divided by the reference of the CCD's left quarter, which puts in banding and a colour ramp while the record says the correction was applied.

**Fix:** In corrected(), refuse (or report 'no mask') when the mask is None and reference.pixels_per_line != image width. Have verify flag a reference without a mask.

<details><summary>Second reader's check</summary>

corrected() calls `apply_shading(image, record["reference"], record["ccd_mask"])` and sets 'applied' unconditionally (library.py:595-599). With no mask, apply_shading uses `loc = np.arange(min(w, reference.pixels_per_line))` (shading.py:236-237), which maps columns one to one. verify flags a missing mask only when the record names one. Reachable only for legacy entries with a reference and no mask, so low.

</details>

<a id="library-lib-19"></a>

### LIB-19 -- DemoScanner has no debug filing, and verify has a demo carve-out: the second library writer is never exercised by --demo

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:196`, `rps7200/demo.py:342-344`, `rps7200/session.py:2872-2875`, `rps7200/session.py:1972-1979`, `rps7200/library.py:1116-1121`

**Doc claim:** CLAUDE.md 'The demo is the real software with different inputs': "`--demo` may change what the software is fed. It may not change what the software does."

Under --demo with RPS7200_DEBUG=1, nothing is debug-filed: no metering-probe or hold/aim-prescan entries, no claim logic, and no flush at close() running beside the FrameWriter drain (the concurrent reindex in LIB-04). A path that files real data is therefore untested with no device on the bus. verify also branches on the demo flag, so a demo pass that lost its calibration plumbing can never be reported.

**Evidence (from the code):**

```text
`class DemoScanner:` does not subclass DirectScanner and has no `_debug_capture`, `_debug_flush` or `debug_claim`; its `close()` is `self.t.close(); self._decoded = None`. The session gates on `claim = getattr(self._scanner, "debug_claim", None)`. verify has `demo = bool((record.get("extra") or {}).get("demo"))` and skips the no-reference problem when it is set.
```

**Failure scenario:** A regression in debug_claim that files every window scan twice, or loses hold prescans, never shows in `make run-demo`, which is the only exercise available without the scanner.

**Fix:** Take the debug capture/flush/claim machinery from DirectScanner, for example as a mixin, so the stand-in files what the real one files. Scope the verify exemption to entries whose `calibration.skipped` names UNCALIBRATED_SOURCE rather than to every demo entry.

<details><summary>Second reader's check</summary>

DemoScanner is a standalone class (demo.py:196) with no _debug_capture, _debug_flush or debug_claim. Its close() only closes the transport and clears _decoded (342-344). The session gates on `getattr(self._scanner, "debug_claim", None)` (session.py:2872). verify's `demo = bool((record.get("extra") or {}).get("demo"))` exemption (library.py:1116-1121) is scoped to every demo entry, not to entries that are uncalibrated by design. The debug machinery lives in the scanner class, below the seam, so this is untested code rather than an `if demo` in the scan path. Low.

</details>

<a id="library-lib-20"></a>

### LIB-20 -- `analyse` always prints 'pipeline: unknown' (reads a provenance key that does not exist)

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/uniformity.py:377`, `rps7200/library.py:101-115`

The header that says which code produced the study's answer is always 'unknown'. The --out JSON does embed the full provenance.

**Evidence (from the code):**

```text
`print(f"pipeline: {library.provenance().get('commit', 'unknown')}")`, while provenance() returns keys `driver_commit`, `driver_commit_full`, `driver_dirty`, ... and has no 'commit'.
```

**Failure scenario:** Two analyse runs from different commits both print 'pipeline: unknown', and the console record cannot say which pipeline gave which verdict.

**Fix:** Use `driver_commit` (plus dirty state).

<details><summary>Second reader's check</summary>

`library.provenance().get('commit', 'unknown')` (tools/uniformity.py:377): provenance() returns driver_commit, driver_commit_full and the other driver_* keys, and has no 'commit' (library.py:101-115).

</details>

<a id="library-lib-a1"></a>

### LIB-A1 -- A corrupt shading.npz aborts reconstruct, migrate-raw and every export of the entry with an uncaught BadZipFile

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:731-744`, `rps7200/library.py:533-541`, `rps7200/shading.py:94-106`, `tools/library.py:60-63`, `tools/library.py:146-148`

**Doc claim:** rps7200/library.py:717-718 (comment: "bytes that are not a pass at all are a verdict about this entry, not a reason to stop checking every entry after it")

np.load on a truncated or corrupt .npz raises zipfile.BadZipFile (or EOFError), and none of these callers catch it. The 'bytes that are not a pass are a verdict about this entry, not a reason to stop checking every entry after it' rule that reconstruct applies to ScanReadError does not cover the reference. One damaged reference on a legacy labelled entry stops `make reconstruct` with a traceback partway through. The same damage makes load()/corrected() raise for that entry in the GUI and make_comparison.

**Evidence (from the code):**

```text
reconstruct legacy branch: `reference = ShadingReference.load(path / ref_file)` with no try; load(): `ShadingReference.load(path / ref_file) if ref_file and (path / ref_file).exists() else None`; ShadingReference.load is `with np.load(path) as z:`. _one_shading_explains catches only `(OSError, ValueError, KeyError)`; the reconstruct CLI loop has no try at all.
```

**Failure scenario:** A sync tool truncates one legacy entry's shading.npz. `tools/library.py reconstruct` dies with BadZipFile at that entry, and none of the entries after it are checked.

**Fix:** Catch reference-load failures in reconstruct, load and _one_shading_explains, and report them as a per-entry verdict. verify already catches the checksum mismatch.

<a id="library-lib-a2"></a>

### LIB-A2 -- A refused command is recorded only by its exception class name

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:483-488`

**Doc claim:** CLAUDE.md 'Scans': "The record carries ... every command the pass sent with what came back (`extra.commands`)"

extra.commands is described as every command with what came back, but for a command that failed the entry keeps only e.g. 'ScanReadError' or 'USBError'. The message is dropped: sense data, bytes received before failure, LIBUSB error code. A pass that is later analysed for why it failed or was cut short cannot say what the device answered.

**Evidence (from the code):**

```text
`except Exception as exc:
    entry["refused"] = type(exc).__name__
    self.record.append(entry)
    raise`
```

**Failure scenario:** A READ fails with 'bulk read of 16384 bytes failed after 0 bytes: LIBUSB_ERROR_PIPE'. The filed record (debug spool, or a partially successful pass) says only 'refused': 'OSError', and the detail that told the known stall apart from others is gone.

**Fix:** Record `f"{type(exc).__name__}: {exc}"` (and any sense bytes the exception carries).

<a id="library-lib-21"></a>

### LIB-21 -- No regression check covers the correction step: reconstruct tests only the decode

**Severity** info · **Category** test-gap · **Verdict** confirmed

**Where:** `rps7200/library.py:687-774`, `rps7200/library.py:360-375`

Every export is recomputed with today's apply_shading and ShadingReference. A change there shifts every Save As, Export and comparison in the library with no check at all. The stored scan-time report is a free, weak baseline that nothing reads.

**Evidence (from the code):**

```text
reconstruct compares the decode against scan.tif. Each entry stores `"report": meta.get("shading")` (the scan-time apply_shading report: columns, clipped, clipped_per_channel, two_point), but nothing compares `corrected(entry)` today against it.
```

**Failure scenario:** A change to the rounding or the depth scaling in apply_shading alters every corrected export in the library, and `make reconstruct` and `make verify` both stay green.

**Fix:** Add a `recorrect` check that re-runs corrected() and compares its report (clipped counts, columns) with calibration.report, and optionally a stored hash of the corrected pixels.

<details><summary>Second reader's check</summary>

reconstruct only re-decodes and compares against scan.tif. calibration.report (library.py:360) is stored and nothing reads it for comparison. No command re-runs corrected() against a baseline.

</details>

<a id="library-lib-22"></a>

### LIB-22 -- The record is serialised with `default=str`: non-JSON values in meta are silently stringified

**Severity** info · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:397`, `rps7200/library.py:337`, `rps7200/library.py:202-213`

Any numpy integer, bytes or ndarray that reaches meta (registration/hold histories, a future field) is written as its str(): an ndarray over 1000 elements becomes a repr with '...' and bytes become "b'...'". Nothing fails, and the value's type (for an ndarray, its content) is gone. The device record keeps only the parsed Inquiry fields, not the INQUIRY bytes.

**Evidence (from the code):**

```text
`_write_atomic(path / "scan.json", json.dumps(record, indent=2, default=str))`, where `"extra": {k: v for k, v in meta.items() if k not in _RECORDED}` passes through any caller-added value unchecked.
```

**Failure scenario:** A later change stores a per-column profile array in registration. Every entry then records '[0.1 0.2 ... 0.9]' and the profile cannot be recovered.

**Fix:** Serialise with a strict encoder that converts numpy scalars and arrays losslessly (tolist) or raises, and keep the raw INQUIRY bytes as hex.

<details><summary>Second reader's check</summary>

The record is written with `json.dumps(record, indent=2, default=str)` (library.py:397), and `extra` passes any non-recorded meta through (337). numpy integer scalars, arrays and bytes would be stringified silently. The device record comes from the dataclass fields of Inquiry (protocol.py:479-492), and the INQUIRY bytes are not kept. Commands are already hex (direct.py:480-491), so today's known fields are safe. Info.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Decoded pass pixels | library/<YYYYMMDDTHHMMSSZ>_<stock-slug\|unknown-film>[_f<frame-slug>]_<dpi>dpi[_ir][-N]/scan.tif | TIFF, uint8 or uint16, (H,W,C) C=3 RGB or 4 (IR as an 'unspecified' extra sample); deflate+predictor when tifffile is present and compress=True, otherwise uncompressed | Raw decode (decode_index turns bottom-up passes upright; 7200 dpi has the 4-line stagger trim, recorded as scan.stagger_realigned). Exceptions: legacy entries with corrections_applied ['shading'], and the FrameWriter fallback when no raw pixels came with a pass (labelled ['shading']) | library.save (FrameWriter in session.py:1656, DirectScanner._debug_flush direct.py:1064, tools/scan.py:368, tools/uniformity.py:609); rewritten by library.compact, library.migrate_direction, tools/library.py migrate-raw | library.load/corrected/reconstruct/migrate_*, demo._decode fallback and picture_signature, GUI _load_full and export, make_comparison, dpi_analysis, linearity, registration_margin | Pixel-lossless. Checksum image.sha256 over the file bytes, which changes on compaction. dtype is not compared by reconstruct |
| Raw scanner bytes | library/<id>/raw.bin.gz (compress=True) or library/<id>/raw.bin (window single scans/prescans until compact) | Concatenated INDEX-format lines exactly as returned by READ: 2-byte tag header + bytes_per_line per line, little-endian samples; gzip level 6. Layout in scan.json raw.layout {format, bytes_per_line, line_stride, index_header, width, lines, channels, byte_order, lines_received} | Raw, byte for byte | library.save from capture_record()['raw'] (bytes) or a debug spool raw_path (streamed); library.compact gzips raw.bin to raw.bin.gz | library.read_raw / decode_raw / reconstruct / migrate_direction / verify, demo._decode (via decode_raw), tools/uniformity.py rebuild/raw_image, exposure_headroom | Exact: raw.sha256 over the uncompressed content, raw.bytes the length. Absent when the pass did not keep its bytes or the session's shape guard dropped them. Never present for a frame entry's prescan.tif |
| Shading reference | library/<id>/shading.npz | np.savez_compressed: pixels_per_line, channels, dark_channels, ref<c> float64[ppl], mean<c> float64, dark<c> float64[ppl], darkmean<c> float64 | Derived: the averaged dark/light reduction of the calibration lines (calculate_shading), not the lines themselves | library.save (ShadingReference.save) from the session's reference at filing time | library.load/corrected/reconstruct, migrate-raw _one_shading_explains, demo._decode, tools/uniformity.py rebuild | Exact for the reduction; files.shading.npz sha256. The calibration bytes are not in the entry (see calibration archive) |
| CCD mask for this pass | library/<id>/ccd_mask.bin | Raw bytes from SCSI COPY, length = reference pixels_per_line or 5172; 0x00 = used column | Raw | library.save from capture_record()['ccd_mask'] (DirectScanner._read_pass sets it per pass) | library.load/corrected/reconstruct, demo._decode, tools/uniformity.py rebuild | Exact; files.ccd_mask.bin sha256. Only the frame pass's mask; a frame entry's prescan mask is not stored |
| Framing prescan beside a frame | library/<id>/prescan.tif | TIFF uint8 (H,W,3), 300 dpi typically, no resolution tag | CORRECTED on real-roll frame entries (rf.prescan / frame.prescan), not labelled as such; raw only when the roll ran with shading=False | library.save(prescan=...) from FrameWriter (session roll path) and tools/scan_roll.py; rewritten by compact and migrate_direction (turned upright) | demo picture_signature/best_pair, GUI, migrate_direction | Pixel-lossless for what was given; files.prescan.tif sha256 (verify ignores a missing file). Not re-derivable: no raw bytes, no own mask, meta limited to read_direction/carriage_state |
| Entry record | library/<id>/scan.json | JSON (indent 2, default=str): id, created (s), image{file,shape,dtype,channels,corrections_applied,sha256[,replaced]}, raw{file,bytes,sha256,layout}, scan{SCAN_FIELDS: resolution_dpi, frame, width, height, depth, channels, channel_order, bytes_per_line, film, exposure_scale, exposure_metered, duration_s, protocol_revision, rotation, flipped, reversal, read_direction, carriage_state, stagger_realigned, fast_infrared, filter_offsets}, extra{all other meta: commands{sent[{t,cdb,out,in,refused}],image_reads}, mode, started_utc, shading_origin, roll_membership, roll_index, roll_position, bracket_*, demo, demo_source, uniformity_session, ...}, device (parsed Inquiry), device_settings{exposure,gain,offset}, metering, registration, calibration{shading, ccd_mask, pixels_per_line, light_mean (rounded 0.1), report, skipped}, prescan{file, read_direction, carriage_state}, film, tags, provenance, files{sha256 of shading.npz, ccd_mask.bin, prescan.tif} | Metadata | library.save (_write_atomic + fsync); rewritten by compact, add_tags, migrate_direction (atomic), migrate-raw (atomic), tools/uniformity.py redo (NON-atomic write_text) | Everything: entries(), load, verify, reconstruct, duplicates/prunable, demo, GUI roll join (gui.py:5497), tools/uniformity.py select/rebuild | Mostly exact. default=str stringifies non-JSON types silently; the record itself has no checksum |
| Write-in-progress marker | library/<id>/INCOMPLETE | Text | n/a | library.save, first; removed after scan.json | library.verify | Directories without scan.json are invisible to entries()/demo/reconstruct; one with scan.json but a stale marker is listed and flagged |
| Derived index | library/index.json | JSON list of {id, created, dpi, channels, film, frame, tags, corrected, notes} | Derived | library.reindex after every save/tag/delete/migrate (non-atomic write_text) | Nothing in code | Derived; can be torn by concurrent writers, and its failure makes save() raise (LIB-04) |
| migrate-raw backup | library/<id>/scan.before-migrate-raw.tif | TIFF | Corrected (the legacy stored pixels) | tools/library.py migrate-raw --write (os.replace of the old scan.tif) | Nothing (recorded as image.replaced) | Not checksummed; overwritten by a rerun |
| Rejected uniformity pass marker | library/<id>/REJECTED (+ tag 'rejected') | Text | n/a | tools/uniformity.py one_pass redo | Nothing (analyse still includes the entry) | n/a |
| Temp files from atomic rewrites | library/<id>/.scan.json.part, .scan.tif.part, .prescan.tif.part, .raw.bin.gz.part | Partial copies | as target | _write_atomic, _replace_tiff, compact, migrate-raw | Nothing; left behind on crash, and verify ignores them | n/a |
| Calibration archive | calibration/<YYYYMMDDTHHMMSSZ>[-N]/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | data.bin: every calibration line as read (16-bit, 2-byte tag); calibration.json: resolution, pixels_per_line, bytes_per_line, bytes, sha256, commands, protocol_revision | Raw calibration bytes + derived reference | DirectScanner.archive_calibration via ensure_shading only (not tools/uniformity.py, which calls calibrate_shading directly) | Nothing in code; linked from entries as CWD-relative extra.shading_origin.archive only for 'calibrated' origins | Exact but outside the library, unverified, and not linked from 'loaded' references |
| Cached session reference | calibration/shading.npz (window/tools), calibration/shading_uniformity.npz (uniformity) | ShadingReference npz | Derived | DirectScanner.save_shading (atomic), tools/uniformity.py reference.save (non-atomic) | load_shading (reuse), recorded in entries as shading_origin.path/file_modified_utc | Overwritten by each calibration; entries keep their own copy |
| Debug spool | <tmp>/rps7200-debug-*/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz, NNN-ccd_mask.bin | npy raw pixels, raw bytes, JSON {meta, raw_layout, captured} | Raw | DirectScanner._debug_capture during each scan() when debug is on | DirectScanner._debug_flush at close(), which files each into RPS7200_DEBUG_ROOT or ./library | Exact; kept on a failed save |
| Uniformity analyse report / orientation crops | --out <file>.json; previews/orientation_<id>.png | JSON; 8-bit PNG | Derived from raw bytes + today's correction | tools/uniformity.py | Humans | Derived |

**Second reader's corrections to this table:**

Framing prescan beside a frame: prescan_before is never a library entry on any path. A dry run writes it as a corrected TIFF in the roll directory only (session `_file(..., file_entry=False)`; scan_roll.py `tiff.write`). A real roll does not write it at all. A dry-run walk's prescans are filed as their own entries, with raw pixels in scan.tif and their raw bytes; that path is correct. Debug spool: `_debug_flush` does not file claimed items. It unlinks them (direct.py:1055-1059), and claiming happens when the job is submitted (session.py:2872-2875, tools/scan.py:294-296), before the caller's library.save has succeeded, so a failed FrameWriter save loses the spooled copy too. 'Kept on a failed save' applies only to unclaimed items. Entry record: `created`, and the id timestamp, is filing time, not scan time. For debug-filed passes and tools/scan.py passes that is after close(); the scan time is extra.started_utc. extra.commands.sent[].refused holds only the exception class name. READ replies of 256 bytes or more are counted in image_reads, not kept. Calibration archive: written with the device still open (uncompressed, 1.7 MB) under `Path(reference).parent`, which is CWD-relative 'calibration/' by default. For a 'loaded' origin, shading_origin.path names the cache file that every calibration overwrites, so it no longer identifies the reference used, and no hash of the reference is recorded. Cached session reference (uniformity): written by a plain `reference.save`, not atomically. That part of the table is correct; also, uniformity never archives the calibration bytes. The rest of the table matches the code.

## What the operator can do

- List, verify, reconstruct, reindex, find duplicates, migrate-raw, migrate-direction and tag entries with tools/library.py (make verify / make reconstruct).
- Delete redundant entries with `tools/library.py duplicates --delete [--keep N]`. Only entries with the same signature AND the same raw (or image) checksum are offered.
- Mark an entry deliberately uncalibrated with `tools/library.py tag ENTRY --add uncalibrated-on-purpose` to silence verify.
- Copy or rename an entry directory: entry_path() follows the directory, and verify reports the id mismatch.
- Run the vignette study: `tools/uniformity.py capture [--ir] [--reuse] [--exposure-scale S] [--dpi N] [--tag T]`, answering accept/redo/abort per pass, then `tools/uniformity.py analyse --tag T [--out file]`.
- Export or Save As any entry from the window; it is re-corrected by library.corrected() with today's code and the recorded rotation/flip.
- Set RPS7200_DEBUG=1 (and RPS7200_DEBUG_ROOT) so probes and hold/aim prescans are filed too.

## What the operator should not do

- Delete or move calibration/: entries link to their calibration bytes only through a CWD-relative path there, and nothing else in the library holds those bytes.
- Run migrate-raw --write on labelled legacy entries without first checking reconstruct: a changed decode or a missing reference is rewritten anyway.
- Tag an entry 'uncalibrated-on-purpose' when its calibration.skipped says correction was asked for: it hides a real shortfall from verify.
- Kill the window while it closes: plain entries then stay uncompressed for good, and a partial compact leaves false checksum alarms.
- Run tools from a directory other than the repository root: library/, calibration/ and the debug root are all relative to the current directory.
- Use `uniformity.py capture --reuse` across a power cycle, or calibrate for the study with an empty transport.
- Edit scan.json by hand: it has no checksum, and verify trusts it.

## Mistakes nothing guards against

- Scanning a real roll with debug off loses every frame's raw prescan bytes, and the prescan_before of corrected frames. Only the corrected prescan.tif survives, unlabelled.
- A full or locked library disk loses the whole frame (raw bytes and delivered copies), even when the output folder has room. A locked index.json alone does the same to a completely filed entry.
- Trusting a green `make reconstruct` whose lines show 'could not decode' or 'could not read scan.tif': the exit code is 0.
- Answering 'redo' during a uniformity capture leaves the rejected pass in the analysis, where it is used as the base or orientation pass.
- Running `uniformity.py capture` without --exposure-scale crashes at metering after the operator emptied the transport. `--ir` is always refused, and only after a 3-4 minute calibration.
- The uniformity capture flow calibrates with the transport just emptied, which CLAUDE.md says preceded a wedge.
- Running a tool with `--library elsewhere` and RPS7200_DEBUG=1 files the unclaimed passes in ./library. tools/uniformity.py with debug on files every pass twice.
- Deleting prescan.tif from an entry goes unnoticed by verify.
- Running migrate-raw --write twice after an interruption overwrites scan.before-migrate-raw.tif with the raw decode.

## Dataflow notes

Into the library.
- DirectScanner.read_planes (direct.py:1799-1913) joins the READ replies into `blob` and keeps them as `last_raw` / `last_raw_layout` when keep_raw or debug is set. decode_index (1923-1971) deinterleaves by tag and turns bottom-up passes upright.
- scan() (2842-3235): 7200 dpi is realigned, and `raw_pixels` is kept as `last_pixels_raw`. It returns the CORRECTED image (apply_shading, shading.py:196) and a meta containing the shading report, shading_skipped, commands (the _CommandLog, direct.py:433-497), mode, shading_origin, read_direction, carriage_state, started_utc, filter_offsets, and so on.
- capture_record() (889-904) returns {reference: self._shading, ccd_mask: this pass's mask, raw, raw_layout}.

Writers that call library.save (library.py:216-400):
(a) FrameWriter._write (session.py:1618-1703), fed by ScanSession._file (2781-2906). _file reads capture_record, drops the bytes if raw_bytes_disagree on shape, adds rotation and flip to meta, claims the raw pixels for debug, and queues the job with compress=bool(roll).
(b) DirectScanner._debug_capture / _debug_flush (908-1125). Each pass is spooled at scan time and filed after close() into RPS7200_DEBUG_ROOT or ./library.
(c) tools/scan.py:366-375, from `pending` (raw pixels plus the capture of each pass, bracket members one entry each with bracket_index, ratio, passes and stops in extra).
(d) tools/scan_roll.py via FrameWriter (lines 676-685 for dry-run prescans, 731-776 for frames).
(e) tools/uniformity.py one_pass (586-621).

How save() writes an entry:
- _reserve mkdir, with -N on a collision.
- INCOMPLETE marker.
- scan.tif, then prescan.tif, then shading.npz, then ccd_mask.bin, then raw bytes (gzip, or plain for compress=False).
- A record whose `scan` holds SCAN_FIELDS and whose `extra` holds every other meta key, written atomically.
- Unlink INCOMPLETE, then reindex(root).

Transformations after filing:
- compact() (414-453) at session close gzips raw.bin and recompresses the TIFFs.
- migrate_direction (777-896) records the direction and turns stored pictures upright.
- migrate-raw (tools/library.py:199-294) replaces corrected scan.tif with decode_raw.

Out of the library:
- load() gives stored pixels plus reference and mask.
- corrected() (548-601) gives today's apply_shading, or 'already', 'deliberately raw', 'raw -- correction was asked for' or 'no reference'. It is consumed by the GUI's full-resolution view and export (gui.py:4330, 4593), make_comparison, dpi_analysis, linearity and registration_margin.
- decode_raw() (657-684) replays the stagger trim and feeds DemoScanner._decode (demo.py:1137-1190), which re-encodes the pixels into index lines for the demo's own passes.
- reconstruct() (687-774) re-decodes and compares exactly: shape, recorded direction, values; it re-shades legacy labelled entries before comparing.
- verify() (1068-1140) checks structure, checksums, the reference/raw presence rules and the raw checksum after decompression.

Uniformity. tools/uniformity.py rebuild (110-158) decodes raw.bin.gz with _deinterleave and applies the entry's stored reference and mask with today's apply_shading (dropping the unshaded trailing columns). register/align, block_ratios, fit_field (Huber IRLS, degree 4) and solve_components/parity_residual (rps7200/uniformity.py) then produce the odd field components. The flats are reported raw against corrected.

What cannot be recomputed from an entry today:
- the calibration lines behind shading.npz (outside the entry; absent for loaded references and for uniformity captures);
- a real-roll frame's prescan in raw form (corrected only; no bytes, no mask, only read_direction and carriage_state of its meta), and prescan_before on real rolls;
- metering-probe and hold/aim pixels and bytes unless debug was on (only summaries in metering and registration);
- the INQUIRY bytes (parsed fields only);
- the delivered JPEG quality and mono choice;
- which cached reference file (by content) a 'loaded' entry used, beyond its mtime;
- any check that today's correction matches what was delivered that day.
