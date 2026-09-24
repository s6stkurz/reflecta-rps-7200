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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from . import tiff
from .direct import SHADING_SKIPPED_EXPLICIT, DirectScanner, ScanParameters
from .protocol import ScanReadError
from .shading import ShadingReference, apply_shading

DEFAULT_ROOT = Path("library")
INDEX = "index.json"


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


_AT_IMPORT: dict[str, Any] = _identity_now()


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
) -> Path:
    """Write one scan and everything needed to use it again. Returns its path.

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
    """
    film = film or FilmNotes()
    when = datetime.now(timezone.utc)
    root = Path(root)
    path = _reserve(root, entry_id(meta, film, when))
    # Present until the record is in place, so an entry a crash or a full
    # disk cut short says so rather than passing for a directory of files.
    # `verify` reports it; `entries()` never sees it (no `scan.json`).
    (path / INCOMPLETE).write_text(
        "this entry was being written and did not finish\n", encoding="utf-8")

    resolution = int(meta.get("resolution_dpi") or 0) or None
    tiff.write(str(path / "scan.tif"), image, resolution=resolution)
    if prescan is not None:
        tiff.write(str(path / "prescan.tif"), prescan)
    if reference is not None:
        reference.save(path / "shading.npz")
    if ccd_mask is not None:
        (path / "ccd_mask.bin").write_bytes(bytes(ccd_mask))
    raw_bytes = raw_sha = None
    if raw is not None or raw_path is not None:
        # Compressed, but byte-exact: measured on a real pass, gzip takes it to
        # 72% of its size, and the decompressed bytes are identical to what
        # arrived.
        digest = hashlib.sha256()
        with gzip.open(path / "raw.bin.gz", "wb", compresslevel=6) as fh:
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
            "file": "raw.bin.gz" if raw_bytes is not None else None,
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
        # gitignored and rewritten per frame. `tools/registration_margin.py`
        # reads them back. Absent on a scan that never looked.
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
    # so a reader never meets half of one.
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=str))
    (path / INCOMPLETE).unlink(missing_ok=True)
    reindex(root)
    return path


#: A tag saying an entry was taken without a shading reference on purpose --
#: the byte-14 ladder, say, which is evidence and must never be pruned. `verify`
#: does not report such an entry for lacking one. `tools/library.py tag` sets it.
ON_PURPOSE = "uncalibrated-on-purpose"

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
            path = root / f"{name}-{n}"
            n += 1


def _write_atomic(path: Path, text: str) -> None:
    """Write beside, then rename over: the old file or the new, never half."""
    temp = path.with_name(f".{path.name}.part")
    with open(temp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def add_tags(path: Path | str, tags: list[str]) -> list[str]:
    """Add tags to one entry's record, in place and atomically. Returns them all."""
    path = Path(path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["tags"] = sorted(set(record.get("tags") or ()) | set(tags))
    _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=str))
    return record["tags"]


def _replace_tiff(path: Path, image: np.ndarray, **kw: Any) -> None:
    """Rewrite a stored TIFF beside, then swap it in: never a half-written one.

    An interrupted in-place rewrite of a complete entry left a truncated file
    under its ordinary name, which only a checksum could tell from a good one.
    """
    temp = path.with_name(f".{path.name}.part")
    tiff.write(str(temp), image, **kw)
    os.replace(temp, path)


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
    at scan time.
    """
    path = Path(path)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    image = tiff.read(str(path / "scan.tif"))

    ref_file = (record.get("calibration") or {}).get("shading")
    record["reference"] = (
        ShadingReference.load(path / ref_file)
        if ref_file and (path / ref_file).exists() else None
    )
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

    The last two look identical in the record -- both are a non-empty
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
    image, record = load(path)
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
        record["corrected"] = "no reference"
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
    path = Path(path) / "raw.bin.gz"
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
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def reconstruct(path: Path | str) -> tuple[np.ndarray | None, str]:
    """Decode this entry's raw bytes with the *current* code.

    Returns ``(image, verdict)``. The verdict says whether today's decode still
    reproduces the pixels stored at scan time -- which is the whole reason the
    bytes are kept. A mismatch is not necessarily a regression: it is where a
    deliberate change to the decode shows up, on every scan in the library at
    once rather than on the next one taken.
    """
    path = Path(path)
    raw = read_raw(path)
    if raw is None:
        return None, "no raw bytes stored for this entry"

    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read scan.json: {exc}"
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
    # this entry, not a reason to stop checking every entry after it.
    except (KeyError, ValueError, TypeError, ScanReadError) as exc:
        return None, f"could not decode: {exc}"

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
        if not ref_file or not (path / ref_file).exists():
            return image, (
                "stored image is shading-corrected but its reference is "
                "missing, so it cannot be reproduced"
            )
        reference = ShadingReference.load(path / ref_file)
        mask = ((path / mask_file).read_bytes()
                if mask_file and (path / mask_file).exists() else None)
        image, _ = apply_shading(image, reference, mask)

    try:
        stored = tiff.read(str(path / "scan.tif"))
    except (OSError, ValueError) as exc:
        return image, f"could not read scan.tif: {exc}"
    # The 7200 dpi realignment is part of the path from bytes to `scan.tif`,
    # so it is replayed here, not reported as a changed decode.
    image = _replay(image, record, stored.shape[0])
    if image.shape != stored.shape:
        return image, (
            f"decode CHANGED: now {image.shape}, stored {stored.shape}"
        )
    recorded = ((record.get("scan") or {}).get("read_direction") or {}).get("direction")
    if recorded is not None and recorded != direction.state:
        return image, (
            f"read direction CHANGED: recorded {recorded}, the line tags now "
            f"say {direction.state} ({direction.why})")
    if np.array_equal(image, stored):
        return image, "identical to the stored image"
    if direction.reversed and np.array_equal(image[::-1], stored):
        # Filed before passes were turned upright in the decode: the stored
        # image is the pass in the order it was read. Not a regression, and
        # `tools/library.py migrate-direction` is what brings it up to date.
        return image, ("stored as it was read, bottom-up; today's decode "
                       "turns it upright -- see migrate-direction")
    differing = int(np.count_nonzero(image != stored))
    return image, (
        f"decode CHANGED: {differing} of {image.size} samples differ "
        f"({100 * differing / image.size:.3f}%)"
    )


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
        _write_atomic(path / "scan.json", json.dumps(record, indent=2, default=str))
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
    re-decoded and re-corrected, and one without cannot. Ties go to the newest.
    """
    def usefulness(record: dict[str, Any]) -> tuple:
        raw = bool((record.get("raw") or {}).get("file"))
        cal = bool((record.get("calibration") or {}).get("shading"))
        return (raw, cal, str(record.get("created")))

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
            twin = next((k for k in kept if same_data(record, k)), None)
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
    index.write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
                json.loads((folder / "scan.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{folder.name}: scan.json cannot be read ({exc})")
    for record in entries(root):
        path = entry_path(root, record)
        if str(record.get("id")) != path.name:
            problems.append(f"{path.name}: records itself as {record.get('id')}")
        image = record.get("image") or {}
        scan = path / str(image.get("file", "scan.tif"))
        if not scan.exists():
            problems.append(f"{path.name}: {scan.name} is missing")
            continue
        if image.get("sha256") and _sha256(scan) != image["sha256"]:
            problems.append(f"{path.name}: {scan.name} does not match its checksum")
        cal = record.get("calibration") or {}
        for key in ("shading", "ccd_mask"):
            name = cal.get(key)
            if name and not (path / name).exists():
                problems.append(f"{path.name}: {name} is missing")
        for name, digest in (record.get("files") or {}).items():
            if (path / name).exists() and _sha256(path / name) != digest:
                problems.append(f"{path.name}: {name} does not match its checksum")
        if not cal.get("shading"):
            # Say which kind this is. A scan deliberately taken raw and one that
            # wanted correction and silently went without look the same here
            # otherwise, and only the second is a thing that went wrong. The
            # first is not a problem at all: reported as one -- with the words
            # "correction was asked for", about the sentinel that says it was
            # not -- it kept `make verify` red for a week, and a check that is
            # always red is a check nobody reads.
            why = cal.get("skipped")
            if why != SHADING_SKIPPED_EXPLICIT and ON_PURPOSE not in (record.get("tags") or ()):
                problems.append(
                    f"{path.name}: no shading reference, so this scan can never "
                    f"be corrected"
                    + (f" -- correction was asked for: {why}" if why else "")
                )
        raw = record.get("raw") or {}
        if not raw.get("file"):
            problems.append(
                f"{path.name}: no raw bytes, so it cannot be re-decoded"
            )
        elif not (path / raw["file"]).exists():
            problems.append(f"{path.name}: {raw['file']} is missing")
        elif raw.get("sha256"):
            data = read_raw(path)
            if data is None or hashlib.sha256(data).hexdigest() != raw["sha256"]:
                problems.append(
                    f"{path.name}: raw bytes do not match their checksum"
                )
    return problems
