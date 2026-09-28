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
| 1. Second audit | **done** | 15 areas and 6 gap passes, every finding verified: 437 findings (0 critical, 24 high), 1 refuted |
| 2. Write-up | **done** | README, status.md, areas/, dataflow, persisted state, library exactness, demo, user errors, doc mismatches; regenerate with `run/gen/merge2.py` then `run/gen/gen3.py` |
| 3. Fix round 3 | **done** | merged as `49bd7d0` and pushed: seven subsystems fixed, reviewed, review acted on, merged (1748 / 1681 / 2227 tests passed, ruff and ty clean). **Three commits change when film moves** (3358414 holds no longer mirror under "reverse", 2cccdbb an unconfirmed one-member reading moves nothing on a walk, b9bee37 `scan_roll --approved` holds every walked frame, clamped); PROTOCOL_REVISION is 7; to be tried on the scanner first, each one revert away. |
| 3b. Follow-up round | running | `run/audit-fixes-round3b.js` from `49bd7d0` (run `wf_8f6a1a88-8aa`): triage of the 144 left items, then capture-device, storage, session-cli, window, demo-framing-analysis on `fix3b/<group>`, each reviewed; results committed to `fixes3b/raw/`. Restart: same args, plus `"triage": <result of fixes3b/raw/triage.json>` once the triage is on disk; a fixer continues its branch. |
| 4. Docs, re-check | not started | docs pass with the fixers' proposals as "Decisions for Stefan" in TODO.md; re-check P01-P32 and the round-3 items; CI on three systems |

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
- **Review fixes:** start `run/audit-fixes-wave3-reviews.js` with `{"base": "e700609", "groups": [the groups not yet done]}`; an agent continues its branch.
- **Fix round 3:** start `run/audit-fixes-wave3.js` again with the same `args`
  plus `"skip": [groups already finished]` (their results are in
  `audit/second/fixes/raw/fix3-<group>.json`). An agent whose branch
  `fix3/<group>` exists continues it instead of starting over.
- **The checkpoint loop** (`run/checkpoint3.sh`) follows every journal listed in
  the scratchpad's `journals.txt` (`<journal> <out dir>` per line) and commits
  finished results; add the new run's journal there when a workflow is restarted.
- **The merge:** in `/home/user/integ3`, `git merge-base --is-ancestor fix3/<g> HEAD` says which branches are in; abort a half-done merge and merge the rest in the order filing, library, window, demo-framing-outputs, tests, with a "Merge fix-up" commit wherever the suites need one.
- Fix branches (`fix3/*`) live only in the local clone until merged; the
  merged branch is what is pushed.
