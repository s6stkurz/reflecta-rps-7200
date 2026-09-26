#!/usr/bin/env python3
"""Does moving a pass change its noise? The control every stacking figure needs.

    uv run python docs/multi-exposure/analysis/resampling_control.py ENTRY

A stack of passes shifted by interpolation reads quieter than stacking made it,
because interpolation smooths noise. `passes.shift_image` shifts by a Fourier
phase ramp instead, which should leave noise untouched. This moves a stored pass
away and back and reports the shadow-noise change (measured +0.00%, +0.00%,
+0.01% for 0.25, 0.5 and 2.45 px) and how the round-trip error falls off toward
the frame's edge, which is what `EDGE_MARGIN_PX` is set from. Offline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
from merge_library import MEASURE_MARGIN_PX, REPO, _metrics, select  # noqa: E402  (puts code/ on the path)
from passes import shift_image  # noqa: E402

from rps7200 import library  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entry")
    ap.add_argument("--root", default=str(REPO / "library"))
    args = ap.parse_args()
    m = _metrics()
    (path,) = select(Path(args.root), [args.entry], None)
    image = library.corrected(path)[0][..., :3]
    cut = (slice(MEASURE_MARGIN_PX, -MEASURE_MARGIN_PX),) * 2
    dark = m.dark_mask(image[cut])
    base = m.relative_noise(image[cut], dark)
    for d in (0.25, 0.5, 2.45):
        moved, _ = shift_image(image, d, -d / 2)
        back, _ = shift_image(moved, -d, d / 2)
        change = m.relative_noise(back[cut], dark) / base - 1
        err = np.abs(back.astype(int) - image).max(axis=2)
        falloff = "  ".join(
            f"{k:3} px in: p99.9 {np.percentile(err[k:-k, k:-k], 99.9):5.0f} DN"
            for k in (16, 32, 64))
        print(f"there and back by {d:4} px: shadow noise {100 * change:+.2f}%   {falloff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
