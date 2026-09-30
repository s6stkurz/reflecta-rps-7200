export const meta = {
  name: 'rps7200-full-audit-2b',
  description: 'Finish the second audit: the area readers, verifiers, critic and gap readers the spend limit cut off; results already on disk are reused, not re-run',
  phases: [
    { title: 'Find', detail: 'one reader per subsystem not yet read, from the code itself' },
    { title: 'Verify', detail: 'adversarial re-read of every finding against the code' },
    { title: 'Gaps', detail: 'completeness critic, then targeted readers for what was missed' },
  ],
}

const REPO = '/home/user/reflecta-rps-7200'
const GUI_SPLIT = args.guiSplit
const GUI_END = args.guiEnd
const HEAD = args.head
const FIRST = '83dbb22'

const RULES = `
You are auditing the repository at ${REPO} (a Python USB driver + Tk GUI for the Reflecta RPS 7200 film scanner).
STRICTLY READ-ONLY: do not create, modify or delete any file anywhere (no Write/Edit, no "make", no pytest, no uv sync, no ruff --fix, no git commands that change state). Use Read, Grep, Glob and read-only Bash (cat, sed -n, grep, rg, wc, git log, git show, git blame). There is no scanner attached; never try to talk to hardware.
Judge ONLY from the actual code. README.md, CLAUDE.md, docs/*.md, TODO.md, docstrings and comments are CLAIMS, not facts: when you rely on behaviour, confirm it in the executable code path. When a doc/comment claims something the code does not do (or does differently), that is itself a finding with category "doc-mismatch" and doc_reference naming the doc file:line and the claim.
The owner's central requirement: the library (library/ entries) must hold the EXACT bit data of every scan — raw bytes as received, the shading reference, the CCD mask, all parameters/commands/state needed — so that everything can be recalculated and evaluated later with newer code. Anything that is lost, altered, rounded, truncated, stored only in corrected form, stored non-atomically, or not recorded (and so not re-derivable) is important.
Second requirement: the demo (DemoScanner, --demo) must be the real software with different inputs — any divergence in behaviour/constants/refusals between DemoScanner and DirectScanner, or any "if demo" branching above the seam, matters.
Third: user errors — what can an operator (GUI or CLI) do that they should not, which mistakes are unguarded, what leaves inconsistent state, what could wedge the scanner or lose data.
This is the SECOND audit of this repository. The first one (at commit ${FIRST}) is written up in audit/; the code has changed a great deal since (\`git log --oneline ${FIRST}..${HEAD}\`). Do NOT read audit/ -- judge the code afresh; a separate stage compares against the first audit.
Every finding must cite path:line locations and quote the relevant code in "evidence". No padding: only real, specific problems. Severity: critical = data loss / scanner wedge / wrong pixels silently; high = wrong results or corrupt state in a plausible path; medium = real but narrow; low = minor; info = noteworthy observation (not a defect).
Also report: persisted_state (every file/field this area writes or reads on disk: path pattern, format/dtype, raw vs corrected, who writes, who reads, whether it is lossless/exact), user_actions (can_do / should_not_do / unguarded_mistakes as concrete sentences), and dataflow_notes (how data enters, is transformed, and leaves this area, with function names and file:line).
`

const FINDING = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
    category: { type: 'string', enum: ['bug', 'data-integrity', 'library-completeness', 'demo-divergence', 'user-error', 'concurrency', 'hardware-safety', 'doc-mismatch', 'design', 'test-gap', 'dead-code', 'error-handling'] },
    locations: { type: 'array', items: { type: 'string' } },
    evidence: { type: 'string' },
    description: { type: 'string' },
    failure_scenario: { type: 'string' },
    recommendation: { type: 'string' },
    doc_reference: { type: 'string' },
  },
  required: ['id', 'title', 'severity', 'category', 'locations', 'evidence', 'description', 'failure_scenario', 'recommendation'],
}

const FIND_SCHEMA = {
  type: 'object',
  properties: {
    area_summary: { type: 'string' },
    findings: { type: 'array', items: FINDING },
    persisted_state: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          what: { type: 'string' }, path_pattern: { type: 'string' }, format: { type: 'string' },
          raw_or_corrected: { type: 'string' }, written_by: { type: 'string' }, read_by: { type: 'string' },
          exact: { type: 'string' },
        },
        required: ['what', 'path_pattern', 'format', 'raw_or_corrected', 'written_by', 'read_by', 'exact'],
      },
    },
    user_actions: {
      type: 'object',
      properties: {
        can_do: { type: 'array', items: { type: 'string' } },
        should_not_do: { type: 'array', items: { type: 'string' } },
        unguarded_mistakes: { type: 'array', items: { type: 'string' } },
      },
      required: ['can_do', 'should_not_do', 'unguarded_mistakes'],
    },
    dataflow_notes: { type: 'string' },
  },
  required: ['area_summary', 'findings', 'persisted_state', 'user_actions', 'dataflow_notes'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          verdict: { type: 'string', enum: ['confirmed', 'partly', 'refuted'] },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
          corrected_description: { type: 'string' },
          corrected_locations: { type: 'array', items: { type: 'string' } },
          reasoning: { type: 'string' },
        },
        required: ['id', 'verdict', 'severity', 'reasoning'],
      },
    },
    additional_findings: { type: 'array', items: FINDING },
    persisted_state_corrections: { type: 'string' },
  },
  required: ['verdicts', 'additional_findings'],
}

const AREAS = [
  { key: 'transport-protocol', focus: `USB transport and command layer: rps7200/usb_transport.py, rps7200/protocol.py, rps7200/usbpcap.py, the command-sending / state-machine / timeout / error-handling / sense parts of rps7200/direct.py (DirectScanner open/close, calibrate, scan command sequence, READ_STATE, SLIDE, timeouts, INFRARED_FLOOR_S, PROTOCOL_REVISION), tools/check_scanner.py, tools/verify_protocol.py, tools/parse_capture.py, tools/verify_capture.py, docs/protocol.md. Look for: abandoned reads, timeouts shorter than the pass, forbidden commands (SET_SCAN_HEAD 0xD2, STOP SCAN, IEEE1284 reset) reachable, retry loops that resend, exception paths that leave the device mid-read, missing close(), thread safety of the transport, what command/response data is NOT recorded to the library (sense bytes, READ_STATE bytes, mode select bytes, gain/offset), and pcap reader returning interrupt payloads.` },
  { key: 'decode-and-debug-filing', focus: `Bytes to pixels and debug filing in rps7200/direct.py (decode, decode_index, line tags, channel interleave, bit depth, CCD mask, shading acquisition, metering/exposure, infrared tied/untied, debug-mode spooling to temp files and assembly into library entries after close()), rps7200/direction.py, rps7200/shading.py, rps7200/defects.py. Key question: is the raw USB byte stream stored bit-exactly (every byte received, including headers/padding/line tags), plus every parameter needed to re-decode and re-correct? What happens to the spool if the process crashes, close() is never called, the disk fills, or debug is off? Is anything rounded/clipped/cast (float->uint16, >>8, dtype) before storage? Is exposure's 16-bit wrap guarded? Is read_direction recorded consistently?` },
  { key: 'demo-parity', focus: `The demo seam: rps7200/demo.py (DemoScanner) versus rps7200/direct.py (DirectScanner), the injection point session._open_scanner in rps7200/session.py, every --demo / demo / look-only path in tools/gui.py and tools/*.py, tests/test_demo.py and tests/conftest.py fakes. Enumerate EVERY public method/attribute/constant DirectScanner exposes that the session/GUI uses and compare DemoScanner's version: signature, return shape/meta keys, exceptions raised, timing, constants retyped instead of taken from DirectScanner (caps, ramps, param_for_mm, units, aperture, frame width, exposure limits), refusals (no film, no scanner, far frame) implemented as GUI greying rather than backend exceptions, any "if demo"/"demo" branch in the scan path, and what the demo files in the library (does it write library entries marked as demo? could demo entries be confused with real scans, pollute reconstruct/verify, or overwrite real ones?). Where does the demo get its pictures (other library entries) and what if the library is empty?` },
  { key: 'library', focus: `The library store: rps7200/library.py and tools/library.py (save, corrected(entry), reconstruct, verify, migrate-raw, checksums, entry naming/collision, meta schema, scan.tif, shading.npz, raw bytes gzip, CCD mask), rps7200/uniformity.py, tools/uniformity.py. Determine precisely what one entry contains, byte for byte: raw bytes (exact? compressed losslessly?), decode, shading reference, mask, meta (which fields, from where), prescans, bracket members, roll frames. Is saving atomic (temp + rename) or can a crash leave a half entry that verify/reconstruct/demo later trust? Name collisions (two scans in the same second, -2/-3 suffixes, concurrent writers FrameWriter + GUI)? Is corrections= honoured everywhere? Does reconstruct compare bit-exactly? Can verify distinguish deliberate uncalibrated entries? What cannot be recomputed from an entry today?` },
  { key: 'session-roll', focus: `rps7200/session.py in full: ScanSession, jobs, FrameWriter (background gzip thread), Roll, prescans, holds (_hold_to_approved), nudge, approved.json, roll.json, rolls/ directory, _open_scanner seam, edge_reader, cancellation, error propagation from worker threads, queue shutdown, what happens when a job fails mid-roll, when the GUI closes during a roll, when disk fills, when two rolls run, when the writer thread dies silently. What state is saved where (library entries, rolls/, frame*.tif, approved.json, roll.json) and in raw or corrected form; is it atomic; can a resumed roll misnumber or overwrite frames? Does anything hold the device open during heavy local work (gzip) against the rule?` },
  { key: 'framing-units', focus: `rps7200/framing.py, rps7200/shortcuts.py, tools/frame_edges/* (EdgeWatch, propose_centred, detectors), and how the session/GUI use them. Check: transport distances in param units vs any remaining millimetre usage (the owner prohibits mm), constants (FRAME_WIDTH_UNITS 350.6, aperture 345.2 vs 344.5 inconsistencies, param+1.84 ramp, param 1..87 caps, param 0 no-op) defined once or retyped in several places, division by zero / empty prescans / NaN, sign errors, off-by-one in frame numbering, what happens when the edge detector finds nothing, thread safety of EdgeWatch, and whether framing results are persisted with enough data to recompute them.` },
  { key: 'gui-part1', focus: `tools/gui.py lines 1-${GUI_SPLIT} (read the rest only for context). The operator's window. For each control/action: what it calls, whether it runs on the Tk thread or a worker, whether errors surface to the user, what the user can do out of order (scan before calibrate, change settings mid-scan, close window mid-scan, press buttons twice, pick dpi/IR combos that exceed the 10-minute/wedge budget, save over files, choose a non-film setting), whether any control bypasses ScanSession, whether anything uncorrected (raw) is shown or exported to the user, any demo/look-only branching, Tk calls from worker threads.` },
  { key: 'gui-part2', focus: `tools/gui.py lines ${GUI_SPLIT}-${GUI_END} (read the rest only for context). The operator's window: contact sheet, roll dialog, big view, Save As, nudges, frame navigation (prev/next slide), approved.json writing (on_scan_chosen), roll submission. For each control/action: what it calls, thread, error surfacing, out-of-order use, double clicks, closing mid-operation, exporting raw instead of corrected, any demo/look-only branching, Tk calls from worker threads, overwrite of user files.` },
  { key: 'outputs', focus: `rps7200/export.py, rps7200/tiff.py (with and without tifffile), rps7200/dng.py, rps7200/preview.py, rps7200/mono.py, rps7200/bracket.py, rps7200/settings.py, rps7200/console.py (incl. DeferredInterrupt), rps7200/__init__.py. Check bit depth preservation, byte order, dtype casts, clipping, gamma/inversion, metadata written, the two TIFF paths agreeing, bracket merge correctness (confidence gate, clipped sample handling), settings persistence (where, format, corrupt file handling, defaults that are unsafe), anything that writes corrected data where raw is expected or vice versa, and overwrite without confirmation.` },
  { key: 'cli-operator-tools', focus: `Operator command-line tools and build: tools/scan.py, tools/scan_roll.py, tools/library.py (CLI surface), tools/make_comparison.py, tools/filing_load_test.py, tasks.py, Makefile, pyproject.toml, packaging/60-rps7200.rules, .github workflows if present. Argument validation and user errors: dangerous flag combos (--no-fast-ir at low dpi, --reuse with stale shading, --frames beyond the roll, 7200 dpi RGBI memory/time), foreground runtime vs the 10-minute kill, whether they file to library with raw bytes always, whether debug/filing defaults differ between tools, what happens on Ctrl-C mid-scan (abandoned read -> wedge?), exit codes, cross-platform issues (Windows paths, env vars).` },
  { key: 'probe-and-analysis-tools', focus: `Probe and analysis tools: tools/hold_probe.py, tools/transport_probe.py, tools/byte14_probe.py, tools/gain_probe.py, tools/fast_ir_probe.py, tools/exposure_probe.py, tools/transport_truth.py, tools/exposure_headroom.py, tools/linearity.py, tools/dpi_analysis.py, tools/registration_margin.py, tools/roll_registration_study.py, tools/roll_registration_walk.py. Check: do probes bypass ScanSession/DirectScanner filing (scans that leave no library entry), send hazardous commands, use millimetres, abandon reads, or duplicate constants? Do analysis tools read raw vs corrected correctly (library.corrected), measure delivered files rather than recomputations, depend on library fields that may be missing in legacy entries, and still run against the current library format?` },
  { key: 'docs-readme-claude', focus: `Documentation vs code for README.md and CLAUDE.md. Go claim by claim: every command, flag, default, number, file name, function name, path, behaviour statement (e.g. 'DirectScanner files every scan when debug on', 'make test skips hardware', 'library.save takes raw pixels', 'FrameWriter gzips on its own thread', 'nine tests in test_hardware.py', timing medians, constants 345.2/344.5/350.6/2.84/88.8/0.80/1.84, INFRARED_FLOOR_S 212, PROTOCOL_REVISION, decode_index, _note_reversal, carriage_state, BLUE_RGBI_HEADROOM values, param_for_mm staticmethod, session._open_scanner, EdgeWatch/propose_centred, tools/library.py subcommands, make targets in Makefile/tasks.py). For each, check the code and report mismatches (category doc-mismatch, with doc_reference). Only report claims that are wrong, stale, or unverifiable in code; note in area_summary how many claims you checked.` },
  { key: 'docs-plans-todo', focus: `Documentation vs code for docs/*.md and TODO.md (and research/frame-edge/README.md, REPORT.md only where they describe tools/frame_edges). Focus on present-tense statements about what the code does now, file/function names, defaults, constants, 'fixed'/'not fixed' status claims in TODO.md (is a listed known problem actually already fixed in code, or a claimed fix absent?), and plans marked done that are not implemented. Report mismatches as doc-mismatch findings with doc_reference. Also report TODO items that describe real open defects that are still present in code (category per defect).` },
  { key: 'changes-since-first-audit', focus: `The fixes themselves: \`git diff ${FIRST}..${HEAD} -- rps7200 tools tasks.py Makefile .github\` (read the diff in pieces, then the changed functions in full). Look for regressions the fixes introduced: new exception paths that abandon a read or leave the device open, anything that changes the commands sent in normal operation without a PROTOCOL_REVISION bump, new retry loops that can spin, os.replace/rename on Windows with a file open (PermissionError), new files written with the device open and idle (compression, large writes), new state that is written but never read or read but never written, new record fields that legacy entries lack and readers assume, threads added without joins, and behaviour a CLAUDE.md rule forbids (demo-only branches above the seam, millimetres for transport, SET_SCAN_HEAD).` },
  { key: 'tests', focus: `The test suite tests/*.py and tests/conftest.py vs the code. Find: important behaviours with no test (library exactness, atomic saves, demo parity, FrameWriter failure, cancellation, GUI user errors), tests that exercise a fake instead of the real code path (fakes in conftest diverging from DirectScanner/DemoScanner), tests asserting on recomputations rather than delivered files, tests that silently skip everywhere (hardware, parity, Tk-less), and assertions that are too weak to catch the failure they are named for. Cite the test and the untested code.` },
]

const DATAFLOW_PROMPT = `${RULES}
YOUR TASK: produce a precise, code-verified DATAFLOW description of this driver (not a findings hunt, though report findings you notice). Trace these flows end to end, naming every function hop as path:line, the data's type/shape/dtype at each hop, which thread it runs on, and every point where something is persisted to disk (path, format, raw vs corrected) or shown to the user:
 1. Single scan from the GUI (calibrate -> meter -> scan pass(es) -> USB bytes -> decode -> direction -> shading/mask -> library.save -> corrected view / Save As).
 2. Single scan from tools/scan.py (incl. --bracket and --reuse).
 3. Roll: walk/prescans -> frame edges (EdgeWatch/propose_centred) -> contact sheet -> approved.json -> Roll submit -> holds/nudges -> per-frame scan -> FrameWriter -> library + frame*.tif + roll.json.
 4. Demo: DemoScanner inputs (where its pixels/bytes come from) through the same path.
 5. Offline: library entry -> library.corrected / reconstruct / verify / migrate-raw / analysis tools.
 6. Settings and calibration cache (calibration/shading.npz etc.): who writes/reads.
Return: flows (each with ordered steps), a module dependency list (which rps7200/tools module imports which, and any cycles or layering violations such as rps7200 importing tools), a persisted-state table, and findings.`

const DATAFLOW_SCHEMA = {
  type: 'object',
  properties: {
    flows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          steps: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                where: { type: 'string' }, does: { type: 'string' }, data: { type: 'string' },
                thread: { type: 'string' }, persists: { type: 'string' },
              },
              required: ['where', 'does', 'data'],
            },
          },
        },
        required: ['name', 'steps'],
      },
    },
    module_dependencies: { type: 'array', items: { type: 'object', properties: { module: { type: 'string' }, imports: { type: 'array', items: { type: 'string' } }, notes: { type: 'string' } }, required: ['module', 'imports'] } },
    persisted_state: FIND_SCHEMA.properties.persisted_state,
    findings: { type: 'array', items: FINDING },
  },
  required: ['flows', 'module_dependencies', 'persisted_state', 'findings'],
}

const findPrompt = a => `${RULES}
YOUR AREA (${a.key}): ${a.focus}
Read the files in full (use offset/limit for large files; do not skim only the top). Follow calls into other modules as needed to confirm behaviour. Return your structured result.`

const verifyPrompt = (a, r) => `${RULES}
You are an ADVERSARIAL VERIFIER for area "${a.key}". Another reader produced the findings below. For EACH finding, open the cited code yourself and try to REFUTE it: is the code really like that, is the failure scenario actually reachable through a real caller, is a guard elsewhere already preventing it, is the doc claim really contradicted? Give verdict confirmed / partly (real but mis-described: give corrected_description and corrected_locations) / refuted, and set the severity you believe is right. Default to refuted if you cannot find the cited behaviour in code.
Then, with the area focus in mind (${a.focus}), add any clearly real findings the reader missed (additional_findings, same rigour, cite code). If the persisted_state table has errors, describe them in persisted_state_corrections.
FINDINGS TO VERIFY:
${JSON.stringify(r.findings, null, 1)}
PERSISTED STATE CLAIMED:
${JSON.stringify(r.persisted_state, null, 1)}`

function merge(a, r, v) {
  if (!r) return null
  const byId = {}
  for (const x of (v && v.verdicts) || []) byId[x.id] = x
  const kept = []
  const refuted = []
  for (const f of r.findings) {
    const x = byId[f.id]
    if (x && x.verdict === 'refuted') { refuted.push({ ...f, verify: x }); continue }
    kept.push({
      ...f,
      severity: x ? x.severity : f.severity,
      description: x && x.corrected_description ? x.corrected_description : f.description,
      locations: x && x.corrected_locations && x.corrected_locations.length ? x.corrected_locations : f.locations,
      verdict: x ? x.verdict : 'unverified',
      verify_reasoning: x ? x.reasoning : '',
    })
  }
  for (const f of (v && v.additional_findings) || []) kept.push({ ...f, verdict: 'found-by-verifier' })
  return {
    area: a.key,
    area_summary: r.area_summary,
    findings: kept,
    refuted: refuted.map(f => ({ id: f.id, title: f.title, why: f.verify.reasoning })),
    persisted_state: r.persisted_state,
    persisted_state_corrections: v ? v.persisted_state_corrections || '' : '',
    user_actions: r.user_actions,
    dataflow_notes: r.dataflow_notes,
  }
}

const RAW = `${REPO}/audit/second/raw`
const CACHED = args.cachedFinds || []
// For a restart: what is already on disk in audit/second/raw is not run again.
const DONE_VERIFIES = args.doneVerifies || []
const GAP_LIST = args.gapList || null
const DONE_GAP_FINDS = args.doneGapFinds || []
const DONE_GAPS = args.doneGaps || []

const verifyFromFile = a => `${RULES}
You are an ADVERSARIAL VERIFIER for area "${a.key}". Another reader produced findings for this area; they are in the JSON file ${RAW}/find-${a.key}.json under "result" -> "findings" (and its persisted state under "result" -> "persisted_state"). Read that file first. For EACH finding, open the cited code yourself and try to REFUTE it: is the code really like that, is the failure scenario actually reachable through a real caller, is a guard elsewhere already preventing it, is the doc claim really contradicted? Give verdict confirmed / partly (real but mis-described: give corrected_description and corrected_locations) / refuted, and set the severity you believe is right. Default to refuted if you cannot find the cited behaviour in code.
Then, with the area focus in mind (${a.focus}), add any clearly real findings the reader missed (additional_findings, same rigour, cite code). If the persisted_state table has errors, describe them in persisted_state_corrections.`

phase('Find')
const dataflowV = args.dataflowDone ? Promise.resolve(null) : agent(`${RULES}
You are an ADVERSARIAL VERIFIER of a dataflow description of this repository. It is in the JSON file ${RAW}/dataflow.json under "result" (flows, module_dependencies, persisted_state, findings); read it first. Check every step's location, data type/dtype, thread and persistence claim against the code. Return the corrected dataflow in the same structure (fix wrong steps, add missing hops, drop invented ones), plus any findings.`, { label: 'verify:dataflow', phase: 'Verify', schema: DATAFLOW_SCHEMA, effort: 'high' })

const areaResults = await pipeline(
  AREAS,
  a => DONE_VERIFIES.includes(a.key) ? { skip: true } : CACHED.includes(a.key) ? { cached: true } : agent(findPrompt(a), { label: `find:${a.key}`, phase: 'Find', schema: FIND_SCHEMA }),
  (r, a) => r && r.skip ? { area: a.key, find: null, verify: null, fromDisk: true } : r ? agent(r.cached ? verifyFromFile(a) : verifyPrompt(a, r), { label: `verify:${a.key}`, phase: 'Verify', schema: VERIFY_SCHEMA, effort: 'high' })
    .then(v => ({ area: a.key, find: r.cached ? null : r, verify: v })) : null,
)

const done = areaResults.filter(Boolean)
const missingAreas = AREAS.filter((a, i) => !areaResults[i]).map(a => a.key)
if (missingAreas.length) log(`areas with no result: ${missingAreas.join(', ')}`)

phase('Gaps')
const digest = done.filter(r => r.find).map(r => `## ${r.area}\n${r.find.area_summary}\n` + r.find.findings.map(f => `- [${f.severity}] ${f.title} (${f.locations.slice(0, 2).join(', ')})`).join('\n')).join('\n\n')
const GAP_SCHEMA = {
  type: 'object',
  properties: {
    gaps: { type: 'array', items: { type: 'object', properties: { key: { type: 'string' }, focus: { type: 'string' } }, required: ['key', 'focus'] } },
    duplicates: { type: 'array', items: { type: 'string' } },
  },
  required: ['gaps', 'duplicates'],
}
const critic = GAP_LIST ? { gaps: GAP_LIST, duplicates: [] } : await agent(`${RULES}
You are the COMPLETENESS CRITIC. Below is a digest of an audit split by area; the areas ${CACHED.join(', ')} are not in the digest -- read their findings from ${RAW}/find-<area>.json ("result" -> "findings", titles and severities are enough). Using the repository itself (list files with Glob, grep for cross-cutting patterns), identify what is MISSING: files no area read, cross-cutting concerns not examined (e.g. crash recovery and atomic writes across all writers, disk-full handling, Windows paths/encodings, timezones in entry names, concurrency between GUI thread / session worker / FrameWriter / EdgeWatch, exception swallowing (bare except / except Exception: pass), PROTOCOL_REVISION bumps vs actual command changes, the library's exactness end to end, user-error paths no one traced, demo-vs-direct divergences not covered). Return up to 6 gaps, each with a precise focus description naming files and questions. Also list titles that look like duplicates across areas.
AREAS COVERED: ${AREAS.map(a => a.key).join(', ')}${missingAreas.length ? `\nAREAS THAT FAILED (re-cover these as gaps): ${missingAreas.join(', ')}` : ''}
DIGEST:
${digest}`, { label: 'critic', phase: 'Gaps', schema: GAP_SCHEMA, effort: 'high' })

const gaps = critic ? critic.gaps.slice(0, 6) : []
if (critic && critic.gaps.length > 6) log(`critic proposed ${critic.gaps.length} gaps; ran first 6`)
const gapResults = await pipeline(
  gaps,
  g => DONE_GAPS.includes(g.key) ? { skip: true } : DONE_GAP_FINDS.includes(g.key) ? { cached: true } : agent(findPrompt(g) + `\nALREADY FOUND ELSEWHERE (do not repeat):\n${digest.slice(0, 30000)}\n(and the findings in ${CACHED.map(k => `${RAW}/find-${k}.json`).join(', ')})`, { label: `gap:${g.key}`, phase: 'Gaps', schema: FIND_SCHEMA }),
  (r, g) => r && r.skip ? { area: g.key, focus: g.focus, find: null, verify: null, fromDisk: true } : r ? agent(r.cached ? verifyFromFile(g).replace(`find-${g.key}.json`, `gap-${g.key}.json`) : verifyPrompt(g, r), { label: `verify-gap:${g.key}`, phase: 'Gaps', schema: VERIFY_SCHEMA, effort: 'high' }).then(v => ({ area: g.key, focus: g.focus, find: r.cached ? null : r, verify: v })) : null,
)

return {
  areas: done,
  gaps: gapResults.filter(Boolean),
  duplicates: critic ? critic.duplicates : [],
  dataflowVerify: await dataflowV,
  missingAreas,
}
