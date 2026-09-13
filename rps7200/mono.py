"""Black and white: one channel, chosen by measurement.

The scanner has no monochrome mode worth using. `passes = 0x04` selects the
green filter alone, but it returns untagged PIXEL-format data the deinterleave
cannot read -- `pieusb` lists it unsupported for that reason -- and it still
costs a full colour pass. CyberView agrees: a whole black and white session in
`captures/bw.pcapng` is eight passes and every one is RGB.

So a black and white scan *is* an RGB scan, and turning it into one channel is
a host-side step. This is that step.

**Why it has to happen here rather than downstream.** NegPy classifies a scan
by the minimum correlation between its channels, monochrome above 0.99. Across
this library black and white spans 0.926-0.988 and colour negative 0.008-0.976:
they overlap, so no threshold separates them and every black and white scan is
processed as colour. Its loader does one thing that saves us
(``negpy/infrastructure/loaders/tiff_loader.py``)::

    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)

A single-channel TIFF becomes three identical channels, whose correlation is
exactly 1.0, and the classification then cannot go wrong. Nothing downstream
has to change.

**The default is the average of R, G and B, and single channels are offered
beside it.** What each costs was measured on a repeat pair at 900 dpi, which is
the only way to separate the noise averaging can remove from the grain it
cannot:

* The *random* part does average down, near the ideal: the mean of R, G and B
  cut it 32.7% against green alone, against the 42% perfect independence would
  give.
* But the random part is only 12-18% of the high-frequency content. The rest is
  grain, and in a silver emulsion that is the *same* grain in all three
  channels, so it does not average at all. Net effect on what the eye sees:
  under 1%.
* Against that, red is soft. Measured at 3600 dpi it carries less
  high-frequency content than green or blue at the dense end *and* the thin
  end, which is the signature of a channel that resolves less rather than one
  that is cleaner. Averaging it in trades a little real detail for that
  fraction of a percent of noise.

So the average is the *default* rather than the verdict: it uses everything the
scanner captured and needs no argument about which channel deserves to win,
which is worth more than the sharpness it gives up on most frames. Where that
sharpness matters, pick a channel.

**If you are picking one, pick green or blue.** They are indistinguishable on
random noise -- 1.548% against 1.554% in the densest tenth -- and comparably
sharp, while red is worse on both counts. Green is the better tie-break: it is
the filter the hardware's own single-filter mode uses, and the one the SANE
backend found to be the only one that works alone.

**Nothing is lost either way.** The library files all three channels with the
raw bytes, so a merge is a delivery choice and never a destructive one.
"""
from __future__ import annotations

import numpy as np

from .protocol import CHANNEL_ORDER, FILM_BW

#: The average of R, G and B, as a value for the ``channel`` argument.
MONO_AVERAGE = "avg"

#: What a monochrome file carries by default. See the module docstring: the
#: average gives up a little sharpness for a noise gain under 1%, and is the
#: default because it needs no argument about which channel deserves to win.
MONO_CHANNEL = MONO_AVERAGE

#: Everything that can be asked for, in the order a chooser should offer it.
MONO_CHOICES = (MONO_AVERAGE, "R", "G", "B")

#: How many leading channels the average covers. Infrared is never one of
#: them: it is not a record of the picture, it is the dust plane.
_VISIBLE = 3


def to_monochrome(image: np.ndarray, channel: str = MONO_CHANNEL) -> np.ndarray:
    """Reduce an ``(H, W, C)`` scan to the single ``(H, W)`` plane to deliver.

    ``channel`` is one of :data:`MONO_CHOICES` -- ``"avg"`` for the mean of the
    visible channels, or a channel name for that one alone.

    Returns a copy, and leaves the input alone: the three-channel array is what
    the library files, and a merged channel cannot be un-merged.
    """
    if image.ndim == 2:
        return image.copy()
    if image.ndim != 3:
        raise ValueError(f"expected a 2-D or 3-D image, got shape {image.shape}")

    if channel == MONO_AVERAGE:
        # Infrared is excluded even when present: averaging the dust plane into
        # the picture would be a different kind of mistake entirely.
        visible = image[..., :_VISIBLE]
        if visible.shape[2] == 1:
            return visible[..., 0].copy()
        mean = visible.astype(np.float64).mean(axis=2)
        if np.issubdtype(image.dtype, np.integer):
            info = np.iinfo(image.dtype)
            # floor(x + 0.5), matching how the rest of this driver rounds.
            return np.clip(np.floor(mean + 0.5), info.min, info.max).astype(
                image.dtype
            )
        return mean.astype(image.dtype)

    order = list(CHANNEL_ORDER[: image.shape[2]])
    if channel not in order:
        raise ValueError(
            f"channel {channel!r} is not in this scan; it has {order} "
            f"(or {MONO_AVERAGE!r} for their average)"
        )
    return image[..., order.index(channel)].copy()


def wants_mono(asked: bool | None, film: str) -> bool:
    """Whether to deliver one channel, given what was asked and the film.

    ``None`` means "follow the film", which is the useful default: black and
    white is the case a consumer cannot work out for itself, and every other
    film is one where three channels are the point.
    """
    if asked is not None:
        return bool(asked)
    return film == FILM_BW
