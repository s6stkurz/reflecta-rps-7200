# What an operator can do, should not do, and is not stopped from doing

[Back to the summary](README.md)

Collected from every area at `03aacba`: the findings filed as user errors, then each area's
lists of what an operator can do, should not do, and the mistakes nothing guards against.

## What changed since the first audit

- Busy guards now sit on the actions, not only on the buttons.
- Stop drops queued jobs, and quitting offers to stop after the frame in flight.
- Rolls get their own folders.
- A turn during a walk is recorded with what it reached.
- Calibration asks what is in the transport, in the window.
- Arguments that cannot work are refused before the device is opened.

## What is still open

- **"Reverse the direction"** is remembered across launches and silently mirrors every
  approved hold target of a commissioned roll (FR-01).
- **Ctrl-C:**
  - The first press is ignored through calibration and metering in `tools/scan.py`, and is not
    honoured between phases (CLI-02, CSA-03).
  - Filing after close is outside the guard (CSA-04).
  - Most probes and the filing load test abandon the read in flight (TP-11, PAT-03, CLI-15).
  - Closing the terminal that launched the window kills its threads mid-pass (SR-19).
- **The roll browser:**
  - Open is not guarded while the scanner works (GUI1-02, GUI2-02).
  - Rename and Delete protect only a roll reopened from the browser (GUI1-10, GUI2-08).
  - Delete's dialog understates what is lost (GUI1-05, DOC-08).
- **The contact sheet:**
  - Double-clicking a thumbnail toggles its tick (GUI2-04).
  - Return and All re-tick scanned frames (GUI2-05).
  - Decisions come back onto a new strip walked into a used folder name (GUI2-03, GUI1-03).
- **Calibration from the CLI** on an empty transport is unguarded (TP-09), and `tools/uniformity.py` calibrates in an unprompted state (LIB-08).
- **7200 dpi** is offered in the window and refused only once a pass is due (GUI1-08).
- **A flat or blank frame** ends a walk or roll as "end of film", with exit 0 (FR-07, CLI-14).
- **A CLI resume** mixes the earlier run's settings with new ones and re-scans everything from
  `--start-at` (CLI-11, CLI-10).

## Findings filed as user errors

{{USER_FINDINGS}}

## Per area: can do, should not do, unguarded

{{USER_APPENDIX}}
