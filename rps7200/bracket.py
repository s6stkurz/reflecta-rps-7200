"""Merge a bracket of exposures into one frame, by inverse-variance weighting.

A single exposure cannot hold a dense negative's range: the densest parts sit in
sensor noise, and the longer exposure that reaches them blows the thin areas.
Scanning the frame several times at different exposures and fusing the results
recovers both ends.

The fusion is **inverse-variance weighting**. Each pass is scaled into the
reference's units and contributes in proportion to ``confidence / variance``, so
a pass carries a pixel exactly as far as it is trustworthy there: a clipped
highlight contributes nothing, a noisy shadow contributes little, and a
well-exposed mid-tone dominates. Variance comes from a Poisson-Gaussian model,
``var ~ alpha * signal + beta`` -- shot noise proportional to signal, read noise
constant -- whose two constants are measured from our own flats by
:func:`fit_noise_params` rather than assumed.

Adapted from pyopticfilm's `exposure_merge.py`, specifically its
`feat/me-n-brackets` branch, which generalises the pairwise merge to N:

    https://github.com/jboneng/pyopticfilm

pyopticfilm is GPL-3.0-or-later, as is this project. The structure here is
theirs; the noise constants and thresholds are ours, because theirs were
measured on a different sensor.

Everything works on **shading-corrected linear samples**. Correct each pass with
its own reference and CCD mask first (`rps7200.shading`), because the passes
carry different masks and an uncorrected bracket fuses the sensor's column
pattern along with the picture.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FULL_SCALE = 65535.0

#: Below this fraction of full scale a sample is noise; confidence ramps up from
#: zero to one across it.
SNR_FLOOR = 0.002 * FULL_SCALE
#: Confidence falls from one to zero across this range. It starts well below the
#: 16-bit rail because a CCD goes non-linear before it saturates, and letting
#: that knee into the weighting biases every pixel near a highlight.
CLIP_START = 0.80 * FULL_SCALE
CLIP_END = 0.95 * FULL_SCALE

#: Residual z-scores: fully trusted to `Z_LO`, distrusted entirely past `Z_HI`.
Z_LO = 3.0
Z_HI = 5.0

#: Fallback Poisson-Gaussian constants, used only when no flats are available to
#: fit. `beta` is read-noise variance in DN^2. Measure instead: see
#: :func:`fit_noise_params`.
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 4096.0

#: Where passes disagree by more than this many sigma AND the fused result shows
#: more than `CHANNEL_SPREAD_TAU` of cross-channel spread, the pixel is taken
#: from the reference alone. Per-channel weighting across a misregistered edge
#: is what turns a shift into a coloured fringe.
#:
#: Measured in **sigma of the expected noise**, not in DN. pyopticfilm uses an
#: absolute 300 DN, which does not survive being moved to another scanner:
#: across a two-stop bracket here the reference is the darkest and so the
#: noisiest pass, and its own noise puts the median disagreement at 970 DN. An
#: absolute threshold then fires on 60% of the frame, each of those pixels falls
#: back to the single noisiest pass, and the merge degenerates into it. In sigma
#: the threshold means the same thing at every exposure.
MISALIGN_SIGMA = 8.0
CHANNEL_SPREAD_TAU = 150.0

#: Rows per band. A 3600 dpi bracket would otherwise need several full-frame
#: float32 planes at once.
CHUNK_ROWS = 128
#: Frames are strided down to about this on a side for the global statistics,
#: which only read a distribution.
STATS_MAX_SIDE = 1024


@dataclass(frozen=True)
class MergeStats:
    """What the merge did, for verifying that it did anything."""

    passes: int
    exposure_ratio: float
    mean_weight_first: float
    mean_weight_last: float
    mean_confidence: float
    zero_confidence_pixels: int
    total_pixels: int
    reference_fallback_pixels: int

    @property
    def zero_confidence_fraction(self) -> float:
        return self.zero_confidence_pixels / self.total_pixels if self.total_pixels else 0.0

    @property
    def reference_fallback_fraction(self) -> float:
        """Pixels taken from the reference alone rather than the blend.

        Not only misregistration: where a longer pass is clipped it disagrees
        with the reference legitimately, and falls back here too. On a synthetic
        bracket with perfect registration this still reads ~10%, all of it
        clipping, so a high number is not by itself evidence of a shift.
        """
        return self.reference_fallback_pixels / self.total_pixels if self.total_pixels else 0.0

    def describe(self) -> str:
        return (
            f"{self.passes} passes spanning x{self.exposure_ratio:.2f} "
            f"({np.log2(max(self.exposure_ratio, 1e-9)):.2f} stops); "
            f"mean confidence {self.mean_confidence:.3f}; "
            f"{100 * self.zero_confidence_fraction:.3f}% of pixels had no usable "
            f"pass; {100 * self.reference_fallback_fraction:.3f}% took the "
            f"reference alone (clipping or disagreement)"
        )


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def confidence(raw: np.ndarray) -> np.ndarray:
    """How much a raw sample is worth: zero in the noise, zero at saturation.

    A ramp up from the noise floor multiplied by a smooth ramp down into
    saturation. Smooth on purpose -- a hard switch between passes prints as
    banding across a gradient.
    """
    floor_w = np.clip((raw - SNR_FLOOR) / max(SNR_FLOOR, 1e-12), 0.0, 1.0)
    clip_w = 1.0 - _smoothstep((raw - CLIP_START) / max(CLIP_END - CLIP_START, 1e-12))
    return floor_w * clip_w


def _residual_confidence(z: np.ndarray) -> np.ndarray:
    """1 where passes agree within `Z_LO` sigma, 0 past `Z_HI`."""
    az = np.abs(z)
    conf = np.ones_like(az, dtype=np.float32)
    conf[az >= Z_HI] = 0.0
    mid = (az > Z_LO) & (az < Z_HI)
    conf[mid] = (Z_HI - az[mid]) / max(Z_HI - Z_LO, 1e-12)
    return conf


def fit_noise_params(flats: list[np.ndarray], patch: int = 32) -> tuple[float, float]:
    """Fit ``var ~ alpha * mean + beta`` from flat fields.

    Tiles each flat, takes (mean, variance) per tile, and fits a line through
    them robustly. The slope is the shot-noise term and the intercept the read
    noise, both in DN. Falls back to the defaults when there is too little to
    fit -- which is worth knowing about, so the caller should say which it used.

    **Each channel is tiled separately.** The channels of a flat sit at
    different levels -- blue returns several times brighter than red at the same
    exposure -- so a tile spanning all three measures the spread between them,
    not the sensor's noise. Flattening ``(H, W, C)`` to ``(H, W*C)`` does
    exactly that: it interleaves the channels along the width, and a 32-wide
    tile then covers ten pixels of three channels rather than 32 pixels of one.
    The variance comes out inflated by the channel offsets and the fit is
    biased, which reaches every pixel of every merge through the weighting.
    """
    means: list[float] = []
    variances: list[float] = []
    for flat in flats:
        a = np.asarray(flat, dtype=np.float64)
        planes = [a] if a.ndim == 2 else [a[..., c] for c in range(a.shape[2])]
        for plane in planes:
            h, w = plane.shape
            for y in range(0, h - patch + 1, patch):
                for x in range(0, w - patch + 1, patch):
                    tile = plane[y : y + patch, x : x + patch]
                    means.append(float(tile.mean()))
                    variances.append(float(tile.var()))
    if len(means) < 8:
        return DEFAULT_ALPHA, DEFAULT_BETA

    m = np.asarray(means)
    v = np.asarray(variances)
    # Least squares through the median of each mean-decile, so a few tiles
    # containing an edge cannot tilt the fit.
    order = np.argsort(m)
    m, v = m[order], v[order]
    bins = np.array_split(np.arange(m.size), min(10, m.size))
    bm = np.array([np.median(m[b]) for b in bins if b.size])
    bv = np.array([np.median(v[b]) for b in bins if b.size])
    if bm.size < 2 or np.ptp(bm) <= 0:
        return DEFAULT_ALPHA, DEFAULT_BETA
    beta, alpha = np.polynomial.polynomial.polyfit(bm, bv, 1)
    if not np.isfinite(alpha) or not np.isfinite(beta) or alpha <= 0:
        return DEFAULT_ALPHA, DEFAULT_BETA
    return float(alpha), float(max(beta, 1.0))


def solve_relation(ref: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    """Fit ``other = slope * ref + intercept`` on the pixels both resolve.

    The commanded exposure ratio is not the relationship between two passes.
    Measured on a real 2-stop bracket: at a requested x4.000 the slope is 3.828
    and the intercept 1279 DN, and the intercept grows as ``425 * (r - 1)``,
    which rearranges to ``(raw + 425) = r * (raw_0 + 425)`` -- a constant the
    correction has over-subtracted, left behind by ``raw / r`` differently in
    every pass.

    Trusting the commanded ratio instead made the passes disagree by 1.78 sigma
    where the fitted relation gives 1.27, which was enough to fire the
    disagreement guard on 62% of the frame and collapse the merge into its
    single noisiest pass.

    Only pixels well clear of both the noise floor and saturation are fitted,
    since neither end carries a usable relationship.
    """
    a = np.asarray(ref, dtype=np.float64).reshape(-1)
    b = np.asarray(other, dtype=np.float64).reshape(-1)
    usable = (a > SNR_FLOOR) & (a < CLIP_START) & (b > SNR_FLOOR) & (b < CLIP_START)
    if usable.sum() < 64:
        return float("nan"), 0.0
    a, b = a[usable], b[usable]
    if a.size > 200_000:
        step = a.size // 200_000 + 1
        a, b = a[::step], b[::step]
    slope, intercept = np.polyfit(a, b, 1)
    if not np.isfinite(slope) or slope <= 0:
        return float("nan"), 0.0
    return float(slope), float(intercept)


def _subsample(frames: list[np.ndarray]) -> list[np.ndarray]:
    h, w = frames[0].shape[:2]
    sy = max(1, h // STATS_MAX_SIDE)
    sx = max(1, w // STATS_MAX_SIDE)
    return [f[::sy, ::sx] for f in frames]


def _z_medians(
    frames: list[np.ndarray], ratios: list[float], alpha: float, beta: float
) -> list[float]:
    """The systematic part of each pass's disagreement with the reference.

    Subtracted before the residual gate so that a pass which is uniformly a
    little off -- a slightly wrong exposure ratio, say -- is not mistaken for a
    frame full of misregistration.
    """
    subs = _subsample(frames)
    lum_ref = subs[0].astype(np.float32).mean(axis=2)
    v_ref = alpha * np.maximum(lum_ref, 0.0) + beta
    out = []
    for raw, r in zip(subs[1:], ratios[1:]):
        lum_raw = raw.astype(np.float32).mean(axis=2)
        v = (alpha * np.maximum(lum_raw, 0.0) + beta) / (r * r)
        z = (lum_ref - lum_raw / r) / np.sqrt(np.maximum(v_ref + v, 1e-12))
        out.append(float(np.median(z)))
    return out


def merge_bracket(
    frames: list[np.ndarray],
    exposures: list[float],
    *,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
) -> tuple[np.ndarray, MergeStats]:
    """Fuse `frames` into one, weighting each pass by how much it is worth.

    `frames` are ``(H, W, 3)`` uint16 in ascending exposure order and already
    aligned to ``frames[0]``, which is the reference: everything is computed in
    its exposure units, so the result stays on the reference's scale rather than
    drifting to some average of the bracket's.

    Reduces exactly to the pairwise form at two frames.
    """
    if len(frames) != len(exposures):
        raise ValueError(
            f"{len(frames)} frames but {len(exposures)} exposures"
        )
    if len(frames) < 2:
        raise ValueError(f"a bracket needs at least 2 frames, got {len(frames)}")
    if any(e <= 0 for e in exposures):
        raise ValueError(f"exposures must be positive, got {exposures}")

    ref = np.asarray(frames[0])
    if ref.ndim != 3 or ref.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) frames, got {ref.shape}")
    for i, f in enumerate(frames[1:], 1):
        if np.asarray(f).shape != ref.shape:
            raise ValueError(
                f"frame {i} is {np.asarray(f).shape}, frame 0 is {ref.shape}"
            )

    commanded = [float(e) / float(exposures[0]) for e in exposures]
    # Solve each pass against the reference rather than trusting what the
    # scanner was told; see :func:`solve_relation`.
    ratios = [1.0]
    offsets = [0.0]
    for frame, want in zip(frames[1:], commanded[1:]):
        slope, intercept = solve_relation(ref[..., 1], np.asarray(frame)[..., 1])
        if not np.isfinite(slope):
            slope, intercept = want, 0.0
        ratios.append(slope)
        offsets.append(intercept)
    h, w = ref.shape[:2]
    out = np.empty((h, w, 3), dtype=np.uint16)
    medians = _z_medians(
        [np.asarray(f).astype(np.float64) - o for f, o in zip(frames, offsets)],
        ratios, alpha, beta,
    )

    w_first = w_last = conf_sum = 0.0
    n_samples = zero_pixels = fallback_pixels = 0

    for y0 in range(0, h, CHUNK_ROWS):
        y1 = min(h, y0 + CHUNK_ROWS)
        raws, scaled, confs, weights = [], [], [], []
        for frame, r, off in zip(frames, ratios, offsets):
            raw = np.asarray(frame[y0:y1], dtype=np.float32)
            c = confidence(raw)
            raw = raw - np.float32(off)
            # Variance transforms with the square of the scale, so a long pass
            # divided down carries proportionally less variance -- which is the
            # whole reason a longer exposure is worth taking.
            v = (alpha * np.maximum(raw, 0.0) + beta) / (r * r)
            raws.append(raw)
            scaled.append(raw / r)
            confs.append(c)
            weights.append(c / np.maximum(v, 1e-12))

        acc = weights[0] * scaled[0]
        w_sum = weights[0].copy()
        no_confidence = confs[0] <= 1e-6
        for x, c, weight in zip(scaled[1:], confs[1:], weights[1:]):
            acc += weight * x
            w_sum += weight
            no_confidence &= c <= 1e-6
        ivw = acc / np.maximum(w_sum, 1e-12)

        # How far each pass disagrees with the reference, in sigma. The pixel is
        # only as trustworthy as its worst pass: one bracket disagreeing sharply
        # is enough to distrust the blend there.
        lum_ref = scaled[0].mean(axis=2)
        v_ref = alpha * np.maximum(lum_ref, 0.0) + beta
        c_res = np.ones_like(lum_ref, dtype=np.float32)
        worst_z = np.zeros_like(lum_ref, dtype=np.float32)
        for raw, x, c, r, median in zip(
            raws[1:], scaled[1:], confs[1:], ratios[1:], medians
        ):
            lum_raw = raw.mean(axis=2)
            lum_x = x.mean(axis=2)
            v = (alpha * np.maximum(lum_raw, 0.0) + beta) / (r * r)
            z = (lum_ref - lum_x) / np.sqrt(np.maximum(v_ref + v, 1e-12))
            gate = np.minimum(confs[0], c).mean(axis=2)
            c_res = np.minimum(c_res, 1.0 - gate * (1.0 - _residual_confidence(z - median)))
            worst_z = np.maximum(worst_z, np.abs(z - median))

        # Where the passes disagree, fall back on whichever single pass is most
        # trusted at that pixel rather than on a blend of ones that conflict.
        best = np.argmax(np.stack(weights, axis=0), axis=0)
        prefer = np.take_along_axis(
            np.stack(scaled, axis=0), best[np.newaxis, ...], axis=0
        )[0]
        merged = c_res[..., None] * ivw + (1.0 - c_res[..., None]) * prefer

        spread = np.max(ivw, axis=2) - np.min(ivw, axis=2)
        misaligned = (worst_z > MISALIGN_SIGMA) & (spread > CHANNEL_SPREAD_TAU)
        chunk = np.where(misaligned[..., None], scaled[0], merged)
        # Where no pass has any confidence -- every one either in the noise or
        # saturated -- keep the reference's own measurement rather than writing
        # zero. pyopticfilm writes zero here; on a negative that turns a blown
        # highlight into pure black, which is worse than the clipped value it
        # replaces.
        chunk = np.where(no_confidence, scaled[0], chunk)
        out[y0:y1] = np.clip(chunk, 0, FULL_SCALE).astype(np.uint16)

        w_first += float(weights[0].sum())
        w_last += float(weights[-1].sum())
        n_samples += int(weights[0].size)
        zero_pixels += int(np.count_nonzero(np.all(no_confidence, axis=-1)))
        fallback_pixels += int(np.count_nonzero(misaligned))
        conf_sum += float(c_res.sum())

    total = int(h * w)
    return out, MergeStats(
        passes=len(frames),
        exposure_ratio=ratios[-1],
        mean_weight_first=w_first / max(n_samples, 1),
        mean_weight_last=w_last / max(n_samples, 1),
        mean_confidence=conf_sum / max(total, 1),
        zero_confidence_pixels=zero_pixels,
        total_pixels=total,
        reference_fallback_pixels=fallback_pixels,
    )
