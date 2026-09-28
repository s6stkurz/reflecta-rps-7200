"""Measurements that have survived being checked against Stefan's eye.

Each of these was pasted by hand into a throwaway script several times during
the shading and multi-exposure work. They are here so the next measurement
starts from the version that was right rather than from memory.

Everything works on shading-corrected linear samples, `(H, W, C)` uint16.
The shape and the depth are checked (:func:`_check`); whether the samples were
corrected cannot be, so say which they were wherever a number is quoted.
"""
from __future__ import annotations

import numpy as np

FULL_SCALE = 65535.0


def _check(image: np.ndarray, name: str = "image") -> None:
    """Refuse what these metrics cannot mean anything on.

    ``(H, W, C)`` with at least R, G and B: a single plane used to fail deep
    inside with an AxisError. And nothing narrower than 16 bits: the noise
    floor, the clip gate and the noise model below are all in 16-bit counts,
    so on an 8-bit prescan every sample sits under the floor and every
    rail-clipped one counts as usable. Floats are allowed -- an average or a
    merge of 16-bit passes is on the same scale.
    """
    a = np.asarray(image)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError(f"{name} must be (H, W, C) with at least R, G, B; "
                         f"got shape {a.shape}")
    if np.issubdtype(a.dtype, np.integer) and a.dtype.itemsize < 2:
        raise ValueError(f"{name} is {a.dtype}: these metrics are in 16-bit "
                         f"counts, and an 8-bit pass means nothing to them")


def _odd(window: int) -> int:
    """An odd box width, as `rps7200.defects` forces: an even one made the
    'valid' convolution one sample longer than the profile, and it raised."""
    return max(3, int(window) | 1)


def dark_mask(image: np.ndarray, percentile: float = 10.0) -> np.ndarray:
    """The densest part of a frame, where noise actually matters.

    Take it from the *brightest* pass of a bracket -- it resolves that end best
    -- and then reuse the same mask for every candidate, so they are compared on
    identical pixels rather than each on its own idea of "dark".
    """
    _check(image)
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
    _check(image)
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

    **Both terms go through the same high-pass**, so the random part is a
    share of the total by construction. It used to be taken from the plain
    difference against a high-passed total, and the filter removes 1/k of
    white noise's variance: a registered, gain-matched pair of pure random
    noise read a share of 1/sqrt(1 - 1/5) = 1.118, which :func:`ceiling`
    rightly cannot answer and so refused -- in exactly the case where
    averaging helps most. It also counted what the filter removes from the
    total -- noise that is smooth along a row, a whole line brighter in one
    pass -- as random, which the total then never contained.

    The shares the skill quotes (21% at 300 dpi, 27% at 1800, the -3.5%
    ceiling) were measured with that earlier estimator, which read white
    noise 1.118x high. Re-measure before holding a new pair against them.

    **The pair is gain-matched here**, ``b`` onto ``a`` by the relation fitted
    from their pixels (`rps7200.bracket.solve_relation`): two passes a few
    percent apart in exposure put that few percent of every edge and grain
    into the difference, and it read as random. **Registration is still the
    caller's**: align the pair first (`rps7200.uniformity.register` and
    `align`), because two passes of one frame here have been seen 16 columns
    apart, and a shift puts every edge in the difference however well the
    gain is matched.
    """
    from rps7200.bracket import solve_relation

    _check(a, "a")
    _check(b, "b")
    x = a.astype(np.float64)[..., channel]
    y = b.astype(np.float64)[..., channel]
    slope, intercept = solve_relation(x, y)
    if np.isfinite(slope):
        y = (y - intercept) / slope
    random_sigma = float(np.std(_highpass(x - y)[mask]) / np.sqrt(2))
    total_sigma = float(np.std(_highpass(x)[mask]))
    return random_sigma, total_sigma, random_sigma / max(total_sigma, 1e-9)


def ceiling(random_sigma: float, total_sigma: float, passes: int) -> float:
    """Best fractional improvement `passes` scans can give. Negative is better.

    Averaging divides only the random part; the fixed part is untouched however
    many passes are taken. Compute this *before* booking scanner time -- on a
    slide here it came to -3.5% for nine passes, which is not worth 25 minutes
    (measured with the earlier, unfiltered estimator; re-measure before
    comparing a new pair with it).

    A random part as large as the total is refused rather than answered.
    :func:`noise_split` reads both through one filter, so on a registered,
    gain-matched pair the random part is a share of the total and can reach
    it only by sampling error, where the fixed part is too small for the mask
    to resolve. Past that it is what an unregistered or unmatched pair
    measures: grain and detail leak into the difference. Clamping the fixed
    part to zero there turned the worst measurement into the most optimistic
    ceiling -- a share of 1.12 came out at -63% for nine passes, against the
    -3.5% above.
    """
    if random_sigma >= total_sigma:
        raise ValueError(
            f"random {random_sigma:.1f} DN is not less than the total "
            f"{total_sigma:.1f} DN: no split to take a ceiling from. Register "
            f"the pair and match its gain before trusting noise_split; if it "
            f"is both, the fixed part is below what this mask resolves -- "
            f"take a larger one.")
    fixed = np.sqrt(total_sigma**2 - random_sigma**2)
    reached = np.sqrt((random_sigma / np.sqrt(passes)) ** 2 + fixed**2)
    return float(reached / max(total_sigma, 1e-9) - 1.0)


def agreement_z(a: np.ndarray, b: np.ndarray, mask: np.ndarray,
                channel: int = 1, alpha: float | None = None,
                beta: float | None = None,
                sensor_a: np.ndarray | None = None,
                sensor_b: np.ndarray | None = None) -> float:
    """Median |z| between two scans, once put on a common scale.

    The scale is fitted from the pixels, never taken from the commanded
    exposure: at a requested x4.000 the fit gave slope 3.828 and intercept 1279.

    Two repeats at one exposure give about 1.03, and that is the baseline any
    pair must reach to be called consistent -- not 1.0, because the noise model
    slightly understates the truth, equally for every comparison.

    ``alpha`` and ``beta`` default to `rps7200.bracket`'s constants, taken from
    there rather than typed again here: a second copy is a second home, and the
    two drift.

    ``sensor_a`` and ``sensor_b`` are the uncorrected pixels behind corrected
    ``a`` and ``b`` (an entry's `scan.tif`), for the reason
    `rps7200.bracket` judges its merge on them: a railed sample in a column
    whose gain is below one comes back under the rail. The fit uses them, and
    the median is taken only where neither pass is at the rail. It was taken
    over every masked pixel, clipped ones included -- which disagree by
    construction and pulled the median up.
    """
    from rps7200.bracket import (
        CLIP_START,
        DEFAULT_ALPHA,
        DEFAULT_BETA,
        solve_relation,
    )

    _check(a, "a")
    _check(b, "b")
    alpha = DEFAULT_ALPHA if alpha is None else alpha
    beta = DEFAULT_BETA if beta is None else beta
    x = a.astype(np.float64)[..., channel]
    y = b.astype(np.float64)[..., channel]
    sa = None if sensor_a is None else np.asarray(sensor_a)[..., channel]
    sb = None if sensor_b is None else np.asarray(sensor_b)[..., channel]
    slope, intercept = solve_relation(a[..., channel], b[..., channel],
                                      ref_sensor=sa, other_sensor=sb)
    if not np.isfinite(slope):
        return float("nan")
    scaled = (y - intercept) / slope
    var = (alpha * np.maximum(x, 0) + beta) + (alpha * np.maximum(y, 0) + beta) / slope**2
    z = np.abs(x - scaled) / np.sqrt(np.maximum(var, 1e-12))
    unrailed = mask & (x < CLIP_START) & (y < CLIP_START)
    for sensor in (sa, sb):
        if sensor is not None:
            unrailed &= sensor < CLIP_START
    if not unrailed.any():
        return float("nan")
    return float(np.median(z[unrailed]))


def colour_deviation(image: np.ndarray, window: int = 25) -> np.ndarray:
    """Per-column deviation of each channel *from the other channels*, signed.

    A visible line is one channel departing from its neighbours, so the mean
    across channels is subtracted and the sign kept. `np.abs` would hide a
    violet/green pair -- they are opposite deviations -- and per-channel maxima
    cannot express the comparison at all. Both mistakes were made here and both
    hid 5-10% defects.

    Returns ``(3, W)`` in percent.
    """
    _check(image)
    window = _odd(window)
    pad = window // 2
    dev = []
    for c in range(3):
        col = np.median(image[..., c].astype(np.float64), axis=0)
        smooth = np.convolve(np.pad(col, pad, mode="reflect"),
                             np.ones(window) / window, "valid")
        dev.append(100 * (col - smooth) / np.median(col))
    d = np.stack(dev)
    return d - d.mean(axis=0, keepdims=True)


def persistent_deviation(a: np.ndarray, b: np.ndarray,
                         window: int = 25) -> np.ndarray:
    """What `colour_deviation` finds in both of two different frames, signed.

    The cross-frame test the skill names as the one that settles sensor
    against picture: a sensor defect sits at a fixed sensor column across
    different film positions, and picture content does not. Kept where the
    two frames deviate the same way, as the smaller of the two; zero where
    they disagree in sign -- four columns once turned up in both frames with
    opposite tints, which is chance and not a defect.

    ``a`` and ``b`` must be passes over the same window at the same
    resolution. `colour_deviation` is indexed by output column, which is a
    sensor column only then; across windows or resolutions the same index
    is a different place on the sensor.

    Returns ``(3, W)`` in percent.
    """
    _check(a, "a")
    _check(b, "b")
    if a.shape[1] != b.shape[1]:
        raise ValueError(f"{a.shape[1]} columns against {b.shape[1]}: the "
                         "same window at the same resolution, or a column "
                         "is not the same sensor column")
    da = colour_deviation(a, window)
    db = colour_deviation(b, window)
    same = np.sign(da) == np.sign(db)
    return np.where(same, np.sign(da) * np.minimum(np.abs(da), np.abs(db)), 0.0)


def fixed_pattern(image: np.ndarray, channel: int, window: int = 25) -> float:
    """How well the top half of a frame predicts the bottom, for one channel.

    A sensor pattern reproduces between the halves; picture content does not.
    Shading correction took red from 0.897 to 0.265 by this measure. It is
    defeated by frames with full-height vertical structure, which correlate for
    reasons that have nothing to do with the sensor -- prefer comparing two
    different film positions when a second frame exists.
    """
    _check(image)
    window = _odd(window)
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
