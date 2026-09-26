"""Register passes of one frame onto each other, to a fraction of a pixel.

Two passes of the same frame are not pixel-aligned on this scanner. The
carriage re-homes before every pass and does not land in the same place twice:
six passes of one frame registered at 0, 0, -1, -2, -2, -3 lines (TODO.md), and
the nine-pass 3600 dpi bracket in the library walks 2.4 lines and 0.7 columns
from its first pass to its last. Any method that combines passes pixel by pixel
-- a bracket merge, a stack of repeats -- has to undo that first.

It went unnoticed for a whole study. The bracket's passes were compared
unregistered, their disagreement grew along the ladder, and because the ladder
is always shot in ascending order the drift read as an exposure effect: "passes
stop agreeing as the bracket widens". Registered, the last pass agrees with the
first at a median |z| of 1.27 where unregistered it read 5.71, against 1.03 for
a repeat pair. The pass at x4 from a *three*-pass run sits 0.1 line from its
first pass; it is how many passes came before, not the exposure.

Two decisions carry the design:

- **Sub-pixel, by evaluating the phase-correlation spectrum between its
  samples** (:func:`register_subpixel`), not by fitting a parabola to the
  sampled peak, which biases toward whole pixels. The integer peak comes from
  the same kind of surface :func:`rps7200.uniformity.register` searches.
- **Shift by the Fourier theorem, never by interpolation** (:func:`shift_image`).
  A phase ramp moves every frequency and attenuates none, so a shifted pass
  keeps exactly the noise it had. Bilinear or cubic resampling smooths noise,
  and a stack of smoothed passes then reads as a noise reduction the stacking
  did not earn.

The shift is rigid: one (dy, dx) per pass. :func:`band_residuals` measures what
that leaves along the frame, which is how to tell whether a warp that varies
down the frame is ever needed.

The approach -- register each pass to the first, independently, and let a
phase-correlation peak decide -- is pyopticfilm's `pass_align.py`
(https://github.com/jboneng/pyopticfilm, GPL-3.0-or-later, as is this
project). It uses OpenCV; this is numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from bracket import CLIP_START, SNR_FLOOR

#: Furthest a pass is searched from its reference, in pixels, either axis. The
#: largest pass-to-pass offset recorded here is 16 columns at 3600 dpi; this
#: leaves room for the 20-40 px pyopticfilm measured on its own hardware
#: without letting a distant repeat in the picture outrank the true peak.
SEARCH_PX = 64

#: Registration reads a central window at most this big on a side. A rigid
#: shift is the same everywhere, and a 3600 dpi frame at full size would want
#: several 300 MB spectra for no better answer.
WINDOW_PX = 2048

#: Side of the box whose mean is taken off the log plane. Removes the level --
#: the only thing that differs between two exposures of one picture -- and keeps
#: grain and edges, which are what lock the peak.
DETAIL_BOX_PX = 31

#: Upsampling of the spectrum's inverse around the integer peak. 1/20 px steps,
#: refined by a parabola through the three best, reach about 0.01 px.
UPSAMPLE = 20

#: Below this peak z-score the match is refused. Same statistic as
#: `uniformity.register`'s confidence, and like it only meaningful at a fixed
#: reach -- here always `SEARCH_PX`. Two unrelated noise frames score 4-6, the
#: expected maximum of that many samples; every real pass pair in the library
#: scores far above it.
CONFIDENCE_FLOOR = 12.0

#: Rows and columns next to the edge that stay invalid beyond the shift itself.
#: A Fourier shift is an exact interpolation, and an exact interpolator reaches
#: past the frame's edge, where the mirror it is given is only a guess; the
#: error falls off only as the distance grows. Measured on a real 3600 dpi pass
#: whose edge is the film's hard border (3000 DN beside 54000), shifted 2.45
#: lines and back: 99.9th-percentile error 161 DN 16 px in, 111 at 32, 71 at 64,
#: against a median of 4 -- and one shift is about half a round trip. Inside
#: this margin a merged frame speaks with the reference's voice alone.
EDGE_MARGIN_PX = 32


def cross_power(fa: np.ndarray, fb: np.ndarray, *, drop_axes: bool = False) -> np.ndarray:
    """The normalised cross-power spectrum of two equal-sized planes.

    Half-spectrum (``rfft2`` layout), in the sign convention of
    `rps7200.uniformity.register`, whose FFT core this repeats. A sub-pixel
    peak is found by evaluating this spectrum's inverse at fractional
    positions, which a sampled surface cannot give.

    ``drop_axes`` zeroes the spatial frequencies within one bin of either axis
    before normalising. Two passes through one sensor share its residual
    column pattern -- constant down the frame, so all of its energy sits on
    the ``ky = 0`` line (and ``ky = +-1`` once the window has spread it) -- and
    that shared pattern is a copy of itself at zero shift, which pulls the peak
    toward ``dx = 0`` whatever the film did.
    """
    h, w = fa.shape
    fa = fa - fa.mean()
    fb = fb - fb.mean()
    # Hann window: the FFT treats the frame as periodic, so an un-windowed
    # frame's opposite edges act like a hard seam and can outrank the target.
    win = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    cross = np.fft.rfft2(fa * win) * np.conj(np.fft.rfft2(fb * win))
    if drop_axes:
        cross[[0, 1, -1], :] = 0.0             # ky = 0, +-1: column pattern
        cross[:, :2] = 0.0                     # kx = 0, 1: row pattern
    mag = np.abs(cross)
    return cross / np.where(mag > 0, mag, 1.0)


@dataclass(frozen=True)
class PassShift:
    """Where a pass sits relative to its reference.

    ``ref[i, j]`` and ``mov[i - dy, j - dx]`` look at the same place on the
    film -- the convention of :func:`rps7200.uniformity.register`, so an integer
    answer here can be checked against it directly. ``reason`` is set when the
    match was refused, and then ``dy`` and ``dx`` are zero and mean nothing.
    """

    dy: float
    dx: float
    confidence: float
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None


def _box_mean(plane: np.ndarray, k: int) -> np.ndarray:
    """Mean over a k x k box, edges padded by replication."""
    r = k // 2
    p = np.pad(plane, ((r + 1, r), (r + 1, r)), mode="edge")
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)


def registration_plane(image: np.ndarray) -> np.ndarray:
    """The detail two passes share, whatever their exposures.

    Green, because it is neither the channel that reaches the rail first (red,
    in every bracket here) nor the noisiest (blue). Its log, so a pass at x4 is
    the same picture plus a constant; minus a local mean, so that constant goes.
    Clipped samples and samples in the noise carry no picture and are left at
    zero rather than turned into edges.
    """
    a = np.asarray(image, dtype=np.float64)
    g = a if a.ndim == 2 else a[..., 1]
    usable = (g > SNR_FLOOR) & (g < CLIP_START)
    log = np.where(usable, np.log(np.maximum(g, SNR_FLOOR)), 0.0)
    m = usable.astype(np.float64)
    local = _box_mean(log * m, DETAIL_BOX_PX) / np.maximum(_box_mean(m, DETAIL_BOX_PX), 1e-9)
    return np.where(usable, log - local, 0.0)


def _central(image: np.ndarray, rows: slice | None = None) -> np.ndarray:
    """The central `WINDOW_PX` columns of `rows` (default: the central rows)."""
    h, w = image.shape[:2]
    if rows is None:
        y0 = max(0, (h - WINDOW_PX) // 2)
        rows = slice(y0, y0 + min(h, WINDOW_PX))
    x0 = max(0, (w - WINDOW_PX) // 2)
    return image[rows, x0 : x0 + min(w, WINDOW_PX)]


def _plane(image: np.ndarray, rows: slice | None = None) -> np.ndarray:
    # cropped before the plane is built: the box mean only reaches half a box
    # past the crop, and the window has already faded the crop's edges out
    return registration_plane(_central(np.asarray(image), rows))


def _evaluate(cross: np.ndarray, shape: tuple[int, int], ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """The phase surface at arbitrary (fractional) positions.

    The inverse transform of the half-spectrum written out as two matrix
    products, so it can be sampled between pixels -- at integer positions it is
    exactly what `irfft2` returns, up to scale.
    """
    h, w = shape
    ky = np.fft.fftfreq(h)
    kx = np.fft.rfftfreq(w)
    # the half-spectrum stands for its conjugate twin too, except at kx = 0 and
    # (for even widths) the Nyquist column, which have none
    twice = np.full(kx.size, 2.0)
    twice[0] = 1.0
    if w % 2 == 0:
        twice[-1] = 1.0
    ey = np.exp(2j * np.pi * ys[:, None] * ky[None, :])
    ex = np.exp(2j * np.pi * kx[:, None] * xs[None, :]) * twice[:, None]
    return np.real(ey @ cross @ ex)


def _parabola(m1: float, c0: float, p1: float) -> float:
    denom = m1 - 2.0 * c0 + p1
    return 0.0 if denom >= 0 else 0.5 * (m1 - p1) / denom


def _register_planes(pa: np.ndarray, pb: np.ndarray) -> PassShift:
    h = min(pa.shape[0], pb.shape[0])
    w = min(pa.shape[1], pb.shape[1])
    pa, pb = pa[:h, :w], pb[:h, :w]
    if h < 16 or w < 16:
        return PassShift(0.0, 0.0, 0.0, f"{h}x{w} is too small to register")
    cross = cross_power(pa, pb, drop_axes=True)
    surface = np.fft.irfft2(cross, s=(h, w))

    ry, rx = min(SEARCH_PX, h // 2 - 1), min(SEARCH_PX, w // 2 - 1)
    ys = np.arange(-ry, ry + 1)
    xs = np.arange(-rx, rx + 1)
    window = surface[np.ix_(ys % h, xs % w)]
    spread = float(window.std())
    peak = int(np.argmax(window))
    iy, ix = divmod(peak, window.shape[1])
    confidence = float((window.max() - window.mean()) / spread) if spread > 0 else 0.0
    if confidence < CONFIDENCE_FLOOR:
        return PassShift(0.0, 0.0, confidence,
                         f"peak z {confidence:.1f} below the floor of {CONFIDENCE_FLOOR:g}")
    if iy in (0, window.shape[0] - 1) or ix in (0, window.shape[1] - 1):
        return PassShift(0.0, 0.0, confidence,
                         f"peak at the edge of the {SEARCH_PX} px search; the shift is larger")
    dy0, dx0 = int(ys[iy]), int(xs[ix])

    # Refine: sample the surface every 1/UPSAMPLE px across one pixel either
    # side of the integer peak, then a parabola through the best three.
    steps = (np.arange(2 * UPSAMPLE + 1) - UPSAMPLE) / UPSAMPLE
    fine = _evaluate(cross, (h, w), dy0 + steps, dx0 + steps)
    fy, fx = np.unravel_index(int(np.argmax(fine)), fine.shape)
    fy = min(max(int(fy), 1), fine.shape[0] - 2)
    fx = min(max(int(fx), 1), fine.shape[1] - 2)
    sub_y = _parabola(fine[fy - 1, fx], fine[fy, fx], fine[fy + 1, fx])
    sub_x = _parabola(fine[fy, fx - 1], fine[fy, fx], fine[fy, fx + 1])
    dy = dy0 + steps[fy] + sub_y / UPSAMPLE
    dx = dx0 + steps[fx] + sub_x / UPSAMPLE
    return PassShift(float(dy), float(dx), confidence)


def register_subpixel(ref: np.ndarray, mov: np.ndarray) -> PassShift:
    """Where `mov` sits relative to `ref`, to about a hundredth of a pixel.

    Read from the central `WINDOW_PX` of both. Returns a refused
    :class:`PassShift` rather than raising when there is too little detail to
    lock onto or the peak lies outside the search.
    """
    return _register_planes(_plane(ref), _plane(mov))


def band_residuals(ref: np.ndarray, mov: np.ndarray, bands: int = 6) -> list[tuple[int, PassShift]]:
    """The shift of each horizontal band, as ``(centre_row, shift)``.

    A diagnostic, not a correction. After a rigid alignment every band should
    read near zero; a band that does not is the evidence a warp varying down the
    frame would need before anyone builds one.
    """
    h = min(np.asarray(ref).shape[0], np.asarray(mov).shape[0])
    edges = np.linspace(0, h, bands + 1).astype(int)
    out = []
    for y0, y1 in zip(edges[:-1], edges[1:]):
        rows = slice(int(y0), int(y1))
        out.append(((int(y0) + int(y1)) // 2,
                    _register_planes(_plane(ref, rows), _plane(mov, rows))))
    return out


def shift_image(image: np.ndarray, dy: float, dx: float) -> tuple[np.ndarray, np.ndarray]:
    """Move `image` so that it lies on its reference: ``out[i, j] = image[i - dy, j - dx]``.

    Returns ``(shifted, valid)``, `valid` an ``(H, W)`` mask that is False where
    the shifted frame has no data of its own -- the rows and columns the shift
    uncovered, plus `EDGE_MARGIN_PX`. Feed it to the merge so those pixels
    carry no weight.

    By a phase ramp on each channel's spectrum, which preserves the noise
    exactly (see the module docstring). The frame is mirrored at its edges
    first: a periodic transform otherwise sees the top row next to the bottom
    one, and a sub-pixel shift of that step rings across the frame.
    """
    a = np.asarray(image)
    h, w = a.shape[:2]
    pad = int(math.ceil(max(abs(dy), abs(dx)))) + 8
    ky = np.fft.fftfreq(h + 2 * pad)[:, None]
    kx = np.fft.rfftfreq(w + 2 * pad)[None, :]
    ramp = np.exp(-2j * np.pi * (ky * dy + kx * dx))

    def one(plane: np.ndarray) -> np.ndarray:
        p = np.pad(plane.astype(np.float64), pad, mode="symmetric")
        moved = np.fft.irfft2(np.fft.rfft2(p) * ramp, s=p.shape)
        return moved[pad : pad + h, pad : pad + w]

    planes = [a] if a.ndim == 2 else [a[..., c] for c in range(a.shape[2])]
    moved = [one(p) for p in planes]
    if np.issubdtype(a.dtype, np.integer):
        info = np.iinfo(a.dtype)
        moved = [np.clip(np.rint(m), info.min, info.max) for m in moved]
    out = (moved[0] if a.ndim == 2 else np.stack(moved, axis=-1)).astype(a.dtype)

    # row i reads source row i - dy, which exists for 0 <= i - dy <= h - 1
    rows = np.arange(h) - dy
    cols = np.arange(w) - dx
    ok_y = (rows >= EDGE_MARGIN_PX) & (rows <= h - 1 - EDGE_MARGIN_PX)
    ok_x = (cols >= EDGE_MARGIN_PX) & (cols <= w - 1 - EDGE_MARGIN_PX)
    if abs(dy) < 1e-9:
        ok_y[:] = True
    if abs(dx) < 1e-9:
        ok_x[:] = True
    return out, ok_y[:, None] & ok_x[None, :]


def register_passes(
    frames: Sequence[np.ndarray],
) -> tuple[list[np.ndarray], list[np.ndarray], list[PassShift]]:
    """Put every pass onto the first: ``(aligned, valid, shifts)``.

    Each pass is registered to ``frames[0]`` on its own, never to its
    neighbour, so an error in one match cannot carry into the next. A pass whose
    match is refused comes back unmoved with `valid` all False -- a pass nobody
    could place must not be averaged into a picture -- and its shift says why.
    """
    if not frames:
        raise ValueError("no passes to register")
    ref = np.asarray(frames[0])
    h, w = ref.shape[:2]
    aligned = [ref]
    valid = [np.ones((h, w), dtype=bool)]
    shifts = [PassShift(0.0, 0.0, float("inf"))]
    ref_plane = _plane(ref)
    for frame in frames[1:]:
        f = np.asarray(frame)
        if f.shape[:2] != (h, w):
            raise ValueError(f"pass is {f.shape[:2]}, reference is {(h, w)}")
        s = _register_planes(ref_plane, _plane(f))
        shifts.append(s)
        if s.ok:
            moved, ok = shift_image(f, s.dy, s.dx)
        else:
            moved, ok = f, np.zeros((h, w), dtype=bool)
        aligned.append(moved)
        valid.append(ok)
    return aligned, valid, shifts
