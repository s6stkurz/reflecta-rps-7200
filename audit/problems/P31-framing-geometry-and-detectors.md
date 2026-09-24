# P31 -- Framing: two geometry models, a search window too small, one voter can move film

**Severity** high · **Group** E: Outputs, measurement and framing · **Reported independently by** 11 findings in 4 areas

[Back to the summary](../README.md)

## The problem

- Two contradictory frame-geometry models coexist: the legacy path in `framing.py` aims
  to a 36 mm frame, while `frame_edges` uses the measured 350.6 units -- and the roll uses
  the 36 mm one for positions. Tests pin both.
- `SEARCH_MM` (9.0 mm, about 105 px at 300 dpi) is smaller than the largest single command
  the sheet can ask for (param 87, about 110 px), so such holds can never verify.
- One detector member alone can move film: `lone_gap` -> `decide()` -> `WalkReader.judge`,
  and `StripWalk` skips `combine()`.
- 600 and 900 dpi prescans (offered in the window) are never read by the detector: the
  device returns 860/862 and 1292 columns, not multiples of 428; nothing warns.
- Member exceptions are not contained on the roll path (division by zero on near-black
  frames). `scan_roll.py --approved` uses `prescanNN.tif` without un-orienting it.

## Fix plan

1. Retire the 36 mm model; one geometry source (`frame_edges.FRAME_WIDTH_UNITS`) used by
   the roll, the window and the demo.
2. Derive `SEARCH` from the largest command the sheet can send, in param units.
3. Require agreement of at least two members before a proposal can move film.
4. Refuse (or warn loudly) when the walk's prescan dpi is one the detector cannot read.
5. `try`/`except` per member on the roll path; a failed member abstains.

## Evidence (from [FE-02](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-02))

**Where:** `tools/frame_edges/vote.py:90-109`, `tools/frame_edges/centre.py:233-244`, `tools/frame_edges/propose.py:137-143`, `tools/frame_edges/propose.py:240-243`, `rps7200/framing.py:1729-1733`, `rps7200/direct.py:2985-3045`, `tools/scan_roll.py:252-256`, `tools/scan_roll.py:283-289`

```text
**One member's word.** `def lone_gap(...)`: `for name, s in sides.items(): if s.state != EDGE or s.x is None or s.outer is None: continue ... if not elsewhere: return Side(EDGE, x=s.x, ..., conf=0.3, ..., note=f"gap with neighbour, one vote: {name}")` (vote.py:90-101). vote_v2 returns it whenever the two-vote rule fails (104-109).

**One side is enough.** decide(): `if points: ... u = float(np.mean(points))`. With one EDGE side and the other REFUSE, lo and hi stay infinite and the move stands (centre.py:233-244).

**It is labelled, then returned.** centring: `note.update(source="unconfirmed" if lone else "measured", ...)` / `return columns_to_mm(columns, width), note` (propose.py:141-143).

**Reader path bypasses combine.** StripWalk.judge: `if self.reader is not None: decision, detail = self.reader.judge(int(number), image) ... return decision, dict(detail)` (framing.py:1729-1733).

**No source check before the move.** _aim_frame: `decision, detail = walk.judge(index, image)` ... `agreed = "+".join(detail.get("agreed", []))` ... `self._hold_to_approved(index, image, prescan_resolution, _Aim(offset_mm=decision, reference=image), ...)` (direct.py:2985-3042).

**The CLI holds every proposal.** `held = {n: Approved(number=n, offset_mm=float(offsets[n]), reference=im, source=...) for n, im in frames if n in offsets}` (scan_roll.py:254-256). This includes 'unconfirmed' and 'neighbours' offsets, and only a count is printed (283-289).
```

**Failure scenario:** The operator ticks 'correct' (or passes --correct) for an unattended roll. On one frame a black stripe in the scene, about 45 columns in, forms a straight band that only changepoint reads as a gap with the neighbour beyond. No other member places an edge elsewhere on that side, so lone_gap stands. decide() gives about -40 units, and the hold loop drives the film about 4 mm, framing the scan off by the width of that stripe. Nothing asks, and the log does not say it was one member.

**Second reader's check:** Confirmed. `lone_gap` returns an EDGE side resting on one member (vote.py:90-101), and `vote_v2` uses it whenever round 1 fails (104-109). `decide` averages whatever points exist, so a single EDGE side stands while the other side is REFUSE (centre.py:233-244, with lo/hi left infinite). `centring` labels the result 'unconfirmed' but still returns the offset (propose.py:139-143). With a reader installed, `StripWalk.judge` returns `reader.judge` directly and never calls `combine` (framing.py:1729-1733). `_aim_frame` moves on any non-None decision and never reads `detail['source']` (direct.py:2985-3045). Its log reads `detail.get('agreed', [])` and `detail.get('chose','?')`, keys the reader note does not have, so the line says 'by  (from ?)'. TODO.md:214-218 states the two-member rule. Two nuances. The sheet's confirmation dialog does name these frames explicitly as 'one detector, uncorroborated' (gui.py:2911-2918), so that path discloses them, though it still does not require separate consent. `scan_roll --approved` holds every proposal, including 'unconfirmed' and 'neighbours', after printing only a count (scan_roll.py:252-256, 283-289). Moves are bounded by the per-command cap, the hold loop and the 12 mm `ROLL_TRAVEL_LIMIT_MM` (framing.py:1169, 1773-1775).

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [FE-02](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-02) | frame-edges-detectors | high | confirmed | One detector member alone can move film: lone_gap, then decide(), then WalkReader.judge, and StripWalk skips combine(). scan_roll --approved also holds 'unconfirmed' and 'neighbours' positions without review | tools/frame_edges/vote.py:90-109, tools/frame_edges/centre.py:233-244, tools/frame_edges/propose.py:137-143 |
| [FU-07](../areas/framing-units.md#framing-units-fu-07) | framing-units | medium | confirmed | Two contradictory frame-geometry models coexist; the legacy path aims to a 36 mm frame that the current measurement says is wrong | rps7200/framing.py:3-7, rps7200/framing.py:42, rps7200/framing.py:285-290 |
| [FU-08](../areas/framing-units.md#framing-units-fu-08) | framing-units | medium | confirmed | SEARCH_MM (9.0 mm) is smaller than the largest single command the sheet can ask for (param 87, 9.39 mm), so such holds can never verify | rps7200/framing.py:841-857, rps7200/framing.py:1061-1063, tools/gui.py:217-230 |
| [UT-08](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-08) | uncited-tests-vs-findings | medium | confirmed | SEARCH_MM (±105 px at 300 dpi) is shorter than one param-87 move (~110 px); test_roll pins it with a stale bound, and the cited framing tests do not touch it | rps7200/framing.py:854-857, rps7200/framing.py:1061-1063, rps7200/session.py:93-95 |
| [UT-09](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-09) | uncited-tests-vs-findings | medium | confirmed | Tests pin two contradictory frame-width models, 36 mm in framing and 350.6 units in frame_edges; the roll uses the 36 mm one for positive and Kodachrome film | tests/test_frame_position.py:299-313, tests/test_frame_position.py:166-167, tests/test_ensemble.py:129-134 |
| [FU-05](../areas/framing-units.md#framing-units-fu-05) | framing-units | medium | confirmed | 600/900 dpi prescans (offered in the GUI) are silently never read by the frame-edge detector; docstring says they are | tools/frame_edges/propose.py:14-17, tools/frame_edges/propose.py:51-65, tools/frame_edges/propose.py:95-98 |
| [FE-05](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-05) | frame-edges-detectors | medium | confirmed | No 600 or 900 dpi prescan is ever read: the device returns 860/862 and 1292 columns, which are not multiples of 428, although propose.py says those resolutions are supported | tools/frame_edges/propose.py:14-17, tools/frame_edges/propose.py:51-65, tools/gui.py:1751-1752 |
| [FE-A2](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-a2) | frame-edges-detectors | low | found-by-verifier | Frame-edge reads on walks at any prescan resolution other than 300 dpi fail silently: nothing refuses or warns when the walk is set up | tools/gui.py:1751-1752, tools/scan_roll.py:108, tools/frame_edges/propose.py:58-61 |
| [FE-03](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-03) | frame-edges-detectors | medium | confirmed | Member exceptions are not contained on the roll path. gapmodel and stepline divide Python floats by zero on near-black frames, and anything other than ValueError aborts the roll, possibly after the film has moved | tools/frame_edges/gapmodel.py:444-450, tools/frame_edges/gapmodel.py:464, tools/frame_edges/gapmodel.py:487-489 |
| [FU-03](../areas/framing-units.md#framing-units-fu-03) | framing-units | medium | confirmed | tools/scan_roll.py --approved uses prescanNN.tif without un-orienting it | tools/scan_roll.py:243-258, tools/gui.py:4740, rps7200/session.py:2026-2029 |
| [CLI-13](../areas/cli-operator-tools.md#cli-operator-tools-cli-13) | cli-operator-tools | medium | confirmed | --approved diverges from the window: ignores the walk's prescan dpi, does not un-orient GUI-walk prescans, and ignores approved.json | tools/scan_roll.py:200-261, tools/scan_roll.py:108-114, tools/gui.py:4722-4744 |
