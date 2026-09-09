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

from functools import lru_cache

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


#: The turns a picture can be given. Quarter turns only: anything else would
#: have to resample, and a scan is measurement -- a rotation that invents
#: pixels is not the same file any more.
ROTATIONS = (0, 90, 180, 270)


def rotate(image: np.ndarray, degrees: int = 0) -> np.ndarray:
    """`image` turned clockwise by 0, 90, 180 or 270 degrees.

    Lossless: a quarter turn is a transpose and a flip, so every pixel survives
    and the result is still exactly what the scanner measured, differently
    arranged.
    """
    if degrees % 360 == 0:
        return image
    if degrees % 90:
        raise ValueError(f"{degrees} is not a quarter turn; expected {ROTATIONS}")
    # np.rot90 turns anticlockwise, and this counts clockwise.
    return np.rot90(image, k=-(degrees // 90) % 4, axes=(0, 1))


def unrotate_point(
    x: float, y: float, shape: tuple[int, ...], degrees: int = 0
) -> tuple[float, float]:
    """Where a point on a rotated view sits in the unrotated image.

    `shape` is the *rotated* image's shape, which is what the caller has. This
    is what keeps the aim honest on a turned prescan: the transport moves along
    the unrotated x axis whatever the picture looks like on screen, so a click
    has to come back here before it means a distance.
    """
    turn = degrees % 360
    height, width = shape[0], shape[1]
    if turn == 0:
        return x, y
    if turn == 90:
        # A clockwise quarter turn sent original (x, y) to (H-1-y, x).
        return y, (width - 1) - x
    if turn == 180:
        return (width - 1) - x, (height - 1) - y
    if turn == 270:
        return (height - 1) - y, x
    raise ValueError(f"{degrees} is not a quarter turn; expected {ROTATIONS}")


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


def levels(
    image: np.ndarray, low: float = LOW, high: float = HIGH
) -> np.ndarray:
    """Per-channel `(lo, hi)` cut points, as an array of shape `(C, 2)`.

    Separated from applying them because they must be computed **once per
    picture**, not once per redraw. Recomputing them from whatever happened to
    be on screen made the brightness change as you panned -- the same negative
    looking different depending on where you were looking -- and cost 27 ms a
    frame at fit and 69 ms at 1:1, which is what made zooming feel dead.

    Exact, over every pixel: the comparison files Stefan judges by eye come
    through here, and a subsampled percentile would move them.
    """
    x = image if image.ndim == 3 else image[..., None]
    return np.array([
        np.percentile(x[..., c], [low, high]) for c in range(x.shape[2])
    ])


def channel_levels(all_levels: np.ndarray, channel: str = "RGB") -> np.ndarray:
    """The rows of `levels()` that a given view uses."""
    if channel == "RGB":
        return all_levels[:3]
    return all_levels[[_PLANE[channel]]]


def normalise(
    image: np.ndarray,
    low: float = LOW,
    high: float = HIGH,
    cuts: np.ndarray | None = None,
) -> np.ndarray:
    """Per-channel percentile stretch to floats in [0, 1].

    Per channel rather than jointly, because a negative's orange mask is a huge
    constant offset between the channels and stretching them together leaves the
    picture inside a tenth of the range.

    `cuts` supplies the levels from `levels()` instead of measuring them here,
    which is how a zoomed or panned view keeps the brightness of the whole.
    """
    x = image.astype(np.float64)
    if x.ndim == 2:
        x = x[..., None]
        flat = True
    else:
        flat = False
    if cuts is None:
        cuts = levels(image, low, high)
    out = np.empty_like(x)
    for c in range(x.shape[2]):
        lo, hi = float(cuts[c][0]), float(cuts[c][1])
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
    cuts: np.ndarray | None = None,
) -> np.ndarray:
    """`image` as `(H, W, 3)` uint8, ready for the screen.

    `invert` is a display choice and nothing else: what reaches the library is
    the raw negative the scanner sent. A single plane comes back as grey rather
    than tinted -- the infrared view is a measurement, not a colour.

    `cuts` are levels from `levels()` for this view, so that a crop of a
    picture is stretched like the picture rather than like the crop.
    """
    view = select(image, channel)
    if cuts is None:
        cuts = levels(view, low, high)
    planes = view if view.ndim == 3 else view[..., None]

    if planes.dtype in (np.uint8, np.uint16):
        # Through a lookup table rather than floating-point arithmetic. The
        # stretch is the same curve for every pixel, so it can be computed once
        # for each of the 256 or 65536 possible values and then read off: one
        # gather per channel instead of four passes over the whole array in
        # float. On a 1:1 crop that is 21 ms down to about 3.
        out = np.empty(planes.shape[:2] + (3,), np.uint8)
        for c in range(planes.shape[2]):
            table = _screen_table(planes.dtype, float(cuts[c][0]),
                                  float(cuts[c][1]), invert)
            out[..., c] = table[planes[..., c]]
    else:
        x = normalise(view, low, high, cuts)
        if invert:
            x = 1.0 - x
        out = np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if out.ndim == 2:
            out = out[..., None]

    if out.shape[2] == 1 or planes.shape[2] == 1:
        # A single plane is grey, not tinted: the infrared view is a
        # measurement, not a colour.
        out[..., 1] = out[..., 0]
        out[..., 2] = out[..., 0]
    return out


@lru_cache(maxsize=64)
def _table(size: int, lo: float, hi: float, invert: bool) -> np.ndarray:
    """The stretch as a lookup, one entry per possible pixel value."""
    values = np.arange(size, dtype=np.float64)
    span = hi - lo
    scaled = (np.zeros(size, np.float32) if span <= 0
              else np.clip((values - lo) / span, 0.0, 1.0))
    if invert:
        scaled = 1.0 - scaled
    return np.clip(scaled * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _screen_table(dtype, lo: float, hi: float, invert: bool) -> np.ndarray:
    # Rounded so that a redraw with imperceptibly different cuts still hits the
    # cache; a hundredth of a count cannot move an eight-bit result.
    return _table(256 if dtype == np.uint8 else 65536,
                  round(lo, 2), round(hi, 2), invert)


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
