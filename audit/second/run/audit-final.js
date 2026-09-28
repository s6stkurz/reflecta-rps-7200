export const meta = {
  name: 'audit-final',
  description: 'Bring the docs up to the fixed code with the decisions for Stefan in TODO.md, and re-check every first-audit problem against it',
  phases: [
    { title: 'Docs', detail: 'README, CLAUDE.md facts, docs/, TODO.md' },
    { title: 'Recheck', detail: 'P01-P32 against the final code' },
  ],
}

const REPO = '/home/user/reflecta-rps-7200'
const SP = '/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad'
const HEAD = args.head
const FIRST = '83dbb22'

const DOCS_RESULT = {
  type: 'object',
  properties: {
    branch: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    changed: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, what: { type: 'string' } }, required: ['file', 'what'] } },
    decisions: { type: 'number' },
    tests: { type: 'string' },
  },
  required: ['branch', 'commits', 'changed', 'decisions', 'tests'],
}
const STATUS_SCHEMA = {
  type: 'object',
  properties: {
    problems: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, title: { type: 'string' },
          status: { type: 'string', enum: ['fixed', 'mostly-fixed', 'partly-fixed', 'open', 'regressed', 'superseded'] },
          evidence: { type: 'string' }, remaining: { type: 'string' },
          commits: { type: 'array', items: { type: 'string' } }, new_problems: { type: 'string' },
        },
        required: ['id', 'title', 'status', 'evidence', 'remaining'],
      },
    },
  },
  required: ['problems'],
}

const docs = () => agent(`You work in your own git worktree of ${REPO} (your current directory). FIRST: if branch docs3 exists, \`git checkout docs3\` and continue from \`git log ${HEAD}..HEAD\`; otherwise \`git checkout -B docs3 ${HEAD}\`.
TASK: bring the documentation up to the code at ${HEAD}, after three rounds of fixes following two code audits (audit/, audit/second/). Read the commit messages since 03aacba (\`git log --no-merges 03aacba..${HEAD}\`) -- they say what changed and why -- and audit/second/doc-mismatches.md (claims the second audit found contradicted at 03aacba; re-check each against the CURRENT code).
1. README.md, docs/*.md, research/frame-edge/README.md (where it describes tools/frame_edges), and the docstrings of the tools' --help where a flag changed: correct every factual statement (commands, flags, defaults, file names, what is stored where, what a tool does) to match the code. Keep the author's voice and structure; do not rewrite what is right; never reformat; hand-wrap like the neighbouring text.
2. CLAUDE.md holds Stefan's working rules: change ONLY statements of fact that are now wrong (e.g. what a library entry holds, where the debug spool lives, what DeferredInterrupt covers, PROTOCOL_REVISION's value and why), NEVER a rule, never soften or invert one.
3. TODO.md: close items the code now resolves (strike through with a dated note naming the commit, in the file's existing style). Then add a section "Decisions for Stefan (from the 2026-09 audits)" -- or extend it if it exists -- with every open decision: the "decisions" list in ${REPO}/audit/second/fixes3b/raw/triage.json ("result" -> "decisions": question, proposal, from) and every item under "left" with a proposal in ${REPO}/audit/second/fixes3b/raw/fix3b-*.json and revfix3b-*.json. Merge duplicates. One entry each: the question in a sentence, why it is his, the proposal, where in the code. Group them (hardware measurements; what is sent to the scanner; storage layout; defaults; UI behaviour; tooling). At the very top of that section, a short list headed "Try on the scanner first", naming the three commits that change when film moves (3358414 holds under "reverse direction" no longer mirrored; 2cccdbb an unconfirmed one-member edge reading moves nothing on a walk; b9bee37 scan_roll --approved holds every walked frame, clamped to one command), PROTOCOL_REVISION 7, and that nothing in these rounds has been run on the hardware.
Rules: documentation files only (plus tool help strings if wrong). Tests must still pass (some read docs): ${REPO}/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/ and ${REPO}/.venv/bin/ruff check . Commit per file or topic, each message ending with exactly:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk
Do NOT push or merge. Return the branch, commits, what changed per file, how many decisions TODO.md now lists, and the test result.`, { label: 'docs', phase: 'Docs', schema: DOCS_RESULT, isolation: 'worktree' })

const recheck = ids => agent(`STRICTLY READ-ONLY: modify nothing, commit nothing, run no tests, never talk to hardware. Repository ${REPO}; judge the code at commit ${HEAD} (read with \`git -C ${REPO} show ${HEAD}:<path>\` and \`git -C ${REPO} grep <pattern> ${HEAD}\`; the working tree may differ).
The first code audit (commit ${FIRST}) described these problems, one file each: ${ids.map(id => `audit/problems/${id}-*.md`).join(', ')}. The second audit re-checked them at 03aacba: audit/second/status.md says, per problem, what was still left then. Three rounds of fixes followed (\`git log --no-merges 03aacba..${HEAD}\`).
For EACH problem: read its problem file and its section of audit/second/status.md, then check the code at ${HEAD} yourself -- every sub-issue, not the headline, and especially everything status.md listed as left. Judge from the code, not from commit messages. Status: fixed (every sub-issue resolved), mostly-fixed (the harmful path closed, small parts left), partly-fixed, open, regressed (worse or broken by a later change), superseded (the code no longer exists and the concern does not apply). Evidence cites path:line at ${HEAD}. "remaining" lists precisely what is still open, or "nothing"; say when what remains is a decision for the owner rather than a defect. Note any new problem a fix introduced.`, { label: `recheck:${ids[0]}-${ids[ids.length - 1]}`, phase: 'Recheck', schema: STATUS_SCHEMA, effort: 'high' })

const GROUPS = [['P01','P02','P03','P04','P05','P06','P07','P08'], ['P09','P10','P11','P12','P13','P14','P15','P16'], ['P17','P18','P19','P20','P21','P22','P23','P24'], ['P25','P26','P27','P28','P29','P30','P31','P32']]
const done = args.doneRechecks || []
const [d, ...rs] = await parallel([
  () => args.docsDone ? Promise.resolve(null) : docs(),
  ...GROUPS.filter(g => !done.includes(g[0])).map(g => () => recheck(g)),
])
return { docs: d, rechecks: rs }
