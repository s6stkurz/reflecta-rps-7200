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
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from . import tiff
from .direct import DirectScanner, ScanParameters
from .shading import ShadingReference

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
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=10,
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

    return {
        "driver_commit": git("rev-parse", "--short", "HEAD"),
        "driver_dirty": bool(git("status", "--porcelain")),
        "versions": versions,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
    }


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
    inquiry: Any = None,
    raw: bytes | None = None,
    raw_layout: dict[str, Any] | None = None,
) -> Path:
    """Write one scan and everything needed to use it again. Returns its path.

    ``image`` should be the **raw** pixels. Corrections belong downstream: they
    change, and a corrected file cannot be un-corrected. `meta["shading"]`
    records whether any were already applied, so a file that is not raw is at
    least labelled as such.
    """
    film = film or FilmNotes()
    when = datetime.now(timezone.utc)
    root = Path(root)
    path = root / entry_id(meta, film, when)
    # Two scans in the same second with the same film and settings would
    # otherwise land on one id and the second would overwrite the first.
    if (path / "scan.json").exists():
        base, n = path, 2
        while (path / "scan.json").exists():
            path = base.with_name(f"{base.name}-{n}")
            n += 1
    path.mkdir(parents=True, exist_ok=True)

    resolution = int(meta.get("resolution_dpi") or 0) or None
    tiff.write(str(path / "scan.tif"), image, resolution=resolution)
    if prescan is not None:
        tiff.write(str(path / "prescan.tif"), prescan)
    if reference is not None:
        reference.save(path / "shading.npz")
    if ccd_mask is not None:
        (path / "ccd_mask.bin").write_bytes(bytes(ccd_mask))
    if raw is not None:
        # Compressed, but byte-exact: gzip so a 26 MB pass does not cost 26 MB
        # twice, and the decompressed bytes are identical to what arrived.
        with gzip.open(path / "raw.bin.gz", "wb", compresslevel=6) as fh:
            fh.write(raw)

    record: dict[str, Any] = {
        "id": path.name,
        "created": when.isoformat(timespec="seconds"),
        "image": {
            "file": "scan.tif",
            "shape": list(image.shape),
            "dtype": str(image.dtype),
            "channels": meta.get("channel_order"),
            "corrections_applied": (
                ["shading"] if meta.get("shading") else []
            ),
            "sha256": _sha256(path / "scan.tif"),
        },
        "raw": {
            "file": "raw.bin.gz" if raw is not None else None,
            "bytes": len(raw) if raw is not None else None,
            "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "layout": raw_layout,
        },
        "scan": {
            k: meta.get(k)
            for k in (
                "resolution_dpi", "frame", "width", "height", "depth",
                "channels", "channel_order", "bytes_per_line", "film",
                "exposure_scale", "duration_s",
            )
        },
        "device_settings": {
            k: meta.get(k) for k in ("exposure", "gain", "offset")
        },
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
        },
        "film": asdict(film),
        "tags": sorted(set(tags or [])),
        "provenance": provenance(),
    }
    (path / "scan.json").write_text(json.dumps(record, indent=2, default=str))
    reindex(root)
    return path


def load(path: Path | str) -> tuple[np.ndarray, dict[str, Any]]:
    """Read a library entry back: ``(image, record)``.

    ``record["reference"]`` and ``record["ccd_mask"]`` are filled in where the
    entry has them, so a correction can be re-run exactly as it would have been
    at scan time.
    """
    path = Path(path)
    record = json.loads((path / "scan.json").read_text())
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

    record = json.loads((path / "scan.json").read_text())
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
        image = DirectScanner._deinterleave(raw, params, int(layout["channels"]))
    except (KeyError, ValueError, TypeError) as exc:
        return None, f"could not decode: {exc}"

    stored = tiff.read(str(path / "scan.tif"))
    if image.shape != stored.shape:
        return image, (
            f"decode CHANGED: now {image.shape}, stored {stored.shape}"
        )
    if np.array_equal(image, stored):
        return image, "identical to the stored image"
    differing = int(np.count_nonzero(image != stored))
    return image, (
        f"decode CHANGED: {differing} of {image.size} samples differ "
        f"({100 * differing / image.size:.3f}%)"
    )


def entries(root: Path | str = DEFAULT_ROOT) -> list[dict[str, Any]]:
    """Every entry's record, oldest first. Unreadable entries are skipped."""
    root = Path(root)
    out = []
    for candidate in sorted(root.glob("*/scan.json")):
        try:
            out.append(json.loads(candidate.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
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
    index.write_text(json.dumps(summary, indent=2))
    return index


def verify(root: Path | str = DEFAULT_ROOT) -> list[str]:
    """Problems found in the library: missing files, checksum mismatches."""
    root = Path(root)
    problems = []
    for record in entries(root):
        path = root / str(record.get("id"))
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
        if not cal.get("shading"):
            problems.append(
                f"{path.name}: no shading reference, so this scan can never be "
                f"corrected"
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
