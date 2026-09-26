#!/usr/bin/env python3
"""Re-merge stored passes of one frame -- the study's main instrument.

    uv run python docs/multi-exposure/analysis/merge_library.py --tag bracket-3600x9
    uv run python docs/multi-exposure/analysis/merge_library.py ENTRY ENTRY ... --no-register
    uv run python docs/multi-exposure/analysis/merge_library.py --tag bracket-3600x9 --out merged.tif

Loads every pass from its stored raw bytes with today's correction
(`library.corrected`), registers each onto the first (`../code/passes.py`),
merges them (`../code/bracket.py`), and reports how far each pass sat from the
first, how well the passes agree before and after registering, what a rigid
shift leaves along the frame, and the merge's shadow noise against every single
pass on one scale. `--out` writes the merged picture and a JSON report; nothing
is filed in the library.

This was `tools/library.py merge` until the study was archived. Run it from the
repository root: the library and the measurement skill are found from there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE.parent / "code"))

import numpy as np  # noqa: E402
from bracket import merge_bracket, solve_relation  # noqa: E402
from passes import band_residuals, register_passes  # noqa: E402

from rps7200 import library, tiff  # noqa: E402
from rps7200.console import use_utf8_stdout  # noqa: E402

#: Border left out of every agreement and noise figure, in pixels: the strip a
#: registered pass is invalid in (`passes.EDGE_MARGIN_PX`) plus more
#: than any shift the library has shown. Inside it a merge is the reference
#: alone, and counting it would understate what merging did.
MEASURE_MARGIN_PX = 40


def _metrics():
    """The measure-scan-quality skill's metrics, as other tools reach them."""
    sys.path.insert(0, str(REPO / ".claude" / "skills" / "measure-scan-quality" / "scripts"))
    import metrics
    return metrics


def _exposure(record: dict) -> float:
    """Green's exposure multiplier: the merge needs one number per pass."""
    scale = (record.get("scan") or {}).get("exposure_scale")
    if isinstance(scale, (list, tuple)) and len(scale) > 1 and scale[1]:
        return float(scale[1])
    return 1.0


def select(root: Path, names: list[str], tag: str | None) -> list[Path]:
    """Entry directories by id or path, or every entry carrying `tag`."""
    paths = []
    for name in names:
        path = Path(name)
        paths.append(path if path.is_dir() else root / name)
    if tag:
        paths += [root / str(r.get("id")) for r in library.entries(root)
                  if tag in (r.get("tags") or [])]
    missing = [p for p in paths if not (p / "scan.json").exists()]
    if missing:
        raise ValueError(f"no library entry at {', '.join(map(str, missing))}")
    return paths


def merge_entries(paths: list[Path], *, register: bool = True) -> tuple:
    """Register and merge stored passes of one frame: ``(merged, report)``.

    Ordered by exposure, not by `library.entries`, which sorts an id's ``-2``
    before the id itself. Equal exposures stack; different ones bracket -- the
    same merge does both.
    """
    if len(paths) < 2:
        raise ValueError(f"a merge needs at least 2 passes, got {len(paths)}")
    loaded = []
    for path in paths:
        image, record = library.corrected(path)
        loaded.append((_exposure(record), path, image[..., :3], record))
    loaded.sort(key=lambda t: t[0])
    exposures = [t[0] for t in loaded]
    frames = [t[2] for t in loaded]
    shapes = {f.shape for f in frames}
    if len(shapes) > 1:
        raise ValueError(f"passes differ in shape: {sorted(shapes)}")

    m = _metrics()
    cut = (slice(MEASURE_MARGIN_PX, -MEASURE_MARGIN_PX),) * 2
    dark = m.dark_mask(frames[-1][cut])

    def agreement(fs):
        return [m.agreement_z(fs[0][cut], f[cut], dark) for f in fs[1:]]

    report = {
        "entries": [t[1].name for t in loaded],
        "corrected": [t[3].get("corrected") for t in loaded],
        "exposures": exposures,
        "kind": "stack" if max(exposures) <= min(exposures) * 1.01 else "bracket",
        "registered": register,
        "agreement_before": agreement(frames),
    }
    if register:
        aligned, valid, shifts = register_passes(frames)
        report["shifts"] = [
            {"dy": s.dy, "dx": s.dx, "confidence": s.confidence, "refused": s.reason}
            for s in shifts
        ]
        report["agreement_after"] = agreement(aligned)
        # what the rigid shift leaves along the frame, on the pass furthest from
        # the first -- the evidence a warp varying down the frame would need
        report["band_residuals"] = [
            {"row": row, "dy": s.dy, "dx": s.dx, "refused": s.reason}
            for row, s in band_residuals(aligned[0], aligned[-1])
        ]
    else:
        aligned, valid = frames, None
    merged, stats = merge_bracket(aligned, exposures, valid=valid)
    report["stats"] = stats.describe()
    report["fallback_fraction"] = stats.fallback_fraction

    # Every pass is measured on the reference's scale, as the merge is. Relative
    # noise divides by the mean, and a longer pass's own mean carries the
    # offset `solve_relation` finds -- 377 DN in green at x4 on the three-pass
    # bracket -- so measured on its own scale a pass looks a few percent
    # quieter than it is, and a merge that beat it read as losing to it.
    ref = aligned[0]
    singles = []
    for f in aligned:
        scaled = np.empty(f.shape, dtype=np.float64)
        for c in range(3):
            slope, intercept = solve_relation(ref[..., c], f[..., c])
            if not np.isfinite(slope):
                slope, intercept = 1.0, 0.0
            scaled[..., c] = (f[..., c] - intercept) / slope
        singles.append(m.relative_noise(scaled[cut], dark))
    fused = m.relative_noise(merged[cut], dark)
    report["noise_passes"] = singles
    report["noise_merged"] = fused
    report["noise_vs_middle"] = fused / singles[len(singles) // 2] - 1.0
    report["noise_vs_best"] = fused / min(singles) - 1.0
    report["resolution_dpi"] = (loaded[0][3].get("scan") or {}).get("resolution_dpi")
    return np.ascontiguousarray(merged), report


def print_merge(report: dict) -> None:
    print(f"{report['kind']} of {len(report['entries'])} passes"
          f"{'' if report['registered'] else ', NOT registered'}")
    shifts = report.get("shifts") or [None] * len(report["entries"])
    after = [None, *report.get("agreement_after", [None] * (len(shifts) - 1))]
    before = [None, *report["agreement_before"]]
    print(f"  {'entry':40} {'exposure':>8} {'dy':>7} {'dx':>7} {'peak z':>7}"
          f"  {'|z| before':>10} {'after':>6} {'noise':>6}")
    for i, name in enumerate(report["entries"]):
        s = shifts[i]
        where = (f"{s['dy']:+7.2f} {s['dx']:+7.2f} {s['confidence']:7.1f}"
                 if s and not s["refused"] else
                 f"{'refused: ' + s['refused'] if s else '':>23}")
        b = f"{before[i]:10.2f}" if before[i] is not None else f"{'reference':>10}"
        a = f"{after[i]:6.2f}" if after[i] is not None else ""
        a = a or " " * 6
        print(f"  {name:40} {report['exposures'][i]:8.3f} {where}  {b} {a} "
              f"{100 * report['noise_passes'][i]:5.2f}%")
    for band in report.get("band_residuals", []):
        left = ("refused: " + band["refused"] if band["refused"]
                else f"dy {band['dy']:+.2f}  dx {band['dx']:+.2f}")
        print(f"  left after registering, last pass, row {band['row']:5}: {left}")
    print(f"  {report['stats']}")
    print(f"  shadow noise, on the reference's scale: merged "
          f"{100 * report['noise_merged']:.2f}%, "
          f"{100 * report['noise_vs_middle']:+.1f}% against the middle pass, "
          f"{100 * report['noise_vs_best']:+.1f}% against the best single pass")


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*", help="the passes, as entry ids or paths")
    ap.add_argument("--root", default=str(REPO / "library"))
    ap.add_argument("--tag", help="every entry carrying this tag")
    ap.add_argument("--no-register", action="store_true",
                    help="merge the passes as they lie, for comparison")
    ap.add_argument("--out", type=Path,
                    help="write the merged picture here, with a .json report")
    args = ap.parse_args()
    try:
        paths = select(Path(args.root), args.entries, args.tag)
        merged, report = merge_entries(paths, register=not args.no_register)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print_merge(report)
    if args.out:
        tiff.write(str(args.out), merged, resolution=report["resolution_dpi"])
        args.out.with_suffix(".json").write_text(json.dumps(report, indent=1),
                                                 encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
