#!/usr/bin/env python3
"""Scan a whole roll or strip, unattended, filing every frame in the library.

    python3 tools/scan_roll.py --dry-run --frames 6
    python3 tools/scan_roll.py --dpi 1800 --ir --frames 6 \
        --roll 2026-08-28-gold200 --stock "Kodak Gold 200"

The film is already at the first picture when this starts, so the first frame is
scanned before anything moves; the transport advances between frames. Shading is
calibrated once and reused for the whole roll -- which is what the vendor does,
and the reason a 17-pass session in the captures contains no calibration at all.

Every frame goes to disk the moment it exists: a library entry with the raw
bytes, the shading reference and the CCD mask beside the pixels, plus a
`roll.json` manifest rewritten after each one. A roll takes hours, and a crash
two hours in should cost the frame it was on, not the roll -- `--start-at`
resumes from the manifest.

Start with `--dry-run`. It prescans and advances only, so it walks the whole
strip in a couple of minutes and shows where each picture sits before three
hours are committed to scanning them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library, tiff
from rps7200.direct import (
    METER_EACH,
    METER_MODES,
    DirectScanner,
)
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dpi", type=int, default=1800)
    ap.add_argument("--ir", action="store_true", help="capture the infrared plane too")
    ap.add_argument("--frames", type=int, default=None,
                    help="how many pictures to scan; without it the roll runs "
                         "until the window holds no picture or the transport "
                         "stops moving")
    ap.add_argument("--start-at", type=int, default=1, metavar="N",
                    help="resume at picture N, advancing to it without scanning "
                         "(1 = the picture the film is on now)")
    ap.add_argument("--dry-run", action="store_true",
                    help="prescan and advance only -- no full scans. Walks a "
                         "6-frame strip in about 2.5 minutes")
    ap.add_argument("--meter", choices=METER_MODES, default=METER_EACH,
                    help="'each' re-meters every frame, as CyberView does; "
                         "'once' meters the first picture and holds it, which "
                         "keeps the roll internally consistent and saves ~45 s "
                         "a frame; 'none' scans at the device's own settings")
    ap.add_argument("--film", default="negative",
                    choices=["negative", "positive", "kodachrome", "bw"])
    ap.add_argument("--reference", default="calibration/shading.npz")
    ap.add_argument("--reuse", action="store_true",
                    help="load the cached shading reference instead of "
                         "calibrating (saves 3-4 min, but the reference "
                         "describes the sensor at the exposure that measured it)")
    ap.add_argument("--no-shading", action="store_true",
                    help="skip calibration entirely; scans come back striped")
    ap.add_argument("--roll", default=None,
                    help="name for this roll (default: today's date)")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="where the manifest and per-frame TIFFs go "
                         "(default: rolls/<roll>)")
    ap.add_argument("--library", nargs="?", const="library", default="library",
                    metavar="DIR",
                    help="file every frame in the library with its raw bytes "
                         "and calibration; --library '' to skip")
    ap.add_argument("--stock", default="", help="film stock, e.g. 'Kodak Gold 200'")
    ap.add_argument("--process", default="", help="e.g. 'C-41'")
    ap.add_argument("--notes", default="")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("--max-failures", type=int, default=3,
                    help="consecutive failed frames before the roll gives up")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    return ap


def calibrate(scanner: DirectScanner, args: argparse.Namespace) -> None:
    """Once per roll, as the vendor does once per power-on."""
    ref_path = Path(args.reference)
    if args.no_shading:
        print("shading correction disabled: expect vertical striping")
        return
    if args.reuse and ref_path.exists():
        scanner._shading = ShadingReference.load(ref_path)
        print(f"reusing {ref_path} ({scanner._shading.pixels_per_line} columns, "
              f"channels {scanner._shading.channels})")
        return

    print("calibrating (about 3-4 minutes, once for the whole roll) ...", flush=True)
    t0 = time.monotonic()
    result = scanner.calibrate_shading()
    print(f"  {result['bytes_drained']/1e6:.2f} MB in {time.monotonic()-t0:.0f}s")
    if result["reference"] is None:
        print("  no usable shading reference; the roll will be raw", file=sys.stderr)
    else:
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        result["reference"].save(ref_path)
        print(f"  saved {ref_path}")


def main() -> int:
    args = build_parser().parse_args()

    roll_name = args.roll or datetime.now().strftime("%Y-%m-%d")
    out = Path(args.out or f"rolls/{roll_name}")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "roll.json"

    manifest = {
        "roll": roll_name,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "dpi": args.dpi, "infrared": args.ir, "meter": args.meter,
            "film": args.film, "dry_run": args.dry_run,
            "start_at": args.start_at, "frames": args.frames,
        },
        "frames": [],
    }

    def checkpoint() -> None:
        """Rewritten after every frame: a crash must not lose the record."""
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    checkpoint()
    started = time.monotonic()
    scanned = failed = 0

    with DirectScanner(verbose=args.verbose) as s:
        info = s.inquiry()
        print(f"{info.vendor} {info.product}, firmware {info.firmware}")
        print(f"roll {roll_name} -> {out}\n")

        if not args.dry_run:
            calibrate(s, args)

        for frame in s.scan_roll(
            frames=args.frames,
            resolution=args.dpi,
            infrared=args.ir,
            film=args.film,
            meter=args.meter,
            skip=max(0, args.start_at - 1),
            keep_raw=bool(args.library),
            max_failures=args.max_failures,
            dry_run=args.dry_run,
        ):
            number = frame.index + 1
            record = {
                "number": number,
                "index": frame.index,
                "transport_position": frame.position,
                "registration": frame.registration,
                "error": frame.error,
                "entry": None,
                "file": None,
            }

            if frame.error:
                failed += 1
                print(f"picture {number}: FAILED -- {frame.error}", file=sys.stderr)
            elif args.dry_run:
                r = frame.registration
                short = r.get("shortfall_mm", 0.0)
                # Keep the prescan. The registration numbers are derived from
                # it, and a number that looks wrong can only be settled by
                # looking at what it was measured on.
                if frame.prescan is not None:
                    pre = out / f"prescan{number:02d}.tif"
                    tiff.write(str(pre), frame.prescan)
                    record["prescan"] = pre.name
                print(f"picture {number}: contrast {r.get('contrast')}, "
                      f"x{r.get('x0')}..{r.get('x1')}, "
                      f"offset {r.get('offset_mm'):+.2f} mm"
                      + (f", SHORT BY {short:.2f} mm -- the film has drifted"
                         if short > 0.85 else ""))
            else:
                scanned += 1
                path = out / f"frame{number:02d}.tif"
                tiff.write(str(path), frame.image, resolution=args.dpi)
                record["file"] = path.name
                record["shape"] = list(frame.image.shape)
                record["duration_s"] = frame.meta.get("duration_s")
                record["exposure"] = frame.meta.get("exposure")

                if args.library:
                    entry = library.save(
                        frame.image, frame.meta,
                        root=args.library,
                        film=FilmNotes(
                            stock=args.stock,
                            process=args.process,
                            # Distinct per frame, and it has to be:
                            # library.signature() includes film.frame, so
                            # without it every picture of a roll would register
                            # as a duplicate of every other.
                            frame=f"{roll_name}/{number:02d}",
                            notes=args.notes,
                        ),
                        tags=sorted({*args.tags, "roll", roll_name}),
                        reference=s._shading,
                        ccd_mask=s._ccd_mask,
                        prescan=frame.prescan,
                        raw=s.last_raw,
                        raw_layout=s.last_raw_layout,
                        inquiry=info,
                    )
                    record["entry"] = str(entry)

                print(f"picture {number}: {path} {frame.image.shape} "
                      f"in {frame.meta.get('duration_s')}s"
                      + (f", filed as {record['entry']}" if record["entry"] else ""))

            manifest["frames"].append(record)
            checkpoint()

    manifest["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["duration_s"] = round(time.monotonic() - started, 1)
    checkpoint()

    print(f"\n{scanned} scanned, {failed} failed, "
          f"{manifest['duration_s']/60:.1f} min")
    print(f"manifest: {manifest_path}")
    if failed:
        print(f"resume a failed picture with --start-at N", file=sys.stderr)
    return 1 if failed and not scanned else 0


if __name__ == "__main__":
    raise SystemExit(main())
