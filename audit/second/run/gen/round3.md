Three rounds of fixes followed this audit, on branch `claude/clever-mayer-dy1j3o`:

1. **Round 3** (`49bd7d0`): seven subsystems -- capture and debug filing, device safety,
   filing, the library, the window, demo/framing/outputs, the tests -- each fixed on its own
   branch against this audit's findings and [status.md](status.md), reviewed adversarially,
   the review acted on, merged.
2. **The follow-up round** (`f4984ea`): a triage of everything round 3 left, then five
   subsystem fixers on the remaining defects and the six gap passes' findings -- among them
   keeping the host awake through a roll, a free-space check before one, crash leftovers
   found and finished, and every delivered file written beside and renamed over -- again each
   reviewed and the review acted on.
3. **A docs pass**: README, docs/, tool help and CLAUDE.md's facts (no rule touched) say what
   the code does; TODO.md lists every open decision.

**Where the first audit's problems stand now:** 13 fixed, 18 mostly fixed, 1 partly fixed
(P31), none open or regressed -- see [final/status.md](final/status.md). What is left is
almost all a decision rather than a defect, and each one is in TODO.md under **"Decisions
for Stefan (from the 2026-09 audits)"**, which starts with **"Try on the scanner first"**:
three commits change when the film moves (3358414, 2cccdbb, b9bee37) and PROTOCOL_REVISION
is 7. **Nothing in these rounds has been run on the scanner.**

Each round's agent reports, reviews and triage are kept in [fixes/raw/](fixes/raw/) and
[fixes3b/raw/](fixes3b/raw/); the scripts that ran them in [run/](run/).
