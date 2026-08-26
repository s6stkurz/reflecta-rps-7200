#!/usr/bin/env python3
"""Inspect the scan library.

    python3 tools/library.py list
    python3 tools/library.py verify
    python3 tools/library.py reconstruct        # re-decode every entry
    python3 tools/library.py duplicates         # what is redundant, and why
    python3 tools/library.py duplicates --delete

`reconstruct` is the one worth running after any change to how the scanner's
bytes become pixels: it decodes every stored pass with today's code and says
which entries no longer match what was saved.

`duplicates` finds entries that are the same scan of the same picture at the
same protocol revision -- the scanner was driven identically, so one of them
holds nothing the other does not. It only reports; `--delete` is what removes
them, and `--keep N` leaves more than one of each behind.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["list", "verify", "reconstruct", "reindex", "duplicates"])
    ap.add_argument("--root", default="library")
    ap.add_argument("--delete", action="store_true",
                    help="duplicates: actually remove them (default is a dry run)")
    ap.add_argument("--keep", type=int, default=1, metavar="N",
                    help="duplicates: how many of each group to keep (default 1). "
                         "Use 2 to retain a pair for pass-to-pass comparisons")
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

    elif args.action == "duplicates":
        doomed = library.prunable(root, keep=args.keep)
        if not doomed:
            print(f"no duplicates (keeping {args.keep} of each group)")
            return 0

        freed = 0
        for record, reason in doomed:
            path = root / str(record.get("id"))
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            freed += size
            print(f"{'removing' if args.delete else 'redundant'}: {path.name}"
                  f"  ({size / 1e6:.1f} MB)\n    {reason}")
            if args.delete:
                shutil.rmtree(path)

        print(f"\n{len(doomed)} entr{'y' if len(doomed) == 1 else 'ies'}, "
              f"{freed / 1e6:.1f} MB"
              + (" removed" if args.delete else " would be freed -- pass --delete"))
        if args.delete:
            library.reindex(root)

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
