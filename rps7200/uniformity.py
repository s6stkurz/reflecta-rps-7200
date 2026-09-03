"""Measure how uniform the *delivered* image is across the frame.

Two-point shading correction divides each output column by that column's light
reference, so it already flattens the lamp's left-to-right falloff -- around 25%
centre to edge on this scanner. What it cannot touch is anything varying along
y: the reference is one value per column, measured over ``CALIBRATION_FRAME``,
the lower part of the transport. Whether a *2D* falloff survives into the
finished image is an open question, and this module is how it gets answered.

The method is to rotate a target and difference the results. Model the corrected
image multiplicatively, in log space::

    m(x, y) = s(x, y) + t(x, y)

with ``s`` the scanner's field and ``t`` the target. Insert the target in
orientation ``g``, scan, un-rotate the pixels back into target coordinates::

    n_g(u, v) = t(u, v) + s(g(u, v))

and difference two orientations. **``t`` cancels exactly** -- no assumption that
the target is uniform, flat, or characterised::

    d_g = n_g - n_e = s∘g - s

That exactness is the whole point. A flat cannot do this: a falloff measured
from clear film cannot be attributed, because nothing in it says whether the
falloff belongs to the scanner or to the film.

**What rotation cannot see.** The four insertions form the Klein four-group, so
splitting ``s`` by parity gives four components, and the part that is *even in
both axes* -- which is exactly a centred radial vignette -- cancels in every
difference along with the target. Three of four components are recovered
exactly; the fourth needs a flat, and :func:`solve_components` reports it as
``None`` rather than zero so the blind spot cannot be mistaken for a result.

Everything here works on a **linear** image. Running it after the percentile
stretch in ``tools/make_comparison.invert`` would stop ``t`` cancelling and
manufacture a field out of nothing -- see :func:`log_image`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .direct import MM_PER_INCH

#: The four ways a mounted slide goes into the transport. They form the Klein
#: four-group: each is its own inverse, so un-rotating a pass is applying the
#: same operation again, and every one is an exact array reversal -- no
#: interpolation, so differencing two orientations introduces nothing of its own.
AS_IS = "as-is"
ROT180 = "180"
MIRROR_X = "turned-over"
MIRROR_Y = "turned-over-180"
ORIENTATIONS = (AS_IS, ROT180, MIRROR_X, MIRROR_Y)

#: Block side used to sample patch interiors, in millimetres. An IT8 patch is
#: roughly 1.3 mm, so half a millimetre sits inside one with margin at any
#: resolution -- which is what lets a 600 dpi and an 1800 dpi measurement be
#: compared without either being resampled.
BLOCK_MM = 0.5

#: A block counts as patch interior when its relative standard deviation is
#: below this in *both* images. Patch borders and the target's printed text
#: land far above it and are dropped, which is what makes registration only
#: need to be good to a fraction of a patch rather than sub-pixel.
#:
#: Calibrated on real scans, not on synthetic flats. Measured over a 600 dpi IT8
#: pass (12 px blocks, median level ~6150 counts), the two populations are:
#: patch interiors around 0.035-0.06, and border-straddling blocks from 0.25
#: upward. A threshold of 0.02 -- which a noiseless synthetic target satisfies
#: easily -- kept 7 blocks of 3337 and made every fit meaningless. 0.10 sits in
#: the gap between the populations and keeps ~34%. Sensor noise inside a real
#: patch is not a reason to reject it: it averages out over 144 pixels and again
#: over the ~1000 surviving blocks, whereas a border does not average out at all.
FLAT_BLOCK_RSD = 0.10

#: Floor on |Spearman rho| for a band to give a meaningful *direction*. This is
#: deliberately loose, because measurement showed monotonicity does not separate
#: the populations at all: a real IT8 greyscale row scored 0.834-0.838 across six
#: passes, while nine film negatives -- pictures, with no greyscale row anywhere
#: -- reached up to 0.934. A real GS row is not a clean ramp: Dmin/Dmax patches
#: and the target border break its rank correlation well below the ~0.99 a
#: synthetic ramp gives.
ORIENTATION_CONFIDENCE = 0.6

#: How far the winning band must beat the best band elsewhere in the frame.
#: Measured: IT8 passes 0.374-0.386, film negatives 0.029-0.309. The best single
#: -image discriminator available, but the gap is thin and this gate is a sanity
#: check, not proof -- see :func:`orientation_signature`.
MIN_ORIENTATION_MARGIN = 0.35

#: Fraction of a profile's steps that may be large before it stops looking like
#: a staircase. This is what separates a greyscale row from a gradient in a
#: picture: the GS row is 24 discrete plateaus, so almost all of its
#: neighbour-to-neighbour differences are ~0 and a couple of dozen are large,
#: giving ~0.03 (0.09 with sensor noise). A smooth gradient spreads its change
#: evenly across every column and scores 1.0. Both are perfectly monotonic, so
#: only this tells them apart.
MAX_STEP_FRACTION = 0.3


def apply_orientation(image: np.ndarray, orientation: str) -> np.ndarray:
    """Transform ``image`` as inserting the film that way would.

    Each operation is an involution, so this doubles as the un-rotate: applying
    :data:`ROT180` to a pass taken at :data:`ROT180` puts it back into target
    coordinates. Views, not copies -- the caller should not write through them.
    """
    if orientation == AS_IS:
        return image
    if orientation == ROT180:
        return image[::-1, ::-1]
    if orientation == MIRROR_X:
        return image[:, ::-1]
    if orientation == MIRROR_Y:
        return image[::-1]
    raise ValueError(f"unknown orientation {orientation!r}; expected one of {ORIENTATIONS}")


def block_size(dpi: int, mm: float = BLOCK_MM) -> int:
    """Block side in pixels for a physical size, never below 8 px."""
    return max(8, int(round(dpi * mm / MM_PER_INCH)))


def luminance(image: np.ndarray) -> np.ndarray:
    """Mean of the visible channels as float64.

    Deliberately an unweighted mean of at most three channels. A luma weighting
    would be wrong here twice over: these are raw sensor channels, not sRGB
    primaries, and channel 3 is infrared, which carries no lightness at all.
    """
    data = image.astype(np.float64)
    if data.ndim == 2:
        return data
    return data[:, :, : min(3, data.shape[2])].mean(axis=2)


# -- orientation, read off the target itself --------------------------------


@dataclass(frozen=True)
class OrientationSignature:
    """Which edge the greyscale row sits on and which way it ramps.

    Deliberately *not* an absolute orientation. Which edge counts as "bottom"
    for an as-is insertion is a convention about how the film is handled, not
    something the pixels know, so this records what was seen and
    :func:`classify_relative` names it against a reference pass. That way the
    study never has to assume which physical flip produces which mirror -- the
    image says.
    """

    edge: str            # "top" or "bottom"
    rising: bool         # luminance increases left to right
    confidence: float    # |Spearman rho| of the winning band
    row: int             # centre row of the band, for the crop
    step_fraction: float = 0.0   # how staircase-like the band's profile is
    margin: float = 0.0          # how far it beat the best band elsewhere

    @property
    def confident(self) -> bool:
        """A weak per-image sanity check -- **not** proof of a greyscale row.

        Measured on real data, no single-image statistic separates an IT8 from a
        photograph cleanly: monotonicity overlaps outright, and the margin gap is
        only 0.374 against 0.309. So this gate exists to catch gross failure --
        the target half out of frame, the wrong film loaded -- and the real
        validation is at the level of the *set*: six passes of one target must
        produce exactly the four (edge, direction) combinations with the repeat
        passes agreeing. That is six measurements constraining four outcomes, and
        it is far stronger than any threshold applied to one image.
        """
        return (
            self.confidence >= ORIENTATION_CONFIDENCE
            and self.step_fraction <= MAX_STEP_FRACTION
            and self.margin >= MIN_ORIENTATION_MARGIN
        )


def _spearman(values: np.ndarray) -> float:
    """Rank correlation of ``values`` against its own index.

    Rank-based rather than Pearson because the IT8 greyscale is a *density*
    ramp: its steps are roughly even in log exposure, so in linear counts it is
    strongly curved and a Pearson correlation understates it. Monotonicity is
    the property being tested, and that is what rank correlation measures.
    """
    n = values.size
    if n < 3:
        return 0.0
    ranks = np.empty(n, dtype=np.float64)
    ranks[np.argsort(values, kind="stable")] = np.arange(n, dtype=np.float64)
    index = np.arange(n, dtype=np.float64)
    a = ranks - ranks.mean()
    b = index - index.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def _step_fraction(profile: np.ndarray) -> float:
    """Fraction of a profile's steps that are large -- low for a staircase.

    A 24-step greyscale row changes at 23 places and is flat everywhere else,
    so almost every neighbour difference is near zero. A gradient in a
    photograph changes by a similar amount at every column. Both can be
    perfectly monotonic, which is why :func:`orientation_signature` cannot rely
    on rank correlation alone.
    """
    d = np.abs(np.diff(profile.astype(np.float64)))
    peak = d.max() if d.size else 0.0
    return float((d > 0.2 * peak).mean()) if peak > 0 else 1.0


def orientation_signature(
    image: np.ndarray, bands: int = 16, margin: float = 0.02
) -> OrientationSignature:
    """Find the greyscale row and report which edge it is on and its direction.

    An IT8 target carries a 24-step greyscale row along **one** edge, ramping
    light to dark in **one** direction. That is a two-bit signature, and the two
    bits are precisely the two mirrors: a left-right mirror reverses the ramp
    and leaves the edge, a top-bottom mirror moves the edge and leaves the ramp,
    and a 180° rotation does both. So the four insertions are distinguishable
    from the pixels alone, with no OCR and no knowledge of the patch grid.

    Scans row bands one patch-row tall, takes each band's column profile as a
    median down its rows -- median so a speck of dust or a scratch crossing the
    band cannot drag the profile -- and keeps the band whose profile is most
    monotonic across the frame. The colour-patch rows are not monotonic, so the
    margin to second place is large.

    ``margin`` trims the outermost fraction of columns before ranking: a slide
    mount can shadow the extreme edge, and a shadow at one end is itself
    monotonic, which would otherwise compete with the real ramp.
    """
    lum = luminance(image)
    h, w = lum.shape
    band = max(2, h // bands)
    cut = max(0, int(round(w * margin)))
    stop = w - cut
    if stop - cut < 8:
        cut, stop = 0, w

    # Score every band on both axes, then prefer the most monotonic band that
    # still looks like a staircase. Selecting on monotonicity alone would let a
    # smooth gradient in the picture outrank the real greyscale row.
    scored = []
    for top in range(0, h - band + 1, max(1, band // 2)):
        profile = np.median(lum[top : top + band, cut:stop], axis=0)
        scored.append((abs(_spearman(profile)), _spearman(profile),
                       _step_fraction(profile), top + band // 2))

    stepped = [row for row in scored if row[2] <= MAX_STEP_FRACTION]
    best = max(stepped or scored, key=lambda row: row[0])
    _, rho, steps, row = best

    # Margin against the best band that is not adjacent to the winner. The
    # greyscale row spans several overlapping bands, so its own neighbours would
    # otherwise be its competition and the margin would always read ~0.
    elsewhere = [r for r in (stepped or scored) if abs(r[3] - row) > 3 * band]
    margin = best[0] - (max(r[0] for r in elsewhere) if elsewhere else 0.0)

    return OrientationSignature(
        edge="top" if row < h / 2 else "bottom",
        rising=rho > 0,
        confidence=abs(rho),
        row=int(row),
        step_fraction=steps,
        margin=float(margin),
    )


def classify_relative(
    signature: OrientationSignature, reference: OrientationSignature
) -> str:
    """Name ``signature``'s orientation relative to a reference pass.

    A left-right mirror keeps the greyscale row on its edge and reverses the
    ramp; a top-bottom mirror moves it to the other edge and keeps the ramp;
    180° does both. So the two bits map onto the four insertions with nothing
    left over.
    """
    same_edge = signature.edge == reference.edge
    same_dir = signature.rising == reference.rising
    if same_edge and same_dir:
        return AS_IS
    if same_edge:
        return MIRROR_X
    if same_dir:
        return MIRROR_Y
    return ROT180


def channel_report(image: np.ndarray, signature: OrientationSignature,
                   band: int | None = None) -> dict[str, object]:
    """Sanity-check the channels against the greyscale row.

    The greyscale row is neutral by construction, so after correction the
    visible channels should read within a few percent of each other along it.
    A gross per-channel gain error shows up here immediately.

    **This cannot catch a red/blue swap**, and pretending otherwise would be
    worse than not checking: the greyscale row is neutral, so swapping two
    channels leaves it neutral. Detecting that needs the colour patches, which
    needs the grid, which needs far more machinery than the rest of this module.
    What is reported instead is `live`, whether each channel varies at all --
    enough to catch a dead or constant plane.
    """
    data = image.astype(np.float64)
    if data.ndim == 2 or data.shape[2] < 3:
        return {"neutral": None, "live": None, "note": "fewer than three channels"}

    h = data.shape[0]
    band = band or max(2, h // 16)
    top = int(np.clip(signature.row - band // 2, 0, max(0, h - band)))
    strip = data[top : top + band, :, :3]

    means = strip.reshape(-1, 3).mean(axis=0)
    overall = float(means.mean())
    spread = float(means.max() - means.min()) / overall if overall > 0 else float("inf")
    live = [bool(data[:, :, c].std() > 0) for c in range(3)]
    return {
        "neutral": round(spread, 4),
        "channel_means": [round(float(m), 1) for m in means],
        "live": live,
        "note": "greyscale row is neutral, so a red/blue swap is invisible here",
    }


# -- registration -----------------------------------------------------------


def register(a: np.ndarray, b: np.ndarray, max_shift: int = 64) -> tuple[int, int, float]:
    """Integer-pixel shift taking ``b`` onto ``a``, by phase correlation.

    Returns ``(dy, dx, confidence)``, defined so that ``a[i, j]`` and
    ``b[i - dy, j - dx]`` look at the same place on the film -- which is what
    :func:`align` consumes. Pass the pair straight to :func:`align` rather than
    applying the shift by hand; the sign is easy to invert and a silent
    inversion here would double the misalignment instead of removing it.

    Not optional. Two passes at an identical frame and dpi have already been
    seen on this scanner to correlate at **lag -16 columns rather than lag 0**
    (docs/shading-calibration-plan.md), so assuming pixel j of one pass and
    pixel j of the next look at the same place on the film is known to be wrong
    here. Differencing misaligned frames would turn every patch border into a
    fake field.

    Phase correlation rather than a plain cross-correlation because the target
    is mostly flat patches: normalising each frequency by its magnitude weights
    the patch borders, which is where the alignment information actually lives.
    ``confidence`` is the peak height over the mean of the correlation surface.
    """
    fa, fb = luminance(a), luminance(b)
    h = min(fa.shape[0], fb.shape[0])
    w = min(fa.shape[1], fb.shape[1])
    fa, fb = fa[:h, :w], fb[:h, :w]
    fa = fa - fa.mean()
    fb = fb - fb.mean()

    # Hann window: the FFT treats the frame as periodic, so an un-windowed
    # frame's opposite edges act like a hard seam and can outrank the target.
    win = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    A = np.fft.rfft2(fa * win)
    B = np.fft.rfft2(fb * win)
    cross = A * np.conj(B)
    mag = np.abs(cross)
    surface = np.fft.irfft2(cross / np.where(mag > 0, mag, 1.0), s=(h, w))

    reach_y = min(max_shift, h // 2)
    reach_x = min(max_shift, w // 2)
    window = np.full((2 * reach_y + 1, 2 * reach_x + 1), -np.inf)
    for i, dy in enumerate(range(-reach_y, reach_y + 1)):
        for j, dx in enumerate(range(-reach_x, reach_x + 1)):
            window[i, j] = surface[dy % h, dx % w]

    peak = int(np.argmax(window))
    dy = peak // window.shape[1] - reach_y
    dx = peak % window.shape[1] - reach_x
    spread = float(window[np.isfinite(window)].std())
    confidence = float((window.max() - window[np.isfinite(window)].mean()) / spread) if spread > 0 else 0.0
    return int(dy), int(dx), confidence


def align(a: np.ndarray, b: np.ndarray, dy: int, dx: int) -> tuple[np.ndarray, np.ndarray]:
    """Crop both frames to the region where they overlap after a shift."""
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    ay0, by0 = max(0, dy), max(0, -dy)
    ax0, bx0 = max(0, dx), max(0, -dx)
    height = h - abs(dy)
    width = w - abs(dx)
    if height <= 0 or width <= 0:
        raise ValueError(f"shift ({dy}, {dx}) leaves no overlap in {h}x{w}")
    return (
        a[ay0 : ay0 + height, ax0 : ax0 + width],
        b[by0 : by0 + height, bx0 : bx0 + width],
    )


# -- the target-cancelling core ---------------------------------------------


@dataclass
class BlockSamples:
    """Log-ratio samples on flat patch interiors, in normalised coordinates."""

    u: np.ndarray            # (N,) horizontal, -1..1
    v: np.ndarray            # (N,) vertical, -1..1
    values: np.ndarray       # (N, C) natural log of the ratio
    kept: int
    total: int

    @property
    def fraction_kept(self) -> float:
        return self.kept / self.total if self.total else 0.0


def block_ratios(
    a: np.ndarray,
    b: np.ndarray,
    dpi: int,
    rsd: float = FLAT_BLOCK_RSD,
    trailing: int = 0,
) -> BlockSamples:
    """Log-ratio of two aligned frames, sampled on flat blocks only.

    This is where the target cancels. Both frames show the same film, so
    dividing them removes it and leaves the difference of the two scanner
    fields -- but only where both frames are genuinely uniform. A block
    straddling a patch border is thrown out rather than averaged, because a
    border under a one-pixel misalignment produces an enormous ratio that no
    amount of smoothing afterwards would survive.

    Rejecting on within-block variance rather than aligning to sub-pixel
    accuracy is what makes the method robust: what survives is patch interiors,
    and inside a patch a small misalignment changes nothing at all.

    ``trailing`` drops that many columns from the right edge. At 600 dpi the
    CCD mask marks 860 used pixels while the pass is 862 wide, so
    :func:`rps7200.shading.apply_shading` leaves the last columns *unshaded* --
    at the frame edge, which is exactly where a vignette is largest. Passing
    ``width - report["columns"]`` here keeps that artefact out of the fit.
    """
    if a.shape != b.shape:
        raise ValueError(f"frames differ in shape: {a.shape} vs {b.shape}")
    fa = a.astype(np.float64)
    fb = b.astype(np.float64)
    if fa.ndim == 2:
        fa, fb = fa[:, :, None], fb[:, :, None]
    if trailing > 0:
        fa, fb = fa[:, :-trailing], fb[:, :-trailing]

    h, w, nc = fa.shape
    k = block_size(dpi)
    ny, nx = h // k, w // k
    if ny < 2 or nx < 2:
        raise ValueError(f"{h}x{w} is too small for {k}px blocks at {dpi} dpi")

    ga = fa[: ny * k, : nx * k].reshape(ny, k, nx, k, nc).transpose(0, 2, 1, 3, 4)
    gb = fb[: ny * k, : nx * k].reshape(ny, k, nx, k, nc).transpose(0, 2, 1, 3, 4)
    ga = ga.reshape(ny, nx, k * k, nc)
    gb = gb.reshape(ny, nx, k * k, nc)

    mean_a, mean_b = ga.mean(axis=2), gb.mean(axis=2)
    std_a, std_b = ga.std(axis=2), gb.std(axis=2)

    with np.errstate(divide="ignore", invalid="ignore"):
        rsd_a = np.where(mean_a > 0, std_a / mean_a, np.inf)
        rsd_b = np.where(mean_b > 0, std_b / mean_b, np.inf)

    positive = (mean_a > 0) & (mean_b > 0)
    flat = (rsd_a < rsd) & (rsd_b < rsd) & positive
    keep = flat.all(axis=2)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.log(np.where(positive, mean_a, 1.0) / np.where(positive, mean_b, 1.0))
    ratio = np.where(np.isfinite(ratio), ratio, 0.0)

    rows, cols = np.nonzero(keep)
    centres_y = (rows * k + k / 2) / h
    centres_x = (cols * k + k / 2) / w
    return BlockSamples(
        u=2 * centres_x - 1,
        v=2 * centres_y - 1,
        values=ratio[rows, cols, :],
        kept=int(keep.sum()),
        total=int(ny * nx),
    )


# -- the smooth field -------------------------------------------------------


def _basis(u: np.ndarray, v: np.ndarray, degree: int) -> np.ndarray:
    """Total-degree polynomial terms, ordered so parity is easy to read off."""
    return np.stack(
        [u**i * v**j for i in range(degree + 1) for j in range(degree + 1 - i)],
        axis=-1,
    )


@dataclass
class Field:
    """A smooth multiplicative field, in log space, per channel.

    Coefficients are over *frame-normalised* coordinates -- u and v run -1..1
    across whatever was scanned -- so a 600 dpi and an 1800 dpi measurement of
    the same frame produce directly comparable numbers with neither resampled.
    This follows `resample_reference` in :mod:`rps7200.direct`, which maps a
    reference between frames by scanner position rather than by width ratio,
    for the same reason: a ratio of widths smears the thing being measured.
    """

    coefficients: np.ndarray   # (terms, channels)
    degree: int
    channels: int
    samples: int = 0
    kept_fraction: float = 0.0
    #: Bounding box of the samples the fit was made from, in normalised
    #: coordinates. A target does not fill the frame -- an IT8 at 600 dpi covers
    #: about u -0.96..0.94, v -0.90..0.90 -- and a degree-4 polynomial
    #: extrapolates hard outside its data. Measured on a real repeat pair, the
    #: span read 2.32% over the whole grid against 1.42% where blocks actually
    #: were: a 60% overstatement, all of it invented at the corners. Report over
    #: :meth:`support` and never over the bare grid.
    support_box: tuple[float, float, float, float] = (-1.0, 1.0, -1.0, 1.0)

    def evaluate(self, height: int, width: int) -> np.ndarray:
        v, u = np.meshgrid(
            np.linspace(-1, 1, height), np.linspace(-1, 1, width), indexing="ij"
        )
        design = _basis(u.ravel(), v.ravel(), self.degree)
        out = design @ self.coefficients
        return out.reshape(height, width, self.channels)

    def support(self, height: int, width: int) -> np.ndarray:
        """Boolean mask of the region the fit is actually supported by data."""
        u_lo, u_hi, v_lo, v_hi = self.support_box
        v, u = np.meshgrid(
            np.linspace(-1, 1, height), np.linspace(-1, 1, width), indexing="ij"
        )
        return (u >= u_lo) & (u <= u_hi) & (v >= v_lo) & (v <= v_hi)

    def span_percent(self, height: int, width: int, channel: int) -> float:
        """Peak-to-peak of one channel, over the supported region only."""
        surface = self.evaluate(height, width)[:, :, channel]
        mask = self.support(height, width)
        return peak_to_peak_percent(surface[mask]) if mask.any() else 0.0

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path,
            coefficients=self.coefficients,
            degree=self.degree,
            channels=self.channels,
            samples=self.samples,
            kept_fraction=self.kept_fraction,
            support_box=np.array(self.support_box),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Field":
        with np.load(path) as z:
            box = (tuple(float(v) for v in z["support_box"])
                   if "support_box" in z else (-1.0, 1.0, -1.0, 1.0))
            return cls(
                coefficients=z["coefficients"],
                degree=int(z["degree"]),
                channels=int(z["channels"]),
                samples=int(z["samples"]),
                kept_fraction=float(z["kept_fraction"]),
                support_box=box,
            )


def fit_field(
    samples: BlockSamples, degree: int = 4, rounds: int = 5, tune: float = 1.345
) -> Field:
    """Robust least-squares fit of a smooth surface to block log-ratios.

    Total degree 4 -- fifteen terms -- because the thing being described is an
    optical falloff, which is smooth by nature. A basis flexible enough to
    follow patch-to-patch structure would absorb the target instead of the
    field, and there would be no way to tell from the residual.

    Iteratively reweighted with a Huber loss: a handful of blocks always slip
    the flatness test -- a scratch, a dust mote, a patch that happens to be
    uniform across a border -- and plain least squares would let each one pull
    the surface. ``tune`` is the standard Huber constant in units of the robust
    scale.
    """
    design = _basis(samples.u, samples.v, degree)
    nc = samples.values.shape[1]
    coeffs = np.zeros((design.shape[1], nc))

    for c in range(nc):
        y = samples.values[:, c]
        weights = np.ones_like(y)
        beta = np.zeros(design.shape[1])
        for _ in range(rounds):
            root = np.sqrt(weights)[:, None]
            beta, *_ = np.linalg.lstsq(design * root, y * np.sqrt(weights), rcond=None)
            residual = y - design @ beta
            scale = 1.4826 * np.median(np.abs(residual - np.median(residual)))
            if not np.isfinite(scale) or scale <= 0:
                break
            z = np.abs(residual) / (tune * scale)
            weights = np.where(z <= 1.0, 1.0, 1.0 / np.maximum(z, 1e-9))
        coeffs[:, c] = beta

    return Field(
        coefficients=coeffs,
        degree=degree,
        channels=nc,
        samples=int(samples.u.size),
        kept_fraction=samples.fraction_kept,
        support_box=(float(samples.u.min()), float(samples.u.max()),
                     float(samples.v.min()), float(samples.v.max()))
        if samples.u.size else (-1.0, 1.0, -1.0, 1.0),
    )


# -- symmetry ---------------------------------------------------------------

#: Parity of a component: signs applied under a left-right and a top-bottom
#: mirror. ``pp`` is even in both -- the invisible one.
COMPONENTS = ("pp", "mp", "pm", "mm")


def decompose(surface: np.ndarray) -> dict[str, np.ndarray]:
    """Split a sampled field into its four parity components.

    ``pp`` even in both axes, ``mp`` odd in x, ``pm`` odd in y, ``mm`` odd in
    both. They sum back to the original exactly.
    """
    f = surface
    fx = surface[:, ::-1]
    fy = surface[::-1]
    fxy = surface[::-1, ::-1]
    return {
        "pp": (f + fx + fy + fxy) / 4.0,
        "mp": (f - fx + fy - fxy) / 4.0,
        "pm": (f + fx - fy - fxy) / 4.0,
        "mm": (f - fx - fy + fxy) / 4.0,
    }


def solve_components(
    d_mirror_x: np.ndarray, d_mirror_y: np.ndarray, d_rot180: np.ndarray
) -> dict[str, np.ndarray | None]:
    """Recover the field's odd components from three orientation differences.

    With ``d_g = s∘g - s`` and ``s`` split by parity::

        d_fx = -2(s_mp + s_mm)      s_mm = -(d_fx + d_fy - d_r) / 4
        d_fy = -2(s_pm + s_mm)  =>  s_mp = -d_fx/2 - s_mm
        d_r  = -2(s_mp + s_pm)      s_pm = -d_fy/2 - s_mm

    ``pp`` comes back **None**, not zero. It is even under every operation in
    the group, so it cancels in each difference exactly as the target does, and
    a centred radial vignette -- the shape most likely to be there -- is
    precisely that component. Returning zero would report the blind spot as a
    measurement. It has to come from a flat instead.
    """
    mm = -(d_mirror_x + d_mirror_y - d_rot180) / 4.0
    return {
        "pp": None,
        "mp": -d_mirror_x / 2.0 - mm,
        "pm": -d_mirror_y / 2.0 - mm,
        "mm": mm,
    }


#: Which parity components each orientation difference is *allowed* to contain.
#: ``d_fx = -2(s_mp + s_mm)`` and both terms are odd in x, so any energy in an
#: x-even component is not the scanner. Same reasoning down the other two rows.
ALLOWED_COMPONENTS = {
    MIRROR_X: ("mp", "mm"),   # both terms odd in x
    MIRROR_Y: ("pm", "mm"),   # both terms odd in y
    ROT180: ("mp", "pm"),     # both terms odd under the point reflection
}


def parity_residual(difference: np.ndarray, orientation: str) -> float:
    """How much of a difference sits in a parity it cannot legitimately have.

    ``d_fx`` is by construction odd in x -- it is ``-2(s_mp + s_mm)`` and both
    terms are x-odd -- so any even-in-x energy in the measured version is not
    the scanner. It is misregistration, the focus shift from turning a slide
    over, or the lamp drifting between passes. This is the study's own error
    bar, and it costs nothing: the redundancy is already in the measurement.

    Returned as a fraction of total magnitude, so 0.0 is a perfectly consistent
    measurement and 1.0 is one carrying no legitimate signal at all.
    """
    if orientation not in ALLOWED_COMPONENTS:
        raise ValueError(
            f"{orientation!r} is not a difference orientation; "
            f"expected one of {sorted(ALLOWED_COMPONENTS)}"
        )
    parts = decompose(difference)
    energy = {k: float((parts[k] ** 2).sum()) for k in COMPONENTS}
    total = sum(energy.values())
    if total <= 0:
        return 0.0
    good = sum(energy[k] for k in ALLOWED_COMPONENTS[orientation])
    return float(np.sqrt(max(0.0, total - good) / total))


def peak_to_peak_percent(surface: np.ndarray) -> float:
    """Span of a log-space field, as a percentage brightness ratio.

    ``exp(max - min) - 1``: a field spanning 0.1 in natural log is a 10.5%
    brightness difference corner to corner, which is the number worth reporting.
    """
    if surface.size == 0:
        return 0.0
    return float((np.exp(float(surface.max() - surface.min())) - 1.0) * 100.0)


def log_image(image: np.ndarray, floor: float = 1.0) -> np.ndarray:
    """Natural log of a **linear** image, for differencing.

    The whole method rests on ``m = s + t`` in log space, which needs the image
    linear. Running it on the output of ``tools/make_comparison.invert`` would
    be silently wrong: that is a per-channel percentile stretch computed
    independently per image, so its parameters differ between two orientations
    of the same target, ``t`` would not cancel, and the difference would be a
    field manufactured out of the stretch. Measure before inversion, always.
    """
    data = image.astype(np.float64)
    return np.log(np.maximum(data, floor))
