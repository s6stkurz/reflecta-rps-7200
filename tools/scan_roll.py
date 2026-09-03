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
import queue
import sys
import threading
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
    if not args.no_shading and not (args.reuse and ref_path.exists()):
        print("calibrating (about 3-4 minutes, once for the whole roll) ...",
              flush=True)
    print(scanner.ensure_shading(ref_path, reuse=args.reuse,
                                 skip=args.no_shading)["summary"])


class FrameWriter:
    """Writes finished frames to disk on a thread, off the scanning loop.

    Filing a frame gzips its raw bytes -- seconds at 1800 dpi and several times
    that at 3600 -- and doing it inline leaves the scanner **open and idle** for
    exactly that long, once per frame. That is the state that preceded a wedge
    (see CLAUDE.md). On this thread the write instead overlaps the next frame's
    scan, so the device is busy rather than idle throughout.

    The queue is bounded. A scan costs far longer than a write, so the writer is
    normally idle waiting; a bound only matters if that stops being true, and
    then blocking is right -- an unbounded queue would hold whole frames in
    memory, and at 3600 dpi one frame is over a hundred megabytes.

    Failures are collected, not raised: a roll runs for hours, and a frame that
    cannot be filed should cost that frame, not the thirty after it. `errors`
    is drained by the caller once the roll ends.
    """

    def __init__(self, depth: int = 2):
        self.queue: queue.Queue = queue.Queue(maxsize=depth)
        self.errors: list[str] = []
        self.done: list[tuple[int, Path | None]] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            job = self.queue.get()
            try:
                if job is None:
                    return
                self._write(job)
            except Exception as exc:                     # noqa: BLE001
                self.errors.append(f"picture {job['number']}: {exc}")
            finally:
                self.queue.task_done()

    def _write(self, job: dict) -> None:
        tiff.write(str(job["path"]), job["image"], resolution=job["dpi"])
        entry = None
        if job["library"]:
            entry = library.save(
                job["image"], job["meta"],
                root=job["library"],
                film=job["film"],
                tags=job["tags"],
                prescan=job["prescan"],
                inquiry=job["inquiry"],
                **job["capture"],
            )
        self.done.append((job["number"], entry))

    def submit(self, **job) -> None:
        self.queue.put(job)

    def finish(self) -> None:
        """Wait for every queued frame. Call after the session has closed."""
        self.queue.put(None)
        self._thread.join()


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

    writer = FrameWriter()
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
                record["file"] = path.name
                record["shape"] = list(frame.image.shape)
                record["duration_s"] = frame.meta.get("duration_s")
                record["exposure"] = frame.meta.get("exposure")

                # capture_record() is read here, on this thread, before the next
                # scan overwrites last_raw. Everything after it belongs to the
                # writer and happens while the scanner is busy again.
                writer.submit(
                    number=number,
                    path=path,
                    dpi=args.dpi,
                    image=frame.image,
                    meta=frame.meta,
                    prescan=frame.prescan,
                    library=args.library,
                    inquiry=info,
                    capture=s.capture_record(),
                    tags=sorted({*args.tags, "roll", roll_name}),
                    film=FilmNotes(
                        stock=args.stock,
                        process=args.process,
                        # Distinct per frame, and it has to be:
                        # library.signature() includes film.frame, so without it
                        # every picture of a roll would register as a duplicate
                        # of every other.
                        frame=f"{roll_name}/{number:02d}",
                        notes=args.notes,
                    ),
                )
                print(f"picture {number}: {path} {frame.image.shape} "
                      f"in {frame.meta.get('duration_s')}s")

            manifest["frames"].append(record)
            checkpoint()

    # Only now, with the device closed: the last frame or two may still be
    # gzipping, and that is exactly the work that must not happen with an open
    # session.
    writer.finish()
    filed = dict(writer.done)
    for record in manifest["frames"]:
        entry = filed.get(record["number"])
        if entry is not None:
            record["entry"] = str(entry)
    for problem in writer.errors:
        failed += 1
        print(f"could not file {problem}", file=sys.stderr)

    manifest["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["duration_s"] = round(time.monotonic() - started, 1)
    checkpoint()

    print(f"\n{scanned} scanned, {failed} failed, "
          f"{manifest['duration_s']/60:.1f} min")
    print(f"manifest: {manifest_path}")
    if failed:
        print("resume a failed picture with --start-at N", file=sys.stderr)
    return 1 if failed and not scanned else 0


if __name__ == "__main__":
    raise SystemExit(main())
