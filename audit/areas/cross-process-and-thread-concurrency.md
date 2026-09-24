# Cross-process and thread concurrency (gap pass)

Area key `cross-process-and-thread-concurrency`. 18 findings: 3 high, 11 medium, 4 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: concurrency between processes, and between threads beyond Tk calls. I traced it from the code in rps7200/library.py, rps7200/session.py (ScanSession, FrameWriter, _file, _roll), tools/gui.py (event pump, the background threads, roll browser, contact sheet, shortcuts), tools/frame_edges/watch.py (EdgeWatch), rps7200/usb_transport.py (open/claim), tools/library.py (duplicates/migrate-raw/migrate-direction), tools/scan_roll.py, tools/check_scanner.py, rps7200/settings.py, rps7200/demo.py and tools/filing_load_test.py. No file was modified and no hardware was touched.


Threads in one GUI process:
- Tk main thread.
- The "scanner" worker, which owns the device.
- The FrameWriter thread, the only in-process caller of library.save.
- The frame-edges thread (EdgeWatch).
- Short-lived daemon threads: full-resolution read, histogram, save-all, export-rolls, and the demo's signing thread.

Filesystem state shared across processes: library/, library/index.json, rolls/<name>/, gui-settings.json and calibration/shading.npz, plus the USB device itself. The only atomic write in the codebase is settings.save (temp file + replace). There is no lock anywhere.


What holds:
- In-process, full-res, Save As and export only ever see complete entries. Result.entry is set only by the "filed" event, which is emitted after library.save has written scan.json.
- EdgeWatch's own generation and version logic is sound.
- A second process that tries the scanner fails before sending any command. On Linux and macOS the claim is refused (auto-detach does not steal a usbfs claim). On Windows WinUSB refuses the open. The device state of neither side is disturbed.
- Queued frames keep the output folder, format and quality they were submitted with.

What breaks:
1. **Entry-id reservation.** library.save reserves an id with a scan.json-exists check and mkdir(exist_ok=True). scan.json is written last, after a gzip lasting minutes. Two savers in the same second therefore write into one directory: one pass is lost, raw.bin.gz is corrupted, and a record can describe pass A while holding pass B's pixels under a checksum that verifies. Debug entries all share the stem <ts>_unknown-film_<dpi>dpi, and the library already shows four of them filed within one second.
2. **Mid-walk rotation.** The worker reads the Tk-mutated session.rotation and flip at every _file. A rotation during a walk therefore writes later prescanNN.tif files in a different orientation from the one survey.json records, and read_survey un-orients all of them by that single value.
3. **Busy guards live on buttons, not on actions.** Three actions bypass them:
   - Ctrl-B calls on_roll.
   - The already-open roll browser calls open_roll.
   - An aim-click calls on_nudge.
   Through these the survey and EdgeWatch are replaced mid-walk, and Moves or rolls get queued behind the running job. Every submit() also clears the stop flag of the job that is running.
4. **Stale contact sheet.** A sheet left open is lifted, not rebuilt, when the next walk ends. It then commissions a roll on the new strip with the old strip's frames and references.
5. **Output name decided early.** _unclaimed picks the output name on the worker at submit time, but the writer thread writes later. In a corrected walk the before-prescan's copy overwrites the frame's final prescan copy in the output folder.
6. **Export can pick the wrong entry.** roll_entry_index joins roll frames to entries by film.frame, which walk prescans share. Export rolls can deliver a 300 dpi prescan as "frame NN at full resolution".
7. **Unguarded lifetimes and folders:**
   - Roll-folder delete and rename are guarded only by this window's busy flag.
   - The tools/library.py mutators are uncoordinated with a running GUI. migrate-raw writes scan.tif before scan.json.
   - Quitting kills the save-all and export threads mid-file.
   - Manifests and approved.json are rewritten in place.
8. **Windows misdiagnosis.** When another process holds the scanner, the message says the driver is wrong and sends the operator to Zadig.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [CC-02](#cross-process-and-thread-concurrency-cc-02) | high | concurrency | A rotation made while a walk or roll runs changes how later prescanNN.tif/frameNN.tif are written, but the manifest records the rotation once, so read_survey and roll exports un-orient them wrongly | [P20](../problems/P20-rotation-during-walk.md) |
| [CC-11](#cross-process-and-thread-concurrency-cc-11) | high | data-integrity | Export rolls can export a walk's 300 dpi prescan entry as 'frame NN at full resolution': roll_entry_index joins on film.frame, which walk prescans share, and the last entry in directory order wins | [P21](../problems/P21-roll-to-library-join.md) |
| [CC-A1](#cross-process-and-thread-concurrency-cc-a1) | high | data-integrity | The roll's folder, approved.json's folder and a reopened roll's folder are derived three different ways, so commissioning scatters one roll across folders and the non-derivable approved.json can land in a shared 'rolls/roll/' | [P18](../problems/P18-roll-folder-identity.md) |
| [CC-01](#cross-process-and-thread-concurrency-cc-01) | medium | concurrency | library.save reserves an entry id with a non-atomic scan.json check; two concurrent savers in the same second write into ONE directory | [P10](../problems/P10-non-atomic-writes.md) |
| [CC-03](#cross-process-and-thread-concurrency-cc-03) | medium | concurrency | Busy guards sit on buttons, not on actions: Ctrl-B (on_roll) and the already-open roll browser (open_roll) replace the survey and EdgeWatch generation of a walk that is still running | [P23](../problems/P23-busy-guards-and-stop.md) |
| [CC-04](#cross-process-and-thread-concurrency-cc-04) | medium | concurrency | A contact sheet left open is lifted, not rebuilt, when the next walk ends, and it commissions a roll on the new strip with the old strip's frames, references and positions | [P22](../problems/P22-contact-sheet-state.md) |
| [CC-05](#cross-process-and-thread-concurrency-cc-05) | medium | concurrency | submit() clears the stop flag of the job that is still running, and aim-clicks (on_nudge) and Ctrl-B submit while busy | [P23](../problems/P23-busy-guards-and-stop.md) |
| [CC-06](#cross-process-and-thread-concurrency-cc-06) | medium | concurrency | Output names are chosen by _unclaimed on the worker at submit time but written later by the writer thread; the before-prescan's copy overwrites the frame's final prescan copy | -- |
| [CC-07](#cross-process-and-thread-concurrency-cc-07) | medium | concurrency | Delete/rename/duplicate of a roll folder is guarded only by this window's busy flag, not by the FrameWriter still writing that roll or by another process | [P23](../problems/P23-busy-guards-and-stop.md) |
| [CC-08](#cross-process-and-thread-concurrency-cc-08) | medium | concurrency | tools/library.py mutators run uncoordinated with a running GUI; migrate-raw --write rewrites scan.tif before scan.json, so a concurrent read or a kill leaves raw pixels labelled 'shading already applied' | [P10](../problems/P10-non-atomic-writes.md) |
| [CC-09](#cross-process-and-thread-concurrency-cc-09) | medium | concurrency | Quitting while Save all / Export rolls is running kills the daemon writer thread mid-file and leaves truncated delivered files | [P24](../problems/P24-disk-full-and-quitting.md) |
| [CC-10](#cross-process-and-thread-concurrency-cc-10) | medium | user-error | Second process vs the scanner: no device state is disturbed, but on Windows the error blames the driver and sends the operator to Zadig mid-scan; a GUI that failed to open stays enabled and silently drops jobs | -- |
| [CC-A2](#cross-process-and-thread-concurrency-cc-a2) | medium | library-completeness | The pre-correction prescan's raw bytes are never filed; the _file docstring says a debug entry covers it, but ScanSession forces debug off | [P08](../problems/P08-passes-never-filed.md) |
| [CC-A3](#cross-process-and-thread-concurrency-cc-a3) | medium | bug | roll_entry_index cannot join frames filed by tools/scan_roll.py, and joins a duplicated or same-named roll to the other roll's entries | [P21](../problems/P21-roll-to-library-join.md) |
| [CC-12](#cross-process-and-thread-concurrency-cc-12) | low | concurrency | The worker reads Tk-mutated out_dir/out_format/jpeg_quality without a snapshot: a double read of out_dir can crash a roll, and a mid-roll change splits the output with no record | -- |
| [CC-13](#cross-process-and-thread-concurrency-cc-13) | low | concurrency | index.json is rebuilt by a full O(N) re-parse on every save and rewritten in place from several threads and processes; nothing reads it, and entries() silently skips a scan.json being rewritten | -- |
| [CC-14](#cross-process-and-thread-concurrency-cc-14) | low | data-integrity | Roll/survey manifests, approved.json and the shared calibration cache are rewritten in place; a crash during the per-frame rewrite makes a resume discard every earlier frame's record | -- |
| [CC-15](#cross-process-and-thread-concurrency-cc-15) | low | doc-mismatch | CLAUDE.md presents tools/filing_load_test.py as 'the measurement' for the roll's concurrent filing, but it measures a different load and resolution, and its own usage turns debug filing off | -- |

## Findings in full

<a id="cross-process-and-thread-concurrency-cc-02"></a>

### CC-02 -- A rotation made while a walk or roll runs changes how later prescanNN.tif/frameNN.tif are written, but the manifest records the rotation once, so read_survey and roll exports un-orient them wrongly

**Severity** high · **Category** concurrency · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `rps7200/session.py:1686-1688`, `rps7200/session.py:1732-1733`, `rps7200/session.py:2011-2029`, `rps7200/session.py:1817-1833`, `tools/gui.py:3911-3924`, `tools/gui.py:7735-7742`, `tools/gui.py:4724-4750`, `tools/gui.py:4886-4899`

**Doc claim:** rps7200/session.py:2018-2023 says 'Prescans are exempt, deliberately. prescanNN.tif ... read_survey un-orients it by the single pair the manifest carries'. The pair is not constant across a walk. tools/gui.py:4670-4672 makes the same claim.

session.rotation and flip are shared between the Tk thread and the worker with no snapshot. The session's own docstring says prescanNN.tif must be un-orientable by the single pair the manifest carries. That holds only if nobody turns a picture while the walk is running. Turning the prescan on screen is the documented way to say which way up the film is, and prescans arrive one by one during a walk, so this is exactly when an operator does it. Frames filed after the turn are written in the new orientation while survey.json keeps the old one. For a roll started from the Roll button (no sheet, no per-frame approvals), frameNN.tif follows the live value too, while roll.json settings.rotation keeps the start value that roll_exports uses.

**Evidence (from the code):**

```text
Manifest, written once at roll start on the worker (session.py:1687-1688): `"rotation": self.rotation, "flipped": self.flip,`

Every _file call on the worker (session.py:2025-2029):
    if kind != "prescan":
        turn = self._frame_rotation.get(number) ...
    return self.rotation, self.flip
The prescanNN.tif is then written by FrameWriter with that turn.

The Tk thread mutates the value at any time (gui.py:3922-3923, in _carry, called by on_rotate/on_flip): `self.session.rotation = result.rotation` / `self.session.flip = result.flipped`. The sheet's rotate-all does the same (gui.py:7741-7742).

read_survey (gui.py:4724, 4740): `turn = int(manifest.get("rotation") or 0)` ... `image=preview.unorient(image, turn, mirrored)` is applied to every prescan.

roll_exports (gui.py:4888, 4897): `turn = int(settings.get("rotation") or 0)` ... `rotation=rotations.get(number, turn)`.
```

**Failure scenario:** 1. Start a 12-frame walk with rotation 0.
2. When frame 3's prescan appears upside down, press rotate 180. prescan04..12.tif are now written turned 180, and survey.json still says 0.
3. Next day, open the roll. read_survey un-orients those nine by 0, so their arrays are not the film's own orientation.

Effects:
- The edge detector reads them mirrored, so a left/right offset changes sign.
- A 90° turn changes the column count, so the detector refuses the frame.
- Approved.reference is built from these arrays, so the hold loop's check against a fresh pass fails ('unverified'), or an offset drives the film the wrong way.
- In the same session, an export of a Roll-button roll turned mid-way delivers frames 4-12 with the start rotation, unlike their frameNN.tif.

**Fix:** Snapshot rotation and flip on the worker once per job (at the top of _roll) and use the snapshot in _orientation_for for the rest of that roll. Alternatively, record the turn actually applied in each frame record (`prescan_rotation`) and un-orient by that per file. At minimum, make _carry and the sheet's rotate-all leave the session value alone while a roll or walk is running and apply it after 'finished'.

<details><summary>Second reader's check</summary>

The manifest records rotation and flip once (session.py:1687-1688, 1732-1733). _orientation_for returns the live self.rotation/self.flip for prescans (2025-2029), and _file reads it per picture (2106). The Tk thread writes session.rotation/flip at any time: _carry at gui.py:3922-3923 (on_rotate/on_flip have no busy guard) and the sheet's _all at 7741-7742. read_survey un-orients every prescan by the single manifest pair (gui.py:4724-4740), and roll_exports falls back to settings.rotation (4888-4897). Each entry's meta does record the applied turn (session.py:2110), but read_survey never consults it. Turning a prescan mid-walk is the documented way to say which way up the film is, so this path is plausible.

</details>

<a id="cross-process-and-thread-concurrency-cc-11"></a>

### CC-11 -- Export rolls can export a walk's 300 dpi prescan entry as 'frame NN at full resolution': roll_entry_index joins on film.frame, which walk prescans share, and the last entry in directory order wins

**Severity** high · **Category** data-integrity · **Verdict** confirmed · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `tools/gui.py:4979-5009`, `rps7200/session.py:1823-1828`, `rps7200/session.py:1880-1882`, `tools/gui.py:4871-4906`, `tools/gui.py:3858-3875`

**Doc claim:** tools/gui.py:4986-4987 says 'The join is on `film.frame`, which `ScanSession._file` sets to "{roll}-{NN}" for every roll frame'. It sets the same key for every walk prescan too.

The workflow is walk the strip, then scan the chosen frames into the same roll name. That files two entries per frame with the same film.frame: the walk's 300 dpi prescan entry and the full-resolution frame entry. The index keeps whichever `glob` yields last. Path.glob is in os.scandir order, which is hash order on ext4 and APFS, and not creation time. So Export rolls (and the browser's per-roll entries) can bind a frame to its prescan entry. A rescanned frame's two entries are chosen the same arbitrary way. If the operator typed a Frame in the notes, every frame of the roll carries that same string and the join fails or collapses to one key.

**Evidence (from the code):**

```text
roll_entry_index (4999-5008):
    for record_path in root.glob("*/scan.json"):
        ...
        frame = str(((record.get("film") or {}).get("frame") or "")).strip()
        roll, _, number = frame.rpartition("-")
        ...
        out.setdefault(roll, {})[int(number)] = record_path.parent

Walk prescans are filed with `replace(job.notes, frame=job.notes.frame or f"{name}-{number:02d}")` and tags ('gui','roll','prescan',name) (session.py:1826-1828). Roll frames use the identical key (1880-1882).

_deliver_one writes `library.corrected(result.entry)` and reports `f"{Path(path).name} at full resolution"` (3864-3871).
```

**Failure scenario:** 1. On Linux or macOS, walk a strip into roll '2026-09-24', then scan frames 3-8 at 3600 dpi into it.
2. Export the roll.
3. For roughly half the frames, the exported '<roll>_frame05_3600dpi.tif' is the 431-pixel, 8-bit walk prescan, reported as 'at full resolution'. Nothing in the log says otherwise.

**Fix:** Exclude entries tagged 'prescan' from roll_entry_index (or key on tags plus resolution). Among several candidates, choose deliberately: the newest by record 'created', matching the roll's resolution. Better, record each frame's entry id in roll.json from the 'filed' event on the worker side, so the join does not depend on directory order.

<details><summary>Second reader's check</summary>

roll_entry_index keys on film.frame rpartition('-') with setdefault(...)[n] = path, so the last match in glob order wins (gui.py:4999-5008). Walk prescans are filed with frame=f"{name}-{number:02d}" (session.py:1826-1828), as are roll frames (1880-1882). rolls_on_disk joins by the manifest roll name (5127). roll_exports uses those entries (4893). _deliver_one re-corrects the entry and reports 'at full resolution' (3864-3871). The file name uses settings.resolution (4902), so a 300 dpi prescan is exported under the roll's dpi. Nothing filters on the prescan tag or on resolution.

</details>

<a id="cross-process-and-thread-concurrency-cc-a1"></a>

### CC-A1 -- The roll's folder, approved.json's folder and a reopened roll's folder are derived three different ways, so commissioning scatters one roll across folders and the non-derivable approved.json can land in a shared 'rolls/roll/'

**Severity** high · **Category** data-integrity · **Verdict** found-by-verifier · **Problem** [P18](../problems/P18-roll-folder-identity.md)

**Where:** `rps7200/session.py:1634-1637`, `tools/gui.py:2938-2945`, `tools/gui.py:2156`, `tools/gui.py:2862`, `tools/gui.py:2697-2720`, `tools/gui.py:4780-4792`, `tools/gui.py:2626`

approved.json goes to rolls/<_safe(field) or 'roll'>/. The roll it describes goes to rolls/<raw field or today's date>/. The two differ whenever the Roll field is empty (every unnamed roll) or contains a character _safe rewrites (a space, '/', ':'). Reopening a roll never puts its name back in the field, so a roll commissioned from a reopened sheet, or resumed with the Roll button, is written into whatever folder the field or the date names. It is not written into the folder that was reopened, so the resume merge (session.py:1649-1667) never sees the earlier roll.json. With an empty field, every unnamed roll's approved.json overwrites the same rolls/roll/approved.json.

**Evidence (from the code):**

```text
Worker (session.py:1634-1635): `name = job.name or time.strftime("%Y-%m-%d")` / `out = Path(job.out) if job.out else self.rolls / name`. The name is not sanitised.

_write_approved (gui.py:2940-2943): `name = _safe(self.fields["roll"].get().strip())` / `folder = Path(self.session.rolls) / name` / `(folder / "approved.json").write_text(...)`. _safe('') returns 'roll' (session.py:2184).

The GUI's Roll jobs pass `name=self.fields["roll"].get().strip()` with no `out` (2156, 2862).

open_roll sets `self._sheet_roll = folder` (2626) and restores only the RESTORABLE controls (4780-4792), which do not include the roll name field.
```

**Failure scenario:** 1. Walk a strip with the Roll field empty. The walk goes to rolls/2026-09-24/.
2. Commission frames from the sheet. The roll goes to rolls/2026-09-24/ and approved.json to rolls/roll/.
3. Reopening rolls/2026-09-24 shows no approved positions.
4. The next unnamed roll overwrites rolls/roll/approved.json, and the operator's hand-set positions and turns (the one file Delete warns is not re-derivable) are gone.

Separately: reopen 'Portra-A' with the field holding 'Portra-B' and resume. The roll is written into rolls/Portra-B, and Portra-A's roll.json still lists the frames as remaining.

**Fix:** Derive the folder in one place. Have the worker compute `out` with _safe(name), and have _write_approved write into the folder the job will use: pass it as Roll.out, or write approved.json on the worker from job.approved. On open_roll, set the Roll field (or a dedicated `_loaded_roll` target) so commissioning and resuming go to the folder that was reopened.

<a id="cross-process-and-thread-concurrency-cc-01"></a>

### CC-01 -- library.save reserves an entry id with a non-atomic scan.json check; two concurrent savers in the same second write into ONE directory

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `rps7200/library.py:118-128`, `rps7200/library.py:166-176`, `rps7200/library.py:179-211`, `rps7200/library.py:229`, `rps7200/library.py:308-310`, `rps7200/direct.py:711-729`, `tests/test_decode.py:303-306`

**Doc claim:** rps7200/library.py:169-170 says 'Two scans in the same second ... would otherwise land on one id and the second would overwrite the first'. That is prevented only for sequential saves in one thread, not for concurrent ones.

The collision guard only works when saves are sequential. It tests for scan.json, the last file written, which can be tens of seconds to minutes after the directory was created, while the raw bytes gzip. It then creates the directory with exist_ok=True. A second saver in another process, or any saver outside the one FrameWriter thread, that computes the same id in the same second sees no scan.json and writes into the same directory. The -N loop has the same hole: two savers can both pick `-2` while its first writer is still gzipping. Nothing reserves the name atomically, and nothing detects the clash afterwards. Every file is written in place, and gzip.open('wb') truncates a raw.bin.gz that the other process is still appending to.

**Evidence (from the code):**

```text
library.py:166-176:
    when = datetime.now(timezone.utc)
    path = root / entry_id(meta, film, when)
    if (path / "scan.json").exists():
        base, n = path, 2
        while (path / "scan.json").exists(): ...
    path.mkdir(parents=True, exist_ok=True)

entry_id has one-second resolution: when.strftime("%Y%m%dT%H%M%SZ"). The saver writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and then `with gzip.open(path / "raw.bin.gz", "wb", compresslevel=6)` (192). Only after that does it write `"sha256": _sha256(path / "scan.tif")` (229) and finally `(path / "scan.json").write_text(...)` (308).

The debug flush files every pass with `film=FilmNotes(notes="captured with RPS7200_DEBUG on")` (direct.py:722), so every debug entry is `<ts>_unknown-film_<dpi>dpi[_ir]`. The library already contains 20260911T091346Z_unknown-film_600dpi, -2, -3 and -4 (tests/test_decode.py:303-306), which is four ids in one second.
```

**Failure scenario:** CLAUDE.md says to run long scripts in the background with RPS7200_DEBUG=1.
1. Script A (a 600 dpi ladder) closes the device and starts flushing 17 entries, several per second.
2. Script B, started once A released the device, scans one 600 dpi pass and flushes while A is still flushing.
3. Both compute 20260924T101502Z_unknown-film_600dpi.
4. B's gzip.open('wb') truncates the raw.bin.gz that A is writing, so the file ends up an interleaving of both streams.
5. B overwrites scan.tif.
6. A then hashes B's scan.tif into A's record.
7. Whichever scan.json is written last survives.

Result: one pass is gone without trace. The survivor can describe pass A's resolution and exposure while scan.tif holds pass B's pixels under an image checksum that verifies. verify reports only 'raw bytes do not match their checksum', and reconstruct reports 'no raw bytes stored for this entry'.

The same applies to the GUI FrameWriter (real, or `--demo --library library`, which needs no device and so can run alongside anything) racing a tools/scan.py, scan_roll.py or uniformity.py filing, or a probe's flush.

**Fix:** Reserve the id atomically: `path.mkdir(exist_ok=False)` in a loop that moves to -2, -3 on FileExistsError. Better still, assemble the entry in `root/.incoming-<uuid>/` and `os.rename` it to the final id (retry with -N if the rename fails because the target exists). Add a sub-second or pid component to the id. Write scan.json through temp+replace. Add a test with two threads calling save() under a frozen clock.

<details><summary>Second reader's check</summary>

library.py:166-176 is as quoted: entry_id has one-second resolution (library.py:120), the guard tests scan.json (written last, library.py:308), and the directory is created with mkdir(exist_ok=True). Every file, including gzip.open('wb') for raw.bin.gz (192), is written in place. Nothing reserves the name atomically, so two concurrent savers that compute the same id share one directory. Within one process only one thread ever saves: the FrameWriter, or _debug_flush after close. ScanSession forces debug=False (session.py:1270), so the GUI never runs both. The collision therefore needs two processes filing at the same second with the same stock/frame/dpi. Debug flushes (all 'unknown-film', direct.py:722) make that more likely, but it is still narrow, so I rate it medium rather than high. The test ids at tests/test_decode.py:303-306 show that sequential same-second collisions happen and that the -N path handles them.

</details>

<a id="cross-process-and-thread-concurrency-cc-03"></a>

### CC-03 -- Busy guards sit on buttons, not on actions: Ctrl-B (on_roll) and the already-open roll browser (open_roll) replace the survey and EdgeWatch generation of a walk that is still running

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `rps7200/shortcuts.py:117`, `tools/gui.py:740`, `tools/gui.py:2112-2133`, `tools/gui.py:2138-2139`, `tools/gui.py:2147`, `tools/gui.py:7049-7054`, `tools/gui.py:2552-2563`, `tools/gui.py:2603-2608`, `tools/gui.py:2655-2657`, `tools/gui.py:3498-3505`, `tools/gui.py:3249-3262`, `tools/gui.py:7677`

**Doc claim:** tools/gui.py:2138-2139 says 'The button this handler is behind is disabled while busy, so this cannot race a job that is still running'. The default Ctrl-B binding reaches the handler without the button. tools/gui.py:2239-2243 says opening a roll while the scanner works is refused, but that is only true for the menu entry, not for an already-open browser.

The survey list, EdgeWatch generation, sheet state and _sheet_roll belong to 'the walk now running', but three Tk-side actions can replace them while the worker is still delivering that walk's prescans. EdgeWatch's generation counter protects only against stale detector answers. Results carry no walk identity, and remember_arrangement appends any numbered prescan to whatever self.survey is while _surveying is True. The same path appends duplicates: rotating a prescan during a walk (or in a sheet opened mid-walk) appends that Result to the survey again.

**Evidence (from the code):**

```text
Default key (shortcuts.py:117): `Action("roll", "window", "Scan a roll (asks first)", f"<{ACCEL}-Key-b>")`. It is bound as `"roll": self.on_roll` (gui.py:740). Unlike prescan/scan, it does not go through _confirm_then, which is where the busy check lives.

on_roll has no busy check. On a dry run it does (2115-2133): `self.survey = []`, `self.orientations = {}`, `self.sheet_state = {}`, `self._sheet_roll = None`, `self._surveying = True`, `self.edge_watch.begin(...)`. The comment at 2138-2139 claims: 'The button this handler is behind is disabled while busy, so this cannot race a job that is still running.'

The roll browser is non-modal (no grab_set). `_open` calls `self.gui.open_roll(summary["folder"])` (7054). open_roll has no busy check: `self.survey = out["results"]` (2603) and `self.edge_watch.load(...)` (2655).

remember_arrangement (3503-3505): `if self._surveying and result.kind == "prescan" and result.number: self.survey.append(result); self.edge_watch.add(result.number, result.image)`. This also runs from _carry (3924) and the sheet's _orient (7677).
```

**Failure scenario:** (a) During a walk, the operator presses Ctrl-B (dry run still ticked) and confirms.
- The first walk's survey, orientations and sheet decisions are wiped.
- Its remaining prescans go into the new list and generation.
- At its 'finished', _surveying becomes False, so the second queued walk's prescans are never collected and no sheet opens for it.
- _sheet_roll is read from session.last_roll_dir, which the queued job may already have overwritten.

(b) With the roll browser open from before, the operator starts a walk, then double-clicks another roll.
- The live walk's prescans are appended to the reopened roll's survey.
- EdgeWatch.add replaces the reopened roll's frames of the same numbers with pictures of a different strip, so the sheet proposes positions for roll X measured on strip Y.
- At walk end, the decisions are filed under the live walk's folder.

(c) Rotating frame 3 twice during a walk puts frame 3 in the survey three times.

**Fix:** Put `if self.busy: refuse` inside on_roll and open_roll themselves, not only on the buttons, and make the browser's Open refuse while busy. Tag each Result with the job or walk id (or the EdgeWatch generation) that produced it, and have remember_arrangement add only results of the current walk, deduplicated by frame number. Destroy or rebuild an open sheet when a new walk begins.

<details><summary>Second reader's check</summary>

The Ctrl-B roll action is bound directly to on_roll (gui.py:740). The _bind_shortcuts runner (878-895) has no busy gate. on_roll checks calibration but never busy, and its dry branch clears survey, orientations, sheet_state and _sheet_roll and calls edge_watch.begin (2112-2133). The comment at 2138-2139 is contradicted by the key path. RollBrowser._open calls gui.open_roll directly (7049-7054), bypassing on_open_rolls' busy check (2239-2244). open_roll replaces self.survey (2603) and calls edge_watch.load (2655) without clearing _surveying, so a running walk's later prescans are appended to the reopened roll via remember_arrangement (3503-3505). remember_arrangement also runs from _carry and the sheet's _orient, so a prescan rotated during a walk is appended to the survey again.

</details>

<a id="cross-process-and-thread-concurrency-cc-04"></a>

### CC-04 -- A contact sheet left open is lifted, not rebuilt, when the next walk ends, and it commissions a roll on the new strip with the old strip's frames, references and positions

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P22](../problems/P22-contact-sheet-state.md)

**Where:** `tools/gui.py:2112-2133`, `tools/gui.py:2170-2173`, `tools/gui.py:3249-3262`, `tools/gui.py:7142-7150`, `tools/gui.py:8004-8015`, `tools/gui.py:2340-2350`, `tools/gui.py:3093-3095`

The sheet is a non-modal Toplevel whose content is fixed at construction. A new walk replaces everything behind it except the sheet itself. The auto-open at walk end then brings the stale sheet forward instead of building one for the strip just walked. A sheet opened mid-walk (Ctrl-K, or b_sheet still enabled from the previous survey) is never extended with the frames that arrive later either. on_scan_chosen checks busy, but not that the sheet belongs to the current survey.

**Evidence (from the code):**

```text
on_contact_sheet (2170-2173): `if self.sheet is not None and self.sheet.alive(): self.sheet.top.lift(); ... return`. It is called automatically at walk end (3261-3262).

The sheet copies its frames once (7150): `self.frames = [r for r in frames if r.image is not None]`.

Its Scan button builds approvals from those frames (8005-8015): `approved = approved_from_sheet(self.frames, picked, self.offsets, self.proposals)` ... `self.gui.on_scan_chosen(picked, approved, options)`.

The dry-run branch of on_roll resets survey and sheet_state and begins a new EdgeWatch generation (2115-2133), but never touches self.sheet. After the new walk, readings stop reaching the old sheet (3093-3095, generation mismatch).
```

**Failure scenario:** 1. Walk strip A. The sheet opens and the operator leaves it open.
2. Load strip B and walk it.
3. At the end, strip A's sheet comes forward. The operator ticks frames and presses 'Scan chosen frames'.

Result:
- A roll runs on strip B with A's offsets.
- Approved.reference arrays show strip A, so every hold fails verification or moves B's frames by A's numbers.
- The dialog's 'of the N frames walked' uses B's count.
- _store_sheet_state files A's ticks and positions in gui-settings.json under B's folder key.

A sheet opened at frame 5 of a 12-frame walk likewise never offers frames 6-12, so they are silently unscannable from it.

**Fix:** When a walk starts (on_roll dry branch) or a roll is opened, destroy any open sheet. Alternatively, give the sheet the generation and roll folder it was built for, and in on_contact_sheet and on_scan_chosen rebuild or refuse when `sheet.generation != edge_watch.generation` or the survey list has changed.

<details><summary>Second reader's check</summary>

on_contact_sheet lifts any alive sheet (gui.py:2170-2173) and is auto-called at walk end (3261-3262). The dry branch of on_roll never touches self.sheet. The sheet copies its frames once (7150), and _scan builds approvals from self.frames (8005-8015). on_scan_chosen checks busy and calibration but not that the sheet belongs to the current generation or survey. _dismiss files the stale state through _store_sheet_state under the new _sheet_roll (2340-2350, 7927). One qualification: open_roll does destroy an alive sheet (2620-2624), so the defect is specific to a new walk. It does not arise when a roll is reopened.

</details>

<a id="cross-process-and-thread-concurrency-cc-05"></a>

### CC-05 -- submit() clears the stop flag of the job that is still running, and aim-clicks (on_nudge) and Ctrl-B submit while busy

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `rps7200/session.py:1214-1218`, `rps7200/session.py:1313-1318`, `rps7200/session.py:1762`, `tools/gui.py:3020-3023`, `tools/gui.py:4500-4504`, `tools/gui.py:4587`, `tools/gui.py:2972-3018`, `tools/gui.py:740`

Clearing in submit() is redundant for queued jobs, because the worker clears at dispatch. For a running job it is harmful. After Stop, a roll keeps running until its next safe point, which can be minutes at 3600 dpi RGBI. If anything is submitted in that window, the stop is silently cancelled. The Stop button is already greyed and the log says 'stop requested', so the operator believes it is stopping. Two paths submit while busy: the default Ctrl-B roll key, and an aim-click on a prescan with 'aim' ticked, which queues a sub-frame Move behind the running job. That Move then runs after the walk or roll ends, at a film position it was not computed for.

**Evidence (from the code):**

```text
session.py:1214-1218:
    def submit(self, job: Job) -> None:
        # A stop asked for during the previous job must not silently kill the
        # next one the operator deliberately started.
        self._stop.clear()
        self._jobs.put(job)

The worker already clears it when a job starts (1317): `self._stop.clear()`. The roll reads the flag live: `should_stop=self._stop.is_set` (1762).

on_stop disables the button after one press (3023): `self.b_stop.configure(state="disabled")`.

Aim-click: on_press calls self._aim when v_aim is ticked on a prescan (4501-4504). _aim then calls `self.on_nudge(1 if want > 0 else -1, millimetres=abs(want))` (4587), and on_nudge ends in `self.session.submit(Move(millimetres=...))` (3018) with no busy check.
```

**Failure scenario:** 1. During a long roll the operator presses Stop.
2. While the current frame is still scanning, they tick 'aim' and click a prescan to line up the next picture, or press Ctrl-B.
3. submit() clears _stop, so the roll never sees the stop and runs to the end of the strip.
4. The queued Move then nudges the film by an amount computed from a different frame. Frames of a later roll without approvals keep that displacement.

**Fix:** Remove `self._stop.clear()` from submit() and rely on the clear at job start (session.py:1317). Add a busy check to on_nudge (and to _aim), matching the one _confirm_then applies to the prescan and scan keys.

<details><summary>Second reader's check</summary>

submit() clears _stop (session.py:1214-1218), and the worker also clears it when a job starts (1317), so the clear in submit only affects a running job. The roll polls _stop live (1762, 1928). on_stop disables the button (gui.py:3023). on_nudge ends in session.submit(Move(...)) (3018) with no busy check, and _aim reaches it from any click on a prescan with 'aim' ticked (4501-4504, 4587). v_aim is not among _run_buttons (3346-3348). Ctrl-B reaches on_roll with no busy check (see CC-03). Either path silently cancels a pending stop.

</details>

<a id="cross-process-and-thread-concurrency-cc-06"></a>

### CC-06 -- Output names are chosen by _unclaimed on the worker at submit time but written later by the writer thread; the before-prescan's copy overwrites the frame's final prescan copy

**Severity** medium · **Category** concurrency · **Verdict** confirmed

**Where:** `rps7200/session.py:2067-2071`, `rps7200/session.py:2192-2204`, `rps7200/session.py:1817-1854`, `rps7200/session.py:1074-1083`, `rps7200/session.py:1101-1103`, `rps7200/session.py:1348-1355`, `tools/gui.py:2436-2438`, `rps7200/session.py:2163-2164`

The name is decided on one thread and the file is created on another, so two jobs submitted before the first write lands get the same free name. In a walk with the automatic correction on (correct=True, dry run), every frame the correction moved submits its final prescan and its pre-correction 'before' prescan with the same output-folder name. The writer writes the final one and then overwrites it with the before picture. export.write writes in place. The before job also logs a spurious 'picture N could not be filed: None' for every corrected frame. Export rolls (gui.py:2436-2438) uses the same `{roll}_frameNN_{dpi}dpi` naming and runs on its own thread with no busy check, so exporting into the output folder while that roll is being rescanned has the same race.

**Evidence (from the code):**

```text
_file on the worker (2068-2071):
    if self.out_dir is not None:
        where = (self.out_dir / PRESCAN_SUBDIR if kind == "prescan" else self.out_dir)
        paths.append(_unclaimed(where / self._out_name(number, meta, roll)))
_unclaimed only checks `if not wanted.exists(): return wanted` (2198-2199), and the file is written later on the FrameWriter thread (1074-1081).

In a walk, two _file calls with kind="prescan" and the same number are made back to back: rf.prescan (1818-1833) and rf.prescan_before (1841-1854). _out_name gives both `{roll}_frame{NN}_{dpi}dpi.tif` (2164).

The before job has file_entry=False, so library is None and entry is None. `self.on_done(job.get("seq", 0), job["number"], entry, None)` (1102-1103) then reaches `_filed`: `self._emit("log", text=f"picture {number} could not be filed: {err}")` (1353-1354).
```

**Failure scenario:** 1. With an output folder set and 'correct' ticked, walk a strip.
2. For every frame the correction moved, out/prescans/<roll>_frame05_300dpi.tif ends up holding the picture from before the move, not the one the sheet shows and the roll folder's prescan05.tif holds.
3. The log reads 'picture 5 could not be filed: None' for each such frame, so the operator thinks prescans were lost.

**Fix:** Resolve the free name on the writer thread at write time, by creating the file with an exclusive open ('xb' / O_EXCL) and incrementing on FileExistsError. Give the before-prescan a distinct delivered name (for example a '-before' suffix, as in the roll folder). Have _filed skip jobs submitted with file_entry=False instead of reporting them as failures.

<details><summary>Second reader's check</summary>

_file computes the output path with _unclaimed on the worker (session.py:2068-2071). _unclaimed only tests exists() (2198-2199), and FrameWriter writes later (1074-1081). In a correcting walk, the final prescan and prescan_before are filed back to back (1818-1854), both kind='prescan' with the same number and roll. _out_name gives both `{roll}_frame{NN}_{dpi}dpi.tif` (2164). The second job almost always gets the same name, because the first write has not landed yet, so the before picture overwrites the final one. The before job has file_entry=False, so entry is None, and _filed logs 'picture N could not be filed: None' (1353-1354). The export-rolls part is also accurate: on_export_rolls checks only _saving, not busy (2390-2394).

</details>

<a id="cross-process-and-thread-concurrency-cc-07"></a>

### CC-07 -- Delete/rename/duplicate of a roll folder is guarded only by this window's busy flag, not by the FrameWriter still writing that roll or by another process

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P23](../problems/P23-busy-guards-and-stop.md)

**Where:** `tools/gui.py:2367-2381`, `tools/gui.py:2484-2537`, `tools/gui.py:2453-2482`, `rps7200/session.py:1320-1331`, `rps7200/session.py:1341-1343`, `rps7200/session.py:1074-1075`, `rps7200/session.py:1918-1926`

'busy' means the scanner worker has a job. It says nothing about the writer thread, which keeps writing frameNN.tif into the roll folder after 'finished', and nothing about any other process. A second window that failed to open the scanner, or a shell running tools/scan_roll.py, has busy=False while another process scans into the folder.

**Evidence (from the code):**

```text
_roll_is_busy (2369-2373): `if self.busy: messagebox.showinfo(what, "The scanner is working. Wait for it to finish -- a roll it is writing into is not one to move or remove.")`.

The worker emits 'finished' when _dispatch returns (1326), while the FrameWriter still holds up to three frames (queue depth 2 plus one in progress). The writer drains only in the finally block at session close (1341-1343).

The writer recreates folders: `Path(path).parent.mkdir(parents=True, exist_ok=True)` (1075).

Delete: `shutil.rmtree(summary["folder"])` (2513). Rename: `source.rename(target)` (2533). The worker rewrites `manifest_path.write_text(...)` after every frame (1924-1926).
```

**Failure scenario:** After a 3600 dpi roll reports done, the operator immediately renames or deletes its folder in the browser. Consequences:
- The writer's mkdir recreates rolls/<old name>/ holding only the last frame(s).
- The renamed roll lacks them.
- A delete also removes approved.json, the one non-derivable file.

From a second window, deleting the folder that the first window's roll is writing makes the worker's next roll.json write raise FileNotFoundError. The multi-hour roll is aborted as 'failed' mid-strip.

**Fix:** Track outstanding writer jobs per roll folder (a counter updated in _file and on_done) and treat a folder with pending writes as busy. Also refuse while a save-all or export is running. For cross-process safety, have the worker hold a lock file in the roll folder while a roll or walk runs, and have the browser refuse a folder whose lock is held.

<details><summary>Second reader's check</summary>

_roll_is_busy tests only self.busy and the loaded roll (gui.py:2367-2381). 'finished' is emitted as soon as _dispatch returns (session.py:1326), and the FrameWriter is drained only at session close (1341-1343). The writer recreates parent folders (1075). Delete uses rmtree (2513), rename uses source.rename (2533), and duplicate uses copytree (2479, which can copy a half-written frame). A second window whose session failed to open has busy=False. The worker rewrites roll.json after each frame (1924-1926), and that write raises FileNotFoundError once the folder is gone.

</details>

<a id="cross-process-and-thread-concurrency-cc-08"></a>

### CC-08 -- tools/library.py mutators run uncoordinated with a running GUI; migrate-raw --write rewrites scan.tif before scan.json, so a concurrent read or a kill leaves raw pixels labelled 'shading already applied'

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P10](../problems/P10-non-atomic-writes.md)

**Where:** `tools/library.py:115-135`, `tools/library.py:193-207`, `rps7200/library.py:373-377`, `rps7200/library.py:741-750`, `tools/gui.py:3858-3886`, `tools/gui.py:4104-4120`

These commands open no lock and take no snapshot. They assume nothing else is reading or writing the library.
- **migrate-raw --write.** It replaces scan.tif first and scan.json second, both in place. Between the two writes, a library.corrected() call from the GUI's full-resolution thread, Save As, Save All or export gets raw pixels with a record saying they are already corrected, and returns them uncorrected. A Ctrl-C or crash between the two writes makes that state permanent until migrate-raw is run again.
- **duplicates --delete.** It can remove an entry the GUI holds as Result.entry. Save As then silently falls back to the decimated preview and claims the file is 'not filed yet'.
- **On Windows, a busy file.** rmtree can fail part-way on a file the GUI's reader has open. The uncaught OSError aborts the tool, leaves a half-deleted entry that entries() no longer lists (scan.json gone), and skips reindex.

**Evidence (from the code):**

```text
migrate-raw --write (tools/library.py:195-206):
    tiff.write(str(path / "scan.tif"), plain, resolution=resolution)
    image = record.setdefault("image", {})
    image["corrections_applied"] = []
    ...
    (path / "scan.json").write_text(json.dumps(record, ...))

corrected() trusts the record (library.py:374-377): `if "shading" in applied: record["corrected"] = "already"; return image, record`.

duplicates --delete: `shutil.rmtree(path)` (tools/library.py:129), with no try/except and no reindex until the loop ends.

GUI Save As / Save All / export (gui.py:3858): `if result.entry and (result.entry / "scan.tif").exists():` ... and otherwise `return (f"{Path(path).name} -- reduced preview, the full-resolution file is not filed yet" ...)` (3883-3885).
```

**Failure scenario:** Case 1:
1. The operator runs `tools/library.py migrate-raw --write` with the GUI open.
2. It is interrupted (Ctrl-C) after rewriting one entry's scan.tif.
3. From then on, every export of that entry is uncorrected, labelled '(already)'.

Case 2:
1. `duplicates --delete` removes an older entry that the open session is displaying.
2. Save As writes a 1500-pixel preview under a full-resolution file name and says the scan 'is not filed yet'.

**Fix:** Take an advisory lock (for example library/.lock via O_EXCL, or a lock-file library) in library.save and in every tools/library.py mutator. Refuse to run the mutators while the lock is held. In migrate-raw, write scan.tif.new and scan.json.new, then os.replace both, scan.json last. In the GUI, when a known entry has vanished, say it was deleted rather than 'not filed yet'.

<details><summary>Second reader's check</summary>

migrate-raw --write rewrites scan.tif first and scan.json second, both in place (tools/library.py:193-206). corrected() trusts corrections_applied (library.py:373-377). duplicates --delete calls rmtree unguarded (tools/library.py:129), and reindex runs only after the loop. _deliver_one falls back to the preview with 'not filed yet' when scan.tif is missing (gui.py:3858, 3883-3885). No lock exists anywhere. Two mitigations are in the code: an interrupted migrate is re-planned on the next run (applied is still ['shading']), and `verify` would flag the image sha mismatch. The Windows claim, that the GUI keeps a file open, is weak: tiff.read and gzip reads are short-lived.

</details>

<a id="cross-process-and-thread-concurrency-cc-09"></a>

### CC-09 -- Quitting while Save all / Export rolls is running kills the daemon writer thread mid-file and leaves truncated delivered files

**Severity** medium · **Category** concurrency · **Verdict** confirmed · **Problem** [P24](../problems/P24-disk-full-and-quitting.md)

**Where:** `tools/gui.py:3039-3050`, `tools/gui.py:3052-3068`, `tools/gui.py:3070-3086`, `tools/gui.py:3822-3841`, `tools/gui.py:2431-2451`, `rps7200/export.py:181-193`

Save all and Export re-correct each entry at full resolution, which takes seconds per frame and minutes per roll. The window tracks this with self._saving but never consults it on quit. When mainloop ends the interpreter exits and the daemon thread is killed wherever it is. The file being written is left truncated under its final name, beside the complete ones, with no log line. For JPEG a partial .dng can be left as well.

**Evidence (from the code):**

```text
on_close checks only the scanner (3040-3045): `if self.busy and not messagebox.askokcancel("Quit", "A scan is still running. ...")`.

_wait_to_quit waits only for the session thread (3064-3066): `thread = self.session._thread; if self._session_closed or thread is None or not thread.is_alive(): self._quit()`.

The batch threads are daemons: `threading.Thread(target=run, daemon=True, name="save-all").start()` (3840-3841) and `threading.Thread(target=run, daemon=True, name="export-rolls").start()` (2451). export.write writes in place through tiff.write / _write_jpeg (export.py:181-193).
```

**Failure scenario:** 1. The operator starts 'Export' of a 38-frame roll into a delivery folder.
2. Seeing the scanner idle, they close the window after a minute. on_close does not ask.
3. frame07's TIFF is left half-written, frames 08-38 are never written, and the 'saved 6 of 38' line never appears.
4. NegPy later opens a corrupt frame07 that looks like a finished file.

**Fix:** In on_close, also check self._saving and either wait (poll as _wait_to_quit does) or ask. Make the batch threads non-daemon, or join them in _quit. Write each delivered file to '<name>.part' and os.replace it to the final name.

<details><summary>Second reader's check</summary>

on_close asks only when self.busy (gui.py:3040-3045). _wait_to_quit waits only for the session thread (3064-3066), which does include the FrameWriter drain. The save-all and export-rolls threads are daemon threads (3840-3841, 2451), and _saving is never consulted on quit. export.write writes the final name in place (export.py:181-193). Destroying the root ends mainloop, the interpreter exits, and the daemon threads die wherever they are.

</details>

<a id="cross-process-and-thread-concurrency-cc-10"></a>

### CC-10 -- Second process vs the scanner: no device state is disturbed, but on Windows the error blames the driver and sends the operator to Zadig mid-scan; a GUI that failed to open stays enabled and silently drops jobs

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:444-463`, `rps7200/usb_transport.py:452-458`, `rps7200/usb_transport.py:477-484`, `tools/check_scanner.py:128-138`, `rps7200/session.py:1293-1304`, `rps7200/session.py:5-8`, `tools/gui.py:3288-3291`, `tools/gui.py:3350-3365`, `tools/gui.py:1929-1933`

**Doc claim:** rps7200/usb_transport.py:452-456 says 'macOS and Windows answer NOT_SUPPORTED' for auto-detach. On libusb >= 1.0.25, macOS supports kernel-driver detach (with authorization), so this is version-dependent. rps7200/session.py:5-8 says 'The only mutual exclusion is libusb_claim_interface'. On Windows it is the open that excludes.

The question asked: does a second process opening the scanner leave either side inconsistent? On the device side, no.
- On Linux, open succeeds and the claim fails with BUSY; auto-detach does not steal another process's usbfs claim.
- On macOS, the claim fails with an exclusive-access error.
- In both cases the handle is closed before any control transfer.
- On Windows, WinUSB allows one open handle, so libusb_open_device_with_vid_pid returns NULL. The code can only interpret that as 'driver binding', and both the transport message and check_scanner tell the operator to replace the driver.

On the software side, a second GUI whose session failed to open stays up with enabled buttons and an idle light. Calibrate, Scan and Roll then queue jobs that no thread will ever run, with no message. That window's roll browser and settings writes also act on the first window's rolls and gui-settings.json; see CC-07 and CC-13.

**Evidence (from the code):**

```text
_raw_open (445-463):
    handle = _lib.libusb_open_device_with_vid_pid(...)
    if not handle: raise ScannerNotFound(self._why_not_found())
    ...
    rc = _lib.libusb_claim_interface(self._handle, 0)
    if rc < 0: _lib.libusb_close(self._handle) ... raise UsbError(...)
Nothing is sent to the device before the claim.

The Windows text for a NULL handle while the device is on the bus (478-483): 'Windows binds its own Image/WIA driver to it ... Replace it with WinUSB or libusbK using Zadig'.

check_scanner rung 3 (129-133): 'It is on the bus -- rung 2 just said so -- so this is the driver binding, which is exactly what Zadig changes.'

The failed session returns with no worker (session.py:1301-1304). 'closed' then calls _set_busy(False) (gui.py:3290), which re-enables the run buttons and sets the light back to idle (3359-3365). on_calibrate still does `self.calibrated = True` (1932).
```

**Failure scenario:** 1. On Windows, with a roll running in the GUI, the operator runs `uv run python tools/check_scanner.py` (documented as harmless) or opens a second window.
2. It says the Image/WIA driver is bound and to use Zadig.
3. Following that, Zadig reinstalls the driver. The device is restarted under the first process's read mid-scan, and the scanner wedges.

**Fix:** In _why_not_found on win32, say the device may be held by another program (this driver, CyberView, VueScan) before suggesting Zadig. If libusb_open itself can be called on the enumerated device, map LIBUSB_ERROR_ACCESS to 'in use'. Put the same wording in check_scanner rung 3. After a failed open, keep the run buttons disabled and the light broken, or refuse submit() when the worker is not alive.

<details><summary>Second reader's check</summary>

_raw_open sends nothing before the claim. On a claim failure it closes the handle and raises (usb_transport.py:445-463). On Linux, libusb refuses to auto-detach a usbfs claim, so the claim fails BUSY. The device side therefore stays consistent. On Windows a NULL handle while the device is enumerated always produces the 'Image/WIA driver ... Zadig' text (478-483), and check_scanner repeats the diagnosis (128-133). A second GUI whose session failed gets 'failed' and then 'closed'. 'closed' calls _set_busy(False), which re-enables the run buttons and sets the light back to idle (3288-3291, 3359-3365). submit() puts jobs on a queue that no thread reads. The failure itself is announced once by the 'No scanner' error box (3285-3286), so 'silently' applies only to later jobs. The macOS auto-detach remark is version-dependent and incidental.

</details>

<a id="cross-process-and-thread-concurrency-cc-a2"></a>

### CC-A2 -- The pre-correction prescan's raw bytes are never filed; the _file docstring says a debug entry covers it, but ScanSession forces debug off

**Severity** medium · **Category** library-completeness · **Verdict** found-by-verifier · **Problem** [P08](../problems/P08-passes-never-filed.md)

**Where:** `rps7200/session.py:2047-2058`, `rps7200/session.py:1265-1271`, `rps7200/session.py:1834-1854`, `rps7200/direct.py:3578-3586`, `tools/scan_roll.py:344-353`, `tools/scan_roll.py:502-510`

**Doc claim:** rps7200/session.py:2054-2058 ('Under RPS7200_DEBUG=1 that picture already has a correct entry anyway'); CLAUDE.md 'File every scan in the library, with its raw bytes'.

When the walk's correction moves a frame, the pass the frame arrived as is kept only as a corrected TIFF, prescanNN-before.tif, in the roll folder. It has no raw bytes, no library entry and no shading or mask of its own. The debug filing the docstring relies on cannot run in either roll path, whatever RPS7200_DEBUG says, because both construct DirectScanner with debug=False. So the evidence of whether a correction helped cannot be re-derived with newer decode or correction code, contrary to the library's central requirement. The intermediate passes of the aim/hold loops are not filed either.

**Evidence (from the code):**

```text
_file docstring (session.py:2054-2058): 'Under `RPS7200_DEBUG=1` that picture already has a correct entry anyway, filed at the instant it was taken'.

_default_scanner (1265-1271): `DirectScanner(verbose=self.verbose, debug=False)`. scan_roll does the same: `DirectScanner(verbose=args.verbose, debug=False)` (353).

The before pass keeps only pixels (direct.py:3583-3586): `prescan_before = prescan_image` / `prescan_image = fix.pop("prescan")` / `raw_prescan = self.last_pixels_raw`. It is filed with `file_entry=False` (session.py:1852).
```

**Failure scenario:** A walk with 'correct' on moves 9 of 24 frames. Months later, a changed decode or correction is to be evaluated against the 'before' pictures. Only 8-bit or 16-bit corrected TIFFs exist in the roll folder, and nothing about them can be recomputed.

**Fix:** Keep the before pass's raw pixels and capture record at the moment it is replaced (as done for raw_prescan), and file it as its own entry tagged 'prescan-before'. Or correct the docstring so it no longer claims debug filing covers it.

<a id="cross-process-and-thread-concurrency-cc-a3"></a>

### CC-A3 -- roll_entry_index cannot join frames filed by tools/scan_roll.py, and joins a duplicated or same-named roll to the other roll's entries

**Severity** medium · **Category** bug · **Verdict** found-by-verifier · **Problem** [P21](../problems/P21-roll-to-library-join.md)

**Where:** `tools/scan_roll.py:571-581`, `tools/gui.py:4999-5008`, `tools/gui.py:5127`, `tools/scan_roll.py:606-609`

For a roll scanned from the command line, 'myroll/05' has no '-', or '2026-09-24/05' gives a non-digit number. Every such frame is skipped, so the roll browser reports no library entries and Export says there is 'nothing to re-correct from'. Meanwhile the exact per-frame entry path is recorded in that roll.json and ignored. Because the join is by the manifest's roll name and not by folder, a Duplicate (which keeps the roll name) and the original share one entry map. Exporting either delivers whichever same-numbered entry glob returned last, which may be the other copy's rescan.

**Evidence (from the code):**

```text
scan_roll files `frame=f"{roll_name}/{number:02d}"` (scan_roll.py:579).

roll_entry_index splits on '-': `roll, _, number = frame.rpartition("-")` / `if not roll or not number.isdigit(): continue` (gui.py:5004-5006).

rolls_on_disk joins on the manifest's roll name: `summary["entries"] = dict(index.get(summary["roll"], {}))` (5127).

scan_roll records each frame's entry in its roll.json (`record["entry"] = str(entry)`, 606-609), but nothing in the GUI reads that field.
```

**Failure scenario:** 1. Scan a roll with `tools/scan_roll.py --roll portra`.
2. Open the roll browser. Export reports no frames with a library entry.
3. Separately, duplicate roll X to X-2 and rescan X-2 at a different dpi.
4. Exporting X may deliver X-2's frames.

**Fix:** Prefer the per-frame 'entry' recorded in the manifest. Record it in the GUI's roll.json too, once the 'filed' event arrives. Accept both 'roll-NN' and 'roll/NN' in the fallback join, and scope the join to the roll folder (for example with a roll-folder tag) rather than the manifest name.

<a id="cross-process-and-thread-concurrency-cc-12"></a>

### CC-12 -- The worker reads Tk-mutated out_dir/out_format/jpeg_quality without a snapshot: a double read of out_dir can crash a roll, and a mid-roll change splits the output with no record

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `rps7200/session.py:2067-2071`, `rps7200/session.py:2129`, `rps7200/session.py:2156-2167`, `tools/gui.py:1544-1545`, `tools/gui.py:1577-1582`, `tools/gui.py:1893-1895`

Queued frames are unaffected by a change: the path, format suffix and quality are bound into the job dict at submit time. Frames submitted after the change use the new values. The roll's manifest records neither the output folder nor the format, so a roll split across two folders or formats is unrecorded. The one real race: if Clear runs between the check and the use of self.out_dir, `None / 'prescans'` raises TypeError inside _file. That aborts the roll, and the frame just scanned never reaches FrameWriter, so its raw bytes are lost. The window is a few bytecodes (GIL switch interval 5 ms), so this is rare.

**Evidence (from the code):**

```text
Worker (session.py:2068-2071):
    if self.out_dir is not None:
        where = (self.out_dir / PRESCAN_SUBDIR if kind == "prescan" else self.out_dir)
The attribute is read twice.

Tk thread: `ttk.Button(row, text="Clear", command=lambda: self._set_outdir(""))` (1544-1545), with `self.session.out_dir = Path(path) if path else None` (1895). Also `self.session.out_format = ...` / `self.session.jpeg_quality = quality` (1580-1582), read at `quality=self.jpeg_quality` (2129) and `export.suffix_for(self.out_format)` (2162).
```

**Failure scenario:** The operator presses 'Clear' on the output folder exactly as a frame is filed. The roll ends 'failed: TypeError' and that frame's library entry is never written. More commonly, switching TIFF to JPEG mid-roll gives frames 1-9 as TIFF and 10-24 as JPEG plus DNG, and nothing in roll.json says which.

**Fix:** In _file, bind `out_dir = self.out_dir` (and out_format, jpeg_quality) once into locals. Better, snapshot them per job at the top of _roll and record them in the manifest. Grey the output controls while a roll runs, or state in the log that the change applies from the next frame.

<details><summary>Second reader's check</summary>

_file reads self.out_dir twice (session.py:2068-2070), and _set_outdir can set it to None from Tk at any time (gui.py:1544-1545, 1893-1895). A switch between the check and the use raises TypeError before writer.submit, which aborts the roll and loses that frame. The window is a few bytecodes wide. out_format is read in _out_name at submit time (2162) and jpeg_quality is bound into the job (2129), so queued frames are unaffected. The manifest records neither setting.

</details>

<a id="cross-process-and-thread-concurrency-cc-13"></a>

### CC-13 -- index.json is rebuilt by a full O(N) re-parse on every save and rewritten in place from several threads and processes; nothing reads it, and entries() silently skips a scan.json being rewritten

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `rps7200/library.py:310`, `rps7200/library.py:753-773`, `rps7200/library.py:741-750`, `rps7200/library.py:74-102`, `tools/gui.py:4010-4012`

Per save, the FrameWriter spends O(N) JSON parsing (N=311 here) plus two git subprocesses (up to 10 s each on a slow Windows host). For a GUI single scan this all happens with the device open and idle. index.json is written in place by the writer thread, the Tk thread (on_delete) and any other process's save or tools/library.py run, so concurrent writers can leave it torn (the longer earlier content's tail after the shorter later one). No production code reads index.json (only tests/test_library.py:204), so a torn index is not a data-loss path. But any library reader, including verify, duplicates, reindex and list, silently omits an entry whose scan.json is mid-rewrite (migrate tools, or a save finishing), and reports 'library is intact'.

**Evidence (from the code):**

```text
save() ends with `reindex(root)` (310). reindex parses `for r in entries(root)`, which globs and json.loads every scan.json, then `index.write_text(json.dumps(summary, indent=2), ...)` (771-772).

The Tk thread calls it too: `shutil.rmtree(entry); library.reindex(entry.parent)` (gui.py:4011-4012).

entries() skips anything it cannot read: `except (OSError, json.JSONDecodeError): continue` (748-749).

Each save also runs `git rev-parse` and `git status --porcelain` with timeout=10 (80-99).
```

**Failure scenario:** The operator deletes a pass in the GUI while the FrameWriter files a frame. Both rewrite index.json at once, and it ends with trailing bytes of the older, longer summary, which is invalid JSON. Separately, `tools/library.py verify` run during migrate-raw skips the entry being rewritten and prints 'library is intact'.

**Fix:** Write index.json via temp file + os.replace, or drop it (it is derivable and unread). Make reindex incremental (append or replace one row), or run it at session end rather than per save. Cache provenance() per process. Make entries() count or report unreadable scan.json files instead of skipping them silently.

<details><summary>Second reader's check</summary>

save() ends with reindex(root) (library.py:310). reindex re-parses every scan.json and write_text()s index.json in place (753-772). It is also called from the Tk thread in on_delete (gui.py:4011-4012) and from tools/library.py. The only reference to INDEX is library.py:771, and no production code reads index.json. entries() skips unreadable scan.json files silently (741-749). Each save runs provenance(), which makes two git subprocess calls with timeout=10 (library.py:80-99). N=311 cannot be verified because this checkout has no library/.

</details>

<a id="cross-process-and-thread-concurrency-cc-14"></a>

### CC-14 -- Roll/survey manifests, approved.json and the shared calibration cache are rewritten in place; a crash during the per-frame rewrite makes a resume discard every earlier frame's record

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1649-1654`, `rps7200/session.py:1918-1926`, `tools/scan_roll.py:326-329`, `tools/gui.py:2939-2955`, `rps7200/shading.py:75-91`, `rps7200/direct.py:612-615`

write_text truncates first and then writes. A process killed inside that window leaves an empty or partial roll.json. force_abort's docstring says closing libusb under a transfer 'can take this process with it', and a power loss or Ctrl-C can do the same. On the next resume the unreadable file becomes `{}` without a word, and the rewritten manifest describes only the new run. That is exactly the 'four frames scanned across two sessions came back as a two-frame roll' loss the merge code was written to prevent. approved.json, described as the one non-derivable file in a roll folder, has the same exposure. calibration/shading.npz is shared by the GUI, tools/scan.py and scan_roll.py; a partial one makes 'reuse' fail.

**Evidence (from the code):**

```text
Per frame (session.py:1924-1926): `manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")`.

Resume (1650-1654):
    try:
        earlier = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        earlier = {}
The new manifest then starts from `"frames": list(earlier.get("frames") or [])` (1747).

scan_roll checkpoint (tools/scan_roll.py:328): `manifest_path.write_text(...)`.

approved.json (gui.py:2943): `(folder / "approved.json").write_text(json.dumps({...}))`.

The calibration cache is overwritten by `np.savez_compressed(path, **arrays)` (shading.py:91) on every measured calibration (direct.py:615).
```

**Failure scenario:** The process dies while roll.json is being rewritten after frame 17. The next day's resume reads '' and falls back to `earlier = {}`. roll.json now lists only the resumed frames, and the browser offers frames 1-17 as never scanned.

**Fix:** Write manifests, approved.json and the calibration cache through '<name>.part' + os.replace, as settings.save already does (settings.py:88-95). On resume, if the existing manifest is unreadable, refuse or keep a backup (roll.json.bak) instead of silently starting from {}.

<details><summary>Second reader's check</summary>

The following are all written in place: roll.json/survey.json via write_text after each frame (session.py:1924-1926), the scan_roll checkpoint (tools/scan_roll.py:326-329), approved.json (gui.py:2943), and the calibration cache via np.savez_compressed (shading.py:91, called via save_shading at direct.py:563-570, 612). On resume, an unreadable roll.json silently becomes {} (session.py:1650-1654). settings.save is the one atomic writer (settings.py:88-95). Truncation needs a kill inside the write, so this is low.

</details>

<a id="cross-process-and-thread-concurrency-cc-15"></a>

### CC-15 -- CLAUDE.md presents tools/filing_load_test.py as 'the measurement' for the roll's concurrent filing, but it measures a different load and resolution, and its own usage turns debug filing off

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/filing_load_test.py:1-20`, `tools/filing_load_test.py:42-61`, `tools/filing_load_test.py:83-92`, `rps7200/session.py:1060-1100`, `rps7200/library.py:179-211`

**Doc claim:** CLAUDE.md:170-172 says '`tools/filing_load_test.py` is the measurement: alternating identical passes on a quiet host and one gzipping in the background ... Run it before trusting the roll path unattended.'

The roll path's hazard argument rests on this measurement. It times 22-second 8-bit 300 dpi RGB passes against a gzip-only load. The roll it vouches for runs 1800-7200 dpi 16-bit RGBI passes alongside TIFF encoding, hashing, subprocesses and O(N) JSON parsing on the writer thread. Most of that holds the GIL between the transport's ctypes calls, and gzip largely does not. The docstring also tells the user to set RPS7200_DEBUG=0, contrary to CLAUDE.md's 'always scan with debug filing on', so the eight passes are never filed.

**Evidence (from the code):**

```text
Tool usage (filing_load_test.py:3): `RPS7200_DEBUG=0 uv run python tools/filing_load_test.py`.

The load (54-57): `gzip.compress(self.payload, compresslevel=6)` on random bytes in a loop.

The passes (86-89): `s.scan(resolution=300, infrared=False, depth=DEPTH_8, frame=FULL_FRAME, shading=False, require_media=False)`.

FrameWriter's real per-frame work (session.py:1067-1100 and library.py:179-211): orient/mono, export.write of the delivered TIFF/JPEG/DNG, tiff.write of scan.tif (tifffile deflate), prescan.tif, np.savez_compressed of shading.npz, gzip of raw, sha256 of scan.tif, the git subprocesses, and reindex (JSON parsing that holds the GIL).
```

**Failure scenario:** The tool reports 'no measurable effect -- overlapping filing looks safe', and the roll path is trusted unattended. The load and pass sizes that actually run in a roll were never measured.

**Fix:** Make the Grinder run the real FrameWriter._write on a stored library entry (or a synthetic one of roll size) into a temp root. Add a --dpi option. Report the time between consecutive READ windows, not only total pass time. Drop RPS7200_DEBUG=0 from the usage line, or explain why these passes are exempt.

<details><summary>Second reader's check</summary>

Its usage line sets RPS7200_DEBUG=0 (filing_load_test.py:3). The load is gzip.compress of random bytes (54-57), and zlib releases the GIL for large buffers. The passes are 300 dpi 8-bit RGB with shading=False (86-89). FrameWriter's real work is different: TIFF encoding, savez_compressed, sha256, git subprocesses, and JSON-parsing reindex (session.py:1067-1100, library.py:179-311). CLAUDE.md:170-172 nonetheless presents the tool as 'the measurement' to run before trusting the roll path.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry directory: scan.tif, raw.bin.gz, shading.npz, ccd_mask.bin, prescan.tif, scan.json | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/ | TIFF (tifffile deflate or builtin uncompressed, uint8/uint16), gzip level 6 of the concatenated READ payloads, npz (float64 reference), raw bytes, JSON | Raw pixels, except for GUI single scans and demo entries (reported elsewhere). raw.bin.gz is byte-exact. | library.save (rps7200/library.py:131-311), called from the FrameWriter thread (session.py:1090), DirectScanner._debug_flush after close (direct.py:719), tools/scan.py:239, tools/uniformity.py:609, and the scan_roll FrameWriter. Id reservation is a check-then-mkdir (library.py:171-176); every file is written in place, scan.json last. | GUI full-res thread and Save As/Save All/export threads via library.corrected (gui.py:4110, 3864), tools/library.py (verify/reconstruct/duplicates/migrate-*), roll_entry_index (gui.py:4999), DemoScanner (demo.py:283, 1134, 974) | Exact only while a single writer owns the directory. Two savers in the same second share one directory (CC-01), and migrate-raw can leave scan.tif and scan.json out of step (CC-08). |
| Library index | library/index.json | JSON list of summaries | derived | library.reindex after every save (library.py:310, 753-773), from the FrameWriter thread, the Tk thread in on_delete (gui.py:4012), and tools/library.py; written in place | No production code (only tests/test_library.py:204) | No. It can be torn by concurrent writers, and entries() silently skips unreadable scan.json files. |
| Walk / roll manifests | rolls/<name or YYYY-MM-DD>/survey.json, roll.json | JSON; records rotation/flipped once at start, frames[] rewritten per frame | n/a (metadata) | ScanSession._roll on the scanner worker thread (session.py:1924-1926), in place per frame; tools/scan_roll.py checkpoint (scan_roll.py:326-329) | Resume merge on the worker (session.py:1650-1667), read_survey (gui.py:4690-4694), rolls_on_disk (Tk), scan_roll --approved | No. rotation does not describe prescans filed after a mid-walk turn (CC-02). An in-place rewrite that crashes makes the next resume discard the history (CC-14). |
| Walk prescans and roll frames | rolls/<name>/prescanNN.tif, prescanNN-before.tif, frameNN.tif | TIFF | corrected and oriented (by the live session.rotation/flip at submit time, per-frame approval for frames) | FrameWriter thread (session.py:1074-1083); path fixed on the worker in _file | read_survey un-orients them by the manifest's single rotation (gui.py:4735-4740); walked_prescans | Orientation can vary within one walk while the manifest says one value (CC-02) |
| Operator's approved positions | rolls/<_safe(name) or 'roll'>/approved.json | JSON | n/a (decisions; the only non-derivable roll file) | Tk thread _write_approved (gui.py:2939-2955), in place | read_approved (Tk), roll_exports, tools/scan_roll.py --approved | Not atomic. It can be deleted by a roll-folder delete that is guarded only by this window's busy flag (CC-07). |
| Output-folder copies | <out_dir>/<roll>_frameNN_<dpi>dpi[_ir].(tif\|jpg[+dng]), <out_dir>/prescans/..., <timestamp>_<dpi>dpi... | TIFF or JPEG + DNG | corrected, oriented, optionally mono | FrameWriter thread. The name is chosen by _unclaimed on the worker at submit time (session.py:2068-2071); out_dir/out_format/jpeg_quality are read from Tk-mutated attributes. | external (NegPy) | A before-prescan can overwrite a frame's final prescan copy (CC-06); a mid-roll settings change is unrecorded (CC-12) |
| Save all / Export rolls deliveries | <chosen folder>/<kind>NN_<dpi>dpi..., <roll>_frameNN_<dpi>dpi... | TIFF or JPEG + DNG | corrected at full resolution via library.corrected, or the reduced preview when the pass is not filed | daemon threads save-all / export-rolls (gui.py:3822-3841, 2431-2451), in place | external | Truncated if the window is closed mid-batch (CC-09). An export may pick a walk prescan entry for a frame (CC-11). |
| Window settings, including contact-sheet decisions before commissioning | gui-settings.json (+ gui-settings.json.part) | JSON | n/a | Tk thread _remember -> settings.save: temp + replace, the only atomic write in the codebase (settings.py:82-97) | settings.load at window start | Atomic for one process. Two windows share the same '.part' name and overwrite each other's whole file (last writer wins), losing the other window's sheet decisions. |
| Session shading cache | calibration/shading.npz | npz (derived float64 reference) | derived | ensure_shading -> save_shading on the scanner worker (direct.py:612-615), GUI Calibrate, tools/scan.py, tools/scan_roll.py; in place | Calibrate mode 'reuse' / --reuse (direct.py:600-610) | Not atomic, and shared across processes. 'reuse' loads whatever another process last measured. |

**Second reader's corrections to this table:**

1. approved.json does not sit reliably beside the roll it describes. It is written to rolls/<_safe(Roll field) or 'roll'>/ (gui.py:2940). The roll and survey manifests go to rolls/<raw Roll field or YYYY-MM-DD>/ (session.py:1634-1635). The two differ for an empty or unsanitised name, and every unnamed roll shares rolls/roll/approved.json (CC-A2 in this report's numbering is CC-A1).

2. For walk prescans and roll frames, add prescanNN-before.tif: a corrected TIFF with no library entry and no raw bytes. Nothing else in the codebase keeps the pre-correction pass (CC-A2). Each library entry's scan.json does record the rotation actually applied (meta rotation/flipped, session.py:2110). read_survey ignores it in favour of the manifest's single value.

3. The GUI's roll.json records no library entry ids. tools/scan_roll.py's roll.json does (per-frame 'entry', scan_roll.py:606-609), but uses film.frame '<roll>/NN', which roll_entry_index cannot parse (CC-A3).

4. Library entries are not read mid-write by the GUI's full-resolution, histogram or Save As paths. Result.entry is set only by the 'filed' event, which is emitted after library.save returns (session.py:1356-1359, gui.py:3237-3240). roll_entry_index and entries() see an entry only once scan.json exists, and scan.json is written last. The race described in focus item (3) is therefore limited to the tools/library.py mutators (CC-08) and cross-process id collisions (CC-01).

5. calibration/shading.npz is written through DirectScanner.save_shading (direct.py:563-570), called from ensure_shading (direct.py:612). It is shared by all processes started from the same working directory.

6. The EdgeWatch generation check (tools/frame_edges/watch.py:221-226) correctly discards stale detector answers. session.edge_reader is set once at start-up (gui.py:8186) and never replaced. The only EdgeWatch hazards are the survey and generation replacements from Tk actions (CC-03), not the watcher itself.

## What the operator can do

- Run `tools/library.py list`, `verify` or `reconstruct` while the GUI is open. They only read. An entry whose scan.json is being written at that moment is skipped for that run.
- Use Save as or Save all during a roll. Passes not yet filed are written as the reduced preview, and the message says so.
- Change the output folder, format or JPEG quality mid-roll. Frames already queued keep the old values and later frames use the new ones.
- Rotate or flip passes and use the sheet's rotate-all after a walk or roll has finished (not while it runs).
- Run tools/check_scanner.py on Linux or macOS while the GUI holds the scanner. The claim is refused before any command is sent, and the message says something else holds it.
- Open the contact sheet and the 1:1 view of filed passes at any time. Full-resolution reads only ever see complete entries in this process.

## What the operator should not do

- Run two processes that file into the same library at once. This includes two RPS7200_DEBUG=1 scripts whose flushes overlap, a `--demo --library library` window beside real scanning, or the GUI beside tools/scan.py, scan_roll.py or uniformity.py filing.
- Run `tools/library.py duplicates --delete`, `migrate-raw --write` or `migrate-direction --write` while the GUI or any scan tool is open or filing, and never interrupt migrate-raw --write.
- Rotate, flip or rotate-all while a walk or roll is running.
- Press Ctrl-B, open a roll from an already-open roll browser, or aim-click with 'aim' ticked while the scanner is busy.
- Leave a contact sheet open from one strip and walk another.
- Rename, delete or duplicate a roll folder right after its roll reports done, or while another window or tool uses it.
- Close the window while Save all or Export is still writing.
- Open two GUI windows on one checkout. They share library/, rolls/ and gui-settings.json.
- On Windows, reinstall the driver with Zadig because a second program reported 'Image/WIA driver' while another program might be holding the scanner.

## Mistakes nothing guards against

- Pressing Ctrl-B (the default roll key) during a walk and confirming wipes the running walk's survey, orientations and sheet decisions and starts a new EdgeWatch generation. It queues a second walk whose prescans are never collected, and cancels any pending Stop.
- Double-clicking a roll in a browser that was opened before a walk started replaces the survey mid-walk. The live strip's prescans overwrite the reopened roll's frames of the same numbers in the frame-edge reader.
- Aim-clicking a prescan during a walk or roll queues a sub-frame Move that runs after the job ends, at a position it was not computed for, and clears any pending Stop.
- Pressing Stop, then anything that submits a job before the current frame ends, silently cancels the stop while the Stop button stays greyed.
- Rotating a prescan during a walk makes later prescanNN.tif disagree with survey.json's rotation. The reopened survey then shows and uses those frames turned, so detector offsets and hold references are wrong.
- An old contact sheet is brought forward after a new walk, and 'Scan chosen frames' there scans strip B with strip A's positions and references.
- In a corrected walk with an output folder set, each moved frame's delivered prescan copy is silently replaced by its pre-correction picture, and the log says 'picture N could not be filed: None'.
- Deleting or renaming a roll folder in the few seconds after 'done' makes the writer recreate the old folder with the last frames. Doing it from a second window aborts the other window's running roll.
- Quitting during Save all or Export leaves a truncated file under a final name without asking.
- A second GUI whose scanner open failed shows enabled buttons and an idle light. Calibrate, Scan and Roll then do nothing, with no message.
- `duplicates --delete` removing an entry that the open session shows makes Save as deliver a reduced preview while saying the scan 'is not filed yet'.
- Exporting a walked-then-scanned roll can deliver walk prescans as full-resolution frames. Which entry is chosen depends on directory order.

## Dataflow notes

Threads in one GUI process (tools/gui.py main at 8178-8205):

1. **Tk main thread.** _pump runs every POLL_MS=120 (gui.py:233, 3131-3179).
   - It drains _saves (batch threads), _reads (full-res), _measured (histogram) and session.poll() (worker/writer events).
   - It mutates session.out_dir (1895), out_format and jpeg_quality (1580-1582), and rotation and flip (2074-2075, 3922-3923, 7741-7742), with no lock.
   - It submits jobs through session.submit (session.py:1214-1218), which also clears _stop.
   - It feeds EdgeWatch with add/begin/load/finish (gui.py:3505, 2133, 2655, 3251).

2. **Scanner worker thread "scanner"** (session.py:1211, 1293-1346). It is the only owner of DirectScanner or DemoScanner (the seam is session._open_scanner, gui.py:8187-8203).
   - Each job goes through _dispatch to _prescan, _scan, _roll or _move.
   - Pixels leave through _deliver (1945-1971) as a downscaled Result event, and through _file (2031-2141). _file:
     - fixes the output paths with _unclaimed(out_dir/_out_name) at submit time;
     - reads capture_record() (reference object, ccd_mask bytes, last_raw, layout) and drops the raw bytes if the layout disagrees with the image shape;
     - takes the orientation from the live session.rotation/flip or the per-frame approval;
     - puts one job dict on FrameWriter's bounded queue (depth 2; it blocks the worker, with the device open, if full).
   - _roll also rewrites survey.json/roll.json in place after every frame (1924-1926). It sets last_roll_dir (1637), which the Tk thread reads at 'finished' (gui.py:3258).
   - 'finished' is emitted when the job returns (1326), before the writer has drained.

3. **FrameWriter thread** (session.py:1043-1111).
   - It orients and optionally monos the pixels, then calls export.write for each path (in place).
   - It then calls library.save (library.py:131-311):
     - entry_id from the wall-clock second;
     - an exists-check on scan.json, then mkdir(exist_ok=True);
     - writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin and raw.bin.gz (gzip);
     - hashes scan.tif, calls provenance() (two git subprocesses), writes scan.json last, then reindex (O(N) parse, in-place index.json).
   - on_done then calls _filed, which emits 'log' and 'filed' (seq, path). The Tk thread sets Result.entry from 'filed' (gui.py:3238-3241), so the full-res and export readers only ever get completed entries of this process.
   - At session close the order is scanner.close(), then writer.finish() join (1336-1343). The GUI's quit waits for this (gui.py:3052-3068).

4. **frame-edges thread** (watch.py:92, 199-242). It runs summary/read_frame under a Condition. Results tagged with a stale generation or version are discarded, and the sheet accepts readings only when sheet.generation == progress.generation (gui.py:3093-3095). Results carry no walk identity: remember_arrangement appends any numbered prescan to self.survey while _surveying (gui.py:3503-3505).

5. **Short-lived daemon threads:**
   - _load_full (gui.py:4104-4120) and _measure_histogram (3969-3978) call library.corrected, which reads scan.json, then scan.tif, shading.npz and ccd_mask, and applies shading today.
   - save-all and export-rolls (3822-3841, 2431-2451) call _deliver_one, then export.write. They are killed at interpreter exit.
   - demo-pictures signing (demo.py:288, 1069-1096) globs */scan.json across the source libraries and np.savez to demo/pictures.npz.

Cross-process shared state. There is no lock anywhere; the only atomic write is settings.save.
- **USB device.** Exclusion comes from libusb open/claim (usb_transport.py:444-463). The loser sends nothing, but on Windows it is told to use Zadig.
- **library/.** Written by the GUI FrameWriter, the tools/scan_roll.py FrameWriter, tools/scan.py and tools/uniformity.py (both after close), and every DirectScanner._debug_flush after close (direct.py:694-768, all with FilmNotes(notes=...), so ids differ only by second, dpi and ir). It is mutated by tools/library.py (rmtree, in-place rewrites). The demo reads it as its source.
- **rolls/<name>/.** GUI worker and writer, tools/scan_roll.py, and the GUI roll browser (rmtree/rename/copytree).
- **gui-settings.json.** Every window writes the whole file.
- **calibration/shading.npz.** Every calibrating process writes it; the 'reuse' paths read it.

Exactness: raw.bin.gz is the exact concatenation of READ payloads only while exactly one saver writes the entry directory. The library's promise of exact, re-derivable data therefore depends on no two filers sharing a second, which nothing enforces.
