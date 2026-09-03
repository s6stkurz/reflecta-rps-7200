"""Scanner control over the direct USB transport.

This drives the scanner the way the vendor's own software does, which differs
from SANE's ``pieusb`` backend in one decisive respect.

``pieusb`` leaves "shading analysis" enabled. The scanner then answers
``MUST_CALIBRATE`` when the scan starts, and the backend tries to read an
82752-byte shading block whose geometry it is openly unsure about (its own
comment reads *"although it's 45 lines, ccd_mask_size pixels, 16 bit depth in
all cases"*). This scanner delivers exactly 32768 bytes of that block and then
stops, the read times out after 30 s, and the device drops off the USB bus.

CyberView sets bit ``0x08`` -- skip shading analysis -- in the mode's quality
byte, documented in ``pieusb_scancmd.c``'s own reference dump of CyberView
traffic, and so never performs that read. This module does the same.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import numpy as np

from .shading import ShadingReference, apply_shading, calculate_shading
from .usb_transport import CheckCondition, NoDataYet, Transport, UsbError

# The revision of what this driver *says to the scanner*: the command
# sequence, its order, and the payloads. Two scans of one picture taken at the
# same settings and the same revision are interchangeable -- the scanner was
# driven identically, so neither holds anything the other does not, and the
# library treats them as duplicates.
#
# Bump this whenever the conversation with the device changes: a command added,
# removed or reordered, a payload byte altered, a different frame or mode. Do
# NOT bump it for host-side work -- decoding, shading, metering maths -- since
# those are re-runnable from the raw bytes every entry keeps.
PROTOCOL_REVISION = 1

# SCSI opcodes
SCSI_TEST_UNIT_READY = 0x00
SCSI_REQUEST_SENSE = 0x03
SCSI_READ = 0x08
SCSI_WRITE = 0x0A
SCSI_PARAM = 0x0F
SCSI_COPY = 0x18
SCSI_INQUIRY = 0x12
SCSI_MODE_SELECT = 0x15
SCSI_SCAN = 0x1B
SCSI_SLIDE = 0xD1
SCSI_SET_SCAN_HEAD = 0xD2
SCSI_READ_GAIN_OFFSET = 0xD7
SCSI_WRITE_GAIN_OFFSET = 0xDC
SCSI_READ_STATE = 0xDD
SCSI_VENDOR_E7 = 0xE7   # sent once at session start; purpose unknown

# Sub-commands carried in a WRITE payload
SUB_SCAN_FRAME = 0x12
SUB_EXPOSURE = 0x13
SUB_HIGHLIGHT_SHADOW = 0x14
SUB_CALIBRATION_INFO = 0x15
SUB_CAL_DATA = 0x16
SUB_CMD_17 = 0x17

# Mode: passes
ONE_PASS_COLOR = 0x80
ONE_PASS_RGBI = 0x90

# Mode: colour depth
DEPTH_16 = 0x20
DEPTH_8 = 0x04

# Mode: colour format
FORMAT_PIXEL = 0x01   # R,G,B[,I] interleaved per pixel
FORMAT_LINE = 0x02
FORMAT_INDEX = 0x04

# Mode: quality field. Bytes 9 and 10 of the MODE payload behave as one
# 16-bit little-endian value. Across 33 scan cycles in six captures of the
# vendor software, 32 send 0x0008 and exactly one sends 0x0800 -- so 0x0008
# means "reuse the calibration already held" and 0x0800 means "calibrate now".
# Sending neither (both bytes zero) does nothing at all.
QUALITY_SHARPEN = 0x02
QUALITY_SKIP_SHADING = 0x08     # reuse existing calibration
QUALITY_CALIBRATE = 0x0800      # run a shading calibration pass
QUALITY_FAST_INFRARED = 0x80

BYTE_ORDER_INTEL = 0x01

#: Bytes prefixed to each line in INDEX colour format; the first is the ASCII
#: channel letter.
INDEX_HEADER = 2

#: Channel letters, in the order the scanner tags them.
CHANNEL_ORDER = "RGBI"

#: Read budget per READ command. Captures show the vendor software sizing its
#: batches to about this many bytes -- 208 lines x 2522 at 900 dpi, 104 x 5042
#: at 1800 -- subject to :data:`MAX_BATCH_LINES`.
READ_BUDGET_BYTES = 512 * 1024

#: Upper bound on lines per READ, which the vendor software never exceeds even
#: when the byte budget would allow far more (216 x 430 at 300 dpi).
MAX_BATCH_LINES = 216


def calibration_references(
    data: bytes, bytes_per_line: int
) -> tuple[np.ndarray, np.ndarray]:
    """Split calibration data into dark and light references.

    The pass captures a two-point sensor calibration, not an image: roughly
    half the lines are a dark reference (lamp blocked, giving each element's
    offset and dark current) and half a light reference (lamp unobstructed,
    giving each element's gain). This is why it works on an unknown film strip
    -- it never measures the film.

    Returns ``(dark, light)``, each ``(width, channels)`` in R, G, B, I order.
    """
    lines = len(data) // bytes_per_line
    planes: dict[str, list[np.ndarray]] = {}
    for i in range(lines):
        line = data[i * bytes_per_line : (i + 1) * bytes_per_line]
        planes.setdefault(chr(line[0]), []).append(
            np.frombuffer(line[INDEX_HEADER:], dtype="<u2")
        )

    order = [c for c in CHANNEL_ORDER if c in planes]
    if not order:
        raise ValueError(f"no channel tags in calibration data; saw {sorted(planes)}")

    darks, lights = [], []
    for c in order:
        rows = np.array(planes[c], dtype=np.float64)
        level = rows.mean(axis=1)
        # Split at the midpoint of the range: the two groups are far apart.
        cut = (level.min() + level.max()) / 2.0
        dark_rows, light_rows = rows[level <= cut], rows[level > cut]
        if dark_rows.size == 0 or light_rows.size == 0:
            raise ValueError(
                f"channel {c!r} has no clear dark/light split "
                f"(levels {level.min():.0f}..{level.max():.0f})"
            )
        darks.append(np.median(dark_rows, axis=0))
        lights.append(np.median(light_rows, axis=0))

    return np.stack(darks, axis=-1), np.stack(lights, axis=-1)


def gain_from_calibration(
    data: bytes, bytes_per_line: int, highpass: bool = True, window: int = 65
) -> np.ndarray:
    """Per-column gain from the scanner's own calibration data.

    Uses the dark and light references properly: an element's response is
    ``light - dark``, so the gain is that span normalised. Taking a plain
    median across all the lines instead would average black and white together
    and measure nothing useful.

    Returns ``(width, channels)`` in R, G, B, I order, for
    :func:`apply_flat_field`.
    """
    dark, light = calibration_references(data, bytes_per_line)
    span = light - dark
    span = np.where(span > 1.0, span, 1.0)

    if highpass:
        k = max(3, int(window) | 1)
        pad = k // 2
        reference = np.empty_like(span)
        for c in range(span.shape[1]):
            padded = np.pad(span[:, c], pad, mode="reflect")
            reference[:, c] = np.convolve(padded, np.ones(k) / k, mode="valid")
    else:
        reference = span.mean(axis=0, keepdims=True)

    with np.errstate(divide="ignore", invalid="ignore"):
        gain = span / reference
    return np.where(np.isfinite(gain) & (gain > 0), gain, 1.0)


def _smooth(profile: np.ndarray, window: int) -> np.ndarray:
    """Moving average down axis 0, reflecting at the edges."""
    k = max(3, int(window) | 1)
    pad = k // 2
    out = np.empty_like(profile)
    for c in range(profile.shape[1]):
        padded = np.pad(profile[:, c], pad, mode="reflect")
        out[:, c] = np.convolve(padded, np.ones(k) / k, mode="valid")
    return out


def scanner_corrections(
    flat: np.ndarray, tolerance: float = 0.02, defect_window: int = 25,
    vignette_window: int = 301,
) -> tuple[np.ndarray, np.ndarray]:
    """Derive vignetting profile and bad-column mask from a clear-film scan.

    A scan of clear film is uniformly lit, so everything in it is the scanner.
    Two separate defects live there and need opposite treatment:

    * **Vignetting** -- a smooth ~22% falloff from centre to edge, horizontal
      only (the lamp lights a line across the film; the carriage travels down
      it). Corrected by dividing, since it is multiplicative and smooth.
    * **Bad columns** -- sharp 3-5% outliers where a sensor element misreads.
      Dividing cannot fix these; the column carries no useful signal, so it has
      to be interpolated from its neighbours.

    Returns ``(vignette, bad)``: the smooth profile normalised to its peak, and
    a boolean mask of defective columns.
    """
    profile = np.median(flat.astype(np.float64), axis=0)          # (W, C)

    local = _smooth(profile, defect_window)
    with np.errstate(divide="ignore", invalid="ignore"):
        dev = np.abs(profile - local) / np.where(local > 0, local, 1)
    bad = np.any(dev > tolerance, axis=1)

    # Fit the vignette on good columns only, so defects do not drag it around.
    clean = profile.copy()
    good = np.flatnonzero(~bad)
    if good.size and bad.any():
        idx = np.flatnonzero(bad)
        for c in range(profile.shape[1]):
            clean[idx, c] = np.interp(idx, good, profile[good, c])

    vignette = _smooth(clean, vignette_window)
    peak = vignette.max(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        vignette = vignette / np.where(peak > 0, peak, 1)
    return np.where(np.isfinite(vignette) & (vignette > 0), vignette, 1.0), bad


def correct_scan(
    image: np.ndarray,
    vignette: np.ndarray,
    bad: np.ndarray,
    frame: tuple[int, int, int, int],
    ref_frame: tuple[int, int, int, int],
) -> np.ndarray:
    """Interpolate bad columns, then divide out vignetting.

    Order matters: a bad column would otherwise be scaled rather than replaced,
    and would still be visible. Apply this to linear scan data, before any
    inversion -- an inversion curve is steep where a flat sits and multiplies
    both defects several-fold, after which they cannot be removed cleanly.
    """
    w = image.shape[1]
    v = resample_reference(vignette, ref_frame, w, frame)
    flags = resample_reference(
        bad.astype(float)[:, None], ref_frame, w, frame
    )[:, 0] > 0.3

    out = interpolate_columns(image, flags).astype(np.float64)
    channels = min(image.shape[2], v.shape[1])
    out[..., :channels] /= np.where(v[None, :, :channels] > 0.05,
                                    v[None, :, :channels], 1.0)
    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


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
            good_x = np.concatenate([left, right])
            expected = np.interp(np.arange(lo, hi), good_x, prof[good_x])
            actual = prof[lo:hi]
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(expected > 0, actual / expected, 1.0)
            ratio = np.clip(np.where(np.isfinite(ratio), ratio, 1.0),
                            1.0 / max_correction, max_correction)
            out[:, lo:hi, c] /= ratio[None, :]

    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


def flat_field_gain(
    flat: np.ndarray,
    highpass: bool = True,
    window: int = 65,
    clip_fraction: float = 0.001,
    saturation: int = 64000,
) -> np.ndarray:
    """Per-column gain from a flat field, normalised to a mean of 1.

    Striping is per-CCD-element, so it is constant down each column and shows
    up as high-frequency variation *across* columns. Film base density, lamp
    falloff and vignetting vary smoothly across the sensor instead.

    With ``highpass`` (the default) the profile is divided by a smoothed copy
    of itself, so everything smooth cancels and only the element-to-element
    jitter remains. That matters because the flat has to be captured through
    *some* film: an unexposed colour negative carries a strong orange mask, and
    baking it into the correction would tint every B&W or slide scan it was
    applied to. High-pass gain is independent of film stock, development and
    overall exposure.

    Set ``highpass=False`` for a conventional full flat that also corrects lamp
    falloff and vignetting -- but only apply that to scans of the same stock,
    since it carries the base colour with it.

    The column profile is a median, not a mean. Element gain is identical in
    every row of a column, so it survives either; but dust and short defects
    touch only a few rows and a median discards them outright. What a median
    cannot reject is a scratch running the length of the film, since that marks
    one column in every row and is geometrically indistinguishable from a CCD
    stripe -- so the flat should be captured on unscratched film, or averaged
    over several film positions (:func:`combine_flats`), which moves scratches
    while leaving the sensor pattern fixed.

    Raises if the flat is clipped: saturated columns record no variation, so
    their stripes would go uncorrected.
    """
    if flat.ndim != 3:
        raise ValueError(f"expected (H, W, C), got {flat.shape}")

    clipped = (flat >= saturation).mean()
    if clipped > clip_fraction:
        raise ValueError(
            f"flat field is {clipped:.1%} saturated (limit {clip_fraction:.1%}); "
            "re-capture it with a lower exposure_scale"
        )

    profile = np.median(flat.astype(np.float64), axis=0)     # (W, C)

    if highpass:
        k = max(3, int(window) | 1)                          # odd window
        pad = k // 2
        smooth = np.empty_like(profile)
        for c in range(profile.shape[1]):
            padded = np.pad(profile[:, c], pad, mode="reflect")
            smooth[:, c] = np.convolve(padded, np.ones(k) / k, mode="valid")
        reference = smooth
    else:
        reference = profile.mean(axis=0, keepdims=True)

    with np.errstate(divide="ignore", invalid="ignore"):
        gain = profile / reference
    return np.where(np.isfinite(gain) & (gain > 0), gain, 1.0)


def combine_flats(flats: Sequence[np.ndarray]) -> np.ndarray:
    """Merge several flats taken at different film positions.

    The sensor pattern sits at the same columns in every capture; a scratch in
    the film moves with the film. Taking the median across captures keeps the
    former and rejects the latter.
    """
    stack = [np.asarray(f) for f in flats]
    if not stack:
        raise ValueError("no flats given")
    shapes = {f.shape for f in stack}
    if len(shapes) != 1:
        raise ValueError(f"flats differ in shape: {shapes}")
    return np.median(np.stack(stack), axis=0).astype(stack[0].dtype)


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


def defective_columns(
    light: np.ndarray, tolerance: float = 0.06, window: int = 25
) -> np.ndarray:
    """Find columns that respond abnormally under uniform illumination.

    A CCD element that reads far from its neighbours when the lamp shines
    evenly is genuinely defective, and shows in scans as a sharp vertical line.
    This is different from the gain variation a flat-field corrects, and needs
    interpolation rather than scaling.

    Note the scanner's own CCD mask is *not* a defect map: it flags every 12th
    column exactly, which is structural. Interpolating on it would blur 8% of
    the image for nothing.

    Returns a boolean array, True where the column is defective.
    """
    k = max(3, int(window) | 1)
    pad = k // 2
    bad = np.zeros(light.shape[0], dtype=bool)
    for c in range(light.shape[1]):
        col = light[:, c]
        smooth = np.convolve(np.pad(col, pad, mode="reflect"), np.ones(k) / k, "valid")
        with np.errstate(divide="ignore", invalid="ignore"):
            dev = np.abs(col - smooth) / np.where(smooth > 0, smooth, 1)
        bad |= dev > tolerance
    return bad


def interpolate_columns(image: np.ndarray, bad: np.ndarray) -> np.ndarray:
    """Replace defective columns by interpolating from their good neighbours."""
    if bad.shape[0] != image.shape[1] or not bad.any():
        return image
    good = np.flatnonzero(~bad)
    idx = np.flatnonzero(bad)
    if good.size == 0:
        return image
    out = image.astype(np.float64).copy()
    for c in range(image.shape[2]):
        for row in range(out.shape[0]):
            out[row, idx, c] = np.interp(idx, good, out[row, good, c])
    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


def apply_calibration(
    image: np.ndarray,
    dark: np.ndarray,
    gain: np.ndarray,
    frame: tuple[int, int, int, int],
    ref_frame: tuple[int, int, int, int] | None = None,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """Full two-point correction: ``(raw - dark) / gain``.

    Both halves matter. The dark reference is a per-element offset -- additive,
    so dividing alone cannot remove it. The gain is the element's response
    span. Applying only the gain, as an earlier version did, leaves the
    additive component untouched.
    """
    ref_frame = ref_frame or CALIBRATION_FRAME
    w = image.shape[1]
    d = resample_reference(dark, ref_frame, w, frame)
    g = resample_reference(gain, ref_frame, w, frame)

    channels = min(image.shape[2], d.shape[1], g.shape[1])
    out = image.astype(np.float64).copy()
    out[..., :channels] -= d[None, :, :channels]
    out[..., :channels] /= np.where(g[None, :, :channels] > 0, g[None, :, :channels], 1.0)

    if mask is not None and mask.shape[0] == w:
        bad = np.flatnonzero(mask == 0)
        good = np.flatnonzero(mask != 0)
        if good.size and bad.size:
            for c in range(image.shape[2]):
                for row in range(out.shape[0]):
                    out[row, bad, c] = np.interp(bad, good, out[row, good, c])

    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


def apply_flat_field(
    image: np.ndarray, gain: np.ndarray, mask: np.ndarray | None = None
) -> np.ndarray:
    """Divide out the per-column gain, and interpolate flagged dead columns."""
    if image.shape[1] != gain.shape[0]:
        # Legacy path: no frame information, so interpolate across the width.
        # Prefer apply_calibration(), which matches columns by scanner position.
        frame = (0, 0, gain.shape[0], 1)
        gain = resample_reference(gain, frame, image.shape[1], frame)
    channels = min(image.shape[2], gain.shape[1])
    out = image.astype(np.float64).copy()
    out[..., :channels] /= gain[None, :, :channels]

    if mask is not None and mask.shape[0] == image.shape[1]:
        bad = np.flatnonzero(mask == 0)
        good = np.flatnonzero(mask != 0)
        if good.size and bad.size:
            for c in range(image.shape[2]):
                for row in range(out.shape[0]):
                    out[row, bad, c] = np.interp(bad, good, out[row, good, c])

    return np.clip(out, 0, np.iinfo(image.dtype).max).astype(image.dtype)


def batch_for(bytes_per_line: int) -> int:
    """Lines to request per READ, mirroring the vendor software."""
    if bytes_per_line <= 0:
        return MAX_BATCH_LINES
    fits = -(-READ_BUDGET_BYTES // bytes_per_line)   # round up, as the vendor does
    return max(1, min(MAX_BATCH_LINES, fits))


#: Inset the vendor software keeps clear of the film edge -- every frame it
#: detected began at x=96, y=71. Not applied by default here: trimming loses
#: real image area, so detection returns the full picture it finds. Pass
#: ``inset=True`` to :meth:`DirectScanner.detect_frame` to trim like CyberView.
MIN_INSET_X = 96
MIN_INSET_Y = 71

#: Frame the vendor scans for shading calibration -- the lower part of the
#: transport, not the picture area.
CALIBRATION_FRAME = (0, 3431, 10343, 6888)

#: Full scan extent, 0-based pixels at maximum resolution. Matches the vendor
#: software's frame and this scanner's 10344 x 6888 CCD. Used so that scanning
#: needs no INQUIRY: neither CyberView nor any run that scanned successfully
#: issues one, and doing so from inside the scan flow has broken reads.
FULL_FRAME = (0, 0, 10343, 6887)

#: CCD mask length the vendor software requests (pieusb uses shading_width).
CCD_MASK_SIZE = 5172

#: A whole 35 mm frame, in scanner units at maximum resolution. From the vendor's
#: own detected windows on a strip it had registered correctly -- (96,71) to
#: (10175,6815), so 10079 wide -- against a 10344-unit transport window. A
#: picture measuring much less than this has part of itself outside the
#: aperture, which is the only way a drifted frame is visible: the prescan
#: cannot see what the window does not cover.
NOMINAL_FRAME_WIDTH = 10080

#: Below this, a prescan is clear film rather than a picture, and a roll walking
#: frame by frame has run off the end of the film. Measured as variation *down*
#: the columns, so the lamp's horizontal falloff -- about 22% centre to edge, and
#: present in a blank window too -- does not read as a picture.
BLANK_CONTRAST = 0.02


def frame_contrast(image: np.ndarray) -> float:
    """How much a prescan varies down its columns, relative to its own level.

    The discriminator for "is there a picture in the window at all", which is
    what tells a roll it has reached the end of the film.

    Down the columns, specifically. Vignetting and lamp falloff vary *across*
    the sensor and are constant down it, so a window of blank film still varies
    ~22% column to column while varying almost nothing row to row. Measuring
    across the width would score empty film as a picture.

    Relative to the mean, so it does not move with exposure.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return 0.0
    level = float(grey.mean())
    if level <= 0:
        return 0.0
    return float(np.median(grey.std(axis=0)) / level)


#: Fraction of the clear-aperture level below which a column is film. Measured
#: on a C-41 negative: the clear strip read 143/153/153 in R/G/B and the film
#: 34/15/7, so anything from 0.6 to 0.9 returns the same edges.
FILM_LEVEL = 0.75

#: How much brighter the clear aperture has to be than the median column before
#: there is believed to be one in view at all. Below this the film fills the
#: window, which is the normal case for a well-registered frame.
CLEAR_RATIO = 2.0


def film_bounds(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
    level: float = FILM_LEVEL,
    clear_ratio: float = CLEAR_RATIO,
) -> tuple[int, int, int, int]:
    """Where the film sits in the transport window, by how much light it stops.

    Film attenuates and an empty aperture does not, so the film's edge is a step
    in *level*. :meth:`DirectScanner.detect_frame` looks for a step in variance
    instead, and on a real negative that fails badly: a dark, low-contrast frame
    varies less than the hard border at the film's edge, so a threshold set
    relative to the peak selects the border and discards the picture. Measured
    on this scanner it reduced a perfectly registered frame -- picture filling
    the window edge to edge -- to a 0.26 mm sliver, and reported it as 35 mm of
    drift.

    Level does not have that failure mode, because it does not depend on picture
    content at all. On one 300 dpi prescan of a C-41 negative the clear strip
    read 143/153/153 in R/G/B against the film's 34/15/7, and every threshold
    between 60% and 90% of the clear level returned the same edges.

    With no clear aperture in view -- ``clear_ratio`` -- the film fills the
    window, which is what a well-registered frame looks like, and the whole
    window is returned. An *empty* window reads the same way; use
    :func:`frame_contrast` to tell those apart, as :meth:`DirectScanner.scan_roll`
    does before it calls this.
    """
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    if grey.size == 0:
        return full_frame

    fx0, fy0, fx1, fy1 = full_frame

    def span(profile: np.ndarray, lo: int, hi: int, n: int) -> tuple[int, int]:
        clear = float(np.percentile(profile, 98))
        median = float(np.median(profile))
        if median <= 0 or clear < median * clear_ratio:
            return lo, hi                       # no empty aperture in view
        covered = np.flatnonzero(profile < clear * level)
        if covered.size == 0:
            return lo, hi
        return (
            lo + int(round(covered[0] / n * (hi - lo))),
            lo + int(round(covered[-1] / n * (hi - lo))),
        )

    x0, x1 = span(grey.mean(axis=0), fx0, fx1, grey.shape[1])
    y0, y1 = span(grey.mean(axis=1), fy0, fy1, grey.shape[0])
    if x1 <= x0 or y1 <= y0:
        return full_frame
    return x0, y0, x1, y1


def registration(
    image: np.ndarray,
    full_frame: tuple[int, int, int, int] = FULL_FRAME,
) -> dict[str, float | int]:
    """Where the picture sits in the transport window, from a prescan.

    The window is 36.5 mm and a 35 mm frame is 36 mm, so there is half a
    millimetre of slack: a frame that has drifted is a frame with its edge
    outside the aperture, and no scan window can get that back. The vendor's own
    5-frame strip shows it happening -- its detected windows started at x=96 for
    four frames and then at x=1727 for the fifth, which lost 6 mm of picture.

    ``offset`` is signed, in scanner units at maximum resolution: positive means
    the picture sits right of centre, i.e. the film is under-advanced.

    ``shortfall`` is the one that matters, and it is why the width is measured at
    all. A drifted frame cannot be seen directly -- the prescan only covers the
    aperture, so a picture hanging outside it is simply not there to be found.
    What shows instead is a picture *narrower* than a whole frame. The vendor's
    fifth strip frame measured 8472 units against 10079 for the four before it:
    1.6 k units, 5.7 mm, of picture that never reached the sensor.

    Measurement only. Nothing here moves the film.
    """
    x0, _, x1, _ = film_bounds(image, full_frame)
    fx0, _, fx1, _ = full_frame
    width = x1 - x0
    offset = (x0 + x1) / 2.0 - (fx0 + fx1) / 2.0
    shortfall = max(0, NOMINAL_FRAME_WIDTH - width)

    def mm(units: float) -> float:
        return round(units * MM_PER_INCH / COORD_PER_INCH, 2)

    return {
        "x0": int(x0),
        "x1": int(x1),
        "width": int(width),
        "offset": int(round(offset)),
        "offset_mm": mm(offset),
        "shortfall": int(shortfall),
        "shortfall_mm": mm(shortfall),
        "margin": int(min(x0 - fx0, fx1 - x1)),
        "margin_mm": mm(min(x0 - fx0, fx1 - x1)),
    }


# Film types, for metering. Only the white-balance rule depends on these.
FILM_NEGATIVE = "negative"
FILM_POSITIVE = "positive"
FILM_KODACHROME = "kodachrome"
FILM_BW = "bw"
FILM_TYPES = (FILM_NEGATIVE, FILM_POSITIVE, FILM_KODACHROME, FILM_BW)


# How a roll is metered. See DirectScanner.scan_roll.
METER_EACH = "each"
METER_ONCE = "once"
METER_NONE = "none"
METER_MODES = (METER_EACH, METER_ONCE, METER_NONE)


def locks_white_balance(film: str) -> bool:
    """Whether metering must move the visible channels together.

    A colour negative's orange mask sits over the whole frame. Metering the
    channels as one group leaves it there to be quantised through, and the blue
    record ends up in a fraction of the range it could have had -- so a negative
    is metered per channel, which takes the mask off before the ADC instead of
    after it.

    Everything else keeps its balance. A slide, a Kodachrome and a black and
    white negative all carry their cast because that cast *is* the picture;
    stretching each channel to the same target on its own takes it off.

    Note what this scanner can actually deliver on the negative side. Blue sits
    near the top of the 16-bit exposure timer before any film is loaded -- the
    lamp is weak there and the blue filter passes little -- so there is only
    about x1.2 of exposure left to give it. The mask can be taken off red and
    green; on blue the hardware has almost nothing left. See
    :meth:`DirectScanner.auto_exposure`, which reports when it hits that.
    """
    if film not in FILM_TYPES:
        raise ValueError(f"unknown film type {film!r}; expected one of {FILM_TYPES}")
    return film != FILM_NEGATIVE

# Slide / autofeed transport actions
SLIDE_NEXT = 0x04
SLIDE_PREV = 0x05
SLIDE_INIT = 0x10
SLIDE_RELOAD = 0x40

#: Scanner coordinates are in units of 1/7200 inch.
COORD_PER_INCH = 7200
MM_PER_INCH = 25.4


def _is_unity(scale: float | Sequence[float]) -> bool:
    """Whether an exposure scale asks for no change at all.

    A per-channel scale is a list, and ``[1.0, 1.0, 1.0] != 1.0`` is always
    True, so comparing against the number alone reported every metered scan as
    rescaled.
    """
    if isinstance(scale, (int, float)):
        return float(scale) == 1.0
    return all(float(v) == 1.0 for v in scale)


def _cmd(opcode: int, size: int) -> bytes:
    """Build a 6-byte command; size goes big-endian into bytes 3-4."""
    return bytes([opcode, 0, 0, (size >> 8) & 0xFF, size & 0xFF, 0])


class CalibrationRequired(RuntimeError):
    """The scanner insists on calibrating and will not start the scan."""


class NoMediaLoaded(RuntimeError):
    """No film is loaded in the transport."""


class ScanReadError(RuntimeError):
    """The scanner refused a read of image data."""


class EndOfData(ScanReadError):
    """The scanner has no more scan lines to give.

    Reported as ILLEGAL REQUEST / ASC 0x20 once a scan is exhausted, which is
    indistinguishable by sense alone from a genuinely invalid command -- so it
    is only treated as end-of-data mid-read.
    """


_SENSE_KEYS = {
    0x00: "no sense",
    0x02: "not ready",
    0x03: "medium error",
    0x04: "hardware error",
    0x05: "illegal request",
    0x06: "unit attention",
    0x0B: "aborted command",
}

# Vendor-specific codes. pieusb_usb.c only decodes these under UNIT ATTENTION;
# every other sense key falls through to a generic message there, so they are
# keyed that way here rather than applied to all keys.
_UNIT_ATTENTION_CODES = {
    (0x1A, 0x00): "invalid field in parameter list",
    (0x20, 0x00): "invalid command operation code",
    (0x82, 0x00): "calibration disable not granted",
    (0x00, 0x06): "I/O process terminated",
    (0x26, 0x82): "MODE SELECT invalid: resolution too high",
    (0x26, 0x83): "MODE SELECT invalid: select only one colour",
}

# Standard SCSI additional sense codes, used for every other key.
_ASC = {
    (0x00, 0x00): "no additional sense",
    (0x1A, 0x00): "parameter list length error",
    (0x20, 0x00): "invalid command operation code",
    (0x24, 0x00): "invalid field in CDB",
    (0x25, 0x00): "logical unit not supported",
    (0x26, 0x00): "invalid field in parameter list",
    (0x29, 0x00): "power on or bus device reset occurred",
    (0x2C, 0x00): "command sequence error",
    (0x3D, 0x00): "invalid bits in identify message",
}


#: ASC reported once a scan is exhausted. Indistinguishable by sense alone from
#: a genuinely invalid command, so it only means end-of-data mid-read.
ASC_END_OF_DATA = 0x20

#: ASC the scanner reports while it is still becoming ready.
ASC_NOT_READY = 0x04


@dataclass(frozen=True)
class Sense:
    """A parsed REQUEST SENSE response.

    Exists so a caller can ask what the scanner said instead of matching
    substrings in a log line. A read that ran out of scan lines and a read
    refused for any other reason both arrive as CHECK CONDITION, and only the
    ASC separates them.
    """

    key: int
    code: int
    qualifier: int
    #: Why the sense could not be read, when it could not be.
    problem: str | None = None

    @classmethod
    def parse(cls, info: bytes) -> "Sense":
        if len(info) < 14:
            return cls.unreadable(f"{len(info)} bytes, expected 14")
        return cls(key=info[2] & 0x0F, code=info[12], qualifier=info[13])

    @classmethod
    def unreadable(cls, problem: str) -> "Sense":
        return cls(key=0, code=0, qualifier=0, problem=problem)

    @property
    def readable(self) -> bool:
        return self.problem is None

    @property
    def end_of_data(self) -> bool:
        """The scan is exhausted -- only meaningful mid-read."""
        return self.readable and self.code == ASC_END_OF_DATA

    @property
    def not_ready(self) -> bool:
        return self.readable and self.code == ASC_NOT_READY

    def __str__(self) -> str:
        if not self.readable:
            return f"(sense unavailable: {self.problem})"
        return (
            f"key={self.key:#04x} code={self.code:#04x} "
            f"qual={self.qualifier:#04x} "
            f"-- {describe_sense(self.key, self.code, self.qualifier)}"
        )


def describe_sense(key: int, code: int, qualifier: int) -> str:
    name = _SENSE_KEYS.get(key, f"key {key:#04x}")
    if key == 0x06:
        detail = _UNIT_ATTENTION_CODES.get((code, qualifier))
    else:
        detail = _ASC.get((code, qualifier))
    return f"{name}: {detail}" if detail else name


@dataclass
class Inquiry:
    vendor: str
    product: str
    model: int
    firmware: str
    max_resolution: int
    ccd_width: int
    ccd_length: int
    filters: int
    depths: int
    formats: int
    optional_devices: int
    frame: tuple[int, int, int, int]
    preview_resolution: int

    @property
    def has_infrared(self) -> bool:
        return bool(self.filters & 0x10)

    @property
    def has_adf(self) -> bool:
        return bool(self.optional_devices & 0x01)

    @property
    def supports_16bit(self) -> bool:
        return bool(self.depths & DEPTH_16)

    def describe(self) -> str:
        return (
            f"{self.vendor} {self.product} (model {self.model:#06x}, "
            f"fw {self.firmware})\n"
            f"  optical resolution : {self.max_resolution} dpi\n"
            f"  CCD                : {self.ccd_width} x {self.ccd_length} px\n"
            f"  infrared channel   : {'yes' if self.has_infrared else 'no'}\n"
            f"  16-bit             : {'yes' if self.supports_16bit else 'no'}\n"
            f"  ADF / autofeed     : {'yes' if self.has_adf else 'no'}\n"
            f"  preview resolution : {self.preview_resolution} dpi\n"
            f"  scan frame         : {self.frame}"
        )


@dataclass
class ScanParameters:
    width: int           # pixels per line
    lines: int           # total lines in the scan
    bytes_per_line: int
    filter_offset1: int
    filter_offset2: int
    available_lines: int  # lines ready to read right now


#: Bit seen set in the state byte when a strip holder was inserted (0x0D empty,
#: 0x4D loaded). Its exact meaning is unconfirmed -- it has since read 0x0D with
#: film demonstrably loaded -- so it is reported but never used to block a scan.
MEDIA_PRESENT = 0x40


@dataclass
class State:
    button: bool
    warming_up: bool
    scanning: int
    busy: int
    #: Where the transport has the film, counting from 0. This is the one
    #: trustworthy signal that an advance has happened -- see
    #: :meth:`DirectScanner.advance`.
    position: int = 0

    @property
    def media_loaded(self) -> bool:
        """Whether film is in the transport.

        The scanner ejects the strip at the end of every scan, so this is False
        again after each frame until the film is re-inserted.

        Unreliable, and only reported. The bit is clear in every state seen in
        six captures of the vendor software, including ones taken with film
        demonstrably loaded. :attr:`position` is what to trust about the
        transport.
        """
        return bool(self.scanning & MEDIA_PRESENT)


@dataclass
class Settings:
    """Per-channel exposure, gain and offset, in R, G, B, I order."""

    exposure: list[int]
    gain: list[int]
    offset: list[int]
    light: int = 4
    extra_entries: int = 0
    double_times: int = 0

    def scaled(self, factor: float | Sequence[float]) -> Settings:
        """Copy with exposure multiplied, clamped to the 16-bit field.

        ``factor`` may be one number for every channel, or one per channel in
        R, G, B, I order -- the channels need very different exposures, most
        obviously blue, which saturates far sooner than the rest with no film
        in the transport.

        Always a new object, including at a factor of 1.0. Returning self there
        aliases the caller's settings to the device's, so a later edit to one
        silently moves the other.
        """
        if isinstance(factor, (int, float)):
            factors = [float(factor)] * len(self.exposure)
        else:
            factors = list(factor)
            factors += [1.0] * (len(self.exposure) - len(factors))
        return Settings(
            exposure=[
                int(max(100, min(65535, round(e * f))))
                for e, f in zip(self.exposure, factors)
            ],
            gain=list(self.gain),
            offset=list(self.offset),
            light=self.light,
            extra_entries=self.extra_entries,
            double_times=self.double_times,
        )

    def describe(self) -> str:
        return (
            f"exposure={'-'.join(map(str, self.exposure))} "
            f"gain={'-'.join(map(str, self.gain))} "
            f"offset={'-'.join(map(str, self.offset))} light={self.light}"
        )


@dataclass
class RollFrame:
    """One picture from a roll, as :meth:`DirectScanner.scan_roll` yields it.

    ``image`` and ``meta`` are None and empty on a frame that failed, or on a
    dry run; ``error`` says which. A failed frame is still yielded, because a
    roll takes hours and the caller needs to know what it lost without losing
    the rest.
    """

    index: int                          # 0-based, from the start of the roll
    position: int | None                # what READ_STATE said the transport held
    image: np.ndarray | None
    meta: dict[str, Any]
    prescan: np.ndarray | None
    registration: dict[str, Any]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.image is not None


class DirectScanner:
    """Command-level control of the scanner."""

    def __init__(self, transport: Transport | None = None, verbose: bool = False):
        self.verbose = verbose
        self._own_transport = transport is None
        self.t = transport or Transport(verbose=verbose)
        self._scanning = False
        self._inquiry: Inquiry | None = None
        # The shading reference this session has acquired. The scanner returns
        # raw pixels and never applies it itself -- see rps7200.shading.
        self._shading: ShadingReference | None = None
        self._ccd_mask: bytes | None = None
        # The last pass's bytes exactly as the scanner sent them, kept only
        # when asked: enough to rebuild the image if the decode ever changes.
        self.last_raw: bytes | None = None
        self.last_raw_layout: dict[str, Any] | None = None

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[scan] {message}")

    # -- the session's calibration -----------------------------------------
    #
    # The scanner hands back its per-column response and never applies it, so
    # correcting a scan needs the reference, the CCD mask for that pass, and --
    # to correct it again later, differently -- the raw bytes. All three belong
    # to the session and vanish with it, which is why they are reachable rather
    # than private: every tool needs them to file a scan, and reaching into
    # `_shading` from outside is how that was done before.

    @property
    def shading(self) -> ShadingReference | None:
        """The reference this session will correct with, or None."""
        return self._shading

    @shading.setter
    def shading(self, reference: ShadingReference | None) -> None:
        self._shading = reference

    @property
    def ccd_mask(self) -> bytes | None:
        """The column mapping for the most recent pass.

        Read per pass, not per session: the mask says which CCD pixels *this*
        resolution sampled, so a scan saved with the calibration pass's mask
        cannot be corrected afterwards.
        """
        return self._ccd_mask

    def load_shading(self, path: str | Path) -> ShadingReference:
        """Use a reference saved earlier instead of calibrating.

        Saves the 3-4 minutes a calibration costs, at the price of a reference
        that describes the sensor at the exposure and gain of the pass that
        measured it -- prefer a fresh one when the exposure has moved.
        """
        self._shading = ShadingReference.load(Path(path))
        self._log(
            f"loaded shading from {path}: {self._shading.pixels_per_line} columns, "
            f"channels {self._shading.channels}"
        )
        return self._shading

    def save_shading(self, path: str | Path) -> Path | None:
        """Write this session's reference. Returns None when there is none."""
        if self._shading is None:
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._shading.save(path)
        return path

    def ensure_shading(
        self, path: str | Path, reuse: bool = False, skip: bool = False
    ) -> dict[str, Any]:
        """Have a shading reference for this session: load one, or measure one.

        Once per session, as the vendor does once per power-on -- every later
        scan reuses the result. Returns what happened, so a caller can report it
        without repeating the decision:

        ``action``     one of ``"skipped"``, ``"loaded"``, ``"calibrated"``
        ``reference``  the reference now in force, or None
        ``path``       where it was read from or written to, or None
        ``duration_s`` what the calibration cost, when one ran
        ``summary``    one line saying which of those happened, to print

        ``skip`` leaves the session with no reference at all, which returns raw
        pixels: the scanner never corrects its own output, so scans then come
        back striped.
        """
        path = Path(path)
        if skip:
            return {
                "action": "skipped",
                "reference": None,
                "path": None,
                "summary": "shading correction disabled: expect vertical striping",
            }

        if reuse and path.exists():
            reference = self.load_shading(path)
            return {
                "action": "loaded",
                "reference": reference,
                "path": path,
                "summary": (
                    f"reusing {path} ({reference.pixels_per_line} columns, "
                    f"channels {reference.channels})"
                ),
            }

        started = time.monotonic()
        result = self.calibrate_shading()
        duration = round(time.monotonic() - started, 1)
        saved = self.save_shading(path)
        drained = result["bytes_drained"] / 1e6
        summary = f"  {drained:.2f} MB in {duration:.0f}s"
        summary += (
            f", saved {saved}" if result["reference"] is not None
            else " -- no usable shading reference; scans will be raw"
        )
        return {
            "action": "calibrated",
            "reference": result["reference"],
            "path": saved,
            "duration_s": duration,
            "bytes_drained": result["bytes_drained"],
            "summary": summary,
        }

    def capture_record(self) -> dict[str, Any]:
        """Everything `rps7200.library.save` needs beyond the pixels and meta.

        Gathered while the session is open and written after it closes: filing
        an entry gzips well over a hundred megabytes, and holding the device
        open and idle through that has preceded it going unresponsive.

        Only the most recent pass is described. A bracket has to call this once
        per pass, as each is captured, because `last_raw` is overwritten.
        """
        return {
            "reference": self._shading,
            "ccd_mask": self._ccd_mask,
            "raw": self.last_raw,
            "raw_layout": self.last_raw_layout,
        }

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DirectScanner:
        if self._own_transport:
            self.t.open()
        return self

    def close(self) -> None:
        # Always attempt both, whatever state we think we are in: a scan left
        # running, or a command left half-issued, wedges the scanner until it is
        # power-cycled, and our idea of the state may be wrong.
        # No STOP SCAN and no bridge reset: the vendor software sends neither,
        # and resetting here is what left the next session unable to talk to the
        # scanner.
        self._scanning = False
        if self._own_transport:
            self.t.close()

    def __enter__(self) -> DirectScanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- basic commands ----------------------------------------------------

    def inquiry(self, refresh: bool = False) -> Inquiry:
        if not refresh and self._inquiry is not None:
            return self._inquiry
        head = self.t.command(_cmd(SCSI_INQUIRY, 5), read_size=5)
        length = head[4] + 4
        d = self.t.command(_cmd(SCSI_INQUIRY, length), read_size=length)

        def text(start: int, size: int) -> str:
            return d[start : start + size].decode("ascii", "replace").rstrip("\x00 ")

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        # Offsets follow sanei_pieusb_cmd_inquiry in pieusb_scancmd.c.
        result = Inquiry(
            vendor=text(8, 8),
            product=text(16, 16),
            model=short(116),
            firmware=text(32, 4),
            max_resolution=short(36),
            ccd_width=short(40),
            ccd_length=short(42),
            filters=d[44],
            depths=d[45],
            formats=d[46],
            optional_devices=d[50],
            frame=(short(108), short(110), short(112), short(114)),
            preview_resolution=short(54),
        )
        self._inquiry = result
        return result

    def _query(
        self, command: bytes, read_size: int, label: str, retries: int = 3
    ) -> bytes:
        """Run a status/parameter command, absorbing one-shot CHECK CONDITIONs.

        The scanner queues a sense condition and reports it on whatever command
        comes next, whether or not that command is the one it relates to.
        Reading the sense clears it, so a retry generally succeeds.
        """
        last: Sense | str = ""
        for attempt in range(1, retries + 1):
            try:
                return self.t.command(command, read_size=read_size)
            except CheckCondition:
                last = self.read_sense()
                self._log(f"  {label}: {last}")
                time.sleep(0.3)
        raise ScanReadError(f"{label} failed after {retries} attempts: {last}")

    def read_state(self, retries: int = 3) -> State:
        d = self._query(
            _cmd(SCSI_READ_STATE, 13), 13, "read_state", retries=retries
        )
        return State(
            button=bool(d[0]),
            warming_up=bool(d[5]),
            scanning=d[6],
            busy=d[8],
            position=d[2],
        )

    def sense(self) -> bytes:
        return self.t.command(_cmd(SCSI_REQUEST_SENSE, 14), read_size=14)

    def read_sense(self) -> Sense:
        """REQUEST SENSE, parsed. Never raises.

        A sense read can itself fail -- the condition it was going to explain is
        often the reason -- and a caller deciding what a refusal meant still has
        to decide something. `Sense.unreadable` is that case, and it answers no
        to every question rather than pretending to be a code.
        """
        try:
            return Sense.parse(self.sense())
        except (UsbError, IndexError) as exc:
            return Sense.unreadable(str(exc))

    def _unit_ready_sense(self) -> Sense | None:
        """TEST UNIT READY; returns None when good, else what it complained of."""
        try:
            self.t.command(_cmd(SCSI_TEST_UNIT_READY, 0))
            return None
        except CheckCondition:
            return self.read_sense()

    def wait_warm(self, timeout: float = 300.0, poll: float = 5.0) -> None:
        """Wait out the lamp warm-up (about 80 s from cold).

        Polls TEST UNIT READY rather than READ STATE: while the lamp warms, the
        scanner answers NOT READY (ASC 0x04) to *every* command, READ STATE
        included, so asking it for its state cannot work.
        """
        deadline = time.monotonic() + timeout
        announced = False
        while True:
            info = self._unit_ready_sense()
            if info is None:
                return
            warming = info.key == 0x02 or info.not_ready
            if not warming:
                # Some other one-shot condition; reading the sense cleared it.
                self._log(f"  wait_warm: {info}")
                if time.monotonic() > deadline:
                    return
                time.sleep(0.5)
                continue
            if not announced:
                print(
                    f"  lamp warming up (up to {timeout:.0f}s) ...", flush=True
                )
                announced = True
            if time.monotonic() > deadline:
                raise TimeoutError(f"lamp still warming after {timeout:.0f}s")
            time.sleep(poll)

    def test_unit_ready(self) -> bool:
        """Standard SCSI TEST UNIT READY.

        Also the conventional way to clear a pending sense condition, which is
        why the backend leans on it between phases -- the scanner refuses data
        reads while one is outstanding.
        """
        try:
            self.t.command(_cmd(SCSI_TEST_UNIT_READY, 0))
            return True
        except CheckCondition:
            try:
                self._log(f"  unit not ready: {self.read_sense()}")
            except UsbError:
                pass
            return False

    def wait_ready(self, timeout: float = 120.0, poll: float = 0.5) -> bool:
        """Poll TEST UNIT READY until the scanner reports good, as SANE does."""
        deadline = time.monotonic() + timeout
        while True:
            if self.test_unit_ready():
                return True
            if time.monotonic() > deadline:
                self._log("wait_ready timed out")
                return False
            time.sleep(poll)

    # -- configuration -----------------------------------------------------

    def _write_sub(self, sub: int, filter_mask: int, value: int) -> None:
        """Send an 8-byte sub-command payload via SCSI WRITE."""
        data = bytearray(8)
        data[0:2] = sub.to_bytes(2, "little")
        data[2:4] = (8 - 4).to_bytes(2, "little")
        data[4:6] = filter_mask.to_bytes(2, "little")
        data[6:8] = value.to_bytes(2, "little")
        self.t.command(_cmd(SCSI_WRITE, 8), data=bytes(data))

    def set_exposure_time(self, values: tuple[int, int, int] = (100, 100, 100)) -> None:
        """Set relative exposure time per channel (0-100), one write each."""
        self._log(f"exposure time {values}")
        for mask, value in zip((0x02, 0x04, 0x08), values):
            self._write_sub(SUB_EXPOSURE, mask, value)

    def set_highlight_shadow(
        self, values: tuple[int, int, int] = (100, 100, 100)
    ) -> None:
        self._log(f"highlight/shadow {values}")
        for mask, value in zip((0x02, 0x04, 0x08), values):
            self._write_sub(SUB_HIGHLIGHT_SHADOW, mask, value)

    def get_shading_parms(self) -> list[dict[str, int]]:
        """Read the shading/calibration descriptor (prepare-then-read)."""
        prep = bytearray(6)
        prep[0] = SUB_CALIBRATION_INFO | 0x80  # bit 7 = prepare read
        self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(prep))
        d = self._query(_cmd(SCSI_READ, 32), 32, "get_shading_parms")

        entries, entry_size = d[4], d[5]
        out = []
        for k in range(entries):
            base = 8 + entry_size * k
            out.append(
                {
                    "type": d[base],
                    "send_bits": d[base + 1],
                    "receive_bits": d[base + 2],
                    "lines": d[base + 3],
                    "pixels_per_line": int.from_bytes(
                        d[base + 4 : base + 6], "little"
                    ),
                }
            )
        self._log(f"shading parms: {out}")
        return out

    def set_scan_frame(
        self, x0: int, y0: int, x1: int, y1: int, index: int = 0x80
    ) -> None:
        """Set the scan window.

        Coordinates are 0-based pixels at the scanner's maximum resolution, so
        a full frame is ``(0, 0, ccd_width - 1, ccd_length - 1)`` -- not the
        ``x0,y0,x1,y1`` reported by INQUIRY, which describe something else.
        ``index`` is 0x80, matching what the backend sends; 0 is not accepted.
        """
        data = bytearray(14)
        data[0:2] = SUB_SCAN_FRAME.to_bytes(2, "little")
        data[2:4] = (14 - 4).to_bytes(2, "little")
        data[4:6] = index.to_bytes(2, "little")
        data[6:8] = x0.to_bytes(2, "little")
        data[8:10] = y0.to_bytes(2, "little")
        data[10:12] = x1.to_bytes(2, "little")
        data[12:14] = y1.to_bytes(2, "little")
        self._log(f"scan frame {x0},{y0} -> {x1},{y1}")
        self.t.command(_cmd(SCSI_WRITE, 14), data=bytes(data))

    def set_mode(
        self,
        resolution: int,
        passes: int = ONE_PASS_RGBI,
        depth: int = DEPTH_16,
        color_format: int = FORMAT_PIXEL,
        skip_shading: bool = True,
        calibrate: bool = False,
        sharpen: bool = False,
        fast_infrared: bool = False,
        halftone_pattern: int = 0,
        line_threshold: int = 0x80,
    ) -> None:
        """Configure the scan.

        ``skip_shading`` defaults to True deliberately: leaving it off is what
        sends the backend into the shading read this scanner cannot complete.
        """
        quality = 0
        if sharpen:
            quality |= QUALITY_SHARPEN
        if calibrate:
            quality |= QUALITY_CALIBRATE      # byte 10; excludes skip-shading
        elif skip_shading:
            quality |= QUALITY_SKIP_SHADING   # byte 9
        if fast_infrared:
            quality |= QUALITY_FAST_INFRARED

        data = bytearray(16)
        data[1] = 16 - 1
        data[2:4] = resolution.to_bytes(2, "little")
        data[4] = passes
        data[5] = depth
        data[6] = color_format
        data[8] = BYTE_ORDER_INTEL
        data[9:11] = int(quality).to_bytes(2, "little")
        data[12] = halftone_pattern if halftone_pattern else 0x02
        data[13] = line_threshold
        # Byte 14 is 0x21 for a four-channel RGBI pass and 0x10 for RGB,
        # from captures of the vendor software with and without infrared
        # cleaning enabled. pieusb hardcodes 0x10 (its comment reads "?"),
        # which is why it never yields an infrared plane.
        data[14] = 0x21 if passes == ONE_PASS_RGBI else 0x10

        self._log(
            f"mode res={resolution} passes={passes:#04x} depth={depth:#04x} "
            f"format={color_format:#04x} quality={quality:#04x}"
        )
        self.t.command(_cmd(SCSI_MODE_SELECT, 16), data=bytes(data))

    # -- scanning ----------------------------------------------------------

    def cmd_17(self, value: int = 1) -> None:
        """Vendor command 0x17, sent right after the scan frame.

        This is what makes the scanner *grant* "skip shading analysis". Without
        it, MODE SELECT with quality bit 0x08 is refused with sense 0x82
        ("calibration disable not granted"), the scanner insists on a shading
        pass, and the shading read then stalls at 32768 bytes.

        pieusb has this command but only issues it for models its config marks
        as having a slide transport -- which is 0 for model 0x31 -- so the stock
        backend never sends it here. CyberView always does.

        Captured bytes: cmd `0a 00 00 00 06 00`, data `17 00 02 00 01 00`.
        """
        data = bytearray(6)
        data[0:2] = SUB_CMD_17.to_bytes(2, "little")
        data[2:4] = (2).to_bytes(2, "little")
        data[4:6] = value.to_bytes(2, "little")
        self._log(f"cmd_17({value})")
        self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(data))

    def slide(
        self, action: int = SLIDE_INIT, param: int = 0x16, value: int = 0
    ) -> None:
        """Drive the film/slide transport.

        pieusb only issues this when its config marks the model as having a
        slide transport, and for model 0x31 that flag is 0, so the stock backend
        never initialises the transport at all -- even though INQUIRY reports an
        ADF. SLIDE_NEXT is also how a whole strip gets advanced frame by frame.

        The payload is four bytes, ``action param 00 value``. ``param`` is 0x16
        in the capture this driver was reconstructed from; it takes 0x01, 0x13,
        0x14, 0x15 and 0x16 across the six captures with no visible difference,
        so it is left where it is. ``value`` matters: every film advance the
        vendor performs carries 1 there (once 2), never 0, which is what this
        driver used to send.
        """
        names = {
            SLIDE_NEXT: "next",
            SLIDE_PREV: "prev",
            SLIDE_INIT: "init",
            SLIDE_RELOAD: "reload",
        }
        self._log(f"slide transport: {names.get(action, hex(action))}")
        data = bytes([action, param, 0x00, value])
        self.t.command(_cmd(SCSI_SLIDE, 4), data=data)

    def advance(
        self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5
    ) -> int | None:
        """Move the film on by one frame, and wait until it has.

        The payload is the vendor's: ``04 01 00 01``, seen three times in
        ``600_ICE_FILM_STRIP_5.pcapng`` (with ``04 01 00 02`` once, for reasons
        the capture does not explain -- the position still moved by one).

        Waiting is the point. `READ_STATE` byte 2 is the transport position, and
        it is the only signal in any capture that says the film has actually
        moved: it stepped 0 -> 1 -> 2 -> 3 -> 4 across the strip session's four
        advances, and stayed put through a session that never advanced. The new
        value showed up 1.6 s to 6.2 s later, and the READ_STATE issued
        immediately after the command came back empty every time -- so the poll
        has to survive a failed read rather than treat it as the end.

        Returns the new position, or None if it never moved -- which is how a
        roll ends.
        """
        before = self.position()
        self.slide(SLIDE_NEXT, param=0x01, value=steps)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll)
            now = self.position()
            if now is not None and now != before:
                self._log(f"advanced to position {now}")
                return now
        self._log(
            f"no advance: position still {before} after {timeout:.0f}s -- "
            "treating this as the end of the film"
        )
        return None

    def position(self) -> int | None:
        """Where the transport has the film, or None if it would not say.

        The one trustworthy signal that an advance happened -- see
        :meth:`advance`. Never raises: a READ_STATE issued right after a
        transport command comes back empty every time, so a caller polling this
        has to be able to tell "not yet" from "failed".
        """
        try:
            return self.read_state(retries=1).position
        except (CheckCondition, UsbError, ScanReadError, IndexError):
            return None

    def start_scan(
        self,
        retries: int = 15,
        ready_timeout: float = 600.0,
        ready_poll: float = 1.0,
    ) -> None:
        """Begin scanning.

        Two distinct conditions have to be waited out, and they are counted
        separately:

        * NOT READY (ASC 0x04) -- the scanner is still preparing. This is not a
          failure and does not consume a retry; higher resolutions take longer,
          60+ seconds at 1800 dpi. Polled until ``ready_timeout``.
        * UNIT ATTENTION 0x82 ("calibration disable not granted") and friends --
          one-shot conditions that clear when read. These do consume a retry,
          but typically need two or three attempts before the scan starts.
        """
        deadline = time.monotonic() + ready_timeout
        attempts = 0
        last = Sense.unreadable("the scanner never refused with a sense")

        while True:
            try:
                self.t.command(_cmd(SCSI_SCAN, 1))
                self._scanning = True
                return
            except CheckCondition:
                # read_sense rather than sense(): a REQUEST SENSE that itself
                # fails used to propagate straight out of this handler, leaving
                # the start abandoned mid-sequence, which is what wedges the
                # scanner. An unreadable sense is a reason to retry, not to
                # walk away.
                last = self.read_sense()

                if last.not_ready:        # still becoming ready
                    if time.monotonic() > deadline:
                        break
                    time.sleep(ready_poll)
                    continue

                attempts += 1
                self._log(f"  start_scan: {last} (retry {attempts}/{retries})")
                if attempts >= retries or time.monotonic() > deadline:
                    break
                time.sleep(0.5)

        # Leave the scanner usable; an abandoned start wedges it otherwise.
        self._scanning = False
        raise CalibrationRequired(f"scanner refused to start: {last}")

    def finish_scan(self, polls: int = 3) -> None:
        """End a completed scan the way the vendor software does.

        CyberView never sends STOP SCAN. It reads all the data and then polls
        READ_STATE while the scanner settles. Sending STOP SCAN after a
        successful read appears to be what leaves this scanner unresponsive to
        the next session, so it is reserved for cancelling a scan that is still
        running.
        """
        self._scanning = False
        for _ in range(polls):
            try:
                self.read_state()
            except (CheckCondition, UsbError, ScanReadError):
                return
            time.sleep(0.2)

    def stop_scan(self) -> None:
        """Stop scanning. Never raises -- it runs on the cleanup path.

        Leaving a scan running is what wedges the scanner badly enough to need
        a power cycle, so this always makes the attempt.
        """
        self._log("stop scan")
        try:
            self.t.command(_cmd(SCSI_SCAN, 0))
        except CheckCondition:
            try:
                self._log(f"  stop_scan: {self.read_sense()}")
            except UsbError:
                pass
        except UsbError as exc:
            self._log(f"  stop_scan failed: {exc}")
        finally:
            self._scanning = False

    def get_gain_offset(self) -> Settings:
        """Read the scanner's current exposure/gain/offset."""
        d = self._query(
            _cmd(SCSI_READ_GAIN_OFFSET, 123), 123, "get_gain_offset"
        )

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        return Settings(
            exposure=[short(60), short(62), short(64), short(98)],
            offset=[d[66], d[67], d[68], d[100]],
            gain=[d[72], d[73], d[74], d[102]],
            light=d[75],
        )

    def set_gain_offset(self, s: Settings, infrared: bool = False) -> None:
        """Write exposure/gain/offset.

        The scanner will not accept a data READ until this has been sent -- it
        answers ILLEGAL REQUEST otherwise. This is the calibration step it means
        by "calibration disable not granted".
        """
        data = bytearray(29)
        for i in range(3):
            data[i * 2 : i * 2 + 2] = int(s.exposure[i]).to_bytes(2, "little")
            data[6 + i] = int(s.offset[i]) & 0xFF
            data[12 + i] = int(s.gain[i]) & 0xFF
        data[15] = s.light & 0xFF
        # With infrared enabled the vendor software sets byte 16 (extra
        # entries) and byte 27; both are 0 for a plain RGB pass.
        data[16] = 1 if infrared else (s.extra_entries & 0xFF)
        data[17] = s.double_times & 0xFF
        if infrared:
            data[27] = 1
        data[18:20] = int(s.exposure[3]).to_bytes(2, "little")
        data[20] = int(s.offset[3]) & 0xFF
        data[22] = int(s.gain[3]) & 0xFF

        self._log(f"gain/offset {s.describe()}")
        self.t.command(_cmd(SCSI_WRITE_GAIN_OFFSET, 29), data=bytes(data))

    def get_ccd_mask(self, size: int) -> bytes:
        """Read the CCD mask (SCSI COPY).

        ``sane_start`` performs this in "scan phase 3", immediately before
        reading scan parameters and image data. ``size`` is the shading width
        from :meth:`get_shading_parms`.
        """
        data = self._query(_cmd(SCSI_COPY, size), size, "get_ccd_mask")
        self._log(f"ccd mask: {len(data)} bytes")
        return data

    def get_parameters(self) -> ScanParameters:
        d = self._query(_cmd(SCSI_PARAM, 18), 18, "get_parameters")
        return ScanParameters(
            width=int.from_bytes(d[0:2], "little"),
            lines=int.from_bytes(d[2:4], "little"),
            bytes_per_line=int.from_bytes(d[4:6], "little"),
            filter_offset1=d[6],
            filter_offset2=d[7],
            available_lines=int.from_bytes(d[14:16], "little"),
        )

    def read_lines(
        self,
        lines: int,
        bytes_per_line: int,
        retries: int = 3,
        timeout_ms: int = 120_000,
        max_wait_s: float = 300.0,
    ) -> bytes:
        """Read ``lines`` scan lines.

        Retries like :meth:`_query` does: a queued one-shot sense condition is
        reported against whichever command arrives next, so the first attempt
        can be rejected for something that has nothing to do with this read.

        ``max_wait_s`` is well above the transport's 60 s default because a
        read here waits on the scanner physically scanning, not on a bus
        round trip. Infrared holds the device busy for its own ~212 s floor
        however few lines were asked for, so at low resolution with IR the
        60 s default expired first -- and abandoning a read mid-scan is what
        leaves this device needing a power cycle.
        """
        last = Sense.unreadable("no attempt was made")
        for _ in range(retries):
            try:
                return self.t.command(
                    _cmd(SCSI_READ, lines),
                    read_size=lines * bytes_per_line,
                    timeout_ms=timeout_ms,
                    max_wait_s=max_wait_s,
                )
            except CheckCondition:
                last = self.read_sense()
                self._log(f"  read_lines: {last}")
                time.sleep(0.3)
        if last.end_of_data:
            raise EndOfData(
                f"scanner has no more lines (asked for {lines}): {last}"
            )
        raise ScanReadError(
            f"reading {lines} lines x {bytes_per_line} bytes was refused: {last}"
        )

    def read_planes(
        self,
        params: ScanParameters,
        channels: int,
        batch: int | None = None,
        timeout: float = 3600.0,
        poll: float = 0.02,
        idle_timeout: float = 120.0,
        keep_raw: bool = False,
    ) -> np.ndarray:
        """Read a frame and deinterleave it into ``(H, W, channels)``.

        In INDEX colour format the scanner sends one colour plane per line,
        each prefixed with a 2-byte header whose first byte is the ASCII channel
        letter -- 'R', 'G', 'B' or 'I'. A frame is therefore ``channels x height``
        lines of ``bytes_per_line + 2``.

        Reads are paced against ``available_lines``, which rises as the scanner
        physically scans. Asking for more lines than it has ready makes the read
        stall until it times out, and a bulk timeout is unrecoverable -- the
        device then needs a power cycle. This is why the vendor software's reads
        come in uneven sizes (216, 3, 216, 216, 105, 105): it takes whatever is
        ready.
        """
        bpl = params.bytes_per_line + INDEX_HEADER
        total_lines = channels * params.lines
        if batch is None:
            batch = batch_for(bpl)
        deadline = time.monotonic() + timeout

        self._log(
            f"reading {total_lines} lines x {bpl} bytes, {batch} per request"
        )

        chunks: list[bytes] = []
        got = 0
        idle_since: float | None = None
        while got < total_lines:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"read {got}/{total_lines} lines before timing out"
                )

            n = min(batch, total_lines - got)
            try:
                chunk = self.read_lines(n, bpl, retries=1)
            except NoDataYet:
                # The scanner has not scanned this far yet. This is its normal
                # way of saying "wait" -- the vendor software sees it on most
                # of its reads and simply asks again a moment later. Notably it
                # does not poll scan parameters to pace itself, and doing so
                # here was slow enough at high resolution to abort the scan.
                now = time.monotonic()
                idle_since = idle_since or now
                if now - idle_since > idle_timeout:
                    raise ScanReadError(
                        f"no data for {idle_timeout:.0f}s at "
                        f"{got}/{total_lines} lines"
                    ) from None
                time.sleep(poll)
                continue
            except EndOfData:
                self._log(f"end of data at {got}/{total_lines} lines")
                break

            idle_since = None
            chunks.append(chunk)
            got += n
            self._log(f"{got}/{total_lines} lines")

        blob = b"".join(chunks)
        if keep_raw:
            # Everything a decoder needs, so the bytes stay meaningful without
            # this object. Line stride includes the 2-byte channel tag.
            self.last_raw = blob
            self.last_raw_layout = {
                "format": "index",
                "bytes_per_line": int(params.bytes_per_line),
                "line_stride": int(params.bytes_per_line) + INDEX_HEADER,
                "index_header": INDEX_HEADER,
                "width": int(params.width),
                "lines": int(params.lines),
                "channels": int(channels),
                "byte_order": "little",
                "lines_received": len(blob) // (int(params.bytes_per_line) + INDEX_HEADER),
            }
        return self._deinterleave(blob, params, channels)

    @staticmethod
    def _deinterleave(
        blob: bytes, params: ScanParameters, channels: int
    ) -> np.ndarray:
        bpl = params.bytes_per_line + INDEX_HEADER
        depth_bytes = params.bytes_per_line // params.width if params.width else 2
        dtype = np.dtype("<u2") if depth_bytes == 2 else np.dtype(np.uint8)

        planes: dict[str, list[np.ndarray]] = {}
        for i in range(len(blob) // bpl):
            line = blob[i * bpl : (i + 1) * bpl]
            tag = chr(line[0])
            planes.setdefault(tag, []).append(
                np.frombuffer(line[INDEX_HEADER:], dtype=dtype)
            )

        order = [c for c in CHANNEL_ORDER if c in planes]
        if not order:
            raise ScanReadError(
                "no recognisable channel tags in scan data; "
                f"saw {sorted(set(planes))!r}"
            )
        if len(order) != channels:
            raise ScanReadError(
                f"expected {channels} channels {list(CHANNEL_ORDER[:channels])}, "
                f"but the scanner produced {len(order)}: {order} "
                f"({ {c: len(v) for c, v in planes.items()} })"
            )

        height = min(len(planes[c]) for c in order)
        return np.stack([np.array(planes[c][:height]) for c in order], axis=-1)

    # -- prescan and framing -----------------------------------------------

    def prescan(
        self, resolution: int = 300, frame: tuple[int, int, int, int] | None = None
    ) -> tuple[np.ndarray, ScanParameters]:
        """Low-resolution RGB pass over the full transport.

        This is what the vendor software runs before every frame: 300 dpi,
        three channels, 8-bit, covering the whole scan area. It carries no
        infrared -- captures confirm the prescan is always ``passes=0x80`` --
        and exists to find where the picture actually sits.

        Shading correction is off, and has to be: the reference is measured in
        16-bit units and this pass is 8-bit, so subtracting its dark half would
        drive every pixel to zero. A framing pass does not need the correction
        anyway -- it is looking for where the picture stops, not at its
        colour.
        """
        image, meta = self.scan(
            resolution=resolution,
            infrared=False,
            depth=DEPTH_8,
            frame=frame or FULL_FRAME,
            shading=False,
        )
        params = ScanParameters(
            width=meta["width"],
            lines=meta["height"],
            bytes_per_line=meta["bytes_per_line"],
            filter_offset1=0,
            filter_offset2=0,
            available_lines=0,
        )
        return image, params

    @staticmethod
    def detect_frame(
        image: np.ndarray,
        full_frame: tuple[int, int, int, int] = FULL_FRAME,
        threshold: float = 0.25,
        pad: int = 0,
        inset: bool = False,
    ) -> tuple[int, int, int, int]:
        """Locate the picture within a prescan image.

        Film base and the gaps between frames are close to uniform, while the
        picture varies; so the picture is the region whose per-row and
        per-column variation rises above a fraction of the maximum. Returns
        scanner coordinates (0-based pixels at maximum resolution), ready to
        pass to :meth:`set_scan_frame`.

        **Not reliable for locating film**, and measurement says so. The
        threshold is a fraction of the *maximum*, so one high-variance column
        sets the scale for all of them -- and the film's own edge, skewed a few
        pixels across the sensor, is exactly that: it read std 36-59 on a real
        prescan against the picture's 1-10. Four columns of 428 cleared the
        threshold and a frame filling the window came back as a 0.26 mm sliver.
        Use :func:`film_bounds`, which keys on level instead and does not depend
        on picture content.
        """
        grey = image.astype(np.float64)
        if grey.ndim == 3:
            grey = grey.mean(axis=2)

        col = grey.std(axis=0)
        row = grey.std(axis=1)

        def bounds(profile: np.ndarray) -> tuple[int, int]:
            peak = float(profile.max())
            if peak <= 0:
                return 0, len(profile) - 1
            active = np.flatnonzero(profile >= peak * threshold)
            if active.size == 0:
                return 0, len(profile) - 1
            return int(active[0]), int(active[-1])

        c0, c1 = bounds(col)
        r0, r1 = bounds(row)
        h, w = grey.shape

        fx0, fy0, fx1, fy1 = full_frame
        span_x, span_y = fx1 - fx0, fy1 - fy0

        x0 = fx0 + int(round((max(0, c0 - pad) / w) * span_x))
        x1 = fx0 + int(round((min(w - 1, c1 + pad) / w) * span_x))
        y0 = fy0 + int(round((max(0, r0 - pad) / h) * span_y))
        y1 = fy0 + int(round((min(h - 1, r1 + pad) / h) * span_y))

        if inset:
            # Opt-in only: trims the film edge the way CyberView does, at the
            # cost of a little real image area.
            x0 = max(x0, fx0 + MIN_INSET_X)
            y0 = max(y0, fy0 + MIN_INSET_Y)
            x1 = min(x1, fx1 - MIN_INSET_X)
            y1 = min(y1, fy1 - MIN_INSET_Y)

        # Never return a degenerate window.
        if x1 <= x0 or y1 <= y0:
            return full_frame
        return x0, y0, x1, y1

    def session_start(self) -> None:
        """Open a session the way the vendor software does after power-on.

        INQUIRY, then the vendor command 0xE7, then REQUEST SENSE and a SLIDE
        with `00 01 00 04`. 0xE7 takes no data and its meaning is unknown, but
        it appears at the start of every captured session and only in the two
        captures that contain a successful calibration -- so it may be what
        puts the scanner into a state where calibration is accepted.
        """
        self.inquiry(refresh=True)
        try:
            self.t.command(_cmd(SCSI_VENDOR_E7, 4))
            self._log("0xE7 accepted")
        except CheckCondition:
            try:
                self._log(f"0xE7: {self.read_sense()}")
            except UsbError:
                pass
        except UsbError as exc:
            self._log(f"0xE7 failed: {exc}")
        try:
            self.sense()
        except UsbError:
            pass
        try:
            self.slide(0x00, param=0x01)
        except (CheckCondition, UsbError) as exc:
            self._log(f"opening slide failed: {exc}")

    def calibrate_shading(
        self,
        timeout: float = 300.0,
        keep_data: bool = False,
        exposure_scale: float | Sequence[float] = 1.0,
    ) -> dict[str, Any]:
        """Run the scanner's shading calibration, as the vendor does at startup.

        This runs and returns data: 40 blocks, 1.66 MB, the scanner ending the
        pass itself. Whether it actually improves striping is UNVERIFIED -- the
        scan that would have measured it stalled before completing.

        What blocked this for four attempts was a PARAM call of my own: the
        vendor issues none during calibration, and mine was refused and
        evidently disturbed the pass. The width comes from the frame instead.
        The 0xe7 vendor command is not involved -- it is refused with ASC 0x20,
        and the vendor follows it immediately with REQUEST SENSE, so it is
        refused there too.

        Reconstructed from a capture taken from scanner power-on: this is the
        very first thing the vendor software does once the device is ready, and
        every later scan reuses the result (mode quality 0x0008, "reuse", versus
        0x0800 here, "calibrate now").

        Details that matter, all of which differ from an ordinary scan:

        * frame ``(0, 3431, 10343, 6888)`` -- the lower part of the transport
        * 3600 dpi, three channels, mode depth 8-bit
        * ``SLIDE INIT`` carries ``10 01 00 00`` here, not the 0x15/0x16 second
          byte seen elsewhere
        * calibration lines come back **16-bit regardless of the mode depth**:
          2 * width + 2 bytes, so 10346 at 3600 dpi. Sizing the read from the
          mode depth reads nothing at all, which is why an earlier attempt
          drained zero bytes.
        * the vendor alternates 4-line and 72-line reads, re-reading and
          re-writing gain/offset between them
        """
        for _ in range(4):
            try:
                if not self.read_state().warming_up:
                    break
            except (CheckCondition, ScanReadError):
                pass
            time.sleep(1)
        self.wait_warm()
        self.test_unit_ready()

        self.set_exposure_time()
        self.set_highlight_shadow()

        prep = bytearray(6)
        prep[0:2] = (SUB_CALIBRATION_INFO | 0x80).to_bytes(2, "little")
        prep[2:4] = (2).to_bytes(2, "little")
        try:
            self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(prep))
            info = self._query(_cmd(SCSI_READ, 128), 128, "calibration_info")
            self._log(f"calibration info: {info[:12].hex(' ')}")
        except (CheckCondition, ScanReadError) as exc:
            self._log(f"calibration info read failed ({exc}); continuing")

        self.set_scan_frame(*CALIBRATION_FRAME)
        try:
            self.cmd_17(1)
        except CheckCondition:
            pass
        # Calibrate at the exposure the scans will use. The reference
        # describes the sensor at one integration time and does not carry
        # across a large change in it: measured on a real frame, a channel
        # calibrated 3x below its scan exposure corrected 13.0% -> 1.4%, one
        # 6x below 8.2% -> 2.0%, and one 10x below got WORSE, 10.0% -> 11.2%.
        # The vendor writes 8277/28645/53160 immediately before this pass --
        # scanning exposures, not the power-on defaults.
        settings = self.get_gain_offset().scaled(exposure_scale)
        self.set_gain_offset(settings)
        if not _is_unity(exposure_scale):
            self._log(f"calibrating at {settings.describe()}")

        self.set_mode(
            resolution=3600,
            passes=ONE_PASS_COLOR,
            depth=DEPTH_8,
            color_format=FORMAT_INDEX,
            calibrate=True,
        )
        self.slide(SLIDE_INIT, param=0x01)
        self.wait_ready()

        started = time.monotonic()
        # Width comes from the frame, not from PARAM: the vendor issues no
        # PARAM at all during calibration, and calling it here is rejected and
        # may disturb the scan. Lines are 16-bit whatever the mode depth says.
        # Column count from the device's own descriptor. `pixels_per_line`
        # there is a BYTE count -- 10344 = 5172 columns x 16 bits -- which is
        # easy to read as columns and be exactly twice wrong. The frame-derived
        # value is the fallback, and the two are compared so a mismatch is
        # visible rather than silent.
        x0, _, x1, _ = CALIBRATION_FRAME
        width = round((x1 - x0) * 3600 / COORD_PER_INCH)
        try:
            parms = self.get_shading_parms()
        except (CheckCondition, ScanReadError) as exc:
            self._log(f"shading descriptor unreadable ({exc}); sizing from the frame")
        else:
            declared = [e["pixels_per_line"] // 2 for e in parms if e.get("pixels_per_line")]
            if declared:
                if declared[0] != width:
                    self._log(
                        f"note: descriptor says {declared[0]} columns, the frame "
                        f"implies {width}; using the descriptor"
                    )
                width = declared[0]
            self._log(
                f"shading descriptor: {len(parms)} entries, "
                f"{sum(e.get('lines', 0) for e in parms)} lines declared, "
                f"{width} columns"
            )
        bpl = 2 * width + INDEX_HEADER

        self.start_scan()
        drained = 0
        collected: list[bytes] = []
        try:
            self._log(f"calibration reads: {bpl} bytes/line (width {width})")

            # The vendor issues nothing at all for nine seconds after START
            # SCAN -- not a poll, literal silence -- then TEST UNIT READY and
            # its first read. Polling here instead appears to disturb the
            # calibration: doing so left every read refused with ASC 0x20.
            self._log("waiting 10s in silence, as the vendor does")
            time.sleep(10.0)
            self.test_unit_ready()

            # The vendor alternates 4-line and 72-line reads, but conditionally
            # -- after a 4-line read it sometimes takes 72 and sometimes not,
            # judging by the data. Since that condition is unknown, read in
            # small fixed blocks until the scanner refuses. A refusal (ASC 0x20)
            # is answered before any transfer and is harmless; asking for lines
            # that do not exist stalls the bulk transfer instead, and a bulk
            # timeout leaves the device needing a power cycle.
            deadline = time.monotonic() + timeout
            blocks = 0
            while time.monotonic() < deadline:
                try:
                    self.set_gain_offset(self.get_gain_offset())
                except (CheckCondition, ScanReadError):
                    pass
                try:
                    chunk = self.read_lines(4, bpl, retries=1)
                except NoDataYet:
                    time.sleep(0.05)
                    continue
                except (EndOfData, ScanReadError):
                    self._log(f"  scanner finished after {blocks} blocks")
                    break
                drained += len(chunk)
                # Always kept: the reference is built from these bytes, so
                # collecting only on request meant the default path parsed an
                # empty buffer and quietly produced no reference at all.
                # `keep_data` decides whether the caller also gets them back.
                collected.append(chunk)
                blocks += 1
                if blocks % 10 == 0:
                    self._log(f"  {blocks} blocks, {drained/1e6:.2f} MB")
            mask = self.get_ccd_mask(CCD_MASK_SIZE)
        finally:
            self.finish_scan()

        data = b"".join(collected)
        # The point of the pass. The scanner measured its per-column response
        # and handed it back; it does not apply it, so a calibration whose
        # result is discarded genuinely changes nothing in the image.
        self._shading = calculate_shading(data, width)
        if self._shading is None:
            self._log("calibration returned no usable shading lines")
        else:
            self._ccd_mask = mask
            self._log(
                f"shading reference: {width} columns, channels "
                f"{self._shading.channels}, means "
                f"{[round(self._shading.mean[c], 1) for c in self._shading.channels]}"
            )

        return {
            "shading_calibration": True,
            "data": data if keep_data else None,
            "reference": self._shading,
            "ccd_mask": mask,
            "bytes_per_line": bpl,
            "pixels_per_line": width,
            "bytes_drained": drained,
            "duration_s": round(time.monotonic() - started, 1),
        }

    # -- exposure ----------------------------------------------------------

    def auto_exposure(
        self,
        target: float = 0.7,
        percentile: float = 99.5,
        resolution: int = 300,
        infrared: bool = False,
        rounds: int = 2,
        tolerance: float = 0.08,
        start: Sequence[float] | None = None,
        film: str = FILM_NEGATIVE,
        infrared_blue_headroom: float = 4.0,
    ) -> list[float]:
        """Find per-channel exposure scales by probing at low resolution.

        Aims to put ``percentile`` of each channel at ``target`` of full scale
        -- high enough to use the range, with headroom so highlights do not
        clip. Returns scales to hand to :meth:`scan` as ``exposure_scale``.

        **Always probes in RGB, never in infrared**, and in at most two rounds:
        this is what the vendor software does. An infrared pass costs its own
        ~212 s floor however few lines are asked for, so metering in infrared
        would spend ten minutes to learn what a three-second pass can tell us.

        ``infrared`` therefore does not change how the probe is taken. It says
        the scan that follows will be RGBI, which matters only for blue: blue
        comes back brighter in an RGBI pass than in an RGB one at the *same*
        exposure -- measured 2.0x on one frame and about 3.7x on another -- so
        a blue metered to fill the range in RGB clips in RGBI. Blue's target is
        divided by ``infrared_blue_headroom`` to leave room for that.

        Costing blue some exposure is the right trade here, and the vendor
        makes it too: its own captures meter blue to 1475-5906 where green sits
        near 40000, an order of magnitude down, and reuse those values verbatim
        for the infrared scan. Blue on this scanner carries no fixed column
        pattern -- it is noise-limited, not detail-limited -- so a darker blue
        costs little, while a clipped blue is unrecoverable.

        ``film`` decides whether the visible channels are metered together or
        apart -- see :func:`locks_white_balance`. This matters: metering a slide
        per channel stretches each one to the same target and takes the cast
        off the picture. Infrared is always metered on its own, being no part
        of the colour balance.

        The channels differ enormously -- with no film in the transport blue
        saturates while red sits near a fifth of scale -- so a negative is
        metered per channel rather than with one global factor.
        """
        locked = locks_white_balance(film)
        # The probe is always three-channel; `infrared` describes the scan
        # that follows, not this pass.
        channels = 3
        scales = list(start) if start else [1.0] * channels
        limited = [False] * channels
        full = 65535.0

        # The exposure every scale is relative to, read once. :meth:`scan`
        # multiplies whatever the device currently holds, and SET GAIN OFFSET
        # persists, so re-reading it each round would compound the scales.
        base = self.get_gain_offset()
        self._log(
            f"auto-exposure: film={film}, "
            f"{'locked (one factor for R/G/B)' if locked else 'per channel'}"
        )

        for round_no in range(1, rounds + 1):
            self.set_gain_offset(base)
            image, _ = self.scan(
                resolution=resolution,
                infrared=False,
                exposure_scale=scales,
            )
            levels = [
                float(np.percentile(image[..., c], percentile)) / full
                for c in range(image.shape[2])
            ]
            self._log(
                f"auto-exposure round {round_no}: "
                + " ".join(
                    f"{'RGBI'[c]}={levels[c]:.0%}" for c in range(len(levels))
                )
            )
            # Blue's target is the one that moves: it comes back 2-3.7x
            # brighter in an RGBI pass than in the RGB probe at the same
            # exposure, so a blue metered to fill the range here clips there.
            targets = [target] * len(levels)
            if infrared and len(targets) > 2:
                targets[2] = target / max(1.0, infrared_blue_headroom)

            if all(abs(v - t) <= tolerance for v, t in zip(levels, targets)):
                break

            visible = levels[:3]
            for c, level in enumerate(levels):
                if c >= len(scales):
                    break
                # Locked: every visible channel moves by the one factor the
                # brightest of them needs, so none clips and the proportions --
                # the film's own cast -- survive. Infrared is metered alone.
                measured = max(visible) if (locked and c < 3) else level
                if measured <= 0.001:
                    scales[c] *= 4.0            # far too dark to measure
                elif measured >= 0.999:
                    scales[c] *= 0.25           # clipped; back well off
                else:
                    scales[c] *= targets[c] / measured
                # Bound by what the timer can actually hold, not by a
                # guess. A fixed cap of 8x used to stop blue short: with film
                # loaded the device's own blue exposure sits low (6506 in one
                # scan), leaving room for 10x, and the cap -- not the hardware
                # -- was what kept the blue record dark.
                ceiling = 65535 / base.exposure[c] if base.exposure[c] else 8.0
                if scales[c] > ceiling:
                    limited[c] = True
                scales[c] = max(0.01, min(ceiling, scales[c]))

        self._log(f"auto-exposure result: {[round(v, 3) for v in scales]}")

        # Exposure is a 16-bit timer count, and past full scale the firmware
        # wraps -- the pass comes out darker, not brighter. A channel that
        # wanted more than the timer holds was held at the ceiling and did not
        # reach the target; say so, because silently returning a scale that
        # could not be applied reads as a metering failure later.
        for c, was_limited in enumerate(limited):
            if was_limited and c < len(base.exposure):
                # The same guard the ceiling above carries. Without it this
                # message -- which exists only to explain the ceiling -- was the
                # one thing that could not survive the case it describes: the
                # scanner reported a zero exposure just after re-enumerating and
                # metering died here, inside the branch that reports the
                # problem, rather than in the arithmetic that handles it.
                room = (
                    f"{65535 / base.exposure[c]:.2f}x of exposure "
                    f"{base.exposure[c]}"
                    if base.exposure[c]
                    else "the device reported an exposure of 0, so the fallback "
                    "ceiling of 8x"
                )
                self._log(
                    f"  note: {'RGBI'[c]} is held at the timer ceiling "
                    f"({room} is all there is); this channel could "
                    f"not reach the target"
                )
        return scales

    # -- flat field --------------------------------------------------------

    def flat_field(
        self,
        resolution: int = 3600,
        infrared: bool = True,
        exposure_scale: float | Sequence[float] = 1.0,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Capture a flat field: a scan with nothing in the transport.

        With no film attenuating it, the lamp lights the sensor evenly, so the
        result is the response of the whole optical path -- per-element CCD
        gain, lamp falloff and vignetting together. Dividing a scan by this
        removes the fixed vertical striping.

        Watch for clipping: saturated columns all read the same value and hide
        the variation being measured, so :func:`flat_field_gain` rejects a flat
        that is clipped. Reduce ``exposure_scale`` if that happens.
        """
        image, meta = self.scan(
            resolution=resolution,
            infrared=infrared,
            exposure_scale=exposure_scale,
        )
        meta["flat_field"] = True
        return image, meta

    # -- orchestration -----------------------------------------------------

    # -- exposure brackets --------------------------------------------------

    MIN_BRACKET_PASSES = 2
    MAX_BRACKET_PASSES = 9

    def bracket_ladder(
        self,
        scales: Sequence[float],
        passes: int,
        stops: float = 2.0,
        base: Settings | None = None,
    ) -> list[float]:
        """Geometric multipliers for a bracket, topping out at the timer ceiling.

        ``scales`` is the metered per-channel exposure scale. Every pass
        multiplies all three channels by the *same* number, so the ratio between
        two passes is the same in every channel -- which is what lets the merge
        weight a pass with one exposure value instead of three.

        The top of the ladder is the largest multiplier that keeps every channel
        inside the 16-bit exposure timer. Past 65535 the timer wraps and the pass
        comes back darker, so a bracket that ignored this would not merely
        saturate, it would fold. The rest of the ladder steps down from there by
        ``stops``.

        Which channel binds depends on the film: green metered highest in every
        negative measured here, so green usually sets the ceiling.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )
        if stops <= 0:
            raise ValueError(f"stops must be positive, got {stops}")

        base = base or self.get_gain_offset()
        metered = [
            base.exposure[c] * (scales[c] if c < len(scales) else 1.0)
            for c in range(3)
        ]
        headroom = min(
            (65535.0 / e for e in metered if e > 0), default=1.0
        )
        if headroom < 1.0:
            # The metered exposure is already at the rail; the bracket can only
            # go down from here.
            headroom = 1.0
        top = headroom
        bottom = top / (2.0 ** stops)
        ladder = list(np.geomspace(bottom, top, passes))

        binding = min(range(3), key=lambda c: 65535.0 / metered[c] if metered[c] else 1e9)
        self._log(
            f"bracket ladder: {passes} passes over {stops:g} stops, "
            f"x{bottom:.3f} to x{top:.3f} of the metered exposure "
            f"({'RGB'[binding]} binds the ceiling)"
        )
        return ladder

    def scan_bracket(
        self,
        passes: int = 3,
        stops: float = 2.0,
        resolution: int = 300,
        infrared: bool = False,
        film: str = FILM_NEGATIVE,
        frame: tuple[int, int, int, int] | None = None,
        auto_exposure: bool = True,
        exposure_scale: Sequence[float] | None = None,
        keep_raw: bool = True,
        shading: bool = True,
        on_pass: Callable[[int, np.ndarray, dict[str, Any], dict[str, Any]], None]
        | None = None,
    ) -> tuple[list[np.ndarray], list[float], list[dict[str, Any]]]:
        """Scan one frame several times at different exposures.

        Returns ``(frames, ratios, metas)`` in ascending exposure order, ready
        for :func:`rps7200.bracket.merge_bracket`. The film is not advanced and
        the shading reference is acquired once for the whole bracket, so every
        pass describes the same frame through the same sensor state.

        Infrared is deliberately *not* bracketed. It costs its own ~212 s floor
        per pass however few lines are asked for, and its exposure is a device
        constant the vendor never meters, so bracketing it would multiply the
        scan time for nothing. With ``infrared`` set, one pass -- the brightest,
        which carries the most signal -- is taken as RGBI and the rest as RGB.

        ``on_pass(index, image, meta, capture)`` is called as each pass lands,
        with that pass's :meth:`capture_record`. It exists because only one
        pass's raw bytes survive on the scanner: ``last_raw`` is overwritten by
        the pass after it, so a caller that waits for the return value can file
        the last pass and no other. Do no heavy work in it -- the session is
        open and the next pass is about to start.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )

        if exposure_scale is not None:
            scales = list(exposure_scale)
        elif auto_exposure:
            scales = self.auto_exposure(film=film, infrared=infrared)
        else:
            scales = [1.0, 1.0, 1.0]

        ladder = self.bracket_ladder(scales, passes, stops)

        frames: list[np.ndarray] = []
        ratios: list[float] = []
        metas: list[dict[str, Any]] = []
        for i, k in enumerate(ladder):
            last = i == len(ladder) - 1
            pass_scale = [s * k for s in scales[:3]]
            self._log(
                f"bracket pass {i + 1}/{passes}: x{k:.3f} "
                f"({'RGBI' if (infrared and last) else 'RGB'})"
            )
            image, meta = self.scan(
                resolution=resolution,
                infrared=infrared and last,
                frame=frame,
                exposure_scale=pass_scale,
                keep_raw=keep_raw,
                shading=shading,
                film=film,
            )
            meta["bracket_index"] = i
            meta["bracket_ratio"] = float(k)
            meta["bracket_passes"] = passes
            meta["bracket_stops"] = float(stops)
            frames.append(image)
            ratios.append(float(k))
            metas.append(meta)
            if on_pass is not None:
                on_pass(i, image, meta, self.capture_record())
        return frames, ratios, metas

    def scan(
        self,
        resolution: int = 300,
        infrared: bool = True,
        depth: int = DEPTH_16,
        frame: tuple[int, int, int, int] | None = None,
        advance: bool = False,
        require_media: bool = True,
        exposure_scale: float | Sequence[float] = 1.0,
        auto_exposure: bool = False,
        exposure_target: float = 0.7,
        skip_shading: bool = True,
        shading: bool = True,
        film: str = FILM_NEGATIVE,
        keep_raw: bool = False,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run one scan and return ``(image, metadata)``.

        The command order here is the vendor software's, recovered from a USB
        capture. It is load-bearing: in particular :meth:`cmd_17` must follow
        the scan frame, or the scanner refuses to skip shading analysis and the
        scan cannot complete. See the README.

        ``film`` reaches auto-exposure, and only auto-exposure: it decides
        whether the visible channels are metered together or apart. Getting it
        wrong on a slide takes the cast off the picture -- see
        :func:`locks_white_balance`.

        ``shading`` applies this session's shading reference, which is what
        removes the vertical striping. The scanner measures its per-column
        response but returns raw pixels, so without a reference -- acquired by
        :meth:`calibrate_shading`, once per session, as the vendor does at
        power-on -- the stripes are simply left in.
        """
        if auto_exposure:
            # Probe in RGB whatever the scan will be, in at most two rounds --
            # the vendor's own sequence. Scans otherwise run at the scanner's
            # defaults, which land around 3-10% of full scale, most of the
            # 16-bit range unused.
            #
            # Blue does behave differently with infrared enabled, coming back
            # 2-3.7x brighter at the same exposure. That is handled by metering
            # blue lower when an RGBI scan follows, not by probing in RGBI: an
            # infrared probe costs its own ~212 s floor per round.
            self._log(f"auto-exposure: probing in RGB (scan is "
                      f"{'RGBI' if infrared else 'RGB'})")
            exposure_scale = self.auto_exposure(
                target=exposure_target, infrared=infrared, film=film
            )
            self._log(
                f"auto-exposure: {[round(v, 3) for v in exposure_scale]}"
            )

        # Open with READ_STATE polling, as the vendor software does.
        for _ in range(4):
            try:
                if not self.read_state().warming_up:
                    break
            except (CheckCondition, ScanReadError):
                pass
            time.sleep(1)
        self.wait_warm()
        self.test_unit_ready()

        if require_media:
            state = self.read_state()
            if not state.media_loaded:
                # Reported, not enforced: this bit has read clear with film
                # definitely loaded, so trusting it would block valid scans.
                # Let the scanner itself refuse if there is really no film.
                self._log(
                    f"note: state {state.scanning:#04x} suggests no film, but "
                    "that bit is not reliable; continuing"
                )

        self.set_exposure_time()
        self.set_highlight_shadow()

        if frame is None:
            frame = FULL_FRAME
        self.set_scan_frame(*frame)

        # Must come after the scan frame. Without it the scanner will not grant
        # "skip shading analysis" and insists on a shading pass it cannot serve.
        try:
            self.cmd_17(1)
        except CheckCondition:
            self._log("  cmd_17 reported a condition; continuing")

        settings = self.get_gain_offset().scaled(exposure_scale)
        self.set_gain_offset(settings, infrared=infrared)
        if not _is_unity(exposure_scale):
            shown = (
                f"x{exposure_scale:g}"
                if isinstance(exposure_scale, (int, float))
                else "x" + "/".join(f"{v:g}" for v in exposure_scale)
            )
            self._log(f"exposure scaled {shown}: {settings.describe()}")

        passes = ONE_PASS_RGBI if infrared else ONE_PASS_COLOR
        channels = 4 if infrared else 3
        self.set_mode(
            resolution=resolution,
            passes=passes,
            depth=depth,
            color_format=FORMAT_INDEX,
            skip_shading=skip_shading,
        )
        self.test_unit_ready()

        self.slide(SLIDE_INIT)
        self.wait_ready()

        started = time.monotonic()
        self.start_scan()
        try:
            self.wait_ready()
            # Read per pass, not once: the mask marks which CCD pixels *this*
            # pass samples, which is what keeps the shading columns aligned at
            # reduced resolutions.
            ccd_mask = self.get_ccd_mask(CCD_MASK_SIZE)
            # Kept for the caller: this pass's mask, not the calibration
            # pass's. They differ -- the mask says which CCD pixels *this*
            # resolution sampled -- so correcting a saved scan later needs
            # this one.
            self._ccd_mask = ccd_mask
            params = self.get_parameters()
            self._log(
                f"params width={params.width} lines={params.lines} "
                f"bpl={params.bytes_per_line}"
            )
            image = self.read_planes(params, channels, keep_raw=keep_raw)
        except BaseException:
            # Deliberately no STOP SCAN. The vendor software never sends it,
            # and issuing it here reliably leaves the scanner unresponsive to
            # the next session, needing a power cycle. Settling the bridge is
            # enough to leave things usable.
            self._scanning = False
            raise
        else:
            self.finish_scan()

        # After the scan has settled, never inside it: the vendor polls
        # READ_STATE for several seconds once the last line is read and only
        # then moves the film.
        if advance:
            self.advance()

        shading_report = None
        if (
            shading
            and self._shading is not None
            and params.width > self._shading.pixels_per_line
        ):
            # The mask holds one byte per calibration column, so a pass wider
            # than the calibration cannot be mapped -- at 7200 dpi the image is
            # 10344 columns against 5172 in the reference. Correcting half the
            # frame is worse than correcting none.
            self._log(
                f"shading skipped: this pass is {params.width} columns but the "
                f"reference covers {self._shading.pixels_per_line}; returning "
                f"raw pixels"
            )
        elif shading and self._shading is not None:
            image, shading_report = apply_shading(image, self._shading, ccd_mask)
            self._log(
                f"shading corrected: {shading_report['columns']}/"
                f"{shading_report['width']} columns"
                + (f", {shading_report['clipped']} samples clipped"
                   if shading_report["clipped"] else "")
            )
        elif shading:
            self._log(
                "no shading reference: returning raw pixels. Run "
                "calibrate_shading() once per session -- the scanner does not "
                "correct its own output"
            )

        meta = {
            "resolution_dpi": resolution,
            "channels": channels,
            "film": film,
            "protocol_revision": PROTOCOL_REVISION,
            "shading": shading_report,
            "channel_order": list(CHANNEL_ORDER[:channels]),
            "depth": 16 if depth == DEPTH_16 else 8,
            "frame": list(frame),
            "width": int(params.width),
            "height": int(params.lines),
            "bytes_per_line": int(params.bytes_per_line),
            # Read by get_parameters() and otherwise discarded. Recorded
            # because two passes at an identical frame and dpi have correlated
            # at lag -16 columns rather than lag 0, and these are the prime
            # suspect -- a suspicion that cannot be tested without the numbers.
            "filter_offsets": [int(params.filter_offset1), int(params.filter_offset2)],
            "exposure": settings.exposure,
            "gain": settings.gain,
            "offset": settings.offset,
            "exposure_scale": list(exposure_scale)
            if not isinstance(exposure_scale, (int, float))
            else exposure_scale,
            # Whether the exposure above was *metered* or *asked for*. The
            # library's signature() excludes a metered exposure deliberately --
            # it is an outcome that lands slightly differently every run without
            # changing what was requested. A commanded exposure is the opposite:
            # it is the request, and it is the only thing distinguishing the
            # members of a bracket, which are otherwise the same frame at the
            # same dpi, depth and channel count.
            "exposure_metered": bool(auto_exposure),
            "duration_s": round(time.monotonic() - started, 1),
        }
        return image, meta

    # -- rolls -------------------------------------------------------------

    def scan_roll(
        self,
        frames: int | None = None,
        resolution: int = 1800,
        infrared: bool = True,
        film: str = FILM_NEGATIVE,
        meter: str = METER_EACH,
        exposure_target: float = 0.7,
        prescan_resolution: int = 300,
        blank_contrast: float = BLANK_CONTRAST,
        drift_warning: int = 240,
        skip: int = 0,
        keep_raw: bool = True,
        max_failures: int = 3,
        scan_frame: tuple[int, int, int, int] | None = None,
        dry_run: bool = False,
    ) -> Iterator[RollFrame]:
        """Walk a roll or strip, yielding one :class:`RollFrame` per picture.

        A generator, not a list. At 3600 dpi a frame is ~142 MB of pixels plus
        its raw bytes, so the caller has to write each one out and let it go;
        collecting a roll in memory is not possible. It also means the caller
        can stop mid-roll, and that a frame reaches disk the moment it exists
        rather than at the end of a three-hour run.

        The first picture is scanned **before** any advance -- the film is
        already positioned at it when the roll starts. ``skip`` advances that
        many times first, which is how a part-scanned roll is resumed.

        Every frame is scanned at the full transport window. Cropping is a
        host-side decision that can be revisited; a window detected wrongly
        during an unattended run cannot.

        ``drift_warning`` is how much narrower than a whole frame a picture may
        measure before the log says the film has drifted -- 240 units is 0.85 mm,
        comfortably past detection jitter and well short of losing anything.

        Stops on whichever comes first: ``frames`` pictures, a prescan with no
        picture in it (:func:`frame_contrast` below ``blank_contrast``), an
        advance that does not move the film, or ``max_failures`` consecutive
        failures. A single failed frame does not end the roll -- it is yielded
        with ``error`` set and the roll goes on.

        ``meter`` is one of:

        ``"each"``
            re-meter before every frame, which is what CyberView does -- its
            gain/offset writes differ frame to frame.
        ``"once"``
            meter on the first picture and hold those scales for the roll. The
            frames stay comparable to each other, which matters when the whole
            roll is inverted with one set of parameters, and it saves ~45 s a
            frame.
        ``"none"``
            scan at whatever the device holds.
        """
        if meter not in METER_MODES:
            raise ValueError(
                f"unknown meter mode {meter!r}; expected one of {METER_MODES}"
            )
        # Up front, not on the first frame: a bad film type raises from inside
        # metering, and a roll would otherwise spend three failures discovering
        # a typo it could have refused in the first second.
        locks_white_balance(film)

        window = scan_frame or FULL_FRAME

        # The reference every frame is metered from. On this device READ
        # GAIN/OFFSET returns a fixed reference rather than a readback, so this
        # is the same value every frame anyway -- but reading it once and
        # writing it back explicitly is what makes that assumption checkable
        # instead of load-bearing and invisible.
        baseline = self.get_gain_offset()
        self._log(f"roll baseline exposure: {baseline.describe()}")

        scales: float | Sequence[float] = 1.0
        metered = False
        failures = 0
        index = 0

        for _ in range(skip):
            position = self.advance()
            if position is None:
                self._log("nothing to skip to: the transport did not move")
                return
            index += 1

        while frames is None or index < skip + frames:
            started = time.monotonic()
            prescan_image = None
            marks: dict[str, Any] = {}
            position = self.position()

            try:
                prescan_image, _ = self.prescan(resolution=prescan_resolution)
                contrast = frame_contrast(prescan_image)
                marks = dict(registration(prescan_image, window))
                marks["contrast"] = round(contrast, 4)
                self._log(
                    f"frame {index}: contrast {contrast:.3f}, "
                    f"picture x{marks['x0']}..{marks['x1']}, "
                    f"offset {marks['offset_mm']:+.2f} mm, "
                    f"short by {marks['shortfall_mm']:.2f} mm"
                )

                if contrast < blank_contrast:
                    self._log(
                        f"frame {index}: nothing in the window "
                        f"(contrast {contrast:.3f} < {blank_contrast}); "
                        "end of film"
                    )
                    return

                if marks["shortfall"] > drift_warning:
                    # Reported, never corrected here. Nothing in six captures
                    # moves the film by less than a whole frame, and
                    # SET_SCAN_HEAD is never sent by anything, so there is no
                    # verified way to nudge it back -- see
                    # tools/transport_probe.py. Saying so is better than a
                    # correction invented on the spot.
                    self._log(
                        f"frame {index}: picture is {marks['shortfall_mm']:.2f} mm "
                        "narrower than a whole frame -- the film has drifted and "
                        "part of it is outside the aperture"
                    )

                if dry_run:
                    yield RollFrame(index, position, None, {}, prescan_image, marks)
                else:
                    if meter != METER_NONE and not (meter == METER_ONCE and metered):
                        # `infrared` here says the scan that follows is RGBI;
                        # it does not make the probe infrared. auto_exposure
                        # always probes in RGB. What the flag buys is blue's
                        # headroom: blue comes back 2-3.7x brighter in RGBI than
                        # in RGB at the same exposure, so a blue metered to fill
                        # the range on an RGB probe clips in the scan. Passing
                        # False here cost exactly that -- one roll metered blue
                        # to 10.07x, which on a 6506 base pins the 16-bit timer
                        # at its 65535 ceiling before the RGBI gain is applied.
                        self.set_gain_offset(baseline, infrared=infrared)
                        scales = self.auto_exposure(
                            target=exposure_target, infrared=infrared, film=film
                        )
                        metered = True

                    # Correct whether or not the device echoes back what was
                    # written. It does not: across 17 READ GAIN/OFFSET
                    # responses in the strip capture only bytes 66-68 -- the
                    # live R/G/B offsets -- ever change, and the exposure fields
                    # hold 9604/6506/6506/7745 however different the value just
                    # written. So the read is a fixed reference, `scaled()`
                    # always yields base x scale, and exposure cannot compound
                    # frame to frame. One write costs nothing and keeps the roll
                    # right if that ever stops being true.
                    self.set_gain_offset(baseline, infrared=infrared)
                    image, meta = self.scan(
                        resolution=resolution,
                        infrared=infrared,
                        frame=window,
                        exposure_scale=scales,
                        film=film,
                        keep_raw=keep_raw,
                    )
                    meta["roll_index"] = index
                    meta["roll_position"] = position
                    meta["registration"] = marks
                    yield RollFrame(index, position, image, meta, prescan_image, marks)
                failures = 0
            # UsbError covers CheckCondition and NoDataYet. ValueError is in
            # here because a roll runs for hours unattended: one frame that
            # decodes to an unexpected shape should cost that frame, not the
            # thirty after it.
            except (UsbError, ScanReadError, CalibrationRequired,
                    TimeoutError, ValueError) as exc:
                failures += 1
                self._log(f"frame {index} failed ({failures}/{max_failures}): {exc}")
                yield RollFrame(
                    index, position, None, {}, prescan_image, marks, error=str(exc)
                )
                if failures >= max_failures:
                    self._log(f"giving up after {failures} consecutive failures")
                    return

            self._log(f"frame {index} took {time.monotonic() - started:.0f}s")
            index += 1
            if frames is not None and index >= skip + frames:
                break
            if self.advance() is None:
                return
