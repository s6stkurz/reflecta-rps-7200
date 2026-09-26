#!/usr/bin/env python3
"""What share of shadow noise a repeat pair can remove, raw and registered.

    uv run python docs/multi-exposure/analysis/noise_split.py ENTRY_A ENTRY_B

Two passes at one exposure differ only by what is random per pass -- once they
are registered. Prints the skill's `noise_split` and `ceiling` for the pair as
stored and after registering the second onto the first, and what averaging the
registered pair actually achieves against the single pass. At 3600 dpi the
registered random share was 53-66% (21% at 300 dpi, 27% at 1800); unregistered,
a x4 pair 2.3 lines apart read 334%, because the difference then holds the grain
shifted against itself. Offline; reads the library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from merge_library import MEASURE_MARGIN_PX, REPO, _metrics, select  # noqa: E402  (puts code/ on the path)
from passes import register_passes  # noqa: E402

from rps7200 import library  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs=2)
    ap.add_argument("--root", default=str(REPO / "library"))
    args = ap.parse_args()
    m = _metrics()
    a, b = (library.corrected(p)[0][..., :3].astype(float)
            for p in select(Path(args.root), args.entries, None))
    aligned, _, shifts = register_passes([a, b])
    cut = (slice(MEASURE_MARGIN_PX, -MEASURE_MARGIN_PX),) * 2
    a, b, br = a[cut], b[cut], aligned[1][cut]
    mask = m.dark_mask(a)
    print(f"shift of the second pass: dy {shifts[1].dy:+.2f}  dx {shifts[1].dx:+.2f}")
    for tag, other in (("as stored", b), ("registered", br)):
        rnd, total, share = m.noise_split(a, other, mask)
        print(f"{tag:>10}: random {rnd:6.1f} DN  total {total:6.1f} DN  share "
              f"{100 * share:5.1f}%  ceiling n=2 {100 * m.ceiling(rnd, total, 2):+5.1f}%  "
              f"n=9 {100 * m.ceiling(rnd, total, 9):+5.1f}%")
    single = m.relative_noise(a, mask)
    averaged = m.relative_noise((a + br) / 2, mask)
    print(f"averaging the registered pair: {100 * (averaged / single - 1):+.1f}% "
          f"against the first pass alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
