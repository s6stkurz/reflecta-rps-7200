#!/usr/bin/env python3
"""Write the three files used to check corrections by eye.

    1_nothing_done.tif        the scan as it came off the scanner
    2_corrected.tif           corrections applied, still a linear negative
    3_corrected_inverted.tif  the corrected one inverted, for viewing

Run after any change to the correction pipeline:

    python3 tools/make_comparison.py [scan.tif] [flat.tif]

Vignetting correction is off by default: measured on a real negative it made
the worst column defect worse (4.47% -> 5.28%), because the flat it comes from
was captured at a very different exposure. Pass --vignette to include it.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from rps7200 import tiff
from rps7200.direct import (
    find_column_defects, destripe, resample_reference, scanner_corrections,
)

Image.MAX_IMAGE_PIXELS = None
FULL = (0, 0, 10343, 6887)


def worst_defect(image: np.ndarray, edge: int = 60) -> float:
    """Largest column deviation, in percent, ignoring the film edge."""
    col = np.median(image[..., 1].astype(np.float64), axis=0)
    k, pad = 25, 12
    smooth = np.convolve(np.pad(col, pad, mode="reflect"), np.ones(k) / k, "valid")
    dev = np.abs(col - smooth) / np.median(col)
    return 100 * dev[edge:-edge].max()


def invert(image: np.ndarray) -> np.ndarray:
    """Plain per-channel inversion, enough to judge by eye. Use NegPy for real work."""
    x = image[..., :3].astype(np.float64)
    out = np.empty_like(x)
    for c in range(3):
        lo, hi = np.percentile(x[..., c], [0.5, 99.5])
        out[..., c] = 65535 - np.clip((x[..., c] - lo) / (hi - lo), 0, 1) * 65535
    return np.clip(out, 0, 65535).astype(np.uint16)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    use_vignette = "--vignette" in sys.argv
    scan_path = args[0] if args else "scans/negatives/state_1800dpi.tif"
    flat_path = args[1] if len(args) > 1 else "scans/flat/flat_clearfilm_3600dpi.tif"

    raw = tiff.read(scan_path)
    flat = tiff.read(flat_path)
    print(f"scan {scan_path}  {raw.shape}")

    vignette, flat_defects = scanner_corrections(flat, tolerance=0.015)
    from_flat = resample_reference(
        flat_defects.astype(float)[:, None], FULL, raw.shape[1], FULL
    )[:, 0] > 0.3
    from_scan = find_column_defects(raw, tolerance=0.02, agreement=0.6)
    defects = from_flat | from_scan
    print(f"defects: {from_flat.sum()} from flat, {from_scan.sum()} from scan, "
          f"{defects.sum()} combined")

    work = raw
    if use_vignette:
        v = resample_reference(vignette, FULL, raw.shape[1], FULL)
        work = np.clip(
            raw.astype(np.float64)[..., :3] / np.where(v[None, :, :3] > 0.05, v[None, :, :3], 1),
            0, 65535,
        ).astype(np.uint16)

    corrected = destripe(work, defects, margin=12, dilate=5)
    resolution = int(round(raw.shape[1] * 7200 / (FULL[2] - FULL[0])))

    tiff.write("1_nothing_done.tif", raw, resolution=resolution)
    tiff.write("2_corrected.tif", corrected, resolution=resolution)
    tiff.write("3_corrected_inverted.tif", invert(corrected), resolution=resolution)

    print(f"worst column defect: {worst_defect(raw):.2f}% -> {worst_defect(corrected):.2f}%")
    for name, img in (("cmp_before", raw), ("cmp_after", corrected)):
        v = (invert(img) / 256).astype(np.uint8)
        Image.fromarray(v).resize((v.shape[1] // 2, v.shape[0] // 2)).save(f"previews/{name}.png")
    print("wrote 1_nothing_done.tif, 2_corrected.tif, 3_corrected_inverted.tif "
          "and previews/cmp_before.png, previews/cmp_after.png")


if __name__ == "__main__":
    main()
