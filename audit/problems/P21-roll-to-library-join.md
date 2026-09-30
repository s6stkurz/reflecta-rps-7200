# P21 -- Roll export can deliver the wrong entry (a 300 dpi prescan as a frame)

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 7 findings in 5 areas

[Back to the summary](../README.md)

## The problem

`roll_entry_index` finds a roll's library entries by parsing `film.frame` as
`<roll>-<NN>`. Walk-prescan entries and scanned-frame entries have the same key, and the
last one in glob order wins, so **Export rolls can deliver a 300 dpi prescan as "frame NN
at full resolution"**. A value typed into the Film panel's "frame" note replaces every
roll frame's label, breaking the join and giving all frames one library signature (P11).
`tools/scan_roll.py` labels frames `<roll>/<NN>`, which the window cannot parse, and a
duplicated or same-named roll joins to the other roll's entries.

## Fix plan

1. Record `roll: {name, folder, index, kind: frame|prescan}` in `scan.json` and join on
   it, not on the free-text frame note.
2. `roll.json` stores each frame's library entry id; export follows the id.
3. One label format for both writers.

## Evidence (from [GUI2-05](../areas/gui-part2.md#gui-part2-gui2-05))

**Where:** `tools/gui.py:4999-5008`, `tools/gui.py:4892-4905`, `rps7200/session.py:1817-1827`, `rps7200/session.py:1880-1882`, `tools/gui.py:1724-1731`, `tools/gui.py:6997-6998`

```text
roll_entry_index: `frame = str(((record.get("film") or {}).get("frame") or "")).strip()`; `roll, _, number = frame.rpartition("-")`; `out.setdefault(roll, {})[int(number)] = record_path.parent` (last glob hit wins; `root.glob("*/scan.json")` order is filesystem-dependent). A dry-run walk files each prescan as its own entry with `replace(job.notes, frame=job.notes.frame or f"{name}-{number:02d}")` (session.py:1827), and a roll frame with the same key (1881). _notes() passes `frame=self.fields["frame"].get().strip()`. roll_exports labels every item `kind="frame"` with `"resolution_dpi": settings.get("resolution") or 0`.
```

**Failure scenario:** On Linux (ext4 hash order), Rolls -> Export on a roll walked at 300 dpi and scanned at 3600 dpi writes some frames from the 300 dpi walk-prescan entries. The log says 'exported ... at full resolution' and the file name says 3600dpi.

**Second reader's check:** session.py:1827 files dry-run prescan entries with `frame=job.notes.frame or f"{name}-{number:02d}"`, and 1880-1882 does the same for roll frames. roll_entry_index (4999-5008) keeps the last glob hit per key and does not filter by tags or resolution. roll_exports labels every item with the roll's resolution_dpi. A walk and a commission on the same day with an empty name share the key (both default to today's date). FilmNotes.frame from the Film panel overrides the key for every frame.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI2-05](../areas/gui-part2.md#gui-part2-gui2-05) | gui-part2 | high | confirmed | roll_entry_index keys walk-prescan entries and scanned-frame entries identically, so Export can deliver 300 dpi prescans as frames; a value in the Film 'frame' field breaks the join altogether | tools/gui.py:4999-5008, tools/gui.py:4892-4905, rps7200/session.py:1817-1827 |
| [SR-10](../areas/session-roll.md#session-roll-sr-10) | session-roll | high | confirmed | A value in the Film 'frame' note replaces every roll frame's label: all entries share one library signature and the roll-entry join breaks | rps7200/session.py:1826-1827, rps7200/session.py:1880-1882, rps7200/library.py:686-698 |
| [CC-11](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-11) | cross-process-and-thread-concurrency | high | confirmed | Export rolls can export a walk's 300 dpi prescan entry as 'frame NN at full resolution': roll_entry_index joins on film.frame, which walk prescans share, and the last entry in directory order wins | tools/gui.py:4979-5009, rps7200/session.py:1823-1828, rps7200/session.py:1880-1882 |
| [GUI1-21](../areas/gui-part1.md#gui-part1-gui1-21) | gui-part1 | medium | confirmed | The Film panel's 'frame' field is stamped on every frame of a roll, which breaks the roll-to-library join | tools/gui.py:1724-1731, tools/gui.py:2157, tools/gui.py:2863 |
| [SR-26](../areas/session-roll.md#session-roll-sr-26) | session-roll | medium | confirmed | The two roll writers label library entries differently; the GUI join only parses the session's form | rps7200/session.py:1880-1882, tools/scan_roll.py:577, tools/gui.py:5004-5008 |
| [CC-A3](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-a3) | cross-process-and-thread-concurrency | medium | found-by-verifier | roll_entry_index cannot join frames filed by tools/scan_roll.py, and joins a duplicated or same-named roll to the other roll's entries | tools/scan_roll.py:571-581, tools/gui.py:4999-5008, tools/gui.py:5127 |
| [RX-14](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-14) | resource-exhaustion-crash-recovery-time | medium | confirmed | The GUI cannot join library entries of rolls scanned by tools/scan_roll.py: the frame key is 'roll/NN' there and 'roll-NN' in the window's index | tools/scan_roll.py:570-578, tools/gui.py:4979-5008, rps7200/session.py:1880-1882 |
