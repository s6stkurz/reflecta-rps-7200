# Code identity and PROTOCOL_REVISION (gap pass)

Area key `code-identity-and-protocol-revision`. 14 findings: 3 medium, 11 low.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area: code identity and PROTOCOL_REVISION. I read in full rps7200/library.py (provenance, save, load, corrected, decode_raw, reconstruct, migrate_direction, signature, duplicates, verify) and tools/library.py. I also read the capture and filing paths that feed them: DirectScanner.scan meta (direct.py:2760-2804), _debug_capture/_debug_flush/close (direct.py:650-788), ScanSession._run/_file and FrameWriter (session.py:1013-1103, 1293-1346, 2031-2141), tools/scan.py and tools/scan_roll.py filing, the demo's meta and _decode, tiff.write, and the hold/nudge law (direct.py:2873-3168, framing.py:1151, 1172-1215, protocol.py:15-40, 255-296). I walked `git show` on every commit since 0f7c21d that touches rps7200/direct.py, protocol.py or usb_transport.py (2dcf361, 2b73430, e3229ac, df7d4e6, d723896, 46eddc1, e942a16, 33ad07b, e43c607, 2b394b0, a0de517, 30bad8e, f056bfe, 2ce1167, 622aa5a), plus the session and tool commits after the revision-6 bump. The clone is shallow, and 0f7c21d is a graft boundary. Nothing was modified and no hardware was touched.


Answers to the six questions:

(1) Confirmed. provenance() runs `git rev-parse --short HEAD` and `git status --porcelain` in the source directory at the moment library.save builds the record:
- on the FrameWriter thread for the GUI and scan_roll;
- after close() for debug entries;
- after the with-block for tools/scan.py.
The code in memory was imported at launch, so a branch switch in the shared tree gets the next entry the new HEAD. Worse, rps7200.library/tiff (at the debug flush) and rps7200.uniformity (at the first hold measurement) are imported lazily. A process can therefore run a mix of two branches that no commit describes.

(2) driver_dirty is bool(stdout) of `git status --porcelain`. That is true for any untracked or doc change. No diff, diff hash, branch or full sha is stored, so a dirty entry cannot be tied to its code.

(3) Without git, or when git refuses (dubious ownership, timeout, a ZIP checkout), driver_commit is None and driver_dirty is **False**, not None. The record claims a clean tree.

(4) Each save runs two git subprocesses with 10 s timeouts. `git status` takes the optional index.lock, and a timeout SIGKILLs git. In the GUI's single Scan the device is open and idle during this.

(5) Command bytes and sequences changed without a bump:
- d723896 changed the SLIDE param byte for 25.6% of requested distances, and the hold deadband, at revision 5. The project's own step-calibration plan says that change moves the revision.
- 30bad8e and f056bfe changed the sequence (roll-end rule; calibrate-first vs calibrate-last in scan_roll) and edited the revision-6 note in place, so '6' names two different orders.
- 2ce1167 and 2dcf361 changed which SLIDE moves a held or aimed frame gets.
- 622aa5a genuinely sent nothing new.
(6) No. reconstruct, corrected, decode_raw, migrate-raw and migrate-direction never read protocol_revision or provenance. The only consumer of protocol_revision is signature() (duplicates/--delete), and nothing reads driver_commit. The image sha256 hashes the TIFF container, which differs between the tifffile and built-in writers. verify does not break when the writer changes, because only migrate rewrites scan.tif and it recomputes the hash. One migrate-direction failure path does leave the hash permanently stale.

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [CIP-1](#code-identity-and-protocol-revision-cip-1) | medium | data-integrity | Provenance names the working tree at filing time, not the code the process imported; lazy imports let one process run two branches | [P09](../problems/P09-record-missing-parameters.md) |
| [CIP-2](#code-identity-and-protocol-revision-cip-2) | medium | data-integrity | driver_dirty reports 'clean' when git is absent or refuses, is true for any untracked file, and no diff, branch or full sha is kept | [P09](../problems/P09-record-missing-parameters.md) |
| [CIP-5](#code-identity-and-protocol-revision-cip-5) | medium | data-integrity | The SLIDE bytes that placed a frame are never recorded; spent_mm and asked_mm are derived with the unversioned law, so the transport cannot be re-evaluated | [P09](../problems/P09-record-missing-parameters.md) |
| [CIP-3](#code-identity-and-protocol-revision-cip-3) | low | concurrency | Two git subprocesses inside every save: up to 20 s on the writer thread with the device open, and `git status` takes the optional index.lock in the shared tree | -- |
| [CIP-4](#code-identity-and-protocol-revision-cip-4) | low | data-integrity | PROTOCOL_REVISION was not bumped when the bytes or sequence sent changed, and revision 6's definition was rewritten in place after entries were filed under it | -- |
| [CIP-6](#code-identity-and-protocol-revision-cip-6) | low | design | The recorded code identity is never consulted: reconstruct, corrected and migrate ignore protocol_revision and provenance, so their verdicts cannot be attributed | -- |
| [CIP-7](#code-identity-and-protocol-revision-cip-7) | low | data-integrity | image.sha256 hashes the TIFF container, not the pixels; migrate-direction can leave it permanently stale | -- |
| [CIP-8](#code-identity-and-protocol-revision-cip-8) | low | demo-divergence | Demo entries carry no protocol_revision, and raw bytes copied from a real entry are re-filed under the demo's provenance with no link to their source | -- |
| [CIP-9](#code-identity-and-protocol-revision-cip-9) | low | doc-mismatch | The docs misstate where PROTOCOL_REVISION lives, when it moves, and how far param 1 travels | -- |
| [CIP-10](#code-identity-and-protocol-revision-cip-10) | low | test-gap | No test ties the bytes sent to PROTOCOL_REVISION, and provenance's failure modes are untested | -- |
| [CIP-11](#code-identity-and-protocol-revision-cip-11) | low | data-integrity | Roll manifests, approved.json, delivered TIFFs and scan.py sidecars carry no code identity; a resumed roll merges runs from different code | -- |
| [CIP-A1](#code-identity-and-protocol-revision-cip-a1) | low | data-integrity | The INQUIRY (vendor, product, firmware, CCD geometry) is passed to every library.save and silently discarded | -- |
| [CIP-A2](#code-identity-and-protocol-revision-cip-a2) | low | bug | tools/uniformity.py prints the pipeline identity from a key provenance() never sets, so it always says 'unknown' | -- |
| [CIP-A3](#code-identity-and-protocol-revision-cip-a3) | low | data-integrity | provenance() runs git in the package's parent directory, so a non-editable install records an unrelated repository's commit | -- |

## Findings in full

<a id="code-identity-and-protocol-revision-cip-1"></a>

### CIP-1 -- Provenance names the working tree at filing time, not the code the process imported; lazy imports let one process run two branches

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:67-101`, `rps7200/library.py:306`, `rps7200/session.py:1090`, `rps7200/session.py:1311`, `rps7200/direct.py:694-708`, `rps7200/direct.py:777-788`, `rps7200/framing.py:1039`, `tools/scan.py:237-246`, `rps7200/session.py:47`

driver_commit/driver_dirty describe the working tree when the record is built: FrameWriter time in the window, after close() for RPS7200_DEBUG entries, after the with-block for tools/scan.py. They do not describe the code the process imported. In this shared tree a checkout in another terminal relabels every later entry from a still-running process. Modules imported lazily can come from a different branch than the rest of the process: rps7200.uniformity via framing.py:1039 at the first held frame, and library/tiff in an ad-hoc DirectScanner(debug=True) script that never imported session. The GUI imports library/tiff at startup, so its record format is process-accurate.

**Evidence (from the code):**

```text
library.py:79-84 `out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace", cwd=Path(__file__).resolve().parent.parent,)`; library.py:98-99 `"driver_commit": git("rev-parse", "--short", "HEAD"), "driver_dirty": bool(git("status", "--porcelain")),`; library.py:306 `"provenance": provenance(),` (evaluated inside save()). The GUI calls save() from FrameWriter._write on its own thread (session.py:1090 `entry = library.save(`), created once per window session (session.py:1311 `self._writer = FrameWriter(on_done=self._filed)`). Debug entries: close() at direct.py:785-788 `self.t.close()` ... `self._debug_flush()`, and _debug_flush imports lazily at direct.py:705-706 `from . import library` / `from .library import FilmNotes`. The hold loop's correlation is imported lazily at framing.py:1039 `from .uniformity import register` (no module-level import of rps7200.uniformity exists in rps7200/, tools/gui.py or tools/scan_roll.py). By contrast `versions` in the same record comes from the already-imported modules and is process-accurate.
```

**Failure scenario:** 1. Stefan opens the window on main at commit A and walks a roll.
2. Meanwhile a Claude session in the same working tree runs `git checkout -b feat/x`, commits B, then `git checkout experiment/y` (commit C) to try something.
3. Every frame FrameWriter files after that records driver_commit C and driver_dirty per C's tree. The first approved frame imports C's rps7200/uniformity.py, so its hold moves were decided by A's direct.py and framing.py on top of C's register().
4. Months later a registration regression is bisected by driver_commit, and it blames C, which never scanned anything.
Debug variant: a 90-minute DirectScanner(debug=True) script is running while someone checks out a branch whose library.py imports a name A's already-loaded direct.py lacks. At close(), `from . import library` raises. _debug_flush logs 'library unavailable (...); N lost' and returns, and every pass of the run is unfiled.

**Fix:** Capture identity once, when the rps7200 package is imported, and reuse it:
- the full 40-character sha;
- the branch;
- the dirty state;
- a git-independent sha256 over the source files actually loaded (every sys.modules entry under the repo root, tools/ included).
Store it on each spooled debug item and each FrameWriter job at capture time. At save time, also record the tree's current identity and flag a mismatch ('code changed on disk while this process ran'). Import rps7200.library, rps7200.tiff and rps7200.uniformity eagerly in direct.py/framing.py so one process can never mix branches. Better still, refuse to file when the loaded-source hash no longer matches the files on disk.

<details><summary>Second reader's check</summary>

Confirmed in code. provenance() runs `git rev-parse --short HEAD` and `git status --porcelain` with cwd=the repo root (library.py:79-84, 97-99), and it is evaluated inside save() (library.py:306). That is filing time on every path:
- FrameWriter._write -> library.save (session.py:1090), on a writer created once per window session (session.py:1311).
- DirectScanner.close() -> _debug_flush after t.close() (direct.py:785-788, 694-708).
- tools/scan.py saves after the with-block (scan.py:237-246).
framing.py:1039 imports rps7200.uniformity lazily, and nothing in rps7200/, tools/gui.py or tools/scan_roll.py imports it at module level, so a branch switch mid-session can mix modules.

Two parts of the finding are overstated.
- The GUI imports library (and through it tiff) at startup (session.py:47 `from . import export, library, preview`). So for the window, the record format, whitelist and TIFF writer are process-accurate. Only lazily imported modules (uniformity, and library.py:537-538's migrate imports) can come from another branch.
- The debug-flush 'from . import library raises' variant needs a checked-out library.py that imports a name the loaded direct.py lacks. That is possible but speculative.
The pixels and raw bytes are unaffected; the harm is misattribution. So medium, not high.

</details>

<a id="code-identity-and-protocol-revision-cip-2"></a>

### CIP-2 -- driver_dirty reports 'clean' when git is absent or refuses, is true for any untracked file, and no diff, branch or full sha is kept

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/library.py:76-87`, `rps7200/library.py:97-101`

The code has three distinct defects.

(a) When git is not on PATH (FileNotFoundError is an OSError), the repo is not a git checkout (a ZIP download), git exits 128 on 'detected dubious ownership' (a repo on an external or other-owner drive, common on Windows), or the 10 s timeout fires, git() returns None. driver_commit is then None and driver_dirty is bool(None) == **False**. 'Unknown' is recorded as 'clean'.

(b) `git status --porcelain` lists untracked files and changes to any tracked file (TODO.md, docs, research/). One throwaway probe script left in the tree, which CLAUDE.md says Claude writes routinely, marks every entry dirty, so the flag stops meaning 'the driver was modified'.

(c) When it is dirty, nothing records what differed:
- no `git diff`;
- no diff hash;
- no branch;
- only a --short sha.
Experiment branches (CLAUDE.md: 'might not work at all') are later deleted, their commits become unreachable and are garbage-collected, and the recorded short sha then resolves to nothing. A dirty entry can never be tied to the code that produced it.

**Evidence (from the code):**

```text
library.py:85 `return out.stdout.strip() or None if out.returncode == 0 else None`; library.py:86-87 `except (OSError, subprocess.SubprocessError): return None`; library.py:98-99 `"driver_commit": git("rev-parse", "--short", "HEAD"), "driver_dirty": bool(git("status", "--porcelain")),`
```

**Failure scenario:** First case: on Windows the window is launched from a shell where git is not on PATH. Every entry records {"driver_commit": null, "driver_dirty": false}, which reads as a clean build.
Second case:
1. For a test roll someone edits COMMAND_UNITS or MAX_CORRECTION_PARAM locally without committing.
2. They scan a roll.
3. They run `git checkout -- rps7200/protocol.py`.
The entries say commit X, dirty True, but X plus 'dirty' also describes every entry filed while an untracked scratch script sat in the tree. What SLIDE law placed those frames is unrecoverable.

**Fix:** Make the dirty state three-valued (null when unknown). Separate tracked modifications (`git status --porcelain=v2 --untracked-files=no --branch`, one call that also yields the full HEAD oid and the branch) from the untracked count. Store a sha256 of `git diff HEAD -- rps7200 tools` and keep the diff itself, compressed and content-addressed under library/_code/<hash>.diff.gz, so an entry can be rebuilt against its exact code. Record the full sha and the branch name. Also store the git-independent source hash from CIP-1, so the record means something when git is unavailable.

<details><summary>Second reader's check</summary>

library.py:85-87 returns None on a non-zero exit or on OSError/SubprocessError (git missing, timeout, exit 128 for dubious ownership). library.py:99 `bool(git("status", "--porcelain"))` then turns None into False, so 'unknown' is recorded as clean. `git status --porcelain` includes untracked files by default. .gitignore does not cover many things operators routinely produce:
- a tools/scan.py `--out foo.jpg` and its `foo.json` sidecar (only `scan.json` and `*.tif` are ignored);
- a library filed under any folder other than library/ or 'library 2/', whose raw.bin.gz, shading.npz and ccd_mask.bin are not ignored.
So the flag flips to dirty for reasons unrelated to the driver. Only `--short` HEAD is kept; there is no branch, no diff and no diff hash.

</details>

<a id="code-identity-and-protocol-revision-cip-5"></a>

### CIP-5 -- The SLIDE bytes that placed a frame are never recorded; spent_mm and asked_mm are derived with the unversioned law, so the transport cannot be re-evaluated

**Severity** medium · **Category** data-integrity · **Verdict** confirmed · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `rps7200/direct.py:2873-2905`, `rps7200/direct.py:3138-3168`, `rps7200/direct.py:3549-3551`, `rps7200/direct.py:3655`, `rps7200/library.py:280`, `rps7200/session.py:1584`, `tools/scan_roll.py:405-413`

The owner's requirement is that everything can be recalculated with newer code. For the transport, what would have to be recalculated is which commands moved the film. The library keeps the target, the number of moves, spent_mm, clamped and the correlation history. It never keeps the (action, param, value) bytes sent.

spent_mm is not a measurement. It is param x STEP_MM + moves x OVERHEAD_MM, computed with whichever OVERHEAD_MM the process had: 0.1662 or 0.1945 per command across d723896, and revision 5 does not tell them apart. With two or three moves summed, the individual params cannot be recovered at all.

Several moves are not recorded anywhere durable:
- the window's Move and nudge jobs;
- scan_roll's --nudge;
- seek's whole-frame moves.
They exist only in logs, although every later pixel depends on them. The dry-run aim path is the only one that records a param (`would_send`).

**Evidence (from the code):**

```text
direct.py:2897-2903 `asked = self.nudge(want)` / `out["clamped"] = out["clamped"] or bool(asked.get("clamped"))` / `delivered_mm = asked["asked_mm"] * (1 if want > 0 else -1)` / `spent += abs(delivered_mm)` / `out["spent_mm"] = round(spent, 4)`. nudge returns `{"param": param, "forward": forward, "asked_mm": ...}` (direct.py:3165-3168), but `param` is never copied into `out`. `asked = self.STEP_MM * param + self.OVERHEAD_MM` (direct.py:3151). The only per-frame record is `marks["approved"] = {k: v for k, v in fix.items() if k != "prescan"}` -> `meta["registration"] = marks` -> library.py:280 `"registration": meta.get("registration")`. The GUI's Move job calls `out = self._scanner.nudge(step)` (session.py:1584), and scan_roll --nudge only prints `sent`.
```

**Failure scenario:** After the 1.572 -> 1.84 change, an analyst refits the ramp from stored rolls using registration.approved.spent_mm against the measured history. Entries from the same revision mix two formulas for spent_mm and nothing says which, so the fit reproduces whichever constant was in the code. A param-1 move reads 0.2719 in one entry and 0.3002 in another for identical bytes on the wire.

**Fix:** Record in the hold/aim history each command as sent: {action, param, value, monotonic t}, and the READ STATE after it. Store the law constants the decision used (MM_PER_UNIT, COMMAND_UNITS, HOLD_TOLERANCE_MM, MAX_CORRECTION_PARAM, CONFIDENCE_FLOOR) in the registration record. Better still, keep a per-session command log (opcode plus payload hex, with timestamps), spooled with each entry, so every SLIDE, READ STATE and MODE SELECT that preceded a pass travels with it.

<details><summary>Second reader's check</summary>

_hold_to_approved gets nudge()'s dict, which includes `param`, but copies only clamped and asked_mm (direct.py:2897-2903). `spent_mm` accumulates asked_mm, which is `STEP_MM*param + OVERHEAD_MM` (direct.py:3151), not a measurement. marks['approved'] keeps everything except the prescan (direct.py:3549-3550) and reaches the record via meta['registration'] (3655) and library.py:280. The params sent are never stored.

Other moves leave no durable record either:
- the window's Move job sums asked_mm only (session.py:1584-1585);
- scan_roll --nudge only prints `sent` (scan_roll.py:409-413), and the manifest `settings` do not include `nudge` (scan_roll.py:305-321).
Only the dry-run aim records would_send (direct.py:3021).

</details>

<a id="code-identity-and-protocol-revision-cip-3"></a>

### CIP-3 -- Two git subprocesses inside every save: up to 20 s on the writer thread with the device open, and `git status` takes the optional index.lock in the shared tree

**Severity** low · **Category** concurrency · **Verdict** confirmed

**Where:** `rps7200/library.py:79-84`, `rps7200/library.py:306`, `rps7200/session.py:1085-1100`, `rps7200/session.py:1311-1330`

Every entry spawns `git rev-parse` and `git status`, each allowed 10 s. There are three consequences.

(1) In the window's single Scan and Prescan path the device is open and idle while FrameWriter files. This adds up to 20 s of git to the known open-and-idle window. A 38-frame debug flush spawns 76 git processes.

(2) `git status` without `--no-optional-locks` refreshes the index and rewrites .git/index under .git/index.lock (git-status(1), 'BACKGROUND REFRESH'). A `git commit`, `git checkout` or `git add` run at the same moment in this shared tree can fail with 'Unable to create .git/index.lock: File exists'. So filing a frame has a side effect on the repository.

(3) On timeout, subprocess.run kills git with SIGKILL (TerminateProcess on Windows). That cannot run git's lockfile cleanup, so a stale .git/index.lock blocks every later git write until someone deletes it by hand.

The cost is paid per entry although the answer is per process.

**Evidence (from the code):**

```text
library.py:79-84 `subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, ...)`, called twice per provenance(), and provenance() is called by every library.save (library.py:306). FrameWriter._write runs save on its thread while ScanSession's worker keeps the device open between jobs (session.py:1311-1330; close happens only in the finally at session.py:1333-1339).
```

**Failure scenario:** On Windows, antivirus or a network drive makes `git status` slow. The GUI files a scan and git status passes 10 s while holding index.lock. It is killed and leaves .git/index.lock behind. Stefan's next `git commit` refuses with 'Another git process seems to be running', and the scan sat open and idle for 20 s meanwhile. A milder version happens with no timeout at all: a parallel Claude session's `git commit` collides with a roll frame being filed.

**Fix:** Compute provenance once per process (CIP-1) and cache it. Run git with `--no-optional-locks`, or GIT_OPTIONAL_LOCKS=0, and with a short timeout. Never run it on the filing path while the device is open.

<details><summary>Second reader's check</summary>

There are two subprocess.run calls per save (library.py:79-84, 98-99), each with timeout=10. FrameWriter files on its own thread while the session worker keeps the device open between jobs; close happens only in the finally at session.py:1333-1339. So in the window the git time falls within the open-and-idle window.

`git status` without --no-optional-locks does an opportunistic index refresh under index.lock. On timeout, subprocess.run kills the child (SIGKILL on POSIX, TerminateProcess on Windows), which can leave a stale lock. Both are real but narrow. The debug flush and tools/scan.py run after close, so there the device is not affected.

</details>

<a id="code-identity-and-protocol-revision-cip-4"></a>

### CIP-4 -- PROTOCOL_REVISION was not bumped when the bytes or sequence sent changed, and revision 6's definition was rewritten in place after entries were filed under it

**Severity** low · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/protocol.py:15-40`, `rps7200/protocol.py:260-279`, `rps7200/direct.py:3094-3136`, `rps7200/framing.py:1200-1202`, `tools/scan_roll.py:369-446`, `rps7200/library.py:695`

PROTOCOL_REVISION was not bumped for d723896, whose ramp change alters param_for_mm for about 26% of distances. Within 22 minutes the constant was stamped 5 with both ramp values, but only on the feature branch: main received df7d4e6 and d723896 together in b14ecdb. Revision 6 was stamped for about 2h19m with scan_roll calibrating first (7463990 to f056bfe), again only on the branch. The revision-6 note was rewritten in place twice (30bad8e, f056bfe), so the history no longer describes what those runs sent. Host-side decisions that choose SLIDE params changed within revision 6 on main without any marker (2ce1167 frame-edge proposer), because CLAUDE.md exempts host-side work. The policy was also applied inconsistently: 33ad07b declined a bump that 2b394b0 then made for a similar sequence change.

**Evidence (from the code):**

```text
d723896 (2026-09-22 15:29, revision stays 5): `-MM_PER_COMMAND = 0.1662` -> `+COMMAND_UNITS = 1.84` `+MM_PER_COMMAND = MM_PER_UNIT * COMMAND_UNITS`, and framing `-HOLD_TOLERANCE_MM = 0.2719` -> `+HOLD_TOLERANCE_MM = 0.3002`. The byte on the wire is direct.py:3134-3136 `n = round((abs(millimetres) - DirectScanner.OVERHEAD_MM) / DirectScanner.STEP_MM)` with OVERHEAD_MM = MM_PER_COMMAND (direct.py:3097), and the deadband is framing.py:1201 `if abs(residual) < HOLD_TOLERANCE_MM: return None, "held"`. Evaluating that formula with both constants over 0-9.5 mm gives a different param for 25.6% of distances, e.g. 0.748 mm (7.08 units): `SLIDE 00 06 00 04` before, `SLIDE 00 05 00 04` after. docs/step-calibration-plan.md:207-208: '`STEP` and `OVERHEAD` are both low. Correcting them changes what `param_for_mm` returns, so `PROTOCOL_REVISION` moves.' 30bad8e edited the revision-6 note (protocol.py:26-27) to add 'a roll whose counter reads past its end or behind its count sends nothing more', a new sequence, without a bump. f056bfe replaced '...And the roll tool calibrates before any of that rather than after it.' with (protocol.py:27-29) 'still calibrates last -- after the rewind, the seek and any `--nudge`'. At 2b394b0, tools/scan_roll.py called `calibrate(s, args)` (line 371) before `rewind` (373) and `seek` (388). At HEAD it calls `s.wait_warm()` (369), then rewind, seek and nudge, then `calibrate(s, args)`. Both are stamped 6. 2ce1167 swapped `framing.propose_offsets` for `frame_edges.propose_centred` in hold_from_walk and passes `edge_reader=frame_edges.walk_reader`. 2dcf361 (revision 4) made measure_shift_mm accept reversed prescans (`flipped = register(reference, now[::-1], ...)`, and a gate in mm). Both change which SLIDE moves a held frame gets. 33ad07b explicitly declined a bump for a sequence change, and 2b394b0 then bumped for it: the policy was applied inconsistently within a day.
```

**Failure scenario:** First case:
1. A roll is scanned under revision 5 before d723896 and re-run under the same roll name after it. The frame notes are '<name>-NN', so film.frame matches.
2. signature() is identical for each pair, so `tools/library.py duplicates --delete` removes one of each pair as 'the same scan of the same picture'.
3. The two frames were placed with different SLIDE params and deadbands. The comparison that showed whether 1.84 placed frames better than 1.572 is gone.
Second case: a reader of protocol.py trusts 'revision 6 = calibrate last' for a scan_roll run filed at 08:00 on 2026-09-23, which in fact calibrated first.

**Fix:** Bump to 7 now, with a note that says:
- revision 5 spans the 1.572 -> 1.84 ramp change of d723896;
- revision 6 covers the calibrate-first and calibrate-last scan_roll orders;
- revision 6 predates and postdates the behind-count roll end.
Make revision notes append-only: never edit a published revision's text. Say explicitly, in CLAUDE.md and protocol.py, that host-side decisions choosing command parameters (param_for_mm constants, HOLD_TOLERANCE_MM, MAX_CORRECTION_PARAM, the detector or gate proposing targets) count as sequence changes. Alternatively, add a separate recorded `transport_revision` for them. Enforce all of this with the golden-trace test in CIP-10.

<details><summary>Second reader's check</summary>

The facts check out.
- df7d4e6 (15:07) bumped to 5.
- d723896 (15:29, same day) changed MM_PER_COMMAND 0.1662 -> 0.1945 and HOLD_TOLERANCE_MM 0.2719 -> 0.3002 without a bump. For 0.748 mm, param_for_mm goes (0.748-0.1662)/0.1057 = 5.50 -> 6, versus (0.748-0.1945)/0.1057 = 5.24 -> 5.
- 7463990 (07:17:02) moved calibrate first, then 2b394b0 (07:17:36) bumped to 6 with a note saying 'calibrates before any of that'.
- f056bfe (09:36) restored calibrate-last and rewrote the note in place. At HEAD, scan_roll.py:369 calls wait_warm and :446 calls calibrate; at 2b394b0, calibrate was at :371, before rewind at :373.
- 30bad8e edited the revision-6 note in place to add 'or behind its count'.
- 33ad07b explicitly kept 5 for a sequence change, and 2b394b0 then bumped for a similar one.

The finding overstates the reach, though. On main, df7d4e6 and d723896 arrived in the same merge (b14ecdb), and 7463990 and f056bfe both arrived in dcb4e8f. So main never ran revision 5 with the 0.1662 ramp or revision 6 with calibrate-first. The ambiguity exists only for scans run from the feature branch in a 22-minute window and a 2h19m window. 30bad8e changes only a path its own message says has never been seen on the hardware. 622aa5a is a pure refactor: byte14_for returns the same 0x21/0x10 and carriage_state reuses last_state, so no command was added.

What remains real:
- the in-place edits to published revision notes;
- host-side changes that pick SLIDE bytes without a marker, notably 2ce1167 (detector swap) inside revision 6 on main between dcb4e8f and 2ec7aa2.
CLAUDE.md explicitly exempts host-side work, so this is a policy gap rather than a violation.

</details>

<a id="code-identity-and-protocol-revision-cip-6"></a>

### CIP-6 -- The recorded code identity is never consulted: reconstruct, corrected and migrate ignore protocol_revision and provenance, so their verdicts cannot be attributed

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/library.py:439-515`, `rps7200/library.py:478-491`, `rps7200/library.py:338-392`, `rps7200/library.py:518-636`, `rps7200/library.py:639-698`, `tools/library.py:92-113`, `tests/test_library.py:89-90`

reconstruct compares today's code with a scan.tif written by some earlier code and prints 'identical' or 'decode CHANGED'. It never says which code wrote the stored image and never uses the recorded revision or commit. Format generations are instead guessed from which keys are present (read_direction, exposure_metered, fast_infrared, numbering), with no schema_version.

For legacy entries it re-applies today's apply_shading to today's decode and compares against pixels corrected by the capture-time shading code. Any change to shading.py therefore shows up as 'decode CHANGED' on every legacy entry and fails `make reconstruct` (exit 1). A correction change is reported as a decode regression: the false-alarm pattern CLAUDE.md describes (26 false alarms), from a new source. corrected() returns today's correction with no note of what the entry was originally corrected with. migrate-raw's 'mislabelled' verdict cannot check which commit filed the entry. The one attribution the test's comment promises is never made anywhere.

**Evidence (from the code):**

```text
The only reader of the revision anywhere in rps7200/ and tools/ is library.py:695 `scan.get("protocol_revision"),` inside signature(). No code reads `driver_commit` or `driver_dirty`: grep finds only the writer and tests/test_library.py:90 `assert "driver_commit" in record["provenance"]`, under the comment 'which build produced it, so a decode change can be attributed'. reconstruct for legacy entries: library.py:480-491 `if "shading" in applied: ... image, _ = apply_shading(image, reference, mask)`, then library.py:512-515 `f"decode CHANGED: {differing} of {image.size} samples differ"`. tools/library.py:113 `return 1 if changed else 0`.
```

**Failure scenario:** A shading improvement lands. `make reconstruct` prints '! <entry>: decode CHANGED: 38201133 of 38400000 samples differ' for every entry with corrections_applied ['shading'] and exits 1, although the INDEX decode is untouched. Nothing in the output shows that these entries came from a different commit or revision than the ones reporting 'identical'. Meanwhile `migrate-raw --write` would treat the same entries as candidates to rewrite.

**Fix:** Print each entry's recorded revision, commit, dirty state and created time next to every reconstruct, verify and migrate verdict, and group the summary by them. Add `schema_version` to scan.json. For legacy corrected entries, report 'shading code changed since filing' separately from a decode change: compare decode_raw against a fresh decode first, then the correction. Let reconstruct take `--since <commit|revision>`.

<details><summary>Second reader's check</summary>

grep across rps7200/, tools/ and tests/ finds these readers:
- protocol_revision: only signature() (library.py:695);
- driver_commit and driver_dirty: nothing except tests/test_library.py:90's key-exists assertion;
- tools/uniformity.py:377 looks up a non-existent 'commit' key (see the additional finding).
For entries with corrections_applied=['shading'], reconstruct re-applies today's apply_shading (library.py:479-491) and reports 'decode CHANGED' (512-515). tools/library.py:103-113 then counts it and exits 1. So a shading-code change reads as a decode regression on legacy entries.

The impact is limited to legacy entries not yet converted by migrate-raw, and to diagnosis rather than data. Hence low. The attribution gap itself is real.

</details>

<a id="code-identity-and-protocol-revision-cip-7"></a>

### CIP-7 -- image.sha256 hashes the TIFF container, not the pixels; migrate-direction can leave it permanently stale

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:229`, `rps7200/library.py:563-576`, `rps7200/library.py:633-635`, `rps7200/library.py:787`, `rps7200/tiff.py:128-160`, `tools/library.py:222-229`

Changing the writer does not break verify. Nothing rewrites scan.tif except migrate-raw (tools/library.py:198-203) and migrate-direction, and both recompute the hash. The hash still cannot identify pixels, though: the same array filed with and without tifffile, or with different tifffile or zlib versions, gives different digests. There is no pixel hash, so nothing can confirm two entries hold identical pixels without decoding both.

There is also a real gap. migrate-direction rewrites scan.tif upright, then may raise in the prescan step (an unreadable prescan.tif gives a ValueError from tiff.read). The per-entry except swallows it, so the json keeps the old hash and no read_direction. A rerun sees the stored image equal to the decode and takes the first branch. That branch records the direction but never refreshes the hash, so verify reports a checksum mismatch forever.

**Evidence (from the code):**

```text
library.py:229 `"sha256": _sha256(path / "scan.tif"),` is computed over the file bytes. tiff.write: the tifffile path sets `kwargs["compression"] = "zlib"` / `kwargs["predictor"] = True` (tiff.py:147-151); the built-in writer is uncompressed. migrate_direction, first branch (library.py:565-567): `if plain and np.array_equal(stored, decoded): done.append(...); upright = decoded`, with no sha refresh. The rewrite branch writes scan.tif at library.py:573-576, but scan.json only at the end, library.py:633 `if done and write:`. tools/library.py:226 `except (OSError, ValueError, KeyError) as exc:` swallows a failure in between.
```

**Failure scenario:** 1. An entry was read bottom-up and stored that way, and its prescan.tif is truncated.
2. `migrate-direction --write` turns scan.tif upright, then prints '! <entry>: ...'.
3. It is rerun after the prescan is fixed or removed.
4. From then on `make verify` prints '<entry>: scan.tif does not match its checksum'. That is a permanent red line of the kind TODO.md says makes verify unread.

**Fix:** Add `pixels_sha256` over shape, dtype and C-order bytes of the decoded array, which is writer-independent, and have verify and reconstruct use it. Refresh image.sha256 in every migrate branch that finds the stored file differs from what the record says. Write scan.tif to a temporary file and rename it, and write scan.json atomically in the same step.

<details><summary>Second reader's check</summary>

image.sha256 is _sha256 of the scan.tif file (library.py:229). tiff.write uses zlib+predictor through tifffile (tiff.py:147-151) and uncompressed output otherwise, so the digest depends on the writer.

The stale-hash path is real:
1. The rewrite branch writes scan.tif and updates the in-memory sha (library.py:573-576).
2. The prescan step can raise (tiff.read of a bad prescan.tif, at library.py:594) before scan.json is written (633-635).
3. tools/library.py:226 swallows the error, so scan.json is never written.
4. On rerun, read_direction is still absent (library.py:545), stored now equals decoded, and the first branch (565-567) records the direction without refreshing the sha.
5. verify then reports a checksum mismatch permanently.
prescan.tif has no checksum at all, so its rewrite is unverifiable.

</details>

<a id="code-identity-and-protocol-revision-cip-8"></a>

### CIP-8 -- Demo entries carry no protocol_revision, and raw bytes copied from a real entry are re-filed under the demo's provenance with no link to their source

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:576-596`, `rps7200/demo.py:955-1021`, `rps7200/direct.py:2760-2764`

CLAUDE.md says a value the stand-in needs is taken from DirectScanner, never retyped or dropped. The demo drops the revision stamp, so demo entries file `protocol_revision: null`.

The raw.bin.gz a demo entry files is a byte-identical copy of a real entry's pass, captured under that entry's revision and commit. It is re-filed with a new created time, the demo process's provenance and no field naming the source entry. The library can then hold the same scanner bytes twice with contradictory identity. (This complements the known finding that the 'demo' flag is dropped by the whitelist.)

**Evidence (from the code):**

```text
DirectScanner meta includes `"protocol_revision": PROTOCOL_REVISION,` (direct.py:2764). DemoScanner.scan's meta (demo.py:576-591) has resolution_dpi, channels, channel_order, film, depth, width, height, shading, exposure_scale, duration_s and `"demo": True`, with no protocol_revision. _decode returns `{"reference": reference, "ccd_mask": mask, "raw": raw, "raw_layout": layout}` from `raw = library.read_raw(path)` of a stored entry.
```

**Failure scenario:** A demo run is pointed at the real library with --library, which is possible. Its entries' raw bytes are identical to real entries' bytes, but they claim revision null and today's commit. A later analysis that selects by revision or commit either drops real data or counts the same pass twice under two identities.

**Fix:** Stamp the source entry's recorded protocol_revision and provenance, plus a `demo_source` entry id, and whitelist `demo`/`demo_source` in library.save. If a revision is wanted on synthetic passes, take PROTOCOL_REVISION from the driver rather than omitting it.

<details><summary>Second reader's check</summary>

DemoScanner.scan's meta (demo.py:576-591) has no protocol_revision, while DirectScanner stamps PROTOCOL_REVISION (direct.py:2764). _decode returns the stored entry's raw bytes (demo.py:1021) for re-filing. `--library` is honoured with --demo (gui.py:8120-8122, 8179), so a demo can file into the real library. 'demo' is not in save's whitelist (library.py:238-259), and no source-entry field exists.

</details>

<a id="code-identity-and-protocol-revision-cip-9"></a>

### CIP-9 -- The docs misstate where PROTOCOL_REVISION lives, when it moves, and how far param 1 travels

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `CLAUDE.md:450-451`, `TODO.md:156`, `docs/byte14-plan.md:295`, `docs/step-calibration-plan.md:207-208`, `rps7200/protocol.py:15-29`, `rps7200/protocol.py:286-291`

The policy documents point at the wrong file. The CLAUDE.md exemption for host-side work does not fit host-side decisions that choose SLIDE bytes (CIP-4). The step-calibration plan's own bump rule was not followed. The revision history in protocol.py no longer describes what entries stamped 6 in the morning of 2026-09-23 actually sent. The units_for_param docstring carries the retired 1.572 ramp.

**Evidence (from the code):**

```text
CLAUDE.md:450 'Bump `PROTOCOL_REVISION` in `rps7200/direct.py` when the commands sent to the device change — not for host-side work, which is re-runnable from raw bytes.' The constant is defined at rps7200/protocol.py:40 `PROTOCOL_REVISION = 6`, and direct.py only imports it (direct.py:91). TODO.md:156 and docs/byte14-plan.md:295 repeat 'PROTOCOL_REVISION in rps7200/direct.py'. docs/step-calibration-plan.md:207-208 says correcting OVERHEAD 'changes what param_for_mm returns, so PROTOCOL_REVISION moves', but d723896 corrected it and the revision stayed 5. protocol.py:290 says '`param 1` travels 2.57', while COMMAND_UNITS = 1.84 (protocol.py:276) makes it 2.84. The revision-6 note (protocol.py:15-29) was rewritten by 30bad8e and f056bfe after the bump.
```

**Failure scenario:** A contributor looks for PROTOCOL_REVISION in direct.py, does not find it defined there, and edits the revision history in protocol.py in place instead of bumping. That is what happened twice on 2026-09-23.

**Fix:** Point every doc at rps7200/protocol.py. Rewrite the CLAUDE.md rule to cover host-side decisions that choose command parameters. Make the revision history append-only. Fix the units_for_param docstring to derive its number from COMMAND_UNITS.

<details><summary>Second reader's check</summary>

The doc claims do not match the code:
- CLAUDE.md:450, TODO.md:156 and docs/byte14-plan.md:295 say PROTOCOL_REVISION lives in rps7200/direct.py. It is defined at protocol.py:40, and direct.py only imports it.
- docs/step-calibration-plan.md:207-208 says correcting OVERHEAD moves the revision. d723896 did not move it.
- protocol.py:290's units_for_param docstring says 'param 1 travels 2.57', while COMMAND_UNITS=1.84 gives 2.84.
- CLAUDE.md itself says both '`param 1` ... is **2.84**' (units section) and '`param 1` (~2.6 units) is the finest move' (facts section).
- The revision-6 note was edited after the bump (30bad8e, f056bfe).

</details>

<a id="code-identity-and-protocol-revision-cip-10"></a>

### CIP-10 -- No test ties the bytes sent to PROTOCOL_REVISION, and provenance's failure modes are untested

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_fast_infrared.py:60-74`, `tests/test_roll.py:66-76`, `tests/test_library.py:89-91`

No test records the command trace of a canonical scan, prescan, roll-with-hold or nudge on a fake transport and compares it with a stored expectation keyed to the revision. So d723896's param change, 30bad8e's roll-end change and f056bfe's reordering all passed CI without touching the revision. Nothing tests provenance with git missing, a timeout, a dirty tree or a branch switch; the None->False collapse in CIP-2 would have been caught.

**Evidence (from the code):**

```text
tests/test_fast_infrared.py:74 `assert PROTOCOL_REVISION >= 3`. tests/test_roll.py:66-76 pins only the SLIDE_INIT payload, with the docstring '`PROTOCOL_REVISION` marks entries as comparable only while the conversation with the device is identical'. tests/test_library.py:90 `assert "driver_commit" in record["provenance"]` is the only provenance assertion.
```

**Failure scenario:** The next change to COMMAND_UNITS, HOLD_TOLERANCE_MM, MAX_CORRECTION_PARAM, byte14_for or a tool's command order merges green and still stamps the old revision.

**Fix:** Add a golden-trace test. Run DirectScanner against a recording transport through scan(), prescan(), nudge() over a grid of distances, _hold_to_approved on fixed image pairs, and scan_roll's pre-roll path. Hash the (opcode, payload) sequence and assert it against a table keyed by PROTOCOL_REVISION, so any byte change fails until the revision moves. Add provenance tests with PATH stripped of git, a tree with an untracked file, and a monkeypatched timeout.

<details><summary>Second reader's check</summary>

The only revision assertions are `PROTOCOL_REVISION >= 3` (test_fast_infrared.py:74) and a SLIDE_INIT payload pin (test_roll.py:66-76). d723896 updated param expectations in test_roll, test_session and test_gui without touching the revision, which shows that tests pin bytes but not their tie to the revision. The only provenance test is key existence (test_library.py:90-91). None exercises git missing, a timeout or a dirty tree.

</details>

<a id="code-identity-and-protocol-revision-cip-11"></a>

### CIP-11 -- Roll manifests, approved.json, delivered TIFFs and scan.py sidecars carry no code identity; a resumed roll merges runs from different code

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:1669-1747`, `tools/scan_roll.py:297-326`, `tools/gui.py:2943-2952`, `tools/scan.py:280-281`, `rps7200/tiff.py:86`, `rps7200/__init__.py:18`

The files that describe how a roll was decided and placed record no commit, revision or detector version:
- roll.json and survey.json hold hold outcomes, targets and spent_mm.
- approved.json holds offsets proposed by framing.propose_offsets before 2ce1167 and by frame_edges.propose_centred after it.
'numbering' is the only format marker. A resumed roll merges earlier runs' frame records into one manifest with nothing marking which code produced each. Delivered frame TIFFs and tools/scan.py's JSON sidecar name neither the library entry they came from nor the code. The per-frame library entries are the only place with a (flawed, CIP-1/2) identity.

**Evidence (from the code):**

```text
The session's manifest keys are roll, numbering, dpi, infrared, meter, film, dry_run, start_at, prescan_resolution, rotation, flipped, only, settings, wanted, and `"frames": list(earlier.get("frames") or [])` (session.py:1747). scan_roll's are roll, numbering, started, settings, held, frames. approved.json has `"roll"`, `"numbering": NUMBERING`, and `"offset_mm": round(a.offset_mm, 4)` per frame. The scan.py sidecar is `out.with_suffix(".json").write_text(json.dumps(meta, ...))`. The TIFF software tag is `_SOFTWARE_NAME = "rps7200"`, and `__version__ = "0.1.0"` is never recorded.
```

**Failure scenario:** 1. A roll is started on 2026-09-22 before d723896 and resumed on 2026-09-24.
2. roll.json now lists frames placed under two SLIDE laws and two deadbands, with no per-run revision or commit.
3. An approved.json reopened after the detector change cannot say whether its offsets came from the old or the new proposer.

**Fix:** Stamp every manifest and approved.json with protocol_revision, the import-time code identity and the proposer or detector name and version, per run. Tag each merged frame record with its run id. Write the library entry id and code identity into delivered TIFFs (ImageDescription) and into the scan.py sidecar.

<details><summary>Second reader's check</summary>

The window's roll manifest (session.py:1700-1747), scan_roll's manifest (scan_roll.py:298-321) and approved.json (gui.py:2943-2952) carry no revision, commit or proposer version. approved.json does have a per-frame `source` (measured/neighbours/unconfirmed/operator, from frame_edges/propose.py:141,162), which says how an offset was obtained but not by which code. The resumed roll keeps earlier frames verbatim (session.py:1747). The scan.py sidecar is the meta, which includes protocol_revision but no entry id or commit. The TIFF Software tag is the static 'rps7200' (tiff.py:86), and __version__ is never recorded.

</details>

<a id="code-identity-and-protocol-revision-cip-a1"></a>

### CIP-A1 -- The INQUIRY (vendor, product, firmware, CCD geometry) is passed to every library.save and silently discarded

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:142`, `rps7200/library.py:212-307`, `rps7200/direct.py:728`, `rps7200/session.py:1098`, `rps7200/session.py:2137`, `tools/scan.py:192`, `rps7200/library.py:16`

**Doc claim:** rps7200/library.py:16 'scan.json every setting, the device state, and the provenance'

The device half of 'which code and which hardware produced this' is thrown away on every save, although all callers supply it. The module docstring says scan.json holds 'every setting, the device state, and the provenance', but the firmware revision and the CCD and frame geometry the device reported are not in it. git history shows it was never written: the parameter has existed since the first library.py.

**Evidence (from the code):**

```text
library.py:142 `inquiry: Any = None,` is a save() parameter, and every filing path passes it: direct.py:728 `inquiry=self._inquiry,`, session.py:1098 `inquiry=job["inquiry"],`, session.py:2137 `inquiry=getattr(self._scanner, "_inquiry", None)`, and scan.py:192 `dict(capture, inquiry=info, ...)`. The record built at library.py:212-307 contains no inquiry, device or firmware field; `grep inquiry rps7200/library.py` finds only the parameter. The Inquiry dataclass (protocol.py:467-480) carries vendor, product, model, firmware, max_resolution, ccd_width, ccd_length and frame.
```

**Failure scenario:** A second RPS 7200, or the same one after a firmware update, is used. Entries from both are indistinguishable, and a column-defect or shading difference between the units cannot be attributed to the device.

**Fix:** Write `device: asdict(inquiry)` (or the raw 96-byte INQUIRY hex plus the parsed fields) into scan.json, or drop the parameter so callers stop believing it is recorded.

<a id="code-identity-and-protocol-revision-cip-a2"></a>

### CIP-A2 -- tools/uniformity.py prints the pipeline identity from a key provenance() never sets, so it always says 'unknown'

**Severity** low · **Category** bug · **Verdict** found-by-verifier

**Where:** `tools/uniformity.py:377`, `rps7200/library.py:97-101`

**Doc claim:** CLAUDE.md 'Re-run with `tools/uniformity.py analyse --tag vignette-study` after any correction change: it rebuilds from stored raw bytes, so the answer tracks the current pipeline.'

The vignette/uniformity analysis, which CLAUDE.md says must be re-run after any correction change because it 'tracks the current pipeline', prints 'pipeline: unknown' on every run. The line exists to say which code produced the verdict. The --out payload at uniformity.py:514 stores the full provenance dict, so the problem is limited to the console line.

**Evidence (from the code):**

```text
tools/uniformity.py:377 `print(f"pipeline: {library.provenance().get('commit', 'unknown')}")`; provenance() returns keys driver_commit, driver_dirty, versions, platform (library.py:97-101).
```

**Failure scenario:** Stefan re-runs `tools/uniformity.py analyse --tag vignette-study` on two branches to compare, and both outputs say 'pipeline: unknown'. The console record cannot tell the runs apart.

**Fix:** Use `.get('driver_commit')` and print driver_dirty with it, or print the import-time identity proposed in CIP-1.

<a id="code-identity-and-protocol-revision-cip-a3"></a>

### CIP-A3 -- provenance() runs git in the package's parent directory, so a non-editable install records an unrelated repository's commit

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:79-84`, `pyproject.toml:48`

With `uv sync` the project is installed editable, so cwd is the repo root. If rps7200 is installed as a wheel (`pip install .`, `uv pip install git+...`), cwd becomes site-packages. git then walks up to whatever repository encloses it. A .venv inside some other project is typical, and in that case driver_commit and driver_dirty record that project's HEAD and status. Nothing checks that the repo found is this driver's.

**Evidence (from the code):**

```text
library.py:83 `cwd=Path(__file__).resolve().parent.parent,`; pyproject.toml:48 `packages = ["rps7200"]` (a wheel ships only the package).
```

**Failure scenario:** A user installs the driver into their photo-archive project's .venv (itself a git repo) and scans. Every entry records the archive repo's short sha as driver_commit, which is plausible-looking and wrong.

**Fix:** Verify the toplevel (`git rev-parse --show-toplevel`) contains rps7200/library.py, or record null. Prefer the git-independent loaded-source hash from CIP-1, plus the installed distribution version from importlib.metadata.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Entry provenance: driver_commit, driver_dirty, versions, platform | library/<YYYYMMDDTHHMMSSZ>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.json -> provenance | JSON: {driver_commit: 7+ char short sha str \| null, driver_dirty: bool (False also means unknown), versions: {python, numpy, tifffile?, PIL?}, platform: str} | metadata | library.provenance() inside library.save (library.py:67-101, 306), at filing time: FrameWriter thread (session.py:1090), debug flush after close() (direct.py:694-740), after the with-block in tools/scan.py (237-246), tools/uniformity.py:609 | nobody in code (tests/test_library.py:90 only checks the key exists); tools/uniformity.py:377 reads a non-existent 'commit' key of a fresh provenance() | No. It describes the working tree when the record is built, not the code imported by the process that scanned. versions is process-accurate; the git fields are not. With git unavailable or refusing, the commit is null and dirty is recorded as false (clean). No diff, branch or full sha is kept. |
| Protocol revision stamp | library/<entry>/scan.json -> scan.protocol_revision | JSON int (null for DemoScanner entries and legacy prescans filed with hand-built meta) | metadata | DirectScanner.scan meta at capture (direct.py:2764, from protocol.py:40) -> library.save whitelist (library.py:243) | library.signature (library.py:695) -> duplicates/prunable -> tools/library.py duplicates [--delete] | Exact to the process's imported constant, but under-resolved. Revision 5 spans two SLIDE laws (d723896); revision 6 spans calibrate-first and calibrate-last scan_roll orders and a changed roll-end rule, and its note was edited in place. |
| Image container checksum | library/<entry>/scan.json -> image.sha256 | hex sha256 of the scan.tif file bytes (a tifffile deflate+predictor file or a built-in uncompressed file) | checksum of whatever scan.tif holds (normally the raw decode) | library.save (library.py:229); tools/library.py migrate-raw --write (198-203); library.migrate_direction rewrite branch (library.py:573-576) | library.verify (library.py:787) | Exact for the file as written, but writer- and version-dependent: identical pixels hash differently with and without tifffile. It is not refreshed by migrate_direction's first branch, so an interrupted migrate leaves it permanently stale. |
| Raw bytes checksum | library/<entry>/scan.json -> raw.sha256, raw.bytes; raw.bin.gz | hex sha256 of the uncompressed concatenated READ payloads; gzip level 6 | raw | library.save (library.py:185-211) | library.verify (library.py:810-815) | Yes (writer-independent). |
| Filing timestamp (entry id prefix and created) | library/<YYYYMMDDTHHMMSSZ>_.../scan.json -> created | ISO-8601 UTC seconds | metadata | library.save (library.py:170, 215) at filing time | duplicates ordering (library.py:707-708), prunable tie-break, tools/dpi_analysis.py:52 (selects entries by created range as a proxy for code era) | No. It is filing time, not capture time, and it is used as a substitute for a code identity by an analysis tool. |
| Registration or hold record of the SLIDE decisions for a frame | library/<entry>/scan.json -> registration.approved \| registration.correction | JSON: target_mm, outcome, moves, spent_mm, clamped, source, history[{confidence, dy, dx, px, row_reversed...}], final_mm, residual_mm; would_send{action,param} only on dry runs | derived (host decisions) | DirectScanner._hold_to_approved / _aim_frame (direct.py:2873-2960, 2964-3070) -> marks (3549-3551) -> meta['registration'] (3655) -> library.save (library.py:280) | tools/registration_margin.py, humans | No. The param bytes actually sent are not stored. spent_mm is derived from STEP_MM and OVERHEAD_MM, whose value changed within revision 5. |
| Roll and walk manifests and approved positions | rolls/<name\|date>/roll.json, survey.json; rolls/<_safe(name)\|roll>/approved.json | JSON; the only format marker is 'numbering': 'strip' | metadata / decisions | ScanSession._roll (session.py:1669-1747, 1924-1926), tools/scan_roll.py checkpoint (326-329), tools/gui.py _write_approved (2943-2960) | session.renumbered/read_survey, tools/gui.py read_approved, tools/scan_roll.py hold_from_walk | No code identity at all. Resumed rolls merge frames from earlier runs of possibly different code without per-run markers. |
| Repository index (side effect) | .git/index, .git/index.lock | git index | n/a | `git status --porcelain` run by library.provenance() on every save (optional index refresh; no --no-optional-locks) | git | A side effect of filing. It can collide with a concurrent git write in the shared tree, and a SIGKILL on timeout can leave a stale index.lock. |
| Delivered-file identity | rolls/<name>/frameNN.tif, output folder files, tools/scan.py <out>.json sidecar | TIFF Software tag 'rps7200'; the sidecar JSON is meta incl. protocol_revision | corrected | FrameWriter._write via export.write (session.py:1074-1085); tools/scan.py:278-281 | NegPy / operator | No link to the library entry id or the code commit. __version__ 0.1.0 is static and never recorded. |

**Second reader's corrections to this table:**

These rows are confirmed and need no change:
- Entry provenance, except for the addition below.
- Image checksum.
- Registration record.
- Roll manifests.
- Delivered-file identity. Correction: tools/scan.py's sidecar does carry protocol_revision (it is the meta), but no entry id or commit.

Corrections and additions:

1. **Protocol revision.** Revision 5 spanned two SLIDE laws, and revision 6 spanned calibrate-first and calibrate-last, only on the feature branch. main received df7d4e6 and d723896 together (merge b14ecdb), and 7463990 and f056bfe together (merge dcb4e8f). On main, revision 6 does span the framing.propose_offsets -> frame_edges.propose_centred change (2ce1167, merged 2ec7aa2).

2. **Device identity (missing row).** An `inquiry` argument (vendor, product, firmware, CCD and frame geometry) is passed to every library.save but written nowhere (library.py:142). No row should list it as persisted.

3. **Approved positions.** approved.json does record a per-frame `source` (measured/neighbours/unconfirmed/operator) and `reference_entry`, but no proposer version.

4. **Prescan.** prescan.tif (library/<entry>/prescan.tif) has no checksum in scan.json at all. migrate_direction rewrites it in place (library.py:614-616) with nothing verifiable afterwards.

5. **Dirty flag.** driver_dirty is also set by untracked operator output not covered by .gitignore:
- tools/scan.py `--out x.jpg` and its `x.json` sidecar;
- libraries filed under folders other than library/ or 'library 2/'.

6. **Timestamp.** The filing timestamp row is correct. The GUI's `created` is FrameWriter time, shortly after capture. For debug entries it is after close(), possibly many minutes after capture.

## What the operator can do

- Run the window, tools/scan.py, tools/scan_roll.py or an ad-hoc DirectScanner(debug=True) script. Every filed entry gets scan.protocol_revision from the imported code and a provenance block computed at filing time.
- Run `tools/library.py reconstruct`, `verify`, `migrate-raw` and `migrate-direction`. None of them look at protocol_revision or provenance.
- Run `tools/library.py duplicates [--delete]`. This is the only command whose outcome depends on protocol_revision, through signature().
- Commit changes to COMMAND_UNITS, HOLD_TOLERANCE_MM, MAX_CORRECTION_PARAM, the frame-edge proposer or a tool's command order. Nothing forces a PROTOCOL_REVISION bump.

## What the operator should not do

- Do not switch, create or pull branches in the working tree while the window, a roll or a debug script is running from it. Entries filed afterwards record the new HEAD, and lazily imported modules (rps7200.uniformity at the first hold, rps7200.library and tiff at the debug flush) come from the new branch.
- Do not scan from a machine or shell where git is not on PATH, from a ZIP checkout, or from a repo git refuses as dubious ownership, if provenance matters. The entries record commit null and dirty false.
- Do not leave untracked scratch scripts in the repo while scanning. They make driver_dirty true on every entry and hide a real local modification.
- Do not scan with uncommitted edits to driver constants and then discard them. The entry says only 'dirty', and the code is lost.
- Do not delete experiment/ branches whose commits are named by library entries. The short sha then resolves to nothing.
- Do not edit an existing revision note in rps7200/protocol.py. Bump and append.
- Do not run `duplicates --delete` across revision 5 (the SLIDE law changed within it) or across the revision-6 scan_roll reorder.

## Mistakes nothing guards against

- A parallel Claude session runs `git checkout -b feat/x` (as CLAUDE.md instructs) in the tree Stefan's window is running from. Every later entry is attributed to the feature branch, with no warning.
- A branch whose library.py needs a name the running process's direct.py lacks is checked out mid-run. At close() the lazy `from . import library` fails and every pending debug entry of the run is lost ('library unavailable ... lost').
- A frame is filed while someone runs `git commit` in the same tree. The commit can fail on .git/index.lock, and a `git status` killed at the 10 s timeout can leave a stale lock that blocks all git writes.
- A change to param_for_mm's constants or the hold deadband merges without a bump. Entries before and after share a revision and signature, and duplicates --delete can remove one of a pair placed by different SLIDE bytes.
- migrate-direction --write is interrupted after rewriting scan.tif (for example by an unreadable prescan.tif). The rerun never refreshes image.sha256, and verify reports a checksum mismatch forever.
- A demo run with --library pointing at the real library files real raw bytes again under the demo's provenance with protocol_revision null and no source id.

## Dataflow notes

Code identity enters an entry at two moments, with different fidelity.

(1) At capture:
- DirectScanner.scan builds meta with `"protocol_revision": PROTOCOL_REVISION` (direct.py:2764; the constant is at protocol.py:40, imported at direct.py:91), plus `fast_infrared` after gating (2635) and `carriage_state`/`read_direction`. This is exact to the process's imported code.
- DemoScanner.scan builds its own meta without it (demo.py:576-591).
- Meta then travels by one of four routes:
  a. debug: _debug_capture spools the meta dict and the arrays to a temp dir (direct.py:650-692);
  b. GUI: ScanSession._file snapshots capture_record() and submits a job to FrameWriter (session.py:2031-2141);
  c. scan_roll: FrameWriter (tools/scan_roll.py:343);
  d. tools/scan.py: held in memory in `pending` (tools/scan.py:159-246).

(2) At filing, library.save (library.py:143-309):
- writes scan.tif through tiff.write, which uses tifffile deflate+predictor or the built-in uncompressed writer (tiff.py:128-160);
- hashes the container into image.sha256 (229);
- streams raw bytes and hashes them into raw.sha256 (185-211);
- whitelists meta into record.scan, so protocol_revision survives (237-264), and copies meta['registration'] (280);
- calls provenance() (306), which runs `git rev-parse --short HEAD` and `git status --porcelain` in the package's parent directory with 10 s timeouts (library.py:67-101).
Filing happens:
- on the FrameWriter thread (session.py:1060-1103, created at 1311, device still open until session.py:1333-1339);
- in _debug_flush after close() (direct.py:777-788), with library imported lazily at 705-706;
- after the with-block in tools/scan.py.
So the git fields describe the tree at filing time, not the imported code. rps7200.uniformity.register, which drives hold decisions, is itself imported lazily at framing.py:1039.

(3) SLIDE decisions: scan_roll -> _hold_to_approved / _aim_frame -> hold_plan (framing.py:1172-1215, deadband HOLD_TOLERANCE_MM framing.py:1151) -> nudge (direct.py:3138-3168) -> param_for_mm (3126-3136, OVERHEAD_MM = MM_PER_COMMAND = MM_PER_UNIT*COMMAND_UNITS, protocol.py:260-279) -> slide(0x00/0x01, param, 0x04). Only asked_mm, summed into spent_mm, reaches out/marks['approved'] (direct.py:2897-2903, 3549-3551) -> meta['registration'] (3655) -> scan.json.registration. The param bytes are dropped. GUI Move jobs (session.py:1584) and scan_roll --nudge are not recorded at all.

(4) Readers:
- signature() reads scan.protocol_revision (library.py:695) for duplicates/prunable -> tools/library.py duplicates [--delete] (rmtree).
- verify reads image.sha256 and raw.sha256 (library.py:776-816).
- reconstruct, corrected, decode_raw, migrate-raw and migrate-direction read raw.layout, corrections_applied, calibration.* and read_direction, never revision or provenance. reconstruct re-applies today's apply_shading to legacy entries before comparing (library.py:480-491).
- Only migrate paths rewrite scan.tif, and they recompute image.sha256, except migrate_direction's first branch (565-567).
- tools/dpi_analysis.py selects entries by `created` (the filing time) as its stand-in for a code era.

(5) Sidecar state with no identity:
- rolls/<name>/roll.json and survey.json (session.py:1669-1747, 1924-1926; tools/scan_roll.py:297-329);
- approved.json (tools/gui.py:2943-2960);
- delivered TIFFs, whose Software tag is 'rps7200' (tiff.py:86);
- tools/scan.py's <out>.json sidecar (280-281).
A resumed roll carries earlier runs' frame records forward (session.py:1747) with no per-run code marker.
