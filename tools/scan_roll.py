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
two hours in should cost the frame it was on, not the roll -- given the roll's
`--roll` or `--out`, a run adds to its manifest, and `--only` with `--start-at`
takes again just the frames it lists; without `--only` a run scans every frame
from `--start-at` on, finished or not. Without `--roll` or `--out` a run is a
new roll in a folder of its own, so the tool names the folder, and the frames
left, when it says how to resume.

Start with `--dry-run`. It prescans and advances only, so it walks the whole
strip in minutes -- a calibration and some 25 s a frame -- and shows where
each picture sits before three hours are committed to scanning them. Every run
says how long it should take before it opens the scanner, and when to run it
in the background: a foreground command killed at 10 minutes abandons its
read, which wedges the scanner. Its manifest is `survey.json`, beside the
`prescanNN.tif` it measured each frame on, so the walk survives the roll that
follows it into the same directory.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import export, library, locks, session, storage
from rps7200.awake import KeepAwake
from rps7200.console import DeferredInterrupt, use_utf8_stdout
from rps7200.direct import (
    METER_EACH,
    METER_MODES,
    METER_ONCE,
    DirectScanner,
    debug_from_env,
    supports_infrared,
)
# The class itself, for checks made before any scanner is opened. Not the
# `DirectScanner` name below, which tests replace with a stand-in factory.
from rps7200.direct import DirectScanner as _Driver
from rps7200.framing import MAX_HOLD_MOVES, say_hold
from rps7200.library import FilmNotes
from rps7200.mono import MONO_CHANNEL, MONO_CHOICES, wants_mono
from rps7200.protocol import FILM_NEGATIVE, MM_PER_UNIT, say_units, units
# Lives in the package so the GUI and this tool share one writer rather than
# two copies of the same reasoning about not gzipping with the device open.
from rps7200.session import (
    COPY_FAILED,
    FINE_MAX_MM,
    NUMBERING,
    Approved,
    FilmNotPlaced,
    Filing,
    FrameWriter,
    HeldOpen,
    RollManifest,
    approved_shift,
    debug_filing_into,
    earlier_manifest,
    filing_interrupt,
    keep_first_numbering,
    manifest_settings,
    name_cut,
    plan_nudges,
    read_approved,
    read_manifest,
    read_walk_prescan,
    recorded_roll_name,
    reference_refused,
    renumbered,
    roll_dir,
    say_leftovers,
    say_reused,
    seek,
    walk_shift,
    walked_prescan_entries,
    walked_prescans,
)
from rps7200.session import BACKLASH_COMMANDS as _BACKLASH_COMMANDS
from rps7200.session import (
    answering,
    bytes_are_another_pass,
    queued,
    raw_bytes_disagree,
    roll_frame_label,
    roll_membership,
)
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
    ap.add_argument("--only", type=_frame_list, default=None,
                    metavar="N[,N...]",
                    help="scan only these frames, by their place on the "
                         "strip, advancing past the rest -- what a resume "
                         "asks for: the frames an earlier run left unfinished, "
                         "which it names when it ends. The window's chosen "
                         "frames are the same thing")
    ap.add_argument("--rewind", type=int, default=0,
                    help="wind the film back this many frames before doing "
                         "anything else, one frame at a time, checking each "
                         "one landed. With --frames 0 it rewinds and stops. "
                         "A roll no longer needs it: --start-at goes to its "
                         "frame from wherever the film is.")
    ap.add_argument("--nudge", type=float, default=0.0, metavar="UNITS",
                    help="move the film this far before starting, after any "
                         "--rewind, in units of the adjustment parameter -- "
                         "the unit the window's fine adjust shows; one "
                         "command of param 1 is 2.84, a frame about 350. The "
                         "window's fine-adjust buttons do the same thing; "
                         "here it is for setting a strip deliberately badly, "
                         "to see the correction work against something worth "
                         "correcting.")
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
                         "whole strip, and every walked frame held to its own "
                         "-- one the detector left unplaced where the walk saw "
                         "it, and none further than one command moves -- the "
                         "same path the window's contact sheet drives, "
                         "runnable without it. With --dry-run this moves the "
                         "film and costs prescans rather than scans.")
    ap.add_argument("--positions", type=Path, default=None, metavar="FILE",
                    help="with --approved: an approved.json -- the one the "
                         "window wrote when it commissioned a roll, or the "
                         "roll folder holding it -- whose placed frames are "
                         "held to its positions rather than to the ones "
                         "proposed from the walk, frame by frame, each still "
                         "no further than one command moves. A frame it does "
                         "not place is proposed as without it. Its numbers "
                         "must be places on the strip (\"numbering\": "
                         "\"strip\"), or a walk beside it must say how they "
                         "were counted; otherwise it is refused before the "
                         "scanner opens. For replaying, here, the positions "
                         "set on the window's sheet.")
    ap.add_argument("--correct", action="store_true",
                    help="nudge the film back into registration between frames, "
                         "using the calibrated sub-frame move. Off by default: "
                         "the vendor does not do this during a roll either, and "
                         "a strip that does not drift gains nothing from it")
    ap.add_argument("--correct-dry-run", action="store_true",
                    help="measure registration and log the correction that "
                         "would be sent, without moving the film")
    ap.add_argument("--dry-run", action="store_true",
                    help="prescan and advance only -- no full scans. A walk "
                         "still calibrates first, so a 6-frame strip is about "
                         "6 minutes; the run says its own estimate before it "
                         "opens the scanner")
    ap.add_argument("--meter", choices=METER_MODES, default=METER_EACH,
                    help="'each' re-meters every frame, as CyberView does; "
                         "'once' meters the first picture and holds it, which "
                         "keeps the roll internally consistent and saves ~45 s "
                         "a frame; 'none' scans at the device's own settings")
    ap.add_argument("--film", default=None,
                    choices=["negative", "positive", "kodachrome", "bw"],
                    help="what is in the transport, for metering (default: "
                         "negative; with --approved, the film its walk was "
                         "made on)")
    ap.add_argument("--mono", dest="mono", action="store_true", default=None,
                    help="deliver each frame as one channel. On by default for "
                         "--film bw, as in tools/scan.py and the window: a "
                         "black and white scan is an RGB scan on this "
                         "hardware, and a consumer cannot tell it from colour "
                         "negative by its pixels. The library keeps all three")
    ap.add_argument("--no-mono", dest="mono", action="store_false",
                    help="deliver all three channels even for --film bw")
    ap.add_argument("--mono-channel", default=MONO_CHANNEL,
                    choices=list(MONO_CHOICES),
                    help="what a monochrome frame carries (default: "
                         "%(default)s)")
    ap.add_argument("--reference", default="calibration/shading.npz")
    ap.add_argument("--reuse", action="store_true",
                    help="load the cached shading reference instead of "
                         "calibrating (saves 3-4 min, but the reference "
                         "describes the sensor at the exposure that measured it)")
    ap.add_argument("--no-shading", action="store_true",
                    help="skip calibration entirely; scans come back striped")
    ap.add_argument("--film-loaded", action="store_true",
                    help="the film is in the transport, so the calibration "
                         "may run without asking. Without it the tool asks, "
                         "and refuses where nobody can answer")
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


def _frame_list(text: str) -> tuple[int, ...]:
    """``--only``'s frame numbers: places on the strip, counted from 1."""
    try:
        numbers = tuple(sorted({int(part) for part in text.replace(",", " ").split()}))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r}: frame numbers, separated by commas") from None
    if not numbers or numbers[0] < 1:
        raise argparse.ArgumentTypeError(
            f"{text!r}: frames are counted from 1")
    return numbers


#: Where a roll's folder is made when --out does not name one: the window's
#: rolls folder, relative to where the tool is run, as `roll_dir` takes it.
ROLLS = "rolls"


def calibrates(args: argparse.Namespace) -> bool:
    """Whether `calibrate` will measure a reference, not load or skip one.

    `ensure_shading`'s own decision, asked before it is made: by the
    estimate, the refusals before the device opens and the Ctrl-C check in
    front of the calibration. One place, because it was written out five
    times, and a copy that drifted would say "after the calibration" when
    none ran. Once it has run, what it did is in `calibrate`'s answer.
    """
    return not args.no_shading and not (args.reuse
                                        and Path(args.reference).exists())


def longest_paths(args: argparse.Namespace, out: Path, roll_name: str,
                  spool: Path | None) -> list[Path]:
    """The longest path this run can write, each from the name it will
    really use and its real bound: the roll's records and a frame's file,
    written beside, a library entry with its record's temporary name, and
    debug filing's spool."""
    number = session.LAST_PLAUSIBLE_POSITION + 1
    resolution = args.prescan_dpi if args.dry_run else args.dpi
    infrared = bool(args.ir) and not args.dry_run
    paths = [session.manifest_bound(out), export.temp_name(out / (
        f"prescan{number:02d}-before-{number}.tif" if args.dry_run
        else f"frame{number:02d}.tif"))]
    if args.library:
        notes = FilmNotes(stock=args.stock, process=args.process,
                          frame=roll_frame_label(roll_name, number),
                          notes=args.notes)
        paths.append(session.entry_bound(args.library, library.entry_id(
            {"resolution_dpi": resolution, "channels": 4 if infrared else 3},
            notes, datetime.now(timezone.utc))))
    if spool is not None:
        paths.append(session.spool_bound(spool))
    return paths


def space(args: argparse.Namespace, frames: int, out: Path,
          spool: Path | None) -> list:
    """Where ``frames`` of this run's frames go, and at most how much each
    place takes: a walk's prescans (`session.walk_space`), or a roll's
    frames, one pass each (`session.roll_space`)."""
    if args.dry_run:
        return session.walk_space(frames, args.prescan_dpi,
                                  library_root=args.library or None,
                                  roll_folder=out, spool=spool)
    return session.roll_space(frames, args.dpi, args.ir,
                              library_root=args.library or None,
                              roll_folder=out, spool=spool,
                              prescan_resolution=args.prescan_dpi)


def calibrate(scanner: DirectScanner, args: argparse.Namespace) -> dict:
    """Once per roll, as the vendor does once per power-on.

    Returns `ensure_shading`'s answer, so a caller says what happened --
    ``action`` is ``"calibrated"``, ``"loaded"`` or ``"skipped"`` -- from
    the driver rather than by deciding again.
    """
    if calibrates(args):
        print("calibrating (about 3-4 minutes, once for the whole roll) ...",
              flush=True)
    result = scanner.ensure_shading(Path(args.reference), reuse=args.reuse,
                                    skip=args.no_shading)
    print(result["summary"])
    return result


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


def positions_from(path: Path) -> dict:
    """The frames an `approved.json` places, for ``--positions``.

    Read by the window's own reader, `session.read_approved`, so a frame
    counts as placed here exactly when the reopened sheet would show it
    placed: a position that is not zero, or zero he said was his ("as
    walked"). An untouched frame's zero is no decision -- every ticked frame
    is written with one -- and is left to the proposal, as on the sheet.

    The numbers have to be places on the strip. A file that says so is read
    as it stands; an older one is moved onto the strip's numbers by the walk
    or roll beside it (`session.approved_shift`), as the window moves it; and
    one that neither it nor anything beside it can number is refused. It
    keeps no positions of its own, so a guess would hold each frame to
    another frame's position, and the roll would report it held.

    Returns ``{"file", "shift", "offsets", "sources", "entries"}``, the last
    three keyed by frame number; raises SystemExit, saying why, for a file
    that is not there, cannot be read, cannot be numbered or places nothing.
    All of it before the scanner opens.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "approved.json"
    if not path.is_file():
        raise SystemExit(f"--positions {path}: there is no such file")
    try:
        shift = approved_shift(path, say=print)
    except ValueError as exc:
        raise SystemExit(f"--positions: {exc}")
    if shift is None:
        raise SystemExit(
            f"--positions {path} does not say its frame numbers are places on "
            'the strip ("numbering": "strip"), and no walk or roll beside it '
            "says how they were counted, so its positions cannot be matched to "
            "the strip's frames. Give the approved.json in the roll folder it "
            "was written in, or add \"numbering\": \"strip\" to it if its "
            "numbers are places on the strip.")
    offsets, _turns, _flips, entries, sources = read_approved(
        path, legacy=shift, say=print)
    if not offsets:
        raise SystemExit(
            f"--positions {path} places no frame: none has a position other "
            "than zero, or a zero marked as walked, so every frame would be "
            "held where the walk proposes -- which is --approved without it.")
    return {"file": path, "shift": shift, "offsets": offsets,
            "sources": sources, "entries": entries}


def hold_from_walk(folder: Path, positions: Path | None = None,
                   library_root=None) -> tuple[dict[int, Approved], dict]:
    """The positions an earlier walk's strip proposes, ready to be held to.

    ``folder`` is a roll directory from a previous ``--dry-run`` walk: its
    ``prescanNN.tif`` are the pictures the positions were measured on, and they
    become the references the roll correlates each fresh prescan against.

    ``positions`` is an `approved.json` (`positions_from`) whose placed frames
    win over the proposals, frame by frame, each clamped as a proposal is. It
    changes the targets and nothing else: the references stay the walk's own
    prescans, so a position is still an offset from where this walk saw the
    frame. Each frame's `Approved` names the entry its reference was filed
    as, which the hold records as its `reference_entry` -- without it, a roll
    held from here could not be laid beside its references afterwards
    (`tools/hold_check.py`). The walk's own record names it, by id, whatever
    library this run files into; ``library_root`` is where it is looked for,
    and where the entries of a walk from before records named them are
    found.

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
    walked_from = None
    for name in ("survey.json", "roll.json"):
        # The walk first, as the window reads it; a roll's own manifest only
        # where there is no walk, and it lists no prescans unless it was one.
        if (folder / name).exists():
            # As the window reads it: the version kept beside it when this
            # one does not parse, and refused when that is not a JSON object
            # either. A bare `json.loads` had no fallback, and a manifest
            # that parsed to a list got as far as `walked_prescans`.
            try:
                manifest = read_manifest(folder / name, say=print)
            except ValueError as exc:
                raise SystemExit(str(exc))
            walked_from = name
            break
    # Before the prescans are read: a file that cannot be used should cost
    # nothing, and says so before anything else does.
    chosen = positions_from(positions) if positions is not None else None
    # Each un-turned into the film's own orientation first, by the pair its
    # file was written with, as the window's `read_survey` does. The window
    # writes a walk's prescans arranged the way the screen had them, and they
    # were used here as they lay on disk -- a walk made turned or mirrored was
    # handed to the detector, and to the hold as references, the wrong way
    # round.
    walked = walked_prescans(folder, manifest, say=print)
    # And each on its own: one that cannot be read, or is no longer the file
    # its walk wrote, is made again from the walk's own entry for it or left
    # out and said, and a frame left out is held to nothing -- one bad file
    # stopped the run here with a traceback (`session.read_walk_prescan`).
    frames = []
    for number, path, record in walked:
        image = read_walk_prescan(manifest, number, path, record,
                                  library_root=library_root, say=print,
                                  left="that frame is held to nothing")
        if image is not None:
            frames.append((number, image))
    if not frames:
        raise SystemExit(f"{folder}'s walk lists no prescans that are still "
                         "there and readable, so there is nothing to propose "
                         "positions from")
    # The entry each reference was filed as. The walk's own record names it
    # -- its id, which `RollManifest.amend` writes once the writer has filed
    # it -- and that id is what the hold records and what a position's file
    # is compared with, whatever library this run files into. Both used to
    # come from the library alone (`entry_of` below), so a run whose
    # --library held none of the walk's entries -- a worktree's own, empty
    # `library` -- held every frame naming no reference, and a position set
    # on another picture than the walk's was held to without a word.
    named_by_walk = {n: PureWindowsPath(str(r["prescan_entry"])).name
                     for n, _p, r in walked if r.get("prescan_entry")}
    # Where those entries are, as the window's sheet finds them
    # (`session.walked_prescan_entries`): under the library, by the id the
    # record gives, or, for a walk from before records said, the entries
    # filed while it ran -- the roll beside it bounding that, as on the
    # sheet. That join needs the library; nothing else here does.
    entry_of: dict = {}
    if library_root:
        beside: dict = {}
        if walked_from == "survey.json" and (folder / "roll.json").exists():
            try:
                beside = read_manifest(folder / "roll.json")
            except ValueError:
                beside = {}
        entry_of = walked_prescan_entries(
            folder, [(n, r) for n, _p, r in walked], library_root,
            walk=manifest, roll=beside or None)
    #: Each frame's reference by id, by whichever of the two names it.
    ids = {n: named_by_walk.get(n) or (Path(entry_of[n]).name
                                       if n in entry_of else None)
           for n, _im in frames}

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
    placed = chosen["offsets"] if chosen else {}
    # Every walked frame, as the sheet commissions every ticked one: a frame
    # the detector left unplaced is held at 0 -- where the walk saw it --
    # which is what corrects the rewind's error. It got no Approved here and
    # went unheld. And each offset clamped to what one command delivers, as
    # the sheet's snap clamps it: unclamped, a proposal past it asked the
    # hold loop for a move it could only chain, past what its verification
    # can see. A position from --positions is clamped the same way: it was
    # set on the sheet, which clamps too, but a file can be written by hand.
    held: dict[int, Approved] = {}
    asked: dict[int, float] = {}
    for n, im in frames:
        if n in placed:
            wanted = float(placed[n])
            # Who the file says decided it; a placed frame it says nothing
            # about is his, which is what a position meant before the sheet
            # proposed any (the window's `_merge_kept` reads it so too).
            source = (chosen or {}).get("sources", {}).get(n) or "operator"
        else:
            wanted = float(offsets.get(n, 0.0))
            source = ((notes.get(n) or {}).get("source") or "none"
                      if n in offsets else "none")
        offset = max(-FINE_MAX_MM, min(FINE_MAX_MM, wanted))
        if offset != wanted:
            asked[n] = round(wanted, 4)
        # The entry where the library has it, and the id the walk recorded
        # where it does not: `tools/hold_check.py` looks an id up in the
        # library it is given, which need not be this run's.
        held[n] = Approved(number=n, offset_mm=offset, reference=im,
                           reference_entry=str(entry_of.get(n)
                                               or ids.get(n) or ""),
                           source=source)
    # The film too, as the one the positions above were read on: whether the
    # walk's prescans could be read at all is a question about its film, and
    # `--film` is negative unless told whatever the walk was.
    #
    # Said from `held`, what the roll will be sent to, not from the proposal:
    # read off the proposal, the note left the unplaced frames out and kept
    # the offsets unclamped, so "holding N frame(s)" counted fewer sources
    # than N and the manifest recorded moves the roll never asked for.
    note = {"offsets": {n: round(a.offset_mm, 4) for n, a in held.items()},
            "sources": {n: a.source for n, a in held.items()},
            "walked": len(frames), "from": str(folder),
            "prescan_resolution": walked_at, "film": film,
            # What a clamp took off, by frame: asked for, and not sent.
            "clamped": asked,
            # Frames whose reference nothing names: a walk from before its
            # records said, whose entries the library does not hold either,
            # or a prescan that was never filed. Their holds record none.
            "unnamed": sorted(n for n in held if not ids.get(n))}
    if library_root:
        # Named by the walk and not under this run's library: the frames
        # filed there will lie apart from the pictures they were held to.
        note["not_in_library"] = sorted(n for n in held
                                        if n in named_by_walk
                                        and n not in entry_of)
    if chosen:
        named = {n: PureWindowsPath(str(e)).name
                 for n, e in chosen["entries"].items() if e}
        note.update({
            "positions": str(chosen["file"]),
            "positions_shift": chosen["shift"],
            # Which frames the file decided, and which it places that the
            # walk has no prescan of -- nothing to hold those against.
            "from_positions": sorted(n for n in held if n in placed),
            "not_walked": sorted(n for n in placed if n not in held),
            # A frame whose position was set on another picture than the
            # walk's prescan of it: the folder walked again since, most
            # likely, and an offset from where *that* walk saw the frame.
            # Said, not refused; the reference is still the walk's. By id,
            # the file's against the walk's, so it needs no library.
            "other_reference": {
                n: named[n] for n in held
                if n in placed and n in named and ids.get(n)
                and named[n] != ids[n]},
            # And one whose file names the picture it was set on while
            # nothing names the walk's: the two cannot be compared.
            "unchecked": sorted(n for n in held if n in placed and n in named
                                and not ids.get(n)),
        })
    return held, note


def said_target(number: int, approved: Approved, note: dict) -> str:
    """Where one frame is to be held and who decided it, said before the roll.

    In units, never millimetres (CLAUDE.md); `Approved` keeps what the driver
    is sent.
    """
    by_file = number in (note.get("from_positions") or ())
    whence = (Path(str(note.get("positions"))).name if by_file
              else "proposed from the walk")
    clamped = (note.get("clamped") or {}).get(number)
    return (f"frame {number}: {say_units(approved.offset_mm)}, "
            f"{approved.source} ({whence}"
            + (f"; clamped from {say_units(clamped)}, the most one command "
               "moves" if clamped is not None else "")
            + ")")


def _u(millimetres) -> str:
    """A distance in units, short, for the hold lines: ``+4.2 u``."""
    return "--" if millimetres is None else f"{units(millimetres):+.1f} u"


def hold_line(number: int, hold: dict, failed: bool = False) -> str:
    """One frame's hold as the roll goes, one line, the same shape each time.

    ``hold`` is the record the driver files in the frame's registration
    (`DirectScanner._hold_to_approved`): the target, where the first look
    found the frame (``arrived_units``), where the last one did
    (``final_mm``) and how many moves it took. An outcome other than
    ``held`` is said in words as well (`framing.say_hold`). A value the
    record does not have -- a frame left alone because holding was off, a
    look that could not be believed -- is ``--``.

    ``failed`` is a frame that failed, which was not scanned: the record is
    the hold as far as it got -- ``move``, the step it was taking when a pass
    under it failed, or a hold that finished before the frame's scan did not.
    """
    outcome = str(hold.get("outcome") or "?")
    arrived = hold.get("arrived_units")
    line = (f"hold: frame {number} {outcome} target "
            f"{_u(hold.get('target_mm'))}, arrived "
            + ("--" if arrived is None else f"{float(arrived):+.1f} u")
            + f", now {_u(hold.get('final_mm'))} after "
            f"{int(hold.get('moves') or 0)} move(s)")
    said = [] if outcome == "held" else [say_hold(outcome)]
    if failed:
        said.append("the frame was not scanned")
    return f"{line} -- {'; '.join(said)}" if said else line


def not_reached(holds: dict[int, dict]) -> list[str]:
    """The frames whose hold did not end ``held``, a line each, for the end.

    Every one was still scanned -- a hold records its outcome rather than
    refusing a frame -- so this is the list of frames that may sit somewhere
    other than where they were asked to, and the only place a roll says so
    once its log has scrolled away. Only frames that were: one that failed
    was not scanned, wherever its hold had got to, and is `not_delivered`'s.
    """
    out = []
    for number, hold in sorted(holds.items()):
        outcome = str(hold.get("outcome") or "?")
        if outcome == "held":
            continue
        # What the record says beyond the outcome, where it says more: the
        # wrong-way measurement, what an abandoned frame no longer looked
        # like. An `off` record's reason is its outcome again.
        why = (hold.get("roll_abort") or hold.get("abandoned")
               or (hold.get("reason") if outcome != "off" else None))
        out.append(f"frame {number}: {say_hold(outcome)} ({outcome}); target "
                   f"{_u(hold.get('target_mm'))}, now "
                   f"{_u(hold.get('final_mm'))} after "
                   f"{int(hold.get('moves') or 0)} move(s)"
                   + (f" -- {why}" if why else ""))
    return out


def not_delivered(holds: dict[int, dict]) -> list[str]:
    """The held frames that then failed, a line each, for the end.

    Apart from `not_reached`, which lists frames scanned off their position:
    these were not scanned at all. A pass under the hold that failed leaves
    its record at the step it was taking (``move``: cut off part way), and a
    hold that finished before the frame's scan failed keeps its outcome.
    Such a frame was listed among the positions not reached, as ``move
    (move)``, as though it had been scanned there.
    """
    out = []
    for number, hold in sorted(holds.items()):
        outcome = str(hold.get("outcome") or "?")
        out.append(f"frame {number}: {say_hold(outcome)} ({outcome}) after "
                   f"{int(hold.get('moves') or 0)} move(s)"
                   + ("" if outcome == "move" else ", and then failed"))
    return out


def say_roll_estimate(args: argparse.Namespace, held=()) -> float:
    """Say how long this roll should take before the device opens.

    CLAUDE.md sends a walk here and says anything over ~8 minutes must run in
    the background -- a foreground command killed at 10 minutes abandons its
    read, which wedges the scanner -- and this tool, the one most likely to
    pass that line, said nothing: a walk of a whole strip is a calibration
    and twenty-odd prescans, and any roll is minutes a frame. `tools/scan.py`
    has always said. With no --frames the roll runs to the end of the strip,
    so it is costed to the last place a strip can have.

    ``held`` is the frames --approved holds, by their place on the strip.
    Each one the roll reaches is placed by up to `framing.MAX_HOLD_MOVES`
    moves, every move a SLIDE and a verification prescan -- and so is every
    frame --correct aims, through the same loop. Costed at that cap: those
    prescans were left out, and a ten-frame held walk at 300 dpi came to 475
    s at the slow end, under the 480 that warns, with up to thirty more
    prescans still to take. And the hold now moves on whatever residual it
    can measure, where it used to refuse an arrival past its budget.

    Returns the slow end, in seconds.
    """
    first = max(0, args.start_at - 1)
    # The frames the roll will yield, as its end is judged (`frames_asked`):
    # with --only beside --frames it was costed as --frames full scans, the
    # places it advances past unscanned included.
    asked = session.frames_asked(args.start_at, args.frames, args.only)
    if asked is None:
        count = max(1, session.LAST_PLAUSIBLE_POSITION + 1 - first)
        what = f"up to {count} frames -- to the end of the strip, as --frames is not given"
        places = count
        reached = set(range(args.start_at, args.start_at + count))
    else:
        count = len(asked)
        what = (f"the {count} chosen frame(s)" if args.only is not None
                else f"{count} frame(s)")
        # Every place up to the last one scanned is moved over, chosen or not.
        places = asked[-1] - args.start_at + 1 if asked else 0
        reached = set(asked)
    # Held, or aimed: a frame --approved holds is not aimed as well.
    placing = (count if args.correct
               else len(reached.intersection(int(n) for n in held)))
    looks = placing * MAX_HOLD_MOVES
    prescan = session.estimate_seconds(args.prescan_dpi, False)
    scan = (0.0 if args.dry_run else session.estimate_seconds(
        args.dpi, args.ir, bool(args.fast_ir and args.ir)))
    metering = 0.0
    if not args.dry_run and args.meter == METER_EACH:
        metering = count * session.METERING_S
    elif not args.dry_run and args.meter == METER_ONCE:
        metering = session.METERING_S
    calibrating = calibrates(args)
    placed = ""
    if placing:
        if not args.correct:
            verb = "held"
        else:
            verb = "held or aimed" if held else "aimed"
        placed = (f", {'each' if placing == count else f'{placing} of them'} "
                  f"{verb} with up to {MAX_HOLD_MOVES} more prescans")
    print(f"{'walking' if args.dry_run else 'scanning'} {what}{placed}:",
          flush=True)
    return session.say_estimate(
        count * (prescan + scan) + looks * prescan,
        places * session.FORWARD_FRAME_S + metering
        + looks * session.HOLD_MOVE_S
        + (session.CALIBRATION_S if calibrating else 0.0),
        say=lambda m: print(m, flush=True),
        warn=lambda m: print(m, file=sys.stderr, flush=True))


def _differs_from_earlier(path: Path, args: argparse.Namespace) -> list[str]:
    """How this run's request differs from the one the roll's frames took.

    Read from the roll's own manifest, in either tool's shape
    (`manifest_settings`), and only for what decides the pixels: the
    resolution, the channels, the film metering reads, and the metering. A
    manifest with no frames yet, or one that cannot be read, has nothing to
    hold a run to.
    """
    try:
        earlier = read_manifest(path)
    except ValueError:
        return []
    if not isinstance(earlier, dict) or not earlier.get("frames"):
        return []
    was = manifest_settings(earlier)
    now = {"resolution": (args.dpi, "dpi"),
           "infrared": (args.ir, "infrared"),
           "film": (args.film, "film"),
           "meter": (args.meter, "metering")}
    if args.ir and was.get("infrared"):
        # Only between two infrared runs: an RGB roll's record of the flag
        # governs nothing, and the window writes it whatever the channels.
        now["fast_infrared"] = (bool(args.fast_ir), "tied infrared")
    return [f"{name} {was[key]} where this run asks for {value}"
            for key, (value, name) in now.items()
            if was.get(key) is not None and was[key] != value]


def _refuse_infrared(ap: argparse.ArgumentParser, film: str) -> None:
    ap.error(
        f"--ir with --film {film}: infrared is blind to it -- its "
        + ("grain" if film == "bw" else "cyan layer")
        + " absorbs infrared, so every frame would spend its ~212 s floor "
        "and hand back the picture rather than the dust. Drop --ir. "
        "(Chromogenic C-41 black and white does clean properly: scan that "
        "as --film negative.)"
    )


def main() -> int:
    # What `_main` holds (`rps7200.locks`) is let go however it ends.
    with contextlib.ExitStack() as locked:
        return _main(locked)


def _main(locked: contextlib.ExitStack) -> int:
    use_utf8_stdout()
    ap = build_parser()
    args = ap.parse_args()
    if args.positions is not None and not args.approved:
        # Its positions are offsets from where a walk saw each frame, and that
        # walk's prescans are what the hold measures against; with no walk
        # named there is nothing to hold any of them to.
        ap.error("--positions needs --approved: each of its positions is an "
                 "offset from where a walk saw that frame, and the walk's "
                 "prescans are what the roll holds each frame against. Name "
                 "the walk folder with --approved.")
    if args.film is None and not args.approved:
        args.film = FILM_NEGATIVE
    if args.ir and args.film and not supports_infrared(args.film):
        _refuse_infrared(ap, args.film)
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
    if args.only is not None and args.only[0] < args.start_at:
        # The roll starts at --start-at and only goes forward, so a frame
        # before it would be asked for and never reached.
        ap.error(f"--only {args.only[0]} is before --start-at {args.start_at}; "
                 f"start at {args.only[0]} or before it")
    if (args.only is not None and args.frames is not None
            and args.only[-1] >= args.start_at + args.frames):
        # And one past the places --frames counts from --start-at: the roll
        # ends there (`DirectScanner.roll_ends`) before it is reached. The
        # advice a failed run prints is a --start-at and an --only, and
        # added to the first run's command line it keeps that run's
        # --frames -- which would drop the end of the list without a word.
        beyond = [n for n in args.only if n >= args.start_at + args.frames]
        ap.error(f"--only {','.join(str(n) for n in beyond)}: --frames "
                 f"{args.frames} from --start-at {args.start_at} ends the roll "
                 f"after frame {args.start_at + args.frames - 1}, so "
                 f"{'it' if len(beyond) == 1 else 'they'} would never be "
                 "reached. Leave --frames out -- --only ends the roll after "
                 "its last frame -- or give more.")
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
        # Held as shown while it is read, and only then (`locks.ROLL_VIEW`):
        # every prescan is read here, and the window's Rename of that walk
        # need not wait for the hours the roll runs.
        try:
            with locks.take(locks.ROLL_VIEW, args.approved, exclusive=False):
                held, held_note = hold_from_walk(
                    args.approved, positions=args.positions,
                    library_root=args.library or None)
        except locks.Refused as exc:
            ap.error(f"--approved {args.approved}: {exc.who} is renaming or "
                     "deleting it; run this once it has finished")
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
        file = held_note.get("positions")
        print(f"holding {len(held)} frame(s) to positions from "
              f"{args.approved}"
              + (f", and from {file} for the frames it places" if file
                 else "") + ": "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
        if held_note.get("positions_shift"):
            print(f"  {file} numbers its frames from where its walk started, "
                  f"{held_note['positions_shift']} behind the strip's; read "
                  "as the strip's, as the window reads it")
        # Each frame, before anything moves: where it is to be held, who said
        # so, and what the clamp took off. The summary above counts sources
        # and could not say which frame would be sent where.
        for n in sorted(held):
            print("  " + said_target(n, held[n], held_note))
        for n in held_note.get("not_walked") or ():
            print(f"warning: {file} places frame {n}, which the walk in "
                  f"{args.approved} has no prescan of: there is nothing to "
                  "hold it against, so it is not held", file=sys.stderr)
        for n, named in sorted((held_note.get("other_reference") or {}).items()):
            print(f"warning: frame {n}'s position in {file} was set on "
                  f"{named}, not on the walk's prescan of it, which is what it "
                  "is held against -- walked again since? Its offset is from "
                  "where that other pass saw the frame", file=sys.stderr)
        unchecked = held_note.get("unchecked") or []
        if unchecked:
            print(f"warning: frame(s) {_listed(unchecked)}: {file} names the "
                  "picture each position was set on, and nothing names the "
                  "entry the walk's prescan of it was filed as, so whether "
                  "the two are one picture cannot be checked", file=sys.stderr)
        # Where the references are, for whoever lays this roll beside them
        # afterwards (`tools/hold_check.py`). Each hold records its
        # reference by the id the walk gives it, found or not; said here
        # when this run's library will not hold it, before anything moves.
        elsewhere = held_note.get("not_in_library") or []
        if elsewhere:
            print(f"warning: {args.library} holds none of the entries the "
                  f"walk in {args.approved} filed its prescans of frame(s) "
                  f"{_listed(elsewhere)} as -- another --library, or run from "
                  "another folder? Each hold records its reference by id all "
                  "the same; tools/hold_check.py finds it in the library it "
                  "is given", file=sys.stderr)
        unnamed = held_note.get("unnamed") or []
        if unnamed:
            print(f"warning: nothing names the entry the walk's prescan of "
                  f"frame(s) {_listed(unnamed)} was filed as -- a walk from "
                  "before its records said, "
                  + (f"with none in {args.library} from while it ran"
                     if args.library else "with no library named to look in")
                  + ", or a prescan never filed -- so their holds record no "
                  "reference, and tools/hold_check.py has to guess it",
                  file=sys.stderr)
        # Metered and scanned as the film the walk was made on, as its
        # prescan resolution is adopted above. --film defaulted to negative
        # whatever the walk was: a slide walk scanned from here was metered
        # per channel, taking each slide's own cast off -- baked into the raw
        # bytes, where nothing re-derives it. Typed, it wins, and is said.
        walked_film = held_note.get("film") or FILM_NEGATIVE
        if args.film is None:
            args.film = walked_film
            print(f"scanning as {walked_film} film, as the walk in "
                  f"{args.approved} was made")
        elif args.film != walked_film:
            print(f"warning: --film {args.film}, but the walk in "
                  f"{args.approved} was made on {walked_film} film, and its "
                  "positions were proposed as that", file=sys.stderr)
        if args.ir and not supports_infrared(args.film):
            _refuse_infrared(ap, args.film)
    if args.prescan_dpi is None:
        args.prescan_dpi = 300
    # Said before the device opens, not frame by frame after: at a prescan
    # resolution the frame-edge detector cannot read, every frame is refused
    # and the roll looks centred without being so. `--correct` is refused --
    # it would correct nothing, and an unattended roll is exactly where
    # nobody reads the per-frame "left as it came". A walk is only warned
    # about: its prescans are still a survey of the strip.
    #
    # Each judged on the film its prescans are read as. This run's own --
    # what --correct and --correct-dry-run read, and what a --dry-run walk is
    # read as on the sheet later -- are --film's. The walk --approved names
    # was read by `hold_from_walk` on the walk's film, and was judged on
    # --film too, which is negative unless told: a slide walk at 600 dpi was
    # warned about though slides never reach this detector, and a negative
    # walk run with --film positive was not, though every frame of it had
    # been refused.
    unread = frame_edges.unread_at(args.prescan_dpi, args.film)
    if unread and args.correct:
        ap.error(f"--correct with --prescan-dpi {args.prescan_dpi}: {unread}")
    warning = unread if (args.dry_run or args.correct_dry_run) else None
    if args.approved and not warning:
        warning = frame_edges.unread_at(args.prescan_dpi,
                                        held_note.get("film") or args.film)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)

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
        out = roll_dir(ROLLS, args.roll or "")
        roll_name = recorded_roll_name(out) or out.name
    # A dry run and the scan that follows it share a directory, so they must
    # not share a file: the record of what was walked is what says which frames
    # are worth scanning, and writing the scan over it loses that.
    manifest_path = out / ("survey.json" if args.dry_run else "roll.json")
    # Not for --frames 0, which rewinds and stops: nothing is scanned, so
    # nothing can differ, and a rewind of a roll taken at 3600 dpi was
    # refused for the 1800 it never asked for.
    if not args.dry_run and args.frames != 0:
        differs = _differs_from_earlier(manifest_path, args)
        if differs:
            # A resume adds to the roll, and this run's frames would be
            # scanned otherwise: another resolution, another channel set, a
            # slide metered as a negative -- baked into the raw bytes, and
            # one roll.json saying only what the last run asked for. Refused
            # before anything opens; nothing here can tell a deliberate
            # change from a flag forgotten overnight.
            ap.error(f"{manifest_path} was scanned with "
                     + "; ".join(differs)
                     + ". Give the flags its earlier frames were taken with, "
                     "or scan into a new --roll.")
    # The folders, the paths and the disks, before anything opens -- a
    # walk's as well as a roll's, as the window's are (`ScanSession.
    # _preflight`). The library, the rolls folder or --out, and the
    # reference's folder may have their last folder made and never their
    # parents (`storage`): a drive not plugged in failed after the seek and
    # the calibration, or was made again on C:. A path past Windows' 259
    # characters failed the same way. And a roll that cannot be filed
    # showed itself as a failed filing hours in: one to the end of the strip
    # is refused only when not even its first frame fits, and stops before
    # a frame that does not. Each disk keeps `storage.SPACE_RESERVE` free
    # beyond what the run puts there, debug filing's spool included.
    #: Debug filing's spool, where RPS7200_DEBUG is on: counted on its disk.
    spool = (_Driver.debug_root_for(args.library or None)
             / _Driver.DEBUG_SPOOL_DIR if debug_from_env() else None)
    what = f"not starting the {'walk' if args.dry_run else 'roll'}"
    if args.frames != 0:
        refused = [said for said in (
            storage.missing_root(args.library) if args.library else None,
            storage.missing_root(out if args.out else ROLLS),
            (storage.missing_root(Path(args.reference).parent)
             if calibrates(args) else None)) if said]
        if refused:
            ap.error(f"{what}: " + "; ".join(refused))
        too_long = storage.longest_too_long(
            longest_paths(args, out, roll_name, spool))
        if too_long:
            ap.error(f"{what}: " + "; ".join(too_long))
        asked = session.frames_asked(args.start_at, args.frames, args.only)
        places = max(1, session.LAST_PLAUSIBLE_POSITION + 2 - args.start_at)
        short, unknown = session.check_space(
            space(args, len(asked) if asked is not None else 1, out, spool))
        if short:
            ap.error(f"{what}: " + "; ".join(short))
        tight, _ = session.check_space(
            space(args, len(asked) if asked is not None else places, out,
                  spool), session.SPACE_WARN)
        if tight:
            print(f"warning: the {'walk' if args.dry_run else 'roll'} may "
                  "fill the disk: " + "; ".join(tight), file=sys.stderr)
        for said in unknown:
            print(f"warning: {said}", file=sys.stderr)
    if not args.out and args.roll and name_cut(args.roll):
        print(f"the roll's name is {len(args.roll.strip())} characters, "
              "longer than a folder's name may be here; its folder and files "
              f"are called {out.name}, and roll.json keeps the name as typed")
    # The roll's folder and the library, held from before the scanner opens
    # until this exits (`rps7200.locks`). The window's Rename, Delete and
    # Duplicate, and its own rolls, wrote into or took away a folder this
    # was scanning into; `compact --write` and the migrations rewrote the
    # entries it filed. Each refuses beside this now, as this beside them.
    if args.frames != 0:
        try:
            taken = locked.enter_context(locks.take(
                locks.ROLL, out, exclusive=True))
        except locks.Refused as exc:
            ap.error(f"{what}: {out} is in use by {exc.who}: nothing was "
                     "moved or scanned")
        if taken.note:
            print(f"warning: {taken.note}; going ahead", file=sys.stderr)
    if args.library:
        try:
            taken = locked.enter_context(locks.take(
                locks.LIBRARY, args.library, exclusive=False))
        except locks.Refused as exc:
            ap.error(f"{what}: {exc.who} is rewriting the library "
                     f"{args.library}; run this once it has finished. Nothing "
                     "was sent.")
        if taken.note:
            print(f"warning: {taken.note}; going ahead", file=sys.stderr)
    # Each frame's delivered file in one channel or three. This tool wrote
    # three always, so the same B&W strip came out RGB from here and mono
    # from the window and tools/scan.py -- and was then taken for colour
    # negative by whatever read it next.
    mono = wants_mono(args.mono, args.film)

    typed = (args.roll or "").strip()
    manifest = {
        "roll": roll_name,
        # The name as it was typed, where the folder's is another: made safe
        # to be a folder, or cut to `session.NAME_CAP`.
        **({"typed_name": typed} if typed and typed != roll_name else {}),
        # The frame numbers below are places on the strip, as the window's
        # manifests say too; a file without this counted from wherever its
        # roll happened to start.
        "numbering": NUMBERING,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "dpi": args.dpi, "infrared": args.ir, "meter": args.meter,
            "film": args.film, "dry_run": args.dry_run,
            "start_at": args.start_at, "frames": args.frames,
            "only": list(args.only) if args.only is not None else None,
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
            # The rest of what the frames were taken with, as the window
            # records it: without these, which passes of a roll were tied or
            # corrected could not be told from roll.json.
            "fast_infrared": bool(args.fast_ir and args.ir),
            "shading": not args.no_shading,
            "correct": args.correct,
            "correct_dry_run": args.correct_dry_run,
            "max_failures": args.max_failures,
            "mono": mono,
            "mono_channel": args.mono_channel,
        },
        "held": held_note,
        "frames": [],
    }
    if args.frames != 0:
        ref_path = Path(args.reference)
        if calibrates(args) and reference_refused(ref_path):
            ap.error(str(reference_refused(ref_path)))
        if args.reuse and ref_path.exists() and not args.no_shading:
            say_reused(ref_path)
        say_roll_estimate(args, held=held)
    # What earlier runs left behind, before anything is sent.
    say_leftovers(args.library, rolls=out.parent if args.out else ROLLS)

    # Before the device opens. The seek before the calibration protects only
    # a roll that starts past frame 1: on an empty transport the counter
    # reads 0, a seek to frame 1 moves nothing and succeeds, and the
    # calibration ran on nothing -- the state that preceded a wedge.
    #
    # Asked last, once everything above has been refused or said. It was
    # asked before --approved was read, so what that prints -- a position
    # set on another picture, a reference this library does not hold --
    # came after the answer, and the scanner opened straight after it.
    if args.frames != 0 and calibrates(args):
        from rps7200.console import film_unconfirmed

        unconfirmed = film_unconfirmed(args.film_loaded)
        if unconfirmed:
            ap.error(unconfirmed)

    #: The one writer of the manifest, from this thread and the writer's; see
    #: `session.RollManifest`. Made once the roll is placed, and not before.
    record_of: RollManifest | None = None

    started = time.monotonic()
    scanned = failed = 0
    #: Every place the roll reached, scanned, walked or failed, and the last.
    covered = 0
    reached: int | None = None
    #: Which frames those were, for the frames asked for that were not.
    seen: set[int] = set()
    #: Every held frame's hold record, by frame number, for the list of
    #: positions not reached that ends the run (`not_reached`) -- the frames
    #: that were scanned -- and the held frames that failed, which were not
    #: (`not_delivered`).
    holds: dict[int, dict] = {}
    failed_holds: dict[int, dict] = {}
    #: Failed frames in a row at the end, which the driver gives up at
    #: (`--max-failures`) without a word to this loop.
    in_a_row = 0
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

    #: Set on the writer's thread when a frame could not be filed. The roll
    #: stops after the frame in flight, as the window's does: a full disk or
    #: a library that has gone fails every frame after it the same way, and
    #: this used to scan on for hours, printing a success line for each
    #: frame, and say so only at the end.
    filing_failed = threading.Event()

    def filed(filing) -> None:
        # Said as it happens, on the writer's thread: what the chosen format
        # could not carry, a copy that could not be written, and a frame that
        # could not be filed at all. Only the last stops the roll: a copy
        # not written leaves its pass in the library, and the frames after
        # it are filed as surely.
        while writer.notes:
            print(writer.notes.pop(0), file=sys.stderr, flush=True)
        for line in filing.trouble():
            print(line, file=sys.stderr, flush=True)
        if not filing.failed:
            return
        if not filing_failed.is_set():
            filing_failed.set()
            print("stopping after the frame in flight: the frames after it "
                  "would be lost the same way", file=sys.stderr, flush=True)

    #: Why there is no room for the next frame, once a check finds none.
    no_room: list[str] = []
    #: The disks whose room could not be read, each said once a run.
    unknown_said: set[str] = set()
    #: How many frames the roll had reached at the last check.
    room_checked = [-1]

    def room_for_next() -> bool:
        """Whether the disks hold the next frame, asked as the driver asks
        whether to go on: before each frame, once per frame reached. The
        disk can fill while a roll runs, and one to the end of the strip was
        let start with room for its first frame only. The frames still being
        filed are not on it yet, so they count."""
        if args.dry_run or no_room:
            return not no_room
        if room_checked[0] == covered:
            return True
        room_checked[0] = covered
        waiting = record_of.waiting() if record_of is not None else 0
        short, unknown = session.check_space(
            space(args, 1 + waiting, out, spool))
        # A disk whose room cannot be read -- a drive pulled out -- does not
        # stop the roll: what cannot be written there is said as it is not.
        for said in unknown:
            if said not in unknown_said:
                unknown_said.add(said)
                print(f"warning: {said}; the roll goes on", file=sys.stderr,
                      flush=True)
        if short:
            no_room.append("; ".join(short))
            print("stopping: no room for the next frame: " + no_room[0],
                  file=sys.stderr, flush=True)
        return not no_room

    writer = FrameWriter(on_done=filed)
    # RPS7200_DEBUG decides, as everywhere else. This tool files its own
    # frames and claims each of those passes as it hands it to the writer,
    # which answers for it once filed (`answering` below), so debug filing
    # leaves them out rather than writing every frame twice -- 43 GB of duplicate on a 38-frame roll at 7200 dpi,
    # which is why this used to say debug=False and so filed none of the
    # prescans, probes and holds either. Held open by `HeldOpen`, so the
    # device closes when the block ends and debug filing waits for the writer.
    device: HeldOpen | None = None
    # Wrapped so `writer.finish()` below runs whatever comes out of this.
    # An exception the roll loop does not catch used to unwind straight
    # past it, and the frames already queued died unfiled -- scanner time
    # turned into nothing, with no message.
    trouble: BaseException | None = None
    # Ctrl-C finishes the frame in flight and stops there; a second one
    # aborts. Abandoning a read wedges the scanner, and the frames queued for
    # filing used to die with it. Said as what it does: the driver asks
    # `should_stop` only between frames, so the shared wording's "after the
    # pass in flight" was followed by the rest of the frame -- minutes, and
    # the wait that makes an operator press again. Before the first frame it
    # is the lamp's warm-up, move or calibration under way that finishes
    # (`stopped`). The lamp is named because a press during it is answered
    # only once it is over -- about 80 s from cold, up to 300 -- and a wait
    # the promise left out is as likely to bring the second press.
    frame_passes = ("its prescans" if args.dry_run
                    else "its prescans, metering and the scan")
    interrupt = DeferredInterrupt(message=(
        f"\nstopping after the frame in flight -- {frame_passes} -- or, "
        "before the first frame, after the lamp's warm-up, move or "
        "calibration under way. Interrupting a read wedges the scanner. "
        "Press Ctrl-C again to abort anyway."))

    def stopped(where: str) -> bool:
        """Whether a Ctrl-C has asked the roll to stop, said as it stops.

        Asked before each step that drives the device ahead of the first
        frame -- the rewind, the seek, each offset command, the calibration
        -- once after the calibration, and once more where `--frames 0` ends
        the run with nothing else to start; never inside a step, so the
        lamp's warm-up or whatever is under way finishes. A press during the
        warm-up is answered by whichever of them comes next. Only the roll
        used to ask (`should_stop`): a Ctrl-C during a seek was told
        "stopping" and then sat through 3-4 minutes of calibration, and a dry
        run replaced survey.json with an empty walk before the roll's first
        question ended it. Nothing is written before `placed`, so a stop here
        leaves the folder as it was.
        """
        if not interrupt.requested():
            return False
        print(f"stopped at Ctrl-C {where}: nothing was scanned, and the "
              "roll's folder was left as it was", file=sys.stderr)
        return True

    #: The scanner, once opened, for `debug_settle` after the writer.
    scanner: DirectScanner | None = None
    try:
        device = HeldOpen(DirectScanner(verbose=args.verbose, debug=None))
        # Debug filing beside this roll's frames, not in `./library` -- said
        # before the scanner opens, which holds that library as in use.
        debug_filing_into(device.scanner, args.library)
        # The host kept out of idle sleep until the device has closed: a
        # roll runs unattended for hours, and a machine that sleeps mid-pass
        # abandons the read, which wedges the scanner (`awake`).
        with interrupt, KeepAwake(say=lambda m: print(m, file=sys.stderr)), \
                device as s:
            scanner = s
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
                if stopped("before the rewind, with the film where it was"):
                    return 130
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
                if stopped(f"before placing the film on frame "
                           f"{args.start_at}"):
                    return 130
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
                # Through `plan_nudges` because one command reaches only the
                # largest correction, param 87 (88.8 units), and
                # `param_for_mm` clamps there silently -- a request past it
                # would otherwise deliver part of it and say nothing. The
                # planner raises instead, and it is the same planner the
                # window's adjuster and the hold loop size themselves from.
                #
                # Taken in units, as the window shows them. It took
                # millimetres, which CLAUDE.md prohibits for transport
                # distances, and a value read off the window and typed here
                # moved the film about 9.5 times as far as meant.
                try:
                    steps = plan_nudges(args.nudge * MM_PER_UNIT)
                except ValueError as exc:
                    print(f"cannot offset the film: {exc}", file=sys.stderr)
                    return 1
                sent = 0.0
                for count, step in enumerate(steps):
                    # Between commands, as between frames: a stop part way
                    # says how much was sent, since the prescan that would
                    # have shown where the film went is not coming.
                    if stopped("before offsetting the film" if not count else
                               f"after {count} of the {len(steps)} offset "
                               f"commands, {say_units(sent)} of the "
                               f"{args.nudge:+.1f} units asked"):
                        return 130
                    asked = s.nudge(step)
                    sent += asked["asked_mm"]
                    time.sleep(0.5)
                print(f"offset the film by {say_units(sent)} in {len(steps)} "
                      f"command(s) (asked {args.nudge:+.1f} units)")
                if args.nudge < 0:
                    print("  backward, so the first two or three commands may "
                        "have gone into backlash -- the prescan below is what "
                        "says where the film really is")
                print()
            if args.frames == 0:
                # Asked here too, though nothing is left to start. A Ctrl-C
                # during the last move asked was told "stopping" like any
                # other, and exited 0 -- after a rewind always, and after an
                # offset when it landed in the last command, where one a
                # command earlier exited 130.
                if stopped("after the offset" if args.nudge
                           else "after the rewind" if args.rewind
                           else "after the lamp's warm-up"):
                    return 130
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
            #
            # Asked on both sides of it: before, so a Ctrl-C during the seek
            # spends no 3-4 minutes of lamp, and after, so one during the
            # calibration starts no frame -- and, on a dry run, replaces no
            # survey.json with a walk of nothing. Afterwards it says what the
            # driver did, from its answer, rather than deciding again.
            if stopped("before calibrating" if calibrates(args)
                       else "before the first frame"):
                return 130
            shading = calibrate(s, args)
            if stopped("after the calibration"
                       if shading["action"] == "calibrated"
                       else "before the first frame"):
                return 130

            # The film is on the roll's first frame and the reference is in
            # hand, so there is a roll to record -- and not before. See
            # `placed`: a calibration that fails leaves the folder as it was.
            # Its own folder at most, and the rolls folder above it, never
            # anything further up (`storage`): refused before the device
            # opened, and gone since if it is not there now.
            if args.out:
                storage.make_root(out)
            else:
                storage.make_within(ROLLS, out)
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

            def file_prescan(number, image, raw, meta, capture, path,
                             before=False, record=None) -> None:
                """One prescan, filed raw in its own entry and written to
                ``path`` (a walk's `prescanNN.tif`), on the writer thread.

                On a walk and on a real roll alike, and for a frame that then
                failed: a real roll's prescans used to survive only inside
                the frame's entry, as the corrected `prescan.tif`, and a
                failed frame's not at all. Written by the writer rather than
                here, as the window's are: nothing local happens on this
                thread with the device open, and the TIFF compresses.

                ``record`` is the frame's, which names ``path`` -- and the
                entry -- once the writer has written them
                (`RollManifest.prescan_told`): named as it was queued, a copy
                that then failed left the walk naming a file nobody wrote.
                """
                library_root = args.library or None
                if library_root is None and path is None:
                    return
                if not meta and library_root is not None:
                    # The pass's own meta or no entry: one made up here would
                    # describe itself wrongly (CLAUDE.md). Still written.
                    print(f"picture {number}: the scanner published no "
                          "record of this prescan, so it is not filed in the "
                          "library", file=sys.stderr)
                    library_root = None
                meta = dict(meta or {}, roll_membership=roll_membership(
                    roll_name, number, "prescan", out))
                capture = dict(capture or {})
                if raw is not None and raw_bytes_disagree(
                        raw.shape, capture.get("raw_layout"), meta):
                    capture.update(raw=None, raw_layout=None)
                # Claimed only with its bytes, and let go by debug filing
                # when the writer says it is filed: see
                # `DirectScanner.debug_claim`.
                receipt = (s.debug_claim(raw)
                           if library_root and raw is not None
                           and capture.get("raw") is not None else None)
                told = (None if path is None or record is None
                        else record_of.prescan_told(
                            record, path,
                            key="prescan_before" if before else "prescan",
                            entry_key=("prescan_before_entry" if before
                                       else "prescan_entry")))
                # Promised as it is queued, never before (`session.queued`).
                queued(
                    writer.submit, told,
                    number=number, kind="prescan",
                    paths=[path] if path is not None else [],
                    # Named as the window names them (`Filing`): only the
                    # frame's own prescanNN.tif is what the sheet reads,
                    # and the picture from before an aim never is.
                    part="prescan before its aim" if before else "",
                    sheet=None if before else path,
                    # Never made again once it has gone (`_folder_for`).
                    roll_folder=out,
                    dpi=args.prescan_dpi, image=image, raw_image=raw,
                    meta=meta, prescan=None, library=library_root,
                    inquiry=info, capture=capture,
                    tags=sorted({*args.tags, "roll", "prescan", roll_name,
                                 *(("before",) if before else ())}),
                    film=FilmNotes(stock=args.stock, process=args.process,
                                   frame=roll_frame_label(roll_name, number),
                                   notes=args.notes),
                    on_filed=answering(receipt, told),
                )

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
                # Counted from 1 here and from 0 by the transport, as the
                # window's chosen frames are.
                only=(None if args.only is None
                      else tuple(n - 1 for n in args.only)),
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
                should_stop=lambda: (interrupt.requested()
                                     or filing_failed.is_set()
                                     or not room_for_next()),
                # Every pass of the roll, prescans and metering probes
                # included. `--no-shading` used to skip only the calibration
                # above, so the first prescan -- still asking for a correction
                # -- calibrated inside itself, on the path that stalls.
                shading=not args.no_shading,
                # In the role of each probe and hold look debug filing keeps.
                roll=roll_name,
            ):
                number = frame.index + 1
                covered, reached = covered + 1, number
                seen.add(number)
                in_a_row = in_a_row + 1 if frame.error else 0
                # Where a held frame was asked to go, where it arrived and
                # where it ended, as it happens -- on a walk too, which holds
                # each frame as a roll does. The driver's own log line says
                # it in prose; this one has one shape for every frame.
                #
                # A frame that failed was not scanned, wherever its hold got
                # to: a pass under the hold that failed leaves the record at
                # the step it was taking (`move`), and it was listed at the
                # end among the frames scanned off their positions.
                hold = (frame.registration or {}).get("approved")
                if isinstance(hold, dict):
                    (failed_holds if frame.error else holds)[number] = hold
                    print(hold_line(number, hold, failed=bool(frame.error)),
                          flush=True)
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

                # The prescans first, whatever became of the frame. The
                # frame as it arrived, before a hold or an aim moved it,
                # before the one that replaced it: a corrected prescan
                # replaces the original outright, so without it the only
                # account of whether a correction helped is the detector's
                # own -- which is the thing being checked. First, too, so a
                # walk's record names the final prescan's entry below.
                if frame.prescan_before is not None:
                    was = (out / f"prescan{number:02d}-before.tif"
                           if args.dry_run else None)
                    file_prescan(number, frame.prescan_before,
                                 frame.raw_prescan_before,
                                 # its own meta, never the later pass's
                                 frame.prescan_before_meta,
                                 frame.prescan_before_capture, was, before=True,
                                 record=record)
                if frame.prescan is not None:
                    # Kept on a walk beside the manifest. The registration
                    # numbers are derived from it, and a number that looks
                    # wrong can only be settled by looking at what it was
                    # measured on. Filed with the record the driver took as
                    # the pass was taken (`RollFrame.prescan_capture`); a
                    # scanner that carries none is read as before on a walk,
                    # and on a real roll -- whose last pass is the frame --
                    # gives none.
                    pre = (out / f"prescan{number:02d}.tif"
                           if args.dry_run else None)
                    #
                    # Read off the scanner, it is its last pass's, and the
                    # bytes of another pass decode to another picture: a
                    # later pass's of the same shape, say a verification
                    # prescan after an aim that failed, is caught by
                    # identity (`bytes_are_another_pass`), and one laid out
                    # for another shape by `file_prescan`.
                    if frame.prescan_capture is not None:
                        capture = frame.prescan_capture
                    elif args.dry_run and not bytes_are_another_pass(
                            s, frame.raw_prescan):
                        capture = s.capture_record()
                    else:
                        capture = {}
                    file_prescan(number, frame.prescan, frame.raw_prescan,
                                 frame.prescan_meta, capture, pre,
                                 record=record)

                if frame.error:
                    failed += 1
                    print(f"picture {number}: FAILED -- {frame.error}", file=sys.stderr)
                elif args.dry_run:
                    r = frame.registration
                    short = r.get("shortfall_mm", 0.0)
                    # Every number here is optional. `registration` abstains on
                    # a loaded strip -- and once it says so honestly rather than
                    # returning a fallback zero, these keys go missing. Formatting
                    # a None with `:+.2f` raises, and it would raise in the middle
                    # of a walk, after the scanner time had been spent.
                    # Said in units, never millimetres (CLAUDE.md); the
                    # records keep what the driver measured.
                    offset = r.get("offset_mm")
                    said = ("offset --" if offset is None
                            else f"offset {say_units(offset)}")
                    print(f"picture {number}: contrast {r.get('contrast')}, "
                          f"x{r.get('x0')}..{r.get('x1')}, {said}"
                          + (f", SHORT BY {say_units(short, signed=False)} "
                             "-- the film has drifted"
                             if short and short > 0.85 else ""))
                    fix = r.get("correction")
                    if fix:
                        aimed = fix.get("decision_mm")
                        print(f"    aim: {fix.get('outcome')}"
                              + ("" if aimed is None
                                 else f" {say_units(aimed)}")
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
                    capture = s.capture_record()
                    receipt = (s.debug_claim(frame.raw_image)
                               if args.library and frame.raw_image is not None
                               and capture.get("raw") is not None else None)
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
                        # Never made again once it has gone (`_folder_for`).
                        roll_folder=out,
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
                        # One channel for black and white, as the window and
                        # tools/scan.py deliver it; see --mono.
                        mono=mono,
                        mono_channel=args.mono_channel,
                        # The corrected prescan, as the operator's framing
                        # picture beside the frame; the pass itself is its
                        # own entry, raw, filed above.
                        prescan=frame.prescan,
                        prescan_meta=frame.prescan_meta,
                        library=args.library,
                        inquiry=info,
                        capture=capture,
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
                        on_filed=answering(
                            receipt,
                            lambda filing, n=number, p=path: record_of.filed(
                                n, None if filing.failed else filing.entry,
                                filing.error if filing.failed else None,
                                **_copy_said(filing, p))),
                    )
                    submitted = True
                    # Scanned, and handed to the writer: not yet written. It
                    # says so, on stderr and at once, if it cannot be filed.
                    print(f"picture {number}: scanned {frame.image.shape} in "
                          f"{frame.meta.get('duration_s')}s, filing it as "
                          f"{path}")

                record_of.record(record, awaiting=submitted)

    except BaseException as exc:                          # noqa: BLE001
        # Recorded rather than raised: the frames already scanned are
        # worth filing and the manifest is worth finishing. The exit
        # status says it went wrong. BaseException, because a second Ctrl-C
        # or SIGTERM (KeyboardInterrupt, `DeferredInterrupt`; a closed
        # terminal only ever asks) is exactly the exit that used to skip
        # `writer.finish()` and lose queued frames.
        trouble = exc
        print(f"the roll stopped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    finally:
        # Only now, with the device closed: the last frame or two may still
        # be gzipping, and that is exactly the work that must not happen with
        # an open session.
        #
        # Reached through this `finally` around the whole scanning block, so
        # it runs whatever came out of it -- the early returns above
        # included, which the comment here once claimed and the code did not
        # do. Without it, an exception the roll loop does not catch unwinds
        # straight past here and the frames already queued die unfiled --
        # scanner time turned into nothing, with no message. That is
        # structural; widening the roll's except tuple only moves the next
        # one. `ScanSession._run` has had this shape all along, which is why
        # the window never lost a frame this way.
        #
        # Ctrl-C deferred through it as through the frames: see
        # `session.filing_interrupt`.
        with filing_interrupt(say=lambda m: print(m, file=sys.stderr,
                                                  flush=True)):
            writer.finish()
            # Debug filing last, once every frame is filed and answered for:
            # what it still holds is what this run did not keep -- probes,
            # holds, and a frame whose filing failed. The scanner's own exit
            # files what nobody claimed; `DirectScanner.debug_settle` then
            # files a claimed pass whose answer never came.
            if device is not None:
                try:
                    device.release()
                except Exception as exc:                 # noqa: BLE001
                    print(f"debug filing: {exc}", file=sys.stderr)
            if scanner is not None:
                try:
                    scanner.debug_settle()
                except Exception as exc:                 # noqa: BLE001
                    print(f"debug filing: {exc}", file=sys.stderr)
            # A frame the library refused is kept plain beside its copy
            # (`session.keep_unfiled`), as it is filed with the device open;
            # compressed now it has closed, as the window's close does. Left
            # to nothing, it stayed raw.bin and uncompressed TIFFs for good.
            for entry in writer.uncompressed:
                try:
                    library.compact(entry)
                except (OSError, ValueError) as exc:
                    print(f"could not compress {entry}: {exc}; it stays "
                          "uncompressed and complete", file=sys.stderr)
    # A walk's record names its prescan's entry: the last filed under its
    # number, which is why the prescan before a correction is queued first. A
    # roll's frames are named as they are filed (`RollManifest.filed`), and
    # only then: taken from here, a frame that failed was named by the entry
    # of the prescan it left. Only the entries: a picture the library refused
    # is done with none.
    filed = ({n: e for n, e in writer.done if e is not None}
             if args.dry_run else {})
    for record in manifest["frames"]:
        entry = filed.get(record["number"])
        if entry is not None:
            record["entry"] = str(entry)
    # Counted from the writer's records, never from its words. Every line of
    # them was counted as a picture not filed, so a copy that could not be
    # written -- its pass safe in the library -- read "1 failed", and the
    # advice below was to scan the frame again.
    for filing in writer.filings:
        if filing.failed:
            failed += 1
            print(f"could not file picture {filing.number}: {filing.error}",
                  file=sys.stderr)
    unwritten = [f for f in writer.filings if f.outcome == COPY_FAILED]
    for filing in unwritten:
        for path, why in filing.unwritten:
            print(_unwritten_said(filing, path, why), file=sys.stderr)
    # A report that raised: the filing stands, its record may not.
    for problem in writer.problems:
        print(problem, file=sys.stderr)

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
    elif filing_failed.is_set():
        manifest["stopped"] = ("stopped after the frame in flight: a frame "
                               "could not be filed")
    elif no_room:
        manifest["stopped"] = ("stopped after the frame in flight: no room "
                               "for the next frame: " + no_room[0])
    elif interrupt.requested():
        manifest["stopped"] = "stopped by Ctrl-C after the frame in flight"
    #: The frames this run was asked for, where the driver ends the roll:
    #: --only's inside the places --frames counts (`frames_asked`), or None
    #: to the end of the strip. It was --frames alone, and a resume that
    #: added the --only it was advised to the first run's --frames scanned
    #: every frame it chose and failed, "after 2 of the 10".
    asked = session.frames_asked(args.start_at, args.frames, args.only)
    #: Ended short of the frames asked for, with nobody asking it to: a
    #: frame with no picture in it reads as the end of the film -- a missed
    #: shot, a fogged frame -- and so does the end of the transport. It
    #: ended with a line in the driver's log and exit 0, so an unattended
    #: roll of 36 that stopped at 12 reported success to whatever checked.
    short = (asked is not None and covered < len(asked)
             and "stopped" not in manifest)
    if short:
        manifest["stopped"] = (f"ended after {covered} of the {len(asked)} "
                               "frames asked for")
    # Placed, so it was made. Not a traceback when the disk refuses it: the
    # manifest says so on stderr, and the exit status says it went wrong.
    saved = record_of is None or record_of.save()

    print(f"\n{scanned} scanned, {failed} failed, "
          + (f"{len(unwritten)} filed without "
             f"{'its' if len(unwritten) == 1 else 'their'} copy, "
             if unwritten else "")
          + f"{manifest['duration_s']/60:.1f} min")
    print(f"manifest: {manifest_path}")
    # Every frame a hold did not place, by name, whatever else went right: a
    # frame that missed its position is still scanned, and its line above has
    # scrolled away by the end of a roll. Not a failure of the run -- the
    # exit status stays what the frames say -- but the frames to look at.
    missed = not_reached(holds)
    if missed:
        print(f"positions not reached ({len(missed)} of the {len(holds)} "
              "frame(s) held):", file=sys.stderr)
        for line in missed:
            print(f"  {line}", file=sys.stderr)
    elif holds:
        print(f"every one of the {len(holds)} frame(s) held reached its "
              "position")
    # And the held frames that failed, apart: nothing was delivered of them,
    # so they are not frames to look at but frames to take again.
    lost = not_delivered(failed_holds)
    if lost:
        print(f"held, and then failed -- not delivered ({len(lost)}):",
              file=sys.stderr)
        for line in lost:
            print(f"  {line}", file=sys.stderr)
    if short:
        print(f"the roll ended after {covered} of the {len(asked)} frames "
              "asked for" + (f", after frame {reached}" if reached else "")
              + ": the log above says why. A frame with no picture in it is "
              "taken as the end of the film, and so is the end of the "
              "transport.", file=sys.stderr)
    # What is left of this roll: its frames not done -- failed, or never
    # filed, in this run or an earlier one -- and the frames this run was
    # asked for and never reached. Those have no record at all, and the
    # advice named only the recorded ones: frames 10-12 of 36 failing, the
    # driver giving up, and "--start-at 10 --only 10,11,12" followed as
    # printed never scanned 13-36 -- nor the rest after a Ctrl-C, a device
    # gone suspect or a frame the library refused.
    done = {int(r["number"]) for r in manifest["frames"] if r.get("done")}
    left = {int(r["number"]) for r in manifest["frames"] if not r.get("done")}
    #: Where the rest of the strip starts, when this run ended before the
    #: strip did and was asked for all of it. A roll with no end asked for
    #: ends at a blank frame or a transport that does not move, which is
    #: the strip's own end; only these end it early.
    rest: int | None = None
    if asked is not None:
        left |= {n for n in asked if n not in seen and n not in done}
    elif (trouble is not None or filing_failed.is_set() or no_room
          or interrupt.requested()
          or (in_a_row and in_a_row >= args.max_failures)):
        rest = args.start_at if reached is None else reached + 1
    # With the folder named. An unnamed roll's folder is new every run, so
    # "--start-at N" alone started another roll beside this one, and never
    # read the manifest that says what this one has done.
    again = (f"--out {_quoted(out)}" if args.out
             else f"--roll {_quoted(out.name)}")
    if args.dry_run:
        # Never --start-at or --only for a walk. Followed as printed, the
        # roll advice ran a full-resolution scan into the walk's folder; and
        # with --dry-run added it would be worse, because a walk here is
        # never carried forward -- it replaces survey.json, so "--start-at 7
        # --only 7" left a walk of one frame where there were 36. Only the
        # window adds to a walk.
        if failed or rest is not None or any(
                n not in seen for n in asked or ()):
            print("walk the strip again with the same flags (survey.json is "
                  "replaced; the old one is kept as survey.json.bak), or open "
                  "this walk in the window and walk the missing frames again, "
                  "keeping the sheet", file=sys.stderr)
    elif left or rest is not None:
        # And with the frames left named. "--start-at N" alone, as this
        # used to say, scanned every frame from N to the end of the strip
        # again -- hours, at 3600 dpi, and a second library entry for each
        # frame already done -- and could not take 3, 9 and 15 in one run.
        if left:
            print(f"resume the unfinished frames with {again} --start-at "
                  f"{min(left)} --only {','.join(str(n) for n in sorted(left))}",
                  file=sys.stderr)
        if rest is not None:
            # Its own run: --only ends a roll after its last frame, so one
            # list cannot also say "and on to the end of the strip".
            print(("and " if left else "resume ")
                  + "the frames this run never reached, to the end of the "
                  f"strip, with {again} --start-at {rest}", file=sys.stderr)
    elif failed:
        print(f"resume a failed picture with {again} --start-at N "
              "--only N", file=sys.stderr)
    # Any loss is a non-zero exit. It used to be `failed and not scanned`, so
    # a roll that scanned twenty frames and lost three reported success -- and
    # a caller checking the status is exactly who needs to know it lost three.
    # A copy not written is a loss too, if a smaller one: the file a caller
    # reads next is not there. And so is a record that could not be told.
    if (trouble is not None or failed or short or not saved or no_room
            or unwritten or writer.problems):
        return 1
    # Stopped at Ctrl-C with nothing lost is still not a finished roll: 130,
    # as `tools/scan.py` says. It returned 0 -- the manifest's `stopped`
    # keeps such a roll from counting as short, and nothing else looked.
    return 130 if interrupt.requested() else 0


def _listed(numbers) -> str:
    """Frame numbers as a line says them: ``1, 2, 5``."""
    return ", ".join(str(n) for n in sorted(numbers))


def _copy_said(filing: Filing, path: Path) -> dict:
    """What a frame's record says of its frameNN.tif, from the writer's
    answer: `file` once it is written, or `file_error`, why it is not."""
    if path in filing.written:
        return {"file": path.name}
    why = filing.why_unwritten(path)
    return {"file_error": why} if why is not None else {}


def _unwritten_said(filing: Filing, path: str, why: str) -> str:
    """A copy not written, at the end of a run: what holds the pass, and
    what writes the copy again. Never a rescan: the pass is in the library.

    Nothing here writes it again yet -- the window's Save As and Export do,
    under names of their own, and a walk's prescan is put back by walking
    that frame again from the window (TODO.md: `--rewrite-copies`).
    """
    entry = filing.entry.name if filing.entry is not None else "?"
    if filing.sheet is not None and Path(path) == filing.sheet:
        return (f"frame {filing.number}'s prescan is filed in the library as "
                f"{entry}, but {path} was not written ({why}); the contact "
                "sheet will not show it -- open this walk in the window and "
                "walk that frame again, keeping the sheet")
    if filing.part:
        # The picture from before an aim, which no sheet reads and no
        # window shows: kept to compare against, and whole in its entry.
        return (f"frame {filing.number}'s prescan from before its aim is "
                f"filed in the library as {entry}, but {path} was not "
                f"written ({why}); the entry holds it whole")
    return (f"picture {filing.number} is filed in the library as {entry}, "
            f"but {path} was not written ({why}); the window's Save As or "
            "Export writes it again")


def _quoted(value) -> str:
    """A path or name as it would be typed, quoted where a shell would split it.

    Double quotes, which bash, PowerShell and cmd.exe all read the same way.
    """
    text = str(value)
    return f'"{text}"' if not text or any(c.isspace() for c in text) else text


if __name__ == "__main__":
    raise SystemExit(main())
