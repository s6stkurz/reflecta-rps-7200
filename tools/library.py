#!/usr/bin/env python3
"""Inspect the scan library.

    python3 tools/library.py list
    python3 tools/library.py verify
    python3 tools/library.py reconstruct        # re-decode every entry

`reconstruct` is the one worth running after any change to how the scanner's
bytes become pixels: it decodes every stored pass with today's code and says
which entries no longer match what was saved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "verify", "reconstruct", "reindex"])
    ap.add_argument("--root", default="library")
    args = ap.parse_args()
    root = Path(args.root)

    if not root.exists():
        print(f"no library at {root}", file=sys.stderr)
        return 1

    if args.action == "list":
        rows = library.entries(root)
        if not rows:
            print("library is empty")
            return 0
        print(f"{'id':52} {'dpi':>5} {'ch':>3}  film / notes")
        for r in rows:
            scan, film = r.get("scan") or {}, r.get("film") or {}
            raw = "raw" if (r.get("raw") or {}).get("file") else "NO RAW"
            desc = " / ".join(x for x in (film.get("stock"), film.get("notes")) if x)
            print(f"{r.get('id',''):52} {scan.get('resolution_dpi',''):>5} "
                  f"{scan.get('channels',''):>3}  {desc}  [{raw}]")
        print(f"\n{len(rows)} entries")

    elif args.action == "verify":
        problems = library.verify(root)
        for p in problems:
            print(p)
        print(f"\n{len(problems)} problem(s)" if problems else "\nlibrary is intact")
        return 1 if problems else 0

    elif args.action == "reconstruct":
        changed = 0
        for r in library.entries(root):
            path = root / str(r.get("id"))
            _, verdict = library.reconstruct(path)
            mark = " " if verdict.startswith("identical") else "!"
            if mark == "!":
                changed += 1
            print(f"{mark} {path.name}: {verdict}")
        print(f"\n{changed} entr{'y' if changed == 1 else 'ies'} no longer "
              f"decode to what was stored" if changed
              else "\nevery entry still decodes to exactly what was stored")
        return 1 if changed else 0

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
