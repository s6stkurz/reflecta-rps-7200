"""Copy every completed workflow agent result into audit/raw/<label>.json.

Prints the labels written this run (one per line); prints nothing if nothing
new appeared. Never touches anything outside audit/raw/.
"""

import json
import re
import sys
from pathlib import Path

JOURNAL = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

labels = {}
written = []
for line in JOURNAL.read_text().splitlines():
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        continue  # a line still being written
    key = e.get("key") or e.get("agentId")
    if e.get("type") == "started":
        labels[key] = e.get("label") or e.get("agentId")
        continue
    if e.get("type") in ("launched", "failed") or key is None:
        continue
    label = e.get("label") or labels.get(key) or str(key)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    path = OUT / f"{slug}.json"
    body = json.dumps(e, indent=1, ensure_ascii=False)
    if not path.exists() or path.read_text() != body:
        path.write_text(body)
        written.append(slug)

print("\n".join(written))
