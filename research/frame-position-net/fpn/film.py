"""Film: from a positive picture in linear light to transmittance, per film type.

Everything is transmittance T relative to the empty gate (T = 1: no film), per
channel R, G, B. `GATE_COUNTS` turns it into corrected 8-bit counts at the
device's own prescan exposure, which `fpn.device` then delivers. A prescan is
not metered, so the levels are absolute: a colour negative reads under 30% of
the scale, black and white under 10%.

Three films, all read from a positive `L` (the lab's picture, linear light):

- **slide**: T = Tmin + (Tmax - Tmin) * L^kappa. Unexposed slide film is black,
  so the gaps between frames are Tmin.
- **c41**: a colour negative. Each layer forms dye with exposure through a toe,
  D_layer = gamma * softplus(log10 E - log10 E0); the base carries the orange
  mask; and where dye forms the mask is partly consumed, so the cross terms in
  `C41.cross` may be negative. That is why 1-7% of the picture is brighter than
  base in some channel (frame-edge REPORT).
- **bw**: one neutral density from the luminance, on a neutral base, with grain
  shared by all three channels (channel correlation 0.93-0.99).

The defaults below are the numbers measured on real prescans, each with where
it came from. For C-41 they are the starting point `calibrate.py` fits from;
for B&W and slides they are the model (Stefan, 2026-09-30): the only real
frames of either are their test sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

#: Corrected 8-bit counts of the empty gate at prescan exposure: 181-186 in
#: every channel (r0914_21 left, r0909_13 right; prescan-geometry survey).
GATE_COUNTS = 182.0
LN10 = np.log(10.0)


def softplus(x: np.ndarray, sharpness: float) -> np.ndarray:
    return np.logaddexp(0.0, sharpness * x) / sharpness


@dataclass(frozen=True)
class Slide:
    #: Clear-film transmittance and contrast. A slide prescan's picture median
    #: is about 14/10/14 counts (x0920, by analogy with their exposure).
    tmax: tuple[float, float, float] = (0.62, 0.60, 0.62)
    kappa: float = 1.15
    #: The unexposed gap against the picture's p99.5: 1.1-1.3% / 0.75% / 2.4-2.7%,
    #: bluish (x0920_01..09, exposure 1.0).
    tmin_share: tuple[float, float, float] = (0.012, 0.0075, 0.025)
    #: Random noise, relative: 1.7-1.9% on the x0920 repeat pair.
    grain: float = 0.018


@dataclass(frozen=True)
class C41:
    #: Base in counts: R 35-68, G 15-30, B 8-17; R/G 2.04-2.30, B/G 0.53-0.64
    #: (12 labelled frames; chroma.py C41_RG/C41_BG).
    base_red: tuple[float, float] = (35.0, 68.0)
    red_over_green: tuple[float, float] = (2.04, 2.30)
    blue_over_green: tuple[float, float] = (0.53, 0.64)
    #: Dye per layer: density per decade of exposure, and the toe.
    gamma: tuple[float, float, float] = (0.55, 0.60, 0.62)
    toe_sharpness: float = 4.0
    #: Exposure E0 of the toe, relative to the picture's 99th percentile.
    toe: float = 1.0 / 90.0
    #: Net cross terms after masking: d(G) from the red layer, d(B) from green
    #: and from red. Negative where the mask is consumed faster than the dye
    #: absorbs.
    cross: tuple[float, float, float] = (-0.05, -0.08, -0.03)
    #: Grain, sigma of density per pixel. Base reads 3% / 4.2% / 5.7% SD in all,
    #: sensor and rounding included (chroma.py:20-22).
    grain: tuple[float, float, float] = (0.011, 0.015, 0.020)


@dataclass(frozen=True)
class BW:
    #: Base 19 / 18 / 21 counts, R/G 1.06, B/G 1.17 (bw0910).
    base: tuple[float, float, float] = (19.0, 18.0, 21.0)
    base_spread: tuple[float, float] = (0.8, 1.25)
    gamma: float = 0.62
    toe_sharpness: float = 4.0
    toe: float = 1.0 / 60.0
    #: Grain 7-10% per pixel, common to all channels (gapmodel.py:156, mono.py).
    grain: float = 0.033
    grain_own: float = 0.006


@dataclass(frozen=True)
class FilmParams:
    slide: Slide = field(default_factory=Slide)
    c41: C41 = field(default_factory=C41)
    bw: BW = field(default_factory=BW)


@dataclass(frozen=True)
class Roll:
    """What one strip shares: the film, its base, and the exposure scale."""
    film: str                                   # "slide", "c41", "bw"
    base_t: tuple[float, float, float]          # unexposed film, transmittance
    exposure_stops: float                       # the roll's exposure offset


def draw_roll(film: str, params: FilmParams, rng: np.random.Generator) -> Roll:
    if film == "c41":
        p = params.c41
        red = rng.uniform(*p.base_red)
        green = red / rng.uniform(*p.red_over_green)
        blue = green * rng.uniform(*p.blue_over_green)
        base = (red / GATE_COUNTS, green / GATE_COUNTS, blue / GATE_COUNTS)
    elif film == "bw":
        b = params.bw
        s = rng.uniform(*b.base_spread)
        base = (b.base[0] * s / GATE_COUNTS, b.base[1] * s / GATE_COUNTS,
                b.base[2] * s / GATE_COUNTS)
    elif film == "slide":
        sl = params.slide
        base = (sl.tmax[0] * sl.tmin_share[0], sl.tmax[1] * sl.tmin_share[1],
                sl.tmax[2] * sl.tmin_share[2])
    else:
        raise ValueError(f"film {film!r}")
    return Roll(film, base, float(rng.normal(0.0, 0.5)))


def normalise(linear: np.ndarray) -> np.ndarray:
    """A picture's light relative to its own 99th percentile: 1 is a highlight."""
    return linear / (np.percentile(linear, 99.0) + 1e-6)


def film_curve(exposure: np.ndarray, roll: Roll, params: FilmParams) -> np.ndarray:
    """Transmittance from ``exposure`` (rows, cols, 3): the light that reached the
    film, each picture `normalise`d and scaled by its exposure, summed over the
    frames in view (the gate's soft shadow already applied). 0 is unexposed film."""
    e = np.clip(exposure * 2.0 ** roll.exposure_stops, 0.0, None)
    if roll.film == "slide":
        sl = params.slide
        tmin = np.asarray(roll.base_t)
        tmax = np.asarray(sl.tmax)
        return tmin + (tmax - tmin) * np.clip(e, 0.0, 1.0) ** sl.kappa
    if roll.film == "c41":
        p = params.c41
        d = np.asarray(p.gamma) * softplus(np.log10(e + 1e-9) - np.log10(p.toe), p.toe_sharpness)
        dr, dg, db = d[..., 0], d[..., 1], d[..., 2]
        k_gr, k_bg, k_br = p.cross
        dens = np.stack([dr, dg + k_gr * dr, db + k_bg * dg + k_br * dr], axis=-1)
        return np.asarray(roll.base_t) * 10.0 ** (-dens)
    if roll.film == "bw":
        b = params.bw
        y = e @ np.array([0.2126, 0.7152, 0.0722])
        d = b.gamma * softplus(np.log10(y + 1e-9) - np.log10(b.toe), b.toe_sharpness)
        return np.asarray(roll.base_t) * 10.0 ** (-d[..., None])
    raise ValueError(f"film {roll.film!r}")


def grain(t: np.ndarray, roll: Roll, params: FilmParams,
          rng: np.random.Generator, film_mask: np.ndarray) -> np.ndarray:
    """Per-pixel grain on transmittance, only where there is film."""
    shape = t.shape
    if roll.film == "slide":
        noise = params.slide.grain * rng.standard_normal(shape)
        out = t * (1.0 + noise)
    elif roll.film == "c41":
        sigma = np.asarray(params.c41.grain)
        out = t * 10.0 ** (-sigma * rng.standard_normal(shape))
    else:
        b = params.bw
        common = rng.standard_normal(shape[:2])[..., None]
        own = rng.standard_normal(shape)
        out = t * 10.0 ** (-(b.grain * common + b.grain_own * own))
    return np.where(film_mask[..., None], out, t)


def with_overrides(params: FilmParams, c41: dict | None = None) -> FilmParams:
    """The defaults with a calibration's C-41 fit applied."""
    if not c41:
        return params
    return replace(params, c41=replace(params.c41, **{k: tuple(v) if isinstance(v, list) else v
                                                       for k, v in c41.items()}))
