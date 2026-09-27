"""Render audit/second/raw/final.json into the audit/second/ Markdown files.

Generated: areas/*.md (every finding in full), problems/*.md (from problems.py),
and the tables substituted into the hand-written templates (templates/*.md).
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SP = Path(__file__).parent
sys.path.insert(0, str(SP))
GROUPS, PROBLEMS = [], []

REPO = Path("/home/user/reflecta-rps-7200")
OUT = REPO / "audit/second"
data = json.loads((OUT / "raw/final.json").read_text())["result"]
AREAS = data["areas"] + data["gaps"]
SEV_ORDER = ["critical", "high", "medium", "low", "info"]

AREA_TITLES = {
    "transport-protocol": "USB transport, protocol and command sequence",
    "decode-and-debug-filing": "Decode, direction, shading and debug filing",
    "demo-parity": "Demo scanner vs the real scanner",
    "library": "The library store",
    "session-roll": "ScanSession, FrameWriter and rolls",
    "framing-units": "Framing and transport units",
    "gui-part1": "The window (tools/gui.py, first half)",
    "gui-part2": "The window (tools/gui.py, second half)",
    "outputs": "Outputs: export, TIFF, DNG, preview, mono, bracket, settings",
    "cli-operator-tools": "Operator CLI tools and build",
    "probe-and-analysis-tools": "Probe and analysis tools",
    "docs-readme-claude": "README.md and CLAUDE.md vs the code",
    "docs-plans-todo": "docs/*.md and TODO.md vs the code",
    "tests": "The test suite",
    "changes-since-first-audit": "The fixes themselves (83dbb22..03aacba)",
    "frame-edges-detectors": "Frame-edge detectors (gap pass)",
    "code-identity-and-protocol-revision": "Code identity and PROTOCOL_REVISION (gap pass)",
    "cross-process-and-thread-concurrency": "Cross-process and thread concurrency (gap pass)",
    "resource-exhaustion-crash-recovery-time": "Disk full, crashes, recovery, time (gap pass)",
    "measure-scan-quality-skill": "The measure-scan-quality metrics (gap pass)",
    "uncited-tests-vs-findings": "Tests that pin the defects (gap pass)",
}


def cell(s) -> str:
    s = "" if s is None else str(s)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def anchor(area: str, fid: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", f"{area}-{fid}".lower())


def sev_key(f) -> int:
    return SEV_ORDER.index(f["severity"])



def prose(text: str) -> str:
    """Readers wrote plain text: give headings and paragraphs Markdown's blank lines."""
    out = []
    for line in text.strip().splitlines():
        bare = line.strip()
        if bare and len(bare) < 80 and re.fullmatch(r"[A-Z0-9 ,'()/&?:.-]+", bare) \
                and sum(c.isalpha() for c in bare) > 3:
            out += ["", f"**{bare.rstrip(':').capitalize()}**", ""]
        elif re.match(r"\s*(?:[-*]|\d+[.)])\s", line):
            out.append(line)
        else:
            if out and out[-1] and not re.match(r"\s*(?:[-*]|\d+[.)])\s", out[-1]):
                out.append("")
            out.append(line)
    return "\n".join(out)


# -- lookup -------------------------------------------------------------------
by_id: dict[str, list] = defaultdict(list)
for a in AREAS:
    for f in a["findings"]:
        by_id[f["id"]].append((a["area"], f))


def resolve(key: str):
    if "/" in key:
        area, fid = key.split("/", 1)
        hits = [x for x in by_id[fid] if x[0] == area]
    else:
        hits = by_id[key]
    if len(hits) != 1:
        raise SystemExit(f"cannot resolve {key}: {len(hits)} hits")
    return hits[0]


in_problem: dict[tuple, list] = defaultdict(list)
for p in PROBLEMS:
    for key in p["members"]:
        area, f = resolve(key)
        in_problem[(area, f["id"])].append(p)


def pfile(p) -> str:
    return f"P{p['n']:02d}-{p['slug']}.md"


def link_finding(area, f, prefix="../areas/") -> str:
    return f"[{f['id']}]({prefix}{area}.md#{anchor(area, f['id'])})"


# -- areas/*.md ------------------------------------------------------------------
(OUT / "areas").mkdir(parents=True, exist_ok=True)
for a in AREAS:
    area = a["area"]
    fs = sorted(a["findings"], key=sev_key)
    c = Counter(f["severity"] for f in fs)
    L = [f"# {AREA_TITLES.get(area, area)}", ""]
    L += [f"Area key `{area}`. {len(fs)} findings: "
          + ", ".join(f"{c[s]} {s}" for s in SEV_ORDER if c[s]) + ".", ""]
    L += ["Every finding below was produced by one reader and then re-checked against "
          "the code by a second, adversarial reader. `verdict` is that second reader's: "
          "`confirmed`, `partly` (real, description corrected -- the corrected text is "
          "shown), or `found-by-verifier` (added by the second reader).", ""]
    if all(f.get("verdict") == "unverified" for f in fs):
        L += ["**Not verified.** The adversarial second reading of this area did not "
              "complete; these are one reader's findings.", ""]
    L += ["[Back to the summary](../README.md)", "", "## What this area is", "",
          prose(a["area_summary"]), ""]
    L += ["## Findings at a glance", "", "| ID | Severity | Category | Verdict | Title |",
          "|---|---|---|---|---|"]
    for f in fs:
        ps = in_problem.get((area, f["id"]), [])
        pl = ", ".join(f"[P{p['n']:02d}](../problems/{pfile(p)})" for p in ps) or "--"
        L.append(f"| [{f['id']}](#{anchor(area, f['id'])}) | {f['severity']} | "
                 f"{f['category']} | {f.get('verdict', '')} | {cell(f['title'])} |")
    L += ["", "## Findings in full", ""]
    for f in fs:
        ps = in_problem.get((area, f["id"]), [])
        L += [f'<a id="{anchor(area, f["id"])}"></a>', "",
              f"### {f['id']} -- {f['title']}", "",
              f"**Severity** {f['severity']} · **Category** {f['category']} · "
              f"**Verdict** {f.get('verdict', '?')}"
              + (" · **Problem** " + ", ".join(
                  f"[P{p['n']:02d}](../problems/{pfile(p)})" for p in ps) if ps else ""),
              "",
              "**Where:** " + ", ".join(f"`{x}`" for x in f["locations"]), ""]
        if f.get("doc_reference"):
            L += [f"**Doc claim:** {f['doc_reference'].strip()}", ""]
        L += [f["description"].strip(), "",
              "**Evidence (from the code):**", "", "```text", f["evidence"].strip(), "```", "",
              f"**Failure scenario:** {f['failure_scenario'].strip()}", "",
              f"**Fix:** {f['recommendation'].strip()}", ""]
        if f.get("verify_reasoning"):
            L += [f"<details><summary>Second reader's check</summary>", "",
                  f["verify_reasoning"].strip(), "", "</details>", ""]
    L += ["## What this area persists", "",
          "| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |",
          "|---|---|---|---|---|---|---|"]
    for s in a["persisted_state"]:
        L.append("| " + " | ".join(cell(s.get(k)) for k in (
            "what", "path_pattern", "format", "raw_or_corrected", "written_by",
            "read_by", "exact")) + " |")
    if a.get("persisted_state_corrections"):
        L += ["", "**Second reader's corrections to this table:**", "",
              a["persisted_state_corrections"].strip()]
    ua = a["user_actions"]
    for head, key in (("What the operator can do", "can_do"),
                      ("What the operator should not do", "should_not_do"),
                      ("Mistakes nothing guards against", "unguarded_mistakes")):
        L += ["", f"## {head}", ""] + [f"- {x}" for x in ua.get(key, [])]
    L += ["", "## Dataflow notes", "", a["dataflow_notes"].strip(), ""]
    (OUT / "areas" / f"{area}.md").write_text("\n".join(L))

# -- fragments for the templates ------------------------------------------------------
frag = {}

all_f = [(a["area"], f) for a in AREAS for f in a["findings"]]
sev = Counter(f["severity"] for _, f in all_f)
ver = Counter(f.get("verdict") for _, f in all_f)
cat = Counter(f["category"] for _, f in all_f)
frag["STATS"] = (
    f"{len(all_f)} findings: " + ", ".join(f"{sev[s]} {s}" for s in SEV_ORDER) + ". "
    f"Verdicts: {ver['confirmed']} confirmed, {ver['partly']} partly (re-described), "
    f"{ver['found-by-verifier']} added by the second reader, "
    f"{sum(len(a.get('refuted', [])) for a in AREAS)} refuted and dropped, "
    f"{ver['unverified']} unverified.")
frag["CATEGORIES"] = "\n".join(
    ["| Category | Findings |", "|---|---|"]
    + [f"| {k} | {v} |" for k, v in cat.most_common()])

rows = []
for g, gname in GROUPS:
    rows.append(f"| **{g}** | **{gname}** | | |")
    for p in PROBLEMS:
        if p["group"] == g:
            rows.append(f"| [P{p['n']:02d}](problems/{pfile(p)}) | {cell(p['title'])} | "
                        f"{p['severity']} | {len(p['members'])} |")
_unused = "\n".join(
    ["| # | Problem | Severity | Reports |", "|---|---|---|---|"] + rows)

frag["AREAS"] = "\n".join(
    ["| Area | Findings | critical | high | medium | low | info |", "|---|---|---|---|---|---|---|"]
    + [f"| [{AREA_TITLES.get(a['area'], a['area'])}](areas/{a['area']}.md) | "
       f"{len(a['findings'])} | " + " | ".join(
           str(Counter(f['severity'] for f in a['findings'])[s]) for s in SEV_ORDER) + " |"
       for a in AREAS])

unclustered = [(a, f) for a, f in all_f
               if (a, f["id"]) not in in_problem and f["severity"] in ("critical", "high")]
frag["UNCLUSTERED_HIGH"] = "\n".join(
    [f"- {link_finding(a, f, 'areas/')} ({a}, {f['severity']}) -- {f['title']}"
     for a, f in unclustered]) or "None."

# doc mismatches
def doc_file(ref: str) -> str:
    m = re.search(r"((?:docs/|research/)?[\w./-]+\.(?:md|json|py))", ref or "")
    return m.group(1) if m else "(docstrings and comments)"

docs = [(a, f) for a, f in all_f if f["category"] == "doc-mismatch"]
other_docs = [(a, f) for a, f in all_f
              if f["category"] != "doc-mismatch" and (f.get("doc_reference") or "").strip()]
groups = defaultdict(list)
for a, f in docs:
    groups[doc_file(f.get("doc_reference") or "")].append((a, f))
L = []
for name in sorted(groups, key=lambda k: (-len(groups[k]), k)):
    L += [f"### {name}", "", "| Finding | Severity | What the doc says vs what the code does | Code |",
          "|---|---|---|---|"]
    for a, f in sorted(groups[name], key=lambda x: sev_key(x[1])):
        claim = (f.get("doc_reference") or "").strip()
        L.append(f"| {link_finding(a, f, 'areas/')} | {f['severity']} | **{cell(f['title'])}**"
                 + (f"<br>Doc: {cell(claim)}" if claim else "")
                 + f" | {cell(', '.join(f['locations'][:3]))} |")
    L.append("")
frag["DOC_TABLES"] = "\n".join(L)
frag["DOC_COUNT"] = str(len(docs))
frag["DOC_OTHER"] = "\n".join(
    ["| Finding | Category | Severity | Title | Doc claim |", "|---|---|---|---|---|"]
    + [f"| {link_finding(a, f, 'areas/')} | {f['category']} | {f['severity']} | "
       f"{cell(f['title'])} | {cell(f['doc_reference'])} |"
       for a, f in sorted(other_docs, key=lambda x: sev_key(x[1]))])
frag["DOC_OTHER_COUNT"] = str(len(other_docs))

# user errors
L = []
for a in AREAS:
    ua = a["user_actions"]
    if not any(ua.get(k) for k in ("can_do", "should_not_do", "unguarded_mistakes")):
        continue
    L += [f"### {AREA_TITLES.get(a['area'], a['area'])}", ""]
    for head, key in (("Can do", "can_do"), ("Should not do", "should_not_do"),
                      ("Unguarded mistakes", "unguarded_mistakes")):
        if ua.get(key):
            L += [f"**{head}**", ""] + [f"- {x}" for x in ua[key]] + [""]
frag["USER_APPENDIX"] = "\n".join(L)
ue = [(a, f) for a, f in all_f if f["category"] == "user-error"]
frag["USER_FINDINGS"] = "\n".join(
    ["| Finding | Severity | Title |", "|---|---|---|"]
    + [f"| {link_finding(a, f, 'areas/')} | {f['severity']} | {cell(f['title'])} |"
       for a, f in sorted(ue, key=lambda x: sev_key(x[1]))])

# demo divergences
dd = [(a, f) for a, f in all_f if f["category"] == "demo-divergence"]
frag["DEMO_FINDINGS"] = "\n".join(
    ["| Finding | Severity | Title | Where |", "|---|---|---|---|"]
    + [f"| {link_finding(a, f, 'areas/')} | {f['severity']} | {cell(f['title'])} | "
       f"{cell(', '.join(f['locations'][:2]))} |"
       for a, f in sorted(dd, key=lambda x: sev_key(x[1]))])

# library exactness
le = [(a, f) for a, f in all_f if f["category"] in ("data-integrity", "library-completeness")]
frag["LIB_FINDINGS"] = "\n".join(
    ["| Finding | Severity | Title | Problem |", "|---|---|---|---|"]
    + [f"| {link_finding(a, f, 'areas/')} | {f['severity']} | {cell(f['title'])} | "
       + (", ".join(f"[P{p['n']:02d}](problems/{pfile(p)})"
                    for p in in_problem.get((a, f['id']), [])) or "--") + " |"
       for a, f in sorted(le, key=lambda x: sev_key(x[1]))])
frag["LIB_COUNT"] = str(len(le))

# persisted state: every row from every area, grouped by path
ps_rows = []
for a in AREAS:
    for s in a["persisted_state"]:
        ps_rows.append((a["area"], s))
for s in data["dataflow"]["persisted_state"]:
    ps_rows.append(("dataflow", s))
frag["PS_APPENDIX"] = "\n".join(
    ["| Reader | What | Path | Format | Raw or corrected | Written by | Read by | Exact? |",
     "|---|---|---|---|---|---|---|---|"]
    + ["| " + a + " | " + " | ".join(cell(s.get(k)) for k in (
        "what", "path_pattern", "format", "raw_or_corrected", "written_by", "read_by",
        "exact")) + " |" for a, s in sorted(ps_rows, key=lambda x: x[1]["path_pattern"])])

# dataflow
df = data["dataflow"]
L = []
for fl in df["flows"]:
    L += [f"### {fl['name']}", "", "| # | Where | What happens | Data | Thread | Persists |",
          "|---|---|---|---|---|---|"]
    for i, s in enumerate(fl["steps"], 1):
        L.append(f"| {i} | `{cell(s['where'])}` | {cell(s['does'])} | {cell(s['data'])} | "
                 f"{cell(s.get('thread'))} | {cell(s.get('persists'))} |")
    L.append("")
frag["DF_FLOWS"] = "\n".join(L)
frag["DF_DEPS"] = "\n".join(
    ["| Module | Imports | Notes |", "|---|---|---|"]
    + [f"| `{d['module']}` | {cell(', '.join(d['imports']))} | {cell(d.get('notes'))} |"
       for d in df["module_dependencies"]])
frag["DF_FINDINGS"] = "\n".join(
    [f"- **{f['severity']}** {f['title']} (`{', '.join(f['locations'][:2])}`)"
     for f in sorted(df["findings"], key=sev_key)])

# status of the first audit's problems
status = []
for f in sorted((OUT / "raw").glob("status-*.json")):
    status += json.loads(f.read_text())["result"]["problems"]
firstp = {p.name[:3]: p.name for p in (REPO / "audit/problems").glob("P*.md")}
sc = Counter(p["status"] for p in status)
frag["STATUS_COUNTS"] = ", ".join(f"{sc[k]} {k}" for k in (
    "fixed", "mostly-fixed", "partly-fixed", "open", "regressed", "superseded") if sc[k])
srows = ["| # | Problem (first audit) | Status | First thing still open |", "|---|---|---|---|"]
for p in status:
    left = re.split(r"(?<=\.)\s", p["remaining"].strip())[0]
    left = left if len(left) <= 240 else left[:237] + "..."
    srows.append(f"| [{p['id']}](../problems/{firstp[p['id']]}) | {cell(p['title'])} | "
                 f"**{p['status']}** | {cell(left)} |")
frag["STATUS_TABLE"] = "\n".join(srows)
frag["HIGH_ALL"] = "\n".join(
    ["| Finding | Area | Severity | Verdict | Title |", "|---|---|---|---|---|"]
    + [f"| {link_finding(a, f, 'areas/')} | {a} | {f['severity']} | {f.get('verdict','')} | {cell(f['title'])} |"
       for a, f in sorted(all_f, key=lambda x: sev_key(x[1])) if f["severity"] in ("critical", "high")])
refuted = [(a["area"], r) for a in AREAS for r in a.get("refuted", [])]
frag["REFUTED"] = "\n".join(
    ["| Area | Finding | Title | Why the second reader refuted it |", "|---|---|---|---|"]
    + [f"| {a} | {r['id']} | {cell(r['title'])} | {cell(r['why'])[:400]} |" for a, r in refuted]) if refuted else "None."
frag["REFUTED_COUNT"] = str(len(refuted))

r3 = SP / "round3.md"
frag["ROUND3"] = r3.read_text().strip() if r3.exists() else (
    "Fix round 3 is running on the findings above; see [PROGRESS.md](PROGRESS.md).")

# status.md
L = ["# Status of the first audit's problems at 03aacba", "",
     "[Back to the summary](README.md)", "",
     "Each problem file under `audit/problems/` was re-checked against the code at 03aacba by a "
     "reader told to judge from the code, not from the commit messages. Every sub-issue a "
     "problem file lists was checked, not just its headline, so *mostly fixed* usually "
     "means the harmful path is closed and something small is left.", ""]
for p in status:
    L += [f"## {p['id']} -- {p['title']}", "",
          f"**Status:** {p['status']} · **First audit:** [{p['id']}](../problems/{firstp.get(p['id'], '')})"
          + (f" · **Commits:** {', '.join(p.get('commits') or [])}" if p.get("commits") else ""), "",
          "**What the code does now:**", "", p["evidence"].strip(), "",
          "**What is left:**", "", p["remaining"].strip(), ""]
    if (p.get("new_problems") or "").strip():
        L += ["**New problem the fix introduced:**", "", p["new_problems"].strip(), ""]
(OUT / "status.md").write_text("\n".join(L))

# -- templates -------------------------------------------------------------------------
for t in sorted((SP / "templates2").glob("*.md")):
    text = t.read_text()
    for k, v in frag.items():
        text = text.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise SystemExit(f"{t.name}: unfilled {left}")
    (OUT / t.name).write_text(text)

print(frag["STATS"])
print("unclustered high/critical:", len(unclustered))
for a, f in unclustered:
    print("  ", a, f["id"], f["severity"], f["title"][:100])
