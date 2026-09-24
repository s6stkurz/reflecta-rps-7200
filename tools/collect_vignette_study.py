#!/usr/bin/env python3
"""Copy the entries a vignette re-analysis needs onto another machine.

    uv run python tools/collect_vignette_study.py --out ~/vignette-transfer

Run this **on the machine that holds the library** -- the Mac. It selects the
tagged study plus, optionally, a spread of ordinary scans, checks each one is
actually re-analysable, and copies only the five files per entry that the
analysis reads. Nothing here touches the scanner.

Why a tool rather than a `cp`: two of those five files fail *silently* when
absent, and both failures look like a successful run.

  * `ccd_mask.bin` missing -> `apply_shading` falls back to
    `np.arange(min(w, pixels_per_line))` (`rps7200/shading.py:236-239`), which
    maps output column j to CCD element j. At 600 dpi that is the leftmost 862
    of 5172 elements -- the far edge of the lamp profile stretched across the
    whole frame. No exception, no warning, a completely wrong field.
  * `shading.npz` missing -> `rebuild` returns the raw decode with
    `shaded: False` (`tools/uniformity.py:144-150`). One unshaded pass
    differenced against shaded ones carries the full ~39% x-falloff into the
    result and reads as an enormous odd-in-x component. The only signal is the
    word `RAW` in a log line.

So this refuses an incomplete entry rather than copying it, and prints a
manifest saying what went and what did not. An entry that cannot be
re-analysed is worse than an absent one: it produces a number.

`scan.tif` is copied even though `analyse` throws its pixels away. It is the
decode gate -- an independent re-implementation has to reproduce it byte for
byte before any conclusion drawn from the raw bytes is worth reading.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.console import use_utf8_stdout

#: What the analysis reads, per entry. Every one is required: the absence of
#: any of them either stops the run or -- worse -- silently changes the answer.
FILES = ("scan.json", "raw.bin.gz", "scan.tif", "shading.npz", "ccd_mask.bin")
REQUIRED = frozenset(FILES)


def _record(entry: Path) -> dict | None:
    try:
        return json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def inspect(entry: Path, *, checksum: bool) -> dict:
    """What is here, and whether it can be believed."""
    record = _record(entry) or {}
    scan = record.get("scan") or {}
    present = {name for name in FILES if (entry / name).exists()}
    problems = [f"missing {name}" for name in sorted(REQUIRED - present)]

    # The stored digest is over the *uncompressed* bytes, so this catches a
    # truncated gzip as well as a corrupted one. It is the only check that
    # would notice a half-finished copy from an earlier attempt.
    if checksum and "raw.bin.gz" in present:
        digest = (record.get("raw") or {}).get("sha256")
        if digest:
            try:
                with gzip.open(entry / "raw.bin.gz", "rb") as fh:
                    actual = hashlib.sha256(fh.read()).hexdigest()
            except OSError as exc:
                problems.append(f"raw.bin.gz unreadable: {exc}")
            else:
                if actual != digest:
                    problems.append("raw.bin.gz does not match its sha256")

    size = sum((entry / name).stat().st_size for name in present)
    return {
        "path": entry,
        "id": entry.name,
        "subject": (record.get("film") or {}).get("subject") or "",
        "dpi": scan.get("resolution_dpi"),
        "channels": scan.get("channels"),
        "tags": record.get("tags") or [],
        "when": record.get("when") or record.get("timestamp") or "",
        "bytes": size,
        "problems": problems,
    }


def select(root: Path, tag: str, extra: int,
           *, checksum: bool) -> tuple[list, list]:
    """The tagged study, plus `extra` others spread across dpi and date.

    The spread matters because of what the study cannot do alone: a sensor
    defect sits at a fixed sensor column across *different* film positions,
    and picture content does not. Entries from other days, other resolutions
    and other film settle that where one study cannot.
    """
    found = [inspect(p.parent, checksum=checksum)
             for p in sorted(root.glob("*/scan.json"))]
    if not found:
        return [], []

    study = [e for e in found if tag in e["tags"]]
    chosen = {e["id"] for e in study}

    # Spread rather than sample: sort the rest into buckets by resolution and
    # take from each in turn, so 12 extras are not 12 copies of the commonest
    # setting. Within a bucket, oldest first, so the pipeline has changed
    # underneath them by varying amounts.
    rest = [e for e in found if e["id"] not in chosen and not e["problems"]]
    buckets: dict[object, list] = {}
    for candidate in sorted(rest, key=lambda e: (str(e["when"]), e["id"])):
        buckets.setdefault(candidate["dpi"], []).append(candidate)

    spread: list = []
    while len(spread) < extra and any(buckets.values()):
        for key in sorted(buckets, key=lambda k: (k is None, str(k))):
            if len(spread) >= extra:
                break
            if buckets[key]:
                spread.append(buckets[key].pop(0))
    return study, spread


def copy(entries: list, out: Path) -> int:
    copied = 0
    for entry in entries:
        target = out / entry["id"]
        target.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            source = entry["path"] / name
            if source.exists():
                shutil.copy2(source, target / name)
        copied += 1
    return copied


def human(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _plain(entry: dict) -> dict:
    return {k: (str(v) if k == "path" else v) for k, v in entry.items()}


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("library"),
                    help="the library to read (default: library/)")
    ap.add_argument("--tag", default="vignette-study",
                    help="which study to collect (default: vignette-study)")
    ap.add_argument("--extra", type=int, default=12,
                    help="how many further entries to include, spread across "
                         "resolutions and dates (default: 12, 0 for none)")
    ap.add_argument("--out", type=Path, required=True,
                    help="where to write the transfer directory")
    ap.add_argument("--no-checksum", action="store_true",
                    help="skip verifying raw.bin.gz against its stored "
                         "digest, which is the slow part")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be copied and stop")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"no library at {args.root}")
        return 1

    study, spread = select(args.root, args.tag, max(0, args.extra),
                           checksum=not args.no_checksum)
    if not study and not spread:
        print(f"no entries found under {args.root}")
        return 1

    broken = [e for e in study if e["problems"]]
    good = [e for e in study if not e["problems"]]

    print(f"library {args.root}  --  tag {args.tag!r}\n")
    print(f"{len(study)} tagged, {len(good)} usable, {len(broken)} refused")
    for entry in study:
        mark = "  " if not entry["problems"] else "!!"
        dpi = f"{entry['dpi']}dpi" if entry["dpi"] else "?dpi"
        print(f" {mark} {entry['id']:<44} {dpi:>8}  {entry['subject']}")
        for problem in entry["problems"]:
            print(f"      refused: {problem}")

    if spread:
        print(f"\n{len(spread)} further entries, spread across resolution "
              f"and date")
        for entry in spread:
            dpi = f"{entry['dpi']}dpi" if entry["dpi"] else "?dpi"
            print(f"    {entry['id']:<44} {dpi:>8}  {entry['subject']}")

    selected = good + spread
    total = sum(e["bytes"] for e in selected)
    print(f"\n{len(selected)} entries, {human(total)}")

    if broken:
        print(f"\n{len(broken)} tagged entries are NOT re-analysable and were")
        print("left behind. Both missing-file cases change the answer without")
        print("raising, so copying them would produce a number rather than an")
        print("error -- see this file's docstring.")

    if args.dry_run:
        print("\ndry run, nothing copied")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    copied = copy(selected, args.out)
    manifest = {
        "tag": args.tag,
        "root": str(args.root),
        "entries": [_plain(e) for e in selected],
        "refused": [_plain(e) for e in broken],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\ncopied {copied} entries to {args.out}")
    print(f"manifest at {args.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
