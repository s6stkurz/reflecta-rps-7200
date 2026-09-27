"""Print the args that restart rps7200-full-audit-2b.js without redoing anything on disk."""
import json
from pathlib import Path
R = Path("/home/user/reflecta-rps-7200/audit/second/raw")
def ok(name):
    p = R / f"{name}.json"
    return p.exists() and json.loads(p.read_text()).get("type") == "result"
AREAS = ["transport-protocol", "decode-and-debug-filing", "demo-parity", "library", "session-roll",
         "framing-units", "gui-part1", "gui-part2", "outputs", "cli-operator-tools",
         "probe-and-analysis-tools", "docs-readme-claude", "docs-plans-todo",
         "changes-since-first-audit", "tests"]
args = {"guiSplit": 4456, "guiEnd": 8916, "head": "03aacba",
        "cachedFinds": [a for a in AREAS if ok(f"find-{a}")],
        "doneVerifies": [a for a in AREAS if ok(f"verify-{a}")],
        "dataflowDone": ok("verify-dataflow")}
if ok("critic"):
    args["gapList"] = json.loads((R / "critic.json").read_text())["result"]["gaps"][:6]
    keys = [g["key"] for g in args["gapList"]]
    args["doneGapFinds"] = [k for k in keys if ok(f"gap-{k}")]
    args["doneGaps"] = [k for k in keys if ok(f"verify-gap-{k}")]
print(json.dumps(args))
