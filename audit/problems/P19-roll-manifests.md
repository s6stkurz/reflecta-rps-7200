# P19 -- roll.json / survey.json / approved.json can lie about what was done

**Severity** high · **Group** C: Rolls, walks and their state on disk · **Reported independently by** 12 findings in 7 areas

[Back to the summary](../README.md)

## The problem

- A frame is marked done in `roll.json` before -- and regardless of whether -- it was
  filed; after a crash or a writer failure a resume skips it.
- Manifests are truncated and rewritten in place every frame; a kill or ENOSPC mid-write
  leaves an empty file, the roll disappears from the list, and an unreadable earlier
  `roll.json` is silently replaced on resume.
- `tools/scan_roll.py --start-at N` (documented as resume) writes a fresh manifest over
  the old one.
- Resuming rewrites the original frame numbers in place.
- Each commission replaces `approved.json` with only the frames ticked this time, erasing
  earlier frames' positions and turns; a damaged file reads as "no decisions".

## Fix plan

1. Atomic manifest writes (temp + `os.replace`), a `.bak` of the previous version.
2. Mark a frame done from `FrameWriter.on_done` with its library entry id, not at submit.
3. `--start-at`: load and merge the existing manifest.
4. `approved.json`: merge per frame rather than replace; refuse to overwrite a file that
   does not parse.

## Evidence (from [SR-03](../areas/session-roll.md#session-roll-sr-03))

**Where:** `rps7200/session.py:1895-1926`, `rps7200/session.py:1348-1355`, `tools/gui.py:5240-5258`, `tools/gui.py:5086-5104`

```text
session.py:1904 `"done": bool(rf.error is None and rf.image is not None)` is computed on the scanner thread and written at once (session.py:1924 `manifest_path.write_text(...)`), while the frame is merely queued to the writer (`self._file(...)` session.py:1883). Writer failures only produce `self._emit("log", text=f"picture {number} could not be filed: {err}")` (session.py:1354); nothing updates the manifest. gui.py scanned_frames reads `finished = record.get("done")` (gui.py:5254) and roll_summary computes `"remaining": [n for n in wanted if n not in done]`.
```

**Failure scenario:** Disk fills during frame 20 of a roll; frames 20-24 fail in the writer. The operator reopens the roll after freeing space: the browser shows it complete, 'remaining' is empty, and frames 20-24 exist nowhere.

**Second reader's check:** session.py:1904 `"done": bool(rf.error is None and rf.image is not None)` is computed on the scanner thread and written at once (1924-1926), while the frame has only been queued with `self._writer.submit` (via _file at 1883). A writer failure reaches only `_filed` -> `_emit("log", ...could not be filed...)` (1353-1354) and never touches the manifest. The GUI's resume logic reads those done flags.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [SR-03](../areas/session-roll.md#session-roll-sr-03) | session-roll | high | confirmed | roll.json marks a frame done before, and regardless of whether, it was filed | rps7200/session.py:1895-1926, rps7200/session.py:1348-1355, tools/gui.py:5240-5258 |
| [SR-07](../areas/session-roll.md#session-roll-sr-07) | session-roll | high | confirmed | roll.json/survey.json written non-atomically; an unreadable earlier roll.json is silently discarded and overwritten on resume | rps7200/session.py:1649-1654, rps7200/session.py:1924-1926, tools/gui.py:2943 |
| [CLI-04](../areas/cli-operator-tools.md#cli-operator-tools-cli-04) | cli-operator-tools | high | confirmed | Resuming a roll with --start-at overwrites roll.json; docs say it resumes from and carries forward the manifest | tools/scan_roll.py:22-24, tools/scan_roll.py:291-329, tools/scan_roll.py:451-452 |
| [RX-2](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-2) | resource-exhaustion-crash-recovery-time | high | confirmed | roll.json/survey.json are truncated in place every frame; a kill or ENOSPC mid-write empties it, the roll disappears from the list, cannot be reopened, and the next resume silently overwrites it | rps7200/session.py:1922-1926, rps7200/session.py:1649-1654, tools/gui.py:5048-5055 |
| [RX-4](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-4) | resource-exhaustion-crash-recovery-time | high | confirmed | tools/scan_roll.py --start-at 'resume' replaces the existing roll.json with a fresh manifest, contrary to README and its own docstring | tools/scan_roll.py:298-329, tools/scan_roll.py:448-453, tools/scan_roll.py:20-24 |
| [RX-6](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-6) | resource-exhaustion-crash-recovery-time | medium | confirmed | A frame is recorded as done (GUI) or as having its file (CLI) before anything is written; after a crash or a writer failure, resume skips frames that have no file and no entry | rps7200/session.py:1883-1905, rps7200/session.py:1922-1923, tools/scan_roll.py:532-542 |
| [DOC-10](../areas/docs-plans-todo.md#docs-plans-todo-doc-10) | docs-plans-todo | medium | confirmed | `tools/scan_roll.py --start-at N` 'resume' overwrites the earlier roll.json instead of resuming from it | tools/scan_roll.py:20-24, tools/scan_roll.py:296-329, tools/scan_roll.py:451-453 |
| [D10](../areas/docs-readme-claude.md#docs-readme-claude-d10) | docs-readme-claude | medium | confirmed | tools/scan_roll.py --start-at overwrites roll.json instead of carrying the earlier run forward | tools/scan_roll.py:291-329, tools/scan_roll.py:446-453, tools/scan_roll.py:584-585 |
| [SR-17](../areas/session-roll.md#session-roll-sr-17) | session-roll | medium | confirmed | Resuming an old roll rewrites its frame numbers in place, destroying the originally recorded numbering | rps7200/session.py:1655-1668, rps7200/session.py:1747, rps7200/session.py:575-631 |
| [GUI1-09](../areas/gui-part1.md#gui-part1-gui1-09) | gui-part1 | medium | confirmed | Each commission replaces approved.json with only the frames ticked this time, so earlier frames' positions and turns are lost (written non-atomically) | tools/gui.py:2929-2955, tools/gui.py:2831, tools/gui.py:4871-4899 |
| [GUI2-04](../areas/gui-part2.md#gui-part2-gui2-04) | gui-part2 | medium | confirmed | Each commission replaces approved.json wholesale, non-atomically, and a damaged file reads back as 'no decisions' with nothing said | tools/gui.py:2831, tools/gui.py:2943-2955, tools/gui.py:4952-4956 |
| [RX-11](../areas/resource-exhaustion-crash-recovery-time.md#resource-exhaustion-crash-recovery-time-rx-11) | resource-exhaustion-crash-recovery-time | medium | confirmed | approved.json is rewritten whole with only this commission's ticked frames; on a resume it erases the earlier frames' turns and flips, which roll export uses; a truncated file reads as empty | tools/gui.py:2929-2955, tools/gui.py:5818-5837, tools/gui.py:4949-4956 |
