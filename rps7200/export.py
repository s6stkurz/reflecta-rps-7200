"""Writing the files the *operator* asked for, in the format they asked for.

This is the delivery path, and it is deliberately not the library path. An
entry keeps raw pixels, the shading reference and the raw bytes so a scan can
be re-derived for ever (see :mod:`rps7200.library`); what leaves through here
is a finished file for somebody to use, and it is allowed to be lossy because
the thing it was made from is not going anywhere.

So `tiff.write` stays exactly what the library writes with, and everything
delivered -- the window's output folder, Save as ..., `tools/scan.py` -- goes
through :func:`write` instead, which picks the format from the filename.

**A JPEG here holds the same picture as the TIFF, reduced to eight bits.** Not
inverted, not stretched. The window's rule is that "the inversion in this
window is for your eyes only and never reaches disk" -- NegPy does the
inverting, and a file that had already been inverted and stretched would be a
different kind of thing wearing the same name. The cost is that a JPEG of a
colour negative looks like an orange negative, which is the intended trade:
one meaning of "the scan" on disk, in two container formats.

**Infrared leaves in a DNG beside the JPEG.** Three channels is all a JPEG
has, and the fourth plane is the reason an infrared pass costs its ~212 s
floor, so it is written as a four-sample LinearRaw DNG next to the picture --
the container NegPy reads an infrared plane out of. That is the only place it
can go: NegPy's JPEG loader reports no infrared whatever sits next to the
file, and its sidecar convention wants a TIFF as the main image. A TIFF
delivery needs none of this, carrying the plane in-band as a fourth sample.
See :mod:`rps7200.dng`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import dng, tiff

#: What each format is called on disk. The key is what a setting stores; the
#: value is what a filename ends with.
SUFFIXES = {"tiff": ".tif", "jpeg": ".jpg"}

#: Every suffix :func:`write` will accept, mapped to its format. `.tiff` and
#: `.jpeg` are here because people type them, not because anything writes them.
FORMATS = {".tif": "tiff", ".tiff": "tiff", ".jpg": "jpeg", ".jpeg": "jpeg"}

#: Visually lossless for photographs, and about a tenth the size of the 8-bit
#: TIFF. Overridable per call; the window remembers its own value.
DEFAULT_QUALITY = 95

#: Most channels a JPEG can carry. Three, or one for greyscale -- there is no
#: fourth, which is what sends the infrared plane to a DNG of its own.
JPEG_MAX_CHANNELS = 3


def suffix_for(fmt: str) -> str:
    """`.tif` or `.jpg`. Raises on anything else rather than guessing."""
    try:
        return SUFFIXES[fmt]
    except KeyError:
        raise ValueError(
            f"unknown format {fmt!r}; expected one of {sorted(SUFFIXES)}"
        ) from None


def format_of(path: str | Path) -> str:
    """Which format this filename asks for. Raises if it does not say.

    Deliberately strict. A silent default here would write a TIFF into a file
    called `.png` and leave the operator to find out later.
    """
    ext = Path(path).suffix.lower()
    try:
        return FORMATS[ext]
    except KeyError:
        raise ValueError(
            f"cannot tell what format {Path(path).name!r} should be; "
            f"expected one of {sorted(FORMATS)}"
        ) from None


def to_8bit(image: np.ndarray) -> np.ndarray:
    """A 16-bit pass reduced to 8, or an 8-bit one untouched.

    A plain shift, which is the faithful reduction: metering targets 80% of
    full scale (see `EXPOSURE_TARGET` in :mod:`rps7200.direct`), so the top of
    the range is genuinely occupied and there is no headroom to reclaim. A
    percentile stretch would look better and would no longer be the same
    picture as the TIFF beside it, which is the one thing this must not be.

    Prescans are already 8-bit, so they pass straight through.
    """
    if image.dtype == np.uint8:
        return image
    if image.dtype != np.uint16:
        raise ValueError(f"expected uint8 or uint16, got {image.dtype}")
    return (image >> 8).astype(np.uint8)


def infrared_path(path: str | Path) -> Path:
    """Where the infrared plane goes for a JPEG at ``path``.

    The same stem, so the two files sort together and read as one scan. The
    picture's own `_ir` tag, where `_out_name` added one, stays on both: it
    says what was *scanned*, and both files came off that pass.
    """
    return Path(path).with_suffix(dng.SUFFIX)


def _write_jpeg(path: Path, image: np.ndarray, quality: int) -> None:
    """The picture alone. Anything past three channels leaves in the DNG."""
    from PIL import Image                                # noqa: PLC0415

    if image.ndim == 3 and image.shape[2] > JPEG_MAX_CHANNELS:
        image = image[:, :, :JPEG_MAX_CHANNELS]
    if image.ndim == 3 and image.shape[2] == 1:
        # PIL reads (H, W, 1) as nothing it knows; greyscale is (H, W).
        image = image[:, :, 0]
    Image.fromarray(np.ascontiguousarray(to_8bit(image))).save(
        path,
        quality=int(quality),
        # No chroma subsampling. The default throws away half the colour
        # resolution, which on a negative's orange mask is a visible loss for a
        # few percent of file size.
        subsampling=0,
        optimize=True,
    )


def _write_infrared(path: Path, image: np.ndarray, resolution: int | None) -> str:
    """The plane the JPEG could not take, in the container NegPy reads it from.

    Written *after* the JPEG and never allowed to raise, which is the same line
    `FrameWriter` takes about a frame that cannot be filed: the picture is on
    disk by now and the library entry is still to come, so a full disk or a
    read-only folder must cost the infrared plane and not the scan. The
    operator is told either way -- the note goes to the window's log and to
    `tools/scan.py`'s output -- so nothing here is lost quietly.
    """
    # `preview.has_infrared` answers the same question and is deliberately not
    # called: what matters here is not "is there infrared" but "is there a plane
    # this container cannot hold", and the two coincide only while JPEG is the
    # lossy format on offer.
    if image.ndim != 3 or image.shape[2] <= JPEG_MAX_CHANNELS:
        return ""
    companion = infrared_path(path)
    channels = image.shape[2]
    try:
        dng.write(companion, image[:, :, :dng.CHANNELS], resolution=resolution)
    except Exception as exc:                             # noqa: BLE001
        return (f"infrared does not fit in a JPEG and {companion.name} could not "
                f"be written ({exc}); the library entry keeps all {channels} "
                f"channels")
    return (f"infrared does not fit in a JPEG, so it is in {companion.name} "
            f"beside it -- that is the file to open for dust removal")


def write(
    path: str | Path,
    image: np.ndarray,
    *,
    resolution: int | None = None,
    quality: int = DEFAULT_QUALITY,
) -> str:
    """Write one delivered file, in the format its name asks for.

    Returns a note worth showing the operator, or ``""``. A note is not a
    failure: it is something the chosen format could not carry, and the caller
    is expected to log it rather than swallow it.

    A four-channel pass delivered as JPEG leaves **two** files -- the picture,
    and `<stem>.dng` holding R, G, B and infrared at full depth. A TIFF
    delivery leaves one, the plane riding along as a fourth sample.

    **A JPEG asked for without Pillow is written as a TIFF instead**, with a
    note saying so. A scan costs minutes of hardware and an optional package
    that is not installed is no reason to lose one -- the same line
    `FrameWriter` takes about a frame that cannot be filed. No DNG is written
    in that case: the TIFF it fell back to carries the plane itself.
    """
    path = Path(path)
    fmt = format_of(path)
    if fmt == "jpeg":
        try:
            _write_jpeg(path, image, quality)
        except ImportError:
            path = path.with_suffix(SUFFIXES["tiff"])
            tiff.write(str(path), image, resolution=resolution)
            return (f"Pillow is not installed, so this was written as "
                    f"{path.name} instead. `uv sync --extra jpeg` adds it.")
        return _write_infrared(path, image, resolution)
    tiff.write(str(path), image, resolution=resolution)
    return ""
