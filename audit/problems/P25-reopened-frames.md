# P25 -- Reopened frames collide, and Delete removes real library entries

**Severity** medium · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 7 findings in 2 areas

[Back to the summary](../README.md)

## The problem

Reopened frames get `seq = -number`, so two reopened rolls collide: the wrong
full-resolution picture is shown and Delete removes the wrong item. Delete on a reopened
frame `rmtree`s a real library entry -- even under `--demo` / `make run-sheet`, whose
promise is that nothing real is touched. Reopening a roll without a walk restores the
original frame count and moves start-at, so the Roll button rescans done frames. Per-frame
turns from a commissioned roll persist and are applied to the next plain roll.

## Fix plan

1. Unique sequence numbers for reopened frames (a counter, not `-number`).
2. Delete of a library entry goes to `.trash/`, needs confirmation, and is refused
   outright under `--demo` for entries outside the demo library.
3. Clear per-roll state when another roll is opened.

## Evidence (from [GUI1-19](../areas/gui-part1.md#gui-part1-gui1-19))

**Where:** `tools/gui.py:4742`, `tools/gui.py:3999-4011`, `tools/gui.py:8165-8171`, `tasks.py:167-185`

```text
read_survey: `entry=Path(entries[number]) if number in entries else None` (from approved.json reference_entry, e.g. 'library/<id>'). on_delete: `if not keep: shutil.rmtree(entry); library.reindex(entry.parent)`. main(): 'It is safe because opening a roll only reads it -- `_write_approved` derives its folder from `session.rolls`, which --demo pins under demo/'.
```

**Failure scenario:** While exploring make run-sheet the operator deletes 'frame 3 (reopened)' and clicks No, thinking it is the demo copy. The real library entry with the raw bytes of that prescan is gone for good.

**Second reader's check:** The code path is real. read_survey sets entry from approved.json's reference_entry (gui.py:4742), and on_delete rmtrees it and reindexes its parent when the answer to 'Keep the library entry?' is No (3999-4011). There is no check for demo, seq<0, or a path outside session.root. main()'s comment (8165-8171) claims nothing the window does can write back. Two corrections. make run-sheet opens rolls/aligned-strip (tasks.py:167, 184), not registration-D. And only frames whose approved.json carries a reference_entry get an entry. The deletion also sits behind an explicit dialog that says the raw bytes are unrecoverable. So medium rather than high.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI1-19](../areas/gui-part1.md#gui-part1-gui1-19) | gui-part1 | medium | partly | Delete on a reopened frame deletes a real library entry, even under --demo / make run-sheet | tools/gui.py:4742, tools/gui.py:3999-4011, tools/gui.py:8165-8171 |
| [GUI2-11](../areas/gui-part2.md#gui-part2-gui2-11) | gui-part2 | medium | confirmed | Deleting a reopened frame from the filmstrip can rmtree a real library entry, even under --demo whose promise is that nothing touches the real library | tools/gui.py:4742, tools/gui.py:3999-4015, tools/gui.py:8083-8087 |
| [GUI1-13](../areas/gui-part1.md#gui-part1-gui1-13) | gui-part1 | medium | confirmed | Reopened frames use seq = -number, so two reopened rolls collide: wrong full-resolution pixels can be shown for a frame, and Delete removes both | tools/gui.py:4734-4746, tools/gui.py:2603-2609, tools/gui.py:4092-4102 |
| [GUI2-12](../areas/gui-part2.md#gui-part2-gui2-12) | gui-part2 | medium | partly | Reopened frames are numbered seq=-frame, so two opened rolls (or one opened twice) collide: the wrong picture is shown, keyboard walking can raise, and Delete removes both | tools/gui.py:4737, tools/gui.py:2606-2607, tools/gui.py:3643-3664 |
| [GUI1-25](../areas/gui-part1.md#gui-part1-gui1-25) | gui-part1 | medium | confirmed | Resuming a roll that has no sheet via the Roll button rescans done frames and scans past the wanted ones | tools/gui.py:2578-2601, tools/gui.py:4780-4792, tools/gui.py:2077-2158 |
| [GUI2-20](../areas/gui-part2.md#gui-part2-gui2-20) | gui-part2 | medium | partly | Reopening a roll that has no walk restores the original frame count and moves start-at, so the Roll button rescans done frames or runs past the roll | tools/gui.py:2576, tools/gui.py:2581-2598, tools/gui.py:4780-4792 |
| [GUI2-23](../areas/gui-part2.md#gui-part2-gui2-23) | gui-part2 | medium | confirmed | Per-frame turns from a commissioned roll persist in the window and are applied to the next plain roll's frames of the same number, on screen and in Save As but not in the delivered files | tools/gui.py:2845-2847, tools/gui.py:2112-2124, tools/gui.py:3477-3482 |
