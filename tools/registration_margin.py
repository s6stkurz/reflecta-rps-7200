#!/usr/bin/env python3
"""Does `CONFIDENCE_FLOOR` still sit between the two clusters? Measured.

    uv run python tools/registration_margin.py
    uv run python tools/registration_margin.py --self-similar
    uv run python tools/registration_margin.py --rolls rolls/2026-09-14
    uv run python tools/registration_margin.py --holds
    uv run python tools/registration_margin.py --holds --search-units 94.6 --max-dy 3

The evidence behind `docs/frame-adjustment-findings.md`. `rolls/` and
`library/` are gitignored, so the numbers in that document cannot be committed
alongside it -- this is how they are re-derived, on whatever film is at hand.

Run it after any change to `register`, to the prescan resolution, or to
`SEARCH_UNITS`. Each of those moves the scale: confidence is a z-score over the
*searched* surface, so the same pair scores 23.8 at a 16 px reach and 129.7 at
200 px. A floor fitted at one reach means nothing at another.

**Labels are not ground truth here.** A library entry's `frame` is its number
within one run, and two runs start at different places on the strip, so the
same label is routinely a different picture -- measured: five entries labelled
frame 01 contained three distinct photographs. Using labels would have scored
those as catastrophic false negatives. So the arbiter is the pixels: align the
pair at the lag `register` found and correlate. Same picture comes back at
>= 0.95, a different one at <= 0.85, and the band between is reported rather
than assumed. `confidence` is then judged against that, never against a name.

What the three modes answer:

  default          does the floor separate same-picture from different-picture
  --self-similar   are grass/sky frames the dangerous case  (they are not)
  --holds          what every hold the driver made would score at another
                   reach, off-axis gate and floor -- its own looks against
                   its own reference, and against the other frames of its roll

`--search-units` (or `--search-mm`, the same reach as the millimetre
figure the records were made with, converted), `--max-dy`, `--floor` and
`--agreement` stand in for
`SEARCH_UNITS`, `MAX_DY_PX`, `CONFIDENCE_FLOOR` and `AGREEMENT_FLOOR` in every
mode, so a candidate is judged without editing `framing`. Nothing here
changes what the driver does; choosing is for a person.

Every confidence here is the one the driver judges (`framing.match_against`):
the shift found on the whole pictures, smoothed by `MATCH_SMOOTH`, and the
score taken again on the part both pictures show. A look recorded before
2026-10-06 holds the whole pictures' unsmoothed score instead, and is checked
against that (`recorded_reading`).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import library, preview, tiff
from rps7200.console import use_utf8_stdout
from rps7200.framing import (
    AGREEMENT_FLOOR,
    APERTURE_UNITS,
    CONFIDENCE_FLOOR,
    MATCH_SMOOTH,
    MAX_DY_PX,
    PRESCAN_ROWS_300,
    SEARCH_UNITS,
    match_against,
    measure_shift,
    reach_px,
    score_shared,
)
from rps7200.protocol import to_units
from rps7200.session import (
    prescan_arrangement,
    recorded_roll_name,
    walked_prescans,
)
from rps7200.uniformity import luminance, register, smoothing

#: Correlation of two frames aligned at the lag `register` chose. The arbiter.
#:
#: Placed by measurement, not by eye: over 3850 pairs of real film the
#: distribution is sharply bimodal and **nothing at all lands between 0.75 and
#: 0.85**. Different photographs pile up around 0.0 and tail off by 0.75; the
#: same picture twice starts at 0.89 and runs to 1.0. 0.80 is the middle of the
#: empty band.
#:
#: An earlier 0.95 was too strict and called 22 genuine same-picture pairs
#: false positives: two library passes of one frame differ in exposure and
#: metering, which costs real correlation without making them different
#: pictures. `ARBITER_MARGIN` guards against the same mistake on new film.
SAME_PICTURE = 0.80

#: How close to the cut a pair may land before the arbiter itself is suspect.
#: Nothing came within 0.05 in the data it was fitted on, so anything that does
#: is new behaviour and is reported rather than silently binned.
ARBITER_MARGIN = 0.05

#: How far from the peak a competing peak has to sit before it counts as a
#: rival rather than the same peak's shoulder.
RIVAL_GAP_PX = 3

# The off-axis gate is rows of a 300 dpi prescan, as the driver's is: a
# fraction of the pass's height (`framing.MAX_DY_FRACTION`, `MAX_DY_PX` of
# `PRESCAN_ROWS_300`), so a gate of any other number of rows is that many of
# them and means the same thing at any resolution.


class Gate(NamedTuple):
    """What a measurement must clear to be believed: `measure_shift`'s own
    four numbers, any of them replaced by a candidate.

    The reach is held in units and turned into columns of each pass
    through `framing.reach_px`, the one formula there is: this tool once
    searched 106 px where the driver searched 105, and a confidence is only
    comparable at the reach it was searched at. The off-axis gate is rows of a
    300 dpi prescan, judged as a fraction of the height as the driver judges
    it.
    """

    search_units: float = SEARCH_UNITS
    max_dy: int = MAX_DY_PX
    floor: float = CONFIDENCE_FLOOR
    agreement: float = AGREEMENT_FLOOR

    def reach(self, width: int) -> int:
        """Columns searched on a pass this wide."""
        return reach_px(width, self.search_units)

    def on_axis(self, dy: int, rows: int, max_dy: int | None = None) -> bool:
        """Whether a match this far off the film axis is one the transport
        could have made -- `measure_shift`'s test, at ``max_dy`` rows."""
        limit = (self.max_dy if max_dy is None else max_dy) / PRESCAN_ROWS_300
        return abs(dy) / max(rows, 1) <= limit

    def bears_out(self, agreement: float | None = None,
                  shared_px=None) -> bool:
        """Whether the part both pictures show bears a match out, as
        `measure_shift` asks: peaking where the whole did, and agreeing
        block for block. A measurement that has neither -- one recorded
        before 2026-10-06 -- is not asked."""
        if shared_px is not None and max(abs(int(v)) for v in shared_px) > 1:
            return False
        return agreement is None or agreement >= self.agreement

    def believes(self, confidence: float, dy: int, rows: int,
                 agreement: float | None = None, shared_px=None) -> bool:
        return (confidence >= self.floor and self.on_axis(dy, rows)
                and self.bears_out(agreement, shared_px))

    @property
    def is_drivers(self) -> bool:
        return (self.search_units, self.max_dy, self.floor, self.agreement) == (
            SEARCH_UNITS, MAX_DY_PX, CONFIDENCE_FLOOR, AGREEMENT_FLOOR)

    def describe(self) -> str:
        said = (f"reach {say_reach(self.search_units)}, |dy| <= {self.max_dy} "
                f"rows, floor {self.floor:g}, agreement {self.agreement:g}")
        if self.is_drivers:
            return said + " -- the driver's"
        return (said + f" -- a candidate; the driver's is "
                f"{SEARCH_UNITS:.1f} units, {MAX_DY_PX} rows, {CONFIDENCE_FLOOR:g}, "
                f"{AGREEMENT_FLOOR:g}")


def full_surface(a: np.ndarray, b: np.ndarray,
                 smooth: float | None = MATCH_SMOOTH) -> np.ndarray:
    """`register`'s whole correlation surface, before it takes a window --
    smoothed as `measure_shift` asks for it, or as ``smooth`` says (``None``
    for plain phase correlation, which every record before 2026-10-06 holds).

    Line for line with `uniformity.register`, the same operations on the same
    arrays in the same order, so a window cut from it holds the very numbers
    `register` would have searched -- bit for bit, which `--holds` checks on
    every look it re-measures (against `measure_shift` at today's reach,
    against `register` at any other a look was recorded at; `check_look`),
    and `tests/test_registration_studies.py` at several reaches. If
    `register` changes, this must.

    Duplicated rather than returned from `register`, whose signature has a
    dozen callers; this tool is the only thing that wants the whole surface,
    and wanting it once per pair rather than once per reach is what lets one
    pass over the library answer three reaches at once.
    """
    fa, fb = luminance(a), luminance(b)
    h = min(fa.shape[0], fb.shape[0])
    w = min(fa.shape[1], fb.shape[1])
    fa, fb = fa[:h, :w], fb[:h, :w]
    fa = fa - fa.mean()
    fb = fb - fb.mean()
    win = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    cross = np.fft.rfft2(fa * win) * np.conj(np.fft.rfft2(fb * win))
    mag = np.abs(cross)
    whitened = cross / np.where(mag > 0, mag, 1.0)
    if smooth is not None:
        whitened = whitened * smoothing((h, w), smooth)
    return np.fft.irfft2(whitened, s=(h, w))


def window_of(surf: np.ndarray, reach: int) -> tuple[np.ndarray, int, int]:
    """The part of a surface `register` searches at this reach, laid out as it
    lays it out, with the two reaches it clamped to."""
    h, w = surf.shape
    ry, rx = min(reach, h // 2), min(reach, w // 2)
    ys = [dy % h for dy in range(-ry, ry + 1)]
    xs = [dx % w for dx in range(-rx, rx + 1)]
    return surf[np.ix_(ys, xs)], ry, rx


def peak_of(surf: np.ndarray, reach: int) -> tuple[int, int, float]:
    """``(dy, dx, confidence)``: what `register` returns, from its surface."""
    window, ry, rx = window_of(surf, reach)
    peak = int(np.argmax(window))
    dy = peak // window.shape[1] - ry
    dx = peak % window.shape[1] - rx
    finite = window[np.isfinite(window)]
    spread = float(finite.std())
    confidence = (float((window.max() - finite.mean()) / spread)
                  if spread > 0 else 0.0)
    return int(dy), int(dx), confidence


def spectrum(image: np.ndarray) -> np.ndarray:
    """One side of `register`'s cross-power spectrum: the picture's luminance,
    its mean taken out, Hann-windowed and transformed -- what `full_surface`
    computes for each half of a pair the same size, by the same operations.

    Kept per picture by :class:`Pictures`, so a reference compared with forty
    looks is transformed once rather than forty times. The transforms are most
    of what a pair costs at these sizes: 428 is 4 x 107 and 287 is 7 x 41,
    slow lengths for an FFT.
    """
    f = luminance(image)
    f = f - f.mean()
    h, w = f.shape[:2]
    win = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    return np.fft.rfft2(f * win)


def surface_of(a_spectrum: np.ndarray, b_spectrum: np.ndarray,
               shape: tuple[int, int],
               smooth: float | None = MATCH_SMOOTH) -> np.ndarray:
    """`full_surface` from two :func:`spectrum` halves of a pair this size."""
    cross = a_spectrum * np.conj(b_spectrum)
    mag = np.abs(cross)
    whitened = cross / np.where(mag > 0, mag, 1.0)
    if smooth is not None:
        whitened = whitened * smoothing(shape, smooth)
    return np.fft.irfft2(whitened, s=shape)


def surface(a: np.ndarray, b: np.ndarray, reach: int) -> np.ndarray:
    """`register`'s searched window, which it computes and discards."""
    return window_of(full_surface(a, b), reach)[0]


def rival_margin(a: np.ndarray, b: np.ndarray, reach: int) -> float:
    """Peak z-score over the best competing peak's, both on the same surface.

    The obvious alternative statistic to `confidence`, measured here so the
    question stays settled: on real film the true peak beat every rival by
    16x to 59x, and no rival anywhere scored above 6.5 -- the whole surface
    away from the peak sits in the same band as a pure null. A statistic with
    nothing left to separate is not an improvement.
    """
    window = surface(a, b, reach)
    mu, sd = window.mean(), window.std()
    if sd <= 0:
        return 0.0
    peak = np.unravel_index(int(np.argmax(window)), window.shape)
    z = (window.max() - mu) / sd
    rest = window.copy()
    lo = max(0, peak[1] - RIVAL_GAP_PX)
    rest[:, lo:peak[1] + RIVAL_GAP_PX + 1] = -np.inf
    rival = (rest.max() - mu) / sd
    return float(z / rival) if rival > 0 else float("inf")


def aligned_correlation(a: np.ndarray, b: np.ndarray, dx: int,
                        dy: int = 0) -> float:
    """How alike the pair is once the found shift is taken out.

    Cropped well inside the frame: `np.roll` wraps, and the wrapped strip is
    unrelated content that would drag the correlation down on a true match.

    On luminance, as `register` itself is -- an RGB pass and an RGBI pass of
    the same frame are both in the library at the same resolution, and
    comparing them channel-for-channel raises `ValueError` on the fourth.

    ``dy`` takes out the rows the match found as well, which `--holds` needs
    and `separation` never passed: the cut was placed on negatives whose true
    pairs sat 0 or 1 rows apart. A slide's first frame comes back two or three
    rows off its reference, and on 2026-10-03-175055 and -221354 its own looks
    scored 0.71 to 0.79 aligned along x alone -- under the cut, so a true pair
    at confidence 160 to 237 was counted as a different picture -- and 0.89 to
    0.97 with the rows taken out too.
    """
    a, b = luminance(a), luminance(b)
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    shifted = np.roll(b, (dy, dx), axis=(0, 1))
    margin = max(abs(dx) + 8, w // 10)
    rim = max(abs(dy) + 8, h // 10) if dy else h // 10
    core = (slice(rim, -rim or None), slice(margin, -margin))
    x, y = a[core].ravel(), shifted[core].ravel()
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def walk(folder: Path) -> list[tuple[str, np.ndarray]] | None:
    """A walk's prescans, each turned back into the film's own orientation.

    Read through the walk's own records (`session.walked_prescans`) and
    un-turned by the pair each file was written with
    (`session.prescan_arrangement`), as the window and `scan_roll.py
    --approved` read them. The files lie on disk the way the operator viewed
    them -- turned or mirrored, per frame if the turn changed mid-walk -- and
    were read as they lay: x-axis measurements then ran along the frame's
    height, or with left and right swapped. And a glob took in
    `prescanNN-before.tif`, the picture before a correction, which sorts just
    before its frame and shifted every later frame's place in the walk.

    None when the folder has no manifest to read the walk from.
    """
    manifest = None
    for name in ("survey.json", "roll.json"):
        if (folder / name).exists():
            try:
                manifest = json.loads((folder / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            break
    if manifest is None:
        return None
    out = []
    for number, path, record in walked_prescans(folder, manifest):
        try:
            image = tiff.read(str(path))
        except (OSError, ValueError):
            continue
        upright = preview.unorient(image, *prescan_arrangement(manifest, record))
        out.append((f"{number:02d} {path.name}", upright.astype(np.float64)))
    return out


def cohort(folder: Path, dpi: int) -> list[tuple[str, np.ndarray]]:
    """Every comparable frame in one folder, which is one pass over one strip.

    Compared only within a folder, never across: `rolls/*/prescanNN.tif` are
    written rotated for the contact sheet and library scans are not, so mixing
    them would compare a frame against a turned copy of itself.

    A walk's prescans come upright from :func:`walk`. A folder with no
    manifest to say how its files were turned is read as it lies -- the
    before-correction pictures left out -- which is only right for a walk
    made with no turn at all.

    A library folder is filtered to one resolution as well. `register` crops a
    mismatched pair to their common size, which for two resolutions of the same
    frame is not the same piece of film -- it would answer confidently and
    wrongly, and the whole point here is to catch exactly that.
    """
    walked = walk(folder)
    if walked:
        return walked
    out = []
    for path in sorted(folder.glob("prescan*.tif")):
        if path.stem.endswith("-before"):
            continue
        try:
            out.append((path.name, tiff.read(str(path)).astype(np.float64)))
        except (OSError, ValueError):
            continue
    if out:
        return out
    for record in sorted(folder.glob("*/scan.json")):
        try:
            scan = json.loads(record.read_text(encoding="utf-8")).get("scan") or {}
            if int(scan.get("resolution_dpi") or 0) != dpi:
                continue
            # Corrected, to match the rolls/ prescans in the other branch:
            # those are written from the corrected pass, and comparing a
            # corrected frame against a raw one measures the shading, not the
            # registration.
            image, info = library.corrected(record.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        # And only where it was: an entry `corrected` hands back raw -- a
        # deliberately raw ladder, one with no reference -- was mixed in as
        # though it were, which is the comparison the line above rules out.
        # A demo entry is not a pass of any film.
        if (info.get("corrected") != "applied"
                or (info.get("extra") or {}).get("demo")):
            continue
        out.append((record.parent.name, image.astype(np.float64)))
    return out


def separation(images: list[tuple[str, np.ndarray]], dpi: int,
               pool: dict[str, list[float]] | None = None,
               gate: Gate = Gate()) -> int:
    """Register every pair, sort by the arbiter, report where the floor falls.

    `pool` collects both clusters across cohorts. One strip's worst null is not
    the worst null -- 2026-09-14 alone tops out at 11.4 and 2026-09-11 reaches
    30.2 -- so the figure worth quoting is the one over every roll at hand.
    """
    # The driver's reach for passes this wide, not one worked out from the
    # resolution: `measure_shift` asks `framing.reach_px` with the pass's
    # own width, and so does this.
    width = images[0][1].shape[1]
    reach = gate.reach(width)
    same: list[tuple] = []
    differ: list[tuple] = []
    unsure: list[tuple] = []
    for (na, a), (nb, b) in itertools.combinations(images, 2):
        # Both ways up, the stronger taken, and scored on the part both show:
        # `framing.match_against`, which is how `measure_shift` scores the
        # match that moves film. One orientation alone measured a different
        # statistic from the gate this tool exists to re-fit: over a pair of
        # different pictures the larger of two null draws is higher than one,
        # so its worst null was a lower bound on the production gate's.
        m = match_against(a, b, reach)
        dy, dx, conf, turned = m.dy, m.dx, m.confidence, m.flipped
        agrees = gate.bears_out(m.agreement, m.shared_px)
        # The rows taken out as well as the columns, as `--holds` judges a
        # pair. Along x alone, two passes of one slide frame a few rows apart
        # correlated under the cut -- 2026-10-03-221354 frame 1, 157.5 at dy
        # 2, 0.74; -175055 frame 1, 126.8 at dy 3 once the gate admitted
        # three rows, 0.71 -- and each was reported as a false positive: the
        # tool asked for a re-fit on pairs that are the same picture, 0.97
        # and 0.89 with the rows out too.
        corr = aligned_correlation(a, b[::-1] if turned else b, dx, dy)
        row = (na, nb, dy, dx, conf, corr, min(a.shape[0], b.shape[0]),
               agrees)
        (same if corr >= SAME_PICTURE else differ).append(row)
        if abs(corr - SAME_PICTURE) < ARBITER_MARGIN:
            unsure.append(row)

    # `unsure` overlaps both, so it is not part of the total.
    print(f"  {len(images)} frames, {len(same) + len(differ)} pairs, "
          f"reach {reach} px ({say_reach(gate.search_units)} across {width} "
          f"columns, {dpi} dpi)")
    for label, rows in (("same picture", same), ("different picture", differ)):
        if not rows:
            continue
        c = np.array([r[4] for r in rows])
        print(f"    {label:18s} n={len(rows):4d}  confidence "
              f"{c.min():7.1f} .. {c.max():7.1f}   median {np.median(c):7.1f}")
    for na, nb, _dy, _dx, conf, corr, _rows, _agrees in unsure:
        print(f"    ?? the arbiter is unsure about {na} vs {nb}: correlates "
              f"{corr:.4f}, within {ARBITER_MARGIN} of the {SAME_PICTURE} cut. "
              f"Nothing did that on the film this was fitted on -- look at the "
              f"pair before believing its verdict either way.")

    problems = 0
    # The only failure that matters. Everything else degrades safely: a match
    # below the floor is refused, the film is not moved, the frame is scanned
    # and flagged. A confident match on a different picture would move it.
    for na, nb, dy, dx, conf, corr, rows, agrees in differ:
        if gate.believes(conf, dy, rows) and agrees:
            problems += 1
            print(f"    !! FALSE POSITIVE {na} vs {nb}: confidence {conf:.1f} "
                  f"above the floor, but the pair correlates at only {corr:.4f}")
    for na, nb, dy, dx, conf, corr, _rows, _agrees in same:
        if conf < gate.floor:
            print(f"    -- missed {na} vs {nb}: correlates {corr:.4f} but scores "
                  f"{conf:.1f}; refused, so this frame is scanned unmoved")

    if pool is not None:
        pool.setdefault("same", []).extend(r[4] for r in same)
        pool.setdefault("differ", []).extend(r[4] for r in differ)

    if same and differ:
        gap_lo = max(r[4] for r in differ)
        gap_hi = min(r[4] for r in same)
        where = "inside" if gap_lo < gate.floor < gap_hi else "OUTSIDE"
        print(f"    gap {gap_lo:.1f} .. {gap_hi:.1f}; floor {gate.floor:g} "
              f"falls {where} it")
        if same:
            margins = [rival_margin(a, b, reach)
                       for (na, nb, *_), (a, b) in
                       ((r, (dict(images)[r[0]], dict(images)[r[1]])) for r in same)]
            print(f"    true peak beats its best rival by "
                  f"{min(margins):.0f}x .. {max(margins):.0f}x")
    return problems


def self_similar(images: list[tuple[str, np.ndarray]], dpi: int,
                 gate: Gate = Gate()) -> int:
    """Shift real frames by known amounts; does texture ever win over truth?

    The grass-and-sky question. A second pass is *synthesised* here -- the
    frame displaced, plus a real pass-to-pass residual measured from a genuine
    repeat pair in the same cohort. Synthesising the residual instead gets this
    wrong twice over: rolling a frame rolls its noise too, which matches
    perfectly and scores everything alike, and Gaussian noise added to
    synthetic content collapses everything to nothing. The content must be real
    film and the residual must be real, or the answer is an artefact.
    """
    reach = gate.reach(images[0][1].shape[1])
    residual, donors = None, set()
    for (na, a), (nb, b) in itertools.combinations(images, 2):
        m = match_against(a, b, reach)
        dy, dx, conf = m.dy, m.dx, m.confidence
        # Judged and subtracted with the rows taken out as well: a repeat
        # pair a few rows apart is a repeat pair (see `separation`), and its
        # residual taken along x alone would be the picture against itself
        # three rows down -- content, not the pass-to-pass noise wanted here.
        if (conf >= gate.floor
                and aligned_correlation(a, b, dx, dy) >= SAME_PICTURE):
            h = min(a.shape[0], b.shape[0])
            w = min(a.shape[1], b.shape[1])
            residual = a[:h, :w] - np.roll(b, (dy, dx), axis=(0, 1))[:h, :w]
            # The residual carries a trace of the two frames it was taken from,
            # so testing either of them correlates the "noise" with the content
            # and dips the score at whichever shift lines the trace back up --
            # seen at 110 against ~150 for its neighbours. Excluded, not
            # explained away.
            donors = {na, nb}
            print(f"  residual from the real pair {na} / {nb}: "
                  f"std {residual.std():.2f} DN  (both excluded below)")
            break
    if residual is None:
        print("  no repeat pair in this cohort -- cannot measure a real residual,"
              " and a synthetic one would answer the wrong question")
        return 0

    shifts = (-24, -8, -3, 3, 8, 24)
    print(f"  {'frame':>22s} {'detail':>7s}  " +
          "  ".join(f"{s:>+6d}" for s in shifts))
    wrong = 0
    h, w = residual.shape[:2]
    # Ascending, so the claim underneath is something the reader can see
    # rather than take on trust.
    ordered = sorted(
        ((name, image[:h, :w]) for name, image in images if name not in donors),
        key=lambda item: float(np.abs(np.diff(luminance(item[1]), axis=1)).mean()),
    )
    for name, frame in ordered:
        detail = float(np.abs(np.diff(luminance(frame), axis=1)).mean())
        cells = []
        for shift in shifts:
            # Content displacement is -dx; see `measure_shift`.
            m = match_against(frame, np.roll(frame, shift, axis=1) + residual,
                              reach)
            dx, conf = m.dx, m.confidence
            ok = dx == -shift
            cells.append(f"{conf:6.0f}{'' if ok else '!'}")
            if not ok and conf >= gate.floor:
                wrong += 1
        print(f"  {name:>22s} {detail:7.2f}  " + "  ".join(cells))
    print("\n  In ascending detail, so confidence should RISE down the table:"
          "\n  textured frames are the best case for phase correlation, not the"
          "\n  worst. A '!' marks a recovered lag that was not the one applied.")
    return wrong


# --- the holds the driver made, measured again ------------------------------
#
# `separation` asks whether the floor divides pictures that happen to be in
# the same folder. `--holds` asks the question the floor is actually for: of
# the looks a hold took -- its arrival, and where it left the frame -- how
# many would a candidate gate have believed, and would it have believed a
# look at the wrong frame. The pairs are the hold's own: its reference (the
# walk's prescan the operator approved) against its looks, and against the
# looks of every other frame of the same roll, which is the wrong frame a
# missed or doubled advance would put in the aperture: same film, same lamp,
# same session. Those are realistic nulls in a way two frames from different
# months are not.
#
# Every number comes from the library: a reference through
# `library.corrected`, as the window held it, an arrival from the entry the
# roll filed tagged `before`, and the last look from the frame entry's own
# `prescan.tif`. A hold that made no move looked once, and that picture is
# both.
#
# What a pair *is* is the arbiter's call, not the pairing's -- the module's
# rule -- judged at the whole surface's peak with the whole shift taken out.
# Of a reference against another frame's look it says "same picture" twice
# over on this library: a rescan, the same frame under another run's number
# (the two runs of 2026-09-22), and two neighbours shot of one scene
# (2026-09-27 11 and 12, 2026-10-03-182818 1 to 3). Neither is a null, and no
# gate on pictures can tell the second from a true look; both are shown, set
# aside, with how strongly they matched.

#: The reach every stored hold was measured at, in units: the search reach
#: from 2026-09-14, when it became 9.0 mm (85.1 units), until each look began
#: recording its own (`reach_px`, 2026-10-05). A fact about the records, not a
#: copy of the driver's constant -- which is what may change once this has
#: been run -- so a look that does not say is read at this, and one that does
#: at its own. The millimetre figure over `MM_PER_UNIT`, as `SEARCH_UNITS` is
#: written, so it searches exactly the columns it always did.
RECORDED_SEARCH_UNITS = to_units(9.0)

#: The reaches `--holds` compares when none is given, in units. 85.1 is the
#: one the records were taken at, 105 columns of a 300 dpi prescan, and so the
#: only one they can be checked against; 94.6 and 104.1 were the candidates
#: for a frame arriving further out -- one param-87 command travels about 110
#: columns -- and 104.1, 129 columns, is the driver's `SEARCH_UNITS` since
#: 2026-10-05. Written as the millimetre figures they were chosen as (9.0,
#: 10.0, 11.0) over `MM_PER_UNIT`.
HOLD_REACHES_UNITS = tuple(to_units(mm) for mm in (9.0, 10.0, 11.0))

#: The floors tabulated. 55 is the one in force; the rest is the band its own
#: comment says the 2026-09-14 fit allowed ("anything in 35-55 fits").
HOLD_FLOORS = (30.0, 35.0, 40.0, 45.0, 50.0, 55.0)

#: The off-axis gates tabulated, in rows of a 300 dpi prescan. Slides came
#: back at dy = 3 on the first frame of a roll and were refused at 2.
HOLD_DY_ROWS = (2, 3, 4, 5)

#: How far apart a true pair sits, in prescan columns, for the confidence
#: table. A frame arriving 30 columns or more out is one the walk's own aims
#: or a slipped advance put there; 80 and over is near the end of the reach.
SHIFT_BINS = ((0, 30), (30, 80), (80, None))

#: A reach larger than any surface, which `register`'s clamp turns into the
#: whole of it. The arbiter judges a pair at the peak found here, so the label
#: does not depend on the reach being judged: a true pair whose shift lies
#: past a candidate's reach is still known to be one -- and is then counted
#: as what it is at that reach, a match the gate cannot see.
WHOLE_SURFACE = 1 << 16

#: How long before a frame's scan its arrival prescan may have been taken and
#: still be the picture its hold started from, in seconds. Measured on the
#: 44 moved holds that had one on 2026-10-05: 53 to 96 s before the scan
#: began (arrival, moves and their prescans, metering). The walk's own
#: pictures of the same frame before an aim are 195 s and more before it, and
#: the roll's start bounds them out where `roll.json` names the entry.
BEFORE_WITHIN_S = 300.0


class Look(NamedTuple):
    """One picture a hold took, and what the hold recorded of it."""

    source: tuple            # ("entry", path) or ("tiff", path)
    kind: str                # "first", "last", or "only" (one look, no move)
    stored: dict             # its record in the hold's `history`


class Hold:
    """One stored hold: a frame, its reference, and its looks on disk.

    A plain class and not a dataclass: the tests load this file as a module
    of no package, and a dataclass under ``from __future__ import
    annotations`` looks its module up in `sys.modules` to read its fields.
    """

    def __init__(self, roll: str, number: int | None, film: str, entry: Path,
                 record: dict, reference: tuple | None = None,
                 looks: list[Look] | None = None,
                 label_number: int | None = None, day: str | None = None):
        self.roll, self.number, self.film = roll, number, film
        self.entry, self.record, self.reference = entry, record, reference
        #: How the reference is known: "named" by the hold's
        #: `reference_entry`, or "identified" -- a hold from before records
        #: named one, matched to the walk prescan that reproduces what it
        #: measured, to 0.01 and the pixel. That is not a check of anything;
        #: it is how the pair is found.
        self.origin = ""
        self.looks: list[Look] = list(looks or [])
        self.label_number = label_number
        #: Every entry the hold is in: its own, and for a slide's RGBI pass
        #: the colour pass that kept its picture (see `stored_holds`).
        self.names: set[str] = {Path(entry).name}
        #: Read from `roll.json` because the entry kept no record of the hold.
        self.from_manifest = False
        #: The UTC day the frame was filed, which bounds where its walk is.
        self.day = day

    @property
    def label(self) -> str:
        number = self.number if self.number is not None else self.label_number
        return f"{self.roll} #{number if number is not None else '?'}"


def _time(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def entry_named(library_root: Path, named) -> Path | None:
    """The entry a record names, under the library given here.

    A record says ``library\\20261005T...`` -- relative to wherever the window
    ran, with Windows separators -- so only the directory name means anything
    here, and only if it is there.
    """
    if not named:
        return None
    path = Path(library_root) / PureWindowsPath(str(named)).name
    return path if (path / "scan.json").exists() else None


def _label_number(record: dict) -> int | None:
    """The frame number at the end of an entry's label, `2026-09-27-03` or
    `scan600/01`. A number within one run, not a place on the strip."""
    frame = str((record.get("film") or {}).get("frame") or "")
    digits = ""
    for ch in reversed(frame):
        if not ch.isdigit():
            break
        digits = ch + digits
    return int(digits) if digits else None


#: Tags every roll entry carries, which name no roll.
_COMMON_TAGS = frozenset({"gui", "roll", "prescan", "debug", "before", "probe",
                          "hold", "colour pass", "infrared pass"})


def _roll_of(record: dict, rolls_root: Path) -> str | None:
    """The roll an entry from before `roll_membership` belongs to: the tag
    that names a folder under ``rolls_root``, or failing that the only tag
    left once the common ones are set aside."""
    tags = [t for t in record.get("tags") or [] if t not in _COMMON_TAGS]
    for tag in tags:
        if (Path(rolls_root) / tag).is_dir():
            return tag
    return tags[0] if len(tags) == 1 else None


def stored_holds(library_root: Path, rolls_root: Path,
                 records: list[dict] | None = None
                 ) -> tuple[list[Hold], dict[str, list[tuple]]]:
    """Every hold the library kept a look of, with its reference where it is
    named and every candidate for one where it is not.

    The library rather than `roll.json`, which keeps the latest run of each
    frame and nothing of a run before it; a frame entry records its hold on
    every run. `roll.json` is read as well, for a hold whose entry kept no
    record of it. A hold that never looked -- holding was off -- has nothing
    to measure and is left out.

    A slide scanned with infrared from the window is two entries a frame
    (`DirectScanner.wants_colour_pass`), and they split the hold between
    them: the RGBI pass, tagged `infrared pass`, carries its record, and its
    RGB partner, tagged `colour pass` and found by their shared
    `infrared_pair` id, keeps the prescan it looked at. So such a hold is
    taken once, the record from the one and the picture from the other. It
    was left out as "the same hold a second time", which in this library it
    never was: the colour pass records none, and those holds came back only
    where `roll.json` still kept the frame -- a frame's earlier run on a
    slide roll was lost.

    Returns the holds and the walk prescans a hold with no named reference
    may have been held to, as ``(source, number, day)``: per roll, its
    prescan entries in the library and then the folder's own `prescanNN.tif`
    turned back upright; and under ``""`` every one of them, for a roll held
    to a walk made under another name -- `scan600` to `registration-I`, the
    first run of `2026-09-22` to `aligned-strip`. ``records`` is the
    library's, for a caller that has read them already.
    """
    library_root, rolls_root = Path(library_root), Path(rolls_root)
    if records is None:
        records = library.entries(library_root)
    befores: dict[tuple, list[tuple[float, Path]]] = defaultdict(list)
    candidates: dict[str, list[tuple]] = defaultdict(list)
    colour: dict[str, tuple[dict, Path]] = {}
    for record in records:
        tags = record.get("tags") or []
        member = (record.get("extra") or {}).get("roll_membership") or {}
        path = library.entry_path(library_root, record)
        pair = (record.get("extra") or {}).get("infrared_pair") or {}
        if pair.get("id") and "colour pass" in tags:
            colour[str(pair["id"])] = (record, path)
        if member.get("kind") == "prescan" and "before" in tags:
            when = (_time((record.get("extra") or {}).get("started_utc"))
                    or _time(record.get("created")))
            if when is not None:
                befores[(member.get("roll"), member.get("number"))].append(
                    (when, path))
        elif "prescan" in tags and "before" not in tags and not member:
            # A walk prescan from before entries said which roll they were.
            item = (("entry", path), _label_number(record),
                    _day(record.get("created")))
            candidates[""].append(item)
            roll = _roll_of(record, rolls_root)
            if roll:
                candidates[roll].append(item)

    # The roll's own `roll.json`: which entries its latest run filed, and
    # when that run started -- the bound on its arrival pictures.
    latest: dict[str, float] = {}
    #: Each folder of a roll's records, by the roll its frames are filed
    #: under: a roll from before strips by its folder, a strip by its roll's
    #: label -- two strips of a roll are two manifests of one roll, and a
    #: listing one level deep missed every strip (`library.roll_folders`).
    manifests: list[tuple[str, dict]] = []
    if rolls_root.is_dir():
        for folder in library.roll_folders(rolls_root, trash=False):
            try:
                manifest = json.loads((folder / "roll.json").read_text(
                    encoding="utf-8"))
            except (OSError, ValueError):
                continue
            manifests.append((roll_named(folder), manifest))
            started = _time(manifest.get("started"))
            for frame in manifest.get("frames") or ():
                named = entry_named(library_root, frame.get("entry"))
                if named is not None and started is not None:
                    latest[named.name] = started

    holds: list[Hold] = []
    seen: set[str] = set()
    for record in records:
        approved = (record.get("registration") or {}).get("approved") or {}
        path = library.entry_path(library_root, record)
        picture = path
        if "infrared pass" in (record.get("tags") or []):
            pair = (record.get("extra") or {}).get("infrared_pair") or {}
            partner = colour.get(str(pair.get("id") or ""))
            if partner is not None:
                if (((partner[0].get("registration") or {}).get("approved")
                     or {}).get("history")):
                    continue              # the colour pass carries it itself
                picture = partner[1]
        hold = _hold_from(record, approved, path, library_root, rolls_root,
                          befores, latest, picture=picture)
        if hold is not None:
            holds.append(hold)
            seen.update(hold.names)

    # A hold `roll.json` kept and its entry did not.
    for roll, manifest in manifests:
        for frame in manifest.get("frames") or ():
            approved = (frame.get("registration") or {}).get("approved") or {}
            path = entry_named(library_root, frame.get("entry"))
            if path is None or path.name in seen or not approved:
                continue
            try:
                record = json.loads((path / "scan.json").read_text(
                    encoding="utf-8"))
            except (OSError, ValueError):
                continue
            record.setdefault("extra", {}).setdefault("roll_membership", {
                "roll": roll, "number": frame.get("number"), "kind": "frame"})
            hold = _hold_from(record, approved, path, library_root,
                              rolls_root, befores, latest)
            if hold is not None:
                hold.from_manifest = True
                holds.append(hold)
                seen.update(hold.names)

    if any(h.reference is None for h in holds) and rolls_root.is_dir():
        for folder in library.roll_folders(rolls_root, trash=False):
            files = _walk_files(folder)
            candidates[roll_named(folder)].extend(files)
            candidates[""].extend(files)
    return holds, candidates


def roll_named(folder: Path) -> str:
    """The roll a folder of records is filed under: a roll from before
    strips by its folder's name, as it always was, and a strip of a roll of
    strips by its roll's label (`session.recorded_roll_name`) -- what its
    entries' `roll_membership` names, never "strip-02"."""
    folder = Path(folder)
    if library.lock_folder(folder) == folder:
        return folder.name
    return recorded_roll_name(folder) or library.lock_folder(folder).name


def _day(text) -> str | None:
    """The UTC day of a record's time, ``YYYY-MM-DD``."""
    when = _time(text)
    if when is None:
        return None
    return datetime.fromtimestamp(when, timezone.utc).date().isoformat()


def _near(day: str | None, other: str | None) -> bool:
    """Within a day of each other: a walk is held to the same day or the next."""
    if not day or not other:
        return False
    try:
        a, b = (datetime.fromisoformat(d).date() for d in (day, other))
    except ValueError:
        return False
    return abs((a - b).days) <= 1


def _hold_from(record: dict, approved: dict, path: Path, library_root: Path,
               rolls_root: Path, befores: dict, latest: dict,
               picture: Path | None = None) -> Hold | None:
    """One hold from the entry that recorded it. ``picture`` is the entry
    whose `prescan.tif` its look is: this one, or for an RGBI pass of a
    slide its colour pass (see :func:`stored_holds`)."""
    picture = path if picture is None else picture
    history = approved.get("history") or []
    if not history or not (picture / "prescan.tif").exists():
        return None
    member = (record.get("extra") or {}).get("roll_membership") or {}
    if member and member.get("kind") not in ("frame", "infrared pass"):
        return None
    roll = member.get("roll") or _roll_of(record, rolls_root)
    if not roll:
        return None
    number = member.get("number")
    hold = Hold(roll=str(roll), number=int(number) if number is not None else None,
                film=str((record.get("scan") or {}).get("film") or "?"),
                entry=path, record=approved,
                label_number=_label_number(record),
                day=_day(record.get("created")))
    hold.names = {path.name, picture.name}
    named = entry_named(library_root, approved.get("reference_entry"))
    if named is not None:
        hold.reference, hold.origin = ("entry", named), "named"

    prescan = ("tiff", picture / "prescan.tif")
    if not approved.get("moves"):
        hold.looks.append(Look(prescan, "only", history[0]))
        return hold
    hold.looks.append(Look(prescan, "last", history[-1]))
    # This record's own start: for a slide's two passes the RGBI pass's,
    # which follows the hold, where the colour pass starts minutes later.
    started = _time((record.get("extra") or {}).get("started_utc"))
    if started is None or number is None:
        return hold
    lo = started - BEFORE_WITHIN_S
    run = next((latest[name] for name in (path.name, picture.name)
                if name in latest), None)
    if run is not None:
        lo = max(lo, run - 1.0)
    arrived = [(when, p) for when, p in befores.get((roll, number), ())
               if lo <= when <= started]
    if arrived:
        hold.looks.insert(0, Look(("entry", max(arrived)[1]), "first",
                                  history[0]))
    return hold


def _walk_files(folder: Path) -> list[tuple]:
    """A roll folder's prescans as candidate references, upright.

    The walk's records first, each turned back by its own arrangement; then
    any other `prescanNN.tif` left behind by an earlier walk into the same
    folder, turned back by the manifest's. Never a `-before` picture: that
    is the frame before an aim, never what an operator approved.
    """
    out: list[tuple] = []
    manifest: dict = {}
    for name in ("survey.json", "roll.json"):
        try:
            manifest = json.loads((folder / name).read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    name = folder.name
    day = (name[:10] if len(name) >= 10 and name[4] == "-" and name[7] == "-"
           and name[:4].isdigit() else _day(manifest.get("started")))
    listed = set()
    if manifest:
        for number, path, record in walked_prescans(folder, manifest):
            turn, mirrored = prescan_arrangement(manifest, record)
            out.append((("walk", path, turn, mirrored), number, day))
            listed.add(path.name)
    turn = int(manifest.get("rotation") or 0) % 360 if manifest else 0
    mirrored = bool(manifest.get("flipped")) if manifest else False
    for path in sorted(folder.glob("prescan*.tif")):
        if path.name in listed or path.stem.endswith("-before"):
            continue
        digits = "".join(ch for ch in path.stem if ch.isdigit())
        out.append((("walk", path, turn, mirrored),
                    int(digits) if digits else None, day))
    return out


class Pictures:
    """Each picture once, however many pairs it is in, and its :func:`spectrum`
    each way up; kept for one roll."""

    def __init__(self):
        self._held: dict[tuple, np.ndarray | None] = {}
        self._spectra: dict[tuple, np.ndarray] = {}

    def __call__(self, source: tuple) -> np.ndarray | None:
        key = _key(source)
        if key not in self._held:
            self._held[key] = self._load(source)
        return self._held[key]

    def spectrum(self, source: tuple, flipped: bool = False) -> np.ndarray:
        key = (_key(source), flipped)
        if key not in self._spectra:
            image = self(source)
            assert image is not None, source
            # `look[::-1]`, the view `measure_shift` hands `register`, and
            # not a copy of it: the transform has to see what register sees.
            self._spectra[key] = spectrum(image[::-1] if flipped else image)
        return self._spectra[key]

    def readings(self, reference: tuple, look: tuple, reaches: list[int],
                 smooth: float | None = MATCH_SMOOTH) -> dict[int, Reading]:
        """:func:`readings` for two pictures held here, from their kept
        spectra: the same numbers to the bit, for a pair the same size --
        the only kind this compares. Found on the whole pictures; see
        :func:`scored` for the confidence the gate judges."""
        shape = self(look).shape[:2]
        a = self.spectrum(reference)
        return peaks(surface_of(a, self.spectrum(look), shape, smooth),
                     surface_of(a, self.spectrum(look, True), shape, smooth),
                     reaches)

    @staticmethod
    def _load(source: tuple) -> np.ndarray | None:
        kind = source[0]
        try:
            if kind == "entry":
                image, info = library.corrected(source[1])
                # As the window held it: a prescan it could not correct was
                # never anybody's reference, and comparing it with corrected
                # looks would measure the shading.
                return image if info.get("corrected") == "applied" else None
            if kind == "tiff":
                return tiff.read(str(source[1]))
            if kind == "walk":
                return preview.unorient(tiff.read(str(source[1])),
                                        source[2], source[3])
        except (OSError, ValueError, KeyError):
            return None
        raise ValueError(f"unknown picture source {source!r}")


class Reading(NamedTuple):
    """What the gate would see at one reach: `measure_shift`'s numbers.

    Straight from :func:`peaks`, ``confidence`` is the whole pictures' score
    at the peak; through :func:`scored`, it is the part both show -- the one
    the gate judges -- and ``whole`` keeps the other.
    """

    dy: int
    dx: int
    confidence: float
    flipped: bool
    #: The whole pictures' score, once :func:`scored` has replaced
    #: ``confidence`` with the shared part's.
    whole: float | None = None
    #: Where the shared part peaked, ``(rows, columns)`` in the picture's sign.
    shared_px: tuple[int, int] | None = None
    #: How alike the shared part is block for block (`framing.agreement`).
    agreement: float | None = None

    @property
    def px(self) -> int:
        return -self.dx

    def labelled(self) -> dict:
        """For `--json`: named, and with `px` -- the picture's displacement
        in the sign every report here prints -- rather than `register`'s dx,
        which is its negative. Converted by hand because `json` writes a
        named tuple as a bare list and never asks how: the file held
        ``[dy, dx, confidence, flipped]`` unlabelled, read naturally as
        ``[dy, px, ...]``, which gets every shift's sign backwards."""
        out = {"dy": int(self.dy), "px": int(self.px),
               "confidence": round(float(self.confidence), 3),
               "flipped": bool(self.flipped)}
        if self.whole is not None:
            out["whole"] = round(float(self.whole), 3)
        if self.shared_px is not None:
            out["shared_px"] = [int(v) for v in self.shared_px]
        if self.agreement is not None:
            out["agreement"] = round(float(self.agreement), 4)
        return out


def _key(source: tuple) -> tuple:
    return tuple(str(part) for part in source)


def peaks(up: np.ndarray, down: np.ndarray,
          reaches: list[int]) -> dict[int, Reading]:
    """The match at each reach from the upright and rows-reversed surfaces,
    the stronger taken, as `measure_shift` takes it -- on a tie, upright."""
    out = {}
    for reach in reaches:
        u, d = peak_of(up, reach), peak_of(down, reach)
        flipped = d[2] > u[2]
        dy, dx, conf = d if flipped else u
        out[reach] = Reading(dy, dx, conf, flipped)
    return out


def readings(reference: np.ndarray, look: np.ndarray, reaches: list[int],
             smooth: float | None = MATCH_SMOOTH) -> dict[int, Reading]:
    """The match at each reach, both ways up, as `measure_shift` finds it
    -- from two surfaces rather than two per reach. :data:`WHOLE_SURFACE`
    among ``reaches`` is the whole of it. :func:`scored` gives each the
    confidence the gate judges."""
    return peaks(full_surface(reference, look, smooth),
                 full_surface(reference, look[::-1], smooth), reaches)


def scored(reading: Reading, reference: np.ndarray, look: np.ndarray,
           reach: int, smooth: float | None = MATCH_SMOOTH) -> Reading:
    """A reading found on the whole pictures, scored as the driver scores it:
    on the part both show, by `framing.score_shared` -- so ``confidence`` is
    `measure_shift`'s to the bit, and ``whole`` keeps what it was."""
    oriented = look[::-1] if reading.flipped else look
    shared = score_shared(reference, oriented, reading.dy, reading.dx, reach,
                          smooth)
    return reading._replace(confidence=shared.confidence,
                            whole=reading.confidence,
                            shared_px=(shared.dy, -shared.dx),
                            agreement=shared.agreement)


def recorded_reading(stored: dict, pictures: "Pictures", reference: tuple,
                     look: tuple, reach: int) -> Reading:
    """A look measured again as it was measured when it was recorded.

    A record that says ``scored_on: shared`` was judged on the part both
    pictures show, smoothed by what it names; one from before 2026-10-06 says
    nothing, and its ``confidence`` is the whole pictures', unsmoothed.
    Measuring an old look the new way would call every one of them changed.
    """
    if stored.get("scored_on") == "shared":
        smooth = stored.get("smooth")
        found = pictures.readings(reference, look, [reach], smooth)[reach]
        return scored(found, pictures(reference), pictures(look), reach,
                      smooth)
    return pictures.readings(reference, look, [reach], None)[reach]


def recorded_reach(stored: dict, width: int) -> int:
    """The reach a stored look was searched at: its own, or the records'."""
    return int(stored.get("reach_px") or reach_px(width, RECORDED_SEARCH_UNITS))


def reproduces(stored: dict, confidence: float, px: int) -> bool:
    """Whether a measurement is the one the hold recorded: confidence to the
    0.01 it was stored at, and the pixel."""
    return (stored.get("confidence") is not None
            and round(float(confidence), 2) == float(stored["confidence"])
            and stored.get("px") is not None and int(px) == int(stored["px"]))


def identify(hold: Hold, candidates: list[tuple], pictures: Pictures) -> bool:
    """Find the reference of a hold whose record does not name one.

    The candidates are the roll's walk prescans. The one the hold was held to
    reproduces what it recorded of its last look -- a z-score to 0.01 and a
    displacement to the pixel, which a wrong frame scores around 5 to 30 at a
    random lag -- so the first that does is it. Tried in the order most
    likely to be right first: the same number in the run's own labels.
    """
    last = next((look for look in hold.looks if look.kind in ("last", "only")),
                None)
    if last is None:
        return False
    now = pictures(last.source)
    if now is None:
        return False
    reach = recorded_reach(last.stored, now.shape[1])
    ordered = sorted(candidates, key=lambda c: (c[1] != hold.label_number,
                                                c[0][0] != "entry"))
    for source, number, *_rest in ordered:
        reference = pictures(source)
        if reference is None or reference.shape[:2] != now.shape[:2]:
            continue
        r = recorded_reading(last.stored, pictures, source, last.source, reach)
        if reproduces(last.stored, r.confidence, r.px):
            hold.reference, hold.origin = source, "identified"
            if hold.number is None:
                hold.number = number
            return True
    return False


def check_look(hold: Hold, look: Look, pictures: Pictures) -> dict:
    """Measure one look again and say whether it is the one recorded.

    Through `measure_shift` itself where the record was searched as the
    driver now searches, so what is checked is the driver's own code on the
    library's own pictures; and this tool's surface beside it, which must
    agree with it to the bit or nothing else here can be believed. A look
    recorded at another reach is checked at that reach against `register`
    itself, both ways up and the stronger taken as `measure_shift` takes
    it -- the driver's code at the reach the record was searched at, so the
    cross-check of the surface survives a change of `SEARCH_UNITS`. It was the
    surface alone, which after the reach went to 11.0 left every stored look
    unchecked: "0 agree, 0 differ".
    """
    reference, now = pictures(hold.reference), pictures(look.source)
    out = {"hold": hold.label, "kind": look.kind, "film": hold.film,
           "origin": hold.origin, "stored": look.stored}
    if reference is None or now is None:
        out["verdict"] = "missing"
        return out
    if reference.shape[:2] != now.shape[:2]:
        out["verdict"] = "resampled"
        return out
    width = now.shape[1]
    reach = recorded_reach(look.stored, width)
    fast = recorded_reading(look.stored, pictures, hold.reference, look.source,
                            reach)
    out.update(reach_px=reach, confidence=round(fast.confidence, 2), px=fast.px)
    shared = look.stored.get("scored_on") == "shared"
    if (shared and reach == reach_px(width)
            and look.stored.get("smooth") == MATCH_SMOOTH):
        _shift, detail = measure_shift(reference, now)
        out["by"] = "measure_shift"
        out["surface_agrees"] = (detail["confidence"] == round(fast.confidence, 2)
                                 and detail["px"] == fast.px
                                 and detail["dy"] == fast.dy)
        confidence, px = detail["confidence"], detail["px"]
    elif shared:
        m = match_against(reference, now, reach, look.stored.get("smooth"))
        out["by"] = f"match_against at {reach} px"
        out["surface_agrees"] = (round(m.confidence, 2) == round(fast.confidence, 2)
                                 and -m.dx == fast.px and m.dy == fast.dy)
        confidence, px = m.confidence, -m.dx
    else:
        # Recorded before 2026-10-06: the whole pictures, unsmoothed.
        upright = register(reference, now, max_shift=reach)
        flipped = register(reference, now[::-1], max_shift=reach)
        dy, dx, conf = flipped if flipped[2] > upright[2] else upright
        out["by"] = f"register at {reach} px"
        out["surface_agrees"] = (round(float(conf), 2) == round(fast.confidence, 2)
                                 and -int(dx) == fast.px and int(dy) == fast.dy)
        confidence, px = conf, -int(dx)
    out["verdict"] = ("reproduced" if reproduces(look.stored, confidence, px)
                      else "differs")
    return out


def find_references(holds: list[Hold], candidates: dict[str, list[tuple]],
                    pictures: Pictures) -> list[str]:
    """Find the reference of each hold here whose record names none, among
    its roll's walk prescans and then any walk prescan within a day of it --
    a roll held to a walk filed under another name. Returns the labels of
    the holds none of them reproduces."""
    missing = []
    for hold in holds:
        if hold.reference is not None:
            continue
        own = candidates.get(hold.roll, [])
        if identify(hold, own, pictures):
            continue
        tried = {_key(c[0]) for c in own}
        near = [c for c in candidates.get("", [])
                if _key(c[0]) not in tried and _near(c[2], hold.day)]
        if not identify(hold, near, pictures):
            missing.append(hold.label)
    return missing


def measure_holds(holds: list[Hold], candidates: dict[str, list[tuple]],
                  reaches: list[float], say=None) -> tuple[list, list, dict]:
    """Every pair, measured at every reach. Returns ``(pairs, checks, notes)``.

    A roll at a time, and its pictures dropped after it: the nulls never
    cross a roll, and a library's worth of prescans held at once is most of
    a gigabyte.
    """
    say = say or (lambda text: print(text, file=sys.stderr, flush=True))
    by_roll: dict[str, list[Hold]] = defaultdict(list)
    for hold in holds:
        by_roll[hold.roll].append(hold)
    pairs, checks = [], []
    notes = {"unidentified": [], "shapes": defaultdict(int), "resampled": 0}
    started = time.monotonic()
    for i, (roll, group) in enumerate(sorted(by_roll.items()), 1):
        pictures = Pictures()
        notes["unidentified"].extend(find_references(group, candidates,
                                                     pictures))
        group = [h for h in group if h.reference is not None]
        checked: dict[tuple, str] = {}
        for hold in group:
            for look in hold.looks:
                check = check_look(hold, look, pictures)
                checks.append(check)
                checked[(id(hold), look.kind)] = check["verdict"]
        done = set()
        for hold in group:
            reference = pictures(hold.reference)
            for other in group:
                own = other.reference == hold.reference
                for look in other.looks:
                    key = (tuple(map(str, hold.reference)),
                           tuple(map(str, look.source)))
                    if key in done:
                        continue
                    done.add(key)
                    # A hold's own look counts as a true pair only if it is
                    # the look it recorded: one that does not reproduce may be
                    # some other picture of the frame, and is left out of both.
                    if own and checked.get((id(other), look.kind)) != "reproduced":
                        continue
                    now = pictures(look.source)
                    if reference is None or now is None:
                        continue
                    if reference.shape[:2] != now.shape[:2]:
                        notes["resampled"] += 1
                        continue
                    notes["shapes"][now.shape[:2]] += 1
                    pairs.append(_pair(hold, other, look, pictures,
                                       reaches, own))
        say(f"  [{i}/{len(by_roll)}] {roll}: {len(group)} holds, "
            f"{len(pairs)} pairs so far, {time.monotonic() - started:.0f} s")
    return pairs, checks, notes


def _pair(hold: Hold, other: Hold, look: Look, pictures: Pictures,
          reaches: list[float], own: bool) -> dict:
    reference, now = pictures(hold.reference), pictures(look.source)
    width, rows = now.shape[1], now.shape[0]
    columns = {reach: reach_px(width, reach) for reach in reaches}
    found = pictures.readings(hold.reference, look.source,
                              sorted(set(columns.values())) + [WHOLE_SURFACE])
    whole = found[WHOLE_SURFACE]
    # Each reach's peak, scored on the part both pictures show: what the
    # gate judges. The whole surface's stays as found -- the arbiter wants
    # where the peak is, not how a gate would score it.
    at = {reach: scored(found[px], reference, now, px)
          for reach, px in columns.items()}
    oriented = now[::-1] if whole.flipped else now
    return {
        "roll": hold.roll, "film": hold.film, "reference": hold.label,
        "look": other.label, "kind": look.kind,
        "pairing": "true" if own else "null",
        "rows": rows, "width": width,
        "whole": whole,
        # The whole shift taken out, rows too -- see `aligned_correlation`.
        "arbiter": aligned_correlation(reference, oriented, whole.dx,
                                       whole.dy),
        "at": at,
        "reach_px": columns,
    }


def _fmt(values: list[float], spec: str = ".1f") -> str:
    return "-" if not values else format(max(values), spec)


def say_reach(reach: float, columns=None) -> str:
    """A reach for a person: in units and columns, never millimetres."""
    out = f"{reach:.1f} units"
    if columns:
        out += " = " + ", ".join(map(str, columns)) + " px"
    return out


def report_holds(pairs: list[dict], checks: list[dict], notes: dict,
                 reaches: list[float], gate: Gate) -> dict:
    """Print the check, then per film and reach what a gate would have kept."""
    summary: dict = {"checks": {}, "films": {}}
    print("\n-- the stored looks, measured again " + "-" * 39)
    for kinds, name, slot in ((("first", "only"), "first looks", "history[0]"),
                              (("last",), "last looks after a move",
                               "history[-1]")):
        for origin in ("named", "identified"):
            rows = [c for c in checks if c["kind"] in kinds
                    and c["origin"] == origin]
            if not rows:
                continue
            good = sum(1 for c in rows if c["verdict"] == "reproduced")
            by = sorted({c.get("by", "-") for c in rows})
            how = ("reference named by the hold" if origin != "identified"
                   else "reference found by reproducing the last look -- "
                        "how the pair was found, so not a check")
            print(f"  {name:24s} {good:4d} of {len(rows):4d} reproduce "
                  f"{slot}  ({how}; {', '.join(by)})")
            summary["checks"][f"{name} / {origin}"] = [good, len(rows)]
            for c in rows:
                if c["verdict"] != "reproduced":
                    stored = c["stored"]
                    print(f"      {c['hold']:<26} {c['kind']:5s} {c['verdict']}: "
                          f"stored {stored.get('confidence')} at "
                          f"{stored.get('px')} px, now {c.get('confidence')} "
                          f"at {c.get('px')} px")
    disagree = [c for c in checks if c.get("surface_agrees") is False]
    print(f"  this tool's surface against the driver's own code "
          f"(measure_shift, or register at a look's own reach): "
          f"{sum(1 for c in checks if c.get('surface_agrees'))} agree, "
          f"{len(disagree)} differ")
    summary["surface_disagrees"] = len(disagree)
    if notes["unidentified"]:
        print(f"  {len(notes['unidentified'])} hold(s) named no reference and "
              f"none of their roll's prescans reproduces them -- left out: "
              f"{', '.join(notes['unidentified'][:8])}"
              + (" ..." if len(notes["unidentified"]) > 8 else ""))
    if notes["resampled"]:
        print(f"  {notes['resampled']} pair(s) of different sizes left out: "
              f"the driver would resample them, at half the confidence")
    shapes = ", ".join(f"{h}x{w} ({n})" for (h, w), n in
                       sorted(notes["shapes"].items()))
    print(f"  looks measured: {shapes or 'none'}")

    odd_true = [p for p in pairs if p["pairing"] == "true"
                and p["arbiter"] < SAME_PICTURE]
    print(f"\n  {len(pairs)} pairs, labelled by the arbiter at the whole "
          f"surface's peak with the whole shift taken out, not by the pairing")
    for p in odd_true:
        print(f"    a hold's own look is not its reference's picture: "
              f"{p['look']} {p['kind']} (arbiter {p['arbiter']:.3f}, "
              f"{p['whole'].confidence:.1f} at {p['whole'].px:+d} px) -- "
              f"counted with the nulls, the wrong frame in the aperture")

    films = sorted({p["film"] for p in pairs},
                   key=lambda f: (["negative", "bw", "positive"].index(f)
                                  if f in ("negative", "bw", "positive") else 9, f))
    for film in films + ["all films"]:
        chosen = (pairs if film == "all films"
                  else [p for p in pairs if p["film"] == film])
        summary["films"][film] = _report_film(film, chosen, reaches, gate)
    return summary


def _report_film(film: str, pairs: list[dict], reaches: list[float],
                 gate: Gate) -> dict:
    true = [p for p in pairs if p["pairing"] == "true"
            and p["arbiter"] >= SAME_PICTURE]
    nulls = [p for p in pairs if p["arbiter"] < SAME_PICTURE]
    # Another frame's look the arbiter calls this reference's picture: a
    # rescan under another number, or two frames alike enough to fool a
    # correlation of the whole picture. Counted as neither -- and shown, so
    # that leaving them out is seen to hide nothing.
    alike = [p for p in pairs if p["pairing"] == "null"
             and p["arbiter"] >= SAME_PICTURE]
    kinds: dict[str, int] = defaultdict(int)
    for p in true:
        kinds[p["kind"]] += 1
    out: dict = {"true": len(true), "nulls": len(nulls), "alike": len(alike),
                 "reaches": {}}
    print(f"\n== {film}: {len(true)} true looks ("
          + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
          + f"), {len(nulls)} nulls, {len(alike)} other frames' looks the "
          f"arbiter calls the same picture")
    own = [p["arbiter"] for p in pairs if p["pairing"] == "true"]
    other = [p["arbiter"] for p in pairs if p["pairing"] == "null"]
    if own and other:
        near = sum(1 for v in own + other
                   if abs(v - SAME_PICTURE) < ARBITER_MARGIN)
        print(f"   arbiter: a hold's own looks {min(own):.3f} .. "
              f"{max(own):.3f}; other frames' {min(other):.3f} .. "
              f"{max(other):.3f}; within {ARBITER_MARGIN} of the "
              f"{SAME_PICTURE} cut: {near}")
    if true:
        widest = max(true, key=lambda p: abs(p["whole"].px))
        far = abs(widest["whole"].px)
        print(f"   largest true shift {far} px "
              f"({far * APERTURE_UNITS / widest['width']:.1f} "
              f"units, {widest['look']} {widest['kind']}); past the reach at "
              + ", ".join(f"{reach:.1f} units: " + str(sum(
                  1 for p in true if abs(p["whole"].px) > p["reach_px"][reach]))
                  for reach in reaches))
    for reach in reaches:
        out["reaches"][round(reach, 2)] = _report_reach(reach, true, nulls,
                                                        alike, gate)
    return out


def _report_reach(reach: float, true: list[dict], nulls: list[dict],
                  alike: list[dict], gate: Gate) -> dict:
    def found(p):
        r, w = p["at"][reach], p["whole"]
        return (r.dx, r.dy, r.flipped) == (w.dx, w.dy, w.flipped)

    hits = [p for p in true if found(p)]
    lost = [p for p in true if not found(p)]
    columns = sorted({p["reach_px"][reach] for p in true + nulls + alike})
    print(f"\n   reach {say_reach(reach, columns)}")
    print(f"     true matches found {len(hits)} of {len(true)}; lost (true "
          f"shift past the reach, the peak found is noise) {len(lost)}")
    out: dict = {"reach_px": columns, "found": len(hits), "lost": len(lost),
                 "bins": {}, "floors": {}}
    for lo, hi in SHIFT_BINS:
        inside = [p["at"][reach].confidence for p in hits
                  if abs(p["at"][reach].px) >= lo and (hi is None
                                                    or abs(p["at"][reach].px) < hi)]
        name = f"{lo}-{hi} px" if hi is not None else f">={lo} px"
        if inside:
            print(f"     |shift| {name:9s} n={len(inside):4d}  confidence min "
                  f"{min(inside):6.1f}  median {float(np.median(inside)):6.1f}")
        else:
            print(f"     |shift| {name:9s} n=   0")
        out["bins"][name] = ([len(inside), round(min(inside), 2),
                              round(float(np.median(inside)), 2)]
                             if inside else [0, None, None])
    dys = [abs(p["at"][reach].dy) for p in hits]
    print(f"     largest |dy| of a true match: "
          f"{max(dys) if dys else '-'} rows")
    out["max_dy"] = max(dys) if dys else None

    def worst(rows, dy=None, borne=False):
        return [p["at"][reach].confidence for p in rows
                if (dy is None or gate.on_axis(p["at"][reach].dy, p["rows"], dy))
                and (not borne or gate.bears_out(p["at"][reach].agreement,
                                                 p["at"][reach].shared_px))]

    for name, rows in (("worst null", nulls), ("worst lost peak", lost),
                       ("worst look the arbiter set aside", alike)):
        if rows or name == "worst null":
            print(f"     {name} {_fmt(worst(rows))};  with " + "   ".join(
                f"|dy|<={d}: {_fmt(worst(rows, d))}" for d in HOLD_DY_ROWS))
    # What the floor has to clear: the nulls the other tests let through.
    # The rest -- a pattern both frames hold, the same painting photographed
    # twice -- the agreement test refuses whatever they score.
    print(f"     worst null the shared part bears out "
          f"(agreement >= {gate.agreement:g}) {_fmt(worst(nulls, None, True))};"
          "  with " + "   ".join(
              f"|dy|<={d}: {_fmt(worst(nulls, d, True))}"
              for d in HOLD_DY_ROWS))
    out["worst_null"] = {str(d): (max(worst(nulls, d)) if worst(nulls, d)
                                  else None) for d in (None, *HOLD_DY_ROWS)}
    out["worst_borne_null"] = {
        str(d): (max(worst(nulls, d, True)) if worst(nulls, d, True)
                 else None) for d in (None, *HOLD_DY_ROWS)}
    out["worst_alike"] = max(worst(alike)) if alike else None
    # Which pairs decide it: the strongest nulls any tabulated gate would
    # see, and the weakest true looks a floor would refuse first.
    widest = max(HOLD_DY_ROWS)
    strongest = sorted((p for p in nulls if gate.on_axis(
        p["at"][reach].dy, p["rows"], widest)),
        key=lambda p: -p["at"][reach].confidence)[:3]
    for p in strongest:
        r = p["at"][reach]
        print(f"       null {r.confidence:6.1f} at {r.px:+4d} px, dy {r.dy:+d}: "
              f"{p['reference']} against {p['look']} {p['kind']} "
              f"(arbiter {p['arbiter']:.2f}"
              + (f", agreement {r.agreement:.2f}" if r.agreement is not None
                 else "") + ")")
    for p in sorted(hits, key=lambda p: p["at"][reach].confidence)[:3]:
        r = p["at"][reach]
        print(f"       true {r.confidence:6.1f} at {r.px:+4d} px, dy {r.dy:+d}: "
              f"{p['look']} {p['kind']}")

    floors = sorted(set(HOLD_FLOORS) | {gate.floor})
    gates = sorted(set(HOLD_DY_ROWS) | {gate.max_dy})
    print("     " + f"{'floor':>6}" + "".join(
        f"  {'kept |dy|<=' + str(d):>14} {'nulls':>5}" for d in gates))
    for floor in floors:
        cells = []
        for d in gates:
            def passing(rows, floor=floor, d=d):
                return sum(1 for p in rows if p["at"][reach].confidence >= floor
                           and gate.on_axis(p["at"][reach].dy, p["rows"], d)
                           and gate.bears_out(p["at"][reach].agreement,
                                              p["at"][reach].shared_px))
            kept, wrong, astray = passing(hits), passing(nulls), passing(lost)
            cells.append(f"  {f'{kept}/{len(true)}':>14} "
                         f"{str(wrong + astray) + ('*' if astray else ''):>5}")
            out["floors"][f"{floor:g}/{d}"] = [kept, len(true), wrong, astray]
        print("     " + f"{floor:>6g}" + "".join(cells)
              + ("   <- candidate" if floor == gate.floor else ""))
    if any(v[3] for v in out["floors"].values()):
        print("     * includes a lost true pair's noise peak passing -- a "
              "move to the wrong place, as a null passing is")
    return out


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rolls", type=Path, nargs="*",
                    help="folders of prescans; default every roll plus the library")
    ap.add_argument("--dpi", type=int, default=300,
                    help="resolution the prescans were taken at (default 300)")
    ap.add_argument("--self-similar", action="store_true",
                    help="the grass/sky question instead of the separation")
    ap.add_argument("--holds", action="store_true",
                    help="re-measure every stored hold against its own "
                         "reference and the other frames of its roll")
    ap.add_argument("--library", type=Path, default=Path("library"),
                    help="the library to read (default ./library)")
    ap.add_argument("--rolls-root", type=Path, default=Path("rolls"),
                    help="the folder of roll folders (default ./rolls)")
    ap.add_argument("--search-units", type=float, default=None,
                    help=f"the reach to judge, in units "
                         f"(default the driver's {SEARCH_UNITS:.1f}; --holds "
                         f"compares "
                         f"{', '.join(f'{r:.1f}' for r in HOLD_REACHES_UNITS)})")
    # The millimetre figure the reaches were chosen and recorded as, read and
    # converted: what the docs and older notes give. Never printed back.
    ap.add_argument("--search-mm", type=float, default=None,
                    help="the same reach as the millimetre figure the "
                         "constant was once written as, converted to units")
    ap.add_argument("--max-dy", type=int, default=None,
                    help=f"the off-axis gate, in rows of a 300 dpi prescan "
                         f"(default the driver's {MAX_DY_PX})")
    ap.add_argument("--floor", type=float, default=None,
                    help=f"the confidence floor (default the driver's "
                         f"{CONFIDENCE_FLOOR:g})")
    ap.add_argument("--agreement", type=float, default=None,
                    help=f"how alike the shared part must be (default the "
                         f"driver's {AGREEMENT_FLOOR:g})")
    ap.add_argument("--json", type=Path, default=None,
                    help="--holds: also write every pair and check here")
    args = ap.parse_args(argv)
    if args.search_mm is not None:
        if args.search_units is not None:
            ap.error("give --search-units or --search-mm, not both")
        args.search_units = to_units(args.search_mm)

    gate = Gate(search_units=(SEARCH_UNITS if args.search_units is None
                              else args.search_units),
                max_dy=MAX_DY_PX if args.max_dy is None else args.max_dy,
                floor=CONFIDENCE_FLOOR if args.floor is None else args.floor,
                agreement=(AGREEMENT_FLOOR if args.agreement is None
                           else args.agreement))
    print(f"gate: {gate.describe()}")

    if args.holds:
        return run_holds(args, gate)

    folders = args.rolls
    if not folders:
        # Each strip of a roll of strips as well as each roll from before
        # strips: a roll of strips' own folder holds no prescans.
        folders = list(library.roll_folders(args.rolls_root, trash=False))
        if args.library.is_dir():
            folders.append(args.library)
    if not folders:
        print("no rolls and no library -- nothing to measure", file=sys.stderr)
        return 2

    problems = 0
    pool: dict[str, list[float]] = {}
    for folder in folders:
        images = cohort(folder, args.dpi)
        if len(images) < 2:
            continue
        print(f"\n{folder}")
        problems += (self_similar(images, args.dpi, gate) if args.self_similar
                     else separation(images, args.dpi, pool, gate))

    print()
    same, differ = pool.get("same", []), pool.get("differ", [])
    if same and differ:
        print(f"Over every roll: {len(differ)} pairs that are not the same "
              f"picture reach {max(differ):.1f} at worst; {len(same)} that are "
              f"start at {min(same):.1f}.")
        print(f"A floor of {gate.floor:g} clears the worst null by "
              f"{gate.floor - max(differ):.1f} and sits "
              f"{min(same) - gate.floor:.1f} below the weakest true "
              f"match.")
        print("The first number is the one that matters: a false positive "
              "moves the film,\nwhile a refusal only leaves the frame "
              "unheld and flagged.")
        print()
    if problems:
        print(f"{problems} pair(s) would move the film on a match that is not "
              f"real. The floor no longer separates; re-fit it before trusting "
              f"a hold.")
        return 1
    print(f"No confident match on a pair that is not the same picture. "
          f"A floor of {gate.floor:g} holds"
          + ("." if gate.is_drivers else " -- for the candidate above."))
    return 0


def run_holds(args, gate: Gate) -> int:
    """`--holds`: collect, check, measure, report."""
    if not args.library.is_dir():
        print(f"no library at {args.library}", file=sys.stderr)
        return 2
    reaches = ([args.search_units] if args.search_units is not None
               else list(HOLD_REACHES_UNITS))
    started = time.monotonic()
    holds, candidates = stored_holds(args.library, args.rolls_root)
    named = sum(1 for h in holds if h.reference is not None)
    kept = sum(1 for h in holds if h.from_manifest)
    print(f"\n{len(holds)} stored holds with a look on disk"
          + (f" ({kept} kept only by roll.json)" if kept else "")
          + f": {named} name their reference, {len(holds) - named} are matched "
          f"to their roll's walk prescans by what they recorded")
    firsts = sum(1 for h in holds for look in h.looks if look.kind == "first")
    moved = sum(1 for h in holds if h.record.get("moves"))
    print(f"  {moved} moved; {firsts} of those have their arrival picture "
          f"(filed tagged 'before'), the rest only where they were left")
    pairs, checks, notes = measure_holds(holds, candidates, reaches)
    summary = report_holds(pairs, checks, notes, reaches, gate)
    print(f"\n({time.monotonic() - started:.0f} s)")
    if args.json:
        write_holds_json(args.json, gate, reaches, pairs, checks, summary)
        print(f"wrote {args.json}")
    return 0


def write_holds_json(path: Path, gate: Gate, reaches: list[float],
                     pairs: list[dict], checks: list[dict],
                     summary: dict) -> None:
    """`--holds --json`: every pair and check, each reading labelled.

    A reading goes through :meth:`Reading.labelled` while the list is built.
    A `default=` hook cannot do it: `json` encodes a named tuple as a plain
    list without consulting the hook, so the conversion written for it there
    never ran.
    """
    def plain(value):
        if isinstance(value, (np.integer, np.floating)):
            return value.item()
        return str(value)

    path.write_text(json.dumps({
        "gate": gate.describe(),
        "reaches_units": [round(r, 2) for r in reaches],
        "readings": "dy in rows, px in columns: the picture's displacement "
                    "against its reference, as every report prints it "
                    "(measure_shift's px, the negative of register's dx)",
        "pairs": [dict(p, whole=p["whole"].labelled(),
                       at={f"{k:.2f}": v.labelled()
                           for k, v in p["at"].items()},
                       reach_px={f"{k:.2f}": v
                                 for k, v in p["reach_px"].items()})
                  for p in pairs],
        "checks": checks, "summary": summary}, default=plain, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
