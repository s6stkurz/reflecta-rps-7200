# Progress of the audit-and-fix run

Kept up to date as the work goes, so that a run stopped by a usage limit -- or
a fresh session -- knows exactly where it is and what comes next. Everything
listed as done is committed on `claude/clever-mayer-dy1j3o`.

## Plan

1. **Finish the second audit** (read-only, code at `03aacba`). The workflow
   `rps7200-full-audit-2` stopped at the spend limit with 8 of its agents done;
   it is resumed from its cache (run `wf_05e4ee75-b95`) so only the missing
   ones run: 12 area readers, every adversarial verification, the
   completeness critic and up to 6 gap readers. Each finished result is
   committed to `audit/second/raw/` as it lands.
2. **Write the second audit up in full**, like the first: summary, every area,
   dataflow, persisted state, library exactness, demo vs direct, user errors,
   doc mismatches, and `status.md` for P01-P32.
3. **Fix round 3**, on branches merged only once green: everything
   `status.md` lists as still open, the two problems the fixes introduced, and
   the second audit's verified findings (critical, high, medium; low where
   cheap). Each branch reviewed adversarially before merging. Decisions that
   are Stefan's go to TODO.md under "Decisions for Stefan".
4. **Re-check** P01-P32 and the round-3 fixes against the merged code, update
   `status.md` and the summary, CI green on Ubuntu, macOS and Windows.

Not in the plan, by CLAUDE.md: anything that drives the scanner.

## State

| Step | State | Notes |
|---|---|---|
| 1. Second audit | running | resumed 2026-09-27 11:20 UTC |
| 2. Write-up | partial | status.md, 2 areas and dataflow written from the first, cut-short run |
| 3. Fix round 3 | not started | waits for step 1, so fixes do not move the code under the readers |
| 4. Re-check | not started | |

## If this stopped

- A workflow that died on a limit: re-run it with `resumeFromRunId`; finished
  agents replay from cache. Results already in `audit/second/raw/` are safe.
- Fix branches live only in the local clone until merged; the merged branch
  is what is pushed.
