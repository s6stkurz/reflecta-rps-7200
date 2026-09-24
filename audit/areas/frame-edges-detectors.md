# Frame-edge detectors (gap pass)

Area key `frame-edges-detectors`. 15 findings: 2 high, 5 medium, 7 low, 1 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Area covered: the frame-edge detector members in tools/frame_edges/ that decide live film moves. I read every file in full: changepoint.py, chroma.py, gapmodel.py, stepline.py, vote.py, centre.py, propose.py, watch.py, roll.py, sides.py, units.py and __init__.py. I followed each output to the device:
- Window (in-walk and sheet): session.edge_reader -> StripWalk.judge -> DirectScanner._aim_frame / _hold_to_approved -> nudge (SLIDE).
- Window (background): EdgeWatch -> sheet -> Approved -> approved.json -> _hold_to_approved.
- CLI: tools/scan_roll.py --approved (hold_from_walk).
- Demo: DemoScanner prescans.
Nothing was run and no file was modified. numpy is not installed on this machine, so the exception paths below come from reading the code, not from execution.


**Q1. What input the members assume, and what they actually get.**

The members assume float RGB (H, 428, 3) with rows upright and the transport along columns. They trim 5 rows and work at the 428-column scale. All of them are relative except chroma, which uses absolute counts (DOM_COUNTS 2.5 and 0.4-count noise floors), tuned on uint8 and uint16.


What each path actually delivers:
- **Live walk and in-walk reader:** the driver's shading-corrected uint8 prescan at 300 dpi, un-rotated. A bottom-up pass is already turned upright by decode_index. The members' x readings are row-order invariant anyway: medians and mid-height fits.
- **Reopen and --approved:** they read rolls/<roll>/prescanNN.tif. The window writes that file in the session's rotation and flip at write time. survey.json records only the walk-start value. read_survey un-orients by that value; scan_roll --approved un-orients nothing.
  - A turn or flip made during a walk therefore hands the detector mirrored or rotated frames, which gives wrong-signed "measured" positions or refusals. The screen hides this. (FE-01)
- **Frozen correction:** these TIFFs keep the shading correction from the day of the walk. They are not library.corrected(entry), whatever CLAUDE.md says. (FE-12)
- **Other prescan resolutions:** the device returns 860/862 columns at 600 dpi and 1292 at 900 dpi. Neither is a multiple of 428, so every such frame is refused, whatever propose.py says. (FE-05)
- **Scale bug for wider passes:** if a pass ever is an exact multiple (856 wide), centring calls decide() with edges in pass columns but a width of 428, and the move comes out wrong. (FE-04)
- **dtype:** chroma compares roll levels only within one dtype (roll_levels and roll_support filter on dtype). On the device every prescan is uint8, so this never splits. The demo mixes uint8 and uint16, and does so while labelling the pixels depth 8. (FE-07)

**Q2. Which exceptions can escape.**
- No member call is guarded, neither in WalkReader nor in vote.detect.
- gapmodel raises a Python ZeroDivisionError (`rel_level=level / p.level`) on a near-black 8-bit frame, such as fogged leader with sparse 1-count noise. Such a frame passes frame_contrast.
- stepline has the same class of error (`/ base`).
- propose._downscaled divides by zero at width 0.
- direct.scan_roll catches only (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError). An error from judge, or from reread after a move, therefore ends the roll with the film already moved. (FE-03)
- EdgeWatch contains errors per frame. propose_centred aborts the whole proposal, so the two differ on failure.

**Q3. Parity with the study.**
- The ~420- and ~390-line diffs compare against stepline_v2 and chroma_v2. ensemble_v2 does not use those files: its MEMBERS are changepoint_v2, chroma, stepline and gapmodel_v2.
- Against the modules it does use, with comments stripped, the copy differs only in import lines and in the roll-context plumbing (roll.Summary in place of study caches). So there is no unintended divergence today. (FE-11)
- The parity test would catch future drift only on a machine that has the study's frames and FRAME_EDGE_PARITY=1. It exercises vote.detect only, not propose's downscale, centring or fill.

**Q4. Can one member produce a move?** Yes.
- vote.lone_gap lets one member's gap-with-neighbour stand. decide() moves on one EDGE side even when the other side refuses.
- WalkReader.judge returns the offset and StripWalk.judge passes it straight through, bypassing combine(). _aim_frame moves on it with no source check.
- scan_roll --approved holds "unconfirmed" and "neighbours" positions without review.
- This is contrary to TODO.md's "no single detector may move film". (FE-02)

**Q5. Constants.**
- Pitch, frame width, aperture and SMALLEST_MOVE do come from rps7200.framing: units.py -> centre.py and propose.py, and gapmodel via pitch_columns.
- The members still carry their own frame-width and aperture geometry, verbatim from the study: stepline FRAME_MIN 395 and a literal 428.0; chroma MIN_FRAME_COLS 420; gapmodel G_PRIOR 17, which implies F = 438.3 against framing's 435.6.
- Their docstrings say ~445 and ~438 columns. (FE-09)

**Other findings:**
- Blank frames are filled from neighbours rather than "none". (FE-08)
- The records of automated moves cannot be re-derived: member answers and context are dropped, and no code identity is recorded. (FE-06)
- mm in place of units, and assorted stale docs. (FE-10)
- Commissioning while the reader is still reading. (FE-13)

## Findings at a glance

| ID | Severity | Category | Title | Problem file |
|---|---|---|---|---|
| [FE-01](#frame-edges-detectors-fe-01) | high | bug | Turning or flipping a prescan during a walk makes the window write later prescanNN.tif in a new orientation. survey.json keeps the old one, and reopen and --approved then feed the detector mirrored or rotated frames | [P20](../problems/P20-rotation-during-walk.md) |
| [FE-02](#frame-edges-detectors-fe-02) | high | design | One detector member alone can move film: lone_gap, then decide(), then WalkReader.judge, and StripWalk skips combine(). scan_roll --approved also holds 'unconfirmed' and 'neighbours' positions without review | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FE-03](#frame-edges-detectors-fe-03) | medium | error-handling | Member exceptions are not contained on the roll path. gapmodel and stepline divide Python floats by zero on near-black frames, and anything other than ValueError aborts the roll, possibly after the film has moved | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FE-05](#frame-edges-detectors-fe-05) | medium | doc-mismatch | No 600 or 900 dpi prescan is ever read: the device returns 860/862 and 1292 columns, which are not multiples of 428, although propose.py says those resolutions are supported | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FE-06](#frame-edges-detectors-fe-06) | medium | data-integrity | Automated edge decisions cannot be re-derived: the record keeps only the voted sides. Member answers, the roll context and the detector code identity are dropped, and approved.json keeps only offset_mm and source | [P09](../problems/P09-record-missing-parameters.md) |
| [FE-07](#frame-edges-detectors-fe-07) | medium | demo-divergence | The demo feeds the detectors prescans the device never produces: uint16 pixels labelled depth 8, mixed dtypes within one walk, and film moves simulated by np.roll wrap-around | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [FE-A1](#frame-edges-detectors-fe-a1) | medium | demo-divergence | DemoScanner.scan_roll ignores the prescan resolution and always walks at 300 dpi, so the demo reads edges at settings where the hardware reads none. It also skips the blank-frame end check and observe for held frames, and has no per-frame error net | [P27](../problems/P27-demo-refusals-and-roll-loop.md) |
| [FE-04](#frame-edges-detectors-fe-04) | low | bug | centring() mixes scales for any pass wider than 428 columns: the edges are in pass columns but decide() is told the width is 428 | -- |
| [FE-08](#frame-edges-detectors-fe-08) | low | bug | A blank (all-base) frame is voted REFUSE, not ALL_BASE, so fill_refused gives it a 'neighbours' position, although the docstring promises 'none' | -- |
| [FE-09](#frame-edges-detectors-fe-09) | low | design | The members keep their own frame-width and aperture geometry, retyped from the study and not taken from rps7200.framing | -- |
| [FE-10](#frame-edges-detectors-fe-10) | low | doc-mismatch | Documentation in and around the detector contradicts the code: millimetres, parity coverage, aperture units and stale study references | -- |
| [FE-12](#frame-edges-detectors-fe-12) | low | doc-mismatch | Reopened walks and scan_roll --approved feed the detectors the corrected prescanNN.tif written on the day of the walk, not library.corrected(entry) as CLAUDE.md states | -- |
| [FE-13](#frame-edges-detectors-fe-13) | low | user-error | Commissioning while the edge reader is still reading uses preliminary readings of already-placed frames, and the warning mentions only unplaced frames | -- |
| [FE-A2](#frame-edges-detectors-fe-a2) | low | user-error | Frame-edge reads on walks at any prescan resolution other than 300 dpi fail silently: nothing refuses or warns when the walk is set up | [P31](../problems/P31-framing-geometry-and-detectors.md) |
| [FE-11](#frame-edges-detectors-fe-11) | info | test-gap | Parity: the copy matches the members ensemble_v2 actually uses. The ~420/~390-line diffs compare against chroma_v2/stepline_v2, which it does not use | -- |

## Findings in full

<a id="frame-edges-detectors-fe-01"></a>

### FE-01 -- Turning or flipping a prescan during a walk makes the window write later prescanNN.tif in a new orientation. survey.json keeps the old one, and reopen and --approved then feed the detector mirrored or rotated frames

**Severity** high · **Category** bug · **Verdict** confirmed · **Problem** [P20](../problems/P20-rotation-during-walk.md)

**Where:** `rps7200/session.py:2025-2029`, `rps7200/session.py:2106`, `rps7200/session.py:1067-1068`, `rps7200/session.py:1687-1688`, `tools/gui.py:3920-3924`, `tools/gui.py:719-723`, `tools/gui.py:4724-4740`, `tools/scan_roll.py:243-256`, `tools/gui.py:3503-3505`

**Doc claim:** tools/gui.py:4668-4674 (read_survey docstring): 'a per-frame turn is recorded in approved.json and never applied to a prescanNN.tif, so this arithmetic stays true however many frames were turned individually'. False, because a turn in the main window is carried into session.rotation. Also rps7200/session.py:2014-2020.

The detectors assume columns lie along the transport and the film is un-mirrored. That holds on the live path: EdgeWatch.add and WalkReader get the un-oriented Result.image. It fails on the two paths that read stored walks.

**Mid-walk turn or flip.** Frames written after the change are stored as orient(raw, new), while the manifest still records the old arrangement. read_survey returns unorient(orient(raw, new), old) as result.image, which is what EdgeWatch.load reads and what becomes Approved.reference.
- A flip or 180 degree difference mirrors the frame: left and right edges swap, and centring's sign reverses.
- A 90 or 270 degree difference changes the width, so the frame is refused, and fill_refused then fabricates a 'neighbours' position for it.

**The screen hides it.** result.rotation is set to the manifest value, so the display computes orient(unorient(file, old), old), which is the file exactly as the operator last arranged it. The pictures look right while the detector input and the hold reference are wrong.

**tools/scan_roll.py --approved on a folder walked in the window** with any rotation or flip set reads the oriented TIFFs as the film's own pixels, even with no mid-walk change.

**Nothing records the orientation per file.** So a stored detector input cannot be re-interpreted later, which defeats the re-derivation requirement.

**Side effect.** `_carry` calls remember_arrangement, which during a walk runs `self.survey.append(result)` again (gui.py:3503-3504), so each turn duplicates that frame in the survey list the sheet is built from.

**Evidence (from the code):**

```text
The session orients each prescan by the arrangement current at write time. `_orientation_for`: `if kind != "prescan": ... return self.rotation, self.flip` (session.py:2025-2029), called per frame at 2106 as `turn, flip = self._orientation_for(number, kind)`. FrameWriter then applies it to every path, prescanNN.tif included: `turned = preview.orient(job["image"], job.get("rotate") or 0, bool(job.get("flip")))` (1067).

The manifest records the arrangement once. It is built at 1669 with `"rotation": self.rotation, "flipped": self.flip,` (1687-1688) and rewritten unchanged after every frame (1924).

Any turn or flip in the main window carries into the session: `_carry`: `self.session.rotation = result.rotation` / `self.session.flip = result.flipped` (gui.py:3922-3923). The rotate and flip shortcuts are always live ("Everything here is viewing or arranging", gui.py:708-723).

On reopen: `turn = int(manifest.get("rotation") or 0)` ... `image=preview.unorient(image, turn, mirrored)` (gui.py:4724-4740).

The CLI does no un-orienting at all: `frames = [(number, tiff.read(str(path))) for number, path, _ in walked_prescans(...)]` -> `propose_centred(frames, ...)` -> `Approved(..., reference=im, ...)` (scan_roll.py:243-256).
```

**Failure scenario:** The operator starts a dry-run walk with no rotation. At frame 3 he sees the strip is in backwards and presses flip. Frames 4-12 are written mirrored, and survey.json says flipped=false.

The next day he reopens the roll. For frames 4-12 EdgeWatch reads mirrored pixels. A frame with 8 columns of base at its right is read as base at its left, and the sheet shows a 'measured' proposal of about 9 units the wrong way. Its reference prescan is the mirror of what the film looks like.

When commissioned, measure_shift_mm either fails to lock (the frame is 'unverified' and not corrected) or locks on a spurious peak and drives the film the wrong way. Running the same folder through scan_roll.py --approved does the same without any sheet in between.

**Fix:** Record the orientation per prescan: in each frame record of survey.json, or better, write prescanNN.tif un-oriented always (it is a reference, not a deliverable) and orient only the out_dir copy. read_survey and hold_from_walk should un-orient per frame from that record, and hold_from_walk must apply the manifest orientation at all. Add a test that flips mid-walk, reopens, and asserts the detector sees the raw orientation. Stop remember_arrangement from re-appending an already-surveyed result.

<details><summary>Second reader's check</summary>

Checked against the code and it holds. `_orientation_for` exempts prescans from per-frame turns but still returns `self.rotation, self.flip` (session.py:2027-2029). Each prescan is written with that value at the moment it is written (session.py:2106 -> FrameWriter._write, preview.orient at 1067-1068). Nothing guards `_carry` during a walk: it sets `self.session.rotation = result.rotation` and `self.session.flip = result.flipped` (gui.py:3920-3923), and the rotate and flip actions are always bound (gui.py:716-723, `_on_current` has no `_surveying` check). The manifest's `rotation`/`flipped` are set once when the manifest dict is built (session.py:1687-1688); a grep finds no later assignment, and the per-frame rewrite (1924) reuses the same dict. `read_survey` un-orients every file by the single manifest pair (gui.py:4724-4740) and feeds those images to `edge_watch.load` (gui.py:2655-2657) and to the sheet as references. The read_survey docstring (gui.py:4668-4674) says the arithmetic 'stays true however many frames were turned individually'. That holds for sheet-level per-frame turns but not for a main-window turn during the walk, which changes `session.rotation`. `hold_from_walk` passes `tiff.read(path)` straight to `propose_centred` and to `Approved(reference=im)` with no un-orienting (scan_roll.py:243-256). So any walk made in the window with a non-zero rotation or a flip is misread by `--approved`, even with no mid-walk change. Side effect confirmed too: `remember_arrangement` appends to `self.survey` and calls `edge_watch.add` again whenever `_surveying` is set (gui.py:3503-3505), and `_ContactSheet` builds `self.frames` from that list with no de-duplication (gui.py:7152).

</details>

<a id="frame-edges-detectors-fe-02"></a>

### FE-02 -- One detector member alone can move film: lone_gap, then decide(), then WalkReader.judge, and StripWalk skips combine(). scan_roll --approved also holds 'unconfirmed' and 'neighbours' positions without review

**Severity** high · **Category** design · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/frame_edges/vote.py:90-109`, `tools/frame_edges/centre.py:233-244`, `tools/frame_edges/propose.py:137-143`, `tools/frame_edges/propose.py:240-243`, `rps7200/framing.py:1729-1733`, `rps7200/direct.py:2985-3045`, `tools/scan_roll.py:252-256`, `tools/scan_roll.py:283-289`

**Doc claim:** TODO.md:214-218: '...plus the rule that no single detector may move film. `combine` requires two members that agree in millimetres.'; rps7200/framing.py:1545-1547: 'One member alone is not an ensemble, and that is the case this exists to refuse'.

The project's safety rule is that no single detector may move film. It was enforced by framing.combine(), which needs two members that agree in millimetres. With the frame_edges reader installed (the window and scan_roll both install it), StripWalk.judge returns the reader's decision directly and combine() never runs.

The ensemble's round-2 rule lets one member's gap-with-neighbour stand as an EDGE side at conf 0.3. decide() places the frame from that one side even when the other side refused. The note says source='unconfirmed', but _aim_frame never reads it: it moves on any non-None decision.

The move is bounded only by the roll's ROLL_TRAVEL_LIMIT_MM (12 mm). changepoint accepts a gap up to MAX_OUTER=100 columns in, so one member can command roughly 80+ units on one frame.

The log line is also misleading. The reader note has no 'agreed' or 'chose' keys, so it reads 'by  (from ?)', and nothing in the log says the move rested on one member.

In the sheet path, 'unconfirmed' and 'neighbours' positions (neighbours: no detector read that frame at all) are pre-filled and 'used exactly as given' after a dialog that only counts them. scan_roll --approved holds them with no confirmation.

**Evidence (from the code):**

```text
**One member's word.** `def lone_gap(...)`: `for name, s in sides.items(): if s.state != EDGE or s.x is None or s.outer is None: continue ... if not elsewhere: return Side(EDGE, x=s.x, ..., conf=0.3, ..., note=f"gap with neighbour, one vote: {name}")` (vote.py:90-101). vote_v2 returns it whenever the two-vote rule fails (104-109).

**One side is enough.** decide(): `if points: ... u = float(np.mean(points))`. With one EDGE side and the other REFUSE, lo and hi stay infinite and the move stands (centre.py:233-244).

**It is labelled, then returned.** centring: `note.update(source="unconfirmed" if lone else "measured", ...)` / `return columns_to_mm(columns, width), note` (propose.py:141-143).

**Reader path bypasses combine.** StripWalk.judge: `if self.reader is not None: decision, detail = self.reader.judge(int(number), image) ... return decision, dict(detail)` (framing.py:1729-1733).

**No source check before the move.** _aim_frame: `decision, detail = walk.judge(index, image)` ... `agreed = "+".join(detail.get("agreed", []))` ... `self._hold_to_approved(index, image, prescan_resolution, _Aim(offset_mm=decision, reference=image), ...)` (direct.py:2985-3042).

**The CLI holds every proposal.** `held = {n: Approved(number=n, offset_mm=float(offsets[n]), reference=im, source=...) for n, im in frames if n in offsets}` (scan_roll.py:254-256). This includes 'unconfirmed' and 'neighbours' offsets, and only a count is printed (283-289).
```

**Failure scenario:** The operator ticks 'correct' (or passes --correct) for an unattended roll. On one frame a black stripe in the scene, about 45 columns in, forms a straight band that only changepoint reads as a gap with the neighbour beyond. No other member places an edge elsewhere on that side, so lone_gap stands. decide() gives about -40 units, and the hold loop drives the film about 4 mm, framing the scan off by the width of that stripe. Nothing asks, and the log does not say it was one member.

**Fix:** Make the reader path honour the rule. Either have _aim_frame (or StripWalk.judge) refuse decisions whose note source is not 'measured', or have WalkReader.judge return None for 'unconfirmed'. Log detail['source'] and the voting members. In scan_roll --approved, hold only 'measured' (and operator) sources by default, and require a flag for 'unconfirmed' and 'neighbours'. If the ensemble_v2 lone-gap rule is meant to move film, change TODO.md to say so explicitly.

<details><summary>Second reader's check</summary>

Confirmed. `lone_gap` returns an EDGE side resting on one member (vote.py:90-101), and `vote_v2` uses it whenever round 1 fails (104-109). `decide` averages whatever points exist, so a single EDGE side stands while the other side is REFUSE (centre.py:233-244, with lo/hi left infinite). `centring` labels the result 'unconfirmed' but still returns the offset (propose.py:139-143). With a reader installed, `StripWalk.judge` returns `reader.judge` directly and never calls `combine` (framing.py:1729-1733). `_aim_frame` moves on any non-None decision and never reads `detail['source']` (direct.py:2985-3045). Its log reads `detail.get('agreed', [])` and `detail.get('chose','?')`, keys the reader note does not have, so the line says 'by  (from ?)'. TODO.md:214-218 states the two-member rule. Two nuances. The sheet's confirmation dialog does name these frames explicitly as 'one detector, uncorroborated' (gui.py:2911-2918), so that path discloses them, though it still does not require separate consent. `scan_roll --approved` holds every proposal, including 'unconfirmed' and 'neighbours', after printing only a count (scan_roll.py:252-256, 283-289). Moves are bounded by the per-command cap, the hold loop and the 12 mm `ROLL_TRAVEL_LIMIT_MM` (framing.py:1169, 1773-1775).

</details>

<a id="frame-edges-detectors-fe-03"></a>

### FE-03 -- Member exceptions are not contained on the roll path. gapmodel and stepline divide Python floats by zero on near-black frames, and anything other than ValueError aborts the roll, possibly after the film has moved

**Severity** medium · **Category** error-handling · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/frame_edges/gapmodel.py:444-450`, `tools/frame_edges/gapmodel.py:464`, `tools/frame_edges/gapmodel.py:487-489`, `tools/frame_edges/gapmodel.py:821`, `tools/frame_edges/gapmodel.py:888`, `tools/frame_edges/stepline.py:561-563`, `tools/frame_edges/stepline.py:570`, `tools/frame_edges/propose.py:60-63`, `tools/frame_edges/vote.py:120`, `tools/frame_edges/propose.py:234-246`, `rps7200/direct.py:3666-3667`, `rps7200/direct.py:2932`, `rps7200/direct.py:3076`

**Doc claim:** CLAUDE.md:476-479: 'its final answer must equal `propose_centred`'s' -- false whenever a member raises: EdgeWatch gives that frame 'none', propose_centred raises for the whole walk.

Consider an 8-bit corrected prescan that is almost entirely 0 with sparse 1-count noise, such as fogged leader or an opaque strip end. frame_contrast is a relative std, so it reads such a frame as picture (mean about 0.02, column std about 0.08, contrast about 4, far above BLANK_CONTRAST 0.02). The frame is observed and, with correct on, judged.

What each member does with it:
- **changepoint and chroma** return answers.
- **stepline** calls it a gate: neutral and flat medians give NO_FILM.
- **gapmodel** raises. Its 5-row running median wipes out the sparse noise, so colmed and peak are 0. Columns with no nonzero smoothed pixel are uniform, so level = 0 and floor = 0. A run is found, and `level / p.level` raises ZeroDivisionError.

roll.summarise catches this during observe, but judge does not. ZeroDivisionError is not in scan_roll's except tuple, so the generator raises:
- **The window:** the session's roll job ends.
- **tools/scan_roll.py:** the outer handler prints 'the roll stopped' and the remaining frames are lost.

When the same error comes from reread inside _hold_to_approved, the film has already been nudged, and neither walk.record nor a RollFrame records it.

On the other paths:
- **Sheet (EdgeWatch):** contains the error per frame and marks the light FAILED.
- **propose_centred:** has no per-frame guard, so one bad frame fails the whole proposal. hold_from_walk crashes before scanning.

That also breaks CLAUDE.md's claim that EdgeWatch's final answer equals propose_centred's: they differ whenever a frame raises.

**Evidence (from the code):**

```text
**gapmodel.** The level and peak are Python floats that can both be 0: `level = float(np.median(colmed[pick]))` (447), `peak = float(np.percentile(gs[:, a:b], 99.5))` (464), `return max(LEVEL_FLOOR * p.level, PEAK_FLOOR * p.peak)` (489). A run then qualifies with `if p.uniform[c] and p.colmed[c] >= floor:` (757). `level = float(np.median(prof))` (821) is then divided in `Candidate(..., rel_level=level / p.level, ...)` (888), which is float/float and raises ZeroDivisionError.

**stepline.** `base = float(colmed[ref])`; `tol = float(np.clip(BASE_SPREAD * _spread(sv[:, ref]) / base, ...))` (562-563) and `darkest = float(np.min(colmed[film])) / base` (570).

**propose.** `if w % SCALE: return None` / `k = w // SCALE` / `h = (img.shape[0] // k) * k` (60-63): width 0 gives k = 0.

**No guard anywhere upstream.** `results = {role: module.detect(image, ctx) for role, module in MEMBERS.items()}` (vote.py:120). WalkReader._read calls detect and centring with no try (propose.py:234-238).

**The roll's net is narrow.** `except (UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError) as exc:` (direct.py:3666-3667).

**It can fire after a move.** `keep, why = rejudge(image)` runs after `self.nudge(want)` (direct.py:2932), and rejudge calls `walk.reader.reread(index, image)` (3076).
```

**Failure scenario:** A strip starts with fogged leader and is rolled with 'correct' on. The leader frame passes the blank check. walk.judge -> vote.detect -> gapmodel.detect raises ZeroDivisionError, and scan_roll aborts before the first real picture. In the CLI, every queued frame after it is lost.

In a variant, a frame darkens after a large nudge (it moved onto leader), the rejudge reread raises, and the roll dies with the film displaced and nothing recorded about the move.

**Fix:** Guard divisions in the members: use max(p.level, 1e-12) and max(base, 1e-12), and refuse when level or base is 0. Guard k == 0 in _downscaled. Wrap WalkReader.judge and reread (and propose_centred per frame) so that any exception becomes a refusal with the error in the note, as EdgeWatch does. Add a regression test with a near-black uint8 frame through StripWalk(reader=walk_reader('negative')).judge.

<details><summary>Second reader's check</summary>

Structurally confirmed. `vote.detect` calls every member with no guard (vote.py:120), and `WalkReader._read`/`judge`/`reread` have no try (propose.py:234-246). `_aim_frame` runs inside scan_roll's try, whose except catches only `UsbError, ScanReadError, CalibrationRequired, ShadingUnavailable, TimeoutError, ValueError` (direct.py:3666-3667). Anything else ends the generator. `rejudge` calls `reread` after `self.nudge(want)` (direct.py:2896, 2930-2932, 3076), so an escape can happen after the film has moved. The division sites are real Python float/float divisions: gapmodel `rel_level=level / p.level` (gapmodel.py:888, both floats from 447/821) and stepline `float(np.min(colmed[film])) / base` (stepline.py:570). Both raise ZeroDivisionError when the level is 0. With no numpy available here I could not confirm numerically that a sparse near-black frame drives `p.level` to exactly 0 and still passes `frame_contrast` (framing.py:64-72). The reasoning is plausible (5-row median, uniform-by-default columns), but the trigger is unmeasured. `EdgeWatch._run` does contain exceptions per frame (watch.py:213-236), while `propose_centred` has no per-frame guard (propose.py:201-206). The CLAUDE.md 'must equal propose_centred's' claim therefore fails exactly when a member raises. The demo's scan_roll has no except at all (demo.py:666-795), so there even a ValueError aborts the roll.

</details>

<a id="frame-edges-detectors-fe-05"></a>

### FE-05 -- No 600 or 900 dpi prescan is ever read: the device returns 860/862 and 1292 columns, which are not multiples of 428, although propose.py says those resolutions are supported

**Severity** medium · **Category** doc-mismatch · **Verdict** confirmed · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/frame_edges/propose.py:14-17`, `tools/frame_edges/propose.py:51-65`, `tools/gui.py:1751-1752`, `rps7200/demo.py:904-909`, `tests/test_frame_edges.py:190-196`

**Doc claim:** tools/frame_edges/propose.py:14-17: 'a pass whose width is a whole multiple of it (600, 900 dpi) is averaged down first and its positions scaled back; any other width is refused'.

propose.py's docstring says 600 and 900 dpi prescans are averaged down to 428 columns. The widths the device actually returns at those resolutions are 860/862 and 1292, so every frame is refused. With no frame placed, fill_refused has nothing to fill from, so the sheet shows every frame as 'refused'/'none' and the in-walk reader abstains on every frame.

Nothing warns the operator that the prescan resolution is the cause. The only test for 'a 600 dpi prescan' uses a synthetic exact multiple, so it passes while the real path never works. Nothing moves the film wrongly here: it is a silent loss of the feature at two resolutions the code and docs present as supported.

**Evidence (from the code):**

```text
**The refusal.** `if w == SCALE: return img, 1` / `if w % SCALE: return None` (propose.py:58-61). detect() then refuses with `f"a {w}-column pass is not a multiple of the {SCALE}-column prescan the detector knows"` (96-98).

**The device widths, as recorded in the repo.** demo.py:907-909: 'the widths the scanner reports are 428, 860, 1292, 2584, 5172 for 300 to 3600 dpi'. TODO.md:418: 'at 600 dpi width and columns both came back 860'. tools/uniformity.py:146: 'the CCD mask marks 860 used pixels against a width of 862'.

**The window accepts any value.** `return self._int(self.v_predpi, "Prescan dpi", 25, 7200)` (gui.py:1752).

**The test uses a width the device never returns.** `big = np.kron(img, np.ones((2, 2, 1), ...))` gives 856 columns (test_frame_edges.py:192).
```

**Failure scenario:** The operator sets the prescan dpi to 600 for a sharper contact sheet and walks 36 frames. The frame-edge light goes to done with no positions. Every caption says refused, the roll is commissioned with all offsets 0, and frames are scanned wherever the walk left them.

**Fix:** Crop or resample to 428*k before averaging: drop the 4 or 8 trailing columns (checking which side the CCD mask pads), then fix FE-04 before enabling it. Or refuse non-300 prescan resolutions in the window when edges are wanted, with a clear message. Replace the kron test with one that uses 860 and 1292 widths.

<details><summary>Second reader's check</summary>

`_downscaled` returns None whenever `w % 428` is non-zero (propose.py:58-61), and `detect` then refuses the frame (96-98). The repo's own records give 600 and 900 dpi widths of 860 and 1292 (demo.py:907-908). 860 % 428 = 4 and 1292 % 428 = 8, so every frame is refused. So is any other prescan dpi, such as 150. The window accepts prescan dpi 25-7200 (gui.py:1751-1752), and the CLI accepts any `--prescan-dpi` (scan_roll.py:108), with no warning that edges will not be read. The propose.py:14-17 docstring names 600 and 900 dpi as supported. The hardware roll path is `StripWalk.judge` -> `reader.judge` -> refuse, so no film moves. The failure is silent loss of the feature, and the demo hides it (see the additional finding FE-A1).

</details>

<a id="frame-edges-detectors-fe-06"></a>

### FE-06 -- Automated edge decisions cannot be re-derived: the record keeps only the voted sides. Member answers, the roll context and the detector code identity are dropped, and approved.json keeps only offset_mm and source

**Severity** medium · **Category** data-integrity · **Verdict** partly · **Problem** [P09](../problems/P09-record-missing-parameters.md)

**Where:** `tools/frame_edges/vote.py:123-125`, `tools/frame_edges/propose.py:113-116`, `tools/frame_edges/propose.py:240-243`, `rps7200/direct.py:2985-2989`, `rps7200/direct.py:3518`, `rps7200/direct.py:3580-3588`, `tools/gui.py:2944-2954`, `rps7200/library.py:97-100`

The in-walk and sheet decisions cannot be replayed exactly. The persisted note keeps only the two voted sides, not what each of the four members answered. The roll context (which frames' summaries, made from their pre-move prescans) is not recorded, and after a correction the pre-move prescan it was built from is replaced by the post-move one. approved.json stores the snapped offset and source with no reading behind them. Library entries do carry `provenance.driver_commit`/`driver_dirty`, which identify the detector code unless the tree was dirty. The walk and roll manifests and approved.json carry no such identity.

**Evidence (from the code):**

```text
**Member answers are dropped.** vote.detect keeps them in `debug = {k: {"left": [...], "right": [...]} ...}; return EdgeResult(left, right, {"members": debug})` (vote.py:123-125). `_edges_note` keeps only `state, x, x_top, x_bottom, outer, note` of the two voted sides (propose.py:113-116). WalkReader.judge then sets `note["members"] = []` (242). _aim_frame stores `"ensemble": detail` (direct.py:2987-2988).

**Context is built from images that are later replaced.** `marks["base"] = walk.observe(index, prescan_image)` summarises the pre-move prescan (3518). After a correction, `prescan_image = fix.pop("prescan")` replaces it (3584-3585), and the replacement is what gets filed.

**approved.json.** `{"number", "offset_mm", "rotation", "flipped", "reference_entry", "source"}` (gui.py:2947-2953). No edges, units or reason.
```

**Failure scenario:** A roll with 'correct' on moves frame 14 by 30 units and the scan comes out badly framed. Later, a new detector version is to be judged against what happened. The record says source 'measured', edges left EDGE at 34.1 and 'edge: changepoint,gapmodel', but not what chroma and stepline said. The frames it was compared against had their pre-move prescans replaced, and nothing names the code that ran. The decision cannot be replayed.

**Fix:** Persist EdgeResult.debug['members'] (each member's state and x per side) in the note WalkReader and centring return. Record the context as (frame number, sha of the image summarised) tuples and a detector identity (for example a hash of tools/frame_edges/*.py or a version constant). Keep the pre-move prescan the decision used. Store the reading (edges, units, reason) in approved.json next to offset_mm.

<details><summary>Second reader's check</summary>

Member answers are dropped as described: `vote.detect` keeps them in `debug` (vote.py:123-125), `centring` stores only `_edges_note` of the voted sides (propose.py:113-116, 131), and `judge` sets `members=[]` (242). The context summaries come from the pre-move prescan (`walk.observe` at direct.py:3518). The corrected replacement is never re-observed, yet it is what gets filed (3580-3588). approved.json holds only number, offset_mm, rotation, flipped, reference_entry and source (gui.py:2947-2953). The 'code identity' point is overstated. Every library entry records `provenance()` with `driver_commit` and `driver_dirty` (library.py:67-100, 306), and that commit covers tools/frame_edges. What is missing is identity in survey.json/roll.json/approved.json, and exactness when the tree is dirty.

</details>

<a id="frame-edges-detectors-fe-07"></a>

### FE-07 -- The demo feeds the detectors prescans the device never produces: uint16 pixels labelled depth 8, mixed dtypes within one walk, and film moves simulated by np.roll wrap-around

**Severity** medium · **Category** demo-divergence · **Verdict** partly · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:414-427`, `rps7200/demo.py:1036-1061`, `rps7200/demo.py:98-113`, `rps7200/demo.py:1186-1194`, `rps7200/demo.py:455-477`, `tools/frame_edges/chroma.py:319-336`

The demo's walk frames diverge from the device's in two ways that matter to the detectors. (a) A film move is simulated with np.roll, which wraps the frame's own far border round instead of revealing the neighbour's gap and picture, so the reread and rejudge after a move see a picture the transport cannot produce. (b) From the second strip of a session on, frames whose entry has no prescan.tif are decoded scans, usually uint16, labelled depth 8. Such a strip mixes dtypes, and chroma's roll support (same-dtype only) is split.

**Evidence (from the code):**

```text
**Where the pixels come from.** DemoScanner.prescan: `image = self._pair_image("prescan.tif", film, resolution)` / `if image is None: image = self._pixels(channels=3)` / `self.last_scan_meta = {..., "depth": 8, ...}` (demo.py:460-474).

**The fallback is a scan.** When the source has no prescan.tif, `_pair_image` decodes the scan: `got = self._decode(source)` ... `image = (self._resample(image, *shape) if shape else self._rescale(image, dpi / self._source_dpi))` (1186-1194). `_decode` keeps the entry's dtype through apply_shading, which casts back to image.dtype (shading.py:281).

**Moves wrap the picture.** `_as_positioned`: `return np.roll(image, pixels, axis=1)` (427).

**chroma has absolute-count thresholds.** `tol = np.maximum(DOM_TOL, DOM_COUNTS / np.maximum(np.exp(level), 1e-9))` (372) and `floor = 0.4 / np.maximum(np.exp(M), 1e-9)  # 0.4 count` (384).

**It compares roll levels within one dtype only.** `if other.chroma_level is not None and other.dtype == ctx.get("dtype")` (423, 433).
```

**Failure scenario:** A demo walk mixes entries with and without prescan.tif. Frames from scan entries arrive as uint16. chroma trusts their base less ('brightest plateau only') and never confirms it from the uint8 frames, so votes and proposals differ from what the device would give for the same pictures. After a demo nudge the reread sees base wrapped in at the opposite border and rejudge abandons the frame, which reads as a weak detector.

**Fix:** Make the demo's prescan match the device contract: convert to uint8 at the prescan's level (as the real 8-bit pass does), resample to the device's recorded 300 dpi shape, and derive film moves by revealing the neighbouring entry's pixels (or padding with base) instead of np.roll. Take the shape and dtype from DirectScanner.prescan's contract, not from the source entry.

<details><summary>Second reader's check</summary>

The np.roll wrap-around is confirmed (demo.py:414-427). A nudged demo frame brings its own far border round to the near side. On the device it would bring in the neighbouring frame's gap and picture, which is exactly what the reread and gapmodel/lone_gap key on. The dtype claim is narrower than stated. The first strip of a session comes from `_pool_for`, which globs only entries with `prescan.tif` (demo.py:1123-1144), so its frames are the stored uint8 prescans. Only from `_next_strip` on (second and later rolls) can entries without prescan.tif appear, because `picture_signature` accepts scans of 600 dpi or less (demo.py:98-113). Those go through `_decode` and `_resample` and keep the scan's dtype while meta says depth 8 (460-474, 1186-1194). chroma does split its roll support by dtype (chroma.py:319-336). The study data used both dtypes, so uint16 is not foreign to the members, only to this device. The 856-column route is not reached by a demo roll, because prescans are always 300 dpi (see FE-04 and FE-A1).

</details>

<a id="frame-edges-detectors-fe-a1"></a>

### FE-A1 -- DemoScanner.scan_roll ignores the prescan resolution and always walks at 300 dpi, so the demo reads edges at settings where the hardware reads none. It also skips the blank-frame end check and observe for held frames, and has no per-frame error net

**Severity** medium · **Category** demo-divergence · **Verdict** found-by-verifier · **Problem** [P27](../problems/P27-demo-refusals-and-roll-loop.md)

**Where:** `rps7200/demo.py:615`, `rps7200/demo.py:708`, `rps7200/demo.py:721-724`, `rps7200/demo.py:749-753`, `rps7200/demo.py:1177-1183`, `rps7200/session.py:1763`, `rps7200/direct.py:3494-3496`, `rps7200/direct.py:3509-3518`, `rps7200/direct.py:3666-3667`

The demo must be the real software with different inputs. For the frame-edge path it is not.

**Prescan resolution.** With prescan dpi set to 600 or 900, the hardware refuses every frame (860/1292 columns, FE-05). The demo's walk still delivers 428-column frames, so every frame is read, proposed and moved. The manifest and the session's deliver meta say 600 (session.py:1686, 1798), the demo's own last_scan_meta says 300, and the demo shows a working edge path exactly where the device has none. It is a constant retyped as a literal 300 in the stand-in, the pattern CLAUDE.md forbids.

**Other divergences.**
- **Blank frames.** A demo roll does not stop at a blank frame, so blank frames reach the detector. On hardware the roll ends before them.
- **Held frames.** A held frame's summary never enters the reader's context, so later frames are judged against fewer frames than on the device.
- **Errors.** A member ValueError aborts the whole demo roll where the device would lose only that frame.

**Evidence (from the code):**

```text
**The session passes it.** `prescan_resolution=job.prescan_resolution` (session.py:1763).

**The demo swallows it.** `def scan_roll(..., edge_reader: Any = None, **kw: Any,)` (demo.py:615), then `prescan, _ = self.prescan(film=film)` with the default `resolution: int = 300` (708). It holds with `self._hold_to_approved(index, prescan, 300, held, ...)` (723) and aims with `self._aim_frame(index, prescan, 300, walk, ...)` (752).

**The real loop.** It prescans with `self.prescan(resolution=prescan_resolution, ...)` (direct.py:3494-3496). It ends the roll on `if contrast < blank_contrast: ... return` (3509-3515) and calls `walk.observe` for every frame before the approved/correct branches (3517-3518). The demo calls observe only in `elif walk is not None:` (749-750) and has no blank check and no `except` around a frame.

**Separately, a stored prescan.tif is returned at its stored size whatever resolution is asked**: `image = tiff.read(str(tif)) ... return image` (demo.py:1177-1183).
```

**Failure scenario:** The operator tries prescan dpi 600 in `--demo` to get sharper contact sheets. Every frame gets a measured position and the sheet looks right. He then uses the same setting on the scanner, every caption says refused, and the roll scans uncorrected. The demo, which is how the driver is judged with the scanner off, reported the opposite of the hardware.

**Fix:** Give DemoScanner.scan_roll an explicit `prescan_resolution` parameter and pass it to `self.prescan(resolution=...)`, `_hold_to_approved` and `_aim_frame`. Have `DemoScanner.prescan` produce the device's recorded shape for that resolution (`_shape_for`) even when a prescan.tif exists. Port the blank_contrast end check, the unconditional `walk.observe` and the per-frame except tuple from DirectScanner.scan_roll, or better, share that loop body.

<a id="frame-edges-detectors-fe-04"></a>

### FE-04 -- centring() mixes scales for any pass wider than 428 columns: the edges are in pass columns but decide() is told the width is 428

**Severity** low · **Category** bug · **Verdict** partly

**Where:** `tools/frame_edges/propose.py:129-138`, `tools/frame_edges/propose.py:68-78`, `tools/frame_edges/sides.py:88-89`, `rps7200/demo.py:615`, `rps7200/demo.py:708`, `tests/test_frame_edges.py:190-196`

**Doc claim:** tools/frame_edges/propose.py:14-17: 'a pass whose width is a whole multiple of it (600, 900 dpi) is averaged down first and its positions scaled back'.

`centring` passes the 428-column `SCALE` to `decide` while the EdgeResult's positions are in pass columns (k x 428). The mix-up makes the right-side base width negative and the left-side base about k times too large. No current caller reaches it: the device's 600/900 dpi widths are refused as non-multiples, and the demo's rolls always prescan at 300 dpi. It is a trap for any fix to FE-05, and the only multi-width test never calls centring.

**Evidence (from the code):**

```text
**detect returns pass columns.** `return _scaled(vote.detect(small, ctx), k)`, and `_scaled` multiplies x, lo, hi, outer, x_top and x_bottom by k (68-78, 102).

**centring is told 428.** `scale = width / SCALE` / `dec = decide(result, SCALE, frame_columns(SCALE, frame_units))` (129-130).

**decide computes base width at that width.** `b = s.base_width(name, width)`, where base_width is `self.x if side == "left" else width - self.x` (centre.py:223, sides.py:88-89).

**Then the result is scaled again.** `columns = units / units_per_column(SCALE) * scale` (138).

**The test does not reach it.** It only checks `res.left.x` of `frame_edges.detect(big, ...)` for a 2x np.kron image and never calls centring.
```

**Failure scenario:** In the demo, the film's source entry is a 300 dpi pass with no prescan.tif. A 600 dpi walk rescales it to 856 columns. A frame with base at its right gets a proposal of about -34 mm, which the sheet snaps and commissions. A frame with base at its left is proposed about 1.8x too far. On hardware, the same happens as soon as prescans are cropped to 856 or 1284.

**Fix:** Call decide on the pass's own scale: `decide(result, width, frame_columns(width, frame_units))` and `columns = units / units_per_column(width)`. Alternatively, run decide on the unscaled 428-column result and scale only the drawing notes. Extend the 600 dpi test to assert that centring(detect(big), 856) equals centring(detect(img), 428) in mm.

<details><summary>Second reader's check</summary>

The code defect is real. `detect` returns x scaled by k (propose.py:68-78, 102), but `centring` calls `decide(result, SCALE, frame_columns(SCALE, ...))` (129-130), so `base_width` for the right side is 428 - x with x in pass columns (sides.py:88-89). It then multiplies by `scale` again (138). The claimed demo route does not happen. `DemoScanner.scan_roll` swallows `prescan_resolution` in `**kw` (demo.py:615) and always calls `self.prescan(film=film)` at the default 300 dpi (708), holding with a literal 300 (723, 752). Every walk and in-walk read in the demo is therefore at 428 columns and never 856. `DemoScanner.prescan` also returns a stored prescan.tif unscaled whatever resolution is asked (demo.py:1177-1183). On hardware, 600 and 900 dpi give 860/862 and 1292 columns, which are refused (FE-05). The branch is latent and only the kron test reaches it (which tests detect, not centring). It becomes live the moment FE-05 is fixed by cropping to a multiple.

</details>

<a id="frame-edges-detectors-fe-08"></a>

### FE-08 -- A blank (all-base) frame is voted REFUSE, not ALL_BASE, so fill_refused gives it a 'neighbours' position, although the docstring promises 'none'

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/vote.py:51-87`, `tools/frame_edges/centre.py:220-221`, `tools/frame_edges/propose.py:133-136`, `tools/frame_edges/propose.py:153-166`, `tools/frame_edges/propose.py:178-183`

**Doc claim:** tools/frame_edges/propose.py:180-183: '``none`` (no film, a blank frame, or a film the detector does not read)'.

changepoint, chroma, stepline and gapmodel each return ALL_BASE for a blank frame, but the vote drops that state. The frame becomes 'refused' and receives an interpolated or extrapolated 'neighbours' position, which the sheet pre-fills as a machine proposal. The docstring's contract is 'none' for a blank frame.

Blank frames with contrast of 0.02 or more reach the sheet: unexposed frames with dust or scratches, or a partly blank frame. If such a frame is ticked, the film is moved for it.

**Evidence (from the code):**

```text
**vote() never emits ALL_BASE.** It only counts NO_FILM, EDGE and PICTURE_TO_BORDER: `edges = {... if s.state == EDGE ...}`, `borders = [... == PICTURE_TO_BORDER]`, and otherwise `return Side(REFUSE, note="no agreement: " ...)` (vote.py:57-87).

**decide's ALL_BASE branch is dead here.** `if s.state in (NO_FILM, ALL_BASE): return Decision("refuse", ...)` (centre.py:220-221) never sees ALL_BASE on this path.

**So centring calls the frame refused.** `gate = {result.left.state, result.right.state} & {NO_FILM, ALL_BASE}` / `note["source"] = "none" if gate else "refused"` (propose.py:134-135).

**And fill_refused fills it.** `unplaced = [n for n, note in notes.items() if note["source"] == "refused"]` ... `offsets[n] = filled[n]` (153-163).
```

**Failure scenario:** An unexposed frame with a scratch passes the blank check. All members say all_base, the vote says refuse, and the sheet shows 'neighbours' with a 6-unit move extrapolated from the frames around it. The operator ticks it by accident, and the transport spends a nudge on it.

**Fix:** Have vote() return ALL_BASE when two or more members say so (mirroring NO_FILM), or have centring treat 'no agreement' notes whose members are all ALL_BASE as a gate. Add a test that a blank frame's note source is 'none'.

<details><summary>Second reader's check</summary>

`vote()` only ever returns NO_FILM, EDGE, PICTURE_TO_BORDER or REFUSE (vote.py:54-87), so ALL_BASE from the members becomes REFUSE with 'no agreement'. `decide` then refuses with 'no side places the frame' (centre.py:250-251). The gate set in `centring` is empty, so the source is 'refused' (propose.py:134-135), and `fill_refused` gives the frame a 'neighbours' offset (153-164). That contradicts the docstring 'none (no film, a blank frame...)' (propose.py:180-183). In-walk it is harmless, since the decision is None and nothing moves. On the sheet the frame is pre-filled with a machine position. Frames with contrast below 0.02 end the hardware roll before reaching the detector (direct.py:3509-3515), so only textured blank frames are affected.

</details>

<a id="frame-edges-detectors-fe-09"></a>

### FE-09 -- The members keep their own frame-width and aperture geometry, retyped from the study and not taken from rps7200.framing

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `tools/frame_edges/stepline.py:187-193`, `tools/frame_edges/chroma.py:191-196`, `tools/frame_edges/gapmodel.py:194-206`, `tools/frame_edges/gapmodel.py:1190`, `tools/frame_edges/changepoint.py:319-325`, `tools/frame_edges/sides.py:21`, `rps7200/framing.py:1950-1957`

Q5: pitch, FRAME_WIDTH_UNITS, APERTURE_MM, SMALLEST_MOVE and units_per_column really do come from rps7200.framing, through units.py into centre.py, propose.py and gapmodel's pitch_columns.

The members' own consistency checks, however, use other frame widths:
- gapmodel's no-roll prior F = 455.3 - 17 = 438.3 columns, against framing's 435.6;
- stepline's 395 and a literal 428.0 aperture;
- chroma's 420.

Their docstrings describe frames of ~438-445 columns. These are verbatim study values, so parity holds, and they only work because _downscaled forces 428 columns. They are nevertheless a second home for the frame width: a change to framing.FRAME_WIDTH_UNITS changes centring but none of the members' geometric vetoes. Numerically the difference is small today (gapmodel's joint 'room' is 2 columns either way).

**Evidence (from the code):**

```text
**stepline.** `FRAME_MIN = 395.0` / `IMPLIES_BORDER = 428.0 - FRAME_MIN`, with the docstring 'The labelled frame is ~445 columns' (stepline.py:187-193).

**chroma.** `MIN_FRAME_COLS = 420` (chroma.py:196).

**gapmodel.** `G_PRIOR = 17.0`, commented 'pitch 455.3 minus the frame width measured on both sets, 438', and `frame_w = pitch_columns(w) - roll.gap` (gapmodel.py:196-198, 1190).

**changepoint.** BOTH_SIDES_MIN is commented 'the frame measures 429-440 columns (median 438)'.

**sides.** `TRIM_ROWS = 5`, copied from the study's common.py.

**framing.** `FRAME_WIDTH_UNITS = 350.6`, which is 435.6 columns (framing.py:1950-1957).
```

**Failure scenario:** framing.FRAME_WIDTH_UNITS is re-measured, for example as 348 units for a different camera. centre.decide recentres with it, but stepline and chroma keep judging 'room for a frame' with 395 and 420 and gapmodel with 438.3, so their conflict rules and the centring target disagree about what a frame is.

**Fix:** Leave the values as the study's for parity, but derive them in units.py from framing and assert at import that they are consistent. Alternatively, document them as study-fixed column constants valid only at 428 columns. Update the stale docstring widths.

<details><summary>Second reader's check</summary>

Verified. stepline has `FRAME_MIN = 395.0` and `IMPLIES_BORDER = 428.0 - FRAME_MIN` (stepline.py:187-193). chroma has `MIN_FRAME_COLS = 420` (chroma.py:196). gapmodel has `G_PRIOR = 17.0` with frame 'measured 438' and `frame_w = pitch_columns(w) - roll.gap` (gapmodel.py:194-198, 1190). changepoint's BOTH_SIDES_MIN comment says 429-440/438 (changepoint.py:319-325). framing's FRAME_WIDTH_UNITS is 350.6 units = 435.6 columns (framing.py:1950-1957). Pitch, units_per_column, APERTURE_MM and SMALLEST_MOVE are all imported from rps7200.framing through units.py (units.py:9-12), and centring uses FRAME_WIDTH_UNITS. These are verbatim study constants, kept for parity and valid only because input is forced to 428 columns.

</details>

<a id="frame-edges-detectors-fe-10"></a>

### FE-10 -- Documentation in and around the detector contradicts the code: millimetres, parity coverage, aperture units and stale study references

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/frame_edges/centre.py:133-140`, `tools/frame_edges/centre.py:160-162`, `tools/frame_edges/propose.py:119-143`, `tests/test_frame_edges_parity.py:66-98`, `tools/frame_edges/gapmodel.py:10`, `tools/frame_edges/chroma.py:728-731`

**Doc claim:** CLAUDE.md:245-252 'Millimetres are prohibited ... express every sub-frame distance in units'; CLAUDE.md:470-474 'It holds the copy to the study's stored answers on real film'; CLAUDE.md:264 'the aperture is **345.2** units' vs CLAUDE.md:460 '350.6 units against 344.5'.

Four mismatches:
- **mm output.** The detector's output contract is offset_mm, contrary to CLAUDE.md's rule that sub-frame distances are held in param units. It crosses two scales that differ by 0.2% (APERTURE_MM/width against MM_PER_UNIT).
- **Parity scope.** CLAUDE.md says the parity test 'holds the copy to the study's stored answers'. It holds only the vote, and only on the owner's machine. CI never runs it, and the layer that decides moves is outside it.
- **Aperture in units.** CLAUDE.md gives the aperture as 345.2 units (the mm conversion) in one place and 344.5 (428 columns / 1.2423) in another.
- **Copied study docstrings.** Some numbers and module names are no longer true here.

**Evidence (from the code):**

```text
**Millimetres.** centring returns `columns_to_mm(columns, width)`, which is `float(columns) * APERTURE_MM / float(width)` (centre.py:160-162, propose.py:143). The module was created 2026-09-23 (git log 2ce1167), after CLAUDE.md's mm prohibition of 2026-09-21.

**Parity coverage.** The parity test calls `vote.detect(img, ctx)` directly (test_frame_edges_parity.py:95, 98). It never runs propose.detect, _downscaled, centring or fill_refused, and it skips unless `STUDY / "data" / "manifest.json"` exists and FRAME_EDGE_PARITY is set (36-39).

**Stale study reference.** The gapmodel docstring cites `common.pitch_columns` (line 10), which does not exist in this package.

**Stale arithmetic.** chroma's `_left_side` docstring says 'frames 445 columns wide ... leaves a 13.6-unit window'.
```

**Failure scenario:** A maintainer edits propose.centring, runs FRAME_EDGE_PARITY=1 and sees it pass, and concludes the move path is unchanged, although the parity test never calls centring. Or a reader takes offset_mm at face value and compares it against the unit law, finding a 0.2% disagreement that is only a scale mismatch.

**Fix:** Either express centring's output in units and convert at the hold-loop boundary, or amend CLAUDE.md to state the exception. Say in CLAUDE.md exactly what the parity test covers, and add a CI-runnable parity check on a small committed fixture. Fix the aperture figure and the stale docstrings.

<details><summary>Second reader's check</summary>

`centring` returns `columns_to_mm(...)` = columns * APERTURE_MM / width (centre.py:160-162, propose.py:143). The module was created in 2ce1167 on 2026-09-23, after the CLAUDE.md mm rule, although centre.py's own docstring (133-140) justifies mm as the hold-loop scale. The parity test calls only `vote.detect` and skips unless `research/frame-edge/data/manifest.json` exists and FRAME_EDGE_PARITY is set (test_frame_edges_parity.py:34-40, 95-98). data/ is absent in this checkout. CLAUDE.md:264 gives the aperture as 345.2 units (APERTURE_MM 36.49 / MM_PER_UNIT 0.1057) and CLAUDE.md:460 as 344.5 (428/1.2423). The code holds both, as `APERTURE_MM` and `units_per_column` (framing.py:373, 1972-1980), and units_per_column's docstring calls `APERTURE_MM / width` 'wrong by 0.7%' while `columns_to_mm` uses exactly that. The gapmodel docstring cites `common.pitch_columns` (gapmodel.py:10) and the chroma docstring cites 445 columns (chroma.py:728).

</details>

<a id="frame-edges-detectors-fe-12"></a>

### FE-12 -- Reopened walks and scan_roll --approved feed the detectors the corrected prescanNN.tif written on the day of the walk, not library.corrected(entry) as CLAUDE.md states

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `tools/gui.py:4733-4743`, `tools/scan_roll.py:243-244`, `rps7200/session.py:1817-1837`, `tools/scan_roll.py:499-501`

**Doc claim:** CLAUDE.md:116-119: 'Everything an operator sees ... the prescans in `rolls/` — and `library.corrected(entry)` is what computes it, with *today's* correction code rather than whatever ran that day.'

CLAUDE.md says the prescans in rolls/ are computed by library.corrected(entry) with today's correction code. In the code they are frozen, corrected at walk time. Re-reading a stored walk ('positions measured again from the prescans on every launch') therefore runs today's detector on yesterday's shading correction, and the hold reference is that old-corrected picture compared against a freshly corrected pass.

In the window, the walk prescans also have raw library entries, since dry-run prescans are filed with raw_image. So an exact, re-correctable source exists and is not used.

**Evidence (from the code):**

```text
**read_survey reads the TIFF.** `image = tiff.read(str(path))` from walked_prescans, even though the entry is known: `entry=Path(entries[number]) if number in entries else None` (gui.py:4735-4743).

**So does the CLI.** `frames = [(number, tiff.read(str(path))) ...]` (scan_roll.py:243).

**How the TIFF was made.** The window writes it once from the driver's corrected pixels via FrameWriter (session.py:1817-1837). The CLI writes it with `tiff.write(str(pre), frame.prescan)` (scan_roll.py:500).
```

**Failure scenario:** A fix to the 8-bit prescan shading changes column levels. Reopened walks still read the old-corrected TIFFs, so the sheet's readings after the fix differ from those of a fresh walk of the same strip, and the difference is not attributable to the detector.

**Fix:** In read_survey and hold_from_walk, rebuild each prescan from its library entry with library.corrected(entry) (with a TIFF fallback), un-oriented. Otherwise, correct CLAUDE.md to say rolls/ prescans are frozen deliverables.

<details><summary>Second reader's check</summary>

`read_survey` builds each Result from `tiff.read(str(path))` (gui.py:4733-4739), and `hold_from_walk` does the same (scan_roll.py:243-244). Nothing on either path calls `library.corrected`, which the GUI uses only at gui.py:3864 and 4110 for other views. So the detector on reopen reads walk-day correction, not today's, contrary to CLAUDE.md:116-119. The dry-run window prescans do have raw library entries (session.py:1817-1837, `raw_image=rf.raw_prescan`, file_entry default True), so a re-correctable source exists.

</details>

<a id="frame-edges-detectors-fe-13"></a>

### FE-13 -- Commissioning while the edge reader is still reading uses preliminary readings of already-placed frames, and the warning mentions only unplaced frames

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/frame_edges/watch.py:508-515`, `tools/frame_edges/watch.py:441-450`, `tools/gui.py:3106-3113`

While the state is READING, the sheet shows positions for frames read only against the frames before them. The final re-read against the whole walk can change those positions: chroma's roll support, gapmodel's G and colour all depend on the context. A commission made before the light turns green freezes the preliminary numbers into Approved and approved.json, and the dialog does not say that placed frames may still move.

**Evidence (from the code):**

```text
**Preliminary readings count.** `_progress` includes any reading made from the current version (`if f.reading is None or f.read != f.version: continue`), including readings made against only the frames that had arrived (watch.py:508-514). Frames are re-read against the whole walk only after finish (`again` pass, 441-450).

**The warning.** `_edges_pending` says only 'A frame the reader has not placed yet is scanned where the walk left it.' (gui.py:3110-3113).
```

**Failure scenario:** The operator commissions right after the walk ends while the reader is in its re-read pass. Frame 2 was read with one frame of context (gap prior 17, no roll colour) and carries a position the final reading would have refused. It is held to that position.

**Fix:** Block or explicitly confirm commissioning until the state is DONE or FAILED. Alternatively, mark preliminary readings in the notes (for example 'final': False) and say in the dialog how many are preliminary.

<details><summary>Second reader's check</summary>

`_progress` includes every reading whose `read == version` (watch.py:254-260), including readings taken before the walk finished against a partial context. `_next` re-reads them against the whole walk only once `_finished` is set (187-196). `_edges_pending` warns only about frames 'not placed yet' (gui.py:3106-3113) and says nothing about placed frames that may still change.

</details>

<a id="frame-edges-detectors-fe-a2"></a>

### FE-A2 -- Frame-edge reads on walks at any prescan resolution other than 300 dpi fail silently: nothing refuses or warns when the walk is set up

**Severity** low · **Category** user-error · **Verdict** found-by-verifier · **Problem** [P31](../problems/P31-framing-geometry-and-detectors.md)

**Where:** `tools/gui.py:1751-1752`, `tools/scan_roll.py:108`, `tools/frame_edges/propose.py:58-61`, `tools/frame_edges/watch.py:244-269`

This is the operator-facing side of FE-05. Both the window and the CLI accept any prescan dpi and start a walk with correction or edge reading on. At 150, 600 or 900 dpi each frame's note says the width is not a multiple, and the light still goes to DONE (not FAILED). The frame-edge feature is lost for the whole roll, and the only notice is buried in each frame's caption.

**Evidence (from the code):**

```text
`return self._int(self.v_predpi, "Prescan dpi", 25, 7200)` (gui.py:1752). `ap.add_argument("--prescan-dpi", type=int, default=300, ...)` (scan_roll.py:108). `if w % SCALE: return None` (propose.py:60-61). The refusal reason reaches only the per-frame note; EdgeWatch reports DONE with every note 'refused'/'none' (watch.py:261-267).
```

**Failure scenario:** --correct with --prescan-dpi 600 on a 36-frame roll: every frame is 'left as it came' with a width-refusal reason in the log. The operator believes the roll was centred.

**Fix:** When correct is set or edges will be read, refuse or warn before the walk starts if prescan dpi is not 300 (or not a supported width). Report a whole-walk refusal as FAILED with one summary message.

<a id="frame-edges-detectors-fe-11"></a>

### FE-11 -- Parity: the copy matches the members ensemble_v2 actually uses. The ~420/~390-line diffs compare against chroma_v2/stepline_v2, which it does not use

**Severity** info · **Category** test-gap · **Verdict** confirmed

**Where:** `research/frame-edge/algos/ensemble_v2.py:38-39`, `tools/frame_edges/chroma.py:419-438`, `tools/frame_edges/gapmodel.py:1020-1036`, `tests/test_frame_edges_parity.py:34-40`

The large divergence in the question comes from comparing against the round-2 chroma and stepline, which ensemble_v2 does not use. Against the right modules the executable code is identical except for imports and roll-context plumbing, so there is no unintended divergence today.

Two behavioural differences remain, both deliberate:
- the roll context is 'other frame numbers of this walk', where the study used 'other film frames of the same roll and split', including duplicate prescans of one frame;
- dtype comes from the array, where the study used the manifest.

The parity test would catch future code drift in the members. It needs the study's local frames (research/frame-edge/data, absent in this checkout) and FRAME_EDGE_PARITY=1, so no CI run and no machine without Stefan's pictures can detect a regression.

**Evidence (from the code):**

```text
The study's ensemble_v2 sets `MEMBERS = {"changepoint": "changepoint_v2", "chroma": "chroma", "stepline": "stepline", "gapmodel": "gapmodel_v2"}`.

Changed lines after stripping comments and docstrings, copy against the study module it uses: changepoint vs changepoint_v2, 2 (the import); chroma vs chroma, 35 (imports, and `_ROLL_CACHE`/`_roll_level(fid)` replaced by `ctx["roll"]` Summaries); stepline vs stepline, 5 (imports); gapmodel vs gapmodel_v2, 18 (imports, and `_CACHE`/`load(fid)` replaced by `other.gap_widths`/`gap_colours`).

Against the modules ensemble_v2 does not use: chroma vs chroma_v2, 274; stepline vs stepline_v2, 336.

vote.vote and lone_gap are the same as study ensemble.vote and ensemble_v2.lone_gap.
```

**Failure scenario:** Someone edits a threshold in gapmodel.py on a laptop without the study frames. make test is green (the parity test is skipped), and the change reaches main and the scanner unnoticed.

**Fix:** Commit a small, anonymised set of study frames (or their Summaries plus a few crops) with their stored answers, so parity runs in CI. Record the diff baseline (study commit 32a98e5 and member mapping) in the parity test.

<details><summary>Second reader's check</summary>

Verified by diffing after stripping comment lines (docstrings kept, which gives slightly higher counts). changepoint vs changepoint_v2: 2 lines. chroma vs chroma: 38. stepline vs stepline: 6. gapmodel vs gapmodel_v2: 22. The study's ensemble_v2 maps members to exactly those modules (research/frame-edge/algos/ensemble_v2.py:37-38). All the differences are imports and the `roll_ids`/module cache being replaced by `ctx['roll']` Summaries (chroma.py:319-336, gapmodel.py:856-858). No unintended behavioural drift. Running the parity test needs local study frames and FRAME_EDGE_PARITY=1, so CI never runs it.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Walk prescan written by the window (detector input on reopen, and the hold reference) | rolls/<roll>/prescanNN.tif (plus a copy under <out_dir>/<PRESCAN_SUBDIR>/) | TIFF uint8 RGB, about (287, 428, 3) at 300 dpi | corrected (the driver's shading-corrected 8-bit pass), and ORIENTED by session.rotation/flip at write time | ScanSession._file(kind='prescan', path=...) -> FrameWriter._write -> preview.orient -> export.write (rps7200/session.py:1817-1837, 1067-1080) | gui.read_survey -> preview.unorient(manifest rotation/flipped) -> EdgeWatch.load and Approved.reference (tools/gui.py:4724-4743, 5825-5831). Also tools/scan_roll.py hold_from_walk, as-is (243-256). | Pixel values are lossless, but corrected rather than raw. The orientation is not recorded per file (only the walk-start value in survey.json), so after a mid-walk turn or flip the stored input cannot be un-oriented correctly. The raw bytes live in the separate library entry, which is not used here. |
| Walk prescan written by the CLI | rolls/<roll>/prescanNN.tif | TIFF uint8 RGB | corrected, un-oriented | tools/scan_roll.py dry run: tiff.write(str(pre), frame.prescan) (499-501) | gui.read_survey (manifest has no rotation, so 0) and scan_roll hold_from_walk | Lossless corrected pixels, orientation correct. Not raw. |
| Pre-correction prescan that the in-walk detector judged | rolls/<roll>/prescanNN-before.tif | TIFF uint8 | corrected. Oriented when written by the window, un-oriented when written by the CLI. | session.py:1841-1853 (dry run only, file_entry=False); tools/scan_roll.py:506-514 (dry run only) | nothing in code | Dry runs only, with no library entry and no raw bytes. On non-dry-run rolls the image the move was decided from is not kept. |
| Walk/roll manifest orientation and in-walk correction record | rolls/<roll>/survey.json \| roll.json: top-level rotation/flipped; frames[].registration.correction | JSON. correction = {decision_mm, ensemble:{edges:{left/right:{state,x,x_top,x_bottom,outer,note}}, width, reason, source, units, columns, members:[]}, outcome, moved, target_mm, history[...], ...} | derived | ScanSession roll loop (manifest built once at session.py:1669, rotation at 1687-1688, rewritten per frame at 1924); tools/scan_roll.py; direct._aim_frame builds the ensemble detail (2985-2989) | gui.read_survey (rotation/flipped, registration), session.renumbered, tools/registration_margin.py | rotation/flipped hold the walk-start value only. Per-member answers, roll context and detector identity are not recorded, so the decision cannot be re-derived. |
| Library record of a scanned roll frame (registration and prescan) | library/<entry>/scan.json 'registration'; library/<entry>/prescan.tif | JSON plus TIFF uint8 | registration is derived; prescan.tif is the corrected post-correction (replacement) prescan | library.save via FrameWriter (registration=meta.get('registration'), library.py:280) | tools/registration_margin.py; research build_dataset (prescan.tif) | Same gaps as the manifest record. The stored prescan is not the one the decision was judged on when a correction moved the frame. |
| Approved positions (the sheet's outcome) | rolls/_safe(<roll field>)/approved.json | JSON frames[{number, offset_mm (snapped, 4 dp), rotation, flipped, reference_entry, source}] | derived | ScannerGui._write_approved (tools/gui.py:2929-2954) | gui.read_approved on reopen; the roll it commissions | offset_mm is rounded to 4 dp. The detector's reading (edges, units, reason) and any preliminary/final flag are not stored. |
| Window's remembered sheet state | gui-settings.json -> 'sheet' (per roll: offsets, sources, ticks, rotations, flips) | JSON | derived | ScannerGui (tools/gui.py:2346-2350) | _recall_sheet_state -> _merge_kept (machine sources are dropped and re-read) | Machine positions are not replayed but re-measured, which is only as good as the stored prescans (see FE-01/FE-12). |
| Study parity data | research/frame-edge/results/ensemble_v2/iter01/results*.json, mirrored*.json (in repo); research/frame-edge/data/manifest.json and data/frames/*.npy (local only, absent here) | JSON EdgeResult per frame id; .npy float32 frames | corrected study inputs; answers derived | research/frame-edge/run.py | tests/test_frame_edges_parity.py (only with FRAME_EDGE_PARITY=1 and the data on disk) | Answers are compared to 1e-9 columns. Only vote.detect is covered. |
| Detector memory during a walk | (in memory) EdgeWatch._frames {image, summary, reading, against}; WalkReader._summaries | numpy arrays and roll.Summary dataclasses | corrected input; derived summaries | EdgeWatch.add/load; WalkReader.observe | EdgeWatch.progress -> sheet; WalkReader.judge/reread -> StripWalk -> _aim_frame | Never persisted, so the context a move was read against is lost when the process ends. |

**Second reader's corrections to this table:**

These corrections are checked against the code:

1. **rolls/<roll>/prescanNN.tif (window).**
   - Written only on dry runs: `if job.dry_run:` at session.py:1803. On a non-dry-run roll the window writes no prescanNN.tif into rolls/.
   - Its orientation is `_orientation_for(number, 'prescan')`, i.e. session.rotation/flip at write time, composed with `meta['reversal']` if the prescan meta carries one (session.py:2105-2109). It is not just rotation/flip.
   - The same call files a library entry with `raw_image=rf.raw_prescan` (file_entry defaults to True). So for dry-run window walks, raw prescan bytes do exist in the library; the reopen path just does not use them.

2. **prescanNN-before.tif.**
   - DirectScanner also yields `prescan_before` on non-dry-run RollFrames (direct.py:3647-3650).
   - The session drops it there: it is filed only inside the dry-run block (session.py:1841-1853). The CLI writes it only in its dry-run branch (scan_roll.py:492, 506-514).
   - So the table's 'dry runs only' is correct, but the loss is a choice made in the session and the CLI, not a gap in the driver.

3. **Library scan.json.** Every entry also records `provenance` = {driver_commit, driver_dirty, versions, platform} (library.py:67-100, 306). The detector code identity is therefore recorded for library entries, exactly when the tree is clean. It is absent from survey.json, roll.json and approved.json.

4. **Detector input dtype on reopen.** It is the prescanNN.tif TIFF, not library.corrected(entry). Nothing on the frame-edge path reads library.corrected; the GUI calls it only at gui.py:3864 and 4110 for other views. So the 'uint16 from library.corrected on reopen' premise in the area question does not happen on this path.

5. **Study parity data.** `research/frame-edge/data/` is absent in this checkout. Only results/*.json are present, so the parity test always skips here.

## What the operator can do

- Walk a strip in the window (dry run). Each prescan is read in the background by EdgeWatch as it arrives, and the contact sheet shows the positions (measured / unconfirmed / neighbours / none) with edge lines.
- Open the contact sheet before the reader has finished. It fills in as readings arrive, and the header light shows reading, done or failed.
- Override any proposal on the sheet. An operator position is kept and never re-proposed; machine positions are re-read on every reopen.
- Reopen a stored walk (Open roll, or make run-sheet). Positions are measured again from rolls/<roll>/prescanNN.tif with today's detector.
- Tick 'correct' in the window or pass --correct to let the in-walk reader (WalkReader) move each unapproved frame through _hold_to_approved. --correct-dry-run only logs the would-send command.
- Run tools/scan_roll.py --approved <walk folder> to propose and hold every frame without the window.
- Choose the film. Negatives and bw are read; positive and kodachrome are skipped (no positions, no in-walk reading).
- Type any prescan dpi from 25 to 7200 in the window, or pass --prescan-dpi. Only 300 dpi produces readings.
- Run FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py where the study frames exist.

## What the operator should not do

- Rotate or flip a prescan (keyboard shortcut or buttons) while a dry-run walk is running. Every later prescanNN.tif is written in the new arrangement while survey.json keeps the old one.
- Run tools/scan_roll.py --approved on a folder walked in the window with any rotation or flip set. The CLI does not un-orient the stored prescans.
- Rely on edge positions or 'correct' with a prescan dpi other than 300. 600 and 900 dpi passes (860/862 and 1292 columns) are refused frame by frame.
- Enable 'correct' on an unattended roll expecting two-member agreement. One member's gap-with-neighbour can move the film by up to the roll's 12 mm budget.
- Enable 'correct' on strips that begin or end with fogged or opaque leader. A near-black frame raises ZeroDivisionError in gapmodel and aborts the roll.
- Commission from the sheet while the edge light is still blue (reading). Already-placed frames may still change once they are re-read against the whole walk.
- Change anything under tools/frame_edges on a machine without the study frames and trust make test. The parity test is skipped there.
- Tick blank or partly blank frames that show a 'neighbours' position. The detector did not place them; the position is an interpolation.

## Mistakes nothing guards against

- Turning or flipping during a walk: nothing warns, and the pictures still look right on reopen (the display re-applies the same turn). The detector and the hold references get mirrored or rotated frames: wrong-signed 'measured' proposals, or refusals filled from neighbours. Each turn also appends the frame to self.survey again.
- Using --approved on a window-walked, oriented folder: no check that the manifest's rotation/flipped is non-zero before the TIFFs are used as detector input and references.
- Setting the prescan dpi to 600 or 900: no warning that the edge detector will refuse every frame. The sheet just shows refused/none and the roll scans uncorrected.
- A near-black frame judged in-walk (correct on): the exception is not in scan_roll's except tuple, so the whole roll ends, possibly after the film has been nudged, and nothing records the move.
- 'Unconfirmed' (single-member) and 'neighbours' (no reading) positions are held automatically by --approved and by the in-walk correction, with no confirmation step.
- Commissioning during READING freezes preliminary readings into approved.json. The dialog only mentions frames not yet placed.
- Reopening an old survey.json with no 'film' key uses the window's current film selector, which may not be the film that was walked.

## Dataflow notes

**Entry 1: live walk in the window.**
DirectScanner.scan_roll(dry_run) -> prescan() -> scan(depth=8, shading=True) -> decode_index (a bottom-up pass is turned upright; columns never reverse) -> apply_shading -> uint8 (~287, 428, 3) at 300 dpi.
- It becomes RollFrame.prescan and is delivered by ScanSession._deliver as preview.downscale(prescan, 1400), a no-op at 300 dpi (session.py). The window then calls remember_arrangement -> EdgeWatch.add(number, result.image) (gui.py:3503-3505). This image is un-oriented.
- In parallel, ScanSession._file(kind='prescan') -> FrameWriter orients it by session.rotation/flip -> rolls/<roll>/prescanNN.tif, and library.save stores it with raw_image. survey.json's rotation/flipped are written once at start (session.py:1669-1688).

**Detector core (the same on every path).**
1. propose.detect: film_type (negative -> c41, bw -> bw, else refuse) -> _downscaled (width must be 428 or an exact multiple, else refused; float32; RGBI -> RGB) -> ctx{film_type, dtype of the source array, roll = Summaries of the OTHER frames}.
2. vote.detect runs changepoint, chroma, stepline and gapmodel, unguarded. Each trims 5 rows and works as a left side on the mirrored plane. vote_v2 then decides per side: NO_FILM with two votes; EDGE when a cluster of two or more agrees within 1 column; PICTURE_TO_BORDER with two votes; otherwise lone_gap, one member with an outer edge. ALL_BASE is never produced. _scaled multiplies x by k.
3. centre.decide at 428 scale (t = (428 - 435.55)/2 columns; one EDGE side is enough; two sides must agree within 2 units; deadband SMALLEST_MOVE 2.84 units) -> units -> columns (units * 1.2423 * width/428) -> offset_mm = columns * APERTURE_MM / width.
4. The note carries source measured/unconfirmed/refused/none. fill_refused (framing.fill_from_neighbours, Theil-Sen over frame number, three or more placed) turns 'refused' into 'neighbours'.

**Context (roll.Summary).**
roll.summarise runs chroma.frame_summary for the frame's own base level (compared only within the same dtype) and gapmodel._both_sides for full-gap widths and base colours. Exceptions are swallowed.
- EdgeWatch reads each frame against the frames already arrived, then, after finish(), re-reads it against all the others. That should equal propose_centred except when a member raises.
- WalkReader is causal: only frames observed so far, keyed by transport index.

**Sheet to film.**
Progress.offsets -> _edges_changed -> _ContactSheet.take_readings -> _snap_proposals/_merge_kept -> operator -> Approved(offset_mm=snap_offset(...), reference=result.image, source) (gui.py:5825) -> _write_approved -> approved.json -> Roll job -> scan_roll(approved) -> _hold_to_approved: measure_shift_mm(reference, fresh prescan) at APERTURE_MM/width -> hold_plan -> nudge(mm) -> param_for_mm -> SLIDE 0x00/0x01.

**Entry 2: reopen.**
read_survey -> walked_prescans -> tiff.read(prescanNN.tif) -> preview.unorient(manifest turn/flip) -> Result.image (result.rotation = manifest turn) -> EdgeWatch.load -> core as above. The reference is the same array.

**Entry 3: in-walk correction** (correct or correct_dry_run in the window or --correct).
scan_roll builds StripWalk(reader=walk_reader(film)) (direct.py:3419-3423). Per chosen frame: prescan -> frame_contrast blank check -> walk.observe (summary of the pre-move image, 3518) -> if not approved, _aim_frame -> StripWalk.judge -> WalkReader.judge (combine() bypassed) -> decision mm -> _hold_to_approved(_Aim(decision, reference=image), rejudge = reader.reread after each move) -> nudge. The replacement prescan becomes RollFrame.prescan; prescan_before is kept on dry runs only. The correction detail goes to registration.correction in roll.json/survey.json and library scan.json. Member exceptions escape unless they are ValueError (3666-3667).

**Entry 4: tools/scan_roll.py --approved.**
hold_from_walk -> tiff.read (no un-orient) -> propose_centred (unguarded) -> Approved for every frame with an offset (measured, unconfirmed or neighbours) -> scan_roll(approved) -> _hold_to_approved.

**Entry 5: demo.**
DemoScanner.prescan -> prescan.tif (uint8), or a decoded scan entry (entry dtype, often uint16) resampled or rescaled, or a full-resolution entry. It is labelled depth 8. np.roll stands in for film moves. The same StripWalk and EdgeWatch then run.

**Entry 6: parity test.**
Study .npy float32 frames -> vote.detect (no propose layer) compared with results/ensemble_v2/iter01.

**Units and constants.** PITCH_UNITS, FRAME_WIDTH_UNITS, APERTURE_MM, SMALLEST_MOVE and units_per_column come from rps7200/framing.py through units.py. The members keep their own study constants at 428-column scale.
