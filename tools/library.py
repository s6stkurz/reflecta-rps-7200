#!/usr/bin/env python3
"""Inspect the scan library.

    uv run python tools/library.py list
    uv run python tools/library.py verify
    uv run python tools/library.py reconstruct        # re-decode every entry
    uv run python tools/library.py duplicates         # what is redundant, and why
    uv run python tools/library.py duplicates --delete
    uv run python tools/library.py migrate-direction  # which way each pass was read
    uv run python tools/library.py merge --tag bracket-3600x9 --out merged.tif

`reconstruct` is the one worth running after any change to how the scanner's
bytes become pixels: it decodes every stored pass with today's code and says
which entries no longer match what was saved.

`duplicates` finds entries that are the same scan of the same picture at the
same protocol revision -- the scanner was driven identically, so one of them
holds nothing the other does not. It only reports; `--delete` is what removes
them, and `--keep N` leaves more than one of each behind.

`migrate-direction` brings entries filed before passes were read upright from
their own line tags up to date: every entry records which way the carriage
read it, a scan stored bottom-up is turned upright from its raw bytes, and a
stored `prescan.tif` is judged against its upright scan and turned only when
that is decisive. A dry run unless given `--write`.

`merge` puts several passes of one frame together -- a bracket, or repeats at
one exposure -- from their stored raw bytes with today's correction: register
every pass onto the first (`rps7200.passes`), then `rps7200.bracket.merge_bracket`.
Name the entries, or pick them by `--tag`. It reports how far each pass sat
from the first, how well the passes agree before and after registering, and
the merge's noise against a single pass; `--out` also writes the merged,
corrected picture. Nothing is filed: the library holds raw passes, and the
merge is recomputed from them whenever it is wanted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library
from rps7200.console import use_utf8_stdout

#: Border left out of every agreement and noise figure, in pixels: the strip a
#: registered pass is invalid in (`rps7200.passes.EDGE_MARGIN_PX`) plus more
#: than any shift the library has shown. Inside it a merge is the reference
#: alone, and counting it would understate what merging did.
MEASURE_MARGIN_PX = 40


def _metrics():
    """The measure-scan-quality skill's metrics, as other tools reach them."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / ".claude" / "skills" / "measure-scan-quality" / "scripts"))
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
    import numpy as np

    from rps7200.bracket import merge_bracket, solve_relation
    from rps7200.passes import band_residuals, register_passes

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
    ap.add_argument("action",
                    choices=["list", "verify", "reconstruct", "reindex",
                             "duplicates", "migrate-raw", "migrate-direction",
                             "merge"])
    ap.add_argument("entries", nargs="*",
                    help="merge: the passes, as entry ids or paths")
    ap.add_argument("--root", default="library")
    ap.add_argument("--delete", action="store_true",
                    help="duplicates: actually remove them (default is a dry run)")
    ap.add_argument("--write", action="store_true",
                    help="migrate-raw, migrate-direction: actually rewrite "
                         "the entries (default is a dry run)")
    ap.add_argument("--keep", type=int, default=1, metavar="N",
                    help="duplicates: how many of each group to keep (default 1). "
                         "Use 2 to retain a pair for pass-to-pass comparisons")
    ap.add_argument("--tag", help="merge: every entry carrying this tag")
    ap.add_argument("--no-register", action="store_true",
                    help="merge: merge the passes as they lie, for comparison")
    ap.add_argument("--out", type=Path,
                    help="merge: write the merged picture here, with a .json report")
    args = ap.parse_args()
    root = Path(args.root)

    if not root.exists():
        print(f"no library at {root}", file=sys.stderr)
        return 1

    if args.action == "list":
        rows = library.entries(root)
        if not rows:
            print("library is empty")
            return 0
        print(f"{'id':52} {'dpi':>5} {'ch':>3}  film / notes")
        for r in rows:
            scan, film = r.get("scan") or {}, r.get("film") or {}
            raw = "raw" if (r.get("raw") or {}).get("file") else "NO RAW"
            desc = " / ".join(x for x in (film.get("stock"), film.get("notes")) if x)
            # `or ''` rather than a `get` default: these keys are *present* and
            # hold None on entries filed before they were recorded -- 69 of the
            # 311 here -- so a default never fires and the format spec raises on
            # NoneType. `list` died 56 rows in, which is the one command that is
            # meant to survey a library that has grown over time.
            print(f"{r.get('id') or '':52} "
                  f"{scan.get('resolution_dpi') or '':>5} "
                  f"{scan.get('channels') or '':>3}  {desc}  [{raw}]")
        print(f"\n{len(rows)} entries")

    elif args.action == "verify":
        problems = library.verify(root)
        for p in problems:
            print(p)
        print(f"\n{len(problems)} problem(s)" if problems else "\nlibrary is intact")
        return 1 if problems else 0

    elif args.action == "reconstruct":
        # An entry with no bytes to decode is not a decode regression, and
        # counting it as one turns this check into a metric that cries wolf --
        # the summary read "6 entries no longer decode to what was stored"
        # when all six simply had nothing stored to decode.
        changed = unreadable = 0
        for r in library.entries(root):
            path = root / str(r.get("id"))
            _, verdict = library.reconstruct(path)
            if verdict.startswith("identical"):
                mark = " "
            elif "no raw bytes" in verdict or verdict.startswith("could not"):
                mark, unreadable = "-", unreadable + 1
            else:
                mark, changed = "!", changed + 1
            print(f"{mark} {path.name}: {verdict}")
        print(f"\n{changed} entr{'y' if changed == 1 else 'ies'} no longer "
              f"decode to what was stored" if changed
              else "\nevery entry that can be decoded still decodes to exactly "
                   "what was stored")
        if unreadable:
            print(f"{unreadable} had nothing to decode from -- not a "
                  f"regression, but they cannot be re-corrected either")
        return 1 if changed else 0

    elif args.action == "duplicates":
        doomed = library.prunable(root, keep=args.keep)
        if not doomed:
            print(f"no duplicates (keeping {args.keep} of each group)")
            return 0

        freed = 0
        for record, reason in doomed:
            path = root / str(record.get("id"))
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            freed += size
            print(f"{'removing' if args.delete else 'redundant'}: {path.name}"
                  f"  ({size / 1e6:.1f} MB)\n    {reason}")
            if args.delete:
                shutil.rmtree(path)

        print(f"\n{len(doomed)} entr{'y' if len(doomed) == 1 else 'ies'}, "
              f"{freed / 1e6:.1f} MB"
              + (" removed" if args.delete else " would be freed -- pass --delete"))
        if args.delete:
            library.reindex(root)

    elif args.action == "migrate-raw":
        # Entries hold raw pixels and recompute the correction on the way out.
        # Ones filed before that hold corrected pixels, and two kinds of them
        # are wrong in different ways:
        #
        #   labelled     `corrections_applied: ["shading"]` -- self-consistent,
        #                but not raw, so the correction can never be improved
        #                on them and `reconstruct` has to re-apply shading to
        #                compare at all.
        #   mislabelled  `corrections_applied: []` on pixels that are corrected
        #                -- these describe themselves wrongly, and `reconstruct`
        #                calls every one a changed decode.
        #
        # Both are repaired the same way and without guessing: the raw bytes
        # are still there, so scan.tif is rewritten from them. Nothing is
        # inferred and nothing is inverted -- a corrected image is never
        # un-corrected, it is simply replaced by a fresh decode of the bytes it
        # came from, which is what should have been stored.
        import numpy as np

        from rps7200 import tiff

        planned, skipped, failed = [], [], []
        for r in library.entries(root):
            path = root / str(r.get("id"))
            stored_shape = tuple((r.get("image") or {}).get("shape") or ())
            applied = (r.get("image") or {}).get("corrections_applied") or []
            decoded, verdict = library.reconstruct(path)
            if decoded is None:
                skipped.append((path.name, verdict))
                continue
            try:
                stored = tiff.read(str(path / "scan.tif"))
            except (OSError, ValueError) as exc:
                failed.append((path.name, f"cannot read scan.tif: {exc}"))
                continue
            # `reconstruct` hands back the decode already re-corrected when the
            # record says the pixels are corrected, so decode again plainly.
            plain = library.decode_raw(path)
            if plain is None:
                skipped.append((path.name, "no raw bytes to decode"))
                continue
            if plain.shape != stored.shape:
                failed.append((path.name,
                               f"decode is {plain.shape}, stored {stored.shape}"))
                continue
            if np.array_equal(plain, stored) and not applied:
                continue                       # already raw and says so
            planned.append((path, plain, applied, stored_shape))

        for path, _plain, applied, _shape in planned:
            why = ("mislabelled: corrected pixels filed as raw" if not applied
                   else "corrected pixels, and the correction cannot be improved")
            print(f"{'rewriting' if args.write else 'would rewrite'}: "
                  f"{path.name}\n    {why}")

        if args.write:
            for path, plain, _applied, _shape in planned:
                record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
                resolution = ((record.get("scan") or {}).get("resolution_dpi")
                              or None)
                tiff.write(str(path / "scan.tif"), plain, resolution=resolution)
                image = record.setdefault("image", {})
                image["corrections_applied"] = []
                image["shape"] = list(plain.shape)
                image["dtype"] = str(plain.dtype)
                image["sha256"] = library._sha256(path / "scan.tif")
                (path / "scan.json").write_text(
                    json.dumps(record, indent=2, default=str),
                    encoding="utf-8")
            library.reindex(root)

        print(f"\n{len(planned)} entr{'y' if len(planned) == 1 else 'ies'} "
              + ("rewritten to raw pixels" if args.write
                 else "would be rewritten to raw pixels -- pass --write"))
        for name, why in failed:
            print(f"! {name}: {why}")
        if skipped:
            print(f"{len(skipped)} could not be decoded and were left alone")
        return 1 if failed else 0

    elif args.action == "migrate-direction":
        # One level deep, like every other command here: a library kept in
        # subfolders is migrated one subfolder at a time.
        turned = recorded = left = 0
        for scan_json in sorted(root.glob("*/scan.json")):
            path = scan_json.parent
            try:
                done = library.migrate_direction(path, write=args.write)
            except (OSError, ValueError, KeyError) as exc:
                print(f"! {path.name}: {exc}")
                left += 1
                continue
            for line in done:
                mark = "~" if "turned upright" in line else (
                    "!" if "left alone" in line else " ")
                turned += mark == "~"
                left += mark == "!"
                recorded += mark == " "
                print(f"{mark} {path.name}: {line}")
        verb = "" if args.write else "would be "
        print(f"\n{turned} {verb}turned upright, {recorded} {verb}recorded as "
              f"they are, {left} left alone"
              + ("" if args.write else " -- pass --write"))
        if args.write:
            library.reindex(root)

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")

    elif args.action == "merge":
        from rps7200 import tiff

        try:
            paths = select(root, args.entries, args.tag)
            merged, report = merge_entries(paths, register=not args.no_register)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print_merge(report)
        if args.out:
            tiff.write(str(args.out), merged, resolution=report["resolution_dpi"])
            args.out.with_suffix(".json").write_text(
                json.dumps(report, indent=1), encoding="utf-8")
            print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
