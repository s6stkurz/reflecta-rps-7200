#!/usr/bin/env python3
"""Does `CONFIDENCE_FLOOR` still sit between the two clusters? Measured.

    uv run python tools/registration_margin.py
    uv run python tools/registration_margin.py --self-similar
    uv run python tools/registration_margin.py --rolls rolls/2026-09-14

The evidence behind `docs/registration-confidence-plan.md`. `rolls/` and
`library/` are gitignored, so the numbers in that document cannot be committed
alongside it -- this is how they are re-derived, on whatever film is at hand.

Run it after any change to `register`, to the prescan resolution, or to
`SEARCH_MM`. Each of those moves the scale: confidence is a z-score over the
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

What the two modes answer:

  default          does the floor separate same-picture from different-picture
  --self-similar   are grass/sky frames the dangerous case  (they are not)
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import library, tiff
from rps7200.console import use_utf8_stdout
from rps7200.framing import CONFIDENCE_FLOOR, MAX_DY_PX, SEARCH_MM
from rps7200.protocol import MM_PER_INCH
from rps7200.uniformity import luminance, phase_surface, register

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


def reach_px(dpi: int) -> int:
    """`SEARCH_MM` in pixels at this resolution -- what `register` is given."""
    return int(round(SEARCH_MM / (MM_PER_INCH / dpi)))


def surface(a: np.ndarray, b: np.ndarray, reach: int) -> np.ndarray:
    """`register`'s searched window, which it computes and discards.

    `register`'s signature has a dozen callers, so it does not return the
    surface; both compute it with `uniformity.phase_surface`, which is what
    keeps this window identical to the one `register` searched.
    """
    fa, fb = luminance(a), luminance(b)
    h = min(fa.shape[0], fb.shape[0])
    w = min(fa.shape[1], fb.shape[1])
    surf = phase_surface(fa[:h, :w], fb[:h, :w])
    ry, rx = min(reach, h // 2), min(reach, w // 2)
    ys = [dy % h for dy in range(-ry, ry + 1)]
    xs = [dx % w for dx in range(-rx, rx + 1)]
    return surf[np.ix_(ys, xs)]


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


def aligned_correlation(a: np.ndarray, b: np.ndarray, dx: int) -> float:
    """How alike the pair is once the found shift is taken out.

    Cropped well inside the frame: `np.roll` wraps, and the wrapped strip is
    unrelated content that would drag the correlation down on a true match.

    On luminance, as `register` itself is -- an RGB pass and an RGBI pass of
    the same frame are both in the library at the same resolution, and
    comparing them channel-for-channel raises `ValueError` on the fourth.
    """
    a, b = luminance(a), luminance(b)
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    shifted = np.roll(b, dx, axis=1)
    margin = max(abs(dx) + 8, w // 10)
    core = (slice(h // 10, -(h // 10) or None), slice(margin, -margin))
    x, y = a[core].ravel(), shifted[core].ravel()
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def cohort(folder: Path, dpi: int) -> list[tuple[str, np.ndarray]]:
    """Every comparable frame in one folder, which is one pass over one strip.

    Compared only within a folder, never across: `rolls/*/prescanNN.tif` are
    written rotated for the contact sheet and library scans are not, so mixing
    them would compare a frame against a turned copy of itself.

    A library folder is filtered to one resolution as well. `register` crops a
    mismatched pair to their common size, which for two resolutions of the same
    frame is not the same piece of film -- it would answer confidently and
    wrongly, and the whole point here is to catch exactly that.
    """
    out = []
    for path in sorted(folder.glob("prescan*.tif")):
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
            image, _ = library.corrected(record.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        out.append((record.parent.name, image.astype(np.float64)))
    return out


def separation(images: list[tuple[str, np.ndarray]], dpi: int,
               pool: dict[str, list[float]] | None = None) -> int:
    """Register every pair, sort by the arbiter, report where the floor falls.

    `pool` collects both clusters across cohorts. One strip's worst null is not
    the worst null -- 2026-09-14 alone tops out at 11.4 and 2026-09-11 reaches
    30.2 -- so the figure worth quoting is the one over every roll at hand.
    """
    reach = reach_px(dpi)
    same: list[tuple] = []
    differ: list[tuple] = []
    unsure: list[tuple] = []
    for (na, a), (nb, b) in itertools.combinations(images, 2):
        dy, dx, conf = register(a, b, max_shift=reach)
        corr = aligned_correlation(a, b, dx)
        row = (na, nb, dy, dx, conf, corr)
        (same if corr >= SAME_PICTURE else differ).append(row)
        if abs(corr - SAME_PICTURE) < ARBITER_MARGIN:
            unsure.append(row)

    # `unsure` overlaps both, so it is not part of the total.
    print(f"  {len(images)} frames, {len(same) + len(differ)} pairs, "
          f"reach {reach} px ({SEARCH_MM} mm at {dpi} dpi)")
    for label, rows in (("same picture", same), ("different picture", differ)):
        if not rows:
            continue
        c = np.array([r[4] for r in rows])
        print(f"    {label:18s} n={len(rows):4d}  confidence "
              f"{c.min():7.1f} .. {c.max():7.1f}   median {np.median(c):7.1f}")
    for na, nb, _dy, _dx, conf, corr in unsure:
        print(f"    ?? the arbiter is unsure about {na} vs {nb}: correlates "
              f"{corr:.4f}, within {ARBITER_MARGIN} of the {SAME_PICTURE} cut. "
              f"Nothing did that on the film this was fitted on -- look at the "
              f"pair before believing its verdict either way.")

    problems = 0
    # The only failure that matters. Everything else degrades safely: a match
    # below the floor is refused, the film is not moved, the frame is scanned
    # and flagged. A confident match on a different picture would move it.
    for na, nb, dy, dx, conf, corr in differ:
        if conf >= CONFIDENCE_FLOOR and abs(dy) <= MAX_DY_PX:
            problems += 1
            print(f"    !! FALSE POSITIVE {na} vs {nb}: confidence {conf:.1f} "
                  f"above the floor, but the pair correlates at only {corr:.4f}")
    for na, nb, dy, dx, conf, corr in same:
        if conf < CONFIDENCE_FLOOR:
            print(f"    -- missed {na} vs {nb}: correlates {corr:.4f} but scores "
                  f"{conf:.1f}; refused, so this frame is scanned unmoved")

    if pool is not None:
        pool.setdefault("same", []).extend(r[4] for r in same)
        pool.setdefault("differ", []).extend(r[4] for r in differ)

    if same and differ:
        gap_lo = max(r[4] for r in differ)
        gap_hi = min(r[4] for r in same)
        where = "inside" if gap_lo < CONFIDENCE_FLOOR < gap_hi else "OUTSIDE"
        print(f"    gap {gap_lo:.1f} .. {gap_hi:.1f}; floor {CONFIDENCE_FLOOR} "
              f"falls {where} it")
        if same:
            margins = [rival_margin(a, b, reach)
                       for (na, nb, *_), (a, b) in
                       ((r, (dict(images)[r[0]], dict(images)[r[1]])) for r in same)]
            print(f"    true peak beats its best rival by "
                  f"{min(margins):.0f}x .. {max(margins):.0f}x")
    return problems


def self_similar(images: list[tuple[str, np.ndarray]], dpi: int) -> int:
    """Shift real frames by known amounts; does texture ever win over truth?

    The grass-and-sky question. A second pass is *synthesised* here -- the
    frame displaced, plus a real pass-to-pass residual measured from a genuine
    repeat pair in the same cohort. Synthesising the residual instead gets this
    wrong twice over: rolling a frame rolls its noise too, which matches
    perfectly and scores everything alike, and Gaussian noise added to
    synthetic content collapses everything to nothing. The content must be real
    film and the residual must be real, or the answer is an artefact.
    """
    reach = reach_px(dpi)
    residual, donors = None, set()
    for (na, a), (nb, b) in itertools.combinations(images, 2):
        _, dx, conf = register(a, b, max_shift=reach)
        if conf >= CONFIDENCE_FLOOR and aligned_correlation(a, b, dx) >= SAME_PICTURE:
            h = min(a.shape[0], b.shape[0])
            w = min(a.shape[1], b.shape[1])
            residual = a[:h, :w] - np.roll(b, dx, axis=1)[:h, :w]
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
            # Content displacement is -dx; see `measure_shift_mm`.
            _, dx, conf = register(frame, np.roll(frame, shift, axis=1) + residual,
                                   max_shift=reach)
            ok = dx == -shift
            cells.append(f"{conf:6.0f}{'' if ok else '!'}")
            if not ok and conf >= CONFIDENCE_FLOOR:
                wrong += 1
        print(f"  {name:>22s} {detail:7.2f}  " + "  ".join(cells))
    print("\n  In ascending detail, so confidence should RISE down the table:"
          "\n  textured frames are the best case for phase correlation, not the"
          "\n  worst. A '!' marks a recovered lag that was not the one applied.")
    return wrong


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
    args = ap.parse_args(argv)

    folders = args.rolls
    if not folders:
        folders = [p for p in sorted(Path("rolls").glob("*")) if p.is_dir()]
        if Path("library").is_dir():
            folders.append(Path("library"))
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
        problems += (self_similar(images, args.dpi) if args.self_similar
                     else separation(images, args.dpi, pool))

    print()
    same, differ = pool.get("same", []), pool.get("differ", [])
    if same and differ:
        print(f"Over every roll: {len(differ)} pairs that are not the same "
              f"picture reach {max(differ):.1f} at worst; {len(same)} that are "
              f"start at {min(same):.1f}.")
        print(f"CONFIDENCE_FLOOR = {CONFIDENCE_FLOOR} clears the worst null by "
              f"{CONFIDENCE_FLOOR - max(differ):.1f} and sits "
              f"{min(same) - CONFIDENCE_FLOOR:.1f} below the weakest true "
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
          f"CONFIDENCE_FLOOR = {CONFIDENCE_FLOOR} holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
