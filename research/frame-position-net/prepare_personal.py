"""Cache the private pictures at 4x prescan resolution, in linear light.

    uv run python research/frame-position-net/prepare_personal.py
    uv run python research/frame-position-net/prepare_personal.py --source "<path>/library personal"

Reads `library personal/` beside the repository root (or --source), writes
only to research/frame-position-net/local/personal/: the anonymous inventory
and one float16 grid per picture, about 12 MB each. Never inside the source.
Already-cached pictures are skipped, so it can be run again.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fpn import personal  # noqa: E402

ROOT = HERE.parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=ROOT / "library personal")
    ap.add_argument("--out", type=Path, default=HERE / "local" / "personal")
    args = ap.parse_args(argv)
    if not args.source.is_dir():
        print(f"no folder at {args.source}", file=sys.stderr)
        return 1
    pictures = personal.write_cache(args.source, args.out,
                                    progress=lambda s: print(s, end="\r", flush=True))
    borders = personal.measure_borders(args.out)
    dark = sum(max(b.values()) >= 0.5 for b in borders.values())
    rolls = sorted({p.roll for p in pictures})
    distinct = len({p.picture for p in pictures})
    print(f"\n{len(pictures)} files, {distinct} pictures, rolls {', '.join(rolls)} -> {args.out}")
    print(f"{dark} pictures have a side border at least half unexposed (all are used)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
