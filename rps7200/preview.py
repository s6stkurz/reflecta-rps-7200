"""Turning scanned pixels into something a screen can show.

Nothing here talks to the scanner and nothing here is a correction. The scans
this driver files are raw negatives and stay that way; what follows is the
eyeball-grade stretch-and-invert that makes one judgeable on screen, the same
transform `tools/make_comparison.py` has always used to produce the comparison
files -- which is why that tool now calls into this module rather than keeping
its own copy.

The output path is deliberately plain. Tk's `PhotoImage` reads raw PPM bytes,
so `render` -> `to_ppm` -> `PhotoImage(data=...)` puts an array on screen with
no image library at all, and the runtime dependency set stays `numpy` alone.

    rgb8 = preview.render(image, channel="IR", invert=False)
    photo = tkinter.PhotoImage(data=preview.to_ppm(rgb8))
"""
from __future__ import annotations

import numpy as np

#: The views a channel selector can offer. "IR" needs a four-channel pass;
#: everything else works on any scan or prescan.
CHANNELS = ("RGB", "R", "G", "B", "IR")

_PLANE = {"R": 0, "G": 1, "B": 2, "IR": 3}

#: Long side of the working copy a session keeps in memory per scan. Big enough
#: to fill a canvas, small enough that a roll does not: at 3600 dpi the frame it
#: comes from is 142 MB, and this is about 8.
PREVIEW_MAX_SIDE = 1400

#: Long side of a filmstrip thumbnail.
THUMB_MAX_SIDE = 256

#: Percentiles the stretch clips to. Wide enough to survive a stuck column or a
#: dust speck, narrow enough that a flat frame still shows its range.
LOW, HIGH = 0.5, 99.5


def has_infrared(image: np.ndarray) -> bool:
    """Whether this array carries an infrared plane at all."""
    return image.ndim == 3 and image.shape[2] >= 4


def channels_available(image: np.ndarray) -> tuple[str, ...]:
    """The subset of `CHANNELS` this array can actually show.

    A prescan is RGB 8-bit, so its infrared view does not exist -- the caller
    greys the control out rather than rendering a black rectangle and letting
    the operator wonder whether the IR really came back empty.
    """
    if image.ndim == 2:
        return ("RGB",)
    return tuple(c for c in CHANNELS if c == "RGB" or _PLANE[c] < image.shape[2])


def select(image: np.ndarray, channel: str = "RGB") -> np.ndarray:
    """One view of `image`: the visible three, or a single plane as 2-D."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {CHANNELS}")
    if image.ndim == 2:
        if channel != "RGB":
            raise ValueError(f"a single-plane image has no {channel!r} channel")
        return image
    if channel == "RGB":
        return image[..., :3]
    plane = _PLANE[channel]
    if plane >= image.shape[2]:
        raise ValueError(
            f"this scan has {image.shape[2]} channels; no {channel!r} to show"
        )
    return image[..., plane]


def normalise(
    image: np.ndarray, low: float = LOW, high: float = HIGH
) -> np.ndarray:
    """Per-channel percentile stretch to floats in [0, 1].

    Per channel rather than jointly, because a negative's orange mask is a huge
    constant offset between the channels and stretching them together leaves the
    picture inside a tenth of the range.
    """
    x = image.astype(np.float64)
    if x.ndim == 2:
        x = x[..., None]
        flat = True
    else:
        flat = False
    out = np.empty_like(x)
    for c in range(x.shape[2]):
        lo, hi = np.percentile(x[..., c], [low, high])
        span = hi - lo
        # A frame with no range at all -- an unexposed prescan, a blank test
        # array -- would otherwise divide by zero and come out as NaN.
        out[..., c] = 0.0 if span <= 0 else np.clip((x[..., c] - lo) / span, 0, 1)
    return out[..., 0] if flat else out


def render(
    image: np.ndarray,
    channel: str = "RGB",
    invert: bool = True,
    low: float = LOW,
    high: float = HIGH,
) -> np.ndarray:
    """`image` as `(H, W, 3)` uint8, ready for the screen.

    `invert` is a display choice and nothing else: what reaches the library is
    the raw negative the scanner sent. A single plane comes back as grey rather
    than tinted -- the infrared view is a measurement, not a colour.
    """
    x = normalise(select(image, channel), low, high)
    if invert:
        x = 1.0 - x
    v = np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if v.ndim == 2:
        v = np.repeat(v[:, :, None], 3, axis=2)
    return v


def to_ppm(rgb8: np.ndarray) -> bytes:
    """A binary PPM, which is what `tkinter.PhotoImage(data=...)` accepts."""
    if rgb8.ndim != 3 or rgb8.shape[2] != 3 or rgb8.dtype != np.uint8:
        raise ValueError(f"expected (H, W, 3) uint8, got {rgb8.shape} {rgb8.dtype}")
    h, w = rgb8.shape[:2]
    return b"P6\n%d %d\n255\n" % (w, h) + np.ascontiguousarray(rgb8).tobytes()


def _step(height: int, width: int, max_side: int) -> int:
    """The decimation factor that brings the long side within `max_side`."""
    longest = max(height, width)
    if longest <= max_side or max_side <= 0:
        return 1
    return -(-longest // max_side)          # ceil, so the result really fits


def downscale(image: np.ndarray, max_side: int) -> np.ndarray:
    """Strided decimation to a bounded long side, aspect preserved.

    Plain `[::s, ::s]`, as `rps7200.bracket._subsample` already does for its
    statistics. Not an area average: this is for looking at, and a decimated
    view that aliases is honest about grain where a smoothed one invents a
    cleanliness the file does not have.
    """
    s = _step(image.shape[0], image.shape[1], max_side)
    return image if s == 1 else image[::s, ::s]


def thumbnail(image: np.ndarray, max_side: int = THUMB_MAX_SIDE) -> np.ndarray:
    """A filmstrip-sized rendering: decimated, stretched, inverted, uint8."""
    return render(downscale(image, max_side))


def fit(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Decimate so the image fits a `width` x `height` box."""
    if width <= 0 or height <= 0:
        return image
    s = max(
        _step(image.shape[0], image.shape[1], max(width, height)),
        -(-image.shape[0] // height) if image.shape[0] > height else 1,
        -(-image.shape[1] // width) if image.shape[1] > width else 1,
    )
    return image if s <= 1 else image[::s, ::s]


def crop(
    image: np.ndarray, cx: float, cy: float, width: int, height: int
) -> np.ndarray:
    """A `width` x `height` window centred on `(cx, cy)`, clamped to the image.

    This is what the 1:1 view reads. A downscaled preview cannot show shadow
    noise or grain, so any judgement about those has to come from real pixels.
    """
    h, w = image.shape[:2]
    width, height = min(width, w), min(height, h)
    x0 = int(round(cx - width / 2))
    y0 = int(round(cy - height / 2))
    x0 = max(0, min(x0, w - width))
    y0 = max(0, min(y0, h - height))
    return image[y0:y0 + height, x0:x0 + width]
