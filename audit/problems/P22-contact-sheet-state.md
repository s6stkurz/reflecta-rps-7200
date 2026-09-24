# P22 -- Contact-sheet decisions leak between strips or are lost

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 7 findings in 4 areas

[Back to the summary](../README.md)

## The problem

A contact sheet left open across a new dry-run walk is raised instead of rebuilt, and
its decisions are commissioned against the new strip. An explicit "Centre" (zero)
position is forgotten when the sheet or roll is reopened, and the detector's number
silently takes its place. The Frame position window outlives its sheet and later edits
raise `TclError`; decisions in an open sheet are lost when the window quits or another
roll is opened.

## Fix plan

1. Key the sheet to the walk (folder + survey hash); a new walk closes the old sheet.
2. Store "explicit zero" distinctly from "no decision" (`None` vs `0.0`) in
   `approved.json` and settings.
3. The position window is a child of the sheet and is destroyed with it; decisions are
   saved on every change.

## Evidence (from [GUI1-07](../areas/gui-part1.md#gui-part1-gui1-07))

**Where:** `tools/gui.py:2112-2133`, `tools/gui.py:2160-2191`, `tools/gui.py:2340-2350`, `tools/gui.py:3249-3262`, `tools/gui.py:2723-2771`

```text
on_roll (dry) clears `self.survey`, `self.orientations`, `self.sheet_state`, `self._sheet_roll` but does not close `self.sheet`. on_contact_sheet: `if self.sheet is not None and self.sheet.alive(): self.sheet.top.lift(); ... return`. _dismiss -> `_store_sheet_state(self.state())` -> `self.sheet_state = state` (set even when key is None). on_scan_chosen checks only `self.busy`.
```

**Failure scenario:** The operator leaves strip 1's sheet open, loads strip 2, ticks dry run and presses Scan roll. When it ends, the sheet that pops up is strip 1's. They tick frames and press Scan chosen frames: frames of strip 2 are held to strip 1's prescans and written with strip 1's orientations.

**Second reader's check:** The on_roll dry branch (gui.py:2112-2133) resets survey, orientations, sheet_state and _sheet_roll but never destroys self.sheet (open_roll does, at 2620-2624). on_contact_sheet lifts a live sheet and returns (2170-2173). The old _ContactSheet holds the old survey list object. _dismiss calls _store_sheet_state, which sets self.sheet_state = state even when _sheet_key() is None (2342-2345), so closing it mid-walk puts the old decisions back for the next sheet. on_scan_chosen checks only busy and calibration.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI1-07](../areas/gui-part1.md#gui-part1-gui1-07) | gui-part1 | high | confirmed | A contact sheet from the previous strip survives a new walk: it is raised instead of rebuilt, or its state is put back by closing it mid-walk | tools/gui.py:2112-2133, tools/gui.py:2160-2191, tools/gui.py:2340-2350 |
| [GUI2-07](../areas/gui-part2.md#gui-part2-gui2-07) | gui-part2 | high | confirmed | A contact sheet left open across a new dry-run walk is lifted in place of the new walk's sheet, and its decisions are filed under the new walk | tools/gui.py:2112-2133, tools/gui.py:3249-3262, tools/gui.py:2170-2173 |
| [CC-04](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-04) | cross-process-and-thread-concurrency | medium | confirmed | A contact sheet left open is lifted, not rebuilt, when the next walk ends, and it commissions a roll on the new strip with the old strip's frames, references and positions | tools/gui.py:2112-2133, tools/gui.py:2170-2173, tools/gui.py:3249-3262 |
| [GUI2-10](../areas/gui-part2.md#gui-part2-gui2-10) | gui-part2 | high | confirmed | An explicit 'Centre' (zero) position is forgotten when the sheet is closed and reopened or the roll is reopened, and the detector's number silently takes its place | tools/gui.py:6715-6721, tools/gui.py:7898-7910, tools/gui.py:5519-5534 |
| [FU-04](../areas/framing-units.md#framing-units-fu-04) | framing-units | medium | confirmed | An operator's explicit 'as surveyed' (zero) position is lost on sheet or roll reopen and replaced by the detector's proposal | tools/gui.py:4963-4964, tools/gui.py:5510-5534, tools/gui.py:6716-6721 |
| [GUI2-15](../areas/gui-part2.md#gui-part2-gui2-15) | gui-part2 | medium | confirmed | The Frame position window outlives its contact sheet; later edits go into a destroyed sheet, raise TclError and are lost | tools/gui.py:6548, tools/gui.py:7917-7932, tools/gui.py:8012-8013 |
| [GUI2-16](../areas/gui-part2.md#gui-part2-gui2-16) | gui-part2 | medium | confirmed | Decisions in an open sheet are lost when the main window quits or another roll is opened | tools/gui.py:3039-3050, tools/gui.py:3070-3086, tools/gui.py:2620-2624 |
