#!/usr/bin/env python3
"""Scan a whole roll or strip, unattended, filing every frame in the library.

    uv run python tools/scan_roll.py --dry-run --frames 6
    uv run python tools/scan_roll.py --dpi 1800 --ir --frames 6 \
        --roll 2026-08-28-gold200 --stock "Kodak Gold 200"

Frame numbers are places on the strip: frame N is where the transport's own
counter reads N-1, counted from where the strip went in. It has been seen
resetting to 0 as a strip goes in -- once, in `full_17_strip` -- so the numbers
are right as long as the strip is in the way it was when it was walked, which
only the operator can see. The roll starts at `--start-at` (frame 1 unless told
otherwise) and winds or advances the film there, from wherever the transport
says it is -- refusing, with nothing calibrated, nothing scanned and nothing
written, if it cannot tell. Shading is then calibrated once, with the strip in,
and reused for the whole roll -- which is what the vendor does, and the reason
a 17-pass session in the captures contains no calibration at all -- and the
film advances between frames.

Every frame goes to disk the moment it exists: a library entry with the raw
bytes, the shading reference and the CCD mask beside the pixels, plus a
`roll.json` manifest rewritten after each one. A roll takes hours, and a crash
two hours in should cost the frame it was on, not the roll -- `--start-at`,
with the roll's `--roll` or `--out`, resumes from the manifest. Without either
a run is a new roll in a folder of its own, so the tool names the folder when
it says how to resume.

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

from rps7200 import preview, tiff
from rps7200.console import DeferredInterrupt, use_utf8_stdout
from rps7200.direct import (
    METER_EACH,
    METER_MODES,
    DirectScanner,
    supports_infrared,
)
# The class itself, for checks made before any scanner is opened. Not the
# `DirectScanner` name below, which tests replace with a stand-in factory.
from rps7200.direct import DirectScanner as _Driver
from rps7200.library import FilmNotes
from rps7200.protocol import FILM_NEGATIVE
# Lives in the package so the GUI and this tool share one writer rather than
# two copies of the same reasoning about not gzipping with the device open.
from rps7200.session import (
    NUMBERING,
    Approved,
    FilmNotPlaced,
    FrameWriter,
    RollManifest,
    earlier_manifest,
    keep_first_numbering,
    manifest_settings,
    plan_nudges,
    prescan_arrangement,
    recorded_roll_name,
    renumbered,
    roll_dir,
    seek,
    walk_shift,
    walked_prescans,
)
from rps7200.session import BACKLASH_COMMANDS as _BACKLASH_COMMANDS
from rps7200.session import raw_bytes_disagree, roll_frame_label, roll_membership
from rps7200.session import rewind as _rewind
from tools import frame_edges  # noqa: E402  (repo root is on the path above)


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
                    help="start at frame N of the strip, winding the film "
                         "there first from wherever it is. Frame 1 is where "
                         "the transport's counter reads 0, counted from where "
                         "the strip went in (it has been seen resetting to 0 "
                         "as a strip goes in, once); the frames are numbered "
                         "by their place on the strip, so a roll resumed with "
                         "N files its frames under the same numbers as "
                         "before, as long as the strip went back in the same "
                         "way -- which only you can see")
    ap.add_argument("--rewind", type=int, default=0,
                    help="wind the film back this many frames before doing "
                         "anything else, one frame at a time, checking each "
                         "one landed. With --frames 0 it rewinds and stops. "
                         "A roll no longer needs it: --start-at goes to its "
                         "frame from wherever the film is.")
    ap.add_argument("--nudge", type=float, default=0.0,
                    help="move the film this many mm before starting, after "
                         "any --rewind. The window's fine-adjust buttons do "
                         "the same thing; here it is for setting a strip "
                         "deliberately badly, to see the correction work "
                         "against something worth correcting.")
    ap.add_argument("--prescan-dpi", type=int, default=None,
                    help="resolution of the survey prescan (default 300; with "
                         "--approved, the one its walk was made at, and "
                         "another is refused). A "
                         "commissioned scan must use the same one its "
                         "positions were set on: `measure_shift_mm` resamples "
                         "a mismatched reference at about half the "
                         "confidence, under the floor, so every frame would "
                         "read unverified and nothing would move. Frame edges "
                         "are read at 300 dpi only, so --correct is refused "
                         "at any other on a film they are read on.")
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
                    help="name for this roll (default: the date and time, "
                         "new for every run). Made safe to be a folder name, "
                         "as the window makes it. To resume a roll, give the "
                         "name it was given -- the folder under rolls/ -- "
                         "with --start-at")
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


#: Re-exported so the name still resolves here. It lives in `rps7200.session`
#: now, beside the rewind that spends it, because two copies of a measured
#: number drift and this one is load-bearing.
BACKLASH_COMMANDS = _BACKLASH_COMMANDS


def rewind(scanner: DirectScanner, frames: int) -> int | None:
    """The session's checked rewind, with this tool's own reporting.

    The algorithm moved to `rps7200.session` so the window shares it -- it was
    the window's copy that drifted, queuing an unchecked `Move` and then
    running the roll over frames it had mis-numbered. This keeps the CLI's
    split: progress on stdout, the give-up line on stderr, where a caller
    redirecting one still sees the other.
    """
    def say(message: str) -> None:
        if message.lstrip().startswith("stopped after"):
            print(message, file=sys.stderr)
        else:
            print(message, flush=True)

    return _rewind(scanner, frames, say=say)


def hold_from_walk(folder: Path) -> tuple[dict[int, Approved], dict]:
    """The positions an earlier walk's strip proposes, ready to be held to.

    ``folder`` is a roll directory from a previous ``--dry-run`` walk: its
    ``prescanNN.tif`` are the pictures the positions were measured on, and they
    become the references the roll correlates each fresh prescan against.

    This is the window's contact-sheet path with the window taken off. It calls
    the same `tools/frame_edges.propose_centred` the sheet seeds itself from --
    each frame centred between the edges the detector reads, as the walk's
    film -- and builds
    the same `session.Approved` the sheet hands to a commissioned scan, so what
    runs here is what runs there -- which is the point, because a probe that
    agrees with itself proves nothing about the path an operator uses.

    An offset is **relative to where the film sat when that frame was
    surveyed**, never an absolute coordinate: a sub-frame move does not touch
    the frame counter, so there is no such coordinate to name. That is also
    what makes this survive an imprecise rewind -- the reference anchors it, so
    the film is driven to "where the walk saw this frame, plus the correction"
    however exactly the transport came back.

    The frames are keyed by their place on the strip, as the roll numbers
    them, and read from the walk's own records the way the window's contact
    sheet reads them -- `session.walked_prescans`, one reader for both -- never
    from the file names in the folder. A walk made before frame numbers meant
    that named its prescans from wherever it started (`registration-F` calls
    the counter's 14 its frame 1), and a second walk into the same folder
    leaves the first one's extra prescans beside its own, named by the other
    walk's count: `rolls/2026-09-23` was held as frames 6 to 11 here while the
    window showed it as 6 and 7, and two of those six were one picture.
    """
    manifest: dict = {}
    for name in ("survey.json", "roll.json"):
        # The walk first, as the window reads it; a roll's own manifest only
        # where there is no walk, and it lists no prescans unless it was one.
        if (folder / name).exists():
            try:
                manifest = json.loads(
                    (folder / name).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SystemExit(f"{folder / name} cannot be read: {exc}")
            break
    # Each un-turned into the film's own orientation first, by the pair its
    # file was written with, as the window's `read_survey` does. The window
    # writes a walk's prescans arranged the way the screen had them, and they
    # were used here as they lay on disk -- a walk made turned or mirrored was
    # handed to the detector, and to the hold as references, the wrong way
    # round.
    frames = [(number, preview.unorient(tiff.read(str(path)),
                                        *prescan_arrangement(manifest, record)))
              for number, path, record in walked_prescans(folder, manifest,
                                                          say=print)]
    if not frames:
        raise SystemExit(f"{folder}'s walk lists no prescans that are still "
                         "there, so there is nothing to propose positions from")

    # The walk's settings as the window reads them (`manifest_settings`): the
    # window writes them at the top level and inside `settings`, this tool
    # inside `settings` only. `prescan_resolution` is the one that matters:
    # the roll prescans at it, so a fresh pass correlates against these
    # references at their own scale. `--prescan-dpi` was used instead, 300
    # unless told, and a walk made at 600 dpi in the window was held against
    # 300 dpi passes -- every frame unverified, nothing moved, exit 0.
    settings = manifest_settings(manifest)
    film = settings.get("film") or FILM_NEGATIVE
    try:
        walked_at = int(settings["prescan_resolution"])
    except (KeyError, TypeError, ValueError):
        walked_at = None            # a walk from before it was recorded
    offsets, notes = frame_edges.propose_centred(frames, film=film)
    held = {
        n: Approved(number=n, offset_mm=float(offsets[n]), reference=im,
                    source=(notes.get(n) or {}).get("source") or "none")
        for n, im in frames if n in offsets
    }
    return held, {"offsets": {n: round(v, 4) for n, v in offsets.items()},
                  "sources": {n: (notes.get(n) or {}).get("source")
                              for n in offsets},
                  "walked": len(frames), "from": str(folder),
                  "prescan_resolution": walked_at}


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
    # Everything knowable before the device is opened is checked here: the
    # roll calibrates and meters before its first frame, and a refusal after
    # that has spent minutes on what these lines say at once.
    for flag, value in (("--dpi", args.dpi), ("--prescan-dpi", args.prescan_dpi)):
        if value is not None and value <= 0:
            ap.error(f"{flag} must be positive, got {value}")
    if args.frames is not None and args.frames < 0:
        ap.error(f"--frames must not be negative, got {args.frames}")
    if args.start_at < 1:
        ap.error(f"--start-at counts frames from 1, got {args.start_at}")
    if (not args.no_shading and not args.dry_run
            and not _Driver.correctable_at(args.dpi)):
        ap.error(
            f"--dpi {args.dpi} cannot be shading-corrected on this scanner: its "
            f"calibration never gives a reference wider than "
            f"{_Driver.MAX_SHADING_COLUMNS} columns, so every frame would "
            f"be refused. Scan at 3600 dpi or below.")

    held: dict[int, Approved] = {}
    held_note: dict = {}
    if args.approved:
        # Before the device is opened: a folder that cannot be read should cost
        # nothing, and the scanner should never be left open waiting on a file.
        held, held_note = hold_from_walk(args.approved)
        # Its prescans are the references, so the roll prescans at their
        # resolution -- the pin the window's `_survey_predpi` holds a
        # commissioned scan to. A different one asked for is refused rather
        # than obeyed: it is the run that ends with every frame unverified.
        walked_at = held_note.get("prescan_resolution")
        if walked_at is not None:
            if args.prescan_dpi is not None and args.prescan_dpi != walked_at:
                ap.error(
                    f"--prescan-dpi {args.prescan_dpi} with --approved "
                    f"{args.approved}: its walk was prescanned at {walked_at} "
                    "dpi, and its prescans are what each frame is held to. At "
                    "another resolution `measure_shift_mm` reads them at about "
                    "half the confidence, under its floor, so every frame "
                    "would read unverified and nothing would move. Leave "
                    f"--prescan-dpi out, or give {walked_at}.")
            args.prescan_dpi = walked_at
            print(f"prescanning at {walked_at} dpi, as the walk in "
                  f"{args.approved} was")
        counts: dict[str, int] = {}
        for source in held_note["sources"].values():
            counts[source] = counts.get(source, 0) + 1
        print(f"holding {len(held)} frame(s) to positions from "
              f"{args.approved}: "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    if args.prescan_dpi is None:
        args.prescan_dpi = 300
    # Said before the device opens, not frame by frame after: at a prescan
    # resolution the frame-edge detector cannot read, every frame is refused
    # and the roll looks centred without being so. `--correct` is refused --
    # it would correct nothing, and an unattended roll is exactly where
    # nobody reads the per-frame "left as it came". A walk is only warned
    # about: its prescans are still a survey of the strip.
    unread = frame_edges.unread_at(args.prescan_dpi, args.film)
    if unread and args.correct:
        ap.error(f"--correct with --prescan-dpi {args.prescan_dpi}: {unread}")
    if unread and (args.dry_run or args.correct_dry_run or args.approved):
        print(f"warning: {unread}", file=sys.stderr)

    # The folder the window's rolls use for the same name (`roll_dir`): made
    # safe to be one folder, and a new name of its own when none is given.
    # It was `rolls/<today>`, the folder every unnamed walk of the window's
    # went into too -- so a run from here replaced that day's walk. The label
    # its frames carry is the one the folder already records, as the
    # window's is, so a roll added to keeps calling itself what it did.
    if args.out:
        out = Path(args.out)
        roll_name = args.roll or recorded_roll_name(out) or out.name
    else:
        out = roll_dir("rolls", args.roll or "")
        roll_name = recorded_roll_name(out) or out.name
    # A dry run and the scan that follows it share a directory, so they must
    # not share a file: the record of what was walked is what says which frames
    # are worth scanning, and writing the scan over it loses that.
    manifest_path = out / ("survey.json" if args.dry_run else "roll.json")

    manifest = {
        "roll": roll_name,
        # The frame numbers below are places on the strip, as the window's
        # manifests say too; a file without this counted from wherever its
        # roll happened to start.
        "numbering": NUMBERING,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "dpi": args.dpi, "infrared": args.ir, "meter": args.meter,
            "film": args.film, "dry_run": args.dry_run,
            "start_at": args.start_at, "frames": args.frames,
            # Written because the window pins a commissioned scan's prescan to
            # whatever the survey walked at, and reads that pin from here
            # (`tools/gui.py` `_survey_predpi`). That was not true when it was
            # written: the window read the top level and this is inside
            # `settings`, so every walk made here opened there with the pin
            # inert. `manifest_settings` merges the two now, which is what
            # makes the sentence above correct. Without the key the pin is
            # inert and a mismatch silently resamples the reference, which
            # halves `measure_shift_mm`'s confidence -- 93.5 to 47.4 measured,
            # against a floor of 55. Every frame would then read `unverified`,
            # nothing would move, and the run would be a loss with no error.
            "prescan_resolution": args.prescan_dpi,
        },
        "held": held_note,
        "frames": [],
    }

    #: The one writer of the manifest, from this thread and the writer's; see
    #: `session.RollManifest`. Made once the roll is placed, and not before.
    record_of: RollManifest | None = None

    started = time.monotonic()
    scanned = failed = 0
    #: Whether the film reached the roll's first frame and the calibration
    #: after it succeeded, and so whether this run has a manifest at all.
    #: Nothing is written before that, which is `ScanSession._roll`'s
    #: "before the directory, before the manifest": the default roll name
    #: was today's date, the name the window's walks used, and the manifest
    #: used to be written before the device was even opened -- so a seek that
    #: refused replaced that day's survey.json with an empty one, and the walk
    #: it described was gone. A folder named with --roll or --out can still
    #: hold a walk.
    placed = False

    writer = FrameWriter()
    # RPS7200_DEBUG decides, as everywhere else. This tool files its own
    # frames and claims each of those passes (`debug_claim` below), so debug
    # filing leaves them out rather than writing every frame twice -- 43 GB of
    # duplicate on a 38-frame roll at 7200 dpi, which is why this used to say
    # debug=False and so filed none of the prescans, probes and holds either.
    # Wrapped so `writer.finish()` below runs whatever comes out of this.
    # An exception the roll loop does not catch used to unwind straight
    # past it, and the frames already queued died unfiled -- scanner time
    # turned into nothing, with no message.
    trouble: BaseException | None = None
    # Ctrl-C finishes the frame in flight and stops there; a second one
    # aborts. Abandoning a read wedges the scanner, and the frames queued for
    # filing used to die with it.
    interrupt = DeferredInterrupt()
    try:
        with interrupt, DirectScanner(verbose=args.verbose, debug=None) as s:
            info = s.inquiry()
            print(f"{info.vendor} {info.product}, firmware {info.firmware}")
            print(f"roll {roll_name} -> {out}\n")

            # The lamp first, with status queries only: TEST UNIT READY, and
            # REQUEST SENSE when one is refused -- what `tools/hold_probe.py`
            # and `tools/transport_truth.py` send before they move the film.
            # `DirectScanner.wait_warm` says the scanner answers NOT READY
            # to every command while the lamp warms, READ_STATE included;
            # that is its docstring's premise, not something measured here.
            # If it holds, a roll that asked the counter from cold would hear
            # nothing and refuse, and a rewind would read "did not move" --
            # shown on a test double, never seen on the scanner. The
            # calibration waited the lamp out when it ran first, and it
            # cannot run first: see below.
            s.wait_warm()
            if args.rewind:
                landed = rewind(s, args.rewind)
                if landed is None:
                    print("refusing to go on: the film is not where it was "
                          "asked to be, so every frame after this would be "
                          "mis-numbered", file=sys.stderr)
                    return 1
                print(f"rewound to frame {landed + 1}")
                print()
            if args.frames != 0:
                # To the first frame, by the transport's own counter, before
                # the nudge -- so an offset set on purpose is the last thing
                # the film does before the roll, and the whole-frame moves do
                # not happen on top of it. The same helper the window's roll
                # uses, so the two cannot disagree about where frame N is.
                try:
                    seek(s, max(0, args.start_at - 1),
                         say=lambda m: print(m, flush=True))
                except FilmNotPlaced as exc:
                    print(f"refusing to go on: {exc}", file=sys.stderr)
                    return 1
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

            # After the seek, never before it. The seek is what refuses a
            # counter no strip can have -- `full_17_strip` read a stale 72
            # before its strip went in (`docs/protocol.md` section 9) -- and
            # in front of it this spent 3-4 minutes calibrating what may have
            # been an empty transport before refusing: the state CLAUDE.md
            # says preceded a wedge ("Calibrate with the film loaded"). With
            # the film placed it measures the band below the film,
            # `(0, 3431, 10343, 6888)`, strip in, as CyberView does. The
            # window's Roll seeks before anything calibrates, too.
            #
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
            calibrate(s, args)

            # The film is on the roll's first frame and the reference is in
            # hand, so there is a roll to record -- and not before. See
            # `placed`: a calibration that fails leaves the folder as it was.
            out.mkdir(parents=True, exist_ok=True)
            if not args.dry_run:
                # Carried forward, as the window's rolls are: this run's frames
                # replace the ones it takes again and the rest stay. The
                # docstring has promised a resume since `--start-at` existed,
                # and the manifest was written afresh over the earlier run's
                # -- the one record of which frames it had scanned. A walk is
                # its own: a new one replaces the last, which stays beside it
                # as survey.json.bak.
                earlier = earlier_manifest(manifest_path, say=print)
                keep_first_numbering(manifest_path, earlier)
                earlier = renumbered(earlier, fallback=walk_shift(out),
                                     say=print)
                manifest["frames"] = list(earlier.get("frames") or [])
                if manifest["frames"]:
                    print(f"adding to {manifest_path}: "
                          f"{len(manifest['frames'])} frame(s) from earlier "
                          "runs are kept, and a frame taken again replaces "
                          "its own record")
            elif manifest_path.exists():
                # Said, since nobody is asked: --roll or --out named a folder
                # that holds a walk, and this one replaces it.
                print(f"replacing the walk in {manifest_path}; the old one is "
                      f"kept beside it as {manifest_path.name}.bak")
            # Said on stderr when a frame's rewrite of it is refused, and
            # the roll goes on: the next one writes it whole.
            record_of = RollManifest(
                manifest_path, manifest,
                say=lambda m: print(m, file=sys.stderr))
            record_of.write()
            placed = True

            for frame in s.scan_roll(
                frames=args.frames,
                resolution=args.dpi,
                infrared=args.ir,
                fast_infrared=args.fast_ir and args.ir,
                film=args.film,
                meter=args.meter,
                prescan_resolution=args.prescan_dpi,
                # The film is on this frame now; the roll counts from it, so
                # an index is a transport position and a number is that + 1.
                first_index=max(0, args.start_at - 1),
                keep_raw=bool(args.library),
                max_failures=args.max_failures,
                dry_run=args.dry_run,
                # Keyed by the roll's own index, as `session.py` keys it -- a
                # place on the strip. The offsets themselves stay relative to
                # where the walk saw each frame; only the key is absolute.
                approved={n - 1: a for n, a in held.items()},
                correct=args.correct,
                correct_dry_run=args.correct_dry_run,
                # the window's detector, so --correct reads edges as it does
                edge_reader=frame_edges.walk_reader,
                should_stop=interrupt.requested,
                # Every pass of the roll, prescans and metering probes
                # included. `--no-shading` used to skip only the calibration
                # above, so the first prescan -- still asking for a correction
                # -- calibrated inside itself, on the path that stalls.
                shading=not args.no_shading,
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
                    # True once the writer has filed it, and not before; see
                    # `session.RollManifest`.
                    "done": False,
                }
                submitted = False

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
                    # And filed, with its raw bytes, as the window files a
                    # walk's prescans. A walk from here used to leave only the
                    # corrected TIFF above: nothing that could be re-decoded,
                    # and the references `--approved` holds frames to later.
                    if (args.library and frame.prescan is not None
                            and frame.raw_prescan is not None):
                        capture = dict(s.capture_record())
                        meta = dict(frame.prescan_meta or {},
                                    roll_membership=roll_membership(
                                        roll_name, number, "prescan", out))
                        if raw_bytes_disagree(frame.raw_prescan.shape,
                                              capture.get("raw_layout"), meta):
                            capture.update(raw=None, raw_layout=None)
                        s.debug_claim(frame.raw_prescan)
                        writer.submit(
                            number=number, paths=[], dpi=args.prescan_dpi,
                            image=frame.prescan, raw_image=frame.raw_prescan,
                            meta=meta, prescan=None, library=args.library,
                            inquiry=info, capture=capture,
                            tags=sorted({*args.tags, "roll", "prescan", roll_name}),
                            film=FilmNotes(stock=args.stock, process=args.process,
                                           frame=roll_frame_label(roll_name, number),
                                           notes=args.notes),
                        )
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
                    # `file` is written when the writer has written it, by
                    # `on_filed` below. It used to be set here, naming a TIFF
                    # nothing had written yet -- and after a crash or a full
                    # disk, one nothing ever would.
                    record["shape"] = list(frame.image.shape)
                    record["duration_s"] = frame.meta.get("duration_s")
                    record["exposure"] = frame.meta.get("exposure")

                    # capture_record() is read here, on this thread, before the next
                    # scan overwrites last_raw. Everything after it belongs to the
                    # writer and happens while the scanner is busy again.
                    if args.library and frame.raw_image is not None:
                        s.debug_claim(frame.raw_image)
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
                        meta=dict(frame.meta, roll_membership=roll_membership(
                            roll_name, number, "frame", out)),
                        prescan=frame.prescan,
                        # Which way the prescan was read; it has no raw bytes
                        # of its own to say so in the entry.
                        prescan_meta=frame.prescan_meta,
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
                            # of every other. The window's format, so the window
                            # can find these entries from the roll.
                            frame=roll_frame_label(roll_name, number),
                            notes=args.notes,
                        ),
                        on_filed=(lambda entry, error, written, n=number,
                                  p=path: record_of.filed(
                                      n, entry, error,
                                      **({"file": p.name} if p in written
                                         else {}))),
                    )
                    submitted = True
                    print(f"picture {number}: {path} {frame.image.shape} "
                          f"in {frame.meta.get('duration_s')}s")

                record_of.record(record, awaiting=submitted)

    except BaseException as exc:                          # noqa: BLE001
        # Recorded rather than raised: the frames already scanned are
        # worth filing and the manifest is worth finishing. The exit
        # status says it went wrong. BaseException, because a second Ctrl-C
        # (KeyboardInterrupt) and a SIGTERM-turned-SystemExit are exactly the
        # exits that used to skip `writer.finish()` and lose queued frames.
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

    if not placed:
        # Stopped before the roll began -- the device would not open, the
        # lamp never warmed, or the calibration after the seek failed -- so
        # nothing was scanned and there is no roll to record. A manifest
        # already in this folder is left as it was, for the reason `placed`
        # gives.
        print("nothing was scanned, and nothing was written", file=sys.stderr)
        return 1

    manifest["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["duration_s"] = round(time.monotonic() - started, 1)
    if trouble is not None:
        manifest["stopped"] = f"{type(trouble).__name__}: {trouble}"
    elif interrupt.requested():
        manifest["stopped"] = "stopped by Ctrl-C after the frame in flight"
    # Placed, so it was made. Not a traceback when the disk refuses it: the
    # manifest says so on stderr, and the exit status says it went wrong.
    saved = record_of is None or record_of.save()

    print(f"\n{scanned} scanned, {failed} failed, "
          f"{manifest['duration_s']/60:.1f} min")
    print(f"manifest: {manifest_path}")
    if failed:
        # With the folder named. An unnamed roll's folder is new every run,
        # so "--start-at N" alone started another roll beside this one, and
        # never read the manifest that says what this one has done.
        again = (f"--out {_quoted(out)}" if args.out
                 else f"--roll {_quoted(out.name)}")
        print(f"resume a failed picture with {again} --start-at N",
              file=sys.stderr)
    # Any loss is a non-zero exit. It used to be `failed and not scanned`, so
    # a roll that scanned twenty frames and lost three reported success -- and
    # a caller checking the status is exactly who needs to know it lost three.
    return 1 if trouble is not None or failed or not saved else 0


def _quoted(value) -> str:
    """A path or name as it would be typed, quoted where a shell would split it.

    Double quotes, which bash, PowerShell and cmd.exe all read the same way.
    """
    text = str(value)
    return f'"{text}"' if not text or any(c.isspace() for c in text) else text


if __name__ == "__main__":
    raise SystemExit(main())
