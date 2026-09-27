"""Build audit/second/raw/final.json from the per-agent results on disk.

The same merge as the audit script's `merge()`: a finding the verifier
refuted is dropped (and listed), a `partly` one takes the corrected text and
locations, and the verifier's additional findings are added.
"""
import json
from pathlib import Path
R = Path("/home/user/reflecta-rps-7200/audit/second/raw")
AREAS = ["transport-protocol", "decode-and-debug-filing", "demo-parity", "library", "session-roll",
         "framing-units", "gui-part1", "gui-part2", "outputs", "cli-operator-tools",
         "probe-and-analysis-tools", "docs-readme-claude", "docs-plans-todo",
         "changes-since-first-audit", "tests"]


def load(name):
    p = R / f"{name}.json"
    if not p.exists():
        return None
    e = json.loads(p.read_text())
    return e.get("result") if e.get("type") == "result" else None


def merge(key, r, v):
    by = {x["id"]: x for x in (v or {}).get("verdicts", [])}
    kept, refuted = [], []
    for f in r["findings"]:
        x = by.get(f["id"])
        if x and x["verdict"] == "refuted":
            refuted.append({"id": f["id"], "title": f["title"], "why": x["reasoning"]})
            continue
        kept.append({**f,
                     "severity": x["severity"] if x else f["severity"],
                     "description": (x or {}).get("corrected_description") or f["description"],
                     "locations": (x or {}).get("corrected_locations") or f["locations"],
                     "verdict": x["verdict"] if x else "unverified",
                     "verify_reasoning": x["reasoning"] if x else ""})
    for f in (v or {}).get("additional_findings", []):
        kept.append({**f, "verdict": "found-by-verifier"})
    return {"area": key, "area_summary": r["area_summary"], "findings": kept, "refuted": refuted,
            "persisted_state": r["persisted_state"],
            "persisted_state_corrections": (v or {}).get("persisted_state_corrections", ""),
            "user_actions": r["user_actions"], "dataflow_notes": r["dataflow_notes"],
            "verified": v is not None}


areas, missing = [], []
for a in AREAS:
    r = load(f"find-{a}")
    if r is None:
        missing.append(a)
        continue
    areas.append(merge(a, r, load(f"verify-{a}")))
critic = load("critic") or {"gaps": [], "duplicates": []}
gaps = []
for g in critic["gaps"][:6]:
    r = load(f"gap-{g['key']}")
    if r is None:
        missing.append("gap:" + g["key"])
        continue
    gaps.append(merge(g["key"], r, load(f"verify-gap-{g['key']}")))
dataflow = load("verify-dataflow") or load("dataflow")
out = {"result": {"areas": areas, "gaps": gaps, "duplicates": critic.get("duplicates", []),
                  "dataflow": dataflow, "dataflow_verified": load("verify-dataflow") is not None,
                  "missingAreas": missing}}
(R / "final.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print("areas", len(areas), "gaps", len(gaps), "missing", missing,
      "unverified", [a["area"] for a in areas + gaps if not a["verified"]])
