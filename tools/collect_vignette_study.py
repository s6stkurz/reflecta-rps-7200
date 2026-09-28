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
import hashlib
import json
import shutil
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library
from rps7200.console import use_utf8_stdout

#: The raw bytes, in either form an entry keeps them: gzipped, or plain where
#: the window filed with the device open and has not compacted yet
#: (`library.save(compress=False)`). `library.read_raw` takes either, and so
#: does `uniformity.rebuild`, which reads them through it. A complete plain
#: entry used to be refused as "missing raw.bin.gz".
RAW_NAMES = (library.RAW_FILE, library.RAW_PLAIN)

#: What the analysis reads, per entry, beside the raw bytes. Every one is
#: required: the absence of any of them either stops the run or -- worse --
#: silently changes the answer.
FILES = ("scan.json", "scan.tif", "shading.npz", "ccd_mask.bin")
REQUIRED = frozenset(FILES)

#: What `uniformity.rebuild` needs of `raw.layout` to decode the bytes at all:
#: without it `analyse` stops on the receiving machine.
LAYOUT_KEYS = ("width", "lines", "bytes_per_line", "channels")


def _record(entry: Path) -> dict | None:
    try:
        return json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    # ValueError covers a record that is not UTF-8 as well as one that does
    # not parse: either is an entry to refuse, not a run to end.
    except (OSError, ValueError):
        return None


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _expected(record: dict) -> dict[str, str]:
    """The digest the record carries for each file it names but the raw
    bytes, whose digest is over their uncompressed form."""
    named = dict(record.get("files") or {})
    image = (record.get("image") or {}).get("sha256")
    if image:
        named["scan.tif"] = image
    return named


def inspect(entry: Path, *, checksum: bool) -> dict:
    """What is here, and whether it can be believed."""
    record = _record(entry)
    problems = [] if record is not None else ["scan.json unreadable"]
    record = record or {}
    scan = record.get("scan") or {}
    present = {name for name in FILES if (entry / name).exists()}
    problems += [f"missing {name}" for name in sorted(REQUIRED - present)]
    raws = [name for name in RAW_NAMES if (entry / name).exists()]
    if not raws:
        problems.append(f"missing {' or '.join(RAW_NAMES)}")
    layout = (record.get("raw") or {}).get("layout") or {}
    if record and not all(layout.get(k) for k in LAYOUT_KEYS):
        problems.append("no raw layout: the bytes cannot be decoded")
    # A demo pass is another stored picture moved by a pretend transport: not
    # sensor evidence, and in the spread it breaks the cross-frame test the
    # spread is there for.
    if (record.get("extra") or {}).get("demo"):
        problems.append("a demo entry, not a scan")

    # Every file against the digest its record carries: the reference and the
    # mask are the two whose damage changes the answer silently, and
    # `scan.tif` is the decode gate. Only the raw bytes were checked. Their
    # digest is over the uncompressed bytes, read as every reader reads them,
    # so a truncated gzip is caught as well as a corrupted one -- and caught,
    # not raised: EOFError and zlib.error escaped, and one damaged entry
    # anywhere in the library ended the whole collection.
    if checksum:
        for name, digest in sorted(_expected(record).items()):
            if name in present and _digest(entry / name) != digest:
                problems.append(f"{name} does not match its sha256")
        digest = (record.get("raw") or {}).get("sha256")
        if raws and digest:
            try:
                data = library.read_raw(entry)
            except zlib.error:
                data = None
            if data is None:
                problems.append(f"{raws[0]} unreadable")
            elif hashlib.sha256(data).hexdigest() != digest:
                problems.append(f"{raws[0]} does not match its sha256")

    size = sum((entry / name).stat().st_size for name in [*present, *raws])
    return {
        "path": entry,
        "id": entry.name,
        "subject": (record.get("film") or {}).get("subject") or "",
        "dpi": scan.get("resolution_dpi"),
        "channels": scan.get("channels"),
        "tags": record.get("tags") or [],
        # What `library.save` writes. `when` and `timestamp` were never keys
        # of a record, so this was empty for every entry.
        "when": record.get("created") or "",
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
    """Copy each entry, and read every copy back against its source.

    Nothing checked the transfer: a half-finished copy from an earlier
    attempt, or a flipped block on the way to an external drive, went out as
    exact. Each file is written beside its target and renamed over it once it
    matches; an entry any of whose copies does not is not counted, and says
    why in the manifest.
    """
    copied = 0
    for entry in entries:
        target = out / entry["id"]
        target.mkdir(parents=True, exist_ok=True)
        entry["copied"] = {}
        for name in (*FILES, *RAW_NAMES):
            source = entry["path"] / name
            if not source.exists():
                continue
            part = target / f".{name}.part"
            shutil.copy2(source, part)
            digest = _digest(source)
            if _digest(part) != digest:
                part.unlink(missing_ok=True)
                entry["problems"].append(f"{name}: the copy does not match")
                continue
            library._replace(part, target / name)
            entry["copied"][name] = digest
        if not entry["problems"]:
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
                    help="skip verifying each entry's files against the "
                         "digests its record carries, which is the slow part")
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
    if not study:
        # A mistyped tag copied the spread alone and said it had succeeded;
        # the other machine then found nothing to analyse.
        print(f"no entry under {args.root} is tagged {args.tag!r}")
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
    failed = [e for e in selected if e["problems"]]
    manifest = {
        "tag": args.tag,
        "root": str(args.root),
        # With the digest each copy was read back against.
        "entries": [_plain(e) for e in selected if not e["problems"]],
        "refused": [_plain(e) for e in broken],
        "failed_to_copy": [_plain(e) for e in failed],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\ncopied {copied} entries to {args.out}")
    for entry in failed:
        for problem in entry["problems"]:
            print(f" !! {entry['id']}: {problem}")
    print(f"manifest at {args.out / 'manifest.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
