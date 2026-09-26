#!/usr/bin/env python3
"""The side-by-side the study was decided on: one pass, unregistered, registered.

    uv run python docs/multi-exposure/analysis/crops.py --tag bracket-3600x9

Writes three full-size TIFFs -- the middle pass on the reference's scale, the
merge of the passes as stored, and the merge of the registered passes -- and
one PNG of 100% crops, a dark region and a detailed one, on a shared display
stretch. The crops are cut from the TIFFs read back from disk, so what is shown
is what was written. Output goes to `previews/`, which git ignores: pictures of
film do not belong in the repository. Offline; reads the library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
from merge_library import REPO, _exposure, select  # noqa: E402  (puts code/ on the path)

from bracket import merge_bracket, solve_relation  # noqa: E402
from passes import register_passes  # noqa: E402

from rps7200 import library, tiff  # noqa: E402

SIDE = 512


def best_window(score, h, w, margin=64):
    top = (-1.0, 0, 0)
    for y in range(margin, h - margin - SIDE, SIDE // 4):
        for x in range(margin, w - margin - SIDE, SIDE // 4):
            top = max(top, (score(y, x), y, x))
    return top[1], top[2]


def main() -> int:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*")
    ap.add_argument("--root", default=str(REPO / "library"))
    ap.add_argument("--tag")
    ap.add_argument("--out-dir", type=Path, default=REPO / "previews")
    args = ap.parse_args()

    loaded = []
    for path in select(Path(args.root), args.entries, args.tag):
        image, record = library.corrected(path)
        loaded.append((_exposure(record), image[..., :3]))
    loaded.sort(key=lambda t: t[0])
    exposures = [e for e, _ in loaded]
    frames = [f for _, f in loaded]
    aligned, valid, _ = register_passes(frames)
    registered, _ = merge_bracket(aligned, exposures, valid=valid)
    unregistered, _ = merge_bracket(frames, exposures)
    mid = aligned[len(aligned) // 2]
    single = np.empty(mid.shape, dtype=np.float64)
    for c in range(3):
        slope, intercept = solve_relation(aligned[0][..., c], mid[..., c])
        single[..., c] = (mid[..., c] - intercept) / slope
    single = np.rint(np.clip(single, 0, 65535)).astype(np.uint16)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    names = ["merge_1_single_pass.tif", "merge_2_unregistered.tif", "merge_3_registered.tif"]
    for name, img in zip(names, (single, unregistered, registered)):
        tiff.write(str(args.out_dir / name), img)
    shown = [tiff.read(str(args.out_dir / n)).astype(np.float64) for n in names]

    lum = shown[0].mean(axis=2)
    h, w = lum.shape
    dark = lum < np.percentile(lum, 10)
    energy = np.hypot(*np.gradient(lum))
    windows = [best_window(lambda y, x: dark[y:y + SIDE, x:x + SIDE].mean(), h, w),
               best_window(lambda y, x: energy[y:y + SIDE:4, x:x + SIDE:4].mean(), h, w)]
    rows = []
    for y, x in windows:
        crops = [s[y:y + SIDE, x:x + SIDE] for s in shown]
        lo, hi = np.percentile(crops[0], 0.5), np.percentile(crops[0], 99.5)
        view = [np.clip((c - lo) / (hi - lo), 0, 1) ** (1 / 2.2) for c in crops]
        gap = np.ones((SIDE, 8, 3))
        rows.append(np.hstack([view[0], gap, view[1], gap, view[2]]))
        print(f"crop at row {y}, column {x}")
    sheet = np.vstack([rows[0], np.ones((8, rows[0].shape[1], 3)), rows[1]])
    Image.fromarray((sheet * 255).astype(np.uint8)).save(args.out_dir / "merge_crops.png")
    print(f"wrote {', '.join(names)} and merge_crops.png in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
