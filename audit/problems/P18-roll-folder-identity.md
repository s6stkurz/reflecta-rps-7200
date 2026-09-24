# P18 -- One roll is scattered across folders; unnamed rolls share one folder

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 16 findings in 7 areas

[Back to the summary](../README.md)

## The problem

The roll's folder is derived three different ways:

- the session writes to `rolls/<job.name or today's date>` (unsanitised);
- `approved.json` goes to `rolls/_safe(<roll field>)`, which is `rolls/roll` when the field
  is empty;
- a reopened roll's name is never restored into the roll field, so "continue" scans into
  `rolls/<today>`.

With the default empty name every walk and roll of one day shares `rolls/<date>/`: a
second walk replaces `survey.json` wholesale and overwrites `prescanNN.tif`, a roll
overwrites `frameNN.tif`, and a stale `approved.json` / `roll.json` is applied to a
different strip. Roll names reach the filesystem unsanitised in the GUI roll and the CLI
(separators, `..`, absolute paths, Windows-reserved names). `--demo` with `--rolls` or
`--library` pins nothing, so a demo run can overwrite a real walk.

## Fix plan

1. One function, `session.roll_dir(name)`, used by the session, `_write_approved`, the
   reopen path and `scan_roll.py`; it sanitises and never returns a shared default.
2. An empty name becomes a unique id (`<date>-<HHMMSS>`), shown in the roll field, not
   `<date>`.
3. Reopen sets the roll field to the reopened folder's name.
4. Refuse to walk into a folder whose `survey.json` belongs to another strip unless the
   operator confirms.

## Evidence (from [GUI1-03](../areas/gui-part1.md#gui-part1-gui1-03))

**Where:** `tools/gui.py:2929-2967`, `tools/gui.py:2853-2864`, `tools/gui.py:2147-2158`, `rps7200/session.py:1634-1637`, `rps7200/session.py:2181-2189`, `tools/gui.py:4924-4957`

```text
GUI: `name = _safe(self.fields["roll"].get().strip())` / `folder = Path(self.session.rolls) / name` / `(folder / "approved.json").write_text(...)`. Session: `name = job.name or time.strftime("%Y-%m-%d")` / `out = Path(job.out) if job.out else self.rolls / name`. _safe: `cleaned = "".join(kept).strip("-.") or "roll"`. Roll(name=self.fields["roll"].get().strip()) is passed unsanitised.
```

**Failure scenario:** Walk a strip with the roll field empty, turn two portrait frames in the sheet, commission. Tomorrow open rolls/2026-09-23 from Rolls...: no turns, no positions, and 'Export' writes the portrait frames sideways. Name a roll 'Gold 200' and approved.json lands in rolls/Gold-200 while the roll is in 'rolls/Gold 200'.

**Second reader's check:** _write_approved (gui.py:2940-2943) uses _safe(roll field), and _safe maps '' to 'roll' (session.py:2183). The session (session.py:1634-1635) uses `job.name or time.strftime('%Y-%m-%d')` with the raw, unsanitised name that on_roll and on_scan_chosen pass (gui.py:2156, 2862). The roll field is not in REMEMBERED_FILM (gui.py:194), so it starts empty. With an empty name, approved.json lands in rolls/roll/ and the frames in rolls/<date>/. read_approved(folder) reads only folder/approved.json (gui.py:4949-4951).

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [GUI1-03](../areas/gui-part1.md#gui-part1-gui1-03) | gui-part1 | high | confirmed | approved.json is written to a different folder than the roll it belongs to (empty or unsanitised roll names) | tools/gui.py:2929-2967, tools/gui.py:2853-2864, tools/gui.py:2147-2158 |
| [DP-07](../areas/demo-parity.md#demo-parity-dp-07) | demo-parity | high | confirmed | approved.json is written to rolls/_safe(name) ('roll' when blank) while the roll writes to rolls/<name or date>; reopening loses the approved positions | tools/gui.py:2929-2955, rps7200/session.py:1634-1637, rps7200/session.py:2181-2189 |
| [SR-08](../areas/session-roll.md#session-roll-sr-08) | session-roll | high | confirmed | Sheet decisions and resumed rolls land in different folders from the roll they belong to | rps7200/session.py:1634-1637, rps7200/session.py:1148-1153, tools/gui.py:2929-2943 |
| [SR-09](../areas/session-roll.md#session-roll-sr-09) | session-roll | high | confirmed | Unnamed walks and rolls of one day share rolls/<date>/: frames and prescans overwritten, stale approved.json and roll.json applied to a different strip | rps7200/session.py:1634-1642, rps7200/session.py:1817, rps7200/session.py:1889 |
| [GUI1-04](../areas/gui-part1.md#gui-part1-gui1-04) | gui-part1 | high | confirmed | With the default empty roll name every strip of the day shares rolls/<date>: walks overwrite each other, frames overwrite frameNN.tif, and the previous strip's sheet decisions are restored onto the next | rps7200/session.py:1634-1654, rps7200/session.py:1817-1833, rps7200/session.py:1889 |
| [GUI1-05](../areas/gui-part1.md#gui-part1-gui1-05) | gui-part1 | high | confirmed | Continuing a reopened roll scans into a different folder: the roll's name is never restored | tools/gui.py:2552-2563, tools/gui.py:2574-2601, tools/gui.py:2697-2721 |
| [GUI2-03](../areas/gui-part2.md#gui-part2-gui2-03) | gui-part2 | high | confirmed | approved.json is written to rolls/_safe(<roll field>) but the roll goes to rolls/<roll field or today's date>. With the default empty name it always lands in rolls/roll/ | tools/gui.py:2940-2943, tools/gui.py:2862, tools/gui.py:2156 |
| [GUI2-21](../areas/gui-part2.md#gui-part2-gui2-21) | gui-part2 | high | confirmed | A dry-run walk or roll with an empty roll name reuses rolls/<today>/, overwriting the earlier walk's survey.json and prescans and merging roll.json, with no warning | tools/gui.py:2156, tools/gui.py:2102-2111, rps7200/session.py:1634-1650 |
| [CC-A1](../areas/cross-process-and-thread-concurrency.md#cross-process-and-thread-concurrency-cc-a1) | cross-process-and-thread-concurrency | high | found-by-verifier | The roll's folder, approved.json's folder and a reopened roll's folder are derived three different ways, so commissioning scatters one roll across folders and the non-derivable approved.json can land in a shared 'rolls/roll/' | rps7200/session.py:1634-1637, tools/gui.py:2938-2945, tools/gui.py:2156 |
| [RX-3](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-3) | resource-exhaustion-crash-recovery-time | high | confirmed | Resuming a reopened roll does not write into the reopened folder: the roll name field is never restored, and blank falls back to today's local date | tools/gui.py:2552-2576, tools/gui.py:4780-4792, tools/gui.py:1520-1532 |
| [RX-5](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-5) | resource-exhaustion-crash-recovery-time | high | confirmed | Unnamed rolls share one local-date folder: a second walk (or CLI roll) the same day overwrites the first walk's survey.json and prescanNN.tif, and two strips' library entries share one roll key | rps7200/session.py:1634-1642, rps7200/session.py:1650, rps7200/session.py:1817-1833 |
| [SR-A3](../areas/session-roll.md#session-roll-sr-a3) | session-roll | high | found-by-verifier | Any walk into an existing roll folder replaces survey.json wholesale: a partial re-walk erases the earlier walk's frames | rps7200/session.py:1638-1654, rps7200/session.py:1740-1748, rps7200/session.py:1914-1926 |
| [SR-15](../areas/session-roll.md#session-roll-sr-15) | session-roll | medium | confirmed | Roll name is used as a path component without sanitising | rps7200/session.py:1634-1636, rps7200/session.py:2170-2189, tools/gui.py:2156 |
| [RX-7](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-7) | resource-exhaustion-crash-recovery-time | medium | confirmed | Roll names reach the filesystem unsanitised in the GUI roll and the CLI: separators, '..', absolute paths and Windows-illegal or reserved names; the failure comes after the seek has moved the film | rps7200/session.py:1619-1636, rps7200/session.py:2170-2189, tools/gui.py:2156 |
| [T15](../areas/tests.md#tests-t15) | tests | medium | confirmed | A second walk under the same roll name (the default is today's date) overwrites survey.json and prescanNN.tif | rps7200/session.py:1634-1656, rps7200/session.py:1817-1831, rps7200/session.py:1924-1926 |
| [DP-05](../areas/demo-parity.md#demo-parity-dp-05) | demo-parity | medium | partly | --demo pins nothing when --library/--rolls/--out/--settings are given; a demo run can overwrite a real walk's survey.json, prescanNN.tif and approved.json | tools/gui.py:8117-8122, tools/gui.py:8132, tools/gui.py:8150 |
