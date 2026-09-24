# P23 -- Busy guards sit on buttons, not on actions; submit() cancels Stop

**Severity** medium · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 11 findings in 5 areas

[Back to the summary](../README.md)

## The problem

The Roll keyboard shortcut (Ctrl-B), aim-clicks, fine moves and the Rolls browser's
Open all bypass the busy guard: a second roll can be queued, a running walk's survey
reset, film moved during a job, or a reopened survey mixed with a walk in progress.
`ScanSession.submit()` clears a pending stop, so any submission cancels the operator's
Stop. Stop cannot cancel a queued job, and double-presses queue duplicate scans. Roll
folder delete/rename is guarded only by the window's busy flag, not by the writer still
writing that roll.

## Fix plan

1. Guard in the session, not the widgets: `submit()` refuses (or queues explicitly) while
   a job runs; it never clears `_stop`.
2. One `busy` check used by every handler that moves film or starts a job.
3. Stop clears the queue as well as the running job.

## Evidence (from [GUI1-08](../areas/gui-part1.md#gui-part1-gui1-08))

**Where:** `tools/gui.py:736-741`, `tools/gui.py:2077-2146`, `tools/gui.py:815-833`, `rps7200/shortcuts.py:117`

```text
`"roll": self.on_roll,` (default key `<ACCEL-Key-b>`). _confirm_then for prescan/scan starts with `if self.busy: ... return`, but on_roll has no busy check, and its comment says `# The button this handler is behind is disabled while busy, so this cannot race a job that is still running.` A dry run then does `self.survey = []`, `self.orientations = {}`, `self.sheet_state = {}`, `self._sheet_roll = None`, `self.edge_watch.begin(...)` and resets `_roll_wall_start`/`_roll_frames_total`.
```

**Failure scenario:** A walk is on frame 7 of 12. The operator presses Cmd-B intending to check settings and clicks OK. The contact sheet that opens has only frames 8-12, and a second walk starts afterwards with no sheet.

**Second reader's check:** The 'roll' action maps straight to self.on_roll (gui.py:740). _runner adds no guard (891-896), and on_roll has no busy check. Its own comment at 2138-2139 says it cannot race, which is false for the key <ACCEL-Key-b> (shortcuts.py:117). A dry run mutates survey, sheet_state, _sheet_roll and edge_watch immediately (2112-2133), while the first walk's 'finished' still clears _surveying (3249-3250).

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI1-08](../areas/gui-part1.md#gui-part1-gui1-08) | gui-part1 | medium | confirmed | The Roll keyboard shortcut bypasses the busy guard: it queues a second roll and resets the running walk's survey mid-walk | tools/gui.py:736-741, tools/gui.py:2077-2146, tools/gui.py:815-833 |
| [SR-13](../areas/session-roll.md#session-roll-sr-13) | session-roll | medium | confirmed | submit() cancels a requested stop, and two GUI paths queue jobs while a roll runs | rps7200/session.py:1214-1218, rps7200/session.py:1317, rps7200/session.py:1364 |
| [CC-03](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-03) | cross-process-and-thread-concurrency | medium | confirmed | Busy guards sit on buttons, not on actions: Ctrl-B (on_roll) and the already-open roll browser (open_roll) replace the survey and EdgeWatch generation of a walk that is still running | rps7200/shortcuts.py:117, tools/gui.py:740, tools/gui.py:2112-2133 |
| [CC-05](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-05) | cross-process-and-thread-concurrency | medium | confirmed | submit() clears the stop flag of the job that is still running, and aim-clicks (on_nudge) and Ctrl-B submit while busy | rps7200/session.py:1214-1218, rps7200/session.py:1313-1318, rps7200/session.py:1762 |
| [T14](../areas/tests.md#tests-t14) | tests | medium | partly | submit() clears a pending Stop, and the aim-click, fine-move and frame-move handlers submit while busy; untested | rps7200/session.py:1213-1217, rps7200/session.py:1317, tools/gui.py:4500-4504 |
| [GUI1-15](../areas/gui-part1.md#gui-part1-gui1-15) | gui-part1 | medium | confirmed | Stop cannot cancel a job already queued, and double-presses or keyboard repeats queue duplicate scans | rps7200/session.py:1214-1218, rps7200/session.py:1313-1318, tools/gui.py:3020-3023 |
| [GUI1-12](../areas/gui-part1.md#gui-part1-gui1-12) | gui-part1 | medium | confirmed | Clicking a prescan in aim mode can queue a film move during a running job, or aim from a stale prescan of another position | tools/gui.py:4500-4504, tools/gui.py:4531-4587, tools/gui.py:2972-3018 |
| [GUI2-19](../areas/gui-part2.md#gui-part2-gui2-19) | gui-part2 | medium | confirmed | Aim-click moves film with no busy guard and no check that the prescan clicked shows where the film is now | tools/gui.py:4500-4504, tools/gui.py:4531-4587, tools/gui.py:2972-3018 |
| [GUI1-22](../areas/gui-part1.md#gui-part1-gui1-22) | gui-part1 | medium | confirmed | Rolls... 'Open' works while the scanner is busy and can replace the survey of a walk in progress | tools/gui.py:7049-7054, tools/gui.py:2226-2244, tools/gui.py:2552-2660 |
| [GUI2-14](../areas/gui-part2.md#gui-part2-gui2-14) | gui-part2 | medium | confirmed | Opening a roll from the Rolls table is not guarded against a running job, so a walk in progress mixes with the reopened survey | tools/gui.py:7049-7054, tools/gui.py:2552-2626, tools/gui.py:2239-2247 |
| [CC-07](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-07) | cross-process-and-thread-concurrency | medium | confirmed | Delete/rename/duplicate of a roll folder is guarded only by this window's busy flag, not by the FrameWriter still writing that roll or by another process | tools/gui.py:2367-2381, tools/gui.py:2484-2537, tools/gui.py:2453-2482 |
