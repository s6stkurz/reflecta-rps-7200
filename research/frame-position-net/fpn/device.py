"""The device's last steps: sensor counts, 8-bit raw, and the driver's own correction.

A synthetic prescan is made in *corrected counts*: what a perfectly flat
sensor would deliver at the device's own prescan exposure (`fpn.film`). This
turns that into what `scan()` really hands the edge reader:

1. back through a real calibration's shading, column by column, to 8-bit raw;
2. sensor noise, then rounding and clipping to uint8, as the device returns it;
3. `library.correct` with that same calibration -- the driver's own code, so
   the comb-shaped histograms and the noise that rises towards the ends of the
   aperture are the real ones, not a model of them.

The calibrations come from real 300 dpi uint8 prescan entries (`ReferencePool`).
Only their references and masks are used, never their pixels.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rps7200 import library
from rps7200.shading import apply_shading

from fpn.inputs import COLUMNS

#: Sensor noise in raw 8-bit counts, sigma^2 = READ^2 + SHOT * level. Small
#: beside film grain: the empty gate, ~182 counts, holds to 0.4%.
READ_NOISE = 0.35
SHOT_NOISE = 0.002


@dataclass
class Calibration:
    entry: str
    record: dict[str, Any]
    gain: np.ndarray    # (3, 428): corrected = (raw - dark) * gain
    dark: np.ndarray    # (3, 428), in 8-bit raw counts


def _affine(record: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """The correction's gain and dark per column for an 8-bit, 428-wide pass.

    Read off `apply_shading` itself with two float probes, so nothing of its
    formula is restated here. A float image is corrected on the reference's own
    16-bit scale; an 8-bit pass on that scale times 255/65535, which moves the
    dark term and leaves the gain (a ratio) alone -- `apply_shading`'s docstring.
    """
    probe = 1000.0
    zero = np.zeros((1, COLUMNS, 3), dtype=np.float64)
    out0, _ = apply_shading(zero, record["reference"], record["ccd_mask"])
    out1, _ = apply_shading(zero + probe, record["reference"], record["ccd_mask"])
    gain = (out1[0] - out0[0]).T / probe                 # (3, 428)
    dark16 = -out0[0].T / np.where(gain > 0, gain, 1.0)
    return gain.astype(np.float64), dark16 * (255.0 / 65535.0)


class ReferencePool:
    """Distinct calibrations, from real raw uint8 300 dpi entries."""

    def __init__(self, entries: list[Path]):
        self.calibrations: list[Calibration] = []
        seen: set[str] = set()
        for path in entries:
            image, record = library.load(path)
            if image.dtype != np.uint8 or image.shape[1] != COLUMNS:
                continue
            if record.get("reference") is None or record.get("ccd_mask") is None:
                continue
            if (record.get("image") or {}).get("corrections_applied"):
                continue
            key = hashlib.sha256((path / "shading.npz").read_bytes()
                                 if (path / "shading.npz").exists() else path.name.encode()
                                 ).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            gain, dark = _affine(record)
            self.calibrations.append(Calibration(path.name, record, gain, dark))
        if not self.calibrations:
            raise ValueError("no usable calibration among the entries given")

    def pick(self, rng: np.random.Generator) -> Calibration:
        return self.calibrations[int(rng.integers(len(self.calibrations)))]

    def by_entry(self, entry: str) -> Calibration:
        for cal in self.calibrations:
            if cal.entry == entry:
                return cal
        raise KeyError(f"no calibration from {entry!r} in this pool")


def deliver(corrected_counts: np.ndarray, cal: Calibration,
            rng: np.random.Generator) -> np.ndarray:
    """(rows, 428, 3) float corrected counts -> the uint8 prescan `scan()` returns."""
    gain = cal.gain.T[None]          # (1, 428, 3)
    dark = cal.dark.T[None]
    raw = corrected_counts / np.where(gain > 0, gain, 1.0) + dark
    sigma = np.sqrt(READ_NOISE ** 2 + SHOT_NOISE * np.clip(raw, 0, None))
    raw = raw + rng.standard_normal(raw.shape) * sigma
    raw8 = np.clip(np.floor(raw + 0.5), 0, 255).astype(np.uint8)
    record = dict(cal.record)
    out, record = library.correct(raw8, record)
    if record.get("corrected") != "applied":
        raise RuntimeError(f"{cal.entry}: correction not applied ({record.get('corrected')})")
    return out
