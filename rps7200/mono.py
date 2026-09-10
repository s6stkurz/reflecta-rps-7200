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

**Why one channel and not an average.** Both were measured on a repeat pair at
900 dpi, which is the only way to separate the noise that averaging can remove
from the grain that it cannot:

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
  that is cleaner. Averaging it in trades real detail for a fraction of a
  percent of noise.

**Why green.** Green and blue are indistinguishable on random noise -- 1.548%
against 1.554% in the densest tenth -- and comparably sharp, while red is worse
on both counts. Green is the tie-break: it is the filter the hardware's own
single-filter mode uses, and the one the SANE backend found to be the only one
that works alone.
"""
from __future__ import annotations

import numpy as np

from .protocol import CHANNEL_ORDER, FILM_BW

#: Which channel a monochrome file carries. Green -- see the module docstring.
MONO_CHANNEL = "G"


def to_monochrome(image: np.ndarray, channel: str = MONO_CHANNEL) -> np.ndarray:
    """Reduce an ``(H, W, C)`` scan to the single ``(H, W)`` plane to deliver.

    Returns a copy, and leaves the input alone: the three-channel array is what
    the library files, and a merged channel cannot be un-merged.
    """
    if image.ndim == 2:
        return image.copy()
    if image.ndim != 3:
        raise ValueError(f"expected a 2-D or 3-D image, got shape {image.shape}")

    order = list(CHANNEL_ORDER[: image.shape[2]])
    if channel not in order:
        raise ValueError(
            f"channel {channel!r} is not in this scan; it has {order}"
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
