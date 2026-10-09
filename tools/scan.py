#!/usr/bin/env python3
"""Scan one frame, corrected on the host from the scanner's own reference.

    uv run python tools/scan.py --dpi 1800 --out scans/shaded_1800dpi.tif

The folder --out names may be made; the one above it must be there already.

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
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import export, library, locks, session, storage
from rps7200.awake import KeepAwake
from rps7200.console import (DeferredInterrupt, film_unconfirmed,
                             use_utf8_stdout)
from rps7200.direct import (DirectScanner, StoppedBeforePass, debug_from_env,
                            supports_infrared)
# The class itself, for checks made before any scanner is opened. Not the
# `DirectScanner` name below, which tests replace with a stand-in factory.
from rps7200.direct import DirectScanner as _Driver
from rps7200.mono import (
    MONO_CHANNEL,
    MONO_CHOICES,
    infrared_left_out,
    to_monochrome,
)
from rps7200.library import FilmNotes
from rps7200.session import (
    HeldOpen,
    debug_filing_into,
    filing_interrupt,
    keep_unfiled,
    kept_said,
    reference_refused,
    say_leftovers,
    say_reused,
)


def say_estimate(*, passes: int, resolution: int, infrared: bool,
                 fast_infrared: bool, calibrating: bool, metering: bool) -> float:
    """Print how long this run should take, before it starts.

    Returns the slow end, which is what the backgrounding warning is judged
    on: `estimate_seconds` sits at or below the library's medians, and this
    used to warn on it -- a two-pass bracket at 3600 dpi, when there was one,
    came to 7.9 minutes and no warning, its top pass pinned to the exposure
    ceiling, the slowest a pass can be.
    """
    from rps7200 import session

    return session.say_estimate(
        passes * session.estimate_seconds(resolution, infrared, fast_infrared),
        (session.CALIBRATION_S if calibrating else 0.0)
        + (session.METERING_S if metering else 0.0),
        say=lambda m: print(m, flush=True),
        warn=lambda m: print(m, file=sys.stderr, flush=True))


def longest_paths(args, spool: Path | None) -> list[Path]:
    """The longest path this run can write, each from the name it will
    really use and its real bound: the library entry with its record's
    temporary name, --out at its last free name with its writer's temporary
    name and its record's, and debug filing's spool."""
    from datetime import datetime, timezone

    meta = {"resolution_dpi": args.dpi, "channels": 4 if args.ir else 3}
    notes = FilmNotes(stock=args.stock, frame=args.frame,
                      subject=args.subject, notes=args.notes)
    paths: list[Path] = []
    if args.library is not None:
        paths.append(session.entry_bound(args.library, library.entry_id(
            meta, notes, datetime.now(timezone.utc))))
    out = session.copy_bound(Path(args.out))
    paths += [export.temp_name(out),
              session.written_beside(out.with_suffix(".json"))]
    if spool is not None:
        paths.append(session.spool_bound(spool))
    return paths


def preflight(ap: argparse.ArgumentParser, args, calibrating: bool) -> None:
    """Refuse a run its folders, paths or disks cannot take, before anything
    is asked or opened (`storage`).

    The library, the reference's folder and --out's may have their last
    folder made and never their parents: a drive that is not plugged in
    used to fail after the calibration and the pass, or be made again on
    C:. A path past Windows' 259 characters failed the same way. And the
    disks were never asked at all: a full one showed itself as a pass kept
    in a temporary folder. Each disk must keep `storage.SPACE_RESERVE` free
    beyond what the run writes there, debug filing's spool included where
    RPS7200_DEBUG is on; one that may fill is warned about, and one whose
    room cannot be read is said.
    """
    refused = [said for said in (
        storage.missing_root(args.library) if args.library else None,
        (storage.missing_root(Path(args.reference).parent) if calibrating
         else None),
        storage.missing_root(Path(args.out).parent)) if said]
    if refused:
        ap.error("; ".join(refused))
    spool = (_Driver.debug_root_for(args.library) / _Driver.DEBUG_SPOOL_DIR
             if debug_from_env() else None)
    too_long = storage.longest_too_long(longest_paths(args, spool))
    if too_long:
        ap.error("; ".join(too_long))
    needs = session.scan_space([session.frame_bytes(args.dpi, args.ir)],
                               library_root=args.library,
                               out_dir=Path(args.out).parent, spool=spool)
    short, unknown = session.check_space(needs)
    if short:
        ap.error("not scanning: " + "; ".join(short))
    tight, _ = session.check_space(needs, session.SPACE_WARN)
    for said in [*(f"this scan may fill the disk: {t}" for t in tight),
                 *unknown]:
        print(f"warning: {said}", file=sys.stderr)
    # --out's folder now, its last folder only: a pass the library refuses
    # is kept beside it (`keep_unfiled`), before --out itself is written.
    try:
        storage.make_root(Path(args.out).parent)
    except OSError as exc:
        ap.error(str(exc))


class _StoppedBetweenPasses(Exception):
    """Stopped at Ctrl-C between two things the scanner does, never inside one."""


def main() -> int:
    # What `_main` holds (`rps7200.locks`) is let go however it ends.
    with contextlib.ExitStack() as locked:
        return _main(locked)


def _main(locked: contextlib.ExitStack) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=1800)
    ap.add_argument("--out", default="scan.tif",
                    help="the delivered file. Its extension picks the format: "
                         ".tif or .jpg. A JPEG is the same picture at 8 bits, "
                         "still a negative, and cannot carry infrared. A file "
                         "already there is never replaced: the next free name "
                         "is taken, and said. Its folder may be made, never "
                         "the one above it")
    ap.add_argument("--quality", type=int, default=export.DEFAULT_QUALITY,
                    metavar="N", help="JPEG quality 60-100 (default "
                                      f"{export.DEFAULT_QUALITY}); ignored for TIFF")
    ap.add_argument("--ir", action="store_true", help="capture the infrared plane too")
    ap.add_argument("--fast-ir", dest="fast_ir", action="store_true",
                    default=True,
                    help="tie the infrared plane's cost to --dpi (default). An "
                         "untied pass costs ~220 s at every resolution; a tied "
                         "one costs what its lines cost -- 110 s at 1800 dpi, "
                         "25 s at 300. See docs/fast-infrared-plan.md.")
    ap.add_argument("--no-fast-ir", dest="fast_ir", action="store_false",
                    help="untie it: the fixed-cost infrared pass. Nothing "
                         "measured says it is better, and above ~3700 dpi the "
                         "two cost the same anyway.")
    ap.add_argument("--reference", default="calibration/shading.npz",
                    help="where to cache the shading reference")
    ap.add_argument("--reuse", action="store_true",
                    help="load the cached reference instead of calibrating")
    ap.add_argument("--no-shading", action="store_true",
                    help="return raw pixels, for comparison")
    ap.add_argument("--film-loaded", action="store_true",
                    help="the film is in the transport, so the calibration "
                         "may run without asking. Without it the tool asks, "
                         "and refuses where nobody can answer")
    ap.add_argument("--auto-exposure", action="store_true")
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
                    help="what is in the transport: a negative is metered per "
                         "channel to take the orange mask off before the ADC; "
                         "everything else keeps its cast. It also decides "
                         "whether --ir is refused (bw, kodachrome) and whether "
                         "one channel is delivered (bw)")
    ap.add_argument("--mono", dest="mono", action="store_true", default=None,
                    help="deliver one channel instead of three. On by default "
                         "for --film bw: a black and white scan is an RGB scan "
                         "on this hardware, and a consumer that has to guess "
                         "from the pixels gets it wrong -- channel correlation "
                         "does not separate B&W from colour negative here. The "
                         "library keeps all three regardless")
    ap.add_argument("--no-mono", dest="mono", action="store_false",
                    help="deliver all three channels even for --film bw")
    ap.add_argument("--mono-channel", default=MONO_CHANNEL,
                    choices=list(MONO_CHOICES),
                    help="what a monochrome file carries: the average of "
                         "the visible channels, or one of them alone "
                         "(default: %(default)s)")
    ap.add_argument("--library", nargs="?", const="library", default="library",
                    metavar="DIR",
                    help="file this scan in the reusable library, with its raw "
                         "bytes and calibration, so it can be re-decoded and "
                         "re-corrected later without rescanning (default: "
                         "library/)")
    ap.add_argument("--no-library", action="store_true",
                    help="do not file this scan. Its raw bytes are then gone "
                         "for good: they cannot be recovered from the TIFF, so "
                         "a later change to the decode or the correction cannot "
                         "be applied to it. A calibration's own lines are still "
                         "kept, in library/calibrations/ under the current "
                         "directory (RPS7200_DEBUG_ROOT's, where that is set)")
    ap.add_argument("--stock", default="", help="film stock, e.g. 'Kodak Gold 200'")
    ap.add_argument("--frame", default="", help="frame position on the roll")
    ap.add_argument("--subject", default="")
    ap.add_argument("--notes", default="", help="anything about this frame worth "
                                                "knowing later, e.g. 'dust top left'")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    args = ap.parse_args()
    if args.ir and not supports_infrared(args.film):
        ap.error(
            f"--ir with --film {args.film}: infrared is blind to it -- its "
            + ("grain" if args.film == "bw" else "cyan layer")
            + " absorbs infrared, so the pass would spend its ~212 s floor a "
            "frame and hand back the picture rather than the dust. Drop --ir. "
            "(Chromogenic C-41 black and white does clean properly: scan that "
            "as --film negative.)"
        )
    if args.no_library or args.library == "":
        # `--library ''` is how `tools/scan_roll.py` says "do not file", and
        # here it filed into the current directory -- `Path('')` is `.` --
        # beside an index.json, out of sight of `make verify`.
        args.library = None

    # Everything knowable before the device is opened is checked here: a
    # refusal after a calibration and metering has spent minutes on it.
    if args.dpi <= 0:
        ap.error(f"--dpi must be positive, got {args.dpi}")
    if not args.no_shading and not _Driver.correctable_at(args.dpi):
        ap.error(
            f"--dpi {args.dpi} cannot be shading-corrected on this scanner: its "
            f"calibration never gives a reference wider than "
            f"{_Driver.MAX_SHADING_COLUMNS} columns. Scan at 3600 dpi or "
            f"below, or add --no-shading for raw, striped pixels on purpose.")
    if export.FORMATS.get(Path(args.out).suffix.lower()) is None:
        ap.error(f"--out {args.out}: the extension picks the format, and must "
                 f"be one of {', '.join(sorted(export.FORMATS))}")
    # What the help promises, refused rather than passed to Pillow, which
    # turns 0 into 1 -- `--quality 9` for 90 was a heavily blocked JPEG
    # delivered without a word, after the scan had been paid for.
    if not 60 <= args.quality <= 100:
        ap.error(f"--quality {args.quality}: JPEG quality is 60-100")

    exposure_scale: float | list[float] = 1.0
    if args.exposure_scale:
        parts = [float(v) for v in args.exposure_scale.replace(",", " ").split()]
        exposure_scale = parts[0] if len(parts) == 1 else parts
    if args.auto_exposure and args.exposure_scale:
        print("--exposure-scale overrides --auto-exposure", file=sys.stderr)
    # The folders, the paths and the disks, before the film is asked about.
    ref_path = Path(args.reference)
    calibrating = not args.no_shading and not (args.reuse and ref_path.exists())
    preflight(ap, args, calibrating)
    # Before the device opens, and only when a calibration will run: it went
    # straight from INQUIRY into one, on whatever the transport held.
    if calibrating:
        unconfirmed = film_unconfirmed(args.film_loaded)
        if unconfirmed:
            ap.error(unconfirmed)
    if not args.ir:
        # No plane to acquire, so the bit governs nothing. Silently cleared now
        # that it is the default -- warning on every RGB scan about a flag
        # nobody asked for would be noise, where warning about one typed
        # explicitly was not.
        args.fast_ir = False

    if calibrating and reference_refused(ref_path):
        ap.error(str(reference_refused(ref_path)))
    if args.reuse and ref_path.exists() and not args.no_shading:
        say_reused(ref_path)
    say_estimate(
        passes=1, resolution=args.dpi, infrared=args.ir,
        fast_infrared=args.fast_ir,
        calibrating=calibrating,
        metering=args.auto_exposure and not args.exposure_scale)
    # What earlier runs left behind, before anything is sent.
    say_leftovers(args.library)
    # The library this run files into, held as in use from before the
    # scanner opens until this exits (`rps7200.locks`): `compact --write` and
    # the migrations rewrote entries beside a run filing into the same
    # library, and refuse beside it now -- as this refuses beside them.
    if args.library is not None:
        try:
            taken = locked.enter_context(locks.take(
                locks.LIBRARY, args.library, exclusive=False))
        except locks.Refused as exc:
            ap.error(f"not scanning: {exc.who} is rewriting the library "
                     f"{args.library}; run this once it has finished. "
                     "Nothing was sent.")
        if taken.note:
            print(f"warning: {taken.note}; going ahead", file=sys.stderr)
    # RPS7200_DEBUG decides, as everywhere else. This tool files its own
    # entries and claims each of those passes once it has filed it (below),
    # so debug filing leaves them out rather than writing every frame twice --
    # 43 GB of duplicate on a 38-frame roll at 7200 dpi, which is why this
    # used to say debug=False and so filed none of the metering probes either.
    # Held open by `HeldOpen`: the device closes when the block ends, and the
    # scanner's own exit -- debug filing -- waits for this tool's filing.
    # Ctrl-C finishes the pass in flight instead of abandoning its read --
    # which wedges the scanner. Whatever went wrong, the passes already
    # scanned are filed below: they used to be held only in `pending` and die
    # with the exception.
    interrupt = DeferredInterrupt()
    trouble: BaseException | None = None
    device: HeldOpen | None = None
    pending: list[dict] = []
    #: The scanner, once opened, for `debug_settle` after the filing below.
    scanner: DirectScanner | None = None
    try:
        # And the host out of idle sleep until the device has closed: a
        # machine that sleeps mid-pass abandons the read (`awake`).
        with interrupt, KeepAwake(say=lambda m: print(m, file=sys.stderr)):
            device = HeldOpen(DirectScanner(verbose=args.verbose, debug=None))
            # Debug filing beside this tool's entries, not in whatever
            # `./library` the shell happens to be in -- said before the
            # scanner opens, which holds that library as in use.
            debug_filing_into(device.scanner, args.library)
            with device as s:
                scanner = s
                info = s.inquiry()
                print(f"{info.vendor} {info.model}, firmware {info.firmware}")

                if not args.no_shading and not (args.reuse and ref_path.exists()):
                    print("calibrating (about 3-4 minutes; the vendor does this once "
                          "per power-on) ...", flush=True)
                print(s.ensure_shading(ref_path, reuse=args.reuse,
                                       skip=args.no_shading)["summary"])
                # A Ctrl-C through the calibration's three or four minutes was
                # told "stopping after the pass in flight", and then metering
                # and a whole pass ran anyway -- 138 s at 3600 dpi, 314 s at
                # 7200. Nothing visibly stopping is what makes an operator press
                # it again, and a second press lands mid-read: the wedge.
                if interrupt.requested():
                    raise _StoppedBetweenPasses(
                        "after the calibration, at Ctrl-C; nothing scanned")

                # Every pass from here asks first -- metering's probes go
                # through `scan` as the frame does -- so a Ctrl-C during the
                # calibration, metering or any pass stops before the next one
                # starts, filed or not. Asked only once a pass had landed, it
                # told the operator "stopping" through a calibration and
                # metering and then let a full pass start, and invited the
                # second Ctrl-C -- the one that abandons a read.
                scan_now = s.scan

                def scan_unless_stopped(*a, **kw):
                    if interrupt.requested():
                        raise _StoppedBetweenPasses(
                            f"after {len(pending)} pass"
                            f"{'' if len(pending) == 1 else 'es'} kept, "
                            "at Ctrl-C")
                    return scan_now(*a, **kw)

                s.scan = scan_unless_stopped

                # Everything the library needs is gathered while the session is open and
                # written after it closes: filing an entry gzips well over a hundred
                # megabytes, and holding the device open and idle through that has
                # preceded it going unresponsive.
                def hold(image, meta, capture) -> None:
                    if args.library is None:
                        return
                    # The RAW pixels, not the ones the scan returned. `scan()` hands
                    # back the *corrected* image -- that is what a caller wants to
                    # look at -- and keeps the uncorrected one in `last_pixels_raw`.
                    # Filing the returned array put shading into `scan.tif` while
                    # `corrections_applied` still said nothing was baked in, so
                    # `library.corrected()` shaded it a second time and `reconstruct`
                    # called every such entry a changed decode.
                    raw = getattr(s, "last_pixels_raw", None)
                    # A scanner that kept none -- a stand-in, never the
                    # driver -- leaves only the returned pixels, which are
                    # corrected exactly when the meta reports a correction.
                    # Filed, they are labelled so, as `FrameWriter` labels
                    # them: unlabelled, `library.corrected` shaded them a
                    # second time and `reconstruct` called the entry a
                    # changed decode.
                    corrections = None
                    if raw is None and meta.get("shading"):
                        corrections = ["shading"]
                        print("no raw pixels came with this pass; filing its "
                              "corrected pixels, labelled as corrected",
                              flush=True)
                    # Claimed only with its bytes, and answered for below once
                    # the filing is over: the spooled copy is deleted on that
                    # answer, not on the claim. It used to go at close(), on
                    # the claim alone, and a full library disk then lost
                    # the pass from both places.
                    receipt = (s.debug_claim(raw)
                               if raw is not None and capture.get("raw") is not None
                               else None)
                    pending.append(
                        dict(capture, inquiry=info, meta=meta,
                             image=image if raw is None else raw,
                             corrections=corrections, receipt=receipt)
                    )

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
                    fast_infrared=args.fast_ir,
                    # Asked again after metering: `scan_unless_stopped`
                    # asks only before, and a Ctrl-C during the probes
                    # otherwise still cost the whole pass.
                    should_stop=interrupt.requested,
                )
                hold(image, meta, s.capture_record())
    except BaseException as exc:                          # noqa: BLE001
        trouble = exc
        print(f"stopped: {type(exc).__name__}: {exc}" if str(exc)
              else f"stopped: {type(exc).__name__}", file=sys.stderr)

    # Every pass held in memory is its only copy until this loop files it, so
    # it runs under its own deferred Ctrl-C -- the first press waits for the
    # filing, as the scan's did for its read -- and one pass that cannot be
    # filed is reported and does not stop the ones behind it. Unguarded, a
    # Ctrl-C here (the operator had just been told a second one aborts) or one
    # full-disk save lost every pass after it, and the delivered file too.
    entries = []
    #: Passes the library would not take, said once filing is over, and
    #: where each was kept instead.
    unfiled: list[str] = []
    kept_at: list[Path] = []
    # Ctrl-C deferred through the filing as through the passes, and
    # through debug filing after it: see `session.filing_interrupt`.
    with filing_interrupt(say=lambda m: print(m, file=sys.stderr,
                                              flush=True)):
        try:
            for n, held in enumerate(pending, 1):
                # Each pass on its own. A bare loop let the first refusal --
                # a full disk, --library naming a file -- escape as a
                # traceback, and every pass after it and --out went with it:
                # all of them held only here, in memory.
                pixels, meta = held.pop("image"), held.pop("meta")
                receipt = held.pop("receipt", None)
                filing = dict(film=FilmNotes(stock=args.stock, frame=args.frame,
                                             subject=args.subject, notes=args.notes),
                              tags=args.tags, **held)
                entry = None
                try:
                    entry = library.save(pixels, meta, root=args.library, **filing)
                except Exception as exc:                     # noqa: BLE001
                    # Its raw data kept elsewhere, whole, where it can be
                    # moved into the library later -- beside --out, or in the
                    # system's temporary directory. See
                    # `session.keep_unfiled`. Compressed, as the passes filed
                    # here are: the device is closed by now.
                    kept, elsewhere = keep_unfiled(pixels, meta,
                                                   near=[Path(args.out).parent],
                                                   compress=True,
                                                   refused_by=args.library,
                                                   why=str(exc), **filing)
                    said = (f"pass {n} could not be filed in {args.library} "
                            f"({exc}); ")
                    said += (f"its raw data is kept in {kept} -- "
                             + kept_said(kept) if kept is not None
                             else "and could not be kept anywhere else either ("
                             + "; ".join(elsewhere) + ")")
                    print(said, file=sys.stderr, flush=True)
                    unfiled.append(said)
                    if kept is not None:
                        kept_at.append(kept)
                else:
                    entries.append(entry)
                finally:
                    # The answer the claim was waiting for: the spooled copy
                    # goes now if the pass was filed, and is filed by debug
                    # filing below if it was not.
                    if receipt is not None:
                        receipt(entry)
        finally:
            # Debug filing last, once every pass here is filed and answered
            # for: what it still holds is what this run did not keep --
            # metering probes, and a pass whose filing failed -- filed from
            # its own spooled copy.
            if device is not None:
                device.release()
            if scanner is not None:
                try:
                    scanner.debug_settle()
                except Exception as exc:                  # noqa: BLE001
                    print(f"debug filing: {exc}", file=sys.stderr)

    if trouble is not None:
        for e in entries:
            print(f"filed before stopping: {e}")
        return 130 if isinstance(trouble, (KeyboardInterrupt,
                                           _StoppedBetweenPasses,
                                           StoppedBeforePass)) else 1

    # Never over an earlier scan: two runs in one directory replaced
    # scan.tif and scan.json, and with --no-library that was the only copy
    # of the first. The next free name, as the window's output folder does,
    # and free for the record written beside it too: `--out scan.jpg` then
    # `--out scan.tif` wrote the second scan's scan.json over the first's.
    #
    # Under the deferred Ctrl-C, as the filing above is, and guarded: an
    # --out that cannot be written -- a missing drive, a file held open --
    # raised a traceback after the passes were filed, so the lines saying
    # where they were filed never printed; and with --no-library, or every
    # pass refused, this file is the only copy there is.
    out = Path(args.out)
    claimed: list[Path] = []
    failure: Exception | None = None
    #: Whether the picture is on disk, whatever became of its record: one
    #: failure for both said "could not write" of a picture that was there.
    written = False
    note = ""
    delivered = image
    with filing_interrupt(say=lambda m: print(m, file=sys.stderr,
                                              flush=True)):
        try:
            out = export.claim(Path(args.out), sidecars=(".json",))
            claimed.append(out)
            if out != Path(args.out):
                print(f"{args.out} is already there; writing {out.name} "
                      "instead")
            # Its own folder at most, never the ones above it: checked
            # before the scan, and gone since if it is not there now.
            storage.make_root(out.parent)
            if args.mono is None:
                args.mono = args.film == "bw"
            if args.mono:
                # One channel, so a consumer cannot mistake this for a colour
                # scan. The library keeps all three either way -- see
                # rps7200/mono.py.
                delivered = to_monochrome(image, args.mono_channel)
                meta = dict(meta, mono_channel=args.mono_channel,
                            channel_order=[args.mono_channel], channels=1)
            note = export.write(out, delivered, resolution=args.dpi,
                                quality=args.quality)
            written = True
            if args.mono:
                note = "; ".join(filter(None, (note,
                                               infrared_left_out(image))))
            # Beside and renamed over, as the picture is; and its values as
            # an entry's record writes them, where `default=str` wrote a
            # numpy number or an array as its printed form.
            library._write_atomic(out.with_suffix(".json"), json.dumps(
                meta, indent=2, default=library._plain))
        except Exception as exc:                          # noqa: BLE001
            failure = exc
        finally:
            export.release(claimed)
    if written:
        print(f"wrote {out}  {delivered.shape}  {delivered.dtype}"
              + (f"  (monochrome, {args.mono_channel})" if args.mono else ""))
    if note:
        print(note)
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
    if failure is not None and written:
        # The picture is whole; only the record beside it is not. Its
        # values are in the pass's entry too, where there is one.
        said = (f"wrote {out}, but its record {out.with_suffix('.json')} "
                f"could not be written ({failure})")
        if entries:
            said += ("; the pass's record is in its library entry "
                     + ", ".join(e.name for e in entries))
        print(said, file=sys.stderr)
    elif failure is not None:
        # What still holds the pass, which decides how bad this is.
        said = f"could not write {out} ({failure}); "
        if entries:
            # Not the window's Save As, which reaches only the passes of its
            # own session and the rolls it opens; this entry is neither.
            said += ("the pass is filed in the library as "
                     + ", ".join(e.name for e in entries)
                     + ", its raw bytes and reference with it -- nothing of "
                     "the scan is lost, though nothing writes the delivered "
                     "file again from an entry yet")
        elif kept_at:
            said += ("the pass is kept outside the library in "
                     + ", ".join(str(k) for k in kept_at) + " -- "
                     + kept_said(kept_at[0]))
        else:
            said += "nothing else holds this scan" + (
                " unless RPS7200_DEBUG kept it"
                if getattr(scanner, "debug", False) else "")
        print(said, file=sys.stderr)
    if unfiled:
        # Said again last, where it is read: the file above was written, and
        # this is what it cost. A pass not in the library is a failed run.
        for said in unfiled:
            print(said, file=sys.stderr)
        return 1
    return 1 if failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
