#!/usr/bin/env python3
"""Does `film_bounds` find the film's edge, and would a different rule do better?

    python3 tools/film_edge_study.py
    python3 tools/film_edge_study.py --json study.json

**No scanner needed.** Everything is re-read from stored prescans, so this
re-runs whenever the rule changes, and the numbers move with it.

`film_bounds` (`rps7200/framing.py`) finds the film's edge by level: film
attenuates light and an empty transport aperture does not, so the edge is a
step in brightness. It cuts at a fixed fraction of the "clear" level, gated by
a `CLEAR_RATIO` test that abstains -- returning the whole window -- when no
aperture appears to be in view.

`TODO.md` proposed replacing the fixed fraction with Otsu. This measures
whether that is worth doing, and the first thing it reports is why the
question is not the one it looks like: **the fraction almost never fires**,
because the gate short-circuits first on nearly every real prescan. Whatever
decides abstention is the load-bearing part.

What this CANNOT tell you:

* **Whether an edge it found is the right one**, on a real prescan. There is no
  ground truth in the corpus. Tier A positives below carry a known answer by
  construction; everything else needs a person to look. That is what
  `--render` is for.
* **Anything about empty aperture.** Every bright band in the corpus is clear
  C-41 *film base* -- strongly orange, R:B 3-7 -- and not the neutral empty
  aperture (R:B ~0.93) the `film_bounds` docstring is calibrated to. The two
  are different physical objects and the corpus contains only one of them.
* **Whether cropping helps metering on a frame with real aperture in it.** The
  5.6-10.0% figure that motivated `metering_region` was measured against
  aperture; against clear base the same crop moves the percentile by ~0.0%.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import tiff                                          # noqa: E402
from rps7200.framing import (                                     # noqa: E402
    CLEAR_RATIO,
    FILM_LEVEL,
    FULL_FRAME,
    MM_PER_INCH,
    gap_edges,
)
from rps7200.protocol import COORD_PER_INCH                       # noqa: E402

#: The percentile `film_bounds` calls "clear". Not the maximum, deliberately:
#: a threshold set as a fraction of the maximum is set by its worst outlier,
#: which is the lesson `docs/whole-roll-plan.md` records from the detector this
#: one replaced.
CLEAR_PERCENTILE = 98.0

#: Where metering reads. Matches `auto_exposure`, so a delta here means what it
#: means there.
METER_PERCENTILE = 99.5

#: A rule that leaves a real negative holding less than this much of the window
#: is cropping a frame where the film fills it, which is the one thing no rule
#: may do. Disqualifying, not a score.
MIN_SAFE_RETAINED = 0.90


# --- the profile, and the rules that cut it ------------------------------


def profiles(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The two 1-D mean profiles `film_bounds` works on: (columns, rows)."""
    grey = image.astype(np.float64)
    if grey.ndim == 3:
        grey = grey.mean(axis=2)
    return grey.mean(axis=0), grey.mean(axis=1)


def clear_ratio(profile: np.ndarray) -> float:
    """How much brighter the clear level is than the median. The gate's input."""
    med = float(np.median(profile))
    if med <= 0:
        return 0.0
    return float(np.percentile(profile, CLEAR_PERCENTILE)) / med


def cut_fixed(profile: np.ndarray) -> float:
    """What ships: a fixed fraction of the clear level."""
    return float(np.percentile(profile, CLEAR_PERCENTILE)) * FILM_LEVEL


def cut_otsu(profile: np.ndarray, bins: int = 256) -> float:
    """Otsu's threshold on the 1-D profile.

    Hand-written: `pyproject.toml` declares exactly numpy, and
    `tests/test_optional_libraries.py` requires `rps7200.framing` to import
    with nothing else present.

    Maximises between-class variance over a histogram of the profile. Note it
    always returns *a* cut -- it has no notion of "these are one population",
    which is exactly what the gate provides and Otsu does not.
    """
    lo, hi = float(profile.min()), float(profile.max())
    if hi <= lo:
        return hi
    counts, edges = np.histogram(profile, bins=bins, range=(lo, hi))
    centres = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(counts)
    w1 = w0[-1] - w0
    total = np.cumsum(counts * centres)
    with np.errstate(invalid="ignore", divide="ignore"):
        m0 = total / np.where(w0 > 0, w0, 1)
        m1 = (total[-1] - total) / np.where(w1 > 0, w1, 1)
        between = w0 * w1 * (m0 - m1) ** 2
    between[(w0 == 0) | (w1 == 0)] = -1.0
    return float(centres[int(np.argmax(between))])


def cut_ratio_gap(profile: np.ndarray) -> float:
    """The widest *multiplicative* gap between sorted samples.

    The in-repo idiom, from `calculate_shading` in `rps7200/shading.py`, which
    splits calibration lines into dark and light the same way. Scale-free and
    count-free -- it never assumes what proportion of the profile is film,
    which is the property TODO.md actually asks for. Its weakness against Otsu
    is that one outlier can open the widest gap on its own.
    """
    ranked = np.sort(profile[profile > 0])
    if ranked.size < 3:
        return float(profile.max())
    gaps = ranked[1:] / np.maximum(ranked[:-1], 1e-9)
    return float(ranked[int(np.argmax(gaps))])


CUTS: dict[str, Callable[[np.ndarray], float]] = {
    "fixed": cut_fixed,
    "otsu": cut_otsu,
    "ratio_gap": cut_ratio_gap,
}


# --- morphological opening on the covered mask ---------------------------


def open_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """Erode then dilate a 1-D boolean mask: drop runs shorter than `radius`.

    The shift-AND / shift-OR idiom `dilate_defects` uses in
    `rps7200/defects.py`, written here rather than imported so framing does not
    depend on defect correction for eight lines.

    Why it matters: `film_bounds` takes `covered[0]` and `covered[-1]` from a
    raw mask, so one stray dark sample out in the clear region sets the edge
    outright.
    """
    if radius <= 0 or not mask.any():
        return mask

    def erode(m: np.ndarray) -> np.ndarray:
        out = m.copy()
        for shift in range(1, radius + 1):
            out[shift:] &= m[:-shift]
            out[:-shift] &= m[shift:]
        return out

    def dilate(m: np.ndarray) -> np.ndarray:
        out = m.copy()
        for shift in range(1, radius + 1):
            out[shift:] |= m[:-shift]
            out[:-shift] |= m[shift:]
        return out

    return dilate(erode(mask))


@dataclass(frozen=True)
class Rule:
    """One way of deciding where the film is, along a single axis."""

    name: str
    cut: str = "fixed"
    gate: float | None = CLEAR_RATIO      # None = no abstention at all
    opening: int = 0

    def span(self, profile: np.ndarray) -> tuple[int, int] | None:
        """First and last covered sample, or None for "no edge -- whole window"."""
        n = profile.size
        if n == 0:
            return None
        if self.gate is not None and clear_ratio(profile) < self.gate:
            return None
        covered = profile < CUTS[self.cut](profile)
        if self.opening:
            covered = open_mask(covered, self.opening)
        idx = np.flatnonzero(covered)
        if idx.size == 0:
            return None
        return int(idx[0]), int(idx[-1])


#: The rules compared. `fixed_gated` is what ships.
RULES = [
    Rule("fixed_gated"),
    Rule("fixed_gated_open3", opening=3),
    Rule("fixed_ungated", gate=None),
    Rule("otsu_gated", cut="otsu"),
    Rule("otsu_gated_open3", cut="otsu", opening=3),
    Rule("otsu_ungated", cut="otsu", gate=None),
    Rule("ratio_gap_gated", cut="ratio_gap"),
    Rule("ratio_gap_ungated", cut="ratio_gap", gate=None),
]


# --- the corpus ----------------------------------------------------------


@dataclass
class Frame:
    path: str
    image: np.ndarray
    kind: str                              # prescan | roll | probe
    notes: dict[str, Any] = field(default_factory=dict)


def load_corpus(root: Path) -> list[Frame]:
    """Every prescan-like image on disk, read back from the file itself."""
    out: list[Frame] = []
    for p in sorted(glob.glob(str(root / "library" / "*" / "prescan.tif"))):
        out.append(Frame(p, tiff.read(p), "prescan"))
    for p in sorted(glob.glob(str(root / "rolls" / "*" / "prescan*.tif"))):
        out.append(Frame(p, tiff.read(p), "roll"))

    # The metering probes: what metering_slice actually sees in production.
    # scan() takes these at DEPTH_16 where prescan() hard-codes DEPTH_8, so
    # they are a different regime and belong in the corpus on their own.
    for entry in sorted(glob.glob(str(root / "library" / "*" / "scan.json"))):
        try:
            record = json.loads(Path(entry).read_text())
        except (OSError, ValueError):
            continue
        image_meta = record.get("image") or {}
        shape = image_meta.get("shape") or []
        if image_meta.get("dtype") == "uint16" and len(shape) == 3 and shape[1] == 428:
            path = str(Path(entry).with_name("scan.tif"))
            if Path(path).exists():
                out.append(Frame(path, tiff.read(path), "probe"))
    return out


def colour_of_band(image: np.ndarray, columns: np.ndarray) -> float | None:
    """R:B of the given columns. Empty aperture is neutral (~0.93); clear C-41
    base is strongly orange (3-7). This is the property that tells the two
    apart, and `film_bounds` discards it by averaging the channels."""
    if image.ndim != 3 or image.shape[2] < 3 or not columns.any():
        return None
    band = image[:, columns, :].astype(np.float64)
    red, blue = band[..., 0].mean(), band[..., 2].mean()
    return float(red / max(blue, 1e-9))


# --- measuring one frame under one rule ----------------------------------


def meter_delta(image: np.ndarray, sl: tuple[slice, slice]) -> list[float]:
    """Signed change in the metering percentile from cropping, per channel.

    The number that actually decides this: `auto_exposure` reads this
    percentile inside `metering_slice`'s crop, so a change here is a change in
    the exposure of the real scan. Negative means the crop *lowered* it, which
    raises exposure -- the direction that clips, and nothing downstream undoes
    a clipped highlight.
    """
    whole = image.reshape(-1, image.shape[2]) if image.ndim == 3 else image.reshape(-1, 1)
    crop = image[sl]
    crop = crop.reshape(-1, crop.shape[2]) if crop.ndim == 3 else crop.reshape(-1, 1)
    if crop.size == 0:
        return [0.0] * whole.shape[1]
    out = []
    for c in range(whole.shape[1]):
        a = float(np.percentile(whole[:, c], METER_PERCENTILE))
        b = float(np.percentile(crop[:, c], METER_PERCENTILE))
        out.append(100.0 * (b - a) / max(a, 1e-9))
    return out


def assess(frame: Frame, rule: Rule) -> dict[str, Any]:
    """One frame under one rule."""
    cols, rows = profiles(frame.image)
    h, w = frame.image.shape[:2]

    x = rule.span(cols)
    y = rule.span(rows)
    sl = (
        slice(y[0], y[1] + 1) if y else slice(None),
        slice(x[0], x[1] + 1) if x else slice(None),
    )
    retained_x = ((x[1] - x[0] + 1) / w) if x else 1.0
    retained_y = ((y[1] - y[0] + 1) / h) if y else 1.0

    deltas = meter_delta(frame.image, sl)
    # Registration reports x only, in mm across the aperture.
    span_units = FULL_FRAME[2] - FULL_FRAME[0] + 1
    mm_per_px = span_units * MM_PER_INCH / COORD_PER_INCH / max(w, 1)
    shortfall_mm = (w - (x[1] - x[0] + 1)) * mm_per_px if x else 0.0

    return {
        "abstain_x": x is None,
        "abstain_y": y is None,
        "retained_x": round(retained_x, 4),
        "retained_y": round(retained_y, 4),
        "meter_delta_pct": [round(v, 3) for v in deltas],
        "worst_meter_delta_pct": round(min(deltas), 3) if deltas else 0.0,
        "shortfall_mm": round(shortfall_mm, 3),
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    rx = np.array([r["retained_x"] for r in rows])
    ry = np.array([r["retained_y"] for r in rows])
    worst = np.array([r["worst_meter_delta_pct"] for r in rows])
    return {
        "frames": n,
        "abstain_x": sum(r["abstain_x"] for r in rows),
        "abstain_y": sum(r["abstain_y"] for r in rows),
        "retained_x_min": round(float(rx.min()), 4),
        "retained_x_p5": round(float(np.percentile(rx, 5)), 4),
        "retained_x_median": round(float(np.median(rx)), 4),
        "retained_y_min": round(float(ry.min()), 4),
        "worst_meter_delta_pct": round(float(worst.min()), 3),
        "frames_meter_lowered": int((worst < -0.05).sum()),
        "unsafe_frames": int(((rx < MIN_SAFE_RETAINED) | (ry < MIN_SAFE_RETAINED)).sum()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default=".", help="repo root (default: %(default)s)")
    ap.add_argument("--json", default=None, help="also write the full per-frame table here")
    args = ap.parse_args()

    root = Path(args.root)
    corpus = load_corpus(root)
    if not corpus:
        print("no prescans found -- library/ and rolls/ are gitignored, so a "
              "fresh clone has none", file=sys.stderr)
        return 1

    kinds: dict[str, int] = {}
    for f in corpus:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    print(f"corpus: {len(corpus)} frames  " +
          "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))

    # --- what the gate sees, before any rule runs ------------------------
    ratios_x, ratios_y, clears = [], [], []
    for f in corpus:
        cols, rows = profiles(f.image)
        ratios_x.append(clear_ratio(cols))
        ratios_y.append(clear_ratio(rows))
        clears.append(float(np.percentile(cols, CLEAR_PERCENTILE)))
    rx, ry = np.array(ratios_x), np.array(ratios_y)
    print(f"\nclear/median ratio, x: min {rx.min():.2f}  median {np.median(rx):.2f}  "
          f"max {rx.max():.2f}    (gate fires at {CLEAR_RATIO})")
    print(f"clear/median ratio, y: min {ry.min():.2f}  median {np.median(ry):.2f}  "
          f"max {ry.max():.2f}")
    print(f"x profiles reaching the gate: {int((rx >= CLEAR_RATIO).sum())}/{len(corpus)}"
          f"    y: {int((ry >= CLEAR_RATIO).sum())}/{len(corpus)}")
    print(f"clear level (98th pct): min {min(clears):.0f}  median "
          f"{np.median(clears):.0f}  max {max(clears):.0f}")

    # --- the independent detector ----------------------------------------
    fires = [f for f in corpus if any(gap_edges(f.image))]
    print(f"\ngap_edges -- the independent detector -- fires on {len(fires)}/"
          f"{len(corpus)} frames")
    for f in fires:
        left, right = gap_edges(f.image)
        cols, _ = profiles(f.image)
        band = (cols >= np.percentile(cols, 95))
        rb = colour_of_band(f.image, band)
        rb_text = f"R:B {rb:.2f}" if rb is not None else "R:B n/a"
        verdict = ("clear base (orange)" if rb and rb > 2.0
                   else "neutral -- aperture?" if rb else "")
        print(f"    {Path(f.path).parent.name[:44]:46} gap L{left:3d} R{right:3d}   "
              f"{rb_text}   {verdict}")

    # --- the rules --------------------------------------------------------
    print(f"\n{'rule':<20}{'abst x':>7}{'abst y':>7}{'ret x min':>11}"
          f"{'ret x p5':>10}{'worst meter %':>15}{'lowered':>9}{'unsafe':>8}")
    results: dict[str, Any] = {}
    for rule in RULES:
        rows = [assess(f, rule) for f in corpus]
        s = summarise(rows)
        results[rule.name] = {"summary": s,
                              "frames": {f.path: r for f, r in zip(corpus, rows)}}
        flag = "  <-- ships" if rule.name == "fixed_gated" else ""
        print(f"{rule.name:<20}{s['abstain_x']:>7}{s['abstain_y']:>7}"
              f"{s['retained_x_min']:>11.2f}{s['retained_x_p5']:>10.2f}"
              f"{s['worst_meter_delta_pct']:>15.2f}{s['frames_meter_lowered']:>9}"
              f"{s['unsafe_frames']:>8}{flag}")

    print(f"\n'abst' counts frames where the rule declines to crop -- for this "
          f"corpus, where film fills the window, that is the correct answer.")
    print(f"'unsafe' counts frames left holding under {MIN_SAFE_RETAINED:.0%} of an "
          f"axis. Any non-zero disqualifies a rule.")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
