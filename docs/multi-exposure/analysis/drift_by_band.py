#!/usr/bin/env python3
"""How far each stored pass sits from the first, band by band down the frame.

    uv run python docs/multi-exposure/analysis/drift_by_band.py --tag bracket-3600x9
    uv run python docs/multi-exposure/analysis/drift_by_band.py ENTRY ENTRY ...

The measurement that found the confound. Passes are ordered by exposure and
each is registered, unmoved, against the first in horizontal bands. On the
nine-pass 3600 dpi bracket the shift grows along the ladder to 2.4 lines at the
last pass -- but the three-pass run's x4 pass sits 0.1 line from its first, and
the same x4 exposure in the two runs is 2.3 lines apart. The drift follows how
many passes came before, not the exposure. Offline; reads the library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from merge_library import REPO, _exposure, select  # noqa: E402  (puts code/ on the path)
from passes import band_residuals, register_subpixel  # noqa: E402

from rps7200 import library  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*")
    ap.add_argument("--root", default=str(REPO / "library"))
    ap.add_argument("--tag")
    ap.add_argument("--bands", type=int, default=6)
    args = ap.parse_args()
    passes = []
    for path in select(Path(args.root), args.entries, args.tag):
        image, record = library.corrected(path)
        passes.append((_exposure(record), path.name, image[..., :3]))
    passes.sort(key=lambda t: t[0])
    if len(passes) < 2:
        print("need at least two passes", file=sys.stderr)
        return 1
    ref = passes[0][2]
    print(f"reference {passes[0][1]}, x{passes[0][0]:.3f}")
    for exposure, name, image in passes[1:]:
        whole = register_subpixel(ref, image)
        bands = band_residuals(ref, image, bands=args.bands)
        row = "  ".join(f"{s.dy:+5.2f},{s.dx:+5.2f}" if s.ok else "  refused  "
                        for _, s in bands)
        print(f"{name:34} x{exposure:5.3f}  whole {whole.dy:+5.2f},{whole.dx:+5.2f}"
              f"  bands (dy,dx) {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
