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
| 1. Second audit | nearly done | all 15 areas read and verified (361 findings, 1 refuted), critic done; the 6 gap readers run as `wf_8807e7b9-eec` (they hit the spend limit once) |
| 2. Write-up | partial | `areas/*.md` for all 15 areas committed; summary and cross-cutting files follow once the gaps are in |
| 3. Fix round 3 | running | base `e700609`; two workflows of `run/audit-fixes-wave3.js`: `wf_8316fb77-f1f` (capture, library, device, tests) and `wf_64d04286-f1a` (filing, window, demo-framing-outputs); branches `fix3/<group>`, each reviewed adversarially |
| 4. Re-check | not started | |

After step 3: merge `fix3/*` into this branch (resolve conflicts, act on the
reviews), full suite with and without tifffile and under Tk, ruff, ty, push, CI
on three systems; then a docs pass (README, CLAUDE.md facts, docs, TODO with the
fix agents' proposals under "Decisions for Stefan"), then step 4.

## If this stopped

Everything needed is in [`run/`](run/): the workflow scripts, and the helper
that says what is already done.

- **The audit:** run `python3 audit/second/run/audit_restart_args.py` and start
  `run/rps7200-full-audit-2b.js` with what it prints as `args`. It reads
  `audit/second/raw/` and skips every reader, verifier, critic and gap reader
  whose result is already there -- nothing is run twice.
- **Fix round 3:** start `run/audit-fixes-wave3.js` again with the same `args`
  plus `"skip": [groups already finished]` (their results are in
  `audit/second/fixes/raw/fix3-<group>.json`). An agent whose branch
  `fix3/<group>` exists continues it instead of starting over.
- **The checkpoint loop** (`run/checkpoint3.sh`) follows every journal listed in
  the scratchpad's `journals.txt` (`<journal> <out dir>` per line) and commits
  finished results; add the new run's journal there when a workflow is restarted.
- Fix branches (`fix3/*`) live only in the local clone until merged; the
  merged branch is what is pushed.
