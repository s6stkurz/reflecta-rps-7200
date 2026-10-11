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

One thing is shared between entries, and kept in the library beside them:
the calibration each reference is a reduction of, in
``calibrations/<sha256 of its lines>/`` (:data:`CALIBRATIONS`). Each
calibration is kept there once, however many entries it corrects, and an
entry whose reference names its calibration names it by that id (one filed
before 2026-09-28 by a path, found by content once the path is gone -- see
`calibration_of`), so a library copied whole carries the lines behind every
reference with it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import warnings
import zipfile
import zlib
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, TypeGuard

import numpy as np

from . import locks, storage, tiff
from .direct import (
    SHADING_SKIPPED_EXPLICIT,
    DirectScanner,
    ScanParameters,
    library_named,
    raw_bytes_disagree,
    spooled_for,
)
from .merge import Registration, lay_on, merge_infrared
from .protocol import ScanReadError
from .shading import ShadingReference, apply_shading, calculate_shading

DEFAULT_ROOT = Path("library")
INDEX = "index.json"

#: Where each calibration's own bytes are kept: in the library, one folder
#: per calibration named by the SHA-256 of its lines (`data.bin`) --
#: ``<library>/calibrations/<sha256>/`` -- which is the id an entry it
#: corrects records, as ``extra.shading_origin.archive_sha256``, where it
#: was filed since 2026-09-28 and its reference names its calibration
#: (`DirectScanner.archive_calibration`). Decided 2026-10-09 (Stefan). Kept
#: beside the cache, outside the library, a library copied or moved without
#: that folder lost the lines behind every reference in it; and named by
#: content, so archiving the same lines again, or copying an old archive in
#: twice, writes nothing.
CALIBRATIONS = "calibrations"

#: The folder of the cached reference, `calibration/shading.npz`, and where
#: each calibration's bytes were archived until 2026-10-09, one folder per
#: calibration named by its UTC time. Nothing but the cache is written here
#: now. The old archives are still read (`calibration_of`, `tools/library.py
#: calibrations`), and `tools/library.py migrate-calibrations` copies them
#: into the library -- copies: it never moves or deletes one.
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
        # The edge reader's, the same way. A field of its own, so the one
        # above still compares with every entry filed before this: the
        # package's digest never covered `tools/`, and a frame's position
        # comes from there. Hashed from the files beside the package when
        # this was imported -- which the window and `tools/scan_roll.py` do
        # beside the reader itself -- and recorded by every process, one
        # that never read an edge included: what was there, not that it ran.
        # None where the package travels without it.
        "edge_reader_source_sha256_at_import": _AT_IMPORT.get("edge_reader_sha256"),
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


#: The frame-edge reader (`tools/frame_edges`): beside this package, never
#: imported by it, and what places every frame the window and
#: `tools/scan_roll.py` centre.
EDGE_READER = Path(__file__).resolve().parent.parent / "tools" / "frame_edges"


def _source_digest(folder: Path | None = None) -> str | None:
    """One hash over every module in ``folder``, name and bytes, in order:
    this package's unless said. None where there are none to hash."""
    try:
        digest = hashlib.sha256()
        modules = sorted((folder or Path(__file__).resolve().parent).glob("*.py"))
        if not modules:
            return None
        for module in modules:
            digest.update(module.name.encode("utf-8") + b"\0")
            digest.update(module.read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


_AT_IMPORT: dict[str, Any] = {**_identity_now(), "source_sha256": _source_digest(),
                              "edge_reader_sha256": _source_digest(EDGE_READER)}


def code_identity() -> dict[str, Any]:
    """Which code this process runs, as it was imported. Asks nothing of git.

    For a record written where waiting is not free -- the window's Tk
    thread, a roll's frame records between frames with the device open --
    and where what counts is the code that ran, not the tree now.
    `provenance` asked git twice, ten seconds each at worst, from the
    window's thread.

    A frame's reading is named by the pair: the edge reader's source
    (`tools/frame_edges`) and the driver's, because the reader takes its
    constants and part of its code from `rps7200.framing`. Hashes rather
    than a revision number bumped by hand (Stefan's call, 2026-10-10): they
    cannot be forgotten, and a dirty or git-less tree has them too. Editing
    a comment changes one, so "the same code" means the same bytes; whether
    a reading still holds is answered by making it again, not by a label.
    """
    return {"driver_commit": _AT_IMPORT.get("commit"),
            "driver_dirty": _AT_IMPORT.get("dirty"),
            "driver_source_sha256": _AT_IMPORT.get("source_sha256"),
            "edge_reader_sha256": _AT_IMPORT.get("edge_reader_sha256")}


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


def _raw_disagrees(image: np.ndarray, meta: dict[str, Any],
                   layout: dict[str, Any] | None,
                   size: int) -> dict[str, list] | None:
    """Where the raw bytes being filed cannot be this picture's, as
    ``{field: [the picture's, the bytes']}``: empty when nothing says so,
    None when the picture is not one to judge.

    The writers' own question (`direct.raw_bytes_disagree`), asked once more
    here of everything filed, with the bytes' length besides. The session,
    `tools/scan_roll.py` and debug filing drop bytes that disagree before
    they get this far; a caller that does not ask -- `tools/scan.py`, a
    script, a stand-in -- filed whatever it held, under a record that said
    nothing. Shape only. The decode that would settle it is the heavy work
    nothing may do with the device open, and `reconstruct` does it later.
    """
    # A pass whose bytes nothing could decode is filed as its lines, one row
    # each, and says so (`DirectScanner._keep_failed_pass`): not a picture,
    # so no shape of one to hold the bytes against.
    failed = meta.get("failed")
    if isinstance(failed, dict) and failed.get("pixels", "decoded") != "decoded":
        return None
    # Never at the cost of the filing it describes: a layout this cannot
    # read is left unjudged, as an absent field is.
    try:
        found = raw_bytes_disagree(image.shape, layout, meta, size=size)
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return {k: [picture, held] for k, (held, picture) in found.items()}


def raw_disagreement(record: dict[str, Any]) -> str | None:
    """What an entry's record says of raw bytes that cannot be its pixels'.

    None for an entry whose bytes agree, or that was filed before
    `library.save` asked (no ``raw.disagrees`` at all). Such bytes are filed
    all the same -- they are what the scanner sent, and the one record of
    how a pass went wrong -- and this is how `verify`, `tools/library.py
    list` and the writer's notes say so.
    """
    found = (record.get("raw") or {}).get("disagrees")
    if not found or not isinstance(found, dict):
        return None
    detail = ", ".join(
        f"{k} {pair[0]} for the picture, {pair[1]} in the bytes"
        if isinstance(pair, (list, tuple)) and len(pair) == 2 else f"{k} {pair}"
        for k, pair in found.items())
    return (f"its raw bytes cannot be this picture's ({detail}); `reconstruct` "
            f"will not reproduce it from them")


@locks.letting_go
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

    Raw bytes laid out as some other picture -- another width, channel count
    or height, or a length the picture's rows cannot take -- are filed all
    the same, never refused or dropped here: they may be the one record of
    how a pass went wrong. The record says so, as ``raw.disagrees``, and so
    does a warning (`raw_disagreement`).
    """
    film = film or FilmNotes()
    when = created or datetime.now(timezone.utc)
    root = Path(root)
    path = _reserve(root, entry_id(meta, film, when))

    def describe() -> dict[str, Any]:
        stored = raw is not None or raw_path is not None
        expected = len(raw) if raw is not None else None
        if expected is None and raw_path is not None:
            try:
                expected = os.path.getsize(raw_path)
            except OSError:
                pass
        return {
            "id": path.name,
            "started_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "created": when.isoformat(timespec="seconds"),
            "resolution_dpi": meta.get("resolution_dpi"),
            "channels": meta.get("channels"),
            "channel_order": meta.get("channel_order"),
            "image": {"shape": list(image.shape), "dtype": str(image.dtype)},
            "raw": {"file": (RAW_FILE if compress else RAW_PLAIN) if stored
                    else None,
                    "bytes": expected, "layout": raw_layout},
            "roll_membership": meta.get("roll_membership"),
            "film": asdict(film),
            "tags": sorted(set(tags or [])),
            # Who is writing it, for `verify` to name while the marker is
            # held -- and only then: a marker nobody holds is a save that
            # did not finish, whoever started it.
            **_writer(),
        }

    # Present until the record is in place, so an entry a crash or a full
    # disk cut short says so rather than passing for a directory of files.
    # `verify` reports it; `entries()` never sees it (no `scan.json`).
    #
    # And it says which pass it was. It was a fixed sentence, and the record
    # is written last, so a kill or a power cut part way left raw bytes that
    # nothing named: which roll and frame, at what resolution, and the layout
    # a decode of them needs. All of that is known before the first byte.
    #
    # And held, locked, for as long as this save writes (`locks.hold_file`):
    # `verify` tells an entry being written now from one a failed save left
    # by that lock -- per save, never per process, since a window whose save
    # raised lives on for days. Let go however the save ends (`letting_go`).
    marker = _mark_unfinished(path, describe)

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
    # Asked of the bytes' layout and length against the picture's shape:
    # nothing decoded, so nothing heavy with the device open.
    disagrees = (_raw_disagrees(image, meta, raw_layout, raw_bytes)
                 if raw_bytes is not None else None)

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
            # Where these bytes cannot be the picture above, field by field
            # as [the picture's, the bytes'], and {} where nothing says so.
            # None where there are no bytes, or no picture to hold them
            # against. Absent on an entry filed before it was asked.
            "disagrees": disagrees,
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
            # What `apply_shading` reported as the pass was taken. Its
            # `corrected_channels` names the planes divided, the infrared one
            # among them on an RGBI pass; an entry filed before 2026-10-09
            # says the same only as `uncorrected: 0`.
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
    # The entry is complete, and nothing past this point may say otherwise --
    # the marker's removal included. `verify` reads every marker it meets,
    # this one too, and a removal refused while it reads made a whole entry
    # a failed save: the frame failed, the roll stopped, the pass filed a
    # second time by debug filing. Left there, the marker is `verify`'s to
    # name, beside a record whose checksums it checks as any other's.
    # Let go first: Windows refuses to remove a file that is open.
    marker.release()
    try:
        _unmark(path)
    except OSError as exc:
        warnings.warn(f"{path.name} is filed, but its {INCOMPLETE} marker "
                      f"could not be removed ({exc}); `make verify` names it, "
                      f"and it can be deleted once verify finds the entry "
                      f"otherwise whole", RuntimeWarning, stacklevel=2)
    _sync_dir(path)
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
    # Said once the entry is whole, so "filed" is true: the bytes are kept
    # and the record names the disagreement, which `verify` reports.
    said = raw_disagreement(record)
    if said:
        warnings.warn(f"{path.name} is filed with its raw bytes, but {said}",
                      RuntimeWarning, stacklevel=2)
    return path


#: The folder a pass the library refused is kept in instead, beside the
#: delivered copies it was written with (`session.keep_unfiled`); and where
#: it goes when there is no such folder, or it refuses as well.
UNFILED = "unfiled"
UNFILED_TEMP = "rps7200-unfiled"
#: Where `keep_unfiled` writes down, in the library that refused it, where
#: it kept a pass instead -- a roll's `unfiled/`, an output folder's, the
#: system's temporary directory -- so `leftovers` finds it wherever it is;
#: and where it writes that down when the library will not take even that.
#: Never in `failures/`: those are passes that failed with nothing to file.
UNFILED_ELSEWHERE = "unfiled-elsewhere"
UNFILED_POINTERS_TEMP = "rps7200-unfiled-pointers"
#: What the window's Delete renamed an entry to while it removed it, before
#: Delete moved entries into the trash instead; one a failed removal left is
#: still found (`leftovers`).
DELETING = ".deleting"

#: Where a library, or a rolls folder, keeps what was deleted from it: one
#: folder per delete, ``.trash/<UTC time>/``, holding what it took and
#: `TRASH_RECORD` (`trash`). Two levels down, so no `*/scan.json` glob --
#: `entries`, the roll joins, the demo's pictures -- ever sees a deleted
#: entry, and a dot name, which `verify` and every folder walk pass over.
TRASH = ".trash"
#: What one delete moved, why, by whom and when, in its own folder.
TRASH_RECORD = "trashed.json"
#: A delete's folder being emptied is renamed to this first, so a run cut
#: short is never offered back by `restore`, and the next run finishes it.
EMPTYING = ".emptying"

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

    And one a second call finishes. Before the first TIFF is swapped in, the
    record is told what each new one hashes to (:data:`COMPACTING`), so a
    TIFF that disagrees with its checksum and matches that note is this
    compaction's own. An entry stopped by code from before the note has only
    its raw bytes to show it: `raw.bin` goes only after the record, and there
    `scan.tif` is proved against the decode of its bytes, and `prescan.tif`
    -- which has no bytes of its own -- is accepted only where `scan.tif`,
    swapped before it, shows the compaction got that far. Anything else stops
    here, left as it is.
    """
    path = Path(path)
    plain = path / RAW_PLAIN
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    pending = record.get(COMPACTING) or {}
    # An entry filed plain with no raw bytes at all -- the shape guard refused
    # them, or the demo had none to give -- still has TIFFs to deflate. This
    # returned before reaching them, and they stayed uncompressed for good.
    # One whose TIFFs were swapped before a stop has nothing plain left to
    # show, and only its record's `compacting` says it is not finished.
    if not plain.exists() and not _uncompressed_tiffs(path) and not pending:
        return False
    _room_to_compact(path, plain)
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
                or _sha256(path / name) in (said, pending.get(name)):
            continue
        if name == "scan.tif":
            swapped = _holds_the_decode(path, name)
        if not swapped:
            raise OSError(f"{path.name}: {name} does not match its checksum; "
                          "left as it is")
    if plain.exists():
        raw = record.setdefault("raw", {})
        temp = path / f".{RAW_FILE}.part"
        digest = hashlib.sha256()
        # Removed on any failure: a full disk half-way through a 3600 dpi
        # pass left a hundred megabytes of it behind.
        try:
            with open(plain, "rb") as src, \
                    gzip.open(temp, "wb", compresslevel=6) as fh:
                while chunk := src.read(8 << 20):
                    digest.update(chunk)
                    fh.write(chunk)
            if raw.get("sha256") and digest.hexdigest() != raw["sha256"]:
                raise OSError(f"{path.name}: {RAW_PLAIN} does not match its "
                              "checksum; left as it is")
            # On the disk before it is named: `raw.bin` goes at the end, and
            # renamed in unsynced, a power cut after that could leave a gzip
            # of zeros under the name and no plain bytes beside it -- the
            # one file nothing can be derived back into, lost both ways.
            _sync(temp)
            _replace(temp, path / RAW_FILE)
        finally:
            _discard(temp)
        raw["file"] = RAW_FILE
    # Every TIFF is written beside first, and the record told what each will
    # hash to before any is swapped in. A stop between the swaps and the final
    # record otherwise left TIFFs failing their checksums, and in an entry
    # with no raw bytes nothing could prove them -- there is no decode -- and
    # a second compact found nothing uncompressed and gave up: intact pixels
    # reported damaged for good.
    parts: dict[str, Path] = {}
    try:
        for name in ("scan.tif", "prescan.tif"):
            if (path / name).exists():
                pixels = tiff.read(str(path / name))
                resolution = ((record.get("scan") or {}).get("resolution_dpi")
                              if name == "scan.tif" else None) or None
                parts[name] = path / f".{name}.part"
                tiff.write(str(parts[name]), pixels, resolution=resolution)
                _sync(parts[name])
        if parts:
            record[COMPACTING] = {name: _sha256(temp)
                                  for name, temp in parts.items()}
            _keep_tags(path, record)
            _write_atomic(path / "scan.json",
                          json.dumps(record, indent=2, default=_plain))
        for name, temp in parts.items():
            _replace(temp, path / name)
    finally:
        for temp in parts.values():
            _discard(temp)
    for name, digest_now in (record.pop(COMPACTING, None) or {}).items():
        if name == "scan.tif":
            record.setdefault("image", {})["sha256"] = digest_now
        else:
            record.setdefault("files", {})[name] = digest_now
    _keep_tags(path, record)
    # Renamed in with the folder synced (`_replace`), as the gzip was: both
    # names are durable before the plain bytes go.
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=_plain))
    plain.unlink(missing_ok=True)
    return True


def _keep_tags(path: Path, record: dict[str, Any]) -> None:
    """Keep in ``record`` every tag its entry's scan.json has gained since
    ``record`` was read from it.

    `compact` reads the record, spends minutes gzipping, and writes it back
    whole; `tools/library.py tag` beside it -- which only ever adds -- lost
    its tag to that write. Tags only ever grow, so what is on disk now is
    added to what is held, never put in its place.
    """
    try:
        now = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    tags = now.get("tags") if isinstance(now, dict) else None
    if isinstance(tags, list):
        record["tags"] = sorted(set(record.get("tags") or ())
                                | {t for t in tags if isinstance(t, str)})


def _room_to_compact(path: Path, plain: Path) -> None:
    """Refuse a compaction its disk cannot finish, before anything is touched.

    Everything is written beside before the plain files go, and `raw.bin`
    goes only at the very end, so for a while the entry holds both forms:
    measured on the library, 1.82-1.86 times a pass's pixels more than its
    plain size. Taken here at its worst -- gzip and deflate gaining nothing,
    the plain raw bytes and both TIFFs written again in full -- with
    `storage.SPACE_RESERVE` kept free besides. A full disk used to show
    itself part way through, as a gzip cut short. A disk whose room cannot
    be read is let through: what compaction writes is removed again if it
    fails, and the entry stays plain and whole either way.
    """
    need = 0
    for file in (plain, path / "scan.tif", path / "prescan.tif"):
        try:
            need += file.stat().st_size
        except OSError:
            pass
    short, _unknown = storage.check_space([(path, need)])
    if short:
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            free = 0
        raise OSError(f"{path.name}: {free / 1e9:.1f} GB free, compacting can "
                      f"need up to {(need + storage.SPACE_RESERVE) / 1e9:.1f} "
                      "GB with what is kept free; it stays plain and complete "
                      "-- tools/library.py compact --write finishes it later")


#: The record's note, written by :func:`compact` before it swaps a TIFF in, of
#: the checksum each will have: a TIFF matching it is the compaction's own,
#: stopped before the record caught up, and not damage.
COMPACTING = "compacting"


def _discard(temp: Path) -> None:
    """Remove what a failed write left beside, without hiding why it failed.

    Called from a `finally`: on Windows a scanner for viruses still holding
    the file makes the unlink raise PermissionError too, which would replace
    the error that says what actually went wrong. Left there, it is a `.part`
    `verify` names.
    """
    try:
        temp.unlink(missing_ok=True)
    except OSError:
        pass


def wants_compacting(path: Path | str) -> bool:
    """Whether :func:`compact` has anything to do in this entry.

    Plain raw bytes, a compaction a stop left unfinished, or TIFFs still
    uncompressed -- the same test `compact` makes. The last is an entry the
    window filed plain without bytes and was killed before compacting, which
    looking for `raw.bin` alone never found. An entry with no record is not
    one: it is still being written, or was cut short, and `verify` names it.
    """
    path = Path(path)
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if (path / RAW_PLAIN).exists() or (
            isinstance(record, dict) and record.get(COMPACTING)):
        return True
    return _uncompressed_tiffs(path)


def _uncompressed_tiffs(path: Path) -> bool:
    """Whether a stored TIFF here is plain and could be deflated.

    Only asked where there are no plain raw bytes to say the entry was filed
    plain. False without tifffile, which is the only writer that compresses.
    """
    if not tiff._has_tifffile():
        return False
    import tifffile                                      # noqa: PLC0415

    for name in ("scan.tif", "prescan.tif"):
        if not (path / name).exists():
            continue
        try:
            with tifffile.TiffFile(path / name) as stored:
                if int(stored.pages[0].compression) == 1:    # none
                    return True
        except (OSError, ValueError, IndexError):
            continue
    return False


#: Marks an entry still being written. See :func:`save`. Its presence is what
#: marks it, and every reader asks only that; what it holds is for a person,
#: through :func:`unfinished`.
INCOMPLETE = "INCOMPLETE"

#: What the marker says first -- and, written before it described the pass,
#: all it said.
UNFINISHED = "this entry was being written and did not finish"


def _mark_unfinished(path: Path, describe: Any) -> locks.FileHold:
    """Write the `INCOMPLETE` marker, saying what `describe()` knows of the
    pass, and hold it locked until the caller lets go (`locks.hold_file`).

    Its presence is the part that matters, so nothing about the description
    may cost it: one that will not build or serialise leaves a marker naming
    the entry and nothing more. A plain write, not one beside and renamed: a
    kill part way leaves a marker that does not parse, which still marks the
    entry -- one beside, never renamed, would leave no marker at all. Nor
    may the lock cost it: a disk that will not lock a file gets the marker
    written plainly, and `verify` then calls it unfinished, as it did every
    marker before they were locked.
    """
    try:
        text = json.dumps({"incomplete": UNFINISHED, **describe()},
                          indent=2, default=_plain)
    except Exception:                                     # noqa: BLE001
        text = json.dumps({"incomplete": UNFINISHED, "id": path.name})
    return _write_marker(path / INCOMPLETE, text + "\n")


def _write_marker(marker: Path, text: str) -> locks.FileHold:
    """Write a marker through a lock held on it, or plainly where none can
    be had; either way the marker is there."""
    try:
        hold = locks.hold_file(marker)
    except OSError:
        marker.write_text(text, encoding="utf-8")
        _sync(marker)
        return locks.FileHold(None, marker)
    if hold.fd is not None:
        try:
            os.ftruncate(hold.fd, 0)
            os.lseek(hold.fd, 0, os.SEEK_SET)
            data = memoryview(text.encode("utf-8"))
            while data:
                data = data[os.write(hold.fd, data):]
            _fsync(hold.fd)
        except OSError:
            # Its text or not, the marker is there, which is what marks it.
            pass
    return hold


def _writer() -> dict[str, Any]:
    """Who writes a marker: this program, its process and its machine, for
    `verify` to name while the marker is held."""
    return {"what": locks.program(), "pid": os.getpid(),
            "host": platform.node()}


def _writing_now(marker: Path) -> str | None:
    """Who is writing the entry or calibration whose marker this is, where
    one is: its lock is held. None where it is free -- a save that stopped,
    in this process or any -- or cannot be asked.

    A marker is made and then locked, so one met in between is empty and
    free: one that young is asked again, once, a moment later.
    """
    held = locks.file_held(marker)
    if held is False:
        try:
            found = marker.stat()
            young = found.st_size == 0 and time.time() - found.st_mtime < 2.0
        except OSError:
            young = False
        if young:
            time.sleep(0.1)
            held = locks.file_held(marker)
    if not held:
        return None
    try:
        said = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        said = None
    said = said if isinstance(said, dict) else {}
    what = str(said.get("what") or "another process")
    return f"{what} (pid {said['pid']})" if said.get("pid") else what


def _unmark(path: Path) -> None:
    """Remove the `INCOMPLETE` marker, as patient with a holder as `_replace`.

    Since the marker describes its pass, `verify` opens every one it meets
    (:func:`unfinished`), the one a save is about to remove among them; and
    on Windows a file Python has open for reading -- without
    FILE_SHARE_DELETE, as every `open()` there is -- refuses an unlink with
    PermissionError. The indexer and Defender hold new files briefly too.
    """
    for wait in (*REPLACE_RETRY_S, None):
        try:
            (path / INCOMPLETE).unlink(missing_ok=True)
            return
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def unfinished(folder: Path | str) -> dict[str, Any] | None:
    """What an unfinished entry's marker says of it, or None if it is finished.

    A marker from :func:`save` since 2026-10-09 is a JSON object: the entry's
    id, when the save started, the resolution and channels, the raw bytes'
    file, length and layout, the roll membership, the film notes and tags.
    An older one is a sentence, and one a kill cut short may be neither;
    both are still markers, and come back as ``{}`` with ``"unreadable"``
    saying why where it was not the sentence.
    """
    marker = Path(folder) / INCOMPLETE
    if not marker.exists():
        return None
    try:
        text = marker.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return {"unreadable": str(exc)}
    if text.strip() in ("", UNFINISHED):
        return {}
    try:
        said = json.loads(text)
    except ValueError as exc:
        return {"unreadable": f"not the description save() writes ({exc})"}
    return said if isinstance(said, dict) else {
        "unreadable": f"a JSON {type(said).__name__}, not a description"}


def _describe_unfinished(folder: Path, said: dict[str, Any]) -> str:
    """One line of what an unfinished entry was, for `verify`."""
    parts = []
    if said.get("unreadable"):
        parts.append(f"its marker cannot be read: {said['unreadable']}")
    elif not said:
        parts.append("its marker is from before markers described the pass")
    if said.get("id") and str(said["id"]) != folder.name:
        parts.append(f"filed as {said['id']}")
    if said.get("started_utc"):
        parts.append(f"started {said['started_utc']}")
    taken = [f"{said['resolution_dpi']} dpi" if said.get("resolution_dpi") else "",
             f"{said['channels']} channels" if said.get("channels") else ""]
    if any(taken):
        parts.append(", ".join(t for t in taken if t))
    member = said.get("roll_membership")
    if isinstance(member, dict) and member.get("roll"):
        parts.append(f"roll {member['roll']} {member.get('kind') or 'pass'} "
                     f"{member.get('number')}")
    film = said.get("film")
    film = film if isinstance(film, dict) else {}
    named = [str(film["stock"])] if film.get("stock") else []
    if film.get("frame"):
        named.append(f"frame {film['frame']}")
    if named:
        parts.append(" ".join(named))
    if said.get("tags"):
        parts.append("tagged " + ", ".join(str(t) for t in said["tags"]))
    raw = said.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("file"):
        layout = raw.get("layout")
        layout = layout if isinstance(layout, dict) else {}
        parts.append(f"{raw['file']} expected"
                     + (f", {raw['bytes']} bytes" if raw.get("bytes") is not None
                        else "")
                     + (f", laid out {layout.get('width')} wide by "
                        f"{layout.get('lines')} lines of {layout.get('channels')} "
                        f"channels" if layout else ""))
    try:
        held = sorted(p.name for p in folder.iterdir() if p.name != INCOMPLETE)
    except OSError:
        held = []
    parts.append("holds " + (", ".join(held) if held else "nothing else"))
    if "scan.json" in held:
        # Killed between the record's rename and the marker's removal, or the
        # removal refused (`save` warns and carries on): the entry may be
        # whole, and only `verify`'s checksums below can say.
        parts.append("its record was written, so only removing this marker "
                     "was cut short or refused")
    return "; ".join(parts)


def _reserve(root: Path, name: str) -> Path:
    """Create this entry's directory, or the next free ``-N`` beside it.

    `mkdir` either creates the directory or fails because it exists, in one
    step, so two writers in the same second -- the window's writer and a debug
    flush, or the window and a tool -- can never be handed one directory. The
    check this replaces looked for `scan.json`, which the first writer only
    creates at the very end.

    The library's own folder is made where it is missing, and never the
    folders above it (`storage.make_root`): a library on a drive that is not
    plugged in raises DriveMissing here, and the pass is kept elsewhere
    (`session.keep_unfiled`), rather than a library made again on C:.
    """
    storage.make_root(root)
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
    temp = _beside(path)
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            _fsync(fh.fileno())
        _replace(temp, path)
    finally:
        _discard(temp)


def _beside(path: Path) -> Path:
    """The name a writer of ``path`` writes it under before renaming it
    over: beside it, and this process's and thread's own.

    For a file more than one writer rewrites -- `index.json`, an entry's
    record, a roll's manifests, which the roll's writer thread, the scanning
    thread, the window and `tools/scan_roll.py` all write. A file only one
    writer ever rewrites keeps a fixed name, which that writer's next run
    finds and replaces: a name of its own, left by a kill, would be left for
    good.
    """
    return path.with_name(
        f".{path.name}.{os.getpid()}-{threading.get_ident()}.part")


#: How long `_replace` waits between attempts to rename over a file someone
#: holds open. On Windows the rename fails outright while any handle lacks
#: FILE_SHARE_DELETE -- Python's own `open()` for reading, a sync client, the
#: indexer and Defender all briefly hold new files. The same waits as
#: `session.write_manifest`, for the same reason.
REPLACE_RETRY_S = (0.05, 0.05, 0.1, 0.1, 0.1)


def _replace(temp: Path, path: Path) -> None:
    """`os.replace`, patient with a file that is only briefly held open.

    And the new name made durable once it is in (`_sync_dir`): on POSIX a
    rename lives in the folder, and until the folder is synced a power cut
    can take it back after the data it names has reached the disk. Windows
    makes its own names durable, and there this costs nothing.
    """
    for wait in (*REPLACE_RETRY_S, None):
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)
    _sync_dir(path.parent)


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
        _fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync(fd: int) -> None:
    """Flush what was written through ``fd`` to the disk itself.

    `os.fsync` is that everywhere but macOS, where it hands the data to the
    drive and the drive may hold it in its own cache through a power cut;
    there `F_FULLFSYNC` asks the drive to write it. A filesystem that will
    not take that -- a network share, some external disks -- gets the plain
    fsync, which is all there is to ask of it. Raises what `os.fsync` does;
    `_sync` is the caller that lets that pass.
    """
    if sys.platform == "darwin":
        import fcntl                                     # noqa: PLC0415

        full = getattr(fcntl, "F_FULLFSYNC", None)
        if full is not None:
            try:
                fcntl.fcntl(fd, full)
                return
            except OSError:
                pass
    os.fsync(fd)


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
        _fsync(fd)
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
    try:
        tiff.write(str(temp), image, **kw)
        # On the disk before it is named: renamed in unsynced, a power cut
        # could leave zeros under the name -- for a prescan, with no bytes
        # of its own to rebuild it from.
        _sync(temp)
        _replace(temp, path)
    finally:
        # Nothing of a failed rewrite stays behind: `verify` reports a `.part`
        # as a partial write, and nothing else would ever remove it.
        _discard(temp)


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


def corrected(path: Path | str, rebuild: bool = False
              ) -> tuple[np.ndarray, dict[str, Any]]:
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
        "not rebuilt"  ``rebuild`` was asked for and could not be had
                     (`record["rebuild_error"]` says why); returned raw

    ``rebuild`` corrects with *today's* reduction of the calibration the
    entry's reference came from (`calibration_of`, `rebuild_reference`) in
    place of the `shading.npz` filed with it: the correction recomputed from
    the calibration's own lines, which is how a better `calculate_shading`
    is judged on the picture rather than on the reference. Off by default,
    because the stored reference is the one the entry was delivered with.
    `record["reference_rebuilt"]` names the archive used, and is there only
    when "applied": an entry filed corrected, taken raw, or with no mask was
    not divided by it. Where there is none, or it is damaged, or today's
    reduction finds no usable lines, the pixels come back raw as "not
    rebuilt" -- never corrected with the stored reference under the name of
    a rebuilt one, which a comparison of the two would read as "no change".

    "applied" covers every plane the reference carries, the infrared one
    included -- every reference on record has a channel 3 -- and
    `record["shading_report"]["corrected_channels"]` names the planes divided.
    Not on a slide's merged picture (below): that report is its RGB pass's,
    three planes, and the infrared plane's correction is its partner's,
    `record["infrared_corrected"]` and `record["infrared_shading_report"]`.

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

    **A slide's RGB pass comes back with its infrared.** A slide scanned with
    infrared from the window is two entries -- its RGB pass, which is the
    frame, and its RGBI pass, kept for the infrared -- and the picture that
    was delivered is the one with both (`with_infrared`). Every view and
    export of the frame wants that one, made again here with today's
    registration as with today's correction.
    """
    image, record = _corrected_one(path, rebuild)
    return with_infrared(path, image, record, rebuild=rebuild)


def _corrected_one(path: Path | str, rebuild: bool
                   ) -> tuple[np.ndarray, dict[str, Any]]:
    """:func:`correct` on :func:`_loaded`: one entry, without its partner.

    A rebuilt reference is named only where it was the one applied. It is
    put in the record before `correct` decides anything, and an entry filed
    corrected, taken raw, or with no mask came back named for an archive
    that divided nothing -- which a comparison keyed on the name reads as
    a rebuilt picture.
    """
    image, record = correct(*_loaded(path, rebuild))
    if record.get("corrected") != "applied":
        record.pop("reference_rebuilt", None)
    return image, record


def _loaded(path: Path | str, rebuild: bool
            ) -> tuple[np.ndarray, dict[str, Any]]:
    """:func:`load`, with today's reduction of the entry's calibration in
    place of its stored reference where ``rebuild`` asks for it."""
    image, record = load(path)
    if not rebuild:
        return image, record
    try:
        folder = calibration_of(path, record)
        if folder is None:
            raise LookupError("no archived calibration was found for its "
                              "reference")
        reference = rebuild_reference(folder)
        if reference is None:
            raise ValueError(f"today's reduction of {folder.name} finds no "
                             "usable lines")
    except (LookupError, OSError, ValueError, KeyError, TypeError) as exc:
        record["reference"] = None
        record["rebuild_error"] = str(exc)
        return image, record
    record["reference"] = reference
    record["reference_rebuilt"] = str(folder)
    record.pop("reference_error", None)
    return image, record


#: How many entries either side of a slide's RGB pass are searched for the
#: RGBI pass it was taken with. They are filed back to back by one writer, so
#: the two are neighbours; the margin is for a pass of something else filed
#: in between -- a debug-filed probe -- and costs a JSON read each.
PARTNER_SEARCH = 8


def infrared_partner(path: Path | str, record: dict[str, Any]) -> Path | None:
    """The entry holding the RGBI pass this RGB pass was paired with.

    None for any entry that is not a slide's RGB pass. The two share an
    ``infrared_pair`` id in their meta (`ScanSession._pair`); found among
    this entry's neighbours by that id, never by time or by name alone.
    """
    pair = (record.get("extra") or {}).get("infrared_pair") or {}
    if pair.get("role") != "colour" or not pair.get("id"):
        return None
    path = Path(path)
    root = path.parent
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
        at = names.index(path.name)
    except (OSError, ValueError):
        return None
    for name in names[max(0, at - PARTNER_SEARCH):at + PARTNER_SEARCH + 1]:
        if name == path.name:
            continue
        try:
            other = json.loads((root / name / "scan.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        theirs = ((other.get("extra") or {}).get("infrared_pair") or {}
                  if isinstance(other, dict) else {})
        if theirs.get("id") == pair["id"] and theirs.get("role") == "infrared":
            return root / name
    return None


def with_infrared(path: Path | str, image: np.ndarray, record: dict[str, Any],
                  rebuild: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
    """``image``, a slide's corrected RGB pass, with its RGBI pass's infrared.

    Anything else comes back as it went in. ``record["infrared"]`` says what
    happened where there was a partner to look for: ``"merged"``, or why
    not -- the partner is gone, unreadable, or would not register -- and
    then the picture is the RGB pass alone, which is the colour the frame
    was delivered with either way.

    The RGBI pass goes through :func:`correct` with its own entry's
    reference before it is laid on, so its infrared is divided only where
    that comes back ``"applied"`` (or ``"already"``, filed corrected): a
    partner with no reference, no mask, or raw on purpose is laid on raw,
    and that is still ``"merged"``.
    ``record["infrared_corrected"]`` is the partner's ``corrected`` and
    ``record["infrared_shading_report"]`` its report, whose
    ``corrected_channels`` include 3 where the infrared plane was divided.
    ``record["shading_report"]`` stays the RGB pass's own, and never names
    the infrared plane.

    **The map the entry was filed with is used where it has one** -- the
    registration is a measurement of where the two passes' pixels sit, which
    no change to the correction moves, and measuring it again costs about
    six seconds a 3600 dpi frame on every view and export. An entry filed
    before the map had row bands (the one-affine merge of 2026-10-03, which
    refused nine slides of twelve) is measured with today's code instead.

    ``rebuild`` is :func:`corrected`'s, for the partner too: its reference
    rebuilt from its own calibration's lines, as the RGB pass's was.
    """
    pair = (record.get("extra") or {}).get("infrared_pair") or {}
    if pair.get("role") != "colour":
        return image, record
    partner = infrared_partner(path, record)
    if partner is None:
        record["infrared"] = "its RGBI pass is not in this library"
        return image, record
    try:
        other, theirs = _corrected_one(partner, rebuild)
    except Exception as exc:                             # noqa: BLE001
        record["infrared"] = f"its RGBI pass {partner.name} cannot be read ({exc})"
        return image, record
    record["infrared_partner"] = partner.name
    # The infrared plane's own correction, which `shading_report` cannot say:
    # that one is the RGB pass's, three planes, and read for channel 3 it
    # called every merged slide's infrared undivided. Kept even where the
    # plane is not laid on, beside the partner's name; `record["infrared"]`
    # says whether it was.
    record["infrared_corrected"] = theirs.get("corrected")
    record["infrared_shading_report"] = theirs.get("shading_report")
    stored = Registration.from_record(pair.get("registration"))
    if stored is not None:
        record["infrared_registration"] = pair.get("registration")
        record["infrared"] = "merged"
        return lay_on(image, other, stored), record
    merged, registration = merge_infrared(image, other)
    record["infrared_registration"] = registration
    if merged is None:
        record["infrared"] = f"not laid on: {registration.get('reason')}"
        return image, record
    record["infrared"] = "merged, measured again"
    return merged, record


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
        record["corrected"] = (
            "not rebuilt" if record.get("rebuild_error")
            else "reference unreadable" if record.get("reference_error")
            else "no reference")
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
    # zlib.error too, which is not an OSError: most damage inside the deflate
    # stream ends in the gzip checksum, but a flip that breaks the stream's
    # own structure raises this instead -- between 1% and 6% of single flips,
    # depending on the data. It escaped every caller, and `make verify` and
    # `make reconstruct` both stopped with a traceback at that entry, never
    # reaching the ones after it.
    try:
        with gzip.open(path, "rb") as fh:
            return fh.read()
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error):
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
    # Anything, not only OSError and ValueError: it is only ever asked of a
    # file already failing its checksum, and a compressed TIFF cut short
    # raises zlib.error from the read. That escaped `damage`, which `verify`
    # does not guard, and `make verify` died with a traceback on the very
    # damage it exists to name. A file that will not read is not the decode.
    try:
        pixels = tiff.read(str(path / name))
        decoded = decode_raw(path)
        return bool(decoded is not None and decoded.dtype == pixels.dtype
                    and np.array_equal(decoded, pixels))
    except Exception:                                     # noqa: BLE001
        return False


#: What a :func:`reconstruct` verdict is, as :attr:`Verdict.kind`.
IDENTICAL = "identical"      # today's decode reproduces the stored pixels
CHANGED = "changed"          # it does not: shape, samples, type or direction
BEHIND = "behind"            # filed bottom-up before passes were turned upright
NOTHING = "nothing"          # nothing stored to decode or reproduce from
DAMAGED = "damaged"          # a stored file is there and cannot be trusted
FAILED = "failed"            # today's decode raised on the stored bytes
# A pass filed tagged failed because its bytes would not decode when it was
# taken (`DirectScanner._keep_failed_pass`): its scan.tif is the lines as they
# arrived, not a picture, so there is nothing a decode could reproduce.
KNOWN_FAILED = "known failed"    # and today's decode still raises on them
DECODES_NOW = "decodes now"      # and today's decode makes a picture of them
# Bytes filed knowing they cannot be the picture beside them (`raw.disagrees`,
# which `save` records and `verify` names): a decode of them that does not
# reproduce it says what the record already said, not that the decoder moved.
DISAGREED = "disagreed"


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


def _filed_undecoded(record: dict[str, Any]) -> str | None:
    """How a pass filed tagged failed failed, where its bytes never decoded.

    None for every other entry -- one that is not tagged failed, and one that
    failed after its decode or in a read whose lines did decode, whose
    scan.tif is a picture like any other and is compared like any other.
    Both the tag and `extra.failed` are asked for: an exemption from the
    regression check is not to be had on one word.
    """
    failed = (record.get("extra") or {}).get("failed")
    if "failed" not in (record.get("tags") or ()) or not isinstance(failed, dict):
        return None
    # `pixels` has said "decoded" or not since the first such entry; the
    # stage alone is the fallback for a record that somehow lacks it.
    if failed.get("pixels") == "decoded" or (
            "pixels" not in failed and failed.get("stage") != "in the decode"):
        return None
    return (f"filed tagged failed: it failed {failed.get('stage') or 'unsaid'}"
            f" ({failed.get('error')}), so scan.tif holds its lines as they "
            "arrived")


def reconstruct(path: Path | str) -> tuple[np.ndarray | None, Verdict]:
    """Decode this entry's raw bytes with the *current* code.

    Returns ``(image, verdict)``. The verdict says whether today's decode still
    reproduces the pixels stored at scan time -- which is the whole reason the
    bytes are kept. A mismatch is not necessarily a regression: it is where a
    deliberate change to the decode shows up, on every scan in the library at
    once rather than on the next one taken. `verdict.kind` says which of the
    module's kinds (:data:`IDENTICAL`, :data:`CHANGED` ...) it is.

    A pass filed tagged failed because its bytes would not decode is neither
    a match nor a regression: there is no picture stored to reproduce. It
    is :data:`KNOWN_FAILED` while today's decode still raises on its bytes,
    and :data:`DECODES_NOW`, with the picture, once one does. Damage to its
    files is damage as on any other entry, and a failed pass whose bytes did
    decode is compared like any other.

    An entry filed with raw bytes its record says cannot be its picture's
    (:func:`raw_disagreement`) is decoded and compared like any other, and
    is :data:`DISAGREED` where it would have been changed or failed: known
    since the day it was filed, so no news of the decoder. Damage to its
    files is damage, and bytes that reproduce it after all are identical.
    """
    path = Path(path)
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    # ValueError, not only the JSON one: a record that is not UTF-8 raises
    # UnicodeDecodeError, which is a ValueError and not a JSONDecodeError.
    except (OSError, ValueError) as exc:
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
    undecoded = _filed_undecoded(record)

    # Asked after the checksum, so damage is still named as damage. Counted
    # as a change, one entry filed this way kept `make reconstruct` red for
    # good, over bytes whose record said from the start that they are not
    # this picture's -- the false alarm BEHIND was made to end.
    disagrees = raw_disagreement(record)

    def changed(head: str, what: str) -> Verdict:
        if disagrees:
            return Verdict(f"as filed, {disagrees} -- and today's {head}: "
                           f"{what}", DISAGREED)
        return Verdict(f"{head} CHANGED: {what}", CHANGED)

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
        # Unless it raised on them the day they arrived, too, and the pass
        # was filed tagged failed for it. Called a regression, every such
        # entry kept `make reconstruct` red for good, over bytes that are
        # kept precisely because nothing could decode them.
        if undecoded:
            return None, Verdict(f"{undecoded}; today's decode still cannot: "
                                 f"{exc}", KNOWN_FAILED)
        if disagrees:
            return None, Verdict(f"as filed, {disagrees} -- and today's "
                                 f"decode raises on them: {exc}", DISAGREED)
        return None, Verdict(f"could not decode: {exc}", FAILED)
    if undecoded:
        # Nothing to compare: scan.tif is one row per line, tags included.
        # A decode that works now is news, and the entry stays as filed.
        return image, Verdict(
            f"{undecoded}; today's decode makes a "
            f"{' x '.join(str(n) for n in image.shape)} picture of them -- "
            "the decode has moved since, worth a look", DECODES_NOW)

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
        return image, changed("decode",
                              f"now {image.shape}, stored {stored.shape}")
    # `array_equal` compares values and not their type, and the type is not
    # a detail: `apply_shading` scales the reference by it, so 8-bit samples
    # coming back as uint16 with the same values would correct almost black
    # while every value still matched.
    if image.dtype != stored.dtype:
        return image, changed("decode",
                              f"now {image.dtype}, stored {stored.dtype}")
    recorded = ((record.get("scan") or {}).get("read_direction") or {}).get("direction")
    if recorded is not None and recorded != direction.state:
        return image, changed(
            "read direction", f"recorded {recorded}, the line tags now say "
            f"{direction.state} ({direction.why})")
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
    return image, changed(
        "decode", f"{differing} of {image.size} samples differ "
        f"({100 * differing / image.size:.3f}%)")


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
        if raw is None and _raw_on_disk(path):
            # There and unreadable is damage, not "no raw bytes": recorded as
            # unknown, the direction would never be asked again once the file
            # was restored. `tools/library.py` names it and carries on.
            raise OSError("raw bytes are stored but cannot be read -- damage, "
                          "see verify; left as it is")
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
    root: Path | str = DEFAULT_ROOT, keep: int = 1,
    named: frozenset[str] | set[str] = frozenset(),
) -> list[tuple[dict[str, Any], str]]:
    """Which entries are redundant, and why. Nothing is deleted here.

    Redundant means two things at once: the same request of the scanner
    (`signature`) *and* the same data (`same_data`). An entry that shares a
    signature but holds different bytes is kept, whatever `keep` says.

    Within a group the ones kept are chosen on what they can still be used for,
    not on age. First whether something names it: ``named`` is the ids a
    roll's or a walk's records name (`session.entries_named`) -- a roll
    frame's entry, a walk prescan's, the reference an approval was made on --
    and pruning the one they name left them naming nothing while its twin
    stayed. One named is never pruned at all, so no roll is left naming an
    entry `--delete` took and none needs marking for it. Then: an entry
    carrying its raw bytes and its calibration can be re-decoded and
    re-corrected, and one without cannot. Then on what else it carries that
    its twin may not -- a prescan, film notes, tags, the pass's own record --
    so a bare copy never survives a rich one. Ties go to the newest.

    Nor is one pruned for a twin that lacks its raw bytes or its calibration.
    Twins with no raw bytes on one side are the same by their decoded pixels
    (`same_data`), so a named copy with neither stood as the twin of one
    holding both, and the one with the only raw bytes of that scan went.

    And only once the survivor has been checked on disk. The choice is made
    from the records; a survivor whose raw bytes were truncated since would
    otherwise be kept while the intact copy went.
    """
    def is_named(record: dict[str, Any]) -> bool:
        return record.get("_dir") in named or record.get("id") in named

    def keeps(record: dict[str, Any]) -> tuple[bool, bool]:
        """Whether it holds its raw bytes, and its calibration."""
        return (bool((record.get("raw") or {}).get("file")),
                bool((record.get("calibration") or {}).get("shading")))

    def usefulness(record: dict[str, Any]) -> tuple:
        # Everything a byte-identical twin can still differ by. The newest
        # used to win outright, so a debug copy filed after close() -- the
        # pass alone, "captured with RPS7200_DEBUG on" -- could survive the
        # tool's own entry with its film notes, prescan and roll membership.
        carries = (bool(record.get("prescan")),
                   sum(1 for v in (record.get("film") or {}).values() if v)
                   + len(record.get("tags") or ())
                   + len(record.get("extra") or {}))
        return (is_named(record), *keeps(record), *carries,
                str(record.get("created")))

    def holds_more(record: dict[str, Any], twin: dict[str, Any]) -> bool:
        """Whether ``record`` holds raw bytes or a calibration ``twin``
        does not."""
        return any(mine and not theirs
                   for mine, theirs in zip(keeps(record), keeps(twin)))

    out = []
    for group in duplicates(root).values():
        ranked = sorted(group, key=usefulness, reverse=True)
        kept = ranked[:keep]
        for record in ranked[keep:]:
            if is_named(record):
                kept.append(record)
                continue
            # Redundant only if it holds the same data as one that stays. A
            # shared signature says the scanner was asked the same thing; it
            # cannot say the answer was the same picture. Film notes left
            # empty, or two strips filed under one day's default roll name,
            # gave different photographs one signature, and `--delete` then
            # destroyed all but one of them, raw bytes included.
            twin = next((k for k in kept if same_data(record, k)
                         and not holds_more(record, k)
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
        # ValueError, not only the JSON one: a record that is not UTF-8 --
        # edited by hand in an editor saving cp1252 -- raises
        # UnicodeDecodeError, and took `reindex`, `verify` and every later
        # `save`'s index with it. `verify` names it.
        try:
            record = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
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
    storage.make_root(root)
    index = root / INDEX
    # Beside and swapped in, like every other file here: a plain rewrite cut
    # short left half an index, and two at once left a torn one.
    _write_atomic(index, json.dumps(summary, indent=2))
    return index


# ---------------------------------------------------------------------------
# The trash: what Delete takes away, kept whole until it is emptied by hand
# ---------------------------------------------------------------------------
#
# Stefan's call, 2026-10-09. The window's Delete, the roll browser's Delete
# and `tools/library.py duplicates --delete` each removed what they took for
# good -- the only raw copy of a frame, a walk and the positions set on it,
# and once, under a twin rule that was wrong, different photographs. Now
# each moves it into `TRASH` beside where it was, with one rename: on the
# same disk, so it is instant and cannot stop half way, as an `rmtree` did on
# Windows with one file held open. `restore` puts it back the same way.
# Nothing empties itself: `empty_trash` is a command, run with the scanner
# closed, because removing gigabytes is the heavy local work that preceded a
# wedge with the device open. Until then the space stays used, and `verify`,
# the window's log and its roll browser say how much (`trash_said`).


@dataclass
class Trashed:
    """What one delete into the trash did (`trash`): the folder it made
    (None where nothing moved), the names moved in the order they went,
    each left where it was with why, and the bytes moved."""

    batch: Path | None
    moved: list[str] = field(default_factory=list)
    left: dict[str, str] = field(default_factory=dict)
    bytes: int = 0


@dataclass
class TrashBatch:
    """One delete's folder in a trash (`trash_batches`): what its record says
    and what is in it now. ``record`` is ``{}`` where `TRASH_RECORD` is
    missing or cannot be read; the folder is listed all the same."""

    path: Path
    record: dict[str, Any]
    names: list[str]
    bytes: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kind(self) -> str:
        kind = self.record.get("kind")
        if kind in ("entries", "rolls"):
            return str(kind)
        # A record that cannot be read: an entry is known by its record.
        return ("entries" if any((self.path / n / "scan.json").is_file()
                                 for n in self.names) else "rolls")

    @property
    def at(self) -> str:
        """When it was deleted, as UTC: its record's word, or its name's."""
        said = self.record.get("at")
        if isinstance(said, str) and said:
            return said
        when = _batch_time(self.path.name)
        return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "unknown"


@dataclass
class Restored:
    """What `restore` did: each name brought back with the batch it came from
    and the name it has now -- a roll whose name was taken since comes back
    as the next free one -- and each left in the trash, with why."""

    restored: list[tuple[str, str, str]] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)
    #: The record each restored item was trashed with, by its name now.
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Why `index.json` could not be rewritten, where it could not; the
    #: entries are back all the same.
    index_error: str | None = None


@dataclass
class Emptied:
    """What `empty_trash` removed, or would: its batches and their bytes,
    each one it could not finish and why, and the runs cut short it found.
    ``unfiled`` is each delete holding passes the library refused, by its
    name, with those passes (`unfiled_in_trash`): left out of ``batches``,
    and alone, unless they were asked for."""

    batches: list[TrashBatch] = field(default_factory=list)
    bytes: int = 0
    problems: list[str] = field(default_factory=list)
    unfinished: list[Path] = field(default_factory=list)
    unfiled: dict[str, list[Path]] = field(default_factory=dict)


def _utc_stamp() -> str:
    """Now, as a delete's folder is named: ``20261009T121314Z``."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _batch_time(name: str) -> datetime | None:
    """When a delete's folder was made, from its name; None where the name
    is no time (one renamed by hand). A second delete in one second is
    ``-2``, as `_reserve` names it."""
    try:
        return datetime.strptime(name.split("-", 1)[0], "%Y%m%dT%H%M%SZ"
                                 ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _move_patiently(source: Path, target: Path) -> None:
    """Rename one folder, waiting out a reader that briefly holds a file in
    it, as `_replace` does: on Windows a folder with any file open inside
    cannot be renamed -- the window's own full-resolution reader, Explorer's
    preview, Defender. Never over anything: a ``target`` that exists raises
    FileExistsError, where POSIX would have put an empty folder of that name
    under the one moved."""
    if os.path.lexists(target):
        raise FileExistsError(f"{target} is there already")
    for wait in (*REPLACE_RETRY_S, None):
        try:
            os.rename(source, target)
            break
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)
    _sync_dir(source.parent)
    _sync_dir(target.parent)


def _pair_of(record: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """A slide's pass pair, as ``(id, role)``; ``(None, None)`` for any other
    entry (`ScanSession._pair`)."""
    pair = ((record or {}).get("extra") or {}).get("infrared_pair") or {}
    if not isinstance(pair, dict) or not pair.get("id"):
        return None, None
    return str(pair["id"]), str(pair.get("role") or "")


def _read_record(path: Path) -> dict[str, Any] | None:
    """An entry's record, or None where it cannot be read."""
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _to_trash(root: Path, paths: Any, kind: str) -> list[Path]:
    """`trash`'s items, checked and in the order they go: each a folder
    directly in ``root``, by a name that is no dot name -- an entry holding
    its record, for ``kind`` "entries" -- or ValueError, naming it, before
    anything moves. A slide's colour pass goes ahead of its infrared pass,
    so the pass that is the frame never stays behind without the one its
    picture is made with."""
    if kind not in ("entries", "rolls"):
        raise ValueError(f"unknown kind of trash {kind!r}")
    try:
        home = root.resolve()
    except OSError as exc:
        raise ValueError(f"{root} cannot be looked at ({exc})") from exc
    items: list[Path] = []
    for given in paths:
        path = Path(given)
        try:
            parent = path.resolve().parent
        except OSError as exc:
            raise ValueError(f"{path} cannot be looked at ({exc})") from exc
        if path.name.startswith(".") or parent != home or not path.is_dir():
            raise ValueError(f"{path} is not a folder in {root}; nothing was "
                             "moved")
        if kind == "entries" and not (path / "scan.json").is_file():
            raise ValueError(f"{path.name} holds no scan.json, so it is no "
                             "entry; nothing was moved")
        if all(p.name != path.name for p in items):
            items.append(root / path.name)
    if kind != "entries":
        return items
    pairs = {p.name: _pair_of(_read_record(p)) for p in items}
    colours = {pid for pid, role in pairs.values() if role == "colour"}
    ordered: list[Path] = []
    waiting: dict[str, list[Path]] = {}
    for path in items:
        pid, role = pairs[path.name]
        if role == "infrared" and pid in colours and not any(
                pairs[p.name] == (pid, "colour") for p in ordered):
            waiting.setdefault(str(pid), []).append(path)
            continue
        ordered.append(path)
        if role == "colour":
            ordered += waiting.pop(str(pid), [])
    return ordered + [p for left in waiting.values() for p in left]


def trash(root: Path | str, paths: Any, *, by: str, why: str = "",
          kind: str = "entries",
          before: Any = None) -> Trashed:
    """Move ``paths`` -- entries of the library ``root``, or roll folders of
    the rolls folder ``root`` for ``kind`` "rolls" -- into one new folder of
    its trash, ``root/.trash/<UTC time>/``, each by one rename.

    Checked first, and nothing moves unless all pass (`_to_trash`). Then:
    each one's size, and `TRASH_RECORD` written in the new folder as
    ``moving``, naming ``by`` and ``why`` and, for an entry, the roll frame
    and the slide pair its record names; then ``before(batch)``, the caller's
    own record of the delete -- the window marks the roll frames each entry
    was (`session.ScanSession.trash_entries`) -- so that is on the disk
    before anything goes; then the moves; then the record again, as
    ``trashed``. One whose rename is still refused after the waits is left
    whole where it was, with why, and so is a slide's infrared pass whose
    colour pass was. A folder nothing went into is removed again.

    The index loses the entries moved (`_unindex`) and is not rebuilt: on
    the window's thread a rebuild reads every record in the library.

    Never touches `calibrations/`, the spool or `failures/`: an entry
    restored finds its calibration where it left it.
    """
    root = Path(root)
    items = _to_trash(root, paths, kind)
    if not items:
        return Trashed(None)
    records = ({p.name: _read_record(p) for p in items} if kind == "entries"
               else {})
    sizes = {p.name: _size(p) for p in items}
    said: dict[str, Any] = {
        "state": "moving", "kind": kind, "by": by, "why": why,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": str(root),
        "items": [_trash_item(p.name, sizes[p.name], records.get(p.name))
                  for p in items]}
    batch = _reserve(root / TRASH, _utc_stamp())
    record = batch / TRASH_RECORD
    try:
        _write_atomic(record, json.dumps(said, indent=2, default=_plain))
        if before is not None:
            before(batch)
    except BaseException:
        # Nothing moved: the folder goes with its record, and the caller
        # says why it could not start.
        _discard(record)
        with _quietly():
            batch.rmdir()
        raise
    moved: list[str] = []
    left: dict[str, str] = {}
    stayed: set[str] = set()
    for path in items:
        pid, role = _pair_of(records.get(path.name))
        if role == "infrared" and pid in stayed:
            left[path.name] = ("kept with its colour pass, which could not "
                               "be moved")
            continue
        try:
            _move_patiently(path, batch / path.name)
        except OSError as exc:
            left[path.name] = str(exc)
            if role == "colour" and pid is not None:
                stayed.add(pid)
            continue
        moved.append(path.name)
    if not moved:
        _discard(record)
        with _quietly():
            batch.rmdir()
        return Trashed(None, [], left, 0)
    said.update(state="trashed", moved=moved, left=left)
    with _quietly():
        # What moved is in the trash whatever this says; a record left saying
        # "moving" is noted by `leftovers` as a move cut short.
        _write_atomic(record, json.dumps(said, indent=2, default=_plain))
    if kind == "entries":
        _unindex(root, moved)
    return Trashed(batch, moved, left, sum(sizes[n] for n in moved))


class _quietly:
    """Run a block whose OSError costs nothing worth raising: a courtesy
    write or the removal of an empty folder."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, exc: Any, tb: Any) -> bool:
        return kind is not None and issubclass(kind, OSError)


def _trash_item(name: str, size: int,
                record: dict[str, Any] | None) -> dict[str, Any]:
    """One item of a delete's record: its name, its size and, for an entry,
    what its record says it was -- so `restore` can say which roll frame
    counts as scanned again without reading the entry, and a person reading
    the folder knows what is in it."""
    item: dict[str, Any] = {"name": name, "bytes": size}
    if record is not None:
        extra = record.get("extra") or {}
        for key in ("roll_membership", "infrared_pair"):
            if extra.get(key):
                item[key] = extra[key]
        frame = (record.get("film") or {}).get("frame")
        if frame:
            item["frame"] = frame
        if record.get("id"):
            item["id"] = record["id"]
    return item


def _unindex(root: Path, names: list[str]) -> None:
    """Drop ``names`` from `index.json`, as one small rewrite. A missing or
    unreadable index is left: it is derived, and the next filing rebuilds
    it whole (`reindex`)."""
    index = root / INDEX
    try:
        rows = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(rows, list):
        return
    gone = set(names)
    kept = [r for r in rows if not (isinstance(r, dict) and r.get("id") in gone)]
    if len(kept) != len(rows):
        with _quietly():
            _write_atomic(index, json.dumps(kept, indent=2))


def trash_batches(root: Path | str) -> list[TrashBatch]:
    """Every delete in ``root``'s trash, oldest first, with what it holds now.

    Its size from its record where the record says, and by looking where it
    does not -- so the window's roll browser lists a trash of gigabytes
    without walking every file in it. One being emptied (`EMPTYING`) is not
    listed: it is never restored.
    """
    trash_dir = Path(root) / TRASH
    try:
        folders = sorted(p for p in trash_dir.iterdir()
                         if p.is_dir() and not p.name.endswith(EMPTYING))
    except OSError:
        return []
    out = []
    for folder in folders:
        try:
            record = json.loads((folder / TRASH_RECORD).read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_dir())
        except OSError:
            names = []
        sizes = {str(i.get("name")): i.get("bytes")
                 for i in record.get("items") or () if isinstance(i, dict)}
        total = 0
        for name in names:
            size = sizes.get(name)
            total += (int(size) if isinstance(size, int) and size >= 0
                      else _size(folder / name))
        out.append(TrashBatch(folder, record, names, total))
    return out


def in_trash(root: Path | str, name: str) -> TrashBatch | None:
    """The newest delete in ``root``'s trash holding ``name``, or None."""
    for batch in reversed(trash_batches(root)):
        if name in batch.names:
            return batch
    return None


def trash_summary(root: Path | str) -> tuple[int, int, int, str | None]:
    """``(deletes, items, bytes, oldest)`` of ``root``'s trash: how many
    deletes it holds, how many entries or roll folders, their bytes, and
    when the oldest was made (None for an empty trash)."""
    batches = [b for b in trash_batches(root) if b.names]
    if not batches:
        return 0, 0, 0, None
    return (len(batches), sum(len(b.names) for b in batches),
            sum(b.bytes for b in batches), batches[0].at)


def trash_said(root: Path | str | None, rolls: Path | str | None = None
               ) -> str | None:
    """What the library's trash and the rolls' hold, as one line for the
    window's log, `verify` and the tools as they start; None where both are
    empty. Said because a Delete no longer frees the disk: until emptied,
    a 7200 dpi entry keeps its gigabyte and a roll folder its frames."""
    parts = []
    for place, one, many in ((root, "entry", "entries"),
                             (rolls, "roll folder", "roll folders")):
        if place is None:
            continue
        _batches, items, size, oldest = trash_summary(place)
        if items:
            since = f", the oldest since {oldest[:10]}" if oldest else ""
            parts.append(f"{items} {one if items == 1 else many} "
                         f"({size / 1e9:.1f} GB{since}) in {Path(place) / TRASH}")
    if not parts:
        return None
    return ("the trash holds " + " and ".join(parts) + " -- `tools/library.py "
            "trash` lists them, `restore` brings one back, `empty-trash "
            "--write`, with no window open, removes them for good")


def _free_name(root: Path, name: str) -> str:
    """``name``, or the next free ``<name>-2``, ``-3`` ... in ``root``."""
    if not os.path.lexists(root / name):
        return name
    for n in range(2, 1000):
        if not os.path.lexists(root / f"{name}-{n}"):
            return f"{name}-{n}"
    raise FileExistsError(f"no free name beside {name} in {root}")


def restore(root: Path | str, which: str) -> Restored:
    """Bring back from ``root``'s trash one delete, by its folder's name, or
    one thing in it, by its name -- an entry's id, a roll folder's name --
    from the newest delete that holds it. Raises LookupError where nothing
    in the trash is called ``which``.

    In the reverse of the order it went: a slide's infrared pass before its
    colour pass, and the colour pass only once that is back, so the frame is
    never in the library with its picture's infrared still in the trash.
    Restoring a colour pass by its id brings its infrared pass with it.

    An entry whose id the library holds again is left in the trash and said:
    two entries of one id cannot be told apart. A roll folder whose name is
    taken since comes back as the next free ``<name>-2``, said: a roll is
    its records, which keep its name inside (`session.recorded_roll_name`).

    Each by one rename, as it went. A delete emptied of everything is
    removed, its record last; one that still holds something keeps a record
    of what it holds. The index is rebuilt whole once entries are back --
    this is a command, not the window, and it reads every record anyway --
    and one that cannot be rewritten is said (``Restored.index_error``),
    never raised past what is already back. Never writes a roll's records:
    a frame whose entry is back counts as scanned again by itself
    (`session.scanned_frames`).
    """
    root = Path(root)
    batches = trash_batches(root)
    chosen = [(b, None) for b in batches if b.name == which]
    if not chosen:
        holding = [b for b in batches if which in b.names]
        chosen = [(holding[-1], which)] if holding else []
    if not chosen:
        raise LookupError(f"nothing called {which} is in {root / TRASH}")
    done = Restored()
    for batch, only in chosen:
        _restore_batch(root, batch, only, done)
    if any(b.kind == "entries" for b, _ in chosen) and done.restored:
        # The index is a summary nothing reads (see `save`): a held or
        # read-only index.json must not hide what is already back. Raised,
        # the command died with a traceback before saying what came back,
        # and run again said nothing called it was in the trash.
        try:
            reindex(root)
        except Exception as exc:                          # noqa: BLE001
            done.index_error = (f"{root / INDEX} could not be rewritten "
                                f"({exc})")
    return done


def _restore_batch(root: Path, batch: TrashBatch, only: str | None,
                   done: Restored) -> None:
    """`restore`'s work on one delete."""
    kind = batch.kind
    items = {str(i.get("name")): i for i in batch.record.get("items") or ()
             if isinstance(i, dict)}
    moved = [n for n in batch.record.get("moved") or () if n in batch.names]
    order = moved + [n for n in batch.names if n not in moved]
    pairs = ({n: _pair_of(_read_record(batch.path / n)) for n in order}
             if kind == "entries" else {})
    if only is not None:
        pid, role = pairs.get(only, (None, None))
        wanted = {only} | ({n for n in order if pairs.get(n) == (pid, "infrared")}
                           if role == "colour" else set())
        order = [n for n in order if n in wanted]
    # Last in, first out -- and every infrared pass ahead of the colour
    # passes whatever the record says, for one whose record is lost.
    order.reverse()
    order.sort(key=lambda n: pairs.get(n, (None, None))[1] != "infrared")
    held_back: set[str] = set()
    for name in order:
        pid, role = pairs.get(name, (None, None))
        if role == "colour" and pid in held_back:
            done.refused[name] = ("left in the trash with its infrared pass, "
                                  "which could not come back")
            continue
        target = name
        if kind == "entries" and os.path.lexists(root / name):
            done.refused[name] = (f"an entry called {name} is in {root} "
                                  "again; left in the trash")
            if role == "infrared" and pid is not None:
                held_back.add(pid)
            continue
        try:
            target = _free_name(root, name) if kind == "rolls" else name
            _move_patiently(batch.path / name, root / target)
        except OSError as exc:
            done.refused[name] = f"{exc}; left in the trash"
            if role == "infrared" and pid is not None:
                held_back.add(pid)
            continue
        done.restored.append((batch.name, name, target))
        done.items[target] = items.get(name, {"name": name})
    _settle_batch(batch)


def _settle_batch(batch: TrashBatch) -> None:
    """After a restore: a delete holding nothing more is removed, its record
    last; one holding something keeps a record of only that."""
    try:
        left = sorted(p.name for p in batch.path.iterdir() if p.is_dir())
    except OSError:
        return
    record = batch.path / TRASH_RECORD
    if not left:
        _discard(record)
        with _quietly():
            batch.path.rmdir()
        return
    said = dict(batch.record)
    said["items"] = [i for i in said.get("items") or ()
                     if isinstance(i, dict) and i.get("name") in left]
    said["moved"] = [n for n in said.get("moved") or () if n in left]
    with _quietly():
        _write_atomic(record, json.dumps(said, indent=2, default=_plain))


def unfiled_in_trash(rolls: Path | str) -> list[tuple[TrashBatch, Path]]:
    """Each pass the library refused that a roll Delete took with its roll:
    kept in the `unfiled/` of a roll folder now in the rolls' trash at
    ``rolls`` (`session.keep_unfiled`), with the delete holding it. Its raw
    bytes are nowhere else, so `leftovers` still calls it a problem there
    and `empty_trash` leaves its delete alone."""
    out = []
    for batch in trash_batches(rolls):
        for name in batch.names:
            try:
                kept = sorted(p for p in (batch.path / name / UNFILED).iterdir()
                              if p.is_dir())
            except OSError:
                continue
            out += [(batch, p) for p in kept]
    return out


def empty_trash(root: Path | str, older_than_days: float | None = None,
                write: bool = False, include_unfiled: bool = False) -> Emptied:
    """Remove for good what ``root``'s trash holds -- the deletes older than
    ``older_than_days`` where given -- or, unless ``write``, only say what
    that would be.

    A delete holding passes the library refused -- a roll taken with its
    `unfiled/` -- is left alone and said (``Emptied.unfiled``) unless
    ``include_unfiled``: their raw bytes are nowhere else, `verify` fails
    for them until each is moved into the library (Stefan's call on
    leftovers, 2026-10-09), and an emptying weeks after the Delete that
    alone named them took them with nothing said.

    Each delete is renamed to ``<name>.emptying`` first, in one step, so
    one this cuts short -- a kill, a file held open -- is never offered by
    `restore` again, and the next run with ``write`` finishes it before
    anything else. Never called by the window, which may have the scanner
    open: a command, run with the scanner closed, as `compact` is.
    """
    trash_dir = Path(root) / TRASH
    done = Emptied()
    try:
        done.unfinished = sorted(p for p in trash_dir.iterdir()
                                 if p.is_dir() and p.name.endswith(EMPTYING))
    except OSError:
        return done
    cutoff = (None if older_than_days is None else
              datetime.now(timezone.utc).timestamp() - older_than_days * 86400)
    held: dict[str, list[Path]] = {}
    for batch, kept in unfiled_in_trash(root):
        held.setdefault(batch.name, []).append(kept)
    for batch in trash_batches(root):
        when = _batch_time(batch.name)
        if cutoff is not None and (when is None or when.timestamp() > cutoff):
            continue
        if batch.name in held:
            done.unfiled[batch.name] = held[batch.name]
            if not include_unfiled:
                continue
        done.batches.append(batch)
        done.bytes += batch.bytes
    if not write:
        return done
    for folder in done.unfinished:
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            done.problems.append(f"{folder.name}: {exc}; the next empty-trash "
                                 "--write tries again")
    for batch in done.batches:
        going = batch.path.with_name(batch.name + EMPTYING)
        try:
            _move_patiently(batch.path, going)
        except OSError as exc:
            done.problems.append(f"{batch.name}: {exc}; left as it is, and "
                                 "it can still be restored")
            continue
        try:
            shutil.rmtree(going)
        except OSError as exc:
            done.problems.append(f"{batch.name}: {exc}; what is left of it is "
                                 f"in {going.name}, and the next empty-trash "
                                 "--write finishes it")
    with _quietly():
        trash_dir.rmdir()                # only where nothing is left in it
    return done


def failure_records(root: Path | str = DEFAULT_ROOT
                    ) -> list[tuple[Path, list[Path]]]:
    """The records of passes that failed with nothing to file, by folder.

    One small JSON each (`DirectScanner._record_failure`): a pass that
    stopped before a line arrived -- the wedge cases -- or kept none, with
    every command it sent, what came back and why it failed. Beside the
    entries, and in the system's temporary directory where the library
    could not be written; that one is shared by every library on the
    machine, so only the records there that name this library are its.
    Each folder that holds any, oldest record first.
    """
    def names_this(path: Path) -> bool:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(record, dict) and record.get("library") == mine

    mine = library_named(root)
    beside, fallback = DirectScanner.failure_folders(root)
    out = []
    for folder in (beside, fallback):
        # By the time in the name, and a second failure in the same second
        # (`-2`, `-3`) after the first.
        found = sorted(folder.glob("*.json") if folder.is_dir() else (),
                       key=lambda p: (p.name.split("_", 1)[0], len(p.name),
                                      p.name))
        if folder == fallback:
            found = [p for p in found if names_this(p)]
        if found:
            out.append((folder, found))
    return out


def verify(root: Path | str = DEFAULT_ROOT,
           notes: list[str] | None = None) -> list[str]:
    """Problems found in the library: missing files, checksum mismatches.

    ``notes``, where given, is handed what is worth saying and is no
    problem: calibrations the entries name that are intact and still kept
    outside the library, with the command that copies them in. Returned
    with the problems, that line made every library filed before
    2026-10-09 fail `make verify` with nothing damaged, and the exit could
    no longer tell such a library from a damaged one.

    And what is being written as this looks: an entry or a calibration
    whose marker its writer holds (`_writing_now`), and -- while anything
    holds the library (`locks.LIBRARY`) -- a ``.part`` beside an entry,
    which may be a write in progress as well as one that stopped.
    """
    root = Path(root)
    problems = []
    #: Who holds the library as this looks, for its partial writes.
    live = locks.holders(locks.LIBRARY, root)
    # What `entries()` cannot see: a directory with no record, or one a
    # crash or a full disk left half-written. Each is either a scan's bytes
    # with nothing to say what they are, or a gap nobody would otherwise
    # notice -- and `verify` saying "intact" over one is the failure.
    for folder in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else ():
        # The spool, the records of passes that failed with nothing to
        # file (`failure_records`) and the calibrations: none is an
        # entry. The second is counted by `tools/library.py verify`
        # rather than called one with no scan.json, the third checked
        # below.
        # A Delete that did not finish is `leftovers`'s, and a note: it says
        # whether the folder is still read as an entry, and how to clear it.
        if (folder.name.startswith(".")
                or folder.name in (DirectScanner.FAILURES_DIR, CALIBRATIONS,
                                   UNFILED_ELSEWHERE)
                or folder.name.endswith(DELETING)):
            continue
        said = unfinished(folder)
        writing = (_writing_now(folder / INCOMPLETE) if said is not None
                   else None)
        if writing is not None:
            # Its save holds the marker for as long as it writes: an entry
            # being filed now, by the window or a tool beside this, which
            # every filing used to turn into a problem until it finished.
            if notes is not None:
                notes.append(f"{folder.name}: being written now by {writing}")
        elif said is not None:
            # With what the marker says of the pass, so the bytes left behind
            # can be named -- which roll and frame, what layout -- rather than
            # only counted. A save that raised in a process still running
            # let go of its marker as it raised, and is one of these.
            problems.append(f"{folder.name}: was being written and did not "
                            f"finish -- {_describe_unfinished(folder, said)}")
        elif not (folder / "scan.json").exists():
            problems.append(f"{folder.name}: has no scan.json, so no check sees it")
        else:
            try:
                record = json.loads(
                    (folder / "scan.json").read_text(encoding="utf-8"))
            # A record that is not UTF-8 is a ValueError and not a JSON one;
            # it stopped `make verify` with a traceback instead of naming it.
            except UnicodeDecodeError as exc:
                problems.append(f"{folder.name}: scan.json cannot be read (not "
                                f"UTF-8: {exc})")
            except (OSError, ValueError) as exc:
                problems.append(f"{folder.name}: scan.json cannot be read ({exc})")
            else:
                if not isinstance(record, dict):
                    problems.append(f"{folder.name}: scan.json is not a record "
                                    f"(a JSON {type(record).__name__})")
    archives: dict[str, dict[str, Any]] = {}
    for record in entries(root):
        path = entry_path(root, record)
        if path.name.endswith(DELETING):
            continue
        # Compared as the same text, not the same code points: HFS+ hands a
        # name back decomposed where the record keeps it as typed, so every
        # entry of a roll called "Südtirol" on such a disk -- or copied off
        # one -- recorded itself as someone else.
        if (unicodedata.normalize("NFC", str(record.get("id")))
                != unicodedata.normalize("NFC", path.name)):
            problems.append(f"{path.name}: records itself as {record.get('id')}")
        for found in damage(path, record):
            if live and PARTIAL in found:
                # Nothing locks a `.part`: while a writer holds the library
                # it cannot be told from that writer's file in progress.
                # Not "safe to delete" then: it may be that write's own.
                if notes is not None:
                    found = found.removesuffix(f" -- {PART_REMEDY}")
                    notes.append(f"{path.name}: {found} -- or a write in "
                                 f"progress, while {locks.who(live)} holds "
                                 f"the library")
            else:
                problems.append(f"{path.name}: {found}")
        sha, named = archive_of(record)
        if sha or named:
            # By id where the entry carries one: entries corrected by one
            # calibration name it by the same lines, whatever path each
            # recorded.
            named_by = archives.setdefault(sha or f"path {named}", {
                "sha": sha, "paths": set(), "count": 0, "entry": path,
                "record": record})
            named_by["count"] += 1
            if named:
                named_by["paths"].add(named)
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
        # Bytes kept beside a picture they cannot be: said once at filing
        # (`save`), as a warning nobody may have read, and here every time.
        said = raw_disagreement(record)
        if said:
            problems.append(f"{path.name}: {said}")
    lost, outside = _calibration_problems(root, archives, live)
    if notes is not None:
        notes += outside
    return problems + lost


@dataclass
class Leftover:
    """One thing a crash, a kill or a failure left behind, and how to clear
    it (`leftovers`).

    ``problem`` is a scan that is not in the library: `verify` exits 1 for
    it until it is filed. Everything else is a note -- Stefan's call,
    2026-10-09. ``count`` is how many of ``kind`` this one is (the plain
    entries are one leftover), ``said`` the line for a person, the command
    or the move that clears it included.

    ``where`` is "rolls" for one found in the rolls folder and "library"
    for the rest, which is where `leftovers_said` counts it. Told by its
    kind's name, a partial write in a roll folder was counted under the
    library, and the one line the window and the tools open with pointed
    at the wrong folder.
    """

    kind: str
    path: Path
    said: str
    problem: bool = False
    count: int = 1
    where: str = "library"


#: The words each kind is counted in, for `leftovers_said`'s one line.
_LEFTOVER_WORDS = {
    "plain": ("entry still plain", "entries still plain"),
    "spool": ("spool left behind", "spools left behind"),
    "unfiled": ("pass kept outside the library",
                "passes kept outside the library"),
    "deleting": ("Delete that did not finish", "Deletes that did not finish"),
    "stray-part": ("partial write", "partial writes"),
    "roll-copy": ("part-copied roll folder", "part-copied roll folders"),
    "roll-copied": ("finished copy still marked unfinished",
                    "finished copies still marked unfinished"),
    "roll-deleting": ("roll Delete that did not finish",
                      "roll Deletes that did not finish"),
    "roll-orphan": ("folder of decisions with no roll",
                    "folders of decisions with no roll"),
    "unreadable-manifest": ("roll record set aside", "roll records set aside"),
    "trash-moving": ("move into the trash cut short",
                     "moves into the trash cut short"),
    "trash-emptying": ("empty-trash cut short", "empty-trash runs cut short"),
}

#: What a roll folder's own files are called: its frames, its walk's
#: prescans and its decisions. A folder holding these and no survey.json or
#: roll.json is what a roll left; one of probe TIFFs (`A01_p1.tif`) is not.
_ROLL_FILE = re.compile(r"^(approved\.json|(frame|prescan)\d+.*\.tif)$",
                        re.IGNORECASE)


def _is_part(name: str) -> bool:
    """A name a write beside leaves: ``.<name>....part...``, or the
    settings' ``<name>.part``."""
    return ".part" in name and (name.startswith(".") or name.endswith(".part"))


def _size(path: Path) -> int:
    """What a file, or a folder and everything in it, holds on disk."""
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def leftovers(root: Path | str = DEFAULT_ROOT, rolls: Path | str | None = None,
              quick: bool = False, also: tuple[Path | str, ...] = ()
              ) -> list[Leftover]:
    """What crashes, kills and failures left around the library at ``root``
    and the rolls at ``rolls``, each with what clears it.

    - ``plain``: entries filed plain and never compacted -- complete and
      verifiable (CLAUDE.md), so a note: `compact --write` gzips them.
    - ``spool``: debug spools no running program holds (`locks.SPOOL`) --
      in the library's `.spool`, and in the system's temporary directory
      where their records name this library. One holding passes nobody
      claimed is a **problem**: they are scans not in the library, and
      `file-spool` files them. A spool its session still holds is not
      reported: it files it as it closes.
    - ``unfiled``: passes the library refused, kept elsewhere
      (`session.keep_unfiled`) -- a roll's `unfiled/`, the temporary
      directory's `rps7200-unfiled` where they name this library, and
      wherever a record in the library's `unfiled-elsewhere/` says one
      went -- a roll's taken into the rolls' trash by a Delete included.
      Each a **problem** until moved into the library.
    - ``deleting``: an entry the window's Delete renamed and did not finish
      removing, from before Delete moved entries into the trash.
    - ``trash-moving`` and ``trash-emptying``: a move into the trash, or an
      empty-trash, that a kill cut short (`_trash_left`), in the library's
      trash and, with ``rolls``, in the rolls'.
    - ``stray-part``: a file written beside and never renamed in, at the
      library's top, in roll folders and in ``also`` -- the reference's
      folder, the settings'. Safe to delete. Inside an entry `verify`
      calls one damage, as it always has.

    With ``rolls``: a Duplicate that did not finish (``roll-copy``), one
    that finished and whose marker would not go (``roll-copied``), a roll
    Delete that did not finish (``roll-deleting``), a roll's own files with
    no survey.json or roll.json beside them (``roll-orphan``: the
    approved.json a roll that never started leaves), and a roll record set
    aside as unreadable (``unreadable-manifest``).

    ``quick`` is the window's look as it opens: an entry is plain when it
    holds `raw.bin`, and no record or TIFF header is read for it. Nothing
    here writes, files or compacts: those gzip, and must not run with a
    device open.
    """
    root = Path(root)
    found: list[Leftover] = []
    mine = library_named(root)
    temp = Path(tempfile.gettempdir())
    dirs = []
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        pass
    plain = 0
    for folder in dirs:
        name = folder.name
        if name.endswith(DELETING):
            whole = (folder / "scan.json").exists()
            found.append(Leftover(
                "deleting", folder,
                f"{name}: a Delete that did not finish"
                + (" -- still read as an entry; rename it back to its id, or "
                   "remove it" if whole else
                   " -- nothing in it is an entry any more; remove it by "
                   "hand")))
            continue
        if name.startswith(".") or name in (
                DirectScanner.FAILURES_DIR, CALIBRATIONS, UNFILED_ELSEWHERE):
            continue
        if (quick and (folder / RAW_PLAIN).exists()
                and (folder / "scan.json").exists()) or (
                not quick and wants_compacting(folder)):
            plain += 1
    if plain:
        found.append(Leftover(
            "plain", root,
            f"{plain} entr{'y is' if plain == 1 else 'ies are'} still plain "
            "-- complete and verifiable; `tools/library.py compact --write`, "
            f"with no window open, gzips {'it' if plain == 1 else 'them'}",
            count=plain))
    found += _spools_left(root, mine, temp)
    found += _unfiled_left(root, mine, temp, rolls)
    found += _trash_left(root, "library")
    live = locks.holders(locks.LIBRARY, root)
    for place in [root, *(Path(p) for p in also)]:
        try:
            parts = sorted(p for p in place.iterdir()
                           if p.is_file() and _is_part(p.name))
        except OSError:
            continue
        found += [Leftover("stray-part", part, _part_said(part, live))
                  for part in parts]
    if rolls is not None:
        found += _rolls_left(Path(rolls))
    return found


def _part_said(part: Path, live: list[dict[str, Any]]) -> str:
    """A write beside never renamed in, said: safe to delete -- or, while
    something holds the folder it is in, perhaps that writer's own file in
    progress, which nothing locks."""
    if live:
        return (f"{part}: {PARTIAL} -- or a write in progress, while "
                f"{locks.who(live)} holds it")
    return f"{part}: {PARTIAL} -- {PART_REMEDY}"


def _spools_left(root: Path, mine: str, temp: Path) -> list[Leftover]:
    """`leftovers`' spools: each nobody holds, with what it holds."""
    spools = []
    for place, only_mine in ((root / DirectScanner.DEBUG_SPOOL_DIR, False),
                             (temp, True)):
        try:
            candidates = sorted(p for p in place.glob(f"{DirectScanner.DEBUG_SPOOL_PREFIX}*")
                                if p.is_dir())
        except OSError:
            continue
        for spool in candidates:
            if only_mine and spooled_for(spool) != {mine}:
                # Every library's fallback, and a test's: only one whose
                # passes all name this library is this library's.
                continue
            spools.append(spool)
    found = []
    for spool in spools:
        if locks.holders(locks.SPOOL, spool):
            continue                    # its session files it as it closes
        unclaimed = claimed = 0
        try:
            sides = sorted(spool.glob("*-meta.json"))
        except OSError:
            sides = []
        for side in sides:
            try:
                record = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
            if isinstance(record, dict) and record.get("claimed"):
                claimed += 1
            else:
                unclaimed += 1
        # Pixels a kill left before their record was written: nothing says
        # what they are, so nothing can file them, and the spool they keep
        # is never removed.
        recorded = {side.name[:-len("-meta.json")] for side in sides}
        try:
            bare = sum(1 for image in spool.glob("*-image.npy")
                       if image.name[:-len("-image.npy")] not in recorded)
        except OSError:
            bare = 0
        unnamed = (f"; and {bare} picture{'' if bare == 1 else 's'} with no "
                   f"record beside {'it' if bare == 1 else 'them'}, which "
                   f"nothing can file -- delete {'it' if bare == 1 else 'them'}"
                   " once not wanted" if bare else "")
        size = _size(spool) / 1e9
        if unclaimed:
            found.append(Leftover(
                "spool", spool,
                f"{spool}: {unclaimed} pass{'' if unclaimed == 1 else 'es'} "
                "taken and never filed, and no running program holds the "
                f"spool ({size:.1f} GB) -- `tools/library.py file-spool` "
                f"files {'it' if unclaimed == 1 else 'them'}{unnamed}",
                problem=True, count=1))
        elif claimed:
            found.append(Leftover(
                "spool", spool,
                f"{spool}: {claimed} pass{'' if claimed == 1 else 'es'} the "
                "program that took them claimed, and has probably filed "
                "itself; nothing holds the spool -- `tools/library.py "
                f"file-spool --claimed` files them if not{unnamed}"))
        elif bare:
            found.append(Leftover(
                "spool", spool,
                f"{spool}: {bare} picture{'' if bare == 1 else 's'} with no "
                f"record beside {'it' if bare == 1 else 'them'}, which "
                "nothing can file -- delete the spool once "
                f"{'it is' if bare == 1 else 'they are'} not wanted"))
        else:
            found.append(Leftover(
                "spool", spool,
                f"{spool}: no pass left in it, only what passes shared -- "
                "`tools/library.py file-spool` removes it"))
    return found


def _unfiled_left(root: Path, mine: str, temp: Path,
                  rolls: Path | str | None) -> list[Leftover]:
    """`leftovers`' passes kept outside the library, each found once, and
    counted where it was first found: a roll's `unfiled/` is the rolls'.

    A roll the browser's Delete moved into the rolls' trash took its
    `unfiled/` with it, and those passes dropped out of here while their
    raw bytes were still nowhere else: they are looked for there too
    (`unfiled_in_trash`), and by a pointer whose roll folder went, and said
    with the delete that holds them."""
    kept: dict[str, tuple[Path, str, str | None]] = {}

    def names_me(entry: Path) -> bool | None:
        """Whether the pass kept here says it was refused by this library;
        None where it says nothing (kept before it said)."""
        try:
            record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            said = unfinished(entry) or {}
            record = {"extra": {"unfiled_from": said.get("unfiled_from")}}
        if not isinstance(record, dict):
            return None
        named = (record.get("extra") or {}).get("unfiled_from")
        return None if not named else named == mine

    def keep(entry: Path, where: str = "library",
             batch: str | None = None) -> None:
        try:
            key = os.path.normcase(str(entry.resolve()))
        except OSError:
            key = os.path.normcase(os.path.abspath(entry))
        kept.setdefault(key, (entry, where, batch))

    def entries_in(folder: Path) -> list[Path]:
        try:
            return sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return []

    def in_rolls_trash(gone: Path) -> None:
        """A pointer's pass whose roll folder is no longer where it was:
        in the trash of the rolls folder it was in, where a Delete took it."""
        if gone.parent.name != UNFILED:
            return
        roll = gone.parent.parent
        for batch, entry in unfiled_in_trash(roll.parent):
            if entry.parent.parent.name == roll.name and entry.name == gone.name:
                keep(entry, "rolls", batch.name)

    for entry in entries_in(temp / UNFILED_TEMP):
        if names_me(entry):
            keep(entry)
    if rolls is not None:
        for roll in entries_in(Path(rolls)):
            if roll.name == TRASH:
                continue                        # looked at below
            for entry in entries_in(roll / UNFILED):
                if names_me(entry) is not False:
                    keep(entry, "rolls")
        for batch, entry in unfiled_in_trash(Path(rolls)):
            if names_me(entry) is not False:
                keep(entry, "rolls", batch.name)
    inside = os.path.normcase(str(root.resolve())) if root.exists() else None
    for folder in (root / UNFILED_ELSEWHERE, temp / UNFILED_POINTERS_TEMP):
        try:
            pointers = sorted(folder.glob("*.json"))
        except OSError:
            continue
        for pointer in pointers:
            try:
                said = json.loads(pointer.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (not isinstance(said, dict) or said.get("library") != mine
                    or not said.get("kept")):
                continue
            entry = Path(str(said["kept"]))
            if not entry.is_dir():
                # Moved in, or cleared by hand -- or gone with its roll
                # into the rolls' trash.
                in_rolls_trash(entry)
                continue
            where = os.path.normcase(str(entry.resolve()))
            if inside is not None and (where == inside or where.startswith(
                    inside.rstrip(os.sep) + os.sep)):
                continue                    # in the library now
            keep(entry)
    return [Leftover(
        "unfiled", entry,
        f"{entry}: a pass the library refused, kept outside it "
        f"({_size(entry) / 1e9:.1f} GB) -- move that folder into {root} to "
        "file it" + ("" if batch is None else
                     f"; its roll folder is in the rolls' trash, which "
                     f"`tools/library.py restore {batch}` brings back with "
                     "it, and `empty-trash` leaves that delete alone until "
                     "it is filed"),
        problem=True, where=where)
        for entry, where, batch in kept.values()]


def _rolls_left(rolls: Path) -> list[Leftover]:
    """`leftovers`' half of the rolls folder, each counted as the rolls'."""
    found: list[Leftover] = _trash_left(rolls, "rolls")
    try:
        folders = sorted(p for p in rolls.iterdir() if p.is_dir())
    except OSError:
        return found
    for folder in folders:
        name = folder.name
        if name.startswith(".") and name.endswith(DELETING):
            found.append(Leftover(
                "roll-deleting", folder,
                f"{folder}: a roll Delete that did not finish -- it lists as "
                "no roll; remove it by hand", where="rolls"))
            continue
        if name.startswith("."):
            continue
        try:
            names = [p.name for p in folder.iterdir()]
        except OSError:
            continue
        if INCOMPLETE in names:
            who = locks.holders(locks.ROLL, folder)
            if who:
                found.append(Leftover(
                    "roll-copy", folder,
                    f"{folder}: being copied now by {locks.who(who)}",
                    where="rolls"))
            elif _copy_finished(folder, names):
                # The window kept it, and called it whole: a long copy is
                # not thrown away for a marker a reader held (`copy_roll`).
                found.append(Leftover(
                    "roll-copied", folder,
                    f"{folder}: the copy finished, but its {INCOMPLETE} "
                    "marker was left -- delete that file and it lists as a "
                    "roll", where="rolls"))
            else:
                found.append(Leftover(
                    "roll-copy", folder,
                    f"{folder}: a Duplicate that did not finish -- it lists "
                    "as no roll; delete it", where="rolls"))
            continue
        writing = (locks.holders(locks.ROLL, folder)
                   if any(_is_part(n) for n in names) else [])
        found += [Leftover("stray-part", folder / n,
                           _part_said(folder / n, writing), where="rolls")
                  for n in sorted(names) if _is_part(n)]
        found += [Leftover(
            "unreadable-manifest", folder / n,
            f"{folder / n}: a roll record that could not be read, set aside "
            "-- what it held can be read by hand; delete it once it is not "
            "wanted", where="rolls")
            for n in sorted(names) if ".unreadable" in n]
        if "survey.json" in names or "roll.json" in names:
            continue
        theirs = sorted(n for n in names if _ROLL_FILE.match(n))
        if theirs:
            what = (", ".join(theirs) if len(theirs) <= 3
                    else f"{', '.join(theirs[:3])} and {len(theirs) - 3} more")
            found.append(Leftover(
                "roll-orphan", folder,
                f"{folder}: {what}, with no survey.json or roll.json beside "
                + ("it" if len(theirs) == 1 else "them")
                + ", so it opens as no roll"
                + (" -- the positions and turns of a roll that never started"
                   if theirs == ["approved.json"] else "")
                + "; delete it once nothing in it is wanted", where="rolls"))
    return found


def _trash_left(place: Path, where: str) -> list[Leftover]:
    """`leftovers`' look in one trash (`TRASH`): a delete whose record still
    says it was moving, and an empty-trash a kill cut short. Notes both:
    whatever a cut-short move left is either back where it was or whole in
    the trash, and a delete being emptied is never offered back."""
    found: list[Leftover] = []
    try:
        folders = sorted(p for p in (place / TRASH).iterdir() if p.is_dir())
    except OSError:
        return found
    for folder in folders:
        if folder.name.endswith(EMPTYING):
            found.append(Leftover(
                "trash-emptying", folder,
                f"{folder}: an empty-trash cut short -- what is left of it "
                "is never restored; `tools/library.py empty-trash --write` "
                "finishes it", where=where))
            continue
        try:
            said = json.loads((folder / TRASH_RECORD).read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(said, dict) and said.get("state") == "moving":
            found.append(Leftover(
                "trash-moving", folder,
                f"{folder}: a move into the trash cut short -- what it holds "
                "went, the rest is where it was; `tools/library.py restore "
                f"{folder.name}` brings back what went", where=where))
    return found


def _copy_finished(folder: Path, names: list[str]) -> bool:
    """Whether a Duplicate's target that still holds its marker is a whole
    copy: every record its source held went in, and they go in last
    (`copy_roll`), after every file they name.

    By the records its marker lists (`reserve_copy`). For a marker that
    lists none, or cannot be read, survey.json, which goes in after the
    others; or roll.json where the source the marker names is there and
    has no walk. Anything else -- roll.json with no source to ask, or one
    that has gone -- is a copy that did not finish: deleting a whole one
    costs a copy, keeping a half one as whole costs the frames it lacks.
    """
    said = unfinished(folder) or {}
    records = said.get("records")
    if isinstance(records, list) and records:
        return all(str(r) in names for r in records)
    if "survey.json" in names:
        return True
    source = said.get("from")
    if "roll.json" not in names or not source:
        return False
    source = Path(str(source))
    return source.is_dir() and not (source / "survey.json").exists()


def leftovers_said(found: list[Leftover]) -> str | None:
    """`leftovers` as one line, for the window and the tools to say as
    they start; None when there is nothing. Each is counted under the
    folder it was found in (`Leftover.where`)."""
    if not found:
        return None
    counts: dict[tuple[str, str], int] = {}
    for item in found:
        key = (item.where, item.kind)
        counts[key] = counts.get(key, 0) + item.count
    unfiled = sum(1 for i in found if i.kind == "spool" and i.problem)
    parts: dict[str, list[str]] = {"library": [], "rolls": []}
    for where, said in parts.items():
        for kind, (one, many) in _LEFTOVER_WORDS.items():
            n = counts.get((where, kind))
            if not n:
                continue
            if kind == "spool" and unfiled:
                said.append(f"{n} {one if n == 1 else many}, {unfiled} with "
                            "passes not filed")
            else:
                said.append(f"{n} {one if n == 1 else many}")
    line = "; ".join(f"{where}: {', '.join(said)}"
                     for where, said in parts.items() if said)
    return f"left behind -- {line} -- `make verify` says what clears each"


#: How `damage` and `verify` say a file written beside and never renamed in.
PARTIAL = "is a partial write left behind"
#: And what to do with one: nothing reads a name written beside, and the
#: file it was for is the old one or the new one whole.
PART_REMEDY = "safe to delete"


def _calibration_problems(root: Path, archives: dict[str, dict[str, Any]],
                          live: list[dict[str, Any]] | None = None
                          ) -> tuple[list[str], list[str]]:
    """`verify`'s half for the calibrations: the ones the entries name, and
    every one the library keeps. Returns the problems, and apart from them
    the notes: which are still outside the library, and what is being
    written now -- ``live`` is who holds the library as `verify` looks.

    Each named one is looked for in the library by its id. Nothing checked
    that one was still there or still its own bytes while they lived beside
    the cache, so deleting `calibration/` cost every re-reduction silently;
    and an archive still there, outside the library, is not damage but a
    library that is not yet whole -- said once, with the command that copies
    them in, rather than once an archive or once an entry.

    An entry filed from 2026-09-24 to 28 names its archive by a path and no
    id. Where that path is gone -- the original deleted once
    `migrate_calibrations` had copied it in, or the library carried off
    without it -- its lines are looked for by content, as `calibration_of`
    finds them, before they are called missing: by path alone, an archive
    the library holds was reported lost.
    """
    def named(count: int) -> str:
        return f"named by {count} entr{'y' if count == 1 else 'ies'}"

    problems: list[str] = []
    outside: dict[Path, int] = {}
    inside: dict[str, int] = {}
    shelf = root / CALIBRATIONS
    kept: dict[Path, ShadingReference | None] = {}
    for _, named_by in sorted(archives.items()):
        sha, count = named_by["sha"], named_by["count"]
        whose = named(count)
        label = sha or sorted(named_by["paths"])[0]
        if sha and (calibration_folder(root, sha) / CALIBRATION_RECORD).exists():
            # Checked with the rest of the shelf, below, and said once.
            inside[sha] = inside.get(sha, 0) + count
            continue
        found = next((f for f in (_named_outside(root, n)
                                  for n in sorted(named_by["paths"])) if f),
                     None)
        if found is None and sha is None:
            mine = _entry_reference(named_by["entry"], named_by["record"])
            found = None if mine is None else _kept_as(
                mine, [*calibrations(shelf),
                       *calibrations(DEFAULT_CALIBRATIONS),
                       *calibrations(root.parent / "calibration")], kept)
            if found is not None and found.parent == shelf:
                inside[found.name] = inside.get(found.name, 0) + count
                continue
        if found is None:
            problems.append(f"calibration {label} ({whose}) is missing: the "
                            f"lines behind their reference cannot be reduced "
                            f"again")
            continue
        try:
            data, _record = read_calibration(found)
        except (OSError, ValueError) as exc:
            problems.append(f"calibration {label} ({whose}) is damaged: {exc}")
            continue
        # An entry from before the id was recorded names a path only; its
        # lines may be in the library all the same.
        its_id = hashlib.sha256(data).hexdigest()
        if sha is None and _intact(calibration_folder(root, its_id)):
            inside[its_id] = inside.get(its_id, 0) + count
            continue
        # Resolved: entries recorded the same folder absolute and relative,
        # and the note named it twice and counted its archives twice.
        found = found.resolve()
        outside[found] = outside.get(found, 0) + count
    elsewhere: list[str] = []
    if outside:
        where = sorted({str(f.parent) for f in outside})
        elsewhere.append(
            f"{len(outside)} calibration{'s' if len(outside) != 1 else ''} "
            f"named by {sum(outside.values())} entr"
            f"{'y' if sum(outside.values()) == 1 else 'ies'} "
            f"{'are' if len(outside) != 1 else 'is'} kept outside the library, "
            f"in {', '.join(where)} -- intact, not damage; `tools/library.py "
            f"migrate-calibrations --write` copies them in")
    # And the library's own, whoever names them: a copy a kill cut short,
    # or lines that are no longer the ones the folder is named for.
    for folder in sorted(shelf.iterdir()) if shelf.is_dir() else ():
        name = f"{CALIBRATIONS}/{folder.name}"
        if folder.name in inside:
            name += f" ({named(inside[folder.name])})"
        if folder.name.startswith("."):
            continue
        if not folder.is_dir():
            problems.append(f"{name}: is not a calibration")
            continue
        writing = ((folder / INCOMPLETE).exists()
                   and _writing_now(folder / INCOMPLETE))
        if writing:
            # Kept now, by the session that calibrated (`calibration_slot`).
            elsewhere.append(f"{name}: being written now by {writing}")
        elif (folder / INCOMPLETE).exists():
            problems.append(f"{name}: {_left_marked(folder)}")
        elif not (folder / CALIBRATION_RECORD).exists():
            problems.append(f"{name}: has no {CALIBRATION_RECORD}, so its "
                            f"lines have no width or stride")
        else:
            why = archive_damage(folder)
            if why:
                problems.append(f"{name} is damaged: {why}")
        for part in sorted(folder.glob(".*.part")):
            # As beside an entry: a `calibration.json` written beside and
            # renamed in by a session archiving now, which turned a `make
            # verify` run while the window calibrated into a failure.
            if writing:
                continue
            if live:
                elsewhere.append(f"{name}: {part.name} {PARTIAL} -- or a "
                                 f"write in progress, while "
                                 f"{locks.who(live)} holds the library")
            else:
                problems.append(f"{name}: {part.name} {PARTIAL}")
    return problems, elsewhere


def _left_marked(folder: Path) -> str:
    """What a library calibration still marked `INCOMPLETE`, and held by
    nobody, is: one whose marker alone was left, or one that did not finish.

    `archive_calibration` and `migrate_calibrations` keep a whole archive
    whose marker a reader held past `_unmark`'s waits, and say `verify`
    names it. It named it "did not finish", as it does one a kill cut
    short, and that archive holds the only copy of its calibration's lines.
    Whole is its record written no earlier than its marker -- a folder kept
    again, which rewrites the marker first, is still that marker's write
    until its record is new -- with its lines and files as the record says
    (`archive_damage`). The marker's time is its write's, not its
    release's: held open past the record and then let go, it kept the
    earlier time (measured on Windows, 2026-10-09).
    """
    unfinished_said = "was being written and did not finish"
    try:
        recorded = (folder / CALIBRATION_RECORD).stat().st_mtime_ns
        marked = (folder / INCOMPLETE).stat().st_mtime_ns
    except OSError:
        return unfinished_said
    if recorded < marked or archive_damage(folder) is not None:
        return unfinished_said
    return (f"its record and lines are whole, so only removing its "
            f"{INCOMPLETE} marker was cut short or refused -- delete that "
            "file")


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
            # scan.tif has shown that compaction got that far -- or where it
            # is what the record says compact was about to swap in.
            if _compacted_to(path, record, name):
                continue
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
        found.append(f"{part.name} {PARTIAL} -- {PART_REMEDY}")
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
    if _compacted_to(path, record, name) or (
            (path / RAW_PLAIN).exists() and _holds_the_decode(path, name)):
        return ("compaction stopped part way; `tools/library.py compact "
                "--write` finishes it")
    if ((path / MIGRATE_KEPT).exists()
            and MIGRATE_KEPT not in (record.get("files") or {})
            and _holds_the_decode(path, name)):
        return ("migrate-raw stopped part way; `tools/library.py migrate-raw "
                "--write` finishes it")
    return None


def _compacted_to(path: Path, record: dict[str, Any], name: str) -> bool:
    """Whether `name` is the TIFF `compact` noted it was about to swap in.

    The note is a checksum taken of the file written beside, so a match is
    that very file; an entry with no raw bytes has no other proof.
    """
    said = (record.get(COMPACTING) or {}).get(name)
    return bool(said) and (path / name).exists() and _sha256(path / name) == said


# -- the calibration behind an entry's reference -------------------------------
#
# `shading.npz` is a reduction -- `calculate_shading`'s split into dark and
# light and its averaging -- of the calibration's own lines, and a reduction
# cannot be redone with better code once its input is gone. Those lines are
# archived per calibration (`DirectScanner.archive_calibration`), and nothing
# read them back: the archive was kept for a recomputation no code could make.
#
# Since 2026-10-09 they are kept in the library, by id (`CALIBRATIONS`). An
# entry filed before names its archive by a path -- `calibration/<UTC time>`
# beside the cache, or a `--reference` directory's -- and by the same id
# where it was filed since 2026-09-28 (468733a); those are still found where
# they are until `migrate_calibrations` copies them in. One filed from
# 2026-09-24 to 28 names the path alone, and once that path is gone its
# lines are found by content, in the library first, by `calibration_of` and
# by `verify` alike. One corrected by a cached reference that named no
# calibration names nothing, and is found by content too.


def calibration_folder(root: Path | str, sha: str) -> Path:
    """Where the library at ``root`` keeps the calibration whose lines hash
    to ``sha``."""
    return Path(root) / CALIBRATIONS / sha


def _is_sha(value: Any) -> TypeGuard[str]:
    """Whether ``value`` can be an archive's id: a SHA-256 in hex, and so
    never a path -- a record is data, and its id is joined to a folder."""
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def archive_of(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """The calibration an entry's record names: ``(id, path)``, either None.

    The id is ``archive_sha256``, the SHA-256 of the calibration's lines and
    the name of its folder in the library; the path is where it was written
    when the entry was filed. An id that is not one is no id at all.
    """
    origin = (record.get("extra") or {}).get("shading_origin") or {}
    if not isinstance(origin, dict):
        return None, None
    sha, named = origin.get("archive_sha256"), origin.get("archive")
    return (sha if _is_sha(sha) else None), (str(named) if named else None)


def archive_named(named: str) -> Path:
    """The archive a record names, as a path on this machine.

    The driver records it with `str()`, so an entry filed on Windows says
    ``calibration\\20260927T...``. Read on macOS or Linux that is one file
    name with a backslash in it, and a library carried across found every
    such archive missing. The folders are `calibration/<UTC time>`, a
    `--reference` directory's, or ``<library>/calibrations/<id>``, never a
    name holding a backslash, so a backslash is always Windows' separator
    here.
    """
    return Path(PureWindowsPath(named).as_posix()) if "\\" in named else Path(named)


def _named_outside(root: Path, named: str) -> Path | None:
    """The archive a record names by path, where it still is: as written,
    or beside the library -- the path was recorded relative to wherever the
    scan ran from. None when neither holds one."""
    where = archive_named(named)
    return next((c for c in (where, root.parent / where)
                 if (c / CALIBRATION_RECORD).exists()), None)


def archive_damage(folder: Path | str) -> str | None:
    """What is wrong with one archived calibration, or None.

    Its record and lines are there and agree (`read_calibration`), the
    files the record names are there, and -- for one kept in the library --
    the lines are the ones its folder is named for: a folder named for one
    calibration and holding another's would hand every entry that names it
    the wrong lines.
    """
    folder = Path(folder)
    try:
        data, record = read_calibration(folder)
    except (OSError, ValueError) as exc:
        return str(exc)
    if folder.parent.name == CALIBRATIONS and _is_sha(folder.name) and (
            hashlib.sha256(data).hexdigest() != folder.name):
        return "data.bin is not the calibration this folder is named for"
    for key in ("reference", "ccd_mask"):
        name = record.get(key)
        if name and not (folder / str(name)).exists():
            return f"{name} is missing"
    return None


def _intact(folder: Path) -> bool:
    """Whether a library archive is whole: finished, and its own lines."""
    return (folder.is_dir() and not (folder / INCOMPLETE).exists()
            and (folder / CALIBRATION_RECORD).exists()
            and archive_damage(folder) is None)


def calibration_slot(root: Path | str, sha: str
                     ) -> tuple[Path, locks.FileHold] | None:
    """Where to keep the calibration whose lines hash to ``sha``, with the
    lock on its marker; None when the library at ``root`` already holds
    them whole.

    Once per calibration, however often its lines come to be kept: the same
    lines archived again, or an old archive copied in twice, write nothing.
    The folder comes back made and marked ``INCOMPLETE``, as an entry is
    while it is written, and the marker held, as an entry's is
    (`_mark_unfinished`): the writer puts its files in, `calibration.json`
    last through `_write_atomic`, lets go of the marker and removes it last
    -- and lets go of it however it ends (`locks.letting_go`), so a folder a
    failure left marked is `verify`'s to report, never "being written". A
    folder a kill or a full disk cut short says so, and is written again,
    whole, the next time these lines are kept.
    """
    if not _is_sha(sha):
        raise ValueError(f"{sha!r} is not a calibration's id")
    folder = calibration_folder(root, sha)
    if _intact(folder):
        return None
    # The parents first and on their own. Asked of the folder itself,
    # `mkdir(parents=True)` on Windows answers FileExistsError when a
    # *parent* is a file -- for a folder that does not exist. Here a parent
    # in the way raises, once. The library's own folder is made at most,
    # never the ones above it (`storage.make_within`).
    storage.make_within(root, folder.parent)
    folder.mkdir(exist_ok=True)
    marker = _write_marker(folder / INCOMPLETE, json.dumps({
        "incomplete": "this calibration was being written and did not finish",
        "id": sha, **_writer()}, indent=2) + "\n")
    return folder, marker


def calibrations_outside(root: Path | str,
                         sources: tuple[Path | str, ...] = ()) -> list[Path]:
    """Every archived calibration a library could take in, oldest first.

    Those under ``sources`` -- `calibration/` here and beside the library by
    default -- and every one an entry names by a path outside the library: a
    `--reference` directory's archive is in neither folder.
    """
    root = Path(root)
    found: dict[Path, Path] = {}
    for source in sources or (DEFAULT_CALIBRATIONS, root.parent / "calibration"):
        for folder in calibrations(source):
            found.setdefault(folder.resolve(), folder)
    for record in entries(root):
        _sha, named = archive_of(record)
        where = _named_outside(root, named) if named else None
        if where is not None and where.resolve().parent != (
                root / CALIBRATIONS).resolve():
            found.setdefault(where.resolve(), where)
    return sorted(found.values(), key=_oldest_first)


@locks.letting_go
def migrate_calibrations(root: Path | str,
                         sources: tuple[Path | str, ...] = (),
                         write: bool = False) -> list[tuple[Path, Path | None, str]]:
    """Copy the calibrations kept outside the library into it, by id.

    Every one `calibrations_outside` finds goes to
    ``<root>/calibrations/<sha256 of data.bin>/``: every file of its folder,
    `calibration.json` last. **Copied, never moved**, and nothing outside the
    library is changed or deleted -- the originals stay where every entry
    filed before this recorded them. A dry run unless ``write``.

    Returns one ``(source, folder in the library or None, what)`` for each,
    ``what`` being one of "copied", "would copy", "already in the library",
    "the same calibration as <an earlier source>", or "damaged: <why>, not
    copied" -- damaged lines copied in would be filed as sound under an id
    they no longer hash to.
    """
    root = Path(root)
    done: list[tuple[Path, Path | None, str]] = []
    taken: dict[str, Path] = {}
    for source in calibrations_outside(root, sources):
        try:
            data, _record = read_calibration(source)
        except (OSError, ValueError) as exc:
            done.append((source, None, f"damaged: {exc}, not copied"))
            continue
        sha = hashlib.sha256(data).hexdigest()
        target = calibration_folder(root, sha)
        if sha in taken:
            done.append((source, target, f"the same calibration as "
                                         f"{taken[sha]}"))
            continue
        taken[sha] = source
        if _intact(target):
            done.append((source, target, "already in the library"))
            continue
        if not write:
            done.append((source, target, "would copy"))
            continue
        slot = calibration_slot(root, sha)
        what = "copied"
        if slot is not None:
            folder, marker = slot
            # Each copy on the disk before the record that vouches for it,
            # as `archive_calibration` writes them: the record is what makes
            # the folder whole, and a power cut after it could otherwise
            # leave it naming lines that never arrived.
            for f in sorted(source.iterdir()):
                if (f.is_file() and not f.name.startswith(".")
                        and f.name not in (INCOMPLETE, CALIBRATION_RECORD)):
                    shutil.copyfile(f, folder / f.name)
                    _sync(folder / f.name)
            # The record last, and whole or not at all, as it is written.
            temp = folder / f".{CALIBRATION_RECORD}.part"
            try:
                shutil.copyfile(source / CALIBRATION_RECORD, temp)
                _sync(temp)
                _replace(temp, folder / CALIBRATION_RECORD)
            finally:
                _discard(temp)
            marker.release()
            # A marker that will not go costs the marker: the copy is whole,
            # and `verify` names what is left.
            try:
                _unmark(folder)
            except OSError as exc:
                what = (f"copied, but its {INCOMPLETE} marker could not be "
                        f"removed ({exc}); verify names it")
        done.append((source, target, what))
    return done


def calibrations(root: Path | str = DEFAULT_CALIBRATIONS) -> list[Path]:
    """Every archived calibration under `root`, oldest first.

    By `calibration_time`, not by name: named by its UTC time a folder
    sorted by its name, and named by its lines one sorts by hash.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted((p.parent for p in root.glob(f"*/{CALIBRATION_RECORD}")),
                  key=_oldest_first)


def calibration_time(folder: Path | str) -> str | None:
    """When an archived calibration was kept, as UTC text, or None.

    Its record's ``archived_utc``; for one kept before that was recorded,
    the UTC time its folder was named by under `calibration/`, or -- copied
    into the library, where the name is its id -- its ``measured_utc``.
    That one is the time of the reference in force for a calibration that
    failed, which may be another day's, so it comes last.
    """
    folder = Path(folder)
    try:
        record = json.loads((folder / CALIBRATION_RECORD).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if isinstance(record, dict) and record.get("archived_utc"):
        return str(record["archived_utc"])
    stamp = folder.name.split("-")[0]
    if (len(stamp) == 16 and stamp[8] == "T" and stamp[-1] == "Z"
            and (stamp[:8] + stamp[9:15]).isdigit()):
        return (f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:"
                f"{stamp[11:13]}:{stamp[13:15]}Z")
    if isinstance(record, dict) and record.get("measured_utc"):
        return str(record["measured_utc"])
    return None


def _oldest_first(folder: Path) -> tuple[str, str]:
    """`calibrations`' order: by `calibration_time`, its digits only so
    the two spellings of a time agree, and by name where a time is the
    same or unknown -- those last."""
    when = calibration_time(folder)
    digits = "".join(c for c in when if c.isdigit()) if when else "~"
    return digits, folder.name


def read_calibration(folder: Path | str) -> tuple[bytes, dict[str, Any]]:
    """One archived calibration's bytes, exactly as read, and its record.

    Raises OSError when a file is missing and ValueError when the bytes do not
    match the checksum taken as they arrived: a reduction of damaged lines
    would be a wrong reference presented as a better one.
    """
    folder = Path(folder)
    record = json.loads((folder / CALIBRATION_RECORD).read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{folder.name}: {CALIBRATION_RECORD} is not a record")
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

    By the id the record names (`extra.shading_origin.archive_sha256`), in
    the entry's own library first -- which is where every calibration has
    been kept since 2026-10-09, and where `migrate_calibrations` copies the
    older ones. Then by the path it names (`extra.shading_origin.archive`),
    found as written or beside the entry's library: the path was recorded
    relative to wherever the scan ran from. A reference *loaded* from a
    cache that named no calibration names none either; those are matched by
    content instead, against every archive in the library and under
    ``search`` (by default `calibration/` here and beside the library),
    since the cache and the archive are the same reference written twice.
    None when nothing matches.
    """
    path = Path(path)
    if record is None:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    sha, named = archive_of(record)
    if sha:
        inside = calibration_folder(path.parent, sha)
        if (inside / CALIBRATION_RECORD).exists():
            return inside
    if named:
        found = _named_outside(path.parent, named)
        if found is not None:
            return found
    mine = _entry_reference(path, record)
    if mine is None:
        return None
    roots = (path.parent / CALIBRATIONS,
             *(search or (DEFAULT_CALIBRATIONS,
                          path.parent.parent / "calibration")))
    return _kept_as(mine, [f for root in roots for f in calibrations(root)], {})


def _entry_reference(path: Path, record: dict[str, Any]
                     ) -> ShadingReference | None:
    """The reference filed with an entry, or None where it has none that
    loads -- which `verify` reports on its own account."""
    ref_file = (record.get("calibration") or {}).get("shading")
    if not ref_file or not (path / ref_file).exists():
        return None
    try:
        return ShadingReference.load(path / ref_file)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None


def _kept_as(mine: ShadingReference, folders: list[Path],
             kept: dict[Path, ShadingReference | None]) -> Path | None:
    """The first of ``folders`` whose kept reference is ``mine``, or None.

    The cache and an archive are the same reference written twice, so an
    entry's `shading.npz` finds the calibration it came from where nothing
    names it. Each archive's is read once into ``kept``, which a caller
    matching many entries passes again: `verify` would otherwise read every
    archive once an entry.
    """
    for folder in folders:
        key = folder.resolve()
        if key not in kept:
            try:
                kept[key] = (ShadingReference.load(folder / "shading.npz")
                             if (folder / "shading.npz").exists() else None)
            except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
                kept[key] = None
        theirs = kept[key]
        if theirs is not None and same_reference(mine, theirs):
            return folder
    return None
