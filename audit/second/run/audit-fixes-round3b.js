export const meta = {
  name: 'audit-fixes-round3b',
  description: 'Triage what fix round 3 left, then fix the remaining defects and the gap passes\' findings per subsystem, each reviewed',
  phases: [
    { title: 'Triage', detail: 'every item the round-3 fixers left: resolved, defect, or a decision for Stefan' },
    { title: 'Fix', detail: 'one agent per subsystem, own worktree and branch' },
    { title: 'Review', detail: 'adversarial check of each branch' },
  ],
}

const REPO = '/home/user/reflecta-rps-7200'
const SP = '/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad'
const BASE = args.base
const FIXES = `${REPO}/audit/second/fixes/raw`
const GROUPS = args.groups
const KEYS = GROUPS.map(g => g.key)

const TRIAGE = {
  type: 'object',
  properties: {
    resolved: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, from: { type: 'string' }, by: { type: 'string' } }, required: ['item', 'from', 'by'] } },
    defects: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, from: { type: 'string' }, group: { type: 'string', enum: KEYS }, what: { type: 'string' }, where: { type: 'string' } }, required: ['item', 'from', 'group', 'what'] } },
    decisions: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, from: { type: 'string' }, question: { type: 'string' }, proposal: { type: 'string' } }, required: ['item', 'from', 'question', 'proposal'] } },
  },
  required: ['resolved', 'defects', 'decisions'],
}
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

phase('Triage')
const triage = args.triage || await agent(`Read-only (modify nothing, commit nothing). Repository ${REPO}; the code to judge is commit ${BASE} (\`git -C ${REPO} show ${BASE}:<path>\`, or \`git -C ${REPO} worktree add\` is NOT allowed -- read with git show / git grep ${BASE}). A code audit (audit/second/) was fixed in round 3 by seven agents in parallel, one per subsystem; ${BASE} is all of their work merged. Each fixer returned a "left" list: items it did not do, because another subsystem owned the code, or because they were a decision for the owner (Stefan), or because they were wrong. Those lists are in ${FIXES}/fix3-*.json and ${FIXES}/revfix3-*.json, under "result" -> "left" (item, why, and often proposal).
Go through EVERY left item of every file. For each, check the code at ${BASE} and put it in exactly one list:
- resolved: another branch's work (now merged) already does it, or it was wrong -- say which commit or why (\`git -C ${REPO} log --oneline e700609..${BASE}\` lists the commits).
- defects: a real defect still present at ${BASE}, fixable in code without the scanner and without a decision -- give the subsystem group that owns the code (${GROUPS.map(g => `${g.key}: ${g.scope}`).join(' | ')}), what to do, and where.
- decisions: a choice that is Stefan's (UI behaviour, storage layout, defaults, anything needing a hardware measurement, anything changing what is sent to the scanner) -- state the question and the fixer's proposal.
Merge duplicates (the same item raised by several fixers) into one entry, listing every source in "from". Be concrete; cite file:line at ${BASE}.`, { label: 'triage', phase: 'Triage', schema: TRIAGE, effort: 'high' })

const COMMON = `
You work in your own git worktree of ${REPO} (your current directory). FIRST: if the branch named below already exists (\`git branch --list BRANCH\`), an earlier run of this task was cut off -- \`git checkout BRANCH\` (prune worktrees if git says it is checked out elsewhere) and continue from \`git log ${BASE}..HEAD\`; otherwise \`git checkout -B BRANCH ${BASE}\`. ${BASE} is the integration branch with every earlier audit fix merged.
Context: audit/second/ is a verified code audit (areas/<area>.md: each finding with its verdict; status.md: the first audit's problems). Rules: no scanner is attached -- never run hardware tests or open a device, and never change what is sent to the device in normal operation (if an item needs that, leave it as a decision). Follow CLAUDE.md (comments explain why, in the surrounding voice; never reformat files; param units, never millimetres, for transport distances; the demo is the real software with different inputs; file every scan with its raw bytes). Verify each item against the code first; an item that is wrong or already fixed goes under "left" with the reason. Every behaviour change gets a test that fails without it (stash the fix and check). Keep changes minimal. Decisions (UI choices, storage layout, defaults, anything needing a hardware measurement) are not implemented: list them under "left" with a proposal.
Windows matters (CI runs it): tests/conftest.py has windows_mkdir; os.replace needs the retry helper already in the code.
Test commands (all must pass before each commit):
  ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  xvfb-run -a ${SP}/guienv/bin/python -m pytest -q -p no:cacheprovider --no-cov -n 2 tests/test_gui.py
  RPS7200_NO_TIFFFILE=1 ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  ${REPO}/.venv/bin/ruff check .
  ${REPO}/.venv/bin/ty check --python ${REPO}/.venv --exclude tests/ --exclude tools/
Commit as you go in this repo's style, each message ending with exactly:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk
Do NOT push or merge. Return your branch, commits, what you changed per item, what you left and why (with proposals), and the test results.`

const results = await pipeline(
  GROUPS,
  g => {
    const mine = ((triage && triage.defects) || []).filter(d => d.group === g.key)
    return agent(`${COMMON}
BRANCH = fix3b/${g.key}. Your subsystem: ${g.scope}
YOUR ITEMS, severity first:
1. Defects the round-3 fixers left for this subsystem, as triaged against ${BASE}: ${JSON.stringify(mine)}
2. From the second audit's gap passes, every finding of severity critical, high or medium (low where it is a few lines) in ${g.gapAreas.map(a => `audit/second/areas/${a}.md`).join(', ') || '(none)'} that falls in your subsystem's code${g.gapNote ? ` -- ${g.gapNote}` : ''}. Skip any the triage or round 3 already fixed (check the code at ${BASE}).`, { label: `fix3b:${g.key}`, phase: 'Fix', schema: RESULT, isolation: 'worktree' })
  },
  (r, g) => r ? agent(`ADVERSARIAL REVIEWER, read-only (do not modify files or commit). Repository ${REPO}. Branch "${r.branch}" was made from ${BASE}. Review \`git -C ${REPO} diff ${BASE}..${r.branch}\` and the changed code in context. Look for bugs, regressions, tests that cannot fail (reason about the base code), CLAUDE.md violations, Windows-only failure modes, anything that changes what is sent to the scanner in normal operation, and claimed fixes that do not fix the item. Claimed: ${JSON.stringify(r.done)}. Report only real issues with file, line and a concrete fix.`, { label: `review3b:${g.key}`, phase: 'Review', schema: REVIEW, effort: 'high' }).then(v => ({ group: g.key, result: r, review: v })) : { group: g.key, result: null, review: null },
)
return { triage, results }
