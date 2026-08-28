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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import library, tiff
from rps7200.direct import DirectScanner
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference


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
    ap.add_argument("--film", default="negative",
                    choices=["negative", "positive", "kodachrome", "bw"],
                    help="metering only: a negative is metered per channel to "
                         "take the orange mask off before the ADC; everything "
                         "else keeps its cast")
    ap.add_argument("--library", nargs="?", const="library", default=None,
                    metavar="DIR",
                    help="also file this scan in the reusable library, with its "
                         "raw bytes and calibration, so it can be re-decoded "
                         "and re-corrected later without rescanning")
    ap.add_argument("--stock", default="", help="film stock, e.g. 'Kodak Gold 200'")
    ap.add_argument("--frame", default="", help="frame position on the roll")
    ap.add_argument("--subject", default="")
    ap.add_argument("--notes", default="", help="anything about this frame worth "
                                                "knowing later, e.g. 'dust top left'")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    args = ap.parse_args()

    ref_path = Path(args.reference)
    with DirectScanner(verbose=args.verbose) as s:
        info = s.inquiry()
        print(f"{info.vendor} {info.model}, firmware {info.firmware}")

        if args.no_shading:
            print("shading correction disabled: expect vertical striping")
        elif args.reuse and ref_path.exists():
            s._shading = ShadingReference.load(ref_path)
            print(f"reusing {ref_path} ({s._shading.pixels_per_line} columns, "
                  f"channels {s._shading.channels})")
        else:
            print("calibrating (about 3-4 minutes; the vendor does this once "
                  "per power-on) ...", flush=True)
            t0 = time.monotonic()
            result = s.calibrate_shading()
            print(f"  {result['bytes_drained']/1e6:.2f} MB in "
                  f"{time.monotonic()-t0:.0f}s")
            if result["reference"] is None:
                print("  no usable shading reference; the scan will be raw",
                      file=sys.stderr)
            else:
                ref_path.parent.mkdir(parents=True, exist_ok=True)
                result["reference"].save(ref_path)
                print(f"  saved {ref_path}")

        print(f"scanning at {args.dpi} dpi{' with IR' if args.ir else ''} ...",
              flush=True)
        image, meta = s.scan(
            resolution=args.dpi,
            infrared=args.ir,
            auto_exposure=args.auto_exposure,
            film=args.film,
            shading=not args.no_shading,
            keep_raw=args.library is not None,
        )
        # Everything the library needs, gathered while the session is open.
        # Writing it happens after the session closes: filing an entry gzips
        # well over a hundred megabytes, and holding the device open and idle
        # through that has coincided with it going unresponsive.
        pending = None
        if args.library is not None:
            pending = dict(
                reference=s._shading, ccd_mask=s._ccd_mask,
                raw=s.last_raw, raw_layout=s.last_raw_layout, inquiry=info,
            )

    entry = None
    if pending is not None:
        entry = library.save(
            image, meta,
            root=args.library,
            film=FilmNotes(stock=args.stock, frame=args.frame,
                           subject=args.subject, notes=args.notes),
            tags=args.tags,
            **pending,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tiff.write(str(out), image, resolution=args.dpi)
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"wrote {out}  {image.shape}  {image.dtype}")
    if meta.get("shading"):
        r = meta["shading"]
        print(f"shading: {r['columns']}/{r['width']} columns corrected, "
              f"{r['clipped']} samples clipped")
    if entry is not None:
        print(f"filed in the library as {entry}")
        raw_mb = (entry / "raw.bin.gz").stat().st_size / 1e6 if (
            entry / "raw.bin.gz").exists() else 0
        print(f"  raw bytes kept ({raw_mb:.1f} MB compressed) -- this scan can "
              f"be re-decoded and re-corrected without the scanner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
