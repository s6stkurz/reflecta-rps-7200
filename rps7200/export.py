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

import itertools
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
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


def outputs(path: str | Path) -> tuple[Path, ...]:
    """Every file :func:`write` may leave for this name.

    A JPEG's infrared goes to ``<stem>.dng`` beside it, and without Pillow the
    picture itself becomes ``<stem>.tif``: a name is free only when all of
    these are. Checking the one name asked for is how a Save all promising
    "nothing already there is overwritten" replaced an earlier export's DNG,
    or its TIFFs.
    """
    path = Path(path)
    if format_of(path) == "jpeg":
        return (path, infrared_path(path), path.with_suffix(SUFFIXES["tiff"]))
    return (path,)


# ---------------------------------------------------------------------------
# Names being written
# ---------------------------------------------------------------------------

#: How many names `claim` and `unclaimed` try before giving up: the one asked
#: for and ``-2`` to ``-999`` beside it. Past that the folder is not one a
#: person is choosing names in, and taking the asked-for name anyway -- what
#: the session's search used to do -- wrote over the file holding it.
FREE_NAMES = 1000

#: The names claimed and not yet released, in this process: each claimed
#: path's key, with the keys of every file its write may leave.
_claims: dict[str, frozenset[str]] = {}
_claims_lock = threading.Lock()


def _key(path: Path) -> str:
    """One spelling per file, as the platform compares names."""
    return os.path.normcase(os.path.abspath(path))


def _names(path: Path, sidecars: Sequence[str]) -> list[Path]:
    """Every file a write under ``path`` may leave: its `outputs`, or only
    itself for a name that is no delivery, and the caller's ``sidecars``."""
    try:
        names = list(outputs(path))
    except ValueError:
        names = [path]
    return names + [path.with_suffix(end) for end in sidecars]


def _candidates(wanted: Path, limit: int) -> Iterator[Path]:
    """``wanted``, then ``stem-2``, ``stem-3`` ... beside it."""
    yield wanted
    for n in range(2, limit):
        yield wanted.with_name(f"{wanted.stem}-{n}{wanted.suffix}")


def _held() -> frozenset[str]:
    """Every key a claim in flight holds. Call with `_claims_lock` held."""
    return frozenset().union(*_claims.values())


def unclaimed(wanted: str | Path, *, sidecars: Sequence[str] = (),
              limit: int = FREE_NAMES) -> Path:
    """``wanted``, or the next free name beside it, claiming nothing.

    Free means free for every file `write` may leave under the name -- a
    JPEG's companion DNG, its TIFF fallback -- and for ``sidecars``, the
    suffixes a caller writes beside the picture itself; any other name is
    only its own. And not held by a `claim` in flight, so a name offered here
    is not one a write in this process is about to take.

    Raises FileExistsError once ``limit`` names are taken. The one place
    this rule lives: the session's copies, the window's Save all, Export and
    Save As, and `tools/scan.py` all reach it, where three copies of it once
    each checked a different set of files.
    """
    wanted = Path(wanted)
    with _claims_lock:
        held = _held()
    for candidate in _candidates(wanted, limit):
        names = _names(candidate, sidecars)
        if any(_key(p) in held for p in names):
            continue
        if not any(p.exists() for p in names):
            return candidate
    raise FileExistsError(f"no free name beside {wanted}")


def claim(wanted: str | Path, *, sidecars: Sequence[str] = (),
          exact: bool = False, limit: int = FREE_NAMES) -> Path:
    """Reserve an output name until it is written: see `release`.

    The session reserved its output names when a pass was queued, and wrote
    them seconds later on its writer; Export, choosing the same names from
    the same roll, found them free in between and took them -- one of the
    two files then replaced the other. Every writer of delivered files in
    this process claims its name here first: the session's copies
    (`FrameWriter` releases them once its job is over), Save all, Export,
    Save As and `tools/scan.py`. A claimed name is not free to anyone else
    until released, so the second writer takes ``-2``.

    ``exact`` takes ``wanted`` as it is, whether or not a file is there:
    Save As, whose dialog has already asked about replacing it. Refused --
    FileExistsError -- only while another claim in flight holds it.

    Returns the claimed path. Raises FileExistsError when no name is free
    (`unclaimed`). The existence checks run outside the lock, so a slow
    disk holds up no other writer; only the insert is under it, and a name
    taken meanwhile is passed over.
    """
    wanted = Path(wanted)
    if exact:
        keys = frozenset(_key(p) for p in _names(wanted, sidecars))
        with _claims_lock:
            if keys & _held():
                raise FileExistsError(
                    f"{wanted.name} is being written by another save in "
                    "this window; try again once it has finished")
            _claims[_key(wanted)] = keys
        return wanted
    for candidate in _candidates(wanted, limit):
        names = _names(candidate, sidecars)
        if any(p.exists() for p in names):
            continue
        keys = frozenset(_key(p) for p in names)
        with _claims_lock:
            if keys & _held():
                continue
            _claims[_key(candidate)] = keys
        return candidate
    raise FileExistsError(f"no free name beside {wanted}")


def release(paths: Iterable[str | Path]) -> None:
    """Let claimed names go once their writes are over, written or not.

    A name never claimed is passed over, so a caller can release in a
    `finally` whatever it got to.
    """
    with _claims_lock:
        for path in paths:
            _claims.pop(_key(Path(path)), None)


#: Numbers each temporary name `temp_name` gives out in this process.
_temps = itertools.count(1)


def _base36(n: int) -> str:
    """``n`` in base 36, lower case: as short as a name can say it on a
    disk that does not tell case apart."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, d = divmod(n, 36)
        out = digits[d] + out
        if not n:
            return out


def temp_name(path: str | Path) -> Path:
    """Where `_whole` writes ``path`` before renaming it over it.

    Beside it, and this write's own: the process, and a count of the
    writes it has made. Derived from the final name alone, two writers of
    one name -- two windows, or Export beside the session -- wrote into one
    temporary file at once and renamed a torn file into place.

    Short, because it is the longest path a delivery makes, and on Windows
    without long paths a name past 259 characters is refused. The process
    and thread in decimal made it 18 characters longer than the final name
    here, and nearer 30 on Linux, whose thread numbers run to fifteen
    digits; in base 36, counting writes rather than naming threads, it is
    12 to 16. A check of whether a job's paths will fit measures this one,
    not the final names.
    """
    path = Path(path)
    with _claims_lock:
        n = next(_temps)
    return path.with_name(f".{path.stem}.{_base36(os.getpid())}-"
                          f"{_base36(n)}.part{path.suffix}")


def _whole(path: Path, write: Callable[[Path], object]) -> None:
    """Write through ``write`` beside ``path``, then rename it over.

    Every delivered file is written again under the same name -- a roll's
    frameNN.tif when a frame is retaken, Save As over an earlier export --
    and was written in place: a crash, a full disk or a lost drive part-way
    left a truncated file where a good one had been, and the retry the log
    suggested then found the name taken and put the good copy at `-2`. Now
    the name holds the old file or the new one, never half of either, and a
    failure leaves nothing behind. The temporary name keeps the suffix, which
    is what Pillow chooses the format by.

    The temporary name is this write's own (`temp_name`), so two writers of
    one name each write whole and the later rename wins. And it is flushed
    to the disk before the rename: renamed first, a power cut can leave the
    name on a file whose data never arrived, and an uncompressed strip of
    zeros reads as a picture.
    """
    from .library import _discard, _replace, _sync        # noqa: PLC0415

    temp = temp_name(path)
    try:
        write(temp)
        _sync(temp)
        _replace(temp, path)
    finally:
        # Never an unlink that raises in place of the failure: on Windows the
        # hand that held the file through the rename holds it still, and
        # Save As then reported the temporary rather than the failed write.
        _discard(temp)


def _write_jpeg(path: Path, image: np.ndarray, quality: int,
                resolution: int | None = None) -> None:
    """The picture alone. Anything past three channels leaves in the DNG.

    With the scan's resolution in its header, as the TIFF carries it: without
    one every JPEG opened at an editor's default, 72 per inch.
    """
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
        **({"dpi": (int(resolution), int(resolution))} if resolution else {}),
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
        _whole(companion, lambda temp: dng.write(
            temp, image[:, :, :dng.CHANNELS], resolution=resolution))
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
    compress: bool = True,
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

    ``compress=False`` writes a TIFF plain, for a caller writing with the
    scanner open and idle -- where deflate is the heavy local work CLAUDE.md
    names as preceding a wedge. It is the caller's to know; this cannot.
    """
    path = Path(path)
    fmt = format_of(path)
    if fmt == "jpeg":
        try:
            _whole(path, lambda temp: _write_jpeg(temp, image, quality,
                                                  resolution))
        except ImportError:
            path = path.with_suffix(SUFFIXES["tiff"])
            _whole(path, lambda temp: tiff.write(
                str(temp), image, resolution=resolution, compress=compress))
            return (f"Pillow is not installed, so this was written as "
                    f"{path.name} instead. `uv sync --extra jpeg` adds it.")
        # Named from the picture's own name, never the temporary one it was
        # written under.
        return _write_infrared(path, image, resolution)
    _whole(path, lambda temp: tiff.write(str(temp), image,
                                         resolution=resolution,
                                         compress=compress))
    return ""
