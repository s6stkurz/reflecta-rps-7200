#!/usr/bin/env python3
"""Scan a whole roll or strip, unattended, filing every frame in the library.

    uv run python tools/scan_roll.py --dry-run --frames 6
    uv run python tools/scan_roll.py --dpi 1800 --ir --frames 6 \
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
hours are committed to scanning them. Its manifest is `survey.json`, beside the
`prescanNN.tif` it measured each frame on, so the walk survives the roll that
follows it into the same directory.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import framing, tiff
from rps7200.console import use_utf8_stdout
from rps7200.direct import (
    METER_EACH,
    METER_MODES,
    DirectScanner,
    supports_infrared,
)
from rps7200.library import FilmNotes
# Lives in the package so the GUI and this tool share one writer rather than
# two copies of the same reasoning about not gzipping with the device open.
from rps7200.session import Approved, FrameWriter, plan_nudges


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dpi", type=int, default=1800)
    ap.add_argument("--ir", action="store_true", help="capture the infrared plane too")
    ap.add_argument("--fast-ir", dest="fast_ir", action="store_true", default=True,
                    help="tie the infrared plane's cost to --dpi (default). "
                         "Worth most here: it is the difference between a "
                         "38-frame infrared roll spending 2.3 hours on the "
                         "fixed floor and 1.2. See docs/fast-infrared-plan.md.")
    ap.add_argument("--no-fast-ir", dest="fast_ir", action="store_false",
                    help="untie it: the fixed ~220 s infrared pass.")
    ap.add_argument("--frames", type=int, default=None,
                    help="how many pictures to scan; without it the roll runs "
                         "until the window holds no picture or the transport "
                         "stops moving")
    ap.add_argument("--start-at", type=int, default=1, metavar="N",
                    help="resume at picture N, advancing to it without scanning "
                         "(1 = the picture the film is on now)")
    ap.add_argument("--rewind", type=int, default=0,
                    help="wind the film back this many frames before doing "
                         "anything else, one frame at a time, checking each "
                         "one landed. With --frames 0 it rewinds and stops.")
    ap.add_argument("--nudge", type=float, default=0.0,
                    help="move the film this many mm before starting, after "
                         "any --rewind. The window's fine-adjust buttons do "
                         "the same thing; here it is for setting a strip "
                         "deliberately badly, to see the correction work "
                         "against something worth correcting.")
    ap.add_argument("--prescan-dpi", type=int, default=300,
                    help="resolution of the survey prescan (default 300). A "
                         "commissioned scan must use the same one its "
                         "positions were set on: `measure_shift_mm` resamples "
                         "a mismatched reference at about half the "
                         "confidence, under the floor, so every frame would "
                         "read unverified and nothing would move.")
    ap.add_argument("--approved", type=Path, default=None,
                    help="a roll folder from an earlier --dry-run walk. Its "
                         "prescans are re-read, positions proposed for the "
                         "whole strip, and each frame held to its own -- the "
                         "same path the window's contact sheet drives, "
                         "runnable without it. With --dry-run this moves the "
                         "film and costs prescans rather than scans.")
    ap.add_argument("--correct", action="store_true",
                    help="nudge the film back into registration between frames, "
                         "using the calibrated sub-frame move. Off by default: "
                         "the vendor does not do this during a roll either, and "
                         "a strip that does not drift gains nothing from it")
    ap.add_argument("--correct-dry-run", action="store_true",
                    help="measure registration and log the correction that "
                         "would be sent, without moving the film")
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


#: How many backward commands a direction change may swallow before the film
#: actually moves. Two to three, measured -- `tools/transport_truth.py` and
#: `_hold_to_approved`'s own docstring both say so -- so three is the
#: conservative end, and the film has not moved while they are being spent.
BACKLASH_COMMANDS = 3


def rewind(scanner: DirectScanner, frames: int) -> int | None:
    """Wind the film back, one frame at a time, checking each one landed.

    One at a time deliberately. `retreat(steps=N)` exists, but the wait
    underneath only watches for the position to *change*, so a multi-step call
    returns as soon as it has moved at all -- it cannot tell sixteen frames
    from one.

    Checking each one is the other half, and it is the part the window does not
    do: `on_scan_chosen` queues `Move(frames=-back)` and the roll back to back,
    and `_move` reports a failure by returning a *string* that the worker logs
    before taking the next job. A rewind that got three of fourteen is followed
    immediately by a roll that scans frames it has mis-numbered -- and a break
    after three successes reads identically to a break after none.

    The first command or two may do nothing, and that is expected rather than a
    failure: a roll leaves the transport loaded forward, so the first backward
    command is a direction change and **backlash swallows two to three of
    them** -- `tools/transport_truth.py` says so in as many words, and adds
    that one failure proves nothing. Measured here 2026-09-21: a 14-frame
    rewind from position 14 ran first time after one roll and had its first
    command swallowed after the next, with the device healthy either way.

    So a no-op is tolerated while the film has not started moving, and is the
    end of the strip once it has. Returns where the film ended up, or None if
    it stopped short: a caller that gets None must not go on, because
    everything after this assumes the film is where it was asked to be.
    """
    start = scanner.position()
    print(f"rewinding {frames} frame(s) from position {start}")
    done, swallowed = 0, 0
    while done < frames:
        before = scanner.position()
        landed = scanner.retreat()
        now = scanner.position()
        if landed is None or now == before:
            if done == 0 and swallowed < BACKLASH_COMMANDS:
                swallowed += 1
                print(f"  (no movement yet -- backlash, command "
                      f"{swallowed}/{BACKLASH_COMMANDS})", flush=True)
                continue
            print(f"  stopped after {done} of {frames}: position still "
                  f"{before}", file=sys.stderr)
            return None
        done += 1
        print(f"  {done}/{frames}: {before} -> {now}", flush=True)
    return scanner.position()


def hold_from_walk(folder: Path) -> tuple[dict[int, Approved], dict]:
    """The positions an earlier walk's strip proposes, ready to be held to.

    ``folder`` is a roll directory from a previous ``--dry-run`` walk: its
    ``prescanNN.tif`` are the pictures the positions were measured on, and they
    become the references the roll correlates each fresh prescan against.

    This is the window's contact-sheet path with the window taken off. It calls
    the same `framing.propose_offsets` the sheet seeds itself from and builds
    the same `session.Approved` the sheet hands to a commissioned scan, so what
    runs here is what runs there -- which is the point, because a probe that
    agrees with itself proves nothing about the path an operator uses.

    An offset is **relative to where the film sat when that frame was
    surveyed**, never an absolute coordinate: a sub-frame move does not touch
    the frame counter, so there is no such coordinate to name. That is also
    what makes this survive an imprecise rewind -- the reference anchors it, so
    the film is driven to "where the walk saw this frame, plus the correction"
    however exactly the transport came back.
    """
    frames = []
    for path in sorted(folder.glob("prescan*.tif")):
        if path.stem.endswith("-before"):
            continue                      # the picture a correction replaced
        number = int("".join(c for c in path.stem if c.isdigit()) or 0)
        if number:
            frames.append((number, tiff.read(str(path))))
    if len(frames) < 2:
        raise SystemExit(f"{folder} holds {len(frames)} prescan(s); a strip is "
                         "needed to propose positions from")

    offsets, notes = framing.propose_offsets(
        [(n, im.astype(float)) for n, im in frames])
    held = {
        n: Approved(number=n, offset_mm=float(offsets[n]), reference=im)
        for n, im in frames if n in offsets
    }
    return held, {"offsets": {n: round(v, 4) for n, v in offsets.items()},
                  "sources": {n: (notes.get(n) or {}).get("source")
                              for n in offsets},
                  "walked": len(frames), "from": str(folder)}


def main() -> int:
    use_utf8_stdout()
    ap = build_parser()
    args = ap.parse_args()
    if args.ir and not supports_infrared(args.film):
        ap.error(
            f"--ir with --film {args.film}: infrared is blind to it -- its "
            + ("grain" if args.film == "bw" else "cyan layer")
            + " absorbs infrared, so every frame would spend its ~212 s floor "
            "and hand back the picture rather than the dust. Drop --ir. "
            "(Chromogenic C-41 black and white does clean properly: scan that "
            "as --film negative.)"
        )

    held: dict[int, Approved] = {}
    held_note: dict = {}
    if args.approved:
        # Before the device is opened: a folder that cannot be read should cost
        # nothing, and the scanner should never be left open waiting on a file.
        held, held_note = hold_from_walk(args.approved)
        counts: dict[str, int] = {}
        for source in held_note["sources"].values():
            counts[source] = counts.get(source, 0) + 1
        print(f"holding {len(held)} frame(s) to positions from "
              f"{args.approved}: "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))

    roll_name = args.roll or datetime.now().strftime("%Y-%m-%d")
    out = Path(args.out or f"rolls/{roll_name}")
    out.mkdir(parents=True, exist_ok=True)
    # A dry run and the scan that follows it share a directory, so they must
    # not share a file: the record of what was walked is what says which frames
    # are worth scanning, and writing the scan over it loses that.
    manifest_path = out / ("survey.json" if args.dry_run else "roll.json")

    manifest = {
        "roll": roll_name,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "dpi": args.dpi, "infrared": args.ir, "meter": args.meter,
            "film": args.film, "dry_run": args.dry_run,
            "start_at": args.start_at, "frames": args.frames,
            # Written because the window pins a commissioned scan's prescan to
            # whatever the survey walked at, and reads that pin from here
            # (`tools/gui.py` `_survey_predpi`). Without the key the pin is
            # inert and a mismatch silently resamples the reference, which
            # halves `measure_shift_mm`'s confidence -- 93.5 to 47.4 measured,
            # against a floor of 55. Every frame would then read `unverified`,
            # nothing would move, and the run would be a loss with no error.
            "prescan_resolution": args.prescan_dpi,
        },
        "held": held_note,
        "frames": [],
    }

    def checkpoint() -> None:
        """Rewritten after every frame: a crash must not lose the record."""
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str),
                                 encoding="utf-8")

    checkpoint()
    started = time.monotonic()
    scanned = failed = 0

    writer = FrameWriter()
    # debug=False deliberately: this tool files its own library entries,
    # and letting the driver file as well writes every frame twice --
    # 43 GB of duplicate on a 38-frame roll at 7200 dpi.
    # Wrapped so `writer.finish()` below runs whatever comes out of this.
    # An exception the roll loop does not catch used to unwind straight
    # past it, and the frames already queued died unfiled -- scanner time
    # turned into nothing, with no message.
    trouble: Exception | None = None
    try:
        with DirectScanner(verbose=args.verbose, debug=False) as s:
            info = s.inquiry()
            print(f"{info.vendor} {info.product}, firmware {info.firmware}")
            print(f"roll {roll_name} -> {out}\n")

            # On a dry run too. A dry run still prescans, and a prescan
            # still wants a shading reference -- so skipping this did not
            # avoid the calibration, it only moved it inside `prescan()`,
            # where it runs lazily on the first frame. Measured twice on this
            # machine, that lazy calibration stalls: `bulk read of 16384 bytes
            # failed after 0 bytes: LIBUSB_ERROR_PIPE`, right after the
            # shading descriptor, and the device stops answering. Called from
            # here it is the same call `tools/scan.py` and the window's
            # Calibrate job make, both of which work.
            #
            # It also made `--reuse` inert on a dry run: the flag is read
            # here and nowhere else, so the lazy path ignored it and
            # recalibrated regardless.
            if args.rewind:
                landed = rewind(s, args.rewind)
                if landed is None:
                    print("refusing to go on: the film is not where it was "
                          "asked to be, so every frame after this would be "
                          "mis-numbered", file=sys.stderr)
                    return 1
                print(f"rewound to position {landed}")
                print()
            if args.nudge:
                # Deliberately, and said out loud: everything downstream
                # measures against where the film is now, so a displacement
                # nobody knows about would read as the film's own error.
                #
                # Through `plan_nudges` because one command reaches only
                # 1.0118 mm and `param_for_mm` clamps there silently -- a
                # request for 2 mm would otherwise deliver half of it and say
                # nothing. The planner raises instead, and it is the same
                # planner the window's adjuster and the hold loop size
                # themselves from.
                try:
                    steps = plan_nudges(args.nudge)
                except ValueError as exc:
                    print(f"cannot offset the film: {exc}", file=sys.stderr)
                    return 1
                sent = 0.0
                for step in steps:
                    asked = s.nudge(step)
                    sent += asked["asked_mm"]
                    time.sleep(0.5)
                print(f"offset the film by {sent:+.3f} mm in {len(steps)} "
                      f"command(s) (asked {args.nudge:+.3f})")
                if args.nudge < 0:
                    print("  backward, so the first two or three commands may "
                        "have gone into backlash -- the prescan below is what "
                        "says where the film really is")
                print()
            if args.frames == 0:
                print("nothing else asked for")
                return 0

            calibrate(s, args)

            for frame in s.scan_roll(
                frames=args.frames,
                resolution=args.dpi,
                infrared=args.ir,
                fast_infrared=args.fast_ir and args.ir,
                film=args.film,
                meter=args.meter,
                prescan_resolution=args.prescan_dpi,
                skip=max(0, args.start_at - 1),
                keep_raw=bool(args.library),
                max_failures=args.max_failures,
                dry_run=args.dry_run,
                # Keyed by the roll's own index, as `session.py` keys it: the
                # offsets are relative to the survey's start, not to a
                # transport coordinate.
                approved={n - 1: a for n, a in held.items()},
                correct=args.correct,
                correct_dry_run=args.correct_dry_run,
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
                    if frame.prescan_before is not None:
                        # The frame as it arrived, before aiming moved it. A
                        # corrected prescan replaces the original outright, so
                        # without this the only account of whether a correction
                        # helped is the detector's own -- which is the thing
                        # being checked.
                        was = out / f"prescan{number:02d}-before.tif"
                        tiff.write(str(was), frame.prescan_before)
                        record["prescan_before"] = was.name
                    # Every number here is optional. `registration` abstains on
                    # a loaded strip -- and once it says so honestly rather than
                    # returning a fallback zero, these keys go missing. Formatting
                    # a None with `:+.2f` raises, and it would raise in the middle
                    # of a walk, after the scanner time had been spent.
                    offset = r.get("offset_mm")
                    said = "offset --" if offset is None else f"offset {offset:+.2f} mm"
                    print(f"picture {number}: contrast {r.get('contrast')}, "
                          f"x{r.get('x0')}..{r.get('x1')}, {said}"
                          + (f", SHORT BY {short:.2f} mm -- the film has drifted"
                             if short and short > 0.85 else ""))
                    fix = r.get("correction")
                    if fix:
                        aimed = fix.get("decision_mm")
                        print(f"    aim: {fix.get('outcome')}"
                              + ("" if aimed is None else f" {aimed:+.2f} mm")
                              + (f" ({fix['ensemble'].get('chose')})"
                                 if fix.get("ensemble", {}).get("chose") else "")
                              + (f" -- {fix['reason']}" if fix.get("reason")
                                 else ""))
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
                        # `paths`, plural. It was `path` until 2026-09-09, when
                        # FrameWriter grew a second destination and this call site
                        # was not updated with it -- so `job.get("paths")` was None,
                        # the write loop never ran, nothing raised, and the line
                        # below went on naming a file that was not there. No roll
                        # scanned from the command line produced a TIFF for twelve
                        # days. The test was updated instead of the tool, which is
                        # how it stayed green.
                        paths=[path],
                        dpi=args.dpi,
                        image=frame.image,
                        # The uncorrected pixels, which is what the library stores.
                        # Without this the entry holds the corrected image while
                        # its record says raw, and `library.corrected()` shades it
                        # a second time. `session.py:1110` has always passed this;
                        # this tool never did.
                        raw_image=frame.raw_image,
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

    except Exception as exc:                              # noqa: BLE001
        # Recorded rather than raised: the frames already scanned are
        # worth filing and the manifest is worth finishing. The exit
        # status says it went wrong.
        trouble = exc
        print(f"the roll stopped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    # Only now, with the device closed: the last frame or two may still be
    # gzipping, and that is exactly the work that must not happen with an open
    # session.
    #
    # Reached through a `finally` around the whole scanning block, so it runs
    # whatever came out of it. Without that, an exception the roll loop does
    # not catch unwinds straight past here and the frames already queued die
    # unfiled -- scanner time turned into nothing, with no message. That is
    # structural; widening the roll's except tuple only moves the next one.
    # `ScanSession._run` has had this shape all along, which is why the window
    # never lost a frame this way.
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
    if trouble is not None:
        manifest["stopped"] = f"{type(trouble).__name__}: {trouble}"
    checkpoint()

    print(f"\n{scanned} scanned, {failed} failed, "
          f"{manifest['duration_s']/60:.1f} min")
    print(f"manifest: {manifest_path}")
    if failed:
        print("resume a failed picture with --start-at N", file=sys.stderr)
    # Any loss is a non-zero exit. It used to be `failed and not scanned`, so
    # a roll that scanned twenty frames and lost three reported success -- and
    # a caller checking the status is exactly who needs to know it lost three.
    return 1 if trouble is not None or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
