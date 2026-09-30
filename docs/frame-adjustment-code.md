# Automatic frame adjustment: the code

How the driver decides where each frame of a roll should sit in the aperture,
and how it moves the film there. This describes the code as it stands; the
measurements and decisions behind it are in `docs/frame-adjustment-findings.md`.

> **Distances are in param units** -- one unit is what one increment of the
> `SLIDE` param adds (CLAUDE.md). The code's interfaces still carry
> millimetres (`offset_mm`, `nudge(millimetres)`, `HOLD_*_MM`); where a
> constant is named in mm here, its value is given in units beside it.

## In one paragraph

A **walk** prescans every frame of the strip without scanning it. A detector
(`tools/frame_edges`) reads the two edges of each frame against the other
frames of the walk and proposes the move that **centres** it. The operator sees
the proposals on the **contact sheet**, changes any, and ticks the frames to
scan. Each ticked frame becomes an `Approved`: an offset relative to where the
walk saw the frame, plus the prescan it was decided on. During the roll the
driver **holds** each frame to its offset: one sub-frame `SLIDE` command, a
fresh prescan, a phase-correlation against the approved prescan, and repeat
until the residual is below the smallest move the transport can make. Whatever
happens, the frame is scanned; the outcome is recorded, not enforced.

## The seam

`rps7200` never imports `tools/frame_edges`. The detector is handed in:

| where | what |
|---|---|
| `tools/gui.py:8424` | `session.edge_reader = frame_edges.walk_reader` |
| `tools/scan_roll.py:476` | `scan_roll(..., edge_reader=frame_edges.walk_reader)` |
| `rps7200/session.py:1189` | `ScanSession.edge_reader`, None by default |
| `rps7200/session.py:1852` | passed on to `DirectScanner.scan_roll` |
| `rps7200/direct.py:3273` | `StripWalk(reader=edge_reader(film) if edge_reader else None)` |

`walk_reader(film)` (`tools/frame_edges/propose.py:249`) returns None for a
film the detector does not read, and then the driver's older strip-level
detector decides (see *Legacy code*).

## Data flow

```
WALK  (scan_roll --dry-run, or the window's walk)
  DirectScanner.scan_roll ── prescan per frame ──► survey.json + prescanNN.tif
                                                     │
PROPOSE                                              ▼
  window:  EdgeWatch (thread) ──► propose_centred ──► (offsets_mm, notes)
  CLI:     hold_from_walk ──────► propose_centred ──┘
                                                     │
APPROVE  (window only)                               ▼
  _snap_proposals ─► _merge_kept ─► contact sheet ─► approved_from_sheet
  ─► on_scan_chosen ─► approved.json + Roll(approved=[Approved, ...])
                                                     │
HOLD                                                 ▼
  DirectScanner.scan_roll, per frame:
    prescan ─► approved?  ── yes ─► _hold_to_approved(source=<sheet's source>)
                          └─ no, and --correct ─► _aim_frame
                                 ─► StripWalk.judge ─► _hold_to_approved(source="ensemble")
    _hold_to_approved: hold_plan ─► nudge ─► prescan ─► measure_shift_mm ─► hold_plan …
    ─► scan ─► registration marks into the manifest and the library entry
```

There are two ways into the hold loop:

- **Approved path**, the normal one. The positions come from a finished walk,
  are seen by a person, and are held to during a later roll. It is driven by the
  window's contact sheet, or by `scan_roll.py --approved DIR` without the window.
- **Aim path**, which is `--correct`. The position is judged frame by frame
  *during* the roll, from the prescan just taken, and nobody sees it first. It
  applies only to frames that have no `Approved`.

## 1. The walk

`DirectScanner.scan_roll` (`rps7200/direct.py:3143`) is the one loop behind a
walk, a roll and a held roll. Per frame (`rps7200/direct.py:3340-3460`):

1. `prescan(resolution=prescan_resolution)`.
2. `frame_contrast` below `blank_contrast` means the end of the film.
3. `registration(prescan, window)` fills `marks` with the legacy `film_bounds`
   reading (x0, x1, offset, shortfall). It is logged but moves nothing.
4. If a `StripWalk` exists, `walk.observe(index, prescan)` → `marks["base"]`.
5. Then the approved branch, the aim branch, or neither (section 5).
6. It yields a `RollFrame` (`rps7200/direct.py:385`), including `prescan_before`
   when aiming replaced the prescan.

Writers:

- **CLI**, `tools/scan_roll.py`. `--dry-run` writes `survey.json` and a
  `prescanNN.tif` per frame, plus `prescanNN-before.tif` when a correction moved
  the film (`tools/scan_roll.py:495-510`).
- **Window.** `ScanSession._roll` (`rps7200/session.py:1654`) does the same for a
  `Roll(dry_run=True)` job (`rps7200/session.py:917`).
- **Extending a walk.** `extend_walk` adds to an earlier survey rather than
  replacing it.
- **Reading a walk back.** `session.walked_prescans` (`rps7200/session.py:639`)
  is the one reader, shared by the contact sheet and by `--approved`.

### `StripWalk`

`StripWalk` (`rps7200/framing.py:1611`) is the only memory a roll has across
frames. It is built only when `correct` or `correct_dry_run` is set
(`rps7200/direct.py:3273`); a plain walk and an approved roll have none.

| member | role |
|---|---|
| `reader` | the injected detector. With one, `observe` / `judge` delegate to it |
| `observe(n, image)` | take this frame's evidence; with no reader, re-arm the base level from the strip |
| `judge(n, image)` | `(offset_mm \| None, detail)` for this frame |
| `record(n, settled, travelled, verified)` | where the frame was left; an **unverified** move drops the frame from the history |
| `affordable(offset)` | whether the roll-wide travel budget allows this move |
| `landed()` / `missed(why)` | three misses running call `stop` |
| `stop(why)` | aiming off for the rest of the roll; frames are still scanned |

## 2. Reading edges and proposing a position

`tools/frame_edges` is a copy of the `research/frame-edge` study's `ensemble_v2`
detector (`tools/frame_edges/__init__.py`). `tests/test_frame_edges_parity.py`
holds it to the study's stored answers.

### Per side: four members and a vote

`vote.detect` (`tools/frame_edges/vote.py:112`) runs the four `MEMBERS`
(`changepoint`, `chroma`, `stepline`, `gapmodel`; `tools/frame_edges/vote.py:41`),
each returning an `EdgeResult`. It then votes each side into one of the states
in `tools/frame_edges/sides.py`:

| state | meaning |
|---|---|
| `edge` | base shows; `x` is where it meets the picture |
| `picture_to_border` | no base; the picture runs off the aperture |
| `all_base` | blank frame or strip end |
| `no_film` | empty gate |
| `refuse` | no agreement |

The vote rules (`vote.vote`, `tools/frame_edges/vote.py:51`):

- An edge stands when at least `MIN_VOTES = 2` members agree within `AGREE = 1.0`
  column, and they are at least as many as the members saying border.
- Otherwise border stands on two votes.
- Anything else is refused, **except** a single member's gap with the
  neighbouring frame's picture beyond it (`vote.lone_gap`, `vote.vote_v2`).
- A tie between edge and border goes to the edge.

`gapmodel` uses the strip pitch, and `roll.Summary` / `summarise`
(`tools/frame_edges/roll.py`) carry what each frame tells the others: base
chroma, gap widths and gap colours.

### From two sides to one move

`centre.decide` (`tools/frame_edges/centre.py:70`) returns a `Decision` in
units, positive = picture to the right.

- **Target.** `t = (width - frame_columns) / 2` (`target_base`). It is negative,
  because the frame is 350.6 units wide and the aperture about 344.5: a centred
  frame overhangs both edges and shows no base.
- **Edges.** A left edge with `b_L` columns of base gives `u = -(b_L - t)`; a
  right edge gives `u = +(b_R - t)`, both scaled by `units_per_column`. Two edges
  are averaged and must agree within 2 units.
- **Border sides.** A `picture_to_border` side is a half-open bound. Two border
  sides give the middle of the interval; a lone one refuses.
- **Deadband.** A result smaller than `SMALLEST_MOVE` (2.84 units) is `none`.
- **Bias.** `bias_units` is added to every answer. It defaults to 0 and nothing
  sets it.

### `propose.py`

`tools/frame_edges/propose.py`:

- **`detect`** reads a 428-column prescan, or a whole multiple of it averaged down.
  Any other width is refused. Only colour negative (`c41`) and black-and-white
  (`bw`) are read (`FILM_TYPES`). Slides and Kodachrome have an opaque gap, and
  no member was built for that.
- **`centring`** returns `(offset_mm, note)`. `note["source"]` is:
  - `measured` when two or more members stand behind each side used
  - `unconfirmed` when a side rests on a lone gap-with-neighbour
  - `refused`, or `none` when the gate fired or the frame is all base

  The note also carries `units`, `columns`, `edges`, `width` and `reason`.
- **`fill_refused`** turns `refused` into `neighbours` using the Theil-Sen line
  through the placed frames (`framing.fill_from_neighbours`), or into `none`.
- **`propose_centred(frames, film=...)`** is the whole walk at once. Each frame is
  read against the summaries of every *other* frame, and it returns
  `(offsets_mm, notes)` keyed by frame number.
- **`WalkReader`** / **`walk_reader(film)`** is the same detector frame by frame,
  for `StripWalk`. It has `observe`, `judge` and `reread`; `reread` is the
  driver's second look during a hold.

**Scale handed to the hold loop.** `centre.columns_to_mm` converts with
`APERTURE_MM / width`, the scale `measure_shift_mm` verifies in, not with
`MM_PER_UNIT`. The two differ by 0.2%. See *Units*.

### `EdgeWatch`: reading while the walk runs

`EdgeWatch` (`tools/frame_edges/watch.py:70`) runs one daemon thread per window.

- **Lifecycle.** `begin(film, expected)`, `add(n, image)` for each prescan as it
  lands, `finish()`, `extend()` for more of the same walk, and `load` for a walk on
  disk.
- **Reading.** Each frame is read against the frames before it on arrival. Once
  the walk ends, every frame whose context has grown is read again, so the final
  answer equals `propose_centred`'s (`tests/test_frame_edges.py` checks this).
- **State.** `progress()` returns `Progress(state, done, total, offsets, notes)`.
  The states are `idle`, `reading`, `done`, `failed` and `skipped`.
- **Window side.** The window polls `version` in `_pump` and hands new readings to
  `_ContactSheet.take_readings`.

## 3. Approval

`Approved` (`rps7200/session.py:825`):

| field | meaning |
|---|---|
| `number` | frame on the strip, from 1 |
| `offset_mm` | signed move **relative to where the walk saw this frame**. Never absolute: a sub-frame move does not touch the frame counter, so there is no absolute coordinate. Zero means "leave it where I saw it". |
| `reference` | the prescan array it was decided on; the hold loop correlates against this |
| `reference_entry` | library path of that prescan |
| `rotation`, `flipped` | orientation for the delivered files only |
| `source` | `operator`, or the detector's `measured` / `unconfirmed` / `neighbours` / `none`. It is logged, so the log never claims the operator asked for a detector's number |

**Window path** (`tools/gui.py`):

1. `_snap_proposals` (`tools/gui.py:5666`) puts every proposal on a position one
   command can reach (`snap_offset`, `tools/gui.py:5466`, via `session.plan_nudges`).
   A proposal below one command keeps its note, with `in_place=True`.
2. `_merge_kept` (`tools/gui.py:5691`): positions the operator set win.
   Remembered machine positions (`MACHINE_SOURCES`) are read again rather than
   replayed.
3. `approved_from_sheet` (`tools/gui.py:5969`) gives **every** ticked frame an
   `Approved`, zero offsets included.
4. `on_scan_chosen` (`tools/gui.py:2840`) pins the prescan resolution to the
   walk's, then `_write_approved` (`tools/gui.py:3046`) writes
   `rolls/<name>/approved.json`:

   ```json
   {"roll": ..., "numbering": ..., "frames": [{"number", "offset_mm",
    "rotation", "flipped", "reference_entry", "source"}]}
   ```

   `on_scan_chosen` then submits the `Roll`. It is the only writer of
   `approved.json` and the only submitter of a roll from the sheet.

**CLI path.** `hold_from_walk(folder)` (`tools/scan_roll.py:200`) reads the walk
through `walked_prescans`. It calls the same `propose_centred` and builds the same
`Approved`s, with no operator in between. `--approved DIR` enables it, and it
must run at the walk's prescan resolution (see `measure_shift_mm`).

## 4. From a distance to a command

`DirectScanner` (`rps7200/direct.py`):

- **The law.** `STEP_MM = MM_PER_UNIT` and `OVERHEAD_MM = MM_PER_COMMAND`
  (`rps7200/protocol.py`) mean a command travels **`param + 1.84` units**.
- **`param_for_mm(mm)`** (`rps7200/direct.py:2977`) computes
  `round((|mm| - OVERHEAD) / STEP)`, clamped to `1..MAX_CORRECTION_PARAM`. It is a
  `@staticmethod` so the session planner and the demo take it rather than
  retyping it.
- **`MAX_CORRECTION_PARAM = 87`** (`rps7200/direct.py:2974`), which is
  **88.8 units**. This is the largest move two prescans can still confirm: param
  160 moved 196 px at confidence 27, under the floor of 55. It is also the
  largest param the vendor sends.
- **`nudge(mm)`** (`rps7200/direct.py:2989`) sends
  `slide(0x00 | 0x01, param, value=0x04)`: forward is `00`, back is `01`. It
  returns `{param, forward, asked_mm, requested_mm, clamped, short_mm}`, and it
  logs a clamp rather than refusing.
- **`slide(action, param, value)`** (`rps7200/direct.py:1154`) is the 4-byte
  `SLIDE` payload.

**One command per move.** The hold loop calls `nudge` once per iteration.
`session.plan_nudges` / `deliverable_mm` model chaining for the manual adjuster
and the snapping; the hold loop does not use them.

## 5. The hold loop

### `hold_plan`: the decision table

`hold_plan(target, measured, spent, direction, moves)`
(`rps7200/framing.py:1172`) is pure. It returns `(distance | None, outcome)`,
checked in this order:

| # | condition | outcome |
|---|---|---|
| 1 | `measured is None` | `unverified` |
| 2 | `\|target - measured\| < HOLD_TOLERANCE_MM` (2.84 units) | `held` |
| 3 | `moves >= MAX_HOLD_MOVES` (3) | `not_converged` |
| 4 | residual's sign differs from the previous move | `would_reverse` |
| 5 | `spent + \|residual\| > \|target\| + HOLD_HEADROOM_MM` (18.9 units) | `budget` |
| 6 | otherwise | `move` the residual |

`HOLD_TOLERANCE_MM` is *defined* as what param 1 travels. The loop can never
ask for a move smaller than the transport can make, so it cannot chatter around
the target. **Never reversing within a frame** is how backlash is handled:
after a direction change, two to three commands are swallowed and released
later. Overshoot is accepted and reported instead.

### `measure_shift_mm`: is the film where it should be?

`measure_shift_mm(reference, now)` (`rps7200/framing.py:1007`):

- **Correlation.** It calls `uniformity.register` (`rps7200/uniformity.py:336`),
  a phase correlation with a Hann window that returns `(dy, dx, confidence)`.
  `confidence` is a **z-score**: the peak above the mean of the searched surface,
  in standard deviations of that surface. So it is comparable only at a fixed
  reach.
- **Reach.** Fixed at `SEARCH_MM` (**85 units**), about 105 columns at 428.
- **Orientation.** It runs `register` upright *and* row-flipped, and the stronger
  wins (`row_reversed`). A stored reference can be a bottom-up pass; the flip is
  in y only, so x is unaffected.
- **Refusals.**
  - Below `CONFIDENCE_FLOOR = 55`.
  - A match more than `MAX_DY_MM` (**1.6 units**, 2 px at 300 dpi) off the film
    axis, because the transport moves only in x.
- **Resampling.** A reference of a different shape is resampled, at about half
  the confidence. That is why a commissioned roll is pinned to the walk's prescan
  resolution.
- **Result.** The shift, `-dx × APERTURE_MM / width` (positive = picture
  moved +x), plus a detail dict: `confidence`, `confidence_other`, `dy`, `dx`,
  `px`, `resampled`, `row_reversed`, `reason`.

### `_hold_to_approved`

`DirectScanner._hold_to_approved(index, image, prescan_resolution, approved, …)`
(`rps7200/direct.py:2687`):

```
target = approved.offset_mm            (negated when reverse_hold)
measured = measure_shift_mm(approved.reference, image)
loop:
    want, outcome = hold_plan(target, measured, spent, direction, moves)
    stop if want is None, or should_stop() → "stopped"
    nudge(want); sleep HOLD_SETTLE_S (0.4 s); prescan
    measured = measure_shift_mm(approved.reference, new prescan)
    after move 1: film moved > tolerance the wrong way → "wrong_way", roll_abort
    rejudge(image) says no → "abandoned"
```

It returns `target_mm`, `outcome`, `moves`, `spent_mm`, `final_mm`, `residual_mm`,
`confidence`, `dy`, `row_reversed`, `history` (one measurement detail per pass),
`clamped`, `source`, `roll_abort`, and the last `prescan`. It logs
`frame N: <source> <target> -> <outcome>, now … after k move(s)`.

The target is a constant of the frame. `rejudge` may only **stop** the loop,
never re-aim it: re-aiming on its own noise is how a loop chases itself.

### `_aim_frame`: the `--correct` path

`_aim_frame` (`rps7200/direct.py:2814`) asks `walk.judge` **once**, from the
prescan already taken, then:

| condition | outcome |
|---|---|
| no decision | `abstained` |
| `\|decision\| < HOLD_TOLERANCE_MM` | `in_place` |
| not `walk.affordable` | `budget`, and aiming stops for the roll |
| `correct_dry_run` | `dry_run`, with `would_send {action, param, asked_mm}` |
| otherwise | `_hold_to_approved(_Aim(decision, reference=image), source="ensemble")` |

After the hold, `walk.record(...)` runs, followed by `landed` or `missed`.

`_rejudge_for` (`rps7200/direct.py:2912`) is the second look. It uses
`walk.reader.reread`, or `frame_offset_mm` when there is no reader, and abandons
the frame only when it now reads further out than
`|target| + MAX_CORRECTION_MM` (**22.3 units**).

### Budgets and giving up

| scope | limit | where |
|---|---|---|
| one frame | travel ≤ `\|target\| + HOLD_HEADROOM_MM` (18.9 units) | `hold_plan` |
| one frame | ≤ `MAX_HOLD_MOVES` = 3 commands | `hold_plan` |
| one roll, aim path | `ROLL_TRAVEL_LIMIT_MM` (113.5 units) | `StripWalk.record` / `affordable` |
| one roll, approved path | 3 consecutive non-`held` frames (`HOLD_GIVE_UP_FRAMES`), or a `roll_abort` → holding off; later approved frames record `outcome: "off"` | `scan_roll`, `rps7200/direct.py:3377-3420` |
| one roll, aim path | 3 consecutive misses → aiming off | `StripWalk.missed` |

### What is recorded

- **Manifest.** `marks["approved"]` (hold path) or `marks["correction"]` (aim
  path), without the prescan array, goes into `RollFrame.registration`. That
  lands in `survey.json` / `roll.json` under `frames[].registration`.
- **Library.** The same marks go into `meta["registration"]` of every scanned
  frame (`rps7200/direct.py:3506`), and `library.save` keeps them
  (`rps7200/library.py:280`). This is the data `tools/registration_margin.py`
  re-fits the confidence floor from.
- **Before and after.** When aiming moved the film, the prescan as it arrived is
  kept as `prescanNN-before.tif`, and the moved one replaces `prescanNN.tif`.

## 6. Outcomes

| outcome | set by | meaning |
|---|---|---|
| `held` | `hold_plan` | residual below 2.84 units |
| `not_converged` | `hold_plan` | three moves and not there |
| `would_reverse` | `hold_plan` | the next move would change direction |
| `budget` | `hold_plan`, `_aim_frame` | travel budget exhausted (frame or roll) |
| `unverified` | `hold_plan` | the shift could not be measured |
| `stopped` | `_hold_to_approved` | the operator stopped the job |
| `wrong_way` | `_hold_to_approved` | first move went the other way; the roll stops holding |
| `abandoned` | `_hold_to_approved` via `rejudge` | the frame no longer reads where it was judged to be |
| `off` | `scan_roll` | holding was switched off earlier in the roll |
| `abstained` | `_aim_frame` | the detector gave no decision |
| `in_place` | `_aim_frame` | the decision is below one command |
| `dry_run` | `_aim_frame` | `--correct-dry-run`: logged, not sent |

## 7. Entry points and defaults

| entry | default | effect |
|---|---|---|
| window: walk + contact sheet + *scan* | — | approved path, every ticked frame |
| `tools/scan_roll.py --approved DIR` | off | approved path from a stored walk |
| `tools/scan_roll.py --correct` | off | aim path |
| `tools/scan_roll.py --correct-dry-run` | off | aim path, nothing sent; wins over `--correct` |
| window `v_correct` / `v_correct_dry` | off | aim path, "aim each frame while prescanning" |
| `DirectScanner.scan_roll(correct=, correct_dry_run=, approved=, reverse_hold=, edge_reader=)` | off / off / None / False / None | the one implementation |

**An approved frame beats `correct`** (`rps7200/direct.py:3377`). The sheet
gives every ticked frame an `Approved`, so `correct` never acts on a roll
commissioned from the sheet.

## 8. Units

| constant | where | value |
|---|---|---|
| `COLUMNS_PER_UNIT` | `rps7200/framing.py` | 1.2423 columns of a 300 dpi prescan per unit |
| `COMMAND_COST` / `COMMAND_UNITS` | `framing.py` / `protocol.py` | 1.84 units per command |
| `SMALLEST_MOVE` | `framing.py` | 2.84 units (param 1) |
| `HOLD_TOLERANCE_MM` | `framing.py` | 2.84 units |
| param 87 | `direct.py` | 88.8 units |
| `FRAME_WIDTH_UNITS` | `framing.py` | 350.6 (435.6 columns) |
| `PITCH_UNITS` | `framing.py` | 366.5 |
| aperture | 428 / `COLUMNS_PER_UNIT` | 344.5 |
| aperture | `APERTURE_MM / MM_PER_UNIT` | 345.2 |

**Conversion points:**

- `protocol.units`, `say_units` and `say_command`, for everything a person reads.
- `centre.columns_to_mm`, from detector columns to `offset_mm`.
- The GUI's unit fields multiply by `MM_PER_UNIT`.
- `framing.units_per_column` for detector columns.

**Two scales, 0.2% apart.** The detector counts columns at 1.2423 per unit,
which puts the aperture at 344.5 units. The hold loop verifies in
`APERTURE_MM / width`, which puts it at 345.2. The detector's move is handed
over in the verifier's scale on purpose (`tools/frame_edges/centre.py`), so the
loop lands on the column the detector asked for.

## 9. The demo stand-in

`DemoScanner` (`rps7200/demo.py`) runs the real loop:

- **What it borrows.** `_hold_to_approved`, `_aim_frame`, `_rejudge_for` and
  `HOLD_GIVE_UP_FRAMES` come from `DirectScanner`, with `HOLD_SETTLE_S = 0`.
- **`nudge`** (`rps7200/demo.py:360`) takes `param_for_mm`, `STEP_MM` and
  `OVERHEAD_MM` from `DirectScanner`. It simulates slip and backlash.

It differs from `DirectScanner.scan_roll` in four ways:

- The hold call hard-codes a prescan resolution of 300.
- The hold call passes no `source`, so every approved frame logs as `operator`.
- `walk.observe` runs only in the aim branch.
- The backlash amount uses a retyped `0.1057`.

## 10. Legacy code still present

- **The base-level strip detector.** It includes `edge_bands`, `film_base_from`,
  `frame_offset_mm`, `right_gap_closure`, `predict_offset`, `combine` and the
  `picture_*` / `strip_offsets` helpers (`rps7200/framing.py`). It is **still
  live**: `StripWalk` uses it whenever `reader is None`. That happens on a
  positive or Kodachrome roll, or on a direct `scan_roll(correct=True)` with no
  `edge_reader`. `_rejudge_for` also falls back to `frame_offset_mm`.
- **`film_bounds` / `registration()`** (`rps7200/framing.py:86`). They still fill
  the per-frame `marks`, the drift warning and the metering slice, but move
  nothing.
- **`propose_offsets`**. Superseded by `propose_centred`; only
  `tests/test_ensemble.py` calls it. `fill_from_neighbours` from the same family
  is still used.
- **Not called in production:** `gap_edges` and `registration_error_mm` (study
  tools and tests only).
- **Deleted since (887879c, 2026-09-27):** the per-row gap rule (`Band`,
  `edge_band`, `_row_runs`, `GAP_ROW_AGREEMENT`, `GAP_WIDTH_SPREAD`) and the
  second model of the SLIDE law -- `command_for`, `describe_command`,
  `MAX_PARAM`, `MAX_VERIFIABLE_PARAM`, `LARGEST_*` -- from `rps7200/framing.py`,
  and `DirectScanner.CORRECTION_DEADBAND_MM`. None was called; a second home for
  the transport law beside the live mover is the drift CLAUDE.md warns about.
  `SMALLEST_MOVE` stays.

## 11. Tests

| file | covers |
|---|---|
| `tests/test_frame_edges.py` | edge placement, centring, vote rules, film refusal, 600 dpi, `WalkReader` through `StripWalk`, `EdgeWatch` = `propose_centred` |
| `tests/test_frame_edges_parity.py` | the copy against the study's stored answers (`FRAME_EDGE_PARITY=1`, needs `research/frame-edge/data`) |
| `tests/test_roll.py` | aim path, units, `measure_shift_mm` (sign, floor, off-axis, reach), `hold_plan` decision table, hold loop, `wrong_way`, reversed passes |
| `tests/test_uniformity.py` | `register` recovers a known shift |
| `tests/test_scan_roll_tool.py` | `hold_from_walk` |
| `tests/test_session.py` | `plan_nudges`, `deliverable_mm`, survey files |
| `tests/test_demo.py` | nudge parity, the demo runs the real hold loop and converges |
| `tests/test_gui.py` | snapping, `approved.json` and sources, proposals, edge lines |
| `tests/test_ensemble.py`, `tests/test_frame_position.py` | the legacy strip detector |

`command_for`, `describe_command` and `CORRECTION_DEADBAND_MM`, which had no
test, are deleted (887879c).

## 12. Loose ends

These are in the code today. None is fixed here.

- **Units.** Every interface on the path is still in millimetres, against
  CLAUDE.md's rule.
- **Stale comments:**
  - `rps7200/session.py:92` and the `plan_nudges` docstring say param 8 / "1..8"; the cap is 87.
  - `tools/scan_roll.py:397` says one command reaches 1.0118 mm.
  - `snap_offset`'s docstring says "eight commands".
  - `protocol.units_for_param` says param 1 travels 2.57; it is 2.84.
  - `tools/hold_probe.py:34` cites a floor of 40.
- **Roll-wide travel.** The approved path has no roll-wide limit (`TODO.md`).
- **The travel budget scales with the target.** A large, wrong target buys more
  travel than a small, right one (see findings, *Open questions*).
- **The `correct` toggle** on the contact sheet can never act (section 7).
