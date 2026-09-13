"""Read and write multi-channel TIFFs, including 4-channel RGBI.

Written by hand rather than via Pillow because Pillow will not round-trip a
16-bit four-sample image: the infrared plane has to be declared through
``ExtraSamples``, and the whole point here is that it survives untouched.

Writes are losslessly compressed (deflate + horizontal differencing) where
``tifffile`` is installed, and uncompressed otherwise; **both paths read
either**. The asymmetry is deliberate and the reader is the half that matters:
a file this package writes must open without the optional dependency that
wrote it. Compression changes the size of the file and nothing else -- not the
pixels, and not the size of the array once loaded, so it buys disk space and
not speed.

``tifffile`` is used automatically when it is installed; the built-in path is a
dependency-free equivalent. *Equivalent* is the load-bearing word: which one
runs is an installation accident, so the two must not disagree about what comes
back. Both therefore return a **writeable, native-byte-order** array, and both
treat a one-sample file as a 2D ``(H, W)`` image -- TIFF cannot record the
difference between ``(H, W)`` and ``(H, W, 1)``, so :func:`read` settles it the
way every other reader does rather than letting it depend on tifffile's private
shape metadata. ``tests/test_tiff.py`` runs every write/read pairing of the two
implementations against each other.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from typing import BinaryIO, cast

import numpy as np

# Tag numbers
_IMAGE_WIDTH = 256
_IMAGE_LENGTH = 257
_BITS_PER_SAMPLE = 258
_COMPRESSION = 259
_PHOTOMETRIC = 262
_STRIP_OFFSETS = 273
_SAMPLES_PER_PIXEL = 277
_ROWS_PER_STRIP = 278
_STRIP_BYTE_COUNTS = 279
_X_RESOLUTION = 282
_Y_RESOLUTION = 283
_PLANAR_CONFIG = 284
_PREDICTOR = 317
_RESOLUTION_UNIT = 296
_SOFTWARE = 305
_EXTRA_SAMPLES = 338
_SAMPLE_FORMAT = 339
# Only named so the reader can refuse a tiled file by name instead of dying on a
# missing StripOffsets.
_TILE_WIDTH = 322
_TILE_OFFSETS = 324

# Field types
_ASCII = 2
_SHORT = 3
_LONG = 4
_RATIONAL = 5

_TYPE_SIZE = {1: 1, _ASCII: 1, _SHORT: 2, _LONG: 4, _RATIONAL: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

_PHOTOMETRIC_MINISBLACK = 1
_PHOTOMETRIC_RGB = 2

_COMPRESSION_NONE = 1
#: 8 is the TIFF 6 "Adobe-style Deflate"; 32946 is the older private tag for
#: the same zlib stream. Writers differ about which they emit -- tifffile uses
#: 8 -- and they decompress identically, so both are accepted.
_COMPRESSION_DEFLATE = 8
_COMPRESSION_DEFLATE_LEGACY = 32946
_COMPRESSION_READABLE = (
    _COMPRESSION_NONE, _COMPRESSION_DEFLATE, _COMPRESSION_DEFLATE_LEGACY
)

_PREDICTOR_NONE = 1
_PREDICTOR_HORIZONTAL = 2

_SAMPLE_FORMAT_UINT = 1

_TARGET_STRIP_BYTES = 8 << 20  # ~8 MiB per strip

_SOFTWARE_NAME = "rps7200"


def _has_tifffile() -> bool:
    try:
        import tifffile  # noqa: F401
    except ImportError:
        return False
    return True


def write(
    path: str,
    image: np.ndarray,
    resolution: int | None = None,
    software: str = _SOFTWARE_NAME,
    compress: bool = True,
) -> None:
    """Write ``image`` as a TIFF, losslessly compressed where possible.

    ``image`` is ``(H, W)`` or ``(H, W, C)`` of uint8 or uint16. Channels beyond
    the third are tagged as unspecified extra samples, which is how the IR plane
    is carried in a 4-channel file.

    ``compress`` asks for deflate with horizontal differencing -- measured at
    -16% on a 1800 dpi frame, lossless, and costing only CPU on write. It is
    honoured on the ``tifffile`` path; the built-in writer ignores it and
    always writes uncompressed, which is a deliberate asymmetry: *reading*
    compressed files is mandatory or files become unopenable without an
    optional dependency, but a second hand-written compressor would be code
    with no benefit. Both writers produce identical pixels; only the bytes on
    disk differ, and nothing depends on those.

    ``compress=False`` restores byte-for-byte what this wrote before
    compression existed, which is what to reach for when debugging interop.
    """
    if image.ndim == 2:
        image = image[:, :, None]
    if image.ndim != 3:
        raise ValueError(f"expected a 2D or 3D array, got shape {image.shape}")
    if image.dtype not in (np.uint8, np.uint16):
        raise ValueError(f"expected uint8 or uint16, got {image.dtype}")
    if image.size == 0:
        # TIFF has no conformant way to say "no pixels", and tifffile only warns
        # and then writes a file it cannot read back. Refuse on both paths.
        raise ValueError(f"cannot write an empty image, shape {image.shape}")

    if _has_tifffile():
        import tifffile

        channels = image.shape[2]
        kwargs: dict[str, object] = {
            "photometric": "rgb" if channels >= 3 else "minisblack",
        }
        if channels > 3:
            kwargs["extrasamples"] = ["unspecified"] * (channels - 3)
        if resolution:
            kwargs["resolution"] = (resolution, resolution)
            kwargs["resolutionunit"] = "inch"
        if compress:
            # Predictor 2 is what makes it worth having: plain deflate on raw
            # sensor data is -7%, differencing along x first takes it to -16%.
            kwargs["compression"] = "zlib"
            kwargs["predictor"] = True
        # Otherwise the Software tag says "tifffile.py" or "rps7200" depending
        # on what happened to be installed when the scan was written.
        kwargs["software"] = software
        # Hand a single-channel image over as (H, W), not (H, W, 1). tifffile
        # records the array's own shape so it can round-trip it exactly, so a
        # trailing length-1 axis comes back on every read -- and a consumer
        # that keys on `ndim == 2` to recognise greyscale then misses it.
        # NegPy is exactly such a consumer, and this is what decides whether a
        # black and white scan is processed as black and white.
        out = image[:, :, 0] if image.shape[2] == 1 else image
        tifffile.imwrite(path, np.ascontiguousarray(out), **kwargs)
        return

    with open(path, "wb") as fh:
        _write_builtin(fh, image, resolution, software)


def _write_builtin(
    fh: BinaryIO, image: np.ndarray, resolution: int | None, software: str
) -> None:
    height, width, channels = image.shape
    bits = 8 if image.dtype == np.uint8 else 16
    bytes_per_sample = bits // 8
    row_bytes = width * channels * bytes_per_sample

    rows_per_strip = max(1, min(height, _TARGET_STRIP_BYTES // max(row_bytes, 1)))
    n_strips = (height + rows_per_strip - 1) // rows_per_strip
    strip_counts = [
        min(rows_per_strip, height - i * rows_per_strip) * row_bytes
        for i in range(n_strips)
    ]

    # The header below says "II", so the samples have to be little-endian too.
    # Testing for ">" is not enough: a native uint16 reports "=", which is
    # big-endian on a big-endian host. Asking for "<" is a no-op where it
    # already holds and a conversion where it does not.
    data = np.ascontiguousarray(image, dtype=image.dtype.newbyteorder("<"))

    # Header, then pixel data, then IFD, then any values too big to inline.
    data_offset = 8
    data_size = int(sum(strip_counts))

    # Collect (tag, type, count, payload) first; out-of-line offsets can only be
    # resolved once the entry count is known, since it sets the IFD's size.
    fields: list[tuple[int, int, int, bytes]] = []

    # A TIFF value is a string, a list of integers, or -- for RATIONAL -- a list
    # of numerator/denominator pairs. The three are spelled out rather than
    # narrowed with a cast, so the reader can see which shape each tag sends.
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
            fmt = "<H" if ftype == _SHORT else "<I"
            words = cast("Sequence[int]", values)
            payload = b"".join(struct.pack(fmt, v) for v in words)
            count = len(words)
        fields.append((tag, ftype, count, payload))

    strip_offsets = []
    running = data_offset
    for count in strip_counts:
        strip_offsets.append(running)
        running += count

    add(_IMAGE_WIDTH, _LONG, [width])
    add(_IMAGE_LENGTH, _LONG, [height])
    add(_BITS_PER_SAMPLE, _SHORT, [bits] * channels)
    add(_COMPRESSION, _SHORT, [1])
    add(
        _PHOTOMETRIC,
        _SHORT,
        [_PHOTOMETRIC_RGB if channels >= 3 else _PHOTOMETRIC_MINISBLACK],
    )
    add(_STRIP_OFFSETS, _LONG, strip_offsets)
    add(_SAMPLES_PER_PIXEL, _SHORT, [channels])
    add(_ROWS_PER_STRIP, _LONG, [rows_per_strip])
    add(_STRIP_BYTE_COUNTS, _LONG, strip_counts)
    res = int(resolution) if resolution else 72
    add(_X_RESOLUTION, _RATIONAL, [(res, 1)])
    add(_Y_RESOLUTION, _RATIONAL, [(res, 1)])
    add(_PLANAR_CONFIG, _SHORT, [1])
    add(_RESOLUTION_UNIT, _SHORT, [2])
    add(_SOFTWARE, _ASCII, software.encode() + b"\x00")
    if channels > 3:
        # 0 = unspecified: the IR plane is data, not alpha, so nothing should
        # try to composite with it.
        add(_EXTRA_SAMPLES, _SHORT, [0] * (channels - 3))
    add(_SAMPLE_FORMAT, _SHORT, [1] * channels)

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


def read(path: str) -> np.ndarray:
    """Read a strip-based TIFF back into an array, compressed or not.

    ``(H, W, C)`` for a multi-sample file and ``(H, W)`` for a single-sample
    one -- so a 2D plane written on its own, as ``--split`` writes the IR,
    comes back 2D. Always writeable and in the host's byte order, whichever
    implementation ran.
    """
    if _has_tifffile():
        import tifffile

        image = np.asarray(tifffile.imread(path))
    else:
        with open(path, "rb") as fh:
            image = _read_builtin(fh)

    if image.ndim == 3 and image.shape[2] == 1:
        # tifffile keeps a trailing 1 when it wrote the file itself, from the
        # shape it stashes in ImageDescription; no other reader sees that, so
        # the file's own answer -- one sample, one plane -- wins.
        image = image[..., 0]
    return image


def _exact(fh: BinaryIO, size: int, what: str) -> bytes:
    """Read exactly ``size`` bytes or say which part of the file ran out.

    The IFD is written *after* the pixel data, so a file cut short loses its
    directory first; without this the failure surfaces as a bare
    ``struct.error`` about a 2-byte buffer, naming nothing.
    """
    data = fh.read(size)
    if len(data) != size:
        raise ValueError(
            f"truncated file: {what} wanted {size} bytes, only {len(data)} readable"
        )
    return data


def _read_builtin(fh: BinaryIO) -> np.ndarray:
    """Always ``(H, W, C)``; :func:`read` is what drops a lone channel axis."""
    magic = _exact(fh, 4, "the header")
    if magic[:2] == b"II":
        end = "<"
    elif magic[:2] == b"MM":
        end = ">"
    else:
        raise ValueError("not a TIFF file")
    if struct.unpack(end + "H", magic[2:4])[0] != 42:
        raise ValueError("not a classic TIFF file")
    (ifd_offset,) = struct.unpack(end + "I", _exact(fh, 4, "the IFD pointer"))

    fh.seek(ifd_offset)
    (n_entries,) = struct.unpack(end + "H", _exact(fh, 2, "the IFD entry count"))
    tags: dict[int, list[int]] = {}
    for _ in range(n_entries):
        tag, ftype, count = struct.unpack(end + "HHI", _exact(fh, 8, "an IFD entry"))
        raw = _exact(fh, 4, "an IFD entry value")
        size = _TYPE_SIZE.get(ftype, 1) * count
        if size > 4:
            (offset,) = struct.unpack(end + "I", raw)
            here = fh.tell()
            fh.seek(offset)
            raw = _exact(fh, size, f"the out-of-line value for tag {tag}")
            fh.seek(here)
        if ftype == _SHORT:
            tags[tag] = list(struct.unpack(end + "H" * count, raw[: 2 * count]))
        elif ftype == _LONG:
            tags[tag] = list(struct.unpack(end + "I" * count, raw[: 4 * count]))
        elif ftype == _ASCII:
            tags[tag] = list(raw[:count])

    def one(tag: int, default: int | None = None) -> int:
        if tag in tags and tags[tag]:
            return tags[tag][0]
        if default is None:
            raise ValueError(f"missing required TIFF tag {tag}")
        return default

    # Everything refused here names what it found, because the alternative is a
    # reader that dies on a KeyError or, worse, returns plausible nonsense.
    compression = one(_COMPRESSION, _COMPRESSION_NONE)
    if compression not in _COMPRESSION_READABLE:
        raise ValueError(
            f"unsupported TIFF compression {compression}; only uncompressed "
            f"(1) and deflate (8, 32946) are read"
        )
    predictor = one(_PREDICTOR, _PREDICTOR_NONE)
    if predictor not in (_PREDICTOR_NONE, _PREDICTOR_HORIZONTAL):
        # 3 is the floating-point predictor. We never write float samples, so
        # meeting one means the file is not ours and guessing would be worse
        # than stopping.
        raise ValueError(
            f"unsupported TIFF predictor {predictor}; only none (1) and "
            f"horizontal differencing (2) are read"
        )
    planar = one(_PLANAR_CONFIG, 1)
    if planar != 1:
        raise ValueError(
            f"unsupported planar configuration {planar}; only interleaved (chunky, 1)"
        )
    if _TILE_OFFSETS in tags or _TILE_WIDTH in tags:
        raise ValueError("tiled TIFFs are not supported; only strip-based ones")
    if _STRIP_OFFSETS not in tags or _STRIP_BYTE_COUNTS not in tags:
        raise ValueError("missing strip offsets or byte counts: not a strip-based TIFF")

    width = one(_IMAGE_WIDTH)
    height = one(_IMAGE_LENGTH)
    channels = one(_SAMPLES_PER_PIXEL, 1)
    bits = tags.get(_BITS_PER_SAMPLE, [8])
    if len(set(bits)) != 1 or bits[0] not in (8, 16):
        raise ValueError(f"unsupported bits per sample: {bits}")
    formats = set(tags.get(_SAMPLE_FORMAT, [_SAMPLE_FORMAT_UINT]))
    if formats != {_SAMPLE_FORMAT_UINT}:
        # float16 and int16 are the same width as the uint16 we expect, so
        # without this they would decode silently into wrong numbers.
        raise ValueError(
            f"unsupported sample format {sorted(formats)}; only unsigned integer (1)"
        )
    dtype = np.dtype(np.uint8) if bits[0] == 8 else np.dtype(end + "u2")

    offsets = tags[_STRIP_OFFSETS]
    counts = tags[_STRIP_BYTE_COUNTS]
    # A bytearray rather than joined bytes: np.frombuffer over an immutable
    # buffer yields a read-only array, and tifffile's does not, so a caller that
    # writes into the result would break only where tifffile is missing.
    buffer = bytearray()
    for offset, count in zip(offsets, counts):
        fh.seek(offset)
        chunk = fh.read(count)
        if len(chunk) != count:
            raise ValueError(
                f"truncated file: strip at offset {offset} claims {count} bytes, "
                f"only {len(chunk)} readable"
            )
        if compression != _COMPRESSION_NONE:
            # Per strip, not over the concatenation: StripByteCounts is the
            # *compressed* length and each strip is its own zlib stream.
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error as exc:
                raise ValueError(
                    f"corrupt deflate strip at offset {offset}: {exc}"
                ) from exc
        buffer += chunk

    expected = height * width * channels * dtype.itemsize
    if len(buffer) < expected:
        raise ValueError(
            f"truncated image data: {len(buffer)} bytes for "
            f"{height}x{width}x{channels} at {bits[0]} bits, expected {expected}"
        )
    # A writer is allowed to pad the final strip out to a whole rows-per-strip.
    del buffer[expected:]

    image = np.frombuffer(buffer, dtype=dtype).reshape(height, width, channels)
    native = np.dtype(dtype.kind + str(dtype.itemsize))
    if dtype != native:
        image = image.astype(native)  # a big-endian file, read on a little host

    if predictor == _PREDICTOR_HORIZONTAL:
        # Each row was stored as differences along x, per channel. Undoing it
        # is a running sum across the width -- and it has to accumulate *in the
        # sample dtype*, so it wraps modulo 2**16 exactly as the writer's
        # subtraction did. Letting numpy widen the accumulator would turn every
        # wrapped difference into a huge positive number.
        #
        # Rows never span strips (RowsPerStrip is a whole number of rows), so
        # doing this over the assembled image is identical to doing it per
        # strip, with one fewer place to get the boundary wrong.
        image = np.cumsum(image, axis=1, dtype=image.dtype)
    return image
