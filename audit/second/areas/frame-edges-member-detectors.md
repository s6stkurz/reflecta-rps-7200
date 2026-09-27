# The frame-edge detectors themselves (gap pass)

Area key `frame-edges-member-detectors`. 11 findings: 3 medium, 6 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

This area covers the four frame-edge members that decide film moves (changepoint.py, chroma.py, gapmodel.py, stepline.py) and their support modules (sides.py, roll.py, units.py), read in full and followed through vote, propose, centre and watch into the window, scan_roll and StripWalk.


The members are faithful to the study. A diff against research/frame-edge/algos shows the copies are textually the modules ensemble_v2 scored: changepoint_v2, round-1 chroma, round-1 stepline and gapmodel_v2. They differ only in the roll-context plumbing: a Summary per frame instead of a module cache keyed by frame id. vote.py matches ensemble.vote plus lone_gap, and centre.decide matches common.decide.


Checks that turned out clean:
- **Hard-coded geometry.** The 428-column literals are safe. Examples are stepline's 428.0/FRAME_MIN, chroma's MIN_FRAME_COLS, and the gap ranges in changepoint and gapmodel. propose._downscaled hands the members exactly 428 columns or refuses. The device's 600 and 900 dpi widths (860, 862, 1292) are refused.
- **Non-finite values.** No NaN or inf reaches the vote. Every nanmedian/percentile path in the members is guarded. A 60-frame in-memory fuzz produced no non-finite value and no exception.
- **Input domain.** The input is consistent across paths: corrected uint8 prescans in the transport's column order. This holds for the live window, a reopened walk (prescanNN.tif un-oriented) and scan_roll --approved. The study was fitted on the same kind of data (library.corrected pixels and delivered prescan.tif).
- **Library re-runs.** Re-running on the library's raw walk-prescan entries gives the live answer only after library.corrected, and only if today's correction code is unchanged.

Real problems:
- A member that raises is silently dropped. The reason goes into a debug dict nothing reads, summaries swallow errors, the window's light stays green, and no CI test would notice.
- A slide read as a negative (the default film in both tools) comes out "measured, centred". Stepline's "not a negative" refusal is outvoted.
- centring mis-scales any pass whose width is a whole multiple of 428. This is latent, because the device never returns such a width.
- The vote never passes ALL_BASE through, so blank frames get positions filled from their neighbours, which the docs say they should not.
- The in-walk record keeps neither the member answers nor which frames formed the context.
- Smaller items: --no-shading --correct is unguarded, CLAUDE.md overstates the role of the base colour ratio, and propose_centred excludes a frame's own summary by list index rather than by frame number.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [FE-01](#frame-edges-member-detectors-fe-01) | medium | error-handling | confirmed | A member that raises on every frame is silently dropped from the vote; nothing surfaces it at runtime, in the records or in CI |
| [FE-02](#frame-edges-member-detectors-fe-02) | medium | user-error | confirmed | A slide read as a negative (the default film) comes out 'measured, centred'; stepline's 'not a negative' refusal is outvoted |
| [FE-A1](#frame-edges-member-detectors-fe-a1) | medium | demo-divergence | found-by-verifier | The demo reads and moves 600 dpi walks the device refuses: a 300 dpi source fitted to 600 dpi becomes 856 columns, a multiple of 428, and reaches the wrong-move path of FE-03 |
| [FE-03](#frame-edges-member-detectors-fe-03) | low | bug | confirmed | centring computes the wrong move for a pass whose width is a whole multiple of 428 (positions are scaled back, then decided at 428) |
| [FE-04](#frame-edges-member-detectors-fe-04) | low | bug | confirmed | The vote never passes ALL_BASE through: a frame all four members call blank is 'refused' and then given a position from its neighbours |
| [FE-05](#frame-edges-member-detectors-fe-05) | low | data-integrity | confirmed | In-walk detector decisions cannot be re-derived: the record keeps neither the member answers nor which frames formed the roll context |
| [FE-06](#frame-edges-member-detectors-fe-06) | low | user-error | confirmed | scan_roll --no-shading with --correct (or a --no-shading walk proposed later) feeds uncorrected prescans to a detector validated only on corrected ones, unguarded |
| [FE-08](#frame-edges-member-detectors-fe-08) | low | bug | confirmed | propose_centred excludes a frame's own summary by list index, not frame number, so duplicate numbers self-validate and it disagrees with EdgeWatch on the same folder |
| [FE-A2](#frame-edges-member-detectors-fe-a2) | low | error-handling | found-by-verifier | _aim_frame logs 'by {agreed} (from {chose})', keys the frame_edges note never has, so every in-walk move is logged with no attribution |
| [FE-07](#frame-edges-member-detectors-fe-07) | info | doc-mismatch | partly | CLAUDE.md says base is known by its colour ratio; the detector that moves film does not use that absolute ratio to identify base |
| [FE-09](#frame-edges-member-detectors-fe-09) | info | design | confirmed | Member geometry is literal 428-column constants, while the gate that guarantees 428 columns comes from framing.PRESCAN_COLUMNS |

## Findings in full

<a id="frame-edges-member-detectors-fe-01"></a>

### FE-01 -- A member that raises on every frame is silently dropped from the vote; nothing surfaces it at runtime, in the records or in CI

**Severity** medium · **Category** error-handling · **Verdict** confirmed

**Where:** `tools/frame_edges/vote.py:114-132`, `tools/frame_edges/vote.py:149-155`, `tools/frame_edges/propose.py:153-177`, `tools/frame_edges/propose.py:274-277`, `tools/frame_edges/roll.py:44-56`, `tools/frame_edges/watch.py:226-251`, `tools/frame_edges/watch.py:279-282`, `tools/gui.py:6247-6248`, `tests/test_frame_edges.py:247-268`, `tests/test_frame_edges_parity.py:34-40`

**Doc claim:** tools/frame_edges/watch.py:34-35 says ``failed``: the detector raised on a frame -- that frame has no position and says why. In practice a member raising never reaches this state.

The abstain-on-exception wrapper was added so that one member's ZeroDivisionError could not end a roll. It turns any systematic failure, such as a numpy upgrade or an edit that breaks chroma on uint8 input, into a silent 3-member (or 2-member) vote. Nothing records which member abstained or why:
- neither the sheet note, nor registration.correction.ensemble, nor the window's edge light;
- the vote note for an edge lists only the members in its cluster, so an absent member looks like one that merely disagreed;
- the failed member's summaries also vanish. For gapmodel this means G falls back to G_PRIOR with no roll colour, so band-seen edges are rejected ('no roll base colour to check it') and deep gaps are refused.

**Evidence (from the code):**

```text
vote._member: `except Exception as exc: why = f"{role} failed: ..."; return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why)), why`. The reason is stored only as `debug[k]["failed"] = why` (vote.py:154). A grep shows no production reader of it. propose.centring builds its note from `_edges_note(result)` and never copies `result.debug`. WalkReader.judge then sets `note["members"] = []`. roll.summarise wraps both context computations in `except Exception: level = None` and `except Exception: widths, colours = (), ()`, so a failing member also silently stops contributing roll context. EdgeWatch reaches FAILED only when `summary`/`read_frame` themselves raise (`state = FAILED if self._errors else DONE`), and `_member` and `summarise` stop member exceptions from reaching them. The only test touching this checks a monkeypatched gapmodel on one frame (`assert "failed" not in failed.debug["members"]["changepoint"]`). No test asserts that no member failed on the synthetic walk. The parity test is skipped unless FRAME_EDGE_PARITY=1 and research/frame-edge/data exists, which it does not in this checkout.
```

**Failure scenario:** A change makes chroma.frame_summary raise on every 8-bit prescan. Every frame reads with three members:
- the maximum vote conf drops to 0.75;
- a 2-edge-vs-2-border tie can no longer occur;
- a lone_gap from gapmodel stands where chroma would have placed an edge elsewhere.
Proposals and in-walk moves change. The window shows 'frame edges 36/36' with a green light, and roll.json's correction notes read 'edge: changepoint,stepline'. The unit tests stay green because the other members still find the synthetic edges, and CI never runs the parity test.

**Fix:** Carry `result.debug['members'][k]['failed']` into the centring note (for example an `abstained` field) and into WalkReader.judge's detail, which is persisted in registration.correction.ensemble. Have EdgeWatch record member failures in `_errors` so the light says FAILED. Make roll.summarise report its exceptions instead of swallowing them. Add a unit test asserting that no member has 'failed' in debug on the conftest synthetic walk, for both c41 and bw.

<details><summary>Second reader's check</summary>

vote.py:130-132 turns any member exception into REFUSE/REFUSE, and the reason is kept only in debug[k]['failed'] (vote.py:153-154). The only reader of that key is tests/test_frame_edges.py:266-268. propose.centring builds the note from _edges_note(result) and never copies result.debug (propose.py:147-150, 165-176), and WalkReader.judge sets note['members'] = [] (propose.py:276). roll.summarise swallows exceptions from both chroma and gapmodel (roll.py:181-191). EdgeWatch reaches FAILED only when summary() or read_frame() itself raises (watch.py:236-256, 279-282), which _member and summarise prevent for member faults. research/frame-edge/data is absent, so the parity test is skipped here (tests/test_frame_edges_parity.py:34-40). One nuance: when a side ends in REFUSE, the 'no agreement' note does list every member, the failed one as '=refuse' (vote.py:87-89), so it looks like an ordinary refusal. Edge and border notes omit it entirely. The cited doc line watch.py:34 is not really contradicted: it describes read_frame raising, which still reaches FAILED. The defect is the silent systematic abstention, not a false doc claim.

</details>

<a id="frame-edges-member-detectors-fe-02"></a>

### FE-02 -- A slide read as a negative (the default film) comes out 'measured, centred'; stepline's 'not a negative' refusal is outvoted

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `tools/frame_edges/stepline.py:567-574`, `tools/frame_edges/vote.py:53-89`, `tools/frame_edges/centre.py:120-124`, `tools/frame_edges/propose.py:43`, `tools/frame_edges/propose.py:59-61`, `tools/frame_edges/propose.py:170-177`, `tools/scan_roll.py:157`, `tools/gui.py:1392`, `tools/gui.py:2986-2988`

**Doc claim:** tools/frame_edges/stepline.py:94-95: 'Not a negative. A frame with a full-height strip near black ... is refused: nothing above holds there.' The refusal is not what the software acts on.

None of the members can tell a positive from a negative except stepline, and the vote treats stepline's refusal as an abstention. When the film label is wrong, the result is a confident 'measured' answer that the frame sits right, not a refusal. Such a frame is actually 10-22 units off with a black gap in view. Slides are meant to get no reader at all; a labelled positive still aims on the legacy 36 mm model. Mislabelling silently swaps that for a 'measured' no-move.

**Evidence (from the code):**

```text
stepline: `if darkest < NOT_A_NEGATIVE: why = f"a full-height strip at {darkest:.1%} of the brightest: not a negative"; return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why), debug)`. vote(): `answers = {k: s for k, s in sides.items() if s.state != REFUSE}`, so the refusal is simply left out, and `if len(borders) >= MIN_VOTES: return Side(PICTURE_TO_BORDER, ...)`. decide() with both sides at the border gives `u = (lo + hi) / 2.0`, which is 0. centring then labels it `source="unconfirmed" if lone else "measured"`. Protection comes only from the label: `FILM_TYPES = {FILM_NEGATIVE: "c41", FILM_BW: "bw"}` and `return FILM_TYPES.get(str(film or FILM_NEGATIVE))`. scan_roll: `ap.add_argument("--film", default="negative", ...)`. gui: `self.v_film = tk.StringVar(value="negative")`, and a reopened walk with no recorded film uses `self._survey_film or self.v_film.get()`. Reproduced in memory on 6 synthetic slides, each with a 13-28-column opaque inter-frame gap at the left and read with film='negative': every one gave members changepoint/chroma/gapmodel=picture_to_border and stepline=refuse. The vote returned picture_to_border on both sides, and centring returned source 'measured' with units 0.0.
```

**Failure scenario:** An operator runs `tools/scan_roll.py --correct` on a slide strip and forgets `--film positive`, or reopens an old slide walk whose manifest lacks `film` while the window is set to negative. Every frame with its black gap in view is logged as measured and sitting right. The sheet shows it as a measured proposal of 0, and the roll scans it with the gap in the picture.

**Fix:** Let a member's 'not a negative' finding veto the frame. Either give stepline a distinct state or note that vote.detect/centring turns into a refusal with source 'none', or run the same full-height-near-black test in propose.detect before voting. Also warn when the walk's film falls back to a default.

<details><summary>Second reader's check</summary>

stepline.py:567-574 refuses with 'not a negative', and vote() drops REFUSE answers (vote.py:55). With three members saying picture_to_border, vote.py:81-84 returns PICTURE_TO_BORDER. decide() then gives u=(lo+hi)/2=0 (centre.py:120-124), and centring labels it 'measured' (propose.py:175). I reproduced this in memory on 6 synthetic slides (a positive picture from 200*(0.9-t) with a 13-28 column near-black gap at the left) read with film='negative'. Every one gave stepline refuse/refuse, the other members picture_to_border (one side edge in one case), a vote of picture_to_border on both sides, source 'measured' and units 0.0. The film defaults are as cited: scan_roll --film default 'negative' (scan_roll.py:157), gui v_film 'negative' (gui.py:1392), a reopened walk falls back to v_film (gui.py:2986-2988), hold_from_walk uses settings.get('film') or FILM_NEGATIVE (scan_roll.py:~322), and film_type(None) is 'c41' (propose.py:59-61). Caveat: a more naive inversion, a bright neutral positive, was read as no_film/refuse (source 'none') instead, so the outcome depends on the slide's density. The 'measured, centred' result is still reachable.

</details>

<a id="frame-edges-member-detectors-fe-a1"></a>

### FE-A1 -- The demo reads and moves 600 dpi walks the device refuses: a 300 dpi source fitted to 600 dpi becomes 856 columns, a multiple of 428, and reaches the wrong-move path of FE-03

**Severity** medium · **Category** demo-divergence · **Verdict** found-by-verifier

**Where:** `rps7200/demo.py:935-943`, `rps7200/demo.py:1110-1135`, `tools/frame_edges/propose.py:85-99`, `tools/frame_edges/propose.py:153-177`, `tools/gui.py:2219-2229`, `tools/frame_edges/propose.py:50-56`

When the demo's library holds no 600 dpi entry, which is typical of a fresh or small demo library, a 600 dpi walk hands the detector 856-column prescans. The device's 860/862 would be refused. At 856 every frame is read through the k=2 path, whose move FE-03 shows is wrong: left bases about 1.8x too far, right bases refused. The window has just warned that 'every frame of this walk will be refused ... "correct" will move nothing'. In the demo, frames are proposed and, with correct on, moved. The demo reports something plausible and wrong about exactly the path CLAUDE.md says it must exercise faithfully. A source with no recorded dpi (the test card, or a prescan.tif fallback with 'dpi': None, demo.py:1364, 1387) likewise keeps its stored width at any resolution, so 600/900 dpi demo walks can come back 428 wide and be read as if 300 dpi.

**Evidence (from the code):**

```text
demo._fit: `shape = None if dpi == resolution else self._shape_for(resolution)` ... `elif dpi: h = max(1, round(height * resolution / dpi)); w = max(1, round(width * resolution / dpi))`. _shape_for returns None when no library entry has `resolution_dpi == dpi`. For a 428-column 300 dpi source at 600 dpi, w = round(428*2) = 856. propose._downscaled: `if w % SCALE: return None; k = w // SCALE` accepts 856 as k=2. The device returns 860/862 at 600 dpi (READ_AT_DPI comment, propose.py:50-56), which is refused. The window only warns: `unread = (frame_edges.unread_at(predpi, ...) ...); if unread: question += unread` (gui.py:2225-2229). It does not refuse.
```

**Failure scenario:** In `--demo` with a library holding only 300 dpi prescans and higher-dpi scans but no 600 dpi entry, the operator sets prescan 600 dpi and ticks 'correct'. The dialog warns that nothing will be read. The sheet then shows measured proposals, left-base frames about 1.8x too far, and the demo roll moves them. On the device the same run would refuse every frame.

**Fix:** Make the demo refuse a resolution it cannot size from a recorded pass, raising in the stand-in, as the refusal rule requires. Or derive the fallback width from the same per-dpi table the device is known to produce, not from a ratio. Independently, fix FE-03 (decide on the unscaled result) or refuse k>1 in _downscaled.

<a id="frame-edges-member-detectors-fe-03"></a>

### FE-03 -- centring computes the wrong move for a pass whose width is a whole multiple of 428 (positions are scaled back, then decided at 428)

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/propose.py:85-99`, `tools/frame_edges/propose.py:102-112`, `tools/frame_edges/propose.py:130-136`, `tools/frame_edges/propose.py:153-177`, `tests/test_frame_edges.py:190-196`

**Doc claim:** tools/frame_edges/propose.py:15-17: 'a pass whose width is a whole multiple of it is averaged down first and its positions scaled back'. The move computed from those positions is not scaled.

The downscale path is documented and tested as supported, but its move is wrong: left bases give about 1.8x the move, and right bases are refused. It is unreachable on this device today, because 600, 900, 1800 and 3600 dpi return 860/862, 1292, 2584 and 5172 columns, none of them multiples. Any width that is a multiple would silently produce wrong film moves through EdgeWatch, propose_centred and WalkReader alike.

**Evidence (from the code):**

```text
detect() returns `_scaled(vote.detect(small, ctx), k)`, with x, lo, hi, outer, x_top and x_bottom multiplied by k. centring then runs `scale = width / SCALE; dec = decide(result, SCALE, frame_columns(SCALE, frame_units))`, so the k-scaled positions are read as 428-column positions. Reproduced in memory with negative_prescan and a 2x kron to 856 columns. A left base of 12 columns gives -12.706 u (-1.3458 mm) at 428 and -22.374 u (-2.3698 mm) at 856. A right base of 9.5 columns gives +10.702 u at 428, but at 856 it is refused with 'edge contradicts the other side's border'. The only test of this path (`test_a_600_dpi_prescan_is_read_at_the_prescan_scale_and_scaled_back`) checks detect's x and never checks centring.
```

**Failure scenario:** The demo library, a firmware revision or a future resolution returns 856 or 1284 columns. Every frame with base at the left is moved about 1.8x too far, and every frame with base at the right is refused.

**Fix:** Decide on the unscaled `small` result, calling decide(vote.detect(small), SCALE, ...) and scaling only the drawn edges. Alternatively refuse k>1 in _downscaled until it is validated. Extend the test to compare read_frame's offset_mm at 428 and 856.

<details><summary>Second reader's check</summary>

propose.detect returns _scaled(vote.detect(small), k) (propose.py:136, 102-112). centring then calls decide(result, SCALE, frame_columns(SCALE)) (propose.py:164), so the k-scaled x values are read as 428-column positions, and Side.base_width computes width - x with width=428 (sides.py:271-275). I reproduced it in memory with negative_prescan and a 2x kron. Left base 12 gave -1.3451 mm/-12.7 u at 428 and -2.3684 mm/-22.36 u at 856. Right base 9.5 gave +1.1323 mm/+10.69 u at 428 and 'refused: edge contradicts the other side's border' at 856. It is unreachable on the device (860/862/1292 columns), but it is reachable in the demo; see the additional finding FE-A1.

</details>

<a id="frame-edges-member-detectors-fe-04"></a>

### FE-04 -- The vote never passes ALL_BASE through: a frame all four members call blank is 'refused' and then given a position from its neighbours

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/vote.py:53-89`, `tools/frame_edges/propose.py:167-170`, `tools/frame_edges/propose.py:187-201`, `tools/frame_edges/propose.py:212-217`, `tools/frame_edges/centre.py:95-96`

**Doc claim:** tools/frame_edges/propose.py:215-217: '``none`` (no film, a blank frame, or a film the detector does not read)'.

The documented contract is that blank frames and no-film frames get source 'none'. In code they fall into fill_refused and receive an interpolated move, and the ALL_BASE checks downstream are dead code. The practical reach is small, because frame_contrast below blank_contrast ends a walk before most such frames are yielded (FR-07). A frame that passes the contrast test but is read as all-base by the members is still placed as if the detector had merely disagreed.

**Evidence (from the code):**

```text
vote() has branches only for NO_FILM (at least 2 members), EDGE and PICTURE_TO_BORDER; everything else becomes `Side(REFUSE, note="no agreement: ...")`. centring's `gate = {result.left.state, result.right.state} & {NO_FILM, ALL_BASE}` and decide's `if s.state in (NO_FILM, ALL_BASE)` can therefore never see ALL_BASE. Reproduced on a synthetic unexposed C-41 frame: changepoint, chroma, stepline and gapmodel all said all_base on both sides, and the vote gave refuse/refuse with source 'refused'. In a 5-frame walk with it as frame 3, propose_centred gave frame 3 `0.963 mm, source 'neighbours'`. A fully empty gate goes the same way on C-41, where only stepline says no_film and the other three say all_base.
```

**Failure scenario:** A stored or live walk holds a mostly-unexposed frame that passed the contrast check. The sheet proposes a 'from neighbours' offset for it, and a commissioned roll moves and holds it there, where 'not placed' was promised.

**Fix:** In vote(), let at least MIN_VOTES all_base answers (or all_base plus no_film) stand as ALL_BASE. Alternatively have centring read the member states from result.debug and return source 'none' when the members unanimously say all_base or no_film.

<details><summary>Second reader's check</summary>

vote() has no ALL_BASE branch (vote.py:53-89), so a unanimous all_base answer falls through to REFUSE 'no agreement'. The gate test in propose.py:168 and decide's check at centre.py:95 therefore never see ALL_BASE on a voted result. I reproduced this in memory on a flat C-41 base frame: all four members said all_base on both sides, the vote gave refuse/refuse, and centring gave source 'refused'. fill_refused (propose.py:187-200) would then fill it from neighbours when placed frames bracket it. As the reader notes, the practical reach is narrowed by the blank_contrast end-of-film check in the roll path (direct.py:3985-3992). No such check exists in the sheet path (EdgeWatch/propose_centred over stored prescans).

</details>

<a id="frame-edges-member-detectors-fe-05"></a>

### FE-05 -- In-walk detector decisions cannot be re-derived: the record keeps neither the member answers nor which frames formed the roll context

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `tools/frame_edges/propose.py:262-266`, `tools/frame_edges/propose.py:268-277`, `rps7200/direct.py:3996`, `rps7200/direct.py:3457-3461`, `rps7200/direct.py:4055-4056`, `rps7200/framing.py:1670-1681`

**Doc claim:** rps7200/framing.py:1673-1674: 'Called on every frame including the first two, and again on a frame's replacement prescan.' Only direct.py:3996, on the arrival prescan, calls it.

Each in-walk decision depends on three things:
- the arrival prescan, which is not kept raw without debug filing (FR-02);
- ctx['roll'], the summaries of the arrival prescans of the frames observed before it, in that order, each replaced if a frame was re-observed;
- the member answers.
The persisted note has the voted edges and the move, but not the context membership, the member answers or a detector version. So even where every prescan exists, the decision cannot be recomputed exactly with the same code, and cannot be compared cleanly under newer code.

**Evidence (from the code):**

```text
WalkReader.observe returns `{"reader": "frame_edges", "summarised": len(self._summaries)}`, which is stored as `marks["base"]`: a count, not which frames or which prescan versions. WalkReader.judge sets `note["members"] = []`, and that detail is persisted as `marks["correction"]["ensemble"]`. The members' own states and positions (result.debug) are never kept. observe is called only at direct.py:3996 on the arrival prescan. StripWalk.observe's docstring nevertheless says 'Called on every frame including the first two, and again on a frame's replacement prescan.'
```

**Failure scenario:** A later detector change needs to be checked against the moves a past roll actually made. The roll.json notes say 'edge: chroma,gapmodel' and 'summarised: 7', but nothing says which seven prescans formed the context or what changepoint and stepline answered. The old decision cannot be reproduced to diff against.

**Fix:** Persist the member answers and failures (result.debug['members']) and a list of (frame number, prescan identity such as a library entry id or pixel sha1) that formed ctx['roll'], plus a detector revision. Also correct the StripWalk.observe docstring, or observe the replacement prescan as it claims.

<details><summary>Second reader's check</summary>

WalkReader.observe returns only {'reader','summarised':count} (propose.py:262-266), stored as marks['base'] (direct.py:3996). judge sets note['members']=[] (propose.py:276), and _aim_frame persists detail as 'ensemble' (direct.py:3457-3461, 4055-4056). result.debug with the member answers is never carried. grep finds only one observe call in the roll path (direct.py:3996, on the arrival prescan), while the StripWalk.observe docstring (framing.py:1673-1674) claims it is also called on a replacement prescan. No detector revision is recorded.

</details>

<a id="frame-edges-member-detectors-fe-06"></a>

### FE-06 -- scan_roll --no-shading with --correct (or a --no-shading walk proposed later) feeds uncorrected prescans to a detector validated only on corrected ones, unguarded

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/scan_roll.py:164-165`, `tools/scan_roll.py:626-632`, `tools/scan_roll.py:332-338`, `rps7200/direct.py:3968-3972`, `rps7200/direct.py:3996`, `research/frame-edge/build_dataset.py:6-12`, `research/frame-edge/build_dataset.py:82-89`

The combination is accepted silently. Uncorrected prescans carry the ~39% x-falloff and column pattern that every member's relative thresholds were never fitted on. Examples are TOP_BAND 6% of the clearest column, LEVEL_FLOOR 0.85 of B, and stepline's base-from-brightest-column. An in-memory test with a synthetic linear falloff of up to 39% still read edges correctly, so the effect on real film is unmeasured rather than shown wrong. It is a domain the owner has no evidence for, and it drives the film.

**Evidence (from the code):**

```text
`ap.add_argument("--no-shading", action="store_true", help="skip calibration entirely; scans come back striped")`, then `shading=not args.no_shading` is passed to s.scan_roll. In the driver, `prescan_image, _ = self.prescan(..., shading=shading, ...)` goes straight to `walk.observe(index, prescan_image)` and `_aim_frame`. The only up-front checks are dpi-correctability and `unread_at`, and neither looks at shading. The study's inputs were `image, rec = library.corrected(entry)` for 300 dpi entries and prescan.tif 'as delivered', both shading-corrected.
```

**Failure scenario:** An operator runs `scan_roll.py --no-shading --correct` to save calibration time. Frames are aimed from striped, falling-off prescans with no warning that the detector was never validated there.

**Fix:** Refuse --correct and --correct-dry-run with --no-shading, as --correct is already refused at unreadable prescan dpis. Or at least print a warning, and record in the correction note that the prescan was uncorrected.

<details><summary>Second reader's check</summary>

--no-shading (scan_roll.py:164-165) is passed as shading=not args.no_shading (scan_roll.py:632). The driver's prescan(shading=shading) result goes to walk.observe and _aim_frame (direct.py:3968-3972, 3996). The pre-flight checks at scan_roll.py:387-392 look at the dpi and film only. The study built its inputs through library.corrected or the delivered prescan.tif (build_dataset.py:82-89). It is unverifiable here whether every study frame was in fact corrected: legacy entries were recorded with their correction state, and the data is absent. So 'validated only on corrected' is the documented intent rather than a proven fact. The combination is still accepted silently and records nothing about it.

</details>

<a id="frame-edges-member-detectors-fe-08"></a>

### FE-08 -- propose_centred excludes a frame's own summary by list index, not frame number, so duplicate numbers self-validate and it disagrees with EdgeWatch on the same folder

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/frame_edges/propose.py:221-245`, `tools/frame_edges/watch.py:125-137`, `tools/frame_edges/watch.py:183-185`, `rps7200/session.py:641-669`, `rps7200/session.py:498-503`, `tools/scan_roll.py:268-292`

**Doc claim:** tools/frame_edges/gapmodel.py:66-67: 'The roll ... supplies two things, each from the *other* frames only, so a frame never validates itself'.

gapmodel and chroma rely on the context holding only other frames ('each from the *other* frames only, so a frame never validates itself'). With two prescans under one number, propose_centred (scan_roll --approved) reads each against the other copy: its own gap width and base colour confirm itself, and chroma's roll_support counts itself. The window's EdgeWatch.load keeps only the last copy. The two paths that are meant to give 'what runs here is what runs there' therefore propose different positions for the same folder.

**Evidence (from the code):**

```text
propose_centred: `others = [s for j, s in enumerate(summaries) if j != i and s is not None]`, keyed by position, then `notes[n] = note` and `offsets[n] = mm`, where a later frame with the same n overwrites the earlier one. EdgeWatch.add replaces a frame with the same number (`old.image, old.version = image, version`), and `_others` excludes by number. walked_prescans appends every record with no de-duplication, and renumbered documents that 'Where two runs of a merged file put two frames on one place, both are kept there'. hold_from_walk calls `frame_edges.propose_centred(frames, film=film)` on that list.
```

**Failure scenario:** A merged roll folder lists frame 6 twice. `scan_roll.py --approved` reads frame 6 against its own twin: its gap is 'confirmed by the roll' and a deep gap that the window refuses is placed and held. The window shows a different proposal for frame 6.

**Fix:** Exclude by frame number in propose_centred, and de-duplicate by number with the same rule EdgeWatch uses (last one wins) before reading. Or have walked_prescans return one record per number.

<details><summary>Second reader's check</summary>

propose_centred excludes by list index (propose.py:236), and later duplicates overwrite notes and offsets by number (propose.py:238-240). EdgeWatch.add replaces by number and _others excludes by number (watch.py:125-137, 183-185). walked_prescans appends every record without de-duplication (session.py:641-669), and renumbered documents that both frames are kept at one place (session.py:498-503). hold_from_walk feeds that list to propose_centred (scan_roll.py:~320-330), and its held dict takes the last reference per number. Reach is narrow: the renumbered docstring says no stored manifest currently has such a collision.

</details>

<a id="frame-edges-member-detectors-fe-a2"></a>

### FE-A2 -- _aim_frame logs 'by {agreed} (from {chose})', keys the frame_edges note never has, so every in-walk move is logged with no attribution

**Severity** low · **Category** error-handling · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3469`, `rps7200/direct.py:3474`, `rps7200/direct.py:3503`, `rps7200/direct.py:3508-3509`, `tools/frame_edges/propose.py:165-176`, `tools/frame_edges/propose.py:274-277`

This is a leftover of the older ensemble's detail shape. With frame_edges as the only reader, the operator log for every in-walk move reads 'X u by  (from ?)', and the dry-run and in-place lines read 'by ' with nothing. Which members agreed is available in edges.left/right.note ('edge: changepoint,stepline'), but the log does not use it. The persisted ensemble does keep the edge notes, so no stored data is lost; only the live log is blank.

**Evidence (from the code):**

```text
direct.py: `agreed = "+".join(detail.get("agreed", []))` and `self._log(f"frame {index + 1}: {say_units(decision)} by {agreed} " f"(from {detail.get('chose', '?')})")`. The centring note carries only `edges`, `width`, `reason`, `source`, `units` and `columns`, and judge adds `members = []`. There is no `agreed` or `chose`.
```

**Failure scenario:** An operator watching a --correct roll sees 'frame 4: +12.7 u by  (from ?)' and cannot tell which detectors agreed or whether it was a one-vote lone gap. For that, only 'source' says 'unconfirmed', and it is not logged here.

**Fix:** Build the attribution from note['edges'][side]['note'] and note['source'], or have WalkReader.judge populate 'agreed' and 'chose' from the voted sides.

<a id="frame-edges-member-detectors-fe-07"></a>

### FE-07 -- CLAUDE.md says base is known by its colour ratio; the detector that moves film does not use that absolute ratio to identify base

**Severity** info · **Category** doc-mismatch · **Verdict** partly

**Where:** `CLAUDE.md:527-531`, `tools/frame_edges/chroma.py:189-190`, `tools/frame_edges/chroma.py:849-853`, `tools/frame_edges/gapmodel.py:1042-1072`, `tools/frame_edges/changepoint.py:119-125`

**Doc claim:** CLAUDE.md:529

CLAUDE.md:529 presents the absolute C-41 base ratio as how base is recognised. The frame-edge members use it at most as a 0.85 confidence factor in chroma, and gapmodel uses consistency with the roll's own base colour, not an absolute ratio. This is background prose that could mislead someone debugging, not a code defect.

**Evidence (from the code):**

```text
CLAUDE.md:529: 'Base is known by its colour ratio (R/G 2.1-2.3, B/G ~0.53) and a straight full-height edge.' In code, chroma only scales confidence: `if ctx.get("film_type") == "c41" and not (C41_RG[0] <= rg <= C41_RG[1] and C41_BG[0] <= bg <= C41_BG[1]): trust *= 0.85`, with windows of 1.85-2.5 and 0.46-0.66. gapmodel compares a run with the roll's own median colour (`_colour_off`, COLOUR_TOL 0.03), never with an absolute ratio. changepoint says '(Base colour is stable within one gain setting ... but the dups at other gains move it, so it is not used.)' stepline has no colour test. On B&W no colour rule applies at all.
```

**Failure scenario:** A stock whose base reads R/G 1.9 on this scanner is expected to be rejected, or a scene area with base-like ratios is expected to be accepted by colour. Neither happens, and the explanation in CLAUDE.md sends the investigation the wrong way.

**Fix:** Reword CLAUDE.md:529 to describe what the members use: dominance, flatness, the straight gate edge, the roll's own base colour in gapmodel, and the C-41 ratio only as a confidence factor in chroma.

<details><summary>Second reader's check</summary>

CLAUDE.md:527-531 is written as a statement about film ('Base is known by its colour ratio ... and a straight full-height edge'), explaining why level-and-flatness detectors failed. It is not an explicit claim about which code gates on the ratio. The code facts are as cited: chroma applies the C-41 window only as a confidence factor of 0.85 (chroma.py:189-190, 849-853); gapmodel compares against the roll's own median colour with COLOUR_TOL 0.03 (gapmodel.py:281, 1042-1072); changepoint explicitly does not use colour (changepoint.py:119-125). A reader could be misled, but this is an overstatement in background prose, not a broken promise.

</details>

<a id="frame-edges-member-detectors-fe-09"></a>

### FE-09 -- Member geometry is literal 428-column constants, while the gate that guarantees 428 columns comes from framing.PRESCAN_COLUMNS

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `tools/frame_edges/stepline.py:187-193`, `tools/frame_edges/chroma.py:191-196`, `tools/frame_edges/changepoint.py:221-244`, `tools/frame_edges/gapmodel.py:194-218`, `tools/frame_edges/propose.py:44`, `tools/frame_edges/propose.py:85-99`

This answers the stepline:188-193 question. Today every member sees exactly 428 columns, because _downscaled refuses any other width. The device's 600 and 900 dpi widths (860, 862, 1292) are refused, and so is any 300 dpi pass that is not 428 wide. So there is no live mis-scaling. The two, however, have separate homes: if framing.PRESCAN_COLUMNS were changed, for example to accept a 430-column pass, SCALE would follow but none of the members' column constants would. Height-dependent constants assume about 276 rows after trimming (MIN_ROWS, BAND, TREND_ROWS, SWEEP_MIN_BINS), and no guard checks the height.

**Evidence (from the code):**

```text
stepline: `FRAME_MIN = 395.0` and `IMPLIES_BORDER = 428.0 - FRAME_MIN`. chroma: `MIN_FRAME_COLS = 420`. changepoint: `MAX_OUTER = 100` and `MAX_GAP = 40`. gapmodel: `G_PRIOR = 17.0`, `DEEP_REACH = 40.0` and more, all in columns of a 428-wide prescan. The only thing that makes them valid is `SCALE = int(PRESCAN_COLUMNS)` in propose, with `if w == SCALE: return img, 1; if w % SCALE: return None`.
```

**Failure scenario:** Not a failure today. It would become one if someone edited PRESCAN_COLUMNS, or relaxed _downscaled to resample non-multiples, without re-deriving the members' thresholds.

**Fix:** Assert in propose (or a test) that SCALE == 428, the width the members were fitted on, instead of deriving SCALE from the framing constant. Refuse passes whose height is far from the study's roughly 286 rows.

<details><summary>Second reader's check</summary>

The constants exist as cited: stepline FRAME_MIN=395.0 and IMPLIES_BORDER=428.0-FRAME_MIN (stepline.py:187-193), chroma MIN_FRAME_COLS=420 (chroma.py:196), changepoint MAX_OUTER=100 and MAX_GAP=40 (changepoint.py:223, 227), gapmodel G_PRIOR=17.0 and DEEP_REACH=40.0 (gapmodel.py:198, 218). _downscaled admits only w==SCALE or a multiple (propose.py:85-99), and SCALE=int(PRESCAN_COLUMNS) (propose.py:44). No height check exists. There is no live mis-scaling at 428 columns. The multiple-of-428 path does exist, and it is wrong for the move (FE-03) rather than for the member constants, because members always see 428 after averaging.

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Walked prescans the detector re-reads on reopen and for scan_roll --approved | rolls/<roll>/prescanNN.tif | TIFF, uint8 RGB (H x 428 x 3 at 300 dpi), written through export.write/tiff.write, quarter-turned or mirrored by the (prescan_rotation, prescan_flipped) pair in the walk record | corrected (that day's shading; no record in the file says so) | rps7200/session.py:_file -> FrameWriter._write -> export.write (window walks); tools/scan_roll.py --dry-run | tools/gui.py:read_survey (preview.unorient) -> EdgeWatch.load -> propose.read_frame; tools/scan_roll.py:hold_from_walk -> propose_centred | Lossless for the corrected pixels: quarter turns and mirrors invert exactly, and a uint8 TIFF round-trips. The detector input on reopen therefore equals the live result.image. It is not raw, and it carries that day's correction, not today's. |
| In-walk correction record: the detector's voted note | rolls/<roll>/survey.json \| roll.json -> frames[].registration.correction.ensemble, .decision_mm, .outcome; frames[].registration.base | JSON. ensemble = centring note {edges:{left/right:{state,x,x_top,x_bottom,outer,note}}, width, reason, source, units (rounded to 3 dp), columns (rounded to 3 dp), members: []}. decision_mm is rounded to 4 dp. base = {reader:'frame_edges', summarised:N} | derived | rps7200/direct.py:_aim_frame (3457-3461) via marks['correction'] (4055-4056), marks['base'] (3996); manifests written by session.py / tools/scan_roll.py | tools/scan_roll.py:708-715 (printing); humans; not re-read by the detector | No. Member answers and any member failure (vote debug) are dropped, and the context membership (which frames and prescan versions) is recorded only as a count. There is no detector version, and the arrival prescan it read is not kept raw without debug filing. |
| Sheet positions produced from the detector's proposals | rolls/<roll>/approved.json (offsets, sources) | JSON, offset_mm float and source string per frame | derived | tools/gui.py sheet (on_scan_chosen), seeded from EdgeWatch.progress()/propose_centred | tools/gui.py:read_approved -> Approved -> DirectScanner._hold_to_approved | Keeps the snapped move only. The detector's edges and members are not kept, and on reopen the sheet re-proposes with today's code on prescanNN.tif. |
| Walk prescan library entries (the raw form of the same passes) | library/<entry>/scan.tif + raw.bin(.gz) + shading.npz + ccd_mask.bin + scan.json (roll_membership kind 'prescan') | uint8 RGB raw decode plus the scanner's bytes, reference and mask | raw | rps7200/session.py:_file(raw_image=rf.raw_prescan) on dry-run walks; debug filing for hold/aim prescans | never by tools/frame_edges; research/frame-edge/build_dataset.py reads 300 dpi entries through library.corrected | Raw and exact. Re-running the detector on it needs library.corrected(entry), which applies today's correction code, so the answer equals the live one only while that code and the reference reduction are unchanged. The walk's context must be rebuilt from its sibling entries through roll_membership. |
| Study frames and stored answers the parity test compares against | research/frame-edge/data/frames/<id>.npy, data/manifest.json, results/ensemble_v2/iter01/results*.json, mirrored*.json (and under lib2/) | float32 .npy of corrected pixels in native counts (uint8 and uint16 sources); JSON answers | corrected | research/frame-edge/build_dataset.py, run.py | tests/test_frame_edges_parity.py (only with FRAME_EDGE_PARITY=1 and the data present) | Absent from this checkout (research/frame-edge/data does not exist), so parity is unverifiable here. By diff, the copies are textually the scored modules apart from the context plumbing. |

**Second reader's corrections to this table:**

The table is broadly accurate. Three refinements:
(1) prescanNN.tif is written from session.py:2549-2560 via self._file(seq, number, rf.prescan, dict(rf.prescan_meta ...)). Whether export.write keeps the uint8 corrected pixels exactly was not re-verified here; the 'lossless' claim rests on that.
(2) The in-walk correction note has no 'agreed' or 'chose' keys, although direct.py:3469/3509 reads them (see FE-A2). The ensemble note shape as listed is correct.
(3) Under 'Walk prescan library entries': re-running the detector on a stored raw entry through library.corrected gives the live answer only if (a) today's correction equals that day's, (b) the rotation/mirror is the same (the library holds the upright decode, which the live path also saw), and (c) the roll context is rebuilt from the same frames in the same arrival-only order. For the in-walk path, that means only the frames observed before this one (WalkReader._summaries), not all siblings. The sheet path (EdgeWatch/propose_centred) uses all other frames, so in-walk and sheet answers for the same frame legitimately differ, and re-derivation must know which path it is reproducing.

## What the operator can do

- Choose the film type in the window or with scan_roll --film. negative and bw get the four-member reader; positive and kodachrome get none (legacy 36 mm aiming, or SKIPPED on the sheet).
- Walk a strip at 300 dpi and have EdgeWatch propose centred positions while the prescans arrive, or reopen a stored walk and have it re-proposed from rolls/<roll>/prescanNN.tif.
- Extend a walk. The old frames stay and are re-read against the new ones once the walk finishes.
- Run scan_roll --correct or --correct-dry-run to aim each frame in-walk through WalkReader, or --approved <walk> to hold frames to propose_centred's positions for an earlier walk.
- Prescan at 600 or 900 dpi. Every frame is refused by width; the window warns (FR-13) and scan_roll refuses --correct.

## What the operator should not do

- Scan slides or positives with the film left at its default 'negative'. The detector then reports frames as measured and centred (FE-02) instead of refusing.
- Combine --no-shading with --correct, or propose a sheet from a --no-shading walk. The detector was only ever fitted on shading-corrected prescans (FE-06).
- Treat the green 'frame edges N/N' light as proof that all four members ran. A member that raises is dropped silently (FE-01).
- Change framing.PRESCAN_COLUMNS, or relax propose._downscaled, without re-deriving the members' 428-column constants (FE-09) and fixing centring's k>1 scaling (FE-03).
- Merge two walks with overlapping frame numbers into one roll folder and then use scan_roll --approved on it. Duplicates validate themselves (FE-08).

## Mistakes nothing guards against

- scan_roll.py defaults --film to negative and the window defaults v_film to negative. A slide walk run without changing it gets 'measured, 0' proposals, and nothing warns.
- Reopening a walk whose manifest has no recorded film uses whatever film the window is currently set to.
- --no-shading together with --correct or --dry-run is accepted without comment.
- A member failure, for example after a dependency upgrade, produces no message anywhere: not the log, not the light, not the manifest, not CI.
- A blank or all-base frame that passes the contrast test is given an interpolated 'from neighbours' position rather than 'not placed' (FE-04).

## Dataflow notes

**How images enter.** Three routes, all ending in propose.detect(image, film, roll):

- **Live walk (window).** DirectScanner.scan_roll (rps7200/direct.py:3968-3972) takes prescan(shading=True, depth 8), a corrected uint8 (H x 428 x 3) in transport column order; decode_index turns bottom-up passes, rows only. The same array goes two ways:
  - to StripWalk.observe/judge through WalkReader (propose.py:249-283), for in-walk aiming;
  - through session._deliver (session.py:2705-2730; preview.downscale is a no-op below 1400 px) as result.image to the window, where gui.py:3914 calls EdgeWatch.add.
- **Reopened walk.** gui.read_survey (gui.py:5104+) reads rolls/<roll>/prescanNN.tif (uint8, corrected, oriented) and un-orients it with preview.unorient using session.prescan_arrangement, then EdgeWatch.load (gui.py:2986).
- **scan_roll --approved.** hold_from_walk (scan_roll.py:223-306) does the same un-orienting, then calls propose_centred.

All three hand the members the same orientation, the transport's column order. The members are mirror-symmetric by construction, so a wrong mirror flag upstream would flip the sign of the move, and nothing inside the detector could notice.

**Transform.**
1. propose.detect (propose.py:119-136) checks the film label: c41 or bw, or refuse.
2. _downscaled (85-99) converts to float32, keeps RGB, and returns exactly 428 columns or averages down a multiple of 428. Anything else is refused, which is the real gate; READ_AT_DPI is only advisory.
3. ctx = {film_type, dtype = source dtype ('uint8'), roll = Summary records of the other frames}.
4. vote.detect (vote.py:135-155) calls each member through _member, which abstains on exceptions. vote_v2 then combines them per side: NO_FILM needs at least 2, an EDGE cluster within 1 column needs at least 2, then PICTURE_TO_BORDER at least 2, then lone_gap. ALL_BASE is never produced.
5. _scaled multiplies positions by k.
6. centring (153-177) runs centre.decide at 428 columns (interval logic, 2-unit agreement, SMALLEST_MOVE deadband), converts units to columns to mm (columns * APERTURE_MM / width), and sets source to measured, unconfirmed, refused or none.
7. propose_centred/EdgeWatch then run fill_refused (framing.fill_from_neighbours, Theil-Sen over at least 3 placed frames).

**Context.** roll.summarise (roll.py:39-56) builds each Summary from:
- chroma.frame_summary()['best']['level'] (ln counts, compared only within the same dtype and within ROLL_TOL 0.05);
- gapmodel._both_sides, giving full-gap widths for G and base colours (log R/G, log B/G) for the colour gate.
Exceptions in either are swallowed. Which frames make up the context differs by path:
- EdgeWatch and WalkReader exclude the frame by number;
- propose_centred excludes it by list index;
- WalkReader summarises arrival prescans only, while the sheet summarises the final ones.

**Output.**
- Window: EdgeWatch.progress offsets and notes feed the sheet, then approved.json offset_mm/source, then Approved, then _hold_to_approved.
- In-walk: judge returns (offset_mm, note) to _aim_frame, then hold/verify. The note is persisted as registration.correction.ensemble, with members [] and base {summarised: N}.

**Verified.**
- Member copies are identical to research/frame-edge/algos changepoint_v2, chroma (round 1), stepline (round 1) and gapmodel_v2, except for the ctx['roll'] Summary plumbing, which replaces the study's module caches. vote, sides and decide match the study.
- An in-memory fuzz (60 synthetic frames covering dense, fogged, B&W-like and dead-column cases) produced no non-finite x/lo/hi/outer/x_top/x_bottom/conf and no exceptions. gapmodel raises ZeroDivisionError only on an all-zero frame, and there it abstains.
- The study was fitted on corrected pixels (build_dataset.py uses library.corrected for 300 dpi entries and delivered, corrected prescan.tif), which matches what the window feeds. The library's raw walk-prescan entries need library.corrected to reproduce the live input.
- These runs were in memory only (python -B, PYTHONDONTWRITEBYTECODE=1); nothing was written.
