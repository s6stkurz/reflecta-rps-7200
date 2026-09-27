export const meta = {
  name: 'audit-fixes-wave3',
  description: 'Fix what the second audit left open and found new, per subsystem, in worktrees; review each branch adversarially',
  phases: [
    { title: 'Fix', detail: 'one agent per subsystem, own worktree and branch' },
    { title: 'Review', detail: 'adversarial check of each branch' },
  ],
}

const REPO = '/home/user/reflecta-rps-7200'
const SP = '/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad'
const BASE = args.base

const COMMON = `
You work in your own git worktree of ${REPO} (your current directory). FIRST run \`git checkout -B BRANCH ${BASE}\` with the branch name given below, so you start from the integration branch as it is now.
Context: a first audit (audit/, problems P01-P32) was fixed in two rounds; a second audit (audit/second/) re-checked it. audit/second/status.md says, per first-audit problem, what the code does now and what is STILL OPEN; audit/second/areas/<area>.md lists the second audit's findings with their verdict (confirmed / partly / found-by-verifier / unverified). Commit messages since 83dbb22 say what was changed and why.
Rules: no scanner is attached -- never run hardware tests or open a device, and never change what is sent to the device in normal operation without saying so and bumping PROTOCOL_REVISION in rps7200/protocol.py. Follow CLAUDE.md (comments explain why, in the surrounding voice; never reformat files; param units, never millimetres, for new transport code; the demo is the real software with different inputs -- no demo-only branches above the seam; file every scan with its raw bytes). Verify each item against the current code before changing anything: an item that is wrong or already fixed goes under "left" with the reason. Every behaviour change gets a test that fails without the change (check it: stash the fix and run the test). Keep each change minimal and local; do not refactor beyond what an item needs.
Some items are decisions for Stefan rather than defects (a UI choice, a measurement only the hardware can make, a trade-off with no clear right answer): do not implement those; list them under "left" with a one-paragraph proposal, and they will be recorded in TODO.md.
Test commands (all must pass before you commit):
  ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  xvfb-run -a ${SP}/guienv/bin/python -m pytest -q -p no:cacheprovider --no-cov -n 2 tests/test_gui.py
  RPS7200_NO_TIFFFILE=1 ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  ${REPO}/.venv/bin/ruff check .
  ${REPO}/.venv/bin/ty check --exclude tests/ --exclude tools/
Windows matters (CI runs it): no os.replace/rename over a file another handle may hold without a retry, no mkdir loops that treat FileExistsError as "name taken" without checking the name exists; tests/conftest.py has windows_mkdir to emulate Windows' mkdir answers.
Commit in small commits in this repo's style (what was wrong, why it mattered, what changed), each ending with exactly:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk
Commit as you go so a stopped run keeps its work. Do NOT push or merge. Return your branch, commit shas, what you changed per item, what you left and why (with proposals for Stefan's decisions), and the test results.
`

const RESULT = {
  type: 'object',
  properties: {
    branch: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    done: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, change: { type: 'string' } }, required: ['item', 'change'] } },
    left: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, why: { type: 'string' }, proposal: { type: 'string' } }, required: ['item', 'why'] } },
    tests: { type: 'string' },
  },
  required: ['branch', 'commits', 'done', 'left', 'tests'],
}
const REVIEW = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['good', 'needs-fixes'] },
    issues: { type: 'array', items: { type: 'object', properties: { severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, line: { type: 'number' }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'file', 'problem', 'fix'] } },
  },
  required: ['verdict', 'issues'],
}

const groupPrompt = g => `${COMMON}
BRANCH = fix3/${g.key}. Your subsystem: ${g.scope}
YOUR ITEMS:
- From audit/second/status.md, everything still open (and any "new problem the fix introduced") under: ${g.problems.join(', ') || '(none)'}.
- From the second audit, every finding of severity critical, high or medium in ${g.areas.map(a => `audit/second/areas/${a}.md`).join(', ') || '(none)'}, whatever its verdict, but never one the file lists as refuted; low ones too where the fix is a few lines.
${g.extra ? `- Also: ${g.extra}` : ''}
Other agents work in parallel on other subsystems; if an item needs a change mainly in another subsystem's files, make the smallest change here or leave it saying which subsystem owns it. Work in order of severity: data loss and wedge risks first.`

const results = await pipeline(
  args.groups,
  g => agent(groupPrompt(g), { label: `fix3:${g.key}`, phase: 'Fix', schema: RESULT, isolation: 'worktree' }),
  (r, g) => r ? agent(`ADVERSARIAL REVIEWER, read-only (do not modify files or commit). Repository ${REPO}. Branch "${r.branch}" was made from ${BASE}. Review \`git -C ${REPO} diff ${BASE}..${r.branch}\` and the changed code in context. Look for bugs, regressions, tests that cannot fail (check by reasoning about the base code), CLAUDE.md violations, Windows-only failure modes (file replace while open, mkdir answers, path separators, encodings), anything that changes what is sent to the scanner in normal operation, and claimed fixes that do not fix the item. Claimed: ${JSON.stringify(r.done)}. Report only real issues with file, line and a concrete fix.`, { label: `review3:${g.key}`, phase: 'Review', schema: REVIEW, effort: 'high' }).then(v => ({ group: g.key, result: r, review: v })) : { group: g.key, result: null, review: null },
)
return results
