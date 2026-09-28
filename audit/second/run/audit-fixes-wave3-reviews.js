export const meta = {
  name: 'audit-fixes-wave3-reviews',
  description: 'Act on the adversarial reviews of fix round 3, each on its own fix3 branch',
  phases: [{ title: 'Fix', detail: 'one agent per branch, own worktree' }],
}

const REPO = '/home/user/reflecta-rps-7200'
const SP = '/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad'
const BASE = args.base
const RAW = `${REPO}/audit/second/${args.raw || 'fixes/raw'}`
const P = args.prefix || 'fix3'

const RESULT = {
  type: 'object',
  properties: {
    branch: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    done: { type: 'array', items: { type: 'object', properties: { issue: { type: 'string' }, change: { type: 'string' } }, required: ['issue', 'change'] } },
    left: { type: 'array', items: { type: 'object', properties: { issue: { type: 'string' }, why: { type: 'string' } }, required: ['issue', 'why'] } },
    tests: { type: 'string' },
  },
  required: ['branch', 'commits', 'done', 'left', 'tests'],
}

const prompt = key => `You work in your own git worktree of ${REPO} (your current directory). FIRST run \`git checkout ${P}/${key}\` (if git says it is checked out in another worktree, run \`git worktree prune\` and try again; if still refused, \`git checkout -B ${P}/${key}-rev ${P}/${key}\` and report that branch). This branch was made from ${BASE} by a fixer whose report is ${RAW}/${P}-${key}.json; an adversarial reviewer then checked it, and the review is ${RAW}/review${P.slice(3)}-${key}.json ("result" -> "issues": severity, file, line, problem, fix). If you were started before on this task and cut off, \`git log ${BASE}..HEAD\` shows what is already done.
YOUR TASK: act on every issue in that review. Verify each against the code first; fix every real one (majors first) with a test that fails without the fix (check it: stash the fix, run the test). If an issue is wrong, say why under "left". Other fix3 branches (\`git branch --list '${P}/*'\`) were made in parallel for other subsystems and will be merged together with yours: if an issue is really in another subsystem's files and \`git log ${BASE}..${P}/<other>\` shows that branch already fixed it, leave it here and name the commit; otherwise make the smallest change here.
Rules: no scanner is attached -- never open a device or run hardware tests; never change what is sent to the device in normal operation. Follow CLAUDE.md (comments explain why, in the surrounding voice; never reformat files; param units, never millimetres, for transport distances; no demo-only branches above the seam). Windows matters (CI runs it): tests/conftest.py has windows_mkdir to emulate Windows' mkdir answers.
Test commands (all must pass before each commit):
  ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  xvfb-run -a ${SP}/guienv/bin/python -m pytest -q -p no:cacheprovider --no-cov -n 2 tests/test_gui.py
  RPS7200_NO_TIFFFILE=1 ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/
  ${REPO}/.venv/bin/ruff check .
  ${REPO}/.venv/bin/ty check --python ${REPO}/.venv --exclude tests/ --exclude tools/
Commit as you go in this repo's style (what was wrong, why it mattered, what changed), each message ending with exactly:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk
Do NOT push or merge. Return the branch, commit shas, what you changed per issue, what you left and why, and the test results.`

const results = await parallel(args.groups.map(key => () =>
  agent(prompt(key), { label: `rev${P}:${key}`, phase: 'Fix', schema: RESULT, isolation: 'worktree' })
    .then(r => ({ group: key, result: r }))))
return results
