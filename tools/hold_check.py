#!/usr/bin/env python3
"""Where each held frame of a roll was asked to sit, and where it was scanned.

    uv run python tools/hold_check.py rolls/2026-10-05-222837 --out DIR
    uv run python tools/hold_check.py ROLL --library LIB --positions approved.json --out DIR

Offline: it reads a roll folder and the library, and never opens the scanner.
For every scanned frame of the roll with a hold record -- the one the driver
files as the frame's `registration.approved` -- it lays the frame that was
delivered beside the picture its position was set on, and writes one PNG a
person can judge at a glance:

  (a) as set on the sheet: the walk's prescan the position was set on,
      moved by the target the way the frame-position window draws it -- the
      picture moves, the guides stay where the aperture is
  (b) as delivered: the scanned frame, brought onto the prescan's grid, at
      the same scale and against the same guides
  (c) both edges magnified, (a) and (b) in alternating bands: a frame
      delivered where it was set reads as one picture down the whole height,
      and one delivered elsewhere steps sideways at every band
  (d) the numbers, in units of the SLIDE param: the target, where the frame
      arrived, the hold's last look, where the delivered frame measures
      against the same picture, and the difference

and `summary.png`, `summary.json` and `summary.md` beside them. Negatives
are inverted for viewing and black and white is grey, each picture stretched
on its own; nothing here is a delivered file.

**Delivered is measured, not taken from the record.** The hold's last look is
a prescan taken before the scan; the scan is the thing that was delivered, so
it is measured again here, against the same reference, by the same
`framing.measure_shift_mm` the hold uses, after being box-averaged onto the
prescan's grid. It is also measured against the frame's own last prescan,
which says whether the scan landed where that look saw the film.

The reference is the hold record's own `reference_entry`; failing that the
one `--positions` (or the roll's own `approved.json`) names for the frame;
failing that the walk's prescan of the frame, from the walk the roll was held
from. Each frame says which it used. All of them are corrected with today's
code (`library.corrected`), as every view of a library entry is.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from rps7200 import library, preview, tiff  # noqa: E402
from rps7200.console import use_utf8_stdout  # noqa: E402
from rps7200.framing import (  # noqa: E402
    APERTURE_MM,
    measure_shift_mm,
    say_hold,
    scan_window,
)
from rps7200.protocol import (  # noqa: E402
    FILM_BW,
    FILM_KODACHROME,
    FILM_NEGATIVE,
    FILM_POSITIVE,
    MM_PER_UNIT,
    units,
)
from rps7200.session import (  # noqa: E402
    approved_shift,
    manifest_settings,
    prescan_arrangement,
    read_approved,
    read_manifest,
    renumbered,
    walk_shift,
    walked_prescan_entries,
    walked_prescans,
)
from rps7200.uniformity import luminance, register  # noqa: E402

#: The resolution a prescan is taken at unless something says otherwise --
#: the one the frame-edge reader reads, and every roll's here.
PRESCAN_DPI = 300

#: Screen pixels to a prescan column for (a) and (b): the frame-position
#: window shows the frame at about this.
SCALE = 2
#: The edge zooms: how magnified, how many prescan columns each side's spans,
#: and how many prescan rows each band is before the other picture takes over.
ZOOM = 6
ZOOM_COLUMNS = 60
BAND_ROWS = 16

#: The window's canvas and its guides' amber (`gui._FrameAdjuster.GUIDE`).
BACKGROUND = (30, 30, 30)
GUIDE = (232, 182, 76)
#: The two greys of the stripes drawn where a panel has no picture.
HATCH = ((44, 44, 52), (66, 66, 76))
#: Which picture a band of a zoom is, in the strip beside it.
SHEET_MARK = GUIDE
DELIVERED_MARK = (96, 168, 232)
TEXT = (225, 225, 225)
MUTED = (150, 150, 150)
PAD = 16


# --- where things are ------------------------------------------------------


def find_entry(named, library_root: Path) -> Path | None:
    """The library entry a record names, wherever that record was written.

    Records name entries as the session had them: ``library\\<id>`` relative
    to wherever the window ran, an absolute path, or the bare id a walk's
    record keeps -- with backslashes on Windows, which a POSIX `Path` reads
    as one name. By its id under ``library_root`` first, the library this
    was asked to read; as written only where that has none.
    """
    if not named:
        return None
    text = str(named)
    for candidate in (library_root / PureWindowsPath(text).name, Path(text)):
        if (candidate / "scan.json").is_file():
            return candidate
    return None


def frame_index(library_root: Path) -> dict:
    """Every frame entry in the library by the roll it was filed for.

    For a roll whose `roll.json` names no entry for a frame: filed before
    rolls recorded them. The window's join (`roll_entry_index`): on the
    folder the entry's `roll_membership` records, then on the roll's name
    where it records none, or the ``<roll>-<NN>`` label an entry filed
    before memberships carries (`session.roll_frame_label`). The newest of a
    number wins; only a scanned frame is a frame, never a walk's prescan of
    the same number. One difference: the folder is matched by its name,
    where the window resolves the path -- which was relative to wherever the
    session ran, and this need not run there.
    """
    out: dict = {"folders": {}, "names": {}}
    if not library_root.is_dir():
        return out
    for record_path in sorted(library_root.glob("*/scan.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        entry = record_path.parent
        member = (record.get("extra") or {}).get("roll_membership")
        if isinstance(member, dict):
            number = member.get("number")
            if member.get("kind") != "frame" or not isinstance(number, int):
                continue
            # By its name only where it records no folder: a duplicate, a
            # renamed folder and a name typed again after a Delete all keep
            # a roll's name, and joined on it one roll's frames were taken
            # for another's (the window's reason, `roll_entry_index`).
            if member.get("folder"):
                key = PureWindowsPath(str(member["folder"])).name.casefold()
                out["folders"].setdefault(key, {})[number] = entry
            elif member.get("roll"):
                out["names"].setdefault(str(member["roll"]), {})[number] = entry
            continue
        if "prescan" in (record.get("tags") or ()):
            continue
        label = str((record.get("film") or {}).get("frame") or "").strip()
        roll, _, number = label.rpartition("/" if "/" in label else "-")
        if roll and number.isdigit():
            out["names"].setdefault(roll, {})[int(number)] = entry
    return out


def walk_references(folder: Path, progress: dict, library_root: Path) -> dict:
    """The walk prescans this roll's frames were held against, by frame.

    For a hold record that names no reference: one from before records named
    it, or one `tools/scan_roll.py --approved` made before its references
    carried their entry. The walk is the one the roll's own record says it
    was held from (``held.from``), or the roll folder's own `survey.json`.
    Each frame's library entry where the walk's records find one
    (`session.walked_prescan_entries`, the window's join), and the walk's
    `prescanNN.tif` itself otherwise, with the pair it was turned by.
    """
    walk = folder
    named = (progress.get("held") or {}).get("from")
    if named:
        for candidate in (Path(str(named)),
                          folder.parent / PureWindowsPath(str(named)).name):
            if (candidate / "survey.json").is_file():
                walk = candidate
                break
    try:
        manifest = read_manifest(walk / "survey.json")
    except ValueError:
        return {}
    if not manifest:
        return {}
    walked = walked_prescans(walk, manifest)
    entries = walked_prescan_entries(
        walk, [(n, r) for n, _p, r in walked], library_root, walk=manifest,
        roll=progress if walk == folder else None)
    return {n: {"entry": entries.get(n), "file": path, "walk": walk,
                "arrangement": prescan_arrangement(manifest, record)}
            for n, path, record in walked}


def _number(value) -> int | None:
    """A resolution as a record or a hand-edited manifest gives it, or None."""
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def _dpi_of(record: dict | None) -> int | None:
    return _number(((record or {}).get("scan") or {}).get("resolution_dpi"))


# --- bringing a scan onto a prescan's grid -----------------------------------


def onto_grid(image: np.ndarray, factor: float,
              shape: tuple[int, int]) -> np.ndarray:
    """``image`` box-averaged ``factor`` times smaller onto a pass of ``shape``.

    The visible planes only, as `uniformity.luminance` reads them: an RGBI
    frame's fourth plane holds the dust, and a prescan has nothing to match
    it against. One plane comes back 2-D, three come back as three.

    Column ``j`` of the result averages the scan's columns ``[j*f, (j+1)*f)``
    -- the two passes start at the same left edge of the window. Seen as
    well as assumed: against its own last prescan, a 3600 dpi scan's left
    third sits within 0.1 column of it whether ``f`` is 12 or up to 0.7%
    more (`measure` says what its right part does). The device's widths
    agree: the window's 10344 columns at 7200 dpi divided down and cut to a
    multiple of four, 428 at 300 dpi against 10344/24 = 431, which is a
    pass trimmed at its right. The scan's columns past the prescan's are
    dropped, and a last row or column it does not fill is the one before it
    again.
    """
    from PIL import Image

    box = getattr(Image, "Resampling", Image).BOX
    planes = (image[..., None] if image.ndim == 2
              else image[..., :min(3, image.shape[2])])
    rows, columns = shape
    f = float(factor)
    tall = max(1, min(rows, int(planes.shape[0] // f)))
    wide = max(1, min(columns, int(planes.shape[1] // f)))
    out = np.empty((rows, columns, planes.shape[2]), np.float64)
    for c in range(planes.shape[2]):
        plane = Image.fromarray(np.ascontiguousarray(planes[..., c],
                                                     dtype=np.float32))
        small = np.asarray(plane.resize((wide, tall), box,
                                        box=(0, 0, wide * f, tall * f)),
                           dtype=np.float64)
        out[..., c] = np.pad(small, ((0, rows - tall), (0, columns - wide)),
                             mode="edge")
    return out[..., 0] if planes.shape[2] == 1 else out


# --- measuring -------------------------------------------------------------

#: How far either side of a whole-column match its fraction is looked for,
#: and how finely, in columns of the prescan.
FINE_REACH = 1.0
FINE_STEP = 0.01

#: Where else a match is searched from, in columns, when neither zero nor
#: the hold's own reading found one `measure_shift_mm` believes: each is
#: searched 105 columns either way, so together they reach past the largest
#: move twice over.
SEARCH_STARTS = (-200, -100, 100, 200)


def _fine_px(reference: np.ndarray, now: np.ndarray, dy: int, dx: int) -> float:
    """A match's sideways shift to a fraction of a column.

    `measure_shift_mm` answers in whole columns of the prescan, 0.81 units
    each -- a quarter of the smallest move, and coarse beside a hold judged
    to within one. This reads the correlation surface `uniformity.register`
    searches, built the same way from the same two pictures, *between* its
    columns: the surface is the inverse transform of the normalised
    cross-power spectrum, so it can be evaluated at any fraction of a column
    straight from the spectrum, and its peak is looked for within
    `FINE_REACH` of the whole column `register` found (``dy``, ``dx``, with
    ``now`` already turned the way it matched). Only the fraction is new;
    whether the match is believed stays `measure_shift_mm`'s to say.

    A parabola through three of its columns was tried first. It pulls a
    fraction toward the nearest whole column: 0.08 to 0.17 column out on a
    synthetic picture moved by quarters of one, where this keeps to 0.04 to
    0.08 -- and strips of a real frame read through it looked like a step
    that was not there.

    In the sense of `measure_shift_mm`'s ``px``: positive where the picture
    sits further along +x than in ``reference``.
    """
    a, b = luminance(reference), luminance(now)
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w] - a[:h, :w].mean(), b[:h, :w] - b[:h, :w].mean()
    window = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    cross = np.fft.fft2(a * window) * np.conj(np.fft.fft2(b * window))
    size = np.abs(cross)
    spectrum = cross / np.where(size > 0, size, 1.0)
    # The surface's row ``dy``, as a spectrum along x; then that row at
    # every fraction asked for. Signed frequencies, so between columns it is
    # the band-limited surface rather than an alias of it.
    row = (spectrum * np.exp(2j * np.pi * np.fft.fftfreq(h) * dy)[:, None]
           ).sum(axis=0)
    xs = dx + np.arange(-FINE_REACH, FINE_REACH + FINE_STEP / 2, FINE_STEP)
    surface = np.real(np.exp(2j * np.pi * np.outer(xs, np.fft.fftfreq(w)))
                      @ row)
    return -float(xs[int(np.argmax(surface))])


def _local_px(reference: np.ndarray, now: np.ndarray, lo: int, hi: int,
              px: int) -> float | None:
    """Where columns ``lo`` to ``hi`` of ``reference`` sit in ``now``.

    Given the whole picture's match, ``px`` whole columns: the two windows
    are cut that far apart, so what is left to find is how this part
    differs from the whole, well inside any reach. None where the overlap
    is too narrow to say anything.
    """
    width = min(reference.shape[1], now.shape[1])
    start, end = max(lo, -px, 0), min(hi, width - px, width)
    if end - start < 32:
        return None
    a = np.ascontiguousarray(reference[:, start:end])
    b = np.ascontiguousarray(now[:, start + px:end + px])
    dy, dx, _confidence = register(a, b, max_shift=4)
    return px + _fine_px(a, b, dy, dx)


def measure(reference: np.ndarray, now: np.ndarray, about: int = 0) -> dict:
    """Where ``now`` sits against ``reference``, as the hold would read it.

    ``units`` is in units of the SLIDE param, on the hold's own scale: a
    column is `APERTURE_MM / width`, as `measure_shift_mm` converts it, so
    a delivered position compares like for like with the record's target
    and last look. (`framing.units_per_column` is the truer pitch, 0.7%
    finer; on a 30-unit move that is 0.2 units, the same for all three.)

    ``about`` is where the picture is expected, in whole columns -- the
    hold's own last look, say. `measure_shift_mm` searches 105 columns
    either way and no further, so a frame held near the largest move, 88.8
    units or 110 columns, has its true match outside that and comes back
    with a wrong one at a confidence of 5 to 8 (2026-10-03, frames 19-21).
    Given ``about``, the two pictures are also cut so that what should
    coincide is compared, searched the same 105 columns either way from
    there, and the more confident of the two readings is kept. Where
    neither is believed it is searched from `SEARCH_STARTS` as well: a frame
    left where it arrived carries whatever earlier moves left behind, and
    its record says nothing of where (2026-10-03 frame 22: nothing within
    reach of zero, and a weak match 108 columns out from anywhere near it).

    ``halves`` is the same for the left and the right half of the picture
    on their own. A scan is not always one shift against a prescan. Run over
    the 34 rolls of 2026-10-03 to 05, a 3600 dpi scan against its own last
    prescan, both read forward (179 frames), sits +0.23 units over on the
    left half and +0.56 on the right, medians, the change at a different
    place on every frame; after a prescan read in reverse (12) both halves
    within 0.06 of nothing; a scan read in reverse (8) +0.8 on both. At 600
    dpi it is one shift. The whole picture's number is whatever mix of the
    halves its detail makes, and they say how far apart they were.

    ``believed`` is `measure_shift_mm`'s verdict. A match it refused -- too
    weak, or too far off the film axis -- still carries the shift it found,
    because the picture beside it may say more than the score did; it is
    marked, and never counted as measured.
    """
    best = _measured(reference, now, 0)

    def keep(start: int) -> None:
        nonlocal best
        found = _measured(reference, now, start)
        if (found.get("confidence") or 0) > (best.get("confidence") or 0):
            best = found

    if about:
        keep(int(about))
    if not best["believed"]:
        for start in SEARCH_STARTS:
            keep(start)
    return best


def _measured(reference: np.ndarray, now: np.ndarray, about: int) -> dict:
    """`measure`, searching from ``about`` columns: both pictures cut to the
    columns that coincide there, matched as `measure_shift_mm` matches, and
    the answer put back on the whole picture's columns."""
    width = max(int(now.shape[1]), 1)
    cut = min(abs(int(about)), width - 64)
    a = reference[:, :width - cut] if about > 0 else reference[:, cut:]
    b = now[:, cut:] if about > 0 else now[:, :width - cut]
    start = cut if about > 0 else -cut
    # The aperture these columns are of, so the search reaches as many of
    # them as it does of a whole pass and the confidence means what it does
    # there.
    mm, detail = measure_shift_mm(
        np.ascontiguousarray(a), np.ascontiguousarray(b),
        aperture_mm=APERTURE_MM * (width - cut) / width)
    out: dict = {"believed": mm is not None, "reason": detail.get("reason"),
                 "confidence": detail.get("confidence"),
                 "dy": detail.get("dy"),
                 "row_reversed": bool(detail.get("row_reversed")),
                 "px": None, "px_fine": None, "units": None, "halves": None,
                 "searched_from": start}
    if detail.get("px") is None:
        return out
    out["px"] = int(detail["px"]) + start

    def in_units(px: float | None) -> float | None:
        return None if px is None else round(units(px * APERTURE_MM / width), 2)

    if detail.get("resampled"):
        # Matched against a reference resampled onto this one's grid, which
        # the surface here would not be: the whole column only.
        out["px_fine"] = float(out["px"])
        out["units"] = in_units(out["px_fine"])
        return out
    turned = b[::-1] if detail.get("row_reversed") else b
    fine = start + _fine_px(a, turned, int(detail["dy"]), int(detail["dx"]))
    out["px_fine"] = round(fine, 3)
    out["units"] = in_units(fine)
    whole = now[::-1] if detail.get("row_reversed") else now
    half = width // 2
    out["halves"] = [in_units(_local_px(reference, whole, lo, hi, out["px"]))
                     for lo, hi in ((0, half), (half, width))]
    return out


def arrived_units(hold: dict, width: int | None) -> float | None:
    """Where the first look found the frame, in units.

    The record says, since the hold began recording it (``arrived_units``);
    an older one is read off its first look, when that look was believed.
    """
    if hold.get("arrived_units") is not None:
        return float(hold["arrived_units"])
    first = (hold.get("history") or [None])[0]
    if not isinstance(first, dict) or first.get("px") is None:
        return None
    if first.get("reason") not in ("matched", "matched with its rows reversed"):
        return None
    shape = hold.get("reference_shape") or ()
    columns = shape[1] if len(shape) >= 2 else width
    if not columns:
        return None
    return round(units(float(first["px"]) * APERTURE_MM / float(columns)), 2)


def _expected_px(hold: dict, width: int) -> int:
    """Where the hold says the frame should be found, in whole columns of a
    pass this wide, for `measure`'s ``about``: its last look, which the scan
    follows; where it arrived, if no look after a move was believed; its
    target, if neither."""
    seen = hold.get("final_mm")
    try:
        if seen is None and hold.get("arrived_units") is not None:
            seen = float(hold["arrived_units"]) * MM_PER_UNIT
        if seen is None:
            seen = hold.get("target_mm")
        value = float(seen) / APERTURE_MM * width if seen is not None else 0.0
    except (TypeError, ValueError):
        return 0
    return int(round(value)) if math.isfinite(value) else 0


def _units_of(millimetres) -> float | None:
    try:
        value = float(millimetres)
    except (TypeError, ValueError):
        return None
    return round(units(value), 2) if math.isfinite(value) else None


# --- drawing ---------------------------------------------------------------


def guides(width: int) -> tuple[float, float]:
    """Where the scan cuts, in columns of a prescan this wide.

    Where the frame-position window draws its guides: the scan's own window
    (`framing.scan_window`), about 0.3 columns past the prescan's left edge
    and 2.5 past its right, because the 300 dpi prescan comes back 428 of the
    device window's columns. Every picture here follows this one call.
    """
    left, right = scan_window(width)
    return float(left), float(right)


def shown(image: np.ndarray, film: str) -> np.ndarray:
    """``image`` as ``(H, W, 3)`` uint8 for the eye.

    Stretched on its own, channel by channel (`preview.normalise`, the
    window's stretch), so a 16-bit scan and an 8-bit prescan come out alike.
    A negative is inverted, colour or black and white; black and white is
    the mean of its visible planes, in grey; a slide is shown as it is.
    """
    x = np.asarray(image, dtype=np.float64)
    if x.ndim == 3:
        x = x[..., :min(3, x.shape[2])]
        if film == FILM_BW or x.shape[2] < 3:
            x = x.mean(axis=2)
    x = preview.normalise(x)
    if film not in (FILM_POSITIVE, FILM_KODACHROME):
        x = 1.0 - x
    out = np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return np.repeat(out[..., None], 3, axis=2) if out.ndim == 2 else out


def _hatch(rows: int, columns: int) -> np.ndarray:
    """Where a panel has no picture: diagonal stripes, which no film looks
    like. A flat dark grey, the window's own canvas, could not be told from
    the clear base of an inverted negative beside it."""
    stripe = ((np.arange(rows)[:, None] + np.arange(columns)[None, :])
              // 5) % 2
    out = np.empty((rows, columns, 3), np.uint8)
    out[stripe == 0] = HATCH[0]
    out[stripe == 1] = HATCH[1]
    return out


def placed(view: np.ndarray, at: float, lo: float, hi: float,
           scale: int) -> np.ndarray:
    """Columns ``lo`` to ``hi`` of the aperture's frame, ``scale`` to a column.

    ``view`` lies ``at`` columns right of where it was taken -- a fraction of
    a column honoured to the nearest screen pixel -- as the frame-position
    window draws a picture moved against its fixed guides, with stripes
    where there is none. Columns are the prescan's, the aperture's left edge
    0.
    """
    rows, width = view.shape[:2]
    xs = (np.arange(max(1, int(round((hi - lo) * scale)))) + 0.5) / scale
    source = np.floor(xs + lo - at).astype(int)
    inside = (source >= 0) & (source < width)
    out = _hatch(rows * scale, xs.size)
    out[:, inside] = np.repeat(view[:, source[inside]], scale, axis=0)
    return out


def draw_guide(canvas: np.ndarray, x: float, dash: int = 8,
               gap: int = 6) -> None:
    """A dashed amber line down ``canvas`` at screen column ``x``, 2 px wide."""
    left = int(round(x)) - 1
    lo, hi = max(0, left), min(canvas.shape[1], left + 2)
    if hi <= lo:
        return
    for y in range(0, canvas.shape[0], dash + gap):
        canvas[y:y + dash, lo:hi] = GUIDE


def panel(view: np.ndarray, at: float, width: int, margin: float,
          scale: int) -> tuple[np.ndarray, list[int]]:
    """(a) or (b): the picture on the aperture's frame with both guides.

    Returns the pixels and the screen columns the guides were drawn at.
    """
    canvas = placed(view, at, -margin, width + margin, scale)
    xs = []
    for g in guides(width):
        x = (g + margin) * scale
        draw_guide(canvas, x)
        xs.append(int(round(x)))
    return canvas, xs


def edge_zoom(sheet: np.ndarray, sheet_at: float, delivered: np.ndarray,
              delivered_at: float, edge: float) -> np.ndarray:
    """(c) at one guide: both pictures, magnified, in alternating bands.

    ``ZOOM_COLUMNS`` prescan columns centred on the guide at ``edge``, the
    sheet's picture in the first band and every other one after it, the
    delivered frame in the rest, each drawn where its own panel draws it. A
    strip at the left says which band is which -- amber the sheet's, blue
    the delivered frame's -- and the guide runs down the middle.
    """
    half = ZOOM_COLUMNS / 2.0
    a = placed(sheet, sheet_at, edge - half, edge + half, ZOOM)
    b = placed(delivered, delivered_at, edge - half, edge + half, ZOOM)
    rows = min(a.shape[0], b.shape[0])
    out = a[:rows].copy()
    marks = np.empty((rows, 10, 3), np.uint8)
    band = BAND_ROWS * ZOOM
    for start in range(0, rows, band):
        theirs = (start // band) % 2 == 1
        if theirs:
            out[start:start + band] = b[start:start + band]
        marks[start:start + band] = DELIVERED_MARK if theirs else SHEET_MARK
    draw_guide(out, half * ZOOM)
    return np.concatenate([marks, np.full((rows, 4, 3), BACKGROUND, np.uint8),
                           out], axis=1)


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:                     # Pillow before 10.1: one size only
        return ImageFont.load_default()


def _said(value, *, unit: str = " u") -> str:
    """A signed distance in units, as every number here is said."""
    return "--" if value is None else f"{value:+.1f}{unit}"


def _halves(m: dict) -> str:
    """A measurement's two halves, ``left / right``, or "" without them."""
    halves = m.get("halves") or ()
    if len(halves) != 2 or all(h is None for h in halves):
        return ""
    return " / ".join(_said(h, unit="") for h in halves)


def frame_lines(row: dict) -> list[tuple[str, str, str]]:
    """(d): the numbers, as ``(name, value, what it is)`` rows."""
    delivered = row["delivered"]
    against = row["against_prescan"]

    def measured(m: dict) -> str:
        if m.get("units") is None:
            return "--"
        text = _said(m["units"])
        return text if m.get("believed") else f"({text})"

    def halves(m: dict) -> str:
        left, right = (list(m.get("halves") or ()) + [None, None])[:2]
        if left is None and right is None:
            return ""
        return f"; left half {_said(left)}, right half {_said(right)}"

    lines = [
        ("target", _said(row["target_units"]), "where the sheet put it"),
        ("arrived", _said(row["arrived_units"]),
         "where the first look found it, before any move"),
        ("last look", _said(row["last_look_units"]),
         "the hold's last check prescan, against the same picture"),
        ("delivered", measured(delivered),
         "the scan itself, against the same picture"
         + (f"; confidence {delivered['confidence']:.0f}"
            if delivered.get("confidence") is not None else "")
         + halves(delivered)
         + ("" if delivered.get("believed")
            else f"; not believed: {delivered.get('reason')}")),
        ("error", _said(row["error_units"]) if delivered.get("believed")
         else (f"({_said(row['error_units'])})"
               if row["error_units"] is not None else "--"),
         "delivered - target"),
        ("vs its prescan", measured(against),
         "the scan against its own last prescan"
         + (f" (scan {row['scan_direction']}, prescan "
            f"{row['prescan_direction']})"
            if row.get("scan_direction") or row.get("prescan_direction")
            else "")
         + halves(against)),
        ("moves", str(row["moves"]),
         ", ".join(_said(m) for m in row["moves_units"]) or "none sent"),
    ]
    if row.get("positions_units") is not None:
        lines.append(("approved.json", _said(row["positions_units"]),
                      "the position the file gives this frame"))
    return lines


def render_frame(row: dict, sheet: np.ndarray, delivered: np.ndarray):
    """One frame's picture: (a) and (b) on the left with (d) under them, and
    (c), both edges, on the right. Returns the image and where its guides
    were drawn, ``{"a": {...}, "b": {...}}`` in the image's own pixels."""
    import textwrap

    from PIL import Image, ImageDraw

    width = sheet.shape[1]
    at = row["target_px"]
    margin = max(ZOOM_COLUMNS / 2.0 + 4, math.ceil(abs(at)) + 12)
    a, a_x = panel(sheet, at, width, margin, SCALE)
    b, b_x = panel(delivered, 0.0, width, margin, SCALE)
    left, right = guides(width)
    zooms = [edge_zoom(sheet, at, delivered, 0.0, g) for g in (left, right)]
    title, label, small = _font(18), _font(15), _font(14)
    head, line = 30, 22
    column_w = max(a.shape[1], b.shape[1])
    # (d) wrapped to the column it sits in, so no line runs under the zooms:
    # about 8 pixels a character at this size.
    wrap = max(30, (column_w - 230) // 8)
    text = []
    for name, value, what in frame_lines(row):
        for i, part in enumerate(textwrap.wrap(what, wrap) or [""]):
            text.append((name if i == 0 else "", value if i == 0 else "",
                         part, 220))
    for note in row["notes"]:
        text += [("", "", part, 0) for part in textwrap.wrap(note, wrap + 25)]
    zoom_w = sum(z.shape[1] for z in zooms) + PAD
    total_w = PAD + column_w + 2 * PAD + zoom_w + PAD
    left_h = (head + line + a.shape[0] + PAD + line + b.shape[0] + PAD
              + line * len(text))
    right_h = head + line + max(z.shape[0] for z in zooms)
    picture = Image.new("RGB", (total_w, max(left_h, right_h) + PAD), BACKGROUND)
    draw = ImageDraw.Draw(picture)
    draw.text((PAD, 6), row["title"], fill=TEXT, font=title)

    y = head
    draw.text((PAD, y), "(a) as set on the sheet: the picture it was set on, "
              f"moved by the target, {_said(row['target_units'])}",
              fill=TEXT, font=label)
    y += line
    picture.paste(Image.fromarray(a), (PAD, y))
    layout = {"a": {"top": y, "bottom": y + a.shape[0],
                    "guides": [PAD + x for x in a_x]}}
    y += a.shape[0] + PAD
    draw.text((PAD, y), "(b) as delivered: the scan, on the same grid and "
              "the same guides", fill=TEXT, font=label)
    y += line
    picture.paste(Image.fromarray(b), (PAD, y))
    layout["b"] = {"top": y, "bottom": y + b.shape[0],
                   "guides": [PAD + x for x in b_x]}
    y += b.shape[0] + PAD
    for name, value, what, indent in text:
        if name:
            draw.text((PAD, y), name, fill=MUTED, font=small)
            draw.text((PAD + 130, y), value, fill=TEXT, font=small)
        draw.text((PAD + indent, y), what, fill=MUTED, font=small)
        y += line

    x = PAD + column_w + 2 * PAD
    draw.text((x, head), f"(c) both edges, x{ZOOM}: bands alternate the "
              "sheet's (amber) and the scan (blue)", fill=TEXT, font=label)
    layout["zooms"] = []
    for zoom in zooms:
        picture.paste(Image.fromarray(zoom), (x, head + line))
        layout["zooms"].append({"left": x, "top": head + line,
                                "width": zoom.shape[1],
                                "height": zoom.shape[0]})
        x += zoom.shape[1] + PAD
    return picture, layout


def render_summary(rows: list[dict], views: dict):
    """Every frame's (a) above its (b), small, labelled with its error."""
    from PIL import Image, ImageDraw

    if not rows:
        return None
    cells = []
    for row in rows:
        sheet, delivered = views[row["frame"]]
        width = sheet.shape[1]
        margin = math.ceil(abs(row["target_px"])) + 8
        a, _ = panel(sheet, row["target_px"], width, margin, 1)
        b, _ = panel(delivered, 0.0, width, margin, 1)
        cells.append((row, a, b))
    font = _font(14)
    line = 20
    cell_w = max(max(a.shape[1], b.shape[1]) for _, a, b in cells) + PAD
    cell_h = max(a.shape[0] + b.shape[0] for _, a, b in cells) + 2 * line + 6
    across = min(4, len(cells))
    down = -(-len(cells) // across)
    picture = Image.new("RGB", (PAD + across * cell_w, PAD + down * cell_h),
                        BACKGROUND)
    draw = ImageDraw.Draw(picture)
    for i, (row, a, b) in enumerate(cells):
        x = PAD + (i % across) * cell_w
        y = PAD + (i // across) * cell_h
        error = row["error_units"]
        said = ("error --" if error is None else
                f"error {_said(error)}" if row["delivered"].get("believed")
                else f"error ({_said(error)})")
        draw.text((x, y), f"frame {row['frame']}  {row['outcome']}  {said}",
                  fill=TEXT, font=font)
        picture.paste(Image.fromarray(a), (x, y + line))
        picture.paste(Image.fromarray(b), (x, y + line + a.shape[0] + 2))
    return picture


# --- the roll ---------------------------------------------------------------


def _positions(path: Path | None, say, asked: bool = False) -> dict:
    """The frames an `approved.json` places, by the strip's numbers, and the
    reference each names; nothing when it cannot be read or numbered, or is
    not there -- said, where it was ``asked`` for by name."""
    if path is None:
        return {}
    path = path / "approved.json" if path.is_dir() else path
    if not path.is_file():
        if asked:
            say(f"{path}: there is no such file; no positions are used")
        return {}
    try:
        shift = approved_shift(path, say=say)
    except ValueError as exc:
        say(f"{exc}; its positions are not used")
        return {}
    if shift is None:
        say(f"{path} does not say its numbers are places on the strip, and "
            "nothing beside it says how they were counted; its positions "
            "are not used")
        return {}
    offsets, _turns, _flips, entries, sources = read_approved(
        path, legacy=shift, say=say)
    return {"file": path, "offsets": offsets, "entries": entries,
            "sources": sources}


def check_roll(folder: Path, library_root: Path, out: Path,
               positions: Path | None = None, say=print) -> list[dict]:
    """Every held frame of one roll, measured and drawn into ``out``.

    Returns the summary's rows, one per frame checked; a frame that cannot be
    checked -- no entry, no hold record, no reference -- is said and left out.
    """
    folder, library_root, out = Path(folder), Path(library_root), Path(out)
    try:
        raw = read_manifest(folder / "roll.json", say=say)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if not raw.get("frames"):
        raise SystemExit(f"{folder} has no roll.json with frames in it: a "
                         "walk, or not a roll folder")
    # Onto the strip's numbers, as the window reads a roll; the library's
    # entries are joined on the numbers they were filed under, which are the
    # file's own.
    progress = renumbered(raw, fallback=walk_shift(folder), say=say)
    settings = manifest_settings(progress)
    roll = str(progress.get("roll") or folder.name)
    chosen = _positions(positions if positions is not None
                        else folder / "approved.json", say,
                        asked=positions is not None)
    index: dict | None = None
    walk: dict | None = None
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    views: dict = {}
    unheld: list[int] = []
    for filed, record in zip(raw["frames"], progress["frames"]):
        number = record.get("number")
        if not isinstance(number, int) or record.get("error"):
            continue
        entry = find_entry(record.get("entry"), library_root)
        if entry is None and not record.get("entry"):
            if index is None:
                index = frame_index(library_root)
            at = filed.get("number")
            entry = ((index["folders"].get(folder.name.casefold()) or {}).get(at)
                     or (index["names"].get(roll) or {}).get(at))
        if entry is None:
            if (record.get("registration") or {}).get("approved"):
                say(f"frame {number}: held, and its library entry is not in "
                    f"{library_root}; skipped")
            continue
        try:
            filed_as = json.loads((entry / "scan.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError) as exc:
            say(f"frame {number}: {entry.name} cannot be read ({exc}); skipped")
            continue
        # The entry's own record of the hold before its scan, which is the
        # roll's for the same run; the roll's where the entry kept none.
        hold = ((filed_as.get("registration") or {}).get("approved")
                or (record.get("registration") or {}).get("approved"))
        if not isinstance(hold, dict):
            unheld.append(number)
            continue
        reference, whence, reference_dpi = None, None, None
        named = find_entry(hold.get("reference_entry"), library_root)
        if named is not None:
            whence = "the hold's record"
        elif chosen and number in chosen["entries"]:
            named = find_entry(chosen["entries"][number], library_root)
            whence = Path(chosen["file"]).name if named is not None else None
        if named is None:
            if walk is None:
                walk = walk_references(folder, progress, library_root)
            walked = walk.get(number) or {}
            if walked.get("entry") is not None:
                named, whence = Path(walked["entry"]), "the walk's prescan entry"
            elif walked.get("file") is not None:
                try:
                    reference = preview.unorient(
                        tiff.read(str(walked["file"])), *walked["arrangement"])
                    whence = f"the walk's {Path(walked['file']).name}"
                except (OSError, ValueError) as exc:
                    say(f"frame {number}: {walked['file']} cannot be read "
                        f"({exc})")
        if named is not None:
            try:
                reference, ref_record = library.corrected(named)
                reference_dpi = _dpi_of(ref_record)
            except (OSError, ValueError) as exc:
                say(f"frame {number}: its reference {named.name} cannot be "
                    f"read ({exc}); skipped")
                continue
        if reference is None:
            say(f"frame {number}: nothing names the picture its position was "
                "set on; skipped")
            continue
        row = check_frame(number, roll, hold, entry, filed_as, reference,
                          reference_dpi or settings.get("prescan_resolution")
                          or PRESCAN_DPI, settings, chosen, whence,
                          named.name if named is not None else whence, say=say)
        if row is None:
            continue
        sheet, delivered = row.pop("_views")
        picture, layout = render_frame(row, sheet, delivered)
        name = out / f"frame{number:02d}.png"
        picture.save(name)
        # Where its panels and their guides are, for a reader that wants to
        # measure the picture rather than look at it.
        row["png"], row["layout"] = str(name), layout
        views[number] = (sheet, delivered)
        rows.append(row)
        say(f"frame {number}: {row['outcome']}, target "
            f"{_said(row['target_units'])}, delivered "
            f"{_said(row['delivered']['units'])}, error "
            f"{_said(row['error_units'])} -> {name.name}")
    if unheld:
        say(f"not held, so not checked: frame(s) "
            f"{', '.join(str(n) for n in unheld)}")
    # By frame, whatever order the roll's runs filed them in.
    rows.sort(key=lambda row: row["frame"])
    summary = render_summary(rows, views)
    if summary is not None:
        summary.save(out / "summary.png")
    write_summary(rows, out, folder, library_root, chosen)
    return rows


def check_frame(number: int, roll: str, hold: dict, entry: Path,
                filed_as: dict, reference: np.ndarray, reference_dpi,
                settings: dict, chosen: dict, whence, reference_name,
                say=print):
    """One held frame: its numbers, and the two pictures to draw them on.

    None, having said why, when its delivered entry cannot be read.
    """
    try:
        image, record = library.corrected(entry)
    except (OSError, ValueError) as exc:
        say(f"frame {number}: {entry.name} cannot be read ({exc}); skipped")
        return None
    scan = record.get("scan") or {}
    film = str(scan.get("film") or settings.get("film") or FILM_NEGATIVE)
    dpi = _dpi_of(record) or _number(settings.get("resolution"))
    reference_dpi = _number(reference_dpi)
    rows, width = reference.shape[:2]
    factor = (float(dpi) / float(reference_dpi) if dpi and reference_dpi
              else image.shape[1] / float(width))
    small = onto_grid(image, factor, (rows, width))
    delivered = measure(reference, small, about=_expected_px(hold, width))
    # The frame's own last prescan: the hold's last look, taken just before
    # the scan -- the corrected one the entry keeps beside it.
    last_path = entry / "prescan.tif"
    against: dict = {"believed": False, "reason": "no prescan.tif",
                     "units": None, "halves": None}
    if last_path.is_file():
        try:
            against = measure(tiff.read(str(last_path)), small)
        except (OSError, ValueError) as exc:
            against["reason"] = f"prescan.tif cannot be read ({exc})"

    target = _units_of(hold.get("target_mm"))
    error = (None if target is None or delivered["units"] is None
             else round(delivered["units"] - target, 2))
    moves = [m.get("asked_units") for m in hold.get("moves_sent") or ()
             if isinstance(m, dict) and m.get("asked_units") is not None]
    outcome = str(hold.get("outcome") or "?")
    in_file = (chosen or {}).get("offsets", {}).get(number)
    notes = [f"reference: {reference_name} (from {whence})",
             f"delivered: {entry.name}, {dpi or '?'} dpi, box-averaged "
             f"{factor:g} to one onto the reference's grid"]
    if delivered.get("row_reversed"):
        notes.append("the scan matched with its rows reversed, and is drawn "
                     "turned to match")
    shown_delivered = shown(small[::-1] if delivered.get("row_reversed")
                            else small, film)
    return {
        "frame": number,
        "title": (f"frame {number} of {roll} -- {say_hold(outcome)} "
                  f"({outcome}), {int(hold.get('moves') or 0)} move(s), "
                  f"decided by {hold.get('source') or '?'}"),
        "outcome": outcome,
        "source": hold.get("source"),
        "moves": int(hold.get("moves") or 0),
        "moves_units": [float(m) for m in moves],
        "target_units": target,
        "target_px": (0.0 if hold.get("target_mm") is None
                      else float(hold["target_mm"]) / APERTURE_MM * width),
        "arrived_units": arrived_units(hold, width),
        "last_look_units": _units_of(hold.get("final_mm")),
        "delivered": delivered,
        "error_units": error,
        "against_prescan": against,
        # The file's own position, shown only where it is not the target:
        # clamped on the way to the driver, or a roll held to proposals.
        "positions_units": (None if in_file is None
                            or (target is not None
                                and abs(units(in_file) - target) < 0.05)
                            else round(units(in_file), 2)),
        "film": film,
        "resolution_dpi": dpi,
        "factor": round(factor, 4),
        "scan_direction": (scan.get("read_direction") or {}).get("direction"),
        "prescan_direction": ((filed_as.get("prescan") or {}).get(
            "read_direction") or {}).get("direction"),
        "reference": reference_name,
        "reference_from": whence,
        "entry": entry.name,
        "notes": notes,
        "_views": (shown(reference, film), shown_delivered),
    }


#: The summary table. ``L / R`` is ``vs prescan`` for the left and the
#: right half of the picture on its own (`measure`'s ``halves``).
COLUMNS = ("frame", "outcome", "moves", "target", "arrived", "last look",
           "delivered", "error", "vs prescan", "L / R", "conf")


def table_rows(rows: list[dict]) -> list[list[str]]:
    """The summary table's cells, a list per frame, in `COLUMNS` order. A
    delivered position the match did not believe is in parentheses."""
    out = []
    for row in rows:
        delivered, against = row["delivered"], row["against_prescan"]

        def marked(value, believed):
            text = _said(value, unit="")
            return text if believed or value is None else f"({text})"

        out.append([
            str(row["frame"]), row["outcome"], str(row["moves"]),
            _said(row["target_units"], unit=""),
            _said(row["arrived_units"], unit=""),
            _said(row["last_look_units"], unit=""),
            marked(delivered.get("units"), delivered.get("believed")),
            marked(row["error_units"], delivered.get("believed")),
            marked(against.get("units"), against.get("believed")),
            _halves(against) or "--",
            ("--" if delivered.get("confidence") is None
             else f"{delivered['confidence']:.0f}"),
        ])
    return out


def write_summary(rows: list[dict], out: Path, folder: Path,
                  library_root: Path, chosen: dict) -> None:
    """The table on stdout, and as `summary.md` and `summary.json` in ``out``."""
    cells = table_rows(rows)
    widths = [max(len(c[i]) for c in [list(COLUMNS), *cells])
              for i in range(len(COLUMNS))]
    print()
    print("  ".join(name.rjust(w) for name, w in zip(COLUMNS, widths)))
    for cell in cells:
        print("  ".join(c.rjust(w) for c, w in zip(cell, widths)))
    print("units of the SLIDE param; error is delivered - target; vs "
          "prescan is the scan against its own last prescan, L / R its left "
          "and right halves; a parenthesised value is a match the "
          "correlation did not believe")
    markdown = [f"# Holds in {folder.name}", "",
                "Units of the SLIDE param. *error* is delivered - target; "
                "*vs prescan* is the scan against its own last prescan, and "
                "*L / R* the same for the left and the right half of the "
                "picture on their own. A parenthesised value is a match "
                "`measure_shift_mm` did not believe.", "",
                "| " + " | ".join(COLUMNS) + " |",
                "|" + "|".join("---:" for _ in COLUMNS) + "|"]
    markdown += ["| " + " | ".join(cell) + " |" for cell in cells]
    (out / "summary.md").write_text("\n".join(markdown) + "\n",
                                    encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({
        "roll": str(folder), "library": str(library_root),
        "positions": str(chosen["file"]) if chosen else None,
        "units": "the SLIDE param's, as every distance here",
        "frames": [{k: v for k, v in row.items() if k != "notes"}
                   for row in rows],
    }, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roll", type=Path, help="a roll folder, holding roll.json")
    ap.add_argument("--library", type=Path, default=library.DEFAULT_ROOT,
                    help="where its entries were filed (default: %(default)s)")
    ap.add_argument("--positions", type=Path, default=None, metavar="FILE",
                    help="an approved.json naming the picture each frame's "
                         "position was set on, for a frame whose hold record "
                         "names none (default: the roll's own, if it has one)")
    ap.add_argument("--out", type=Path, required=True,
                    help="where the pictures and the summary go; never the "
                         "roll or the library")
    args = ap.parse_args(argv)
    if not (args.roll / "roll.json").is_file():
        ap.error(f"{args.roll} has no roll.json")
    # Stored data is read and never written: a picture filed among a roll's
    # frames or in the library is something the next reader takes for one.
    out = args.out.resolve()
    for kept in (args.roll, args.library):
        if out == kept.resolve() or kept.resolve() in out.parents:
            ap.error(f"--out {args.out} is inside {kept}; give a folder of "
                     "its own")
    rows = check_roll(args.roll, args.library, args.out,
                      positions=args.positions)
    print(f"\n{len(rows)} held frame(s) checked; pictures and summary in "
          f"{args.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
