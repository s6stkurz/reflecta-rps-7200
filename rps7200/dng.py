"""Writing a LinearRaw DNG -- the one delivered container that carries infrared.

A JPEG holds three channels. An RGBI pass has four, and the fourth is the whole
reason infrared costs its ~212 s floor: it is what dust removal runs on. So when
the operator asks for JPEG and the scan has an infrared plane, the plane does not
have to be thrown away -- it goes into a DNG written beside the JPEG, and the
choice of the smaller container for the *picture* costs nothing that matters.

**DNG because that is what the consumer reads.** NegPy's ``RawpyLoader`` looks
for a four-sample LinearRaw page *before* it hands anything to libraw, and
returns the first three samples as the picture and the fourth as ``ir`` --
``negpy/infrastructure/loaders/rawpy_loader.py``, ``_peek_linearraw_4ch``. Its
JPEG loader, by contrast, sets ``ir`` to ``None`` unconditionally and never looks
for a sidecar at all, so no file placed next to a JPEG can reach it. That
asymmetry is the whole argument for this module: a four-sample TIFF already
carries infrared in-band and needs nothing, and a JPEG can carry it *only* by
having a DNG of its own.

The tag set below is the one NegPy's own ``write_dng_linear`` emits, because that
is the file its reader is known to open, plus ``UniqueCameraModel``, which the
DNG specification requires and nothing here reads.

**Written by hand rather than through :func:`rps7200.tiff.write`.** A DNG is a
TIFF -- one IFD, uncompressed, a few extra tags -- so the temptation is real, but
three things differ at once: the photometric, the ``ExtraSamples`` count (a
LinearRaw page implies *one* colour sample, so a four-sample file declares three
extra where an RGB one declares one), and the DNG tags themselves. ``tiff.py`` is
what every library entry is written with, and it is the last file in this package
that should grow a second mode for the sake of a delivered file. The duplication
is paid for by a cross-check instead: a DNG *is* a TIFF, so ``tiff.read`` reads
one back, and the tests assert it returns exactly the array that went in.

**Uncompressed, deliberately.** DNG blesses deflate for floating-point samples
only; for the 16-bit integers here the conformant choices are "none" and lossless
JPEG, and nothing in this package encodes the latter. NegPy would read a deflated
one -- it goes through ``tifffile`` -- but a file that only one reader in the
world accepts should not be wearing the ``.dng`` extension. The cost is measured
and worth stating: a 3600 dpi RGBI frame is 142 MB here against 117 MB as a
compressed TIFF (``docs/tiff-compression-plan.md``), so asking for JPEG *and*
getting infrared uses more disk than the TIFF would have, not less.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, cast

import numpy as np

# Tag numbers. Spelled out again rather than imported from `tiff.py` so this
# file reads as one complete description of one container format.
_NEW_SUBFILE_TYPE = 254
_IMAGE_WIDTH = 256
_IMAGE_LENGTH = 257
_BITS_PER_SAMPLE = 258
_COMPRESSION = 259
_PHOTOMETRIC = 262
_MAKE = 271
_MODEL = 272
_STRIP_OFFSETS = 273
_ORIENTATION = 274
_SAMPLES_PER_PIXEL = 277
_ROWS_PER_STRIP = 278
_STRIP_BYTE_COUNTS = 279
_X_RESOLUTION = 282
_Y_RESOLUTION = 283
_PLANAR_CONFIG = 284
_RESOLUTION_UNIT = 296
_SOFTWARE = 305
_EXTRA_SAMPLES = 338
_SAMPLE_FORMAT = 339
_DNG_VERSION = 50706
_DNG_BACKWARD_VERSION = 50707
_UNIQUE_CAMERA_MODEL = 50708

# Field types
_BYTE = 1
_ASCII = 2
_SHORT = 3
_LONG = 4
_RATIONAL = 5

#: TIFF/EP's "linear raw": demosaiced sensor samples in the camera's own space,
#: with no colour interpretation attached. It is what makes this a DNG rather
#: than a four-sample TIFF, and it is one of the two tags NegPy matches on.
_PHOTOMETRIC_LINEAR_RAW = 34892

#: DNG 1.4.0.0, and readable by anything that understands 1.0.0.0. Four bytes
#: each, most significant first, which is why they are BYTE and not LONG.
_VERSION = (1, 4, 0, 0)
_BACKWARD_VERSION = (1, 0, 0, 0)

#: Four samples, R G B IR. Not a maximum and not a default: a three-sample
#: LinearRaw DNG would be handed to libraw by the very reader this exists for,
#: which is a different and much less certain path, so :func:`write` refuses
#: anything else rather than producing a file nobody asked for.
CHANNELS = 4

_MAKE_NAME = "Reflecta"
_MODEL_NAME = "RPS 7200"
_UNIQUE_MODEL_NAME = f"{_MAKE_NAME} {_MODEL_NAME}"
_SOFTWARE_NAME = "rps7200"

_TARGET_STRIP_BYTES = 8 << 20  # ~8 MiB per strip, as `tiff.py` uses

_SAMPLE_FORMAT_UINT = 1
_ORIENTATION_TOPLEFT = 1
_COMPRESSION_NONE = 1
_PLANAR_CONTIG = 1
_RESOLUTION_UNIT_INCH = 2
_NEW_SUBFILE_TYPE_FULL = 0  # not a thumbnail, not a mask -- LibRaw insists

#: What one of these is called on disk.
SUFFIX = ".dng"


def write(
    path: str | Path,
    image: np.ndarray,
    resolution: int | None = None,
    software: str = _SOFTWARE_NAME,
) -> None:
    """Write ``image`` as an uncompressed LinearRaw DNG.

    ``image`` is ``(H, W, 4)`` of uint8 or uint16 -- R, G, B, IR, in the
    scanner's own linear samples, corrected the same way everything else
    delivered is corrected. The samples are written as they arrive: an 8-bit
    array stays 8-bit rather than being stretched into 16, because the reader
    scales by the dtype's own maximum and inventing precision here would only
    make the file bigger.
    """
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[2] != CHANNELS:
        raise ValueError(
            f"a LinearRaw DNG here carries {CHANNELS} samples (R, G, B, IR); "
            f"got shape {image.shape}"
        )
    if image.dtype not in (np.uint8, np.uint16):
        raise ValueError(f"expected uint8 or uint16, got {image.dtype}")
    if image.size == 0:
        raise ValueError(f"cannot write an empty image, shape {image.shape}")

    with open(path, "wb") as fh:
        _write(fh, image, resolution, software)


def _write(
    fh: BinaryIO, image: np.ndarray, resolution: int | None, software: str
) -> None:
    height, width, channels = image.shape
    bits = 8 if image.dtype == np.uint8 else 16
    row_bytes = width * channels * (bits // 8)

    rows_per_strip = max(1, min(height, _TARGET_STRIP_BYTES // max(row_bytes, 1)))
    n_strips = (height + rows_per_strip - 1) // rows_per_strip
    strip_counts = [
        min(rows_per_strip, height - i * rows_per_strip) * row_bytes
        for i in range(n_strips)
    ]

    # The header says "II", so the samples have to be little-endian too. A
    # native uint16 reports "=", which is big-endian on a big-endian host, so
    # asking for "<" is the only test that holds everywhere.
    data = np.ascontiguousarray(image, dtype=image.dtype.newbyteorder("<"))

    # Header, then pixel data, then the IFD, then any values too big to inline.
    data_offset = 8
    data_size = int(sum(strip_counts))

    fields: list[tuple[int, int, int, bytes]] = []

    def add(
        tag: int,
        ftype: int,
        values: bytes | Sequence[int] | Sequence[tuple[int, int]],
    ) -> None:
        if ftype == _ASCII:
            assert isinstance(values, bytes)
            payload, count = values, len(values)
        elif ftype == _RATIONAL:
            pairs = cast("Sequence[tuple[int, int]]", values)
            payload = b"".join(struct.pack("<II", num, den) for num, den in pairs)
            count = len(pairs)
        else:
            fmt = {_BYTE: "<B", _SHORT: "<H"}.get(ftype, "<I")
            words = cast("Sequence[int]", values)
            payload = b"".join(struct.pack(fmt, v) for v in words)
            count = len(words)
        fields.append((tag, ftype, count, payload))

    strip_offsets = []
    running = data_offset
    for count in strip_counts:
        strip_offsets.append(running)
        running += count

    add(_NEW_SUBFILE_TYPE, _LONG, [_NEW_SUBFILE_TYPE_FULL])
    add(_IMAGE_WIDTH, _LONG, [width])
    add(_IMAGE_LENGTH, _LONG, [height])
    add(_BITS_PER_SAMPLE, _SHORT, [bits] * channels)
    add(_COMPRESSION, _SHORT, [_COMPRESSION_NONE])
    add(_PHOTOMETRIC, _SHORT, [_PHOTOMETRIC_LINEAR_RAW])
    add(_MAKE, _ASCII, _MAKE_NAME.encode() + b"\x00")
    add(_MODEL, _ASCII, _MODEL_NAME.encode() + b"\x00")
    add(_STRIP_OFFSETS, _LONG, strip_offsets)
    # The picture is already the way up it was asked to be -- `FrameWriter`
    # turns it before anything is written -- so the file says "no further
    # rotation" rather than repeating the turn as a tag a reader might apply
    # a second time.
    add(_ORIENTATION, _SHORT, [_ORIENTATION_TOPLEFT])
    add(_SAMPLES_PER_PIXEL, _SHORT, [channels])
    add(_ROWS_PER_STRIP, _LONG, [rows_per_strip])
    add(_STRIP_BYTE_COUNTS, _LONG, strip_counts)
    res = int(resolution) if resolution else 72
    add(_X_RESOLUTION, _RATIONAL, [(res, 1)])
    add(_Y_RESOLUTION, _RATIONAL, [(res, 1)])
    add(_PLANAR_CONFIG, _SHORT, [_PLANAR_CONTIG])
    add(_RESOLUTION_UNIT, _SHORT, [_RESOLUTION_UNIT_INCH])
    add(_SOFTWARE, _ASCII, software.encode() + b"\x00")
    # Three, not one. A LinearRaw page implies a single colour sample, so every
    # plane past the first is "extra" -- which is how tifffile writes it and
    # therefore what NegPy's reader has seen. An RGB TIFF of the same array
    # declares one extra sample instead; see `tiff.py`.
    add(_EXTRA_SAMPLES, _SHORT, [0] * (channels - 1))
    add(_SAMPLE_FORMAT, _SHORT, [_SAMPLE_FORMAT_UINT] * channels)
    add(_DNG_VERSION, _BYTE, list(_VERSION))
    add(_DNG_BACKWARD_VERSION, _BYTE, list(_BACKWARD_VERSION))
    add(_UNIQUE_CAMERA_MODEL, _ASCII, _UNIQUE_MODEL_NAME.encode() + b"\x00")

    fields.sort(key=lambda f: f[0])

    ifd_offset = data_offset + data_size
    # 2-byte entry count + 12 bytes per entry + 4-byte next-IFD pointer
    extras_base = ifd_offset + 2 + 12 * len(fields) + 4

    entries: list[tuple[int, int, int, bytes]] = []
    extras = bytearray()
    for tag, ftype, count, payload in fields:
        if len(payload) <= 4:
            slot = payload + b"\x00" * (4 - len(payload))
        else:
            slot = struct.pack("<I", extras_base + len(extras))
            extras.extend(payload)
            if len(payload) % 2:  # keep word alignment
                extras.extend(b"\x00")
        entries.append((tag, ftype, count, slot))

    fh.write(b"II")
    fh.write(struct.pack("<HI", 42, ifd_offset))
    fh.write(data.tobytes())
    fh.write(struct.pack("<H", len(entries)))
    for tag, ftype, count, slot in entries:
        fh.write(struct.pack("<HHI", tag, ftype, count))
        fh.write(slot)
    fh.write(struct.pack("<I", 0))
    fh.write(bytes(extras))
