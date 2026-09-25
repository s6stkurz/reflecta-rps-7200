"""Driving the scanner from a user interface, without wedging it.

A UI cannot call `DirectScanner` directly. An infrared pass holds the device for
its ~212 s floor, and a main loop that blocks that long is a frozen window; but
the alternative -- touching the device from whichever thread felt like it -- is
worse, because there is no lock anywhere in this package. The only mutual
exclusion is `libusb_claim_interface`, which stops a second *process*, not a
second thread.

So the discipline is structural. One worker thread owns the scanner and is the
only thing in the process that speaks to it. Jobs go in on a queue, events come
out on another, and the UI thread does nothing but drain events and draw. A
second thread writes finished frames to disk, because gzipping a library entry
with the device open and idle is the state that preceded a wedge -- that is
`FrameWriter`, which lived in `tools/scan_roll.py` and now lives here so the
GUI and the roll tool share one copy.

Cancelling is two different things and they are not interchangeable:

`request_stop()`
    Cooperative, and always safe. The worker checks it before starting a pass
    and between frames of a roll -- `scan_roll` is a generator, so closing it
    ends the roll cleanly once the frame in flight has finished. A pass already
    running always runs to completion.

`force_abort()`
    Closes the transport out from under the worker, which is the only thing
    that unblocks a synchronous `libusb_bulk_transfer`. This is *abandoning a
    read mid-scan*, the specific act the whole driver is written to avoid: the
    frame is lost and the scanner will almost certainly need a power cycle at
    its own switch. It exists because sometimes that is the better trade, not
    because it is safe. Callers must confirm with the operator first.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from . import export, library, preview
from .direct import METER_EACH, DirectScanner
from .direction import FORWARD, REVERSED
from .framing import reversal_against
from .library import FilmNotes
from .mono import MONO_CHANNEL, to_monochrome, wants_mono
from .protocol import say_units

#: The infrared floor: an **untied** pass with infrared on holds the device this
#: long however few lines were asked for. Measured at 212-227 s across
#: resolutions, and the reason a short timeout once wedged the device -- so it
#: stays the conservative end of the range, because what it guards is a read
#: that must not be abandoned.
#:
#: A *tied* pass does not spend it at all; see :data:`INFRARED_UNTIED_S` and
#: :func:`estimate_seconds`.
INFRARED_FLOOR_S = DirectScanner.INFRARED_FLOOR_S

#: What an untied infrared pass actually costs, for the readout rather than the
#: timeout. Measured 2026-09-16 across five resolutions on one slide: 219.2,
#: 219.6, 219.8, 220.1, 220.3 s from 300 to 1800 dpi -- flat to 1.1 s over a
#: sixfold range -- and 221.0 s at 3600.
#:
#: **This supersedes the older 334 s figure at 3600 dpi** from the timing table
#: in `docs/dpi-tradeoff-plan.md`. The two disagree because they are different
#: film at different exposures and scan time tracks exposure; the sweep is the
#: one that covers the whole range in a single run at a single held exposure,
#: which is what a table of resolutions needs to be comparable at all.
INFRARED_UNTIED_S = 219.8

#: Where tying the infrared plane to the resolution stops buying anything.
#:
#: Measured 2026-09-16: a tied pass costs `7.46 s + 59.88 ms/line` and an
#: untied one a flat ~219.8 s, so the two curves meet at 3546 lines -- about
#: **3709 dpi**. Below it the floor is the dominant cost and removing it is
#: worth nearly everything: -88.8% at 300 dpi, -49.8% at 1800.
#:
#: The constant is 3600 rather than 3709 because 3600 is the resolution the
#: device actually offers nearest the crossing, and there the saving already
#: measured **-3.4%** -- seven seconds. Rounding up to 3709 would have the
#: window offer a choice at 3600 that measurement says is not one.
#: See `docs/fast-infrared-plan.md`.
INFRARED_TIE_CROSSOVER_DPI = 3600

#: What one SLIDE sub-frame command can move, from the calibrated law:
#: distance = STEP_MM x param + OVERHEAD_MM, for param 1 and param 8.
FINE_MIN_MM = DirectScanner.STEP_MM + DirectScanner.OVERHEAD_MM
FINE_MAX_MM = (DirectScanner.STEP_MM * DirectScanner.MAX_CORRECTION_PARAM
               + DirectScanner.OVERHEAD_MM)

#: How many commands a rewind may spend on backlash before giving up. A roll
#: leaves the transport loaded forward, so the first backward command is a
#: direction change and two to three of them are swallowed -- measured
#: 2026-09-21, where a 14-frame rewind ran first time after one roll and had
#: its first command swallowed after the next, the device healthy either way.
BACKLASH_COMMANDS = 3


def _frame(position: int | None) -> str:
    """A transport position as the frame number a person reads, or "?".

    The counter is 0-based and every number this driver shows is not: frame N
    of a strip is where the counter reads N-1. Converted in one place so a log
    line and the window cannot count the same film two ways.
    """
    return "?" if position is None else str(position + 1)


def rewind(scanner, frames: int, say=None) -> int | None:
    """Wind the film back, one frame at a time, checking each one landed.

    One at a time deliberately. `retreat(steps=N)` exists, but the wait
    underneath only watches for the position to *change*, so a multi-step call
    returns as soon as it has moved at all -- it cannot tell sixteen frames
    from one.

    Checking each one is the other half. A rewind that got three of fourteen
    and one that got none are the same silence otherwise, and a roll queued
    behind either scans frames it has mis-numbered.

    The first command or two may do nothing, and that is expected rather than a
    failure -- see `BACKLASH_COMMANDS`. So a no-op is tolerated while the film
    has not started moving, and is the end of the strip once it has.

    Returns where the film ended up, or None if it stopped short: a caller that
    gets None **must not go on**, because everything after this assumes the
    film is where it was asked to be.

    Lives here rather than in `tools/scan_roll.py` so the window and the
    command line share one, the same arrangement `plan_nudges` already has --
    a second copy of this drifts, and the copy that drifted was the window's.
    """
    def tell(message):
        if say is not None:
            say(message)

    start = now = scanner.position()
    tell(f"rewinding {frames} frame(s) from frame {_frame(start)}")
    done, swallowed = 0, 0
    while done < frames:
        before = scanner.position()
        landed = scanner.retreat()
        now = scanner.position()
        if landed is None or now == before:
            if done == 0 and swallowed < BACKLASH_COMMANDS:
                swallowed += 1
                tell(f"  (no movement yet -- backlash, command "
                     f"{swallowed}/{BACKLASH_COMMANDS})")
                continue
            tell(f"  stopped after {done} of {frames}: still on frame "
                 f"{_frame(before)}")
            return None
        done += 1
        tell(f"  {done}/{frames}: frame {_frame(before)} -> {_frame(now)}")
    # The last position it confirmed, when the closing read comes back empty:
    # a READ_STATE straight after a transport command often does, and None
    # here means "stopped short" to every caller -- which it did not.
    end = scanner.position()
    return now if end is None else end


#: The highest transport position taken at its word -- see
#: `DirectScanner.LAST_PLAUSIBLE_POSITION`, which is its home because both
#: roll loops decide with it. Named here too for the window and the seek.
LAST_PLAUSIBLE_POSITION = DirectScanner.LAST_PLAUSIBLE_POSITION

#: How often, and how far apart, the transport is asked where the film is
#: before a roll gives up on knowing. A READ_STATE sent right after a
#: transport command comes back empty every time, and a new position has taken
#: up to 6.2 s to appear (`DirectScanner._whole_frames`); eight asks a second
#: apart outlast that. Module-level so a test can take the waiting out.
POSITION_READS = 8
POSITION_POLL_S = 1.0

#: What one whole frame forward costs, for the estimates a person reads before
#: a roll moves the film. Measured from the stored walks: the gap between
#: consecutive frames' filed prescans less the later pass's own duration, 38
#: pairs across four walks (2026-09-21 to -23 and aligned-strip), median 4.6 s,
#: 3.6 to 4.8 s between the 10th and 90th percentiles, on timestamps with one
#: second's resolution. It includes the host's work around the move.
#:
#: **A move backwards has never been timed.** There is no figure for it here
#: on purpose: an estimate that invents one reads exactly like one that
#: measured it.
FORWARD_FRAME_S = 4.6


class FilmNotPlaced(RuntimeError):
    """The film could not be put where a roll starts, so nothing was scanned.

    Raised rather than returned so it takes the failed-job path: the window
    shows it where it shows a transport fault, and the job behind it is not
    mistaken for one that finished. Its text is the sentence the operator
    reads, so it says what to do.
    """


def _ask_position(scanner) -> int | None:
    """What the transport's counter reads, asking again while it says nothing.

    None only once every ask has come back empty. A stand-in that cannot say at
    all -- or says it by raising -- is the same answer: not known.
    """
    for attempt in range(POSITION_READS):
        if attempt:
            time.sleep(POSITION_POLL_S)
        try:
            here = scanner.position()
        except Exception:                                # noqa: BLE001
            here = None
        if here is not None:
            return int(here)
    return None


def plausible(position: int | None) -> bool:
    """Whether a counter reading is a place on a strip at all."""
    return DirectScanner.plausible_position(position)


def seek(scanner, target: int, say=None) -> int:
    """Put frame ``target + 1`` of the strip in the gate, or refuse.

    Frame numbers are places on the strip: frame N is where the transport's
    own counter -- READ_STATE byte 2, which resets when a strip goes in and
    follows the scanner's keys as well as this driver's moves -- reads N-1.
    A roll used to count from wherever the film happened to be, so a roll of
    frames 1 to 15 started with the film on frame 11 scanned 11 and called it
    1, and nothing anywhere said so.

    Reads where the film is, then winds back with the checked :func:`rewind`
    or steps forward one frame at a time, each checked, and reads again at the
    end. Nothing moves when the film is already there.

    Refuses, by raising :class:`FilmNotPlaced`, whenever it cannot be sure: the
    counter will not answer, answers with a number no strip has, the strip ends
    first, a rewind stops short, or the film is not on the frame afterwards.
    Every one of those would otherwise be a roll numbering frames it is not
    on. Returns the position it arrived at, which is ``target``.

    Waits for the lamp first. While it warms -- about 80 s from cold -- the
    scanner answers NOT READY to every command, READ_STATE included: that is
    `DirectScanner.wait_warm`'s docstring, not a measurement made here. If it
    holds, a roll started straight after power-on would hear nothing from the
    counter and refuse -- shown on a test double built on that premise, never
    seen on the scanner. `wait_warm` sends only TEST UNIT READY, and REQUEST
    SENSE to read why one was refused: no transport command and no scan.

    Above the seam on purpose. It speaks only through `wait_warm`,
    `position`, `advance` and `retreat`, so the demo's stand-in runs it
    unchanged -- the arrangement CLAUDE.md asks for, and the reason it is not
    inside `scan_roll`, which each backend has its own copy of.
    """
    def tell(message):
        if say is not None:
            say(message)

    frame = target + 1
    if not plausible(target):
        raise FilmNotPlaced(
            f"frame {frame} is past the {LAST_PLAUSIBLE_POSITION + 1} frames "
            "a strip is taken to have, so nothing was scanned")
    waiting = time.monotonic()
    scanner.wait_warm()
    waited = time.monotonic() - waiting
    if waited >= 1.0:
        tell(f"waited {waited:.0f} s for the lamp to warm up before asking "
             "where the film is")
    here = _ask_position(scanner)
    if here is None:
        raise FilmNotPlaced(
            "the transport would not say which frame the film is on, so "
            "nothing was scanned: a roll numbers its frames by where they are "
            "on the strip, and without that every number would be a guess. "
            "The scanner says nothing while its lamp warms up, about 80 s "
            "after it is switched on -- if it was just switched on, wait a "
            "minute; otherwise check the strip is in. Then start the roll "
            "again.")
    if not plausible(here):
        raise FilmNotPlaced(
            f"the transport says the film is on frame {here + 1}, which no "
            "strip has -- a counter left over from before the strip went in "
            "reads like that. Nothing was scanned. Take the strip out and put "
            "it back, which reset the counter to frame 1 the one time this "
            "was seen, then start the roll again.")

    if here > target:
        tell(f"the film is on frame {here + 1}; winding back "
             f"{here - target} to frame {frame}")
        if rewind(scanner, here - target, say=say) is None:
            raise FilmNotPlaced(
                f"the film stopped short of frame {frame} while winding back, "
                "so nothing was scanned -- the roll would have numbered "
                "frames it was not on")
    elif here < target:
        tell(f"the film is on frame {here + 1}; advancing "
             f"{target - here} to frame {frame}")
        at = here
        while at < target:
            landed = scanner.advance()
            if landed is None or landed <= at:
                raise FilmNotPlaced(
                    f"the strip ended on frame {at + 1}, before frame "
                    f"{frame}, so nothing was scanned")
            tell(f"  frame {at + 1} -> {landed + 1}")
            at = landed

    arrived = _ask_position(scanner)
    if arrived != target:
        raise FilmNotPlaced(
            f"the film should be on frame {frame} and the transport says "
            f"frame {_frame(arrived)}, so nothing was scanned")
    return arrived


#: Written into every manifest since frame numbers became places on the strip
#: (see :func:`seek`), under the key ``numbering``. A manifest without it
#: numbered its frames from wherever that walk or roll started, which is only
#: the same thing when it started on frame 1.
NUMBERING = "strip"


def _counted_shift(record) -> int | None:
    """How far one old record's number sat behind the strip's, by its own
    recorded position: frame ``n`` recorded on the counter's 5 was counted
    ``5 - (n - 1)`` behind.

    None for a record with no number, no position, or a position no strip
    has -- the stale 72 of `docs/protocol.md` section 9 is a counter left over
    from before the strip went in, and a shift worked out from it is 72 frames
    of nothing.
    """
    try:
        number = int(record["number"])
        position = int(record["transport_position"])
    except (KeyError, TypeError, ValueError):
        return None
    return position - (number - 1) if plausible(position) else None


def legacy_shift(manifest: dict) -> int | None:
    """How far a manifest's frame numbers sit behind the strip's own.

    0 for a manifest that says it numbers by the strip. For an older one, what
    its frames' recorded transport positions say: frame ``n`` of a walk that
    started on the counter's 5 was on 5 + (n - 1), a shift of 5. The commonest
    is taken, and it is only a best guess for what has no position of its own
    -- a roll's ``start_at`` and ``only``, `approved.json`, a roll that died
    before its first frame. None when no frame recorded a position a strip can
    have, which says nothing either way; a reading no strip has is not
    counted at all, so the stale 72 cannot outvote a short walk.

    One walk has one shift, because it numbered its frames once per advance
    from wherever it started. A `roll.json` need not: 8a9ba17 merged a resumed
    roll into the file its earlier run left, and each run counted from wherever
    the film then was, so "Start at 4" with the film left on frame 3 files
    frames 1-3 at positions 0-2 and frames 4-6 at 5-7 -- two shifts, three
    frames each, in one file. :func:`renumbered` follows each frame's own
    position for that reason, and this is not what it numbers frames by.
    """
    if manifest.get("numbering") == NUMBERING:
        return 0
    seen: dict[int, int] = {}
    for record in manifest.get("frames") or ():
        shift = _counted_shift(record)
        if shift is not None:
            seen[shift] = seen.get(shift, 0) + 1
    if not seen:
        return None
    return max(seen, key=lambda k: seen[k])


def _is_one_run(manifest: dict) -> bool:
    """Whether an old manifest holds what one walk or one roll counted.

    A walk's does -- each walk wrote `survey.json` afresh -- and says it is a
    walk with ``dry_run``: at the top level from the session, inside
    ``settings`` from `tools/scan_roll.py`. So does every `roll.json` the
    tool wrote, which is the file with ``dry_run`` only inside ``settings``:
    every version of the tool began its manifest with no frames and never
    read one back. Only the session's `roll.json` merged runs -- 8a9ba17
    resumed a roll into the file the last one under that name left -- and
    the session writes ``dry_run`` at the top level, so a file the tool
    wrote and the session then resumed into reads as the session's.

    False where neither says, which leaves a file to be read as several.

    A walk that adds to the one before it (`Roll.extend_walk`) does merge two
    walks into one `survey.json` -- but it writes ``numbering: strip``, so
    that file never reaches here, and every walk's file that does was written
    afresh.
    """
    settings = manifest.get("settings")
    if "dry_run" in manifest:
        return bool(manifest["dry_run"])
    return isinstance(settings, dict) and "dry_run" in settings


def _runs(records: list[dict], shift: int, one_run: bool) -> dict[int, int]:
    """The shift of the run each old record sits in, by its place in the file.

    A run is what one walk, or one roll, counted: its numbers went up once
    per advance from wherever it started, so one shift holds across it.

    A file that is ``one_run`` (:func:`_is_one_run`) is all on ``shift``. A
    `roll.json` the session wrote can hold several: 8a9ba17 merged every roll
    into the file the last one left under that name, and a roll with no name
    typed was named by the date, so every such roll of a day went into one
    file. There a run is records next to each other whose positions give one
    shift, and a lone record is a run too: a roll of one frame, which is one
    way to take a frame again. Except between two stretches of one shift it
    does not share, which is read as one misread counter inside a run, 5, 5,
    7, rather than three rolls placed so that the first and the last agree.
    Neither has been seen; the second takes more.

    A record with no position a strip has goes with the runs either side of
    it when they agree, or with the one beside it at an end of the file.
    Between two that disagree it has none, and nor does anything in a file
    where no record has a shift; those are left to the manifest's.
    """
    if one_run:
        return dict.fromkeys(range(len(records)), shift)
    blocks: list[tuple[int, list[int]]] = []
    for i, record in enumerate(records):
        s = _counted_shift(record)
        if s is None:
            continue
        if blocks and blocks[-1][0] == s:
            blocks[-1][1].append(i)
        elif (len(blocks) >= 2 and len(blocks[-1][1]) == 1
              and blocks[-2][0] == s):
            lone = blocks.pop()
            blocks[-1][1].extend(lone[1] + [i])
        else:
            blocks.append((s, [i]))
    spans = [(members[0], members[-1], s) for s, members in blocks]
    run: dict[int, int] = {}
    for first, last, s in spans:
        run.update(dict.fromkeys(range(first, last + 1), s))
    for i in range(len(records)):
        if i not in run:
            before = [s for _, last, s in spans if last < i][-1:]
            after = [s for first, _, s in spans if first > i][:1]
            sides = set(before + after)
            if len(sides) == 1:
                run[i] = sides.pop()
    return run


def renumbered(manifest: dict, fallback: int = 0, say=None) -> dict:
    """A manifest with its frame numbers moved onto the strip's.

    Each frame goes where its own recorded transport position says it was --
    position + 1 -- and never stays at its number, which is what was relative.
    A frame with no position is moved by the shift of the run it sits in (see
    :func:`_runs`), or by the manifest's, :func:`legacy_shift`; ``fallback`` is
    the shift to use when the manifest cannot say: a roll that died before its
    first frame is numbered the way the walk beside it was, because that is
    where its frame numbers came from.

    Its own position and not the manifest's one shift, because a manifest can
    hold two honestly: a `roll.json` 8a9ba17 merged across a "Start at N, same
    roll name" resume, each run counted from wherever the film then was (see
    :func:`legacy_shift`). One shift for all of it misnumbered one run, and a
    resume then wrote those numbers back under ``numbering: strip`` for good --
    the records of the frames really scanned replaced, and a scan filed under
    another frame's number.

    Two exceptions, and each is told to ``say``:

    - **A position no strip has** -- the stale 72 -- is no place at all, so the
      frame is numbered by its run, the way the roll loop keeps its count past
      such a reading (`DirectScanner.place_on_strip`). Followed, it made frame
      1 of a walk frame 73: past the end of every strip, so the sheet could
      never scan it, and a resume wrote 73 back for good.
    - **A frame whose position gives the number another frame's does**, when
      its run puts it elsewhere. A misread counter looks like that: positions
      5, 5, 7 for frames counted 1, 2, 3 were frames 6, 6 and 8, two pictures
      under one number, and the run -- numbers counted once per advance, one
      apart -- puts the middle one on 7. It moves by its own run: the shift
      of a whole merged file belongs to one of its runs, and moving by it
      filed a scan under a frame never scanned. In a file that is one run --
      a walk, or a `roll.json` the tool wrote (:func:`_is_one_run`) -- a
      frame such a move lands on is moved by the run's shift as well, round
      after round, because one run numbered no two frames alike: an advance
      that did not move, 5, 6, 6, 7, reads 6, 7, 8, 9 and says frames 3 and
      4. In a merged file only a frame that collided moves; the frame it
      lands on can be another run's, and pushing that one along filed it as
      a frame never scanned.

    Where two runs of a merged file put two frames on one place, both are
    kept there and it is said. That is the film going over a place twice --
    wound back, or the strip put in again, between two rolls into one file --
    and both pictures are of that frame; no two numbers that kept them apart
    would both be true. A misread and a film that really went back look the
    same here, and so do a misread at either end of the session's
    `roll.json` and a roll of one frame, which this reads as the roll: it
    keeps the place its own counter named. Nothing stored has any of it --
    every manifest under `rolls/` with positions has one shift throughout,
    213 frames across 25 of them, checked 2026-09-23.

    ``start_at``, ``only`` and ``wanted`` carry no positions and are moved by
    the manifest's shift.

    Returns the manifest unchanged when it already numbers by the strip, and a
    copy otherwise; nothing on disk is rewritten.
    """
    if not manifest or manifest.get("numbering") == NUMBERING:
        return manifest
    shift = legacy_shift(manifest)
    if shift is None:
        shift = fallback

    def moved(value, by=None):
        try:
            return int(value) + (shift if by is None else by)
        except (TypeError, ValueError):
            return value

    def moved_all(values):
        return None if values is None else [moved(v) for v in values]

    records = [dict(record) for record in manifest.get("frames") or ()]
    one_run = _is_one_run(manifest)
    run = _runs(records, shift, one_run)
    own: dict[int, int] = {}          # what each frame's position says
    nowhere: dict[int, int] = {}      # a reading no strip has
    for i, record in enumerate(records):
        try:
            position = int(record["transport_position"])
        except (KeyError, TypeError, ValueError):
            continue
        if plausible(position):
            own[i] = position + 1
        else:
            nowhere[i] = position
    held: dict[int, int] = {}
    for n in own.values():
        held[n] = held.get(n, 0) + 1

    numbers: dict[int, Any] = {}
    for i, record in enumerate(records):
        if i in own:
            numbers[i] = own[i]
            # Its run, and not the file's commonest shift, which in a merged
            # file can be the other run's.
            if held[own[i]] > 1 and i in run:
                ran = moved(record.get("number"), run[i])
                if isinstance(ran, int):
                    numbers[i] = ran
        elif "number" in record:
            numbers[i] = moved(record["number"], run.get(i))
    # In one run, a frame moved off a collision can land where another's own
    # position put it, one that collided with nothing -- an advance that did
    # not move, 5, 6, 6, 7, puts frame 3 on 8, frame 4's place -- and one run
    # numbered no two frames alike. So that frame takes the run's shift too,
    # round after round until none is left; a frame taken off its own
    # position never goes back, so it ends. Not in a merged file, where the
    # frame landed on can be another run's: moved by this run's shift, it was
    # filed as a frame never scanned, and pushed on the one it landed on.
    while one_run:
        taken: dict[Any, int] = {}
        for n in numbers.values():
            taken[n] = taken.get(n, 0) + 1
        landed = [i for i in own if numbers[i] == own[i]
                  and taken[own[i]] > 1
                  and isinstance(moved(records[i].get("number")), int)
                  and moved(records[i].get("number")) != own[i]]
        if not landed:
            break
        for i in landed:
            numbers[i] = moved(records[i]["number"])

    roll = manifest.get("roll") or "a roll"
    counted = [record.get("number") for record in records]
    frames = []
    for i, record in enumerate(records):
        if i in numbers:
            record["number"] = numbers[i]
            if say is not None and i in own and own[i] != numbers[i]:
                say(f"frame {counted[i]} of {roll} recorded the transport on "
                    f"frame {own[i]}, a number another of its frames has as "
                    f"well; numbered as frame {numbers[i]}, where the frames "
                    "around it put it -- look at its prescan before trusting "
                    "either")
            if say is not None and i in nowhere:
                say(f"frame {counted[i]} of {roll} recorded the transport's "
                    f"counter at {nowhere[i]}, which no strip has, so it is "
                    f"numbered as frame {numbers[i]}, where the frames around "
                    "it put it")
        if isinstance(record.get("number"), int):
            record["index"] = record["number"] - 1
        frames.append(record)
    places: dict[int, list[int]] = {}
    for i, n in numbers.items():
        if isinstance(n, int):
            places.setdefault(n, []).append(i)
    for n, sharing in sorted(places.items()):
        if say is None or len(sharing) < 2:
            continue
        named = ", ".join(str(counted[i]) for i in sharing[:-1])
        both = "both" if len(sharing) == 2 else "all"
        # One run cannot have gone over a place again between two rolls, and
        # after the rounds above only frames it numbered alike share one.
        why = ("the file numbers them alike, which one walk or one roll, "
               "counting once per advance, never did" if one_run else
               "the film went over that place again -- wound back, or the "
               "strip put in again, between two rolls filed under one name "
               "-- or a counter misread")
        say(f"frames {named} and {counted[sharing[-1]]} of {roll} are {both} "
            f"frame {n} of the strip: {why}. Kept, "
            f"{both} as frame {n}; look at the pictures before trusting any "
            "of them")
    out = dict(manifest)
    if "frames" in manifest:
        out["frames"] = frames
    for key in ("only", "wanted"):
        if key in out:
            out[key] = moved_all(out[key])
    if out.get("start_at") is not None:
        out["start_at"] = moved(out["start_at"])
    if isinstance(out.get("settings"), dict):
        settings = dict(out["settings"])
        if "only" in settings:
            settings["only"] = moved_all(settings["only"])
        if settings.get("start_at") is not None:
            settings["start_at"] = moved(settings["start_at"])
        out["settings"] = settings
    out["numbering"] = NUMBERING
    return out


def walked_prescans(folder, manifest: dict,
                    say=None) -> list[tuple[int, Path, dict]]:
    """A walk's prescans, each as ``(frame number, file, record)``.

    Taken from the walk's own records -- the frames it lists with a `prescan`
    file that is still there, numbered through :func:`renumbered` -- and never
    from the files in the folder. A second walk into the same date-named
    folder rewrites `survey.json` and leaves the first walk's extra
    `prescanNN.tif` behind, named by *that* walk's count: `rolls/2026-09-23`
    holds prescan01-02 from the walk its survey lists, at transport positions
    5 and 6, beside prescan03-06 from an earlier walk, at 2 to 5. Read by file
    name and one shift, those four were frames 8 to 11, and prescan06 was a
    second picture of the place prescan01 shows.

    The window's contact sheet and the roll tool's ``--approved`` both read a
    walk through this, so they cannot key one folder two ways.
    """
    folder = Path(folder)
    out: list[tuple[int, Path, dict]] = []
    for record in renumbered(manifest, say=say).get("frames") or ():
        name = record.get("prescan")
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if not name or not (folder / name).exists():
            continue
        out.append((number, folder / name, record))
    return out


def raw_bytes_disagree(shape: tuple[int, ...], layout: dict[str, Any] | None,
                       meta: dict[str, Any] | None = None) -> dict[str, tuple]:
    """Where raw bytes laid out like this cannot be the pass with this shape.

    Empty when they can. Every writer that files bytes beside pixels asks this
    first: bytes of another pass decode to a different photograph, which is
    the one failure the library exists to make impossible.
    """
    layout = dict(layout or {})
    actual = {
        "lines": shape[0],
        "width": shape[1],
        "channels": shape[2] if len(shape) > 2 else 1,
    }
    # The rows the bytes can decode to: what arrived, not what GET PARAMETERS
    # declared, less what the 7200 dpi realignment trimmed. Judged against the
    # declared count, every pass that ended early -- the one whose bytes
    # matter most -- and every 7200 dpi pass looked like another pass's bytes,
    # and was filed without them.
    received = layout.get("lines_received")
    channels = layout.get("channels")
    if received is not None and channels:
        layout["lines"] = int(received) // int(channels)
    if layout.get("lines") is not None:
        layout["lines"] = (int(layout["lines"])
                           - int((meta or {}).get("stagger_realigned") or 0))
    # Only fields the layout actually declares are judged; an absent one says
    # nothing, and dropping good bytes over it would be its own bug.
    return {
        k: (layout[k], actual[k])
        for k in actual
        if layout.get(k) is not None and layout[k] != actual[k]
    }


def roll_frame_label(roll: str, number: int) -> str:
    """The `film.frame` every roll entry carries: ``<roll>-<NN>``.

    One format for the window and `tools/scan_roll.py`, which wrote
    ``<roll>/<NN>`` and so could never be found from the window.
    """
    return f"{roll}-{int(number):02d}"


def roll_membership(roll: str, number: int, kind: str, folder: Any) -> dict[str, Any]:
    """What a roll entry records about its place: roll, frame, and which pass.

    ``kind`` is ``"frame"`` or ``"prescan"``. A walk's prescan and the frame
    later scanned there share a roll and a number, and joining on those alone
    let Export deliver a 300 dpi prescan as the frame at full resolution.
    """
    return {"roll": str(roll), "number": int(number), "kind": kind,
            "folder": str(folder)}


# ---------------------------------------------------------------------------
# A roll's folder, and its own files: survey.json, roll.json, approved.json
# ---------------------------------------------------------------------------


def new_roll_name(rolls, now: float | None = None) -> str:
    """A name no roll under `rolls` has yet: the date and the time, to the second.

    What a roll is called when nobody named it. It used to be the date alone,
    so every unnamed walk and roll of a day went into one folder: a second
    walk replaced the first one's survey.json and prescans, a second roll
    wrote over frameNN.tif and merged into roll.json, and the sheet's turns
    for one strip were applied to the next. Still starting with the date, so
    the folder sorts by it and `folder_created` reads it.
    """
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))
    name, n = stamp, 2
    while (Path(rolls) / name).exists():
        name, n = f"{stamp}-{n}", n + 1
    return name


def roll_dir(rolls, name: str) -> Path:
    """The one folder a roll called `name` lives in, under `rolls`.

    One function for the session, the window's `approved.json`, a reopened
    roll and `tools/scan_roll.py`. They derived it three ways -- the name as
    typed, `_safe` of it with "roll" for an empty one, and today's date -- so
    one roll's frames, decisions and walk could land in three folders.

    The name is made safe to be one folder (`_safe`): no separators, no
    ``..``, nothing absolute, no Windows device name. A folder that already
    has exactly this name is kept as it is, so a roll made before names were
    cleaned -- `rolls/Gold 200` -- is still the folder that name finds. An
    empty name, or one of which nothing survives cleaning, is never a shared
    default such as `rolls/roll`; it is a new one (`new_roll_name`), and the
    caller takes it from the result's `.name`.
    """
    rolls = Path(rolls)
    name = (name or "").strip()
    as_typed = rolls / name
    if (name not in ("", ".", "..") and not any(c in name for c in "/\\:")
            and as_typed.parent == rolls and as_typed.is_dir()):
        return as_typed
    cleaned = _safe(name, fallback="")
    return rolls / (cleaned or new_roll_name(rolls))


def recorded_roll_name(folder) -> str | None:
    """The name a roll folder's own manifest gives its roll, if it has one.

    A frame's library label is ``<roll>-<NN>`` (`roll_frame_label`), and a
    roll is found from its entries by that roll -- so a roll added to must go
    on calling itself what it did, whatever its folder is called now. A
    duplicate or a renamed folder keeps the name it was scanned under.
    """
    for manifest in ("survey.json", "roll.json"):
        try:
            name = read_manifest(Path(folder) / manifest).get("roll")
        except ValueError:
            continue
        if isinstance(name, str) and name.strip():
            return name
    return None


#: What a manifest's previous version is kept as, beside it: the file as it
#: stood before this run or this commission first wrote over it.
PREVIOUS = ".bak"


def write_manifest(path, data: dict, keep_previous: bool = False) -> None:
    """Write a roll's JSON beside itself, then rename it over: old or new, never half.

    These were truncated and rewritten in place, once a frame, for hours. A
    kill or a full disk between the two left an empty `roll.json`: the roll
    dropped out of the window's list, could not be reopened, and the next
    resume read nothing and wrote a fresh one over it.

    Serialised before anything on disk is touched, so a value JSON cannot
    carry costs this write and not the file already there. ``keep_previous``
    copies that file to ``<name>.bak`` first -- once per run, not once per
    frame, so the copy is the manifest as it stood before this run began:
    a walk that replaced another strip's, or a resume that merged wrongly,
    can still be undone by hand.
    """
    path = Path(path)
    text = json.dumps(data, indent=2, default=str)
    temp = path.with_name(f".{path.name}.part")
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        # A full disk, most likely: the file already there is untouched, and
        # the half-written one beside it is not left to be mistaken for it.
        temp.unlink(missing_ok=True)
        raise
    if keep_previous and path.exists():
        try:
            shutil.copyfile(path, path.with_name(path.name + PREVIOUS))
        except OSError:
            pass                        # the copy is a courtesy; the write is not
    os.replace(temp, path)


def read_manifest(path, say=None) -> dict:
    """A roll's JSON: ``{}`` when there is none, and never a silent ``{}`` otherwise.

    A file that does not parse falls back to the version kept beside it
    (`write_manifest`), and says so. Raises ValueError, naming the file, when
    neither can be read -- a reader that took a damaged manifest for an empty
    one reported a roll as having no frames, and a writer that did the same
    replaced it.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data
    except (OSError, ValueError) as exc:
        problem = exc
    kept = path.with_name(path.name + PREVIOUS)
    try:
        data = json.loads(kept.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        if say is not None:
            say(f"{path} cannot be read ({problem}); read {kept.name}, the "
                "version before it, instead")
        return data
    raise ValueError(f"{path} cannot be read: {problem}")


def set_aside(path) -> Path:
    """Move a file that cannot be read out of the way, under a name that says so.

    Before anything is written where it was: it may be the only record of a
    roll, and a hand can often recover what a parser cannot.
    """
    path = Path(path)
    aside = _unclaimed(path.with_name(path.name + ".unreadable"))
    os.replace(path, aside)
    return aside


def earlier_manifest(path, say=None) -> dict:
    """The manifest a run is about to add to, for a writer.

    As `read_manifest`, except that a file neither it nor its kept version can
    read is moved aside (`set_aside`) and said, and the run starts a new one:
    a resume that cannot tell what was done still has a roll to scan, and the
    file it could not read is kept rather than written over.
    """
    try:
        return read_manifest(path, say=say)
    except ValueError as exc:
        aside = set_aside(path)
        if say is not None:
            say(f"{exc}; kept as {aside.name}, and a new "
                f"{Path(path).name} started beside it")
        return {}


def walk_shift(folder) -> int:
    """How the walk beside a roll numbered its frames, for a legacy roll.

    See :func:`legacy_shift`. 0 when there is no walk or it cannot say,
    which leaves the numbers exactly as they were.
    """
    try:
        return legacy_shift(read_manifest(Path(folder) / "survey.json")) or 0
    except ValueError:
        return 0


def keep_first_numbering(path, earlier: dict) -> None:
    """Keep a manifest as first written before its frame numbers are moved.

    A resume moves an old file's numbers onto the strip's (`renumbered`) and
    writes them back, for good -- and that is a best guess from recorded
    positions, not a fact. The file as it was is copied once, to
    ``<name>.legacy``, so the numbering it was written with is never lost.
    """
    path = Path(path)
    if (not earlier or earlier.get("numbering") == NUMBERING
            or not earlier.get("frames") or not path.exists()):
        return
    legacy = path.with_name(path.name + ".legacy")
    if not legacy.exists():
        shutil.copyfile(path, legacy)


class RollManifest:
    """One roll's manifest, and the only thing that writes it.

    Two threads have something to say about a frame. The scanning thread
    records it as it is taken; the writer thread says, some seconds later,
    whether it was filed. `done` used to be decided on the first and written
    at once, while the frame was still only queued -- so a frame the writer
    then failed to file, or that a crash took with it, was recorded as done,
    and a resume skipped a frame that existed nowhere.

    So a frame waiting to be filed is recorded with ``done`` false, and
    `filed` makes it true, with the library entry it went to. The writer can
    get there first -- a small frame on an idle disk -- and then the answer
    waits for the record rather than being lost. One lock, because both
    threads write the one file.
    """

    def __init__(self, path, data: dict):
        self.path = Path(path)
        self.data = data
        self._lock = threading.Lock()
        self._awaiting: set[int] = set()
        self._early: dict[int, tuple] = {}
        self._written = False

    def _write(self) -> None:
        # The first write of a run keeps what was there before it.
        write_manifest(self.path, self.data, keep_previous=not self._written)
        self._written = True

    def write(self) -> None:
        with self._lock:
            self._write()

    def record(self, record: dict, awaiting: bool = False) -> None:
        """This frame's record, replacing any earlier one of its number.

        ``awaiting`` says it was handed to the writer and is not done until
        `filed` says so.
        """
        number = int(record["number"])
        with self._lock:
            early = self._early.pop(number, None)
            if early is not None:
                self._apply(record, *early)
            elif awaiting:
                record["done"] = False
                self._awaiting.add(number)
            self.data["frames"] = [
                f for f in self.data.get("frames") or ()
                if str(f.get("number")) != str(number)] + [record]
            self._write()

    def filed(self, number: int, entry, error: str | None,
              **extra: Any) -> None:
        """The writer's answer for one frame: its entry, or why there is none.

        ``extra`` is written into the record only when it was filed -- the
        roll tool's `file`, which named a TIFF before anything had written it.
        """
        number = int(number)
        with self._lock:
            if number not in self._awaiting:
                self._early[number] = (entry, error, extra)
                return
            self._awaiting.discard(number)
            for record in self.data.get("frames") or ():
                if str(record.get("number")) == str(number):
                    self._apply(record, entry, error, extra)
            self._write()

    @staticmethod
    def _apply(record: dict, entry, error, extra=None) -> None:
        if error is None:
            record["done"] = True
            record["entry"] = str(entry) if entry else None
            record.update(extra or {})
        else:
            record["done"] = False
            record["filing_error"] = str(error)


def walk_span(earlier: dict, start_at: int,
              frames: int | None) -> tuple[int, int | None]:
    """The range two walks of one strip cover together, as ``(start, count)``.

    ``earlier`` is the `survey.json` a walk adds to, already on the strip's
    numbering; its range is what its settings say, or failing that the frames
    it lists. A count of None is "to the end of the strip", and either walk
    having gone there makes the pair go there too.
    """
    settings = earlier.get("settings") or {}
    numbers = []
    for record in earlier.get("frames") or ():
        try:
            numbers.append(int(record["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    before = settings.get("start_at", earlier.get("start_at"))
    try:
        first = int(before) if before is not None else min(numbers)
    except (TypeError, ValueError):      # no start recorded and no frames
        return start_at, frames
    if "frames" in settings:
        try:
            last = (None if settings["frames"] is None
                    else first + int(settings["frames"]) - 1)
        except (TypeError, ValueError):
            last = max(numbers, default=first)
    else:
        last = max(numbers, default=first)
    begin = min(first, start_at)
    if last is None or frames is None:
        return begin, None
    return begin, max(last, start_at + frames - 1) - begin + 1


#: How many sub-frame commands one move may use. The law itself holds over
#: twenty steps and goes sub-linear past them, but the guard sits lower: a
#: sub-frame move asked to travel further than this is a whole-frame job, and
#: SLIDE_NEXT/SLIDE_PREV do that properly.
MAX_FINE_STEPS = 8


def plan_nudges(millimetres: float) -> list[float]:
    """The sub-frame moves a request actually becomes, in order and signed.

    The transport cannot travel an arbitrary distance. One `SLIDE` command
    delivers ``STEP_MM x param + OVERHEAD_MM`` for an integer param in 1..8,
    so the reachable set is a lattice starting at ``FINE_MIN_MM`` -- and
    **nothing in ``(0, FINE_MIN_MM)`` exists at all**. Asking for 0.1 mm does
    not get you 0.1 mm; it gets you nothing or a whole first command.

    This exists so the three places that need that truth share it rather than
    each modelling it: :meth:`ScanSession._move` executes the plan, the
    contact sheet's adjuster snaps what it shows to it, and a correction loop
    sizes its travel budget from it. A plan that predicts something the mover
    would not do is worse than no plan.

    Returns an empty list when the distance is below half the smallest
    deliverable move -- the same "leave it alone" the mover applies. Raises
    ``ValueError`` past :data:`MAX_FINE_STEPS`, deliberately rather than
    clamping: `DirectScanner.param_for_mm` already clamps silently at param 8,
    and a caller that cannot see its request was truncated will keep issuing
    commands against a ceiling it does not know is there.
    """
    want = abs(float(millimetres))
    if want < FINE_MIN_MM * 0.5:
        return []
    steps = max(1, -(-int(want * 1000) // int(FINE_MAX_MM * 1000)))
    if steps > MAX_FINE_STEPS:
        raise ValueError(
            f"{say_units(want, signed=False)} needs {steps} sub-frame "
            "moves; past "
            f"{MAX_FINE_STEPS} the calibration goes sub-linear and the "
            "distance would not be what was asked for"
        )

    sign = 1.0 if millimetres > 0 else -1.0
    out: list[float] = []
    moved = 0.0
    while want - moved >= FINE_MIN_MM * 0.5 and len(out) < steps:
        # Snapped through the scanner's own param arithmetic, not a copy of
        # it, so the plan cannot drift from what nudge() will really send.
        asked = min(FINE_MAX_MM, want - moved)
        param = DirectScanner.param_for_mm(asked)
        landed = DirectScanner.STEP_MM * param + DirectScanner.OVERHEAD_MM
        out.append(sign * landed)
        moved += landed
    return out


def deliverable_mm(millimetres: float) -> float:
    """What the transport will actually travel for this request.

    Signed, and never more precise than the hardware: a number finer than
    :data:`FINE_MIN_MM` is a lie, and showing one to an operator invites them
    to aim at a position that does not exist.
    """
    return float(sum(plan_nudges(millimetres)))

#: Where prescans go inside the operator's output folder. They are framing
#: passes, not photographs, and mixing them in with the scans buries them.
PRESCAN_SUBDIR = "prescans"

#: Lines per inch of transport travel, for turning dpi into a line count.
_LINES_PER_DPI = 6888 / 7200

#: How many results keep a full working copy in memory. Beyond this only the
#: filmstrip thumbnail is kept and the preview is re-read from the library
#: entry on demand: at 3600 dpi a working copy is ~8 MB and a roll is 36 of
#: them, which is not a thing to hold for the whole session.
WORKING_COPIES = 12


def estimate_seconds(resolution: int, infrared: bool,
                     fast_infrared: bool = True) -> float:
    """Roughly how long one pass will take, for a progress readout.

    Scan time barely depends on resolution for a plain RGB pass -- the carriage
    traverse dominates -- and the figure is the measured fit `8 + 0.036 x
    lines`.

    **Infrared has two costs now, and they are nothing like each other.** With
    the plane tied to the resolution asked for, which is the default, the pass
    costs `7.5 + 0.0599 x lines`: measured across five resolutions on one slide
    2026-09-16, fitting every point to within 0.16 s and predicting a pass it
    was not fitted to within 0.24 s. Untied, it is the old fixed floor --
    anchored on 227 s at 900 and 1800 dpi and 334 s at 3600 -- which the line
    count does not move until about 3700 dpi.

    **Untied is a floor, not a curve**: ~220 s until the line count overtakes
    it, and the line count after that. The older form anchored 334 s at
    3600 dpi and grew with resolution throughout; the sweep measured 221 s
    there, and flat to 1.1 s from 300 to 1800 dpi. Same shape as the tied
    branch with a floor under it, which is also the physically sensible one --
    a pass cannot cost less than the work it does.

    An estimate, and labelled as one wherever it is shown. Note that exposure
    moves all of these: the same 1800 dpi pass took 250.5 s on colour negative
    and 220.3 s on slide.
    """
    lines = max(1.0, resolution * _LINES_PER_DPI)
    rgb = 8.0 + 0.036 * lines
    if not infrared:
        return rgb
    tied = 7.5 + 0.0599 * lines
    if fast_infrared:
        return tied
    return max(INFRARED_UNTIED_S, tied)


# ---------------------------------------------------------------------------
# Jobs -- what the UI can ask for
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Approved:
    """Where the operator decided one frame should sit, and the picture he
    decided it from.

    `offset_mm` is a signed displacement **relative to where the film sat when
    that frame was surveyed**, in the same sense `nudge` and `Move.millimetres`
    use. It cannot be an absolute transport coordinate: the frame counter does
    not see a sub-frame move, so there is no such coordinate to name. Zero is a
    real answer -- "leave it exactly where I saw it" -- and is not the same as
    having no approval at all.

    `reference` is the prescan he actually looked at, carried by value. It is
    368 KB at 300 dpi and already in memory, and it is byte-identical to the
    pixels on his screen -- which is what "check it against the frame I
    approved" has to mean. `reference_entry` is the library path to the same
    pass, for the case where the array did not survive.

    `rotation` and `flipped` are how that picture is arranged -- degrees
    clockwise, and whether it reads left to right -- as it was set in the
    contact sheet. Per frame rather than per session because a strip is not one
    orientation: a portrait among landscapes is ordinary, and one answer for the
    roll can only get one of them right. A frame without one falls back to
    :attr:`ScanSession.rotation` and :attr:`ScanSession.flip`. They reach the
    delivered files only; the library entry keeps the scanner's own
    orientation, for the reason :class:`FrameWriter` gives.
    """

    number: int                              # its frame on the strip, from 1
    offset_mm: float = 0.0
    reference: Any = field(default=None, compare=False, repr=False)
    reference_entry: str = ""
    rotation: int = 0
    flipped: bool = False
    #: Who decided the number: ``operator`` when he set it himself, or the
    #: ensemble's own word for how it read the frame -- ``measured``,
    #: ``unconfirmed``, ``neighbours``, ``none``, the vocabulary
    #: `propose_offsets` uses and `tests/test_ensemble.py` pins. The sheet
    #: pre-fills a position for every frame it can read, so "he approved this"
    #: stopped being true of most of them, and a driver logging `operator`
    #: about a detector's number is claiming he asked for something he did not.
    #: Appended and defaulted: `tests/test_demo.py` builds these positionally.
    source: str = "operator"


@dataclass(frozen=True)
class Calibrate:
    """Acquire this session's shading reference. Once per power-on."""

    mode: str = "measure"                    # measure | reuse | off
    reference: str = "calibration/shading.npz"


@dataclass(frozen=True)
class Prescan:
    """A 300 dpi RGB framing pass over the whole transport, ~16 s."""

    resolution: int = 300
    #: What is in the transport. A framing pass does not expose for it -- it
    #: runs at the device's own settings -- but the entry it files should say
    #: what it was looking at, and the demo picks its picture by it.
    film: str = "negative"
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scan:
    """One frame at full quality."""

    resolution: int = 1800
    infrared: bool = True
    #: Tie the infrared plane's cost to the resolution asked for. On by
    #: default, and worth a great deal: an untied infrared pass costs ~220 s
    #: whatever the resolution, a tied one costs what its lines cost -- 110 s
    #: at 1800 dpi, 25 s at 300, and no different above ~3700 dpi where the
    #: line count has already overtaken the floor. See `rps7200/direct.py`.
    fast_infrared: bool = True
    film: str = "negative"
    auto_exposure: bool = True
    exposure_scale: Any = 1.0
    shading: bool = True
    #: Deliver one channel instead of three. None follows the film:
    #: on for black and white, off otherwise. See rps7200/mono.py.
    mono: bool | None = None
    #: Which channel that is. Green by measurement; see rps7200/mono.py.
    mono_channel: str = MONO_CHANNEL
    frame: tuple[int, int, int, int] | None = None
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Roll:
    """Walk a strip or roll, one picture at a time."""

    frames: int | None = None
    #: The frame of the strip to start at: frame N is where the transport's
    #: counter reads N-1, and it reads 0 when a strip goes in. The roll winds
    #: or advances the film there before anything else -- see :func:`seek` --
    #: and numbers every frame by its place on the strip from then on.
    start_at: int = 1
    resolution: int = 1800
    infrared: bool = True
    #: As on :class:`Scan`, and worth most here: it is the difference between
    #: a 38-frame infrared roll spending 2.3 hours on the floor and 1.2.
    fast_infrared: bool = True
    film: str = "negative"
    meter: str = METER_EACH
    #: As on :class:`Scan`.
    mono: bool | None = None
    mono_channel: str = MONO_CHANNEL
    prescan_resolution: int = 300
    # There was a `rewind` here: frames to wind back first, which the sheet
    # computed from how far its walk had gone. It is gone because `start_at`
    # now names a place on the strip and the roll goes there itself, from
    # wherever the transport says the film is. Counting back from where the
    # walk *ended* scanned the wrong frames without a word whenever the film
    # had moved since, by the window's buttons or by the scanner's own keys.
    # The reason it was part of the roll rather than a `Move` still holds for
    # the seek: a refusal has to stop the roll, and one job cannot cancel the
    # job behind it.
    dry_run: bool = False
    #: A dry run that adds to the `survey.json` already in its folder instead
    #: of replacing it: the same strip walked further, so a sheet of frames 1
    #: to 10 becomes one of 1 to 12. A frame walked again replaces its own
    #: record and prescan. Ignored on a real roll, whose `roll.json` is always
    #: carried forward.
    extend_walk: bool = False
    #: The frames worth scanning, numbered the same way as `start_at` -- by
    #: their place on the strip. Anything else is advanced past unprescanned
    #: and unscanned, and the roll ends after the last one. This is what a
    #: survey is for: walk the strip in four minutes, look at it, then spend
    #: the hours on the frames that earn them. None scans every frame.
    only: tuple[int, ...] | None = None
    correct: bool = False
    #: Judge every frame and log what would be commanded, without sending it.
    #: The walk costs what it costs today, no film moves, and the sheet says
    #: what aiming would have done -- which is the only way to see that before
    #: letting it happen. `tools/scan_roll.py` has had this since the
    #: correction did; the window could only do the real thing.
    correct_dry_run: bool = False
    #: Positions the operator set by hand in the contact sheet, one per frame
    #: he picked. These are authoritative: a frame carrying one is held to it
    #: and `correct` does not apply to that frame. Frames without one are
    #: unaffected, so a mixed roll is coherent and a roll without approvals
    #: behaves exactly as it did before this existed.
    approved: tuple[Approved, ...] = ()
    #: Whether the transport's +x is the operator's +x. Mirrors the window's
    #: "reverse the direction" tick, which exists because the physical sense
    #: was never certain.
    reverse_hold: bool = False
    max_failures: int = 3
    name: str = ""
    out: str = ""
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Move:
    """Move the film without scanning anything.

    Two different mechanisms, deliberately in one job so the UI cannot confuse
    them. `frames` steps whole pictures with SLIDE_NEXT/SLIDE_PREV, which the
    transport counts and `READ_STATE` confirms. `millimetres` is a sub-frame
    nudge, which the frame counter does **not** see -- only a prescan can tell
    you it landed, and two to three steps are swallowed after a direction
    change, so a small move that reverses may not move the film at all.
    """

    frames: int = 0                          # +1 next picture, -1 previous
    millimetres: float = 0.0                 # + towards the end of the film


Job = Calibrate | Prescan | Scan | Roll | Move


# ---------------------------------------------------------------------------
# Events -- what comes back
# ---------------------------------------------------------------------------


@dataclass
class Result:
    """One pass that produced pixels, as the UI wants to show it."""

    seq: int                                 # ties this result to its entry
    kind: str                                # prescan | scan | frame
    label: str
    image: np.ndarray | None                 # working copy, decimated
    meta: dict[str, Any]
    entry: Path | None = None
    registration: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: Where the transport was when this pass was taken. A scan can only stand
    #: in for a prescan of the same picture, and this is how that is known.
    position: int | None = None
    #: Which frame of the strip this is, counting from 1 -- its place on the
    #: strip, the same number whichever roll or walk took it -- or 0 for a pass
    #: that belongs to no roll. It is what a contact sheet ticks and what
    #: `Roll.only` is then given, so it must not be recovered from `label`.
    number: int = 0


@dataclass(frozen=True)
class Event:
    kind: str                                # see KINDS
    text: str = ""
    done: int = 0
    total: int = 0
    result: Result | None = None
    busy: bool = False

#: "filed" carries the sequence number in `done` and the entry path in `text`,
#: which is how a result learns where its full-resolution pixels ended up.
#: "transport" carries the transport's own counter in `done` -- 0-based, so
#: frame N of the strip is N-1 -- or -1 when it is unknown or not plausible.
#: It is sent when the session opens, after every job that finished, and as a
#: roll moves, so the window's readout follows the film rather than only the
#: moves its own buttons made.
#: "calibrated" is sent when a `Calibrate` job ends, with 1 in `done` when the
#: session now holds a shading reference and 0 when it does not -- a
#: measurement can come back with nothing usable, and one that fails leaves
#: whatever the session held before.
KINDS = ("state", "log", "progress", "result", "filed", "transport",
         "calibrated", "finished", "failed", "closed")


# ---------------------------------------------------------------------------
# The writer thread
# ---------------------------------------------------------------------------


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

    def __init__(self, depth: int = 2, on_done: Any = None):
        self.queue: queue.Queue = queue.Queue(maxsize=depth)
        self.errors: list[str] = []
        # Not failures: things the chosen format could not carry, like the
        # infrared plane in a JPEG. Drained per frame by `_filed` so they are
        # read while the roll is running, not at the end of it.
        self.notes: list[str] = []
        self.done: list[tuple[int, Path | None]] = []
        # Called with (number, entry_path, error) as each job lands, so a UI can
        # show where a frame went without polling `done`.
        self.on_done = on_done
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
                if self.on_done is not None:
                    self.on_done(job.get("seq", 0), job["number"], None, str(exc))
                self._tell(job, None, str(exc), [])
            finally:
                self.queue.task_done()

    def _tell(self, job: dict, entry, error: str | None, written: list) -> None:
        """The job's own answer, for whoever records it: see `RollManifest`.

        Per job rather than on the writer, because the roll that queued a
        frame is the one whose manifest says whether it is done. Never lets a
        failure here cost the writer the frames behind it.
        """
        then = job.get("on_filed")
        if then is None:
            return
        try:
            then(entry, error, written)
        except Exception as exc:                         # noqa: BLE001
            self.errors.append(f"picture {job['number']}: filed, but its "
                               f"manifest could not say so ({exc})")

    def _write(self, job: dict) -> None:
        # The delivered files carry the orientation that was asked for and the
        # shading correction; the library entry below carries neither. Its
        # pixels have to stay exactly what the scanner sent, or they stop
        # matching the raw bytes beside them and `library.reconstruct` is right
        # to call it a changed decode. `raw_image` is that; `image` is what the
        # operator asked to be given.
        turned = preview.orient(job["image"], job.get("rotate") or 0,
                                bool(job.get("flip")))
        # One channel on the way out, three in the library. A consumer cannot
        # tell black and white from a slide by looking at the pixels -- see
        # rps7200/mono.py -- so the file it reads has to say so by its shape.
        delivered = (to_monochrome(turned, job.get("mono_channel") or MONO_CHANNEL)
                     if job.get("mono") else turned)
        # The library entry first, the operator's copies after. A copy can be
        # written again from the entry at any time; the entry cannot be written
        # again from anything, because it holds the only raw bytes. Written the
        # other way round, an unplugged drive or a full output folder raised
        # before `library.save` ran, and the scan's raw bytes were lost with it.
        entry = None
        if job["library"]:
            # The raw pixels where the job carries them, the delivered
            # ones otherwise -- CLAUDE.md's rule that the library holds raw.
            # Bound once: `.get()` is Optional however many times it is called.
            raw_image = job.get("raw_image")
            corrections = None
            if raw_image is None and (job["meta"] or {}).get("shading"):
                # The pass was flat-fielded and no raw pixels came with it, so
                # what is about to be filed is corrected. Said so in the entry,
                # rather than filed as raw: an entry that claims raw pixels it
                # does not hold is corrected a second time by
                # `library.corrected`, and `reconstruct` calls it a changed
                # decode -- which is how every Scan from the window was filed
                # until the Scan job passed its raw pixels.
                corrections = ["shading"]
                self.notes.append(
                    f"picture {job['number']}: no raw pixels came with this "
                    "pass; filed its corrected pixels, labelled as corrected")
            entry = library.save(
                job["image"] if raw_image is None else raw_image,
                job["meta"],
                root=job["library"],
                film=job["film"],
                tags=job["tags"],
                prescan=job["prescan"],
                prescan_meta=job.get("prescan_meta"),
                inquiry=job["inquiry"],
                corrections=corrections,
                **job["capture"],
            )
        problems = []
        written = []
        for path in job.get("paths") or ():
            # Each copy on its own: one that cannot be written -- a missing
            # drive, a full disk -- says so and does not stop the others.
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                # The format comes from the name, so one call covers both: a
                # roll's own `rolls/...tif` and an output folder set to JPEG are
                # written correctly side by side without this having to know
                # the setting.
                note = export.write(str(path), delivered, resolution=job["dpi"],
                                    quality=job.get("quality")
                                    or export.DEFAULT_QUALITY)
            except Exception as exc:                     # noqa: BLE001
                problems.append(f"could not write {path} ({exc})")
                continue
            written.append(Path(path))
            if note:
                self.notes.append(f"{Path(path).name}: {note}")
        if problems and entry is None:
            # Nothing of this picture was kept anywhere: a failure, reported
            # as one by `_run`.
            raise OSError("; ".join(problems))
        for problem in problems:
            said = (f"picture {job['number']}: {problem}; the library entry is "
                    "safe and can be exported again")
            self.errors.append(said)
            self.notes.append(said)
        self.done.append((job["number"], entry))
        if self.on_done is not None:
            self.on_done(job.get("seq", 0), job["number"], entry, None)
        self._tell(job, entry, None, written)

    def submit(self, **job) -> None:
        self.queue.put(job)

    def finish(self) -> None:
        """Wait for every queued frame. Call after the session has closed."""
        self.queue.put(None)
        self._thread.join()


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


class _Stopped(Exception):
    """Raised on the worker when a cooperative stop was asked for."""


class ScanSession:
    """A scanner, a worker thread that owns it, and a queue of jobs.

    Nothing on this class touches the device except from the worker thread.
    `submit`, `request_stop`, `force_abort` and `poll` are the UI's side and are
    safe to call from a main loop.
    """

    def __init__(
        self,
        root: str | Path | None = "library",
        reference: str | Path = "calibration/shading.npz",
        rolls: str | Path = "rolls",
        open_scanner: Any = None,
        verbose: bool = True,
        out_dir: str | Path | None = None,
    ):
        self.root = str(root) if root else None
        self.reference = str(reference)
        self.rolls = Path(rolls)
        #: Makes the reader a roll's in-walk correction asks about each frame's
        #: edges: ``edge_reader(film)`` -> a fresh reader, or None. The window
        #: sets it to `tools/frame_edges.walk_reader`; left None, the driver's
        #: own strip-level detector decides, as it always has.
        self.edge_reader: Callable[[str], Any] | None = None
        #: Where the last roll or walk wrote its manifest. The folder is
        #: derived here, from the job's name and a date fallback, so a caller
        #: that wants to file something beside that manifest -- the contact
        #: sheet's decisions, which are made after the walk finishes -- can ask
        #: rather than recompute the same name and drift out of step with it.
        self.last_roll_dir: Path | None = None
        self.verbose = verbose
        # A second copy of each scan, written where the operator asked for it as
        # the scan lands rather than afterwards. The library entry is the record;
        # this is the file they actually wanted.
        self.out_dir = Path(out_dir) if out_dir else None
        #: A quarter turn applied to every file written from here on -- the
        #: output folder's copy and a roll's own TIFF. Never the library entry.
        #: Set from the UI, so a picture rotated on screen is rotated in the
        #: files that follow it.
        self.rotation = 0
        #: Whether those files also read left to right. A strip loaded the other
        #: way up comes off this scanner backwards, and turning it does not fix
        #: that -- so it is its own answer rather than a fourth angle.
        self.flip = False
        #: The same two, for one picture of a roll, keyed by frame number --
        #: from `Approved` for the frames that carry one. Filled at the top of a
        #: roll and emptied when it ends, so they only ever describe the roll in
        #: flight. Read and written on the scanner thread alone, so no lock.
        self._frame_rotation: dict[int, int] = {}
        self._frame_flip: dict[int, bool] = {}
        #: Whether a pass that comes back reversed against its own prescan is
        #: turned to match it. On, because the scanner does this with nothing
        #: to say it has and the alternative is a frame filed upside down; and
        #: switchable, because it is a detector and every detector written for
        #: this scanner has been confidently wrong on some frame.
        self.match_prescan = True
        #: The last framing pass and where the transport was for it, so a scan
        #: taken straight afterwards has something to be judged against.
        self._last_prescan: tuple[Any, int | None, dict] | None = None
        #: What the output folder's copy is written as -- "tiff" or "jpeg".
        #: Only that copy: a roll's own files under `rolls/` stay TIFF whatever
        #: this says, because they are machinery rather than deliverables and
        #: `prescanNN.tif` is what reopening a survey reads back.
        self.out_format = "tiff"
        #: Quality for the JPEG path, ignored by the TIFF one.
        self.jpeg_quality = export.DEFAULT_QUALITY
        # The seam that lets tests and `--demo` run with nothing on the bus.
        self._open_scanner = open_scanner or self._default_scanner
        self._jobs: queue.Queue = queue.Queue()
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scanner: Any = None
        self._writer: FrameWriter | None = None
        self._seq = 0
        self.dead = False                    # set by force_abort
        self.inquiry_text = ""
        #: Whether a `Calibrate` job has left this session a shading reference.
        #: Written on the worker, read by the window through the "calibrated"
        #: event rather than directly, so it never reads one job behind.
        self.calibrated = False

    # -- the UI's side -----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("session already started")
        self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")
        self._thread.start()

    def submit(self, job: Job) -> None:
        # A stop asked for during the previous job must not silently kill the
        # next one the operator deliberately started.
        self._stop.clear()
        self._jobs.put(job)

    def request_stop(self) -> None:
        """Stop at the next safe point. Never risks the device."""
        self._stop.set()

    def force_abort(self) -> None:
        """Abandon whatever is running by closing the transport under it.

        The frame is lost and the scanner will very probably need a power cycle.
        Confirm with the operator before calling this; there is no undo, and
        closing a libusb handle with a transfer in flight is undefined enough
        that it can take this process with it.
        """
        self.dead = True
        self._stop.set()
        scanner = self._scanner
        transport = getattr(scanner, "t", None) if scanner is not None else None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:                     # noqa: BLE001
                self._emit("log", text=f"force abort: {exc}")
        self._emit(
            "state",
            text="aborted -- power-cycle the scanner at its own switch",
        )

    def shutdown(self) -> None:
        """Ask the worker to close the device and stop."""
        self._jobs.put(None)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def poll(self) -> list[Event]:
        """Every event waiting, oldest first. Called from the UI's timer."""
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    # -- the worker's side -------------------------------------------------

    def _default_scanner(self) -> DirectScanner:
        return DirectScanner(
            verbose=self.verbose,
            # RPS7200_DEBUG decides, as it does everywhere else. The passes this
            # session files itself are claimed in `_file` (`debug_claim`), so
            # debug filing does not write them twice -- which at 7200 dpi was
            # 43 GB of duplicate on a roll, and why this used to say
            # debug=False. That also switched off the one record of the passes
            # nothing here files: metering probes, hold and aim prescans.
            debug=None,
        )

    def _listen(self, scanner: Any) -> None:
        """Attach this session's hooks to whatever the factory handed back.

        Here rather than in `_default_scanner`, so an injected scanner -- the
        demo backend, a test double -- reports just as much as the real one.
        """
        if hasattr(scanner, "log_hook"):
            scanner.log_hook = lambda m: self._emit("log", text=m)
        if hasattr(scanner, "progress_hook"):
            scanner.progress_hook = lambda done, total: self._emit(
                "progress", done=done, total=total
            )

    def _emit(self, kind: str, **kw: Any) -> None:
        self._events.put(Event(kind=kind, **kw))

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Stopped()

    def _run(self) -> None:
        try:
            self._scanner = self._open_scanner()
            self._listen(self._scanner)
            self._scanner.open()
            info = self._scanner.inquiry()
            self.inquiry_text = info.describe() if hasattr(info, "describe") else str(info)
            self._emit("state", text=self.inquiry_text)
        except Exception as exc:                         # noqa: BLE001
            self._emit("failed", text=f"could not open the scanner: {exc}")
            self._emit("closed")
            return
        # Where the film is before anything has moved it. The window used to
        # learn the position only from its own frame buttons, so at launch --
        # and after anyone used the scanner's keys -- it had nothing to say
        # about where a roll would start from.
        self._report_position()

        self._writer = FrameWriter(on_done=self._filed)
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                self._stop.clear()
                self._emit("state", text=_describe(job), busy=True)
                try:
                    note = self._dispatch(job)
                    # A move reports every frame it makes; anything else says
                    # once where it left the film. Only after a job that ended
                    # normally: after a failure the device is left alone.
                    if not isinstance(job, Move):
                        self._report_position()
                    self._emit("finished", text=note or _describe(job))
                except _Stopped:
                    self._emit("finished", text="stopped")
                except Exception as exc:                 # noqa: BLE001
                    self._emit("failed", text=f"{type(exc).__name__}: {exc}")
                self._emit("state", text="idle", busy=False)
        finally:
            # Order matters: the device closes first, and only then does the
            # writer get to spend time gzipping. The other way round is the
            # open-and-idle state that preceded a wedge.
            try:
                if not self.dead:
                    self._scanner.close()
            except Exception as exc:                     # noqa: BLE001
                self._emit("log", text=f"close: {exc}")
            if self._writer is not None:
                self._emit("log", text="filing what is still queued ...")
                self._writer.finish()
                for problem in self._writer.errors:
                    self._emit("log", text=problem)
            self._emit("closed")

    def _filed(self, seq: int, number: int, entry: Path | None, err: str | None) -> None:
        """Called on the writer thread as each frame lands."""
        if self._writer is not None:
            while self._writer.notes:
                self._emit("log", text=self._writer.notes.pop(0))
        if entry is None:
            self._emit("log", text=f"picture {number} could not be filed: {err}")
            return
        self._emit("log", text=f"filed: {entry.name}")
        # The UI needs the path to read full-resolution pixels back for a 1:1
        # look; the working copy it already has is decimated and cannot show
        # grain or shadow noise.
        self._emit("filed", done=seq, text=str(entry))

    def _dispatch(self, job: Job) -> str | None:
        """Run one job. Returns a note when the outcome needs explaining."""
        self._check_stop()
        if isinstance(job, Calibrate):
            self._calibrate(job)
        elif isinstance(job, Prescan):
            self._prescan(job)
        elif isinstance(job, Scan):
            return self._scan(job)
        elif isinstance(job, Roll):
            return self._roll(job)
        elif isinstance(job, Move):
            return self._move(job)
        else:
            raise TypeError(f"unknown job {job!r}")
        return None

    # -- jobs --------------------------------------------------------------

    def _calibrate(self, job: Calibrate) -> None:
        try:
            summary = self._scanner.ensure_shading(
                Path(job.reference),
                reuse=job.mode == "reuse",
                skip=job.mode == "off",
            )
        except Exception:
            # A calibration that failed part way leaves the reference the
            # session already had, if it had one.
            self._emit("calibrated", done=int(self.calibrated))
            raise
        self._emit("log", text=summary["summary"])
        # Skipped is raw pixels by request, and a measurement can end with no
        # usable reference; neither is a calibration. `get`, because a stand-in
        # need not hand back the reference object itself.
        self.calibrated = (summary.get("action") in ("loaded", "calibrated")
                           and summary.get("reference", True) is not None)
        self._emit("calibrated", done=int(self.calibrated))

    def _prescan(self, job: Prescan) -> None:
        # The scanner's own meta, not a hand-built one. Substituting a short
        # dict here is what filed every prescan claiming to be uncorrected: it
        # dropped `shading`, and with it `protocol_revision` and the exposure,
        # so 26 entries described themselves wrongly and `reconstruct` called
        # every one of them a changed decode.
        image, _ = self._scanner.prescan(
            resolution=job.resolution, film=job.film, keep_raw=True)
        # `getattr`, like `_inquiry` below: a stand-in scanner need not carry
        # every attribute the real one publishes, and a prescan that fails to
        # file is worse than one filed with a thinner meta.
        meta = dict(getattr(self._scanner, "last_scan_meta", None) or {
            "resolution_dpi": job.resolution, "film": job.film,
            "channel_order": ["R", "G", "B"]})
        raw_image = getattr(self._scanner, "last_pixels_raw", None)
        label = f"prescan {job.resolution} dpi"
        # Kept so the scan taken next has something to be judged against. One
        # pass, ~370 KB at 300 dpi, replaced each time -- not a history.
        self._last_prescan = (image, self._position(), meta)
        seq = self._deliver(
            "prescan", label, image, {
                "resolution_dpi": job.resolution,
                # So the window can say a pass was read bottom-up and turned.
                "read_direction": meta.get("read_direction")}
        )
        # Filed like everything else. A prescan is ~370 KB and it is the
        # evidence about framing that went missing the last time it was not
        # kept; CLAUDE.md's rule is "file every scan", without an exception.
        self._file(
            seq=seq,
            number=0,
            kind="prescan",
            image=image,
            raw_image=raw_image,
            meta=meta,
            notes=job.notes,
            tags=tuple(job.tags) + ("gui", "prescan"),
        )

    def _scan(self, job: Scan) -> str | None:
        image, meta = self._scanner.scan(
            resolution=job.resolution,
            infrared=job.infrared,
            fast_infrared=job.fast_infrared,
            film=job.film,
            auto_exposure=job.auto_exposure,
            exposure_scale=job.exposure_scale,
            shading=job.shading,
            frame=job.frame,
            keep_raw=True,
        )
        # `scan()` hands back the corrected pixels and keeps the ones the
        # scanner sent in `last_pixels_raw`. Read now, before anything else
        # runs a pass: it describes the pass that just ran. Without it this job
        # filed the corrected pixels as the entry's raw `scan.tif`, and every
        # view and export of it was then corrected a second time.
        raw_image = getattr(self._scanner, "last_pixels_raw", None)
        label = f"{job.resolution} dpi {'RGBI' if job.infrared else 'RGB'}"
        here = self._prescan_here()
        meta = self._note_reversal(meta, image, *(here or (None, None)))
        seq = self._deliver("scan", label, image, meta)
        self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",),
                   raw_image=raw_image,
                   mono=wants_mono(job.mono, job.film),
                   mono_channel=job.mono_channel)
        # A pass that wanted correction and could not get one no longer comes
        # back here at all -- scan() raises ShadingUnavailable instead, which
        # this job's dispatcher already turns into a surfaced error. There is
        # nothing left worth flagging on a normal return: the only remaining
        # reason meta["shading_skipped"] is set is job.shading=False, and that
        # is the job's own choice, not a shortfall.
        return None

    def _prescan_here(self):
        """The last framing pass, if it was of the picture now in the gate.

        A prescan of a different frame is a different photograph, and judging
        a scan against one would be worse than not judging it at all. The
        transport position is the same test `_add_result` uses to decide that
        a scan stands in for a prescan, so the two cannot disagree about which
        pictures are the same one.
        """
        if not self._last_prescan:
            return None
        image, where, meta = self._last_prescan
        return (image, meta) if where == self._position() else None

    def _note_reversal(self, meta, image, reference, reference_meta=None,
                       prescan_reversed=False):
        """Record any half turn this pass needs to read like its prescan.

        Written into the meta rather than applied to the pixels here, which is
        bookkeeping and not restraint: **every file that leaves is turned by
        it**, TIFF and JPEG alike, the output folder's copy and a roll's own
        `frameNN.tif`. What the meta buys is that the library entry can go on
        holding exactly what the scanner sent, with its record saying how the
        delivered file was arranged -- the same split a rotation already has.
        `_file` and the window both read it from here, so the picture on
        screen and the file on disk cannot end up disagreeing about which way
        up a photograph is.

        **A pass whose own lines said which way it was read is never turned
        here.** The decode has already put it upright (`rps7200.direction`),
        and so has its prescan's, so a picture that still disagrees with its
        prescan is not the carriage -- and `reversal_against` cannot tell
        which of the two is the odd one out. It blamed the scan on all five
        reversed prescans of one roll, at margins of 0.32 to 1.21 against a
        threshold of 0.25; acted on, five correct frames would have shipped
        upside down. Such a disagreement is said, and the scan is left.

        Only a pass whose direction is unknown is still judged by picture, as
        before -- and ``prescan_reversed``, the hold loop's word that the
        prescan was the one that disagreed, still spares it.
        """
        if not self.match_prescan or reference is None:
            return meta
        if _read_known(meta):
            extra, detail = reversal_against(reference, image)
            if extra != (0, False):
                self._emit("log", text=(
                    f"this pass reads {extra[0]}\u00b0"
                    f"{' mirrored' if extra[1] else ''} against its own prescan "
                    f"(by {detail['margin']:+.2f}), but both were read upright "
                    "from their own lines, so it is not the carriage; the scan "
                    "is left as it came -- check this frame"))
            return meta
        if prescan_reversed:
            self._emit("log", text=(
                "this frame's prescan came back with its rows reversed, so it "
                "is not evidence about which way up the scan is; the scan is "
                "left exactly as it came"))
            return meta
        extra, detail = reversal_against(reference, image)
        if extra == (0, False):
            return meta
        self._emit("log", text=(
            f"this pass came back {extra[0]}\u00b0"
            f"{' mirrored' if extra[1] else ''} against its own prescan "
            f"(by {detail['margin']:+.2f}); which way it was read is unknown, "
            "so it is turned to match"))
        return dict(meta, reversal=[extra[0], bool(extra[1])])

    def _move(self, job: Move) -> str | None:
        """Whole frames, or a sub-frame nudge. Never both in one job."""
        if job.frames:
            step = self._scanner.advance if job.frames > 0 else self._scanner.retreat
            landed = None
            for _ in range(abs(job.frames)):
                landed = step()
                if landed is None:
                    break
                # Through the filter `_report_position` uses: a counter no
                # strip can have is unknown, not "film on frame 73", and the
                # Roll dialog would forecast a wind from it.
                self._emit("transport", done=landed if plausible(landed)
                           else -1)
            if landed is None:
                return "the film did not move -- it may be at the end of the strip"
            if not plausible(landed):
                # Beside a readout of "film on frame ?", which is what the
                # emit above made of it, "on frame 74 of the strip" named a
                # place nobody could find.
                return (f"the film moved, and the transport's counter reads "
                        f"{landed}, which no strip has -- which frame it is "
                        "on is not known")
            return f"on frame {_frame(landed)} of the strip"

        if job.millimetres:
            # One SLIDE command tops out at ~1.01 mm, so anything further is
            # several of them. Doing that here rather than making the caller
            # loop is what stops a request for 7 mm quietly becoming a single
            # 1.01 mm move -- which is exactly what it used to do, so every
            # click on the prescan moved the film the same distance.
            #
            # The planning itself lives in `plan_nudges` so the adjuster and
            # any correction loop can ask what a distance becomes without
            # driving the transport to find out.
            try:
                plan = plan_nudges(job.millimetres)
            except ValueError as exc:
                return f"{exc} -- use the slide buttons for anything this far"
            sign = 1.0 if job.millimetres > 0 else -1.0
            moved = 0.0
            done = 0
            for step in plan:
                if self._stop.is_set():
                    break
                # `moved` accumulates what the scanner says it did, not what
                # the plan predicted: the plan is a forecast and this line is
                # the report.
                out = self._scanner.nudge(step)
                moved += abs(out.get("asked_mm", 0.0))
                done += 1
            # The frame counter does not see a sub-frame move, so the position
            # is reported as whatever it still says rather than pretending it
            # changed. Only a prescan can confirm a nudge landed.
            #
            # And nothing at all when it says nothing. The READ_STATE straight
            # after a whole-frame SLIDE comes back empty every time
            # (`DirectScanner._whole_frames`); after a sub-frame one that is
            # inferred, not measured, and this read follows the last SLIDE at
            # once. Reported as unknown, it made every nudge forget which
            # frame the film was on, and the Roll dialog lost a forecast the
            # nudge cannot have changed. A counter that answers with a number
            # no strip has is still unknown, as everywhere else.
            position = self._scanner.position()
            if position is not None:
                self._emit("transport",
                           done=position if plausible(position) else -1)
            how = f" in {done} moves" if done > 1 else ""
            return (f"moved {say_units(sign * moved)}{how} -- the frame counter "
                    "does not see this; prescan to check it landed")
        return "nothing to move"

    def _roll(self, job: Roll) -> str | None:
        # Before the directory, before the manifest: a roll that cannot put the
        # film on its first frame must leave nothing behind and scan nothing.
        # Refusing raises, so the window reports it as the failure it is rather
        # than as a job that finished.
        #
        # This is where a roll used to begin wherever the film happened to be,
        # and call that frame 1: with the film on frame 11, a roll of frames 1
        # to 15 scanned frame 11 first and filed it as frame 1. The numbers are
        # places on the strip now, and the film goes to the first one before
        # anything else happens.
        first = max(0, job.start_at - 1)
        try:
            seek(self._scanner, first, say=lambda m: self._emit("log", text=m))
        except FilmNotPlaced:
            # Where it stopped, so the readout does not go on showing where it
            # started. Only for a refusal: after a transport fault the device
            # is left alone, as it is after every other failed job.
            self._report_position()
            raise
        # Read and checked by the seek, so the readout has it without another
        # question to the device.
        self._emit("transport", done=first)
        if self._stop.is_set():
            return (f"stopped with the film on frame {first + 1}, before "
                    "anything was scanned")
        # The folder by `roll_dir`, the one derivation everything that files
        # beside a roll uses; the label the roll's entries carry is the name
        # its folder already records, so a roll added to keeps calling itself
        # what it did. `job.out` is a folder the caller already has -- the
        # sheet's own, a reopened roll's -- and a name given with it wins.
        if job.out:
            out = Path(job.out)
            name = job.name or recorded_roll_name(out) or out.name
        else:
            out = roll_dir(self.rolls, job.name)
            name = recorded_roll_name(out) or out.name
        out.mkdir(parents=True, exist_ok=True)
        self.last_roll_dir = out
        # Two products, two files. A walk and the scan of what it found go into
        # the same directory, and writing both into roll.json meant the record
        # of six frames walked was replaced by the record of the three that
        # were then scanned -- losing exactly what the walk was kept for.
        manifest_path = out / ("survey.json" if job.dry_run else "roll.json")
        # A resumed roll writes into the manifest its earlier attempt left, so
        # what is carried forward has to be read before anything is written.
        # Without this the second run *replaced* the first: four frames scanned
        # across two sessions came back as a two-frame roll, and the record of
        # the first two was simply gone -- in the one file whose job is to still
        # describe this roll a year from now.
        #
        # A walk that adds to the one before it (`extend_walk`) reads its
        # `survey.json` the same way and for the same reason: a second walk of
        # frames 11 to 12 used to replace the record of 1 to 10.
        earlier: dict[str, Any] = {}
        carried = not job.dry_run or job.extend_walk
        if carried and manifest_path.exists():
            # Only once the frames the last roll queued are filed: each says
            # it is done in this same file, as it lands, and read before that
            # this run would carry them forward as not done and write over the
            # writer's answer. Nothing is waited for when nothing is queued.
            if self._writer is not None and self._writer.queue.unfinished_tasks:
                self._emit("log", text="waiting for the last roll's frames to "
                           "be filed before adding to its manifest ...")
                self._writer.queue.join()
            # Not read as "nothing" when it cannot be read: the version kept
            # beside it is tried, and failing that the file is kept aside and
            # said. `json.loads` inside a bare except used to hand a damaged
            # roll.json back as empty, and the next write replaced it.
            earlier = earlier_manifest(
                manifest_path, say=lambda m: self._emit("log", text=m))
            # The numbering it was written with, kept once before it is
            # moved; see `keep_first_numbering`.
            keep_first_numbering(manifest_path, earlier)
            # Written before frame numbers were places on the strip, its
            # numbers are counted from wherever that roll started, and this
            # run's are not -- merged as they stand, frame 1 of the old record
            # and frame 6 of this one could be the same picture. Moved onto the
            # strip's numbering first, each frame by its own recorded position
            # -- a file resumed under 8a9ba17 holds one shift per run -- and by
            # its own run's shift where that position is no place on a strip,
            # or gives a number another frame's does and the run puts it
            # elsewhere, or, in a roll.json the tool wrote, which is one run,
            # another frame was moved onto it; a roll that recorded none is
            # numbered as the walk beside it was. What this reads is what gets
            # written back, under "strip", for good.
            earlier = renumbered(earlier, fallback=self._walk_shift(out),
                                 say=lambda m: self._emit("log", text=m))
        if job.dry_run:
            # A walk from before its records said how each prescan was turned
            # was turned by the file's one pair -- which this walk is about to
            # overwrite with its own. So its frames say it for themselves first.
            for record in earlier.get("frames") or ():
                if record.get("prescan"):
                    record.setdefault("prescan_rotation",
                                      int(earlier.get("rotation") or 0))
                    record.setdefault("prescan_flipped",
                                      bool(earlier.get("flipped")))
        # What the manifest says was walked or asked for: this run's range, or
        # on a walk that adds to an earlier one, the two together -- so a
        # reopened roll puts back the range the sheet really covers.
        start_at, count = job.start_at, job.frames
        if job.dry_run and earlier:
            start_at, count = walk_span(earlier, job.start_at, job.frames)
        manifest: dict[str, Any] = {
            "roll": name,
            # Says the numbers below are places on the strip, so a reader can
            # tell this file from one written before they were.
            "numbering": NUMBERING,
            "dpi": job.resolution,
            "infrared": job.infrared,
            "meter": job.meter,
            "film": job.film,
            "dry_run": job.dry_run,
            "start_at": start_at,
            # Both recorded so the survey can be opened again rather than
            # walked again. The prescan resolution because an approved
            # position is checked against a reference at that resolution and
            # a mismatch costs half the correlation confidence; the
            # orientation because prescanNN.tif is written arranged the way the
            # screen had it, and a reference has to be the film's own.
            "prescan_resolution": job.prescan_resolution,
            "rotation": self.rotation,
            "flipped": self.flip,
            "only": list(job.only) if job.only is not None else None,
            # Everything a `Roll` needs to be rebuilt from this file alone, so a
            # roll that died part-way can be finished later -- a year later, by
            # which time the window's own `gui-settings.json` has long moved on
            # to other film. That is the whole reason this is here and not left
            # to the window: this file describes *this roll*, and it is the only
            # thing that will still be true about it.
            #
            # `device` holds what the scanner was *asked* for rather than what
            # metering landed on, which is the same distinction
            # `library.signature` makes: a resumed roll should ask for the same
            # exposure rather than inherit whatever the window happens to hold,
            # and where metering was on it still runs again.
            #
            # **The shading reference is deliberately not in here**, and the
            # reason is not that it could not be: `load_shading` exists and
            # `--reuse` uses it, and every library entry keeps the reference it
            # would be corrected with. It is that a reference "describes the
            # sensor at the exposure and gain of the pass that measured it" --
            # so the one a roll started with is the wrong thing to hand a
            # resume months later, when the lamp and the metered exposure have
            # both moved. A resumed roll measures a new one, which is the
            # vendor's own once-per-power-on behaviour.
            #
            # The CCD mask is a different matter and needs no storing at all:
            # it is read fresh on every pass, because it says which CCD pixels
            # *that* resolution sampled.
            "settings": {
                "resolution": job.resolution,
                "infrared": job.infrared,
                "fast_infrared": job.fast_infrared,
                "film": job.film,
                "meter": job.meter,
                "mono": job.mono,
                "mono_channel": job.mono_channel,
                "prescan_resolution": job.prescan_resolution,
                "correct": job.correct,
                "correct_dry_run": job.correct_dry_run,
                "reverse_hold": job.reverse_hold,
                "max_failures": job.max_failures,
                "frames": count,
                "start_at": start_at,
                "only": list(job.only) if job.only is not None else None,
                "rotation": self.rotation,
                "flipped": self.flip,
            },
            #: Every frame this roll is meant to end up with, across however
            #: many sessions it takes. The union, because a resumed run is told
            #: only what is *left* -- taking its `only` as the answer is what
            #: made a four-frame roll report itself as two.
            "wanted": sorted(
                {int(n) for n in (earlier.get("wanted")
                                  or (earlier.get("settings") or {}).get("only")
                                  or earlier.get("only") or ())}
                | {int(n) for n in (job.only or ())}
            ) or None,
            # Earlier attempts' frames, kept. This run's records replace the
            # ones for the frames it scans and leave the rest alone.
            "frames": list(earlier.get("frames") or []),
        }
        # The one writer of this file from here on, from both threads; see
        # `RollManifest`.
        record_of = RollManifest(manifest_path, manifest)

        # How each chosen picture is arranged. Every approved frame appears,
        # zeros and falses included: the contact sheet knows each frame's
        # orientation outright, so an approval is the answer for that frame
        # rather than a deviation from the session's. Dropping the zeros meant
        # a frame deliberately straightened after a "rotate all" fell back to
        # the default that rotate-all had just moved, and was scanned sideways.
        # Frames with no approval at all -- a roll commissioned without a
        # sheet -- still fall through to `self.rotation` and `self.flip`.
        self._frame_rotation = {a.number: a.rotation for a in job.approved}
        self._frame_flip = {a.number: bool(a.flipped) for a in job.approved}

        frames = self._scanner.scan_roll(
            should_stop=self._stop.is_set,
            prescan_resolution=job.prescan_resolution,
            frames=job.frames,
            resolution=job.resolution,
            infrared=job.infrared,
            fast_infrared=job.fast_infrared,
            film=job.film,
            meter=job.meter,
            # The film is on this frame now, so the roll counts from it: a
            # frame's index is its transport position, and the number every
            # file and record carries is that plus one.
            first_index=first,
            # The window counts frames from 1 and the transport from 0.
            only=(None if job.only is None
                  else tuple(n - 1 for n in job.only)),
            max_failures=job.max_failures,
            dry_run=job.dry_run,
            correct=job.correct,
            correct_dry_run=job.correct_dry_run,
            # Keyed the driver's way, from 1-based as the window counts.
            approved={a.number - 1: a for a in job.approved},
            reverse_hold=job.reverse_hold,
            keep_raw=True,
            edge_reader=self.edge_reader,
        )
        stopped = None
        try:
            for rf in frames:
                number = rf.index + 1
                if rf.position is not None and plausible(rf.position):
                    # Already read for this frame, so the readout follows the
                    # roll without asking the device anything more.
                    self._emit("transport", done=rf.position)
                if rf.prescan is not None:
                    seq = self._deliver(
                        "prescan", f"frame {number} prescan", rf.prescan,
                        {"resolution_dpi": job.prescan_resolution,
                         "read_direction": (rf.prescan_meta or {})
                         .get("read_direction")},
                        registration=rf.registration, position=rf.position,
                        number=number,
                    )
                    if job.dry_run:
                        # On a dry run the prescans are the entire product --
                        # there is no frame entry to hang them off, so they are
                        # filed in their own right. On a real roll they ride
                        # along with the frame instead, which is why this is not
                        # unconditional: that would file every one of them twice.
                        #
                        # One of them also goes into the roll directory beside
                        # the manifest, the way `tools/scan_roll.py` writes it, so
                        # a survey can be opened again tomorrow instead of being
                        # walked again. Written by the writer thread, not here:
                        # it is only ~370 KB, but nothing local happens on the
                        # scanning thread with the device open.
                        surveyed = out / f"prescan{number:02d}.tif"
                        self._file(
                            seq, number, rf.prescan,
                            # The pass's own meta. A hand-built one here is
                            # what filed 26 prescans describing themselves as
                            # uncorrected raw when they were neither.
                            dict(rf.prescan_meta or {
                                "resolution_dpi": job.prescan_resolution,
                                "channel_order": ["R", "G", "B"]},
                                 roll_membership=roll_membership(
                                     name, number, "prescan", out)),
                            replace(job.notes, frame=roll_frame_label(name, number)),
                            tuple(job.tags) + ("gui", "roll", "prescan", name),
                            kind="prescan",
                            raw_image=rf.raw_prescan,
                            path=surveyed,
                            roll=name,
                        )
                        if rf.prescan_before is not None:
                            # The picture as the frame arrived, kept beside the
                            # one that replaced it. Without it a correction
                            # that moved a frame somewhere worse is
                            # indistinguishable from one that worked, and the
                            # only account of either would be the detector's
                            # own -- which is the thing under test.
                            self._file(
                                seq, number, rf.prescan_before,
                                dict(rf.prescan_meta or {
                                    "resolution_dpi": job.prescan_resolution,
                                    "channel_order": ["R", "G", "B"]}),
                                replace(job.notes,
                                        frame=roll_frame_label(name, number)),
                                tuple(job.tags) + ("gui", "roll", "prescan",
                                                   name),
                                kind="prescan",
                                path=out / f"prescan{number:02d}-before.tif",
                                roll=name,
                                file_entry=False,
                            )
                # The scan's own meta, for the manifest below. Bound out here
                # because `record` is written for a dry run too, where there is
                # no scan and no exposure to record.
                scanned: dict[str, Any] | None = None
                if rf.error:
                    self._emit("log", text=f"frame {number}: {rf.error}")
                elif rf.image is not None:
                    label = (
                        f"frame {number} · {job.resolution} dpi "
                        f"{'RGBI' if job.infrared else 'RGB'}"
                    )
                    # Judged against the framing pass taken of this very
                    # frame a minute earlier, which is the only evidence there
                    # is that the carriage reversed: see `_note_reversal`.
                    frame_meta = self._note_reversal(
                        rf.meta, rf.image, rf.prescan, rf.prescan_meta,
                        prescan_reversed=bool(
                            ((rf.registration or {}).get("approved") or {})
                            .get("row_reversed")))
                    scanned = frame_meta
                    seq = self._deliver(
                        "frame", label, rf.image, frame_meta,
                        registration=rf.registration, position=rf.position,
                        number=number,
                    )
                    # Always the roll's label: a value left in the Film
                    # panel's "frame" note used to replace it on every frame,
                    # which gave a whole roll one library signature and made
                    # its entries unfindable from the roll.
                    notes = replace(job.notes, frame=roll_frame_label(name, number))
                    self._file(
                        seq, number, rf.image,
                        dict(frame_meta, roll_membership=roll_membership(
                            name, number, "frame", out)),
                        notes,
                        tuple(job.tags) + ("gui", "roll", name),
                        raw_image=rf.raw_image,
                        prescan=rf.prescan,
                        prescan_meta=rf.prescan_meta,
                        path=out / f"frame{number:02d}.tif",
                        roll=name,
                        mono=wants_mono(job.mono, job.film),
                        mono_channel=job.mono_channel,
                        # Done when the writer says it was filed, and not
                        # before: see `RollManifest`.
                        on_filed=(lambda entry, error, _written, n=number:
                                  record_of.filed(n, entry, error)),
                    )

                record: dict[str, Any] = {
                    "number": number,
                    "index": rf.index,
                    "transport_position": rf.position,
                    "registration": rf.registration,
                    "error": rf.error,
                    # What a resume needs to know about this frame: whether it
                    # is finished. A frame that errored is *not* done and gets
                    # offered again -- that is the case a resume exists for.
                    # Nor is one merely scanned: it is done once the writer
                    # has filed it, which `record_of.filed` says.
                    "done": False,
                }
                if scanned is not None:
                    # Per frame rather than only in `settings`, because metering
                    # each frame is the default and then no single exposure
                    # describes the roll.
                    for key in ("exposure", "gain", "offset"):
                        if scanned.get(key) is not None:
                            record[key] = scanned[key]
                if job.dry_run and rf.prescan is not None:
                    record["prescan"] = f"prescan{number:02d}.tif"
                    # How that file was turned, per frame. The manifest's one
                    # `rotation` said it for a walk made in one go, but a walk
                    # that adds to another can be made after "rotate all" has
                    # moved the session's, and one pair then un-turned half
                    # the prescans wrong. See `read_survey`.
                    turn, mirrored = self._orientation_for(number, "prescan")
                    record["prescan_rotation"] = turn
                    record["prescan_flipped"] = mirrored
                # This frame's record replaces any earlier attempt's, so a
                # frame rescanned after a failure is not in the file twice
                # saying two different things about itself. Rewritten after
                # every frame, whole and atomically: a roll takes hours and a
                # crash should cost the frame it was on, not the roll.
                record_of.record(record, awaiting=scanned is not None)

                if self._stop.is_set():
                    stopped = f"stopped after frame {number}, as asked"
                    self._emit("log", text=stopped)
                    break
        finally:
            # Ends the generator at its yield rather than leaving it suspended
            # with the device half-way through a roll.
            frames.close()
            # This roll's orientations die with it. A single scan taken
            # afterwards is not frame 3 of anything, and letting it inherit
            # frame 3's arrangement would be a silent wrong answer.
            self._frame_rotation = {}
            self._frame_flip = {}
        return stopped

    # -- shared ------------------------------------------------------------

    def _deliver(
        self,
        kind: str,
        label: str,
        image: np.ndarray,
        meta: dict[str, Any],
        registration: dict[str, Any] | None = None,
        position: int | None = None,
        number: int = 0,
    ) -> int:
        """Hand the UI a working copy small enough to keep.

        A copy rather than a view, so the full frame can be freed as soon as the
        writer has it -- a strided view would pin all 142 MB of a 3600 dpi pass.
        The copy is a few megabytes and takes milliseconds, which is the only
        reason it is allowed to happen with the device still open.
        """
        working = np.ascontiguousarray(preview.downscale(image, preview.PREVIEW_MAX_SIDE))
        if position is None:
            position = self._position()
        self._seq += 1
        self._emit("result", result=Result(
            seq=self._seq, kind=kind, label=label, image=working, meta=dict(meta),
            registration=dict(registration or {}), position=position,
            number=number,
        ))
        return self._seq

    @staticmethod
    def _walk_shift(folder: Path) -> int:
        """See :func:`walk_shift`."""
        return walk_shift(folder)

    def _position(self) -> int | None:
        """Where the transport is, or None if it will not say."""
        try:
            return self._scanner.position()
        except Exception:                                # noqa: BLE001
            return None

    def _report_position(self) -> None:
        """Tell the window where the film is, as the counter says it.

        One READ_STATE, not the patient read a roll makes before it moves: a
        readout that says "?" for a moment costs nothing, and this runs after
        every job. A counter no strip can have is reported as unknown rather
        than as a frame number nobody could find.
        """
        if self.dead:
            return
        here = self._position()
        if here is None or not plausible(here):
            here = -1
        self._emit("transport", done=here)

    def _orientation_for(self, number: int, kind: str) -> tuple[int, bool]:
        """How this picture's delivered file should be arranged.

        The frame's own turn and mirror if the contact sheet gave it them, the
        session's otherwise. One method for both, because they are one decision
        and `preview.orient` takes them together.

        **Prescans are exempt, deliberately.** `prescanNN.tif` is a reference
        rather than a deliverable: `read_survey` un-orients it by the single
        pair the manifest carries, and a per-frame answer here would make that
        arithmetic wrong -- the reference would come back arranged a way the
        film was never in, and it would no longer correlate against a fresh
        pass of the same frame.
        """
        if kind != "prescan":
            turn = self._frame_rotation.get(number)
            if turn is not None:
                return turn, self._frame_flip.get(number, self.flip)
        return self.rotation, self.flip

    def _file(
        self,
        seq: int,
        number: int,
        image: np.ndarray,
        meta: dict[str, Any],
        notes: FilmNotes,
        tags: tuple[str, ...],
        prescan: np.ndarray | None = None,
        raw_image: np.ndarray | None = None,
        path: Path | None = None,
        kind: str = "scan",
        prescan_meta: dict[str, Any] | None = None,
        roll: str = "",
        mono: bool = False,
        mono_channel: str = MONO_CHANNEL,
        file_entry: bool = True,
        on_filed: Callable[..., Any] | None = None,
    ) -> None:
        """Write this picture, and unless told otherwise file it in the library.

        ``file_entry=False`` writes the file and no entry. It exists for the
        prescan a correction replaced, and the reason is specific: the capture
        record below describes the scanner's **last** pass, which by then is the
        verification prescan -- and the shape guard cannot catch the swap,
        because both passes are identically shaped prescans of the same frame at
        the same resolution. That is exactly the failure the guard was written
        for, in the one form it is blind to. Under `RPS7200_DEBUG=1` that
        picture already has a correct entry anyway, filed at the instant it was
        taken, which is the only moment its bytes and its pixels are certainly
        the same pass.

        ``on_filed(entry, error, written)`` is called on the writer thread
        once the picture has been filed, or has failed to be; see
        `RollManifest`.
        """
        if self._writer is None:
            return
        # A roll frame has its own place in the roll directory *and* wants a
        # copy wherever the operator asked for one. Setting `path` used to skip
        # the output folder entirely, so a whole roll went missing from it.
        paths = [path] if path is not None else []
        if self.out_dir is not None:
            where = (self.out_dir / PRESCAN_SUBDIR if kind == "prescan"
                     else self.out_dir)
            paths.append(_unclaimed(where / self._out_name(number, meta, roll)))
        capture = self._scanner.capture_record()
        if capture.get("raw") is not None or capture.get("raw_path") is not None:
            disagree = raw_bytes_disagree(image.shape, capture.get("raw_layout"),
                                          meta)
            if disagree:
                # `last_raw` holds whatever the previous pass left behind when a
                # pass did not keep its own. Filing that here produces an entry
                # that decodes to a different photograph -- which is the one
                # failure the library exists to make impossible. It happened:
                # three roll prescans were filed with a 600 dpi RGBI scan's
                # bytes before the driver kept the prescan's own.
                detail = ", ".join(f"{k} {a} vs {b}" for k, (a, b) in disagree.items())
                self._emit("log", text=(
                    f"raw bytes do not describe this image ({detail}); "
                    "filing it without them rather than filing the wrong ones"))
                capture = dict(capture, raw=None, raw_path=None, raw_layout=None)
        # One answer, recorded and applied, so the entry's record says what the
        # delivered file actually got rather than what the session default was.
        # Any reversal the scanner made necessary goes on first: it brings the
        # pixels into the arrangement the operator was looking at when he chose
        # the rest, so his choice is the second of the two.
        reversal = meta.get("reversal")
        turn, flip = self._orientation_for(number, kind)
        if reversal:
            turn, flip = preview.compose(
                (int(reversal[0]), bool(reversal[1])), (turn, flip))
        meta = dict(meta, rotation=turn, flipped=flip)
        # Only if it really is this picture. A raw array of another shape is a
        # different pass, and filing it here is exactly the failure the guard
        # above exists to prevent -- better to file the corrected pixels and
        # have `reconstruct` say so than to file the wrong photograph.
        if raw_image is not None and raw_image.shape != image.shape:
            self._emit("log", text=(
                "raw pixels do not match this image "
                f"({raw_image.shape} vs {image.shape}); filing the corrected "
                "pixels instead"))
            raw_image = None
        # This pass is filed here, so debug filing (RPS7200_DEBUG=1) leaves it
        # out rather than filing it twice; it still files the passes nothing
        # here keeps -- metering probes, hold and aim prescans.
        claim = getattr(self._scanner, "debug_claim", None)
        if (raw_image is not None and file_entry and self.root is not None
                and callable(claim)):
            claim(raw_image)
        self._writer.submit(
            seq=seq,
            number=number,
            paths=paths,
            rotate=turn,
            flip=flip,
            image=image,
            raw_image=raw_image,
            quality=self.jpeg_quality,
            meta=meta,
            dpi=meta.get("resolution_dpi"),
            library=self.root if file_entry else None,
            film=notes,
            tags=list(tags),
            prescan=prescan,
            prescan_meta=prescan_meta,
            inquiry=getattr(self._scanner, "_inquiry", None),
            capture=capture,
            mono=mono,
            mono_channel=mono_channel,
            on_filed=on_filed,
        )

    def _out_name(
        self, number: int, meta: dict[str, Any], roll: str
    ) -> str:
        """A filename that says which roll and which frame it came from.

        NegPy reads these next, so the roll and the frame number lead. What this
        replaced led with a timestamp and ended with a sequence number, which
        sorted by when it was scanned and said nothing about what it was --
        fine for one pass, useless for thirty-eight of them.

        A scan that belongs to no roll keeps a timestamp, because there is
        nothing better to call it.
        """
        dpi = meta.get("resolution_dpi") or 0
        channels = meta.get("channels") or len(meta.get("channel_order") or "")
        ir = "_ir" if channels and int(channels) >= 4 else ""
        # `_ir` still names a pass that *was* infrared even when the format
        # cannot carry the plane: it says what was scanned, and the JPEG's own
        # note says what arrived. Renaming it would lose the first.
        end = export.suffix_for(self.out_format)
        if roll and number:
            return f"{_safe(roll)}_frame{number:02d}_{dpi}dpi{ir}{end}"
        if roll:
            return f"{_safe(roll)}_{time.strftime('%H%M%S')}_{dpi}dpi{ir}{end}"
        return f"{time.strftime('%Y%m%dT%H%M%S')}_{dpi}dpi{ir}{end}"


#: Names Windows will not give a file or a folder, whatever the extension.
#: They are device names, not reserved words, so `CON` fails where `CON-1`
#: is fine -- and it fails as NotADirectoryError from `mkdir`, which reads
#: like a bug in this driver rather than a name the operator chose.
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{n}" for n in range(1, 10)]
    + [f"lpt{n}" for n in range(1, 10)]
)


def _safe(name: str, fallback: str = "roll") -> str:
    """`name` with anything a filename should not carry taken out.

    ``fallback`` when nothing of it is left; `roll_dir` asks for "" there, so
    that a name of nothing but slashes is a new roll and not a shared one.
    """
    kept = [c if (c.isalnum() or c in "-_.") else "-" for c in name.strip()]
    cleaned = "".join(kept).strip("-.") or fallback
    # Checked against the part before the first dot, which is what Windows
    # matches on: `con.tif` is refused as surely as `con`.
    if cleaned and cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = f"{cleaned}-roll"
    return cleaned


def _unclaimed(wanted: Path) -> Path:
    """`wanted`, or the next free name beside it.

    A frame rescanned after a failure would otherwise land on the file the
    first attempt wrote, and the better of the two is not always the second.
    """
    if not wanted.exists():
        return wanted
    for n in range(2, 1000):
        candidate = wanted.with_name(f"{wanted.stem}-{n}{wanted.suffix}")
        if not candidate.exists():
            return candidate
    return wanted


def _read_known(meta: dict[str, Any] | None) -> bool:
    """Whether a pass's own line tags said which way it was read."""
    read = (meta or {}).get("read_direction") or {}
    return read.get("direction") in (FORWARD, REVERSED)


def _describe(job: Job) -> str:
    if isinstance(job, Calibrate):
        return {"measure": "calibrating (3-4 minutes)",
                "reuse": "loading the cached reference",
                "off": "shading off"}[job.mode]
    if isinstance(job, Prescan):
        return f"prescanning at {job.resolution} dpi (~16 s)"
    if isinstance(job, Scan):
        return (f"scanning at {job.resolution} dpi "
                f"{'RGBI' if job.infrared else 'RGB'}")
    if isinstance(job, Roll):
        what = "walking" if job.dry_run else "scanning"
        if job.only is not None:
            n = len(job.only)
            return f"{what} {n} chosen frame{'s' if n != 1 else ''}"
        n = job.frames if job.frames else "?"
        return f"{what} a roll of {n} frames"
    if isinstance(job, Move):
        if job.frames:
            way = "forward" if job.frames > 0 else "back"
            n = abs(job.frames)
            return f"moving {n} frame{'s' if n != 1 else ''} {way} (~7 s each)"
        return f"nudging the film {say_units(job.millimetres)}"
    return str(job)
