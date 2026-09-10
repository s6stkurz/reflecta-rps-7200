"""Measurements that have survived being checked against Stefan's eye.

Each of these was pasted by hand into a throwaway script several times during
the shading and multi-exposure work. They are here so the next measurement
starts from the version that was right rather than from memory.

Everything works on shading-corrected linear samples, `(H, W, C)` uint16.
"""
from __future__ import annotations

import numpy as np

FULL_SCALE = 65535.0


def dark_mask(image: np.ndarray, percentile: float = 10.0) -> np.ndarray:
    """The densest part of a frame, where noise actually matters.

    Take it from the *brightest* pass of a bracket -- it resolves that end best
    -- and then reuse the same mask for every candidate, so they are compared on
    identical pixels rather than each on its own idea of "dark".
    """
    lum = image.astype(np.float64)[..., :3].mean(axis=2)
    return lum < np.percentile(lum, percentile)


def _highpass(plane: np.ndarray, k: int = 5) -> np.ndarray:
    """Remove smooth scene content, keep what varies pixel to pixel."""
    pad = k // 2
    smooth = np.apply_along_axis(
        lambda r: np.convolve(np.pad(r, pad, mode="edge"), np.ones(k) / k, "valid"),
        1, plane,
    )
    return plane - smooth


def relative_noise(image: np.ndarray, mask: np.ndarray, scale: float = 1.0) -> float:
    """High-frequency content as a fraction of signal, averaged over R, G, B.

    Relative so that scans taken at different exposures compare directly; a
    figure in DN would just say which one was brighter.
    """
    a = image.astype(np.float64)[..., :3] / scale
    out = []
    for c in range(3):
        hp = _highpass(a[..., c])
        out.append(float(np.std(hp[mask]) / max(np.mean(a[..., c][mask]), 1e-9)))
    return float(np.mean(out))


def noise_split(a: np.ndarray, b: np.ndarray, mask: np.ndarray,
                channel: int = 1) -> tuple[float, float, float]:
    """Split high-frequency content into random and fixed, from two repeats.

    Two scans at one exposure differ only by what is random per pass. Grain,
    detail and fixed pattern are identical in both and cancel in the difference,
    so this is the only honest way to learn how much of the "noise" any
    multi-pass method could ever remove.

    Returns ``(random_sigma, total_sigma, random_share)`` in DN.
    """
    x = a.astype(np.float64)[..., channel]
    y = b.astype(np.float64)[..., channel]
    random_sigma = float(np.std((x - y)[mask]) / np.sqrt(2))
    total_sigma = float(np.std(_highpass(x)[mask]))
    return random_sigma, total_sigma, random_sigma / max(total_sigma, 1e-9)


def ceiling(random_sigma: float, total_sigma: float, passes: int) -> float:
    """Best fractional improvement `passes` scans can give. Negative is better.

    Averaging divides only the random part; the fixed part is untouched however
    many passes are taken. Compute this *before* booking scanner time -- on a
    slide here it came to -3.5% for nine passes, which is not worth 25 minutes.
    """
    fixed = np.sqrt(max(total_sigma**2 - random_sigma**2, 0.0))
    reached = np.sqrt((random_sigma / np.sqrt(passes)) ** 2 + fixed**2)
    return float(reached / max(total_sigma, 1e-9) - 1.0)


def agreement_z(a: np.ndarray, b: np.ndarray, mask: np.ndarray,
                channel: int = 1, alpha: float = 1.0, beta: float = 4096.0) -> float:
    """Median |z| between two scans, once put on a common scale.

    The scale is fitted from the pixels, never taken from the commanded
    exposure: at a requested x4.000 the fit gave slope 3.828 and intercept 1279.

    Two repeats at one exposure give about 1.03, and that is the baseline any
    pair must reach to be called consistent -- not 1.0, because the noise model
    slightly understates the truth, equally for every comparison.
    """
    from rps7200.bracket import solve_relation

    x = a.astype(np.float64)[..., channel]
    y = b.astype(np.float64)[..., channel]
    slope, intercept = solve_relation(a[..., channel], b[..., channel])
    if not np.isfinite(slope):
        return float("nan")
    scaled = (y - intercept) / slope
    var = (alpha * np.maximum(x, 0) + beta) + (alpha * np.maximum(y, 0) + beta) / slope**2
    z = np.abs(x - scaled) / np.sqrt(np.maximum(var, 1e-12))
    return float(np.median(z[mask]))


def colour_deviation(image: np.ndarray, window: int = 25) -> np.ndarray:
    """Per-column deviation of each channel *from the other channels*, signed.

    A visible line is one channel departing from its neighbours, so the mean
    across channels is subtracted and the sign kept. `np.abs` would hide a
    violet/green pair -- they are opposite deviations -- and per-channel maxima
    cannot express the comparison at all. Both mistakes were made here and both
    hid 5-10% defects.

    Returns ``(3, W)`` in percent.
    """
    pad = window // 2
    dev = []
    for c in range(3):
        col = np.median(image[..., c].astype(np.float64), axis=0)
        smooth = np.convolve(np.pad(col, pad, mode="reflect"),
                             np.ones(window) / window, "valid")
        dev.append(100 * (col - smooth) / np.median(col))
    d = np.stack(dev)
    return d - d.mean(axis=0, keepdims=True)


def fixed_pattern(image: np.ndarray, channel: int, window: int = 25) -> float:
    """How well the top half of a frame predicts the bottom, for one channel.

    A sensor pattern reproduces between the halves; picture content does not.
    Shading correction took red from 0.897 to 0.265 by this measure. It is
    defeated by frames with full-height vertical structure, which correlate for
    reasons that have nothing to do with the sensor -- prefer comparing two
    different film positions when a second frame exists.
    """
    h = image.shape[0]
    pad = window // 2

    def dev(rows):
        col = np.median(image[rows, :, channel].astype(np.float64), axis=0)
        smooth = np.convolve(np.pad(col, pad, mode="reflect"),
                             np.ones(window) / window, "valid")
        return (col - smooth) / np.median(col)

    a, b = dev(slice(0, h // 2)), dev(slice(h // 2, h))
    w = len(a)
    inner = slice(int(0.06 * w), int(0.94 * w))
    return float(np.corrcoef(a[inner], b[inner])[0, 1])
