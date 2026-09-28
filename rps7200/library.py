"""A durable library of scans, so a test negative is scanned once and reused.

Re-scanning to test a change is slow, and it is not even the same experiment:
the transport moves, the lamp drifts, auto-exposure lands somewhere new. A
saved scan is a fixed input a correction can be measured against.

What makes that possible is saving enough beside the pixels. In particular
**the calibration travels with the scan**. The scanner returns raw pixels and
the shading reference that corrects them is acquired per session, the CCD mask
that aligns it per pass; neither can be recovered afterwards. A raw file saved
without them can never be corrected again, and a corrected file cannot be
re-corrected when the correction improves. So each entry keeps:

    raw.bin.gz        the scanner's bytes, exactly as they arrived
    scan.tif          those bytes decoded to pixels, uncorrected
    scan.json         every setting, the device state, and the provenance
    shading.npz       the reference this pass would be corrected with
    ccd_mask.bin      the column mapping for this pass
    prescan.tif       the framing pass, when there was one

`raw.bin.gz` is the ground truth and everything else is derived from it. The
decode is not settled -- the INDEX layout, the channel tags, the line stride
and the column alignment have all been in question at some point -- so keeping
the bytes means a change to any of that can be re-run against every scan ever
taken and compared, instead of being tested on whatever is scanned next.
:func:`reconstruct` does exactly that.

Entries are self-contained directories: nothing refers out, so one can be
copied or deleted on its own. `index.json` is a derived summary for finding
things and can be rebuilt from the entries at any time.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import warnings
import zipfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np

from . import tiff
from .direct import SHADING_SKIPPED_EXPLICIT, DirectScanner, ScanParameters
from .protocol import ScanReadError
from .shading import ShadingReference, apply_shading, calculate_shading

DEFAULT_ROOT = Path("library")
INDEX = "index.json"

#: Where each calibration's own bytes are archived unless a caller says
#: otherwise: beside the default cached reference, `calibration/shading.npz`,
#: one folder per calibration (`DirectScanner.archive_calibration`).
DEFAULT_CALIBRATIONS = Path("calibration")
CALIBRATION_RECORD = "calibration.json"


@dataclass
class FilmNotes:
    """What is on the film. Nothing here can be recovered from the file."""

    stock: str = ""            # "Kodak Gold 200"
    format: str = "135"
    process: str = ""          # "C-41", "E-6", developed by whom / when
    frame: str = ""            # position on the roll
    subject: str = ""
    notes: str = ""            # "dust top left", "the stripe test frame"


def provenance() -> dict[str, Any]:
    """Which build produced this, and on what.

    The versions are not ceremony: tifffile 2026.8.23 against numpy 1.26 could
    not read our own files at all, and without a record of what was installed
    that kind of failure is unattributable months later.
    """
    def git(*args: str) -> str | None:
        # `text=True` alone decodes with the machine's locale encoding, which
        # is cp1252 on a German Windows. A filename git could not represent
        # there would raise UnicodeDecodeError -- not caught below, and this
        # runs inside every `save()`, so it would abort a roll mid-frame over
        # a provenance field. Nothing here is worth that: replace and move on.
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
                cwd=Path(__file__).resolve().parent.parent,
            )
            return out.stdout.strip() or None if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    versions = {"python": sys.version.split()[0], "numpy": np.__version__}
    for name in ("tifffile", "PIL"):
        try:
            versions[name] = __import__(name).__version__
        except Exception:
            pass

    status = git("status", "--porcelain", "--untracked-files=no")
    head = git("rev-parse", "HEAD")
    return {
        "driver_commit": head[:7] if head else None,
        "driver_commit_full": head,
        # None when git could not say, rather than False: "clean" was what an
        # absent git reported, and untracked files -- scratch scripts, a
        # library inside the tree -- no longer count as a change to the code.
        "driver_dirty": None if head is None else bool(status),
        # The code this process imported, which is what filed the entry. The
        # tree can move under a long-running window -- a branch checked out
        # while it runs -- and the fields above describe the tree now.
        "driver_commit_at_import": _AT_IMPORT.get("commit"),
        "driver_dirty_at_import": _AT_IMPORT.get("dirty"),
        # The package's own source, hashed as it was imported. A commit says
        # which code only when the tree was clean; "dirty" says only that it
        # was not. Two entries filed from one dirty tree, or from a copy with
        # no git at all, can still be told the same code or not.
        "driver_source_sha256_at_import": _AT_IMPORT.get("source_sha256"),
        "versions": versions,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
    }


def _identity_now() -> dict[str, Any]:
    """HEAD and whether tracked files differ from it, read once at import."""
    try:
        cwd = Path(__file__).resolve().parent.parent
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10, encoding="utf-8",
                              errors="replace", cwd=cwd)
        if head.returncode != 0:
            return {}
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
            errors="replace", cwd=cwd)
        return {"commit": head.stdout.strip() or None,
                "dirty": bool(status.stdout.strip()) if status.returncode == 0
                else None}
    except (OSError, subprocess.SubprocessError):
        return {}


def _source_digest() -> str | None:
    """One hash over every module of this package, name and bytes, in order."""
    try:
        digest = hashlib.sha256()
        for module in sorted(Path(__file__).resolve().parent.glob("*.py")):
            digest.update(module.name.encode("utf-8") + b"\0")
            digest.update(module.read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


_AT_IMPORT: dict[str, Any] = {**_identity_now(), "source_sha256": _source_digest()}


def _plain(value: Any) -> Any:
    """A value JSON cannot hold, as the nearest thing it can: losslessly
    where there is such a thing.

    The record used `default=str`, which wrote whatever reached it as its
    printed form: an array over a thousand elements as "[0.1 0.2 ... 0.9]",
    bytes as "b'...'", a numpy integer as a string. Nothing failed, and the
    value -- a registration profile, say -- was gone for good.
    """
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    return "".join(keep).strip("-").replace("--", "-")[:40] or "scan"


def entry_id(meta: dict[str, Any], film: FilmNotes, when: datetime) -> str:
    parts = [
        when.strftime("%Y%m%dT%H%M%SZ"),
        _slug(film.stock) if film.stock else "unknown-film",
    ]
    if film.frame:
        parts.append(f"f{_slug(film.frame)}")
    parts.append(f"{meta.get('resolution_dpi', 0)}dpi")
    if meta.get("channels", 3) >= 4:
        parts.append("ir")
    return "_".join(parts)


#: The pass's own fields, recorded as `scan` in every entry. Anything else
#: the meta carries goes to `extra` -- see :func:`save`.
SCAN_FIELDS = (
    "resolution_dpi", "frame", "width", "height", "depth",
    "channels", "channel_order", "bytes_per_line", "film",
    "exposure_scale", "exposure_metered", "duration_s",
    "protocol_revision", "rotation", "flipped", "reversal",
    # Which way the carriage read the pass, from its own line tags,
    # and whether `scan.tif` was turned upright from the order the
    # raw bytes are in (`rps7200.direction`). And the READ STATE
    # before it, as evidence of where the carriage was.
    "read_direction", "carriage_state",
    # Rows the 7200 dpi column-stagger realignment trimmed from the
    # decode before it became `scan.tif`; `decode_raw` replays it.
    "stagger_realigned",
    # Which side of a fast-infrared ladder this pass came from.
    # Without it `signature` cannot tell the halves apart -- the
    # whole ladder is one frame at one dpi, depth, channel count
    # and commanded exposure -- and `duplicates` would call six
    # deliberately different passes interchangeable.
    "fast_infrared",
    # Read from GET PARAMETERS and otherwise discarded. `scan()`
    # keeps them because they are the prime suspect for the
    # pass-to-pass offset, and a suspicion that cannot be tested
    # without the numbers is not worth having.
    "filter_offsets",
)

#: Meta keys recorded somewhere other than `extra`.
_RECORDED = frozenset(SCAN_FIELDS) | {
    "exposure", "gain", "offset", "metering", "registration", "shading",
    "shading_skipped",
}


def _describe_inquiry(inquiry: Any) -> dict[str, Any] | None:
    """The scanner's INQUIRY as a record: vendor, model, firmware and the rest."""
    if inquiry is None:
        return None
    try:
        from dataclasses import fields, is_dataclass
        if is_dataclass(inquiry) and not isinstance(inquiry, type):
            return {f.name: getattr(inquiry, f.name) for f in fields(inquiry)}
    except Exception:                                    # noqa: BLE001
        pass
    describe = getattr(inquiry, "describe", None)
    return {"description": describe() if callable(describe) else str(inquiry)}


def save(
    image: np.ndarray,
    meta: dict[str, Any],
    *,
    root: Path | str = DEFAULT_ROOT,
    film: FilmNotes | None = None,
    tags: list[str] | None = None,
    reference: ShadingReference | None = None,
    ccd_mask: bytes | None = None,
    prescan: np.ndarray | None = None,
    prescan_meta: dict[str, Any] | None = None,
    inquiry: Any = None,
    raw: bytes | None = None,
    raw_path: Path | str | None = None,
    raw_layout: dict[str, Any] | None = None,
    corrections: list[str] | None = None,
    prescan_corrections: list[str] | None = None,
    compress: bool = True,
    created: datetime | None = None,
) -> Path:
    """Write one scan and everything needed to use it again. Returns its path.

    ``compress=False`` writes the raw bytes as `raw.bin` and the TIFFs
    uncompressed: plain writes, for a caller filing with the scanner open and
    idle, where compressing is what preceded a wedge. :func:`compact` makes
    it the ordinary gzipped entry later, losslessly; every reader takes either.

    ``image`` must be the **raw** pixels -- what the scanner sent, before
    flat-fielding. Corrections belong downstream: they change, and a corrected
    file cannot be un-corrected. The shading reference is stored beside the
    pixels, so :func:`corrected` recomputes the usable image at any time with
    whatever the correction code does *today*.

    That is the whole bargain of this library, and it is why everything the
    operator sees, exports or saves is corrected while what is kept here is
    not. `corrections` names anything a caller has nonetheless baked into
    ``image``, so a file that is not raw is at least labelled as such.

    ``prescan_meta`` is the framing pass's own meta, from the scanner, for a
    frame filed with its prescan: `prescan.tif` has no raw bytes of its own,
    so this is the only record of which way it was read.

    ``created`` is when the pass was taken, for one filed well after it. A
    debug spool is filed after close(), or days later by `file-spool`, and
    its id and ``created`` said when it was filed -- among another day's
    scans. Unsaid, it is now.

    ``prescan_corrections`` is `corrections` for the prescan, recorded as
    `prescan.corrections_applied`. Every caller today hands over what
    `prescan()` returned, which is corrected exactly when its meta carries a
    shading report, so that is what an unstated one is taken to be; a caller
    filing the prescan's raw pixels says ``prescan_corrections=[]``. With no
    meta and nothing said, the record says None: not known.
    """
    film = film or FilmNotes()
    when = created or datetime.now(timezone.utc)
    root = Path(root)
    path = _reserve(root, entry_id(meta, film, when))
    # Present until the record is in place, so an entry a crash or a full
    # disk cut short says so rather than passing for a directory of files.
    # `verify` reports it; `entries()` never sees it (no `scan.json`).
    (path / INCOMPLETE).write_text(
        "this entry was being written and did not finish\n", encoding="utf-8")

    # The raw bytes first: they are the ground truth and the only file here
    # nothing else can be derived back into. Written last, any failure on the
    # way -- a TIFF refused for its shape, a reference that would not save, a
    # disk filling during scan.tif -- aborted the save before they were even
    # tried, and a pass that went wrong lost the one record of how.
    raw_bytes = raw_sha = None
    raw_name = None
    if raw is not None or raw_path is not None:
        # Compressed, but byte-exact: measured on a real pass, gzip takes it to
        # 72% of its size, and the decompressed bytes are identical to what
        # arrived.
        digest = hashlib.sha256()
        raw_name = RAW_FILE if compress else RAW_PLAIN
        opener = ((lambda f: gzip.open(f, "wb", compresslevel=6)) if compress
                  else (lambda f: open(f, "wb")))
        with opener(path / raw_name) as fh:
            if raw is not None:
                digest.update(raw)
                fh.write(raw)
                raw_bytes = len(raw)
            elif raw_path is not None:
                # `elif`, not `else`: the outer guard is an `or`, so this arm is
                # the one where raw_path is set -- said here rather than assumed,
                # which is also the only way it can be checked.
                # Streamed in chunks. A 7200 dpi frame is 570 MB, and reading it
                # whole to hand over as `raw` would spike memory by that much for
                # no reason -- the point of spooling it was to keep it off the
                # heap in the first place.
                raw_bytes = 0
                with open(raw_path, "rb") as src:
                    while chunk := src.read(8 << 20):
                        digest.update(chunk)
                        fh.write(chunk)
                        raw_bytes += len(chunk)
        raw_sha = digest.hexdigest()
        _sync(path / raw_name)

    resolution = int(meta.get("resolution_dpi") or 0) or None
    tiff.write(str(path / "scan.tif"), image, resolution=resolution,
               compress=compress)
    _sync(path / "scan.tif")
    if prescan is not None:
        tiff.write(str(path / "prescan.tif"), prescan, compress=compress)
        _sync(path / "prescan.tif")
    if reference is not None:
        # Plain too, when the caller says the device is open (`compress`).
        reference.save(path / "shading.npz", compress=compress)
        _sync(path / "shading.npz")
    if ccd_mask is not None:
        (path / "ccd_mask.bin").write_bytes(bytes(ccd_mask))
        _sync(path / "ccd_mask.bin")

    record: dict[str, Any] = {
        "id": path.name,
        "created": when.isoformat(timespec="seconds"),
        "image": {
            "file": "scan.tif",
            "shape": list(image.shape),
            "dtype": str(image.dtype),
            "channels": meta.get("channel_order"),
            # What is baked into `scan.tif`, which is normally nothing: the
            # entry stores raw pixels and `calibration.report` below records
            # the correction that was computed for this pass, so a consumer
            # can tell "no correction was available" from "the correction is
            # stored beside the pixels rather than in them". `corrections`
            # exists for the caller that really does hand over a corrected
            # image and must say so.
            "corrections_applied": list(corrections or []),
            "sha256": _sha256(path / "scan.tif"),
        },
        "raw": {
            "file": raw_name if raw_bytes is not None else None,
            "bytes": raw_bytes,
            "sha256": raw_sha,
            "layout": raw_layout,
        },
        "scan": {k: meta.get(k) for k in SCAN_FIELDS},
        # Everything else the pass's meta carried, kept rather than dropped.
        # A fixed list of fields was the whole record, so whatever a caller
        # added that the list did not name -- a bracket's membership and
        # ratios, a roll frame's index and position, the demo's own flag, the
        # commands a pass was sent -- vanished at filing: a bracket could not
        # be re-merged, nor a demo entry told from a real one.
        "extra": {k: v for k, v in meta.items() if k not in _RECORDED},
        # Which scanner, as it described itself. Every caller handed it over
        # and it was ignored.
        "device": _describe_inquiry(inquiry),
        "device_settings": {
            k: meta.get(k) for k in ("exposure", "gain", "offset")
        },
        # What the metering probe measured, when this scan did its own. The
        # exposure above is the conclusion; this is the evidence for it, and
        # without it a scan that came out wrong cannot be told from one metered
        # against a frame that asked for something odd. Absent on a scan given
        # its exposure rather than metering one.
        "metering": meta.get("metering"),
        # Where the frame was and what was done about it: the hold loop's
        # outcome, or the older automatic correction's. Same relationship to
        # the scan as `metering` above -- the pixels are the conclusion, this
        # is the evidence. Kept because `CONFIDENCE_FLOOR` is fitted from these
        # numbers and they existed nowhere durable before: `meta` carried them
        # this far and the record dropped them, so every confidence the driver
        # had ever measured lived only in the roll's own `roll.json`, which is
        # gitignored and rewritten per frame. (`tools/registration_margin.py`
        # re-derives the floor from the pictures themselves and does not read
        # these; they are what the loop measured at the time, kept for a
        # study that wants them.) Absent on a scan that never looked.
        "registration": meta.get("registration"),
        "calibration": {
            "shading": "shading.npz" if reference is not None else None,
            "ccd_mask": "ccd_mask.bin" if ccd_mask is not None else None,
            "pixels_per_line": (
                reference.pixels_per_line if reference is not None else None
            ),
            "light_mean": (
                [round(reference.mean[c], 1) for c in reference.channels]
                if reference is not None else None
            ),
            "report": meta.get("shading"),
            # Set when the pass asked to be corrected and could not be. An
            # empty `corrections_applied` alone cannot say that: it looks
            # identical to a scan deliberately taken raw.
            "skipped": meta.get("shading_skipped"),
        },
        # The framing pass stored beside the scan, and which way it was read.
        # Absent when there is no `prescan.tif`.
        **({"prescan": {
            "file": "prescan.tif",
            # Labelled, as `image.corrections_applied` labels scan.tif. A roll's
            # frame entries file the prescan corrected, with no bytes and no
            # mask of its own -- the framing picture the operator was shown,
            # corrected by that day's code, the pass itself filed raw in an
            # entry of its own -- and nothing said so: the convention lived
            # only in the demo's docstring, a reader of the entry had to
            # guess, and one taking it for raw corrected it a second time.
            "corrections_applied": (
                list(prescan_corrections) if prescan_corrections is not None
                else None if prescan_meta is None
                else ["shading"] if (prescan_meta.get("shading")
                                     and not prescan_meta.get("shading_skipped"))
                else []),
            "read_direction": (prescan_meta or {}).get("read_direction"),
            "carriage_state": (prescan_meta or {}).get("carriage_state"),
        }} if prescan is not None else {}),
        "film": asdict(film),
        "tags": sorted(set(tags or [])),
        "provenance": provenance(),
    }
    # Every file but the record itself, checksummed: `verify` could see damage
    # to `scan.tif` and the raw bytes, and to nothing else -- a corrupt
    # reference or mask would correct every export of the entry wrongly.
    record["files"] = {
        name: _sha256(path / name)
        for name in ("shading.npz", "ccd_mask.bin", "prescan.tif")
        if (path / name).exists()
    }
    # The record last, whole or not at all: written beside and renamed over,
    # so a reader never meets half of one. Every file above was synced before
    # it, so a power cut cannot leave a durable record naming data that never
    # reached the disk -- the marker going would otherwise vouch for it.
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=_plain))
    _sync_dir(path)
    (path / INCOMPLETE).unlink(missing_ok=True)
    _sync_dir(path)
    # The entry is complete, and nothing past this point may say otherwise.
    # The index is a summary nothing reads; a sync client or a scanner for
    # viruses holding it for a moment made a complete save raise, and every
    # caller then treated a filed frame as a failed one -- the roll stopped,
    # its delivered copies were never written, and the debug spool was kept
    # to be filed a second time by hand.
    # Anything, not only OSError: whatever the summary trips on is a reason to
    # say so, never to make a complete entry look like a failed one -- the
    # caller would then keep the picture elsewhere, a second copy of it.
    try:
        reindex(root)
    except Exception as exc:                              # noqa: BLE001
        warnings.warn(f"{path.name} is filed, but {root / INDEX} could not be "
                      f"rewritten ({exc}); `tools/library.py reindex` "
                      f"rebuilds it", RuntimeWarning, stacklevel=2)
    return path


#: A tag saying an entry was taken without a shading reference on purpose --
#: the byte-14 ladder, say, which is evidence and must never be pruned. `verify`
#: does not report such an entry for lacking one. `tools/library.py tag` sets it.
ON_PURPOSE = "uncalibrated-on-purpose"

#: The raw bytes, gzipped (the ordinary form) and plain (filed with the
#: scanner open, before :func:`compact`).
RAW_FILE = "raw.bin.gz"
RAW_PLAIN = "raw.bin"


def compact(path: Path | str) -> bool:
    """Compress an entry filed with ``compress=False``. Lossless; True if done.

    For after the scanner is closed. The raw bytes are gzipped and checked
    against their recorded checksum before the plain file goes; the TIFFs are
    rewritten compressed with identical pixels. Each file is swapped in whole
    and the record last, so an interruption leaves a readable entry.

    And one a second call finishes. `raw.bin` goes only after the record, so
    an entry still holding it has not finished compacting; a TIFF swapped in
    before the stop no longer matches the checksum the record still holds.
    That is not damage and is not passed over as none: `scan.tif` is proved
    against the decode of its bytes, and `prescan.tif` -- which has no bytes
    of its own -- is accepted only where `scan.tif`, swapped before it, shows
    the compaction got that far. Anything else stops here, left as it is.
    """
    path = Path(path)
    plain = path / RAW_PLAIN
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    if not plain.exists():
        return False
    # The TIFFs are rewritten below and given fresh checksums, so a TIFF
    # damaged since it was filed would have come out *verified*: a prescan,
    # with no raw bytes to rebuild it from, damaged beyond anyone's telling.
    # Checked first, before anything is touched, as the raw bytes are -- and
    # a disagreeing checksum is let through only where it is proved to be a
    # stopped compaction's swap (see above).
    swapped = False
    for name in ("scan.tif", "prescan.tif"):
        said = ((record.get("image") or {}).get("sha256") if name == "scan.tif"
                else (record.get("files") or {}).get(name))
        if not said or not (path / name).exists() \
                or _sha256(path / name) == said:
            continue
        if name == "scan.tif":
            swapped = _holds_the_decode(path, name)
        if not swapped:
            raise OSError(f"{path.name}: {name} does not match its checksum; "
                          "left as it is")
    raw = record.setdefault("raw", {})
    temp = path / f".{RAW_FILE}.part"
    digest = hashlib.sha256()
    with open(plain, "rb") as src, gzip.open(temp, "wb", compresslevel=6) as fh:
        while chunk := src.read(8 << 20):
            digest.update(chunk)
            fh.write(chunk)
    if raw.get("sha256") and digest.hexdigest() != raw["sha256"]:
        temp.unlink(missing_ok=True)
        raise OSError(f"{path.name}: {RAW_PLAIN} does not match its checksum; "
                      "left as it is")
    _replace(temp, path / RAW_FILE)
    raw["file"] = RAW_FILE
    for name in ("scan.tif", "prescan.tif"):
        if (path / name).exists():
            pixels = tiff.read(str(path / name))
            resolution = ((record.get("scan") or {}).get("resolution_dpi")
                          if name == "scan.tif" else None) or None
            _replace_tiff(path / name, pixels, resolution=resolution)
            digest_now = _sha256(path / name)
            if name == "scan.tif":
                record.setdefault("image", {})["sha256"] = digest_now
            else:
                record.setdefault("files", {})[name] = digest_now
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=_plain))
    plain.unlink()
    return True


#: Marks an entry still being written. See :func:`save`.
INCOMPLETE = "INCOMPLETE"


def _reserve(root: Path, name: str) -> Path:
    """Create this entry's directory, or the next free ``-N`` beside it.

    `mkdir` either creates the directory or fails because it exists, in one
    step, so two writers in the same second -- the window's writer and a debug
    flush, or the window and a tool -- can never be handed one directory. The
    check this replaces looked for `scan.json`, which the first writer only
    creates at the very end.
    """
    root.mkdir(parents=True, exist_ok=True)
    path, n = root / name, 2
    while True:
        try:
            path.mkdir()
            return path
        except FileExistsError:
            # Only a name that is taken earns the next one; anything else
            # would be asked again, under a new name, for ever.
            if not path.exists():
                raise
            path = root / f"{name}-{n}"
            n += 1


def _write_atomic(path: Path, text: str) -> None:
    """Write beside, then rename over: the old file or the new, never half.

    The name written beside is this writer's own. `index.json` is rewritten
    by the roll's filing thread and by the debug flush in `close()` at the
    same moment, and two writers sharing one temporary name truncated and
    interleaved each other's text before either renamed it.
    """
    temp = path.with_name(
        f".{path.name}.{os.getpid()}-{threading.get_ident()}.part")
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


#: How long `_replace` waits between attempts to rename over a file someone
#: holds open. On Windows the rename fails outright while any handle lacks
#: FILE_SHARE_DELETE -- Python's own `open()` for reading, a sync client, the
#: indexer and Defender all briefly hold new files. The same waits as
#: `session.write_manifest`, for the same reason.
REPLACE_RETRY_S = (0.05, 0.05, 0.1, 0.1, 0.1)


def _replace(temp: Path, path: Path) -> None:
    """`os.replace`, patient with a file that is only briefly held open."""
    for wait in (*REPLACE_RETRY_S, None):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def _sync(path: Path) -> None:
    """Flush one written file to the disk, not just to the cache.

    Best effort. The file is written either way; a sync refused -- a handle
    Windows will not grant while a scanner for viruses has the file -- costs
    durability across a power cut, and must not cost the entry.
    """
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sync_dir(path: Path) -> None:
    """Make the names in a directory durable, where the platform can.

    POSIX needs the directory itself synced for a new or renamed name to
    survive a power cut. Windows cannot open a directory this way and makes
    its metadata durable itself, so there it is nothing to do.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def add_tags(path: Path | str, tags: list[str]) -> list[str]:
    """Add tags to one entry's record, in place and atomically. Returns them all."""
    path = Path(path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["tags"] = sorted(set(record.get("tags") or ()) | set(tags))
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=_plain))
    return record["tags"]


def _replace_tiff(path: Path, image: np.ndarray, **kw: Any) -> None:
    """Rewrite a stored TIFF beside, then swap it in: never a half-written one.

    An interrupted in-place rewrite of a complete entry left a truncated file
    under its ordinary name, which only a checksum could tell from a good one.
    """
    temp = path.with_name(f".{path.name}.part")
    tiff.write(str(temp), image, **kw)
    _replace(temp, path)


def entry_path(root: Path | str, record: dict[str, Any]) -> Path:
    """Where this record's entry is: its directory, not the id it records.

    The same until an entry is copied or renamed, after which the id inside
    names a directory that is not there. `entries()` says which directory it
    read each record from.
    """
    return Path(root) / str(record.get("_dir") or record.get("id"))


def load(path: Path | str) -> tuple[np.ndarray, dict[str, Any]]:
    """Read a library entry back: ``(image, record)``.

    ``record["reference"]`` and ``record["ccd_mask"]`` are filled in where the
    entry has them, so a correction can be re-run exactly as it would have been
    at scan time. A reference that is there and will not load is None, with
    ``record["reference_error"]`` saying why.
    """
    path = Path(path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    image = tiff.read(str(path / "scan.tif"))

    ref_file = (record.get("calibration") or {}).get("shading")
    record["reference"] = None
    if ref_file and (path / ref_file).exists():
        try:
            record["reference"] = ShadingReference.load(path / ref_file)
        # A truncated .npz raises BadZipFile, not an OSError, and it made
        # every view and export of the entry fail with a traceback. The
        # pixels are fine; only the correction cannot be had, and saying so
        # is `corrected()`'s job.
        except (OSError, ValueError, KeyError, EOFError,
                zipfile.BadZipFile) as exc:
            record["reference_error"] = f"{ref_file} cannot be read ({exc})"
    mask_file = (record.get("calibration") or {}).get("ccd_mask")
    record["ccd_mask"] = (
        (path / mask_file).read_bytes()
        if mask_file and (path / mask_file).exists() else None
    )
    return image, record


def corrected(path: Path | str) -> tuple[np.ndarray, dict[str, Any]]:
    """The usable image: an entry's pixels with its own correction applied.

    :func:`load` hands back what is stored, which is raw. This hands back what
    a person should look at. Anything that shows, exports or saves a library
    entry wants this one -- the raw file is for re-deriving, not for viewing,
    and a raw frame shown to an operator is the thing this driver stopped
    doing everywhere else.

    The correction is recomputed here rather than read from disk, so an entry
    scanned months ago gets today's correction code. `record["corrected"]`
    says what happened, including when nothing could be done:

        "applied"    the reference was there and was used
        "already"    the stored pixels were corrected before they were filed
                     -- legacy entries, from before the library stored raw
        "no reference"  nothing to correct with; the pixels are returned raw
        "deliberately raw"  the pass asked for `shading=False`
        "raw -- correction was asked for"  it wanted correction and was filed
                     without any; the rawness was a shortfall, not a choice
        "reference unreadable"  there is one and it will not load
                     (`record["reference_error"]` says why); returned raw
        "no mask"    a reference and no CCD mask, on a pass narrower or wider
                     than the reference: nothing says which of its columns
                     this pass read, so it is returned raw

    The two "raw" ones look identical in the record -- both are a non-empty
    `calibration.skipped` -- and they are opposite things. Only
    :data:`rps7200.direct.SHADING_SKIPPED_EXPLICIT` means the caller chose it.
    `verify` already draws that line and calls the other one "a thing that went
    wrong"; this used to call both a choice, so Save As told an operator that a
    shortfall had been intended.

    Four entries in this library are the second kind, all from 2026-09-11 and
    all from before `scan()` raised `ShadingUnavailable` instead of returning
    raw quietly: two filed with no reference in the session at all, and two
    7200 dpi passes, for which the device will not produce a reference wider
    than 5172 columns at any resolution. The reason itself stays in
    `calibration.skipped` for a caller that wants to say which.
    """
    return correct(*load(path))


def correct(image: np.ndarray, record: dict[str, Any]
            ) -> tuple[np.ndarray, dict[str, Any]]:
    """:func:`corrected`, on an entry :func:`load` has already read.

    For a caller that wants the stored pixels as well: the window counts
    what sat at the rail on them, and reading the entry twice for it -- once
    raw, once inside `corrected` -- was two reads of a 7200 dpi scan, some
    570 MB, where one was enough.
    """
    applied = (record.get("image") or {}).get("corrections_applied") or []
    if "shading" in applied:
        record["corrected"] = "already"
        return image, record
    skipped = (record.get("calibration") or {}).get("skipped")
    if skipped:
        record["corrected"] = (
            "deliberately raw" if skipped == SHADING_SKIPPED_EXPLICIT
            else "raw -- correction was asked for"
        )
        return image, record
    if record.get("reference") is None:
        record["corrected"] = ("reference unreadable"
                               if record.get("reference_error") else "no reference")
        return image, record
    if (record["ccd_mask"] is None
            and record["reference"].pixels_per_line != image.shape[1]):
        # Without a mask `apply_shading` matches columns one to one, which is
        # right only for a pass that read every CCD pixel. On any other it
        # divided a 1800 dpi pass's left half by the reference of the CCD's
        # left quarter -- banding and a colour ramp -- and called it applied.
        record["corrected"] = "no mask"
        return image, record
    image, report = apply_shading(image, record["reference"], record["ccd_mask"])
    record["corrected"] = "applied"
    record["shading_report"] = report
    return image, record


def read_raw(path: Path | str) -> bytes | None:
    """The scanner's own bytes for this entry, decompressed.

    None when there are none stored *or* when what is stored cannot be read --
    a truncated or corrupt file is a library problem for :func:`verify` to
    report, not an exception for every caller to handle.
    """
    folder = Path(path)
    if (folder / RAW_PLAIN).exists() and not (folder / RAW_FILE).exists():
        try:
            return (folder / RAW_PLAIN).read_bytes()
        except OSError:
            return None
    path = folder / RAW_FILE
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rb") as fh:
            return fh.read()
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


def stagger_lines(record: dict[str, Any], decoded_rows: int | None = None,
                  stored_rows: int | None = None) -> int:
    """Rows the 7200 dpi realignment trimmed from this entry's decode.

    From the record where it says (`scan.stagger_realigned`). An entry filed
    before the record carried it, at the native resolution and exactly that
    many rows short of its decode, was realigned too -- `scan()` has done it
    to every 7200 dpi pass since 2026-09-13 -- and is read as such.
    """
    scan = record.get("scan") or {}
    # None, or absent, on an entry filed before it was recorded.
    if scan.get("stagger_realigned") is not None:
        return int(scan["stagger_realigned"])
    lines = DirectScanner.NATIVE_COLUMN_STAGGER_LINES
    if (scan.get("resolution_dpi") == DirectScanner.NATIVE_COLUMN_STAGGER_DPI
            and decoded_rows is not None and stored_rows is not None
            and decoded_rows - stored_rows == lines):
        return lines
    return 0


def _replay(image: np.ndarray, record: dict[str, Any],
            stored_rows: int | None = None) -> np.ndarray:
    """Apply the host transforms `scan()` makes after the decode, from the record."""
    lines = stagger_lines(record, image.shape[0], stored_rows)
    if lines:
        image = DirectScanner._realign_native_column_stagger(image, lines)
    return image


def decode_raw(path: Path | str) -> np.ndarray | None:
    """This entry's raw bytes decoded to pixels, with nothing applied.

    What `scan.tif` should hold. :func:`reconstruct` compares against it and
    `tools/library.py migrate-raw` writes it; both want the decode alone, with
    no correction folded in, so it lives here rather than being spelled out
    twice. None when there are no bytes or the layout cannot drive a decode.
    """
    path = Path(path)
    raw = read_raw(path)
    if raw is None:
        return None
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
        layout = (record.get("raw") or {}).get("layout") or {}
        params = ScanParameters(
            width=int(layout["width"]),
            lines=int(layout["lines"]),
            bytes_per_line=int(layout["bytes_per_line"]),
            filter_offset1=0,
            filter_offset2=0,
            available_lines=0,
        )
        image = DirectScanner._deinterleave(raw, params, int(layout["channels"]))
        stored = (record.get("image") or {}).get("shape") or [None]
        return _replay(image, record, stored[0])
    # ScanReadError is what the decode raises for tags it cannot place; it is
    # a RuntimeError, and escaped the "None when the layout cannot drive a
    # decode" this promises.
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError,
            ScanReadError):
        return None


def _holds_the_decode(path: Path, name: str) -> bool:
    """Whether a stored TIFF holds exactly the decode of the entry's raw bytes.

    The proof that a TIFF disagreeing with its checksum was swapped in by a
    rewrite that stopped before the record, rather than damaged: `compact`
    uses it to finish one, and `damage` to tell one from damage.
    """
    try:
        pixels = tiff.read(str(path / name))
    except (OSError, ValueError):
        return False
    decoded = decode_raw(path)
    return bool(decoded is not None and decoded.dtype == pixels.dtype
                and np.array_equal(decoded, pixels))


#: What a :func:`reconstruct` verdict is, as :attr:`Verdict.kind`.
IDENTICAL = "identical"      # today's decode reproduces the stored pixels
CHANGED = "changed"          # it does not: shape, samples, type or direction
BEHIND = "behind"            # filed bottom-up before passes were turned upright
NOTHING = "nothing"          # nothing stored to decode or reproduce from
DAMAGED = "damaged"          # a stored file is there and cannot be trusted
FAILED = "failed"            # today's decode raised on the stored bytes


class Verdict(str):
    """A :func:`reconstruct` verdict: the sentence, and which kind it is.

    A str, so every caller that prints the sentence or tests how it starts
    still works. `kind` is for the caller that has to *count* them, which
    used to sort by the wording: "could not" covered a decode that now raises
    and a scan.tif that no longer reads, and "no raw bytes" a gzip that was
    there and corrupt -- and all three were counted as nothing stored, so
    `make reconstruct` passed with every entry failing to decode.
    """

    kind: str

    def __new__(cls, text: str, kind: str) -> "Verdict":
        self = super().__new__(cls, text)
        self.kind = kind
        return self


def _raw_on_disk(path: Path) -> bool:
    return (path / RAW_FILE).exists() or (path / RAW_PLAIN).exists()


def reconstruct(path: Path | str) -> tuple[np.ndarray | None, Verdict]:
    """Decode this entry's raw bytes with the *current* code.

    Returns ``(image, verdict)``. The verdict says whether today's decode still
    reproduces the pixels stored at scan time -- which is the whole reason the
    bytes are kept. A mismatch is not necessarily a regression: it is where a
    deliberate change to the decode shows up, on every scan in the library at
    once rather than on the next one taken. `verdict.kind` says which of the
    module's kinds (:data:`IDENTICAL`, :data:`CHANGED` ...) it is.
    """
    path = Path(path)
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, Verdict(f"could not read scan.json: {exc}", DAMAGED)
    raw = read_raw(path)
    if raw is None:
        # `read_raw` answers None for a file that is not there and for one
        # that is there and will not read. Only the first is "nothing stored",
        # and only when the record never named one: a raw file the record
        # names and checksums that has since gone is a loss, not an entry
        # filed without bytes. Asked of the disk alone, deleting raw.bin.gz
        # turned "damaged" into "nothing stored" and `reconstruct` exited 0
        # while `verify` called the same file missing.
        named = (record.get("raw") or {}).get("file")
        if named and not _raw_on_disk(path):
            return None, Verdict(
                f"{named} is missing -- the raw bytes this entry was filed "
                "with are lost, not a decode change; see verify", DAMAGED)
        if _raw_on_disk(path):
            return None, Verdict(
                "raw bytes are stored but cannot be read -- damage to the "
                "file, not a decode change; see verify", DAMAGED)
        return None, Verdict("no raw bytes stored for this entry", NOTHING)
    # Checked before decoding, so storage damage is named as storage damage.
    # Unchecked, a bit flipped in a plain `raw.bin` decoded and read as
    # "decode CHANGED" -- blaming the decoder, and inviting someone to "fix"
    # it to match the damaged bytes.
    digest = (record.get("raw") or {}).get("sha256")
    if digest and hashlib.sha256(raw).hexdigest() != digest:
        return None, Verdict(
            "raw bytes do not match their checksum -- damage to the file, not "
            "a decode change; see verify", DAMAGED)
    layout = (record.get("raw") or {}).get("layout") or {}
    try:
        params = ScanParameters(
            width=int(layout["width"]),
            lines=int(layout["lines"]),
            bytes_per_line=int(layout["bytes_per_line"]),
            filter_offset1=0,
            filter_offset2=0,
            available_lines=0,
        )
        image, direction = DirectScanner.decode_index(
            raw, params, int(layout["channels"]))
    # ScanReadError too: bytes that are not a pass at all are a verdict about
    # this entry, not a reason to stop checking every entry after it. The
    # bytes passed their checksum above, so what raised is today's decode.
    except (KeyError, ValueError, TypeError, ScanReadError) as exc:
        return None, Verdict(f"could not decode: {exc}", FAILED)

    # An entry stores raw pixels, so a raw decode is what should match and this
    # is normally an exact comparison of the decode alone.
    #
    # Legacy entries -- filed before the library stored raw -- hold corrected
    # pixels, and there a raw decode can never match: every one of them reported
    # ~99% of samples differing, which made a real decode regression invisible
    # among the false alarms. Re-apply their own reference before comparing, so
    # the check tests the whole path from bytes to stored image rather than half
    # of it. `tools/library.py migrate-raw` converts them and this branch then
    # stops being reached.
    applied = (record.get("image") or {}).get("corrections_applied") or []
    if "shading" in applied:
        cal = record.get("calibration") or {}
        ref_file, mask_file = cal.get("shading"), cal.get("ccd_mask")
        # The same line as the raw bytes above: a legacy entry filed with no
        # reference has nothing to reproduce from, but one whose record names
        # its reference and has lost it is damaged, and `verify` says so.
        if ref_file and not (path / ref_file).exists():
            return image, Verdict(
                f"stored image is shading-corrected and its reference "
                f"{ref_file} is missing -- damage, not a decode change; see "
                "verify", DAMAGED)
        if not ref_file:
            return image, Verdict(
                "stored image is shading-corrected but its reference is "
                "missing, so it cannot be reproduced", NOTHING)
        try:
            reference = ShadingReference.load(path / ref_file)
        # A truncated .npz raises BadZipFile, which is not an OSError: it
        # stopped the whole run at the first damaged reference.
        except (OSError, ValueError, KeyError, EOFError,
                zipfile.BadZipFile) as exc:
            return image, Verdict(
                f"could not read {ref_file}: {exc} -- see verify", DAMAGED)
        mask = ((path / mask_file).read_bytes()
                if mask_file and (path / mask_file).exists() else None)
        image, _ = apply_shading(image, reference, mask)

    try:
        stored = tiff.read(str(path / "scan.tif"))
    except (OSError, ValueError) as exc:
        return image, Verdict(f"could not read scan.tif: {exc}", DAMAGED)
    # The 7200 dpi realignment is part of the path from bytes to `scan.tif`,
    # so it is replayed here, not reported as a changed decode.
    image = _replay(image, record, stored.shape[0])
    if image.shape != stored.shape:
        return image, Verdict(
            f"decode CHANGED: now {image.shape}, stored {stored.shape}",
            CHANGED)
    # `array_equal` compares values and not their type, and the type is not
    # a detail: `apply_shading` scales the reference by it, so 8-bit samples
    # coming back as uint16 with the same values would correct almost black
    # while every value still matched.
    if image.dtype != stored.dtype:
        return image, Verdict(
            f"decode CHANGED: now {image.dtype}, stored {stored.dtype}",
            CHANGED)
    recorded = ((record.get("scan") or {}).get("read_direction") or {}).get("direction")
    if recorded is not None and recorded != direction.state:
        return image, Verdict(
            f"read direction CHANGED: recorded {recorded}, the line tags now "
            f"say {direction.state} ({direction.why})", CHANGED)
    if np.array_equal(image, stored):
        return image, Verdict("identical to the stored image", IDENTICAL)
    if direction.reversed and np.array_equal(image[::-1], stored):
        # Filed before passes were turned upright in the decode: the stored
        # image is the pass in the order it was read. Not a regression, and
        # `tools/library.py migrate-direction` is what brings it up to date.
        return image, Verdict("stored as it was read, bottom-up; today's "
                              "decode turns it upright -- see "
                              "migrate-direction", BEHIND)
    differing = int(np.count_nonzero(image != stored))
    return image, Verdict(
        f"decode CHANGED: {differing} of {image.size} samples differ "
        f"({100 * differing / image.size:.3f}%)", CHANGED)


def migrate_direction(path: Path | str, *, write: bool = False) -> list[str]:
    """Bring one entry filed before passes were read upright up to date.

    Returns what was (or, with ``write`` False, would be) done, one line each;
    empty when the entry already says which way it was read.

    * **The scan**, from its raw bytes: the direction its line tags say goes
      into the record. A pass read bottom-up whose `scan.tif` is still in the
      order it was read is rewritten upright -- only when the stored image is
      exactly that, so a file that matches neither reading is reported and
      left alone. No raw bytes: recorded as unknown.
    * **Its `prescan.tif`**, which has no bytes of its own: judged against the
      upright scan, both ways up, and turned only when the rows-reversed
      reading wins by `framing.REVERSAL_MARGIN`. Anything less certain is
      recorded as unknown and not touched.

    Raw bytes are never changed, so every rewrite here can be undone from them
    -- and for a prescan by turning it again, which its record says was done.
    """
    from .direction import FORWARD, REVERSED, UNKNOWN
    from .framing import REVERSAL_MARGIN, _comparable

    path = Path(path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    scan_part = record.setdefault("scan", {})
    done: list[str] = []
    upright = None

    if not scan_part.get("read_direction"):
        raw = read_raw(path)
        layout = (record.get("raw") or {}).get("layout") or {}
        if raw is None or layout.get("format") != "index":
            scan_part["read_direction"] = {
                "direction": UNKNOWN, "lead": None, "trail": None, "turned": False,
                "evidence": None, "why": "no raw bytes to read the line tags from"}
            done.append("scan: no raw bytes -- recorded as unknown")
        else:
            params = ScanParameters(
                width=int(layout["width"]), lines=int(layout["lines"]),
                bytes_per_line=int(layout["bytes_per_line"]),
                filter_offset1=0, filter_offset2=0, available_lines=0)
            decoded, direction = DirectScanner.decode_index(
                raw, params, int(layout["channels"]))
            stored = tiff.read(str(path / "scan.tif"))
            read = direction.as_record()
            applied = (record.get("image") or {}).get("corrections_applied") or []
            plain = not applied and stored.shape == decoded.shape
            if plain and np.array_equal(stored, decoded):
                done.append(f"scan: read {direction.state} -- recorded")
                upright = decoded
                # The pixels are proved against the bytes here, so a checksum
                # that disagrees is stale, not damage: a run stopped after it
                # turned scan.tif and before the record said so arrives here
                # the second time, and left the old checksum for ever -- a
                # mismatch verify reported on an intact picture.
                if write and stored.dtype == decoded.dtype:
                    record.setdefault("image", {})["sha256"] = _sha256(
                        path / "scan.tif")
            elif (plain and direction.reversed
                    and np.array_equal(stored, decoded[::-1])):
                done.append("scan: read bottom-up and stored that way -- "
                            "turned upright")
                if write:
                    _replace_tiff(path / "scan.tif", decoded,
                                  resolution=scan_part.get("resolution_dpi") or None)
                    record.setdefault("image", {})["sha256"] = _sha256(
                        path / "scan.tif")
                upright = decoded
            else:
                # The direction is a fact of the bytes and is kept; whether
                # `scan.tif` was turned is not known, because it is not the
                # plain decode (corrected pixels, a realigned 7200 dpi pass).
                read.update(turned=None, why=read["why"] + "; scan.tif is not "
                            "the plain decode of these bytes, so it was left "
                            "as it is")
                done.append(f"scan: read {direction.state} -- recorded; "
                            "scan.tif is not a plain decode and was left alone")
            scan_part["read_direction"] = read
    else:
        said = scan_part.get("read_direction") or {}
        if (path / "scan.tif").exists() and (
                said.get("direction") == FORWARD
                or (said.get("direction") == REVERSED and said.get("turned"))):
            upright = tiff.read(str(path / "scan.tif"))

    pre = record.get("prescan") or {}
    if (path / "prescan.tif").exists() and not pre.get("read_direction"):
        if upright is None and (path / "scan.tif").exists():
            said = scan_part.get("read_direction") or {}
            if (said.get("direction") == FORWARD
                    or (said.get("direction") == REVERSED and said.get("turned"))):
                upright = tiff.read(str(path / "scan.tif"))
        prescan = tiff.read(str(path / "prescan.tif"))
        a, b = _comparable(prescan), (_comparable(upright) if upright is not None
                                      else None)
        judged: dict[str, Any] = {"direction": UNKNOWN, "lead": None,
                                  "trail": None, "turned": False,
                                  "evidence": "picture against its upright scan"}
        if a is None or b is None:
            judged["why"] = "no upright scan to judge it against"
        else:
            scores = {"upright": float((a * b).sum()),
                      "rows reversed": float((a[::-1] * b).sum()),
                      "mirrored": float((a[:, ::-1] * b).sum()),
                      "half turn": float((a[::-1, ::-1] * b).sum())}
            best = max(scores, key=lambda k: scores[k])
            margin = scores["rows reversed"] - scores["upright"]
            judged["scores"] = {k: round(v, 4) for k, v in scores.items()}
            if best == "rows reversed" and margin >= REVERSAL_MARGIN:
                judged.update(direction=REVERSED, turned=True,
                              why=f"reads rows-reversed by {margin:+.2f}")
                if write:
                    _replace_tiff(path / "prescan.tif",
                                  np.ascontiguousarray(prescan[::-1]))
                    record.setdefault("files", {})["prescan.tif"] = _sha256(
                        path / "prescan.tif")
            elif best == "upright" and -margin >= REVERSAL_MARGIN:
                judged.update(direction=FORWARD, why=f"reads upright by {-margin:+.2f}")
            else:
                judged["why"] = f"not decisive ({best} best, margin {margin:+.2f})"
        record["prescan"] = dict(pre, file="prescan.tif", read_direction=judged)
        done.append(f"prescan: {judged['direction']}"
                    + (" -- turned upright" if judged["turned"] else "")
                    + f" ({judged.get('why', '')})")

    if done and write:
        _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=_plain))
    return done


def signature(record: dict[str, Any]) -> tuple:
    """What makes two entries the same scan of the same picture.

    The picture, plus how the scanner was driven to capture it. Two entries
    sharing a signature are interchangeable: the device was told exactly the
    same thing about the same frame, so neither holds anything the other does
    not, and one of them is redundant.

    `protocol_revision` is part of it deliberately. Entries only reduce to each
    other while the conversation with the scanner is unchanged; once a command
    or payload moves, scans taken before and after are different measurements
    of the same film and both are worth keeping.

    Deliberately *not* included: the exposure the metering *landed on*, the
    shading reference, anything host-side. Those vary run to run without
    changing what was asked for, and everything host-side is re-runnable from
    the raw bytes.

    A *commanded* exposure is included, and the distinction matters. A bracket
    is the same frame at the same dpi, depth and channel count, differing only
    in the exposure each pass was told to use -- so without this, every member
    of a bracket shares one signature, `duplicates` calls the bracket redundant,
    and `--delete` keeps one and destroys the rest. That is precisely the data a
    bracket exists to capture.

    ``scan.exposure_metered`` says which kind it was. Entries written before
    that field existed are treated as metered, which is what they were: it
    leaves their signatures exactly as they were.

    ``scan.fast_infrared`` is included for exactly the bracket reason above. A
    fast-infrared ladder is one frame at one dpi, depth, channel count and
    commanded exposure, differing only in a quality bit -- so without this the
    whole ladder collapses to a single signature and `--delete` would keep one
    pass and destroy the comparison it was run to make. Entries written before
    the field existed read as ``None``, which is what they were taken with, so
    their signatures do not move.
    """
    scan, film = record.get("scan") or {}, record.get("film") or {}

    commanded = None
    if not scan.get("exposure_metered", True):
        scale = scan.get("exposure_scale")
        if isinstance(scale, (int, float)):
            commanded = (round(float(scale), 4),)
        elif scale:
            commanded = tuple(round(float(v), 4) for v in scale)

    return (
        (film.get("stock") or "").strip().lower(),
        (film.get("frame") or "").strip().lower(),
        (film.get("subject") or "").strip().lower(),
        scan.get("resolution_dpi"),
        scan.get("channels"),
        tuple(scan.get("frame") or ()),
        scan.get("depth"),
        scan.get("film"),
        scan.get("protocol_revision"),
        commanded,
        scan.get("fast_infrared"),
    )


def duplicates(root: Path | str = DEFAULT_ROOT) -> dict[tuple, list[dict[str, Any]]]:
    """Groups of interchangeable entries, newest first within each group.

    Only groups with more than one entry are returned.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for record in entries(root):
        groups.setdefault(signature(record), []).append(record)
    for group in groups.values():
        group.sort(key=lambda r: str(r.get("created")), reverse=True)
    return {sig: g for sig, g in groups.items() if len(g) > 1}


def prunable(
    root: Path | str = DEFAULT_ROOT, keep: int = 1
) -> list[tuple[dict[str, Any], str]]:
    """Which entries are redundant, and why. Nothing is deleted here.

    Redundant means two things at once: the same request of the scanner
    (`signature`) *and* the same data (`same_data`). An entry that shares a
    signature but holds different bytes is kept, whatever `keep` says.

    Within a group the ones kept are chosen on what they can still be used for,
    not on age: an entry carrying its raw bytes and its calibration can be
    re-decoded and re-corrected, and one without cannot. Then on what else it
    carries that its twin may not -- a prescan, film notes, tags, the pass's
    own record -- so a bare copy never survives a rich one. Ties go to the
    newest.

    And only once the survivor has been checked on disk. The choice is made
    from the records; a survivor whose raw bytes were truncated since would
    otherwise be kept while the intact copy went.
    """
    def usefulness(record: dict[str, Any]) -> tuple:
        raw = bool((record.get("raw") or {}).get("file"))
        cal = bool((record.get("calibration") or {}).get("shading"))
        # Everything a byte-identical twin can still differ by. The newest
        # used to win outright, so a debug copy filed after close() -- the
        # pass alone, "captured with RPS7200_DEBUG on" -- could survive the
        # tool's own entry with its film notes, prescan and roll membership.
        carries = (bool(record.get("prescan")),
                   sum(1 for v in (record.get("film") or {}).values() if v)
                   + len(record.get("tags") or ())
                   + len(record.get("extra") or {}))
        return (raw, cal, *carries, str(record.get("created")))

    out = []
    for group in duplicates(root).values():
        ranked = sorted(group, key=usefulness, reverse=True)
        kept = ranked[:keep]
        for record in ranked[keep:]:
            # Redundant only if it holds the same data as one that stays. A
            # shared signature says the scanner was asked the same thing; it
            # cannot say the answer was the same picture. Film notes left
            # empty, or two strips filed under one day's default roll name,
            # gave different photographs one signature, and `--delete` then
            # destroyed all but one of them, raw bytes included.
            twin = next((k for k in kept if same_data(record, k)
                         and not damage(entry_path(root, k), k)), None)
            if twin is None:
                kept.append(record)
                continue
            reason = f"same scan of the same picture as {twin.get('id')}"
            if not (record.get("raw") or {}).get("file"):
                reason += "; no raw bytes either"
            out.append((record, reason))
    return out


def same_data(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two entries hold the same scan, byte for byte.

    By the raw bytes where both kept them -- they are what the scanner sent --
    and by the decoded pixels otherwise. An entry that recorded neither
    checksum is never the same as anything: nothing here can prove it.
    """
    raw_a = (a.get("raw") or {}).get("sha256") if (a.get("raw") or {}).get("file") else None
    raw_b = (b.get("raw") or {}).get("sha256") if (b.get("raw") or {}).get("file") else None
    if raw_a and raw_b:
        return raw_a == raw_b
    image_a = (a.get("image") or {}).get("sha256")
    image_b = (b.get("image") or {}).get("sha256")
    return bool(image_a) and image_a == image_b


def entries(root: Path | str = DEFAULT_ROOT) -> list[dict[str, Any]]:
    """Every entry's record, oldest first. Unreadable entries are skipped."""
    root = Path(root)
    out = []
    for candidate in sorted(root.glob("*/scan.json")):
        try:
            record = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Valid JSON is not yet a record. A list or a string here raised on
        # the line below, and took with it every caller: `reindex`, `verify`,
        # and so every later `save`, which then reported a filed frame as a
        # failed one. `verify` names it.
        if not isinstance(record, dict):
            continue
        # Which directory it came from, for `entry_path`. Never written back.
        record["_dir"] = candidate.parent.name
        out.append(record)
    return out


def reindex(root: Path | str = DEFAULT_ROOT) -> Path:
    """Rebuild `index.json` from the entries. Derived: safe to delete."""
    root = Path(root)
    summary = [
        {
            "id": r.get("id"),
            "created": r.get("created"),
            "dpi": (r.get("scan") or {}).get("resolution_dpi"),
            "channels": (r.get("scan") or {}).get("channels"),
            "film": (r.get("film") or {}).get("stock"),
            "frame": (r.get("film") or {}).get("frame"),
            "tags": r.get("tags"),
            "corrected": (r.get("image") or {}).get("corrections_applied"),
            "notes": (r.get("film") or {}).get("notes"),
        }
        for r in entries(root)
    ]
    root.mkdir(parents=True, exist_ok=True)
    index = root / INDEX
    # Beside and swapped in, like every other file here: a plain rewrite cut
    # short left half an index, and two at once left a torn one.
    _write_atomic(index, json.dumps(summary, indent=2))
    return index


def verify(root: Path | str = DEFAULT_ROOT) -> list[str]:
    """Problems found in the library: missing files, checksum mismatches."""
    root = Path(root)
    problems = []
    # What `entries()` cannot see: a directory with no record, or one a
    # crash or a full disk left half-written. Each is either a scan's bytes
    # with nothing to say what they are, or a gap nobody would otherwise
    # notice -- and `verify` saying "intact" over one is the failure.
    for folder in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else ():
        if folder.name.startswith("."):
            continue
        if (folder / INCOMPLETE).exists():
            problems.append(f"{folder.name}: was being written and did not finish")
        elif not (folder / "scan.json").exists():
            problems.append(f"{folder.name}: has no scan.json, so no check sees it")
        else:
            try:
                record = json.loads(
                    (folder / "scan.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{folder.name}: scan.json cannot be read ({exc})")
            else:
                if not isinstance(record, dict):
                    problems.append(f"{folder.name}: scan.json is not a record "
                                    f"(a JSON {type(record).__name__})")
    archives: dict[str, int] = {}
    for record in entries(root):
        path = entry_path(root, record)
        if str(record.get("id")) != path.name:
            problems.append(f"{path.name}: records itself as {record.get('id')}")
        problems += [f"{path.name}: {p}" for p in damage(path, record)]
        named = ((record.get("extra") or {}).get("shading_origin") or {}).get("archive")
        if named:
            archives[str(named)] = archives.get(str(named), 0) + 1
        image = record.get("image") or {}
        if not (path / str(image.get("file", "scan.tif"))).exists():
            continue
        cal = record.get("calibration") or {}
        if not cal.get("shading"):
            # Say which kind this is. A scan deliberately taken raw and one that
            # wanted correction and silently went without look the same here
            # otherwise, and only the second is a thing that went wrong. The
            # first is not a problem at all: reported as one -- with the words
            # "correction was asked for", about the sentinel that says it was
            # not -- it kept `make verify` red for a week, and a check that is
            # always red is a check nobody reads.
            why = cal.get("skipped")
            # A demo entry built from a finished picture -- a stored prescan,
            # a test card -- has no calibration to describe it by design; it is
            # not evidence, and listing it here buries what is.
            demo = bool((record.get("extra") or {}).get("demo"))
            if (why != SHADING_SKIPPED_EXPLICIT and not demo
                    and ON_PURPOSE not in (record.get("tags") or ())):
                problems.append(
                    f"{path.name}: no shading reference, so this scan can never "
                    f"be corrected"
                    + (f" -- correction was asked for: {why}" if why else "")
                )
        width = ((record.get("image") or {}).get("shape") or [None, None])[1:2]
        if (cal.get("shading") and not cal.get("ccd_mask") and width
                and cal.get("pixels_per_line") not in (None, width[0])):
            # `corrected()` refuses these rather than guess which columns the
            # pass read; said here too, as the other entries that can never be
            # corrected are.
            problems.append(
                f"{path.name}: a reference but no CCD mask, on a pass "
                f"{width[0]} columns wide against its {cal['pixels_per_line']}"
                f", so it cannot be corrected")
        if not (record.get("raw") or {}).get("file"):
            problems.append(
                f"{path.name}: no raw bytes, so it cannot be re-decoded"
            )
    # The calibrations the entries name, once each. They live outside the
    # library, behind a path recorded relative to wherever the scan ran from,
    # and nothing checked that one was still there or still its own bytes --
    # so deleting calibration/ cost every re-reduction silently.
    for named, count in sorted(archives.items()):
        where = archive_named(named)
        found = next((c for c in (where, root.parent / where)
                      if (c / CALIBRATION_RECORD).exists()), None)
        whose = f"named by {count} entr{'y' if count == 1 else 'ies'}"
        if found is None:
            problems.append(f"calibration {named} ({whose}) is missing: the "
                            f"lines behind their reference cannot be reduced "
                            f"again")
            continue
        try:
            read_calibration(found)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"calibration {named} ({whose}) is damaged: {exc}")
    return problems


def damage(path: Path | str, record: dict[str, Any]) -> list[str]:
    """What is wrong with the files one entry's record names. Empty if none.

    Integrity only: every file the record names is there and matches its
    checksum. Whether the entry is *complete* -- a reference, raw bytes at
    all -- is `verify`'s other half, and not asked here, so a caller about to
    rely on an entry (`prunable`, choosing which copy survives) can ask just
    this.
    """
    path = Path(path)
    found: list[str] = []
    image = record.get("image") or {}
    scan = path / str(image.get("file", "scan.tif"))
    if not scan.exists():
        return [f"{scan.name} is missing"]
    stopped = None
    if image.get("sha256") and _sha256(scan) != image["sha256"]:
        stopped = _stopped_rewrite(path, record, scan.name)
        found.append(stopped or f"{scan.name} does not match its checksum")
    cal = record.get("calibration") or {}
    named = {cal.get("shading"), cal.get("ccd_mask"),
             (record.get("prescan") or {}).get("file")}
    files = record.get("files") or {}
    # A file the record names and checksums is part of the entry whether or
    # not anything else points at it; only the reference and the mask were
    # checked for being there, so a lost prescan.tif passed as intact.
    for name in sorted(n for n in named | set(files) if n):
        if not (path / name).exists():
            found.append(f"{name} is missing")
        elif name in files and _sha256(path / name) != files[name]:
            # The prescan has no bytes of its own to prove it by. `compact`
            # swaps it after scan.tif, so it is let through only where
            # scan.tif has shown that compaction got that far.
            if not (stopped and name == "prescan.tif"
                    and (path / RAW_PLAIN).exists()):
                found.append(f"{name} does not match its checksum")
    raw = record.get("raw") or {}
    if raw.get("file"):
        if not (path / raw["file"]).exists():
            found.append(f"{raw['file']} is missing")
        elif raw.get("sha256"):
            data = read_raw(path)
            if data is None or hashlib.sha256(data).hexdigest() != raw["sha256"]:
                found.append("raw bytes do not match their checksum")
    # Every rewrite here goes beside and is renamed over, so a name left
    # behind is a write that stopped part way -- or one in progress while
    # this looks. Either way it is not part of the entry, and it was never
    # reported.
    for part in sorted(path.glob(".*.part")):
        found.append(f"{part.name} is a partial write left behind")
    return found


#: What `tools/library.py migrate-raw --write` keeps of the scan.tif it
#: replaces -- named here because `damage` has to recognise a run of it that
#: stopped part way.
MIGRATE_KEPT = "scan.before-migrate-raw.tif"


def _stopped_rewrite(path: Path, record: dict[str, Any], name: str) -> str | None:
    """Why `name` may disagree with its checksum and still be sound, or None.

    `compact` and `migrate-raw` both swap a TIFF in before they write the
    record that holds its new checksum, so a kill between the two left an
    entry whose pixels were exact and which `verify` called damaged -- a false
    alarm in the one check that exists to find real damage, and one that only
    a decode could tell from the real thing. That decode is made here, and
    only where the entry still shows the rewrite unfinished: `raw.bin` beside
    the record, which `compact` removes last, or the file `migrate-raw` keeps
    with the record not yet naming it. The state is still reported -- it is
    not finished -- but as what it is, with the command that finishes it.
    """
    if (path / RAW_PLAIN).exists() and _holds_the_decode(path, name):
        return ("compaction stopped part way; `tools/library.py compact "
                "--write` finishes it")
    if ((path / MIGRATE_KEPT).exists()
            and MIGRATE_KEPT not in (record.get("files") or {})
            and _holds_the_decode(path, name)):
        return ("migrate-raw stopped part way; `tools/library.py migrate-raw "
                "--write` finishes it")
    return None


# -- the calibration behind an entry's reference -------------------------------
#
# `shading.npz` is a reduction -- `calculate_shading`'s split into dark and
# light and its averaging -- of the calibration's own lines, and a reduction
# cannot be redone with better code once its input is gone. Those lines are
# archived per calibration (`DirectScanner.archive_calibration`), and nothing
# read them back: the archive was kept for a recomputation no code could make.


def archive_named(named: str) -> Path:
    """The archive a record names, as a path on this machine.

    The driver records it with `str()`, so an entry filed on Windows says
    ``calibration\\20260927T...``. Read on macOS or Linux that is one file
    name with a backslash in it, and a library carried across found every
    such archive missing. The folders are `calibration/<UTC time>` or a
    `--reference` directory's, never a name holding a backslash, so a
    backslash is always Windows' separator here.
    """
    return Path(PureWindowsPath(named).as_posix()) if "\\" in named else Path(named)


def calibrations(root: Path | str = DEFAULT_CALIBRATIONS) -> list[Path]:
    """Every archived calibration under `root`, oldest first."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.parent for p in root.glob(f"*/{CALIBRATION_RECORD}"))


def read_calibration(folder: Path | str) -> tuple[bytes, dict[str, Any]]:
    """One archived calibration's bytes, exactly as read, and its record.

    Raises OSError when a file is missing and ValueError when the bytes do not
    match the checksum taken as they arrived: a reduction of damaged lines
    would be a wrong reference presented as a better one.
    """
    folder = Path(folder)
    record = json.loads((folder / CALIBRATION_RECORD).read_text(encoding="utf-8"))
    data = (folder / "data.bin").read_bytes()
    if record.get("sha256") and hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError(f"{folder.name}: data.bin does not match its checksum")
    return data, record


def rebuild_reference(folder: Path | str) -> ShadingReference | None:
    """An archived calibration reduced again, by *today's* `calculate_shading`.

    What a better split or average would be applied through: build the
    reference from the lines, then correct an entry with it
    (`apply_shading(load(entry)[0], reference, mask)`). None when today's
    reduction finds no usable lines in them.
    """
    data, record = read_calibration(folder)
    return calculate_shading(data, int(record["pixels_per_line"]))


def same_reference(a: ShadingReference, b: ShadingReference) -> bool:
    """Whether two references would correct every pixel identically."""
    if (a.pixels_per_line != b.pixels_per_line or a.channels != b.channels
            or sorted(a.dark) != sorted(b.dark)):
        return False
    return (all(np.array_equal(a.ref[c], b.ref[c]) and a.mean[c] == b.mean[c]
                for c in a.channels)
            and all(np.array_equal(a.dark[c], b.dark[c])
                    and a.dark_mean[c] == b.dark_mean[c] for c in a.dark))


def recalibrate(folder: Path | str) -> tuple[ShadingReference | None, Verdict]:
    """:func:`reconstruct`, for the reference half of the correction.

    Reduces an archived calibration's lines with today's code and says
    whether that is still the reference kept beside them. A mismatch is
    where a change to `calculate_shading` shows up -- on every calibration
    ever archived, and through them on every entry each one corrects.
    """
    folder = Path(folder)
    try:
        data, record = read_calibration(folder)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, Verdict(f"could not read the calibration: {exc}", DAMAGED)
    try:
        rebuilt = calculate_shading(data, int(record["pixels_per_line"]))
    except (KeyError, ValueError, TypeError) as exc:
        return None, Verdict(f"could not reduce: {exc}", FAILED)
    kept = record.get("reference")
    if not kept or not (folder / kept).exists():
        return rebuilt, Verdict("no reference was kept beside it to compare "
                                "with", NOTHING)
    try:
        stored = ShadingReference.load(folder / kept)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as exc:
        return rebuilt, Verdict(f"could not read {kept}: {exc}", DAMAGED)
    if rebuilt is None:
        return None, Verdict("reduction CHANGED: today's finds no usable "
                             "lines", CHANGED)
    if same_reference(rebuilt, stored):
        return rebuilt, Verdict("identical to the kept reference", IDENTICAL)
    return rebuilt, Verdict("reduction CHANGED: today's reference differs "
                            "from the one kept", CHANGED)


def calibration_of(path: Path | str, record: dict[str, Any] | None = None,
                   search: tuple[Path | str, ...] = ()) -> Path | None:
    """The archived calibration an entry's reference was reduced from.

    Where the record names one (`extra.shading_origin.archive`), found as
    written or beside the entry's library -- the path was recorded relative
    to wherever the scan ran from. A reference *loaded* from the cache names
    none, only the cache every calibration overwrites; those are matched by
    content instead, against every archive under ``search`` (by default
    `calibration/` here and beside the library), since the cache and the
    archive are the same reference written twice. None when nothing matches.
    """
    path = Path(path)
    if record is None:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    origin = (record.get("extra") or {}).get("shading_origin") or {}
    named = origin.get("archive")
    if named:
        where = archive_named(str(named))
        for candidate in (where, path.parent.parent / where):
            if (candidate / CALIBRATION_RECORD).exists():
                return candidate
    ref_file = (record.get("calibration") or {}).get("shading")
    if not ref_file or not (path / ref_file).exists():
        return None
    try:
        mine = ShadingReference.load(path / ref_file)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None
    roots = search or (DEFAULT_CALIBRATIONS, path.parent.parent / "calibration")
    seen: set[Path] = set()
    for root in roots:
        for folder in calibrations(root):
            key = folder.resolve()
            kept = folder / "shading.npz"
            if key in seen or not kept.exists():
                continue
            seen.add(key)
            try:
                if same_reference(mine, ShadingReference.load(kept)):
                    return folder
            except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
                continue
    return None
