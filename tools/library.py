#!/usr/bin/env python3
"""Inspect the scan library.

    uv run python tools/library.py list
    uv run python tools/library.py verify
    uv run python tools/library.py reconstruct        # re-decode every entry
    uv run python tools/library.py duplicates         # what is redundant, and why
    uv run python tools/library.py duplicates --delete

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
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library
from rps7200.console import use_utf8_stdout


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["list", "verify", "reconstruct", "reindex",
                             "duplicates", "migrate-raw"])
    ap.add_argument("--root", default="library")
    ap.add_argument("--delete", action="store_true",
                    help="duplicates: actually remove them (default is a dry run)")
    ap.add_argument("--write", action="store_true",
                    help="migrate-raw: actually rewrite the entries "
                         "(default is a dry run)")
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
        # An entry with no bytes to decode is not a decode regression, and
        # counting it as one turns this check into a metric that cries wolf --
        # the summary read "6 entries no longer decode to what was stored"
        # when all six simply had nothing stored to decode.
        changed = unreadable = 0
        for r in library.entries(root):
            path = root / str(r.get("id"))
            _, verdict = library.reconstruct(path)
            if verdict.startswith("identical"):
                mark = " "
            elif "no raw bytes" in verdict or verdict.startswith("could not"):
                mark, unreadable = "-", unreadable + 1
            else:
                mark, changed = "!", changed + 1
            print(f"{mark} {path.name}: {verdict}")
        print(f"\n{changed} entr{'y' if changed == 1 else 'ies'} no longer "
              f"decode to what was stored" if changed
              else "\nevery entry that can be decoded still decodes to exactly "
                   "what was stored")
        if unreadable:
            print(f"{unreadable} had nothing to decode from -- not a "
                  f"regression, but they cannot be re-corrected either")
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

    elif args.action == "migrate-raw":
        # Entries hold raw pixels and recompute the correction on the way out.
        # Ones filed before that hold corrected pixels, and two kinds of them
        # are wrong in different ways:
        #
        #   labelled     `corrections_applied: ["shading"]` -- self-consistent,
        #                but not raw, so the correction can never be improved
        #                on them and `reconstruct` has to re-apply shading to
        #                compare at all.
        #   mislabelled  `corrections_applied: []` on pixels that are corrected
        #                -- these describe themselves wrongly, and `reconstruct`
        #                calls every one a changed decode.
        #
        # Both are repaired the same way and without guessing: the raw bytes
        # are still there, so scan.tif is rewritten from them. Nothing is
        # inferred and nothing is inverted -- a corrected image is never
        # un-corrected, it is simply replaced by a fresh decode of the bytes it
        # came from, which is what should have been stored.
        import numpy as np

        from rps7200 import tiff

        planned, skipped, failed = [], [], []
        for r in library.entries(root):
            path = root / str(r.get("id"))
            stored_shape = tuple((r.get("image") or {}).get("shape") or ())
            applied = (r.get("image") or {}).get("corrections_applied") or []
            decoded, verdict = library.reconstruct(path)
            if decoded is None:
                skipped.append((path.name, verdict))
                continue
            try:
                stored = tiff.read(str(path / "scan.tif"))
            except (OSError, ValueError) as exc:
                failed.append((path.name, f"cannot read scan.tif: {exc}"))
                continue
            # `reconstruct` hands back the decode already re-corrected when the
            # record says the pixels are corrected, so decode again plainly.
            plain = library.decode_raw(path)
            if plain is None:
                skipped.append((path.name, "no raw bytes to decode"))
                continue
            if plain.shape != stored.shape:
                failed.append((path.name,
                               f"decode is {plain.shape}, stored {stored.shape}"))
                continue
            if np.array_equal(plain, stored) and not applied:
                continue                       # already raw and says so
            planned.append((path, plain, applied, stored_shape))

        for path, _plain, applied, _shape in planned:
            why = ("mislabelled: corrected pixels filed as raw" if not applied
                   else "corrected pixels, and the correction cannot be improved")
            print(f"{'rewriting' if args.write else 'would rewrite'}: "
                  f"{path.name}\n    {why}")

        if args.write:
            for path, plain, _applied, _shape in planned:
                record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
                resolution = ((record.get("scan") or {}).get("resolution_dpi")
                              or None)
                tiff.write(str(path / "scan.tif"), plain, resolution=resolution)
                image = record.setdefault("image", {})
                image["corrections_applied"] = []
                image["shape"] = list(plain.shape)
                image["dtype"] = str(plain.dtype)
                image["sha256"] = library._sha256(path / "scan.tif")
                (path / "scan.json").write_text(
                    json.dumps(record, indent=2, default=str),
                    encoding="utf-8")
            library.reindex(root)

        print(f"\n{len(planned)} entr{'y' if len(planned) == 1 else 'ies'} "
              + ("rewritten to raw pixels" if args.write
                 else "would be rewritten to raw pixels -- pass --write"))
        for name, why in failed:
            print(f"! {name}: {why}")
        if skipped:
            print(f"{len(skipped)} could not be decoded and were left alone")
        return 1 if failed else 0

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
