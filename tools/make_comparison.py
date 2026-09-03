#!/usr/bin/env python3
"""Write the three files used to check corrections by eye.

    1_nothing_done.tif        the scan as it came off the scanner
    2_corrected.tif           corrections applied, still a linear negative
    3_corrected_inverted.tif  the corrected one inverted, for viewing

Run after any change to the correction pipeline:

    python3 tools/make_comparison.py [scan.tif] [flat.tif]

There is no vignette correction here and none should be added. The ~39% falloff
across the frame is real but lives entirely in x, which shading already takes to
1.4%; along y it is 1.1% before any correction. See docs/vignette-plan.md.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from rps7200 import tiff
from rps7200.direct import (
    destripe,
    find_column_defects,
    flat_defect_sigma,
    resample_reference,
)

Image.MAX_IMAGE_PIXELS = None
FULL = (0, 0, 10343, 6887)
SIGMA = float(__import__("os").environ.get("RPS_SIGMA", 3.0))


def worst_defect(image: np.ndarray, edge: int = 60) -> float:
    """Largest column deviation, in percent, ignoring the film edge.

    Measured over every channel. Looking at green alone understates it badly --
    this is a trilinear CCD and its worst defects sit in one colour, so a
    green-only metric read 9.62% where the true figure was 14.43%.
    """
    worst = 0.0
    k, pad = 25, 12
    for c in range(image.shape[2]):
        col = np.median(image[..., c].astype(np.float64), axis=0)
        smooth = np.convolve(np.pad(col, pad, mode="reflect"), np.ones(k) / k, "valid")
        dev = np.abs(col - smooth) / np.median(col)
        worst = max(worst, 100 * dev[edge:-edge].max())
    return worst


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
    scan_path = args[0] if args else "scans/negatives/state_1800dpi.tif"
    flat_path = args[1] if len(args) > 1 else "scans/flat/flat_clearfilm_3600dpi.tif"

    raw = tiff.read(scan_path)
    flat = tiff.read(flat_path)
    print(f"scan {scan_path}  {raw.shape}")

    # Both detectors are thresholded against their own noise rather than a fixed
    # percentage: a fixed cutoff flagged 453 mostly-noise columns, which dilation
    # then blew up to 81% of the image for no gain. Both are per-channel, because
    # this sensor's defects usually sit in one colour only.
    nc = raw.shape[2]
    flat_defects = (flat_defect_sigma(flat) > 4.0)[:, :nc]
    from_flat = resample_reference(
        flat_defects.astype(float), FULL, raw.shape[1], FULL
    ) > 0.3
    from_scan = find_column_defects(raw, sigma=SIGMA)
    defects = from_flat | from_scan
    print(f"defects: {from_flat.any(1).sum()} columns from flat, "
          f"{from_scan.any(1).sum()} from scan, {defects.any(1).sum()} combined "
          f"({defects.sum()} column-channels)")

    corrected = destripe(raw, defects, margin=12, dilate=5)
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
