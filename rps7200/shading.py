"""Host-side flat-field (shading) correction.

The scanner returns **raw, uncorrected pixels**. It measures its own per-column
sensor response during a calibration pass and hands that measurement back, but
it never applies it: the correction is the host's job. Running the calibration
and discarding the result therefore changes nothing in the image, which is
exactly what was observed here for a long time -- a 1.66 MB reference read from
the device on every session and thrown away, while the vertical stripes it
describes stayed in every scan.

The correction is one division per column, per channel:

    value = shading_mean[c] / shading_ref[c][width_to_loc[j]] * value

This is why a flat captured through film could never replace it. The reference
is measured by the scanner, on its own calibration strip, *at the exposure and
gain the pass will use* -- and the strength of a column defect scales with
exposure, which is why the same defect measured 4.7% in one flat and 9.6% in a
scan taken at other settings. A reference imported from a different pass is
describing a different sensor state.

Follows the SANE C backend: pieusb_calculate_shading (pieusb_specific.c:1681)
and sanei_pieusb_correct_shading (pieusb_specific.c:1207).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# First byte of a shading line's two-byte header -> channel index. The tag is
# emitted duplicated ('RR', 'GG', ...) and only byte 0 is keyed off. Same
# INDEX-format header as the image data.
TAG_TO_CHANNEL = {0x52: 0, 0x47: 1, 0x42: 2, 0x49: 3}

# A CCD mask byte of 0x00 marks a pixel this pass USES; 0x70 marks one it does
# not. The mask is read per pass, so at reduced resolution it marks the subset
# actually sampled -- which is what keeps the mapping correct at any dpi.
MASK_USED = 0x00


@dataclass(frozen=True)
class ShadingReference:
    """The sensor's per-column response, ready to correct with.

    Outlives a single scan but not the session: it describes the CCD under one
    calibration, so it is cached on the Scanner exactly as the C backend caches
    it on the open handle (pieusb_specific.h:292-294).

    ``ref`` is the **light** reference -- the lit path -- and ``dark`` the
    unlit one, which the device sends in the same pass. ``dark`` may be empty:
    the correction falls back to a single-point division then, as the SANE C
    backend does throughout.

    ``pixels_per_line`` is the CCD-native width it was read at, kept so a
    reference cannot be applied to a pass the device sized differently.
    """

    ref: dict[int, np.ndarray]
    mean: dict[int, float]
    pixels_per_line: int
    dark: dict[int, np.ndarray] = field(default_factory=dict)
    dark_mean: dict[int, float] = field(default_factory=dict)

    @property
    def channels(self) -> list[int]:
        return sorted(self.ref)

    @property
    def two_point(self) -> bool:
        return bool(self.dark)

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path,
            pixels_per_line=self.pixels_per_line,
            channels=np.array(self.channels),
            dark_channels=np.array(sorted(self.dark), dtype=np.int64),
            **{f"ref{c}": self.ref[c] for c in self.channels},
            **{f"mean{c}": np.float64(self.mean[c]) for c in self.channels},
            **{f"dark{c}": self.dark[c] for c in sorted(self.dark)},
            **{f"darkmean{c}": np.float64(self.dark_mean[c]) for c in sorted(self.dark)},
        )

    @classmethod
    def load(cls, path: str | Path) -> "ShadingReference":
        with np.load(path) as z:
            channels = [int(c) for c in z["channels"]]
            dark_channels = (
                [int(c) for c in z["dark_channels"]] if "dark_channels" in z else []
            )
            return cls(
                ref={c: z[f"ref{c}"] for c in channels},
                mean={c: float(z[f"mean{c}"]) for c in channels},
                pixels_per_line=int(z["pixels_per_line"]),
                dark={c: z[f"dark{c}"] for c in dark_channels},
                dark_mean={c: float(z[f"darkmean{c}"]) for c in dark_channels},
            )


def calculate_shading(
    data: bytes, pixels_per_line: int, split_ratio: float = 5.0
) -> ShadingReference | None:
    """Parse the calibration block into a dark and a light per-column reference.

    Calibration lines are **always 16-bit little-endian regardless of the mode
    depth**, carrying the same two-byte channel tag as image data. Sizing the
    read from an 8-bit mode reads nothing at all.

    The pass returns two phases, unlit first and lit second, interleaved by
    channel throughout. Measured on this scanner: the dark lines average around
    170 counts and the light ones around 47,000, so the two are separated by a
    factor of roughly 250. They are split **by level rather than by counting
    lines**, because the device's own descriptor declares 4 x 20 lines while
    around 160 arrive -- one declaration for two phases -- so any split derived
    from the declared counts would be wrong.

    `pieusb`'s `calculate_shading` averages every line sharing a tag, blending
    the two into one useless reference. That is worth knowing about but not
    copying; the dark half matters here, varying 12-15% column to column
    against the light half's 0.8%.

    ``split_ratio`` is the smallest max/min ratio of line levels that counts as
    two phases. Below it every line is treated as light and the correction
    falls back to a single point.
    """
    stride = 2 + pixels_per_line * 2
    if stride <= 2 or len(data) < stride:
        return None

    # collect the lines per channel first; the split needs their levels
    lines: dict[int, list[np.ndarray]] = {}
    for k in range(len(data) // stride):
        off = k * stride
        channel = TAG_TO_CHANNEL.get(data[off])
        if channel is None:
            continue
        lines.setdefault(channel, []).append(
            np.frombuffer(data, dtype="<u2", count=pixels_per_line, offset=off + 2)
            .astype(np.float64)
        )
    if not lines:
        return None

    ref: dict[int, np.ndarray] = {}
    mean: dict[int, float] = {}
    dark: dict[int, np.ndarray] = {}
    dark_mean: dict[int, float] = {}

    for channel, rows in lines.items():
        levels = np.array([r.mean() for r in rows])
        lo, hi = levels.min(), max(levels.max(), 1e-9)
        if lo > 0 and hi / lo >= split_ratio:
            # Two populations. Cut at the widest ratio gap between consecutive
            # levels rather than at a fixed threshold, so the boundary is the
            # data's own and not a guess about exposure.
            order = np.argsort(levels)
            ranked = levels[order]
            gaps = ranked[1:] / np.maximum(ranked[:-1], 1e-9)
            cut = ranked[int(np.argmax(gaps))]
            is_dark = levels <= cut
            if is_dark.any() and not is_dark.all():
                stack = np.stack(rows)
                dark[channel] = stack[is_dark].mean(axis=0)
                dark_mean[channel] = float(dark[channel].mean())
                ref[channel] = stack[~is_dark].mean(axis=0)
                mean[channel] = float(ref[channel].mean())
                continue
        ref[channel] = np.mean(np.stack(rows), axis=0)
        mean[channel] = float(ref[channel].mean())

    if not ref:
        return None
    return ShadingReference(ref, mean, pixels_per_line, dark, dark_mean)


def build_width_to_loc(ccd_mask: bytes, width: int) -> np.ndarray:
    """Map output column j to its column in the shading reference.

    The reference spans the whole CCD, including pixels this pass does not
    read, so the two cannot be matched by index. The j-th *used* pixel in the
    mask is the reference column for output column j.
    """
    locs = np.flatnonzero(np.frombuffer(ccd_mask, dtype=np.uint8) == MASK_USED)
    return locs[:width]


def apply_shading(
    image: np.ndarray,
    reference: ShadingReference,
    ccd_mask: bytes | np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, int]]:
    """Flat-field an ``(H, W, C)`` image from a reference. Returns (image, report).

    Two-point where the calibration gave both phases::

        value = (raw - dark[c][j]) * (mean_light[c] - mean_dark[c])
                                   / (light[c][j] - dark[c][j])

    and the single-point form the SANE C backend uses where it did not::

        value = raw * mean_light[c] / light[c][j]

    The dark half is worth the extra term: it varies 12-15% column to column
    against the light half's 0.8%, and it is an *offset*, so it dominates
    exactly where a negative is densest and the signal is smallest.

    ``j`` is not the output column. The reference spans the whole CCD including
    pixels this pass did not read, so the mask maps them -- see
    :func:`build_width_to_loc`. Passing no mask matches columns one to one,
    which is only right when the pass read every CCD pixel.

    Over-range results are clamped rather than wrapped, and counted: the gain
    exceeds 1 wherever the lamp falls off, so edge columns reach the ceiling at
    a lower raw value than centre ones, and heavy clipping re-introduces
    banding in the highlights. Lower the exposure if it does.
    """
    h, w, nc = image.shape
    if ccd_mask is None:
        loc = np.arange(min(w, reference.pixels_per_line), dtype=np.intp)
    else:
        loc = build_width_to_loc(bytes(ccd_mask), w)

    out = image.copy()
    maxval = np.iinfo(image.dtype).max if np.issubdtype(image.dtype, np.integer) else None
    report = {
        "columns": int(loc.size), "width": w, "clipped": 0, "uncorrected": 0,
        "two_point": int(reference.two_point),
    }

    for c in range(nc):
        if c not in reference.ref:
            report["uncorrected"] += 1
            continue
        light = reference.ref[c][loc]
        if c in reference.dark:
            dark = reference.dark[c][loc]
            span = light - dark
            gain = np.where(span > 0, (reference.mean[c] - reference.dark_mean[c]) / np.where(span > 0, span, 1.0), 1.0)
            vals = (image[:, : loc.size, c].astype(np.float64) - dark) * gain
        else:
            gain = np.where(light > 0, reference.mean[c] / np.where(light > 0, light, 1.0), 1.0)
            vals = image[:, : loc.size, c].astype(np.float64) * gain

        if maxval is not None:
            # floor(x + 0.5) reproduces the C's lround(); np.rint would not,
            # rounding halves to even.
            np.floor(vals + 0.5, out=vals)
            report["clipped"] += int(np.count_nonzero(vals > maxval))
            np.clip(vals, 0, maxval, out=vals)
        out[:, : loc.size, c] = vals.astype(image.dtype)

    return out, report
