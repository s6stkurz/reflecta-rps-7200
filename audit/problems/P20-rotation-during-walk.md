# P20 -- Turning or flipping during a walk corrupts the stored walk

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 8 findings in 6 areas

[Back to the summary](../README.md)

## The problem

The orientation the writer applies to `prescanNN.tif` / `frameNN.tif` is read when each
file is written, but `survey.json` records the rotation once, at the start. Rotating or
flipping a prescan while a walk or roll runs therefore writes later files in a new
orientation that the manifest does not describe, so a reopened walk and
`scan_roll.py --approved` read them wrongly. `remember_arrangement` also appends the
prescan to the survey again on every change, duplicating entries.

## Fix plan

1. Record orientation per file in the manifest (`frames[n].rotation/flipped`), written
   with the file.
2. Or freeze orientation for the duration of a walk (controls disabled while it runs,
   applied afterwards to the delivered copies only).
3. `remember_arrangement`: replace by frame number, never append.

## Evidence (from [GUI1-06](../areas/gui-part1.md#gui-part1-gui1-06))

**Where:** `tools/gui.py:3911-3921`, `rps7200/session.py:1687-1688`, `rps7200/session.py:1732-1733`, `rps7200/session.py:2011-2029`, `tools/gui.py:4722-4747`

```text
_carry: `self.session.rotation = result.rotation` / `self.session.flip = result.flipped` (any time, from the Tk thread). _roll builds the manifest once: `"rotation": self.rotation, "flipped": self.flip` and rewrites that same dict after every frame. _orientation_for for prescans: `return self.rotation, self.flip` (read at filing time, per frame). read_survey: `turn = int(manifest.get("rotation") or 0)` ... `image=preview.unorient(image, turn, mirrored)`.
```

**Failure scenario:** The dry run starts at rotation 0 and the operator rotates frame 1's prescan 90 degrees as it lands. prescan02-06.tif are written rotated 90 while survey.json says 0. Next day, reopening shows frames 2-6 turned 180 relative to frame 1 (90 un-rotated by 0, plus the 90 applied as result.rotation). The references are rotated, so every held frame reads unverified and nothing moves.

**Second reader's check:** The mechanism is real. _carry sets session.rotation/flip at any time (gui.py:3919-3920). The manifest's rotation and flipped are captured once at walk start (session.py:1687-1688, 1732-1733) and never updated. _orientation_for returns self.rotation/self.flip for prescans at filing time (session.py:2025-2029). read_survey un-orients every prescan by the single manifest pair (gui.py:4722-4723, 4739). The failure-scenario arithmetic is wrong, though. read_survey sets result.rotation = turn (the manifest's 0) for every reopened frame (4747), not the operator's 90. So frames 2-6 reopen turned 90 degrees relative to frame 1, not 180. The consequence stands: those arrays are the sheet's pictures and the references on a reopened commission.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI1-06](../areas/gui-part1.md#gui-part1-gui1-06) | gui-part1 | high | partly | Turning or flipping a prescan during a walk changes the orientation of the prescanNN.tif files still to be written, while survey.json keeps the start value, so reopened references come back mis-oriented | tools/gui.py:3911-3921, rps7200/session.py:1687-1688, rps7200/session.py:1732-1733 |
| [GUI2-09](../areas/gui-part2.md#gui-part2-gui2-09) | gui-part2 | high | confirmed | Rotating during a walk or roll changes the orientation of later prescanNN.tif files while survey.json keeps the start-time rotation, so a reopened walk un-orients those references wrongly | rps7200/session.py:1687-1688, rps7200/session.py:1732-1733, rps7200/session.py:2029 |
| [FE-01](../areas/frame-edges-detectors.md#frame-edges-detectors-fe-01) | frame-edges-detectors | high | confirmed | Turning or flipping a prescan during a walk makes the window write later prescanNN.tif in a new orientation. survey.json keeps the old one, and reopen and --approved then feed the detector mirrored or rotated frames | rps7200/session.py:2025-2029, rps7200/session.py:2106, rps7200/session.py:1067-1068 |
| [CC-02](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-02) | cross-process-and-thread-concurrency | high | confirmed | A rotation made while a walk or roll runs changes how later prescanNN.tif/frameNN.tif are written, but the manifest records the rotation once, so read_survey and roll exports un-orient them wrongly | rps7200/session.py:1686-1688, rps7200/session.py:1732-1733, rps7200/session.py:2011-2029 |
| [SR-14](../areas/session-roll.md#session-roll-sr-14) | session-roll | medium | confirmed | Rotating or flipping during a walk changes how prescanNN.tif is written, but the manifest keeps the orientation from the start | rps7200/session.py:1686-1688, rps7200/session.py:1732-1733, rps7200/session.py:2025-2029 |
| [FU-02](../areas/framing-units.md#framing-units-fu-02) | framing-units | medium | confirmed | prescanNN.tif orientation is not recorded per file; rotating or flipping during a walk corrupts the stored walk and duplicates survey entries | tools/gui.py:3498-3505, tools/gui.py:3911-3923, tools/gui.py:719-723 |
| [GUI1-23](../areas/gui-part1.md#gui-part1-gui1-23) | gui-part1 | medium | confirmed | Turning or flipping a walked prescan during the walk appends it to the survey again | tools/gui.py:3498-3505, tools/gui.py:3911-3936, tools/gui.py:5791-5837 |
| [GUI2-08](../areas/gui-part2.md#gui-part2-gui2-08) | gui-part2 | medium | confirmed | remember_arrangement appends to the survey on every arrangement change during a walk: rotating a prescan duplicates it, and rotating an older walk's prescan injects it into the new walk | tools/gui.py:3498-3505, tools/gui.py:3911-3924, tools/gui.py:7660-7677 |
