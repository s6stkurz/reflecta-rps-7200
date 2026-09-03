#!/usr/bin/env python3
"""Scan with the scanner's own shading correction applied.

    python3 tools/scan.py --dpi 1800 --out scans/negatives/shaded_1800dpi.tif

The scanner returns raw pixels: it measures its per-column response during a
calibration pass and hands that back, but never applies it. So a session runs
the calibration once -- as the vendor software does at power-on -- and every
scan after it is corrected from that reference. Skipping this is what leaves the
vertical stripes in.

The reference is cached to disk so it can be inspected or reused, but prefer a
fresh one: it describes the sensor at the exposure and gain of the pass that
measured it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import numpy as np

from rps7200 import library, tiff
from rps7200.direct import DirectScanner
from rps7200.library import FilmNotes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=1800)
    ap.add_argument("--out", default="scan.tif")
    ap.add_argument("--ir", action="store_true", help="capture the infrared plane too")
    ap.add_argument("--reference", default="calibration/shading.npz",
                    help="where to cache the shading reference")
    ap.add_argument("--reuse", action="store_true",
                    help="load the cached reference instead of calibrating")
    ap.add_argument("--no-shading", action="store_true",
                    help="return raw pixels, for comparison")
    ap.add_argument("--auto-exposure", action="store_true")
    ap.add_argument("--bracket", type=int, default=0, metavar="N",
                    help="scan N exposures of this frame (2-9) and merge them by "
                         "inverse-variance weighting, for lower shadow noise. "
                         "Infrared is not bracketed: one pass carries it. "
                         "0, the default, takes a single pass")
    ap.add_argument("--stops", type=float, default=2.0,
                    help="how far the bracket spans, in stops (default 2). The "
                         "top is pinned to the exposure timer's ceiling and the "
                         "rest step down from it")
    ap.add_argument("--exposure-scale", default=None, metavar="X|R,G,B[,I]",
                    help="hold exposure at this multiple of the scanner's own "
                         "settings instead of metering. One value, or one per "
                         "channel. Needed whenever several passes have to be "
                         "comparable: SET GAIN OFFSET does not persist across a "
                         "scan sequence, so every pass sets it afresh, and two "
                         "passes metered independently are not the same "
                         "measurement")
    ap.add_argument("--film", default="negative",
                    choices=["negative", "positive", "kodachrome", "bw"],
                    help="metering only: a negative is metered per channel to "
                         "take the orange mask off before the ADC; everything "
                         "else keeps its cast")
    ap.add_argument("--library", nargs="?", const="library", default="library",
                    metavar="DIR",
                    help="file this scan in the reusable library, with its raw "
                         "bytes and calibration, so it can be re-decoded and "
                         "re-corrected later without rescanning (default: "
                         "library/)")
    ap.add_argument("--no-library", action="store_true",
                    help="do not file this scan. The raw bytes and the "
                         "session's calibration are then gone for good: neither "
                         "can be recovered from the TIFF, so a later change to "
                         "the decode or the correction cannot be applied to it")
    ap.add_argument("--stock", default="", help="film stock, e.g. 'Kodak Gold 200'")
    ap.add_argument("--frame", default="", help="frame position on the roll")
    ap.add_argument("--subject", default="")
    ap.add_argument("--notes", default="", help="anything about this frame worth "
                                                "knowing later, e.g. 'dust top left'")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    args = ap.parse_args()
    if args.no_library:
        args.library = None
    if args.bracket and not (
        DirectScanner.MIN_BRACKET_PASSES
        <= args.bracket
        <= DirectScanner.MAX_BRACKET_PASSES
    ):
        # Before the device is opened, not after: a bracket refused three
        # minutes into a calibration has already cost the calibration.
        ap.error(
            f"--bracket takes {DirectScanner.MIN_BRACKET_PASSES} to "
            f"{DirectScanner.MAX_BRACKET_PASSES} passes, got {args.bracket}"
        )

    exposure_scale: float | list[float] = 1.0
    if args.exposure_scale:
        parts = [float(v) for v in args.exposure_scale.replace(",", " ").split()]
        exposure_scale = parts[0] if len(parts) == 1 else parts
    if args.auto_exposure and args.exposure_scale:
        print("--exposure-scale overrides --auto-exposure", file=sys.stderr)

    ref_path = Path(args.reference)
    with DirectScanner(verbose=args.verbose) as s:
        info = s.inquiry()
        print(f"{info.vendor} {info.model}, firmware {info.firmware}")

        if not args.no_shading and not (args.reuse and ref_path.exists()):
            print("calibrating (about 3-4 minutes; the vendor does this once "
                  "per power-on) ...", flush=True)
        print(s.ensure_shading(ref_path, reuse=args.reuse,
                               skip=args.no_shading)["summary"])

        # Everything the library needs is gathered while the session is open and
        # written after it closes: filing an entry gzips well over a hundred
        # megabytes, and holding the device open and idle through that has
        # preceded it going unresponsive.
        pending: list[dict] = []

        def hold(image, meta, capture) -> None:
            if args.library is not None:
                pending.append(
                    dict(capture, inquiry=info, image=image, meta=meta)
                )

        bracket = None
        if args.bracket:
            print(f"scanning {args.bracket} exposures over {args.stops:g} stops "
                  f"at {args.dpi} dpi{' (one with IR)' if args.ir else ''} ...",
                  flush=True)
            # Each pass is filed as it lands. Only one pass's raw bytes survive
            # on the scanner -- last_raw is overwritten by the pass after it --
            # so waiting for the return value would file the last and lose the
            # rest, which is the whole point of taking a bracket.
            bracket = s.scan_bracket(
                passes=args.bracket,
                stops=args.stops,
                resolution=args.dpi,
                infrared=args.ir,
                film=args.film,
                auto_exposure=args.auto_exposure and not args.exposure_scale,
                exposure_scale=(
                    list(exposure_scale)
                    if isinstance(exposure_scale, list) else None
                ),
                keep_raw=args.library is not None,
                shading=not args.no_shading,
                on_pass=lambda i, image, meta, capture: hold(image, meta, capture),
            )
            image, meta = bracket[0][-1], bracket[2][-1]
        else:
            print(f"scanning at {args.dpi} dpi{' with IR' if args.ir else ''} ...",
                  flush=True)
            image, meta = s.scan(
                resolution=args.dpi,
                infrared=args.ir,
                exposure_scale=exposure_scale,
                auto_exposure=args.auto_exposure and not args.exposure_scale,
                film=args.film,
                shading=not args.no_shading,
                keep_raw=args.library is not None,
            )
            hold(image, meta, s.capture_record())

    entries = []
    for held in pending:
        entries.append(library.save(
            held.pop("image"), held.pop("meta"),
            root=args.library,
            film=FilmNotes(stock=args.stock, frame=args.frame,
                           subject=args.subject, notes=args.notes),
            tags=args.tags,
            **held,
        ))

    if bracket is not None:
        from rps7200.bracket import merge_bracket

        frames, ratios, metas = bracket
        # Merge the visible channels only: with --ir the brightest pass is RGBI
        # and the rest RGB, so the frames do not share a channel count.
        merged, stats = merge_bracket([f[..., :3] for f in frames], ratios)
        print(f"bracket: {stats.describe()}")
        if args.ir and frames[-1].shape[2] == 4:
            # The infrared pass is the brightest; carry its plane through.
            image = np.dstack([merged, frames[-1][..., 3]])
        else:
            image = merged
        meta = dict(metas[-1])
        meta["bracket"] = {
            "passes": len(frames), "ratios": ratios,
            "stops": args.stops, "stats": stats.describe(),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tiff.write(str(out), image, resolution=args.dpi)
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"wrote {out}  {image.shape}  {image.dtype}")
    if meta.get("shading"):
        r = meta["shading"]
        print(f"shading: {r['columns']}/{r['width']} columns corrected, "
              f"{r['clipped']} samples clipped")
    if entries:
        raw_mb = sum(
            (e / "raw.bin.gz").stat().st_size for e in entries
            if (e / "raw.bin.gz").exists()
        ) / 1e6
        noun = "pass" if len(entries) == 1 else "passes"
        print(f"filed {len(entries)} {noun} in the library:")
        for e in entries:
            print(f"  {e}")
        print(f"  raw bytes kept ({raw_mb:.1f} MB compressed) -- these can be "
              f"re-decoded and re-corrected without the scanner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
