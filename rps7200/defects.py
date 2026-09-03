"""Finding and repairing the sensor's bad columns.

This is a trilinear CCD -- red, green and blue sit on separate rows of
photosites -- so a bad element produces a defect in one colour only, and every
measurement here is per channel. Pooling them hides real defects: a column
deviating in green alone, in every band of the frame, is a textbook green-line
defect that any test requiring agreement across channels discards.

Nothing here corrects lamp falloff or a vignette. There is no vignette on this
scanner: the ~39% falloff across the frame lives entirely in x, where shading
already takes it to 1.4%, and along y it is 1.1% before any correction at all.
See docs/vignette-plan.md.
"""
from __future__ import annotations

import numpy as np

def _smooth(profile: np.ndarray, window: int) -> np.ndarray:
    """Moving average down axis 0, reflecting at the edges."""
    k = max(3, int(window) | 1)
    pad = k // 2
    out = np.empty_like(profile)
    for c in range(profile.shape[1]):
        padded = np.pad(profile[:, c], pad, mode="reflect")
        out[:, c] = np.convolve(padded, np.ones(k) / k, mode="valid")
    return out


def flat_defect_sigma(flat: np.ndarray, window: int = 25) -> np.ndarray:
    """How far each column of a flat departs from its neighbours, in sigma.

    Scaled by the flat's own noise, estimated robustly from the median absolute
    deviation, so the threshold does not have to be guessed. That matters
    because the defects here reach only 4-12x the noise floor, and a
    hand-picked cutoff is correspondingly unstable: 1.5% flagged 453 columns,
    mostly noise, while 4% flagged 8 and missed real defects.

    Returns one value per column *per channel*: see `column_defect_sigma` for
    why the channels must not be pooled.
    """
    k = max(3, int(window) | 1)
    pad = k // 2
    out = np.zeros((flat.shape[1], flat.shape[2]))
    for c in range(flat.shape[2]):
        col = np.median(flat[..., c].astype(np.float64), axis=0)
        level = np.median(col)
        if level <= 0:
            continue
        smooth = np.convolve(np.pad(col, pad, mode="reflect"),
                             np.ones(k) / k, mode="valid")
        dev = (col - smooth) / level
        noise = 1.4826 * np.median(np.abs(dev - np.median(dev)))
        if noise <= 0:
            continue
        out[:, c] = np.abs(dev) / noise
    return out


def column_defect_sigma(
    image: np.ndarray,
    bands: int = 8,
    window: int = 25,
) -> np.ndarray:
    """Per-column defect strength of a scan, in sigma, one value per channel.

    Two things separate a defective column from the picture itself.

    *Consistency down the frame.* A defective column reads wrong in every row,
    while vertical detail in a photograph does not. So the frame is split into
    horizontal bands, each band's column profile is measured against its
    neighbours, and the **median across bands** is kept. A defect survives that
    median; an edge in the picture, present in only some bands and with varying
    sign, collapses towards zero.

    *Independence between colours.* This is a trilinear CCD -- red, green and
    blue sit on separate rows of photosites -- so a bad element produces a
    defect in one colour only. Pooling the channels therefore hides real
    defects: a column deviating 3.9% in green in all eight bands, and under 2%
    in red and blue, is a textbook green-line defect, yet requiring agreement
    across channels scores it 0.46 and discards it. Each channel is measured
    and thresholded on its own.

    The scale is each channel's own noise floor, from the median absolute
    deviation, because the absolute strength varies with exposure -- the same
    defect measured 4.7% in a flat and 9.6% in a scan.
    """
    h, w, nc = image.shape
    bands = max(1, min(int(bands), h // 4))
    k = max(3, int(window) | 1)
    pad = k // 2

    out = np.zeros((w, nc))
    for c in range(nc):
        devs = []
        for b in range(bands):
            rows = slice(b * h // bands, (b + 1) * h // bands)
            prof = np.median(image[rows, :, c].astype(np.float64), axis=0)
            level = np.median(prof)
            if level <= 0:
                continue
            smooth = np.convolve(np.pad(prof, pad, mode="reflect"),
                                 np.ones(k) / k, mode="valid")
            devs.append((prof - smooth) / level)
        if not devs:
            continue
        dev = np.median(np.stack(devs), axis=0)
        noise = 1.4826 * np.median(np.abs(dev - np.median(dev)))
        if noise <= 0:
            continue
        out[:, c] = np.abs(dev) / noise
    return out


def find_column_defects(
    image: np.ndarray,
    bands: int = 8,
    sigma: float = 4.0,
    window: int = 25,
) -> np.ndarray:
    """Columns of a scan that are defective, as a (width, channels) mask.

    Thin wrapper over `column_defect_sigma`; the threshold is in units of the
    scan's own noise, so it does not have to be retuned per exposure.

    This finds defects a flat misses. A flat locates them at its own exposure
    and their strength varies with exposure, so a flat taken at other settings
    flags only the strongest -- in one scan it caught the 9.6% defect but not
    four others between 3% and 7%.
    """
    return column_defect_sigma(image, bands=bands, window=window) > sigma


def dilate_defects(defects: np.ndarray, by: int = 3) -> np.ndarray:
    """Widen each defect run, along the column axis only.

    Detection tends to flag a defect's core but not its shoulders, and a
    partially covered defect is only partially corrected -- the middle is
    fixed and the edges remain, which still reads as a line. Widening costs
    little, since the correction measures each column's own strength and
    leaves a healthy column essentially untouched.

    Accepts a 1-D mask or a per-channel (width, channels) one; a per-channel
    mask is widened within each channel, never across them.
    """
    if by <= 0 or not defects.any():
        return defects
    out = defects.copy()
    for shift in range(1, by + 1):
        out[shift:] |= defects[:-shift]
        out[:-shift] |= defects[shift:]
    return out


def destripe(
    image: np.ndarray,
    defects: np.ndarray,
    margin: int = 10,
    max_correction: float = 2.0,
    dilate: int = 3,
    max_run: int = 96,
    max_step: float = 1.5,
) -> np.ndarray:
    """Remove known column defects, measuring their strength in this scan.

    A flat locates defects reliably, but their magnitude changes with exposure:
    the same defect measured 4.7% in a flat and 9.6% in a scan taken at a
    different exposure, so importing the strength from the flat only half
    corrects it.

    So take the positions from the flat and the strength from the image. For
    each run of defective columns, the expected column profile is interpolated
    from good columns on either side; the ratio of actual to expected is the
    defect's strength here, and dividing by it removes exactly that. Nothing
    outside a known defect is touched, so real vertical detail elsewhere in the
    picture survives.

    `defects` may be a 1-D mask, applied to every channel, or a per-channel
    (width, channels) one. Prefer the latter: on this trilinear CCD a defect
    usually belongs to one colour, and scaling all three to fix it tints the
    column instead of repairing it.

    **A run spanning a step in level is refused**, `max_step`. A sensor defect
    is a small multiplicative error on a locally smooth profile, so the good
    columns either side of one sit at the same level. The film's own edge does
    not: it is a step from clear aperture to film, it reads identically in every
    row, and it is therefore indistinguishable from a defect by consistency
    alone. Interpolating across it replaces the edge with a straight line.

    Getting this wrong is not symmetric across channels, which is what makes it
    visible. Whether a run happens to reach column 0 -- and so trip the
    frame-edge guard below -- differs per channel, so one channel keeps the real
    edge while the others are flattened, and the frame ends in a coloured
    fringe rather than a soft one.
    """
    if defects.shape[0] != image.shape[1] or not defects.any():
        return image

    if defects.ndim == 1:
        defects = np.repeat(defects[:, None], image.shape[2], axis=1)
    defects = dilate_defects(defects, dilate)
    out = image.astype(np.float64).copy()

    for c in range(image.shape[2]):
        mask = defects[:, c].view(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], mask, [0]))))
        runs = list(zip(edges[::2], edges[1::2]))
        prof = np.median(out[..., c], axis=0)
        for lo, hi in runs:
            if hi - lo > max_run:
                continue  # too wide to be a sensor defect; a straight
                          # interpolation across it would flatten the picture
            left = np.arange(max(0, lo - margin), lo)
            right = np.arange(hi, min(len(prof), hi + margin))
            if left.size < 2 or right.size < 2:
                continue  # a run against the frame edge is the film border,
                          # not a bad column, and has no good data on one side
            lo_level = float(np.median(prof[left]))
            hi_level = float(np.median(prof[right]))
            span = max(lo_level, hi_level) / max(min(lo_level, hi_level), 1e-9)
            if span > max_step:
                continue  # the anchors sit at different levels, so this run
                          # contains a real edge -- the film's own border, or a
                          # hard edge in the picture. Either way a straight
                          # interpolation across it destroys what is there.
            good_x = np.concatenate([left, right])
            expected = np.interp(np.arange(lo, hi), good_x, prof[good_x])
            actual = prof[lo:hi]
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(expected > 0, actual / expected, 1.0)
            ratio = np.clip(np.where(np.isfinite(ratio), ratio, 1.0),
                            1.0 / max_correction, max_correction)
            out[:, lo:hi, c] /= ratio[None, :]

    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


def resample_reference(
    reference: np.ndarray,
    ref_frame: tuple[int, int, int, int],
    width: int,
    frame: tuple[int, int, int, int],
) -> np.ndarray:
    """Map a per-column reference onto a scan's columns by scanner position.

    Columns must be matched by where they sit on the sensor, not by a width
    ratio. The calibration covers one x range and a scan may cover another, at
    any resolution; scaling by width alone drifts progressively across the
    frame, which smears each stripe over its neighbours instead of cancelling
    it -- visibly making stripes broader and softer rather than removing them.
    """
    rx0, _, rx1, _ = ref_frame
    sx0, _, sx1, _ = frame
    have = reference.shape[0]

    # x position of each source sample and each destination column
    src_x = rx0 + (np.arange(have) + 0.5) * (rx1 - rx0) / have
    dst_x = sx0 + (np.arange(width) + 0.5) * (sx1 - sx0) / width

    return np.stack(
        [np.interp(dst_x, src_x, reference[:, c]) for c in range(reference.shape[1])],
        axis=-1,
    )
